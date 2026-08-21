"""LLM 适配 —— 用 openai SDK 接任意 OpenAI 兼容端点。

DeepSeek / OpenAI / 本地 Ollama（http://localhost:11434/v1）都是同一个
client，只换 base_url / model。不做多 provider 网关——那是原项目为了支持
几十家厂商才有的复杂度，一家就够。
"""
from __future__ import annotations

import os
from typing import Any

from openai import AsyncOpenAI


class LLM:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    async def chat(self, messages: list[dict], tools: list[dict] | None = None,
                   response_format: dict | None = None) -> dict:
        """调用一次模型，返回 assistant 消息 dict：{role, content, tool_calls?}。

        response_format 用于要求 JSON 输出（如 {"type": "json_object"}），
        DeepSeek / OpenAI / Ollama 的 OpenAI 兼容端点都支持。
        """
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if response_format:
            kwargs["response_format"] = response_format
        resp = await self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message

        out: dict[str, Any] = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            out["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]
        return out


def from_env() -> LLM:
    return LLM(
        base_url=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"),
        api_key=os.environ.get("LLM_API_KEY", ""),
        model=os.environ.get("LLM_MODEL", "deepseek-chat"),
    )
