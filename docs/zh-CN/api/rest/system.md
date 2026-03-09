# 系统 API

**前缀：** `/api`

用于情感分析、文件工具、Steam 集成、主动聊天和翻译的杂项系统端点。

## 情感分析

### `POST /api/emotion/analysis`

分析文本的情感倾向。

**请求体：**

```json
{
  "text": "I'm so happy to see you!",
  "lanlan_name": "character_name",
  "api_key": "optional",
  "model": "optional"
}
```

**响应：**

```json
{
  "emotion": "happy",
  "confidence": 0.95
}
```

## 文件工具

### `GET /api/file-exists`

检查指定路径的文件是否存在。

**查询参数：** `path` — 要检查的文件路径。

### `GET /api/find-first-image`

在目录中查找第一张预览图片文件。

**查询参数：** `folder` — 要搜索的目录路径。

### `GET /api/steam/proxy-image`

代理图片请求用于本地文件访问。

**查询参数：** `image_path` — 本地图片文件路径。

## Steam 集成

### `POST /api/steam/set-achievement-status/{name}`

设置 Steam 成就的解锁状态。

**路径参数：** `name` — 成就标识符。

### `GET /api/steam/list-achievements`

列出所有 Steam 成就及其状态。

### `POST /api/steam/update-playtime`

更新追踪的游戏时间。

**请求体：**

```json
{ "seconds": 3600 }
```

## 主动聊天

### `POST /api/proactive_chat`

生成角色的主动消息（两阶段架构）。

**请求体：**

```json
{
  "lanlan_name": "character_name",
  "enabled_modes": ["vision", "news", "video", "home", "window", "personal"],
  "screenshot_data": "optional base64",
  "language": "zh"
}
```

## 翻译

### `POST /api/translate`

翻译文本。

**请求体：**

```json
{
  "text": "Hello",
  "target_lang": "zh",
  "source_lang": "en",
  "skip_google": false
}
```

## 其他端点

### `POST /api/personal_dynamics`

获取个性化内容数据。

**请求体：**

```json
{ "limit": 10 }
```

### `GET /api/get_window_title`

获取当前活动窗口的标题（仅 Windows）。
