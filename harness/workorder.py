"""工单状态机 —— 安全链的骨架（harness 固定核心）。

候选操作从生成到执行，必须走一条受控流转，不能从"生成"直接跳"执行"：

    pending ──复核──> reviewed ──人批──> approved ──> executing ──> done
        │                │                              │
        └──复核拒绝──────┴──人批拒绝──> rejected          └──> failed ──> rolled_back

写操作（改 bid / 改预算 / 否词）不能绕过这条链。状态机本身是 harness 固定的，
领域插件只能往里塞候选，不能改流转规则。
"""
from __future__ import annotations

import enum
import itertools
from dataclasses import dataclass, field


class State(str, enum.Enum):
    PENDING = "pending"         # 已生成，待复核
    REVIEWED = "reviewed"       # LLM 复核完成（同意/改参）
    REJECTED = "rejected"       # 复核拒绝 或 人批拒绝
    APPROVED = "approved"       # 人批通过
    EXECUTING = "executing"     # 正在写操作
    DONE = "done"               # 执行成功
    FAILED = "failed"           # 执行失败（待回滚）
    ROLLED_BACK = "rolled_back"

# 合法流转（状态机守卫）
_TRANSITIONS: dict[State, set[State]] = {
    State.PENDING: {State.REVIEWED, State.REJECTED},
    State.REVIEWED: {State.APPROVED, State.REJECTED},
    State.APPROVED: {State.EXECUTING},
    State.EXECUTING: {State.DONE, State.FAILED},
    State.FAILED: {State.ROLLED_BACK},
    State.REJECTED: set(),
    State.DONE: set(),
    State.ROLLED_BACK: set(),
}

_id = itertools.count(1)


@dataclass
class WorkOrder:
    candidate: dict
    state: State = State.PENDING
    verdict: dict | None = None   # LLM 复核结果 {verdict, reason, proposed}
    audit: list[dict] = field(default_factory=list)
    id: str = field(default_factory=lambda: f"WO-{next(_id):04d}")

    @property
    def lever(self) -> str:
        return self.candidate.get("lever", "")

    @property
    def target_name(self) -> str:
        return self.candidate.get("target_name", "")

    def transition(self, to: State, reason: str = "") -> None:
        if to not in _TRANSITIONS[self.state]:
            raise ValueError(f"非法流转: {self.state.value} -> {to.value}")
        self.audit.append({"from": self.state.value, "to": to.value, "reason": reason})
        self.state = to

    def apply_verdict(self, verdict: dict) -> None:
        """把 LLM 复核结果落到工单：反对→rejected；同意/改参→reviewed。

        modify 只能覆盖候选里已有的 proposed 参数键，防止 LLM 乱改 op_type 或目标。
        """
        v = verdict.get("verdict")
        if v == "reject":
            self.verdict = verdict
            self.transition(State.REJECTED, f"复核拒绝: {verdict.get('reason')}")
            return

        # approve 或 modify → reviewed
        if v == "modify":
            proposed = verdict.get("proposed")
            if isinstance(proposed, dict):
                allowed = set(self.candidate.get("proposed", {}))
                safe = {k: val for k, val in proposed.items() if k in allowed}
                if safe:
                    self.candidate["proposed"] = {**self.candidate.get("proposed", {}), **safe}
                else:
                    self.candidate["rationale"] = (
                        self.candidate.get("rationale", "") + f"（LLM 建议改参但键非法，忽略）"
                    )
        self.verdict = verdict
        self.transition(State.REVIEWED, f"复核{'改参' if v == 'modify' else '同意'}")
