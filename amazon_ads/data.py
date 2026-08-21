"""读亚马逊广告报表 CSV，归一化成内部字段。

真实报表列名中英文混杂、带空格，这里做「小写 + 去空格」归一化，把数值列
转成 float。D2 先认一组规范列名（含常见变体），脏数据清洗后续再加固。
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

# 内部字段 -> 报表里可能出现的列名（小写后匹配，取第一个命中）
ALIASES: dict[str, tuple[str, ...]] = {
    "campaign": ("campaign", "campaign name", "活动名称", "广告活动"),
    "ad_group": ("ad_group", "ad group", "ad group name", "广告组", "广告组名称"),
    "query": ("query", "customer search term", "search term", "搜索词", "客户搜索词"),
    "keyword": ("keyword", "关键词"),
    "match_type": ("match_type", "match type", "匹配类型"),
    "impressions": ("impressions", "展示量", "曝光", "曝光量"),
    "clicks": ("clicks", "点击", "点击量"),
    "spend": ("spend", "花费", "广告花费"),
    "sales": ("sales", "销售额"),
    "orders": ("orders", "订单", "订单量"),
    "bid": ("bid", "竞价", "当前竞价"),
    "daily_budget": ("daily_budget", "daily budget", "预算", "日预算"),
}

_NUMERIC = {"impressions", "clicks", "spend", "sales", "orders", "bid", "daily_budget"}


def _to_float(v: Any) -> float:
    if v is None:
        return 0.0
    s = str(v).replace(",", "").replace("$", "").replace("¥", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def load_csv(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        raw_headers = list(reader.fieldnames or [])
        raw_rows = list(reader)

    header_map: dict[str, str] = {}
    for h in raw_headers:
        key = str(h).strip().lower()
        for field, aliases in ALIASES.items():
            if key in aliases and field not in header_map:
                header_map[field] = h
                break

    rows: list[dict] = []
    for raw in raw_rows:
        row: dict[str, Any] = {}
        for field, src in header_map.items():
            v = raw.get(src)
            row[field] = _to_float(v) if field in _NUMERIC else str(v or "").strip()
        rows.append(row)
    return rows
