"""跑一遍规则引擎，打印候选操作（不接 LLM、不执行写操作）。

用法: python rules_cli.py [data_dir]
"""
from __future__ import annotations

import sys
from pathlib import Path

from amazon_ads import DEFAULT_CONFIG, load_csv, run_analysis

# Windows 控制台默认 GBK，这里强制 UTF-8 输出，避免中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DATA_DIR = Path(__file__).parent / "data"


def _fmt(m: dict) -> str:
    acos = f"{m['acos']:.0%}" if m.get("acos") is not None else "—"
    return f"clicks={m['clicks']} orders={m['orders']} spend={m['spend']} acos={acos}"


def main() -> None:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA_DIR
    search = load_csv(data_dir / "search_term_report.csv")
    keyword = load_csv(data_dir / "keyword_report.csv")
    campaign = load_csv(data_dir / "campaign_report.csv")

    result = run_analysis(search, keyword, campaign, DEFAULT_CONFIG)
    print(f"目标ACOS={result['target_acos']:.1%}  保本ACOS={result['breakeven_acos']:.1%}")
    print(f"（{result['note']}）\n")
    print(f"共 {result['count']} 条候选：\n")

    for c in result["candidates"]:
        tag = " [建议]" if c.get("advisory") else ""
        print(f"[{c['lever']}]{tag} {c['target_name']}")
        print(f"    规则: {c['rule']}")
        print(f"    指标: {_fmt(c['metrics'])}")
        if c.get("proposed"):
            print(f"    变更: {c['current']} → {c['proposed']}")
        print(f"    理由: {c['rationale']}")
        print()


if __name__ == "__main__":
    main()
