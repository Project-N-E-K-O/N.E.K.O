"""QQ Open Platform connector -- official QQ Bot API."""

from __future__ import annotations

import asyncio
import base64
import inspect as _inspect
import json
import os
import time
from collections import deque
from typing import Any, Optional

import re as _re
import httpx
import websockets

from ..base import ConnectionBase
from .open_platform_media import (
    QQOpenPlatformMediaMixin, _BASE64_PREFIX, _base64_decoded_size, _brief,
    _decode_base64_source, _digests, _local_path)

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

#: Intents this connector identifies with by default -- unchanged bits that carry
#: the group full/AT message, robot add/remove and friend families.
BASE_INTENTS = (1 << 25) | (1 << 12)
#: ``GROUP_JOIN_REQUEST`` / ``GROUP_MEMBER_ADD`` / ``GROUP_MEMBER_REMOVE`` only
#: arrive with GROUP_MEMBER_EVENT (1 << 24), ``INTERACTION_CREATE`` only with
#: INTERACTION (1 << 26). Subscribing an intent the bot is not entitled to makes
#: the gateway drop the connection outright, so those bits stay opt-in through
#: the ``extra_intents`` argument; the event types remain registered so wired
#: consumers start working the moment the operator opts in.

#: Rich-media ``file_type`` values of the upload interfaces.
FILE_TYPE_IMAGE = 1
FILE_TYPE_VIDEO = 2
FILE_TYPE_VOICE = 3
FILE_TYPE_FILE = 4

#: Passive-reply and mute relevant ``err_code``s ("judge by err_code, not by message").
ERR_TOKEN_INVALID = 40011027
#: ``40054002`` is what the plain send interface reports while the bot is muted;
#: ``850018`` comes from the rich-media/upload family. Watching only one leaves
#: the other mute unrecorded.
ERR_GROUP_MUTED = frozenset({850018, 40054002})

#: How long an id stays in ``_seen_groups`` without another message.
_SEEN_GROUPS_TTL = 30 * 24 * 3600

#: ``file_data`` (inline binary upload) size ceiling; above it URL/chunked upload is
#: required. shortcut: inline upload only, >5 MB falls back to the URL path.
INLINE_UPLOAD_MAX_BYTES = 5 * 1024 * 1024
#: Hard ceiling for any single rich-media upload, inline or chunked. Voice and file
#: accept 200 MB per the media docs; this is also the read cap, so a caller pointing at
#: an oversized file cannot make the process hold more than this in memory.
UPLOAD_HARD_MAX_BYTES = 200 * 1024 * 1024


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

#: How long an inbound ``message_id`` is remembered, for duplicate suppression. Short on
#: purpose: it only has to span the gap between two deliveries of the *same* event, which
#: arrive back to back. A long window would risk dropping a legitimately distinct message
#: that happened to reuse an id.
_INBOUND_DEDUP_TTL_SECONDS = 60.0
#: Prune the dedup table only once it grows past this, so the common path stays O(1).
_INBOUND_DEDUP_PRUNE_AT = 256

#: How long one notice-sink callback may take before the connector gives up on it, and
#: how many such callbacks may be outstanding at once. Both bound the same failure:
#: sinks run detached now (so a slow one cannot stall the receive loop), and detached
#: work needs a ceiling of its own or a hanging consumer turns a notification burst
#: into an unbounded task pile.
_NOTICE_SINK_TIMEOUT_SECONDS = 10.0
_NOTICE_SINK_MAX_TASKS = 32

#: How long a cached whole-group mute rule stays believed. The platform sends no event
#: when an admin lifts it, so a TTL is the only thing that lets the connector answer
#: that group again without a restart.
_SHUT_RULE_TTL_SECONDS = 300.0

#: How long one unattributable group message waits for its ``GROUP_AT_MESSAGE_CREATE``
#: copy before it goes out as chatter. Short because it adds latency to the reply path,
#: and long enough for the platform to deliver the second frame: the two arrive
#: back-to-back in every observation so far.
_AT_MERGE_WINDOW_SECONDS = 0.5

#: How many unattributed messages may wait at once. Past this they are delivered
#: immediately: the window is a courtesy to a frame that usually arrives
#: milliseconds later, not a queue of its own.
_ATTRIBUTION_HOLD_MAX = 8

#: Most member openids the group blacklist endpoint accepts in one call. Callers may
#: pass more; the operation batches rather than dropping the tail.
_BLACKLIST_BATCH = 20

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
#: GROUP_MESSAGE_CREATE belongs in this list: with the privilege on it is the frame
#: that carries the group's unaddressed traffic, so identity forensics without it is
#: blind to exactly the mode whose behaviour is under discussion.
_IDENTITY_PROBE_EVENTS = ("GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE",
                            "C2C_MESSAGE_CREATE")
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

    #: Tolerance for instances built through ``__new__`` (the harnesses in
    #: tests/unit/test_qq_open_plat_convert_event.py construct them on purpose, to pin
    #: that a partially built connection cannot raise inside event handling). Every
    #: read below has a class-level answer, so a missing instance attribute is a
    #: default, not an AttributeError that the receive loop would report as a
    #: disconnect -- which would read as an unstable connection instead of a bug.
    logger: Any = None

    #: Defaults for the v2 reception state. Instances are sometimes built through
    #: ``__new__`` (test harnesses, partial reconstruction), and a missing attribute
    #: there raises inside event handling -- which the receive loop reports as a
    #: disconnect, so it would look like an unstable connection rather than a bug.
    #: The per-group facts live in dicts created on first use (``_mutable``), so no
    #: mutable default is ever shared between instances: the bot's group-side openid,
    #: its role and the push setting are all issued **per conversation**, and a single
    #: attribute let one group's value decide another group's @-attribution.

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
        extra_intents: int = 0,
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
        #: Identify intents; bits beyond ``BASE_INTENTS`` must be granted to this
        #: bot first, or the gateway disconnects on identify.
        self._intents = BASE_INTENTS | _to_int(extra_intents)
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
        #: Per group: the privilege is granted conversation by conversation, so one
        #: connection-wide boolean would let a single full-push group advertise every
        #: other group as full-push too.
        self._all_messages_observed_groups: set[str] = set()
        self._recv_msg_setting_by_group: dict[str, str] = {}
        self._bot_openid_by_group: dict[str, str] = {}
        self._bot_member_role_by_group: dict[str, str] = {}
        #: group_id -> the group allows unprompted (proactive) sends. From
        #: GROUP_MSG_RECEIVE / GROUP_MSG_REJECT and bot_state.allow_proactive_msg.
        self._proactive_allowed_by_group: dict[str, bool] = {}
        #: Full-push copies whose @ could not be attributed, waiting one window for
        #: the AT frame. See ``_hold_for_at_frame``.
        self._attribution_hold: dict[str, dict] = {}
        self._attribution_hold_tasks: dict[str, object] = {}
        self._hold_saturated_warned: set = set()
        #: Inbound ids accepted recently -> the monotonic time they arrived. Guards
        #: against the platform delivering one message twice (see ``_enqueue_inbound``).
        self._seen_inbound_ids: dict[str, float] = {}
        #: Detached notice-sink tasks, so a slow listener cannot stall the receive
        #: loop (see ``_fan_out_notice``). Cleared on disconnect.
        self._sink_tasks: set = set()
        #: One token refresh at a time (see ``_api``).
        self._token_lock: Optional[asyncio.Lock] = None
        #: Groups this connection actually saw a message from; the platform offers no
        #: "my groups" listing, so observed ids are the only honest answer.
        self._seen_groups: dict[str, float] = {}
        #: Notice-event sinks (message events keep going to ``_message_queue``).
        self._event_sinks: list = []
        #: group_id -> muted-until, same shape as ``OneBotClient._group_muted``.
        self._group_muted: dict[str, float] = {}
        #: group_id -> last observed ``global_rule`` of the mute settings.
        self._shut_global_rule: dict[str, dict] = {}
        #: group_id -> the time that rule was read. See ``_SHUT_RULE_TTL_SECONDS``.
        self._shut_global_rule_at: dict[str, float] = {}

    @property
    def needs_attention(self) -> bool:
        """Whether this channel delivers traffic that is not addressed to the bot.

        It used to be a flat ``False`` ("only @bot"), which is true only while the
        group is in mention-only mode. Once the platform pushes
        ``GROUP_MESSAGE_CREATE`` the consumer does have to decide who a message was
        for, so the flag follows :attr:`receives_all_messages` instead of a constant.

        Connection-wide by contract: it answers "is any conversation here delivering
        unaddressed traffic", which is what an attention consumer branches on before
        it has a message. For one group ask
        :meth:`group_receives_all_messages` instead.
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
        """True while any group is known to push every group message.

        Proof only, the rule #3706 established and this merge keeps: the intents
        bitmap is something we submit, the privilege is granted group by group by a
        human, and ``bot_state`` is permission-gated, so a subscription, a setting or a
        probe is never evidence. Receiving a ``GROUP_MESSAGE_CREATE`` is.

        ``recv_msg_setting`` therefore stays a report of what bot_state said
        (:meth:`group_recv_msg_setting`) and deliberately does not drive this flag.
        Folding the two together is how a claim outlives the platform's decision.

        Aggregated pessimistically in the other direction too: one group's traffic
        must not advertise a second group as full-push. Use
        :meth:`group_receives_all_messages` wherever a group id is at hand, which is
        everywhere a reply decision is made.
        """
        return bool(self._mutable("_all_messages_observed_groups", set))

    def group_receives_all_messages(self, group_id: str) -> bool:
        """Whether this group has actually delivered a whole-group message.

        Proof only, for the same reason as :attr:`receives_all_messages`: a group
        whose ``bot_state`` says ``all`` but which has pushed nothing since this
        session started reports ``False``, because that is what we can act on.
        """
        gid = str(group_id or "").strip()
        if not gid:
            return False
        return gid in self._mutable("_all_messages_observed_groups", set)

    def group_proactive_allowed(self, group_id: str):
        """Whether one group lets the bot send unprompted. ``None`` when unknown.

        ``GROUP_MSG_RECEIVE`` / ``GROUP_MSG_REJECT`` set this, and ``bot_state``
        confirms it with ``allow_proactive_msg``. Nothing about it belongs in
        :attr:`receives_all_messages`: proactive push and full message push are two
        different switches pointing in opposite directions (out and in).
        """
        gid = str(group_id or "").strip()
        if not gid:
            return None
        return self._mutable("_proactive_allowed_by_group", dict).get(gid)

    def group_recv_msg_setting(self, group_id: str) -> str:
        """``all`` / ``only_mention`` / ``mention_and_context`` / ``""`` for one group."""
        gid = str(group_id or "").strip()
        if not gid:
            return ""
        return str(self._mutable("_recv_msg_setting_by_group", dict).get(gid, "") or "")

    def group_bot_member_role(self, group_id: str) -> str:
        """``member`` / ``admin`` / ``owner`` for one group, or ``""`` when unknown."""
        gid = str(group_id or "").strip()
        if not gid:
            return ""
        return str(self._mutable("_bot_member_role_by_group", dict).get(gid, "") or "")

    @property
    def supports_ark_cards(self) -> bool:
        return False  # v2 has no ark on the send side any more (receive side still does)

    @property
    def supports_card_messages(self) -> bool:
        """Image-text card (``msg_type=8``): what ``send_group_ark_card`` now builds."""
        return True

    @property
    def supports_reference_messages(self) -> bool:
        """Whether the connector can see a quote the user made, not create one.

        Receive side works: ``message_reference.message_id`` is read and matched
        against the ids this connection sent, so ``is_reply_to_bot`` and
        ``quoted_message_id`` are real. Send side does not: building a visible quote
        needs a ``REFIDX`` token that only arrives on ``INTERACTION_CREATE``, which
        needs the interaction intent (1<<26) the platform has not granted this bot.
        A consumer that wants to *emit* quotes must check that separately.
        """
        return True

    @property
    def supports_recall(self) -> bool:
        """Recall of own messages, and of others' messages when the bot is a group admin."""
        return True

    @property
    def recv_msg_setting(self) -> str:
        """The push setting, when exactly one group is known (else ``""``).

        The real answer is per group, so a single connection-wide string would be a
        guess. Use :meth:`group_recv_msg_setting` wherever a group id exists; this
        property only survives for callers that provably serve one group, and it
        refuses to answer rather than mix conversations.
        """
        return self._sole_group_value("_recv_msg_setting_by_group")

    @property
    def bot_member_role(self) -> str:
        """The bot's role, when exactly one group is known (else ``""``).

        Same reasoning as :attr:`recv_msg_setting`: ``member`` in one group and
        ``admin`` in another has no connection-wide answer worth reporting.
        """
        return self._sole_group_value("_bot_member_role_by_group")

    def _sole_group_value(self, container: str) -> str:
        """One value out of a per-group map, but only when the map holds exactly one.

        Returning the first entry would be the bug this avoids: dict order is an
        accident of which group spoke first.
        """
        values = {str(v or "") for v in self._mutable(container, dict).values()}
        if len(values) != 1:
            # Two groups, two answers: there is no connection-wide value worth
            # reporting, and picking one would make the result depend on which group
            # spoke first.
            return ""
        return values.pop()

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
        # Identify -- this establishes a NEW session: either the very first one, or the
        # fallback after Resume was rejected or timed out. Either way the full-push
        # proof must be re-learned rather than inherited, because a group can switch
        # back to "@-only" while the connection is down. A successful Resume returns
        # above and keeps the known-good value, which is why the reset belongs here and
        # not in the reconnect path.
        self._mutable("_all_messages_observed_groups", set).clear()
        # Identify is the only reset point for the reception proof, per #3706: Resume
        # keeps what it had, and nothing else touches it. The setting map clears
        # alongside it because it is a claim about privileges that may have moved
        # while we were away -- it never drove the flag anyway.
        self._mutable("_recv_msg_setting_by_group", dict).clear()
        self._mutable("_proactive_allowed_by_group", dict).clear()
        await self._ws.send(json.dumps({
            "op": 2, "d": {
                "token": f"QQBot {self._access_token}",
                # getattr keeps a __new__-built instance usable; BASE_INTENTS is the
                # documented default set, and extra_intents only ever adds to it.
                "intents": getattr(self, "_intents", BASE_INTENTS),
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
        # A held copy must not die with the connection. No AT frame from the old
        # session will ever arrive for it, and the new session will not re-send the
        # message, so flush what is waiting before the tasks go away.
        for held_id in list(self._mutable("_attribution_hold", dict)):
            self._release_attribution_hold(held_id)
        for task in list(self._mutable("_attribution_hold_tasks", dict).values()):
            task.cancel()
        self._attribution_hold_tasks.clear()
        # Detached notice sinks belong to this connection: leaving them running past a
        # stop means a callback lands in the UI after the user pressed stop.
        for task in list(self._mutable("_sink_tasks", set)):
            task.cancel()
        self._sink_tasks.clear()
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
            if event_type == "GROUP_MESSAGE_CREATE":
                # The privilege is per group and invisible in the intents bitmap, so a
                # real push is the only proof. Recorded here rather than in
                # _convert_event so a payload that fails to convert still proves it.
                self._note_full_group_message(data)
            msg = self._convert_event(event_type, data)
            if not msg:
                return
            self._remember_group(msg.get("group_id") or "")
            if (event_type == "GROUP_MESSAGE_CREATE"
                    and not msg.get("is_at_bot")
                    and self._attribution_ambiguous(data)):
                self._hold_for_at_frame(msg)
                return
            if event_type == "GROUP_AT_MESSAGE_CREATE":
                # This frame is the authoritative copy of a message whose full-push twin
                # may still be waiting out the attribution window. Drop the waiting copy
                # now instead of letting it flush as a duplicate.
                self._drop_attribution_hold(str(msg.get("message_id") or "").strip())
            refusal = self._enqueue_inbound(msg)
            if refusal == "duplicate" and self.logger:
                # Same message already queued from another event type: routine, but it
                # is what a double reply would have started with.
                self.logger.info(
                    f"[QQOpenPlatform] 丢弃重复投递的消息 {msg.get('message_id')!r} "
                    f"(event={event_type})"
                )
            # The capacity refusal is logged inside _enqueue_inbound, where the
            # eviction decision is known.
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
        self._apply_notice_state(event_type, payload)
        self._fan_out_notice(event_type, payload)

    def _apply_notice_state(self, event_type: str, payload: dict) -> None:
        """Keep the reception-mode proof in step with what the group just did.

        ``receives_all_messages`` and ``needs_attention`` used to be one-way latches:
        the first ``GROUP_MESSAGE_CREATE`` set them and nothing ever cleared them, not
        even ``GROUP_DEL_ROBOT`` or a reconnect. Being removed from a group kept the
        group advertised as full-push for the life of the connection.

        Only two things reset the proof, and both are real changes of state: leaving
        the group, and a new Identify session (see ``_handshake``).

        ``GROUP_MSG_RECEIVE`` and ``GROUP_MSG_REJECT`` deliberately do NOT touch it.
        Those two notice the group administrator allowed or blocked the bot's
        *proactive* messages (the platform labels them "accept" and "reject" the
        robot's active messages) -- a send-side permission. They
        say nothing about ``recv_msg_setting``, which is the push-side choice between
        "@-only" and "every message". Mapping them onto the reception state turned an
        admin enabling proactive push into a claim that the group delivers every
        message, and made the consumer pay for attention arbitration it does not need.
        They set ``_proactive_allowed_by_group`` instead, which is what they describe.

        State is per group, so the reset is per group too: being removed from one group
        must not revoke the proof another group already gave.
        """
        gid = str(payload.get("group_id") or "").strip()
        if event_type == "GROUP_MSG_RECEIVE" and gid:
            self._mutable("_proactive_allowed_by_group", dict)[gid] = True
            return
        if event_type == "GROUP_MSG_REJECT" and gid:
            # Proactive-send permission only. The reception proof stays: this notice is
            # not about the push switch, and clearing the proof here would let an
            # unrelated admin action silently revoke a mode we have proved.
            self._mutable("_proactive_allowed_by_group", dict)[gid] = False
            return
        if event_type == "GROUP_DEL_ROBOT" and gid:
            # Out of this group: nothing it once proved still applies.
            self._mutable("_recv_msg_setting_by_group", dict).pop(gid, None)
            self._mutable("_all_messages_observed_groups", set).discard(gid)
            self._mutable("_bot_openid_by_group", dict).pop(gid, None)
            self._mutable("_seen_groups", dict).pop(gid, None)
            self._mutable("_bot_member_role_by_group", dict).pop(gid, None)
            self._mutable("_shut_global_rule", dict).pop(gid, None)
            self._mutable("_shut_global_rule_at", dict).pop(gid, None)
            self._mutable("_proactive_allowed_by_group", dict).pop(gid, None)

    def _fan_out_notice(self, event_type: str, payload: dict) -> None:
        """Hand one notice to every sink without waiting on any of them.

        The sinks used to be awaited in place, inside the single ``_receive_loop``
        task. One listener that waits on a slow HTTP call therefore stopped the
        connector from reading the *next* message, and a listener that never returned
        stopped it for good -- catching the exception cannot help, because nothing was
        ever raised. Catching is still needed (a broken consumer must not drop the
        transport), so it moved into the task wrapper.

        Coroutines go out as detached tasks; anything a sink returns synchronously
        still runs inline, since by definition it already returned.
        """
        for sink in list(self._mutable("_event_sinks", list)):
            self._adopt_notice_sink(sink, event_type, payload)

    def _adopt_notice_sink(self, sink, event_type: str, payload: dict) -> None:
        """Hand one notification to a sink without the receive loop waiting on it.

        Exactly one coroutine object crosses the task boundary, and the task owns it
        from birth. Two earlier shapes leaked an un-awaited coroutine each: calling the
        sink first and wrapping its result (a cancel before the first step destroyed
        it), and wrapping a call coroutine inside an awaiter coroutine (the outer task
        could be cancelled while the inner object had never started). Both produced a
        ``RuntimeWarning`` per shutdown under traffic, and the reject path closed only
        the outer object.

        A coroutine function is called *inside* the task. A plain callable is invoked
        inline, because it already returned and its exception belongs to this frame; if
        what it returned is awaitable, that coroutine becomes the task's own, so
        nothing is left over to leak, and the deadline is attached with a timer rather
        than a second coroutine (r4576525889 / r4576493234: the wrapper-free shape had
        dropped the timeout, and a hung listener then held its slot forever).

        The slot check sits with the code that takes a slot. Checking it up front meant
        a saturated pool also skipped plain synchronous listeners, which never create a
        task and never needed a slot -- a silent loss of every other consumer the moment
        one listener hung (r4576541138, problem 2).
        """
        if asyncio.iscoroutinefunction(sink):
            if not self._sink_slot_available(event_type):
                return
            task = asyncio.create_task(self._run_notice_sink(sink, event_type, payload))
            self._track_sink_task(task)
            return
        try:
            result = sink(event_type, payload)
        except Exception as exc:  # a broken consumer must not drop the transport
            if self.logger:
                self.logger.warning(
                    f"[QQOpenPlatform] 事件监听器异常({event_type}): {exc}")
            return
        if not _inspect.isawaitable(result):
            return
        if not self._sink_slot_available(event_type):
            # The awaitable has been created by the call above and this task would own
            # it; refusing here would strand it un-awaited, so the slot is decided
            # before the task and the awaitable both go. Only a coroutine can be
            # closed: a Future belongs to whoever made it and is left alone.
            if asyncio.iscoroutine(result):
                result.close()
            return
        # create_task takes a coroutine only. An awaitable sink result can also be a
        # bare Future or a Task-like (the shape a reviewer reproduced: a plain
        # ``lambda et, p: gate.wait()``), so ensure_future decides, and the timeout
        # below then works on either kind.
        task = asyncio.ensure_future(result)
        task.add_done_callback(
            lambda t, et=event_type: self._note_sink_task_failure(et, t))
        self._track_sink_task(task)
        self._bound_sink_task(task, event_type)

    def _sink_slot_available(self, event_type: str) -> bool:
        """Whether a notification may still take a task slot, with the reason logged."""
        # Bounded on purpose: a sink that never finishes must not turn a traffic burst
        # into an unbounded task pile. Dropping the newest notification is visible in
        # the log; blocking the loop is not.
        if len(self._mutable("_sink_tasks", set)) < _NOTICE_SINK_MAX_TASKS:
            return True
        if self.logger:
            self.logger.warning(
                f"[QQOpenPlatform] 事件监听任务已满（{_NOTICE_SINK_MAX_TASKS}），"
                f"丢弃 {event_type} 的一次通知"
            )
        return False

    def _track_sink_task(self, task) -> None:
        tasks = self._mutable("_sink_tasks", set)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    def _bound_sink_task(self, task, event_type: str) -> None:
        """Give an already-created coroutine task its deadline, without wrapping it.

        ``call_later`` + ``Task.cancel`` needs no second coroutine object, and a task
        cancelled before its first step gets the cancellation thrown into it, so the
        coroutine is closed by asyncio rather than left un-awaited.
        """
        deadline = asyncio.get_running_loop().call_later(
            _NOTICE_SINK_TIMEOUT_SECONDS, self._timeout_sink_task, task, event_type)
        task.add_done_callback(lambda _t, d=deadline: d.cancel())

    def _timeout_sink_task(self, task, event_type: str) -> None:
        if task.done():
            return
        task.cancel()
        if self.logger:
            self.logger.warning(
                f"[QQOpenPlatform] 事件监听器超时({event_type}, "
                f"{_NOTICE_SINK_TIMEOUT_SECONDS}s)，已取消该通知任务")

    def _note_sink_task_failure(self, event_type: str, task) -> None:
        """Log what a bare sink coroutine raised, so its failure is not silent."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None and self.logger:
            self.logger.warning(f"[QQOpenPlatform] 事件监听器异常({event_type}): {exc}")

    async def _run_notice_sink(self, sink, event_type: str, payload: dict) -> None:
        """Call a coroutine sink, await it, and bound it, all inside this task."""
        try:
            async with asyncio.timeout(_NOTICE_SINK_TIMEOUT_SECONDS):
                await sink(event_type, payload)
        except asyncio.TimeoutError:
            if self.logger:
                self.logger.warning(
                    f"[QQOpenPlatform] 事件监听器超时({event_type}, "
                    f"{_NOTICE_SINK_TIMEOUT_SECONDS}s)，已放弃等待该通知")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.logger:
                self.logger.warning(f"[QQOpenPlatform] 事件监听器异常({event_type}): {exc}")

    def _attribution_ambiguous(self, data: dict) -> bool:
        """True when a full-push frame @-mentions somebody we cannot identify as us.

        Same three inputs ``_convert_event`` uses, and it must stay in step with it:
        an inline marker the ``mentions`` array does not explain, no ``is_you`` in the
        payload, and no openid learned for this group yet. Keep the two in sync or one
        of them silently stops firing.
        """
        content = str(data.get("content") or "")
        gid = str(data.get("group_id") or data.get("group_openid") or "").strip()
        if not gid or self._remembered_bot_openid(gid):
            return False
        inline = _AT_OPENID_RE.findall(content) + _AT_LEGACY_RE.findall(content)
        if not inline:
            return False
        structured = set()
        for item in data.get("mentions") or []:
            if isinstance(item, dict):
                if item.get("is_you") is True:
                    return False
                who = str(
                    item.get("member_openid") or item.get("openid") or item.get("id") or ""
                ).strip()
                if who:
                    structured.add(who)
        return any(who not in structured for who in inline)

    def _hold_for_at_frame(self, msg: dict[str, Any]) -> None:
        """Wait one short window before delivering an unattributable group message.

        The summon can reach us as ``GROUP_MESSAGE_CREATE`` first and
        ``GROUP_AT_MESSAGE_CREATE`` second. In-place merging only helps while the
        first copy is still in the queue, and the common case is a waiting consumer
        that takes it immediately: the AT frame then found nothing to upgrade and the
        summon was delivered as chatter anyway.

        Holding the unattributable copy closes that hole, and costs one window only
        for frames that carry an @ we cannot attribute while the group's openid is
        still unknown -- a per-group, once-per-session state. If the AT frame arrives,
        it converts (``is_at_bot=True`` is the platform's own statement), enqueues,
        and this copy is dropped as the duplicate on flush. If it never arrives, the
        copy goes out unchanged, which is what happened before this existed.
        """
        mid = str(msg.get("message_id") or "").strip()
        holds = self._mutable("_attribution_hold", dict)
        if mid and mid in holds:
            # One window per message id. Re-registering here would leave the earlier
            # timer running with nothing left to cancel it (drop and disconnect only
            # reach the newest registration), and its completion callback would then
            # discard the newer task's entry instead of its own (r4576525899).
            holds[mid] = msg
            return
        if not mid or len(holds) >= _ATTRIBUTION_HOLD_MAX:
            # Bounded. Holding is a courtesy to the AT frame, not a reason to park an
            # unbounded queue of messages: past the cap, deliver immediately and say so
            # once per group, so a group whose openid never gets learned does not turn
            # every mention-bearing message into a delayed one.
            self._enqueue_inbound(msg)
            self._note_hold_saturation()
            return
        holds[mid] = msg
        task = asyncio.create_task(self._flush_attribution_hold(mid))
        self._mutable("_attribution_hold_tasks", dict)[mid] = task
        # Pop only when the registration still points at this task, so a stale timer can
        # never orphan the live one.
        task.add_done_callback(lambda _t, key=mid, own=task: (
            self._mutable("_attribution_hold_tasks", dict).pop(key, None)
            if self._mutable("_attribution_hold_tasks", dict).get(key) is own else None))

    def _note_hold_saturation(self) -> None:
        if not self.logger or self._mutable("_hold_saturated_warned", set):
            return
        # Recorded so the operator can see the window is being skipped, without turning
        # a busy group into a log flood.
        self._mutable("_hold_saturated_warned", set).add(True)
        self.logger.warning(
            f"[QQOpenPlatform] 归因等待已满 {_ATTRIBUTION_HOLD_MAX} 条，"
            "后续未归因消息直接入队（该群尚未学到机器人 member_openid）")

    def _drop_attribution_hold(self, mid: str) -> None:
        """Discard a waiting full-push copy because the AT frame has taken over."""
        if not mid:
            return
        msg = self._mutable("_attribution_hold", dict).pop(mid, None)
        task = self._mutable("_attribution_hold_tasks", dict).pop(mid, None)
        if task is not None:
            task.cancel()
        if msg is not None and self.logger:
            self.logger.info(
                f"[QQOpenPlatform] 点名帧到达，撤销等待中的全量副本 {mid!r}")

    async def _flush_attribution_hold(self, mid: str) -> None:
        """Deliver a held copy if no better-attributed frame took over first.

        Two costs of this window, stated here because both are real:

        - ordering: the held message enters the queue after any later message from the
          same group, so a "question then self-answer" pair can reach the consumer in
          reverse order, up to 0.5 s apart;
        - persistence: the window ends early only when an AT frame arrives. Learning
          the group's bot openid needs ``is_you`` or ``bot_state``, and neither the
          receive loop nor any caller probes ``bot_state`` today, so in a group whose
          frames carry no ``is_you`` every inline-mention message waits the full window
          until the cap above takes over. The durable fix is on the host layer, where
          the reply decision can consume the AT frame instead of the connector guessing
          twice.
        """
        try:
            await asyncio.sleep(_AT_MERGE_WINDOW_SECONDS)
        except asyncio.CancelledError:
            # A cancellation means the AT frame took ownership of this message id.
            return
        self._release_attribution_hold(mid)

    def _release_attribution_hold(self, mid: str) -> None:
        msg = self._mutable("_attribution_hold", dict).pop(mid, None)
        if msg is None:
            return
        refusal = self._enqueue_inbound(msg)
        if self.logger:
            if refusal == "duplicate":
                # The AT frame got there first: the summon is already queued with the
                # platform's own attribution, so this copy is the one to drop.
                self.logger.info(
                    f"[QQOpenPlatform] 点名帧已到达，丢弃未归因的全量副本 {mid!r}")
            else:
                self.logger.info(
                    f"[QQOpenPlatform] 归因等待结束，按未点名投递 {mid!r}"
                    f"（该群仍未学到机器人自己的 member_openid）")

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
        if not group_id:
            return
        seen = self._mutable("_seen_groups", dict)
        now = time.time()
        # shortcut: TTL sweep happens at record time; a group silent for 30 days
        # drops out of ``get_group_list``, which stays honest for "actually seen".
        for stale in [k for k, ts in seen.items() if now - ts > _SEEN_GROUPS_TTL]:
            seen.pop(stale, None)
        seen[group_id] = now

    def _is_pending_inbound(self, message_id: str) -> bool:
        """True when a message with this id is still sitting in the queue.

        Scanning beats keeping a parallel pending-set: the queue is bounded
        (``message_queue_size``, 100 by default) and this only runs for an id that is
        already being re-delivered. A second structure would also have to be kept in
        sync by every eviction path, and a missed sync there is exactly the silent
        double-delivery this guard exists to prevent.
        """
        mid = str(message_id or "").strip()
        if not mid:
            return False
        queue = getattr(self, "_message_queue", None)
        items = getattr(queue, "_queue", None)
        if items is None:
            return False
        try:
            for queued in list(items):
                if (
                    isinstance(queued, dict)
                    and str(queued.get("message_id") or "").strip() == mid
                ):
                    return True
        except Exception:
            # Unknown internals must never turn a duplicate check into a crash.
            return False
        return False

    def _is_duplicate_inbound(self, message_id: str) -> bool:
        """True when this inbound message id was already accepted.

        Two horizons, because they answer different questions:

        - **still queued** -> a duplicate, no matter how long it has waited. The TTL
          must not apply here: a slow consumer could otherwise leave a message pending
          past the window, and a redelivery would queue the same id twice and deliver
          it twice.
        - **already delivered** -> a duplicate only within the short TTL, so a reused
          id is not suppressed forever.

        Query only. Recording is a separate step (:meth:`_remember_inbound`) performed
        *after* the message really lands in the queue. Recording here would be a trap:
        a message that arrives, gets recorded, and is then evicted to make room would be
        suppressed on redelivery while no longer pending anywhere -- lost for good.
        """
        mid = str(message_id or "").strip()
        if not mid:
            return False
        if self._is_pending_inbound(mid):
            return True
        seen = self._mutable("_seen_inbound_ids", dict)
        previous = seen.get(mid)
        if previous is None:
            return False
        return (time.monotonic() - previous) < _INBOUND_DEDUP_TTL_SECONDS

    def _remember_inbound(self, message_id: Any) -> None:
        """Record that this id is now pending, or already handed to the caller."""
        mid = str(message_id or "").strip()
        if not mid:
            return
        seen = self._mutable("_seen_inbound_ids", dict)
        now = time.monotonic()
        if len(seen) > _INBOUND_DEDUP_PRUNE_AT:
            cutoff = now - _INBOUND_DEDUP_TTL_SECONDS
            for stale in [key for key, stamp in seen.items() if stamp < cutoff]:
                seen.pop(stale, None)
        seen[mid] = now

    def _forget_inbound(self, message_id: Any) -> None:
        """Drop the dedup record when the message is no longer pending anywhere."""
        mid = str(message_id or "").strip()
        if not mid:
            return
        self._mutable("_seen_inbound_ids", dict).pop(mid, None)

    def _enqueue_inbound(self, msg: dict[str, Any]) -> Optional[str]:
        """Queue one converted message, keeping the dedup table consistent.

        Returns ``None`` when the message is queued, otherwise the refusal reason:

        - ``"duplicate"`` -- the same id was accepted moments ago;
        - ``"queue_full"`` -- the queue holds only bot-addressed messages and this one
          is not addressed, so it is refused rather than sacrificing a summon.

        They are returned separately because the caller reports them differently: a
        duplicate is routine (the platform documents redelivery), while refusing for
        capacity says the consumer is falling behind.

        Ordering matters: the id is recorded only once the message is safely queued, and
        an *evicted* message's record is removed again. Both halves exist so a message
        can never end up "not pending, yet considered already seen".
        """
        mid = str(msg.get("message_id") or "").strip()
        if self._is_duplicate_inbound(mid):
            # "merged" only when the second frame actually said more than the queued
            # copy does. Plain redelivery stays "duplicate", so the caller's log and
            # the contract #3706 shipped keep their meaning.
            return "merged" if self._merge_pending_duplicate(msg) else "duplicate"
        evicted_id = ""
        try:
            self._message_queue.put_nowait(msg)
        except asyncio.QueueFull:
            evicted_id = self._evict_inbound_for(msg)
            if evicted_id is None:
                # Everything queued is addressed to the bot and this arrival is not:
                # keep the summons, drop the chatter.
                if self.logger:
                    self.logger.warning(
                        f"[QQOpenPlatform] 队列已满且全是点名消息，丢弃普通消息 {mid or '(未知)'}"
                    )
                return "queue_full"
            self._message_queue.put_nowait(msg)
            if evicted_id:
                # The evicted message is no longer pending, so it must not stay
                # remembered: a redelivery of it is the only remaining chance to have
                # it processed.
                self._forget_inbound(evicted_id)
            if self.logger:
                kept = "点名消息" if msg.get("is_at_bot") else "消息"
                self.logger.warning(
                    f"[QQOpenPlatform] 消息队列已满，丢弃 {evicted_id or '(未知)'} "
                    f"以容纳{kept} {mid or '(未知)'}"
                )
        self._remember_inbound(mid)
        return None

    def _merge_pending_duplicate(self, msg: dict[str, Any]) -> bool:
        """Fold a second delivery of one message into the copy that is still queued.

        This is where an @ signal used to die. With the group privilege on, the same
        mention can arrive as ``GROUP_MESSAGE_CREATE`` first and as
        ``GROUP_AT_MESSAGE_CREATE`` second. Until this group's bot openid is known the
        full-push copy reads ``is_at_bot=False``; the AT frame -- the one that says,
        from the platform itself, that the bot was addressed -- then matched the dedup
        rule and was dropped, so the bot saw a summon, queued it as chatter and never
        answered.

        Only a still-queued copy can be upgraded. One already handed to the caller is
        gone: re-queuing it would produce the second reply this whole guard exists to
        prevent, so that path stays a plain duplicate.
        """
        mid = str(msg.get("message_id") or "").strip()
        if not mid:
            return False
        items = getattr(self._message_queue, "_queue", None)
        if items is None:
            return False
        try:
            snapshot = list(items)
        except Exception:
            return False
        for queued in snapshot:
            if not (isinstance(queued, dict)
                    and str(queued.get("message_id") or "").strip() == mid):
                continue
            changed = False
            if msg.get("is_at_bot") and not queued.get("is_at_bot"):
                queued["is_at_bot"] = True
                # The AT frame is authoritative about ownership too. The full-push copy
                # guessed ``mentions_other_user=True`` because it could not name the
                # bot among the targets; leaving that flag alone delivered a message
                # that reads as "summoned the bot AND somebody else", which consumers
                # treat as a conversation not to interrupt.
                queued["mentions_other_user"] = bool(msg.get("mentions_other_user"))
                changed = True
            for key in ("mentions_all", "quoted_message_id", "is_reply_to_bot"):
                if msg.get(key) and not queued.get(key):
                    queued[key] = msg[key]
                    changed = True
            ids = list(queued.get("mentioned_user_ids") or [])
            for who in msg.get("mentioned_user_ids") or []:
                if who not in ids:
                    ids.append(who)
                    changed = True
            if not changed:
                return False
            queued["mentioned_user_ids"] = ids
            if self.logger:
                self.logger.info(
                    f"[QQOpenPlatform] 重复投递补全点名信号 {mid!r}"
                    f"（is_at_bot={queued.get('is_at_bot')}）")
            return True
        return False

    @staticmethod
    def _is_addressed(msg: dict[str, Any]) -> bool:
        """Whether one queued message was aimed at the bot: an @ or a reply to it.

        A swipe-reply that quotes the bot is addressed traffic even though
        ``is_at_bot`` is False for it. Treating it as ordinary chatter let a burst of
        group noise evict the one message the user was waiting on.
        """
        return bool(msg.get("is_at_bot") or msg.get("is_reply_to_bot"))

    def _evict_inbound_for(self, incoming: dict[str, Any]) -> Optional[str]:
        """Free one queue slot for ``incoming``, protecting bot-addressed messages.

        Under full-message mode the queue fills with ordinary group chatter, and a blind
        drop-oldest discards whichever message happens to be oldest -- quite possibly the
        one @bot a human is waiting on. Priority, highest first:

        1. an already-queued *unaddressed* message goes, regardless of what arrives (the
           @s already queued are as precious as the new one);
        2. if every queued message is addressed to the bot:
           - the arrival is addressed too -> evict the oldest @ (someone has to go);
           - the arrival is chatter -> evict nothing, and the caller must reject it.
             Trading a queued @ for new chatter is exactly backwards.

        Returns the evicted message id ("" when unknown), or ``None`` when nothing could
        be evicted -- in which case the caller drops ``incoming``.

        Reaches into ``asyncio.Queue._queue`` because the public API cannot remove an
        arbitrary element; ``get_nowait()`` only ever yields the oldest. Both paths leave
        ``_unfinished_tasks`` untouched, so they stay equivalent on that front.
        """
        queue = self._message_queue
        items = getattr(queue, "_queue", None)
        if not isinstance(items, deque) or not items:
            # Unrecognised internals (or an empty queue that reported full): fall back
            # to the documented drop-oldest behaviour rather than raising.
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            return ""
        for index, queued in enumerate(items):
            if isinstance(queued, dict) and not self._is_addressed(queued):
                victim = items[index]
                del items[index]
                return str(victim.get("message_id") or "")
        if not (isinstance(incoming, dict) and self._is_addressed(incoming)):
            return None
        victim = items[0]
        del items[0]
        return str(victim.get("message_id") or "") if isinstance(victim, dict) else ""

    def _learn_bot_openid(self, group_id: str, openid: str) -> None:
        """Cache the bot's own group-side openid for one group.

        Openids are issued per conversation, so a single field would let one group's
        value misclassify another group's @bot as "mentioning someone else". READY only
        carries the app-level id, which never matches a group openid.
        """
        gid = str(group_id or "").strip()
        if not gid:
            return
        self._mutable("_bot_openid_by_group", dict)[gid] = str(openid or "")

    def _remembered_bot_openid(self, group_id: str) -> str:
        """The bot's openid previously learned for ``group_id`` ("" when unknown)."""
        gid = str(group_id or "").strip()
        if not gid:
            return ""
        return str(self._mutable("_bot_openid_by_group", dict).get(gid, "") or "")

    def _warn_unknown_bot_openid(self, group_id: str, event_type: str) -> None:
        """Report once per group that an @ target could not be attributed to the bot.

        Before the platform has ever flagged this bot with ``is_you``, an inline-only
        mention has no trustworthy owner. Rather than guess (a wrong guess either
        swallows the real @ or makes the bot answer messages aimed at someone else), the
        conversion reports ``False`` and this makes the situation observable.
        """
        warned = self._mutable("_openid_unattributed_warned", set)
        key = str(group_id or "").strip()
        if key in warned:
            return
        warned.add(key)
        message = (
            f"[QQOpenPlatform] 群 {key or '(未知)'} 的 @ 无法归因到本机器人："
            f"该群尚未收到过带 is_you 的提及（event={event_type}）。"
            "在平台重新下发 is_you 之前，只带内联 <@OPENID> 的消息会被判定为未 @ 本机器人。"
        )
        if self.logger:
            self.logger.warning(message)
        # ``_emit_log`` is an instance attribute installed by ``__init__``, so objects
        # built through ``__new__`` (the harnesses here) do not have it.
        emit = getattr(self, "_emit_log", None)
        if callable(emit):
            emit("WARNING", message)

    def _note_full_group_message(self, data: dict) -> None:
        """Record that one group really pushed a non-mention message."""
        gid = str(data.get("group_id") or data.get("group_openid") or "").strip()
        seen = self._mutable("_all_messages_observed_groups", set)
        if gid in seen:
            return
        seen.add(gid)
        if self.logger:
            self.logger.info(
                f"[QQOpenPlatform] 收到 GROUP_MESSAGE_CREATE：群 {gid or '(未知)'} 正在推送全量群消息"
            )

    def is_group_muted(self, group_id: str) -> bool:
        """Local mute bookkeeping, same shape as ``OneBotClient.is_group_muted``.

        The only reliable signal this channel has is the platform rejecting a send
        with ``40054002`` / ``850018``; without recording it the caller keeps sending
        into a muted group.
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
        # A group can be muted as a whole (``global_rule`` in the shut list) without
        # any per-member record. That rule was stored and never read, so ``is_group_muted``
        # answered False for a group nobody could post in, and the caller kept paying
        # for sends the platform was going to reject.
        rule = self._mutable("_shut_global_rule", dict).get(gid) or {}
        mode = str(rule.get("mode") or "").strip().lower()
        if mode in ("always", "global", "all"):
            # Fresh or stale? An admin lifting the whole-group mute sends no event, and
            # nothing else expires this record, so without a TTL the connector skipped
            # every reply to that group forever after one read.
            stamp = float(self._mutable("_shut_global_rule_at", dict).get(gid, 0) or 0)
            if time.time() - stamp > _SHUT_RULE_TTL_SECONDS:
                self._mutable("_shut_global_rule", dict).pop(gid, None)
                self._mutable("_shut_global_rule_at", dict).pop(gid, None)
                return False
            return True
        return False

    async def receive_message(self, timeout: float = 1.0) -> Optional[dict[str, Any]]:
        # Dequeue and stamp the dedup record in one uninterrupted step. Two failure
        # modes motivate this, and each is covered by a different half:
        #
        # 1. **Which asyncio helper is used.** On 3.11 ``asyncio.wait_for`` runs the
        #    awaitable in its *own* Task and hands the result back through a
        #    done-callback, so the message is already dequeued while this coroutine is
        #    still waiting to resume. The receive loop can run inside that gap and find
        #    the id neither queued (the pending scan misses) nor within its TTL (it was
        #    stamped at enqueue time), accept the redelivery, and the caller ends up
        #    with the same message twice. ``asyncio.timeout`` cancels the *current*
        #    task instead, so there is no such gap.
        # 2. **Someone reintroducing an await in between.** Keeping the pair inside
        #    ``_take_and_stamp`` makes "dequeued implies recorded" structural rather
        #    than a convention about call sites.
        async def _take_and_stamp() -> Any:
            taken = await self._message_queue.get()
            if isinstance(taken, dict):
                # Restart the dedup clock at hand-off. The record was written when the
                # message was *enqueued*, so one that waited longer than the TTL would
                # fall out of its window the instant it is delivered -- and a
                # redelivery right after would be accepted, handing it over twice.
                self._remember_inbound(taken.get("message_id"))
            return taken

        try:
            async with asyncio.timeout(timeout):
                message = await _take_and_stamp()
        except asyncio.TimeoutError:
            return None
        if isinstance(message, dict):
            # Inbound broadcast hook (optional): hand the normalized message to the
            # registered sink. Best-effort.
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

        try:
            await self._ensure_token()
        except Exception as exc:
            # The classified QQOpenPlatformAPIError used to escape here: the review
            # contract for these senders is "None means not delivered", and the caller
            # awaits them without a try, so raising lost the whole reply instead of
            # degrading it.
            if self.logger:
                self.logger.warning(f"[QQOpenPlatform] 群消息发送前 token 不可用: {exc}")
            return None
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
            data = await self._api("POST", f"/v2/groups/{group_id}/messages", payload=body)
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

        try:
            await self._ensure_token()
        except Exception as exc:
            # Same reason as the group branch above.
            if self.logger:
                self.logger.warning(f"[QQOpenPlatform] 私聊消息发送前 token 不可用: {exc}")
            return None
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
            data = await self._api("POST", f"/v2/users/{user_id}/messages", payload=body)
            msg_id = str(data.get("id") or "")
            if msg_id and record_sent:
                self.record_sent_message_id(msg_id)
            return msg_id if msg_id else None
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[QQOpenPlatform] 发送私聊失败: {e}")
            return None

    async def send_group_poke(self, group_id: str, user_id: str, **_: Any) -> bool:
        # base.py:111 pins this entry point to `-> bool` and the reply path does not guard
        # it, so raising here dropped the whole group reply over a bit of decoration.
        return False

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
        self, group_id: str, ark_obj: dict[str, Any], *,
        reply_message_id: str = "", event_id: str = "", **_: Any,
    ) -> bool:
        """Send a group card. v2 has no ark on the send side, so this builds an image-text card.

        ``supports_ark_cards`` is therefore False while ``supports_card_messages`` is
        True: callers that check the first flag alone would drop a card they can
        actually deliver. Both the NapCat-shaped ``{"ark": {"kv": [{"key": "#TITLE#"...}]}}``
        input and a plain ``{"title": ..., "description": ...}`` are accepted, because
        the qq_auto_reply plugin still assembles the former.
        """
        payload: dict[str, Any] = {
            "msg_type": 8, "card": self._card_message_payload(ark_obj)}
        # Keep the passive-reply anchors. The card build replaced the whole body, so
        # ``msg_id`` / ``msg_seq`` / ``event_id`` carried in ark_obj (or passed as
        # keywords) were dropped and the card went out as a proactive send: it then
        # either consumed the active-message quota or was refused outright in a group
        # that disabled proactive messages (r4576712126).
        anchor = str(reply_message_id or ark_obj.get("msg_id") or "").strip()
        if anchor:
            payload["msg_id"] = anchor
            seq = _to_int(ark_obj.get("msg_seq"))
            payload["msg_seq"] = seq if seq > 0 else self._next_msg_seq(anchor)
        else:
            ev = str(event_id or ark_obj.get("event_id") or "").strip()
            if ev:
                payload["event_id"] = ev
        try:
            data = await self._api(
                "POST", f"/v2/groups/{group_id}/messages", payload=payload)
            # Record it like every other send. The id is what quote detection matches
            # against, and this method hands back only a bool, so nobody else can
            # register it: without this line a user quoting a bot-sent card reads as
            # ordinary chatter and loses both the reply signal and the queue priority.
            mid = str(data.get("id") or "")
            if mid:
                self.record_sent_message_id(mid)
            return bool(mid)
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
            data = resp.json() if isinstance(resp.json(), dict) else {}
            token = str(data.get("access_token") or "")
            if not token:
                # A parseable body is not a token. Accepting the error shape here set
                # ``_access_token`` to "" and pushed the expiry two hours out, so every
                # call for that window carried an empty Authorization header and the
                # connector looked connected while nothing could send.
                raise QQOpenPlatformAPIError(
                    method="POST", path="/token",
                    err_code=_to_int(data.get("err_code") or data.get("code")),
                    message=f"token endpoint returned no access_token: {str(data)[:200]}")
            self._access_token = token
            expires_in = int(data.get("expires_in") or 7200)
            self._token_expires_at = time.time() + max(60, expires_in) - 300
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
        # Through _api, like every other v2 call: the raw form had no err_code check,
        # no trace_id and no token retry, so a permission or rate failure on the
        # lifecycle path surfaced as a bare transport error.
        data = await self._api("GET", "/gateway/bot")
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
        # v2 attaches a swipe-reply's target as ``message_reference``; a quoted id in
        # our recorded sends means the user answered the bot. No get_msg fallback:
        # unlike OneBotClient._is_reply_to_bot_message, v2 has no by-id endpoint.
        reference = data.get("message_reference")
        quoted_message_id = (
            str(reference.get("message_id") or "") if isinstance(reference, dict) else "")
        is_reply_to_bot = quoted_message_id in self.sent_message_ids

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
                "is_reply_to_bot": is_reply_to_bot,
                "group_id": "",
                "quoted_message_id": quoted_message_id,
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
            #: @ targets found in the body, and those the ``mentions`` array explains.
            #: Kept apart so the unattributed-@ diagnostic fires only for targets the
            #: platform left unaccounted for.
            inline_ids: list[str] = []
            structured_ids: list[str] = []
            mentions_all = False
            #: Whether the payload itself says the bot was mentioned. This is the
            #: authoritative signal: the platform marks each mention with
            #: ``is_you: true`` for the receiving bot.
            #:
            #: Do **not** compare ``self_id`` against mention ids: ``self_id`` is the
            #: app-level id while mentions carry per-group **openids**, and they never
            #: match. That mismatch is exactly why @bot was dropped under full mode.
            at_bot_by_payload = False
            # Two mention shapes exist in the wild: the documented v2 form
            # ``<@member_openid>`` (hex) and the legacy ``<@!uin>`` that older payloads
            # and the OneBot side still produce.
            #
            # Deduplicated: a user can @ the same account twice, and counting
            # *occurrences* instead of *distinct targets* would make "summoned only me,
            # twice" look like "named me and someone else".
            for who in _AT_OPENID_RE.findall(content) + _AT_LEGACY_RE.findall(content):
                if who not in inline_ids:
                    inline_ids.append(who)
                if who not in mentioned_ids:
                    mentioned_ids.append(who)
            for item in data.get("mentions") or []:
                if isinstance(item, dict):
                    # ``member_openid`` first, matching ``_GROUP_ACTOR_ID_KEYS`` and the
                    # inline markers: the body's ``<@OPENID>`` carries the *member*
                    # openid, so that is the value the learned cache must hold. Reading
                    # ``id`` first would cache a different field and make later
                    # inline-only payloads miss the learned openid.
                    who = str(
                        item.get("member_openid") or item.get("openid") or item.get("id") or ""
                    ).strip()
                    if who:
                        if who not in structured_ids:
                            structured_ids.append(who)
                        if who not in mentioned_ids:
                            mentioned_ids.append(who)
                    # Only ``is_you`` means "this mention is the receiving bot".
                    # ``bot`` merely says the *target* is some bot: in a group with
                    # several bots, @-ing a neighbour carries ``bot: true`` and no
                    # ``is_you`` -- treating that as self would make this bot answer
                    # messages addressed to it.
                    if item.get("is_you") is True:
                        at_bot_by_payload = True
                        if who:
                            # Cache per group: openids are issued per conversation.
                            self._learn_bot_openid(group_id, who)
                elif isinstance(item, str) and item.strip():
                    if item.strip() not in mentioned_ids:
                        mentioned_ids.append(item.strip())
            # No fallback learning here. The v2 event page for
            # GROUP_AT_MESSAGE_CREATE defines ``mentions`` as the list of users @-ed
            # *excluding the bot itself*, and ``content`` as the text with the bot's own
            # @ prefix already removed. So every target left in an AT frame is somebody
            # else, and caching the sole target as this bot's openid would do more
            # damage than the miss it tried to fix: the bot would answer other people's
            # conversations, real summons would read is_at_bot=False, and because a
            # learned id also disarms the attribution hold and the one-shot diagnostic,
            # the wrong answer would arrive with no log at all.
            #
            # Two sources remain, and both are honest: an ``is_you`` mention item, and
            # ``bot_state``'s ``member_openid``. If the platform never sends is_you,
            # attribution comes from bot_state probes, not from guessing.
            clean_content = _AT_OPENID_RE.sub("", _AT_LEGACY_RE.sub("", content))
            clean_content = clean_content.replace("<@all>", "").strip()
            mentions_all = "<@all>" in content
            bot_openid = self._remembered_bot_openid(group_id)
            # Which @ targets are the bot itself? ``self_id`` is the app-level id and
            # only matches legacy payloads; ``bot_openid`` is the per-group openid the
            # current protocol actually emits.
            bot_ids = {str(self._self_id or ""), str(bot_openid or "")}
            bot_ids.discard("")
            if bot_ids & set(mentioned_ids):
                # The bot can be named in this list -- judge precisely.
                mentions_other_user = any(mid not in bot_ids for mid in mentioned_ids)
            elif event_type == "GROUP_AT_MESSAGE_CREATE":
                # The bot is not in this list by definition (see the note above), so any
                # surviving target is somebody else. "len > 1" was wrong for the common
                # "@bot @alice" case: one target left, and it is a human.
                mentions_other_user = any(mid not in bot_ids for mid in mentioned_ids)
            else:
                # Full-message event with no known bot openid: any @ target could be
                # someone else. Erring towards True keeps the bot from butting into
                # conversations aimed at other people.
                mentions_other_user = bool(mentioned_ids)

            # is_at_bot resolution order (first hit wins):
            #   1. GROUP_AT_MESSAGE_CREATE -- the platform only sends it when @bot;
            #   2. the payload's own ``is_you`` mention flag;
            #   3. a mention id matching this group's learned bot openid (payloads that
            #      carry inline ``<@OPENID>`` markers and no mentions array).
            #
            # Step 3 needs an openid learned earlier from step 2, so it cannot fire on
            # the very first inline-only payload of a group. That is acceptable while
            # the platform keeps sending ``is_you``, but it would degrade silently if
            # it ever stopped -- hence the diagnostic below.
            if event_type == "GROUP_AT_MESSAGE_CREATE":
                is_at_bot = True
            elif at_bot_by_payload:
                is_at_bot = True
            else:
                is_at_bot = bool(bot_openid and bot_openid in mentioned_ids) or any(
                    f"<@{sid}>" in content or f"<@!{sid}>" in content
                    for sid in bot_ids
                )
                if not bot_openid:
                    # Only warn about @ targets the platform did NOT account for. If
                    # ``mentions`` already describes every inline marker, the payload is
                    # complete and the absence of ``is_you`` simply means the bot was
                    # not summoned -- @-ing someone else must not consume this group's
                    # one-shot diagnostic, or a later genuinely unattributable @bot
                    # would go unreported.
                    unexplained = [mid for mid in inline_ids if mid not in structured_ids]
                    if unexplained:
                        self._warn_unknown_bot_openid(group_id, event_type)

            return {
                "message_type": "group",
                "channel": self.CHANNEL,
                "user_id": pick_actor_id(author, _GROUP_ACTOR_ID_KEYS),
                "user_nickname": user_nickname,
                "content": clean_content,
                "message_id": str(data.get("id") or ""),
                "timestamp": int(time.time()),
                "is_at_bot": is_at_bot,
                "is_reply_to_bot": is_reply_to_bot,
                "group_id": group_id,
                "quoted_message_id": quoted_message_id,
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
        ``message``), and success may carry no body at all -- any 2xx (a 200 with an
        empty body, or a 204) is returned as ``{}`` rather than treated as an error.
        """
        from urllib.parse import urlencode

        try:
            await self._ensure_token()
        except Exception as exc:
            # The token calls used to sit outside the guard, so a token failure left
            # _api as a raw httpx error. Callers that degrade (media send returns None
            # and the reply falls back to text) only work when the error is the type
            # they catch.
            if isinstance(exc, QQOpenPlatformAPIError):
                raise
            raise QQOpenPlatformAPIError(
                method=method, path=path, message=f"token unavailable: {exc}") from exc
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
        if 200 <= status < 300 and not err_code:
            return body
        if err_code == ERR_TOKEN_INVALID and retry_on_token:
            # One refresher at a time, and the old value is replaced rather than
            # blanked: two concurrent sends that both saw 40011027 used to clear the
            # shared token and then fire, so the second one posted an empty
            # Authorization header.
            async with self._mutable("_token_lock", asyncio.Lock):
                self._token_expires_at = 0
                try:
                    await self._refresh_token()
                except QQOpenPlatformAPIError:
                    raise
                except Exception as exc:
                    # The first token call of _api is wrapped; this one was not. A
                    # transport or JSON failure here escaped as a raw httpx error, and
                    # the media senders catch only the classified type, so a public
                    # voice send raised instead of answering its documented None.
                    raise QQOpenPlatformAPIError(
                        method=method, path=path,
                        message=f"token refresh failed: {exc}") from exc
            return await self._api(
                method, path, params=params, payload=payload, retry_on_token=False)
        if err_code in ERR_GROUP_MUTED:
            gid = _group_id_of(path)
            if gid:
                # Without this the caller keeps sending into a muted group and learns
                # nothing except a rejected send, every round.
                self._mutable("_group_muted", dict)[gid] = time.time() + 300
        raise QQOpenPlatformAPIError(
            method=method, path=path, http_status=status, err_code=err_code,
            message=str(body.get("message") or ""), trace_id=str(body.get("trace_id") or ""))

    async def _upload_media(
        self, *, scope: str, owner_id: str, source: str, file_type: int,
        file_name: str = "",
    ) -> str:
        """Upload one rich-media item and return ``file_info`` (``""`` when it failed).

        Three documented shapes: URL hand-off, inline binary (``file_data``, up to
        5 MB), and the chunked protocol (``upload_prepare`` / ``upload_part_finish``)
        for anything larger. ``_media_upload_chunked`` comes from the media mixin, which
        already used it for images; the local branch was the one that stopped at 5 MB.
        """
        source = str(source or "").strip()
        if not source:
            return ""
        endpoint = f"/v2/{scope}/{owner_id}/files"
        if source.startswith(("http://", "https://")):
            body: dict[str, Any] = {
                "file_type": int(file_type), "url": source, "srv_send_msg": False}
        else:
            if source.startswith(_BASE64_PREFIX):
                encoded = source[len(_BASE64_PREFIX):]
                # Reuse the mixin's measure instead of len()*3//4. The rough form
                # counted the ``=`` padding and any embedded newlines as payload, so an
                # exactly-at-limit file measured one byte over and was refused, and a
                # line-wrapped encoding refused files even further below the ceiling
                # (r4574585087).
                predicted = _base64_decoded_size(encoded)
                if predicted > UPLOAD_HARD_MAX_BYTES:
                    # Refuse on the encoded length, before decoding. Decoding first to
                    # measure the result allocates the whole payload plus its
                    # intermediate copies only to throw it away, and concurrent sends
                    # turn that into memory pressure. shortcut: the file name comes
                    # from the image magic table; extend it per file_type when a
                    # caller appears.
                    if self.logger:
                        self.logger.warning(
                            f"[QQOpenPlatform] base64 载荷约 {predicted} 字节，超过 "
                            f"{UPLOAD_HARD_MAX_BYTES} 上限，未解码即拒绝")
                    return ""
                decoded = await asyncio.to_thread(_decode_base64_source, encoded)
                raw = decoded.payload
                # An explicit name wins over the magic-table guess: base64 upload of
                # report.pdf came out named image.png, and the platform stores that
                # name in the file metadata the recipient then sees.
                file_name = file_name or decoded.file_name
            else:
                path = _local_path(source)
                file_name = file_name or path.replace("\\", "/").rsplit("/", 1)[-1]
                try:
                    # Capped: the old version read the whole file first and checked the
                    # size after, so a 2 GB path put 2 GB in memory to be refused.
                    raw = await asyncio.to_thread(_read_bytes, path, UPLOAD_HARD_MAX_BYTES)
                except Exception as exc:
                    if self.logger:
                        self.logger.warning(f"[QQOpenPlatform] 富媒体读取失败: {exc}")
                    return ""
            if not raw:
                if self.logger:
                    self.logger.warning(f"[QQOpenPlatform] 富媒体内容为空或读不到: {_brief(source)}")
                return ""
            if len(raw) > UPLOAD_HARD_MAX_BYTES:
                if self.logger:
                    self.logger.warning(
                        f"[QQOpenPlatform] 富媒体超过 {UPLOAD_HARD_MAX_BYTES} 字节硬上限，"
                        f"拒绝上传: {_brief(source)}")
                return ""
            if len(raw) > INLINE_UPLOAD_MAX_BYTES:
                # The inline form (``file_data``) tops out at 5 MB, but the platform
                # also documents a chunked protocol, which the media mixin already
                # implements for images. Routing voice and file there is what makes a
                # minute of audio sendable at all: one minute of 44.1 kHz stereo WAV is
                # about 10 MB, and refusing it was the whole reason the voice feature
                # looked dead on a real recording.
                try:
                    await self._ensure_token()
                    # digests in a thread: the mixin computes MD5 + SHA1 over the
                    # whole payload itself when the caller does not pass them, which
                    # for a 200 MB file is a multi-second stall of the one event loop
                    # every other channel shares.
                    digests = await asyncio.to_thread(_digests, raw)
                    return await self._media_upload_chunked(
                        scope=scope, owner_id=owner_id, payload=raw,
                        file_name=file_name, file_type=int(file_type),
                        digests=digests)
                except Exception as exc:
                    # Broad on purpose, and only for this branch. The chunked protocol
                    # lives in the media mixin and posts through ``_media_post``, which
                    # raises ``httpx.HTTPStatusError`` on a non-2xx answer instead of
                    # the classified ``QQOpenPlatformAPIError`` this method otherwise
                    # catches, and it never touched the token at all. Letting either
                    # out would break the documented degrade: the caller expects
                    # ``""`` / ``None`` and falls back to text.
                    if self.logger:
                        self.logger.warning(f"[QQOpenPlatform] 分片上传失败: {exc}")
                    return ""
            body = {
                "file_type": int(file_type),
                "file_name": file_name,
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
        reply_message_id: str = "", record_sent: bool = True, file_name: str = "",
    ) -> Optional[str]:
        file_info = await self._upload_media(
            scope=scope, owner_id=owner_id, source=source, file_type=file_type,
            file_name=file_name)
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
        # The platform rejects plain-http card links, so the scheme gets upgraded --
        # by *replacing* an existing one. Prefixing instead produced
        # "https://http://a.example/x": the card rendered, the link went nowhere.
        for key in ("url", "pic_url"):
            content[key] = _https_link(content[key])
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

    def _missing_argument(self, operation: str, missing: str, *, empty: type = dict):
        """Refuse one operation whose target id did not arrive, out loud.

        The pattern this replaces was ``return {}`` with no comment: an empty dict does
        not raise, so a caller that only checks for an exception reads it as success.
        That is the same silent-success shape this change set went to remove, moved
        from "unsupported operation" to "caller passed nothing to act on".

        Returning the empty container keeps the documented return type and the caller's
        ``.get(...)`` working; the log line is what was missing.
        """
        if self.logger:
            self.logger.warning(
                f"[QQOpenPlatform] {operation} 缺少参数 {missing}，未执行任何操作")
        return empty()

    async def get_group_info(self, group_id: str = "", **_: Any) -> dict:
        gid = str(group_id or "").strip()
        if not gid:
            return self._missing_argument("get_group_info", "group_id", empty=dict)
        return await self._api("GET", f"/v2/groups/{gid}/info")

    async def get_group_bot_state(self, group_id: str, *, force: bool = False) -> dict:
        """Read this group's reception setting, the bot's role and the push permission.

        This is what makes :attr:`receives_all_messages` answerable before any traffic
        arrives. The interface is permission-gated, so the caller must tolerate the
        error and fall back to observed events.
        """
        gid = str(group_id or "").strip()
        if not gid:
            return self._missing_argument("get_group_bot_state", "group_id", empty=dict)
        state = await self._api("GET", f"/v2/groups/{gid}/bot_state")
        # Per group: the reception setting, the role and the group-side openid are all
        # conversation facts. Storing them connection-wide let one group's admin role
        # or openid decide another group's judgement.
        self._mutable("_recv_msg_setting_by_group", dict)[gid] = str(state.get("recv_msg_setting") or "")
        self._mutable("_bot_member_role_by_group", dict)[gid] = str(state.get("member_role") or "")
        # Keep an earlier self-detected id when this interface is not entitled to
        # answer with one.
        learned = str(state.get("member_openid") or "").strip()
        if learned:
            self._learn_bot_openid(gid, learned)
        # bot_state is the authoritative answer for proactive sending too. Writing it
        # here keeps group_proactive_allowed() from returning a stale value that an
        # older notice set, or None right after a probe that did answer. An absent key
        # stays unknown rather than defaulting to allowed.
        if "allow_proactive_msg" in state:
            self._mutable("_proactive_allowed_by_group", dict)[gid] = bool(
                state.get("allow_proactive_msg"))
        return state

    async def get_group_member_info(self, group_id: str = "", user_id: str = "", **_: Any) -> dict:
        gid = str(group_id or "").strip()
        member = str(user_id or "").strip()
        if not gid or not member:
            return self._missing_argument("get_group_member_info", "group_id/user_id", empty=dict)
        return await self._api("GET", f"/v2/groups/{gid}/members/{member}")

    async def get_group_member_list(self, group_id: str = "", **kw: Any) -> list:
        """Page through members (30 at a time) until the cursor runs out or max_pages hits."""
        gid = str(group_id or "").strip()
        if not gid:
            return self._missing_argument("get_group_member_list", "group_id", empty=list)
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
            return self._missing_argument("set_group_ban", "group_id/user_id", empty=dict)
        seconds = _to_int(duration)
        op = "del"
        if seconds > 0:
            # ``add`` is rejected for a member who is already muted; retiming
            # one is an ``update``. A failed pre-read must not cost the mute
            # itself: fall back to ``add`` (the pre-update behavior); a dead
            # transport still raises from the POST below.
            try:
                muted = {str(m.get("member_openid") or "")
                         for m in await self.get_group_shut_list(gid)
                         if isinstance(m, dict)}
                op = "update" if member in muted else "add"
            except Exception:
                op = "add"
        entry = {
            "op": op,
            "member_openid": member,
            # The platform takes an expiry timestamp, not a duration.
            "mute_expire_at": "" if seconds <= 0 else _rfc3339_in(seconds)}
        await self._api(
            "POST", f"/v2/groups/{gid}/restrict_chat_setting", payload={"members": [entry]})
        return {"status": "ok"}

    async def get_group_shut_list(self, group_id: str = "", **_: Any) -> list:
        gid = str(group_id or "").strip()
        data = await self._api("GET", f"/v2/groups/{gid}/restrict_chat_setting")
        rule = data.get("global_rule")
        self._mutable("_shut_global_rule_at", dict)[gid] = time.time()
        self._mutable("_shut_global_rule", dict)[gid] = (
            rule if isinstance(rule, dict) else {})
        members = data.get("members")
        return members if isinstance(members, list) else []

    def group_shut_global_rule(self, group_id: str) -> dict:
        """Last observed global rule (``mode``: none/always/schedule) for one group.

        With a mode other than ``none`` everyone -- the bot included -- is muted,
        so an empty :meth:`get_group_shut_list` does not mean sends will pass.
        """
        return dict(
            self._mutable("_shut_global_rule", dict).get(str(group_id or "").strip()) or {})

    async def set_group_kick(self, group_id: str = "", user_id: str = "", **kw: Any) -> dict:
        gid = str(group_id or "").strip()
        member = str(user_id or "").strip()
        if not gid or not member:
            return self._missing_argument("set_group_kick", "group_id/user_id", empty=dict)
        body: dict[str, Any] = {"member_openids": [member]}
        if kw.get("add_to_member_blacklist") or kw.get("reject_add_request"):
            body["add_to_member_blacklist"] = True
        return await self._api("POST", f"/v2/groups/{gid}/batch_remove_members", payload=body)

    async def list_group_join_requests(self, group_id: str = "", cursor: str = "", limit: int = 20, **_: Any) -> dict:
        gid = str(group_id or "").strip()
        if not gid:
            return self._missing_argument("list_group_join_requests", "group_id", empty=dict)
        return await self._api(
            "GET", f"/v2/groups/{gid}/join_request_list",
            params={"cursor": cursor, "limit": max(1, min(_to_int(limit) or 20, 50))})

    async def set_group_add_request(
        self, flag: str = "", sub_type: str = "", approve: bool = True,
        reason: str = "", **kw: Any,
    ) -> dict:
        """Approve or decline one join request; ``list_group_join_requests`` supplies the ids.

        ``sub_type`` is not a v2 concept. It stays in the signature in the OneBot
        position because callers written against that connector pass it positionally,
        and dropping it would bind ``"add"`` to ``approve`` -- a truthy string -- so a
        DECLINE request would silently APPROVE the applicant.

        Join-request *events* need the GROUP_MEMBER_EVENT intent, off unless
        ``extra_intents`` opts in, so approvals are pulled rather than pushed.
        """
        gid = str(kw.get("group_id") or kw.get("group_openid") or "").strip()
        member = str(kw.get("user_id") or kw.get("member_openid") or "").strip()
        if not gid or not member:
            return self._missing_argument("set_group_add_request", "group_openid/user_id", empty=dict)
        body: dict[str, Any] = {
            "op": "approve" if approve else "decline", "join_request_id": str(flag or "")}
        if not approve:
            if reason:
                body["reject_reason"] = str(reason)[:100]
            if kw.get("add_to_member_blacklist"):
                body["add_to_member_blacklist"] = True
        await self._api("POST", f"/v2/groups/{gid}/approval_join_request/{member}", payload=body)
        return {"status": "ok"}

    async def operate_group_member_blacklist(
        self, group_id: str, member_openids: list, *, op: str = "add") -> dict:
        gid = str(group_id or "").strip()
        ids = [str(x).strip() for x in (member_openids or []) if str(x).strip()]
        if not gid or not ids or op not in ("add", "del"):
            return self._missing_argument("operate_group_member_blacklist", "group_id/member_openids/op", empty=dict)
        # The interface takes at most 20 openids per call. Truncating here answered the
        # caller's 21st member as if it had been handled: the send succeeded, so
        # nothing upstream retried, and the extra members quietly stayed off the
        # blacklist (r4574519956). Batch instead, and return the last batch's answer
        # with the count actually processed.
        if len(ids) <= _BLACKLIST_BATCH:
            return await self._api(
                "POST", f"/v2/groups/{gid}/blacklist",
                payload={"op": op, "member_openids": ids})
        result: dict[str, Any] = {}
        for start in range(0, len(ids), _BLACKLIST_BATCH):
            chunk = ids[start:start + _BLACKLIST_BATCH]
            result = await self._api(
                "POST", f"/v2/groups/{gid}/blacklist",
                payload={"op": op, "member_openids": chunk})
        result["processed"] = len(ids)
        result["batches"] = (len(ids) + _BLACKLIST_BATCH - 1) // _BLACKLIST_BATCH
        return result
        return await self._api(
            "POST", f"/v2/groups/{gid}/member_blacklist", payload={"op": op, "member_openids": ids})

    async def list_group_member_blacklist(self, group_id: str, cursor: str = "") -> dict:
        gid = str(group_id or "").strip()
        if not gid:
            return self._missing_argument("list_group_member_blacklist", "group_id", empty=dict)
        return await self._api("GET", f"/v2/groups/{gid}/member_blacklist", params={"cursor": cursor})

    async def upload_group_file(self, file: str = "", name: str = "", **kw: Any) -> dict:
        """Send a group file (``file_type=4``). Upload-only: v2 has no file management."""
        gid = str(kw.get("group_id") or kw.get("group_openid") or "").strip()
        if not gid:
            return self._missing_argument("upload_group_file", "group_id", empty=dict)
        mid = await self._send_media(
            scope="groups", owner_id=gid, file_type=FILE_TYPE_FILE,
            source=str(file or ""), file_name=str(name or ""))
        return {"file_id": mid, "id": mid} if mid else {}

    async def upload_private_file(self, file: str = "", name: str = "", **kw: Any) -> dict:
        uid = str(kw.get("user_id") or kw.get("openid") or "").strip()
        if not uid:
            return self._missing_argument("upload_private_file", "user_id", empty=dict)
        mid = await self._send_media(
            scope="users", owner_id=uid, file_type=FILE_TYPE_FILE,
            source=str(file or ""), file_name=str(name or ""))
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
            return self._missing_argument("respond_interaction", "interaction_id", empty=dict)
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
        a QQ group number. ``group_name`` is empty: no push carries it, and
        :meth:`get_group_info` resolves it one call per group.
        """
        seen = self._mutable("_seen_groups", dict)
        now = time.time()
        # Read-time expiry: the record-time sweep never runs when no message
        # arrives, so stale ids would otherwise linger here.
        live = [gid for gid, ts in seen.items() if now - ts <= _SEEN_GROUPS_TTL]
        for gid in set(seen) - set(live):
            seen.pop(gid, None)
        return [{"group_id": gid, "group_name": ""} for gid in sorted(live)]

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


def _https_link(value: Any) -> str:
    """Force one card link onto https, replacing an existing scheme.

    The platform refuses plain-http card links, so a caller that hands over
    ``http://a.example/x`` still wants a working card. Three rules, each learned from a
    way the old prefixing code produced a link that went nowhere:

    - an existing ``http:`` / ``https:`` scheme is rewritten, never stacked, so
      ``http://a`` gives ``https://a`` and not ``https://http://a``;
    - a protocol-relative ``//host/path`` gets https, because the caller left the
      scheme out on purpose rather than naming a wrong one;
    - any other scheme (``javascript:``, ``data:``, a bare word) yields ``""``: the
      platform rejects those anyway, and an empty field degrades the card while a
      stacked string pretends the link works.
    """
    link = str(value or "").strip()
    if not link:
        return ""
    lowered = link.lower()
    if lowered.startswith("https://"):
        return link
    if lowered.startswith("http://"):
        return "https://" + link[len("http://"):]
    if link.startswith("//"):
        return "https:" + link
    head = link.split("/", 1)[0]
    if not head or ":" in head:
        # Either an absolute path with no host at all (``/a/b``, nothing to prepend a
        # host to), or some other scheme (``javascript:``, ``data:``, ``ftp:``) or a
        # bare host:port. The platform refuses the schemes outright, so these become
        # empty: the card degrades to "no jump target", which is visible, instead of a
        # stacked string that looks like it works.
        return ""
    return "https://" + link


def _read_bytes(path: str, limit: int = 0) -> bytes:
    """Blocking read; callers run it in a thread so a slow disk cannot stall the loop.

    ``limit`` caps how much is taken. Without a cap a caller could hand over a 2 GB file
    and the whole thing would land in memory *before* the size check that rejects it --
    the refusal never protected anything. One extra byte is read so the caller can tell
    "exactly at the limit" from "over the limit".
    """
    if not path or not os.path.isfile(path):
        return b""
    with open(path, "rb") as handle:
        return handle.read(limit + 1) if limit > 0 else handle.read()


def _rfc3339_in(seconds: int) -> str:
    """RFC3339 timestamp ``seconds`` from now, in the platform's own +08:00 frame."""
    from datetime import datetime, timedelta, timezone

    moment = datetime.now(timezone(timedelta(hours=8))) + timedelta(seconds=int(seconds))
    return moment.replace(microsecond=0).isoformat()
