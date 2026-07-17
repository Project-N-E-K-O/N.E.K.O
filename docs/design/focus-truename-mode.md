# Focus / True-Name 模式

> **文档性质：current implementation record + proposal boundary。** Focus 是本仓库已有但默认关闭的 v1 能力；True Name 仅保留枚举和产品边界，没有可达的 v1 状态迁移。不得把 True Name 写成已上线功能。

## 当前状态

Focus 通过信号评分和带滞回的状态机，让少量高价值回复使用 provider 对应的思考参数。总开关 `FOCUS_MODE_ENABLED` 默认关闭；阈值、provider 成本和可见输出尚需按真实分布验证后再启用。

当前实现分布在：

- `config/focus_settings.py`：开关、阈值、保留率和回合上限；
- `config/prompts/prompts_focus.py`：脆弱性词表和主题切换检测；
- `main_logic/activity/focus_scorer.py`：消息级轻量评分；
- `main_logic/session_state.py`：`CognitionMode`、charge、滞回、episode 与 enter/exit；
- `main_logic/core/focus.py`：manager 侧 inline 决策、idle 读取和冷却；
- `main_logic/core/streaming.py`：把 Focus 决策带入可见回复；
- `main_logic/omni_offline_client/_streaming.py`：把 per-call provider override 传入流式请求；
- `tests/unit/test_focus_mode.py`：状态机、评分、provider override、竞态与清理回归。

## 状态模型

v1 的可达状态只有：

```text
REGULAR --charge >= enter threshold--> FOCUS
FOCUS   --decay/topic change/hard cap--> REGULAR
```

`CognitionMode.TRUE_NAME` 是 v2 占位；当前 `_focus_decide` 不会进入它。

Focus charge 由用户消息的 inline 评分增加，并通过保留率衰减。主动对话只能读取当前 Focus 状态并在回合结束后冷却，不能凭空把 REGULAR 推入 FOCUS。episode/turn token 用来阻止旧的异步回调影响新 episode。

## 思考参数

Focus 不重建 client，而是在单次请求上生成 provider 对应的 override。不同 provider 的字段和“开启思考”语义不同，映射以 `config/providers.py` 和 `_focus_stream_overrides` 的测试为准。未知 provider 必须安全降级，不能假定所有 API 都接受同一字段。

## 隐私与记忆边界

Focus 评分是会话控制信号，不应把命中的原始私密文本写入额外日志。Focus 退出后的专用记忆提炼/清理仍不是完整公开契约；在对应订阅者和端到端测试落地前，不要宣称 Focus 自动产生长期记忆。

## True Name 提案边界

True Name 设想用于高风险、可审阅的人设或记忆重写。若未来实施，至少需要：

- 用户明确同意，而不是只凭情绪信号自动进入；
- 生成建议与实际写入分离；
- 可见 diff、回滚和审计；
- 对记忆、人设和外部任务分别授权；
- 独立的状态迁移和安全测试。

这些约束是 proposal，不是当前 API。

## 验证

```bash
uv run pytest tests/unit/test_focus_mode.py tests/unit/test_master_emotion.py -q
```

修改 Focus 时还应检查普通文本回复、主动对话和各 provider 的流式路径，避免默认关闭时改变 REGULAR 行为。
