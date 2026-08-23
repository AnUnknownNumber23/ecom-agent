"""上下文压缩 —— 对话太长时，把早期轮次压成摘要，保住最近上下文。

LLM 上下文窗口有限，ReAct 循环里 messages 会无限增长。这里按 token 预算触发：
超过预算就把「早期完整轮次」压成一条 system 摘要，最近几轮保留原文。

压缩本身是一次 LLM 调用——这是少数该花钱的 LLM 用法，且和"只审不造"同源：
摘要只是压缩既有事实（保留用户目标、已确认结论），不编造新内容。
"""
from __future__ import annotations

import json

_SUMMARY_HEADER = "<早期对话摘要>\n"
_COMPACT_PROMPT = (
    "把下面的对话历史压缩成要点摘要。要求：\n"
    "1. 保留用户目标、已确认的事实、工具调用的结论；\n"
    "2. 丢弃寒暄和冗余细节；\n"
    "3. 只输出摘要本身，不要任何解释。\n\n对话历史：\n"
)


def estimate_tokens(text: str) -> int:
    """粗略 token 估算（不引 tokenizer）：CJK 1 字≈1 token，其余 4 字符≈1 token。"""
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    return cjk + (len(text) - cjk) // 4


def count_tokens(messages: list[dict]) -> int:
    """整条消息序列化后估算总 token（含 role/content/tool_calls）。"""
    return sum(estimate_tokens(json.dumps(m, ensure_ascii=False)) for m in messages)


def _transcript(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role", "")
        if m.get("content"):
            lines.append(f"[{role}] {m['content']}")
        elif m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                lines.append(f"[{role} 调用工具 {fn.get('name')}] {fn.get('arguments', '')}")
    return "\n".join(lines)


async def _summarize(llm, transcript: str) -> str:
    resp = await llm.chat([{"role": "user", "content": _COMPACT_PROMPT + transcript}])
    return (resp.get("content") or "").strip()


async def compact_history(llm, messages: list[dict],
                          budget_tokens: int, keep_turns: int = 3) -> bool:
    """超预算时压缩早期轮次，就地改写 messages。返回是否发生了压缩。"""
    if count_tokens(messages) <= budget_tokens:
        return False

    # 分离前置 system 与正文（摘要要插在 system 之后、正文之前）
    head: list[dict] = []
    body: list[dict] = list(messages)
    while body and body[0]["role"] == "system":
        head.append(body.pop(0))

    # head = [基础 system, (可能还有上一轮摘要)]；上一轮摘要要一并压，防止越积越多
    base = head[:1]
    prior_summaries = head[1:]

    # 找保留起点：最近 keep_turns 个 user 消息里最早的那个（按整轮切，不拆工具调用）
    user_idx = [i for i, m in enumerate(body) if m["role"] == "user"]
    if len(user_idx) <= keep_turns:
        return False  # 正文轮次已经不多，压不动

    split = user_idx[-keep_turns]
    old, recent = body[:split], body[split:]

    summary = await _summarize(llm, _transcript(prior_summaries + old))
    messages[:] = base + [{"role": "system", "content": _SUMMARY_HEADER + summary}] + recent
    return True
