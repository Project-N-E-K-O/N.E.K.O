# 云存档同步优化方案

> 核心方向：把云存档从“同步一批本地文件”升级为“可控的导出 / 导入管理层”。
> v1 只同步角色状态等小文件与关键状态文件；Live2D / VRM / MMD / Workshop 等大文件资源默认不进入云端，目标设备若已有对应资源则直接复用，没有则由用户手动导入或重新下载恢复。
> 关联文档：`cloud-save-sync-optimization-overview.md`

---

## 1. 文档目标

本文只做四件事：

- 固化云存档方案的统一口径，避免继续在“现状 / 目标 / 临时补丁”之间来回摇摆。
- 明确 v1 同步边界：哪些内容默认同步，哪些内容保留本地，哪些内容永不进云端。
- 在不大改现有运行时存储结构的前提下，给出可落地的导出、导入、冲突和恢复流程。
- 把“当前实现基线”和“目标状态”分开写，避免把尚未落地的改造误写成现状。

本文默认面向 Steam v1，但导出层本身不绑定 Steam，可复用于其他云端实现。

---

## 2. 当前实现基线

这一节描述的是当前项目代码已经存在的基础，不是目标态。

### 2.1 存档根与运行时目录

- `ConfigManager` 已承担用户目录解析、迁移和配置写入职责。
- Windows 侧主写入根已基本收敛到 `%LOCALAPPDATA%/N.E.K.O`，并保留从历史 `Documents` 目录迁移旧数据的能力。
- Windows 还存在 CFA / 受控文件夹访问兼容逻辑：写入可能落到 AppData，但读取仍可能兼容原始 `Documents/N.E.K.O/live2d`。
- macOS / Linux 代码当前仍主要使用 `Documents` 或 `cwd` 作为候选目录，因此“统一到标准应用数据目录”目前仍是阶段 0 的改造目标，不是既有事实。
- 当前运行时主目录主要包括：
  - `config/`
  - `memory/`
  - `plugins/`
  - `live2d/`
  - `vrm/`
  - `mmd/`
  - `workshop/`
  - `character_cards/`

### 2.2 角色、记忆与配置的现状

- 角色配置当前聚合在 `config/characters.json`，运行时主键仍是猫娘档案名。
- 记忆数据已基本按 `memory/<name>/...` 组织，包含 `recent.json`、`settings.json`、`facts.json`、`persona.json`、`persona_corrections.json`、`reflections.json`、`surfaced.json` 与 `time_indexed.db`。
- `time_indexed.db` 当前是长期记忆真实持久化的一部分，不是简单缓存。
- `user_preferences.json` 当前同时存放：
  - 模型位置、缩放、视口、显示器、相机等设备相关偏好
  - `__global_conversation__` 下的全局对话设置
- 角色当前还有一批与体验强相关的小配置直接保存在 `characters.json` 的 `_reserved` 中，例如 `touch_set`、VRM 打光、MMD 的 `lighting / rendering / physics / cursor_follow`。
- `core_config.json`、`workshop_config.json`、`voice_storage.json` 等仍属于本地配置。

### 2.3 模型与资源来源的现状

- Live2D、VRM、MMD 与 Workshop 资源并不只来自一个物理目录。
- 当前运行时实际会同时识别以下资源来源：
  - 项目内置资源，例如 `/static/vrm/*`、`/static/mmd/*`
  - 用户导入资源，例如 `/user_live2d/*`、`/user_vrm/*`、`/user_mmd/*`
  - Steam Workshop 资源，例如 `/workshop/<item_id>/*`
- 因此云存档层不能把“模型绑定”简单等同于“某个固定目录下的一份文件”，而应同步绑定摘要、来源类型和可恢复线索。

### 2.4 与云存档直接相关的现状风险

- 运行时主键是名称，而 v1 云同步也决定继续按名称组织；跨平台命名规范、同名碰撞和改名语义还没有在实现里被统一写死。
- 当前删除、改名、当前角色切换、记忆目录变更仍是分散链路，不适合直接拿来当云导入事务复用。
- Workshop 的部分删除路径当前会顺带改 `characters.json`，这说明“卸载资源”和“删除角色”尚未完全分离。
- 全局对话设置仍直接写 `user_preferences.json`，因此它在文档中只能算“v1 要拆分的内容”，不能写成“现状已完成”。
- 项目当前已经存在“角色卡导出 / 导入并可附带模型资源”的链路，它的语义是人工分享 / 手工备份，不是云存档 v1 的“小文件漫游”语义；如果两者不显式分离，后续实现很容易误复用整包回灌逻辑。
- `main_server` 启动后会在后台执行 Workshop 角色卡同步，这条链路会读写 `characters.json` 并触发 `initialize_character_data()`；如果云导入也在启动期修改角色数据，实际会形成并发写入和重载竞争。
- 当前改名与删除实现还没有覆盖 `memory/<name>/...` 目录迁移 / 清理、tombstone 生成和导入期回退等完整事务要求，说明这部分必须先在设计上写成硬约束，不能默认“现有接口稍作包装即可复用”。

---

## 3. v1 统一口径

以下结论是整份方案的统一前提。

### 3.1 云端只同步 `cloudsave/`

- Steam AutoCloud v1 只监控 `cloudsave/`，不直接监控整个用户目录。
- 运行时目录和云端导出目录必须解耦。
- 任何需要进云端的内容，都必须先经导出层规范化后再写入 `cloudsave/`。

### 3.2 运行时与云端都按档案名组织

- v1 不再引入 `character_id`。
- 运行时界面、现有 API、当前角色、绑定摘要、记忆路径和云端目录键都统一按猫娘档案名组织。
- 同名即视为同一角色；不同名即视为不同角色。
- `catalog/current_character.json`、`bindings/`、`memory/` 与 tombstone 也统一按档案名表达。

### 3.3 同名角色按云端直接覆盖

- 名称相同即视为命中本地同名角色，直接应用云端覆盖。
- 覆盖前必须保留本地冲突备份。
- v1 不再尝试跨设备识别“同一角色改名”。
- 改名在云同步语义上等价于“旧名称删除 + 新名称新增”。

### 3.4 小文件默认同步，大文件默认留本地

- v1 默认同步角色状态等小文件与关键状态文件。
- Live2D / VRM / MMD / Workshop 等大资源文件默认不进入云端。
- 目标设备若已有对应资源，应直接复用。
- 目标设备若没有对应资源，云存档只恢复角色与绑定摘要，不自动回灌大文件；用户通过现有模型导入或 Workshop 恢复流程补齐资源。
- v1 不要求新增云存档专属开关，也不要求新增专门前端入口。
- 当前项目中的角色卡导出 / 导入继续保留为“分享 / 手工备份”通道；云存档 v1 不得复用其“整包模型资源导出 / 回灌”语义。

### 3.5 `time_indexed.db` 在 v1 走 shadow copy

- `time_indexed.db` 在 v1 继续纳入同步范围，因为它属于关键状态。
- 但进入云端的必须是安全影子快照，而不是运行中的主库。
- Steam 不应直接监控运行中的 SQLite 文件。

### 3.6 导入导出必须可回滚、可审计

- 所有覆盖本地数据的导入动作都必须先经过 staging。
- 正式应用必须采用原子替换，不直接就地覆盖运行时目录。
- 覆盖前必须保留冲突备份。
- 失败时必须可以整体回滚到导入前状态。
- 启动期任何会改写 `characters.json`、`user_preferences.json` 或 `memory/<name>/...` 的后台链路，都必须服从同一导入屏障；至少要与 Workshop 角色卡同步串行化，不能与云导入并发落盘。

### 3.7 冲突判定不能只看时间戳

- `manifest.json` 必须包含 `client_id`、`device_id`、`sequence_number` 与版本锁字段。
- 新旧判断优先使用 `client_id + sequence_number`。
- 时间戳只作为辅助信号，且统一使用 UTC。

### 3.8 档案名必须满足跨平台安全约束

- 既然 v1 直接用档案名作为云端键，导出前必须做统一的档案名安全校验。
- 至少需要拦截：
  - 路径分隔符
  - `..`
  - Windows 保留名
  - 尾部空格 / 尾部点
  - 导入后会落成空字符串的非法名称
- 导入和导出前都必须做 Unicode 规范化与大小写折叠冲突检查。
- 如果两个角色在跨平台规范化后映射到同一个安全名称，必须进入显式冲突分支，不能静默覆盖。

### 3.9 根路径切换必须带确定性兜底

- 阶段 0 的根路径切换不能只靠“改默认目录”完成，必须配套 `root_state` 和 `last_known_good_root`。
- `Documents`、exe 目录和 `cwd` 只允许作为历史导入源，不能继续作为随机长期写入根。
- 启动时如果目标根异常、迁移中断或云端导入失败，系统至少应能回到最后一次本地可用状态，或进入只读救援模式。

### 3.10 不要求先大改运行时存储结构

- 当前角色状态与模型资源已经基本分开存储。
- v1 的重点是建立导出层、同步边界和恢复流程，而不是整体重做运行时目录。
- 运行时仍可继续读写现有主文件，导出层负责规范化，导入层负责回填兼容字段。

### 3.11 运行中会话下的导入必须有维护栏杆

- 项目当前已经对“语音会话中切换角色 / 改名”做了运行时限制，因此云导入若会影响当前角色、当前绑定或相关记忆，也必须进入同级别的 maintenance fence。
- 如果当前存在活跃语音会话或其他不能被安全热替换的角色会话，导入层至少应支持：
  - 延迟应用到安全时机
  - 或进入明确的维护 / 只读分支
- 不允许在活跃会话中静默热替换当前角色配置，再依赖后续 `initialize_character_data()` 碰运气恢复一致性。

---

## 4. v1 同步边界

### 4.1 默认同步的小文件与关键状态文件

| 类别 | 当前来源 | 导出到 `cloudsave/` | 说明 |
| --- | --- | --- | --- |
| 猫娘目录与当前选择 | `characters.json` 中的目录信息、当前角色状态 | `catalog/catgirls_index.json`、`catalog/current_character.json` | 先恢复“有哪些猫娘”和“当前是谁” |
| 角色删除墓碑 | 本地删除事务日志 | `catalog/character_tombstones.json` | 防止旧设备把已删除角色重新导出；墓碑至少按旧档案名记录删除结果 |
| 角色配置 | `config/characters.json` | `profiles/characters.json` | v1 仍允许聚合导出，继续按档案名组织 |
| 全局对话设置 | `user_preferences.json` 中 `__global_conversation__` | `profiles/conversation_settings.json` | 这是 v1 的拆分目标，不代表当前实现已完成 |
| 模型引用与资源状态摘要 | 角色配置中的模型选择状态 | `bindings/<character_name>.json` | 只同步绑定、来源与状态，不同步模型本体 |
| 角色体验保留配置 | `characters.json` 中 `_reserved.touch_set`、`_reserved.avatar.vrm.lighting`、`_reserved.avatar.mmd.{lighting,rendering,physics,cursor_follow}` | `bindings/<character_name>.json` | 属于角色体验的一部分，应与绑定摘要一起导出，而不是继续散落在运行时私有结构里 |
| 角色记忆 JSON | `memory/<name>/recent.json`、`settings.json`、`facts*.json`、`persona*.json`、`reflections*.json`、`surfaced.json` | `memory/<character_name>/...` | 云端目录继续按档案名组织 |
| 长期记忆快照 | `memory/<name>/time_indexed.db` | `memory/<character_name>/time_indexed.db` | 通过 shadow copy 导出 |
| 小型参数 / 映射文件 | `live2d/<模型>/parameters.json`、`static/vrm/configs/*_emotion.json`、`mmd/emotion_config/*.json` | `overrides/...` | 体积小、与角色体验强相关 |
| Workshop 轻量元数据 | `character_cards/*.workshop_meta.json` | `meta/workshop/*.workshop_meta.json` | 用于恢复提示与来源识别 |

补充约束：

- `catalog/catgirls_index.json` 中每个角色条目至少应包含 `character_name`、展示字段、资源状态摘要和最近导出版本信息。
- `catalog/catgirls_index.json` 中每个角色条目还应包含 `entry_sequence_number` 或等价字段，用于与 tombstone 做先后比较。
- `catalog/current_character.json` 至少应包含 `current_character_name`、`last_known_name` 与最近一次成功应用时间或等价标记。
- `profiles/characters.json` 继续按名称组织，但导出层必须保证条目顺序稳定，避免无意义整文件抖动。

### 4.2 v1 默认不进入云端的大文件

| 类别 | 当前来源 | v1 处理方式 | 恢复方式 |
| --- | --- | --- | --- |
| Live2D 模型本体 | `live2d/` 下模型文件、贴图、动作等 | 不进入云端 | 目标设备已有则直接复用，否则用户手动导入 |
| VRM 模型本体 | `vrm/` 下 `.vrm`、贴图、动画等 | 不进入云端 | 目标设备已有则直接复用，否则用户手动导入 |
| MMD 模型与动作资源 | `mmd/` 下 `pmx` / `pmd` / `vmd` / 音频等 | 不进入云端 | 目标设备已有则直接复用，否则用户手动导入 |
| Workshop 下载资源与缓存 | `workshop/` 下 item 资源、缓存包 | 不进入云端 | 目标设备已有则直接复用，否则通过 Workshop 重新下载恢复 |

### 4.3 始终不纳入同步

| 类别 | 当前来源 | 不同步原因 |
| --- | --- | --- |
| 敏感配置 | `core_config.json` 中的 API Key、Provider 地址、Cookie、密钥 | 安全红线 |
| 本机设备参数 | `user_preferences.json` 中的布局、显示器、视口、相机参数 | 跨设备会错位 |
| 本机路径配置 | `workshop_config.json`、Workshop 路径缓存 | 换机后失效 |
| 运行态统计与配额文件 | `token_usage.json`、`.telemetry_unsent.json`、`agent_quota.json` 等 | 会污染统计和配额 |
| 插件目录与插件数据 | `plugins/<plugin_id>/...` | v1 不支持插件级同步协议 |
| `voice_storage.json` | `config/voice_storage.json` | 当前与 API Key 相关，不适合直接漫游 |
| 普通角色卡资产 | `character_cards/*.chara.json`、角色卡 PNG / `.nekocfg` 等人工分享文件 | 属于角色卡分享 / 手工备份通道，不属于云存档 v1 的运行时真源 |

### 4.4 需要轻量整理后再同步

| 内容 | 当前问题 | v1 处理方式 |
| --- | --- | --- |
| 角色身份 | 运行时与大量现有接口都以名称为主键 | v1 不引入新身份字段，继续按档案名组织导出与导入 |
| `user_preferences.json` | 同时包含漫游配置和本机设备参数 | 拆出 `conversation_settings.json` |
| 模型绑定信息 | 当前散落在角色配置与资源路径字段里 | 导出层补一份 `bindings/<character_name>.json` |
| 角色体验保留配置 | `touch_set`、VRM / MMD 小型体验配置当前散落在 `_reserved` 中 | 与模型绑定摘要一起归并到 `bindings/<character_name>.json` 的派生结果中 |
| 小型参数 / 映射文件 | 位置分散 | 先按白名单导出，必要时只在导出层归一化到 `overrides/` |
| 长期记忆导出 | 当前是运行中的 SQLite 文件 | 改为 checkpoint + shadow copy |
| 根路径统一 | macOS / Linux 仍存在旧逻辑 | 作为阶段 0 目标改造，不写成既有事实 |
| 角色卡导入导出 | 当前会按“角色 + 模型资源整包”处理 | 明确与云存档导入 / 导出分离，禁止直接复用整包回灌语义 |

---

## 5. 目标架构

### 5.1 分层原则

- `Local Root` 是运行时真实读写根。
- `cloudsave/` 是唯一云端导出目录。
- Steam 只负责传输 `cloudsave/`，不直接读运行时目录。
- 导出层负责：
  - 白名单序列化
  - 目录归一化
  - 版本与冲突元数据
  - 敏感字段检查
- 导入层负责：
  - 读取 `cloudsave/`
  - staging 校验
  - 原子应用
  - 回填现有运行时兼容字段

### 5.2 建议的 `cloudsave/` 结构

```text
cloudsave/
  manifest.json
  catalog/
    catgirls_index.json
    current_character.json
    character_tombstones.json
  profiles/
    characters.json
    conversation_settings.json
  bindings/
    <character_name>.json
  memory/
    <character_name>/
      recent.json
      settings.json
      facts.json
      persona.json
      persona_corrections.json
      reflections.json
      surfaced.json
      time_indexed.db
  overrides/
    live2d/
      <model_key>/
        parameters.json
        model3_overrides.json
    vrm/
      <model_key>_emotion.json
    mmd/
      <model_key>.json
  meta/
    workshop/
      <character_card_name>.workshop_meta.json
  backups/
    conflict_<timestamp>.zip
```

说明：

- v1 不需要 `assets/` 目录，因为大文件本体不进入云端。
- `bindings/` 与 `memory/` 从 v1 开始统一使用档案名作为键。
- `catalog/current_character.json` 建议保存 `current_character_name`，必要时可附带展示字段用于提示。
- `backups/` 只用于本地回滚与排障，不应进入 Steam 同步白名单。
- 如果平台侧无法稳定排除 `backups/`，应改为 `Local Root` 下的本地专用目录，且 `manifest.json` 不得引用该目录内容。
- `backups/` 必须做轮转清理，建议至少满足以下之一：
  - 仅保留最近 5 个冲突包
  - 或总大小不超过 50MB

### 5.3 真源关系

- `profiles/characters.json` 负责角色设定、展示字段和非资源型配置。
- `bindings/<character_name>.json` 负责模型类型、资源来源、资源状态、回退信息以及与绑定强相关的小型体验配置。
- `memory/<character_name>/...` 负责云端统一视角下的角色记忆。
- 运行时仍可保留现有 `characters.json`、`memory/<name>/...` 与模型目录结构。
- 如果角色配置和绑定摘要出现重叠字段，应以 `bindings/<character_name>.json` 为准处理资源相关逻辑。

### 5.4 迁移期兼容契约

为了避免阶段 0 / 1 期间形成双真源，v1 必须把迁移期规则写死。

#### 5.4.1 `conversation_settings.json`

- 当前运行时真实读写点仍是 `user_preferences.json` 中的 `__global_conversation__` 条目。
- 在业务层尚未切到新文件前，`profiles/conversation_settings.json` 只能作为导出层的规范化结果，不能单独成为新的运行时真源。
- 导出顺序应为：
  1. 先读取 `user_preferences.json` 中的白名单字段
  2. 再生成 `profiles/conversation_settings.json`
- 导入顺序应为：
  1. 先应用 `profiles/conversation_settings.json`
  2. 再把兼容字段回填到 `user_preferences.json` 的全局哨兵条目
- 只有当运行时读写路径正式切到新文件后，才允许去掉这层兼容回填。

#### 5.4.2 `bindings/<character_name>.json`

- 当前模型类型、模型来源、模型路径等信息仍主要来自角色配置里的现有字段和 `_reserved.avatar...` 结构。
- 在业务层尚未直接读取 `bindings/<character_name>.json` 之前，绑定摘要必须由导出层从现有角色配置派生。
- 导入时必须先解析 `bindings/<character_name>.json`，再把兼容字段回填到现有角色配置结构。
- 在运行时正式切到绑定摘要前，不允许出现“角色配置直接写绑定字段”和“业务层单独写 bindings 文件”并行长期存在的双真源。
- 现有 `_reserved.touch_set`、`_reserved.avatar.vrm.lighting`、`_reserved.avatar.mmd.{lighting,rendering,physics,cursor_follow}` 也属于这份派生 / 回填契约的一部分；在运行时真源尚未迁移前，仍只能由导出层归并和回填，不能额外形成第三套持久化真源。

---

## 6. 核心流程

### 6.1 启动与导入流程

启动链路必须先完成云端导入判定，再让依赖角色与记忆状态的服务进入正常写态。

建议顺序：

1. 进入 launcher 预导入屏障，或等价的 bootstrap 只读屏障。
2. 读取本地 `manifest` 指纹与云端 `manifest`。
3. 如果云端无变化，快速跳过导入。
4. 如果云端有变化，只把变更文件导入到 staging 区，不做无差别全量恢复。
5. 先应用 `catalog/`、`profiles/`、`bindings/` 与当前角色必需状态，再补齐其余数据。
6. 通过导入结果回填现有运行时兼容字段。
7. 导入成功后记录 `last_applied_manifest_fingerprint` 或等价状态。
8. 解除 sync fence，允许服务进入正常写态。

要求：

- 不允许让 `memory_server`、`main_server` 先按旧本地状态初始化，再被云导入反向覆盖。
- 导入失败时必须回退到最后一次本地可用状态，并给出明确降级状态。
- 如果 `current_character_name` 在导入后不存在、被 tombstone 命中或资源未就绪到无法使用，必须先回退到第一个有效角色，再解除启动屏障。
- 如果导入后没有任何有效角色，必须进入明确的 `no_character` / 救援分支，而不是让角色相关服务按半初始化状态继续运行。
- 启动期必须把云导入与 Workshop 角色卡后台同步、其他 `characters.json` 写链路串行化；在导入结论落定前，不允许后台补卡任务先把新角色写入本地再触发二次重载。

#### 6.1.1 启动屏障归属与放行顺序

v1 必须把“谁拥有启动导入屏障”写死，不能只写成抽象原则。

默认实现约束：

1. `launcher` 或等价 bootstrap 进程是唯一的启动屏障拥有者。
2. 屏障拥有者在拉起 `memory_server`、`main_server` 前，只允许读取最小本地状态和云端 `manifest`，不允许先启动角色相关运行时。
3. 如果云端无变化，屏障拥有者写入本地 `cloudsave_local_state` / `root_state` 的“本次启动已判定”标记后，再放行子进程启动。
4. 如果云端有变化，屏障拥有者必须先完成 staging、校验、正式应用、回滚判定和 `current_character` 回退，再放行子进程启动。
5. `main_server` 的 Workshop 后台同步只能在启动屏障释放后进入可写阶段；在此之前只允许做不落盘的准备动作。
6. `memory_server` 和 `main_server` 若因架构限制必须先起进程，也只能停留在 `bootstrap_readonly` / `deferred_init` 状态，不得在模块导入或 startup 中初始化角色真源、迁移 `memory/<name>/...`、实例化长期记忆引擎或启动角色卡同步。

禁止事项：

- 不允许把“先启动服务，启动后再热导入覆盖旧状态”作为 v1 的主启动路径。
- 不允许由 `memory_server` 和 `main_server` 各自判断云导入时机，否则会形成双重屏障和竞态。

#### 6.1.2 `manifest.json` 最小字段与版本闸门

`manifest.json` 至少应包含：

- `schema_version`
- `min_reader_schema_version`
- `min_app_version`
- `client_id`
- `device_id`
- `sequence_number`
- `exported_at_utc`
- 导出文件清单、文件哈希和总指纹

约束：

- 只有当本轮导出全部成功后，才允许更新 `manifest.json`。
- 低版本客户端不得静默导入高版本 `manifest`。
- `sequence_number` 必须单调递增；时间戳只作辅助。
- 如果导入后的规范化结果与刚应用的云端内容哈希一致，不应立即生成新的等价 `manifest`，避免回声导出。

#### 6.1.3 本地 `cloudsave_local_state`

除云端文件外，本地还应维护一份不进入云端的 `cloudsave_local_state`，至少记录：

- `client_id`
- `next_sequence_number`
- `last_applied_manifest_fingerprint`
- `last_successful_export_at`
- `last_successful_import_at`

约束：

- `client_id` 只在首次启用云存档、明确重置本地同步身份或本地同步状态文件丢失时重新生成。
- 根路径迁移、应用升级和普通重启都不应重置 `client_id`。
- `next_sequence_number` 必须在本地持久化，且不得因回滚旧备份、切根或应用崩溃而倒退。
- 启动时若发现本地 `next_sequence_number` 小于最近一次已应用 `manifest` 或本地 tombstone / 目录索引里的最大序列号，必须先把它推进到安全值后再允许导出。
- 切 Steam 账号时默认不重置 `client_id`，但必须进入 `account_switch_pending` 保护分支，防止错误把旧本地状态自动导出到新账号。

#### 6.1.4 导入期全局写栅栏

除了启动屏障外，v1 还必须有一个实现层可调用的全局写栅栏，例如 `cloud_apply_lock`、`maintenance_mode` 或等价机制。

要求：

- 任何会改写以下真源的链路都必须服从同一把写栅栏，而不是各自加锁：
  - `characters.json` / `profiles/characters.json`
  - `user_preferences.json` / `profiles/conversation_settings.json`
  - `memory/<name>/recent.json`、`settings.json`、`facts*.json`、`persona*.json`、`reflections*.json`、`surfaced.json`
  - `memory/<name>/time_indexed.db`
  - Workshop 角色卡后台同步、退出前 flush、运行期 dirty exporter
- 写栅栏进入后，普通写接口只能二选一：
  - 返回明确的 retryable / locked 状态
  - 或进入有上限的队列，等待正式应用结束后重试
- 写栅栏进入后，`memory_server` 的长期记忆写入、`characters_router` 的角色配置修改、Workshop 同步和导出任务都必须暂停，不能靠“尽量快执行完”碰运气。
- 写栅栏释放前必须先完成：
  - 角色配置与绑定摘要回填
  - `current_character` 回退校验
  - 角色 / 记忆 reload
  - 失败时的整体回滚或只读降级

### 6.2 运行期导出与退出 flush

运行时不能把每次本地落盘都直接变成一次完整云导出。

建议使用分域 dirty queue：

- P0：目录索引、当前角色、当前角色绑定摘要、对话设置
- P1：角色配置、记忆 JSON、小型参数 / 映射文件、Workshop 轻量元数据
- P2：`time_indexed.db` 影子快照等重文件

导出规则：

1. 业务层本地写成功后，只做 `mark_dirty`。
2. `CloudSaveManager` 按域合并写入，避免重复导出同一批文件。
3. P0 / P1 使用较短 debounce。
4. P2 只在空闲窗口、角色切换、会话收束点或退出前执行快照。
5. 如果目标导出文件哈希未变化，直接跳过重写。
6. 只有本轮导出全部成功后，才更新 `manifest.json`。
7. 退出时进入 shutdown fence，只刷关键小文件与确实 dirty 的长期记忆快照，并设置超时上限。

额外约束：

- `CloudSaveManager` 不得直接复用现有角色卡导出 / 导入接口作为云导出 / 云导入实现，因为那条链路的语义是“分享 / 手工备份 + 可选模型资源整包”。
- 角色卡链路可以继续存在，但其产物不得被自动视为 `cloudsave/` 真源，也不得在云导入时触发模型资源整包回灌。

重点打点入口至少包括：

- `characters.json` 写入
- 当前角色切换
- 全局对话设置保存
- 角色记忆 JSON 写入
- 模型绑定摘要变化
- 小型参数 / 映射文件写入
- `time_indexed.db` shadow copy 生成完成

### 6.3 名称主键、重名、改名与删除

#### 6.3.1 名称主键原则

- v1 不引入 `character_id`。
- 角色档案名同时作为运行时主键、云端目录键和绑定摘要 / 记忆目录键。
- 文档中的“同名”判断，都以档案名为准。
- 首次启用云存档前，必须先执行一次档案名审计。
- 档案名审计至少要检查：
  - 非法字符与路径穿越片段
  - Windows 保留名
  - Unicode 规范化冲突
  - 大小写折叠冲突
- 如果审计发现冲突或不安全名称，必须先进入显式整改分支，再允许任何角色进入正式导出队列。
- 整改方式只能是：
  - 用户显式改名
  - 或系统执行一次可回滚的安全重命名事务，并记录本地迁移日志
- 不允许在导出时静默把本地名称映射成另一个云端安全名而不改变本地真源，否则后续删除、当前角色和记忆路径都会失配。

#### 6.3.2 重名处理

- `profiles/characters.json` 虽然仍是聚合文件，但冲突决策不得采用整文件 last-writer-wins。
- v1 必须按“角色条目”决策，再重建聚合文件：
  - 角色是否存在、是否被删除，先看 `catalog/catgirls_index.json` 与 `catalog/character_tombstones.json`
  - 同名角色的胜负先看 `entry_sequence_number`
  - tombstone 与普通角色文件的胜负先看 tombstone `sequence_number` 与角色条目 `entry_sequence_number`
- 只有“同一个角色条目”发生真正冲突时，才允许在该角色范围内做 deterministic tie-break；不同角色的无关修改必须共存，不能因为聚合文件整体较新就互相覆盖。
- 如果同名角色条目的 `entry_sequence_number` 相同但内容哈希不同，必须：
  - 保留失败方的本地冲突备份
  - 记录冲突日志
  - 再按稳定规则决策，例如 `(manifest.sequence_number, client_id)` 的固定顺序
- 导入落盘时必须根据最终条目集合重建 `profiles/characters.json`，并保持稳定排序，不能直接把云端整文件原样覆盖到本地。

#### 6.3.3 改名事务

- 本地改名仍必须走名称级事务。
- 事务范围至少覆盖：
  - `profiles/characters.json`
  - 当前角色索引
  - 整棵 `memory/<old_name>/` 目录
  - 相关缓存刷新
- 事务期间必须进入 sync fence，禁止半完成状态重新导出。
- 但跨设备云同步不再尝试识别“同一角色改名”。
- v1 中，改名的云同步语义等价于：旧名称被删除，新名称作为一个新角色出现。

#### 6.3.4 删除事务

- 删除角色不能只靠“角色条目消失”表达，必须写 tombstone。
- tombstone 至少应包含旧档案名、`deleted_at` 与 `sequence_number` 或等价版本信息。
- 卸载 Workshop item、本地模型删除或路径失效，不等于删除角色。
- 资源卸载只应更新 `bindings/<character_name>.json` 中的 `asset_state`，不应直接写角色 tombstone。
- 导入时必须先应用 tombstone，再处理普通角色文件，避免已删除角色被旧设备普通文件重新带回。
- 如果被删除的是当前角色，必须先完成当前角色回退，再应用删除结果。
- 如果同一档案名后续被重新创建，必须把它视为一次显式“名称复用”事件，而不是普通覆盖。
- 名称复用至少要满足以下规则之一，实施时必须二选一写死：
  - 新角色条目的 `entry_sequence_number` 严格大于同名 tombstone 的 `sequence_number`，并在下一轮导出中撤销或清理旧 tombstone
  - 或进入显式确认 / 冲突分支，由用户确认“这是重新创建，不是恢复旧角色”
- 在没有满足以上条件前，同名 tombstone 应继续优先于普通角色文件生效。

### 6.4 模型绑定与资源恢复

v1 的关键不是同步模型本体，而是同步“绑定了什么、资源来自哪里、能否在目标设备恢复”。

建议 `bindings/<character_name>.json` 至少包含：

- `character_name`
- `model_type`
- `asset_source`
  - `builtin`
  - `steam_workshop`
  - `local_imported`
  - `manual_external`
- `asset_source_id`
- `model_ref`
- `asset_display_name`
- `asset_fingerprint`
- `asset_state`
  - `ready`
  - `downloadable`
  - `import_required`
  - `missing`
  - `fallback_active`
- `fallback_model_ref`
- `last_verified_at`
- `experience_overrides`
  - `touch_set`
  - `vrm_lighting`
  - `mmd_lighting`
  - `mmd_rendering`
  - `mmd_physics`
  - `mmd_cursor_follow`

约束：

- 绝对物理路径不得直接进入绑定摘要。
- Local Root 内资源可以导出为相对路径标识。
- Local Root 外资源只允许导出指纹、逻辑 ID、展示名或来源类型。

导入行为：

1. 先恢复猫娘目录、配置、记忆与绑定摘要。
2. 再按 `asset_source`、`asset_source_id`、`model_ref`、`asset_fingerprint` 在当前设备已有资源中做匹配。
3. 若命中已有资源，直接恢复绑定并使用本机模型。
4. 若未命中，保留角色与绑定摘要，不自动回灌大文件。
5. 用户后续通过现有模型导入或 Workshop 恢复流程补齐资源。

派生约束：

- 现有运行时里依赖路径前缀和 `_reserved` 字段判断来源，因此 v1 必须把“旧字段 / URL 前缀 -> `asset_source` / `asset_source_id` / `model_ref` / `experience_overrides`”的映射规则写死，不能由各模块各自推断。
- `asset_source` 至少要覆盖：
  - `builtin`
  - `steam_workshop`
  - `local_imported`
  - `manual_external`
- 现有只写 `model_path` 但未写 `asset_source` 的链路，在导出层必须按来源补全，而不是把“未知来源”直接导进云端。

### 6.5 长期记忆与冲突策略

#### 6.5.1 `time_indexed.db`

- `time_indexed.db` 的 shadow copy 在本项目里必须定义成“SQLite 一致性快照”，不能等价理解为对运行中文件做普通文件复制。
- 推荐实现协议：
  1. 先获取该角色的 `memory_snapshot_lock` 或等价写栅栏，阻止 `/cache`、`/process`、`/renew`、review 后台任务继续写入
  2. 等待该角色当前写事务结束
  3. 通过同一数据库连接执行 `PRAGMA wal_checkpoint(TRUNCATE)` 或等价 checkpoint
  4. 使用 SQLite 原生 backup API，例如 Python `sqlite3.Connection.backup()`，生成临时快照文件
  5. 校验快照能被打开，且核心表存在，再写入 `cloudsave/memory/<character_name>/time_indexed.db`
  6. 释放该角色写栅栏
- 禁止直接对运行中的 `time_indexed.db` 主文件做普通文件复制；也不允许把 `.wal` / `.shm` 一并交给 Steam 观察。
- 导入替换协议也必须写死：
  1. 获取同一把角色级写栅栏
  2. 停止该角色的后台记忆任务并结束新写入
  3. 显式释放 / 重建该角色的 SQLAlchemy engine，例如调用 `dispose_engine(name)` 或等价逻辑
  4. 原子替换本地 `time_indexed.db`
  5. 清理遗留 WAL / SHM
  6. reload 该角色记忆组件并做一次可打开性验证
  7. 只有验证成功后才允许释放写栅栏
- 如果任一步失败，必须回退到替换前的本地库或进入只读救援，不能带着半替换状态继续启动。

#### 6.5.2 v1 文件级冲突策略

| 数据类型 | v1 策略 |
| --- | --- |
| `profiles/characters.json` | 按角色条目合并；禁止整文件 last-writer-wins |
| `profiles/conversation_settings.json` | last-writer-wins，并记录 manifest 决策来源 |
| `memory/<character_name>/recent.json` | last-writer-wins |
| `facts*.json` / `persona*.json` / `reflections*.json` | v1 先做文件级 last-writer-wins |
| `memory/<character_name>/time_indexed.db` | last-writer-wins + 序列号校验 + 备份池 |
| `overrides/*` | last-writer-wins；资源缺失时保留覆盖层 |

补充规则：

- `profiles/characters.json` 的冲突决策必须以 `catalog/` 中的角色级元数据为准，`profiles/characters.json` 只是聚合承载体，不是独立的冲突时钟。
- 如果某个角色被 tombstone 命中，则该角色在重建后的 `profiles/characters.json` 中必须缺席，即使云端聚合文件仍残留旧条目也不能把它带回。
- `bindings/<character_name>.json`、`catalog/catgirls_index.json` 与 `profiles/characters.json` 同名条目之间如果不一致，必须先进入 staging 冲突分支，不能直接套用 last-writer-wins。

### 6.6 敏感信息防线、staging 与回滚

- 导出层必须采用白名单序列化，默认不允许把运行时原文件原样打包进云端。
- 敏感字段扫描至少覆盖：
  - `api_key`
  - `authorization`
  - `bearer`
  - `cookie`
  - `token`
  - `sk-`
- staging 校验不通过时，必须整体回滚，不进入正式应用。
- 冲突备份池必须做轮转清理。

staging 阶段至少应覆盖：

- 版本闸门校验
- 文件哈希校验
- 档案名安全检查
- tombstone / 当前角色 / 绑定摘要之间的一致性检查
- 目标文件可写性和原子替换前置条件检查

### 6.7 同机切换 Steam 账号与旧档回灌保护

- 本地维护一份不进入云端的 `account_state`。
- 启动时若检测到 Steam 账号变化，不允许立刻把当前 Local Root 自动导出到新账号云端。
- 应进入 `account_switch_pending`、`needs_confirmation` 或等价保护状态。
- 对手动还原旧本地备份、离线长时间游玩后再联网的场景，必须阻止静默把旧快照回灌成新的云真相。

### 6.8 运行中会话与维护栏杆

- 如果当前角色存在活跃语音会话或其他不能被安全热替换的角色会话，导入层不得直接覆盖其配置、绑定或记忆目录。
- 至少需要二选一写死：
  - 延迟到安全时机再应用
  - 进入维护 / 只读分支并要求显式恢复
- 当前项目已经对“语音状态下改名 / 切换角色”做保护，因此云导入不能比本地接口更宽松。
- 除了当前角色会话外，所有运行期写链路也必须进入统一维护态：
  - `characters_router` 的改名、删除、当前角色切换、模型绑定、`touch_set` / VRM / MMD 配置修改
  - Workshop 角色卡后台同步
  - `memory_server` 的 `/cache`、`/process`、`/renew` 与后台 review
  - 运行期 dirty exporter 与退出前 flush
- 维护态必须由实现层提供统一可观测状态，例如 `root_state.mode = bootstrap_importing | maintenance_readonly | normal`；不能只靠零散锁对象隐式协调。
- 如果运行中热导入无法在有限时间内拿到全局写栅栏，必须放弃本次热应用并延迟到下次启动，而不是部分成功后继续执行。

### 6.9 根路径切换、`root_state` 与只读救援

建议本地额外维护不进入云端的 `root_state`，至少记录：

- `current_root`
- `last_known_good_root`
- `last_migration_source`
- `last_migration_result`
- `last_successful_boot_at`

要求：

- 切根时必须先写迁移结果，再切正式读写入口。
- 如果新根异常、迁移中断或导入失败，允许回到 `last_known_good_root` 或进入只读救援。
- 只读救援不等于回退到 `Documents` / exe 目录 / `cwd` 做随机写入；它必须是可解释、可复现的确定性分支。

---

## 7. 分阶段实施

### 阶段 0：先把本地闭环打通

目标：在不接 Steam 的前提下，把本地导出 / 导入 / 回滚链路跑通，并把根路径问题说清楚。

处理内容：

1. 明确三平台目标根路径
   - Windows：`%LOCALAPPDATA%/N.E.K.O`
   - macOS：`~/Library/Application Support/N.E.K.O`
   - Linux：`${XDG_DATA_HOME:-~/.local/share}/N.E.K.O`
2. 保留 `Documents`、exe 目录、`cwd` 仅作为历史导入源，不再作为长期真实写入根。
3. 建立 `cloudsave/`、`manifest.json`、staging、原子替换与回滚框架。
4. 建立 `root_state`、`last_known_good_root` 与只读救援分支。
5. 建立冲突备份池与敏感信息扫描。
6. 建立由 `launcher` 或等价 bootstrap 单点持有的启动屏障。
7. 建立全局写栅栏，至少覆盖角色配置修改、Workshop 同步、`memory_server` 写入和退出前 flush。

完成标志：

- 不接 Steam 也能稳定完成一次完整导出和导入。
- Windows 主写入根与现有实现一致。
- macOS / Linux 的“目标路径”和“当前代码现状”在文档中不再混写。
- 启动屏障的持有者、放行时机和失败回退路径已经明确，`memory_server` / `main_server` 不会先按旧状态完成初始化。
- 运行期主要写链路已经能被统一写栅栏拦住，不会在导入应用期间继续落盘。

### 阶段 1：把同步边界和绑定摘要定清楚

目标：在基础设施稳定后，正式定义同步边界、角色身份与资源恢复策略。

处理内容：

1. 明确云端目录、绑定摘要、记忆目录都继续按档案名组织。
2. 建立 `catalog/`、`bindings/` 与 tombstone 语义。
3. 建立档案名审计、冲突整改与安全重命名事务。
4. 拆分 `user_preferences.json`，把全局对话设置导出为 `profiles/conversation_settings.json`，并补齐兼容回填。
5. 建立 `bindings/` 与现有角色配置之间的派生 / 回填契约，避免双真源。
6. 建立“小文件默认同步 / 大文件默认留本地”的资源清单。
7. 为 `time_indexed.db` 建立安全 shadow copy 导出链路。
8. 建立启动快路径与当前角色优先恢复流程。
9. 把 `profiles/characters.json` 的冲突策略从整文件覆盖改成角色条目级合并。
10. 把角色卡导入 / 导出与云存档导入 / 导出的语义边界写死，禁止后续实现重新混用。

完成标志：

- 可以明确列出默认同步、小文件、本地保留与永不同步的内容。
- 同名覆盖、改名事务、删除墓碑和资源缺失恢复规则已成体系。
- tombstone、名称复用、档案名审计、当前角色回退和迁移期兼容契约都已写死。
- 目标设备即使缺少模型资源，也能先看到猫娘和绑定状态。
- `profiles/characters.json` 的冲突决策已经与 `catalog/` / tombstone 规则对齐，不再依赖整文件 last-writer-wins。
- `time_indexed.db` 的导出 / 导入协议已经细化到可直接实现的停写、快照、替换与校验步骤。

### 阶段 2：接入 Steam AutoCloud 并优化体验

目标：把 Steam 作为传输层接入，同时保证启动、运行和退出体验可接受。

处理内容：

1. 配置 Steam 只监听 `cloudsave/`。
2. 补齐 `mark_dirty` 打点与覆盖审计。
3. 建立分域 dirty queue 与退出前限时 flush。
4. 增加导出耗时、体积、失败原因等可观测性。
5. 校验现有模型导入 / Workshop 恢复流程与绑定摘要兼容，无需新增云存档专属前端入口。

完成标志：

- 双机之间可以通过 Steam 稳定同步 v1 范围内的数据。
- 启动不会因为全量恢复而明显拖慢。
- 缺资源时的恢复路径仍落在现有导入 / Workshop 流程上。

### 阶段 3：继续演进长期记忆与合并策略

目标：在 v1 可上线后，再处理成本更高、改动更大的优化项。

处理内容：

1. 评估把长期记忆从整库 shadow copy 演进为事件流或更细粒度同步。
2. 如果 `profiles/characters.json` 的体积或冲突率成为问题，再考虑角色分片。
3. 从文件级覆盖逐步演进到更细粒度的冲突合并。

---

## 8. 验收标准

至少覆盖以下结果：

- Windows 首次启动时主存档根落在 `%LOCALAPPDATA%/N.E.K.O`，历史 `Documents` 旧档可迁移。
- `profiles/characters.json` 即使继续聚合导出，也不会因整文件 last-writer-wins 吃掉无关角色的并行修改。
- 本地角色改名时，会走名称级事务，至少保证 `profiles/characters.json`、当前角色与 `memory/<old_name>/` 不留下半完成状态。
- 跨设备导入改名结果时，表现为旧名称消失、新名称出现，而不是错误覆盖另一只同名角色。
- 删除角色会写 tombstone，旧设备不会把已删除角色重新导出回来。
- 目标设备已有本地模型资源时，可直接恢复绑定并使用本机资源。
- 目标设备缺少模型资源时，猫娘仍可见，后续通过现有模型导入或 Workshop 恢复流程补齐资源。
- `time_indexed.db` 导出 / 导入都有明确的角色级停写、checkpoint、SQLite backup、engine 释放与 reload 校验步骤。
- 导入必须先经 staging，失败可整体回滚。
- 启动时若云端无变化，应快速跳过导入；有变化时只导入变更文件。
- 启动导入由 `launcher` 或等价 bootstrap 单点持有屏障；`memory_server`、`main_server` 不会先按旧状态完成角色初始化再被覆盖。
- 启动期云导入与 Workshop 角色卡后台同步不会并发改写 `characters.json`。
- 运行期热导入、Workshop 同步、角色配置修改和记忆写入都受同一全局写栅栏约束，不会在导入应用期间交叉落盘。
- 云存档链路不会复用角色卡“整包模型资源导入 / 导出”语义；角色卡仍是人工分享 / 手工备份通道。
- `touch_set`、VRM / MMD 小型体验配置会与绑定摘要一起被稳定导出和恢复，或被显式标记为本地不漫游，而不是处于未定义状态。

---

## 9. 与当前项目实现的主要差距

这部分不是改设计，而是明确“目前代码还没完全走到文档目标态”的地方，避免误判。

- Windows 主存档根已基本符合方向，但 macOS / Linux 当前代码仍主要走 `Documents` / `cwd` 逻辑，尚未切到标准应用数据目录。
- 全局对话设置当前仍直接写 `user_preferences.json`，`conversation_settings.json` 还没有真正落地。
- `cloudsave/`、`manifest`、`bindings/`、tombstone、导入屏障、sync fence 目前仍是设计目标，不是既有实现。
- 资源来源现实上同时覆盖项目内置、用户导入和 Workshop 三类路径，因此绑定摘要必须以“来源 + 引用 + 指纹”为主，不能假设只有单一路径。
- Workshop 的部分删除路径当前仍会删除 `characters.json` 中的角色配置，说明“资源卸载”和“角色删除”还需要在实现层继续拆开。
- 当前角色卡导出 / 导入链路仍会按“角色 + 模型资源整包”工作，这条能力需要继续保留，但必须与云存档导出层严格分开。
- 当前改名 / 删除实现还没有覆盖 `memory/<name>/...` 目录迁移 / 清理、tombstone 生成和导入期事务回退，离文档目标态还有明显差距。
- `main_server` 启动后会后台执行 Workshop 角色卡同步，这条链路目前可能与未来云导入形成并发写入竞争，因此必须在实现层补导入屏障或串行调度。
- `touch_set`、VRM / MMD 小型体验配置当前仍直接散落在 `_reserved`，还没有收敛成文档定义的云端绑定摘要结构。
- `client_id + sequence_number` 的本地状态文件、名称复用撤销 tombstone 的规则、以及档案名审计整改链路，目前都还没有正式实现。

---

## 10. 结论

这份方案的核心不是“把哪些目录交给 Steam”，而是先建立一层稳定、可分类、可导入、可回滚的云存档规范。

v1 的明确落点是：

1. 运行时继续基于现有目录结构工作，不做大规模重构。
2. Steam 只同步 `cloudsave/`。
3. 默认同步猫娘目录、角色配置、对话设置、记忆、模型引用摘要、小型参数文件和轻量元数据。
4. Live2D / VRM / MMD / Workshop 等大文件资源在 v1 默认不进入云端，目标设备有则直接用，没有则用户手动导入或重新下载恢复。
5. 运行时与云端都继续按档案名组织，不再引入 `character_id`。
6. 同名角色按云端直接覆盖；改名在云同步语义上按“旧名称删除 + 新名称新增”处理，本地改名仍需事务化。
7. `time_indexed.db` 只以 shadow copy 方式进入云端。
8. 导入导出必须具备 staging、原子应用、冲突备份与回滚能力。

只要以上口径保持不变，文档与后续实现就不会再因为“临时补丁式追加”而偏离核心设计方向。
