# v0.8 存储位置迁移设计（唯一合并版）

> 本文是后续查看和实现存储位置迁移功能时的唯一设计入口。
>
> 原网页端实施文档与原桌面端实施文档的有效内容已经合并到本文，
> 旧文档不再单独维护。

## 0. 文档边界

本文描述 v0.8 存储位置迁移的现行设计和当前代码事实，覆盖：

- NEKO 后端的存储根、锚点根、迁移检查点、恢复与清理规则。
- NEKO 网页端的首启阻断覆盖层、路径选择、维护页、完成提示卡片。
- NEKO-PC 桌面端对该流程的承载边界。
- 自动化测试和人工验收时应遵守的核心语义。

本文不覆盖：

- cloudsave 传输协议本身。
- 打包流水线细节。
- 所有业务模块的完整数据格式。

核心结论：

- 迁移决策、路径校验、检查点、实际复制、恢复、清理都属于 NEKO。
- NEKO-PC 只作为宿主和窗口外壳承载网页端流程，不能重新实现迁移状态机。
- 当前会话不能热切存储根。改用不同根时必须先记录检查点，再受控关闭，关闭后迁移或重连，随后自动重启。

## 1. 角色与职责

### 1.1 NEKO 后端

NEKO 后端负责所有会影响数据安全的决策：

- 计算推荐存储根和固定锚点根。
- 读取和写入 `storage_policy.json`、`root_state`、`storage_migration.json`。
- 校验用户选择的路径是否合法、可写、是否命中保留区。
- 对目标已有数据进行预检和二次确认校验。
- 在关闭后的启动阶段执行待迁移检查点。
- 维护受限启动状态，阻断主业务初始化直到存储状态可用。
- 生成迁移完成提示和旧目录清理能力。
- 执行旧目录清理，并在清理前复验安全条件。

### 1.2 NEKO 网页端

NEKO 网页端负责展示和用户交互：

- 在主界面前显示存储位置阻断覆盖层。
- 展示推荐路径、当前路径、其他位置输入和旧路径候选。
- 调用后端 `select` 做预检，再调用 `restart` 发起受控关闭。
- 当目标已有运行时数据时，在用户点击“确认关闭并迁移”时做二次确认。
- 在服务短暂断开期间显示维护态并轮询恢复。
- 在 ready 后显示非阻断迁移完成提示卡片。
- 调用后端接口清理旧目录，不能自己删除文件。

### 1.3 NEKO-PC 桌面端

NEKO-PC 只提供宿主能力，不拥有迁移规则：

- loadingWindow 展示冷启动阶段的服务状态和迁移状态。
- Pet 等业务窗口必须等待后端可用和存储闸门完成后再放行业务使用。
- 可以提供通用 `window.nekoHost.openPath({ path })` 打开目录。
- 可以提供通用 `window.nekoHost.closeWindow()` 作为右上角关闭兜底。
- 不能直接写 `storage_policy.json`。
- 不能直接写 `storage_migration.json`。
- 不能直接删除旧目录。
- 不能绕过 NEKO 后端路径校验或迁移确认。

## 2. 存储布局模型

v0.8 使用“运行时根 + 固定锚点根”的双根模型。

| 名称 | 含义 | 当前用途 |
| --- | --- | --- |
| `current_root` / `selected_root` | 本次业务运行时实际读写的数据根 | `config`、`memory`、模型与插件等运行时目录 |
| `anchor_root` / `recommended_root` | 标准数据目录下的固定锚点 | `state`、`cloudsave`、策略与迁移检查点 |
| `cloudsave_root` | `anchor_root/cloudsave` | 本地 cloudsave 固定位置 |

`selected_root` 可以等于 `anchor_root`，也可以是用户自定义目录。`anchor_root` 必须保持稳定，用来承载跨迁移周期仍需固定存在的状态。

### 2.1 推荐位置

推荐位置由 `compute_anchor_root()` 根据当前平台标准数据目录计算，通常是标准数据目录下的 `N.E.K.O` 文件夹。

用户选择普通自定义文件夹时，后端会按以下规则归一化：

- 如果选择的目录名已经是 `N.E.K.O`，则该目录就是最终运行根。
- 如果选择的目录名不是 `N.E.K.O`，最终运行根为 `<用户选择目录>/N.E.K.O`。

这个归一化规则由 `utils/storage_policy.py::normalize_selected_root()` 负责，网页端只展示和提交用户选择，最终结果以后端返回为准。

### 2.2 迁移对象

当前迁移的运行时条目为：

- `config`
- `memory`
- `plugins`
- `live2d`
- `vrm`
- `mmd`
- `workshop`
- `character_cards`
- `jukebox`

固定锚点区域不作为普通运行时条目迁移：

- `state`
- `cloudsave`
- `.cloudsave_staging`
- `cloudsave_backups`

这些区域位于 `anchor_root` 下，迁移旧目录清理时也必须保留。

### 2.3 跨进程传播

`utils/storage_layout.py` 将解析出的布局导出为环境变量：

- `NEKO_STORAGE_SELECTED_ROOT`
- `NEKO_STORAGE_ANCHOR_ROOT`
- `NEKO_STORAGE_CLOUDSAVE_ROOT`

后续 Python 子进程和伴生服务应通过该布局继续使用同一套运行根和锚点根。

## 3. 状态文件

### 3.1 `storage_policy.json`

位置：`anchor_root/state/storage_policy.json`

职责：记录稳定选择结果。

核心字段：

- `version`
- `anchor_root`
- `selected_root`
- `selection_source`
- `cloudsave_strategy = fixed_anchor`
- `first_run_completed`
- `updated_at`

`first_run_completed=false` 或策略缺失时，网页端必须进入存储位置选择流程。

### 3.2 `root_state`

职责：记录启动期间根状态、只读维护态、恢复态和最近迁移结果。

关键模式：

- `normal`
- `maintenance_readonly`
- `deferred_init`

当进入 `maintenance_readonly` 时，当前实例应准备关闭或等待关闭后的迁移。迁移失败时会进入 `deferred_init`，下一次启动必须走恢复流程。

### 3.3 `storage_migration.json`

位置：`anchor_root/state/storage_migration.json`

职责：记录关闭后迁移的检查点。

活跃状态：

- `pending`
- `preflight`
- `copying`
- `verifying`
- `committing`
- `retaining_source`
- `rollback_required`

终态：

- `completed`
- `failed`

重要字段：

- `txid`
- `source_root`
- `target_root`
- `selection_source`
- `confirmed_existing_target_content`
- `backup_root`
- `retained_source_root`
- `retained_source_mode`
- `error_code`
- `error_message`
- `requested_at`
- `started_at`
- `updated_at`
- `completed_at`

`confirmed_existing_target_content=true` 表示用户已经确认目标目录存在同名运行时数据覆盖风险。后端在启动迁移时必须再次校验该字段，防止绕过网页端确认。

## 4. 启动与迁移生命周期

### 4.1 正常 ready 启动

1. launcher 解析布局。
2. 如果不存在待迁移检查点，则继续启动主服务和伴生服务。
3. 后端 `bootstrap/status` 返回 ready。
4. 网页端不显示阻断覆盖层。
5. 如果存在已完成迁移且旧目录仍可清理，则显示非阻断完成提示卡片。

### 4.2 首次或状态需要选择

1. 主服务以受限启动方式提供静态页面和存储接口。
2. 主业务初始化被 storage startup barrier 阻断。
3. 网页端先请求系统状态，再请求 `/api/storage/location/bootstrap`。
4. 如果 `blocking_reason=selection_required`，显示选择页。
5. 用户可以选择推荐位置、保持当前路径或选择其他位置。

保持当前路径时：

- 后端写入 `storage_policy.json`。
- 后端释放 startup barrier。
- 当前会话继续，不重启，不迁移。

选择不同路径时：

- `POST /select` 只做校验和预检。
- 后端返回 `restart_required`。
- 网页端显示预检确认卡片。
- 用户确认后，`POST /restart` 写入迁移检查点并触发受控关闭。
- 当前实例关闭后，下一次 launcher 启动时执行迁移。

### 4.3 前后端启动不同步

真实环境里前端、后端和 Electron loadingWindow 并不会同时完成启动。因此设计要求：

- 网页端不能假定后端始终在线。
- 维护态必须能处理请求失败、服务断线和后端恢复。
- `status/bootstrap` 必须设置 no-cache，避免浏览器拿到旧状态。
- `migration_pending` 期间不能回到选择页。
- 服务恢复后，网页端应重新拉取状态并自动恢复主界面。
- 桌面端 loadingWindow 可以展示冷启动迁移状态，但迁移语义仍来自 NEKO launcher 事件和后端状态。

### 4.4 关闭后迁移

`utils/storage_migration.py::run_pending_storage_migration()` 在 launcher 启动阶段执行待迁移检查点。

执行顺序：

1. 加载 `storage_migration.json`。
2. 校验源根、目标根、目标确认标记、写入权限。
3. 将运行时条目复制到目标根。
4. 校验复制结果。
5. 写入 `storage_policy.json`，让新根生效。
6. 更新 `root_state=normal`。
7. 保留旧目录，记录 `retained_source_root`。
8. 标记迁移 `completed`。

如果失败：

- 标记迁移 `failed`。
- 策略恢复到源根。
- `root_state` 进入 `deferred_init`。
- 下一次进入恢复流程，不能静默继续。

### 4.5 恢复态 `rebind_only`

当原始已提交根曾不可用，但后来恢复可用时，后端允许用户重连原路径：

- `select` 返回 `restart_required`。
- `restart_mode=rebind_only`。
- 后续关闭并重启到原路径。
- 不复制运行时数据。

恢复态下不能任意切到第三条新路径；只能恢复原提交根，或显式切回当前可用根继续当前会话。

## 5. 后端接口契约

### 5.1 `GET /api/storage/location/bootstrap`

用于网页端决定是否显示阻断覆盖层。

关键字段：

- `current_root`
- `recommended_root`
- `legacy_sources`
- `anchor_root`
- `cloudsave_root`
- `selection_required`
- `migration_pending`
- `recovery_required`
- `blocking_reason`
- `legacy_cleanup_pending`
- `last_known_good_root`
- `last_error_summary`
- `migration`
- `stage`
- `poll_interval_ms`

### 5.2 `GET /api/storage/location/status`

用于维护态轮询和 ready 后完成提示。

关键字段：

- `ready`
- `status`
- `lifecycle_state`
- `migration_stage`
- `maintenance_message`
- `poll_interval_ms`
- `effective_root`
- `blocking_reason`
- `completion_notice`
- `storage`
- `migration`

### 5.3 `POST /api/storage/location/select`

用于路径选择预检。

请求字段：

- `selected_root`
- `selection_source`

可能结果：

- `continue_current_session`
- `restart_required` + `restart_mode=migrate_after_shutdown`
- `restart_required` + `restart_mode=rebind_only`
- 校验失败错误

预检字段：

- `target_root`
- `estimated_required_bytes`
- `target_free_bytes`
- `permission_ok`
- `warning_codes`
- `target_has_existing_content`
- `requires_existing_target_confirmation`
- `existing_target_confirmation_message`
- `blocking_error_code`
- `blocking_error_message`

目标目录已有运行时数据不是硬阻断。它必须返回风险标记，让网页端在用户点击“确认关闭并迁移”时做二次确认。

### 5.4 `POST /api/storage/location/restart`

用于写入检查点并请求受控关闭。

请求字段：

- `selected_root`
- `selection_source`
- `confirm_existing_target_content`

约束：

- 如果目标与当前根一致，返回 `restart_not_required`。
- 如果目标不可写或空间不足，返回对应阻断错误。
- 如果 `requires_existing_target_confirmation=true` 但请求未携带 `confirm_existing_target_content=true`，返回 `target_confirmation_required`。
- 普通迁移成功受理后，创建 `storage_migration.json`，设置 `root_state=maintenance_readonly`，触发关闭。
- `rebind_only` 成功受理后，更新策略和维护态，触发关闭，但不创建普通复制迁移。

### 5.5 `POST /api/storage/location/pick-directory`

由 NEKO 后端提供系统目录选择器能力。当前桌面端不需要单独实现 Electron 原生选择器。

返回路径只代表用户选择，仍必须进入 `select/restart` 流程，由后端重新校验。

### 5.6 `GET /api/storage/location/retained-source`

用于查询已完成迁移的旧目录保留信息。

### 5.7 `POST /api/storage/location/retained-source/cleanup`

用于清理旧目录。所有清理必须通过该接口，不能由网页端或 NEKO-PC 直接删除。

接口必须校验：

- 当前存在完成迁移提示。
- 请求路径与后端记录的 `retained_root` 一致。
- `retained_root` 满足安全清理条件。
- 清理完成后更新迁移检查点和 `root_state.legacy_cleanup_pending`。

### 5.8 `GET /api/storage/location/diagnostics`

用于测试和排查运行时读写是否收敛到有效根。

关注字段：

- `layout.effective_root`
- `layout.anchor_root`
- `layout.retained_source_root`
- `runtime_entries`
- `anchored_entries`
- `summary.all_runtime_entries_read_from_effective_root_only`

## 6. 路径合法性与预检

路径选择由 `utils/storage_policy.py::validate_selected_root()` 统一校验。

必须拒绝：

- 空路径。
- 相对路径。
- 文件路径。
- 项目目录及其子目录。
- `anchor_root/cloudsave`、`anchor_root/state`、`anchor_root/.cloudsave_staging`、`anchor_root/cloudsave_backups` 及其子目录。
- `anchor_root` 的其他子目录，除非目标正好等于 `anchor_root`。
- 不可写目录。
- 父目录不存在或父目录不可写的新目录。

预检必须提示但不一定阻断：

- 同步盘。
- 网络共享目录。
- 外置卷或挂载卷。
- 符号链接路径。
- 目标根已有运行时数据。

预检必须阻断：

- 目标不可写。
- 目标空间不足。
- 恢复态下选择了不允许的路径。
- 当前实例无法执行受控关闭。

## 7. 目标已有数据处理

当前规则是“允许用户确认后继续”，不是“选择阶段强制换目录”。

当目标根已有运行时数据时：

1. `select` 返回：
   - `target_has_existing_content=true`
   - `requires_existing_target_confirmation=true`
   - `existing_target_confirmation_message`
2. 网页端预检卡片显示风险提示。
3. 用户点击“确认关闭并迁移”后，网页端弹出二次确认。
4. 用户确认后，`restart` 请求必须携带 `confirm_existing_target_content=true`。
5. 后端写入检查点中的 `confirmed_existing_target_content=true`。
6. 启动迁移时再次检查该字段。

迁移到已有目标根时，只覆盖目标中的同名运行时条目。目标目录里的其他文件保留。

## 8. 旧目录保留与清理

迁移完成后，旧目录默认保留，不自动删除。

完成提示中的 `cleanup_available` 由后端计算，网页端只能按该字段显示或隐藏清理入口。

### 8.1 普通旧目录

如果 `retained_root` 是普通旧运行根，并且不等于当前根、目标根、锚点根，也不包含这些受保护根，则可以整目录清理。

### 8.2 旧目录等于 `anchor_root`

当从推荐目录迁移到其他自定义目录时，旧目录可能等于 `anchor_root`。

这种情况下仍可以提供清理入口，但只能删除已迁移的运行时条目：

- `config`
- `memory`
- `plugins`
- `live2d`
- `vrm`
- `mmd`
- `workshop`
- `character_cards`
- `jukebox`

必须保留：

- `state`
- `cloudsave`
- `.cloudsave_staging`
- `cloudsave_backups`
- 其他非迁移运行时条目

### 8.3 禁止清理的情况

如果 `retained_root` 满足任一条件，必须隐藏清理入口，后端接口也必须拒绝：

- 等于当前生效根。
- 包含当前生效根。
- 等于目标根。
- 包含目标根。
- 包含 `anchor_root`。
- 不存在或不是后端记录的保留目录。

这些规则的目标是避免递归删除误伤仍在使用的数据或锚点保留区。

## 9. 网页端 UX

网页端入口：`static/app-storage-location.js` 与 `static/css/storage-location.css`。

### 9.1 页面状态

当前网页端不是多页面拼接，而是：

- 一个阻断覆盖层。
- 一个 ready 后非阻断完成提示卡片。

状态包括：

- `loading`
- `selection_required`
- `selection_required + preview`
- `maintenance`
- `recovery_required`
- `error`
- `completion_notice`

### 9.2 选择页

首屏展示：

- 推荐路径。
- 当前路径。
- `使用推荐位置`。
- `保持当前路径`。
- `选择其他位置`。

推荐位置是期望用户优先选择的路径，因此：

- 推荐路径排在当前路径前面。
- `使用推荐位置` 使用主按钮样式并排第一。
- `保持当前路径` 是次级按钮。

选择页不展示内部实现细节，例如 `anchor_root`、`cloudsave_root`、`storage_policy.json`。

### 9.3 其他位置面板

用户可以：

- 使用系统目录选择器。
- 手动输入绝对路径。
- 使用检测到的旧数据路径。

输入的路径只作为候选，最终以后端 `select` 返回的 `target_root/selected_root` 为准。

### 9.4 预检确认卡片

当 `select` 返回 `restart_required` 时，网页端显示预检卡片。

卡片必须表达：

- 当前会话不会热切根。
- 确认后当前实例会关闭。
- 关闭后会迁移或重连。
- 完成后会自动重启。
- 旧目录默认保留，不自动清理。

按钮：

- 普通迁移：`确认关闭并迁移`
- 恢复重连：`确认关闭并重连路径`
- 返回重新选择

如果存在 `blocking_error_code`，确认按钮禁用。

如果存在 `requires_existing_target_confirmation=true`，确认按钮仍可点击，但点击后必须二次确认。

### 9.5 维护页

维护页覆盖以下阶段：

- 等待当前实例关闭。
- 关闭后迁移或重连。
- 服务短暂断开。
- 服务恢复后重新连接。

进度展示是阶段表达，不是文件级进度。网页端必须能在请求失败时继续停留维护页并重试。

### 9.6 关闭按钮

存储覆盖层和完成提示卡片可以提供右上角关闭按钮，但关闭不是“绕过存储闸门继续使用”。

语义：

- 选择或恢复仍未完成时，关闭只能关闭当前窗口或应用。
- 浏览器拒绝 `window.close()` 时，不应隐藏阻断覆盖层。
- 维护态下关闭不能取消已经由后端接管的关闭、迁移或恢复流程。
- 完成提示卡片关闭只隐藏该提示，不改变迁移状态。

### 9.7 完成提示卡片

迁移完成后，不进入独立成功页，而是在 ready 主界面上显示非阻断提示卡片。

卡片展示：

- 当前生效路径。
- 当前保留目录。
- 完成说明。

不再单独展示“原始路径”，因为它与“当前保留目录”语义重复。

卡片能力：

- 支持拖动，避免遮挡主界面。
- 右上角关闭。
- 在 host 能力可用时，打开当前目录。
- 在 host 能力可用时，打开旧目录。
- 如果 `cleanup_available=true`，显示清理旧数据目录。

卡片底部不提供单独的“关闭”按钮。

## 10. NEKO-PC 承载方案

NEKO-PC 当前应保持“宿主能力”模型。

### 10.1 启动期 loadingWindow

loadingWindow 可以根据 launcher 输出的 `NEKO_EVENT` 展示：

- `storage_migration_processing`
- `storage_migration_completed`
- `storage_migration_failed`
- `storage_migration_restart`

这些事件只用于显示，不改变迁移决策。

### 10.2 业务窗口闸门

桌面端业务窗口不能在后端未 ready 或存储闸门未完成时提前放行。

设计要求：

- 后端 ready 前只显示 loadingWindow。
- 存储阻断期间由网页端覆盖层接管。
- 卫星窗口、托盘动作、快捷键不应绕过主存储状态。

### 10.3 通用 host capability

NEKO-PC 可以提供：

- `window.nekoHost.openPath({ path })`
- `window.nekoHost.closeWindow()`

网页端只检测通用 host capability，不检测 NEKO-PC 专有迁移桥。

禁止出现：

- `window.nekoStorageLocation`
- 桌面端直接调用 `/api/storage/location/select` 代替网页端。
- 桌面端直接调用 cleanup 删除文件。
- 桌面端自己决定目标路径是否安全。

### 10.4 目录选择器

当前首版目录选择器由 NEKO 后端 `pick-directory` 提供。

未来如果切换为 Electron 原生目录选择器，也只能作为“选择路径”能力：

1. Electron 返回用户选择的路径。
2. 网页端把路径填入输入框。
3. 后续仍调用 NEKO 后端 `select/restart`。
4. 后端仍是唯一校验者。

## 11. 故障与恢复

### 11.1 迁移失败

迁移失败时：

- `storage_migration.json.status=failed`。
- `error_code/error_message` 写入检查点。
- 策略回到源根。
- `root_state=deferred_init`。
- 下一次启动进入恢复流程。

用户必须重新确认当前可用路径，不能静默跳过。

### 11.2 源根不可用

如果已提交根不可用：

- 启动进入 `recovery_required`。
- 网页端展示恢复态。
- 用户可以恢复原路径后重连，或切回当前可用根继续。
- 不允许在恢复态任意选择第三条新路径。

### 11.3 服务断线

受控关闭和自动重启期间，网页端请求失败是正常现象。

要求：

- 维护页继续保留。
- 按 `poll_interval_ms` 轮询。
- 服务恢复后重新获取 status。
- 不能因为一次请求失败就回到选择页或隐藏覆盖层。

## 12. 当前代码入口

NEKO：

- `utils/storage_policy.py`
- `utils/storage_layout.py`
- `utils/storage_migration.py`
- `utils/storage_location_bootstrap.py`
- `main_routers/storage_location_router.py`
- `static/app-storage-location.js`
- `static/css/storage-location.css`
- `launcher.py`
- `main_server.py`
- `memory_server.py`

NEKO-PC：

- `src/main.js`
- `src/preload-pet.js`
- `test/storage-window-display-contract.test.js`

## 13. 测试与验收

### 13.1 自动化测试

NEKO 建议运行：

```bash
node --check static/app-storage-location.js
./.venv/bin/pytest tests/unit/test_storage_policy.py tests/unit/test_storage_migration.py tests/unit/test_storage_location_router.py tests/frontend/test_storage_location_startup.py -q
```

NEKO-PC 建议运行：

```bash
node --check src/main.js
node --check src/preload-pet.js
node --test test/storage-window-display-contract.test.js
```

### 13.2 人工验收矩阵

必须覆盖：

- 首次启动，保持当前路径。
- 首次启动，使用推荐位置。
- 首次启动，选择普通自定义文件夹，最终落到其 `N.E.K.O` 子目录。
- 目标根已有运行时数据，点击确认时二次确认。
- 目标不可写或空间不足时阻断。
- 迁移过程中服务断线，维护页持续轮询。
- 迁移完成后显示完成提示卡片。
- 完成提示卡片可拖动。
- 完成提示卡片不展示重复原始路径。
- 完成提示卡片只有右上角关闭，没有底部关闭按钮。
- 打开当前目录和旧目录只作为便利动作。
- 清理旧目录必须二次确认并调用后端接口。
- 旧目录等于 anchor root 时，只删除运行时条目，保留 `state/cloudsave`。
- 保留目录包含当前根、目标根或 anchor 根时，不显示清理入口。
- NEKO-PC 关闭按钮不能绕过后端存储闸门。

### 13.3 完成定义

v0.8 视为完成时必须满足：

- 存储位置选择只在需要时阻断。
- 当前会话不热切根。
- 迁移只在关闭后执行。
- 失败可恢复，且不会静默进入不一致状态。
- cloudsave 和 state 保持 anchor root 固定。
- 运行时读写收敛到 selected root。
- 目标已有数据需要用户二次确认。
- 旧目录清理不会误删当前根、目标根或 anchor 保留区。
- NEKO-PC 不拥有迁移决策，只承载窗口和通用 host 能力。

## 14. 一句话结论

v0.8 存储位置迁移的安全边界是：NEKO 后端负责真实状态与文件操作，网页端负责可解释的用户确认，NEKO-PC 只负责承载窗口和通用宿主能力；任何会改变存储根、复制数据或删除目录的动作都必须回到 NEKO 后端接口。
