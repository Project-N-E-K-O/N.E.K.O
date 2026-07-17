# 后台深话题 hooks

> **文档性质：current implementation record + future research boundary。** 当前 hooks、物料池、准备和投递链路已实现；多轮 research agent 仍是后续方向，不能写成现状。

## 当前模块

- `main_logic/topic/signals.py`：候选信号；
- `main_logic/topic/hooks.py`：从会话产出结构化 hook；
- `main_logic/topic/materials.py`：物料归一化；
- `main_logic/topic/pipeline.py`：池、去重、ready 与持久化；
- `main_logic/topic/delivery.py`：投递和 ack/defer；
- `main_logic/core/proactive.py`：活动/语音/主动对话门控；
- `main_routers/system_router/proactive_sources.py` 与 `proactive_chat_flow.py`：主动对话消费。

## 契约

hook 是“值得以后继续”的信号，不是强制发送指令。结构化候选包含兴趣、关键词、相关性、风险和必要的来源上下文；阈值由后端门控，不应写进 prompt 诱导模型迎合。

- 隐私或敏感来源不能进入 deep-topic 链路；
- 去重以规范化关键词/语义证据为主，文本 n-gram 只作兜底；
- `is_ready` 表示物料充分，不表示当前时机允许打扰；
- 实时语音、忙碌或受限场景应 defer，而不是错误标记为已使用；
- 只有真正渲染给用户的 reflection/topic 才能记录 surfaced id；
- 投递失败保持可重试语义，同时防止陈旧任务跨 session 送达；
- prompt 输入必须有 token 预算，联网内容也要经过长度和来源限制。

## 当前数据流

```text
conversation/reflection signals
  -> hook extraction
  -> normalize + deduplicate + persist
  -> optional prepare/search
  -> proactive delivery gate
  -> deliver and acknowledge, or defer
```

## Future research boundary

多轮检索、证据合并和 research-agent 状态机仍属于 proposal。未来实现必须保留预算、取消、来源标注、隐私门和 defer/ack 语义，不能绕过当前 pool 直接发送。

## 验证

```bash
uv run pytest tests/unit/test_topic_common.py tests/unit/test_topic_signals.py tests/unit/test_topic_hooks.py tests/unit/test_topic_materials.py tests/unit/test_topic_pipeline.py tests/unit/test_topic_delivery.py tests/unit/test_system_router_topic_hooks.py -q
```
