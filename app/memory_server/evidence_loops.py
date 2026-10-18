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

"""Evidence-pipeline background loops (memory-evidence RFC).

Rebuttal check, auto-promote, idle maintenance, the one-shot migrations
(evidence-field seeding + archive sharding), the slow schema v1→v2 recheck
loop and the periodic archive sweep. Component singletons are read through
``runtime.<attr>`` at call time so component reloads and test monkeypatches
are picked up; cross-loop gating and scheduling constants live in ``gates``.
"""

import asyncio
from datetime import datetime, timedelta

from config import (
    EVIDENCE_ARCHIVE_DAYS,
    EVIDENCE_ARCHIVE_SWEEP_INTERVAL_SECONDS,
    MEMORY_RECHECK_ENABLED,
    MEMORY_RECHECK_INITIAL_DELAY_SECONDS,
    MEMORY_RECHECK_INTERVAL_SECONDS,
)
from memory.cursors import CURSOR_REBUTTAL_CHECKED_UNTIL
from memory.event_log import EVIDENCE_SOURCE_MIGRATION_SEED
from memory.message_sources import is_theater_memory_message

from . import gates, review, runtime
from .locale_state import (
    aget_subject_prompt_locale,
    run_with_character_prompt_locale,
)
from ._shared import logger
from .gates import (
    IDLE_CHECK_INTERVAL,
    _INITIAL_DELAY_ARCHIVE,
    _INITIAL_DELAY_AUTO_PROMOTE,
    _INITIAL_DELAY_IDLE_MAINT,
    _INITIAL_DELAY_REBUTTAL,
)
from .rows import _coerce_db_ts, _extract_user_messages_with_ts_from_rows


REBUTTAL_CHECK_INTERVAL = 180  # Three minutes.
REBUTTAL_FIRST_RUN_LOOKBACK_HOURS = 1  # Fallback lookback window for first startup / clock rollback.
# Drain pattern: handle at most N user messages at once to bound prompts for frequent users.
# Leave the rest for the next round (advance cursor to the Nth timestamp without losing messages).
REBUTTAL_DRAIN_BATCH_LIMIT = 20
# Hard SQL read limit bounds memory so the 1h fallback cannot fetch the entire table.
# 200 rows usually contain 50-100 user messages, enough for several drains.
REBUTTAL_SQL_ROW_LIMIT = 200


async def _run_with_character_language(name: str, operation):
    """Run one async maintenance operation with the latest session locale."""
    return await run_with_character_prompt_locale(name, operation, name)


async def _get_batch_subject_prompt_locale(name: str, subject):
    """Return a scoped locale override; legacy batches keep character context."""
    if subject is None:
        return None
    return await aget_subject_prompt_locale(name, subject)


async def _resolve_fact_dedup_with_language(name: str):
    async def operation(character_name: str):
        return await runtime.fact_dedup_resolver.aresolve(
            character_name,
            prompt_locale_resolver=lambda subject: (
                _get_batch_subject_prompt_locale(character_name, subject)
            ),
        )

    return await _run_with_character_language(name, operation)


async def _resolve_persona_corrections_with_language(name: str):
    async def operation(character_name: str):
        return await runtime.persona_manager.resolve_corrections(
            character_name,
            prompt_locale_resolver=lambda subject: (
                _get_batch_subject_prompt_locale(character_name, subject)
            ),
        )

    return await _run_with_character_language(name, operation)


async def _auto_promote_character(name: str, powerful: bool):
    operation = (
        runtime.reflection_engine.aauto_promote_stale
        if powerful
        else runtime.reflection_engine.aauto_promote_time_driven
    )
    return await _run_with_character_language(name, operation)


async def _compress_recent_history(name: str):
    return await run_with_character_prompt_locale(
        name,
        runtime.recent_history_manager.update_history,
        [],
        name,
        detailed=True,
        on_compress_done=review._on_compress_done,
    )


async def _spawn_review_with_character_language(name: str):
    return await _run_with_character_language(name, review.maybe_spawn_review)


async def _check_feedback_for_confirmed(
    name: str,
    confirmed: list[dict],
    user_msgs: list[str],
):
    """Run periodic rebuttal analysis with the durable character locale."""
    return await run_with_character_prompt_locale(
        name,
        runtime.reflection_engine.check_feedback_for_confirmed,
        name,
        confirmed,
        user_msgs,
    )


async def _resolve_rebuttal_start_time(name: str, now: datetime):
    """Decide the starting time for this round's rebuttal_loop query.

    Priority:
      1. persisted CURSOR_REBUTTAL_CHECKED_UNTIL
      2. fallback look-back window (first launch / cursor file missing)
      3. clock-rollback protection: cursor > now is treated as dirty data; use the
         fallback and **immediately rewrite** the cursor

    Why the rollback branch overwrites the cursor immediately: if only the main
    loop's success branch overwrote it, then under persistent LLM failures + a
    clock rollback, every loop iteration would hit the fallback and warn, but the
    cursor would stay stuck at a future time, never self-healing; writing the
    fallback back here breaks that infinite loop.

    Why write fallback rather than now: if we wrote now and this tick's LLM call
    failed, messages in the window `[fallback, now]` would be skipped because the
    next round's cursor has already advanced to now; writing fallback preserves
    retry semantics — the main loop's success branch then advances the cursor to
    now.

    Standalone function for easy unit testing.
    """
    cursor = await runtime.cursor_store.aget_cursor(name, CURSOR_REBUTTAL_CHECKED_UNTIL)
    fallback = now - timedelta(hours=REBUTTAL_FIRST_RUN_LOOKBACK_HOURS)
    if cursor is None:
        # First startup: persist the fallback anchor. Otherwise repeated LLM failures leave next round's
        # cursor as None, causing fallback to be recalculated from the new now and move forward
        # (a sliding 1h window), permanently skipping the earliest messages in the original window.
        try:
            await runtime.cursor_store.aset_cursor(
                name, CURSOR_REBUTTAL_CHECKED_UNTIL, fallback,
            )
        except Exception as e:
            logger.debug(f"[Rebuttal] {name}: 首次 fallback 锚定写入失败（将在下轮重试）: {e}")
        return fallback
    if cursor > now:
        logger.warning(
            f"[Rebuttal] {name}: 游标 {cursor.isoformat()} 晚于当前时间 "
            f"{now.isoformat()}（时钟回拨?），回退到 {fallback.isoformat()} 并覆写"
        )
        # Self-heal by resetting the cursor to fallback (not now), avoiding rollback on later ticks
        # while preserving retries for this [fallback, now] window if the LLM fails.
        try:
            await runtime.cursor_store.aset_cursor(
                name, CURSOR_REBUTTAL_CHECKED_UNTIL, fallback,
            )
        except Exception as e:
            logger.debug(f"[Rebuttal] {name}: rollback 自愈写入失败（将在下轮重试）: {e}")
        return fallback
    return cursor


_rebuttal_failures: dict[str, dict[str, int]] = {}
"""Per-character rebuttal LLM failure counts: ``{name: {cursor_iso: count}}``.

In-memory only (the cursor persists in cursors.json, but counters reset on restart).
Why in-memory: after restarting, retry ``MEMORY_LIVENESS_MAX_ATTEMPTS`` times before dead-lettering,
preventing memory counters from permanently abandoning transient failures; the visible cost is an extra
N × REBUTTAL_CHECK_INTERVAL delay after restart, which is acceptable.

Liveness fallback rationale: when ``check_feedback_for_confirmed`` returned None, the old code simply
returned without moving the cursor -> reread the same [cursor, now] window containing the same poisonous user
message next cycle -> fail again -> permanently stall rebuttals (the poisoned window keeps user rebuttal signals out of the
evidence loop）。"""


def _rebuttal_bump_failure(name: str, cursor_key: str) -> int:
    """Bump the failure counter and return the cumulative count. The caller checks >= MEMORY_LIVENESS_MAX_ATTEMPTS itself."""
    fails = _rebuttal_failures.setdefault(name, {})
    fails[cursor_key] = int(fails.get(cursor_key, 0) or 0) + 1
    return fails[cursor_key]


def _rebuttal_clear_failures(name: str) -> None:
    """Reset the counter after a cursor advance (success) or a forced dead-letter push."""
    _rebuttal_failures.pop(name, None)


async def _periodic_rebuttal_loop():
    """Every 5 minutes, check whether confirmed reflections are rebutted by recent conversation.

    Queries all new conversation messages since the last check via time_indexed SQL,
    ensuring no unconsumed user replies are missed.

    Cursor persistence (P0 fix): `CURSOR_REBUTTAL_CHECKED_UNTIL` is written to
    cursors.json and read back from disk after shutdown→restart, eliminating the
    flaw where "the default 1-hour look-back loses rebuttals from the shutdown
    period".

    First run delayed by _INITIAL_DELAY_REBUTTAL seconds (staggered against the
    other background loops).
    """
    await asyncio.sleep(_INITIAL_DELAY_REBUTTAL)
    while True:
        # Strong memory disabled: stop the entire rebuttal LLM path (one of the most expensive periodic
        # LLM calls introduced by evidence-RFC, draining with thinking every 180s). User rebuttals
        # can still enter evidence through per-turn check_feedback (proactive-chat responses).
        #
        # While disabled, advance cursor to now: otherwise _resolve_rebuttal_start_time gets the old
        # cursor on re-enable, and the next round processes every user message accumulated
        # while disabled (huge prompts + many LLM calls). Disabled should mean
        # processed as a noop: accumulate anew from now on re-enable, without backfilling.
        if not await gates._ais_powerful_memory_enabled():
            try:
                character_data = await runtime._config_manager.aload_characters()
                catgirl_names = list(character_data.get('猫娘', {}).keys())
                cursor_now = datetime.now()
                for name in catgirl_names:
                    try:
                        await runtime.cursor_store.aset_cursor(
                            name, CURSOR_REBUTTAL_CHECKED_UNTIL, cursor_now,
                        )
                    except Exception as cursor_e:
                        # A single character's cursor update failure is nonfatal: retry next round; at worst,
                        # that character rescans a longer window after re-enable. Other characters are unaffected.
                        logger.debug(
                            f"[Rebuttal] {name}: 关态 cursor 推进失败: {cursor_e}"
                        )
            except Exception as e:
                logger.debug(f"[Rebuttal] 关态 cursor 推进 batch 失败: {e}")
            await asyncio.sleep(REBUTTAL_CHECK_INTERVAL)
            continue

        try:
            character_data = await runtime._config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[Rebuttal] 加载角色列表失败: {e}")
            await asyncio.sleep(REBUTTAL_CHECK_INTERVAL)
            continue

        now = datetime.now()

        async def _check_one_rebuttal(name: str):
            """Rebuttal check for a single catgirl. Characters are mutually independent; the outer gather runs them in parallel.
            Internally, feedbacks still run areject_promotion serially (the same reflection must not be processed concurrently).

            Drain mode: each round processes at most ``REBUTTAL_DRAIN_BATCH_LIMIT`` (=20)
            user messages, advancing the cursor to the Nth message's timestamp. Under
            backpressure (high-frequency chat users or the 1h fallback) it drains over
            multiple ticks with bounded LLM prompt size per tick; no messages are lost
            (the cursor advances strictly by processed position).
            """
            try:
                confirmed = await runtime.reflection_engine.aget_confirmed_reflections(name)
                if not confirmed:
                    # Advance the cursor even without confirmed reflections: otherwise the first new confirmed
                    # reflection causes all user messages accumulated between cursor and now to reach
                    # check_feedback_for_confirmed, risking unrelated historical replies being classified as rebuttals.
                    await runtime.cursor_store.aset_cursor(
                        name, CURSOR_REBUTTAL_CHECKED_UNTIL, now,
                    )
                    _rebuttal_clear_failures(name)
                    return

                start_time = await _resolve_rebuttal_start_time(name, now)
                rows = await runtime.time_manager.aretrieve_original_by_timeframe(
                    name, start_time, now,
                    limit_rows=REBUTTAL_SQL_ROW_LIMIT,
                )
                if not rows:
                    await runtime.cursor_store.aset_cursor(
                        name, CURSOR_REBUTTAL_CHECKED_UNTIL, now,
                    )
                    _rebuttal_clear_failures(name)
                    return

                # Extract (msg, ts) tuples (ascending ts; timestamps already normalized to datetime).
                user_msgs_with_ts = _extract_user_messages_with_ts_from_rows(rows)
                if not user_msgs_with_ts:
                    # Only AI messages or no user content in the window: advance cursor to the final SQL
                    # row's timestamp (if LIMIT was reached and more rows remain) or now (fully drained).
                    last_row_ts = _coerce_db_ts(rows[-1][0])
                    if len(rows) >= REBUTTAL_SQL_ROW_LIMIT and last_row_ts is not None:
                        await runtime.cursor_store.aset_cursor(
                            name, CURSOR_REBUTTAL_CHECKED_UNTIL, last_row_ts,
                        )
                    else:
                        # LIMIT was not reached, so the entire window was scanned; advance directly to now.
                        # Also take this conservative fallback if last_row_ts parsing fails.
                        await runtime.cursor_store.aset_cursor(
                            name, CURSOR_REBUTTAL_CHECKED_UNTIL, now,
                        )
                    _rebuttal_clear_failures(name)
                    return

                # Drain the first N user messages, then extend the batch to include subsequent user
                # messages sharing the final timestamp. SQL uses
                # ``timestamp BETWEEN`` (inclusive), so after advancing cursor to batch[-1].ts,
                # the next round rereads rows with that timestamp. Without extension, multiple messages
                # sharing a timestamp can be split at the boundary, causing an endless loop where
                # only part is processed and the rest is split again at the next batch boundary
                # (``store_conversation`` assigns one timestamp per message batch, so ties are common).
                # The SQL row LIMIT bounds extension, preventing unbounded growth.
                batch = user_msgs_with_ts[:REBUTTAL_DRAIN_BATCH_LIMIT]
                if len(user_msgs_with_ts) > len(batch):
                    boundary_ts = batch[-1][1]
                    extend_idx = len(batch)
                    while (
                        extend_idx < len(user_msgs_with_ts)
                        and user_msgs_with_ts[extend_idx][1] == boundary_ts
                    ):
                        extend_idx += 1
                    if extend_idx > len(batch):
                        batch = user_msgs_with_ts[:extend_idx]
                user_msgs = [m for m, _ in batch]

                # Reuse check_feedback to detect rebuttals.
                feedbacks = await _check_feedback_for_confirmed(
                    name, confirmed, user_msgs,
                )
                if feedbacks is None:
                    # LLM call failed: keep the cursor and retry these messages next time.
                    # Liveness fallback: after repeated failures at the same cursor reach
                    # MEMORY_LIVENESS_MAX_ATTEMPTS, force cursor to now and abandon this
                    # window (dead letter) so a toxic user message cannot permanently stall the rebuttal
                    # path. The cursor is persisted to cursors.json, so restarting cannot revive a stuck cursor;
                    # it is more persistent than the in-memory signal-extraction cursor.
                    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
                    cursor_key = (
                        start_time.isoformat(timespec='microseconds')
                        if start_time else 'cold'
                    )
                    attempts = _rebuttal_bump_failure(name, cursor_key)
                    if attempts >= MEMORY_LIVENESS_MAX_ATTEMPTS:
                        logger.warning(
                            f"[Rebuttal] {name}: 反驳检查在 cursor {cursor_key!r} "
                            f"累计失败 {attempts} 次 ≥ {MEMORY_LIVENESS_MAX_ATTEMPTS}，"
                            f"强推 cursor 到 {now.isoformat(timespec='seconds')} "
                            f"放弃该窗口（dead-letter）。Why: 毒窗口 liveness 兜底。"
                        )
                        await runtime.cursor_store.aset_cursor(
                            name, CURSOR_REBUTTAL_CHECKED_UNTIL, now,
                        )
                        _rebuttal_clear_failures(name)
                    else:
                        logger.warning(
                            f"[Rebuttal] {name}: 反驳检查失败，保留游标待重试 "
                            f"({attempts}/{MEMORY_LIVENESS_MAX_ATTEMPTS})"
                        )
                    return

                # Advance and persist the cursor only on success. Drain advancement rules:
                # - Unprocessed user messages remain in this read (the batch includes all timestamp ties,
                #   so every remaining timestamp is greater than batch[-1].ts):
                #   advance to the first unprocessed user message's timestamp, the next read's
                #   inclusive BETWEEN start; this row has not been processed, so no duplicate processing occurs.
                # - SQL reached LIMIT but all user messages were processed: advance to the final row timestamp.
                #   The next read repeats the tied cluster, but LLM calls are idempotent and harmless.
                # - Fully drained: advance cursor to now.
                more_user_msgs = len(user_msgs_with_ts) > len(batch)
                hit_sql_limit = len(rows) >= REBUTTAL_SQL_ROW_LIMIT
                if more_user_msgs:
                    new_cursor = user_msgs_with_ts[len(batch)][1]
                    logger.info(
                        f"[Rebuttal] {name}: drain 处理 {len(batch)} 条，"
                        f"cursor 推进到下一未处理 user msg ts，下轮续"
                    )
                elif hit_sql_limit:
                    last_row_ts = _coerce_db_ts(rows[-1][0])
                    new_cursor = last_row_ts if last_row_ts is not None else now
                    logger.info(
                        f"[Rebuttal] {name}: drain 处理 {len(batch)} 条 user msg，"
                        f"SQL 命中 LIMIT，cursor 推进到最后一行 ts，下轮续"
                    )
                else:
                    new_cursor = now
                await runtime.cursor_store.aset_cursor(
                    name, CURSOR_REBUTTAL_CHECKED_UNTIL, new_cursor,
                )
                # Cursor advanced: its old key will never be used again; clear the counter
                # to prevent unbounded dictionary growth with cursor history (symmetric with Sites 0a/0b).
                _rebuttal_clear_failures(name)
                for fb in feedbacks:
                    if isinstance(fb, dict) and fb.get('feedback') == 'denied':
                        rid = fb.get('reflection_id')
                        if rid:
                            await runtime.reflection_engine.areject_promotion(name, rid)
                            logger.info(f"[Rebuttal] {name}: confirmed 反思被反驳: {rid}")
            except Exception as e:
                logger.debug(f"[Rebuttal] {name}: 处理失败，跳过: {e}")

        if catgirl_names:
            await asyncio.gather(
                *(_check_one_rebuttal(name) for name in catgirl_names),
                return_exceptions=True,
            )

        await asyncio.sleep(REBUTTAL_CHECK_INTERVAL)


AUTO_PROMOTE_CHECK_INTERVAL = 180  # Three minutes, matching rebuttal checks to cover the same degree of state change.

async def _periodic_auto_promote_loop():
    """Periodically run auto_promote_stale: pending→confirmed→promoted state migration.

    PR-3 (RFC §3.9.1): `aauto_promote_stale` now has two parts:
      1. in-lock pending → confirmed (score driven)
      2. out-of-lock confirmed → promoted via `_apromote_with_merge` (LLM decides
         merge / standalone promotion / rejection; throttled to prevent
         LLM-failure DOS)

    Per-character via asyncio.gather in parallel — within each character operations
    remain sequential (lock-serialized), but across characters it can saturate.

    First run delayed by _INITIAL_DELAY_AUTO_PROMOTE seconds (staggered against the
    other background loops).
    """
    await asyncio.sleep(_INITIAL_DELAY_AUTO_PROMOTE)
    while True:
        try:
            character_data = await runtime._config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[AutoPromote] 加载角色列表失败: {e}")
            await asyncio.sleep(AUTO_PROMOTE_CHECK_INTERVAL)
            continue

        powerful = await gates._ais_powerful_memory_enabled()

        async def _promote_one(name: str):
            try:
                transitions = await _auto_promote_character(name, powerful)
                if transitions:
                    logger.info(
                        f"[AutoPromote] {name}: {transitions} 条状态迁移"
                        f"({'score+merge' if powerful else 'time-driven'})"
                    )
            except Exception as e:
                logger.debug(f"[AutoPromote] {name}: 处理失败: {e}")

        if catgirl_names:
            await asyncio.gather(
                *(_promote_one(name) for name in catgirl_names),
                return_exceptions=True,
            )

        await asyncio.sleep(AUTO_PROMOTE_CHECK_INTERVAL)


async def _periodic_idle_maintenance_loop():
    """Periodically check whether the system is idle and run memory maintenance tasks when it is.

    First run delayed by _INITIAL_DELAY_IDLE_MAINT seconds (letting startup-phase
    cloudsave / outbox replay / migration tasks digest first), then polled every
    IDLE_CHECK_INTERVAL seconds.

    Each round runs, for every character in order:
    1. History compression — runs when needed (history > compress_threshold)
    1b. Fact vector dedup — runs when needed (vectors enabled and pending dedup queue non-empty)
    2. Persona contradiction review — runs when needed (pending corrections non-empty); unaffected
       by the recent_memory_auto_review switch or REVIEW_SKIP_HISTORY_LEN: persona corrections
       don't read recent history; they are an independent contradiction-resolution pipeline and
       shouldn't be blanket-disabled by the review switch.
    3. Memory tidy-up review — subject to the REVIEW_MIN_INTERVAL
       minimum interval; skipped when history < REVIEW_SKIP_HISTORY_LEN or review_enabled is off.
    """
    await asyncio.sleep(_INITIAL_DELAY_IDLE_MAINT)
    while True:
        try:
            if not gates._is_idle():
                continue

            try:
                character_data = await runtime._config_manager.aload_characters()
                catgirl_names = list(character_data.get('猫娘', {}).keys())
            except Exception as e:
                logger.debug(f"[IdleMaint] 加载角色列表失败: {e}")
                continue

            # Strong memory controls whether tasks 1b (fact_dedup) and 2 (persona corrections)
            # run. Tasks 1 (history compression) and 3 (recent.review) predate the RFC
            # and always run. Snapshot the flag once per round and reuse across characters.
            powerful_enabled = await gates._ais_powerful_memory_enabled()

            for name in catgirl_names:
                # Recheck idleness before each character; exit immediately when busy.
                if not gates._is_idle():
                    logger.debug("[IdleMaint] 检测到新活动，中断本轮维护")
                    break

                try:
                    history = await runtime.recent_history_manager.aget_recent_history(name)
                    # Preserve theater capsules unchanged during compression; exclude them from update_history's threshold.
                    ordinary_history_len = sum(
                        1
                        for message in history
                        if not is_theater_memory_message(message)
                    )

                    # -- Task 1: Compress history when needed, independent of the global switch. --
                    # Match update_history's actual trigger: `ordinary_message_count > compress_threshold`
                    # (default 20). max_history_length (default 10, retained after compression) would
                    # keep triggering IdleMaint at 11-20 messages while update_history does no compression,
                    # producing idle-work logs every IDLE_CHECK_INTERVAL.
                    if ordinary_history_len > runtime.recent_history_manager.compress_threshold:
                        logger.info(
                            f"[IdleMaint] {name}: 历史记录过长 ({ordinary_history_len} > "
                            f"{runtime.recent_history_manager.compress_threshold})，触发压缩"
                        )
                        try:
                            # Pass an empty message list to trigger compression only.
                            await _compress_recent_history(name)
                            logger.info(f"[IdleMaint] {name}: 历史记录压缩完成")
                        except Exception as e:
                            logger.warning(f"[IdleMaint] {name}: 历史记录压缩失败: {e}")

                    # -- Task 1b: Vector-based fact deduplication (P2 step 2). --
                    # Runs *before* the review-gate so a character with
                    # short history still gets paraphrase consolidation
                    # (Codex PR-957 P2). The embedding worker enqueued
                    # candidate paraphrase pairs after the last fact-sweep;
                    # resolve them here via a single LLM call.
                    # fact_dedup_resolver is None when vectors are disabled
                    # or bootstrap failed — legacy hash + FTS5 dedup
                    # remains the entire dedup pipeline in that case.
                    # Strong memory disabled: skip entirely (vector deduplication was added late in evidence-RFC).
                    if powerful_enabled and runtime.fact_dedup_resolver is not None:
                        if not gates._is_idle():
                            break
                        try:
                            pending_dedup = await runtime.fact_dedup_resolver.aload_pending(name)
                            if pending_dedup:
                                logger.info(
                                    f"[IdleMaint] {name}: 发现 {len(pending_dedup)} 对未处理的 fact 候选去重，触发 LLM 审视"
                                )
                                resolved = await _resolve_fact_dedup_with_language(name)
                                if resolved:
                                    logger.info(
                                        f"[IdleMaint] {name}: 完成 {resolved} 对 fact 去重决策"
                                    )
                        except Exception as e:
                            logger.warning(f"[IdleMaint] {name}: fact 向量去重失败: {e}")

                    # -- Task 2: Review persona contradictions (skip when strong memory is disabled). --
                    # resolve_corrections came from evidence-RFC. Contradictions may still be queued
                    # by the keyword-overlap heuristic in aadd_fact triggering _aqueue_correction
                    # while strong memory is disabled (time-driven aadd_fact also runs this check),
                    # but batch LLM review is expensive, so do not consume the queue while disabled.
                    # The queue accumulates until the user re-enables strong memory.
                    if powerful_enabled:
                        if not gates._is_idle():
                            break
                        try:
                            pending_corrections = await runtime.persona_manager.aload_pending_corrections(name)
                            if pending_corrections:
                                logger.info(
                                    f"[IdleMaint] {name}: 发现 {len(pending_corrections)} 条未处理的 persona 矛盾，触发审视"
                                )
                                resolved = await _resolve_persona_corrections_with_language(name)
                                if resolved:
                                    logger.info(f"[IdleMaint] {name}: 审视了 {resolved} 条 persona 矛盾")
                        except Exception as e:
                            logger.warning(f"[IdleMaint] {name}: persona 矛盾审视失败: {e}")

                    # -- Task 3: Memory consolidation review. --
                    # Phase C: centralize all gates in maybe_spawn_review; IdleMaint
                    # no longer gates individually. The spawn function checks review_enabled / history length /
                    # min_interval / new messages / in-flight state itself, skipping when gates fail.
                    if not gates._is_idle():
                        break
                    try:
                        await _spawn_review_with_character_language(name)
                    except Exception as e:
                        logger.warning(f"[IdleMaint] {name}: 记忆整理启动失败: {e}")

                except Exception as e:
                    logger.debug(f"[IdleMaint] {name}: 处理失败，跳过: {e}")
        finally:
            await asyncio.sleep(IDLE_CHECK_INTERVAL)


# ── memory-evidence-rfc §5: one-shot migration ──────────────────────

_MIGRATION_MARKER_ENTITY = '__meta__'
_MIGRATION_MARKER_ENTRY = '__evidence_migration_v1__'


def _migration_seed_from_reflection_status(status: str) -> tuple[float, float]:
    if status == 'promoted':
        return 2.0, 0.0
    if status == 'confirmed':
        return 1.0, 0.0
    if status == 'denied':
        return 0.0, 2.0
    return 0.0, 0.0


async def _aone_shot_migration_if_needed(lanlan_name: str) -> None:
    """Seed evidence fields on legacy reflection / persona entries.

    Marker-based guard: we inject a synthetic `__meta__.__evidence_migration_v1__`
    entry into persona (idempotent — `_find_entry_in_section` returns None if
    missing). Subsequent boots see the marker and skip.

    Reconciler-safe: all seed mutations go through `aapply_signal` which is
    event-sourced. A half-run migration is fully resumable: already-seeded
    entries have non-None `rein_last_signal_at`/`disp_last_signal_at` (set by
    the first seed event) and are skipped on resume.
    """
    try:
        persona = await runtime.persona_manager.aensure_persona(lanlan_name)
    except Exception as e:
        logger.debug(f"[Migration] {lanlan_name}: 读取 persona 失败: {e}")
        return

    marker_section = persona.get(_MIGRATION_MARKER_ENTITY)
    if isinstance(marker_section, dict):
        for entry in marker_section.get('facts', []):
            if isinstance(entry, dict) and entry.get('id') == _MIGRATION_MARKER_ENTRY:
                return  # Already migrated on a prior boot

    logger.info(f"[Migration] {lanlan_name}: 触发 evidence 字段一次性种子迁移")

    # Seed reflections
    try:
        reflections = await runtime.reflection_engine._aload_reflections_full(lanlan_name)
    except Exception as e:
        logger.warning(f"[Migration] {lanlan_name}: 读取 reflections 失败: {e}")
        reflections = []

    seeded_reflection = 0
    seed_failures = 0  # Do not write the marker if anything fails, allowing completion next round.
    for r in reflections:
        if not isinstance(r, dict):
            continue
        rid = r.get('id')
        if not rid:
            continue
        # Skip already-seeded
        if r.get('rein_last_signal_at') or r.get('disp_last_signal_at'):
            continue
        rein, disp = _migration_seed_from_reflection_status(r.get('status', 'pending'))
        if rein == 0.0 and disp == 0.0:
            continue  # pending → no seed needed (defaults already 0)
        delta = {'reinforcement': rein, 'disputation': disp}
        try:
            ok = await runtime.reflection_engine.aapply_signal(
                lanlan_name, rid, delta, source=EVIDENCE_SOURCE_MIGRATION_SEED,
            )
            if ok:
                seeded_reflection += 1
        except Exception as e:
            seed_failures += 1
            logger.warning(f"[Migration] {lanlan_name}: seed reflection {rid} 失败: {e}")

    # Persona entries: non-protected with no prior signal timestamps get a
    # zero-seed event so they carry the evidence schema keys consistently
    # on disk even before the first real signal arrives. Protected entries
    # are exempt (their evidence_score is always inf anyway).
    seeded_persona = 0
    for entity_key, section in list(persona.items()):
        if entity_key == _MIGRATION_MARKER_ENTITY or not isinstance(section, dict):
            continue
        for entry in section.get('facts', []):
            if not isinstance(entry, dict):
                continue
            if entry.get('protected'):
                continue
            if entry.get('rein_last_signal_at') or entry.get('disp_last_signal_at'):
                continue
            if entry.get('reinforcement') or entry.get('disputation'):
                continue
            entry_id = entry.get('id')
            if not entry_id:
                continue
            # A zero delta means noop plus field normalization; do not advance last_signal_at,
            # but run record_and_save once to ensure the view has the complete schema.
            try:
                ok = await runtime.persona_manager.aapply_signal(
                    lanlan_name, entity_key, entry_id,
                    delta={'reinforcement': 0.0, 'disputation': 0.0},
                    source=EVIDENCE_SOURCE_MIGRATION_SEED,
                )
                if ok:
                    seeded_persona += 1
            except Exception as e:
                seed_failures += 1
                logger.warning(
                    f"[Migration] {lanlan_name}: seed persona {entity_key}/{entry_id} 失败: {e}"
                )

    # CodeRabbit PR #929 fix: do not write the marker if any seed failed this round;
    # resume on next startup (already seeded fields are skipped), preventing transient I/O
    # failures from leaving entries permanently unseeded.
    if seed_failures > 0:
        logger.warning(
            f"[Migration] {lanlan_name}: 本轮 {seed_failures} 条 seed 失败 "
            f"（reflection={seeded_reflection} persona={seeded_persona}），"
            f"marker 暂不写入，下次启动继续补"
        )
        return

    # Drop the marker entry so we don't re-run next boot. Marker is a
    # synthetic "fact" under a synthetic entity — it never surfaces in
    # render (protected-free path for it is also skipped; render loops
    # over the known entity keys and the sync_character_card path).
    async with runtime.persona_manager._get_alock(lanlan_name):
        persona = await runtime.persona_manager._aensure_persona_locked(lanlan_name)
        marker_section = persona.setdefault(_MIGRATION_MARKER_ENTITY, {})
        facts = marker_section.setdefault('facts', [])
        if not any(
            isinstance(e, dict) and e.get('id') == _MIGRATION_MARKER_ENTRY
            for e in facts
        ):
            facts.append({
                'id': _MIGRATION_MARKER_ENTRY,
                'text': '',
                'source': EVIDENCE_SOURCE_MIGRATION_SEED,
                'source_id': None,
                'protected': True,  # Exempt arbitrary render/archive scans.
                'migrated_at': datetime.now().isoformat(),
            })
            await runtime.persona_manager.asave_persona(lanlan_name, persona)

    logger.info(
        f"[Migration] {lanlan_name}: seed 完成 "
        f"reflection={seeded_reflection} persona={seeded_persona}"
    )


# ── memory-evidence-rfc §3.5.5: one-shot archive migration ──────────


async def _aone_shot_archive_migration_if_needed(lanlan_name: str) -> None:
    """Migrate legacy flat ``reflections_archive.json`` → sharded directory.

    Idempotent: a sentinel file inside the new dir guards re-runs.
    Persona had no flat archive predecessor, so only reflection needs
    migration here.
    """
    try:
        await runtime.reflection_engine.aone_shot_archive_migration(lanlan_name)
    except Exception as e:
        # NEVER let archive migration block boot — RFC §3.5.5 explicitly
        # allows the legacy file to remain as fallback if migration fails.
        logger.warning(
            f"[Migration] {lanlan_name}: 旧 reflections_archive 分片迁移失败 (非致命): {e}"
        )


# ── memory-evidence-rfc §3.5: periodic archive sweep ────────────────


# Round-robin starting cursor: increment each round. Always starting at catgirl_names[0]
# and breaking on the first hit would let the first character monopolize work (PR #1316 CodeRabbit review).
# Module state is safe: one loop instance, one event loop, no concurrency.
_RECHECK_RR_CURSOR: int = 0


async def _periodic_slow_memory_recheck_loop():
    """Schema v1 → v2 slow memory re-judgement loop.

    Re-judges 1 reflection / fact every MEMORY_RECHECK_INTERVAL_SECONDS seconds.
    Priority: finish all characters' v1 reflections first, then facts. Only 1
    entry per round, throttled so the LLM doesn't steal quota from the working
    model (following the background-tier design of archive_sweep).

    Multi-character fairness: `_RECHECK_RR_CURSOR` rotates the round-robin start —
    each round scans from the cursor, breaks on a hit + advances the cursor. When
    catgirl A has 100 v1 entries and catgirl B only 1, B still gets a scheduling
    slot within N rounds rather than being monopolized by A's long tail.

    LLM output:
    - reflection: temporal_scope (pattern/state/episode) + event_when (relative offset)
    - fact:       single event_when field
    The system resolves event_start_at / event_end_at against created_at as the
    anchor and writes them back.

    Skip conditions (done in the store layer):
    - schema_version >= CURRENT
    - reflection status in REFLECTION_TERMINAL_STATUSES (archived etc.)
    - archived reflections / facts live in shard files, never loaded on the main
      path, so they naturally can't be selected

    First run delayed by MEMORY_RECHECK_INITIAL_DELAY_SECONDS seconds (staggered
    against the other background loops). When `MEMORY_RECHECK_ENABLED=False` the
    whole loop never starts.
    """
    global _RECHECK_RR_CURSOR
    if not MEMORY_RECHECK_ENABLED:
        logger.info("[MemoryRecheck] 重判循环未启用 (MEMORY_RECHECK_ENABLED=False)")
        return
    await asyncio.sleep(MEMORY_RECHECK_INITIAL_DELAY_SECONDS)
    logger.info("[MemoryRecheck] 慢速 schema v1→v2 重判循环启动")
    while True:
        try:
            character_data = await runtime._config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[MemoryRecheck] 加载角色列表失败: {e}")
            await asyncio.sleep(MEMORY_RECHECK_INTERVAL_SECONDS)
            continue

        # Round-robin: increment the starting point each round so all N characters are tried within N rounds.
        n = len(catgirl_names)
        if n == 0:
            await asyncio.sleep(MEMORY_RECHECK_INTERVAL_SECONDS)
            continue
        start = _RECHECK_RR_CURSOR % n
        ordered = catgirl_names[start:] + catgirl_names[:start]
        _RECHECK_RR_CURSOR = (start + 1) % n

        # Stage 1: prioritize reflections (less data, direct prompt impact, high value).
        # Stage 2: process facts only after all reflections (more data, indirect impact).
        # Touch only one entry per outer loop to prevent one character's reflections monopolizing work.
        did_one = False
        for name in ordered:
            try:
                if await runtime.reflection_engine.arecheck_one_legacy_reflection(name):
                    did_one = True
                    break
            except Exception as e:
                logger.debug(f"[MemoryRecheck] {name} reflection recheck 异常: {e}")
        if not did_one:
            for name in ordered:
                try:
                    if await runtime.fact_store.arecheck_one_legacy_fact(name):
                        did_one = True
                        break
                except Exception as e:
                    logger.debug(f"[MemoryRecheck] {name} fact recheck 异常: {e}")

        await asyncio.sleep(MEMORY_RECHECK_INTERVAL_SECONDS)


# In-process throttling for time-based scoped-subject archival (day-level criteria need no persistence:
# an extra first sweep after restart is idempotent).
_subject_archive_last_run: dict[str, datetime] = {}


async def _amaybe_sweep_subject_archive(name: str, now: datetime) -> None:
    """Scoped subject time-driven archival stage inside the archive sweep.

    Dual of the score-driven sweep body: legacy entries leave via
    ``sub_zero_days`` accumulation, scoped subjects leave via last-write
    age (they can never accumulate ``sub_zero_days`` — no evidence signal
    path reaches them). Throttled per character because the staleness
    criterion has day granularity while the sweep loop runs hourly.
    """
    # Function-local import reads config attributes on every call, allowing tests to patch
    # config.SCOPED_*; a module-level from-import would freeze values at import time.
    from config import (
        SCOPED_SUBJECT_ARCHIVE_ENABLED,
        SCOPED_SUBJECT_ARCHIVE_MIN_INTERVAL_SECONDS,
    )
    if not SCOPED_SUBJECT_ARCHIVE_ENABLED:
        return
    min_interval = SCOPED_SUBJECT_ARCHIVE_MIN_INTERVAL_SECONDS
    last = _subject_archive_last_run.get(name)
    if last is not None and (now - last).total_seconds() < min_interval:
        return
    # Reserve the window before running: persistent failures retry at the throttle interval, not hourly.
    _subject_archive_last_run[name] = now
    from memory.subject_archive import asweep_scoped_subject_archive
    try:
        await asweep_scoped_subject_archive(
            name,
            fact_store=runtime.fact_store,
            persona_manager=runtime.persona_manager,
            reflection_engine=runtime.reflection_engine,
            now=now,
        )
    except Exception as e:
        logger.warning(f"[SubjectArchive] {name}: sweep 失败: {e}")


async def _periodic_archive_sweep_loop():
    """Periodically scan all non-protected reflection / persona entries
    and (a) bump `sub_zero_days` for those with `evidence_score < 0`
    today, (b) move entries with `sub_zero_days >= EVIDENCE_ARCHIVE_DAYS`
    into a sharded archive file.

    Additionally runs the scoped-subject time-driven archival stage
    (``_amaybe_sweep_subject_archive``) per character, throttled to
    ``SCOPED_SUBJECT_ARCHIVE_MIN_INTERVAL_SECONDS``.

    Runs every `EVIDENCE_ARCHIVE_SWEEP_INTERVAL_SECONDS`. The
    `maybe_mark_sub_zero` helper has its own day-based debounce so a
    sub-day cadence does not over-count (RFC §3.5.3).

    Per-character iteration is parallel (`asyncio.gather`) — each
    character has independent files + locks; one slow char must not
    block another.

    First run delayed by _INITIAL_DELAY_ARCHIVE seconds (much smaller than
    INTERVAL=3600s, so even short-session users get one archive pass; afterwards
    it runs at the INTERVAL cadence).
    """
    from memory.evidence import maybe_mark_sub_zero
    await asyncio.sleep(_INITIAL_DELAY_ARCHIVE)
    while True:
        try:
            character_data = await runtime._config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[ArchiveSweep] 加载角色列表失败: {e}")
            await asyncio.sleep(EVIDENCE_ARCHIVE_SWEEP_INTERVAL_SECONDS)
            continue

        now = datetime.now()

        async def _sweep_one(name: str):
            """Scan one character's reflections + persona entries.

            For each non-protected entry:
              1. Snapshot-test `maybe_mark_sub_zero` (mutates a COPY so
                 we don't dirty the cache; the real increment + event
                 happen inside `aincrement_sub_zero` under the per-char
                 lock).
              2. Call `aincrement_sub_zero` if needed → returns the new
                 count or None (no-op).
              3. Determine the effective `sub_zero_days` for the archive
                 check:
                    - If we just incremented → use the returned count
                    - Else → use the on-disk count from step 1's read
                 Same-tick archival saves an extra sweep cycle for
                 entries that were already at threshold but missed the
                 last increment due to debounce.
              4. If `effective_sz >= EVIDENCE_ARCHIVE_DAYS` → archive.

            All three operations (increment / archive / their event
            writes) re-read the view under the per-char lock, so this
            outer scan can use a stale snapshot safely.
            """
            try:
                # ── reflections ──
                refls = await runtime.reflection_engine._aload_reflections_full(name)
                for r in refls:
                    if not isinstance(r, dict):
                        continue
                    if r.get('protected'):
                        continue
                    rid = r.get('id')
                    if not rid:
                        continue
                    pre_sz = int(r.get('sub_zero_days', 0) or 0)
                    will_increment = maybe_mark_sub_zero(dict(r), now)
                    new_count: int | None = None
                    if will_increment:
                        try:
                            new_count = await runtime.reflection_engine.aincrement_sub_zero(
                                name, rid, now,
                            )
                        except Exception as e:
                            logger.warning(
                                f"[ArchiveSweep] {name}: reflection {rid} "
                                f"sub_zero 增量失败: {e}"
                            )
                    effective_sz = new_count if new_count is not None else pre_sz
                    if effective_sz >= EVIDENCE_ARCHIVE_DAYS:
                        try:
                            await runtime.reflection_engine.aarchive_reflection(name, rid)
                        except Exception as e:
                            logger.warning(
                                f"[ArchiveSweep] {name}: reflection {rid} 归档失败: {e}"
                            )

                # ── persona entries ──
                persona = await runtime.persona_manager.aensure_persona(name)
                # Snapshot (entity_key, entry_id, pre_sz) tuples; mutations
                # go through aincrement / aarchive which re-load.
                snapshots: list[tuple[str, str, int, bool]] = []
                for entity_key, section in list(persona.items()):
                    if not isinstance(section, dict):
                        continue
                    for entry in section.get('facts', []):
                        if not isinstance(entry, dict):
                            continue
                        if entry.get('protected'):
                            continue
                        eid = entry.get('id')
                        if not eid:
                            continue
                        pre_sz = int(entry.get('sub_zero_days', 0) or 0)
                        will_inc = maybe_mark_sub_zero(dict(entry), now)
                        snapshots.append((entity_key, eid, pre_sz, will_inc))

                for entity_key, eid, pre_sz, will_inc in snapshots:
                    new_count = None
                    if will_inc:
                        try:
                            new_count = await runtime.persona_manager.aincrement_sub_zero(
                                name, entity_key, eid, now,
                            )
                        except Exception as e:
                            logger.warning(
                                f"[ArchiveSweep] {name}: persona {entity_key}/{eid} "
                                f"sub_zero 增量失败: {e}"
                            )
                    effective_sz = new_count if new_count is not None else pre_sz
                    if effective_sz >= EVIDENCE_ARCHIVE_DAYS:
                        try:
                            await runtime.persona_manager.aarchive_persona_entry(
                                name, entity_key, eid,
                            )
                        except Exception as e:
                            logger.warning(
                                f"[ArchiveSweep] {name}: persona {entity_key}/{eid} 归档失败: {e}"
                            )

                # -- Scoped subjects (time-driven, group-memory series 5/7). --
                await _amaybe_sweep_subject_archive(name, now)
            except Exception as e:
                logger.debug(f"[ArchiveSweep] {name}: 扫描失败，跳过: {e}")

        if catgirl_names:
            await asyncio.gather(
                *(_sweep_one(name) for name in catgirl_names),
                return_exceptions=True,
            )

        await asyncio.sleep(EVIDENCE_ARCHIVE_SWEEP_INTERVAL_SECONDS)
