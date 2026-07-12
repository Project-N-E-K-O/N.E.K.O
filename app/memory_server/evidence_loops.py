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

from . import gates, review, runtime
from ._shared import logger
from .gates import (
    IDLE_CHECK_INTERVAL,
    _INITIAL_DELAY_ARCHIVE,
    _INITIAL_DELAY_AUTO_PROMOTE,
    _INITIAL_DELAY_IDLE_MAINT,
    _INITIAL_DELAY_REBUTTAL,
)
from .rows import _coerce_db_ts, _extract_user_messages_with_ts_from_rows


REBUTTAL_CHECK_INTERVAL = 180  # 3 分钟
REBUTTAL_FIRST_RUN_LOOKBACK_HOURS = 1  # 首次启动 / 时钟回拨兜底回扫窗口
# Drain pattern: 一次最多处理 N 条 user 消息，避免高频用户场景下 prompt 爆炸。
# 多余的留到下一轮（cursor 推进到第 N 条的 timestamp，不丢消息）。
REBUTTAL_DRAIN_BATCH_LIMIT = 20
# 读 SQL 时的硬上限——bound memory，防止 1h fallback 把整张表拉进来。
# 200 行通常包含 50-100 条 user 消息，足以喂多次 drain。
REBUTTAL_SQL_ROW_LIMIT = 200


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
        # 首次启动：把 fallback 落盘锚定。否则 LLM 连续失败时，下轮
        # cursor 仍为 None，新的 fallback 会基于新的 now 重新计算并前移
        # （滑动 1h 窗口），首轮窗口最早段消息会被永久跳过。
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
        # 自愈：把游标拉回 fallback（而非 now），使后续 tick 不再命中 rollback
        # 分支，同时保留本轮窗口 [fallback, now] 的重试能力（若 LLM 失败）
        try:
            await runtime.cursor_store.aset_cursor(
                name, CURSOR_REBUTTAL_CHECKED_UNTIL, fallback,
            )
        except Exception as e:
            logger.debug(f"[Rebuttal] {name}: rollback 自愈写入失败（将在下轮重试）: {e}")
        return fallback
    return cursor


_rebuttal_failures: dict[str, dict[str, int]] = {}
"""Per-character rebuttal LLM 失败计数：``{name: {cursor_iso: count}}``。

In-memory only（cursor 本身落盘到 cursors.json，但 counter 重启清零）。
Why in-memory: 重启后再试 ``MEMORY_LIVENESS_MAX_ATTEMPTS`` 次再 dead-letter，
避免内存 counter 错把短暂 transient 失败永久放弃；user-visible 代价 = 重启
后多卡 N × REBUTTAL_CHECK_INTERVAL 一段时间，可接受。

Liveness 兜底原因：``check_feedback_for_confirmed`` 返 None 时原代码直接
``return`` 不动 cursor → 下轮重读相同 [cursor, now] 窗口含同样的毒 user
msg → 仍失败 → 永久卡死 rebuttal 链路（毒窗口让 user 反驳信号永远进不来
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
        # 强力记忆关 → rebuttal LLM 整段停（这是 evidence-RFC 引入的最贵
        # 周期 LLM 之一，每 180s 一次开 thinking 跑 drain）。关闭后用户的
        # 反驳信号经由 per-turn check_feedback (主动搭话回应) 仍能进 evidence。
        #
        # 关态推进 cursor 到 now：否则重新开启时 _resolve_rebuttal_start_time
        # 拿到的是关闭前的旧 cursor，下一轮会把关闭期间积攒的所有 user msg
        # 整段补处理（极大 prompt + 大量 LLM 调用）。"关时不跑" 应等价于
        # "关时已 noop 处理完"——重开后从 now 重新累积，不回补。
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
                        # 单角色 cursor 推进失败不致命——下一轮再试，最坏
                        # 是该角色重开时多扫一段窗口，不影响其他角色。
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
                    # 无 confirmed 时仍需推进游标：否则等到有新 confirmed reflection
                    # 出现后，首轮会把 cursor-now 之间积攒的全部用户消息喂给
                    # check_feedback_for_confirmed，容易把无关历史回复误判为反驳。
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

                # 提取 (msg, ts) 元组（ASC by ts；ts 已归一化为 datetime）
                user_msgs_with_ts = _extract_user_messages_with_ts_from_rows(rows)
                if not user_msgs_with_ts:
                    # 窗口里只有 AI 消息或无 user 内容 → 推进 cursor 到 SQL 截
                    # 取的最后一行 ts（如果命中 LIMIT 还有更多行）或 now（清空了）
                    last_row_ts = _coerce_db_ts(rows[-1][0])
                    if len(rows) >= REBUTTAL_SQL_ROW_LIMIT and last_row_ts is not None:
                        await runtime.cursor_store.aset_cursor(
                            name, CURSOR_REBUTTAL_CHECKED_UNTIL, last_row_ts,
                        )
                    else:
                        # 既然没命中 LIMIT，窗口已经全部扫过；直接推到 now。
                        # last_row_ts 解析失败也走这条（保守 fallback）。
                        await runtime.cursor_store.aset_cursor(
                            name, CURSOR_REBUTTAL_CHECKED_UNTIL, now,
                        )
                    _rebuttal_clear_failures(name)
                    return

                # Drain 取前 N 条 user msg。然后扩展 batch 把和 batch 末位
                # 共享同 ts 的后续 user msg 也吸收进来——因为 SQL 用
                # ``timestamp BETWEEN`` (inclusive)，cursor 推进到 batch[-1].ts
                # 后下一轮会把同 ts 的行原样重读。如果不扩展，多条同 ts 的
                # user msg 在 batch 边界被切，会出现"只处理一部分，剩下的下
                # 轮当 batch 边界又被切"的死循环（``store_conversation`` 一
                # 批 message 共享 timestamp，所以同 ts 多条很常见）。
                # 扩展受 SQL 行 LIMIT 兜底，不会无界增长。
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

                # 复用 check_feedback 判断反驳
                feedbacks = await runtime.reflection_engine.check_feedback_for_confirmed(
                    name, confirmed, user_msgs,
                )
                if feedbacks is None:
                    # LLM 调用失败 → 不推进游标，下次重试这批消息。
                    # Liveness 兜底：同一 cursor 反复失败 ≥
                    # MEMORY_LIVENESS_MAX_ATTEMPTS 时强推 cursor 到 now 放弃这段
                    # 窗口（dead-letter），避免毒 user msg 让 rebuttal 链路永久
                    # 卡死。cursor 落盘到 cursors.json，stuck cursor 重启都不
                    # 复活，比 in-memory 的 signal extraction cursor 更顽固。
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

                # 成功才推进游标并持久化。Drain 推进规则：
                # - 还有 user msgs 在本次 read 内未处理（batch 已扩展含所有
                #   同 ts，所以剩余的 ts 一定 > batch[-1].ts）
                #   → cursor 推到第一个未处理 user msg 的 ts（next read 的
                #     BETWEEN 起点，包含该行不会重处理因为它本来就 unprocessed）
                # - SQL 命中 LIMIT 但 user msgs 全处理 → cursor 推到最后一行 ts
                #   (next read 会重读 same-ts cluster 但 LLM 调用幂等无害)
                # - 全干净 → cursor 推到 now
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
                # Cursor 推进 → 旧 cursor key 永远不会再被命中，清空 counter
                # 避免内存 dict 随 cursor 历史无限增长（对偶 Site 0a/0b）。
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


AUTO_PROMOTE_CHECK_INTERVAL = 180  # 3 分钟（与 rebuttal 同步，覆盖同样级别的状态变化）

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
                if powerful:
                    # score-driven + merge LLM (current evidence-RFC 路径)
                    transitions = await runtime.reflection_engine.aauto_promote_stale(name)
                else:
                    # 强力记忆关：time-driven 直接 aadd_fact，零 LLM
                    transitions = await runtime.reflection_engine.aauto_promote_time_driven(name)
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
    3. Memory tidy-up review — skipped when review_clean; subject to the REVIEW_MIN_INTERVAL
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

            # 强力记忆开关 → 控制 1b (fact_dedup) 和 2 (persona corrections)
            # 是否跑。子任务 1 (history 压缩) 和 3 (recent.review) 是 RFC 之
            # 前的基础设施，永远跑。本轮快照一次，跨角色复用。
            powerful_enabled = await gates._ais_powerful_memory_enabled()

            for name in catgirl_names:
                # 每处理一个角色前重新检查空闲，一旦变忙立即退出
                if not gates._is_idle():
                    logger.debug("[IdleMaint] 检测到新活动，中断本轮维护")
                    break

                try:
                    history = await runtime.recent_history_manager.aget_recent_history(name)
                    history_len = len(history)

                    # ── 子任务1: 历史记录压缩（有需要就跑，不受全局开关控制） ──
                    # 门槛对齐 update_history 内部的真实触发条件 `len > compress_threshold`
                    # （默认 20）。用 max_history_length（默认 10，压缩后保留条数）会让
                    # 11~20 区间持续触发 IdleMaint 但 update_history 实际不压缩，形成
                    # 每 IDLE_CHECK_INTERVAL 一次的空转日志。
                    if history_len > runtime.recent_history_manager.compress_threshold:
                        logger.info(
                            f"[IdleMaint] {name}: 历史记录过长 ({history_len} > "
                            f"{runtime.recent_history_manager.compress_threshold})，触发压缩"
                        )
                        try:
                            # 传空消息列表仅触发压缩逻辑
                            await runtime.recent_history_manager.update_history([], name, detailed=True, on_compress_done=review._on_compress_done)
                            logger.info(f"[IdleMaint] {name}: 历史记录压缩完成")
                        except Exception as e:
                            logger.warning(f"[IdleMaint] {name}: 历史记录压缩失败: {e}")

                    # ── 子任务1b: Fact 向量去重（P2 step 2） ──
                    # Runs *before* the review-gate so a character with
                    # short history still gets paraphrase consolidation
                    # (Codex PR-957 P2). The embedding worker enqueued
                    # candidate paraphrase pairs after the last fact-sweep;
                    # resolve them here via a single LLM call.
                    # fact_dedup_resolver is None when vectors are disabled
                    # or bootstrap failed — legacy hash + FTS5 dedup
                    # remains the entire dedup pipeline in that case.
                    # 强力记忆关 → 整段跳过（向量去重是 evidence-RFC 后期引入的）
                    if powerful_enabled and runtime.fact_dedup_resolver is not None:
                        if not gates._is_idle():
                            break
                        try:
                            pending_dedup = await runtime.fact_dedup_resolver.aload_pending(name)
                            if pending_dedup:
                                logger.info(
                                    f"[IdleMaint] {name}: 发现 {len(pending_dedup)} 对未处理的 fact 候选去重，触发 LLM 审视"
                                )
                                resolved = await runtime.fact_dedup_resolver.aresolve(name)
                                if resolved:
                                    logger.info(
                                        f"[IdleMaint] {name}: 完成 {resolved} 对 fact 去重决策"
                                    )
                        except Exception as e:
                            logger.warning(f"[IdleMaint] {name}: fact 向量去重失败: {e}")

                    # ── 子任务2: Persona 矛盾审视（强力记忆关时跳过） ──
                    # resolve_corrections 由 evidence-RFC 引入；矛盾队列的产生路
                    # 径（aadd_fact 的 keyword overlap heuristic 触发 _aqueue_correction）
                    # 在强力记忆关时仍可能产生（time-driven aadd_fact 也走启发式检查），
                    # 但消化路径 LLM 整批审视成本高，关时不跑。queue 会累积，
                    # 等用户重开强力记忆时一次性消化。
                    if powerful_enabled:
                        if not gates._is_idle():
                            break
                        try:
                            pending_corrections = await runtime.persona_manager.aload_pending_corrections(name)
                            if pending_corrections:
                                logger.info(
                                    f"[IdleMaint] {name}: 发现 {len(pending_corrections)} 条未处理的 persona 矛盾，触发审视"
                                )
                                resolved = await runtime.persona_manager.resolve_corrections(name)
                                if resolved:
                                    logger.info(f"[IdleMaint] {name}: 审视了 {resolved} 条 persona 矛盾")
                        except Exception as e:
                            logger.warning(f"[IdleMaint] {name}: persona 矛盾审视失败: {e}")

                    # ── 子任务3: 记忆整理 review ──
                    # Phase C: gate 逻辑全部集中到 maybe_spawn_review，IdleMaint
                    # 不再做单点门禁。spawn 函数内部自查 review_enabled / 历史长度
                    # / min_interval / 新消息门 / in-flight，不过门就 skip。
                    if not gates._is_idle():
                        break
                    try:
                        await review.maybe_spawn_review(name)
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
    seed_failures = 0  # 只要有一条失败就不写 marker，保证下轮可补
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
            # 零 delta 等效 "no-op + 字段 normalize"；不推进 last_signal_at，
            # 但走完一次 record_and_save 保证 view 里 schema 完整。
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

    # CodeRabbit PR #929 fix: 如果本轮有任何 seed 失败，marker 不写入——
    # 下次启动继续从断点补（已 seed 过的字段检查会跳过）。避免瞬时 IO
    # 抖动导致某些 entry 永远漏种。
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
                'protected': True,  # 豁免 render/archive 任意扫描
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


# Round-robin 起点游标：每轮 +1。避免每次都从 catgirl_names[0] 开始扫描
# + 命中即 break 造成首角色独占（CodeRabbit review on PR #1316 catch）。
# 模块级状态可接受：循环单实例、单事件循环、无并发。
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

        # Round-robin: 每轮起点比上轮 +1，保证 N 角色在 N 轮内都被尝试到
        n = len(catgirl_names)
        if n == 0:
            await asyncio.sleep(MEMORY_RECHECK_INTERVAL_SECONDS)
            continue
        start = _RECHECK_RR_CURSOR % n
        ordered = catgirl_names[start:] + catgirl_names[:start]
        _RECHECK_RR_CURSOR = (start + 1) % n

        # 阶段 1：reflection 优先（数据少、影响 prompt 直接、价值高）
        # 阶段 2：所有 reflection 跑完后才轮到 fact（数据多、影响间接）
        # 每次外循环只动 1 条，避免单角色 reflection 长时间独占
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


async def _periodic_archive_sweep_loop():
    """Periodically scan all non-protected reflection / persona entries
    and (a) bump `sub_zero_days` for those with `evidence_score < 0`
    today, (b) move entries with `sub_zero_days >= EVIDENCE_ARCHIVE_DAYS`
    into a sharded archive file.

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
            except Exception as e:
                logger.debug(f"[ArchiveSweep] {name}: 扫描失败，跳过: {e}")

        if catgirl_names:
            await asyncio.gather(
                *(_sweep_one(name) for name in catgirl_names),
                return_exceptions=True,
            )

        await asyncio.sleep(EVIDENCE_ARCHIVE_SWEEP_INTERVAL_SECONDS)

