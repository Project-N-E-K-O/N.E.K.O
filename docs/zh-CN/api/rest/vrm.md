# VRM API

**前缀：** `/api/model/vrm`

管理 VRM（3D）模型 — 列表、上传、动画管理和表情映射。

## 模型

### `GET /api/model/vrm/models`

列出所有可用的 VRM 模型（来自项目目录和用户目录）。

### `POST /api/model/vrm/upload`

上传新的 VRM 模型。

**请求体：** 包含 `.vrm` 文件的 `multipart/form-data`。

::: info
最大文件大小：**200 MB**。文件以分块方式流式传输，并带有路径遍历防护。
:::

## 配置

### `GET /api/model/vrm/config`

获取 VRM 模型的统一路径配置（项目目录、用户目录）。

## 动画

### `GET /api/model/vrm/animations`

列出所有可用的 VRM 动画文件（`.vrma` 和 `.vrm`）。

### `POST /api/model/vrm/upload_animation`

上传 VRM 动画文件（`.vrma`）。

**请求体：** 包含动画文件的 `multipart/form-data`。

## 表情映射

### `GET /api/model/vrm/emotion_mapping/{model_name}`

获取 VRM 模型的情感-表情映射。

### `POST /api/model/vrm/emotion_mapping/{model_name}`

更新 VRM 表情映射。

**请求体：**

```json
{
  "happy": ["expressionName1"],
  "sad": ["expressionName2"]
}
```

## 表情列表

### `GET /api/model/vrm/expressions/{model_name}`

获取 VRM 模型支持的表情列表（通用参考列表）。
