# 小剧场 NSN v2.3 开发文档：可编译、可验证的剧场演绎

> 历史说明：本文记录瘦身前的 v2.3 设计与开发过程。当前运行架构已经改为单猫娘轻量主链，Overlay、随机事件、复杂实体、Evidence、GM Anchor 和多阶段模型编排均已删除。当前事实请以 [`neko-theater-v2.3-implementation-architecture.md`](./neko-theater-v2.3-implementation-architecture.md) 和 [`neko-theater-slimming-proposal.md`](./neko-theater-slimming-proposal.md) 为准。

## 开发进度

- 文档状态：v2.3 产品方向已经建立；Story Compiler、Condition / Evidence / Entity Engine、Scenario Projector、结构化 Turn Request、稳定 Choice、作者 Choice callback、单调 state revision、候选 session State Preview、完整事实边界、双层 Validator、失败 Trace、响应幂等回放、玩家/Runtime 随机事件、Runtime Graph Overlay、确定性 Progress Resolver、角色互动、歧义确认回合、GM Anchor 冷却轮换、公开恢复快照和跨链路记忆确认保护，以及第一版受约束模型候选抽取已进入可测试实现。现有小剧场前端已消费结构化 Choice、自由输入、主动随机事件、结构化离场、Scenario Board、Public Trace 和 session 恢复协议；星灯祭真实主线演出已经收口，丰富多对象动态生成仍待后续阶段评估。
- 代码状态：v2.3 后端使用 `free_input -> graph/random_event/dynamic_branch/bridge/roleplay_response/clarification/redirect` 统一事务链；当前按钮的自然语言等价表达可命中同一图节点，同时命中多个可执行节点时会保留按钮并请玩家确认，无关键状态变化的场景内表达则交由 Persona 回应。只有硬边界违反才进入拉回，并按剧本 Anchor 条件、优先级、冷却和历史轮换世界内提示。前端会优先按本地 session 指针恢复，缺失时回查服务端 active session；瞬时网络错误复用完全相同的请求体重试一次，revision 冲突只刷新公开状态，不会重放旧动作。剧情回合与落幕后记忆确认共享同一 session 串行锁，并发双击“记住”只会向普通记忆服务写入一次。星灯祭图外分支已补当前开场可执行的安全桥接目标，不再生成线索后把玩家留在空 Choice 动态节点。
- 剧本状态：《星灯祭的糖霜危机》已切换为首条真实 v2.3 剧本，显式声明 9 个 feature，并完成 4 个稳定 question 引用、6 个 stage entity、10 条 entity transition、42 个唯一 choice id 与 42 条独立即时 callback、3 个玩家或 Runtime 可触发随机事件、包含开场安全返回点的图外改写边界、2 条 Evidence 条件树、4 个对象化 GM 拉回锚点、terminal ending id 和 primary completion role。糖霜暴走会先回团子摊开放桂花糖再使用，不再跳过道具生命周期；正式落幕明确回收六件道具和第一炉开张结果。
- 最近验证：全部本地小剧场自动化 `317 passed, 4 skipped`，其中 Chromium Playwright `7 passed`；最新真实演绎记录复现的空 Choice 已增加独立回归，覆盖行动/对白分区、空结构化数组兼容显示，以及先查糖霜锅后回补配方卡。OpenAI-compatible 协议纵向测试真实经过 SDK、HTTP `/v1/chat/completions`、响应解析、动态候选和语义 Validator。4 项真实模型 smoke 只因未设置显式发行候选模型环境而跳过。真实 Electron 进程 smoke `2 passed`；普通聊天、游戏、语音和记忆针对性回归 `393 passed, 3 skipped`，跳过项均因未配置 Qwen API key。Full/Economy 均覆盖真实星灯祭 20 轮主线、图外纸风车分支安全返回，以及按钮/自然语言等价输入的独立 callback；真实浏览器覆盖星灯祭 Economy 21 次提交、六道具 Board、玩家专属河风事件和正式结局。真实 PC 主进程验证子窗口恢复与主宠物窗口隔离，并发记忆确认只写一次普通记忆。v2.1/v2.2 与内置 MVP 存档形状保持不变。
- 当前完成度：互动小说流程整改第一至十一轮均为 100%；v2.2 通用后端约 99%，示例剧本适配约 99%，前端 v2.3 消费约 95%；v2.3 产品方向约 99%，架构设计约 99%，协议冻结约 100%，v2.3 整体实现约 98%。完成度描述代码与自动化覆盖，不替代真实玩家对演绎质量的验收。

## 版本关系

v2.3 不是另起一套小剧场，也不是把 v2.2 推翻重做。三代框架的职责关系如下：

```text
v2.1：叙事内核
  图节点、可达边、Director Router、Preview / Commit / Rollback、Validator、Ending、Memory Boundary

v2.2：剧场玩法语义
  剧本卡、剧本动作、舞台道具、线索、疑点、剧情凭据、GM 拉回、Scenario Board、Scenario Trace

v2.3：工程化收口
  Story Compiler、结构化 Turn Request、稳定选择 ID、实体状态转换、随机复现、Runtime Graph Overlay、统一回合事务、双层 Trace、多结局解析
```

核心原则：v2.1 决定“剧情能走到哪里”，v2.2 解释“玩家正在做什么、发现了什么”，v2.3 保证“这套规则能被校验、稳定运行并扩展到更多剧本”。

## v2.2 已完成记录

以下能力于 2026-07-09 完成并已进入当前工作区：

- 完成 v2.2 第一轮“剧本卡地基”。玩家公开信息新增身份、目标、疑点、舞台道具和 GM 拉回方式；运行时私有信息新增线索 `hidden_meaning` 与剧情凭据规则，但不会出现在公开故事列表和开场卡中。
- 完成 v2.2 第二轮“剧情账本”。`story_state.scenario_state` 会记录可用道具、已使用道具、已发现线索、已确认剧情凭据、未解决疑点和 GM 拉回次数；每个 assistant turn 会落盘 `scenario_trace`，区分调查、使用道具、发现线索、确认凭据、GM 拉回和没有推进。
- 完成 v2.2 第三轮“Choice Policy 收口”。图路由会优先推荐当前可用道具对应的调查/询问动作，过滤已使用且不会产生新线索的重复道具动作；《星灯祭的糖霜危机》主线关键节点已补 `script_action`，糖霜暴走分支也会作为救场线索来源进入同一套剧情账本。
- 完成 v2.2 第四轮“Persona 防剧透”。Runtime 会给 Persona 传入只包含公开道具、已发现线索 public text 和已确认剧情凭据标题的 `scenario_visible_context`；Persona prompt 不接收 `hidden_meaning`，且当本轮明确禁止隐藏步骤/剧透时，会拒绝答案播报式输出并回退。
- 完成 v2.2 第五轮“Ending Evidence 凭据化”。Ending Engine 继续兼容旧版 committed `narrative_facts`，同时支持 `ending_attractors.evidence_required` 直接引用 v2.2 `confirmed_evidence_ids`；《星灯祭的糖霜危机》正式结局改由 `evidence_glow_timing_ready` 和 `evidence_first_batch_ready` 闭环触发。
- 完成 v2.2 第六轮“结局证据不足 GM 控场”。玩家提前要求收尾但剧情凭据没齐时，economy 模式不会推进节点或关闭 session，`scenario_trace.blocked_reason` 会记录为 `ending_evidence_required`，用于区分普通跑题拉回和结局凭据不足拉回。
- 完成 v2.2 第七轮“公开剧本板”。`/api/theater/session/state` 会返回安全的 `scenario_board`，包含当前可用道具、已使用道具、已发现线索 public text、已确认剧情凭据标题、未解决/已解决疑点和 GM 拉回摘要；仍不暴露 `hidden_meaning`、私有 `story_state` 或 `evidence_rules` 原文。
- 完成 v2.2 第八轮“剧本板随响应同步”。`start_session` 与 `submit_input` 响应也会返回同一套安全 `scenario_board`，前端每轮可直接刷新道具板和线索板，不需要额外请求状态接口。
- 完成 v2.2 第九轮“剧本板随 turn 快照落盘”。assistant turn 会持久化当轮安全 `scenario_board`，复盘时可以看到玩家当时实际看见的道具、线索、凭据和疑点板；快照仍不包含 hidden meaning。
- 完成 v2.2 第十轮“剧情凭据使用闭环”。正式 `story_ending` 达成时，runtime 会把该结局实际依赖的 v2.2 `required_evidence_ids` 写入 `scenario_state.used_evidence_ids`，公开剧本板只显示对应凭据标题，最后一轮 `scenario_trace.used_evidence_ids` 也会记录本次收束消费的凭据，方便复盘结局由哪些剧情凭据托住。
- 完成 v2.2 第十一轮“公开按钮动作分类”。`suggestion_options` 会安全暴露 `action_type`，前端可以区分调查、询问、使用道具、行动、自由输入和离场等按钮；系统补充离场和作者写在剧本里的软离场都会归为 `leave`，`target_id`、线索 id 和内部剧本动作对象仍只留在服务端，不进入公开响应。
- 完成 v2.2 第十二轮“示例剧本动作覆盖”。《星灯祭的糖霜危机》主线新增 `organize`、`compare`、`deduce` 动作节点，完整主线公开按钮已能覆盖调查、询问、使用道具、整理、对照、推理、行动和离场；整理/对照/推理只声明前置线索，不伪装成重复使用道具。
- 完成 v2.2 第十三轮“主线全程动作分类”。《星灯祭的糖霜危机》除开场 seed 和边界救场外，所有主线核心/结局节点都已声明 `script_action.action_type`；完整主线跑到正式结局时不会因新增动作前置而卡住。
- 完成 v2.2 第十四轮“公开已使用道具”。`scenario_board` 新增安全 `used_props`，`start_session`、`submit_input`、state 接口和 turn 快照都会显示玩家已经使用过的舞台道具；Persona 的 `scenario_visible_context` 也会接收同一套安全已使用道具摘要，只暴露道具公开名称和提示，不暴露线索 hidden meaning。
- 完成 v2.2 第十五轮“Trace 中文动作名”。`scenario_trace` 新增 `action_label`，正式推进会显示“调查/询问/使用道具/整理/对照/推理/行动”，GM 拉回会显示“GM 拉回”，方便复盘直接看懂本轮发生了什么。
- 完成 v2.2 第十六轮“剧本板中文上一轮动作”。`scenario_board` 和 Persona 的 `scenario_visible_context` 新增 `last_action_label`，角色提示优先使用“调查”等中文动作名，不再把 `investigate` 这类内部枚举写进 Persona prompt。
- 完成 v2.2 第十七轮“拉回原因中文化”。`scenario_trace` 新增 `blocked_label`，`scenario_board` 新增 `last_redirect_label`；自由输入拉回会显示“请回到剧本选项”，结局凭据不足会显示“结局凭据不足”，内部 reason 枚举仍保留给程序判断。
- 完成 v2.2 第十八轮“Persona 拉回原因可见”。Persona prompt 会消费安全的 `last_redirect_label`，让猫娘知道本轮是“请回到剧本选项”还是“结局凭据不足”；`scripted_choice_required` 等内部 reason 枚举不会进入角色提示词。
- 完成 v2.2 第十九轮“Trace 复盘摘要”。`scenario_trace` 新增 `summary`，推进回合会优先记录“调查：半张配方卡”这类公开名称，GM 拉回会记录“GM 拉回：请回到剧本选项/结局凭据不足”；前端复盘后续可直接展示中文短句，不需要拼内部枚举。
- 完成 v2.2 第二十轮“Trace 随响应同步”。`submit_input` 响应会直接返回本轮安全 `scenario_trace`，前端每轮都能拿到动作、摘要、发现线索、确认凭据和 GM 拉回原因，不需要读取私有 session 日志。
- 完成 v2.2 第二十一轮“Trace 公开名称同步”。`scenario_trace` 在保留 `used_prop_ids / revealed_clue_ids / confirmed_evidence_ids` 给程序定位的同时，新增 `used_prop_labels / revealed_clue_titles / confirmed_evidence_titles`，前端可直接展示“半张配方卡”“三次铃声”等公开名称，不需要读取隐藏线索。
- 完成 v2.2 第二十二轮“结局凭据标题同步”。正式 `story_ending` 消费剧情凭据时，`scenario_trace` 会同时写入 `used_evidence_ids` 和公开 `used_evidence_titles`，复盘可以直接显示结局由哪些剧情凭据托住，但不暴露 `evidence_rules` 的内部规则。

## 文档定位

本文档定义小剧场 v2.3 的下一阶段目标：在 v2.2“类剧本杀式剧场演绎”已经建立的基础上，把 NSN 收口成 **可编译、可验证、可审计、可扩展** 的通用剧场框架。

这里的“剧本杀”不是指必须做凶案、事故调查、恐怖、复杂身份对抗，也不是把 N.E.K.O 变成传统多人桌游。它指的是一种产品体验：

1. 玩家进入一个封闭故事场。
2. 玩家先看到清楚的剧本卡、身份、目标、舞台道具和当前可推进方向。
3. 玩家通过调查、询问、使用道具、对照信息、提出推理或执行行动推动剧情。
4. GM 充当旁白和主持人，负责描述场面、提示可用道具、把跑飞话题带回剧本。
5. 猫娘是共演角色，会反应、误判、补充现场细节，但不会越过当前剧本锚点直接剧透。
6. 正式结局由剧情凭据闭环触发，不由好感度、轮数或随意生成决定。

v2.3 不改变剧场题材边界，但允许玩家自由触发随机关键事件和图外剧情改写。核心任务是让预写图、随机事件和动态扩写都能被 NSN 接住，并让“剧本卡、道具、线索、疑点、剧情凭据、GM 控场、收束”具备稳定协议、完整校验和统一运行语义。

## 当前代码基础

当前小剧场已经具备 v2.2 可以复用的基础：

1. `runtime.py`
   - 已支持 `full` 和 `economy` 两种演绎模式。
   - `economy` 对 NSN 图故事已改成 Galgame 式按剧本按钮推进。
   - `economy` 下剧情走向不再经过 Anchor / Director / Narrator / Validator 推导，但猫娘对白仍通过 Persona 转述。
2. `graph_router.py`
   - 已能从当前 active node 找可达目标节点。
   - 已支持多个公开按钮映射同一目标节点。
   - 已支持点击清洗后的公开文案反查 NSN edge signal。
3. `state_manager.py`
   - 已有 `story_state`、`active_node_id`、`completed_node_ids`、`blocked_node_ids`、`state_preview`、`rollback_logs`、`narrative_facts`。
   - 已支持 preview / commit / rollback 的最小闭环。
4. `ending_engine.py`
   - 已区分 `story_ending` 和 `user_exit`。
   - `story_ending` 已依赖 committed `narrative_facts` 中的 evidence。
5. `suggestion_engine.py`
   - 已能生成结构化 `suggestion_options`。
   - `economy` 图剧本下已关闭自由输入提示，让玩家主要按剧本按钮推进。
6. `story_loader.py`
   - 已校验 NSN v2 的 `seed / narrative_nodes / edges / ending_attractors / suggestion_policy`。
   - 当前公开故事列表仍主要暴露 `id / title / summary / scenes`。

v2.3 当前待收口问题：

1. 星灯祭已通过 Full/Economy 后端主线、图外动态分支安全返回和 Economy 真实 Chromium 六道具/玩家事件验收；真实模型驱动的图外抽取仍受本机未配置模型 API 限制。
2. 星灯祭的糖霜暴走汇合、42 个即时 callback 和正式结局文案已经收口；后续内容工作只剩更丰富的非主线场面差异与真实模型端到端抽取验收。
3. Persona 已按字段白名单隔离公开事实；Level 2 Validator 已能比较隐藏事实与演绎文本的语义等价泄露，并在失败时回滚且不回显私密正文。当前证据来自受控模型，真实供应商模型仍需在具备 API 的发行候选环境验收。
4. 当前 session 锁只覆盖单进程；跨进程仍依赖 revision 乐观冲突保护，尚未实现跨进程锁。
5. 浏览器内存档恢复、同请求体网络重试、revision 冲突刷新、Electron 真实进程恢复，以及普通聊天、语音、小游戏和角色记忆的针对性跨链路回归已经完成；打包安装后的真实模型、语音和用户数据环境仍不在当前自动化证据内。
6. 第一版模型候选只支持一个新对象和一条明确观察；多对象、多步骤 Overlay Plan 留待核心纵向链稳定后评估。

## 代码规模与弃用治理

1. `runtime.py` 抽离公开投影后曾从 1822 行降到 1678 行，接入 Turn Request、事务桥接和玩家主动随机事件后回涨到 1920 行。请求、随机事件和 Economy 图回合迁入单一 `turn_coordinator.py` 后降到 1393 行，合并公共 Finalizer 后为 1336 行，渲染重路由与 GM-Lite 收口后为 1195 行；接入 revision、自动作者事件、Overlay、自由输入分流、受约束候选、角色互动、歧义确认、公开 session 恢复、串行记忆确认、Full callback 和语义泄露终止回滚后当前为 1567 行。
2. 当前小剧场生产代码共 12674 行。`turn_coordinator.py` 当前为 1647 行，统一负责动态候选、角色互动、歧义确认、Choice callback 与事实边界编排；`progress_resolver.py` 为 691 行，负责确定性优先级、当前选项单一/多候选语义命中、模型候选白名单转换与服务端 Overlay Plan 组装；674 行 `graph_router.py` 只在服务端确认 Choice 后返回 callback，不把它提前放进按钮协议。138 行 `redirect_engine.py` 负责 GM Anchor 条件、优先级、冷却和持久化轮换；117 行 `turn_transaction.py` 为剧情回合和记忆确认提供同一 session guard。`runtime.py` 只恢复公开快照，904 行 `theater.js` 负责恢复、同请求体重试和冲突刷新。472 行 `validator_engine.py` 复核 callback 合并后的完整演绎文本，并只在关键 core/ending 回合把限量私密事实交给 Level 2 进行语义比较；模型不直接生成节点、ID、状态增量、Evidence 或 Ending。
3. 新能力不能以永久双写方式接入。兼容期可以按 `scenario_protocol` 分流，但新路径通过真实回归后，必须删除对应旧参数、临时状态和重复提交逻辑。
4. v2.1 / v2.2 当前仍承载已有剧本和历史 session，不视为可直接删除的弃用功能；只有代码引用、测试和存档迁移边界都确认不再需要时才能移除。
5. 已删除 `event_pool` 遗留的 `story` 参数、空事件初始化包装、恒等事件刷新和只用于证明旧池被忽略的测试 fixture；保留仍在使用的 Director 事件提交、图暂停和 scene 轨迹。
6. 已删除通过 `story_state["_scenario_evidence_rules"]` 临时注入规则的过渡方案，改为单一 Evidence Evaluator 直接读取 Story 与 committed state。
7. 已删除 Runtime 内旧的请求事务闭包、随机事件整轮实现、Economy 图回合、玩家 turn 构造、脚本 Persona 上下文和对应辅助函数；调用点统一进入 Turn Coordinator，不保留转发壳或双写实现。
8. 已删除 Runtime 与 State Manager 各自维护的动作名、拉回原因、公开名称和剧本板投影函数，统一由 Scenario Projector 负责；v2.3 question ID、stage entity 和 GM anchor 对象不会直接泄露给玩家。
9. 已删除 Full 与脚本路径各自维护的 Board、Trace、Suggestions、结局凭据、记忆候选、turn 落盘和公开响应组装；统一 `_finalize_turn` 每轮只投影一次 Scenario Board，并让响应快照与 assistant turn 快照读取同一对象。
10. 已删除 Full Validator 首次渲染与一次重路由之间重复的 Persona / Ending / Narrator / Validator 调用块，改为最多两次的显式循环；持续拒绝时固定校验两次后安全降级，不会形成无界重试。

## v2.3 产品目标

v2.3 的目标不是增加更多系统，而是让玩家每一轮都能理解“为什么能做、做完发生了什么、下一步为什么出现”。

任意 v2.3 剧本的玩家体验应变成：

```text
进入小剧场
→ 阅读剧本卡
→ 知道自己是谁、目标是什么、现场有哪些道具和剧情锚点
→ 选择剧本动作，或用自由输入提出图外行动
→ 预写图推进、随机关键事件或动态分支被触发
→ 获得线索、改变局内事实或触发场面变化
→ GM 旁白控场，猫娘参与共演
→ 可兼容的图外改写被吸收进本局，无法兼容的输入由 GM 桥接或拉回
→ 形成已确认事实
→ 触发阶段推进
→ 用足够剧情凭据完成正式结局
```

### 互动小说回合语义修订

2026-07-10 真实演出审查确认：当前 `free_input` 仍过度偏向“可提交状态变化的动作解析”。玩家在场景内询问猫娘、表达紧张、补充态度，或用不同说法描述当前可见按钮时，都可能被错误归为 `redirect`，并在玩家界面显示“GM 拉回：请回到剧本选项”。这不符合互动小说目标，必须把“角色互动”和“关键剧情推进”拆开。

v2.3 从本轮起冻结以下玩家回合优先级：

```text
当前 Choice 的精确或语义同义表达，以及 Event 的当前公开表达
→ graph_progress / random_event

场景内对话、情绪表达、轻微动作或对角色的追问
→ roleplay_response
→ 角色必须回应，但不提交关键线索、剧情凭据或正式结局

场景内可验证的图外行动
→ dynamic_branch

行动意图成立但暂时无法接入主图
→ bridge

输入存在两个以上合理解释
→ clarification
→ 先向玩家确认理解，不把歧义显示成跑题

明确违反玩家身份、当前世界事实或正式结局硬边界
→ redirect
```

产品规则：

1. `roleplay_response` 是成功完成的角色互动回合，不是失败、阻断或无效输入；它递增请求 revision，但不递增剧情推进、随机冷却或 Evidence 时间轴。
2. `clarification` 是成功完成的歧义确认回合；只有原话同时命中两个以上当前可执行 Choice 时才进入，其它无关键变化输入继续使用 `roleplay_response`。
3. 自由输入和按钮必须读取同一组当前 Choice、实体可见性和动作能力；如果界面提供某个动作，玩家用自然语言表达同一动作也必须可达。
4. 模型不可用、超时、拒绝或输出格式错误属于引擎理解失败，只进入 Internal Trace；玩家界面仍获得安全角色回应和当前可选方向。
5. Public Trace 不显示 `blocked / rolled_back / Validator` 等内部术语。真正需要控场时也优先展示世界内反应，不把“GM 拉回”作为角色对白或惩罚标签。
6. 成功的 `graph_progress / random_event / dynamic_branch / bridge / roleplay_response / clarification` 必须清除上一轮可见拉回原因和 Anchor 文案，避免旧错误污染后续剧本板。
7. `roleplay_response` 必须通过现有 Persona 公开事实边界和输出校验；它可以回应已知事实，但不能新增线索、解决疑点、确认 Evidence 或替玩家行动。

第一轮必须通过以下玩家脚本：

1. “我有点紧张，你第一次摆摊也会紧张吗？”返回 `roleplay_response + committed`，猫娘直接回应，不重复开场旁白，不出现 GM 拉回。
2. 当前存在“凑近看看锅里那团发光糖霜”按钮时，输入“我想看看发光糖霜锅”应命中同一预写节点。
3. 玩家明确要求“直接宣布通关”时仍进入 `redirect + blocked`，且不提交 Overlay、线索或 Evidence。
4. 拉回后完成一次合法互动或推进，剧本板不得继续显示上一轮拉回原因。
5. 页面运行状态使用本地化“演出中/已结束”，不得直接展示 `setup / escalation / convergence / ending` 内部阶段枚举。

#### 第二轮：歧义确认与 GM Anchor 控场

第二轮将 `clarification` 从预留枚举落成可恢复回合，并让已编译的 `redirect_anchors` 真正参与 Runtime。

`clarification` 只能在以下条件同时成立时使用：

1. 玩家原话明确命中至少两个当前公开实体或两种当前允许动作。
2. 这些命中项对应至少两个不同的当前可执行 Choice 节点。
3. 候选来自 committed graph 和当前公开实体，不调用模型创造解释。
4. 精确 Choice label 命中仍直接推进，不因其它相似候选被强制确认。

`clarification` 是 `committed` 的合法交互回合：它递增 `state_revision`，保留当前可点 Choice，但不推进 active node、不重播旁白、不消费随机冷却、不发现线索也不确认 Evidence。重启或重试后，玩家仍可使用新 revision 点击原候选继续。

GM Anchor 选择规则：

1. 只有 `redirect` 能选择 Anchor；`roleplay_response / clarification / validator rollback` 不能消费 Anchor。
2. Runtime 先用统一 Condition Evaluator 检查 `available_when`，再按 `priority` 从高到低、作者声明顺序选择。
3. 已使用 Anchor 按 `cooldown_turns` 进入冷却；冷却以已成功剧情推进回合序号计算，blocked、回滚和重试不推进时间轴。
4. 多次连续拉回导致所有可用 Anchor 均在冷却时，Runtime 选择最久未使用的已满足条件 Anchor，不回退到永远重复同一句。
5. 没有任何 Anchor 满足条件时，才使用原有通用拉回文案。
6. 私有状态记录最后 Anchor ID、公开文案、使用顺序和冷却；Public Board / Trace 只展示当轮公开 Anchor 文案。成功回合清除可见旧 Anchor，但保留冷却历史。

第二轮必须通过以下玩家脚本：

1. 开场输入“我想看看半张配方卡和发光糖霜锅”返回 `clarification + committed`，两个原 Choice 仍可点，剧情不推进。
2. 确认回合之后重载 session，使用新 revision 点击其中一个 Choice 可正常推进，不重复提交确认回合。
3. 糖霜锅节点已完成后明确越过剧情凭据，首次拉回展示“锅里的糖霜正在冒泡发光”。
4. 紧接着再次越界时换用其它已满足条件的 Anchor，不重复首句；重启后冷却与选择历史仍保留。

#### 第三轮：刷新恢复与幂等网络重试

第三轮解决“后端已经持久化，但页面刷新后不知道如何继续”的产品断层。恢复不是重放私有日志，而是从服务端最后一份 committed 公开快照重建可交互界面。

服务端恢复规则：

1. 增加当前角色 active theater session 查询；返回值只包含公开 session 快照，不暴露私有 Turn Plan、Story State、Anchor ID 冷却映射或隐藏线索。
2. 恢复快照包含当前 scene、performance mode、`state_revision`、最后一轮公开 narration/dialogue、Scenario Board / Trace、Choice、玩家事件、Ending 和可选记忆状态。
3. Choice / Trace 优先回放 assistant turn 中已持久化的公开快照，不用未来状态重新生成过去按钮。
4. 过期、被新 session 顶替或已强制结束且没有公开 Ending 的 session 不得恢复输入。如果结局回合已提交但响应丢失，恢复页仍可展示落幕与待确认记忆。

前端重试规则：

1. 新玩家操作先且只生成一次 `client_turn_id` 和请求 body；断网、连接中断或短暂 `502 / 503 / 504` 最多自动重试一次，且必须复用原 body。
2. 首次请求如果已在服务端提交但响应丢失，第二次由 `turn_results_by_client_id` 精确回放；页面只显示一条玩家输入和一条演出结果。
3. `state_revision_conflict + retryable=true` 不自动重提交旧选择。前端先获取最新公开快照、恢复玩家尚未成功提交的自由文本，再请玩家基于新 Choice 重新确认。
4. 页面刷新或 Electron 子窗口重建时，优先恢复本地记录的 session ID；本地记录缺失时可从服务端 active 索引找回当前角色 session。
5. 成功落幕、玩家离场、session 过期或 stale 时清理本地恢复指针，不让新故事被旧窗口误锁。

第三轮必须通过以下玩家脚本：

1. 进行一轮 `clarification` 或剧情推进后刷新页面，Board、Trace、最后角色回应、Choice 和 revision 与刷新前一致，输入框可继续使用。
2. 模拟首次网络响应丢失、第二次回放成功；两次请求 body 完全一致，界面只出现一条玩家回合。
3. 模拟 revision 冲突；页面刷新到服务端新 revision 和新 Choice，不自动执行旧 Choice。
4. 清除浏览器本地 session 记录后重建小剧场窗口，仍能通过服务端 active 索引恢复当前演出。

#### 第四轮：真实 Electron 进程恢复验收

第四轮验证第三轮恢复协议确实能穿过桌面壳，而不只在普通 Chromium 页面中成立。测试使用真实 N.E.K.O.-PC 主进程、真实 Electron 二进制、隔离 userData、正式 `theater.html` 和正式 `theater.js`；后端使用可计数的隔离协议服务器，避免模型与用户本地状态影响恢复断言。

桌面验收规则：

1. 由 PC 真实主进程从宠物页通过 `_blank` 打开小剧场子窗口，不能绕开正式 child-window handler 或 preload。
2. 首次加载由真实 `theater.js` 启动 session，并把唯一 session ID 写入当前 Electron storage partition。
3. 使用真实 `webContents.reload()` 刷新子窗口；刷新后必须调用公开 state 接口恢复演出，不能再次调用 start 接口。
4. 恢复页必须重新显示公开 scene、角色对白、Board、Choice 和可输入状态，同时继续保留关闭、最小化、最大化与最大化状态查询桥。
5. 主宠物窗口不得出现 `[data-theater-app]`，也不得注入 `theater.js`；剧场恢复逻辑只作用于子窗口。
6. 独立 child-window smoke 继续验证从 React Chat 父窗口打开剧场时同样获得宿主窗口控制桥。

第四轮组合证据边界：

1. Electron smoke 证明真实桌面主进程、窗口创建、preload、存储分区、正式前端脚本和刷新恢复可以连续工作。
2. Python/Chromium 回归证明正式 Runtime 的公开 state / active 接口、私有字段隔离、幂等回放和 revision 冲突语义成立。
3. 两组验证当前是隔离可复现的组合验收，不声称已经覆盖打包安装后的真实模型、语音或用户数据环境。

第四轮必须通过以下玩家脚本：

1. 从真实 PC 宠物主窗口打开小剧场，开始一次演出后刷新子窗口，恢复公开对白且输入框继续可用。
2. 刷新前后 session ID 保持一致，start 接口总调用次数为 1，state 接口至少调用 1 次。
3. 刷新后窗口控制桥仍完整，主宠物窗口仍不包含剧场根节点或剧场脚本。

#### 第五轮：聊天、语音、小游戏与记忆跨链路隔离

第五轮原始范围不把小剧场接进普通聊天、语音或小游戏。轻量版后续增加了一条明确例外：只允许把 Runtime 已提交的公开猫娘对白交给现有 Project TTS 朗读；该例外不开放私有 Session 读取、不占用小游戏 route，也不把台词写回普通聊天。

跨链路规则：

1. `/theater` 与 `/chat`、`/subtitle`、游戏 Router 平行注册；chat/subtitle 页面不得加载 `theater.js`、`theater.css` 或 `[data-theater-app]`。
2. 小剧场启动、输入、恢复和结束不得占用游戏 route state，不得调用游戏 API、BroadcastChannel 或原生语音注册表；唯一允许的语音调用是 Theater Router 使用当前猫娘 Session Manager 的 Project TTS 窄桥接，且只处理公开 `dialogue.text`。
3. 普通聊天、游戏、语音和 Core 文件不得依赖 `services.theater` 或读取 theater 私有 session；小剧场也不得把 Story State、隐藏线索、GM Anchor 或剧情物理事实推入这些链路。
4. 落幕只生成私有 `theater_affective` 候选，默认 `ordinary_memory_write=false`；玩家未确认或选择“暂不记住”时不得调用普通记忆 `/cache`。
5. 玩家确认后仍只允许 Type A 用户行为偏好进入普通记忆；Type B 角色反应偏置和 Type C 剧本物理事实不得写作现实长期记忆。
6. 剧情回合与记忆确认共享同一 session guard。两个同时到达的“记住”请求必须串行读取最新落盘状态，只允许第一次执行普通记忆副作用。
7. 普通记忆写入失败可以再次确认重试；已经写入成功的 session 再次确认只能回放已写状态，不能重复调用记忆服务。

第五轮验证结果：

1. 失败测试先稳定复现并发双确认会产生两次普通记忆调用；接入共享 session guard 后，同一测试只产生一次调用。
2. 小剧场完整回归 `301 passed`，其中 Chromium Playwright `6 passed`；真实 Electron 进程 smoke `2 passed`。
3. 普通聊天、游戏、语音和记忆的针对性测试 `393 passed, 3 skipped`；3 项文本聊天真实模型测试只因本机未配置 Qwen API key 跳过，不属于功能失败。
4. `test_react_chat_window_static.py` 另有 7 个与小剧场无关的既存静态断言失败，涉及新用户破冰无障碍标签、工具扇样式、教程 spotlight 和音乐挂载函数。第五轮未修改这些文件，也不把这 7 项计入小剧场通过结果。

第五轮必须通过以下玩家脚本：

1. 同时打开小剧场、普通聊天和字幕页，只有小剧场窗口加载 theater 资源，普通页面继续正常渲染。
2. 小剧场运行期间游戏 route state 保持原值，语音注册和 TTS provider 不读取 theater session。
3. 落幕后不点击记忆确认，普通记忆服务零调用；点击“暂不记住”仍为零调用。
4. 快速双击“记住”，两个请求都返回已记住，但普通记忆服务总调用次数为 1。

#### 第六轮：星灯祭六道具、玩家事件与图外分支桌面验收

第六轮把第一条真实 v2.3 样例从“后端能跑完”推进到“玩家在正式界面能看见状态变化”。验收使用正式星灯祭 JSON、正式 Runtime、正式 Router、正式 `theater.js` 和真实 Chromium，不用模拟 Board 响应。

六道具桌面规则：

1. 开场公开半张配方卡和发光糖霜锅；调查完成后分别进入已使用列表。
2. 到达灯笼摊后开放三只调音风铃，借回后进入已使用列表。
3. 到达团子摊后开放桂花糖，只加一勺后进入已使用列表。
4. 到达面具摊后开放旧鱼形印章，压出第一片星鳞后进入已使用列表。
5. 第一个小客人出现后开放试吃纸杯，客人确认成品后进入已使用列表。
6. 正式结局前 Scenario Board 必须同时显示 6 件可用道具和 6 件已使用道具，不能靠前端自行推算。

玩家事件规则：

1. `event_river_breeze_rings_bells` 是玩家专属事件；完成猫娘开张目标后显示独立事件按钮，Runtime 不得自动消费。
2. 玩家点击后公开“风铃节奏”线索，事件按钮消失，原主线 Choice 保留，revision 与后续主线连续递增。
3. `player_or_runtime` 事件继续按现有确定性随机规则工作，不因玩家专属事件验收而改成强制按钮。

图外分支修复：

1. 实际探针确认：开场输入“我捡起纸风车，发现叶片沾着桂花糖粉”会正确提交动态节点和公开线索，但原剧本桥接白名单只有中后段目标，当前动态节点没有任何可执行 Choice。
2. 星灯祭动态边界新增 `node_read_recipe_clue / node_check_frosting_pot` 两个开场安全返回点；没有放宽 Overlay 动作、实体、事实、Evidence 或 Ending 白名单。
3. Full/Economy 均验证图外纸风车分支提交后公开“关于纸风车的观察”，随后点击正式配方卡 Choice 以 `bridge` 返回静态主线。
4. 返回主线后图外线索继续留在公开 Board，同时新增正式“三次铃声”线索，证明动态影响不会因桥接丢失。

第六轮证据边界：

1. 正式浏览器 Economy 流程实际执行 21 次提交，覆盖玩家事件、20 个稳定主线 Choice、六道具状态和正式结局。
2. Full/Economy 图外分支使用固定的受约束模型抽取结果，后续 Overlay、Validator、事务、Board 和 bridge 全部走正式 Runtime。
3. 本机没有配置模型 API，因此不把固定抽取测试表述为“真实模型端到端”；模型只负责抽取原话跨度，安全提交仍由服务端确定性完成。

第六轮必须通过以下玩家脚本：

1. 在正式 Chromium 选择星灯祭 Economy，沿主线触发河风事件并完成结局，六件道具依次开放且最终全部显示为已使用。
2. 玩家专属河风事件被点击前不自动消失，点击后公开风铃线索且主线按钮仍可继续。
3. 在 Full/Economy 开场提交纸风车图外行动，界面协议返回安全主线 Choice；点击后以 bridge 返回，并同时保留动态图外线索与正式配方线索。

#### 第七轮：Choice 即时 callback、糖霜救场与正式落幕

第七轮解决“两个按钮虽然文字不同，但点完只看到同一段节点结果”的互动小说体感问题。callback 不是新的剧情节点或状态增量，而是作者对玩家刚刚所选动作的即时公开反馈；公共节点仍负责共享推进结果。

callback 运行规则：

1. 每个 Story suggestion 使用 `callback` 声明一段简短公开反馈；星灯祭 42 个稳定 Choice 已全部补齐且内容互不复制。
2. `suggestion_options` 仍只公开 `choice_id / label / action_type`，不提前把 callback 下发给前端，避免玩家从按钮协议读取未选择分支的结果。
3. 服务端确认当前 committed state 中的稳定 Choice 后，`resolve_choice_id()` 才把对应 callback 放入私有 route。
4. callback 位于共享节点旁白之前；按钮提交和自然语言精确/语义等价提交只要解析到同一 Choice，就使用同一 callback。
5. Full/Economy 共用同一 callback 合并函数；Full Narrator 不能覆盖作者反馈，结局确认后重新生成终章旁白时也必须重新附加。
6. callback 合并后的完整 narration/dialogue 继续经过事实边界和 Validator；内部字段、隐藏线索和未计划剧情结果仍会导致回滚。
7. 作者 callback 只能描述该 Choice 已明确承诺的即时动作或可见结果，不能替玩家补内心、额外行动、Evidence 或 Ending。

糖霜暴走救场规则：

1. 原路径 `node_sugarstorm_mistake -> node_add_osmanthus` 会直接使用尚未开放的桂花糖，违反六道具生命周期。
2. 修正后路径为 `糖霜暴走 -> 团子摊 -> 加入桂花糖 -> 鱼形印章主线`；事故节点先公开甜香救场方向，团子摊节点负责开放道具，下一轮才实际使用。
3. 玩家询问“接下来做什么”时 Full GM-Lite 也先进入团子摊；继续添加草莓酱等图外材料时只提示当前甜香方向，不编造新摊位。
4. 糖霜暴走仍是明亮的喜剧事故，不生成 ending、不关闭 session，也不把错误选择变成硬死路。

正式结局文案：

1. 结局旁白明确回收半张配方卡、糖霜锅、三只风铃、桂花糖、鱼形印章和试吃纸杯，并说明第一炉“不完美但已经能交到客人手里”。
2. 两个结局 Choice 分别反馈“调亮招牌灯”和“确认第一炉步骤”，之后汇合到同一正式落幕。
3. 猫娘只保留试吃券与下次排队的轻喜剧邀请，不延伸成现实长期承诺或普通记忆事实。

第七轮必须通过以下玩家脚本：

1. 从相同 committed state 分别选择“凑近看糖霜”和“先别加料观察节拍”，本轮旁白不同，但 revision、节点、Board 和后续 Choice 一致。
2. 使用自然语言输入第二个按钮原文，得到与该按钮相同的 callback，不被误判为 GM 拉回或动态图外行动。
3. 触发糖霜暴走后，桂花糖先从隐藏变为可用，下一轮使用后才进入已使用列表，最终继续进入鱼形印章主线。
4. Full/Economy 主线均能携带 callback 完成正式结局，Compiler 对星灯祭不再报告 `choices missing callback`。

#### 第八轮：语义级隐藏事实防泄露

第八轮补齐“没有逐字复述，但换一种说法提前讲出答案”的防剧透缺口。Level 1 继续负责确定性边界、逐字敏感文本和状态规则；Level 2 只在 Full 模式的关键 core / ending 回合读取限量私密事实，对最终旁白与角色对白进行语义比较。模型仍是输出校验器，不参与节点选择、状态判断、Evidence 确认或 Ending 决策。

实现约束：

1. Level 2 最多接收 12 条、每条最多 240 字的 `forbidden_reveals` 私密正文，不接收 clue / question / evidence 等内部引用 ID；这些正文只进入 Validator prompt，不进入 Persona、公开响应或 Public Trace。
2. 语义等价改写、解释、步骤重排和可直接推出隐藏结论的暗示都算泄露；玩家自己在输入中提出的猜测不算系统泄露，校验对象只限最终旁白与角色对白。
3. 模型只能返回受限 `failed_check`；命中 `semantic_forbidden_reveal` 后，服务端统一替换为固定私密错误原因，不采用模型原始 reason，避免模型在报错中再次复述隐藏正文。
4. 语义泄露属于终止型渲染失败，Runtime 必须立即离开二次重路由循环，进入现有 rollback 与安全公开文本替换；不得把第二次 `graph_no_candidate` 结果当作通过，从而“洗白”第一次泄露。
5. 未配置模型、调用超时、异常、坏 JSON 或不合规错误码时，保留 Level 1 的确定性结果并继续正常游玩；高级校验不可用不能制造新的 GM 拉回或空回合。
6. 当前自动化使用受控模型稳定证明调用边界、语义拒绝、回滚和降级行为；本机未配置供应商模型 API，因此不把这一证据描述为真实模型端到端效果。

第八轮必须通过以下玩家脚本：

1. 隐藏规则写作“第一声铃响时开火，第二声搅拌，第三声立刻收光”，演绎改写为“铃第一次响就点火，接着搅两下，最后一响马上停手”时，Level 1 未逐字命中但 Level 2 拒绝。
2. 同一次 Full 回合被语义校验拒绝后，节点、剧情事实和 Overlay 不提交，revision 只记录一次已回滚请求；公开响应、Trace 和 rollback 日志都不出现隐藏原文或泄露改写。
3. Level 2 返回坏 JSON、超时或没有模型配置时，Level 1 已通过的普通演绎继续提交，不显示“GM 拉回：请回到剧本选项”。

#### 第九轮：真实模型发行候选验收入口

第九轮不把受控模型测试冒充真实模型效果，而是补齐一个只能通过显式环境变量开启的发行候选 smoke。测试不读取用户正式配置或本地存档；没有开关、模型名或服务地址时明确显示 skipped，避免 CI 意外消费额度，也避免“没运行”被误报成“已通过”。

真实模型 smoke 覆盖：

1. 既有 Anchor、Persona 和 Memory 三段 summary 模型链保持正常。
2. 图外候选只能返回固定七字段协议；新对象名称和观察正文必须是玩家原话中的连续片段，动作和实体类型必须来自允许枚举。
3. 同义改写完整传达隐藏操作步骤时，Level 2 必须返回 `semantic_forbidden_reveal`，且服务端结果不包含隐藏原文。
4. 只描写公开风铃声、没有传达隐藏步骤的对照样本必须通过，防止模型以“一律拒绝”制造假安全。

发行候选执行命令：

```bash
NEKO_RUN_THEATER_LLM_SMOKE=1 \
NEKO_THEATER_LLM_SMOKE_MODEL='<model>' \
NEKO_THEATER_LLM_SMOKE_BASE_URL='<openai-compatible-base-url>' \
NEKO_THEATER_LLM_SMOKE_API_KEY='<api-key>' \
NEKO_THEATER_LLM_SMOKE_PROVIDER_TYPE='openai_compatible' \
.venv/bin/python -m pytest -q tests/integration/test_theater_real_model_smoke.py
```

验收时必须看到 4 项通过；本机当前结果为 4 项 skipped，原因是没有设置上述显式发行候选环境，不能据此宣称真实模型效果已经通过。

#### 第十轮：OpenAI-compatible 协议纵向闭环

第十轮解决“组件 monkeypatch 通过，但正式 SDK 与 HTTP 协议可能没有走通”的证据缺口。测试在进程内启动只监听 `127.0.0.1` 随机端口的兼容服务器，不访问外网、不读取用户密钥，但 theater 调用端使用正式 `create_chat_llm_async`、OpenAI SDK、Bearer 鉴权和 `/v1/chat/completions` 请求。

纵向协议断言：

1. Dynamic Candidate 和 Level 2 Validator 分别完成一次真实 HTTP 往返，并继续经过各自正式 JSON 解析器。
2. 两次请求都只发送 system/user 两条消息，使用 `max_completion_tokens=220/180`，不发送项目禁止的 `temperature`。
3. 图外候选响应进入固定七字段协议，玩家原话连续片段可以被正式解析为新对象与观察。
4. Validator wire prompt 可以携带限量隐藏正文做语义比较，但不能携带 `ref_id` 等内部 clue 引用；模型 reason 即使返回私密错误，也由服务端固定错误替换。
5. 测试服务器完整关闭监听端口和线程，不残留后台服务，不依赖供应商稳定性或额度。

这一轮证明的是“正式客户端协议链可用”，不是“真实模型理解质量已通过”；理解质量仍由第九轮显式 real-model smoke 在发行候选环境确认。

#### 第十一轮：行动/对白分区与空 Choice 回收

第十一轮直接审查最新真实演绎 session，而不是只看自动化主线。记录显示玩家先用自由输入摇空风铃，再选择查看糖霜锅并完成“花火前开张”目标；由于配方卡线索尚未发现，`node_split_tasks` 前置条件不满足，后端产生 `suggestions` 但 `suggestion_options=[]`。前端用 JavaScript 真值选择结构化数组，空数组因此吞掉文字推荐，玩家只能反复输入“然后呢 / 可以 / 找什么节奏”绕圈。

对照结论：

1. AI织梦在作品页把自由行动输入框与“选择选项”入口明确分开，玩家不会把预写选择和自由输入误认为同一种交互。
2. 橙光采用固定下一句、菜单和分支选择语义；即使不提供自由输入，也始终保留明确可继续的控制。
3. 小剧场继续保留 Choice 与自由输入并存，但 Choice 内部进一步分成“你可以做 / 你可以说”，不照搬外部产品视觉或商业系统。

实现约束：

1. Story suggestion 可选声明 `choice_mode: action | dialogue`；对话必须由作者明确标注，通用层不按“问、说”等词猜测。未声明的旧剧本默认进入行动区。
2. `choice_mode` 作为安全公开字段穿过 Compiler、Graph Router、Suggestion Engine 和 session 响应；非法枚举在编译阶段阻断。
3. 星灯祭的明确对白改写成玩家可以直接说出口的引号句，行动仍保持动词短句；两组分别显示、分别编号，稳定 `choice_id` 和 callback 不变。
4. 前端必须检查 `suggestion_options.length`；空数组时才使用兼容 `suggestions`，避免 JavaScript 中空数组真值导致整个推荐区消失。
5. 星灯祭增加 `node_catgirl_sets_goal -> node_read_recipe_clue` 恢复边。玩家先查糖霜锅时，分工节点因缺配方线索暂不可用，但配方卡 Choice 会重新出现；完成配方节点后恢复边因目标已完成自动隐藏，再回到正常主线。
6. 空推荐区、两种选项分区和恢复边都必须有自动化证据；不能用“自由输入仍能打字”替代可理解的下一步。

第十一轮验收脚本：

1. 同一回合同时存在行动与对白 Choice 时，页面分别显示“你可以做 / 你可以说”，点击仍提交对应稳定 `choice_id`。
2. 响应包含非空 `suggestions` 和空 `suggestion_options` 时，兼容推荐进入行动区，页面不为空。
3. 从开场先查看糖霜锅，再完成花火前开张目标，下一轮必须出现 `choice_read_recipe_clue_1`，不得只剩自由输入。
4. 八种语言包含两组标题，窄窗口两组按钮仍可横向滚动，输入区保持可见。

这个流程对不同题材都成立：

```text
日常委托：找丢失物件、确认线索、使用道具、完成委托。
轻冒险：探索场景、使用道具、避开危险、完成目标。
情感陪伴：确认场面、选择回应、整理误会、达成约定。
轻推理：发现矛盾、对照线索、提出推理、完成收束。
```

《星灯祭的糖霜危机》继续作为第一条适配样例，用来验证通用框架是否能承载“夜市甜品共演”，但它不是 v2.3 的专属核心。

## 不做什么

v2.3 必须明确排除以下方向：

1. 不做 SDK。
2. 不接视觉和多模态；语音只限已提交猫娘对白复用现有 Project TTS，不建设独立语音编排系统。
3. 不引入好感度、信任分、理智值、黑化值、亲密度等任何量化系统。
4. 不做传统橙光式硬死路；错误选择可以制造小麻烦、延迟、绕路或边界暂停，但不应让玩家莫名提前坏结局。
5. 不让作者靠复杂提示词直接控制运行时。
6. 不让猫娘提前公布玩家尚未发现的关键线索或道具用途。
7. 不把小剧场内的剧情事实直接写入普通长期记忆。
8. 不污染普通聊天、语音、小游戏、角色记忆和其它前端链路。
9. 不允许未经 Runtime 校验和事务提交的自由脚本直接修改 theater 私有状态。
10. 随机关键事件和图外改写可以产生新节点、道具、线索与剧情事实，但不能绕过状态提交直接宣布完成。
11. 不把图外输入一律当作跑题；只有违反剧本硬边界、用户身份权限或当前世界事实且无法桥接时，GM 才负责拉回。
12. 不为了复用外部项目而引入成就、进度百分比、草率结局或大规模多 Agent 编排。

说明：`state_revision / draw_index / cooldown_turns / priority / selection_weight` 只属于并发控制、随机复现和候选排序的内部技术元数据，不向玩家展示，不表达角色关系或剧情完成度，也不能参与 evidence 和 ending 判断。

## 体验模型

### 1. 剧本卡

剧本卡是玩家开场前看到的结构化背景，不等同于长篇设定，也不限定为凶案或事故。

剧本卡要回答：

1. 发生了什么事？
2. 玩家是谁？
3. 当前目标是什么？
4. 现场有哪些舞台道具？
5. 当前有哪些可推进方向？
6. GM 会如何把玩家跑飞的话题带回剧本？
7. 有哪些边界规则？

通用剧本卡模板：

```text
开场：本剧本发生了什么，为什么现在必须行动。
身份：玩家在本剧本里的身份和权限。
目标：本次剧场要完成的明确目标。
舞台道具：当前可看见、可询问、可使用的物件或角色。
可推进方向：调查、询问、使用道具、整理信息、执行行动等。
GM 拉回方式：玩家跑题时，用哪些场景压力、道具反应或角色提醒带回剧本。
边界：哪些事不能由玩家独自完成，哪些内容不能越过当前剧本。
```

### 2. 道具与线索板

道具与线索板记录玩家已经看见、使用或确认过的舞台元素。它不是分数，也不是进度条。

内容可以分四类：

1. 可用道具：玩家已经知道可以拿来互动的东西。
2. 未解释线索：玩家看到但还不知道意义的信息。
3. 已确认事实：玩家和猫娘已经验证过的内容。
4. 已使用凭据：已经用于推动阶段、解开疑点或触发结局的事实。

通用记录示例：

```text
可用道具：一件能被玩家使用或询问的舞台物。
未解释线索：玩家看见但还不知道意义的信息。
已确认事实：玩家和猫娘已经共同验证过的内容。
已使用凭据：已经推动阶段或触发收束的剧情事实。
```

### 3. 疑点板

疑点板记录当前仍未解决的问题，帮助 GM 和 Choice Policy 生成下一步推进方向。

疑点示例可以按题材变化：

```text
这个道具真正能做什么？
猫娘刚才回避了哪个问题？
当前目标还缺哪一步？
为什么这个场面不能直接跳到结局？
```

疑点解决后不删除历史，而是标记为 resolved，方便复盘。

### 4. 剧本动作选择

v2.2 的选项不只是“下一句话”，而是“下一步剧本动作”。

公开按钮仍然必须自然，不展示内部字段。

允许的选项类型：

1. `investigate`：调查物件或场景。
2. `question`：询问猫娘或现场角色。
3. `use_prop`：使用当前可用道具。
4. `compare`：对照两个线索或道具反应。
5. `deduce`：提出推理。
6. `act`：执行已经有剧情凭据支撑的动作。
7. `organize`：暂时停下，整理线索、道具和目标。
8. `redirect`：GM 把跑飞话题带回剧本。
9. `leave`：主动结束本次小剧场。

公开文案示例：

```text
检查桌上的旧纸条是不是还藏着字
问猫娘这个道具是不是上一幕留下的提示
把刚发现的线索和现场反应对照一下
先别急着行动，推一下这个道具可能对应哪一步
把当前能用的道具拿回来试一次
让 GM 先把现在能用的道具捋一遍
停一下，先把我们知道的线索捋清楚
那我先走了，下次见。
```

## v2.2 继承协议

以下字段是 v2.3 继续保留的 v2.2 已实现基线。v2.3 只在其上补稳定 ID、交叉引用、实体生命周期和统一事务，不重新发明一套剧本格式。

### Story 级字段

```json
{
  "scenario_card": {
    "brief": "玩家可见剧本卡正文。",
    "player_role": "玩家在本剧场内的身份。",
    "primary_goal": "本次剧场的明确目标。",
    "known_questions": ["开场时玩家就知道的疑点或目标。"],
    "stage_props": [
      {
        "id": "prop_key_item",
        "label": "关键道具",
        "public_hint": "当前场景里最值得先看的物件。",
        "available_from_node": "node_opening"
      }
    ],
    "gm_guidance": {
      "role": "旁白与主持人，负责描述场面、提示道具、拉回跑题。",
      "redirect_anchors": ["当前道具出现反应", "场景目标临近", "猫娘提醒正事"]
    },
    "rules": ["玩家不能替猫娘独自完成关键作品。"]
  }
}
```

说明：

1. `scenario_card` 面向用户和前端，不给 Director 自由发挥。
2. `brief` 可以比 `summary` 更结构化；`summary` 继续服务故事列表短介绍。
3. `stage_props` 是玩家可用道具和可互动对象，不是可点击 UI 的唯一来源；最终按钮仍由 NSN 当前可达节点生成。
4. `gm_guidance` 约束 GM 旁白如何控场，尤其是玩家脑洞跑远时如何回到剧本。

### Narrative Node 增量字段

```json
{
  "node_id": "node_check_key_item",
  "node_type": "clue_discovery",
  "script_action": {
    "action_type": "investigate",
    "target_id": "prop_key_item",
    "reveals_clues": ["clue_key_item_has_mark"],
    "resolves_questions": [],
    "requires_clues": [],
    "uses_props": ["prop_key_item"]
  },
  "state_diff": {
    "add": [
      {
        "type": "clue",
        "subject": "key_item",
        "predicate": "has",
        "object": "unexplained_mark"
      }
    ],
    "remove": [],
    "modify": []
  }
}
```

说明：

1. `node_type` 可以继续兼容 `core / boundary / ending`，但 v2.2 推荐新增语义类型。
2. `script_action.action_type` 决定本节点是调查、询问、使用道具、对照、推理、执行、整理还是 GM 拉回。
3. `reveals_clues` 只记录本节点新揭示的线索。
4. `requires_clues` 用来防止未发现线索时提前执行关键动作。
5. `uses_props` 记录本轮使用了哪些舞台道具。

### Clue / Prop 定义

```json
{
  "stage_props": [
    {
      "id": "prop_key_item",
      "label": "关键道具",
      "public_hint": "当前场景里最值得先看的物件。",
      "available_from_node": "node_opening"
    },
    {
      "id": "prop_helper_tool",
      "label": "辅助道具",
      "public_hint": "可能和关键道具发生反应的物件。",
      "available_from_node": "node_opening"
    }
  ],
  "clues": [
    {
      "id": "clue_key_item_has_mark",
      "title": "关键道具上的记号",
      "public_text": "关键道具上有一个还没解释的记号。",
      "hidden_meaning": "这个记号可能对应后续行动步骤。",
      "source_target_id": "prop_key_item"
    }
  ]
}
```

说明：

1. `stage_props` 是道具或可互动对象，允许没有谜题含义，只负责把玩家行为锚回舞台。
2. `public_text` 可以展示给玩家。
3. `hidden_meaning` 只给运行时和复盘，不在发现瞬间自动剧透。
4. 线索本身不带分数，只带是否已发现、是否已确认、是否已使用。

### Progress Evidence Rule

```json
{
  "evidence_rules": [
    {
      "id": "evidence_goal_step_ready",
      "title": "关键步骤已经准备好",
      "requires_clues": [
        "clue_key_item_has_mark",
        "clue_helper_tool_reacts"
      ],
      "commits_fact": {
        "type": "evidence",
        "subject": "main_goal",
        "predicate": "has_ready_step",
        "object": "key_item_resolved"
      }
    }
  ]
}
```

说明：

1. evidence 在 v2.2 中表示“剧情凭据”，不是刑侦证据。
2. evidence 可以来自线索组合、道具使用、角色承认、任务完成或场面变化。
3. Ending 只看 committed evidence，不看“玩家可能猜到了”。
4. evidence 不需要数值强度。

## v2.2 Story State 基线

建议在现有 `story_state` 中增加：

```json
{
  "scenario_state": {
    "available_prop_ids": [],
    "used_prop_ids": [],
    "discovered_clue_ids": [],
    "confirmed_evidence_ids": [],
    "used_evidence_ids": [],
    "open_question_ids": [],
    "resolved_question_ids": [],
    "visited_anchor_ids": [],
    "redirect_count": 0,
    "last_redirect_reason": "",
    "last_action_type": ""
  }
}
```

说明：

1. 这些字段都是 theater 私有状态，不进入普通记忆。
2. 字段记录“是否发生过”，不记录分数。
3. Runtime 和 Session Trace 可以用它判断本轮推进类型。
4. Persona 只能看到公开可见的道具、线索和剧情凭据摘要，不能看到 hidden meaning 和未发现线索。
5. `redirect_count` 不是惩罚分，只用于判断玩家是否连续跑题，帮助 GM 换一种更自然的拉回方式。

## v2.2 Runtime 基线

### Full 模式

```text
User Input
→ Input Signal
→ Candidate Edges
→ Script Anchor Filter
→ Turn Plan
→ State Preview
→ GM Narrator + Persona
→ Validator
→ Commit / Rollback
→ Choice Policy
→ Session Trace
```

变化点：

1. `Script Anchor Filter` 根据已发现线索、可用道具、当前目标和 GM 拉回策略过滤候选节点。
2. `Turn Plan` 需要标记本轮 action type。
3. `Validator` 要检查猫娘是否提前剧透未发现线索，或 GM 是否把玩家带到剧本外。
4. `Commit` 时要更新 `scenario_state`。
5. `Choice Policy` 根据当前 open questions、available props 和 redirect anchors 生成按钮。

### Economy 模式

以下描述是当前 v2.2 已实现行为，不是 v2.3 的最终限制：

```text
Clicked Choice
→ Graph Edge Match
→ Script Anchor Rule Check
→ Scripted Node Commit
→ Persona Paraphrase
→ GM Redirect When Needed
→ Choice Policy
→ Session Trace
```

变化点：

1. 不做剧情推导。
2. 只允许当前可见剧本按钮推进。
3. 旁白按剧本节点。
4. 猫娘对白仍通过 Persona 转述。
5. 若用户自由输入，不推进剧情；GM 用当前道具、目标或场面变化把话题拉回可选剧本动作。

v2.3 会替换第 1、2、5 条的限制：

1. 点击当前公开 choice 时继续走确定性低成本链路，不调用剧情推导模型。
2. 玩家自由输入仍可触发随机关键事件、动态分支、桥接或拉回。
3. Economy 遇到自由输入时允许调用一次受约束的 Progress Resolver；不启用 Full 模式的完整 Director / Narrator 多层生成。
4. Economy 表示减少生成层和模型调用，不表示禁止图外自由改写。
5. Full 与 Economy 必须产生相同的 Turn Plan、状态校验和提交结果；差异只存在于演绎文本的生成方式。

## v2.2 Choice Policy 基线

v2.2 的按钮策略：

1. 优先给和当前目标、疑点、可用道具相关的调查/询问/使用/对照按钮。
2. 剧情凭据不足时，不给最终执行按钮。
3. 已使用过的道具或已访问过的锚点不重复推荐，除非新的线索让它重新变得有意义。
4. 至少保留一个推进按钮；已有推进后可补中性离场按钮。
5. 当前 v2.2 `economy` 模式不展示自由输入提示；v2.3 前端必须恢复独立自由输入入口，但不把它伪装成 choice。
6. `full` 和 `economy` 的自由输入都必须经过 Progress Resolver 与 Runtime Validator；只有无法吸收或桥接时才由 GM 拉回。

按钮返回仍保持：

```json
{
  "label": "把刚发现的线索和现场反应对照一下",
  "behavior_hint": "compare",
  "meaning_hint": "evidence_check",
  "action_type": "compare"
}
```

以下内部字段只给运行时使用，不直接展示给用户：

```json
{
  "target_id": "prop_key_item",
  "prop_ids": ["prop_helper_tool"],
  "clue_ids": ["clue_key_item_has_mark"],
  "redirect_anchor": "当前目标正在临近"
}
```

## Persona 边界

猫娘在 v2.2 不是答案播报器。

Persona 可以做：

1. 用猫娘语气转述当前节点对白。
2. 表达情绪、犹豫、嘴硬、催促或误判。
3. 对已发现线索做自然反应。
4. 提醒玩家“我们还没确认这个”。
5. 对 GM 拉回后的场面做角色化回应，例如提醒玩家先看当前道具、先确认目标、先别离开剧本场景。

Persona 不能做：

1. 提前说出未发现线索。
2. 把 hidden meaning 直接说给玩家。
3. 独自完成关键推理。
4. 跳过道具、线索或剧情凭据直接宣布结局。
5. 把剧场内事件写成现实记忆。

实现上，Persona prompt 应只接收：

1. 当前 scene。
2. 玩家本轮输入。
3. 当前节点的 `catgirl_instruction`。
4. 当前可见道具的 `public_hint`。
5. 已发现线索的 `public_text`。
6. 已确认 evidence 的公开摘要。
7. GM 本轮是否正在拉回跑题。
8. 用户身份边界。

## v2.2 Ending Evidence 基线

正式结局必须满足剧情凭据，而不是轮数。

通用正式结局建议要求：

```text
evidence_key_clue_confirmed
evidence_required_prop_used
evidence_main_goal_ready
evidence_final_response_accepted
```

对应含义：

1. 关键线索已经确认。
2. 必要道具已经使用。
3. 主目标已经具备完成条件。
4. 角色或场景已经接受最终收束。

只有当前剧本声明的 evidence 都 committed，才允许对应 `story_ending` 达成。

玩家主动离场仍然是 `user_exit`，不伪装成成功结局。

## v2.2 Session Trace 基线

每轮 trace 需要能回答：

1. 玩家本轮做了什么类型的动作？
2. 命中了哪个道具、场景锚点或角色锚点？
3. 新发现了什么线索？
4. 确认了什么 evidence？
5. 解开了哪个疑点？
6. GM 是否进行了跑题拉回？
7. 为什么给出下一组选项？
8. 如果没有推进，是因为自由输入、剧情凭据不足、节点不可达、跑题拉回，还是 Validator 拒绝？

建议新增 trace 字段：

```json
{
  "scenario_trace": {
    "action_type": "investigate",
    "action_label": "调查",
    "target_id": "prop_key_item",
    "used_prop_ids": ["prop_key_item"],
    "revealed_clue_ids": ["clue_key_item_has_mark"],
    "confirmed_evidence_ids": [],
    "resolved_question_ids": [],
    "redirected": false,
    "redirect_anchor": "",
    "blocked_reason": "",
    "blocked_label": ""
  }
}
```

## v2.3 外部参考与吸收边界

v2.3 的外部调研只用于补强 NSN 已有职责，不代表复制其它项目的产品模型或运行时。

### WhatIf

参考来源：

1. [WhatIf 项目说明](https://github.com/ypcypc/WhatIf)
2. [实体状态转换校验](https://github.com/ypcypc/WhatIf/blob/main/backend/preprocessing/entity_transition/validators.py)
3. [Delta State 生命周期](https://github.com/ypcypc/WhatIf/blob/main/backend/runtime/agents/delta_state.py)
4. [上下文召回](https://github.com/ypcypc/WhatIf/blob/main/backend/runtime/agents/context_enrichment/agent.py)
5. [分层压缩](https://github.com/ypcypc/WhatIf/blob/main/backend/runtime/agents/memory_compression/agent.py)
6. [分类 JSONL 日志](https://github.com/ypcypc/WhatIf/blob/main/backend/runtime/game_logger.py)

可以吸收：

1. 把 Story JSON 先编译成运行时索引，而不是每轮重复扫描松散字段。
2. 用明确的实体 `from -> to` 状态转换表达道具开放、取得、使用和失效。
3. 区分“事实是否仍成立”和“是否仍处于当前叙事焦点”。
4. 长剧本按事件摘要和跨事件摘要分层召回上下文。
5. 把玩家公开复盘与开发者内部引擎日志分开。
6. 根据连续偏离历史判断是吸收为动态分支、桥接回主图，还是使用 GM 拉回。

明确不吸收：

1. 不照搬“覆盖原著正史”的替代时间线模型；N.E.K.O 的图外改写属于当前 theater session 的动态分支。
2. 不引入 Delta intensity、关系态度值或其它数值状态。
3. 不为小剧场复制六 Agent 编排；现有模块职责已经足够。
4. 动态世界改写可以扩展或改变当前局内事实，但不能绕过 `forbidden_assumptions`、用户身份权限和正式结局凭据。

### Story-to-game

参考来源：

1. [Story-to-game 项目说明](https://github.com/Shanyin-ai/Story-to-game)
2. [Story-to-game 技能包](https://github.com/Shanyin-ai/Story-to-game/blob/main/story-to-game.skill)

可以吸收：

1. 写作前先形成节拍序列、分支文学大纲、汇合点和结局因果表。
2. 所有节点必须从入口可达，所有非结局节点必须有出口。
3. 所有节点、道具、线索、疑点、凭据和结局引用必须完整。
4. 每条主要路径都必须能到达一个正式结局或自然离场。
5. 多个选项即使汇合到同一节点，也必须有可感知的即时 callback。
6. 每条路径只能使用该路径已经建立的信息，禁止选项和对白跨分支提前引用。
7. 正式结局前必须有收束节点，不能从普通选择直接跳到结局页。
8. 自动检查模板词、作者层字段和过长无互动链。

明确不吸收：

1. 不使用 `val` 主状态值、关系变量或好感数值。
2. 不引入成就数量、剧情进度百分比和草率结局指标。
3. 不采用固定每千字节点数量，也不为追求体量拆出无意义节点。
4. 不把静态 HTML 启动器的数据协议替换成 NSN。

### SillyTavern

参考来源：

1. [World Info](https://sillytavern.wiki/usage/core-concepts/worldinfo/)
2. [群组聊天与发言策略](https://sillytavern.wiki/usage/core-concepts/groupchats/)
3. [会话分支与 Checkpoint](https://sillytavern.wiki/usage/core-concepts/chatfilemanagement/)
4. [聊天与 Prompt 检查](https://sillytavern.wiki/usage/chatting/)

可以吸收：

1. 按当前节点、公开道具、已发现线索和疑点动态装配 Persona / GM 上下文。
2. 借鉴 inclusion group、优先级、sticky、cooldown 和 delay，控制 GM 提示互斥、持续和轮换。
3. 多角色剧场将“谁发言”与“说什么”拆开，每轮只给当前发言角色自己的 Persona 上下文。
4. 从某轮创建独立 session 分支，允许玩家探索另一条路径而不重写原会话。
5. 开发模式显示本轮实际注入的上下文、候选过滤和 Validator 结果。

明确不吸收：

1. World Info 式关键词或语义命中可以触发随机事件候选，但候选仍需经过 NSN 条件检查和事务提交。
2. 关键线索和关键道具可以由随机事件产生；正式结局不能只靠随机命中，仍需剧情凭据闭环。
3. 不开放通用脚本直接写 `story_state`。
4. 不把所有角色卡拼成一个 Prompt，避免角色人格融合。
5. 不把聊天历史中的自然语言自动视为已提交剧情事实。

## v2.3 目标架构

```text
Story JSON
→ Story Compiler
→ Compiled Story Index
→ Turn Request（choice_id / free_input / random_event / user_exit）
→ Progress Resolver（graph / random event / dynamic branch / bridge / redirect）
→ Turn Planner
→ Turn Plan + State Preview
→ Renderer（Full / Economy）
→ Validator
→ Commit / Rollback
→ Projectors
   ├─ Scenario Board
   ├─ Public Scenario Trace
   ├─ Persona Visible Context
   ├─ Suggestion Options
   └─ Internal Engine Trace
→ Session Store
```

约束：

1. Full 和 Economy 共享同一个 Progress Resolver、Planner、Preview、确定性 Validator、Commit 和 Projector。
2. 点击 choice 时两种模式都可以走确定性解析；自由输入时 Economy 最多调用一次受约束 Progress Resolver，Full 可以使用完整 Director / Narrator / 高级 Validator 演绎链。
3. 两种模式的差异只允许影响文本丰富度和模型调用量，不能影响同一 Turn Plan 的状态提交结果。
4. 所有公开投影必须读取同一份 committed state，不能分别推测剧情进度。
5. Validator 失败必须形成非推进 Trace，不能返回空 `scenario_trace`。

## Story Compiler v2.3

Story Compiler 是 `story_loader` 之后、Runtime 之前的确定性编译步骤。它不生成剧情，只负责把作者 JSON 转成可靠运行包。

职责边界：

1. Compiler 只证明静态主图、作者声明的随机事件模板、动态边界和正式结局在协议层有效。
2. Compiler 必须证明不依赖随机命中的静态核心路径至少能到达一个正式结局或自然离场，避免随机事件缺席时剧本不可完成。
3. Compiler 不能证明未来所有 Runtime 生成分支都可达或可收束；每个动态节点和 Overlay delta 由 Runtime Validator 在提交前单独检查。
4. Compiled Story Index 是按 Story JSON 内容哈希生成的进程内只读运行包；第一版不新增需要提交到仓库的编译产物。
5. Runtime 不得修改 Compiled Story Index；会话内新增内容只写 Runtime Graph Overlay。
6. 静态可满足性使用“条件生产者索引 + 图可达性固定点”检查，不枚举或猜测模型输出；只有静态节点和作者声明事件明确提交的 fact 才能证明静态 `fact_committed` 条件可达。
7. 新 session 保存 `compiled_story_hash + private_story_snapshot`。恢复时从私有快照重建 Compiled Story Index，不因磁盘上的同 story id 已更新而静默切换主图；新剧本版本只影响新 session。

建议输出：

```json
{
  "scenario_protocol": "2.3",
  "story": {},
  "indexes": {
    "nodes_by_id": {},
    "outgoing_edges_by_node": {},
    "props_by_id": {},
    "clues_by_id": {},
    "questions_by_id": {},
    "evidence_by_id": {},
    "endings_by_id": {},
    "random_events_by_id": {}
  },
  "policies": {
    "dynamic_boundaries": {}
  },
  "validation_report": {
    "errors": [],
    "warnings": []
  }
}
```

### 协议启用

v2.3 剧本应显式声明：

```json
{
  "schema_version": "2.0.0",
  "scenario_protocol": "2.3",
  "features": [
    "scenario_card",
    "entity_state",
    "clues",
    "questions",
    "evidence",
    "random_events",
    "free_rewrite",
    "gm_redirect",
    "public_trace"
  ]
}
```

说明：

1. `schema_version` 继续表示 NSN Story Package 大版本。
2. `scenario_protocol` 表示剧场玩法协议，避免 v2.1、v2.2、v2.3 共用一个无法区分的版本号。
3. 声明某项 feature 后，对应字段必须完整存在，不能半配置后静默降级。

### 编译期错误

以下问题必须阻止剧本进入 Runtime：

1. 入口节点、edge、prop、clue、question、evidence、ending 引用不存在。
2. `script_action.action_type` 不在冻结词表。
3. `target_id / uses_props / reveals_clues / requires_clues / resolves_questions` 引用未知对象。
4. `ending_attractors.evidence_required` 引用未知剧情凭据。
5. 存在从入口不可达的必需节点或正式结局，或静态核心路径在不依赖随机命中的情况下无法到达任何正式结局或自然离场。
6. 非结局节点没有可达出口，且没有合法 `user_exit` 语义。
7. 静态 evidence 所需条件在主图和作者声明事件模板中都不可能成立。
8. 正式结局依赖结局节点自身提交后才出现的凭据，形成自我依赖。
9. 同一节点的公开 `choice_id` 重复。
10. 公开文案包含 `node_id / behavior_hint / meaning_hint` 等作者层或内部机制词。
11. 随机事件的 eligibility、线索、道具、疑点或 evidence 引用未知对象。
12. 剧本声明的随机事件没有任何可满足状态，或 bridge target 不存在。
13. 动态边界没有声明允许的动作、事实谓词、可生成实体类型或合法 bridge target。
14. `completion_role=primary` 的正式结局只能通过随机抽取获得的条件成立，且主目标没有其它确定性正式结局。
15. v2.3 节点或动态模板声明通用 `state_diff.remove / modify`；旧协议字段只在 v2.1/v2.2 兼容路径读取。

### 编译期警告

以下问题不一定阻止加载，但必须进入验证报告：

1. 开场疑点没有任何节点负责解决。
2. 多个选项进入同一节点但没有独立 callback。
3. 已声明道具从未开放、使用或成为调查目标。
4. 已声明线索从未被发现或不参与任何疑点、凭据和结局。
5. 连续多个节点没有真实选择或调查动作。
6. 某条主要分支没有形成自己的场面变化，只是换文案后立刻汇合。
7. 正式结局前缺少收束节点。

## Turn Request 协议

v2.3 不再用一个 `message` 字段同时表示按钮、自由输入、随机事件和退出。前端统一提交：

```json
{
  "session_id": "theater_xxx",
  "client_turn_id": "turn_01J...",
  "base_revision": 3,
  "input_kind": "choice",
  "choice_id": "choice_check_recipe_card",
  "message": ""
}
```

冻结规则：

1. `input_kind` 第一版只允许 `choice / free_input / random_event / user_exit`。
2. `choice` 必须提供 `choice_id`，服务端以当前 committed state 的公开候选校验，不读取 `message` 路由。
3. `free_input` 必须提供 `message`，不得同时提供 `choice_id`。
4. `random_event` 表示玩家明确尝试触发某个已公开事件入口，必须提供公开 `event_id`；Runtime 自发抽取不伪装成玩家请求。
5. `user_exit` 只表达离开本次剧场，不伪装成正式剧情结局。
6. `client_turn_id` 在同一 session 内唯一。重复提交返回首次提交的已保存结果，不重复抽取事件、创建动态节点或消费凭据。
7. v2.2 `{session_id, message}` 在迁移期继续兼容：若 message 精确匹配当前旧版公开 label，则按 legacy choice 处理，否则按 legacy free input 处理；具体推进语义仍由 session 绑定的 scenario protocol 决定，v2.2 session 不会因此启用 v2.3 动态改写。v2.3 前端不得继续使用该兼容路径。
8. `base_revision` 为可选非负整数。缺省时服务端在 session 锁内使用最新 revision；显式旧值返回 `state_revision_conflict + retryable=true`，不会执行剧情 mutation。重复 `client_turn_id` 优先回放首次结果，不受旧 base revision 影响。

请求解析结果形成内部 `Turn Request`，原始自然语言只保存在 theater 私有 turn 中，不直接视为 committed fact。

公开 Turn Response 至少包含：

```json
{
  "ok": true,
  "session_id": "theater_xxx",
  "client_turn_id": "turn_01J...",
  "input_kind": "choice",
  "base_revision": 3,
  "state_revision": 4,
  "progress_kind": "graph_progress",
  "commit_result": "committed",
  "scenario_board": {},
  "scenario_trace": {},
  "suggestion_options": [],
  "ending": {}
}
```

`ok=true` 表示请求已经形成可复盘的 theater 回合，不等于一定推进；blocked 和 rolled_back 仍返回非空 Public Trace。请求结构非法、session 不存在或安全校验失败时返回 `ok=false + reason`，不得创建回合。完成结果按 `client_turn_id` 建立私有索引，重复请求返回同一公开响应快照。

## 稳定 Choice 协议

前端点击剧本按钮时不能再依赖显示文案反查 edge。v2.3 的公开按钮建议为：

```json
{
  "choice_id": "choice_check_recipe_card",
  "label": "检查半张配方卡的断口",
  "action_type": "investigate"
}
```

服务端私有映射：

```json
{
  "choice_id": "choice_check_recipe_card",
  "from_node": "node_cart_setup",
  "to_node": "node_read_recipe_clue",
  "behavior_hint": "question",
  "meaning_hint": "evidence_check"
}
```

规则：

1. 前端只提交 `choice_id`，`label` 可以改写和翻译。
2. Runtime 校验该 `choice_id` 是否属于当前 committed node 的公开候选。
3. 自由输入使用独立 `input_kind: free_input`，不能伪装成 choice。
4. 多个 choice 可以汇合到同一节点，但本轮 callback 必须回应玩家实际选择。
5. `choice_id` 在 Story Compiler 输出中稳定；动态节点生成的 choice 使用 `dyn_choice_<session-scope-id>` 命名空间，并随 Runtime Graph Overlay 一起保存。

## 通用 Condition DSL

随机 eligibility、实体 transition、动态边界、Context Policy 和 Evidence 共用一个确定性 Condition Evaluator，避免每个模块各自解释条件。

第一版组合运算只允许 `all_of / any_of`，最多嵌套两层。基础原语为：

1. `node_completed`
2. `clue_discovered`
3. `question_open`
4. `question_resolved`
5. `entity_state`，内容为 `{entity_id, dimension, equals}`
6. `fact_committed`
7. `event_triggered`
8. `prop_used`，表示该 prop id 至少出现在一次 committed action 的 `uses_props` 历史中

规则：

1. Condition Evaluator 只读取 committed state 和已提交 Overlay，不读取自然语言聊天历史、Renderer 输出或 State Preview。
2. Story Compiler 校验静态条件引用；Runtime Validator 校验引用 Overlay 对象的动态条件。
3. `when_node_completed / when_action_completed` 等旧作者字段由 Compiler 规范化成 Condition DSL，不在运行时维护第二套判断逻辑。
4. Evidence 只开放本节原语的安全子集，具体范围以 Evidence Condition 章节为准。
5. 不支持任意 `not`、脚本、表达式字符串、数值比较或作者自定义函数。

## 随机关键事件与图外改写

v2.3 将每轮处理结果拆成明确的 `progress_kind`，而不是只分“命中图”和“跑题”：

```text
graph_progress
  命中当前预写节点或 choice，按既有 NSN 图推进。

random_event
  满足当前场景、实体、线索和历史条件后，触发剧本声明或 Runtime 生成的随机关键事件。

dynamic_branch
  玩家提出图外行动，但与角色权限、世界事实和剧本硬边界兼容；Runtime 生成动态节点并提交为本局事实。

bridge
  玩家行动暂时不在主图上，但可以保留其意图并生成过渡节点，之后回到批准的桥接点。

roleplay_response
  玩家进行场景内对话、情绪表达或轻互动；角色回应但不提交关键剧情变化。

clarification
  输入同时存在多个可合法执行的理解；系统请玩家确认，不将歧义视为跑题。

redirect
  输入明确违反玩家身份、当前世界事实或会直接越过正式结局硬边界；GM 进行拉回，不提交关键进度。

none
  请求合法，但当前没有形成可提交剧情变化。

user_exit / story_ending
  分别表示玩家自由离场和满足凭据后的正式剧情结局。
```

### 随机事件候选

剧本可以声明随机事件候选池：

```json
{
  "random_events": [
    {
      "id": "event_festival_guest_arrives_early",
      "title": "提前到来的试吃客人",
      "public_trigger": true,
      "public_label": "请提前到场的客人试吃一小块",
      "eligible_when": {
        "all_of": [
          {"node_completed": "node_borrow_bells"},
          {"question_open": "question_first_batch_ready"}
        ]
      },
      "exclusion_group": "festival_pressure_event",
      "cooldown_turns": 3,
      "trigger_mode": "player_or_runtime",
      "repeat_policy": "once",
      "selection_weight": 1,
      "may_reveal_clues": ["clue_customer_waiting_pressure"]
    }
  ]
}
```

规则：

1. 玩家可以通过自由输入主动触发符合条件的随机事件，也可以由 Runtime 在合适回合提出候选。
2. 随机只决定“当前哪些合格事件被选中”，不能让不满足前置条件的事件越权发生。
3. 随机事件可以产生关键道具、关键线索和新的剧情凭据来源。
4. 事件实际发生后必须经过 Preview、Validator、Commit，并写入 Scenario Trace。
5. 同一 exclusion group 同轮只允许一个事件成立；cooldown 防止连续重复。
6. 玩家明确触发公开 `event_id` 时不进行随机抽取，只检查 eligibility、repeat policy 和 cooldown；条件满足即可进入 Turn Plan。
7. Runtime 自发触发时只在当前合格候选中抽取，并使用 session 私有 `random_seed + draw_index` 得到可复现结果。
8. `selection_weight` 只用于合格事件选择，不是玩家数值、剧情分数或结局条件。
9. 作者声明事件和 Runtime 生成事件都必须形成唯一 `event_instance_id`；重复 `client_turn_id` 返回原实例，不再次抽取。
10. 随机事件可以提供关键线索；剧本主目标至少要有一个不依赖随机命中的正式结局或保证触发的公开救场路径。`completion_role=optional` 的特殊结局可以依赖随机事件，但不能成为完成主目标的唯一通道。
11. cooldown 按成功 committed 的 theater 回合序号计算；blocked、rolled back 和重复请求不消耗 cooldown，也不增加 draw index。
12. `trigger_mode` 只允许 `player / runtime / player_or_runtime`，`repeat_policy` 第一版只允许 `once / cooldown`。
13. Runtime 生成事件使用 `dynamic_event_<session-scope-id>`，完整定义和产生的节点写入 Overlay；它不能伪装成 Story 已声明 event id。
14. 只有 `public_trigger=true` 的声明事件可以投影到公开 `random_event_options`；每项只含 `event_id / label / action_type=random_event`，点击后提交 `input_kind=random_event + event_id`，隐藏候选的 id、条件树和潜在线索不进入前端。
15. 玩家用自然语言尝试触发事件时仍提交 `input_kind=free_input`，由 Progress Resolver 在当前合格事件中匹配，不要求玩家知道 event id。
16. 每轮只有一个主 `progress_kind`。玩家明确触发事件时 `random_event` 是主推进；Runtime 自动事件只能在玩家主行动已经形成合法 Turn Plan 后附加最多一个作者声明事件，不能覆盖或替换玩家行动。
17. Runtime 自动附加事件先独立通过 eligibility 和动态边界校验，再合并到同一 State Delta；不合格候选只记录 Internal Trace，不使玩家主行动回滚。
18. 第一版 Runtime 自动选择只使用作者声明事件模板；模型生成的随机事件只能由玩家自由输入触发，并按动态分支规则写入 Overlay。

会话内随机状态至少保存：

```json
{
  "random_state": {
    "random_seed": "session-private-seed",
    "committed_turn_index": 5,
    "draw_index": 4,
    "triggered_event_ids": ["event_festival_guest_arrives_early"],
    "eligible_after_turn_by_event_id": {
      "event_festival_guest_arrives_early": 7
    },
    "event_instances_by_id": {
      "event_instance_0004": {
        "event_id": "event_festival_guest_arrives_early",
        "origin_turn_id": "turn_01J...",
        "eligible_candidate_ids": ["event_festival_guest_arrives_early"]
      }
    }
  }
}
```

`start_session` 初始化私有 seed 和 `committed_turn_index=0`。存档恢复后必须继续使用已保存的 seed、committed turn index、draw index、cooldown 和事件实例，不能重新计算历史抽取结果。

### 动态节点

图外改写通过后，Runtime 生成 session 私有动态节点：

```json
{
  "node_id": "dynamic_0007",
  "source": "player_free_rewrite",
  "parent_node_id": "node_dango_stall",
  "summary": "玩家提议把桂花糖做成可携带的小糖片，猫娘决定先试一小块。",
  "script_action": {
    "action_type": "act",
    "target_id": "prop_osmanthus_sugar"
  },
  "state_diff": {},
  "bridge_targets": ["node_add_osmanthus", "node_mask_stamp"]
}
```

动态节点中的 `state_diff` 是节点级声明；Turn Planner 必须把它和实体、随机状态及 Overlay 变化规范化成统一 `State Delta`。只有通过 Validator 的 State Delta 可以进入 `state_manager` 提交。

规则：

1. 动态节点只属于当前 theater session，不反写 Story JSON，也不进入普通长期记忆。
2. 动态节点可以继续生成后续动态节点，也可以通过 `bridge_targets` 回到预写图。
3. 动态节点提交的局内事实会参与后续 Persona、实体状态、随机事件和剧情凭据判断。
4. 动态节点不能伪造用户没有表达的动作，不能越过用户身份权限，也不能违反 `forbidden_assumptions`。
5. 玩家可以改写过程与局内现实；正式结局仍需满足对应 evidence，必要时可由动态分支形成新的合法凭据路径。

### Runtime Graph Overlay

动态节点不能只保留一段摘要。每个 theater session 必须持有可序列化的 Runtime Graph Overlay：

```json
{
  "runtime_graph_overlay": {
    "dynamic_nodes_by_id": {
      "dynamic_0007": {
        "node_id": "dynamic_0007",
        "source": "player_free_rewrite",
        "parent_node_id": "node_dango_stall",
        "origin_turn_id": "turn_01J...",
        "summary": "玩家提议先试做一小块桂花糖片。",
        "script_action": {"action_type": "act", "target_id": "prop_osmanthus_sugar"},
        "state_diff": {},
        "bridge_targets": ["node_add_osmanthus"]
      }
    },
    "dynamic_edges": [
      {"from_node": "node_dango_stall", "to_node": "dynamic_0007"}
    ],
    "dynamic_entities_by_id": {},
    "dynamic_clues_by_id": {},
    "dynamic_choices_by_id": {},
    "dynamic_facts": []
  }
}
```

Overlay 规则：

1. `state_manager.py` 是 Overlay delta 的唯一提交者；Progress Resolver 和 Renderer 只能提出 Preview，不能直接写 session。
2. `graph_router.py` 查询 `Compiled Story Index + Runtime Graph Overlay` 的联合视图。静态 ID 与动态 ID 不得重复；动态对象统一使用 `dynamic_ / dyn_choice_ / dyn_entity_ / dyn_clue_` 命名空间。
3. Overlay 中新增的实体和线索必须保存完整的公开定义与私有定义，Projector 只输出公开字段，Persona 只读取已提交且已公开的内容。
4. 来自 Story clue template 的动态线索实例必须保存 `template_id + instance_id`；`clue_discovered` 检查 template id 时，只认已经 committed 且公开发现的实例。
5. 动态节点可以连接静态或动态父节点；`bridge_targets` 只能指向 Compiler 已批准的静态桥接点，或当前 Overlay 中已提交的动态节点。
6. 当前 active node 可以是静态节点或动态节点。建议项、随机 eligibility、实体状态和 evidence 判断都读取同一联合视图。
7. `session_store.py` 随 session 原子保存 Overlay；重启恢复、重复提交和旧 turn 复盘不得重新生成动态对象。
8. v2.2 旧 session 没有 Overlay 时按空 Overlay 懒初始化，不修改原 Story JSON。
9. Runtime 生成的随机事件也落为 Overlay 节点和事件实例，必须满足剧本的 `dynamic_boundaries`，不能绕过动态分支校验。

### 动态边界

Story JSON 必须声明 Runtime 可以改写的范围，而不是让模型自由决定什么都能发生：

```json
{
  "dynamic_boundaries": {
    "allowed_action_types": ["investigate", "question", "use_prop", "compare", "deduce", "organize", "act"],
    "allowed_entity_types": ["prop", "interactive_object", "npc", "location_anchor"],
    "allowed_fact_predicates": ["has_property", "was_used_for", "was_confirmed_by", "is_ready_for"],
    "allowed_bridge_targets": ["node_add_osmanthus", "node_mask_stamp"],
    "forbidden_outcomes": ["declare_story_ending", "grant_undeclared_evidence", "rewrite_player_identity"]
  }
}
```

Runtime Validator 必须拒绝超出允许动作、事实谓词、实体类型和桥接点的 Overlay delta。动态分支可以扩写过程和形成合法局内事实，但不能创造新的 evidence 规则、正式 ending 定义或用户未表达的决定。

## 疑点协议 v2.3

疑点不能再使用显示文案充当内部 ID：

```json
{
  "questions": [
    {
      "id": "question_glow_timing",
      "text": "三次铃声怎样让糖霜稳定发光？",
      "known_from_start": true
    }
  ]
}
```

`script_action.resolves_questions` 只引用稳定 ID。Scenario Board 再把 ID 投影成当前语言的 `text`。

## 场景实体生命周期

v2.3 把“道具”和“可互动对象”统一视为场景实体，但不把角色人格或普通记忆并入该状态。v2.3 实体定义至少包含：

```json
{
  "id": "prop_tuning_bells",
  "label": "三只调音风铃",
  "public_hint": "铃片会对糖霜气泡产生不同节奏的回应。",
  "entity_type": "prop",
  "capabilities": ["investigate", "use_prop"],
  "initial_state": {
    "visibility_state": "hidden",
    "interaction_state": "untouched",
    "ownership_state": "world"
  }
}
```

v2.2 `stage_props.available_from_node` 由 Compiler 规范化成 v2.3 initial state 和 entity transition，Runtime 不再单独计算一次性 `available_prop_ids`。

实体状态拆成正交维度，避免把 NPC、地点和道具强行套进同一条“取得—使用—耗尽”流程：

```text
visibility_state
  hidden       尚未进入玩家可见范围
  available    当前可调查、询问或使用
  unavailable  当前暂不可互动，但已发生事实仍保留

interaction_state
  untouched    尚未产生有效互动
  interacted   已进行调查、询问或操作
  resolved     当前剧情用途已经完成，除非显式重新开放

ownership_state（仅 applies_to: prop）
  world        仍在场景中
  held         已由玩家或共演角色取得
  consumed     已消耗或永久交付
```

建议协议：

```json
{
  "entity_transitions": [
    {
      "entity_id": "prop_tuning_bells",
      "dimension": "visibility_state",
      "from": "hidden",
      "to": "available",
      "when_node_completed": "node_lantern_stall"
    },
    {
      "entity_id": "prop_tuning_bells",
      "dimension": "interaction_state",
      "from": "untouched",
      "to": "interacted",
      "when_action_completed": "node_borrow_bells"
    }
  ]
}
```

规则：

1. 节点 commit 时应用实体状态转换，Scenario Board 随 committed state 更新。
2. 实体必须声明 `entity_type` 和支持的能力；NPC、地点和场景锚点不得出现 `ownership_state`。
3. `scenario_state.entities_by_id` 保存当前实体状态；Board、Choice Policy 和 Persona 都从该 committed map 投影，不维护各自的可用集合。
4. 实体是否仍然为真、当前是否可互动、是否仍是叙事焦点分开记录。
5. `interaction_state=resolved` 的实体默认不重复推荐；新线索可以通过显式 transition 重新开放为 `available + interacted`。
6. 不使用 intensity、耐久值或其它数值模拟。
7. v2.3 第一版的作者级 `state_diff` 只允许追加结构化 fact；通用 `remove / modify` 从 v2.3 作者协议移除并由 Compiler 拒绝。实体变化使用显式 transition，事务回滚使用 State Preview，不用通用字段猜测修改语义。

## Evidence Condition v2.3

剧情凭据继续是布尔式剧情闭环，不是分数。v2.3 把来源从单一线索组合扩成受限的确定性条件树：

```json
{
  "id": "evidence_first_batch_ready",
  "title": "第一炉具备开张条件",
  "all_of": [
    {"clue_discovered": "clue_bells_control_timing"},
    {"prop_used": "prop_osmanthus_sugar"},
    {"question_resolved": "question_brand_shape"},
    {"node_completed": "node_customer_smiles"},
    {
      "fact_committed": {
        "subject": "first_batch",
        "predicate": "is_ready_for",
        "object": "festival_customer"
      }
    }
  ]
}
```

允许的第一版组合运算：

1. `all_of`：全部子条件成立。
2. `any_of`：至少一个子条件成立，用于表达预写主线或动态分支形成的替代合法路径。

条件树最多嵌套两层；不支持 `eval`、任意表达式、脚本回调、数值比较和作者自定义代码。

允许的第一版条件原语：

1. `clue_discovered`
2. `prop_used`
3. `question_resolved`
4. `node_completed`
5. `fact_committed`

动态路径形成凭据时遵守：

1. evidence 定义和 evidence id 必须来自 Story JSON，并在 Compiler 阶段建立索引；Runtime 不得临时创造 evidence 规则。
2. 动态节点可以发现已声明 clue、改变已声明 prop/question/node 条件，或提交符合 `dynamic_boundaries.allowed_fact_predicates` 的结构化 fact。
3. 如果需要让预写路径和动态路径都能形成同一凭据，作者使用 `any_of` 明确声明两组条件。
4. Overlay 中动态 clue 可以作为公开叙事结果。它只有来自 Story 预先声明的 clue template 时才能通过稳定 template id 满足 `clue_discovered`；完全临时生成的 clue 只能通过受限 `fact_committed` 条件参与凭据。

## 多结局解析

v2.3 正式结局应由 terminal node 明确声明，而不是根据用户当前 intent 猜测：

```json
{
  "node_id": "node_ending_first_batch",
  "node_type": "ending",
  "ending_id": "first_starlight_batch"
}
```

对应 ending attractor 必须声明 `completion_role: primary | optional`。`primary` 表示主目标正式收束，必须存在确定性完成路径；`optional` 可以依赖随机事件或动态凭据分支，但同样必须预先声明 ending id 和 evidence，不能由 Runtime 临时创造。

解析顺序：

```text
terminal node 提供 ending_id
→ Ending Engine 查找该 ending attractor
→ 检查 required / forbidden evidence
→ 检查玩家是否仍在追问或明确拒绝落幕
→ story_ending 或继续演绎
```

旧 `strength` 只保留给 v2.1 / v2.2 兼容路径，不参与 v2.3 正式结局选择。

动态分支的结局边界：

1. 动态节点不能定义新的正式 `ending_id` 或修改 `ending_attractors`。
2. 动态分支结束时必须桥接到静态收束节点，或生成引用 Story JSON 已声明 `ending_id` 的动态 terminal candidate。
3. 动态 terminal candidate 仍需 Ending Engine 检查 required / forbidden evidence；证据不足时保持 session 运行并返回公开下一步方向。
4. `user_exit` 始终独立于动态 terminal 和正式结局。

## 统一回合事务

v2.3 的 Full 和 Economy 必须共享：

```text
Turn Request
→ Resolve Choice / Encode Free Input / Select Random Event
→ Decide Progress Kind（graph_progress / random_event / dynamic_branch / bridge / roleplay_response / clarification / redirect / user_exit / story_ending / none）
→ Candidate Filter
→ Turn Plan
→ State Preview
→ Build Public Context
→ Render Narration + Persona
→ Validate
→ Commit / Rollback
→ Build Board + Trace + Suggestions
```

模块职责冻结：

1. `progress_resolver.py`：把 Turn Request 解析为 graph、random event、dynamic branch、bridge、redirect 或 exit 候选；不写状态。
2. `turn_transaction.py`：持有 Turn Plan、State Preview、Validator 结果和 Commit / Rollback 编排；Full 与 Economy 共用。
3. `state_manager.py`：先在 Preview 副本应用 State Delta 并用确定性 Evidence Evaluator 计算新凭据，Validator 通过后提交同一副本；它是静态状态、实体状态、随机状态和 Runtime Graph Overlay 的唯一写入口。
4. `graph_router.py`：只查询 Compiled Story 与 Overlay 联合视图，不生成或提交动态对象。
5. `runtime.py`：负责调用 Renderer、组织公开响应和 session 保存，不再分别维护两套提交顺序。
6. `session_store.py`：原子保存 `client_turn_id` 结果索引、story revision 与私有快照、random state、Overlay、committed state 和双层 Trace。

Turn Plan 至少包含：

```json
{
  "input_kind": "choice",
  "client_turn_id": "turn_01J...",
  "progress_kind": "graph_progress",
  "choice_id": "choice_check_recipe_card",
  "action_type": "investigate",
  "target_id": "prop_recipe_card",
  "node_id": "node_read_recipe_clue",
  "state_delta": {
    "story_facts": {"add": []},
    "entity_transitions": [],
    "scenario_changes": {
      "used_prop_ids": [],
      "discovered_clue_refs": [],
      "resolved_question_ids": []
    },
    "random_changes": {
      "draw_advance": 0,
      "event_instances_to_add": [],
      "eligible_after_turn_updates": {}
    },
    "overlay_changes": {
      "nodes_to_add": [],
      "edges_to_add": [],
      "entities_to_add": [],
      "clues_to_add": [],
      "choices_to_add": [],
      "facts_to_add": []
    }
  },
  "allowed_public_facts": [],
  "newly_revealed_facts": [],
  "forbidden_reveals": [],
  "ending_candidates": []
}
```

Persona 只接收 `allowed_public_facts + newly_revealed_facts`。Validator 可以读取 `forbidden_reveals`，但这些内容不能进入 Persona prompt 或公开 Trace。

当前实现由 `fact_policy.py` 从 committed `Scenario Projector`、本轮计划发现的线索/疑点和静态/动态私有定义统一计算三组事实。Director 重路由会替换旧候选的 `newly_revealed_facts`；Runtime 自动事件则在主行动事实上追加。即将公开的线索正文属于 `newly_revealed_facts`，不会同时进入 `forbidden_reveals`，但该线索的 `hidden_meaning / private_meaning` 仍保持禁止揭示。

State Delta 是白名单协议：第一版只允许追加 fact、实体 transition、剧本账本追加项、随机状态更新和 Overlay 新对象。`confirmed_evidence_ids / used_evidence_ids / ending_id` 不允许由 Planner 直接填写；它们分别由 Evidence Evaluator 和 Ending Engine 根据应用后的 Preview 推导，Validator 通过后再随同一事务提交。

事务规则：

1. 同一 `client_turn_id` 只允许形成一个最终结果；已 committed、rolled_back 或 blocked 的结果都可幂等读取。
2. Renderer 失败或高级 LLM Validator 不可用时，不得重新解析输入或重新抽取随机事件；只能基于同一 Turn Plan 使用安全 fallback。
3. 确定性协议校验失败必须 Rollback；文本演绎失败可以降级 Renderer，但不得改变 State Delta。
4. State Manager 在 session 副本上应用 State Delta，再从该候选 committed state 生成 Board、Public Trace、Suggestions 和幂等响应快照；Session Store 将候选 session 与响应索引一次原子保存，保存成功后才返回。保存失败不得污染已持久化 session。
5. Economy 与 Full 共用同一份已解析 Turn Plan；Renderer 不能重新解析输入或修改 State Delta。确定性 choice 在相同初始状态下必须提交相同结果，自由输入的模式差异只能影响渲染文本。
6. 同一 session 的 mutation 必须串行执行：在 session 级锁内重新加载最新状态、检查 `client_turn_id`、提交候选副本并原子保存。并发请求不能基于同一个旧版本各自提交。
7. session 保存结果记录单调 `state_revision`；Turn Plan 保存其 `base_revision`，revision 不一致时必须重新解析或返回可重试冲突，不能把旧 Preview 提交到新状态。

## Context Policy v2.3

Context Policy 借鉴 World Info 的动态装配思想，但触发源必须是 committed NSN state，不是聊天关键词猜测。

v2.3 核心动态闭环第一轮只实现 `activate_when / deactivate_when / inclusion_group / priority`。以下 `sticky_turns / cooldown_turns` 保留为协议字段和后续实现，不作为第一轮交付阻塞项；GM redirect anchor 自身的 cooldown 不受此延期影响。

上下文条目建议包含：

```json
{
  "id": "context_current_frosting_problem",
  "public_text": "糖霜亮度已经稳定，但味道仍需确认。",
  "activate_when": {"clue_discovered": "clue_bells_control_timing"},
  "deactivate_when": {"question_resolved": "question_flavor_balance"},
  "inclusion_group": "current_problem",
  "priority": 80,
  "sticky_turns": 2,
  "cooldown_turns": 1
}
```

规则：

1. `priority` 只用于上下文选择顺序，不参与剧情、关系或结局判断。
2. 同一 inclusion group 默认只选择一个最高优先条目，避免 Persona 同时收到互相冲突的场面提示。
3. 后续实现的 `sticky / cooldown / delay` 只服务 GM 提示和上下文节奏，不能控制关键线索是否提交。
4. 第一版仍应优先使用节点、线索、疑点和道具的结构化条件，不接 embedding 或自由 Lorebook。

## GM Progress / Redirect Policy v2.3

GM 的第一职责不是把所有图外输入拉回，而是判断怎样把玩家行动接进当前故事：

```text
命中预写图
→ graph_progress

命中合格随机事件
→ random_event

图外但与世界和角色权限兼容
→ dynamic_branch

与主图暂时不相连，但可以保留玩家意图
→ 生成 bridge node 后回主图

违反硬边界或无法形成可演事件
→ redirect
```

只有最终进入 `redirect` 时才使用以下拉回策略。

`redirect_anchors` 从自然语言数组升级为稳定对象：

```json
{
  "id": "redirect_frosting_bubbles",
  "public_text": "锅里的糖霜又冒出一串急促星泡。",
  "available_when": {
    "entity_state": {
      "entity_id": "prop_frosting_pot",
      "dimension": "visibility_state",
      "equals": "available"
    }
  },
  "priority": 80,
  "cooldown_turns": 2
}
```

拉回顺序建议：

1. 第一次跑题：承认玩家输入，再提醒当前目标。
2. 第二次连续跑题：使用当前道具或场面反应拉回。
3. 第三次及以后：明确列出当前可达动作，但不惩罚、不扣分、不推进关键线索。
4. 结局凭据不足：说明仍缺剧情闭环，只展示当前可获得的公开方向，不泄露隐藏答案。

每次拉回应记录实际 `redirect_anchor_id`，并对刚使用的 anchor 进入 cooldown，避免连续重复同一句话。

## 双层 Trace v2.3

### Public Scenario Trace

玩家可见，每轮必须存在：

```json
{
  "input_kind": "choice",
  "progress_kind": "graph_progress",
  "commit_result": "committed",
  "action_type": "investigate",
  "action_label": "调查",
  "summary": "调查：半张配方卡",
  "revealed_clue_titles": ["三次铃声"],
  "confirmed_evidence_titles": [],
  "resolved_question_texts": [],
  "triggered_event_titles": [],
  "blocked_label": ""
}
```

`progress_kind` 解释本轮怎样接入故事，使用冻结枚举：

```text
graph_progress
random_event
dynamic_branch
bridge
roleplay_response
clarification
redirect
user_exit
story_ending
none
```

`commit_result` 解释事务结果，使用冻结枚举：

```text
committed
blocked
rolled_back
exited
ended
```

例如动态分支通过 Resolver 但被 Validator 拒绝时，记录 `progress_kind=dynamic_branch`、`commit_result=rolled_back`，不能把推进来源和提交结果压成一个含混的 outcome。

### Internal Engine Trace

只供开发与真实运行验收：

1. 输入如何解析成 choice 或 free input。
2. 候选节点列表及每个过滤原因。
3. 本轮注入了哪些公开上下文。
4. 哪些隐藏信息被排除。
5. 为什么选择 graph、random event、dynamic branch、bridge 或 redirect。
6. Validator 层级、结果和 rollback 原因。
7. Choice Policy 为什么生成下一组按钮。
8. 提交前后 state delta。

Internal Engine Trace 不进入普通聊天、角色记忆或玩家默认界面。

## 分支质量规则

1. 一个节点只解决一个可感知的小推进。
2. 每个 choice 必须在本轮得到角色、旁白、道具或状态上的即时回应。
3. 多条路径可以汇合，但汇合前至少保留一个能体现玩家选择差异的 callback。
4. 每条路径只能引用该路径已经公开的角色、地点、道具、线索和概念。
5. 错误行动可以产生小麻烦、延迟或救场路径，但不能无提示进入硬死路。
6. 正式结局前必须有收束节点，呈现凭据如何共同托住最终行动。

## v2.3 后续扩展边界

以下能力有参考价值，但不进入 v2.3 第一轮实现：

1. **Session Checkpoint**：从某个 committed turn 克隆独立 theater session，原分支保持不变。
2. **Speaker Policy**：多角色剧场按节点、玩家点名和剧本顺序选择发言者；每轮只注入当前角色卡。
3. **长剧本分层摘要**：超过约 50 回合后，再评估事件摘要、跨事件摘要和按需召回。
4. **开发者 Trace Inspector**：在独立调试入口显示候选过滤、上下文注入和 Validator 结果。

## v2.2 已完成实施基线

以下七个阶段记录 v2.2 已有实现和历史验收，不是 v2.3 待办清单。v2.3 的新增实施路线在后文单独定义。

### 第一阶段：只加协议校验和公开剧本卡

目标：让 Story Package 可以声明 `scenario_card / stage_props / clues / evidence_rules`，并让前端能读取公开剧本卡。

涉及文件：

1. `services/theater/story_loader.py`
   - 校验 `scenario_card` 基础结构。
   - `public_story()` 暴露安全的剧本卡字段。
2. `tests/unit/test_theater_story_loader.py`
   - 增加通用剧本卡加载测试。
   - 验证不暴露 hidden meaning。
3. `config/theater/stories/starlight_festival_test_story.json`
   - 给示例剧本添加 `scenario_card / stage_props / clues / evidence_rules`。

验收：

1. `/api/theater/stories` 能返回剧本卡。
2. 任意 v2.2 剧本的公开信息包含地点、身份、目标、舞台道具、疑点和 GM 拉回方式。
3. hidden meaning 不出现在 public story。

### 第二阶段：Story State 加线索板

目标：运行时能记录可用道具、已使用道具、已发现线索、已确认 evidence、剧情锚点和未解疑点。

涉及文件：

1. `services/theater/state_manager.py`
   - 初始化 `scenario_state`。
   - commit NSN 节点时同步 `script_action` 字段。
2. `services/theater/graph_router.py`
   - 根据 `requires_clues` 过滤不可达节点。
3. `tests/unit/test_theater_state_manager.py`
   - 测试 clue discovery commit。
   - 测试 evidence commit。
4. `tests/unit/test_theater_graph_router.py`
   - 测试证据不足时不能进入执行节点。

验收：

1. 使用关键道具后出现对应 clue id。
2. 未发现必要线索前不能执行最终目标节点。
3. 所有状态仍保留在 theater 私有 session。

### 第三阶段：Choice Policy 剧本杀化

目标：按钮从普通下一步变成调查、询问、使用道具、对照、推理、执行、整理和离场。

涉及文件：

1. `services/theater/suggestion_engine.py`
   - 根据 action type 和 open questions 排序按钮。
   - 公开响应只暴露安全 `action_type`，不暴露 `target_id`、线索 id 或内部剧本动作对象。
   - economy 模式继续禁用自由输入提示。
2. `services/theater/graph_router.py`
   - `suggestion_options_for_active_node()` 保留内部 action metadata。
3. `tests/unit/test_theater_router.py`
   - 验证开场按钮优先围绕当前剧本卡声明的舞台道具。
   - 验证线索发现后推荐与当前目标相关的对照、使用道具或整理信息按钮。

验收：

1. 玩家开场看到的是剧本动作，不是泛泛“你想怎么做”。
2. 后续按钮能解释为当前疑点的下一步。
3. 选项不展示任何内部字段。

### 第四阶段：Persona 防剧透

目标：猫娘只能基于已发现线索转述，不提前揭开 hidden meaning。

涉及文件：

1. `services/theater/persona_engine.py`
   - prompt 增加已发现线索公开摘要。
   - fallback 也不能引用未发现线索。
2. `services/theater/validator_engine.py`
   - Level 1 检查输出是否包含未发现关键线索关键词。
   - Level 2 Validator 在 full 模式下检查是否剧透。
3. `tests/unit/test_theater_router.py`
   - economy 模式下 Persona 转述仍保留当前线索边界。
   - full 模式下未发现关键线索前，猫娘不能直接说出隐藏用途。

验收：

1. 猫娘可以提示“还差一个关键线索”。
2. 猫娘不能提前说出未发现道具的隐藏用途。
3. Validator 拒绝剧透后不提交错误节点。

### 第五阶段：GM 跑题拉回

目标：用户脑洞大开或输入剧本外话题时，GM 不硬拒绝，而是用当前道具、场面压力或角色目标把话题带回剧本。

涉及文件：

1. `services/theater/runtime.py`
   - 自由输入或 graph 无命中时生成 `redirect` 类型回合。
   - 记录 `scenario_trace.redirected` 和 `redirect_anchor`。
2. `services/theater/narrator_engine.py`
   - 增加 GM 拉回旁白兜底：先承认玩家输入，再把镜头推回当前舞台。
3. `tests/unit/test_theater_router.py`
   - 验证当前 v2.2 economy 模式的自由输入不推进节点，但返回当前可用道具和剧本按钮；该断言不作为 v2.3 行为要求。
   - full 模式跑题输入不能越过 Script Anchor Filter。

验收：

1. 用户说“我们去宇宙开甜品店”时，不进入宇宙剧情。
2. GM 可以回应“这个脑洞先记下，但眼前的道具又有反应了”，并给回当前剧本按钮。
3. redirect 不记为失败分，也不写普通记忆。

### 第六阶段：Ending Evidence 剧本杀化（已完成）

目标：正式结局由 evidence_rules 闭环触发。

涉及文件：

1. `services/theater/ending_engine.py`
   - 支持 ending evidence 引用 `confirmed_evidence_ids`。
   - 保持现有 committed `narrative_facts` 兼容。
2. `config/theater/stories/starlight_festival_test_story.json`
   - 将 `first_starlight_batch` 的 evidence_required 改成 v2.2 evidence。
3. `tests/unit/test_theater_ending_engine.py`
   - 测试 evidence 未齐不结局。
   - 测试 evidence 齐全且用户接受收束才 story_ending。

验收：

1. 走到 ending phase 但证据不足时不能正式结局。
2. 证据齐全后才能达成 `first_starlight_batch`。
3. 用户主动退出仍是 `user_exit`。

当前状态：

1. `ending_attractors.evidence_required` 已支持旧版 committed fact 和 v2.2 `confirmed_evidence_ids` 两种写法。
2. 《星灯祭的糖霜危机》已改成由 `evidence_glow_timing_ready` 与 `evidence_first_batch_ready` 触发正式结局。

### 第七阶段：Session Trace 复盘

目标：排查“剧情烂还是代码错”时能直接看懂每轮问题。

涉及文件：

1. `services/theater/runtime.py`
   - assistant turn 和 user turn 增加 `scenario_trace`。
2. `services/theater/session_store.py`
   - 不需要改协议，只持久化新增字段。
3. `tests/unit/test_theater_router.py`
   - 验证每轮 trace 记录 action type、target、prop、clue、evidence、redirect、blocked reason。

验收：

1. 最新演绎记录能看出每轮是否使用道具、发现线索或发生 GM 拉回。
2. 选项错位能定位为路由、剧本、Persona 或 Choice Policy 问题。
3. Public Scenario Trace 可以随响应安全返回；候选过滤、Prompt 和 Validator 细节继续只留在私有审计。

## v2.3 实施路线

实施原则：每个阶段都要形成最小纵向闭环，不能先堆完所有后端协议再第一次接前端。每阶段至少验证“请求进入 → 状态提交或回滚 → session 保存 → 公开响应 → 前端消费或契约测试”。

### 第一阶段：协议冻结与 Story Compiler

目标：先把 v2.3 剧本定义变成可判定、可失败的协议。

涉及范围：

1. 冻结 `scenario_protocol / features / Turn Request / choice_id / questions / entity state / random state / dynamic boundaries / Overlay / Evidence Condition / Public Trace` schema。
2. `story_loader.py` 增加 `scenario_protocol / features` 校验。
3. 新增 `story_compiler.py`，构建静态 ID 索引和引用关系；运行包按 Story JSON 内容哈希在进程内缓存。
4. 校验 graph 可达性、随机事件模板、动态边界、死胡同、疑点闭环、静态 evidence 可满足性、确定性保底路径和 ending 可达性。
5. 为 Compiler 错误、警告、缓存失效和 v2.2 兼容增加独立单元测试。

验收：

1. 半配置 v2.3 剧本不能静默进入 Runtime。
2. 未知 prop/clue/question/evidence/ending 引用在加载时直接报错。
3. 星灯祭能够输出零硬错误的结构化 validation report，并把剩余质量提醒与协议阻断明确分开。
4. Compiler 不声称验证未生成的动态分支；该边界在 validation report 中明确可见。

当前状态：

1. 已实现 `scenario_protocol: "2.3"` 严格启用和未知协议拒绝，v2.1/v2.2 继续走原校验链路。
2. 已实现节点、出边、实体、道具、线索、疑点、凭据、结局、随机事件和 choice 索引，以及按 Story 内容哈希缓存和深拷贝隔离。
3. 已实现 feature 原子性、稳定 choice、Condition DSL、图可达性、非结局出口、实体 transition 状态图、动态桥接点、结局凭据和追加式 `state_diff` 校验。
4. 已实现静态条件生产者固定点、随机事件 eligibility 可达性、random-only / impossible evidence 分类、terminal 自依赖阻断和 primary 结局确定性保底分析。
5. 已增加结构化 `validation_report`、加载阶段阻断和 v2.2 只读迁移预检；预检会继续检查顶层 question 存在但公开卡或动作仍引用中文文案的半迁移状态。全部本地小剧场自动化当前为 `317 passed, 4 skipped`，跳过项均为需要显式模型环境的真实模型 smoke。
6. 已实现开场疑点无 resolver、闲置舞台实体、线索无生产者、线索未参与 Evidence、Evidence 未被 Ending 使用和多选项缺 callback 等首批质量预警；连续空转节点、分支场面差异和结局前收束节点仍待后续覆盖。
7. 星灯祭已显式切换 `scenario_protocol: "2.3"` 和 9 个 feature，稳定疑点引用、Evidence 条件树与对象化 GM 拉回锚点全部写回真实 Story；迁移预检为零阻断，随机事件、两条 Evidence 和 primary Ending 都存在确定性静态路径。
8. 星灯祭 Full/Economy 已各自通过 20 轮结构化 Choice 主线、图外纸风车分支安全返回和 callback 差异验证；真实 Chromium Economy 已覆盖六道具、玩家专属事件和正式结局。42 个 Choice callback、糖霜暴走救场路径和结局文案已经完成。
9. 已新增统一 Evidence Evaluator，v2.2 `requires_clues` 与 v2.3 `all_of / any_of` 共用同一提交入口；同时删除 `_scenario_evidence_rules` 临时状态和 `event_pool` 遗留 API。
10. 已新增 Scenario Projector、Condition Engine 和 Entity Engine：v2.3 可以初始化实体状态、按节点/动作/Condition 提交 transition，并把稳定 question ID、实体和 GM 锚点投影成公开文案；v2.2 session 的序列化字段保持不变。

### 第二阶段：稳定 Choice 与实体生命周期

目标：前端按钮不再依赖 label 回传，道具能随剧情真实开放、使用、失效和重新开放，并尽早跑通一条真实 API 纵向链路。

涉及范围：

1. `main_routers/theater_router.py / runtime.py` 接收结构化 Turn Request，并按 `client_turn_id` 提供幂等结果。
2. `suggestion_engine.py / graph_router.py` 生成并解析 `choice_id`。
3. `state_manager.py` 提交按维度区分的 entity transitions。
4. `scenario_state` 改用稳定 question id，并记录实体 visibility / interaction / ownership 状态。
5. `static/js/theater.js` 先完成 choice_id 提交与 legacy message 兼容验证，不等待完整 Board UI。
6. 旧 v2.2 label 提交保留有限兼容，不继续作为 v2.3 主协议。

验收：

1. 翻译或动态改写按钮 label 不影响路由。
2. 星灯祭六件舞台道具按节点逐步开放。
3. 已使用道具不会空转；新线索可以显式重新开放旧道具。
4. 重复 `client_turn_id` 不会重复推进节点。
5. 当前前端按钮已经通过真实请求提交 choice_id，不再依赖显示文案反查。

当前状态：

1. Router / Runtime 已解析 `choice / free_input / random_event / user_exit` 四类请求，并拒绝互相冲突的字段组合。
2. v2.3 suggestion options 会公开稳定 `choice_id`；Graph Router 只在当前 committed node 的可见候选中解析，并锁定对应目标节点。
3. Full 与 Economy 已共用该 choice 解析结果；v2.2 suggestion options 和 legacy message turn 的公开与存档形状保持不变。
4. 同一 session 的结构化 mutation 已在进程内锁中串行执行；候选 session 与首次公开响应按 `client_turn_id` 一次原子保存。并发重复请求和后续重试都会精确返回同一响应，不重复推进节点、创建事件或消费剧情状态。
5. 玩家主动提交的 `random_event` 已能按 committed state 筛选当前可用作者事件，并形成稳定 `event_instance_id`、random state、线索 / Evidence 提交和 Public Trace；重复请求精确回放，同一一次性事件换新 turn 后不能再次触发。
6. Runtime 自动事件抽取与事件冷却重新开放已经完成；前端结构化 Choice 已通过 Playwright 捕获真实请求体，确认不再回传显示文案。星灯祭 20 轮后端主线已验证实体生命周期，六件道具的真实桌面界面变化仍待浏览器完整验收。

### 第三阶段：统一回合事务、动态分支与防剧透

目标：Full 和 Economy 共享同一个 Turn Plan、Preview、Validator、Commit 和 Projector；自由输入可以进入随机事件、动态分支、桥接或拉回，并能跨重启继续运行。

涉及范围：

1. 新增 `progress_resolver.py / turn_transaction.py`，Runtime 改为调用统一事务。
2. Progress Resolver 区分 graph/random event/dynamic branch/bridge/redirect/user exit。
3. Turn Plan 生成 allowed/newly revealed/forbidden facts 和完整 State Delta。
4. `state_manager.py` 提交 Runtime Graph Overlay、random state、追加式 story facts 和显式 entity transitions。
5. `graph_router.py` 查询 Compiled Story 与 Overlay 联合视图。
6. Persona 只读取公开上下文；Validator 检查隐藏事实、用户身份越权和动态边界。
7. Validator 拒绝时生成 `progress_kind + commit_result=rolled_back` Public Trace。
8. Economy 的 choice 走确定性解析；Economy 自由输入最多调用一次受约束 Progress Resolver。

验收：

1. 同一 choice 在 Full / Economy 下提交相同剧情状态。
2. 合格自由输入能创建随机关键事件或 session 私有动态节点。
3. Persona 在两种模式中看到相同的公开线索边界。
4. 任意失败回合都有非空 Trace，且不推进关键线索。
5. 保存并重启后可以从动态节点继续选择、继续扩写或桥接回主图。
6. 相同 seed 和 committed state 的 Runtime 自发事件抽取结果可复现。
7. 重复请求不会重复创建事件实例、动态节点或动态实体。

当前状态：

1. 已新增 `turn_transaction.py`，在 session 级进程内锁中重新加载状态、检查结果索引和可选 base revision，并将候选 session、递增 revision 与公开响应一次原子保存；结构非法、过期 Choice、不可用随机事件、v2.2 协议拒绝、session 不存在或已结束不会污染结果索引或推进 revision。
2. 已新增稳定 Random State。玩家可以显式触发当前 eligible 的作者事件；事件实例由 `client_turn_id + event_id` 稳定派生，提交后可以发现线索、确认已有 Evidence，并生成 `progress_kind=random_event` 的公开 Trace。
3. Full 与 Economy 的结构化 choice 和玩家主动随机事件已复用同一提交入口，重复 `client_turn_id` 会返回首次完整响应，不会再次执行 Persona 或状态 mutation。
4. 玩家显式触发与 Runtime 作者事件自动附加均已实现。自动事件只在成功 `graph_progress` 后从当前 eligible 的 `runtime / player_or_runtime` 候选中抽取，使用稳定 seed、`draw_index`、作者候选顺序和 `selection_weight` 复现结果；它只追加子 Trace 和 State Delta，不覆盖玩家主推进。`once / cooldown`、玩家来源隔离、实例幂等，以及 blocked / rolled back / duplicate 不推进抽取和冷却时间轴均已有测试。模型动态生成事件仍未实现。
5. Runtime Graph Overlay 已完成空状态、命名空间与动态边界校验、State Manager 原子追加、静态/动态联合路由、动态 Choice、批准桥接点、session 保存恢复和旧集合懒修复。动态实体与线索只按公开字段进入 Board、Persona 和 Trace，私有说明不泄漏；动态事实可以满足 Story 已声明 Evidence，但不能创建 Evidence 或 Ending。
6. `progress_resolver.py` 已接入结构化 `free_input`。它优先匹配当前公开 Choice、eligible 随机事件 public label、作者启用的 forbidden outcome，以及含明确行动词的动态行动；硬禁止和确定性命中均不会调用模型。普通未命中时最多调用一次 `dynamic_candidate_engine.py`：模型只能选择允许动作、已有公开实体，或抽取玩家原话中连续出现的一个新实体名称和一段明确观察。服务端生成稳定 ID、实体状态、动态线索、节点和批准桥接边，再经过 Overlay Validator、Dynamic Validator、事实边界和候选副本事务。
7. 当前 session 锁是单进程运行时锁；保存前会重读 session，若其它进程已推进 revision 则返回可重试冲突，同一 client turn 已被抢先保存时直接回放。它提供乐观并发保护，但不能描述成分布式锁。
8. 请求事务、玩家主动随机事件、Economy 图回合、图节点公开指令和 GM-Lite 提示已归入 `turn_coordinator.py`；Full 与脚本路径共用唯一 `_finalize_turn`，Full Validator 最多重路由一次。Board / Trace 快照一致性和最多两次校验都已有测试固定。
9. v2.3 session 从 `state_revision=0` 启动；每个 `ok=true` 结构化回合保存实际 `base_revision` 并递增一次。相同 base 的不同 client turn 并发时只有一个提交，错误请求不递增，v2.2 session 不增加 revision 或 protocol 字段。
10. 结构化请求会生成私有 Turn Plan，保存 input、base revision、稳定 node / event / action / target、追加式 State Delta 和 Resolver Trace；Turn Transaction 在深拷贝候选 session 上执行，冲突或失败时丢弃候选。公开响应返回实际 `progress_kind / commit_result`，Validator 最终拒绝形成 `rolled_back` 非空 Trace，GM 拉回形成 `blocked` Trace。
11. Turn Plan 已生成完整三段事实边界。Persona 的 Story State 改为显式字段白名单，只接收阶段、关系摘要、Scenario 公开投影和 `allowed + newly revealed`；Level 1 Validator 拦截逐字隐藏事实泄露，Level 2 在关键 Full 回合比较语义等价泄露。语义失败使用固定私密原因并立即终止重路由，失败结果、公开 Trace 和 rollback 日志都不回显隐藏正文。Full、Economy 图节点、玩家主动随机事件和动态分支均执行确定性输出校验。
12. 动态分支先通过独立确定性计划校验，再在候选 Story State 中提交 Overlay 和节点；公开演绎通过后才替换正式 session，失败时保留 `progress_kind=dynamic_branch / commit_result=rolled_back`，不推进 Overlay 或随机冷却时间轴。Runtime 自动作者事件也有独立引用与禁写字段校验；附加事件失败只进入 Turn Plan 的私有 Internal Trace，不回滚已经成功的玩家主行动。
13. 第一版模型补位已覆盖两种安全用途：把玩家口语同义表达映射到当前公开实体；把玩家明确带入的新对象和明确说出的观察转换成最多一个动态实体与一个动态线索。名称和线索正文必须逐字存在于用户输入，私有定义固定为空；模型捏造跨度、夹带未知字段、引用隐藏实体、违反 capability、超时或不可用时不产生 Overlay，并安全降级为 `roleplay_response`，不显示成玩家越界。

### 第四阶段：Evidence、Ending 与 GM 控场

目标：剧情凭据支持预写图和动态分支的合法来源，多结局按 terminal node 解析，GM 只在无法吸收或桥接时拉回。

涉及范围：

1. evidence conditions 支持受限 `all_of / any_of` 和 clue/prop/question/node/fact 原语。
2. ending node 显式声明 `ending_id`。
3. 随机事件和动态节点只能通过 Story 已声明的 evidence 条件形成凭据，不能创建新的 evidence 或 ending 定义。
4. redirect anchor 使用稳定 ID、条件、优先级和 cooldown。
5. Public Trace 记录实际 progress kind 或 anchor；Internal Trace 记录候选过滤和下一组选项依据。

验收：

1. 多结局不再依赖当前 intent 猜测。
2. 结局凭据不足时只展示可公开的下一步方向。
3. 图外输入优先被吸收为动态分支或桥接节点，不会被机械判成跑题。
4. 真正需要拉回时不会连续重复同一句 GM 提示。
5. 主目标至少有一个正式结局在随机事件不触发时仍可通过确定性主线或保证触发的公开救场路径达成；随机专属可选结局不承担主目标保底。
6. 动态 terminal 只能引用已声明 ending_id，且不能绕过 evidence。

当前状态：

1. v2.3 Ending Engine 已改为以当前 active terminal node 和匹配的 `ending_id` 作为正式结局真值；全局 phase 或凭据提前满足都不会再让普通节点落幕。
2. v2.1/v2.2 继续沿用原 phase / strength 兼容语义；玩家主动退出仍与正式 story ending 分开记录。
3. 星灯祭主线已验证两条 Evidence 先确认、后在 `node_ending_first_batch` 实际消费，提前收束不会泄露候选 ending id。
4. v2.3 GM Anchor 已使用统一 Condition Evaluator、整数优先级、成功剧情回合冷却和最久未用轮换；Public Board / Trace 只展示公开文案。
5. Anchor 使用顺序与 eligible-after 映射已写入 v2.3 scenario state；清除进程内 session 状态后继续拉回仍会按存档历史换句，v2.1/v2.2 不新增该存档字段。

### 第五阶段：前端消费与真实演出验收

目标：现有小剧场界面真正消费 v2.3 公开协议。

涉及范围：

1. 展示剧本卡、当前道具、已发现线索、疑点和剧情凭据。
2. 按 `action_type` 渲染按钮，并提交 `choice_id / client_turn_id / input_kind`。
3. 提供独立自由输入入口，Economy 不再隐藏该能力。
4. 展示每轮 Public Scenario Trace 的 `progress_kind / commit_result`。
5. 通过真实浏览器和桌面端演出验证 Full / Economy。

验收：

1. 玩家不用读内部字段也能理解每轮发生了什么。
2. Board、Trace、按钮和角色对白读取同一 committed state。
3. 不污染普通聊天、语音、小游戏、角色记忆和独立页面链路。
4. 网络重试不会在界面或后端产生重复回合。

当前状态：

1. `theater.js` 已按 `state_revision` 识别 v2.3 session；Choice 点击提交稳定 `choice_id / client_turn_id / base_revision`，文本框提交 `input_kind=free_input`。403 token 刷新与瞬时网络错误重试都会复用同一份序列化请求 body，不会生成第二个 client turn id；revision 冲突只拉取最新公开快照并要求玩家重新选择，不自动重放旧动作。
2. 页面已增加公开 Scenario Board，展示可用/已使用道具、已发现线索、已确认剧情凭据、待解/已解决疑点；空的旧剧本板不会占据日志空间。
3. 页面已增加本轮 Public Trace，展示中文动作与摘要，并用公开名称列出使用道具、发现线索、确认/消费凭据和自动事件；内部 target、reason、节点和 Story State 不进入 DOM。
4. 桌面端 Board 与日志并列；`390×844` 下 Board、日志、Trace 进入单一滚动流，最新 Trace 自动滚入视野，选项横向滚动，输入区保持在视口内。8 个 locale 已同步新增面板文案。
5. Playwright 已验证主动随机事件、结构化 Choice、结构化 `user_exit` 的连续 revision 请求，以及每轮 Board/Trace/事件列表刷新；也已验证页面刷新优先恢复本地 session、本地指针缺失时恢复服务端 active session、断网后重发完全相同的 `client_turn_id` 和请求体，以及 revision 冲突时刷新界面而不重复提交。同时验证移动端输入区仍在视口内，theater 资源不注入 chat/subtitle。Electron child/main-app 两条真实进程 smoke 已显式启用并通过；main-app smoke 使用正式模板和脚本完成启动、真实子窗口刷新、同 session 恢复、start 不重复调用、state 回读、preload 控制桥与主宠物窗口资源隔离验证。
6. 前端已增加独立主动事件带，只消费公开 `random_event_options`；v2.3 工具栏“离场”提交可审计 `user_exit`，Full/Economy 均在模型编排前短路，旧 session 继续使用 `/session/end`。
7. 星灯祭真实 v2.3 剧本已通过 Full/Economy 后端 20 轮结构化主线和图外分支安全返回；真实 Chromium Economy 已完成六件道具、玩家专属河风事件与正式结局的完整桌面演出验收。
8. 普通聊天、语音、小游戏和记忆针对性回归已完成；落幕后记忆确认与剧情回合共享 session guard，并发双确认只执行一次普通记忆写入。React Chat 静态契约仍有 7 个与小剧场无关的既存失败，未在本轮越界修复。

### 第六阶段：星灯祭 v2.3 样例收口

目标：用第一条完整样例证明协议可写、可查、可演、可复盘。

验收：

1. 四个开场疑点都有稳定 ID 和明确解决路径。
2. 六件道具都有开放、使用或调查状态变化。
3. 主路径与糖霜暴走救场路径都能自然汇合。
4. 至少有一条自由输入能触发随机关键事件或图外动态分支，并在后续留下可观察影响。
5. 每个 choice 有即时 callback，正式结局前有收束节点。
6. Story Compiler、单元测试和完整前后端演出全部通过。

当前状态：

1. 协议迁移、Compiler、4 个稳定疑点、6 个实体、1 个玩家专属事件、2 个玩家/Runtime 事件和 2 条 Evidence 已通过真实 Story 验证。
2. Full/Economy 均可沿 20 个稳定 Choice 从 revision 0 走到 revision 20，并只在显式 terminal node 正式结束。
3. 玩家专属河风事件已在真实 Chromium 中由玩家点击并公开风铃线索；六件道具依次开放并全部进入已使用列表，图外纸风车线索可在 Full/Economy 中经安全 bridge 返回主线后继续保留。
4. 糖霜暴走已按道具生命周期汇合，42 个 Choice 均有即时 callback，正式结局已回收六道具与第一炉开张结果；真实模型端到端图外抽取留待具备模型 API 的发行候选环境。

### 第七阶段：兼容与回归收口

目标：证明 v2.3 是 v2.1/v2.2 上的演进，不会让旧剧本、旧 session 和小剧场外链路被新协议接管。

验收：

1. v2.1 / v2.2 剧本继续按旧协议加载；没有 `scenario_protocol: "2.3"` 时不启用 v2.3 强校验。
2. v2.2 旧 session 缺少 random state、Overlay、client turn index 和新实体字段时能够安全懒初始化。
3. legacy message 提交在迁移期可用，但 v2.3 前端和新测试只使用结构化 Turn Request。
4. v2.3 session 恢复时使用其 private story snapshot；同 id Story JSON 更新不会改变进行中的静态图。快照缺失或损坏时返回明确恢复错误，不猜测迁移。
5. 普通聊天、语音、小游戏、角色记忆、独立页面和旧小剧场恢复链路通过针对性回归。
6. theater 动态事实只保存在 theater session；正式结局后的记忆候选仍需用户确认，不直接写普通长期记忆。

## v2.3 测试矩阵

除现有 theater 单元测试外，v2.3 必须覆盖：

1. **模式一致性**：同一 choice 和初始状态在 Full / Economy 下产生相同 State Delta、commit result、Board 和 Suggestions。
2. **随机复现**：固定 seed、draw index 和候选快照时结果一致；存档恢复后不会重抽历史事件。
3. **幂等提交**：重复 `client_turn_id` 返回同一响应，不重复推进、消耗道具、确认凭据或创建 Overlay 对象。
4. **并发串行化**：同一 session 的相同和不同 client turn 并发提交不会丢失更新；过期 base revision 不能提交。
5. **Overlay 恢复**：动态节点、边、实体、线索和 choice 在重启后仍可路由；静态与动态 ID 冲突被拒绝。
6. **Story revision**：会话开始后修改同 id Story JSON，再重启恢复时仍使用原私有快照；缺失或损坏快照不会静默加载新版。
7. **公开边界**：动态 clue/entity、private story snapshot 的私有定义、hidden meaning、forbidden reveals 和内部候选不会进入 Board、Persona 或 Public Trace。
8. **随机保底**：不触发任何可选随机事件时，主目标仍至少有一个 primary 正式结局可完成；optional 随机专属结局仍需凭据且不能被误判为主线阻塞。
9. **动态结局边界**：动态分支不能创建 ending/evidence 定义，不能绕过凭据；合法动态事实可以满足已声明 `any_of` 条件。
10. **事务失败**：Resolver、Renderer、Validator 和保存失败分别验证 Commit / Rollback、fallback 和非空 Trace。
11. **前端契约**：choice、free input、主动随机事件和 user exit 都提交正确 input kind；Board 与 Trace 每轮刷新。
12. **跨链路回归**：普通聊天、语音、小游戏、角色记忆和旧小剧场不读取 v2.3 私有状态。

## 附录 A：星灯祭继承样例

本节记录首条已迁移 v2.3 样例的剧本事实，用于验证通用协议，不代表其它剧本必须复用同一题材。其它剧本可以是雨夜陪伴、初相遇、校园委托、冒险探索或轻悬疑，不需要复用星灯祭的道具或凭据名称。

《星灯祭的糖霜危机》应从“帮猫娘救甜品摊”改成“夜市甜品共演剧本”：有清楚的剧本卡、舞台道具、GM 控场、轻推理和救场，但不把题材锁死成严肃事故调查。

### 开场剧本卡

星灯祭花火前，猫娘第一次独立摆甜品摊。招牌星灯鲷鱼烧需要亮度、味道、形状三件事同时成立，但完整配方断掉，只剩“三次铃声”。糖霜会发光，却味道跑偏、形状不稳。玩家作为临时帮手，要和猫娘在夜市有限范围内试道具、补线索、救回第一炉。

### 舞台道具

1. 半张配方卡：揭示“三次铃声”。
2. 发光糖霜锅：观察亮度和泡泡节奏。
3. 三只调音风铃：验证铃声节奏控制亮度。
4. 桂花糖：修正味道。
5. 旧鱼形印章：固定招牌形状。
6. 试吃纸杯：让第一位小客人验证成品是否真的能卖出去。

### 核心疑点

1. “三次铃声”到底是什么意思？
2. 糖霜为什么亮了但不好吃？
3. 星灯鲷鱼烧为什么必须有鱼形？
4. 第一炉怎样才算真正开张成功？

### 剧情凭据闭环

1. `clue_three_bell_words`
2. `clue_frosting_glows_before_flavor`
3. `clue_bells_control_timing`
4. `clue_osmanthus_balances_flavor`
5. `clue_stamp_shapes_brand`
6. `evidence_glow_timing_ready`
7. `evidence_first_batch_ready`

### 结局

正式结局 `first_starlight_batch` 当前由 `evidence_glow_timing_ready` 和 `evidence_first_batch_ready` 支撑；后续文案打磨仍需在收束节点明确呈现以下结果：

1. 亮度稳定。
2. 味道平衡。
3. 鱼形招牌成立。
4. 第一位小客人接受第一炉。

## v2.3 验收标准

1. v2.3 剧本有明确 `scenario_protocol` 和 feature 声明。
2. Story Compiler 能检查静态引用、可达性、死胡同、疑点闭环、静态 evidence 可满足性、确定性保底路径和 ending 可达性；动态分支由 Runtime Validator 负责。
3. 前端提交稳定 `input_kind / choice_id / client_turn_id`，公开 label 修改不影响路由，重复提交不重复推进。
4. 道具和可互动对象随 committed state 更新分维度实体状态；NPC、地点不套用道具 ownership。
5. 疑点使用稳定 ID，Board 显示公开文案。
6. 每个 choice 都有即时 callback，多分支汇合前保留选择差异。
7. Full / Economy 共享统一事务并提交相同状态；Economy 的自由输入仍可触发受约束随机事件和动态分支。
8. Persona 只读取公开事实，Validator 能检查未发现线索和隐藏含义泄露。
9. 剧情凭据可以由受限 `all_of / any_of` 以及 clue、prop、question、node、fact 条件形成；Runtime 不得创建 evidence 定义。
10. 正式结局由 terminal node 的 `ending_id` 和剧情凭据决定，不由 intent 或强度猜测。
11. 每轮都有 Public Scenario Trace，并分别记录 `progress_kind` 与 `commit_result`，覆盖推进、拉回、阻断、回滚、离场和正式结局。
12. 玩家可以明确触发符合当前状态的随机关键事件；Runtime 自发抽取可复现，事件提交后能产生道具、线索或局内事实。
13. 合格图外输入可以生成持久化的 Runtime Graph Overlay，并在重启后继续扩写或桥接回预写图。
14. Internal Engine Trace 能解释候选过滤、推进类型、上下文注入、Validator 和下一组选项来源。
15. GM 只在无法吸收或桥接时拉回，并使用当前可用 anchor 和 cooldown。
16. 前端能展示 Scenario Board、Public Trace 和动作分类。
17. 不引入好感度、信任分、理智值、亲密度、成就分或剧情进度百分比。
18. 不污染普通聊天、语音、小游戏、角色记忆和其它小剧场外链路。
19. 小剧场单元测试、Story Compiler 校验和真实前后端演出全部通过。
20. v2.1/v2.2 剧本和旧 session 保持兼容；网络重试、重启恢复和小剧场外链路通过回归。
21. 场景内对话、情绪表达和不产生关键状态变化的轻互动使用 `roleplay_response`，不得显示为 GM 拉回。
22. 当前按钮与自由输入共享同一候选语义；按钮可执行的动作不能因玩家改用自然语言描述而被拒绝。
23. 成功回合清除旧拉回原因，前端不展示内部 phase、Validator 或事务枚举。
24. 页面刷新或 Electron 风格新窗口重开后，必须从 committed 公开快照恢复当前 scene、演出文本、Board、Trace、Choice、事件、结局和记忆候选，不得把私有 turns 或 Story State 发到前端。
25. 瞬时网络失败或 `502/503/504` 时只允许复用同一份序列化请求体重试一次；相同 `client_turn_id` 不得在界面或后端形成两个回合。
26. `state_revision` 冲突时必须刷新服务端最新公开状态、恢复玩家尚未提交成功的文本并要求重新选择，不得自动用旧 revision 重放动作。
27. 落幕后两个并发“记住”请求必须共享 session 串行锁；普通记忆服务最多调用一次，未确认、拒绝、Type B 或 Type C 候选均不得进入普通长期记忆。
28. 星灯祭正式 Chromium 主线必须让六件道具依次开放并全部进入已使用列表；玩家专属河风事件只能由玩家点击，点击后主线 Choice 继续存在。
29. 图外动态节点提交后必须至少提供一个当前可执行的安全返回 Choice；bridge 回到静态图后，图外公开线索和主线新线索都必须保留。
30. callback 不得出现在未点击的公开按钮协议中；服务端解析到稳定 Choice 后才合入本轮演绎，并继续经过事实边界和 Validator。
31. 同一目标节点的不同 Choice 必须产生不同即时 callback；按钮和自然语言等价输入解析到同一 Choice 时必须得到相同 callback。
32. 未逐字复述但语义等价的隐藏事实泄露必须被 Level 2 拒绝并回滚；模型返回的私密 reason、隐藏原文和泄露改写不得进入公开响应、Trace 或 rollback 日志。
33. Level 2 未配置、超时、异常或返回坏 JSON 时必须保留 Level 1 结果，不得阻塞普通推进、制造 GM 拉回或重新解析玩家输入。
34. OpenAI-compatible 正式客户端必须以受限 token 预算贯通动态图外候选与语义 Validator；Validator wire prompt 不得携带 hidden fact 的内部 ref ID，模型响应仍需经过固定字段解析和私密原因替换。
35. 推荐 Choice 必须按作者声明分为玩家行动与玩家对白；通用层不得从自然语言措辞猜测类型，对话按钮必须是玩家可直接说出口的文本。
36. 非结束回合不得因为空 `suggestion_options` 吞掉仍可用推荐；星灯祭先查糖霜锅的非线性路径必须提供配方卡恢复 Choice。

## 下一步

1. 使用第九轮显式 real-model smoke 在具备模型 API 的发行候选环境验收真实供应商模型的图外抽取、语义泄露拒绝和安全文本放行；当前本机 4 项均因未配置显式环境而 skipped，OpenAI-compatible 正式 SDK/HTTP 协议链、确定性安全边界与受控模型自动化已经完成。
2. 在发行候选环境补充打包安装后的真实模型、语音和用户数据手工验收；当前自动化已完成普通聊天、语音、小游戏、角色记忆、浏览器恢复与 Electron 真进程恢复的针对性回归。
3. 多对象模型候选、Checkpoint、多角色 Speaker Policy、Context Policy 高级 sticky/cooldown 和长剧本分层召回留到核心纵向演出稳定后评估。
