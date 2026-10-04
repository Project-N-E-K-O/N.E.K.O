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
replacement connection calls ``liveness.on_page_back``; once its iframe is
back in the vendor room (first ``state`` joined / connected) the session
resends ``hello`` and resumes the outbox, then exactly one full ``media``
snapshot from ``session.media_snapshot()`` follows.
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
    VISIT_CAPTURE_FPS,
    VISIT_LIVEKIT_PUBLISH,
    VISIT_VIDEO_KBPS,
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

SIDES = ("host", "guest")
DOWNLINK_TYPES = frozenset({"credentials", "media", "send", "stop"})
PREFLIGHT_REASONS = frozenset({"insecure_context", "foreign_websocket", "no_webrtc"})
SDK_REASONS = frozenset({"sdk_unsupported", "sdk_load_failed", "no_encoder"})
REJOINED_STATES = frozenset({"joined", "connected"})
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
    msg: dict[str, Any] = {
        "type": "credentials",
        "visit_id": creds.visit_id,
        "side": side,
        "transport": creds.transport,
        "vendor": {creds.transport: dict(creds.vendor[creds.transport])},
        "own_vid": creds.vid,
        "peer_vid": creds.peer_vid if side == "guest" else peer_vid,
        "allowed_hosts": sorted(allowed_livekit_hosts()),
        "tier": "sd600",
        "crop": crop,
        "publish": {
            "codec": codec,
            "bitrate_kbps": VISIT_VIDEO_KBPS,
            "fps": VISIT_CAPTURE_FPS,
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
        """``caps{stage:'preflight'}`` (already written into the route state).

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
        """The current transport socket dropped: 20 s grace starts, delivery timers pause."""
        self.liveness.on_page_lost(now)
        self.outbox.pause(now, reason=PAUSE_PAGE_RELOAD)

    def on_page_attached(self, now: float) -> None:
        """A replacement socket authenticated within the grace.

        The outbox stays paused until this socket's iframe is back in the
        vendor room (it may replace a live socket, which never paused it).
        """
        self.liveness.on_page_back(now)
        self.outbox.pause(now, reason=PAUSE_PAGE_RELOAD)

    def on_page_rejoined(self, now: float) -> None:
        """The replacement iframe is back in the vendor room: queue ``hello`` first, resume.

        Synchronous on purpose: the transport calls it only while the socket
        is still the current one and sends what the outbox releases right
        after it on that same socket, so a socket replaced meanwhile can never
        resume the outbox or flush into its successor.
        """
        self.outbox.resend_hello(now)
        self.outbox.resume(now, reason=PAUSE_PAGE_RELOAD)

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
    sdk_seen: bool = False
    rejoined: bool = False

    async def send_json(self, msg: Mapping[str, Any]) -> bool:
        text = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
        size = len(text.encode("utf-8"))
        limit = CREDENTIALS_MAX_BYTES if msg.get("type") == "credentials" else FRAME_MAX_BYTES
        if size > limit:
            logger.warning("visit transport: dropping oversize %s downlink (%d B)", msg.get("type"), size)
            return False
        async with self.send_lock:
            # 排队等锁期间可能已被顶掉或关闭：拿到锁后两样都要复查
            if self.closed or self.retired:
                return False
            try:
                await self.websocket.send_text(text)
            except Exception as exc:  # noqa: BLE001 - 断开中的 socket：当作未送达
                logger.debug("visit transport: send failed: %s", type(exc).__name__)
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
        _spawn_close(old.conn, CLOSE_NORMAL, "visit replaced")


def unregister_transport_session(session: VisitTransportSession) -> None:
    """Forget the session; its socket (if any) is closed 1000 (send ``stop`` first)."""
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
    return link is not None and link.conn is not None and not link.conn.closed


_close_tasks: set[asyncio.Task] = set()


def _spawn_close(conn: _Connection, code: int, reason: str) -> None:
    task = asyncio.ensure_future(conn.close(code, reason))
    _close_tasks.add(task)
    task.add_done_callback(_close_tasks.discard)


async def send_downlink(visit_id: str, side: str, msg: Mapping[str, Any]) -> bool:
    """Send one downlink message to the current socket of ``(visit_id, side)``.

    Only ``credentials / media / send / stop``. A first-issue ``credentials``
    (no ``refresh``) is accepted once per connection, and only after the
    preflight; ``stop`` once per connection. Returns False when nothing was
    sent (no socket, rule violation, oversize, socket closing).
    """
    if msg.get("type") not in DOWNLINK_TYPES:
        raise ValueError("unknown downlink type")
    link = _links.get((visit_id, side))
    conn = link.conn if link is not None else None
    if conn is None:
        return False
    return await _send_on(conn, msg)


async def _send_on(conn: _Connection, msg: Mapping[str, Any]) -> bool:
    """Send on one specific connection, enforcing the per-connection downlink rules.

    The once-only slots (first ``credentials``, ``stop``) are reserved while
    the send is in flight and consumed only when it succeeded, so a failed
    send does not burn them and two concurrent senders cannot both pass.
    """
    if conn.closed or conn.retired:
        return False
    kind = msg.get("type")
    first_credentials = kind == "credentials" and not msg.get("refresh")
    if first_credentials:
        if conn.credentials_sent or conn.credentials_reserved or not conn.preflight_seen:
            logger.warning("visit transport: refusing a second first-issue credentials")
            return False
        conn.credentials_reserved = True
    elif kind == "credentials" and not conn.credentials_sent:
        # 续期只替换已有凭证：首发之前没有可替换的
        return False
    if kind == "stop":
        if conn.stop_sent or conn.stop_reserved:
            return False
        conn.stop_reserved = True
    try:
        ok = await conn.send_json(msg)
    finally:
        if first_credentials:
            conn.credentials_reserved = False
        if kind == "stop":
            conn.stop_reserved = False
    if ok and first_credentials:
        conn.credentials_sent = True
    if ok and kind == "stop":
        conn.stop_sent = True
    return ok


def _reset_for_tests() -> None:
    """Forget every registration (unit tests only)."""
    _links.clear()


# ── 上行处理 ───────────────────────────────────────────────────────────


def _is_int(value: Any) -> bool:
    return type(value) is int


def _record_preflight(session: VisitTransportSession, msg: dict[str, Any], ok: bool) -> dict[str, Any]:
    reason = msg.get("reason")
    caps = {
        "stage": "preflight",
        "preflight_ok": ok,
        "reason": reason if reason in PREFLIGHT_REASONS else None,
        "is_secure_context": bool(msg.get("is_secure_context")),
        "ua": str(msg.get("ua") or "")[:_UA_MAX_CHARS],
        "at": time.time(),
    }
    # 能力门缓存（PR-09a 读）：只写进本角色仍在的串门槽，不建新槽
    slot = get_visit_route_state(session.lanlan_name)
    if slot is not None:
        slot["caps_preflight"] = dict(caps)
    return caps


def _sdk_caps(msg: dict[str, Any]) -> dict[str, Any]:
    reason = msg.get("reason")
    codecs = msg.get("codecs")
    codec_list = [c[:64] for c in codecs if isinstance(c, str)][:_CODECS_MAX_ITEMS] if isinstance(codecs, list) else []
    return {
        "stage": "sdk",
        "transport_ok": msg.get("transport_ok") is True,
        "video_ok": msg.get("video_ok") is True,
        "reason": reason if reason in SDK_REASONS else None,
        "codecs": codec_list,
    }


_HOOK_FAILED = object()


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
    conn = _Connection(websocket=websocket, reattach=link.connections_seen > 0)
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


async def _handle_frame(
    link: _Link, conn: _Connection, msg: dict[str, Any], nbytes: int, visit_id: str, side: str,
) -> None:
    # 被顶掉（4409）或场次已注销的连接，后续帧一概不交给 runtime
    if not _is_current(link, conn):
        return
    session = link.session
    kind = msg.get("type")
    if kind == "caps":
        stage = msg.get("stage")
        if stage == "preflight":
            if conn.preflight_seen:
                return
            conn.preflight_seen = True
            ok = msg.get("preflight_ok") is True
            caps = _record_preflight(session, msg, ok)
            # runtime 没能处理预检（例如更新串门状态失败）就不去 Servers 领凭证
            if await _call(session, "on_preflight", caps) is _HOOK_FAILED or not ok:
                return
            # 等 on_preflight 期间可能已被顶掉：领凭证有 Servers 侧副作用（签发记录、配额），不为它白领一份
            if not _is_current(link, conn):
                return
            creds = await _call(session, "issue_credentials")
            if creds is None or creds is _HOOK_FAILED:
                return
            if not isinstance(creds, Mapping) or creds.get("type") != "credentials" or creds.get("refresh"):
                logger.warning("visit transport: issue_credentials returned a non-credentials message")
                return
            # 等凭证期间本连接可能已被顶掉：只发给本连接，被顶掉（closed）就不发
            await _send_on(conn, creds)
        elif stage == "sdk":
            if conn.sdk_seen or not conn.credentials_sent:
                return
            conn.sdk_seen = True
            await _call(session, "on_sdk_caps", _sdk_caps(msg))
        return
    if kind == "state":
        await _call(session, "on_state", msg)
        # 重入只认「本连接已拿到首发凭证、且仍是当前连接」之后的入房上报：
        # 否则会绕过预检提前恢复 outbox，或让已被顶掉的旧连接替新连接恢复
        if (
            conn.reattach and not conn.rejoined and conn.credentials_sent
            and msg.get("state") in REJOINED_STATES and _is_current(link, conn)
        ):
            now = session.now()
            try:
                session.on_page_rejoined(now)
                frames = list(session.outbox.due(now))
            except Exception as exc:  # noqa: BLE001 - 不置 rejoined：下一条 joined / connected 再试
                logger.warning("visit transport: rejoin failed: %s", type(exc).__name__)
                return
            conn.rejoined = True
            for frame in frames:
                await _send_on(conn, frame.to_ws())
            if not _is_current(link, conn):
                return
            snapshot = None
            try:
                snapshot = session.media_snapshot()
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit transport: media_snapshot failed: %s", type(exc).__name__)
            if isinstance(snapshot, Mapping):
                await _send_on(conn, {**snapshot, "type": "media"})
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
        return
    if kind == "tx_backpressure":
        await _call(session, "on_backpressure", msg)
        return
    if kind == "auth":
        return
    logger.debug("visit transport: unknown upstream type ignored")


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
        await conn.close(close_code, close_reason)
