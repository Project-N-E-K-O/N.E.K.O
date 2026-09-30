"""Platform-neutral connection layer: the abstract base, the inbound message
shape, and the connector Protocol consumers type against.

Nothing here knows about a protocol or a platform. ``utils.connection.onebot``
implements the OneBot v11 transport on top of it; ``utils.connection.qq`` holds
the QQ-specific pieces (the QQ Open Platform connection and the factory that
reads the QQ settings keys).
"""

from __future__ import annotations

import asyncio
import collections
from abc import ABC, abstractmethod
from typing import Any, Literal, Optional, Protocol, TypedDict, runtime_checkable


class InboundMessage(TypedDict, total=False):
    """The normalized dict every ``receive_message()`` returns.

    Documents the shape both implementations produce today; it is a type hint
    only and nothing validates it at runtime. Where the two implementations
    disagree the field is typed loosely and the difference is noted.
    """

    message_type: Literal["group", "private", "notice"]
    #: Observed transport ("onebot" | "open"), stamped at ingest.
    channel: str
    user_id: str
    user_nickname: Optional[str]
    content: str
    #: OneBotClient passes the implementation's value through (usually int);
    #: the QQ Open Platform connection uses str.
    message_id: Any
    #: OneBotClient: the event's ``time``; QQ Open Platform: receive time.
    timestamp: Any
    is_at_bot: bool
    is_reply_to_bot: bool
    #: Group messages only ("" on QQ Open Platform private messages).
    group_id: str
    quoted_message_id: str
    #: OneBotClient only.
    quoted_sender_id: str
    mentioned_user_ids: list[str]
    mentions_other_user: bool
    mentions_all: bool
    raw: dict[str, Any]
    attachments: list[dict[str, Any]]
    #: Notice messages only: ``"poke"`` or ``"group_ban"`` (OneBotClient). Consumers
    #: must branch on this, not on ``message_type == "notice"`` alone.
    notice_type: str
    #: Notice messages only (poke: the poked user).
    target_id: str
    #: Notice messages only: the raw event's sub_type (poke: ``"poke"``; group_ban:
    #: ``"ban"`` / ``"lift_ban"``).
    sub_type: str
    #: Notice messages only (group_ban: who issued it; "" when absent).
    operator_id: str
    #: Notice messages only (group_ban: seconds, 0 on lift_ban or when unparsable).
    duration: int


class ConnectionBase(ABC):
    """Abstract base for every connection type.

    Both NapCat (OneBot) and the QQ Open Platform implement this interface and
    output a unified internal message format to the consuming plugin.

    ``receive_message()`` returns an :class:`InboundMessage`. OneBotClient also
    yields notices as ``{"message_type": "notice", "notice_type", "sub_type",
    "user_id", "operator_id", "duration", "group_id", "target_id", "content": "",
    "timestamp", "raw", "channel"}``, where ``notice_type`` is ``"poke"`` or --
    only after the consumer sets ``forward_group_ban_notices = True`` on the client --
    ``"group_ban"`` (a ban / lift-ban aimed at a **third party** by someone other than
    the bot; the bot's own and whole-group mutes are only tracked internally). Notices
    carry no ``sender`` or ``message_id``, so a consumer must branch on
    ``notice_type``, not on ``message_type == "notice"`` alone. The inbound sink gets
    notices only when it was registered with ``include_notices=True``.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Establish the connection (WebSocket + auth + heartbeat)."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect and clean up resources."""
        ...

    @abstractmethod
    async def receive_message(self, timeout: float = 1.0) -> Optional[dict[str, Any]]:
        """Blocking-receive one message; returns the normalized dict or None on timeout."""
        ...

    @abstractmethod
    async def send_group_message_segments(
        self, group_id: str, segments: list[dict[str, Any]], *, record_sent: bool = True
    ) -> Optional[str]:
        """Send a group message (platform-native format); returns the message_id."""
        ...

    @abstractmethod
    async def send_private_message_segments(
        self, user_id: str, segments: list[dict[str, Any]]
    ) -> Optional[str]:
        """Send a private message (platform-native format); returns the message_id."""
        ...

    @abstractmethod
    async def send_group_poke(self, group_id: str, user_id: str) -> bool:
        """Send a group poke; returns whether it succeeded."""
        ...

    @abstractmethod
    async def send_group_image(
        self, group_id: str, image_data: str, *, reply_message_id: str = "", at_user_id: str = "", sub_type: str = ""
    ) -> Optional[str]:
        """Send a group image."""
        ...

    @abstractmethod
    async def send_group_record(
        self, group_id: str, file_uri: str, *, reply_message_id: str = "", at_user_id: str = ""
    ) -> Optional[str]:
        """Send a group voice message."""
        ...

    @abstractmethod
    async def get_login_status(self) -> dict[str, Any]:
        """Return login status: {"status": "online"|"offline", "self_id": str|None, "nickname": str|None}"""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the connection is established."""
        ...

    @abstractmethod
    def record_sent_message_id(self, message_id: str) -> None:
        """Record a sent message id (used by is_reply_to_bot detection)."""
        ...

    token: str = ""  # access token (the consuming plugin's settings code reads it directly)

    @property
    def needs_attention(self) -> bool:
        """Whether the attention mechanism is needed (NapCat yes, Open Platform no)."""
        return True

    @property
    def supports_voice(self) -> bool:
        """Whether voice replies are supported."""
        return True

    @property
    def supports_poke(self) -> bool:
        """Whether poke is supported."""
        return True

    @property
    def receives_all_messages(self) -> bool:
        """Whether all group messages are received (Open Platform only gets @bot)."""
        return True

    @property
    def supports_ark_cards(self) -> bool:
        """Whether Ark rich cards are supported (Open Platform only; OneBot degrades to text)."""
        return False

    def is_group_muted(self, group_id: str) -> bool:
        """Check whether the bot is muted in this group (incl. whole-group mute).

        NapCat tracks mute state via OneBot notice events; the Open Platform does
        not track it and returns False by default.
        """
        return False

    @property
    def self_id(self) -> str:
        """The bot's own user id ("" when unknown). Public alias for ``_self_id``."""
        return str(getattr(self, "_self_id", "") or "")

    @property
    def sent_message_ids(self) -> dict[str, float]:
        """Sent message id -> sent timestamp. Public alias for ``_sent_message_ids``."""
        return getattr(self, "_sent_message_ids", {})

    async def send_group_ark_card(
        self, group_id: str, ark_obj: dict[str, Any], **_: Any
    ) -> bool:
        """Send a group Ark rich card. Open Platform only (``supports_ark_cards`` True);
        OneBot backends degrade to text before reaching this method, so it raises
        NotImplementedError as a fallback."""
        raise NotImplementedError("Ark rich cards are supported only on the Open Platform channel")

    # ── inbound broadcast hook (adapter -> subscribers) ────────
    # Any plugin may attach a sink via ``set_inbound_sink``: the connection layer
    # calls it once per normalized inbound message. qq_auto_reply uses it to push
    # inbound QQ messages to other plugins; a plugin that owns its own connection
    # can also attach its own sink. Never blocks the message pipeline (best-effort).
    # The sink is invoked from ``receive_message()``; the connection does not drain
    # its queue on its own, so the owner must keep calling ``receive_message()``
    # (the queue holds 100 messages and drops the oldest when full).
    _INBOUND_SINK_ATTR = "_inbound_sink"
    _INBOUND_SINK_NOTICES_ATTR = "_inbound_sink_notices"
    #: Cap on the inbound-sink task set; when full, cancel and drop the oldest.
    #: Same drop-oldest semantics as the SSE channel -- otherwise a slow or never-finishing sink grows the unfinished-task set with the message rate and takes the connector process down.
    _INBOUND_SINK_MAX_BACKLOG = 100

    @property
    def inbound_sink(self) -> Any | None:
        """The registered inbound sink (None = not registered)."""
        return getattr(self, self._INBOUND_SINK_ATTR, None)

    def set_inbound_sink(self, sink: Any | None, *, include_notices: bool = False) -> None:
        """Register an inbound sink ``async sink(message: dict) -> None``.

        After each ``receive_message()`` yields a normalized message, the connection
        layer calls it and swallows every exception (the broadcast is best-effort and
        must never stall the pipeline). Pass ``None`` to unregister.

        Notices (``message_type == "notice"``: pokes, third-party bans) are delivered
        only with ``include_notices=True``. A sink registered the old way keeps
        receiving chat messages only: notices carry no sender, message id or text, and
        a subscriber that treats every delivery as a chat message would record them as
        empty messages.

        Registering a sink does not start delivery by itself: it only fires while
        someone keeps calling ``receive_message()``.
        """
        setattr(self, self._INBOUND_SINK_ATTR, sink)
        setattr(self, self._INBOUND_SINK_NOTICES_ATTR, bool(include_notices) and sink is not None)

    async def _dispatch_inbound(self, message: dict[str, Any]) -> None:
        """Internal: hand one inbound message to the registered sink.

        Fire-and-forget and fault-tolerant: the sink runs on an independent task,
        so a slow or never-completing subscriber can never stall the receive loop.
        Every exception is swallowed (the broadcast is best-effort).
        """
        sink = self.inbound_sink
        if sink is None:
            return
        if message.get("message_type") == "notice" and not getattr(
            self, self._INBOUND_SINK_NOTICES_ATTR, False,
        ):
            return
        try:
            self._spawn_inbound_sink(sink, message)
        except Exception:
            pass

    def _spawn_inbound_sink(self, sink: Any, message: dict[str, Any]) -> None:
        """Schedule ``_run_inbound_sink`` on a background task, keeping a strong ref.

        ``create_task`` must run inside a live event loop (``receive_message`` is
        always awaited on one); without a reference the task could be collected
        before it runs, so we track it in a FIFO deque and discard it on completion
        (or when it is dropped as the oldest occupant of the bounded backlog).
        """
        task = asyncio.create_task(self._run_inbound_sink(sink, message))
        tasks = getattr(self, "_inbound_sink_tasks", None)
        if tasks is None:
            tasks = collections.deque()
            setattr(self, "_inbound_sink_tasks", tasks)
        # Bounded backlog: when full, cancel and drop the oldest (front-of-queue) unfinished task.
        # Best-effort broadcast -- under pressure drop old, keep new, matching the SSE channel's drop-oldest.
        # Otherwise a slow or never-finishing sink grows the task set without bound and takes the connector process down.
        while len(tasks) >= self._INBOUND_SINK_MAX_BACKLOG:
            tasks.popleft().cancel()
        tasks.append(task)
        task.add_done_callback(self._discard_inbound_sink_task)

    def _discard_inbound_sink_task(self, task: asyncio.Task) -> None:
        """Drop a finished (or already-dropped) sink task from the tracked backlog."""
        tasks = getattr(self, "_inbound_sink_tasks", None)
        if tasks is None:
            return
        try:
            tasks.remove(task)
        except ValueError:
            # Already removed from the front by drop-oldest; the done_callback fires once more
            # after cancel -- ignore it.
            pass

    def _cancel_inbound_sink_tasks(self) -> None:
        """Cancel lingering inbound-sink tasks (called from each ``disconnect()``).

        Drop-oldest bounds the backlog, but tasks still running when the
        connection tears down would otherwise linger until their sink finishes;
        cancel them so nothing outlives the connection.
        """
        tasks = getattr(self, "_inbound_sink_tasks", None)
        if not tasks:
            return
        for task in list(tasks):
            task.cancel()
        tasks.clear()

    async def _run_inbound_sink(self, sink: Any, message: dict[str, Any]) -> None:
        """Run one sink call; swallow any failure so the broadcast never raises."""
        try:
            result = sink(message)
            if hasattr(result, "__await__"):
                await result
        except Exception:
            logger = getattr(self, "logger", None)
            if logger:
                logger.exception("Inbound sink failed (swallowed; broadcast is best-effort)")

    @property
    @abstractmethod
    def onebot_url(self) -> str:
        """Connection endpoint: the reverse-WS listen address, the forward-WS dial
        target, or (Open Platform) the API base URL."""
        ...

    @onebot_url.setter
    @abstractmethod
    def onebot_url(self, value: str) -> None:
        ...


@runtime_checkable
class ChatConnector(Protocol):
    """The connection surface any plugin consumes.

    Concrete instances today are ``OneBotClient`` (OneBot v11) and
    ``QQOpenPlatformConnection`` (QQ Open Platform). Both already expose every
    member below; the Protocol is a type annotation only (checked structurally,
    not at construction).

    Also exported as ``utils.connection.onebot.OneBotConnector`` (same object):
    plugins pin that name and its member set, so members must not be added or
    removed here without coordinating with them.
    """

    token: str

    # ── lifecycle ───────────────────────────────────────────
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    def is_connected(self) -> bool: ...
    async def receive_message(self, timeout: float = 1.0) -> Optional[dict[str, Any]]: ...

    # ── send surface ────────────────────────────────────────
    async def send_group_message_segments(
        self, group_id: str, segments: list[dict[str, Any]], *, record_sent: bool = True
    ) -> Optional[str]: ...
    async def send_private_message_segments(
        self, user_id: str, segments: list[dict[str, Any]]
    ) -> Optional[str]: ...
    async def send_group_poke(self, group_id: str, user_id: str) -> bool: ...
    async def send_group_image(
        self, group_id: str, image_data: str, *, reply_message_id: str = "", at_user_id: str = "", sub_type: str = ""
    ) -> Optional[str]: ...
    async def send_group_record(
        self, group_id: str, file_uri: str, *, reply_message_id: str = "", at_user_id: str = ""
    ) -> Optional[str]: ...

    # ── info / state ────────────────────────────────────────
    async def get_login_status(self) -> dict[str, Any]: ...
    def record_sent_message_id(self, message_id: str) -> None: ...
    def is_group_muted(self, group_id: str) -> bool: ...
    @property
    def onebot_url(self) -> str: ...
    @property
    def self_id(self) -> str: ...
    @property
    def sent_message_ids(self) -> dict[str, float]: ...

    # ── capability flags ────────────────────────────────────
    @property
    def needs_attention(self) -> bool: ...
    @property
    def supports_voice(self) -> bool: ...
    @property
    def supports_poke(self) -> bool: ...
    @property
    def supports_ark_cards(self) -> bool: ...

    # ── extended send ───────────────────────────────────────
    async def send_group_ark_card(
        self, group_id: str, ark_obj: dict[str, Any]
    ) -> bool: ...
    def set_inbound_sink(self, sink: Any | None, *, include_notices: bool = False) -> None: ...
