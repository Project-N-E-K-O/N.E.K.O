# 系统 API

**前缀：** `/api`

该 router 汇总不属于更具体资源 router 的第一方应用服务：就绪状态、通知、活动信号、提示流程、截图、Steam、问卷、翻译、主动消息和页面交接。

::: warning 本地应用 API
部分端点会读取本地文件、截取桌面、修改 Steam 状态或引导状态。写接口使用项目的本地请求/CSRF 校验，截图还要求回环客户端。不要把该 router 直接暴露给不可信网络。
:::

## 就绪、用量与通知

| 方法和路径 | 用途 |
|---|---|
| `GET /api/system/status` | 返回 `no-store` 启动快照；`status` 为 `starting`、`migration_required` 或 `ready`，并含存储迁移标志。 |
| `GET /api/token-usage` | 返回查询参数 `days` 范围内的 token 统计；默认 7，最多 90。 |
| `GET /api/pending-notices` | 查看突出通知队列，返回 `{ notices, cursor }`，但不删除。 |
| `POST /api/pending-notices/ack` | 仅清除请求体 `cursor` 及之前的通知，避免丢失读取之后入队的新通知。 |
| `POST /api/activity_signal` | 接收前端发送的有界 OS/活动心跳并交给活动跟踪器。 |

启动信息暂不可用时，状态探针会故意返回 HTTP 200 和 `ready: false`；它是启动哨兵，不是深度健康检查。

## 更新日志与问卷

| 方法和路径 | 用途 |
|---|---|
| `GET /api/changelog` | 返回查询参数 `since` 之后的日志；`lang` 经过 locale 白名单校验并支持回退。 |
| `GET /api/survey` | 向符合条件的 Steam 用户返回当前版本本地化问卷，否则为 `has_survey: false`；DNT/上报退出会禁用问卷。 |
| `POST /api/survey/submit` | 提交或跳过当前版本问卷；答案有类型/大小上限，上传为尽力而为，`uploaded` 表示远端结果。 |

提交需要通过本地写请求校验。服务端使用自身应用版本，不信任客户端提供的问卷版本。

## 情感与翻译

### `POST /api/emotion/analysis`

分析指定角色的文本响应并归一化为项目情感标签。可扩展 JSON 请求体包含 `text` 和 `lanlan_name`，响应含情感和置信度。接口会使用已配置模型，必要时退化到有界启发式推断。

### `POST /api/translate`

第一方字幕翻译接口：

```json
{
  "text": "Hello",
  "target_lang": "ja",
  "source_lang": "en",
  "skip_google": false
}
```

`source_lang` 可省略并自动检测。响应含 `success`、`translated_text`、规范化的源/目标语言，按情况含 `google_failed`。翻译失败时在应用层信封中返回原文。

## 本地文件与图片

| 方法和路径 | 用途与边界 |
|---|---|
| `GET /api/file-exists` | 必填查询参数 `path`，返回 `{ exists }`。拒绝显式路径穿越，但有意支持正常的用户/Workshop 绝对路径。 |
| `GET /api/find-first-image` | 必填查询参数 **`folder`**。只在允许的应用、assets、用户数据根目录中搜索固定预览文件名和小于 1 MiB 的图片。 |
| `GET /api/meme/proxy-image` | 必填远端 `url`；通过 SSRF 校验、内容限制和缓存代理 HTTP(S) 图片。 |
| `GET /api/steam/proxy-image` | 必填本地 `image_path`；经过路径边界和类型校验后提供本地/Workshop 图片。 |

缺少输入通常返回 `400`；禁止路径/目标为 `403`；本地文件不存在为 `404`；上游图片错误按代理阶段使用 `4xx`/`5xx`。

## 截图与活动窗口

| 方法和路径 | 用途 |
|---|---|
| `GET /api/get_window_title` | 平台集成可用时返回活动窗口标题，主要用于 Windows。 |
| `POST /api/screenshot` | 仅回环可用的 pyautogui 后备截图，返回 JPEG data URL 和字节数。 |
| `POST /api/screenshot/interactive` | macOS 上使用原生区域选择；其他平台通知前端执行交互截图。仅回环可用。 |

成功格式为 `{ "success": true, "data": "data:image/jpeg;base64,...", "size": 123 }`；取消交互选择为 `success: false, canceled: true`。远程配置或非回环请求会被拒绝，不会截取主机桌面。

## 主动消息与小游戏事件

| 方法和路径 | 用途 |
|---|---|
| `POST /api/proactive_chat` | 为 `lanlan_name` 运行主动内容选择、生成和投递流程。 |
| `POST /api/proactive/music_played_through` | 记录推荐歌曲播放完成，作为来源加权的正反馈。 |
| `POST /api/mini_game/invite/respond` | 将用户响应应用到当前小游戏邀请状态机。 |

主动消息响应使用 `action: chat` 或 `action: pass`，并通过稳定的 `reason_code`/`stage` 表达忙碌、来源为空、重复、投递抢占、超时或成功投递等结果。不存在单独可调用的“第一阶段筛选”路由。

## 七日教程与自启动提示

这些是主页教程和自启动流程使用的内部接口：

| 方法和路径 | 用途 |
|---|---|
| `GET /api/seven-day-tutorial/state` | 读取后端权威的 Day 1–7 进度。 |
| `PUT /api/seven-day-tutorial/state` | `expectedRevision` 匹配时替换 Day 1–7 权威进度。 |
| `GET /api/autostart-prompt/state` | 读取自启动提示状态。 |
| `POST /api/autostart-prompt/heartbeat` | 记录主页状态并判断是否应提示。 |
| `POST /api/autostart-prompt/shown` | 记录展示。 |
| `POST /api/autostart-prompt/decision` | 记录用户的自启动决策。 |

该组所有写接口都要求通过本地写请求校验。七日教程 PUT 请求体包含 `state` 和最近读取的 `expectedRevision`；版本过期时返回 `409` 及当前权威状态。请求体属于第一方 UI 状态，不是稳定的第三方 schema。

## Steam 状态

| 方法和路径 | 用途 |
|---|---|
| `POST /api/steam/set-achievement-status/{name}` | 解锁/设置指定已配置成就。 |
| `POST /api/steam/update-playtime` | 累加有界游戏时长并写入 Steam stats。 |
| `GET /api/steam/list-achievements` | 列出成就状态，主要用于诊断。 |

Steamworks 不可用时返回失败信封；无效本地写请求会在修改 Steam 状态前被拒绝。

## Yui 引导交接

| 方法和路径 | 用途 |
|---|---|
| `POST /api/yui-guide/handoff/create` | 创建短期、签名、内存中的一次性交接 token。必填 `target_page`，可附源/目标路径和恢复元数据。 |
| `POST /api/yui-guide/handoff/consume` | 用必填 `token`、`signature`、`expected_page` 消费 token；`consumer_id` 可选。 |

响应为 `no-store`。无效输入为 `400`，签名/来源/页面不匹配为 `403`，不存在或过期为 `404`，重放/冲突为 `409`。

## 经实现核对的路由清单

```text
POST /api/activity_signal
GET  /api/changelog
GET  /api/survey
POST /api/survey/submit
POST /api/emotion/analysis
GET  /api/file-exists
GET  /api/find-first-image
GET  /api/meme/proxy-image
POST /api/mini_game/invite/respond
POST /api/proactive_chat
POST /api/proactive/music_played_through
GET  /api/seven-day-tutorial/state
PUT  /api/seven-day-tutorial/state
GET  /api/autostart-prompt/state
POST /api/autostart-prompt/heartbeat
POST /api/autostart-prompt/shown
POST /api/autostart-prompt/decision
GET  /api/get_window_title
POST /api/screenshot
POST /api/screenshot/interactive
GET  /api/system/status
GET  /api/token-usage
GET  /api/pending-notices
POST /api/pending-notices/ack
POST /api/steam/set-achievement-status/{name}
POST /api/steam/update-playtime
GET  /api/steam/list-achievements
GET  /api/steam/proxy-image
POST /api/translate
POST /api/yui-guide/handoff/create
POST /api/yui-guide/handoff/consume
```
