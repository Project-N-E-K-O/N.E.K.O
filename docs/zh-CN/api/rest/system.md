# 系统 API

**前缀：** `/api`

用于情感分析、文件工具、截图和主动聊天的杂项系统端点。

## 情感分析

### `POST /api/analyze_emotion`

分析文本的情感倾向。

**请求体：**

```json
{
  "text": "I'm so happy to see you!",
  "lanlan_name": "character_name"
}
```

**响应：** 用于 Live2D/VRM 表情映射的情感标签。

## 文件工具

### `GET /api/file-exists`

检查指定路径的文件是否存在。

**查询参数：** `path` — 要检查的文件路径。

### `GET /api/find-first-image`

在目录中查找第一个图片文件。

**查询参数：** `directory` — 要搜索的目录路径。

### `GET /api/proxy-image`

代理图片请求以绕过 CORS 限制。

**查询参数：** `url` — 要代理的图片 URL。

## Steam 成就

### `POST /api/steam_achievement`

解锁 Steam 成就。

**请求体：**

```json
{ "achievement_id": "ACHIEVEMENT_NAME" }
```

## 主动聊天

### `POST /api/proactive_chat`

生成角色的主动消息（用于空闲对话）。

**请求体：**

```json
{
  "lanlan_name": "character_name",
  "context": "optional context about what's happening"
}
```

::: info
主动消息有频率限制：每个角色每小时最多 10 条。
:::

## 网页审查

### `POST /api/web_screening`

通过 AI 审查网页内容（用于内容过滤和相关性排序）。

**请求体：** 包含审查模式的网页内容数据。

## 截图分析

### `POST /api/screenshot_analysis`

使用视觉模型分析截图。

**请求体：** Base64 编码的图片数据，可附带上下文信息。
