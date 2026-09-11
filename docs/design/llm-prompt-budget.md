# LLM Prompt Budget

> **文档性质：current implementation guidelines。** 本页描述当前输入/输出预算守门和审计方式。具体模型上下文窗口、价格与 provider 限制会变化，必须以当前配置和官方 provider 合同为准。

## 两类预算

1. **输出预算**：每个 LLM client 构造点都要明确 token 上限与 timeout，避免 provider 默认值造成失控成本或长时间挂起。
2. **输入预算**：每个动态调用点要在组装 prompt 前限制历史、检索结果、工具输出、图片描述和用户附件；不能只依赖模型端截断。

静态检查位于 `scripts/check_llm_budget.py`，规则代码为 `LLM_OUTPUT_BUDGET` 和 `LLM_INPUT_BUDGET`。`# noqa` 只允许用于已有等价预算且检查器无法识别的场景，并在同一行说明理由。

## 配置与审计

- 模型默认配置位于 `config/model_defaults.py` 及相关 settings 模块；
- `NEKO_LLM_PROMPT_AUDIT=1` 可启用输入审计；
- 审计只能记录长度、角色、来源类别、截断结果等必要元数据，不能默认记录原始私密对话；
- provider 特有字段由其 client/adapter 负责，不能假定所有服务接受同名 token 参数。

## 组装顺序

```text
固定 system contract
  + 有上限的角色/会话上下文
  + 有上限的历史摘要或最近消息
  + 有上限的检索/工具材料
  + 当前用户输入
  -> provider-aware token estimate
  -> deterministic trimming
  -> LLM call with output budget + timeout
```

裁剪优先删除低价值、可重新获取的材料；不能删除安全、水印或当前任务必需的 system contract。用户输入也要有 API 层总大小限制，不能以“用户自担风险”为由允许无界输入。

## Theater

Numeric v2 的固定预算定义在 `services/theater/numeric_v2_budget.py`；`economy`、`balanced`、`quality` 仅是旧存档兼容别名，共用以下预算，不再提供容量档位选择。推荐已并入同一次 Actor 调用：

| 档位 | Actor 总输入 | 历史 Token / 回合 | 连续性胶囊 |
| --- | ---: | ---: | ---: |
| 共用标准预算 | 10000 | 5200 / 12 | 1600 |

- Actor 输出上限按阶段分别为普通回合 700、初始开场 900、换场 1200 Token；
- Evaluator 输入上限为 7000、输出上限为 360 Token；共享历史证据上限为 1500 Token / 12 条；
- 普通正文及已有邀请留幕回应也经过复核：普通输入上限 6000、输出 190 Token；正式转场复核输入 8000、输出 512 Token，两者 timeout 均为 8 秒。首次争议可独立复查一次，输出 4096 Token、timeout 30 秒；整轮共享一次语义改稿。语义额度耗尽与技术失败分别按工作流合同处理，详见[小剧场架构](./neko-theater-architecture.md)；
- 正文合法但推荐格式失败时，Actor 只允许一次 260 Token 的补推荐调用，最多返回 3 条候选；
- 超预算时普通回合只从较早完整历史回合开始整项淘汰，换场则使用独立的紧凑 `transition` 上下文；不截断安全合同、当前玩家输入、当前幕/目标幕必要信息或 Runtime 交付指令；
- v2.2 不再把 `goal_progress`、完成证据胶囊或来源完成胶囊作为 Actor 上下文；当前已发生内容只来自真实历史、仍影响当前的关键事实和作者明确声明的连续性状态，不能把目标描述冒充已发生事实。

## 验证

```bash
uv run python scripts/check_llm_budget.py
uv run pytest tests/unit/test_check_llm_budget.py -q
```

新增 LLM 调用时同时回答：输入各段上限是多少、输出上限是多少、timeout 是多少、失败如何降级、审计是否泄露正文。
