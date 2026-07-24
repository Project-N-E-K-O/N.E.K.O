# 云存档 API

云存档路由提供桌面端使用的角色单元同步能力。它由主服务器在 `/api/cloudsave` 下提供，存储后端是当前配置的 Steam Auto Cloud。

> [!CAUTION]
> 上传和下载会改动角色数据。下载可能终止角色的活动会话、释放记忆服务器的数据库句柄、替换本地文件、重新加载角色状态并刷新记忆服务器。应把它们视为需要用户确认的数据管理操作，而不是后台轮询接口。

## 路由清单

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/cloudsave/summary` | 比较本地与云端角色单元，并附带 Steam 创意工坊素材状态 |
| `GET` | `/api/cloudsave/steam-autocloud-config` | 返回同步后端和 Steam Auto Cloud 可用状态 |
| `GET` | `/api/cloudsave/character/{name}` | 返回单个角色的同步详情 |
| `POST` | `/api/cloudsave/character/{name}/upload` | 将一个完整一致的角色单元导出到云存储 |
| `POST` | `/api/cloudsave/character/{name}/download` | 将一个角色单元导入本地并重新加载运行时状态 |

以上路径末尾均没有 `/`。

## 读取接口

`GET /api/cloudsave/summary` 根据当前角色配置生成云存档摘要。响应还包含 `sync_backend`、`steam_autocloud`；如果条目引用 Steam 创意工坊素材，还会带上该素材当前的创意工坊状态。

`GET /api/cloudsave/steam-autocloud-config` 返回：

```json
{
  "success": true,
  "sync_backend": "steam_auto_cloud",
  "steam_autocloud": {}
}
```

`steam_autocloud` 的具体字段反映当前安装状态，并可能随 Steam 可用性变化。

`GET /api/cloudsave/character/{name}` 返回指定角色的本地/云端对比。云端角色不存在时返回 HTTP `404` 和错误码 `CLOUDSAVE_CHARACTER_NOT_FOUND`。

## 上传角色

```http
POST /api/cloudsave/character/Lanlan/upload
Content-Type: application/json

{"overwrite": false}
```

`overwrite` 可选，默认为 `false`，且必须是 JSON 布尔值。成功响应包含 `character_name`、更新后的 `detail`、导出的 `meta`、`sequence_number`、`sync_backend` 和 `steam_autocloud`。

导出对象是角色单元，不只是角色卡，但它**不是完整的记忆目录备份**。当前快照只会复制下列实际存在的白名单平面文件：

```text
recent.json
settings.json
facts.json
facts_archive.json
persona.json
persona_corrections.json
reflections.json
reflections_archive.json
surfaced.json
time_indexed.db
```

快照不包含当前分片归档 `reflection_archive/`、`persona_archive/`，也不包含近期摘要元数据 `recent_meta.json`、恢复状态 `cursors.json`、`outbox.ndjson`、`events.ndjson`、`events_applied.json`，以及 `facts_pending_dedup.json` 等 worker sidecar。这些路径不会上传，也不会在另一台设备上恢复。覆盖前操作备份只用于本地回滚，不会扩大云端覆盖范围。如果云端单元已存在且 `overwrite` 为 false，接口返回 HTTP `409`。

## 下载角色

```http
POST /api/cloudsave/character/Lanlan/download
Content-Type: application/json

{
  "overwrite": true,
  "backup_before_overwrite": true,
  "force": false
}
```

| 字段 | 类型 | 默认值 | 含义 |
|---|---|---:|---|
| `overwrite` | boolean | `false` | 允许替换已存在的本地角色 |
| `backup_before_overwrite` | boolean | `true` | 替换前创建操作备份 |
| `force` | boolean | `false` | 导入前终止活动会话 |

角色存在活动会话且 `force` 不为 true 时，接口返回 HTTP `409`、错误码 `ACTIVE_SESSION_BLOCKED` 和 `can_force: true`。使用 `force: true` 时，服务器会先终止会话并释放记忆服务器句柄，再执行导入。

导入后，服务器会重新加载角色配置，并通知记忆服务器重新加载。如果重新加载失败，服务器会尝试恢复操作备份，并返回 HTTP `500`、错误码 `LOCAL_RELOAD_FAILED_ROLLED_BACK` 和回滚字段。成功响应包含 `detail`、`backup_path`、`sync_backend` 和 `steam_autocloud`。

## 错误

云存档错误使用以下结构，而不是只有 FastAPI 的 `detail`：

```json
{
  "success": false,
  "error": "LOCAL_CHARACTER_EXISTS",
  "code": "LOCAL_CHARACTER_EXISTS",
  "message": "local character already exists: Lanlan",
  "message_key": "cloudsave.error.localCharacterExists",
  "message_params": {},
  "character_name": "Lanlan"
}
```

常见状态码：

| 状态码 | 含义 |
|---:|---|
| `400` | JSON 无效、布尔选项类型错误、名称审核失败或操作被拒绝 |
| `404` | 指定的本地或云端角色不存在 |
| `409` | 目标已存在、存在活动会话或云存档写入栅栏生效 |
| `500` | 上传/下载异常，或重新加载/回滚失败 |
| `503` | 云存档提供方不可用、会话终止失败或记忆句柄无法释放 |

客户端不要只依赖英文 `message`；应按 `code` 分支，并使用 `message_key` 显示本地化文本。
