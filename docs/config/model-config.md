# Model Configuration

N.E.K.O. uses different AI models for different tasks. Each can be individually configured.

## Model roles

The model for each role is resolved from the **selected assist provider** (`config/api_providers.json` → `assist_api_providers`), so there is no single global default — each provider ships its own per-role models. The columns below show the shipped defaults for the OpenAI, Claude (Anthropic), and Qwen providers. (The `DEFAULT_*_MODEL` constants in `config/__init__.py` are last-resort fallbacks only.)

| Role | Config field | OpenAI | Claude | Qwen |
|------|--------------|--------|--------|------|
| Conversation | `conversation_model` | `gpt-5-chat-latest` | `claude-sonnet-4-6` | `qwen3.7-plus-2026-05-26` |
| Summary | `summary_model` | `gpt-5-chat-latest` | `claude-sonnet-4-6` | `qwen3.7-plus-2026-05-26` |
| Correction | `correction_model` | `gpt-5-chat-latest` | `claude-sonnet-4-6` | `qwen3.7-plus` |
| Emotion | `emotion_model` | `gpt-4.1-nano` | `claude-haiku-4-5-20251001` | `qwen3.6-flash` |
| Vision | `vision_model` | `gpt-5-chat-latest` | `claude-sonnet-4-6` | `qwen3.7-plus-2026-05-26` |
| Agent | `agent_model` | `glm-5v-turbo` | `claude-opus-4-6` | `qwen3.7-plus` |

> **Note:** `agent_model` is the model used for agent (Computer Use) task grounding. Several providers intentionally ship a strong vision/grounding model here regardless of their own family — e.g. the OpenAI provider's `agent_model` is `glm-5v-turbo` (a GLM vision model). These values are copied verbatim from `config/api_providers.json` and are by design, not typos.

## Custom model endpoints

Each model role can use a custom API endpoint. This is configured in `core_config.json` or via the Web UI:

```json
{
  "conversationModel": "custom-model-name",
  "conversationModelUrl": "https://custom-api.example.com/v1",
  "conversationModelApiKey": "sk-xxxxx"
}
```

When a custom URL/key is set, it overrides the global assist API provider for that specific role.

## Computer Use models

Computer Use requires two vision models:

| Role | Default | Purpose |
|------|---------|---------|
| Planning model | `qwen3-vl-plus-2025-09-23` | Analyze screenshots and plan actions |
| Grounding model | `qwen3-vl-plus-2025-09-23` | Locate UI elements for clicking |

Configure via `core_config.json`:

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

## Thinking mode configuration

Some models support "thinking" or "extended reasoning" modes. N.E.K.O. disables these by default for faster responses. The disable format varies by provider:

| Provider | Disable format |
|----------|---------------|
| Qwen, Step, DeepSeek | `{"enable_thinking": false}` |
| GLM | `{"thinking": {"type": "disabled"}}` |
| Gemini 2.x | `{"thinking_config": {"thinking_budget": 0}}` |
| Gemini 3.x | `{"thinking_config": {"thinking_level": "low"}}` |

This is handled automatically in `config/__init__.py` based on the model name.

## Image rate limiting

| Setting | Default | Description |
|---------|---------|-------------|
| `NATIVE_IMAGE_MIN_INTERVAL` | 1.5s | Minimum interval between screen captures |
| `IMAGE_IDLE_RATE_MULTIPLIER` | 5x | Multiplier when no voice activity |
