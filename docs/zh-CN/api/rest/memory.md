# 记忆 API

**前缀：** `/api/memory`

这是主服务器为记忆浏览器和设置页面提供的 API，涵盖近期记忆编辑、记忆功能开关、角色记忆重命名、本地复读洞察、习惯用语避用表管理、外部记忆导入，以及由用户主动触发的遗留存储清理。它不是进程内 [记忆服务器 API](/zh-CN/api/memory-server) 的通用代理。

所有路由都不带末尾斜杠。云存储处于维护态或只读态时，写操作可能返回 `409`。

## 端点一览

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/memory/recent_files` | 列出近期记忆的逻辑文件名 |
| `GET` | `/api/memory/recent_file` | 读取一个近期记忆文件 |
| `POST` | `/api/memory/recent_file/save` | 替换一个角色的近期记忆历史 |
| `POST` | `/api/memory/update_catgirl_name` | 完整角色改名事务的兼容别名 |
| `GET` | `/api/memory/review_config` | 读取近期记忆自动复核开关 |
| `POST` | `/api/memory/review_config` | 更新近期记忆自动复核开关 |
| `GET` | `/api/memory/powerful_memory_config` | 读取强力记忆开关 |
| `POST` | `/api/memory/powerful_memory_config` | 更新强力记忆开关，并执行必要迁移 |
| `GET` | `/api/memory/insight_characters` | 列出允许进行复读洞察分析的角色 |
| `POST` | `/api/memory/repetition_insights` | 对已落盘的助手回复运行纯本地复读分析 |
| `POST` | `/api/memory/repetition_effects/reset` | 清空单个角色的本地防复读效果统计 |
| `GET` | `/api/memory/habitual_phrases` | 读取角色的习惯用语避用表及面板状态 |
| `POST` | `/api/memory/habitual_phrases/override` | 对单条习惯用语设置放行、抑制或清除覆盖决定 |
| `POST` | `/api/memory/habitual_phrases/recompute` | 立即重算单个角色的习惯用语避用表 |
| `POST` | `/api/memory/external_import/preview` | 在不写盘的情况下预览 OpenClaw、Hermes 或 Molili 导入 |
| `POST` | `/api/memory/external_import/commit` | 将预览通过的外部记忆合并写入角色记忆存储 |
| `GET` | `/api/memory/external_import/progress` | 轮询指定角色的外部记忆导入进度计数 |
| `GET` | `/api/memory/farewell-summary` | 只读的记忆摘要，供桌面端退出告别卡使用 |
| `GET` | `/api/memory/legacy/scan` | 只读扫描用户可见的遗留记忆根目录 |
| `POST` | `/api/memory/legacy/purge` | 删除从遗留根目录中明确选中的条目 |

## 近期记忆文件

为兼容旧版记忆浏览器，API 仍使用逻辑文件名 `recent_<角色>.json`。当前存储会把它解析到 `memory/<角色>/recent.json`；迁移期间仍可读取旧的扁平文件。

### `GET /api/memory/recent_files`

无参数。

该路由会搜索活动记忆根目录和项目记忆根目录，去重逻辑文件名并排序返回。

```json
{
  "files": ["recent_小天.json", "recent_小夜.json"]
}
```

### `GET /api/memory/recent_file`

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `filename` | 字符串 | 是 | 例如 `recent_小天.json`；拒绝路径分隔符和 `..` |

`content` 是文件中的 UTF-8 JSON 文本，不是已经解析的消息数组。

```json
{
  "content": "[{\"type\":\"human\",\"data\":{...}}]",
  "fingerprint": "b4c1000000000000000000000000000000000000000000000000000000000000",
  "identity_token": "a7e3111111111111111111111111111111111111111111111111111111111111"
}
```

`fingerprint` 与 `identity_token` 是不透明的快照令牌。客户端编辑内容时必须保留二者，并在对应的保存请求中原样回传。

错误响应为 `{"success": false, "error": "..."}`：文件名非法返回 `400`，逻辑文件无法解析返回 `404`。

### `POST /api/memory/recent_file/save`

替换所选角色的近期历史，并取消该角色正在执行的复核任务，使手动编辑立即生效。

**请求体**

```json
{
  "filename": "recent_小天.json",
  "fingerprint": "b4c1000000000000000000000000000000000000000000000000000000000000",
  "identity_token": "a7e3111111111111111111111111111111111111111111111111111111111111",
  "chat": [
    { "role": "human", "text": "你好！" },
    { "role": "ai", "text": "晚上好！" }
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `filename` | 字符串 | 是 | 必须匹配 `recent_<角色>.json`；角色名从该字段推导 |
| `fingerprint` | 字符串 | 是 | 用于载入本次编辑的 GET 所返回的不透明内容令牌 |
| `identity_token` | 字符串 | 是 | 同一次 GET 所返回的不透明文件身份令牌 |
| `chat` | 数组 | 是 | 替换后的历史，最多 10,000 条 |
| `chat[].role` | 字符串 | 是 | 写入的消息类型，通常为 `human`、`ai` 或 `system` |
| `chat[].text` | 字符串 | 否 | 消息文本；默认为空字符串 |
| `chat[].source_index` | 整数 | 否 | 该条目在 GET 返回数组中的下标，只用于定位 |
| `chat[].theater` | 布尔 | 否 | 标记该条目代表剧场记忆消息；它不会以普通文本写回 |

单条消息文本上限为 32,768 个字符，请求中全部消息文本合计上限为 2,097,152 个字符。聊天条目中的未知字段不会持久化。

剧场记忆消息（小剧场单集胶囊）在这里只读。无论提交的 `chat` 如何表示它们，当前快照里的每条剧场消息都会连同元数据按原位置原样保留，只替换普通消息。若不带 `source_index` 的客户端提交了对不上任何已存消息的条目，同时又有剧场消息没被对上，保存会以 `409` 和 `code: "RECENT_FILE_THEATER_READONLY"` 拒绝。

成功响应：

```json
{
  "success": true,
  "need_refresh": true,
  "catgirl_name": "小天",
  "fingerprint": "d8f2222222222222222222222222222222222222222222222222222222222222",
  "identity_token": "a7e3111111111111111111111111111111111111111111111111111111111111"
}
```

校验失败返回 `400` 和 `success: false`。缺少快照令牌，或文件内容/身份已经并发变化时返回 `409`；冲突响应带有 `code: "RECENT_FILE_CONFLICT"` 和当前令牌。发生冲突后必须重新 GET 文件、合并用户编辑再保存，不能仅替换为响应中的新令牌后重放旧 `chat`。成功响应会返回下一次编辑要使用的令牌。存储失败返回 `success: false` 及 `error`；云存储维护态同样返回 `409`。

## 角色记忆重命名

### `POST /api/memory/update_catgirl_name`

这是规范接口 `POST /api/characters/catgirl/{old_name}/rename` 的兼容别名。它会排空记忆复核/压缩任务、发布角色改名、迁移全部记忆与卡面状态、重载 memory server，并在失败时回滚。若旧客户端在该事务已经提交后重复调用，本接口会以幂等 no-op 成功返回。

```json
{
  "old_name": "旧名字",
  "new_name": "新名字"
}
```

两个字段都是必填字符串。历史 `old_name` 可以包含点号；`new_name` 使用当前角色名规则，且不能是保留路由名。

```json
{
  "success": true,
  "memory_renamed": true,
  "memory_server_reloaded": true
}
```

幂等重复调用返回 `changed: false`、`already_renamed: true` 和 `exists_after`。只有配置已经包含新名称且旧存储已经消失时才允许该 no-op；若配置已发布但旧存储仍残留，接口返回 `409`，不会掩盖部分改名，需通过规范的角色管理接口修复。名称缺失或非法返回 `400`；存储不可写时也可能返回 `409`。

## 记忆功能开关

### `GET /api/memory/review_config`

返回是否启用近期记忆的自动复核和修正。配置不存在时默认为 `true`。

```json
{ "enabled": true }
```

读取会等待正在进行的记忆开关保存结束，再返回落盘值。文件或设置项缺失时默认 `true`。文件不可读、JSON 损坏、配置不是对象或设置值不是布尔值时，返回 HTTP `503` 和 `{"error": "..."}`，不返回 `enabled`。如果仅该设置值无效，响应还包含 `"code": "invalid_memory_setting"`，页面会提供明确的开启/关闭修复选项；合法布尔值 POST 可修复该设置并保留其他字段。

### `POST /api/memory/review_config`

请求体必须是 JSON 对象，且包含必填的布尔字段 `enabled`；JSON 损坏、字段缺失或类型错误返回 HTTP `400` 和 `{"success": false, "error": "..."}`。读取或保存失败返回 `success: false`，不会用新配置覆盖不可读、JSON 损坏或根类型不是对象的配置文件；只有文件不存在时才创建新配置。两个记忆开关的更新在必要迁移和文件保存完成前串行执行。将强力记忆保存为 `false` 时，只有存量值已为布尔 `false` 才跳过迁移；非法旧值按未知状态处理。

```json
{ "enabled": false }
```

该路由把 `recent_memory_auto_review` 写入 `core_config.json`。

```json
{ "success": true, "enabled": false }
```

失败响应为 `{"success": false, "error": "..."}`。存储维护态可能返回 `409`。

### `GET /api/memory/powerful_memory_config`

返回 `powerful_memory_enabled` 设置。已有安装没有显式配置时默认为 `true`。

```json
{ "enabled": true }
```

读取会等待正在进行的记忆开关保存结束，再返回落盘值。文件或设置项缺失时默认 `true`。文件不可读、JSON 损坏、配置不是对象或设置值不是布尔值时，返回 HTTP `503` 和 `{"error": "..."}`，不返回 `enabled`。如果仅该设置值无效，响应还包含 `"code": "invalid_memory_setting"`，页面会提供明确的开启/关闭修复选项；合法布尔值 POST 可修复该设置并保留其他字段。

### `POST /api/memory/powerful_memory_config`

请求体必须是 JSON 对象，且包含必填的布尔字段 `enabled`；JSON 损坏、字段缺失或类型错误返回 HTTP `400` 和 `{"success": false, "error": "..."}`。读取或保存失败返回 `success: false`，不会用新配置覆盖不可读、JSON 损坏或根类型不是对象的配置文件；只有文件不存在时才创建新配置。两个记忆开关的更新在必要迁移和文件保存完成前串行执行。将强力记忆保存为 `false` 时，只有存量值已为布尔 `false` 才跳过迁移；非法旧值按未知状态处理。

```json
{ "enabled": false }
```

强力记忆开关控制证据驱动的 LLM 路径，包括信号分析、晋升时合并、反驳检查、负向目标检查、事实去重和 persona 修正。关闭强力记忆后，轻量反馈路径仍可用。

从 `ON` 切换到 `OFF` 时，会先请求记忆服务器进程重置已确认反思的计时锚点，避免旧的 confirmed 条目被时间驱动的降级路径立即晋升。只有迁移成功后才保存配置。

成功响应：

```json
{ "success": true, "enabled": false }
```

迁移或持久化失败：

```json
{ "success": false, "error": "migration HTTP 409" }
```

## 复读洞察与防复读效果

### `GET /api/memory/insight_characters`

无参数。

返回 `POST /api/memory/repetition_insights` 可接受的已排序角色名列表，合并了已配置角色与磁盘上的真实角色记忆目录（排除符号链接和遗留向量库目录）及近期记忆文件。

```json
{
  "characters": ["小天", "小夜"]
}
```

### `POST /api/memory/repetition_insights`

对已持久化的助手回复运行纯本地复读分析（不调用 LLM，不外发数据）。接口会转发到记忆服务器的 `/internal/memory/{character_name}/repetition_insights`，移除内部 `_anti_repeat_response_ids` 字段，并补充本地防复读 `effectiveness` 与折叠后的 `associations`。

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `character_name` | 字符串 | 是 | — | 目标角色名称 |
| `language` | 字符串 | 是 | — | 分析语言，支持 `en`、`es`、`pt`、`ru`、`ja`、`ko`、`zh-CN`、`zh-TW` |
| `assistant_message_limit` | 整数 | 否 | `100` | 要检查的最近助手回复条数（`3`–`100`） |
| `effect_days` | 整数 | 否 | `30` | 兜底效果窗口天数（`7`、`30` 或 `90`，兼容旧客户端） |

成功返回 `200`，包含 `success: true`、`schema_version`、`artifact_type`、`character_name`、`language`、`parameters`、`summary`、`candidates`、`effectiveness` 和 `associations`。角色名非法、语言不受支持或挖掘失败返回 `422`；角色不存在返回 `404`；记忆服务器不可用或处于写围栏期间返回 `503`。

### `POST /api/memory/repetition_effects/reset`

仅清空指定现有角色的本地防复读效果聚合统计。

**请求体**

```json
{
  "character_name": "小天"
}
```

成功返回 `{"success": true, "character_name": "小天", "cleared": true}`；角色名非法返回 `422`；角色不存在返回 `404`；效果存储不可用返回 `503`。

## 习惯用语避用表

### `GET /api/memory/habitual_phrases`

返回下一轮会话开始时将生效的角色习惯用语避用表面板状态。

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `character_name` | 字符串 | 是 | 目标角色名称 |
| `ui_language` | 字符串 | 否 | 角色没有持久化 prompt 语言时的界面语言兜底 |

成功返回 `200`，包含 `success`、`character_name`、`switches`（`natural_expression`、`review`）、`rules`（`min_sessions`、`session_gap_minutes`、`max_items`、`candidate_pool`、`prompt_max_tokens`、`refresh_interval_hours`、`max_age_days`）、`next_refresh_at`、`computed_at`、`language`、`language_matches`、`stale`、`parameters`、`source`、`review`、`entries`、`injected` 和 `overrides`。名称非法返回 `422`，角色不存在返回 `404`，面板不可用返回 `503`。

### `POST /api/memory/habitual_phrases/override`

对单条习惯用语保存用户覆盖决策（`allow`、`suppress` 或 `clear`），并返回刷新后的面板结构（同 `GET /api/memory/habitual_phrases`）。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `character_name` | 字符串 | 是 | 目标角色名称 |
| `phrase` | 字符串 | 是 | 习惯用语文本（`1`–`200` 字符） |
| `action` | 字符串 | 是 | `"allow"`、`"suppress"` 或 `"clear"` |
| `ui_language` | 字符串 | 否 | 可选界面语言提示（最多 `32` 字符） |

名称或用语非法返回 `422`；角色不存在返回 `404`；恢复/维护模式或存储不可用返回 `503`。

### `POST /api/memory/habitual_phrases/recompute`

通过记忆服务器（130 秒转发超时）立即重算指定角色的习惯用语避用表，并返回刷新后的面板数据。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `character_name` | 字符串 | 是 | 目标角色名称 |
| `ui_language` | 字符串 | 否 | 可选界面语言提示（最多 `32` 字符） |

若该角色正在重算则返回 `409`（`{"success": false, "error": "already recomputing"}`）；名称非法返回 `422`；角色不存在返回 `404`；不可用返回 `503`。

## 外部记忆导入

### `POST /api/memory/external_import/preview`

在不写入记忆文件的情况下解析 OpenClaw、Hermes 或 Molili 的 Markdown 文件或 ZIP 压缩包。请求体受 `EXTERNAL_IMPORT_MAX_REQUEST_BODY_BYTES` 限制。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `character_name` | 字符串 | 是 | 目标角色名称（允许点号，最长 `50` 字符） |
| `source_format` | 字符串 | 否 | `"auto"`（默认）、`"openclaw"`、`"hermes"` 或 `"molili"` |
| `files` | 数组 | 否 | `{path/name, content}` 对象数组；与 `archive_b64` 互斥 |
| `archive_b64` | 字符串 | 否 | Base64 编码的 ZIP 压缩包（可带 `data:` 前缀）；与 `files` 互斥 |

成功返回 `success`、`character_name`、`source_format`、`files`、`counts`（`persona`、`facts`、`daily`）、`candidate_count`、`persona_fusion_calls`、`persona_candidate_tokens`、`daily_extraction_calls`、`daily_candidate_tokens`、`warning_count`、`warnings`（最多 20 条）、`candidates`（最多 100 条）和 `truncated_preview`；Molili 压缩包还会返回 `character_card`、`master_profile`、`skipped` 和 `snapshot_time`。请求体超过 `EXTERNAL_IMPORT_MAX_REQUEST_BODY_BYTES` 时会先被 `InboundBodySizeLimitMiddleware` 直接以 HTTP `413` 拒绝并返回 `{"ok": false, "error_code": "payload_too_large", "max_bytes": ..., "error": ...}`（非 `{success, error}` 格式）。校验或解析失败返回 `400`；未预期异常返回 `500`。

### `POST /api/memory/external_import/commit`

通过 `POST /internal/memory/import_external_markdown`（480 秒转发超时）将已预览的外部记忆合并写入角色记忆存储。

**请求体**

接收与 `preview` 相同的 `character_name`、`source_format` 及 `files` / `archive_b64` 字段，另加：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `acknowledge_warnings` | 布尔 | 条件必填 | 检测到可疑指令警告时必须为 `true`，否则返回 `409` |
| `render_language` | 字符串 | 否 | 仅用于本次渲染的语言兜底，不会持久化为角色语言偏好 |

成功返回 `success: true`、`need_refresh: true`、`memory_server_reloaded: true`、`status: "success"`、`character_name`、`source_format`、`imported_files`、`added_persona`、`added_facts`、`skipped_duplicates` 和 `warning_count`。请求体超过 `EXTERNAL_IMPORT_MAX_REQUEST_BODY_BYTES` 时会先被 `InboundBodySizeLimitMiddleware` 直接以 HTTP `413` 拒绝并返回 `{"ok": false, "error_code": "payload_too_large", "max_bytes": ..., "error": ...}`（非 `{success, error}` 格式）。路由与记忆服务器错误码包括：`400`（请求非法）、`409`（未确认警告或云存储处于维护态）、`413`（`error_code: "external_import_too_large"` 并附带 `partial_import`）、`500`（`error_code: "external_import_partial"` 并附带 `partial_import` 或通用失败）以及 `504`（`error_code: "external_import_timeout"`，表示主服务转发超时但记忆服务器仍在后台执行）。

### `GET /api/memory/external_import/progress`

轮询记忆服务器内存中记录的外部导入进度计数器。

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `character_name` | 字符串 | 是 | 目标角色名称（允许点号，最长 `50` 字符） |

当前记忆服务器进程内尚未为该角色执行过导入时返回 `{"success": true, "active": false}`；导入进行中返回 `{"success": true, "active": true, "stage": "persona"|"daily", "persona_total": ..., "persona_done": ..., "daily_total": ..., "daily_done": ..., "finished": ...}`；导入结束后内存保留该条目，返回 `{"success": true, "active": false, "finished": true, ...}` 及最终阶段与计数。名称非法返回 `400`；记忆服务器返回的非 200 状态码会原样透传，转发异常返回 `502`。

## 告别摘要

### `GET /api/memory/farewell-summary`

当前选中猫娘的只读记忆摘要，显示在桌面端的退出告别卡上。桌面端主进程以回环地址调用，超时 1.2 秒，因此该路由只读本地文件：不调用大模型、不触发同步、不写任何文件。

```json
{
  "memory_count": 128,
  "conversation_count": 342,
  "flashback": {
    "id": "fact_20260314103000_ab12cd34",
    "text": "主人最喜欢下雨天窝在家里看书",
    "date": "2026-03-14"
  }
}
```

| 字段 | 说明 |
|---|---|
| `memory_count` | 本地有效记忆数，与社区铸卡的 `totalMemoryCount` 同口径：`facts.json` 与 `facts_archive.json` 去重，去掉机器日志行。 |
| `conversation_count` | 该猫娘 `time_indexed.db` 中的用户消息数（每轮对话一条）。数据库不存在或读不了时省略。 |
| `flashback` | 一段值得回忆的旧记忆，可以为 `null`。 |
| `flashback.id` | 记忆 id；不是普通字符串的 id 会转成稳定的不透明标记。 |
| `flashback.text` | 记忆原文，合并空白，最多 200 个字符。 |
| `flashback.date` | 记忆发生的本地日期 `YYYY-MM-DD`；没有事件时间时用记录时间。 |

闪回只从可分享的记忆里挑，规则与 `/api/card-drop/facts` 一致：跳过 private、redacted、机器日志行和 `neko_visit` 来源，归档行里带 `subject_archived_at` 或 `arbitration_*` 的也跳过。不满 7 天的记忆不会被选中。其余记忆优先取 importance ≥ 5 的，在最早的四分之一里按 importance 加权随机抽一条，所以每次调用可能不同。

没有选中猫娘时返回 `{"flashback": null}`；出错时省略统计项，不返回错误码。

## 遗留记忆清理

遗留清理是明确的两步操作：先扫描，再提交选中的绝对路径。扫描不会迁移或删除任何数据。

### `GET /api/memory/legacy/scan`

无参数。

响应会标识活动运行时记忆目录之外的候选根目录，并描述每个根目录的直接子项。无法计算大小或超过安全扫描上限时，`size_bytes` 为 `-1`。

```json
{
  "success": true,
  "runtime_memory_dir": "C:\\...\\memory",
  "legacy_roots": [
    {
      "root": "C:\\...\\old-root\\memory",
      "source": "legacy_app_root",
      "exists": true,
      "entries": [
        {
          "name": "小天",
          "path": "C:\\...\\old-root\\memory\\小天",
          "is_dir": true,
          "size_bytes": 12345,
          "is_unlinked": false,
          "runtime_has_same_name": true
        }
      ]
    }
  ],
  "total_entries": 1,
  "total_size_bytes": 12345
}
```

意外的扫描失败返回 `500` 和 `success: false`。

### `POST /api/memory/legacy/purge`

这是破坏性操作。只应提交最近一次扫描作为条目返回的路径。

```json
{
  "paths": [
    "C:\\...\\old-root\\memory\\小天"
  ]
}
```

`paths` 必须是非空的绝对路径数组。每个路径都必须严格位于当前识别出的遗留根目录之下。该路由拒绝相对路径、`..` 段、根目录本身和活动运行时记忆目录。目标不存在会被视为已删除，因此重试是幂等的。

删除按条目尽力执行，因此成功请求可以同时包含 `removed` 和 `errors`：

```json
{
  "success": true,
  "removed": ["C:\\...\\old-root\\memory\\小天"],
  "errors": [
    { "path": "C:\\...\\not-allowed", "error": "..." }
  ]
}
```

请求体非法返回 `400`；没有可识别的遗留根目录返回 `409`；初始化失败返回 `500`。
