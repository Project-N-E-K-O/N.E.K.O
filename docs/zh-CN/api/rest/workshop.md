# Steam 创意工坊 API

**前缀：** `/api/steam/workshop`

这是第一方创意工坊 UI 的集成面，负责本地暂存、Steam UGC 浏览/下载/发布、角色同步、退订清理，以及可选的参考声音打包。

::: warning 第一方本地接口
很多请求/响应字段是 UI 工作流状态，不是带版本承诺的第三方 schema。部分接口接受本地路径，或会修改 Steam 订阅与角色数据。服务应仅监听回环地址。Steamworks 未初始化时，依赖 Steam 的操作返回 `503`。
:::

## 配置与沙箱文件工具

| 方法和路径 | 用途 |
|---|---|
| `GET /api/steam/workshop/config` | 读取 `default_workshop_folder`、`user_mod_folder` 和自动创建设置。 |
| `POST /api/steam/workshop/config` | 合并这些支持字段，并在启用时创建配置目录。 |
| `GET /api/steam/workshop/read-file` | 读取创意工坊根目录下必填查询参数 `path`；文本直接返回，已知二进制类型转 base64；上限 5 MiB。 |
| `GET /api/steam/workshop/list-chara-files` | 列出创意工坊根目录下必填 `directory` 的顶层 `*.chara.json`。 |
| `GET /api/steam/workshop/list-audio-files` | 列出必填 `directory` 的顶层 `.mp3`/`.wav`。 |

路径边界检查会拒绝穿越；路径不存在为 `404`，读取过大为 `413`，其他读取错误为 `500`。

## Steam 物品浏览与下载

| 方法和路径 | 用途 |
|---|---|
| `GET /api/steam/workshop/status` | 报告 Steamworks 是否初始化。 |
| `GET /api/steam/workshop/subscribed-items` | 返回缓存/刷新的已订阅 UGC 元数据。 |
| `GET /api/steam/workshop/item/{item_id}` | 返回单个物品元数据。 |
| `GET /api/steam/workshop/item/{item_id}/path` | 解析已安装物品的本地路径。 |
| `POST /api/steam/workshop/item/{item_id}/download` | 触发下载；可选请求体：`high_priority`、`wait`、`timeout`（1–600 秒）。 |
| `GET /api/steam/workshop/item/{item_id}/download-status` | 轮询状态、字节进度和安装路径。 |

非数字 ID 为 `400`，未订阅下载为 `409`，Steam 拒绝可为 `502`。`wait: true` 超时会返回 HTTP `202` 和当前进度，以便继续轮询；已安装且无需更新时立即成功。

## 暂存与发布

### `POST /api/steam/workshop/prepare-upload`

创建临时 `WorkshopExport/item_*`，复制角色卡以及 Live2D、VRM 或 MMD 模型。UI 必填字段为 `charaData`、`modelName`；`modelType` 默认 `live2d`。可选 `fileName`、`character_card_name`。已上传元数据、不支持类型、不安全路径或模型资源不存在都会被拒绝。

### 上传与清理工具

| 方法和路径 | 用途 |
|---|---|
| `POST /api/steam/workshop/upload-preview-image` | 上传 multipart JPEG/PNG `file`；`content_folder` 可指定暂存目录，返回 `file_path`。 |
| `GET /api/steam/workshop/check-upload-status` | 检查查询参数 `item_path` 的暂存/上传状态。 |
| `POST /api/steam/workshop/cleanup-temp-folder` | 仅当请求体 `temp_folder` 解析后位于 `WorkshopExport` 内时才删除。 |

### `POST /api/steam/workshop/publish`

发布已准备目录。JSON 必填 `title`、`content_folder`、整数 `visibility`；可选 `description`、`preview_image`、`tags`、`change_note`、`character_card_name`。`content_folder` 必须位于配置的创意工坊根目录内。Steam callback 属于异步原生集成；创建/更新进度和错误通过 `success` 信封与 HTTP 状态返回。

::: info 平台边界
macOS arm64 上当前 Steamworks 绑定存在 callback 崩溃风险，因此原生发布会被明确拒绝。
:::

## 角色元数据与同步

| 方法和路径 | 用途 |
|---|---|
| `GET /api/steam/workshop/meta/{character_name}` | 读取角色卡本地 `.workshop_meta.json` 快照和上传状态。 |
| `POST /api/steam/workshop/sync-characters` | 扫描已订阅、已安装物品并同步角色卡。 |
| `POST /api/steam/workshop/sync-character/{item_id}` | 同步单个已订阅物品中的角色卡。 |
| `POST /api/steam/workshop/unsubscribe` | 退订请求体 `item_id`，然后保守清理与该 UGC 物品关联的角色/资源。 |

同步结果可能报告跳过/冲突卡片、未安装或存储写入围栏。退订结合来源元数据和保守磁盘检查，不会仅因 Workshop 文件夹含同名卡片就删除同名本地角色。

## 参考声音打包

| 方法和路径 | 用途 |
|---|---|
| `POST /api/steam/workshop/upload-reference-audio` | 上传 multipart `file` 和 `WorkshopExport` 内 `content_folder`；支持 MP3/WAV 并写入 `voice_manifest.json`。可选 `prefix`、`display_name`、`ref_language`、`provider_hint`。 |
| `POST /api/steam/workshop/remove-reference-audio` | 从请求体 `content_folder` 删除暂存样本和 manifest。 |
| `GET /api/steam/workshop/voice-reference/{item_id}` | 返回已安装订阅物品的规范化 manifest；没有时为 `available: false`。 |
| `GET /api/steam/workshop/voice-reference/{item_id}/audio` | 流式返回该物品经过校验的参考音频。 |

这些接口只打包参考材料，不会自行克隆或注册本地 TTS 声音。

## 经实现核对的路由清单

```text
GET  /api/steam/workshop/config
POST /api/steam/workshop/config
GET  /api/steam/workshop/read-file
GET  /api/steam/workshop/list-chara-files
GET  /api/steam/workshop/list-audio-files
GET  /api/steam/workshop/status
POST /api/steam/workshop/item/{item_id}/download
GET  /api/steam/workshop/item/{item_id}/download-status
GET  /api/steam/workshop/item/{item_id}/path
GET  /api/steam/workshop/item/{item_id}
GET  /api/steam/workshop/meta/{character_name}
POST /api/steam/workshop/upload-preview-image
GET  /api/steam/workshop/check-upload-status
POST /api/steam/workshop/prepare-upload
POST /api/steam/workshop/cleanup-temp-folder
POST /api/steam/workshop/publish
POST /api/steam/workshop/sync-characters
POST /api/steam/workshop/sync-character/{item_id}
GET  /api/steam/workshop/subscribed-items
POST /api/steam/workshop/unsubscribe
POST /api/steam/workshop/upload-reference-audio
POST /api/steam/workshop/remove-reference-audio
GET  /api/steam/workshop/voice-reference/{item_id}
GET  /api/steam/workshop/voice-reference/{item_id}/audio
```
