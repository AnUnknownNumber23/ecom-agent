"""最小 harness 入口 —— 对话 + 工具调用。

用法:
  1. 复制 .env.example 为 .env，填 LLM_API_KEY
  2. pip install -r requirements.txt
  3. python main.py
"""
from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

# Windows 控制台默认 GBK，这里强制 UTF-8 输出，避免中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from harness.loop import run_turn
from harness.provider import from_env
from harness.tools import demo_registry

SYSTEM_PROMPT = (
    "你是一个最小 Agent harness 的测试助手。"
    "当用户的问题需要工具才能回答时，主动调用工具获取结果；"
    "不需要工具就直接回答。回答要简洁。"
)


async def main() -> None:
    load_dotenv()
    llm = from_env()
    if not llm.client.api_key:
        print("缺少 LLM_API_KEY：请复制 .env.example 为 .env 并填入 key")
        return

    registry = demo_registry()
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    print("最小 harness 已启动（输入 quit 退出）")

    while True:
        try:
            text = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.lower() in ("quit", "exit", "q"):
            break

        messages.append({"role": "user", "content": text})
        reply = await run_turn(llm, registry, messages)
        print(f"\n助手> {reply}")


if __name__ == "__main__":
    asyncio.run(main())
