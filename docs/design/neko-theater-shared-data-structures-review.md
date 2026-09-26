# 小剧场与剧本工坊公共数据结构审查

状态：**DS-01 已合并重复投影，DS-02 已对齐作者说明，匹配回归通过**。实际优化与验收边界统一见[问题与处理 2.125](./neko-theater-issues-and-solutions.md)。下文保留优化前的数据合同审查证据、行号和建议；“当前”“本轮”及原检查数量均指审查时点，不是现行待办。当前正式 schema 已按 Numeric v2.2 与完成事实/作者接受输入字段收口；旧字段和旧包不做运行时兼容，缺少当前字段的包必须重新编译，旧作者工程需要重新导入或发布。

## 范围与结论

- N.E.K.O 基线为 `theater_chat_window` / `201e4fc4a`，本专项开始时已有其他审查文档修改，均保留。
- InkAI 依据当前工作区实际文件审查；已有大量修改、删除及未跟踪文件，未清理或改写，结论不冒充其 HEAD 状态。
- 范围涵盖正式 Story Package 编译边界、作者输入 DTO、主线/完善/支线的包字段投影、固定旁白定义，以及 Session/Ledger/表现记录与公开投影的分工。
- 本轮确认 **2 项结构问题**：同一作者核心内重复维护目标投影；已经退役的目标执行语义仍由作者数据协议生产和描述。没有发现足以支持合并作者项目、正式包与 Session 的证据，也没有确认 SDK/InkAI 同名 DTO 已经漂移。

| 编号 | 优先级 | 状态 | 问题 |
| --- | --- | --- | --- |
| DS-01 | P2，维护性 | 已统一每仓公共投影 | 主线/完善与支线重复实现相同的作者目标到正式目标投影 |
| DS-02 | P2，维护性 | 已对齐创作/评改说明，保留字段 | 作者协议仍表达已不执行的目标取证/发声效果，实际运行能力在其他字段 |

路径约定：下文以 `N.E.K.O/` 表示 `/Users/mac/Code/N.E.K.O/`，以 `InkAI-/` 表示 `/Users/mac/Code/InkAI-/`；行号以本次实际读取为准。

## DS-01：同一作者核心内有两份相同目标投影

**生产者 → 消费者证据**

| 环节 | 当前代码 |
| --- | --- |
| 作者输入 | `N.E.K.O/theater_workshop/sdk/contracts.py:103` 的 `BranchGoalContractPayload` 声明 owner、delivery_type、evidence_mode、anchors、sources、timing、dialogue_policy_after；主线模型生成同形的 `ordered_goals`。 |
| 主线/完善投影 | `N.E.K.O/theater_workshop/sdk/generation/numeric_v2.py:2376` 的 `_project_chapter_goals`；调用者为 `:1997` 的节点完善和 `:2097` 的主线投影。 |
| 支线投影 | `N.E.K.O/theater_workshop/sdk/numeric_v2_branch.py:2009` 的 `_project_goals`；调用者为 `:497` 的新结局和 `:540` 的支线场景投影。 |
| 两份相同转换规则 | 两者都生成 `<node_id>_goal_<序号>`，将 opening/player_input/previous_goal 转成同样的 source_ids，生成 evidence、delivery、output_field，并在非 unchanged 时生成 state_effects。两文件各有一份 `_GOAL_DELIVERY_OUTPUTS`（生成模块 `:515`，支线模块 `:20`）。 |
| 正式接收 | `N.E.K.O/services/theater/numeric_v2.py:622` 的目标校验及 `:686` 的 delivery 校验；质量上下文在 `N.E.K.O/theater_workshop/sdk/generation/quality.py:557` 消费这些正式字段。 |
| 原本地工坊 | `InkAI-/theater_generator/generation/numeric_v2.py:2380` 与 `InkAI-/theater_generator/numeric_v2_branch.py:2008` 也存在两份相同责任。 |

**为什么是冗余**

主线和支线的创作入口不同，但目标序列已经规范化后，ID、来源引用和正式交付字段的转换规则相同。任何涉及来源编号、字段形状或交付映射的改动都要在一个作者核心内同步两处，随后再同步另一仓库。现有 `numeric_v2.py` 已为两种入口共享 `acting_contract_to_package`、`character_state_to_package`；目标投影没有沿用这个明确边界。

本轮从当前源码抽取两份纯函数，对 6 种 delivery_type × 2 种 dialogue_policy_after、包含 previous_goal 引用的 **12 组规范化输入**逐一比较，输出一致。主线会再次 strip 文本，支线依赖前置 `_validate_ordered_goals` 规范化；因此不能声称两函数对任意未校验字典都完全等价，也不能直接删除入口校验。

**最小精简方向**

在每个作者核心现有公共投影模块中只保留一份“规范化目标列表 → 正式 goals”的小函数和交付映射，主线/完善/支线负责输入适配与各自校验。不同入口的错误路径、目标数量、结局限制仍留在各自入口。不新增跨仓运行依赖，也不要求正式编译器依赖工坊模块。

**必须保留与验收**

- 保留稳定目标 ID、顺序、来源引用、主体归属与证据文本；DS-02 的语义收口需另行判断，不能在去重时顺带删字段。
- 正式编译器仍独立复验外部包；作者侧提前校验与导入边界校验有不同职责，不能因为枚举一致删除其中一层。
- 后续比较主线、节点完善、支线路径和新结局的完整输出及 canonical bytes；覆盖 exact/semantic、previous_goal、opening/turn、发声效果和非法输入。合法现有稿的 hash 不应因纯去重变化。

## DS-02：作者目标保留了已失效的执行语义

这里审查的是**字段职责与运行能力错位**，不是宣称整个 goals 无用，也不是要求恢复旧目标引擎。

**生产者 → 消费者证据**

1. `N.E.K.O/theater_workshop/sdk/contracts.py:114—121` 接受 evidence_mode/anchors 和 dialogue_policy_after；InkAI 对应 DTO 位于 `InkAI-/theater_generator/api.py:67—85`，两端字段一致。
2. 两端 `generation/numeric_v2.py:310` 的现行创作指令仍将 semantic 目标描述为由 Evaluator 判断是否完成，将 exact 用于必须逐字出现的文本，并要求在睡眠、禁言或恢复发声时改变 dialogue_policy_after；节点完善 `:704` 同样保留该发声效果表述，支线创作 `:832` 仍描述由 Evaluator 判断目标是否完成。
3. DS-01 的两个投影器继续生成 `goals[].delivery.state_effects.dialogue_policy`。正式编译器 `N.E.K.O/services/theater/numeric_v2.py:767—785` 接受并验证该字段，`:654—670` 校验 exact/semantic 锚点。`generation/quality.py:557—570` 又把 evidence 与 state_effects 提供给作者评改链。
4. 当前正式 Runtime 没有 state_effects 消费者。`N.E.K.O/services/theater/numeric_v2_runtime.py:353—359` 从节点 `acting_contract.dialogue_policy` 读取发声策略，`:526—532` 在进入节点时应用；没有目标完成触发的切换。Actor 的 `_beat_for_actor`（`numeric_v2_actor.py:1062`）只投影开场、方向及边界，明确不发送 goals/证据。
5. 需要程序原样展示文本时，实际能力是 `story_beat.fixed_narrations`：`N.E.K.O/services/theater/numeric_v2_fixed_narration.py:17` 校验定义，`:102` 装配 entry，`:138` 处理最终复核结果中的 condition。它和 exact anchors 不是两个等效入口。

**实际维护成本与边界**

创作输入、校验、两份投影、正式包、评分上下文继续传递一组看似可以驱动演绎的字段，但 Runtime 已经撤销对应执行器。共享规则虽然说明 goals 是素材，局部生成合同仍要求模型按完成取证、逐字锚点和后置发声效果编排；维护者需要同时理解“字段存在”和“能力不执行”，新能力还容易与旧字段混淆。

这已能由当前代码证明；**没有通过真实生成证明某个作者包因此漏播、误发声，也没有测量它对模型质量的影响**。字段仍是作者评改、引用与旧稿回读材料，不能用“Runtime 不读”推导为“全仓无用”。实际发声语义应以节点 acting_contract/Session 为准，原文显示应以 fixed_narrations 为准。

**最小收口方向**

先对齐各创作入口对现有字段的解释：不再把目标 evidence/state_effects 描述成运行时已执行的能力；作者要求原样显示时，明确使用现有 fixed_narrations 合同。保留对旧稿这些字段的解析、作者评改与正式编译兼容。

是否进一步停止在新稿生成非 unchanged 的 dialogue_policy_after，或将部分旧目标元数据移出发布包，需要单独确认创作合同与迁移边界；本报告不批准删除已有包字段、不自动将 exact anchors 改造成固定旁白，也不添加按目标完成切换状态的新引擎。

**必须保留与验收**

- 保留 goals 中实际用于主体、因果、内容和引用的作者资料，不把普通目标或 min_turns 恢复成强制任务。
- 保留明确的角色发声、姓名披露、关键行动权限，以及固定旁白原文、触发、顺序和提交/恢复验证。
- 后续用同一作者需求检查主线、续写、完善、支线结局/路径、事实、证据、文学与修订实际输入；确认原样文本不再仅依赖 exact，发声需求不再仅落在未执行的 state_effects。旧稿导入、编译及评改应仍可读；真实模型结果另行验收，不能用 Prompt 改动冒充通过。

## 已检查但不列为问题的结构

| 结构 | 保留理由或本次证据边界 |
| --- | --- |
| SDK 与 InkAI 同名 DTO | AST 对照确认 16 个同名 Payload 完全相同；StrictPayload 仅 docstring 不同。两端独立部署仍需维护同步，但本次没有证据称其已漂移。 |
| 作者项目 envelope 内的宽字典 | 未完成作者稿必须能保留尚未通过正式编译的内容。外层严格输入、作者编辑与正式包校验是不同边界，不建议把所有字段都替换成一套运行时 schema。 |
| 作者项目、Story Package、Session/Ledger、公开表现投影 | 分别保存可编辑意图、版本化作者事实、已提交状态/原文与客户端可见内容。相似的 title、状态或文本字段不表示可以合并存储所有权。 |
| setup metric 与正式 metric_schema | 作者编辑形状包含 preset 和单值设置，正式包使用稳定 ID、限幅与初始状态；需要显式投影，不能按字段相似就去掉正式定义。未穷举所有组合编辑。 |
| 作者项目 revision、正式包 hash 与发布字节 | 分别约束作者版本、正式包身份和同一发布产物；独立复验不是重复实现编译器。WS-02 已记录编译附带软诊断的特定成本，这里不另立。 |
| 运行时姓名投影 | 生成快照与开演姓名可以不同，剧情内披露状态独立，不能因作者保存了姓名而删除。 |
| 固定旁白定义与展示记录两种形状 | 前者是作者原文/条件，后者保存实际绑定、位置和已展示文字，服务恢复与历史不可变性；不是两份可互相覆盖的完成状态。TF-02 的重复装配另见运行端报告。 |
| min_turns/recommended_turns 与正式路线条件 | 属于节奏提示与确定性选路两个层次；不把前者当作已确认的硬门槛冗余。 |

其他专项已记录的旧 Actor 输出分支、重复入幕装配、Prompt 证据反解析、导入写入事务和发布门禁不在这里重复计数，见[运行端报告](./neko-theater-framework-review.md)与[SDK 报告](./neko-theater-workshop-framework-review.md)。

## 本轮验证与未验证范围

完成两仓工作区状态与实际代码核查、生产者/消费者引用检索；在 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B` 临时进程中直接抽取当前源码纯函数执行：

- 12 组规范化主线/支线目标投影相等；3 份本仓交付输出映射相等。
- 节点 acting_contract 为 required、目标 state_effects 为 forbidden 时，实际 `_node_dialogue_policy` 仍返回 required；Runtime AST 没有 state_effects 读取。该探针只验证字段读取关系，不冒充完整回合体验。
- 两仓 16 个共名 DTO AST 相同，另一个共名 StrictPayload 只存在说明文字差异。

以上检查未导入项目运行模块、调用真实模型、初始化服务或写入正式数据；未新增测试与临时脚本到仓库。没有运行 pytest、完整发布链、真实生成/演绎、所有旧作者项目迁移或前端/桌面/TTS。本文只覆盖已列出的结构证据，不是公共数据合同的全功能验收。
