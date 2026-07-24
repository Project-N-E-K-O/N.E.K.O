# Steam Auto-Cloud 云存档

> **文档性质：current implementation record。** 本页记录当前 `cloud_archive` staging、角色快照和 Steam bundle 同步合同，不是旧版优化计划。Steam 客户端最终是否完成跨设备同步仍需在真实 Steam 会话中验证。

## 架构

云存档不是把运行目录直接交给 Steam。项目先把允许同步的数据导出为受控 staging snapshot，再由 Steam Auto-Cloud 或 bundle helper 同步；下载后先校验并导入回运行时。

当前入口：

- `main_routers/cloudsave_router.py`：`/api/cloudsave` summary、角色 upload/download 与 Auto-Cloud 状态；
- `utils/cloudsave_runtime/`：快照构建、staging、导入/导出、绑定和回滚；
- `utils/cloudsave_autocloud.py`：Auto-Cloud 生命周期与状态；
- `utils/steam_cloud_bundle.py`：远端 bundle 上传/下载；
- `static/js/cloudsave_manager.js`：管理页面；
- `launcher.py` 与启动流程：启动前导入/迁移的受控入口。

## 稳定合同

- 同步边界由快照构建器的固定白名单决定，不能递归复制整个数据目录。当前记忆白名单是 `recent.json`、`settings.json`、`facts.json`、`facts_archive.json`、`persona.json`、`persona_corrections.json`、`reflections.json`、`reflections_archive.json`、`surfaced.json` 和 `time_indexed.db`。
- 当前快照不覆盖 `reflection_archive/`、`persona_archive/`、`recent_meta.json`、`cursors.json`、`outbox.ndjson`、`events.ndjson`、`events_applied.json` 或 `facts_pending_dedup.json` 等 worker sidecar；因此“角色快照”不能解释成完整记忆备份。
- staging snapshot、运行时真相和 Steam 远端是三种状态；页面“本地导出成功”不等于远端上传成功。
- manifest 包含 sequence、导出时间、文件清单和完整性信息；导入前必须验证。
- 导入先备份将被覆盖的目标，失败时回滚，不能留下半应用状态。
- 单角色操作与全量快照的角色范围不同，但两者的记忆文件都受同一白名单约束；“全量”不能解释为递归包含每个角色的全部记忆状态。UI 和 API 必须明确显示这一边界。
- Workshop 来源和本地覆盖信息要随角色绑定保留，不能把缺失订阅误判成普通本地模型。
- Steamworks 不可用、未登录或不是 Steam 跟踪启动时，返回可诊断状态并保持本地数据可用。

## 状态解读

`has_snapshot` 只表示 staging 有可读取快照；`steam_session_ready` 还要求 Steamworks 可用、Steam 正在运行、用户已登录且启动可关联。`remote_bundle_result.success` 才是本次远端动作结果。跨设备可用必须在另一设备完成下载、校验和导入后确认。

## 验证

```bash
uv run pytest tests/unit/test_cloudsave_runtime.py tests/unit/test_cloudsave_router.py tests/unit/test_cloudsave_autocloud.py tests/unit/test_cloudsave_autocloud_router.py tests/unit/test_cloudsave_lifecycle_flow.py tests/unit/test_steam_cloud_bundle_i18n_names.py -q
```

涉及真实 Steam 的检查不能由单元测试替代。测试数据必须使用临时目录，不能读写用户实际存档。

## 修改规则

- 新增同步文件先更新白名单、manifest 和回滚测试；
- 不把 cache、日志、密钥、临时文件或绝对路径加入 snapshot；
- 状态字段变更同步 API、前端和启动流程；
- source 与 frozen/bundled helper 的改动保持对偶，避免只修开发环境。
