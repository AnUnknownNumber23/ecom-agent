"""最小 harness 入口 —— 对话 + 工具调用。

用法:
  1. 复制 .env.example 为 .env，填 LLM_API_KEY
  2. pip install -r requirements.txt
  3. python main.py

D6：系统提示词从文件加载（harness/system.md + AGENTS.md + USER.md），
对话过长时自动压缩早期上下文（token 预算用环境变量 CONTEXT_BUDGET_TOKENS 控制）。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Windows 控制台默认 GBK，这里强制 UTF-8 输出，避免中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from harness.compact import compact_history
from harness.context import build_context
from harness.loop import run_turn
from harness.provider import from_env
from harness.tools import demo_registry

ROOT = Path(__file__).parent
SYSTEM_PATH = ROOT / "harness" / "system.md"
AGENTS_PATH = ROOT / "AGENTS.md"
USER_PATH = ROOT / "USER.md"

_FALLBACK_SYSTEM = "你是一个测试助手，回答要简洁。"


async def main() -> None:
    load_dotenv()
    llm = from_env()
    if not llm.client.api_key:
        print("缺少 LLM_API_KEY：请复制 .env.example 为 .env 并填入 key")
        return

    registry = demo_registry()
    messages = build_context(SYSTEM_PATH, [AGENTS_PATH], USER_PATH)
    if not messages:
        messages = [{"role": "system", "content": _FALLBACK_SYSTEM}]
    budget = int(os.environ.get("CONTEXT_BUDGET_TOKENS", "4000"))

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
        if await compact_history(llm, messages, budget):
            print("（上下文已压缩）")
        reply = await run_turn(llm, registry, messages)
        print(f"\n助手> {reply}")


if __name__ == "__main__":
    asyncio.run(main())
