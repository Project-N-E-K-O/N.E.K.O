# 角色 API

**前缀：** `/api/characters`

该 router 负责角色档案、模型绑定、人格引导、角色卡、麦克风选择和声音生命周期。集合路由严格为 `GET /api/characters`，末尾没有 `/`。

## 角色与模型状态

| 方法和路径 | 用途 |
|---|---|
| `GET /api/characters` | 列出角色档案；可根据请求语言本地化可编辑字段名。 |
| `POST /api/characters/catgirl` | 从 JSON 档案创建角色。 |
| `PUT /api/characters/catgirl/{name}` | 更新现有角色的可变字段。 |
| `DELETE /api/characters/catgirl/{name}` | 按安全路径名删除角色。 |
| `POST /api/characters/catgirl/delete` | 通过 JSON 请求体删除；用于无法安全放入 URL 的历史名称。 |
| `POST /api/characters/catgirl/{old_name}/rename` | 重命名角色并迁移关联状态。请求体：`{ "new_name": "..." }`。 |
| `GET`、`POST /api/characters/current_catgirl` | 读取或切换当前角色。POST 请求体：`{ "catgirl_name": "..." }`。 |
| `POST /api/characters/reload` | 从存储重新加载角色配置。 |
| `POST /api/characters/master` | 更新主人档案。 |
| `POST /api/characters/master/{old_name}/rename` | 重命名主人档案。 |
| `POST`、`GET /api/characters/set_microphone`、`/get_microphone` | 保存或读取麦克风。POST 使用 `microphone_id`，`microphone_name` 可选。 |

角色名会做路径安全与长度校验。大多数写接口返回 `{ "success": true }` 一类应用层信封；无效 JSON、非法名称、对象不存在、冲突或存储写入围栏会按操作返回 `400`、`404`、`409` 或 `503`。

### 模型绑定

| 方法和路径 | 用途 |
|---|---|
| `GET /api/characters/current_live2d_model` | 解析当前或指定角色的 Live2D、VRM、MMD 或 PNGTuber 绑定；查询参数为 `catgirl_name`、`item_id`，均可选。 |
| `PUT /api/characters/catgirl/l2d/{name}` | 更新模型绑定；虽然保留历史 `l2d` 路径名，处理器支持当前全部模型类型。 |
| `PATCH /api/characters/catgirl/{name}/touch_set` | 整体替换当前模型的触摸动作配置。 |
| `PUT /api/characters/catgirl/{name}/lighting` | 更新 VRM 灯光。 |
| `GET`、`PUT /api/characters/catgirl/{name}/mmd_settings` | 读取或更新 MMD 渲染、物理、灯光和鼠标跟踪设置。 |

## 人格选择

| 方法和路径 | 用途 |
|---|---|
| `GET /api/characters/persona-presets` | 列出本地化的内置人格预设。 |
| `GET`、`POST /api/characters/persona-onboarding-state` | 读取或更新首次人格引导状态。 |
| `POST`、`DELETE /api/characters/persona-reselect-current` | 请求或清除当前角色的手动重选标记。 |
| `GET`、`PUT`、`DELETE /api/characters/character/{name}/persona-selection` | 读取、设置或清除指定角色的人格选择。 |

## 角色卡与立绘

| 方法和路径 | 用途 |
|---|---|
| `GET /api/characters/character-card/list` | 列出已保存角色卡。 |
| `POST /api/characters/character-card/save` | 将导入的角色卡数据保存到角色配置。 |
| `POST /api/characters/catgirl/save-to-model-folder` | 将角色卡写入模型目录以供打包。 |
| `GET /api/characters/catgirl/{name}/export` | 导出带嵌入档案和模型归档的 PNG 角色卡。 |
| `GET /api/characters/catgirl/{name}/export-settings` | 仅导出档案设置，不含模型资源。 |
| `POST /api/characters/import-card` | 导入 multipart `zip_file`；`card_image` 可选。 |
| `GET /api/characters/card-faces` | 列出卡面数据。 |
| `GET /api/characters/card-metas` | 列出卡片来源和元数据。 |
| `GET`、`PUT /api/characters/catgirl/{name}/card-meta` | 读取或更新单张卡的元数据 JSON。 |
| `GET`、`PUT /api/characters/catgirl/{name}/card-face` | 读取或上传卡面图片（字段 `image`）。 |
| `POST /api/characters/catgirl/{name}/export-with-portrait` | 用 multipart `portrait` 导出；`include_model` 默认为 true。 |

导出接口返回文件而非 JSON。上传/导入会检查文件名、大小、归档条目和路径边界；校验失败通常返回 `400` 或 `413`。

## 声音操作

| 方法和路径 | 用途 |
|---|---|
| `GET /api/characters/voices` | 列出当前 provider 配置可用的声音。 |
| `GET /api/characters/voice_preview` | 为必填 `voice_id` 合成本地化试听；`language`/`i18n_language` 可选，JSON 中返回 base64 音频。 |
| `PUT /api/characters/catgirl/voice_id/{name}` | 给角色绑定声音 ID。 |
| `GET /api/characters/catgirl/{name}/voice_mode_status` | 查询角色声音模式状态。 |
| `POST /api/characters/catgirl/{name}/unregister_voice` | 移除角色的自定义声音绑定/注册。 |
| `POST /api/characters/clear_voice_ids` | 清除所有角色存储的本地声音 ID。 |
| `GET /api/characters/custom_tts_voices` | 列出自定义 TTS 声音；可用 `provider` 过滤。 |
| `POST /api/characters/voices` | 从 JSON 注册自定义声音。 |
| `DELETE /api/characters/voices/{voice_id}` | 删除自定义声音。 |
| `POST /api/characters/voice_clone` | 从 multipart 音频克隆；必填 `file`、`prefix`，另接受 provider 专属字段。 |
| `POST /api/characters/voice_clone_direct` | 从经过安全校验的直链音频注册/克隆；私网地址和不安全重定向会被拒绝。 |
| `POST /api/characters/voice_design_preview` | 请求 ElevenLabs voice design 试听候选。 |
| `POST /api/characters/voice_design_create` | 将选中的 design 试听保存成可复用声音。 |
| `POST /api/characters/audio/analyze_silence` | 分析 multipart `file` 的静音区间。 |
| `POST /api/characters/audio/trim_silence` | 裁剪 multipart `file`；`task_id` 可选。 |
| `GET /api/characters/audio/trim_progress/{task_id}` | 查询裁剪进度。 |
| `POST /api/characters/audio/trim_cancel/{task_id}` | 请求取消裁剪。 |

Provider 错误会进入 JSON 信封；能分类时也会使用 HTTP `4xx`/`5xx`。Provider 专属请求字段和声音目录属于运行时数据，不应视作跨 provider 的稳定 schema。

## 经实现核对的路由清单

```text
GET    /api/characters
GET    /api/characters/character-card/list
POST   /api/characters/catgirl/save-to-model-folder
POST   /api/characters/character-card/save
GET    /api/characters/catgirl/{name}/export
GET    /api/characters/catgirl/{name}/export-settings
POST   /api/characters/import-card
GET    /api/characters/card-faces
GET    /api/characters/card-metas
GET    /api/characters/catgirl/{name}/card-meta
PUT    /api/characters/catgirl/{name}/card-meta
GET    /api/characters/catgirl/{name}/card-face
PUT    /api/characters/catgirl/{name}/card-face
POST   /api/characters/catgirl/{name}/export-with-portrait
POST   /api/characters/catgirl/{old_name}/rename
GET    /api/characters/current_catgirl
POST   /api/characters/current_catgirl
POST   /api/characters/reload
POST   /api/characters/master
POST   /api/characters/master/{old_name}/rename
POST   /api/characters/catgirl
PUT    /api/characters/catgirl/{name}
POST   /api/characters/catgirl/delete
DELETE /api/characters/catgirl/{name}
POST   /api/characters/set_microphone
GET    /api/characters/get_microphone
GET    /api/characters/current_live2d_model
PUT    /api/characters/catgirl/l2d/{name}
PATCH  /api/characters/catgirl/{name}/touch_set
PUT    /api/characters/catgirl/{name}/lighting
PUT    /api/characters/catgirl/{name}/mmd_settings
GET    /api/characters/catgirl/{name}/mmd_settings
GET    /api/characters/persona-presets
GET    /api/characters/persona-onboarding-state
POST   /api/characters/persona-onboarding-state
POST   /api/characters/persona-reselect-current
DELETE /api/characters/persona-reselect-current
GET    /api/characters/character/{name}/persona-selection
PUT    /api/characters/character/{name}/persona-selection
DELETE /api/characters/character/{name}/persona-selection
POST   /api/characters/audio/analyze_silence
POST   /api/characters/audio/trim_silence
GET    /api/characters/audio/trim_progress/{task_id}
POST   /api/characters/audio/trim_cancel/{task_id}
POST   /api/characters/voice_clone
POST   /api/characters/voice_design_preview
POST   /api/characters/voice_design_create
POST   /api/characters/voice_clone_direct
GET    /api/characters/voices
GET    /api/characters/voice_preview
PUT    /api/characters/catgirl/voice_id/{name}
GET    /api/characters/catgirl/{name}/voice_mode_status
POST   /api/characters/catgirl/{name}/unregister_voice
POST   /api/characters/clear_voice_ids
GET    /api/characters/custom_tts_voices
POST   /api/characters/voices
DELETE /api/characters/voices/{voice_id}
```
