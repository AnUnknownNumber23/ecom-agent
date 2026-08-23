"""执行器 —— 快照 + 回滚 + 熔断（harness 固定核心）。

approved 的工单在这里真正"落地"。shadow mode 下落地 = 生成操作清单的一行
（不写 Amazon）；快照/回滚/熔断是 harness 固定机制，换真实写操作（ERP API）
时只需替换 _execute 的实现，其余不动。
"""
from __future__ import annotations

from .workorder import State, WorkOrder


class CircuitBreakerError(Exception):
    """熔断触发：停止整条流水线。"""


def build_row(wo: WorkOrder) -> dict:
    """把一个工单拍平成操作清单里的一行（供人照做 / 程序对接）。"""
    c = wo.candidate
    proposed = c.get("proposed") or {}
    current = c.get("current") or {}
    # 数值字段：bid / daily_budget / suggested_bid；否词只有 match_type
    val_keys = [k for k in proposed if k in ("bid", "daily_budget", "suggested_bid")]
    field = val_keys[0] if val_keys else "match_type"
    return {
        "工单号": wo.id,
        "杠杆": c.get("lever", ""),
        "动作": c.get("op_type", ""),
        "目标": c.get("target_name", ""),
        "字段": field,
        "旧值": current.get(field, "") if isinstance(current, dict) else "",
        "新值": proposed.get(field, ""),
        "匹配类型": proposed.get("match_type", ""),
        "理由": (wo.verdict or {}).get("reason", "") or c.get("rationale", ""),
    }


class Executor:
    def __init__(self, max_ops: int = 50, fail_rate: float = 0.3, min_ops: int = 3):
        self.max_ops = max_ops
        self.fail_rate = fail_rate
        self.min_ops = min_ops  # 熔断前至少尝试 N 个操作，避免单个失败就触发

    def run(self, orders: list[WorkOrder]) -> list[dict]:
        pending = [w for w in orders if w.state == State.APPROVED]
        if len(pending) > self.max_ops:
            raise CircuitBreakerError(f"操作数 {len(pending)} 超过熔断阈值 {self.max_ops}")

        results: list[dict] = []
        failed = done = 0
        for wo in pending:
            snapshot = self._snapshot(wo)
            wo.transition(State.EXECUTING, "开始执行")
            try:
                row = self._execute(wo)
                wo.transition(State.DONE, "执行成功")
                row["结果"] = "done"
                results.append({"order": wo, "row": row, "ok": True})
            except Exception as e:
                failed += 1
                wo.transition(State.FAILED, f"执行失败: {e}")
                self._rollback(wo, snapshot)
                wo.transition(State.ROLLED_BACK, f"已回滚到 {snapshot}")
                results.append({"order": wo, "row": None, "ok": False, "error": str(e)})
            done += 1
            if done >= self.min_ops and failed / done > self.fail_rate:
                raise CircuitBreakerError(
                    f"失败率 {failed}/{done} 超过熔断阈值 {self.fail_rate:.0%}，停止流水线")
        return results

    def _snapshot(self, wo: WorkOrder) -> dict:
        cur = wo.candidate.get("current")
        return dict(cur) if isinstance(cur, dict) else {}

    def _rollback(self, wo: WorkOrder, snapshot: dict) -> None:
        # shadow mode 下外部无改动，仅记录回滚动作；真实模式此处调 API 恢复 snapshot
        wo.audit.append({"rollback_to": snapshot})

    def _execute(self, wo: WorkOrder) -> dict:
        # shadow mode：不写外部，只生成操作清单的一行
        return build_row(wo)
