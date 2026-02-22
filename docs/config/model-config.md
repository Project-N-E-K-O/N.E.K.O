# Model Configuration

N.E.K.O. uses different AI models for different tasks. Each can be individually configured.

## Model roles

| Role | Default | Env var | Purpose |
|------|---------|---------|---------|
| Conversation | `qwen-max` | - | Character chat (offline mode) |
| Summary | `qwen-plus` | `NEKO_SUMMARY_MODEL` | Conversation summarization |
| Correction | `qwen-max` | `NEKO_CORRECTION_MODEL` | Text correction |
| Emotion | `qwen-flash` | `NEKO_EMOTION_MODEL` | Emotion analysis for expressions |
| Vision | `qwen3-vl-plus-2025-09-23` | `NEKO_VISION_MODEL` | Image/screenshot understanding |
| Agent | `qwen3.5-plus` | `NEKO_AGENT_MODEL` | Agent task execution |
| Router | `qwen-plus` | `NEKO_ROUTER_MODEL` | Memory routing decisions |
| Semantic | `text-embedding-v4` | `NEKO_SEMANTIC_MODEL` | Text embeddings for memory |
| Reranker | `qwen-plus` | `NEKO_RERANKER_MODEL` | Search result reranking |
| Setting proposer | `qwen-max` | `NEKO_SETTING_PROPOSER_MODEL` | Proposing setting updates |
| Setting verifier | `qwen-max` | `NEKO_SETTING_VERIFIER_MODEL` | Verifying setting updates |

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
