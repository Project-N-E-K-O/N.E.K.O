# 常驻区和承诺

代码：`memory/pinned_facts.py`（`PinnedFactStore`），常量在 `config/memory_settings.py` 的 `PINNED_*`，提示词是 `PINNED_FACT_RESOLVE_PROMPT`、`PINNED_FACT_REVIEW_PROMPT`。模块 docstring 和常量注释是设计说明，先读。下面只列评审里来回论证过、容易被改回去的规则。

## 1. 边界

- **只在私聊。** 群聊 / 群成员的批量提取提示词有意不带 `pin` 字段；只有 `/new_dialog` 渲染常驻区。守护：`tests/unit/test_pinned_facts.py::test_only_the_private_new_dialog_renders_the_pinned_section`（AST 扫描谁引用了读取函数）。新增读取点要么别加，要么改这份名单并说明为什么不会把私聊内容带进群聊提示词。
- **入区不影响普通流水线。** 被 `pin` 的事实照常走反思和升格；到期只是离开常驻区。
- **store 没有进程内缓存**，每次读文件。这样角色改名、删除、云存档恢复之后不会有一份旧拷贝被下一次写入写回去。`pinned_facts.json` 在云存档白名单里，整段读-改-写包在 `cloudsave_writable_transaction` 里。
- 条目正文不进 `logger`，只记数量和 id。

## 2. 有效期

- 四个档位（`PINNED_FACT_TTL_TIERS_DAYS`）是**硬上限**，从说这句话的时候算，到期即离开；模型的决定和日期都不能把条目留得更久。
- 有已知结束时间的：`expire_at = min(档位上限, 结束时间 + PINNED_FACT_DUE_GRACE_DAYS)`。
- 事实只认结束时间：只有开始时间的「感冒了」不算有到期日。承诺没有结束时间时用开始时间（「明天提醒你」）。
- 续期（renew）没带日期时沿用条目原来的到期日，防止「下周三考试」在周四被再提一次就续满一整档；较新的说法第一次带日期时按日期收紧。
- 条目按**对话时间**记日期，不按处理时间。入区前就已过期的候选只能用来移除。

## 3. 入区决策（add / replace / renew / remove）

- 区里为空时直接加；否则一次模型调用看整个区，逐候选决定。**调模型时不持锁**，所以每个决定落盘前都按 `rev` 核对「我看到的那版还在不在」，最多 `PINNED_FACT_RESOLVE_ATTEMPTS` 轮。最后一轮仍然核对，过时的决定降级而不是覆盖：remove 丢弃，replace / renew 改成 add。
- ⚠️ 因此别的子系统改条目内容时必须给 `rev` 加一；`surfaced_at`、`mentioned_at`、`mention_count` 这类记账不加。
- **时间先后由代码强制**：候选的记录时间早于目标条目的文本时间时，remove 丢弃、replace 降级为 renew。同文本续期时文本时间只往后挪。
- **撤销过的旧说法不会把条目带回来**：remove / replace / retire 的历史记录带时间；逐字相同且早于撤销的说法，add 时直接丢弃。被判为 remove 的候选在后续轮次里只能 remove，撤销句永远不会入区。
- **角色卡冲突按实体选卡面分区比较**（`memory/persona/facts.py::_CARD_SECTIONS_BY_ENTITY`）：`master` / `neko` 只比各自的分区，`relationship` 比两张卡。跨分区比较会误拒：卡上角色的「生日: 3月5日」会把「用户的生日是5月3日」判成冲突。
- 评审担心「入区重试会推迟后面的判分」：不成立，同一角色的两遍提取是串行的，不会并发入区。

## 4. 承诺

- 只放开「角色自己许下的事」这一窄类：`pin_kind: "commitment"`。代码只校验「有时间或有条件」，两者都没有的是客套话，不入区；对象和动作靠提示词把关。
- `pin_done` 是兑现报告，只能把承诺销掉（状态 `done`），自己永不入区。
- 条件只在 `pin_condition` 里有时才并进正文；长承诺截断时先给条件留位置。

## 5. 退役（retire）只认代码能验的证据

- `done` / `invalid` 需要：一条不早于该条目的候选，或对话里的逐字引文（至少 `_QUOTE_MIN_TOKENS` 个 token，出自**严格晚于**条目文本时间的消息）。严格晚于的原因：条目按窗口最后一条消息记时，许诺本身可能正好等于这个时间，许诺不能证明自己被兑现。
- `past` 没有证据时只在两种情况下成立：记录的到期时间已过；或没有到期时间且不是最长那一档。过敏、生日这类不会只因为日期就被移出。
- 退役按 `rev` 核对、不重试。退役记录和移除一样算撤销，旧说法重放不会把它带回来。

## 6. 每日补查

- 空闲时最多一天一次：先在维护状态里占位（`pinned_review_ts`，失败也算看过），store 里的 `reviewed_at` 在间隔内就不调模型。占位用的 mutator 必须是具名的同步函数加 `functools.partial`。
- 区里有未兑现的承诺时，入区决策不算「看过」，补查照跑。
- ⚠️ 补查必须是登记过、可取消的角色任务（`review.pinned_review_tasks`），见 SKILL.md 的清单。测试里等补查结束要设超时，否则「释放时取消不到」的变异会让 pytest 永远挂住。
- 区里没有未兑现的承诺时，入区决策那次模型判断算「看过」，间隔内不再补查；每天都有入区的话补查就一直不跑，这没有问题，整个区已经被入区那次看过了。区里有未兑现的承诺时，补查不受入区影响，照常最多一天一次。

## 7. 防复读

常驻区每次开场都在，所以必须自己防复读：

- 角色的每条私聊回复都会被检查提到了哪些条目（`record_mentions`）。提到后进冷却，渲染到「最近已经提过的事」一节（仍然知道、不主动提、用户说起照接、承诺到时照做），冷却中不交给主动搭话。冷却过后再提就翻倍，有上限；冷却中再提只顺延。有日期的承诺最晚在到期前 `PINNED_COMMITMENT_DUE_LEAD_HOURS` 出冷却。条目被 replace 后清零。
- ⚠️ **提及匹配不能用人格那套「任一关键词命中」**。那套对英文的 `to`、`you` 也算命中，闲聊就会让承诺永远待在冷却里、不再交给主动搭话。这里用 `_mention_keywords` / `_mentions`：CJK 取两字片段，其它文字取至少 4 个字符的整词，至少 2 个不同片段重合才算。匹配是按读到的文本做的，所以写入前核对 `rev`：中途被改写的条目不记。

## 8. 承诺进主动搭话

- `/followup_topics` 只交出**有到期时间、还没兑现、`PINNED_COMMITMENT_DUE_SOON_DAYS` 天内到期**的承诺里最早到期的一条，带 `PINNED_COMMITMENT_SURFACE_COOLDOWN_HOURS` 的冷却。没有日期的承诺不交，渲染时也不写期限。
- 承诺的 id 单独跟踪，经 `/record_surfaced` 的 `commitment_ids` 送回，**不算回忆话题通道的使用**；回忆话题被压住时承诺保留。只有承诺可说时，主动搭话第一阶段不直接放弃（`_decide_phase1_channels(has_due_commitment=True)`）。
- **什么时候算「已经交给过」**：模型回出第一块内容时。`CommitmentOffer.mark_model_replied` 在流里同步置标记，HTTP 记录等生成结束后再发。三种别的时机都试过、都有问题：模型对象建好就记，流连不上或超时时承诺白进冷却；投递成功才记，模型放弃发言的轮次会每轮重选同一条；调模型前就记，本轮作废时误记。
- ⚠️ `_make_proactive_llm` 末尾做完屏幕授权检查之后、到 `llm.astream` 之前，**不能有任何 `await`**。中间如果等一次 HTTP，用户在这段时间撤回授权，截图照样发出去。所以记录承诺的 HTTP 不能放在这里。
- `/record_surfaced` 带 `commitment_ids` 时返回 `commitments_recorded`，只有真正写盘才为 true；否则调用方留给投递后补记。
