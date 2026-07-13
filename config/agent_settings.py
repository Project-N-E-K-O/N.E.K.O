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

"""Agent task, callback, plugin selection, and external-tool limits."""

# ---- Agent: task results / history / plugin pipeline ----
AGENT_HISTORY_TURNS = 10
"""task_executor messages[-N:] 历史窗口。
- 用途：_extract_context_for_user_intent / _resolve_openclaw_sender_id
  等多个站点统一从最近 N 条消息里抽取 user 意图。
- 上游：core.py 维护的 conversation_history。"""

TASK_DETAIL_MAX_TOKENS = 200
"""任务详情字段（detail / desc）回流给 LLM 的 token 上限。
- 用途：agent_server._sanitize / result_parser._truncate / brain/
  task_executor 等多处 detail 字段统一档位。
- 上游：plugin 返回值 / ComputerUse 子任务结果 / OpenFang 输出。"""

TASK_SUMMARY_MAX_TOKENS = 400
"""任务摘要字段（summary）回流给 LLM 的 token 上限。
- 用途：_emit_task_result 的 summary 档位（比 detail 长）。
- 上游：result_parser 生成的自然语言摘要。"""

TASK_LARGE_DETAIL_MAX_TOKENS = 1000
"""任务大详情字段回流给前端 HUD 的 token 上限。
- 用途：_emit_task_result 的 detail 字段；前端展示用，不直接进 LLM。
- 上游：plugin 完整结构化输出。"""

TASK_ERROR_MAX_TOKENS = 350
"""任务错误消息字段的 token 上限。
- 用途：_emit_task_result 的 error 档位。
- 上游：异常 stack / API 错误响应。"""

AGENT_CALLBACK_TEXT_MAX_TOKENS = 1000
"""单条 agent callback 的 summary/detail 注入 LLM 的 token 上限（per-item）。
- 用途：`Core.enqueue_agent_callback` 落队前对每条 callback 的 summary/detail
  截断。task_result 类回流已在 _emit_task_result 用 TASK_*_MAX_TOKENS 截过
  （≤1000），本档对齐 TASK_LARGE_DETAIL 不会误伤；真正的兜底对象是
  **push_message / proactive_message** 这条 plugin 事件流——proactive_bridge
  直接聚合 text parts 写进 summary/detail，此前没有任何 cap。
- 上游：plugin SDK push_message() 的 text parts / 外部通知。"""

AGENT_CALLBACK_TOTAL_MAX_TOKENS = 3000
"""一次注入 LLM 的 agent callback 指令总和 token 上限（total）。
- 用途：`_build_callback_instruction`（文本轮 system_prefix / proactive 触发）
  和 `_render_pending_extra_replies_by_origin`（语音 hot-swap final_prime_text）
  渲染完成后对整段做兜底截断。防 N 条 callback 累加撑爆本轮 prompt。
- 与 per-item 配合：单条 cap 防长贴，total cap 防大量短条累加（见
  docs/design/llm-prompt-budget.md §2.1 三层防护）。"""

AGENT_CALLBACK_QUEUE_MAX_ITEMS = 50
"""pending_agent_callbacks / pending_extra_replies 队列长度上限（flood guard）。
- 用途：`enqueue_agent_callback` 落队后裁到最近 N 条，防 plugin 事件流灌爆
  内存（队列此前无容量上限，drain 时全量 snapshot）。丢最旧的（最新事件
  最相关）。
- 与 AGENT_TASK_TRACKER_MAX_RECORDS=50 同口径。"""

AGENT_DEDUP_CANDIDATES_MAX = 50
"""task deduper 单次比对的 existing-task 候选条数上限。
- 用途：`brain/deduper.py:_build_prompt` 只取前 N 条 candidate 拼 prompt，
  防 backlog/flood 下 `_collect_existing_task_descriptions` 把上百条任务全量
  塞进 dedup prompt。配合 per-item 头尾截断（TASK_DETAIL_MAX_TOKENS）给输入
  一个真实总上限。
- 与 FACT_DEDUP_BATCH_LIMIT=20 类似（LLM 配对决策的舒适 batch）；dedup 只做
  一次 N×1 比对，放宽到 50。"""

# ---- Agent: defensive char-caps (NOT token caps) ----
# 下面这些是"防御性 char-cap"——在异常文本 / cancel reason / plugin reply
# 流入下游字段（summary / detail / error_message / tracker.detail / 前端
# notification）之前的硬截。
#
# 为什么是 char 而不是 token：
# - LLM-facing 字段（summary / detail / error_message / tracker.detail）
#   真正的 prompt budget 在 _emit_task_result 内部用 TASK_*_MAX_TOKENS
#   二次截断；外层 char-cap 只是为了避免把 MB 级原始字符串直接喂给
#   tiktoken（编码本身就很慢）。
# - 前端 agent_notification 字段是 toast / 错误面板展示，不进 LLM；
#   token 精度无业务意义。
#
# 常量值分组（按"是否进 LLM 上下文"切）：
#   进上下文（防御性 char-cap，下游再走 token-cap）：
#     - EXCEPTION_TEXT_MAX_CHARS         = 500  → summary 字段、_exc_text
#                                                / cancel_msg 等共享变量
#     - ERROR_MESSAGE_MAX_CHARS          = 300  → error_message 字段直接 cap
#     - TASK_TRACKER_DETAIL_MAX_CHARS    = 300  → tracker.record_completed
#                                                .detail 字段（inject 时进
#                                                LLM 的 system 消息）
#     - TASK_TRACKER_INJECT_DETAIL_MAX_CHARS = 300 → tracker.inject 渲染
#                                                detail 写进 LLM prompt
#                                                的最终一次 char-cap
#   不进上下文（前端展示）：
#     - USER_NOTIFICATION_REASON_MAX_CHARS = 200  → agent_notification.text
#     - USER_NOTIFICATION_ERROR_MAX_CHARS  = 500  → agent_notification
#                                                  .error_message

EXCEPTION_TEXT_MAX_CHARS = 500
"""LLM-facing summary 字段 / 共享异常变量的防御性 char-cap。
- 用途：
  1. summary=reply[:N] / summary=_exc_text 等直接对 summary 字段的 char-cap。
  2. cancel_msg = str(e)[:N] / _exc_text = str(e)[:N] 这类"一份截断给
     summary/detail/error_message 三个字段共用"的局部变量。
- 为什么是 char：tracebacks / API 错误体可能高达 MB，先 char-cap 再让
  _emit_task_result 内部用 TASK_SUMMARY_MAX_TOKENS / TASK_LARGE_DETAIL_
  MAX_TOKENS / TASK_ERROR_MAX_TOKENS 做精确 token 截，省去对整个原始
  字符串做 tiktoken 编码的开销。
- 与 ERROR_MESSAGE_MAX_CHARS 的关系：单纯 error_message 字段直接 char-cap
  统一走 300（更紧）；本常量是变量级 / summary 级，500 给 summary 留点
  余量；当 cancel_msg / _exc_text 这类已经 500 的变量再赋给 error_message
  时，沿用变量截断结果，不再做二次截。"""

ERROR_MESSAGE_MAX_CHARS = 300
"""LLM-facing error_message 字段直接 char-cap。
- 用途：error_message=str(e)[:N] / error_message=str(nk_result.get("error"))[:N]
  这类直接对 error_message 字段的 char-cap（没有走中间共享变量的那种）。
- 为什么是 char：和 EXCEPTION_TEXT_MAX_CHARS 同样是给下游 _emit_task_result
  内部 TASK_ERROR_MAX_TOKENS（350 token）做防御性预处理。
- 为什么和 EXCEPTION_TEXT_MAX_CHARS 数值不同：error_message 字段下游 token
  budget 比 summary 紧（350 vs 400），300 char 能避免给 token-cap 留无效
  空间，同时与 TASK_TRACKER_*_MAX_CHARS 对齐。"""

TASK_TRACKER_DETAIL_MAX_CHARS = 300
"""AgentTaskTracker.record_completed 的 detail 字段 char-cap。
- 用途：失败 / 取消路径上 detail=str(e)[:N] / detail=cancel_msg[:N] /
  detail=reply[:N] 等给 tracker 的 detail 字段做硬截。
- 为什么是 char：tracker.detail 看似只进内存日志，但 AgentTaskTracker.
  inject() 会把整段记录拼成 system 消息塞进 task_executor 的下次决策
  messages（agent_server.py 中的 _task_tracker.inject(messages, lanlan)），
  所以这条字段实际上会进 LLM 上下文。三层防御链路：
    1. 入站 char-cap = 本常量（300）
    2. record_completed 内部 _tt(detail, TASK_DETAIL_MAX_TOKENS)（200 token）
    3. inject 渲染时再 char-cap = TASK_TRACKER_INJECT_DETAIL_MAX_CHARS（300）
- 注意：成功路径上 OpenFang 已用 _tt(_track_detail, TASK_DETAIL_MAX_TOKENS)
  走 token-cap，那条路径不在本常量管辖范围。"""

TASK_TRACKER_INJECT_DETAIL_MAX_CHARS = 300
"""AgentTaskTracker.inject 渲染 detail 进 LLM system 消息时的最终 char-cap。
- 用途：agent_server.AgentTaskTracker.inject 内部 _sanitize(detail, N) 在把
  每条 record 的 detail 拼进 [AGENT TASK TRACKING …] system 消息前做的
  最后一次 char-cap。
- 为什么是 char：进 LLM prompt 前的硬上限——已经被入站 char-cap +
  record_completed 内 token-cap 处理过；这里再 char-cap 是渲染时为了让
  单行长度可控。"""

USER_NOTIFICATION_REASON_MAX_CHARS = 200
"""agent_notification.text 内嵌 reason 片段的 char-cap。
- 用途：DirectTaskExecutor 评估失败时把 reason 拼进面向前端 toast 的
  text 字段（"⚠️ Agent评估失败: {reason[:N]}"）。
- 为什么是 char：toast 容量小、不进 LLM。"""

USER_NOTIFICATION_ERROR_MAX_CHARS = 500
"""agent_notification.error_message 字段 char-cap（前端展示，不进 LLM）。
- 用途：main_server EventBus 在转发 agent_notification 给前端 WS 时对
  error_message 做的硬截；agent_server 评估失败 / 后台异常时也按此
  cap reason / str(e) 写进 agent_notification.error_message。
- 为什么是 char：纯前端展示字段，不进 LLM；和 USER_NOTIFICATION_REASON_
  MAX_CHARS 数值不同（错误详情比 toast 文本宽容）。
- 注意：本常量服务的是"前端 agent_notification 通道"的 error_message，
  和 LLM-facing 的 ERROR_MESSAGE_MAX_CHARS（300）不是一回事——前者直
  接灌 WS 帧给浏览器，后者是 _emit_task_result 字段经 callback 进
  LLM prompt。"""

AGENT_TASK_TRACKER_MAX_RECORDS = 50
"""AgentTaskTracker 最多保留的任务执行记录数。
- 用途：deque-like 结构 maxlen，供 analyzer 去重 / 上下文交错排序。
- 上游：分发出去的 agent 任务数。"""

AGENT_RECENT_CTX_PER_ITEM_TOKENS = 400
"""task_executor _sanitize_recent_context 单条上限。
- 用途：从 conversation 抽取最近 user/assistant 消息，每条进 prompt
  前先 truncate 到此值。
- 上游：会话流水。"""

AGENT_RECENT_CTX_TOTAL_TOKENS = 1000
"""task_executor _sanitize_recent_context 总和上限。
- 用途：累计 token 超过此值停止收集后续消息（partial last item dropped）。
- 上游：cap 后的 4 条 messages 序列化。"""

AGENT_PLUGIN_DESC_BM25_THRESHOLD = 3000
"""plugins_desc 触发 stage1 BM25 + LLM coarse-screen 并行的 token 阈值。
- 用途：≤ 此值直接 stage2；> 此值跑两阶段筛选。
- 上游：所有可用 plugin 的 description 拼合。"""

AGENT_PLUGIN_SHORTDESC_MAX_TOKENS = 150
"""插件短描述（生成阶段）的 max_completion_tokens。
- 用途：_ensure_short_descriptions LLM 生成 short_description 输出的上限。
- 上游：LLM 输出（不是输入）。"""

AGENT_PLUGIN_COARSE_MAX_TOKENS = 300
"""插件粗筛 stage1 LLM 的 max_completion_tokens。
- 用途：返回选中的 plugin id 列表。
- 上游：LLM 输出。"""

AGENT_UNIFIED_ASSESS_MAX_TOKENS = 600
"""Unified channel assessment 的 max_completion_tokens。
- 用途：判断走哪条执行通道（QwenPaw / OpenFang / BrowserUse / ComputerUse）。
- 上游：LLM 输出。"""

AGENT_PLUGIN_FULL_MAX_TOKENS = 500
"""插件完整评估 stage2 LLM 的 max_completion_tokens。
- 用途：返回 plugin_id + plugin_args + reason。
- 上游：LLM 输出。"""

AGENT_EXTERNAL_GATE_ENABLED = True
"""廉价前置闸总开关（默认开）。
- 用途：开 = 用 master-emotion 在 input-time 顺带产出的 external_intent，在 agent
  侧 turn_end 评估前做一道零成本前置判断：若这一轮被自信地读成「不需要外部能力」
  （既没要求对外操作、也不需要外部/实时信息），且零 LLM 的确定性 shortcut（magic
  word 规则 + 插件关键词）也全静默，就跳过那 1~2 次大模型评估，省掉闲聊轮的
  analyzer 开销。关掉则每个 turn 照常全量评估。
- 闸是非对称的：external_intent 缺失（None）或确定性命中都不刹车，所以最坏只是多花
  一次评估，绝不漏真任务。
- 上游：DirectTaskExecutor._analyze_and_execute_inner 的前置判定。"""

AGENT_EXTERNAL_GATE_THRESHOLD = 0.2
"""external_intent 刹车阈值（0~1）。
- 用途：external_intent < 此值才视为「自信地不需要外部能力」、进入刹车候选；>= 此值
  或为 None 一律放行。
- 取保守低位（默认 0.2）：小模型只需可靠认出「显然只是闲聊、靠对话和常识就能答」
  这 90% 的易判 case，模棱两可的全 fail-open 到准确的大评估。调高 = 更激进省钱但
  漏判风险上升。"""

# ── 主动搭话触发 agent（降临层，默认开）─────────────────────────────────
# Agent 总开关开启时，主动搭话（猫娘自发开口）也能跑一次 analyzer，让她自己起意用
# 工具/查信息（如「我帮你查下天气」），但严格按「每会话上限」节流，绝不频发。
# Agent 总开关关闭时，analyze_request 会在进入主动路径前被硬拦截，不分析也不
# 派单。
AGENT_PROACTIVE_ANALYZE_ENABLED = True
"""主动搭话触发 agent 的总开关（默认开）。
- 关 = 主动搭话从不跑 analyzer，只有新 user 轮才分析。
- 开（默认）= 主动搭话轮也带 proactive 标过河，agent_server 走独立路径：assistant 台词
  指纹去重 + 每会话计数上限（AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION）双重节流，
  通过才跑一次 analyzer、把猫娘的主动台词当意图评估。
- Agent 总开关是更高优先级的硬闸；用户未开启 Agent 时不会分析或派单。
- 上游：cross_server 在 had_user_input=False 的 turn_end 打 proactive 标；
  agent_server 的 analyze handler 分叉。"""

AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION = 2
"""每个会话内主动搭话最多触发几次 analyzer（默认 2）。
- 计的是「主动轮 analyzer 跑的次数」（含未派出工具的），所以同时是成本上界 ——
  一个 session 最多 N 次主动 analyzer 调用，防频发/防廉价层污染。
- 计数在 greeting_check（新会话起点）重置；end_all 清空。
- 调大 = 主动能力更明显但成本/打扰风险上升；0 = 等价于关。"""

PLUGIN_INPUT_DESC_MAX_TOKENS = 1000
"""_ensure_short_descriptions 输入的 plugin manifest description 上限。
- 用途：生成 short_description 时把原始 description 截断后再送入 prompt
  （防止恶意/超大 plugin 喂超长 manifest）。
- 上游：plugin 注册时的 manifest description 字段。"""

# ---- Agent: ComputerUse / OpenClaw ----
COMPUTER_USE_MAX_TOKENS = 6000
"""ComputerUse 主调用的 max_completion_tokens。
- 用途：VLM 生成 thought + action + code 的输出上限。
- 上游：LLM 输出。"""

LLM_PING_MAX_TOKENS = 5
"""LLM 健康检查的 max_completion_tokens。
- 用途：连通性 ping 仅返回 "ok" 即可。
- 上游：LLM 输出。"""

OPENCLAW_MAGIC_INTENT_MAX_TOKENS = 80
"""OpenClaw magic intent 分类的 max_completion_tokens。
- 用途：判断用户输入是 /clear /new /stop /daemon-approve 中的哪个。
- 上游：LLM 输出固定 JSON ~15 token，80 留 5x 安全垫。"""
