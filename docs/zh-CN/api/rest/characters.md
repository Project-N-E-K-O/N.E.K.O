# 角色 API

**前缀：** `/api/characters`

管理 AI 角色（内部称为"catgirl"或"lanlan"），包括增删改查操作、语音设置和麦克风配置。

## 角色管理

### `GET /api/characters/`

列出所有角色，支持可选的语言本地化。

**查询参数：** `language`（可选）— 用于翻译字段名称的区域代码。

---

### `POST /api/characters/catgirl`

创建新角色。

**请求体：** 包含性格字段的角色数据对象。

---

### `PUT /api/characters/catgirl/{name}`

更新现有角色的设置。

**路径参数：** `name` — 角色标识符。

**请求体：** 更新后的角色数据。

---

### `DELETE /api/characters/catgirl/{name}`

删除角色。

---

### `POST /api/characters/catgirl/{old_name}/rename`

重命名角色。更新所有引用，包括记忆文件。

**请求体：**

```json
{ "new_name": "new_character_name" }
```

---

### `GET /api/characters/current_catgirl`

获取当前激活的角色。

### `POST /api/characters/current_catgirl`

切换激活的角色。

**请求体：**

```json
{ "catgirl_name": "character_name" }
```

---

### `POST /api/characters/reload`

从磁盘重新加载角色配置。

### `POST /api/characters/master`

更新主人（所有者/玩家）信息。

## 模型绑定

### `GET /api/characters/current_live2d_model`

获取当前角色的模型信息。

**查询参数：** `catgirl_name`（可选）、`item_id`（可选，创意工坊物品 ID）

### `PUT /api/characters/catgirl/l2d/{name}`

更新角色的模型绑定（Live2D 或 VRM）。

**请求体：**

```json
{
  "live2d": "model_directory_name",
  "vrm": "vrm_model_name",
  "model_type": "live2d",
  "item_id": "workshop_item_id",
  "vrm_animation": "animation_name"
}
```

### `PUT /api/characters/catgirl/{name}/lighting`

更新角色的 VRM 灯光配置。

**请求体：**

```json
{
  "lighting": { ... },
  "apply_runtime": true
}
```

## 语音设置

### `PUT /api/characters/catgirl/voice_id/{name}`

设置角色的 TTS 语音 ID。

**请求体：**

```json
{ "voice_id": "voice-tone-xxxxx" }
```

### `GET /api/characters/catgirl/{name}/voice_mode_status`

检查角色的语音模式可用性。

### `POST /api/characters/catgirl/{name}/unregister_voice`

移除角色的自定义语音。

### `GET /api/characters/voices`

列出当前 API 密钥下所有已注册的语音音色。

### `GET /api/characters/voice_preview`

预览语音（返回音频流）。

**查询参数：** `voice_id`

### `POST /api/characters/clear_voice_ids`

清除所有角色中已保存的语音 ID 记录。

## 麦克风

### `POST /api/characters/set_microphone`

设置输入麦克风设备。

**请求体：**

```json
{ "microphone_id": "device_id" }
```

### `GET /api/characters/get_microphone`

获取当前麦克风设置。
