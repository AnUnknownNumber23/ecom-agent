"""Tool 协议与注册表 —— harness 的插件边界。

每个工具 = name + JSON Schema + async run()。harness 只认这个协议，
不关心工具内部干什么。这就是"一切皆插件"的第一层：换领域 = 换工具集。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable

Handler = Callable[[dict[str, Any]], Awaitable[str]]


class Tool:
    def __init__(self, name: str, description: str, parameters: dict, handler: Handler):
        self.name = name
        self.description = description
        self.parameters = parameters
        self._handler = handler

    async def run(self, args: dict[str, Any]) -> str:
        return await self._handler(args)

    def to_openai(self) -> dict:
        """转成 OpenAI function-calling 的工具描述。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"未知工具: {name}")
        return self._tools[name]

    def all_openai(self) -> list[dict]:
        return [t.to_openai() for t in self._tools.values()]

    async def dispatch(self, name: str, args: dict[str, Any]) -> str:
        """执行一个工具，失败时把错误喂回模型而不是崩掉循环。"""
        tool = self.get(name)
        try:
            return await tool.run(args)
        except Exception as e:  # noqa: BLE001 —— 工具失败不能让整个 loop 崩
            return f"工具执行出错: {e}"


# ---- 演示工具（领域无关，只为证明循环跑通） --------------------------------

async def _get_time(_args: dict[str, Any]) -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def _echo(args: dict[str, Any]) -> str:
    return str(args.get("text", ""))


async def _add(args: dict[str, Any]) -> str:
    a = float(args.get("a", 0))
    b = float(args.get("b", 0))
    return str(a + b)


def demo_registry() -> ToolRegistry:
    """三个演示工具，分别覆盖无参 / 字符串参 / 结构化参。"""
    reg = ToolRegistry()
    reg.register(Tool(
        "get_time", "获取当前日期和时间",
        {"type": "object", "properties": {}, "required": []},
        _get_time,
    ))
    reg.register(Tool(
        "echo", "原样返回一段文本",
        {"type": "object",
         "properties": {"text": {"type": "string", "description": "要返回的文本"}},
         "required": ["text"]},
        _echo,
    ))
    reg.register(Tool(
        "add", "两个数字相加",
        {"type": "object",
         "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
         "required": ["a", "b"]},
        _add,
    ))
    return reg
