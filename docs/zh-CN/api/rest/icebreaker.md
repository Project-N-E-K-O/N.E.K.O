# 破冰 API

**前缀：** `/api/icebreaker`

新用户引导（"破冰"）相关端点。破冰是一段引导对话，而非小游戏：它拥有独立的生命周期状态，与游戏 route 生命周期相互隔离。破冰可以追加上下文、播报固定的引导台词，但它绝不会让 `/api/game/route/active` 报告存在一个已打开的小游戏窗口。

::: info
所有写操作端点（`/route/start`、`/route/end`、`/context`、`/choice`、`/speak`）都是本地写操作端点，受与后端其余部分相同的 CSRF / 本地请求校验保护。校验失败返回 `{ "ok": false, "reason": "csrf_validation_failed" }`。
:::

::: info
`lanlan_name` 标识当前角色，在写操作 POST 端点上为**必填**。`/route/start`、`/route/end`、`/speak` 会先从 Body 解析，解析不到时回退到当前选中的角色（`当前猫娘`）；若仍无法解析出角色，则返回 `{ "ok": false, "reason": "missing_lanlan_name" }`。`/context` 更严格：它要求 Body 中必须带非空 `lanlan_name`（不回退），缺失或为空时返回 `{ "ok": false, "reason": "missing_lanlan_name" }`。
:::

## Route 生命周期

### `POST /api/icebreaker/route/start`

为某个角色激活破冰 route，并将其绑定到一个 session。

**Body：**

```json
{
  "lanlan_name": "character_name",
  "session_id": "session-uuid"
}
```

`session_id` 为必填；缺失时返回 `{ "ok": false, "reason": "missing_session_id" }`。

**Response：**

```json
{
  "ok": true,
  "state": {
    "icebreaker_active": true,
    "lanlan_name": "character_name",
    "session_id": "session-uuid",
    "started_at": 0.0,
    "last_activity": 0.0,
    "source": "new_user_icebreaker"
  }
}
```

### `POST /api/icebreaker/route/end`

结束当前激活的破冰 route。

**Body：**

```json
{
  "lanlan_name": "character_name",
  "session_id": "session-uuid",
  "reason": "icebreaker_end"
}
```

`reason` 可选，默认为 `icebreaker_end`。

**Response：** `{ "ok": true, "state": <route 状态> }`。

::: info
若传入了 `session_id` 但与当前激活 route 的 session 不一致（例如第二个标签页开启了更新的 session），则拒绝该请求且不结束 route：

```json
{
  "ok": false,
  "reason": "session_id_mismatch",
  "handled": false,
  "lanlan_name": "character_name",
  "method": "route_end",
  "state": "<route 状态>"
}
```
:::

### `GET /api/icebreaker/route/state`

读取某个角色当前的破冰 route 状态。

**Query：** `lanlan_name` —— 角色名（可选；回退到当前选中的角色）。

**Response：** 公开的 route 状态。无激活 route 时，`state` 为 `{ "icebreaker_active": false }`。

```json
{ "ok": true, "state": { "icebreaker_active": false } }
```

## 引导交互

### `POST /api/icebreaker/context`

向项目 session 历史追加一行引导上下文（user 或 assistant）。该文本同时会被缓存进记忆系统。需要存在一个绑定到相同 session 的激活 route。

**Body：**

```json
{
  "lanlan_name": "character_name",
  "session_id": "session-uuid",
  "role": "assistant",
  "text": "欢迎！我们来帮你完成设置。"
}
```

- `role` 必须为 `assistant` 或 `user`，否则返回 `{ "ok": false, "reason": "invalid_role" }`。
- `text` 为必填（`missing_text`），且上限为 2000 个字符（`invalid_text_length`）。
- 可选的 `request_id`（也接受 `event.request_id`）用于去重。

**Response：**

```json
{
  "ok": true,
  "method": "project_session_history",
  "lanlan_name": "character_name",
  "source": "new_user_icebreaker",
  "session_id": "session-uuid",
  "memory_cached": true
}
```

重复追加会返回相同结构并带 `"deduped": true`。

若无激活 route，则返回：

```json
{
  "ok": false,
  "reason": "route_not_active",
  "lanlan_name": "character_name",
  "source": "new_user_icebreaker",
  "method": "project_session_history"
}
```

若存在激活 route 但传入的 `session_id` 与之不匹配（陈旧或被顶替的 session），则跳过本次追加：

```json
{
  "ok": true,
  "skipped": "stale_session",
  "reason": "session_id_mismatch",
  "handled": false,
  "lanlan_name": "character_name",
  "method": "project_session_history",
  "state": "<route 状态>"
}
```

### `POST /api/icebreaker/choice`

将一次有效的教程选择持久化到持久化选项池中，使其跨 session 留存。需要存在一个绑定到相同 session 的激活 route。

::: info
该端点目前为只写：记录的选择不会进入记忆系统，也不会影响模型。它与 `/context`（供给临时 session 历史）刻意分开，使该选项池保持为一个独立信号，可在后续逐步消费。
:::

**Body：**

```json
{
  "lanlan_name": "character_name",
  "session_id": "session-uuid",
  "day": 1,
  "node_id": "intro",
  "choice": "option_a",
  "label": "多说一点",
  "handoff": false,
  "completed": false,
  "seq": 0
}
```

**Response：** 来自选项池的持久化结果，其中 `source` 被设为 `new_user_icebreaker`。若无激活 route，则返回 `{ "ok": false, "reason": "route_not_active", ... }`。

### `POST /api/icebreaker/speak`

通过项目 TTS 管线播报一行固定的引导台词（并镜像到聊天界面）。需要存在一个绑定到相同 session 的激活 route。

**Body：**

```json
{
  "lanlan_name": "character_name",
  "session_id": "session-uuid",
  "line": "很高兴见到你！",
  "mirror_text": true,
  "emit_turn_end": true,
  "interrupt_audio": false
}
```

- `line` 为必填（`missing_line`）；类 SSML 标签会被剥除，台词截断至 240 个字符。
- `mirror_text` 与 `emit_turn_end` 默认为 `true`；`interrupt_audio` 默认为 `false`。

**Response：** TTS 结果结构，包含 `method: "project_tts"` 以及 `voice_source` 字段：

```json
{
  "ok": true,
  "lanlan_name": "character_name",
  "method": "project_tts",
  "voice_source": { "provider": "project_tts", "method": "project_tts" }
}
```

若无激活 route，则返回：

```json
{
  "ok": false,
  "reason": "route_not_active",
  "lanlan_name": "character_name",
  "source": "new_user_icebreaker",
  "method": "project_tts",
  "audio_sent": false
}
```

若存在激活 route 但传入的 `session_id` 与之不匹配（陈旧或被顶替的 session），则不会播报台词：

```json
{
  "ok": true,
  "skipped": "stale_session",
  "reason": "session_id_mismatch",
  "handled": false,
  "lanlan_name": "character_name",
  "method": "project_tts",
  "state": "<route 状态>",
  "audio_sent": false,
  "audio_committed": false,
  "voice_source": {
    "provider": "project_tts",
    "method": "project_tts",
    "skipped": "stale_session"
  }
}
```
