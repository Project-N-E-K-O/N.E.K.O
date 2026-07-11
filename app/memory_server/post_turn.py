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

"""Per-turn signals: the outbox op registration + the post-turn background
task (signal-loop counter bump, repetition sniffing, check_feedback and the
OFF-mode Stage-1 fallback). Registers the ``OP_POST_TURN_SIGNALS`` outbox
handler at import time.
"""

import asyncio

from config import (
    IGNORED_REINFORCEMENT_DELTA,
    USER_CONFIRM_DELTA,
    USER_REBUT_DELTA,
)
from memory.event_log import (
    EVIDENCE_SOURCE_USER_CONFIRM,
    EVIDENCE_SOURCE_USER_IGNORE,
    EVIDENCE_SOURCE_USER_REBUT,
)
from memory.outbox import OP_POST_TURN_SIGNALS

from . import gates, outbox_infra, runtime, signal_extraction
from ._shared import logger
from .rows import _extract_ai_response, _extract_user_messages


async def _spawn_outbox_post_turn_signals(lanlan_name: str, messages: list) -> asyncio.Task:
    """Register the per-turn signals background task in the outbox and spawn it.

    "per-turn signals" = counter bump (for the batch loop's counting) + repetition
    sniffing + check_feedback + OFF-mode Stage-1 fallback; see
    ``_run_post_turn_signals``. The registered payload contains the whole turn's
    conversation serialized via messages_to_dict, replayable at restart.
    """
    from utils.llm_client import messages_to_dict

    payload = {'messages': messages_to_dict(messages)}
    try:
        op_id = await runtime.outbox.aappend_pending(lanlan_name, OP_POST_TURN_SIGNALS, payload)
    except Exception as e:
        # Outbox 写失败不能阻塞主流程，降级为一次性任务（与重构前行为一致）
        logger.warning(
            f"[Outbox] {lanlan_name}: append_pending 失败，降级为内存任务: "
            f"{type(e).__name__}: {e}"
        )
        return runtime._spawn_background_task(
            _run_post_turn_signals(messages, lanlan_name)
        )
    op = {'op_id': op_id, 'type': OP_POST_TURN_SIGNALS, 'payload': payload}
    return runtime._spawn_background_task(outbox_infra._run_outbox_op(lanlan_name, op))


async def _run_post_turn_signals(messages: list, lanlan_name: str):
    """Background async: per-turn signals at every turn end. Failures are skipped silently.

    Responsibilities (in step order):
      0. counter bump — +1 to ``_periodic_signal_extraction_loop``'s turn counter,
         so the batch loop triggers Stage-1+Stage-2 at 10 accumulated turns
      1. OFF-mode Stage-1 fallback — when powerful_memory is off the batch loop is
         fully stopped, and per-turn ``fact_store.extract_facts`` is the only
         fallback for fact extraction (not run in ON-mode; left to the batch loop)
      2. repetition sniffing — local BM25, §2.6 5h-window suppress
      3. check_feedback — detects user feedback on surfaced reflections (LLM runs
         only when surfaced has pending entries) + NEGATIVE_KEYWORDS hits trigger
         the LLM target check

    Naming history — this function was introduced in PR-1 (RFC #928) as
    ``_extract_facts_and_check_feedback``, when step 1 still unconditionally ran
    ``fact_store.extract_facts`` (Stage-1) every turn. RFC §3.4.3 verbatim: "do
    **not** run extract_facts every turn on the conversation hot path — too
    expensive. Move to background scheduling" — PR #1346 split ON-mode Stage-1 out
    into ``_periodic_signal_extraction_loop``, step 1 degraded to the OFF-mode
    fallback, and this follow-up renamed the symbols (including the outbox spawn
    helper / handler / op constant) to ``post_turn_signals`` to match the actual
    semantics. The **string value** of ``OP_POST_TURN_SIGNALS`` remains
    ``"extract_facts"`` (the outbox.ndjson wire format is immutable).
    """
    user_msgs = _extract_user_messages(messages)

    # 本轮算入 signal-extraction 触发计数器（RFC §3.4.3）—— batch loop
    # 靠这个 counter 在累积 N 轮时触发 _signal_check_one。
    # 只在 user 有发声时 bump，**故意不**算 AI-only / proactive turn：
    # path A 抽的是 user_observation fact，没 user 发声就抽不出料；
    # path B 是 piggyback A 的 trigger 跑（不独立调度），也跟着只在 user
    # 有 engagement 的窗口里跑。这是 product thesis 的"90% 没心没肺"——
    # AI 自言自语 + user 不搭理的内容是廉价层，不该自动当 fact 沉淀污染
    # memory；只有 user 印证过的才升级到神明降临层。
    try:
        if user_msgs:
            signal_extraction._signal_check_record_turn(lanlan_name)
    except Exception as e:
        # Best-effort counter bump; a failure here only delays the next
        # signal-extraction cycle — not worth interrupting conversation flow.
        logger.debug(f"[MemoryServer] signal-check turn counter 更新失败: {e}")

    # 强力记忆开关——本轮 evidence-related 路径的 gate（promote/negative-keyword/
    # corrections）。check_feedback 自身仍跑（主动搭话回应是核心 channel）。
    powerful_enabled = await gates._ais_powerful_memory_enabled()

    # Step 1 — per-turn Stage-1 fact extraction：只在 powerful_memory **关闭**
    # 时跑（OFF-mode baseline fallback）。ON-mode 下 fact extraction 完全交给
    # ``_periodic_signal_extraction_loop`` 跑 batch Stage-1+Stage-2（RFC §3.4.3
    # 设计意图："不在对话主路径上每轮运行 extract_facts——太贵。改为背景调度"，
    # batch 路径带上下文、质量更高、cost 更低）。
    #
    # OFF-mode 下 batch loop 整段停（见 _periodic_signal_extraction_loop 的
    # `if not powerful_enabled: continue` 分支），如果这里也跳过，facts.json
    # 就完全无路径更新——这是 chatgpt-codex-connector PR #1346 抓到的 regression。
    # OFF-mode 保留 legacy per-turn Stage-1，let user 仍能拿到基础 fact 累积。
    if not powerful_enabled:
        try:
            await runtime.fact_store.extract_facts(messages, lanlan_name)
        except Exception as e:
            logger.warning(f"[MemoryServer] OFF-mode 事实提取失败: {e}")

    try:
        # 2. 全局复读嗅探：扫描 AI 回复中是否重复提及 persona 条目 +
        #    confirmed reflection（§2.6 5h 窗口 suppress 机制，两者正交）。
        #    本地 BM25，无 LLM 调用，per-turn 跑是必要的——5h 窗口逻辑
        #    依赖即时更新。
        ai_response = _extract_ai_response(messages)
        if ai_response:
            await runtime.persona_manager.arecord_mentions(lanlan_name, ai_response)
            await runtime.reflection_engine.arecord_mentions(lanlan_name, ai_response)
    except Exception as e:
        logger.warning(f"[MemoryServer] 复读嗅探失败: {e}")

    try:
        # 3. 检查用户对之前 surfaced 反思的反馈 + 派 evidence 信号
        surfaced = await runtime.reflection_engine.aload_surfaced(lanlan_name)
        pending_surfaced = [s for s in surfaced if s.get('feedback') is None]
        if pending_surfaced and user_msgs:
            feedbacks = await runtime.reflection_engine.check_feedback(lanlan_name, user_msgs)
            if feedbacks is not None:
                # Build id→feedback map for quick lookup
                fb_map: dict[str, str] = {}
                for fb in feedbacks:
                    if not isinstance(fb, dict):
                        continue
                    rid = fb.get('reflection_id')
                    kind = fb.get('feedback')
                    if rid and kind in ('confirmed', 'denied', 'ignored'):
                        fb_map[rid] = kind

                # RFC §3.1.5: confirmed → reinforcement += 1; denied →
                # disputation += 1; ignored → reinforcement += -0.2.
                # pending→confirmed/denied state transitions happen in the
                # score-driven auto_promote_stale path (not here).
                #
                # Retry semantics caveat: `check_feedback` above already
                # persisted the feedback decision into `surfaced.json`, so
                # a downstream aapply_signal / areject_promotion failure
                # here won't be re-tried next cycle (surfaced.feedback !=
                # None skips the row). PR-1 accepts best-effort with WARN
                # logs; a follow-up would move these side-effects behind an
                # outbox op so they survive transient failures. Tracked for
                # PR-2+ decay/archive work.
                for rid, kind in fb_map.items():
                    if kind == 'confirmed':
                        delta = {'reinforcement': USER_CONFIRM_DELTA}
                        source = EVIDENCE_SOURCE_USER_CONFIRM
                    elif kind == 'denied':
                        delta = {'disputation': USER_REBUT_DELTA}
                        source = EVIDENCE_SOURCE_USER_REBUT
                    else:  # ignored
                        delta = {'reinforcement': IGNORED_REINFORCEMENT_DELTA}
                        source = EVIDENCE_SOURCE_USER_IGNORE
                    try:
                        await runtime.reflection_engine.aapply_signal(
                            lanlan_name, rid, delta, source=source,
                        )
                    except Exception as e:
                        # Signal lost this turn (see caveat above). Warn so
                        # operators can spot transient LLM / disk issues.
                        logger.warning(
                            f"[MemoryServer] {lanlan_name}: aapply_signal "
                            f"({rid}, {kind}) 失败，此次反馈 signal 已丢失: {e}"
                        )

                # denied 仍然走 areject_promotion 做 status transition（保留
                # 既有 surfaced 登记 + reflection status='denied' 行为）
                for rid, kind in fb_map.items():
                    if kind == 'denied':
                        try:
                            await runtime.reflection_engine.areject_promotion(lanlan_name, rid)
                        except Exception as e:
                            logger.warning(
                                f"[MemoryServer] areject_promotion 失败 "
                                f"{rid}，此次 denial 未转入 status: {e}"
                            )

                # 让后续扫描把 pending→confirmed 推进。强力记忆决定走哪条：
                #   开 → score-driven + merge LLM
                #   关 → time-driven (14 天 confirm + 14 天 promote, 零 LLM)
                try:
                    if powerful_enabled:
                        await runtime.reflection_engine.aauto_promote_stale(lanlan_name)
                    else:
                        await runtime.reflection_engine.aauto_promote_time_driven(lanlan_name)
                except Exception as e:
                    logger.debug(
                        f"[MemoryServer] {lanlan_name}: auto_promote 失败: {e}"
                    )
    except Exception as e:
        logger.warning(f"[MemoryServer] 反馈检查失败: {e}")

    if powerful_enabled:
        try:
            # 3.5 负面关键词 hook（§3.4.5）——命中就派个异步小 LLM 任务
            # 强力记忆关 → 整段不跑（这是 evidence-RFC 引入的额外 LLM 路径）
            if user_msgs:
                from utils.language_utils import get_global_language
                runtime._spawn_background_task(
                    signal_extraction._amaybe_trigger_negative_keyword_hook(
                        lanlan_name, user_msgs, get_global_language(),
                    )
                )
        except Exception as e:
            logger.debug(f"[MemoryServer] 负面关键词 hook 派发失败: {e}")

        try:
            # 4. 审视矛盾队列（如果有 pending corrections）
            # 强力记忆关 → 不跑 LLM 批量审视（corrections queue 累积，等重开消化）
            resolved = await runtime.persona_manager.resolve_corrections(lanlan_name)
            if resolved:
                logger.info(f"[MemoryServer] {lanlan_name}: 审视了 {resolved} 条 persona 矛盾")
        except Exception as e:
            logger.warning(f"[MemoryServer] 矛盾审视失败: {e}")


async def _outbox_post_turn_signals_handler(lanlan_name: str, payload: dict) -> None:
    """Outbox handler for OP_POST_TURN_SIGNALS: restore messages from the payload and run
    ``_run_post_turn_signals``.

    Sources of idempotency:
      - fact_store.extract_facts (OFF-mode fallback) dedups facts internally via
        SHA-256; repeated extraction produces no duplicate facts.
      - arecord_mentions is a monotonically accumulating counter; replay slightly
        inflates mention counts (acceptable at-least-once semantics).
      - check_feedback naturally catches up next time — a reflection's
        surfaced/feedback lists are persisted.
      - resolve_corrections protects idempotency internally via processed_indices.
    """
    from utils.llm_client import messages_from_dict

    raw = payload.get('messages') or []
    if not raw:
        return
    messages = messages_from_dict(raw)
    if not messages:
        return
    await _run_post_turn_signals(messages, lanlan_name)


outbox_infra.register_outbox_handler(OP_POST_TURN_SIGNALS, _outbox_post_turn_signals_handler)

