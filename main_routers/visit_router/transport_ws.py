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

"""``WS /api/visit/transport/ws``: the transport iframe <-> local backend socket (§4.3, OD-29).

The decorator path is RELATIVE (``/transport/ws``); PR-09a includes this
router under ``APIRouter(prefix='/api/visit')``.

Handshake: before ``accept`` the real peer must be loopback (no proxy mode,
no proxy headers; ``local_guard``) and the Origin must be local; after
``accept`` the first frame must be ``{type:'auth', csrf_token}`` within 5 s,
otherwise close 4403 and nothing sent before it is processed. Text frames
only, each <= 16 KB (larger closes 1009). The vendor grant is sent on this
socket and nowhere else, and never logged.

Close codes the iframe acts on: terminal (do not reconnect; the parent page
removes the iframe) -- 4403 unauthorized, 4404 unknown visit / page reload
deadline passed, 4409 superseded; reconnect -- 1000 normal, 1011 a downlink
could not be written (the page reloads through the normal grace); protocol
errors -- 4400 bad request, 1003 binary frame, 1009 frame too large.

Upstream: ``auth`` (first), ``caps{stage:'preflight'}`` (once; written into
the visit route state, then the first ``credentials`` when it passed),
``caps{stage:'sdk'}`` (once, after ``credentials``), ``state``, ``recv``,
``stats``, ``tx_backpressure``. Downstream: ``credentials`` (first issue once
per connection, ``refresh:true`` any number of times), ``media``, ``send``,
``stop`` (once).

The visit runtime (PR-09a) plugs in by registering a
:class:`VisitTransportSession` per ``(visit_id, side)``; an unknown pair
closes 4404. A second connection for the same pair replaces the first
(the old one gets 4409 and must not reconnect). When the current connection
drops: ``liveness.on_page_lost`` + ``outbox.pause(PAUSE_PAGE_RELOAD)``. A
replacement connection keeps the pause and calls
``liveness.on_page_socket_back`` (the SDK reload gets the absolute reload
deadline of design §4.8); only once its iframe is back in the vendor room
(first ``state`` joined / connected, after its own preflight and
credentials, before the deadline) does the session resend ``hello``, resume
the outbox and clear the deadline (``liveness.on_page_back``), then exactly
one full ``media`` snapshot from
``session.media_snapshot()`` follows. A re-entry whose preparation failed is
retried on the next ``joined`` / ``connected`` report or 5 s ``stats`` frame
of that connection while it is in the room. A replacement socket that
shows up after the reload deadline is closed 4404 (terminal for the iframe,
which leaves the vendor room when its socket closes) -- on attach, before its
credentials are issued, or at the latest when it reports being in the room.
Downlink goes only through
:meth:`VisitTransportSession.send` (bound to the registered session).
"""

from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketState

from config.visit_settings import (
    VISIT_LIVEKIT_PUBLISH,
    VISIT_TIERS,
)
from main_logic.visit.outbox import PAUSE_PAGE_RELOAD
from main_routers.visit_router.credentials import VisitCredentials, allowed_livekit_hosts
from main_routers.visit_router.local_guard import (
    UNAUTHORIZED_CODE,
    local_peer_allowed,
    valid_auth_frame,
    websocket_origin_allowed,
)
from utils.logger_config import get_module_logger
from utils.visit_route_state import get_visit_route_state
from utils.visit_wire import VISIT_ID_RE

router = APIRouter()
logger = get_module_logger(__name__, "Main")

FRAME_MAX_BYTES = 16 * 1024
"""Upper bound of one JSON text frame, both directions."""

CREDENTIALS_MAX_BYTES = 8 * 1024
"""Upper bound of one ``credentials`` message."""

AUTH_TIMEOUT_S = 5.0
"""The ``auth`` frame must arrive within this long after ``accept``."""

CLOSE_LOCK_WAIT_S = 2.0
"""A close waits at most this long for an in-flight send before closing anyway."""

CLOSE_BAD_REQUEST = 4400
CLOSE_UNAUTHORIZED = 4403
CLOSE_UNKNOWN_VISIT = 4404
CLOSE_SUPERSEDED = 4409
CLOSE_TOO_LARGE = 1009
CLOSE_UNSUPPORTED_DATA = 1003
CLOSE_NORMAL = 1000
CLOSE_SEND_FAILED = 1011

SIDES = ("host", "guest")
DOWNLINK_TYPES = frozenset({"credentials", "media", "send", "stop"})
PREFLIGHT_REASONS = frozenset({"insecure_context", "foreign_websocket", "no_webrtc"})
SDK_REASONS = frozenset({"sdk_unsupported", "sdk_load_failed", "no_encoder"})
REJOINED_STATES = frozenset({"joined", "connected"})
OUT_OF_ROOM_STATES = frozenset({"joining", "reconnecting", "left", "kicked"})
STATES = REJOINED_STATES | OUT_OF_ROOM_STATES | {"error"}
CODECS = ("vp9", "vp8", "h264")
CROPS = ("upper", "full")

_UA_MAX_CHARS = 200
_CODECS_MAX_ITEMS = 16
_VID_LEN = 26


# ── 下行消息构造 ───────────────────────────────────────────────────────


def build_credentials_message(
    creds: VisitCredentials,
    *,
    side: str,
    crop: str,
    codec: str,
    peer_vid: str | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Build the downlink ``credentials`` message (§4.3) from validated credentials.

    Only the selected vendor is included. ``peer_vid`` comes from Servers on
    the guest side and is ``None`` on the host side until the peer ``hello``
    verified (``media{peer_vid}`` fills it later); a host may pass the
    verified one when re-issuing after a reload. ``publish`` carries the
    single-layer encoder profile (no ``publish_video``: publish / subscribe
    timing is driven only by ``media``).
    """
    if side not in SIDES or side != creds.role:
        raise ValueError("side must match the credentials role")
    if crop not in CROPS:
        raise ValueError("crop must be 'upper' or 'full'")
    if codec not in CODECS:
        raise ValueError("codec must be one of vp9 / vp8 / h264")
    tier = VISIT_TIERS.get(creds.tier)
    if tier is None:
        raise ValueError("unknown tier")
    if crop == "full" and not tier.get("crop_full"):
        raise ValueError("this tier has no full-body crop")
    msg: dict[str, Any] = {
        "type": "credentials",
        "visit_id": creds.visit_id,
        "side": side,
        "transport": creds.transport,
        "vendor": {creds.transport: dict(creds.vendor[creds.transport])},
        "own_vid": creds.vid,
        "peer_vid": creds.peer_vid if side == "guest" else peer_vid,
        "allowed_hosts": sorted(allowed_livekit_hosts()),
        "tier": creds.tier,
        "crop": crop,
        "publish": {
            "codec": codec,
            "bitrate_kbps": tier["video_kbps"],
            "fps": tier["fps"],
            "scalability_mode": VISIT_LIVEKIT_PUBLISH["scalabilityMode"],
            "simulcast": VISIT_LIVEKIT_PUBLISH["simulcast"],
            "degradation": VISIT_LIVEKIT_PUBLISH["degradationPreference"],
        },
        "expires_at": creds.expires_at,
    }
    if refresh:
        msg["refresh"] = True
    return msg


# ── 运行时接口 ─────────────────────────────────────────────────────────


class VisitTransportSession(ABC):
    """Runtime side of one ``(visit_id, side)`` transport (implemented by PR-09a).

    ``liveness`` / ``outbox`` are the ``VisitLiveness`` / ``VisitOutbox`` of
    this visit; the default page hooks drive them as §4.3 requires. Every
    hook runs on the event loop and must not block. Hooks must not log
    message contents (the vendor grant and peer text pass through here).
    """

    def __init__(self, *, visit_id: str, side: str, lanlan_name: str, liveness: Any, outbox: Any) -> None:
        if side not in SIDES:
            raise ValueError("side must be 'host' or 'guest'")
        if not isinstance(visit_id, str) or VISIT_ID_RE.fullmatch(visit_id) is None:
            raise ValueError("malformed visit_id")
        self.visit_id = visit_id
        self.side = side
        self.lanlan_name = lanlan_name
        self.liveness = liveness
        self.outbox = outbox

    # —— 上行回调（runtime 实现）——

    @abstractmethod
    async def on_preflight(self, caps: dict[str, Any]) -> None:
        """``caps{stage:'preflight'}`` (already written into the route state as ``caps_preflight``;
        the runtime must keep ``visit_id`` in its slot, a slot of another visit is left alone).

        ``preflight_ok:false`` → finalize ``'unsupported'`` without contacting
        Servers (no quota spent); no ``credentials`` will be sent.
        """

    @abstractmethod
    async def issue_credentials(self) -> Optional[dict[str, Any]]:
        """Return the first ``credentials`` message of this connection, or None.

        Fetch / renew as needed (vendor grant with less than
        ``VISIT_VENDOR_REFRESH_MARGIN_S`` left is renewed first; the identity
        ticket is reused). None = nothing to send (e.g. the ticket expired and
        the runtime finalizes instead).
        """

    @abstractmethod
    async def on_sdk_caps(self, caps: dict[str, Any]) -> None:
        """``caps{stage:'sdk'}``; ``transport_ok:false`` → finalize ``'unsupported'``."""

    @abstractmethod
    async def on_state(self, msg: dict[str, Any]) -> None:
        """Vendor connection ``state`` report (reconnecting / kicked / peer presence ...)."""

    @abstractmethod
    async def on_recv(self, *, from_vid: str, cmd: int, payload: dict[str, Any], nbytes: int) -> None:
        """A reassembled data-channel message; per-sender rate limiting happens here (``PeerRateLimiter``)."""

    @abstractmethod
    def media_snapshot(self) -> dict[str, Any]:
        """Full ``media`` state rebuilt from the phase and the media state before the reload.

        Guest ``{publish, crop, ladder}``, host ``{subscribe, peer_vid?, crop,
        ladder, peer_crop}``; ``publish`` / ``subscribe`` stay false until
        ``ready`` was exchanged or when video is unavailable.
        """

    async def on_stats(self, msg: dict[str, Any]) -> None:
        """5 s ``stats`` report (default: ignored)."""

    async def on_backpressure(self, msg: dict[str, Any]) -> None:
        """``tx_backpressure``: pause ``typing`` / new deltas while on."""
        self.outbox.set_backpressure(bool(msg.get("on")))

    # —— 页面生命周期（默认实现即 §4.3 的规则）——

    def on_page_lost(self, now: float) -> None:
        """The current transport socket dropped: the reload deadline runs, delivery timers pause.

        The timing lives in ``VisitLiveness.on_page_lost`` (socket stage:
        ``min(this drop + 20 s, absolute)``). When ``tick`` later reports
        ``local_page_lost`` the page is not in the vendor room (the transport
        closes a page that comes back too late 4404, and an iframe leaves the
        room when its socket closes), so no data-channel ``leave``
        can go out; the peer sees the vendor-level leave and ends within its
        rejoin grace / heartbeat clock.
        """
        self.liveness.on_page_lost(now)
        self.outbox.pause(now, reason=PAUSE_PAGE_RELOAD)

    def on_page_attached(self, now: float) -> None:
        """A replacement socket authenticated: the page is NOT back in the room yet.

        The SDK reload and re-entry get the absolute reload deadline of
        design §4.8 (``min(start + 30 s, last send + 27 s)``;
        ``VisitLiveness.on_page_socket_back``) -- not only what is left of
        the socket's 20 s. The capability gate's own
        ``VISIT_CAPS_SDK_TIMEOUT_S`` (``min(20 s, absolute remaining)``) is
        the runtime's timer.
        A socket that replaced a live one starts the reload now. The outbox
        stays paused; only :meth:`on_page_rejoin_committed` clears the deadline, so a
        page that never re-enters (failed preflight, no credentials, SDK never
        joins) or keeps reconnecting still ends in ``local_page_lost``.
        """
        self.liveness.on_page_socket_back(now)
        self.outbox.pause(now, reason=PAUSE_PAGE_RELOAD)

    def on_page_rejoined(self, now: float) -> None:
        """The replacement iframe is back in the vendor room: queue ``hello`` first, resume.

        Synchronous on purpose: the transport calls it only while the socket
        is still the current one and sends what the outbox releases right
        after it on that same socket, so a socket replaced meanwhile can never
        resume the outbox or flush into its successor. The page deadline is
        NOT cleared here: if anything later in the re-entry fails, the
        transport pauses the outbox again and the deadline keeps running;
        :meth:`on_page_rejoin_committed` clears it once everything succeeded.
        """
        self.outbox.resend_hello(now)
        self.outbox.resume(now, reason=PAUSE_PAGE_RELOAD)

    def on_page_rejoin_committed(self, now: float) -> None:
        """The whole re-entry was prepared: the reload is over, clear the page deadline."""
        self.liveness.on_page_back(now)

    def now(self) -> float:
        """Clock of the lifecycle callbacks: the one ``liveness`` / ``outbox`` run on (monotonic)."""
        return time.monotonic()

    # —— 下行 ——

    async def send(self, msg: Mapping[str, Any]) -> bool:
        """Send a downlink message on the current socket of THIS session.

        False when no socket is attached, or when this session was replaced
        or unregistered (a stale runtime never writes into its successor).
        """
        if msg.get("type") not in DOWNLINK_TYPES:
            raise ValueError("unknown downlink type")
        link = _links.get((self.visit_id, self.side))
        if link is None or link.session is not self or link.conn is None:
            return False
        return await _send_on(link.conn, msg)


# ── 连接登记 ───────────────────────────────────────────────────────────


@dataclass(eq=False)
class _Connection:
    websocket: WebSocket
    reattach: bool
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False
    retired: bool = False
    credentials_sent: bool = False
    credentials_reserved: bool = False
    stop_sent: bool = False
    stop_reserved: bool = False
    preflight_seen: bool = False
    preflight_ok: bool = False
    sdk_seen: bool = False
    in_room: bool = False
    rejoined: bool = False
    media_seq: int = 0
    media_last_ok: int = 0
    media_pending: set[int] = field(default_factory=set)
    close_code: Optional[int] = None
    close_reason: str = ""
    link: Optional["_Link"] = field(default=None, repr=False)

    async def send_json(self, msg: Mapping[str, Any], *, text: Optional[str] = None) -> bool:
        """Send one downlink; ``text`` is ``_encode_downlink(msg)`` when the caller already has it."""
        try:
            if text is None:
                text = _encode_downlink(msg)
            else:
                # 预编码的文本同样受大小上限约束（它与 msg 是否一致由 _media_frame 保证）
                _check_downlink_size(msg, len(text.encode("utf-8")))
        except _DownlinkRejected as exc:
            # 只是这一条发不出去，不能被接收循环当成断线把正常的 socket 关掉
            logger.warning("visit transport: dropping %s downlink: %s", msg.get("type"), exc)
            return False
        async with self.send_lock:
            # 排队等锁期间可能已被顶掉或关闭：拿到锁后两样都要复查
            if self.closed or self.retired:
                return False
            try:
                await self.websocket.send_text(text)
            except Exception as exc:  # noqa: BLE001 - 断开中的 socket：当作未送达
                logger.warning("visit transport: %s downlink not written, dropping the socket: %s",
                               msg.get("type"), type(exc).__name__)
                # 写不出去的 socket 不能留着：stop 失败用终态 4404（iframe 不再重连、不再领凭证入房），
                # 其它用 1011（iframe 重连，走正常的页面重载）
                _abandon(self, CLOSE_UNKNOWN_VISIT if msg.get("type") == "stop" else CLOSE_SEND_FAILED,
                         "send failed")
                return False
        return True

    async def close(self, code: int, reason: str = "") -> None:
        """Close once. ``closed`` flips first so queued sends give up; waits at most
        ``CLOSE_LOCK_WAIT_S`` for an in-flight send (a backpressured ``send_text``
        may never return) and at most as long again for the close frame itself,
        so the background close task always ends. A socket whose writes never
        drain cannot deliver the close frame; the server's ping timeout reaps it."""
        if self.closed:
            return
        self.closed = True
        try:
            await asyncio.wait_for(self.send_lock.acquire(), CLOSE_LOCK_WAIT_S)
            locked = True
        except asyncio.TimeoutError:
            locked = False
        try:
            if self.websocket.application_state != WebSocketState.DISCONNECTED:
                await asyncio.wait_for(self.websocket.close(code=code, reason=reason), CLOSE_LOCK_WAIT_S)
        except Exception as exc:  # noqa: BLE001 - 含超时：写不出去的关闭帧交给服务端心跳超时回收
            logger.debug("visit transport: close failed: %s", type(exc).__name__)
        finally:
            if locked:
                self.send_lock.release()


@dataclass(eq=False)
class _Link:
    session: VisitTransportSession
    conn: Optional[_Connection] = None
    connections_seen: int = 0


_links: dict[tuple[str, str], _Link] = {}


def register_transport_session(session: VisitTransportSession) -> None:
    """Make ``(session.visit_id, session.side)`` connectable (replaces an older session)."""
    key = (session.visit_id, session.side)
    old = _links.get(key)
    _links[key] = _Link(session=session)
    if old is not None and old.conn is not None:
        # 4409 是终态：旧 runtime 的 iframe 不能重连进新登记的 session、顶掉新 runtime 自己的连接
        _spawn_close(old.conn, CLOSE_SUPERSEDED, "visit replaced")


def unregister_transport_session(session: VisitTransportSession) -> None:
    """Forget the session; its socket (if any) is closed 1000.

    A runtime that wants the iframe to leave first sends ``stop`` through
    :meth:`VisitTransportSession.send` before unregistering.
    """
    key = (session.visit_id, session.side)
    link = _links.get(key)
    if link is None or link.session is not session:
        return
    del _links[key]
    if link.conn is not None:
        _spawn_close(link.conn, CLOSE_NORMAL, "visit ended")


def get_transport_session(visit_id: str, side: str) -> Optional[VisitTransportSession]:
    """The registered session of ``(visit_id, side)``, or None."""
    link = _links.get((visit_id, side))
    return link.session if link is not None else None


def is_transport_attached(visit_id: str, side: str) -> bool:
    """True while an authenticated socket serves ``(visit_id, side)``."""
    link = _links.get((visit_id, side))
    return link is not None and link.conn is not None and not link.conn.closed and not link.conn.retired


_close_tasks: set[asyncio.Task] = set()


def _abandon(conn: _Connection, code: int, reason: str) -> None:
    """Retire and close a socket that can no longer be written, and treat it as a lost page now.

    Synchronous unbind + ``on_page_lost``: a half-open socket may keep the
    receive loop waiting until the server ping times out, and the reload
    deadline must run meanwhile. The loop's own ``finally`` then sees the
    link already unbound and does not start a second reload.
    """
    _spawn_close(conn, code, reason)
    link = conn.link
    if link is None or _links.get((link.session.visit_id, link.session.side)) is not link or link.conn is not conn:
        return
    link.conn = None
    try:
        link.session.on_page_lost(link.session.now())
    except Exception as exc:  # noqa: BLE001
        logger.warning("visit transport: on_page_lost failed: %s", type(exc).__name__)


def _spawn_close(conn: _Connection, code: int, reason: str) -> None:
    # 先同步退役：关闭任务真正跑起来之前，仍卡在 await 里的 handler 恢复后也发不出任何下行。
    # 关闭码也同步记下：接收循环有缓冲帧时不让出事件循环，会先走到 finally 自己关，
    # 那里要用这个码（4404 / 4409 是终态），不能被默认的 1000 抢先
    conn.retired = True
    if conn.close_code is None:
        conn.close_code, conn.close_reason = code, reason
    task = asyncio.ensure_future(conn.close(code, reason))
    _close_tasks.add(task)
    task.add_done_callback(_close_tasks.discard)


async def _send_on(conn: _Connection, msg: Mapping[str, Any], *, text: Optional[str] = None) -> bool:
    """Send on one specific connection, enforcing the per-connection downlink rules.

    A first-issue ``credentials`` (no ``refresh``) needs a passed preflight on
    this connection and goes out once; ``stop`` goes out once. Returns False
    when nothing was sent (rule violation, oversize, unserializable, closing).

    The once-only slots (first ``credentials``, ``stop``) are reserved while
    the send is in flight and consumed only when it succeeded, so a failed
    send does not burn them and two concurrent senders cannot both pass.
    """
    if conn.closed or conn.retired:
        return False
    kind = msg.get("type")
    first_credentials = kind == "credentials" and not msg.get("refresh")
    if first_credentials:
        # 首发只在本连接预检通过之后（§4.3）：runtime 出错时也不能绕过
        if conn.credentials_sent or conn.credentials_reserved or not conn.preflight_ok:
            logger.warning("visit transport: refusing a first-issue credentials (duplicate or no passed preflight)")
            return False
        conn.credentials_reserved = True
    elif kind == "credentials" and not conn.credentials_sent:
        # 续期只替换已有凭证：首发之前没有可替换的
        return False
    if kind == "stop":
        if conn.stop_sent or conn.stop_reserved:
            return False
        conn.stop_reserved = True
    media_seq = 0
    if kind == "media":
        # 排进发送锁之前同步编号：重入兜底快照据此判断期间 runtime 是否有更新的 media
        # 已发出或仍在排队（发送失败的不算）
        conn.media_seq += 1
        media_seq = conn.media_seq
        conn.media_pending.add(media_seq)
    ok = False
    try:
        ok = await conn.send_json(msg, text=text)
    finally:
        if first_credentials:
            conn.credentials_reserved = False
        if kind == "stop":
            conn.stop_reserved = False
        if media_seq:
            conn.media_pending.discard(media_seq)
            if ok:
                conn.media_last_ok = max(conn.media_last_ok, media_seq)
    if ok and first_credentials:
        conn.credentials_sent = True
    if ok and kind == "stop":
        conn.stop_sent = True
    return ok


def _newer_media(conn: _Connection, mark: int) -> bool:
    """True when a ``media`` queued after ``mark`` was sent or is still waiting to be sent."""
    return conn.media_last_ok > mark or any(seq > mark for seq in conn.media_pending)


def _reset_for_tests() -> None:
    """Forget every registration (unit tests only)."""
    _links.clear()


# ── 上行处理 ───────────────────────────────────────────────────────────


def _is_int(value: Any) -> bool:
    return type(value) is int


def _enum(value: Any, allowed: frozenset[str]) -> Optional[str]:
    """``value`` when it is one of ``allowed``, else None (an unhashable value must not raise)."""
    return value if isinstance(value, str) and value in allowed else None


def _record_preflight(session: VisitTransportSession, msg: dict[str, Any], ok: bool) -> dict[str, Any]:
    caps = {
        "stage": "preflight",
        "preflight_ok": ok,
        "reason": _enum(msg.get("reason"), PREFLIGHT_REASONS),
        "is_secure_context": bool(msg.get("is_secure_context")),
        "ua": str(msg.get("ua") or "")[:_UA_MAX_CHARS],
        "at": time.time(),
    }
    # 能力门缓存（PR-09a 读）：只写进本角色仍在的串门槽，不建新槽
    # 槽按角色名登记，同一角色可能已开了下一场：只写进 visit_id 对得上的那一场
    slot = get_visit_route_state(session.lanlan_name)
    if slot is not None and slot.get("visit_id") == session.visit_id:
        slot["caps_preflight"] = dict(caps)
    return caps


def _sdk_caps(msg: dict[str, Any]) -> dict[str, Any]:
    codecs = msg.get("codecs")
    codec_list = [c[:64] for c in codecs if isinstance(c, str)][:_CODECS_MAX_ITEMS] if isinstance(codecs, list) else []
    return {
        "stage": "sdk",
        "transport_ok": msg.get("transport_ok") is True,
        "video_ok": msg.get("video_ok") is True,
        "reason": _enum(msg.get("reason"), SDK_REASONS),
        "codecs": codec_list,
    }


_HOOK_FAILED = object()


class _DownlinkRejected(ValueError):
    """A downlink that cannot go out: unserializable or over its size limit."""


def _encode_downlink(msg: Mapping[str, Any]) -> str:
    """The exact text a downlink goes out as; raises ``_DownlinkRejected``.

    The only place that decides what can be sent: ``send_json`` and the
    re-entry snapshot check both use it, so they never disagree.
    """
    try:
        # allow_nan=False：NaN / Infinity 不是合法 JSON；set / bytes / 孤立代理字符等同样发不出去
        text = json.dumps(msg, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        size = len(text.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise _DownlinkRejected(f"unserializable ({type(exc).__name__})") from None
    _check_downlink_size(msg, size)
    return text


def _check_downlink_size(msg: Mapping[str, Any], size: int) -> None:
    limit = CREDENTIALS_MAX_BYTES if msg.get("type") == "credentials" else FRAME_MAX_BYTES
    if size > limit:
        raise _DownlinkRejected(f"oversize ({size} B)")


def _media_frame(snapshot: Any) -> tuple[dict[str, Any], str]:
    """The ``media`` downlink built from a ``media_snapshot()`` result, and its encoded text.

    Raises ``TypeError`` / ``_DownlinkRejected`` when it could not go out.
    """
    if not isinstance(snapshot, Mapping):
        raise TypeError("media_snapshot must return a mapping")
    msg = {**snapshot, "type": "media"}
    return msg, _encode_downlink(msg)


async def _call(session: VisitTransportSession, hook: str, *args: Any, **kwargs: Any) -> Any:
    """Await a runtime hook; an exception is logged and returns ``_HOOK_FAILED``."""
    try:
        return await getattr(session, hook)(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - runtime 回调出错不能拖垮这条 socket
        logger.warning("visit transport: %s hook failed: %s", hook, type(exc).__name__)
        return _HOOK_FAILED


def _attach(link: _Link, websocket: WebSocket) -> _Connection:
    """Make a new authenticated socket the current one of ``link``.

    Synchronous on purpose: taking over never waits on the replaced socket,
    which may be stuck in a backpressured ``send_text`` holding its send lock.
    """
    conn = _Connection(websocket=websocket, reattach=link.connections_seen > 0, link=link)
    previous = link.conn
    link.conn = conn
    link.connections_seen += 1
    if previous is not None:
        # 被顶掉的旧连接收 4409：对它是终态（不重连），否则两个 iframe 会互相驱逐。
        # 先同步退役（之后发往它的一律拒），关闭放后台
        previous.retired = True
        _spawn_close(previous, CLOSE_SUPERSEDED, "superseded")
    if conn.reattach:
        try:
            link.session.on_page_attached(link.session.now())
        except Exception as exc:  # noqa: BLE001
            logger.warning("visit transport: on_page_attached failed: %s", type(exc).__name__)
    return conn


def _is_current(link: _Link, conn: _Connection) -> bool:
    return (
        _links.get((link.session.visit_id, link.session.side)) is link
        and link.conn is conn and not conn.closed and not conn.retired
    )


def _reload_expired(session: VisitTransportSession, now: Optional[float] = None) -> Optional[bool]:
    """True when the page reload of ``session`` already missed its deadline; None when the check failed."""
    try:
        return bool(session.liveness.page_expired(session.now() if now is None else now))
    except Exception as exc:  # noqa: BLE001 - 帧处理路径上不能因此结束接收循环
        logger.warning("visit transport: page deadline check failed: %s", type(exc).__name__)
        return None


def _close_if_late(
    conn: _Connection, session: VisitTransportSession, now: Optional[float] = None,
) -> Optional[bool]:
    """Close a replacement page that missed its reload deadline; True when it did.

    False when it is not late (or not a replacement page), None when the
    deadline could not be checked (callers that would commit something treat
    that as "do not").

    4404 is terminal for the iframe (the parent page removes it) and an iframe
    leaves the vendor room when its socket closes, so no ``stop`` is needed
    and nothing waits on the send lock. Retired synchronously: no later frame
    of it is handled.
    """
    if not conn.reattach or conn.rejoined:
        return False
    expired = _reload_expired(session, now)
    if not expired:
        return expired
    logger.warning("visit transport: page reload deadline passed, closing the late page")
    _spawn_close(conn, CLOSE_UNKNOWN_VISIT, "page reload deadline passed")
    return True


async def _handle_frame(
    link: _Link, conn: _Connection, msg: dict[str, Any], nbytes: int, visit_id: str, side: str,
) -> None:
    # 被顶掉（4409）或场次已注销的连接，后续帧一概不交给 runtime
    if not _is_current(link, conn):
        return
    session = link.session
    # 重载期限已过的替换页面：任何帧都不再交给 runtime（预检写路由状态、sdk 能力都不做），直接关掉
    if _close_if_late(conn, session):
        return
    kind = msg.get("type")
    if kind == "caps":
        stage = msg.get("stage")
        if stage == "preflight":
            if conn.preflight_seen:
                return
            conn.preflight_seen = True
            ok = msg.get("preflight_ok") is True
            caps = _record_preflight(session, msg, ok)
            # runtime 没能处理预检（例如更新串门状态失败）就不去 Servers 领凭证，
            # 也不认这次预检：首发凭证闸门只在 hook 成功之后才放行
            if await _call(session, "on_preflight", caps) is _HOOK_FAILED or not ok:
                return
            conn.preflight_ok = True
            # 等 on_preflight 期间可能已被顶掉：领凭证有 Servers 侧副作用（签发记录、配额），不为它白领一份
            if not _is_current(link, conn):
                return
            # 等 on_preflight 期间期限可能刚过（或查不了）：不为它领凭证，过期就直接关掉
            if _close_if_late(conn, session) is not False:
                return
            creds = await _call(session, "issue_credentials")
            if creds is None or creds is _HOOK_FAILED:
                return
            if not isinstance(creds, Mapping) or creds.get("type") != "credentials" or creds.get("refresh"):
                logger.warning("visit transport: issue_credentials returned a non-credentials message")
                return
            # 等凭证（Servers 往返）期间期限可能刚过：不发，否则 iframe 会带同一个 vid 先入房再被关，
            # 打断对端的 peer_left 宽限。被顶掉（closed）的连接 _send_on 本身就不发
            if _close_if_late(conn, session) is not False:
                return
            await _send_on(conn, creds)
        elif stage == "sdk":
            if conn.sdk_seen or not conn.credentials_sent:
                return
            conn.sdk_seen = True
            await _call(session, "on_sdk_caps", _sdk_caps(msg))
        return
    if kind == "state":
        # 非字符串（list / dict）不能拿去查 frozenset，否则抛 TypeError 被当成断线；
        # 清洗后的值写回再交给 runtime，它那边拿去查集合也不会出错
        state = _enum(msg.get("state"), STATES)
        msg = {**msg, "state": state}
        was_in_room = conn.in_room
        # 先当作不在房内：hook 失败时（runtime 没认这条上报，状态可能不一致）不能留着旧的
        # in_room 让后续 stats 去重入；iframe 不再报的话，这场按 fail-safe 到页面期限判 local_page_lost
        conn.in_room = False
        if await _call(session, "on_state", msg) is _HOOK_FAILED:
            return
        # 只有明确的进出房状态才改它（error 等保持原样）；入房只认「本连接已拿到首发凭证」之后的上报，
        # 否则会绕过预检提前恢复 outbox
        if state in REJOINED_STATES:
            conn.in_room = conn.credentials_sent
        elif state not in OUT_OF_ROOM_STATES:
            conn.in_room = was_in_room
        await _try_rejoin(link, conn, session)
        return
    if kind == "recv":
        from_vid = msg.get("from_vid")
        cmd = msg.get("cmd")
        payload = msg.get("payload")
        if (
            not isinstance(from_vid, str) or len(from_vid) != _VID_LEN
            or not _is_int(cmd) or cmd not in (1, 2, 3)
            or not isinstance(payload, dict)
        ):
            logger.debug("visit transport: malformed recv dropped")
            return
        await _call(session, "on_recv", from_vid=from_vid, cmd=cmd, payload=payload, nbytes=nbytes)
        return
    if kind == "stats":
        await _call(session, "on_stats", msg)
        # 已在房内、重入却没做成（准备失败回滚了）：iframe 一次入房只报一次 joined，
        # 靠 5 s 一次的 stats 再试，不然只能等到页面期限
        await _try_rejoin(link, conn, session)
        return
    if kind == "tx_backpressure":
        await _call(session, "on_backpressure", msg)
        return
    if kind == "auth":
        return
    logger.debug("visit transport: unknown upstream type ignored")


async def _try_rejoin(link: _Link, conn: _Connection, session: VisitTransportSession) -> None:
    """Re-enter after a page reload once the new iframe is in the vendor room; all or nothing.

    Runs only for a replacement connection that is still current, got its
    first credentials and reported ``joined`` / ``connected``. Everything is
    prepared synchronously (snapshot that can actually be sent, ``hello`` +
    resume, the frames the outbox releases, the deadline cleared) before
    ``rejoined`` is set; any failure pauses the outbox again and leaves the
    deadline running, and the next trigger redoes the whole thing.
    """
    # 只有已被顶掉的旧连接不能替新连接恢复；没入房、已重入的连接不做
    if not (conn.reattach and conn.in_room and not conn.rejoined and _is_current(link, conn)):
        return
    # 期限已过（tick 还没来得及判）就不再重入：由 runtime 的 tick 判 local_page_lost。
    # 帧入口已查过一次；这里复查 on_state 等 await 期间刚过期的情形：新 iframe 已经用同一个 vid
    # 回到房里，不能留着（对端会取消 peer_left 宽限），关掉它即离房
    # 只取一次时钟：期限检查与随后的提交（on_page_rejoin_committed → on_page_back）用同一个 now，
    # 否则两次取时之间期限恰好过去，on_page_back 不清期限、连接却被标成已重入
    try:
        now = session.now()
    except Exception as exc:  # noqa: BLE001 - 普通的 stats 帧也会走到这里，不能因此结束接收循环
        logger.warning("visit transport: rejoin check failed: %s", type(exc).__name__)
        return
    if _close_if_late(conn, session, now) is not False:
        return  # 已关掉，或期限查不了：都不重入（下一次触发再试）
    saved = None
    try:
        # 快照最先，且按实际下发的那条（含 type、大小上限）检查：它失败时 outbox 仍保持
        # page_reload 暂停，不会出现「已恢复、重入却没完成」，也不会置位后才发现发不出去
        media, media_text = _media_frame(session.media_snapshot())
        saved = session.liveness.page_reload_state()
        session.on_page_rejoined(now)
        # 清期限在 due() 之前：due() 会取走一次性的 hello 重发、标记首发、扣令牌，是唯一撤不回的一步，
        # 放最后；它之前任何一步失败都能整套回滚（期限按原样写回）
        session.on_page_rejoin_committed(now)
        frames = list(session.outbox.due(now))
    except Exception as exc:  # noqa: BLE001
        logger.warning("visit transport: rejoin failed: %s", type(exc).__name__)
        # 回滚：恢复了的 outbox 重新暂停、清掉的期限写回，下一次触发整套重做（hello 去重、resume 幂等）
        # 两步各自兜底、先写回期限：暂停失败也不能留下「期限已清」的半提交状态
        if saved is not None:
            try:
                session.liveness.restore_page_reload_state(saved)
            except Exception as rollback_exc:  # noqa: BLE001
                logger.warning("visit transport: rejoin rollback failed: %s", type(rollback_exc).__name__)
        try:
            session.outbox.pause(now, reason=PAUSE_PAGE_RELOAD)
        except Exception as rollback_exc:  # noqa: BLE001
            logger.warning("visit transport: rejoin rollback failed: %s", type(rollback_exc).__name__)
        return
    conn.rejoined = True
    media_mark = conn.media_seq
    for frame in frames:
        await _send_on(conn, frame.to_ws())
    if _is_current(link, conn):
        # 发帧期间 runtime 可能已经发了更新的 media（例如刚关掉摄像头）：
        # 前面那份只用于提前发现失败，真正下发的取发送前一刻的最新状态，
        # 取到即同步入发送锁队列，不会再被更早的状态盖掉
        try:
            media, media_text = _media_frame(session.media_snapshot())
        except Exception as exc:  # noqa: BLE001
            logger.warning("visit transport: media_snapshot failed: %s", type(exc).__name__)
            # 取不到最新的：期间 runtime 发过 media 就不再用旧快照兜底（会盖掉更新的状态）
            if _newer_media(conn, media_mark):
                return
        await _send_on(conn, media, text=media_text)


async def _receive_text(websocket: WebSocket) -> Optional[str]:
    """Next text frame, or None on disconnect. Raises ``_FrameError`` on binary / oversize."""
    message = await websocket.receive()
    if message.get("type") == "websocket.disconnect":
        return None
    text = message.get("text")
    if text is None:
        raise _FrameError(CLOSE_UNSUPPORTED_DATA, "binary frames not allowed")
    if len(text) > FRAME_MAX_BYTES or len(text.encode("utf-8")) > FRAME_MAX_BYTES:
        raise _FrameError(CLOSE_TOO_LARGE, "frame too large")
    return text


class _FrameError(Exception):
    def __init__(self, code: int, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(reason)


def _parse_object(text: str) -> dict[str, Any]:
    try:
        msg = json.loads(text)
    except (ValueError, RecursionError):
        raise _FrameError(CLOSE_BAD_REQUEST, "malformed frame") from None
    if not isinstance(msg, dict):
        raise _FrameError(CLOSE_BAD_REQUEST, "malformed frame")
    return msg


@router.websocket("/transport/ws")
async def visit_transport_ws(websocket: WebSocket) -> None:
    """Transport iframe socket; see the module docstring for the protocol."""
    client_host = websocket.client.host if websocket.client else None
    if not local_peer_allowed(client_host, websocket.headers):
        logger.warning("visit transport: %s (non-local peer or proxy headers)", UNAUTHORIZED_CODE)
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return
    if not websocket_origin_allowed(websocket.headers.get("origin", ""), websocket.url.hostname):
        logger.warning("visit transport: %s (origin rejected)", UNAUTHORIZED_CODE)
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return

    await websocket.accept()
    try:
        raw_auth = await asyncio.wait_for(_receive_text(websocket), timeout=AUTH_TIMEOUT_S)
        auth = _parse_object(raw_auth) if raw_auth is not None else None
    except (asyncio.TimeoutError, _FrameError):
        auth = None
    if not valid_auth_frame(auth):
        await websocket.close(code=CLOSE_UNAUTHORIZED, reason="authentication failed")
        return

    visit_id = websocket.query_params.get("visit_id", "")
    side = websocket.query_params.get("side", "")
    if VISIT_ID_RE.fullmatch(visit_id or "") is None or side not in SIDES:
        await websocket.close(code=CLOSE_BAD_REQUEST, reason="bad query")
        return
    link = _links.get((visit_id, side))
    if link is None:
        await websocket.close(code=CLOSE_UNKNOWN_VISIT, reason="unknown visit")
        return

    conn = _attach(link, websocket)

    close_code = CLOSE_NORMAL
    close_reason = ""
    try:
        # 重载期限已过才连回的页面：不再接它（预检、领凭证、入房都不做）；与帧入口同一条 4404 路径
        if _close_if_late(conn, link.session):
            return
        while True:
            try:
                text = await _receive_text(websocket)
                if text is None:
                    break
                msg = _parse_object(text)
            except _FrameError as err:
                close_code, close_reason = err.code, err.reason
                break
            if conn.closed or conn.retired:
                break
            await _handle_frame(link, conn, msg, len(text.encode("utf-8")), visit_id, side)
    except Exception as exc:  # noqa: BLE001 - 断开 / 运行时异常都按断线收尾
        logger.debug("visit transport: receive loop ended: %s", type(exc).__name__)
    finally:
        # 先同步解绑并起宽限，再 await 关闭：handler 被取消（关停 / ASGI 层取消）时
        # await 会立刻抛 CancelledError，放在它后面的掉页处理就永远执行不到。
        # 被顶掉（4409）或 session 已注销的连接不算掉页：只有仍是当前连接时才起宽限
        if _links.get((visit_id, side)) is link and link.conn is conn:
            link.conn = None
            try:
                link.session.on_page_lost(link.session.now())
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit transport: on_page_lost failed: %s", type(exc).__name__)
        if conn.close_code is not None:
            # 已经排了关闭（4404 迟到页面 / 4409 被顶掉）：用那个码，不让默认的 1000 抢先
            close_code, close_reason = conn.close_code, conn.close_reason
        await conn.close(close_code, close_reason)
