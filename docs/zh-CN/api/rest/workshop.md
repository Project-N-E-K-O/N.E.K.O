# Steam 创意工坊 API

**前缀：** `/api/steam/workshop`

管理 Steam 创意工坊物品 — 浏览已订阅物品、发布和本地 Mod 管理。

::: info
Steam 创意工坊功能需要 Steam 客户端正在运行且 Steamworks SDK 已初始化。
:::

## 物品

### `GET /api/steam/workshop/items`

获取所有已订阅的 Steam 创意工坊物品。

### `GET /api/steam/workshop/items/{item_id}`

获取特定创意工坊物品的详细信息。

### `POST /api/steam/workshop/items/publish`

发布新物品到 Steam 创意工坊。

**请求体：** 物品元数据，包括标题、描述、标签和内容路径。

::: warning
发布操作使用序列化锁以防止并发发布。
:::

### `POST /api/steam/workshop/items/{item_id}/update`

更新现有的创意工坊物品。

## 配置

### `GET /api/steam/workshop/config`

获取创意工坊配置（创意工坊根路径、元数据）。

### `GET /api/steam/workshop/local_items`

列出尚未发布到创意工坊的本地 Mod/物品。

## 创意工坊元数据

创意工坊物品在其目录中的 `.workshop_meta.json` 文件中存储角色卡片元数据，包括：

- 角色性格数据
- 模型绑定
- 语音配置
- 发布元数据

所有文件操作均强制执行路径遍历防护。
