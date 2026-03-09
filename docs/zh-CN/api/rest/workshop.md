# Steam 创意工坊 API

**前缀：** `/api/steam/workshop`

管理 Steam 创意工坊物品 — 浏览已订阅物品、发布、本地 Mod 管理和配置。

::: info
Steam 创意工坊功能需要 Steam 客户端正在运行且 Steamworks SDK 已初始化。
:::

## 已订阅物品

### `GET /api/steam/workshop/subscribed-items`

获取所有已订阅的 Steam 创意工坊物品。

### `GET /api/steam/workshop/item/{item_id}`

获取特定创意工坊物品的详细信息。

### `GET /api/steam/workshop/item/{item_id}/path`

获取创意工坊物品的本地安装路径。

### `POST /api/steam/workshop/unsubscribe`

取消订阅创意工坊物品。

**请求体：**

```json
{ "item_id": 12345678 }
```

## 发布

### `POST /api/steam/workshop/prepare-upload`

准备发布内容（创建临时目录、复制文件）。

**请求体：**

```json
{
  "charaData": { ... },
  "modelName": "model_name",
  "fileName": "optional_filename",
  "character_card_name": "optional_card_name"
}
```

### `POST /api/steam/workshop/publish`

将物品发布到 Steam 创意工坊。

**请求体：**

```json
{
  "title": "物品标题",
  "content_folder": "/path/to/content",
  "visibility": 0,
  "preview_image": "/path/to/preview.jpg",
  "description": "物品描述",
  "tags": ["tag1", "tag2"],
  "change_note": "初始发布",
  "character_card_name": "card_name"
}
```

### `POST /api/steam/workshop/upload-preview-image`

上传预览图片（JPEG/PNG，统一为 `preview.*`）。

**请求体：** 包含 `file` 字段和可选 `content_folder` 的 `multipart/form-data`。

### `GET /api/steam/workshop/check-upload-status`

检查当前上传状态。

**查询参数：** `item_path` — 要检查的内容路径。

### `POST /api/steam/workshop/cleanup-temp-folder`

清理临时上传目录。

**请求体：**

```json
{ "temp_folder": "/path/to/temp" }
```

## 配置

### `GET /api/steam/workshop/config`

获取创意工坊配置（路径、设置）。

### `POST /api/steam/workshop/config`

更新创意工坊配置。

**请求体：**

```json
{
  "default_workshop_folder": "/path",
  "auto_create_folder": true,
  "user_mod_folder": "/path"
}
```

## 元数据

### `GET /api/steam/workshop/meta/{character_name}`

获取角色卡片的创意工坊元数据。

## 本地物品

### `POST /api/steam/workshop/local-items/scan`

扫描本地目录中的创意工坊兼容物品。

**请求体：**

```json
{ "folder_path": "/path/to/scan" }
```

### `GET /api/steam/workshop/local-items/{item_id}`

获取本地创意工坊物品。

**查询参数：** `folder_path`（可选）

## 文件工具

### `GET /api/steam/workshop/read-file`

读取创意工坊文件的内容。

**查询参数：** `path` — 创意工坊内容中的文件路径。

### `GET /api/steam/workshop/list-chara-files`

列出目录中所有 `.chara.json` 文件。

**查询参数：** `directory`

### `GET /api/steam/workshop/list-audio-files`

列出目录中所有音频文件。

**查询参数：** `directory`

::: warning
所有文件操作均强制执行路径遍历防护。
:::
