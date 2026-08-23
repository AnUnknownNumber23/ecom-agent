"""上下文构建 —— 系统提示词 + 项目/用户指令，拼成初始上下文。

对标 Claude Code 的分层指令：
  - system.md   基础提示词（harness 自带，描述 agent 是什么、怎么用工具）
  - AGENTS.md   项目说明（这个仓库的约定，进仓库共享）
  - USER.md     用户偏好（个人，通常不提交）

三层都只是文本，harness 只负责读文件拼进一条 system 消息；内容随插件/仓库换，
harness 不关心里面写了什么。这就是"上下文"作为插件边界。
"""
from __future__ import annotations

from pathlib import Path


def read_text(path: Path | None) -> str:
    """读一个文本文件，不存在或读不了返回空串（指令文件缺失不该崩）。"""
    if not path:
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def build_context(system_path: Path | None = None,
                  agents_paths: list[Path] | None = None,
                  user_path: Path | None = None) -> list[dict]:
    """拼出初始 messages（单条 system，分节）。没有任何内容时返回空列表。"""
    sections: list[str] = []

    base = read_text(system_path)
    if base:
        sections.append(base)

    for p in agents_paths or []:
        c = read_text(p)
        if c:
            sections.append(f"# 项目说明（AGENTS.md）\n{c}")

    user = read_text(user_path)
    if user:
        sections.append(f"# 用户偏好（USER.md）\n{user}")

    if not sections:
        return []
    return [{"role": "system", "content": "\n\n".join(sections)}]
