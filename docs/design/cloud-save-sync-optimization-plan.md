# Steam Auto-Cloud 云存档实施计划（按当前仓库实现校准）

> 本文用于替换上一版“远端 provider / 手动上传下载云角色”的口径。
> 当前目标已明确调整为：`Steam Auto-Cloud + 本地 cloudsave 快照自动导入/导出`。
> 用户登录 Steam 后，通过 Steam 启动游戏即可使用真实 Steam 云存档。

---

## 1. 目标结论

- 运行时真源仍然是本地应用数据目录，不直接把 Steam Cloud 当作运行目录。
- Steam 只负责同步本地 `cloudsave/` 快照目录，不直接感知运行时的高频写入目录。
- 启动时自动导入，退出时自动导出，形成“Steam 云端 <-> cloudsave 快照层 <-> 本地运行时真源”闭环。
- 前端“上传 / 下载”语义保留，但产品文案改为：
  - 上传：准备 / 更新 Steam Cloud 快照。
  - 下载：把已同步到当前设备的 Steam Cloud 快照恢复到本地运行时。
- 不再实现自建 HTTP 云存档服务，也不再把 `provider_available` 口径继续往远端服务方向扩展。

---

## 2. 当前代码基线

以下内容已经在当前仓库中存在，后续设计必须建立在这些事实之上。

### 2.1 已有本地快照能力

- `utils/cloudsave_runtime.py`
  - `export_local_cloudsave_snapshot`
  - `import_local_cloudsave_snapshot`
  - 单角色导出 / 导入能力
  - 单角色包的对外语义仍然是“只更新目标角色”，但当前 `export_cloudsave_character_unit()` 的内部产物仍会 stage / 重写合并后的 `profiles/characters.json`
  - `manifest.json`
  - `cloudsave_local_state.json`
  - `character_tombstones.json`
- 快照内容已经是“过滤后的安全快照层”，而不是直接把整个运行目录原样同步。
- SQLite 已有 shadow copy 处理逻辑，避免直接把正在写入的数据库生拷贝进快照。

### 2.2 已有前端与 API 基础

- `main_routers/cloudsave_router.py`
  - `GET /api/cloudsave/summary`
  - `GET /api/cloudsave/character/{name}`
  - `POST /api/cloudsave/character/{name}/upload`
  - `POST /api/cloudsave/character/{name}/download`
- `static/js/cloudsave_manager.js`
  - 现有页面仍保留单角色操作体验
  - 已补成 Steam Auto-Cloud 文案分支
- `templates/cloudsave_manager.html`
  - 继续作为云存档管理页入口

### 2.3 本轮已接入的 Steam Auto-Cloud 管理层

- 新增 `utils/cloudsave_autocloud.py`
  - `CloudSaveManager.build_status()`
  - `CloudSaveManager.import_if_needed()`
  - `CloudSaveManager.export_snapshot()`
- `main_server.py`
  - 启动时：自动导入 `cloudsave/` 到运行时目录
  - 退出时：先尝试释放记忆句柄，再把运行时导出回 `cloudsave/`
  - 退出导出带 3 秒超时保护
- `cloudsave_router` 返回值已补充：
  - `sync_backend = "steam_auto_cloud"`
  - `steam_autocloud = {...}`

---

## 3. 当前真实架构

### 3.1 三层结构

1. Steam Cloud
   - 由 Steam Auto-Cloud 托管。
   - 只同步 `cloudsave/` 目录。
2. 本地 cloudsave 快照层
   - 是给 Steam 同步使用的中间层。
   - 只放安全、可迁移、可校验的快照文件。
3. 本地运行时真源
   - 业务实际读写目录。
   - 高频写入发生在这里。

### 3.2 启动闭环

1. 用户通过 Steam 启动游戏。
2. Steam 在进程启动前，把云端 `cloudsave/` 同步到本机。
3. `main_server` 启动时执行 `CloudSaveManager.import_if_needed()`。
4. 若发现以下任一条件成立，则自动把快照导入运行时目录：
   - 本地运行时为空。
   - `last_applied_manifest_fingerprint` 为空。
   - `manifest.fingerprint` 与上次已应用指纹不同。
5. 导入完成后，再进入角色初始化与业务层启动。

### 3.3 运行中闭环

- 业务层继续直接读写本地运行时目录。
- Steam 不参与运行中的高频同步。
- 云存档页里的“上传”仅更新本地 `cloudsave/` 快照，不会立刻触发 Steam 上传。
- 云存档页里的“下载”仅把当前设备已经拿到的快照恢复到运行时目录。

### 3.4 退出闭环

1. 进入 `main_server` shutdown。
2. 先释放可见角色的 memory server 句柄，降低 SQLite 锁冲突概率。
3. 调用 `CloudSaveManager.export_snapshot()`。
4. 导出后的安全快照覆盖本地 `cloudsave/`。
5. 主进程退出。
6. Steam 观察到游戏结束后，自动把 `cloudsave/` 变化上传到 Steam Cloud。

---

## 4. 平台路径口径

当前 `ConfigManager` 已优先使用标准应用数据目录，而不是旧版 Documents 目录。Steam Auto-Cloud 也应跟随这套真实路径。

### 4.1 Windows

- 运行时根目录：
  - `%LOCALAPPDATA%/N.E.K.O/`
- Steam Auto-Cloud 应同步：
  - `%LOCALAPPDATA%/N.E.K.O/cloudsave/`

### 4.2 macOS

- 运行时根目录：
  - `~/Library/Application Support/N.E.K.O/`
- Steam Auto-Cloud 应同步：
  - `~/Library/Application Support/N.E.K.O/cloudsave/`

### 4.3 Linux

- 运行时根目录优先：
  - `$XDG_DATA_HOME/N.E.K.O/`
- 若未设置 `XDG_DATA_HOME`，则回退：
  - `~/.local/share/N.E.K.O/`
- Steam Auto-Cloud 应同步对应目录下的：
  - `cloudsave/`

### 4.4 与旧路径的关系

- 历史 Documents / 项目目录 / 旧打包目录，当前只作为旧数据导入候选与兜底。
- Steam Auto-Cloud 不应继续指向旧 Documents 目录，否则会与当前真实运行根不一致。

---

## 5. 同步文件边界

### 5.1 允许进入 `cloudsave/` 的内容

- `manifest.json`
- `catalog/`
- `profiles/`
- `bindings/`
- `memory/`
- `meta/`
- `overrides/`
- 受控状态文件快照

### 5.2 不进入 Steam Cloud 的内容

- `live2d/`
- `vrm/`
- `mmd/`
- `workshop/`
- `plugins/`
- 大体积模型资源本体
- API Key、Cookie、Token、Provider 地址等敏感配置
- 设备相关窗口布局、显示器、绝对路径类配置

### 5.3 现阶段快照语义

- `cloudsave/` 不是运行目录镜像。
- 它是“过滤后、可迁移、可被 Steam Auto-Cloud 托管”的快照目录。
- 这也是当前仓库已经具备的正确方向，不需要再回到“全目录直传”方案。

---

## 6. CloudSaveManager 责任定义

### 6.1 `build_status()`

负责返回当前 Steam Auto-Cloud 状态快照，至少包括：

- `backend`
- `has_snapshot`
- `manifest_fingerprint`
- `last_applied_manifest_fingerprint`
- `startup_import_required`
- `runtime_has_user_content`
- `last_successful_export_at`
- `last_successful_import_at`
- `steam_available`
- `steam_running`
- `steam_logged_on`

### 6.2 `import_if_needed()`

职责：

- 启动时检查是否需要把 `cloudsave/` 应用回运行时。
- 没有快照则跳过。
- 快照与本地已应用指纹一致且运行时已有内容时跳过。
- 其余情况执行导入。

设计意图：

- 避免每次启动都无脑覆盖本地运行时。
- 避免崩溃恢复场景下，把未变化的旧快照反复覆盖到可能更新过的本地运行时。

### 6.3 `export_snapshot()`

职责：

- 在退出前把运行时状态提炼为本地快照。
- 更新 `manifest.json`
- 更新 `cloudsave_local_state.json`
- 为 Steam Auto-Cloud 提供最新可上传内容。

约束：

- 必须快。
- 必须可中断。
- 建议始终保持幂等。

---

## 7. 前端与产品口径

### 7.1 页面文案口径

当前页面与按钮应统一解释为：

- 上传：
  - 不是“直接上传到远端自建云”
  - 而是“准备 / 更新本地 Steam Cloud 快照”
- 下载：
  - 不是“实时从远端拉取”
  - 而是“应用已由 Steam 同步到当前设备的快照”

### 7.2 推荐提示文案

- 上传成功：
  - 当前角色状态已生成或更新 Steam Cloud 快照，等你通过 Steam 退出游戏后，Steam 会自动上传到云端。
- 下载成功：
  - 当前设备上的 Steam Cloud 快照已恢复到本地运行时。

### 7.3 当前产品边界

- 仍保留单角色操作体验。
- 但底层真实云同步单位已经变成 `cloudsave/` 整体快照目录。
- 也就是说：
  - UI 可以继续给用户“单角色管理”体验。
  - Steam 侧实际同步对象仍然是整个 `cloudsave/` 文件树。

---

## 8. Steamworks 后台配置建议

### 8.1 采用 Steam Auto-Cloud

- 不新增 `ISteamRemoteStorage` 直连实现。
- 不新增自建网盘 / HTTP API。
- 后台只配置 Auto-Cloud 路径映射即可。

### 8.2 路径规则

- Windows：
  - `%LOCALAPPDATA%/N.E.K.O/cloudsave/*`
- macOS：
  - `~/Library/Application Support/N.E.K.O/cloudsave/*`
- Linux：
  - `$XDG_DATA_HOME/N.E.K.O/cloudsave/*`
  - 或 `~/.local/share/N.E.K.O/cloudsave/*`

实际以 Steamworks 后台支持的宏写法为准，但目录目标必须与当前 `ConfigManager` 的真实数据根保持一致。

### 8.3 配额建议

- 单用户建议先给 `50MB ~ 100MB`
- 当前快照不包含大模型资源，容量主要消耗在：
  - 角色配置
  - memory JSON
  - SQLite shadow copy

---

## 9. 高风险点与现有防护

### 9.1 退出时导出卡死

风险：

- 若退出导出卡住，Steam 会误判游戏仍在运行。

当前处理：

- `main_server` shutdown 导出已加 3 秒超时保护。

### 9.2 SQLite 锁争抢

风险：

- 导出 `time_indexed.db` 时，记忆系统仍可能持有句柄。

当前处理：

- shutdown 前会尝试调用 `release_memory_server_character(...)`
- 快照层本身已有 SQLite shadow copy 逻辑

仍需继续验证：

- 多角色同时活跃时的句柄释放覆盖率
- 极端退出时 WAL / journal 场景

### 9.3 Steam 未登录或不是从 Steam 启动

风险：

- `cloudsave/` 仍可本地存在，但不会发生真实 Steam 云同步。

当前处理：

- 状态接口已区分：
  - `steam_running`
  - `steam_logged_on`
  - `steam_available`
- 前端已增加 Steam Auto-Cloud 在线 / 离线提示

### 9.4 跨平台路径不一致

风险：

- Windows、macOS、Linux 如果指向不同根目录，会导致“看起来导出了快照，但 Steam 实际没同步到正确位置”。

当前要求：

- 只允许使用 `ConfigManager` 当前真实运行根下的 `cloudsave/`
- 不再沿用旧版 Documents 目录口径

---

## 10. 测试与验收口径

### 10.1 单元测试

至少覆盖：

- `CloudSaveManager.import_if_needed()`
  - 运行时为空时会导入
  - 指纹已应用时会跳过
- `CloudSaveManager.export_snapshot()`
  - 能写出快照并回填状态
- `cloudsave_router`
  - 返回 `sync_backend = steam_auto_cloud`
  - 返回 `steam_autocloud` 负载
  - `GET /api/cloudsave/steam-autocloud-config` 返回当前机器路径与推荐 Auto-Cloud 规则
- i18n
  - 所有语言包补齐 Steam Auto-Cloud 新键

### 10.2 手工联调

需要至少验证三类场景：

1. 同设备、同系统、通过 Steam 启动
   - 退出后 Steam 是否上传 `cloudsave/`
   - 下次启动是否自动导回运行时
2. 跨设备 / 跨系统
   - A 设备退出后上传
   - B 设备通过 Steam 启动后，是否先拿到 `cloudsave/` 再自动导入
3. 非 Steam 启动
   - 本地快照导入导出仍工作
   - 但 UI 明确提示 Steam Cloud 未连接

建议联调前先查看：

- `GET /api/cloudsave/steam-autocloud-config`
  - 核对 `app_id`
  - 核对 `runtime_root`
  - 核对 `cloudsave_root`
  - 按 `recommended_paths` 在 Steamworks 后台填写 Auto-Cloud 规则

### 10.3 判定“真实 Steam 云存档已跑通”的标准

必须同时满足：

- 本地运行时目录不是 Steam 同步目录
- `cloudsave/` 会在退出前更新
- Steam 客户端能看到该目录变更并完成上传
- 另一台机器通过 Steam 启动后能拿到相同快照
- 启动导入后，角色配置与记忆在运行时目录正确恢复

---

## 11. 当前阶段结论

- 本项目云存档方案已明确收敛为 Steam Auto-Cloud，而不是自建云同步。
- 当前仓库已经具备本地快照层、启动导入、退出导出、UI 语义调整和状态返回的主体代码。
- 后续工作的重点不再是“设计一个新的云架构”，而是：
  - 继续做真实 Steam Auto-Cloud 后台配置
  - 做跨平台路径联调
  - 做真实 Steam 客户端上传 / 下载验收
  - 补充更多边界测试与极端退出验证
