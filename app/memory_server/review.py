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
import os
from collections import OrderedDict
from datetime import datetime

from . import gates, runtime
from ._shared import logger
from .gates import (
    LONG_IDLE_REVIEW_BYPASS_SECONDS,
    MIN_NEW_MSGS_FOR_REVIEW,
    REVIEW_MIN_INTERVAL,
    REVIEW_SKIP_HISTORY_LEN,
)
from memory.message_sources import is_theater_memory_message
from utils.recent_file import capture_recent_generation


# Global variables managing correction tasks.
correction_tasks = {}  # {lanlan_name: asyncio.Task}
correction_cancel_flags = {}  # {lanlan_name: asyncio.Event}


# Phase C: prevent spawn races; /process /renew /settle / IdleMaint share maybe_spawn_review,
# with an await gap between in-flight check and spawn when multiple entries check gates; use per-name locks
# to serialize gate + spawn and ensure at most one running review per character name.
_review_spawn_locks: dict[str, asyncio.Lock] = {}
_retired_derived_task_names: set[str] = set()
_publication_held_derived_task_names: set[str] = set()
_derived_task_admission_claims: dict[
    str, dict[str, tuple[bool, int | None]]
] = {}
_released_derived_task_claim_tokens: OrderedDict[tuple[str, str], None] = (
    OrderedDict()
)
_RELEASED_DERIVED_TASK_CLAIM_TOKEN_LIMIT = 4096


def is_character_publication_held(lanlan_name: str) -> bool:
    """Return whether a lifecycle transaction has fenced the old identity."""
    return lanlan_name in _publication_held_derived_task_names


def is_character_derived_task_claim_active(
    lanlan_name: str, claim_token: str,
) -> bool:
    """Return whether one lifecycle operation still owns its admission claim."""
    claims = _derived_task_admission_claims.get(lanlan_name)
    return bool(claims and claim_token in claims)


def _capture_character_admission_generation(lanlan_name: str) -> int | None:
    """Return the main-process identity generation for one character path."""
    config_manager = getattr(runtime, "_config_manager", None)
    memory_dir = getattr(config_manager, "memory_dir", None)
    if not memory_dir:
        return None
    path = os.path.join(memory_dir, lanlan_name, "recent.json")
    return capture_recent_generation(path)[1]


def _recompute_derived_task_admission_unlocked(lanlan_name: str) -> None:
    claims = _derived_task_admission_claims.get(lanlan_name)
    if claims:
        _retired_derived_task_names.add(lanlan_name)
        if any(hold for hold, _ in claims.values()):
            _publication_held_derived_task_names.add(lanlan_name)
        else:
            _publication_held_derived_task_names.discard(lanlan_name)
        return
    _derived_task_admission_claims.pop(lanlan_name, None)
    _publication_held_derived_task_names.discard(lanlan_name)
    _retired_derived_task_names.discard(lanlan_name)


def _remember_released_claim_token_unlocked(
    lanlan_name: str, claim_token: str,
) -> None:
    key = (lanlan_name, claim_token)
    _released_derived_task_claim_tokens[key] = None
    _released_derived_task_claim_tokens.move_to_end(key)
    while (
        len(_released_derived_task_claim_tokens)
        > _RELEASED_DERIVED_TASK_CLAIM_TOKEN_LIMIT
    ):
        _released_derived_task_claim_tokens.popitem(last=False)


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
    # Always write all three keys back (preserve pre-refactor memory shape), but dirty depends only on nondefault original values:
    # completing default keys needs no disk write; three missing keys on disk equal three defaults.
    changed = (
        bool(state.get('review_output_exhaustion_attempts'))
        or state.get('review_output_exhaustion_min_context_tokens') is not None
        or bool(state.get('review_output_exhaustion_blocked'))
        or state.get('review_output_exhaustion_generation') is not None
    )
    state['review_output_exhaustion_attempts'] = 0
    state['review_output_exhaustion_min_context_tokens'] = None
    state['review_output_exhaustion_blocked'] = False
    state['review_output_exhaustion_generation'] = None
    return changed


def _mutate_align_output_exhaustion_generation(
    admission_generation,
    state: dict,
) -> tuple[bool, bool]:
    """Discard an inherited breaker before gating a newly activated identity."""
    if not _generation_is_current(admission_generation):
        return False, False
    generation_marker = _generation_marker(admission_generation)
    if state.get('review_output_exhaustion_generation') == generation_marker:
        return False, True
    _clear_review_output_exhaustion_state(state)
    state['review_output_exhaustion_generation'] = generation_marker
    return True, True


def _mutate_clear_output_exhaustion(
    state: dict,
    *,
    seen_attempts: int,
    seen_min_tokens: int,
    seen_generation=None,
) -> tuple[bool, bool]:
    """Mutator: clear the output-exhaustion breaker, unless a newer failure was armed."""
    # Gate 6a recovery must be evaluated outside the lock (async token counting cannot run in a sync mutator), so
    # recheck its input under lock: another background review may record a **new** output-exhaustion failure
    # during that await (with new attempts / min_context_tokens). Unconditionally resetting would erase
    # the newly armed breaker and retry token spending on context already proven uncompressible.
    # Mirrors _mutate_reset_review_fail_backoff's locked recheck.
    try:
        current_min = int(state.get('review_output_exhaustion_min_context_tokens') or 0)
    except (TypeError, ValueError):
        current_min = 0
    current_attempts = state.get('review_output_exhaustion_attempts', 0) or 0
    if (
        current_min != seen_min_tokens
        or current_attempts != seen_attempts
        or state.get('review_output_exhaustion_generation') != seen_generation
    ):
        return False, False
    _clear_review_output_exhaustion_state(state)
    # value carries recovery validity to the caller (precisely the role of value in (dirty, value)).
    # dirty is always True: reaching here means the observed breaker still exists, so resetting changes state.
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
    claim_token: str | None = None,
    claim_generation: int | None = None,
) -> int | None:
    """Retire derived-task admission, then cancel and drain existing work."""
    async with _get_review_spawn_lock(lanlan_name):
        if claim_token:
            released_key = (lanlan_name, claim_token)
            if released_key in _released_derived_task_claim_tokens:
                _released_derived_task_claim_tokens.move_to_end(released_key)
                return None
            claims = _derived_task_admission_claims.setdefault(lanlan_name, {})
            previous = claims.get(claim_token)
            claims[claim_token] = (
                bool((previous and previous[0]) or hold_until_publication),
                previous[1] if previous else (
                    claim_generation
                    if claim_generation is not None
                    else _capture_character_admission_generation(lanlan_name)
                ),
            )
        _retired_derived_task_names.add(lanlan_name)
        if hold_until_publication:
            _publication_held_derived_task_names.add(lanlan_name)
        return await _cancel_character_derived_tasks_unlocked(lanlan_name)


async def release_character_derived_task_admission_claim(
    lanlan_name: str,
    claim_token: str,
) -> None:
    """Release only the admission claim owned by one lifecycle operation."""
    async with _get_review_spawn_lock(lanlan_name):
        _remember_released_claim_token_unlocked(lanlan_name, claim_token)
        claims = _derived_task_admission_claims.get(lanlan_name)
        if not claims or claim_token not in claims:
            return
        claims.pop(claim_token)
        _recompute_derived_task_admission_unlocked(lanlan_name)


async def resume_character_derived_task_admission(
    lanlan_name: str,
    published_generation: int | None = None,
) -> None:
    """Open a published identity without clearing same-generation claims."""
    async with _get_review_spawn_lock(lanlan_name):
        generation = (
            published_generation
            if published_generation is not None
            else _capture_character_admission_generation(lanlan_name)
        )
        claims = _derived_task_admission_claims.get(lanlan_name)
        if claims:
            for token, (_, claim_generation) in list(claims.items()):
                if generation is None or claim_generation != generation:
                    claims.pop(token, None)
        _recompute_derived_task_admission_unlocked(lanlan_name)


async def reconcile_character_derived_task_admission(
    active_names: set[str],
    *,
    resume_names: set[str] | None = None,
    resume_generations: dict[str, int] | None = None,
) -> None:
    """Re-enable a retired name only after reload observes a published identity."""
    explicit_resume = resume_names or set()
    for name in sorted(_retired_derived_task_names.intersection(active_names)):
        async with _get_review_spawn_lock(name):
            if name in explicit_resume:
                generation = (resume_generations or {}).get(name)
                if generation is None:
                    generation = _capture_character_admission_generation(name)
                claims = _derived_task_admission_claims.get(name)
                if claims:
                    for token, (_, claim_generation) in list(claims.items()):
                        if generation is None or claim_generation != generation:
                            claims.pop(token, None)
                _recompute_derived_task_admission_unlocked(name)
                continue
            # Token-owned holds survive unrelated reloads. Only their owner can
            # release them; a newly published identity uses explicit_resume.
            if _derived_task_admission_claims.get(name):
                continue
            if name in _publication_held_derived_task_names:
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
        # Fetch history (needed for gates 3/5 and later snapshot).
        try:
            history, admission_generation = (
                await runtime.recent_history_manager.aget_recent_history(
                    name, include_admission=True,
                )
            )
        except Exception as e:
            logger.debug(f"[Review/spawn] {name}: 拉 history 失败: {e}")
            return
        # Theater capsules bypass general review, but surrounding ordinary chat still forms one replayable timeline.
        history = [
            message
            for message in history
            if not is_theater_memory_message(message)
        ]
        # Gate 3: history length.
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
                # Malformed last_review_ts (legacy fields / manual edits / encoding errors) →
                # treat as never reviewed, allowing triggering to continue to gate 5 (new-message gate).
                # The next successful review overwrites it with valid ISO text.
                pass
        # Gate 5: enough new user messages (including long-idle bypass).
        new_msg_count = _count_new_user_msgs_since_last_review(name, history)
        if new_msg_count < MIN_NEW_MSGS_FOR_REVIEW:
            # Long-idle bypass: at least one unreviewed new message and global silence ≥ 30 min →
            # review the remaining partial batch too, otherwise users returning after an idle night find
            # the previous evening's sparse conversation permanently short of the console's triggering threshold.
            idle_secs = (datetime.now() - gates._last_activity_time).total_seconds()
            if not (new_msg_count >= 1 and idle_secs >= LONG_IDLE_REVIEW_BYPASS_SECONDS):
                return
            logger.info(
                f"[Review/spawn] {name}: 长挂机 bypass MIN_NEW_MSGS_FOR_REVIEW "
                f"(new_msgs={new_msg_count}, idle={idle_secs:.0f}s)"
            )
        # Gate 6a: output-token-exhaustion breaker. Counts across tail fingerprints, so new
        # messages/context growth cannot release it; recover only when compressed context tokens are strictly below
        # the minimum observed during failures.
        from config import MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS
        from memory.recent import review_context_token_count
        view = gates._maint_view(name)
        generation_marker = _generation_marker(admission_generation)
        if view.get('review_output_exhaustion_generation') != generation_marker:
            aligned = await gates._amutate_maint_state(
                name,
                functools.partial(
                    _mutate_align_output_exhaustion_generation,
                    admission_generation,
                ),
            )
            if not aligned:
                return
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
            # Token counting is async (possibly tiktoken); finish outside the critical section before entering the mutator.
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
                    seen_generation=generation_marker,
                ),
            )
            if not cleared:
                # Locked recheck finds the breaker replaced by a newer version (another failure during awaited
                # token counting). Recovery decision is stale; abandon this attempt. Merely retaining the count but continuing
                # to spawn would bypass a newly armed breaker using a stale decision.
                logger.debug(
                    f"[Review/spawn] {name}: 恢复判定已过期"
                    f"（断路器在 token 计数期间被并发写者更新），跳过本轮"
                )
                return
            logger.info(
                f"[Review/spawn] {name}: context 已缩短 "
                f"({current_tokens} < {failed_min_tokens})，恢复历史审阅"
            )

        # Gate 6b: general failure backoff (dead-letter). Consecutive review failures ≥
        # MEMORY_LIVENESS_MAX_ATTEMPTS with **unchanged input** (current last K history messages'
        # fingerprint matches the previous failure) → skip spawning instead of repeatedly wasting
        # 3×110s timeouts. Changed input (master sends a message, changing tail fingerprint) → treat as
        # new input, reset failure count and allow retry.
        # Must follow Gate 5: long-idle bypass would otherwise perpetuate retries while correction times out;
        # this gate must override it (user audit #1: confirmed overnight endless waste).
        from config import MEMORY_LIVENESS_MAX_ATTEMPTS
        from memory.recent import build_review_fingerprint
        # Gate 6a may just have persisted; refresh view (the previous one may be an empty character's _EMPTY view).
        view = gates._maint_view(name)
        fail_attempts = view.get('review_fail_attempts', 0) or 0
        if fail_attempts >= MEMORY_LIVENESS_MAX_ATTEMPTS:
            cur_fp = build_review_fingerprint(history)
            # Dead-letter check also enters the critical section: check and reset form one RMW, preventing another writer
            # from intervening between detecting changed input and clearing the count.
            decision = await gates._amutate_maint_state(
                name,
                functools.partial(
                    _mutate_reset_review_fail_backoff,
                    cur_fp,
                    MEMORY_LIVENESS_MAX_ATTEMPTS,
                    admission_generation=admission_generation,
                ),
            )
            if decision == 'stale':
                return
            if decision == 'dead_letter':
                logger.debug(
                    f"[Review/spawn] {name}: 失败退避 dead-letter "
                    f"(连续失败 {fail_attempts} 次 ≥ {MEMORY_LIVENESS_MAX_ATTEMPTS} "
                    f"且输入未变)，跳过本轮"
                )
                return
        # All gates pass → spawn.
        logger.info(f"[Review/spawn] {name}: 触发 review (history_len={len(history)})")
        cancel_event = asyncio.Event()
        correction_cancel_flags[name] = cancel_event
        snapshot = list(history)  # Shallow copy suffices; message objects are immutable.
        # Pass cancel_event explicitly to the background task rather than looking it up in dict during finally,
        # preserving the task's event reference against concurrent new spawns.
        task = asyncio.create_task(
            _run_review_in_background(
                name, snapshot, cancel_event, admission_generation,
            )
        )
        correction_tasks[name] = task


def _mutate_reset_review_fail_backoff(
    cur_fp,
    max_attempts: int,
    state: dict,
    *,
    admission_generation=None,
) -> tuple[bool, str]:
    """Mutator: re-check Gate 6b under lock and expire the backoff if the input changed.

    Returns ``'dead_letter'`` (caller must skip this spawn) or ``'proceed'``.
    """
    # Caller fetches config constants outside the lock: the mutator runs in the critical section and must not touch import
    # machinery there (config imports here are function-local lazy imports).
    if not _generation_is_current(admission_generation):
        return False, 'stale'
    generation_marker = _generation_marker(admission_generation)
    if state.get('review_fail_generation') != generation_marker:
        state['review_fail_attempts'] = 0
        state['review_fail_fp'] = None
        state['review_fail_generation'] = generation_marker
        return True, 'proceed'
    attempts = state.get('review_fail_attempts', 0) or 0
    if attempts < max_attempts:
        # Another writer already reset the count after the unlocked quick check (e.g. successful review) → allow directly.
        return False, 'proceed'
    if state.get('review_fail_fp') == cur_fp:
        return False, 'dead_letter'
    state['review_fail_attempts'] = 0
    state['review_fail_fp'] = None
    state['review_fail_generation'] = generation_marker
    return True, 'proceed'


def _mutate_record_review_failure(
    cur_fp, admission_generation, state: dict,
) -> tuple[bool, int | None]:
    """Mutator: bump the review failure counter and break the output-exhaustion streak."""
    if not _generation_is_current(admission_generation):
        return False, None
    generation_marker = _generation_marker(admission_generation)
    if (
        state.get('review_fail_fp') != cur_fp
        or state.get('review_fail_generation') != generation_marker
    ):
        state['review_fail_attempts'] = 0
    state['review_fail_attempts'] = (state.get('review_fail_attempts', 0) or 0) + 1
    state['review_fail_fp'] = cur_fp
    state['review_fail_generation'] = generation_marker
    # Ordinary failures interrupt consecutive output exhaustion, preventing mixed failure types from wrongly opening the breaker. This
    # must share the bump's critical section and persistence write: before refactoring, callers mutated memory and this function's
    # save persisted it incidentally; separate saves create a cleared-but-unpersisted interval.
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
    generation_marker = _generation_marker(admission_generation)
    if state.get('review_output_exhaustion_generation') != generation_marker:
        _clear_review_output_exhaustion_state(state)
        state['review_output_exhaustion_generation'] = generation_marker
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
    state['review_output_exhaustion_generation'] = generation_marker
    return True, (attempts, minimum_tokens)


async def _record_review_output_exhaustion(
    lanlan_name: str, snapshot: list, admission_generation=None,
) -> tuple[int, int, int] | None:
    """Record one output-limit failure across growing/changed tail fingerprints."""
    from config import MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS
    from memory.recent import review_context_token_count

    # Token counting is async: finish before entering the critical section (the mutator is synchronous and cannot await).
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


# ── Best-effort background compression (fallback when primary compression fails) ─────────────────────
# Root cause: primary LLM compression takes seconds to tens of seconds; rate-limit jitter / occasional failure → #1629 skips,
# preserves full history, and retries next round. Persistent failure leaves history uncompressed and growing. On primary
# failure, launch a protected one-shot background compression task (snapshot-based, uninterrupted by conversation; merge afterward
# using fingerprints). Primary success cancels any running backup. Failure backoff reuses
# review Gate 6 to avoid spawning doomed tasks every round during persistent summary-model failure.
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
    # Unlocked quick check, symmetric with gates._aclear_review_clean. _on_compress_done's
    # ok=True branch reaches here on every primary compression success, triggered by memory/recent.py's
    # _notify_compress_done; /renew and /settle call update_history while holding settle lock,
    # and this callback's docstring explicitly says 'must not block'. Backoff counts are
    # usually empty; without this check, every successful compression unnecessarily queues a default executor
    # inside settle lock. Quick-check values may be stale harmlessly: the real decision is repeated
    # under lock in _mutate_clear_compress_backup_failure; false negatives merely skip one write
    # until the next compression success, without corrupting state.
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
        # 1) Compress outside lock. Theater capsules bypass the ordinary summary model but remain in the full snapshot for commit alignment.
        preserved_theater = [
            message
            for message in snapshot
            if is_theater_memory_message(message)
        ]
        compression_input = [
            message
            for message in snapshot
            if not is_theater_memory_message(message)
        ]
        if not compression_input:
            return
        try:
            result = await runtime.recent_history_manager.compress_history(
                compression_input,
                lanlan_name,
                detailed,
            )
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
            # Best effort also failed: discard only as a last resort. If history still exceeds the hard cap, trim oldest uncompressed
            # originals as fallback (serialized writes under lock). Temporary failures recover in background and never reach here.
            async with runtime._get_settle_lock(lanlan_name):
                await runtime.recent_history_manager.enforce_hard_cap(
                    lanlan_name,
                    expected_generation=admission_generation,
                )
            return
        # 2) Merge back under lock (fast). merge_backup_memo aligns fingerprints; if the primary path already compressed
        #    or cleared the backlog, return 'moot' and discard the redundant work.
        async with runtime._get_settle_lock(lanlan_name):
            merge_kwargs = {"expected_generation": admission_generation}
            if preserved_theater:
                merge_kwargs["preserved_messages"] = preserved_theater
            status = await runtime.recent_history_manager.merge_backup_memo(
                lanlan_name,
                snapshot,
                result[0],
                **merge_kwargs,
            )
        if status == 'failed':
            # Merge persistence fails → not truly saved; bump backoff rather than clearing, and retry later.
            attempts = await _record_compress_backup_failure(
                lanlan_name, snapshot, admission_generation,
            )
            if attempts is None:
                return
            logger.info(f"[CompressBackup] {lanlan_name} 后台压缩合并落盘失败，退避计数 → {attempts}")
            return
        # 'merged' and 'moot' both mean backlog handled or obsolete; clear backoff counts.
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
    # ok=False: primary compression failed → launch background fallback.
    if not snapshot:
        return
    existing = compress_backup_tasks.get(lanlan_name)
    if existing is not None and not existing.done():
        if compress_backup_task_generations.get(lanlan_name) == admission_generation:
            return  # In-flight: same identity already has background compression; do not duplicate.
        existing.cancel()
    # Failure backoff (Gate 6): consecutive failures ≥ N with unchanged input → dead-letter; stop spawning,
    # preventing doomed background tasks every round during persistent summary-model failure.
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
        # Dead-letter check and reset are one RMW (see _mutate_reset_compress_backup_backoff).
        # The synchronous mutator can neither await nor acquire
        # _get_settle_lock while locked; this callback may already run inside /renew·/settle's settle lock,
        # and this structural constraint prevents reverse lock-order deadlocks.
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
            # Dead-letter: background recovery cannot help → only now trim as last resort. Do not acquire
            # settle lock: this callback may already run under /renew·/settle's lock (reentry would deadlock);
            # enforce_hard_cap writes best effort.
            #
            # recent.json mutual exclusion is elsewhere: utils.recent_file's per-path
            # threading.Lock protects enforce_hard_cap persistence. This callback runs from
            # update_history's async body between CS-1's return and CS-2 entry,
            # when this call chain cannot hold the file lock, so acquisition here is the first.
            # Warning: moving _notify_compress_done into either file critical section creates an unbounded permanent
            # worker-thread deadlock here (threading.Lock is not reentrant).
            await runtime.recent_history_manager.enforce_hard_cap(
                lanlan_name,
                expected_generation=admission_generation,
            )
            return
    # The dead-letter RMW / hard cap above may await; a character lifecycle transaction may
    # retire the old name and drain the registry meanwhile. Recheck spawn + register under
    # the admission lock shared with release; never reinsert old-identity tasks after draining completes.
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
    # Success → clear failure backoff counts (Gate 6).
    state['review_fail_attempts'] = 0
    state['review_fail_fp'] = None
    state['review_fail_generation'] = None
    _clear_review_output_exhaustion_state(state)
    return True, True


def _mutate_review_white(admission_generation, state: dict) -> tuple[bool, bool]:
    """Mutator: record a white review — drop the anchor, keep ``last_review_ts`` stale."""
    if not _generation_is_current(admission_generation):
        return False, False
    state['last_reviewed_cutoff_tail'] = None
    # Intentionally leave last_review_ts unchanged: next round Gate 4 uses the old timestamp (usually older than 30/60s)
    # and passes directly, while fingerprint=None gives Gate 5 infinite allowance → immediate re-review.
    # Wasted review means cutoff mismatch (input actually changed), not failure; clear backoff to rebuild anchors immediately.
    state['review_fail_attempts'] = 0
    state['review_fail_fp'] = None
    state['review_fail_generation'] = None
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
        # Wrap only review_history itself in the inner try: only its exceptions count as review failure,
        # normalized to ('failed', None) for one backoff increment in the unified failure branch. Successful-result
        # processing / state-save exceptions must **not** count as review failure (or patched/white
        # save instability would falsely trigger Gate 6 dead-letter; errors saving the 'failed' branch itself
        # would also increment twice). Leave these to the outer fallback except, without bumping.
        # Note: asyncio.CancelledError inherits BaseException, so except Exception does not catch it;
        # it propagates normally to the outer CancelledError branch.
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
        # Tolerate unexpected return types; unpack uniformly.
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
            # review_history also returns ('failed', None) when cancel_event is set, but this is
            # deliberate cancellation (cancel_correction: memory edits take immediate effect), not failure; exclude it
            # from failure backoff or frequent user memory edits would be misclassified as poison.
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
            # 'failed': persistent LLM failure / timeout / malformed output. Bump failure backoff and record
            # this input fingerprint so Gate 6 dead-letters unchanged input, avoiding
            # overnight waste from correction timeouts prolonged by long-idle bypass (user audit #1).
            # Ordinary failures interrupt consecutive output exhaustion within _record_review_failure's
            # mutator now: clearing and bumping must persist in one write, not two.
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
        # Pure fallback: only result-processing / state-persistence exceptions that are **not review failures**
        # reach here (review_history exceptions already normalized to 'failed' inside). These do **not**
        # count toward failure backoff, or successful-review save instability would wrongly trigger
        # Gate 6 dead-letter and suppress future reviews (Codex P2).
        logger.error(f"❌ {lanlan_name} 的记忆整理后处理出错（不计入失败退避）: {e}")
    finally:
        # Compare task/event identity before cleanup: if a concurrent spawn stored a new task /
        # new event, this task must not clear them.
        current_task = asyncio.current_task()
        if correction_tasks.get(lanlan_name) is current_task:
            correction_tasks.pop(lanlan_name, None)
        if correction_cancel_flags.get(lanlan_name) is cancel_event:
            correction_cancel_flags.pop(lanlan_name, None)
