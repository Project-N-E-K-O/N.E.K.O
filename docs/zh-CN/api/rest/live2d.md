# Live2D API

**前缀：** `/api/live2d`

管理 Live2D 模型 — 列表、配置、表情映射和参数编辑。

## 模型列表

### `GET /api/live2d/models`

列出所有可用的 Live2D 模型（包含 Steam 创意工坊模型）。

**查询参数：** `simple`（可选，布尔值）— 如果为 true，仅返回模型名称，不包含完整配置。

## 模型配置

### `GET /api/live2d/model_config/{model_name}`

获取模型的完整 `model3.json` 配置。

### `POST /api/live2d/model_config/{model_name}`

更新模型配置（动作/表情）。

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

更新表情映射。同步 EmotionMapping 和 FileReferences。

## 参数

### `GET /api/live2d/model_parameters/{model_name}`

获取 `.cdi3.json` 文件中所有可用的模型参数（用于参数编辑器）。

### `POST /api/live2d/save_model_parameters/{model_name}`

保存调整后的模型参数到 `parameters.json`。

**请求体：**

```json
{
  "parameters": { ... }
}
```

## 文件管理

### `GET /api/live2d/model_files/{model_name}`

列出模型的动作文件（`.motion3.json`）和表情文件（`.exp3.json`）。
