# API 提供商

N.E.K.O. 支持两类 API 提供商：**Core**（用于实时语音/多模态）和 **Assist**（用于摘要、情感分析、视觉等文本任务）。

## Core API 提供商

Core 提供商必须支持 **Realtime API**（基于 WebSocket 的流式传输）。

| 提供商 | WebSocket URL | 默认模型 |
|--------|---------------|----------|
| `free` | 内置服务器 | `free-model` |
| `qwen` | `wss://dashscope.aliyuncs.com/api-ws/v1/realtime` | `qwen3-omni-flash-realtime` |
| `openai` | `wss://api.openai.com/v1/realtime` | `gpt-realtime-mini` |
| `step` | `wss://api.stepfun.com/v1/realtime` | `step-audio-2` |
| `gemini` | Google GenAI SDK | `gemini-2.5-flash-native-audio-preview-12-2025` |

::: tip
**free** 层使用社区服务器，无需 API 密钥。适合测试使用，但容量有限。
:::

## Assist API 提供商

Assist 提供商使用兼容 OpenAI 的 HTTP API 来处理文本任务。

| 提供商 | Base URL |
|--------|----------|
| `qwen` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `openai` | `https://api.openai.com/v1` |
| `glm` | `https://open.bigmodel.cn/api/paas/v4` |
| `step` | `https://api.stepfun.com/v1` |
| `silicon` | `https://api.siliconflow.cn/v1` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `kimi` | `https://api.moonshot.cn/v1` |

每个 Assist 提供商为以下任务定义模型：

| 任务 | 配置字段 | 用途 |
|------|----------|------|
| 对话 | `conversation_model` | 角色聊天（离线模式） |
| 摘要 | `summary_model` | 对话摘要 |
| 纠错 | `correction_model` | 文本纠错 |
| 情感 | `emotion_model` | 情感分析 |
| 视觉 | `vision_model` | 图像理解 |
| Agent | `agent_model` | Agent 任务执行 |

## 提供商配置

提供商定义在 `config/api_providers.json` 中。你可以通过以下方式选择提供商：

1. **Web UI** `http://localhost:48911/api_key`
2. **环境变量** `NEKO_CORE_API` 和 `NEKO_ASSIST_API`
3. **配置文件** `core_config.json` 中的 `coreApi` 和 `assistApi` 字段

## API 密钥映射

每个 Assist 提供商对应一个特定的环境变量：

| 提供商 | 环境变量 |
|--------|----------|
| `qwen` | `NEKO_ASSIST_API_KEY_QWEN` |
| `openai` | `NEKO_ASSIST_API_KEY_OPENAI` |
| `glm` | `NEKO_ASSIST_API_KEY_GLM` |
| `step` | `NEKO_ASSIST_API_KEY_STEP` |
| `silicon` | `NEKO_ASSIST_API_KEY_SILICON` |
| `gemini` | `NEKO_ASSIST_API_KEY_GEMINI` |
| `kimi` | `NEKO_ASSIST_API_KEY_KIMI` |
