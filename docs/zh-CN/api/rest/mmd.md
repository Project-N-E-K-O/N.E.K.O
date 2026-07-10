# MMD API

**Prefix:** `/api/model/mmd`

管理 MMD（MikuMikuDance）形象模型 —— PMX/PMD 模型、VMD 动画，以及每个模型的情感映射。

::: info
MMD 表情基于形变目标（morph target）/ 混合形状（blendshape）：每种情感通过驱动模型中具名的 morph 来呈现。下方的情感映射端点用于把情感标签关联到模型的 morph 目标。
:::

## 上传

### `POST /api/model/mmd/upload`

上传单个 MMD 模型文件（`.pmx` / `.pmd`）。请求为 `multipart/form-data`，带一个 `file` 字段，分块流式写入磁盘。最大 500 MB。

**Response:**

```json
{
  "success": true,
  "message": "...",
  "model_name": "<filename stem>",
  "model_url": "/user_mmd/<filename>",
  "file_size": 0
}
```

出错时（无文件、扩展名不符、文件已存在或过大）返回 `{ "success": false, "error": "..." }`，状态码为 4xx/5xx。

### `POST /api/model/mmd/upload_animation`

上传单个 VMD 动画文件（`.vmd`）。与模型上传相同，使用 `multipart/form-data` 的 `file` 字段，限制 500 MB。文件存放在用户 MMD 目录的 `animation/` 子目录下。

**Response:**

```json
{
  "success": true,
  "message": "...",
  "filename": "<filename>",
  "file_path": "/user_mmd/animation/<filename>"
}
```

### `POST /api/model/mmd/upload_zip`

上传 MMD 模型 **ZIP 压缩包**（一个 `.pmx`/`.pmd` 模型及其纹理）。压缩包先写入临时文件，经校验后解压到以模型命名的子目录（或压缩包本身已有的顶层目录）。

::: info
许多 MMD 压缩包来自日本，文件名以 Shift-JIS / CP932 存储（中文/韩文包则常用 GBK、Big5 或 EUC-KR），且未设置 UTF-8 标志位。服务器会探测真实的文件名编码，在解压时还原原始的中日韩文件名，而不是留下乱码。
:::

ZIP 中必须至少包含一个 `.pmx`/`.pmd` 文件。同时应用 zip bomb 防护：条目数最多 10000，解压后总大小最多 2 GB；含绝对路径或 `..` 的条目会被拒绝。

**Response:**

```json
{
  "success": true,
  "message": "...",
  "model_name": "<model stem>",
  "model_url": "/user_mmd/<relative path to model>",
  "file_count": 0,
  "file_size": 0
}
```

## 列表

### `GET /api/model/mmd/models`

列出可用的 MMD 模型（`.pmx` / `.pmd`），递归搜索项目 `static/mmd/` 目录、用户 MMD 目录，以及已订阅的 Steam 创意工坊条目。

**Response:** `{ "success": true, "models": [ ... ] }`。每个条目包含 `name`、`filename`、`url`、`rel_path`、`type`、`size` 和 `location`（`"project"`、`"user"` 或 `"steam_workshop"`）。创意工坊条目额外带有 `source` 和 `item_id`。没有模型文件的残留模型目录会以 `"broken": true` 标记返回。

### `GET /api/model/mmd/animations`

列出 VMD 动画文件，来源为项目 `static/mmd/animation/` 目录和用户 MMD 的 `animation/` 目录。

**Response:** `{ "success": true, "animations": [ ... ] }`。每个条目包含 `name`、`filename`、`url`、`type`（`"vmd"`）和 `size`。

### `GET /api/model/mmd/animations/list`

列出可删除的用户上传 VMD 动画（即位于用户 MMD 的 `animation/` 目录下的动画）。

**Response:** `{ "success": true, "animations": [ ... ] }`。每个条目包含 `name`、`filename`、`url` 和 `path`。

## 配置

### `GET /api/model/mmd/config`

返回 MMD 路径配置。

**Response:**

```json
{
  "success": true,
  "paths": {
    "user_mmd": "/user_mmd",
    "static_mmd": "/static/mmd"
  }
}
```

## 情感映射

### `GET /api/model/mmd/emotion_mapping`

获取某个模型的情感映射配置。

**Query:** `model` —— 模型名称（不允许包含路径分隔符）。当省略该参数或不存在配置时，返回空映射。

**Response:** `{ "success": true, "mapping": { ... } }`

### `POST /api/model/mmd/emotion_mapping`

创建或更新模型的情感映射。映射按模型持久化到用户 MMD 的 `emotion_config/` 目录下。

**Body:**

```json
{
  "model": "<model name>",
  "mapping": { }
}
```

`model` 为必填，且不得包含路径分隔符；`mapping` 必须是对象。

**Response:** `{ "success": true, "message": "..." }`

## 删除

### `DELETE /api/model/mmd/model`

删除一个用户 MMD 模型。当模型位于子目录中时，会删除其整个目录（纹理及其他关联资源）；位于顶层的模型文件则单独删除。对应的情感映射配置也会一并删除。项目内置模型（`/static/mmd/...`）不可删除。

**Body:**

```json
{
  "url": "/user_mmd/<relative path>"
}
```

**Response:** `{ "success": true, "message": "...", "deleted_files": 0 }`

### `DELETE /api/model/mmd/animation`

删除一个用户上传的 VMD 动画。目标必须是位于用户 MMD `animation/` 目录下的 `.vmd` 文件。

**Body:**

```json
{
  "url": "/user_mmd/animation/<filename>"
}
```

**Response:** `{ "success": true, "message": "..." }`
