"""D5 入口：LangGraph 编排（interrupt 人批 + checkpoint 跨进程恢复 + 回退重批）。

用法:
  python graph_cli.py                              # 跑到人批暂停，打印 thread_id，退出
  python graph_cli.py --yes                        # 跑 + 自动批准全部 + 写 CSV（一条龙）
  python graph_cli.py --resume <thread_id> [--yes] # 从 checkpoint 续跑（交互式人批）
  python graph_cli.py --rewind <thread_id> [--yes] # 回退到人批那一步，重新批准
  python graph_cli.py --list                       # 列出所有 thread_id 及其状态
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from amazon_ads import DEFAULT_CONFIG, load_csv, run_analysis
from harness.executor import CircuitBreakerError
from harness.graph import build_app

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DATA_DIR = Path(__file__).parent / "data"
OUTPUT_DIR = Path(__file__).parent / "output"
DB = OUTPUT_DIR / "checkpoints.db"
OUTPUT = OUTPUT_DIR / "actions.csv"

_FIELDS = ["工单号", "杠杆", "动作", "目标", "字段", "旧值", "新值", "匹配类型", "结果", "理由"]


def _flag_value(argv: list[str], name: str) -> str | None:
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
    return None


def load_candidates() -> list[dict]:
    search = load_csv(DATA_DIR / "search_term_report.csv")
    keyword = load_csv(DATA_DIR / "keyword_report.csv")
    campaign = load_csv(DATA_DIR / "campaign_report.csv")
    result = run_analysis(search, keyword, campaign, DEFAULT_CONFIG)
    print(f"目标ACOS={result['target_acos']:.1%}，规则引擎产出 {result['count']} 条候选\n")
    return result["candidates"]


def write_csv(actions: list[dict], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_FIELDS)
        w.writeheader()
        for row in actions:
            w.writerow(row)
    return len(actions)


def _print_pending(pending: list[dict]) -> None:
    print(f"以下 {len(pending)} 条等待人工批准：\n")
    for p in pending:
        print(f"  [{p['index']}] ({p['lever']}) {p['target']}: {p['current']} → {p['proposed']}")
    print()


def _ask_approve(pending: list[dict]) -> dict:
    """交互式人批，返回 resume 值 {"approved": [index...]}。"""
    _print_pending(pending)
    while True:
        raw = input("批准哪些？(编号，逗号分隔 / all 全部 / q 取消): ").strip().lower()
        if raw in ("q", "quit"):
            print("已取消。")
            raise SystemExit(0)
        if raw in ("all", "a", ""):
            return {"approved": [p["index"] for p in pending]}
        try:
            idxs = [int(x) for x in raw.replace("，", ",").split(",") if x.strip()]
            valid = {p["index"] for p in pending}
            return {"approved": [i for i in idxs if i in valid]}
        except ValueError:
            print("输入非法，重试。\n")


def _resume(app, config: dict, decision: dict) -> dict | None:
    try:
        return app.invoke(Command(resume=decision), config)
    except CircuitBreakerError as e:
        print(f"🛑 熔断触发：{e}")
        return None


def _write_result(result: dict | None) -> None:
    if result is None:
        return
    actions = result.get("actions", [])
    written = write_csv(actions, OUTPUT)
    if written:
        print(f"\n操作清单已写入 {OUTPUT}（{written} 条），请照此在 Amazon 后台手动执行。")
        for row in actions:
            print(f"  ✓ [{row['杠杆']}] {row['目标']}: {row['旧值']} → {row['新值']}")
    else:
        print("\n没有可执行的操作（全部被否决或未批准）。")


def _after_interrupt(app, config: dict, result: dict, auto_yes: bool, thread_id: str) -> None:
    """中断后的公共处理：--yes 则续跑到底，否则打印 thread_id 等外部续跑。"""
    if "__interrupt__" not in result:
        _write_result(result)
        return
    pending = result["__interrupt__"][0].value["pending"]
    if auto_yes:
        _write_result(_resume(app, config, {"approved": [p["index"] for p in pending]}))
    else:
        _print_pending(pending)
        print("已暂停等待人工批准（非阻塞）。")
        print(f"  thread_id = {thread_id}")
        print(f"  续跑：python graph_cli.py --resume {thread_id} [--yes]")


def _do_run(app, auto_yes: bool) -> None:
    candidates = load_candidates()
    thread_id = f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    config = {"configurable": {"thread_id": thread_id}}
    result = app.invoke({"candidates": candidates}, config)
    _after_interrupt(app, config, result, auto_yes, thread_id)


def _do_resume(app, thread_id: str, auto_yes: bool) -> None:
    config = {"configurable": {"thread_id": thread_id}}
    snap = app.get_state(config)
    pending = snap.interrupts[0].value["pending"] if snap.interrupts else []
    if not pending:
        print("没有待批项（可能已跑完或 thread_id 不存在）。")
        return
    decision = {"approved": [p["index"] for p in pending]} if auto_yes else _ask_approve(pending)
    _write_result(_resume(app, config, decision))


def _do_rewind(app, thread_id: str, auto_yes: bool) -> None:
    config = {"configurable": {"thread_id": thread_id}}
    if not app.get_state(config).values:
        print(f"thread_id {thread_id} 不存在。")
        return
    # time travel：把状态拨回 review 之后，让 approve 重跑（重新中断）。
    # 同时清掉上一轮的 approvals/actions 残留，避免「等待人批」却还显示旧操作。
    app.update_state(config, {"approvals": {}, "actions": []}, as_node="review")
    result = app.invoke(None, config)
    print(f"已回退到人批步骤（thread_id = {thread_id}）。\n")
    _after_interrupt(app, config, result, auto_yes, thread_id)


def _do_list(app) -> None:
    """列出所有 thread_id 及其当前状态（等待人批 / 已完成 / 进行中）。"""
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute(
            "SELECT thread_id, COUNT(*) FROM checkpoints GROUP BY thread_id ORDER BY thread_id"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("还没有任何检查点（先跑一次 python graph_cli.py）。")
        return

    print("已存在的 thread_id：\n")
    for tid, n in rows:
        snap = app.get_state({"configurable": {"thread_id": tid}})
        values = snap.values
        if snap.interrupts:
            pending = snap.interrupts[0].value.get("pending", [])
            print(f"  {tid}  （{n} 个检查点）\n      ⏸ 等待人批（{len(pending)} 条）→ python graph_cli.py --resume {tid}")
        elif snap.next:
            print(f"  {tid}  （{n} 个检查点）\n      … 进行中 next={snap.next}")
        else:
            print(f"  {tid}  （{n} 个检查点）\n      ✅ 已完成（{len(values.get('actions', []))} 条操作）→ python graph_cli.py --rewind {tid}")
    print()


def main() -> None:
    load_dotenv()
    argv = sys.argv[1:]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with SqliteSaver.from_conn_string(str(DB)) as checkpointer:
        app = build_app(checkpointer=checkpointer)
        if "--list" in argv:
            _do_list(app)
        elif resume_id := _flag_value(argv, "--resume"):
            _do_resume(app, resume_id, "--yes" in argv)
        elif rewind_id := _flag_value(argv, "--rewind"):
            _do_rewind(app, rewind_id, "--yes" in argv)
        else:
            _do_run(app, "--yes" in argv)


if __name__ == "__main__":
    main()
