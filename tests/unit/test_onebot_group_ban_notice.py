"""``group_ban`` notices: a **third party's** ban/unban must travel upstream.

Before this change the connector dropped that case outright:

    if not is_whole_group and not is_self:
        return  # someone else muted; not our concern

so the plugin could never learn that someone in the group had been muted, and reacting to
"the person she is talking with got muted" was not implementable. Now a third party's
ban/unban is enqueued (the plugin decides whether to say anything), while her own or a
whole-group mute stays local bookkeeping only -- she cannot speak in that state anyway.

The normalization is pinned here too: a poke arrives as ``notice_type=notify,
sub_type=poke``, whereas ``group_ban``'s ``sub_type`` is ``ban``/``lift_ban`` (which kind
of ban it is, not which kind of event). Taking the event name from ``sub_type`` would make
the notice unrecognizable upstream.

The plugin repository carries a fallback copy of this connector
(``_vendor/connection_onebot/onebot_client.py``) whose shape is guarded by
``tests/test_qq_group_ban_notice.py``; the two must change together.
Follows ``tests/unit`` conventions: sync tests via ``asyncio.run``, no real sockets.
"""

from __future__ import annotations

import asyncio
import json

from utils.connection.onebot.onebot_client import OneBotClient

GROUP = "1048307485"
ALICE = "1782348687"
ADMIN = "10001"
BOT = "3281414178"


def _client(*, self_id: str = BOT) -> OneBotClient:
    """A client with no sockets: only the queue + mute bookkeeping matter here."""
    client = OneBotClient(onebot_url="ws://127.0.0.1:3001", direction="forward")
    client._message_queue = asyncio.Queue()
    client._self_id = self_id
    return client


def _feed(client: OneBotClient, payload: dict) -> None:
    asyncio.run(client._process_incoming(json.dumps(payload)))


def _take(client: OneBotClient) -> dict:
    return asyncio.run(client.receive_message(timeout=0.05))


def _ban(*, user_id: str, sub_type: str = "ban", duration: int = 600, self_id: str = BOT) -> dict:
    """A raw OneBot event: every event carries the bot's own ``self_id``."""
    return {
        "post_type": "notice", "notice_type": "group_ban", "sub_type": sub_type,
        "group_id": GROUP, "user_id": user_id, "operator_id": ADMIN,
        "duration": duration, "time": 1_790_000_000, "self_id": self_id,
    }


# ---- third party: enqueued + normalized -------------------------------


def test_a_third_party_ban_is_enqueued_and_normalized():
    client = _client()
    _feed(client, _ban(user_id=ALICE))

    assert client._message_queue.qsize() == 1, "someone else's ban was dropped again"

    notice = _take(client)
    assert notice["notice_type"] == "group_ban", "the event kind was not extracted"
    assert notice["sub_type"] == "ban"
    assert notice["user_id"] == ALICE
    assert notice["operator_id"] == ADMIN
    assert notice["duration"] == 600
    assert notice["group_id"] == GROUP


def test_a_third_party_lift_ban_is_enqueued_too():
    client = _client()
    _feed(client, _ban(user_id=ALICE, sub_type="lift_ban", duration=0))

    notice = _take(client)
    assert notice["notice_type"] == "group_ban"
    assert notice["sub_type"] == "lift_ban"


# ---- self / whole group: bookkeeping only -----------------------------


def test_her_own_ban_is_tracked_but_not_enqueued():
    client = _client(self_id=ALICE)
    _feed(client, _ban(user_id=ALICE))

    assert client._message_queue.qsize() == 0
    assert client.is_group_muted(GROUP) is True


def test_whole_group_ban_is_tracked_but_not_enqueued():
    client = _client()
    _feed(client, _ban(user_id="0", duration=0))

    assert client._message_queue.qsize() == 0
    assert client.is_group_muted(GROUP) is True


def test_her_own_lift_ban_clears_the_flag_without_enqueueing():
    client = _client(self_id=ALICE)
    _feed(client, _ban(user_id=ALICE))
    _feed(client, _ban(user_id=ALICE, sub_type="lift_ban", duration=0))

    assert client._message_queue.qsize() == 0
    assert client.is_group_muted(GROUP) is False


# ---- poke normalization unchanged ------------------------------------


def test_poke_notice_normalization_is_unchanged():
    client = _client()
    _feed(client, {
        "post_type": "notice", "notice_type": "notify", "sub_type": "poke",
        "group_id": GROUP, "user_id": ALICE, "target_id": BOT, "time": 1_790_000_000,
    })

    notice = _take(client)
    assert notice["notice_type"] == "poke"
    assert notice["user_id"] == ALICE
    assert notice["target_id"] == BOT


def test_other_notices_are_still_dropped():
    client = _client()
    _feed(client, {
        "post_type": "notice", "notice_type": "group_card", "sub_type": "group_card",
        "group_id": GROUP, "user_id": ALICE,
    })

    assert client._message_queue.qsize() == 0


# ---- identity: her own mute, before the client has learned its own id ----------


def test_her_own_ban_before_login_info_is_still_hers():
    """A ban notice can arrive before ``get_login_info`` (or any group message).

    `_self_id` is normally set while a message passes through ``receive_message()``, so at
    this point it is still empty. Classifying by that empty id made her own mute look like
    a third party's: the notice got enqueued (wrong) and `_group_muted` never updated
    (also wrong -- she would keep trying to speak in a group where she is muted). The
    notice carries `self_id`, so it is the identity source here.
    """
    client = _client(self_id="")
    assert client._self_id == "", "the fixture starts without an identity"

    _feed(client, _ban(user_id=BOT))

    assert client._message_queue.qsize() == 0, "her own mute was forwarded as a third party"
    assert client.is_group_muted(GROUP) is True, "her own mute was not tracked"
    assert client._self_id == BOT, "the notice did not teach the client its own id"


def test_a_third_party_ban_with_an_unknown_identity_is_still_forwarded():
    """The same notice shape, but the muted user is somebody else: forward it."""
    client = _client(self_id="")
    assert client._self_id == ""

    _feed(client, _ban(user_id=ALICE))

    assert client._message_queue.qsize() == 1
    assert client.is_group_muted(GROUP) is False, "a third party's mute must not mute the bot"
    assert client._self_id == BOT


def test_whole_group_ban_before_login_info_is_tracked():
    client = _client(self_id="")
    _feed(client, _ban(user_id="0", duration=0))

    assert client._message_queue.qsize() == 0
    assert client.is_group_muted(GROUP) is True


# ---- the registered inbound sink sees the notice too --------------------------


def test_the_normalized_notice_reaches_the_registered_sink():
    """A sink-only consumer must see notices: the message path dispatches every message.

    Without this the ban notice (the one this change newly forwards) would only reach
    consumers that poll ``receive_message()`` themselves, and a plugin that attached a
    sink via ``set_inbound_sink`` would never learn that anyone was muted.
    """
    seen: list[dict] = []

    async def sink(message):
        seen.append(message)

    client = _client()
    client.set_inbound_sink(sink)
    # Feeds **before** the loop starts: `_feed` calls ``asyncio.run`` itself, and this
    # repo's ``tests/conftest.py`` patches that call to allow nesting (for the Playwright
    # greenlet) while a plain suite -- the plugin's, for one -- does not. Keeping the two
    # apart is what makes this test mean the same thing in both places.
    _feed(client, _ban(user_id=ALICE))

    async def scenario():
        notice = await client.receive_message(timeout=0.05)
        await asyncio.sleep(0.05)          # the sink runs on its own task
        return notice

    notice = asyncio.run(scenario())

    assert notice["notice_type"] == "group_ban"
    assert len(seen) == 1, "the sink never saw the notice"
    assert seen[0]["notice_type"] == "group_ban"
    assert seen[0]["user_id"] == ALICE
    assert seen[0]["sub_type"] == "ban"


def test_a_poke_notice_reaches_the_sink_too():
    """Same branch, same rule -- poke notices were equally invisible to sinks."""
    seen: list[dict] = []

    async def sink(message):
        seen.append(message)

    client = _client()
    client.set_inbound_sink(sink)
    _feed(client, {
        "post_type": "notice", "notice_type": "notify", "sub_type": "poke",
        "group_id": GROUP, "user_id": ALICE, "target_id": BOT, "time": 1_790_000_000,
    })

    async def scenario():
        await client.receive_message(timeout=0.05)
        await asyncio.sleep(0.05)
        return seen

    seen = asyncio.run(scenario())

    assert [item["notice_type"] for item in seen] == ["poke"]


# ---- an unparsable duration never escapes receive_message() ------------------


def test_an_unparsable_duration_does_not_raise_out_of_the_receive_loop():
    """``receive_message()`` only catches the queue timeout, so a ``ValueError`` from
    ``int("600s")`` would escape into the consumer's receive loop."""
    client = _client()
    for raw, expected in (("600s", 600), ("1.5", 1), ("abc", 0), (None, 0)):
        payload = _ban(user_id=ALICE)
        payload["duration"] = raw
        _feed(client, payload)
        notice = _take(client)
        assert notice["duration"] == expected, (raw, notice["duration"])


def test_an_overflowing_duration_is_zero_not_an_exception():
    """``"9" * 400`` matches the number pattern but overflows ``int(float(...))``;
    ``_process_incoming`` calls the parser before enqueueing, so raising there would
    drop the notice and leave her own mute unrecorded."""
    client = _client()
    payload = _ban(user_id=BOT)
    payload["duration"] = "9" * 400
    _feed(client, payload)

    assert client.is_group_muted(GROUP) is True

    third = _ban(user_id=ALICE)
    third["duration"] = "9" * 400
    _feed(client, third)
    assert _take(client)["duration"] == 0


def test_an_unparsable_duration_on_her_own_ban_still_mutes_her():
    client = _client()
    payload = _ban(user_id=BOT)
    payload["duration"] = "not-a-number"
    _feed(client, payload)

    assert client._message_queue.qsize() == 0
    assert client.is_group_muted(GROUP) is True


def test_a_notice_carries_an_empty_content():
    """Sink consumers read ``content`` off every inbound dict; a notice has none."""
    client = _client()
    _feed(client, _ban(user_id=ALICE))

    assert _take(client)["content"] == ""
