# 施工计划：`memory-evidence-rfc.md` v1

> 本文件是 **plan 文件**（不是 RFC 本身，也不是最终交付物）。目的是把 RFC 里每一个设计决定的"前因 + 选型 + 后果"先摊开给 reviewer，等达成共识再动手写 RFC。
>
> 最终交付物是另一个新文件 `docs/design/memory-evidence-rfc.md`（commit + push 到 `claude/review-pr-634-memory-rSsDR`，**不开 PR**）。本文件在 RFC 写完后可以删，也可以保留作为设计讨论记录。
>
> 本计划已经过 round-1 / round-2 reviewer 反馈调整。变更热点：score 公式去 importance 化、token budget 拆分 persona / reflection、背景循环承载信号抽取和归档、`persona.entry_updated` 作为确定的新事件类型。

---

## 0. Context

### 0.1 任务输入

- Handoff spec: [`docs/design/memory-evidence-task.md`](memory-evidence-task.md)（commit `bd2477be`，target branch `claude/review-pr-634-memory-rSsDR`）
- Template RFC: [`docs/design/memory-event-log-rfc.md`](memory-event-log-rfc.md)（840 行，结构参考）
- 上游 PR / Issue：
  - [#849](https://github.com/Project-N-E-K-O/N.E.K.O/issues/849)（**OPEN issue**）条目级 user-driven evidence 提议——本 RFC 是它的设计落地
  - [#905](https://github.com/Project-N-E-K-O/N.E.K.O/pull/905)（**MERGED**）P0+P1+P2.a 基础设施（events.ndjson + Reconciler + per-character 锁 + Outbox + CursorStore）——evidence 机制必须 **复用** `EventLog.(a)record_and_save`

### 0.2 单一交付物

`docs/design/memory-evidence-rfc.md` v1，一次 commit + 一次 push；**不开 PR**、**不改其他文件**。

### 0.3 目标长度

不套用模板的 840 行限制——用户明确要求"尽可能多写、细致"。目标 **1500–2000 行**。每个设计决定必配 rationale 块 + 失败模式分析 + 代码锚点。

---

## 1. 完整生命周期前言（RFC 里作为 §1.1 或 §3.0）

> reviewer 反馈要求一个独立前言段把 fact → reflection → persona → archive 走一遍，避免后续各 § 被局部读时误会。

### 1.1 一条 user-signal 的完整旅程

以一条典型 fact "主人喜欢猫娘" 为例：

```
─────────────── 阶段 0：消息沉淀 ───────────────

 用户 ──对话──▶ recent_history.json
                 │
                 │ (不主动触发任何处理，只是累积)
                 ▼
─────────────── 阶段 1：信号抽取（背景任务，不在对话主路径）───────────────

memory_server.py:680 _periodic_idle_maintenance_loop
  │ (每 40s 轮询；空闲且 history >= 10 轮 / 距上次 check >= 5min 时进入)
  │
  ├──▶ Stage-1: FactStore._allm_extract_facts(new_user_messages)
  │       │  纯抽取，prompt 不带任何已有观察，避免 LLM 把已有观察当新 fact 摘出
  │       │  LLM call → [{text, importance, entity}, ...]
  │       │  （不要 tags、不要 reinforces/negates；这些是 Stage-2 的事）
  │       │  importance < 5 → **存但不参与后续 reflection 合成**
  │       │     （消费侧 get_unabsorbed_facts(min_importance=5) 过滤，
  │       │      不在 extract 内硬丢；保留 audit + 未来调阈值不丢历史）
  │       │  [SHA-256 / FTS5 dedup] → 重复则跳过（已有 fact 与 Stage-2 信号检测照常）
  │       ▼
  │       facts.json 新增 entry {id, text, importance, entity, ...}
  │
  ├──▶ Stage-2: FactStore._allm_detect_signals(new_facts, existing_observations)
  │       │  prompt 带 new_facts + 已有观察清单（confirmed+promoted reflection
  │       │    + 非 protected persona entry, 形如 [{id, text}]）
  │       │  LLM call → {signals: [{source_fact_id?, target_type, target_id,
  │       │                          signal: "reinforces" | "negates", reason}]}
  │       │  防御：每条返回的 target_id 必须在 existing_observations 集合里存在
  │       │       （LLM 可能编造 ID，编造的丢弃 + log warning）
  │       ▼
  │       按 signals 列表 dispatch：
  │         reinforces → reinforcement +=1.0, source='user_fact'
  │         negates    → disputation   +=1.0, source='user_fact'
  │       → EVT_REFLECTION_EVIDENCE_UPDATED / EVT_PERSONA_EVIDENCE_UPDATED
  │
  └──▶ 在【对话主路径】（非背景循环）：负面关键词 → LLM 二次判 target →
       逐条 disputation +=1。完整 cascade 见下方"负面关键词到隐藏的全链路"。

─────────────── 阶段 1.5：负面关键词到隐藏的全链路 ───────────────

用户说 "别提了" / "换个话题" 这类话之后，到该 reflection / persona 真正在
渲染中消失，发生这些事（按时间顺序）：

  ① /process 处理 user 消息时，本地扫 NEGATIVE_KEYWORDS_I18N（5 语言 frozenset）
     命中？没命中 → 走完正常对话流程，结束。
     命中 → 立刻（不等下个 idle 周期）触发 ② 。

  ② NEGATIVE_TARGET_CHECK_PROMPT 异步派发（async task，不阻塞当轮回话）
     LLM 输入：近期 user 消息 + surfaced reflection(feedback=null)
              + 最近被 mention 的 persona entry
     LLM 输出：targets 列表 / 空数组
     空数组 → 用户只是泛化情绪 → no-op，结束。

  ③ 对每个 target 走 PersonaManager.aapply_signal / ReflectionEngine.aapply_signal：
     disputation +=1.0, source='user_keyword_rebut'
     → 落 EVT_*_EVIDENCE_UPDATED 事件、view 同步落盘。

  ④ 该条目 evidence_score 当下立即 -= 1.0（read-time 现算）。

  ⑤ 后续行为：
     - render 时 (arender_persona_markdown)：按 evidence_score DESC 排序 +
       token 预算 trim → 这条低分被挤出渲染 = LLM 看不到 = 不主动提了
     - aget_followup_topics：按 score 过滤（建议 score >= 0 才进候选池），
       低分 reflection 不再被主动挑出搭话
     - 同时：score 跨 0 → sub_zero_days 计数器开始累加（每天 +1）
       连续累计 EVIDENCE_ARCHIVE_DAYS=14 天 score 仍 < 0
       → 真正归档到 *_archive/<date>_<uuid8>.json 分片
       （主路径不再加载，但硬盘永久保留——见 §3.11）

  ⑥ 用户反转态度（重新提起、reinforces）→ reinforcement +=1.0
     → score 回正 → 自然回到 render → 主动 followup 重新候选
     注意：sub_zero_days 计数器不清零（"归档更积极"，reviewer 锁定），
     只是计时器满之前 score 回正的话当然不会 archive。

  整条链路无独立 "suppress=True" 状态变更——隐藏完全靠
  evidence_score 在 read 时排序 + budget 挤出实现。
  现有的 suppress / suppressed_at（persona.py:553）字段是另一套
  机制（"AI 自己提太多 → 抑制"），与本路径正交，保持原状不动。

─────────────── 阶段 2：反思合成 ───────────────

 积累到 MIN_FACTS_FOR_REFLECTION=5 条未 absorb 的 fact 时：
  ReflectionEngine.synthesize_reflections(name)
    │  LLM prompt 分两区：
    │    [待合成 - 未 absorbed 的目标 fact]
    │      - [fact_001] 主人今天咖啡加双份糖  (importance=6)
    │      - [fact_002] 主人买了新的意式咖啡豆  (importance=5)
    │      ...
    │    [上下文参考 - 最近已 absorbed，不要重复合成]
    │      - [fact_old_xxx] 主人喜欢咖啡      (已在 reflection ref_abc)
    │      - [fact_old_yyy] 主人每天一杯拿铁  (已在 reflection ref_def)
    │      ...
    │    取最近 REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_COUNT=10 条已 absorbed、
    │    且在 REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_DAYS=14 天内的 fact 进上下文区。
    │    prompt 明示："新 reflection 必须基于上区；下区仅供参考，若新事实
    │    仅是下区已有观察的细化或重复 → 返回空数组（不合成）。"
    │  reflection_id = sha256(sorted_source_fact_ids) → 确定性 id（P1 幂等）
    │  ▼
    │  EVT_REFLECTION_SYNTHESIZED 事件发出（status=pending, rein=0, disp=0）
    │  EVT_FACT_ABSORBED 事件发出（source fact 标记为已吸收）

─────────────── 阶段 3：主动搭话 + 用户反馈 ───────────────

 猫娘选 reflection 主动聊：
  ReflectionEngine.aget_followup_topics(name)
    │  过滤：pending && next_eligible_at 到期
    │  （原先设想的"tag 不在 topic hard_avoid"过滤 → Q7 待重新设计，V1 不做）
    │  ▼
  前端 render → 用户看到 → 用户回话
  ReflectionEngine.arecord_surfaced → surfaced.json 登记

 下一轮空闲维护：
  ReflectionEngine.check_feedback(new_user_messages) → LLM 判断：
    - confirmed → reinforcement +=1.0 → EVT_REFLECTION_EVIDENCE_UPDATED
    - denied   → disputation   +=1.0 → EVT_REFLECTION_EVIDENCE_UPDATED
    - ignored  → reinforcement -=0.2 → EVT_REFLECTION_EVIDENCE_UPDATED
       （扣在 reinforcement 侧、允许为负；语义"弱正信号衰退"——
        reviewer 权衡后选此方案，非 disputation+=0.2；
        reinforcement 非严格单调，但它本就表达"被积极肯定过的累积强度"，
        负值意味着"曾被肯定过但近期被忽略"，语义自洽）

─────────────── 阶段 4：晋升 / 合并 / 否决 ───────────────

 当 reflection R 的 evidence_score 跨过 EVIDENCE_PROMOTED_THRESHOLD：
  ReflectionEngine._apromote_with_merge(R)
    │  Load: R + 同 entity 的所有现有 persona entry（非 protected）
    │       + 同 entity 的所有 confirmed/promoted reflection
    │  LLM call（PROMOTION_MERGE_PROMPT）
    │       → {action: "promote_fresh" | "merge_into" | "reject", ...}
    │  ▼
    action == "promote_fresh" →
      PersonaManager.aadd_fact(source='reflection', source_id=R.id)
        → EVT_PERSONA_FACT_ADDED
      ReflectionEngine state: confirmed → promoted
        → EVT_REFLECTION_STATE_CHANGED

    action == "merge_into target_id" →
      PersonaManager._amerge_into(target_id, merged_text)
        → EVT_PERSONA_ENTRY_UPDATED（新事件，§3.3）
        → EVT_PERSONA_EVIDENCE_UPDATED（更新 target evidence）
      ReflectionEngine state: confirmed → merged
        → EVT_REFLECTION_STATE_CHANGED（新 status 值 'merged'）

    action == "reject" →
      ReflectionEngine state: confirmed → denied
      denied_reason = 'llm_merge_rejected'

  LLM call 失败（超时 / 非法 JSON）→ 降级走 promote_fresh（不阻塞）

─────────────── 阶段 5：持续 user signal → 老化 → 归档 ───────────────

 promoted 的 persona entry 继续接收 user signal：
  每次 extract_facts 或 check_feedback 都可能再次 mutate evidence
  ↓
 Read-time decay（§3.5）：每次读 entry 现算 effective 值
  effective_reinforcement = reinforcement * 0.5 ^ (age_days / EVIDENCE_REIN_HALF_LIFE_DAYS)
  effective_disputation   = disputation   * 0.5 ^ (age_days / EVIDENCE_DISP_HALF_LIFE_DAYS)
  （disputation **也衰减**，但半衰期远长于 reinforcement——
    EVIDENCE_REIN_HALF_LIFE_DAYS=30, EVIDENCE_DISP_HALF_LIFE_DAYS=180。
    语义：用户否认是强信号但也不该永恒；半年后可重新观望。
    last_signal_at 同时作为两者的衰减起点）
  ↓
 背景归档循环（_periodic_archive_sweep_loop，新增，或合到 idle maintenance）：
  - 扫所有非 protected 条目
  - evidence_score < 0 → 累加到 entry['sub_zero_days']（整数计数器）
  - evidence_score >= 0 → sub_zero_days 不变（累计不回退，归档更积极）
  - sub_zero_days >= EVIDENCE_ARCHIVE_DAYS → 归档到分片文件
    memory/<char>/persona_archive/<YYYY-MM-DD>_<uuid8>.json
    memory/<char>/reflection_archive/<YYYY-MM-DD>_<uuid8>.json
    每个分片 entry 数 <= _ARCHIVE_FILE_MAX_ENTRIES (默认 500)

─────────────── 阶段 6：话题级聚合 ───────────────

 【本阶段待重新设计 —— 详见 §3.7 "pending redesign"。】

 原草案基于 fact 的 `tags` 字段（LLM 抽取时分配）聚合成 topic_score，
 reviewer 反馈 "没有候选、没有 source 的情况下让 LLM 凭空生成 tag 是 nonsense"：
   - LLM 每条 fact 独立生成 tags，缺乏全局一致性（"猫娘" / "猫" / "cat" 三条可能互不串）
   - tags 只有在"积累到一定数量条目后统一组织"时才有聚合价值
   - 更可能的走向是图数据库 / clustering，而非前置打 tag

 结论：**V1 RFC 不落地话题级聚合 + soft/hard avoid + 自动 emit "用户不想聊 X"
 persona fact**。下一轮讨论前这块悬空。

 依赖此阶段的下游效应（avoid section 注入 LLM prompt、extract 阶段按 tag 丢 fact
 等）全部跟着悬空，RFC §3.7 明确标注"pending Q7 下一轮讨论"。

─────────────── 阶段 7：渲染 ───────────────

 arender_persona_markdown(name):
  - Phase 1：split entries
      protected_entries   = source == 'character_card'
      non_protected_persona = 其余 persona
      pending_reflections + confirmed_reflections = 反思
  - Phase 2：score-trim per-section，独立预算
      protected_entries：整段输出，不计 token
      non_protected_persona：按 evidence_score DESC 保留，累计 acount_tokens 直到 <= PERSONA_RENDER_TOKEN_BUDGET
      reflections：按 evidence_score DESC 保留，累计 <= REFLECTION_RENDER_TOKEN_BUDGET
  - Phase 3：markdown 拼装（沿用现有 _compose_persona_markdown 结构）
      "### 关于主人" | "### 关于{ai_name}" | "### 关系动态" | 反思两类 | 抑制区
      （阶段 6 话题聚合 pending redesign，本阶段不追加 avoid section）

 注意：
   - **没有 render-time LLM 调用**。合并只在阶段 4 发生。
   - **没有 render cache**。合并结果是直接写回 persona.json 的（entry_updated 事件），
     下次读已经是合并后态，无需缓存中间产物。
```

### 1.2 关键概念速查

**公式（条目级）**：

```
evidence_score(entry, now)
  = effective_reinforcement(entry, now) - effective_disputation(entry, now)

effective_reinforcement(entry, now)
  = reinforcement(entry) × 0.5 ^ ((now - last_signal_at).days / EVIDENCE_REIN_HALF_LIFE_DAYS)

effective_disputation(entry, now)
  = disputation(entry)   × 0.5 ^ ((now - last_signal_at).days / EVIDENCE_DISP_HALF_LIFE_DAYS)

若 last_signal_at is None → 未收过任何 signal → 两项都不衰减（取原值）

EVIDENCE_REIN_HALF_LIFE_DAYS = 30     (草案值——待 §6.5 Gate 1 reviewer 敲定)
EVIDENCE_DISP_HALF_LIFE_DAYS = 180    (草案值——待 §6.5 Gate 1 reviewer 敲定)
```

初始值：新 reflection / persona entry `reinforcement=0, disputation=0, last_signal_at=null`
→ `score = 0`。**不依赖 importance**（与 task spec §5 公式有意偏离——
见 §3.1 完整 rationale，核心是不给 LLM 对 status 派生的裁量权）。

**importance 的职责**（不参与 score 计算）：
- 抽取时 `importance < 5` 的 fact **存但不参与 reflection 合成**（消费侧 `get_unabsorbed_facts(min_importance=5)` 过滤，`facts.py:315` 硬丢移除）
- 渲染时 score 相同的条目按 importance 高低 tiebreak

**派生状态阈值**：`EVIDENCE_CONFIRMED_THRESHOLD=1.0, EVIDENCE_PROMOTED_THRESHOLD=2.0, EVIDENCE_ARCHIVE_THRESHOLD=-2.0`。语义 = "净 user 确认次数"。

**topic_score（话题级）**：**pending redesign（Q7 下一轮）**。原基于 `tags` 聚合的方案被 reviewer 否定。

**protected**：character_card 来源的 persona entry，豁免衰减 / 归档 / render budget 计数 / LLM 合并改写。

**user signal 封闭集合**：3 个源 + 映射表见 §3.4；**除此之外任何路径都不能动 evidence counter**（红线 1）。

---

## 2. 五条红线（task spec §4 → RFC §2 / §7 逐条复现）

每一条在 RFC 不同位置至少命中 2 次（§2 non-goals 一次，§7 explicit rejects 一次）：

1. **No AI-driven signals.** evidence 计数器仅因"用户显式输入"移动。3 个入口：
   - (a) `FactStore.extract_facts` 识别 `reinforces` / `negates` 字段
   - (b) `ReflectionEngine.check_feedback` 分析 user 消息对 surfaced reflection 的反应
   - (c) 本地负面关键词扫描（`"别提了"` / `"我不想聊"` 等短语命中）+ LLM 二次语义判定 target
   
   AI 在 response 里提到某 fact **不加分**；reflection synthesis 链路不给自己加分；`recent_mentions` 留着但方向相反（AI 提到多 → suppress，不是 reinforce）。

2. **No time-based auto-promotion.** promote 必须是 "score 穿过 `EVIDENCE_PROMOTED_THRESHOLD`" 触发，不是 "N 天没反对就升"。**删除** `memory/reflection.py:66-67` 的 `AUTO_CONFIRM_DAYS = 3` / `AUTO_PROMOTE_DAYS = 3` 常量，**重构** `_aauto_promote_stale_locked`（`reflection.py:820`）去掉时间跳级分支——新版只按 score 触发。
   
   这也是前一轮 reviewer 问 "红线 2 是什么" 的答案。RFC 会把这一条单独拉出来作为 §3 的一个 callout 框。

3. **`protected=True` 条目全面豁免。** `source='character_card'` 的 persona entry（`persona.py:540-572` 里 `_normalize_entry` defaults 含 `protected: False`，card 同步时由 `_apply_character_card_sync` 置 True）：
   - 不参与衰减（§3.5）
   - 不参与归档（§3.5）
   - 不计入 render budget token 数（§3.6）
   - 不参与 score-trim 淘汰（§3.6）
   - 不作为 LLM 合并候选池 / 不被合并改写（§3.9）

4. **事件 payload 不存原文。** 延续 event-log RFC §3.3.1。所有 user 派生内容走 `text_sha256` / `user_msg_sha256`。evidence_updated payload 不含 fact 原文、不含 user 消息原文。

5. **Read-time decay，不是 write-time。** write 只在真 user signal 到达时发生。衰减在每次 read 时现算，纯函数、无 I/O、无事件。否则每天每角色 N 条 evidence_updated 事件污染 event log。

---

## 3. 两条全局口径（reviewer 已锁定）

### 3.1 所有阈值是命名常量

所有新增 evidence 相关常量**统一放到 `config/__init__.py`**，并加进文件末 `__all__`——与项目主 config 单源一致（项目已有 `DEFAULT_SUMMARY_MODEL` / `APP_VERSION` / 各种 port / `NATIVE_IMAGE_MIN_INTERVAL` 等都在这里）。

说明：`memory/reflection.py:66-67` 的 `AUTO_CONFIRM_DAYS` / `AUTO_PROMOTE_DAYS` 是旧约定（模块内常量）——P-B 会**删除**这些（红线 2），新常量直接进 `config/__init__.py`，不沿袭旧的分散约定。

`memory/evidence.py` 是**新模块但只放纯函数 + 背景辅助**（`effective_reinforcement` / `effective_disputation` / `evidence_score` / `derive_status` / `maybe_mark_sub_zero` 等），**不放常量**。

`memory/event_log.py:51-70` 的 `EVT_*` 常量是例外——它们是该模块的 public API surface 一部分，沿用模块内定义的现行约定。新增的 `EVT_REFLECTION_EVIDENCE_UPDATED` / `EVT_PERSONA_EVIDENCE_UPDATED` / `EVT_PERSONA_ENTRY_UPDATED` 也加在 `event_log.py`，不进 `config/__init__.py`。

### 3.2 Token 计量用 tiktoken + o200k_base

- **为什么 o200k_base 不是 cl100k_base**：2026 年主力模型（GPT-4o/4.1/5 家族 + o1/o3/o4）全部在 o200k 上；cl100k 是 GPT-3.5/4 的旧编码。CJK 在 o200k_base 下表示更紧凑，对中文为主的 persona 文本计数更准。CPU 开销比 cl100k 慢 ~20%，2000 char 绝对值仍 <10ms。
- **依赖挂哪里**：项目主 dep manifest 是 **`pyproject.toml`**（uv / PEP 621）。`tiktoken>=0.7.0` 加进 `[project.dependencies]`。`requirements.txt` 是 `uv export` 的导出物，手改没意义——编辑 pyproject + 导出即可同步。
- **GIL 说明**：tiktoken Rust 层 encode 主循环显式释放 GIL，其它 Python 代码可与之并行。但 5ms 在 FastAPI 的 event loop 上仍是阻塞（event loop 不知道 Rust 释放了 GIL，自己停转）。**必须**走 `asyncio.to_thread`。`utils/tokenize.py` 按项目约定暴露 sync + async 对偶：`count_tokens(text) -> int` / `acount_tokens(text) -> int`。render 路径（`arender_persona_markdown`, `persona.py:1118`）用 `acount_tokens`。
- **Fallback 必须有显式 warning**：若 `import tiktoken` 失败 或 `get_encoding('o200k_base')` 抛（encoding 文件没被打包带入），第一次触发降级时 `logger.warning(...)` **一条**日志，内容明示："tiktoken 不可用，降级到启发式 token 计数；如果这是打包产物，请检查 Nuitka/PyInstaller 配置是否包含 tiktoken encoding 文件"。同一进程后续降级调用不再重复 warn（避免日志刷屏）。
- **Fallback 公式**（过估计偏置，宁肯少渲染不要超 budget）：
  ```python
  def _count_tokens_heuristic(text: str) -> int:
      cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' or '\uac00' <= c <= '\ud7af')
      non_cjk = len(text) - cjk
      return int(cjk * 1.5 + non_cjk * 0.25)
  ```
- **打包需求**：Nuitka/PyInstaller 配置要把 `tiktoken/encodings/o200k_base.tiktoken`（~1.5MB）打进产物。RFC §4 P-D 测试表里列一条"打包产物启动后 `count_tokens('测试')` 不走 fallback"。

---

## 4. RFC 章节骨架

> 本节对应 RFC 里每个 § 的"要说什么 + 为什么"。行数估计给自己控密度用——某节实际写出来远低于估值说明 rationale 块写漏了。

### Revision log + Status header（~40 行）

Status header 抄 task spec §7.1 原文（日期 2026-04-21 保持不变——这是 draft 作者开始写的日期）。Revision log v1 一条："initial draft of issue #849 evidence mechanism, built on #905 event-log infrastructure. Score formula departs from task spec §5 by dropping importance from the status-derivation formula (importance retained for extraction filter + render tiebreaker)."

### §1 Motivation（~120 行）

- 4 个已有痛点（`_texts_may_contradict` 启发式 + 无衰减 + 无 token 上限 + `importance` 运行时不决策）
- 为什么 #905 落地后是好时机（`EventLog.(a)record_and_save` 现成、per-character 锁齐备）
- 为什么独立 RFC（event-log RFC 已定稿 Implemented (P2.a)；evidence 新增字段 + 新事件，独立文档边界清）
- 本 RFC 正文大量 cite event-log RFC § 锚点（§3.4 write order、§3.4.3 idempotency、§3.5 startup order、§3.6 compaction），**不复述**

### §2 Non-goals（~100 行，每条一段）

1. No AI-driven signals（红线 1 全文）
2. No cross-character evidence
3. No ML-ranked persona（纯 rule-based，可解释）
4. **压根不做 per-entry UI 编辑按钮**（以前草稿把它放 out-of-scope 是错的——记忆系统需要隐私感，用户不应通过 UI 直接调 score；RFC §7 再强化一次）
5. No event schema versioning（沿用 event-log RFC §7）
6. 不改 `recent_mentions` 语义（反向机制，与 evidence 无关）

### §3 Proposed design（~1000 行主干）

分 11 个子节，比之前草稿多了 lifecycle preamble（§3.0）和 merge-on-promote（§3.9）。

#### §3.0 Lifecycle preamble（~100 行）

内容 = 本 plan 文件 §1.1 的 ASCII 流程图 + 每阶段的 2-3 行补充说明。RFC 读者从这里开始有完整图景，后续 § 都是展开。

#### §3.1 Score formula + derived status（~120 行）

**公式（偏离 task spec §5 的关键点）**：

```python
evidence_score(entry, now) =
    effective_reinforcement(entry, now) - effective_disputation(entry, now)

effective_reinforcement(entry, now) =
    reinforcement(entry) * 0.5 ** ((now - last_signal_at).days / EVIDENCE_REIN_HALF_LIFE_DAYS)
    if last_signal_at is not None else reinforcement(entry)

effective_disputation(entry, now) =
    disputation(entry)   * 0.5 ** ((now - last_signal_at).days / EVIDENCE_DISP_HALF_LIFE_DAYS)
    if last_signal_at is not None else disputation(entry)
```

**两个半衰期而非一个**（reviewer round-2 反馈）：

- `EVIDENCE_REIN_HALF_LIFE_DAYS = 30`：用户 1 个月前肯定的事，如果中间没再提，热度减半
- `EVIDENCE_DISP_HALF_LIFE_DAYS = 180`：用户 6 个月前否认的事，热度仍保留一半；否认是强信号但也不应永恒

语义直觉：**否认比肯定更持久**，但都不是"永不消退"。

**和 task spec §5 的偏离说明（RFC 明写）**：

task spec 原公式是 `importance + reinforcement - disputation`（无衰减），但 reviewer round-1 反馈指出阈值语义混乱："confirmed 阈值=5 意味着要被肯定 5 次？" 实际上这是因为 `importance` 在 [5, 10]，一条新 reflection 天生 `score = importance + 0 - 0 ∈ [5, 10]`，直接跨入 confirmed 甚至 promoted 档，不需要任何用户 signal。这违反 red line 1 的精神（"AI 自己觉得 important → 自动晋升" = AI-driven）。

新公式只用 `reinforcement - disputation`（都带衰减）：

- 一条新 pending reflection：`score = 0`
- 一次 user_confirm：`score = 1`（→ confirmed）
- 两次 user_confirm：`score = 2`（→ promoted）
- 一次 user_rebut：`score = -1`（仍 pending）
- 两次 user_rebut：`score = -2`（→ archive candidate）
- 一次 confirm + 一次 ignored：`score = 0.8`（仍 pending）

这样阈值 = 净 user 确认次数，完全符合 reviewer 直觉。

**importance 的新定位**（保留字段，改用途）：

- `facts.py:315` 的硬丢 `if importance < 5: continue` **移除**——低重要性 fact 存但不参与后续 reflection 合成（消费侧 `get_unabsorbed_facts(min_importance=5)` 已经在 `facts.py:363-368` 实现了过滤，保留即可；extract_facts 端去掉硬丢）
- 好处：保留 audit 轨迹、未来调阈值无需回溯 fact 历史、reviewer Q1 反馈
- 渲染时 tiebreaker：score-trim 阶段若两条 entry score 相等，importance 高的优先保留
- **不参与** status 判定、不参与 topic_score 聚合

**派生状态**（所有阈值在 `config/__init__.py`）：

```python
EVIDENCE_ARCHIVE_THRESHOLD   = -2.0  # score <= -2 → archive_candidate
EVIDENCE_CONFIRMED_THRESHOLD =  1.0  # 1 <= score < 2 → confirmed
EVIDENCE_PROMOTED_THRESHOLD  =  2.0  # score >= 2 → promoted
# pending 是 "其余"：ARCHIVE_THR < score < CONFIRMED_THR
```

（之前草稿里的 `EVIDENCE_PENDING_THRESHOLD` 是冗余——pending 是补集，不需要独立常量。）

**migration seeds 在新公式下**（task spec §5 表的数值要重写，RFC §5 写清楚映射）：

| 旧状态 | reinforcement seed | disputation seed | 迁移后 score | 派生状态 |
|---|---|---|---|---|
| pending | 0 | 0 | 0 | pending ✓ |
| confirmed | 1 | 0 | 1 | confirmed ✓ |
| promoted | 2 | 0 | 2 | promoted ✓ |
| denied | 0 | 2 | -2 | archive_candidate ✓ |

干净，每个旧状态的 score 正好落在新 tier 起点。

**Open Question**：`EVIDENCE_PENDING_THRESHOLD` / `EVIDENCE_CONFIRMED_THRESHOLD` / `EVIDENCE_PROMOTED_THRESHOLD` 的绝对数值是工程判断；可能需要实践后校准。新公式提议的 1 / 2 对应"1 次 confirm 就 confirmed，2 次就 promoted"——reviewer 认可语义方向，具体数值留给 §6 open questions 可再调。

#### §3.2 Schema changes（~90 行）

**fact schema**（`facts.py:334-343` 现行）不变：evidence 不在 fact 层（reviewer 确认：fact 是原料、不会被 user 反馈、维护 evidence 是浪费）。

**reflection schema**（`reflection.py:402-413` 现行）+ 3 字段（tag 传播暂缓）：

```json
{
  "id": "ref_...",
  "text": "...",
  "entity": "master",
  "status": "pending",    // pending | confirmed | promoted | merged | denied | archived
  "source_fact_ids": [...],
  "created_at": "...",
  "feedback": null,
  "next_eligible_at": "...",
  "reinforcement": 0.0,       // NEW — float
  "disputation": 0.0,         // NEW — float
  "last_signal_at": null,     // NEW — ISO8601 or null
  "sub_zero_days": 0          // NEW — accumulated int, 归档计数器
}
```

（原草案加的 `tags` 字段——从 source fact 聚合——**暂缓**。Q7 话题聚合方案重设计前，reflection 上的 tags 无下游消费方，加了就是死字段。V1 RFC 不落地 tags 传播，等 Q7 重新设计后再决定存不存。）

新 status 值 `merged`（§3.9）：被 LLM 合并到某 persona entry，等价于 promoted 但标注合并流向（`absorbed_into: target_id` 字段）。

**persona entry schema**（`persona.py:540-572` 的 `_normalize_entry` defaults）+ 同 4 字段（reinforcement / disputation / last_signal_at / sub_zero_days）。现有 `protected` 字段保留。

**特别说明**：现有 `recent_mentions` / `suppress` / `suppressed_at`（`persona.py:553-555`）与 evidence 正交。前者是 AI-emits-too-much → suppress，反向机制；后者是人工调试痕迹。都保持原语义。

#### §3.3 三个新事件类型（~130 行）

加进 `event_log.py:51-70` 已有 12 个 `EVT_*` + `ALL_EVENT_TYPES` frozenset（`:64`）：

```python
EVT_REFLECTION_EVIDENCE_UPDATED = "reflection.evidence_updated"   # NEW
EVT_PERSONA_EVIDENCE_UPDATED    = "persona.evidence_updated"      # NEW
EVT_PERSONA_ENTRY_UPDATED       = "persona.entry_updated"         # NEW — for merge-on-promote text rewrite
```

**为什么 3 个而不是 2 个**（reviewer 确认）：

合并场景里 target 的 text 会被 LLM 改写（"主人喜欢猫娘" + "主人爱猫娘" → 合并为 "主人喜欢猫娘，尤其感兴趣"），evidence_updated 只改 3 个数字字段无法承载 text 改写。新事件 `persona.entry_updated` 全快照承载 entry 完整 mutation（text + evidence 都可能变）。

**Payload（full-snapshot pattern）**：

```json
// reflection.evidence_updated
{
  "reflection_id": "ref_abc123",
  "reinforcement": 1.8,
  "disputation": 0.0,
  "last_signal_at": "2026-04-22T14:03:00",
  "sub_zero_days": 0,
  "source": "user_fact"
    // user_fact | user_rebut | user_confirm | user_ignore | user_keyword_rebut | migration_seed | topic_avoid_sweep
}

// persona.evidence_updated
{
  "entity_key": "master",
  "entry_id": "prom_ref_abc123",
  "reinforcement": 2.0,
  "disputation": 0.0,
  "last_signal_at": "2026-04-22T14:03:00",
  "sub_zero_days": 0,
  "source": "user_confirm"
}

// persona.entry_updated
{
  "entity_key": "master",
  "entry_id": "prom_ref_abc123",
  "rewrite_text_sha256": "...",       // 新 text 的 hash（原文不落日志）
  "reinforcement": 3.0,                // merge 后的合并 evidence
  "disputation": 0.0,
  "last_signal_at": "2026-04-22T14:03:00",
  "sub_zero_days": 0,
  "merged_from_ids": ["ref_abc", "ref_def"],   // 审计追溯
  "source": "promote_merge"
}
```

**为什么 full snapshot 而非 delta**：

事件日志会被 reconciler 在启动期回放（崩溃恢复 / 多次重启场景）。如果 payload 是 delta（如 `{"reinforcement_delta": +1.0}`），重放两遍同一事件就会 double-count，evidence 错涨。

snapshot payload（直接给字段最终值，不给增量）的话，重放就是"用快照覆写当前 view"——天然幂等：第一次 apply 改成 X，第二次再 apply 还是 X，第 100 次还是 X。

**事件日志和 reconciler 的工作模式**（来自 #905 已落地的基础设施 `memory/event_log.py`，本 RFC 复用）：

每次 evidence 变化都走 `EventLog.(a)record_and_save(name, event_type, payload, sync_load_view, sync_mutate_view, sync_save_view)`。该方法内部把 5 步串在**一把** per-character `threading.Lock` 里、整体包在一个 `asyncio.to_thread` worker 中：

1. `sync_load_view(name)` —— 拿当前 view（`reflections.json` / `persona.json`）的内存对象
2. **append 事件到 `events.ndjson`**（带 fsync）
3. `sync_mutate_view(view)` —— 在内存里改 view
4. `sync_save_view(name, view)` —— `atomic_write_json` 落盘
5. **advance sentinel**（写 `events_applied.json`，记录 "已 apply 到哪个 event_id"）

为什么 append 在 mutate 之前：如果先 mutate 后 append，append 失败（fsync OSError、磁盘满等）会留下 cache 已脏但事件无、后续任一次 normal save 都会把这个"无事件对应的脏改动"刷盘——破坏 event ↔ view 的对应关系。先 append 后 mutate 保证：要么事件 + view 都成、要么都没动、要么事件成但 view 没成（这种由 reconciler 在下次启动时补齐）。

**Reconciler 工作流**（启动期）：

读 sentinel `events_applied.json` 拿"上次 apply 到哪"，从 `events.ndjson` 读未 apply 的尾部事件，逐条调用对应类型的 handler 函数（handler 自己负责 load → 改 view → atomic_write_json save），一条 apply 完一条更新 sentinel。Handler 必须**幂等**——重放一条已经 apply 过的事件不能产生 side-effect 漂移。

**handler 必须 sync**：由 `memory/event_log.py:97` 的 `ApplyHandler` type alias 规定（`Callable[[str, dict], bool]`，无 async）。Reconciler 直接同步调用，async handler 会返回 coroutine 但不会被 await，等于静默失败。

**本 RFC 三个新事件的 handler 行为**：

| 事件 | apply 行为 | 幂等保证 |
|---|---|---|
| `reflection.evidence_updated` | load reflections.json → 找到 `reflection_id` 的条目 → 覆写 reinforcement / disputation / last_signal_at / sub_zero_days 到 payload 值 → atomic_write_json | 覆写到固定值，重放 N 次结果一致 |
| `persona.evidence_updated` | load persona.json → 找到 `(entity_key, entry_id)` → 覆写同 4 字段 | 同上 |
| `persona.entry_updated` | load persona.json → 找到 `(entity_key, entry_id)` → 校验现有 text 的 sha256 是否等于 payload 里的 `rewrite_text_sha256` —— 等于则 no-op（已 apply 过）；不等则**报错**（view 漂了，reconciler 救不了，需人工介入） | text 不在 log 里，靠 sha256 比对判断状态 |

第三个事件的 sha256 验证机制有个老问题（RFC §6 open question 9 登记）：原文不放进 payload 是为了不在事件日志里留 plaintext（红线 4）；代价是如果 view save 失败 + 事件 append 成功，重启后 reconciler 看到 sha256 对不上但又拿不到原文重建——这种情况只能停 reconciler 让人查。生产里这一路径罕见（atomic_write_json 在原子性上够强），所以接受。

**Writer 合约的锁嵌套**（防死锁约定）：

`PersonaManager._alocks` / `ReflectionEngine._alocks`（per-character `asyncio.Lock`，#905 已加）在**外**层。所有 `apply_signal` / `amerge_into` 方法都先 `async with self._get_alock(name)` 拿到 async 锁，再调 `await arecord_and_save(...)`——后者内部 `asyncio.to_thread` 进 worker、worker 拿 `EventLog._locks` 的 `threading.Lock`（event_log.py:111）。方向不能反，否则 sync 锁持锁跨 await 边界进 async 锁会引入死锁风险。

**Reconciler handler 注册**（`memory_server` 启动期；目前没有这段代码，因为 P2.b producer wiring 还没落，本 RFC 实现 PR 一并补上）：

```python
reconciler.register(EVT_REFLECTION_EVIDENCE_UPDATED, _apply_reflection_evidence)
reconciler.register(EVT_PERSONA_EVIDENCE_UPDATED,    _apply_persona_evidence)
reconciler.register(EVT_PERSONA_ENTRY_UPDATED,       _apply_persona_entry)
```

#### §3.4 Signal detection（P-A, ~250 行）

**信号源 + 映射表（两阶段 LLM：先抽 fact、再判 signal 映射）**：

| # | Signal 源 | 触发点 | 目标 | Evidence 变化 |
|---|---|---|---|---|
| 1 | **Stage-2 signal detection** 判定 `reinforces: {target_type, target_id, reason}` | `FactStore.extract_facts_and_detect_signals` 第二次 LLM call | 目标 reflection / persona entry | `reinforcement += 1.0`, source=`user_fact` |
| 2 | **Stage-2 signal detection** 判定 `negates: {target_type, target_id, reason}` | 同上 | 同上 | `disputation += 1.0`, source=`user_fact` |
| 3 | `check_feedback` returns `confirmed` for surfaced reflection | `ReflectionEngine.check_feedback` (`reflection.py:557`) | 该 reflection 自身 | `reinforcement += 1.0`, source=`user_confirm` |
| 4 | `check_feedback` returns `denied` | 同上 | 同上 | `disputation += 1.0`, source=`user_rebut` |
| 5 | `check_feedback` returns `ignored` | 同上 | 同上 | `reinforcement -= 0.2` (可负), source=`user_ignore` |
| 6 | 本地负面关键词命中 → LLM 语义判断确定 target | 对话主路径 hook | LLM 返回的 `target_id` 列表 | `disputation += 1.0`, source=`user_keyword_rebut` |

**核心设计：把 "抽取新 fact" 和 "判 signal 映射" 拆成两次独立 LLM 调用**（reviewer round-3 反馈）。

**为什么拆**：

原 draft 想让 extract_facts 一次 call 同时输出 `{text, importance, entity}` + `reinforces/negates`。问题：

- 给 LLM 传 "现有观察清单" 作 prompt context 是用来**判 signal 映射的输入**，不是抽 fact 的输入。fact 应该完全来自 user 消息、不能从 reflection/persona 里"摘"（否则 LLM 可能把已有观察重复当作新 fact 写回，形成自循环）。
- 单次 call 承载两个职责 → prompt 结构混乱 + 容易让 LLM 在模糊指令下串味（把已有观察当新 fact、或者把新 fact 本体漏掉只输出 signal）。
- 职责拆开后各自 prompt 更清晰、各自 LLM tier 可独立选（Stage-1 更简单、Stage-2 需要更强推理）。

**新流程（都封在 `FactStore.aextract_facts_and_detect_signals()` 方法里）**：

```python
async def aextract_facts_and_detect_signals(
    self, lanlan_name: str, new_user_messages: list[str],
) -> tuple[list[dict], list[dict]]:
    """
    Returns: (new_facts, signals)
      new_facts: 新抽取的 fact 列表（写入 facts.json）
      signals: [{source_fact_id?, target_type, target_id,
                 signal: "reinforces" | "negates", reason}]
    """

    # Stage 1：从 user 消息抽新 fact（不带任何已有观察作 context，避免串味）
    new_facts = await self._allm_extract_facts(new_user_messages)
    # 输出：[{text, importance, entity}] —— 不再带 tags / reinforces / negates

    # Stage 2：把新 fact + 已有观察给 LLM，判 signal 映射
    if new_facts:
        existing_observations = await self._aload_signal_targets(lanlan_name)
        # existing_observations = [
        #   {id: "persona.master.p_001", text: "主人喜欢猫娘"},
        #   {id: "persona.master.p_088", text: "主人 23 岁"},
        #   {id: "reflection.r_023",     text: "主人最近在学日语"},
        #   ...
        # ]
        # 覆盖：confirmed + promoted reflection 全量 + 非 protected persona entry 全量
        signals = await self._allm_detect_signals(new_facts, existing_observations)
    else:
        signals = []

    return new_facts, signals
```

**Stage-2 的 input / output 契约**：

Input：
- `new_facts`: Stage-1 刚抽的新 fact 列表
- `existing_observations`: 当前系统里 LLM 可见的所有观察（id + text）

Output：
```json
{
  "signals": [
    {
      "source_fact_id": "fact_20260422_abc1",   // 新 fact id；可选（也可以是 null，表示纯 user 消息语义判断，不绑定具体新 fact）
      "target_type": "persona" | "reflection",
      "target_id": "p_001",
      "signal": "reinforces" | "negates",
      "reason": "新事实'主人爱猫娘'强化了已有观察'主人喜欢猫娘'"
    }
  ]
}
```

**dispatch 逻辑**：

```python
# memory_server.py 背景循环
new_facts, signals = await fact_store.aextract_facts_and_detect_signals(name, msgs)

# 把新 fact 写进 facts.json（沿用现行 dedup 逻辑）
await fact_store.apersist_new_facts(name, new_facts)

# 按 signals 列表 dispatch evidence 更新
for s in signals:
    # 本地防御：target_id 必须在 existing_observations 里存在（防 LLM 编造）
    if not _target_exists(s['target_id'], existing_observations):
        logger.warning(f"LLM 返回未知 target_id: {s['target_id']}，丢弃该 signal")
        continue

    delta = {'reinforcement': +1.0} if s['signal'] == 'reinforces' else {'disputation': +1.0}

    if s['target_type'] == 'persona':
        entity_key, entry_id = _parse_persona_target(s['target_id'])
        await persona_manager.aapply_signal(name, entity_key, entry_id, delta, source='user_fact')
    else:   # reflection
        await reflection_engine.aapply_signal(name, s['target_id'], delta, source='user_fact')
```

**成本估算**：

- Stage-1：~500 tokens in + ~200 tokens out（每次 idle 触发跑一批 user 消息）
- Stage-2：~500 tokens (new_facts) + ~2000 tokens (existing observations, 假设 100 条) + ~300 tokens out = 2800 tokens total
- 合计 ~3500 tokens/次，每小时 ~10 次 → 35K tokens/h/角色 → 日均 ~840K tokens/角色
- 按 Stage-1 = qwen-plus (¥0.4/M in, ¥1.2/M out)、Stage-2 = qwen-max (¥2.4/M in, ¥9.6/M out) 粗估 → 不到 ¥3/天/角色

（两个 Stage 的 LLM tier 分别对应 `EVIDENCE_EXTRACT_FACTS_MODEL_TIER` 和 `EVIDENCE_DETECT_SIGNALS_MODEL_TIER` —— 后者是新常量，见 §6.5 Gate 3 更新。）

**Stage-2 的 existing_observations 规模控制**：

如果 persona + reflection 很大（比如 500+ 条），一次性全塞会膨胀到 10K+ tokens。策略：
- 优先：**按 entity 过滤** —— 若 Stage-1 抽出来的 new_facts 都是 `entity=master` 的，那 Stage-2 只传 `entity=master` 的 existing_observations
- 若单 entity 仍太多：按 evidence_score DESC 取前 `EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS=200` 条（新常量）
- 丢弃的条目意味着 "这条 observation 不会在本轮被 reinforce/negate" —— 可接受（signal 是 best-effort，漏一次没关系）

**LLM 选型**：2 个 `*_MODEL_TIER` 常量（extract + detect_signals）→ **pending reviewer**（见 §6.5 Gate 3）。草案不定，PR 前 reviewer 给结论。

**§3.4.1 `ignored` 扣 0.2 的理由**：
- 原 task spec 里 `ignored` 是 no-op。reviewer 反馈：实际场景中"surface 多次不回应"是渐进的负面信号——用户大概率不关心 / 觉得无趣 / 觉得不准但懒得反驳。
- 扣 0.2 远小于 `denied` 的 -1.0（5 次 ignored = 1 次 denied）；语义：弱正面信号衰退。
- 扣在 reinforcement 侧（可负）而非 disputation 侧的理由（reviewer round-2 拍板保持此方案）：
  - disputation 语义是"用户**主动**否认"——ignored 达不到这个强度
  - reinforcement 减项更准确地表达"之前被正面提到过但最近被冷落"
  - reinforcement 可以为负，语义自洽（"该条目的正面累积被冷遇抵消殆尽"）
- 红线 1 合规性：`ignored` 判定来自 `check_feedback` 对 user 消息的 LLM 分析，属于 "explicit user input"；不是 AI 自作主张。

**§3.4.2 本地关键词 + LLM 语义判断的双层快速负面信号**：

**问题**：纯关键词命中直接 blanket disputation 会误伤。比如：
- `"别再说这个梗，我要笑死了"`——用户实际是在正面强化
- `"别提了，算了"`——可能是话题疲劳，也可能是敷衍认可
- `"换个话题吧"`——针对刚提的某件事，还是整体想换？

单靠关键词判不出。所以设计成**"关键词做快速触发、LLM 做精确 target 判定"**的双层结构。

**Layer 1 — 关键词扫描（本地、零成本、零延迟）**：

```python
# config/prompts_memory.py 旁路声明
NEGATIVE_KEYWORDS_I18N = {
    'zh': frozenset(["别再说", "别说了", "别提", "不想聊", "换个话题", ...]),
    'en': frozenset(["stop talking about", "don't mention", "change the topic", ...]),
    'ja': frozenset(["その話は", ...]),
    'ko': frozenset([...]),
    'ru': frozenset([...]),
}
```

扫描位置：在对话主路径（非背景循环），`/process` 处理 user 消息时的 hook。这是"快速触发"而非"信号本体"——关键词命中只是开关，触发 Layer 2。

**Layer 2 — LLM 语义判断（确定具体 target）**：

关键词命中后，立即（不等下个 idle 周期）触发一次小 LLM 调用 `check_negative_target`。prompt：

```
用户刚才的消息（最近 3 条）：
{user_messages}

当前系统正在维护的观察列表：
  [当前 surfaced reflection，feedback 还没落定的]
  - [r_023] 主人最近在学日语
  - [r_045] 主人下午打球扭伤了脚

  [最近 N 轮被 mention 的 persona fact]
  - [p_001] 主人喜欢猫娘
  - [p_088] 主人 23 岁

用户的消息里，"别提了 / 不想聊 / 换个话题" 这类表达到底指的是上述哪一条？
（可能多条，也可能一条都没有——用户只是泛化情绪）

返回 JSON：
{"targets": [{"target_type": "reflection"|"persona",
              "target_id": "...",
              "reason": "..."}]}
空数组表示无明确 target。
```

- LLM 返回空数组 → no-op。
- 返回非空列表 → 逐条 `disputation += 1.0`，source=`user_keyword_rebut`。

**LLM 选型**：`EVIDENCE_NEGATIVE_TARGET_MODEL_TIER` → **pending §6.5 Gate 3**。本路径延迟敏感（用户说"别提了"后应 <3s 响应），所以候选里偏向 fast tier（`emotion` → qwen-flash 量级），但是否值得提到 `summary` tier（qwen-plus 更准）由 reviewer 在 Gate 3 决定。成本估计：每次 prompt ~500 tokens + ~50 tokens 输出；关键词命中频率估计 <5 次/小时/角色 → 日均 <10K token/角色。

**5 语言关键词同步**：MEMORY.md `feedback_code_style.md` 要求。

**为什么不直接每轮用 LLM 分析 user 消息负面情绪**：关键词命中是确定性信号，代价为 0；每轮都过 LLM 会把负面检测从"稀疏触发"变成"密集触发"，成本翻十倍以上。关键词是 fast path，LLM 是精确层，两级协同。

**§3.4.3 信号抽取的执行时机（reviewer 关键反馈）**：

**不在对话主路径上每轮运行 extract_facts**——太贵。改为背景调度：

- 复用 `memory_server.py:680` `_periodic_idle_maintenance_loop` 的基础设施（空闲检测 + 按角色迭代）
- 但用**独立开关** `EVIDENCE_SIGNAL_CHECK_ENABLED`（不和现有 `ais_review_enabled()` 共享 toggle）
- 触发条件 OR：
  - 自上次 check 起累积 ≥ `EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS=10` 轮对话，或
  - 自最后一次用户消息起 ≥ `EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES=5` 分钟
- 每次 check 的范围：自上次 check 以来的新消息（游标存储在新文件 `memory/<char>/signal_cursor.json`，模式参考 `memory/cursors.py` 的 CursorStore）
- 错过窗口不补：如果 10 轮累积但系统忙，下次 idle 时再处理；累积量只增不减

**新常量**（进 `config/__init__.py`）：

```python
EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS = 10
EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES = 5
EVIDENCE_SIGNAL_CHECK_ENABLED = True  # 独立开关，可热关
```

**§3.4.4 `_texts_may_contradict` 该用在哪、不该用在哪**：

`memory/persona.py:697` 的 `_texts_may_contradict(old_text, new_text)` 是个**关键词重叠启发式**——CJK n-gram 切词后看两段文字共享词比例，超过 `SIMILARITY_THRESHOLD=0.6` 就认为"可能矛盾"。这是个粗糙的字面相似度检测，不是真正的语义判定。

它现在被用在两个地方：

1. **`PersonaManager.aadd_fact()` 插入新 entry 时**：检查新 fact 跟 persona 里现有的 entry 是否字面冲突。
   - 如果跟 character_card 来源的条目（`source='character_card'`）冲突 → 直接拒绝插入，返回 `FACT_REJECTED_CARD`。这就是"角色卡有最终决定权"——角色卡里写死的人设比新观察优先级高，新观察被拒就拒了。
   - 如果跟非角色卡条目冲突 → 写到 `persona_corrections.json` 排队，由 idle 背景循环里的 `resolve_corrections` 跑 LLM 复核到底哪条对、是否合并。
2. **历史上**还有些路径间接拿它的"判矛盾"结果做下游决策（比如反推"用户对该话题反感"之类）。

**本 RFC 的边界**：

- 用法 1 **保留**——这是 persona 内部一致性维护，不涉及 evidence。
- 用法 2 **禁止**。`_texts_may_contradict` 是 AI 在替用户判断"两段文字是否矛盾"，它的输出不是 user signal，**不能**用来推导 reinforcement / disputation 的变化。否则就违反红线 1（"evidence 只能由用户显式输入驱动"）。
- 代码层保留 `_texts_may_contradict` 函数本身，但调用方只允许 `aadd_fact()` / `_evaluate_fact_contradiction()`（`persona.py:581`）这一个调用链。其他文件 grep 到这个函数名都要审查：是不是想拿它做 evidence 推导？是的话不行。RFC 明示这个收窄。

**§3.4.5 两个独立 prompt 的设计**：

**Prompt 1：`FACT_EXTRACTION_PROMPT` 改造**（`config/prompts_memory.py:699`，5 语言 i18n dict 全部同步）

只做"从 user 消息抽新 fact"。**不**带已有观察作 context、**不**问 reinforces/negates、**不**问 tags（Q9：tags 字段保留但不再要求 LLM 填）。

示例 zh（保留现行 "======以上为对话======" 安全水印结构）：

```
从以下对话中提取关于 {LANLAN_NAME} 和 {MASTER_NAME} 的重要事实信息。

要求：
- 只提取重要且明确的事实（偏好、习惯、身份、关系动态等）
- 忽略闲聊、寒暄、模糊的内容
- 忽略AI幻觉、胡言乱语、无意义的编造内容
- 每条事实必须是一个独立的原子陈述
- importance 评分 1-10
- entity 标注为 "master" / "neko" / "relationship"

======以下为对话======
{CONVERSATION}
======以上为对话======

请以 JSON 数组格式返回，格式如下：
[
  {"text": "事实描述", "importance": 7, "entity": "master"},
  ...
]
```

**与现行版本的 diff**：
- 删 `tags` 字段（reviewer Q9：fact 也不打 tag，回头用 clustering）
- 删 `importance >= 5` 过滤要求（Q1：低 importance 的也存，消费侧过滤）
- 不加 reinforces/negates（这是 Prompt 2 的事）

---

**Prompt 2：新增 `SIGNAL_DETECTION_PROMPT`** —— 这是本 RFC 新增的 prompt，5 语言 i18n。

输入：Stage-1 抽出的新 fact + 当前已有观察清单。输出：每条新 fact 对哪些已有观察构成 reinforces / negates。

示例 zh：

```
你是一个 careful deduplication judge。给你一组新提取的事实，和一组系统已经记录过的观察，请判断每条新事实对已有观察的关系。

======新提取的事实======
[fact_001] 主人在准备 N2 考试
[fact_002] 主人今天买了新猫粮
======以上为新事实======

======已有观察（按 type.entity.id 索引）======
[persona.master.p_001] 主人喜欢猫娘
[persona.master.p_088] 主人 23 岁
[reflection.r_023] 主人最近在学日语
[reflection.r_045] 主人下午打球扭伤了脚
======以上为已有观察======

请对每条新事实判断：
- reinforces：是否加强了某条已有观察？返回 target_id 和理由
- negates：是否反驳了某条已有观察？返回 target_id 和理由
- 若都没有，对应字段返回空列表

target_id 必须来自上面"已有观察"区，不要凭空生成。

输出 JSON：
{
  "signals": [
    {"source_fact_id": "fact_001",
     "target_type": "reflection",
     "target_id": "r_023",
     "signal": "reinforces",
     "reason": "N2 是日语等级考试，与'学日语'强相关"},
    ...
  ]
}
```

（"你是一个 careful deduplication judge" 是安全水印，不可去掉。"======以上为新事实"/"======以上为已有观察" 同样是水印结构，请保留。）

**target_id 格式**：`persona.<entity_key>.<entry_id>` / `reflection.<reflection_id>`——双段式 namespace 防 ID 撞车。下游 dispatch 按前缀判派给 PersonaManager 或 ReflectionEngine。

**LLM 凭空生成 id 的防御**：parse 后每个 `target_id` 在本地 observations 集合里校验存在，不存在 → log warning + 丢弃该 signal。

**i18n 与水印**：5 语言 dict 都要包含一个安全水印字符串（详见 §7 全局约束）。本 prompt 用 `"你是一个 careful deduplication judge"`（zh） / `"careful deduplication judge."`（其它语言保留英文短语）作为统一水印。

---

**Prompt 3（§3.4.2 Layer 2）：`NEGATIVE_TARGET_CHECK_PROMPT`**

已在 §3.4.2 给出框架，5 语言 i18n 同样要求。水印用 `"你是一个情感分析专家"` 系列（zh / ja / ko / ru 都用本地化版本，en 用 `"sends some useful information"` 风格）—— 详见 §7 prompt 水印规则。

#### §3.5 Time decay + archiving（P-C, ~130 行）

**Read-time decay**（红线 5）：

```python
# memory/evidence.py —— 所有 EVIDENCE_* 常量 import 自 config/__init__.py
from config import (
    EVIDENCE_REIN_HALF_LIFE_DAYS,
    EVIDENCE_DISP_HALF_LIFE_DAYS,
)

def _age_days(last_signal_at: str | None, now: datetime) -> float:
    if not last_signal_at:
        return 0.0
    return (now - datetime.fromisoformat(last_signal_at)).total_seconds() / 86400

def effective_reinforcement(entry: dict, now: datetime) -> float:
    r = float(entry.get('reinforcement', 0.0))
    if r == 0.0:
        return r
    age = _age_days(entry.get('last_signal_at'), now)
    if age == 0.0:
        return r
    return r * (0.5 ** (age / EVIDENCE_REIN_HALF_LIFE_DAYS))

def effective_disputation(entry: dict, now: datetime) -> float:
    d = float(entry.get('disputation', 0.0))
    if d == 0.0:
        return d
    age = _age_days(entry.get('last_signal_at'), now)
    if age == 0.0:
        return d
    return d * (0.5 ** (age / EVIDENCE_DISP_HALF_LIFE_DAYS))

def evidence_score(entry: dict, now: datetime) -> float:
    if entry.get('protected'):
        return float('inf')   # protected 永不归档 / 永不被 score-trim 淘汰
    return effective_reinforcement(entry, now) - effective_disputation(entry, now)
```

纯函数，`memory/evidence.py` module-level，无锁、无事件——decay 是**计算**不是状态转移。

**两个半衰期的语义设计**（reviewer round-2 反馈）：

- `EVIDENCE_REIN_HALF_LIFE_DAYS = 30`：正面信号 1 个月衰到一半。典型情境：用户一段时间没聊猫娘 → 猫娘相关 persona 条目热度降低（但不消失），LLM 不会老拿冷话题强塞。
- `EVIDENCE_DISP_HALF_LIFE_DAYS = 180`：否认信号 6 个月衰到一半。半年前说过"不喜欢 X"，半年后如果再没强化，反对态度弱化到 1/2（但不消失）——给用户态度回转的可能。
- 两者都走 `last_signal_at` 同一时间轴。按"哪个信号最后一次到达"作为衰减起点。新的 signal（无论 rein 还是 disp）都重置 `last_signal_at`——相当于重新启动衰减时钟。

**归档触发**（reviewer 反馈：归档更积极 → 累计 sub-zero days 而非连续）：

```python
# 背景循环里每次扫：
def maybe_mark_sub_zero(entry: dict, now: datetime) -> bool:
    """返回 True 表示本次从非-sub-zero 转到 sub-zero 或累加一天。"""
    if entry.get('protected'):
        return False
    score = evidence_score(entry, now)
    if score < 0:
        # 每次扫见到 score<0 就 +1；一天扫多次不会多加（通过"上次增量日期"字段防抖）
        last_incr_date = entry.get('sub_zero_last_increment_date')
        today = now.date().isoformat()
        if last_incr_date != today:
            entry['sub_zero_days'] = int(entry.get('sub_zero_days', 0)) + 1
            entry['sub_zero_last_increment_date'] = today
            return True
    # score >= 0：**不清零**（累计，更积极归档）
    return False

# 归档判定：
if entry.get('sub_zero_days', 0) >= EVIDENCE_ARCHIVE_DAYS:
    archive(entry)
```

Field rename note：我用 `sub_zero_days`（累计整数）而非 `sub_zero_since`（ISO date）——因为"累计"语义需要整数计数器不是时间戳。RFC 明示这字段命名含义。

**防抖字段 `sub_zero_last_increment_date`**：防止背景循环一天跑多次重复 +1。简单的日期比较。

**归档分布式存储**（reviewer 要求）：

每次归档调用创建新分片文件：

```
memory/<character>/persona_archive/
  ├── 2026-04-22_a1b2c3d4.json    # 日期 + uuid8 后缀
  ├── 2026-04-22_e5f6g7h8.json
  ├── 2026-04-23_...
  └── ...

memory/<character>/reflection_archive/
  └── ...
```

每个分片最多 `_ARCHIVE_FILE_MAX_ENTRIES = 500` 条。超过就新开分片。

**迁移现有 `reflections_archive.json`**（`reflection.py:122` `_reflections_archive_path`）：
- 首次 P-C 启动：检测到旧 flat 文件 → 按 `archived_at` 日期拆分迁移到 `reflection_archive/` 目录
- 迁移失败则保留 flat 文件 fallback，RFC 明示

**Archive 事件**：
- 复用 `EVT_REFLECTION_STATE_CHANGED`（to='archived'）+ `EVT_PERSONA_FACT_ADDED`（target 文件是 archive 分片）
- **不**新增 `EVT_REFLECTION_ARCHIVED` / `EVT_PERSONA_ARCHIVED`；12+3 足够
- 归档路径追加字段 `archived_at`, `archive_shard_path`

**`protected=True` 豁免**：`evidence_score` 返回 `inf`，永不触发 `< 0` → 永不 sub_zero_days++ → 永不归档。

#### §3.6 Render budget（P-D, ~150 行）

**Budget 作用域 + 双预算**（reviewer 强调）：

```python
# memory/evidence.py
PERSONA_RENDER_TOKEN_BUDGET    = 2000   # 非-protected persona entries 总预算
REFLECTION_RENDER_TOKEN_BUDGET = 1000   # pending + confirmed reflections 总预算（数值待校准）
PERSONA_RENDER_ENCODING        = "o200k_base"
```

- `protected=True` 条目（character_card 来源的 persona entry）**不计**入任一预算、**永远全量渲染**
- persona 和 reflection 是独立的两个预算
- suppressed section 不计入预算（它是 AI-too-much 抑制机制的展示，体量小）

**渲染流程（简化版——去掉了之前 draft 的 LLM merge phase 和 cache）**：

```python
# memory/persona.py:1118 arender_persona_markdown
async def arender_persona_markdown(name: str, pending_refs, confirmed_refs) -> str:
    persona = await self.aensure_persona(name)
    now = datetime.now()

    # Phase 1：split
    protected_entries = []
    non_protected_by_entity = defaultdict(list)
    for entity_key, section in persona.items():
        for entry in section.get('facts', []):
            if entry.get('protected'):
                protected_entries.append((entity_key, entry))
            else:
                non_protected_by_entity[entity_key].append(entry)

    # Phase 2：score-trim per-section
    # persona：同 entity 内按 evidence_score DESC 累加 token，<= BUDGET
    # 跨 entity 预算是合并的（所有非-protected 共用 2000 token pool）
    trimmed_persona = await _score_trim_async(
        [(ek, e) for ek, entries in non_protected_by_entity.items() for e in entries],
        budget=PERSONA_RENDER_TOKEN_BUDGET, now=now,
    )

    # reflection：pending + confirmed 共用预算
    all_reflections = (pending_refs or []) + (confirmed_refs or [])
    trimmed_reflections = await _score_trim_async(
        all_reflections,
        budget=REFLECTION_RENDER_TOKEN_BUDGET, now=now,
    )

    # Phase 3：compose markdown（沿用现有 _compose_persona_markdown 结构）
    return _compose_markdown(protected_entries, trimmed_persona, trimmed_reflections, ...)


async def _score_trim_async(entries, budget: int, now: datetime) -> list:
    """按 evidence_score DESC 保留，累计 token <= budget。同 score 按 importance DESC tiebreak。"""
    entries_sorted = sorted(
        entries,
        key=lambda e: (evidence_score(e, now), e.get('importance', 0)),
        reverse=True,
    )
    kept = []
    total_tokens = 0
    for e in entries_sorted:
        t = await acount_tokens(e.get('text', ''))
        if total_tokens + t > budget:
            break   # 后续更低分直接截断
        kept.append(e)
        total_tokens += t
    return kept
```

**为什么没有 Phase 2 LLM 合并**（reviewer 关键反馈）：

- 合并不在 render 时发生——render 是无 LLM 的纯同步（事实上是 async 但无外部调用）。
- 合并**只在 reflection promote 时发生**（§3.9）。每次 promote 一次 LLM call，次数可控（~5/天/角色），成本线性。
- 背景循环不做 persona 合并（persona "平时不做纠错/合并"）。

**为什么没有 cache**：

- merge 发生后 target persona entry 的 text / evidence 直接落 persona.json（`persona.entry_updated` 事件驱动）——下次读已经是合并后态。
- render 路径是纯计算：排序 + 累加 token + 拼 markdown。所有代价 <100ms，不需要 cache。
- 如果 render 频次远超合并频次导致每次都重排——**可以**未来加 `_render_cache` 旁路，但 V1 不做（避免 cache invalidation bug）。

**批量 token 计数的性能**：

- N ≤ 100 条 entry，每条 ~2ms tokenize → 累计 200ms？
- 用 `asyncio.to_thread` 放线程池，不阻塞 event loop
- 优化空间：并行计数 `asyncio.gather(*[acount_tokens(e['text']) for e in entries])`——所有 tokenize 并行跑在线程池（tiktoken 释放 GIL，实际 CPU 并行）
- RFC 实现建议：**不**并行。rationale：tokenize 是纯 CPU，线程池 N 并行 vs 串行 CPU 总时间一样（GIL 释放后 CPU 层面仍排队）。只有 I/O 并发才赚。保持串行代码更简洁。

**失败模式**：

- `tiktoken` import 失败：单次 warn + heuristic fallback（§3.2）
- `acount_tokens` 抛异常：log.error + 该 entry 按 `len(text) // 2` 估算，不崩 render

#### §3.7 话题级回避机制 —— V1 不做

**V1 RFC 不实现任何"话题级"机制**。具体不做的：

- 不做按话题聚合 evidence 的逻辑
- 不引入"用户不想聊 X 话题"的自动生成 persona fact
- 不让任何字段往 fact 上打 tag —— `FACT_EXTRACTION_PROMPT` 不再要求 LLM 输出 `tags`，`facts.py:339` 现行的 `'tags': fact.get('tags', [])` 字段写空数组（schema 保留位但不消费）
- 不让 reflection / persona 携带 tags 字段

**为什么不做**：

让 LLM 在每条 fact 抽取时各自打 tag 是个根本结构问题：

- 同一话题被不同 fact 标成 `"猫娘"` / `"猫"` / `"cat"` / `"宠物"` 不一致，聚合不到一起
- "话题"是相对全局的概念，得在已有一批 fact / reflection / persona 之后做**全局组织**才有意义
- 这本质是聚类 / 知识图谱问题，可能要走 embedding 聚类或图数据库，超出本 RFC 范围

**未来怎么做**（不在 V1 范围内、独立讨论）：

- 等条目积累到一定数量后跑全局聚类（embedding 相似度 / 主题模型）
- 形成的 topic 有自己的 ID、独立 evidence 累积、独立"是否回避"判定
- 由那个 RFC 决定如何与本 RFC 的条目级 evidence 关联

#### §3.8 API surface（~80 行）

**新增 event-type 常量**（`event_log.py:51-70`）：
- `EVT_REFLECTION_EVIDENCE_UPDATED` / `EVT_PERSONA_EVIDENCE_UPDATED` / `EVT_PERSONA_ENTRY_UPDATED`
- 同步加进 `ALL_EVENT_TYPES` frozenset（`:64`）

**新增模块 `memory/evidence.py`**：
- 常量区（见 §6 总表）
- 纯函数：`effective_reinforcement`, `effective_disputation`, `evidence_score`, `derive_status`
- 背景任务辅助：`maybe_mark_sub_zero`（`_topic_avoid_sweep` 暂缓 —— Q7 pending）

**新增模块 `utils/tokenize.py`**：
- `count_tokens(text, encoding=PERSONA_RENDER_ENCODING) -> int`（sync）
- `acount_tokens(text, encoding=...) -> int`（async via `asyncio.to_thread`）
- 模块级 `_ENCODERS: dict[str, tiktoken.Encoding]` lazy cache
- `_count_tokens_heuristic(text) -> int`（fallback）
- 首次 fallback 触发时 `logger.warning` 一条明示 "降级到启发式计数" + 推荐装 tiktoken

**新增方法（PersonaManager / ReflectionEngine，sync + async 对偶）**：

方案 A 下信号来源直接带 `target_id`，不需要 `by_text_hint` fuzzy match 变体；API 更简洁。

```python
# ReflectionEngine
def apply_signal(self, name, reflection_id, delta, source) -> None
async def aapply_signal(self, name, reflection_id, delta, source) -> None
#   delta: {"reinforcement": ±float, "disputation": ±float}
#   内部走 record_and_save → EVT_REFLECTION_EVIDENCE_UPDATED

# PersonaManager
def apply_signal(self, name, entity_key, entry_id, delta, source) -> None
async def aapply_signal(self, name, entity_key, entry_id, delta, source) -> None
#   内部走 record_and_save → EVT_PERSONA_EVIDENCE_UPDATED

async def amerge_into(self, name, target_entry_id, merged_text, merged_evidence, source_reflection_id) -> None   # §3.9
#   内部发 EVT_PERSONA_ENTRY_UPDATED（text 改写） + EVT_PERSONA_EVIDENCE_UPDATED（合并 evidence）

# Topic-avoid 相关方法（aensure_avoid_fact / aremove_avoid_fact）→ Q7 pending，V1 不实现

# ReflectionEngine
async def _apromote_with_merge(self, name, reflection) -> str  # §3.9，返回 action
```

每个 async method 内部走 `_get_alock(name)` + `arecord_and_save`。sync twin 给测试 / 迁移脚本用，生产都走 async。

**`memory_server` 启动期新增**：

```python
# reconciler 三个 handler 注册（P2.b 接入点，本 RFC 一并补）
reconciler.register(EVT_REFLECTION_EVIDENCE_UPDATED, _apply_reflection_evidence)
reconciler.register(EVT_PERSONA_EVIDENCE_UPDATED, _apply_persona_evidence)
reconciler.register(EVT_PERSONA_ENTRY_UPDATED, _apply_persona_entry)

# 新背景循环（或合并到现有 _periodic_idle_maintenance_loop 的子任务列表）
_spawn_background_task(_periodic_signal_extraction_loop())    # §3.4.3
_spawn_background_task(_periodic_archive_sweep_loop())        # §3.5（Q7 topic sweep 暂缓）
```

**依赖**：`pyproject.toml` `[project.dependencies]` 加 `tiktoken>=0.7.0`。`requirements.txt` `uv export` 重新生成。

#### §3.9 Merge-on-promote（~150 行）

**触发**：`ReflectionEngine` 检测到 reflection R 的 `evidence_score(R) >= EVIDENCE_PROMOTED_THRESHOLD` 且 `R.status == 'confirmed'`（红线 2：score 穿阈值触发，非时间触发）。

**流程**：

```python
async def _apromote_with_merge(self, name: str, R: dict) -> str:
    async with self._get_alock(name):   # ReflectionEngine._alocks
        # Load candidates
        persona = await self._persona_manager.aget_persona(name)
        same_entity_persona = [
            (ek, e) for ek, section in persona.items()
            if isinstance(section, dict)
            for e in section.get('facts', [])
            if ek == R.get('entity') and not e.get('protected')
        ]
        same_entity_reflections = [
            r for r in await self.aload_reflections(name)
            if r.get('entity') == R.get('entity')
            and r.get('status') in ('confirmed', 'promoted')
            and r['id'] != R['id']
        ]

        # LLM call
        merge_decision = await self._llm_call_promotion_merge(
            R=R,
            persona_pool=same_entity_persona,
            reflection_pool=same_entity_reflections,
        )
        # merge_decision = {"action": "...", ...}

        action = merge_decision.get('action')

        if action == 'promote_fresh' or action is None or merge_decision_is_invalid:
            # Fresh promote via existing path
            result = await self._persona_manager.aadd_fact(
                name, R['text'], entity=R.get('entity'),
                source='reflection', source_id=R['id'],
            )
            if result in (FACT_ADDED, FACT_ALREADY_PRESENT):
                await self._arecord_state_change(name, R['id'], 'confirmed', 'promoted')
                return 'promote_fresh'
            else:
                # FACT_REJECTED_CARD 或 FACT_QUEUED_CORRECTION
                await self._arecord_state_change(name, R['id'], 'confirmed', 'denied',
                    reason='rejected_by_persona_add')
                return 'reject_by_persona'

        elif action == 'merge_into':
            target_id = merge_decision['target_id']
            merged_text = merge_decision['merged_text']
            # LLM 可建议 merged_evidence_delta，但我们用保守规则：
            # merged_reinforcement = max(target.rein, R.rein)
            # merged_disputation   = max(target.disp, R.disp)
            # 理由：合并不应让两个条目 evidence 相加（用户只投过 1 次，不该因合并变 2 次）

            await self._persona_manager.amerge_into(
                name, target_id, merged_text, source_reflection_id=R['id'],
            )   # 内部发 EVT_PERSONA_ENTRY_UPDATED + EVT_PERSONA_EVIDENCE_UPDATED
            await self._arecord_state_change(name, R['id'], 'confirmed', 'merged',
                absorbed_into=target_id)
            return 'merge_into'

        elif action == 'reject':
            await self._arecord_state_change(name, R['id'], 'confirmed', 'denied',
                reason='llm_merge_rejected', reject_explanation=merge_decision.get('reason'))
            return 'reject'
```

**为什么必须过 LLM 而非 keyword overlap**：
- 语义相关但字面不同的合并（"主人喜欢咖啡" vs "主人早上不喝茶只喝咖啡"）启发式判不出
- 字面相似但语义相反的歧义（"主人喜欢安静" vs "主人喜欢吵闹"）启发式会误合
- persona 是 human-readable 文本，合并质量直接影响体感；LLM 合理

**成本**：每次 promotion 触发 1 次 LLM call（~500 tokens in + ~100 tokens out）。promotion 频次 <5/天/角色。日均 <5K token，可忽略。

**LLM prompt 结构**（新增到 `config/prompts_memory.py`，i18n 5 语言）：

```
你在维护{ai_name}对{master_name}的长期印象。当前有一条待晋升的观察：
  R: "{R.text}"
  R.evidence_score: {R.score}

以下是{ai_name}对{master_name}的现有印象池（已 promoted 的 persona fact + 其它 confirmed 的 reflection）：
  [id=p1] "{persona_1.text}" score={...}
  [id=p2] "{persona_2.text}" score={...}
  [id=r3] "{reflection_3.text}" score={...}
  ...

请判断 R 应该：
- promote_fresh：作为新 persona fact 独立收录（和现有任何条目都不重复、不矛盾）
- merge_into：和某条现有条目语义相近，应合并。返回 target_id 和合并后的文本。
- reject：和现有某条明确矛盾且 R 证据弱于对方，不应收录。返回 reason。

返回 JSON：
{"action": "promote_fresh" | "merge_into" | "reject",
 "target_id": "..." (merge_into 时必填),
 "merged_text": "..." (merge_into 时必填),
 "reason": "..." (reject 时必填)}
```

**幂等性**：LLM call 本身不幂等（两次可能不同输出）。但 `_apromote_with_merge` 的入口条件是 `R.status == 'confirmed'`——一旦本次调用执行到 `_arecord_state_change` 让 R 跳到 `promoted` / `merged` / `denied`，下次调用就短路。崩溃恢复：如果 LLM 返回后 emit state_changed 前崩了，重启后 R 还在 confirmed、evidence 还 >= threshold，重跑 `_apromote_with_merge`——LLM 可能给出不同结论，但幂等性不靠 LLM，靠 state 一致性（每步事件都是 `record_and_save`，要么全成功要么无变化）。

**降级**（LLM 超时 / 非法 JSON）：**不**默认 promote_fresh。原因：默认 fresh 在 LLM 服务长期挂掉时会静默累积重复 persona entry，破坏 dedup 语义。

正确降级：reflection 保持在 `confirmed` 状态，本次 promote 操作 **跳过**。下次背景循环再次检测到 `score >= EVIDENCE_PROMOTED_THRESHOLD` 时重试 `_apromote_with_merge`。

为防止反复 LLM 失败的 reflection 把背景循环跑爆，加节流：

- reflection schema 加 `last_promote_attempt_at` 字段（已有 evidence 字段之外）
- 每次 `_apromote_with_merge` 开头，若 `last_promote_attempt_at` 在 `EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES` 内（草案 30 分钟，列入 §6.5 Gate 4 共评） → 跳过本轮
- 连续 N 次失败（`EVIDENCE_PROMOTE_MAX_RETRIES = 5`，pre-merge gate）→ reflection 标 `status='promote_blocked'`、`promote_blocked_reason='llm_unavailable'`，进入死信状态，需人工或后续运维介入；下次 LLM 恢复后用户消息再触发新 signal 才会重置

这样保证：(a) LLM 短暂失败不丢 promote，会自动重试；(b) LLM 长期失败不会让某条 reflection 反复消耗 LLM 调用配额；(c) 不静默生成重复 persona entry。

不引入新事件类型——`status='promote_blocked'` 走现有 `EVT_REFLECTION_STATE_CHANGED` 事件即可。

#### §3.10 Analytics / funnel（~80 行）

reviewer 反馈："reflection 变成 promoted persona 的 funnel 可以在这个 PR 里讨论。"

**定义**：funnel = 时间窗口 W 内，各状态转换的计数。数据源是 `events.ndjson`——本身就是审计日志，自带 "发生了什么" 的有序记录。

**核心事件序列**（event log 里可见的）：

```
fact.added ──▶ reflection.synthesized ──▶ reflection.evidence_updated (累积)
                                      ──▶ reflection.state_changed: confirmed
                                      ──▶ reflection.evidence_updated (继续)
                                      ──▶ reflection.state_changed: promoted/merged/denied
                                            │
            promoted ──▶ persona.fact_added (新条目)
            merged   ──▶ persona.entry_updated (合入已有)
                                      ──▶ persona.evidence_updated (持续)
                                      ──▶ ... 归档：persona.fact_added (archived shard)
```

**V1 API**（`memory/evidence.py` analytics 模块）：

```python
def funnel_counts(lanlan_name: str, since: datetime, until: datetime) -> dict:
    """线性扫 events.ndjson，聚合各类型计数。"""
    return {
        "facts_added": N,
        "reflections_synthesized": N,
        "reflections_confirmed": N,      # state_changed to=confirmed
        "reflections_promoted": N,       # state_changed to=promoted
        "reflections_merged": N,         # state_changed to=merged
        "reflections_denied": N,         # state_changed to=denied
        "reflections_archived": N,       # state_changed to=archived
        "persona_entries_added": N,      # persona.fact_added
        "persona_entries_rewritten": N,  # persona.entry_updated
        "persona_entries_archived": N,   # persona.fact_added 且 path 是 archive shard
    }
```

扫开销：events.ndjson 上限 10K 行（compact 阈值），全扫 <200ms。对偶发 `/analytics` API 调用够快。

**V1 不做**：CLI 工具、UI 报表、定时报告推送。V1 只把 aggregation API 落地，给未来 UI / 脚本复用。

**为什么在本 RFC 讨论**：funnel 的形状依赖 §3.9 的事件设计（`merged` 作为独立 state_changed 值、`persona.entry_updated` 作为独立事件）。不在一个 RFC 里定会出现"funnel 想要 X 但事件里没 X"的锁死。

#### §3.11 全系统可溯源（~70 行）

**承诺**：从硬盘上看，**所有 fact / reflection / persona entry 的来源链永久保留**。哪怕一条已经 merge 进别人、archive 到分片、或被排除出主路径检索，它的原始 ID + source 链 + 全部历史事件都还在。

**链路**（自上而下）：

```
persona entry
  ├── source_reflection_id  → reflection (那条 promote 进来的)
  │     └── source_fact_ids → [fact, fact, fact, ...] (合成时的 source）
  │
  ├── （或）source='manual'   → 用户/脚本手动插入，无上游
  ├── （或）source='character_card' → 角色卡同步进来
  └── （若被 merge 改写）merged_from_ids → [reflection_id, ...] (LLM merge 决策吸收的源)
```

**字段保留承诺**（每个 entry 永远不删的字段）：

- `id`：UUID 形态，永不复用、永不重写。一旦分配，溯源就靠它。
- `source` + `source_id`（persona entry 现有 schema）：来源类型 + 上游 ID
- `source_fact_ids`（reflection 现有 schema）：合成它的那批 fact ID
- `absorbed_into`（reflection 新增字段）：被 merge 时填 target_id；溯源链路另一端
- `merged_from_ids`（persona entry 由 merge 改写时新增字段）：吸收哪些 reflection
- 事件日志 `events.ndjson`：所有 evidence_updated / state_changed / fact_added / entry_updated 全程 append-only，永不删行（compaction 时也只 truncate 给定时间窗之前的、且会保留 snapshot 起点）

**"被隐藏" vs "被删除"**：

- **archive**：entry 从主 view 文件（`reflections.json` / `persona.json`）移到分片归档文件 `*_archive/<date>_<uuid8>.json`。主路径不再加载、render 不再涉及、aget_followup_topics 不再选——但**文件还在硬盘**。日后如果做"主动检索"功能（用户手动搜历史），可以读分片找回。
- **merged（reflection）**：原 reflection status 从 `confirmed` 跳到 `merged`，仍然在 `reflections.json` 里（不归档），但带 `absorbed_into=target_persona_entry_id` 标记。后续主动检索 / proactive chat / aget_followup_topics 一律按 `status in ('pending', 'confirmed')` 过滤，不会再选到它。
- **rewritten（persona entry）**：被 LLM 决定合并、target text 被改写。原 text 从 view 上不见，但事件日志里 `EVT_PERSONA_ENTRY_UPDATED` 的 payload 留有 `merged_from_ids`，循着这个 ID 反查可拿到被合的 reflection 的原文。

**主动检索过滤建议（非本 RFC 强制，给后续 retrieval 功能提示）**：

```python
def _is_visible_for_proactive_retrieval(entry: dict) -> bool:
    if entry.get('protected'):
        return True
    if entry.get('status') in ('archived', 'merged', 'denied', 'promote_blocked'):
        return False
    if evidence_score(entry, now) < 0:
        return False
    return True
```

后续如果做检索，按这个过滤即可——所有"被隐藏"条目都不会浮上来，但 ID 和事件日志全在。

**RFC 明示**：本 RFC 实现的 evidence 机制**不**直接做"主动检索"，但所有数据/事件都为它预留出溯源支持。

### §4 Implementation plan（~180 行）

**4 个 PR，按依赖顺序**：

**PR-1 (P-A + P-B)**：signal detection + data layer + migration
- `pyproject.toml` 加 `tiktoken>=0.7.0`（为 P-D 提前挂，P-A 不需要）；`requirements.txt` 由 `uv export` 重新生成
- `config/__init__.py` 加全部 evidence 常量 + 加进 `__all__`
- `config/prompts_memory.py` 改造 `FACT_EXTRACTION_PROMPT`（5 语言；删 tags 要求；不加 reinforces/negates）；新增 `SIGNAL_DETECTION_PROMPT`（5 语言 i18n，带水印）；新增 `NEGATIVE_TARGET_CHECK_PROMPT`（5 语言 i18n，带水印）；新增 `NEGATIVE_KEYWORDS_I18N`（5 语言 frozenset）
- `memory/evidence.py` 新建（**只放纯函数 + 背景辅助**：score 公式 / derive_status / effective_reinforcement / effective_disputation / maybe_mark_sub_zero；不放常量）
- `memory/facts.py` 重写 `aextract_facts` 为 `aextract_facts_and_detect_signals`（两次 LLM call）；删 `importance < 5` 硬丢
- `memory/reflection.py` 新增 `aapply_signal`（async only）；`check_feedback` 新增 `ignored → reinforcement -= 0.2`；reflection schema 加 4 字段（reinforcement / disputation / last_signal_at / sub_zero_days）；**删除** `AUTO_CONFIRM_DAYS` / `AUTO_PROMOTE_DAYS` + 时间跳级分支
- `memory/persona.py` 新增 `aapply_signal`（async only）；entry schema 加 4 字段（`_normalize_entry` defaults）；`_texts_may_contradict` 调用链收窄到 `aadd_fact` 一处
- `memory_server.py` 注册 3 个 reconciler handler；新增 `_periodic_signal_extraction_loop`；新增对话主路径上的 negative keyword hook；迁移 seed 启动期一次性触发
- 测试：背景循环并发 60s 不死锁、不丢 fsync；migration 数值（新公式下每种旧 status 的 score 映射精确）；新增 evidence event 重放幂等 (10 次)；Stage-1/Stage-2 LLM 失败降级（不丢已成的 facts.json 写入）

**PR-2 (P-C + 归档分布式存储)**：
- `memory/evidence.py` 补 `evidence_score` 归档判定 + `maybe_mark_sub_zero`
- `memory/reflection.py` + `memory/persona.py` 新增 `sub_zero_days` 字段；归档路径改分片存储
- `memory_server.py` 新增 `_periodic_archive_and_topic_loop`（合 §3.5 + §3.7 sweep，共享 loop）
- 测试：decay 数值快照；归档累计 vs 连续（反复抖动不阻塞归档）；`protected=True` 豁免；分片文件大小上限；旧 `reflections_archive.json` 迁移

**PR-3 (P-D + §3.9 merge-on-promote)**：
- `utils/tokenize.py` 新建（sync + async + fallback + 一次性 warn）
- `config/prompts_memory.py` 新增 `PROMOTION_MERGE_PROMPT`（5 语言 i18n）
- `memory/persona.py` `_compose_persona_markdown` 改写为 Phase 1/2/3；新增 `amerge_into`
- `memory/reflection.py` 新增 `_apromote_with_merge`；替换现有 promote 调用
- 测试：Nuitka 打包产物 token 计数真跑（o200k_base encoding 被打入）；merge LLM 超时**不**降级 promote_fresh，reflection 留在 confirmed 等下次 retry；score-trim 保留 `protected`；persona / reflection 预算独立（一侧超不影响另一侧）

**PR-4 (§3.10 analytics only)**：
- `memory/evidence.py` 补 `funnel_counts`（从 events.ndjson 扫）
- （P-E topic projection 整体 Q7 pending，本 PR 不含；待 Q7 重新设计后另开 PR）
- 测试：funnel 数值对账（人工构造事件序列对比输出）

**跨阶段 tests 通用要求**：
- 每个新 `_record_and_save` 调用点都有 crash-between 测试
- 并发 `/process` + 各背景循环 60s 不死锁
- 兼容现有测试套件（`pytest tests/unit -q` + `ruff` + `scripts/check_async_blocking.py`——沿用 #905 gate）

### §5 Migration（~100 行）

完整迁移表 + 每条 math 验证 + 崩溃半程恢复策略 + 分片归档文件初始化。见本 plan §3.1 的迁移 seeds 表 + §3.5 的 reflections_archive 迁移逻辑。RFC 正文把它们展开写。

**关键点**：
- 迁移 seeds 走 `EVT_*_EVIDENCE_UPDATED` + `source='migration_seed'`，reconciler handler 见到该 source 走 overwrite-once（不累加，防重跑翻倍）
- `__migration_marker__` 假 entry_id 标记迁移结束（首次 startup 写，后续 startup 见到就跳过迁移）
- crash 半程：reconciler 从 sentinel 重放未完成的 seed 事件；每条 seed 独立幂等

### §6 Open questions（~100 行）

这些是"数值可后续调"的迭代型问题，**不阻塞 PR merge**：

1. **话题级聚合机制完整缺位**：V1 没有"话题"这个抽象。如果未来需要"用户不想聊某类话题"的行为，得做全局聚类（embedding / 图数据库）。关键问题：什么时候触发全局组织？话题状态怎么持久化？怎么和条目级 evidence 关联？本 RFC 不答，独立 RFC 处理。
2. **`EVIDENCE_CONFIRMED_THRESHOLD=1.0 / EVIDENCE_PROMOTED_THRESHOLD=2.0`**：reviewer 认可方向后留的初值；实践后可调（如要求 3 次确认再 promote）
3. **pending 的下界**：当前 `score >= ARCHIVE_THR && score < CONFIRMED_THR` 都算 pending。问题：一个未受过任何 signal 的新 reflection（score=0）会不会在 `aget_followup_topics` 里被当候选？若是，一次 ignored 就 score=-0.2 掉出，状态来回抖动是否符合预期？
4. **`EVIDENCE_ARCHIVE_DAYS=14`**：累计 sub-zero 天数阈值；reviewer "归档更积极" 的定量表达
5. **`IGNORED_REINFORCEMENT_DELTA=-0.2`**：经验值；若 ignored 频繁、老条目过度削弱，考虑 -0.1
6. **`PERSONA_RENDER_TOKEN_BUDGET=2000 / REFLECTION_RENDER_TOKEN_BUDGET=1000`**：基于 context window 推算；Nuitka 打包产物实测调
7. **方案 A prompt 的 context 规模**：目前估 100 条 entry → ~2000 tokens 带入 extract_facts prompt；如果 persona 规模增长到 500+ 条，prompt 会膨胀到 ~10K tokens。要不要只带最近活跃条目、或按 entity 分片调用？
8. **`persona.entry_updated` 事件的 text 如何在 log 里重放**：方案 B（只带 sha256，view save 若丢则 reconciler 无法重建）vs 方案 A（payload 放 plaintext 违反红线 4）。V1 倾向 B，但需要确认"view save 失败"的 incident rate 够低

### §6.5 Pre-merge reviewer gates（~120 行）

> **这些决定 reviewer 要在对应实现 PR merge 之前敲定**，不能以"开放问题"的方式放过。数值/选型直接进 `config/__init__.py`，改值会产生实际 behavior 变化，所以需要 reviewer 显式 sign-off 而不是默认接受草案值。**不是 out-of-scope**——必须在 V1 实现 PR 合入前给出结论。

**Gate 1：衰减半衰期**（PR-2 P-C 前敲定）

- `EVIDENCE_REIN_HALF_LIFE_DAYS = 30`（草案）——reinforcement 衰到一半的天数
- `EVIDENCE_DISP_HALF_LIFE_DAYS = 180`（草案）——disputation 衰到一半的天数

选型权衡：
- 如果 rein half-life 太短 → 用户一段没聊的喜好会很快"冷下来"，猫娘忘事快；太长 → 旧喜好一直扣着 token 预算
- 如果 disp half-life 太短 → 用户半年前说过"不喜欢 X"，半年后过期，如果新 signal 到了又会立刻正面化（可能和用户期望不符）；太长 → 用户态度转变无法体现
- 两者比值（180/30=6）反映 "否认比肯定持久"的语义强度

候选：
- (30d, 180d) 当前草案
- (14d, 90d) 更快新陈代谢，适合短期关系
- (60d, 365d) 更慢，旧关系不易忘
- `DISP=∞` 不衰减（task spec 原始方案，reviewer round-2 已明确否决）

**需 reviewer 选**：**(30, 180)** 默认 / 需要调整 / 需要做 ablation 实测再定。

**Gate 2：Reflection 合成 context 量**（PR-1 P-A 前敲定）

- `REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_COUNT = 10`（草案）——prompt 里带多少条已 absorbed fact 作上下文
- `REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_DAYS = 14`（草案）——只带最近 N 天内已 absorbed 的 fact

选型权衡：
- 太少 → LLM 不知道哪些已合成过，重复合成风险
- 太多 → prompt 膨胀、token 成本上升、LLM 可能因注意力稀释而误判

候选：
- (10, 14) 当前草案
- (20, 30) 更保守，防重复合成更严，成本 +1 倍
- (5, 7) 更激进，可能漏检已 absorbed 语义重复

**需 reviewer 选**：**(10, 14)** 默认 / 调整 / 需要跑几组真实对话测分布再定。

**Gate 3：3 个 LLM tier 选型**（各 PR 前敲定）

这一组决定实现成本 + 质量权衡，**reviewer round-2 明确 "LLM 选型这个事儿先不要弄"**——所以只列候选和边界，不提默认：

| Tier 常量 | 用途 | 调用频次 | 延迟敏感 | 推荐候选 |
|---|---|---|---|---|
| `EVIDENCE_EXTRACT_FACTS_MODEL_TIER` | Stage-1 抽新 fact（不带已有观察作 context） | ~10 次/h/角色（idle 触发） | 不敏感（背景） | `"summary"` (qwen-plus) / `"correction"` (qwen-max) |
| `EVIDENCE_DETECT_SIGNALS_MODEL_TIER` | Stage-2 判 signal 映射（带 new_facts + 已有观察） | ~10 次/h/角色（紧跟 Stage-1） | 不敏感（背景） | `"correction"` (qwen-max) / `"summary"` (qwen-plus) |
| `EVIDENCE_NEGATIVE_TARGET_MODEL_TIER` | 关键词命中后快速判断 target（§3.4.2 Layer 2） | 稀疏（<5 次/h/角色） | **敏感**（<3s 给用户反应） | `"emotion"` (qwen-flash) / `"summary"` (qwen-plus) |
| `EVIDENCE_PROMOTION_MERGE_MODEL_TIER` | reflection promote 时 LLM 决定合并/独立/拒收（§3.9） | ~5 次/天/角色 | 不敏感（背景） | `"correction"` (qwen-max) / `"conversation"` (qwen-max) |

`config/__init__.py` 已有的 model tier 名字（2026-04 快照）：
- `"conversation"` → `DEFAULT_CONVERSATION_MODEL = 'qwen-max'`
- `"summary"` → `DEFAULT_SUMMARY_MODEL = 'qwen-plus'`
- `"correction"` → `DEFAULT_CORRECTION_MODEL = 'qwen-max'`
- `"emotion"` → `DEFAULT_EMOTION_MODEL = 'qwen-flash'`
- `"vision"` → `DEFAULT_VISION_MODEL = 'qwen3-vl-plus'`（与本 RFC 无关）

成本对比（按 qwen 当前定价粗估，输入 token /M）：
- qwen-flash: ¥0.1
- qwen-plus: ¥0.4
- qwen-max: ¥2.4
（qwen-max 是 qwen-flash 的 ~24 倍）

**需 reviewer 选**：4 个 tier 分别选一个（其中 EXTRACT_FACTS / DETECT_SIGNALS 是 Stage-1/2 的两次 LLM call，可不同 tier）。可以统一用同一 tier，也可以分开。选型影响总 token 成本 + 输出质量 + 每次响应延迟。

**Gate 4（或许也需要）：归档分片策略**（PR-2 前）

- `ARCHIVE_FILE_MAX_ENTRIES = 500`（草案）——每个 `*_archive/<date>_<uuid8>.json` 文件最多多少条

候选：
- 500 草案
- 100 分片更细，文件数多但单文件小
- 1000 分片更粗，单文件大但文件数少

**需 reviewer 选**：500 默认 / 调整。（这个相对低风险，实现时可 fallback 给默认，reviewer 如果没意见就放过）

---

**实现 workflow**：各 PR 开工前、Draft PR 已开、实际 commit 落地前这个窗口，reviewer 在 PR thread 或本 plan 评论里给出选型结论，更新 `config/__init__.py`。Gate 未敲定的话，实现作者用草案值继续，但 PR description 里高亮 "Pending Gate N" 提醒 reviewer 必须在 merge 前给结论。
8. **`persona.entry_updated` 的 text 如何在 event log 里重放**（§3.3 方案 B 的具体实现细节）：若 view 侧 text 保存失败、event 成功，reconciler 怎么报错？终止启动 vs 跳过继续？
9. **背景循环合并 vs 拆分**：`_periodic_archive_and_topic_loop` 把归档 + topic sweep 合一个 loop。若其中一步慢会阻塞另一步——split 成两个独立 loop？V1 先合一。

### §7 Explicit rejects（~100 行）

- Plaintext in event payloads（红线 4）
- Time-based auto-promotion（红线 2）
- Cross-character evidence
- ML-ranked persona
- **Per-entry UI 编辑按钮**（reviewer 强调：记忆系统需要隐私感，压根不做，不是 deferred）
- `recent_mentions` 作为 evidence 输入源
- `_texts_may_contradict` 作为 user feedback 判定（红线 1）
- 写时 decay（红线 5）
- Render-time LLM 合并（合并只在 promote 时发生）
- Render cache（没有 merge phase 就不需要 cache）
- 基于 fact.tags 的 topic 聚合 / 自动生成 "用户不想聊 X" persona fact / 任何"话题级"机制 (V1 不实现)

### §8 Success criteria（~80 行）

只列**本 RFC 新增功能**的验收标准。基础设施层面的崩溃恢复 / event log 重放 / 锁并发安全 已经由依赖项保证（详见 §3.3 已有的 record_and_save 合约说明），不在本 RFC 重新验。

数值与公式正确性：

- **S1**：read-time decay 数学正确——给定 `(reinforcement, disputation, last_signal_at)` 三元组与 `now`，`evidence_score()` 输出符合 §3.1 公式（snapshot 测试覆盖 (0d / 30d / 60d / 180d / 365d) × (rein 衰减 / disp 衰减) 的 10 个组合）
- **S2**：派生状态映射正确——给定 score，`derive_status()` 返回值符合 §3.1 阈值表
- **S3**：迁移 seed 数值——每种旧 status (`pending` / `confirmed` / `promoted` / `denied`) 走完迁移后，view 里的 (rein, disp) = 表中预期值；evidence_score 落对应 tier

事件 + view 联动：

- **S4**：3 个新 evidence event 各自的 reconciler handler 重放 10 次后 view 字段一致（snapshot pattern 自然幂等）
- **S5**：`persona.entry_updated` 的 sha256 校验逻辑——重放时 view text hash 匹配则 no-op；不匹配则 reconciler raise（按设计停启动等人查），不静默通过

两次 LLM 调用的拆分：

- **S6**：Stage-1 `aextract_facts_only` 输入纯 user 消息，输出新 fact，**不**包含来自已有 reflection/persona 的 text（防"摘已有观察当新 fact"回归测）
- **S7**：Stage-2 `adetect_signals` 返回的 `target_id` 100% 在传入的 `existing_observations` 集合里（编造 ID 必须被防御性 drop）
- **S8**：Stage-1 失败 → Stage-2 不跑、新 fact 也不写；Stage-2 失败 → 新 fact 已写入但 signal 全 drop（不阻塞 fact 写入）

负面关键词链路：

- **S9**：关键词命中后 LLM target check 失败 → 不加 disputation、不崩溃，next-of-kin loop 重试
- **S10**：关键词命中后 LLM 返回空 targets → no-op，不盲加 disputation 到 surfaced 任意一条

Render budget：

- **S11**：persona 和 reflection 预算独立——一侧条目超 budget 不影响另一侧
- **S12**：`protected=True` 条目永远渲染 + 永远不计入 token 预算（不论 budget 多紧张）
- **S13**：Nuitka 打包产物启动后 `count_tokens('测试')` 用 tiktoken 真路径（不触发 heuristic fallback warning）

Merge-on-promote：

- **S14**：LLM 失败 → reflection 留 confirmed 不变、不静默 promote_fresh；下次 cycle retry；连续失败到 `EVIDENCE_PROMOTE_MAX_RETRIES` → status='promote_blocked'
- **S15**：LLM 决定 merge_into → target persona entry 的 text 和 evidence 都被改写、原 reflection status='merged' + absorbed_into=target_id（溯源链完整）
- **S16**：merge 后该 reflection 不再被 `aget_followup_topics` 选中（status 过滤生效）
- **S17**：merge 后被 absorb 的 reflection 仍在 reflections.json（不删），可循 absorbed_into 反查

归档：

- **S18**：`sub_zero_days` 累计而非连续——score 抖动到正再回负，计数器不清零（"积极归档"）
- **S19**：归档分片文件按日期 + uuid8 命名、每分片 ≤ `ARCHIVE_FILE_MAX_ENTRIES`、超过自动新分片
- **S20**：归档后主 view 文件不再含该条目，但分片文件可读到完整 entry（包括所有 source/audit 字段）

可溯源（§3.11）：

- **S21**：persona entry 的 source_reflection_id 永远指向有效的 reflection ID（即使该 reflection 已 archived 到分片）
- **S22**：reflection 的 source_fact_ids 永远指向有效的 fact ID（即使该 fact 已被合并 absorb）
- **S23**：events.ndjson 永不删行（compaction 只 truncate 给定时间窗之前的、且保留 snapshot 起点）

兼容性：

- **S24**：现有单元测试套件全绿（`pytest tests/unit -q` + `ruff` + `scripts/check_async_blocking.py`，沿用 #905 gate）
- **S25**：本 RFC 新增 ≥60 个单元测试覆盖 S1-S23

### §9 Out-of-scope follow-ups（~70 行）

- Memory browser UI 展示 evidence score（**只看，不编辑**——编辑是 §7 rejects）
- 定时 funnel 报告推送（基于 §3.10 API）
- 话题级聚合机制（embedding 聚类 / 图数据库 / 主题模型路线）—— 独立 RFC 处理
- tiktoken encoding 切更新家族（reviewer 明示先不讨论）
- 归档恢复 UI（用户手动把 archived 条目拉回 active）
- 背景循环的更细粒度拆分（若实测某 sweep 慢阻塞另一 sweep）

---

## 5. 新常量汇总表（RFC 内反向检查 + 实现时 grep 对照）

**所有常量统一进 `config/__init__.py` 并加进文件末 `__all__`。**

**5.1 稳定默认**（草案值 OK，合 PR 时不需要 reviewer 单独 gate）：

| 常量 | 默认 | 类型 | RFC § | 备注 |
|---|---|---|---|---|
| `EVIDENCE_CONFIRMED_THRESHOLD` | 1.0 | float | §3.1 | `score >= 1` → confirmed（1 次净 confirm） |
| `EVIDENCE_PROMOTED_THRESHOLD` | 2.0 | float | §3.1 | `score >= 2` → promoted（2 次净 confirm） |
| `EVIDENCE_ARCHIVE_THRESHOLD` | -2.0 | float | §3.1 | `score <= -2` → archive_candidate；pending 是补集 |
| `EVIDENCE_ARCHIVE_DAYS` | 14 | int | §3.5 | 累计 sub_zero 天数 ≥ 触发归档 |
| `IGNORED_REINFORCEMENT_DELTA` | -0.2 | float | §3.4 | ignored 反馈扣在 reinforcement 上（可为负） |
| `EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS` | 10 | int | §3.4 | 信号抽取触发条件之一 |
| `EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES` | 5 | int | §3.4 | 信号抽取触发条件之二（idle 时长） |
| `EVIDENCE_SIGNAL_CHECK_ENABLED` | True | bool | §3.4 | 独立开关，与 auto-correction toggle 解耦 |
| `PERSONA_RENDER_TOKEN_BUDGET` | 2000 | int | §3.6 | 非-protected persona 渲染总 token 上限 |
| `REFLECTION_RENDER_TOKEN_BUDGET` | 1000 | int | §3.6 | pending + confirmed reflection 总 token 上限 |
| `PERSONA_RENDER_ENCODING` | `"o200k_base"` | str | §3.6 | tiktoken encoding 名 |

**5.2 待 reviewer 敲定的 pre-merge gates**（详见 RFC §6.5；值在 merge PR 前必须确认）：

| 常量 | 草案默认 | 类型 | RFC § | Gate |
|---|---|---|---|---|
| `EVIDENCE_REIN_HALF_LIFE_DAYS` | 30 | int | §3.5 / §6.5 Gate 1 | reinforcement 半衰期 |
| `EVIDENCE_DISP_HALF_LIFE_DAYS` | 180 | int | §3.5 / §6.5 Gate 1 | disputation 半衰期 |
| `REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_COUNT` | 10 | int | §3.4 / §6.5 Gate 2 | 合成带几条已 absorbed fact |
| `REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_DAYS` | 14 | int | §3.4 / §6.5 Gate 2 | 只带最近几天内 absorbed 的 |
| `EVIDENCE_EXTRACT_FACTS_MODEL_TIER` | **pending** | str | §3.4 / §6.5 Gate 3 | extract_facts 用哪一 tier |
| `EVIDENCE_NEGATIVE_TARGET_MODEL_TIER` | **pending** | str | §3.4 / §6.5 Gate 3 | 负面 target 判定用哪一 tier |
| `EVIDENCE_PROMOTION_MERGE_MODEL_TIER` | **pending** | str | §3.9 / §6.5 Gate 3 | merge 判定用哪一 tier |
| `ARCHIVE_FILE_MAX_ENTRIES` | 500 | int | §3.5 / §6.5 Gate 4 | 归档分片单文件条数上限 |

3 个 model tier 常量的草案标为 **pending**——实现作者拿草案占位，但 PR 开出时必须在 PR description 高亮 "Pending Gate 3"，reviewer 必须在 merge 前给结论。其余 gate 有草案默认值但允许 reviewer 调整。

**Q7 相关常量暂不落地**（原设计过的 `TOPIC_SOFT_AVOID_AT` / `TOPIC_HARD_AVOID_AT`）—— 话题聚合机制 pending Q7 下一轮讨论。

**新 `EVT_*` 常量**（`memory/event_log.py:51-70`，**不进 `config/__init__.py`**——event_log 模块内聚）：
- `EVT_REFLECTION_EVIDENCE_UPDATED = "reflection.evidence_updated"`
- `EVT_PERSONA_EVIDENCE_UPDATED = "persona.evidence_updated"`
- `EVT_PERSONA_ENTRY_UPDATED = "persona.entry_updated"`（同步加进 `ALL_EVENT_TYPES` frozenset）

---

## 6. 引用的代码锚点（RFC 引用 + 写完自检用 grep 对照）

| 文件 | 锚点 | 用处 |
|---|---|---|
| `docs/design/memory-event-log-rfc.md` | §3.4 / §3.4.3 / §3.5 / §3.6 | 结构模板 + 写序 / 幂等契约 / 启动顺序 / compaction 规则——引用不复述 |
| `memory/event_log.py:51-70` | `EVT_*` + `ALL_EVENT_TYPES` | 加 3 个新常量位置 |
| `memory/event_log.py:387` / `:457` | `record_and_save` / `arecord_and_save` | 所有 evidence write 入口 |
| `memory/event_log.py:477` | `Reconciler.register` | 3 个新 handler 注册点 |
| `memory/event_log.py:97` | `ApplyHandler` type alias | handler 合约 |
| `memory/persona.py:43` | `SUPPRESS_MENTION_LIMIT` | 现有约定参考（§3.2 recent_mentions 不动） |
| `memory/persona.py:48` | `SIMILARITY_THRESHOLD` | text_hint fuzzy match 阈值复用（§3.4.5） |
| `memory/persona.py:56` | `_extract_keywords` | fuzzy match 实现复用 |
| `memory/persona.py:110` | `PersonaManager._alocks` | asyncio.Lock 嵌套协议 |
| `memory/persona.py:540-572` | `_normalize_entry` defaults | entry schema 扩 4 字段位置 |
| `memory/persona.py:577-579` | `FACT_ADDED` / `FACT_REJECTED_CARD` / `FACT_QUEUED_CORRECTION` | add_fact 返回码（§3.9 merge 路径） |
| `memory/persona.py:602` | `_build_fact_entry` | tag 传播修复点 |
| `memory/persona.py:697` | `_texts_may_contradict` | §3.4.4 调用链收窄 |
| `memory/persona.py:1040` | `_compose_persona_markdown` | §3.6 改写 |
| `memory/persona.py:1118` | `arender_persona_markdown` | §3.6 async 入口 |
| `memory/reflection.py:49` | `MIN_FACTS_FOR_REFLECTION` | 生命周期阶段 2 |
| `memory/reflection.py:66-67` | `AUTO_CONFIRM_DAYS` / `AUTO_PROMOTE_DAYS` | **删除** |
| `memory/reflection.py:87` | `ReflectionEngine._alocks` | asyncio.Lock 嵌套协议 |
| `memory/reflection.py:402-413` | reflection schema | 扩 4 字段位置 |
| `memory/reflection.py:557` | `check_feedback` | §3.4 ignored -= 0.2 分支补入 |
| `memory/reflection.py:820` | `_aauto_promote_stale_locked` | 重构去时间跳级 |
| `memory/facts.py:312/315` | `importance` 过滤 | 保留（输入门槛）+ §3.7 soft_avoid 降值 |
| `memory/facts.py:337` / `:339` | `importance` / `tags` 写入 | schema 不变 |
| `memory/facts.py:~290` | `extract_facts` | §3.4 扩 reinforces/negates 解析 |
| `memory_server.py:326` | `_spawn_background_task` | 新循环的 spawn 入口 |
| `memory_server.py:569` | `_periodic_rebuttal_loop` | 参考模式 |
| `memory_server.py:651` | `_periodic_auto_promote_loop` | §3.9 入口——重构为 score-driven |
| `memory_server.py:680` | `_periodic_idle_maintenance_loop` | §3.4.3 复用路径（但独立开关） |
| `config/prompts_memory.py:699` | `FACT_EXTRACTION_PROMPT` 5 语言 | §3.4.5 扩字段 |
| `config/prompts_memory.py:899` | `REFLECTION_FEEDBACK_PROMPT` | §3.4 复用 |
| `utils/token_tracker.py` | LLM usage 追踪 | §3.6 区分：新增 `utils/tokenize.py` 管本地计数，职责不重叠 |
| `pyproject.toml` | `[project.dependencies]` | 加 `tiktoken>=0.7.0` |

---

## 7. 全局设计约束（reviewer 跨 § 强调）

1. **所有背景任务不阻塞对话主路径**。loop 内部 `_is_idle()` 检查、逐角色中断。
2. **背景写入之间互斥**（走 `_alocks` + `record_and_save`），**读可陈旧**（persona/reflection manager 本身持 cache；背景任务读 cache 不查磁盘，render 读 cache——stale read 可接受）。
3. **异步写入**：所有 mutation 都走 async path（`aapply_signal` / `amerge_into` / ...），不阻塞 FastAPI event loop。
4. **归档 JSON 分片**：按日期 + uuid8 后缀；每分片 ≤ `ARCHIVE_FILE_MAX_ENTRIES` 条；目录 `memory/<char>/{persona,reflection}_archive/`。
5. **配置常量集中到 `config/__init__.py`**：所有 evidence / token / 阈值常量都在这里、加进文件末 `__all__`。`memory/evidence.py` 是新模块**只放纯函数 + 背景辅助**，不放常量。`memory/event_log.py:51-70` 的 `EVT_*` 常量是例外（event_log 模块内聚 API surface 的一部分），新增的 3 个 `EVT_*` 也加在那里、不进 `config/__init__.py`。
6. **默认只 async，不强制 sync 对偶**：本 RFC 新增的 public method（`aapply_signal` / `amerge_into` / `acount_tokens` / `aextract_facts_and_detect_signals` 等）默认只有 `a*` 异步版本。生产代码路径全 async；测试用 `pytest-asyncio`。**不**为对偶性而对偶——只有真有 sync 调用方（如 migration 脚本必须 sync 跑）才补 sync twin，且明示理由。
7. **i18n 5 语言覆盖**：所有新 prompt / 新用户可见文本（zh / en / ja / ko / ru）同步更新。`config/prompts_memory.py` 的现行 dict 模式（每条 prompt 是 `{lang: prompt_str, ...}`）保持。
8. **Prompt 安全水印**（防 prompt injection 检测）：所有新增 prompt（每语言每条）必须包含以下字符串中**至少一个**：
   - `"======以上为"`（现行 `FACT_EXTRACTION_PROMPT` 已用此模式）
   - `"你是一个情感分析专家"`
   - `"sends some useful information"`
   - `"你是一个图像描述助手, "`
   - `"automation assessment agent, "`
   - `"careful deduplication judge."`
   
   选择建议（按 prompt 用途）：
   - 改造的 `FACT_EXTRACTION_PROMPT`：保留现有 `"======以上为对话======"` 水印
   - 新增 `SIGNAL_DETECTION_PROMPT`：用 `"careful deduplication judge."`（语义最贴）+ `"======以上为已有观察======"` 双重保险
   - 新增 `NEGATIVE_TARGET_CHECK_PROMPT`：用 `"你是一个情感分析专家"`（zh）/ 各语言相应短语
   - 新增 `PROMOTION_MERGE_PROMPT`：用 `"careful deduplication judge."`
   
   每条 prompt 的 5 语言版本可以**统一用同一个** watermark 字符串（不要求每语言都有本地化 watermark），但每条都必须出现至少一次。
9. **命名常量**：RFC 内任何数字（阈值 / 天数 / 预算）都必须在常量表里有名字；裸数字仅在 code block 示例里。
10. **行号飘移补偿**：代码引用用 `file:line (symbol_name)` 双载形式（例 `persona.py:697 (_texts_may_contradict)`），行号飘了仍能 grep 找到。

---

## 8. 写作流程

1. 按本 plan 的 §4 章节骨架顺序逐节写。每节先写决定 + rationale + 代码锚点，再补失败模式 + 边界条件。
2. 写完每节自检 "rationale 块存在" ——若只列决定没讲 why，立刻补。
3. event-log RFC 的锚点（§3.4 write order、§3.4.3 idempotency contract、§3.5 startup order、§3.6 compaction）用 `see event-log RFC §X.Y` 直接引用，**不复述**。
4. 代码引用用 `file:line + 符号名` 双载形式。
5. 每写 300 行 check 一次总长度，1800 行时收尾。

---

## 9. Verification（RFC 写完自检）

1. **长度**：`wc -l docs/design/memory-evidence-rfc.md` → 1500-2000 行
2. **红线覆盖**：grep 5 条红线原文出现次数——§2 / §7 / §8 都命中
3. **API 存在性**：RFC 里每个 `EventLog.*` / `Reconciler.*` / `(Persona|Reflection).*` 引用，grep 代码文件验存
4. **代码锚点准确性**：grep 每个 `file:line` 对应行，确认符号真在那行
5. **i18n 声明**：§3.4.5 / §3.7 / §3.9 等涉及 prompt 改动处明示 5 语言同步
6. **对偶性**：所有新 public method 的 sync/async 对偶在 §3.8 API surface 登记
7. **Magic-number 扫描**：RFC 散文里任何数字 → 必须是 §5 常量表里的名字
8. **红线 5**：§3.5 明示 "read-time decay not write-time"
9. **reviewer 18 条反馈逐条 check**：
   - (1) 长度 1500-2000 行 → §0.3
   - (2) 公式去 importance 化 → §3.1
   - (3) per-entry UI 硬 reject → §2.4 + §7
   - (4) ignored=-0.2 → §3.4 + §5 常量表
   - (5) pyproject.toml 主 + tiktoken fallback warning → §3.2（全局口径）
   - (6) 负面词本地扫 + LLM 二次判 target → §3.4.2
   - (7) 信号抽取复用 idle 背景 + 独立开关 → §3.4.3
   - (8) 方法全名，不用 aadd 简写 → RFC 正文全用 `aadd_fact` 等全名
   - (9) 完整生命周期前言 → §3.0
   - (10) persona/reflection 独立预算 → §3.6
   - (11) 无 render-time 合并，无 cache → §3.6
   - (12) 话题级 V1 不做 → §3.7
   - (13) 归档分布式文件 → §3.5
   - (14) 归档更积极（累计天数） → §3.5
   - (15) `persona.entry_updated` 是确定新事件 → §3.3
   - (16) 复核改为 extraction 直接丢 → §3.7
   - (17) avoid 触发时自动加一条 persona fact → §3.7
   - (18) 背景任务不阻塞、异步写、读可陈旧 → §7 全局约束

---

## 10. Delivery

```bash
# worktree 已在 claude/review-pr-634-memory-rSsDR
# 1. 把 RFC 写到 docs/design/memory-evidence-rfc.md（本计划 §4 骨架展开）
# 2. 自检（本计划 §9）
# 3. commit + push：

git add docs/design/memory-evidence-rfc.md
git commit -m "$(cat <<'EOF'
Draft memory-evidence-rfc.md v1

First draft of the evidence-mechanism RFC (issue #849).
Built on #905 event-log infrastructure.

Scope:
- P-A signal detection (extract_facts with method-A target_id dispatch +
  check_feedback reuse + keyword-then-LLM negative target path +
  periodic scheduler on idle-maintenance loop)
- P-B evidence data layer + 3 new event types (reflection.evidence_updated,
  persona.evidence_updated, persona.entry_updated for merge-on-promote)
- P-C read-time decay (separate rein/disp half-lives) + distributed
  sharded archival
- P-D per-section render budget (persona vs reflection) + tiktoken o200k_base
- §3.9 LLM merge-on-promote (the only LLM call at score-threshold crossing)
- §3.10 funnel analytics API

Explicit deferrals:
- Topic-level aggregation / avoidance — V1 has no "topic" abstraction;
  needs separate RFC (clustering / graph approach).
- Per-entry UI score editing — hard rejected (memory system privacy).

Formula departs from task spec §5 by dropping importance from status
derivation; thresholds become "net user confirmations" (CONFIRMED=1,
PROMOTED=2). Rationale in §3.1.

Authored as temporary owner — successor authors may freely amend.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin claude/review-pr-634-memory-rSsDR
```

推送后向用户报 (1) RFC 行数 (2) commit hash (3) push OK/fail。**不开 PR，不改其他文件。**

**RFC landed 后的清理**（reviewer 已确认）：

```bash
# RFC 写完、reviewer sign-off 后，删掉两个 handoff / plan 文件。仓库里只留 rfc.md。
git rm docs/design/memory-evidence-task.md
git rm docs/design/memory-evidence-rfc-plan.md
git commit -m "Drop handoff + plan docs after memory-evidence-rfc.md landed"
git push
```

理由：
- `memory-evidence-task.md` 是上一个 session 给本 session 的 fresh-session brief，自身 status header 就写了 "Delete after the RFC lands."
- `memory-evidence-rfc-plan.md` 是本 session 设计讨论的过程产物；和 task.md 同时也已经发生过若干处分歧（score 公式 / Q7 deferral / 拆 LLM call 等），留着会和最终 RFC 不一致、误导未来 reader
- RFC 本身要求自包含、可独立读懂——读者不需要读 task / plan 任何一个

---

## 11. Red lines（我执行时不打破）

1. 本次 session 只新增 / 修改 `docs/design/memory-evidence-rfc.md` 和本 plan 文件。不改其他文件。
2. 不开 PR。
3. 若落笔时发现某个决定"对着真实代码感觉不对" → 停下来、在 §6 open questions 登记、**不静默改变**。本 plan 里已经改变的（score 公式去 importance、拆 LLM call 等）是 reviewer 明确要求后的改动，不算静默。
4. 所有数值都是命名常量、且都在 `config/__init__.py`。
5. 所有新 API method 默认只 async（不为对偶性而对偶），sync twin 仅在真有 sync 调用方时补、附理由。
6. 5 语言 i18n 覆盖在 RFC 里明示，本 RFC 本身只是 doc 不改 prompts 文件。
7. 所有新 prompt 必须包含 §7 列的安全水印字符串之一。
