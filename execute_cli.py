"""D4 入口：规则引擎 → LLM 复核 → 人批 → 执行（shadow mode）→ 操作清单 CSV。

用法:
  python execute_cli.py          # 交互式人批
  python execute_cli.py --yes    # 自动通过全部（演示 / 非交互）
"""
from __future__ import annotations

import asyncio
import csv
import sys
from pathlib import Path

from dotenv import load_dotenv

from amazon_ads import DEFAULT_CONFIG, load_csv, run_analysis
from amazon_ads.policy import REVIEW_POLICY
from harness.approval import approve_all, approve_interactive
from harness.executor import CircuitBreakerError, Executor
from harness.provider import from_env
from harness.review import review_candidates
from harness.workorder import WorkOrder

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DATA_DIR = Path(__file__).parent / "data"
OUTPUT = Path(__file__).parent / "output" / "actions.csv"

_FIELDS = ["工单号", "杠杆", "动作", "目标", "字段", "旧值", "新值", "匹配类型", "结果", "理由"]


def write_csv(results: list[dict], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_FIELDS)
        w.writeheader()
        for r in results:
            if not r.get("ok"):
                continue
            w.writerow(r["row"])
            n += 1
    return n


async def main() -> None:
    load_dotenv()
    auto_yes = "--yes" in sys.argv

    # 1. 规则引擎（确定性，不接 LLM）
    search = load_csv(DATA_DIR / "search_term_report.csv")
    keyword = load_csv(DATA_DIR / "keyword_report.csv")
    campaign = load_csv(DATA_DIR / "campaign_report.csv")
    result = run_analysis(search, keyword, campaign, DEFAULT_CONFIG)
    print(f"目标ACOS={result['target_acos']:.1%}，规则引擎产出 {result['count']} 条候选\n")

    orders = [WorkOrder(c) for c in result["candidates"]]

    # 2. LLM 复核
    llm = from_env()
    if not llm.client.api_key:
        print("⚠ 未配置 LLM_API_KEY，跳过复核，工单停在 pending（不会进入人批/执行）。\n")
    else:
        print("LLM 复核中...\n")
        try:
            verdicts = await review_candidates(llm, result["candidates"], REVIEW_POLICY)
            for wo, v in zip(orders, verdicts):
                wo.apply_verdict(v)
        except Exception as e:
            print(f"⚠ 复核失败：{e}，工单停在 pending。\n")

    # 3. 人批（只批 reviewed 的）
    reviewed = [w for w in orders if w.state.value == "reviewed"]
    if reviewed:
        if auto_yes:
            approve_all(orders)
            print(f"已自动通过 {len(reviewed)} 条（--yes）。\n")
        else:
            print(f"以下 {len(reviewed)} 条等待人工批准：\n")
            approve_interactive(orders)
            print()

    # 4. 执行（shadow mode）
    try:
        results = Executor().run(orders)
    except CircuitBreakerError as e:
        print(f"🛑 熔断触发：{e}")
        return

    # 5. 写操作清单
    done = sum(1 for r in results if r["ok"])
    failed = sum(1 for r in results if not r["ok"])
    written = write_csv(results, OUTPUT)
    print(f"执行完成：{done} 成功 / {failed} 失败。")
    if written:
        print(f"操作清单已写入 {OUTPUT}（{written} 条），请照此在 Amazon 后台手动执行。")

    # 6. 逐条汇总
    for r in results:
        if r["ok"]:
            row = r["row"]
            print(f"  ✓ [{row['杠杆']}] {row['目标']}: {row['旧值']} → {row['新值']} "
                  f"（{row['匹配类型'] or row['字段']}）")
        else:
            wo = r["order"]
            print(f"  ✗ [{wo.lever}] {wo.target_name} 失败已回滚: {r.get('error')}")


if __name__ == "__main__":
    asyncio.run(main())
