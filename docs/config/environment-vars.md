# Environment Variables

All environment variables use the `NEKO_` prefix.

## API keys

| Variable | Required | Description |
|----------|----------|-------------|
| `NEKO_CORE_API_KEY` | Yes (unless using free) | Core Realtime API key |
| `NEKO_ASSIST_API_KEY_QWEN` | No | Alibaba Cloud (Qwen) assist API key |
| `NEKO_ASSIST_API_KEY_OPENAI` | No | OpenAI assist API key |
| `NEKO_ASSIST_API_KEY_GLM` | No | Zhipu (GLM) assist API key |
| `NEKO_ASSIST_API_KEY_STEP` | No | StepFun assist API key |
| `NEKO_ASSIST_API_KEY_SILICON` | No | SiliconFlow assist API key |
| `NEKO_ASSIST_API_KEY_GEMINI` | No | Google Gemini assist API key |
| `NEKO_MCP_TOKEN` | No | MCP Router authentication token |
| `NEKO_OPENROUTER_API_KEY` | No | OpenRouter API key |

## Provider selection

| Variable | Default | Options |
|----------|---------|---------|
| `NEKO_CORE_API` | `qwen` | `free`, `qwen`, `openai`, `glm`, `step`, `gemini` |
| `NEKO_ASSIST_API` | `qwen` | `qwen`, `openai`, `glm`, `step`, `silicon`, `gemini` |

## Server ports

| Variable | Default | Description |
|----------|---------|-------------|
| `NEKO_MAIN_SERVER_PORT` | `48911` | Main server (Web UI, API) |
| `NEKO_MEMORY_SERVER_PORT` | `48912` | Memory server |
| `NEKO_MONITOR_SERVER_PORT` | `48913` | Monitor server |
| `NEKO_COMMENTER_SERVER_PORT` | `48914` | Commenter server |
| `NEKO_TOOL_SERVER_PORT` | `48915` | Agent/tool server |
| `NEKO_USER_PLUGIN_SERVER_PORT` | `48916` | User plugin server |
| `NEKO_AGENT_MQ_PORT` | `48917` | Agent message queue |
| `NEKO_MAIN_AGENT_EVENT_PORT` | `48918` | Agent event port |

## Model overrides

| Variable | Default | Description |
|----------|---------|-------------|
| `NEKO_SUMMARY_MODEL` | `qwen-plus` | Summarization model |
| `NEKO_CORRECTION_MODEL` | `qwen-max` | Text correction model |
| `NEKO_EMOTION_MODEL` | `qwen-turbo` / `qwen-flash` | Emotion analysis model |
| `NEKO_VISION_MODEL` | `qwen3-vl-plus-2025-09-23` | Vision/image understanding model |
| `NEKO_ROUTER_MODEL` | `qwen-plus` | Memory routing model |
| `NEKO_SEMANTIC_MODEL` | `text-embedding-v4` | Embedding model |
| `NEKO_RERANKER_MODEL` | `qwen-plus` | Search reranking model |
| `NEKO_SETTING_PROPOSER_MODEL` | `qwen-max` | Setting update proposer |
| `NEKO_SETTING_VERIFIER_MODEL` | `qwen-max` | Setting update verifier |

## Service URLs

| Variable | Default | Description |
|----------|---------|-------------|
| `NEKO_MCP_ROUTER_URL` | `http://localhost:3282` | MCP Router endpoint |
