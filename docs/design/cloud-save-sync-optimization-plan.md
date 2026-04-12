# Steam Auto-Cloud 云存档收口记录与现状计划

> 本文基于 `cloud_archive` 分支从首轮云端同步设计提交到当前代码状态整理。
> 目标不是保留历史设想，而是把“现在代码真实如何工作、平台边界在哪、还剩哪些风险”写清楚。
> 若本文与当前代码冲突，以当前代码为准。

---

## 1. 文档范围

- 覆盖范围：
  - `db687345`、`ceb913a7` 起始设计约束
  - `64033ab4` 到 `1dc2c0f7` 的本地快照、启动导入、退出导出、回滚和路径收口
  - `f6957a25` 的 source 模式 Steamworks 路径修正
  - `0e1f2bbc81685a3f21b0f2e4d91514d50b104a3b` 的 Steam RemoteStorage bundle 辅助链路与 CFA fallback 收口
  - 当前工作树里已经落地、但未必体现在旧设计文档中的启动/关闭顺序、平台门控和性能收口
- 本文讨论的是：
  - Steam Auto-Cloud
  - 本地 `cloudsave/` 快照层
  - 运行时真源目录
  - 启动、关闭、平台路径、source/frozen 分支、状态接口和 UI 口径
- 本文不再沿用：
  - 自建 HTTP provider 作为主方案
  - 把 Steam Cloud 当运行目录
  - “全目录直接同步”方案

---

## 2. 分支演进结论

### 2.1 从第一版设计到当前实现的主线

1. `db687345`、`ceb913a7`
   - 明确把方案收敛到“本地快照层 + 自动导入/导出”，而不是继续扩展自建远端 provider。
2. `64033ab4`、`d48ff1fc`、`7a044555`
   - 把 `cloudsave_runtime`、`manifest.json`、`cloudsave_local_state.json`、tombstone、SQLite shadow copy、`CloudSaveManager` 等核心闭环真正接入代码。
3. `4e00c6a6`、`7e357b6e`、`1e48caf2`、`4c7de26c`
   - 补强回滚安全、单角色流程、状态接口、前端语义，并把旧设计文档压缩到更贴近现实现状的口径。
4. `2663de38`、`f837c0df`、`1dc2c0f7`
   - 继续加固启动/关闭顺序、只读/维护态、preferences 与 packaged 资源路径，避免“表面上导入导出了，实际服务状态不一致”。
5. `f6957a25`
   - 修正 source 模式 Steamworks 根路径，避免被 `cwd` 干扰。
6. `0e1f2bbc81685a3f21b0f2e4d91514d50b104a3b`
   - 新增 Windows source 专用的 Steam RemoteStorage bundle 辅助链路，同时继续收紧 `ConfigManager` 的标准应用数据目录与 CFA fallback。

### 2.2 当前最终方向

- 运行时真源始终是本地应用数据目录。
- Steam Auto-Cloud 只同步本地 `cloudsave/` 快照目录。
- 启动时优先把本机已落地的 `cloudsave/` 应用回运行时。
- 退出时把运行时提炼成新的 `cloudsave/` 快照。
- 单角色“上传 / 下载”体验继续保留，但底层同步单位已经是受控的整体快照树。
- Windows source 额外保留了一条 Steam RemoteStorage bundle 辅助链路，用于源码调试和兜底验证；它不是 Windows Steam 打包版的主路径，也不会在 Linux/macOS 上启用。

---

## 3. 当前真实架构

### 3.1 三层结构

1. Steam Cloud
   - 由 Steam Auto-Cloud 托管。
   - 目标是同步本地 `cloudsave/`。
2. 本地 `cloudsave/` 快照层
   - 面向 Steam 同步的中间层。
   - 只放安全、可迁移、可校验的快照文件。
3. 本地运行时真源
   - 业务真实读写目录。
   - 高频写入和内存服务实际占用都发生在这里。

### 3.2 快照边界

- 当前快照层由 `utils/cloudsave_runtime.py` 管理。
- 受控内容包括：
  - `manifest.json`
  - `catalog/`
  - `profiles/`
  - `bindings/`
  - `memory/`
  - `meta/`
  - `overrides/`
- 明确不进入快照层的内容包括：
  - 本地状态文件 `cloudsave_local_state.json`、`character_tombstones.json`（位于 `app_docs_dir/state/`，不属于 Steam Auto-Cloud 快照层，必须保持本地，不同步 `client_id`、`last_applied_manifest_fingerprint` 等设备相关元数据）
  - `live2d/`
  - `vrm/`
  - `mmd/`
  - `workshop/`
  - `plugins/`
  - 大体积模型本体
  - API Key、Cookie、Token、provider 地址等敏感配置
  - 设备绝对路径和窗口布局类配置

### 3.3 单角色 API 与整体快照的关系

- 前端和 `cloudsave_router` 仍提供单角色上传/下载语义。
- 但底层并不是“只同步一个独立远端对象”。
- 当前实现里，单角色导出/导入仍会更新受控快照树中的共享文件。
- 例如单角色导出仍可能 stage / 重写合并后的 `profiles/characters.json`。

---

## 4. 当前代码基线

### 4.1 关键模块

- `utils/cloudsave_runtime.py`
  - 负责本地快照导出/导入、manifest、local state、tombstone、SQLite shadow copy、原子写和运行时内容判断。
- `utils/cloudsave_autocloud.py`
  - 负责 `CloudSaveManager.build_status()`、`import_if_needed()`、`export_snapshot()`。
- `utils/steam_cloud_bundle.py`
  - 负责 Windows source 专用的 Steam RemoteStorage bundle 下载/上传辅助链路。
- `main_routers/cloudsave_router.py`
  - 负责 `/api/cloudsave/summary`
  - `GET /api/cloudsave/steam-autocloud-config`
  - 单角色上传/下载 API
- `launcher.py`
  - 负责 phase-0 cloudsave bootstrap/import、进程模式选择、服务启动、优雅关闭和顺序收口。
- `main_server.py`
  - 负责直启兜底导入、启动后 memory_server 对齐、退出前导出和延后关闭 memory_server。

### 4.2 当前状态接口口径

- `/api/cloudsave/summary` 和单角色详情都会返回：
  - `sync_backend = "steam_auto_cloud"`
  - `steam_autocloud = {...}`
- `/api/cloudsave/steam-autocloud-config` 直接返回当前平台下的：
  - `runtime_root`
  - `cloudsave_root`
  - `manifest_path`
  - `manifest_exists`
  - `recommended_paths`
  - `current_platform_rule`
  - Steam 在线状态
- 前端 summary 状态文案当前先检查 `provider_available`，只有 provider 可用时才继续区分 Steam Ready / Offline。

---

## 5. 启动闭环

### 5.1 launcher 的 phase-0

当前启动主路径是：

1. `launcher` 在拉起任何服务前先执行 `_prepare_cloudsave_runtime_for_launch()`。
2. 在 `cloud_apply_fence(..., ROOT_MODE_BOOTSTRAP_IMPORTING, ...)` 下先执行 `bootstrap_local_cloudsave_environment()`。
3. 然后调用 `CloudSaveManager.import_if_needed(reason="launcher_phase0_prelaunch_import")`。
4. phase-0 完成后，立刻把 root state 切回 `ROOT_MODE_NORMAL`。
5. 通过 `cloudsave_bootstrap_ready` 事件通知前端 phase-0 已完成。
6. 然后才继续启动 `main_server`、`memory_server`、`agent_server`。

当前 `cloudsave_bootstrap_ready` 事件只暴露非敏感字段：

- `root_state`
- `manifest_name`
- `manifest_exists`
- `import_result`

不会再把本机绝对 `manifest_path` 直接塞进事件负载。

### 5.2 launcher 的模式选择

- `launcher` 当前默认策略：
  - source 模式默认多进程
  - frozen 模式默认 merged mode
- 可通过 `NEKO_MERGED=1/0` 强制覆盖。
- 这个调整不是纯设计项，而是已经落地的启动性能收口：
  - 开发模式保留多进程，方便调试
  - 打包版默认合并，减少内存峰值和冷启动开销

### 5.3 直接启动 main_server 的兜底链路

若绕过 `launcher` 直接启动 `main_server`：

1. `main_server` startup 仍会先执行 `bootstrap_local_cloudsave_environment()`。
2. 然后在 10 秒预算内执行 `CloudSaveManager.import_if_needed(reason="main_server_startup")`。
3. 不论导入是否发生，都会先尝试把 root state 切回 `ROOT_MODE_NORMAL`。
4. 然后才执行 `initialize_character_data()`。
5. 若这次兜底真的做了导入，还会调用 `_sync_memory_server_after_startup_import()` 通知 memory_server reload。

这一步是当前实现里的重要收口：

- 先恢复可写 root mode，再初始化角色数据，避免在 cloudsave import 成功后仍被维护态/只读态拦住。
- 若 main_server 直启时自己做了导入，memory_server 也会被显式同步，避免主服务和记忆服务看到不同状态。

### 5.4 当前导入判定逻辑

`CloudSaveManager.build_status()` 当前把 `startup_import_required` 计算为：

```python
startup_import_required = bool(
    has_snapshot
    and (
        not runtime_has_user_content
        or not last_applied_manifest_fingerprint
        or not manifest_fingerprint
        or manifest_fingerprint != last_applied_manifest_fingerprint
    )
)
```

这代表当前真实行为是：

- 没有快照时跳过导入。
- 有快照且运行时为空时导入。
- 有快照但 `last_applied_manifest_fingerprint` 为空时也导入。
- 有快照但 manifest 指纹为空时也导入。
- 有快照且指纹不一致时也导入。

也就是说，当前实现仍然是“只要存在快照且指纹未确认对齐，就倾向执行导入”，并不是更保守的“运行时已有用户内容就绝不自动导入”版本。

### 5.5 Windows source 的启动前 bundle 下载

`CloudSaveManager.import_if_needed()` 在判断是否导入本地快照前，会先调用 `_try_download_remote_bundle()`：

- Windows source 启动时：
  - 可能先从 Steam RemoteStorage 读取 bundle，覆盖本地 `cloudsave/`
  - 然后再按 manifest/status 判断是否把 `cloudsave/` 应用回运行时
- Windows frozen / Steam 打包版：
  - 这条链路直接返回 `reason = "not_source_launch"`
- Linux/macOS：
  - 这条链路直接返回 `reason = "unsupported_platform"`

因此当前主路径仍是：

- Steam Auto-Cloud 同步本地 `cloudsave/`
- phase-0 再把本地 `cloudsave/` 应用到运行时

而不是所有平台都走 `ISteamRemoteStorage` 直连。

---

## 6. 运行中闭环

### 6.1 运行时真源不变

- 业务读写仍直接发生在运行时目录。
- Steam 不参与运行中的高频写入。
- `cloudsave/` 只是受控快照层，不是实时运行目录。

### 6.2 前端“上传 / 下载”的真实语义

- 上传：
  - 立即更新本地 `cloudsave/` 快照
  - 不等于立刻上传到 Steam 远端
- 下载：
  - 把当前设备已经存在的快照应用回运行时
  - 不等于绕过 Steam 直接实时从远端抓取

### 6.3 运行中安全措施

- 单角色下载前会检查活跃会话，避免正在使用中的角色被覆盖。
- 角色下载会尝试释放 memory_server 句柄，再做磁盘替换和 reload。
- 下载后会重新初始化角色数据，并通知 memory_server reload。
- 回滚失败、memory_server reload 失败等情况当前也会带回错误信息，不再静默吞掉。

---

## 7. 退出闭环

### 7.1 main_server 退出顺序

当前 `main_server` shutdown 主链路是：

1. 清理后台预加载、翻译服务、token tracker、音乐爬虫等资源。
2. 遍历可释放的角色，逐个调用 `release_memory_server_character(...)`，每个角色 1 秒超时。
3. 在 3 秒预算内执行 `CloudSaveManager.export_snapshot(reason="main_server_shutdown")`。
4. 导出完成后，如启动配置要求关闭 memory_server，再显式调用 `_request_memory_server_shutdown()`。

这里的关键变化是：

- 不再让 memory_server 先于主导出流程结束。
- 先导出 cloudsave，再请求 memory_server 退出，避免把数据库句柄和导出顺序打乱。

### 7.2 launcher 的总关闭顺序

`launcher.cleanup_servers()` 当前按固定顺序关停：

1. `main_server`
2. `memory_server`
3. `agent_server`

这样做是为了让：

- `main_server` 的 shutdown 导出链路先执行完
- memory_server 不会因为被过早杀掉而让主服务的收尾行为失序

在 POSIX 平台上，child process 还会先脱离 launcher 的 Ctrl+C 进程组，以避免终端 SIGINT 同时打到所有服务，导致 memory_server 抢先退出。

### 7.3 Steam 观察到的最终行为

- 本地 `cloudsave/` 会在应用退出前被更新。
- Steam 在应用结束后观察到 `cloudsave/` 变化，再执行 Auto-Cloud 上传。
- Windows source 若启用 bundle helper，还会在 `export_snapshot()` 后尝试同步 RemoteStorage bundle；但这只是 source 调试辅助，不改变打包版主路径。

---

## 8. 平台与路径口径

### 8.1 统一根目录语义

当前仓库里，以下模块都已经收口为“源码基于 `__file__`，打包基于 `sys.executable` / `sys._MEIPASS`”，不再依赖 `cwd`：

- `utils/config_manager.py`
- `utils/api_config_loader.py`
- `utils/logger_config.py`
- `utils/cloudsave_autocloud.py`
- `steamworks/__init__.py`

这意味着以下内容现在共用同一套 app root 语义：

- `steam_appid.txt`
- Steamworks 动态库
- 项目内 `static/`、`templates/`、`config/`、`memory/store/`
- cloudsave phase-0 bootstrap

`cwd` 当前只保留为：

- 历史数据迁移候选
- 所有标准应用数据目录都失败时的最终 fallback

### 8.2 Windows 源码模式

- 运行时根目录优先：
  - `%LOCALAPPDATA%/N.E.K.O/`
- `cloudsave/`：
  - `%LOCALAPPDATA%/N.E.K.O/cloudsave/`
- launcher 默认：
  - 多进程模式
- 额外能力：
  - 允许 Windows source 进入 Steam RemoteStorage bundle 下载/上传辅助链路
  - `.py` 和 `.pyw` 都视为 source launch
- 设计定位：
  - 用于源码调试和验证
  - 不是 Windows Steam 打包版的常规同步主路径

### 8.3 Windows Steam 打包版

- frozen 根目录：
  - `sys._MEIPASS` 或 `sys.executable` 所在目录
- 运行时数据根仍优先：
  - `%LOCALAPPDATA%/N.E.K.O/`
- launcher 默认：
  - merged mode
- 主同步路径：
  - Steam Auto-Cloud 同步 `%LOCALAPPDATA%/N.E.K.O/cloudsave/`
  - 应用自身负责 phase-0 导入和 shutdown 导出
- RemoteStorage bundle helper：
  - 当前会直接返回 `not_source_launch`
  - 不会误走 source-only 分支

### 8.4 macOS

- 运行时根目录：
  - `~/Library/Application Support/N.E.K.O/`
- `cloudsave/`：
  - `~/Library/Application Support/N.E.K.O/cloudsave/`
- RemoteStorage bundle helper：
  - 当前返回 `unsupported_platform`
- Steamworks 加载失败时：
  - 当前错误信息会给出 Gatekeeper / `xattr` / `codesign` 指引

### 8.5 Linux 源码模式

- 运行时根目录优先：
  - `$XDG_DATA_HOME/N.E.K.O/`
- 若未设置 `XDG_DATA_HOME`，则回退：
  - `~/.local/share/N.E.K.O/`
- `cloudsave/` 对应为上述根目录下的：
  - `cloudsave/`
- launcher 默认：
  - 多进程模式
- RemoteStorage bundle helper：
  - 当前返回 `unsupported_platform`
- Steamworks 动态库环境：
  - 当前对 `LD_LIBRARY_PATH` 采用 prepend
  - 不再粗暴覆盖现有环境变量

### 8.6 与旧路径的关系

- 旧 Documents 目录、旧打包目录、项目目录当前只作为：
  - 历史数据导入候选
  - 只读探测与 fallback 辅助信息
- Steam Auto-Cloud 不应继续配置到旧 Documents 目录。
- 后台路径必须与当前 `ConfigManager` 真实选中的运行时数据根保持一致。

---

## 9. Steam Auto-Cloud 与 bundle helper 的边界

### 9.1 主方案

- 当前主方案仍然是 Steam Auto-Cloud。
- Steam 负责同步本机 `cloudsave/`。
- 应用负责启动导入和退出导出。

### 9.2 Windows source 的 bundle helper

- `utils/steam_cloud_bundle.py` 当前只支持 Windows。
- 顶层不再直接导入 `WinDLL`，因此非 Windows 导入该模块不会在 import 阶段炸掉。
- bundle 下载路径：
  - 先读 `__neko_cloudsave_bundle_meta__.json`
  - 若远端指纹已和本地 manifest 对齐则跳过
  - 否则下载 `__neko_cloudsave_bundle__.zip`，解压到 staging，再原子应用到本地 `cloudsave/`
- bundle 上传路径：
  - 先生成 zip bundle 和 meta
  - 先写 bundle，再写 meta
  - 若写 meta 失败，会回滚删除刚写入的 bundle，避免“新 bundle + 旧 meta”半成功状态
- 本地应用 bundle 时：
  - 先校验所有 payload 文件都存在
  - 先复制 payload
  - `manifest.json` 最后替换
  - 这样 manifest 替换才是最后的原子收口点

### 9.3 不应误解的地方

- bundle helper 不是全平台同步方案。
- bundle helper 也不是 Steam 打包版的必经链路。
- 若未来要继续扩展 `ISteamRemoteStorage`，需要单独设计；当前代码并没有把它推广为 Linux/macOS 或 frozen 版主路径。

---

## 10. 当前风险点与已落地防护

### 10.1 退出导出卡顿

当前防护：

- `main_server` shutdown 导出有 3 秒预算。
- launcher 对服务退出有 graceful wait、terminate、kill 和进程树兜底。

剩余边界：

- 如果导出超时，Steam 可能上传上一份本地快照而不是本次最新状态。

### 10.2 SQLite / memory 句柄竞争

当前防护：

- 退出前主动释放 memory_server 角色句柄。
- 快照层本身已有 SQLite shadow copy。
- 下载替换前会释放目标角色句柄并在完成后 reload。

剩余边界：

- 极端异常退出时，仍需依赖 timeout、shadow copy 和回滚链路兜底。

### 10.3 自动导入覆盖策略偏激进

当前真实行为：

- 只要有快照且指纹未确认对齐，`startup_import_required` 就可能为真。

这意味着：

- manifest 指纹为空
- `last_applied_manifest_fingerprint` 为空
- 指纹不一致

以上情况即使运行时已有本地用户内容，也仍可能执行自动导入。

这不是文档误差，而是当前代码实际策略。若后续要改成“本地已有内容时更保守”，必须改代码，不是只改文案。

### 10.4 Steam 不在线或非 Steam 启动

当前行为：

- 本地 `cloudsave/` 导入/导出仍可工作。
- summary 会区分 `steam_available`、`steam_running`、`steam_logged_on`。
- provider 不可用时，前端优先显示 provider unavailable，不再误报 Steam ready/offline。

剩余边界：

- 不通过 Steam 启动时，不会发生真实的 Steam 云上传/下载。

### 10.5 后台路径配置错位

当前防护：

- `/api/cloudsave/steam-autocloud-config` 会返回 `recommended_paths` 和 `current_platform_rule`。
- Windows / macOS / Linux 的路径预览已经与 `ConfigManager` 当前真实根目录口径保持一致。

剩余边界：

- Steamworks 后台若仍配置到旧 Documents 或其他错误根目录，应用侧无法替 Steam 修正后台映射。

---

## 11. 当前建议的 Steamworks 后台配置口径

### 11.1 方案

- 继续使用 Steam Auto-Cloud。
- 不新增自建 HTTP provider。
- 不把 RemoteStorage bundle helper 当后台主方案。

### 11.2 路径建议

可优先参考 `/api/cloudsave/steam-autocloud-config` 返回的：

- `recommended_paths`
- `current_platform_rule`

当前推荐策略是：

- primary root:
  - `WinAppDataLocal`
  - `N.E.K.O/cloudsave`
- macOS override:
  - `MacAppSupport`
  - `N.E.K.O/cloudsave`
- Linux override:
  - `LinuxXdgDataHome`
  - `N.E.K.O/cloudsave`

### 11.3 原则

- 后台配置必须和应用当前真实运行根一致。
- 不再沿用旧 Documents 目录口径。
- Linux 下应按 `XDG_DATA_HOME` / `~/.local/share` 语义配置，不应手写成与当前运行根不一致的固定路径。

---

## 12. 验收口径

### 12.1 当前自动化覆盖重点

当前仓库已经围绕以下方向补了测试：

- `tests/unit/test_cloudsave_startup_flow.py`
- `tests/unit/test_cloudsave_lifecycle_flow.py`
- `tests/unit/test_cloudsave_autocloud.py`
- `tests/unit/test_cloudsave_autocloud_router.py`
- `tests/unit/test_cloudsave_config_manager.py`
- `tests/unit/test_steamworks_loader_paths.py`
- `tests/unit/test_steam_cloud_bundle_i18n_names.py`

这些测试主要覆盖：

- phase-0 bootstrap/import 顺序
- main_server 兜底导入与 memory_server reload
- shutdown 导出与延后关闭 memory_server
- source/frozen 根目录选择
- Windows/Linux/macOS 平台路径分支
- Windows source bundle helper 的平台门控

### 12.2 仍需要继续做的人工验收矩阵

1. Windows 源码
   - `uv run python launcher.py` 能正常启动和退出
   - `/api/cloudsave/summary`、`/api/cloudsave/steam-autocloud-config` 路径正确
   - source 启动时 bundle helper 的 skip / hit 行为与日志一致
2. Windows Steam 打包版
   - 走 frozen 根目录和 merged mode
   - 不误走 source-only bundle helper
   - 退出后 Steam Auto-Cloud 能观察到 `cloudsave/` 更新
3. Linux 源码
   - 设置和不设置 `XDG_DATA_HOME` 两种情况下都能正常定位到数据根
   - 不会触发 Windows-only 分支
   - 关闭时顺序正常
4. macOS
   - 启动与关闭顺序正常
   - 非 Windows 下导入 `steam_cloud_bundle` 不会因 `WinDLL` 失败
   - 若 Steamworks 本体加载失败，错误提示应包含 Gatekeeper 指引

### 12.3 判定“真实 Steam 云存档已跑通”的标准

必须同时满足：

- 运行时真源不是 Steam 直接同步目录
- `cloudsave/` 会在退出前更新
- Steam 客户端会在应用结束后上传该快照
- 另一台设备通过 Steam 启动后能先拿到 `cloudsave/`
- phase-0 或 main_server 兜底导入后，角色配置和 memory 数据在运行时正确恢复

---

## 13. 当前阶段结论

- 本分支的云端同步方案已经明确收口为：
  - `Steam Auto-Cloud + 本地 cloudsave 快照 + 启动导入 + 退出导出`
- 当前代码已经不是最初的“设计草案”阶段，而是有明确平台分支和生命周期收口的实现阶段。
- 现在最重要的不是继续发散新方案，而是继续围绕以下几点验收和收尾：
  - Windows source、Windows Steam 打包版、Linux source、macOS 的真实启动/关闭与路径一致性
  - Steamworks 后台路径映射与 `ConfigManager` 真实根目录的一致性
  - 自动导入覆盖策略是否需要从“偏激进”进一步改成“更保守”
  - 极端退出、SQLite 句柄竞争和跨设备真实 Steam 联调
