"""Ivyea harness —— 领域无关的 agent 运行时。

第 0 刀：最小闭环 = Tool 协议 + LLM 适配 + ReAct 循环。
领域逻辑（数据源 / 规则引擎 / 写操作）后续作为 DomainPack 挂进来。
"""
