# 记忆服务器 API

**默认地址：** `http://127.0.0.1:48912`

**配置项：** `MEMORY_SERVER_PORT`

记忆服务器是供主服务器、聊天运行时、主动聊天流程、游戏和内置插件使用的内部环回服务。它不会作为通用代理暴露在主服务器上，其路由也不是稳定的第三方 HTTP 契约。外部集成应优先使用主服务器公开文档中的 API。

独立入口绑定 `127.0.0.1`；启动器也可以在托管运行时中承载同一个 FastAPI 应用。进程组内部使用普通 HTTP 请求。

## 通用约定

- `{lanlan_name}` 是经过 URL 编码的角色名。非法名称返回 `400`。
- 历史写入端点接收对象，其中 `input_history` 字段本身是经过 JSON 序列化的消息数组：

  ```json
  {
    "input_history": "[{\"role\":\"user\",\"content\":\"你好\"}]"
  }
  ```

- 存储位置选择、迁移或恢复期间，服务进入 limited mode。除 `/health`、`/shutdown` 和两个 `/internal/storage/startup/*` 控制端点外，请求都返回 `409`：

  ```json
  {
    "ok": false,
    "error_code": "storage_startup_blocked",
    "blocking_reason": "...",
    "limited_mode": true,
    "error": "..."
  }
  ```

- 部分内部处理器会用 `200` 响应中的 `status: "error"` 或 `ok: false` 表示运行失败。内部调用方必须同时检查 HTTP 状态码和响应体。

## 运行时与生命周期端点

| 方法 | 路径 | 参数 | 响应 |
|---|---|---|---|
| `GET` | `/health` | 无 | `{"app":"N.E.K.O","service":"memory","status":"ok","instance_id":"..."}` |
| `POST` | `/release_character/{lanlan_name}` | 仅路径参数 | 释放该角色的 SQLite 句柄后返回 `{"status":"success","character_name":"..."}`；非法名称保留对应 HTTP 错误状态，其他失败返回 `500` |
| `POST` | `/reload` | 无 | 重建并原子替换记忆组件；返回 `{"status":"success","message":"..."}` 或 `status: "error"` |
| `POST` | `/shutdown` | 无 | 独立进程启用了 shutdown 时返回 `shutdown_signal_received`，否则返回 `shutdown_disabled` |
| `POST` | `/internal/storage/startup/continue` | 可选 `{"reason":"..."}` | 存储就绪后释放 limited mode：`{"ok":true,"initialized":true|false}`；仍受阻时返回 `409` |
| `POST` | `/internal/storage/startup/block` | 可选 `{"reason":"..."}` | 上游启动失败后恢复 limited mode：`{"ok":true,"limited_mode":true,"reason":"..."}` |
| `POST` | `/internal/memory/reset_confirmed_at` | 无 | 强力记忆 `ON` → `OFF` 迁移：`{"ok":true,"count":N}`，或 `{"ok":false,"error":"...","count":0}` |

三个 `/internal/*` 端点是主进程与记忆进程之间的控制面调用，不应暴露为面向用户的管理路由。

## 对话持久化端点

以下四个端点都使用前述 `input_history` 请求结构。

### `POST /cache/{lanlan_name}`

轻量的轮次结束路径：把非空历史追加到 `recent.json`，前台不做压缩；把原始时间序列行写入 `time_indexed.db`；并登记持久化的轮后信号任务。请求路径中不会运行 Stage-1 事实抽取 LLM。

```json
{ "status": "cached", "count": 2 }
```

空消息列表返回 `count: 0`。失败返回 `{"status":"error","message":"..."}`。

### `POST /process/{lanlan_name}`

处理一段新增对话历史：允许常规近期历史压缩、写入原始时间序列行、调度轮后任务，并让历史复核任务经过启动门控。

```json
{ "status": "processed" }
```

失败返回 `{"status":"error","message":"..."}`。

### `POST /renew/{lanlan_name}`

处理会话续接后的第一段新增历史。它在持有角色 settle lock 时执行详细压缩，使 `/new_dialog` 不会读到只完成一半的上下文；其余后台任务与 `/process` 相同。

```json
{ "status": "processed" }
```

失败返回 `{"status":"error","message":"..."}`。

### `POST /settle/{lanlan_name}`

结算已经通过 `/cache` 写入的对话。即使 `input_history` 是空列表，也会执行详细的近期历史结算。如果请求中包含尚未缓存的消息，也会将其写入时间索引并登记轮后处理。

```json
{ "status": "settled" }
```

失败返回 `{"status":"error","message":"..."}`。

## 上下文与召回端点

### `GET /new_dialog/{lanlan_name}`

返回用于新模型会话的 `text/plain` 上下文。对合法角色，它会渲染 persona、活动的 pending/confirmed 反思、动态内心活动、近期历史、聊天间隔提示和节假日上下文。它会等待同角色正在执行的 `/renew` 或 `/settle`。未知角色返回空字符串。

该端点不会执行语义事实召回。语义或时间召回由模型的 `recall_memory` 工具另行通过 `/query_memory` 请求。

### `GET /get_recent_history/{lanlan_name}`

返回本地化、格式化后的历史字符串。未知角色返回本地化的“无历史”字符串。游戏开局上下文使用该端点，它不同于 `/new_dialog` 的完整 prompt 上下文。

### `POST /query_memory/{lanlan_name}`

对活动 facts、活动 reflections 和归档 facts 执行结构化召回。

```json
{
  "query": "用户喜欢什么食物？",
  "time": "2026-05-01/2026-05-07"
}
```

两个字段都是可选字符串，路由规则如下：

| 输入 | 行为 |
|---|---|
| 仅 `query` | BM25 与可选 cosine 召回，再用 reciprocal-rank fusion 融合 |
| `query` 和合法 `time` | 先硬过滤时间窗口，再执行混合语义召回 |
| 仅合法 `time` | 按事实和反思距离目标事件时间窗口的远近排序 |
| 两者都没有 | 返回空 `results` 数组 |
| `time` 非法但有 `query` | 忽略非法时间窗口，回退为纯 query 召回 |

`time` 支持小时（`2026-05-01T14`）、日、月、年，或由 `/`、`..` 分隔的两个 token 组成的闭合端点区间。完整 ISO 时间戳会归入所在小时。

正常响应：

```json
{
  "results": [
    {
      "id": "fact_...",
      "text": "原始记忆文本",
      "tier": "fact",
      "entity": "master",
      "score": 0.032787,
      "created_at": "2026-05-02T10:00:00",
      "event_start_at": "2026-05-01T00:00:00",
      "event_end_at": null
    }
  ],
  "query": "用户喜欢什么食物？",
  "candidates_total": 12,
  "elapsed_ms": 7.4
}
```

`tier` 为 `fact`、`reflection` 或 `fact_archive`。仅时间召回还会带回输入的 `time`，并使用 `score: null`。运行时尚未初始化 fact/reflection 存储时返回 `503`。其他召回失败会降级为成功的空结果，并带 `error_code: "hybrid_recall_failed"`；原始异常细节不会返回。

用户可见的工具调用环路中的混合召回不会再执行 LLM 精排。可选本地 embedding 服务被禁用或尚在预热时，BM25 仍可使用。

### `GET /search_for_memory/{lanlan_name}/{query}` <Badge type="warning" text="已弃用" />

仅为旧调用方保留的兼容端点。它已不执行语义召回，只返回本地化占位文本。新代码必须使用 `POST /query_memory/{lanlan_name}`。

### `GET /get_settings/{lanlan_name}`

以格式化字符串返回渲染后的 persona 和活动反思。persona 数据不可用时回退到旧 settings 渲染器。未知角色返回空 settings 字符串。

### `GET /get_persona/{lanlan_name}`

返回角色完整的内部 persona JSON 对象。当前 memory browser 流程不会调用该路由；它保留给内部或诊断消费者。persona schema 是有版本的内部数据，不是稳定的编辑契约。

### `GET /last_conversation_gap/{lanlan_name}`

```json
{ "gap_seconds": 1820.5 }
```

没有上次对话时返回 `-1`。意外失败返回 `500` 和 `{"gap_seconds":-1,"error":"server_error"}`。

## 反思与主动聊天端点

### `POST /reflect/{lanlan_name}`

请求反思合成，并调度适用的自动晋升路径。当前主动聊天不再在延迟敏感路径中调用该端点；正常生命周期由定期后台合成和晋升循环提供。

```json
{
  "reflection": null,
  "auto_transitions": 0
}
```

有结果时 `reflection` 包含合成结果。由于晋升是 fire-and-forget，`auto_transitions` 始终为 `0`。

### `GET /followup_topics/{lanlan_name}`

返回主动聊天话题候选，但不把它们标记为已展示：

```json
{ "topics": [] }
```

调用方必须把实际使用的 reflection ID 提交给 `/record_surfaced`。

### `POST /record_surfaced/{lanlan_name}`

```json
{ "reflection_ids": ["reflection_..."] }
```

记录主动聊天已展示的反思并刷新冷却时间。列表为空或缺失时是 no-op。稳定响应为 `{"ok":true}`；持久化失败只记录日志，不让调用方失败。

### `POST /cancel_correction/{lanlan_name}`

在可信的手动编辑后，取消正在执行的近期记忆修正任务。

```json
{ "status": "cancelled" }
```

没有正在执行的任务时返回 `{"status":"no_task"}`。

## 证据分析端点

### `GET /api/memory/funnel/{lanlan_name}`

**查询参数**

| 名称 | 类型 | 必填 | 默认值 |
|---|---|---:|---|
| `since` | ISO 8601 时间 | 否 | 当前时间前七天 |
| `until` | ISO 8601 时间 | 否 | 当前时间 |

从角色事件日志返回只读的状态转换计数：

```json
{
  "lanlan_name": "小天",
  "since": "2026-05-01T00:00:00",
  "until": "2026-05-08T00:00:00",
  "counts": {
    "facts_added": 3,
    "reflections_synthesized": 1,
    "reflections_confirmed": 1,
    "reflections_promoted": 0,
    "reflections_merged": 0,
    "reflections_denied": 0,
    "reflections_archived": 0,
    "persona_entries_added": 0,
    "persona_entries_rewritten": 0,
    "persona_entries_archived": 0
  }
}
```

时间非法或 `since > until` 时返回 `400`。

## 存储后端

记忆数据按 `memory/<角色>/` 隔离。主要存储如下：

| 存储 | 用途 |
|---|---|
| `recent.json` 和 `recent_meta.json` | 工作近期历史及其压缩元数据 |
| `time_indexed.db` / `time_indexed_original` | 原始时间序列对话行与时间戳 |
| `facts.json` 和 `facts_archive.json` | 活动抽取事实和较旧的归档事实 |
| `reflections.json` 和 `reflection_archive/` | 活动反思生命周期和分片反思归档 |
| `persona.json`、`persona_corrections.json` 和 `persona_archive/` | 渲染的长期 persona、修正状态和分片归档 |
| `events.ndjson`、`outbox.ndjson` 和 `cursors.json` | 持久化状态转换日志、可重试任务队列和后台循环游标 |
| 其他 sidecar | 展示冷却、合成退避、待处理事实去重及其他可恢复 worker 状态 |

`time_indexed_compressed` 只是兼容表，不再写入新摘要；持久化抽象由 facts、reflections 和 persona entries 表示。`retrieve_summary_by_timeframe` 已弃用且不返回数据。

系统没有独立的 embedding 数据库。可选的本地 CPU ONNX embeddings 会连同文本和模型指纹缓存到活动条目上。向量模型按需加载；模型、运行时、兼容 CPU 路径或最低内存不可用时会自行禁用，召回随后继续使用 BM25。

## 当前模型 tier

模型从配置的 tier 中选择；记忆代码不会写死 provider 模型名。

| 工作 | 当前 tier 或运行时 |
|---|---|
| 用于可选 cosine 召回和去重候选的本地文本 embeddings | `data/embedding_models/<profile>/` 中打包的 CPU ONNX profile；不使用 API 模型 tier |
| 近期历史压缩、事实抽取、证据信号检测、反思合成与反馈检查、事实去重决策，以及内部 LLM recall 重排 | `summary` |
| 近期历史复核、persona 修正/精炼、reflection 精炼，以及 reflection promotion merge | `correction` |
| 负向关键词目标分类 | `emotion` |
| 面向用户的 `POST /query_memory` 融合 | 不使用 LLM tier；BM25 + 可选 cosine + reciprocal-rank fusion |

这些是当前实现默认值，不是 API 保证。运维者为每个 tier 配置底层模型、base URL 和凭据。

## 主要调用方

- `main_logic/cross_server.py` 驱动 `/cache`、`/process`、`/renew` 和 `/settle`。
- 聊天生命周期和内置渠道读取 `/new_dialog`；模型工具处理器调用 `/query_memory`。
- 主动聊天读取 `/followup_topics`，并通过 `/record_surfaced` 记录实际使用的反思。
- 角色管理使用 `/reload` 和 `/release_character`；公开记忆浏览器手动编辑近期历史后使用 `/cancel_correction`。
- 主服务器负责存储启动控制、shutdown 和强力记忆迁移调用。
