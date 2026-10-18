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

"""Diagnostic observability: long-run health metric collection.

Background
----------
Occasional field reports say "after N.E.K.O has been running for two or three
days, CPU slowly climbs to 30%+". For this kind of multi-day, idle-triggered
leak, static code reading has a single-digit hit rate — we must capture
runtime counter curves **while it reproduces**. This router does two things:

1. ``GET /api/debug/health``: returns a snapshot of the key counters (asyncio
   task count, each lanlan core's conversation history length,
   agent_event_bus._ack_waiters size, proactive_chat_history size, process
   RSS, uptime).
2. Starts a background watchdog task on a 5-min cycle that writes the same
   snapshot into an in-memory ring buffer (keeping the last ~16 hours = 200
   entries). With ``NEKO_DEBUG_HEALTH_LOG=1`` it also persists to
   ``<user_data>/debug_health.jsonl`` so users can send the file back for
   curve plotting.

Design principles
-----------------
- **Zero intrusion**: every counter uses getattr / try-except; if this module
  breaks, main functionality is unaffected.
- **On by default**: the endpoint + in-memory ring buffer always run, at ~ms
  cost per pass; file logging is off by default, enabled explicitly via env.
  When a user later reports an issue, no new build is needed — the data is
  already there to collect.
- **No privacy capture**: snapshots only count sizes, never read contents;
  the jsonl contains no conversation text (per the CLAUDE.md rule that raw
  conversations may only be printed, never logged via logger).
"""
from __future__ import annotations

import asyncio
import gc
import json
import logging
import math
import os
import sys
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Module-level state.
# ---------------------------------------------------------------------------

_PROCESS_START_MONO = time.monotonic()
# Ring buffer: ~16 hours (5min x 200). Losing it on restart is acceptable for diagnostics rather than auditing.
_HEALTH_RING: deque[dict[str, Any]] = deque(maxlen=200)
_WATCHDOG_TASK: asyncio.Task | None = None
_WATCHDOG_INTERVAL_SECONDS = 5 * 60  # Five minutes.
# Deep fields (gc.get_objects() 45ms + Windows num_threads() 8ms) are C calls
# that hold the GIL, directly blocking the event loop for 50ms+ in watchdog. Reducing frequency
# to 30 minutes was rejected as perceptibly fatal; watchdog omits both entirely,
# collecting only on demand through /api/debug/health?deep=1. Ring/jsonl loses GC time series;
# users investigating memory can query repeatedly to build their own series.

# Reuse psutil.Process: cpu_percent(None) needs repeated calls on the same instance.
# The first returns zero to establish a baseline; later calls measure CPU since the previous call. New Process
# each time resets the baseline and always yields zero, effectively no sampling. None means uninitialized or psutil unavailable.
# Isolate two channels: cpu_percent shares its baseline within an instance. Sharing one Process between watchdog
# and endpoints makes every endpoint request reset the watchdog window's start,
# so its next sample no longer represents five minutes and may cover mere seconds.
# Maintain two independent Process instances: watchdog for periodic five-minute
# tasks, endpoint for on-demand HTTP. Their baselines cannot interfere.
# Other psutil calls (memory_info / num_handles / num_threads) are stateless instantaneous
# queries, so either instance behaves identically for those fields.
_PSUTIL_PROCESS_WATCHDOG: Any = None
_PSUTIL_PROCESS_ENDPOINT: Any = None
_PSUTIL_INIT_TRIED = False


# ---------------------------------------------------------------------------
# Snapshot collection.
# ---------------------------------------------------------------------------

def _get_psutil_process(channel: str = "watchdog") -> Any:
    """Lazily initialize + reuse ``psutil.Process``. One instance per channel.

    The first call tries to import psutil and primes both instances'
    ``cpu_percent`` (by convention the first call returns 0 to establish a
    baseline; only later readings are meaningful). On failure it permanently
    returns None and never retries — this avoids repeated import noise and
    makes psutil-less environments report cpu_percent/num_handles as null
    across the curve, clearly distinct from "an actual leak".

    ``channel="watchdog"`` / ``"endpoint"`` selects an independent baseline
    instance. Other stateless psutil calls (memory_info/num_handles etc.) also
    go through here; either channel works, defaulting to watchdog."""
    global _PSUTIL_PROCESS_WATCHDOG, _PSUTIL_PROCESS_ENDPOINT, _PSUTIL_INIT_TRIED
    if not _PSUTIL_INIT_TRIED:
        _PSUTIL_INIT_TRIED = True
        try:
            import psutil  # type: ignore
            w = psutil.Process()
            e = psutil.Process()
            # Prime both instances: discard the initial zero so later samples contain real values.
            w.cpu_percent(interval=None)
            e.cpu_percent(interval=None)
            _PSUTIL_PROCESS_WATCHDOG = w
            _PSUTIL_PROCESS_ENDPOINT = e
        except Exception:
            _PSUTIL_PROCESS_WATCHDOG = None
            _PSUTIL_PROCESS_ENDPOINT = None
    return _PSUTIL_PROCESS_ENDPOINT if channel == "endpoint" else _PSUTIL_PROCESS_WATCHDOG


def _safe_rss_mb(channel: str = "watchdog") -> float | None:
    """Read the process's **current** RSS (MB); only when psutil is available, otherwise None.

    Historically ``resource.getrusage(...).ru_maxrss`` was used as a fallback,
    but that is the **lifetime peak** — once up, it never comes down. Using it
    for leak trends would permanently misread a one-off memory spike as a leak,
    more misleading than not having the field at all. So we **prefer returning
    None** over falling back to ru_maxrss. Packaged builds ship psutil by
    default, and source mode installs it via ``uv sync``."""
    proc = _get_psutil_process(channel)
    if proc is None:
        return None
    try:
        return proc.memory_info().rss / (1024 * 1024)
    except Exception:
        # Process disappearance / lost permissions: return None without breaking diagnostics.
        return None


def _safe_psutil_extras(channel: str = "watchdog") -> dict[str, Any]:
    """Add the cheap psutil metrics: cpu%, Windows handles / POSIX fds.

    All calls take < 0.01 ms (measured on Windows). ``num_threads()`` is an
    ~8 ms syscall on Windows and was moved to ``_safe_psutil_heavy()`` on the
    deep tick.

    - cpu_percent: **the golden metric for the original problem** — the 31.9%
      in Task Manager is exactly this. ⚠️ Key normalization:
      ``proc.cpu_percent(None)`` uses UNIX semantics (one full core = 100;
      multi-core parallelism can exceed 100%), but Task Manager shows "percent
      of total CPU" (one core maxed out of 8 = 12.5%). To make the curve
      **directly match user screenshots**, divide by ``cpu_count``. Also
      report ``cpu_percent_raw`` with the UNIX raw value for "how many cores
      are busy" scenarios.
    - num_handles / num_fds: Windows handle / POSIX fd leaks. Most
      "restart fixes it" cases correlate with this. Try Windows num_handles
      first, then POSIX num_fds; None when neither is available.
    """
    out: dict[str, Any] = {
        "cpu_percent": None,       # Task Manager scale: percentage of total CPU.
        "cpu_percent_raw": None,   # Raw psutil percentage of one core, possibly over 100 on multicore systems.
        "cpu_count": None,
        "num_handles": None,
    }
    proc = _get_psutil_process(channel)
    if proc is None:
        return out
    try:
        import psutil  # type: ignore
        cpu_count = psutil.cpu_count() or 1
        raw = proc.cpu_percent(interval=None)
        out["cpu_percent_raw"] = raw
        out["cpu_count"] = cpu_count
        # Normalize to Task Manager scale: raw / cpu_count.
        out["cpu_percent"] = raw / cpu_count
    except Exception:
        # Deliberately swallow psutil subcall failures (cpu_count / cpu_percent), leaving None;
        # cpu_percent=null on the chart identifies psutil failure rather than an actual leak.
        pass
    # Windows uses num_handles, POSIX num_fds; wrong-platform psutil APIs raise AttributeError.
    try:
        out["num_handles"] = proc.num_handles()
    except Exception:
        try:
            out["num_handles"] = proc.num_fds()
        except Exception:
            # Deliberately swallow: if neither platform API exists (containers / rare OS), leave num_handles None.
            pass
    return out


def _safe_psutil_heavy(channel: str = "watchdog") -> dict[str, Any]:
    """Slow psutil calls — ``num_threads()`` is an 8 ms syscall on Windows.

    Deep-tick only; not run on every watchdog cycle."""
    out: dict[str, Any] = {"num_threads": None}
    proc = _get_psutil_process(channel)
    if proc is None:
        return out
    try:
        out["num_threads"] = proc.num_threads()
    except Exception:
        # Deliberately swallow unavailable num_threads, preserving nonintrusive semantics.
        pass
    return out


def _safe_asyncio_task_top(n: int = 10) -> list[list[Any]] | None:
    """Count current asyncio tasks by name; return the top N.

    When ``asyncio.all_tasks()`` only yields a number, a long-run leak shows
    "task count grew" without saying which kind — this distribution pinpoints
    it instantly (e.g. "memory_recall_xxx" climbing steadily). Returns
    list[[name, count], ...] rather than a dict — preserves ordering, JSON friendly."""
    try:
        c: Counter[str] = Counter()
        for t in asyncio.all_tasks():
            try:
                c[t.get_name()] += 1
            except Exception:
                c["<unnamed>"] += 1
        return [[name, cnt] for name, cnt in c.most_common(n)]
    except Exception:
        return None


def _safe_gc_object_top(n: int = 10) -> list[list[Any]] | None:
    """Count all GC-tracked objects by type; return the top N.

    A single ``gc.get_objects()`` pass takes ~tens of ms on a mid-sized Python
    heap; entirely acceptable every 5 min. This is the golden signal for
    "**which objects are growing**" — when the RSS number climbs without saying
    who, the type top tells you directly whether ``HumanMessage`` / ``AIMessage``
    / ``Future`` / ``Task`` / ``dict`` is increasing monotonically."""
    try:
        c: Counter[str] = Counter()
        for obj in gc.get_objects():
            c[type(obj).__name__] += 1
        return [[name, cnt] for name, cnt in c.most_common(n)]
    except Exception:
        return None


def _safe_tts_queue_sizes() -> dict[str, int]:
    """Current length of each lanlan core's ``tts_request_queue``.

    When TTS stalls / the network flaps, queue buildup shows up before CPU —
    an early signal that "the TTS pipeline is in trouble".
    core.tts_request_queue is a ``queue.Queue``; qsize is an estimate on
    Windows but good enough."""
    out: dict[str, int] = {}
    try:
        from main_routers.shared_state import get_session_manager
        session_manager = get_session_manager()
        for name in list(session_manager.keys()):
            try:
                core = session_manager.get(name)
                q = getattr(core, "tts_request_queue", None)
                if q is not None and hasattr(q, "qsize"):
                    out[name] = q.qsize()
            except Exception:
                continue
    except Exception:
        return out
    return out


def _safe_is_responding_map() -> dict[str, bool]:
    """Each lanlan's ``session._is_responding`` state.

    An abnormal distribution (e.g. every lanlan stuck at True) is a strong
    signal of a deadlock / a response handler dropping messages."""
    out: dict[str, bool] = {}
    try:
        from main_routers.shared_state import get_session_manager
        session_manager = get_session_manager()
        for name in list(session_manager.keys()):
            try:
                core = session_manager.get(name)
                session = getattr(core, "session", None)
                if session is None:
                    continue
                v = getattr(session, "_is_responding", None)
                if isinstance(v, bool):
                    out[name] = v
            except Exception:
                continue
    except Exception:
        return out
    return out


def _safe_conv_history_lengths() -> dict[str, int]:
    """Enumerate the _conversation_history length of every lanlan core.

    Any lanlan we cannot reach is skipped — shared_state may not be ready during early startup."""
    out: dict[str, int] = {}
    try:
        from main_routers.shared_state import get_session_manager
        session_manager = get_session_manager()
        # session_manager is a dictionary-like _RoleStateFieldView.
        for name in list(session_manager.keys()):
            try:
                core = session_manager.get(name)
                session = getattr(core, "session", None)
                history = getattr(session, "_conversation_history", None)
                if history is not None:
                    out[name] = len(history)
            except Exception:
                # One lanlan failure must not affect others: end_session / hot-swap may temporarily
                # leave core / session None; the next round naturally recovers.
                continue
    except Exception:
        # shared_state may not be ready early in startup; swallow nonintrusively.
        return out
    return out


def _safe_ack_waiters_size() -> int | None:
    try:
        from main_logic.agent_event_bus import _ack_waiters
        return len(_ack_waiters)
    except Exception:
        # Deliberately swallow: unloaded / renamed agent_event_bus modules degrade gracefully.
        return None


def _safe_proactive_history_size() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        from main_routers.system_router import _proactive_chat_history
        for name, dq in list(_proactive_chat_history.items()):
            try:
                out[name] = len(dq)
            except Exception:
                # Rare deque len failures: skip rather than aborting the whole round.
                continue
    except Exception:
        # Deliberately swallow: unloaded system_router / internal renames degrade gracefully.
        return out
    return out


def _safe_session_lifecycle():
    """Content-free ownership and suspended-task diagnostics, on demand only."""
    from main_routers.shared_state import get_session_manager

    def task_state(task):
        if task is None:
            return None
        chain = []
        coro = task.get_coro()
        for _ in range(20):
            frame = getattr(coro, 'cr_frame', None) or getattr(coro, 'gi_frame', None)
            if frame is not None:
                chain.append(f'{frame.f_code.co_name}:{frame.f_lineno}')
            coro = getattr(coro, 'cr_await', None) or getattr(coro, 'gi_yieldfrom', None)
            if coro is None:
                break
        return {'done': task.done(), 'cancelling': task.cancelling(), 'awaits': chain}

    result = {}
    for name, manager in get_session_manager().items():
        session = getattr(manager, 'session', None)
        arbiter = getattr(session, '_response_arbiter', None)
        current = getattr(arbiter, '_current', None)
        asr = getattr(manager, '_asr_runtime', None)
        dispatcher = getattr(asr, '_asr_transcript_dispatcher', None)
        result[name] = {
            'active': manager.is_active,
            'ready': manager.session_ready,
            'client': type(session).__name__,
            'listener': task_state(getattr(manager, 'message_handler_task', None)),
            'arbiter_worker': task_state(getattr(arbiter, '_worker', None)),
            'arbiter_source': getattr(current, 'source', None),
            'arbiter_idle': arbiter._idle.is_set() if arbiter else None,
            'arbiter_dispatch': arbiter._dispatch_allowed.is_set() if arbiter else None,
            'arbiter_available': getattr(arbiter, '_connection_available', None),
            'voice_pause_id': getattr(session, '_external_voice_turn_pause_id', None),
            'asr_turn_prepared': getattr(asr, '_asr_turn_prepared', None),
            'asr_dispatcher': task_state(getattr(dispatcher, '_worker', None)),
            'asr_pending_delivery': getattr(dispatcher, 'has_pending_delivery', None),
            'speech_output_total': getattr(manager, '_speech_output_total', None),
            'connections_live': sum(not r.closed for r in getattr(manager, '_connection_records', ())),
            'tts_live': sum(bool(r.thread and r.thread.is_alive()) for r in getattr(manager, '_tts_runtimes', ())),
        }
    return result


def _collect_snapshot(include_deep: bool = False, channel: str = "watchdog") -> dict[str, Any]:
    """Take a single snapshot. Every field is individually try-wrapped — one blowing up does not affect the others.

    Default ``include_deep=False`` — cheap-only sampling costs ~0.05ms of its
    own time; the watchdog can call it synchronously with virtually no impact
    on the event loop.

    ``include_deep=True`` runs the slow fields: ``gc.get_objects()`` 45ms
    (memory object type distribution — the golden signal for "which objects
    are growing") + Windows ``num_threads()`` 8ms (thread leak detection).
    Both are C calls that do not release the GIL and block the event loop
    50ms+, so the **watchdog never calls it** — reserved for on-demand
    endpoint triggering only.

    ``channel`` selects the psutil cpu_percent baseline: ``"watchdog"`` is
    dedicated to the 5-min periodic task, ``"endpoint"`` to on-demand HTTP;
    the two baselines are independent — a user hitting the endpoint cannot
    reset the watchdog's window start."""
    snap: dict[str, Any] = {
        "ts": time.time(),
        "uptime_sec": time.monotonic() - _PROCESS_START_MONO,
    }
    try:
        snap["asyncio_tasks"] = len(asyncio.all_tasks())
    except Exception:
        snap["asyncio_tasks"] = None
    snap["asyncio_task_top"] = _safe_asyncio_task_top()
    snap["rss_mb"] = _safe_rss_mb(channel)
    # Flatten psutil extras into the top-level dictionary for direct peer-key chart access.
    snap.update(_safe_psutil_extras(channel))
    snap["conv_history"] = _safe_conv_history_lengths()
    snap["tts_queue_size"] = _safe_tts_queue_sizes()
    snap["is_responding"] = _safe_is_responding_map()
    snap["ack_waiters"] = _safe_ack_waiters_size()
    snap["proactive_history"] = _safe_proactive_history_size()
    try:
        from main_logic.widget_mode_runtime import widget_mode_coordinator

        snap["widget_mode"] = widget_mode_coordinator.snapshot()
    except Exception as exc:
        snap["widget_mode"] = {"error": str(exc)[:160]}
    if include_deep:
        try:
            snap['session_lifecycle'] = _safe_session_lifecycle()
        except Exception as exc:
            snap['session_lifecycle'] = {'error_type': type(exc).__name__}
        # Deep fields block ~50ms but have a 30-minute interval, preserving long-running time-series plots.
        snap["gc_object_top"] = _safe_gc_object_top()
        snap.update(_safe_psutil_heavy(channel))
    return snap


# ---------------------------------------------------------------------------
# File persistence, disabled by default.
# ---------------------------------------------------------------------------

def _resolve_log_path() -> Path | None:
    """Return the jsonl log path; None when disabled.

    Enabled when env ``NEKO_DEBUG_HEALTH_LOG`` is truthy.
    Path: the user config directory from config_manager / ``debug_health.jsonl``;
    falls back to the sys.executable directory when config_manager is unavailable."""
    if os.environ.get("NEKO_DEBUG_HEALTH_LOG", "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    try:
        from main_routers.shared_state import get_config_manager
        cm = get_config_manager()
        config_dir = getattr(cm, "config_dir", None)
        if config_dir:
            return Path(config_dir) / "debug_health.jsonl"
    except Exception:
        # shared_state not ready / config_manager not injected: use the sys.argv[0]
        # fallback below. This is a diagnostic file; writing somewhere is better than nowhere.
        pass
    # Fallback: beside the launcher.
    try:
        return Path(sys.argv[0]).resolve().parent / "debug_health.jsonl"
    except Exception:
        return None


# Per-file limit: rotate to .1, replacing old .1; total storage is hard-capped around 20MB.
# Three lanlans plus merged client row is about 500B; 10MB holds ~21000 rows or ~73 days,
# then another 73 days in the new file, enough if users forget to disable the diagnostic environment variable.
_LOG_ROTATE_BYTES = 10 * 1024 * 1024


def _append_to_log(snap: dict[str, Any]) -> None:
    path = _resolve_log_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Rotate above threshold with os.replace to .1, atomic on Windows/POSIX and atomically replacing existing .1;
        # total storage = current + .1 <= 2x10MB.
        # Use path.name + ".1" rather than with_suffix('.1'), which replaces .jsonl
        # and produces debug_health.1, losing the JSONL suffix.
        try:
            if path.exists() and path.stat().st_size > _LOG_ROTATE_BYTES:
                os.replace(path, path.parent / (path.name + ".1"))
        except OSError as e:
            # Rotation failure must not break the main path: continue appending, allowing temporary growth
            # and retrying next tick.
            logger.debug("debug_health: rotate failed: %s", e)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snap, ensure_ascii=False) + "\n")
    except Exception as e:
        # Never propagate diagnostic-file write failures into the main application.
        logger.debug("debug_health: append jsonl failed: %s", e)


# ---------------------------------------------------------------------------
# Background watchdog task.
# ---------------------------------------------------------------------------

def _absorb_recent_client_payload(server_snap: dict[str, Any]) -> None:
    """On a server tick, look back and absorb the most recent client-only entry (if within the window).

    Timing constraints: debug-health.js first POSTs at t=30s, the watchdog
    first ticks at t=60s, and both then run on a 5-min cadence — so a client
    POST usually lands ~30s **before** the "next" server tick. The client POST
    side appends a client-only entry as a stash; the server tick absorbs it
    here: merge the stashed client payload into the current server snapshot
    and pop the stashed entry off the ring (so the ring's retention is not
    halved).

    Multiple client-only entries: a single cycle may see several client POSTs
    (multiple tabs / beforeunload resend + periodic reporting). The while loop
    pops all trailing consecutive client-only entries to avoid leaving
    orphans; only the **latest** payload is merged into the server snapshot —
    latest = last to arrive = closest to the server tick, highest diagnostic
    value.

    If the client side is not enabled (user did not set localStorage), this
    does nothing; the ring stays all server-only entries, ~200 entries ≈ 16
    hours, unchanged."""
    absorbed_client: dict[str, Any] | None = None
    server_ts = float(server_snap.get("ts") or 0)
    # Consume consecutive client-only tail entries in reverse, retaining the newest payload encountered first.
    while _HEALTH_RING:
        last = _HEALTH_RING[-1]
        # Client-only means no asyncio_tasks key; server snapshots always have it.
        if "asyncio_tasks" in last:
            break
        # Entries outside the absorption window belong to an older round: leave them and their payload untouched,
        # letting ring maxlen evict them naturally rather than disrupting historical order by popping.
        if server_ts - float(last.get("ts") or 0) > _WATCHDOG_INTERVAL_SECONDS:
            break
        if absorbed_client is None and last.get("client") is not None:
            absorbed_client = last["client"]
        _HEALTH_RING.pop()
    if absorbed_client is not None:
        server_snap["client"] = absorbed_client


async def _watchdog_loop() -> None:
    """5-min periodic sampling. Any single-round exception is swallowed and the loop continues — a multi-day run must not drop out over one failure.

    ⚠️ Key point: ``_collect_snapshot()`` is synchronous; its
    ``gc.get_objects()`` scans 280k+ objects on N.E.K.O's actual heap taking
    ~55 ms, with ``psutil`` / file IO adding a few more. A direct
    ``snap = _collect_snapshot()`` would **block the event loop for 50-100 ms**
    — during which every async operation (voice chunk processing, TTS
    streaming, WS ping/pong) is delayed. So collect must run in the thread
    pool via ``asyncio.to_thread`` while the event loop keeps working. The
    append/log operations (≤1ms) stay on the loop."""
    # Sleep initially to avoid cold-start noise from temporarily high startup asyncio-task counts.
    try:
        await asyncio.sleep(60)
    except asyncio.CancelledError:
        return
    while True:
        try:
            # Watchdog omits deep fields. Cheap snapshots take <0.05ms, so direct synchronous calls
            # are lighter than to_thread scheduling overhead. Deep fields
            # (GC / thread counts) move to on-demand endpoints; see _collect_snapshot
            # comments.
            snap = _collect_snapshot(include_deep=False)
            _absorb_recent_client_payload(snap)
            _HEALTH_RING.append(snap)
            # File I/O may take tens of milliseconds during os.replace rotation; offload to avoid blocking.
            await asyncio.to_thread(_append_to_log, snap)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("debug_health watchdog single tick error: %s", e)
        try:
            await asyncio.sleep(_WATCHDOG_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            return


def start_watchdog() -> None:
    """Called from main_server startup. Idempotent: repeated calls do not create a second task."""
    global _WATCHDOG_TASK
    if _WATCHDOG_TASK is not None and not _WATCHDOG_TASK.done():
        return
    try:
        _WATCHDOG_TASK = asyncio.create_task(_watchdog_loop(), name="debug_health_watchdog")
        logger.info("debug_health watchdog started (interval=%ds, log_file=%s)",
                    _WATCHDOG_INTERVAL_SECONDS, _resolve_log_path())
    except RuntimeError:
        # No running loop: startup should never reach here.
        logger.warning("debug_health: no running loop, watchdog NOT started")


# ---------------------------------------------------------------------------
# HTTP endpoints.
# ---------------------------------------------------------------------------

@router.get("/api/debug/health")
async def debug_health(deep: bool = False) -> dict[str, Any]:
    """Return the current snapshot + the recent ring buffer.

    The ring buffer means users need not wait for the next 5-min tick — a
    request at any moment returns the last 16 hours of curves, fresh on
    refresh.

    ``deep=true`` triggers the slow fields (``gc.get_objects()`` 45ms memory
    object type distribution + Windows ``num_threads()`` 8ms thread count).
    The watchdog never calls them — when chasing a memory leak, hit
    ``?deep=1`` once manually for current data; call repeatedly for a time
    series.

    Implementation note: the cheap path costs ~0.13 ms of its own time and is
    called synchronously; the deep path's 50 ms block is the price of a
    **user-initiated** action — they can just wait. No to_thread fallback for
    this case: saves a layer of thread scheduling overhead, keeps the code
    direct.

    Passes ``channel="endpoint"`` for an independent psutil cpu_percent
    baseline that does not disturb the watchdog's 5-min window."""
    current = _collect_snapshot(include_deep=deep, channel="endpoint")
    # Resolve log path once instead of calling _resolve_log_path() twice
    _log_path = _resolve_log_path()
    return {
        "current": current,
        "ring": list(_HEALTH_RING),
        "ring_capacity": _HEALTH_RING.maxlen,
        "watchdog_interval_sec": _WATCHDOG_INTERVAL_SECONDS,
        "log_path": str(_log_path) if _log_path else None,
    }


# Whitelist accepted client payload fields at the HTTP boundary for two reasons:
# (1) Enforce the counts-only protocol at the boundary, not by frontend convention, or arbitrary callers
#     could write large objects / sensitive fields into ring/jsonl.
# (2) Bound per-call payloads so abnormal calls cannot inflate long-running JSONL files to gigabytes.
# Keep names synchronized with static/debug-health.js collectSnapshot(); update both sides
# for new fields. Non-whitelisted fields are silently dropped.
_CLIENT_NUMERIC_FIELDS = frozenset({
    "ts", "live_intervals", "live_timeouts", "raf_fps_60s",
    "dom_nodes", "js_heap_mb", "ws_state",
    "proactive_backoff_level", "agent_task_map_size",
    # Added, matching debug-health.js collectSnapshot.
    "live_object_urls", "error_count", "unhandled_rejection_count",
})
_CLIENT_BOOL_FIELDS = frozenset({"proactive_running", "is_recording"})
_CLIENT_STRING_FIELDS_WITH_CAP = {"location": 128}


def _sanitize_client_payload(raw: Any) -> dict[str, Any]:
    """Trim the client payload to whitelisted fields; unexpected types / over-long strings are dropped or truncated."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k in _CLIENT_NUMERIC_FIELDS:
            if v is None:
                out[k] = v
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                # Reject NaN / Infinity / 1e10000: standard json emits nonstandard NaN /
                # Infinity literals by default, breaking frontend JSON.parse / third-party
                # JSONL tools. try/except also guards huge integers such as 10**500:
                # math.isfinite converts integers to floats internally and may raise OverflowError.
                # Such numbers violate counts-only semantics anyway; discard them too.
                try:
                    if math.isfinite(v):
                        out[k] = v
                except (OverflowError, TypeError):
                    # Deliberately discard huge integers / abnormal numeric subtypes that isfinite cannot handle.
                    # They do not belong in counts-only data; discard without affecting other fields.
                    pass
        elif k in _CLIENT_BOOL_FIELDS:
            if isinstance(v, bool):
                out[k] = v
        elif k in _CLIENT_STRING_FIELDS_WITH_CAP:
            if isinstance(v, str):
                cap = _CLIENT_STRING_FIELDS_WITH_CAP[k]
                out[k] = v[:cap]
        # Silently drop remaining fields, enforcing the counts-only contract here.
    return out


@router.post("/api/debug/health/client")
async def debug_health_client(payload: dict[str, Any]) -> dict[str, Any]:
    """Browser-side snapshot POSTed by the frontend ``debug-health.js``.

    Flow:
    - Boundary whitelist trimming (``_sanitize_client_payload``) enforces the
      "counts only" contract.
    - Append a client-only entry to the in-memory ring as a stash — do **not**
      write jsonl immediately, so the next server tick's absorption cannot
      produce a "stash + merged" double line polluting the jsonl timeline.
    - Writing jsonl is entirely the watchdog's responsibility: a successful
      absorption yields a single merged line; a failed one (outside the window
      or server not started) is naturally dropped by the ring.

    Mismatch fix history: an earlier attempt searched backwards for a server
    entry to merge into here; codex pointed out it would bind the client
    sample to a server snapshot up to 4.5 min old, skewing the timeline by
    270s. Hence the "server tick absorbs backwards" design — see
    ``_absorb_recent_client_payload``."""
    try:
        sanitized = _sanitize_client_payload(payload)
        entry = {"ts": time.time(), "client": sanitized}
        _HEALTH_RING.append(entry)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
