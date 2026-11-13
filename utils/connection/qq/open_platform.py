"""QQ Open Platform connector -- official QQ Bot API."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from typing import Any, Optional

import re as _re
import httpx
import websockets

from ..base import ConnectionBase
from .open_platform_media import QQOpenPlatformMediaMixin

_CQ_CODE_RE = _re.compile(r"\[CQ:(\w+),([^\]]+)\]")

#: Dispatch families, per the v2 event list. The receive loop used to recognise only
#: ``GROUP_AT_MESSAGE_CREATE`` / ``C2C_MESSAGE_CREATE`` and dropped everything else, so
#: full-group-message mode, robot add/remove and friend events never reached a consumer.
MESSAGE_EVENTS = frozenset({
    "GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE", "C2C_MESSAGE_CREATE",
})
NOTICE_EVENTS = frozenset({
    "GROUP_ADD_ROBOT", "GROUP_DEL_ROBOT", "GROUP_JOIN_REQUEST",
    "GROUP_MEMBER_ADD", "GROUP_MEMBER_REMOVE",
    "GROUP_MSG_RECEIVE", "GROUP_MSG_REJECT", "C2C_MSG_RECEIVE", "C2C_MSG_REJECT",
    "FRIEND_ADD", "FRIEND_DEL", "SUBSCRIBE_MESSAGE_STATUS", "INTERACTION_CREATE",
})

#: Rich-media ``file_type`` values of the upload interfaces.
FILE_TYPE_IMAGE = 1
FILE_TYPE_VIDEO = 2
FILE_TYPE_VOICE = 3
FILE_TYPE_FILE = 4

#: Passive-reply and mute relevant ``err_code``s ("judge by err_code, not by message").
ERR_TOKEN_INVALID = 40011027
ERR_GROUP_MUTED = 850018

#: ``file_data`` (inline binary upload) size ceiling; above it URL/chunked upload is
#: required. shortcut: inline upload only, >5 MB falls back to the URL path.
INLINE_UPLOAD_MAX_BYTES = 5 * 1024 * 1024


class QQOpenPlatformAPIError(RuntimeError):
    """A v2 REST call came back failed. Carries the platform ``err_code``/``trace_id``.

    The send paths used to swallow every failure into ``None``, so a caller could not
    tell "quota exhausted" from "wrong id namespace" from "network down" -- and the
    message text is not stable, only ``err_code`` is.
    """

    def __init__(self, *, method: str, path: str, http_status: int = 0, err_code: int = 0,
                 message: str = "", trace_id: str = ""):
        self.method = method
        self.path = path
        self.http_status = int(http_status or 0)
        self.err_code = int(err_code or 0)
        self.api_message = str(message or "")
        self.trace_id = str(trace_id or "")
        super().__init__(
            f"{method} {path} failed: http={self.http_status} err_code={self.err_code} "
            f"{self.api_message}" + (f" trace={self.trace_id}" if self.trace_id else "")
        )


class QQOpenPlatformUnsupportedError(RuntimeError):
    """The official Bot API does not offer this operation at all.

    Not "unimplemented here". These used to be ``return {}`` stubs: an empty dict is
    truthy enough to keep the caller going but tells it the request succeeded, which is
    worse than an error -- e.g. mute detection reads ``shut_up_timestamp`` out of
    ``get_group_member_info`` and never fired, so a muted bot kept sending.
    """

    def __init__(self, operation: str, reason: str):
        self.operation = operation
        self.reason = reason
        super().__init__(f"QQ Open Platform does not offer {operation}: {reason}")


#: Mention shapes inside message content: v2 uses ``<@member_openid>``, the legacy
#: shape (still produced by some payloads and by the OneBot side) is ``<@!uin>``.
_AT_OPENID_RE = _re.compile(r"<@([0-9A-Za-z_-]{8,})>")
_AT_LEGACY_RE = _re.compile(r"<@!(\d+)>")

#: Placeholder text for an image that is not sent as media.
_IMAGE_PLACEHOLDER = "[图片]"

#: How long a replied-to id keeps its ``msg_seq`` counter after its last use: twice the
#: platform's longest passive-reply window (60 minutes, private chat). Dropping a counter
#: any earlier could restart it at 1 while the platform still remembers ``(msg_id, 1)``
#: and rejects the reply as a duplicate; past the window no reply to that id is accepted
#: at all, so restarting is harmless.
_MSG_SEQ_TTL_SECONDS = 2 * 60 * 60

# ==========================================
# R11 identity scope: resolved
# (docs/design/speaker-trust-entity-semantics.md §2.15.4)
# ==========================================
#
# Conclusion: in Open Platform group/private events there is **no ``id`` key under
# ``author``**. This rests on two official first-party Tencent sources that
# corroborate each other:
#
# - The official docs bot-docs `server-inter/message/send-receive/event.md` field
#   table and example JSON: `C2C_MESSAGE_CREATE`'s author has only `user_openid`;
#   `GROUP_AT_MESSAGE_CREATE`'s author has only `member_openid`, and the group
#   identifier is `group_openid`;
# - The official SDK botpy `message.py`: `C2CMessage._User` reads only
#   `user_openid`, `GroupMessage._User` reads only `member_openid`; only the
#   **channel**-system `Message._User` has `id` -- and this connector does not
#   handle channel message events.
#
# Yet `author.get("id")` was what this file used to read (copied from
# napcat/OneBot's `sender.user_id`; even the `<@!(\d+)>` pure-digit regex was a
# product of the same copy). As a result both paths' user_id was **always empty**:
# every speaker collapsed into one empty identity, `_maybe_reserve_open_platform_admin`
# never fired (because `not sender_id`), and private replies POSTed to
# `/v2/users//messages`.
#
# The official "unique identity mechanism" also answers the scope question: *the
# same bot sees a different per-group unique id (member_openid) for the same user*.
# So R11 is confirmed and `actor_scope = per_conversation`, taking the degraded
# path per §2.15.4.3 (registration in the qq_auto_reply plugin's
# `settings_service.declare_identity_scope`; the manual-assertion UI is on the
# trusted-user page).
#
# The forensics constants / functions below are **kept**: the official doc field
# table does not guarantee an exhaustive enumeration of the real payload (e.g. an
# undocumented union_openid sibling key), so keeping them lets us confirm on a live
# bot. Default off; never participates in any decision.
_IDENTITY_PROBE_TAG = "[R11]"
_IDENTITY_PROBE_EVENTS = ("GROUP_AT_MESSAGE_CREATE", "C2C_MESSAGE_CREATE")
#: Max lines recorded per connection. Forensics only needs three (group X, group Y,
#: private each); the cap exists so the log doesn't grow unbounded if the switch is
#: left on, not to limit forensics.
_IDENTITY_PROBE_MAX_LINES = 200
#: Char cap per field value, defending against an abnormally long payload blowing
#: up the log.
_IDENTITY_PROBE_VALUE_MAX_CHARS = 128


def _is_identifier_key(name: str) -> bool:
    """Judge whether a field name looks like an identifier field, **by shape**.

    Deliberately not an enum ``{"id", "member_openid", ...}``: one of the questions
    forensics must answer is exactly "is there another openid sibling key under
    author?", and an enum would keep the value of that unanticipated key out of the
    log, defeating the forensics.
    """
    lowered = str(name).lower()
    return lowered == "id" or lowered.endswith("_id") or "openid" in lowered


def _probe_identifier_values(mapping: Any) -> str:
    """Pick out the **values** of identifier fields; read nothing else."""
    if not isinstance(mapping, dict):
        return "{}"
    picked: dict[str, str] = {}
    for raw_key, raw_value in mapping.items():
        key = str(raw_key)
        if not _is_identifier_key(key):
            continue
        value = str(raw_value)
        if len(value) > _IDENTITY_PROBE_VALUE_MAX_CHARS:
            value = value[:_IDENTITY_PROBE_VALUE_MAX_CHARS] + "…"
        picked[key] = value
    return json.dumps(picked, ensure_ascii=False, sort_keys=True)


def _probe_key_names(mapping: Any) -> str:
    """Take only field **names**. Names contain no user content, so the whole set can be logged."""
    if not isinstance(mapping, dict):
        return "[]"
    return json.dumps(sorted(str(k) for k in mapping), ensure_ascii=False)


def build_identity_probe_line(event_type: str, data: Any) -> str:
    """Build one forensics log line. Pure function, no side effects.

    Emits four items, mapping exactly to the four criteria in the §2.15.4.2 table:

    - ``author.ids``  -- ① author.id itself; ② values of sibling keys like
      member_openid / user_openid / union_openid (which one is equal across groups
      is decided by comparing here);
    - ``author.keys`` -- ② the **full** key names of those siblings (incl. the
      unanticipated ones);
    - ``group.ids``   -- ③ which key holds the group id and what its value is;
    - ``data.keys``   -- ③ fallback: in case the group-id key name doesn't even
      contain "group".

    **Logs identifier-field values only.** Body, attachment URLs, @ lists never
    enter the log -- this line lands on a persistent file
    (my docs/N.E.K.O/logs/, survives restart, which forensics needs it to), and
    the instrumentation can be switched off after forensics ends, but rows already
    written won't roll back.
    """
    payload = data if isinstance(data, dict) else {}
    author = payload.get("author")
    group_fields = {
        str(k): v for k, v in payload.items() if "group" in str(k).lower()
    }
    return (
        f"{_IDENTITY_PROBE_TAG} event={event_type} "
        f"author.ids={_probe_identifier_values(author)} "
        f"author.keys={_probe_key_names(author)} "
        f"group.ids={_probe_identifier_values(group_fields)} "
        f"data.keys={_probe_key_names(payload)}"
    )


#: Order in which speaker ids are read, first non-empty wins. The two paths
#: **must be kept separate**: the same real person is user_openid in private and a
#: distinct member_openid in every group; no single key has a value on both sides
#: (see module top).
#:
#: `id` stays last as a fallback, not because we doubt the official docs, but
#: because removing it would silently return "" when the protocol adds a key --
#: and exactly that (an empty speaker id) is the shape of this defect: permission,
#: memory, and send paths all silently do the wrong thing. A possibly-wrong id is
#: better than an empty one.
_C2C_ACTOR_ID_KEYS = ("user_openid", "id")
_GROUP_ACTOR_ID_KEYS = ("member_openid", "id")


def pick_actor_id(author: Any, keys: tuple[str, ...]) -> str:
    """Take the first non-empty speaker identifier per ``keys`` order. Pure function."""
    if not isinstance(author, dict):
        return ""
    for key in keys:
        value = str(author.get(key) or "").strip()
        if value:
            return value
    return ""


class QQOpenPlatformConnection(QQOpenPlatformMediaMixin, ConnectionBase):
    """Official QQ Open Platform Bot API connection.

    WebSocket events -> internal unified message format -> upper-layer pipeline;
    HTTP API -> send messages.

    Rich media (upload + ``msg_type=7`` image sends) comes from
    ``QQOpenPlatformMediaMixin`` (see ``open_platform_media``). The media mixin comes
    first in the bases by the same convention as ``OneBotClient(NapCatActionsMixin,
    ConnectionBase)``, not to override anything: it defines no name that
    ``ConnectionBase`` has, and ``send_group_image`` is implemented on this class
    itself (it builds an ``image`` segment and goes through
    ``send_group_message_segments``, whose upload is the mixin's ``upload_image``).
    """

    #: Observed transport (see OneBotClient.CHANNEL). Never a key.
    #:
    #: This assignment has to sit **after** the class docstring: placed before it, the
    #: string is just a discarded expression statement and `__doc__` is None.
    CHANNEL: str = "open"

    #: Defaults for the v2 reception state. Instances are sometimes built through
    #: ``__new__`` (test harnesses, partial reconstruction), and a missing attribute
    #: there raises inside event handling -- which the receive loop reports as a
    #: disconnect, so it would look like an unstable connection rather than a bug.
    _all_messages_observed = False
    _recv_msg_setting = ""
    _bot_member_role = ""
    _bot_member_openid = ""

    #: Production and sandbox are **two different domains**. An unpublished bot only
    #: exists in the sandbox: connecting to the production gateway completes the
    #: handshake and returns READY, yet the platform keeps reporting it offline and
    #: no event ever arrives -- so sandbox support is not an optional nicety, it is
    #: a prerequisite for using the platform before the bot is published.
    #:
    #: Note that ``_TOKEN_URL`` does **not** switch with it: the access-token
    #: endpoint on ``bots.qq.com`` is shared by both environments; only the
    #: API/gateway domain is environment-specific.
    _API_BASE_PRODUCTION = "https://api.sgroup.qq.com"
    _API_BASE_SANDBOX = "https://sandbox.api.sgroup.qq.com"

    @property
    def _API_BASE(self) -> str:
        """Base URL for REST calls and the gateway, pinned for the connection's lifetime.

        The environment is a property of a **connection**, not of a request: REST sends
        and gateway events must agree on where they are talking to. So the toggle is
        sampled once in :meth:`connect` and held until :meth:`disconnect`, and a settings
        change takes effect on the next explicit reconnect -- the same rule the plugin
        already applies to the other connection settings (its save response reports
        ``reconnect_required``).

        Resolving per call would be the worse bug: ``_API_BASE`` feeds both the REST
        paths and (via ``_get_gateway_url``) the gateway URL, so flipping it mid-flight
        makes sends go to one environment while the live WebSocket -- and every
        ``_try_reconnect`` from it -- keeps talking to the other.
        """
        pinned = getattr(self, "_pinned_api_base", None)
        return pinned or self._resolve_api_base()

    def _resolve_api_base(self) -> str:
        """Current environment per the toggle (unsampled; see :attr:`_API_BASE`)."""
        sandbox = self._sandbox
        if callable(sandbox):
            try:
                sandbox = sandbox()
            except Exception:
                # Read failure falls back to production: better to connect to the
                # wrong environment and report it than to silently switch to sandbox
                # when the user never asked for it.
                sandbox = False
        return self._API_BASE_SANDBOX if sandbox else self._API_BASE_PRODUCTION
    _TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"

    def __init__(
        self,
        *,
        app_id: str,
        client_secret: str,
        logger: Any = None,
        message_queue_size: int = 100,
        identity_probe: Any = None,
        emit_log: Any = None,
        sandbox: Any = None,
    ):
        #: Sandbox toggle: a bool or a zero-arg callable (same convention as
        #: ``identity_probe``). Passed as a callable, a settings change takes effect
        #: immediately -- switching environments needs no restart.
        self._sandbox = sandbox
        #: Zero-arg callback; only records R11 forensics when it returns truthy
        #: (see module top). A callback (not a bool) so the toggle takes effect
        #: immediately without reconnecting.
        self._identity_probe = identity_probe
        #: The host plugin's in-memory log ring (what the UI "runtime logs" page reads).
        #: No-op by default, same convention as OneBotClient.
        self._emit_log = emit_log or (lambda level, msg: None)
        self._identity_probe_emitted = 0
        #: Connection-mode marker, so runtime knows whether it must rebuild the
        #: connection (= "open_platform").
        self.mode = "open_platform"
        self._app_id = str(app_id or "").strip()
        self._client_secret = str(client_secret or "").strip()
        self.token = ""
        self.logger = logger
        self.ws = None
        self._ws = None
        self._http = None
        self._access_token = ""
        self._token_expires_at: float = 0
        self._heartbeat_task = None
        self._receive_task = None
        self._heartbeat_interval: float = 30.0
        self._closing = False
        self._self_id = ""
        self._self_nickname = ""
        self._last_seq = 0
        self._session_id = ""  # needed for Resume reconnect
        self._sent_message_ids: dict[str, float] = {}
        self._message_queue: asyncio.Queue = asyncio.Queue(maxsize=max(1, message_queue_size))
        #: Set by the media mixin once the legacy direct upload is known dead; cleared
        #: on connect / successful reconnect (see ``open_platform_media``).
        self._legacy_upload_unsupported = False
        #: Replied-to message id -> (last ``msg_seq`` used, monotonic time of that use),
        #: least recently used first (see ``_next_msg_seq``).
        self._msg_seq_by_reply_id: dict[str, tuple[int, float]] = {}
        #: Proof that the platform is pushing every group message (see the property).
        self._all_messages_observed = False
        self._recv_msg_setting = ""
        self._bot_member_openid = ""
        self._bot_member_role = ""
        #: Groups this connection actually saw a message from; the platform offers no
        #: "my groups" listing, so observed ids are the only honest answer.
        self._seen_groups: dict[str, float] = {}
        #: Notice-event sinks (message events keep going to ``_message_queue``).
        self._event_sinks: list = []
        #: group_id -> muted-until, same shape as ``OneBotClient._group_muted``.
        self._group_muted: dict[str, float] = {}

    @property
    def needs_attention(self) -> bool:
        """Whether this channel delivers traffic that is not addressed to the bot.

        It used to be a flat ``False`` ("only @bot"), which is true only while the
        group is in mention-only mode. Once the platform pushes
        ``GROUP_MESSAGE_CREATE`` the consumer does have to decide who a message was
        for, so the flag follows :attr:`receives_all_messages` instead of a constant.
        """
        return self.receives_all_messages

    @property
    def supports_voice(self) -> bool:
        return True  # rich media accepts voice: file_type=3 (silk / mp3 / wav)

    @property
    def supports_poke(self) -> bool:
        return False  # no poke interface for groups or private chats in v2

    @property
    def receives_all_messages(self) -> bool:
        """True once the platform is actually pushing every group message.

        Two independent proofs, either is enough: ``bot_state.recv_msg_setting``
        says ``all`` (that interface is permission-gated, so it is often unavailable),
        or a ``GROUP_MESSAGE_CREATE`` really arrived. Guessing from settings alone
        would claim a capability the platform may not have enabled for the group.
        """
        if self._recv_msg_setting == "all":
            return True
        return self._all_messages_observed

    @property
    def supports_ark_cards(self) -> bool:
        return False  # v2 has no ark on the send side any more (receive side still does)

    @property
    def supports_card_messages(self) -> bool:
        """Image-text card (``msg_type=8``): what ``send_group_ark_card`` now builds."""
        return True

    @property
    def supports_reference_messages(self) -> bool:
        """Visible quoting via ``message_reference`` (needs the REFIDX token)."""
        return True

    @property
    def supports_recall(self) -> bool:
        """Recall of own messages, and of others' messages when the bot is a group admin."""
        return True

    @property
    def recv_msg_setting(self) -> str:
        """``all`` / ``only_mention`` / ``mention_and_context`` / ``""`` (not probed)."""
        return self._recv_msg_setting

    @property
    def bot_member_role(self) -> str:
        """``member`` / ``admin`` / ``owner`` as reported by ``bot_state``, or ``""``."""
        return self._bot_member_role

    async def get_login_info(self) -> dict[str, Any]:
        return {"user_id": self._self_id, "nickname": self._self_nickname}

    # ==========================================
    # connection lifecycle
    # ==========================================

    async def connect(self) -> None:
        if not self._app_id or not self._client_secret:
            raise RuntimeError("QQ 开放平台: app_id 和 client_secret 未配置")
        self._closing = False
        # A new connection re-probes the legacy upload protocol (open_platform_media).
        self._legacy_upload_unsupported = False
        # Sample the environment once, for this connection's lifetime. REST and the
        # gateway must agree on where they are talking to, so a settings change only
        # takes effect on the next explicit connect() -- see _API_BASE.
        self._pinned_api_base = self._resolve_api_base()
        if self.logger:
            env = "沙箱" if self._pinned_api_base == self._API_BASE_SANDBOX else "正式"
            self.logger.info(f"[QQOpenPlatform] 环境: {env} ({self._pinned_api_base})")
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        await self._refresh_token()
        if self.logger:
            self.logger.info(f"[QQOpenPlatform] token 已获取")
        ws_url = await self._get_gateway_url()
        if self.logger:
            self.logger.info(f"[QQOpenPlatform] 连接网关: {ws_url[:60]}...")
        self._ws = await websockets.connect(ws_url, max_size=2 ** 23)
        self.ws = self._ws
        if self.logger:
            self.logger.info("[QQOpenPlatform] WebSocket 已连接")
        await self._handshake(is_reconnect=False)
        self._receive_task = asyncio.create_task(self._receive_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _handshake(self, *, is_reconnect: bool) -> None:
        """WebSocket handshake: Hello -> [Resume] -> Identify -> READY."""
        # Hello
        raw = await self._ws.recv()
        hello = json.loads(raw)
        if hello.get("op") == 10:
            self._heartbeat_interval = max(10.0, float(hello["d"]["heartbeat_interval"]) / 1000.0 - 2.0)
            if self.logger:
                self.logger.info(f"[QQOpenPlatform] Hello 收到, 心跳间隔: {self._heartbeat_interval:.0f}s")
        # On reconnect prefer Resume; fall back to Identify on failure.
        if is_reconnect and self._session_id:
            await self._ws.send(json.dumps({
                "op": 6, "d": {"token": f"QQBot {self._access_token}",
                                "session_id": self._session_id,
                                "seq": self._last_seq},
            }))
            try:
                resp = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
                event = json.loads(resp)
                if event.get("op") == 0 and event.get("t") == "RESUMED":
                    if self.logger:
                        self.logger.info("[QQOpenPlatform] Resume 成功，事件已补发")
                    return
                if self.logger:
                    self.logger.warning(f"[QQOpenPlatform] Resume 失败(op={event.get('op')} t={event.get('t')})，回退 Identify")
            except asyncio.TimeoutError:
                if self.logger:
                    self.logger.warning("[QQOpenPlatform] Resume 超时，回退 Identify")
        # Identify
        await self._ws.send(json.dumps({
            "op": 2, "d": {
                "token": f"QQBot {self._access_token}",
                "intents": (1 << 25) | (1 << 12),
                "shard": [0, 1],
            },
        }))
        resp = await self._ws.recv()
        ready = json.loads(resp)
        if ready.get("op") == 0 and ready.get("t") == "READY":
            user = ready["d"].get("user") or {}
            self._self_id = str(user.get("id") or "")
            self._self_nickname = str(user.get("username") or "")
            self._session_id = str(ready["d"].get("session_id") or "")
            if self.logger:
                self.logger.info(f"[QQOpenPlatform] 已就绪: {self._self_nickname} ({self._self_id})")
        else:
            raise RuntimeError(f"鉴权失败: op={ready.get('op')} t={ready.get('t')}")

    async def disconnect(self) -> None:
        self._closing = True
        # Unpin: the next connect() samples the toggle again, so switching environments
        # is "stop, then start" -- deterministic, never a mid-flight split.
        self._pinned_api_base = None
        self._cancel_inbound_sink_tasks()
        for task in [self._heartbeat_task, self._receive_task]:
            if task and not task.done():
                task.cancel()
        if self._ws:
            await self._ws.close()
            self._ws = None
            self.ws = None
        if self._http:
            await self._http.aclose()
            self._http = None

    def is_connected(self) -> bool:
        return self._ws is not None

    # ==========================================
    # message receive
    # ==========================================

    def _identity_probe_enabled(self) -> bool:
        probe = getattr(self, "_identity_probe", None)
        return bool(probe()) if callable(probe) else False

    def _write_identity_probe(self, text: str) -> None:
        """One forensics line must go to both pools; either alone is not enough.

        - ``self.logger``: file log (my docs/N.E.K.O/logs/), **survives restart**; this
          is the copy you can send a whole to the developer;
        - ``self._emit_log``: the plugin's 500-entry memory ring, i.e. what the UI
          "runtime logs" page reads. Without it, after the user flips the switch
          they see nothing on the log page -- while the adjacent "trusted users"
          page's existing copy just taught them "ID... can be viewed in the log".
          (``get_recent_logs`` only falls back to the file when the ring is empty,
          and the ring is never empty from startup, so writing only the file means
          being completely invisible on the UI.)
        """
        # Single-arg call: the line carries a raw platform id that may contain %,
        # which would blow up logging's %-formatting.
        self.logger.info(text)
        self._emit_log("INFO", text)

    def _log_identity_probe(self, event_type: str, data: Any) -> None:
        """Record one R11 forensics log line (see module top).

        **Never leaks an exception**: ``_receive_loop``'s catch-all ``except Exception``
        treats any exception as a disconnect and reconnects; a forensics line does not
        deserve to trigger a reconnect.
        """
        try:
            if event_type not in _IDENTITY_PROBE_EVENTS:
                return
            if not self.logger or not self._identity_probe_enabled():
                return
            emitted = getattr(self, "_identity_probe_emitted", 0)
            if emitted > _IDENTITY_PROBE_MAX_LINES:
                return
            self._identity_probe_emitted = emitted + 1
            if emitted == _IDENTITY_PROBE_MAX_LINES:
                # The counter lives on this connection object, and qq_auto_reply only
                # sets it to None (rebuilding) when the **connection mode** changes
                # (its runtime_ops_service) -- the sidebar "stop -> start" does
                # NOT rebuild it. So the only correct instruction here is "restart the
                # app"; saying "restart auto-reply" would be a lie.
                self._write_identity_probe(
                    f"{_IDENTITY_PROBE_TAG} 已记录 {_IDENTITY_PROBE_MAX_LINES} "
                    "条，达到上限，后续不再记录；重启应用后重新计数。"
                )
                return
            self._write_identity_probe(build_identity_probe_line(event_type, data))
        except Exception:
            # Deliberately swallowed: re-raising would hit _receive_loop's catch-all
            # and be treated as a disconnect, so a failed diagnostic line could drop
            # the bot offline once.
            pass

    async def _receive_loop(self) -> None:
        while not self._closing:
            if not self._ws:
                await asyncio.sleep(1)
                continue
            try:
                raw = await self._ws.recv()
                payload = json.loads(raw)
                op = payload.get("op")
                if op == 0:  # Dispatch
                    self._last_seq = payload.get("s", self._last_seq)
                    event_type = payload.get("t", "")
                    # The R11 forensics instrumentation must land here, not inside
                    # _convert_event: (a) to sidestep uncertainty about the group-id
                    # key name (_convert_event reads only group_id, which sees nothing
                    # if the platform ships group_openid), and (b) to run before the
                    # trusted-group whitelist gate so an unconfigured group can still
                    # be sampled.
                    self._log_identity_probe(event_type, payload.get("d"))
                    await self._dispatch_event(
                        event_type, payload.get("d"), str(payload.get("id") or "")
                    )
                    continue  # handled; skip reconnect
                elif op == 1:  # Heartbeat
                    await self._ws.send(json.dumps({"op": 11, "d": self._last_seq}))
                    continue  # handled; skip reconnect
                elif op == 11:  # Heartbeat ACK -> ignore
                    continue
                elif op == 7:  # Reconnect -> close current ws; rebuilt below in _try_reconnect()
                    if self.logger:
                        self.logger.warning("[QQOpenPlatform] 服务端要求重连")
                    if self._ws:
                        try: await self._ws.close()
                        except Exception: pass
                    self._ws = None
                    self.ws = None
                # op==7 and other unknown ops -> no continue; fall through to reconnect
            except websockets.ConnectionClosed:
                if self.logger:
                    self.logger.warning("[QQOpenPlatform] WebSocket 断开")
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"[QQOpenPlatform] 接收异常: {e}")
            # Disconnected -> reconnect
            if not self._closing:
                await self._try_reconnect()

    async def _dispatch_event(self, event_type: str, data: Any, envelope_id: str) -> None:
        """Send one dispatch frame where it belongs: queue for messages, sinks for notices.

        Dropping the other families here is what made full-group-message mode look
        broken: the platform pushed ``GROUP_MESSAGE_CREATE``, the loop ignored it, and
        nothing downstream could tell "not enabled" from "connector threw it away".
        """
        if not isinstance(data, dict):
            return
        if event_type in MESSAGE_EVENTS:
            if event_type == "GROUP_MESSAGE_CREATE" and not self._all_messages_observed:
                self._all_messages_observed = True
                if self.logger:
                    self.logger.info(
                        "[QQOpenPlatform] 收到 GROUP_MESSAGE_CREATE：本通道正在推送全量群消息"
                    )
            msg = self._convert_event(event_type, data)
            if not msg:
                return
            self._remember_group(msg.get("group_id") or "")
            try:
                self._message_queue.put_nowait(msg)
            except asyncio.QueueFull:
                # Keep the newest context: dropping the incoming message would leave the
                # conversation with a hole exactly where a reply is being decided.
                try:
                    self._message_queue.get_nowait()
                except Exception:
                    pass
                self._message_queue.put_nowait(msg)
            return
        if event_type not in NOTICE_EVENTS:
            # Loud enough to notice in a log, quiet enough not to look like a failure:
            # the platform adds event types and a silent drop is how they stay unknown.
            # Once per type: channel bots push guild events constantly.
            logged = self._mutable("_unknown_logged", set)
            if self.logger and event_type not in logged:
                logged.add(event_type)
                self.logger.info(f"[QQOpenPlatform] 未处理的官方事件: {event_type}")
            return
        payload = self._normalize_notice(event_type, data, envelope_id)
        for sink in list(self._mutable("_event_sinks", list)):
            try:
                result = sink(event_type, payload)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:  # a broken consumer must not drop the transport
                if self.logger:
                    self.logger.warning(f"[QQOpenPlatform] 事件监听器异常({event_type}): {exc}")

    def _normalize_notice(self, event_type: str, data: dict, envelope_id: str) -> dict:
        """Common shape for non-message events (``channel`` matches ``CHANNEL``)."""
        out = {
            "event_type": event_type,
            "channel": self.CHANNEL,
            "group_id": str(data.get("group_openid") or data.get("group_id") or ""),
            "user_id": str(
                data.get("op_member_openid") or data.get("member_openid")
                or data.get("user_openid") or ""
            ),
            "timestamp": int(time.time()),
            "event_id": envelope_id,  # usable as the passive-reply ``event_id``
            "raw": data,
        }
        if event_type == "INTERACTION_CREATE":
            out["interaction_id"] = str(data.get("id") or "")
            out["interaction_type"] = data.get("type")
        if event_type == "GROUP_JOIN_REQUEST":
            out["join_request_id"] = str(data.get("join_request_id") or "")
            out["username"] = str(data.get("username") or "")
        return out

    def add_event_sink(self, callback) -> None:
        """Register a notice-event consumer: ``callback(event_type, payload)``.

        Message events are not delivered here; they keep arriving through
        :meth:`receive_message`, so an existing consumer needs no change.
        """
        sinks = self._mutable("_event_sinks", list)
        if callback not in sinks:
            sinks.append(callback)

    def remove_event_sink(self, callback) -> None:
        sinks = self._mutable("_event_sinks", list)
        if callback in sinks:
            sinks.remove(callback)

    def _mutable(self, name: str, factory):
        """Container attribute, created on first use (``__new__``-safe)."""
        value = getattr(self, name, None)
        if value is None:
            value = factory()
            setattr(self, name, value)
        return value

    def _remember_group(self, group_id: str) -> None:
        group_id = str(group_id or "").strip()
        if group_id:
            self._mutable("_seen_groups", dict)[group_id] = time.time()

    def is_group_muted(self, group_id: str) -> bool:
        """Local mute bookkeeping, same shape as ``OneBotClient.is_group_muted``.

        The only reliable signal this channel has is the platform rejecting a send with
        ``850018``; without recording it the caller keeps sending into a muted group.
        """
        gid = str(group_id or "").strip()
        if not gid:
            return False
        muted = self._mutable("_group_muted", dict)
        until = float(muted.get(gid, 0) or 0)
        if until and until > 0:
            if time.time() < until:
                return True
            muted.pop(gid, None)
        return False

    async def receive_message(self, timeout: float = 1.0) -> Optional[dict[str, Any]]:
        try:
            message = await asyncio.wait_for(self._message_queue.get(), timeout=timeout)  # noqa: ASYNC_BLOCK -- _message_queue is an asyncio.Queue; .get() is awaitable and does not block the event loop
        except asyncio.TimeoutError:
            return None
        # Inbound broadcast hook (optional): hand the normalized message to the
        # registered sink. Best-effort.
        if isinstance(message, dict):
            await self._dispatch_inbound(message)
        return message

    # ==========================================
    # message send
    # ==========================================

    def _next_msg_seq(self, reply_id: str) -> int:
        """The next ``msg_seq`` for a reply to ``reply_id`` (1, 2, 3, ...).

        The platform de-duplicates replies by ``(msg_id, msg_seq)``: a second reply to
        the same message with the same (or the default) seq is rejected. The plugin
        sends a multi-block answer as several messages that all reply to one id, so
        each needs its own seq.
        """
        now = time.monotonic()
        seqs = self._msg_seq_by_reply_id
        seq = seqs.pop(reply_id, (0, now))[0] + 1
        seqs[reply_id] = (seq, now)
        # Pop + reinsert keeps the dict in last-use order, so stale entries sit at the
        # front. Only age decides: a count cap would reset a counter the platform still
        # remembers once enough other messages were replied to in between.
        while seqs:
            oldest_id, (_seq, used_at) = next(iter(seqs.items()))
            if now - used_at <= _MSG_SEQ_TTL_SECONDS:
                break
            del seqs[oldest_id]
        return seq

    def _take_image_segment(self, image_url: str, data: dict[str, Any], content_parts: list[str]) -> str:
        """Apply one ``image`` segment; returns the image to send (the first one wins).

        A ``msg_type=7`` message carries one media item, so a second image in the same
        message is kept as placeholder text instead of silently replacing the first.
        """
        candidate = str(data.get("file") or "")
        if not image_url:
            return candidate
        if candidate:
            self._media_log("warning", "一条消息只能带一张图片，多出的图片以占位文字发送")
            content_parts.append(_IMAGE_PLACEHOLDER)
        return image_url

    def _expand_cq_segments(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Expand CQ codes inside a text segment into typed segments.

        The qq_auto_reply plugin's reply_delivery_node embeds CQ-code strings
        (e.g. ``[CQ:reply,id=...]``) in the text field; NapCat's OneBot protocol natively understands these codes, but the
        Open Platform needs real typed segments. Here the CQ codes inside a text segment
        are split out.

        A CQ **image** becomes placeholder text, never an ``image`` segment: this text
        is LLM-authored, and ``[CQ:image,file=<path>]`` would otherwise make the send
        paths read any file the process can read and upload it. Only an ``image``
        segment the caller passes explicitly is uploaded. Doing it here, once, keeps
        every send path that expands CQ codes under the same rule.
        """
        expanded: list[dict[str, Any]] = []
        for seg in segments:
            if seg.get("type") != "text":
                expanded.append(seg)
                continue
            raw = str(seg.get("data", {}).get("text", "") or "")
            if not raw:
                expanded.append(seg)
                continue
            # No CQ code -> put back as-is.
            if "[CQ:" not in raw:
                expanded.append(seg)
                continue
            # Has CQ codes -> split segment by segment.
            pos = 0
            for m in _CQ_CODE_RE.finditer(raw):
                if m.start() > pos:
                    expanded.append({"type": "text", "data": {"text": raw[pos:m.start()]}})
                cq_type = m.group(1)
                params_str = m.group(2)
                data: dict[str, str] = {}
                for param in params_str.split(","):
                    param = param.strip()
                    if "=" in param:
                        k, v = param.split("=", 1)
                        data[k.strip()] = v.strip()
                if cq_type == "image":
                    self._media_log(
                        "warning",
                        "忽略 LLM 文本里的 CQ 图片（不上传本地文件）；要发图请用显式 image 段",
                    )
                    expanded.append({"type": "text", "data": {"text": _IMAGE_PLACEHOLDER}})
                else:
                    expanded.append({"type": cq_type, "data": data})
                pos = m.end()
            if pos < len(raw):
                expanded.append({"type": "text", "data": {"text": raw[pos:]}})
        return expanded

    async def send_group_message_segments(
        self, group_id: str, segments: list[dict[str, Any]], *, record_sent: bool = True, keyboard: str = ""
    ) -> Optional[str]:
        """Convert OneBot segments to QQ Open Platform format and send."""
        content_parts: list[str] = []
        reply_msg_id = ""
        at_user_id = ""
        image_url = ""

        for seg in self._expand_cq_segments(segments):
            seg_type = str(seg.get("type") or "").strip()
            data = seg.get("data") or {}
            if seg_type == "reply":
                reply_msg_id = str(data.get("id") or "")
            elif seg_type == "at":
                at_user_id = str(data.get("qq") or "")
                content_parts.append(f"<@!{at_user_id}>")
            elif seg_type == "text":
                content_parts.append(str(data.get("text") or ""))
            elif seg_type == "image":
                # CQ images in the text never get here (see `_expand_cq_segments`).
                image_url = self._take_image_segment(image_url, data, content_parts)
            elif seg_type == "face":
                # small emoji -> text placeholder
                content_parts.append(f"[表情{data.get('id','')}]")
            elif seg_type == "record":
                content_parts.append("[语音消息]")

        content = "".join(content_parts).strip()
        if not content and not image_url:
            return None

        await self._ensure_token()
        body: dict[str, Any] = {}
        # Group images must be uploaded first to get file_info, then sent via msg_type=7 + media.
        if image_url:
            # The `_ensure_token()` above already made the token valid; the upload does
            # not have to ask again.
            file_info = await self._upload_group_image(group_id, image_url, token_checked=True)
            if file_info:
                body["msg_type"] = 7
                body["media"] = {"file_info": file_info}
                if content:
                    body["content"] = content
            else:
                # upload failed -> degrade to text
                if not content:
                    content = _IMAGE_PLACEHOLDER
                body["content"] = content
        else:
            # Auto-detect Markdown syntax (only clear format markers, avoid misjudging plain text).
            _MD_PATTERNS = (r'\*\*[^*]+\*\*', r'\*[^*]+\*', r'~~[^~]+~~', r'^> ', r'`[^`]+`', r'\[.+\]\(.+\)', r'^#{1,3} ')
            import re as _re
            is_md = any(_re.search(p, content, _re.MULTILINE) for p in _MD_PATTERNS)
            if is_md:
                body["msg_type"] = 2
                body["markdown"] = {"content": content}
            else:
                body["content"] = content

        if reply_msg_id:
            body["msg_id"] = reply_msg_id
            body["msg_seq"] = self._next_msg_seq(reply_msg_id)

        if keyboard and body.get("msg_type") == 7:
            # Rich-media (image) payloads can't carry buttons: buttons only apply to
            # type-2 rich text; forcing them on might have the platform reject the whole
            # message. Degrade the options to readable body so the user doesn't get a
            # "asked but no options" message.
            labels = " / ".join(
                b.strip() for b in keyboard.split("|") if b.strip()
            )
            if labels:
                existing = str(body.get("content") or "")
                body["content"] = (existing + "\n" + labels).strip()
            keyboard = ""
        if keyboard:
            buttons = [b.strip() for b in keyboard.split("|") if b.strip()][:4]
            if buttons:
                body.setdefault("msg_type", 2)
                body["keyboard"] = {
                    "content": {
                        "rows": [{
                            "buttons": [
                                {
                                    "id": f"btn_{i}",
                                    "render_data": {"label": b, "visited_label": b},
                                    "action": {"type": 2, "permission": {"type": 2}, "data": b, "unsupport_tips": "请升级QQ版本"},
                                }
                                for i, b in enumerate(buttons)
                            ]
                        }]
                    }
                }
                if content:
                    body.pop("content", None)
                    body["markdown"] = {"content": content}

        try:
            resp = await self._http.post(
                f"{self._API_BASE}/v2/groups/{group_id}/messages",
                json=body,
                headers=self._auth_headers(),
            )
            data = resp.json()
            msg_id = str(data.get("id") or "")
            if msg_id and record_sent:
                self.record_sent_message_id(msg_id)
            return msg_id if msg_id else None
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[QQOpenPlatform] 发送群消息失败: {e}")
            return None

    async def send_message(self, user_id: str, message: str) -> Optional[str]:
        """Send private plain text (used by the qq_auto_reply plugin's voice_reply_service)."""
        return await self.send_private_message_segments(
            user_id, [{"type": "text", "data": {"text": message}}],
        )

    async def send_group_message(self, group_id: str, message: str) -> Optional[str]:
        """Send group plain text (legacy interface compat)."""
        return await self.send_group_message_segments(
            group_id, [{"type": "text", "data": {"text": message}}],
        )

    async def send_private_record(
        self, user_id: str, file_uri: str, *, reply_message_id: str = "", **_: Any
    ) -> Optional[str]:
        """Send a private voice message: rich media ``file_type=3`` with ``msg_type=7``.

        Returns the message id, or ``None`` when upload/send failed so the caller can
        fall back to text. v2 does accept voice (silk / mp3 / wav), so the old
        "not supported" no-op was throwing away every TTS result on this channel.
        """
        return await self._send_media(
            scope="users", owner_id=user_id, file_type=FILE_TYPE_VOICE, source=file_uri,
            reply_message_id=reply_message_id,
        )

    async def send_private_message_segments(
        self, user_id: str, segments: list[dict[str, Any]], *, record_sent: bool = True
    ) -> Optional[str]:
        """Convert OneBot segments to QQ Open Platform private format and send.

        QQ Open Platform private chat supports only plain text + images; other types
        degrade to text placeholders.
        """
        content_parts: list[str] = []
        image_url = ""
        reply_msg_id = ""

        for seg in self._expand_cq_segments(segments):
            seg_type = str(seg.get("type") or "").strip()
            data = seg.get("data") or {}
            if seg_type == "text":
                content_parts.append(str(data.get("text") or ""))
            elif seg_type == "image":
                # CQ images in the text never get here (see `_expand_cq_segments`).
                image_url = self._take_image_segment(image_url, data, content_parts)
            elif seg_type == "reply":
                # A passive reply, as on the group path and in `send_private_image`:
                # without `msg_id` the platform treats the send as an active message,
                # which is quota-limited.
                reply_msg_id = str(data.get("id") or "")
            elif seg_type == "at":
                at_qq = str(data.get("qq") or "")
                content_parts.append(f"[@{at_qq}]" if at_qq else "[@某人]")
            elif seg_type == "face":
                content_parts.append(f"[表情{data.get('id','')}]")
            elif seg_type == "record":
                content_parts.append("[语音]")
            elif seg_type == "rps":
                content_parts.append("[猜拳]")
            elif seg_type == "dice":
                content_parts.append("[骰子]")
            elif seg_type == "contact":
                content_parts.append("[推荐联系人]")
            elif seg_type == "music":
                content_parts.append("[音乐分享]")
            elif seg_type == "mface":
                content_parts.append("[动画表情]")
            elif seg_type == "file":
                content_parts.append(f"[文件 {data.get('name', '')}]")
            elif seg_type == "json":
                content_parts.append("[卡片消息]")
            else:
                pass  # ignore unknown types

        content = "".join(content_parts).strip()
        if not content and not image_url:
            return None

        await self._ensure_token()
        body: dict[str, Any] = {}
        # Private images are the same two steps as group ones (upload -> msg_type=7 +
        # media.file_info); what makes the platform accept the send is the **users**
        # upload scope, since an upload made through one interface can only be sent
        # through that same one. Before this, a private image was never uploaded at
        # all: the whole segment collapsed into a "[图片]" text line, so a sticker
        # reply arrived as three characters of text.
        if image_url and str(user_id or "").strip():
            # As above: the outer `_ensure_token()` already prepared the token.
            file_info = await self.upload_image(
                scope="users", owner_id=str(user_id), source=image_url, token_checked=True,
            )
            if file_info:
                body["msg_type"] = 7
                body["media"] = {"file_info": file_info}
                if content:
                    body["content"] = content
            else:
                # upload failed -> degrade to text, as the group path does
                body["content"] = content or _IMAGE_PLACEHOLDER
        else:
            if image_url and not content:
                # No usable private id to upload against: keep the old text fallback.
                content = _IMAGE_PLACEHOLDER
            body["content"] = content

        if reply_msg_id:
            body["msg_id"] = reply_msg_id
            body["msg_seq"] = self._next_msg_seq(reply_msg_id)

        try:
            resp = await self._http.post(
                f"{self._API_BASE}/v2/users/{user_id}/messages",
                json=body,
                headers=self._auth_headers(),
            )
            data = resp.json()
            msg_id = str(data.get("id") or "")
            if msg_id and record_sent:
                self.record_sent_message_id(msg_id)
            return msg_id if msg_id else None
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[QQOpenPlatform] 发送私聊失败: {e}")
            return None

    async def send_group_poke(self, group_id: str, user_id: str, **_: Any) -> Optional[str]:
        raise self._unsupported("poke", "v2 has no poke interface for any conversation")
    async def send_group_image(
        self, group_id: str, image_data: str, *, reply_message_id: str = "", at_user_id: str = "", sub_type: str = ""
    ) -> Optional[str]:
        segments: list[dict[str, Any]] = []
        if reply_message_id:
            segments.append({"type": "reply", "data": {"id": reply_message_id}})
        if at_user_id:
            segments.append({"type": "at", "data": {"qq": at_user_id}})
        segments.append({"type": "image", "data": {"file": image_data}})
        return await self.send_group_message_segments(group_id, segments, record_sent=False)

    async def send_group_record(
        self, group_id: str, file_uri: str, *, reply_message_id: str = "", at_user_id: str = "", **_: Any
    ) -> Optional[str]:
        """Send a group voice message.

        ``at_user_id`` is accepted for signature parity with the OneBot client and is
        deliberately not attached: one media item per message, and a voice message with
        an @ payload was what made the TTS path unusable on NapCat too.
        """
        return await self._send_media(
            scope="groups", owner_id=group_id, file_type=FILE_TYPE_VOICE, source=file_uri,
            reply_message_id=reply_message_id,
        )

    async def send_group_ark_card(
        self, group_id: str, ark_obj: dict[str, Any], **_: Any
    ) -> bool:
        """Send a group card. v2 has no ark on the send side, so this builds an image-text card.

        ``supports_ark_cards`` is therefore False while ``supports_card_messages`` is
        True: callers that check the first flag alone would drop a card they can
        actually deliver. Both the NapCat-shaped ``{"ark": {"kv": [{"key": "#TITLE#"...}]}}``
        input and a plain ``{"title": ..., "description": ...}`` are accepted, because
        the qq_auto_reply plugin still assembles the former.
        """
        try:
            await self._ensure_token()
            resp = await self._http.post(
                f"{self._API_BASE}/v2/groups/{group_id}/messages",
                json={"msg_type": 8, "card": self._card_message_payload(ark_obj)},
                headers=self._auth_headers(),
            )
            data = resp.json()
            return bool(data.get("id"))
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[QQOpenPlatform] 发送图文卡片失败: {e}")
            return False

    async def get_login_status(self) -> dict[str, Any]:
        if self._ws and self._self_id:
            return {"status": "online", "self_id": self._self_id, "nickname": self._self_nickname or None}
        return {"status": "offline", "self_id": None, "nickname": None}

    def record_sent_message_id(self, message_id: str) -> None:
        mid = str(message_id or "").strip()
        if mid:
            self._sent_message_ids[mid] = time.time()

    @property
    def onebot_url(self) -> str:
        return self._API_BASE

    @onebot_url.setter
    def onebot_url(self, value: str) -> None:
        pass  # Open Platform needs no external URL set

    async def _try_reconnect(self) -> None:
        """Reconnect with exponential backoff."""
        delay = 1.0
        while not self._closing:
            try:
                if self.logger:
                    self.logger.info(f"[QQOpenPlatform] 尝试重连 ({delay:.0f}s)...")
                await asyncio.sleep(delay)
                if self._closing:
                    return
                # clean old connection
                if self._ws:
                    try: await self._ws.close()
                    except Exception: pass
                self._ws = None; self.ws = None
                # reconnect + handshake (prefer Resume to replay missed events)
                await self._refresh_token()
                ws_url = await self._get_gateway_url()
                self._ws = await websockets.connect(ws_url, max_size=2 ** 23)
                self.ws = self._ws
                await self._handshake(is_reconnect=True)
                self._legacy_upload_unsupported = False
                if self.logger:
                    self.logger.info("[QQOpenPlatform] 重连成功")
                return  # back to _receive_loop
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"[QQOpenPlatform] 重连失败: {e}")
            delay = min(delay * 2, 60.0)  # exponential backoff, cap 60s

    # ==========================================
    # internal helpers
    # ==========================================

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"QQBot {self._access_token}",
            "Content-Type": "application/json",
        }

    async def _refresh_token(self) -> None:
        if self._http is None:
            raise RuntimeError("QQ 开放平台未连接，请先调用 connect()")
        try:
            resp = await self._http.post(self._TOKEN_URL, json={
                "appId": self._app_id,
                "clientSecret": self._client_secret,
            })
            data = resp.json()
            self._access_token = str(data.get("access_token") or "")
            expires_in = int(data.get("expires_in") or 7200)
            self._token_expires_at = time.time() + expires_in - 300  # refresh 5 min early
            if self.logger:
                self.logger.info("[QQOpenPlatform] access_token 已获取")
        except Exception as e:
            if self.logger:
                self.logger.error(f"[QQOpenPlatform] 获取 access_token 失败: {e}")
            raise

    async def _ensure_token(self) -> None:
        if time.time() >= self._token_expires_at:
            await self._refresh_token()

    async def _upload_group_image(
        self, group_id: str, image_url: str, *, token_checked: bool = False,
    ) -> str:
        """Upload a group image to the Open Platform; returns file_info or empty.

        Kept as the group path's entry point (``send_group_message_segments`` calls
        it) and forwarded to ``upload_image`` so both directions upload the same way:
        the upload scope is ``groups``, and the two protocols (legacy direct upload
        first, then the documented chunked one) are tried inside the mixin. This used
        to be an inline copy of the legacy protocol only -- which is why the group
        image path silently degraded to a plain text placeholder on a live run
        (2026-09-26), while private images never uploaded at all.
        """
        return await self.upload_image(
            scope="groups", owner_id=str(group_id or ""), source=str(image_url or ""),
            token_checked=token_checked,
        )

    async def _get_gateway_url(self) -> str:
        await self._ensure_token()
        resp = await self._http.get(
            f"{self._API_BASE}/gateway/bot",
            headers=self._auth_headers(),
        )
        data = resp.json()
        return str(data.get("url") or f"{self._API_BASE}/websocket")

    async def _heartbeat_loop(self) -> None:
        while not self._closing:
            if not self._ws:
                await asyncio.sleep(1)
                continue
            await asyncio.sleep(self._heartbeat_interval)
            if self._ws:
                try:
                    await self._ws.send(json.dumps({"op": 1, "d": self._last_seq}))
                except Exception:
                    pass  # _receive_loop handles reconnection

    # ==========================================
    # event conversion
    # ==========================================

    def _convert_event(self, event_type: str, data: dict[str, Any]) -> Optional[dict[str, Any]]:
        """QQ Open Platform event -> internal unified message format."""
        author = data.get("author", {})
        # The Open Platform author has only an openid key, no username; this read
        # finds a value only if the protocol adds a key later. Missing nickname is
        # bucketed by the qq_auto_reply plugin's display_name_service.
        user_nickname = str(author.get("username") or "") or None

        if event_type == "C2C_MESSAGE_CREATE":
            return {
                "message_type": "private",
                "channel": self.CHANNEL,
                "user_id": pick_actor_id(author, _C2C_ACTOR_ID_KEYS),
                "user_nickname": user_nickname,
                "content": str(data.get("content") or ""),
                "message_id": str(data.get("id") or ""),
                "timestamp": int(time.time()),
                "is_at_bot": True,
                "is_reply_to_bot": False,
                "group_id": "",
                "quoted_message_id": "",
                "mentioned_user_ids": [],
                "mentions_other_user": False,
                "mentions_all": False,
                "raw": data,
                "attachments": self._extract_attachments(data),
            }

        if event_type in ("GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"):
            content = str(data.get("content") or "")
            # This channel's group identifier is itself an openid (see the
            # qq_auto_reply plugin's display_name_service note); official v2 puts
            # it on group_openid rather than group_id (bot-docs' GROUP_AT_MESSAGE_CREATE field table and example
            # JSON both have only group_openid), so the real effective branch has
            # always been this fallback. The order still cannot be reversed: when
            # group_id has a value it must keep being used, otherwise the group
            # subject_id switches keys entirely, and memory/scopes.py matches by exact
            # bytes with no aliases, so existing scoped group memory would disconnect
            # all at once. Falling back only when the original key is empty gives both
            # zero behavior change and guards against total message loss.
            group_id = str(data.get("group_id") or data.get("group_openid") or "")
            mentioned_ids: list[str] = []
            # Two mention shapes exist in the wild: the documented v2 form
            # ``<@member_openid>`` (hex) and the legacy ``<@!uin>`` that older payloads
            # and the OneBot side still produce. Parsing only the legacy one meant every
            # v2 mention was invisible -- including mentions of the bot itself.
            v2_tags = _AT_OPENID_RE.findall(content)
            legacy_tags = _AT_LEGACY_RE.findall(content)
            for user in data.get("mentions") or []:
                if isinstance(user, dict):
                    who = str(user.get("member_openid") or user.get("id") or "").strip()
                    if who and who not in mentioned_ids:
                        mentioned_ids.append(who)
            for who in v2_tags + legacy_tags:
                if who not in mentioned_ids:
                    mentioned_ids.append(who)
            mentions_all = "<@all>" in content
            clean_content = _AT_OPENID_RE.sub("", _AT_LEGACY_RE.sub("", content))
            clean_content = clean_content.replace("<@all>", "").strip()
            # The bot's own group-side identity is member_openid, which only bot_state
            # (permission-gated) or a learned mention can supply; READY's id is app-level.
            self_ids = {str(self._self_id or "")}
            if self._bot_member_openid:
                self_ids.add(self._bot_member_openid)
            if event_type == "GROUP_AT_MESSAGE_CREATE":
                # The platform only emits this event when the bot itself was addressed.
                is_at_bot = True
            else:
                is_at_bot = bool(self_ids & set(mentioned_ids)) or any(
                    f"<@{sid}>" in content for sid in self_ids if sid)
                if is_at_bot and len(mentioned_ids) == 1:
                    # The single mention was the bot: learn its group-side id so later
                    # full-mode pushes can recognise it too. bot_state may never come.
                    self._bot_member_openid = mentioned_ids[0]
            if self._self_id:
                mentions_other_user = any(mid not in self_ids for mid in mentioned_ids)
            else:
                # Without a known self id, only several mentions prove someone else was named.
                mentions_other_user = len(mentioned_ids) > 1

            return {
                "message_type": "group",
                "channel": self.CHANNEL,
                "user_id": pick_actor_id(author, _GROUP_ACTOR_ID_KEYS),
                "user_nickname": user_nickname,
                "content": clean_content,
                "message_id": str(data.get("id") or ""),
                "timestamp": int(time.time()),
                "is_at_bot": is_at_bot,
                "is_reply_to_bot": False,
                "group_id": group_id,
                "quoted_message_id": "",
                "mentioned_user_ids": mentioned_ids,
                "mentions_other_user": mentions_other_user,
                "mentions_all": mentions_all,
                "raw": data,
                "attachments": self._extract_attachments(data),
            }
        return None

    @staticmethod
    def _extract_attachments(data: dict[str, Any]) -> list[dict[str, Any]]:
        attachments: list[dict[str, Any]] = []
        for att in data.get("attachments") or []:
            if isinstance(att, dict):
                url = att.get("url") or ""
                content_type = str(att.get("content_type") or "")
                if url:
                    att_type = "image" if content_type.startswith("image/") else "file"
                    attachments.append({"type": att_type, "url": url})
        return attachments

    # ==========================================
    # v2 REST helpers
    # ==========================================

    async def _api(
        self, method: str, path: str, *, params: Optional[dict] = None,
        payload: Optional[dict] = None, retry_on_token: bool = True,
    ) -> dict:
        """One v2 call: token, auth headers, err_code judgement, one token retry.

        ``err_code`` is the only stable failure signal (the docs say not to judge by
        ``message``), and success may carry no body at all -- a 200 with an empty body
        is returned as ``{}`` rather than treated as an error.
        """
        from urllib.parse import urlencode

        await self._ensure_token()
        query = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        url = f"{self._API_BASE}{path}" + (f"?{urlencode(query)}" if query else "")
        headers = self._auth_headers()
        headers["Content-Type"] = "application/json; charset=utf-8"
        try:
            response = await self._http.request(method, url, headers=headers, json=payload)
        except Exception as exc:
            raise QQOpenPlatformAPIError(
                method=method, path=path, message=f"transport error: {exc}") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        try:
            data = response.json()
        except Exception:
            data = {}
        body = data if isinstance(data, dict) else {}
        err_code = _to_int(body.get("err_code") or body.get("code"))
        if status == 200 and err_code == 0:
            return body
        if err_code == ERR_TOKEN_INVALID and retry_on_token:
            self._access_token = ""
            self._token_expires_at = 0
            await self._refresh_token()
            return await self._api(
                method, path, params=params, payload=payload, retry_on_token=False)
        if err_code == ERR_GROUP_MUTED:
            gid = _group_id_of(path)
            if gid:
                # Without this the caller keeps sending into a muted group and learns
                # nothing except a rejected send, every round.
                self._mutable("_group_muted", dict)[gid] = time.time() + 300
        raise QQOpenPlatformAPIError(
            method=method, path=path, http_status=status, err_code=err_code,
            message=str(body.get("message") or ""), trace_id=str(body.get("trace_id") or ""))

    async def _upload_media(
        self, *, scope: str, owner_id: str, source: str, file_type: int
    ) -> str:
        """Upload one rich-media item and return ``file_info`` (``""`` when it failed).

        Two documented paths only: URL hand-off, and inline binary (``file_data``,
        up to 5 MB). shortcut: no chunked upload here; anything larger is refused with a
        log line, and the upgrade is ``upload_prepare``/``upload_part_finish``.
        """
        source = str(source or "").strip()
        if not source:
            return ""
        endpoint = f"/v2/{scope}/{owner_id}/files"
        if source.startswith(("http://", "https://")):
            body: dict[str, Any] = {
                "file_type": int(file_type), "url": source, "srv_send_msg": False}
        else:
            path = source[7:] if source.startswith("file://") else source
            try:
                raw = await asyncio.to_thread(_read_bytes, path)
            except Exception as exc:
                if self.logger:
                    self.logger.warning(f"[QQOpenPlatform] 富媒体读取失败: {exc}")
                return ""
            if not raw:
                return ""
            if len(raw) > INLINE_UPLOAD_MAX_BYTES:
                if self.logger:
                    self.logger.warning(
                        f"[QQOpenPlatform] 富媒体 {len(raw)} 字节超过内联上传上限，"
                        "需要改走分片上传")
                return ""
            body = {
                "file_type": int(file_type),
                "file_name": path.replace("\\", "/").rsplit("/", 1)[-1],
                "file_data": base64.b64encode(raw).decode("ascii"),
                "srv_send_msg": False,
            }
        try:
            data = await self._api("POST", endpoint, payload=body)
        except QQOpenPlatformAPIError as exc:
            if self.logger:
                self.logger.warning(f"[QQOpenPlatform] 富媒体上传失败: {exc}")
            return ""
        return str(data.get("file_info") or "")

    async def _send_media(
        self, *, scope: str, owner_id: str, file_type: int, source: str,
        reply_message_id: str = "", record_sent: bool = True,
    ) -> Optional[str]:
        file_info = await self._upload_media(
            scope=scope, owner_id=owner_id, source=source, file_type=file_type)
        if not file_info:
            return None
        payload: dict[str, Any] = {"msg_type": 7, "media": {"file_info": file_info}}
        anchor = str(reply_message_id or "").strip()
        if anchor:
            payload["msg_id"] = anchor
            payload["msg_seq"] = self._next_msg_seq(anchor)
        try:
            data = await self._api("POST", f"/v2/{scope}/{owner_id}/messages", payload=payload)
        except QQOpenPlatformAPIError as exc:
            if self.logger:
                self.logger.warning(f"[QQOpenPlatform] 富媒体消息发送失败: {exc}")
            return None
        message_id = str(data.get("id") or "")
        if message_id and record_sent:
            self.record_sent_message_id(message_id)
        return message_id or None

    def _card_message_payload(self, obj: dict) -> dict:
        """Build an image-text card body from either the ark-kv shape or a plain dict."""
        kv = obj.get("ark", {}).get("kv") if isinstance(obj.get("ark"), dict) else None
        if isinstance(kv, list):
            fields = {
                str(item.get("key") or "").strip("#").upper(): str(item.get("value") or "")
                for item in kv if isinstance(item, dict)}
            content = {
                "title": fields.get("TITLE") or "卡片",
                "description": fields.get("DESC") or fields.get("SUBTITLE") or "",
                "url": fields.get("URL") or "",
                "pic_url": fields.get("IMG") or ""}
        else:
            raw = obj.get("content") if isinstance(obj.get("content"), dict) else obj
            content = {
                "title": str(raw.get("title") or "卡片"),
                "description": str(raw.get("description") or raw.get("desc") or ""),
                "url": str(raw.get("url") or ""),
                "pic_url": str(raw.get("pic_url") or raw.get("img") or "")}
        # Non-https links are rejected by the platform; fix the scheme instead of
        # silently dropping the fields the caller asked for.
        for key in ("url", "pic_url"):
            if content[key] and not content[key].startswith("https://"):
                content[key] = "https://" + content[key].lstrip("/")
        if not content["description"]:
            content["description"] = content["title"]
        return {"type": "tuwen", "content": content}

    # ==========================================
    # Operations the Open Platform does not offer
    # ==========================================
    #
    # These used to ``return {}`` / ``return []``. An empty dict does not stop a caller
    # that only checks "did it raise", so each of them looked like a success: mute
    # detection read a field that was never there, reactions looked sent, history looked
    # empty by chance. Raising with the reason is the honest version -- and callers that
    # care already wrap these and degrade visibly.

    def _unsupported(self, operation: str, reason: str) -> QQOpenPlatformUnsupportedError:
        return QQOpenPlatformUnsupportedError(operation, reason)

    async def set_msg_emoji_like(self, **kw):
        raise self._unsupported(
            "message reactions",
            "reactions exist only for guild/channel messages; v2 has no group or c2c variant")

    async def send_like(self, **kw):
        raise self._unsupported("profile like", "no such interface in v2")

    async def get_profile_like(self, **kw):
        raise self._unsupported("profile like", "no such interface in v2")

    async def mark_msg_as_read(self, **kw):
        raise self._unsupported("mark as read", "no such interface in v2")

    async def mark_private_msg_as_read(self, **kw):
        raise self._unsupported("mark as read", "no such interface in v2")

    async def mark_group_msg_as_read(self, **kw):
        raise self._unsupported("mark as read", "no such interface in v2")

    async def _mark_all_as_read(self, **kw):
        raise self._unsupported("mark as read", "no such interface in v2")

    async def get_msg(self, **kw):
        raise self._unsupported(
            "message lookup", "v2 has no message-read interface; a bot only learns what "
            "the platform pushed to it")

    async def get_forward_msg(self, **kw):
        raise self._unsupported("forwarded message read-back", "no such interface in v2")

    async def get_friend_msg_history(self, **kw):
        raise self._unsupported(
            "message history", "no such interface in v2; use the consumer's own backlog")

    async def get_group_msg_history(self, **kw):
        raise self._unsupported(
            "message history", "no such interface in v2; use the consumer's own backlog")

    async def send_group_forward_msg(self, **kw):
        raise self._unsupported("merged forward", "no such interface in v2")

    async def send_private_forward_msg(self, **kw):
        raise self._unsupported("merged forward", "no such interface in v2")

    async def send_forward_msg(self, **kw):
        raise self._unsupported("merged forward", "no such interface in v2")

    async def forward_friend_single_msg(self, **kw):
        raise self._unsupported("single-message forward", "no such interface in v2")

    async def forward_group_single_msg(self, **kw):
        raise self._unsupported("single-message forward", "no such interface in v2")

    async def get_friend_list(self, **kw):
        raise self._unsupported(
            "friend list", "v2 identifies users by openid only and lists no friends")

    async def set_friend_add_request(self, **kw):
        raise self._unsupported("friend request approval", "no such interface in v2")

    async def delete_friend(self, **kw):
        raise self._unsupported("friend removal", "no such interface in v2")

    async def get_friends_with_category(self, **kw):
        raise self._unsupported("friend categories", "no such interface in v2")

    async def friend_poke(self, **kw):
        raise self._unsupported("poke", "v2 has no poke interface for any conversation")

    async def group_poke(self, **kw):
        raise self._unsupported("poke", "v2 has no poke interface for any conversation")

    async def set_group_whole_ban(self, **kw):
        raise self._unsupported(
            "whole-group mute", "restrict_chat_setting is applied per member; the "
            "group-wide mute rules are read-only here")

    async def set_group_admin(self, **kw):
        raise self._unsupported("group admin changes", "no such interface in v2")

    async def set_group_card(self, **kw):
        raise self._unsupported("member card nickname", "no such interface in v2")

    async def set_group_name(self, **kw):
        raise self._unsupported("group renaming", "no such interface in v2")

    async def set_group_leave(self, **kw):
        raise self._unsupported("leaving a group", "not offered by the API; use the console")

    async def set_group_special_title(self, **kw):
        raise self._unsupported("special titles", "no such interface in v2")

    async def set_group_sign(self, **kw):
        raise self._unsupported("group sign-in", "no such interface in v2")

    async def send_group_sign(self, **kw):
        raise self._unsupported("group sign-in", "no such interface in v2")

    async def set_group_portrait(self, **kw):
        raise self._unsupported("group portrait", "no such interface in v2")

    async def get_group_at_all_remain(self, **kw):
        raise self._unsupported(
            "@all quota read-back", "the platform does not report the remaining count; "
            "sending without the admin role, or after the daily cap, returns an err_code")

    async def get_group_ignore_add_request(self, **kw):
        raise self._unsupported("ignored join requests", "no such interface in v2")

    async def get_group_system_msg(self, **kw):
        raise self._unsupported("system messages", "no such interface in v2")

    async def _send_group_notice(self, **kw):
        raise self._unsupported("group announcements", "no such interface in v2")

    async def _get_group_notice(self, **kw):
        raise self._unsupported("group announcements", "no such interface in v2")

    async def _del_group_notice(self, **kw):
        raise self._unsupported("group announcements", "no such interface in v2")

    async def send_group_ai_record(self, **kw):
        raise self._unsupported(
            "AI voice pack", "no such interface in v2; voice messages go through "
            "send_group_record with a real audio file")

    async def get_group_honor_info(self, **kw):
        raise self._unsupported("group honours", "no such interface in v2")

    async def get_group_info_ex(self, **kw):
        raise self._unsupported("extended group info", "no such interface in v2")

    async def get_essence_msg_list(self, **kw):
        raise self._unsupported("essence messages", "no such interface in v2")

    async def set_essence_msg(self, **kw):
        raise self._unsupported("essence messages", "no such interface in v2")

    async def delete_essence_msg(self, **kw):
        raise self._unsupported("essence messages", "no such interface in v2")

    async def get_stranger_info(self, **kw):
        raise self._unsupported(
            "stranger profile", "only group members can be looked up, by member_openid")

    async def delete_group_file(self, **kw):
        raise self._unsupported(
            "group file system", "v2 offers upload only; no file management or listing")

    async def create_group_file_folder(self, **kw):
        raise self._unsupported("group file system", "v2 offers upload only")

    async def delete_group_folder(self, **kw):
        raise self._unsupported("group file system", "v2 offers upload only")

    async def get_group_file_system_info(self, **kw):
        raise self._unsupported("group file system", "v2 offers upload only")

    async def get_group_root_files(self, **kw):
        raise self._unsupported("group file system", "v2 offers upload only")

    async def get_group_files_by_folder(self, **kw):
        raise self._unsupported("group file system", "v2 offers upload only")

    async def get_group_file_url(self, **kw):
        raise self._unsupported("group file system", "v2 offers upload only")

    async def get_cookies(self, **kw):
        raise self._unsupported(
            "web login credentials", "an official bot authenticates with AppID and "
            "AppSecret only; there is no cookie and no CSRF token")

    async def get_csrf_token(self, **kw):
        raise self._unsupported("web login credentials", "no web session exists for a bot")

    async def get_credentials(self, **kw):
        raise self._unsupported("web login credentials", "no web session exists for a bot")

    async def get_image(self, **kw):
        raise self._unsupported(
            "local image cache", "attachments arrive as URLs in the event; nothing is "
            "cached on disk by this connector")

    async def download_file(self, **kw):
        raise self._unsupported("file download", "use the URL the event carries")

    async def get_file(self, **kw):
        raise self._unsupported("file download", "use the URL the event carries")

    # ==========================================
    # Operations v2 does support
    # ==========================================

    async def recall_message(self, message_id: str, *, group_id: str = "", user_id: str = "") -> bool:
        """Delete one message. Own messages always; others' only when the bot is a group admin.

        The platform rejects a recall older than 2 minutes with its own err_code, which
        is raised rather than swallowed -- a caller that thinks it retracted something
        still on screen is worse off than one that gets an error.
        """
        message_id = str(message_id or "").strip()
        group_id = str(group_id or "").strip()
        user_id = str(user_id or "").strip()
        if not message_id:
            return False
        if group_id:
            path = f"/v2/groups/{group_id}/messages/{message_id}"
        elif user_id:
            path = f"/v2/users/{user_id}/messages/{message_id}"
        else:
            # The two scopes are different endpoints; guessing would either fail or
            # recall inside the wrong conversation.
            raise ValueError("recall_message needs group_id or user_id")
        await self._api("DELETE", path)
        return True

    async def delete_msg(self, message_id: str = "", **kw) -> dict:
        await self.recall_message(
            message_id or str(kw.get("message_id") or ""),
            group_id=str(kw.get("group_id") or ""), user_id=str(kw.get("user_id") or ""))
        return {"status": "ok"}

    async def get_group_info(self, group_id: str = "", **_: Any) -> dict:
        gid = str(group_id or "").strip()
        if not gid:
            return {}
        return await self._api("GET", f"/v2/groups/{gid}/info")

    async def get_group_bot_state(self, group_id: str, *, force: bool = False) -> dict:
        """Read this group's reception setting, the bot's role and the push permission.

        This is what makes :attr:`receives_all_messages` answerable before any traffic
        arrives. The interface is permission-gated, so the caller must tolerate the
        error and fall back to observed events.
        """
        gid = str(group_id or "").strip()
        if not gid:
            return {}
        state = await self._api("GET", f"/v2/groups/{gid}/bot_state")
        self._recv_msg_setting = str(state.get("recv_msg_setting") or "")
        self._bot_member_role = str(state.get("member_role") or "")
        return state

    async def get_group_member_info(self, group_id: str = "", user_id: str = "", **_: Any) -> dict:
        gid = str(group_id or "").strip()
        member = str(user_id or "").strip()
        if not gid or not member:
            return {}
        return await self._api("GET", f"/v2/groups/{gid}/members/{member}")

    async def get_group_member_list(self, group_id: str = "", **kw: Any) -> list:
        """Page through members (30 at a time) until the cursor runs out or max_pages hits."""
        gid = str(group_id or "").strip()
        if not gid:
            return []
        pages = max(1, min(_to_int(kw.get("max_pages")) or 10, 20))
        out: list = []
        cursor = ""
        for _ in range(pages):
            data = await self._api("GET", f"/v2/groups/{gid}/members", params={"cursor": cursor})
            out.extend(data.get("members") or [])
            cursor = str(data.get("next_cursor") or "")
            if not cursor:
                break
        return out

    async def set_group_ban(self, group_id: str = "", user_id: str = "", duration: int = 600, **_: Any) -> dict:
        """Mute one member for ``duration`` seconds; 0 lifts it. Needs the group admin role."""
        gid = str(group_id or "").strip()
        member = str(user_id or "").strip()
        if not gid or not member:
            return {}
        seconds = _to_int(duration)
        entry = {
            "op": "del" if seconds <= 0 else "add",
            "member_openid": member,
            # The platform takes an expiry timestamp, not a duration.
            "mute_expire_at": "" if seconds <= 0 else _rfc3339_in(seconds)}
        await self._api(
            "POST", f"/v2/groups/{gid}/restrict_chat_setting", payload={"members": [entry]})
        return {"status": "ok"}

    async def get_group_shut_list(self, group_id: str = "", **_: Any) -> list:
        data = await self._api("GET", f"/v2/groups/{str(group_id or '').strip()}/restrict_chat_setting")
        members = data.get("members")
        return members if isinstance(members, list) else []

    async def set_group_kick(self, group_id: str = "", user_id: str = "", **kw: Any) -> dict:
        gid = str(group_id or "").strip()
        member = str(user_id or "").strip()
        if not gid or not member:
            return {}
        body: dict[str, Any] = {"member_openids": [member]}
        if kw.get("add_to_member_blacklist") or kw.get("reject_add_request"):
            body["add_to_member_blacklist"] = True
        return await self._api("POST", f"/v2/groups/{gid}/batch_remove_members", payload=body)

    async def list_group_join_requests(self, group_id: str = "", cursor: str = "", limit: int = 20, **_: Any) -> dict:
        gid = str(group_id or "").strip()
        if not gid:
            return {}
        return await self._api(
            "GET", f"/v2/groups/{gid}/join_request_list",
            params={"cursor": cursor, "limit": max(1, min(_to_int(limit) or 20, 50))})

    async def set_group_add_request(self, flag: str = "", approve: bool = True, reason: str = "", **kw: Any) -> dict:
        """Approve or decline one join request; ``list_group_join_requests`` supplies the ids.

        Join-request *events* need the GROUP_MEMBER_EVENT intent which this connector
        does not subscribe to, so approvals are pulled rather than pushed.
        """
        gid = str(kw.get("group_id") or kw.get("group_openid") or "").strip()
        member = str(kw.get("user_id") or kw.get("member_openid") or "").strip()
        if not gid or not member:
            return {}
        body: dict[str, Any] = {
            "op": "approve" if approve else "decline", "join_request_id": str(flag or "")}
        if not approve and reason:
            body["reject_reason"] = str(reason)[:100]
        await self._api("POST", f"/v2/groups/{gid}/approval_join_request/{member}", payload=body)
        return {"status": "ok"}

    async def operate_group_member_blacklist(
        self, group_id: str, member_openids: list, *, op: str = "add") -> dict:
        gid = str(group_id or "").strip()
        ids = [str(x).strip() for x in (member_openids or []) if str(x).strip()][:20]
        if not gid or not ids or op not in ("add", "del"):
            return {}
        return await self._api(
            "POST", f"/v2/groups/{gid}/member_blacklist", payload={"op": op, "member_openids": ids})

    async def list_group_member_blacklist(self, group_id: str, cursor: str = "") -> dict:
        gid = str(group_id or "").strip()
        if not gid:
            return {}
        return await self._api("GET", f"/v2/groups/{gid}/member_blacklist", params={"cursor": cursor})

    async def upload_group_file(self, file: str = "", name: str = "", **kw: Any) -> dict:
        """Send a group file (``file_type=4``). Upload-only: v2 has no file management."""
        gid = str(kw.get("group_id") or kw.get("group_openid") or "").strip()
        if not gid:
            return {}
        mid = await self._send_media(
            scope="groups", owner_id=gid, file_type=FILE_TYPE_FILE, source=str(file or ""))
        return {"file_id": mid, "id": mid} if mid else {}

    async def upload_private_file(self, file: str = "", name: str = "", **kw: Any) -> dict:
        uid = str(kw.get("user_id") or kw.get("openid") or "").strip()
        if not uid:
            return {}
        mid = await self._send_media(
            scope="users", owner_id=uid, file_type=FILE_TYPE_FILE, source=str(file or ""))
        return {"file_id": mid, "id": mid} if mid else {}

    async def get_menu(self) -> dict:
        return await self._api("GET", "/v2/menu")

    async def set_menu(self, menu: dict) -> dict:
        """Overwrite the whole global menu (``items`` max 10)."""
        if not isinstance(menu, dict):
            raise ValueError("set_menu needs the full menu object")
        await self._api("PUT", "/v2/menu", payload={"menu": menu})
        return {"status": "ok"}

    async def respond_interaction(self, interaction_id: str, code: int = 0) -> dict:
        """Answer an INTERACTION_CREATE; the platform expects it within about 3 seconds."""
        iid = str(interaction_id or "").strip()
        if not iid:
            return {}
        await self._api("PUT", f"/interactions/{iid}", payload={"code": _to_int(code)})
        return {"status": "ok", "interaction_id": iid}

    async def generate_url_link(self, callback_data: str = "") -> dict:
        return await self._api(
            "POST", "/v2/generate_url_link", payload={"callback_data": str(callback_data or "")[:32]})

    async def get_group_list(self) -> list:
        """The groups this connection actually saw a message from.

        There is no "my groups" endpoint, and the old flat ``[]`` could not tell
        "the bot is in no group" from "the ids were never learned" -- which matters
        because group ids here are ``group_openid`` values that cannot be guessed from
        a QQ group number.
        """
        return [
            {"group_id": gid, "group_name": ""}
            for gid in sorted(self._mutable("_seen_groups", dict))
        ]

    # Status
    async def can_send_image(self) -> bool: return True
    async def can_send_record(self) -> bool: return True  # voice = rich media file_type=3
    async def get_status(self) -> dict: return {"online": self._ws is not None}


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _group_id_of(path: str) -> str:
    match = _re.match(r"/v2/groups/([^/?]+)", str(path or ""))
    return match.group(1) if match else ""


def _read_bytes(path: str) -> bytes:
    """Blocking read; callers run it in a thread so a slow disk cannot stall the loop."""
    if not path or not os.path.isfile(path):
        return b""
    with open(path, "rb") as handle:
        return handle.read()


def _rfc3339_in(seconds: int) -> str:
    """RFC3339 timestamp ``seconds`` from now, in the platform's own +08:00 frame."""
    from datetime import datetime, timedelta, timezone

    moment = datetime.now(timezone(timedelta(hours=8))) + timedelta(seconds=int(seconds))
    return moment.replace(microsecond=0).isoformat()
