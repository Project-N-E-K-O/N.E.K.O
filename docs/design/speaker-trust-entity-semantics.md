# §2.15 实体中心语义规范（Entity-Centric Semantics Specification）

> **本章地位**：本章把维护者拍板的三条语义（entity 是人 / group_participant 是 entity×group / trust 绑 entity）形式化成可测试的不变量，并给出问题 A（subject 层与多 account 的交互）与问题 B（R11）的正面解法。本章的结论**优先于**主设计 `speaker-trust-platform-neutral.md` 与修订 `speaker-trust-entity-ontology-revision.md` 中与之冲突的段落；冲突处已在 §2.15.8 逐条列出。
>
> **前提（不重新评估）**：`account_id` = `stable_speaker_id()` 的 `platform:actor`，字节不改；QQ 两通道不可自动链接；trust 池上移服务端落 `<memory_dir>/speaker_trust.json`。
>
> **硬约束（贯穿全章）**：① 不得设计任何把两个不同的人合并成一个 entity 的启发式；② 不得改 `speaker_id` 字节；③ 不得让存量 scoped 记忆 / persona section 变孤儿；④ `resolve_trust` 的 `None` 弃权语义不得位移。

---

## 2.15.0 先更正三处既有结论

本章的所有推理建立在下列复核之上。三处此前流传的事实性错误必须先纠正，否则后面的算式会错。

| # | 此前的说法 | 复核结论 |
|---|---|---|
| 1 | 「`subject_forget_tombstones.json` 在仓里不存在，forget 的 tombstone 只是进程内存写围栏」 | **错。** 该文件真实存在且是**持久**的每-subject 抹除水位：`memory/facts.py:978-982` 生成路径、`:984-1002` 严格读、`:1017-1031` `subject_forget_cutoff` / `asubject_forget_cutoff`。进程内存态是另一个东西（`facts.py:334` `_active_subject_forgets`、`:340` `_subject_forget_transaction_locks`），语义是「本次擦除事务的写围栏」。**两者不可混为一谈**：迁移/扇出必须尊重持久水位（§2.15.2.10）。 |
| 2 | 「群召回成员槽只有 3 个（`GROUP_RECALL_MAX_MEMBER_SUBJECTS=4` 减去恒占的群槽）」 | **错。** `config/memory_settings.py:234` 的注释逐字写着「总数 1 + 4 = 5」：`memory_tool_service.py:44` 群占第 1 个，`:57-59` 当前发言人，`:63-65` 由 `_recent_other_speakers` 补 `4-1=3` 人（`:106`）。**成员槽是 4 个，请求总数是 5。** 这直接决定：任何「一个人吃两个槽」的读侧扇出方案，在满员群里是 `1+4×2=9 > 8`，**会撞端点上限**（`routes.py:1892`）。本设计因此不做 subject 级扇出槽位，只做参与者级槽位（§2.15.2.12）。 |
| 3 | 「`MemorySubjectRequest.to_domain()` 有 8 个调用点」 | **错，是 7 个**：`routes.py:1142 / 1248 / 1504 / 1894 / 1949 / 2053 / 2111`。第 8 个命中在 `tests/unit/test_speaker_trust.py:2433`。任何写成 8 的 grep 断言开箱即红。 |

另需澄清：`SPEAKER_TRUST_MAX_REPORTED_BASE` **今天不在树上**，它由主设计新引入（`speaker-trust-platform-neutral.md:438` 定义、`:827` 列在 file_delta）。因此 §2.15.3.4 对它的修改是**对未落地设计的修订**，不是改已发行行为，代价极低。

---

## 2.15.1 三条语义的形式化

记号：`A` = account_id（`platform:actor`，字节固定）；`E(A)` = A 所属 entity；`G` = 会话原始 id；`C(G)` = G 所属 conversation entity（§2.15.2.5 引入）；`S = (kind, subject_id, scope)` = `MemorySubject`。

### S1 —— 同一个真人在两个群、两个渠道、两个角色下，是同一个 entity

**形式化：**

> **S1.a（身份合一）** 对任意两个 account `A₁ A₂`，若操作者已断言二者同人，则 `E(A₁) = E(A₂)`，且该等式与 group、platform、character、时间全部无关。
> **S1.b（人身属性合一）** 一切「挣来的」人身属性——`adjustment`、`activity`、自证禁令的判定——必须以 `E` 为求和/判定域，而非以 `A`。
> **S1.c（授权不合一）** 一切「被授予的」属性——`base` 档位、`speaker_is_owner`——必须以 `A` 为域，**绝不由 `E` 推导**（理由见 §2.15.3.4；这不是对 S1 的削弱，是 S1 的正确边界）。
> **S1.d（记忆不合一）** S1 **不**蕴含跨群记忆可见。跨群可见由 S2 显式禁止。

| | 成立性 | 靠什么保证 |
|---|---|---|
| 今天 | **S1.a 假**：无 entity 层，`account_id` 就是身份终点。`permission.py:62/:125/:323` 的信任表与 trust 池都是按裸 QQ 号索引的扁平字典。 | — |
| 本设计后 | **S1.a 真**（在操作者已断言的范围内）；**S1.b 真**；**S1.c 真**；**S1.d 真** | S1.a：`TrustSnapshot.entity_of` / `same_entity`（主设计 §2.6），无任何自动建边路径。S1.b：`trust_inputs` 对 `E` 下全部 account 求和（§2.15.3.1）。S1.c：`resolve_trust` 的 `base` 入参只来自本次请求的 account（§2.15.3.4）。S1.d：读侧展开只在**同一 conversation** 内做（§2.15.2.5 的定义域），`resolve_group_recall_subjects` 的第二个群槽位仍刻意留空。 |

**可执行不变量：**

- **I-S1-1** 任意池状态、任意 N，`resolve_trust(A) − base(A)` 与「把 (Σadj, Σmc) 如何分摊到 N 个 account」无关（重分区不变性，见 I-T-1）。
- **I-S1-2** 同一 entity 的两个 account 分别 `tier='admin'` / `tier='none'`，两次 `resolve_trust` 之差**恰好等于** `1.0 − 0.3`（证明 base 未被聚合，且 adjustment/activity 项逐位相同）。
- **I-S1-3** `speaker_is_owner=True` 的 wire 校验仍硬绑 `tier=='admin'`；构造「entity 内另一 account 是 admin」的场景，断言本 account 的 `speaker_is_owner` 仍为 False。
- **I-S1-4** 跨群不可读：entity `E` 在群 `G₁`、`G₂` 均有语料，从 `G₁` 的任意 subject 出发的展开集合中，不存在任何 `subject_id` 第二段为 `G₂` 的 marker。

**R11 兑现时的降级**：S1.a 在开放平台通道上**只在操作者逐个断言的范围内成立**，范围外为假，且必须**显式可见**（§2.15.4.3），不得写成「近似满足」。

---

### S2 —— 同一个 entity 在同一个 group，是同一个 group participant

**形式化：**

> **S2.a（唯一性）** 对任意 (entity `E`, conversation `C`, kind `k ∈ {group_participant}`)，系统中**恰好存在一个**「参与者」，它在读侧是一个授权域、一个渲染标题、一个预算槽；在写侧是一个 canonical subject。
> **S2.b（隔离性）** 不同的 (E, C) 之间零可见性。特别地：同一 `E` 在 `C₁ C₂` 是两个参与者，互不可读。
> **S2.c（对称推广）** `participant`（私聊）= (E, ∅)；`group_chat` = (∅, C)。两者与 S2.a 同规则，**不得只做 group_participant**。
> **S2.d（存量不动）** S2.a 由**解析层**实现，`subject_id` / `scope` 的**存量字节一个不改**。

| | 成立性 | 靠什么保证 |
|---|---|---|
| 今天 | **假**，且比表述的更宽：`subject_id` 的 speaker 段填的是 account 的 actor（`memory_bridge.py:55-62` ← `message_dispatcher.py:22/:310` 的 `message["user_id"]`），group 段填的是通道相关的会话 id（napcat 给 uin 群号、开放平台给 `data.get("group_id")`，`qq_open_plat.py:605`）。**两段都会分裂。** | — |
| 本设计后 | **真**（人侧在 R11 未兑现或已人工断言时；群侧在 conversation 已 bind 时）。R11 兑现且未断言时**为假**，并由 `platform_identity_scope` 显式暴露（§2.15.4.3），不静默。 | 请求级参与者折叠（§2.15.2.4）+ 参与者决定的 marker 集合、读侧永不截断（§2.15.2.5）+ 封定式 canonical 写路由（§2.15.2.6）+ 归档实体化（§2.15.2.11）。 |

**可执行不变量：**

- **I-S2-1（唯一性，最强的一条）** 对任意一次读请求与任意池状态：把请求的 subject 列表折叠后，**不存在两个不同的槽位其 (entity, conversation, kind) 三元组相同**。测试必须包含：请求里同时带同一个人的两个 account 的 subject（这是今天 `_recent_other_speakers` 按 sender 去重的真实产物，`memory_tool_service.py:63-67`）。
- **I-S2-2（marker 集合是参与者的函数）** 对同一 (E, C, k)，从该实体**任意** account 的 subject 出发，`markers()` 返回**逐元素相等**的集合。禁止任何依赖「从哪个 account 出发」或依赖易变字段（如 `channels_seen.last`）的排序/截断。
- **I-S2-3（一人一槽）** 一个 2-account 实体与一个 1-account 实体同群进入 `scoped_context`：两者拿到的 `PERSONA_RENDER_MAX_TOKENS` / `REFLECTION_RENDER_MAX_TOKENS` 逐位相同；渲染出的 `### ` scoped 标题数 == 折叠后的槽位数（不是展开后的 marker 数）；标题里出现的 `subject_id` 只有 primary 的那一个。
- **I-S2-4（新写可从任一 account 读到）** 经 canonical 路由写入的新行，从该实体在该群的**每一个** account 的 subject 出发都能被 `filter_entries_for_subjects` 命中。**这条是整套设计存在的理由，也是最容易被实现细节打掉的一条**（见 §2.15.2.3）。
- **I-S2-5（对称覆盖）** I-S2-1..4 对 `participant` 与 `group_chat` 两个 kind 各跑一遍。

---

### S3 —— trust 绑在 entity 上，不绑在 account 上

**形式化：**

> **S3.a** `adjustment` 与 `activity` 的求和域是 `E`，且求和**无损**（不逐 account 夹）。
> **S3.b** 两个 clamp（`±0.30` 与 `0.02`）各只在 `resolve_trust` 一处、在 Σ 之外施加一次。
> **S3.c** 上确界与 account 数 `N` 无关：`sup = base + 0.30 + 0.02`。
> **S3.d** `base` 不聚合（S1.c）。因此 **S3 是「trust 的可挣得部分绑 entity」，不是「trust 全绑 entity」**——这一点必须写在设计里，不得表述成「trust 绑 entity，完全实现」。

| | 成立性 | 靠什么保证 |
|---|---|---|
| 今天 | **部分真、口径错**：`permission.py:247-279` 的三段式已经是「全局按 QQ 号共享」（`config/memory_settings.py:256-258` 的注释自称「产品拍板的跨 scope 通道」），但那个「全局」的粒度是 **account**，不是 entity——换个通道就是另一个人。 | — |
| 本设计后 | **S3.a-c 真**；**S3.d 为有意的部分实现，需显式接受** | `trust_inputs` 返回未夹原始和 + `resolve_trust` 是唯一 clamp 点（§2.15.3.1）；I-T-1 重分区不变性把 clamp 位置钉死。 |

**S3.d 的量化诚实披露（必须写进文档，不能省）**：`base` 的量程 `1.0 − 0.3 = 0.70` 是 entity 级预算（`0.30 + 0.02`）的 **2.2 倍**。同一个人，QQ admin 段 `clamp01(1.0+0+0.02)=1.0`（`trust_band='high'`，阈值 0.75，`speaker_trust.py:148`），B 站 none 段 `clamp01(0.3+0+0.02)=0.32`（`'low'`，阈值 0.45，`:150`），差 **0.68 ≫ `SPEAKER_TRUST_ARBITRATION_MARGIN=0.15`**。这不是缺陷，是 S1.c 的必然算术后果；但它有两个必须一起做的配套，否则它会变成缺陷：

1. **同实体不自我仲裁**（§2.15.3.10）——否则一个人的旧发言会确定性硬覆盖他自己的新发言。
2. **同实体不折叠 provenance 的 `speaker_trust`**（§2.15.2.10）——否则 `provenance_of_entries` 的 `min(trusts)`（`speaker_trust.py:581`）会把主人已落盘的 1.0 就地改写成 0.32，形成**单向落盘棘轮**（永不回升），把主人的行降到 `'low'` 并让它输给 `base=0.5` 的路人（差 0.18 > margin）。

**可执行不变量：** I-T-1 ~ I-T-8，见 §2.15.3.11。

---

## 2.15.2 问题 A 的解法：PFO-SCR（参与者折叠 + 读时展开 + 封定式 canonical 写路由）

### 2.15.2.1 总纲与结构前提

> **一句话：`subject_id` / `scope` 的存量字节永不改。「同一 entity 在同一 group 只有一个 participant」在服务端的解析层实现——请求进来先按参与者折叠槽位，读时把参与者展开成一组 marker，写时把 subject 路由到一个封定的 canonical account。插件侧零改动。**

三条已复核的结构前提：

1. **服务端从不校验 `segment.speaker_id` 与 subject 第三段的关系。** `routes.py:1504`（取 subject）与 `:1536-1552`（`speaker_id` 单独过 `stable_speaker_id`，只校验自身形状）之间零交叉断言；`ScopedHistorySegment`（`routes.py:975-999`）两个字段独立声明；`scopes.py:88-96` 对 group_participant 第三段的**内容**无约束（只要求 3 段非空）。⇒ 服务端重写 subject 第三段是**结构合法的，零校验改动**。
2. **归属判定全系统唯一入口是 `entry_matches_subject`**（`scopes.py:221-229`），语义是 `(key, scope)` 的**双重字节相等**，无别名、无前缀、无规范化。`filter_entries_for_subjects`（`:250-277`）是它的集合形式，用 `{(key, scope)}` 成员判定 ⇒ **展开集合变大对过滤是 O(1) 零成本**。
3. **「一次读多个 subject 并合并」是已有的生产路径**（`scoped_context` / `query_memory` / `scoped_mentions`，端点各自 `1..8`），不需要发明新机制。

### 2.15.2.2 三个层次（严格分层，`scopes.py` 保持零 IO）

| 层 | 位置 | 允许做什么 |
|---|---|---|
| **值层** | `memory/scopes.py` | 新增 `@dataclass(frozen=True, slots=True) ParticipantGroup`（`primary: MemorySubject` + `members: tuple[MemorySubject, ...]` + `markers: frozenset[tuple[str,str]]`）与 `flatten_groups()`。**不 import trust_store、零 IO。** 既有 `entry_matches_subject` / `filter_entries_for_subjects` / `subject_from_entry` / `normalize_subjects` **一字不改**。 |
| **解析层** | **新** `memory/subject_identity.py` | subject 层**唯一** import trust_store 的模块。`fold_participants(subjects, snap) -> tuple[ParticipantGroup, ...]`、`canonical_subject(s, snap) -> MemorySubject`、`participant_key(s, snap)`。 |
| **端点层** | `app/memory_server/routes.py` | 7 个 `to_domain()` 点各套一层；读侧折叠+展开，写侧路由。 |

这样正面绕开「在 `scopes.py` 里做别名层」的层次倒置：`MemorySubject` 是 frozen+slots 的纯值类型（`scopes.py:75-102`），一旦让它读 `speaker_trust.json` 就把值语义毁了。`ParticipantGroup` 只是「这几个 subject 属于同一个参与者」这条**已经被别人算好的结论**的容器。

### 2.15.2.3 【F1 修复】归一化的定义域：只处理默认 scope，且 canonical 必须重新派生 scope

这是整套设计最容易被实现细节打掉的一条，必须写死。

**问题**：`MemorySubject` 是 frozen+slots，重写 subject_id 最顺手的写法是 `dataclasses.replace(s, subject_id=...)`，而那会留下**旧的 scope**。`scope` 在 wire 上是独立可选字段（`routes.py:944` `scope: str | None = None`），`MemorySubject.create` 只在 `scope is None` 时派生 `f"{kind}:{subject_id}"`（`scopes.py:114-115`）。而归属判据是 **(key, scope) 二元组**字节相等。于是 `replace` 出来的 `(qq:G:A_c, group_participant:qq:G:A₁)` 既不在 A₁ 的展开集里、也不在 A₂ 的里——**每一条新写入的行都会变成谁也读不到的孤儿**，恰好是设计目的的反面。

**规范（三条，缺一不可）：**

- **N-1 定义域限制**：`fold_participants` / `canonical_subject` **只处理默认 scope 的 subject**，即 `s.scope == f"{s.kind}:{s.subject_id}"`。非默认 scope ⇒ **恒等弃权**（返回单元素 group / 返回原 subject）。
  理由三条：(a) 插件从不发 scope（`memory_bridge.py:49-74` 三个 builder 都没有 scope 键），所以 100% 生产流量是默认 scope，限制不损失任何现实收益；(b) 自定义 scope 是调用方**显式声明的隔离边界**，静默重定向等于替调用方作废他的边界；(c) 代码里已有的两处 subject 折叠——`_fact_dedup_domain`（`fact_dedup.py:157-166`）与 `_in_signal_scope`（`facts.py:1306-1319`）——**都只对默认 scope 生效**，本规范与它们同定义域，不引入第三种口径。
- **N-2 canonical 必须重新派生**：canonical subject 一律由 `MemorySubject.group_participant(platform, conv, actor)` / `.participant(...)` / `.group_chat(...)` 构造（它们内部走 `create` 并派生 scope，`scopes.py:120-149`），**禁止使用 `dataclasses.replace`**。lint 规则 + 一条 grep 断言。
- **N-3 展开构造必须防御式**：`stable_speaker_id` 的 actor 字符集是 `[A-Za-z0-9_.:@-]+`（`speaker_trust.py:184`），**允许冒号**；而 group_participant 强制恰好三段（`scopes.py:88-96`）。因此从 (platform, conv, actor) 组合 marker 时必须 try/except `MemoryScopeError` 并**丢弃不可构造的组合**，否则一次读路径展开就能 500 掉整个 `scoped_context`。丢弃要计数并 warning，不得静默。

**配套不变量 I-S2-4（golden 测试，写侧路由 PR 的合入门槛）**：写一条经 canonical 路由的行 → 断言它从该实体在该群**每一个** account 的 subject 出发都能读到；并断言其 `scope` 恒等于 `f"{kind}:{subject_id}"`。

### 2.15.2.4 【F2(a) 修复】请求级参与者折叠——先折叠，再展开

**问题**：`_recent_other_speakers`（`memory_tool_service.py:63-67`）按 **sender_id（account）** 去重，不按 entity。一个人的两个小号同群先后发言，请求里就会同时出现 `qq:G:A₁` 与 `qq:G:A₂`。若只做「逐 subject 展开」，会得到两个 `ParticipantGroup`、两个槽、两份预算、两个 `### ` 标题，而**两个标题的 primary subject_id 完全相同**——模型看到「同一个 id 的两个人」，比不展开更糟。

**规范**：展开是**请求级两阶段**操作，不是逐 subject 操作。

```
输入：req.subjects（已过 1..8 校验，已 to_domain）
① 计算 participant_key(s) = (kind, E(actor段) or actor段, C(conv段) or conv段)
   —— 未注册 / 池未加载 / 非默认 scope ⇒ key 退化为 (kind, subject_id)，即恒等
② 按 participant_key 分组；组内保序，第一个出现的 subject 决定该槽的位置
   （顺序 = 预算优先级，routes.py:1852-1878 的契约，不得改动）
③ 每组产出一个 ParticipantGroup：
     primary  = canonical subject（若可解析）否则该组第一个 subject
     members  = 该 (E, C) 下全部 account 的 subject（§2.15.2.5）∪ 组内出现过的全部原 subject
     markers  = frozenset((m.key, m.scope) for m in members)
④ 槽位列表 = ParticipantGroup 序列（长度 ≤ 原 subject 数，只会变短）
⑤ 授权集合 = flatten_groups(...)，喂给 filter_* / hybrid_recall
```

关键性质：**折叠只会让槽位变少**，所以 wire 侧 `1..8` 校验（`routes.py:1892 / 2048 / 2106`）在折叠之前跑，天然仍然成立；`GROUP_RECALL_MAX_MEMBER_SUBJECTS=4`（总 5）与 `SCOPED_RENDER_*` 一律不动。这就是本方案相对「纯读侧 subject 扇出」的核心差别：那条路会把满员群的 5 个请求 subject 扇成 9~10 个，**直接撞 8 的上限**（见 §2.15.0 更正 2）。

**I-S2-1** 就是这一节的可执行形式。

### 2.15.2.5 【F2(b) 修复】marker 集合是参与者的函数；读侧**永不截断**，上限在 bind 时强制

**问题**：任何「展开时截到前 K 个」的规则，都会让「从 A₁ 出发」与「从 A₅ 出发」得到**成员不同**的集合，于是两个 `ParticipantGroup` 结构上不相等，按值去重救不回来；同时 canonical 那个 marker 同时属于两个 group，`marker → group` 查表出现一对多，必有一个桶被饿死或双计。排序键若含 `channels_seen.last`（随每条消息变化），截断集合还会**逐轮抖动**，同一个人的老堆时隐时现。

**规范：**

- **M-1 无读侧截断。** `members` 是 `(E, C, kind)` 下**全部** account 的 subject，一个不少。合法性依据：展开宽度对**过滤**是 O(1) 集合命中（`scopes.py:265-276`），成本不在这里。
- **M-2 上限在 bind 时强制。** 新增 `IDENTITY_MAX_ACCOUNTS_PER_ENTITY_PER_PLATFORM = 8`，在 `_bind_locked` / `_merge_entities_locked` 的临界区内校验，超限 **409 并拒绝**，附「该实体在 qq 已有 8 个 account」的可读消息。bind 是人工、稀有操作，409 是完全可接受的失败模式；而读时截断不是。
- **M-3 确定性全序。** 需要排序的地方（渲染标题选名、forget 顺序、诊断输出）一律用 `(bound_at, account_id)` 的字典序，**禁止使用 `channels_seen.last` 等易变字段**。canonical 恒排第一。
- **M-4 成本上界的正确落点。** 真实成本只有三处，且都随 marker 数线性、被 M-2 静态封顶：(a) `hybrid_recall` 无跨条目文本去重（`hybrid_recall.py:665-700`），近重复条目互相挤 top-k；(b) `scoped_forget` 的扇出宽度；(c) persona section 遍历。三处都不需要「读时截断」来控制。

⇒ **I-S2-2** 成立，且 `SUBJECT_IDENTITY_EXPANSION_MAX` / `_TOTAL_MAX` 这两个常量**不引入**（引入它们就是把 M-1 打破）。

### 2.15.2.6 写侧：封定式（sealed）canonical 路由

池里新增（并入主设计 §3.5 的 `_copy_on_write` 容器清单）：

```jsonc
"entities": { "ent_…": {
    "canonical_accounts":      {"qq": {"account_id": "qq:123456", "sealed_at": "…"}},
    "canonical_conversations": {"qq": {"conversation_id": "qq:87654321", "sealed_at": "…"}},
    "superseded_canonicals":   [{"platform": "qq", "account_id": "qq:999", "at": "…"}]
}}
```

三条 mutator 规则，全部在池的单写者临界区内：

- **R-CANON-1 懒封定**：写路径发现 (E, platform) 无记录 ⇒ 把**当前发言的那个 account** 封定为 canonical。
- **R-CANON-2 只在成员离开时解封**：`_unbind_locked` / `_forget_entity_locked` 移走的正是 canonical ⇒ 清空该 platform 记录，下一次写重新封定。unbind 非 canonical ⇒ 逐字节不变。
- **R-CANON-3 merge 不改存活者**：`_merge_entities_locked` 的存活者由 `sorted((created_at, entity_id))` 决定；**存活者保留自己的 canonical**；存活者该 platform 为空才领养被吸收方的；两边都有 ⇒ 被吸收方的进 `superseded_canonicals`（只读诊断）。

**稳定性定理**：merge 的存活者是 `(created_at, entity_id)` 全序的最小值 ⇒ 幂等、可交换、可结合 ⇒ 最终存活者是最终实体集合的函数，与合并顺序无关；而存活者的 canonical 是它自己的 ⇒ **canonical 只在「首次封定」与「canonical 账号离开实体」两种情形下变化**。

**为什么不用派生式**（`min(account_id)` / 最早 `bound_at` / seed 账号）：三者都会在 merge 时翻转（并集变大，最小值可能换人），每翻转一次就把上一段时期的新写落到**第三个** subject 上，制造第三堆，一次比一次难收敛。代价是 canonical 依赖事件顺序、不是纯状态函数——由 I-C-1/I-C-2 补回可测性。

**写路由的插入点与硬顺序**：`routes.py:1142`（scoped_facts）、`:1248`（legacy 单发 scoped_history）、`:1504`（批段），三处在 `to_domain()` 之后立刻 `subject = canonical_subject(subject, snap)`，且**必须在 locale 预约之前**（见 §2.15.2.9）。

### 2.15.2.7 为什么不孤儿化存量（逐环核对）

改 `subject_id` 字节的 7 环孤儿链条，本设计**一环都不触发**：

| 环 | 代码位置 | 本设计为什么不踩 |
|---|---|---|
| 1 召回池被过滤掉 | `hybrid_recall.py:646-654 / 822-824` | 展开集合**恒含**该实体在该 conversation 的全部原 subject（M-1）；判据未改。 |
| 2 persona section 永不渲染 | `persona/rendering.py:88-135` | 该文件 `:100-112` 的注释明写「**逐条**授权而非按 section metadata 整段放行」，走 entry 自己的三字段戳；老戳仍在 allowed_keys 里。 |
| 3 老 section 连 legacy 回退都进不去 | `rendering.py:90-94` | 不适用——没有产生「老 section 不在授权集里」这个前提。 |
| 4 `scoped_forget` 再也删不掉 | `facts.py:775-791` 等 | forget 按 marker 集合扇出（§2.15.2.8），老堆仍有清除路径。**这一环最伤（退群清档留底＝隐私回归），必须由 §2.15.2.8 保证。** |
| 5 归档扫描把老堆当独立 subject | `subject_archive.py:92-149` + `SCOPED_SUBJECT_STALE_DAYS=90` | 由 §2.15.2.11 的 `coalesce` 消除。 |
| 6 durable per-subject locale 重置 | `locale_state.py:187-197` | 由 §2.15.2.9 的读写同键消除。 |
| 7 trust 事件 4 元组身份失配 | `facts.py:1390-1403 / 1330-1349` | **本设计不重写任何存量行**，`source_subject_*` 与 `event_id` 一个字节不动 ⇒ replay 与幂等环完全不受影响。这正是本设计相对「物理迁移」路线最大的结构性优势：迁移路线必须重算 `signal_key → event_id` 并原位替换幂等环，任何一步错就是 trust 事件**二次计入**。 |

### 2.15.2.8 【F3 修复】`scoped_forget`：**单事务、多 subject**，不是 N 次独立事务

`routes.py:1953-1955` 的注释明文写死了约束：*Component references are atomically replaced under this lock. Keep the same generation alive until every tombstone is closed, otherwise reload can split one forget transaction across old and new managers.* 拆成 N 次独立事务会同时打破两件事：(a) 两个 subject 之间 `_reload_lock` 被释放，reload 可以插进来；(b) subject i 的墓碑在 subject i+1 的墓碑打开**之前**就关闭，而 fact 抽取 / reflection 合成在 LLM 调用期间放锁——一个在 forget 前就捕获了 subject i 的在途写入，可以在其墓碑关闭后落盘，**删过的域重新长出数据**。这是隐私路径，不能靠「canonical 最后删」的顺序论证兜住。

**规范（改造后的事务骨架）：**

```
markers = 展开(req.subject)             # 该 (E, C, kind) 的全部 subject
targets = sorted(markers, key=(key, scope))   # 确定性序，跨并发 forget 免死锁
                                              # canonical 恒排最后

await runtime._reload_lock.acquire()          # 一次，全程持有（不变）
try:
    for s in targets: acquire(_get_subject_forget_transaction_lock(name, s))   # 全部
    for s in targets: await fact_store.abegin_subject_forget(name, s)          # 全部墓碑先开
    for s in targets: await reflection_engine.abegin_subject_forget(name, s)
    for s in targets:                                                          # 再逐个擦
        stats += fact_dedup_resolver.aforget_subject(name, s)
        stats += fact_store.aforget_subject(name, s)
        stats += reflection_engine.aforget_subject(name, s)
        stats += persona_manager.aforget_subject(name, s)
        stats += locale_state.forget_subject_prompt_locale(name, s)
    for s in targets: await fact_store.afinalize_subject_forget(name, s)       # 墓碑仍全开时推水位
finally:
    逆序 aend_subject_forget / release，全部在同一个 finally 链里
```

可行性已核实：写围栏状态是 `set[tuple[str,str,str]]`（`facts.py:334`）与按 (name,key,scope) 分桶的锁字典（`:340`），**不同 subject 是不同键**，同一任务内并存多个围栏结构上成立。

**语义决定（需拍板，见 §2.15.7）**：`scoped_forget` 扇出到整个参与者，是 S2 的直接推论（participant = entity × conversation，删的是这个隔离单元）。但它把一次「退群清档」放大成多个 account 堆的不可逆删除。

**持久水位必须一起扇出**：`afinalize_subject_forget` 推的是 `subject_forget_tombstones.json`（`facts.py:978-1031`，§2.15.0 更正 1），它是**每 subject 一条**的持久抹除水位，用于挡住归档分片经事件重放复活。少推任何一个 marker 的水位，那个 account 的老归档分片就还能被 `arestore_scoped_subject` 恢复。

### 2.15.2.9 locale：读写必须同键

**问题（今天就存在、写路由会放大）**：`_resolve_scoped_memory_language` 在**外层 wrapper** `routes.py:1841`（`scoped_context`）与 `:2115`（`query_memory`）被调用，吃的是**wire 模型** `req.subjects`，发生在 `to_domain()` 之前、任何路由之前；而写路径按 §2.15.2.6 在 `:1152 / :1258 / :1567` 的 locale 预约**之前**改路由。于是被路由的非 canonical account：**写进 `S_canonical`、读查 `S_A`，永久 miss**，静默回落角色级 locale——`durable per-subject 语言态`这件事的全部意义被抹掉。键是 `json([kind, subject_id, scope])`（`locale_state.py:187-197`）。

**规范：**

- **L-1** `_resolve_scoped_memory_language` 内部先 `to_domain()` → `canonical_subject()` 再查表。（它已经拿到 wire 模型，coerce 是本地操作，不改签名。）
- **L-2** 只喂 **primary**（每个 `ParticipantGroup` 一个），不喂 members——`_SCOPED_LOCALE_LOOKUP_LIMIT = 8`（`routes.py:120`）是线程池预算，喂展开结果会翻倍。折叠只会让 primary 数 ≤ 原 subject 数 ≤ 8，天然满足。
- **L-3** forget 时逐 marker 调 `forget_subject_prompt_locale`（已并入 §2.15.2.8 的循环）。

### 2.15.2.10 provenance：三态判定（same / different / **unknown**），彻底封死 mixed 重开

这是写侧路由**唯一**会新开的吸收态路径，也是本设计里改动语义最深的一处，必须逐条写死。

**因果链（逐处核实）**：canonical 路由让同实体两个 account 的行**首次共享同一个 subject**；而 fact 的 hash 盐了 subject（`facts.py:3567-3570` `hash_input = f"{key}\n{scope}\n{…}"`）⇒ 同一句话得到同一 hash ⇒ `_reconcile_existing_provenance`（`facts.py:3453-3499`）发现 `existing_speaker_id != request speaker_id` ⇒ **`{'speaker_provenance_mixed': True}`**（`:3479-3480`），全仓无清除路径。语义去重侧更常触发：`_pair_can_share_dedup`（`fact_dedup.py:186-194`）对**subject 三元组相等**的两行直接 `return True` ⇒ `_fold_survivor_provenance` 的 `len(attributed_ids) > 1` ⇒ mixed。且 `semantic_dedup` 默认 True（`facts.py:3316`），**「不重算 hash」规避不掉 Stage-2**。

**规范 P-1：新增持久字段 `speaker_entity_id`**（写路径 provenance 增列；`speaker_id` **一个字节不改**，硬约束满足）。

**规范 P-2：三态判定函数**（新增 `memory/speaker_trust.py::same_provenance_source(a, b) -> bool | None`）：

| 条件 | 返回 |
|---|---|
| `speaker_id` 字节相等 | `True` |
| 两侧都带 `speaker_entity_id` 且相等 | `True` |
| 池已加载 **且两侧 account 都已注册** 且 `_resolve_entity` 同解 | `True` |
| 池已加载 **且两侧 account 都已注册** 且解到不同 entity | `False` |
| 其余（池未加载 / 任一 account 未注册 / 任一侧信息缺失） | **`None`（未知）** |

> **【实现期更正：第 5 行收紧】** 落地时把「任一 account 未注册 ⇒ `None`」改成
> **`False`**，`None` 只保留给「池未加载 / 任一 id 缺失或畸形」。
>
> 理由：account 只有在有活跃度或信号时才进池，所以「未注册」是绝大多数 account
> 的常态；按原表 `None` 会成为最常见的返回值，`speaker_provenance_mixed` 对**两个
> 真正不同的人**也基本不再写 —— 正是 I-P-6 明令禁止的回归。改造既有测试时这条被
> 11 条既有用例同时抓到（`test_derived_provenance_marks_multiple_stable_speakers_as_mixed`
> 等）。
>
> 而「未注册 ⇒ 不同人」是**可证的**：bind 会把两侧都注册，所以不在
> `account_index` 里的 account 必然是单例实体，与另一个不同的 account 字符串不可能
> 是同一个人。本行原本的论证（§2.15.2.10 P-3 第四行）通篇只针对「池未加载窗口」，
> 收紧后那条论证逐字仍然成立。
>
> 「误绑 → 解绑」的 mixed 泵也依然封着：搁浅行持久化的 `speaker_entity_id` 等于 A
> 当前的 entity，命中表格第 2 行 `True`，**在查池之前就短路**。

**规范 P-3：三态各自的动作**

| `same_provenance_source` | `_reconcile_existing_provenance` 的动作 | `_fold_survivor_provenance` / `provenance_of_entries` 的动作 |
|---|---|---|
| `True` 且 `speaker_id` **字节相等** | 折叠（今天的行为，含 `min(trusts)`，逐字不动） | 折叠（今天的行为） |
| `True` 但 `speaker_id` **不等**（同实体、跨 account） | **保留 existing 的 provenance 原样**：不折叠、不取 min、不改 `speaker_id`、**不写 mixed** | 同左：保留 survivor 自己的 provenance 原样 |
| `False` | **写 mixed**（今天的行为，回归必须保持） | 写 mixed |
| `None` | **保留 existing 原样，不写 mixed**，计 `provenance_deferred` 指标并 warning | 同左 |

**P-3 第二行是关键，它同时修掉两个致命问题：**

- 它避免了 `min(trusts)`（`speaker_trust.py:581`）跨 account 生效。若允许折叠，主人一条已落盘 `speaker_trust=1.0` 的行，会在他用低 base 通道说同样的话时被就地改写成 `0.32` —— `min` **单向下降永不回升**，是落盘棘轮：该行的 `trust_band` 从 `'high'` 掉到 `'low'`，且因为它不再是 mixed，`scoped_refine.py:186` 的 usable 过滤**不再排除它**，它会带着 0.32 进入确定性仲裁，被任何 `base=0.5` 的普通成员以 0.18 > margin 覆盖。这正是「弃权 → 参战并落败」的语义位移，必须封死。
- 它是「同实体 ⇒ 弃权而非合并」这条纪律在 provenance 轴上的落地，与 §2.15.3.10 在仲裁轴上的同一条纪律同构。

**P-3 第四行（`None` ⇒ 弃权）是对「池未加载窗口」的正确回答。** 主设计 line 345-346 明写 `_load_failed` 只是「本进程只读降级」，memory_server 仍照常收 scoped 写入 ⇒ 这不是理论窗口。此时 canonical 路由恒等（互锁 P-4），但**已经被路由过来的历史行仍躺在 canonical subject 里**；canonical account 本人再写同一句话就会撞上它。今天的 else 分支会把它打成 mixed。**「不知道」必须永远不被记录成「已知混合」**——这是本章唯一一条无条件的元规则。

同一条规则也顺带覆盖 **unbind 之后**的残留：解绑后 B 不再在实体里，`canonical_subject(B)` 立刻回到 B 自己（读写都是live 池查询，没有持久路由表，所以**没有「解绑后仍继续路由」的缺陷**）；而 canonical subject 里那些 `speaker_id=B` 的搁浅行，其 `speaker_entity_id` 恰好等于 A 当前的 entity（unbind 给 **B** 建新实体，A 保留原实体）⇒ 判定为 `True` ⇒ 保留 B 的字节、不写 mixed。**结论：本设计不存在「误绑→合并→解绑」的 mixed 泵。**

**规范 P-4 互锁（写成断言，不靠约定）**：`canonical_subject` 在池未加载 / account 未注册时**必须**返回原 subject。配套不变量 I-P-3。

### 2.15.2.11 归档实体化（否则 S2 会在 90 天后自己失效）

`subject_archive.collect_subject_last_writes`（`:92-127`）从**数据自身**派生 staleness，`find_stale_subjects`（`:129-149`）在 `SCOPED_SUBJECT_STALE_DAYS = 90`（`config/memory_settings.py:719`）后判定 stale。canonical 路由后，非 canonical 堆停止写入 ⇒ 90 天后被归档——而这个人明明还活跃。

**规范**：新增 `coalesce_participant_last_writes(last_writes, group_resolver)`：同一 `ParticipantGroup` 内取 max 时间戳并**写回每个 marker**，再交给 `find_stale_subjects`。resolver 由 sweep 调用方注入，`subject_archive.py` 本身仍不 import trust_store。

注意与 M-1 的相互作用：因为不做读侧截断、又做归档合并，**展开是永久的、不是过渡的**。这是主动选择：让老堆自然衰减 = 让 S2 在 90 天后自己失效。预算设计因此必须对 N 个 account 鲁棒——这正是「一人一槽」存在的理由。

### 2.15.2.12 渲染与预算：一人一槽、一个标题、一个 id

- `_subject_render_slots`（`persona/rendering.py:806-834`）改成返回 `ParticipantGroup` 槽；`_subject_bucket_marker`（`:837-844`）/ `_bucket_entries_by_subject`（`:846-868`）改成 `marker → group` 查表（由 §2.15.2.4 的折叠 + §2.15.2.5 的无截断保证**一对一**，不会出现一个 marker 属于两个 group）。
- `_trim_scoped_by_subject` / `_atrim_scoped_by_subject`（`:920-1044`）的分配循环体**一字不改**，只把迭代对象换成 group ⇒ `PERSONA_RENDER_MAX_TOKENS=2000` / `REFLECTION_RENDER_MAX_TOKENS=2000` / `SCOPED_RENDER_SUBJECT_MIN_TOKENS=200` / `SCOPED_RENDER_TOTAL_MAX_TOKENS=16000` 全部按**人**分配，`routes.py:1852-1878` 的「顺序即预算优先级」契约不变。
- 标题合一：`rendering.py:663-699` 的段落循环按 group 聚合，同 group 的多个 `@subject/...` section 合成**一个** `### ` 标题，`subject_id` 取 primary。`display_name` 取 primary 的；primary 无名时回落到 group 内**唯一**的那个非空 display_name；出现两个不同名字则**不带名字**（与 `rendering.py:120-132` 在混域时主动 `pop('display_name')` 同一条纪律）。

### 2.15.2.13 失败模式全表

| 情形 | 系统状态 | 是否产生不可达行 | 是否产生 mixed |
|---|---|---|---|
| 池未加载 / 未注册 / 非默认 scope | 恒等退化，与今天逐字节相同 | 否 | 否（P-3 `None` 分支弃权） |
| 已 bind、未封定 | 首次写触发懒封定；此前的行留在各自 subject，读侧照常展开 | 否 | 否 |
| 已 bind、已封定 | 新写入 canonical；老堆读侧带 | 否 | 否（P-3 `True` 分支保留原样） |
| merge（存活者不变） | canonical 不动 | 否 | 否 |
| merge（存活者的该 platform 为空，领养） | canonical 首次出现，之后不变 | 否 | 否 |
| unbind 非 canonical | 路由立刻停止；搁浅行留在 canonical 堆 | 否 | 否（`speaker_entity_id` 兜住） |
| unbind canonical | 解封；下次写重新封定，可能是第三个堆 | 否 | 否 |
| 误绑后解绑 | 绑定期间落的行**不可逆**留在 canonical 堆 | 否 | 否 |
| 展开构造遇畸形组合 | 丢弃该 marker + warning | 否（只是少读一个不存在的桶） | 否 |
| forget 中途异常 | 全部墓碑仍开、`_reload_lock` 仍持有，异常沿 finally 逆序收尾；已删的不复活（重试幂等） | 否 | 否 |

**唯一不可逆面（必须公开）**：写侧路由期间落的行，其 `subject_id` 是 canonical 的、`speaker_id` 是真实 account 的。unbind 之后这些行留在别人的堆里。**不提供自动修复**——把行搬回原 subject 必须重算盐了 subject 的 hash（`facts.py:3567-3570`），重算后可能与目标 subject 的既有行塌成同一 hash，正是要躲的陷阱。代替品：`unbind` 响应回一个 `stranded_rows` 计数（「subject 第三段 == canonical 且 `speaker_id` == 被解绑 account」的活跃 fact 行数），核选项是 `scoped_forget`。

**两条方向相反的运维口径，必须同时写进「替换 ID（保留信赖度）」的操作说明**：
- **trust 轴：宁可早绑**（晚绑会先积累一批自签信号且 merge 不退还，§2.15.3.9）。
- **subject 轴：宁可晚绑**（错绑期间的行不可逆）。
把其中一条藏起来比两条都说更危险。

---

## 2.15.3 entity 级 trust 聚合规范

### 2.15.3.1 总算式（唯一自洽形态）

```
trust(A_j) = clamp01( base(tier_j)                                   ← account × 请求局部
                    + clamp( Σ_{i∈E} adj_i , ±0.30 )                 ← entity 全局，Σ 之外夹一次
                    + min( 0.02, min(20, Σ_{i∈E} mc_i) · 0.001 ) )   ← entity 全局，Σ 之外夹一次
```

```python
# memory/trust_store.py
def trust_inputs(self, account_id) -> tuple[float, int]:
    """必须返回**未夹的原始和**。clamp 只允许出现在 resolve_trust 一处——
    否则将来第二个调用方会各夹一次，产生两套不同的有效分。
    未注册 ⇒ (0.0, 0)，**绝不返回 None**（None 是 resolve_trust 的弃权语义，
    不是 trust_inputs 的）。"""
```

常量已复核 `config/memory_settings.py:248-265`：admin/trusted/normal/none = 1.0 / 0.8 / 0.5 / 0.3，`ACTIVITY_WEIGHT=0.001`，`ACTIVITY_MAX_BONUS=0.02`，`CONFIRMATION=+0.04`，`CORRECTION=−0.08`，`ADJUSTMENT_LIMIT=0.30`，`ARBITRATION_MARGIN=0.15`，计数 cap = `ceil(0.02/0.001) = 20`（`permission.py:21-31`）。与现行三段式（`permission.py:247-279`）逐项对齐。

### 2.15.3.2 adjustment：加法后夹一次，**绝不逐 account 夹**

1. **N 不变性**：±0.30 的语义是「这个人的信赖度位移上限」，人只有一个上限。逐 account 夹 ⇒ 上限变 ±0.30·N —— **N=2 就是 margin 0.15 的 4 倍**，绑定即刷分。
2. **符号混合下两种夹法不等价**：A raw=+0.50、B raw=−0.40。Σ 后夹 = +0.10（正确读数）；逐个夹后求和 = +0.30 + (−0.30) = 0.00。
3. **保住 merge 性质**（主设计 §2.7：「合并前后的变化只来自求和范围变大」）。逐 account 夹让截断顺序进入结果。
4. 现行代码已把这条纪律写死在 `permission.py:337-343` 的注释里（逐次夹在上限附近非交换）——本条只是把它从「逐事件」推广到「逐 account」。

**明确否决平均**：`mean_i(adj_i)` 会让绑 N 个干净账号把一次 correction 稀释成 −0.08/N，把绑定变成**洗白**手段。**加法 + 单次夹是唯一同时防刷分与防洗白的形态。**

副作用（既有性质被 N 放大，非新缺陷）：三个账号各 raw −0.8 的人显示 −0.30，需要 60 次 +0.04 才爬回 0。要做康复通道只能另加**有界衰减**，绝不能改成写侧夹（破坏交换律）。

### 2.15.3.3 activity：两道上限都在 Σ 之外；外层 `min(0.02, …)` 是承重墙

`cap ≡ ceil(MAX_BONUS/WEIGHT)` ⇒ 数值上恒等冗余，正是这份冗余在挡多账号刷分：

| 写法 | sup(activity) | N=8 |
|---|---|---|
| `min(0.02, min(20, Σmc)·0.001)`（本规范） | 0.02 | 0.02 |
| `min(0.02, Σ min(20,mc_i)·0.001)` | 0.02 | 0.02 |
| `Σ min(20,mc_i)·0.001`（删外层） | **0.02·N** | **0.16 > MARGIN 0.15** |

第三行是真实可达的击穿，而删掉外层 `min` 在重构中极易发生（它看起来被 cap 蕴含）。三条防御缺一不可：(a) 两个 clamp 都在 Σ 之外；(b) 启动断言 `ceil(MAX_BONUS/WEIGHT)·WEIGHT >= MAX_BONUS`，改动任一常量即 fail loud；(c) 重分区 property 测试 I-T-1。

**必须显式处理的既有事实（此前两份文档都没写）**：`message_count` **今天在写侧就已经夹到 cap**（`permission.py:295-298`：`profile["message_count"] = min(cap, existing + count)`），与 `adjustment` 的无损写侧**不对称**。规范如下：

- **A-1** 保留每 account 写侧夹到 cap（存储有界，与今天字节形状一致）。因为读侧是 `min(cap, Σ)`，per-account 预夹只会**减小** Σ ⇒ 只会**减小** bonus，永不放大 ⇒ **fail-closed，可接受**。
- **A-2** 因此 I-T-1 的重分区不变性必须声明在**写侧夹之后**的分区上，即每个分量 ∈ `[0, cap]`。（宣称「含超 cap 分量」的分区在写路径上构造不出来，那样的测试是假的。）
- **A-3** §4.7 的 cap no-op 写放大优化必须把饱和判据上移到**实体和** `Σ_{i∈E} mc_i >= cap`。否则一个 3 账号各 7 条（和 21，已饱和）的实体每个 `mc_i < 20`，每次 flush 仍重写整份 JSON ⇒ R8/R9 的写放大对多账号实体原样回归。池在内存、`account_index` 可 O(1) 定位实体，仍在同一临界区，不引入新锁。
- **A-4** A-3 的副作用：被跳过的消息**连 event id 都不记**，unbind 出去的 account 若 `mc_A < cap` 会在重投时被重计。上界受 `MAX_BONUS = 0.02 ≪ margin 0.15` 严格约束，可接受，但**必须写进 unbind 端点 docstring**。

### 2.15.3.4 base：不做任何跨 account 聚合

- **max 否决**：等价于「最弱的一个平台接入抬高整个人的仲裁权」，与主设计 §2.5 封杀 `speaker_is_owner` 由 entity 推导同一条理由；且 wire 上 `speaker_is_owner=True` 硬校验 `tier=='admin'`，max-base 等于在分数轴上绕过这道校验。
- **min 否决**：接入一个新平台的低档账号会静默下调这个人在 QQ 的 admin 权重 ⇒ 制造「不要绑定」的反向激励，直接对抗 S1。
- **mean 否决**：显式 N 依赖，可被新账号稀释。
- **正面理由**：base 是「这个平台此刻授予他什么权限」，只有到达通道那一侧有证据；跨平台推导 base 就是伪造授权事实。

**★ `SPEAKER_TRUST_MAX_REPORTED_BASE = 0.8` 的 clamp 必须上移到本段最终分。** 主设计 `:438` 声明这道上界的目的是「上界 0.8 < admin 的 1.0，封死把 guard_level 映射成 owner 级仲裁权」，但若只夹 base，`0.8 + 0.30 + 0.02 = 1.0` = admin 同权——一个 B 站舰长只要被人工 bind 进某个 QQ admin 的实体就拿到满权，那条安全断言变成假命题。改为：

```python
return max(0.0, min(MAX_REPORTED_BASE, min(max(base, 0.0), MAX_REPORTED_BASE) + adj + act))
```

引入一个**有意的不对称**：`tier=='trusted'`(0.8) 能爬到 1.0，自报 `base=0.8` 不能——前者有平台权限模型背书，后者是无鉴权自报（主设计 §9.1 R7 已承认）。这个不对称必须写死在注释里。**代价极低：该常量今天不在树上（§2.15.0），这是对未落地设计的修订。**

### 2.15.3.5 cap 防御与「转移」——陷阱题的正面回答

**按 entity 聚合不会突破 cap**：两轴上确界 `sup = base + 0.30 + 0.02` 与 N 无关。三个账号各诚实赚 +0.20（raw +0.60）显示 +0.30，与一个账号赚 +0.30 完全相同——既不可刷、也不额外奖励。

**按 account 分别 cap 再求和会**：adjustment ±0.30·N（N=2 即 4×margin）、activity 0.02·N（N=8 即 0.16 > margin）。

**不能靠「bind 是人工的」兜底**：自动建边路径数为 0 保证的是**频率**，而攻击者模型是「操作者被说服点一次绑定」——adjustment 轴 N=2 就已击穿。**算术防御必须独立于 bind 的稀有性。**

**必须正面承认的一条：绑定会「转移」，而转移正是 S3 的目的，不是漏洞。** 上确界不变 ≠ 单次绑定的效果小。具体数字（诚实披露，不得省略）：

| 场景 | 绑定前 | 绑定后 | 一次点击的位移 |
|---|---|---|---|
| 全新账号（tier=none, adj=0, mc=0）绑进成熟实体（adj 饱和 +0.30, mc≥20） | 0.30 | **0.62** | **+0.32 = 2.1 × margin** |
| 无辜账号（tier=normal, 0.52）绑进 adj=−0.30 的实体 | 0.52 | **0.22** | **−0.30 = 2 × margin** |

三条边界让这个转移**不越界**且**不等于提权**：

1. **不跨过授权面**：`speaker_is_owner` 绝不由 entity 推导 ⇒ 转移的是「仲裁权重」，不是「owner 授权」。被绑进来的账号**不会**因此获得发 confirmation/correction 的资格。
2. **不越过 sup**：转移后的分数仍 ≤ 该实体自己的 sup，绑定不创造新的信誉额度。
3. **可审计、可撤销（账本层）**：`bound_at` / `bound_by` 落账本；dashboard 必须显示「该账号的 trust 来自 N 天前绑定的实体 ent_…」。unbind 在账本层是精确逆运算（§2.15.3.9）。

因此 §「既不可刷、也不额外奖励」这句话的正确表述是：**「按实体聚合的上确界与 N 无关；但绑定会把实体已有的信誉转移到新 account 上，这是操作者断言的直接后果，转移量最大 +0.32 / −0.30。」** 旧表述只对上界成立、对转移不成立，必须替换。

### 2.15.3.6 两个事件环：严格按 account 分区，读侧**永不并集**

- signal：`trust_event_id(kind, signal_key, target_speaker_id)`（`speaker_trust.py:518-520`），第三参是目标 **account_id**。
- activity：`"activity_" + sha256(f"qq:{sender_id}|{stable}")`（`session_memory_service.py:2005-2007`）。

两族 id 都已把 account 烘进哈希 ⇒ 针对 A 的 id 结构上不可能出现在 B 的环里 ⇒ 并集**零去重收益**，却会让 `_unbind_locked` 无法判定哪些 id 该带走，摧毁「merge = 不相交搬迁 / unbind 零损」。

**必须写进 `memory/trust_store.py` 模块 docstring 的一句话：entity 层对环的作用是「影响事件是否产生」，不是「事件记在谁名下」。** PR7 把 `facts.py:1365-1367` 的自证禁令实体化（`same_entity`），改变的是哪些事件被**生成**；生成之后的幂等与存储 100% 是 account 的事。

**一处必须保持不实体化的对称点**：`facts.py:1349-1351` 的重放环用 `stable_speaker_id(recorded['source_speaker_id']) == source_id` 判定（source = 主人自己 account 的字符串相等）。只实体化 1365-1367、**不动** 1349-1351 ⇒ 主人换 account 后「禁令更严（少发）+ 重放失配（少补）」，两个方向都是 fail-closed 欠计，可接受；但必须显式写下来，否则将来会有人「顺手对称化」，那会让重放环替另一个 account 补发事件。

### 2.15.3.7 闸门（legacy_barriers）：**account 局部**，写死

`barrier_pending(platform(account_id))` 只看请求那一侧的平台，而输入 `trust_inputs` 是 entity 全局。实体含 `qq:123`(cleared) 与 `bili:456`(pending) 时：QQ 请求正常解析但 bili 账本尚未导入 ⇒ **有界欠计**（fail-closed），闸门开后自愈；bili 请求返回 None（弃权）。**无双算路径**（pending 平台的写侧被跳过），两种口径都安全。

选 account 局部的理由：口径「实体内任一平台 pending 则整人弃权」会让接入一个新平台瞬间让这个人在所有已就绪平台上失去 trust。主设计 `:189` 的伪码字面就是 `barrier_pending(platform(account_id))`（本章不新增这条口径，只是把「闸门 account 局部 × `trust_inputs` entity 全局」这个**不对称**显式写下来——读者会合理地误以为两者同域）。

### 2.15.3.8 `None` 弃权语义：逐字不动（硬约束）

`resolve_trust` 的返回 `None` **恰好三个条件，顺序与主设计 `:186-189` 逐字一致**：

```
account_id is None                       → None
barrier_pending(platform(account_id))    → None
tier is None and base is None            → None
```

**禁止新增第四个条件。** 特别地：

- 「账本缺失 / account 未注册」**不是** None ——`trust_inputs` 返回 `(0.0, 0)`，聚合正常进行。
- 「实体为空 / Σ 为空」**不是** None —— 空 Σ = 0.0。
- 「平台无法解析」不构成独立条件：`platform_of` 的输入已由 `stable_speaker_id` 校形，不可解析的 `account_id` 在第一条就已是 `None`。

`None` ⇒ handler **不写 `segment["speaker_trust"]` 键** ⇒ `preferred_by_trust`（`speaker_trust.py:156-170`，任一侧非有限即返回 None）弃权。**必须有一条不变量逐条断言这三个条件，并断言「不存在第四条」**（I-T-6）。

### 2.15.3.9 merge / unbind 的精确语义

**账本层（精确、O(1)）**：`_unbind_locked` 把 account 子记录整条移出，带走的就是 `adj_A`、`mc_A` 与两个环，逐字节可查。这正是「账本按 account 分区」买到的东西——若账本按 entity 汇总，这个问题**无解**。

**效果层（结构上无唯一答案）**：`Δeffective = clamp(Σ_{i≠A} adj_i) − clamp(Σ_i adj_i)`。
- entity raw 和 −2.0、`adj_A = −0.5` ⇒ 前后都是 −0.30，**带走 0**；
- raw 和 −0.35、`adj_A = −0.5` ⇒ 解绑后 +0.15，**净变化 +0.45 > 0.30**（解绑同时释放了另一侧的饱和）。

⇒ 「带走多少」在有 cap 的聚合下**没有唯一答案**。**端点响应与运维文档必须同时回传两个数**（`ledger_delta` 与 `effective_delta_by_account`），否则操作者会拿账本数解释分数，永远对不上。merge 对称：ledger 加法、effect 次可加（可能为 0），收益不可预告，只能事后回读。

**真正不可逆的不是账本，是反事实。** 自证禁令实体化后 `facts.py:1365-1367` 的 `continue` 发生在事件**生成**之前 ⇒ 没有事件体、没有挂到 fact 行 ⇒ `areconcile_from_facts` 的判据（「fact 行上有、池里没有」）**结构上看不见它**。误绑一天，这一天里主人对该「疑似自己」账号的所有纠错（每条 −0.08）永久消失。反方向同理：merge 之前已攒的自签信号不退还——**建议不做**回溯撤销（它在 `ascoped_forget` 之后不完备，且要动 provenance 判定，失败方向落 mixed）。

⇒ **bind/unbind 对账本是群逆运算，对「这段时间本该发生什么」不是。** trust 轴运维口径：**宁可早绑，不要晚绑**。（注意它与 subject 轴的「宁可晚绑」方向相反，§2.15.2.13。）

### 2.15.3.10 同实体不自我仲裁（三处 D 类点，各一行）

canonical 路由把同实体两条行放进同一 subject、同一 `_fact_dedup_domain`（`fact_dedup.py:157-166`）、同一 `_in_signal_scope`（`facts.py:1304-1305`），把「自己跟自己仲裁」从理论变成常态。而 `preferred_by_trust` 的三个调用点（全仓恰好三个，已复核）守门条件都以 **account 级不等式**开闸，account 不等 ≠ 人不等：

| 位置 | 今天的守门 | 改法 |
|---|---|---|
| `memory/fact_dedup.py:1310`（守门在 `:1294-1312`） | `cand_speaker_id != exist_speaker_id` | `and same_provenance_source(...) is not True` |
| `memory/scoped_refine.py:212-213`（`:198-205` 的注释明写这正是它要防的事） | `len(speaker_ids) != len(set(speaker_ids))` 早退 | 早退提升到 entity 维度 |
| `memory/persona/corrections.py:746`（守门在 `:737`） | `stable_old_speaker_id != stable_new_speaker_id` | 同 fact_dedup |

三处的 `None` / 原样返回分支都已存在且是 no-op，改动量各一行，方向 fail-closed，**不落 mixed**（弃权而非合并）。

**具体反例（不做这条会发生什么）**：某人 `qq:123` 是 admin、`bili:456` 是 none，adj=0。QQ 行盖戳 1.0、B 站行盖戳 0.3，差 0.70 ≫ margin 0.15。走 `fact_dedup`：`preferred_by_trust(0.3, 1.0)` 返回 `'new'`，模型给的 `merge` 被改写成 `replace` ⇒ **他自己的 QQ 旧发言硬覆盖他自己的 B 站新发言**。

**优先级说明**：这条在本设计里**不是可选加固，是写侧路由 PR 的前置条件**（见 §2.15.6 的 PR 序）。

### 2.15.3.11 trust 侧不变量

- **I-T-1（重分区不变性，最强）** 固定 (Σadj, Σmc)，任意重新分区成 k=1..8 个 account 记录（含负 adj 分量；mc 分量 ∈ [0, cap]，见 A-2），`resolve_trust` 返回值**逐位相同**。一条测试同时钉死两个 clamp 的位置、禁止将来任何逐 account 夹、覆盖 merge/unbind 前后一致性。
- **I-T-2（N 无关上确界）** 任意 N 与任意账本，`resolve_trust(tier=t) − BY_LEVEL[t] ∈ [−0.30, +0.32]`；且 base 通道 `resolve_trust(base=b) ≤ MAX_REPORTED_BASE`（验证 clamp 已上移）。
- **I-T-3（配置不变量）** 启动断言 `ceil(MAX_BONUS/WEIGHT) * WEIGHT >= MAX_BONUS`，任一常量漂移即 fail loud。
- **I-T-4（base 不聚合）** = I-S1-2。
- **I-T-5（转移量已知且有界）** 断言「全新 none 账号绑进饱和实体」的位移**恰好** +0.32，「normal 账号绑进 adj=−0.30 实体」的位移**恰好** −0.30；这两个数字被测试锁死，将来若有人改动使其变大，测试必红。
- **I-T-6（None 弃权零位移）** 逐条断言三个 None 条件；断言「账本缺失」与「Σ 为空」**不**返回 None；断言 None ⇒ handler 不写 `speaker_trust` 键。
- **I-T-7（unbind 双数）** unbind 响应同时含 `ledger_delta == adj_A` 与 `effective_delta`；构造饱和实体使 `effective_delta == 0` 而 `ledger_delta != 0`，断言两者都被回传且**不相等**（防止将来有人「修」成一致）。
- **I-T-8（同实体不自我仲裁）** 构造同一 entity 两个 account 的 `deterministic_relation == 'correction'` 一对行（trust 分差 ≥ margin），断言三处**全部弃权**（action 与 sources 原样、preference is None），且都不写 `speaker_provenance_mixed`。

---

## 2.15.4 问题 B：R11（开放平台 `author.id` 的作用域）

### 2.15.4.1 三种状态下设计如何表现

| 状态 | 设计行为 | S1 | S2 | S3 |
|---|---|---|---|---|
| **未知（今天）** | `platform_identity_scope.qq.actor_scope = "unknown"`。**代码永不推断。** 折叠/展开在跨群维度上**恒等退化**（因为跨群本来就不展开，S2 禁止），在同群维度上正常工作。 | 在 account 层未知 | 真（同群内 openid 唯一 ⇒ 每群一个 participant） | 真（按已注册 account 求和；若同一人跨群是多个 account，则和跨群累加——正好是 S1.b 想要的） |
| **未兑现**（`author.id` 全局 openid） | **一字不改**。跨群自动就是同一个 account_id、同一个 entity。 | **真**（account 层直接成立） | 真 | 真 |
| **已兑现**（`author.id` 是 `member_openid`，app×群×人） | 恒等退化到「一个 (人, 群) 一个 account」；操作者逐个 bind 后逐个恢复。dashboard 必须显示降级提示。 | **在无操作者断言时不可达**（见 §2.15.4.3） | **真**（且映射更干净，见下） | 在已断言范围内真，范围外**三条轴同时归零**（见 §2.15.4.4） |

**一个不直观但重要的结论：R11 兑现对问题 A 是「有利」的。** 若 `author.id` 是 member_openid，那么**在一个群内一个人只有一个 id** ⇒ R11 本身**不会**在同一个群里制造两个 participant。问题 A 在实践中来自**通道切换的时间维度**（napcat uin → 开放平台 openid），而 `qq_connection_mode` 是全局单值二选一 ⇒ 单个部署任一时刻只在一个体制内。而且 member_openid 的**群作用域恰好与 group_participant subject 的群作用域对齐**：每一次 bind 断言精确对应**恰好一个** participant 的统一，映射一对一，不需要额外推断。**R11 让 S1 变贵，但让 S2 的映射变干净。**

### 2.15.4.2 取证步骤（精确到操作、文件、字段）

R11 **无法离线判定**：零 fixture、零 vendored SDK、零文档样例、零 git 删除痕迹、本机 `%LOCALAPPDATA%\N.E.K.O\plugins\` 为空目录、两份远程副本 `qq:` 零命中。`GROUP_AT_MESSAGE_CREATE` / `C2C_MESSAGE_CREATE` 全仓只命中生产方 `qq_open_plat.py` 与设计文档自身；`_convert_event` 零测试覆盖。唯一沾边的代码假设是 `qq_open_plat.py:610` 的 `<@!(\d+)>` 纯数字正则——它是从 napcat 转换器抄写的、零测试覆盖，不构成证据。

> **修订文档 §2.14.2 宣称的「backlog 零成本取证」对群路径失效，必须改为读日志。** 原因：`qq_open_plat.py:605` 只读 `data.get("group_id")`，而 `group_openid` **全仓零命中**；若平台按 v2 语义下发 `group_openid`，`group_id` 恒为空串 ⇒ `backlog_service.py:88-90` 的 `if not group_id: return` 让群消息**根本不落 `backlog_state.json`**。

**A. 插桩已经在仓库里了（不必再改代码）**

`qq_open_plat.py` 顶部「R11 身份作用域取证」一节即本小节所述的插桩，落在
`_receive_loop` 里 `event_type = payload.get("t", "")` 之后、`_convert_event`
之前。默认**关**，开关是 `qq_open_identity_probe_enabled`，UI 在开放平台的
**「信任用户」页**（`open_platform.html` 的 `page-config-accounts`，紧跟
`accounts_hint` 那句「ID 为加密 openid…可在日志中查看」之后——开关就是那句话的
下一句）。开关按事件现读，一旦生效不必重连；但**打开**要等写盘成功才对运行时
可见（与记忆开关同族的采集授权，见 `settings_service.py` 的 `deferred_opt_ins`），
关掉则立刻生效。

> **只打取证需要的那四项，不要 `json.dumps(payload["d"])`。** §2.15.4.2 的判定
> 只需要 ①`author.id` ②`author` 的兄弟键**名** ③群 id 的键**名** ④C2C 的
> `author.id` —— 四项里没有一项需要消息正文。而这条日志落的是**持久**文件
> （`我的文档/N.E.K.O/logs/`，重启留存，正是取证要它持久的原因），整份 `d`
> 会把群聊原文、附件 URL、@ 列表一起写进去。取证结束后开关要关掉，但
> 已经落盘的日志不会跟着回滚。

落地实现比这段原始设计多两处：**兄弟键的值也打**（只打键名回答不了 ② 的后半
「哪一个兄弟键跨群相等」），以及**标识符字段按名字形状挑**（`id` / `*_id` /
`*openid*`）而不是按枚举挑——取证要找的正是没预料到的那个键，枚举会把它挡在
日志外面。非标识符字段一律只出键名、不出值。

- **两个出口都要写，缺一不可**（`_write_identity_probe`）。`self.logger` 是文件 logger（`__init__.py` `enable_file_logging`），落 `我的文档/N.E.K.O/logs/N.E.K.O_Plugin_qq_auto_reply_*.log`（`plugin/core/plugin_logger.py:15`），**重启留存**——这份才是能整个发给开发者的东西；光有它却**不够**：`_emit_log` 写的那个 500 条内存环才是 UI「运行日志」页读的池子（`get_recent_logs` 只在内存环为空时才回退读文件，而环从启动那刻起恒非空），少了它用户勾完开关在界面上什么也看不到——而隔壁 `accounts_hint` 刚说过「可在日志中查看」。反过来只写 `_emit_log` 也不行：内存环重启即失，取证要的恰恰是重启之后还能翻出来。
- **必须插在 `_receive_loop` 而不是 `_convert_event` 内**：绕开 `group_id` 键名不确定性，也早于 `backlog_service.py:91-93` 的信任群白名单闸。
- 单次连接封顶 200 条（`_IDENTITY_PROBE_MAX_LINES`），封顶后补一条提示、不再记录。计数器挂在 `QQOpenPlatformConnection` 实例上，而 `qq_client` 只有在**切换连接模式**时才被置 None 重建（`runtime_ops_service.py:44-48`）——侧栏的「停止 → 启动」复用同一个对象，计数器纹丝不动，**只有重启应用才重新计数**。这是「开关忘了关」的兜底，取证本身只需要三条。

**B. 维护者操作（四步，不可再省）**

1. `qq_connection_mode` 切 `open_platform`，填真实 appId / secret；
2. 在**「信任用户」页**勾上「记录每条消息里的 ID」并保存（保存成功才会生效）；
3. **同一个真实 QQ 号**在群 X @bot 一次、在群 Y @bot 一次；
4. 同一账号私聊 bot 一条。取证做完把开关关掉。

**C. 看哪个文件的哪个字段**

文件：`我的文档/N.E.K.O/logs/N.E.K.O_Plugin_qq_auto_reply_*.log`，筛 `[R11]` 行，比对四项：

| # | 字段 | 看什么 |
|---|---|---|
| ① | `d.author.id` | 群 X 与群 Y 的这两个值是否**逐字相等** |
| ② | `d.author` 的兄弟键 | 是否存在 `member_openid` / `user_openid` / `union_openid`；若存在，**哪一个跨群相等** |
| ③ | 群 id 的键名 | 挂在 `group_openid` 还是 `group_id` |
| ④ | C2C 那条的 `d.author.id` | 是否等于两条群消息的 id |

**D. 判定**

- **① 相等** ⇒ R11 解除，S1 在 account 层成立，本体无需改动。把 `actor_scope` 人工写成 `"global"`。
- **① 不等、② 中某兄弟键跨群相等** ⇒ **提取器缺陷而非本体缺陷**，改 `qq_open_plat.py:581` 的取值即可。注意这会改变该通道 `speaker_id` 的字节——但取证已确认本机与该通道**均无存量语料**（本机记忆目录 `grep -c "qq:"` 为 0），代价只落在已发行部署上，需单独确认。
- **①② 皆不成立** ⇒ R11 真兑现。把 `actor_scope` 人工写成 `"per_conversation"`，按 §2.15.4.3 执行。
- **④ 不等** ⇒ §2.15.4.4 的 admin bootstrap 缺陷**已兑现**，且应**先于** R11 修。

**E. 完全不改代码的弱化回退**：群 X 发一条 @bot、截图插件「运行日志」页（`message_dispatcher.py:305` 的 `收到消息: type=... from=<author.id> ...`，该行早于所有群闸），群 Y 再发再截，比对两个 `from=`。局限：不打印 group_id（只能靠发送顺序归因）、缓冲仅 500 条不落盘、对 ② ③ 零信息。

### 2.15.4.3 若 R11 兑现：S1 在无断言条件下**不可达**，及最接近的可达点

> **明确结论：若 `author.id` 是 `member_openid` 语义，则在「无操作者断言」的条件下，S1 不可实现。这不是工程取舍，是信息缺失。** 建边需要「这两个 id 是同一个人」这条信息，而它恰恰拿不到。任何声称能自动补上的方案，必然是被硬约束否决的那类启发式（昵称匹配 / 群共现 / 时序相邻 / 编辑距离），一律不设计。

**最接近的可达点，三级降级：**

**第 1 级 —— 操作者人工断言（对被断言的实体精确成立）。**
把 dashboard 的「替换 ID（保留信赖度）」扩成「合并到已有实体」，走 `POST /internal/identity/accounts/bind`。规模 O(操作者关心的人数 × 群数)。**主人是最重要的那一个，而且他必然会去做**——切通道后 `trusted_users` 那一行必须手填新 id（`permission.py:68-70` 只 strip、按裸 actor 索引），这次编辑本身就是一个显式人身断言。
排序规则**必须写死**：候选旧账号列表**只能按账本权重排序**（`|adjustment|` + `message_count`），**绝不能按昵称相似度排序、绝不预选**。把相似度放进 UI 排序 = 把被否决的启发式塞给用户当默认答案。

**第 2 级 —— 挑战-应答**（修订 §2.9.5）：旧通道/旧群发一次性码、新通道/新群回码；码单次消费、不可猜、短 TTL、原子消费。代价必须说清：`qq_auto_reply` 全仓没有任何聊天指令解析，这是**新增能力**；而且它**仍然需要人配合一次**，同样不叫自动。O(群 × 人) 的操作量意味着它只对少数几个人现实。

**第 3 级 —— 其余人：一个 (人, 群) 一个 entity，每个 entity 一个 account。** 本设计在这个形态下**完全退化成恒等**：`fold_participants` 返回单元素 group、`canonical_subject` 返回原 subject、渲染槽位与预算逐位等于今天。**这是设计的正确性质，不是妥协**——展开层的输入是「实体关系」这个抽象，关系为空时它必须是恒等（I-P-1 就是这条的可执行形式）。

**退化必须可见，不得假装成功。** `speaker_trust.json` 增一个纯观测容器（并入 `_copy_on_write` 清单）：

```jsonc
"platform_identity_scope": {
  "qq": {"channel": "open", "actor_scope": "unknown",       // per_conversation | global | unknown
         "conversation_scope": "unknown", "asserted_at": null, "asserted_by": null}
}
```

默认 `"unknown"`，**只能由取证结果人工写入，代码永不推断**。`GET /internal/trust/profile` 暴露它；`actor_scope == "per_conversation"` 时 dashboard 显式提示「该通道的信赖度不跨群累计；主人档位只在配置过的那个群生效」。

**群侧是可以救的（本章相对两份文档的净增量）。** 一个关键非对称：**`group_openid` 是「每群一个」，不是「每群每人一个」**。所以 conversation 本体的人工绑定规模是 **O(群数)**（十几个），现实可做；而 R11 下 `member_openid` 的绑定规模是 **O(群 × 人)**，不可做。⇒ **S2 的群侧可以真正实现；人侧在 R11 兑现时只能到第 1/2 级。** 这个非对称必须写进文档，否则读者会以为两侧同等困难。

### 2.15.4.4 两件优先级高于 R11、必须先做的事

**(a) 群字段键名比 R11 更早爆。** `qq_open_plat.py:605` 只读 `data.get("group_id")`，`group_openid` 全仓零命中；仓库自己已经知道该通道群 id 是 openid（`display_name_service.py:19` 逐字写着）。若平台下发 `group_openid`：`group_id` 恒空 ⇒ `backlog_service.py:88-90` **静默丢掉所有群消息**（连 backlog 都不落）⇒ 群 subject 退化成 `qq::` 被 `scopes.py:88-96` 直接拒 ⇒ 发送路径 POST 到 `/v2/groups//messages`。

**(b) admin bootstrap 缺陷今天就可能已兑现。** `message_dispatcher.py:34-54` 的 `_maybe_reserve_open_platform_admin` 在**第一条私聊**（C2C）上把 `sender_id` 写成 admin（`:415-417`），而群路径用群事件的 `author.id` 鉴权（`:196-204`）。若二者作用域不同，**通过私聊授权的主人在所有群里都不被识别为主人**——这是用户可见的**现存**缺陷，不是未来风险。

> **已落地的是「让它可见」，不是「修好它」。** `message_dispatcher.py` 的
> `_note_open_platform_identity_scope` 是纯观测告警：open 通道下、名册里有
> admin、而某个群里连续出现三个互不相同的说话人却无一匹配名册时，打一条
> `[R11]` 诊断（文件日志 + 插件运行日志页），并从此对该群闭嘴；该群但凡有一
> 个人匹配上，就永久闭嘴。它不改任何权限判定——若 id 本来同作用域，它是彻底的
> no-op。真正的修法要等 §2.15.4.2 的取证数据回来。
>
> **不要**把 `_maybe_reserve_open_platform_admin` 里那句全局
> `if permission_mgr.list_users(): return` 改成「按当前通道过滤后判空」。那不
> 是疏漏，是让通道切换 fail-closed 的门：按通道过滤在刚切到 open_platform 时
> 恒为空，等于让切换后第一个私聊 bot 的陌生人自动拿到 admin —— base 1.0 +
> `speaker_is_owner` + 主人记忆读取权 + 对全体说话人账本签发
> confirmation/correction 的权力。

**R11 兑现的后果比修订文档 §2.14.1 描述的严重一个量级。** 文档说是「trust 按群碎成 N 份、攒不到 cap」，实际是**三条轴同时归零**：`trusted_users` 按裸 actor 索引 ⇒ 主人在其他群解析成 `none`(base 0.3) ⇒ `speaker_is_owner ≡ tier=='admin'` 为 False ⇒ `aevaluate_speaker_trust_events` 在那些群里**根本不产生任何 confirmation/correction** ⇒ adjustment 的**来源**断供。碎片化只是最轻的那一层。

### 2.15.4.5 设计不被 R11 绑架

本设计的输入是「实体关系」这个抽象；R11 只决定这个关系在开放平台上有多稀疏。关系为空 ⇒ 恒等退化；关系稠密 ⇒ 全额兑现。三条硬结论：

1. **R11 不阻塞本设计**（PR 可以照排）。
2. **R11 阻塞 `qq_connection_mode = open_platform` 的上线**。
3. **(a)(b) 两件事优先级高于 R11。**

---

## 2.15.5 为什么必须有 account 层（写给不看代码的人）

维护者的疑问是合理的：既然人是 entity，为什么不干脆只有 entity 一层？

**一句话：entity 是「人」，account 是「这个人拿在手里的一张证件」。系统永远只能看见证件，看不见人。**

展开成四条，每条都是「只有 entity 层会立刻坏掉」的具体理由：

**1. 每一条进来的消息都带着一张证件，从来不带一个人。**
QQ 发来的是一串 id、B 站发来的另一串 id。没有任何一条消息会说「我是张三」。如果系统里只有 entity 一层，那么每见到一串新 id，系统就必须在两件事里选一件：要么把它当成一个新的人（那这个「新的人」其实就是 account，只是换了个名字），要么**猜**它是已知的哪个人——而猜就是昵称匹配、群共现那一类，评审已经认定是致命的。**account 层不是多出来的一层，它是「系统实际能观测到的东西」本身。**

**2. 权限是发给证件的，不是发给人的。**
你在 QQ 群里是管理员，不等于你在 B 站是管理员。护照上的美国签证不会因为你还有另一本护照就自动生效。这条不是技术限制，是产品意义上的正确性：如果一个人在任意一个平台被授予高权限，就自动在所有平台高权限，那么**接入的平台越多，攻击面越大**——最弱的那个平台变成整个系统的入口。所以「谁给你的权限」必须记在证件上（account），只有「你自己挣来的口碑」才记在人身上（entity）。

**3. 断言会错，而错了要能撤销。**
「这两个账号是同一个人」是**操作者做出的判断**，判断可能错。撤销的前提是：撤销时能精确说出「这个账号带走了什么」。如果账本按人记，一个人名下只有一个总数，撤销时**根本不知道该退多少**——这个问题没有答案，不是难算。账本按证件分区记，撤销就是把这个证件的那一栏整条搬走，逐字节可查。**可撤销性只有在 account 层存在时才存在。**

**4. 记忆的隔离单元是「谁在哪儿说话」，而说话的总是一张证件。**
一条记忆是从某个渠道的某次发言里抽出来的。把它记在人头上、丢掉是从哪张证件来的，就再也回答不了「这句话到底是他在 QQ 群说的还是在私聊说的」，也就再也不能按渠道撤回、按群清档。

**类比收尾**：entity 是人，account 是证件。你只有一个身份，但可以有多本证件。系统给证件盖章（权限），给人记口碑（信誉）。发现两本证件属于同一个人时，我们不销毁证件、也不把两本证件的章合并——我们**写一张便条把它们连起来**，便条可以撕掉，证件本身一个字不改。本设计的全部内容，就是「怎么写这张便条、便条生效时系统怎么读、便条撕掉后一切怎么恢复」。

---

## 2.15.6 逐文件增量与 PR 划分

### PR 序（依赖是硬的）

| PR | 内容 | 前置 | 开关 |
|---|---|---|---|
| **PR-E0** | R11 + (a)(b) 取证插桩与结论回填 `platform_identity_scope` | — | 临时插桩，取证后回滚 |
| **PR-E1** | 值层 `ParticipantGroup` + 解析层 `subject_identity.py` + **读侧**折叠/展开（`scoped_context` / `query_memory` / `scoped_mentions`）+ locale 读侧同键 + 渲染一人一槽 + 归档合并 | PR1（identity/trust_store 落地） | `IDENTITY_READ_EXPANSION_ENABLED`，默认 off |
| **PR-E2** | provenance 三态（`same_provenance_source` + `speaker_entity_id` 字段）+ 三处 D 类点同实体弃权 | PR-E1 | 随 PR-E3 的开关生效；`None` ⇒ 弃权分支**无条件生效** |
| **PR-E3** | **写侧** canonical 路由 + 封定规则 + locale 写侧顺序 | **PR-E2 与 PR-E4 均已合入** | `IDENTITY_WRITE_ROUTING_ENABLED`，默认 off，per-platform |
| **PR-E4** | `scoped_forget` 改单事务多 subject 扇出（含持久水位扇出） | PR-E1 | 随 PR-E1 开关 |
| **PR-E5** | conversation 本体（`derive_conversation_id` / bind 端点 / UI）+ 群侧 canonical | PR-E1 | 独立 |
| **PR-E6** | trust 聚合规范落地（`trust_inputs` 未夹 / clamp 上移 / no-op 实体化 / unbind 双数） | **PR4（trust 池上移服务端）** | 随 PR4 |

**PR-E2 必须先于 PR-E3**：没有它，写侧路由会让一个人的旧发言确定性硬覆盖他自己的新发言，并重开 mixed。
**PR-E4 必须先于 PR-E3**：没有它，被路由到 canonical 堆的行在原 account 被 forget 时删不掉——**隐私回归，不可接受**。

### 逐文件增量

| 文件 | 改动 | PR |
|---|---|---|
| **【新】** `memory/subject_identity.py` | `fold_participants` / `canonical_subject` / `participant_key` / `seal_canonical_locked`。subject 层**唯一** import trust_store 的模块 | E1/E3 |
| `memory/scopes.py` | 新增 frozen+slots 纯值类型 `ParticipantGroup` 与 `flatten_groups`。**不 import trust_store、零 IO**。`entry_matches_subject`(:221-229) / `filter_entries_for_subjects`(:250-277) / `subject_from_entry`(:182-203) / `normalize_subjects`(:232-247) **一字不改** | E1 |
| `app/memory_server/routes.py` | 读侧折叠+展开：`:1894` / `:2053` / `:2111`；`:1949` forget 改单事务多 subject；写侧路由：`:1142` / `:1248` / `:1504`，**必须在 locale 预约 `:1152`/`:1258`/`:1567` 之前**；`_resolve_scoped_memory_language`(:123-150) 内部先 canonical 再查表、只喂 primary。wire 侧 `1..8` 校验（`:1892`/`:2048`/`:2106`）**一字不动** | E1/E3/E4 |
| `memory/persona/rendering.py` | `_subject_render_slots`(:806-834) 返回 group 槽；`_subject_bucket_marker`(:837-844) / `_bucket_entries_by_subject`(:846-868) 改 marker→group 查表；`:663-699` 段落循环按 group 合并标题（display_name 规则见 §2.15.2.12）；`_persona_view_for_subjects`(:88-135) 继续吃扁平 allowed_keys，**不动** | E1 |
| `memory/speaker_trust.py` | 新增 `same_provenance_source(a,b) -> bool \| None`（三态）；`provenance_of_entries`(:556-588) 的两条 mixed 早退改用它，**同实体跨 account ⇒ 保留原样、不折叠、不取 min**；模块 docstring 补「零 IO 靠注入保持」 | E2 |
| `memory/facts.py` | `_reconcile_existing_provenance`(:3453-3499) 按 §2.15.2.10 三态改写（`None` ⇒ 弃权，**绝不写 mixed**）；写路径 provenance 增列 `speaker_entity_id`（`speaker_id` 字节不动）；`:1365-1367` 自证禁令实体化；`:1349-1351` 重放环**明确保持 account 级字符串相等，加注释锁死** | E2 |
| `memory/fact_dedup.py` | `_fold_survivor_provenance`(:1325-1350) 的 `len(attributed_ids) > 1` 改用 `same_provenance_source`；`:1294-1312` 守门加同实体弃权 | E2 |
| `memory/scoped_refine.py` | `:203-205` 的重复 speaker 早退提升到 entity 维度 | E2 |
| `memory/persona/corrections.py` | `:737` 守门加同实体弃权 | E2 |
| `memory/subject_archive.py` | `collect_subject_last_writes`(:92-127) 之后新增 `coalesce_participant_last_writes(last_writes, resolver)`；本模块仍不 import trust_store（resolver 由 sweep 注入） | E1 |
| `memory/trust_store.py` | 新增 `canonical_accounts` / `canonical_conversations` / `superseded_canonicals` / `platform_identity_scope`（并入 `_copy_on_write` 清单）；R-CANON-1/2/3 接进 `_bind_locked` / `_unbind_locked` / `_merge_entities_locked` / `_forget_entity_locked`；`IDENTITY_MAX_ACCOUNTS_PER_ENTITY_PER_PLATFORM` 校验（409）；`trust_inputs` 返回**未夹**原始和；`resolve_trust` 把 `MAX_REPORTED_BASE` 的 clamp 上移到本段最终分；模块 docstring 写死「entity 影响事件是否产生，不影响事件记在谁名下」与「闸门是 account 局部」 | E1/E6 |
| `memory/identity.py` | 新增 `derive_conversation_id(conversation_account_id, generation)`（前缀 `conv_`，与 `ent_` 同法保证与必含冒号的 account_id 命名空间不相交）；docstring 补「派生输入 = 身份的唯一真相」 | E5 |
| `app/memory_server/locale_state.py` | **无代码改动**；`_subject_locale_key`(:187-197) 的键从此以 canonical subject 为准，forget 扇出时逐 marker 调 `forget_subject_prompt_locale` | E3/E4 |
| `config/memory_settings.py` | 新增 `IDENTITY_MAX_ACCOUNTS_PER_ENTITY_PER_PLATFORM=8` / 两个 feature flag；新增 activity 两道上限的启动一致性断言。**`GROUP_RECALL_MAX_MEMBER_SUBJECTS`(:234) 与 `SCOPED_RENDER_*`(:121-141) 一律不动**；`:256-258` 那段「trust 池由 QQ 插件持有」的注释随 PR4 修正 | E1/E6 |
| `static/script.js`（`:293`、`:1120-1129` 附近） | 「替换 ID（保留信赖度）」候选列表**只按账本权重排序、绝不预选**；显示 `bound_at` / `bound_by`；显示两条方向相反的运维口径 | E5 |
| `plugin/plugins/qq_auto_reply/**` | **零改动。** `memory_bridge.py:49-74` 三个 builder、9 个调用点、`session_memory_service.py:1420` 的 `f"qq:{sender_id}"` 全部原样。**这是本路线最重要的性质（287 处 `group_participant` 测试引用不用改）** | — |
| `plugin/plugins/qq_auto_reply/qq_open_plat.py` | **仅取证期**：`_receive_loop:181` 后一行 file logger，取证后回滚 | E0 |
| **【新】** `tests/unit/test_subject_identity.py` | I-P-1..4、I-S2-1..2、I-C-1..2 | E1/E3 |
| **【新】** `tests/unit/test_participant_fanout_render.py` | I-S2-3..5、I-L-1 | E1 |
| 扩充 `tests/unit/test_trust_store.py` / `test_fact_dedup.py` / `test_group_memory_scopes.py` | I-T-1..8、I-P-5..6、I-F-1..2 | E2/E4/E6 |
| `docs/design/speaker-trust-platform-neutral.md` / `…-entity-ontology-revision.md` | 收编本章为 §2.15；§2.7 表格补一行「canonical 归属 = 封定、per (entity, platform)」；§2.14 补 R13（写侧路由的不可逆面）/ R14（`platform_identity_scope` 只能人工写入）/ R15（群字段键名先于 R11） | E1 |

### 完整不变量清单（除已列出的 I-S1-*、I-S2-*、I-T-*）

- **I-P-1（恒等退化，最重要的一条）** 池未加载 / account 未注册 / 非默认 scope / 实体只有一个 account 时，`flatten_groups(fold_participants(S))` 与 `S` 逐元素逐字节相等，且 `canonical_subject(s) == s`（含 scope）。对三条读路径与三个写入口各跑一次 golden wire 断言。
- **I-P-2（scope 正确性）** `canonical_subject(s).scope == f"{kind}:{subject_id}"`；grep 断言 `subject_identity.py` 不出现 `dataclasses.replace`。
- **I-P-3（互锁）** mock 池未加载，让同一实体的两个 account 写同一句话；断言产生**两条**行、**两个**不同 subject、**零 mixed**。
- **I-P-4（畸形组合不 500）** 构造 actor 含冒号的 account，断言展开丢弃不可构造 marker、warning 计数 +1、请求正常返回 200。
- **I-P-5（零 mixed）** 同实体两个 account 在同一群先后写入**同一句话**（都走 canonical 路由）：断言落库行不含 `speaker_provenance_mixed`、`speaker_id` 保持首写者字节、`speaker_trust` **未被 min 下调**。exact-hash 与语义去重两条路径各断言一次。
- **I-P-6（mixed 单调性回归）** 不同 entity 的两个 account 落进同一 subject 时，四条路径**仍然必须**打 mixed。实体化只放宽同实体，绝不放宽跨实体。
- **I-C-1（canonical 的 merge 稳定性）** 对随机 k≤8 次 bind/merge 序列做随机 200 次洗牌，断言最终 `canonical_accounts[platform]` 只由最终实体集合决定，与顺序无关。
- **I-C-2（解封仅由离开触发）** unbind(canonical) / forget(entity) 后该项为空；unbind(非 canonical) 后逐字节不变。
- **I-L-1（locale 读写同键）** 经 canonical 路由写入并预约 locale 后，从**非 canonical** account 的 subject 发起读请求，断言解析到的语言等于写侧预约的语言（不回落角色级）。
- **I-F-1（forget 扇出且单事务）** forget 该实体在群 G 的任一 subject 后，**全部** marker 的 facts / archive / reflections / persona section / dedup 队列 / locale / **持久水位** 皆已处理；并断言整个过程 `_reload_lock` 只 acquire 一次、所有墓碑在第一次擦除前全部打开。
- **I-F-2（归档实体化）** canonical 堆 last_write = 昨天、非 canonical 堆 = 200 天前 ⇒ `find_stale_subjects` 对**两者**都返回非 stale。

---

## 2.15.7 遗留问题与需要拍板的点

**必须拍板（阻塞 PR-E3）**

1. **`scoped_forget` 扇出到整个参与者，是否是要的语义？** 我按 S2（participant = entity × conversation）认为是，但它把一次「退群清档」放大成多个 account 堆的不可逆删除。备选：只删请求的那一个 subject——但那样「一个 participant」在删除轴上就不成立。
2. **是否接受新增持久 provenance 字段 `speaker_entity_id`？** 它是本设计唯一新增的持久字段（`speaker_id` 字节不变），merge 之后会陈旧、比较时靠池二次解析兜底。
3. **是否接受 provenance 判定新增第三态「unknown ⇒ 弃权」？** 这实质上推翻主设计 §1.2(1) 在这一处的适用——但那条决定的前提是「放宽 ⇒ mixed」，而这里方向相反（「不放宽 ⇒ mixed」）。**不接受则写侧路由不成立**，只能停在读侧展开。
4. **是否接受写侧路由的不可逆面？** 绑定期间落的行留在 canonical 堆，unbind 救不回；只提供 `stranded_rows` 计数 + `scoped_forget` 核选项，**不提供搬回端点**（搬回必须重算盐化 hash，正是要躲的陷阱）。
5. **是否接受 canonical 用「懒封定」而非纯派生？** 它依赖事件顺序、不是纯状态函数；换来的是唯一的 merge 稳定性（派生式在 merge 时必翻转，每翻一次制造第三堆）。

**必须拍板（trust 轴，阻塞 PR-E6）**

6. **`SPEAKER_TRUST_MAX_REPORTED_BASE` 的 clamp 上移到本段最终分**，引入「`tier='trusted'`(0.8) 能爬到 1.0、自报 `base=0.8` 不能」的有意不对称。不上移则该常量声明的安全断言在算术上为假。
7. **接受绑定的「转移」量**：+0.32 / −0.30，均 ≥ 2×margin（§2.15.3.5）。这是 S3 的直接后果，不是漏洞，但落点是**不可回滚**的 fact 行 stamp，必须显式接受。
8. **接受 S3 的部分实现表述**：`base` 不聚合 ⇒ 同一个人跨通道最大差 0.68，`trust_band` 一个 `'high'` 一个 `'low'`。文档必须写「trust 的**可挣得部分**绑 entity」，**不得**写「trust 绑 entity，完全实现」。
9. **闸门口径保持 account 局部**（写进 `resolve_trust` 注释）。

**需确认**

10. **`IDENTITY_MAX_ACCOUNTS_PER_ENTITY_PER_PLATFORM = 8`，超限 bind 返回 409。** 用「bind 时拒绝」换掉「读时截断」，是本设计消除 S2 反例的关键。8 够不够？
11. **`platform_identity_scope.actor_scope` 只能人工写入、代码永不推断** ⇒ 维护者填写前 dashboard 一直显示 `unknown` 而不是任何猜测。接受这个「宁可显示不知道也不猜」的口径？
12. **conversation 本体的绑定 UI 落点**：`trusted_users` 是「人」的天然落点，群没有对应的持久结构（`display_name_service.py:19` 明写开放平台 `get_group_list` 恒返回空表）。是否先只提供 HTTP 端点、UI 后补？

**本轮未覆盖 / 显式记为已知代价**

13. **`hybrid_recall` 无跨条目文本去重**（`hybrid_recall.py:665-700`）：展开后近重复条目互相挤 top-k。可接受但会随 account 数变差；若成为问题，独立 PR 加文本去重，不在本章范围。
14. **conversation canonical 封定后的信号可见性边界**：`_in_signal_scope`（`facts.py:1304-1305`）与 `_fact_dedup_domain`（`fact_dedup.py:161-166`）都按 `rsplit(':',1)[0]` 比群前缀 ⇒ `qq:G_c` 与 `qq:G_uin` 不等 ⇒ 主人的 confirmation/correction **看不见封定边界之前的老行**，跨渠道同群的重复 fact 也不在同一去重域相遇。方向 fail-closed，可接受；但「写路由买到同一句话能正常去重」这句话**只对封定之后写的行成立**，文档必须限定。
15. **跨群披露的口子仍然关着**：`resolve_group_recall_subjects` 的第二个「群」槽位刻意留空（其 docstring 已写明）。entity 展开只在同一 conversation 内做；conversation 本体的展开只处理「同一个真实群的多个 id」，**不**打开「读另一个真实群的记忆」。这条边界必须在文档里写死，否则将来会有人把 conversation 展开误读成跨群召回许可。

---

## 2.15.8 与既有文档的冲突处（本章优先）

| 既有表述 | 出处 | 本章结论 |
|---|---|---|
| 「backlog 零成本取证」 | 修订 §2.14.2 | **对群路径失效**（`group_id` 键名未验证 ⇒ 群消息可能根本不落 backlog）。改为读日志，方案见 §2.15.4.2。 |
| R11 的后果是「trust 按群碎成 N 份、攒不到 cap」 | 修订 §2.14.1 | **低估一个量级**：base、adjustment 来源、owner 授权三条轴同时归零（§2.15.4.4）。 |
| D 类 11 处等值比较不实体化（理由：放宽的失败方向是 mixed） | 主设计 §1.2(1) | 该理由的**前提是 account ≡ 人**。canonical 路由作废这个前提，方向反转为「不放宽 ⇒ mixed」。本章在 provenance 三处 + 仲裁三处放宽，且全部以**弃权**（而非合并）为放宽形式，失败方向不落 mixed。 |
| `speaker_base_trust` 的 0.8 上界「封死 guard_level → owner 级仲裁权」 | 主设计 §4.1 / `:438` | 只夹 base 时该断言为假（0.8+0.30+0.02=1.0）。clamp 上移到本段最终分（§2.15.3.4）。 |
| `§4.7` 的 cap no-op 写放大优化 | 主设计 §4.7 | 饱和判据必须上移到**实体和**，否则对多账号实体失效（§2.15.3.3 A-3）。 |
| 「`subject_forget_tombstones.json` 不存在，只有进程内存态」 | 前置取证 | **错**，见 §2.15.0 更正 1。 |
| 「群召回成员槽只有 3 个」 | 前置取证 | **错**，是 4 个成员 + 1 个群 = 5，见 §2.15.0 更正 2。 |
| 「`to_domain()` 有 8 个调用点」 | 候选设计 | **错**，生产是 7 个，见 §2.15.0 更正 3。 |
| 「`resolve_trust` 有四个 None 条件（含账本缺失 / 平台不可解析）」 | 候选设计 | **错**，恰好三个，且禁止新增，见 §2.15.3.8。 |
