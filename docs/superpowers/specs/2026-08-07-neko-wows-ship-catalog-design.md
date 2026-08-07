# Neko WoWS 舰船战斗参数目录设计

- 日期：2026-08-07
- 范围：`plugin/plugins/neko_wows`
- 状态：已实施；固定来源全量构建与回归验证通过

## 1. 背景与目标

`neko_wows` 当前从 `8111_for_wows` 接收遥测，标准化为 `WowsSnapshot`，再经过事实构建、事件检测、策略、仲裁和提示路由产生陪玩播报。遥测能识别舰船名称、类型、等级与敌我关系，但不包含完整舰船战斗参数。

本功能增加一套独立、版本化的离线舰船目录。每局战斗中，只要遥测已经识别出己方、友方或敌方舰船，插件就把该舰船的详细参考战斗参数以 `ai_behavior="read"` 注入模型上下文，使后续播报能基于可靠的舰船能力边界，而不是凭名称猜测。

核心目标如下：

1. 覆盖所有已识别的自身、友军和敌军舰船。
2. 离线 SQLite 是唯一自动查询路径，断网时功能仍完整可用。
3. 遥测没有提供实际配装时使用确定性的顶配参考方案；敌舰默认按顶配处理。
4. 同一舰种的完整参数块每局只注入一次，同时附带己方、友方和敌方累计识别数量。
5. 官方 Wargaming 查询仅作为模型显式调用的工具，不得成为自动回退路径。
6. 舰船目录的任何失败都不能影响现有遥测、检测、仲裁和播报链路。

## 2. 已确定的边界

### 2.1 本期包含

- 从公开的已解包数据生成规范化舰船目录。
- 基础属性、武器、弹药、防空、反潜、飞机、消耗品及潜艇专属参数中上游实际提供的部分。
- 顶配模块选择与并列侧选模块的保留。
- 多语言精确别名解析。
- 战局级目录版本冻结、去重、计数、分批注入和诊断。
- 一个受配置控制的官方在线查询 `@llm_tool`。
- 版本校验、空目录降级、构建校验和原子切换。

### 2.2 本期不包含

- 推测玩家真实舰长技能、升级品、信号旗、涂装或临时战斗增益。
- 根据玩家水平、胜率或战绩修正舰船参数。
- 模糊匹配、编辑距离猜船或由模型自行判定同名舰船。
- 完整装甲几何、逐距离穿深仿真或命中概率模拟器。
- 插件运行时自动下载或自动调用官方接口补库。
- 把官方查询结果写回离线目录。
- 在权利审查完成前随仓库分发上游原始 JSON 或生成后的完整数据库。

## 3. 关键设计决策

1. **独立数据库**：新增舰船目录，不改动现有 `tactical_knowledge.db` 的表、迁移和检索行为。
2. **离线优先**：自动上下文注入只读取本地 SQLite；官方接口永远不会被后台代码调用。
3. **参考值而非实装断言**：所有注入块明确标记 `configuration=reference_top`。没有实际模块 ID 时，不声称这是玩家真实配装。
4. **精确解析**：只接受规范化后的精确别名；冲突时用等级和舰种消歧，仍不唯一便返回未解析。
5. **战局冻结**：一局开始时固定目录版本，即使后台激活了新目录，本局也不混用两版数据。
6. **参数与计数分离**：完整参数块每舰种每局最多一次；后续才识别出的同型舰只只允许产生轻量计数修正，不重复参数块。
7. **提交成功后记账**：只有宿主返回 `{"submitted": true}` 才把批次记为已注入，拒绝或异常的批次保留待重试。
8. **原始数据不进提示词**：提示词只接收经过规范化、字段白名单和稳定渲染的参考资料。

## 4. 数据源与来源策略

### 4.1 主数据源

主适配器使用 [`wowsinfo/data`](https://github.com/wowsinfo/data) 中已经生成的 live 数据，只获取两个文件：

- `live/app/data/wowsinfo.json`
- `live/app/lang/lang.json`

不克隆约 2 GB 的完整仓库。构建器先把分支或标签解析为不可变 commit SHA，再通过固定仓库和固定路径下载这两个文件，并记录各自 SHA-256。

2026-08-07 调研快照如下，仅用于证明来源覆盖能力，不作为代码中的永久常量：

- 仓库 HEAD：`c4f6ae751548c8e9a4887f69555a847d1cc5a300`
- live 游戏版本：`15.6.0.0.12830008`
- public test 游戏版本：`15.7.0.0.13016296`
- `wowsinfo.json`：7,114,426 字节
- 约 1,202 艘舰船、3,114 种投射物、198 种能力
- 含模块树、本地化名称、主炮 sigma、Krupp、阻力、消耗品等官方百科通常不完整提供的数据

大和号可作为构建后的金丝雀样本：顶配参考应得到 97,200 HP、26,630 m 主炮射程、2.1 sigma、30.0 s 装填。该样本用于检测字段映射或单位换算回归，不用于替代全量校验。

### 4.2 校验与后备来源

- [`toalba/wows-render-gamedata`](https://github.com/toalba/wows-render-gamedata)：按客户端 build tag 保存的原始 GameParams；live 对应 tag 为 `v12830008`。用于抽样核对和主来源中断时的后续适配，不在首期运行时读取。
- [`landaire/wows-toolkit`](https://github.com/landaire/wows-toolkit)：活跃的 MIT 解析器/导出器，可作为以后直接读取 GameParams 的实现参考。
- [`WoWs-Builder-Team/DataConverter`](https://github.com/WoWs-Builder-Team/DataConverter)：用于校对数据结构、模块选择及公式，不直接复制其实现。

首期只实现 `wowsinfo` JSON 适配器。后备来源不能在同一次构建中静默混入主来源；若未来增加适配器，每个目录仍必须只有一个明确的主来源和完整 provenance。

### 4.3 许可与分发

`wowsinfo/data` 仓库声明为 MIT，生成工具另有 AGPLv3 许可，游戏内容本身仍受 Wargaming 条款约束。因此：

- 本项目自行实现转换器，不复制上游生成器代码。
- 数据库默认由用户或构建流程本地生成。
- 数据库元信息保留仓库、commit、源文件路径、哈希和生成时间。
- 权利审查完成前，不把原始 JSON 或生成数据库加入版本控制或发布包。

## 5. 总体架构

```text
固定上游 JSON
    -> WowsInfoSourceAdapter
    -> 校验 / 单位规范化 / 顶配求解 / 别名生成
    -> versioned ship-catalog-*.sqlite3
    -> active.json 原子切换

WowsSnapshot
    -> ShipResolver（精确匹配）
    -> BattleShipContext（战局冻结、计数、去重、分批）
    -> ShipReferenceRenderer（白名单文本）
    -> push_message(ai_behavior="read")
    -> 原有 FactBuilder / Detector / Policy / Arbiter / PromptRouter

模型显式工具调用
    -> wows_query_ship_official
    -> OfficialWowsApiClient
    -> 官方 HTTPS API
```

离线目录链路与官方工具链路共享规范化输出模型，但不共享持久化写入。官方结果只存在于一次工具返回和短时内存缓存中。

## 6. 组件职责

新增包位于 `plugin/plugins/neko_wows/ship_data/`。

### 6.1 `store.py`

- 读取和校验 `active.json`。
- 按只读、不可变方式打开指定版本 SQLite。
- 暴露目录元信息、精确别名查询和 profile 查询。
- 产生可被单局持有的 `CatalogSnapshot`。
- 在文件缺失、损坏、哈希不符或 schema 不兼容时返回 `NullShipCatalog`，不向上抛出致命错误。

### 6.2 `resolver.py`

- 规范化遥测舰名。
- 用名称、等级和舰种做确定性解析。
- 返回 `ResolvedShip` 或带稳定原因码的 `UnresolvedShip`。
- 不读取玩家名，不做模糊匹配，不调用网络。

### 6.3 `context.py`

- 管理当前 `(instance_id, battle_id)` 的 `BattleShipContext`。
- 首次 live 帧固定 `CatalogSnapshot` 和版本校验结果。
- 累计自身、友军、敌军的舰种数量。
- 管理待注入、已接受、重试和计数修正状态。
- 生成不丢舰船的有界批次。
- 战局结束或 identity 改变时释放快照并清空状态。

### 6.4 `renderer.py`

- 将 canonical profile 渲染为紧凑、稳定、中文可读的参考块。
- 只输出字段白名单，省略缺失值，不用 `0` 代替未知。
- 每个数值都使用明确单位，附带目录版本和 `reference_top` 标签。
- 不输出源 JSON、内部模块图或无关描述文本。

### 6.5 `official_api.py`

- 构造固定白名单内的官方 API 请求。
- 处理 Application ID、超时、响应上限、状态码映射、规范化和短时缓存。
- 不接收任意 URL，不读取或写入舰船 SQLite。

### 6.6 构建适配器

`source_wowsinfo.py` 负责读取两个固定 JSON 并生成中间模型；仓库级脚本 `scripts/build_neko_wows_ship_catalog.py` 负责获取、校验、写库和激活。脚本同时支持固定 commit 的网络来源和两个本地文件，便于可重复构建及测试。

固定 revision 模式只读取两个 `live` 路径并自动绑定 `source_channel=live`。本地文件模式必须显式声明 `--source-channel live`；路径中带有 `public_test` / `pt` 标记，或两个输入来自 live/PT 混合路径时，在读取和激活前直接拒绝。

输入安全门明确为：顶层版本和 `ships`、`projectiles`、`abilities` 结构必须存在；舰船数必须在 500 至 5,000 之间；舰船 ID 和 index 唯一；引用到的模块、组件、弹药和能力必须可解析；每艘纳入目录的舰船必须生成 primary profile。这个宽区间只用于拦截空文件、错误频道和结构突变，当前观测到的 1,202 艘不是硬编码断言。

## 7. SQLite 目录格式

数据库启用外键，构建完成后执行 `PRAGMA foreign_key_check` 与 `PRAGMA integrity_check`。运行时不迁移旧文件；schema 变化时生成新文件。

### 7.1 `catalog_meta`

单行表，`id` 固定为 1：

| 字段 | 含义 |
|---|---|
| `schema_version` | 本项目目录 schema 版本 |
| `catalog_version` | `game_version + source commit + builder version` 形成的唯一版本 |
| `game_version` | 上游 JSON 声明的客户端版本 |
| `channel` | `live`；首期拒绝把 PT 数据激活为 live |
| `source_repo` | 固定上游仓库 |
| `source_commit` | 完整 commit SHA |
| `source_paths_json` | 两个固定源路径 |
| `source_sha256_json` | 两个源文件哈希 |
| `generated_at_utc` | 生成时间 |
| `builder_version` | 转换器版本 |
| `content_sha256` | 排除生成时间和 SQLite 物理布局后的 canonical 内容摘要 |
| `default_language` | 默认显示语言 |
| `ship_count` / `profile_count` | 构建统计 |

### 7.2 `ships`

| 字段 | 含义 |
|---|---|
| `ship_id` | 官方数值 ID，主键 |
| `ship_index` | 稳定内部索引，例如 `PJSC013`，唯一 |
| `name_key` / `display_name` | 本地化键与默认名称 |
| `nation` / `ship_class` / `tier` | 国家、舰种和等级 |
| `is_premium` / `is_special` / `is_paper` | 来源提供的分类标志 |
| `availability_group` | live、preserved、test 或来源原值 |

### 7.3 `ship_aliases`

| 字段 | 含义 |
|---|---|
| `alias_norm` | 规范化精确匹配键 |
| `ship_id` | 外键 |
| `alias` | 原始别名，仅供诊断 |
| `language` | 本地化来源 |
| `alias_kind` | `localized_name`、`ship_index`、`name_key` 或显式兼容别名 |

主键为 `(alias_norm, ship_id)`，并为 `alias_norm` 建索引。一个别名可以合法指向多个舰船，冲突由 resolver 消歧而不是由构建器任意丢弃。

### 7.4 `ship_profiles`

| 字段 | 含义 |
|---|---|
| `profile_id` | 稳定 profile 标识，主键 |
| `ship_id` | 外键 |
| `configuration` | 首期为 `reference_top` |
| `variant_key` | `primary` 或具体侧选模块键 |
| `is_primary` | 是否为运行时默认参考 |
| `profile_schema_version` | canonical profile 版本 |
| `profile_json` | 规范化、单位明确的 JSON |
| `profile_sha256` | profile 内容哈希 |

每艘舰只能有一个 `reference_top` primary profile。侧选 profile 是完整结果，但运行时默认只注入 primary。

`profile_id` 由 `ship_id:configuration:variant_key` 确定性生成，不使用随机 UUID。

### 7.5 `module_selections`

| 字段 | 含义 |
|---|---|
| `profile_id` | 外键 |
| `slot` | hull、artillery、engine、fire_control 等标准槽位 |
| `module_key` | 上游稳定模块键 |
| `module_index` | 来源进度索引 |
| `selection_kind` | `terminal`、`sidegrade_primary` 或 `sidegrade_alternative` |
| `component_ids_json` | 该模块实际引用的组件 ID |

主键为 `(profile_id, slot)`。该表用于审计顶配选择，不直接进入提示词。

## 8. Canonical profile

`profile_json` 使用嵌套结构，但所有叶字段都带语义或单位后缀。统一规则如下：

- 距离：`*_m`
- 时间：`*_s`
- 舰速：`*_knots`
- 投射物速度：`*_mps`
- 口径、装甲：`*_mm`
- 角度：`*_deg`
- 比例和概率：`*_ratio`，范围 `[0, 1]`
- 数量：`*_count`
- 伤害：`*_damage`

禁止同一字段在不同舰种中使用不同单位。非有限数、负时间、明显越界的距离或概率会使构建失败，而不是原样入库。

规范化结构按来源可用性包含以下段落：

| 段落 | 代表字段 |
|---|---|
| `survivability` | `hit_points`, `torpedo_protection_ratio`、来源实际提供的装甲摘要 |
| `mobility` | `max_speed_knots`, `underwater_speed_knots`, `turning_radius_m`, `rudder_shift_s` |
| `concealment` | 水面、空中、烟内、潜望深度探测距离 |
| `main_battery` | 炮塔/炮管布局、口径、射程、装填、180 度转炮、sigma、来源提供的散布值 |
| `projectiles` | AP/HE/SAP 类型、最大伤害、点火率、初速、质量、Krupp、阻力等 |
| `secondary_battery` | 布局、射程、装填、sigma 和弹药 |
| `torpedoes` | 发射器布局、装填、射程、航速、伤害、被发现距离、进水率 |
| `anti_air` | 各圈最小/最大距离、持续伤害、命中率、黑云数量与伤害 |
| `asw` | 深水炸弹或空袭的射程、装填、批次、数量和弹药伤害 |
| `aircraft` | 机种、编队/攻击批次、血量、速度、整备时间和机载武器 |
| `submarine` | 潜航容量、恢复、不同深度航速、声呐脉冲和专属武器 |
| `consumables` | 槽位、可选能力、持续时间、冷却、次数、作用距离和主要效果 |

来源没有的段落或字段直接省略。运行时渲染器不会根据同类舰或官方接口补值。

## 9. 顶配与侧选模块算法

“顶配”只涉及舰船可研发模块，不包含升级品、舰长技能、信号旗、涂装或战斗中临时效果。

对每个模块槽位执行以下步骤：

1. 根据上游 predecessor、next-module 和组件引用建立有向无环图。
2. 删除仍有可研发后继的严格祖先，保留可达终端模块。
3. 只有一个终端模块时直接选择。
4. 存在多个互不支配的终端模块时，将它们视为侧选而不是强行判定强弱。
5. primary 侧选按固定顺序决定：优先上游显式 `top` 标记；其后依次取更高的模块进度索引、研发经验、银币成本；仍相同时取字典序最小的稳定模块键。最后一项保证不同机器上结果一致；该选择只代表“参考方案”，不代表玩法最优。
6. 用各槽 primary 终端模块生成一份 primary profile。
7. 每个非 primary 侧选模块再生成一份完整侧选 profile：其他槽保持 primary，只替换该槽。这样保留每个侧选的属性影响，又避免盲目生成模块笛卡尔积。

构建校验要求每艘可用舰船恰有一个 primary profile，且 primary 的每个可升级槽都落在终端模块上。无法解释的环、悬空组件或缺失关键 hull 会使该次构建失败，不激活半成品。

## 10. 舰船身份解析

别名规范化流程固定为：Unicode NFKC、`casefold`、去除首尾空白、连续空白折叠为一个空格。除 NFKC 自带的全半角归一化外，不删除标点、不做简繁自动转换、不做罗马音猜测。

构建器为每艘舰加入：

- `lang.json` 中实际存在的各语言名称；
- `ship_index`；
- `name_key`；
- 经明确测试确认的遥测兼容别名。

解析顺序如下：

1. 用规范化名称精确查询候选。
2. 若只有一个候选，返回该舰。
3. 若有多个候选，先以 `tier` 精确过滤，再用规范化的 `ship_type` 映射过滤。
4. 仍不唯一时返回 `ambiguous_alias`；零候选返回 `alias_not_found`。

舰种映射只接受显式表项，例如 `Battleship`、`Cruiser`、`Destroyer`、`AirCarrier` 和 `Submarine` 及其已知本地化值；未知舰种不参与消歧。

解析失败只影响该舰的参数注入。不得把最相似候选、同级同型舰或玩家名称交给模型猜测。

## 11. 战局运行流程

在现有 `_evaluate_locked(snapshot)` 中，舰船目录观察发生在 scene context 注入之后、事件播报构建之前。该步骤使用独立的异常边界；无论结果如何，原有 `FactBuilder -> DetectorRegistry -> Policy -> Arbiter -> PromptRouter -> Dispatcher` 继续执行。

### 11.1 建立战局会话

首次收到 live 帧时：

1. 用 `snapshot.identity` 建立 `BattleShipContext`。
2. 从 `active.json` 获取并固定一个 `CatalogSnapshot`。
3. 获取客户端版本并执行版本策略。
4. 遍历 `snapshot.ships` 以及可匹配的 `snapshot.own_ship`。

客户端版本优先取遥测明确提供的 game version，其次只读解析已配置 `game_dir` 下的 `game_info.xml`。两者都没有时记为 `unknown`。

### 11.2 累计与去重

每帧只处理已有名称的舰船，并按最终 `ship_id` 累计：

- `self_count`
- `ally_count`
- `enemy_count`

去重使用稳定的遥测对象身份；同一个对象跨帧出现不会重复计数。完整参数是否已注入则按 `ship_id` 记账，因此同型舰只共享一个参数块。

若某舰种参数块已经成功注入，之后又识别出同型舰，只发送轻量 `count_update`，例如“敌方大和累计从 1 艘更新为 2 艘”，不重复数千字符的参数。

### 11.3 分批提交

- 每个参数块是不可拆分单元。
- 批次在 `ship_catalog_context_batch_chars` 上限内尽量装满；单个参数块超过上限时独占一个批次，不删字段来伪装成功。
- 全量构建测试必须渲染每个 primary profile；任何单舰参数块超过 SDK 单个 text part 的硬限制时构建失败，不能进入运行时无限重试。
- 每批使用唯一 `coalesce_key`：`wows_ship_reference:<battle identity>:<batch id>`，避免宿主合并时覆盖早先舰船。
- 提交参数固定为 `visibility=[]`、`ai_behavior="read"`、`priority=0`。
- metadata 包含 `kind=ship_reference`、战局 identity、目录版本、批次 ID 和 ship IDs。
- 只有明确收到 `submitted=true` 后，才将该批所有 ship IDs 标为已注入。
- 拒绝、异常或背压时保留批次，按有界退避在后续帧重试；重试不阻塞事件播报。
- dry-run 中完成解析、渲染和时间线预览，但不调用宿主，也不标记已注入。关闭 dry-run 后，同局待处理舰船仍可正常提交。

### 11.4 结束与切换

收到 `STATUS_ENDED` 后，先允许当前帧原有结束播报完成，再发送现有 restore context、清空 `BattleShipContext` 并释放目录快照。若没有结束帧但 `(instance_id, battle_id)` 改变，也立即重置，防止跨局串数据。

## 12. 注入文本契约

渲染结果使用明确边界，避免与指令混淆：

```text
<<<WOWS_SHIP_REFERENCE>>>
catalog_version=15.6.0.0.12830008:...
version_status=match
configuration=reference_top
notice=这是离线顶配参考，不代表玩家实际配装或实时增益

舰船：大和 | X级 | 战列舰 | 自身0 友军1 敌军2
生存：HP 97200；鱼雷防护 ...
机动：最大航速 ... kn；转向半径 ... m；转舵 ... s
主炮：3×3 460 mm；射程 26630 m；装填 30.0 s；sigma 2.1；...
...
<<<END_WOWS_SHIP_REFERENCE>>>
```

字段顺序固定，便于测试和模型稳定理解。标签、目录版本、`version_status=match|mismatch|unknown`、配置说明和数量不可省略。原始本地化描述、上游未知键、空值和内部哈希不得进入正文。

## 13. 官方在线查询工具

插件增加：

```text
wows_query_ship_official(ship, configuration="top", language=...)
```

它通过 SDK 的 `@llm_tool` 暴露，只在模型显式选择工具时运行。上下文注入、resolver、目录缺失和版本不匹配都不得触发它。

### 13.1 参数与解析

- `ship`：必填字符串；接受离线目录中的精确别名或数值 ship ID。
- `configuration`：首期只接受 `top`，其他值返回 `invalid_configuration`。
- `language`：可选，必须在官方支持语言白名单内；默认取插件配置。
- region 不由调用者提供，固定读取配置。

文本名称先通过离线 resolver 得到官方 ship ID。离线目录不可用时，数值 ID 仍可查询；文本名称返回稳定错误，不遍历全站做模糊搜索。

### 13.2 网络边界

客户端内部只允许以下 HTTPS host：

- `api.worldofwarships.com`
- `api.worldofwarships.eu`
- `api.worldofwarships.asia`

调用者不能传 scheme、host、path 或任意 URL。Application ID 由配置提供，日志、诊断、metadata 和工具结果中一律脱敏。响应设置超时和最大字节数，只接受合法 JSON。

### 13.3 官方配置查询

工具先读取官方舰船及模块树，再按与离线构建器相同的“终端模块 + 确定性侧选 primary”规则构造 `shipprofile` 请求。返回值转成 canonical 字段子集，并明确：

- `source=official_wargaming_api`
- region、language、查询时间和官方返回的游戏版本信息
- `configuration=top`
- 字段覆盖情况

官方接口没有的数据保持缺失，不用离线值悄悄拼接成“官方结果”。

### 13.4 缓存与错误

成功响应按 `(region, language, ship_id, configuration)` 放入短时内存 LRU，默认 TTL 300 秒，插件重启即丢失。不得写入 SQLite 或宿主长期记忆。

稳定错误码包括：

- `disabled`
- `missing_application_id`
- `invalid_region`
- `invalid_language`
- `invalid_configuration`
- `catalog_unavailable`
- `ship_not_found`
- `unauthorized`
- `rate_limited`
- `timeout`
- `network_error`
- `upstream_error`
- `invalid_response`

错误以工具结果返回，不抛到对话主循环，也不触发自动重试风暴。

## 14. 配置

在 `[neko_wows]` 中增加以下设置，并在 `WowsConfig.from_mapping` 中执行类型、范围和枚举校验：

```toml
ship_catalog_enabled = true
ship_catalog_version_policy = "warn" # warn | strict
ship_catalog_language = "zh-CN"
ship_catalog_context_batch_chars = 12000

official_api_enabled = false
official_api_region = "asia"         # na | eu | asia
official_api_application_id = ""
official_api_language = "zh-cn"
official_api_timeout_seconds = 5.0
official_api_cache_ttl_seconds = 300.0
```

离线语言配置使用 BCP 47 形式；适配器显式把 `zh-CN` 映射到上游 `zh_sg`、`zh-TW` 映射到 `zh_tw`，其他语言也必须通过固定映射表，不能把任意字符串拼成源字段名。

目录位置不接受任意网络 URL，固定落在 `self.data_path("ship_catalog")`。Application ID 为空或 `official_api_enabled=false` 时，工具返回 `disabled` 或 `missing_application_id`，离线功能不受影响。

## 15. 版本策略与原子更新

目录文件命名示例：

```text
ship_catalog/
  active.json
  ship-catalog-15.6.0.0.12830008-c4f6ae75-v1.sqlite3
```

`active.json` 只保存相对文件名、目录版本、schema 版本、game version 和数据库 SHA-256。读取时拒绝绝对路径、`..` 和目录逃逸。

构建及激活顺序：

1. 在临时目录下载或读取两个源文件。
2. 校验来源、结构、引用、数值、数量和版本。
3. 写入一个全新、不可变的版本化 SQLite。
4. 执行数据库完整性检查并计算哈希。
5. 在目标目录写 `active.json.tmp`，flush 后用 `os.replace` 原子替换 manifest。
6. 永不覆盖当前打开的 SQLite 文件。

这种做法在 Windows 上不需要替换已打开的数据库。旧战局继续持有旧文件；新战局重新读取 manifest。旧版本至少保留一个用于回滚，清理由显式维护命令完成，不在战斗中自动删除。

版本比较采用规范化后的完整 installed version：

- `warn`：版本不一致或无法确认时继续注入，但在每个参考块和诊断中明确目录版本状态。
- `strict`：只有客户端版本与目录 `game_version` 完全一致时才注入；不一致或未知时该局使用 `NullShipCatalog`。

## 16. 降级与故障隔离

| 故障 | 行为 |
|---|---|
| manifest 或 DB 不存在 | 使用 `NullShipCatalog`，原有播报正常 |
| DB 哈希、schema 或完整性失败 | 拒绝打开并记录一次诊断，不尝试修复活动文件 |
| 舰名未解析或有歧义 | 跳过该舰，保留原因码，不猜测 |
| profile 缺失或渲染失败 | 跳过该舰参数块，其他舰和播报继续 |
| 宿主拒绝 read 批次 | 不记已提交，退避后重试 |
| 目录更新发生在战斗中 | 当前战斗继续使用冻结版本 |
| 客户端版本不匹配 | 按 `warn` 或 `strict` 策略处理 |
| 官方 API 配置或网络失败 | 仅本次工具调用返回稳定错误，不影响离线目录 |
| 新目录构建校验失败 | 保持旧 `active.json` 不变 |

舰船目录不接入 `NekoDispatcher` 的播报失败熔断，也不能因为 read context 失败而暂停事件播报。它有自己的轻量重试与诊断计数。

## 17. 诊断与可观测性

新增 `STAGE_SHIP_CATALOG` 时间线阶段，记录状态而不记录 Application ID 或上游原始响应：

- `loaded` / `null_catalog`
- `version_match` / `version_mismatch` / `version_unknown`
- `battle_frozen` / `battle_reset`
- `resolved` / `unresolved`
- `batch_preview` / `batch_submitted` / `batch_declined` / `batch_retry`
- `count_update`

dashboard/diagnostics 增加：

- 活动目录版本、game version、source commit 和 schema version
- 当前战局冻结版本及版本策略结果
- 已识别、已解析、未解析、待提交和已提交的舰种数
- 未解析原因的聚合计数
- 官方工具是否启用、region、是否已配置 key、缓存命中统计

只显示“key 已配置/未配置”，绝不显示 key 本身。舰船解析不需要玩家名，诊断中也不新增玩家身份数据。

## 18. 与现有代码的集成点

计划新增：

```text
plugin/plugins/neko_wows/ship_data/__init__.py
plugin/plugins/neko_wows/ship_data/store.py
plugin/plugins/neko_wows/ship_data/resolver.py
plugin/plugins/neko_wows/ship_data/context.py
plugin/plugins/neko_wows/ship_data/renderer.py
plugin/plugins/neko_wows/ship_data/official_api.py
plugin/plugins/neko_wows/ship_data/source_wowsinfo.py
scripts/build_neko_wows_ship_catalog.py
```

计划修改：

- `domain/contracts.py`：新增经过钳制的目录和官方 API 配置。
- `domain/snapshot.py` 与 `adapters/schema_adapter.py`：在来源存在时保留明确的客户端 game version；不改变现有 capability 含义。
- `__init__.py`：初始化目录组件，在 `_evaluate_locked` 中观察舰船，shutdown 时释放资源，并声明 `@llm_tool`。
- `adapters/runtime_timeline.py`：新增目录阶段。
- `plugin.toml`：加入默认安全配置，官方 API 默认关闭。
- dashboard 和 diagnostics 类型/视图：显示目录状态。

现有 `ContextInjector` 继续负责战局场景说明和 restore；舰船参数由独立的 `BattleShipContext` 管理。现有 `KnowledgeStore`、`DocumentImporter`、`WowsTacticsRepository` 及 `tactical_knowledge.db` 均不迁移、不复用。

## 19. 测试设计

### 19.1 构建器

- 最小上游 fixture 能生成完整 schema。
- 固定 commit 与两个源文件哈希写入 metadata。
- 单位换算、空值省略、概率范围和数值有限性。
- 严格升级选择终端模块。
- 多终端侧选全部保留且 primary 在重复构建中一致。
- 缺组件、模块环、重复 ID、PT/live 混用和异常 ship count 均拒绝激活。
- 大和金丝雀字段为 97,200 HP、26,630 m、2.1 sigma、30.0 s。
- 构建失败时旧 manifest 字节不变；成功时 manifest 指向校验通过的新文件。

### 19.2 Store 与 resolver

- 正常打开、只读查询、哈希和 schema 校验。
- manifest 绝对路径、路径逃逸和文件替换攻击被拒绝。
- 空目录和损坏目录返回 Null 实现。
- 中英日等已收录别名精确匹配。
- NFKC、大小写和空白规范化。
- 等级/舰种消歧。
- 相似拼写不命中，歧义不猜测。

### 19.3 Renderer 与战局上下文

- 自身、友军、敌军均被观察。
- 同型舰完整参数只出现一次，数量正确。
- 后识别同型舰只生成 count update。
- 多批次覆盖全部舰种，批次 key 唯一，无静默截断。
- `submitted=false` 不记账，后续可重试；`submitted=true` 后不重复。
- dry-run 只有预览，切换真实输出后仍能提交。
- identity 改变和 `STATUS_ENDED` 清空状态。
- manifest 战中切换时当前战局版本不变，下一局使用新版。
- `warn` 与 `strict` 版本策略分别生效。
- 注入正文不含原始 JSON、未知字段或密钥。

### 19.4 官方工具

- 只有显式工具调用产生 HTTP 请求。
- region host 白名单与 language/configuration 校验。
- key 缺失、401/403、429、超时、网络错误、非 JSON 和超大响应映射到稳定错误码。
- 日志、异常和返回值不泄露 Application ID。
- TTL/LRU 命中正确，插件重启不持久化。
- 工具调用前后舰船 SQLite 内容和 mtime 不变。

### 19.5 回归与集成

- 在 replay fixture 中，舰船 read context 先于同帧 respond 播报提交。
- 无目录、目录损坏、全部舰船未解析时，既有 call-out 数量和结果保持不变。
- 网络完全禁用时，离线舰船参数仍可注入。
- 现有 `test_neko_wows_*` 全部通过。

## 20. 验收标准

实现完成需同时满足：

1. 可从固定 `wowsinfo/data` commit 的两个文件重复生成相同的 canonical `content_sha256` 和 profile 哈希；数据库文件自身另以 manifest 中的 SHA-256 做完整性校验。
2. 每艘可用舰船有且只有一个 primary `reference_top` profile，所有严格升级槽使用终端模块。
3. 一场包含自身、友军、敌军和重复舰种的回放中，所有可精确识别舰种都获得参数，完整参数块不重复，数量可追踪。
4. 自动路径在抓包或 HTTP mock 中产生零个外网请求。
5. 官方接口只在 `wows_query_ship_official` 显式调用时访问固定官方 host，且不写库。
6. 缺库、坏库、版本不匹配和官方网络失败均不改变现有事件检测与播报能力。
7. 战中激活新版目录不会让同一战局混用版本。
8. 提示词、日志、时间线和面板不含 Application ID 或上游原始 JSON。
9. 生成数据未被加入版本控制，provenance 和许可说明完整保留。

## 21. 实施顺序建议

后续实现计划应按以下依赖顺序拆分：

1. canonical 模型、fixture 与构建器测试。
2. SQLite schema、构建校验和原子 manifest。
3. 只读 store、Null 实现和 resolver。
4. renderer 与 BattleShipContext。
5. 插件生命周期、时间线和 UI 诊断集成。
6. 官方 API 客户端与 `@llm_tool`。
7. 回放、故障注入、离线和完整回归验收。

## 22. 固定来源验收记录

2026-08-07 在本地工作树使用全新临时目录执行固定 revision 构建，未复用既有 SQLite 或源文件。验收结果如下：

- `wowsinfo/data` revision：`c4f6ae751548c8e9a4887f69555a847d1cc5a300`
- 游戏版本与频道：`15.6.0.0.12830008` / `live`
- `live/app/data/wowsinfo.json` SHA-256：`93968d1e6ac5f221268021b6cd494d00e95857e926910dda596e19babbf25490`
- `live/app/lang/lang.json` SHA-256：`4b358f9c365bd05d5ac5c2ac58aafb0bef721150503c39e4616a79a7d181d87f`
- canonical `content_sha256`：`6a6c63ea545e7664e7b2437f50f0e3fa6e21357ff7431a4ed20227304269f10c`
- 舰船 / primary profile / 全部 profile：`1202 / 1202 / 1212`
- 大和金丝雀：`97200 HP / 26630 m / 30 s / sigma 2.1`
- 飞机覆盖：`111` 个 primary profile，合计 `290` 个飞机条目
- 潜艇覆盖：`42 / 42` 个 primary profile 含潜航容量
- 全量 primary 使用真实 renderer 通过构建门禁；最长块为合众国（`PASA111`），`1826` 字符、`3142` UTF-8 字节，低于 `240 KiB` 构建硬限制

验收产物只位于系统临时目录；源 JSON、SQLite 和 manifest 均未加入版本控制。
