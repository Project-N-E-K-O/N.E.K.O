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

"""Session lifecycle, anti-repeat, avatar, omni, and proactive search limits."""

# ---- Main: session / avatar / omni ----
SESSION_ARCHIVE_TRIGGER_TOKENS = 5000
"""会话历史归档触发的累计 token 总量。
- 用途：core.py 主循环每 turn-end 后检查；超过则置
  is_preparing_new_session=True，触发记忆压缩 + 新会话准备。
- 上游：当前会话的 conversation_history。
- 限制：仅对 OmniOfflineClient 路径生效（realtime 不维护历史，走轮次触发）。
- 设计依据：用户一轮平均 ~150 token + AI 一轮平均 ~400 token =
  ~550/轮；5000/550 ≈ 9 轮触发归档（与 SESSION_TURN_THRESHOLD 对齐）。"""

SESSION_TURN_THRESHOLD = 10
"""触发会话归档的用户轮次阈值。
- 用途：core.py:_session_turn_count >= 此值触发新会话准备（与
  SESSION_ARCHIVE_TRIGGER_TOKENS 是 OR 关系，任一满足即触发）。
- 计数语义：仅用户输入计数（AI 回复不算），见 core.py:980。
- 设计依据：~10 轮约对应 5500 token 总量，跟 token 触发对齐。"""

USER_DIRECTIVE_TTL_SECONDS = 3 * 86400
"""用户显式 ban-topic 指令（"别再提 X / stop saying X"）的存活时长。
- 用途：memory/user_directives.py 的 active 判定 + render_prompt_block
  注入到下次 session 启动的 system prompt。
- 设计依据：用户态度的有效期介于"本轮结束"和"永久偏好"之间——3 天足够覆盖
  连续几天的会话上下文又不至于把一时情绪固化成长期人设。
- 上游：main_logic/core.py:_build_initial_prompt 注入；
  memory/user_directives.py:UserDirectivesManager 内部判活 + 清理。"""

USER_DIRECTIVE_MAX_ACTIVE = 20
"""注入到 system prompt 的活跃 ban-topic 上限。
- 用途：UserDirectivesManager.get_active 截断到 last_seen 最新的 N 条。
- 设计依据：超过 20 个不同 ban-topic 同时活跃几乎一定是抽取出错或用户在
  故意刷指令——截断比把 prompt 塞爆好。"""

# ── 防复读（anti-repeat）BM25 相关 ─────────────────────────────────
ANTI_REPEAT_BG_WINDOW = 100
"""anti-repeat corpus 背景窗口长度（最近 N 条 AI 输出）。
- 用途：memory/anti_repeat.py 的滚动 corpus 保留最近 N 条文本算 DF。
- 设计依据：100 条 ≈ 用户半天到一天的对话量；窗口太短 IDF 不稳定，太长
  又会让一周前的偶发话题永远算"高 IDF unique"。"""

ANTI_REPEAT_FG_WINDOW = 5
"""anti-repeat 前景窗口长度（最近 N 条算"是否重复"）。
- 用途：BM25 评分把最近 N 条当 query corpus 算 TF；新 draft 与这 5 条比。
- 设计依据：5 条 ≈ 用户最近能感知到的复读窗口；7+ 已经记不清了。"""

ANTI_REPEAT_FG_TTL_SECONDS = 600.0
"""anti-repeat 前景窗口的时间新鲜度上限（秒）。仅作用于 FG（TF/复读判定），
不影响 BG（DF/IDF 词频背景，仍按 ANTI_REPEAT_BG_WINDOW 条数封顶）。
- 用途：memory/anti_repeat.py 的 score_draft / top_recent_topics 只把「最近
  ANTI_REPEAT_FG_TTL_SECONDS 内」的输出计入前景 TF；更早的条目照旧留在 BG
  里贡献 IDF。
- 设计依据：修复「空闲死锁」——主动搭话在用户空闲时才触发，而所有 drop 路径
  都不写 corpus、成功投递才写，于是空闲期 FG 窗被最近几条同话题（如屏幕解说）
  冻结，每轮打出同样的超高 BM25 → 永远 drop → 永远无法搭话。加了 TTL 后，空闲
  超此时长 FG 自然清空、bm25_score 命中 `not fg_docs` 返回 0，本轮放行。
- 取值：10 分钟。防复读本就只防「刚说过、又说一遍」的 back-to-back 复读；十分钟
  前说过的话题再提不算复读。BG（IDF 语境）不设 TTL，评分质量不受影响。"""

ANTI_REPEAT_INJECT_TOP_K = 6
"""注入 system prompt 的 "最近高频 topic 词" 数量。
- 用途：build_recent_topics_block 取 BM25 排名前 K 的 ngram。
- 设计依据：6 个词够覆盖"几个话题"，又不至于把 prompt 撑长。"""

ANTI_REPEAT_REGEN_THRESHOLD = 8.0
"""proactive 出口 BM25 总分超此值则触发 1 次 regen。
- 用途：system_router proactive 流式完成后评分；超阈值用 avoidance prompt
  重 sample 一次。
- 设计依据：经验起点；后续 testbench 调。"""

ANTI_REPEAT_DROP_THRESHOLD = 16.0
"""proactive regen 后仍超此值则放弃投递（不发）。
- 用途：避免 LLM 卡死在某个 topic 上连续复读。
- 设计依据：REGEN 的 2 倍，给 LLM 一次纠正机会。"""

ANTI_REPEAT_BM25_K1 = 1.5
"""BM25 k1 参数（控制 TF saturation 速度）。Robertson 经典推荐值。"""

ANTI_REPEAT_BM25_B = 0.75
"""BM25 b 参数（文档长度归一化强度）。Robertson 经典推荐值。"""

ANTI_REPEAT_MIN_DRAFT_TOKENS = 12
"""draft 短于此长度（tokens 数）就不评分，直接放行。
- 用途：避免"嗯。"、"好"这种短回复被错杀。
- 设计依据：~12 个 ngram token 才能形成稳定的 BM25 信号。"""

ANTI_REPEAT_EXEMPT_SOURCE_TAGS = frozenset({"MUSIC", "MEME"})
"""主动搭话里"复读判定从台词切到素材维度"的来源标签。
- 动机：BM25 防的是"话题/措辞复读"，但素材推送类 channel 的开场白天生模板
  化（推歌"换首歌 / 这旋律 / 听听看"、表情包"看这个 / 笑死"），台词长一个
  样、而推送的素材（曲目 / 表情包搜索关键词）却不同；用台词 BM25 判它属于
  天生误杀——博士连点几首后 FG 窗被音乐 intro 占满，分数爆表，后续自发推
  歌全被 drop，表现为"放音乐频率极低"。
- 语义：这类 channel 的复读按"素材本身"去重——MUSIC 看曲目、MEME 看搜索
  关键词（不是图片）。本轮素材与近期不雷同时，豁免台词级硬拦截（字面相似
  度 + BM25 regen/drop）直接放行；素材雷同（反复推同一曲目 / 同一关键词）
  才回落到正常台词判定，台词没雷同则依然能发。
- 另：这类 channel 的台词不录进 anti-repeat corpus（见 finish_proactive_
  delivery），免得模板化 intro 污染 FG 窗、漂移其它 channel 的复读基线；
  素材标识的近期去重走 system_router 的 _proactive_material_history。"""

AVATAR_INTERACTION_DEDUPE_MAX_ITEMS = 32
"""_recent_avatar_interaction_ids deque maxlen。
- 用途：去重已处理的 avatar 交互 ID。
- 上游：UI/avatar 端的交互事件序列。"""

AVATAR_INTERACTION_DEDUPE_WINDOW_MS = 8000
"""avatar 交互去重的时间窗口。
- 用途：cross_server _should_persist_avatar_interaction_memory 在此窗口
  内同 key 的交互不重复持久化。
- 上游：UI 端的交互时间戳。"""

AVATAR_INTERACTION_CONTEXT_MAX_TOKENS = 80
"""avatar 交互文本上下文的 token 上限。
- 用途：_sanitize_avatar_interaction_text_context 截断后写进 LLM
  prompt 作为 avatar 触发的现场上下文。
- 上游：avatar 端透传的现场文本片段。"""

PENDING_USER_IMAGES_MAX = 3
"""cross_server pending_user_images 保留的最近图片数。
- 用途：del pending_user_images[:-N] 滑动窗口。
- 上游：用户上传的图片队列。"""

OMNI_RECENT_RESPONSES_MAX = 3
"""omni_offline / omni_realtime 最近 AI 回复轮数。
- 用途：_recent_responses 列表 pop(0) 维护的滑动窗口；用于重复检测
  (_check_repetition)。
- 上游：当前会话内的 AI 历史回复。"""

OMNI_WS_FRAME_LIMIT_BYTES = 250_000
"""omni_realtime WebSocket 帧大小安全阈值。
- 用途：发送前检查 payload size，超过则拒绝（低于 256KB 服务器上限）。
- 上游：序列化后的 WS 帧字节数（不是 token）。"""

# ---- Main: proactive search & emotion ----
PROACTIVE_PHASE1_FETCH_PER_SOURCE = 10
"""Phase 1 每个信息源固定抓取条数。
- 用途：fetch_news_content / fetch_video_content 等的 limit 参数统一值。
- 上游：外部 web/news/video 抓取结果。"""

PROACTIVE_PHASE1_TOTAL_TOPICS = 12
"""Phase 1 输入给筛选 LLM 的候选话题总数。
- 用途：从所有 source 合并后去重，截到此数后送 LLM 筛选。
- 上游：cap 后的 fetch 结果汇总。
- 设计依据：原值 20。早期 external 是主要信号源，候选池开得很大。
  Phase 2 引入 vision / music / meme / reminiscence 等并行通道后，
  external 的相对权重下降——筛选 LLM 多看 8 条边际候选无助于挑出更
  好的 top-1，反而让 Phase 1 prompt 一次跑过 2k tokens 上限。下调到
  12 仍给筛选 LLM 充分多样性，且单次调用 token 减半左右。"""

PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS = 200
"""Phase 2 外部内容（news/video/social/meme 等）单条 token 上限。
- 用途：build_phase2_external_section 拼 system prompt 前对每条 web
  content 做截断。
- 上游：外部 API 返回的 title + source + url + 摘要。
- 设计依据：单条 200 token 已足够 LLM 知道"这是什么"，详细信息靠
  Phase 2 LLM 自行总结。"""

PROACTIVE_EXTERNAL_TOTAL_MAX_TOKENS = 1500
"""Phase 1 外部候选拼合后的总 token 上限（Phase 2 实际只看 top-1）。
- 用途：所有 selected web items 序列化后，再做一次总和截断。
- 上游：cap 后的 external_section 文本。
- 设计依据：跟 PROACTIVE_PHASE1_TOTAL_TOPICS 同步下调。原值 2000 是
  20 候选 × 200 token 留的硬顶；候选数收到 12 之后，1500 已留出
  ~250 token 富余，超出仍兜底截断。Phase 2 generate prompt 实际只
  把 Phase 1 选中的单条 web_topic（~50-100 token）放进
  external_section，本字段约束的是 Phase 1 的 prompt 大小。"""

PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS = 300
"""Phase 2 流式输出的 abort fence。
- 用途：流式生成超过此值则 abort（防止 LLM 跑飞写小作文）。
- 上游：LLM 输出（不是输入）。"""

PROACTIVE_PHASE2_GENERATE_MAX_TOKENS = int(PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS * 1.5)
"""Phase 2 主流式生成的 SDK 端 max_completion_tokens。
- 用途：_make_llm 默认值，由 Phase 2 stream 主调用使用。
- 设计依据：应用层在 [main_routers/system_router.py] 流式中段
  `count_tokens(full_text + chunk) > PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS`
  硬 abort，所以 SDK 端再大也用不上。设成 abort fence × 1.5 留 50%
  bandwidth 给 token 计数误差和 prompt-cache flush 边界。"""

PROACTIVE_PHASE1_UNIFIED_MAX_TOKENS = 1024
"""Phase 1 unified 筛选 LLM 的 max_completion_tokens。
- 用途：_llm_call_with_retry 默认值，由 Phase 1 unified prompt 使用
  （web 筛选 + music 关键词 + meme 关键词单次合并调用）。
- 上游：LLM 输出 JSON（话题 ID 列表 + 简短理由）。"""

PROACTIVE_CHAT_HISTORY_MAX = 10
"""_proactive_chat_history deque maxlen。
- 用途：每个 lanlan 维护的最近主动搭话记录，用于 1h 内去重。
- 上游：proactive 触发的搭话事件。"""
