# 点歌台 API

**前缀：** `/api/jukebox`

点歌台是绑定到角色的歌曲 / 动作库，用于唱歌与预设动作。它管理上传的**歌曲**（音频文件）、**动画**（如 VMD/VRMA 的动画文件）、二者之间的**绑定关系**，以及配置的**导入 / 导出**。

歌曲与动画各自维护一份 MD5 索引用于去重。软件自带的资源会标记 `isBuiltin`——删除自带资源只是将其隐藏（`visible: false`），而不会删除文件本身。

::: info
这些接口返回的配置会把自带（内置）库与用户库融合在一起，用户条目优先。落盘时只持久化用户资源与覆盖设置。
:::

## 配置

### `GET /config`

返回完整的点歌台配置：`songs`、`actions`、`bindings`、`md5Index`，以及下文描述的摘要字段。

**响应：**

```json
{
  "version": "1.0",
  "songs": { "song_001": { "id": "song_001", "name": "...", "artist": "...", "audio": "songs/...", "audioMd5": "...", "audioFormat": "mp3", "visible": true, "uploadDate": "...", "defaultAction": "" } },
  "actions": { "action_001": { "id": "action_001", "name": "...", "file": "actions/...", "fileMd5": "...", "format": "vmd", "uploadDate": "...", "visible": true, "missing": false } },
  "bindings": { "song_001": { "action_001": { "offset": 0 } } },
  "md5Index": { "songs": {}, "actions": {} },
  "configRevision": "...",
  "songCount": 0,
  "visibleSongCount": 0,
  "actionCount": 0
}
```

### `GET /config/summary`

返回一份轻量摘要，适合用于轮询判断完整歌单是否需要刷新。

**响应：**

```json
{
  "configRevision": "...",
  "songCount": 0,
  "visibleSongCount": 0,
  "actionCount": 0
}
```

::: info
`configRevision` 是 `version` + `songs` + `actions` + `bindings` 的一个短而稳定的哈希值。轮询 `/config/summary`，仅当 `configRevision` 变化时才重新拉取 `/config`。
:::

## 歌曲

### `POST /songs`

上传一首或多首歌曲。`multipart/form-data`。

**请求体：**

- `files` —— 一个或多个音频文件。允许的扩展名：`.mp3`、`.wav`、`.ogg`、`.flac`。单文件上限 1 GB。
- `metadata` —— JSON 字符串数组，逐首歌曲 `[{ "name": "...", "artist": "..." }, ...]`。可选；缺失的条目会回退到音频内嵌标签，再回退到文件名。

**响应：** 单文件时直接返回结果对象（`{ "success": true, "song": { ... } }` 或 `{ "success": false, "error": "..." }`）。多文件时返回 `{ "success": true, "results": [ ... ] }`。重复音频（MD5 相同）会逐项被拒绝。

### `POST /songs/batch-delete`

在一次校验过的批处理中删除上传的歌曲、隐藏自带的歌曲。

**请求体：**

```json
{ "songIds": ["song_001", "song_002"] }
```

**响应：** 计数与逐项结果。

```json
{
  "success": true,
  "partial": false,
  "requestedCount": 2,
  "deletedCount": 1,
  "hiddenCount": 1,
  "failedCount": 0,
  "deleted": [{ "songId": "song_001", "name": "..." }],
  "hidden": [{ "songId": "song_002", "name": "..." }],
  "failed": []
}
```

### `DELETE /songs/{song_id}`

删除一首上传的歌曲，或隐藏一首自带歌曲。对用户歌曲会移除文件、绑定关系与 MD5 索引条目。对自带歌曲则返回 `{ "success": true, "message": "...", "hidden": true }`。

**路径参数：** `song_id` —— 歌曲 ID。

### `PUT /songs/{song_id}/visibility`

设置歌曲是否可见。`multipart/form-data`。

**路径参数：** `song_id` —— 歌曲 ID。

**请求体：** `visible` —— 布尔值（表单字段）。

### `PUT /songs/{song_id}/metadata`

更新歌曲的显示名称和/或歌手。`multipart/form-data`。

**路径参数：** `song_id` —— 歌曲 ID。

**请求体：** `name`、`artist` —— 可选表单字段；只更新已提供的字段。

### `PUT /songs/{song_id}/default-action`

设置歌曲的默认动画。该动画必须已绑定到这首歌曲。传空字符串可清除默认动画。

**路径参数：** `song_id` —— 歌曲 ID。

**请求体：** `action_id` —— 表单字段；动画 ID，或留空以取消。

**响应：** `{ "success": true, "defaultAction": "action_001" }`

## 动画

### `POST /actions`

上传一个或多个动画。`multipart/form-data`。

**请求体：**

- `files` —— 一个或多个动画文件。允许的扩展名：`.vmd`、`.bvh`、`.fbx`、`.vrma`。单文件上限 1 GB。
- `metadata` —— JSON 字符串数组，逐个动画 `[{ "name": "..." }, ...]`。可选；缺失的名称会回退到文件名。

**响应：** 与 `POST /songs` 形状相同：单文件返回一个结果对象，多文件返回 `{ "success": true, "results": [ ... ] }`。重复文件（MD5 相同）会逐项被拒绝。

### `POST /actions/batch-delete`

在一次校验过的批处理中删除上传的动画、隐藏自带的动画。

**请求体：**

```json
{ "actionIds": ["action_001", "action_002"] }
```

**响应：** 与 `POST /songs/batch-delete` 相同的计数 / 逐项形状，键为 `actionId`。

### `DELETE /actions/{action_id}`

删除一个上传的动画，或隐藏一个自带动画。对用户动画会移除文件、清除其在绑定关系与各歌曲 `defaultAction` 中的引用，并移除 MD5 索引条目。对自带动画则返回 `{ "success": true, "message": "...", "hidden": true }`。

**路径参数：** `action_id` —— 动画 ID。

### `PUT /actions/{action_id}/visibility`

设置动画是否可见。`multipart/form-data`。

**路径参数：** `action_id` —— 动画 ID。

**请求体：** `visible` —— 布尔值（表单字段）。

### `PUT /actions/{action_id}/metadata`

更新动画的显示名称。`multipart/form-data`。

**路径参数：** `action_id` —— 动画 ID。

**请求体：** `name` —— 表单字段（必填）。

## 绑定

### `POST /bind`

将一个动画绑定到一首歌曲。`multipart/form-data`。如果该歌曲尚无同一动画类型的默认动画，则新绑定的动画会成为默认动画。

**请求体：**

- `songId` —— 歌曲 ID。
- `actionId` —— 动画 ID。
- `offset` —— 整数偏移量，默认 `0`。

**响应：** `{ "success": true, "defaultAction": "action_001" }`

### `DELETE /bind`

解除一首歌曲与一个动画之间的绑定。`multipart/form-data`。如果被解绑的动画正是该歌曲的默认动画，则清除默认动画。

**请求体：** `songId`、`actionId` —— 歌曲与动画的 ID。

**响应：** `{ "success": true, "defaultAction": "..." }`。绑定不存在时返回 `404`。

## 导入 / 导出

### `POST /export`

将选中的（或全部）歌曲与动画导出为 ZIP 压缩包。`multipart/form-data`。自带歌曲会被跳过；自带动画只按 ID/MD5 导出（不打包文件）。绑定关系按 MD5 级别导出，以便在另一台机器上正确重新关联。

**请求体：**

- `songIds` —— 可选的歌曲 ID JSON 字符串数组。省略时考虑全部歌曲（受 `includeHidden` 约束）。
- `actionIds` —— 可选的动画 ID JSON 字符串数组。省略时（导出全部）导出所有动画。
- `includeHidden` —— 布尔值，默认 `true`。为 `false` 时，隐藏的歌曲/动画及其绑定会被排除。

**响应：** 一个流式 `application/zip` 下载（`jukebox_export.zip`），包含 `config.json` 以及歌曲/动画文件。

### `POST /import`

导入此前导出的 ZIP 压缩包。`multipart/form-data`。MD5 级别的绑定会被转换回本地的 ID 级别绑定；匹配到的资源会被合并而非重复导入。

**请求体：** `file` —— ZIP 压缩包（上限 10 GB）。

**响应：** 导入统计。

```json
{
  "success": true,
  "stats": {
    "songsAdded": 0,
    "songsMerged": 0,
    "actionsAdded": 0,
    "actionsMerged": 0,
    "bindingsAdded": 0
  }
}
```

### `GET /file/{file_path:path}`

提供一个歌曲或动画文件。优先使用用户文档目录，缺失时回退到自带目录。已防护目录遍历攻击。

**路径参数：** `file_path` —— 相对路径，例如 `songs/song_001.mp3` 或 `actions/action_001.vmd`。

**响应：** 文件本身，媒体类型根据其扩展名推断（例如 `.mp3` 为 `audio/mpeg`）。

### `POST /pack-folder`

将任意一组上传的文件（保留其相对路径）打包为单个 ZIP 压缩包。`multipart/form-data`。这是点歌台导入/导出界面使用的一个通用工具。

**请求体：** `files` —— 一个或多个文件，每个文件以其相对路径作为文件名携带。

**响应：** 一个流式 `application/zip` 下载（`packed.zip`）。
