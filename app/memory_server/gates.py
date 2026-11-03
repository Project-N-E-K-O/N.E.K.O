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

"""Cross-loop gating state for the memory_server package.

Dependency leaf shared by every background loop and every session endpoint:
  - persisted maintenance state (``_maint_view`` to read,
    ``_amutate_maint_state`` to write, ``_aload_maint_state`` at startup)
  - hot-reloadable feature switches (``_ais_review_enabled`` /
    ``_ais_powerful_memory_enabled``)
  - idle detection (``_touch_activity`` / ``_is_idle``) and the loop
    scheduling constants (poll intervals, staggered initial delays)

``idle_maintenance_state.json`` is the one process-wide (not per-character)
file this package owns, and roughly ten call sites across request handlers and
background tasks update it. ``_amutate_maint_state`` is the ONLY write entry
point: it runs read → mutate → persist as a single critical section. Reads go
through ``_maint_view``, which hands back a read-only snapshot, so ``_maint_state``
itself must not be named outside this module (there is a guard test for that) —
see the block comment above the lock for the full contract.

``_maint_state`` and ``_last_activity_time`` are REBOUND in place (not just
mutated), so in-module code must access them as module globals rather than via a
from-import snapshot, which goes stale after the first ``_aload_maint_state`` /
``_touch_activity`` call. For the same reason tests must monkeypatch these names
(and the switch helpers) on THIS module, not on the package facade.
"""

import asyncio
import json
import os
import threading
from datetime import datetime
from types import MappingProxyType
from typing import Any, Callable, Mapping

from utils.config_manager import get_config_manager
# Module-level import, unlike other file_utils uses here: atomic_write_json runs inside
# _maint_state_lock, so local imports would put import machinery inside the critical section.
# file_utils is standard-library-only and config_manager already imports it globally, avoiding cycles.
from utils.file_utils import atomic_write_json

from ._shared import logger

_config_manager = get_config_manager()

# -- Idle maintenance --
_last_activity_time: datetime = datetime.now()            # Last conversation activity time.
IDLE_CHECK_INTERVAL = 40             # Idle-check polling interval in seconds.
IDLE_THRESHOLD = 10                  # Inactivity threshold matching the minimum proactive interval.
REVIEW_MIN_INTERVAL = 60             # Minimum review interval in seconds, combined with message gating.
REVIEW_SKIP_HISTORY_LEN = 8          # Skip reviews below this history count.
MIN_NEW_MSGS_FOR_REVIEW = 5          # Require at least N user messages since the last review cutoff before another round.
LONG_IDLE_REVIEW_BYPASS_SECONDS = 1800  # With at least 30 min inactivity and unreviewed messages, bypass message gating
                                        # to organize tails too short for ordinary batches.

# -- Stagger initial_delay to avoid simultaneous startup/interval work --
# Each loop first runs at startup plus its delay, then on its own interval.
# Archive sweeps have the longest 3600 s interval, but many users exit within an hour,
# requiring a much earlier first run. Rebuttal/auto-promote share 300 s intervals but stagger by 60 s.
# Short IdleMaint/Signal intervals need only startup cloudsave/outbox/migration breathing room.
# EmbeddingWarmupWorker has its own 30 s gate, outside this table.
_INITIAL_DELAY_IDLE_MAINT = 20       # First IdleMaint; old frequent 10 s startup is retired.
_INITIAL_DELAY_SIGNAL = 60           # First Signal extraction, formerly 40 s.
_INITIAL_DELAY_REBUTTAL = 100        # First Rebuttal, formerly 300 s.
_INITIAL_DELAY_AUTO_PROMOTE = 150    # First Auto-promote, formerly 300 s, staggered 50 s from rebuttal.
_INITIAL_DELAY_ARCHIVE = 250         # First archive sweep, advanced greatly from 3600 s for short-session users.
_INITIAL_DELAY_PERSONA_REFINE = 400  # First PERSONA_REFINE, staggered 100 s from reflection refinement.
_INITIAL_DELAY_REFLECTION_REFINE = 500  # First REFLECTION_REFINE.
_INITIAL_DELAY_SCOPED_REFINE = 600   # First SCOPED_REFINE, lightweight group-memory refinement extending the staggered schedule.
_INITIAL_DELAY_REFLECTION_SYNTHESIS = 200  # First REFLECTION_SYNTHESIS, avoiding AUTO_PROMOTE 150 / ARCHIVE 250 while allowing SignalLoop 60 s and one or two actual fact extractions.

# -- Persistent maintenance state retains review_clean across restarts --
_maint_state: dict[str, dict] = {}   # {character_name: {"review_clean": bool, "last_review_ts": str}}.

# -- Sole locked mutation entry for maintenance state --
# This package's only process-wide, non-character-partitioned file has writers across /cache /process /renew
# /settle handlers, background review tasks, and compression fallbacks. Previously all performed unlocked
# read-module-dict -> mutate -> overwrite persistence, causing two failures:
#   (a) Concurrent coroutine to_thread calls make workers os.replace the same file simultaneously,
#       directly causing PermissionError/WinError 5 on Windows.
#   (b) More seriously, json.dumps times differ and os.replace completion can reverse their
#       order, letting stale content overwrite just-written backoff counters
#       or review_clean flags.
#
# Why threading.Lock with the whole critical section in one to_thread, not asyncio.Lock:
# 1) Existing correct disk-write pattern: memory/event_log.py record_and_save explicitly places
#    five steps under one per-character threading.Lock and one asyncio.to_thread,
#    never holding locks across awaits. This package's asyncio locks, _settle_locks /
#    _review_spawn_locks, protect in-loop orchestration, not files.
# 2) Module-level asyncio.Lock binds to the contended event loop; pytest uses
#    asyncio_mode=auto with function-scoped loops, so a second contended case raises
#    RuntimeError for another loop and leaves internal lock state held after failure.
# 3) threading.Lock also protects json.dumps (atomic_write_json serializes before
#    writing). With asyncio.Lock, worker-side dumps can still race residual unlocked
#    mutations and raise dictionary-changed-size-during-iteration errors.
#
# Cancellation safety: the whole section runs inside one asyncio.to_thread, with acquire/release
# entirely within the worker. Canceling the awaiting coroutine cannot stop an already dispatched thread;
# it completes persistence and releases locks, preventing lost writes/permanently held locks.
# Thus no shield/cleanup wait is needed: this module never holds locks across await.
_maint_state_lock = threading.Lock()

# Mutator contract: synchronous function receiving this character's mutable subdict and returning (dirty, value).
#   - dirty=False skips persistence; callers should not blindly write, because _aclear_review_clean runs every
#     /cache /process and unchanged False flags should not pay fsync/replace costs.
#   - Return value unchanged to expose decisions made under lock, e.g. dead-letter.
# Deliberate synchrony makes await or async lock acquisition inside mutation impossible,
# structurally limiting critical sections to pure CPU plus one persistence operation.
# Avoid RLock: nested inner RMW could persist outer transactions' partially modified state.
MaintStateMutator = Callable[[dict], tuple[bool, Any]]

_EMPTY_MAINT_VIEW: Mapping[str, Any] = MappingProxyType({})


def _maint_state_path() -> str:
    return os.path.join(str(_config_manager.memory_dir), 'idle_maintenance_state.json')


def _maint_view(lanlan_name: str) -> Mapping[str, Any]:
    """Read-only SNAPSHOT of one character's maintenance state.

    The proxy wraps a copy, not the live sub-dict, so the result is safe to
    iterate and to hold across an ``await``: a worker thread running a mutator
    can ``setdefault`` new keys at any moment, and iterating a live mapping
    while that happens raises "dictionary changed size during iteration".
    Copying is one C-level ``dict()`` call over a handful of scalars, so it is
    cheaper than the alternative of taking the lock. The per-character sub-dict
    only ever holds scalars, hence a shallow copy is a genuine seal: callers
    cannot reach a mutable handle and must go through ``_amutate_maint_state``.

    Deliberately lock-free. Every field read is advisory — the mutator
    re-checks whatever it depends on inside the critical section — and blocking
    the event loop on another writer's fsync would cost far more than reading a
    value that is a few milliseconds stale.
    """
    entry = _maint_state.get(lanlan_name)
    if entry is None:
        return _EMPTY_MAINT_VIEW
    return MappingProxyType(dict(entry))


def _persist_maint_state_locked() -> None:
    """Write the whole maintenance map to disk. Caller MUST hold ``_maint_state_lock``."""
    atomic_write_json(_maint_state_path(), _maint_state,
                      indent=2, ensure_ascii=False)


def _rebind_maint_state_locked(data: dict[str, dict]) -> None:
    """Replace the whole maintenance map. Acquires ``_maint_state_lock`` itself."""
    global _maint_state
    with _maint_state_lock:
        _maint_state = data


def _mutate_maint_state_locked(lanlan_name: str, mutator: MaintStateMutator) -> Any:
    """Read → mutate → persist as one critical section. Run me inside a worker thread."""
    with _maint_state_lock:
        # Fetch subdicts under lock: _aload_maint_state globally rebinds _maint_state,
        # orphaning references fetched outside; mutating them neither persists
        # nor appears in subsequent reads.
        state = _maint_state.setdefault(lanlan_name, {})
        dirty, value = mutator(state)
        if not dirty:
            return value
        try:
            _persist_maint_state_locked()
        except Exception as e:
            # Preserve old _asave_maint_state behavior: warn on persistence failures, retain memory state, never
            # raise. /cache /process /renew /settle invoke _aclear_review_clean inside
            # try blocks; exposing disk glitches turns whole requests into
            # {"status":"error"}, stalling cross_server.last_synced_index.
            logger.warning(f"[IdleMaint] 维护状态保存失败: {e}")
        return value


async def _amutate_maint_state(lanlan_name: str, mutator: MaintStateMutator) -> Any:
    """The single write entry point for ``idle_maintenance_state.json``.

    Hands the whole read-mutate-persist sequence to one worker thread so that
    concurrent writers cannot interleave (see the block comment above
    ``_maint_state_lock`` for why the lock is a ``threading.Lock`` and why the
    mutator must be synchronous).
    """
    return await asyncio.to_thread(_mutate_maint_state_locked, lanlan_name, mutator)


async def _aload_maint_state() -> None:
    """Load maintenance state from disk at startup."""
    from utils.file_utils import read_json_async
    path = _maint_state_path()
    if not await asyncio.to_thread(os.path.exists, path):
        await asyncio.to_thread(_rebind_maint_state_locked, {})
        return
    try:
        data = await read_json_async(path)
        if isinstance(data, dict):
            await asyncio.to_thread(_rebind_maint_state_locked, data)
            logger.debug(f"[IdleMaint] 已加载维护状态: {len(data)} 个角色")
            return
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        # Catch UnicodeDecodeError separately: read_json uses UTF-8 open plus json.load;
        # external GBK/UTF-16 writes or interrupted multibyte sequences raise it,
        # a ValueError subtype neither JSONDecodeError nor OSError. Otherwise it escapes
        # to ensure_memory_server_runtime_initialized's sole unguarded await,
        # aborting all memory_server runtime initialization.
        logger.warning(f"[IdleMaint] 维护状态文件加载失败: {e}")
    await asyncio.to_thread(_rebind_maint_state_locked, {})


def _is_review_clean(lanlan_name: str) -> bool:
    """Check whether the character is in the review_clean state (reviewed and no new conversation)."""
    return _maint_view(lanlan_name).get('review_clean', False)


def _mutate_clear_review_clean(state: dict) -> tuple[bool, None]:
    """Mutator: drop the review_clean flag, no-op when it is already clear."""
    if not state.get('review_clean'):
        return False, None
    state['review_clean'] = False
    return True, None


async def _aclear_review_clean(lanlan_name: str) -> None:
    """Clear the review_clean flag when a new human message arrives."""
    # Unlocked fast filter: /cache and /process call each turn while flags usually remain False.
    # Without this, every user message needlessly switches threads. Stale fast reads are
    # harmless because _mutate_clear_review_clean rechecks under lock;
    # false negatives merely skip this write, corrected by the next message, without corrupting state.
    if not _is_review_clean(lanlan_name):
        return
    await _amutate_maint_state(lanlan_name, _mutate_clear_review_clean)


async def _ais_review_enabled() -> bool:
    """Check whether correction/review is enabled in config (async IO)."""
    from utils.file_utils import read_json_async
    try:
        config_path = str(_config_manager.get_runtime_config_path('core_config.json'))
        if not await asyncio.to_thread(os.path.exists, config_path):
            return True
        config_data = await read_json_async(config_path)
        if isinstance(config_data, dict) and not config_data.get('recent_memory_auto_review', True):
            return False
    except Exception as e:
        logger.debug(f"[IdleMaint] 读取 review 开关配置失败，默认启用: {e}")
    return True


async def _ais_powerful_memory_enabled() -> bool:
    """Check whether "powerful memory" is enabled — controls all the new LLM paths introduced by the evidence RFC.

    When off, only the pre-RFC base pipeline remains (Stage-1 fact extraction /
    reflection synthesize / recent compress+review / recall reranker /
    check_feedback for proactive-chat responses) + the time-driven promote
    fallback. Turning it off saves ~40-50% tokens.

    Persisted as the ``powerful_memory_enabled`` field in ``core_config.json``;
    missing defaults to True (for compatibility). Re-opens read_json_async on each
    use, no caching — same hot-reload as ``_ais_review_enabled``, takes effect
    without a restart.
    """
    from utils.file_utils import read_json_async
    try:
        config_path = str(_config_manager.get_runtime_config_path('core_config.json'))
        if not await asyncio.to_thread(os.path.exists, config_path):
            return True
        config_data = await read_json_async(config_path)
        if isinstance(config_data, dict) and not config_data.get('powerful_memory_enabled', True):
            return False
    except Exception as e:
        logger.debug(f"[Memory] 读取强力记忆开关配置失败，默认启用: {e}")
    return True


def _touch_activity() -> None:
    """Record one conversation activity, refreshing the idle timer."""
    global _last_activity_time
    _last_activity_time = datetime.now()


def _is_idle() -> bool:
    """Whether the system is currently idle (more than the threshold since the last activity)."""
    return (datetime.now() - _last_activity_time).total_seconds() >= IDLE_THRESHOLD
