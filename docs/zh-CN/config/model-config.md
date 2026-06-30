# 模型配置

N.E.K.O. 针对不同任务使用不同的 AI 模型，每个模型都可以单独配置。

## 模型角色

每个角色实际使用的模型由**所选的辅助 API 提供商**（`config/api_providers.json` → `assist_api_providers`）决定，因此并不存在单一的全局默认值——每个提供商各自附带一套按角色划分的模型。下表列出 OpenAI、Claude（Anthropic）、Qwen 三家提供商各自的出厂默认值。（`config/__init__.py` 里的 `DEFAULT_*_MODEL` 常量只是最后兜底的回退值。）

| 角色 | 配置字段 | OpenAI | Claude | Qwen |
|------|----------|--------|--------|------|
| 对话 | `conversation_model` | `gpt-5-chat-latest` | `claude-sonnet-4-6` | `qwen3.7-plus-2026-05-26` |
| 摘要 | `summary_model` | `gpt-5-chat-latest` | `claude-sonnet-4-6` | `qwen3.7-plus-2026-05-26` |
| 纠错 | `correction_model` | `gpt-5-chat-latest` | `claude-sonnet-4-6` | `qwen3.7-plus` |
| 情感 | `emotion_model` | `gpt-4.1-nano` | `claude-haiku-4-5-20251001` | `qwen3.6-flash` |
| 视觉 | `vision_model` | `gpt-5-chat-latest` | `claude-sonnet-4-6` | `qwen3.7-plus-2026-05-26` |
| Agent | `agent_model` | `glm-5v-turbo` | `claude-opus-4-6` | `qwen3.7-plus` |

> **说明：** `agent_model` 是 agent（Computer Use）任务 grounding 所用的模型。多个 provider 在此处会刻意选用强视觉/grounding 模型，不必与自身厂商一致——例如 OpenAI provider 的 `agent_model` 就是 `glm-5v-turbo`（一个 GLM 视觉模型）。这些值原样取自 `config/api_providers.json`，是有意为之而非笔误。

## 自定义模型端点

每个模型角色都可以使用自定义 API 端点。可以通过 `core_config.json` 或 Web UI 进行配置：

```json
{
  "conversationModel": "custom-model-name",
  "conversationModelUrl": "https://custom-api.example.com/v1",
  "conversationModelApiKey": "sk-xxxxx"
}
```

当设置了自定义 URL/密钥时，它会覆盖该特定角色的全局 Assist API 提供商。

## Computer Use 模型

Computer Use 需要两个视觉模型：

| 角色 | 默认值 | 用途 |
|------|--------|------|
| 规划模型 | `qwen3-vl-plus-2025-09-23` | 分析截图并规划操作 |
| 定位模型 | `qwen3-vl-plus-2025-09-23` | 定位 UI 元素以进行点击 |

通过 `core_config.json` 配置：

```json
{
  "computerUseModel": "qwen3-vl-plus-2025-09-23",
  "computerUseModelUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "computerUseModelApiKey": "sk-xxxxx",
  "computerUseGroundModel": "qwen3-vl-plus-2025-09-23",
  "computerUseGroundUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "computerUseGroundApiKey": "sk-xxxxx"
}
```

## 思考模式配置

部分模型支持"思考"或"扩展推理"模式。N.E.K.O. 默认禁用这些模式以获得更快的响应速度。禁用格式因提供商而异：

| 提供商 | 禁用格式 |
|--------|----------|
| Qwen、Step、DeepSeek | `{"enable_thinking": false}` |
| GLM | `{"thinking": {"type": "disabled"}}` |
| Gemini 2.x | `{"thinking_config": {"thinking_budget": 0}}` |
| Gemini 3.x | `{"thinking_config": {"thinking_level": "low"}}` |

此功能在 `config/__init__.py` 中根据模型名称自动处理。

## 图像速率限制

| 设置 | 默认值 | 说明 |
|------|--------|------|
| `NATIVE_IMAGE_MIN_INTERVAL` | 1.5 秒 | 屏幕截图的最小间隔 |
| `IMAGE_IDLE_RATE_MULTIPLIER` | 5 倍 | 无语音活动时的倍数 |
