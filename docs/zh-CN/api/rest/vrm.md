# VRM API

**前缀：** `/api/model/vrm`

管理 VRM（3D）模型 — 列表、上传、动画管理和表情映射。

## 模型

### `GET /api/model/vrm/models`

列出所有可用的 VRM 模型。

### `GET /api/model/vrm/models/{model_name}`

获取特定 VRM 模型的详细信息。

### `POST /api/model/vrm/upload`

上传新的 VRM 模型。

**请求体：** 包含 `.vrm` 文件的 `multipart/form-data`。

::: info
最大文件大小：**200 MB**。文件以 1 MB 的分块进行流式传输。
:::

### `DELETE /api/model/vrm/delete/{model_name}`

删除 VRM 模型。

::: warning
路径遍历攻击由 `safe_vrm_path()` 验证进行防护。
:::

## 动画

### `GET /api/model/vrm/animation/list`

列出所有可用的 VRM 动画。

### `POST /api/model/vrm/animation/upload`

上传 VRM 动画文件。

**请求体：** 包含动画文件的 `multipart/form-data`。

## 表情映射

### `GET /api/model/vrm/emotion_mapping`

获取 VRM 模型的情感-动画映射。

### `POST /api/model/vrm/emotion_mapping`

更新 VRM 表情映射。
