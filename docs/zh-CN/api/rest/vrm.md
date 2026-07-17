# VRM API

**前缀：** `/api/model/vrm`

管理 VRM（3D）模型 — 列表、上传、动画管理和表情映射。

## 模型

### `GET /api/model/vrm/config`

返回用户 VRM、内置 VRM 和内置动画的运行时路径配置。这是前端集成响应，其中的文件系统路径不是可移植的公共 schema。

### `GET /api/model/vrm/models`

列出所有可用的 VRM 模型。

### `POST /api/model/vrm/upload`

上传新的 VRM 模型。

**请求体：** 包含 `.vrm` 文件的 `multipart/form-data`。

::: info
最大文件大小：**200 MB**。文件以 1 MB 的分块进行流式传输。
:::

### `DELETE /api/model/vrm/model/{model_name}`

按名称删除用户导入的 VRM 模型（当不存在同名内置模型时，同时删除其关联的表情映射配置）。内置/静态模型无法删除（返回 404）。

::: warning
路径遍历攻击由 `safe_vrm_path()` 验证进行防护。
:::

### `DELETE /api/model/vrm/model`

按 URL 删除用户导入的 VRM 模型。请求体为 JSON `{ "url": "/user_vrm/<file>.vrm" }`。仅允许删除 `/user_vrm/` 下的顶层 `.vrm` 文件。

## 动画

### `GET /api/model/vrm/animations`

列出所有可用的 VRM 动画。

### `POST /api/model/vrm/upload_animation`

上传 VRM 动画文件。

**请求体：** 包含动画文件的 `multipart/form-data`。

## 表情映射

### `GET /api/model/vrm/emotion_mapping/{model_name}`

获取特定 VRM 模型的情感-动画映射。

### `POST /api/model/vrm/emotion_mapping/{model_name}`

更新特定 VRM 模型的表情映射。

### `GET /api/model/vrm/expressions/{model_name}`

返回通用参考表情列表。处理器**不会**解析指定 VRM 文件；前端加载模型后才发现真实表情。不要把该响应当作模型专属能力探测。
