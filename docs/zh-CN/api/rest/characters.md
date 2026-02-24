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

## Live2D 模型绑定

### `GET /api/characters/current_live2d_model`

获取当前角色的 Live2D 模型信息。

**查询参数：** `catgirl_name`（可选）、`item_id`（可选）

### `PUT /api/characters/catgirl/l2d/{name}`

更新角色的 Live2D 模型绑定。

**请求体：**

```json
{
  "live2d": "model_directory_name",
  "live2d_item_id": "workshop_item_id"
}
```

### `PUT /api/characters/catgirl/{name}/lighting`

更新角色的 VRM 灯光配置。

**请求体：**

```json
{ "brightness": 0.8 }
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

列出可用的 TTS 语音。

**查询参数：** `voice_provider`（可选）— 按提供商筛选。

### `GET /api/characters/voice_preview`

预览语音（返回音频流）。

**查询参数：** `voice_id`、`text`、`provider`

### `POST /api/characters/voices`

添加自定义语音配置。

### `DELETE /api/characters/voices/{voice_id}`

删除自定义语音。

### `POST /api/characters/voice_clone`

从音频样本克隆语音。

**请求体：** 包含音频文件的 `multipart/form-data`。

## 麦克风

### `POST /api/characters/set_microphone`

设置输入麦克风设备。

**请求体：**

```json
{
  "device_name": "Built-in Microphone",
  "device_id": "default"
}
```

### `GET /api/characters/get_microphone`

获取当前麦克风设置。

## 角色卡片

### `GET /api/characters/character-card/list`

列出角色卡片文件。

### `POST /api/characters/character-card/save`

保存角色卡片。

### `POST /api/characters/catgirl/save-to-model-folder`

将角色数据保存到模型文件夹，用于创意工坊发布。
