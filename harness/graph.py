"""D5：LangGraph 编排 —— 把 D3/D4 的 review → approve → execute 迁到状态图。

interrupt() 在 approve 节点把人批变成一个可跨进程暂停/恢复的检查点：

    START → review(LLM 复核) → approve(人批，interrupt 暂停) → execute(快照/回滚) → END

checkpoint 落在 SQLite（SqliteSaver），按 thread_id 恢复：
  - 第一个进程 invoke 到 approve 时返回 interrupt 载荷并暂停；
  - 第二个进程用同一个 thread_id + Command(resume=...) 续跑，不重跑 review。

节点只搬运 D3/D4 已有的函数（review_candidates / WorkOrder / Executor），
不改它们——这里只负责"编排"，安全链本身仍在 harness 各模块里。
"""
from __future__ import annotations

import asyncio
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: F401  (供 CLI 复用)
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from amazon_ads.policy import REVIEW_POLICY
from .executor import Executor
from .provider import from_env
from .review import review_candidates
from .workorder import State, WorkOrder


class GraphState(TypedDict):
    candidates: list          # 规则引擎候选（输入）
    verdicts: list            # LLM 复核结果（review 节点写）
    approvals: dict           # 人批决定 {approved: [index...]}（approve 节点写）
    actions: list             # 最终操作清单行（execute 节点写）


def build_app(llm=None, checkpointer=None):
    """编译状态图。llm 参数供测试注入，None 时惰性 from_env。"""

    def review_node(state: GraphState) -> dict:
        candidates = state["candidates"]
        active = llm if llm is not None else from_env()
        if not getattr(active.client, "api_key", None):
            # 无 key：安全否决全部，不调 LLM（复核失败即安全失败）
            verdicts = [
                {"verdict": "reject", "reason": "未配置 LLM_API_KEY，安全否决", "proposed": None}
                for _ in candidates
            ]
        else:
            verdicts = asyncio.run(review_candidates(active, candidates, REVIEW_POLICY))
        return {"verdicts": verdicts}

    def approve_node(state: GraphState) -> dict:
        candidates = state["candidates"]
        verdicts = state["verdicts"]
        pending = [
            {
                "index": i,
                "lever": c["lever"],
                "target": c["target_name"],
                "current": c.get("current"),
                "proposed": c.get("proposed"),
                "reason": v.get("reason"),
            }
            for i, (c, v) in enumerate(zip(candidates, verdicts))
            if v.get("verdict") != "reject"
        ]
        if not pending:
            return {"approvals": {"approved": []}}
        # 暂停等人批；resume 值 = {"approved": [index...]}
        decision = interrupt({"pending": pending})
        return {"approvals": decision}

    def execute_node(state: GraphState) -> dict:
        candidates = state["candidates"]
        verdicts = state["verdicts"]
        approvals = state["approvals"] or {}
        approved = set(approvals.get("approved", []))

        orders = [WorkOrder(c) for c in candidates]
        for wo, v in zip(orders, verdicts):
            wo.apply_verdict(v)
        for i, wo in enumerate(orders):
            if wo.state == State.REVIEWED:
                if i in approved:
                    wo.transition(State.APPROVED, "人批通过")
                else:
                    wo.transition(State.REJECTED, "人批拒绝")

        results = Executor().run(orders)
        actions = [r["row"] for r in results if r["ok"]]
        return {"actions": actions}

    g = StateGraph(GraphState)
    g.add_node("review", review_node)
    g.add_node("approve", approve_node)
    g.add_node("execute", execute_node)
    g.add_edge(START, "review")
    g.add_edge("review", "approve")
    g.add_edge("approve", "execute")
    g.add_edge("execute", END)
    return g.compile(checkpointer=checkpointer)
