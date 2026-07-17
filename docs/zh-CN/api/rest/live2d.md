# Live2D API

**前缀：** `/api/live2d`

这 17 个路由服务于 N.E.K.O. 第一方 Live2D 模型管理器和参数编辑器。它们会读取和修改本机模型文件，不是远程模型托管 API。

## 路由清单

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/live2d/models` | 列出内置、用户导入和已安装创意工坊模型 |
| `GET` | `/api/live2d/user_models` | 列出用户导入模型 |
| `GET` / `POST` | `/api/live2d/model_config/{model_name}` | 读取 Cubism `.model3.json`；更新动作/表情引用 |
| `GET` / `POST` | `/api/live2d/model_config_by_id/{model_id}` | 按 Steam 创意工坊物品 ID 执行同类操作 |
| `GET` / `POST` | `/api/live2d/emotion_mapping/{model_name}` | 读取或写入情绪到动作/表情的映射 |
| `GET` | `/api/live2d/model_files/{model_name}` | 列出 `.motion3.json` 和 `.exp3.json` 文件 |
| `GET` | `/api/live2d/model_files_by_id/{model_id}` | 按创意工坊 ID 列出文件，失败时回退为模型名 |
| `GET` | `/api/live2d/model_parameters/{model_name}` | 从 `.cdi3.json` 读取参数元数据 |
| `POST` | `/api/live2d/save_model_parameters/{model_name}` | 将编辑器数值保存到 `parameters.json` |
| `GET` | `/api/live2d/load_model_parameters/{model_name}` | 加载已保存的编辑器数值 |
| `POST` | `/api/live2d/upload_model` | 导入多文件模型包 |
| `POST` | `/api/live2d/upload_file/{model_name}` | 添加一个动作或表情 JSON 文件 |
| `GET` | `/api/live2d/open_model_directory/{model_name}` | 在系统文件管理器中打开模型目录 |
| `DELETE` | `/api/live2d/model/{model_name}` | 删除用户导入模型 |

## 列表

`GET /api/live2d/models?simple=false` 为兼容旧客户端，直接返回完整模型数组。`simple=true` 时返回 `{ "success": true, "models": ["..."] }`。只有 Steam 报告物品已安装且其中含 `.model3.json` 时，才会加入创意工坊模型。

`GET /api/live2d/user_models` 以 `{ "success": true, "models": [...] }` 返回 N.E.K.O. 可读取的用户模型目录。

## 配置与情绪映射

配置 `GET` 路由返回 `{ "success": true, "config": {...} }`。如果可读的 `.model3.json` 缺少 `FileReferences.Motions` 或 `FileReferences.Expressions`，处理器会补上容器并尝试写回。

配置 `POST` 接受类似 Cubism 配置的 JSON，但只持久化：

```json
{
  "FileReferences": {
    "Motions": {},
    "Expressions": []
  }
}
```

提交的其他 `.model3.json` 字段会被忽略；该接口不能替换完整模型配置。

`GET /api/live2d/emotion_mapping/{model_name}` 返回已存储的 `EmotionMapping`，不存在时从 `FileReferences` 推导 `{ "motions": {...}, "expressions": {...} }`。`POST` 接受后一种结构，标准化安全相对路径，同时写入标准 Cubism 引用和兼容字段 `EmotionMapping`，并忽略保留组 `常驻` 中的动作。

## 文件与参数

- `model_files` 和 `model_files_by_id` 递归返回 `motion_files`、`expression_files`；ID 版本还返回 `model_config_url`。
- `model_parameters` 从 `.cdi3.json` 读取参数和参数组元数据，不读取实时运行值。
- `save_model_parameters` 需要 `{ "parameters": { ... } }`，且 `parameters` 必须是对象。
- `parameters.json` 不存在或不是对象时，`load_model_parameters` 返回空对象。

## 导入与修改

`POST /api/live2d/upload_model` 使用 `multipart/form-data`，包含一个或多个 `files`，文件名保留相对路径。它**不是**单压缩包上传。包中必须恰好有一个 `.model3.json`。路径不安全、配置文件为零/多个、或目标已有有效模型时返回 HTTP `400`。导入后，N.E.K.O. 会清除上传动作文件中的嘴型/唇同步曲线，使运行时唇同步可以接管。

`POST /api/live2d/upload_file/{model_name}?file_type=motion` 接受一个 `file`。`file_type` 为 `motion` 或 `expression`；文件后缀必须分别是 `.motion3.json` 或 `.exp3.json`，内容必须是 UTF-8 JSON，且不超过 50 MB。已有文件不会被覆盖。

`DELETE /api/live2d/model/{model_name}` 只删除可写用户导入目录内的模型。内置/创意工坊模型，以及受 Windows 安全策略保护而只读的用户目录，会返回 HTTP `403`。

`GET /api/live2d/open_model_directory/{model_name}` 会启动 Explorer、Finder 或 `xdg-open`，产生本地桌面副作用，仅供第一方设置 UI 使用。

## 错误

大多数修改失败返回 `{ "success": false, "error": "..." }`，状态码为 HTTP `400`、`403`、`404` 或 `500`。部分遗留读取接口会在 HTTP `200` 中返回相同失败结构，因此不能只看状态码，还要检查 `success`。路径末尾没有 `/`。
