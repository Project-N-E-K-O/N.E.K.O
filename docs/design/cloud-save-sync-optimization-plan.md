# Steam Auto-Cloud 云存档基线与维护规范（cloud_archive）

> 这份文档是云存档后续迭代的参考基线。
> 目标是让每次修改都先对齐真实行为，再决定要不要改代码。

---

## 1. 当前目标与硬约束

### 1.1 目标

- 运行时真源始终是本地应用数据目录（不是 Steam 远端目录）。
- Steam Auto-Cloud 只负责同步本机 `cloudsave/` 快照层。
- 启动时可在安全条件下把快照应用回运行时。
- 运行中由用户手动确认上传/下载（单角色）。

### 1.2 硬约束（不能破）

- 不把 Steam Cloud 当运行时实时目录。
- 不做“全目录直接同步”。
- 不在 shutdown 自动把运行时重写回 `cloudsave/`。
- 非 Steam 功能不能被 Steam 登录状态绑死。
- bundle helper 仅是 source 联调辅助，不是打包版主链路。

---

## 2. 一句话架构

`Steam Auto-Cloud <-> 本机 cloudsave/（快照层） <-> 本机运行时真源`

- Steam 只同步 `cloudsave/`。
- 应用负责启动导入判定、单角色上传下载、回滚与一致性校验。

---

## 3. 同步边界（以代码常量为准）

### 3.1 受控快照路径

`utils/cloudsave_runtime.py` 中 `MANAGED_CLOUDSAVE_PREFIXES` 当前为：

- `characters/`
- `catalog/`
- `profiles/`
- `bindings/`
- `memory/`
- `overrides/`
- `meta/`

另有 `manifest.json`（清单入口文件）。

### 3.2 memory 白名单

`memory/<角色名>/` 仅以下文件会被导出/导入：

- `recent.json`
- `settings.json`
- `facts.json`
- `facts_archive.json`
- `persona.json`
- `persona_corrections.json`
- `reflections.json`
- `reflections_archive.json`
- `surfaced.json`
- `time_indexed.db`

### 3.3 明确不进云快照的内容

- 本地状态文件（不进云快照）：`state/cloudsave_local_state.json`、`state/character_tombstones.json`、`state/root_state.json`。
- 资产目录与大文件：`live2d/`、`vrm/`、`mmd/`、`workshop/`、`plugins/`、模型本体。
- 敏感信息：API Key/Cookie/Token、本机绝对路径提示等。
- 非白名单 memory 文件：例如 `custom_notes.json`、`embedding_cache.bin`、`.DS_Store`。

非白名单示例用途说明：

- `custom_notes.json`：本机临时/实验性扩展字段，结构不受主 schema 约束。
- `embedding_cache.bin`：本地性能缓存，设备相关且可再生成。
- `.DS_Store`：系统目录元数据，无业务语义。

---

## 4. 生命周期真实行为

### 4.1 启动 phase-0（launcher）

执行顺序：

1. `bootstrap_local_cloudsave_environment()`。
2. `CloudSaveManager.import_if_needed(reason="launcher_phase0_prelaunch_import")`。
3. root mode 切回 `normal`。
4. 发送 `cloudsave_bootstrap_ready` 事件。

事件脱敏契约：`import_result` 只允许：

- `success`
- `action`
- `requested_reason`

### 4.2 main_server 直启兜底

- startup 会再做一次 `bootstrap + import_if_needed(reason="main_server_startup")`。
- 若导入生效，会触发 memory_server 对齐重载。

### 4.3 自动导入判定

`startup_import_required = has_snapshot && !runtime_has_user_content && snapshot_differs_from_runtime`

- 运行时已有用户内容且快照更新时，不自动覆盖。
- 该场景返回 `manual_download_required`，等待用户手动下载应用。

### 4.4 运行中上传/下载语义

- 上传（单角色）：`export_cloudsave_character_unit`，只更新本机 `cloudsave/` 的该角色快照单元。
- 下载（单角色）：`import_cloudsave_character_unit`，从本机快照应用到运行时。
- 下载前有活跃会话保护，必要时先释放 memory 句柄。
- 下载时若遇到活跃会话（409 `ACTIVE_SESSION_BLOCKED`），支持前端带 `force: true` 参数强制终止会话后继续下载。强制流程：终止 AI 会话（`disconnected_by_server`）→ 将 `rs.session_manager` 置 `None` 阻断自动重连 → 释放 memory server SQLite 句柄 → 执行下载覆盖。

### 4.5 单角色与全量快照差异

单角色上传包含：

- `profiles/characters.json` 中目标角色更新。
- `bindings/<角色名>.json`。
- `memory/<角色名>/` 白名单文件。
- `characters/<角色名>/...` 与 `meta/<角色名>.json`。
- `catalog/catgirls_index.json`、`catalog/character_tombstones.json` 对应更新。

单角色上传不包含：

- 模型本体文件。
- 非白名单 memory 文件。
- `profiles/conversation_settings.json`。
- `catalog/current_character.json`。

全量导出 `export_local_cloudsave_snapshot` 会额外维护：

- `profiles/conversation_settings.json`
- `catalog/current_character.json`
- `catalog/character_tombstones.json`

### 4.6 shutdown 当前行为（main_server）

顺序：

1. 释放后台资源。
2. 遍历角色执行 `release_memory_server_character(...)`；若任一角色释放失败（返回 `False` 或抛错），会记录告警并跳过本次远端上传步骤，避免上传可能 stale/incomplete 的快照。
3. 仅当第 2 步全部成功时，调用 `upload_existing_snapshot`，预算 5 秒，尝试上传“已有 staged snapshot”。
4. 按 `shutdown_memory_server_on_exit` 决定是否请求关闭 memory_server。

约束：

- shutdown 不自动 `export_snapshot`。
- shutdown 第 3 步是“上传已有快照”，不是“重写快照”；若第 2 步失败则该上传步骤会被跳过。

---

## 5. “页面成功”与“远端成功”的判定口径

### 5.1 关键语义

`CloudSaveManager.export_snapshot()` 的顶层结果当前语义是：

- `success: true` + `action: "exported"` 代表本地快照导出成功。
- Steam 远端上传是否成功，需看 `remote_bundle_result`。

这就是“本机云存档管理页看起来正常，但另一台设备没有新快照”的核心风险点。

### 5.2 什么时候可判定跨设备可用

至少满足：

1. 本机已生成目标快照（manifest 序列号/时间更新）。
2. 远端上传动作返回成功（或在 Steam 客户端确认云同步完成）。
3. 另一设备先把 `cloudsave/` 拉到本地。
4. 另一设备再执行手动下载/应用快照。

---

## 6. source/frozen 与 bundle helper 边界

- `download_cloudsave_bundle_from_steam()` / `upload_cloudsave_bundle_to_steam()` 在非 source launch 下直接返回 `reason = "not_source_launch"`。
- source launch 且平台支持时，才走 RemoteStorage bundle helper。
- 打包运行主路径仍然是 Steam Auto-Cloud 同步 `cloudsave/`。

---

## 7. 平台路径口径

运行时根目录：

- Windows：`%LOCALAPPDATA%/N.E.K.O/`
- macOS：`~/Library/Application Support/N.E.K.O/`
- Linux：`$XDG_DATA_HOME/N.E.K.O/`（未设置时 `~/.local/share/N.E.K.O/`）

`cloudsave/` 目录：

- 三平台统一为运行时根目录下 `cloudsave/`。

Steam Auto-Cloud 推荐规则：

- primary: `WinAppDataLocal` + `N.E.K.O/cloudsave`
- macOS override: `MacAppSupport` + `N.E.K.O/cloudsave`
- Linux override: `LinuxXdgDataHome` + `N.E.K.O/cloudsave`

---

## 8. 前后端状态契约

`/api/cloudsave/summary` 与配置接口应稳定提供：

- `sync_backend = "steam_auto_cloud"`
- `steam_autocloud.has_snapshot`
- `steam_autocloud.snapshot_sequence_number`
- `steam_autocloud.snapshot_exported_at_utc`
- `steam_autocloud.startup_import_required`
- `steam_autocloud.manual_download_required`
- `steam_autocloud.source_launch`
- `steam_autocloud.steam_session_ready`
- `steam_autocloud.steam_available / steam_running / steam_logged_on`
- `steam_autocloud.recommended_paths`

---

## 9. 当前最小回归测试集（精简后）

- `tests/unit/test_cloudsave_runtime.py`
- `tests/unit/test_cloudsave_router.py`
- `tests/unit/test_cloudsave_pages.py`
- `tests/unit/test_cloudsave_config_manager.py`
- `tests/unit/test_cloudsave_autocloud.py`
- `tests/unit/test_cloudsave_autocloud_router.py`
- `tests/unit/test_cloudsave_startup_flow.py`
- `tests/unit/test_cloudsave_lifecycle_flow.py`
- `tests/unit/test_cloudsave_i18n.py`
- `tests/unit/test_character_memory_regression.py`
- `tests/unit/test_steam_cloud_bundle_i18n_names.py`
- `tests/unit/test_steamworks_loader_paths.py`

推荐命令：

```bash
uv run pytest -q \
  tests/unit/test_cloudsave_runtime.py \
  tests/unit/test_cloudsave_router.py \
  tests/unit/test_cloudsave_pages.py \
  tests/unit/test_cloudsave_config_manager.py \
  tests/unit/test_cloudsave_autocloud.py \
  tests/unit/test_cloudsave_autocloud_router.py \
  tests/unit/test_cloudsave_startup_flow.py \
  tests/unit/test_cloudsave_lifecycle_flow.py \
  tests/unit/test_cloudsave_i18n.py \
  tests/unit/test_character_memory_regression.py \
  tests/unit/test_steam_cloud_bundle_i18n_names.py \
  tests/unit/test_steamworks_loader_paths.py
```

---

## 10. 维护规则

### 10.1 触发文档必更条件

改到以下任一模块时，PR 必须同步更新本文：

- `utils/cloudsave_runtime.py`
- `utils/cloudsave_autocloud.py`
- `utils/steam_cloud_bundle.py`
- `main_routers/cloudsave_router.py`
- `launcher.py`
- `main_server.py`
- `static/js/cloudsave_manager.js`
- `static/js/chara_manager.js`（涉及 cloudsave）

### 10.2 提交前检查

1. 不破坏硬约束（尤其 shutdown 不自动导出）。
2. 不破坏同步边界（白名单变更需同步文档+测试）。
3. 不破坏 source/frozen 门控（`not_source_launch` 语义一致）。
4. 最小回归测试集通过。
5. 本文已更新对应章节。

### 10.3 提交说明模板

- 标题：`fix(cloudsave): <行为变化摘要>`
- 描述建议至少包含 4 项：
1. 改了什么（行为层）。
2. 为什么改（风险/回归来源）。
3. 如何验证（自动化 + 人工链路）。
4. 本文更新了哪些章节。
