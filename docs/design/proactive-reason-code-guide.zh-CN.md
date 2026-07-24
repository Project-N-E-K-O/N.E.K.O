# proactive_chat reason_code

> **文档性质：current implementation record。** `/api/proactive_chat` 的最终响应包含稳定的机器可读 `reason_code` 和粗粒度 `stage`。具体枚举以当前源码常量为准，本页不复制容易过时的完整列表。

## 用途

`action` 说明结果是 `chat`、`pass` 或错误；`reason_code` 说明为什么；`stage` 说明原因发生在流程哪一段。它们用于测试、诊断和聚合观测，不是用户文案，也不能反向改变主动对话策略。

当前实现位于：

- `main_routers/system_router/proactive_parsing.py`：reason/stage 常量、映射和响应 helper；
- `main_routers/system_router/proactive_chat_flow.py`：各退出点写入原因；
- `tests/unit/test_reflection_synthesis_loop.py`：响应完整性与 stage 映射回归。

## 响应形状

```json
{
  "success": true,
  "action": "pass",
  "reason_code": "pass_source_empty",
  "stage": "source_selection"
}
```

错误响应可以没有 `action`，但仍应有 reason/stage。兼容旧分支的 `_ensure_proactive_reason_code` 会补默认原因；新退出点必须直接提供最具体的 reason，不能依赖 fallback。

## 维护规则

- reason 使用稳定 snake_case，不包含角色名、模型回复或隐私数据；
- 一个 reason 只表达一个主要终止原因；竞态结束时选择最终生效的原因；
- 新 reason 同步 stage 映射和测试；
- 前端展示应把 code 映射为本地化文案，不直接显示内部字符串；
- 不根据旧文档穷举做业务判断，应容忍未知 code 并显示安全 fallback。

## 验证

```bash
uv run pytest tests/unit/test_reflection_synthesis_loop.py -q
```
