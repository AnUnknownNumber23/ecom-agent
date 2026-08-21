"""亚马逊广告规则引擎 —— 确定性、可审计。

输入：广告报表（搜索词/关键词/活动），输出：带证据的候选操作。
LLM 不参与计算——它只负责之后复核这些候选。这是整个 harness 的灵魂：
"确定性规则做决策，LLM 只审不造"。

规则（对应原项目 lingxing_optimizer）：
  否词  — 搜索词 ≥N 点击且 0 单 → 否定（数据确认的输家）
  降bid — 关键词高 ACOS 或 高点击 0 单 → 降至 RPC×目标，单步封顶
  加bid — 赢家（≥N 单、ACOS ≤ 0.8×目标）→ 放量，不超 RPC×目标
  加预算 — 活动预算打满且盈利 → 预算 +step
  收割  — 搜索词 ≥N 单且盈利 → 建议转精准词（advisory，不自动执行）

目标 ACOS 从毛利反推：保本 ACOS = 毛利率，目标 ACOS = 系数 × 毛利率。
"""
from __future__ import annotations

import math
from typing import Any

DEFAULT_CONFIG: dict[str, float] = {
    "margin": 0.35,             # 毛利率（店铺均值）；保本 ACOS = 毛利率
    "target_acos_factor": 0.7,  # 目标 ACOS = 系数 × 毛利率
    "neg_min_clicks": 15,       # 否词：≥15 点击且 0 单
    "bid_min_clicks": 15,       # 调价：≥15 点击才有统计意义
    "scale_min_orders": 3,      # 加 bid：≥3 单才算赢家
    "harvest_min_orders": 3,    # 收割：≥3 单
    "bid_step_pct": 15,         # 单步幅度 %
    "bid_floor": 0.02,          # bid 下限
    "budget_util_floor": 0.85,  # 预算利用率 ≥ 此值才算"打满"
}

# 排序优先级（否词最前，预算最后）
_LEVER_ORDER = {"否词": 0, "收割": 1, "降bid": 2, "加bid": 3, "加预算": 4}


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _round_bid(v: float, direction: str) -> float:
    """2 位小数取整，方向保守：降bid 用 ceil（不超跌）、加bid 用 floor（不超涨）。

    保证"单步封顶"不变量不被浮点舍入破坏：0.7×0.85=0.595 若 round 成 0.59，
    实际降幅 15.7% 就破了 15% 上限；ceil 成 0.60 才守得住。
    """
    return math.ceil(v * 100) / 100 if direction == "reduce" else math.floor(v * 100) / 100


def _metrics(row: dict) -> dict:
    spend, sales = _f(row.get("spend")), _f(row.get("sales"))
    clicks, orders = _f(row.get("clicks")), _f(row.get("orders"))
    return {
        "spend": round(spend, 2), "sales": round(sales, 2),
        "clicks": int(clicks), "orders": int(orders),
        "acos": (spend / sales) if sales else None,
        "cpc": (spend / clicks) if clicks else None,
        "rpc": (sales / clicks) if clicks else None,  # 每次点击收入
        "cvr": (orders / clicks) if clicks else None,
    }


def _candidate(lever, op_type, target_name, metrics, current, proposed,
               rule, significance, rationale, *, advisory=False):
    return {
        "lever": lever, "op_type": op_type, "target_name": target_name,
        "advisory": advisory, "metrics": metrics,
        "current": current, "proposed": proposed,
        "rule": rule, "significance": significance, "rationale": rationale,
    }


def negate_rule(rows: list[dict], cfg: dict) -> list[dict]:
    out = []
    for r in rows:
        q = r.get("query") or r.get("keyword") or ""
        if not q:
            continue
        m = _metrics(r)
        if m["clicks"] >= cfg["neg_min_clicks"] and m["orders"] == 0:
            out.append(_candidate(
                "否词", "negate_keyword", q, m,
                None, {"match_type": "negativeExact"},
                f"搜索词「{q}」{m['clicks']}点击/0单（≥{int(cfg['neg_min_clicks'])}）→ 否定",
                f"{m['clicks']}点击 0单 · 花费{m['spend']}",
                f"近30天 {m['clicks']} 次点击 0 转化、花费 {m['spend']}，纯无效花费，建议否定。",
            ))
    return out


def harvest_rule(rows: list[dict], cfg: dict, breakeven: float, target: float) -> list[dict]:
    out = []
    for r in rows:
        q = r.get("query") or r.get("keyword") or ""
        if not q:
            continue
        m = _metrics(r)
        if (m["orders"] >= cfg["harvest_min_orders"]
                and m["acos"] is not None and m["acos"] <= breakeven):
            sug = round((m["rpc"] or 0) * target, 2)
            out.append(_candidate(
                "收割", "add_keyword", q, m,
                None, {"match_type": "EXACT", "suggested_bid": sug},
                f"搜索词「{q}」{m['orders']}单、ACOS {m['acos']:.0%} → 收割成精准词",
                f"{m['orders']}单 ACOS {m['acos']:.0%}",
                f"该搜索词 {m['orders']} 单、ACOS {m['acos']:.0%} 健康，建议加入精准活动"
                f"（建议 bid≈{sug}）并原活动否定它（毕业）。",
                advisory=True,
            ))
    return out


def bid_rule(rows: list[dict], cfg: dict, target: float) -> list[dict]:
    out = []
    for r in rows:
        name = r.get("keyword") or r.get("query") or ""
        cur = _f(r.get("bid"))
        if not name or not cur:
            continue
        m = _metrics(r)
        if m["clicks"] < cfg["bid_min_clicks"]:
            continue
        step = cfg["bid_step_pct"] / 100.0

        # 降bid：高 ACOS（当前 bid 高于公允价才降，且单步封顶，不一次腰斩）
        if m["acos"] is not None and m["acos"] > target:
            ideal = (m["rpc"] or 0) * target
            if ideal < cur:
                new = max(ideal, cur * (1 - step))
                if new < cur * 0.98:
                    new_r = _round_bid(new, "reduce")
                    out.append(_candidate(
                        "降bid", "keyword_bid", name, m,
                        {"bid": round(cur, 2)}, {"bid": new_r},
                        f"ACOS {m['acos']:.0%} > 目标 {target:.0%}（{m['clicks']}点击≥{int(cfg['bid_min_clicks'])}）→ 降bid",
                        f"ACOS {m['acos']:.0%} · {round(cur, 2)}→{new_r}",
                        f"高ACOS控本，单步封顶 {int(cfg['bid_step_pct'])}%：{round(cur, 2)}→{new_r}。",
                    ))
        # 降bid：高点击 0 单
        elif m["orders"] == 0:
            new = max(cfg["bid_floor"], cur * (1 - step))
            if new < cur * 0.98:
                new_r = _round_bid(new, "reduce")
                out.append(_candidate(
                    "降bid", "keyword_bid", name, m,
                    {"bid": round(cur, 2)}, {"bid": new_r},
                    f"{m['clicks']}点击 0单（花费{m['spend']}）→ 降bid {int(cfg['bid_step_pct'])}%",
                    f"{m['clicks']}点击 0单",
                    f"高点击0单：{round(cur, 2)}→{new_r}（持续无效可考虑暂停）。",
                ))
        # 加bid：赢家
        elif (m["orders"] >= cfg["scale_min_orders"]
                and m["acos"] is not None and m["acos"] <= 0.8 * target):
            ideal = (m["rpc"] or 0) * target
            new = min(cur * (1 + step), ideal)
            if new > cur * 1.02:
                new_r = _round_bid(new, "raise")
                out.append(_candidate(
                    "加bid", "keyword_bid", name, m,
                    {"bid": round(cur, 2)}, {"bid": new_r},
                    f"ACOS {m['acos']:.0%} ≤ 0.8×目标、{m['orders']}单 → 放量 +≤{int(cfg['bid_step_pct'])}%",
                    f"{m['orders']}单 ACOS {m['acos']:.0%}",
                    f"赢家放量：{round(cur, 2)}→{new_r}（不超 RPC×目标）。",
                ))
    return out


def budget_rule(rows: list[dict], cfg: dict, target: float) -> list[dict]:
    out = []
    for r in rows:
        name = r.get("campaign") or ""
        bud = _f(r.get("daily_budget"))
        if not name or not bud:
            continue
        m = _metrics(r)
        if m["spend"] < cfg["budget_util_floor"] * bud:
            continue
        if m["acos"] is not None and m["acos"] <= target:
            new = round(bud * (1 + cfg["bid_step_pct"] / 100.0), 2)
            out.append(_candidate(
                "加预算", "campaign_budget", name, m,
                {"daily_budget": bud}, {"daily_budget": new},
                f"预算打满（利用率≥{int(cfg['budget_util_floor'] * 100)}%）、ACOS {m['acos']:.0%} ≤ 目标 → 预算 +{int(cfg['bid_step_pct'])}%",
                f"花费{m['spend']} ≈ 预算{bud} · ACOS {m['acos']:.0%}",
                f"活动预算打满且盈利（ACOS {m['acos']:.0%}≤目标 {target:.0%}），扩量。",
            ))
    return out


def run_analysis(search_rows: list[dict] | None = None,
                 keyword_rows: list[dict] | None = None,
                 campaign_rows: list[dict] | None = None,
                 config: dict | None = None) -> dict:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    margin = _f(cfg["margin"])
    factor = _f(cfg["target_acos_factor"])
    breakeven = margin if margin > 0 else 0.30
    target = factor * breakeven
    note = (f"毛利率≈{margin:.0%}，保本ACOS={breakeven:.0%}，"
            f"目标ACOS={target:.0%}(={factor:g}×毛利)")

    cands: list[dict] = []
    cands += negate_rule(search_rows or [], cfg)
    cands += harvest_rule(search_rows or [], cfg, breakeven, target)
    cands += bid_rule(keyword_rows or [], cfg, target)
    cands += budget_rule(campaign_rows or [], cfg, target)

    cands.sort(key=lambda c: (_LEVER_ORDER.get(c["lever"], 9), -(c["metrics"].get("spend") or 0)))
    return {
        "margin": margin, "target_acos": round(target, 4),
        "breakeven_acos": round(breakeven, 4),
        "note": note, "count": len(cands), "candidates": cands,
    }
