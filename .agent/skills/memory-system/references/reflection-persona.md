# 反思和人格：合成、证据来源、永久压制、自省

入口代码：`memory/reflection/`（合成、升格、精炼）、`memory/persona/`、`memory/evidence.py`、`memory/introspection.py`、`memory/behavior_notes.py`、`app/memory_server/refine_loops.py`。生命周期和分数见 `docs/architecture/memory-system.md` 与 `docs/design/memory-evidence-rfc.md`，各常量的理由写在 `config/memory_settings.py` 的注释里。

## 1. 合成前查重

背景：反思合成曾经看不到已有的反思和人格，每批事实都被要求产出「高层次洞察」，用久了关系类条目里会堆满同义的空洞定性句。现在合成前把这批事实最接近的已有印象给模型看，模型可以判「已被旧印象覆盖」而跳过（`synthesis.py::_build_existing_impressions_block`）。

- **跳过时不给已有条目加分。** 复述不是证据。跳过的事实 id 记进目标的 `covered_fact_ids`（只留最新 `REFLECTION_COVERED_FACT_IDS_MAX` 个，纯审计）。用户复述了一条原本只靠角色自述的印象时，这些事实会并进来源计数，算作用户的背书；强化值仍不动。群聊反思是例外，见 `scoped-memory.md`。
- **候选池**：同主体的 pending / confirmed 反思，加非保护的人格条目。已升格（promoted）的反思不进池，由它的人格副本代表：每条升格路径都会写人格副本，渲染的也是副本；副本被改写或归档后，那条历史反思不该再吸收新事实。
- 候选池排除被永久压制的条目，以及渲染时被「已退役的同文人格行」挡住的反思（`retired_persona_hides`）。否则新事实会被一条模型已经看不到的句子吞掉。
- **只读已经回填好的向量**（`vectors_ready()`），不现场算。现场算会占住共用的 embedding 模型，拖慢回复前只有 1 秒预算的被动召回。没有可用向量时这一段为空，提示词和以前一样。
- 落盘前在锁内复查：目标还在、状态没变、文本和给模型看的那版相同、没被压制。任何一条不满足就退回正常新增。

同类评审意见的判法：

- 「处在提及冷却（`suppress`）或证据分 ≤ 0 的人格条目不该当跳过目标」：不采纳。冷却中的条目照常渲染在「暂不主动提及」一节，证据分 ≤ 0 是人格条目的常态。
- 「`source_fact_ids` 应按时间排序」：不采纳。单批事实数远小于保留上限，最新一批总是完整保留。

## 2. 一批写几条、依据怎么记

- 一次合成最多 `REFLECTION_SYNTHESIS_MAX_PER_BATCH`（3）条，每条只列它依据的事实（模型用 `based_on` 标出）；起始分和来源计数只按依据算，模型没给出可用编号时退回按整批算。
- **整批事实都标为已吸收**，没被任何一条用到的也一样。「没用到的事实留给下一轮」是考虑过、没选的方案。
- 同批反思共用 `synthesis_batch_id`，幂等检查按批次做。
- 精炼拆分出来的子反思照抄父反思的来源计数。评审建议「按子反思各自重算」不采纳：整理模型看不到父反思的依据事实。

## 3. 证据按来源分级：评审口径

规则本身已写在 `docs/architecture/memory-system.md` 的「Evidence and reflection lifecycle」。评审里反复出现的几条：

- 「把 `engaged`（用户接着话题聊下去）算作认可」：不采纳。它只加 `USER_ENGAGED_DELTA`，不算用户信号（`memory/evidence.py::awaits_user_signal`）。
- 「没有来源计数的旧反思也该加门槛」：不采纳。旧条目没有计数就不加门槛是有意的，存量交给每周自省处理。
- 「看双方发言」那一遍提取让模型自己标每条事实的来源。它标成「用户说的」的事实以 `signal_processed=False` 写入，会被下一次判分当作用户证据。这些行带 `extraction_path=path_b`，提取时打一行 `[PathB]` 计数日志，用来事后核对模型标得对不对。改这条路径的来源归属前先看这些记录。
- 强化值读写两侧都封顶 `EVIDENCE_REINFORCEMENT_CAP`，目的是让再强的条目也能被几次反驳推翻。不要在某条新的加分路径上绕过它。

## 4. 永久压制

`permanent_suppression: {at, reason, issue?, merged_into?, restates?}`，人格和反思同一个字段，用 `memory.introspection.is_permanently_suppressed` 判断。它和 `suppress`（提及冷却，几小时后自动解除）是两回事：**没有任何路径会清掉这个标记，也没有撤销入口**；被压制的条目留在盘上，永不清理。

必须生效的地方（新增同类路径时照此补）：渲染（含「暂不主动提及」一节）、反思的活跃 / 追问候选筛选、升格（候选池两侧和被升格的那条，调模型前、后、写入前各复查一次）、精炼候选和应用前的二次校验（合并的任一来源被压制则整条拒绝）、召回的硬过滤、矛盾检测、合成前查重、长期同步导入。

已定的规则：

- **合并不删来源。** 自省把几条合成一条时，来源条目改为永久压制并带 `merged_into`。原因：`version_history` 只留最近几版，合并的来源一多就会丢原文。
- **`suppress` 动作必须带清单代号 `issue`**（`ISSUE_CODES`）。
- **同一句话再被合成出来怎么办**：合成落盘前和同主体被压制的条目比（余弦 ≥ `REFLECTION_RETIRED_RESTATEMENT_MIN_COSINE`，没有向量时比全文相同），命中就把新反思直接存成已压制、带 `restates`。只对「措辞本身有问题」的压制生效（`_RESTATEMENT_BLOCKING_ISSUES`，以及带 `merged_into` / `restates` 的）。因为过期、矛盾、和角色卡冲突被压制的句子，新事实可以让它重新成立，所以不拦。考虑过「在提示词里列出被否定的说法」，没选。
- **渲染里的同文隐藏**同理分两类：措辞类压制和提及冷却恒隐藏同文反思；过期 / 矛盾 / 角色卡冲突 / 没有 `issue` 的人格压制，只隐藏依据事实早于压制时间的同文反思（合成时记 `source_facts_oldest_at`），全部依据都晚于压制的照常显示。
- 反思合并不跨 entity。来源里有处在提及冷却的条目时，这次合并按 `stale` 拒绝，不改精炼的合并行为。
- 人格压制写 `persona.suppressed` 事件，重放处理器已注册。
- **长期同步**：被压制的人格 / 反思导出时标 `status: superseded`（合并来源带 `supersededById`）；导入时本机已压制的条目一律保留，快照里同编号的行跳过。

不采纳的评审意见：「同文隐藏按 entity 隔离」；「拆分 / 合并出的条目继承依据时间」（缺记录时保守隐藏）。

## 5. 每周自省

模块 docstring 讲了流程。要点和已定取舍：

- 自省审的是 `/new_dialog` 实际注入的那一块，两者走同一个入口 `arender_injected_memory`（返回 `InjectedMemory(pinned, persona)`）。带编号的渲染除了行首编号必须和注入文本逐字节相同（`tests/unit/test_memory_introspection.py::test_annotated_render_matches_injected_render`）。改渲染时两边一起看；预算按不带编号的行算。
- 常驻区在自省里是只读行（`KIND_PINNED`），自省不管常驻区。
- 强力记忆关闭时不跑。从没跑过的角色在第一次空闲时就跑，不等满一周。
- **失败怎么重试**：网络断开、5xx、限流、写盘失败按 `MEMORY_INTROSPECTION_RETRY_BACKOFF_SECONDS` 退避，用完就等下周。超时、输出额度耗尽、JSON 解析失败不提前重试（同样的输入多半同样失败）。进程中途退出不记，重启后重跑。
- ⚠️ 单次调用超时保持 `MEMORY_LLM_HARD_TIMEOUT_SECONDS`（110 秒），**不要调大**：上游代理 120 秒硬断，超过它拿不到任何响应。输出额度按 `MEMORY_INTROSPECTION_OUTPUT_CAPS` 逐级回落。
- 自省 tick 要在 spawn 锁里重读角色名单：锁外读到的名单过期后，渲染会把已删除角色的 persona 文件重建出来。
- 群聊主体各审各的（`arender_scoped_injected_memory`），每个动作应用前核对主体。只有跑完才把开始前的内容指纹记成已查；没查成或没看全的下周再查。

## 6. 行为自省（behavior notes）

每次私聊自省之后跑一次，产出最多几条「少做什么、往哪个方向」的提醒。模块 docstring 是设计说明。已定取舍（同类评审意见不再改）：

- 存独立文件、不进 `arender_injected_memory`，理由见 `layers-and-prompts.md` 第 5 节。
- 用 correction 档并开 thinking，不用便宜档：结果每次开会话都注入，判错的代价高。
- 提醒只有一种语气（少一点 / 可以试试），不分「避免」「少用」。只写方向，不写台词；带引号或照抄回复的会被整条拒绝。
- 证据由代码按模型引用的行号数出来，门槛在 `MEMORY_BEHAVIOR_NOTE_EVIDENCE_GATES`。这组数字是维护者定的，`tests/unit/test_behavior_notes.py::test_gates_are_the_agreed_numbers` 钉着，改之前先问。
- 注入放在 `/new_dialog` 文本的**最后**。实测观察：放在最近对话之前几乎没有效果，因为紧跟着的历史正展示她在做那件事，模型会跟着历史走。
- `history` 是给人事后追溯用的更新记录：不记对话原文、不记被拒的文字，不进提示词。文件不进云存档（可重算）。
- 已知弱点（实测观察）：用户只明确要求过一次的事，很少能留下提醒。
- 用真实对话做验证时，结果进 PR、评论、提交说明、测试的只能是数量和抽象描述，不带对话原文。
