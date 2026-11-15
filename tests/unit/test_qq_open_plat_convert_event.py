"""Group-id key fallback in the QQ Open Platform event converter.

``_convert_event`` used to read only ``group_id``. The channel's group
identifier is in fact an openid, and the v2 payload may carry it under
``group_openid`` instead; when that happens ``group_id`` is the empty
string, the backlog writer drops the message outright, and the group
subject degrades to ``qq::`` which the scope validator rejects.

The fallback order is the load-bearing part: ``group_id`` must keep
winning when present. Flipping it would re-key every group subject_id,
and scope matching is byte equality with no aliasing, so all existing
scoped group memories would be orphaned in one shot.

The ``author`` blocks below carry the real protocol keys. They used to say
``{"id": ...}``, which is the very assumption that turned out to be wrong --
see ``test_qq_open_platform_actor_identity.py`` for where the speaker id
actually comes from.
"""

import json

import pytest

from utils.connection.qq.open_platform import QQOpenPlatformConnection


def _connection() -> QQOpenPlatformConnection:
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = "bot-self"
    return conn


def _group_payload(**group_keys) -> dict:
    payload = {
        "id": "msg-1",
        "content": "<@!bot-self> zaima",
        "author": {"member_openid": "user-1"},
    }
    payload.update(group_keys)
    return payload


@pytest.mark.parametrize(
    ("group_keys", "expected"),
    [
        # Today's protocol: the original key is present and still wins.
        ({"group_id": "G-plain"}, "G-plain"),
        # v2 semantics: only the openid key is sent.
        ({"group_openid": "G-openid"}, "G-openid"),
        # Both present — pins the priority so a later refactor can't flip it.
        ({"group_id": "G-plain", "group_openid": "G-openid"}, "G-plain"),
        # Present but empty is the same as absent for the fallback.
        ({"group_id": "", "group_openid": "G-openid"}, "G-openid"),
    ],
)
def test_convert_event_group_id_prefers_group_id_then_group_openid(group_keys, expected):
    msg = _connection()._convert_event("GROUP_AT_MESSAGE_CREATE", _group_payload(**group_keys))

    assert msg is not None
    assert msg["message_type"] == "group"
    assert msg["group_id"] == expected


def test_convert_event_group_id_empty_when_neither_key_present():
    # Neither key → empty string, i.e. the pre-existing discard path
    # (backlog_service returns early on a falsy group_id). The fallback
    # must not invent an id out of some other field.
    msg = _connection()._convert_event("GROUP_AT_MESSAGE_CREATE", _group_payload())

    assert msg is not None
    assert msg["group_id"] == ""


def test_convert_event_private_message_keeps_group_id_empty():
    # The C2C branch has no group at all; a stray group key on the payload
    # must not leak into the private message's group_id.
    msg = _connection()._convert_event(
        "C2C_MESSAGE_CREATE",
        {
            "id": "msg-2",
            "content": "hi",
            "author": {"user_openid": "user-1"},
            "group_openid": "G-openid",
        },
    )

    assert msg is not None
    assert msg["message_type"] == "private"
    assert msg["group_id"] == ""


#: The platform identifies speakers and @ targets by **openid** (32-char hex), while
#: ``READY`` reports the bot's **numeric QQ id**. The two never compare equal -- that
#: mismatch is exactly why @bot used to be dropped under full-message mode, so every
#: test below keeps them visibly different.
BOT_QQ_ID = "2531289501539311988"
BOT_OPENID = "c3fe22eb942d195d0ea8a3f10f21f66e"
OTHER_OPENID = "9a1b2c3d4e5f60718293a4b5c6d7e8f9"
GROUP_ID = "ddb0dfd2d9e35fb4e6f723a8df675eaf"


def _mention(
    openid: str,
    *,
    is_you: bool = False,
    is_bot: bool = False,
    username: str = "someone",
) -> dict:
    """One entry of the payload's ``mentions`` array, shaped like the real one.

    ``is_bot`` and ``is_you`` are independent on purpose: a group can hold several
    bots, and being *a* bot does not make the target *this* bot.
    """
    return {
        "bot": is_bot or is_you,
        "id": openid,
        "is_you": is_you,
        "member_openid": openid,
        "member_role": "member",
        "scope": "single",
        "username": username,
    }


def _mention_conn() -> QQOpenPlatformConnection:
    """A connection whose numeric self_id can never match an openid on its own."""
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = BOT_QQ_ID
    return conn


class _RecordingLogger:
    """Minimal logger double: records ``warning`` calls, ignores everything else."""

    def __init__(self, sink: list[tuple[str, str]]) -> None:
        self._sink = sink

    def warning(self, msg: str, *args, **kwargs) -> None:
        self._sink.append(("WARNING", str(msg)))


def _mention_payload(content: str, *, mentions=None) -> dict:
    payload = {
        "id": "m-1",
        "group_id": GROUP_ID,
        "group_openid": GROUP_ID,
        "author": {"member_openid": OTHER_OPENID, "username": "Alice"},
        "content": content,
        "timestamp": 1700000000,
    }
    if mentions is not None:
        payload["mentions"] = mentions
    return payload


def test_open_platform_group_mentions_distinguish_bot_from_other_users():
    """Inline ``<@OPENID>`` markers are parsed, and the bot is told apart by openid.

    The old assertion used ``<@!10000>``: a *numeric* id that the current protocol no
    longer emits. Real markers carry 32-char hex openids and no ``!``.
    """
    conn = _mention_conn()

    bot_only = conn._convert_event(
        "GROUP_AT_MESSAGE_CREATE",
        _mention_payload(f"<@{BOT_OPENID}> hello"),
    )
    assert bot_only["mentioned_user_ids"] == [BOT_OPENID]
    assert bot_only["content"] == "hello"
    assert bot_only["mentions_other_user"] is False
    assert bot_only["is_at_bot"] is True

    with_other_user = conn._convert_event(
        "GROUP_AT_MESSAGE_CREATE",
        _mention_payload(f"<@{BOT_OPENID}> <@{OTHER_OPENID}> hello"),
    )
    assert with_other_user["mentioned_user_ids"] == [BOT_OPENID, OTHER_OPENID]
    assert with_other_user["mentions_other_user"] is True
    assert with_other_user["is_at_bot"] is True


def test_full_group_message_at_bot_is_detected_via_is_you_flag():
    """``GROUP_MESSAGE_CREATE`` has no AT guarantee -- ``mentions[].is_you`` decides.

    This is the regression that motivated the fix: under full-message mode the event
    is ``GROUP_MESSAGE_CREATE``, and comparing ``self_id`` (numeric) against the
    mention openid never matched, so @bot was silently treated as unmentioned.
    """
    conn = _mention_conn()

    msg = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(
            f"<@{BOT_OPENID}> 你好",
            mentions=[_mention(BOT_OPENID, is_you=True, username="皖萱")],
        ),
    )

    assert msg["is_at_bot"] is True
    assert msg["mentioned_user_ids"] == [BOT_OPENID]
    assert msg["mentions_other_user"] is False
    assert msg["content"] == "你好"


def test_full_group_message_without_mention_is_not_at_bot():
    """A plain group message reaches the gate but must not look like a summons."""
    conn = _mention_conn()

    msg = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload("just chatting"),
    )

    assert msg["is_at_bot"] is False
    assert msg["mentioned_user_ids"] == []
    assert msg["content"] == "just chatting"


def test_full_group_message_mentioning_only_someone_else_is_not_at_bot():
    conn = _mention_conn()

    msg = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(
            f"<@{OTHER_OPENID}> look",
            mentions=[_mention(OTHER_OPENID, username="Bob")],
        ),
    )

    assert msg["is_at_bot"] is False
    assert msg["mentions_other_user"] is True


def test_full_group_message_at_bot_and_someone_else_sets_both_flags():
    conn = _mention_conn()

    msg = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(
            f"<@{BOT_OPENID}> <@{OTHER_OPENID}> hey",
            mentions=[
                _mention(BOT_OPENID, is_you=True, username="皖萱"),
                _mention(OTHER_OPENID, username="Bob"),
            ],
        ),
    )

    assert msg["is_at_bot"] is True
    assert msg["mentions_other_user"] is True


def test_learned_bot_openid_is_used_when_payload_has_no_mentions_array():
    """Fallback path: an AT-less payload carrying only inline markers still resolves.

    The bot openid is learned from an earlier payload that did include ``is_you``;
    afterwards an inline-only payload can match against the cached value.
    """
    conn = _mention_conn()

    # Learn it from a full payload that carries the structured flag.
    conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(
            f"<@{BOT_OPENID}> hi",
            mentions=[_mention(BOT_OPENID, is_you=True)],
        ),
    )

    # No mentions array at all -- only the inline marker remains.
    inline_only = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(f"<@{BOT_OPENID}> again"),
    )

    assert inline_only["is_at_bot"] is True
    assert inline_only["mentions_other_user"] is False


def test_mentioning_a_different_bot_is_not_being_addressed():
    """@-ing a neighbouring bot must not summon this one.

    Its mention carries ``bot: true`` but not ``is_you`` -- ``bot`` only says the
    target is *some* bot. Relying on it would make this bot answer messages aimed at
    its neighbours, and would also poison the per-group openid cache with a stranger.
    """
    conn = _mention_conn()
    other_bot_openid = "7f7f7f7f8a8a8a8a9b9b9b9bc0c0c0c0"

    msg = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(
            f"<@{other_bot_openid}> 帮我查天气",
            mentions=[_mention(other_bot_openid, is_bot=True, username="别的机器人")],
        ),
    )

    assert msg["is_at_bot"] is False
    assert msg["mentions_other_user"] is True
    # The stranger's openid must NOT be cached as this bot's identity.
    assert conn._remembered_bot_openid(GROUP_ID) == ""

    # A later inline-only message aimed at the same other bot stays unattributed.
    follow_up = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(f"<@{other_bot_openid}> 再查一次"),
    )
    assert follow_up["is_at_bot"] is False


def test_self_mention_still_wins_when_another_bot_is_also_mentioned():
    """``is_you`` on our own entry decides, regardless of a second bot's presence."""
    conn = _mention_conn()
    other_bot_openid = "7f7f7f7f8a8a8a8a9b9b9b9bc0c0c0c0"

    msg = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(
            f"<@{BOT_OPENID}> <@{other_bot_openid}> 你们两个怎么看",
            mentions=[
                _mention(BOT_OPENID, is_you=True, username="皖萱"),
                _mention(other_bot_openid, is_bot=True, username="别的机器人"),
            ],
        ),
    )

    assert msg["is_at_bot"] is True
    assert msg["mentions_other_user"] is True
    assert conn._remembered_bot_openid(GROUP_ID) == BOT_OPENID


def test_unattributable_inline_mention_is_reported_once_per_group():
    """No ``is_you`` and no cached openid: judge conservatively and say so.

    This is the cold-start corner: the platform's ``is_you`` flag is the only
    trustworthy source for "which of these openids is me", so an inline-only mention
    before that flag has ever arrived cannot be attributed. Rather than guess, the
    converter reports ``False`` and emits one diagnostic per group so a platform
    regression shows up in the logs instead of looking like "the bot ignores @s".
    """
    conn = _mention_conn()
    # Two channels are exercised on purpose (file log + plugin UI ring); keep them
    # apart so "emitted once" is actually asserted rather than double-counted.
    log_sink: list[tuple[str, str]] = []
    ui_sink: list[tuple[str, str]] = []
    conn.logger = _RecordingLogger(log_sink)
    conn._emit_log = lambda level, msg: ui_sink.append((level, msg))

    first = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(f"<@{BOT_OPENID}> hello"),
    )
    assert first["is_at_bot"] is False
    assert first["mentions_other_user"] is True
    assert len(log_sink) == 1 and len(ui_sink) == 1
    assert "无法归因" in log_sink[0][1]
    assert ui_sink[0][0] == "WARNING"

    # Same group again -- the diagnostic must not spam.
    conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(f"<@{BOT_OPENID}> again"),
    )
    assert len(log_sink) == 1 and len(ui_sink) == 1


def test_attributable_mention_does_not_emit_the_diagnostic():
    """The healthy path stays quiet: ``is_you`` present => no warning."""
    conn = _mention_conn()
    log_sink: list[tuple[str, str]] = []
    ui_sink: list[tuple[str, str]] = []
    conn.logger = _RecordingLogger(log_sink)
    conn._emit_log = lambda level, msg: ui_sink.append((level, msg))

    msg = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(
            f"<@{BOT_OPENID}> hi",
            mentions=[_mention(BOT_OPENID, is_you=True)],
        ),
    )

    assert msg["is_at_bot"] is True
    assert log_sink == [] and ui_sink == []


def test_mentioning_someone_else_does_not_consume_the_diagnostic():
    """@-ing a third party must not spend this group's one-shot warning.

    ``mentions`` fully explains the inline marker, so the absence of ``is_you`` means
    "the bot was not summoned" -- a complete, understandable payload. If this burned
    the diagnostic, a later genuinely unattributable @bot in the same group would be
    reported only... never.
    """
    conn = _mention_conn()
    log_sink: list[tuple[str, str]] = []
    conn.logger = _RecordingLogger(log_sink)
    conn._emit_log = lambda level, msg: None

    other = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(
            f"<@{OTHER_OPENID}> look at this",
            mentions=[_mention(OTHER_OPENID, username="Bob")],
        ),
    )
    assert other["is_at_bot"] is False
    assert other["mentions_other_user"] is True
    assert log_sink == []                                   # nothing to report

    # The warning is still available for a payload the platform did NOT explain.
    unexplained = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(f"<@{OTHER_OPENID}> look"),
    )
    assert unexplained["is_at_bot"] is False
    assert len(log_sink) == 1
    assert "无法归因" in log_sink[0][1]


def test_partially_explained_mentions_still_warn():
    """If ``mentions`` omits one inline target, that target is unattributable."""
    conn = _mention_conn()
    log_sink: list[tuple[str, str]] = []
    conn.logger = _RecordingLogger(log_sink)
    conn._emit_log = lambda level, msg: None

    extra = "1a2b3c4d5e6f708192a3b4c5d6e7f809"
    msg = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(
            f"<@{OTHER_OPENID}> <@{extra}> hi",
            # Only one of the two inline targets is described.
            mentions=[_mention(OTHER_OPENID, username="Bob")],
        ),
    )

    assert msg["is_at_bot"] is False
    assert len(log_sink) == 1
    assert "无法归因" in log_sink[0][1]


def test_convert_event_survives_an_uninitialised_instance():
    """Conversion must not depend on ``__init__`` having run.

    Tests (and any caller building the object for a single payload) use ``__new__``
    and set only ``_self_id``. Every new piece of per-connection state therefore has
    to be created on demand -- a bare ``self.<attr>`` read would raise AttributeError
    for these instances, which is exactly how this test earns its keep.
    """
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = BOT_QQ_ID
    # Intentionally nothing else: no cache dict, no logger, no _emit_log.

    plain = conn._convert_event("GROUP_MESSAGE_CREATE", _mention_payload("hi"))
    assert plain is not None
    assert plain["is_at_bot"] is False
    assert plain["mentioned_user_ids"] == []

    mentioned = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(
            f"<@{BOT_OPENID}> hi",
            mentions=[_mention(BOT_OPENID, is_you=True)],
        ),
    )
    assert mentioned is not None
    assert mentioned["is_at_bot"] is True
    assert conn._remembered_bot_openid(GROUP_ID) == BOT_OPENID


def test_capability_flags_default_to_off_for_at_only_bots():
    """An AT-only bot genuinely does not receive every message.

    The intents bitmap is submitted by the client, but the group-message privilege is
    granted per group -- so "we asked for it" proves nothing. Reporting ``True`` up
    front would make consumers arbitrate messages that were all explicit @s. Both
    flags therefore start ``False`` and only flip once the platform actually delivers
    a ``GROUP_MESSAGE_CREATE``.
    """
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = BOT_QQ_ID

    assert conn.receives_all_messages is False
    assert conn.needs_attention is False


class _ScriptedWS:
    """Feeds a fixed list of frames, then stops the receive loop cleanly.

    Same shape as the helper in ``test_qq_open_platform_identity_scope.py``: once the
    frames run out it sets ``_closing`` and returns a heartbeat ack, so the loop exits
    without attempting a reconnect. ``send`` records outgoing frames so handshake
    tests can assert which path (Resume vs Identify) was taken.
    """

    def __init__(self, connection, frames):
        self._connection = connection
        self._frames = list(frames)
        self.sent: list[dict] = []

    async def recv(self):
        if self._frames:
            return json.dumps(self._frames.pop(0))
        self._connection._closing = True
        return json.dumps({"op": 11})

    async def send(self, raw):
        self.sent.append(json.loads(raw))


def _handshake_connection(frames: list[dict]) -> QQOpenPlatformConnection:
    """A connection wired for ``_handshake``, with a session id so Resume is attempted."""
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = BOT_QQ_ID
    conn._access_token = "token"
    conn._session_id = "session-1"
    conn._last_seq = 7
    conn.logger = None
    conn._identity_probe = lambda: False
    conn._identity_probe_emitted = 0
    conn._ws = _ScriptedWS(conn, frames)
    return conn


def _hello() -> dict:
    return {"op": 10, "d": {"heartbeat_interval": 30000}}


def _ready() -> dict:
    return {"op": 0, "t": "READY", "d": {"user": {"id": "1", "username": "bot"}, "session_id": "session-2"}}


def _loop_connection(frames: list[dict]) -> QQOpenPlatformConnection:
    """A connection wired to a scripted socket, ready for ``_receive_loop``.

    Only the members the loop actually touches are set, so every other read has to be
    tolerant (the same contract ``test_convert_event_survives_an_uninitialised_instance``
    pins).
    """
    import asyncio

    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = BOT_QQ_ID
    conn._closing = False
    conn._last_seq = 0
    conn._message_queue = asyncio.Queue(maxsize=8)
    conn._identity_probe = lambda: False
    conn._identity_probe_emitted = 0
    conn._ws = _ScriptedWS(conn, frames)
    return conn


def _dispatch(event_type: str, data: dict, seq: int = 1) -> dict:
    return {"op": 0, "s": seq, "t": event_type, "d": data}


async def test_capability_flags_flip_after_first_full_group_message():
    """The probe flips inside ``_receive_loop``, driven by a real dispatch frame.

    This goes through the actual loop deliberately: an earlier version of this test
    assigned ``_received_full_group_message`` by hand, which meant it would still pass
    after the probe code in the loop was deleted -- it proved nothing.
    """
    import asyncio

    conn = _loop_connection([
        _dispatch("GROUP_MESSAGE_CREATE", _mention_payload(
            f"<@{BOT_OPENID}> hi",
            mentions=[_mention(BOT_OPENID, is_you=True)],
        )),
    ])
    assert conn.receives_all_messages is False      # before any event

    await asyncio.wait_for(conn._receive_loop(), timeout=5.0)

    assert conn.receives_all_messages is True
    assert conn.needs_attention is True


async def test_capability_flags_stay_off_for_at_only_traffic():
    """AT-only events must not flip the probe -- that is the whole point of it."""
    import asyncio

    conn = _loop_connection([
        _dispatch("GROUP_AT_MESSAGE_CREATE", _mention_payload(f"<@{BOT_OPENID}> hi")),
    ])

    await asyncio.wait_for(conn._receive_loop(), timeout=5.0)

    assert conn.receives_all_messages is False
    assert conn.needs_attention is False


def test_duplicate_inbound_message_id_is_suppressed():
    """Query and record are separate steps; only a recorded id counts as seen.

    Live runs show one event per @bot message, so this is a guard against a platform
    revision that starts sending both ``GROUP_AT_MESSAGE_CREATE`` and
    ``GROUP_MESSAGE_CREATE`` for one message -- which would make the bot answer twice.
    """
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = BOT_QQ_ID

    assert conn._is_duplicate_inbound("msg-42") is False   # never recorded
    conn._remember_inbound("msg-42")
    assert conn._is_duplicate_inbound("msg-42") is True    # duplicate within window
    assert conn._is_duplicate_inbound("msg-43") is False   # different message

    # An empty id cannot be keyed on, so it must never be treated as a duplicate.
    assert conn._is_duplicate_inbound("") is False
    conn._remember_inbound("")
    assert conn._is_duplicate_inbound("") is False


def test_duplicate_suppression_expires():
    """The window is short: a much later repeat is accepted again."""
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = BOT_QQ_ID

    conn._remember_inbound("msg-9")
    assert conn._is_duplicate_inbound("msg-9") is True
    # Backdate the recorded stamp beyond the TTL instead of sleeping.
    conn._seen_inbound_ids["msg-9"] -= 10_000
    assert conn._is_duplicate_inbound("msg-9") is False


def _queue_with(conn, capacity: int, messages: list[dict]):
    """Attach a bounded queue and return it."""
    import asyncio

    queue: asyncio.Queue = asyncio.Queue(maxsize=capacity)
    conn._message_queue = queue
    for item in messages:
        queue.put_nowait(item)
    return queue


def _qmsg(mid: str, *, is_at_bot: bool) -> dict:
    return {"message_id": mid, "is_at_bot": is_at_bot, "content": mid}


def _drainable_conn() -> QQOpenPlatformConnection:
    """A connection whose ``receive_message`` can run for real (no sink attached).

    ``_dispatch_inbound`` reads the ``inbound_sink`` property, which delegates to the
    ``_inbound_sink`` attribute a ``__new__`` object never initialised. Setting it to
    ``None`` means "no subscriber", the branch that keeps hand-off side-effect free.
    """
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = BOT_QQ_ID
    conn._inbound_sink = None
    return conn


def test_evicted_message_is_not_stuck_as_duplicate():
    """An evicted message must be forgettable, or a redelivery is lost forever.

    This pins the ordering requirement: recording the id before enqueueing would mean
    a message that arrives, is recorded, and is then evicted for a newer one gets
    suppressed on redelivery while no longer pending anywhere -- lost for good.

    Note the second half: once evicted, ``A`` may still be refused for *capacity*
    (the slot is now held by an @), but that refusal must not mark it as seen either,
    or the same trap reappears one step later.
    """
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = BOT_QQ_ID
    queue = _queue_with(conn, 1, [])

    # Chatter first, then an @ arrives: the chatter is evicted (priority rule) and its
    # record must go with it.
    first = _qmsg("A", is_at_bot=False)
    assert conn._enqueue_inbound(first) is None
    assert list(queue._queue) == [first]

    second = _qmsg("B", is_at_bot=True)
    assert conn._enqueue_inbound(second) is None
    assert [item["message_id"] for item in list(queue._queue)] == ["B"]
    assert conn._is_duplicate_inbound("A") is False        # forgotten, not stuck

    # Redelivery of A cannot take the slot back (B is an @ and A is chatter), but the
    # refusal is for capacity, not duplication -- so A stays eligible for the future.
    assert conn._enqueue_inbound(first) == "queue_full"
    assert [item["message_id"] for item in list(queue._queue)] == ["B"]
    assert conn._is_duplicate_inbound("A") is False


def test_enqueue_rejects_a_genuine_duplicate():
    """A message still pending under the same id must not be queued twice."""
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = BOT_QQ_ID
    queue = _queue_with(conn, 4, [])

    msg = _qmsg("dup", is_at_bot=True)
    assert conn._enqueue_inbound(msg) is None              # queued
    assert conn._enqueue_inbound(dict(msg)) == "duplicate"  # redelivery
    assert [item["message_id"] for item in list(queue._queue)] == ["dup"]


def test_queue_full_evicts_chatter_to_keep_an_at_message():
    """The whole point: a full queue must not swallow the @ a human is waiting on.

    Under full-message mode the queue fills with chatter, so drop-oldest would discard
    whichever message is oldest -- often a real @bot from moments ago.
    """
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = BOT_QQ_ID
    queue = _queue_with(conn, 3, [_qmsg("old-at", is_at_bot=True),
                                  _qmsg("chatter-1", is_at_bot=False),
                                  _qmsg("chatter-2", is_at_bot=False)])

    incoming = _qmsg("new-at", is_at_bot=True)
    dropped = conn._evict_inbound_for(incoming)
    queue.put_nowait(incoming)

    remaining = [item["message_id"] for item in list(queue._queue)]
    assert dropped == "chatter-1"          # oldest *unaddressed* one, not the oldest
    assert "old-at" in remaining           # the earlier @bot survived
    assert "new-at" in remaining
    assert "chatter-1" not in remaining


def test_chatter_arrival_evicts_queued_chatter_not_a_queued_at():
    """An @ at the head must survive a chatter arrival that follows it.

    This is the case the naive "only look for chatter when the *arrival* is an @"
    logic got backwards: queue ``[@bot, chatter]`` + arrival ``chatter`` used to
    evict the head, i.e. the @bot.
    """
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = BOT_QQ_ID
    queue = _queue_with(conn, 2, [_qmsg("at-1", is_at_bot=True),
                                  _qmsg("chatter-1", is_at_bot=False)])

    dropped = conn._evict_inbound_for(_qmsg("chatter-2", is_at_bot=False))
    queue.put_nowait(_qmsg("chatter-2", is_at_bot=False))

    assert dropped == "chatter-1"          # the queued chatter, not the head @
    assert [item["message_id"] for item in list(queue._queue)] == ["at-1", "chatter-2"]


def test_queue_full_drops_oldest_when_everything_is_an_at():
    """Nothing to prefer: all queued messages are addressed, so the oldest loses."""
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = BOT_QQ_ID
    queue = _queue_with(conn, 2, [_qmsg("at-1", is_at_bot=True),
                                  _qmsg("at-2", is_at_bot=True)])

    dropped = conn._evict_inbound_for(_qmsg("at-3", is_at_bot=True))
    queue.put_nowait(_qmsg("at-3", is_at_bot=True))

    assert dropped == "at-1"
    assert [item["message_id"] for item in list(queue._queue)] == ["at-2", "at-3"]


def test_chatter_is_refused_when_the_queue_holds_only_ats():
    """Trading a queued summon for fresh chatter is exactly backwards."""
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = BOT_QQ_ID
    queue = _queue_with(conn, 2, [_qmsg("at-1", is_at_bot=True),
                                  _qmsg("at-2", is_at_bot=True)])

    outcome = conn._enqueue_inbound(_qmsg("chatter", is_at_bot=False))

    assert outcome == "queue_full"
    assert [item["message_id"] for item in list(queue._queue)] == ["at-1", "at-2"]
    # The refused message is NOT remembered: a redelivery should get another chance.
    assert conn._is_duplicate_inbound("chatter") is False


def test_connect_does_not_touch_the_probe_before_the_handshake(monkeypatch):
    """Reset lives in ``_handshake``, not in ``connect()``.

    Keeping a single reset point avoids the case where the two disagree: a connection
    attempt that dies before reaching Identify never negotiates anything, so it has no
    business clearing a value it did not re-learn. The handshake-driven tests below
    cover the reset itself.
    """
    import asyncio

    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._app_id = "app"
    conn._client_secret = "secret"
    conn._sandbox = None                              # read by _resolve_api_base
    conn.logger = None                                # keeps connect() quiet
    conn._closing = True
    conn._received_full_group_message = True          # stale from the last session

    def _stop_before_network(*args, **kwargs):
        raise RuntimeError("stop before any network I/O")

    monkeypatch.setattr(
        "utils.connection.qq.open_platform.httpx.AsyncClient", _stop_before_network,
    )

    with pytest.raises(RuntimeError, match="stop before any network I/O"):
        asyncio.run(conn.connect())

    # Nothing was negotiated, so the flag is left alone.
    assert conn._received_full_group_message is True


async def test_successful_resume_keeps_the_full_group_message_probe():
    """Resuming the *same* session must keep the learned value.

    Resume replays missed events; it does not renegotiate the privilege. Clearing the
    flag here would momentarily report ``receives_all_messages=False`` for a bot that
    is in fact still receiving everything.
    """
    import asyncio

    conn = _handshake_connection([_hello(), {"op": 0, "t": "RESUMED", "d": {}}])
    conn._received_full_group_message = True

    await asyncio.wait_for(conn._handshake(is_reconnect=True), timeout=5.0)

    assert conn._received_full_group_message is True
    assert [frame.get("op") for frame in conn._ws.sent] == [6]   # Resume only, no Identify


async def test_resume_falling_back_to_identify_resets_the_probe():
    """A rejected Resume falls through to Identify, which opens a NEW session.

    That is a fresh negotiation: the group may have switched back to "@-only" while we
    were away, so the capability has to be re-learned. Leaving it ``True`` would keep
    both capability properties lying until the next full-group event arrives.
    """
    import asyncio

    conn = _handshake_connection([
        _hello(),
        {"op": 9, "d": False},        # Resume rejected (invalid session)
        _ready(),                     # Identify accepted
    ])
    conn._received_full_group_message = True

    await asyncio.wait_for(conn._handshake(is_reconnect=True), timeout=5.0)

    assert conn._received_full_group_message is False
    assert conn.receives_all_messages is False
    assert conn.needs_attention is False
    assert [frame.get("op") for frame in conn._ws.sent] == [6, 2]  # Resume, then Identify


async def test_first_handshake_resets_the_probe():
    """The initial (non-reconnect) handshake always goes through Identify."""
    import asyncio

    conn = _handshake_connection([_hello(), _ready()])
    conn._received_full_group_message = True

    await asyncio.wait_for(conn._handshake(is_reconnect=False), timeout=5.0)

    assert conn._received_full_group_message is False
    assert [frame.get("op") for frame in conn._ws.sent] == [2]


def test_mentions_entry_prefers_member_openid_over_id():
    """The inline ``<@OPENID>`` carries the *member* openid, so that is what to cache.

    A mentions entry can carry both ``id`` and ``member_openid`` with different values.
    Caching ``id`` would leave the cache holding a value that never appears in an
    inline marker, so the fallback attribution silently never matches. Live payloads
    happen to send them equal -- which is exactly why this needs pinning.
    """
    conn = _mention_conn()
    member_openid = "aaaa1111bbbb2222cccc3333dddd4444"
    differing_id = "ffff9999eeee8888dddd7777cccc6666"

    first = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(
            f"<@{member_openid}> hi",
            mentions=[{
                "bot": True,
                "is_you": True,
                "id": differing_id,
                "member_openid": member_openid,
                "username": "皖萱",
            }],
        ),
    )
    assert first["is_at_bot"] is True
    assert conn._remembered_bot_openid(GROUP_ID) == member_openid   # not differing_id
    # Only the member openid is collected as an @ target. Adding the alternate ``id``
    # would be actively harmful: it is not in ``bot_ids``, so it would flip
    # ``mentions_other_user`` to True for a message that only mentions the bot itself.
    assert first["mentioned_user_ids"] == [member_openid]
    assert first["mentions_other_user"] is False

    # An inline-only follow-up still resolves through the cached member openid.
    follow = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(f"<@{member_openid}> again"),
    )
    assert follow["is_at_bot"] is True
    assert follow["mentions_other_user"] is False


def test_repeated_mention_of_the_same_account_is_not_someone_else():
    """Counting occurrences instead of distinct targets would misread a double @.

    A user can @ the same account twice in one message. Both markers resolve to one
    target, so "summoned only me" must not look like "named me and someone else" --
    otherwise ``mentions_other_user`` flips and downstream decides the message is
    aimed at a third party.
    """
    conn = _mention_conn()

    duplicated = conn._convert_event(
        "GROUP_AT_MESSAGE_CREATE",
        _mention_payload(f"<@{BOT_OPENID}> <@{BOT_OPENID}> hello"),
    )
    assert duplicated["mentioned_user_ids"] == [BOT_OPENID]     # deduplicated
    assert duplicated["mentions_other_user"] is False
    assert duplicated["is_at_bot"] is True

    # Same on the full-message path with the structured flag present.
    conn2 = _mention_conn()
    twice = conn2._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(
            f"<@{BOT_OPENID}> <@{BOT_OPENID}> hi",
            mentions=[_mention(BOT_OPENID, is_you=True)],
        ),
    )
    assert twice["mentioned_user_ids"] == [BOT_OPENID]
    assert twice["mentions_other_user"] is False
    assert twice["is_at_bot"] is True


def test_repeated_mention_of_another_account_still_counts_as_once():
    """The same dedup must not hide a genuinely different third party."""
    conn = _mention_conn()

    msg = conn._convert_event(
        "GROUP_AT_MESSAGE_CREATE",
        _mention_payload(f"<@{BOT_OPENID}> <@{OTHER_OPENID}> <@{OTHER_OPENID}> hi"),
    )
    assert msg["mentioned_user_ids"] == [BOT_OPENID, OTHER_OPENID]
    assert msg["mentions_other_user"] is True


async def test_still_queued_message_never_expires_from_dedup():
    """A message waiting in the queue must stay a duplicate, however old it is.

    The TTL answers "how long after *delivery* should a repeat be suppressed". A slow
    consumer can leave a message pending far longer than that; if the TTL were applied
    to the queued one too, a redelivery would be accepted, the id would appear twice in
    the queue, and ``receive_message`` would hand the same message over twice.
    """
    conn = _drainable_conn()
    queue = _queue_with(conn, 4, [])

    first = _qmsg("A", is_at_bot=True)
    assert conn._enqueue_inbound(first) is None

    # Age the record far beyond the TTL while the message is still pending.
    conn._seen_inbound_ids["A"] -= 10_000
    assert conn._is_duplicate_inbound("A") is True          # still queued => still dup
    assert conn._enqueue_inbound(dict(first)) == "duplicate"
    assert [item["message_id"] for item in list(queue._queue)] == ["A"]


async def test_hand_off_restarts_the_dedup_window():
    """Delivering a message refreshes its record, so the window starts at hand-off.

    Without the refresh, a message that waited in the queue longer than the TTL is
    outside its window the moment it is delivered: a redelivery right after would be
    queued again, and the caller would process the same message twice.
    """
    conn = _drainable_conn()
    _queue_with(conn, 4, [])

    msg = _qmsg("A", is_at_bot=True)
    assert conn._enqueue_inbound(msg) is None

    # Simulate a long wait in the queue, past the TTL.
    conn._seen_inbound_ids["A"] -= 10_000

    delivered = await conn.receive_message(timeout=1)
    assert delivered is not None and delivered["message_id"] == "A"

    # The record now dates from the hand-off, so the window is fresh again.
    assert conn._is_duplicate_inbound("A") is True
    assert conn._enqueue_inbound(dict(msg)) == "duplicate"


async def test_delivered_message_still_has_a_short_dedup_window():
    """After delivery the TTL applies, so a repeat is only suppressed briefly."""
    conn = _drainable_conn()
    _queue_with(conn, 4, [])

    msg = _qmsg("B", is_at_bot=True)
    assert conn._enqueue_inbound(msg) is None
    delivered = await conn.receive_message(timeout=1)
    assert delivered is not None

    assert conn._is_duplicate_inbound("B") is True          # within TTL
    conn._seen_inbound_ids["B"] -= 10_000
    assert conn._is_duplicate_inbound("B") is False         # past TTL


async def test_receive_message_times_out_to_none():
    """The hand-off path must still behave as a plain timeout when idle."""
    conn = _drainable_conn()
    _queue_with(conn, 4, [])

    assert await conn.receive_message(timeout=0.01) is None


def test_receive_message_keeps_dequeue_and_stamp_atomic():
    """``wait_for`` would open a dequeue/stamp gap; the pairing must also be structural.

    Two independent guarantees, both pinned here because the failure is invisible until
    the timing lines up:

    1. ``asyncio.timeout`` instead of ``asyncio.wait_for`` -- on 3.11 the latter runs
       the awaitable in its own Task and returns via a done-callback, so the message is
       out of the queue while this coroutine is still waiting to be resumed. The
       receive loop can run in that gap and accept a redelivery.
    2. the dequeue and the stamp live in one inner coroutine, so even a future caller
       that wraps it differently cannot separate them.
    """
    import inspect

    source = inspect.getsource(QQOpenPlatformConnection.receive_message)

    assert "asyncio.timeout(" in source
    assert "asyncio.wait_for(" not in source
    # The stamp must sit inside the same coroutine that dequeues.
    take_block = source.split("async def _take_and_stamp", 1)
    assert len(take_block) == 2, "dequeue/stamp must be paired in one coroutine"
    inner = take_block[1].split("async with asyncio.timeout", 1)[0]
    assert "self._message_queue.get()" in inner
    assert "_remember_inbound(" in inner


def test_learned_bot_openid_is_scoped_per_group():
    """Openids are per-conversation, so one group's value must not leak into another.

    A single shared slot would let group A's bot openid make group B's @bot look
    like "mentioned someone else".
    """
    conn = _mention_conn()
    other_group = "aaaa1111bbbb2222cccc3333dddd4444"

    conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        _mention_payload(f"<@{BOT_OPENID}> hi", mentions=[_mention(BOT_OPENID, is_you=True)]),
    )

    # Same bot, different group => different openid, and it is not @'d here.
    stranger = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        {
            "id": "m-2",
            "group_id": other_group,
            "author": {"member_openid": OTHER_OPENID},
            "content": f"<@{OTHER_OPENID}> yo",
            "mentions": [_mention(OTHER_OPENID)],
        },
    )
    assert stranger is not None
    assert stranger["is_at_bot"] is False
    # Group A's cached openid must not be treated as "self" here.
    assert stranger["mentions_other_user"] is True


# ── step 1 of the host-channel design: port #3706 onto #3709 ───────────────
#
# Three things the single-slot version could not do: honour the platform's own
# ``is_you`` marker, keep one group's bot openid away from another group, and
# refuse to deliver the same message twice. All three are @-attribution, and all
# three failed silently: the bot either ignored a real @ or answered one message
# twice.

def _bare_connection() -> QQOpenPlatformConnection:
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = ""
    conn.logger = None
    return conn


def test_is_you_marks_the_bot_even_when_another_member_is_named_too():
    conn = _bare_connection()
    msg = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        {
            "id": "m", "group_openid": "G1",
            "content": "<@0123456789ABCDEF> 和 <@FEDCBA9876543210> 看一下",
            "author": {"member_openid": "speaker"},
            "mentions": [
                {"member_openid": "0123456789ABCDEF", "is_you": True, "bot": True},
                {"member_openid": "FEDCBA9876543210"},
            ],
        },
    )
    assert msg["is_at_bot"] is True
    # The other openid is a second target, so this is not "only me".
    assert msg["mentions_other_user"] is True
    # Learning happened, and it landed under this group only.
    assert conn._bot_openid_by_group == {"G1": "0123456789ABCDEF"}


def test_a_flag_for_another_bot_does_not_summon_this_one():
    # ``bot: true`` says the target is *some* bot. In a group with two bots, @-ing
    # the neighbour must not make this bot answer.
    conn = _bare_connection()
    msg = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        {
            "id": "m2", "group_openid": "G1", "content": "<@AAAAAAAAAAAAAAAA>",
            "author": {"member_openid": "speaker"},
            "mentions": [{"member_openid": "AAAAAAAAAAAAAAAA", "bot": True}],
        },
    )
    assert msg["is_at_bot"] is False
    assert conn._bot_openid_by_group == {}


def test_one_groups_learned_openid_does_not_answer_for_another_group():
    conn = _bare_connection()
    conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        {
            "id": "a", "group_openid": "G1", "content": "hi <@BBBBBBBBBBBBBBBB>",
            "author": {"member_openid": "s"},
            "mentions": [{"member_openid": "BBBBBBBBBBBBBBBB", "is_you": True}],
        },
    )
    inline_only_g2 = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        {"id": "b", "group_openid": "G2", "content": "hi <@BBBBBBBBBBBBBBBB>",
         "author": {"member_openid": "s"}},
    )
    # G1 learned its own openid; G2 gets a different conversation openid, so the
    # same token there is not proof this bot was summoned.
    assert inline_only_g2["is_at_bot"] is False
    assert conn._remembered_bot_openid("G1") == "BBBBBBBBBBBBBBBB"
    assert conn._remembered_bot_openid("G2") == ""
    # Once G2 sees its own is_you, later inline-only payloads resolve per group.
    conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        {"id": "c", "group_openid": "G2", "content": "hi <@CCCCCCCCCCCCCCCC>",
         "author": {"member_openid": "s"},
         "mentions": [{"member_openid": "CCCCCCCCCCCCCCCC", "is_you": True}]},
    )
    after = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        {"id": "d", "group_openid": "G2", "content": "hi <@CCCCCCCCCCCCCCCC>",
         "author": {"member_openid": "s"}},
    )
    assert after["is_at_bot"] is True
    assert conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        {"id": "e", "group_openid": "G1", "content": "hi <@CCCCCCCCCCCCCCCC>",
         "author": {"member_openid": "s"}},
    )["is_at_bot"] is False


def test_repeated_inline_at_is_counted_as_one_target():
    conn = _bare_connection()
    conn._learn_bot_openid("G1", "DDDDDDDDDDDDDDDD")
    msg = conn._convert_event(
        "GROUP_MESSAGE_CREATE",
        {"id": "f", "group_openid": "G1", "content": "<@DDDDDDDDDDDDDDDD> <@DDDDDDDDDDDDDDDD>",
         "author": {"member_openid": "s"}},
    )
    # Two occurrences of the same target are one target: otherwise "summoned only
    # me, twice" reads as "named me and someone else".
    assert msg["mentioned_user_ids"] == ["DDDDDDDDDDDDDDDD"]
    assert msg["is_at_bot"] is True
    assert msg["mentions_other_user"] is False


import asyncio


def _queue_connection(size: int) -> QQOpenPlatformConnection:
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = ""
    conn.logger = None
    conn._message_queue = asyncio.Queue(maxsize=size)
    return conn


def _msg(message_id: str, *, at_bot: bool) -> dict:
    return {"message_id": message_id, "is_at_bot": at_bot, "content": message_id}


def test_a_redelivered_id_is_queued_once():
    conn = _queue_connection(4)
    assert conn._enqueue_inbound(_msg("m1", at_bot=True)) is None
    assert conn._enqueue_inbound(_msg("m1", at_bot=True)) == "duplicate"
    assert conn._message_queue.qsize() == 1


def test_an_evicted_message_is_forgotten_so_its_redelivery_still_counts():
    conn = _queue_connection(1)
    assert conn._enqueue_inbound(_msg("old", at_bot=False)) is None
    # The @bot arrival pushes "old" out. "old" must stop being remembered: a
    # redelivery of it is then the only remaining chance to process it, and
    # suppressing it while it is pending nowhere would lose it for good.
    assert conn._enqueue_inbound(_msg("new", at_bot=True)) is None
    assert conn._is_duplicate_inbound("old") is False
    # With a free slot, the redelivery lands.
    conn._message_queue.get_nowait()
    assert conn._enqueue_inbound(_msg("old", at_bot=False)) is None
    assert conn._message_queue.get_nowait()["message_id"] == "old"


def test_a_full_queue_of_at_messages_is_not_cleared_by_ordinary_chatter():
    conn = _queue_connection(2)
    assert conn._enqueue_inbound(_msg("summon-1", at_bot=True)) is None
    assert conn._enqueue_inbound(_msg("summon-2", at_bot=True)) is None
    # Every queued message is an @ and the arrival is chatter: refuse the arrival
    # rather than trade a queued summon for it.
    assert conn._enqueue_inbound(_msg("chatter", at_bot=False)) == "queue_full"
    queued = [conn._message_queue.get_nowait()["message_id"] for _ in range(2)]
    assert queued == ["summon-1", "summon-2"]


def test_ordinary_chatter_yields_to_an_incoming_at():
    conn = _queue_connection(2)
    conn._enqueue_inbound(_msg("chatter-1", at_bot=False))
    conn._enqueue_inbound(_msg("chatter-2", at_bot=False))
    assert conn._enqueue_inbound(_msg("summon", at_bot=True)) is None
    queued = [conn._message_queue.get_nowait()["message_id"] for _ in range(2)]
    assert "summon" in queued
    assert "chatter-1" not in queued       # the oldest chatter went first


def test_full_message_first_does_not_swallow_the_later_at_signal():
    # Greptile r4574476364: with the privilege on, GROUP_MESSAGE_CREATE can land
    # first and read is_at_bot=False because this group's bot openid is not known
    # yet. Dropping the AT frame as a plain duplicate then dropped the summon,
    # and the bot never answered a direct @.
    import asyncio

    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = ""
    conn.logger = None
    conn._message_queue = asyncio.Queue(maxsize=8)
    payload = {
        "id": "summon-1", "group_openid": "G9",
        "content": "喂 <@EEEEEEEEEEEEEEEE>",
        "author": {"member_openid": "MA"},
    }
    at_payload = dict(payload)
    at_payload["mentions"] = [{"member_openid": "EEEEEEEEEEEEEEEE", "is_you": True}]

    async def drive(event_type, body):
        await conn._dispatch_event(event_type, body, "e1")
        await asyncio.sleep(0)

    asyncio.run(drive("GROUP_MESSAGE_CREATE", payload))
    asyncio.run(drive("GROUP_AT_MESSAGE_CREATE", at_payload))

    assert conn._message_queue.qsize() == 1          # still delivered once
    queued = conn._message_queue.get_nowait()
    assert queued["is_at_bot"] is True               # ... and the @ survived
    assert queued["mentions_other_user"] is False    # ... with correct ownership
    assert "EEEEEEEEEEEEEEEE" in queued["mentioned_user_ids"]


def test_at_frame_first_stays_correct_when_the_full_copy_arrives_after():
    import asyncio

    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = ""
    conn.logger = None
    conn._message_queue = asyncio.Queue(maxsize=8)
    payload = {
        "id": "summon-2", "group_openid": "G9", "content": "喂 <@FFFFFFFFFFFFFFFF>",
        "author": {"member_openid": "MA"},
        "mentions": [{"member_openid": "FFFFFFFFFFFFFFFF", "is_you": True}],
    }

    async def drive(body):
        await conn._dispatch_event("GROUP_AT_MESSAGE_CREATE", body, "e1")
        await asyncio.sleep(0)

    asyncio.run(drive(payload))
    asyncio.run(drive(dict(payload, mentions=None)))
    assert conn._message_queue.qsize() == 1
    assert conn._message_queue.get_nowait()["is_at_bot"] is True


def test_full_mode_and_at_event_for_one_message_deliver_once():
    # With the group privilege on, the platform can push both
    # GROUP_AT_MESSAGE_CREATE and GROUP_MESSAGE_CREATE for the same msg_id. Two
    # queue entries mean two replies and a burned passive-reply msg_seq slot.
    import asyncio

    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = ""
    conn.logger = None
    conn._message_queue = asyncio.Queue(maxsize=8)
    payload = {
        "id": "same-msg", "group_openid": "G1",
        "content": "<@BBBBBBBBBBBBBBBB> 在吗",
        "author": {"member_openid": "MA"},
        "mentions": [{"member_openid": "BBBBBBBBBBBBBBBB", "is_you": True}],
    }

    async def drive(event_type):
        await conn._dispatch_event(event_type, payload, "e1")
        await asyncio.sleep(0)

    asyncio.run(drive("GROUP_AT_MESSAGE_CREATE"))
    asyncio.run(drive("GROUP_MESSAGE_CREATE"))
    assert conn._message_queue.qsize() == 1
