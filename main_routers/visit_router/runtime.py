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
import math
import random
import secrets
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from config.visit_settings import (
    VISIT_ACTIVATION_ALLOWANCE_S,
    VISIT_CAPS_PREFLIGHT_TIMEOUT_S,
    VISIT_CAPS_SDK_TIMEOUT_S,
    VISIT_CROP_DEFAULT,
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
    activate_visit_route,
    finalize_visit_route_state,
    get_visit_route_state,
)

logger = get_module_logger(__name__, "Main")

_PUMP_IDLE_S = 0.25
_INTERRUPT_MAIN_TURN_S = 3.0
_CLOSE_WAIT_S = VISIT_LEAVE_GAP_GRACE_S * 2 + 2.0
_HANDOFF_POLL_S = 0.25
# 关机总预算 VISIT_SHUTDOWN_BUDGET_S：每一步各有上限（_SHUTDOWN_TASK_WAIT_S），且都截到总期限的剩余；
# 撤销未配对房间一开始就起、与封存并行，最后最多再等 _SHUTDOWN_ROOM_CANCEL_S
_SHUTDOWN_TASK_WAIT_S = 0.5
_JOURNAL_OPEN_MAX_S = 10.0
_SESSION_CLOSE_S = 5.0
_RESERVED_SEND_MAX_S = 10.0
_SEAL_MAX_S = 10.0
_DISPLAY_FLUSH_S = 2.0
_ACCOUNT_RECORD_S = 3.0
_ACCOUNT_RETRY_DELAYS_S = (1.0, 5.0, 30.0, 120.0, 600.0)
_SHUTDOWN_ROOM_CANCEL_S = 1.0
_VOICE_STATUS_THROTTLE_S = 5.0

# ═════════════════════════════════════════════════════════════════════
# 依赖注入
# ═════════════════════════════════════════════════════════════════════


def _default_config_dir() -> Path:
    from utils.config_manager import get_config_manager

    return Path(get_config_manager().config_dir)


async def _default_settings() -> dict:
    from utils.preferences import aload_global_conversation_settings

    try:
        return dict(await aload_global_conversation_settings())
    except Exception as exc:  # noqa: BLE001 - 读不出按默认值
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
# 模块级登记
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


def get_runtime_by_visit(visit_id: str) -> Optional["VisitRuntime"]:
    return _by_visit.get(visit_id)


def _prune_recent(now: float) -> None:
    # 没人再查的场次也要放掉（它们持有转录、会话与 manager）
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

_pending_visits: set[tuple[str, str]] = set()
"""``(visit_id, side)`` between the slot reservation and the runtime registration (``start_visit``)."""

_stop_gen = 0
"""Bumped by ``stop_all``: a ``start_visit`` that was still awaiting when it ran does not register."""

_stopping = False
"""True while ``stop_all`` runs: a ``start_visit`` arriving meanwhile is refused (it would not be stopped)."""


def has_visit_background_tasks(lanlan_name: str) -> bool:
    """Registry ``has_background_tasks``: this character's visit background writes are running."""
    if _resolving_names:
        # 有后台任务还没认出它属于哪个名字（启动补录派生的）：宁可短暂挡住改名 / 删除
        return True
    uid = _character_uid_of(lanlan_name)
    tasks = _visit_bg_tasks.get(uid) if uid else None
    return bool(tasks and any(not t.done() for t in tasks))


async def _remember_name(character_uid: str) -> None:
    # 守卫按名字查：补录派生的任务只带 uid（这个角色在本进程可能没串过门），先认出它现在叫什么；
    # 每次都重新解析：角色空闲时改过名，旧名字的映射作废
    try:
        from main_logic.visit import local_chars

        name = await local_chars.resolve_char_name(character_uid)
    except Exception:  # noqa: BLE001 - 认不出名字就只按 uid 登记
        return
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


def current_instance(lanlan_name: str) -> Optional[str]:
    """Registry ``current_instance``: the visit id owning the character."""
    rt = _runtimes.get(str(lanlan_name or ""))
    return f"visit:{rt.visit_id}" if rt is not None else None


def _register(rt: "VisitRuntime") -> None:
    _runtimes[rt.lanlan_name] = rt
    # 先登记的那个留着：本机另一个角色兑换本机发出的邀请会被 Servers 以 self_invite 拒掉，
    # 被拒之前它不能顶掉仍在进行的这一场
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
    for task in list(_room_cancels):
        task.cancel()
    _room_cancels.clear()
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
# transport 会话
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
        # 顶替旧连接时旧连接的 finally 不再报 page_lost：同样把它留下的东西作废
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
# 运行时
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
        # 等待期限在入房（joined）时才按侧位起算：之前只有能力门与入房自己的计时
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

        # 凭证与能力门
        self.grant: Optional[cr.VisitGrant] = None
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

        # 接管与回调
        self.takeover_token: Any = None
        self.hold_token: Any = None
        self.inbox = _make_inbox()

        # 任务
        self._kick_event = asyncio.Event()
        self._pump_task: Optional[asyncio.Task] = None
        self._renew_task: Optional[asyncio.Task] = None
        self._exit_task: Optional[asyncio.Task] = None
        self._tasks: set[asyncio.Task] = set()

        # 收尾
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
        self._sdk_ok = False
        self._pending_join: Optional[dict] = None
        self._page_gen = 0
        self._first_join_gen: Optional[int] = None

        self._init_rx()
        self._init_talk()

    # ── 小工具 ───────────────────────────────────────────────────────

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
            await self.host.send_frame(payload)
            return
        self._post_display(payload)

    async def status(self, code: str, **details: Any) -> None:
        details = {"visit_id": self.visit_id, **{k: v for k, v in details.items() if v is not None}}
        await self.host.send_status(code, details)

    # ── 启动 ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Register the runtime and its transport; the page builds the iframe on ``pending``."""
        _register(self)
        register_transport_session(self.transport)
        self._pump_task = asyncio.ensure_future(self._pump_loop())
        self.spawn(self.push(PHASE_PENDING, transport=None))

    # ── 能力门与凭证 ─────────────────────────────────────────────────

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
        gen = self._page_gen  # 期间这条连接被顶替 / 断开：期限不能装在新连接头上（它会自己来要凭证）
        if self.grant is None:
            if self._creds_task is None:
                self._creds_task = asyncio.ensure_future(self._obtain_credentials())
            task = self._creds_task
            # 只等不连带取消（页面重连会再来要）；关机取消了它就按没领到
            await asyncio.wait([task])
            if task.cancelled() or not task.result() or self.finalizing or gen != self._page_gen:
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
                # 续期失败沿用手里那份：凭证过期之前照样能重新入房
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
            # 上一场软编过载只影响下一场：这一场取走标记，续期 / 重连沿用同一个选择
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
        # 两侧一样：普通文字回复还在生成就先打断（接管只压输出，不停离线会话的生成）
        if not await self.host.interrupt_main_turn(_INTERRUPT_MAIN_TURN_S):
            self.request_finalize("busy")
            return False
        if self.finalizing:
            # 等主对话轮期间这场已被结束：不再去领凭证（guest 会兑掉一次性邀请码、扣配额，且撤不回）
            return False
        try:
            creds = await self.deps.fetch_credentials(
                role=self.side, visit_id=self.visit_id, char_tag=self.character_uid,
                tier=VISIT_VIDEO_TIER_DEFAULT, display_name=self.lanlan_name,
                invite_code=self.invite_code if self.side == "guest" else None,
            )
        except cr.VisitServersError as exc:
            self._finalize_for_servers_error(exc)
            return False
        except Exception as exc:  # noqa: BLE001 - 契约外的失败按 Servers 不可达
            logger.warning("visit %s: credentials failed: %s", self.visit_id[:6], type(exc).__name__)
            self.request_finalize("servers_unreachable")
            return False
        self.grant = cr.VisitGrant(creds, display_name=self.lanlan_name,
                                   invite_code=self.invite_code if self.side == "guest" else None)
        try:
            # 有界：本地记账卡住（文件锁 / 磁盘）不能让这场停在 pending、一直占着路由
            await asyncio.wait_for(self.deps.record_account(creds.account, creds.visit_uid), _ACCOUNT_RECORD_S)
        except Exception as exc:  # noqa: BLE001 - 映射写不进不挡串门（凭证已签发、配额已扣），后台补写
            logger.warning("visit %s: account mapping not recorded: %s", self.visit_id[:6], type(exc).__name__)
            # 转录 / 举报上传按这张映射认账号：没写成就一直补写，写成之前上传只是排队等着
            _detach(_retry_record_account(self.deps, creds.account, creds.visit_uid, self.visit_id))
        if self.side == "host" and not creds.invite_code:
            logger.warning("visit %s: host credentials without an invite code", self.visit_id[:6])
            self.request_finalize("servers_unreachable")
            return False
        if self.finalizing:
            # 等 Servers 期间这场已被结束：收尾流程可能已经走过取消那一步，这里补一次（只发一次）
            self._cancel_room_once()
            return False
        try:
            self.takeover_token = self.host.acquire_takeover(self._voice_dispatcher, self.inbox.accept)
        except Exception as exc:  # noqa: BLE001 - 别的路由抢先接管：按 busy 结束
            logger.warning("visit %s: takeover refused: %s", self.visit_id[:6], type(exc).__name__)
            self.request_finalize("busy")
            return False
        try:
            # 有界：页面不收时它会一直等，而此刻已持有接管、能力门计时还没装上
            await asyncio.wait_for(self.host.interrupt_ordinary_speech(), _INTERRUPT_MAIN_TURN_S)
        except Exception as exc:  # noqa: BLE001 - 切不掉普通语音：释放接管、按 busy 结束
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
            # 预览时有效、确认时已进入到期前 60 s：按邀请过期告诉用户
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
        self._sdk_ok = False  # 能力门 ③ 按连接：新连接要自己再报一次
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
            # 旧连接的首次入房还没结束（joined 仍为真）时不取走：留给它作废时补做
            pending, self._pending_join = self._pending_join, None
            await self.on_transport_state(pending)
            return
        if not self.joined and self._join_deadline is None:
            # 能力门通过不代表入房成功：25 s 内没报 joined 按 relay_lost 结束，不发邀请码
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
            # 之后又报了断线 / 出错 / 被踢：记下的那次入房作废，不能在能力门通过后被当成仍在房里
            self._pending_join = None
        if state in ("joined", "connected"):
            reconnected = self.liveness.self_disconnected_at is not None
            self.liveness.on_self_connected(now)
            self.outbox.resume(now, reason=PAUSE_SELF_RECONNECT)
            if self._first_join_gen is not None and self._first_join_gen != self._page_gen:
                # 旧连接的首次入房还在写上传头、它的连接已经没了：新连接这次报告先记下，旧的那次结束后补做
                self._pending_join = dict(msg)
                return
            if not self.joined:
                if self.creds is not None and not self._sdk_ok:
                    # 入房报告比能力门 ③ 先到（iframe 一次入房只报一次）：记下，能力门过了再补做
                    self._pending_join = dict(msg)
                    return
                await self._on_first_join(now)
            elif reconnected:
                # SDK 自己重连成功：重发 hello 与当前媒体快照（手动重新入房后 iframe 的发布 / 订阅都没了）
                self.outbox.resend_hello(now)
                self.spawn(self.send_media())
        elif state == "reconnecting":
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
            # 还没下发凭证 / 能力门 ③ 还没过的连接报的入房不算：等真正入房的那一次再做首次入房的事
            return
        self.joined = True
        gen = self._first_join_gen = self._page_gen
        # 进入本场：先写上传头，之后的每一行、用量与异常都追加在它后面。独立任务：
        # 收尾 / 关机封存之前先等它写完（否则封存时流水还没装好、封存成空操作）。只写一次：
        # 连接被顶替后新连接再次首次入房时沿用这一份
        opening = self._journal_opening
        if opening is None:
            opening = self._journal_opening = asyncio.ensure_future(self.journal.open(
                role=self.side, own_visit_uid=creds.visit_uid, own_char_uid=self.character_uid,
                transport=creds.transport, started_at=self.wall(), app_version=cr._app_version(),
            ))
            # 上传头一落盘就补记等待期间攒下的行 / 用量 / 异常（写失败时 is_open 为假，什么都不做）
            opening.add_done_callback(lambda _t: self._flush_journal_backlog())
        # 写上传头有界：磁盘卡住时收包循环不能一直挂着（入房期限仍在走，到点按 relay_lost 结束）
        await asyncio.wait([opening], timeout=_JOURNAL_OPEN_MAX_S)
        self._first_join_gen = None
        if not opening.done():
            logger.warning("visit %s: upload journal still opening, buffering its lines", self.visit_id[:6])
        if opening.done() and not opening.cancelled() and opening.exception() is not None:
            # 上传流水建不起来：转录少一份，串门照常
            logger.warning("visit %s: upload journal not opened: %s", self.visit_id[:6],
                           type(opening.exception()).__name__)
        if self.finalizing:
            # 写上传头期间这场已被结束：不再把阶段翻回等待
            return
        self._join_deadline = None
        if gen != self._page_gen:
            # 写上传头期间报入房的那条连接没了 / 被顶替：这次入房不算；新连接期间报过的入房这时补做
            self.joined = False
            pending, self._pending_join = self._pending_join, None
            if pending is not None:
                await self.on_transport_state(pending)
            return
        if self.peer is not None:
            # 对端的 hello 先于本侧的入房报告到达、已核验并进了等待接待：阶段与期限已由核验接管，不翻回去
            return
        now = self.clock()  # 写上传头 await 过：等待期限从这一刻算
        if self.side == "host":
            # 等客：只受邀请期限约束；观察到对端入房时由 liveness 延长
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

    def _on_peer_presence(self, present: bool, vendor_reason: Any, now: float) -> None:
        if present:
            if not self.peer_present:
                self.peer_present = True
                if self.liveness.peer_departed_at is not None or self.peer is not None:
                    # 显式离开后的重入，或超时类断开后再出现：vendor 刚确认对端在场，心跳从现在算
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
            # 暂定离开：35 s 重入宽限，期间心跳判死暂停、投递计时暂停
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

    # ── 媒体 ─────────────────────────────────────────────────────────

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

    # ── 计时 ─────────────────────────────────────────────────────────

    async def _pump_loop(self) -> None:
        """Flush the outbox, acks and heartbeats; wakes on :meth:`kick` or every 250 ms."""
        while True:
            try:
                await asyncio.wait_for(self._kick_event.wait(), _PUMP_IDLE_S)
            except asyncio.TimeoutError:
                pass  # 没人 kick：按空闲间隔照常冲一遍
            self._kick_event.clear()
            try:
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 一次发送失败不能停掉整场的出站
                logger.warning("visit %s: pump failed: %r", self.visit_id[:6], exc)

    async def flush(self, *, force_ack: bool = False) -> None:
        """Send what the outbox releases now (plus the owed ``ack`` and a due heartbeat).

        ``force_ack`` sends an owed ``ack`` without waiting for the coalescing
        window (right before the iframe is stopped).
        """
        now = self.clock()
        ack = self.sequencer.poll_ack(now, force=force_ack)
        if ack is not None and ack > 0:
            self.outbox.send({"t": "ack", "v": 1, "seq": ack}, now=now)
        if self.joined and self.peer is not None and not self.finalizing and self.liveness.heartbeat_due(now):
            lp_seen = self.room.max_lp_seen if self.room is not None else 0
            self.outbox.send({"t": "hb", "v": 1, "lp_seen": lp_seen, "crop": self.crop,
                              "hidden": bool(self.local_hidden)}, now=now)
        frames = self.outbox.due(now)
        for frame in frames:
            if await self.transport.send(frame.to_ws()):
                self.on_frame_sent(frame)
        if self.outbox.delivery_failed and not self.finalizing:
            self.request_finalize("delivery_failed")

    def on_frame_sent(self, frame: Any) -> None:
        """Bookkeeping after an outbox frame went out (the pump, or the page re-entry flush)."""
        now = self.clock()
        if frame.seq or frame.t == "hb":
            self.liveness.on_message_sent(now)
        if frame.t == "wrap_up" and not frame.retransmit and self.room is not None:
            self.room.on_wrap_up_sent(frame.payload.get("ph"), now)

    async def tick(self) -> None:
        """Timers of this visit; called by :func:`visit_sweep_loop` every ``VISIT_SWEEP_INTERVAL_S``."""
        if self.finalizing:
            return
        now = self.clock()
        if not self.host.is_current():
            self.request_finalize("manager_replaced")
            return
        # 存活判定在建连期限之前：页面重载期间能力门期限按「绝对期限剩余」截短，两者同时到点时
        # 是页面没在期限内回来（local_page_lost），不是这台机器不支持串门
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
            # 已报入房、只是还在写上传头（它本身有 _JOURNAL_OPEN_MAX_S 上限）：不按入房超时结束
            self.request_finalize("relay_lost")
            return
        if self._accept_deadline is not None and now >= self._accept_deadline and not self.ready_exchanged:
            self.request_finalize("declined")
            return
        if self.grant is not None and self.joined and self._renew_task is None and self.grant.refresh_due():
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
            if await self.grant.ensure_fresh():  # type: ignore[union-attr]
                await self.transport.send(self._credentials_message(refresh=True))
        except cr.VisitRoomEnded:
            self.request_finalize("kicked")
        except cr.VisitServersError as exc:
            logger.warning("visit %s: vendor grant renewal failed (%s)", self.visit_id[:6], exc.code)
        finally:
            self._renew_task = None

    # ── 接待 ─────────────────────────────────────────────────────────

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
        # 已接受、接受期限已撤：接下来的激活与 ready 是一个整体，HTTP 请求被取消（页面断开等）也照样走完，
        # 不然这场既不会开始、也没有期限能把它拒掉，路由一直锁着。任务归运行时，收尾时一起取消
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
        except Exception as exc:  # noqa: BLE001 - 隔离会话建不起来：这场没法说话
            logger.warning("visit %s: activation failed: %r", self.visit_id[:6], exc)
            self.request_finalize("llm_error")
            return
        if self.finalizing:
            return
        # ready 发出时本侧已能接收并处理对方台词
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
        # 在收包循环里同步激活（之后的台词要有 room 才能处理），所以必须有上限：卡住就结束，不拖住心跳与 leave
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
        # 前面 send_media 等页面写入可能卡了一阵：这期间已开始收尾就不再把页面翻回进行中
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

    # ── 激活 ─────────────────────────────────────────────────────────

    async def activate(self) -> None:
        """The single activation: isolated session, proactive parked, spool, ``VisitRoom`` active."""
        if self.activated or self._activation is not None:
            return
        # 独立任务：收尾流程在收口 spool / 关会话之前等它停下（_settle_activation）
        task = asyncio.ensure_future(self._activate())
        self._activation = task
        try:
            await asyncio.wait([task])
        except asyncio.CancelledError:
            task.cancel()  # 调用方超时 / 被取消：激活一起停
            raise
        if not task.cancelled():
            task.result()  # 激活失败照常抛给调用方；被关机取消就静默返回（调用方看 finalizing）

    async def _settle_activation(self) -> None:
        """Let an in-flight activation notice ``finalizing`` and stop before teardown (bounded)."""
        task = self._activation
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), VISIT_ACTIVATION_ALLOWANCE_S)
        except asyncio.TimeoutError:
            task.cancel()
        except Exception:  # noqa: BLE001 - 激活失败由发起方处理，这里只等它结束
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
        except Exception as exc:  # noqa: BLE001 - 记忆块读不到按空开场
            logger.warning("visit %s: memory block unavailable: %s", self.visit_id[:6], type(exc).__name__)
        instructions = build_visit_instructions(
            self.lanlan_name, self.side, self.lang, persona_text=self.persona_text,
            memory_block=memory_block, peer_display=peer.display,
        )
        self.neutral_term = get_family_neutral_term(self.lang)
        if self.finalizing:
            return
        # 之后的 session / spool 一挂到 self 上，收尾流程就负责关闭（它先等激活停下）
        self.session = await self.deps.create_session(self.lanlan_name, self.side,
                                                      instructions=instructions, lang=self.lang)
        if self.finalizing:
            if self._terminated:
                # 激活拖过了收尾的等待、teardown 关会话时它还不在：晚到的客户端自己关掉
                self._close_session_in_background()
            return
        self.host.park_proactive()
        await self._open_spool(subjects)
        if self.finalizing:
            chain_pending = self._files_deferred and not self._deferred_files_done
            if self._files_done and not chain_pending and self._seal_settled() and not self._shutdown_started \
                    and not self._spool_finalized and self.spool is not None:
                # 收尾封存时 spool 还没挂上：晚到的这份自己关掉、标 finalized
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
        except Exception as exc:  # noqa: BLE001 - 对端身份推不出主体：这场不进记忆
            logger.warning("visit %s: peer subjects not derivable: %s", self.visit_id[:6], type(exc).__name__)
            self.memory_enabled = False
            return
        spool = VisitSpool(self.config_dir, self.visit_id)
        memory_on = bool(self.memory_enabled and subjects)
        try:
            await spool.write_state(new_state(
                own_uid=creds.visit_uid, own_char=self.lanlan_name, own_char_uid=self.character_uid,
                pair_id=pair_id, peer_uid=peer.uid, peer_char_id=peer_char_id, memory_enabled=memory_on,
            ))
            if memory_on:
                await spool.open({
                    "v": 1, "visit_id": self.visit_id, "role": self.side, "own_uid": creds.visit_uid,
                    "own_char": self.lanlan_name, "own_char_uid": self.character_uid, "pair_id": pair_id,
                    "peer_uid": peer.uid, "peer_char_id": peer_char_id, "peer_char_tag": peer.char_tag,
                    "started_at": self.wall(), "lang": self.lang or "und",
                }, now=self.clock())
        except Exception as exc:  # noqa: BLE001 - spool 打不开：本场不记串门记忆，对话照常
            logger.warning("visit %s: spool not opened: %s", self.visit_id[:6], type(exc).__name__)
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
        except Exception as exc:  # noqa: BLE001 - 名册是附带的：写不进不影响已打开的转录
            logger.warning("visit %s: peer roster not updated: %s", self.visit_id[:6], type(exc).__name__)

    # ── 输入接管（注册表入口）─────────────────────────────────────────

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

    # ── 收尾 ─────────────────────────────────────────────────────────

    def request_finalize(
        self, reason: str, *, peer_reason: Optional[str] = None, status_details: Optional[dict] = None,
    ) -> bool:
        """Flip to ``ending`` now and start the exit flow (idempotent, never awaits).

        Once the takeover is released ``is_visit_route_active`` is false (the
        family's typing goes to ordinary chat; before that it is refused);
        the slot stays locked until the exit flow completed.
        """
        if self._exit_task is not None or self._terminated:
            # 关机已开始（或已收尾完）：不再起新的收尾流程，免得它和关机的文件收口并发
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
        # 有人能收到才发：核验过的对端，或已在房、刚被拒绝核验的对端（让它也立刻结束）
        if self.peer is None and not self.peer_present:
            return None
        return leave_reason_for(self.finalize_reason or "", side=self.side, done_received=self.done_received)

    async def _exit_flow(self) -> None:
        reason = self.finalize_reason or "route_end"
        try:
            await self._exit_steps(reason)
        except Exception as exc:  # noqa: BLE001 - 收尾每一步都尽力而为，最后一定注销
            logger.error("visit %s: exit flow failed: %r", self.visit_id[:6], exc)
        finally:
            if self._shutdown_started:
                # 关机取消了收尾流程：文件、接管与注销都由 shutdown() 按「文件优先」的顺序做，
                # 这里不能并发 teardown（会抢在封存之前关会话、注销）
                return
            if not self._files_done:
                # 前面的步骤抛了：转录与 spool 的收口是必做的（关机路径自己收口）
                try:
                    await self._finish_files(reason)
                except Exception as exc:  # noqa: BLE001
                    logger.error("visit %s: files not finalized: %r", self.visit_id[:6], exc)
            if not self._handback_started:
                # 中途失败 / 被取消：没排上的段落不再等，回调照样交还（不丢、也不一直暂扣）
                if self.handoff is not None:
                    self.handoff.abandon()
                self._start_handback()
            await self._teardown()

    def _start_handback(self) -> None:
        self._handback_started = True
        _detach(self._hand_back_callbacks())

    async def _exit_steps(self, reason: str) -> None:
        await self.push(PHASE_ENDING, reason=reason)
        # ① 先收口本侧进行中的行：这一行的 text{final} 排在 leave 之前、进得了本场转录
        await self.close_current_line("visit_end")
        # ② 后台关闭数据通道（排空 → leave → 补传窗口），不阻塞下面任何一步
        closing = self._closing_task = asyncio.ensure_future(self._close_channel(reason))
        # ③ 回调暂扣 → 立即释放接管（此后亲人就能普通聊天）
        input_stamp = self.host.last_user_input()
        if self.takeover_token is not None:
            try:
                self.hold_token = self.host.hold_callbacks(self.inbox.accept)
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit %s: callback hold failed: %s", self.visit_id[:6], type(exc).__name__)
            try:
                self.host.release_takeover(self.takeover_token)
            except Exception as exc:  # noqa: BLE001 - token 留着：teardown 再试一次
                logger.warning("visit %s: takeover release failed: %s", self.visit_id[:6], type(exc).__name__)
            else:
                self.takeover_token = None
        if self.activated:
            self.handoff = inbox_handoff.InboxHandoff(self.visit_id, finalize_at=self.clock(), clock=self.clock)
        # ④ 回家仪式句（亲人先开口则放弃）；可选步骤：失败不挡后面的封存与交还
        if self.activated:
            try:
                await self.say_ritual(reason, input_stamp=input_stamp)
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit %s: home-coming line failed: %r", self.visit_id[:6], exc)
                if self.handoff is not None:
                    self.handoff.skip("ritual")  # 不会再播：交还不等它
        await self._settle_activation()
        # ⑤ 等关闭任务结束（数据通道要靠 iframe 发 leave，所以 iframe 留到这时）
        await asyncio.wait([closing], timeout=_CLOSE_WAIT_S)
        if not closing.done() and self.outbox.reserved_bytes:
            # 已接纳的亲人那句还在落盘（持有预留）：它入队、leave 跟上之前 teardown 不能先拆掉 pump 与传输，
            # 否则这句已落盘、已改过 room 的话到不了对端。再多等一段（有界：磁盘一直卡着就放弃）
            await asyncio.wait([closing], timeout=_RESERVED_SEND_MAX_S)
        if not closing.done():
            # 到点还没关完：先收掉接收通道（注销传输、停掉关闭任务）再封存。否则之后对端重传来的整句
            # 回了 ack、却落在封存之后，进不了转录
            logger.warning("visit %s: channel close did not finish; retiring it", self.visit_id[:6])
            closing.cancel()  # 它的 finally 先发对端欠着的 ack 与 stop{reason}，再注销
            await asyncio.wait([closing], timeout=_SHUTDOWN_TASK_WAIT_S)
            # 无条件再注销一次（幂等）：finally 里的发送还卡着、或取消落在别处时，封存之前都不再收对端的帧
            unregister_transport_session(self.transport)
        flushed = await self._flush_display(_DISPLAY_FLUSH_S)  # 告别句等整句先上屏，再发「已结束」
        self._ended_published = True  # 从这里起不再往显示队列放帧（收尾中补到的整句只进转录）
        if not flushed:
            # 页面卡着送不完：停掉显示队列（转录由 GET /state 重放），「已结束」不和它并发写、不排在它后面
            await self._retire_display(_DISPLAY_FLUSH_S)
        await self.push(PHASE_ENDED, reason=reason, peer_reason=self.peer_reason)
        if self.status_code is not None:
            await self.status(self.status_code, reason=self.status_details.get("reason"),
                              retry_after_s=self.status_details.get("retry_after_s"))
        self._set_phase(PHASE_ENDED)
        # ⑥⑦ 收口转录（先 .upload.json）→ spool finalize（后 finalized）→ 后台 digest 与上次摘要
        await self._finish_files(reason)
        # ⑧ debrief（只有真的串过门）；可选步骤，失败照样交还
        if self.activated:
            from main_routers.visit_router.debrief import run_debrief

            try:
                await run_debrief(self, input_stamp=input_stamp)
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit %s: debrief failed: %r", self.visit_id[:6], exc)
                if self.handoff is not None:
                    self.handoff.skip("debrief")
        # ⑨ 交还 VisitInbox：仪式句与简述都播完（或兜底期限）。独立任务，不占角色锁：
        # 路由 pop 之后到的 ended 经 inbox_handoff 表照样转给它
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
                # 已接纳、正在落盘的亲人那句（持有预留）要排在 leave 前面：等到它入队或放弃预留。
                # 这里不设上限：收尾流程只限时等这个关闭任务（_CLOSE_WAIT_S，持有预留时再多等
                # _RESERVED_SEND_MAX_S），到点就取消它；关机也会取消它
                while self.outbox.reserved_bytes:
                    await asyncio.sleep(0.05)
                try:
                    self.outbox.send({"t": "leave", "v": 1, "reason": leave_reason}, now=self.clock())
                except ValueError as exc:
                    # 没入队就没有补传窗口可等（leave_done 永远不会成立）
                    logger.warning("visit %s: leave not queued: %s", self.visit_id[:6], exc)
                else:
                    self.kick()
                    while not self.outbox.leave_done(self.clock()):
                        self.kick()
                        await asyncio.sleep(0.1)
            if self.peer is None:
                self._cancel_room_once()
        finally:
            try:
                try:
                    # 对端的 leave 还欠着 ack（peer_left / 两侧同时收尾）：直接送出（本侧 leave 完成后队列不再出帧），
                    # 免得对方白等补传窗口
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
                # 上面的发送被取消（退出流程到点收掉它）也要走到：注销与关 outbox 不能被跳过
                unregister_transport_session(self.transport)
                try:
                    await self.outbox.close()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("visit %s: outbox close failed: %s", self.visit_id[:6], type(exc).__name__)

    def _cancel_room_once(self) -> None:
        # host 在对端核验之前就结束：邀请码还可能被兑换，后台取消房间（不扣对方配额）。
        # 收尾流程与晚到的凭证各调一次，只发一次
        creds = self.creds
        if self.side != "host" or creds is None or self._room_cancel_sent:
            return
        self._room_cancel_sent = True
        self._room_cancel_task = self.spawn(self.deps.cancel_room(
            self.visit_id, invite_expires_at=creds.invite_expires_at, account=creds.account))
        self._room_cancel_at = asyncio.get_running_loop().time()
        # 运行时正常拆掉（已注销）之后它可能还在飞：单独登记，关机时 stop_all 也能限时等它发完
        _room_cancels.add(self._room_cancel_task)
        self._room_cancel_task.add_done_callback(_room_cancels.discard)

    def transport_alive(self) -> bool:
        from main_routers.visit_router.transport_ws import is_transport_attached

        return self.joined and is_transport_attached(self.visit_id, self.side)

    async def _settle_journal_open(self, timeout: Optional[float] = None) -> None:
        # 收尾等它写完，但有上限；还没写完就登记一条后台链：它晚到写完时立刻封存，不留没封存的流水
        # （关机除外：不起这条链，流水留给下次启动补录）
        opening = self._journal_opening
        if opening is None or opening.done():
            return
        await asyncio.wait([opening], timeout=_JOURNAL_OPEN_MAX_S if timeout is None else timeout)
        if not opening.done() and self._shutdown_started:
            # 关机：不起不设上限的后台封存链，并主动取消写上传头的任务
            self._abandon_journal_open(opening)
            return
        if not opening.done():
            reason = self.finalize_reason or "shutdown"
            ended_at = self.wall()  # 收尾这一刻：磁盘卡多久不该算进这场的时长

            # 现在就按 uid 登记（不等上传头落盘）：这段后台收尾会写 spool 与串门记忆，跑完之前角色不能改名 / 删除。
            # 它不设上限地等写盘：磁盘一直卡着时，这个角色在本进程内都改不了名、删不掉（守卫宁可保守）
            gen = _stop_gen
            chain = spawn_visit_background(self.character_uid,
                                           lambda: self._seal_after_header(opening, reason, ended_at, gen))

            def on_chain_done(task: asyncio.Task) -> None:
                # stop_all 取消这条后台链（不管落在哪一步，包括还在读角色名、_seal_after_header 都没进入时）：
                # 运行时已注销，没有别人再替它收掉写上传头的任务，与关机路径一样放弃并取消，
                # 不让它在事件循环销毁时走「等写盘结束」那条路把关机挂住
                if task.cancelled() and not opening.done():
                    self._abandon_journal_open(opening)

            chain.add_done_callback(on_chain_done)

    def _abandon_journal_open(self, opening: asyncio.Future) -> None:
        # abandon_open 之后写上传头的任务被取消就立刻结束（不再等卡住的写盘），事件循环这一层不会被它挂住；
        # 等待期间攒下的记录排在建文件之后由写线程补上。注意线程池的写盘线程不是 daemon：磁盘一直卡着时，
        # 解释器退出仍会等它（所有走线程池写盘的地方都一样）。留在磁盘上的流水由下次启动补录
        try:
            self.journal.abandon_open(self._backlog_stream_records())
        except Exception as exc:  # noqa: BLE001 - 转换失败也照样放弃并取消：关机后面的步骤一定要走到
            logger.warning("visit %s: buffered records not kept at shutdown: %s", self.visit_id[:6],
                           type(exc).__name__)
            self.journal.abandon_open()
        self._journal_backlog = []
        opening.cancel()

    async def _seal_after_header(self, opening: asyncio.Future, reason: str, ended_at: float, gen: int) -> None:
        await asyncio.wait([opening])  # 被取消时由 spawn 处的 done 回调放弃写上传头
        # 上传头没写成（异常在这里取走）：没有流水可封存，spool 照样收尾
        header_ok = not opening.cancelled() and opening.exception() is None
        await self._seal_late_journal(reason, ended_at, header_ok=header_ok, gen=gen)

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
                    records.append({"kind": "usage", "ts": float(record["ts"]), "d": delta})
            elif kind == "anomaly":
                records.append({"kind": "anomaly", "ts": float(record["ts"])})
        return records

    def _flush_journal_backlog(self) -> None:
        """The upload header landed: record what was buffered meanwhile (lines, usage, anomalies), in order.

        Synchronous: every record is in the in-memory copy before this
        returns (the stream writes follow on the writer thread), so a seal
        right after it -- or interleaved with nothing at all -- has them all.
        Every seal path calls it first; it also runs when the header lands.
        """
        if self._journal_failed():
            # 上传头写不成：积压里的行至少进内存转录（/state 重放、简述），用量与异常没有流水可记
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
                    self.journal.note_usage(record["d"], ts=record["ts"])
                elif kind == "anomaly":
                    self.journal.note_anomaly(ts=record["ts"])
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
                    # 走 _start_seal：这一场只有一个封存 future，关机 / teardown 看到的是同一次写盘
                    sealing = self._start_seal(reason, ended_at=ended_at)
                    await asyncio.wait([sealing])
                    self._take_seal(sealing)
                except Exception as exc:  # noqa: BLE001 - 封存出错也照常往下：已挂上的 spool 照样收
                    logger.warning("visit %s: late seal failed: %s", self.visit_id[:6], type(exc).__name__)
                # 封存失败也排一次：上传重试会从留下的 .upload.jsonl 重封，不必等下次启动
                try:
                    self.deps.schedule_upload(self.visit_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("visit %s: upload not scheduled: %s", self.visit_id[:6], type(exc).__name__)
            if self._files_deferred and not self._shutdown_started and not _stopped_since(gen):
                await self._finalize_spool(reason)
                self._spawn_memory_commits()
        finally:
            self._deferred_files_done = True  # 链中途出错也置位：之后才挂上的 spool 由激活那边自己收尾

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
        # 已接纳的亲人发言还在落盘（对端先走 / 传输已断时关闭通道不等预留）：先等它记进转录，与封存共用一个期限
        await self.settle_family_records(_SEAL_MAX_S)
        await self._settle_journal_open()
        if self._header_pending():
            # 上传头还在写：seal() 这时什么都不封（会立即返回 None）。封存、spool finalize、记忆提交
            # 都交给上传头落盘后的那条后台链，仍是先封存后 finalized
            self._files_deferred = True
            return False
        self._flush_journal_backlog()  # 上传头在等待中落盘：积压的记录先补进去再封存
        sealing = self._start_seal(reason)
        await asyncio.wait([sealing], timeout=max(0.0, seal_deadline - loop.time()))
        if not sealing.done():
            logger.warning("visit %s: upload seal still writing; finishing it in the background", self.visit_id[:6])
            # 按 uid 登记：后台这段跑完（会写 spool 与串门记忆）之前，这个角色不能改名 / 删除
            gen = _stop_gen
            spawn_visit_background(self.character_uid, lambda: self._finalize_after_seal(sealing, reason, gen))
            return False
        self._take_seal(sealing)
        finalizing = asyncio.ensure_future(self._finalize_spool(reason))
        await asyncio.wait([finalizing], timeout=_SEAL_MAX_S)
        if not finalizing.done():
            # spool 写盘卡住：照常注销、释放占位；记忆提交在后台等它写完再起（同样按 uid 登记）
            logger.warning("visit %s: spool still finalizing; finishing it in the background", self.visit_id[:6])
            gen = _stop_gen
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
            sealing.add_done_callback(lambda t: t.cancelled() or t.exception())  # 结果由 _take_seal 取
        return sealing

    def _seal_settled(self) -> bool:
        return not self._header_pending() and (self._sealing is None or self._sealing.done())

    def _header_pending(self) -> bool:
        opening = self._journal_opening
        return opening is not None and not opening.done()

    def _take_seal(self, sealing: asyncio.Future) -> None:
        if sealing.cancelled() or sealing.exception() is not None:
            # 封存失败：流水留着给下次启动补录
            logger.warning("visit %s: upload not sealed: %s", self.visit_id[:6],
                           "cancelled" if sealing.cancelled() else type(sealing.exception()).__name__)
            return
        self.sealed_doc = sealing.result()

    async def _finalize_spool_at_shutdown(self, spool: Any) -> None:
        try:
            await spool.close()
            changes: dict[str, Any] = {"finalized": "shutdown"}
            if self.memory_enabled and self.spool_lines > 0:
                changes.update(debrief_choice="ask_later", debrief_chip_pending=True)
            await spool.update_state(**changes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("visit %s: spool not finalized at shutdown: %s", self.visit_id[:6], type(exc).__name__)

    async def _finalize_late_spool(self, reason: str) -> None:
        # 在后台任务里再看 stop_all：派生之后它先解析角色名，这段时间里进程可能已经开始关机
        if _stopped_since(self._files_gen):
            return
        await self._finalize_spool(reason)

    async def _finalize_spool(self, reason: str) -> None:
        if self.spool is not None and not self._spool_finalized:
            self._spool_finalized = True
            try:
                await self.spool.close()
                await self.spool.update_state(finalized=reason)
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
                # 记忆关：不 digest，只把上次摘要记成已处理（commit_last_summary 自己判门控）
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
            except Exception as exc:  # noqa: BLE001 - 下次启动补录接着做
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
            # 关机取消了它也一样：暂扣的回调放出来、speech id 注销，不能一直扣着
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
        # 准入时按住的、被打断那一轮欠下的收尾（可能换代、放出排队的回调）：等退出流程（仪式句、简述）
        # 全部用完这个管理器之后才结清；准入失败 / 没接管成的路径也在这里放开
        self._release_turn_wrap_up()
        # 客户端关不掉（取消排空 / HTTP 关闭卡住）也照样注销：不能让这个角色一直锁在已结束的场次里
        await self.close_session(_SESSION_CLOSE_S)
        self.speech_router.clear()
        if self._pump_task is not None:
            self._pump_task.cancel()
        self._stop_display()
        unregister_transport_session(self.transport)
        slot = get_visit_route_state(self.lanlan_name)
        if slot is self.slot:
            finalize_visit_route_state(self.lanlan_name)
        self._set_phase(PHASE_ENDED)
        self._terminated = True
        self.ended_at_mono = self.clock()
        _prune_recent(self.ended_at_mono)
        _recent[self.visit_id] = self
        _unregister(self)
        # 封存还没落盘（后台收尾链会在落盘后排上传）：这时排的上传只会找不到 .upload.json、白退避一轮
        if self._seal_settled() and (
                self.journal.sealed or transcript_upload.upload_pending_sync(self.config_dir, self.visit_id)):
            try:
                self.deps.schedule_upload(self.visit_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit %s: upload not scheduled: %s", self.visit_id[:6], type(exc).__name__)

    # ── 关机 ─────────────────────────────────────────────────────────

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

        # 先置终态：挂起中的凭证 / 激活醒来看到 finalizing 就不再接管、不再建会话
        self._terminated = True
        self._shutdown_started = True
        if self._exit_task is not None and not self._exit_task.done():
            self._exit_task.cancel()
        self.finalize_reason = self.finalize_reason or "shutdown"
        self._set_phase(PHASE_ENDED)
        if not self._handback_started:
            # 收尾流程已取消，它的 finally 看到这个标记就不再另起交还
            # 进程要退了：不等仪式句，暂扣的回调立即交还
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
        # 收尾流程另起的关闭通道任务也停掉：关机不发 leave，也不能在封存之后还在排空
        inflight = [t for t in (self._exit_task, self._creds_task, self._activation, self._closing_task)
                    if t is not None and not t.done()]
        for task in inflight:
            task.cancel()
        if self.peer is None:
            # 没配上对的 host 房间：邀请码与配额占用别留到过期（不发 leave，但房间要取消）；
            # 最先起，与后面每一步并行
            self._cancel_room_once()
        if inflight:
            await asyncio.wait(inflight, timeout=left(_SHUTDOWN_TASK_WAIT_S))
        # 先收口本侧在说的那一行：它的 text{final} 与用量要在封存之前进上传流水
        try:
            await asyncio.wait_for(self.close_current_line("visit_end"), left(_SHUTDOWN_TASK_WAIT_S))
        except Exception as exc:  # noqa: BLE001 - 收不完也照样封存
            logger.warning("visit %s: line not closed at shutdown: %r", self.visit_id[:6], exc)
        line = self._line
        if line is not None and line.payload is not None and (line.booking is None or not line.booking.done()):
            # 这一行已收口（text{final} 已入队、已上屏），只是 LLM 还没停下、_finish_line 没跑到：
            # 先记进转录再封存，不然关机取消它之后这一行就丢了。记账是共享、受保护的任务：
            # 这里限时等到点被取消，也不会打断写入
            try:
                await asyncio.wait_for(self.book_line(line), left(_SHUTDOWN_TASK_WAIT_S))
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit %s: closed line not booked at shutdown: %r", self.visit_id[:6], exc)
        # 已接纳的亲人发言还在落盘：先等它记进转录（限时），再划封存的边界
        await self.settle_family_records(left(_SHUTDOWN_TASK_WAIT_S))
        # 封存之前先收掉接收通道：之后 iframe 还送来的可靠整句不会被收下、回 ack，却落在封存之后
        unregister_transport_session(self.transport)
        await self._settle_journal_open(left(_SHUTDOWN_TASK_WAIT_S))
        self._flush_journal_backlog()
        sealed = False
        if self._header_pending():
            # 上传头还在写：不调 seal()（它这时什么都不封、只会返回 None，还会被当成这一场的封存缓存下来）；
            # 关机不起后台封存链，流水与 spool 都留给下次启动补录
            logger.warning("visit %s: upload header still writing at shutdown", self.visit_id[:6])
        else:
            sealing = self._start_seal("shutdown")  # 退出流程已经在封存：等同一个，不再封第二次
            await asyncio.wait([sealing], timeout=left(_SHUTDOWN_TASK_WAIT_S))
            sealed = sealing.done()
            if sealed:
                self._take_seal(sealing)
            else:
                # 磁盘卡住：写盘在线程里照样落地；spool 不标 finalized（顺序是先封存后 finalized），下次启动补录
                logger.warning("visit %s: upload seal still writing at shutdown", self.visit_id[:6])
        if self.spool is not None and sealed and not self._spool_finalized:
            self._spool_finalized = True
            # 关机预算内限时：写盘卡住就不等了（线程里的写照样落地，没落地的由下次启动补录）
            closing = asyncio.ensure_future(self._finalize_spool_at_shutdown(self.spool))
            closing.add_done_callback(lambda t: t.cancelled() or t.exception())
            await asyncio.wait([closing], timeout=left(_SHUTDOWN_TASK_WAIT_S))
            if not closing.done():
                # 不登记到后台：stop_all 紧接着会取消 _detached；线程里的写盘照样落地，没落地的由启动补录
                logger.warning("visit %s: spool still finalizing at shutdown", self.visit_id[:6])
        if self.takeover_token is not None:
            try:
                self.host.release_takeover(self.takeover_token)
            except Exception:  # noqa: BLE001
                pass
            self.takeover_token = None
        # 被打断那一轮欠下的收尾：关机时不放开。放开后关机途中会话一空闲就会去结清（续期 / 换代），
        # 进程马上就退出了，计数留着也没有影响
        cancel = self._room_cancel_task
        if cancel is not None and not cancel.done():
            # 撤销房间是对外请求（邀请码与配额占用）：从它派生那一刻算至少给它 _SHUTDOWN_ROOM_CANCEL_S，
            # 前面的步骤把总预算用完了也一样（它与那些步骤并行，正常不超预算）
            floor = self._room_cancel_at + _SHUTDOWN_ROOM_CANCEL_S - loop.time()
            await asyncio.wait([cancel], timeout=max(left(_SHUTDOWN_ROOM_CANCEL_S), floor, 0.0))
        for task in list(self._tasks):
            task.cancel()
        if self._pump_task is not None:
            self._pump_task.cancel()
        self._stop_display()
        # 正常收尾由关闭通道 / teardown 做的两件事，关机路径也要做（各自限时，不超关机预算）：
        # .outbox.jsonl 带正文，不能比这场活得久；隔离会话的客户端要关掉它自己的任务与 HTTP 连接
        outbox_closing = asyncio.ensure_future(self.outbox.close())
        outbox_closing.add_done_callback(lambda t: t.cancelled() or t.exception())
        # outbox 与隔离会话同时关，共用剩余预算
        await asyncio.gather(asyncio.wait([outbox_closing], timeout=left(_SHUTDOWN_TASK_WAIT_S)),
                             self.close_session(left(_SHUTDOWN_TASK_WAIT_S)))
        if not outbox_closing.done():
            logger.warning("visit %s: outbox still closing at shutdown", self.visit_id[:6])
            self._keep_background(outbox_closing)
        unregister_transport_session(self.transport)
        slot = get_visit_route_state(self.lanlan_name)
        if slot is self.slot:
            finalize_visit_route_state(self.lanlan_name)
        _unregister(self)

    # ── 状态 ─────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """``GET /api/visit/state`` body for this character (never the invite code)."""
        creds = self.creds
        peer = self.peer
        transcript = [self.visit_line_payload_from_record(r) for r in self.journal.lines()[-50:]]
        reconnecting = self.phase != PHASE_ENDED and (
            self.liveness.self_disconnected_at is not None or self.liveness.page_departed_at is not None)
        return {
            "active": self.phase not in (PHASE_ENDED,),
            "role": self.side, "side": self.side, "visit_id": self.visit_id, "phase": self.phase,
            "transport": creds.transport if creds else None,
            "tier": creds.tier if creds else None,
            "crop": self.crop,
            "peer": None if peer is None else {
                "cat_name": peer.display, "short_id": peer.short_id, "human_label": None,
                "lang": peer.lang, "hidden": bool(self.room.peer_hidden) if self.room else False,
            },
            # 页面重载期间（传输页还没回到房间）同样算重连中：这时没有页面能收发帧
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
            logger.warning("visit %s: account mapping retry failed: %s", visit_id[:6], type(exc).__name__)


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
# 发起 / 加入（HTTP 层在 C3a-3b 调用）
# ═════════════════════════════════════════════════════════════════════


class VisitRefused(Exception):
    """Synchronous refusal of a room / join request: ``(status, body)`` for the HTTP layer."""

    def __init__(self, status: int, body: dict) -> None:
        super().__init__(body.get("code") or body.get("reason"))
        self.status = status
        self.body = body


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
    gate = await persona_gate(name)
    if not gate.ok:
        raise VisitRefused(409, {"code": "VISIT_PERSONA_UNREVIEWED", "state": gate.state})
    host = host or ManagerHost.for_character(name)
    if host is None:
        raise VisitRefused(409, {"reason": "busy"})
    vid = visit_id or secrets.token_urlsafe(16)
    # 锁检查与占位之间没有 await：查完立刻占位，别的路由插不进来；占位之后它必为真，不能再查
    if is_external_route_locked(name):
        reason = "already_visiting" if name in _runtimes or get_visit_route_state(name) else "route_owned"
        raise VisitRefused(409, {"code": "VISIT_E_BUSY", "reason": reason})
    key = (vid, side)
    if key in _pending_visits or any(r.visit_id == vid and r.side == side for r in _runtimes.values()):
        # 锁按角色：另一个角色正以同一侧进同一场（同一张邀请），不能互相顶掉登记
        raise VisitRefused(409, {"code": "VISIT_E_BUSY", "reason": "visit_in_progress"})
    slot = activate_visit_route(name, phase=PHASE_PENDING, visit_id=vid)
    _pending_visits.add(key)
    try:
        failure = host.precondition_failure()
        if failure is not None:
            raise VisitRefused(409, {"reason": failure})
        account = await _local_account()
        if _stop_gen != stop_gen or _stopping:
            # 入场途中 stop_all 跑过 / 正在跑（它只看得到已登记的运行时）：不再登记、不再起传输
            raise VisitRefused(409, {"code": "VISIT_E_BUSY", "reason": "shutdown"})
        # 查账号期间（还没登记运行时、输入还不归串门）语音会话 / 热切换可能已经起来、manager 也可能被换掉：再查一次
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
        _pending_visits.discard(key)
        if get_visit_route_state(name) is slot:
            finalize_visit_route_state(name)
        raise
    rt.slot = slot
    rt.start()
    _pending_visits.discard(key)
    return rt


async def _local_account() -> Optional[str]:
    from main_routers.visit_router.accounts import local_account

    try:
        return await local_account()
    except Exception:  # noqa: BLE001 - 读不出本机登录态按未登录
        return None


# ═════════════════════════════════════════════════════════════════════
# 注册表入口
# ═════════════════════════════════════════════════════════════════════


async def route_stream_message(lanlan_name: str, message: dict) -> bool:
    rt = _runtimes.get(str(lanlan_name or ""))
    if rt is None or not is_visit_route_active(lanlan_name):
        return False
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
            # 还没开始聊：叫回来就是直接结束
            started = rt.request_finalize("recall")
            return 200, {"ok": True, "mode": "finalize", "exit_task_started": started}
        if rt.room.phase != "active" or rt.finalizing:
            return 409, {"code": "VISIT_RECALL_ALREADY"}
        rt.apply_effects(rt.room.on_local_recall(rt.clock()))
        return 200, {"ok": True, "mode": "wrap_up", "exit_task_started": False}
    started = rt.request_finalize("route_end")
    return 200, {"ok": True, "mode": "finalize", "exit_task_started": started}


async def stop_all(reason: str = "shutdown") -> None:
    """Shutdown hook (PR-09b, within ``VISIT_SHUTDOWN_BUDGET_S``): files first, never a ``leave``."""
    global _stop_gen, _stopping
    _stop_gen += 1  # 还挂在入场途中的 start_visit 醒来后看到它就不登记
    _stopping = True  # stop_all 进行中新到的入场同样拒绝
    try:
        runtimes = list(_runtimes.values())
        # 已拆掉的场次发出的撤销房间请求（邀请码与配额占用）：与各场关机并行限时等它发完，到点才取消，
        # 不在关机之后再多出一段
        # 还登记着的场次自己发出的那份不算：它的 shutdown() 按自己的规则（保底时长）等
        owned = {rt._room_cancel_task for rt in runtimes}
        orphans = [t for t in _room_cancels if not t.done() and t not in owned]

        async def settle_orphan_cancels() -> None:
            if orphans:
                await asyncio.wait(orphans, timeout=_SHUTDOWN_ROOM_CANCEL_S)
                for task in orphans:
                    task.cancel()

        results = await asyncio.gather(*(rt.shutdown() for rt in runtimes), settle_orphan_cancels(),
                                       return_exceptions=True)
        for rt, result in zip(runtimes, results):
            if isinstance(result, BaseException):
                # 这一场没封存 / 没 finalize，留给下次启动补录：至少在日志里能查到是哪一场、出了什么错
                logger.error("visit %s: shutdown failed: %r", rt.visit_id[:6], result, exc_info=result)
        # 脱离运行时的后台任务（账号映射补写、交还回调、没关完的会话）也一并停掉，不留给事件循环销毁
        # 按角色登记的后台写入（digest、最后总结、延后的 spool 收尾、启动补录）同样停掉：没写完的留给下次启动补录
        # 还没完成的撤销房间请求（某一场 shutdown() 半路出错、没走到它自己的撤销等待）同样取消再限时等
        # （正常路径下这时都已完成）
        detached = [t for t in _detached if not t.done()]
        detached += [t for bucket in list(_visit_bg_tasks.values()) for t in list(bucket) if not t.done()]
        detached += [t for t in _room_cancels if not t.done()]
        for task in detached:
            task.cancel()
        if detached:
            await asyncio.wait(detached, timeout=_SHUTDOWN_TASK_WAIT_S)
    finally:
        _stop_gen += 1  # stop_all 进行中才开始、它结束后才醒的入场：代数已变，同样不登记
        _stopping = False


async def visit_sweep_loop() -> None:
    """Every ``VISIT_SWEEP_INTERVAL_S``: each runtime's timers (PR-09b starts it after startup)."""
    while True:
        await asyncio.sleep(VISIT_SWEEP_INTERVAL_S)
        _prune_recent(time.monotonic())
        for rt in list(_runtimes.values()):
            try:
                await rt.tick()
            except Exception as exc:  # noqa: BLE001 - 一场的计时出错不拖累别的场次
                logger.warning("visit %s: tick failed: %r", rt.visit_id[:6], exc)


def _wire_upload_hooks() -> None:
    # 转录补传 / 举报重试跳过本进程在飞的场次（transcript_upload 留的钩子）
    from main_routers.visit_router import transcript_upload

    transcript_upload.is_live = is_visit_live


_wire_upload_hooks()


def register_visit_route_kind() -> None:
    """Register the ``neko_visit`` external route kind (import time; tests re-run it)."""
    from utils.external_route_registry import ExternalRouteKind, register_external_route_kind

    register_external_route_kind(ExternalRouteKind(
        kind="neko_visit",
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
    "get_runtime", "get_runtime_by_visit", "recent_runtime", "on_page_signal",
]
