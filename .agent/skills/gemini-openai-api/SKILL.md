---
name: gemini-openai-api
description: "Gemini 模型通过 OpenAI 兼容 API 接入指南。包含：(1) 辅助 API 配置（summary/correction/emotion/vision），(2) extra_body 格式用于控制 thinking，(3) 响应格式处理（markdown 代码块）。当需要将 Gemini 作为辅助模型接入、配置 thinking 参数、或处理 Gemini API 返回格式时使用。"
---

# Gemini OpenAI 兼容 API 接入

Gemini 提供 OpenAI 兼容端点，可作为辅助 API 使用。

## Base URL

```
https://generativelanguage.googleapis.com/v1beta/openai/
```

## 模型配置

| 用途                          | 推荐模型                   |
|-------------------------------|---------------------------|
| Summary / Correction / Vision | `gemini-3-flash-preview`  |
| Emotion Analysis              | `gemini-2.5-flash`        |

## 控制 Thinking

Gemini 2.5+ 模型需要通过 `extra_body` 控制 thinking 行为。

### 禁用 Thinking（用于 gemini-2.5-flash）

```python
extra_body = {
    "extra_body": {
        "google": {
            "thinking_config": {
                "thinking_budget": 0
            }
        }
    }
}
```

### 低级别 Thinking（用于 gemini-3-flash-preview）

```python
extra_body = {
    "extra_body": {
        "google": {
            "thinking_config": {
                "thinking_level": "low",
                "include_thoughts": False
            }
        }
    }
}
```

> [!IMPORTANT]
> extra_body 需要双层嵌套：外层 `"extra_body"` 是传给 OpenAI client 的参数名，内层 `{"google": {...}}` 是 Gemini 的实际配置。

## 响应格式处理

Gemini 可能返回 markdown 代码块包装的 JSON：

```
```json
{"emotion": "happy", "confidence": 0.8}
```
```

处理方法：

```python
if result_text.startswith("```"):
    lines = result_text.split("\n")
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    result_text = "\n".join(lines).strip()
```

## 配置文件位置

- `config/api_providers.json` - 添加 gemini 到 `assist_api_providers`
- `config/__init__.py` - 添加 `EXTRA_BODY_GEMINI` 和 `MODELS_EXTRA_BODY_MAP`

### api_providers.json 示例

```json
"gemini": {
  "key": "gemini",
  "name": "Gemini（Google）",
  "description": "Google AI 辅助模型，国内无法使用",
  "openrouter_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
  "summary_model": "gemini-3-flash-preview",
  "correction_model": "gemini-3-flash-preview",
  "emotion_model": "gemini-2.5-flash",
  "vision_model": "gemini-3-flash-preview"
}
```

## 常见问题

### "Unknown name 'google': Cannot find field"

原因：extra_body 格式错误，缺少外层 `"extra_body"` 包装。

解决：使用双层嵌套格式 `{"extra_body": {"google": {...}}}`。

### JSON 解析失败

原因：
1. 响应被截断（token 限制太小）
2. 响应包含 markdown 代码块

解决：
1. 增加 `max_completion_tokens`
2. 添加 markdown 代码块处理逻辑
