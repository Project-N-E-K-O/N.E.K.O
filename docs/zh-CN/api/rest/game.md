# 游戏 API

**Prefix:** `/api/game`

应用内小游戏（足球、羽毛球……）的后端。每个小游戏都由一套通用的双人「捧逗」机制驱动：幕后的 **A** 层（一个纯文本 LLM，接收游戏事件并决定台词以及结构化的控制指令）和台前的 **B** 层（把选定的台词通过现有项目输出通道——语音 / TTS / 文字气泡——说出来或镜像出来）。

大多数端点带一个 `{game_type}` 路径参数（如 `soccer`、`badminton`），这样同一组路由无需新增 handler 就能扩展到新游戏。下面的端点按 **Logs**、**Route lifecycle**、**Interaction**、**Leaderboard** 分组。

::: info
部分响应字段和行为是游戏专属的。例如排行榜目前只在羽毛球上实现，`quick-lines` 也只支持 `soccer` 和羽毛球；其它 `game_type` 取值会返回 `ok: false` / 跳过的响应，而不是错误。
:::

## Logs

针对一局小游戏的逐场次诊断日志。日志默认关闭、需手动开启，且有上限（保留的场次数量和每场次的条目数都受限）。

### `GET /api/game/logs`

以 JSON 读取某个游戏场次的诊断日志；不带 `session_id` 时则列出可用的场次。

**Query:** `session_id`、`game_type`、`since`（序号游标）、`limit`（默认 `300`）。

### `GET /api/game/logs/view`

同一份诊断日志的可读 HTML 视图。带 `session_id` 查看单个场次；否则渲染可用场次列表。

**Query:** `session_id`、`game_type`、`limit`（默认 `300`）。

**Response:** 一个 HTML 页面（不是 JSON）。

### `POST /api/game/logs`

为某个会话追加一条诊断日志条目（前端日志写入；经 CSRF 校验）。

**Body:** `session_id`（必填；别名 `sessionId`）、`game_type`（别名 `gameType`，默认 `game`）、`lanlan_name`（别名 `lanlanName`），以及日志条目内容。缺少 `session_id` 返回 `{ "ok": false, "reason": "missing_session_id" }`。

### `POST /api/game/logs/enable`

手动为某个场次开启诊断日志。

**Body:**

```json
{
  "session_id": "round-id",
  "game_type": "soccer",
  "lanlan_name": "character_name"
}
```

**Response:** `{ "ok": true, "session_id": "...", "game_type": "...", "seq": <number> }`；当缺少 `session_id` 或开启失败时返回 `{ "ok": false, "reason": "..." }`。

::: info
这是一个本地写操作端点，会做 CSRF 校验；校验失败返回 `{ "ok": false, "reason": "csrf_validation_failed" }`。
:::

## Route lifecycle

「游戏路由」记录的是游戏窗口打开、主外部输入（文字 / 语音）被劫持进游戏的那段时间。每个角色同一时刻只能有一个活跃路由；启动新路由会顶替掉该角色其它仍活跃的路由。

### `POST /api/game/{game_type}/route/start`

声明游戏窗口已打开，且主窗口输入应被路由进本局游戏。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id"
}
```

**Response:** `{ "ok": true, ... }`，携带公开路由状态；或 `{ "ok": false, "reason": "missing_lanlan_name" }`。

::: info
此处 `game_type` 为 `new_user_icebreaker` 会以 HTTP 400 拒绝——请改用专门的 `/api/icebreaker/route/start` 端点。
:::

### `GET /api/game/{game_type}/route/state`

读取某个角色 + 游戏类型当前的公开路由状态。

**Query:** `lanlan_name`。

**Response:** `{ "ok": true, "state": { ... } }`。

### `GET /api/game/route/active`

让后到的订阅者与当前游戏窗口的路由状态对账（窗口状态变化是边沿触发的，所以一个新加载的 chat/pet 客户端可能错过最初的「opened」事件）。只读，不限定 `game_type`。

**Query:** `lanlan_name`。

**Response:** `{ "ok": true, "active": false }`；活跃时为 `{ "ok": true, "active": true, "game_type": "...", "session_id": "...", "lanlan_name": "..." }`。

### `POST /api/game/{game_type}/route/drain`

排空因游戏页劫持主窗口输入而产生的后端输出。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id"
}
```

**Response:** `{ "ok": true, "outputs": [ ... ], "state": { ... } }`。当没有活跃路由、或 `session_id` 与活跃路由不匹配时，返回空的 `outputs` 列表。

### `POST /api/game/{game_type}/route/voice-transcript`

接收来自独立语音转写（STT）闸门的最终文本，并作为用户输入路由进游戏。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id",
  "transcript": "final recognized text"
}
```

**Response:** `{ "ok": true, "handled": <bool>, "state": { ... } }`；或 `{ "ok": false, "reason": "..." }`（如 `missing_transcript`、`missing_lanlan_name`、`invalid_body`）。当路由不活跃或场次不匹配时，`handled` 为 `false` 并附带 `reason`。

### `POST /api/game/{game_type}/route/heartbeat`

刷新用于检测漏掉退出清理的游戏页心跳，并上报页面可见性。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id"
}
```

**Response:** `{ "ok": true, "active": true, "heartbeat_interval_seconds": <number>, "heartbeat_timeout_seconds": <number>, "state": { ... } }`。找不到匹配路由时返回 `active: false`。

### `POST /api/game/{game_type}/route/end`

结束游戏路由，使用与公开 game-end 端点相同的清理契约。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id",
  "reason": "route_end"
}
```

**Response:** 描述已结束路由的清理结果（适用时含存档 / 赛后信息）。

## Interaction

核心的「捧逗」端点：把事件送给 A 层，再通过 B 层镜像或说出得到的台词。

### `POST /api/game/{game_type}/passive-guard`

运行足球路由使用的受限被动退出分类器。JSON 请求体包含当前 `session_id`、`lanlan_name`，以及前端收集的精简游戏上下文。目前仅实现 `game_type=soccer`。

响应包含 `ok`、`recommendedAction`、`exitPromptType`；成功时还含分类详情。不支持的游戏类型、超时和异常会在应用层信封中安全回退为 `observe_more` 与 `none`，而不是抛 HTTP 错误。

---

### `POST /api/game/{game_type}/chat`

通用游戏 LLM 对话端点。把游戏事件送给幕后 A 层，拿回一句台词加可选的控制指令。

**Body:**

```json
{
  "session_id": "round-id",
  "event": { },
  "lanlan_name": "character_name"
}
```

`event` 是一个游戏自定义的 dict，会原样透传给 LLM。

**Response:**

```json
{
  "line": "the character's line",
  "control": { }
}
```

`control` 携带可选的游戏控制指令（如情绪 / 难度）。当请求体无效、被限流或路由不活跃时，响应会带 `error` 或 `skipped` 字段，且 `line` / `control` 为空。

### `POST /api/game/{game_type}/mirror-assistant`

B 层文字输出：把 A 层台词镜像进普通聊天显示，**不** 调用 TTS。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id",
  "line": "the line to mirror"
}
```

**Response:** `{ "ok": true, "lanlan_name": "...", "method": "project_text_mirror", ... }`；或 `{ "ok": false, "reason": "..." }`（如 `missing_line`、`missing_lanlan_name`、`no_session_manager`）。

### `POST /api/game/{game_type}/speak`

正式 B 层输出：通过现有项目 TTS 管线把 A 层台词说出来。

**Body:**

```json
{
  "lanlan_name": "character_name",
  "session_id": "round-id",
  "line": "the line to speak"
}
```

**Response:** 描述本次发声尝试的结果（含 TTS 管线状态）；或 `{ "ok": false, "reason": "..." }`。

::: info
此处 `game_type` 为 `new_user_icebreaker` 会以 HTTP 400 拒绝——请改用 `/api/icebreaker/speak`。
:::

### `POST /api/game/{game_type}/realtime-context`

把紧凑的游戏上下文注入活跃的 Realtime 语音会话——这是「非语音信息进入 Realtime」的一座刻意做得简单的桥，不要求服务商支持 function-calling。

**Body:** 含 `lanlan_name` 和描述当前上下文的游戏 `state`。

**Response:** `{ "ok": true, ... }`；或 `{ "ok": false, "reason": "..." }`（如 `no_active_realtime_session`、`no_session_manager`）。会做 CSRF 校验。

### `POST /api/game/{game_type}/quick-lines`

进入游戏时生成角色专属的快路径台词。成功时前端用其替换内置快路径台词；失败时保留内置台词。

**Body:**

```json
{
  "session_id": "round-id",
  "lanlan_name": "character_name"
}
```

**Response:** `{ "ok": true, "character": "...", "lines": { }, "missing": [ ] }`。羽毛球命中缓存时会额外带上 `"cached": true`。

::: info
仅支持 `soccer` 和羽毛球。其它 `game_type` 取值返回 `{ "ok": false, "error": "...", "lines": {} }`。
:::

### `GET /api/game/{game_type}/character`

返回当前角色用于游戏内换模型的模型信息。每个小游戏会按自己的渲染支持选择 Live2D、VRM、MMD 或显式回退。

**Query:** `lanlan_name`（可选；默认当前角色）。

**Response:**

```json
{
  "lanlan_name": "character_name",
  "model_type": "live2d",
  "live3d_sub_type": "",
  "live2d_path": "/static/...",
  "mmd_path": "",
  "vrm_path": ""
}
```

### `POST /api/game/{game_type}/end`

结束一局游戏并清理对应的 LLM 会话。

**Body:**

```json
{
  "session_id": "round-id",
  "lanlan_name": "character_name",
  "reason": "game_end"
}
```

**Response:** 描述已结束这局的清理结果（适用时含存档 / 赛后信息）。

## Leaderboard

各游戏的高分排行榜。目前只有羽毛球有存储支撑；其它游戏类型返回空 / 不支持的响应。

### `GET /api/game/{game_type}/leaderboard`

读取排行榜的榜首条目以及调用方的个人最好成绩。

**Query:** `session_id`、`lanlan_name`、`limit`（默认 `10`）、`offset`（默认 `0`）。

**Response:**

```json
{
  "ok": true,
  "top": [ ],
  "total_players": 0,
  "total_scores": 0,
  "limit": 10,
  "offset": 0,
  "has_more": false,
  "your_best": null
}
```

对于不支持的游戏类型，返回相同结构，但 `top` 为空、计数为零。

### `POST /api/game/{game_type}/leaderboard`

向排行榜提交一个分数。

**Body:**

```json
{ "lanlan_name": "character_name", "session_id": "round-id", "mode": "..." }
```

请求体需回带本局的分数总计（如 `finalScore`）；服务端会将其**与自己记录的该 `lanlan_name` / `session_id` / `mode` 会话总分比对**（对局过程中预留）。不匹配，或会话不存在/已过期，返回 `{ "ok": false, "reason": "invalid_session" }`；请求体格式错误返回 `invalid_body`。因此客户端无法提交任意分数：只有服务端为进行中对局实际记录的总分才会被接受。

**Response:** `{ "ok": true, "rank": <number>, "total_players": <number>, "is_personal_best": <bool> }`；或 `{ "ok": false, "reason": "..." }`（如 `invalid_session`、`invalid_body`，或不支持的游戏类型）。
