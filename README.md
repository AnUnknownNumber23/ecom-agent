# ecom-agent

领域无关的 agent harness（从 0 复现，对标 Claude Code / Codex 的架构）。

## 当前进度

- **D1（最小闭环）**：跑通「对话 + 工具调用」的 ReAct 循环。
- **D2（规则引擎）**：确定性规则引擎（否词/降bid/加bid/加预算/收割），
  输入广告报表 CSV，输出带证据的候选操作。**不接 LLM、不执行写操作。**
- **D3（安全链第一层）**：工单状态机 + LLM 复核。候选必须走
  `pending → reviewed → approved → executing`，LLM 只审不造（approve/reject/modify），
  复核失败 = 安全失败（不放行）。
- **D4（安全链第二层）**：人批 + 快照 + 回滚 + 熔断 + 执行（shadow mode，
  只生成操作清单 CSV，不写 Amazon）。
- **D5（LangGraph 编排）**：把 D3/D4 的 review→approve→execute 迁到状态图。
  approve 节点用 `interrupt()` 暂停人批，checkpoint 落 SQLite，可跨进程恢复
  （第一进程暂停打印 thread_id，第二进程 `--resume` 续跑，不重跑复核），
  也支持 `--rewind` 回退到人批那一步重新批准（time travel）。
- **D6（上下文 + 记忆）**：系统提示词从文件分层加载（`harness/system.md` 基础 +
  `AGENTS.md` 项目 + `USER.md` 个人，对齐 Claude Code 的层级指令），对话过长时
  按 token 预算自动压缩早期轮次为摘要、保留最近几轮原文（`harness/compact.py`）。

```
harness/
  tools.py      Tool 协议 + 注册表 + 演示工具
  provider.py   LLM 适配（openai SDK → 任意 OpenAI 兼容端点）
  loop.py       ReAct 循环
  workorder.py  工单状态机（pending→reviewed→approved→executing→done）
  review.py     LLM 复核（只审不造，复核失败即安全失败）
  approval.py   人批（reviewed → approved/rejected）
  executor.py   执行器（快照 + 回滚 + 熔断）
  graph.py      LangGraph 编排（review→approve→execute + interrupt + checkpoint）
  context.py    上下文构建（system.md + AGENTS.md + USER.md 分层合并）
  compact.py    上下文压缩（token 预算触发，压早期轮次为摘要）
  system.md     harness 基础系统提示词（测试助手）
AGENTS.md        项目说明（架构 + 关键原则）
USER.md.example  个人偏好模板（复制为 USER.md，通常不提交）
amazon_ads/
  data.py       读报表 CSV 并归一化
  rules.py      规则引擎（纯函数，确定性）
  policy.py     复核政策（领域知识，随插件换）
data/           广告报表 mock 数据（搜索词/关键词/活动）
main.py         D6 CLI 入口（分层上下文 + 自动压缩）
rules_cli.py    D2 规则引擎入口（python rules_cli.py）
review_cli.py   D3 复核 + 工单入口（python review_cli.py）
execute_cli.py  D4 全链路入口（python execute_cli.py --yes）
graph_cli.py    D5 编排入口（run / --yes / --list / --resume <id> / --rewind <id>）
```

## 跑起来

```bash
cp .env.example .env   # 填 LLM_API_KEY
pip install -r requirements.txt
python main.py
```

试试：

- `现在几点` → 模型调 `get_time`
- `帮我算 3.5 加 4.2` → 模型调 `add`

D5 编排（LangGraph）：

- `python graph_cli.py` → 跑到人批暂停，打印 thread_id 后退出（非阻塞）
- `python graph_cli.py --resume <thread_id> --yes` → 跨进程续跑并写 CSV
- `python graph_cli.py --rewind <thread_id> [--yes]` → 回退到人批那一步，重新批准
- `python graph_cli.py --list` → 列出所有 thread_id 及其状态（等待人批 / 已完成）
- `python graph_cli.py --yes` → 一条龙：跑 + 自动批准全部 + 写 CSV

D6 上下文 + 压缩：

- `python main.py` → 对话越长，超过 token 预算（`CONTEXT_BUDGET_TOKENS`，默认 4000）自动压缩
- 提示词分层：`harness/system.md`（基础）→ `AGENTS.md`（项目）→ `USER.md`（个人，可选）

## 路线图

- D1 最小循环 ✅
- D2 广告报表 CSV + 确定性规则引擎 ✅
- D3 工单状态机 + LLM 复核 ✅（护栏已内建于规则阈值 + 单步封顶）
- D4 人批 + 快照 + 回滚 + 熔断 + 执行（shadow mode）✅
- D5 LangGraph 迁移（interrupt + checkpoint）✅
- D6 上下文 + 记忆 ✅
- D7 抽象 DomainPack 接口 + 换领域验收
