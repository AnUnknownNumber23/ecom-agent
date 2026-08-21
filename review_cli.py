"""D3 入口：规则引擎生成候选 → LLM 复核 → 工单状态机。

用法: python review_cli.py [data_dir]
需要 .env 里的 LLM_API_KEY。没有 key 或复核失败时，工单安全停在 pending。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from amazon_ads import DEFAULT_CONFIG, load_csv, run_analysis
from amazon_ads.policy import REVIEW_POLICY
from harness.provider import from_env
from harness.review import review_candidates
from harness.workorder import WorkOrder

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DATA_DIR = Path(__file__).parent / "data"

_MARK = {"approve": "同意", "reject": "反对", "modify": "改参"}


async def main() -> None:
    load_dotenv()

    search = load_csv(DATA_DIR / "search_term_report.csv")
    keyword = load_csv(DATA_DIR / "keyword_report.csv")
    campaign = load_csv(DATA_DIR / "campaign_report.csv")
    result = run_analysis(search, keyword, campaign, DEFAULT_CONFIG)
    print(f"目标ACOS={result['target_acos']:.1%}，共 {result['count']} 条候选\n")

    orders = [WorkOrder(c) for c in result["candidates"]]

    llm = from_env()
    if not llm.client.api_key:
        print("⚠ 未配置 LLM_API_KEY，跳过复核，工单停在 pending。\n")
    else:
        print("LLM 复核中...\n")
        try:
            verdicts = await review_candidates(llm, result["candidates"], REVIEW_POLICY)
            for wo, v in zip(orders, verdicts):
                wo.apply_verdict(v)
        except Exception as e:  # 复核失败 = 安全失败，工单停在 pending，不放行
            print(f"⚠ 复核失败：{e}，工单停在 pending（安全起见不继续）。\n")

    for wo in orders:
        c = wo.candidate
        v = wo.verdict
        mark = _MARK.get((v or {}).get("verdict"), "—")
        print(f"[{wo.id}] {wo.state.value:<10} [{wo.lever}] {wo.target_name}  复核={mark}")
        if v and v.get("reason"):
            print(f"     复核理由: {v['reason']}")
        if c.get("proposed"):
            print(f"     变更: {c.get('current')} → {c['proposed']}")

    reviewed = sum(1 for w in orders if w.state.value == "reviewed")
    rejected = sum(1 for w in orders if w.state.value == "rejected")
    pending = sum(1 for w in orders if w.state.value == "pending")
    print(f"\n流水线状态: {reviewed} 条已复核待批 / {rejected} 条被否 / {pending} 条待复核")


if __name__ == "__main__":
    asyncio.run(main())
