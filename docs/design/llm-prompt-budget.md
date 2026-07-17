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

## 验证

```bash
uv run python scripts/check_llm_budget.py
uv run pytest tests/unit/test_check_llm_budget.py -q
```

新增 LLM 调用时同时回答：输入各段上限是多少、输出上限是多少、timeout 是多少、失败如何降级、审计是否泄露正文。
