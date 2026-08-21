"""ReAct 循环 —— harness 的心脏。

模型决策 → 返回 tool_call → 执行 → 结果喂回 → 重复，直到模型给出最终文本。
现在手写，理解原理后（D5）再用 LangGraph 换掉，对比两者的差异。
"""
from __future__ import annotations

import json

from .provider import LLM
from .tools import ToolRegistry


async def run_turn(llm: LLM, registry: ToolRegistry, messages: list[dict],
                   max_steps: int = 20) -> str:
    """跑一轮对话，返回模型的最终文本答复。messages 就地追加（保留历史）。"""
    tools = registry.all_openai() or None
    for _ in range(max_steps):
        assistant = await llm.chat(messages, tools=tools)
        messages.append(assistant)

        if not assistant.get("tool_calls"):
            return assistant.get("content") or ""

        # 并行安全的工具批处理（先顺序执行，够用；后面需要再并发）
        for tc in assistant["tool_calls"]:
            name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"] or "{}")
            result = await registry.dispatch(name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

    return "（达到最大步数上限，仍未产出最终答复）"
