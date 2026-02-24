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

## 模型覆盖

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NEKO_SUMMARY_MODEL` | `qwen-plus` | 摘要模型 |
| `NEKO_CORRECTION_MODEL` | `qwen-max` | 文本纠错模型 |
| `NEKO_EMOTION_MODEL` | `qwen-turbo` / `qwen-flash` | 情感分析模型 |
| `NEKO_VISION_MODEL` | `qwen3-vl-plus-2025-09-23` | 视觉/图像理解模型 |
| `NEKO_ROUTER_MODEL` | `qwen-plus` | 记忆路由模型 |
| `NEKO_SEMANTIC_MODEL` | `text-embedding-v4` | 向量嵌入模型 |
| `NEKO_RERANKER_MODEL` | `qwen-plus` | 搜索重排模型 |
| `NEKO_SETTING_PROPOSER_MODEL` | `qwen-max` | 设定更新提议模型 |
| `NEKO_SETTING_VERIFIER_MODEL` | `qwen-max` | 设定更新验证模型 |

## 服务 URL

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NEKO_MCP_ROUTER_URL` | `http://localhost:3282` | MCP Router 端点 |
