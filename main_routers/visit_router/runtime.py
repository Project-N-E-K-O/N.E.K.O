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

"""The visit runtime: one :class:`VisitRuntime` per character out visiting or hosting (design §3.2, §3.6).

Lifecycle of one side (host and guest are symmetric except where noted):

1. :func:`start_visit` -- synchronous admission (route lock, persona gate,
   then the ``pending`` slot, then the remaining preconditions); the HTTP
   layer answers 202 right after it. The transport iframe is created by the
   page on ``visit_state_change{pending}``.
2. Capability gate ①② (``caps{stage:'preflight'}`` within
   ``VISIT_CAPS_PREFLIGHT_TIMEOUT_S``) → Servers credentials → takeover →
   ``credentials`` downlink → gate ③ (``caps{stage:'sdk'}`` within
   ``VISIT_CAPS_SDK_TIMEOUT_S``) → ``state{joined}`` (host: invite code out).
3. ``hello`` exchange and verification; the host's family accepts within
   ``VISIT_ACCEPT_TIMEOUT_S``; the only activation (isolated session, spool,
   ``VisitRoom``) runs before the host sends ``ready`` / after the guest got
   it; then ``started``.
4. Conversation (:mod:`runtime_talk`) and the receive pipeline
   (:mod:`runtime_rx`).
5. :meth:`VisitRuntime.request_finalize` flips the phase to ``ending``
   synchronously and runs the exit flow (§3.2.6 item 22): close the line in
   progress, close the data channel in the background (drain, ``leave``,
   resend window), hold callbacks, release the takeover, home-coming line,
   seal the upload, finalize the spool, digest / last summary in the
   background, debrief, hand the parked callbacks back.

Nothing here is mounted on the app or wired to the hot paths: PR-09b
includes the routers, calls :func:`visit_sweep_loop` / :func:`stop_all` and
connects ``websocket_router`` / ``turn.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import random
import secrets
import time
from collections.abc import Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from config.visit_settings import (
    VISIT_ACTIVATION_ALLOWANCE_S,
    VISIT_CAPS_PREFLIGHT_TIMEOUT_S,
    VISIT_CAPS_SDK_TIMEOUT_S,
    VISIT_CROP_DEFAULT,
    VISIT_ENABLED_DEFAULT,
    VISIT_ENDING_SOON_S,
    VISIT_IDLE_TIMEOUT_S,
    VISIT_INBOX_HANDOFF_ABS_MAX_S,
    VISIT_LEAVE_GAP_GRACE_S,
    VISIT_MAX_DURATION_S,
    VISIT_MEMORY_DEFAULT,
    VISIT_REPLY_GAP_S,
    VISIT_SELF_RECONNECT_S,
    VISIT_SHUTDOWN_BUDGET_S,
    VISIT_SPOOL_DIRNAME,
    VISIT_SWEEP_INTERVAL_S,
    VISIT_TIME_UP_WRAP_UP_S,
    VISIT_TRANSCRIPT_MEMORY_TTL_S,
    VISIT_VIDEO_TIER_DEFAULT,
    VISIT_VOICE_DEFAULT,
)
from main_logic.visit.liveness import VisitLiveness
from main_logic.visit.limits import PeerRateLimiter
from main_logic.visit.outbox import (
    PAUSE_PEER_ABSENT,
    PAUSE_PEER_AWAY,
    PAUSE_SELF_RECONNECT,
    InboxSequencer,
    VisitOutbox,
)
from main_routers.visit_router import credentials as cr
from main_routers.visit_router import inbox_handoff
from main_routers.visit_router.host_port import ManagerHost, VisitHost
from main_routers.visit_router.line_speaker import SpeechRouter, VoiceState
from main_routers.visit_router.runtime_common import (
    ABORT_REASONS,
    EXPLICIT_VENDOR_LEAVES,
    FINALIZE_REASONS,
    KICKED_VENDOR_REASONS,
    PHASE_ACTIVE,
    PHASE_AWAITING,
    PHASE_ENDED,
    PHASE_ENDING,
    PHASE_INVITE_READY,
    PHASE_JOINING,
    PHASE_PENDING,
    STATUS_FOR_REASON,
    WAITING_PHASES,
    leave_reason_for,
    side_prefix,
)
from main_routers.visit_router.runtime_rx import ReceiveMixin, PeerInfo
from main_routers.visit_router.runtime_talk import TalkMixin
from main_routers.visit_router.transport_ws import (
    VisitTransportSession,
    build_credentials_message,
    register_transport_session,
    unregister_transport_session,
)
from main_routers.visit_router.transcript_upload import UploadJournal
from utils.logger_config import get_module_logger
from utils.visit_route_state import (
    VISIT_ROUTE_KIND,
    activate_visit_route,
    finalize_visit_route_state,
    get_visit_route_state,
)

logger = get_module_logger(__name__, "Main")

_PUMP_IDLE_S = 0.25
_INTERRUPT_MAIN_TURN_S = 3.0
_CLOSE_WAIT_S = VISIT_LEAVE_GAP_GRACE_S * 2 + 2.0
_HANDOFF_POLL_S = 0.25
# Total VISIT_SHUTDOWN_BUDGET_S bounds every independently capped _SHUTDOWN_TASK_WAIT_S step by remaining total time.
# Cancel unmatched rooms immediately alongside archival, then wait at most _SHUTDOWN_ROOM_CANCEL_S afterward.
_SHUTDOWN_TASK_WAIT_S = 0.5
_JOURNAL_OPEN_MAX_S = 10.0
_SESSION_CLOSE_S = 5.0
_RESERVED_SEND_MAX_S = 10.0
_SEAL_MAX_S = 10.0
_DISPLAY_FLUSH_S = 2.0
_ENDED_AFTER_DISPLAY_S = 2.0  # Bound waiting for ended notifications queued behind stubborn display writes, then send best effort.
_ACCOUNT_RECORD_S = 3.0
_ACCOUNT_RETRY_DELAYS_S = (1.0, 5.0, 30.0, 120.0, 600.0)
_SHUTDOWN_ROOM_CANCEL_S = 1.0
_RENEW_RETRY_BASE_S = 5.0
_RENEW_RETRY_MAX_S = 60.0
_VOICE_STATUS_THROTTLE_S = 5.0
_ACCOUNT_CHANGE_SEAL_WAIT_S = 30.0  # Maximum wait for active-visit upload archival before logout/account switching.
# Maximum account-bound replay push, checking account then writing; shorter than account-switch waits so it finishes or truncates before switches proceed.
_ACCOUNT_BOUND_PUSH_MAX_S = 10.0

# ═════════════════════════════════════════════════════════════════════
# Dependency injection.
# ═════════════════════════════════════════════════════════════════════


def _default_config_dir() -> Path:
    from utils.config_manager import get_config_manager

    return Path(get_config_manager().config_dir)


async def _default_settings() -> dict:
    from utils.preferences import aload_global_conversation_settings

    try:
        return dict(await aload_global_conversation_settings())
    except Exception as exc:  # noqa: BLE001 - Unreadable means defaults.
        logger.warning("visit: conversation settings unreadable: %s", type(exc).__name__)
        return {}


async def _default_blocklist(config_dir: Path):
    from main_logic.visit.limits import Blocklist

    return await Blocklist.aload(config_dir)


async def _default_create_session(name: str, side: str, *, instructions: str, lang: Optional[str]):
    from main_routers.visit_router.session_pool import create_visit_session

    return await create_visit_session(name, side, instructions=instructions, lang=lang)


@dataclass
class RuntimeDeps:
    """Everything a runtime reaches outside the process or the session manager (tests replace it)."""

    config_dir: Callable[[], Path] = _default_config_dir
    fetch_credentials: Callable[..., Awaitable[cr.VisitCredentials]] = cr.fetch_visit_credentials
    fetch_pubkeys: Callable[[], Awaitable[Any]] = cr.fetch_pubkeys
    load_blocklist: Callable[[Path], Awaitable[Any]] = _default_blocklist
    record_account: Callable[[str, str], Awaitable[Any]] = None  # type: ignore[assignment]
    cancel_room: Callable[..., Awaitable[Any]] = cr.cancel_visit_room
    settings: Callable[[], Awaitable[dict]] = _default_settings
    create_session: Callable[..., Awaitable[Any]] = _default_create_session
    character_context: Callable[[], Awaitable[Any]] = None  # type: ignore[assignment]
    memory_block: Callable[..., Awaitable[str]] = None  # type: ignore[assignment]
    commit_region: Callable[..., Awaitable[Any]] = None  # type: ignore[assignment]
    commit_summary: Callable[..., Awaitable[Any]] = None  # type: ignore[assignment]
    schedule_upload: Callable[[str], Any] = None  # type: ignore[assignment]
    rng: random.Random = field(default_factory=random.Random)
    reply_gap_s: tuple[float, float] = VISIT_REPLY_GAP_S

    def __post_init__(self) -> None:
        if self.record_account is None:
            from main_routers.visit_router.accounts import record_account_visit_uid

            self.record_account = record_account_visit_uid
        if self.character_context is None:
            from main_routers.visit_router.local_context import load_character_context

            self.character_context = load_character_context
        if self.memory_block is None:
            from main_logic.visit.memory_bridge import build_visit_memory_block

            self.memory_block = build_visit_memory_block
        if self.commit_region is None:
            from main_logic.visit.memory_commit import commit_visit_region

            self.commit_region = commit_visit_region
        if self.commit_summary is None:
            self.commit_summary = _default_commit_summary
        if self.schedule_upload is None:
            from main_routers.visit_router.transcript_upload import schedule_visit_retry

            self.schedule_upload = schedule_visit_retry


async def _default_commit_summary(spool: Any, *, family_names: Iterable[str]) -> Any:
    from config.visit_settings import VISIT_LAST_SUMMARY_MAX_TOKENS, VISIT_LLM_TIMEOUT_S
    from main_logic.visit import local_chars
    from main_logic.visit.memory_commit import commit_last_summary
    from main_routers.visit_router.llm import one_shot_llm

    llm = one_shot_llm(max_tokens=VISIT_LAST_SUMMARY_MAX_TOKENS, timeout=VISIT_LLM_TIMEOUT_S)
    return await commit_last_summary(spool, llm=llm, resolve_char_name=local_chars.resolve_char_name,
                                     family_names=family_names)


_deps: Optional[RuntimeDeps] = None


def runtime_deps() -> RuntimeDeps:
    """The dependencies new runtimes are built with (created lazily)."""
    global _deps
    if _deps is None:
        _deps = RuntimeDeps()
    return _deps


def set_runtime_deps(deps: Optional[RuntimeDeps]) -> None:
    """Replace the dependencies (tests); None restores the defaults on next use."""
    global _deps
    _deps = deps


# ═════════════════════════════════════════════════════════════════════
# Module-level registration.
# ═════════════════════════════════════════════════════════════════════

_runtimes: dict[str, "VisitRuntime"] = {}
"""lanlan_name → the visit runtime of that character (one visit per character, OD-03)."""

_by_visit: dict[str, "VisitRuntime"] = {}
"""visit_id → runtime, while it is registered (``is_visit_live``)."""

_recent: dict[str, "VisitRuntime"] = {}
"""visit_id → ended runtime kept ``VISIT_TRANSCRIPT_MEMORY_TTL_S`` for ``GET /transcript`` (memory off)."""

_visit_bg_tasks: dict[str, set[asyncio.Task]] = {}
"""character_uid → background writes of finished visits (rename / delete guard, §3.2.6 item 22)."""


def get_runtime(lanlan_name: str) -> Optional["VisitRuntime"]:
    """The runtime of ``lanlan_name`` (active or still running its exit flow), or None."""
    return _runtimes.get(str(lanlan_name or ""))


def live_runtimes() -> list["VisitRuntime"]:
    """Every registered runtime (one per visiting character), in registration order."""
    return list(_runtimes.values())


def end_visits_disabled() -> int:
    """``visitEnabled`` was switched off: end every live visit now (``visit_disabled``, design §3.7.6).

    A hard end with the fixed farewell line, no natural wrap-up. Visits
    already running their exit flow are left alone. Returns how many were
    ended.
    """
    global _disabled_gen
    _disabled_gen += 1  # In-flight start_visit checks this after waking and skips registration.
    return sum(1 for rt in live_runtimes() if rt.request_finalize("visit_disabled"))


def apply_voice_setting(enabled: bool) -> int:
    """``visitVoiceEnabled`` was saved: live visits use it from their next line (design §3.7.6).

    Pushed by the settings save instead of re-read before each line, so the
    reply runner keeps no await between its admission check and taking the
    line; the home-coming line and debrief summary follow it as well. The
    one-way TTS fallback (``voice.fallen_back``) is kept. Returns how many
    runtimes were updated.
    """
    runtimes = live_runtimes()
    for rt in runtimes:
        rt.voice.enabled = bool(enabled)
    return len(runtimes)


def get_runtime_by_visit(visit_id: str) -> Optional["VisitRuntime"]:
    return _by_visit.get(visit_id)


def _prune_recent(now: float) -> None:
    # Release unqueried visits too, since they hold transcripts/sessions/managers.
    for visit_id, rt in list(_recent.items()):
        if rt.ended_at_mono is None or now - rt.ended_at_mono > VISIT_TRANSCRIPT_MEMORY_TTL_S:
            del _recent[visit_id]


def recent_runtime(visit_id: str, *, now: Optional[float] = None) -> Optional["VisitRuntime"]:
    """A runtime that ended less than ``VISIT_TRANSCRIPT_MEMORY_TTL_S`` ago (its memory transcript)."""
    rt = _recent.get(visit_id)
    if rt is None:
        return None
    now = time.monotonic() if now is None else now
    if rt.ended_at_mono is None or now - rt.ended_at_mono > VISIT_TRANSCRIPT_MEMORY_TTL_S:
        _recent.pop(visit_id, None)
        return None
    return rt


def is_visit_live(visit_id: str) -> bool:
    """Whether a runtime of this process still owns ``visit_id`` (recovery / uploads skip it)."""
    return visit_id in _by_visit


def is_visit_route_active(lanlan_name: str) -> bool:
    """Registry ``is_active``: the visit owns the character's input.

    False from ``ending`` on, but only once the takeover is released: until
    then ordinary chat would run with its output suppressed, so the visit
    keeps the input and refuses it.
    """
    rt = _runtimes.get(str(lanlan_name or ""))
    if rt is None:
        return False
    return rt.phase not in (PHASE_ENDING, PHASE_ENDED) or rt.takeover_token is not None


def is_visit_route_locked(lanlan_name: str) -> bool:
    """Registry ``is_locked``: the slot is taken until the exit flow completed.

    Also true for a reservation whose runtime is not registered yet
    (``start_visit`` between the slot and the runtime), so a second start of
    the same character is refused instead of replacing the slot.
    """
    name = str(lanlan_name or "")
    return name in _runtimes or get_visit_route_state(name) is not None


def _character_uid_of(lanlan_name: str) -> Optional[str]:
    rt = _runtimes.get(str(lanlan_name or ""))
    if rt is not None:
        return rt.character_uid
    return _uid_by_name.get(str(lanlan_name or ""))


_uid_by_name: dict[str, str] = {}
"""Last known ``character_uid`` of a name that started a visit (background-task lookup by name)."""

_resolving_names: set[object] = set()
"""Background tasks whose character name is still being resolved (the lifecycle guard is conservative)."""

_pending_visits: dict[tuple[str, str], Path] = {}
"""``(visit_id, side)`` between the slot reservation and the runtime registration (``start_visit``),
with the config dir their files will live in."""

_stop_gen = 0
"""Bumped by ``stop_all``: a ``start_visit`` that was still awaiting when it ran does not register."""

_stopping = False
"""True while ``stop_all`` runs: a ``start_visit`` arriving meanwhile is refused (it would not be stopped)."""

_account_changes = 0
"""Community logouts / account switches in progress (:func:`account_change`): no visit is admitted."""

_account_gen = 0
"""Bumped when an account change starts: a ``start_visit`` that was awaiting meanwhile does not register."""

_disabled_gen = 0
"""Bumped by :func:`end_visits_disabled`: a ``start_visit`` still awaiting when the user switched visits off
does not register (it is not in ``_runtimes`` yet, so ending live visits cannot reach it)."""

_account_change_lock: Optional[tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = None
"""Serializes account changes (created per event loop: a lock binds to the loop that first waits on it)."""


def account_epoch() -> Optional[int]:
    """The account-change generation, or None while a logout / account switch is in progress.

    Every logout / switch (:func:`account_change`) bumps it, so two equal
    readings around an await prove no account change happened in between
    (an A→B→A switch included).
    """
    return None if _account_changes else _account_gen


def _account_change_mutex() -> asyncio.Lock:
    global _account_change_lock
    loop = asyncio.get_running_loop()
    if _account_change_lock is None or _account_change_lock[0] is not loop:
        _account_change_lock = (loop, asyncio.Lock())
    return _account_change_lock[1]


def has_visit_background_tasks(lanlan_name: str) -> bool:
    """Registry ``has_background_tasks``: this character's visit background writes are running."""
    if _resolving_names:
        # Background tasks not yet associated with names, from startup backfill, conservatively block rename/delete briefly.
        return True
    uid = _character_uid_of(lanlan_name)
    tasks = _visit_bg_tasks.get(uid) if uid else None
    return bool(tasks and any(not t.done() for t in tasks))


async def _remember_name(character_uid: str) -> None:
    # Guards query names; backfill tasks have UIDs only, so resolve current names even for characters never visited in this process.
    # Resolve every time: idle-time renames invalidate old mappings.
    try:
        from main_logic.visit import local_chars

        name = await local_chars.resolve_char_name(character_uid)
    except Exception:  # noqa: BLE001 - Unknown names register by UID only.
        return
    if not name:
        return
    for old, uid in list(_uid_by_name.items()):
        if uid == character_uid and old != name:
            del _uid_by_name[old]
    _uid_by_name[name] = character_uid


async def _remember_name_strict(character_uid: str) -> None:
    """Like :func:`_remember_name`, but a config read failure raises (the clearing guard fails closed).

    A guard registered only under the uid would not block a rename of the
    character's current name, so it must not be entered without one; a
    deleted character (no current name) has nothing left to protect.
    """
    from main_logic.visit import local_chars

    name = await local_chars.resolve_char_name(character_uid)
    if not name:
        return
    for old, uid in list(_uid_by_name.items()):
        if uid == character_uid and old != name:
            del _uid_by_name[old]
    _uid_by_name[name] = character_uid


def spawn_visit_background(character_uid: str, factory: Callable[[], Awaitable[Any]]) -> asyncio.Task:
    """Run a visit background write registered under ``character_uid`` until it finishes.

    The ``spawn_background`` callback of PR-08 recovery and of the finalize
    flow (digest, last summary): rename / delete of the character stay
    refused while any of them runs (the character's current name is looked
    up first, so a task spawned by recovery is found by name as well).
    """
    bucket = _visit_bg_tasks.setdefault(character_uid, set())

    async def run() -> Any:
        try:
            try:
                await _remember_name(character_uid)
            finally:
                _resolving_names.discard(token)
            return await factory()
        finally:
            bucket.discard(task)
            if not bucket:
                _visit_bg_tasks.pop(character_uid, None)

    token = object()
    _resolving_names.add(token)
    task = asyncio.ensure_future(run())
    bucket.add(task)
    return task


@contextlib.asynccontextmanager
async def hold_character_lifecycle(character_uids: Iterable[str]):
    """``lifecycle_guard`` of the clearing endpoints: rename / delete of these characters answer 400 while held.

    Entered under the character-config mutation lock (the one rename and
    delete run under), so a rename already in progress finishes first and
    the clearing then re-reads the new name; once entered, the characters
    count as having visit background work (``is_character_lifecycle_locked``).
    """
    from utils.character_memory import character_config_mutation_lock

    held = asyncio.get_running_loop().create_future()
    buckets: list[tuple[str, set]] = []
    try:
        async with character_config_mutation_lock:
            for uid in sorted({str(u) for u in character_uids if u}):
                await _remember_name_strict(uid)
                bucket = _visit_bg_tasks.setdefault(uid, set())
                bucket.add(held)
                buckets.append((uid, bucket))
        yield
    finally:
        if not held.done():
            held.set_result(None)
        for uid, bucket in buckets:
            bucket.discard(held)
            if not bucket and _visit_bg_tasks.get(uid) is bucket:
                _visit_bg_tasks.pop(uid, None)


def current_instance(lanlan_name: str) -> Optional[str]:
    """Registry ``current_instance``: the visit id owning the character."""
    rt = _runtimes.get(str(lanlan_name or ""))
    return f"visit:{rt.visit_id}" if rt is not None else None


def _register(rt: "VisitRuntime") -> None:
    _runtimes[rt.lanlan_name] = rt
    # Keep first registration: another local character redeeming a local invitation gets Servers self_invite rejection;
    # before rejection it must not replace the ongoing visit.
    _by_visit.setdefault(rt.visit_id, rt)
    if rt.character_uid:
        _uid_by_name[rt.lanlan_name] = rt.character_uid


def _unregister(rt: "VisitRuntime") -> None:
    if _runtimes.get(rt.lanlan_name) is rt:
        del _runtimes[rt.lanlan_name]
    if _by_visit.get(rt.visit_id) is rt:
        del _by_visit[rt.visit_id]
        other = next((r for r in _runtimes.values() if r.visit_id == rt.visit_id), None)
        if other is not None:
            _by_visit[rt.visit_id] = other


def _reset_for_tests() -> None:
    for task in list(_detached):
        task.cancel()
    _detached.clear()
    for task in list(_room_cancels) + list(_outbox_cleanups):
        task.cancel()
    _room_cancels.clear()
    _outbox_cleanups.clear()
    _awaited_writes.clear()
    global _account_changes, _account_gen, _account_change_lock, _disabled_gen
    _account_changes = 0
    _account_gen = 0
    _disabled_gen = 0
    _account_change_lock = None
    _credential_requests.clear()
    _account_bound_pushes.clear()
    _runtimes.clear()
    _pending_visits.clear()
    _resolving_names.clear()
    _by_visit.clear()
    _recent.clear()
    _visit_bg_tasks.clear()
    _uid_by_name.clear()
    inbox_handoff._reset_for_tests()
    set_runtime_deps(None)


# ═════════════════════════════════════════════════════════════════════
# Transport sessions.
# ═════════════════════════════════════════════════════════════════════


class _Transport(VisitTransportSession):
    """The runtime's side of the transport WS (``VisitTransportSession``)."""

    def __init__(self, rt: "VisitRuntime") -> None:
        super().__init__(visit_id=rt.visit_id, side=rt.side, lanlan_name=rt.lanlan_name,
                         liveness=rt.liveness, outbox=rt.outbox)
        self._rt = rt

    async def on_preflight(self, caps: dict) -> None:
        await self._rt.on_preflight(caps)

    async def issue_credentials(self) -> Optional[dict]:
        return await self._rt.issue_credentials()

    async def on_sdk_caps(self, caps: dict) -> None:
        await self._rt.on_sdk_caps(caps)

    async def on_state(self, msg: dict) -> None:
        await self._rt.on_transport_state(msg)

    async def on_recv(self, *, from_vid: str, cmd: int, payload: dict, nbytes: int) -> None:
        await self._rt.on_recv(from_vid=from_vid, cmd=cmd, payload=payload, nbytes=nbytes)

    def media_snapshot(self) -> dict:
        return self._rt.media_snapshot()

    async def on_stats(self, msg: dict) -> None:
        self._rt.on_transport_stats(msg)

    def on_page_lost(self, now: float) -> None:
        super().on_page_lost(now)
        self._rt.on_page_gone()
        self._rt.kick()

    def on_page_attached(self, now: float) -> None:
        super().on_page_attached(now)
        # Replaced connections' finally no longer reports page_lost; invalidate their residue too.
        self._rt.on_page_gone()

    def on_page_rejoined(self, now: float) -> None:
        super().on_page_rejoined(now)
        self._rt.reconnects += 1
        self._rt.kick()

    def now(self) -> float:
        return self._rt.clock()

    def on_frame_sent(self, frame: Any) -> None:
        self._rt.on_frame_sent(frame)


# ═════════════════════════════════════════════════════════════════════
# Runtime.
# ═════════════════════════════════════════════════════════════════════

_vp8_next_visit = False
"""``stats.softenc_overloaded`` seen: the next LiveKit visit publishes vp8 (``VISIT_VP9_CPU_FALLBACK``)."""


class VisitRuntime(ReceiveMixin, TalkMixin):
    """One side of one visit (see the module docstring)."""

    def __init__(
        self,
        *,
        lanlan_name: str,
        side: str,
        visit_id: str,
        host: VisitHost,
        character_uid: str,
        persona_text: str,
        crop: str = VISIT_CROP_DEFAULT,
        invite_code: Optional[str] = None,
        deps: Optional[RuntimeDeps] = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.lanlan_name = lanlan_name
        self.side = side
        self.peer_side = "guest" if side == "host" else "host"
        self.visit_id = visit_id
        self.host = host
        self.character_uid = character_uid
        self.persona_text = persona_text
        self.crop = crop if crop in ("upper", "full") else VISIT_CROP_DEFAULT
        self.invite_code = invite_code
        self.deps = deps or runtime_deps()
        self.clock = clock
        self.wall = wall
        self.config_dir = Path(self.deps.config_dir())
        self.spool_dir = self.config_dir / VISIT_SPOOL_DIRNAME

        now = clock()
        self.created_at = now
        self.phase = PHASE_PENDING
        self.slot: dict = {}
        self.liveness = VisitLiveness(side, now)
        # Waiting deadlines start by side only after joined; beforehand use capability/room-entry timers only.
        self.liveness.wait_deadline = math.inf
        self.outbox = VisitOutbox(visit_id, side, clock=clock, spool_dir=self.spool_dir)
        self.sequencer = InboxSequencer(on_early=self._on_early_wrap_up,
                                        peer_ln_prefix=side_prefix(self.peer_side))
        self.limiter = PeerRateLimiter(clock=clock)
        self.journal = UploadJournal(self.config_dir, visit_id)
        self.speech_router = SpeechRouter()
        self.transport = _Transport(self)
        self.room = None
        self.spool = None
        self.session = None
        self.voice = VoiceState(enabled=VISIT_VOICE_DEFAULT)
        self.memory_enabled: Optional[bool] = None
        self.lang: Optional[str] = None
        self.family_names: tuple[str, ...] = ()
        self.char_names: tuple[str, ...] = ()

        # Credentials and capability gates.
        self.grant: Optional[cr.VisitGrant] = None
        # Admission clear/block-cache checks use a community account; issued credentials must belong to it.
        self.admitted_account: Optional[str] = None
        # Admission account_epoch must remain current before credential acquisition.
        self.admitted_epoch: Optional[int] = None
        self._livekit_codec: Optional[str] = None
        self._creds_task: Optional[asyncio.Task] = None
        self.preflight_ok: Optional[bool] = None
        self.video_ok = False
        self.codecs: list[str] = []
        self._preflight_deadline: Optional[float] = now + VISIT_CAPS_PREFLIGHT_TIMEOUT_S
        self._sdk_deadline: Optional[float] = None
        self._join_deadline: Optional[float] = None
        self.joined = False
        self.peer_present = False
        self._hello_sent = False
        self.peer: Optional[PeerInfo] = None
        self._accept_deadline: Optional[float] = None
        self.accepted: Optional[bool] = None
        self.activated = False
        self._activation: Optional[asyncio.Task] = None
        self.ready_exchanged = False
        self.started_at_mono: Optional[float] = None
        self.started_at_wall: Optional[float] = None
        self._time_up_sent = False
        self._ending_soon_sent = False
        self._invite_frame: Optional[dict] = None
        self._last_hidden_sent: Optional[bool] = None
        self.last_text_at: Optional[float] = None
        self.local_hidden = False
        self.ladder = 0
        self.stats: dict = {}
        self.reconnects = 0
        self.pre_room_anomalies = 0
        self._last_voice_status = -math.inf

        # Takeover and callbacks.
        self.takeover_token: Any = None
        self.hold_token: Any = None
        self.inbox = _make_inbox()

        # Tasks.
        self._kick_event = asyncio.Event()
        self._pump_task: Optional[asyncio.Task] = None
        self._pump_stop = False
        self._ended_sending: Optional[asyncio.Future] = None
        self._memory_off_unsaved = False
        self._state_writing: Optional[asyncio.Future] = None  # State write still running after activation cancellation; finalization awaits completion first.
        self._renew_task: Optional[asyncio.Task] = None
        self._renew_failures = 0
        self._renew_retry_at: Optional[float] = None
        self._exit_task: Optional[asyncio.Task] = None
        self._tasks: set[asyncio.Task] = set()

        # Finalization.
        self.finalize_reason: Optional[str] = None
        self.peer_reason: Optional[str] = None
        self.status_code: Optional[str] = None
        self.status_details: dict = {}
        self.finalize_at: Optional[float] = None
        self._was_phase = PHASE_PENDING
        self.ended_at_mono: Optional[float] = None
        self.handoff: Optional[inbox_handoff.InboxHandoff] = None
        self.done_received = False
        self.sealed_doc: Optional[dict] = None
        self._sealing: Optional[asyncio.Future] = None
        self._spool_finalized = False
        self._files_deferred = False
        self._deferred_files_done = False
        self._files_gen: Optional[int] = None
        self.spool_lines = 0
        self._terminated = False
        self._ended_published = False
        self._room_cancel_sent = False
        self._room_cancel_task: Optional[asyncio.Task] = None
        self._room_cancel_at = 0.0
        self._handback_started = False
        self._files_done = False
        self._shutdown_started = False
        self._closing_task: Optional[asyncio.Task] = None
        self._journal_opening: Optional[asyncio.Task] = None
        self._journal_backlog: list[dict] = []
        self._journal_started_at: Optional[float] = None
        self._sdk_ok = False
        self._pending_join: Optional[dict] = None
        self._page_gen = 0
        self._first_join_gen: Optional[int] = None

        self._init_rx()
        self._init_talk()

    # -- Small helpers --

    def spawn(self, coro: Awaitable[Any], *, name: str = "") -> asyncio.Task:
        """A task owned by this runtime (cancelled when the runtime is torn down)."""
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning("visit %s: background step failed: %r", self.visit_id[:6], task.exception())

    def kick(self) -> None:
        """Wake the pump (an outbox item is waiting or a state changed)."""
        self._kick_event.set()

    @property
    def creds(self) -> Optional[cr.VisitCredentials]:
        return self.grant.current if self.grant is not None else None

    @property
    def finalizing(self) -> bool:
        return self._exit_task is not None or self._terminated

    def _release_turn_wrap_up(self) -> None:
        release = getattr(self.host, "release_turn_wrap_up", None)
        if callable(release):
            try:
                release()
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit %s: turn wrap-up not released: %s", self.visit_id[:6], type(exc).__name__)

    def _keep_background(self, task: asyncio.Future) -> None:
        _keep_detached(task)

    def _set_phase(self, phase: str) -> None:
        self.phase = phase
        self.slot["phase"] = phase

    async def _send_after_display(self, display: asyncio.Future, payload: dict) -> None:
        await asyncio.wait([display], timeout=_ENDED_AFTER_DISPLAY_S)
        if not display.done():
            # Previous write exceeded its deadline: still send best effort; stuck finalizing UI is worse than occasional concurrent writes.
            logger.warning("visit %s: display write never retired; sending ended anyway", self.visit_id[:6])
        try:
            await self.host.send_frame(payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("visit %s: ended not sent: %s", self.visit_id[:6], type(exc).__name__)

    async def _status_after(self, previous: asyncio.Future, code: str, details: dict) -> None:
        await asyncio.wait([previous])  # previous has its own deadline.
        try:
            await self.host.send_status(code, details)
        except Exception as exc:  # noqa: BLE001
            logger.warning("visit %s: status not sent: %s", self.visit_id[:6], type(exc).__name__)

    async def push(self, action: str, **fields: Any) -> None:
        """``visit_state_change{action}`` to the display socket (§4.5).

        Through the ordered display queue, like every other frame of the
        visit: a backpressured earlier phase can never land after a later
        one. ``ended`` alone is written directly, once the queue has been
        sent or retired (``_exit_steps``); after it the queue takes nothing.
        """
        payload = {"type": "visit_state_change", "action": action, "side": self.side,
                   "visit_id": self.visit_id, "ts": self.wall()}
        payload.update({k: v for k, v in fields.items() if v is not None})
        if action == PHASE_ENDED:
            display = self._display_task
            if display is not None and not display.done():
                # Display write exceeded deadline: queue ended after it in background with bounds, never writing concurrently or arriving first.
                ended = self._ended_sending = asyncio.ensure_future(self._send_after_display(display, payload))
                self._keep_background(ended)
                return
            await self.host.send_frame(payload)
            return
        self._post_display(payload)

    async def status(self, code: str, **details: Any) -> None:
        details = {"visit_id": self.visit_id, **{k: v for k, v in details.items() if v is not None}}
        ended = self._ended_sending
        if ended is not None and not ended.done():
            # ended still waits behind blocked display writes; queue this status afterward without overtaking/concurrent writes.
            self._keep_background(asyncio.ensure_future(self._status_after(ended, code, details)))
            return
        await self.host.send_status(code, details)

    # -- Startup --

    def start(self) -> None:
        """Register the runtime and its transport; the page builds the iframe on ``pending``."""
        _register(self)
        register_transport_session(self.transport)
        self._pump_task = asyncio.ensure_future(self._pump_loop())
        self.spawn(self.push(PHASE_PENDING, transport=None))

    # -- Capability gates and credentials --

    async def on_preflight(self, caps: dict) -> None:
        """``caps{stage:'preflight'}``: ``preflight_ok:false`` ends the visit without contacting Servers."""
        if self.finalizing:
            return
        ok = caps.get("preflight_ok") is True
        if self.preflight_ok is None:
            self.preflight_ok = ok
            self._preflight_deadline = None
        if not ok:
            self.request_finalize("unsupported", status_details={"reason": caps.get("reason")})

    async def issue_credentials(self) -> Optional[dict]:
        """First ``credentials`` of a transport connection (first issue, or page re-entry)."""
        if self.finalizing:
            return None
        gen = self._page_gen  # Connection replaced/disconnected meanwhile: never impose its deadline on new connections, which request credentials themselves.
        if self.grant is None:
            if self._creds_task is None:
                self._creds_task = asyncio.ensure_future(self._obtain_credentials())
            task = self._creds_task
            # Await without cancelling underlying work so page reconnects can retry; shutdown cancellation means no credentials acquired.
            await asyncio.wait([task])
            if task.cancelled():
                return None
            if task.exception() is not None:
                # Noncontract local errors, e.g. main-chat interruption adapters failing, prevent startup; finalize busy like interruption failure,
                # releasing routes without throwing into transport callbacks or leaving failed tasks retried forever on reconnect.
                logger.warning("visit %s: credential setup failed: %r", self.visit_id[:6], task.exception())
                self.request_finalize("busy")
                return None
            if not task.result() or self.finalizing or gen != self._page_gen:
                return None
            self._sdk_deadline = self.clock() + VISIT_CAPS_SDK_TIMEOUT_S
        else:
            creds = self.grant.current
            if creds.ticket_expired(wall_now=self.wall()):
                self.request_finalize("relay_lost")
                return None
            try:
                await self.grant.ensure_fresh()
            except cr.VisitRoomEnded:
                self.request_finalize("kicked")
                return None
            except cr.VisitServersError as exc:
                # Failed renewal retains existing credentials, still valid for room reentry until expiry.
                logger.warning("visit %s: vendor grant renewal failed (%s)", self.visit_id[:6], exc.code)
            if self.finalizing or gen != self._page_gen:
                return None
            now = self.clock()
            remaining = (self.liveness.page_reload_deadline() or math.inf) - now
            self._sdk_deadline = now + max(0.0, min(VISIT_CAPS_SDK_TIMEOUT_S, remaining))
        return self._credentials_message(refresh=False)

    def _codec(self) -> str:
        creds = self.creds
        global _vp8_next_visit
        if creds is not None and creds.transport == "trtc":
            return "h264"
        if self._livekit_codec is None:
            # Prior software-encoding overload affects this next visit only; consume marker once and preserve choice across renewal/reconnect.
            self._livekit_codec = "vp8" if _vp8_next_visit else "vp9"
            _vp8_next_visit = False
        return self._livekit_codec

    def _credentials_message(self, *, refresh: bool) -> dict:
        creds = self.grant.current  # type: ignore[union-attr]
        peer_vid = self.peer.vid if self.peer is not None else None
        return build_credentials_message(creds, side=self.side, crop=self.crop, codec=self._codec(),
                                         peer_vid=peer_vid, refresh=refresh)

    async def _obtain_credentials(self) -> bool:
        """Main turn interrupt → Servers credentials → takeover (§3.2.1 steps 4–6)."""
        # Both sides interrupt ongoing ordinary-text generation first; takeover suppresses output without stopping offline generation.
        if not await self.host.interrupt_main_turn(_INTERRUPT_MAIN_TURN_S):
            self.request_finalize("busy")
            return False
        if self.finalizing:
            # Visit ended while awaiting main-chat turns: never acquire irreversible guest credentials consuming invitations/quota.
            return False
        if self.admitted_epoch is not None and account_epoch() != self.admitted_epoch:
            # Logout/account changes after admission or in progress must end before irreversible credential redemption.
            # Check synchronously and request immediately. Later account changes finalize this visit before mutating local sessions,
            # so this request still uses its admitted account.
            self.request_finalize("busy")
            return False
        # No yield since generation checks; later account changes await this request before changing local sessions.
        # Pin admitted accounts: credential clients send nothing if their session snapshot already changed accounts, preventing wrong-account redemption.
        request = asyncio.ensure_future(self.deps.fetch_credentials(
            role=self.side, visit_id=self.visit_id, char_tag=self.character_uid,
            tier=VISIT_VIDEO_TIER_DEFAULT, display_name=self.lanlan_name,
            invite_code=self.invite_code if self.side == "guest" else None,
            expect_account=self.admitted_account,
        ))
        _credential_requests.add(request)
        request.add_done_callback(_credential_requests.discard)
        try:
            creds = await request
        except cr.VisitServersError as exc:
            self._finalize_for_servers_error(exc)
            return False
        except Exception as exc:  # noqa: BLE001 - Noncontract failures count as Servers unreachable.
            logger.warning("visit %s: credentials failed: %s", self.visit_id[:6], type(exc).__name__)
            self.request_finalize("servers_unreachable")
            return False
        self.grant = cr.VisitGrant(creds, display_name=self.lanlan_name,
                                   invite_code=self.invite_code if self.side == "guest" else None)
        try:
            # Bound local file-lock/disk accounting so pending visits cannot hold routing forever.
            await asyncio.wait_for(self.deps.record_account(creds.account, creds.visit_uid), _ACCOUNT_RECORD_S)
        except Exception as exc:  # noqa: BLE001 - Failed mappings do not block issued/quota-charged visits; backfill asynchronously.
            logger.warning("visit %s: account mapping not recorded: %r", self.visit_id[:6], exc)
            # Transcript/report uploads use this account mapping; retry indefinitely, leaving uploads queued until persistence succeeds.
            _detach(_retry_record_account(self.deps, creds.account, creds.visit_uid, self.visit_id))
        if self.admitted_account is not None and creds.account != self.admitted_account:
            # Account changed since admission clear/block checks: reject credentials and cancel host rooms.
            logger.warning("visit %s: community account changed before credentials", self.visit_id[:6])
            self.request_finalize("busy")
            self._cancel_room_once()
            return False
        if self.side == "host" and not creds.invite_code:
            logger.warning("visit %s: host credentials without an invite code", self.visit_id[:6])
            self.request_finalize("servers_unreachable")
            return False
        if self.finalizing:
            # Visit ended while waiting for Servers; finalization may already have passed cancellation, so attempt once here too.
            self._cancel_room_once()
            return False
        try:
            self.takeover_token = self.host.acquire_takeover(self._voice_dispatcher, self.inbox.accept)
        except Exception as exc:  # noqa: BLE001 - Another route acquired first: finalize busy.
            logger.warning("visit %s: takeover refused: %s", self.visit_id[:6], type(exc).__name__)
            self.request_finalize("busy")
            return False
        try:
            # Bound waiting when pages refuse; takeover already exists but capability deadlines are not yet installed.
            await asyncio.wait_for(self.host.interrupt_ordinary_speech(), _INTERRUPT_MAIN_TURN_S)
        except Exception as exc:  # noqa: BLE001 - Cannot interrupt ordinary speech: release takeover and finalize busy.
            logger.warning("visit %s: ordinary speech not interrupted: %s", self.visit_id[:6], type(exc).__name__)
            self.request_finalize("busy")
            return False
        return True

    def _finalize_for_servers_error(self, exc: cr.VisitServersError) -> None:
        details: dict = {}
        if exc.retry_after_s is not None:
            details["retry_after_s"] = exc.retry_after_s
        if isinstance(exc, cr.VisitRoomEnded):
            self.request_finalize("kicked")
            return
        if isinstance(exc, cr.VisitAccountChanged):
            self.request_finalize("busy")
            return
        if isinstance(exc, cr.VisitLoginRequired):
            reason = "login_required"
        elif isinstance(exc, cr.VisitBanned):
            reason = "banned"
        elif isinstance(exc, cr.VisitQuotaExceeded):
            reason = "quota_exceeded"
        elif isinstance(exc, cr.VisitTierNotEntitled):
            reason = "tier_not_entitled"
        elif isinstance(exc, cr.VisitCrossRegionUnsupported):
            reason = "cross_region_unsupported"
        elif isinstance(exc, cr.VisitInviteInvalid):
            # Valid at preview but now within 60s of expiry: report expired invitation.
            reason = "invite_expired" if exc.reason == "invite_expiring" else "invite_invalid"
            details["reason"] = exc.reason
        else:
            reason = "servers_unreachable"
        self.request_finalize(reason, status_details=details)

    def on_page_gone(self) -> None:
        """The transport socket that was setting up the room is gone (dropped or replaced).

        Its deferred ``joined`` report is void (the new socket must enter and
        report itself), and the per-connection setup deadlines (preflight /
        SDK gate / join) stop: until the new socket gets its credentials the
        page-reload deadline of the liveness is the only clock.
        """
        self._pending_join = None
        self._preflight_deadline = None
        self._sdk_deadline = None
        self._join_deadline = None
        self._sdk_ok = False  # Capability gate 3 is per connection; new connections report independently.
        self._page_gen += 1

    async def on_sdk_caps(self, caps: dict) -> None:
        """Gate ③: ``transport_ok:false`` ends the visit (Servers already counted one issue)."""
        if self.finalizing:
            return
        self._sdk_deadline = None
        if caps.get("transport_ok") is not True:
            self.request_finalize("unsupported", status_details={"reason": caps.get("reason")})
            return
        self.video_ok = caps.get("video_ok") is True
        self.codecs = list(caps.get("codecs") or [])
        self._sdk_ok = True
        if self._pending_join is not None and not self.joined:
            # Do not consume while old first-join work remains joined; retain for retry when it invalidates.
            pending, self._pending_join = self._pending_join, None
            await self.on_transport_state(pending)
            return
        if not self.joined and self._join_deadline is None:
            # Capability success is not room-entry success: no joined within 25s ends relay_lost without issuing invitations.
            self._join_deadline = self.clock() + VISIT_SELF_RECONNECT_S

    # ── transport state ─────────────────────────────────────────────

    async def on_transport_state(self, msg: dict) -> None:
        """Vendor connection report of the iframe (§4.3 ``state``)."""
        if self.finalizing:
            return
        now = self.clock()
        state = msg.get("state")
        if isinstance(msg.get("hidden"), bool):
            self.local_hidden = msg["hidden"]
            self._announce_view()
        if state not in ("joined", "connected"):
            # Later disconnect/error/kick invalidates remembered joins; capability success must not treat them still present.
            self._pending_join = None
        if state in ("joined", "connected"):
            reconnected = self.liveness.self_disconnected_at is not None
            self.liveness.on_self_connected(now)
            self.outbox.resume(now, reason=PAUSE_SELF_RECONNECT)
            if self._first_join_gen is not None and self._first_join_gen != self._page_gen:
                # Old first-join header writes continue after connection loss; record new joins and process after old work ends.
                self._pending_join = dict(msg)
                return
            if not self.joined:
                if self.creds is not None and not self._sdk_ok:
                    # joined may precede gate 3 and occurs once per iframe entry; retain and replay after gate success.
                    self._pending_join = dict(msg)
                    return
                # First-join header/generation/phase accounting is runtime-owned protected work that finishes despite cancelled receive handlers,
                # or _first_join_gen sticks on old generations and reconnect joins remain in _pending_join forever.
                joining = self.spawn(self._on_first_join(now), name="first_join")
                await asyncio.shield(joining)
            elif reconnected:
                # SDK reconnect success resends hello/current-media snapshots; manual room reentry lost iframe publication/subscription.
                self.outbox.resend_hello(now)
                self.spawn(self.send_media())
        elif state in ("reconnecting", "left"):
            # left means iframe left the vendor room, normally only during stop/finalization, already returned above.
            # Transport then closes; otherwise bounded local reconnect pauses deadlines, allowing return within grace,
            # or later transport disconnection enters page-reload grace. Without either, end relay_lost rather than holding routes forever.
            if self.liveness.self_disconnected_at is None:
                self.reconnects += 1
            self.liveness.on_self_disconnected(now)
            self.outbox.pause(now, reason=PAUSE_SELF_RECONNECT)
            self.spawn(self.push("reconnecting"))
        elif state == "kicked":
            if msg.get("vendor_reason") in KICKED_VENDOR_REASONS:
                self.request_finalize("kicked")
                return
        elif state == "error" and not self.joined:
            self.request_finalize("relay_lost", status_details={"reason": msg.get("error_code")})
            return
        present = msg.get("peer_present")
        if isinstance(present, bool) and self.joined:
            self._on_peer_presence(present, msg.get("vendor_reason"), now)
        self.kick()

    async def _on_first_join(self, now: float) -> None:
        creds = self.creds
        if creds is None or not self._sdk_ok or self.finalizing:
            # Ignore joins before credential delivery/gate 3; perform initial-join work on actual entry later.
            return
        self.joined = True
        gen = self._first_join_gen = self._page_gen
        # On entry, write upload headers before lines/usage/errors. Use an independent task;
        # finalization/shutdown await it before sealing, preventing empty-noop archival. Write once,
        # reusing across replacement connections' first joins.
        opening = self._start_journal()
        # Bound upload-header writes so blocked disks cannot hang receives while room-entry deadlines expire as relay_lost.
        await asyncio.wait([opening], timeout=_JOURNAL_OPEN_MAX_S)
        self._first_join_gen = None
        if not opening.done():
            logger.warning("visit %s: upload journal still opening, buffering its lines", self.visit_id[:6])
        if opening.done() and not opening.cancelled() and opening.exception() is not None:
            # Failed upload journals lose one transcript copy, not visit operation.
            logger.warning("visit %s: upload journal not opened: %s", self.visit_id[:6],
                           type(opening.exception()).__name__)
        if self.finalizing:
            # Visit ended during header write: never return phase to waiting.
            return
        self._join_deadline = None
        if gen != self._page_gen:
            # Joining connection vanished/replaced during header writing: invalidate its join and process new-connection reports afterward.
            self.joined = False
            pending, self._pending_join = self._pending_join, None
            if pending is not None:
                await self.on_transport_state(pending)
            elif self._sdk_ok and self._join_deadline is None:
                # New connection passed gate 3 while joined was still true, without its own joined report: install entry deadline,
                # ending relay_lost if no join arrives rather than waiting forever.
                self._join_deadline = self.clock() + VISIT_SELF_RECONNECT_S
            return
        if self.peer is not None:
            # Peer hello arrived earlier, verified, and entered reception waiting; verification owns phase/deadline, never reset them.
            return
        now = self.clock()  # Header write awaited; start waiting deadlines now.
        if self.side == "host":
            # Hosts await guests under invitation expiry only; observed peer entry extends through liveness.
            from config.visit_settings import VISIT_INVITE_WAIT_S

            self.liveness.wait_deadline = now + VISIT_INVITE_WAIT_S
            self._set_phase(PHASE_INVITE_READY)
            await self.push(PHASE_INVITE_READY, invite_code=creds.invite_code,
                            invite_expires_at=creds.invite_expires_at, transport=creds.transport)
        else:
            from config.visit_settings import VISIT_PEER_LOST_S

            self.liveness.wait_deadline = now + VISIT_PEER_LOST_S
            self._set_phase(PHASE_JOINING)
            await self.push(PHASE_JOINING, transport=creds.transport, cross_region=creds.cross_region)

    def _start_journal(self) -> asyncio.Future:
        """Start writing the upload header (once); later calls return the same task."""
        opening = self._journal_opening
        if opening is None:
            creds = self.creds
            # Peer hello/speech preceded our joined report; backlog lines are earlier than now. Start no later than earliest speech,
            # preserving ts>=started_at and backfill ended_at>=start when using final records.
            # Consider lines only: prejoin usage/errors never extend start backward or inflate duration with idle setup time.
            started_at = min([self.wall()] + [float(r["ts"]) for r in self._journal_backlog
                                              if r.get("kind") == "line" and isinstance(r.get("ts"), (int, float))
                                              and not isinstance(r.get("ts"), bool)])
            self._journal_started_at = started_at
            opening = self._journal_opening = asyncio.ensure_future(self.journal.open(
                role=self.side, own_visit_uid=creds.visit_uid, own_char_uid=self.character_uid,
                transport=creds.transport, started_at=started_at, app_version=cr._app_version(),
            ))
            # After header persistence, immediately backfill waiting lines/usage/errors; is_open=False after failure makes this noop.
            opening.add_done_callback(lambda _t: self._flush_journal_backlog())
        return opening

    async def _settle_delivering(self, timeout: float) -> None:
        # Both finalization paths set _rx_closed first, stopping queued on_recv handlers before new batches cross archival
        # boundaries. Wait only preboundary accepted batches here. Loop for calls while receiving remains open,
        # when queued handlers can wake first and synchronously hand off new batches beyond a one-time snapshot.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self._delivering is not None and not self._delivering.done():
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            await asyncio.wait([self._delivering], timeout=remaining)

    def _start_journal_for_backlog(self) -> None:
        # Peer hello was verified and speech began, but no local joined report arrived before finalization, leaving lines only
        # in backlog. Start headers now, then seal normally; shutdown abandons for startup backfill as usual.
        if (self._journal_opening is None and self.creds is not None and not self.journal.sealed
                and any(r.get("kind") == "line" for r in self._journal_backlog)):
            self._start_journal()

    def _on_peer_presence(self, present: bool, vendor_reason: Any, now: float) -> None:
        if present:
            if not self.peer_present:
                self.peer_present = True
                if self.liveness.peer_departed_at is not None or self.peer is not None:
                    # Reentry after explicit leave/timeout confirms peer presence afresh; heartbeat starts now.
                    self.liveness.on_peer_vendor_rejoined(now)
                self.liveness.on_peer_entered(now)
                self.outbox.resume(now, reason=PAUSE_PEER_AWAY)
                self.outbox.resume(now, reason=PAUSE_PEER_ABSENT)
                self._send_hello(now)
            return
        if not self.peer_present:
            return
        self.peer_present = False
        if str(vendor_reason) in EXPLICIT_VENDOR_LEAVES:
            # Tentative departure: 35s reentry grace pauses heartbeat-death detection and delivery timers.
            self.liveness.on_peer_vendor_left(now)
            self.outbox.pause(now, reason=PAUSE_PEER_AWAY)
        else:
            self.liveness.on_peer_vendor_timeout(now)

    def _send_hello(self, now: float) -> None:
        if self._hello_sent:
            self.outbox.resend_hello(now)
            return
        creds = self.creds
        if creds is None:
            return
        self._hello_sent = True
        self.outbox.send({
            "t": "hello", "v": 1, "ticket": creds.identity_ticket,
            "caps": {"video": bool(self.video_ok), "tier": creds.tier, "proto": 1,
                     "app_version": cr._app_version()[:16], "crop": self.crop},
            "lang": (self.lang_tag() or "und")[:16], "jti_reuse": False,
        }, now=now)

    def _announce_view(self) -> None:
        """Data-channel ``state{hidden, crop, tier}`` when this side's visibility changed (lossy, 1 Hz at most)."""
        creds = self.creds
        if self.peer is None or creds is None or self._last_hidden_sent == self.local_hidden:
            return
        self._last_hidden_sent = self.local_hidden
        try:
            self.outbox.send({"t": "state", "v": 1, "hidden": bool(self.local_hidden), "crop": self.crop,
                              "tier": creds.tier}, now=self.clock())
        except ValueError:
            return
        self.kick()

    def on_transport_stats(self, msg: dict) -> None:
        """iframe ``stats``: kept for ``GET /state`` and forwarded to the peer as data-channel ``stats``."""
        global _vp8_next_visit
        keep = ("tx_fps", "enc_fps", "tx_kbps", "rx_fps", "rx_kbps", "rtt_ms", "loss_pct", "dc_queue",
                "rx_w", "rx_h")
        self.stats = {k: msg.get(k) for k in keep if isinstance(msg.get(k), (int, float))
                      and not isinstance(msg.get(k), bool) and math.isfinite(msg.get(k)) and msg.get(k) >= 0}
        if msg.get("softenc_overloaded") is True:
            _vp8_next_visit = True
        if self.peer is None or not self.ready_exchanged or self.finalizing:
            return
        wire = {"rx_fps": float, "rx_kbps": int, "rtt_ms": int, "loss_pct": float, "rx_w": int, "rx_h": int}
        if not all(k in self.stats for k in wire):
            return
        payload = {"t": "stats", "v": 1, **{k: cast(self.stats[k]) for k, cast in wire.items()}}
        if msg.get("qlr") in ("none", "bandwidth", "cpu", "other"):
            payload["qlr"] = msg["qlr"]
        try:
            self.outbox.send(payload, now=self.clock())
        except ValueError:
            return
        self.kick()

    def bind_replay_frames(self) -> list[dict]:
        """Frames a freshly bound display socket must get again (PR-09b ``visit_bind``, §4.5).

        A host still waiting for its guest gets ``invite_ready`` (with the
        invite code, only on bound sockets); a host whose family has not
        answered yet gets the same ``visit_invite`` (original deadline).
        """
        creds = self.creds
        frames: list[dict] = []
        if self.side == "host" and self.phase == PHASE_INVITE_READY and creds is not None:
            frames.append({"type": "visit_state_change", "action": PHASE_INVITE_READY, "side": self.side,
                           "visit_id": self.visit_id, "invite_code": creds.invite_code,
                           "invite_expires_at": creds.invite_expires_at, "transport": creds.transport,
                           "ts": self.wall()})
        if self.side == "host" and self.phase == PHASE_AWAITING and self._invite_frame is not None:
            frames.append(dict(self._invite_frame))
        return frames

    def replay_for_bind(self) -> None:
        """Queue :meth:`bind_replay_frames` on the ordered display queue (``visit_bind``).

        Taken and queued synchronously when the socket binds: frames queued
        earlier go out first, frames of later transitions after, so an older
        snapshot never lands behind a newer phase.
        """
        for frame in self.bind_replay_frames():
            self._post_display(frame)

    # -- Media --

    def media_snapshot(self) -> dict:
        """The full ``media`` state of this phase (§4.3; never ``true`` before ``ready``)."""
        snap: dict[str, Any] = {"crop": self.crop, "ladder": self.ladder}
        if self.side == "guest":
            snap["publish"] = bool(self.ready_exchanged and self.video_ok)
        else:
            peer_video = bool(self.peer is not None and self.peer.video)
            snap["subscribe"] = bool(self.ready_exchanged and peer_video and self.video_ok)
            if self.peer is not None:
                snap["peer_vid"] = self.peer.vid
                snap["peer_crop"] = self.room.peer_crop if self.room is not None else self.peer.crop
        return snap

    async def send_media(self) -> bool:
        return await self.transport.send({"type": "media", **self.media_snapshot()})

    # -- Timing --

    async def _pump_loop(self) -> None:
        """Flush the outbox, acks and heartbeats; wakes on :meth:`kick` or every 250 ms."""
        # Stopped pumps never resume: if cancellation is swallowed by uncooperative writes returning normally, do not flush deregistered runtimes.
        # Check _pump_stop, set only when cancelling pumps, not _terminated set at shutdown entry; subsequent
        # closing lines and peer acknowledgments still require pumping.
        while not self._pump_stop:
            try:
                await asyncio.wait_for(self._kick_event.wait(), _PUMP_IDLE_S)
            except asyncio.TimeoutError:
                pass  # Without kicks, flush at ordinary idle intervals.
            self._kick_event.clear()
            try:
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - One send failure cannot stop all visit outbound work.
                logger.warning("visit %s: pump failed: %r", self.visit_id[:6], exc)

    async def flush(self, *, force_ack: bool = False) -> None:
        """Send what the outbox releases now (plus the owed ``ack`` and a due heartbeat).

        ``force_ack`` sends an owed ``ack`` without waiting for the coalescing
        window (right before the iframe is stopped).
        """
        now = self.clock()
        ack = self.sequencer.poll_ack(now, force=force_ack)
        if ack is not None and ack > 0:
            if self.outbox.leave_done(now):
                # Local leave completed, so queues emit no frames and enqueuing loses delivery while clearing debt. Send peer-leave ack directly.
                await self.transport.send(self.outbox.final_ack_frame(ack, now=now).to_ws())
            else:
                self.outbox.send({"t": "ack", "v": 1, "seq": ack}, now=now)
        if self.joined and self.peer is not None and not self.finalizing and self.liveness.heartbeat_due(now):
            lp_seen = self.room.max_lp_seen if self.room is not None else 0
            self.outbox.send({"t": "hb", "v": 1, "lp_seen": lp_seen, "crop": self.crop,
                              "hidden": bool(self.local_hidden)}, now=now)
        frames = self.outbox.due(now)
        for frame in frames:
            if await self.transport.send(frame.to_ws()):
                try:
                    self.on_frame_sent(frame)
                except Exception as exc:  # noqa: BLE001 - Accounting errors must neither drop nor leave unrolled-back later frames in this batch.
                    logger.warning("visit %s: on_frame_sent failed: %r", self.visit_id[:6], exc)
            else:
                # Failed writes immediately retry required frames; failed initial sends do not start leave grace/wrap_up timers.
                self.outbox.write_failed(frame, now=self.clock())
        if self.outbox.delivery_failed and not self.finalizing:
            self.request_finalize("delivery_failed")

    def on_frame_sent(self, frame: Any) -> None:
        """Bookkeeping after an outbox frame went out (the pump, or the page re-entry flush)."""
        now = self.clock()
        if frame.seq:
            # First actual writes start leave grace/delivery timers, whether initial delivery or retransmission.
            self.outbox.written(frame, now=now)
        if frame.seq or frame.t == "hb":
            self.liveness.on_message_sent(now)
        if frame.t == "wrap_up" and self.room is not None:
            # Report retransmits too: initial sends may target displaced connections; this retry may be first actual delivery.
            # Step timers start on first successful write only; later retries never postpone them, enforced by room.
            self.room.on_wrap_up_sent(frame.payload.get("ph"), now)

    async def tick(self) -> None:
        """Timers of this visit; called by :func:`visit_sweep_loop` every ``VISIT_SWEEP_INTERVAL_S``."""
        if self.finalizing:
            return
        now = self.clock()
        if not self.host.is_current():
            self.request_finalize("manager_replaced")
            return
        # Check liveness before connection deadlines: reload capability timers cap at absolute remaining grace, so simultaneous
        # expiry means local_page_lost, not unsupported visit capabilities.
        verdict = self.liveness.tick(now)
        if verdict is not None:
            self.request_finalize(self.leave_verdict_reason(verdict), peer_reason=self.pending_peer_reason)
            return
        if self._preflight_deadline is not None and now >= self._preflight_deadline:
            self.request_finalize("unsupported", status_details={"reason": "preflight_timeout"})
            return
        if self._sdk_deadline is not None and now >= self._sdk_deadline:
            self.request_finalize("unsupported", status_details={"reason": "sdk_timeout"})
            return
        if self._join_deadline is not None and now >= self._join_deadline and not self.joined:
            # Already joined but writing bounded _JOURNAL_OPEN_MAX_S headers: never end for room-entry timeout.
            self.request_finalize("relay_lost")
            return
        if self._accept_deadline is not None and now >= self._accept_deadline and not self.ready_exchanged:
            self.request_finalize("declined")
            return
        if (self.grant is not None and self.joined and self._renew_task is None
                and (self._renew_retry_at is None or now >= self._renew_retry_at) and self.grant.refresh_due()):
            self._renew_task = self.spawn(self._renew_grant())
        if self.room is not None:
            self.apply_effects(self.room.on_tick(now))
            if self.finalizing:
                return
            self._check_duration(now)
        if self.spool is not None and self.spool.fsync_due(now):
            self.spawn(self.spool.fsync(now))

    def _check_duration(self, now: float) -> None:
        if self.started_at_mono is None or self.room is None:
            return
        elapsed = now - self.started_at_mono
        if elapsed >= VISIT_MAX_DURATION_S:
            self.request_finalize("max_duration")
            return
        if not self._ending_soon_sent and elapsed >= VISIT_MAX_DURATION_S - VISIT_ENDING_SOON_S:
            self._ending_soon_sent = True
            ends_at = (self.started_at_wall or self.wall()) + VISIT_MAX_DURATION_S
            self.spawn(self.push("ending_soon", ends_at=ends_at))
        if not self._time_up_sent and elapsed >= VISIT_MAX_DURATION_S - VISIT_TIME_UP_WRAP_UP_S:
            self._time_up_sent = True
            self.apply_effects(self.room.on_time_up(now))
            return
        if (self.room.phase == "active" and self.last_text_at is not None and not self.room.peer_hidden
                and now - self.last_text_at >= VISIT_IDLE_TIMEOUT_S):
            self.request_finalize("idle_timeout")

    async def _renew_grant(self) -> None:
        try:
            refreshed = await self.grant.ensure_fresh()  # type: ignore[union-attr]
            if refreshed and not self.finalizing:
                # Visit finalized while awaiting Servers: do not send afterward, behind leave/stop to the page.
                await self.transport.send(self._credentials_message(refresh=True))
            self._renew_failures = 0
            self._renew_retry_at = None
        except cr.VisitRoomEnded:
            self.request_finalize("kicked")
        except Exception as exc:  # noqa: BLE001 - All server/network/page-write failures back off.
            logger.warning("visit %s: vendor grant renewal failed (%s)", self.visit_id[:6],
                           getattr(exc, "code", None) or type(exc).__name__)
            # Back off transient errors rather than retry every tick, amplifying outages with dozens of requests per visit/minute.
            self._renew_failures += 1
            delay = min(_RENEW_RETRY_MAX_S, _RENEW_RETRY_BASE_S * 2 ** (self._renew_failures - 1))
            self._renew_retry_at = self.clock() + delay
        finally:
            self._renew_task = None

    # -- Reception --

    async def accept(self, accept: bool) -> tuple[int, dict]:
        """``POST /rooms/{visit_id}/accept`` (host): activate first, ``ready`` last."""
        if self.side != "host" or self.phase != PHASE_AWAITING or self.peer is None:
            return 404, {"error": "no_pending_invite"}
        if self.accepted is not None:
            return 409, {"error": "already_decided"}
        self.accepted = bool(accept)
        if not accept:
            self.request_finalize("declined")
            return 200, {"ok": True}
        self._accept_deadline = None
        # Accepted with acceptance deadlines removed: activation+ready must finish despite HTTP cancellation/page disconnection,
        # or visits neither start nor retain rejection deadlines, locking routes forever. Runtime owns tasks and cancels at cleanup.
        accepting = self.spawn(self._accept_flow(), name="accept")
        await asyncio.shield(accepting)
        return 200, {"ok": True}

    async def _accept_flow(self) -> None:
        try:
            await asyncio.wait_for(self.activate(), VISIT_ACTIVATION_ALLOWANCE_S)
        except asyncio.TimeoutError:
            logger.warning("visit %s: activation exceeded its allowance", self.visit_id[:6])
            self.request_finalize("declined")
            return
        except Exception as exc:  # noqa: BLE001 - Failed isolated sessions mean no visit speech.
            logger.warning("visit %s: activation failed: %r", self.visit_id[:6], exc)
            self.request_finalize("llm_error")
            return
        if self.finalizing:
            return
        # When ready sends, local handling of peer lines must already work.
        self.outbox.send({"t": "ready", "v": 1}, now=self.clock())
        self.ready_exchanged = True
        self.kick()
        await self.send_media()
        await self._after_start()

    async def on_ready(self) -> None:
        """Guest: the host's ``ready`` arrived; activate before the first turn."""
        if self.side != "guest" or self.ready_exchanged or self._activation is not None or self.finalizing:
            return
        self.liveness.on_ready(self.clock())
        # ready is reliable: on_recv owns its protected batch delivery; cancelled receive processing still completes
        # ready, and subsequent receives wait, preventing later lines being dropped as early arrivals.
        await self._ready_flow()

    async def _ready_flow(self) -> None:
        # Activate synchronously in receives so later lines have rooms, with bounded waits to avoid blocking heartbeats/leave.
        try:
            await asyncio.wait_for(self.activate(), VISIT_ACTIVATION_ALLOWANCE_S)
        except asyncio.TimeoutError:
            logger.warning("visit %s: activation exceeded its allowance", self.visit_id[:6])
            self.request_finalize("llm_error")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("visit %s: activation failed: %r", self.visit_id[:6], exc)
            self.request_finalize("llm_error")
            return
        if self.finalizing:
            return
        self.ready_exchanged = True
        await self.send_media()
        await self._after_start()

    async def _after_start(self) -> None:
        # Earlier send_media/page writes may stall; if finalization began meanwhile, never mark the page active again.
        if self.finalizing:
            return
        await self.push("started", peer_crop=self.room.peer_crop if self.side == "host" and self.room else None)
        if self.finalizing:
            return
        if self.side == "guest":
            await self.push("departed")
            if self.finalizing:
                return
        self.start_opening_line()

    # -- Activation --

    async def activate(self) -> None:
        """The single activation: isolated session, proactive parked, spool, ``VisitRoom`` active."""
        if self.activated or self._activation is not None:
            return
        # Independent task: finalization awaits termination through _settle_activation before closing spool/sessions.
        task = asyncio.ensure_future(self._activate())
        self._activation = task
        try:
            await asyncio.wait([task])
        except asyncio.CancelledError:
            task.cancel()  # Caller timeout/cancellation cancels activation too.
            raise
        if not task.cancelled():
            task.result()  # Propagate activation failures to callers; shutdown cancellation returns silently, with callers observing finalizing.

    async def _settle_activation(self) -> None:
        """Let an in-flight activation notice ``finalizing`` and stop before teardown (bounded)."""
        task = self._activation
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), VISIT_ACTIVATION_ALLOWANCE_S)
        except asyncio.TimeoutError:
            task.cancel()
        except Exception:  # noqa: BLE001 - Initiators handle activation failures; await completion only here.
            return

    async def _activate(self) -> None:
        from config.prompts.prompts_visit import build_visit_instructions, get_family_neutral_term
        from main_logic.visit.room import VisitRoom
        from main_logic.visit.sanitize import neutralize_display_name
        from main_logic.visit.subjects import derive_short_code, resolve_visit_recall_subjects
        from main_routers.visit_router.local_context import prompt_lang, protected_display_names

        peer = self.peer
        creds = self.creds
        if peer is None or creds is None:
            raise RuntimeError("activation before the peer verified")
        settings = await self.deps.settings()
        if self.finalizing:
            return
        self.memory_enabled = bool(settings.get("visitMemoryEnabled", VISIT_MEMORY_DEFAULT))
        self.voice = VoiceState(enabled=bool(settings.get("visitVoiceEnabled", VISIT_VOICE_DEFAULT)))
        self.lang = prompt_lang()
        ctx = await self.deps.character_context()
        if self.finalizing:
            return
        self.family_names = tuple(getattr(ctx, "family_names", ()) or ())
        self.char_names = tuple(getattr(ctx, "char_names", ()) or ())
        protected = protected_display_names(self.lang, self.family_names, self.char_names)
        peer.display = neutralize_display_name(
            peer.raw_display, protected_names=protected,
            generic_label=self.speaker_label("peer_cat"), short_code=derive_short_code(peer.uid),
        )
        subjects = resolve_visit_recall_subjects({
            "own_uid": creds.visit_uid, "peer_uid": peer.uid, "peer_char_tag": peer.char_tag,
        })
        memory_block = ""
        try:
            memory_block = await self.deps.memory_block(
                self.lanlan_name, own_uid=creds.visit_uid, own_char=self.lanlan_name, peer_uid=peer.uid,
                peer_display=peer.raw_display, subjects=subjects, lang=self.lang,
                own_char_uid=self.character_uid, config_dir=self.config_dir,
                handoff=self._summary_handoff, protected_names=protected,
                generic_label=self.speaker_label("peer_cat"),
            )
        except Exception as exc:  # noqa: BLE001 - Missing memory blocks mean empty opening context.
            logger.warning("visit %s: memory block unavailable: %s", self.visit_id[:6], type(exc).__name__)
        instructions = build_visit_instructions(
            self.lanlan_name, self.side, self.lang, persona_text=self.persona_text,
            memory_block=memory_block, peer_display=peer.display,
        )
        self.neutral_term = get_family_neutral_term(self.lang)
        if self.finalizing:
            return
        # Once session/spool attaches to self, finalization owns closure after activation settles.
        self.session = await self.deps.create_session(self.lanlan_name, self.side,
                                                      instructions=instructions, lang=self.lang)
        if self.finalizing:
            if self._terminated:
                # Activation outlasted finalization waits and teardown closed before clients attached; late clients close themselves.
                self._close_session_in_background()
            return
        self.host.park_proactive()
        await self._open_spool(subjects)
        if self.finalizing:
            chain_pending = self._files_deferred and not self._deferred_files_done
            if self._files_done and not chain_pending and self._seal_settled() and not self._shutdown_started \
                    and not self._spool_finalized and self.spool is not None:
                # Spool absent during sealing: late spool closes itself and marks finalized.
                reason = self.finalize_reason or "route_end"
                spawn_visit_background(self.character_uid, lambda: self._finalize_late_spool(reason))
            return
        self.room = VisitRoom(self.side, peer_crop=peer.crop, rng=self.deps.rng,
                              reply_gap_s=self.deps.reply_gap_s)
        self.activated = True
        now = self.clock()
        self.started_at_mono = now
        self.started_at_wall = self.wall()
        self.last_text_at = now
        self._set_phase(PHASE_ACTIVE)

    async def _summary_handoff(self) -> None:
        from main_logic.visit.memory_commit import last_summary_handoff

        creds = self.creds
        if creds is None or self.peer is None:
            return
        await last_summary_handoff(
            self.config_dir, own_uid=creds.visit_uid, own_char_uid=self.character_uid,
            peer_uid=self.peer.uid, start_summary=self._start_previous_summary,
            is_live=is_visit_live, opening_visit_id=self.visit_id,
        )

    def _start_previous_summary(self, spool: Any) -> Awaitable[Any]:
        family = self.family_names
        return spawn_visit_background(
            self.character_uid, lambda: self.deps.commit_summary(spool, family_names=family),
        )

    async def _open_spool(self, subjects: list) -> None:
        from main_logic.visit.spool import VisitSpool, new_state
        from main_logic.visit.subjects import PeerRoster, derive_pair_id, derive_peer_char_id

        creds = self.creds
        peer = self.peer
        if creds is None or peer is None:
            return
        try:
            pair_id = derive_pair_id(creds.visit_uid, peer.uid)
            peer_char_id = derive_peer_char_id(peer.uid, peer.char_tag)
        except Exception as exc:  # noqa: BLE001 - Cannot derive peer subject: omit visit memory.
            logger.warning("visit %s: peer subjects not derivable: %s", self.visit_id[:6], type(exc).__name__)
            self.memory_enabled = False
            return
        spool = VisitSpool(self.config_dir, self.visit_id)
        memory_on = bool(self.memory_enabled and subjects)
        # Threaded state writes survive waiter cancellation; use separate tasks for finalization to settle after activation cancellation.
        writing = asyncio.ensure_future(spool.write_state(new_state(
            own_uid=creds.visit_uid, own_char=self.lanlan_name, own_char_uid=self.character_uid,
            pair_id=pair_id, peer_uid=peer.uid, peer_char_id=peer_char_id, memory_enabled=memory_on,
        )))
        writing.add_done_callback(lambda t: t.cancelled() or t.exception())
        try:
            await asyncio.shield(writing)
            if memory_on:
                await spool.open({
                    "v": 1, "visit_id": self.visit_id, "role": self.side, "own_uid": creds.visit_uid,
                    "own_char": self.lanlan_name, "own_char_uid": self.character_uid, "pair_id": pair_id,
                    "peer_uid": peer.uid, "peer_char_id": peer_char_id, "peer_char_tag": peer.char_tag,
                    "started_at": self.wall(), "lang": self.lang or "und",
                }, now=self.clock())
        except asyncio.CancelledError:
            # Cancellation during state writing/spool opening from timeout/finalization may still leave persisted state because workers
            # finish. Attach spool for finalization marking, preventing startup backfill misclassifying it as a crash.
            # Also reset formerly enabled memory to off: no transcript will exist, or startup retries nonexistent transcripts forever without settlement.
            # If state was never written, finalization writes fail and log only.
            self.memory_enabled = False
            self.spool = spool
            if not writing.done():
                # Finalization update_state cannot run first against absent files; later initial writes would leave unfinalized state.
                self._state_writing = writing
            if memory_on:
                self._memory_off_unsaved = True
            raise
        except Exception as exc:  # noqa: BLE001 - Spool unavailable: keep visiting without visit memory.
            logger.warning("visit %s: spool not opened: %s", self.visit_id[:6], type(exc).__name__)
            if memory_on:
                # State already said memory enabled: reset off or startup submits nonexistent transcripts forever.
                # If the same disk fault prevents resetting, remember it and include with finalized writes.
                try:
                    await spool.update_state(memory_enabled=False)
                except Exception as state_exc:  # noqa: BLE001
                    logger.warning("visit %s: memory-off state not written: %s", self.visit_id[:6],
                                   type(state_exc).__name__)
                    self._memory_off_unsaved = True
            memory_on = False
        self.memory_enabled = memory_on
        self.spool = spool
        if not memory_on:
            return
        try:
            await PeerRoster(self.config_dir, own_uid=creds.visit_uid).upsert(
                peer.uid, self.lanlan_name, pair_id=pair_id, peer_char_id=peer_char_id,
                char_tag=peer.char_tag, display_name=peer.display, now=self.wall(),
                visit_id=self.visit_id,
            )
        except Exception as exc:  # noqa: BLE001 - Rosters are auxiliary; failures cannot affect opened transcripts.
            logger.warning("visit %s: peer roster not updated: %s", self.visit_id[:6], type(exc).__name__)

    # -- Input takeover, registry entry --

    async def _voice_dispatcher(self, _lanlan: str, _transcript: str, **_kwargs: Any) -> bool:
        await self._voice_unavailable()
        return True

    async def _voice_unavailable(self) -> None:
        now = self.clock()
        if now - self._last_voice_status >= _VOICE_STATUS_THROTTLE_S:
            self._last_voice_status = now
            await self.status("VISIT_VOICE_UNAVAILABLE")

    async def on_start_session(self, message: dict) -> bool:
        """Text: acknowledged only (no ordinary text session). Audio: refused while visiting."""
        request_id = message.get("request_id") if isinstance(message.get("request_id"), str) else None
        if message.get("input_type") == "audio":
            await self._voice_unavailable()
            await self.host.fail_session("audio", request_id)
            return True
        await self.host.ack_text_session(request_id)
        return True

    # -- Finalization --

    def request_finalize(
        self, reason: str, *, peer_reason: Optional[str] = None, status_details: Optional[dict] = None,
    ) -> bool:
        """Flip to ``ending`` now and start the exit flow (idempotent, never awaits).

        Once the takeover is released ``is_visit_route_active`` is false (the
        family's typing goes to ordinary chat; before that it is refused);
        the slot stays locked until the exit flow completed.
        """
        if self._exit_task is not None or self._terminated:
            # Shutdown started/already finalized: start no concurrent finalization racing shutdown file closure.
            return False
        if reason not in FINALIZE_REASONS and reason not in ABORT_REASONS:
            logger.warning("visit %s: unknown finalize reason %r, using route_end", self.visit_id[:6], reason)
            reason = "route_end"
        self.finalize_reason = reason
        self.peer_reason = peer_reason
        self.status_code = STATUS_FOR_REASON.get(reason)
        self.status_details = dict(status_details or {})
        self.finalize_at = self.clock()
        self._was_phase = self.phase
        self._set_phase(PHASE_ENDING)
        self._exit_task = asyncio.ensure_future(self._exit_flow())
        return True

    @property
    def exit_task(self) -> Optional[asyncio.Task]:
        return self._exit_task

    def _sends_leave(self) -> Optional[str]:
        # Send only to potential recipients: verified peers or joined peers just rejected verification, so they end immediately too.
        if self.peer is None and not self.peer_present:
            return None
        return leave_reason_for(self.finalize_reason or "", side=self.side, done_received=self.done_received)

    async def _exit_flow(self) -> None:
        reason = self.finalize_reason or "route_end"
        try:
            await self._exit_steps(reason)
        except Exception as exc:  # noqa: BLE001 - Every finalization step is best effort; always deregister at the end.
            logger.error("visit %s: exit flow failed: %r", self.visit_id[:6], exc)
        finally:
            if self._shutdown_started:
                # Shutdown cancelled finalization: shutdown() handles files/takeover/deregistration in file-first order;
                # never run concurrent teardown closing/deregistering before archival.
                return
            if not self._files_done:
                # Prior steps failed: transcript/spool closure remains mandatory; shutdown handles its own path.
                try:
                    await self._finish_files(reason)
                except Exception as exc:  # noqa: BLE001
                    logger.error("visit %s: files not finalized: %r", self.visit_id[:6], exc)
            if not self._handback_started:
                # Intermediate failure/cancellation stops waiting for unqueued segments but still returns callbacks, neither dropping nor withholding forever.
                if self.handoff is not None:
                    self.handoff.abandon()
                self._start_handback()
            await self._teardown()

    def _start_handback(self) -> None:
        self._handback_started = True
        _detach(self._hand_back_callbacks())

    async def _exit_steps(self, reason: str) -> None:
        # 1. Finish current local line first, placing text{final} before leave and into this transcript.
        await self.close_current_line("visit_end")
        # 2. Close data channels in background: drain -> leave -> retransmission grace, blocking no later steps.
        closing = self._closing_task = asyncio.ensure_future(self._close_channel(reason))
        # 3. Retain callbacks temporarily, immediately release takeover so family resumes ordinary chat.
        input_stamp = self.host.last_user_input()
        input_released = True
        if self.takeover_token is not None:
            try:
                self.hold_token = self.host.hold_callbacks(self.inbox.accept)
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit %s: callback hold failed: %s", self.visit_id[:6], type(exc).__name__)
            try:
                self.host.release_takeover(self.takeover_token)
            except Exception as exc:  # noqa: BLE001 - Retain token for teardown retry.
                logger.warning("visit %s: takeover release failed: %s", self.visit_id[:6], type(exc).__name__)
                input_released = False
            else:
                # False means token no longer owns input; takeover already moved, counting as handback.
                self.takeover_token = None
        # Push ending only after takeover release, because the page restores ordinary input immediately and visit routing must not block it;
        # before release, finalizing rejects visit submissions with request_id for undelivered marking/text restoration.
        if input_released:
            await self.push(PHASE_ENDING, reason=reason)
        # Release failed: do not push ending; remain visit UI, rejecting/finalizing submissions until teardown retries then sends ended.
        if self.activated:
            self.handoff = inbox_handoff.InboxHandoff(self.visit_id, finalize_at=self.clock(), clock=self.clock)
        # 4. Optional homecoming line, abandoned if family speaks first; failure cannot block archival/handback.
        if self.activated:
            try:
                await self.say_ritual(reason, input_stamp=input_stamp)
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit %s: home-coming line failed: %r", self.visit_id[:6], exc)
                if self.handoff is not None:
                    self.handoff.skip("ritual")  # No playback will occur; handback stops waiting.
        await self._settle_activation()
        # 5. Await channel closure; iframe must remain to send leave until then.
        await asyncio.wait([closing], timeout=_CLOSE_WAIT_S)
        if not closing.done() and self.outbox.reserved_bytes:
            # Accepted family lines still persisting with reservations must enqueue before leave; teardown cannot remove pump/transport
            # prematurely or persisted/room-updated lines never reach peers. Wait additionally, bounded against stuck disk.
            await asyncio.wait([closing], timeout=_RESERVED_SEND_MAX_S)
        if not closing.done():
            # Closure exceeded deadline: stop receives first, unregister transport/cancel closure, then seal; otherwise late retransmitted
            # peer lines get acknowledgments after archival but never enter transcripts.
            logger.warning("visit %s: channel close did not finish; retiring it", self.visit_id[:6])
            closing.cancel()  # Its finally sends owed acknowledgments/stop{reason} before deregistration.
            await asyncio.wait([closing], timeout=_SHUTDOWN_TASK_WAIT_S)
            # Deregister again idempotently so blocked finally sends/cancellation elsewhere cannot permit receives before archival.
            unregister_transport_session(self.transport)
        flushed = await self._flush_display(_DISPLAY_FLUSH_S)  # Display the full farewell before ended.
        self._ended_published = True  # Queue no more display frames after this point; complete late-finalization lines enter transcripts only.
        if not flushed:
            # Stuck page delivery stops display queues; GET /state replays transcripts. ended neither writes concurrently nor waits behind them.
            await self._retire_display(_DISPLAY_FLUSH_S)
        if self.takeover_token is not None:
            # Retry failed early takeover release before ended; seeing it restores page input, which cannot remain visit-owned
            # and reject until teardown.
            try:
                self.host.release_takeover(self.takeover_token)
            except Exception as exc:  # noqa: BLE001 - Retain token for teardown retry; managers still holding takeover need its sole token.
                logger.warning("visit %s: takeover release failed again before ended: %s",
                               self.visit_id[:6], type(exc).__name__)
            else:
                self.takeover_token = None
        await self.push(PHASE_ENDED, reason=reason, peer_reason=self.peer_reason)
        if self.status_code is not None:
            await self.status(self.status_code, reason=self.status_details.get("reason"),
                              retry_after_s=self.status_details.get("retry_after_s"))
        self._set_phase(PHASE_ENDED)
        # 6/7. Seal transcript .upload.json first -> spool finalize -> background digest/last-summary work.
        await self._finish_files(reason)
        # 8. Optional debrief only after actual visits; failures still permit handback.
        if self.activated:
            from main_routers.visit_router.debrief import run_debrief

            try:
                await run_debrief(self, input_stamp=input_stamp)
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit %s: debrief failed: %r", self.visit_id[:6], exc)
                if self.handoff is not None:
                    self.handoff.skip("debrief")
        # 9. Return VisitInbox after ritual/debrief playback or fallback deadline; independent tasks without character locks.
        # After route pop, inbox_handoff still forwards ended events to it.
        self._start_handback()

    async def _finish_files(self, reason: str) -> None:
        """Seal the transcript, finalize the spool, start the memory commits (once; mandatory)."""
        if self._files_done:
            return
        self._files_done = True
        self._files_gen = _stop_gen
        if await self.seal_and_finalize(reason):
            self._spawn_memory_commits()

    async def _close_channel(self, reason: str) -> None:
        """Drain (normal ends), ``leave``, its resend window; then stop the iframe and drop the transport."""
        leave_reason = self._sends_leave()
        try:
            if leave_reason is not None and self.transport_alive():
                now = self.clock()
                if reason not in ("delivery_failed", "shutdown"):
                    deadline = self.outbox.begin_drain(now)
                    while not self.outbox.drain_done(self.clock()) and self.clock() < deadline:
                        self.kick()
                        await asyncio.sleep(0.1)
                # Accepted family lines persisting with reservations must precede leave; wait until enqueue/reservation abandonment.
                # No limit here: finalization bounds this close task by _CLOSE_WAIT_S plus
                # _RESERVED_SEND_MAX_S while reserved, then cancels; shutdown cancels too.
                while self.outbox.reserved_bytes:
                    await asyncio.sleep(0.05)
                try:
                    self.outbox.send({"t": "leave", "v": 1, "reason": leave_reason}, now=self.clock())
                except ValueError as exc:
                    # Without enqueue, no retransmission window can occur because leave_done never becomes true.
                    logger.warning("visit %s: leave not queued: %s", self.visit_id[:6], exc)
                else:
                    self.kick()
                    while not self.outbox.leave_done(self.clock()):
                        self.kick()
                        await asyncio.sleep(0.1)
        finally:
            if self.peer is None:
                # Unmatched hosts must revoke invitation/quota reservations. Use finally so drain/leave/retry stalls
                # and deadline cancellation still initiate revocation.
                self._cancel_room_once()
            try:
                try:
                    # Send owed peer-leave acks directly after peer_left/simultaneous finalization, since queues stop after local leave,
                    # avoiding unnecessary peer retransmission waits.
                    ack = self.sequencer.poll_ack(self.clock(), force=True)
                    if ack is not None and ack > 0:
                        await self.transport.send(self.outbox.final_ack_frame(ack, now=self.clock()).to_ws())
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await self.transport.send({"type": "stop", "reason": reason})
                except Exception:  # noqa: BLE001
                    pass
            finally:
                # Even cancelled sends must reach deregistration/outbox closure.
                unregister_transport_session(self.transport)
                try:
                    await self.outbox.close()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("visit %s: outbox close failed: %s", self.visit_id[:6], type(exc).__name__)

    def _cancel_room_once(self) -> None:
        # Hosts ending before peer verification may still have redeemable invites; cancel rooms asynchronously without charging peers.
        # Finalization and late credentials each invoke cancellation, but send once.
        creds = self.creds
        if self.side != "host" or creds is None or self._room_cancel_sent:
            return
        self._room_cancel_sent = True
        self._room_cancel_task = self.spawn(self.deps.cancel_room(
            self.visit_id, invite_expires_at=creds.invite_expires_at, account=creds.account))
        self._room_cancel_at = asyncio.get_running_loop().time()
        # Cancellation may outlive deregistered runtimes; register separately so stop_all can bound waiting at shutdown.
        _room_cancels.add(self._room_cancel_task)
        self._room_cancel_task.add_done_callback(_room_cancels.discard)

    def transport_alive(self) -> bool:
        from main_routers.visit_router.transport_ws import is_transport_attached

        # Verified peer hello before local joined proves the data channel works; send reliable leave even without joined.
        return (self.joined or self.peer is not None) and is_transport_attached(self.visit_id, self.side)

    async def _settle_journal_open(self, timeout: Optional[float] = None) -> None:
        # Bound header-write waits; unfinished writes get a background chain sealing immediately on late completion,
        # except shutdown, which starts no chain and leaves journals for startup backfill.
        opening = self._journal_opening
        if opening is None or opening.done():
            return
        await asyncio.wait([opening], timeout=_JOURNAL_OPEN_MAX_S if timeout is None else timeout)
        if not opening.done() and self._shutdown_started:
            # Shutdown starts no unbounded archival chains and actively cancels header tasks.
            self._abandon_journal_open(opening)
            return
        if not opening.done():
            reason = self.finalize_reason or "shutdown"
            ended_at = self.wall()  # Use current finalization time; disk stalls must not inflate visit duration.

            # Register UID now before header persistence; background work mutates spool/memory and must block rename/delete until complete.
            # Unbounded disk waits conservatively block rename/delete for this process if disks stay stuck.
            gen = _stop_gen
            chain = spawn_visit_background(self.character_uid,
                                           lambda: self._seal_after_header(opening, reason, ended_at, gen))

            def on_chain_done(task: asyncio.Task) -> None:
                # stop_all cancels chains at any step, even name lookup before _seal_after_header;
                # deregistered runtimes have nobody else to cancel headers. Abandon/cancel as in shutdown,
                # preventing wait-for-disk cleanup hanging event-loop destruction.
                if task.cancelled() and not opening.done():
                    self._abandon_journal_open(opening)

            chain.add_done_callback(on_chain_done)

    def _abandon_journal_open(self, opening: asyncio.Future) -> None:
        # After abandon_open, cancelled header tasks finish immediately without waiting for stuck writes, avoiding loop-level hangs.
        # Writer threads append buffered records after file creation. Thread-pool writers are not daemon threads;
        # interpreter exit still awaits stuck disks, as for all thread-pool writes. Startup backfills remaining journals.
        try:
            self.journal.abandon_open(self._backlog_stream_records())
        except Exception as exc:  # noqa: BLE001 - Even conversion failure must abandon/cancel so later shutdown steps execute.
            logger.warning("visit %s: buffered records not kept at shutdown: %s", self.visit_id[:6],
                           type(exc).__name__)
            self.journal.abandon_open()
        self._journal_backlog = []
        opening.cancel()

    async def _seal_after_header(self, opening: asyncio.Future, reason: str, ended_at: float, gen: int) -> None:
        await asyncio.wait([opening])  # Spawn-time done callbacks abandon headers on cancellation.
        # Header creation failed, consuming its exception here; no journal to seal, but spool still finalizes.
        header_ok = not opening.cancelled() and opening.exception() is None
        await self._seal_late_journal(reason, ended_at, header_ok=header_ok, gen=gen)

    def _not_before_start(self, ts: Any) -> float:
        # Prejoin usage/errors may predate speech-based started_at; clamp during backfill so records never precede
        # started_at and ended_at from final records never predates start.
        start = self._journal_started_at
        return float(ts) if start is None else max(float(ts), start)

    def _backlog_stream_records(self) -> list[dict]:
        """The buffered records in the upload stream's own format (written after the header at shutdown)."""
        from main_routers.visit_router.transcript_upload import USAGE_KEYS

        records: list[dict] = []
        for record in self._journal_backlog:
            kind = record.get("kind")
            if kind == "line":
                records.append({"kind": "line", "lp": int(record["lp"]), "side": record["side"],
                                "from": record["speaker"], "ts": float(record["ts"]), "text": str(record["text"]),
                                "truncated": bool(record["truncated"])})
            elif kind == "usage":
                delta = {k: int(v) for k, v in (record.get("d") or {}).items()
                         if k in USAGE_KEYS and isinstance(v, int) and not isinstance(v, bool) and v > 0}
                if delta:
                    records.append({"kind": "usage", "ts": self._not_before_start(record["ts"]), "d": delta})
            elif kind == "anomaly":
                records.append({"kind": "anomaly", "ts": self._not_before_start(record["ts"])})
        return records

    def _flush_journal_backlog(self) -> None:
        """The upload header landed: record what was buffered meanwhile (lines, usage, anomalies), in order.

        Synchronous: every record is in the in-memory copy before this
        returns (the stream writes follow on the writer thread), so a seal
        right after it -- or interleaved with nothing at all -- has them all.
        Every seal path calls it first; it also runs when the header lands.
        """
        if self._journal_failed():
            # Failed headers still preserve backlog lines in in-memory transcript for /state/debrief, but usage/errors have no journal.
            backlog, self._journal_backlog = self._journal_backlog, []
            for record in backlog:
                if record.get("kind") == "line":
                    line = {k: v for k, v in record.items() if k != "kind"}
                    try:
                        self.journal.remember_line(**line)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("visit %s: buffered line not kept: %s", self.visit_id[:6],
                                       type(exc).__name__)
            return
        while self._journal_backlog and self.journal.is_open:
            record = dict(self._journal_backlog.pop(0))
            kind = record.pop("kind")
            try:
                if kind == "usage":
                    self.journal.note_usage(record["d"], ts=self._not_before_start(record["ts"]))
                elif kind == "anomaly":
                    self.journal.note_anomaly(ts=self._not_before_start(record["ts"]))
                else:
                    self.journal.book_line(**record)
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit %s: buffered %s not recorded: %s", self.visit_id[:6], kind, type(exc).__name__)

    async def _seal_late_journal(self, reason: str, ended_at: float, *, header_ok: bool = True,
                                 gen: Optional[int] = None) -> None:
        """The upload header landed after the exit flow gave up waiting: seal, then what follows the seal.

        The spool finalize and the memory commits were held back for it
        (``seal_and_finalize`` returned False); at shutdown only the seal is
        done here and the spool is left to the startup recovery.
        """
        try:
            if header_ok:
                try:
                    self._flush_journal_backlog()
                    # _start_seal provides one archival future per visit, shared by shutdown/teardown as one disk write.
                    sealing = self._start_seal(reason, ended_at=ended_at)
                    await asyncio.wait([sealing])
                    self._take_seal(sealing)
                except Exception as exc:  # noqa: BLE001 - Sealing failure still allows attached spool closure.
                    logger.warning("visit %s: late seal failed: %s", self.visit_id[:6], type(exc).__name__)
                # Queue retry even after sealing failure; remaining .upload.jsonl reseals without waiting for restart.
                try:
                    self.deps.schedule_upload(self.visit_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("visit %s: upload not scheduled: %s", self.visit_id[:6], type(exc).__name__)
            if self._files_deferred and not self._shutdown_started and not _stopped_since(gen):
                await self._finalize_spool(reason)
                self._spawn_memory_commits()
        finally:
            self._deferred_files_done = True  # Set flags despite intermediate chain failures; late-attached spool finalizes from activation itself.

    async def seal_and_finalize(self, reason: str) -> bool:
        """Seal ``.upload.json`` first, then ``state.json.finalized`` (§3.2.6 item 22 step 3).

        The seal waits at most ``_SEAL_MAX_S`` (the wait for admitted family
        lines to be recorded included): a stalled disk must not keep the
        visit registered and its route locked. Past that the rest (spool
        finalize, memory commits, upload) follows the seal in the background,
        in the same order, and this returns False.
        """
        loop = asyncio.get_running_loop()
        seal_deadline = loop.time() + _SEAL_MAX_S

        def remaining() -> float:
            return max(0.0, seal_deadline - loop.time())

        # Accepted family lines may still persist after peer departure/disconnection, with closure not awaiting reservations; received peer batches may still process.
        # Await transcript recording, sharing one deadline with header writes/sealing.
        await self.settle_family_records(remaining())
        self._rx_closed = True  # Archival boundary: accept no new peer messages after this point.
        await self._settle_delivering(remaining())
        self._start_journal_for_backlog()
        await self._settle_journal_open(min(_JOURNAL_OPEN_MAX_S, remaining()))
        if self._header_pending():
            # Pending headers make seal() immediately return None. Delegate sealing, spool finalization, and memory commit
            # to the post-header background chain, still sealing before finalized.
            self._files_deferred = True
            return False
        self._flush_journal_backlog()  # Headers persisted while waiting: backfill accumulated records before sealing.
        sealing = self._start_seal(reason)
        await asyncio.wait([sealing], timeout=max(0.0, seal_deadline - loop.time()))
        if not sealing.done():
            logger.warning("visit %s: upload seal still writing; finishing it in the background", self.visit_id[:6])
            # Register UID, blocking rename/delete until this background spool/memory work finishes.
            gen = _stop_gen
            # Actual sealing writes already register through _start_seal, surviving outer-chain cancellation.
            spawn_visit_background(self.character_uid, lambda: self._finalize_after_seal(sealing, reason, gen))
            return False
        self._take_seal(sealing)
        finalizing = asyncio.ensure_future(self._finalize_spool(reason))
        await asyncio.wait([finalizing], timeout=_SEAL_MAX_S)
        if not finalizing.done():
            # Stuck spool persistence still allows deregistration/reservation release; memory commits start asynchronously afterward, registered by UID too.
            logger.warning("visit %s: spool still finalizing; finishing it in the background", self.visit_id[:6])
            gen = _stop_gen
            # asyncio.wait shields from outer-chain cancellation; register the task itself for bounded shutdown cancellation/wait.
            self._keep_background(finalizing)
            spawn_visit_background(self.character_uid, lambda: self._commit_after_spool(finalizing, gen))
            return False
        return True

    async def _commit_after_spool(self, finalizing: asyncio.Future, gen: int) -> None:
        await asyncio.wait([finalizing])
        if not _stopped_since(gen):
            self._spawn_memory_commits()

    def _start_seal(self, reason: str, *, ended_at: Optional[float] = None) -> asyncio.Future:
        """The seal of this visit, started once: a later caller (shutdown, teardown) sees the same write."""
        sealing = self._sealing
        if sealing is None:
            sealing = self._sealing = asyncio.ensure_future(self.journal.seal(
                reason, ended_at=self.wall() if ended_at is None else ended_at))
            sealing.add_done_callback(lambda t: t.cancelled() or t.exception())  # _take_seal retrieves results.
            # journal.seal_write starts actual writes in seal()'s first step; outer shielding prevents cancellation propagation.
            # Await that first step before module registration, covering ordinary finalization, deferred sealing, and shutdown.
            asyncio.get_running_loop().call_soon(self._keep_seal_write)
        return sealing

    def _keep_seal_write(self) -> None:
        write = self.journal.seal_write
        if write is not None and not write.done():
            # Await without cancelling: it may queue behind single-thread disk work, and cancellation would remove archival entirely, never writing .upload.json.
            _awaited_writes.add(write)
            write.add_done_callback(_awaited_writes.discard)

    async def wait_upload_sealed(self, timeout: float) -> bool:
        """Wait (bounded) until this visit's upload file is sealed; False on timeout.

        Also True once the exit flow ended with nothing left to seal (no
        upload journal was ever opened, or the seal step failed). A seal the
        exit flow handed to a background task (header still writing, a slow
        disk) is waited for even after the exit flow itself finished.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        while True:
            sealing = self._sealing
            header_pending = self._header_pending()
            if sealing is not None and sealing.done() and not header_pending:
                return True
            task = self._exit_task
            exited = (task is not None and task.done()) or self._terminated
            if exited and not self.seal_pending():
                return True
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(_HANDOFF_POLL_S)

    def seal_pending(self) -> bool:
        """The upload seal is still being written (a background chain may outlive the exit flow)."""
        sealing = self._sealing
        return ((sealing is not None and not sealing.done()) or self._header_pending()
                or (self._files_deferred and not self._deferred_files_done))

    def _seal_settled(self) -> bool:
        return not self._header_pending() and (self._sealing is None or self._sealing.done())

    def _header_pending(self) -> bool:
        opening = self._journal_opening
        return opening is not None and not opening.done()

    def _take_seal(self, sealing: asyncio.Future) -> None:
        if sealing.cancelled() or sealing.exception() is not None:
            # Sealing failed: retain journals for next startup backfill.
            logger.warning("visit %s: upload not sealed: %s", self.visit_id[:6],
                           "cancelled" if sealing.cancelled() else type(sealing.exception()).__name__)
            return
        self.sealed_doc = sealing.result()

    async def _settle_state_write(self) -> None:
        writing = self._state_writing
        if writing is not None and not writing.done():
            await asyncio.wait([writing])  # Callers impose independent finalization/shutdown deadlines.

    async def _finalize_spool_at_shutdown(self, spool: Any) -> None:
        try:
            await self._settle_state_write()
            await spool.close()
            changes: dict[str, Any] = {"finalized": "shutdown"}
            if self._memory_off_unsaved:
                changes["memory_enabled"] = False
            if self.memory_enabled and self.spool_lines > 0:
                changes.update(debrief_choice="ask_later", debrief_chip_pending=True)
            await spool.update_state(**changes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("visit %s: spool not finalized at shutdown: %s", self.visit_id[:6], type(exc).__name__)

    async def _finalize_late_spool(self, reason: str) -> None:
        # Recheck stop_all inside background tasks; shutdown can start while they resolve names after spawning.
        if _stopped_since(self._files_gen):
            return
        await self._finalize_spool(reason)

    async def _finalize_spool(self, reason: str) -> None:
        if self.spool is not None and not self._spool_finalized:
            self._spool_finalized = True
            try:
                await self._settle_state_write()
                await self.spool.close()
                changes: dict[str, Any] = {"finalized": reason}
                if self._memory_off_unsaved:
                    changes["memory_enabled"] = False  # Include earlier failed memory-disable writes during activation now.
                await self.spool.update_state(**changes)
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit %s: spool not finalized: %s", self.visit_id[:6], type(exc).__name__)

    async def _finalize_after_seal(self, sealing: asyncio.Future, reason: str, gen: Optional[int] = None) -> None:
        """The seal outlived the exit flow: finish what follows it, in order, once it lands.

        If the process started shutting down meanwhile (``stop_all``), the
        spool and the memory commits are left to the startup recovery: they
        would be cut off half-way when the event loop goes.
        """
        await asyncio.wait([sealing])
        self._take_seal(sealing)
        if _stopped_since(gen):
            return
        await self._finalize_spool(reason)
        self._spawn_memory_commits()
        if self.journal.sealed:
            try:
                self.deps.schedule_upload(self.visit_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit %s: upload not scheduled: %s", self.visit_id[:6], type(exc).__name__)

    def _spawn_memory_commits(self) -> None:
        spool = self.spool
        if spool is None or not self.memory_enabled:
            if spool is not None:
                # Memory disabled: skip digest, mark last summary handled; commit_last_summary gates itself.
                family = self.family_names
                spawn_visit_background(self.character_uid,
                                       lambda: self.deps.commit_summary(spool, family_names=family))
            return
        family = self.family_names
        chars = tuple(n for n in self.char_names if n != self.lanlan_name)

        async def commit() -> None:
            from main_logic.visit import local_chars
            from main_logic.visit.memory_commit import track_last_summary

            try:
                await self.deps.commit_region(spool, resolve_char_name=local_chars.resolve_char_name,
                                              family_names=family, local_char_names=chars)
            except Exception as exc:  # noqa: BLE001 - Resume through startup backfill.
                logger.warning("visit %s: digest failed: %r", self.visit_id[:6], exc)
            await track_last_summary(self.visit_id, self.deps.commit_summary(spool, family_names=family))

        spawn_visit_background(self.character_uid, commit)

    async def _hand_back_callbacks(self) -> None:
        handoff = self.handoff
        try:
            if handoff is not None and self.hold_token is not None:
                stamps = getattr(self, "_handoff_stamps", {})
                cap = (self.finalize_at or self.clock()) + VISIT_INBOX_HANDOFF_ABS_MAX_S
                while not handoff.due(self.clock()) and self.clock() < cap:
                    handoff.interrupted_since(self.host.last_user_input(), stamps)
                    await asyncio.sleep(_HANDOFF_POLL_S)
        finally:
            # Shutdown cancellation still releases held callbacks/deregisters speech IDs, never retaining them forever.
            self._finish_handback(handoff)

    def _finish_handback(self, handoff: Optional[inbox_handoff.InboxHandoff]) -> None:
        if handoff is not None:
            handoff.close()
        if self.hold_token is not None:
            try:
                self.host.release_callback_hold(self.hold_token)
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit %s: callback hold release failed: %s", self.visit_id[:6], type(exc).__name__)
            self.hold_token = None
        parked = self.inbox.close() or []
        if parked:
            self.host.resubmit_callbacks(parked)

    async def _teardown(self) -> None:
        """Close the session, drop the slot and unregister (always runs)."""
        from main_routers.visit_router import transcript_upload

        await self._settle_activation()
        if self.takeover_token is not None:
            try:
                self.host.release_takeover(self.takeover_token)
            except Exception:  # noqa: BLE001
                pass
            self.takeover_token = None
        # Settle interrupted admission-time turn cleanup only after exit ritual/debrief finishes using this manager;
        # this may advance generations/release queued callbacks. Admission/takeover failures release here too.
        self._release_turn_wrap_up()
        # Unclosable clients from cancelled drain/stuck HTTP closure still deregister, never locking characters into ended visits.
        await self.close_session(_SESSION_CLOSE_S)
        self.speech_router.clear()
        if self._pump_task is not None:
            self._pump_stop = True
            self._pump_task.cancel()
        self._stop_display()
        unregister_transport_session(self.transport)
        slot = get_visit_route_state(self.lanlan_name)
        if slot is self.slot:
            finalize_visit_route_state(self.lanlan_name)
        self._set_phase(PHASE_ENDED)
        self._terminated = True
        # Torn-down visits accept no peer messages even if archival failed before setting the flag.
        self._rx_closed = True
        self.ended_at_mono = self.clock()
        _prune_recent(self.ended_at_mono)
        _recent[self.visit_id] = self
        cleanup = self.outbox.close_task
        if cleanup is not None and not cleanup.done():
            # Outbox cleanup writing/removing text-bearing .outbox.jsonl may outlive close deadlines/deregistration;
            # register at module level because stop_all can no longer find the runtime.
            _outbox_cleanups.add(cleanup)
            cleanup.add_done_callback(_outbox_cleanups.discard)
        # Activation ignores cancellation beyond limits, along with runtime accept/ready tasks still awaiting it:
        # Once unregistered, stop_all cannot see this runtime; register it at module level for bounded shutdown cleanup (leave the spool for startup recovery).
        lingering = [t for t in list(self._tasks) + [self._activation, self._closing_task, self._pump_task, self._creds_task]
                     if t is not None and not t.done() and t is not self._room_cancel_task]
        for task in lingering:
            self._keep_background(task)
        _unregister(self)
        # Sealing has not reached disk yet (background cleanup schedules upload afterward); an upload now would only miss .upload.json and waste a backoff cycle.
        if self._seal_settled() and (
                self.journal.sealed or transcript_upload.upload_pending_sync(self.config_dir, self.visit_id)):
            try:
                self.deps.schedule_upload(self.visit_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit %s: upload not scheduled: %s", self.visit_id[:6], type(exc).__name__)

    # ── Shutdown ──

    async def shutdown(self) -> None:
        """``stop_all``: no ``leave`` (the pages are already gone), files first, takeover released.

        Every wait is capped by its own step limit and by what is left of
        ``VISIT_SHUTDOWN_BUDGET_S``; whatever has not finished by then is left
        to the startup recovery.
        """
        loop = asyncio.get_running_loop()
        started = loop.time()
        budget_end = started + VISIT_SHUTDOWN_BUDGET_S

        def left(cap: float) -> float:
            return max(0.0, min(cap, budget_end - loop.time()))

        # Mark the terminal state first: pending credentials / activation see finalizing on waking and neither take over nor create a session.
        self._terminated = True
        self._shutdown_started = True
        if self._exit_task is not None and not self._exit_task.done():
            self._exit_task.cancel()
        self.finalize_reason = self.finalize_reason or "shutdown"
        self._set_phase(PHASE_ENDED)
        if not self._handback_started:
            # Cleanup was cancelled; its finally sees this flag and does not launch another handback.
            # The process is exiting: return the withheld callback immediately without waiting for a ceremonial line.
            self._handback_started = True
            if self.handoff is not None:
                self.handoff.close()
            if self.hold_token is not None:
                try:
                    self.host.release_callback_hold(self.hold_token)
                except Exception:  # noqa: BLE001
                    pass
                self.hold_token = None
            parked = self.inbox.close() or []
            if parked:
                self.host.resubmit_callbacks(parked)
        # Stop the channel-close task launched by cleanup too: shutdown sends no leave and must not keep draining after sealing.
        inflight = [t for t in (self._exit_task, self._creds_task, self._activation, self._closing_task)
                    if t is not None and not t.done()]
        for task in inflight:
            task.cancel()
        if self.peer is None:
            # No matching host room: release the invite code and quota rather than waiting for expiry (cancel the room without sending leave);
            # start this first, in parallel with every subsequent step.
            self._cancel_room_once()
        if inflight:
            await asyncio.wait(inflight, timeout=left(_SHUTDOWN_TASK_WAIT_S))
        # Finalize the line being spoken locally first: its text{final} and usage must enter the upload stream before sealing.
        try:
            await asyncio.wait_for(self.close_current_line("visit_end"), left(_SHUTDOWN_TASK_WAIT_S))
        except Exception as exc:  # noqa: BLE001 - Seal even if cleanup cannot finish.
            logger.warning("visit %s: line not closed at shutdown: %r", self.visit_id[:6], exc)
        line = self._line
        if line is not None and line.payload is not None and (line.booking is None or not line.booking.done()):
            # This line is finalized (text{final} is queued and displayed), but the LLM has not stopped and _finish_line has not run:
            # record it in the transcript before sealing, or shutdown cancellation would lose it. Accounting is a shared, shielded task:
            # cancelling this bounded wait at its deadline does not interrupt the write.
            try:
                await asyncio.wait_for(self.book_line(line), left(_SHUTDOWN_TASK_WAIT_S))
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit %s: closed line not booked at shutdown: %r", self.visit_id[:6], exc)
        # An accepted relative utterance is still being persisted: wait for its transcript entry (with a deadline) before defining the seal boundary.
        await self.settle_family_records(left(_SHUTDOWN_TASK_WAIT_S))
        # Close the receive channel before sealing so later reliable sentences from the iframe cannot be accepted and acked after the seal.
        unregister_transport_session(self.transport)
        self._rx_closed = True  # Use the same instant as unregistration: accept earlier arrivals, but reject later ones (queued on_recv calls).
        # These peer messages were already accepted (the peer will not retransmit) and are still processing: wait for transcript recording (bounded) so later cancellation cannot cut it off.
        await self._settle_delivering(left(_SHUTDOWN_TASK_WAIT_S))
        self._start_journal_for_backlog()
        await self._settle_journal_open(left(_SHUTDOWN_TASK_WAIT_S))
        self._flush_journal_backlog()
        sealed = False
        if self._header_pending():
            # The upload header is still being written: do not call seal() (it seals nothing and returns None, which would then be cached as this visit's seal);
            # shutdown starts no background seal chain; leave the stream and spool for recovery on the next startup.
            logger.warning("visit %s: upload header still writing at shutdown", self.visit_id[:6])
        else:
            sealing = self._start_seal("shutdown")  # The exit flow is already sealing: await that same operation instead of sealing twice.
            await asyncio.wait([sealing], timeout=left(_SHUTDOWN_TASK_WAIT_S))
            sealed = sealing.done()
            if sealed:
                self._take_seal(sealing)
            else:
                # The outer task only waits under shield; cancelling it cannot stop the write. Register it at module level; the real disk write is registered as wait-only, never cancel.
                self._keep_background(sealing)
                # Disk is stuck: the thread still completes its write; the spool is not marked finalized (seal precedes finalized), so startup can recover it.
                logger.warning("visit %s: upload seal still writing at shutdown", self.visit_id[:6])
        if self.spool is not None and sealed and not self._spool_finalized:
            self._spool_finalized = True
            # Bound the wait by the shutdown budget: stop waiting for stuck disk writes (the thread still persists them; startup recovers anything missing).
            closing = asyncio.ensure_future(self._finalize_spool_at_shutdown(self.spool))
            closing.add_done_callback(lambda t: t.cancelled() or t.exception())
            await asyncio.wait([closing], timeout=left(_SHUTDOWN_TASK_WAIT_S))
            if not closing.done():
                # Register at module level: stop_all cancels and waits with a deadline at the end, avoiding tasks left pending at loop destruction or writing
                # state.json after shutdown returns. Writes already running in threads still complete; startup recovers anything missing.
                logger.warning("visit %s: spool still finalizing at shutdown", self.visit_id[:6])
                self._keep_background(closing)
        if self.takeover_token is not None:
            try:
                self.host.release_takeover(self.takeover_token)
            except Exception:  # noqa: BLE001
                pass
            self.takeover_token = None
        # Cleanup owed by the interrupted cycle remains held during shutdown. Releasing it would settle renewal / replacement whenever the session becomes idle during shutdown;
        # the process is about to exit, so retaining the counter has no effect.
        cancel = self._room_cancel_task
        if cancel is not None and not cancel.done():
            # Room cancellation is an external request (invite code and quota): allow at least _SHUTDOWN_ROOM_CANCEL_S from when it is spawned,
            # even if earlier steps consume the total budget (it runs in parallel with them and normally stays within budget).
            floor = self._room_cancel_at + _SHUTDOWN_ROOM_CANCEL_S - loop.time()
            await asyncio.wait([cancel], timeout=max(left(_SHUTDOWN_ROOM_CANCEL_S), floor, 0.0))
        for task in list(self._tasks):
            task.cancel()
        if self._pump_task is not None:
            self._pump_stop = True
            self._pump_task.cancel()
        # Tasks still alive after cancellation (stuck in cancellation-ignoring dependencies): after unregistration stop_all cannot see this runtime, so register them at module level for cleanup.
        for task in list(self._tasks) + [self._pump_task] + inflight:
            if task is not None and not task.done():
                self._keep_background(task)
        self._stop_display()
        # Perform the two normal channel-close / teardown actions on the shutdown path too (each bounded by the shutdown budget):
        # .outbox.jsonl contains message bodies and must not outlive the visit; the isolated-session client must close its own tasks and HTTP connections.
        outbox_closing = asyncio.ensure_future(self.outbox.close())
        outbox_closing.add_done_callback(lambda t: t.cancelled() or t.exception())
        # Close the outbox and isolated session concurrently using the remaining budget.
        await asyncio.gather(asyncio.wait([outbox_closing], timeout=left(_SHUTDOWN_TASK_WAIT_S)),
                             self.close_session(left(_SHUTDOWN_TASK_WAIT_S)))
        cleanup = self.outbox.close_task
        if cleanup is not None and not cleanup.done():
            logger.warning("visit %s: outbox still closing at shutdown", self.visit_id[:6])
            # Use the same registration as teardown: stop_all first waits with a deadline for file removal, cancelling only at the deadline.
            _outbox_cleanups.add(cleanup)
            cleanup.add_done_callback(_outbox_cleanups.discard)
        unregister_transport_session(self.transport)
        slot = get_visit_route_state(self.lanlan_name)
        if slot is self.slot:
            finalize_visit_route_state(self.lanlan_name)
        _unregister(self)

    # ── State ──

    def _replay_lines(self) -> list[dict]:
        """Every line of this visit so far, ``(lp, side_rank)`` order: the journal plus what is still buffered.

        While the upload header is being written (or has not started: a
        verified ``hello`` before the local ``joined``) accepted lines live
        only in the backlog; a page reload then must still see them.
        """
        from main_logic.visit.memory_commit import SIDE_RANK

        rows = list(self.journal.lines())
        for record in self._journal_backlog:
            if record.get("kind") == "line":
                rows.append({"lp": record["lp"], "side": record["side"], "from": record["speaker"],
                             "ts": record["ts"], "text": record["text"], "truncated": record["truncated"]})
        rows.sort(key=lambda r: (r["lp"], SIDE_RANK.get(r["side"], 2)))
        return rows

    def transcript_records(self) -> list[dict]:
        """The in-memory transcript (both sides, ``(lp, side_rank)`` order) for ``GET /transcript``."""
        return self._replay_lines()

    def snapshot(self) -> dict:
        """``GET /api/visit/state`` body for this character (never the invite code)."""
        creds = self.creds
        peer = self.peer
        transcript = [self.visit_line_payload_from_record(r) for r in self._replay_lines()[-50:]]
        reconnecting = self.phase != PHASE_ENDED and (
            self.liveness.self_disconnected_at is not None or self.liveness.page_departed_at is not None)
        # Cleanup has begun but takeover has not been released (before release or after a release error awaiting teardown retry): the page returns its input box on ending,
        # while visits still own input. Keep reporting the pre-cleanup phase, consistent with sending ending only after takeover release.
        phase = self.phase
        if phase == PHASE_ENDING and self.takeover_token is not None:
            phase = self._was_phase
        return {
            "active": self.phase not in (PHASE_ENDED,),
            "role": self.side, "side": self.side, "visit_id": self.visit_id, "phase": phase,
            "transport": creds.transport if creds else None,
            "tier": creds.tier if creds else None,
            "crop": self.crop,
            "peer": None if peer is None else {
                "cat_name": peer.display, "short_id": peer.short_id, "human_label": None,
                "lang": peer.lang, "hidden": bool(self.room.peer_hidden) if self.room else False,
            },
            # Page reloads (before the transport page rejoins the room) also count as reconnecting: no page can send or receive frames then.
            "connected": self.joined and not reconnecting,
            "reconnecting": reconnecting,
            "rtt_ms": self.stats.get("rtt_ms"), "rx_fps": self.stats.get("rx_fps"),
            "tx_fps": self.stats.get("tx_fps"),
            "reconnects": self.reconnects,
            "anomalies": self.anomaly_count(),
            "invite_expires_at": creds.invite_expires_at if creds and self.side == "host" else None,
            "credentials_expires_at": creds.expires_at if creds else None,
            "cross_region": bool(creds.cross_region) if creds else False,
            "memory_pending": bool(self.memory_enabled),
            "debrief": {"pending": False},
            "room": self.room.snapshot() if self.room is not None else None,
            "transcript": transcript,
        }

    def anomaly_count(self) -> int:
        return (self.room.anomalies_total if self.room is not None else 0) + self.pre_room_anomalies


_detached: set[asyncio.Task] = set()
"""Tasks that outlive their runtime (the inbox handoff); kept referenced until done."""

_room_cancels: set[asyncio.Task] = set()
"""Room cancellation requests still in flight (they may outlive their runtime)."""

_awaited_writes: set[asyncio.Future] = set()
"""Thread-pool writes ``stop_all`` waits for (bounded) but never cancels: cancelling the asyncio wrapper
would also cancel a job still queued on the single writer thread (the upload seal would never be written)."""

_outbox_cleanups: set[asyncio.Future] = set()
"""Outbox cleanups (finish writes, delete the ``.outbox.jsonl`` with text bodies) still running after teardown."""


async def _retry_record_account(deps: "RuntimeDeps", account: Any, visit_uid: Any, visit_id: str) -> None:
    """Keep writing the account map in the background until it lands.

    Backs off along ``_ACCOUNT_RETRY_DELAYS_S``, then keeps the last interval
    (the map is idempotent; the process exit ends it).
    """
    attempt = 0
    while True:
        delays = _ACCOUNT_RETRY_DELAYS_S
        await asyncio.sleep(delays[min(attempt, len(delays) - 1)])
        attempt += 1
        try:
            await asyncio.wait_for(deps.record_account(account, visit_uid), _ACCOUNT_RECORD_S * 4)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("visit %s: account mapping retry failed: %r", visit_id[:6], exc)


def _detach(coro: Awaitable[Any]) -> asyncio.Task:
    task = asyncio.ensure_future(coro)
    _detached.add(task)
    task.add_done_callback(_detached.discard)
    return task


def _stopped_since(gen: Optional[int]) -> bool:
    """``stop_all`` ran (or is running) since ``gen`` was read from ``_stop_gen``."""
    return _stopping or (gen is not None and gen != _stop_gen)


def _keep_detached(task: asyncio.Future) -> None:
    """Track an already running task that outlives its runtime (``stop_all`` cancels it too)."""
    _detached.add(task)
    task.add_done_callback(_detached.discard)


def _make_inbox() -> Any:
    from main_logic.watch_together.live import LiveInbox

    return LiveInbox()


# ═════════════════════════════════════════════════════════════════════
# Start / join (called by the HTTP layer in C3a-3b).
# ═════════════════════════════════════════════════════════════════════


class VisitRefused(Exception):
    """Synchronous refusal of a room / join request: ``(status, body)`` for the HTTP layer."""

    def __init__(self, status: int, body: dict) -> None:
        super().__init__(body.get("code") or body.get("reason"))
        self.status = status
        self.body = body


VISIT_SWITCH_OFF = "VISIT_DISABLED"
"""409 code of a room / join while the user's ``visitEnabled`` setting is off (design §3.7.6)."""


async def visit_switch_refusal(deps: Optional[RuntimeDeps] = None) -> Optional[VisitRefused]:
    """The refusal of a room / join while ``visitEnabled`` is off, else None.

    Read once per request from the conversation settings; settings that cannot
    be read fall back to the default (off), so an unreadable file refuses.
    """
    settings = await (deps or runtime_deps()).settings()
    if settings.get("visitEnabled", VISIT_ENABLED_DEFAULT) is True:
        return None
    return VisitRefused(409, {"code": VISIT_SWITCH_OFF, "reason": "visit_disabled"})


async def start_visit(
    lanlan_name: str,
    side: str,
    *,
    crop: str = VISIT_CROP_DEFAULT,
    invite_code: Optional[str] = None,
    visit_id: Optional[str] = None,
    host: Optional[VisitHost] = None,
    deps: Optional[RuntimeDeps] = None,
    clock: Callable[[], float] = time.monotonic,
    wall: Callable[[], float] = time.time,
) -> "VisitRuntime":
    """Admit and start one side of a visit; raises :class:`VisitRefused` for the synchronous answers.

    Order (design §3.2.1): persona gate, then the route lock checked once and
    the slot reserved right after it with no await in between (a reservation
    makes the lock true), then the preconditions that do not depend on it
    (voice session, goodbye silence, hot swap, local login, banned cache). Everything after this returns (capability gate,
    Servers, takeover) runs on the transport events and the sweep.
    """
    from main_routers.visit_router.persona import persona_gate
    from utils.external_route_registry import is_external_route_locked

    name = str(lanlan_name or "")
    if side not in ("host", "guest"):
        raise ValueError("side must be 'host' or 'guest'")
    stop_gen = _stop_gen
    account_gen = _account_gen
    disabled_gen = _disabled_gen
    if _account_changes:
        raise VisitRefused(409, {"code": "VISIT_E_BUSY", "reason": "account_change"})
    # The user's visitEnabled switch is off: reject all invites and outbound visits (§3.7.6); check before the persona gate to avoid regenerating the persona.
    switch_off = await visit_switch_refusal(deps)
    if switch_off is not None:
        raise switch_off
    gate = await persona_gate(name)
    if not gate.ok:
        raise VisitRefused(409, {"code": "VISIT_PERSONA_UNREVIEWED", "state": gate.state})
    host = host or ManagerHost.for_character(name)
    if host is None:
        raise VisitRefused(409, {"reason": "busy"})
    vid = visit_id or secrets.token_urlsafe(16)
    # No await between checking the lock and reserving it: reserve immediately so other routes cannot intervene; it is necessarily true afterward and must not be checked again.
    if is_external_route_locked(name):
        reason = "already_visiting" if name in _runtimes or get_visit_route_state(name) else "route_owned"
        raise VisitRefused(409, {"code": "VISIT_E_BUSY", "reason": reason})
    key = (vid, side)
    if key in _pending_visits or any(r.visit_id == vid and r.side == side for r in _runtimes.values()):
        # Lock per role: another role entering the same side of the same visit (the same invite) must not replace its registry entry.
        raise VisitRefused(409, {"code": "VISIT_E_BUSY", "reason": "visit_in_progress"})
    config_dir = Path((deps or runtime_deps()).config_dir())
    if (any(pending[0] == vid and pdir == config_dir for pending, pdir in _pending_visits.items())
            or any(r.visit_id == vid and r.config_dir == config_dir for r in _runtimes.values())):
        # This machine already runs the other side of this visit with files in the same config directory: both sides name their spool / state / upload stream / outbox only by
        # visit_id and would overwrite each other. Servers already rejects redeeming this machine's invite with the same account as self_invite; if the desktop switches
        # community accounts midway, Servers cannot catch it, so reject locally for the same reason (with the same message as Servers).
        status, body = cr.VisitInviteInvalid("self_invite").to_local_error()
        raise VisitRefused(status, body)
    slot = activate_visit_route(name, phase=PHASE_PENDING, visit_id=vid)
    _pending_visits[key] = config_dir
    try:
        failure = host.precondition_failure()
        if failure is not None:
            raise VisitRefused(409, {"reason": failure})
        account = await _local_account()
        if _account_gen != account_gen or _account_changes:
            # Logout / account switching began during entry: this visit would run under a nonexistent / different user's account, so do not register it.
            raise VisitRefused(409, {"code": "VISIT_E_BUSY", "reason": "account_change"})
        if _stop_gen != stop_gen or _stopping:
            # stop_all ran / is running during entry (it sees only registered runtimes): do not register or start transport.
            raise VisitRefused(409, {"code": "VISIT_E_BUSY", "reason": "shutdown"})
        if _disabled_gen != disabled_gen:
            # The user disabled visits in settings during entry (the disable hook only ends registered runtimes): do not register.
            raise VisitRefused(409, {"code": "VISIT_E_BUSY", "reason": "visit_disabled"})
        # While checking the account (before runtime registration and visit input ownership), a voice session / hot switch may have started and replaced the manager: check again.
        if not host.is_current():
            raise VisitRefused(409, {"reason": "busy"})
        failure = host.precondition_failure()
        if failure is not None:
            raise VisitRefused(409, {"reason": failure})
        if account is None:
            raise VisitRefused(409, {"code": "VISIT_LOGIN_REQUIRED"})
        if cr.banned_recently(account):
            raise VisitRefused(403, {"code": "VISIT_BANNED"})
        rt = VisitRuntime(
            lanlan_name=name, side=side, visit_id=vid, host=host, character_uid=gate.character_uid or "",
            persona_text=gate.text or "", crop=crop, invite_code=invite_code, deps=deps,
            clock=clock, wall=wall,
        )
    except BaseException:
        _pending_visits.pop(key, None)
        if get_visit_route_state(name) is slot:
            finalize_visit_route_state(name)
        raise
    rt.slot = slot
    rt.admitted_account = account
    rt.admitted_epoch = account_gen
    rt.start()
    _pending_visits.pop(key, None)
    return rt


async def _local_account() -> Optional[str]:
    from main_routers.visit_router.accounts import local_account

    try:
        return await local_account()
    except Exception:  # noqa: BLE001 - Treat unreadable local login state as logged out.
        return None


# ═════════════════════════════════════════════════════════════════════
# Registry entry point.
# ═════════════════════════════════════════════════════════════════════


async def route_stream_message(lanlan_name: str, message: dict) -> bool:
    from main_routers.visit_router import display_socket
    from utils.visit_route_state import DISPLAY_SOCKET_CONNECTION

    rt = _runtimes.get(str(lanlan_name or ""))
    if rt is None or not is_visit_route_active(lanlan_name):
        return False
    origin = DISPLAY_SOCKET_CONNECTION.get()
    if origin is not None and not display_socket.is_bound(origin):
        # From a display socket without visit_bind (the route changed to visits during dispatch): swallow it and send the unauthorized notice back to the originating
        # connection (not the current mgr.websocket), without speaking for the relative.
        request_id = message.get("request_id") if isinstance(message, dict) else None
        await display_socket.refuse(origin, request_id)
        return True
    return await rt.on_stream_message(message)


async def on_start_session(lanlan_name: str, message: dict) -> bool:
    rt = _runtimes.get(str(lanlan_name or ""))
    if rt is None or not is_visit_route_active(lanlan_name):
        return False
    return await rt.on_start_session(message)


async def route_voice_transcript(lanlan_name: str, transcript: str, **_kwargs: Any) -> bool:
    rt = _runtimes.get(str(lanlan_name or ""))
    if rt is None or not is_visit_route_active(lanlan_name):
        return False
    await rt._voice_unavailable()
    return True


async def on_page_signal(lanlan_name: str, message: dict) -> bool:
    """``visit_speech_progress`` (§4.5): a line of a live visit, or a finished visit's handoff segment."""
    if not isinstance(message, dict):
        return False
    speech_id = message.get("speech_id")
    if not isinstance(speech_id, str) or not speech_id:
        return False
    ended = message.get("ended") is True
    final = message.get("final")
    if ended and not isinstance(final, bool):
        return False
    played = message.get("played_ms")
    played_ms = played if isinstance(played, int) and not isinstance(played, bool) and played >= 0 else 0
    rt = _runtimes.get(str(lanlan_name or ""))
    if rt is not None and rt.speech_router.route(speech_id, played_ms=played_ms, ended=ended,
                                                 final=bool(final)):
        return True
    return inbox_handoff.route_progress(speech_id, ended=ended, final=bool(final))


async def finalize_for_character(lanlan_name: str) -> int:
    """Character switch: finalize and only wait for the state flip (not the exit flow)."""
    rt = _runtimes.get(str(lanlan_name or ""))
    if rt is None:
        return 0
    return 1 if rt.request_finalize("character_switch") else 0


async def end_visit(lanlan_name: str, visit_id: str, reason: str) -> tuple[int, dict]:
    """``POST /route/end``: ``recall`` starts the natural wrap-up, ``route_end`` ends now."""
    rt = _runtimes.get(str(lanlan_name or ""))
    if rt is None or rt.visit_id != visit_id:
        return 404, {"error": "unknown_visit"}
    if reason == "recall":
        if rt.room is None or rt.phase in WAITING_PHASES:
            # Chat has not started: recalling directly ends the visit.
            started = rt.request_finalize("recall")
            return 200, {"ok": True, "mode": "finalize", "exit_task_started": started}
        if rt.room.phase != "active" or rt.finalizing:
            return 409, {"code": "VISIT_RECALL_ALREADY"}
        rt.apply_effects(rt.room.on_local_recall(rt.clock()))
        return 200, {"ok": True, "mode": "wrap_up", "exit_task_started": False}
    started = rt.request_finalize("route_end")
    return 200, {"ok": True, "mode": "finalize", "exit_task_started": started}


def cancel_background_writes() -> int:
    """Startup rollback: cancel every registered visit background write (digest / summary); returns how many."""
    tasks = [t for bucket in list(_visit_bg_tasks.values()) for t in list(bucket) if not t.done()]
    for task in tasks:
        task.cancel()
    return len(tasks)


@asynccontextmanager
async def account_change(
    timeout: float = _ACCOUNT_CHANGE_SEAL_WAIT_S,
    *,
    ends_visits: Optional[Callable[[], Awaitable[bool]]] = None,
):
    """Hold across a community logout / account switch: no visit is admitted, live ones end first.

    On entry the admission fence goes up (a start already past its account
    lookup is refused as well), then every live visit is ended and its
    upload seal awaited (:func:`end_visits_for_account_change`); the caller
    changes the credentials inside the block. Account changes run one at a
    time (the fence goes up before queuing). ``ends_visits()`` (asked once
    the fence is up, so no visit is admitted while it decides) can keep live
    visits running, e.g. for a re-login as the same account. Ending visits
    never raises.
    """
    global _account_changes, _account_gen
    _account_changes += 1
    _account_gen += 1
    try:
        # Raise the admission gate before queuing: while an earlier change waits for a visit to seal, that visit may already have unregistered from _runtimes,
        # letting a later change see an empty registry and replace credentials first. Serialize account changes.
        async with _account_change_mutex():
            if (_runtimes or _seals_in_background()) and (ends_visits is None or await ends_visits()):
                try:
                    await end_visits_for_account_change(timeout)
                except Exception as exc:  # noqa: BLE001 - Cleanup failure must not block logout / login; startup recovers unsealed visits.
                    logger.warning("visit: ending live visits before the account change failed: %r", exc)
            # In-flight credential requests (the visit may be unregistered while requests remain in the background) must still read the old account session: await them before changing it.
            await _settle_in_flight(_credential_requests, timeout, "credential requests")
            # Let recap pushes that already verified the account finish too, preventing account changes between verification and writing.
            # Wait at least the push timeout (even if the caller supplied a shorter timeout): on release, all pushes have finished or been cut off.
            await _settle_in_flight(_account_bound_pushes, max(timeout, _ACCOUNT_BOUND_PUSH_MAX_S + 1.0),
                                    "debrief pushes")
            yield
    finally:
        _account_changes -= 1


_credential_requests: set[asyncio.Future] = set()
"""Servers credential requests in flight: an account change waits for them before the session changes."""

_account_bound_pushes: set[asyncio.Future] = set()
"""Debrief pushes in flight (:func:`hold_account_for_push`): an account change waits for them before the session changes."""


@asynccontextmanager
async def hold_account_for_push():
    """Yield whether a push bound to the signed-in account may go out; keep the account from changing until it ends.

    Yields False while a logout / account switch is in progress (the caller
    skips the push; bind replays it to the account that owns the visit).
    Otherwise the push checks the account and writes inside the block, and
    :func:`account_change` waits for it before the session changes, so no
    switch lands between the check and the write. The block is cut off
    (``TimeoutError``) after ``_ACCOUNT_BOUND_PUSH_MAX_S``, shorter than
    that wait: a push never writes after the credentials changed.
    """
    if _account_changes:
        yield False
        return
    held = asyncio.get_running_loop().create_future()
    _account_bound_pushes.add(held)
    try:
        async with asyncio.timeout(_ACCOUNT_BOUND_PUSH_MAX_S):
            yield True
    finally:
        if not held.done():
            held.set_result(None)
        _account_bound_pushes.discard(held)


async def _settle_in_flight(futures: set[asyncio.Future], timeout: float, what: str) -> None:
    """Wait (bounded) for the in-flight ``futures`` an account change must not overtake; never cancels them."""
    pending = [f for f in futures if not f.done()]
    if not pending:
        return
    # Wait without cancelling: once sent, cancelling a credential request cannot undo issuance on Servers (or the redeemed invite code);
    # recap pushes are bounded by their own timeout (hold_account_for_push), so do not cancel those here either.
    _done, still = await asyncio.wait(pending, timeout=max(0.0, timeout))
    if still:
        logger.warning("visit: account change proceeds with %d %s still in flight after %.0fs",
                       len(still), what, timeout)


def _seals_in_background() -> list["VisitRuntime"]:
    """Recently ended runtimes whose upload seal is still being written."""
    return [rt for rt in list(_recent.values()) if rt.seal_pending()]


async def end_visits_for_account_change(timeout: float = _ACCOUNT_CHANGE_SEAL_WAIT_S) -> int:
    """Community logout / account switch: ``route_end`` every live visit, wait until each sealed its upload.

    The upload file names the account the visit ran under; the local login
    state is cleared or replaced only after that (bounded by ``timeout``,
    shared by all visits). Returns how many visits were ended.
    """
    runtimes = list(_runtimes.values())
    ended = 0
    for rt in runtimes:
        if rt.request_finalize("route_end"):
            ended += 1
    # Also await visits just unregistered whose seals are still being written in the background (exit exceeded its seal deadline): they are no longer in _runtimes.
    runtimes += [rt for rt in _seals_in_background() if rt not in runtimes]
    if runtimes:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        results = await asyncio.gather(*(rt.wait_upload_sealed(max(0.0, deadline - loop.time()))
                                         for rt in runtimes), return_exceptions=True)
        if not all(result is True for result in results):
            logger.warning("visit: account change went ahead before every upload was sealed")
    return ended


async def stop_all(reason: str = "shutdown") -> None:
    """Shutdown hook (PR-09b, within ``VISIT_SHUTDOWN_BUDGET_S``): files first, never a ``leave``."""
    global _stop_gen, _stopping
    _stop_gen += 1  # In-flight start_visit checks this after waking and skips registration.
    _stopping = True  # Reject new entries arriving while stop_all runs too.
    loop = asyncio.get_running_loop()
    budget_end = loop.time() + VISIT_SHUTDOWN_BUDGET_S
    try:
        runtimes = list(_runtimes.values())
        # Room-cancellation requests from dismantled visits (invite code and quota): wait with a deadline in parallel with visit shutdown, cancelling only at the deadline,
        # without adding another wait after shutdown.
        # Exclude requests from visits still registered: their shutdown() awaits them under its own rules (minimum duration).
        owned = {rt._room_cancel_task for rt in runtimes}
        orphans = [t for t in _room_cancels if not t.done() and t not in owned]
        # Also wait with a deadline for unfinished outbox cleanup from dismantled visits (removing body-containing .outbox.jsonl), cancelling only at the deadline:
        # cancelling first interrupts flush and leaves the file until next startup.
        orphans += [t for t in _outbox_cleanups if not t.done()]
        # Thread-pool disk writes (upload sealing): wait with a deadline without cancellation (cancelling would remove a queued write).
        writes = [t for t in _awaited_writes if not t.done()]

        async def settle_orphan_cancels() -> None:
            if orphans or writes:
                await asyncio.wait(orphans + writes, timeout=_SHUTDOWN_ROOM_CANCEL_S)
                for task in orphans:
                    task.cancel()

        results = await asyncio.gather(*(rt.shutdown() for rt in runtimes), settle_orphan_cancels(),
                                       return_exceptions=True)
        for rt, result in zip(runtimes, results):
            if isinstance(result, BaseException):
                # This visit was not sealed / finalized; leave it for startup recovery and at least log which visit failed and why.
                logger.error("visit %s: shutdown failed: %r", rt.visit_id[:6], result, exc_info=result)
        # Stop background tasks detached from runtimes (account-map updates, handback callbacks, sessions not fully closed) too, rather than leaving them to loop destruction.
        # Also stop per-role registered background writes (digest, final summary, deferred spool cleanup, startup recovery); recover unfinished writes on next startup.
        # Cancel and then wait with a deadline for unfinished room-cancellation requests too (a visit's shutdown() failed before its own cancellation wait).
        # (On the normal path they have all finished by now.)
        detached = [t for t in _detached if not t.done()]
        detached += [t for bucket in list(_visit_bg_tasks.values()) for t in list(bucket) if not t.done()]
        detached += [t for t in _room_cancels if not t.done()]
        # Outbox cleanup in the initial snapshot has already been awaited and cancelled: wait with a deadline for any tasks still running too.
        detached += [t for t in orphans if not t.done() and t in _outbox_cleanups]
        # Outbox cleanup registered during shutdown (by each visit's shutdown()) gets the same bounded wait before cancellation as the initial batch;
        # cancelling first interrupts flush, leaving the body-containing .outbox.jsonl until next startup.
        late_cleanups = [t for t in _outbox_cleanups if not t.done() and t not in orphans]
        # Tasks abandoned after the deadline for interrupting the main session (registered by ManagerHost).
        from main_routers.visit_router import host_port as _host_port

        detached += _host_port.abandoned_interrupts()
        # Connection-close tasks started when unregistering transport (including visits unregistered during shutdown): backpressure may leave them waiting for the send lock and close frame.
        from main_routers.visit_router import transport_ws as _transport_ws

        detached += _transport_ws.pending_close_tasks()
        for task in detached:
            task.cancel()
        # Thread-pool disk writes registered during shutdown (upload sealing started in each visit's shutdown()) were absent from the initial snapshot; also wait without cancelling,
        # using only the remaining total shutdown budget (each visit has already waited once).
        late_writes = [t for t in _awaited_writes if not t.done() and t not in writes]
        # Cap both waits at the remaining total shutdown budget: do not wait further if visit shutdown() calls have consumed it.
        left = max(0.0, min(_SHUTDOWN_TASK_WAIT_S, budget_end - loop.time()))
        waits = []
        if detached:
            waits.append(asyncio.wait(detached, timeout=left))
        if late_writes or late_cleanups:
            waits.append(asyncio.wait(late_writes + late_cleanups, timeout=left))
        if waits:
            await asyncio.gather(*waits)
        stuck = [t for t in late_cleanups if not t.done()]
        for task in stuck:
            task.cancel()
        if stuck:
            # As with the initial batch, allow cancellation handling after cancelling (cap at the remaining budget, but yield at least once even when exhausted).
            await asyncio.wait(stuck, timeout=max(0.0, budget_end - loop.time()))
    finally:
        _stop_gen += 1  # Entry started during stop_all and woke after it finished: the generation changed, so do not register it either.
        _stopping = False


async def visit_sweep_loop() -> None:
    """Every ``VISIT_SWEEP_INTERVAL_S``: each runtime's timers (PR-09b starts it after startup)."""
    while True:
        await asyncio.sleep(VISIT_SWEEP_INTERVAL_S)
        _prune_recent(time.monotonic())
        for rt in list(_runtimes.values()):
            try:
                await rt.tick()
            except Exception as exc:  # noqa: BLE001 - A timing failure in one visit must not affect other visits.
                logger.warning("visit %s: tick failed: %r", rt.visit_id[:6], exc)


def _wire_upload_hooks() -> None:
    # Transcript recovery upload / report retries skip this process's in-flight visits (hook retained by transcript_upload).
    from main_routers.visit_router import transcript_upload

    transcript_upload.is_live = is_visit_live


_wire_upload_hooks()


def register_visit_route_kind() -> None:
    """Register the ``neko_visit`` external route kind (import time; tests re-run it)."""
    from utils.external_route_registry import ExternalRouteKind, register_external_route_kind

    register_external_route_kind(ExternalRouteKind(
        kind=VISIT_ROUTE_KIND,
        is_active=is_visit_route_active,
        route_stream_message=route_stream_message,
        on_start_session=on_start_session,
        finalize_for_character=finalize_for_character,
        route_voice_transcript=route_voice_transcript,
        on_page_signal=on_page_signal,
        is_locked=is_visit_route_locked,
        has_background_tasks=has_visit_background_tasks,
        current_instance=current_instance,
    ))


__all__ = [
    "VisitRuntime", "VisitRefused", "RuntimeDeps", "start_visit", "end_visit", "stop_all",
    "visit_sweep_loop", "is_visit_live", "is_visit_route_active", "is_visit_route_locked",
    "has_visit_background_tasks", "spawn_visit_background", "register_visit_route_kind",
    "get_runtime", "get_runtime_by_visit", "recent_runtime", "live_runtimes", "on_page_signal",
    "hold_character_lifecycle", "hold_account_for_push", "end_visits_for_account_change", "account_change",
    "cancel_background_writes",
]
