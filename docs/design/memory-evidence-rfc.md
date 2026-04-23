# RFC: Memory subsystem user-driven evidence mechanism

Status: **Draft v1** — authored by Claude during the #849 review session as a
temporary owner. Successor authors may freely amend, restructure, or
override any part of this document. Design decisions here reflect the state
of the discussion at 2026-04-22 and are not frozen — in particular, the
exact score thresholds, half-life days, render-budget token numbers, and
LLM tier choices are engineering judgment calls that the implementation
author should re-verify against real workload traces before coding. A
dedicated §6.5 "Pre-merge reviewer gates" lists the specific values that
require explicit reviewer sign-off before the corresponding implementation
PR merges.

## Revision log

- **v1.1** (2026-04-22, same-day revision): split the single
  `last_signal_at` into independent `rein_last_signal_at` /
  `disp_last_signal_at` per counter. Original v1 design shared one
  timestamp for both rein and disp decay — a late negation would
  reset the clock for long-decayed reinforcement (or vice versa),
  inflating `evidence_score` incorrectly. Fix updates §3.1.1 formula,
  §3.2 schemas, §3.3 event payloads, §3.4 apply semantics, §3.5
  decay implementation, §5 migration table, and §8 success criteria
  (new S1b). No change to overall design direction — just correctness
  of the decay math.

- **v1** (initial draft, 2026-04-22): first RFC for issue #849 user-driven
  evidence mechanism. Builds on the event-sourced infrastructure that
  landed in #905 (`memory/event_log.py`, `Reconciler`, per-character
  `asyncio.Lock` / `threading.Lock`, `Outbox`, `CursorStore`). Score
  formula decouples from `importance` — status derivation uses only
  `effective_reinforcement - effective_disputation` so thresholds read as
  "net user confirmation count" rather than opaque importance offsets.
  Signal detection is split into two LLM calls (Stage-1 抽 fact,
  Stage-2 判 signal 映射). Topic-level mechanism is explicitly deferred;
  V1 has no "topic" abstraction. Promotion from reflection to persona
  goes through an LLM merge gate that can fold a new reflection into an
  existing persona entry (new `persona.entry_updated` event).

## 1. Motivation

The memory subsystem pipeline (`fact → reflection → persona`) currently
has four structural problems that accumulated evidence has made concrete:

### 1.1 矛盾检测漏判率高

现行 `memory/persona.py:697 (_texts_may_contradict)` 是纯关键词重叠启发
式：CJK n-gram 切词后算两段文字的共享关键词比例，超过
`SIMILARITY_THRESHOLD = 0.6` (`persona.py:48`) 就判"可能矛盾"。这个机制
有两个方向的错误：

- **漏判**：用户说"我不喜欢猫娘了"跟已有 persona "主人喜欢猫娘"字面重叠
  低（只有"喜欢猫娘"三字重合，"不"是关键否定词但 n-gram 切不出来），
  ratio 算下来可能 < 0.6，被判"不矛盾"，新 fact 无阻力入库、事实上和老
  persona 同时并存。
- **误判**：两段真实语义无关但恰好共享人名（"主人"/"猫娘"）会把 overlap
  拉高到 0.6 以上，触发虚假的 correction 流程。

更根本的问题：用户**明确的否认信号**（在回话中说"不对"/"我不那样"）根本
进不了这条路径——`_texts_may_contradict` 只在 `aadd_fact` 插入新 entry
时比对 persona 自身两条文本，user 的否认不会经它传导到任何 evidence。

### 1.2 进入 persona 后永久驻留

一条 reflection 晋升到 persona 后没有任何"冷却 / 降级 / 归档"机制。
`memory/reflection.py:820 (_aauto_promote_stale_locked)` 的 promotion
逻辑只有一次升格、之后就永久保留。僵尸 fact 随年累月堆积：

- "主人三年前暂时搬到北京" → 三年后用户早已搬走，persona 还在说这事
- "主人最近在准备考试" → 考试早结束了
- 用户兴趣转变：从前喜欢猫娘、现在喜欢狐娘，猫娘相关 fact 仍在 persona
  影响 LLM 对话

### 1.3 persona markdown 无 token 上限

`memory/persona.py:1040 (_compose_persona_markdown)` 输出的 markdown 长度
随 fact / reflection 累积无上限膨胀。部署到桌面端 Nuitka 打包产物、
跑小型本地 LLM 时，主 prompt 的 context window 被这个无界持续涨的
persona 段挤爆，是实际发生过的问题。

### 1.4 `fact.importance` 字段写了但运行时不决策

`memory/facts.py:312,337 (importance)` 在抽取时 LLM 会给每条 fact 评分
1-10 并写到 facts.json。**但此后没有任何下游消费**：reflection 合成
不看 importance 排序，persona 晋升不看 importance 权重，render 不看
importance 截断。现行代码里 importance 字段事实上是空置。唯一的用途
是 `facts.py:315` 的 `if importance < 5: continue` 硬丢，这本身是个
定性门槛不是定量运用。

### 1.5 为什么现在是好时机

#905 合并后，`memory/event_log.py` 提供的 `EventLog.(a)record_and_save`
基础设施已就绪——任何"状态变更"都可以走 `load → append event → mutate
view → save view → advance sentinel` 的五步合约，per-character
`threading.Lock` 保证并发安全，`Reconciler` 提供崩溃恢复。本 RFC 新增
的事件类型和新机制可以直接挂到这套合约上，不再需要自造持久化路径。

本 RFC 实现 issue #849 提出的 user-driven evidence 框架：每条
reflection / persona entry 维护 evidence 计数器 `(reinforcement,
disputation, rein_last_signal_at, disp_last_signal_at)`——rein 和
disp 各自独立的时间戳，仅由用户显式输入驱动累积，读时做半衰衰减，
派生出 pending / confirmed / promoted / archive_candidate 状态。
顺带解决以上 4 个结构问题。

### 1.6 为什么是独立 RFC 而非塞进 memory-event-log-rfc

`memory-event-log-rfc.md` 的状态已经是 **Implemented (P2.a)**，事实上
已经定稿。本 RFC 新增的是数据字段 + 事件类型 + 信号抽取 + 合并逻辑 +
归档策略——新内容多到塞进老 RFC 会把结构打乱；独立文档边界清晰、修
订史也更易追。

本文会在 §3.3 原地讲清楚 `record_and_save` 合约和 Reconciler 工作
模式，不要求读者先读懂 event-log RFC。

## 2. Non-goals

### 2.1 No AI-driven signals

Evidence 计数器**只能**因"用户显式输入"累加。允许的输入入口只有三个：

- (a) 从 user 消息抽取新 fact 时，判定新 fact 对已有 reflection / persona
  构成 reinforces / negates（§3.4 Stage-2）
- (b) `ReflectionEngine.check_feedback` 对 user 消息分析 surfaced
  reflection 的反应（confirmed / denied / ignored）
- (c) 本地负面关键词扫到 + LLM 二次判定具体 target（§3.4.2）

AI 在 response 里提到某 fact **不加分**；reflection synthesis 的链路
也不给自己加分；`recent_mentions` 保留但方向相反（AI 提到太多 →
suppress，不是 reinforce）。

理由：自我强化循环。如果 AI 自己偏爱的幻觉被计成正面 signal，这些
幻觉只会越来越被 reinforce；AI 习惯性回避的真实事实反而会负向累积。
evidence 必须是"用户的声音"而非"AI 的印象"。

### 2.2 No cross-character evidence

每个角色（猫娘）维护独立的 evidence。角色 A 学到的 fact 不会投射给
角色 B。理由：多角色 persona 代表用户和不同角色的独立关系演化，
串联 evidence 会在多角色场景下污染语义。

### 2.3 No ML-ranked persona

不训模型学"哪些 fact 更值得留"。score 完全 rule-based、可手算解释。
引入 ML 的收益不足以补偿可调性 / 可审计性的损失。

### 2.4 No per-entry UI score editing

V1 **不做**"给用户手动 ±score 的 UI 按钮"。记忆系统需要一定隐私感：
用户不应通过 UI 直接调 persona 条目的 evidence 数值。所有 evidence
演化**全部**通过已有的 user 消息管道驱动——用户"想说什么"是输入，
不是"编辑一张表"。

UI 展示 score（只读）是可能的后续工作，见 §9；但编辑权限在 RFC §7
明确 reject。

### 2.5 No event schema versioning

若新增事件类型 payload 需要 schema 迁移，手动写一次性 migration
pass 即可，不为此引入 schema 版本号 / 动态 dispatch。

### 2.6 `recent_mentions` 语义不动

`memory/persona.py:553 (recent_mentions)` 现行语义是 "AI 在最近 N 次
response 里提到该 entry → 超过 `SUPPRESS_MENTION_LIMIT=2` 就
suppress"——这是**反向**机制（AI 提太多 → 少提），与 evidence（user
说什么 → 累积）方向相反、正交。保持原语义不改动。

### 2.7 话题级聚合 / 自动回避机制不做

V1 没有"话题"这个抽象。如果未来需要"用户不想聊某类话题"的行为（例如
自动识别用户对"前任"话题累积反感 → 避开相关 fact），得做全局聚类
（embedding / 图数据库 / 主题模型），这超出本 RFC 范围。

具体被延后的包括：

- 按话题聚合 evidence 的逻辑
- 自动生成"用户不想聊 X 话题"的 persona fact
- 让 fact / reflection / persona 携带 tags 字段
- `FACT_EXTRACTION_PROMPT` 不再要求 LLM 输出 `tags`
  —— `facts.py:339` 的 `tags` 字段在新 fact 上默认写空数组（schema
  字段保留位但不消费），老数据的 tags 不动（也不用）。

独立 RFC 处理话题级机制，本 RFC 不涉及。

## 3. Proposed design

### 3.0 Lifecycle preamble

本节是全局图景：一个用户说的话、从输入到最终影响 LLM prompt 的完整
路径。后续 §3.1 - §3.11 是对这条路径各环节的展开。

```
─────────────── 阶段 0：消息沉淀 ───────────────

 用户 ──对话──▶ recent_history.json
                 │
                 │ (不主动触发任何处理，只是累积)
                 ▼
─────────────── 阶段 1：信号抽取（背景任务） ───────────────

memory_server.py:680 (_periodic_idle_maintenance_loop)
  │ (每 40s 轮询；空闲且 history ≥ EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS=10
  │  轮 或 距上次 check ≥ EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES=5 分钟 时进入)
  │
  ├──▶ Stage-1: FactStore._allm_extract_facts(new_user_messages)
  │       │  纯抽取，prompt 不带任何已有观察作 context，避免 LLM 把
  │       │  已有观察当新 fact 摘出来（自循环）
  │       │  LLM call → [{text, importance, entity}, ...]
  │       │  （不要 tags、不要 reinforces/negates；这些是 Stage-2 职责）
  │       │  importance < 5 → 存但不参与后续 reflection 合成
  │       │     （消费侧 get_unabsorbed_facts(min_importance=5) 过滤，
  │       │      不在 extract 内硬丢；保留 audit + 未来调阈值不丢历史）
  │       │  [SHA-256 / FTS5 dedup] → 重复则跳过
  │       ▼
  │       facts.json 新增 entry {id, text, importance, entity, ...}
  │
  ├──▶ Stage-2: FactStore._allm_detect_signals(new_facts, existing_observations)
  │       │  prompt 带 new_facts + 已有观察清单
  │       │     （confirmed+promoted reflection + 非 protected persona
  │       │      entry 的 [{id, text}]）
  │       │  LLM call → {signals: [{source_fact_id?, target_type,
  │       │                          target_id, signal, reason}]}
  │       │  防御：每条返回的 target_id 必须在传入的 existing_observations
  │       │       集合里；编造的 ID 丢弃 + log warning
  │       ▼
  │       按 signals 列表 dispatch：
  │         reinforces → reinforcement +=1.0, source='user_fact'
  │         negates    → disputation   +=1.0, source='user_fact'
  │       → EVT_REFLECTION_EVIDENCE_UPDATED / EVT_PERSONA_EVIDENCE_UPDATED
  │
  └──▶ 对话主路径上的 negative keyword hook，详见阶段 1.5

─────────────── 阶段 1.5：负面关键词 → 隐藏的全链路 ───────────────

用户说 "别提了" / "换个话题" 之后，到该 reflection / persona 真正
在渲染中消失，发生这些事（按时间顺序）：

  ① /process 处理 user 消息时，本地扫 NEGATIVE_KEYWORDS_I18N
     （`config/prompts_memory.py` 新增常量，5 语言 frozenset dict）
     命中？没命中 → 走完正常对话流程，结束。
     命中 → 立刻（不等下个 idle 周期）触发 ②，派发为 async task
            （不阻塞当轮回话）。

  ② NEGATIVE_TARGET_CHECK_PROMPT 异步派发一次小 LLM 调用
     输入：近期 user 消息 + surfaced reflection(feedback=null)
           + 最近被 mention 的 persona entry
     输出：targets 列表 / 空数组
     空数组 → 用户只是泛化情绪 → no-op，结束。

  ③ 对每个 target 走 `PersonaManager.aapply_signal` /
     `ReflectionEngine.aapply_signal`：
     disputation +=1.0, source='user_keyword_rebut'
     → 落 EVT_*_EVIDENCE_UPDATED 事件、view 同步落盘。

  ④ 该条目 evidence_score 当下立即 -=1.0（read-time 现算，见 §3.5）。

  ⑤ 后续行为自动级联：
     - render 时：按 evidence_score DESC 排序 + token 预算 trim →
       这条低分被挤出渲染 = LLM 看不到 = 不主动提了
     - `aget_followup_topics`：按 score 过滤（score ≥ 0 才进候选
       池，见 §3.8），低分 reflection 不再被选作主动搭话
     - score 跨 0 → sub_zero_days 计数器开始累加
       连续累计 EVIDENCE_ARCHIVE_DAYS=14 天 score 仍 < 0
       → 真正归档到 *_archive/<date>_<uuid8>.json 分片
       （主路径不再加载，但硬盘永久保留——见 §3.11）

  ⑥ 用户反转态度（重新提起、reinforces）→ reinforcement +=1.0
     → score 回正 → 自然回到 render → 主动 followup 重新候选。
     注意：sub_zero_days 计数器**不清零**（"归档更积极"），
     只是计时器满 14 天之前 score 回正的话当然就不会 archive。

  整条链路**没有**独立的 "suppress=True" 状态变更——隐藏完全靠
  evidence_score 在 read 时排序 + budget 挤出实现。现行的 suppress /
  suppressed_at（`persona.py:553-555`）字段是另一套机制（"AI 自己提
  太多 → 抑制"），与本路径正交，保持原语义不动。

─────────────── 阶段 2：反思合成 ───────────────

 积累到 MIN_FACTS_FOR_REFLECTION=5 (`reflection.py:49`) 条未 absorbed
 fact 时：
  ReflectionEngine.synthesize_reflections(name)
    │  LLM prompt 分两区：
    │    [待合成 - 未 absorbed 的目标 fact]
    │      - [fact_001] 主人今天咖啡加双份糖  (importance=6)
    │      - [fact_002] 主人买了新的意式咖啡豆  (importance=5)
    │    [上下文参考 - 最近已 absorbed，不要重复合成]
    │      - [fact_old_xxx] 主人喜欢咖啡    (已在 reflection ref_abc)
    │      - [fact_old_yyy] 主人每天一杯拿铁 (已在 reflection ref_def)
    │    取最近 REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_COUNT=10 条已
    │    absorbed、且在 REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_DAYS=14
    │    天内的 fact 进上下文区。
    │    prompt 明示："新 reflection 必须基于上区；下区仅供参考；
    │    若新事实仅是下区已有观察的细化或重复 → 返回空数组。"
    │  reflection_id = sha256(sorted_source_fact_ids) → 确定性 id
    │                                                    (P1 幂等)
    │  ▼
    │  EVT_REFLECTION_SYNTHESIZED 事件发出
    │                   (status=pending, rein=0, disp=0)
    │  EVT_FACT_ABSORBED 事件发出 (source fact 标记为已吸收)

─────────────── 阶段 3：主动搭话 + 用户反馈 ───────────────

 猫娘选 reflection 主动聊：
  ReflectionEngine.aget_followup_topics(name)
    │  过滤：pending && next_eligible_at 到期 && evidence_score ≥ 0
    │  ▼
  前端 render → 用户看到 → 用户回话
  ReflectionEngine.arecord_surfaced → surfaced.json 登记
                                       (feedback=null 占位)

 下一轮空闲维护：
  ReflectionEngine.check_feedback(new_user_messages) → LLM 判断：
    - confirmed → reinforcement +=1.0 → EVT_REFLECTION_EVIDENCE_UPDATED
    - denied   → disputation   +=1.0 → EVT_REFLECTION_EVIDENCE_UPDATED
    - ignored  → reinforcement -=0.2 → EVT_REFLECTION_EVIDENCE_UPDATED
       （扣在 reinforcement 侧、允许为负；语义"弱正信号衰退"——
        见 §3.4.1 选型理由。reinforcement 非严格单调，但它本就表达
        "被积极肯定过的累积强度"，负值意味着"曾被肯定过但近期被
        忽略"，语义自洽。）

─────────────── 阶段 4：晋升 / 合并 / 否决 ───────────────

 当 reflection R 的 evidence_score 跨过 EVIDENCE_PROMOTED_THRESHOLD=2.0：
  ReflectionEngine._apromote_with_merge(R)
    │  Load: R + 同 entity 的所有现有 persona entry (非 protected)
    │       + 同 entity 的所有 confirmed/promoted reflection
    │  LLM call (PROMOTION_MERGE_PROMPT)
    │       → {action: "promote_fresh" | "merge_into" | "reject", ...}
    │  ▼
    action == "promote_fresh" →
      PersonaManager.aadd_fact(source='reflection', source_id=R.id)
        → EVT_PERSONA_FACT_ADDED
      ReflectionEngine state: confirmed → promoted
        → EVT_REFLECTION_STATE_CHANGED

    action == "merge_into target_id" →
      PersonaManager._amerge_into(target_id, merged_text, ...)
        → EVT_PERSONA_ENTRY_UPDATED  (新事件：text + evidence 覆写)
        → EVT_PERSONA_EVIDENCE_UPDATED  (target 的 evidence 合并)
      ReflectionEngine state: confirmed → merged  (新 status 值)
        → EVT_REFLECTION_STATE_CHANGED
        + absorbed_into: target_id  (溯源字段，§3.11)

    action == "reject" →
      ReflectionEngine state: confirmed → denied
      denied_reason = 'llm_merge_rejected'

    LLM call 失败（超时 / 非法 JSON）→ **不**默认 promote_fresh。
    reflection 留在 confirmed，下次 cycle 重试；连续失败到
    EVIDENCE_PROMOTE_MAX_RETRIES=5 次 → status='promote_blocked'。
    详见 §3.9。

─────────────── 阶段 5：持续 user signal → 老化 → 归档 ───────────────

 promoted 的 persona entry 继续接收 user signal：
  每次 extract_facts 或 check_feedback 都可能再次 mutate evidence
  ↓
 Read-time decay（§3.5）：每次读 entry 现算 effective 值
  effective_reinforcement = reinforcement ×
                   0.5 ^ (age_days / EVIDENCE_REIN_HALF_LIFE_DAYS)
  effective_disputation   = disputation ×
                   0.5 ^ (age_days / EVIDENCE_DISP_HALF_LIFE_DAYS)
  （disputation 也衰减，但 half-life=180d 远长于 rein 的 30d；
    否认需长期留痕，但不应永恒）
  ↓
 背景归档循环 (_periodic_archive_sweep_loop)：
  - 扫所有非 protected 条目
  - evidence_score < 0 → 累加 entry['sub_zero_days'] (整数计数器)
  - evidence_score ≥ 0 → sub_zero_days 不变 (累计不回退，归档更积极)
  - sub_zero_days ≥ EVIDENCE_ARCHIVE_DAYS=14 → 归档到分片文件
    memory/<char>/persona_archive/<YYYY-MM-DD>_<uuid8>.json
    memory/<char>/reflection_archive/<YYYY-MM-DD>_<uuid8>.json
    每分片最多 ARCHIVE_FILE_MAX_ENTRIES=500 条；满就新开分片

─────────────── 阶段 6：渲染 ───────────────

 arender_persona_markdown(name):
  - Phase 1：split entries
      protected_entries       = source == 'character_card'
      non_protected_persona   = 其余 persona entry
      pending_reflections     + confirmed_reflections = 反思
  - Phase 2：score-trim per-section，独立预算
      protected_entries：整段输出，不计 token（豁免）
      non_protected_persona：按 evidence_score DESC 保留，累计
        acount_tokens ≤ PERSONA_RENDER_TOKEN_BUDGET=2000
      reflections：按 evidence_score DESC 保留，累计
        ≤ REFLECTION_RENDER_TOKEN_BUDGET=1000
  - Phase 3：markdown 拼装（沿用现有 _compose_persona_markdown 结构）

 注意：
   - **没有 render-time LLM 调用**。合并只在阶段 4 发生。
   - **没有 render cache**。合并结果是直接写回 persona.json（通过
     entry_updated 事件），下次读已经是合并后态，不需要中间缓存。
```

### 3.1 Score formula + derived status

#### 3.1.1 公式

```
evidence_score(entry, now) =
    effective_reinforcement(entry, now) - effective_disputation(entry, now)

effective_reinforcement(entry, now) =
    reinforcement(entry) × 0.5 ^ ((now - rein_last_signal_at).days
                                  / EVIDENCE_REIN_HALF_LIFE_DAYS)
    if rein_last_signal_at is not None else reinforcement(entry)

effective_disputation(entry, now) =
    disputation(entry) × 0.5 ^ ((now - disp_last_signal_at).days
                                / EVIDENCE_DISP_HALF_LIFE_DAYS)
    if disp_last_signal_at is not None else disputation(entry)
```

rein 和 disp 各自有**独立的时间戳** `rein_last_signal_at` /
`disp_last_signal_at`。None 表示该侧没收过 signal → 不衰减（一般 counter
也是 0）。

**为什么两个独立时间戳而不是共享一个**（v1.1 修订）：

考虑场景——一条 entry 在 30 天前收过若干 `reinforces`，rein=3；期间
没 disp。共享单一 `last_signal_at` 的话，30 天时衰减后
`effective_rein = 3 × 0.5^1 = 1.5`。现在用户发一次 `negates`，若共享
时间戳会重置为 `now` —— 下次读计算时 `age_days=0`，effective_rein
回到 `3`，旧的 reinforcement 被"刷新"回未衰减态。`score = 3 - 1 = 2`
跨 PROMOTED 阈值，行为错误。

独立时间戳解决：`negates` 事件只改 `disputation` 和 `disp_last_signal_at`，
`rein_last_signal_at` 保持 30 天前不变——下次读 `effective_rein` 仍是
1.5，`score = 1.5 - 1 = 0.5`，正确。

**两个半衰期**（草案值，见 §6.5 Gate 1 reviewer 敲定）：

- `EVIDENCE_REIN_HALF_LIFE_DAYS = 30`：reinforcement 衰到一半的天数。
  语义：用户一个月没再提的喜好，热度减半。
- `EVIDENCE_DISP_HALF_LIFE_DAYS = 180`：disputation 衰到一半的天数。
  语义：用户半年前否认过的事，如果没再强化，反对态度弱化到 1/2。

为什么 disp 衰减比 rein 慢 6 倍：用户**明确否认**是强信号，应有较长
留痕时间；但也不应永恒——半年后用户态度可能软化，应给回转空间。

#### 3.1.2 为什么公式里没有 `importance`

task spec 原草案公式是 `importance + reinforcement - disputation`。
本 RFC 有意**去掉 importance** 使派生状态的阈值语义直接等于"净用户
确认次数"。偏离理由：

task spec 原公式下，一条新 reflection 的 `importance ∈ [5, 10]`（LLM
抽取时分配，`facts.py:315` 过滤 ≥5 才入库），所以 score 从出生就在
[5, 10]。若 `EVIDENCE_CONFIRMED_THRESHOLD = 5`，**新 reflection 立即
跨入 confirmed 档**，根本不需要 user signal；`EVIDENCE_PROMOTED_THRESHOLD
= 10` 也只需要几次 reinforcement 就能穿越。阈值的"绝对数"和"用户要
确认几次"之间没有直接对应，reviewer 读起来困惑："阈值=5 意味着要被
肯定 5 次？"——不是，实际上是被肯定 0~5 次（取决于 importance）。

更根本的是：让 LLM 分配的 `importance` 影响 status 派生 = 让 AI 自己
决定"我觉得哪条重要 → 它就容易升格"，间接违反红线 1（evidence 只应
由 user signal 驱动）。

**新公式下，初始 `score = 0 - 0 = 0`**。一条新 pending reflection 出生
就在 0，需要：

- 1 次 `reinforces` → score = 1 → 跨 CONFIRMED_THR，变 confirmed
- 2 次 `reinforces` → score = 2 → 跨 PROMOTED_THR，触发 `_apromote_with_merge`
- 1 次 `denies` → score = -1（仍 pending）
- 2 次 `denies` → score = -2 → 跨 ARCHIVE_THR，进 archive_candidate
- 1 confirm + 1 ignored → score = 0.8（仍 pending）

阈值数值和"净用户确认次数"1:1 对应，直觉清晰。

#### 3.1.3 importance 的新职责

`importance` 字段**保留**，但职责变化：

- `facts.py:315` 的硬丢 `if importance < 5: continue` **移除**。所有
  LLM 抽到的 fact 都存进 facts.json，无论 importance。这样保留完整
  audit trail，未来调阈值时不用回溯 fact 历史。
- 过滤移到消费侧：`get_unabsorbed_facts(min_importance=5)` 在
  `facts.py:363-368` 已经是这种形式，保留即可——reflection 合成只
  挑 importance ≥ 5 的 fact，低 importance 的 fact 存但不流向 reflection。
- Render 时 tiebreaker：`_score_trim_async` 按 `(evidence_score,
  importance)` 双 key 降序排——score 相同时 importance 高的优先留。

这样 LLM 对 importance 的评分只影响"输入门槛"和"同分排序"，**不影响**
status 派生。AI 裁量权被限制在 evidence 机制之外。

#### 3.1.4 派生状态阈值

```python
# config/__init__.py
EVIDENCE_CONFIRMED_THRESHOLD = 1.0
EVIDENCE_PROMOTED_THRESHOLD  = 2.0
EVIDENCE_ARCHIVE_THRESHOLD   = -2.0
```

Mapping:

| `evidence_score(entry, now)` | 派生状态 |
|---|---|
| `score ≤ EVIDENCE_ARCHIVE_THRESHOLD` | archive_candidate |
| `EVIDENCE_ARCHIVE_THRESHOLD < score < EVIDENCE_CONFIRMED_THRESHOLD` | pending |
| `EVIDENCE_CONFIRMED_THRESHOLD ≤ score < EVIDENCE_PROMOTED_THRESHOLD` | confirmed |
| `score ≥ EVIDENCE_PROMOTED_THRESHOLD` | promoted |

"archive_candidate" 是**派生语义**，不是存储字段。真正的归档触发在
§3.5——score < 0 持续累计 `EVIDENCE_ARCHIVE_DAYS` 天才实际归档。
"candidate" 意思是"正在倒计时、如果 score 不回正就会归档"。

`derive_status(entry, now) -> str` 放在 `memory/evidence.py`，纯函数。

#### 3.1.5 `ignored` 扣 0.2 的理由

`check_feedback` 对 user 消息分析 surfaced reflection，有三种结果：

- `confirmed` → reinforcement += 1.0
- `denied` → disputation += 1.0
- `ignored` → **reinforcement -= 0.2**（非 no-op、非 disputation += 0.2）

为什么不是 no-op：实际场景里"surfaced 多次用户不回应"是渐进负面信号——
大概率是不关心 / 觉得无趣 / 觉得不准但懒得反驳。忽略 = 弱负面。

为什么扣在 reinforcement 侧而非 disputation 侧：disputation 语义是
"用户**主动**否认"——`ignored` 强度达不到。reinforcement 减项更准确
表达"曾被肯定过但近期被冷落"。reinforcement 可以为负，语义自洽。

为什么是 `-0.2` 而不是 `-1.0`：ignored 比 denied 弱得多。5 次 ignored
= 1 次 denied 的负面程度。

红线 1 合规性：`ignored` 判定来自 `check_feedback` 对 user 消息的 LLM
分析，属于 "explicit user input"；不是 AI 自作主张。

#### 3.1.6 Schema + migration seed 在新公式下

每种旧 status 迁移后的 evidence seed：

| 旧 status | reinforcement seed | disputation seed | score | 派生状态 |
|---|---|---|---|---|
| pending | 0 | 0 | 0 | pending ✓ |
| confirmed | 1 | 0 | 1 | confirmed ✓ |
| promoted | 2 | 0 | 2 | promoted ✓ |
| denied | 0 | 2 | -2 | archive_candidate ✓ |

每个旧状态的 score 正好落在新 tier 边界，迁移后不会出现"秒归档"或
"秒升格"的意外。详见 §5。

### 3.2 Schema changes

#### 3.2.1 fact schema 不变

`facts.py:334-343` 现行 schema：

```python
{
    'id': 'fact_20260422141530_a1b2c3d4',
    'text': '...',
    'importance': 7,
    'entity': 'master',
    'tags': [],             # 字段保留，新 fact 默认写空；不再由 LLM 填
    'hash': '...',
    'created_at': '...',
    'absorbed': False,
}
```

evidence 计数器**不**加到 fact 层。理由：fact 是原料，不会被 user 直接
反馈（用户反馈的对象是 reflection / persona entry，不是 fact 本身——
fact 对用户透明）。fact 维护 evidence 是浪费存储且无下游消费。

#### 3.2.2 reflection schema +4 字段

`reflection.py:402-413` 现行 schema 扩展（新增字段标 NEW）：

```python
{
    'id': 'ref_abc123',
    'text': '...',
    'entity': 'master',
    'status': 'pending',   # pending | confirmed | promoted | merged
                           # | denied | archived | promote_blocked
                           # (merged 和 promote_blocked 是新 status 值)
    'source_fact_ids': [...],
    'created_at': '...',
    'feedback': None,
    'next_eligible_at': '...',

    'reinforcement': 0.0,                  # NEW, float
    'disputation': 0.0,                    # NEW, float
    'rein_last_signal_at': None,           # NEW, ISO8601 or null
    'disp_last_signal_at': None,           # NEW, ISO8601 or null
    'sub_zero_days': 0,                    # NEW, int, archive 倒计时
    'sub_zero_last_increment_date': None,  # NEW, str ISO date, 防抖

    # 溯源字段 (§3.11)
    'absorbed_into': None,             # NEW, merged 时填 target entry_id
    'last_promote_attempt_at': None,   # NEW, §3.9 节流
    'promote_attempt_count': 0,        # NEW, §3.9 节流
    'promote_blocked_reason': None,    # NEW, 死信原因
}
```

新 status 值说明：

- `merged`：被 `_apromote_with_merge` 的 LLM 决定 merge 到某个现有
  persona entry。不再被主动检索；但 reflection 本身保留（不归档），
  通过 `absorbed_into` 指向吸收它的 persona entry。
- `promote_blocked`：LLM 连续失败到 `EVIDENCE_PROMOTE_MAX_RETRIES`
  次，进入死信状态。等人工或后续 signal 重置。

#### 3.2.3 persona entry schema +4 字段

`persona.py:540-572` 的 `_normalize_entry` defaults 扩展：

```python
{
    'id': '',
    'text': '',
    'source': 'unknown',
    'source_id': None,
    'recent_mentions': [],
    'suppress': False,
    'suppressed_at': None,
    'protected': False,

    'reinforcement': 0.0,                  # NEW
    'disputation': 0.0,                    # NEW
    'rein_last_signal_at': None,           # NEW, ISO8601 or null
    'disp_last_signal_at': None,           # NEW, ISO8601 or null
    'sub_zero_days': 0,                    # NEW
    'sub_zero_last_increment_date': None,  # NEW

    # 溯源字段 (§3.11)
    'merged_from_ids': [],             # NEW, merge 时填被吸收的
                                       #      reflection id 列表
}
```

`protected=True` 的条目（源自 character_card）evidence 字段保留但
永不被使用——`evidence_score()` 对它们返回 `float('inf')`（见 §3.5），
豁免衰减 / 归档 / budget 淘汰。

#### 3.2.4 `recent_mentions` / `suppress` / `suppressed_at` 不动

这三个字段的现行语义与 evidence 正交：

- `recent_mentions`：AI 在 response 里提到的时戳列表（最近
  `SUPPRESS_WINDOW_HOURS=5` 小时内 `persona.py:44`）
- `suppress`：`recent_mentions` 超过 `SUPPRESS_MENTION_LIMIT=2` 次时
  置 True，表示该 entry 被 AI 提及过频，暂不主动提
- `suppressed_at`：suppress 开始时间戳，冷却期 `SUPPRESS_COOLDOWN_HOURS=5`

这是"反向抑制"机制——AI 提太多 → 主动静默。与 user signal 驱动的
evidence 完全独立。两套机制同时存在：evidence 管"用户是否还喜欢这
条"，recent_mentions 管"AI 最近是不是刚提过"。

### 3.3 Three new event types

本 RFC 在 `memory/event_log.py:51-70` 的现有 12 个 `EVT_*` 常量和
`ALL_EVENT_TYPES` frozenset 里加 **3 个**新事件类型：

```python
EVT_REFLECTION_EVIDENCE_UPDATED = "reflection.evidence_updated"
EVT_PERSONA_EVIDENCE_UPDATED    = "persona.evidence_updated"
EVT_PERSONA_ENTRY_UPDATED       = "persona.entry_updated"
```

#### 3.3.1 为什么 3 个而不是 2 个

前两个很自然：reflection 和 persona entry 各自的 evidence 字段变化。
第三个 `persona.entry_updated` 是为 §3.9 merge-on-promote 准备的：

当 LLM 决定 "reflection R merge 到 persona entry P" 时，P 的 text 会
被 LLM **改写**（例如 "主人喜欢猫娘" + "主人爱猫娘" → 合并为 "主人
喜欢猫娘，尤其对猫娘感兴趣"）。只改 evidence 数字字段的
`persona.evidence_updated` 无法承载 text 改写。

所以单独有个 `persona.entry_updated` 事件，full-snapshot 语义承载
entry 的完整 mutation（text + evidence 都可能一起变）。

#### 3.3.2 Event payload

**三个事件都是 full-snapshot pattern**（见 §3.3.4 理由）：

```json
// reflection.evidence_updated
{
  "reflection_id": "ref_abc123",
  "reinforcement": 1.8,
  "disputation": 0.0,
  "rein_last_signal_at": "2026-04-22T14:03:00",
  "disp_last_signal_at": null,                // 这次事件没改 disp
                                               // 保持该侧原状
  "sub_zero_days": 0,
  "source": "user_fact"
  // source ∈ {user_fact, user_rebut, user_confirm, user_ignore,
  //           user_keyword_rebut, migration_seed}
}

// persona.evidence_updated
{
  "entity_key": "master",
  "entry_id": "prom_ref_abc123",
  "reinforcement": 2.0,
  "disputation": 0.0,
  "rein_last_signal_at": "2026-04-22T14:03:00",
  "disp_last_signal_at": null,
  "sub_zero_days": 0,
  "source": "user_confirm"
}

// persona.entry_updated
{
  "entity_key": "master",
  "entry_id": "prom_ref_abc123",
  "rewrite_text_sha256": "...",            // 新 text 的 hash
                                            // 原文不落日志（红线 4）
  "reinforcement": 3.0,                     // merge 后的 evidence
  "disputation": 0.0,
  "rein_last_signal_at": "2026-04-22T14:03:00",
  "disp_last_signal_at": null,
  "sub_zero_days": 0,
  "merged_from_ids": ["ref_abc", "ref_def"],  // 审计追溯
  "source": "promote_merge"
}
```

**两个时间戳字段的 full-snapshot 语义**：事件 payload 都**总是**带两个
时间戳字段的当前值（即写入后应当存在 view 里的值），不管本次事件是
否"触动"了该侧。例如上面第一个事件只触动了 `reinforcement`，
`disp_last_signal_at` 保持为事件发生前的值（这里是 null，因为该 entry
从未收过 disp signal）。apply handler 直接把两个字段覆写到 payload 值即
可——full-snapshot 的一贯语义，不需要 "delta 判定"。

#### 3.3.3 Event 日志的基础合约（本 RFC 自包含描述，不依赖外部 RFC）

下面复述 `memory/event_log.py` 的关键合约，读者不需要翻其他文档：

**`EventLog.(a)record_and_save` 五步法**：任何 evidence 变化都**必须**
走这个入口，不允许绕过直接 mutate view。方法签名：

```python
def record_and_save(
    self, name: str, event_type: str, payload: dict, *,
    sync_load_view: Callable[[str], object],
    sync_mutate_view: Callable[[object], None],
    sync_save_view: Callable[[str, object], None],
) -> str:  # returns event_id
```

内部把**五步**串在一把 per-character `threading.Lock` (`event_log.py:111`)
里、整体包在一个 `asyncio.to_thread` worker 中：

1. `sync_load_view(name)` — 拿当前 view（`reflections.json` /
   `persona.json`）的内存对象
2. **append 事件到 `events.ndjson`**（带 fsync）
3. `sync_mutate_view(view)` — 在内存里改 view
4. `sync_save_view(name, view)` — `atomic_write_json` 落盘
5. **advance sentinel**（写 `events_applied.json`，记 "已 apply 到哪个
   event_id"）

`arecord_and_save` 是 async twin，内部 `asyncio.to_thread` 进 worker
调 sync 版本。

**append 在 mutate 之前的理由**：如果反过来（先 mutate 后 append），
append 失败（fsync OSError / 磁盘满）会留下 cache 已脏但事件没落盘
的状态，后续任一次 normal save 都会把这个"无事件对应的脏改动"刷盘，
破坏 event ↔ view 的对应关系。现在的顺序保证：要么事件 + view 都成、
要么都没动、要么事件成但 view 没成（这种由 reconciler 在下次启动时
补齐）。

**锁嵌套顺序**（防死锁约定）：

`PersonaManager._alocks` / `ReflectionEngine._alocks`（per-character
`asyncio.Lock`，在 `persona.py:110` / `reflection.py:87`）在**外**层；
`EventLog._locks`（per-character `threading.Lock`）在**内**层。

所有 `apply_signal` / `amerge_into` 方法都先 `async with self._get_alock(name)`
拿到 async 锁，再 `await arecord_and_save(...)`——后者内部
`asyncio.to_thread` 进 worker、worker 拿 `threading.Lock`。方向**不能
反**，否则 sync 锁持锁跨 await 边界进 async 锁会引入死锁风险。

#### 3.3.4 Reconciler 重放机制（本 RFC 自包含描述）

启动期 `Reconciler.areconcile(name)` 的工作：

1. 读 sentinel `events_applied.json` 拿 "上次 apply 到哪个 event_id"
2. 从 `events.ndjson` 读未 apply 的尾部事件
3. 对每条事件，查 `self._handlers[event_type]` 拿到对应 handler
4. 调 handler（**同步调用**，见下）
5. handler 返回成功 → 更新 sentinel 往前推一条
6. handler 抛异常 → 停止整个 reconcile 循环，保留 sentinel 在上一条
   成功位置；未知 event_type 也停（避免静默跳过未来新事件）

**Handler 必须 sync**：`event_log.py:97` 定义 `ApplyHandler = Callable[[str, dict], bool]`（无 async）。Reconciler 直接同步调用，async handler
会返回一个从不 await 的 coroutine，等于静默失败。

**Handler 的幂等契约**：每个 handler 必须支持重复 apply 同一事件，结果
一致（不产生漂移）。payload 是 full-snapshot 则天然幂等（重放 = 覆写
到同一组值）。

#### 3.3.5 为什么 full snapshot 而非 delta

事件日志会在崩溃恢复 / 多次重启时被 reconciler 回放。如果 payload 是
delta（如 `{"reinforcement_delta": +1.0}`），重放两遍同一事件就会
double-count，evidence 错涨。

snapshot payload 的话，重放就是"用快照覆写当前 view"——天然幂等：
第一次 apply 改成 X，第二次再 apply 还是 X，第 100 次还是 X。

成本：payload 多几十 bytes（3 个 float + 1 ISO 字符串），10K 事件日志
里 ~400KB 额外体积，可忽略。

#### 3.3.6 三个新 handler 的行为

```python
# memory_server.py 启动期（本 RFC 实现 PR 一并补）
reconciler.register(EVT_REFLECTION_EVIDENCE_UPDATED, _apply_reflection_evidence)
reconciler.register(EVT_PERSONA_EVIDENCE_UPDATED,    _apply_persona_evidence)
reconciler.register(EVT_PERSONA_ENTRY_UPDATED,       _apply_persona_entry)
```

| 事件 | apply 行为 | 幂等保证 |
|---|---|---|
| `reflection.evidence_updated` | load reflections.json → 找 `reflection_id` 条目 → 覆写 4 个 evidence 字段到 payload 值 → `atomic_write_json` save | 覆写到固定值，重放 N 次结果一致 |
| `persona.evidence_updated` | load persona.json → 找 `(entity_key, entry_id)` → 覆写同 4 字段 | 同上 |
| `persona.entry_updated` | load persona.json → 找条目 → 校验现有 text 的 sha256 是否等于 `rewrite_text_sha256` —— 等则 no-op（已 apply 过）；不等则**抛错**（view 漂了、reconciler 救不了、需人工） | text 不在 log 里，靠 sha256 比对判定状态 |

`persona.entry_updated` 的 sha256 机制有一个已知取舍（见 §6 open
question）：原文不放 payload 是为了不在事件日志里留 plaintext（红线 4）；
代价是如果 view save 失败 + event append 成功，重启后 reconciler 看到
sha256 对不上但又没法从 log 重建 text——这种情况只能停 reconciler 让人
查。生产里这路径罕见（atomic_write_json 原子性够强），本 RFC 接受。

#### 3.3.7 sources 的枚举值

`source` 字段是固定集合，便于审计查询：

- `user_fact`：来自 Stage-2 signal detection 的 reinforces / negates
- `user_confirm` / `user_rebut` / `user_ignore`：来自
  `check_feedback` 三种结果
- `user_keyword_rebut`：来自负面关键词 hook + LLM target check
- `migration_seed`：首次启动时的一次性迁移 seed（§5）
- `promote_merge`：`persona.entry_updated` 专用，来自 `_amerge_into`

source 值加进 event_log 的 `ALL_EVENT_TYPES` 校验外的一个单独 set
（实现为 module-level constant）方便未来扩展。

### 3.4 Signal detection (P-A)

本节定义唯一能移动 evidence 计数器的路径。其他任何代码路径都**不允许**
写 evidence 字段（红线 1）。

#### 3.4.1 信号源 + 映射表

| # | Signal 源 | 触发点 | 目标 | Evidence 变化 | source 值 |
|---|---|---|---|---|---|
| 1 | Stage-2 LLM 判 reinforces | `FactStore.aextract_facts_and_detect_signals` 第二次调用 | reflection / persona entry | `reinforcement += 1.0` | `user_fact` |
| 2 | Stage-2 LLM 判 negates | 同上 | 同上 | `disputation += 1.0` | `user_fact` |
| 3 | `check_feedback` returns `confirmed` | `ReflectionEngine.check_feedback` (`reflection.py:557`) | 该 reflection 自身 | `reinforcement += 1.0` | `user_confirm` |
| 4 | `check_feedback` returns `denied` | 同上 | 同上 | `disputation += 1.0` | `user_rebut` |
| 5 | `check_feedback` returns `ignored` | 同上 | 同上 | `reinforcement -= 0.2` (可负) | `user_ignore` |
| 6 | 本地负面关键词命中 → LLM 判 target | 对话主路径 `/process` hook | LLM 返回的 target 列表 | `disputation += 1.0` | `user_keyword_rebut` |

每次 evidence 变化：

1. 根据 signal 的 delta 判断**只触动哪一侧**：
   - 若 `delta['reinforcement'] != 0` → 更新 `reinforcement` +
     `rein_last_signal_at = now`；`disp_last_signal_at` **不动**
   - 若 `delta['disputation'] != 0` → 更新 `disputation` +
     `disp_last_signal_at = now`；`rein_last_signal_at` **不动**
   - 一次 signal 通常只触动一侧。同时触动两侧的场景目前没有
     （Stage-2 返回 reinforces 或 negates 之一；check_feedback 返回
     三选一；keyword 命中走 disputation）。如果未来出现双侧同步
     触动的场景，两个时间戳都重置。
2. 通过 `PersonaManager.aapply_signal` / `ReflectionEngine.aapply_signal`
   发出对应 `*.evidence_updated` 事件（full-snapshot payload，**两个**
   时间戳字段当前值一起写进 payload）
3. Handler 覆写 view 字段并落盘（§3.3.6）

**独立时钟设计的单元测试要求**：给定 `rein=3, rein_last_signal_at=30天前,
disp=0, disp_last_signal_at=None`，apply 一次 `disp += 1` signal 之后：
`effective_reinforcement(now) ≈ 1.5`（不回弹），`effective_disputation
(now) = 1.0`，`evidence_score = 0.5`。

#### 3.4.2 Stage-1 + Stage-2 两次独立 LLM 调用

**核心设计**：把"抽取新 fact"和"判 signal 映射"拆成**两次独立的 LLM
调用**，职责分离。

**为什么拆**：

原草案曾想让 LLM 一次调用同时输出 `{text, importance, entity}` +
`reinforces` / `negates`。这有结构性问题：

- fact **应该**完全来自 user 消息，不能掺入已有 reflection / persona 的
  内容（否则 LLM 可能把"已有观察"当"新 fact"摘出来，形成自循环）
- 但判 reinforces / negates **必须**看到已有观察清单，否则 LLM 不知
  道强化 / 反驳的对象是什么
- 单次调用承载两种职责 → prompt 结构矛盾（要给 context 又不能让它
  污染抽取），LLM 容易在模糊指令下串味
- 职责拆开后各自 prompt 简单、各自 LLM tier 可独立选（Stage-1 更
  简单、Stage-2 需要更强推理）

**流程**（都封在 `FactStore.aextract_facts_and_detect_signals()`
方法里，调用方无感）：

```python
async def aextract_facts_and_detect_signals(
    self, lanlan_name: str, new_user_messages: list[str],
) -> tuple[list[dict], list[dict]]:
    """
    Returns: (new_facts, signals)
      new_facts: 新抽取的 fact 列表（已写入 facts.json）
      signals: [{source_fact_id?, target_type, target_id,
                 signal: "reinforces" | "negates", reason}]
    """

    # Stage-1：从 user 消息抽新 fact（不带任何已有观察作 context）
    new_facts = await self._allm_extract_facts(new_user_messages)
    # 输出：[{text, importance, entity}, ...]

    if new_facts:
        # 落盘（沿用现行 dedup 逻辑：SHA-256 + FTS5）
        persisted = await self._apersist_new_facts(lanlan_name, new_facts)

        # Stage-2：new_facts + 已有观察 → 判 signal 映射
        existing_observations = await self._aload_signal_targets(lanlan_name)
        # [{id: "persona.master.p_001", text: "主人喜欢猫娘"},
        #  {id: "reflection.r_023", text: "主人最近在学日语"}, ...]
        # 覆盖：confirmed + promoted reflection 全量 + 非 protected
        #      persona entry 全量

        signals = await self._allm_detect_signals(persisted, existing_observations)
    else:
        persisted = []
        signals = []

    return persisted, signals
```

**Stage-2 契约**：

Input:
- `new_facts`: Stage-1 刚抽并落盘的新 fact 列表
- `existing_observations`: 当前可见观察（id + text）

Output:
```json
{
  "signals": [
    {
      "source_fact_id": "fact_20260422141530_abcd",   // 可选
      "target_type": "persona" | "reflection",
      "target_id": "p_001",
      "signal": "reinforces" | "negates",
      "reason": "新事实'主人爱猫娘'强化了已有观察'主人喜欢猫娘'"
    }
  ]
}
```

`source_fact_id` 可选：如果 LLM 判断 signal 是整批 user 消息的语义
共识而非某条具体新 fact 导致，可以不填。下游 dispatch 只用 `target_*`
字段。

**dispatch 逻辑**：

```python
# memory_server.py 背景循环
new_facts, signals = await fact_store.aextract_facts_and_detect_signals(name, msgs)

# 按 signals 列表 dispatch evidence 更新
for s in signals:
    # 本地防御：target_id 必须在 existing_observations 里存在
    # （防 LLM 编造）
    if not _target_exists(s['target_id'], existing_observations):
        logger.warning(
            f"[Signal] LLM 返回未知 target_id: {s['target_id']}，丢弃"
        )
        continue

    delta = {'reinforcement': +1.0} if s['signal'] == 'reinforces' \
            else {'disputation': +1.0}

    if s['target_type'] == 'persona':
        entity_key, entry_id = _parse_persona_target(s['target_id'])
        await persona_manager.aapply_signal(
            name, entity_key, entry_id, delta, source='user_fact',
        )
    elif s['target_type'] == 'reflection':
        await reflection_engine.aapply_signal(
            name, s['target_id'], delta, source='user_fact',
        )
    else:
        logger.warning(f"[Signal] unknown target_type: {s['target_type']}")
```

**Stage-2 prompt 的 existing_observations 规模控制**：

persona + reflection 若很大（500+ 条），全塞进 prompt 会膨胀到 10K+
tokens。策略：

- 优先：**按 entity 过滤**——若 Stage-1 抽的 new_facts 都是
  `entity=master`，Stage-2 只传 `entity=master` 的 existing_observations
- 若单 entity 仍太多：按 `evidence_score` DESC 取前
  `EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS=200` 条
- 丢弃的条目意味着"这条 observation 不会在本轮被 reinforce/negate"——
  可接受（signal 是 best-effort，漏一次没关系）

**LLM tier**：

- Stage-1（extract）：`EVIDENCE_EXTRACT_FACTS_MODEL_TIER` —— §6.5 Gate 3 敲定
- Stage-2（detect signals）：`EVIDENCE_DETECT_SIGNALS_MODEL_TIER` —— 同上

**成本估算**（两次 LLM 合计）：

- Stage-1：~500 tokens in + ~200 tokens out
- Stage-2：~500 (new_facts) + ~2000 (100 条 existing) + ~300 out = 2800
- 合计 ~3500 tokens/次；每小时 ~10 次 → ~35K/h/角色 → ~840K/天/角色
- 按 Stage-1 qwen-plus + Stage-2 qwen-max 粗估 → < ¥3/天/角色

**失败降级**：

- Stage-1 失败 → 整次 abort，新 fact 也不写入；下次 idle 触发再试
- Stage-2 失败 → 新 fact 已落盘（Stage-1 成功的副作用保留），但 signal
  全部 drop。evidence 未变化。不回滚 Stage-1（facts.json 新增的条目
  仍然是有效的 fact，只是没触发本轮 signal——下次循环会继续扫它们
  的 signal 判定）

#### 3.4.3 背景循环：触发时机

**不**在对话主路径上每轮运行 extract_facts——太贵。改为背景调度：

- 复用 `memory_server.py:680 (_periodic_idle_maintenance_loop)` 的
  基础设施（空闲检测 + 按角色迭代）
- **独立开关** `EVIDENCE_SIGNAL_CHECK_ENABLED = True`，与现有
  `ais_review_enabled()` 解耦——用户可以单独关掉 signal 抽取而保留
  其他 idle 维护任务
- 触发条件（OR）：
  - 自上次 check 累积 ≥ `EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS=10`
    轮对话，或
  - 自最后一次用户消息起 ≥ `EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES=5`
    分钟
- 每次 check 的范围：自上次 check 以来的新消息。游标存储在新文件
  `memory/<char>/signal_cursor.json`，模式参考 `memory/cursors.py`
  的 `CursorStore`
- 错过窗口不补：如果 10 轮累积但系统忙，下次 idle 再处理；累积量
  **只增不减**

**游标推进**：每次 `aextract_facts_and_detect_signals` 成功返回后，
游标推进到本批消息的最后一条时戳。Stage-1 失败则游标不推进，下次
重试。

**和现有 `_periodic_auto_promote_loop` / `_periodic_rebuttal_loop`
的关系**：

- `_periodic_auto_promote_loop` (`memory_server.py:651`) 的时间跳级
  promote 逻辑**删除**（红线 2）。但 loop 本身保留——改跑
  `_apromote_with_merge` 对 score ≥ PROMOTED_THR 的 reflection
- `_periodic_rebuttal_loop` (`memory_server.py:569`) 不动，仍用来处理
  现有 reflection 被 surface 后的 check_feedback 回路
- 新增 `_periodic_signal_extraction_loop` 专职跑 §3.4 的 Stage-1+2
- 新增 `_periodic_archive_sweep_loop` 专职 §3.5 的归档判定

4 个 periodic loop 都挂在 `memory_server.py:326 (_spawn_background_task)`
辅助上启动，相互独立、不共享锁。

#### 3.4.4 `_texts_may_contradict` 该用在哪、不该用在哪

`memory/persona.py:697 (_texts_may_contradict)` 是个**关键词重叠启发
式**——CJK n-gram 切词后看两段文字共享词比例，超过 `SIMILARITY_THRESHOLD
= 0.6` 就认为"可能矛盾"。这是粗糙的**字面相似度**检测，**不是**真正
的语义判定。

现行代码里它被用在两个地方：

1. **`PersonaManager.aadd_fact()` 插入新 entry 时**：检查新 fact 跟
   persona 里现有的 entry 是否字面冲突。
   - 如果跟 character_card 来源的条目（`source='character_card'`）冲
     突 → 直接拒绝插入，返回 `FACT_REJECTED_CARD`（`persona.py:577`）。
     这就是"角色卡有最终决定权"——角色卡里写死的人设比新观察优先级
     高，新观察被拒就拒了。
   - 如果跟非角色卡条目冲突 → 写到 `persona_corrections.json` 排队，
     由 idle 背景循环里的 `resolve_corrections` 跑 LLM 复核。
2. **历史上**还可能被间接拿它的"判矛盾"结果做下游决策（比如反推
   "用户对该话题反感"之类，或推导 evidence 变化）。

**本 RFC 的边界**：

- **用法 1 保留**——这是 persona 内部一致性维护，不涉及 evidence，与
  user signal 无关。
- **用法 2 禁止**。`_texts_may_contradict` 是 **AI 在替用户判断** "两段
  文字是否矛盾"，它的输出**不是 user signal**，**不能**用来推导
  reinforcement / disputation 的变化。否则违反红线 1。
- 代码层保留 `_texts_may_contradict` 函数本身不删，但调用方只允许
  `aadd_fact()` / `_evaluate_fact_contradiction()` (`persona.py:581`)
  这一个调用链。本 RFC 实现 PR 包含 grep 审查：若其他文件调用了它，
  审查是否想拿它做 evidence 推导？是的话改掉。

#### 3.4.5 本地负面关键词 + LLM 二次判定

用户说"别提了"/"换个话题"这类话是**强负面信号**，不应该等下一次
`_periodic_signal_extraction_loop` 的 5 分钟窗口才处理。但纯关键词
命中直接 blanket disputation 会误伤：

- `"别再说这个梗，我要笑死了"` — 实际是正面强化
- `"别提了，算了"` — 可能是话题疲劳，也可能是敷衍认可
- `"换个话题吧"` — 针对刚提的某件事，还是整体想换？

所以设计成**"关键词做快速触发、LLM 做精确 target 判定"**的双层结构。

**Layer 1 — 关键词扫描**（本地、零成本、零延迟）

```python
# config/prompts_memory.py 新增常量
NEGATIVE_KEYWORDS_I18N = {
    'zh': frozenset([
        "别再说", "别说了", "别提", "不想聊", "换个话题",
        "这个不用说了", "别聊这个",
    ]),
    'en': frozenset([
        "stop talking about", "don't mention", "change the topic",
        "let's not discuss", "drop the subject",
    ]),
    'ja': frozenset([
        "その話は", "やめて", "話題を変えて",
    ]),
    'ko': frozenset([
        "그만하자", "다른 이야기",
    ]),
    'ru': frozenset([
        "хватит об этом", "сменим тему",
    ]),
}
```

扫描位置：对话**主路径**上的 hook——`/process` 处理 user 消息时，在
开始 LLM 生成之前跑一个 `any(kw in msg for kw in NEGATIVE_KEYWORDS_I18N[lang])`
快速检查。这**不阻塞对话**：命中后异步派发 Layer 2，当前轮回话不等。

**Layer 2 — LLM 语义判断**（异步、快速模型）

关键词命中后派发一次小 LLM 调用 `check_negative_target`。prompt（zh
示例）：

```
你是一个情感分析专家。

======用户最近消息======
{user_messages_last_3}
======以上为用户最近消息======

======系统正在维护的观察列表======
[当前 surfaced reflection (feedback=null)]
  [reflection.r_023] 主人最近在学日语
  [reflection.r_045] 主人下午打球扭伤了脚

[最近 N 轮被 mention 的 persona fact]
  [persona.master.p_001] 主人喜欢猫娘
  [persona.master.p_088] 主人 23 岁
======以上为观察列表======

用户消息里，"别提了 / 不想聊 / 换个话题"这类表达到底指上述哪一条？
可能多条、也可能一条都没有（用户只是泛化情绪）。

返回 JSON：
{"targets": [{"target_type": "reflection"|"persona",
              "target_id": "...",
              "reason": "..."}]}
空数组表示无明确 target。
```

Output 处理：

- LLM 返回空数组 → no-op
- 返回非空列表 → 逐条 `disputation += 1.0`，source=`user_keyword_rebut`

**LLM tier**：`EVIDENCE_NEGATIVE_TARGET_MODEL_TIER` — §6.5 Gate 3
敲定。本路径延迟敏感（用户说"别提了"后应 <3s 给反应）——候选偏
qwen-flash 级别。

**成本估算**：每次 ~500 tokens in + ~50 tokens out；关键词命中频
率估计 <5 次/小时/角色 → 日均 <10K tokens/角色。

**失败降级**：LLM 超时 / 非法 JSON → **不**加任何 disputation、不
崩溃、log warning。用户看到的行为：这一次"别提了"没被系统 pick up，
但对话主路径不受影响。下次用户再触发关键词时重试。

**为什么不直接每轮用 LLM 分析 user 消息负面情绪**：关键词命中是
确定性信号，代价为 0；每轮都过 LLM 把负面检测从"稀疏触发"变成
"密集触发"，成本翻 10 倍以上。关键词是 fast path trigger，LLM 是
精确层，两级协同。

#### 3.4.6 Prompt 设计要求（新增 prompt 共享的规则）

本 RFC 新增或改造 **4 个** prompt：

1. **`FACT_EXTRACTION_PROMPT`**（`config/prompts_memory.py:699` 现有，
   改造）—— Stage-1
2. **`SIGNAL_DETECTION_PROMPT`**（新增）—— Stage-2
3. **`NEGATIVE_TARGET_CHECK_PROMPT`**（新增）—— 负面关键词 Layer 2
4. **`PROMOTION_MERGE_PROMPT`**（新增，§3.9）—— Merge-on-promote

所有 4 个都按项目约定以 5 语言 i18n dict 形式声明（zh / en / ja / ko / ru）。

**安全水印要求**（防 prompt injection 检测）：每条 prompt（每语言版本
各自算一条）必须包含以下字符串**至少一个**：

- `"======以上为"`（现行 `FACT_EXTRACTION_PROMPT` 已用此模式）
- `"你是一个情感分析专家"`
- `"sends some useful information"`
- `"你是一个图像描述助手, "`
- `"automation assessment agent, "`
- `"careful deduplication judge."`

选择建议（按 prompt 用途）：

| Prompt | 推荐水印 |
|---|---|
| `FACT_EXTRACTION_PROMPT` | 保留现有 `"======以上为对话======"` 结构 |
| `SIGNAL_DETECTION_PROMPT` | `"careful deduplication judge."` + `"======以上为已有观察======"` |
| `NEGATIVE_TARGET_CHECK_PROMPT` | `"你是一个情感分析专家"`（zh）/ `"sends some useful information"`（其它语言可统一用后者） |
| `PROMOTION_MERGE_PROMPT` | `"careful deduplication judge."` |

每条 prompt 的 5 语言版本可以**统一用同一个** watermark 字符串，不要
求每语言各自本地化 watermark。但每条必须出现**至少一次**。

#### 3.4.7 Stage-1 / Stage-2 prompt 具体内容

**`FACT_EXTRACTION_PROMPT` 改造**（只抽新 fact，不带 context，不要
tags / reinforces / negates）

zh 版本（模板，5 语言同步）：

```
从以下对话中提取关于 {LANLAN_NAME} 和 {MASTER_NAME} 的重要事实信息。

要求：
- 只提取重要且明确的事实（偏好、习惯、身份、关系动态等）
- 忽略闲聊、寒暄、模糊的内容
- 忽略AI幻觉、胡言乱语、无意义的编造内容
- 每条事实必须是一个独立的原子陈述
- importance 评分 1-10
- entity 标注为 "master"、"neko" 或 "relationship"

======以下为对话======
{CONVERSATION}
======以上为对话======

请以 JSON 数组格式返回：
[
  {"text": "事实描述", "importance": 7, "entity": "master"},
  ...
]
```

与现行版本的 diff（`config/prompts_memory.py:699-718`）：

- **删** "只返回 >= 5 的事实" 过滤要求（§3.1.3 新定位：低 importance
  也存，消费侧过滤）
- **删** `tags` 字段（§2.7 话题级 V1 不做）
- **不加** reinforces / negates（那是 Stage-2）

水印保留现行 `"======以上为对话======"` 结构。

**`SIGNAL_DETECTION_PROMPT` 新增**

zh 版本：

```
你是一个 careful deduplication judge。给你一组新提取的事实，和一组
系统已经记录过的观察，请判断每条新事实对已有观察的关系。

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

水印：`"careful deduplication judge."` + `"======以上为新事实"` / `"======以上为已有观察"`。

**`NEGATIVE_TARGET_CHECK_PROMPT` 新增**

见 §3.4.5 prompt 示例。水印：`"你是一个情感分析专家"`。

**`PROMOTION_MERGE_PROMPT` 新增**

见 §3.9 prompt 示例。水印：`"careful deduplication judge."`。

#### 3.4.8 防 LLM 编造 ID 的通用策略

Stage-2 和 `NEGATIVE_TARGET_CHECK_PROMPT` 都让 LLM 返回 `target_id`。
LLM 偶尔会编造不存在的 ID（特别是在 context 很长时）。本地防御：

```python
# 所有返回 target_id 的 LLM 调用都走这个防御
def _validate_targets(returned_ids: list[str], valid_set: set[str]) -> list[str]:
    valid = []
    for tid in returned_ids:
        if tid in valid_set:
            valid.append(tid)
        else:
            logger.warning(f"[Signal] LLM 返回未知 target_id: {tid}，丢弃")
    return valid
```

`valid_set` 由调用方在发起 LLM 请求前构造（就是传进 prompt 的那组
ID）。任何不在这个集合里的返回值都被丢弃。

这个防御也用于 `SIGNAL_DETECTION_PROMPT` 和 `PROMOTION_MERGE_PROMPT`
的 target_id 解析。

### 3.5 Time decay + archiving (P-C)

#### 3.5.1 Read-time decay

**Decay 是计算，不是状态转移**——在**读**时现算 effective 值，**不**
写盘、**不**发事件。这是红线 5 的关键：若 decay 走写路径，每角色每
天会产出 N 条 `*.evidence_updated` 事件污染 event log。

实现：放在 `memory/evidence.py` 作为 module-level 纯函数，无锁、无
I/O：

```python
# memory/evidence.py
from config import (
    EVIDENCE_REIN_HALF_LIFE_DAYS,
    EVIDENCE_DISP_HALF_LIFE_DAYS,
)

def _age_days(ts: str | None, now: datetime) -> float:
    if not ts:
        return 0.0
    return (now - datetime.fromisoformat(ts)).total_seconds() / 86400

def effective_reinforcement(entry: dict, now: datetime) -> float:
    r = float(entry.get('reinforcement', 0.0))
    if r == 0.0:
        return r
    # 独立时间戳：rein_last_signal_at 只在 reinforcement 侧被触动时
    # 重置；disp 事件不影响本函数计算
    age = _age_days(entry.get('rein_last_signal_at'), now)
    if age == 0.0:
        return r
    return r * (0.5 ** (age / EVIDENCE_REIN_HALF_LIFE_DAYS))

def effective_disputation(entry: dict, now: datetime) -> float:
    d = float(entry.get('disputation', 0.0))
    if d == 0.0:
        return d
    age = _age_days(entry.get('disp_last_signal_at'), now)
    if age == 0.0:
        return d
    return d * (0.5 ** (age / EVIDENCE_DISP_HALF_LIFE_DAYS))

def evidence_score(entry: dict, now: datetime) -> float:
    if entry.get('protected'):
        return float('inf')   # protected 永不被淘汰 / 归档
    return effective_reinforcement(entry, now) - effective_disputation(entry, now)
```

所有 render / archive / promote 判定的调用点都走这三个函数，**不要**
绕过去直接读 `entry['reinforcement']`。

#### 3.5.2 两个半衰期的语义设计

- `EVIDENCE_REIN_HALF_LIFE_DAYS = 30`：reinforcement 衰到一半。
  典型情境：用户一个月没再提的喜好，渐渐"冷却"——persona 条目热度
  下降，render 时被挤出 token 预算，但条目本身不归档（score 仍 ≥ 0）。
- `EVIDENCE_DISP_HALF_LIFE_DAYS = 180`：disputation 衰到一半。
  典型情境：用户半年前说过"不喜欢 X"，半年后如果没再强化反对，否定
  态度弱化到 1/2；若那时有新的 reinforces 到来，可能回正。

比值 6 倍体现"否认比肯定持久"的语义：否认信号强、留痕长；但不应永恒，
给用户态度转变的可能。

两者**各自独立**的时间起点（`rein_last_signal_at` / `disp_last_signal_at`）。
对应侧的 signal 到来时重置该侧时间戳，另一侧不动。这样避免 "用 negate
刷新了 reinforce 的衰减时钟" 这类误差——见 §3.1.1 末段的场景说明。

两个半衰期的具体数值是草案，见 §6.5 Gate 1 reviewer 敲定。

#### 3.5.3 归档触发（归档更积极）

**归档触发条件**：entry 的 `evidence_score < 0` 累计达 `EVIDENCE_ARCHIVE_DAYS`
天。

**关键设计选择**（reviewer 选的 "归档更积极" 方向）：累计计数，**不**
清零。即使 score 在累计期间回正，`sub_zero_days` 计数器继续保留，
不从 0 重新开始。

实现：

```python
# memory/evidence.py
def maybe_mark_sub_zero(entry: dict, now: datetime) -> bool:
    """
    背景循环每次扫描时调用。
    Returns True if entry['sub_zero_days'] was incremented this call.
    """
    if entry.get('protected'):
        return False
    score = evidence_score(entry, now)
    if score < 0:
        # 防抖：一天多次扫不重复加
        last_incr = entry.get('sub_zero_last_increment_date')
        today = now.date().isoformat()
        if last_incr != today:
            entry['sub_zero_days'] = int(entry.get('sub_zero_days', 0)) + 1
            entry['sub_zero_last_increment_date'] = today
            return True
    # score >= 0：**不清零** sub_zero_days（累计不回退）
    return False

# 归档判定：
if entry.get('sub_zero_days', 0) >= EVIDENCE_ARCHIVE_DAYS:
    await _aarchive_entry(name, entry)
```

`sub_zero_last_increment_date` 是防抖字段，避免背景循环一天跑多次
重复 +1。字段本身也持久化到 view 文件。

**字段命名 rationale**：用 `sub_zero_days`（累计整数）而非 `sub_zero_since`
（ISO date）——因为"累计"语义需要计数器，不是时戳。

#### 3.5.4 归档分布式存储

每次归档创建**新分片文件**：

```
memory/<character>/persona_archive/
  ├── 2026-04-22_a1b2c3d4.json
  ├── 2026-04-22_e5f6g7h8.json
  ├── 2026-04-23_...
  └── ...

memory/<character>/reflection_archive/
  └── ...
```

文件命名：`<YYYY-MM-DD>_<uuid8>.json`，日期是归档发生的日期，uuid8 是
生成时的 uuid4 前 8 位。

**分片大小上限** `ARCHIVE_FILE_MAX_ENTRIES = 500` — 草案值，见 §6.5
Gate 4（低风险默认可放过）。每分片最多 500 条；满了下一次归档新开
分片。

**为什么分片**：避免随年累月累积成单个超大 `reflections_archive.json`
文件（想象 5 年后 10 万条归档条目，单文件 100MB+，读/写都慢）。分片
让单文件体积受控、按日期扫易于人工翻找。

#### 3.5.5 老数据迁移

现行 `reflection.py:122 (_reflections_archive_path)` 使用 flat 文件
`memory/<char>/reflections_archive.json`。本 RFC P-C 首次启动时：

- 检测到旧 flat 文件 → 按 `archived_at` 日期拆分迁移到 `reflection_archive/`
  目录
- 每个日期一个或多个分片（按 `ARCHIVE_FILE_MAX_ENTRIES` 切），uuid8
  后缀现场生成
- 迁移失败（I/O 错、磁盘满等） → 保留 flat 文件 fallback，log error
- 迁移成功 → 删除旧 flat 文件、emit 一条 log

`persona_archive/` 是新目录，persona 原本没有归档文件，不需要迁移。

#### 3.5.6 Archive 用什么事件类型

本 RFC **不**新增 `EVT_REFLECTION_ARCHIVED` / `EVT_PERSONA_ARCHIVED`
事件类型。归档本质是 state transition，复用现有事件：

- Reflection archive：`EVT_REFLECTION_STATE_CHANGED` 的 payload
  带 `{from: old_status, to: 'archived'}`，entry 从 `reflections.json`
  移到 `reflection_archive/<date>_<uuid8>.json`
- Persona archive：`EVT_PERSONA_FACT_ADDED` 但 target 文件是 archive
  分片（payload 带 `archive_shard_path` 字段区分）

12 个原 event type + 3 个本 RFC 新增 = 15 个够了，没必要再增归档专用
事件。

归档后字段追加：`archived_at`（ISO8601）和 `archive_shard_path`（相对路径
字符串），供后续主动检索 / debug 用。

#### 3.5.7 `protected=True` 豁免

`evidence_score` 对 `protected=True` 的 entry 返回 `float('inf')`：

- `score < 0` 永远不触发 → sub_zero_days 永远不累加 → 永不归档
- Render 时 `_score_trim_async` 的 `evidence_score DESC` 排序把
  protected 条目永远排最前，不会被预算淘汰
- 但 protected 条目**不**进 Phase 2 的预算核算（见 §3.6）——它们
  永远全量渲染、不计 token

### 3.6 Render budget (P-D)

#### 3.6.1 Budget 作用域 + 双预算

persona 和 reflection **分别独立** 预算，互不影响：

```python
# config/__init__.py
PERSONA_RENDER_TOKEN_BUDGET    = 2000   # 非-protected persona entry 总预算
REFLECTION_RENDER_TOKEN_BUDGET = 1000   # pending+confirmed reflection 总预算
PERSONA_RENDER_ENCODING        = "o200k_base"
```

预算适用范围：

- `protected=True` 条目（character_card 来源）**不计入**任一预算、
  **永远全量渲染**。理由：用户写死的角色设定不应该被"用户又 denied
  了几条"的动态预算机制挤出——这不符合 character_card 的语义。
- persona 和 reflection 独立预算：一边溢出不影响另一边渲染。
- suppressed section（`recent_mentions` 机制触发的静默区）不计入
  预算——体量小，且本身就是"最近提太多"的静默提示。

#### 3.6.2 三阶段渲染流程

```python
# memory/persona.py:1118 (arender_persona_markdown)
async def arender_persona_markdown(
    self, name: str, pending_refs=None, confirmed_refs=None,
) -> str:
    persona = await self.aensure_persona(name)
    now = datetime.now()

    # ─── Phase 1：split ───
    protected_entries = []
    non_protected_by_entity = defaultdict(list)
    for entity_key, section in persona.items():
        if not isinstance(section, dict):
            continue
        for entry in section.get('facts', []):
            if entry.get('protected'):
                protected_entries.append((entity_key, entry))
            else:
                non_protected_by_entity[entity_key].append(entry)

    # ─── Phase 2：score-trim per-section ───
    # persona：所有非-protected 共用同一个 2000 token 预算
    all_non_protected = [
        (ek, e)
        for ek, entries in non_protected_by_entity.items()
        for e in entries
    ]
    trimmed_persona_by_entity = await self._ascore_trim_persona(
        all_non_protected,
        budget=PERSONA_RENDER_TOKEN_BUDGET,
        now=now,
    )

    # reflection：pending + confirmed 共用 1000 token 预算
    all_reflections = (pending_refs or []) + (confirmed_refs or [])
    trimmed_reflections = await self._ascore_trim_reflections(
        all_reflections,
        budget=REFLECTION_RENDER_TOKEN_BUDGET,
        now=now,
    )

    # ─── Phase 3：compose markdown ───
    # 沿用现有 _compose_persona_markdown 结构:
    #   "### 关于主人" | "### 关于{ai_name}" | "### 关系动态"
    #   | 反思两类 section | 抑制区
    return self._compose_markdown(
        protected_entries + list(trimmed_persona_by_entity.values()),
        trimmed_reflections, ...,
    )
```

#### 3.6.3 Score-trim 算法

```python
async def _ascore_trim(
    entries: list, budget: int, now: datetime,
) -> list:
    """按 evidence_score DESC 保留，累计 token <= budget。
    同 score 按 importance DESC tiebreak。"""
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
            break  # 后续更低分直接截断
        kept.append(e)
        total_tokens += t
    return kept
```

#### 3.6.4 为什么没有 Phase 2 LLM 合并

原早期草案曾设想在 token 超 budget 时触发 LLM 合并重组 persona 条目。
**本 RFC 不这么做**，理由：

- 合并（§3.9）只在 reflection promote 时发生。次数可控（<5/天/角色）。
- Render 是 hot path——每轮回话都要渲染。把 LLM 调用放进 render path
  会让每次回话多一次几百 ms 到几 s 的延迟。
- 背景循环也**不做** persona 合并 sweep（reviewer 明确："persona 平时
  不进行纠错/合并，只在 reflection promote 时整理"）。

#### 3.6.5 为什么没有 render cache

- Merge 发生后 target persona entry 的 text / evidence 直接落 persona.json
  （通过 `EVT_PERSONA_ENTRY_UPDATED`），下次读 persona.json 已经是
  合并后态——无需中间缓存。
- Render 路径只做纯计算：排序 + 累加 token + 拼 markdown。所有代价 < 100ms。
- Cache invalidation 是公认的 bug 源；没有明显收益就不引入。
- 如果未来 render 频次远超合并频次、实测 render 是瓶颈——可以加
  `_render_cache` 旁路。V1 不做。

#### 3.6.6 Token 计数：tiktoken + o200k_base

新模块 `utils/tokenize.py`：

```python
# utils/tokenize.py
import asyncio
import logging
from config import PERSONA_RENDER_ENCODING

logger = logging.getLogger(__name__)

_ENCODERS: dict = {}  # encoding name → tiktoken.Encoding
_FALLBACK_WARNED = False

def _get_encoder(encoding: str):
    global _FALLBACK_WARNED
    if encoding in _ENCODERS:
        return _ENCODERS[encoding]
    try:
        import tiktoken
        enc = tiktoken.get_encoding(encoding)
        _ENCODERS[encoding] = enc
        return enc
    except Exception as e:
        if not _FALLBACK_WARNED:
            logger.warning(
                "tiktoken 不可用 (%s)，降级到启发式 token 计数；如果这是"
                "打包产物，请检查 Nuitka/PyInstaller 配置是否包含 tiktoken "
                "encoding 文件", e,
            )
            _FALLBACK_WARNED = True
        return None

def _count_tokens_heuristic(text: str) -> int:
    cjk = sum(
        1 for c in text
        if '\u4e00' <= c <= '\u9fff'
        or '\u3040' <= c <= '\u30ff'
        or '\uac00' <= c <= '\ud7af'
    )
    non_cjk = len(text) - cjk
    return int(cjk * 1.5 + non_cjk * 0.25)  # 过估计偏置，宁少渲染不超 budget

def count_tokens(text: str, encoding: str = PERSONA_RENDER_ENCODING) -> int:
    enc = _get_encoder(encoding)
    if enc is None:
        return _count_tokens_heuristic(text)
    return len(enc.encode(text))

async def acount_tokens(text: str, encoding: str = PERSONA_RENDER_ENCODING) -> int:
    return await asyncio.to_thread(count_tokens, text, encoding)
```

**为什么 o200k_base 而不是 cl100k_base**：2026 年主力模型（GPT-4o / 4.1 /
5 家族 + o1/o3/o4）全部在 o200k 上；cl100k 是 GPT-3.5/4 的旧编码。
CJK 在 o200k_base 下表示更紧凑——同样一段中文，o200k 的 token 数
少 20-30%，对中文为主的 persona 文本计数更准。

CPU 开销方面 o200k 比 cl100k 略慢（词表 2 倍），但在 2000 char 级别
绝对值仍 <10ms（tiktoken Rust 实现），可忽略。

**GIL 说明**：tiktoken 的 Rust 底层在 `encode` 主循环显式释放 GIL
（tiktoken 0.5+ release note 明示）。其他 Python 代码可与之并行。
但 5ms 的调用在 FastAPI event loop 上**仍是阻塞**——event loop 不知
道 Rust 释放了 GIL，自己停转 5ms。所以必须走 `asyncio.to_thread`，
这是 `acount_tokens` 的存在理由。

Render 路径是 async（`arender_persona_markdown` at `persona.py:1118`），
调用 `acount_tokens`，event loop 不阻塞。sync `count_tokens` 仅测试 /
迁移脚本用。

**Fallback 警告必须显式**：开发者打包时如果忘了把 encoding 文件打入
Nuitka 产物，运行时会走 heuristic——必须有 warning 日志提醒，不能
静默。第一次 fallback 触发时 log.warning 一条（后续同进程内不再重复，
避免日志刷屏）。

#### 3.6.7 打包需求

`pyproject.toml` 的 `[project.dependencies]` 加 `tiktoken>=0.7.0`。
`requirements.txt` 是 `uv export` 的导出物，手改没意义——编辑
pyproject + 重新导出即可同步。

Nuitka / PyInstaller 打包配置要把 `tiktoken/encodings/o200k_base.tiktoken`
（~1.5MB）一起打入产物。本 RFC 的 §8 success criteria S13 要求："打
包产物启动后 `count_tokens('测试')` 不触发 heuristic warning"——这是
打包配置是否正确的自检。

#### 3.6.8 批量计数的性能

N ≤ 100 条 entry，每条 ~2ms tokenize → 累计 ~200ms。

用 `asyncio.to_thread` 放线程池，不阻塞 event loop。是否**并行**计数
（`asyncio.gather` 把 N 个 `acount_tokens` 并行）？

**不并行**。rationale：tokenize 是纯 CPU 任务，线程池 N 并行 vs 串行
总时间一样（GIL 释放后 CPU 层面仍排队、单核不会变多核）。只有 I/O
并发才能通过 thread pool 真并行。保持串行代码更简洁。

未来如果 entry 数涨到 500+、render 变慢到不可接受——可以做：

- 一次把整段 markdown tokenize 一次拿到 total
- 按各 entry text length 比例估算 per-entry token
- 这是**优化**不是必须，V1 不做；因为 per-entry 精度影响 trim 决策，
  比例估算偏差会让边界条目反复进出 trim

### 3.7 话题级回避机制：V1 不实现

本节简短：话题级聚合 / 回避机制**整体延后**，V1 RFC 范围内不做。

具体不做的：

- 按话题聚合 evidence 的任何逻辑
- 自动生成"用户不想聊 X 话题"的 persona fact
- 让 fact / reflection / persona 携带 tags 字段（tags 在 fact schema
  上保留位，但新 fact 默认写空数组，下游不消费）
- 任何 `topic_score` / `TOPIC_*` 常量

**为什么整体不做**：让 LLM 在每条 fact 抽取时独立打 tag 是个根本结构
问题——同一话题可能被不同 fact 标成 `"猫娘"` / `"猫"` / `"cat"` /
`"宠物"` 不一致，聚合不到一起。"话题"是相对全局的概念，需要在已有一批
fact / reflection / persona 之后做**全局组织**才有意义（聚类 / embedding
相似度 / 主题模型 / 图数据库）。本质上超出"条目级 evidence"这个 RFC
的作用域。

未来独立 RFC 处理话题级机制时，关键问题：

- 什么时候触发全局组织（多少条目后）
- 话题状态怎么持久化
- 怎么和本 RFC 的条目级 evidence 关联

### 3.8 API surface

#### 3.8.1 新增 event-type 常量

加到 `memory/event_log.py:51-70` 的现有 `EVT_*` 块里、同步加进
`ALL_EVENT_TYPES` frozenset (`event_log.py:64-70`)：

```python
EVT_REFLECTION_EVIDENCE_UPDATED = "reflection.evidence_updated"
EVT_PERSONA_EVIDENCE_UPDATED    = "persona.evidence_updated"
EVT_PERSONA_ENTRY_UPDATED       = "persona.entry_updated"
```

**这三个常量不进 `config/__init__.py`**——event_log 模块内聚其自己的
API surface，沿用 `EVT_FACT_ADDED` 等的现行约定。

#### 3.8.2 新增模块 `memory/evidence.py`

**只放纯函数 + 背景辅助**，**不放常量**（所有常量都在 `config/__init__.py`，
§7 全局约束第 5 条）：

```python
# memory/evidence.py
from datetime import datetime
from config import (
    EVIDENCE_REIN_HALF_LIFE_DAYS,
    EVIDENCE_DISP_HALF_LIFE_DAYS,
    EVIDENCE_CONFIRMED_THRESHOLD,
    EVIDENCE_PROMOTED_THRESHOLD,
    EVIDENCE_ARCHIVE_THRESHOLD,
    EVIDENCE_ARCHIVE_DAYS,
)

def effective_reinforcement(entry: dict, now: datetime) -> float: ...
def effective_disputation(entry: dict, now: datetime) -> float: ...
def evidence_score(entry: dict, now: datetime) -> float: ...
def derive_status(entry: dict, now: datetime) -> str:
    """Returns 'archive_candidate' | 'pending' | 'confirmed' | 'promoted'."""
    ...

def maybe_mark_sub_zero(entry: dict, now: datetime) -> bool:
    """Background-loop helper, mutates entry in place."""
    ...
```

#### 3.8.3 新增模块 `utils/tokenize.py`

见 §3.6.6 完整代码。公开接口：

```python
def count_tokens(text: str, encoding: str = PERSONA_RENDER_ENCODING) -> int
async def acount_tokens(text: str, encoding: str = PERSONA_RENDER_ENCODING) -> int
```

#### 3.8.4 新增方法（`PersonaManager` / `ReflectionEngine`）

**默认只 async**（不强制 sync / async 对偶——§7 全局约束第 6 条）：

```python
# ReflectionEngine
async def aapply_signal(
    self, name: str, reflection_id: str,
    delta: dict,       # {'reinforcement': ±float}  或  {'disputation': ±float}
                       # 通常只有一侧非零
    source: str,       # user_fact | user_rebut | user_confirm | user_ignore
                       # | user_keyword_rebut | migration_seed
) -> None:
    """Mutate evidence, emit EVT_REFLECTION_EVIDENCE_UPDATED."""
    async with self._get_alock(name):
        # 1. load reflections.json
        # 2. find reflection by id
        # 3. 根据 delta 只触动对应侧的时间戳：
        #      if delta.get('reinforcement'):
        #          new_rein = old_rein + delta['reinforcement']
        #          new_rein_ts = now
        #          new_disp_ts = old_disp_ts  # unchanged
        #      elif delta.get('disputation'):
        #          new_disp = old_disp + delta['disputation']
        #          new_disp_ts = now
        #          new_rein_ts = old_rein_ts  # unchanged
        # 4. await arecord_and_save(...) with full-snapshot payload
        #    (两个时间戳字段都放当前值)
        ...

async def _apromote_with_merge(self, name: str, reflection: dict) -> str:
    """§3.9 entry. Returns 'promote_fresh' | 'merge_into' | 'reject'
    | 'skip_retry_pending' | 'blocked'."""
    ...


# PersonaManager
async def aapply_signal(
    self, name: str, entity_key: str, entry_id: str,
    delta: dict, source: str,
) -> None:
    """Mutate evidence, emit EVT_PERSONA_EVIDENCE_UPDATED."""
    ...

async def amerge_into(
    self, name: str, target_entry_id: str, merged_text: str,
    merged_reinforcement: float, merged_disputation: float,
    source_reflection_id: str, merged_from_ids: list[str],
) -> None:
    """§3.9 helper. Emits EVT_PERSONA_ENTRY_UPDATED
    (text + evidence rewrite) + EVT_PERSONA_EVIDENCE_UPDATED
    (evidence-only snapshot)."""
    ...
```

每个 async method 内部都走 `async with self._get_alock(name)` + `await
arecord_and_save(...)`，保证锁嵌套顺序（§3.3.3）。

sync twin 仅在真有 sync 调用方时补——不为对偶性而对偶。生产路径全
async；测试用 `pytest-asyncio`；migration 脚本（§5）需要 sync 跑的话
在那里现写 sync 版本，理由明示。

#### 3.8.5 `memory_server.py` 启动期新增

```python
# 3 个 reconciler handler 注册（P2.b 接入点，本 RFC 实现 PR 一并补）
reconciler.register(EVT_REFLECTION_EVIDENCE_UPDATED, _apply_reflection_evidence)
reconciler.register(EVT_PERSONA_EVIDENCE_UPDATED,    _apply_persona_evidence)
reconciler.register(EVT_PERSONA_ENTRY_UPDATED,       _apply_persona_entry)

# 背景循环（挂在 _spawn_background_task 上启动）
_spawn_background_task(_periodic_signal_extraction_loop())   # §3.4.3
_spawn_background_task(_periodic_archive_sweep_loop())        # §3.5
# 现有 loops 保留：
#   _periodic_auto_promote_loop：改跑 _apromote_with_merge，去掉时间跳级
#   _periodic_rebuttal_loop：不动
#   _periodic_idle_maintenance_loop：不动（auto-correction 逻辑保留）

# 对话主路径上的 negative keyword hook（§3.4.5 Layer 1）
# 注册到 /process 的 user 消息预处理阶段
```

#### 3.8.6 `aget_followup_topics` 补过滤条件

`ReflectionEngine.aget_followup_topics` (`reflection.py:489`) 现行
只按 `status == 'pending'` + `next_eligible_at` 到期过滤。本 RFC 补
一条 `evidence_score >= 0`：

```python
async def aget_followup_topics(self, lanlan_name: str) -> list[dict]:
    pending = await self.aget_pending_reflections(lanlan_name)
    now = datetime.now()
    eligible = []
    for r in pending:
        next_eligible = r.get('next_eligible_at')
        if next_eligible:
            try:
                if datetime.fromisoformat(next_eligible) > now:
                    continue
            except (ValueError, TypeError):
                pass
        # NEW: evidence_score 过滤
        if evidence_score(r, now) < 0:
            continue
        eligible.append(r)
    return eligible[:2]  # 现行限 2 条
```

效果：score < 0 的 reflection 不会被主动挑出来搭话，但也没到归档
（sub_zero_days 还没累计够）——它就在 reflections.json 里"冷藏"。
用户自己提起来时可以正常 signal 回升。

#### 3.8.7 依赖

- `pyproject.toml` `[project.dependencies]` 加 `tiktoken>=0.7.0`
- `requirements.txt` 由 `uv export` 重新生成

### 3.9 Merge-on-promote

本节定义 reflection 从 `confirmed` 升 `promoted` 的唯一路径。与现行
时间跳级的 `_aauto_promote_stale_locked` (`reflection.py:820`) 不同，
新路径**只**由 score 穿 threshold 触发（红线 2），并且**一定**走 LLM
合并决策。

#### 3.9.1 触发条件

```python
async def _periodic_auto_promote_loop():
    """改跑 score-driven promote，不再按天数跳级。"""
    while True:
        await asyncio.sleep(AUTO_PROMOTE_CHECK_INTERVAL)
        # ... （per character 迭代）
        for r in all_confirmed_reflections:
            if evidence_score(r, now) >= EVIDENCE_PROMOTED_THRESHOLD:
                # score 穿阈值 → 尝试 promote
                await reflection_engine._apromote_with_merge(name, r)
```

条件：reflection 的 `status == 'confirmed'` 且
`evidence_score(r, now) >= EVIDENCE_PROMOTED_THRESHOLD`。

**删除**现行 `reflection.py:66-67` 的 `AUTO_CONFIRM_DAYS = 3` /
`AUTO_PROMOTE_DAYS = 3` 常量；**删除** `_aauto_promote_stale_locked`
的时间跳级分支。`_periodic_auto_promote_loop` 保留但逻辑替换。

#### 3.9.2 节流（防 LLM 失败 DOS）

为防止反复 LLM 失败的 reflection 把背景循环跑爆：

```python
# reflection schema 新字段
'last_promote_attempt_at': None,   # ISO8601 or null
'promote_attempt_count': 0,        # int
'promote_blocked_reason': None,    # str or null
```

每次 `_apromote_with_merge` 入口先检查：

- 若 `last_promote_attempt_at` 在 `EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES=30`
  分钟内 → 本轮 skip，返回 `'skip_retry_pending'`（不算失败次数）
- 若 `promote_attempt_count` 已 ≥ `EVIDENCE_PROMOTE_MAX_RETRIES=5` →
  mark `status='promote_blocked'`、`promote_blocked_reason='llm_unavailable'`，
  返回 `'blocked'`。后续需要人工 / 运维介入 / 或新 user signal 重置
  （设计上 user signal 重置该字段，score 再次穿阈值时重试）

两个常量（`BACKOFF_MINUTES`、`MAX_RETRIES`）进 §6.5 Gate（见那里）。

#### 3.9.3 LLM 合并决策流程

```python
async def _apromote_with_merge(self, name: str, R: dict) -> str:
    async with self._get_alock(name):  # ReflectionEngine._alocks
        # 0. 节流检查
        if _within_backoff(R):
            return 'skip_retry_pending'
        if _exceeds_max_retries(R):
            await self._amark_promote_blocked(name, R, 'llm_unavailable')
            return 'blocked'

        # 1. Load candidates
        persona = await self._persona_manager.aget_persona(name)
        same_entity_persona = [
            (ek, e)
            for ek, section in persona.items()
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

        # 2. LLM call（含节流字段先更新，表示"本次开始尝试"）
        await self._arecord_promote_attempt(name, R['id'], now)
        try:
            merge_decision = await self._allm_call_promotion_merge(
                R=R,
                persona_pool=same_entity_persona,
                reflection_pool=same_entity_reflections,
            )
        except Exception as e:
            logger.warning(f"[Promote] LLM call failed: {e}")
            # 节流字段已增加 attempt_count，reflection 留 confirmed 等下次
            return 'skip_retry_pending'

        # 3. Parse + dispatch
        action = merge_decision.get('action')

        if action == 'promote_fresh':
            result = await self._persona_manager.aadd_fact(
                name, R['text'], entity=R.get('entity'),
                source='reflection', source_id=R['id'],
            )
            if result in (self._persona_manager.FACT_ADDED,
                          self._persona_manager.FACT_ALREADY_PRESENT):
                await self._arecord_state_change(
                    name, R['id'], 'confirmed', 'promoted',
                )
                return 'promote_fresh'
            else:
                await self._arecord_state_change(
                    name, R['id'], 'confirmed', 'denied',
                    reason='rejected_by_persona_add',
                )
                return 'reject_by_persona'

        elif action == 'merge_into':
            target_id = merge_decision['target_id']
            merged_text = merge_decision['merged_text']
            # 合并 evidence 的规则（见下节）
            merged_rein, merged_disp = _compute_merged_evidence(
                target_entry, R,
            )
            await self._persona_manager.amerge_into(
                name, target_id, merged_text,
                merged_reinforcement=merged_rein,
                merged_disputation=merged_disp,
                source_reflection_id=R['id'],
                merged_from_ids=[R['id']],
            )
            await self._arecord_state_change(
                name, R['id'], 'confirmed', 'merged',
                absorbed_into=target_id,
            )
            return 'merge_into'

        elif action == 'reject':
            await self._arecord_state_change(
                name, R['id'], 'confirmed', 'denied',
                reason='llm_merge_rejected',
                reject_explanation=merge_decision.get('reason'),
            )
            return 'reject'

        else:
            # LLM 返回了未知 action → 视作 parse 失败
            logger.warning(f"[Promote] unknown action: {action}")
            return 'skip_retry_pending'
```

#### 3.9.4 LLM fail 不默认 promote_fresh

**关键设计选择**：LLM 失败（超时 / 非法 JSON / 未知 action）时，
**不**默认 `promote_fresh`。

为什么不默认 fresh：如果 LLM 服务长期挂掉，默认 fresh 会静默累积重复
persona entry，破坏 dedup 语义——原本 LLM 会判"这条其实跟 P-001 重复，
应 merge_into"的 reflection 都会被 fresh 成独立 entry。

正确降级：

- LLM 失败 → reflection 留 confirmed 不变，节流字段 `last_promote_attempt_at`
  更新、`promote_attempt_count += 1`
- 下次背景循环重试（受 `EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES=30`
  节流）
- 连续失败到 `EVIDENCE_PROMOTE_MAX_RETRIES=5` → `status='promote_blocked'`，
  死信状态，需人工介入或新 user signal 触发重置

这样保证：(a) LLM 短暂失败不丢 promote，会自动重试；(b) LLM 长期
失败不会让某条 reflection 反复消耗 LLM 调用配额；(c) 不静默生成重复
persona entry。

#### 3.9.5 合并 evidence 的规则

当 LLM 决定 `merge_into target_id` 时，target persona entry 的 evidence
怎么算？

**保守规则**（不用 LLM 建议，用确定性算法）：

```python
def _compute_merged_evidence(target: dict, reflection: dict) -> tuple[float, float]:
    """合并两者 evidence 时，保守规则避免"合并引起虚高累积"。"""
    merged_rein = max(
        float(target.get('reinforcement', 0.0)),
        float(reflection.get('reinforcement', 0.0)),
    )
    merged_disp = max(
        float(target.get('disputation', 0.0)),
        float(reflection.get('disputation', 0.0)),
    )
    return merged_rein, merged_disp
```

为什么 `max` 而不是 `sum`：

- `sum` 的语义是 "两条合并后的 evidence 累加" —— 但用户实际给的 signal
  只有 `max(signals)` 那么多。合并不应该让 evidence 虚涨。
  - 例：target 有 `rein=2`（2 次 user confirm），reflection 有 `rein=1`
    （1 次 user confirm）→ 如果 sum，merged `rein=3` 看似用户确认了 3
    次，但实际可能是同一个 user 意图被 Stage-2 和 check_feedback 双路
    拾到的重复计数——不该加倍。
- `max` 的保守假设：取两者较高 evidence，代表"用户对该语义的最强肯定/
  否认"。不虚涨，安全。

如果未来发现 max 太保守（合并后 evidence 不够反映"两条都被提过"），
可以在 §6 open question 里迭代。

#### 3.9.6 事件序列

`merge_into` 分支实际发出的事件（按时间顺序）：

1. `EVT_PERSONA_ENTRY_UPDATED`（在 `amerge_into` 内部发出）
   - payload：target 的新 text sha256 + 新 evidence 值 + merged_from_ids
   - view 同步把 persona.json 里 target entry 的 text / evidence 覆写
2. `EVT_PERSONA_EVIDENCE_UPDATED`（同在 `amerge_into` 内部）
   - payload：evidence-only snapshot，冗余但便于 funnel 扫描（§3.10）
   - 这步是可选优化——如果 handler 实现得好，步 1 的 entry_updated 已
     经覆盖了 evidence 字段，步 2 重复。V1 **发**以保证 funnel API
     可以单独扫 evidence 变化不漏 merge 事件。
3. `EVT_REFLECTION_STATE_CHANGED`（reflection 侧）
   - payload：`{from: 'confirmed', to: 'merged', absorbed_into: target_id}`
   - reflection 在 reflections.json 里 status 改写，不归档

**幂等保证**：三个事件都是 full-snapshot payload，重放 N 次结果一致。

**崩溃半程**：
- 如果 crash 在步 1 成功后、步 3 前重启：reconciler 重放步 1 → persona
  已是 merge 后态；然后 reconciler 发现 reflection 仍是 confirmed，
  但在下次 `_periodic_auto_promote_loop` 时发现 `absorbed_into`
  字段非空（不应该非空，因为没发出 state_changed 前它不会被设）——
  实际上步 3 才写 `absorbed_into`，所以 crash 半程的 reflection 仍
  是 confirmed 且 absorbed_into=None。下次 promote 循环会再次触发
  `_apromote_with_merge`，LLM 可能决定同样 merge_into 或换策略——
  `amerge_into` 内部对 target 的 `merged_from_ids` 做 dedup 检查，
  同一 reflection_id 不会被合并两次。

#### 3.9.7 LLM merge prompt

`PROMOTION_MERGE_PROMPT`（新增，5 语言 i18n），zh 示例：

```
你是一个 careful deduplication judge。你在维护 {AI_NAME} 对 {MASTER_NAME}
的长期印象。现在有一条待晋升的观察：

  R: "{R.text}"
  R.evidence_score: {R_score}

======以下是 {AI_NAME} 关于 {MASTER_NAME} 的现有印象池======
（已 promoted 的 persona fact + 其它 confirmed 的 reflection）

[persona.master.p_001] "{persona_1.text}" (evidence_score={...})
[persona.master.p_002] "{persona_2.text}" (evidence_score={...})
[reflection.r_042] "{reflection_42.text}" (evidence_score={...})
...
======以上为现有印象池======

请判断 R 应该：

- promote_fresh：作为新 persona fact 独立收录（和现有任何条目都不
  重复、不矛盾）
- merge_into：和某条现有 persona entry 语义相近，应合并。返回
  target_id（**必须**来自上面"现有印象池"区里的 persona.* 条目，不
  要合并到 reflection 条目）和合并后的文本。
- reject：和现有某条明确矛盾且 R 证据弱于对方，不应收录。返回 reason。

返回 JSON：
{
  "action": "promote_fresh" | "merge_into" | "reject",
  "target_id": "persona.master.p_001"   // merge_into 时必填；必须在印象池里
  "merged_text": "..."                  // merge_into 时必填
  "reason": "..."                       // reject 时必填
}
```

水印：`"careful deduplication judge."` + `"======以上为现有印象池======"`
（双保险）。

`target_id` 只允许 `persona.*` 前缀——`_apromote_with_merge` 的目标
pool 虽然技术上包含现有 confirmed/promoted reflection，但 LLM 被限制
只合并到 persona 层。合并到 reflection 层语义不清（两条 reflection 合成
一条更高抽象？这已接近话题级聚类，超出本 RFC）。

#### 3.9.8 频率与成本

Promote 频次 ~5 次/天/角色（假设积累足够 signal）。每次 ~500 tokens
in + ~100 tokens out ≈ 600 tokens。日均 3K tokens/角色，可忽略。

LLM tier：`EVIDENCE_PROMOTION_MERGE_MODEL_TIER`（§6.5 Gate 3 敲定）。
候选偏 qwen-max 级别——合并决策影响 persona 可读性，精度优先。

### 3.10 Analytics / funnel API

本 RFC 附带一个轻量 funnel 分析 API，方便 debug + 未来做 UI 报告。

#### 3.10.1 Funnel 定义

Funnel = 时间窗口 W 内，各状态转换的计数。数据源是 `events.ndjson`
本身——它已经是 append-only 审计日志，自带"发生了什么"的有序记录。

核心事件序列（按时间顺序）：

```
fact.added ──▶ reflection.synthesized
                   │
                   │ reflection.evidence_updated (累积)
                   ▼
              status: confirmed (via reflection.state_changed)
                   │
                   │ reflection.evidence_updated (继续)
                   ▼
              status: promoted / merged / denied
                   │
     promoted ──▶ persona.fact_added (新条目)
     merged   ──▶ persona.entry_updated (合入已有 + 改写)
                   │
                   │ persona.evidence_updated (persona 持续)
                   ▼
              ... 归档：persona.fact_added to archive shard 或
                       reflection.state_changed to archived
```

#### 3.10.2 API

```python
# memory/evidence.py analytics submodule
def funnel_counts(
    lanlan_name: str,
    since: datetime,
    until: datetime,
) -> dict:
    """线性扫 events.ndjson，聚合各类型事件的计数。
    Returns: {
        "facts_added": N,
        "reflections_synthesized": N,
        "reflections_confirmed": N,        # state_changed to=confirmed
        "reflections_promoted": N,         # state_changed to=promoted
        "reflections_merged": N,           # state_changed to=merged
        "reflections_denied": N,           # state_changed to=denied
        "reflections_archived": N,         # state_changed to=archived
        "persona_entries_added": N,        # persona.fact_added (main view)
        "persona_entries_rewritten": N,    # persona.entry_updated
        "persona_entries_archived": N,     # persona.fact_added (archive path)
    }
    """
```

#### 3.10.3 扫开销

`events.ndjson` compaction 阈值 `_COMPACT_LINES_THRESHOLD = 10_000`
(`event_log.py:75`)，全扫一次 <200ms。对偶发 `/analytics` API 调用够
快。不需要专用索引。

#### 3.10.4 V1 不做

- CLI 工具 / UI 报表 / 定时报告推送
- 跨角色聚合（按 `/analytics?character=all` 之类）

V1 **只**把 aggregation API 落地，给未来 UI / 脚本复用。

#### 3.10.5 为什么在本 RFC 里讨论 funnel

Funnel 的形状依赖 §3.9 的事件设计（`merged` 作为独立 state_changed
值、`persona.entry_updated` 作为独立事件）。不在同一 RFC 里定会出现
"funnel 想要 X 但事件里没 X"的锁死——以后补事件不向后兼容 funnel
输出。所以在这里一并敲定。

### 3.11 全系统可溯源

**承诺**：从硬盘上看，**所有 fact / reflection / persona entry 的
来源链永久保留**。哪怕一条已经 merge 进别人、archive 到分片、或被排
除出主路径检索，它的原始 ID + source 链 + 全部历史事件都还在。

#### 3.11.1 溯源链路

```
persona entry
  ├── source_reflection_id  → reflection (那条 promote 进来的)
  │     └── source_fact_ids → [fact, fact, fact, ...]
  │                            (合成时的 source)
  │
  ├── （或）source='manual'          → 用户/脚本手动插入，无上游
  ├── （或）source='character_card'  → 角色卡同步进来
  └── （若被 merge 改写）
        merged_from_ids → [reflection_id, ...]
                          (LLM merge 决策吸收的源)
```

#### 3.11.2 字段保留承诺

每个 entry 永远不删的字段：

- `id`：UUID 形态，永不复用、永不重写。一旦分配，溯源就靠它
- `source` + `source_id`（persona entry 现有 schema）：来源类型 + 上游 ID
- `source_fact_ids`（reflection 现有 schema）：合成它的那批 fact ID
- `absorbed_into`（reflection 新增，§3.2）：被 merge 时填 target_id
- `merged_from_ids`（persona entry 新增，§3.2）：吸收哪些 reflection

事件日志 `events.ndjson` 全程 append-only，**永不删行**；compaction
只 truncate 给定时间窗之前的、且保留 snapshot 起点（event-log 模块现
行行为）。

#### 3.11.3 "被隐藏" vs "被删除"

三种状态的语义区别：

**archive**：entry 从主 view 文件（`reflections.json` / `persona.json`）
**移**到分片归档文件 `*_archive/<date>_<uuid8>.json`。

- 主路径 `PersonaManager.aensure_persona` / `ReflectionEngine.aload_reflections`
  不再加载 archive 文件，render 不涉及
- `aget_followup_topics` 不再选它
- **但文件还在硬盘**，分片文件保留完整 entry 结构（含 source / audit
  字段）
- 日后如果做"主动检索"功能（用户手动搜历史），可以读分片找回

**merged（reflection）**：原 reflection status 从 `confirmed` 跳到
`merged`，**仍然在 `reflections.json` 里**（不归档），带
`absorbed_into=target_persona_entry_id` 标记。

- 后续主动检索 / proactive chat / `aget_followup_topics` 一律按
  `status in ('pending', 'confirmed')` 过滤，不会再选到它
- 但 ID 和 source_fact_ids 全保留，可循 `absorbed_into` 反查 persona
  target 并最终追到 fact 源

**rewritten（persona entry）**：被 LLM 决定合并、target text 被改写。

- 原 text 从 view 上不见（被 merged_text 覆盖）
- 但事件日志里 `EVT_PERSONA_ENTRY_UPDATED` 的 payload 留有
  `merged_from_ids`，循着这些 ID 反查可拿到被合的 reflection 的原文
- Reflection 本身（被合的那几条）仍在 reflections.json + 它们的
  source_fact_ids 仍指向 facts.json

#### 3.11.4 主动检索过滤建议

本 RFC 实现的 evidence 机制**不**直接做"主动检索"功能，但所有数据 /
事件都为它预留出溯源支持。后续如果做检索，建议按这个过滤函数：

```python
def _is_visible_for_proactive_retrieval(entry: dict, now: datetime) -> bool:
    if entry.get('protected'):
        return True  # character_card 永远可见
    if entry.get('status') in ('archived', 'merged', 'denied',
                                'promote_blocked'):
        return False
    if evidence_score(entry, now) < 0:
        return False
    return True
```

所有"被隐藏"条目都不会浮上来给用户主动搜到，但 ID 和事件日志全在硬盘。

## 4. Implementation plan

本 RFC 分 4 个 PR 落地，按依赖顺序。

### 4.1 PR-1: P-A signal detection + P-B evidence data layer + migration

**新增 / 改造**：

- `pyproject.toml` 加 `tiktoken>=0.7.0`（P-A 不用但 P-D 要，提前挂避免
  后面 PR 要再改一次依赖）。`requirements.txt` 由 `uv export` 重新生成
- `config/__init__.py` 加所有 evidence 常量 + 同步加进文件末 `__all__`
- `config/prompts_memory.py`：
  - 改造 `FACT_EXTRACTION_PROMPT`（5 语言；删 tags 要求；不加
    reinforces/negates；删 importance >= 5 过滤要求，§3.4.7）
  - 新增 `SIGNAL_DETECTION_PROMPT`（5 语言 i18n，带水印）
  - 新增 `NEGATIVE_TARGET_CHECK_PROMPT`（5 语言 i18n，带水印）
  - 新增 `NEGATIVE_KEYWORDS_I18N`（5 语言 frozenset dict）
- `memory/evidence.py` 新建——**只**放纯函数 + 背景辅助：
  - `effective_reinforcement` / `effective_disputation` / `evidence_score`
  - `derive_status`
  - `maybe_mark_sub_zero`（为 P-C 提前挂函数签名，P-C 补实现）
- `memory/facts.py`：
  - 重写 `aextract_facts` 为 `aextract_facts_and_detect_signals`
    （两次 LLM call，§3.4.2）
  - **删除** `if importance < 5: continue` 硬丢（`facts.py:315`）
- `memory/reflection.py`：
  - 新增 `aapply_signal`（async only，§3.8.4）
  - `check_feedback` 新增 `ignored → reinforcement -= 0.2` 分支
  - Reflection schema 加 4 evidence 字段 + `sub_zero_days` +
    `sub_zero_last_increment_date` + `absorbed_into` +
    `last_promote_attempt_at` + `promote_attempt_count` +
    `promote_blocked_reason`
  - **删除** `AUTO_CONFIRM_DAYS = 3` / `AUTO_PROMOTE_DAYS = 3` 常量
    （`reflection.py:66-67`）
  - `_aauto_promote_stale_locked` 重构：去掉时间跳级分支；新版只按
    `score >= EVIDENCE_CONFIRMED_THRESHOLD` 升 confirmed（pending 的
    score 一般是 user_fact 的 reinforces 推上去）；**不**在这里 promote
    （那是 PR-3 的事）
- `memory/persona.py`：
  - 新增 `aapply_signal`（async only，§3.8.4）
  - Entry schema 加 4 evidence 字段 + `sub_zero_days` +
    `sub_zero_last_increment_date` + `merged_from_ids`（在 `_normalize_entry`
    defaults 里）
  - `_texts_may_contradict` 调用链审查收窄到 `aadd_fact` +
    `_evaluate_fact_contradiction` 两处，其他处调用 grep 后改掉
- `memory_server.py`：
  - 注册 3 个 reconciler handler（`_apply_reflection_evidence` /
    `_apply_persona_evidence` / `_apply_persona_entry`）
  - 新增 `_periodic_signal_extraction_loop`（§3.4.3）
  - 新增对话主路径上的 negative keyword hook（§3.4.5 Layer 1）
  - 迁移 seed 启动期一次性触发（§5）
  - 现有 `_periodic_auto_promote_loop` 暂不动（PR-3 再改跑 `_apromote_with_merge`）

**测试**：

- 背景循环并发 60s 不死锁、不丢 fsync
- Migration 数值对账（每种旧 status 迁移后 score 落对应 tier）
- 新 evidence event 重放 10 次幂等（§3.3.6 三个 handler 各自测）
- Stage-1 / Stage-2 LLM 失败降级不破坏 facts.json 写入
- `check_feedback` 的 `ignored` 分支 reinforcement 确实 -= 0.2
- 全部 300 现有测试绿灯（`pytest tests/unit -q` + `ruff` +
  `scripts/check_async_blocking.py`，沿用 #905 gate）

### 4.2 PR-2: P-C time decay + archival

**新增 / 改造**：

- `memory/evidence.py` 补 `maybe_mark_sub_zero` 实现
- `memory/reflection.py` / `memory/persona.py` 的归档路径从 flat 文件
  改为分片目录
  - `_reflections_archive_path` 改成 `_reflections_archive_dir`，返回
    `memory/<char>/reflection_archive/`
  - 新增 `_persona_archive_dir` → `memory/<char>/persona_archive/`
  - 新增 `_aappend_to_archive_shard`：按 `ARCHIVE_FILE_MAX_ENTRIES` 拼
    分片文件，full 了就新开下一个
- `memory_server.py` 新增 `_periodic_archive_sweep_loop`（§3.5）
- 老数据迁移：首次启动检测到旧 flat `reflections_archive.json` → 按
  `archived_at` 日期拆分迁移到新目录，成功后删除旧 flat 文件（§3.5.5）

**测试**：

- `effective_reinforcement` / `effective_disputation` 数学快照（给定
  不同 age × rein/disp 组合，输出对比预期）
- 归档累计语义（score 抖动到正再回负，sub_zero_days 不清零）
- `protected=True` 豁免（永不累加 sub_zero_days）
- 分片文件大小上限（满 500 条自动新开分片）
- 旧 flat 文件迁移正确（按日期切分、uuid8 后缀唯一）

### 4.3 PR-3: P-D render budget + §3.9 merge-on-promote

**新增 / 改造**：

- `utils/tokenize.py` 新建（sync + async + fallback + 一次性 warn，§3.6.6）
- `config/prompts_memory.py` 新增 `PROMOTION_MERGE_PROMPT`（5 语言 i18n，
  带水印）
- `memory/persona.py`：
  - `_compose_persona_markdown` 改写为三相流程（Phase 1 split +
    Phase 2 per-section score trim + Phase 3 compose，§3.6.2）
  - 新增 `_ascore_trim` 辅助（§3.6.3）
  - 新增 `amerge_into`（§3.8.4）
- `memory/reflection.py`：
  - 新增 `_apromote_with_merge`（§3.9.3）
  - 替换 `_periodic_auto_promote_loop` 现行逻辑：跑 `_apromote_with_merge`
    对 score ≥ `EVIDENCE_PROMOTED_THRESHOLD` 的 reflection
  - 新增 `_amark_promote_blocked` / `_arecord_promote_attempt` 节流辅助
- Nuitka / PyInstaller 打包配置：把 `tiktoken/encodings/o200k_base.tiktoken`
  打入产物

**测试**：

- 打包产物启动后 `count_tokens('测试')` 不触发 heuristic warning（§8 S13）
- Merge LLM 超时**不**降级 promote_fresh，reflection 留在 confirmed 等
  下次 retry（§8 S14）
- Score-trim 保留 `protected`（§8 S12）
- Persona / reflection 预算独立（§8 S11）
- Merge-on-promote 链路的崩溃恢复（§8 S15-S17）
- 节流：连续 5 次 LLM 失败后 `status='promote_blocked'`

### 4.4 PR-4: §3.10 funnel analytics

**新增**：

- `memory/evidence.py` analytics submodule 补 `funnel_counts`（§3.10.2）
- 无前端 UI、无定时推送——V1 仅暴露 API

**测试**：

- 人工构造事件序列 → 对比 `funnel_counts` 输出（§8 S24）

### 4.5 跨阶段测试通用要求

- 每个新 `arecord_and_save` 调用点都有 crash-between 测试（kill
  between append 和 save，reconciler 下次启动收敛）
- 并发 `/process` + 各背景循环 60s 不死锁
- 兼容现有测试套件（pytest + ruff + scripts/check_async_blocking.py）

## 5. Migration

首次启动带新代码的 `memory_server` 时，会检测到**旧** reflection /
persona entry 缺 evidence 字段 → 进入一次性迁移流程。

### 5.1 迁移 seed 表

```
┌──────────────────┬─────────┬────────┬──────────────┬─────────────┐
│ 旧 reflection    │ rein    │ disp   │ 迁移后 score │ 派生状态    │
│ status           │ seed    │ seed   │              │             │
├──────────────────┼─────────┼────────┼──────────────┼─────────────┤
│ pending          │ 0       │ 0      │ 0            │ pending     │
│ confirmed        │ 1       │ 0      │ 1            │ confirmed   │
│ promoted         │ 2       │ 0      │ 2            │ promoted    │
│ denied           │ 0       │ 2      │ -2           │ archive_... │
└──────────────────┴─────────┴────────┴──────────────┴─────────────┘
```

迁移时每个 entry 的时间戳字段按 seed 是否非零分别设置：

- reinforcement seed > 0 → `rein_last_signal_at = migration_time`
- disputation seed > 0 → `disp_last_signal_at = migration_time`
- 对应 seed = 0 → 对应时间戳字段 `None`

`sub_zero_days = 0`，`sub_zero_last_increment_date = None`。

具体：

| 旧 status | rein | disp | rein_last_signal_at | disp_last_signal_at |
|---|---|---|---|---|
| pending | 0 | 0 | None | None |
| confirmed | 1 | 0 | migration_time | None |
| promoted | 2 | 0 | migration_time | None |
| denied | 0 | 2 | None | migration_time |

`denied` 条目迁移后 `score = -2` 虽然立即落在 archive_candidate 派生
状态，但**不会**立即归档——因为 `sub_zero_days = 0`，需要累计 14 天
才真触发归档（§3.5.3）。这给用户留出余地：半年前偶尔否定过的事，
迁移后有 14 天的"观察期"，期间若 user 给新 reinforces 信号，下次读
`effective_rein` 会从新 signal 起衰减，`effective_disp` 仍从
`migration_time` 起衰减——**两侧衰减时钟独立**，不会相互影响。

Persona entry 的迁移：所有非-protected 条目 `rein = 0, disp = 0,
rein_last_signal_at = None, disp_last_signal_at = None`（等价"从未
收过 signal"）。protected 条目同样填默认值但被 `evidence_score`
返回 `inf` 豁免。

### 5.2 迁移事件化

迁移**不**直接 mutate view 文件——走事件路径，保证崩溃恢复：

```python
async def _aone_shot_migration(lanlan_name: str) -> None:
    """首次启动检测到老数据时触发。每条旧 entry 发一条 seed 事件。"""
    # Reflection 侧
    for r in old_reflections:
        if _has_evidence_fields(r):
            continue  # 已迁移过
        rein, disp = _seed_from_status(r['status'])
        await reflection_engine.aapply_signal(
            lanlan_name, r['id'],
            delta={'reinforcement': rein, 'disputation': disp},
            source='migration_seed',
        )

    # Persona 侧
    for entity_key, section in persona.items():
        for entry in section.get('facts', []):
            if _has_evidence_fields(entry):
                continue
            await persona_manager.aapply_signal(
                lanlan_name, entity_key, entry['id'],
                delta={'reinforcement': 0.0, 'disputation': 0.0},
                source='migration_seed',
            )

    # Marker：写一条特殊 entry_id 标记迁移完成
    await persona_manager.aapply_signal(
        lanlan_name, '__meta__', '__migration_marker__',
        delta={}, source='migration_seed',
    )
```

**Reconciler handler 对 source='migration_seed' 的特殊处理**：
overwrite-once 语义——只在 entry 不存在时 set seed；若已存在（重跑
时），skip 不累加。这样避免重跑迁移时 evidence 翻倍。

**marker** `__meta__.__migration_marker__` 是假 entry_id，下次启动
检测到这条事件存在 → 跳过 `_aone_shot_migration` 全程。

### 5.3 Crash 半程恢复

Reconciler 启动顺序（来自 event-log 模块现行行为）：

1. `compact_if_needed` —— 可能的 events.ndjson 压实（与迁移无关）
2. `areconcile(name)` —— 重放 event log 尾部事件（这步会把已发出的
   migration_seed 事件应用到 view）
3. `_aone_shot_migration(name)` —— 检查 marker 是否存在；不存在才真跑
4. Outbox replay（P1.c 现行行为）

如果迁移**半程** crash：

- 已发出的 migration_seed 事件都在 events.ndjson 里
- 重启后 reconciler 步 2 把它们 apply 到 view —— 这些 entry 有 evidence
  字段了
- 步 3 检查 marker —— marker 事件可能没发（crash 在那之前），所以
  `_aone_shot_migration` 再次触发
- 再次遍历 entry → `_has_evidence_fields` 对已迁移的返回 True → skip
- 遍历到 crash 之前没迁完的 → 继续发 seed 事件
- 最后发 marker 事件

天然 resume 语义，不需要额外状态记录。

### 5.4 回滚策略

本 RFC 实现 PR 合入后如果发现严重 bug，回滚 = revert PR + 用户数据怎么办？

迁移**只增字段，不改旧字段**——旧的 `status` / `text` / `source` 都
保留。回滚后旧代码忽略 evidence 字段（Python dict 读不存在的字段默认
`None`/`0`，旧代码流程不动）。新写入的分片归档文件会残留（旧代码
不读），但不影响运行，后续手动清理即可。

## 6. Open questions

下列问题是"数值可后续调"的迭代型，**不阻塞 PR merge**。PR merge 前
必须敲定的另列在 §6.5。

### 6.1 话题级聚合机制完整缺位

V1 没有"话题"这个抽象。如果未来需要"用户不想聊某类话题"的行为，得做
全局聚类（embedding / 图数据库 / 主题模型）。关键问题：

- 什么时候触发全局组织（多少条目后）
- 话题状态怎么持久化
- 怎么和条目级 evidence 关联

独立 RFC 处理，不在本 RFC 范围。

### 6.2 阈值数值

`EVIDENCE_CONFIRMED_THRESHOLD = 1.0 / EVIDENCE_PROMOTED_THRESHOLD = 2.0`
是方向性初值。实践后可调（如要求 3 次净 confirm 再 promote）。本 RFC
的公式本身不变——阈值变化不影响公式结构。

### 6.3 pending 的下界

当前 pending 档是 `ARCHIVE_THR < score < CONFIRMED_THR`。问题：一个未
受过任何 signal 的新 reflection（score = 0）会不会在 `aget_followup_topics`
里被当候选？**会**（score ≥ 0 过滤器，§3.8.6）。若是，一次 ignored
就 score = -0.2 掉出 ≥ 0 过滤——状态来回抖动是否符合预期？目前没有
real workload 数据；上线后跟踪再调整。

### 6.4 `EVIDENCE_ARCHIVE_DAYS`

当前 14 天。是 "归档更积极" 方向的定量表达。实际需要看"平均多少天
一个条目会彻底失去用户兴趣"——需要生产数据。

### 6.5 `IGNORED_REINFORCEMENT_DELTA`

当前 -0.2。经验值。若生产里 ignored 频繁（用户经常不回应 surfaced
reflection）、老条目过度削弱——考虑调到 -0.1 或 -0.15。

### 6.6 Render token 预算

`PERSONA_RENDER_TOKEN_BUDGET = 2000 / REFLECTION_RENDER_TOKEN_BUDGET = 1000`
基于 context window 经验推算。Nuitka 打包产物跑本地小模型 context
window 更紧，可能要降；跑云端大模型可以抬。上线后按真实部署调。

### 6.7 Stage-2 context 规模

目前估 100 条 entry → ~2000 tokens 带入 Stage-2 prompt；如果 persona
规模增长到 500+ 条，prompt 会膨胀到 ~10K tokens。策略（§3.4.2 末段）：

- 按 entity 过滤
- 按 evidence_score DESC 取前 `EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS
  = 200`

未来如果 persona 规模涨到 1000+，需要进一步优化——分片调用？按
embedding 相似度选相关子集？

### 6.8 `persona.entry_updated` 的 text 重放

方案 B 的已知局限（§3.3.6）：若 view save 失败 + event append 成功，
重启后 reconciler 看到 sha256 对不上 text 又拿不到原文重建——只能停
reconciler 让人查。生产里这一路径罕见（atomic_write_json 原子性够强），
但若实测发生率不低，未来可能要考虑方案 A（payload 放 plaintext，显式
违反红线 4 的例外）。

## 6.5 Pre-merge reviewer gates

**这些决定 reviewer 要在对应实现 PR merge 之前敲定**，不能以"开放问题"
的方式放过。数值 / 选型直接进 `config/__init__.py`，改值会产生实际
behavior 变化，所以需要 reviewer 显式 sign-off 而不是默认接受草案值。

### Gate 1: 衰减半衰期（PR-2 前）

- `EVIDENCE_REIN_HALF_LIFE_DAYS = 30`（草案）
- `EVIDENCE_DISP_HALF_LIFE_DAYS = 180`（草案）

候选：

| 方案 | rein | disp | 特点 |
|---|---|---|---|
| 当前草案 | 30 | 180 | 1:6 比例 |
| 更快新陈代谢 | 14 | 90 | 适合短期关系模式 |
| 更慢 | 60 | 365 | 旧关系不易忘 |
| disp 不衰减 | 30 | ∞ | task spec 原始方案，reviewer round-2 否决 |

### Gate 2: Reflection 合成 context 量（PR-1 前）

- `REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_COUNT = 10`（草案）
- `REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_DAYS = 14`（草案）

候选：

| 方案 | count | days | 特点 |
|---|---|---|---|
| 当前草案 | 10 | 14 | 中庸 |
| 更保守 | 20 | 30 | 防重复合成更严，成本 +1 倍 |
| 更激进 | 5 | 7 | 可能漏检已 absorbed 语义重复 |

### Gate 3: 4 个 LLM tier 选型（各 PR 前）

| Tier 常量 | 用途 | 频次 | 延迟敏感 | 推荐候选 |
|---|---|---|---|---|
| `EVIDENCE_EXTRACT_FACTS_MODEL_TIER` | Stage-1 抽新 fact | ~10/h/角色 | 不敏感 | `"summary"` / `"correction"` |
| `EVIDENCE_DETECT_SIGNALS_MODEL_TIER` | Stage-2 判 signal 映射 | ~10/h/角色 | 不敏感 | `"correction"` / `"summary"` |
| `EVIDENCE_NEGATIVE_TARGET_MODEL_TIER` | 关键词二次判定 | <5/h/角色 | **敏感**（<3s） | `"emotion"` / `"summary"` |
| `EVIDENCE_PROMOTION_MERGE_MODEL_TIER` | Promote 合并决策 | ~5/天/角色 | 不敏感 | `"correction"` / `"conversation"` |

`config/__init__.py` 已有 model tier（2026-04 快照）：

- `"conversation"` → `DEFAULT_CONVERSATION_MODEL = 'qwen-max'`
- `"summary"` → `DEFAULT_SUMMARY_MODEL = 'qwen-plus'`
- `"correction"` → `DEFAULT_CORRECTION_MODEL = 'qwen-max'`
- `"emotion"` → `DEFAULT_EMOTION_MODEL = 'qwen-flash'`

成本对比（qwen 当前定价，输入 token /M）：

- qwen-flash: ¥0.1
- qwen-plus: ¥0.4
- qwen-max: ¥2.4

4 个 tier 可分别选、也可统一。实现作者先用占位常量接入，打到 PR
description 里高亮 "Pending Gate 3 — LLM tier selection"，reviewer 在
合入前给结论。

### Gate 4: 归档分片策略 + 节流常量（PR-2 / PR-3 前）

- `ARCHIVE_FILE_MAX_ENTRIES = 500`（草案，PR-2）
- `EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES = 30`（草案，PR-3）
- `EVIDENCE_PROMOTE_MAX_RETRIES = 5`（草案，PR-3）

这些相对低风险，草案默认多数情况可接受——但改起来没成本（只改常量），
reviewer 若有别的直觉就直接改。

### Workflow

各 PR 开工前、Draft PR 已开、实际 commit 落地前这个窗口，reviewer 在
PR thread 给出选型结论，更新 `config/__init__.py`。Gate 未敲定的话，
实现作者用草案值继续，但 PR description 里高亮 "Pending Gate N"
提醒 reviewer 必须在 merge 前给结论。

## 7. What this RFC explicitly rejects

- **Plaintext in event payloads**（红线 4 对应）。所有 user 派生内容
  走 `text_sha256` / `user_msg_sha256`。`persona.entry_updated` 的 text
  不进 payload，只放 sha256 校验（§3.3.6 的取舍）。
- **Time-based auto-promotion**（红线 2）。promote 必须 score 穿阈值
  触发。`AUTO_CONFIRM_DAYS` / `AUTO_PROMOTE_DAYS` 常量 + 时间跳级
  分支**删除**。
- **Cross-character evidence**。Evidence per-character 独立，不跨角色
  投射。
- **ML-ranked persona**。纯 rule-based，score 可手算。
- **Per-entry UI 编辑按钮**。用户不通过 UI 调 persona 条目的 evidence
  数值（§2.4）。记忆系统需要隐私感。
- **`recent_mentions` 作为 evidence 输入源**。它是反向机制（AI 提太多
  → suppress），保持原语义；**不**映射到 evidence。
- **`_texts_may_contradict` 作为 user feedback 判定**（红线 1）。它是
  AI 替用户判断，调用链收窄到 `aadd_fact` 一处（§3.4.4）。
- **写时 decay**（红线 5）。decay 在 read 时现算，不进事件日志。
- **Render-time LLM 合并**。合并只在 promote 时发生（§3.6.4 / §3.9）。
- **Render cache**。无 merge phase 就不需要（§3.6.5）。
- **基于 fact.tags 的 topic 聚合**。`tags` 字段保留位但 V1 不消费；
  话题级机制整体延后（§3.7）。
- **LLM merge 失败默认 promote_fresh**。会静默累积重复 entry；正确
  降级是 retry + 死信状态（§3.9.4）。
- **全系统事件删除**。任何 entry 被 archive / merge / reject 时，原始
  数据和所有历史事件都保留（§3.11）。

## 8. Success criteria

本 §只列**本 RFC 新增功能**的验收标准。基础设施层面的 lock concurrency /
event log replay / record_and_save 合约已经由 #905 保证，不在本 RFC
重新验。

### 8.1 数值与公式正确性

- **S1**：read-time decay 数学正确。给定 `(reinforcement, disputation,
  rein_last_signal_at, disp_last_signal_at)` 与 `now`，`evidence_score()`
  输出符合 §3.1.1 公式。snapshot 测试覆盖 `(0d / 30d / 60d / 180d /
  365d)` × `(rein 衰减 / disp 衰减)` 的 10 个组合。
- **S1b**（新增）：rein / disp 时钟独立性。给定 `rein=3,
  rein_last_signal_at=30 天前, disp=0, disp_last_signal_at=None`，
  apply 一次 `disp += 1` 事件之后，下次读 `effective_reinforcement ≈
  1.5`（不被 disp 事件"刷新"）、`effective_disputation = 1.0`、
  `evidence_score ≈ 0.5`。反向场景（disp 有值后 apply reinforcement）
  对称测一遍。
- **S2**：派生状态映射正确。给定 `score`，`derive_status()` 返回值
  符合 §3.1.4 阈值表（archive_candidate / pending / confirmed /
  promoted）。
- **S3**：迁移 seed 数值正确。每种旧 status（pending / confirmed /
  promoted / denied）走完迁移后，view 里的 `(rein, disp)` = §5.1 表中
  预期值；`evidence_score` 落对应 tier。

### 8.2 事件 + view 联动

- **S4**：3 个新 evidence event 各自的 reconciler handler 重放 10 次
  后 view 字段一致（snapshot pattern 自然幂等）。
- **S5**：`persona.entry_updated` 的 sha256 校验逻辑——重放时 view
  text hash 匹配则 no-op；不匹配则 reconciler raise（按设计停启动
  等人查），不静默通过。

### 8.3 两次 LLM 调用拆分

- **S6**：Stage-1 `_allm_extract_facts` 输入纯 user 消息，输出新
  fact；LLM 返回的 text **不**包含来自已有 reflection / persona 的
  原文（防"摘已有观察当新 fact"回归测）。
- **S7**：Stage-2 `_allm_detect_signals` 返回的 `target_id` 100% 在
  传入的 `existing_observations` 集合里（编造 ID 必须被防御性 drop）。
- **S8**：Stage-1 失败 → Stage-2 不跑、新 fact 也不写；Stage-2 失败
  → 新 fact 已写入但 signal 全 drop（不阻塞 fact 写入）。

### 8.4 负面关键词链路

- **S9**：关键词命中后 LLM target check 失败 → 不加 disputation、不
  崩溃、log warning。
- **S10**：关键词命中后 LLM 返回空 targets → no-op，不盲加 disputation
  到 surfaced 任意一条。

### 8.5 Render budget

- **S11**：persona 和 reflection 预算独立——一侧条目超 budget 不影响
  另一侧。
- **S12**：`protected=True` 条目永远渲染 + 永远不计入 token 预算（不
  论 budget 多紧张）。
- **S13**：Nuitka 打包产物启动后 `count_tokens('测试')` 用 tiktoken
  真路径（不触发 heuristic fallback warning）。

### 8.6 Merge-on-promote

- **S14**：LLM 失败 → reflection 留 confirmed 不变、不静默 `promote_fresh`；
  下次 cycle retry（遵守 `EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES`
  节流）；连续失败到 `EVIDENCE_PROMOTE_MAX_RETRIES` → `status='promote_blocked'`。
- **S15**：LLM 决定 `merge_into` → target persona entry 的 text 和
  evidence 都被改写、原 reflection `status='merged'` + `absorbed_into =
  target_id`（溯源链完整）。
- **S16**：merge 后该 reflection 不再被 `aget_followup_topics` 选中
  （status 过滤生效）。
- **S17**：merge 后被 absorb 的 reflection 仍在 `reflections.json`
  （不删、不归档），可循 `absorbed_into` 反查。

### 8.7 归档

- **S18**：`sub_zero_days` 累计而非连续——score 抖动到正再回负，
  计数器不清零（"积极归档"）。
- **S19**：归档分片文件按日期 + uuid8 命名、每分片 ≤
  `ARCHIVE_FILE_MAX_ENTRIES`、超过自动新分片。
- **S20**：归档后主 view 文件不再含该条目，但分片文件可读到完整
  entry（包括所有 source / audit 字段）。

### 8.8 可溯源（§3.11）

- **S21**：persona entry 的 `source_reflection_id` 永远指向有效的
  reflection ID（即使该 reflection 已 archived 到分片）。
- **S22**：reflection 的 `source_fact_ids` 永远指向有效的 fact ID
  （即使该 fact 已被 reflection 合成 absorb）。
- **S23**：`events.ndjson` 永不删行；compaction 只 truncate 给定时间
  窗之前的、且保留 snapshot 起点。

### 8.9 兼容性

- **S24**：现有单元测试套件全绿（`pytest tests/unit -q` + `ruff` +
  `scripts/check_async_blocking.py`）。
- **S25**：本 RFC 新增 ≥ 60 个单元测试覆盖 S1-S23。

## 9. Out-of-scope follow-ups

- Memory browser UI 展示 evidence score（**只看，不编辑**——编辑是
  §7 rejects）
- 定时 funnel 报告推送（基于 §3.10 API）
- 话题级聚合机制（embedding 聚类 / 图数据库 / 主题模型路线）——
  独立 RFC 处理
- tiktoken encoding 切更新家族（暂不讨论）
- 归档恢复 UI（用户手动把 archived 条目拉回 active）
- 背景循环的更细粒度拆分（若实测某 sweep 慢阻塞另一 sweep）
- 主动检索 API（基于 §3.11 溯源链 + `_is_visible_for_proactive_retrieval`
  过滤建议，但 UI 和 API shape 独立讨论）
- Stage-2 context 规模超过 500 条 entry 时的 embedding 子集选择

## Appendix A — 新常量汇总

所有常量**统一进 `config/__init__.py`** 并加进文件末 `__all__`。
`memory/evidence.py` 是新模块但**只放纯函数 + 背景辅助，不放常量**。

### A.1 稳定默认（草案值 OK，合 PR 时不需 reviewer 单独 gate）

| 常量 | 默认 | 类型 | RFC § | 备注 |
|---|---|---|---|---|
| `EVIDENCE_CONFIRMED_THRESHOLD` | 1.0 | float | §3.1.4 | `score ≥ 1` → confirmed |
| `EVIDENCE_PROMOTED_THRESHOLD` | 2.0 | float | §3.1.4 | `score ≥ 2` → promoted |
| `EVIDENCE_ARCHIVE_THRESHOLD` | -2.0 | float | §3.1.4 | `score ≤ -2` → archive_candidate |
| `EVIDENCE_ARCHIVE_DAYS` | 14 | int | §3.5.3 | 累计 sub_zero 天数阈值 |
| `IGNORED_REINFORCEMENT_DELTA` | -0.2 | float | §3.1.5 | ignored 扣在 rein 侧 |
| `EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS` | 10 | int | §3.4.3 | 信号抽取触发 |
| `EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES` | 5 | int | §3.4.3 | 信号抽取触发 |
| `EVIDENCE_SIGNAL_CHECK_ENABLED` | True | bool | §3.4.3 | 独立开关 |
| `EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS` | 200 | int | §3.4.2 | Stage-2 prompt 带的 existing 上限 |
| `PERSONA_RENDER_TOKEN_BUDGET` | 2000 | int | §3.6.1 | 非-protected persona 预算 |
| `REFLECTION_RENDER_TOKEN_BUDGET` | 1000 | int | §3.6.1 | reflection 渲染预算 |
| `PERSONA_RENDER_ENCODING` | `"o200k_base"` | str | §3.6.6 | tiktoken encoding |

### A.2 待 reviewer 敲定的 pre-merge gates（见 §6.5）

| 常量 | 草案 | 类型 | Gate |
|---|---|---|---|
| `EVIDENCE_REIN_HALF_LIFE_DAYS` | 30 | int | Gate 1 |
| `EVIDENCE_DISP_HALF_LIFE_DAYS` | 180 | int | Gate 1 |
| `REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_COUNT` | 10 | int | Gate 2 |
| `REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_DAYS` | 14 | int | Gate 2 |
| `EVIDENCE_EXTRACT_FACTS_MODEL_TIER` | **pending** | str | Gate 3 |
| `EVIDENCE_DETECT_SIGNALS_MODEL_TIER` | **pending** | str | Gate 3 |
| `EVIDENCE_NEGATIVE_TARGET_MODEL_TIER` | **pending** | str | Gate 3 |
| `EVIDENCE_PROMOTION_MERGE_MODEL_TIER` | **pending** | str | Gate 3 |
| `ARCHIVE_FILE_MAX_ENTRIES` | 500 | int | Gate 4 |
| `EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES` | 30 | int | Gate 4 |
| `EVIDENCE_PROMOTE_MAX_RETRIES` | 5 | int | Gate 4 |

### A.3 新增 `EVT_*` 常量

这些**不**进 `config/__init__.py`——event_log 模块内聚 API 的一部分，
沿用 `memory/event_log.py:51-70` 的现行约定：

- `EVT_REFLECTION_EVIDENCE_UPDATED = "reflection.evidence_updated"`
- `EVT_PERSONA_EVIDENCE_UPDATED = "persona.evidence_updated"`
- `EVT_PERSONA_ENTRY_UPDATED = "persona.entry_updated"`

同步加进 `ALL_EVENT_TYPES` frozenset (`event_log.py:64`)。

