# N.E.K.O 小剧场 v2.6 功能设计与架构说明

## 1. 文档状态

本文同时说明三件事：

1. **v2.4 历史基线**：以作者静态剧情图为权威，支持推荐选项、自然语言命中和作者预设隐藏支线。
2. **v2.5 已完成基线**：在 v2.4 稳定主干上增加受约束的运行时临时支线，让玩家连续坚持未预设的合理行动时，剧情能够围绕该行动继续发展。
3. **当前工作区的 v2.6 开发**：吸收多模型架构点评中经当前代码核实后仍成立的问题，已经依次收口意图连续性、技术降级语义、真实端到端观测、统一事实读取、活动支线的新意图转交、上下文完整性、内部字段隔离、离线叙事评测、Story 作者真源与公开投影边界，以及面向单局 Bug 复盘的 Session 私有模型返回记录；内容槽的可执行语义、真实模型叙事质量和 Session 生命周期仍按后续证据继续改造。

为避免其他开发者或大模型把计划误认成现状，本文使用以下状态：

| 标记 | 含义 |
|---|---|
| **[现有]** | v2.4 或 v2.5 基线已经具备，v2.6 继续沿用 |
| **[已实现]** | 当前工作区已经具备，可从运行链和测试中验证 |
| **[开发中]** | 已进入 v2.6 明确范围，但尚未完成全部代码与验证 |
| **[后续]** | 点评成立，但必须在首批数据或独立设计收敛后实施 |
| **[不采纳]** | 与产品边界冲突、当前代码已解决，或缺少事实依据 |

本文是 v2.6 的开发与交接依据。v2.5 功能链、确定性测试、真实模型长跑、脱敏职责指标和 Electron 实体窗口验证已经完成；v2.6 首批范围见 3.6 和阶段七，后续已实施范围见阶段八至阶段十四。文档不得用 v2.5 的完成数据替代 v2.6 新行为验证。最终事实始终以当前代码、测试、真实模型演绎记录和 Electron 运行结果为准。

v2.3 重型框架的删减背景参见：

- [`neko-theater-slimming-proposal.md`](./neko-theater-slimming-proposal.md)

## 2. 一句话理解小剧场

N.E.K.O 小剧场是一套只有“玩家”和“当前活跃猫娘”直接说话的互动叙事系统。

作者提供故事世界、主线目标、关键因果和可结束范围；服务端保存已经真实发生的剧情状态；大模型负责理解玩家自由输入，并用当前猫娘的人格演出这一回合；前端只展示已经提交成功的公开结果。

v2.5 的核心变化是：

> 推荐选项从“唯一能推进剧情的门票”变成“稳定的主线建议”。玩家连续坚持一个符合当前世界、但作者没有提前写成按钮的行动时，系统可以生成一段受约束、可恢复、能汇流或结束的临时支线。

v2.6 的核心变化是：

> 不再把“相邻两轮说法相同”误当成“玩家坚持”的唯一证据，也不再把模型或合同故障误算成玩家没有推进；活动支线中的新行动只有在玩家明确要求结束当前支线后才进入受约束转交，普通转折继续由当前 Branch Actor 承接；同时用完整回合指标衡量玩家真正等待的时间，而不是用单次模型调用耗时代替端到端体验。

## 3. 产品目标与边界

### 3.1 v2.5 产品目标

1. 玩家可以用自然语言实施推荐行动，不必逐字复述按钮。
2. 玩家可以围绕当前场景自由聊天，猫娘必须优先回应本轮输入。
3. 玩家连续两次坚持同一个合理的图外意图时，系统默认在第二次进入围绕该意图生成的临时支线。
4. 临时支线已经公开发生的物品、行动和关系结果可以成为权威事实，后续剧情不能遗忘或反向否定。
5. 临时支线完成当前剧情功能后，可以带着结果自然汇回作者主线；若玩家选择造成有效分歧，也可以进入作者允许的支线结局域。
6. 推荐选项继续存在，但只提供可执行方向，不向玩家解释路由、计数、世界契约或系统规则。
7. Session 刷新、网络重试、窗口恢复和 TTS 继续保持幂等，不能因为引入动态支线破坏当前稳定底座。

### 3.2 必须保持的叙事边界

1. 一场演出只有玩家与当前活跃猫娘两位直接发言者。
2. 猫娘只能依据眼前观察、玩家已经说过的话、已公开事实和已建立共同记忆回应，不能读取玩家内心或未来剧本。
3. 旁白只描述环境、事件和可见动作，不能替玩家决定心理、动机或未输入的行动。
4. 关系升级、身体接触、承诺和关键秘密必须经过公开铺垫、双方行动与适当同意。
5. 模型不能绕过服务端直接写 Session、revision、正式事实、Ending 或静态故事文件。
6. 支线不是对“不点推荐按钮”的惩罚；不同结局必须来自玩家选择造成的有效叙事后果。
7. 后续正式剧本由用户提供；当前内置剧本只作为兼容性与端到端测试夹具，不定义框架默认题材、物品、关系或剧情节奏。
8. 通用运行时与 Prompt 只能消费 Story Package 的规范字段，禁止按当前剧本的 `story_id`、节点、Choice、关键词或固定文案增加特殊分支；内容专属规则必须写回对应 Story Package。

### 3.3 v2.5 明确不做

1. 不引入 Neo4j 或其他图数据库。
2. 不恢复 v2.3 的 Runtime Graph Overlay、Dynamic Candidate、多对象 Overlay Plan 或四阶段模型编排。
3. 不建设多 NPC 调度、多猫娘同场扮演或通用世界模拟器。
4. 不让每句闲聊都自动变成长期事实。
5. 不允许玩家一句无关输入立即改写世界真相、人物身份或故事类型。
6. 不要求前端展示“已偏离一次”“正在生成支线”等破坏沉浸感的系统说明。
7. 不在第一次图外意图时投机预生成 Patch；是否需要预生成只能在真实 P95、成本和废弃率证明确有必要后另行评估。
8. 不跨 Session 或跨玩家复用完整 Runtime Branch Patch；只允许复用 schema、Prompt 模板和作者静态素材。
9. 不在 Session 原子提交前向前端流式展示角色对白、支线旁白或动态事实。
10. 不把校验失败的 Patch 自动改绑到其他 Narrative Goal 或 Ending Domain；失败必须保守停留，不猜测作者意图。

### 3.4 用户剧本通用性开发硬约束

后续正式剧本来自用户，框架不能把当前内置剧本当成默认世界。该约束高于单个示例跑通、短期模型效果和局部测试便利性，所有 v2.5 代码审查均须逐项确认：

1. 通用运行时、Prompt、Projector 和前端只能读取 Story Package schema、当前 Session、公开事实与稳定公共接口，不得读取“当前剧本是谁”后切换专用逻辑。
2. 禁止在通用代码中匹配当前内置剧本的 `story_id`、节点 ID、Choice ID、角色名、物品名、地点名、关键词、固定台词或剧情顺序；把这些内容换成另一份合法 Story Package 后，框架行为仍应成立。
3. 当前内置剧本只允许出现在三类位置：Story Package 内容本身、明确标注为示例的设计文档、端到端或兼容性测试夹具。示例不得反向成为运行时协议。
4. 某个用户剧本确实需要的新能力，必须先抽象成有明确语义和校验规则的通用 schema，再至少用两份题材或结构不同的 Story Package 证明它不是单剧本特化；否则留在该剧本内容层。
5. 前端不得按剧本专门增加 DOM 分支、选择器、颜色、固定按钮或剧情提示。选剧接口只接收标题、稳定背景、角色身份、不剧透目标和初始 Scene；演出接口再按当前状态提供 Scene、作者 Choice、道具、线索和已验证动态公开实体。生成约束不得进入选择页。
6. 框架级回归必须保留“扫描通用运行时中是否出现当前剧本专属标记”的守门测试；新增功能若必须引用测试剧本内容，只能在对应 fixture 或测试断言中引用。
7. 性能优化同样受此约束：缓存键、检索索引、Prompt 裁剪和回退路径必须基于 schema、revision、Goal、事实类型或稳定引用，不能按现有剧情用词建立捷径。

### 3.5 v2.6 多模型点评采纳结论

本节记录对 `/Users/mac/Desktop/各模型点评.rtf` 的逐项核实结果。点评只提供调查线索，不能直接成为实现依据；每个采纳项都必须能在当前代码、测试或真实运行指标中找到证据。

| 点评方向 | 结论 | v2.6 处理 |
|---|---|---|
| 静态事实与动态事实形成长期双轨 | **已按安全边界采纳** | 已实现统一只读 Fact View：读取 `narrative_facts`，并按 `completed_goal_ids` 追加 Narrative Goal 的作者 `completion_fact_projections`。不合并存储，也不让模型生成的原始 Branch Fact 三元组直接命中静态规则。 |
| `streak=2` 容易被自然问答或短暂闲聊打断 | **采纳** | 首批把一次 idle 从“立即清除”改为有界休眠；只有明确继续、细化或替换才改变支持证据，休眠本身不增加次数。 |
| 长对话中同一意图可能因中间闲聊被过早遗忘 | **采纳为有界修正** | 首批只允许同节点的一次 idle 进入休眠，连续第二次 idle、节点变化或明确替换仍会清理；不引入无限期语义记忆，也不让休眠态自行触发 Planner。 |
| Pending Intent 与普通自由意图争抢调度 | **修正后采纳** | 当前调度顺序是确定的，不存在两条路径随机竞争；真实问题是复合输入后仍可能要求玩家表达第三次。首批允许合法 Pending 在下一轮明确确认时提供前一条证据，但 Pending 单独仍不能激活支线。 |
| Router 拆分复合输入是静默故障点 | **已建立持续评测入口** | 保留自然语言复合输入和现有严格字段、长度与低置信拒绝边界，不要求玩家使用分隔符。阶段十增加跨题材合成人工金标和精确结构评分；默认 CI 只验证数据与评分器，真实准确率仍须显式运行模型后报告。 |
| 三层锁全程包住慢模型并导致全局死锁 | **不按原描述采纳** | 实际只有 `session_guard` 覆盖完整回合，`character_guard` 在提交前获取，异步锁不会阻塞整个事件循环；但同 Session 的锁等待问题成立。首批先记录完整回合和锁等待，后续再决定是否引入 Pending Turn 与锁外模型调用。 |
| 安全回退生硬且会破坏沉浸 | **已按无权威上下文收口** | Router 技术故障继续不冒充真实 idle；普通/入口/活动支线与 handoff 回退现在只使用有界公开 Scene 标题、合同已验证的行动方向、合法 History 是否存在和“是否已有已提交进展”的布尔语义，不复述任意玩家原话、不新增事实、不代做下一步，也不消耗支线预算。 |
| Opaque Choice 会出现失效或幻觉 ID | **不按原描述采纳** | 当前稳定 ID 由服务端从 `branch_id + beat_id` 派生，提交时会重验并拒绝旧 ID；无需重做 ID。Actor 台词与按钮语义对齐仍列后续评测。 |
| 活动支线期间提出新意图可能被现有支线吞没 | **已按显式转交边界采纳** | 活动支线自由输入新增独立 `branch_handoff` 轻量分类。只有玩家同时明确结束当前支线、提出具体新行动，两个逐字摘录均能由服务端在本轮原话中验证且置信度达到严格阈值时，才以 `intent_handoff` 关闭旧支线；普通转折继续交给 Branch Actor，不引入支线栈或嵌套 Patch。 |
| 内容槽没有 Schema 与测试 | **核实后补齐可选严格模式** | 原有 Loader 只能证明槽位引用和 traits 外形，不能证明具体物件语义。阶段十一采用作者有限 Catalog：作者目录成员以 `content_id` 绑定 Patch、Fact、提交和恢复；无 Catalog 的旧 Story 保持兼容，但作者诊断固定标为 `declarative_only`，不得宣称已完成语义证明。 |
| 支线被作者 Choice 中断没有状态机 | **不采纳** | 当前已有 `author_choice` 退出码、事实保留、History 与预算不消耗规则，不重复建设。 |
| 恢复中的“可安全关闭”标准含糊 | **部分采纳** | 当前代码已有 branch ID、作者锚点、revision、Patch 与事实结构检查；后续把判定矩阵补进长期架构文档，并评估损坏 Patch 下事实来源解释是否充分。 |
| 模型职责过度拆分导致支线入口延迟 | **采纳为数据驱动项** | 现有指标只聚合单次模型调用，不能证明玩家端等待。首批增加端到端回合指标；没有数据前不合并 Router 与 Planner。 |
| 作者创作门槛过高、通用性只对当前题材成立 | **已完成首个零模型工具入口** | 新增显式单文件 Story 校验、脱敏稳定原因码、内容槽合同解释和三种无关题材 Catalog 夹具；不扫描正式 Story 目录、不修文件、不调用模型或网络。模拟 Patch 仍可在后续按同一工具边界扩展。 |
| 测试重工程契约、轻叙事质量 | **已完成首版评测底座与输出安全收口** | 阶段十建立带人工金标的路由、事实记忆、按钮一致、人格和汇流评测；机械项与人工项严格分开，人格、对白—按钮自然衔接和汇流自然度在人工审核前固定为 `human_review_pending`，不能用关键词或无模型 auto-pass 代替。阶段十三进一步拒绝报告覆盖 dataset/observations，并以原子写入和稳定脱敏错误保护评测输入。 |
| 背景介绍与生成约束混在 `scenario_card.brief / rules`，框架又补造 Choice、结局和作者对白 | **采纳并完成作者真源收口** | 阶段十四把 `background` 定为唯一公开背景，选剧接口只下发初始 Scene 和结构化公开身份；Loader 拒绝重复 brief/rules、缺失 Choice 字段、坏阶段、非结局死路和隐式结局；开场与静态节点对白原样采用作者文本，静态 Choice 不再接受模型改写；Branch Fact 在提交门再次拒绝作者禁止假设和不可变事实冲突。 |
| 24 小时 Session 静默结束 | **已完成可恢复休眠基础** | 阶段十二把 24 小时无提交改为独立 `dormant`：不写 `ended_at`、不覆盖 `updated_at`、不改剧情或活动索引，成功新回合才原子唤醒；真正归档仍需历史列表与显式恢复入口，不能用清除 active 指针冒充。 |
| 应扩成任意世界模拟与任意结局 | **不采纳** | 与作者权威、双人演出和受约束临时支线的产品定义冲突。v2.6 改善表达自由与反馈，不把小剧场改成通用世界模拟器。 |

### 3.6 v2.6 首批实现范围与成功标准

首批只修改通用服务层、Prompt、开发文档和直接测试，不修改 Story 内容、前端、i18n、TTS 或 Electron 链路：

1. **端到端回合观测**：记录完整 `submit` 耗时、Session 锁等待、稳定场景和固定结果枚举；报告和通用 instrument 都不得包含玩家原话、Story、Prompt、模型全文或角色名。
2. **短期意图线程**：一次普通 idle 只让当前意图休眠，不增加证据；休眠超过固定上限、节点变化、明确替换或作者路径推进才清除。旧 Session 缺少 v2.6 辅助字段时按中性默认读取，不改 revision。
3. **Pending 二次确认**：复合输入创建的合法 Pending 仍不能单独激活支线；Router 返回的摘录必须能由服务端在本轮规范化玩家原话中逐字找到，否则不创建 Pending。玩家在目标节点下一轮明确确认同一语义后，这条已核验摘录与本轮原话构成两条服务端证据，可以当轮进入 Planner。
4. **技术降级隔离**：模型未配置、超时、坏格式 Repair 失败、演绎护栏拒绝或 Fact 合同整组拒绝时，可以公开无事实安全回应并增加 revision，但 `turns_used / nonprogress_turns` 保持不变，Branch Fact、Goal 和 Ending 也不改变。
5. **兼容与回归**：玩家真实闲聊或询问而没有新事实时仍属于叙事非推进回合，继续按作者预算计数；相同 `client_turn_id` 重试不得重复增加证据或任何预算，并在完整事务指标中单列为幂等回放，不能混入正常成功样本。

## 4. 核心名词

| 名词 | 通俗解释 |
|---|---|
| Story Package | 作者写好的故事 JSON，包含背景、场景、节点、边、选项、道具、线索和结局 |
| Scene | 玩家可见的剧情阶段卡；一个 Scene 可以覆盖多个叙事节点 |
| Node | 一个已经发生后可以提交状态的作者剧情节点 |
| Edge | 从当前节点到目标节点的可达关系 |
| Choice | 玩家可见的作者推荐行动或对白，使用稳定 `choice_id` |
| Author Latent Edge | 作者预先写好、但不显示按钮的隐藏语义边 |
| Narrative Goal | 一个剧情段落必须完成的功能，例如“双方完成礼物交换”，不把具体实现写死为某件物品 |
| Free Intent | 玩家没有完成当前 Choice，但持续表达的合理行动目的，例如“我就想送黑色墨水” |
| Pending Intent | 状态已经推进或活动支线已经显式回锚后，仍待在目标节点重新确认的短期剩余行动意图；它本身不拥有支线激活权限 |
| World Contract | 作者声明的动态创作边界，规定允许新增什么、禁止改变什么、可以去往哪些结果 |
| Runtime Branch Patch | 大模型提出、服务端校验后保存在当前 Session 内的临时支线方案 |
| Branch Fact | 临时支线中已经公开发生并经服务端确认的事实 |
| Branch History | 已结束支线的结构化索引，只引用关键事实、完成目标和退出方式，不替代权威事实 |
| Convergence | 支线完成某个 Narrative Goal 后，带着真实结果重新接回作者主线 |
| Ending Domain | 作者允许动态支线抵达的结局类型范围，不是模型任意发明结局 |

## 5. v2.4 历史运行基线

本节记录 v2.5 开发开始时的 **[现有]** 能力，用于解释哪些底座被保留；v2.5 当时新增的职责以第 7、8、14 节为准，v2.6 增量以第 3.5、3.6 节以及阶段七至阶段十二为准。

### 5.1 总体结构

```mermaid
flowchart TD
    UI["小剧场页面 theater.html / theater.js"] --> API["theater_router.py HTTP 入口"]
    API --> RUNTIME["runtime.py Session 生命周期"]
    RUNTIME --> TURN["turn_service.py 单回合编排"]
    TURN --> STORE["session_store.py 原子存档"]
    TURN --> STORY["story_loader.py Story 加载与校验"]
    TURN --> GRAPH["story_graph.py 静态图查询"]
    TURN --> RULES["rules.py 权威状态规则"]
    TURN --> LLM["llm.py 路由模型 + 演绎模型"]
    TURN --> PROJECTOR["projector.py 公开结果投影"]
    STORY --> JSON["config/theater/stories/*.json"]
    LLM --> PROMPT["prompts_theater.py"]
    PROJECTOR --> UI
    API --> TTS["现有 Project TTS"]
    TTS --> VOICE["当前猫娘音色"]
```

### 5.2 当前模块职责

| 模块 | 当前职责 |
|---|---|
| `runtime.py` | 列出故事、创建和恢复 Session、转交输入、认领已提交对白、结束与闲置休眠 |
| `turn_service.py` | 校验输入，在 Session 候选副本上完成路由、演绎、状态提交、幂等和 revision 控制 |
| `session_store.py` | Session 原子读写、活动 Session 索引、同 Session/同角色锁、旧版本隔离 |
| `story_loader.py` | 加载 Story JSON，校验必填结构、节点、边、入口、可达结局、隐藏边元数据和 Goal 的可选事实投影 |
| `story_graph.py` | 查询当前节点、可见推荐边、作者隐藏边、Choice 和自然语言作者完成表达 |
| `fact_view.py` | 只读合并静态事实与已完成 Goal 的作者事实投影，稳定去重且不修改 Session |
| `rules.py` | 应用节点事实、道具、线索、flag、局部隐藏意图计数，并用统一 Fact View 判断静态门禁和确定性结局 |
| `llm.py` | 自由输入路由、猫娘演绎、结构解析、重复与越界修复、离线回退，并向各模型职责投影统一作者权威事实 |
| `projector.py` | 把私有 Session 投影为前端可见的 Scene、Board、Trace、Choice 和 Ending |
| `theater_router.py` | HTTP 接口、本地写入保护、当前猫娘校验，以及已提交对白到 TTS 的窄桥接 |

### 5.3 当前 HTTP 入口

| 接口 | 用途 |
|---|---|
| `GET /api/theater/stories` | 纯读取获取安全故事卡；不触发 24 小时扫描或任何 Session 生命周期写入 |
| `POST /api/theater/session/start` | 为当前猫娘创建新演出或替换旧活动演出 |
| `POST /api/theater/session/input` | 提交 Choice、自由输入或主动离场 |
| `GET /api/theater/session/state` | 读取指定 Session 已提交公开快照 |
| `GET /api/theater/session/active` | 恢复当前猫娘尚未结束的活动 Session |

### 5.4 当前 Story Package

当前故事由以下部分组成：

- `id / story_revision / title`：故事稳定身份、版本和公开标题；
- `background`：选择页唯一稳定背景介绍；正式发布 Story 必须写成单段电影式剧情简介，按 `len(background.strip())` 计算为 350–500 个 Unicode 字符，交代世界、双主角处境、触发事件、升级压力、双方方法冲突与未决悬念，不混入生成规则、系统语言或剧透结局；
- `summary / theme / world_seed`：作者内部摘要、主题和模型世界上下文，不进入选剧接口；
- `restrictions`：仅供服务端与模型执行的作者边界，不进入选择页；
- `runtime_guardrails`：禁止输出模式，以及在指定事实成立前持续生效的关系和动作硬边界；
- `seed`：玩家身份、开场事实和禁止假设；
- `scenario_card`：可选公开角色卡；存在时只包含玩家角色、猫娘角色和不剧透的公开目标，`brief / rules` 属于重复或越界字段，Loader 直接拒绝；
- `opening_dialogue`：作者可直接播放的正式开场对白，不经模型改写；
- `initial_scene_id / scenes`：服务端完整阶段集合；选剧接口只公开作者指定的 `initial_scene`，演出中只公开当前 Scene；
- `narrative_nodes / edges`：作者静态剧情图；
- `stage_props / clues`：道具和公开线索；
- `ending_attractors`：正式结局条件。

节点主要字段：

| 字段 | 当前用途 |
|---|---|
| `node_id` | 稳定节点身份 |
| `belong_phase` | 决定当前展示哪个 Scene |
| `node_type` | `seed`、`core`、`branch` 或 `ending` |
| `title / summary` | 作者定义的节点结果和离线旁白依据 |
| `preconditions` | 进入目标节点前需要或禁止的事实 |
| `runtime_generation_guide` | 演绎模型使用的作者意图和边界 |
| `scripted_dialogue` | 除 seed 外必填的正式作者节点对白；模型可读取它帮助生成旁白，但静态图推进时不能替换正文 |
| `script_action` | 使用道具、公开线索 |
| `state_diff` | 进入节点时提交的作者权威事实 |
| `suggestions` | 进入本节点的玩家行动或对白 |

当前图有一个容易误读、但必须理解的规则：

> 当前节点 A 的出边指向目标节点 B；玩家在 A 演出结束后看到的 Choice，来自 B 的 `suggestions`。选择后先执行该 Choice 的 callback，再演出并提交 B。

因此，B 的 Choice 中出现的人、地点、物品和动作，必须已经在 A 的公开演出中被介绍。

### 5.5 当前推荐边与作者隐藏边

`edges.visibility` 缺省为 `recommended`：

- `recommended`：生成玩家可见的推荐 Choice；
- `latent`：不显示按钮，只作为自由输入路由器的作者白名单候选。

作者隐藏边必须提前写好：

- `transition_id`；
- `goal_id`；
- `intent_id`；
- `intent_summary / intent_examples`；
- `pullbacks_before_transition`；
- 静态目标节点和 callback。

当前模型只能返回作者给出的 `intent_id`。没有对应隐藏边的话，无论玩家把同一合理要求说多少次，都不会形成新支线。

### 5.6 当前 Session 和权威状态

Session 顶层保存：

- `session_id / story_id / lanlan_name`；
- `schema_version / state_revision`；
- `phase / story_state`；
- 最近公开对话 `turns`；
- 最近幂等响应 `turn_results_by_client_id`；
- 仅供服务端单局复盘的私有模型返回 `llm_return_records`；
- `public_snapshot`；
- 开始、更新时间和结束时间；
- 已认领的对白朗读 revision。

`story_state` 当前包含：

| 字段 | 当前含义 |
|---|---|
| `current_node_id` | 当前已提交作者节点 |
| `completed_node_ids` | 已完成作者节点 |
| `narrative_facts` | 作者节点提交的结构化事实 |
| `available_prop_ids / used_prop_ids` | 当前可用和已使用道具 |
| `clue_ids / flags` | 已公开线索和作者标记 |
| `scene_notes` | 最近六条自由互动笔记，不是正式剧情事实 |
| `choice_label_overrides` | 仅为旧 Session 残留字段；当前运行时忽略并在下一次可保存回合清除，不再属于功能协议 |
| `active_goal_id / focused_intent_id` | 当前作者隐藏边的局部目标和意图 |
| `intent_streak / goal_pullback_count` | 作者隐藏意图的连续命中和停留次数 |
| `branch_commitment` | 已进入的作者隐藏边身份 |

权威状态原则：

1. 模型输出不是状态。
2. 只有服务端接受的 Choice、作者节点增量或规则提交才会改变正式事实。
3. `scene_notes` 可以帮助模型承接对话，但不能单独解锁节点、道具、线索或结局。
4. 前端只读取 `public_snapshot`，不能自行推断剧情已经发生。

### 5.7 当前输入和回合顺序

支持三种输入：

| `input_kind` | 行为 |
|---|---|
| `choice` | 使用当前可见 `choice_id` 推进静态图 |
| `free_input` | 尝试自然语言命中当前 Choice 或作者隐藏边；否则留在当前节点对话 |
| `user_exit` | 主动离场，不算作者结局 |

所有输入都携带 `client_turn_id`；同一 ID 只提交一次。`base_revision` 防止旧窗口覆盖更新后的剧情。

```mermaid
flowchart TD
    REQUEST["输入请求"] --> GUARD["Session 锁 / 幂等 / revision"]
    GUARD --> COPY["深拷贝 Session 候选"]
    COPY --> KIND{"输入类型"}
    KIND -->|"Choice"| CHOICE["解析当前稳定 Choice"]
    KIND -->|"自由输入"| ROUTE["路由模型读取公开上下文和白名单边"]
    KIND -->|"离场"| EXIT["结束 Session"]
    ROUTE --> MATCH{"命中什么？"}
    MATCH -->|"可见 Choice"| APPLY["服务端提交作者目标节点"]
    MATCH -->|"作者隐藏意图"| COUNT["更新作者意图计数"]
    MATCH -->|"都没有"| STAY["保持节点并记录 scene_note"]
    COUNT --> LATENT{"超过作者阈值？"}
    LATENT -->|"否"| STAY
    LATENT -->|"是"| APPLY_LATENT["提交作者静态支线节点"]
    CHOICE --> APPLY
    APPLY --> RENDER["按提交后状态演绎"]
    APPLY_LATENT --> RENDER
    STAY --> RENDER
    RENDER --> VALIDATE["结构、重复、边界校验"]
    VALIDATE --> PROJECT["公开投影"]
    PROJECT --> SAVE["revision + 1 原子保存"]
```

### 5.8 当前两阶段模型协议

自由输入通常经历两个职责隔离的模型阶段：

1. **路由阶段**：读取故事背景、当前 Scene、公开事实、最近对话、玩家本轮原话、当前 Choice 和作者隐藏候选；v2.4 基线返回 `matched_choice_id` 与 `observed_intent_id`，v2.5 已把后者更名为 `authored_intent_id`，旧名仅在服务端读取侧兼容过渡数据。
2. **演绎阶段**：读取服务端已经确定的状态；角色互动与动态支线可生成受约束的 `narration / dialogue`，静态图推进只采纳模型旁白并原样使用作者 `scripted_dialogue`。兼容字段 `choice_rewrites` 必须为空且不会取得按钮权限。

路由模型不能写台词，演绎模型不能提交节点。这样可以避免猫娘说“腕带已经戴好”，但后台仍停在“是否戴腕带”的旧状态。

模型失败时：

- 路由失败：保守留在当前节点，不猜测推进；
- 演绎失败：静态图使用作者 summary 与 scripted dialogue；角色互动或动态支线只使用不提交事实的技术降级；
- 未知 ID、坏 JSON 或越界输出：丢弃对应输出，不写权威事实。

### 5.9 当前前端、恢复与 TTS

前端负责故事选择、背景卡、对话日志、旁白、Board、行动/对白 Choice、自由输入、Loading、落幕和恢复；不掌握私有事实与路由规则。

成功回合先原子保存，再把公开 `dialogue.text` 交给现有 Project TTS。`session/state`、`session/active` 和 revision 冲突恢复只读取快照，不重复朗读。TTS 不可用时，文字剧情照常完成。

## 6. v2.4 已确认的能力缺口

本节解释为什么需要 v2.5。

以《约会清单最后一项》的杂货铺为例：玩家连续输入“挑一瓶墨水”“黑色的墨水”“我就要给墨水”。演绎模型可以理解墨水，也可以让猫娘自然回应，并把推荐文案改成包含墨水；但当前节点没有作者预设的“墨水”隐藏边，路由模型只能返回空 ID。

最终结果是：

1. 每轮都被当作普通角色互动；
2. `scene_notes` 记录了墨水，但没有权威剧情含义；
3. 作者隐藏意图计数始终为零；
4. 系统不能创建围绕墨水的支线；
5. 原静态 Choice 仍要求玩家选择星铃或木笔，产生“猫娘接受了墨水，但系统不承认”的割裂。

所以，缺口不是“模型没有足够上下文”，而是当前协议只允许模型从作者白名单边中选择，没有“识别通用自由意图—生成支线—提交支线事实—完成主线目标”的链路。

## 7. v2.5 总体架构

v2.5 保留 v2.4 的静态主线、Session、幂等、revision、公开投影和 TTS，在 Turn Service 与模型之间增加受约束的动态叙事能力。

```mermaid
flowchart TD
    INPUT["玩家输入"] --> ROUTER["统一语义路由"]
    ROUTER --> STATIC{"自然语言完成作者项，或无活动支线点击作者 Choice？"}
    STATIC -->|"是"| STATIC_APPLY["提交作者静态节点"]
    STATIC -->|"否"| BRANCHING{"当前已在临时支线？"}
    BRANCHING -->|"是"| HANDOFF{"明确结束旧支线并提出具体新行动？"}
    HANDOFF -->|"明确 continue"| BRANCH_TURN["临时支线回合"]
    HANDOFF -->|"是，且双摘录与高置信通过"| CLOSE_HANDOFF["intent_handoff：保留事实并回锚"]
    HANDOFF -->|"uncertain 或技术失败"| HANDOFF_SAFE["无事实、无预算技术降级"]
    CLOSE_HANDOFF --> PENDING_HANDOFF["新行动保存为 Pending 第一证据"]
    PENDING_HANDOFF --> HANDOFF_ACK["固定安全回应；不演出新行动"]
    HANDOFF_SAFE --> RENDER
    BRANCHING -->|"否"| AUTHORED{"命中作者隐藏边？"}
    AUTHORED -->|"是"| AUTHOR_LATENT["沿用 v2.4 作者支线"]
    AUTHORED -->|"否"| TRACK["通用自由意图追踪"]
    TRACK --> THRESHOLD{"同一意图连续达到 2 次？"}
    THRESHOLD -->|"否"| ROLEPLAY["自然回应，保留主线建议"]
    THRESHOLD -->|"是"| PLAN["生成 Runtime Branch Patch"]
    PLAN --> CONTRACT["World Contract + 本地规则校验"]
    CONTRACT -->|"失败"| ROLEPLAY
    CONTRACT -->|"通过"| ENTRY["候选支线入口 Actor / 必要时 Repair"]
    ENTRY -->|"失败"| ROLEPLAY
    ENTRY -->|"通过"| ACTIVATE["入口演出与临时支线原子保存"]
    ACTIVATE --> RENDER
    BRANCH_TURN --> OUTCOME{"支线结果"}
    OUTCOME -->|"继续探索"| SAVE_BRANCH["保存支线进度与公开事实"]
    OUTCOME -->|"满足 Narrative Goal"| CONVERGE["携带支线事实汇回主线"]
    OUTCOME -->|"满足允许结局"| ENDING["提交允许的支线结局"]
    STATIC_APPLY --> RENDER["猫娘人格演绎"]
    AUTHOR_LATENT --> RENDER
    ROLEPLAY --> RENDER
    SAVE_BRANCH --> RENDER
    CONVERGE --> RENDER
    ENDING --> RENDER
    RENDER --> PROJECT["公开投影 / 原子保存 / TTS"]
```

## 8. v2.5 新增能力

### 8.1 [已实现] 通用自由意图追踪

当前 `focused_intent_id` 只能记录作者提前声明的意图。v2.5 增加与作者隐藏边分离的通用自由意图：

| 字段 | 含义 |
|---|---|
| `intent_key` | 服务端生成的当前 Session 内稳定意图身份，不由玩家看见 |
| `intent_summary` | 只陈述玩家想实施的公开行动，例如“挑选黑色墨水送给猫娘” |
| `origin_node_id` | 第一次出现该意图时所在的作者节点 |
| `streak` | 连续坚持同一意图的次数 |
| `evidence_messages` | 支撑该判断的最近玩家原话，数量有上限 |
| `relation` | 本轮是继续、细化、替代还是切换意图 |

同义、口语、错别字和细化表达应当合并：

- “挑一瓶墨水”；
- “黑色的”；
- “就送这个，不要配笔”；
- “我还是想把墨水给她”。

这些可以被识别为同一意图。若玩家改成“先去买伞”，则切换意图并重新计数。

路由模型建议只返回语义判断，不拥有状态 ID：

```json
{
  "route_kind": "free_intent",
  "matched_choice_id": "",
  "authored_intent_id": "",
  "free_intent": {
    "summary": "挑选黑色墨水送给猫娘",
    "relation": "refine",
    "confidence": 0.93
  }
}
```

其中 `relation` 只允许 `new / continue / refine / replace`。服务端依据当前 Session 中的意图和本轮证据决定新建、延续或重置 `intent_key`，并负责累计 `streak`；模型不能通过自报 ID 或次数触发支线。

复合输入可以额外返回一个不拥有状态权限的剩余意图。例如：

```json
{
  "route_kind": "authored_choice",
  "matched_choice_id": "choice_wear_pair_wristband",
  "residual_intent": {
    "summary": "进入下一场景后去买伞",
    "evidence_excerpt": "然后先去买伞"
  }
}
```

`residual_intent` 只有在当前 Choice 已经明确完成、后半句又能与当前动作清楚分离时才允许存在。服务端提交 Choice 后，把它保存为带创建 revision、来源节点和短期有效范围的 `pending_intent`；进入目标节点后重新校验，不能在旧节点提前累计 `streak`，也不能因为模型返回了摘要就直接创建支线。

首版 `pending_intent` 固定保存 `summary / evidence_excerpt / source_node_id / target_node_id / target_scene_id / created_revision / expires_revision`。`evidence_excerpt` 必须能由服务端在规范化后的本轮玩家原话中逐字找到，模型改写或虚构的摘录会使整个 residual 被丢弃。`expires_revision = created_revision + 1`：它可以在目标节点当前提交或紧邻的下一 revision 重新交给 Router 校验，之后直接过期。该结构不包含 `intent_key / streak / active_runtime_branch`，因此自身永远不能触发支线；v2.6 只有在下一轮 Router 明确返回同一语义的 `continue/refine` 时，才把已核验摘录作为上一条玩家证据，与本轮原话共同建立服务端意图线程并当轮进入 Planner。

意图生命周期必须使用明确规则，不使用隐藏的强度加权、冷却池或模糊衰减：

1. `continue / refine` 且仍在同一作者节点时才增加 `streak`；active 或 dormant 线程都保留原服务端身份，但 dormant 必须先由本轮明确承接才能恢复为 active；
2. `new / replace` 重建当前通用意图；第一次普通 idle 只休眠且不增加证据，连续第二次 idle、换节点或坏状态才清理；
3. 作者节点推进时默认清除原节点意图，只有合法 `pending_intent` 可以带到目标节点重新判断；
4. `pending_intent` 过期、与新 Scene 不兼容、玩家明确否定或 revision 已落后时直接丢弃；
5. 模型置信度只用于拒绝低可信判断，不能让单句跳过“连续两次”的产品规则；
6. 相同 `client_turn_id` 的重试不能重复增加 streak、重复创建 pending intent 或延长有效期。

`scene_notes`、通用意图和正式事实的职责保持分开：`scene_notes` 是供演绎承接的非权威近期互动摘要；`dynamic_intent.evidence_messages` 是只供路由和规划使用的短期玩家证据；`branch_facts` 才是已经公开发生并通过校验的权威事实。

默认阈值：

1. 第一次合理图外输入：猫娘自然回应，保留或上下文化改写主线建议；
2. 第二次连续坚持同一意图：尝试生成并进入临时支线；
3. 无需让猫娘说“再偏离一次就进入支线”，内部计数永不公开。

### 8.2 [已实现] World Contract

World Contract 是作者为动态支线声明的边界，不是猫娘台词，也不是前端规则说明。

允许动态支线的 Story Package 使用以下结构：

```json
{
  "world_contract": {
    "speaking_roles": ["player", "active_catgirl"],
    "immutable_facts": [],
    "allowed_dynamic_fact_types": [
      "observable_action",
      "ordinary_local_prop",
      "spoken_preference",
      "reciprocal_relationship_step"
    ],
    "dynamic_content_slots": [
      {
        "slot_id": "slot_local_stationery_gift",
        "allowed_fact_type": "ordinary_local_prop",
        "allowed_traits": ["stationery", "locally_available"],
        "forbidden_traits": ["relationship_commitment_symbol"]
      }
    ],
    "forbidden_changes": [
      "player_identity",
      "catgirl_identity",
      "story_genre",
      "unrevealed_secret",
      "unearned_relationship_status"
    ],
    "branch_turn_budget": {"default": 4, "max": 6, "max_nonprogress_turns": 2},
    "branch_abort_policy": {
      "mode": "return_to_anchor",
      "neutral_callback": "两人暂时停下这次尝试，重新看向眼前仍待决定的事情。"
    },
    "allowed_ending_domains": [],
    "convergence_goal_ids": []
  }
}
```

上面的槽位结构仍是 v2.5 兼容形态：`allowed_traits` 按三份现有 Story 的真实用法表示“目录成员必须同时具备的正向特征”，`forbidden_traits` 表示不得命中的特征。它们本身只是作者声明，不能由模型自报或物件名称关键词证明。需要确定性授权的槽位可选增加作者 Catalog：

```json
{
  "slot_id": "slot_local_stationery_gift",
  "allowed_fact_type": "ordinary_local_prop",
  "allowed_traits": ["stationery", "locally_available"],
  "forbidden_traits": ["relationship_commitment_symbol"],
  "catalog_items": [
    {
      "content_id": "content_black_ink",
      "entity_kind": "prop",
      "label": "黑色墨水",
      "fact_object": "black_ink",
      "traits": ["stationery", "locally_available", "ordinary_gift"]
    }
  ]
}
```

`catalog_items` 每项必须且只能包含 `content_id / entity_kind / label / fact_object / traits`；同槽 `content_id` 唯一，`entity_kind` 只允许现有 Board 支持的 `prop / clue`。`content_id / fact_object` 必须是含下划线的稳定小写引用且不超过 `64` 字符，不能用自然语言冒充内部身份；公开 `label` 与运行时 Board 共用短自然语言校验，不能包含内部字段或机器引用。每槽最多 `16` 个成员、单 Story 最多 `32` 个 Catalog 成员；成员与严格槽的正向/禁止 traits 均限制数量和单项长度，全部严格槽投影到 Planner 的 JSON 总计不得超过 `12000` 字符。Loader 要求全部 `allowed_traits` 都出现在成员 traits 中，并拒绝任何 `forbidden_traits` 交集。运行时不解释 `stationery`、`repair_component` 等词义，只信作者已经校验通过的 `(slot_id, content_id)` 成员关系，也不会静默截断超限权威 ID。

这些字段已经进入 v2.5 Story 协议，并由 Loader 严格校验。World Contract 至少回答：

1. 谁可以说话；
2. 哪些事实永远不能被动态修改；
3. 当前世界允许临时出现哪些普通物件与动作；
4. 哪些关系变化需要什么公开证据；
5. 支线最长可以演几回合；
6. 可以汇入哪些 Narrative Goal；
7. 可以抵达哪些结局类型；
8. 预算耗尽、玩家转回作者 Choice 或连续不推进时如何安全退出；
9. 自由生成的物件和动作必须绑定哪个作者声明的动态内容槽位。

只声明 `ordinary_local_prop` 这类宽泛类型不足以完成可验证校验。Planner 提出的自由物件、地点或关系动作必须绑定当前 Story 中稳定的 `dynamic_content_slots.slot_id`。对于带 Catalog 的严格槽位，事实模板还必须绑定该槽现有 `content_id`，Actor 的事实对象和公开实体种类/标签必须精确匹配作者成员，提交与 Session 恢复会结合当前 Story 再次查表；活动支线恢复还会把已有事实重新对照原 Patch，不能整体改绑到同槽另一成员。已结束 History 没有保存完整 Patch，只能重验事实仍属于当前 Story 作者目录，不能把这一边界表述成本地文件防篡改签名。服务端不能把模型自报的“这个物件很普通”或名称关键词当作证明。无 Catalog 的旧槽位继续按 v2.5 行为运行，以免静默关闭既有用户 Story，但其 traits 只能称为 `declarative_only`，不能称为可执行语义证明。作者可以不声明任何槽位，此时该 Story 仍可使用静态主线和作者隐藏边，只是不允许凭空新增对应动态内容。允许动态支线的 Story 必须提供非空 `branch_abort_policy.neutral_callback`，否则 Loader 拒绝加载相关配置。

边界检查分为三层，不能都塞进 World Contract：

1. **产品安全策略**：在普通路由和 Patch 规划前处理产品级非法或高风险请求；
2. **World Contract**：判断内容是否符合当前故事、人物、地点、关系和动态创作范围；
3. **演绎输出护栏**：检查旁白、对白和动态支线 Choice/Board 公开文案是否出现第三人发言、读心、内部规则、未获同意的关系动作或未公开事实；静态 Choice 不进入模型改写链。

被拒绝的输入可以得到符合当前人格的安全回应，但拒绝本身不能创建 Branch Fact、增加 streak、改变 Narrative Goal 或伪装成作者结局。

### 8.3 [已实现] Runtime Branch Patch

当同一合理自由意图第二次出现时，支线规划模型只生成结构化候选方案，不直接写 Session，也不直接生成正式结局。

当前候选结构：

```json
{
  "origin_node_id": "node_enter_festival",
  "seed_intent": "挑选黑色墨水送给猫娘",
  "objective": "完成一段围绕墨水选择与交换的双人互动",
  "entry_callback": "玩家仍然把黑色墨水留在手中，没有改选其他礼物。",
  "turn_budget": 4,
  "content_slot_ids": ["slot_local_stationery_gift"],
  "allowed_new_facts": [
    {
      "fact_type": "ordinary_local_prop",
      "fact_role": "player_selected_gift",
      "content_slot_id": "slot_local_stationery_gift",
      "content_id": "content_black_ink"
    },
    {
      "fact_type": "observable_action",
      "fact_role": "catgirl_accepted_gift",
      "content_slot_id": ""
    },
    {
      "fact_type": "observable_action",
      "fact_role": "gift_exchange_publicly_completed",
      "content_slot_id": ""
    }
  ],
  "forbidden_assumptions": [
    {
      "subject": "outside_character",
      "predicate": "speaks_directly",
      "object": "scene_dialogue"
    }
  ],
  "beat_outline": [
    {
      "beat_id": "beat_confirm_ink",
      "objective": "确认玩家仍选择黑色墨水",
      "observable_action": "黑色墨水仍在玩家手中",
      "player_choice_label": "拿起选中的墨水，确认就要这一瓶",
      "exit_preparation": ["player_selected_gift"]
    },
    {
      "beat_id": "beat_exchange_gift",
      "objective": "公开完成礼物交换",
      "observable_action": "猫娘收下礼物，双方结束挑选",
      "player_choice_label": "把选好的墨水递给她，说明这是礼物",
      "exit_preparation": [
        "catgirl_accepted_gift",
        "gift_exchange_publicly_completed"
      ]
    }
  ],
  "exit_candidates": [
    {"kind": "converge", "goal_id": "goal_exchange_personal_gift"},
    {"kind": "ending_domain", "ending_domain_id": "ending_domain_gentle_pause"}
  ]
}
```

模型只提出 Patch 内容。`branch_id`、创建 revision、当前猫娘和实际起点由服务端在校验通过后写入，模型不能指定或覆盖这些受保护字段。`entry_callback` 当前仅作为旧 Patch 结构兼容字段接受合同校验，不进入入口 Actor Prompt、确定性回退或公开演出；入口公开结果只允许使用当前 Scene 与合同已验证的行动方向。

`allowed_new_facts` 只声明当前 Patch 可以产生的事实模板，基础字段固定为 `fact_type / fact_role / content_slot_id`；它不能直接写事实三元组或服务端 ID。引用 Catalog 严格槽位时，该模板必须再携带本槽现有 `content_id`；旧声明式槽位和无槽事实不得自报该字段。新生成的 `beat_outline` 每一项固定使用 `beat_id / objective / observable_action / player_choice_label / exit_preparation`，必须至少有一个可执行节拍，且只能为本 Patch 已授权的事实角色做出口准备。`observable_action` 是 Actor 使用的内部完整舞台编排；`player_choice_label` 是唯一允许公开成按钮的简短玩家行动，不能替玩家决定猫娘的反应、双方结果或后续剧情。为兼容修复前已经保存的活动 Patch，恢复校验允许缺少 `player_choice_label`，但这种旧 Beat 不会再投影动态按钮，也绝不回退公开 `observable_action`。

Patch 只存在于当前 Session，不写回 `config/theater/stories/*.json`。相同 Story 的其他玩家、其他猫娘和新 Session 不会继承它。

服务端必须验证：

1. 起点仍是当前节点，不能使用过期上下文；
2. 意图有最近玩家原话作为证据；
3. 新物件、行动和地点符合 World Contract；
4. 不新增第三位直接发言者；
5. 不暴露作者秘密或玩家内心；
6. 至少存在一个合法出口；
7. 回合预算在作者范围内；
8. Catalog 严格槽位的事实模板必须绑定本槽现有 `content_id`，不能跨槽冒用；
9. 目标和结局只能引用当前 Story 声明的稳定 ID。

校验失败时，系统继续在当前场景自然回应，不把失败原因告诉角色或玩家，也不写任何支线事实。

`beat_outline` 只描述这条短支线的目标、内部可观察行动、玩家当前可实施的单一行动和出口准备，不是固定节拍表，也不强制每条功能性支线制造告白、危机或情绪高潮。Patch 必须有因果可执行的下一步，但不能依赖模型即兴补齐缺失的动机、线索、同意或收束条件。合同会拒绝以“双方、两人、彼此、猫娘”等舞台主语开头，或在后续分句规定其他角色反应的 `player_choice_label`；该规则只约束通用行动归属，不匹配任何具体剧本人物、物件或情节。

服务端只能接受完整合法的 Patch，不能把未知 Goal、坏出口或越界内容自动改写成“最接近”的作者目标。避免死锁的责任由激活前的出口校验和作者 `branch_abort_policy` 共同承担，不由运行时猜测汇流方向。

### 8.4 [已实现] Narrative Goal 与目标式汇流

当前静态图常把剧情功能和具体执行方式写死。例如“完成礼物交换”被写成“选择星铃或木笔”。这会让墨水支线即使演得自然，也无法被主线承认。

v2.5 已把关键段落抽象为作者目标：

```json
{
  "narrative_goal": {
    "goal_id": "goal_exchange_personal_gift",
    "summary": "玩家与猫娘各自选择并交换一件具有个人含义的礼物",
    "completion_evidence": [
      "player_selected_gift",
      "catgirl_accepted_gift",
      "gift_exchange_publicly_completed"
    ],
    "converge_to_node_id": "node_share_dessert",
    "convergence_fact_roles": [
      "player_selected_gift",
      "catgirl_accepted_gift"
    ],
    "fallback_convergence_callback": "双方已经完成这次礼物交换，带着实际选中的礼物离开货架。"
  }
}
```

作者的星铃和木笔 Choice 是完成该目标的稳定推荐路径；经验证的墨水支线也可以提供相同完成证据。

静态路径不得通过节点名、物件名或 `state_diff` 文案猜测 Goal。作者在真正提交该剧情功能的节点显式声明：

```json
{
  "node_id": "node_authored_goal_result",
  "completes_goal_ids": ["goal_exchange_personal_gift"]
}
```

如果某条推荐或隐藏入口只在 Goal 未完成时有效，作者可以在 edge 上绑定同一个稳定 `goal_id`。Story Graph 会同时过滤已完成 Goal 的绑定边，以及任何试图再次完成该 Goal 的目标节点；`suggestion_options / resolve_choice / resolve_authored_completion / latent_transition_options` 共用这一个过滤结果，因此旧按钮、自然语言完成表达、隐藏语义入口和刷新快照不会分叉。推荐边的 Goal 引用和节点 `completes_goal_ids` 由 Loader 对照 Narrative Goal 白名单校验；v2.4 已存在且不属于 Narrative Goal 的 latent `goal_id` 仍只保留局部路由语义，只有其值真实进入 `completed_goal_ids` 时才会失效。

一旦目标完成：

1. 服务端提交实际发生的礼物事实；
2. 将该 Narrative Goal 标记为完成；
3. 跳过仍要求重新挑礼物的过时 Choice；
4. 从与当前 Goal 关联的 Branch Fact 中选择实际完成方式，生成一段只承接公开结果的汇流 callback；
5. 从作者声明的 `converge_to_node_id` 继续主线。

汇流不是把玩家强行送回旧问题，而是让不同做法完成同一个剧情功能。

`completed_goal_ids` 只记录“剧情功能已经完成”，具体通过墨水、星铃还是木笔完成应继续保存在 Branch Fact 或作者事实中。不要为每种自由物件不断增加 `gift_type` 一类专用状态字段；汇流演绎使用 `convergence_fact_roles` 选出相关事实即可。模型汇流失败时使用作者 `fallback_convergence_callback`，但 callback 只能确认已经公开发生的共同结果，不能补写具体礼物、动作或关系变化。

当前实现中，静态节点提交和动态支线汇流都只追加同一个 `completed_goal_ids` 集合。Planner Prompt 会收到已完成作者 Goal ID，但服务端仍会独立拒绝任何再次以该 Goal 为 `converge` 出口的 Patch；恢复中的旧活动 Patch 也走同一校验并按 `restore_invalid` 安全关闭。Ending Domain 仍可把已完成 Goal 当成作者声明的前置证据，不会被误当成重复汇流。内置兼容性夹具因新增静态 Goal 完成语义将 `story_revision` 从 `date-list-last-item-v2.5.0` 升为 `date-list-last-item-v2.5.1`，旧 revision Session 按既有门禁保留原文件并返回不兼容结果，不解释到新图。

### 8.4.1 [已实现] 统一只读 Fact View 与作者事实投影

静态作者节点把结果写入 `narrative_facts`，动态支线则以带 Branch 身份的事实证明 Narrative Goal。两者不能直接合并存储：Branch Fact 的 `subject / predicate / object` 来自模型候选，只做公开性与合同白名单校验，不足以直接解锁作者静态规则。

v2.6 因此新增只读 `Fact View`，其来源固定为：

```text
narrative_facts
+ 已完成 completed_goal_ids 对应的作者 completion_fact_projections
```

作者可以在 Narrative Goal 上可选声明永久、单调成立的投影：

```json
{
  "goal_id": "goal_restore_shared_system",
  "completion_fact_projections": [
    {
      "subject": "shared_system",
      "predicate": "repair_state",
      "object": "restored"
    }
  ]
}
```

Loader 要求每项恰好包含非空的 `subject / predicate / object`，并拒绝同一 Goal 内的重复投影和附加 Branch 身份字段。字段缺失时等同空列表，因此旧 Story 与旧 Session 不需要迁移；若作者为正式 Story 新增投影并改变可达条件或演绎语义，应提升 `story_revision`。

Fact View 按静态事实、Goal 作者顺序和投影顺序稳定去重，返回副本且不写回 `story_state`。当前消费者只有：

1. 静态节点 `preconditions.required_facts / forbidden_facts`；
2. 传统 `ending_attractors` 事实条件；
3. `runtime_guardrails.conditional_output_guards` 的阶段解除条件；
4. Router、Planner、普通 Actor、支线入口 Actor 与活动 Branch Actor 共用的公开已确认事实。

原始 `branch_facts` 不进入该视图；当前支线的 Goal/Ending Domain 证据、动态 Choice、Branch Actor 事实合同、History、Board 和恢复检查继续读取带 `branch_id / fact_role / fact_type / source_revision` 的作用域事实。这样实现的是“统一读取、保持分域真源”，不是把两套事实对象扁平合并，也不会把服务端身份泄露给前端或模型。

### 8.5 [已实现] Branch Fact

临时支线事实分为三层：

| 层级 | 示例 | 是否权威 |
|---|---|---|
| 对话上下文 | “玩家正在考虑黑色墨水” | 否，只帮助当前演绎 |
| 候选事实 | “玩家准备购买黑色墨水” | 否，尚未公开实施 |
| 已提交 Branch Fact | “玩家选择黑色墨水；猫娘已经收下” | 是，后续必须承认 |

只有可观察、已发生、通过 World Contract 校验的结果才能提交。禁止把“玩家很紧张”“猫娘已经爱上玩家”“墨水代表永恒”等模型解释写成事实。

Branch Fact 至少需要保留稳定身份、来源与公开证据：

```json
{
  "fact_id": "branch_fact_server_generated",
  "branch_id": "branch_server_generated",
  "goal_id": "goal_exchange_personal_gift",
  "fact_type": "ordinary_local_prop",
  "fact_role": "player_selected_gift",
  "subject": "player",
  "predicate": "selected_gift",
  "object": "black_ink",
  "content_slot_id": "slot_local_stationery_gift",
  "content_id": "content_black_ink",
  "source_revision": 12,
  "public_entity": {
    "entity_id": "branch_entity_server_generated",
    "kind": "prop",
    "label": "黑色墨水",
    "status": "used"
  }
}
```

模型先提出不含 `fact_id / branch_id / source_revision / public_entity.entity_id` 的 Branch Fact Candidate；服务端确认本轮结果已经公开发生、且命中活动 Patch 的事实模板后，才在原子提交边界补齐这些权威字段。Catalog 严格槽位的候选必须原样携带 Patch 已绑定的 `content_id`，`object` 必须等于作者 `fact_object`，并必须提供与作者 `entity_kind / label` 精确一致的 `public_entity`；任一候选篡改都会原子拒绝整组事实。提交后的 `content_id` 保留在私有 Branch Fact 中，Session 恢复会结合当前 Story 再次查表，但 Projector、Board 和完成 History 召回都不公开该内部 ID。旧无 Catalog 槽位继续允许没有 `content_id` 的事实结构，只能视为兼容行为。

`fact_role` 必须来自当前 Goal 或 Ending Domain 预先声明的稳定语义角色，用于确定性校验完成证据；模型不能临时创造新角色名来证明 Goal 或结局成立。普通公开动作和对白事实可以没有实体投影；上述“目录事实必须有实体”只作用于带 Catalog 的内容槽。

`public_entity.status` 是框架枚举而不是自由文案：`kind=prop` 时只允许 `available / selected / used`，其中 `available / selected` 投影到现有可用道具区，`used` 投影到已使用区；`kind=clue` 时只允许 `discovered` 并投影到已发现线索区。Projector 只接受同时具备服务端 `fact_id / branch_id / source_revision / entity_id` 的已提交实体，输出时仅保留现有 Board 所需的 `id / label / public_hint` 或 `id / title / public_text`，不得复制原始 Branch Fact、Goal、revision 或私有字段。同一 `entity_id` 出现多条合法记录时采用最高 revision 的最后状态，使恢复和迁移投影保持确定性。

Session 权威状态已经增加：

| 字段 | 用途 |
|---|---|
| `dynamic_intent` | 当前通用自由意图的服务端身份、有限玩家证据和 `active/dormant` 短期线程状态；旧存档缺少线程状态时按 active 懒归一化 |
| `pending_intent` | 复合输入提交当前 Choice 后，或 `intent_handoff` 关闭旧支线并回锚后，等待在目标节点下一轮重新校验的短期剩余意图 |
| `active_runtime_branch` | 当前临时支线 Patch、进度、预算和出口 |
| `branch_facts` | 已提交的动态公开事实 |
| `completed_goal_ids` | 已由静态路径或动态支线完成的 Narrative Goal |
| `branch_history` | 已结束支线的结构化索引，不保存无限原始生成内容，也不复制权威事实全文 |
| `return_anchor` | 支线结束后允许汇回的作者节点与目标 |

这些字段属于服务端权威状态。前端只接收其中适合公开展示的物品、线索、行动结果和当前 Choice。

每条 `branch_history` 至少记录 `branch_id`、`completed_goal_ids`、`key_fact_ids`、`exit_kind` 和 `ended_revision`。可选自然语言 recap 只帮助调试或演绎，不参与节点、Goal、Ending 或关系判断。演绎模型需要回忆支线细节时，应以 `key_fact_ids` 精确取回已提交 Branch Fact，不能把所谓“最有情感价值”的模型判断直接升级成权威状态。当前首版按最近四条合法 History 选择、总计最多八条关键事实，并对 Goal 作者摘要和事实语义字段逐项施加 24 token 上限；只向普通 Actor 投影 `subject / predicate / object`、可选公开实体语义和已完成 Goal 的作者摘要，不下发 `fact_id / branch_id / source_revision / entity_id / fact_role`。结构损坏、跨支线或不存在的引用整条忽略，绝不按相似文本改绑。后续 History 规模确实超过首版预算时，再基于玩家本轮提及实体和当前 Goal 增加确定性相关性选择。

v2.5 首版正常运行时 `exit_kind` 固定为 `goal_converged / ending_domain / author_choice / budget_exhausted / nonprogress_exhausted / user_exit`；恢复校验另有服务端专用的 `restore_invalid`，只表示活动状态损坏但仍具有可信 `branch_id` 与作者 `return_anchor`，因此被安全关闭。v2.6 阶段九增加服务端固定退出码 `intent_handoff`，只表示玩家明确结束旧支线并把具体新行动交给锚点后的 Pending 重验。新增退出原因必须先扩展服务端状态机和测试，不能把任意模型文本直接保存为退出类型。

### 8.6 [已实现] 临时支线内的自由演绎

进入临时支线后，按钮只显示当前 Patch 已校验的玩家行动建议，作用是提供灵感，不是推进门禁。作者静态 Choice 暂时隐藏，避免已经被玩家自由选择绕开的主线物件或动作与当前支线继续竞争；支线结束并回到作者图后再按当前节点和 Goal 恢复投影。

每个支线回合需要判断：

1. 玩家是在继续当前支线意图；
2. 玩家是否公开实施了一个可提交动作；
3. 是否产生新的候选或正式 Branch Fact；
4. 是否已经满足 Narrative Goal；
5. 是否达到作者允许的 Ending Domain；
6. 是否需要继续、汇流或结束。

支线内动态 Choice 由服务端从 Patch 中已经校验的 Beat 确定性派生；模型不能提供、改写或替换其稳定 ID。前端提交后仍由服务端按当前活动支线和事实重验，不能把任意模型文本当成稳定 ID 执行。

首版动态 Choice 不增加新的模型调用，也不允许 Actor/Planner 提供 `choice_id`。服务端按 `beat_outline` 顺序，只从首个尚未被本支线已提交事实角色满足、且 `exit_preparation` 非空的 Beat 读取 `player_choice_label` 并派生一个行动按钮；`observable_action` 始终是私有编排数据。没有可验证完成证据的 Beat 只供 Actor 编排，不投影为按钮。公开 `choice_id` 使用由服务端私有 `branch_id + beat_id` 确定性派生的 opaque UUID，刷新后稳定但不直接暴露支线或 Beat 身份。活动支线期间服务端只接受当前动态按钮，旧页面或伪造请求提交作者静态 Choice、旧 Beat、旧支线、已完成或损坏状态的按钮时统一返回 `choice_not_available`，不调用 Actor、不消耗预算也不增加 revision；自由输入和明确 `user_exit` 不受影响。点击合法动态按钮后仍进入现有 Branch Actor 和事实提交链，不重新运行 Router 或 Planner。旧活动 Patch 若没有玩家按钮文案则安全显示零个按钮，玩家仍可自由输入或退出，不会把内部舞台说明暴露给玩家。

Branch Runtime 必须实现下面的确定性生命周期：

1. 每个已提交的支线输入都消耗总 `turn_budget`；没有形成可提交动作或进度时，同时增加 `nonprogress_turns`；
2. 玩家用自由输入明确完成作者项时，既有自然语言作者完成入口仍按路由优先级执行，活动支线以 `exit_kind=author_choice` 关闭，已经提交的 Branch Fact 保留；活动支线隐藏作者静态按钮并拒绝旧页面提交的静态 ID；
3. 玩家在支线中短暂闲聊时，猫娘先回应本轮输入，但不得把闲聊伪装成支线进度或 Branch Fact；
4. `nonprogress_turns` 或总预算达到作者上限且 Goal 尚未完成时，按 `branch_abort_policy` 关闭支线并回到 `return_anchor`，使用作者中性 callback；
5. 安全退出不得强制标记 Goal 完成、不得进入 Ending Domain，也不得删除已经公开发生的 Branch Fact；
6. v2.5 不允许活动支线内再创建第二条 Runtime Branch Patch。玩家提出新的合理意图时，可以先作为非推进互动回应；当前支线关闭后，新的连续证据必须重新累计；
7. 支线关闭后清除旧 `dynamic_intent`、预算和临时候选事实，只把结构化 `branch_history` 与已提交 Branch Fact 留在 Session。

v2.6 阶段九在上述“不嵌套”边界内增加显式转交：只有独立 `branch_handoff` 分类确认玩家同时要求结束当前支线和实施具体新行动，服务端又能逐字验证结束摘录与新行动摘录，且模型置信度达到严格阈值时，才以 `intent_handoff` 无预算退出并回 `return_anchor`。新行动只复用现有 `pending_intent` 保存为第一条证据，同一回合不调用 Planner、不激活新 Patch；下一轮玩家明确确认后才沿用现有双证据 Planner 链。普通增补、细化和话题转折只有明确分类为 `continue` 时才交给 Branch Actor；低置信、坏结构和技术失败不能关闭支线，并走无事实、无预算变化的技术降级。

活动支线首版固定保存 `branch_id / patch / created_revision / return_anchor / turn_budget / max_nonprogress_turns / turns_used / nonprogress_turns`。状态转换优先级固定如下：

1. 自由输入命中作者完成入口、玩家主动离场，或严格通过 `intent_handoff` 转交时先关闭支线，不消耗支线演绎预算；其中转交只回作者锚点并创建 Pending，不在同回合规划；
2. 已公开提交的支线回合增加 `turns_used`，未推进时增加 `nonprogress_turns`，推进时把连续不推进次数清零；
3. 同一提交若已经满足 Goal，先汇流；否则若满足 Ending Domain，进入作者结局；
4. 仍未完成时才判断连续不推进和总预算上限；两者同回合触发时记录更具体的 `nonprogress_exhausted`；
5. 无提交、revision 冲突和相同 `client_turn_id` 幂等回放不调用状态转换，因此不消耗或延长任何预算。

### 8.7 [已实现] Ending Domain

v2.5 不允许模型凭空创建任意结局。作者提供结局域，例如：

- 完成主线关系确认；
- 双方确认彼此重要，但把正式告白留到以后；
- 双方因公开分歧提前结束本次约会，但不改变既有身份；
- 完成当前事件后自然离场。

支线规划器只能选择作者允许的 Ending Domain，并提交该结局要求的公开证据。最终 Ending ID、状态写入和公开落幕仍由服务端规则决定。

Ending Domain 不能只是一个名称列表。作者至少需要为每个 Domain 声明：

```json
{
  "ending_domain_id": "ending_domain_gentle_pause",
  "required_goal_ids": [],
  "required_fact_types": ["observable_action"],
  "required_fact_roles": ["mutual_pause_confirmed"],
  "forbidden_fact_roles": ["unilateral_relationship_commitment"],
  "ending_id": "ending_gentle_pause"
}
```

Planner 只能提出候选 Domain；服务端按已提交 Goal、Branch Fact 和作者禁止条件重新判断。证据不足时保持支线或按安全退出策略返回，不允许模型用一段结局文案代替正式证据。

当前实现状态：Story Loader 会校验 Domain、作者 Ending、必需 Goal、必需/禁止事实角色与事实类型的稳定引用；Runtime Branch Patch 在激活前还必须证明自己能够产出所选 Domain 的全部必需事实角色和类型，避免生成形式合法但永远不可达的出口。活动支线只使用当前 `branch_id` 下已经原子提交的事实，并按 Patch 出口顺序确定性检查已完成 Goal、事实类型、必需角色和禁止角色；同一回合若同时完成 Narrative Goal，仍按 8.6 的优先级先汇流。Actor 或 Planner 输出的 Ending ID 不具备权威性，最终 `ending_id` 只能由服务端从作者 Domain 映射取得，并与 Session 终态、Branch History、活动索引清理、恢复快照和 TTS 幂等状态一起提交。上述链路已经使用独立的通用用户 Story Package 夹具完成端到端自动化验证，不依赖当前内置剧本的题材、节点或关键词。

## 9. v2.5 路由优先级

同一句话可能同时包含主线行动和自由意图，因此需要固定优先级：

1. 主动离场、非法请求和 Session 安全校验；
2. 明确完成当前可见 Choice；
3. 当前已经激活的临时支线；
4. 当前节点的作者隐藏边；
5. 通用自由意图追踪；
6. 普通场景内闲聊。

采用该顺序的原因：

- 作者写好的稳定路径优先，减少不必要的动态生成；
- 已进入的支线必须连续，不能每轮重新规划；
- 作者隐藏边比临时支线拥有更完整的铺垫和结局，应优先采用；
- 只有前面都不匹配时，才把内容作为新的通用自由意图。

复合输入允许先完成主线，再保留兼容的后续意图。例如“戴上腕带，然后先去买伞”可以先提交戴腕带，随后把买伞保存为目标节点中的待回应意图；不能丢掉后半句，也不能在旧节点先演绎后再晚一轮推进。

处理复合输入时：当前 Choice 的 callback 和作者节点先成为权威状态；目标节点的演绎可以立即承接后半句，但 `pending_intent` 仍只是下一步路由证据，不能在同一请求里绕过“连续两次”直接激活临时支线。下一轮开始前，服务端按目标 Scene、当前事实、创建 revision 和玩家最新输入重新校验；不兼容或过期就丢弃。

模型调用职责仍保持分离：普通自由输入使用“路由 + 演绎”；达到动态支线阈值的入口回合额外执行一次 Patch 规划与本地校验；Patch 只在进入支线时生成一次，不在每个支线回合重复规划。当前调用已经按职责打标，并持续导出普通回合和支线入口回合的模型调用数、token、P50/P95、Repair 率、合同拒绝率、回退率和支线终态失败率。

Router、Planner、Actor 和 Repair 必须使用不同的内部调用标签分别观测。初版可以继续复用当前已配置模型；是否按职责选择不同能力档位，只能根据路由准确率、Patch 通过率、Actor 质量、延迟与成本数据决定，不把具体供应商或模型名称写入 Story Package。不得为了减少一次调用而把 Router 和 Planner 重新合并为一个既判断路由又生成复杂 Patch 的协议。

脱敏聚合指标与 Session 私有返回记录必须保持分离。指标继续只保存调用职责、执行面、状态、token 和耗时，不接收任何正文；`llm_return_records` 则服务于指定 Session 的 Bug 复盘，按实际调用顺序保存 Router、Planner、Actor、Repair 的供应商原始 `content`。记录同时包含固定调用标签、执行面、传输状态、模型/供应商标识、异常类型、调用时间，以及提交后补齐的 `session_id / client_turn_id / base_revision / result_revision`。它不保存系统 Prompt、用户 Prompt、API Key、Base URL 或异常消息，也不进入 Projector、`public_snapshot`、HTTP 响应、TTS 或前端。

模型返回不做正文截断，因此该字段应视为与 Session 对话同等级别的本地私有诊断数据。只有完整回合通过角色归属与 revision 二次校验后，采集结果才与候选 Session 一起原子保存；被拒绝、冲突或保存前失败的候选不留记录，`client_turn_id` 幂等重放也不会重复追加。一次成功模型传输即使随后因为坏 JSON、护栏或合同问题被 Repair 或安全回退替换，原始返回仍保留，后续 Repair 返回也作为下一条独立记录保存。新 Session 初始化空列表；字段上线前的旧 Session 在下一次成功回合中补空列表，不提升公开协议或 Session schema 版本。

## 10. v2.5 完整回合时序

### 10.1 普通主线回合

1. 前端提交 `choice_id` 或玩家自由输入。
2. 服务端取得 Session 锁并校验 `client_turn_id / base_revision`。
3. 在 Session 深拷贝上执行，不直接修改磁盘真源。
4. 明确 Choice 命中后提交作者节点、事实、道具和目标完成证据。
5. 若输入还包含可分离的后续意图，在目标节点创建有时效的 `pending_intent`，但不增加其 streak。
6. 演绎模型读取提交后的状态生成旁白；静态图推进的猫娘对白原样采用目标节点 `scripted_dialogue`，普通角色互动与受约束动态支线才使用模型对白。模型可以读取下一轮作者 Choice 作为剧情交接上下文，但不能改写其文案。`scenario_card.catgirl_role` 作为独立故事身份输入，与日常人格摘要分开；当前故事身份、任务关系和公开事实决定称呼与场域语域，角色卡只影响模型拥有创作权的文本，不能覆盖作者对白或把平等队员改写成主从、亲属或既成恋人。
7. 服务端验证输出时区分硬护栏与软语义检查。坏 JSON、明确玩家完成态抢跑、作者禁用内容、关系/世界合同越界仍须在公开前 Repair 一次，Repair 仍失败才使用安全回退；反问、复述和称呼细节只影响文风，不触发 Repair、文本替换或兜底。完成态检查必须在同一分句内出现玩家主语与明确动作/完成表达，环境事件中的“了”和“立即去做”的命令不能作为玩家已完成 Choice 的证据。任何非空 `choice_rewrites` 都无条件丢弃，作者按钮保持原文，不为按钮文案额外调用模型。
8. Projector 生成公开快照。
9. 将本回合采集的私有模型返回绑定 `client_turn_id` 和提交前后 revision；随后与公开快照、幂等结果及权威状态一起原子保存。
10. HTTP 返回公开结果；已提交猫娘对白按 revision 交给 TTS。

### 10.2 第一次图外合理意图

1. 路由确认没有完成可见 Choice，也没有命中作者隐藏边。
2. 通用意图追踪生成或更新 `intent_summary`，`streak=1`。
3. 不创建支线，不写正式事实。
4. 猫娘直接回应玩家当前要求。
5. 作者静态 Choice 保持原文，不根据本轮模型文本追加或删减上下文；只有活动 Runtime Branch 才能按已验证 Patch 生成独立的动态 Choice。

### 10.3 第二次坚持同一意图

1. 路由确认本轮是对同一意图的继续或细化，`streak=2`。
2. 支线规划模型生成 Runtime Branch Patch。
3. 服务端使用 World Contract、本地 schema、当前事实和出口规则校验 Patch。
4. 校验通过后，只在候选 Session 内构造带服务端身份和回锚点的活动支线候选，尚不写入真源。
5. 入口 Actor 按已验证 Patch 回应本轮输入；首版坏 JSON、护栏失败或复读时，只允许在提交前执行一次 `theater_repair`。
6. 模型入口合法时使用人格化对白；首版与 Repair 均失败、调用异常或模型配置临时缺失时，必须改用当前公开 Scene 与合同已验证的 `seed_intent` 生成不新增事实的确定性安全对白，入口旁白保持为空，随后把 Patch、活动支线、公开演出和 revision 一起原子保存。Planner 的 `entry_callback` 只为旧 Patch 结构兼容保留，既不进入入口 Actor Prompt，也不得成为公开演出。只有 Planner、Patch 合同或入口字段本身不合法时才丢弃候选，不能因 Actor 输出抖动把已经确认两次的自由意图静默送回固定推荐项，也不能出现“台词进了支线、Session 没进”的双重现实。

### 10.4 临时支线回合

1. 路由优先读取当前 Patch，不再把玩家当成主线普通闲聊。
2. 模型提出本轮公开演出、动作结果和候选事实。
3. 服务端只提交可观察且通过校验的 Branch Fact。
4. 若尚未满足出口，保存支线进度并继续。
5. 若满足 Narrative Goal，提交目标完成并执行汇流。
6. 若满足允许 Ending Domain，由确定性规则提交正式结局。
7. 每轮提交后更新总预算和非推进回合数；达到任一上限且仍未满足 Goal 时，按作者退出策略关闭支线并返回 `return_anchor`。
8. 若模型未配置、调用失败、Repair 失败、演绎护栏拒绝或 Fact 合同整组拒绝，使用 Patch 的目标与当前事实生成技术降级演出；安全文字可以随 revision 原子提交，但不消耗总预算或非推进预算，也不改变 Fact、Goal 或 Ending。只有协议合法但没有新增事实的正常 Actor 回合才按叙事非推进计数；无提交或同一 `client_turn_id` 的幂等重试同样不消耗预算。

## 11. “送墨水”端到端示例

> 本节只用当前剧本说明协议如何工作，不授权运行时代码匹配该剧本的 ID、节点、Choice、“墨水”等关键词或固定句式。相同链路必须仅依赖 Story Package、World Contract 和 Session，在任意符合 schema 的用户剧本中成立。

假设当前公开场景是旧街杂货铺，作者推荐玩家选择星铃或木笔，World Contract 允许当地货架上的普通文具成为动态礼物。

### 回合一

玩家：“我想挑一瓶墨水。”

系统内部结果：

- 没有完成星铃或木笔 Choice；
- 没有作者预设墨水隐藏边；
- 生成自由意图“挑选墨水作为礼物”；
- `streak=1`；
- 不提交购买事实。

公开表现：猫娘根据货架、盲选规则和玩家这句话自然回应；推荐项可以变成“继续确认墨水”或“改选作者礼物”，但不能要求玩家重复已经说清的内容。

### 回合二

玩家：“黑色的，就送这个，不配笔。”

系统内部结果：

- 判断为同一意图的细化；
- `streak=2`；
- 生成“黑色墨水礼物交换”Patch；
- World Contract 确认墨水属于当前场景可用普通文具；
- 激活临时支线。

公开表现：猫娘回应黑色墨水及“不配笔”这一明确选择，不能再建议必须搭配木笔。

### 支线发展

可能发生：

1. 玩家确认选择黑色墨水；
2. 猫娘隔着货架完成自己的礼物选择；
3. 双方交换礼物；
4. 猫娘根据当前人格说出会怎样使用墨水。

只有交换真正公开发生后，服务端才提交：

```text
player selected black ink as gift
catgirl accepted black ink
personal gift exchange completed
```

### 汇流

`goal_exchange_personal_gift` 已满足，系统跳过仍要求选择星铃或木笔的旧 Choice，携带“黑色墨水已经送出”的事实进入甜品屋或下一作者节点。后续猫娘可以记得墨水，但不能凭空声称已经用它写过尚未发生的内容。

## 12. 故障、恢复与安全回退

### 12.1 模型调用失败

| 失败位置 | 当前行为 |
|---|---|
| 当前玩家原话超过职责输入预算 | 不把截断前缀送入 Router、Handoff 或 Actor；记录固定 `context_incomplete`，普通回合使用无权威公开回退，活动支线不提交事实且不消耗预算。自由意图证据若无法无损写入 Session，同样拒绝累计 |
| Planner 意图摘要或任一玩家证据超过单字段预算 | 不生成 Patch、不调用 Planner 模型；当前作者节点与意图线程保持原语义，由普通无权威演绎承接 |
| 普通路由技术失败 | 公开通用安全回应但不冒充真实 idle；动态意图不休眠，已重验 Pending 在原 TTL 内保留一次重试机会，私有技术标记不写入 Session 或公开响应 |
| 通用意图识别失败或一次普通 idle | 当作普通角色互动，不增加证据；同节点合法意图先休眠一次，休眠态不能规划，连续第二次 idle 才清理 |
| 普通 Actor 明确抢跑未提交 Choice 结果 | 作者回调不进入普通 Actor 公开上下文；只有同一分句明确写出玩家实施/完成待选动作或感谢玩家完成时才阻断，Repair 仍失败后使用不提交事实的安全回应与作者原始选项。环境完成语气、命令和建议不得参与该判定 |
| 普通 Actor 软语义质量可疑 | 反问、复述、人格自称缺失和近似照读均原样放行，不触发 Repair 或兜底；这些文本不具备写入节点、Goal、事实和结局的权力 |
| 普通 Actor 返回非空 Choice 改写 | 无条件丢弃 `choice_rewrites`，作者原始按钮保持不变；该字段仅保留旧 JSON 响应形状，不参与质量重试 |
| Actor 公开正文或按钮泄漏内部字段、机器式稳定引用或回合预算 | 视为硬越界并丢弃候选；活动 Branch Actor 同时丢弃整组 Fact Candidate，转入无事实、无预算技术降级 |
| 产品安全策略拒绝 | 不进入意图追踪或 Patch 规划；使用产品允许的安全回应，不写剧情事实 |
| Patch 生成失败 | 不进入支线；自然回应当前输入 |
| Patch 校验失败 | 丢弃 Patch；不公开校验细节 |
| Patch 出口或内容槽位非法 | 整体拒绝，不自动改绑到其他 Goal、Domain 或作者节点 |
| 支线入口 Actor 坏 JSON、护栏失败、复读或调用异常 | 在尚未展示、尚未写 Session 时对同一已验证 Patch Repair 一次；仍失败则使用公开 Scene 与合同已验证的行动方向生成无事实对白并原子激活支线，旁白保持为空，绝不公开坏输出、`entry_callback` 或退回固定推荐项 |
| 支线演绎失败 | 使用已保存 Patch 与 Branch Fact 回退，不能丢失支线；安全文字可随 revision 提交，但不消耗支线总预算或非推进预算 |
| Branch Fact 合同整组拒绝 | 丢弃整份 Actor 正文和候选并改用技术降级；不提交部分事实、不完成 Goal/Ending，也不消耗支线预算 |
| 合法 Actor 返回空事实候选 | 仍是正常的玩家非推进回合，按确定性生命周期消耗总预算与非推进预算 |
| 汇流生成失败 | 使用作者提供的中性汇流 callback |
| 支线预算耗尽但 Goal 未完成 | 保留已提交事实，按作者退出策略关闭支线，不伪造完成证据 |
| TTS 失败 | 文字结果照常成功 |

技术降级不复用作者剧情预算，因此连续基础设施故障可能让活动支线保持更久；这是首批保护玩家剧情机会的明确取舍。后续若线上数据证明需要熔断，应增加独立、低基数、可恢复的运维失败计数或暂停状态，不能重新借用 `turns_used / nonprogress_turns` 充当基础设施错误计数。

上下文化回退的边界是“不断裂、不矛盾、不抢跑”，不是在模型不可用时重新实现自然语言生成。普通回退可以承认当前公开 Scene、仍存在的未执行选择以及合法 History 中确有已确认内容；入口回退只确认当前 Scene 与合同已验证的行动方向，narration 固定为空；活动支线只把 `seed_intent` 当作玩家想做的方向，并用布尔值说明既有进展仍保留。Planner 的 `entry_callback`、原始 Branch Fact、玩家长原话、服务端 ID、预算、计数和失败原因都不能拼入对白。

### 12.2 刷新与网络重试

1. Patch 和 Branch Fact 必须保存在 Session，不得只留在模型上下文或前端内存。
2. 相同 `client_turn_id` 重试必须返回同一公开结果，不能生成第二条支线。
3. `base_revision` 冲突时前端读取最新快照，不能覆盖已经推进的支线。
4. 活动 Session 恢复后，前端只显示公开支线状态；内部 World Contract、意图计数和校验原因不投影。
5. Story Package 需要新增稳定 `story_revision`。Session 应记录开场时的 Story revision，避免更新剧本后用旧节点状态读取新图。
6. `pending_intent`、活动支线预算、非推进回合数和 `branch_history` 必须随 Session 恢复；过期 pending intent 在服务端恢复时清除，不能由前端自行判断。

当前实现中，新 Session 会同时保存 `schema_version` 与 Story Package 的 `story_revision`。恢复和未刷新页面的直接回合提交都会重验 Story revision；明确不一致时返回 `session_story_revision_mismatch`，保留原 Session 文件和 active 指针，不把旧按钮解释到新作者图。缺少 v2.5 私有字段但仍使用当前 Session schema 的早期存档，只补 `dynamic_intent / pending_intent / active_runtime_branch / branch_facts / completed_goal_ids / branch_history` 的中性空默认值和当前已成功加载 Story 的 revision，不修改节点、事实、对话或 revision。

活动支线恢复会重新校验固定字段、计数、Patch 合同、作者锚点、已提交事实/History 结构及其 revision 上界。合法状态原样继续，动态 Choice 保持稳定；超预算或 Patch 已损坏但仍保有可信服务端 `branch_id` 和作者锚点时，保留已提交 Branch Fact，以 `restore_invalid` 写入结构化 History，清除活动意图并回到该锚点，同时重建 Board 与 Choice 投影。找不到可信锚点、权威事实/History 损坏、Story 缺失或 revision 不兼容时不猜测修复、不清空进度，返回明确错误等待用户选择替换或后续兼容工具。恢复清理不调用模型、不增加 Session revision，也不会改写最后一次已提交旁白和对白。

当前前端把 `session_upgrade_required / session_version_unsupported / session_story_unavailable / session_story_revision_mismatch / session_state_invalid / session_snapshot_missing` 统一视为“存档已保留但不可安全继续”。页面使用与【通用设置】一致的蓝白信息卡明确说明旧演出不会被删除，禁用普通“开始”入口，只允许玩家通过卡片内“重新开演”发出 `replace_incompatible_session=true`。服务端对同一组六类结果执行只读开场预检，旧页面或直接 API 未携带明确确认时同样被阻断；确认后先完整创建新 Session，再原子切换 active，旧 Session 文件不标记结束、不重写快照。浏览器本地恢复指针在确认前继续指向原 Session，页面不会自动重开。

### 12.3 [已实现] 存档版本策略

v2.5 已采用“可证明兼容时中性补齐，不兼容时保留原文件并要求明确重开”的策略：

1. Session schema 仍为 `1`。同一 schema 下缺少 v2.5 私有字段的早期存档，只补 `dynamic_intent / pending_intent / active_runtime_branch / branch_facts / completed_goal_ids / branch_history` 的中性空默认值，不改节点、事实、对话或 revision。
2. Session 缺少 `story_revision` 且当前 Story 已成功加载时，可以补入当前 revision；已保存 revision 与当前 Story 明确不一致时，返回 `session_story_revision_mismatch`，不能把旧状态解释到新图。
3. schema 不支持、Story 缺失、Story revision 不一致、权威状态损坏或公开快照缺失时，保留旧 Session 文件与 active 指针，前端展示明确的不兼容信息卡。
4. 只有用户通过“重新开演”明确提交 `replace_incompatible_session=true`，服务端才先创建完整新 Session，再原子切换 active；旧 Session 文件不结束、不删除、不重写。
5. 当前策略不提供任意旧 revision 回滚或历史消息重写，避免制造两个权威现实。

### 12.4 观测与模型回归

每次模型调用至少记录调用职责、耗时、token、是否超时、解析结果、校验结果和回退原因。v2.6 另以完整 `submit` 为独立分母，记录固定输入类型、固定执行面、固定事务结果、端到端耗时及进入 `session_guard` 前的锁等待；成功、业务拒绝、异常和取消都只记录一次，相同 `client_turn_id` 回放单列为 `idempotent_replay`。指标签名不接收 `session_id / client_turn_id / story_id`、玩家原话、完整 Prompt、模型全文、角色名或人格内容。

当前生产 `instrument` 的全局直方图最大边界为 `10000ms`，超过十秒的完整事务会进入 overflow；本地显式评测窗口仍以原始数值计算精确 P50/P95。首批不为小剧场修改全局桶，若长期线上分位数需要区分十秒以上样本，应另行设计通用可配置边界。当前 `session_guard` 仍覆盖同 Session 的模型调用；首批只建立事实数据，不在没有长期等待分布时引入 Pending Turn 或锁外候选协议。

测试分成四层：

1. **确定性回归**：固定模型输出或本地兼容服务，覆盖 Router、Planner、Validator、Branch Runtime、Projector、幂等、迁移与失败回退，进入普通 CI；
2. **协议与页面回归**：验证模型 HTTP 协议、页面路由、Chromium 公开投影、恢复与错误处理，不把页面可见结果等同于真实模型质量；
3. **离线叙事评测**：固定集使用三个互不依赖的合成题材、十六个案例和人工金标，分别检查路由、事实记忆、对白—按钮一致、人格和剧情收束，并要求每个维度都跨三个题材。评分器只自动判断可精确比较的结构项；人格一致性、对白与按钮自然衔接、收束自然度在显式提交结构化 `human_review_result` 前保留为 `human_review_pending`，评分器不能代填。`uv run python scripts/run_theater_narrative_eval.py --output <显式本地路径>` 只校准固定集和评分器，零模型、零网络调用；传入另行生成的 `--observations` 后自动进入 candidate 模式，移除校准标签，机械失败或人工明确失败时返回非零，只输出脱敏案例编号、维度、结果和失败码，不把候选全文复制到报告。输出若与 dataset 或 observations 形成直接、符号链接或硬链接别名会在读取前拒绝；合法报告通过同目录临时文件原子替换，工具失败不覆盖旧报告。生成真实候选仍须单独取得模型预算授权；
4. **显式真实模型与 Electron 验收**：使用固定案例集检查自然语言准确率、Patch 合法率、汇流质量和越界率，并验证实体窗口；只通过显式开关或单独任务运行，不把一次随机模型波动直接当成代码回归结论。

真实模型层现在包含一条与内置剧本无关的通用车站 Story 长跑：连续两次 Router 必须把玩家坚持识别为同一自由意图，随后经过合同校验 Planner、严格入口 Actor 和最多三次活动支线 Actor；模型事实候选只有通过服务端 Patch 合同后才能提交，最终必须凭 `player_selected_drink / catgirl_received_drink` 两类作者证据汇流。正常链最多产生七次独立模型调用。Router 坏 JSON、入口 Actor 坏格式，以及活动 Actor 没有产生任何尚未提交事实角色时，都只允许在本轮尚未公开、尚未写 Session 的边界内执行一次 `theater_repair`；活动 Actor 的进度复核仍可返回空候选，不能把玩家的询问或计划强行提交为事实。按两次 Router、一次入口和三次活动 Actor 都触发 Repair 计算，理论最坏上限为十三次。入口 Repair 仍失败时不公开模型坏输出，而以当前公开 Scene 和合同已验证的行动方向生成通用无事实对白并原子激活支线；入口 narration 为空，Planner `entry_callback` 不进入公开演出。活动 Actor Repair 失败或没有补出新白名单角色时保留首版安全演出。测试另用不调用模型的代表性 Patch 证明夹具自身合法。入口只读取 `NEKO_THEATER_LLM_SMOKE_*` 专用环境变量；没有 `NEKO_RUN_THEATER_LLM_SMOKE=1` 时保持跳过，不读取用户日常配置或静默消耗额度。

当前 summary 配置已完成多批显式真实模型评测。初始重复运行先后暴露出职责误用、Planner 预算/空字段/数组问题、无槽动作事实复挂实体、入口坏 JSON，以及活动 Actor 偶发遗漏当前未完成事实等通用问题。修正只收紧职责入口、唯一 JSON 对象提取、通用合同和“当前待推进 Beat / 尚未提交事实合同”投影；没有加入车站、热饮或当前内置剧本特例。中间压力批次仍真实出现过 Router 坏输出、入口 Repair 拒绝和预算安全回锚，因此指标额外纳入 `branch_outcome`，避免只看传输/合同成功而漏掉生命周期失败。最终修正后的五轮完整评测均为 `5 passed`，耗时分别为 `31.35s / 28.46s / 26.58s / 25.95s / 23.51s`。

最终五轮评测窗口共记录 `47` 次模型调用，且只保存固定枚举与聚合数值：Router `20` 次（输入/输出 token `20439/1410`，P50/P95 `1535.39/3321.97ms`）、Planner `5` 次（`6479/3045`，`3530.26/3846.91ms`）、Actor `20` 次（`34623/3333`，`3926.63/4719.38ms`）、Repair `2` 次（`3536/200`，`3192.79/4282.43ms`）。按演出场景统计，自由输入 P50/P95 为 `1535.39/3321.97ms`，支线入口为 `3602.98/4356.01ms`，活动支线为 `3282.63/4167.82ms`。五个 Patch 和十组 Fact 合同全部通过，五条支线全部 `goal_converged`；Patch 拒绝率、合同越界率、回退率和支线失败率均为 `0`，Repair/Actor 为 `2/20 = 10%`。该窗口是完成验收样本，不冒充长期线上分布；生产 `instrument` 通道会继续按相同低基数维度累计。

后续只有在长期样本表明 P50/P95、失败率或 Patch 废弃率需要优化时，才评估更换职责模型、异步预规划或其他延迟优化。任何优化都必须继续服从 revision、原子提交和公开快照边界。

## 13. 前端与玩家体验

### 13.1 SillyTavern 参考结论与取舍

本轮以 [SillyTavern 官方仓库](https://github.com/SillyTavern/SillyTavern) 的 `8172dcd0ee672d3cd9a5e5f7af134f91a45cd2b8` 快照为参考，重点检查 `world-info.js`、`bookmarks.js`、聊天备份、Visual Novel Mode 和移动端样式。参考只用于验证成熟交互与上下文管理思路，不复制其聊天产品的状态语义，也不把外部实现当成本项目事实源。

| 酒馆能力 | 对小剧场的判断 | v2.5 处理方式 |
|---|---|---|
| World Info 按触发条件扫描，并按上下文比例与绝对上限控制注入预算 | **可吸收**。能避免支线事实增长后把全部历史塞回 Prompt | 在 Branch Fact 与 Branch History 达到需要检索的规模后，按当前 Goal、公开实体、事实类型和稳定引用选择相关事实，并设置确定性的 token 预算与上限；检索结果仍是上下文，不获得提交权威性 |
| 从指定消息建立 Branch/Checkpoint 快照 | **部分吸收**。结构化历史锚点有价值，但任意回滚会制造两个权威现实 | 继续使用 `branch_history / return_anchor / revision` 记录只读支线索引；不允许玩家把已提交 Session 回滚或从旧 revision 覆盖当前状态 |
| Visual Novel Mode、次要区域折叠和移动端适配 | **可吸收**。符合小剧场“演出优先”的产品目标 | 舞台、演绎日志和输入保持主视觉；剧本面板继续默认折叠；窄窗口保持 Choice 横向可读、工作区纵向滚动和输入可达 |
| 聊天备份浏览与恢复 | **暂不进入核心**。存档可见性有价值，但恢复语义必须先服从 Story revision 与 Session 原子性 | v2.5 先完成现有 Session 恢复、revision 与幂等；未来若增加玩家可见存档，只能做版本校验后的导出/归档或明确的安全恢复流程 |
| Swipe 重生成、编辑历史消息、从旧消息任意改写后续 | **不吸收**。会破坏已提交事实、TTS 和 revision 的单一真相 | 修复或重试只允许发生在当轮原子提交之前；提交后不向前端暴露改写剧情历史的入口 |
| 大量扩展与可拖拽自定义面板 | **不作为 v2.5 目标**。会扩大状态面和前端复杂度 | 保持少量稳定面板与项目统一布局；只有真实用户需求和跨剧本价值都成立时再评估扩展点 |

因此，本轮不直接移植 SillyTavern 代码。近期可落地项是统一前端聚焦体验；“受预算约束的事实检索”作为 Branch Fact 规模增长后的通用优化点，必须通过不同用户剧本的检索正确率与 token 数据再启用。

### 13.2 N.E.K.O 统一视觉与交互基线

v2.5 原则上不新增复杂面板。

小剧场以项目现有【通用设置】界面为视觉事实源，而不是照搬 SillyTavern 的深色密集聊天界面：

1. 标题栏复用 N.E.K.O 的青蓝渐变、白色窗口控件和一致的拖拽区域。
2. 页面使用淡蓝背景、白色圆角卡片、浅蓝描边和低强度蓝色阴影；主按钮使用项目蓝色渐变，危险或结束动作只使用克制的暖色语义。
3. 舞台可以保留低透明度动态氛围，但必须被蓝色背景体系收敛，不能重新形成与设置页割裂的棕黑主题。
4. 必须同时支持 `theme-manager.js` 的亮色与深色主题；深色主题只替换表面、文本、描边和阴影 token，不改变信息层级或剧情语义。
5. 桌面端优先展示舞台和演绎日志，次要剧本面板默认折叠；窄窗口下保证日志可滚动、Choice 可触达、输入区不被视口裁切。
6. 视觉调整不得改变 Session、Choice、Board、Trace、Loading、落幕、恢复、窗口控制或 TTS 链路；不得为当前测试剧本增加专属样式。
7. 舞台动态背景以 `tools/hyperframes/theater-galaxy-loop` 为 HyperFrames 真源：视觉形态参考原舞台素材，使用宽而柔和、沿途密度自然起伏的银河雾带与带内不规则聚集星尘，禁止激光式直线内核、规则稀疏点阵和剧情专属元素；银河带固定为左上到右下，所有离散星尘只沿与雾带坡度一致的相同正向向量线性移动，不允许反向、折返或随机漂移；每层位移量必须等于自身平铺周期，并用首尾快照与 8 秒、60fps 成片验证循环衔接。

前端继续展示：

- 当前公开 Scene；
- 旁白、玩家输入和猫娘对白；
- 当前可执行的行动/对白建议；
- 静态道具、线索，以及由已提交 Branch Fact 派生的动态公开物件和已发生结果；
- Loading、落幕和恢复。

前端不展示：

- World Contract；
- `intent_key / streak`；
- Patch JSON；
- “主线/支线”系统标签；
- 校验失败或拉回次数；
- 模型、Prompt、节点 ID 和内部规则。

动态公开物件必须由 Projector 根据已提交 Branch Fact 和已验证 `public_entity` 生成，与静态 Board 使用同一公开结构；前端不读取 `branch_facts` 原始对象，也不根据对白猜测物件是否已经取得、使用或失效。

Loading 文案只描述当前可感知的演出准备状态，不暴露“正在调用模型生成动态支线”。等待期间只能展示非剧情性的状态动画或占位，不能提前流式展示角色反应、旁白或候选动态物件；这些内容必须与 Session revision 一起提交成功后再显示。

第一次图外意图后，现有静态 Choice 仍显示作者原文，不能为了暗示 streak 而改写或新增“确认这个想法”“再坚持一次”等按钮。只有已经激活并校验的 Patch 才能提供绑定稳定行动方向的动态 Choice；静态作者 Choice 与动态支线 Choice 使用不同的权威来源。

## 14. 当前代码职责

v2.6 没有把全部能力继续堆进 `turn_service.py` 或已经较大的 `llm.py`，当前按职责拆分为以下模块：

| 当前模块 | 说明 |
|---|---|
| `intent_tracker.py` | 归一化自由意图、判断继续/细化/切换，维护有限证据和 active/dormant 短期线程，并只在下一轮明确确认时接纳普通复合输入或 `intent_handoff` 留下的 Pending 证据 |
| `branch_lifecycle.py` | 创建和重验 pending intent，构造活动支线并执行预算、非推进、技术降级、`intent_handoff` 与其他退出状态转换 |
| `branch_planner.py` | 调用通用 Planner，并只向下游返回通过合同校验的 Patch |
| `branch_contracts.py` | 校验 World Contract 引用、Patch、动态内容槽位、Catalog `content_id` 绑定、候选/已提交事实、Branch History、出口和 Ending Domain 可达证据 |
| `branch_runtime.py` | 提交合法 Branch Fact，按当前 Story 重验目录事实，判断 Goal/Ending Domain，派生和重验动态 Choice，提供无事实/无预算变化的技术降级提交，并生成有限 History 召回投影 |
| `fact_view.py` | 从静态事实和已完成 Goal 的作者投影构建统一只读事实视图；不读取原始 Branch Fact，也不修改 Session |
| `llm.py` | 构造普通 Router、活动支线 `branch_handoff` 轻量分类、Planner、Actor、Repair 调用，解析模型输出并提供无权威安全回退 |
| `observability.py` | 记录脱敏职责/场景指标与完整 `submit`/锁等待，并导出调用量、token、P50/P95、合同与支线终态比率；`branch_handoff` 只属于 Router 模型 surface，完整回合仍归入 `branch_turn` |
| `tests/utils/theater_narrative_eval.py` | 校验合成评测集并对可精确比较的结构项评分；人格、自然衔接和收束自然度只生成明确的人工待审项，不调用模型或网络 |
| `scripts/run_theater_narrative_eval.py` | 读取固定校准集或调用方显式提供的候选观测，拒绝输出与任一输入的路径别名，并向显式本地路径原子写入不含上下文和正文的脱敏报告；脚本自身不生成模型候选 |
| `scripts/validate_theater_story.py` | 只校验作者显式指定的一份 Story，输出稳定脱敏原因码和可选槽位执行级别；不扫描目录、不修复内容、不调用模型或网络 |

现有模块继续负责：

- `turn_service.py`：总流程、活动支线转交优先级、回锚与 Pending 桥接、锁、候选副本和原子提交；
- `story_loader.py`：World Contract、Narrative Goal、可选完成事实投影、Catalog 成员和 Story revision 校验，并提供不扫描 sibling 的单文件作者校验 API；
- `story_graph.py`：作者主线、作者隐藏边和目标汇流查询；
- `rules.py`：作者静态事实提交，以及基于统一 Fact View 的节点门禁与确定性 Ending；
- `projector.py`：只公开玩家可见状态，并把已验证的动态公开实体映射到现有 Board 结构；
- `session_store.py`：存档、恢复、revision 和幂等；
- `theater_router.py`：HTTP 和已提交对白 TTS。

禁止重新形成“普通回合一条链、动态回合另一套完整链”的双框架。两种回合必须共用同一个 Session、revision、Projector、TTS 和提交边界。

## 15. 实施阶段与当前状态

### 阶段一：协议和测试夹具（已完成）

1. 固化本文中的 World Contract、动态内容槽位、Narrative Goal、Runtime Branch Patch、Branch Fact、Branch History 和 Ending Domain 证据 schema。
2. 固化 `pending_intent`、活动支线预算、非推进回合、作者 Choice 退出、预算耗尽和安全关闭的状态转换表。
3. 为《约会清单最后一项》的礼物段补充目标式定义、作者中性汇流/退出 callback 和 World Contract 测试夹具。
4. 先保证现有两阶段模型协议回归通过，再写“墨水连续两次仍无法进入支线”的失败测试。

### 阶段二：通用自由意图追踪（已完成）

1. 实现继续、细化、替代和换话题判断。
2. 兼容口语停顿、错别字、指代和短句承接。
3. 保证一次输入只更新一次计数，刷新和重试不重复累计。
4. 实现复合输入剩余意图的创建、目标节点重验、过期和否定清理；pending intent 不直接增加 streak。
5. 明确 `scene_notes / dynamic_intent / pending_intent` 的输入来源和清理边界。
6. 阶段二只负责意图与 pending intent；支线激活继续由阶段三的 Planner、合同和入口原子边界负责。

### 阶段三：Patch 与 World Contract 校验（已完成）

1. 增加 Patch 结构化生成。
2. 增加本地 schema、动态内容槽位、引用、边界、预算和出口校验。
3. 校验通过后与当前回合公开演出一起原子保存。
4. 模型或校验失败时保持 v2.4 安全行为，绝不自动改绑 Goal、Domain 或作者节点。
5. 为产品安全策略、World Contract 和演绎输出护栏分别建立测试入口和失败原因。

当前推进状态：通用 Planner Prompt、一次性结构化调用、JSON 对象解析和 `Runtime Branch Patch` 合同校验服务已经完成；Planner 只接收作者合同、当前作者节点、公开状态、最近公开对话和自由意图语义证据，不接收 `intent_key / origin / streak`，也不能输出服务端 ID 或 revision。Router、Planner、Actor、Repair 已使用四个独立内部调用标签。第二次连续意图达到阈值后，合法 Patch 会先取得服务端 `branch_id / created_revision / return_anchor` 候选，再交给严格入口 Actor；入口 Actor 输出通过结构和演绎护栏时使用人格化对白，连续失败时使用公开 Scene、合同已验证的行动方向和通用无事实对白，随后 `active_runtime_branch`、公开旁白/对白和 revision 才在候选 Session 中一起提交。Planner 拒绝、Patch 合同或入口字段非法、角色变化、Session 替换或最终 revision 冲突都不会保存半激活 Patch。公开响应继续使用 `roleplay_response`，不向前端暴露“支线入口”内部标签。活动支线后续回合已经接入阶段四的专用 Actor 和现有原子提交边界，入口激活后的下一回合不再退回普通自由输入链。

### 阶段四：支线执行与事实提交（已完成）

1. 实现支线内继续、动作提交、Branch Fact 和结构化 Branch History。
2. 保证猫娘优先回应本轮输入，并继承当前人格。
3. 实现总预算、非推进回合、自然语言作者完成入口、用户退出、安全关闭和禁止嵌套支线。
4. 让 Projector 从已验证 Branch Fact 派生动态公开物件，不向前端暴露私有事实。
5. 保证支线恢复、幂等、revision 和 TTS 共用现有底座。

当前推进状态：活动支线后续回合 Actor 已接通，并继续复用现有 Session、revision、候选副本、公开回合记录和 TTS 链路。Actor 只接收用户 Story Package 提供的边界、当前场景、已验证 Patch 语义、已公开事实和公开对话，不接收或生成服务端事实 ID；输出中的事实候选必须与本轮公开演出一起通过合同校验，任一候选非法时整组演出丢弃并走无事实安全回退，不会部分入账。合法事实由服务端分配 `fact_id / entity_id / source_revision`，只有本轮新增且通过验证的事实才能推进预算和目标证据；模型自报的“已推进”不具备权威性。总预算、非推进上限、禁止嵌套、自然语言作者完成入口、用户主动退出、预算耗尽回锚点和结构化 Branch History 已接入。Narrative Goal 的完整证据满足后可经作者声明的中性 callback 汇流，Ending Domain 的 Patch 可达性、证据不足继续、必需 Goal、必需/禁止事实、作者 Ending 映射和终态提交也已在同一服务端执行链完成端到端验证。动态公开实体已经按固定状态枚举投影到现有 Board 三组，且只接受具备服务端提交身份的事实；原始 Branch Fact、Goal 和 revision 不向前端公开。活动 Patch 的首个未完成证据 Beat 只会使用经过玩家行动归属校验的 `player_choice_label` 投影为 opaque 动态 Choice；内部 `observable_action`、作者静态 Choice、伪造或过期按钮都不会在活动支线中公开或产生提交。Story revision、早期中性默认值迁移、活动支线恢复重验、可修复损坏状态回锚点和不可修复存档保留已完成专项覆盖；六类保留型不兼容结果的前端显式重开卡、8 语种文案、服务端确认门禁和旧文件不改写语义也已经接通。

### 阶段五：Narrative Goal 汇流与结局域（已完成）

1. 静态 Choice 和动态支线使用同一目标完成证据。
2. 完成目标后过滤过时 Choice。
3. 增加作者中性汇流 callback、Goal 相关事实选择和结构化 Branch History 召回。
4. 为 Ending Domain 增加必需 Goal、必需/禁止 Fact 证据，并由确定性规则提交最终 Ending。
5. 验证汇流后不会忘记支线事实、混淆具体完成方式或重复已经完成的动作。

当前推进状态：第 1 至第 5 项的确定性服务端链路已经完成。静态节点使用作者 `completes_goal_ids`，动态支线使用同一 `completed_goal_ids`；Story Graph 按节点完成声明和 edge Goal 绑定统一过滤推荐 Choice、自然语言完成入口与 latent transition，Planner/恢复校验拒绝重复汇流 Patch。Ending Domain 由独立用户 Story Package 夹具覆盖证据不足、Goal 门禁、禁止角色冲突、服务端作者 Ending、Session/History/active index/TTS 终态闭环；Goal 汇流后的普通 Actor 已接入 `Branch History → key_fact_ids → 已提交事实语义` 的精确有限召回，并覆盖损坏引用隔离、服务端身份删除、事实数/token 预算以及下一普通回合真实接线。真实模型通用支线长跑已连续五轮完成 Router、Planner、入口 Actor、活动 Actor、事实提交与 Goal 汇流；长期自然引用质量仍通过阶段六持续观测，而不是写入新的内容特例。所有完成和失效语义都来自用户 Story Package 明示字段，不把当前示例剧本内容写入通用规则。

### 阶段六：真实演绎验收（已完成）

1. **已完成（冻结的 v2.5 结果）**：当时 Python 小剧场自动化回归共 `259` 项，默认 CI 为 `255 passed, 4 skipped`；Router、Planner、Actor、Repair 使用独立调用标签。
2. **已完成**：当前 summary 配置完成通用用户 Story 最终五轮连续长跑，并覆盖普通演绎、明确/非明确 Choice 路由、Planner 合同、入口 Actor、活动 Actor、事实提交和 Goal 汇流。
3. **已完成**：职责/演出场景指标持续导出调用数、输入/输出 token、P50/P95、传输状态、解析/回退原因、Patch/Fact 合同、Repair 和支线终态；最终五轮样本见 12.4，长期分布继续观测。
4. **已完成现有范围**：Chromium 页面、PC 主进程/子窗口、刷新恢复和实体双显示器失焦回焦 smoke 已通过；TTS 已有已提交对白、revision 幂等和恢复不重播的确定性覆盖。
5. **已完成首轮调整**：真实运行发现的预算、空字段、数组、实体归属和入口坏 JSON 问题均按通用合同修正，没有引入异步预规划、职责模型拆分或剧情特例。
6. **未执行**：只有用户明确确认“剧本与框架运行理想，可以同步”后，才更新 `neko-theater-story-writer` Skill。

### 阶段七：v2.6 首批体验语义收口（已完成）

1. 为完整 `submit` 事务增加端到端耗时和 Session 锁等待观测，并用固定执行面区分普通回应、作者图推进、支线入口、活动支线、用户退出和幂等回放；所有标签必须来自固定低基数枚举，指标中不得出现玩家原话、Story、Prompt、角色名或模型全文。
2. 把通用自由意图从“相邻回合计数”收口为有界短期意图线程：第一次普通 `idle` 只进入休眠，连续第二次 `idle` 才清理；休眠态不能自行触发 Planner，只有同节点的明确继续或细化可以恢复。
3. 复合输入产生的合法 Pending 仍不携带服务端意图身份，也不能单独激活支线；Router 摘录必须先由服务端证明来自本轮玩家原话。只有玩家在目标节点下一轮明确确认、Router 返回 `continue/refine` 时，这条已核验摘录才可作为上一条证据，与本轮原话共同进入 Planner。
4. 为活动支线增加独立的技术降级提交路径：安全文字可以随 revision 原子提交，但不得推进 `turns_used / nonprogress_turns`，不得提交 Branch Fact、完成 Goal 或触发 Ending；合法的无事实 Actor 回合仍按正常非推进语义计数。
5. 保持 `SESSION_SCHEMA_VERSION = 1`、公开响应、Story Package、前端和八语种文案不变；新增私有字段采用旧存档缺失时的中性懒归一化，不为补字段单独改盘。

当前状态：本节的服务层、Prompt、开发文档与直接测试已经完成；完整小剧场 Python 回归为 `270 passed, 4 skipped`。四项跳过仍是显式开关的真实模型测试，本轮没有重新执行真实模型长跑或 Electron 实体窗口 smoke，因此它们只保留为 v2.5 既有证据，不作为 v2.6 新语义的完成依据。

### 阶段八：v2.6 统一只读 Fact View（已完成）

1. 新增纯函数 `fact_view.authoritative_facts`，只合并 `narrative_facts` 与已完成 Goal 的作者 `completion_fact_projections`，按事实语义稳定去重并返回副本。
2. Story Loader 校验可选投影只能是无附加字段的完整事实三元组，并拒绝同一 Goal 内重复项；旧 Story 缺省为空，无需 Session schema 迁移。
3. 静态节点前置条件、传统 Ending、阶段性输出护栏以及五个模型职责的公开状态统一读取 Fact View。
4. 原始 Branch Fact 继续保留在动态 Goal/Ending、Choice、History、Board 与恢复链，不能直接解锁静态规则；Projector、公开 API、Story 内容、前端、i18n、TTS 和 Electron 均未改动。
5. 新增跨“档案门禁”和“轨道中继维修”两个无关题材的合成测试，覆盖静态/动态 Goal 等价、稳定去重、只读副本、节点 required/forbidden、传统 Ending、模型公开状态、护栏解除、旧 Story 兼容以及原始 Branch Fact 越权隔离。

当前状态：完整小剧场 Python 回归为 `278 passed, 4 skipped`。四项跳过仍需显式开启真实模型额度；本阶段没有修改 Prompt 文案或模型协议，也没有重新执行真实模型长跑和 Electron 实体窗口 smoke。

### 阶段九：v2.6 活动支线新意图显式转交（已完成）

1. 活动支线的自由输入在 Branch Actor 之前增加独立 `branch_handoff` 轻量分类。该分类只读取 Story 的公开标题/主题/背景、当前场景、近期公开对话、玩家本轮原话，以及活动 Patch 的 `seed_intent / objective` 公开语义；不接收 `branch_id`、预算、Beat、Fact 身份或任何服务端计数。它复用 `theater_router` 职责标签并使用独立模型调用 surface，完整 `submit` 事务仍记为 `branch_turn`。
2. `intent_handoff` 必须同时满足四项门禁：玩家明确要求结束当前支线、提出一个具体且当前世界内可实施的新行动、结束摘录与新行动摘录均能由服务端在规范化玩家原话中逐字找到、模型置信度达到严格阈值。任一条件不满足都不能关闭支线。
3. 只有置信度不低于 `0.65` 的 `continue` 才进入现有 Branch Actor 与 Fact 合同；低置信 continue、`uncertain`、输出坏结构、模型未配置、超时或调用失败时，使用无 Fact、无 Goal/Ending、无 `turns_used / nonprogress_turns` 变化的技术降级，不把不确定判断冒充玩家非推进或旧支线续演。
4. 合法转交以固定生命周期事件和 History 退出码 `intent_handoff` 关闭旧支线，完整保留既有 Branch Fact 与 History 引用，不消耗旧支线预算，不伪造 Goal 或 Ending，并回到服务端保存的作者 `return_anchor`。
5. 新行动不创建第二条 Patch，也不在同一回合调用 Planner；服务端复用现有 `pending_intent` 保存经过逐字验证的新行动摘录作为第一条证据。转交当轮不再调用普通 Actor 自由演绎整句新行动，而是使用固定的“已暂停、尚未开始、等待确认”安全回应；只有下一轮玩家在锚点明确确认同一语义时，现有 Pending 双证据链才进入 Planner，普通 Actor 才恢复并可通过合法 History 召回旧事实。
6. 动态 Choice 继续直接进入 Branch Actor；`user_exit` 继续结束整个 Session；自然语言作者完成入口仍优先以 `author_choice` 关闭支线。三条既有路径均不经过 `branch_handoff`，公开响应和输入协议不增加新类型。

当前状态：本轮服务端状态机、轻量分类 Prompt/解析、回合原子编排、直接测试与两份架构文档已经完成；相关链定向回归为 `177 passed`，完整小剧场 Python 回归为 `293 passed, 4 skipped`。阶段九保持 `SESSION_SCHEMA_VERSION = 1`，没有修改 API、公开响应、前端、i18n、TTS、Electron、Story Package 或正式 Story 内容。真实模型下的转交语义准确率和新增分类延迟本轮没有执行，因此不能声称已经验证；按既定六项权重口径，v2.6 已采纳改造总体进度为 **65%**。

### 阶段十：v2.6 上下文完整性、公开边界与叙事评测（已完成）

1. 当前玩家原话在 Router、Handoff、普通 Actor、入口 Actor 和活动 Branch Actor 中统一要求完整落入 `140` token 职责预算；需要截断时不把前缀交给模型，也不累计自由意图、提交 Fact 或消耗支线预算。Planner 的意图摘要与每条证据同样必须完整落入字段预算；规划门禁还必须显式匹配当前节点、固定 `streak=2` 阈值和至少两条完整玩家证据，阈值后因 Planner 技术失败产生的第三条有界重试证据仍可保留。旧 Session 中超限、错节点、次数越界或证据数量不一致的意图状态不能触发规划。
2. Scene Note 超过固定字符上限时整条拒绝，历史玩家消息超过单条模型窗口时整条不进入 Prompt；历史猫娘同一回合的对白或旁白任一超限时也整条退出，不能只保留正向前缀。需要路由、规划或提交事实的职责默认不读取 Scene Note。这样历史句尾的否定、转折和未写入模型的片段不会在下一轮悄悄取得权威。
3. 公开演出增加内部信息硬门禁：服务端 ID、Story/Scene/Node/Choice/Prop/Clue 等机器引用、内部字段和回合预算话术一旦出现在旁白、对白或 Choice 显示文案中，整份 Actor 候选被拒绝；动态按钮和 Board 标签另在 Patch/Fact 合同入口限制为 80 字以内的单行自然语言并拒绝同类机器引用，恢复旧提交事实时也重验。活动支线越界时同时丢弃 Fact Candidate 并走无事实、无预算技术降级。Planner 的 `entry_callback` 不再进入入口 Actor Prompt 或公开演出，只保留旧 Patch 结构兼容。
4. 普通、入口、活动支线和 handoff 的确定性回退只使用有界公开 Scene、合同已验证的行动方向、合法 History 是否存在及是否已有已提交进展等低权威语义；所有回退都不复述当前长原话、不代做动作、不生成事实。支线终态指标只在 Session 原子保存成功后记录，候选冲突或保存失败不会污染完成率。
5. 新增三个互不依赖的合成题材、十六个人工标注案例、五个叙事维度和五类固定失败码；每个维度都必须横跨三个题材。默认评分器与报告脚本零模型、零网络调用，并严格区分机械结论与结构化人工复核；外部候选自动移除内置校准标签，未复核项保持 `human_review_pending`。外部模型候选必须由单独授权的任务生成，报告只保留案例编号、维度、机械/人工结果和失败码。

当前状态：完整小剧场 Python 回归为 `335 passed, 4 skipped`，离线评测校准报告覆盖全部 `16` 个案例，Ruff 和 JSON/脚本入口检查通过。四项跳过仍由真实模型显式开关控制；本阶段没有调用真实模型，没有重新执行 Electron 实体窗口 smoke，也没有修改 API、公开响应结构、前端、i18n、TTS、Story Package 或正式 Story 内容。按既定路线权重，v2.6 已采纳改造总体开发进度为 **80%**。

### 阶段十一：v2.6 动态内容 Catalog 与作者单文件诊断（已完成框架能力）

1. 零模型审计复现了原合同漏洞：文具槽即使禁止 `relationship_commitment_symbol`，任意 `wedding_ring` 对象和“结婚戒指”公开实体仍能通过，因为旧链只证明槽位 ID 与事实类型，不消费 traits。阶段十一不采用模型自报 traits、名称关键词或内置题材词表。
2. 动态内容槽新增可选 `catalog_items` 严格模式；作者成员精确声明 `content_id / entity_kind / label / fact_object / traits`。Loader 把现有 `allowed_traits` 明确解释为全部必需正向 traits，并拒绝重复 ID、空目录、缺必需 traits、命中 forbidden traits、坏实体类型/标签/稳定引用、额外/缺失字段，以及超出成员、traits、字段或 Planner 投影预算的目录。
3. Catalog 槽位的 Patch Fact Rule 与 Branch Fact Candidate 必须绑定相同 `content_id`；候选 `object / public_entity.kind / public_entity.label` 必须精确匹配作者成员，目录事实必须有公开实体。提交保留私有 `content_id`，事实去重包含该字段，Session 恢复和完成 History 召回会结合当前 Story 重验；活动支线恢复还会对回原 Patch，Projector 和 Board 不公开该 ID。
4. Planner 读取作者 Catalog，入口与活动 Actor 只接收当前 Patch 已选择的目录成员；未选择目录不会重复注入。`content_id / fact_object` 纳入内部引用泄漏门禁，不能出现在公开旁白或对白中。整个实现只查稳定引用，不含当前三份 Story 的题材关键词。
5. 新增 `validate_story_file(path)` 和 `uv run python scripts/validate_theater_story.py --story <显式文件> [--explain-slots] [--output <显式路径>]`。工具只校验一个文件，不扫描同目录、不修复、不调用模型或网络；稳定区分 JSON、根类型、Story 合同、输入/输出和内部错误，报告不含绝对路径、Story 正文或异常原文。报告不能写入正式 Story 目录；同一作者目录中任何会被直接 `*.json` glob 命中的文件或目录也以 `output_conflicts_story_directory` 拒绝，输入/输出符号链接不能绕过。无 Catalog 槽固定警告 `slot_traits_declarative_only`，合法且可执行的目录才标记 `catalog_verified`。
6. 无 Catalog 的 v2.5 Story、Patch、Fact 和旧提交结构继续兼容，避免本轮静默关闭既有玩家内容；但兼容路径不获得 traits 语义证明。三份正式 Story 本轮没有自动补目录，因为目录成员和公开标签属于作者内容，不能由框架根据现有文案猜测。正式 Story 将来启用 Catalog 时必须人工确认内容并提升 `story_revision`。

当前状态：新增 `39` 项 Catalog 合同测试、`20` 项作者 CLI 测试和 Actor 目录投影/内部引用回归，覆盖文具、轨道维修件、档案线索三个无关题材，并补齐目录容量、活动 Patch 恢复绑定和报告路径污染边界。完整小剧场 Python 回归为 `402 passed, 4 skipped`；Ruff、格式、中文 docstring、异步阻塞和 diff 检查通过。本阶段没有调用真实模型、没有重新执行 Electron 实体窗口 smoke，也没有修改 API、公开响应、前端、i18n、TTS 或正式 Story 内容。按既定路线权重，总体开发进度为 **88%**；剩余约 `2%` 是正式 Story 的人工 Catalog 迁移与严格槽位真实演绎验证，约 `10%` 是真实模型叙事质量、Session 休眠/归档和长期锁等待数据驱动的生命周期收口。

### 阶段十二：v2.6 可恢复 Session 休眠（已完成基础闭环）

1. 只读审计确认原 24 小时机会性扫描会把未完成演出直接写成 `ended`、清空 Choice、移除活动索引并用清理时间覆盖 `updated_at`；页面通常先请求故事列表再恢复，因此玩家会在没有原因提示的情况下失去产品内恢复入口。Session 文件虽未物理删除，但剧情完成、玩家离场、替换、角色切换、管理关闭与超时无法可靠区分。
2. 24 小时无成功剧情提交现在只写顶层私有 `dormant_at`，不写 `ended_at`，不改变 phase、revision、Story State、公开 Choice、`updated_at` 或活动索引。重复扫描幂等；`dormant_at / ended_at` 若存在必须是非布尔正整数毫秒，`end_reason` 必须是固定字符串枚举并伴随合法 `ended_at`。旧结束存档仍允许缺原因，但坏类型、孤儿原因和任意外部文本都不会被 truthy 值静默放行。
3. 扫描落盘前执行只读恢复预检；Story/revision、私有状态、公开快照或生命周期不兼容时，原 Session 与 active 指针保持不变。`updated_at` 缺失时才回退 `started_at`，单份坏活动时间只跳过自身，不中止同批合法 Session。
4. 休眠扫描按 `session_guard -> character_guard` 取锁，并在角色锁内重读；与同猫娘新开场替换并发时，旧 Session 最终只能是 `replaced_by_new_session` 结束态，不能被扫描旧副本复活。
5. Projector 新增固定公开 `session_lifecycle = active / dormant / ended`；`dormant` 仍保持 `can_resume=true`。前端恢复休眠演出时保留原 Scene、Board、Choice 和 revision，并用八语种状态文案说明“演出已休眠，可以继续”，不把它渲染成剧情落幕。
6. 回合候选会先在副本中移除 `dormant_at`，只有完整校验、模型/规则处理、角色与 revision 二次校验和 Session 保存全部成功后才真正唤醒。失败输入、revision 冲突、只读恢复或旧 `client_turn_id` 幂等回放均保留休眠；幂等响应会覆盖为当前生命周期，不能回放 24 小时前的 active 标记。
7. 新终止继续复用既有作者/服务端语义并保存固定 `end_reason`：`story_complete / branch_ending_domain / user_exit / replaced_by_new_session / character_switch / management_end / start_publish_failed`。明确终止会清除休眠标记；旧结束存档允许缺字段。
8. 本阶段只实现可恢复“休眠”，不冒充真正“归档”。归档若要清除 active 指针，必须同时设计历史列表、玩家可见入口和原子恢复协议；否则仍会制造不可访问存档。正式 Story 的五个槽位也经只读审计确认没有成员级作者授权，本阶段不根据现有道具或对白猜配 Catalog，不修改正式 Story revision。

当前状态：新增休眠/唤醒、扫描幂等、真实 Session 锁争用、扫描与新开场替换竞争、幂等不唤醒、非法与孤儿生命周期字段保留、不兼容存档扫描原样保留、坏活动时间单文件隔离、固定终止原因、前端八语种提示和 Chromium 恢复回归；完整小剧场 Python 回归为 `416 passed, 4 skipped`。本阶段没有调用真实模型、没有执行人工叙事复核或 Electron smoke，也没有修改正式 Story。按既定路线权重，总体开发进度为 **92%**；剩余约 `2%` 是正式 Story Catalog 的作者决策，约 `5%` 是显式真实模型候选与人工叙事复核，约 `1%` 是需要线上长期样本才能判断的锁生命周期优化。真正归档属于另行产品能力，不在没有恢复入口时计作应补代码。

#### 产品复议：24 小时自动休眠（候选删除，尚未执行）

1. 产品侧认为“超过 24 小时自动进入休眠”可能属于多余机制，后续候选方案是删除自动时间判断。本记录表示待复议意见，不代表已经批准删除；当前代码和测试仍按上面的既有行为运行。
2. 主要质疑是：当前休眠不会释放文件、清除活动索引或形成真正归档，只增加一个状态提示；与此同时，它引入机会性扫描、额外生命周期字段、并发锁、兼容恢复、前端分支、八语种文案和相应测试，产品收益可能不足以覆盖维护成本。
3. 删除前必须先明确两项兼容决策：已有 `dormant_at` 存档应如何无损恢复为 active，以及公开 `session_lifecycle` 是收缩为 `active / ended` 还是暂时保留兼容字段。不得通过删除 Session 文件或清除 active 指针处理历史休眠存档。
4. 若确认删除，实施范围应严格限于自动休眠链：停止 24 小时扫描与 `dormant_at` 新写入，移除唤醒分支和休眠提示，并同步 Router、Runtime、Projector、前端、八个 locale、测试和两份架构文档。固定结束原因和正常 Session 恢复属于独立能力，不应随休眠机制一起删除。
5. 在产品最终确认前，不继续扩展真正归档、更多休眠策略或新的超时配置，也不以本节为依据提前修改代码。

### 阶段十三：v2.6 叙事评测报告输出安全（已完成）

1. 只读审计确认原评分命令在完成读取和评分后直接使用 `Path.write_text`：`--output` 与 `--dataset` 或 `--observations` 同路径时会覆盖输入；输出符号链接会跟随并截断目标输入，硬链接则会改写同一 inode。已有报告也可能在写入中途只剩空文件或半截 JSON。
2. 命令现在会在任何输入读取、目录创建和报告写入前逐一比较输出与两份输入：解析后的同一路径和符号链接别名由 `resolve(strict=False)` 拒绝，已存在的硬链接由 `samefile` 拒绝；路径比较本身失败时按冲突关闭，不能把无法证明安全当成覆盖授权。
3. 合法报告复用项目 `atomic_write_text`，在目标目录创建唯一临时文件，完整写入、flush、文件 `fsync` 后以 `os.replace` 提交；替换失败会清理临时文件，并保持既有报告及两份输入逐字不变。
4. 工具错误固定返回 `2`，并只在 stderr 输出稳定原因码：`output_conflicts_input / dataset_json_invalid / dataset_schema_invalid / dataset_read_failed / observations_json_invalid / observations_schema_invalid / observations_read_failed / output_write_failed / internal_error`。控制台不回显绝对路径、候选正文、异常类型、异常消息或 traceback。
5. 原退出语义保持不变：校准标签全部匹配，或 candidate 没有机械失败和人工明确失败时返回 `0`；校准失配、candidate 机械失败或人工明确失败时返回 `1`，且仍生成完整脱敏报告。人工待审不被擅自当作失败或通过。

当前状态：叙事评测单元回归为 `36 passed`，与作者单文件工具交叉回归为 `56 passed`，完整小剧场 Python 回归为 `432 passed, 4 skipped`。本阶段没有调用模型或网络、没有执行人工叙事复核或 Electron smoke，也没有修改正式 Story、Session、API、前端、i18n 或 TTS。该项是已建评测底座的确定性安全加固，没有完成剩余的作者决策、真实模型质量或线上长期数据门禁，因此总体开发进度保持 **92%**。

### 阶段十四：v2.6 Story 作者真源与公开投影收口（已完成框架主链）

1. 只读审计确认 `scenario_card.brief` 与顶层 `background` 重复，且 `scenario_card.rules` 把选剧介绍和生成约束混在同一公开对象；选剧接口还曾下发 `summary` 与全部 Scene，使浏览器在开演前持有后续剧情。当前协议把顶层 `background` 定为唯一公开背景真源，`GET /stories` 固定返回 `id / title / background / initial_scene`，Story 声明角色卡时再附带仅含玩家角色、猫娘角色和不剧透目标的 `scenario_card`，并保持纯读取。
2. 三份正式 Story 已删除 `scenario_card.brief / rules`，公开目标改为不剧透表述；约会故事背景移除“只有两位角色发言”等生成规则，未来故事背景移除协议权力边界，完整约束继续只存在于 `restrictions / runtime_guardrails / seed / world_contract`。初始 Scene 只描述开场时刻，不再承担稳定背景职责。
3. Loader 现在要求顶层背景、完整 Scene、唯一阶段、唯一 setup seed、合法节点类型、非 seed 节点的正式 `scripted_dialogue`、显式 ending ID、完整作者 Choice 字段和全节点可达的前向无环图；推荐边必须能匹配作者 Choice，非结局节点必须存在结构出口。缺阶段、缺作者对白、缺 Choice ID/文案/模式/callback、孤立子图、非结局死路、隐式结局或错误 Story ID 都直接拒绝，运行时不再用数组首项、节点标题、Node ID、模型对白或固定文案补造作者语义。
4. `opening_dialogue` 与静态节点 `scripted_dialogue` 作为作者可播放正文原样投影：开场不再调用模型做人格化转述，静态图推进只采纳模型旁白并覆盖回作者节点对白。`choice_rewrites` 只保留为空数组的旧模型 JSON 形状，解析器与 Turn Service 都不接受改写；旧 Session 的 `choice_label_overrides` 被忽略并在后续可保存回合清除。玩家主动离场只提交管理状态，不再伪造一句角色告别台词。
5. 作者边界不再只依赖 Prompt。服务端把 `seed.forbidden_assumptions` 确定性并入 Runtime Branch Patch；Branch Fact 提交门会再次拒绝命中禁止三元组的候选，并拒绝用同一主语和谓词的另一对象改写 `world_contract.immutable_facts`。任一候选越界仍原子拒绝整组，不能借合法 `fact_role` 完成 Goal。
6. 本阶段没有为三份正式 Story 自动猜配 Catalog。无 Catalog 的五个动态内容槽继续是明确标记的 `declarative_only` 兼容路径，尚不能证明模型生成的具体对象符合 traits；目录成员与公开标签必须由作者确认后才能提升 Story revision。普通角色互动和活动支线在模型故障时仍使用不提交事实的通用技术降级文本，这属于基础设施表现而非 Story 内容；若产品要求“可见角色文字也必须零框架代写”，需要另行设计公开降级状态与前端提示，不能把内部生成指南直接当对白。
7. Edge 缺少 `visibility` 时沿用 `recommended` 仍作为旧 Story 兼容规则保留。改成强制显式字段会让既有用户 Story 与存档失效，必须在兼容迁移策略确认后单独实施，不能在本轮静默收紧。
8. 三份正式 Story 的顶层 `background` 已改写为单段电影式剧情简介，按 `len(background.strip())` 计算实际为 `357 / 398 / 448` 个 Unicode 字符；继续以 `background` 为唯一公开背景真源，不增加 `synopsis`，不使用 `summary` 或 `scenario_card.brief`。简介只重组既有世界与双主角处境、触发事件、两至三个升级压力、双方方法冲突和未决悬念，不泄露关键反转、关系或救援结果、正式结局，也不混入 Story、Scene、Node、Choice、Goal、模型、规则或护栏等系统语言；初始 Scene、静态节点、Choice、事实、Goal、结局与 Session 合同均未改变，因此不提升 `story_revision`。

本轮电影式简介改写后的新鲜验证：Story 与 Chromium 定向回归为 `67 passed`，完整小剧场 Python/Chromium 回归为 `453 passed, 4 skipped, 25 warnings`；四项跳过仍是显式真实模型测试，25 项 warning 均为既有依赖或框架弃用提示。三份正式 Story 的单文件校验均返回 `valid`，无 Catalog 的五个槽继续只报告预期的 `slot_traits_declarative_only` 兼容警告；个人 Skill 通过 `quick_validate.py`，三条未提示目标长度的隔离前向输出为 `380 / 400 / 398` 字。该轮没有调用真实模型、执行人工叙事复核或重跑 Electron 实体窗口。按当前剩余门禁，v2.6 总体开发进度为 **95%**。

### 阶段十五：v2.6 Session 私有模型返回记录（已完成框架主链）

1. 新 Session 增加顶层私有 `llm_return_records`，统一模型传输入口按实际发生顺序采集 Router、Planner、Actor 与 Repair 的供应商原始 `content`。成功、超时和异常分别保留固定状态；异常只记类型，不保存可能夹带请求信息的异常正文。
2. 采集使用异步上下文隔离，每个回合得到独立列表，不跨 Session 或并发 Task 串写。采集上下文覆盖完整 `_apply_turn`，因此首个坏返回、Repair 返回及随后被服务端覆盖的返回都能保留，而没有活动回合上下文的普通模型调用不会污染历史 Session。
3. 模型返回只在回合通过角色归属、活动 Session 和 revision 二次校验后绑定 `session_id / client_turn_id / base_revision / result_revision`，并与候选 Session 一起原子保存。失败候选不写盘；旧 `client_turn_id` 在采集前命中幂等缓存，因此不会重复追加。
4. `Projector` 不读取该字段，公开响应、`public_snapshot`、TTS、前端与脱敏聚合指标均保持原协议。记录明确排除 Prompt、API Key、Base URL 和异常消息；原始模型正文不截断，所以 Session 文件属于本地敏感诊断数据，后续若增加导出、上传、共享或保留期必须另行设计授权与脱敏，不能直接复用该私有字段。
5. 字段上线前创建的当前 schema Session 在下一次成功回合中补为空列表，不修改旧文件的失败候选，也不提升 `SESSION_SCHEMA_VERSION`。记录能力只改善 Bug 证据，不改变路由、状态权威、剧本内容或玩家可见演出。

当前状态：模型采集、统一入口、Session 原子提交、幂等隔离与公开边界的定向回归为 `174 passed`；完整小剧场 Python/Chromium 回归为 `457 passed, 4 skipped, 25 warnings`。四项跳过仍是显式真实模型测试，warning 均为既有依赖或框架弃用提示。本阶段没有调用真实模型、执行人工叙事复核或重跑 Electron 实体窗口；它补齐了后续演绎问题复盘所需证据，但没有替代正式 Story Catalog、真实模型候选、人工自然度或长期锁数据门禁，因此 v2.6 总体开发进度保持 **95%**。

## 16. v2.6 验收标准

### 16.1 自由度

- 玩家第一次提出合理图外行动时得到直接回应。
- 玩家第二次坚持同一行动时进入围绕当前内容生成的临时支线。
- 同义表达、错别字、指代和细化短句能维持同一意图。
- 换话题后不会错误沿用旧意图计数。
- 复合输入先提交当前 Choice，后半句在目标节点得到回应并保存为有时效的 pending intent；它不会在同一请求内直接触发支线。
- pending intent 过期、与新 Scene 不兼容或被玩家否定时会清除，不会在后续意外复活。
- 玩家不点击推荐按钮也能完成临时支线、汇流或抵达允许结局。

### 16.2 逻辑自洽

- 支线只使用当前公开人物、地点、物件和已知事实，或 World Contract 允许的普通新增内容。
- 猫娘没有读心、上帝视角或未来知识。
- 支线完成的物品与行动在后续持续有效。
- 进入 Board 的动态物件都来自已提交 Branch Fact 和已验证动态内容槽位，前端不从对白推断状态。
- 已完成目标的旧 Choice 不再出现。
- 汇流台词承认支线余波，不假装支线没有发生。
- 汇流使用当前 Goal 的关键 Branch Fact 保留具体完成方式，但不会为每种物件膨胀出专用状态字段。
- Ending Domain 只有在作者声明的必需 Goal 和事实证据全部满足时才能提交。
- 已完成 Goal 的作者投影可以满足静态节点、传统 Ending 和阶段护栏；原始 Branch Fact 本身不能直接命中这些静态条件。

### 16.3 稳定性

- Patch 生成或校验失败不污染 Session。
- 相同 `client_turn_id` 不会创建重复支线或重复事实。
- revision 冲突不能覆盖较新的支线进度。
- 刷新和活动 Session 恢复后可以继续同一支线。
- 24 小时无剧情提交只进入可恢复 `dormant`，不改变 phase、revision、事实、Choice、`updated_at` 或活动索引；页面恢复明确提示休眠而不是落幕。
- 只有成功新回合可以原子唤醒；失败、冲突、只读恢复和幂等回放不唤醒。真正结束保存固定原因，休眠不写 `end_reason`。
- 不兼容存档、非法生命周期字段和坏活动时间不能被休眠扫描改写；坏活动时间只隔离当前 Session，不阻断同批合法扫描。
- 休眠扫描与同猫娘新开场替换并发时，被替换旧 Session 不能从结束态复活。
- 支线达到预算或非推进上限时会保留已提交事实并安全返回，不会死锁、伪造 Goal 或进入未授权结局。
- 活动支线内不能创建嵌套 Runtime Branch Patch；作者静态按钮不再与支线按钮竞争，但自由输入明确完成作者项时仍可关闭当前支线。
- Branch History 只引用关键事实和退出结果，不把自然语言摘要当成权威状态。
- TTS、前端投影和落幕继续使用已提交公开快照。

### 16.4 观测与性能

- Router、Planner、Actor、Repair 的调用数、耗时、token、失败和回退原因可以独立统计。
- Planner 只在进入支线时调用一次；普通支线回合不重复规划。
- 前端等待期间不展示未提交剧情内容，失败后不会留下“演出已经发生但 Session 没有记录”的残影。
- 完成声明包含普通回合与支线入口回合的 P50/P95、失败率和 Patch 拒绝率；真实模型未运行时明确标注未验证，不用单元测试结果替代。

### 16.5 示例 Story 协议验收（非框架特例）

下列“墨水”场景只验证当前示例 Story Package 是否正确使用通用协议；框架验收必须还能替换为题材、物件和目标不同的用户 Story，生产代码不得匹配这里的内容词。示例体验应满足：

1. “挑一瓶墨水”被记录为第一次自由意图；
2. “黑色的，就送这个”被判断为同一意图的细化；
3. 第二次输入当轮进入墨水临时支线；
4. 猫娘不再要求必须放下墨水或搭配木笔；
5. 交换完成后提交“黑色墨水已经作为礼物送出”；
6. 礼物 Narrative Goal 完成；
7. 系统跳过星铃/木笔旧 Choice，自然进入后续剧情；
8. 后续对白记得墨水，但不虚构尚未发生的使用经历。

### 16.6 v2.6 首批新增验收

- 一次 `submit` 无论成功、业务拒绝、异常或取消都只记录一次完整事务结果；执行面只使用 `roleplay_response / graph_progress / branch_entry / branch_turn / user_exit / idempotent_replay / unresolved / invalid` 固定枚举，Session 锁等待只统计进入 `session_guard` 之前的等待，不把持锁模型耗时误算成锁等待。`branch_handoff` 只是 Router 模型调用 surface，完整活动支线自由输入事务仍记为 `branch_turn`。
- 相同 `client_turn_id` 的回放单列为 `idempotent_replay`，仍记录本次请求的端到端耗时，但不能混入首次执行的成功样本，也不能重复提交状态或消耗叙事预算。
- 第一次普通 `idle` 后，同节点意图保留服务端身份和有限证据但进入休眠；休眠态不会触发 Planner，连续第二次 `idle` 清空，`continue/refine` 可以恢复并累计。
- 合法 Pending 的摘录必须能在创建回合的规范化玩家原话中逐字找到；只有下一轮明确确认同一语义时，它才与本轮组成两条玩家证据并进入 Planner。模型改写/虚构摘录、Pending 单独存在、换意图、过期、错节点、作者推进或幂等重试均不得重复规划。
- Router 的模型未配置、调用失败或 Repair 失败使用私有技术标记，不能当成真实 `idle`；已经验证的意图线程保持不变，Pending 对象与原 `expires_revision` 不变，只在既有 TTL 内保留一次重试机会。技术标记不得写入公开响应或 Session。
- 技术降级回合可以保存安全旁白和对白并增加 revision，但支线预算、事实、Goal 和 Ending 保持不变；正常 Actor 的合法空事实候选仍消耗非推进预算。
- 所有新增观测标签都来自固定枚举；报告、instrument、公开响应和 Session 文件中都不包含玩家原话、Prompt、Story 内容、模型全文或内部技术降级标记。

### 16.7 v2.6 统一 Fact View 新增验收

- 静态节点与动态支线完成同一 Narrative Goal 时，Fact View 产生相同作者投影，且不会把投影写回 `narrative_facts`。
- required/forbidden 节点条件、传统 Ending、阶段输出护栏和五个模型职责读取同一权威事实结果。
- 未完成 Goal、未知 Goal、模型原始 Branch Fact、服务端 Branch 身份和重复投影均不能污染该视图。
- 旧 Story 缺少 `completion_fact_projections` 时保持原行为；正式 Story 新增投影属于作者语义变化，按 Story revision 门禁管理。

### 16.8 v2.6 活动支线意图转交新增验收

- 只有“明确结束旧支线 + 具体新行动 + 两段原话逐字可证 + 高置信”同时成立时，才产生 `intent_handoff`；普通增补、细化、话题转折和单纯提出另一行动都不能误关当前支线。
- `continue` 继续进入现有 Branch Actor；`uncertain`、坏结构和分类基础设施故障只提交无事实、无预算变化的安全降级，不产生 handoff、Goal、Ending 或新 Patch。
- `intent_handoff` 保留旧支线全部已提交事实，以固定 History 退出码关闭并回作者锚点；旧事实继续可由 Board 与合法 History 召回，不合并到静态事实存储。
- 新行动只作为锚点范围内 Pending 的第一条证据；同回合不调用 Planner，下一轮明确确认后才可进入现有双证据规划链，因此任意时刻最多只有一条活动 Runtime Branch。
- 动态 Choice、`user_exit`、自然语言作者完成入口、幂等回放、revision 冲突和最终原子保存保持原有优先级；冲突候选不能留下半关闭 History 或半创建 Pending。
- 本轮自动化只证明协议、状态与原子边界；真实模型语义准确率、误中断率和新增分类 P50/P95 必须在显式评测后单独报告。

### 16.9 v2.6 上下文完整性与叙事评测新增验收

- 当前玩家原话或 Planner 任一语义证据超过职责预算时，Router、Handoff、Planner 和拥有 Fact Candidate 的 Actor 均不得读取截断前缀、累计意图、提交事实或消耗支线预算；下一轮也不能通过被截短的历史消息、Scene Note 或旧动态意图重新获得权威。
- 超限 Scene Note、历史玩家消息，以及猫娘同回合中任一超限的旁白/对白按整条拒绝；旧 Session 中字段缺失、超限、来源节点错误、`streak` 越界或证据数量不足的 `dynamic_intent` 不满足 Planner 门禁。合法短历史只能提供无权威演绎上下文，不能修改完整服务端事实。
- 模型旁白、对白、动态按钮和 Board 标签中的内部字段、服务端 ID、Story/Scene/Node/Choice/Prop/Clue 稳定引用及预算话术均按硬越界拒绝；动态按钮与 Board 标签还必须是 80 字以内的单行自然语言，入口 `entry_callback` 不进入 Actor Prompt、Repair 或公开回退。静态 Choice 直接使用作者原文，不经过 Actor 显示改写。
- 普通、入口、活动支线与 handoff 回退可以承认当前公开 Scene、合同已验证的行动方向、合法 History 和既有进展，但不得复述长原话、补写动作结果、创建事实或暴露内部身份。
- `safe_fallback`、`context_incomplete` 与支线终态使用固定低基数指标；终态只有在 Session 保存成功后才计入，候选冲突、保存失败或幂等回放不能重复污染结果。
- 固定叙事评测集至少覆盖三个独立合成题材、五个维度和十二至十六个案例，并锁定每个维度的跨题材覆盖。机械评分、人工复核、校准模式和外部候选模式必须分开；没有真实模型候选或人工复核时，只能声明评测底座和校准通过，不能声明五维叙事质量通过。
- 叙事评测输出不能与 dataset/observations 形成直接、符号链接或硬链接别名；输入、schema、编码、原子替换或内部异常失败时，输入和既有报告保持原字节，只输出稳定脱敏原因码。校准、质量门禁和工具错误继续分别使用退出码 `0 / 1 / 2`。

### 16.10 v2.6 Story 作者真源新增验收

- 选择页背景只读取顶层 `background`，不增加 `synopsis`，也不重新启用 `summary` 或 `scenario_card.brief`；`scenario_card.brief / rules` 被 Loader 拒绝，`summary`、未来 Scene、限制条件和运行时护栏不进入 `GET /stories`。
- 正式发布 Story 的 `background` 按 `len(background.strip())` 计算必须为 350–500 个 Unicode 字符，并写成单段电影式剧情简介：依次覆盖时代与地点、双主角当前关系和初始失衡、触发事件与眼前目标、两至三个升级压力、双方方法或价值观冲突，以及未决悬念；猫娘保持共同主角的主动性与边界。简介不得公布关键反转、关系或救援结果、正式结局，也不得混入 Story、Scene、Node、Choice、Goal、模型、规则或护栏等系统语言。该长度是正式发布 Story 与生成 Skill 的作者验收，不是 Loader 对自定义 Story 的硬长度门禁。
- `GET /stories` 是纯读取；只有启动 Session 的写流程可以触发当前仍保留的 24 小时休眠扫描。
- Scene 缺 ID/phase/title/text、节点阶段未知、setup seed 不唯一、非 seed 节点缺作者 `scripted_dialogue`、Choice 缺 ID/label/mode/callback、推荐边没有对应 Choice、存在不可达节点、非结局死路、静态图成环或 ending 节点缺显式 `ending_id` 时，Story 在进入运行时前失败。
- 开场对白逐字等于作者 `opening_dialogue`；静态图推进对白逐字等于目标节点 `scripted_dialogue`。作者未提供开场对白时保持为空，内部 `runtime_generation_guide` 永不冒充可见角色台词。
- 模型返回任意非空 `choice_rewrites` 或旧 Session 残留 `choice_label_overrides` 都不能改变静态 Choice 文案、ID、模式、目标或 callback。
- Branch Fact 即使拥有合法类型与完成角色，只要命中作者禁止假设或以相同主语/谓词冲突不可变事实，就必须整组拒绝，不能完成 Goal 或 Ending。
- 显式未知 Story ID 返回 `story_not_found`，缺失 Scene、Choice、Ending 或节点不能回退到目录第一份 Story、数组第一项、节点标题、Node ID 或框架固定剧情文案。

## 17. 测试范围

下列第 1 至第 39 项均已有确定性自动化、显式真实模型评测或实体 smoke；每项具体证据层级以测试开关和第 18 节声明为准：

1. Story Loader 对 World Contract、动态内容槽位、Narrative Goal、Story revision、退出策略和 Ending Domain 证据的校验；
2. 同义、错别字、标点、短指代、话题切换、一次 idle 休眠、同节点恢复、连续第二次 idle 清理和旧 Session 懒归一化的意图测试；
3. 第二条真实证据触发、首次不触发、休眠态不自行触发，以及同节点明确 `continue/refine` 恢复后可以触发；单句高置信不能越过阈值；
4. 复合输入 pending intent 的创建、目标节点重验、过期、否定、revision 冲突、不提前累计，以及下一轮明确确认时把 Pending 作为前一条证据并当轮进入 Planner；
5. Patch 未知引用、越界事实、第三角色、无出口、未知内容槽位和超预算整体拒绝，且不自动改绑；
6. Branch Fact 只有公开动作发生后才提交，服务端 ID、来源 revision 和动态公开实体不可由模型伪造；
7. 静态路径和动态路径都能完成同一 Narrative Goal；
8. 汇流后过时 Choice 消失，具体完成方式、关键 Branch Fact 和结构化 Branch History 保留；
9. Ending Domain 的必需 Goal、必需/禁止事实全部由服务端判断，证据不足不能结束；
10. 支线总预算、非推进回合、自然语言作者完成入口、静态按钮隔离、用户退出、安全关闭、禁止嵌套和已提交事实保留；
11. 产品安全策略、World Contract、演绎输出护栏分别失败时的无污染回退；
12. 模型失败、坏 JSON、超时、汇流失败和作者中性 callback；
13. Session 恢复、幂等、revision、并发、角色切换、闲置休眠/唤醒、固定终止原因和 Story revision 迁移；
14. 前端 Loading、Choice 更新、动态 Board、落幕和公开信息隔离，未提交剧情不提前显示；
15. TTS 只朗读新提交的猫娘对白，不朗读恢复快照或内部规则；
16. Router、Planner、Actor、Repair 的独立观测标签、调用数、token、延迟和回退指标；
17. 确定性 CI 回归、显式开启的真实模型评测与 Electron 窗口 smoke；
18. 完整回合成功、业务拒绝、异常、取消、锁等待、固定执行面、幂等回放与低基数隐私标签的精确一次观测；
19. 活动支线技术降级不推进预算/事实/Goal/Ending，以及合法空 Fact Candidate 仍按正常非推进回合计数；
20. Router 技术故障不让意图线程休眠，并让已验证 Pending 在原 TTL 内保留一次重试机会；下一轮明确确认仍可规划，且私有技术标记不会进入公开响应或 Session；
21. 静态节点、传统 Ending、阶段输出护栏和模型公开状态都能读取已完成 Goal 的作者事实投影，静态与动态 Goal 完成路径得到相同结果；
22. 未完成/未知 Goal 和原始 Branch Fact 不能进入统一 Fact View，投影稳定去重、只读复制，Loader 拒绝附加身份字段与重复项；
23. 活动支线 handoff 分类的严格字段、双摘录来源、高置信门禁，以及 `continue / intent_handoff / uncertain / technical_degraded` 四类结果隔离；
24. `intent_handoff` 的无预算 History、事实保留、作者回锚、Pending 第一证据、下一轮确认后才规划、禁止嵌套，以及动态 Choice、用户退出、作者完成、幂等和 revision 冲突优先级；
25. Router、Handoff、Planner、普通/入口/活动 Actor 对当前玩家原话与意图证据的完整预算门禁，超限时统一 `context_incomplete` 且不把前缀交给权威职责；
26. 超限历史玩家消息、猫娘同回合旁白/对白、Scene Note 和旧动态意图证据的整条拒绝，错节点/伪造次数/证据不足不能触发 Planner，以及跨两轮输入仍不能利用被裁掉的句尾推进或提交事实；
27. 公开旁白、对白、Choice、动态按钮和 Board 标签中的内部字段、稳定机器引用、服务端状态 ID 与预算话术拒绝，按钮/Board 标签的长度与单行门禁，以及 `entry_callback` 不进入入口 Prompt、Repair、旁白或对白；
28. 普通、入口、活动支线与 handoff 的跨题材上下文化安全回退，确保只使用有界公开锚点、无事实、无代做动作且不泄漏私有身份；
29. Fact 合同拒绝后的 `safe_fallback`、`context_incomplete` 与 Session 保存后支线终态观测，确保冲突候选和保存失败不计入完成率；
30. 三个独立合成题材、十六个案例、五维覆盖、五类失败码、机械与人工分离、外部候选标签隔离、脱敏报告和零模型默认路径的叙事评测回归；
31. Catalog Loader 的精确成员字段、必需/禁止 traits、重复 ID、实体类型和文具/维修件/档案线索三种无关题材；
32. Patch、Fact、提交、原子拒绝和恢复对 `(content_slot_id, content_id)` 的精确绑定，以及 object、kind、label 篡改和跨槽冒用拒绝；
33. Planner/入口/活动 Actor 的已选目录投影与 `content_id / fact_object` 公开泄漏门禁，未选择目录不得进入 Actor 上下文；
34. 单文件作者校验的稳定退出码、输入不变、坏 sibling 隔离、报告脱敏、显式输出冲突和零模型/零网络边界；无 Catalog 旧 Story、Patch 和 Fact 继续兼容但固定报告 `declarative_only`。
35. 24 小时休眠不改剧情状态、Choice、最后活动时间或 active 指针，重复扫描幂等，成功回合原子唤醒，失败/冲突/幂等不唤醒；覆盖真实 Session 锁争用、扫描与新开场替换竞争、`dormant_at / ended_at / end_reason` 非法与孤儿组合、坏 `updated_at` 批次隔离、不兼容文件与 active 指针保持不变，以及前端 `active / dormant / ended` 生命周期与八语种提示。
36. 叙事评测输出与 dataset/observations 的直接、相对解析、符号链接和硬链接冲突拒绝，坏 JSON/schema/UTF-8 与内部异常的稳定脱敏错误，原子替换失败时输入和既有报告逐字不变，以及校准 `0`、质量失败 `1`、工具错误 `2` 的退出语义兼容。
37. 安全故事卡只公开稳定背景、结构化角色/目标和初始 Scene，未来剧情与生成约束不下发；故事列表纯读取，不触发 Session 休眠或其他写入。
38. 严格 Story 合同覆盖完整 Scene、唯一 setup seed、显式作者 Choice、前向无环图、非结局出口、显式 ending ID、未知 Story ID，以及开场/静态节点作者对白与静态 Choice 不经模型改写。
39. Runtime Branch Patch 确定性并入作者 seed 禁止事实；Branch Fact 提交门拒绝禁止三元组与不可变事实冲突，并保持候选整组原子拒绝。

## 18. 当前基线与完成声明规则

当前正式 Story Package 包含《约会清单最后一项》《明天还会有两只杯子》和《等轨道灯再亮一次》三份用户可选故事。第一份包含十九个节点、十九条边和两个作者结局；第二份是原创温暖科幻成长故事，包含二十八个节点、二十九条边、三个动态汇流目标和两个自主关系结局；第三份是原创双人星球求生故事，包含二十八个节点、二十八条边、三个必须由双方专业证据共同完成的汇流目标、二十五回合静态主路径和两个获救后关系结局。三份题材与结构不同的正式剧本共用同一 Loader、Runtime、Projector 和前端故事卡，不允许在生产代码中增加题材专属判断。v2.4 已经具备作者隐藏边试点、两阶段自由输入路由、Session 恢复、TTS 和相关自动化回归；这些能力构成 v2.5 的实施基线。

冻结的 v2.5 基线共收集 `259` 项 Python 小剧场回归，结果为 `255 passed, 4 skipped`；四项跳过来自普通 CI 下的真实模型显式开关，其中三项是一轮生成/路由质量检查，一项是完整通用支线长跑。它们不能在默认 CI 中表述为已执行，但当时的 summary 配置已经另行完成最终五轮完整显式评测且全部通过。该基线的定向验证覆盖 Runtime、模型/Story/PC 契约、复合输入、通用自由意图、Pending Intent、Planner Prompt 与独立调用、Patch 合同失败回退、唯一 JSON 对象提取、Router/入口/活动 Actor 提交前单次 Repair、入口 Actor 连续坏输出后的通用安全激活、普通 Actor 明确完成态抢跑拒绝、环境完成语气与命令式回应放行、软语义与 Choice 显示问题的单次 Actor 放行、玩家明确行动豁免、推荐项作者核心保留与服务层二次校验、故事身份和日常人格的称呼语域优先级、当前未完成 Beat 的玩家行动按钮投影、内部舞台指令不可公开、其他参与者结果控制拒绝、活动支线静态按钮隔离和旧静态 ID 二次拒绝、旧 Patch 缺少公开文案时安全隐藏、活动支线原子激活与续演、事实候选整体拒绝、服务端事实身份、预算与非推进关闭、静态/动态统一 Goal 完成、过时推荐/隐藏入口过滤、重复汇流 Patch 拒绝、Branch History 精确有限召回与私有身份隔离、Ending Domain Patch 可达性与终态端到端闭环、自然语言作者完成入口/用户退出、动态实体 Board 投影与私有事实隔离、动态 Choice 刷新稳定性与伪造/过期拒绝、Story revision 门禁、用户 Story 严格 ID 恢复、早期存档中性迁移、活动支线恢复修复与不可修复存档保留、显式重开门禁、旧文件不改写、三份正式 Story 卡列表、两份新增题材剧本的双结局静态可达性、二十四与二十五回合无模型 fallback 全路径、星球求生剧本专业短句与非主从语域守门、三份正式剧本的七成自由输入压力、脱敏职责/场景指标、通用真实模型长跑夹具合同、Chromium 页面和 PC 窗口契约。

Electron 实体进程验证已经使用同级 `N.E.K.O.-PC` 的真实 Electron binary 通过两条 smoke：同源小剧场子窗口正确获得关闭、最小化、最大化与最大化状态桥；真实 PC 主进程完成开演、子窗口刷新和服务端 Session 恢复。双显示器强制模式也已在两块在线且未镜像的实体显示器上执行：小剧场被放入副屏工作区，父窗口与小剧场依次重新聚焦后，窗口仍命中同一副屏且 bounds 完全不变。单屏 CI 不会把这条强制双屏断言静默记为通过。

World Contract、动态内容槽位、Narrative Goal、Ending Domain、Runtime Branch Patch、Branch Fact、Branch History 和确定性生命周期目前已经具备 schema、纯规则与自动化测试。v2.6 把通用自由意图收口为同节点有界线程：一次真实 idle 只进入休眠，连续第二次 idle 才清理，明确 `continue/refine` 可以恢复；复合输入的 `residual_intent` 只有在服务端证明摘录来自本轮规范化玩家原话后，才会随作者 Choice 原子提交保存为短期 `pending_intent`，并在目标节点按节点、Scene 与 revision 重验；下一轮明确确认同一语义时，该摘录才作为前一条玩家证据，Pending 自身不携带服务端意图身份。Router 技术故障不冒充真实 idle，意图线程不休眠，已验证 Pending 只在原 TTL 内保留一次重试机会。阶段三至阶段五的 Planner、Patch、活动支线、事实、Goal、Ending、Projector 和恢复边界保持不变；活动支线的技术降级可以提交安全文字 revision，但不推进叙事预算或权威事实，正常合法空事实回合仍按非推进语义计数。阶段六的真实模型和 Electron 结果保留为 v2.5 既有证据；阶段七新增完整事务、锁等待、固定执行面与幂等回放观测；阶段八以统一只读 Fact View 和作者事实投影消除静态条件与已完成动态 Goal 的读取分叉，但不合并事实存储；阶段九增加严格 `branch_handoff` 分类和 `intent_handoff` 生命周期，只在玩家明确结束旧支线并提出具体新行动时保留事实回锚，以现有 Pending 跨回合确认，既不静默吞掉新意图，也不创建嵌套 Patch；阶段十让当前玩家输入和 Planner 证据遵守完整预算，拒绝内部字段外显，以有界公开上下文改善安全回退，并建立机械项与人工项分离的跨题材叙事评测底座；阶段十一用可选作者 Catalog 为动态内容事实建立稳定成员证明，并提供零模型单文件诊断，同时明确把无 Catalog 的旧槽位保留为 `declarative_only` 兼容路径；阶段十二把 24 小时静默终结改成保留剧情与活动索引的可恢复 Session 休眠，成功新回合才唤醒，并为真正终止保存固定原因；阶段十三保护离线评测输入与报告写入；阶段十四把背景、Scene、作者对白、静态 Choice、结局和 Branch Fact 边界重新收回 Story Package 与确定性提交门。全部实现只消费用户 Story Package 声明的合同、目标、目录、投影、出口和结局域，不得写入针对当前示例剧本的关键词、节点或情节优化。

### 18.1 v2.5 最终收口结果

1. 已使用与内置剧本无关的固定评测 Story，按 `theater_router / theater_planner / theater_actor / theater_repair` 导出调用数、输入/输出 token、耗时与结果状态。
2. 已分开导出自由输入、支线入口、活动支线和普通推进场景的 P50/P95，并计算 Patch 拒绝率、Repair 率、回退率、合同越界率和支线终态失败率。
3. 最终五轮、`47` 次调用的初始验收样本已经记录于 12.4；API Key、完整 Prompt、玩家原话、模型全文和 Story 内容均不进入指标。线上长期样本仍需自然积累，不能把本批数据解释成长期分布。
4. `neko-theater-story-writer` Skill 仍需用户单独确认后才能同步，不属于 v2.5 代码完成门槛。

### 18.2 v2.6 首批收口结果

1. 完整小剧场 Python 回归为 `270 passed, 4 skipped`；新增确定性覆盖完整事务精确一次记录、锁等待边界、固定执行面、幂等回放、一次 idle 休眠与恢复、Pending 摘录来源校验与明确确认、Router 技术故障保留，以及活动支线技术降级与合法空事实的差异。
2. 新增观测只使用固定低基数输入、执行面和结果枚举；报告 schema 保持 `1` 并以新增聚合字段兼容扩展，不记录玩家原话、Prompt、Story、角色名或模型全文。
3. 本轮未修改 Story Package、前端、i18n、TTS 或 Electron 链路；未重新执行真实模型长跑和 Electron 实体窗口 smoke，四项显式真实模型测试保持跳过，不能用 v2.5 的既有结果替代 v2.6 新语义评测。
4. 本轮已更新项目开发文档和通用运行时代码，但没有同步本地 Codex `neko-theater-story-writer` Skill；Skill 更新仍需用户单独明确确认。

### 18.3 v2.6 统一 Fact View 收口结果

1. 完整小剧场 Python 回归为 `278 passed, 4 skipped`；相对阶段七新增 8 项确定性测试。
2. 统一视图只读取静态事实和已完成 Goal 的作者投影，不读取原始 Branch Fact；静态节点、传统 Ending、输出护栏与五个模型职责已经共享同一作者权威结果。
3. 本阶段保持 `SESSION_SCHEMA_VERSION = 1`，没有修改正式 Story Package、公开响应、Projector、前端、i18n、TTS 或 Electron 链路；正式 Story 若将来新增投影，应按既有门禁提升自身 `story_revision`。
4. 本阶段未调用真实模型，也未重跑 Electron 实体 smoke；四项显式真实模型测试保持跳过，不能把确定性 Fact View 结果解释为叙事质量验证。

### 18.4 v2.6 活动支线意图转交收口结果

1. 本轮实现已纳入定向与完整自动化回归：相关链 `177 passed`，完整小剧场 Python 回归 `293 passed, 4 skipped`；相对阶段八新增 15 项确定性用例。
2. 活动支线自由输入现在先经过独立 `branch_handoff` 轻量分类：严格确认进入 `intent_handoff`，置信度不低于 `0.65` 的明确继续才交给 Branch Actor，低置信、不确定或技术失败走无事实、无预算变化的降级。
3. 转交使用固定 History 退出码，完整保留旧 Branch Fact，回作者锚点并复用现有 `pending_intent` 保存新行动第一证据；同一回合不调用 Planner 或普通 Actor，而是使用不抢跑新行动的固定安全回应，下一轮明确确认后才允许激活新 Patch。
4. 本轮保持 `SESSION_SCHEMA_VERSION = 1`，未修改 API 输入/公开响应、前端、i18n、TTS、Electron、Story Package 或正式 Story 内容；按既定路线权重，v2.6 已采纳改造总体开发进度为 **65%**。
5. 本轮未执行真实模型或 Electron 实体 smoke；因此 `branch_handoff` 的自然语言准确率、误中断率和新增分类端到端延迟仍属于未验证范围，不能由确定性测试替代。

### 18.5 v2.6 上下文完整性与叙事评测收口结果

1. 完整小剧场 Python 回归为 `335 passed, 4 skipped`；新增覆盖当前输入与 Planner 证据的完整预算、两轮句尾保护、Scene Note/历史/旧意图整条拒绝、内部信息硬门禁、动态按钮与 Board 标签合同、入口 `entry_callback` 隔离、上下文化安全回退、保存后终态观测，以及叙事评测数据与评分协议。
2. 合成固定集包含三个无关题材、十六个案例、五个维度和五类失败码，并强制每个维度都覆盖三个题材；默认校准与报告脚本不调用模型或网络，生成报告不保存合成上下文、玩家输入、对白、旁白或按钮全文。机械项与人工项分别统计，人工未复核时固定为 `human_review_pending`，提交结构化人工结论后才可得到人工通过或失败。
3. 当前只能声明“评测底座和内置校准通过”，不能声明真实模型的五维叙事质量已经通过。本阶段未生成外部模型候选、未消耗真实模型额度，也未执行人工自然度审核；真实准确率、人格一致性、对白—按钮自然衔接和收束自然度仍待显式评测。
4. 本阶段保持 `SESSION_SCHEMA_VERSION = 1`，未修改 API 输入/公开响应、前端、i18n、TTS、Electron、Story Package 或正式 Story 内容；没有重新执行 Electron 实体 smoke，四项真实模型测试继续按显式开关跳过。
5. 按既定路线权重，v2.6 已采纳改造总体开发进度为 **80%**。剩余约 `10%` 是动态内容槽的可执行语义与作者工具，约 `10%` 是真实模型叙事质量、Session 休眠/归档和长期锁等待数据驱动的生命周期收口。

### 18.6 v2.6 动态内容 Catalog 与作者诊断收口结果

1. 原 `allowed_traits / forbidden_traits` 只能校验声明外形，不能证明模型生成的具体实体。阶段十一新增可选作者 Catalog，服务端从 Loader、Patch Fact Rule、Fact Candidate、Committed Fact 到 Session 恢复都按稳定 `(content_slot_id, content_id)` 查表，不使用模型自报 traits、标签关键词或题材词表。
2. Catalog 成员固定包含 `content_id / entity_kind / label / fact_object / traits`。内部引用、公开标签、每槽/全 Story 成员数、traits 和 Planner 目录投影均有 Loader 上限；全部正向 traits 必须满足、禁止 traits 不得命中。Fact 的 object、公开实体种类和标签必须精确等于作者成员，混合候选一项非法即整组拒绝；活动支线恢复还会对照原 Patch 防止同槽改绑，预算和既有事实不变化。
3. 新增显式单文件作者命令，稳定区分合法、JSON 错误、非对象根、Story 合同错误和工具 I/O 错误；控制台与可选报告只含 schema、固定原因码、JSON 行列和槽位合同摘要，不回显绝对路径、标题、背景、对白、callback、原始异常或 JSON 片段。默认路径不调用模型或网络，也不写仓库；显式输出也不能把报告文件或 `.json` 目录混入 Story 扫描面，路径与最终文件符号链接不能绕过该门禁。
4. 无 Catalog 的正式 Story 和旧 Session 本阶段保持兼容并由工具警告 `slot_traits_declarative_only`；这只保证没有功能回退，不等于现有五个正式槽位已经获得语义证明。正式 Story 的目录成员、公开标签和 revision 需要作者另行确认，框架不能自动猜配。
5. 完整小剧场 Python 回归为 `402 passed, 4 skipped`，其中 Catalog 合同 `39` 项、作者 CLI `20` 项。本阶段未执行真实模型、人工叙事复核或 Electron smoke，未修改正式 Story、API 公开结构、前端、i18n 或 TTS。按既定路线权重，总体进度为 **88%**；剩余约 `2%` 为正式 Story Catalog 迁移与严格槽位真实演绎验证，另约 `10%` 为真实模型质量和数据驱动生命周期收口。

### 18.7 v2.6 可恢复 Session 休眠收口结果

1. 24 小时机会性扫描现在只写 `dormant_at`，不再写 `ended_at`、覆盖 `updated_at`、改变剧情 phase/revision/事实/Choice 或清除活动索引；重复扫描幂等，现有 `/session/state` 与 `/session/active` 均可恢复原快照。扫描先做只读恢复预检，不兼容文件原样保留；坏 `updated_at` 只隔离当前 Session，不阻断同批合法扫描。
2. 公开响应新增固定 `session_lifecycle`，前端与八个 locale 已接通休眠提示。成功回合通过候选副本原子清除休眠；失败、冲突、只读恢复和幂等回放均不唤醒。真实 Session 锁争用测试证明扫描不会覆盖先提交的新 revision；`session_guard -> character_guard` 与锁内重读进一步保证新开场替换不会被扫描旧副本复活。
3. 新结束存档保存固定 `end_reason`，覆盖作者完成、支线结局、玩家离场、替换、角色切换、管理关闭和开场发布失败；休眠不写结束原因。`dormant_at / ended_at` 必须是非布尔正整数，原因必须是固定字符串并伴随合法结束时间；旧结束存档缺原因仍兼容，坏类型或孤儿原因保留原文件并返回明确状态错误。
4. 三份正式 Story 的五个动态槽位经只读审计均没有足够成员级作者证据，框架未根据现有道具、对白或假设性文档示例猜配 Catalog，也未提升任何正式 Story revision。
5. 完整小剧场 Python 回归为 `416 passed, 4 skipped`，其中包含真实 Chromium 的休眠快照恢复与动态 i18n 状态竞争回归。本阶段未调用真实模型、未执行人工叙事审核或 Electron smoke；总体进度为 **92%**。剩余 `8%` 由正式 Story Catalog 作者决策、显式真实模型候选与人工复核、以及只能依赖线上长期样本判断的锁生命周期优化构成。
6. 产品侧已把“24 小时自动休眠”标为候选删除项：当前实现继续作为代码事实保留，但在最终决策前不再扩展；删除时必须无损兼容已有休眠 Session，且不能连带移除正常恢复与固定结束原因。

### 18.8 v2.6 叙事评测报告输出安全收口结果

1. `--output` 现在于读取前同时对照 `--dataset` 和可选 `--observations`，拒绝直接同路径、父目录跳转后的等价路径、符号链接和硬链接；比较失败按冲突关闭，输入文件和链接身份保持不变。
2. 报告改用项目统一的同目录原子写入。替换提交失败时旧报告、dataset 和 observations 均保持原字节，临时文件被清理；合法校准和 candidate 报告继续保留尾换行与既有脱敏 schema。
3. 输入 JSON、schema、编码、读取、输出写入和未预期内部错误均映射到稳定小写原因码与工具退出码 `2`，不回显绝对路径、候选正文、异常类型、消息或 traceback；校准/候选通过 `0` 和质量门禁失败 `1` 的语义未改变。
4. 叙事评测定向回归为 `36 passed`，与作者单文件工具交叉回归为 `56 passed`，完整小剧场 Python 回归为 `432 passed, 4 skipped`。本阶段保持零模型、零网络，不修改正式 Story 或运行时产品链；总体进度仍为 **92%**，剩余门禁仍是作者 Catalog 决策、显式真实模型与人工复核、以及线上长期锁数据。

### 18.9 v2.6 Story 作者真源收口结果

1. 选择页协议已从“整份 Story 的公开子集”收窄为独立安全投影：稳定背景只来自 `background`，只附带结构化角色/目标和作者初始 Scene；`summary`、未来 Scene、`restrictions`、`runtime_guardrails`、`seed`、图节点与边均不进入浏览器。故事列表不再触发 24 小时休眠扫描。
2. 三份正式 Story 已移除重复 `scenario_card.brief / rules` 并清理公开背景中的生成规则；Loader、Graph、Ending 和 Runtime 不再合成 Choice ID/模式/文案/callback、缺失 Scene、隐式结局或错误 Story 回退。开场与静态节点对白保持作者原文，静态 Choice 的显示和路由合同完全由作者控制。
3. Branch Fact 提交门补齐 Prompt 之外的作者事实保护：命中 `seed.forbidden_assumptions` 或以相同主语/谓词改写不可变事实时，整组候选被拒绝，合法 `fact_role` 不能绕过该门禁完成 Goal。
4. 三份正式 Story 的 `background` 已改写为单段电影式剧情简介，按 `len(background.strip())` 计算为 `357 / 398 / 448` 个 Unicode 字符；内容只重组既有世界、双主角处境、触发事件、升级压力、方法冲突与未决悬念，不增加 `synopsis`，不使用 `summary` 或 `scenario_card.brief`，不泄露关键反转或结局，也不混入系统语言。Story 结构和可恢复剧情合同未变，`story_revision` 保持不变；对应公开简介检查已同步到个人 Skill `neko-theater-story-writer`。
5. 本轮电影式简介改写后，Story 与 Chromium 定向回归为 `67 passed, 22 warnings`，完整小剧场 Python/Chromium 回归为 `453 passed, 4 skipped, 25 warnings`；warning 均为既有依赖或框架弃用提示。三份正式 Story 的 JSON 与单文件校验均通过，五个无 Catalog 槽只保留预期的 `slot_traits_declarative_only` 兼容警告。个人 Skill `neko-theater-story-writer` 通过 `quick_validate.py`，使用与修改前相同且未透露目标长度的三条隔离提示后，输出长度由 `350 / 399 / 288` 变为 `380 / 400 / 398`，三种题材都主动进入正式作者范围。
6. 本轮没有调用真实模型、执行人工叙事复核或重跑 Electron 实体窗口；四项真实模型测试继续按显式开关跳过。正式 Story Catalog、真实模型候选与人工自然度、线上长期锁数据，以及旧 Story `visibility`/技术降级展示的产品兼容决策仍未被确定性测试替代。
7. 按当前剩余门禁，v2.6 总体开发进度为 **95%**。其中作者真源和公开投影代码主链已完成；剩余约 `2%` 是正式 Story Catalog 与严格动态内容演绎，约 `2%` 是显式真实模型候选和人工复核，约 `1%` 是长期数据及上述兼容产品决策。

### 18.10 v2.6 Session 私有模型返回记录收口结果

1. 新 Session 会初始化 `llm_return_records=[]`；旧当前 schema Session 仅在下一次成功提交时补齐。每条记录保存一次真实模型传输的职责、执行面、状态、模型/供应商、原始返回、异常类型与时间，提交时再绑定 Session、客户端回合和前后 revision。
2. 统一 `_invoke_model_once` 是唯一正文采集入口，覆盖 Router、Planner、Actor 和 Repair。被解析器拒绝、被护栏替换或触发 Repair 的成功返回不会丢失；超时或异常不虚构正文，只保留固定状态和异常类型。
3. 记录与成功候选原子保存，revision 冲突、角色切换、过时 Session 或业务拒绝均不会写入；幂等重放不重新调用模型也不重复记账。ContextVar 隔离保证并发回合不会共享采集列表。
4. Prompt、玩家请求载荷、API Key、Base URL 和异常消息不进入记录；Projector、公开快照、HTTP、TTS、前端和聚合指标也不读取它。模型原始返回本身可能包含玩家内容，因此该 Session 字段按本地敏感诊断数据管理，不授权自动上传或对外导出。
5. 定向回归为 `174 passed`，完整小剧场 Python/Chromium 回归为 `457 passed, 4 skipped, 25 warnings`。本阶段没有真实模型调用或 Electron smoke，也不声称解决叙事自然度；总体开发进度仍为 **95%**。
