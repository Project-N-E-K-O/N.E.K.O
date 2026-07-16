# 环境变量

所有环境变量均使用 `NEKO_` 前缀。

## API 密钥

| 变量 | 是否必需 | 说明 |
|------|----------|------|
| `NEKO_CORE_API_KEY` | 是（除非使用 free） | Core Realtime API 密钥 |
| `NEKO_ASSIST_API_KEY_QWEN` | 否 | 阿里云（通义千问）辅助 API 密钥 |
| `NEKO_ASSIST_API_KEY_OPENAI` | 否 | OpenAI 辅助 API 密钥 |
| `NEKO_ASSIST_API_KEY_GLM` | 否 | 智谱（GLM）辅助 API 密钥 |
| `NEKO_ASSIST_API_KEY_STEP` | 否 | 阶跃星辰辅助 API 密钥 |
| `NEKO_ASSIST_API_KEY_SILICON` | 否 | SiliconFlow 辅助 API 密钥 |
| `NEKO_ASSIST_API_KEY_GEMINI` | 否 | Google Gemini 辅助 API 密钥 |
| `NEKO_MCP_TOKEN` | 否 | MCP Router 认证令牌 |
| `NEKO_OPENROUTER_API_KEY` | 否 | OpenRouter API 密钥 |

## 提供商选择

| 变量 | 默认值 | 可选项 |
|------|--------|--------|
| `NEKO_CORE_API` | `qwen` | `free`、`qwen`、`openai`、`glm`、`step`、`gemini` |
| `NEKO_ASSIST_API` | `qwen` | `qwen`、`openai`、`glm`、`step`、`silicon`、`gemini` |

## 服务端口

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NEKO_MAIN_SERVER_PORT` | `48911` | 主服务器（Web UI、API） |
| `NEKO_MEMORY_SERVER_PORT` | `48912` | 记忆服务器 |
| `NEKO_MONITOR_SERVER_PORT` | `48913` | 监控服务器 |
| `NEKO_COMMENTER_SERVER_PORT` | `48914` | 弹幕服务器 |
| `NEKO_TOOL_SERVER_PORT` | `48915` | Agent/工具服务器 |
| `NEKO_USER_PLUGIN_SERVER_PORT` | `48916` | 用户插件服务器 |
| `NEKO_AGENT_MQ_PORT` | `48917` | Agent 消息队列 |
| `NEKO_MAIN_AGENT_EVENT_PORT` | `48918` | Agent 事件端口 |

## 运行拓扑

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NEKO_MERGED` | 源码环境：`0`；冻结发行包：`1` | `1` 让 main、memory、agent 三个 HTTP 服务在同一进程中运行，但保留原有契约；`0` 保留三个服务进程。不会复用不完整的已有后端：冲突的公开端口与内部端口会回退，确保隔离启动一套完整的新拓扑。 |

源码开发、独立服务监管或需要 agent 故障隔离时应使用多进程模式。发行包可通过
`NEKO_MERGED=0` 立即回退。

## 服务 URL

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NEKO_MCP_ROUTER_URL` | `http://localhost:3283` | MCP Router 端点 |
