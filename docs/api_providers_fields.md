# API 提供商字段对照表

本文档汇总了 N.E.K.O-Himifox 支持的所有 API 提供商的配置字段、缓存机制和遥测字段。

## 支持的 API 提供商

| 提供商 | 标识符 | 类型 | 支持状态 |
|--------|--------|------|----------|
| 阿里云 (DashScope) | `qwen` | 国产 | ✅ 完全支持 |
| OpenAI | `openai` | 国际 | ✅ 完全支持 |
| 智谱 (GLM) | `glm` | 国产 | ✅ 完全支持 |
| 阶跃星辰 (Step) | `step` | 国产 | ✅ 完全支持 |
| 硅基流动 (Silicon) | `silicon` | 聚合 | ✅ 完全支持 |
| Google Gemini | `gemini` | 国际 | ✅ 完全支持 |
| Moonshot (Kimi) | `kimi` | 国产 | ✅ 完全支持 |

---

## 1. 阿里云 (DashScope / Qwen)

### 基础配置
```python
{
    'OPENROUTER_URL': "https://dashscope.aliyuncs.com/compatible-mode/v1",
    'CONVERSATION_MODEL': "qwen3-235b-a22b-instruct-2507",
    'SUMMARY_MODEL': "qwen3-next-80b-a3b-instruct",
    'CORRECTION_MODEL': "qwen3-235b-a22b-instruct-2507",
    'EMOTION_MODEL': "qwen-flash",
    'VISION_MODEL': "qwen3-vl-plus-2025-09-23",
    'AGENT_MODEL': "qwen3.5-plus",
}
```

### Context Cache 配置

| 字段 | 值 | 说明 |
|------|-----|------|
| `default_headers` | `{"x-dashscope-session-cache": "enable"}` | 开启会话级缓存 |
| `enable_cache_control` | `True` | 启用缓存控制 |

### 遥测字段 (Token Usage)

| 字段路径 | 类型 | 说明 |
|----------|------|------|
| `usage.prompt_tokens` | int | 总提示词 Token 数 |
| `usage.completion_tokens` | int | 生成 Token 数 |
| `usage.total_tokens` | int | 总计 Token 数 |
| `usage.prompt_tokens_details.cached_tokens` | int | 缓存命中 Token 数 |

### 特殊配置
- **Extra Body**: `{"enable_thinking": False}`
- **WebSocket URL**: `wss://dashscope.aliyuncs.com/ws/v1/realtime`
- **缓存命中率**: 可达 99.2%

---

## 2. OpenAI

### 基础配置
```python
{
    'OPENROUTER_URL': "https://api.openai.com/v1",
    'CONVERSATION_MODEL': "gpt-5-chat-latest",
    'SUMMARY_MODEL': "gpt-4.1-mini",
    'CORRECTION_MODEL': "gpt-5-chat-latest",
    'EMOTION_MODEL': "gpt-4.1-nano",
    'VISION_MODEL': "gpt-5-chat-latest",
    'AGENT_MODEL': "gpt-5-chat-latest",
}
```

### Context Cache 配置

| 字段 | 值 | 说明 |
|------|-----|------|
| `default_headers` | `{}` | 无需特殊 Header |
| `enable_cache_control` | `False` | 通过 API 自动管理 |

### 遥测字段 (Token Usage)

| 字段路径 | 类型 | 说明 |
|----------|------|------|
| `usage.prompt_tokens` | int | 总提示词 Token 数 |
| `usage.completion_tokens` | int | 生成 Token 数 |
| `usage.total_tokens` | int | 总计 Token 数 |
| `usage.prompt_tokens_details.cached_tokens` | int | 缓存命中 Token 数 (官方格式) |

### 特殊配置
- **Extra Body**: `{"enable_thinking": False}`
- **WebSocket URL**: `wss://api.openai.com/v1/realtime`

---

## 3. 智谱 (GLM)

### 基础配置
```python
{
    'OPENROUTER_URL': "https://open.bigmodel.cn/api/paas/v4",
    'CONVERSATION_MODEL': "glm-4.5-air",
    'SUMMARY_MODEL': "glm-4.5-flash",
    'CORRECTION_MODEL': "glm-4.5-air",
    'EMOTION_MODEL': "glm-4.5-flash",
    'VISION_MODEL': "glm-4.6v-flash",
    'AGENT_MODEL': "glm-4.5-air",
}
```

### Context Cache 配置

| 字段 | 值 | 说明 |
|------|-----|------|
| `default_headers` | `{}` | 无需特殊 Header |
| `enable_cache_control` | `False` | 通过 API 自动管理 |

### 遥测字段 (Token Usage)

| 字段路径 | 类型 | 说明 |
|----------|------|------|
| `usage.prompt_tokens` | int | 总提示词 Token 数 |
| `usage.completion_tokens` | int | 生成 Token 数 |
| `usage.total_tokens` | int | 总计 Token 数 |
| `usage.cached_tokens` | int | 缓存命中 Token 数 (可能) |

### 特殊配置
- **Extra Body**: `{"thinking": {"type": "disabled"}}`
- **WebSocket URL**: `wss://open.bigmodel.cn/api/paas/v4/realtime`

---

## 4. 阶跃星辰 (Step)

### 基础配置
```python
{
    'OPENROUTER_URL': "https://api.stepfun.com/v1",
    'CONVERSATION_MODEL': "step-2-mini",
    'SUMMARY_MODEL': "step-2-mini",
    'CORRECTION_MODEL': "step-2-mini",
    'EMOTION_MODEL': "step-2-mini",
    'VISION_MODEL': "step-1o-turbo-vision",
    'AGENT_MODEL': "step-2-mini",
}
```

### Context Cache 配置

| 字段 | 值 | 说明 |
|------|-----|------|
| `default_headers` | `{}` | 无需特殊 Header |
| `enable_cache_control` | `False` | 通过 API 自动管理 |

### 遥测字段 (Token Usage)

| 字段路径 | 类型 | 说明 |
|----------|------|------|
| `usage.prompt_tokens` | int | 总提示词 Token 数 |
| `usage.completion_tokens` | int | 生成 Token 数 |
| `usage.total_tokens` | int | 总计 Token 数 |
| `usage.cached_tokens` | int | 缓存命中 Token 数 (阶跃特有，顶层字段) |

### 特殊配置
- **Extra Body**: `{"tools":[{"type": "web_search", ...}]}`
- **WebSocket URL**: `wss://api.stepfun.com/v1/realtime`

---

## 5. 硅基流动 (Silicon)

### 基础配置
```python
{
    'OPENROUTER_URL': "https://api.siliconflow.cn/v1",
    'CONVERSATION_MODEL': "deepseek-ai/DeepSeek-V3.2",
    'SUMMARY_MODEL': "Qwen/Qwen3-Next-80B-A3B-Instruct",
    'CORRECTION_MODEL': "deepseek-ai/DeepSeek-V3.2",
    'EMOTION_MODEL': "inclusionAI/Ling-mini-2.0",
    'VISION_MODEL': "zai-org/GLM-4.6V",
    'AGENT_MODEL': "deepseek-ai/DeepSeek-V3.2",
}
```

### Context Cache 配置

| 字段 | 值 | 说明 |
|------|-----|------|
| `default_headers` | `{}` | 转发层，依赖上游 |
| `enable_cache_control` | `False` | 由上游模型决定 |

### 遥测字段 (Token Usage)

| 字段路径 | 类型 | 说明 |
|----------|------|------|
| `usage.prompt_tokens` | int | 总提示词 Token 数 |
| `usage.completion_tokens` | int | 生成 Token 数 |
| `usage.total_tokens` | int | 总计 Token 数 |
| `usage.prompt_cache_hit_tokens` | int | 缓存命中 Token 数 (可能) |

### 特殊配置
- **Extra Body**: `{"enable_thinking": False}` (取决于具体模型)
- **注意**: 硅基流动是模型聚合平台，缓存行为取决于所选模型的上游提供商

---

## 6. Google Gemini

### 基础配置
```python
{
    'OPENROUTER_URL': "https://generativelanguage.googleapis.com/v1beta/openai/",
    'CONVERSATION_MODEL': "gemini-3-flash-preview",
    'SUMMARY_MODEL': "gemini-3-flash-preview",
    'CORRECTION_MODEL': "gemini-3-flash-preview",
    'EMOTION_MODEL': "gemini-2.5-flash",
    'VISION_MODEL': "gemini-3-flash-preview",
    'AGENT_MODEL': "gemini-3-flash-preview",
}
```

### Context Cache 配置

| 字段 | 值 | 说明 |
|------|-----|------|
| `default_headers` | `{}` | 通过 SDK 管理 |
| `enable_cache_control` | `False` | Google 自有缓存机制 |

### 遥测字段 (Token Usage)

| 字段路径 | 类型 | 说明 |
|----------|------|------|
| `usage.prompt_tokens` | int | 总提示词 Token 数 |
| `usage.completion_tokens` | int | 生成 Token 数 |
| `usage.total_tokens` | int | 总计 Token 数 |
| `usage.cached_content_token_count` | int | 缓存内容 Token 数 (Gemini 旧版) |
| `usage.prompt_tokens_details.cached_tokens` | int | 缓存命中 Token 数 (新版) |

### 特殊配置
- **Extra Body**: `{"extra_body": {"google": {"thinking_config": {...}}}}`
- **注意**: Gemini 使用 google-genai SDK，非原生 OpenAI 格式

---

## 7. Moonshot (Kimi)

### 基础配置
```python
{
    'OPENROUTER_URL': "https://api.moonshot.cn/v1",
    'CONVERSATION_MODEL': "kimi-latest",
    'SUMMARY_MODEL': "moonshot-v1-8k",
    'CORRECTION_MODEL': "kimi-latest",
    'EMOTION_MODEL': "moonshot-v1-8k",
    'VISION_MODEL': "kimi-latest",
    'AGENT_MODEL': "kimi-latest",
}
```

### Context Cache 配置

| 字段 | 值 | 说明 |
|------|-----|------|
| `default_headers` | `{}` | 无需特殊 Header |
| `enable_cache_control` | `False` | 通过 API 自动管理 |

### 遥测字段 (Token Usage)

| 字段路径 | 类型 | 说明 |
|----------|------|------|
| `usage.prompt_tokens` | int | 总提示词 Token 数 |
| `usage.completion_tokens` | int | 生成 Token 数 |
| `usage.total_tokens` | int | 总计 Token 数 |
| `usage.prompt_cache_hit_tokens` | int | 缓存命中 Token 数 (可能) |

### 特殊配置
- **Extra Body**: 无特殊配置

---

## 遥测字段统一映射表

### Token Tracker 支持的缓存字段 ( `_CACHED_TOKEN_FIELDS` )

| 字段名 | 提供商 | 位置 |
|--------|--------|------|
| `cached_tokens` | Step (阶跃星辰) | 顶层 |
| `cache_read_input_tokens` | Anthropic Claude | 顶层 |
| `prompt_cache_hit_tokens` | 部分国产 Provider | 顶层 |
| `cached_content_token_count` | Google PaLM/旧版 Gemini | 顶层 |
| `cache_tokens` | 其他变体 | 顶层 |

### 嵌套字段检查 ( `_NESTED_DETAIL_FIELDS` )

| 字段名 | 提供商 | 用途 |
|--------|--------|------|
| `prompt_tokens_details` | OpenAI 官方 | 包含 cached_tokens |
| `details` | 通用 | 可能包含缓存信息 |
| `token_details` | 通用 | 可能包含缓存信息 |
| `prompt_details` | 通用 | 可能包含缓存信息 |

---

## Context Cache 支持总结

| 提供商 | Header 控制 | 自动缓存 | 备注 |
|--------|-------------|----------|------|
| DashScope (阿里云) | ✅ `x-dashscope-session-cache` | ✅ | 99.2% 命中率 |
| OpenAI | ❌ | ✅ | 官方自动管理 |
| GLM (智谱) | ❌ | ✅ | 自动管理 |
| Step (阶跃) | ❌ | ✅ | 自动管理 |
| Silicon (硅基) | ❌ | 依赖上游 | 转发层 |
| Gemini (Google) | ❌ | ✅ | Google 自有机制 |
| Kimi (Moonshot) | ❌ | ✅ | 自动管理 |

---

## 相关代码文件

- `utils/token_tracker.py` - Token 用量追踪
- `utils/llm_client.py` - LLM 客户端 (含 `get_dashscope_cache_config`)
- `config/__init__.py` - API 配置定义
- `main_logic/omni_offline_client.py` - 离线客户端缓存逻辑
- `memory/recent.py` - 记忆系统缓存配置
- `brain/deduper.py` - 任务去重缓存配置
