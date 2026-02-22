# API Providers

N.E.K.O. supports two categories of API providers: **Core** (for real-time voice/multimodal) and **Assist** (for text-based tasks like summarization, emotion analysis, and vision).

## Core API providers

Core providers must support **Realtime API** (WebSocket-based streaming).

| Provider | WebSocket URL | Default model |
|----------|---------------|---------------|
| `free` | Built-in server | `free-model` |
| `qwen` | `wss://dashscope.aliyuncs.com/api-ws/v1/realtime` | `qwen3-omni-flash-realtime` |
| `openai` | `wss://api.openai.com/v1/realtime` | `gpt-realtime-mini` |
| `step` | `wss://api.stepfun.com/v1/realtime` | `step-audio-2` |
| `gemini` | Google GenAI SDK | `gemini-2.5-flash-native-audio-preview-12-2025` |

::: tip
The **free** tier uses a community server and requires no API key. It's suitable for testing but has limited capacity.
:::

## Assist API providers

Assist providers use OpenAI-compatible HTTP APIs for text-based tasks.

| Provider | Base URL |
|----------|----------|
| `qwen` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `openai` | `https://api.openai.com/v1` |
| `glm` | `https://open.bigmodel.cn/api/paas/v4` |
| `step` | `https://api.stepfun.com/v1` |
| `silicon` | `https://api.siliconflow.cn/v1` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `kimi` | `https://api.moonshot.cn/v1` |

Each assist provider defines models for these tasks:

| Task | Field in config | Purpose |
|------|----------------|---------|
| Conversation | `conversation_model` | Character chat (offline mode) |
| Summary | `summary_model` | Conversation summarization |
| Correction | `correction_model` | Text correction |
| Emotion | `emotion_model` | Emotion analysis |
| Vision | `vision_model` | Image understanding |
| Agent | `agent_model` | Agent task execution |

## Provider configuration

Providers are defined in `config/api_providers.json`. You can select providers through:

1. **Web UI** at `http://localhost:48911/api_key`
2. **Environment variables** `NEKO_CORE_API` and `NEKO_ASSIST_API`
3. **Config file** `core_config.json` fields `coreApi` and `assistApi`

## API key mapping

Each assist provider maps to a specific environment variable:

| Provider | Environment variable |
|----------|---------------------|
| `qwen` | `NEKO_ASSIST_API_KEY_QWEN` |
| `openai` | `NEKO_ASSIST_API_KEY_OPENAI` |
| `glm` | `NEKO_ASSIST_API_KEY_GLM` |
| `step` | `NEKO_ASSIST_API_KEY_STEP` |
| `silicon` | `NEKO_ASSIST_API_KEY_SILICON` |
| `gemini` | `NEKO_ASSIST_API_KEY_GEMINI` |
| `kimi` | `NEKO_ASSIST_API_KEY_KIMI` |
