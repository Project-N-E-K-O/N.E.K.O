# Live2D API

**前缀：** `/api/live2d`

管理 Live2D 模型 — 列表、配置、表情映射、文件上传和参数编辑。

## 模型列表

### `GET /api/live2d/models`

列出所有可用的 Live2D 模型。

**查询参数：** `simple`（可选，布尔值）— 如果为 true，仅返回模型名称，不包含完整配置。

### `GET /api/live2d/user_models`

列出用户导入的模型（区别于内置模型或创意工坊模型）。

## 模型配置

### `GET /api/live2d/model_config/{model_name}`

获取模型的完整配置（位置、缩放、表情映射）。

### `POST /api/live2d/model_config/{model_name}`

保存模型配置。

### `GET /api/live2d/model_config_by_id/{model_id}`

通过 Steam 创意工坊物品 ID 获取配置。

### `POST /api/live2d/model_config_by_id/{model_id}`

通过创意工坊物品 ID 保存配置。

## 表情映射

### `GET /api/live2d/emotion_mapping/{model_name}`

获取模型的情感-动画映射。

**响应示例：**

```json
{
  "happy": { "expression": "f01", "motion": "idle_01" },
  "sad": { "expression": "f03", "motion": "idle_02" }
}
```

### `POST /api/live2d/emotion_mapping/{model_name}`

更新表情映射。

## 参数

### `GET /api/live2d/model_parameters/{model_name}`

获取所有可用的模型参数（用于参数编辑器）。

### `POST /api/live2d/save_model_parameters/{model_name}`

保存调整后的模型参数。

### `GET /api/live2d/load_model_parameters/{model_name}`

加载之前保存的模型参数。

## 文件管理

### `GET /api/live2d/model_files/{model_name}`

列出模型的所有文件。

### `GET /api/live2d/model_files_by_id/{model_id}`

通过创意工坊物品 ID 列出文件。

### `POST /api/live2d/upload_model`

上传新的 Live2D 模型（包含模型压缩包的 multipart 表单）。

### `POST /api/live2d/upload_file/{model_name}`

向现有模型上传附加文件。

### `DELETE /api/live2d/model/{model_name}`

删除模型及其所有文件。

### `GET /api/live2d/open_model_directory/{model_name}`

在系统文件浏览器中打开模型目录。
