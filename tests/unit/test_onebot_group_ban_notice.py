"""`group_ban` 通知：**第三方**被禁言/解禁要往上游送（改之前是被丢掉的）。

使用者口径（2026-09-29）：「可以让猫娘对正在聊天的对象的禁言做出反应吗」——
要回答这个问题，插件首先得**看得到**这件事；而改之前连接层在这里直接 return：

    if not is_whole_group and not is_self:
        return  # someone else muted; not our concern

于是插件永远不知道群里有人被禁言。现在：第三方 → 入队（由插件判断要不要开口）；
她自己 / 全员被禁言 → 仍然只做本地记账（那种情况她也发不出去），不入队。

归一化那一处也一并守着：poke 的形状是 ``notice_type=notify, sub_type=poke``，
而 group_ban 的 sub_type 是 ban/lift_ban（那是"哪一种禁言"，不是"哪一类事件"），
所以事件名不能取 sub_type —— 否则上游认不出这是禁言通知。

插件仓库里那份 `_vendor/connection_onebot/onebot_client.py` 是回退副本，
`tests/test_qq_group_ban_notice.py` 守着同一份形状；两份必须同改。
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


def _ban(*, user_id: str, sub_type: str = "ban", duration: int = 600) -> dict:
    return {
        "post_type": "notice", "notice_type": "group_ban", "sub_type": sub_type,
        "group_id": GROUP, "user_id": user_id, "operator_id": ADMIN,
        "duration": duration, "time": 1_790_000_000,
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
