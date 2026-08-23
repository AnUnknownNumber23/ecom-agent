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

```
harness/
  tools.py      Tool 协议 + 注册表 + 演示工具
  provider.py   LLM 适配（openai SDK → 任意 OpenAI 兼容端点）
  loop.py       ReAct 循环
  workorder.py  工单状态机（pending→reviewed→approved→executing→done）
  review.py     LLM 复核（只审不造，复核失败即安全失败）
  approval.py   人批（reviewed → approved/rejected）
  executor.py   执行器（快照 + 回滚 + 熔断）
amazon_ads/
  data.py       读报表 CSV 并归一化
  rules.py      规则引擎（纯函数，确定性）
  policy.py     复核政策（领域知识，随插件换）
data/           广告报表 mock 数据（搜索词/关键词/活动）
main.py         D1 CLI 入口
rules_cli.py    D2 规则引擎入口（python rules_cli.py）
review_cli.py   D3 复核 + 工单入口（python review_cli.py）
execute_cli.py  D4 全链路入口（python execute_cli.py --yes）
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

## 路线图

- D1 最小循环 ✅
- D2 广告报表 CSV + 确定性规则引擎 ✅
- D3 工单状态机 + LLM 复核 ✅（护栏已内建于规则阈值 + 单步封顶）
- D4 人批 + 快照 + 回滚 + 熔断 + 执行（shadow mode）✅
- D5 LangGraph 迁移（interrupt + checkpoint）
- D6 上下文 + 记忆
- D7 抽象 DomainPack 接口 + 换领域验收
