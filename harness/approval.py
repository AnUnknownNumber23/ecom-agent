"""人批 —— 三重复核里的第三重（第一重规则护栏，第二重 LLM 复核）。

reviewed 的工单必须经过人工确认才能进入 executing。CLI 里是交互式确认；
execute_cli 加 --yes 时自动通过（演示/非交互环境用）。
"""
from __future__ import annotations

from .workorder import State, WorkOrder


def _summary(wo: WorkOrder) -> str:
    c = wo.candidate
    v = wo.verdict or {}
    mark = {"approve": "同意", "modify": "改参"}.get(v.get("verdict"), "—")
    return (f"[{wo.lever}] {wo.target_name}  "
            f"{c.get('current')} → {c.get('proposed')}  （复核={mark}）")


def approve_interactive(orders: list[WorkOrder]) -> None:
    """逐条确认 reviewed 工单：y=通过 n=拒绝 a=全部通过 q=放弃剩余。"""
    approve_all = False
    for wo in orders:
        if wo.state != State.REVIEWED:
            continue
        if approve_all:
            wo.transition(State.APPROVED, "人批通过(全部)")
            continue
        print(_summary(wo))
        ans = input("批准? [y]是 [n]否 [a]全部通过 [q]放弃剩余 > ").strip().lower()
        if ans == "a":
            wo.transition(State.APPROVED, "人批通过(全部)")
            approve_all = True
        elif ans in ("y", ""):
            wo.transition(State.APPROVED, "人批通过")
        elif ans == "q":
            wo.transition(State.REJECTED, "人批放弃剩余")
            break
        else:
            wo.transition(State.REJECTED, "人批拒绝")


def approve_all(orders: list[WorkOrder]) -> None:
    """非交互：所有 reviewed 工单直接通过。"""
    for wo in orders:
        if wo.state == State.REVIEWED:
            wo.transition(State.APPROVED, "人批通过(自动)")
