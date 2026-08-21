"""LLM 复核 —— 三重复核里的第二重（第一重是规则引擎自带护栏，第三重是人批）。

核心约束：LLM 只审不造。给它候选 + 政策，它只能逐条批注 approve/reject/modify，
不能新增候选、不能改 op_type 或目标对象。复核失败 = 安全失败（不通过），
因为流水线宁可停，也不能把没复核过的东西放行。
"""
from __future__ import annotations

import json

VERDICT_CHOICES = {"approve", "reject", "modify"}


def _payload(candidates: list[dict]) -> list[dict]:
    return [
        {
            "index": i,
            "lever": c["lever"],
            "op_type": c["op_type"],
            "target_name": c["target_name"],
            "advisory": c.get("advisory", False),
            "metrics": c["metrics"],
            "current": c.get("current"),
            "proposed": c.get("proposed"),
            "rule": c["rule"],
        }
        for i, c in enumerate(candidates)
    ]


def build_review_prompt(policy: str, candidates: list[dict]) -> str:
    return (
        f"{policy}\n\n"
        "以下是一批确定性规则引擎生成的候选操作。请逐条复核，返回 JSON。\n\n"
        "候选列表：\n" + json.dumps(_payload(candidates), ensure_ascii=False, indent=2) + "\n\n"
        "返回格式（严格 JSON 对象）：\n"
        '{"verdicts": [{"index": <int>, "verdict": "approve|reject|modify", '
        '"reason": "<中文说明>", "proposed": {可选的改后参数}}]}\n\n'
        "硬约束：\n"
        "1. 只能对给出的候选逐条批注，index 必须是候选列表里的；禁止新增候选。\n"
        "2. verdict 只能是 approve / reject / modify。modify 时 proposed 只能覆盖"
        "已有参数键（如 bid / daily_budget / suggested_bid），禁止改 op_type / target_name。\n"
        "3. advisory 候选（收割）本身是建议，数值合理就 approve。\n"
        "4. 每个候选都要有一条对应 verdict，不要漏。"
    )


def parse_verdicts(text: str, n: int) -> list[dict]:
    """把 LLM 返回的 JSON 解析成 n 条 verdict（按 index 对齐）。

    - 越界/非法 index 直接丢弃（防止 LLM 编造候选）。
    - 缺失的候选默认 reject（安全失败：没复核到就不放行）。
    - JSON 本身非法则抛异常，由调用方决定停流水线。
    """
    data = json.loads(text)
    raw = data.get("verdicts") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise ValueError("复核返回不是 verdicts 列表")

    by_index: dict[int, dict] = {}
    for v in raw:
        if not isinstance(v, dict):
            continue
        idx = v.get("index")
        if not isinstance(idx, int) or not (0 <= idx < n):
            continue
        if v.get("verdict") not in VERDICT_CHOICES:
            continue
        by_index[idx] = {
            "verdict": v["verdict"],
            "reason": str(v.get("reason", "")),
            "proposed": v.get("proposed") if v.get("verdict") == "modify" else None,
        }

    return [
        by_index.get(i, {"verdict": "reject", "reason": "（LLM 未覆盖，安全起见否决）", "proposed": None})
        for i in range(n)
    ]


async def review_candidates(llm, candidates: list[dict], policy: str) -> list[dict]:
    if not candidates:
        return []
    messages = [{"role": "user", "content": build_review_prompt(policy, candidates)}]
    raw = await llm.chat(messages, response_format={"type": "json_object"})
    text = (raw.get("content") or "{}").strip()
    # 容错：剥掉可能的 ```json ... ``` 包裹
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:].strip()
    return parse_verdicts(text, len(candidates))
