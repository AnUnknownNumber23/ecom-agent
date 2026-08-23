# AGENTS.md

这是 ecom-agent：一个领域无关的 agent harness（对标 Claude Code / Codex 架构），
当前第一个领域是跨境电商（亚马逊广告报表优化）。

## 架构

- `harness/`：领域无关的固定核心 —— ReAct 循环、工具注册表、工单状态机、
  LLM 复核、人批、执行、LangGraph 编排。
- `amazon_ads/`：第一个 DomainPack —— 规则引擎 + 复核政策（换领域就换这个包）。

## 关键原则

- 确定性规则引擎做决策，LLM 只审不造（approve/reject/modify，不新增候选）。
- 写操作必须走 pending → reviewed → approved → executing → done 状态机，不能绕过。
- 安全链（工单状态机 / 人批 / 快照 / 回滚 / 熔断）是固定核心，领域只能参数化，不能替换。
