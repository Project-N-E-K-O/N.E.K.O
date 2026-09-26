# 小剧场运行端框架与结构审查

状态：**TF-01—03 已处理，匹配回归通过**。本文是运行端历史结构审查，当前模块职责、生产导入关系和开关边界以[架构开发文档第2节](./neko-theater-architecture.md#2-模块与权限)为准；实际优化与验收边界统一见[问题与处理 2.125](./neko-theater-issues-and-solutions.md)。下文保留优化前的审查证据、行号和建议；“当前”“本轮”及末尾检查数量均指审查时点，不是现行待办。

## 范围与结论

- 基线：`theater_chat_window`，`201e4fc4a`；审查开始时工作区干净。后续并行任务的文档修改不属于本报告实现变更。
- 范围：`services/theater/` 的 Actor、Evaluator/Guard、Workflow、Runtime、Store、上下文及固定旁白分工，并追踪 `main_routers/numeric_theater_router.py` 和压测入口的调用关系。
- 依据：当前代码、架构第 10—12 节、实测问题文档及本轮三个隔离检查。公共 Story Package/作者项目字段与工坊实现交给对应专项审查。
- 结论：现有主链的职责分层有业务依据。本轮确认 **3 项局部结构问题**，其中 1 项是未启用的生成协议分支，1 项是同一路径重复装配，1 项是结构化证据依赖 Prompt 文案反解析。没有据此确认新的玩家可见故障，也没有证明需要整体重写框架。

下文 `numeric_v2_*.py:行号` 均相对仓库的 `services/theater/`；`test_theater_*.py:行号` 均相对 `tests/unit/`。行号以以上基线为准。

| 编号 | 优先级 | 状态 | 问题 |
| --- | --- | --- | --- |
| TF-01 | P2，维护性 | 已移除旧生成分支，保留存档读取 | 正式 Actor 只有一个生产输出协议，仍维护另一套模型生成分支 |
| TF-02 | P3，维护性 | 已统一由 Runtime 装配 | 同一次正式换幕在 Runtime 与 Workflow 重复装配入幕固定旁白 |
| TF-03 | P2，维护性 | 已直接交接结构化证据 | Guard 从中文 Prompt 字符串反解析已构造的授权证据表 |

## TF-01：移除旧模型生成分支之前，先与旧存档读取明确分开

**位置与生产者 → 消费者**

1. `numeric_v2_actor.py:2792` 明确所有正式转场使用紧凑文本合同；下一行将 `deterministic_transition` 无条件设为 `route_changed`。
2. 同文件 `2802—2833` 的生产调用同时传入 `transition_required=route_changed` 和上述 `deterministic_transition`，`_turn_messages` 也取得同一个值。
3. `numeric_v2_actor.py:2150` 因而只在正式转场选择 `transition_compact`；但 `:94` 仍保留 `transition` 的旧模型输出 Prompt。
4. `_invoke` 在 `numeric_v2_actor.py:3046` 调用 `numeric_v2_actor_output._parse_output`。后者 `:383` 是生产紧凑合同，`:420—483` 还维护另一条 `transition_required=True / deterministic_transition=False` 的旧 `segments` 模型解析路径，并在 `:454` 调用旧桥段去重函数。
5. 当前仓库生产入口只有 Actor 的 `_invoke` 消费该解析器；Workflow、HTTP 和压测均走公开 `generate_turn`。未找到给正式生成开启旧模式的生产调用者。`test_theater_numeric_v2_source_narration.py:22、46` 则继续为 `compact=True/False` 两种生成协议各维护一套测试。

**为什么是冗余**

这里有两套需要同步的模型字段形状、来源旁白解析和目标旁白处理策略，但产品入口只会选择其中一套。旧生成分支还保留“目标开场只能由 Runtime 提供”的规则（`numeric_v2_actor_output.py:461`），与当前动态生成目标旁白的策略不同。新增来源旁白等能力时已经需要同时修改并测试两个分支；这不是单纯的函数较长。

本项只证明**旧模型生成入口**没有当前生产调用者，不代表 `segments` 数据结构无用。现行正式提交仍然使用三段 `segments`；Store、冷恢复、历史投影、归档和前端也仍需读取它。`NumericV2Engine.finalize_transition_performance` 的直接旧数组调用与存量记录读取，需要另外核实兼容边界，不能一并删掉。

**最小精简方向**

先限定 Actor 新请求只支持现行紧凑协议，去掉未选中的旧 Prompt/解析模式和只服务该模式的参数、桥段去重代码。把测试中的“旧模型输出”与“旧存档读取”分开，保留后者。无需增加策略注册器或新协议版本。

**必须保留与后续验证**

- 保留来源回应、桥段、目标开场的确定顺序，动态旁白、可选来源旁白、对白策略和空桥段许可。
- 保留紧凑输出到正式 `segments` 的转换，以及存量 `performance_contract_version` 读取。
- 修改前核对所有 Actor 调用者；修改后跑真实 `generate_turn` 的模拟模型客户端路径、转场提交/冷恢复/分叉、来源旁白和旧记录兼容测试。不能只直接调用解析器冒充生产入口验收。

## TF-02：入幕固定旁白在同一换幕生成链中被装配两次

**位置与生产者 → 消费者**

1. `NumericV2Actor.generate_turn` 在 `numeric_v2_actor.py:2836` 调用 `engine.finalize_transition_performance`，将原始文本组装为正式三段。
2. `numeric_v2_runtime.py:724` 已对目标段调用 `add_entry`，使用目标节点、候选 Session 的姓名与披露状态。
3. Actor 返回后，Workflow 的 `generate_actor_turn` 在 `numeric_v2_workflow.py:491—496` 再次对相同目标段调用 `add_entry`，随后才交给 Guard。
4. `numeric_v2_fixed_narration.py:102—110` 每次复制目标段、读取展示历史、检查已有编号、装配未展示的入幕文本。Runtime 传入 `outcome.session`，Workflow 传入 `current.session`；`numeric_v2_runtime.py:535—553` 的候选构造不追加历史，二者此时展示历史相同。

**为什么是冗余**

两次调用作用于同一个候选目标段，第一处已经完成了应由程序持有的入幕装配；第二处因已有编号而不再添加内容，但重复复制和扫描历史，并让 Runtime 与 Workflow 同时承担“保证目标段带齐入幕片段”的职责。若日后修改装配时点、传入快照或姓名绑定，必须同时判断两处是否需要同步。

**这不是重复展示 bug。** 本轮抽取当前函数进行隔离执行，两次调用结果相等、仅含一份片段，第二次返回新的深拷贝对象。当前 ID 去重有效；未测量其性能成本，不把重复扫描描述为已确认的延迟根因。

**最小精简方向**

以当前正式 Actor 的返回合同为依据，保留 Runtime 组装时的入幕注入，取消 Workflow 同一路径的重复注入。若有模拟 Actor 返回未装配片段，应让相应夹具遵守生产 Actor 合同；不要为了测试替身长期维持第二个装配所有者。

**必须保留与后续验证**

- `add_entry` 能力本身、初始建档注入、Runtime/Store 的展示完整性校验、原文及姓名绑定、每 Session/节点/编号只显示一次全部保留。
- 条件片段仍由最终合格 Guard 结果驱动，不能移入 Actor 或与入幕片段合并判断。
- 后续用真实 `generate_turn` 配模拟模型客户端检查正式转场：缺失入幕片段拒绝提交、正确原文只展示一次、改稿不会遗留旧片段、存储失败回滚、冷恢复和循环访问去重。现有 `test_theater_numeric_v2_fixed_narration.py:84` 可作为完整性回归入口。

## TF-03：Guard 的证据表经过 Prompt 字符串再反解析

**位置与生产者 → 消费者**

1. `_build_transition_judge_messages` 在 `numeric_v2_evaluator.py:842` 从本次访问的公开演出构造 `data["public_destination_evidence"]`。
2. 在 `:1204—1247` 按预算装箱，将数据序列化为第二条 HumanMessage，并在前面拼接中文说明。
3. `validate_transition_offer` 的 `:1674` 再执行 `json.loads(messages[1].content.split("：", 1)[1])`，取出同一编号表传给 `_parse_transition_judge_output(recovery_evidence=...)`。
4. 解析器据这张表恢复公开原话并验证出处；Workflow 仅在这一步通过后才尝试主动转场漏判恢复。

**为什么是结构问题**

保存“模型实际看到的编号表”是必要的，但不需要丢弃结构化对象后，再从面向模型的中文字符串找回它。当前内部授权证据交接依赖 `messages[1]` 的固定位置、中文全角冒号及开头说明只能出现这一分隔符。Prompt 排版和证据合同因此需要一起维护；调用链没有类型层面的关联提示。

本轮直接提取 `:1674` 的当前表达式：原前缀可以恢复原证据；只将前缀末尾冒号改为换行、JSON 数据完全不变时，恢复表达式失败。**这是证明维护耦合的隔离探针，不是当前未修改版本的生产故障复现。**

**最小精简方向**

让现有消息构造步骤同时交回最终装箱对应的证据表（例如简单二元返回值），调用方直接将它交给输出解析器。不要另建历史事实库，不要在响应回来后重新检索一张可能顺序不同的表，也无需引入通用 Prompt 框架。

**必须保留与后续验证**

- 证据必须来自实际发送的同一张表；未知编号、玩家输入、未提交候选、作者未来计划及旧访问记录不能成为恢复授权。
- 保留当前输入预算、完整原话、出处复验、首次争议额度、共享一次改稿和最终原子提交。
- 以 `test_theater_numeric_v2_missed_transition.py`、`test_theater_numeric_v2_transition_quote_handoff.py`、`test_theater_numeric_v2_review_capacity.py` 检查补查/争议是否消费同一原话。另验证修改纯提示说明或消息排版不会改变编号恢复。

## 已排除的“看似复杂”结构

| 结构 | 本轮不列为冗余的原因 |
| --- | --- |
| Evaluator → Runtime → Actor → Guard → Store | 分别承担概率语义判断、确定性结算、表现生成、复核和原子提交；删层会改变已确认权限分工。 |
| 普通/正式 Guard 不同技术失败处理，首次争议与共享改稿 | 用户已明确选择的回合行为；本轮没有用文件长度或调用次数推断其可以删除。 |
| Runtime 与 Store 均有校验；旧包结束与继续分开 | 提交输入、磁盘恢复与生命周期收尾面临不同信任边界。旧包不得继续生成，但应允许受保护结束/遗忘，不能合并成单一门禁。 |
| Actor、Evaluator、Guard 的历史投影不同 | 共享已提交原文及访问边界；各消费者仍有不同总预算、目标段作用域与输入形状。没有证据支持将其塞进通用上下文树。 |
| 姓名投影和不可变角色身份 | 创作姓名与开演姓名可能变化，且剧情内披露状态独立；生成时写入名字不替代运行投影。 |
| 固定旁白原文、触发条件、提交/恢复校验 | 原文不能由模型改写，只有明确 required_before_exit 才阻断离幕；TF-02 只处理重复装配所有权。 |
| 初始开场前后两次校验角色/包/槽位 | 模型调用期间不持生命周期锁，因此提交前必须重新核实可变事实，不能因代码相似删除第二次检查。 |
| 基础补推荐与提交后 TTS | 各自已有明确降级和消费时点；不是单凭“存在额外调用/桥接”即可删除的层。 |

## 本轮验证与未验证范围

已完成：

1. `git status --short`、目标模块/生产入口引用检索及架构合同对照。
2. 只读 AST 检查正式 Actor 的两个模式参数来自同一个 `route_changed`，确认旧生成条件在现有生产入口不可达。
3. 使用 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B` 从当前源码抽取固定旁白纯函数，在内存中执行两次装配；内容相同且只有一份片段。
4. 从当前源码提取 Guard 证据恢复表达式，以相同 JSON 和不同说明分隔符执行隔离探针；原前缀通过、仅改前缀后失败。

三个检查均未导入项目运行模块、调用模型或写入正式数据；没有新增测试/临时脚本到仓库。它们证明上述结构事实，不代表修复后的回归或模型准确度验收。

未执行 pytest：仓库测试入口包含模型资产解包、浏览器探测与运行根初始化，本轮纯结构审查没有为复跑扩大动作范围。未运行服务、HTTP/浏览器/桌面/TTS、整剧/多路线模型压测，也没有检查全部外部私有调用者。后续实施应按各项列出的边界运行相应隔离回归。
