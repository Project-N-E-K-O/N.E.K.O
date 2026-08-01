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
path.

Failure-backoff bookkeeping lives in ``idle_maintenance_state.json``. Every
update in this module goes through ``gates._amutate_maint_state`` with a
synchronous mutator (``_mutate_*`` below) so that read-modify-write runs as one
critical section; reads go through the read-only ``gates._maint_view``. Anything
that needs an ``await`` (token counting, fingerprinting a snapshot) is computed
before the mutator is handed over.
"""

import asyncio
import functools
from datetime import datetime

from . import gates, runtime
from ._shared import logger
from .gates import (
    LONG_IDLE_REVIEW_BYPASS_SECONDS,
    MIN_NEW_MSGS_FOR_REVIEW,
    REVIEW_MIN_INTERVAL,
    REVIEW_SKIP_HISTORY_LEN,
)
from utils.recent_file import capture_recent_generation


# 全局变量用于管理correction任务
correction_tasks = {}  # {lanlan_name: asyncio.Task}
correction_cancel_flags = {}  # {lanlan_name: asyncio.Event}


# Phase C: 防 spawn 竞态——/process /renew /settle / IdleMaint 都共用 maybe_spawn_review，
# 多入口同时进 gate 检查会有 in-flight check → spawn 之间的 await 窗口；用 per-name lock
# 串行化 gate+spawn 这一段，确保同名角色至多一个 review 在跑。
_review_spawn_locks: dict[str, asyncio.Lock] = {}
_retired_derived_task_names: set[str] = set()
_publication_held_derived_task_names: set[str] = set()


async def _cancel_character_derived_tasks_unlocked(lanlan_name: str) -> int:
    """Cancel and drain review/compression tasks derived from one character identity."""
    cancel_event = correction_cancel_flags.get(lanlan_name)
    if cancel_event is not None:
        cancel_event.set()

    candidates = []
    for registry in (correction_tasks, compress_backup_tasks):
        task = registry.get(lanlan_name)
        if task is not None and not task.done() and task not in candidates:
            candidates.append(task)
            task.cancel()

    for task in candidates:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(
                "取消角色 %s 的派生记忆任务时出现异常: %s",
                lanlan_name,
                exc,
            )

    for registry in (correction_tasks, compress_backup_tasks):
        task = registry.get(lanlan_name)
        if task is None or task.done():
            registry.pop(lanlan_name, None)
    if correction_cancel_flags.get(lanlan_name) is cancel_event:
        correction_cancel_flags.pop(lanlan_name, None)
    compress_backup_task_generations.pop(lanlan_name, None)
    return len(candidates)


def _clear_review_output_exhaustion_state(state: dict) -> bool:
    """Zero the output-exhaustion breaker in place; returns whether anything changed.

    Mutator helper — always call it with the sub-dict handed to a mutator, never
    with a ``gates._maint_view`` result.
    """
    # 三个键无条件写回（保持重构前的内存形状），但 dirty 只按「原本是否非默认值」
    # 算：全是默认值时把键补齐一遍不需要落盘，磁盘上缺这三个键与三个默认值等价。
    changed = (
        bool(state.get('review_output_exhaustion_attempts'))
        or state.get('review_output_exhaustion_min_context_tokens') is not None
        or bool(state.get('review_output_exhaustion_blocked'))
    )
    state['review_output_exhaustion_attempts'] = 0
    state['review_output_exhaustion_min_context_tokens'] = None
    state['review_output_exhaustion_blocked'] = False
    return changed


def _mutate_clear_output_exhaustion(
    state: dict, *, seen_attempts: int, seen_min_tokens: int
) -> tuple[bool, bool]:
    """Mutator: clear the output-exhaustion breaker, unless a newer failure was armed."""
    # Gate 6a 的恢复判定必须在锁外做（token 计数是 async，进不了同步 mutator），所以
    # 锁内要复查它依赖的那份输入还在不在：另一个后台 review 可能在那次 await 期间刚写下
    # 一次**新的**输出耗尽失败（带着新的 attempts / min_context_tokens）。无条件清零会把
    # 刚 armed 的断路器抹掉，于是那条已经证明压不动的 context 又会被放行重烧一轮。
    # 与 _mutate_reset_review_fail_backoff 的锁内复查同构。
    try:
        current_min = int(state.get('review_output_exhaustion_min_context_tokens') or 0)
    except (TypeError, ValueError):
        current_min = 0
    current_attempts = state.get('review_output_exhaustion_attempts', 0) or 0
    if current_min != seen_min_tokens or current_attempts != seen_attempts:
        return False, False
    _clear_review_output_exhaustion_state(state)
    # value 把「恢复是否成立」带给调用方（这正是 (dirty, value) 里 value 的用途）。
    # dirty 恒 True：走到这里说明观测到的断路器确实还在，清零一定改变了状态。
    return True, True


def _get_review_spawn_lock(name: str) -> asyncio.Lock:
    """Lazy per-name asyncio.Lock serializing the gate+spawn check."""
    lock = _review_spawn_locks.get(name)
    if lock is None:
        lock = asyncio.Lock()
        _review_spawn_locks[name] = lock
    return lock


async def cancel_character_derived_tasks(
    lanlan_name: str,
    *,
    hold_until_publication: bool = False,
) -> int:
    """Retire derived-task admission, then cancel and drain existing work."""
    async with _get_review_spawn_lock(lanlan_name):
        _retired_derived_task_names.add(lanlan_name)
        if hold_until_publication:
            _publication_held_derived_task_names.add(lanlan_name)
        return await _cancel_character_derived_tasks_unlocked(lanlan_name)


async def resume_character_derived_task_admission(lanlan_name: str) -> None:
    """Undo retirement when the operation that requested a drain did not commit."""
    async with _get_review_spawn_lock(lanlan_name):
        _publication_held_derived_task_names.discard(lanlan_name)
        _retired_derived_task_names.discard(lanlan_name)


async def reconcile_character_derived_task_admission(
    active_names: set[str],
    *,
    resume_names: set[str] | None = None,
) -> None:
    """Re-enable a retired name only after reload observes a published identity."""
    explicit_resume = resume_names or set()
    for name in sorted(_retired_derived_task_names.intersection(active_names)):
        async with _get_review_spawn_lock(name):
            if (
                name in _publication_held_derived_task_names
                and name not in explicit_resume
            ):
                continue
            _publication_held_derived_task_names.discard(name)
            _retired_derived_task_names.discard(name)


def _count_new_user_msgs_since_last_review(name: str, current_history: list) -> float:
    """Count the user msgs in history since the last review cutoff.

    White review (fingerprint=None) → treated as plenty, allowed through.
    Fingerprint not found in current (compressed / cleared) → likewise treated as
    plenty, allowed through (should re-review ASAP to rebuild the fingerprint).
    """
    from memory.recent import _find_fingerprint_position
    fp = gates._maint_view(name).get('last_reviewed_cutoff_tail')
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
        if name in _retired_derived_task_names:
            return
        # Gate 1: in-flight
        existing = correction_tasks.get(name)
        if existing is not None and not existing.done():
            return
        # Gate 2: review_enabled
        if not await gates._ais_review_enabled():
            return
        # 拉 history（gate 3/5 + 后续做 snapshot 都需要）
        try:
            history, admission_generation = (
                await runtime.recent_history_manager.aget_recent_history(
                    name, include_admission=True,
                )
            )
        except Exception as e:
            logger.debug(f"[Review/spawn] {name}: 拉 history 失败: {e}")
            return
        # Gate 3: history 长度
        if len(history) < REVIEW_SKIP_HISTORY_LEN:
            return
        # Gate 4: min interval
        last_review = gates._maint_view(name).get('last_review_ts')
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
        view = gates._maint_view(name)
        exhaustion_attempts = view.get('review_output_exhaustion_attempts', 0) or 0
        exhaustion_blocked = bool(view.get('review_output_exhaustion_blocked'))
        if (
            exhaustion_blocked
            or exhaustion_attempts >= MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS
        ):
            failed_min_tokens = view.get('review_output_exhaustion_min_context_tokens')
            try:
                failed_min_tokens = int(failed_min_tokens or 0)
            except (TypeError, ValueError):
                failed_min_tokens = 0
            # token 计数是 async（可能走 tiktoken），必须留在临界区外算完再进 mutator。
            current_tokens = await review_context_token_count(history)
            if failed_min_tokens > 0 and current_tokens >= failed_min_tokens:
                logger.debug(
                    f"[Review/spawn] {name}: 输出耗尽断路器已开启 "
                    f"(连续 {exhaustion_attempts} 次，context={current_tokens} >= "
                    f"失败最小值 {failed_min_tokens})，跳过本轮"
                )
                return
            cleared = await gates._amutate_maint_state(
                name,
                functools.partial(
                    _mutate_clear_output_exhaustion,
                    seen_attempts=exhaustion_attempts,
                    seen_min_tokens=failed_min_tokens,
                ),
            )
            if not cleared:
                # 锁内复查发现断路器已经被换成更新的一份（在 await token 计数期间又失败
                # 了一次）。恢复判定已经过期，本轮必须跟着放弃 —— 只是不清零却继续往下
                # spawn 的话，等于拿一个过期的判定绕过了刚 armed 的断路器。
                logger.debug(
                    f"[Review/spawn] {name}: 恢复判定已过期"
                    f"（断路器在 token 计数期间被并发写者更新），跳过本轮"
                )
                return
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
        # Gate 6a 可能刚落过盘，重新取一次 view（旧的可能还是空角色的 _EMPTY 视图）。
        view = gates._maint_view(name)
        fail_attempts = view.get('review_fail_attempts', 0) or 0
        if fail_attempts >= MEMORY_LIVENESS_MAX_ATTEMPTS:
            cur_fp = build_review_fingerprint(history)
            # dead-letter 判定本身也进临界区：判定和复位是同一次 RMW，别的写者不可能
            # 在「读到输入已变」和「把计数清零」之间插进来。
            decision = await gates._amutate_maint_state(
                name,
                functools.partial(
                    _mutate_reset_review_fail_backoff, cur_fp, MEMORY_LIVENESS_MAX_ATTEMPTS,
                ),
            )
            if decision == 'dead_letter':
                logger.debug(
                    f"[Review/spawn] {name}: 失败退避 dead-letter "
                    f"(连续失败 {fail_attempts} 次 ≥ {MEMORY_LIVENESS_MAX_ATTEMPTS} "
                    f"且输入未变)，跳过本轮"
                )
                return
        # 全过 → spawn
        logger.info(f"[Review/spawn] {name}: 触发 review (history_len={len(history)})")
        cancel_event = asyncio.Event()
        correction_cancel_flags[name] = cancel_event
        snapshot = list(history)  # 浅拷贝即可，消息对象不可变
        # 把 cancel_event 显式传给后台 task（不再依靠 finally 时再从 dict 拿），
        # 这样 task 自己持有的 event 引用不会被并发的新 spawn 覆盖。
        task = asyncio.create_task(
            _run_review_in_background(
                name, snapshot, cancel_event, admission_generation,
            )
        )
        correction_tasks[name] = task


def _mutate_reset_review_fail_backoff(cur_fp, max_attempts: int, state: dict) -> tuple[bool, str]:
    """Mutator: re-check Gate 6b under lock and expire the backoff if the input changed.

    Returns ``'dead_letter'`` (caller must skip this spawn) or ``'proceed'``.
    """
    # 配置常量由调用方在锁外取好传进来：mutator 跑在临界区里，不该在里面碰 import
    # machinery（本仓库这些 config import 都是函数内 lazy import）。
    attempts = state.get('review_fail_attempts', 0) or 0
    if attempts < max_attempts:
        # 锁外快筛之后另一个写者已经把计数清了（如成功的 review）→ 直接放行。
        return False, 'proceed'
    if state.get('review_fail_fp') == cur_fp:
        return False, 'dead_letter'
    state['review_fail_attempts'] = 0
    state['review_fail_fp'] = None
    return True, 'proceed'


def _mutate_record_review_failure(
    cur_fp, admission_generation, state: dict,
) -> tuple[bool, int | None]:
    """Mutator: bump the review failure counter and break the output-exhaustion streak."""
    if not _generation_is_current(admission_generation):
        return False, None
    if state.get('review_fail_fp') != cur_fp:
        state['review_fail_attempts'] = 0
    state['review_fail_attempts'] = (state.get('review_fail_attempts', 0) or 0) + 1
    state['review_fail_fp'] = cur_fp
    # 普通失败会中断"连续输出耗尽"序列，不能让两类失败交错累计后误开断路器。这一步
    # 必须和 bump 同在一个临界区、同一次落盘里：重构前它靠调用方先改内存、再靠本函数
    # 的 save 顺带持久化，拆成两次落盘会留下"清了但没写"的窗口。
    _clear_review_output_exhaustion_state(state)
    return True, state['review_fail_attempts']


async def _record_review_failure(
    lanlan_name: str, snapshot: list, admission_generation=None,
) -> int | None:
    """Record one review failure into the failure-backoff counter (used by Gate 6); returns the cumulative count.

    If the input fingerprint differs from the last failure record → zero the
    budget first, then +1, so each history tail gets its own independent budget of
    N attempts instead of accumulating across inputs (Codex P2).

    Also clears the output-exhaustion breaker in the same critical section: a
    generic failure breaks the "consecutive output exhaustion" streak, and the two
    updates must land in one write.
    """
    from memory.recent import build_review_fingerprint
    cur_fp = build_review_fingerprint(snapshot)
    return await gates._amutate_maint_state(
        lanlan_name,
        functools.partial(
            _mutate_record_review_failure, cur_fp, admission_generation,
        ),
    )


def _mutate_record_output_exhaustion(
    current_tokens: int, max_attempts: int, admission_generation, state: dict,
) -> tuple[bool, tuple[int, int] | None]:
    """Mutator: fold one output-limit failure into the breaker, tracking the minimum context."""
    if not _generation_is_current(admission_generation):
        return False, None
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
    state['review_output_exhaustion_blocked'] = attempts >= max_attempts
    return True, (attempts, minimum_tokens)


async def _record_review_output_exhaustion(
    lanlan_name: str, snapshot: list, admission_generation=None,
) -> tuple[int, int, int] | None:
    """Record one output-limit failure across growing/changed tail fingerprints."""
    from config import MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS
    from memory.recent import review_context_token_count

    # token 计数是 async，必须在进临界区前算完（mutator 是同步函数，写不出 await）。
    current_tokens = await review_context_token_count(snapshot)
    recorded = await gates._amutate_maint_state(
        lanlan_name,
        functools.partial(
            _mutate_record_output_exhaustion,
            current_tokens,
            MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS,
            admission_generation,
        ),
    )
    if recorded is None:
        return None
    attempts, minimum_tokens = recorded
    return attempts, current_tokens, minimum_tokens


# ── best-effort 后台压缩（主路径 compress 失败时兜底）─────────────────────
# 真根因：主路径压缩走 LLM 耗时数秒~数十秒，限流抖动 / 偶发失败 → #1629 跳过
# 保留完整历史、下轮重试。但若持续失败，历史一直压不掉、越积越多。这里在主路径
# 压缩失败时起一个受保护的一次性后台任务尽力压（基于快照、不被对话打断；压完用
# fingerprint 对齐合并回写）。主路径某轮成功 → cancel 在跑的后台。失败退避复用
# review 的 Gate 6 模式，防 summary 模型持续故障时每轮起一个注定失败的任务空烧。
compress_backup_tasks: dict[str, asyncio.Task] = {}
compress_backup_task_generations: dict[str, tuple[str, int] | None] = {}


def _generation_marker(admission_generation):
    return list(admission_generation) if admission_generation is not None else None


def _generation_is_current(admission_generation) -> bool:
    return (
        admission_generation is None
        or capture_recent_generation(admission_generation[0]) == admission_generation
    )


def _mutate_record_compress_backup_failure(
    cur_fp, admission_generation, state: dict,
) -> tuple[bool, int | None]:
    """Mutator: bump the backup-compression failure counter for this input fingerprint."""
    if not _generation_is_current(admission_generation):
        return False, None
    generation_marker = _generation_marker(admission_generation)
    if (
        state.get('compress_backup_fail_fp') != cur_fp
        or state.get('compress_backup_generation') != generation_marker
    ):
        state['compress_backup_fail_attempts'] = 0
    state['compress_backup_fail_attempts'] = (state.get('compress_backup_fail_attempts', 0) or 0) + 1
    state['compress_backup_fail_fp'] = cur_fp
    state['compress_backup_generation'] = generation_marker
    return True, state['compress_backup_fail_attempts']


async def _record_compress_backup_failure(
    lanlan_name: str, snapshot: list, admission_generation=None,
) -> int | None:
    """Record one backup-compression failure and return the current attempt count.

    A changed input fingerprint resets the counter so each backlog segment gets
    its own budget, matching the review-failure backoff shape.
    """
    if not _generation_is_current(admission_generation):
        return None
    from memory.recent import build_review_fingerprint
    cur_fp = build_review_fingerprint(snapshot)
    return await gates._amutate_maint_state(
        lanlan_name,
        functools.partial(
            _mutate_record_compress_backup_failure,
            cur_fp,
            admission_generation,
        ),
    )


def _mutate_clear_compress_backup_failure(state: dict) -> tuple[bool, None]:
    """Mutator: clear the backup-compression backoff, no-op when already clear."""
    if not (state.get('compress_backup_fail_attempts') or state.get('compress_backup_fail_fp')):
        return False, None
    state['compress_backup_fail_attempts'] = 0
    state['compress_backup_fail_fp'] = None
    state['compress_backup_generation'] = None
    return True, None


def _mutate_clear_compress_backup_failure_for_generation(
    admission_generation, state: dict,
) -> tuple[bool, None]:
    if not _generation_is_current(admission_generation):
        return False, None
    generation_marker = _generation_marker(admission_generation)
    state_marker = state.get('compress_backup_generation')
    if state_marker is not None and state_marker != generation_marker:
        return False, None
    return _mutate_clear_compress_backup_failure(state)


async def _clear_compress_backup_failure(
    lanlan_name: str, admission_generation=None,
) -> None:
    """Clear the backup-compression failure backoff counter."""
    # 锁外快筛，与 gates._aclear_review_clean 对偶。调用点 _on_compress_done 的
    # ok=True 分支每次主路径压缩成功都会走到这里，而它由 memory/recent.py 的
    # _notify_compress_done 触发 —— /renew、/settle 是持着 settle lock 调
    # update_history 的，该回调的 docstring 自己写明 "must not block"。退避计数
    # 绝大多数时候本来就是空的，没有这一层就等于每次压缩成功都在 settle lock 内
    # 白排一次 default executor。快筛读到的值可能过期，但无害——真正的判定在
    # _mutate_clear_compress_backup_failure 里锁内又做了一次，「假阴性」只会让本轮
    # 少写一次（下次压缩成功立刻会补上），不会写坏状态。
    if not _generation_is_current(admission_generation):
        return
    view = gates._maint_view(lanlan_name)
    if not (view.get('compress_backup_fail_attempts') or view.get('compress_backup_fail_fp')):
        return
    if admission_generation is None:
        await gates._amutate_maint_state(
            lanlan_name, _mutate_clear_compress_backup_failure,
        )
    else:
        await gates._amutate_maint_state(
            lanlan_name,
            functools.partial(
                _mutate_clear_compress_backup_failure_for_generation,
                admission_generation,
            ),
        )


def _mutate_reset_compress_backup_backoff(
    cur_fp, max_attempts: int, state: dict, *, admission_generation=None,
) -> tuple[bool, str]:
    """Mutator: re-check the compress-backup dead-letter under lock and expire it if the input changed.

    Returns ``'dead_letter'``, ``'proceed'``, or ``'stale'``.
    """
    if not _generation_is_current(admission_generation):
        return False, 'stale'
    generation_marker = _generation_marker(admission_generation)
    state_marker = state.get('compress_backup_generation')
    if admission_generation is not None and state_marker != generation_marker:
        return False, 'proceed'
    attempts = state.get('compress_backup_fail_attempts', 0) or 0
    if attempts < max_attempts:
        return False, 'proceed'
    if state.get('compress_backup_fail_fp') == cur_fp:
        return False, 'dead_letter'
    state['compress_backup_fail_attempts'] = 0
    state['compress_backup_fail_fp'] = None
    state['compress_backup_generation'] = generation_marker
    return True, 'proceed'


async def _run_backup_compress(
    lanlan_name: str, snapshot: list, detailed: bool, admission_generation=None,
):
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
        if not _generation_is_current(admission_generation):
            return
        if result is None:
            attempts = await _record_compress_backup_failure(
                lanlan_name, snapshot, admission_generation,
            )
            if attempts is None:
                return
            logger.info(f"[CompressBackup] {lanlan_name} 后台压缩失败，退避计数 → {attempts}")
            # best-effort 也没压成 → 实在不行才丢：若历史仍超硬上限，裁剪最旧未压缩
            # 原文兜底（锁内串行化写）。暂时性失败时后台会成功、走不到这里。
            async with runtime._get_settle_lock(lanlan_name):
                await runtime.recent_history_manager.enforce_hard_cap(
                    lanlan_name,
                    expected_generation=admission_generation,
                )
            return
        # 2) 合并写回（锁内，快）。merge_backup_memo 用 fingerprint 对齐，积压已被
        #    主路径压掉 / 被清空就返回 'moot' 丢弃（白做）。
        async with runtime._get_settle_lock(lanlan_name):
            status = await runtime.recent_history_manager.merge_backup_memo(
                lanlan_name,
                snapshot,
                result[0],
                expected_generation=admission_generation,
            )
        if status == 'failed':
            # 合并落盘失败 → 没真正写成功，bump 退避（不清），下次再试。
            attempts = await _record_compress_backup_failure(
                lanlan_name, snapshot, admission_generation,
            )
            if attempts is None:
                return
            logger.info(f"[CompressBackup] {lanlan_name} 后台压缩合并落盘失败，退避计数 → {attempts}")
            return
        # 'merged' 或 'moot' 都说明这段积压已处理 / 已过时，清退避计数。
        await _clear_compress_backup_failure(lanlan_name, admission_generation)
        logger.info(f"[CompressBackup] {lanlan_name} 后台压缩完成：{status}")
    except asyncio.CancelledError:
        logger.info(f"[CompressBackup] {lanlan_name} 后台压缩被取消（主路径已成功）")
    except Exception as e:
        logger.error(f"[CompressBackup] {lanlan_name} 后台压缩后处理出错: {e}")
    finally:
        cur = asyncio.current_task()
        if compress_backup_tasks.get(lanlan_name) is cur:
            compress_backup_tasks.pop(lanlan_name, None)
            compress_backup_task_generations.pop(lanlan_name, None)


async def _on_compress_done(
    lanlan_name: str,
    snapshot: list,
    ok: bool,
    detailed: bool,
    admission_generation=None,
):
    """Compression-finished callback for update_history (injected into recent.py).

    ok=True (main-path compression succeeded) → cancel any running backup task +
    clear the backoff counter; ok=False (main-path compression failed) → spawn a
    protected best-effort backup compression (unless one is in flight or the
    failure backoff blocks it).

    This callback only spawns / cancels tasks and never awaits the background
    LLM — it may be invoked while _get_settle_lock is held (/renew, /settle)
    and must not block."""
    if not _generation_is_current(admission_generation):
        return
    if lanlan_name in _retired_derived_task_names:
        return
    if ok:
        task = compress_backup_tasks.get(lanlan_name)
        if task is not None and not task.done():
            task.cancel()
        await _clear_compress_backup_failure(lanlan_name, admission_generation)
        return
    # ok=False：主路径压缩失败 → 起后台兜底
    if not snapshot:
        return
    existing = compress_backup_tasks.get(lanlan_name)
    if existing is not None and not existing.done():
        if compress_backup_task_generations.get(lanlan_name) == admission_generation:
            return  # in-flight：同一身份已有后台压缩在跑，不重复起
        existing.cancel()
    # 失败退避（Gate 6 模式）：连续失败 ≥ N 且输入未变 → dead-letter，不再起，
    # 防 summary 模型持续故障时每轮都起一个注定失败的后台任务空烧。
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    from memory.recent import build_review_fingerprint
    state_view = gates._maint_view(lanlan_name)
    state_marker = state_view.get('compress_backup_generation')
    generation_marker = _generation_marker(admission_generation)
    fail_attempts = 0
    if state_marker == generation_marker:
        fail_attempts = state_view.get('compress_backup_fail_attempts', 0) or 0
    if fail_attempts >= MEMORY_LIVENESS_MAX_ATTEMPTS:
        cur_fp = build_review_fingerprint(snapshot)
        # dead-letter 判定与复位是同一次 RMW（见 _mutate_reset_compress_backup_backoff）。
        # mutator 是同步函数，所以它既不可能在锁内 await、也不可能在锁内去取
        # _get_settle_lock —— 本回调可能已经在 /renew·/settle 的 settle lock 内被调，
        # 这条结构性约束正是它不会反向死锁的原因。
        decision = await gates._amutate_maint_state(
            lanlan_name,
            functools.partial(
                _mutate_reset_compress_backup_backoff, cur_fp, MEMORY_LIVENESS_MAX_ATTEMPTS,
                admission_generation=admission_generation,
            ),
        )
        if decision == 'stale':
            return
        if decision == 'dead_letter':
            logger.debug(
                f"[CompressBackup] {lanlan_name} 失败退避 dead-letter"
                f"（连续失败 {fail_attempts} 次且输入未变），跳过"
            )
            # dead-letter：后台已救不回 → 此时才裁剪兜底（实在不行才丢）。不 acquire
            # settle lock：本回调可能已在 /renew·/settle 的锁内被调（重入会死锁）；
            # enforce_hard_cap 是 best-effort 写。
            #
            # recent.json 的互斥不靠这里：它由 utils.recent_file 的 per-path
            # threading.Lock 保证，enforce_hard_cap 的落盘走那把锁。本回调是从
            # update_history 的 async 体里、CS-1 已返回而 CS-2 未进入的间隙被调的，
            # 那一刻文件锁必定未被本调用链持有，所以这里是首次获取。
            # ⚠️ 谁把 _notify_compress_done 挪进任何一个文件临界区，这行就是 worker
            # 线程上的无超时永久死锁（threading.Lock 不可重入）。
            await runtime.recent_history_manager.enforce_hard_cap(
                lanlan_name,
                expected_generation=admission_generation,
            )
            return
    # 上面的 dead-letter RMW / hard-cap 都可能 await；角色生命周期事务可能在
    # 这段窗口里退休旧名并排空 registry。spawn+register 必须回到与 release 共用
    # 的准入锁下复查，不能在 drain 完成后再把旧身份任务塞回 registry。
    async with _get_review_spawn_lock(lanlan_name):
        if (
            lanlan_name in _retired_derived_task_names
            or not _generation_is_current(admission_generation)
        ):
            return
        existing = compress_backup_tasks.get(lanlan_name)
        if existing is not None and not existing.done():
            if compress_backup_task_generations.get(lanlan_name) == admission_generation:
                return
            existing.cancel()
        task = runtime._spawn_background_task(
            _run_backup_compress(
                lanlan_name,
                list(snapshot),
                detailed,
                admission_generation,
            )
        )
        compress_backup_tasks[lanlan_name] = task
        compress_backup_task_generations[lanlan_name] = admission_generation
    logger.info(f"[CompressBackup] {lanlan_name} 主路径压缩失败，已起后台兜底压缩任务")


def _mutate_review_patched(
    fingerprint, admission_generation, state: dict,
) -> tuple[bool, bool]:
    """Mutator: record a successful review and clear every backoff it invalidates."""
    if not _generation_is_current(admission_generation):
        return False, False
    state['review_clean'] = True
    state['last_review_ts'] = datetime.now().isoformat()
    state['last_reviewed_cutoff_tail'] = fingerprint
    # 成功 → 清掉失败退避计数（Gate 6）
    state['review_fail_attempts'] = 0
    state['review_fail_fp'] = None
    _clear_review_output_exhaustion_state(state)
    return True, True


def _mutate_review_white(admission_generation, state: dict) -> tuple[bool, bool]:
    """Mutator: record a white review — drop the anchor, keep ``last_review_ts`` stale."""
    if not _generation_is_current(admission_generation):
        return False, False
    state['last_reviewed_cutoff_tail'] = None
    # 故意不更新 last_review_ts：让下轮 gate 4 用旧 ts（通常已过 30/60s）
    # 直接放行，配合 fingerprint=None 触发 gate 5 的 ∞ 通行 → 立即重 review。
    # 白 review 是 cutoff 失配（输入实际已变）而非失败，清退避计数允许立即重建锚点。
    state['review_fail_attempts'] = 0
    state['review_fail_fp'] = None
    _clear_review_output_exhaustion_state(state)
    return True, True


async def _run_review_in_background(
    lanlan_name: str,
    snapshot: list,
    cancel_event: asyncio.Event,
    admission_generation=None,
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
        if not _generation_is_current(admission_generation):
            logger.info(f"ℹ️ {lanlan_name} 的旧身份记忆整理在调用 LLM 前失效，已丢弃")
            return
        # 只把 review_history 调用本身包进内层 try：它抛异常才算"review 失败"，
        # 收口成 ('failed', None) 走下面统一的失败分支记一次退避。成功后的 result
        # 处理 / state 落盘异常**不**能被当成 review 失败（否则 patched/white 的
        # save 抖动会误判成失败、误触 Gate 6 dead-letter；'failed' 分支自己 save
        # 抛异常也会被重复记一次）——那类异常交给外层 except 纯兜底、不 bump。
        # 注：asyncio.CancelledError 是 BaseException，不被 except Exception 捕获，
        # 会正常冒泡到外层 CancelledError 分支。
        try:
            result = await runtime.recent_history_manager.review_history(
                lanlan_name,
                snapshot,
                cancel_event=cancel_event,
                expected_generation=admission_generation,
            )
        except Exception as e:
            logger.error(f"❌ {lanlan_name} 的 review_history 抛异常，按失败处理: {e}")
            result = ('failed', None)
        # 兼容意外的返回类型，统一解包
        if isinstance(result, tuple) and len(result) == 2:
            status, fingerprint = result
        else:
            status, fingerprint = ('failed', None)
        if not _generation_is_current(admission_generation):
            return

        if status == 'patched':
            applied = await gates._amutate_maint_state(
                lanlan_name,
                functools.partial(
                    _mutate_review_patched, fingerprint, admission_generation,
                ),
            )
            if applied:
                logger.info(f"✅ {lanlan_name} 的记忆整理任务完成")
        elif status == 'white':
            applied = await gates._amutate_maint_state(
                lanlan_name,
                functools.partial(_mutate_review_white, admission_generation),
            )
            if applied:
                logger.info(
                    f"⚠️ {lanlan_name} 白 review（cutoff 失配），fingerprint 清空、不刷 ts，允许立即重试"
                )
        elif cancel_event.is_set():
            # review_history 在 cancel_event 置位时也返回 ('failed', None)，但这是
            # 主动取消（cancel_correction：记忆编辑后立即生效）而非失败，不能计入
            # 失败退避——否则用户频繁编辑记忆会被误判成 poison。
            logger.info(f"ℹ️ {lanlan_name} 的记忆整理被取消（不计入失败退避）")
        elif status == 'output_exhausted':
            recorded = await _record_review_output_exhaustion(
                lanlan_name, snapshot, admission_generation,
            )
            if recorded is None:
                return
            attempts, context_tokens, minimum_tokens = recorded
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
            # 普通失败中断“连续输出耗尽”序列这一步已并进 _record_review_failure 的
            # mutator：清计数与 bump 必须落在同一次写里，不能拆成两次。
            attempts = await _record_review_failure(
                lanlan_name, snapshot, admission_generation,
            )
            if attempts is None:
                return
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
