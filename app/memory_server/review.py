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

"""Recent-history review + best-effort backup compression pipeline.

Owns the review/correction task registries (``correction_tasks`` /
``correction_cancel_flags`` / ``compress_backup_tasks``) and the per-name
spawn locks, co-located with the unified ``maybe_spawn_review`` gate chain
(Phase C), the background review runner and the compression-failure backup
path. Failure-backoff bookkeeping persists via ``gates._maint_state``.
"""

import asyncio
from datetime import datetime

from . import gates, runtime
from ._shared import logger
from .gates import (
    LONG_IDLE_REVIEW_BYPASS_SECONDS,
    MIN_NEW_MSGS_FOR_REVIEW,
    REVIEW_MIN_INTERVAL,
    REVIEW_SKIP_HISTORY_LEN,
)


# 全局变量用于管理correction任务
correction_tasks = {}  # {lanlan_name: asyncio.Task}
correction_cancel_flags = {}  # {lanlan_name: asyncio.Event}


# Phase C: 防 spawn 竞态——/process /renew /settle / IdleMaint 都共用 maybe_spawn_review，
# 多入口同时进 gate 检查会有 in-flight check → spawn 之间的 await 窗口；用 per-name lock
# 串行化 gate+spawn 这一段，确保同名角色至多一个 review 在跑。
_review_spawn_locks: dict[str, asyncio.Lock] = {}


def _clear_review_output_exhaustion_state(state: dict) -> None:
    state['review_output_exhaustion_attempts'] = 0
    state['review_output_exhaustion_min_context_tokens'] = None
    state['review_output_exhaustion_blocked'] = False


def _get_review_spawn_lock(name: str) -> asyncio.Lock:
    """Lazy per-name asyncio.Lock serializing the gate+spawn check."""
    lock = _review_spawn_locks.get(name)
    if lock is None:
        lock = asyncio.Lock()
        _review_spawn_locks[name] = lock
    return lock


def _count_new_user_msgs_since_last_review(name: str, current_history: list) -> float:
    """Count the user msgs in history since the last review cutoff.

    White review (fingerprint=None) → treated as plenty, allowed through.
    Fingerprint not found in current (compressed / cleared) → likewise treated as
    plenty, allowed through (should re-review ASAP to rebuild the fingerprint).
    """
    from memory.recent import _find_fingerprint_position
    fp = gates._maint_state.get(name, {}).get('last_reviewed_cutoff_tail')
    if not fp:
        return float('inf')
    cutoff_idx = _find_fingerprint_position(current_history, fp)
    if cutoff_idx is None:
        return float('inf')
    return sum(
        1 for m in current_history[cutoff_idx + 1:]
        if getattr(m, 'type', '') == 'human'
    )


async def maybe_spawn_review(name: str) -> None:
    """Unified review trigger entry (Phase C).

    /process /renew /settle / IdleMaint all call this one function. It does
    **not** cancel any running review — on seeing one in-flight it simply skips
    this spawn. The spawn lock serializes gate+spawn against multi-entry races.

    Gates (failing any one skips):
    1. a review is already running (in-flight)
    2. ``review_enabled`` (the ``recent_memory_auto_review`` flag)
    3. history length < ``REVIEW_SKIP_HISTORY_LEN``
    4. less than ``REVIEW_MIN_INTERVAL`` since the last review finished
    5. user msgs accumulated since the last review cutoff < ``MIN_NEW_MSGS_FOR_REVIEW``
    """
    async with _get_review_spawn_lock(name):
        # Gate 1: in-flight
        existing = correction_tasks.get(name)
        if existing is not None and not existing.done():
            return
        # Gate 2: review_enabled
        if not await gates._ais_review_enabled():
            return
        # 拉 history（gate 3/5 + 后续做 snapshot 都需要）
        try:
            history = await runtime.recent_history_manager.aget_recent_history(name)
        except Exception as e:
            logger.debug(f"[Review/spawn] {name}: 拉 history 失败: {e}")
            return
        # Gate 3: history 长度
        if len(history) < REVIEW_SKIP_HISTORY_LEN:
            return
        # Gate 4: min interval
        last_review = gates._maint_state.get(name, {}).get('last_review_ts')
        if last_review:
            try:
                elapsed = (datetime.now() - datetime.fromisoformat(last_review)).total_seconds()
                effective_min = REVIEW_MIN_INTERVAL
                if elapsed < effective_min:
                    return
            except (ValueError, TypeError):
                # last_review_ts 格式损坏（旧版本字段 / 手改文件 / 编码错误）→
                # 视为"从未 review 过"，不阻塞触发；继续走 gate 5（新消息门）。
                # 下次 review 成功后会用合法 ISO 字符串覆写。
                pass
        # Gate 5: 够多新 user 消息（含长挂机 bypass）
        new_msg_count = _count_new_user_msgs_since_last_review(name, history)
        if new_msg_count < MIN_NEW_MSGS_FOR_REVIEW:
            # 长挂机 bypass：≥1 条未 review 的新消息且全局静默 ≥ 30 min →
            # 允许凑不够批量的尾巴也跑一次 review。否则用户挂机一夜回来发现
            # console 里前一晚的零散对话永远停在"差几条不够触发"。
            idle_secs = (datetime.now() - gates._last_activity_time).total_seconds()
            if not (new_msg_count >= 1 and idle_secs >= LONG_IDLE_REVIEW_BYPASS_SECONDS):
                return
            logger.info(
                f"[Review/spawn] {name}: 长挂机 bypass MIN_NEW_MSGS_FOR_REVIEW "
                f"(new_msgs={new_msg_count}, idle={idle_secs:.0f}s)"
            )
        # Gate 6a: 输出 token 耗尽断路器。它跨 tail fingerprint 累计，因此新增
        # 消息/上下文增长不会解禁；只有压缩后 context token 严格低于失败期间的
        # 最小值才恢复。
        from config import MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS
        from memory.recent import review_context_token_count
        state = gates._maint_state.setdefault(name, {})
        exhaustion_attempts = state.get('review_output_exhaustion_attempts', 0) or 0
        exhaustion_blocked = bool(state.get('review_output_exhaustion_blocked'))
        if (
            exhaustion_blocked
            or exhaustion_attempts >= MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS
        ):
            failed_min_tokens = state.get('review_output_exhaustion_min_context_tokens')
            try:
                failed_min_tokens = int(failed_min_tokens or 0)
            except (TypeError, ValueError):
                failed_min_tokens = 0
            current_tokens = await review_context_token_count(history)
            if failed_min_tokens > 0 and current_tokens >= failed_min_tokens:
                logger.debug(
                    f"[Review/spawn] {name}: 输出耗尽断路器已开启 "
                    f"(连续 {exhaustion_attempts} 次，context={current_tokens} >= "
                    f"失败最小值 {failed_min_tokens})，跳过本轮"
                )
                return
            _clear_review_output_exhaustion_state(state)
            await gates._asave_maint_state()
            logger.info(
                f"[Review/spawn] {name}: context 已缩短 "
                f"({current_tokens} < {failed_min_tokens})，恢复历史审阅"
            )

        # Gate 6b: 通用失败退避（dead-letter）。review 连续失败 ≥
        # MEMORY_LIVENESS_MAX_ATTEMPTS 次且**输入未变**（当前 history 末尾 K 条
        # fingerprint == 上次失败时记下的）→ 跳过本次 spawn，不再每轮空烧
        # 3×110s 超时。输入一变（master 发了新消息，尾部 fingerprint 变）→ 视为
        # 新输入，清掉失败计数放行重试。
        # 必须放在 Gate 5 之后：长挂机 bypass 在 correction 模型持续超时时会
        # 主动给死循环续命，本闸门要能压过它（用户审计 #1：实锤的整夜无限重烧）。
        from config import MEMORY_LIVENESS_MAX_ATTEMPTS
        from memory.recent import build_review_fingerprint
        fail_attempts = state.get('review_fail_attempts', 0) or 0
        if fail_attempts >= MEMORY_LIVENESS_MAX_ATTEMPTS:
            cur_fp = build_review_fingerprint(history)
            if state.get('review_fail_fp') == cur_fp:
                logger.debug(
                    f"[Review/spawn] {name}: 失败退避 dead-letter "
                    f"(连续失败 {fail_attempts} 次 ≥ {MEMORY_LIVENESS_MAX_ATTEMPTS} "
                    f"且输入未变)，跳过本轮"
                )
                return
            # 输入已变 → 旧失败计数过期，复位后放行重试
            state['review_fail_attempts'] = 0
            state['review_fail_fp'] = None
            await gates._asave_maint_state()
        # 全过 → spawn
        logger.info(f"[Review/spawn] {name}: 触发 review (history_len={len(history)})")
        cancel_event = asyncio.Event()
        correction_cancel_flags[name] = cancel_event
        snapshot = list(history)  # 浅拷贝即可，消息对象不可变
        # 把 cancel_event 显式传给后台 task（不再依靠 finally 时再从 dict 拿），
        # 这样 task 自己持有的 event 引用不会被并发的新 spawn 覆盖。
        task = asyncio.create_task(_run_review_in_background(name, snapshot, cancel_event))
        correction_tasks[name] = task


async def _record_review_failure(lanlan_name: str, snapshot: list) -> int:
    """Record one review failure into the failure-backoff counter (used by Gate 6); returns the cumulative count.

    If the input fingerprint differs from the last failure record → zero the
    budget first, then +1, so each history tail gets its own independent budget of
    N attempts instead of accumulating across inputs (Codex P2). The 'failed'
    return branch and the except branch share this function to keep the two paths
    from drifting apart.
    """
    from memory.recent import build_review_fingerprint
    state = gates._maint_state.setdefault(lanlan_name, {})
    cur_fp = build_review_fingerprint(snapshot)
    if state.get('review_fail_fp') != cur_fp:
        state['review_fail_attempts'] = 0
    state['review_fail_attempts'] = (state.get('review_fail_attempts', 0) or 0) + 1
    state['review_fail_fp'] = cur_fp
    await gates._asave_maint_state()
    return state['review_fail_attempts']


async def _record_review_output_exhaustion(
    lanlan_name: str, snapshot: list,
) -> tuple[int, int, int]:
    """Record one output-limit failure across growing/changed tail fingerprints."""
    from config import MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS
    from memory.recent import review_context_token_count

    state = gates._maint_state.setdefault(lanlan_name, {})
    current_tokens = await review_context_token_count(snapshot)
    previous_min = state.get('review_output_exhaustion_min_context_tokens')
    try:
        previous_min = int(previous_min or 0)
    except (TypeError, ValueError):
        previous_min = 0

    attempts = state.get('review_output_exhaustion_attempts', 0) or 0
    if previous_min > 0 and current_tokens < previous_min:
        attempts = 0
        minimum_tokens = current_tokens
    else:
        minimum_tokens = min(previous_min, current_tokens) if previous_min > 0 else current_tokens

    attempts += 1
    state['review_output_exhaustion_attempts'] = attempts
    state['review_output_exhaustion_min_context_tokens'] = minimum_tokens
    state['review_output_exhaustion_blocked'] = (
        attempts >= MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS
    )
    await gates._asave_maint_state()
    return attempts, current_tokens, minimum_tokens


# ── best-effort 后台压缩（主路径 compress 失败时兜底）─────────────────────
# 真根因：主路径压缩走 LLM 耗时数秒~数十秒，限流抖动 / 偶发失败 → #1629 跳过
# 保留完整历史、下轮重试。但若持续失败，历史一直压不掉、越积越多。这里在主路径
# 压缩失败时起一个受保护的一次性后台任务尽力压（基于快照、不被对话打断；压完用
# fingerprint 对齐合并回写）。主路径某轮成功 → cancel 在跑的后台。失败退避复用
# review 的 Gate 6 模式，防 summary 模型持续故障时每轮起一个注定失败的任务空烧。
compress_backup_tasks: dict[str, asyncio.Task] = {}


async def _record_compress_backup_failure(lanlan_name: str, snapshot: list) -> int:
    """Record one backup-compression failure and return the current attempt count.

    A changed input fingerprint resets the counter so each backlog segment gets
    its own budget, matching the review-failure backoff shape.
    """
    from memory.recent import build_review_fingerprint
    state = gates._maint_state.setdefault(lanlan_name, {})
    cur_fp = build_review_fingerprint(snapshot)
    if state.get('compress_backup_fail_fp') != cur_fp:
        state['compress_backup_fail_attempts'] = 0
    state['compress_backup_fail_attempts'] = (state.get('compress_backup_fail_attempts', 0) or 0) + 1
    state['compress_backup_fail_fp'] = cur_fp
    await gates._asave_maint_state()
    return state['compress_backup_fail_attempts']


async def _clear_compress_backup_failure(lanlan_name: str) -> None:
    """Clear the backup-compression failure backoff counter."""
    state = gates._maint_state.setdefault(lanlan_name, {})
    if state.get('compress_backup_fail_attempts') or state.get('compress_backup_fail_fp'):
        state['compress_backup_fail_attempts'] = 0
        state['compress_backup_fail_fp'] = None
        await gates._asave_maint_state()


async def _run_backup_compress(lanlan_name: str, snapshot: list, detailed: bool):
    """Run best-effort background compression and merge the result under lock."""
    try:
        # 1) 压缩（锁外）。compress_history 内部按输入大小自动分段，避免输入过大超时。
        try:
            result = await runtime.recent_history_manager.compress_history(snapshot, lanlan_name, detailed)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[CompressBackup] {lanlan_name} 后台压缩抛异常，按失败处理: {e}")
            result = None
        if result is None:
            attempts = await _record_compress_backup_failure(lanlan_name, snapshot)
            logger.info(f"[CompressBackup] {lanlan_name} 后台压缩失败，退避计数 → {attempts}")
            # best-effort 也没压成 → 实在不行才丢：若历史仍超硬上限，裁剪最旧未压缩
            # 原文兜底（锁内串行化写）。暂时性失败时后台会成功、走不到这里。
            async with runtime._get_settle_lock(lanlan_name):
                await runtime.recent_history_manager.enforce_hard_cap(lanlan_name)
            return
        # 2) 合并写回（锁内，快）。merge_backup_memo 用 fingerprint 对齐，积压已被
        #    主路径压掉 / 被清空就返回 'moot' 丢弃（白做）。
        async with runtime._get_settle_lock(lanlan_name):
            status = await runtime.recent_history_manager.merge_backup_memo(lanlan_name, snapshot, result[0])
        if status == 'failed':
            # 合并落盘失败 → 没真正写成功，bump 退避（不清），下次再试。
            attempts = await _record_compress_backup_failure(lanlan_name, snapshot)
            logger.info(f"[CompressBackup] {lanlan_name} 后台压缩合并落盘失败，退避计数 → {attempts}")
            return
        # 'merged' 或 'moot' 都说明这段积压已处理 / 已过时，清退避计数。
        await _clear_compress_backup_failure(lanlan_name)
        logger.info(f"[CompressBackup] {lanlan_name} 后台压缩完成：{status}")
    except asyncio.CancelledError:
        logger.info(f"[CompressBackup] {lanlan_name} 后台压缩被取消（主路径已成功）")
    except Exception as e:
        logger.error(f"[CompressBackup] {lanlan_name} 后台压缩后处理出错: {e}")
    finally:
        cur = asyncio.current_task()
        if compress_backup_tasks.get(lanlan_name) is cur:
            compress_backup_tasks.pop(lanlan_name, None)


async def _on_compress_done(lanlan_name: str, snapshot: list, ok: bool, detailed: bool):
    """Compression-finished callback for update_history (injected into recent.py).

    ok=True (main-path compression succeeded) → cancel any running backup task +
    clear the backoff counter; ok=False (main-path compression failed) → spawn a
    protected best-effort backup compression (unless one is in flight or the
    failure backoff blocks it).

    This callback only spawns / cancels tasks and never awaits the background
    LLM — it may be invoked while _get_settle_lock is held (/renew, /settle)
    and must not block."""
    if ok:
        task = compress_backup_tasks.get(lanlan_name)
        if task is not None and not task.done():
            task.cancel()
        await _clear_compress_backup_failure(lanlan_name)
        return
    # ok=False：主路径压缩失败 → 起后台兜底
    if not snapshot:
        return
    existing = compress_backup_tasks.get(lanlan_name)
    if existing is not None and not existing.done():
        return  # in-flight：同角色已有后台压缩在跑，不重复起
    # 失败退避（Gate 6 模式）：连续失败 ≥ N 且输入未变 → dead-letter，不再起，
    # 防 summary 模型持续故障时每轮都起一个注定失败的后台任务空烧。
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    from memory.recent import build_review_fingerprint
    state = gates._maint_state.setdefault(lanlan_name, {})
    fail_attempts = state.get('compress_backup_fail_attempts', 0) or 0
    if fail_attempts >= MEMORY_LIVENESS_MAX_ATTEMPTS:
        cur_fp = build_review_fingerprint(snapshot)
        if state.get('compress_backup_fail_fp') == cur_fp:
            logger.debug(
                f"[CompressBackup] {lanlan_name} 失败退避 dead-letter"
                f"（连续失败 {fail_attempts} 次且输入未变），跳过"
            )
            # dead-letter：后台已救不回 → 此时才裁剪兜底（实在不行才丢）。不 acquire
            # settle lock：本回调可能已在 /renew·/settle 的锁内被调（重入会死锁）；
            # enforce_hard_cap 是 best-effort 写。
            await runtime.recent_history_manager.enforce_hard_cap(lanlan_name)
            return
        # 输入变了 → 旧计数过期，复位放行
        state['compress_backup_fail_attempts'] = 0
        state['compress_backup_fail_fp'] = None
        await gates._asave_maint_state()
    task = runtime._spawn_background_task(_run_backup_compress(lanlan_name, list(snapshot), detailed))
    compress_backup_tasks[lanlan_name] = task
    logger.info(f"[CompressBackup] {lanlan_name} 主路径压缩失败，已起后台兜底压缩任务")


async def _run_review_in_background(
    lanlan_name: str, snapshot: list, cancel_event: asyncio.Event,
):
    """Run review_history in the background, with cancellation support.

    Phase C changes:
    - snapshot + cancel_event are captured and passed in by the caller (the task
      holds its own references)
    - review_history returns a (status, fingerprint) tuple:
        ('patched', new_fp) → patch succeeded; new_fp is the fingerprint of the
                              last K entries of new_history after the patch —
                              **must** use this new fingerprint (the review may
                              have rewritten any of the last K entries;
                              ``build_review_fingerprint(snapshot)`` is stale)
        ('white', None)    → cutoff mismatch / whole segment dropped
        ('failed', None)   → LLM failure / cancelled / malformed output
        ('output_exhausted', None) → provider hit its output-token limit

    White-review handling (CodeRabbit Issue #1 fix):
    - do **not** update last_review_ts → next round's gate 4 sees "long since the
      last review" → combined with fingerprint=None → the MIN_NEW_MSGS gate reads
      as ∞ → the next /process re-reviews immediately, rebuilding the anchor.
      This matches the original user intent of "white review = anchor lost,
      rebuild ASAP".

    Cleanup (CodeRabbit Issue #2 fix):
    - finally compares task/event identity before pop/clear, so entries written by
      a concurrently spawned new review aren't deleted by mistake. In theory the
      spawn lock + asyncio finally semantics already preclude the race, but the
      identity check is cheap defense.
    """
    try:
        # 只把 review_history 调用本身包进内层 try：它抛异常才算"review 失败"，
        # 收口成 ('failed', None) 走下面统一的失败分支记一次退避。成功后的 result
        # 处理 / state 落盘异常**不**能被当成 review 失败（否则 patched/white 的
        # save 抖动会误判成失败、误触 Gate 6 dead-letter；'failed' 分支自己 save
        # 抛异常也会被重复记一次）——那类异常交给外层 except 纯兜底、不 bump。
        # 注：asyncio.CancelledError 是 BaseException，不被 except Exception 捕获，
        # 会正常冒泡到外层 CancelledError 分支。
        try:
            result = await runtime.recent_history_manager.review_history(
                lanlan_name, snapshot, cancel_event=cancel_event,
            )
        except Exception as e:
            logger.error(f"❌ {lanlan_name} 的 review_history 抛异常，按失败处理: {e}")
            result = ('failed', None)
        # 兼容意外的返回类型，统一解包
        if isinstance(result, tuple) and len(result) == 2:
            status, fingerprint = result
        else:
            status, fingerprint = ('failed', None)

        state = gates._maint_state.setdefault(lanlan_name, {})
        if status == 'patched':
            logger.info(f"✅ {lanlan_name} 的记忆整理任务完成")
            state['review_clean'] = True
            state['last_review_ts'] = datetime.now().isoformat()
            state['last_reviewed_cutoff_tail'] = fingerprint
            # 成功 → 清掉失败退避计数（Gate 6）
            state['review_fail_attempts'] = 0
            state['review_fail_fp'] = None
            _clear_review_output_exhaustion_state(state)
            await gates._asave_maint_state()
        elif status == 'white':
            logger.info(
                f"⚠️ {lanlan_name} 白 review（cutoff 失配），fingerprint 清空、不刷 ts，允许立即重试"
            )
            state['last_reviewed_cutoff_tail'] = None
            # 故意不更新 last_review_ts：让下轮 gate 4 用旧 ts（通常已过 30/60s）
            # 直接放行，配合 fingerprint=None 触发 gate 5 的 ∞ 通行 → 立即重 review。
            # 白 review 是 cutoff 失配（输入实际已变）而非失败，清退避计数允许立即重建锚点。
            state['review_fail_attempts'] = 0
            state['review_fail_fp'] = None
            _clear_review_output_exhaustion_state(state)
            await gates._asave_maint_state()
        elif cancel_event.is_set():
            # review_history 在 cancel_event 置位时也返回 ('failed', None)，但这是
            # 主动取消（cancel_correction：记忆编辑后立即生效）而非失败，不能计入
            # 失败退避——否则用户频繁编辑记忆会被误判成 poison。
            logger.info(f"ℹ️ {lanlan_name} 的记忆整理被取消（不计入失败退避）")
        elif status == 'output_exhausted':
            attempts, context_tokens, minimum_tokens = (
                await _record_review_output_exhaustion(lanlan_name, snapshot)
            )
            from config import MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS
            if attempts >= MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS:
                logger.warning(
                    f"[Review/output-limit] {lanlan_name}: 连续 {attempts} 次输出耗尽，"
                    f"暂停审阅，直到 context token 低于 {minimum_tokens}"
                )
            else:
                logger.info(
                    f"[Review/output-limit] {lanlan_name}: 输出耗尽 {attempts}/"
                    f"{MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS} "
                    f"(context={context_tokens}, 失败最小值={minimum_tokens})"
                )
        else:
            # 'failed'：LLM 持续失败 / 超时 / 格式错误。bump 失败退避计数 + 记下
            # 本次失败的输入 fingerprint，供 Gate 6 在输入不变时 dead-letter，避免
            # correction 模型一直超时 + 长挂机 bypass 续命导致整夜空烧（用户审计 #1）。
            attempts = await _record_review_failure(lanlan_name, snapshot)
            logger.info(
                f"ℹ️ {lanlan_name} 的记忆整理未执行（被跳过或失败），"
                f"失败退避计数 → {attempts}"
            )
    except asyncio.CancelledError:
        logger.info(f"⚠️ {lanlan_name} 的记忆整理任务被取消")
    except Exception as e:
        # 纯兜底：能到这里的只剩 result 处理 / state 持久化等"非 review 失败"
        # 的异常（review_history 自身抛已在内层收口成 'failed'）。这类异常**不**
        # 计入失败退避——否则成功 review 的 save 抖动会被误判成失败、误触
        # Gate 6 dead-letter 压住后续 review（Codex P2）。
        logger.error(f"❌ {lanlan_name} 的记忆整理后处理出错（不计入失败退避）: {e}")
    finally:
        # 按 task/event 身份比对再清理：如果并发的新 spawn 已经写入了新 task /
        # 新 event，本 task 不应该把它们清掉。
        current_task = asyncio.current_task()
        if correction_tasks.get(lanlan_name) is current_task:
            correction_tasks.pop(lanlan_name, None)
        if correction_cancel_flags.get(lanlan_name) is cancel_event:
            correction_cancel_flags.pop(lanlan_name, None)
