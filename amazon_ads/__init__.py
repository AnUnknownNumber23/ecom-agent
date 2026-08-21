"""亚马逊广告 DomainPack —— 第一个领域插件包。

对 harness 而言它只暴露一件事：给定广告报表 CSV → 产出带证据的候选操作。
规则引擎是确定性代码，LLM 不参与计算（只负责 D3 的复核）。
"""
from .data import load_csv
from .rules import DEFAULT_CONFIG, run_analysis

__all__ = ["load_csv", "DEFAULT_CONFIG", "run_analysis"]
