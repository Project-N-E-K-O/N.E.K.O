# 群聊 / 群成员 / 跨群的人的记忆（带作用域的记忆）

代码：`memory/scopes.py`（主体和作用域）、`memory/subject_archive.py`、`memory/scoped_refine.py`、`memory/scoped_client.py`、`memory/reflection/synthesis.py::synthesize_scoped_reflections`、`app/memory_server/routes.py` 里的 `/scoped_context`、`/scoped_history`、`/query_memory`。升格门槛和「重新看到」加分的规则写在 `docs/architecture/memory-system.md`。

调用方（聊天平台接入）不在主仓库里时，下面的端点行为就是主仓库这边要保持的契约。

## 1. 群聊记忆走简化流水线

群里收不到用户反馈，所以不走私聊那套「待定 → 反馈 → 确认」：

- 带作用域的事实写盘时就是 `signal_processed=True`，不进判分；万一出现没判分的带作用域事实，判分那一轮会直接把它标掉。
- 反思合成出来就是 `confirmed`。⚠️ 已确认反思的渲染条件是证据分**严格大于 0**，而群聊反思永远收不到证据，所以合成时给一个最小的正起始分（0.1）。起始分为 0 的话，这条反思在 `/scoped_context` 里是隐身的。
- 生命周期只按时间推进；按分数的各步都跳过带作用域的条目。主动提起（surfacing）只对私聊旧数据做。
- 群聊主体多，合成用轮转游标逐个主体推进。
- 群聊几乎不进人格。门槛见架构文档；**想放宽门槛，或把「被别人的话重新看到就加分」扩到私聊，先问维护者**。已有的群聊人格条目不回头处理。升格被送进矛盾队列就算结束（`promoted_via='correction_queue'`），由仲裁决定进不进人格。

## 2. 主体有三态，旧数据路径要用专门的判断

一行记录的主体描述符由 `subject_kind`、`subject_id`、`scope` 三个字段组成：

| 状态 | 判断 | 归属 |
|---|---|---|
| 私聊旧数据 | 三个字段都没有：`is_legacy_private_entry()` 为真 | 私聊 |
| 带作用域 | 三个字段齐全且合法：`subject_from_entry()` 返回主体 | 对应的群 / 人 |
| 损坏 | 只有一部分字段，或不合法 | **哪边都不归**，所有正常读取路径都排除 |

- ⚠️ 只该处理私聊旧数据的路径（升格进私聊人格、主动提起等）必须用 `is_legacy_private_entry()`。「主体不为空就跳过」只挡得住带作用域的行，挡不住损坏的行；损坏的描述符曾经因此被放进私聊人格，等于跨边界泄漏。
- 存下来的行缺 `scope` 不能被补成默认作用域：那会让自定义作用域的行越过隔离边界。只有请求入参的归一化（`coerce_subject`）可以补默认值。
- 匹配用 `entry_matches_subject()`，它同时比 key 和 scope。

## 3. 域隔离要在每个共用的地方做

群聊和私聊的条目共用同一批文件，隔离靠逐条判断：

- `persona.json` 的 section key 只有 `kind:subject_id`，同一个 section 里可能有两个自定义作用域的条目。渲染时**逐条**授权，不按 section 整段放行；预算分桶也按条目自己的 `(主体 key, scope)`。
- 矛盾队列、事实去重队列、精炼的聚类，都要按 `(主体 key, scope)` 定域，一批只处理一个域。带作用域的升格合并，候选和目标两侧都要限定在同一个域里。
- 带作用域的文本不能在 `facts.json` 之外再出现一份明文（去重队列只存 id 就是这个原因），也不进 `logger`。

## 4. 端点契约

- `/query_memory`：`subjects` **省略**表示查私聊旧数据；**显式传空列表**返回 422。群聊调用方没有任何已授权主体时必须拿到零结果，不能落到私聊语料上。这个区分只能在入口做：下游把空元组和 `None` 当成一回事。`/scoped_context` 同样拒绝空列表。
- 因此 `subjects` 永远由调用方的代码组装，**不能进工具 schema 让模型填**：模型的任何输入都不该影响查的是谁的记忆。
- 失败即拒绝（fail-closed）的写入端点，落盘失败时要把缓存里的新增和 FTS 索引一起回滚。否则重试会撞上去重，报一个「空成功」并推进游标。
- 被动注入只查私聊作用域（见 `recall-and-tool-leak.md`）。

## 5. 主体按时间归档

群聊条目收不到负面证据，按分数的归档对它们永远触发不了，所以改按「这个主体最后一次写入距今多久」归档（`SCOPED_SUBJECT_STALE_DAYS`）。

- **账本就是数据本身。** 主体的最后写入时间每轮现算（该主体所有行的创建 / 确认时间取最大，事实要算活跃区加归档的全量）。有意不设 sidecar：活得比数据久的账本在数据回滚后会永久错位。想给它加缓存文件，必须满足 `storage.md` 里「新增记忆文件」的全部条件。
- 归档不是删除。事实带上 `subject_archived_at` 标记移走；这个标记有几个消费点，动任何一处都要对齐：
  - 召回的归档池装配（`hybrid_recall._aload_archive_facts`，两条召回路径共用）滤掉带标记的行；
  - `facts.py` 的字面相近去重跳过带标记的行，这样主体复活后重述的旧事实能作为新事实落盘；
  - 反思合成排除带标记的行；
  - 恢复时剥掉标记。
- ⚠️ **反思不能就地把 `status` 改成 `'archived'`。** `memory/_reflection/schema.py::prepare_save_reflections` 只保留 `merged`、`promote_blocked`（以及未到期的 `promoted` / `denied`），盘上状态为 `'archived'` 的行会在下一次保存时被静默丢弃。主体归档反思必须走事件溯源的 `aarchive_reflection`（物理搬进分片）。轻量精炼消耗掉的来源用 `status='merged'` 加 `absorbed_into`，这个是安全的。
- 恢复会追加补偿事件。哨兵被重置后的全量重放会重新应用归档（恢复事件重放时是空操作），这时再跑一次恢复即可，分片里的副本从不销毁。

## 6. 轻量精炼的成本约定

`memory/scoped_refine.py` 只做合并。分桶按 `(主体 key, scope)` 乘以存储种类，**不按 entity**；一个桶至少 `SCOPED_REFINE_MIN_ENTRIES` 条才触发；每一轮全部角色合起来最多处理一个聚类，成不成功都算。改这些会直接改后台调用量。

## 7. 每周自省和来源标记

- 强力记忆开启时，每周自省也按主体逐个审群聊的注入块（见 `reflection-persona.md` 第 5 节）。
- 整群那一路的提取、且历史里有角色自己的发言时，提示词末尾追加一段要求标 `source`，角色自己的话存成 `ai_disclosure`。私聊和按人分段那一路的提示词不加这一段。只靠角色自述的群聊印象照常生成和显示，但不能升格。
