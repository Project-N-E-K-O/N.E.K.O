# 云存档同步优化概览（v1 技术评审版）

> 关联文档：`cloud-save-sync-optimization-plan.md`
> 目标：给技术评审提供一份短版结论，明确当前实现基线、v1 同步边界、核心流程和实施顺序。

---

## 1. 核心结论

- 本方案不是“把整个用户目录交给 Steam Cloud”，而是先建立独立的 `cloudsave/` 导出层，再让 Steam v1 只同步允许跨设备漫游的小文件与关键状态。
- 运行时与云端都继续按猫娘档案名组织，不再引入 `character_id`。
- v1 对同名角色仍按档案名直接应用云端覆盖。
- 当前角色状态与模型资源已经基本分开存储，v1 不要求先大改底层目录结构。
- v1 默认同步猫娘目录、角色配置、全局对话设置、记忆、模型绑定摘要、小型参数 / 映射文件和 Workshop 轻量元数据。
- Live2D / VRM / MMD / Workshop 等大资源文件在 v1 默认不进入云端；目标设备若已有对应资源则直接复用，没有则由用户手动导入或重新下载恢复。
- `time_indexed.db` 在 v1 只以 shadow copy 方式进入 `cloudsave/`，Steam 不直接观察运行中的 SQLite。
- 导入导出必须经过版本闸门、staging、哈希校验、原子替换和覆盖前备份，保证安全性与可回滚性。
- 删除角色必须写 tombstone；卸载模型资源与删除角色本人必须分离。
- 启动链路必须先完成云端导入判定，再让依赖角色、记忆和模型配置的服务进入正常写态。
- 云存档 v1 必须与现有角色卡导出 / 导入语义显式分离；角色卡继续是“分享 / 手工备份”通道，不能复用为云存档整包回灌。
- 启动期必须把云导入与 Workshop 角色卡后台同步串行化，避免并发改写 `characters.json` 和重复触发 `initialize_character_data()`。
- 与角色体验强相关的小配置，如 `touch_set`、VRM 打光、MMD 的 `lighting / rendering / physics / cursor_follow`，必须在 v1 中被明确归入绑定摘要或显式排除，不能保持未定义状态。
- 由于 v1 直接按档案名作为云端键，必须补齐档案名跨平台安全规则、大小写 / Unicode 规范化冲突检查，以及根路径切换的恢复机制。

---

## 2. 当前实现基线

这一节只描述项目当前代码已经存在的事实。

- Windows 侧主写入根已基本收敛到 `%LOCALAPPDATA%/N.E.K.O`，并保留从历史 `Documents` 迁移旧档的能力。
- macOS / Linux 当前代码仍主要使用 `Documents` 或 `cwd` 作为候选路径，因此“统一到 Application Support / XDG 数据目录”仍是阶段 0 目标，不是现状。
- 运行时主要目录包括：
  - `config/`
  - `memory/`
  - `live2d/`
  - `vrm/`
  - `mmd/`
  - `workshop/`
  - `character_cards/`
- 角色配置当前聚合在 `config/characters.json`，记忆主要在 `memory/<name>/...`，长期记忆使用 `time_indexed.db`。
- `user_preferences.json` 当前同时存放设备相关偏好和 `__global_conversation__` 对话设置，因此文档里只能把“拆分 `conversation_settings.json`”写成 v1 改造项。
- 模型资源当前同时可能来自项目内置、用户导入和 Workshop 路径，因此云存档层应同步绑定摘要与来源信息，而不是假设只有单一资源目录。
- 角色当前还有一批体验相关的小配置直接保存在 `characters.json` 的 `_reserved` 中，例如 `touch_set`、VRM 打光和 MMD 运行参数。
- 项目当前已经有“角色卡导出 / 导入并可附带模型资源”的链路，它与云存档 v1 的“小文件漫游”不是同一语义。
- `main_server` 启动后会后台执行 Workshop 角色卡同步，这条链路会改写 `characters.json` 并触发角色重载。
- 当前代码里还没有 `root_state`、`last_known_good_root`、导入完成屏障和 tombstone 应用顺序等正式实现。

---

## 3. v1 同步边界

### 3.1 默认同步的小文件与关键状态文件

| 类别 | 导出形态 | 说明 |
| --- | --- | --- |
| 猫娘目录与当前选择 | `catalog/catgirls_index.json`、`catalog/current_character.json` | 先恢复“有哪些猫娘”和“当前是谁” |
| 角色删除墓碑 | `catalog/character_tombstones.json` | 防止旧设备把已删除角色重新导出 |
| 角色配置 | `profiles/characters.json` | v1 仍允许聚合导出 |
| 全局对话设置 | `profiles/conversation_settings.json` | 来自 `user_preferences.json` 的拆分目标 |
| 模型引用摘要 | `bindings/<character_name>.json` | 同步模型来源、资源状态和回退信息 |
| 角色体验保留配置 | `bindings/<character_name>.json` | 包含 `touch_set`、VRM / MMD 小型体验配置等与绑定强相关的小状态 |
| 角色记忆 JSON | `memory/<character_name>/...` | 包含 `recent.json`、`settings.json`、`facts*.json`、`persona*.json`、`reflections*.json`、`surfaced.json` |
| 长期记忆快照 | `memory/<character_name>/time_indexed.db` | 通过安全 shadow copy 导出 |
| 小型参数 / 映射文件 | `overrides/...` | 参数文件、情绪映射等默认同步 |
| Workshop 轻量元数据 | `meta/workshop/*.workshop_meta.json` | 用于来源识别和恢复提示 |

### 3.2 v1 默认不进入云端的大文件

| 类别 | 内容 | v1 策略 |
| --- | --- | --- |
| Live2D 模型本体 | 模型文件、贴图、动作等 | 不进入云端；目标设备已有则直接复用，没有则手动导入 |
| VRM 模型本体 | `.vrm`、贴图、动画等 | 不进入云端；目标设备已有则直接复用，没有则手动导入 |
| MMD 模型与动作资源 | `pmx` / `pmd` / `vmd` / 音频等 | 不进入云端；目标设备已有则直接复用，没有则手动导入 |
| Workshop 下载资源与缓存 | Workshop item 资源、缓存包 | 不进入云端；目标设备已有则直接复用，没有则重新下载恢复 |

### 3.3 始终不纳入同步

| 类别 | 内容 | 原因 |
| --- | --- | --- |
| 敏感配置 | `core_config.json` 中的 API Key、Provider 地址、Cookie、密钥 | 安全红线 |
| 本机设备参数 | 窗口位置、显示器、视口、相机参数 | 跨设备会错位 |
| 本机路径配置 | `workshop_config.json`、Workshop 路径缓存 | 换机后失效 |
| 运行态文件 | `token_usage.json`、`.telemetry_unsent.json`、`agent_quota.json` 等 | 会污染统计和配额 |
| 插件目录 | `plugins/<plugin_id>/...` | v1 默认排除 |
| 语音存储 | `config/voice_storage.json` | 当前与 API Key 相关，不适合直接漫游 |
| 普通角色卡资产 | `character_cards/*.chara.json`、角色卡 PNG / `.nekocfg` 等 | 属于分享 / 手工备份通道，不属于云存档真源 |

### 3.4 需要轻量整理的内容

- 保持运行时与云端都按档案名组织，不再额外引入角色稳定 ID。
- 将 `user_preferences.json` 中的全局对话设置拆到 `profiles/conversation_settings.json`。
- 在导出层补 `bindings/<character_name>.json`，表达模型绑定、来源和资源状态。
- 将 `touch_set`、VRM / MMD 小型体验配置并入 `bindings/<character_name>.json` 的派生结果，避免继续散落在 `_reserved`。
- 对小型参数 / 映射文件建立白名单，必要时只在导出层归一化到 `overrides/`。
- 对 `time_indexed.db` 采用 checkpoint + shadow copy 导出。
- 把档案名安全规则、大小写 / Unicode 规范化冲突和当前角色回退策略写死，避免不同模块各自实现。
- 把 `conversation_settings.json` 和 `bindings` 的迁移期兼容契约写死，避免形成双真源。
- 把角色卡导出 / 导入与云存档导出 / 导入的语义边界写死，禁止后续实现混用。

---

## 4. 目标架构与流程

### 4.1 目录分层

- `Local Root` 是运行时真实读写根。
- `cloudsave/` 是唯一云端导出目录。
- Steam v1 只监控 `cloudsave/`。
- v1 建议的导出结构包括：
  - `manifest.json`
  - `catalog/`
  - `profiles/`
  - `bindings/`
  - `memory/`
  - `overrides/`
  - `meta/`
  - `backups/`
- `backups/` 只用于本地回滚与排障，不应进入 Steam 同步白名单，并应有轮转清理策略。

### 4.2 启动、运行与退出链路

- 启动时先做 `manifest` 预检；无变化快速跳过，有变化只导入变更文件。
- 导入必须先进入 staging，再做校验与原子替换，不能直接覆盖运行时目录。
- 运行期导出采用 `mark_dirty`、分域防抖和重文件延迟快照。
- 退出前进入限时 flush，只刷关键小文件和确实 dirty 的长期记忆快照。
- 导入、重命名和删除事务期间必须进入 sync fence，避免半完成状态重新刷回 `cloudsave/`。
- 启动期必须把云导入与 Workshop 角色卡同步、其他角色真源写链路串行化。
- `manifest.json` 至少要有 `schema_version`、`min_reader_schema_version`、`min_app_version`、`client_id`、`device_id`、`sequence_number` 与导出指纹。
- 本地还需要一份不进入云端的 `cloudsave_local_state`，持久化 `client_id`、`next_sequence_number` 和最近一次应用 / 导出状态。
- 当前角色在导入后若不存在或不可用，必须先回退到第一个有效角色，再解除启动屏障。
- v1 默认必须由 `launcher` 或等价 bootstrap 单点持有启动屏障，先完成导入判定 / 应用，再放行 `memory_server`、`main_server` 进入正常写态。
- 如果进程因架构限制必须先起，也只能停留在 `bootstrap_readonly` / `deferred_init` 状态，不能先初始化角色真源和长期记忆引擎。
- 运行期还必须有统一的全局写栅栏，覆盖角色配置修改、Workshop 同步、`memory_server` 写入、dirty exporter 和退出前 flush。

### 4.3 重名、删除与资源恢复

- `profiles/characters.json` 虽仍聚合导出，但冲突决策必须按角色条目进行，禁止整文件 last-writer-wins。
- 同名角色先比较 `entry_sequence_number`，删除则先比较 tombstone `sequence_number`。
- 不同角色的无关改动必须共存，最后再按稳定顺序重建 `profiles/characters.json`。
- 本地改名仍需走名称级事务；但跨设备云同步不再识别“同一角色改名”，而是按“旧名称删除 + 新名称新增”处理。
- 删除角色必须写 tombstone，不能只靠“角色条目消失”表达。
- 资源卸载不等于角色删除；缺资源时只更新绑定摘要中的资源状态。
- 导入后若当前设备已有匹配资源，则直接恢复绑定；若没有，则保留猫娘和绑定摘要，让用户通过现有模型导入或 Workshop 恢复流程补齐资源。
- tombstone 导入顺序必须先于普通角色文件，否则已删除角色会被旧设备重新带回。
- 如果同名角色在删除后被重新创建，必须显式满足“新条目序列号大于 tombstone”或进入确认分支，不能静默复用旧 tombstone 结果。
- 如果当前角色存在活跃语音会话或其他不能安全热替换的会话，云导入必须延迟应用或进入维护分支，不能直接热覆盖。

### 4.4 SQLite 与冲突控制

- Steam 不应直接监控运行中的 `time_indexed.db`。
- `time_indexed.db` 的 shadow copy 必须通过角色级停写 + checkpoint + SQLite 原生 backup API 生成，不能对运行中的主文件做普通文件复制。
- 导入替换前必须获取同一把角色级写栅栏，释放该角色数据库 engine，替换完成后 reload 并验证可打开性。
- 冲突判定优先使用 `client_id + sequence_number`，时间戳只作辅助。
- 任何覆盖本地文件的导入动作，都应先把旧版本打进本地冲突备份池。
- staging 阶段至少要校验版本闸门、文件哈希、档案名安全性和 tombstone / 当前角色 / 绑定摘要的一致性。

---

## 5. 分阶段实施建议

### 阶段 0：路径与基础设施

- 明确三平台目标根路径，区分“当前实现”与“目标状态”。
- 建立 `cloudsave/`、`manifest.json`、staging、原子替换、回滚与冲突备份池。
- 建立 `root_state`、`last_known_good_root` 与只读救援分支。
- 建立导出前敏感信息扫描。
- 建立启动期串行化约束，至少保证云导入与 Workshop 角色卡后台同步不会并发改写角色真源。

### 阶段 1：边界拆分与导出改造

- 明确云端目录、绑定摘要、记忆目录都继续按档案名组织，并明确双机同名覆盖规则。
- 建立目录索引、当前角色、删除 tombstone 与模型绑定摘要。
- 建立档案名审计、安全重命名事务以及 tombstone / 名称复用规则。
- 拆分 `user_preferences.json`。
- 建立 `conversation_settings` 与 `bindings` 的迁移期兼容回填。
- 明确“小文件默认同步 / 大文件默认留本地”的资源清单。
- 建立 `time_indexed.db` 的 shadow copy 导出链路。
- 明确角色卡链路与云存档链路的边界，避免误复用现有整包模型导入 / 导出能力。

### 阶段 2：接入 Steam AutoCloud

- Steam 仅监控 `cloudsave/`。
- 补齐 `mark_dirty` 覆盖。
- 建立“启动快路径、运行期分域导出、退出前限时 flush”的完整链路。
- 校验现有模型导入 / Workshop 恢复流程与绑定摘要兼容。

### 阶段 3：长期记忆与合并策略演进

- 评估从整库 shadow copy 演进到更细粒度长期记忆同步。
- 视情况把 `profiles/characters.json` 演进为角色分片。
- 从文件级覆盖逐步演进到更细粒度冲突合并。

---

## 6. 主要落地差距与风险

- Windows 路径方向已基本一致，但 macOS / Linux 当前代码还没有切到标准应用数据目录。
- `conversation_settings.json` 仍未真正从 `user_preferences.json` 拆出。
- `cloudsave/`、`manifest`、`bindings/`、tombstone、导入屏障和 sync fence 目前仍是设计目标，不是既有实现。
- 资源来源现实上同时覆盖项目内置、用户导入和 Workshop 三类路径，因此绑定摘要设计必须保持“来源 + 引用 + 指纹”导向。
- Workshop 的部分删除路径当前仍会改 `characters.json`，实现层还需要继续拆开“资源卸载”和“角色删除”。
- 当前角色卡导出 / 导入仍会按“角色 + 模型资源整包”工作，这条能力需要保留，但必须与云存档设计严格分离。
- 当前改名 / 删除实现还没有覆盖 `memory/<name>/...` 目录迁移 / 清理、tombstone 生成和导入期事务回退。
- `main_server` 启动后的 Workshop 角色卡同步目前是独立后台写链路，未来云导入必须与之串行化。
- 由于不再使用 `character_id`，同名不同角色的跨设备区分能力在 v1 明确不存在，这是一条必须接受并写进产品预期的限制。
- `client_id + sequence_number` 的本地持久化、名称复用撤销 tombstone 的规则和档案名审计整改链路，目前都还没有正式实现。

---

## 7. 评审建议

建议批准进入第一阶段，但需要明确两点：

1. 文档中的“统一到标准应用数据目录”在当前代码里仍属于目标态，不能被误解为已落地事实。
2. v1 的资源策略已经固定为“大文件不入云、目标设备有则直接用、没有则走现有手动恢复流程”，后续实现不应再回到“资产本体可选同步”的方向。
