# 系统 API

**前缀：** `/api`

用于情感分析、文件工具、截图和主动聊天的杂项系统端点。

## 情感分析

### `POST /api/emotion/analysis`

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

### `GET /api/meme/proxy-image`

代理远程图片（例如表情包）以绕过 CORS 限制，带 SSRF 防护与缓存。

**查询参数：** `url` — 要代理的远程图片 URL（必须为 http/https）。

### `GET /api/steam/proxy-image`

代理访问本地图片文件（尤其是 Steam Workshop 目录），支持绝对路径与相对路径。

**查询参数：** `image_path` — 图片的本地文件路径。

## Steam 成就

### `POST /api/steam/set-achievement-status/{name}`

解锁 Steam 成就。成就名称通过路径参数 `{name}` 传入。

**路径参数：** `name` — Steam 成就名称（例如 `ACH_FIRST_DIALOGUE`）。

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

**响应：** 主动搭话成功投递时 `action` 为 `chat`，本轮跳过时为 `pass`。
主动搭话的 `pass` / `chat` / error 响应会包含稳定的机器字段 `reason_code`，
例如 `CHAT_DELIVERED`、`PASS_BUSY`、`PASS_SOURCE_EMPTY`、`PASS_DUPLICATE`、
`DELIVERY_PREEMPTED` 或 `ERROR_TIMEOUT`。响应也会包含 `stage`，表示粗粒度流程阶段，
例如 `entry_guard`、`activity_gate`、`source_selection`、`model_decision`、
`generation`、`dedup`、`delivery` 或 `runtime_error`。

::: info
主动消息有频率限制：每个角色每小时最多 10 条。
:::

::: info
在内部，主动聊天运行两阶段流程：阶段 1 的 LLM 调用会先审查候选网页内容（并提取音乐/表情包关键词），然后才在阶段 2 生成符合人设的回复。不存在单独可调用的网页审查端点。
:::

## 截图

### `POST /api/screenshot`

后端截图兜底：当所有前端屏幕捕获 API 都失败时，由后端使用 pyautogui 捕获本地屏幕。仅限本地回环（loopback）；当后端配置为远程时禁用。

**响应：** `{ "success": true, "data": "data:image/jpeg;base64,...", "size": <字节数> }`

### `POST /api/screenshot/interactive`

系统原生的交互式（框选区域）截图，聊天截图按钮优先使用。macOS 使用 `screencapture` 框选；其他平台委托给前端。仅限本地回环（loopback）。

**响应：** 一个 JSON 信封（而非原始 DataURL）。

```json
{ "success": true, "data": "data:image/jpeg;base64,...", "size": <字节数> }
```

用户取消框选时：`{ "success": false, "canceled": true }`。当后端非 localhost / 配置为远程时：`{ "success": false, "error": "..." }`。
