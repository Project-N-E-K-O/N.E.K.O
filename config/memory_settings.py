# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Memory evidence, recall, reflection, persona, and vector tuning settings."""

from .network import _read_bool_env, _read_str_env

TIME_ORIGINAL_TABLE_NAME = "time_indexed_original"
TIME_COMPRESSED_TABLE_NAME = "time_indexed_compressed"


# ── Memory evidence mechanism (docs/design/memory-evidence-rfc.md) ────
# 用户驱动的 evidence 计数器相关常量。所有评分计算都以 "净用户确认次数"
# 为单位（§3.1.2 偏离 task spec 原公式——去掉 importance 项）。阈值改值
# 会产生实际 behavior 变化，详见 RFC §6.5 pre-merge reviewer gates。

# §3.1.4 派生状态阈值
EVIDENCE_CONFIRMED_THRESHOLD = 1.0   # score ≥ 1 → confirmed
EVIDENCE_PROMOTED_THRESHOLD = 2.0    # score ≥ 2 → promoted
EVIDENCE_ARCHIVE_THRESHOLD = -2.0    # score ≤ -2 → archive_candidate

# 强力记忆 OFF（powerful_memory_enabled=False）时的 time-driven fallback 阈值。
# pre-RFC 行为：不靠 evidence_score，纯按 reflection 年龄推进 lifecycle，零
# LLM 成本。pre-RFC 用 3 天，但实测过激（"3 天没否认 != 用户认可"）；这里
# 拉到 7 天给用户更长窗口主动反驳。
WEAK_MEMORY_AUTO_CONFIRM_DAYS = 7   # pending → confirmed (按 created_at 计)
WEAK_MEMORY_AUTO_PROMOTE_DAYS = 7   # confirmed → promoted (按 confirmed_at 计)

# §3.5.3 归档相关（sub_zero_days 计数 + 分片大小上限）
EVIDENCE_ARCHIVE_DAYS = 14           # sub_zero 累计达此天数 → 真正归档
ARCHIVE_FILE_MAX_ENTRIES = 500       # 归档分片文件单文件最大 entry 数

# §3.1.5 ignored 扣分
IGNORED_REINFORCEMENT_DELTA = -0.2   # check_feedback ignored → reinforcement += delta

# §3.1.8 每种 signal 源的 delta 权重（v1.2.1：区分 direct vs indirect）
# 直接信号（用户显式回应 surfaced reflection 或命中负面关键词）权重 1.0；
# 间接信号（Stage-2 LLM 推断 fact 对 reflection 的关系）权重 0.5，避免
# LLM 误关联把 evidence 污染太快。
USER_FACT_REINFORCE_DELTA = 0.5      # Stage-2 reinforces（间接，银标准）
USER_FACT_NEGATE_DELTA = 1.0         # Stage-2 negates（否定即使间接也保留强权，
                                     # 因 LLM 判 negates 通常语义更明确）
USER_CONFIRM_DELTA = 1.0             # check_feedback confirmed（直接，金标准）
USER_REBUT_DELTA = 1.0               # check_feedback denied（直接）
USER_KEYWORD_REBUT_DELTA = 1.0       # 关键词 + LLM target 检查（直接 + 显式）

# user_fact reinforces 的 combo bonus：累计 count 超过阈值后，每条新信号额
# 外加 bonus，让"用户反复间接表达"的信号仍能追上"一次直接确认"的权重。
# 默认：前 2 条各 0.5；第 3 条起每条 0.5 + 0.5 bonus = 1.0。
USER_FACT_REINFORCE_COMBO_THRESHOLD = 2   # count > threshold 时激活
USER_FACT_REINFORCE_COMBO_BONUS = 0.5     # 超阈值后每条的额外加权

# §3.4.3 signal 抽取背景循环触发条件
EVIDENCE_SIGNAL_CHECK_ENABLED = True             # 独立开关
EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS = 10         # 累积 N 轮触发
EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES = 5           # 或空闲 N 分钟触发
EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS = 40      # 轮询间隔（与 IDLE_CHECK_INTERVAL 对齐）
EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS = 30    # Stage-2 LLM rerank 后进 prompt 的 obs 上限（减少 NxM 配对决策点）

# ── activity_guess 自适应退避门控 ──────────────────────────────────────
# 活动心跳 (main_logic/activity/tracker.py:_activity_guess_loop) 通过 emotion-tier
# LLM 把"用户在干嘛"叙述出来，只喂 proactive 搭话 prompt。这组旋钮约束「活动没
# 实质变化时」它多久刷一次——用户在两个 app 间来回切窗口曾让它每 ~40s 烧一次
# (静默, 无业务日志) 无限持续。详见 main_logic/activity/activity_guess_gate.py。
# 同一活动每被重述一次，下次重述间隔就 ×MULTIPLIER 增长，封顶 CAP：30→120→480→900。
# CAP 选 900s 对齐 AWAY_IDLE_SECONDS（state_machine.py，挂机 15min 进 away 后心跳硬 bail），
# 这样稳定活动退避到地板时差不多也该转 away 了。消费端 get_snapshot 读 cache 无 TTL
# 守卫，所以 CAP 放大不会让 proactive 拿到“过期”叙述（叙述只描述“在干嘛”，旧 = 仍准）。
ACTIVITY_GUESS_BACKOFF_BASE_SECONDS = 30.0   # 两次调用之间的硬地板 + 首次重述间隔
ACTIVITY_GUESS_BACKOFF_MULTIPLIER = 4.0      # 每次重述后退避间隔的增长倍数（必须 > 1）
ACTIVITY_GUESS_BACKOFF_CAP_SECONDS = 900.0   # 活动稳定后重述间隔的封顶（对齐 AWAY_IDLE 15min）
ACTIVITY_GUESS_SIG_CACHE_SIZE = 8            # 退避记忆的「不同活动签名」条数

# ── AI-aware Stage-1 (path B) ─────────────────────────────────────────
# 原 SignalLoop (path A) 只看 user 消息，导致 PR #1346 之后 AI 自我披露 + proactive
# 引入的屏幕/活动上下文全失明。Path B 走每 N 个 A tick 触发一次的 piggyback
# 节奏：A 跑完后 b_tick_counter++，达到 N 就跑 B；窗口下游边界用 A 实际处理过
# 的最晚 msg ts（不是 wall-clock now）保证 B 看的消息严格被 A 看过。
EVIDENCE_AI_AWARE_EVERY_N_A_TICKS = 3
"""Path B 每 N 次 A tick 触发一次（piggyback 在 A 循环里，不维护独立 wall-clock cadence）。
- 选 3：A 平均 5 min 一次 tick → B 平均 15 min 一次。tempo 跟着对话强度自适应——
  用户聊得越多 B 越频繁，符合"对话量大才需要补抓 AI fact"的直觉
- B cold start lookback 自动 = N × EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES = 15 min"""

MAX_AI_AWARE_WINDOW_MSGS = 200
"""Path B 单次窗口 SQL LIMIT 上限。挂机后重启 / 长 idle 突发 burst 可能让
[last_b_check_ts, last_a_msg_ts] 窗口跨越数小时百余条消息——cap 住防 prompt
爆炸。LIMIT 在 SQL 层执行（aretrieve_original_by_timeframe 的 limit_rows 参数），
ORDER BY ts ASC 取最早 N 条而不是最新（保 cursor 单调推进）。"""

MAX_KNOWN_POOL_FACTS = 30
"""Path B prompt 里塞的"已知 fact 池"上限（按 importance DESC 取前 N）。
- 30 × ~30 tok = ~900 tok overhead，控制在 prompt 总 budget 的 ~20%
- 作用：让 path B 的 LLM 知道哪些 fact 已被 path A 抽出，主动避免重抽 user 段
  内容；命中的 fact 通常带 source='user_observation'"""

# §3.5 / §6.5 Gate 4：归档扫描背景循环间隔
# 1 小时一次：sub_zero_days 计数本身按"自然日"防抖（每天最多 +1），
# 所以扫描频率 ≥ 一天即可保证不漏；选 1h 是为了让"score 跌穿 0 当天"
# 也能尽快被抓住而非等到次日 00:00。低频远低于 evidence 信号循环
# (40s)，对 IO/CPU 影响可以忽略。
EVIDENCE_ARCHIVE_SWEEP_INTERVAL_SECONDS = 3600

# §3.6 render budget（PR-3 使用，此处先占位）
PERSONA_RENDER_MAX_TOKENS = 2000         # 非-protected persona 预算
REFLECTION_RENDER_MAX_TOKENS = 2000      # reflection 渲染预算（pending+confirmed 总和）
PERSONA_RENDER_ENCODING = "o200k_base"   # tiktoken encoding

# ── 混合记忆召回（recall_memory 工具后端） ───────────────────────────────
# 模型决定调 recall_memory(query) 时，memory_server 在内存里并行跑 BM25 +
# cosine 召回，两路各自阈值过滤 + 限 top-K，RRF 融合后整体再限 N 条返回。
#
# 候选范围：
#   - BM25 池：     facts.json + reflections.json + facts_archive.json
#                  （BM25 对大池子廉价，archive 也能搜到罕见关键词命中）
#   - Embedding 池: facts.json + reflections.json
#                  （embedding 计算贵 + archive 已经超出常态记忆窗口；
#                   persona 整段不入池——它已经被常态渲染进 system prompt，
#                   再检索就是冗余）
#
# 阈值是经验值，跑起来再调；cosine 用 sentence-embedding 常见的相关性下限
# 0.3；BM25 用 0.1 接近 "any meaningful overlap"（零 overlap 早就被
# _bm25_rank 的 score > 0 卡掉了，0.1 主要挡偶发高频词碰瓷）。
#
# ⚠️ BM25 阈值不能定高：Okapi 公式在小 pool 下 IDF 系数自然就矮，
# 单 doc pool 即使 exact match 最高也就 ~0.72（``log((1-1+0.5)/(1+0.5)
# + 1) × (k1+1)``）；2-doc pool 两条都有词时 IDF 跌到 ~0.18。最初拍
# 1.0 是用大语料经验值，结果新用户 / 小语料 / 高频词查询全部被阈值
# 杀掉，BM25 兜底功能等于死掉（codex P1 review on PR #1385）。
HYBRID_RECALL_BUDGET_EACH = 4            # 每路（BM25 / embedding）top-K 上限
HYBRID_RECALL_BUDGET_TOTAL = 8           # RRF 融合后总条数上限（两路去重 + 取分前 N）
HYBRID_RECALL_TIME_BUDGET = 8            # 按时间回溯（recall_memory time 参数）返回的最接近条数上限
HYBRID_RECALL_COSINE_THRESHOLD = 0.3     # cosine < 阈值视为不相关
HYBRID_RECALL_BM25_THRESHOLD = 0.1       # BM25 < 阈值视为不相关（保 small-pool exact match）
HYBRID_RECALL_RRF_K = 60                 # RRF 常数（k=60 = Elastic / OpenSearch 默认）

# ========================================================================
# §3.7 LLM Context & Output Budget
# ------------------------------------------------------------------------
# 所有"会被拼进 LLM messages 的输入侧 component"和"LLM 输出侧 max_tokens"
# 都集中在这里。对应的设计文档：docs/design/llm-prompt-budget.md
#
# 命名约定：
#   *_MAX_TOKENS                       → tiktoken o200k_base token 数
#                                         （≈ 1.3-1.5 CJK char / 4 EN char）
#   *_TRIGGER_TOKENS                   → 触发某个动作的 token 阈值（不是硬上限）
#   *_MAX_ITEMS / *_MAX                → 条数（消息 / deque maxlen / list[-N:]）
#   *_MAX_CHARS                        → 字符数（仅非 prompt-facing 的 UI /
#                                         payload 防爆流程用，不作为 LLM input
#                                         budget 证据）
#   *_BYTES                            → 字节
#   *_MS                               → 毫秒
#
# 注释格式（每条常量）：
#   - "用途"：这个值会卡哪个 component
#   - "上游"：被 cap 的内容来自哪里（用户输入 / 外部 API / 内部计算）
#   - 设计依据 / 互动关系（如有）
#
# 已知"咎由自取"项（NOT capped by design）：
#   - 用户原话直接拼进 HumanMessage（omni_offline_client.py:413）
#   - OpenClaw magic intent user_text（用 1MB 输入做 80-token 分类，自找的）
#   - emotion 分析 user text
#   - bilibili knowledge_context（用户配置的知识库）
#   - 插件自定义 prompt / strategy 文件（由插件自行管理）
# 详见 docs/design/llm-prompt-budget.md "已知不 cap 项"。
# ========================================================================

# ---- Memory: recent history compression ----
RECENT_HISTORY_MAX_ITEMS = 10
"""压缩后保留的近期消息条数。
- 用途：CompressedRecentHistoryManager 把超过 compress_threshold 的旧消息
  压缩成 1 条 summary 后，原始消息列表保留最后 N 条。
- 上游：用户和 AI 的对话流水。
- 互动：和 RECENT_COMPRESS_THRESHOLD_ITEMS 配对——压缩后保留 N 条 +
  Stage-1 summary 1 条 = N+1 条进入下次压缩计数。"""

RECENT_COMPRESS_THRESHOLD_ITEMS = 20
"""触发 LLM 压缩的条数阈值。
- 用途：当某 lanlan 的 user_histories 累积到 > 此值时调一次
  compress_history。
- 上游：累积的对话条数。"""

RECENT_SUMMARY_MAX_TOKENS = 1000
"""Stage-1 压缩输出的 token 上限。
- 用途：Stage-1 LLM 把 N 条原始消息压缩成一段文本；如果输出
  > 此值则触发 Stage-2 进一步压缩（500 chars/words 硬截）。
- 上游：Stage-1 LLM 自由生成的摘要长度。
- 触发关系：output_tokens > 此值 → further_compress() 二次压缩。"""

RECENT_PER_MESSAGE_MAX_TOKENS = 500
"""压缩输入的单条 message token 上限。
- 用途：compress_history 把每条原始 message 拼进 prompt 前先做头尾保留
  截断（utils.tokenize.truncate_head_tail_tokens，head=tail=250）。
- 上游：用户/AI 的原始对话文本，正常一轮 30-500 token，长贴可能数 KB。
- 截断策略：保留头尾各 250 token，中段用 "…[省略中段]…" 替换。"""

RECENT_COMPRESS_INPUT_BUDGET_TOKENS = 8000
"""后台 best-effort 压缩的单段输入 token 预算（分段阈值）。
- 用途：待压积压渲染成文本后若超过此值，compress_history 走分段
  map-reduce——切成每段 ≤ 此值的小段分别压成中间摘要，再 reduce 成最终
  备忘录，减小单次 LLM 输入、避免输入过大导致超时。未超此值的正常压缩
  走原一次性路径，行为不变。
- 上游：积压对话渲染文本的 token 数。"""

RECENT_HARD_CAP_TOKENS = 60000
"""recent 历史的硬上限（最终兜底，平时不触发）。
- 用途：压缩持续失败（如持续 429，best-effort 后台也救不回）导致历史
  一直压不掉、无限膨胀时，update_history 保留完整历史前若总 token 超过
  此值，丢弃最旧的未压缩对话原文，保留近期若干条 + 备忘录，保证 prompt
  有界。设得很大，只作最后防线。
- 上游：未被压缩而累积的 recent 历史 token 数。"""

# ---- Memory: reflection ----
REFLECTION_TEXT_MAX_TOKENS = 150
"""单条 reflection 文本的 soft cap。
- 用途：超过此值的 reflection 在保存时会剥离 ontology 字段
  (relation_type / temporal_scope) — 文本本身不丢。
- 上游：LLM 综合若干 fact 后输出的反思文本。"""

REFLECTION_SURFACE_TOP_K = 3
"""单次 surfacing 最多返回的反思条数。
- 用途：get_pending_reflections_for_check / followup 等查询接口的截断。
- 上游：满足 evidence_score≥0 且 cooldown 已过的候选反思集合。"""

REFLECTION_SYNTHESIS_FACTS_MAX = 20
"""单次 reflection synthesis 最多带入的 unabsorbed fact 数。
- 用途：_synthesize_reflections_locked 调用 LLM 前先按 importance/创建
  时间排序，截到此数。
- 上游：用户长期不"吸收"事实就会堆积；外循环（aget_unabsorbed_facts）
  当前没数量限制，所以这层是唯一保护。
- 设计依据：30 条 × 平均 50 token = 1500 token，留给 LLM 综合处理够用。"""

MEMORY_REFLECTION_SYNTHESIS_INTERVAL_SECONDS = 180
"""``_periodic_reflection_synthesis_loop`` 每轮轮询间隔（秒）。
- 用途：后端定期对每个角色调 ``reflection_engine.synthesize_reflections``。
- 设计依据：synthesize_reflections 内部对"同批 source_fact_ids → 同 rid"做
  幂等 short-circuit，无新 unabsorbed fact 时 LLM 不会被调，所以这层只是
  调度频率上限。**真 LLM 调用频率约等于"用户在 N 秒内新积了 ≥5 条 unabsorbed
  fact 的次数"**，与 SignalLoop 实际产出速率绑死、与本常量解耦——把间隔从
  600s 缩到 180s 不会按比例加 LLM 成本。
- 选 180s：对齐 ``AUTO_PROMOTE_CHECK_INTERVAL = 180s``。两条 loop 一个产
  pending、一个把 pending 推 confirmed，节奏对齐让 user-visible 状态机延迟
  最短（合成 → 下一 tick 内就能被 promote 看到）。也跟
  ``EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES * 60 = 300s`` 错峰，让 SignalLoop 抽
  完一批 fact 后 1-2 个 reflection tick 内能消化掉。
- 历史：以前 reflection 合成挂在 ``/api/proactive_chat`` handler 里（PR #1015
  顺手塞的，见 main_routers/system_router.py 历史 blame），整套合成链路与
  前端 setTimeout 强耦合——前端不开 / proactive 不触发 → reflection 永远不
  增长。本常量配套的后端 loop 把合成从 HTTP/前端解耦，与其他 9 条 periodic
  loop（rebuttal / auto_promote / idle_maint / signal_extraction / archive /
  refine 等）对偶。"""

REFLECTION_RELATED_PER_QUERY_K = 3
"""Reflection synthesis 时，每条 unabsorbed fact 单独 query 召回的 absorbed
fact 数量上限。
- 上游：synthesize_reflections 调 ``MemoryRecallReranker.aretrieve_per_query_topk``
  时按本常量给每条 query 配独立预算。
- 设计依据（PR #1401 thread 拍板）：原先用 max-pool top-K (=6 全局预算)，
  20 条 unabsorbed 主题分散时冷门主题会被高频主题挤掉冷板凳。改成 per-query
  K=3 + 全局 cap，保证每条 unabsorbed 至少能拿到自己的 top-3 锚（除非这条
  query embed 失败 / 候选池没语义匹配）。
- 单条 query 拿 3 条而不是 1 条：考虑到主题边界模糊（用户聊 MC 同时聊到
  红石和挖矿，cosine top-1 可能只命中其中一条），多给两条让 LLM 能看出
  "主题群"的轮廓。"""

REFLECTION_RELATED_TOTAL_CAP = 20
"""``aretrieve_per_query_topk`` 跨 query union+dedup 后的最终上限。
- 设计依据：与 ``REFLECTION_SYNTHESIS_FACTS_MAX`` (=20) 同档，让 anchor 集
  最坏也能跟 source 集等量——但实际命中通常远小于此（query 间 nearest
  neighbor 大量重叠 + dedup）。典型 batch 10 条 unabsorbed × per_query=3
  = 30 候选 → dedup 后落在 ~10-15 anchor。
- 上界用于防御性截断：极端"20 条全主题不重叠"假设下，per_query=3 × 20 = 60
  候选，dedup 不能去重时砍到 20，避免 prompt token 爆。
- prompt 实际成本：20 × ~50 tok ≈ 1000 tok anchor + 20 × ~50 tok ≈ 1000 tok
  source = 2k 上限，summary tier 模型完全吃得下。"""

# ---- Memory: temporal scope (memory/temporal.py) ─────────────────────
# Reflection 用 4 档 temporal_scope（pattern / state / episode / past）做时间
# 衰减。state 与 episode 各有 TTL，超期自动进过时 block。pattern 永不过时。
# `past` 是历史兼容值（旧数据可能存了），render 时直接进过时 block。
MEMORY_STATE_PAST_DAYS = 7
"""state 类 reflection 距 event 多少天后被视为已过时。
- 用途：memory.temporal.is_past_for_render；render 时把此条移入过时 block。
- 上游：reflection synth LLM 标注 temporal_scope='state' 的条目。"""

MEMORY_EPISODE_PAST_DAYS = 3
"""episode 类 reflection 距 event 多少天后被视为已过时。
- 用途：同上，但 episode 是一次性事件，衰减更快。
- 上游：reflection synth LLM 标注 temporal_scope='episode' 的条目。"""

MEMORY_SCHEMA_VERSION_CURRENT = 2
"""fact / reflection 当前 schema 版本号。
- v1（缺失或显式 1）：旧 ontology（current/ongoing/None temporal_scope，无
  event_when）。
- v2：新 ontology（pattern/state/episode）+ event_start_at / event_end_at。
- 用途：背景循环找 schema_version < CURRENT 的条目慢慢重判升版本。"""

# ---- Memory: slow recheck loop (memory/temporal.py + app/memory_server/) ─
MEMORY_RECHECK_ENABLED = True
"""慢速记忆重判循环总开关。
- 用途：app/memory_server/evidence_loops.py _periodic_slow_memory_recheck_loop 启动门控。
- 关闭时老数据不会被升版本（render 兜底走 pattern 不淡出）。"""

MEMORY_RECHECK_INTERVAL_SECONDS = 30
"""慢速重判循环单条间隔。
- 用途：每 N 秒重判 1 条 reflection / fact。
- 上游：背景循环 sleep；设计参考 §3.5 archive_sweep（更慢、低 IO）。"""

MEMORY_RECHECK_INITIAL_DELAY_SECONDS = 180
"""慢速重判循环启动延迟（错峰）。
- 用途：和现有 6 个循环错峰，避开启动峰值。
- 现有 _INITIAL_DELAY_* 在 20s~250s，本值 180s 接近末尾。"""

MEMORY_RECHECK_MAX_ATTEMPTS = 5
"""单条 v1 entry 重判失败几次后放弃，避免饥饿后续合法 v1 条目。
- 失败定义：LLM 调用抛异常、返回非 dict、temporal_scope 不在合法集合
  （reflection 限定 pattern/state/episode）。
- 计数字段：reflection / fact entry 上的 `recheck_attempts` (int)。
- 命中阈值的条目仍保留 schema_version<2（不静默升版洗白），但被 filter
  排除，让循环把名额匀给其它 v1 条目。dev 可读 logger.debug 看积压。"""

MEMORY_LIVENESS_MAX_ATTEMPTS = 5
"""LLM 终态失败 N 次后强推 progress marker / dead-letter 的统一上限。
- 适用场景：所有"同点 input + 无 counter + LLM 永久失败 → 永久卡死"的后台
  路径。包括 signal extraction path A/B、rebuttal feedback、persona
  corrections resolve、fact dedup resolve、refine cluster、outbox handler。
- 治理思路：参考 `MEMORY_RECHECK_MAX_ATTEMPTS` (schema 重判 dead-letter) 的
  套路，把"同一 cursor / 队头 / cluster_hash / op 反复打 LLM"收敛掉，避免
  毒窗口 / 毒 payload 让整条 pipeline 哑火。
- 失败定义：LLM 返 None / 抛异常 / handler raise / parse 失败等终态。
- 5 跟 `MEMORY_RECHECK_MAX_ATTEMPTS` 同口径——按 40s 一轮算 3 分钟级窗口，
  跨过偶发 transient failure 够用；再多就属于真正 poison。"""

MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS = 3
"""历史审阅因输出 token 耗尽而暂停前的连续失败次数。

- 只统计 provider 明确返回 ``length`` / ``max_tokens``，或空正文且输出 token
  已触及 ``LLM_OUTPUT_GUARD_MAX_TOKENS`` 的调用；网络、429、普通 JSON 错误仍走
  ``MEMORY_LIVENESS_MAX_ATTEMPTS`` 的通用 fingerprint 退避。
- 达到 3 次后按角色暂停 review。新增消息不会解禁；只有当前 review 上下文 token
  数严格低于失败期间的最小值（通常由 recent compression 造成）才清零恢复。"""

MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS = 5 * 60 * 60
"""dead-letter 的时间冷却自愈窗口（秒）。

- 问题：达 `MEMORY_LIVENESS_MAX_ATTEMPTS` 被冻结的 entry（reflection synth /
  schema recheck / refine cluster）只在"成功"或"输入变化"时才解冻。但当失败
  其实是**一次性持续故障**（correction 模型快照下线一直超时 / cloudsave 卡
  维护态 / FS 只读）时，故障期间会把一批无辜 entry 一路 bump 到 MAX 永久冻死，
  故障恢复后也不会自愈（内容没变、又进不了候选）。
- 治理：给这些 dead-letter 加时间冷却——冻结后每过本窗口放行**一次** probe。
  probe 成功 → 计数清零彻底恢复；probe 失败 → 重新计时、再等一个窗口。这样
  一次性故障 5h 后自愈，真正 poison 仍被压到"每 5h 一次"不空烧。
- **不适用 memory_review**：它的恢复机制是"对话尾部 fingerprint 变化即复位"
  （master 一发新消息就重试），不需要也不应该有时间自愈——挂机期间就该一直停。
- 5h：refine cron 30min 一轮 → 一次 >2.5h 的模型宕机会把 entry 顶到 MAX；
  5h 冷却确保宕机恢复后下一轮就能 probe，又远大于偶发抖动窗口。"""

# ---- Memory: followup picker (memory/reflection.py) ─
REFLECTION_FOLLOWUP_WEIGHTED = True
"""主动搭话 followup 候选采样是否按 evidence_score 加权随机。
- 用途：_filter_followup_candidates；False 时回退到旧行为（按落盘顺序取
  top-K）。
- 设计依据：候选池大时纯落盘顺序总取同一批，造成主动搭话内容雷同。"""

REFLECTION_FOLLOWUP_WEIGHT_BASE = 0.5
"""加权采样的最低权重（score=0 时也有此权重，避免全 0 score 时退化）。"""

# ---- Memory: summary stale prompt (memory/recent.py) ─
RECENT_SUMMARY_STALE_HOURS = 1
"""距上次"LLM 实际更新 past block 的时刻"超过此小时数，下一次 compress
时在 prompt 头部附加"时间已过 X"提示，让 LLM 主动把过时片段挪进 summary
内部的过时 block。
- 锚点：不是"上次 summary 时间"——summary 每轮压缩都会跑，跟着锚点会让
  stale hint 永远跟在最后一次压缩后 1 小时，无法形成"每隔 N 小时刷一次
  past block"的稳定节奏。改记"上次 hint 真正注入的时刻"，即 LLM 实际
  被要求更新 past block 的那一刻。
- 上游：recent_meta.json 里的 last_past_block_update_at 字段。
- 注意：summary 的过时 block 只在当前 session 临时降级，不持久化到
  reflection / persona。"""

# ---- Memory: persona ----
PERSONA_MERGE_POOL_MAX_TOKENS = 4000
"""promote-merge 时同 entity persona+reflection 池总 token 上限。
- 用途：_allm_call_promotion_merge 把同 entity 的所有 confirmed/promoted
  persona 和 reflection 全拼进 prompt，本 cap 防止该池失控。
- 上游：同一 entity 长期累积的 persona/reflection。
- 注意：这条不复用 PERSONA_RENDER_MAX_TOKENS（render 是给主对话看的，
  merge 是给 promotion LLM 看的，需要更大的池才能做合并判断）。"""

# ---- Memory: 外部记忆导入 · persona LLM 融合预算 ----
# 背景（也是这条链路存在的根本原因）：persona 渲染进 system prompt 有一个严格的
# token 上限（PERSONA_RENDER_MAX_TOKENS，non-protected 条目共抢一个池），外部工作
# 区（OpenClaw/Hermes 的 USER.md / SOUL.md）动辄几十条自由 Markdown，若原样精确
# 去重后逐条追加，很快就会把 persona 池撑爆、把角色自身积累的印象挤掉。因此
# USER.md / SOUL.md 必须先经一次 LLM 融合（归纳/合并/去重/消歧/按重要度排序），
# 把内容压进下面的预算，再落盘为 non-protected persona 条目。
# 两个 entity 各自的融合产出上限（token）。neko(SOUL.md 助手人格) 给得比
# master(USER.md 用户) 多；两者合计 < PERSONA_RENDER_MAX_TOKENS，给对话中自然
# 积累的 persona 条目留出竞争空间。
EXTERNAL_IMPORT_PERSONA_NEKO_MAX_TOKENS = 1000
EXTERNAL_IMPORT_PERSONA_MASTER_MAX_TOKENS = 600
# 喂给融合 LLM 的输入池上限（原始候选拼起来的 token 上界，防超长 prompt）。
EXTERNAL_IMPORT_FUSION_INPUT_MAX_TOKENS = 6000
# 融合产出的单条 soft cap（token）：防 LLM 把多条揉成一条超长文本，在渲染层
# whole-entry 贪心截断里挤掉大量其它条目。
EXTERNAL_IMPORT_FUSION_ENTRY_MAX_TOKENS = 200

PERSONA_CORRECTION_BATCH_LIMIT = 10
"""单次 persona corrections resolve 处理的 batch 大小。
- 用途：_resolve_corrections_locked 从 pending_corrections 队列取前 N
  条丢给 LLM 做对错判断，剩下的下一轮再处理。
- 上游：pending_corrections 队列。"""

PERSONA_VERSION_HISTORY_MAX = 5
"""单条 persona entry 的 version_history 保留上限（Phase B-1）。
- 用途：每次 resolve_corrections 的 replace/merge 或 apply_refine_actions
  的 merge/modify append 后裁到最近 K 个，防长期运行无限累积。
- 老版本直接丢；version_history 是审计而非数据，超过 5 条价值极低。"""

MEMORY_LLM_HARD_TIMEOUT_SECONDS = 110
"""所有 memory 后台 LLM 调用的硬上限 timeout（秒）。
- 上游转发服务器 hard timeout 120s；client 必须留 ≥10s margin，否则会被
  转发层先 timeout 截断，连 response 都拿不到。**不能超过 110**。
- 覆盖：reflection synthesis / persona correction / memory_refine /
  recent review_history 等所有后台跑的 LLM 调用。
- 不适用：用户面前的 chat / realtime 路径有独立的更严 timeout 控制。"""

LLM_OUTPUT_GUARD_MAX_TOKENS = 4096
"""变长输出 LLM 调用的 max_completion_tokens **runaway guard**（不是紧 budget）。
- 用途：那些输出长度天然变动、没有紧的 task-specific budget 的调用——memory
  的结构化 JSON（reflection / recall / persona / facts / refine / dedup recheck）、
  fact dedup、card-assist、window-title 关键词等。
- 取值：4096。**必须保持在主流 provider 的输出上限之内**——`max_completion_tokens`
  是上限不是目标，但很多 provider（OpenAI 及兼容端点）会在请求时就校验它 >
  模型 max output 而直接 400，而不是退回默认值。这正是 `omni_offline_client.
  _budget_to_max_tokens` 对 unlimited 直接 **omit 字段**（"large fixed values get
  rejected as out-of-range by some providers"）的原因。8000 会打爆 max output<8000
  的自建/老模型；4096 是绝大多数 summary/correction/agent tier 模型都接受的安全档，
  同时对这些任务的正常输出（含 thinking reasoning）仍是宽裕兜底。
- 政策：LLM_OUTPUT_BUDGET lint 要求每个 client 构造都带 token budget；本常量是
  "无紧 budget 但仍需有上限"这类调用的统一来源（见 docs/design/llm-prompt-budget.md §0）。
- 不适用：有明确紧 budget 的调用（emotion / translation / vision / plugin 粗筛等）
  仍用各自的 *_MAX_TOKENS 常量，不要图省事换成本 guard。
- 残留边界：max output < 4096 的极老/极小模型仍可能 400；这类安装可下调本常量。
  彻底鲁棒需要 per-model 上限元数据（codebase 目前不跟踪），故取保守定值。"""

ICEBREAKER_FREE_TEXT_INTERPRETER_TIMEOUT_SECONDS = 20.0
"""新用户破冰自由输入解释器 LLM timeout（秒）。用户面前的短分类/短回复调用，卡住时应快速失败。"""

ICEBREAKER_FREE_TEXT_OUTPUT_MAX_TOKENS = 512
"""新用户破冰自由输入解释器输出 token 上限。输出固定 JSON，512 只作短任务上限。"""

ICEBREAKER_FREE_TEXT_ASSISTANT_LINE_MAX_TOKENS = 800
"""破冰自由输入解释器：当前 YUI 台词输入 token 上限。"""

ICEBREAKER_FREE_TEXT_USER_TEXT_MAX_TOKENS = 800
"""破冰自由输入解释器：用户自由输入 token 上限。"""

ICEBREAKER_FREE_TEXT_OPTION_LABEL_MAX_TOKENS = 200
"""破冰自由输入解释器：单个选项文案 token 上限。"""

ICEBREAKER_FREE_TEXT_HISTORY_TEXT_MAX_TOKENS = 240
"""破冰自由输入解释器：近期自由输入记录单段文本 token 上限。"""

ICEBREAKER_FREE_TEXT_HISTORY_MAX_ITEMS = 4
"""破冰自由输入解释器：近期自由输入记录最多带入条数。"""

ICEBREAKER_FREE_TEXT_REPLY_MAX_TOKENS = 240
"""破冰自由输入解释器：模型 reply 字段清洗后的 token 上限。"""

DIALOG_LLM_STREAM_TIMEOUT_SECONDS = 180
"""主对话流式 LLM client 的总请求 timeout（秒），作 hang-guard。
- 用途：OmniOfflineClient 的 streaming chat client（stream_text /
  prompt_ephemeral 共用同一个 self.llm）。SDK 的 timeout 是整次请求上限，
  对流式即"出完整条回复"的时间。
- 取值：刻意取大（180s）——正常 TTS 短回复 / summary 3000-token 长回复
  都远低于此，不会被截；只在上游真正卡死（既不出 token 也不断流）时兜底
  释放连接。比 MEMORY_LLM_HARD_TIMEOUT_SECONDS 大，因为主对话是用户面前
  路径，宁可多等也不能误截正常回复。
- 政策：LLM_OUTPUT_BUDGET lint 要求每个 client 构造都带 timeout；本常量是
  主对话流式路径的统一来源。"""

FOCUS_THINKING_EXTRA_TOKENS = 800
"""凝神（focus / thinking-on）轮次额外放宽的 max_completion_tokens。
- 背景：thinking 模型（Qwen enable_thinking / GLM·Kimi·Doubao thinking.type /
  OpenRouter reasoning.effort）的 reasoning token 与正式回复共享同一个
  max_completion_tokens 预算池（见 docs/design/llm-prompt-budget.md §0），
  凝神轮一开思考就会从回复额度里扣，把正式回复挤短。
- 作用：仅在 thinking_on 的那一轮，把 API 端 max_completion_tokens 临时
  抬高本值，给推理链单独留头寸，不动 Python-side 长度 guard（回复可见
  长度仍按 max_response_length 收口）。
- 路由：作为 per-call override 经 _focus_stream_overrides → astream →
  ChatOpenAI._params 透传，不改 self.llm 实例属性（与 extra_body 同一条
  per-call 路径，并发安全、下一轮自动复位）。
- 适用面：Claude 凝神保持 thinking-off（config/providers.py），本加值对其
  天然 no-op；Gemini thinking_budget 是独立字段（800），本余量也足够覆盖。
- 取值：扁平 800，不按 provider 分叉——只在真正开思考的轮次生效。"""

# ---- Memory: refine (Phase A-3) — MemoryRefineEngine 的 cron 参数 ----
# 通用 cosine 聚类 + LLM 决议管道，复用在 PERSONA_REFINE 和
# REFLECTION_REFINE 两条 cron 上。fact 不可变（只能作 merge/modify
# 的信息源，不能被 split/discard）。

MEMORY_REFINE_COSINE_THRESHOLD = 0.82
"""refine cluster 的 cosine 阈值。比 FACT_DEDUP 的 0.85 略松——persona
和 reflection 文本通常更长，cosine 难拉到 0.85+；同时这里是聚类找
"相关"而非 dedup 找"等价"，松一点更合适。"""

MEMORY_REFINE_TOPK_PER_ENTRY = 5
"""单个 entry 在邻接图上最多保留的近邻数（双 cap 的第二条）。防止某条
被高度引用的 hub entry 把一大坨弱相关条目都拉进同一 cluster。"""

MEMORY_REFINE_CLUSTER_SIZE_MAX = 6
"""单 cluster 内最多成员数。超过 6 LLM 难以一致处理；溢出的 cluster
按 cosine 强度截到前 6 条。"""

MEMORY_REFINE_REVISIT_AFTER_DAYS = 30
"""同一 cluster_hash 多久后允许重审（即使 hash 全员命中也不 skip）。
LLM 行为月级别可能漂移，1 个月重审一次成本可控。"""

MEMORY_REFINE_CLUSTERS_PER_PASS = 3
"""单次 cron 触发最多送 LLM 的 cluster 数。按饥饿度（cluster 内
min(last_refine_at)）升序取前 N。约 3 次 LLM call ≈ 60-90s 阻塞。"""

MEMORY_REFINE_CRON_INTERVAL_SECONDS = 1800
"""PERSONA_REFINE / REFLECTION_REFINE cron 的轮询间隔（秒）。
- 30 分钟一次；engine 内 cluster_hash skip 让"刚审过"的 cluster
  零成本跳过，所以高频触发也不会浪费 LLM token。
- 两条 cron 用同一间隔，靠 _INITIAL_DELAY_* 错峰起始。"""

# ---- Memory: recall ----
RECALL_COARSE_OVERSAMPLE = 3
"""vector coarse-rank 的过采样倍数。
- 用途：top_k = budget * 此值；coarse 阶段多取 3× 候选给 LLM rerank
  挑选。
- 上游：embedding 检索的 candidate pool。"""

RECALL_PER_CANDIDATE_MAX_TOKENS = 200
"""LLM rerank 输入的单条 candidate text 上限。
- 用途：_fine_rank 拼 candidates 前对每条 candidate.text 做截断。
- 上游：archived fact / observation 文本。"""

RECALL_CANDIDATES_TOTAL_MAX_TOKENS = 15000
"""LLM rerank 输入的 candidates 拼合后总 token 上限。
- 用途：候选数已 cap 但单条单独 cap 仍可能撑爆——这条是兜底。
- 上游：cap 之后的 candidates 列表序列化。
- 设计依据：理论上 budget*3 × per_candidate = 600*200 = 120k；25k 是
  实际安全值，超出时按尾部截断（保留高 score 的）。"""

# ---- Memory: evidence signal detection ----
EVIDENCE_PER_OBSERVATION_MAX_TOKENS = 200
"""Stage-2 signal detection 输入的单条 observation text 上限。
- 用途：_allm_detect_signals 拼 observations 前对每条 text 截断。
- 上游：archived fact / observation 文本。"""

EVIDENCE_OBSERVATIONS_TOTAL_MAX_TOKENS = 15000
"""Stage-2 signal detection observations 拼合后总 token 上限。
- 用途：兜底，防止单条上限 × 条数撑爆。
- 上游：cap 之后的 observations 列表序列化。"""

EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS = 20
"""Stage-2 signal detection 单次 batch 处理的 new_facts 上限。
- 用途：_allm_detect_signals 入口对 new_facts 按 importance DESC 截到 N 条；
  超出部分留在 facts.json 中 `signal_processed=False`，下次 idle 维护循环
  再 drain 一批。
- 与 FACT_DEDUP_BATCH_LIMIT 同口径（LLM 在 N×M 配对决策时的舒适 batch
  ~20 条），避免 LLM 在 30+ 条 new_facts 上判失焦。
- 上游：Stage-1 LLM 抽取出来的 new facts 列表。"""

NEGATIVE_KEYWORD_CHECK_CONTEXT_ITEMS = 3
"""负面关键词检查带的 user message 上下文条数。
- 用途：memory_server._amaybe_trigger_negative_keyword_hook 取 user
  消息列表的最后 N 条作为 LLM 上下文。
- 上游：会话流水。"""

# §3.9 merge-on-promote 节流（PR-3 使用）
EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES = 30      # 连续失败节流窗口
EVIDENCE_PROMOTE_MAX_RETRIES = 5                 # 死信阈值

# §6.5 pre-merge reviewer gates —— 草案值，reviewer 敲定前保留
# Gate 1: 半衰期（§3.5.2）
EVIDENCE_REIN_HALF_LIFE_DAYS = 30        # reinforcement 半衰期
EVIDENCE_DISP_HALF_LIFE_DAYS = 180       # disputation 半衰期（longer than rein）

# Gate 2: reflection 合成 context 量（§3.4.3 阶段 2）
REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_COUNT = 10   # 最近 N 条 absorbed fact 作参考
REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_DAYS = 14    # 且在 N 天内

# Gate 3: LLM tier 选型（候选见 RFC §6.5 Gate 3 表）
# "summary" = qwen-plus 级；"correction" = qwen-max 级；"emotion" = qwen-flash 级
EVIDENCE_EXTRACT_FACTS_MODEL_TIER = "summary"       # Stage-1 抽 fact
EVIDENCE_DETECT_SIGNALS_MODEL_TIER = "summary"      # Stage-2 判 signal 映射
EVIDENCE_NEGATIVE_TARGET_MODEL_TIER = "emotion"     # 关键词二次判定（延迟敏感）
EVIDENCE_PROMOTION_MERGE_MODEL_TIER = "correction"  # Promote 合并决策


# memory-enhancements P2: vector hybrid retrieval (memory/embeddings.py).
# Master kill switch + auto-resolve hints. The service degrades to no-op
# under any of: VECTORS_ENABLED=False / RAM < min / no onnxruntime / no
# model file. See memory/embeddings.py docstring for the full fallback
# matrix. Defaults are tuned so the feature is opt-out at the install
# level (drop the model file → on; remove it → off) without a config edit.
# 默认值不变；额外支持 env 覆盖（opt-in 逃生口，不设就走原 auto 策略）。
# 典型用途：无 AVX-VNNI 的老 CPU 上 auto 会自动关闭向量，用户可设
# NEKO_VECTORS_QUANTIZATION=int8 强制照跑 int8（慢但正确），无需重新打包。
VECTORS_ENABLED = _read_bool_env("VECTORS_ENABLED", True)        # master kill switch
VECTORS_EMBEDDING_DIM = "auto"               # "auto" | 32/64/128/256/512/768
VECTORS_QUANTIZATION = _read_str_env(        # "auto" | "int8" | "fp32" (fp32 needs model.onnx on disk)
    "VECTORS_QUANTIZATION", "auto", allowed=("auto", "int8", "fp32"),
)
VECTORS_MIN_RAM_GB = 4.0                     # below this → disabled regardless
VECTORS_MODEL_PROFILE_ID = "local-text-retrieval-v1"  # anonymous profile id + local model folder
# Warmup: the ONNX session (~150 MB unpack) loads on first triggering
# event after startup. The warmup task waits up to this many seconds
# after startup OR until first /process call, whichever comes first.
VECTORS_WARMUP_DELAY_SECONDS = 30
