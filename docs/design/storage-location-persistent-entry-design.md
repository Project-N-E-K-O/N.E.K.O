# 存储位置迁移架构与维护

> **文档状态：Current contract（当前合同）。** 本文由历史上的“存储位置常驻入口优化设计”恢复，并按当前 N.E.K.O 与 N.E.K.O-PC 实现重写。当前代码与测试优先于本文；修改相关行为时必须同步更新本文。

## 1. 范围

本文记录桌面端“存储位置”功能的长期架构、迁移流程、跨仓库边界和维护检查项，覆盖：

- 首次启动选择存储位置；
- 在记忆浏览器中查看和更改存储位置；
- 运行时数据从旧根目录迁移到新根目录；
- 已选择目录不可用时的恢复或重新绑定；
- 迁移后的旧目录保留与手动清理；
- N.E.K.O-PC 在启动和迁移期间提供的桌面宿主能力。

本文**不是云存档设计文档**。云存档只作为固定锚点中的受保护目录出现在边界说明中；其同步协议、Steam Auto-Cloud 和冲突处理由其他文档维护。

## 2. 核心原则

1. **N.E.K.O 后端是业务状态和文件安全的唯一权威。** 路径校验、空间检查、迁移检查点、复制、验证、策略提交、失败恢复和旧目录清理都由后端执行。
2. **N.E.K.O Web 负责产品交互。** 首次选择、预检展示、已有目标内容确认、维护遮罩、完成提示和旧目录清理入口都属于 Web。
3. **N.E.K.O-PC 只提供宿主能力和桌面生命周期协调。** 它可以选择目录、打开路径、关闭窗口、阻止过早打开卫星窗口，但不能复制后端的存储状态机或绕过后端校验。
4. **策略只在迁移验证完成后提交。** `selected_root` 不能先于数据复制和核验切换。
5. **源目录默认保留。** 自动迁移不是移动操作；只有用户在完成提示中明确清理，后端再次验证安全边界后，旧目录才会删除。
6. **读取接口不得意外写状态。** 启动状态轮询不能覆盖并发迁移失败时刚恢复的状态。

## 3. 跨仓库职责

| 层 | 当前职责 | 不应承担的职责 |
| --- | --- | --- |
| N.E.K.O `utils/storage/` | 根目录布局、策略、路径校验、迁移检查点、复制与验证、路径重写 | UI 和 Electron 窗口管理 |
| N.E.K.O `main_routers/storage_location_router.py` | HTTP 合同、并发串行化、预检、受控重启、回滚、诊断、旧目录清理 | 原生目录选择器的具体实现 |
| N.E.K.O Web | 首次启动与常驻入口 UI、确认语义、跨页面迁移通知 | 决定某个路径是否安全、直接迁移文件 |
| N.E.K.O launcher | 服务启动前执行待处理迁移，重建并导出布局，发出启动事件 | 更改 Web 交互语义 |
| N.E.K.O-PC main/preload | 后端状态轮询、启动门禁、维护期间窗口保护、通用宿主桥 | 存储策略、迁移数据、已有内容覆盖决策 |

这条边界是跨仓库兼容合同：Web 在普通浏览器中也必须能使用后端回退；N.E.K.O-PC 缺失时，存储功能不能因为没有 Electron API 而失去业务正确性。

## 4. 根目录模型

### 4.1 运行根目录与固定锚点

系统区分两个根目录：

- **selected root / runtime root**：用户选择的运行时数据根目录。配置、记忆、插件、模型和其他运行数据从这里读取和写入。
- **anchor root**：平台推荐位置下的固定锚点。它不随用户选择迁移，用于保存策略、迁移状态、云存档和云存档临时/备份数据。

平台推荐锚点为：

| 平台 | 推荐位置 |
| --- | --- |
| Windows | `%LOCALAPPDATA%/N.E.K.O` |
| macOS | `~/Library/Application Support/N.E.K.O` |
| Linux | `$XDG_DATA_HOME/N.E.K.O`，未设置时为 `~/.local/share/N.E.K.O` |

历史 Documents、可执行文件目录和工作目录只是旧数据导入候选或最后回退，不再是正常情况下的推荐根目录。

锚点内的长期文件和目录：

```text
<anchor_root>/
├── state/
│   ├── storage_policy.json
│   ├── storage_migration.json
│   ├── community_auth.json
│   ├── social_session.json              # 普通浏览器回退
│   ├── community_oauth_pending.json     # 有效期内的一次性 PKCE 状态
│   └── community_steam_pending.json     # 有效期内的一次性 PKCE 状态
├── cloudsave/
├── .cloudsave_staging/
└── cloudsave_backups/
```

迁移和清理逻辑不得把 `state`、`cloudsave`、`.cloudsave_staging` 或 `cloudsave_backups` 当成运行时数据迁走或删除。

社区登录凭据属于固定本机私有状态，不跟随 selected root。桌面宿主提供 `NEKO_USER_DATA_DIR` 时，`social_session.json` 以宿主 userData 为权威；普通浏览器回退才使用 anchor 的 `state`。这组文件不属于云存档，也不加入运行根迁移清单。

### 4.2 进程间布局环境变量

launcher 在确定布局后导出：

- `NEKO_STORAGE_SELECTED_ROOT`
- `NEKO_STORAGE_ANCHOR_ROOT`
- `NEKO_STORAGE_CLOUDSAVE_ROOT`

这些变量是 N.E.K.O 进程间传递已解析布局的内部合同，不是用户配置接口。布局解析实现在 `utils/storage/layout.py`，`ConfigManager` 的目录绑定实现在 `utils/config_manager/storage_roots.py`。
运行期读取或写入迁移检查点时必须优先使用 `ConfigManager.anchor_root`；只有布局尚未解析、且调用方没有提供锚点时，才允许重新计算平台推荐位置。否则 owner 导出的布局、自定义测试布局和实际检查点可能落在不同目录。

### 4.3 选择目录不可用

策略中已经提交的 `selected_root` 不可访问时，运行时会临时回退到锚点，以便启动最小服务和展示恢复 UI；同时保留：

- `committed_selected_root`：策略中原本提交的路径；
- `reported_current_root`：对 UI 报告的原路径；
- `recovery_committed_root_unavailable = true`：进入恢复流程的事实。

回退不能被误写成新的用户选择。用户可以重新连接原路径并执行 `rebind_only`，或明确选择推荐位置/其他位置。

## 5. 持久化状态

### 5.1 存储策略

`<anchor_root>/state/storage_policy.json` 当前版本为 `1`，关键字段为：

- `version`
- `anchor_root`
- `selected_root`
- `selection_source`：`default`、`user_selected` 或 `recovered`
- `cloudsave_strategy`：固定为 `fixed_anchor`
- `first_run_completed`
- `updated_at`

策略表示最后一次**已经提交并可用**的选择，不表示正在进行的迁移目标。

策略读取同样采用 fail-closed 语义。只有策略文件不存在时才使用首次启动默认值；坏 JSON、非对象、未知版本、缺少必填字段、非法 `selection_source`/`cloudsave_strategy`，以及指向项目目录、锚点保留区、文件系统根、符号链接或 Windows 重解析点的路径，都返回 `storage_policy_unavailable`。`ConfigManager` 不得吞掉这个错误并临时把运行根改成默认目录，否则同一实例可能在错误根产生一套新数据。已经提交但暂时离线的外置盘不是坏策略，仍走“选择目录不可用”的恢复流程并保留原路径。

### 5.2 迁移检查点

`<anchor_root>/state/storage_migration.json` 当前版本为 `2`。活动状态包括：

```text
pending -> preflight -> copying -> verifying -> publishing -> committing
        -> retaining_source -> completed
```

恢复相关状态包括 `rollback_required`，终态还包括 `failed`。检查点除源目录、目标目录、选择来源、进度和错误外，还保存事务编号、受约束的事务目录、目标确认时的摘要、发布入口、目标原有入口和保留源目录状态。事务目录只能是当前目标卷内布局，或旧版已经写入检查点的目标同级布局；不得接受任意路径。版本 1 检查点仍可读取，但恢复后统一按复制语义执行。

只要存在活动检查点，启动门禁必须把系统视为 `migration_pending`，而不能同时继续普通业务初始化。

检查点读取采用 fail-closed 语义：只有文件确实不存在才等价于“没有迁移”；坏 JSON、非对象内容、权限错误或其他读取异常都必须作为 `storage_status_unavailable` 阻断启动。不得把损坏或暂时不可读的检查点吞掉后切回普通根目录，否则可能在未完成发布旁边启动一套新的业务写入。

### 5.3 启动阻塞原因与生命周期

后端对外的三个业务引导原因是：

- `selection_required`
- `migration_pending`
- `recovery_required`

另有 `rollback_required`、`storage_policy_unavailable` 和 `storage_status_unavailable` 三类完整性阻塞。它们不能被降级成业务引导或普通启动等待。同时出现多个条件时，优先级为策略/检查点不可用、未完成回滚、迁移、恢复、首次选择。HTTP 的 canonical 生命周期使用 `ready`、`maintenance`、`rollback_required`、`recovery_required`、`selection_required` 和 `storage_policy_unavailable`/`storage_status_unavailable`；`/api/system/status` 的 legacy `status=migration_required` 继续保留给旧调用方，新增逻辑应读取 `lifecycle_state` 及 `storage.*`。每个状态响应还带本进程随机 `instance_id`，宿主在受管代次内必须校验它，不能把复用端口上的另一实例当成本代恢复。前端和 N.E.K.O-PC 不应自行读取策略文件。

launcher 必须在 cloudsave phase-0 和三个服务的业务初始化之前解析策略与迁移检查点；`root_state` 在 phase-0 读取，其失败同样必须先进入恢复代次再启动服务。任一权威损坏或读取失败时，不能直接退出，也不能猜测一个默认业务根继续启动。当前启动代次通过进程内 `NEKO_STORAGE_RECOVERY_MODE` 标记进入受限恢复；合法原因只有 `selection_required`、`migration_pending`、`recovery_required`、`storage_policy_unavailable` 和 `storage_status_unavailable`。首次策略缺失也必须在 phase-0 前设置 `selection_required`，确保 Main、Memory、Agent 都不在用户确认运行根之前初始化或写入临时默认根。该标记不写回用户状态，下一次显式启动仍重新检查磁盘事实。坏策略只能使用固定锚点承载恢复 HTTP 表面；坏检查点和坏 `root_state` 保留已提交策略对应的根，但不执行迁移、配置升级或普通业务写入。策略在首次读取后、检查点处理时或最终布局重建时发生变化，也必须落到同一个锚点恢复代次，不能留下先检查后使用的 fail-dead 窗口。受限代次不能在网页选择后直接依次释放 Memory、Agent、Main：Cloud Save 导入和配置迁移尚未执行时，先初始化任一子服务都会缓存导入前状态，甚至制造本地内容而改变云快照判定。有效选择必须在同一事务中提交策略和 `restart_rebind` 交接状态，再请求受控退出；launcher 在任何 phase-0 之前统一核对策略根、交接目标和迁移状态并原子消费该标记，正常接力和旧代崩溃/断电后的显式冷启动使用同一入口。只有消费成功，下一代才按标准顺序执行 phase-0、初始化三个服务；目标不一致或写入失败必须保留 maintenance 证据并只启动固定恢复表面。退出请求未被接受时恢复策略、检查点和根状态前像。`startup_release_failed` 仅作为旧代/异常嵌入路径的防御性进程覆盖层，不能污染 Memory/Agent 对持久化状态的重新检查。受限状态下的 shutdown 必须跳过普通持久化和云导出。

`maintenance` 还有一个可操作的子阶段 `migration_phase=awaiting_shutdown`：迁移检查点已经建立，但当前服务尚未完成受控退出，迁移 worker 还没有开始。此时仅允许再次调用 `/exit`，并暴露 `shutdown_retry_allowed=true`、`recovery_action=retry_safe_exit`；不得关闭桌面壳、启动本地 replacement 或把状态翻回 ready。一旦 launcher 报告 processing/copying，安全关闭重试权限永久撤销。

`STORAGE_LOCATION_STAGE` 当前为 `stage3_web_restart`。这个名字是兼容字段，不代表功能仍处于未完成阶段。

## 6. 数据迁移语义

### 6.1 当前迁移清单

`utils/storage/entries.py` 的 `RUNTIME_STORAGE_ENTRIES` 是迁移、旧数据识别、预检、诊断和选择性清理的唯一权威清单。`MIGRATED_RUNTIME_ENTRY_NAMES` 只是由它派生的兼容名称：

```text
config
memory
plugins
live2d
vrm
mmd
pngtuber
workshop
character_cards
card_faces
jukebox
avatar_tools
state/game_scores
embedding_models
runtimes
plugin-runtime
```

前 13 项属于用户数据；`embedding_models`、`runtimes` 和 `plugin-runtime` 属于随运行根变化的可重建运行缓存，但为保证迁移后离线可用和插件任务连续性也一并复制。`state` 本身是固定锚点控制数据，只有其子项 `state/game_scores` 随 selected root 迁移。

迁移拒绝源入口、内部条目及源/目标路径链上的符号链接；Windows junction 等重解析点按同一边界处理，避免复制越过用户确认的目录。这个检查覆盖每个清单条目的**完整词法父链**，包括 `state/game_scores` 的中间 `state`，并在源扫描、暂存、发布、回滚和旧根清理的不可逆边界重复检查；不能只检查末级入口是否为链接。每个文件按 SHA-256、长度和相对路径生成确定性清单摘要，空目录也进入摘要；复制前后的源摘要必须一致，暂存和最终发布摘要必须与预期一致。`workshop_config.json` 中绑定旧运行根的路径只在暂存副本中重写到最终目标，不修改源目录。

### 6.2 目标目录已有内容

目标目录含有用户数据时，预检返回已有内容提示，并要求 `confirmed_existing_target_content`。确认只允许迁移继续，不改变后端规则：

- 与迁移清单同名的目标入口会被源入口替换；
- 目标目录中无关的其他文件会保留；
- `selection_source` 只用于展示和审计，`legacy`、`recovered` 等来源不能把复制变成“直接采用目标内容”。

确认建立时，后端记录目标中受管入口的内容摘要；launcher 在复制前和发布前各复核一次。目标受管内容只要发生变化，迁移就以 `target_changed_since_confirmation` 停止，不能用过期确认覆盖新数据。

### 6.3 目标路径约束

目标必须是绝对路径，并且满足以下条件：

- 不是普通文件；
- 已存在时可写，未存在时可安全创建；
- 不是文件系统根目录，且目标及其现存父路径不包含符号链接；
- 不位于项目/仓库目录内；
- 与源目录不同，且源、目标不能互相嵌套；
- 不位于锚点的 `state`、`cloudsave`、`.cloudsave_staging` 或 `cloudsave_backups` 保留区域内；
- 用户在原生选择器中选择父目录时，若末级名称不是 `N.E.K.O`，规范化逻辑会追加应用目录名。

同一路径在首次选择 `/select` 中不写策略、不建立复制检查点，也不直接关闭；它只返回 `restart_required + restart_operation_id + rebind_only`。用户确认后由 `/restart` 在互斥锁内重新读取当前事实、建立 `restart_rebind` 受控接力并请求关闭，让下一代在任何业务服务初始化前完成 Cloud Save/config phase-0。这样 `/select` 的响应丢失不会留下一个仍可能迟到落盘的一阶段变更。普通 ready 会话的 `/preflight` 对同根仍返回 `restart_not_required`。

### 6.4 提交、失败与旧目录

正常迁移顺序是：

1. 在锁内重新预检并记录写入前快照；
2. 写入迁移检查点，并把根状态切到 `maintenance_readonly`；
3. 请求当前服务受控退出；
4. launcher 再次实际探测目标可写性和磁盘空间，在目标根内部的隐藏事务目录中完整暂存并核验数据，确保目标根本身是挂载点时仍与发布入口同卷；若该随机事务路径已经存在但检查点不能证明归属，必须停止而不能递归删除未知内容；
5. 先 flush 暂存文件和目录，再记录并持久化 `publishing` 检查点；把目标同名入口原子移入事务备份，再把已验证的暂存入口逐项原子发布；每次 replace 后 flush 对应父目录；
6. 对最终目标再次做内容摘要核验，之后才提交策略和正常根状态；
7. N.E.K.O-PC 关闭旧一代 launcher/Job/supervisor 所有权后启动新一代服务；没有桌面属主时 launcher 才走 self relaunch 回退；
8. 源目录保持不变，等待用户手动清理。

如果请求退出失败或调用在持久化期间被取消，路由会用写入前快照精确恢复。若恢复 root state 和保留 recovery checkpoint 又同时失败，同进程的退化标记仍必须让后续 `/status` 保持 `awaiting_shutdown`，不能因检查点缺失而翻回 ready。暂存、发布、最终核验或策略提交失败时，后端先删除本次发布入口并恢复目标原有入口，再让策略和根状态回到源目录；恢复策略、根状态或终态检查点有任一无法落盘时，launcher 使用迁移结果中的 source root 强制构建只读恢复布局并阻止普通服务启动，不能重新解析到已经回滚的空目标。自动回滚本身失败时保留事务目录并进入 `rollback_required`，不得假装迁移成功。回滚只有在发布清单、备份摘要和目标最终摘要全部能证明与 `target_baseline` 一致时才可删除事务目录；备份缺失只允许“此前已经恢复且目标正好等于基线”的幂等恢复。任何目标并发改写、清单缺失、事务目录缺失或摘要不一致都继续保留 `rollback_required` 证据。

原子写和 rename 的掉电顺序是安全合同：POSIX 在文件 `fsync` 后同步父目录；Windows 对目录句柄能力不一致，目录 flush 为 best-effort，但仍依赖同卷原子 replace、检查点和内容摘要在下次启动恢复。运行目录从暂存区公开时还必须拒绝覆盖切换窗口中新出现的名字：Windows 使用拒绝覆盖的 `os.rename`，Linux 使用 `renameat2(RENAME_NOREPLACE)`，macOS 使用 `renameatx_np(RENAME_EXCL)`；缺少原子 no-replace 原语时 fail-closed，不能用会替换空目录的普通 POSIX rename。任何平台都不能把“API 返回成功”当成磁盘已持久化的替代证据。

进程在 `publishing` 或 `committing` 中崩溃时，下次启动先按检查点恢复目标，再从源目录重新执行。一个由普通 pending 进入 `rollback_required` 的启动代次最多交接给宿主一次；若本次启动进入时已经是 `rollback_required`，launcher 不再自动重启，避免失败代次无限循环。此时在线受限服务只提供状态与受控退出；用户安全退出后，下一次显式启动再做一次恢复尝试，不在业务服务在线时执行危险回滚。

预检和实际迁移都要求“源数据估算值 + 安全余量”不超过目标卷可用空间；安全余量为 64 MiB 与估算值 5% 中较大者。无法读取磁盘空间不是成功或警告，而是阻断错误。

旧目录清理再次校验当前根、目标根和锚点边界：

- 当前根、锚点、目标根及它们的祖先或子目录都不能作为旧根删除；只有“旧运行根恰好就是锚点”这一历史兼容场景允许选择性删除锚点内的运行条目，锚点本身和固定状态始终保留；
- 旧版本可能写在 selected root 顶层的 `community_auth.json`、`social_session.json` 和仍有效的 OAuth/Steam pending，必须先无覆盖地发布到固定私有状态并再次核验，之后才能删除旧副本；损坏凭据、目标落盘失败或活动中/无法验证的 social lock 都必须中止整次清理；
- 过期或损坏的项目自有 pending 可以在用户明确发起清理时删除，但 `.lock` 永不迁移。wall-clock 年龄不能证明一个挂起或慢 I/O 进程已放弃所有权；空文件、损坏 JSON、身份探测失败、权限拒绝或无法确认来自本机的 lock 一律保留并继续阻断；
- 新 social lock 必须先在同目录临时文件中完整写入并 `fsync`，再用 no-replace 原语一次性公开：backend 在 Windows 使用 Python 明确拒绝覆盖目标的 `os.rename`、在 macOS/Linux 使用 hard-link；PC 的 Node/libuv `rename` 在 Windows 可能覆盖目标，因此三平台统一使用同卷 hard-link。文件系统不支持时必须 fail-closed，不能退回覆盖 rename 或“先创建公开空 lock、再填内容”的窗口。公开记录包含兼容 `token`、PID 和可用时的启动身份；
- 自动接管必须同时满足三项：lock 是可完整解析的记录、OS 确证 PID 已消失或同 PID 的可靠启动身份已经变化、当前进程持有唯一 recovery authority。PID 存活且身份一致继续视为活动；证据 unknown 时 fail-closed；
- orphan recovery 按 lock 中不可变的 `owner_kind` 分区：backend 只接管 `neko` 和缺少该字段的历史 lock，PC 只接管 `pc` lock，任何未知类型均 fail-closed。这样即使 remote 标记与仍存活的本地 backend 重叠，两端也不会对同一个 orphan 执行 compare-delete。backend 覆盖 canonical、legacy 和 retained 路径，且只有在 launcher 单实例锁已正向证明后才能接管，并在重验、删除和新 lock 原子发布期间持有固定 runtime-state 下的 `flock`/`msvcrt` 恢复锁及进程内互斥锁；PC 还必须持有 Electron 单实例锁；
- Windows 使用进程启动时间、Linux 使用 boot ID 与 `/proc/<pid>/stat` starttime、macOS 使用 `ps lstart` 关闭 PID 复用窗口；缺少相同方案的可靠启动身份时只接受“PID 已确证不存在”，不把可疑差异当作死亡。固定私有状态的合同仅覆盖本机 userData，本版本没有跨主机身份，明确不支持把该目录配置到跨主机共享文件系统；若未来支持，必须先禁用自动接管或增加可靠主机身份，不能拿本机 PID 证据判断远端 owner；
- social session 的多个路径锁按物理父目录身份统一排序；正常释放仍只删除本进程持有且 token/文件身份匹配的 lock。旧格式只有在 token 可解析出 PID且唯一 authority 确证 PID 已不存在时才可接管；历史空/坏 lock 无法自动证明 owner，保留安全退出并要求显式人工恢复；
- 所有旧根都只删除权威清单中的受管入口；未知文件、导出、笔记或未来版本数据一律保留；
- 受管入口清理后，非锚点旧根仅在已经为空时删除；锚点本身始终保留；
- 第一次删除前先把 `retained_source_mode=cleanup_in_progress`、私有文件摘要和旧根设备号/inode 持久化；落盘失败时零删除。重试只能清理同一物理目录，原路径被复用为另一真实目录时必须停止；
- POSIX 清理逐级以 `O_NOFOLLOW` 打开并固定目录句柄，运行条目和社区私有文件都只通过该句柄的相对路径访问。即使旧根或祖先在清理期间被改名、替换或改成链接，也不能转而删除新路径指向的数据；
- Windows 的 Python 标准库不能提供与 POSIX `dir_fd` 等价的 handle-relative 安全删除合同，因此当前不显示自动清理入口，也不写清理意图，只提示用户手动清理保留目录。不能退回到普通 `rmtree(path)`；
- 兼容读取只从已完成检查点确认的 retained/source 导入，target 只能作为冲突见证，不能在源凭据缺失时反向成为权威。`cleanup_in_progress` 和 `cleaned` 的旧根都禁止懒导入；
- logout 在删除任何权威凭据前必须证明已提交 selected root 可访问；外置盘离线时零删除，重新挂载后才允许重试。删除顺序为旧副本在前、固定权威在后，任一步失败立即停止。

清理事实以文件系统中的受管条目为准：如果删除已完成而检查点或 `root_state` 落盘失败，接口仍返回成功并标记 `metadata_persisted=false`；后续状态不得因为陈旧的 `legacy_cleanup_pending` 或 `last_migration_backup` 再显示假待清理。旧根不可访问与旧根不存在必须区分，前者继续 fail-closed。

## 7. HTTP 合同

路由前缀为 `/api/storage/location`，所有路由声明都不带尾部斜杠。

| 方法与路径 | 用途 | 是否允许改变状态 |
| --- | --- | --- |
| `GET /bootstrap` | 首次加载或受限启动所需的完整快照 | 否；默认不执行持久化 reconcile |
| `GET /status` | 维护轮询和完成提示 | 否；默认不执行持久化 reconcile |
| `GET /diagnostics` | 布局、运行时读写路径和状态诊断 | 否 |
| `GET /retained-source` | 查询可清理的旧目录 | 否 |
| `POST /pick-directory` | 浏览器环境下的原生/后端目录选择回退 | 只调用选择器，不提交策略 |
| `POST /open-current` | 打开后端当前运行根目录 | 不改变存储状态 |
| `POST /select` | 首次启动或恢复阶段校验选择并生成一次性重启预检；不提交持久状态 | 是 |
| `POST /preflight` | 常驻入口迁移预检 | 否；只做临时写探测并立即删除，不持久化业务状态 |
| `POST /restart` | 建立迁移或 `rebind_only` 意图，并请求受控重启 | 是，必须串行化和可回滚 |
| `POST /retained-source/cleanup` | 清理预期旧目录 | 是，必须在锁内重新验证目标 |
| `POST /exit` | 阻塞/维护页退出应用 | 是；要求 `X-Neko-Storage-Action: exit` |

关键约束：

- 只读端点的 `persist_reconcile` 默认必须为 `false`；
- `/preflight` 返回空间、权限、非空目标和风险提示，但不写策略、检查点或根状态；
- `/restart` 必须在 `_storage_mutation_lock` 内重新验证，不能信任较早的预检结果；
- 磁盘写通过工作线程执行时，取消请求不能提前释放互斥锁；
- 本地状态目录不可用并导致云存档被禁用时，状态读取仍可返回 ready，但所有存储变更端点必须拒绝执行。
- `/bootstrap` 和 `/status` 返回 `autostart_csrf_token`；包括 `/preflight` 在内的所有 POST 都必须同时通过本机 Origin/Referer 与 `X-CSRF-Token` 校验。Electron 和普通浏览器回退路径使用同一合同。
- `/select` 和 `/restart` 在 `rollback_required` 时返回 `409 storage_rollback_required`，不能覆盖检查点或事务证据；`/exit` 仍然允许受控关闭。
- `/restart` 的关闭回调失败且状态恢复不完整时返回显式的结果未知/`awaiting_shutdown`；Web 只可重试 `/exit`，不能重复建立迁移事务。
- `/api/system/status` 的策略或存储探针异常必须返回显式 `storage_policy_unavailable`/`storage_status_unavailable`，不能伪装为会无限重试的普通 `starting`。
- `/api/system/status` 与 `/api/storage/location/status` 必须返回相同进程的 `instance_id`、canonical lifecycle、`migration_phase` 和 `shutdown_retry_allowed`。
- 策略、迁移检查点或 root state 无法可靠读取时，`/status` 仍要返回带 CSRF token 的 `storage_status_unavailable`；Web 保持主功能阻断并显示“安全退出”。`/exit` 不修复、不覆盖这些文件，只请求受控关闭；PC 若仍能证明受管迁移代次活动则继续拒绝退出，否则按 safe-exit-only 恢复态处理。

## 8. N.E.K.O 启动与 Web 交互

### 8.1 受限启动

首次选择、迁移和恢复期间，主服务与记忆服务可以先以受限模式启动：

- 主服务只放行存储页面、静态资源、状态/健康检查、存储 API 和调试入口；普通 API 返回 `409 storage_startup_blocked`；
- 记忆服务只放行 `/health`、`/shutdown`、`/internal/storage/startup/continue` 和 `/internal/storage/startup/block`；Agent 只放行健康检查和同一组存储启动控制端点；其他请求返回 409；
- 内部 continue/block 端点只保留为异常嵌入与旧代补偿边界，并用单调代次防止超时或取消后的迟到初始化覆盖新阻断；网页存储选择不得调用这条同代释放链，而必须走受控重启，使下一代 launcher 统一掌握 phase-0 顺序。

完整性恢复代次比普通首次选择更严格：`ConfigManager` 不运行默认配置、旧配置或根目录迁移；launcher 不自动安装 Playwright 浏览器；main 不初始化 voice、Avatar Tool 和后台业务运行时；Agent 不启动 token tracker、插件宿主或 LLM 探测，也不发外部请求。普通 main、memory 和 Agent API 都返回 409，只保留健康检查、存储状态/bootstrap、受控安全退出以及服务间恢复控制所需的最小白名单。退出钩子不得写 token、角色释放、插件状态、记忆状态、`root_state` 或上传 cloudsave；launcher 也不得在三个服务 ready 后把 `root_state` 改回 normal。允许在固定锚点写诊断日志，但受损权威文件和已提交用户数据必须保持逐字节不变。

打包版默认走 merged launcher，因此上述约束不能只存在于源码多进程分支：merged 和 multi 都必须在同一布局解析之后才 import/启动服务，并共享同一个恢复标记、API 门禁和退出不持久化合同。N.E.K.O-PC 看到受限服务 ready 只表示恢复表面可用，不表示业务 ready；任何互相矛盾的 `ready:true` 都不能覆盖 maintenance、recovery、selection 或完整性阻塞。

### 8.2 首页入口

共享控制器位于 `static/app/app-storage-location.js`。首页通过 `window.appStorageLocation`、`window.__nekoStorageLocationStartupBarrier` 和 `waitUntilMainUiAllowed` 与正常初始化衔接。

页面先查询系统状态，只在被阻塞时获取完整 bootstrap，然后展示对应阶段：

- 首次选择；
- 重启前预览与已有内容确认；
- 维护遮罩和 `/status` 轮询；
- 完成提示、打开新/旧目录和旧目录清理。

维护轮询同时兼容 `/storage/location/status` 顶层字段与 `/system/status` 的 `storage.*` 字段。每次重启轮询都分配 generation，旧请求、旧 bootstrap 和旧定时器不能覆盖新的外部维护事件。维护页不能因第一拍同实例、缺失身份或字段互相矛盾的 `ready` 立即 reload；必须先观察到明确迁移阻塞，或确认响应来自不同 `instance_id`，且当前响应没有任何 pending/publishing/阻塞字段。预检还要生成单次 `restart_operation_id`：`/restart` 在进入互斥队列前认领为 `in_flight`，状态轮询按该 ID 读取权威阶段；结果未知时只允许取消仍为 `prepared` 的预约，已在途或已受理的操作不能由网页猜测取消。结果未知且带操作 ID 时，其他标签页产生的迁移阻塞不能替当前操作背书：同一实例只允许当前 ID、目标和实例完全匹配且状态为 `cancelled/rejected/expired` 后恢复 ready，或在 `indeterminate` 等终态下进入相应恢复/选择面；成功请求则必须看到新的 `instance_id`。进入 `failed`、`recovery_required` 或 `selection_required` 时必须重新读取 bootstrap 后再切换选择页，不能复用迁移前快照；`rollback_required` 则保持业务门禁，显示真实错误和“安全退出并在下次启动恢复”，不能画成仍会自动重启的假进度。

所有状态、退出和迁移变更请求都必须有界；截止时间覆盖 `fetch()`、响应体读取和 JSON 解析，而不只覆盖响应头。`/select` 或 `/restart` 在网络超时后属于“结果未知”，前端必须查询当前状态确认事实，不能直接重试一次可能已经成功的变更。

### 8.3 记忆浏览器常驻入口

记忆浏览器入口由以下文件共同维护：

- `templates/memory_browser.html`
- `static/css/memory_browser.css`
- `static/js/memory_browser.js`
- `static/app/app-storage-location.js`

页面必须 bootstrap-first：受限时不启动普通记忆数据加载，只渲染存储占位和恢复 UI。系统已经 ready 时，“更改存储位置”使用 `/preflight` 和 `/restart`，不能调用首次启动语义的 `/select`。

重启请求被接受后，页面立即锁定控件，并通过以下方式通知其他页面进入维护：

- `BroadcastChannel("neko_storage_location_channel")`；
- 向 opener/parent 发送 `postMessage`；
- Electron 存在时调用 `window.nekoHost.closeWindow()`；
- 无法关闭时，在当前页启用共享维护遮罩继续轮询。

目录选择和打开路径的调用顺序为宿主优先、后端回退。普通浏览器环境必须仍然可工作。

## 9. N.E.K.O-PC 集成合同

### 9.1 启动门禁

`src/main/storage-gate.js` 轮询 N.E.K.O 的 `/api/system/status`；维护期间优先查询 `/api/storage/location/status`，两者互为回退。当前轮询间隔为：

- 未 ready：1000 ms；
- ready：3000 ms；
- 单次请求超时：2500 ms。

PC 只把后端字段映射为 `checking`、`selection_required`、`maintenance`、`recovery_required`、`ready` 或 `backend_unreachable`。每次启动轮询都分配单调递增的 generation；异步响应、回退请求、UI 更新和下一次定时器都必须仍属于当前 generation，旧轮询不能在 stop/start 或维护切换后覆盖新状态。`guardStorageStartupGate()` 在 ready 前阻止聊天、字幕、Agent HUD、点唱机等卫星窗口动作，并把焦点带回 Pet 窗口。托盘 Reload、移动模式和直播模式也必须在修改配置、停止轮询或重建窗口之前经过同一门禁，不能销毁正在承担进度与兜底职责的维护页。

迁移进入维护时，PC 记录当前可见的 compact/full chat、字幕、Agent HUD 和点唱机，隐藏或暂停它们；后端恢复 ready 后，PC 通过文档重载 fence 刷新 Pet，再恢复之前可见的卫星窗口。

### 9.2 通用宿主桥

`src/preload/shared/common.js` 只暴露通用能力：

- `window.nekoHost.pickDirectory()`
- `window.nekoHost.openPath()`
- `window.nekoHost.closeWindow()`
- `window.nekoHost.getBackendRecoveryState()`
- `window.nekoHost.retryBackendRecovery()`
- `window.nekoHost.requestSafeQuit()`

对应 IPC 处理器位于 `src/main/storage-gate.js`：

- `neko:host:pick-directory`
- `neko:host:open-path`
- `neko:host:close-window`
- `neko:host:get-backend-recovery-state`
- `neko:host:retry-backend-recovery`
- `neko:host:request-safe-quit`

preload 不得暴露 `window.nekoStorageLocation`，也不得实现路径是否合法、目标是否可覆盖或迁移处于哪个业务阶段。Pet preload 发送的 `neko:storage-location-phase` 只接受当前 Pet 主窗口 sender，且只是用于更快保护窗口的尽力通知；后端轮询仍是最终权威。

宿主恢复状态和允许操作为：

| 宿主状态 | 含义与允许操作 |
| --- | --- |
| `ready/backend_ready` | 后端正常；非维护页可以先走后端受控退出再关闭。维护页不得因刚进入页面时的一拍陈旧 ready 而退出 |
| `ready/backend_recovery_required` | 在线 `rollback_required`，没有迁移线程继续运行；不提供本地 retry，后端 `/exit` 成功后可以普通关闭，下次启动恢复 |
| `ready/unmanaged_backend` | attached/remote 后端不归 PC 管理；只关闭 PC 壳，不请求外部后端退出，也不启动本地替代进程 |
| `active/owner_handoff_awaiting_shutdown` | 在线 pending，迁移尚未开始；只允许 Web 重试后端 `/exit`，普通退出、本地 retry 和 replacement 均禁止 |
| 其他 `active` / `transient` | 受管迁移代次或 replacement 仍在运行；Web 不得调用 `/exit`，IPC、原生关闭/Alt+F4 和 loading 窗口关闭都必须拒绝 |
| `terminal` | 已没有仍在执行迁移的受管代次；只允许 `requestSafeQuit()`，仅在所有权清算有明确证据时才允许 `retryBackendRecovery()` |

renderer 传入的布尔值不能作为“后端已经安全退出”的凭据。关闭与重试权限必须由 main 进程根据 ownership、代次和进程退出事实计算。

### 9.3 重启所有权

N.E.K.O-PC 启动后端时设置 `NEKO_OWNER_RELAUNCH=1`。launcher 完成迁移或 rebind 后发出 `NEKO_EVENT storage_migration_restart`，其中 `relaunch=owner` 表示把下一代交回桌面属主：

- POSIX 先关闭 supervisor 的父管道保持端，并在有界时间内等待旧 launcher 的 `close`；
- Windows 必须先确认本代收到 `NEKO_JOB_OK`，再释放对应 Job Object holder，并确认 holder 退出；holder 未建立、分配失败、提前退出或退休超时都不能证明旧代已经清空；
- 清除旧代 ownership、shutdown deadline 和并发启动状态后，只调用一次统一的 `startPythonBackend()`；
- 新一代仍须通过既有 `startup_ready`/健康检查，才解除启动门禁；失败保持失败提示，不能把旧代事件当成新代就绪。

旧 launcher 没有在 handoff deadline 内关闭时，PC 只能执行已有的 ownership-aware 清算；仍不能确认停止时进入 `terminal`、保留 ownership/lease、禁用 retry，绝不能在旁边启动新代。冷启动期间发生 handoff 时，replacement 的成功或失败必须结算原来的 startup Promise，不能让 loading 窗口永远等待。

没有声明 owner relaunch 的宿主仍由 N.E.K.O launcher 使用 self relaunch 兼容路径。

launcher 的 stdout 与普通后端/模型日志共用，因此 `NEKO_EVENT` 不是“看起来像 JSON”就可信的日志。PC 只接受行首精确前缀、JSON 对象、`source=neko_launcher`、非空随机 `launch_id`；`startup_begin` 建立本进程会话，后续事件必须严格匹配该 ID，非活动 ChildProcess、行中嵌入前缀、缺失/错误 source 或 ID 的内容全部忽略。macOS/Linux 的 `NEKO_SUPERVISOR_CHILD_EXIT` 不经过共享 stdout：POSIX spawn 为 supervisor 提供专用 fd4 控制管道，真实 backend 与 watcher 显式关闭 fd4，只有 wrapper 能写退出帧，Electron 也只从当前代 `stdio[4]` 读取。这样普通日志即使输出完全相同的独立行，也不能伪造 child-exit 证据或提前释放持久化关闭屏障。

### 9.4 平台生命周期差异

| 平台 | 旧代清算证据 | 文件系统边界 | 系统退出与普通退出 |
| --- | --- | --- | --- |
| Windows | 当前代 `NEKO_JOB_OK` + 对应 Job holder 已退出；单独 launcher `exit/close` 不足以证明 descendants 消失 | 拒绝 symlink 与所有 reparse point/junction；目录 flush 仅 best-effort | 普通 Alt+F4、菜单、托盘和更新重启受门禁；`query-session-end` 不阻塞 OS，并启动有界 ownership-aware 清算 |
| macOS | POSIX supervisor 父管道关闭、进程组及端口/身份清算 | 拒绝完整路径链上的 symlink；存在路径用 `samefile`/设备号+inode 识别默认 APFS 大小写别名；目录 `fsync` | Cmd+Q、应用菜单、窗口关闭受门禁；`powerMonitor.shutdown` 允许 OS 继续并执行有界清算 |
| Linux | POSIX supervisor/进程组；X11、Wayland、Niri 的窗口输入策略不能改变后端所有权结论 | 拒绝完整路径链上的 symlink；大小写敏感文件系统保持词法大小写语义；目录 `fsync` | window-all-closed、托盘、X11/Wayland 原生关闭受门禁；`powerMonitor.shutdown` 和进程信号走有界清算 |

attached/remote 模式在三个平台都没有本地进程所有权；状态异常时只能保持门禁或关闭桌面壳，不能杀远端服务、清本地 lease 后冒充已退出，也不能启动替代实例。

## 10. 并发与恢复不变量

以下行为属于必须由测试保护的硬约束：

1. 所有存储变更必须经过同一把进程内互斥锁。
2. 状态前像、策略/检查点/根状态写入必须位于同一根状态事务中。
3. `/restart` 在锁内重新预检；预检结果只是 UI 信息，不能当作写入授权。
4. 请求取消后要等待后台磁盘任务真正结束再释放锁。
5. 只读轮询不能持久化 reconcile，避免覆盖并发失败恢复。
6. 受控退出未被接受时必须恢复所有已写状态。
7. 策略提交必须晚于复制验证。
8. 目标原有受管入口在任何失败路径上都必须恢复，目标未知入口不得被修改；发布前须复用同一份基线逐项 CAS，最终切换不得覆盖检查后才出现的外部数据。
9. 清理必须比较请求中的预期旧根与锁内重新读取的当前旧根，并只删除清单入口。
10. 所有会改变本机状态的 Web 请求必须通过本机来源和 CSRF 校验。
11. PC 维护保护必须恢复迁移前真实可见的窗口集合，不能把原本关闭的窗口打开。
12. PC 只能让当前 polling generation 和当前 launcher generation 改变状态。
13. 任何旧代退出或 Job holder 缺失都不能自动等价为“所有后端子进程已停止”；只有平台所有权屏障的肯定证据才能启动 replacement。
14. Web 在宿主 `active/transient` 时不得先请求后端 `/exit`；宿主明确拒绝关闭时也不得回落到 `window.close()`。
15. attached/remote 后端永远不能被 PC 当作本地所有权恢复对象。
16. ConfigManager、launcher、两个状态 API 与 PC 状态轮询都不能把坏策略、坏检查点或错误实例降级成默认 ready。
17. 维护页只有在见过迁移阻塞、确认后端实例已更换，或同一预检操作被后端明确终结且当前无检查点后，才能接受无矛盾字段的 ready；连续 ready 次数不是操作终态证据。
18. stdout 中的 launcher 控制事件必须绑定 launcher source、活动 ChildProcess 和随机 launch ID；POSIX supervisor 的 child-exit 证据只能走真实 backend 无法继承的专用控制 FD，普通日志永远不是退出控制通道。
19. 交互式退出在迁移活动期一律阻止并聚焦维护界面；OS 关机/注销和进程信号不能无限阻塞系统，只执行有界、所有权感知的清算。
20. 社区凭据、会话和一次性 PKCE 状态不能随 selected root 漂移；兼容旧文件时必须先无覆盖发布并验证新权威，再删除旧副本，且不得迁移活动 lock；一次性 pending 消费成功前必须删除所有已接受的同记录副本，并证明本次至少实际 claim 一份。
21. 清理状态必须由旧根中仍存在的受管数据推导；未知文件应保留，但不能单独制造永久的假待清理状态。
22. 旧根清理必须先持久化私有摘要和物理目录身份，再以固定目录句柄执行；原路径被复用、身份不符或平台缺少安全删除能力时一律零删除。
23. 存在路径的相等与包含关系以物理身份为准；macOS 默认 APFS 的大小写别名不能绕过同源、嵌套和当前根保护，Linux 不得被无条件大小写折叠。
24. 已完成迁移中的 retained/source 是旧私有状态唯一导入候选；target 仅参与冲突检测。外置 selected root 不可用时 logout 必须保持所有副本不变。
25. 策略、检查点或 root state 不可判定时不能永久只显示假进度；状态端点必须保留证据并提供受 ownership 约束的安全退出，所有正常业务与迁移写操作继续阻断。

## 11. 维护入口与代码锚点

### N.E.K.O

| 主题 | 代码位置 |
| --- | --- |
| 根目录绑定与平台候选 | `utils/config_manager/storage_roots.py` |
| 布局解析与环境变量 | `utils/storage/layout.py` |
| selected root 权威数据清单 | `utils/storage/entries.py` |
| 固定社区私有状态与旧根清理摘要 | `utils/storage/community_private_state.py` |
| 策略、规范化和路径验证 | `utils/storage/policy.py` |
| 检查点、迁移、核验和清理 | `utils/storage/migration.py` |
| 启动阻塞快照 | `utils/storage/location_bootstrap.py` |
| workshop 路径重写 | `utils/storage/path_rewrite.py` |
| HTTP API 与事务边界 | `main_routers/storage_location_router.py` |
| 启动前迁移和重启事件 | `launcher_core/runtime.py` |
| 主服务门禁 | `app/main_server/` |
| 记忆服务门禁 | `app/memory_server/` |
| 共享 Web 状态机 | `static/app/app-storage-location.js` |
| 记忆浏览器入口 | `templates/memory_browser.html`、`static/js/memory_browser.js` |

`utils/storage_layout.py`、`utils/storage_policy.py`、`utils/storage_migration.py`、`utils/storage_location_bootstrap.py` 和 `utils/storage_path_rewrite.py` 是历史导入兼容别名。新实现应放在 `utils/storage/` 包中；兼容别名及 monkeypatch 目标由 `tests/unit/test_storage_package_compatibility.py` 保护。

### N.E.K.O-PC

| 主题 | 代码位置 |
| --- | --- |
| 后端状态轮询、启动门禁、维护窗口保护、宿主 IPC | `src/main/storage-gate.js` |
| 组件注入与受保护动作 | `src/main.js`、`src/main/hotkey-manager.js`、`src/main/tray-menu.js` |
| 通用 preload 宿主桥 | `src/preload/shared/common.js` |
| Web 维护阶段通知 | `src/preload/bridges/pet-input-region-bridge.js` |
| launcher 事件接收 | `src/main/backend-runtime.js` |

## 12. 变更检查清单

### 新增或调整运行时目录

不能只在 `ConfigManager` 中增加一个目录属性。至少检查：

1. 是否属于用户选择的运行根，还是固定锚点；
2. 是否需要加入 `RUNTIME_STORAGE_ENTRIES`，并标明用户数据或运行缓存；
3. 是否需要路径重写或额外一致性验证；
4. 非空目标替换规则是否安全；
5. 精确锚点旧根清理是否应删除它；
6. `/diagnostics` 是否需要报告它；
7. 云存档包含/排除边界是否因此变化；
8. 成功、失败、恢复和清理测试是否覆盖。

### 调整 API 或状态

同时核对：

- N.E.K.O router、bootstrap 和共享 Web 控制器；
- 首页与记忆浏览器两个入口；
- 主服务和记忆服务的受限路由；
- N.E.K.O-PC `deriveStorageGateState()` 与维护窗口恢复；
- `test/integration/neko-web-contract.test.js` 的跨仓库合同。

### 调整重启方式

任何 owner/self relaunch 协议调整都必须同步验证 N.E.K.O-PC 的进程采纳状态机以及 Windows、macOS 和 Linux 下：

- 旧后端确实退出；
- 新后端只启动一次；
- loading/Pet 窗口绑定到新一代；
- stdout/stderr、job/supervisor 关系和退出语义正确；
- 失败后仍能从检查点恢复。

不要用固定 wall-clock timeout 强杀正在做文件 I/O 的迁移线程，尤其不能在 `publishing` 阶段中断原子发布/回滚。当前未解决的“进程仍存活但底层磁盘或网络文件系统永久不返回”需要独立受监督迁移进程、分阶段 heartbeat/checkpoint，以及只在安全 copy 边界生效的协作取消协议；在这套协议落地前，UI 必须继续 fail-closed 并允许 OS 级有界退出，不能猜测迁移已经结束。

## 13. 验证

N.E.K.O 的核心回归：

```bash
uv run pytest \
  tests/unit/test_storage_layout.py \
  tests/unit/test_storage_policy.py \
  tests/unit/test_storage_migration.py \
  tests/unit/test_storage_location_bootstrap.py \
  tests/unit/test_storage_location_router.py \
  tests/unit/test_community_private_state.py \
  tests/unit/test_system_status_router.py \
  tests/unit/test_storage_path_rewrite.py \
  tests/unit/test_storage_package_compatibility.py \
  tests/frontend/test_storage_location_startup.py
```

涉及主/记忆服务门禁时，再运行对应的 server startup/limited-mode 测试。涉及文档站时，在 `docs/` 中运行 `npm ci` 和 `npm run build`。

启动验收还必须使用隔离锚点和运行根，分别注入坏策略、坏迁移检查点和坏 `root_state`，并从 launcher 入口验证：三个健康检查可达、存储状态为对应完整性阻塞、普通 API 返回 409、安全退出能在有界时间完成、端口全部释放。验收前后应比较受损权威文件和用户数据哨兵的内容摘要，确认没有被“修复”、覆盖或迁移到猜测目录。该验收可以使用源码入口验证状态机，但发布前仍须在目标平台对实际携带的二进制重复；两者不能互相替代。

N.E.K.O-PC 的核心回归：

```bash
node --test \
  test/backend-runtime-ownership.test.js \
  test/exit-retention-dialog-contract.test.js \
  test/external-close-quit-contract.test.js \
  test/main-composition-contract.test.js \
  test/storage-window-display-contract.test.js \
  test/widget-mode-tray-contract.test.js \
  test/update-check-service.test.js
NEKO_WEB_REPO_PATH=/path/to/N.E.K.O \
  node --test test/integration/neko-web-contract.test.js
```

涉及 main 组合、托盘或快捷键保护时，再运行相应 contract tests；准备交付桌面包时按平台执行完整 `npm test`、lint 和打包验证。

## 14. 维护判定标准

一次存储位置修改只有在以下事实同时成立时才算完成：

- 最终交付以打包版为准：Nuitka 后端必须包含当前 Python 状态机与 `static/app/app-storage-location.js`，Electron 包必须携带对应平台的 `bin/projectneko_server[.exe]` 并从该入口建立 owner handoff；源码模式验证不能替代这条打包合同；
- 新旧根目录的读写边界明确；
- 迁移中断不会让已提交策略指向未验证目标；
- 已有目标内容能在失败和崩溃恢复路径上原样恢复；
- 首次启动、常驻入口和恢复入口没有混用写接口；
- 普通浏览器与 N.E.K.O-PC 宿主环境都能走通；
- 维护期间普通业务不会抢跑；
- 请求半开、owner handoff 失败和回滚失败都有可见终态，且不会无限自动重启；
- 活跃迁移不可由 renderer、原生关闭或外部窗口生命周期绕过；
- 旧目录只有在用户明确操作且后端复核后才会清理；
- 两个仓库的合同测试与本文同步更新。
