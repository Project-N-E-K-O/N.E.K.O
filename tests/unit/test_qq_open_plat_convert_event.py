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
"""

import pytest

from plugin.plugins.qq_auto_reply.qq_open_plat import QQOpenPlatformConnection


def _connection() -> QQOpenPlatformConnection:
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = "bot-self"
    return conn


def _group_payload(**group_keys) -> dict:
    payload = {
        "id": "msg-1",
        "content": "<@!bot-self> zaima",
        "author": {"id": "user-1", "username": "neko"},
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
            "author": {"id": "user-1", "username": "neko"},
            "group_openid": "G-openid",
        },
    )

    assert msg is not None
    assert msg["message_type"] == "private"
    assert msg["group_id"] == ""
