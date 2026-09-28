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

"""R11 resolved: where the open platform's speaker id actually comes from.

The payloads below are transcribed from Tencent's own published material and
are the whole reason this file exists -- the bug being pinned here was a
connector reading ``author.id``, a key that does not exist on either of the
two events it handles, which made every speaker collapse into the empty
string in silence.

Sources, both first-party and mutually corroborating:

- ``tencent-connect/bot-docs``,
  ``develop/api-v2/server-inter/message/send-receive/event.md`` -- the field
  tables and the sample JSON for both events;
- ``tencent-connect/botpy``, ``botpy/message.py`` -- ``C2CMessage._User``
  reads only ``user_openid``, ``GroupMessage._User`` only ``member_openid``;
  only the guild-side ``Message._User`` has an ``id``.

See ``docs/design/speaker-trust-entity-semantics.md`` section 2.15.4.
"""

from __future__ import annotations

import pytest

from utils.connection.qq.open_platform import (
    QQOpenPlatformConnection,
    _C2C_ACTOR_ID_KEYS,
    _GROUP_ACTOR_ID_KEYS,
    pick_actor_id,
)


# The vendor's own sample payloads, field for field.
OFFICIAL_C2C_EVENT = {
    "author": {"user_openid": "E4F4AEA33253A2797FB897C50B81D7ED"},
    "content": "123",
    "id": "ROBOT1.0_.b6nx.CVryAO0nR58RXuU6SC.m92gc19j02qKqdm8ek!",
    "timestamp": "2023-11-06T13:37:18+08:00",
}
OFFICIAL_GROUP_EVENT = {
    "author": {"member_openid": "E4F4AEA33253A2797FB897C50B81D7ED"},
    "content": " 123",
    "group_openid": "C9F778FE6ADF9D1D1DBE395BF744A33A",
    "id": "ROBOT1.0_eBIyWnxpmSu6uLQ7u7fU0eGloKGYg4eEa737vRyKnMCgyZjKi7JLYkQ9B0",
    "timestamp": "2023-11-06T13:37:18+08:00",
}


def _connection():
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = ""
    return conn


# ==========================================================================
# A. The extractor, against the vendor's own payloads
# ==========================================================================


def test_official_c2c_payload_yields_the_user_openid():
    message = _connection()._convert_event(
        "C2C_MESSAGE_CREATE", OFFICIAL_C2C_EVENT,
    )

    assert message["user_id"] == "E4F4AEA33253A2797FB897C50B81D7ED"
    assert message["message_type"] == "private"


def test_official_group_payload_yields_the_member_openid_and_group_openid():
    message = _connection()._convert_event(
        "GROUP_AT_MESSAGE_CREATE", OFFICIAL_GROUP_EVENT,
    )

    assert message["user_id"] == "E4F4AEA33253A2797FB897C50B81D7ED"
    assert message["group_id"] == "C9F778FE6ADF9D1D1DBE395BF744A33A"


@pytest.mark.parametrize("event_type,payload", [
    ("C2C_MESSAGE_CREATE", OFFICIAL_C2C_EVENT),
    ("GROUP_AT_MESSAGE_CREATE", OFFICIAL_GROUP_EVENT),
])
def test_no_official_payload_leaves_the_speaker_id_empty(event_type, payload):
    """The regression this whole PR exists for.

    An empty speaker id does not raise anywhere: permissions resolve it to
    ``none``, memory writes it into a subject id, and the sender POSTs to
    ``/v2/users//messages``. Every one of those fails quietly, which is why
    this assertion is worth making separately from the two above.
    """
    message = _connection()._convert_event(event_type, payload)

    assert message["user_id"] != ""


def test_the_two_paths_do_not_read_each_other_s_key():
    """A group event carrying a ``user_openid`` must not be read as one.

    They are different scopes for the same human. Crossing them would merge
    two identities that the platform deliberately keeps apart -- exactly the
    automatic identity merge the design forbids, done by accident.
    """
    group = _connection()._convert_event("GROUP_AT_MESSAGE_CREATE", {
        "author": {"member_openid": "MEMBER_X", "user_openid": "USER_GLOBAL"},
        "group_openid": "GROUP_X",
    })
    c2c = _connection()._convert_event("C2C_MESSAGE_CREATE", {
        "author": {"member_openid": "MEMBER_X", "user_openid": "USER_GLOBAL"},
    })

    assert group["user_id"] == "MEMBER_X"
    assert c2c["user_id"] == "USER_GLOBAL"


def test_id_is_only_a_fallback_never_a_preference():
    """If the protocol ever adds ``id`` back, the documented key still wins."""
    group = _connection()._convert_event("GROUP_AT_MESSAGE_CREATE", {
        "author": {"id": "LEGACY_ID", "member_openid": "MEMBER_X"},
        "group_openid": "GROUP_X",
    })
    c2c = _connection()._convert_event("C2C_MESSAGE_CREATE", {
        "author": {"id": "LEGACY_ID", "user_openid": "USER_1"},
    })

    assert group["user_id"] == "MEMBER_X"
    assert c2c["user_id"] == "USER_1"


def test_id_is_used_when_the_documented_key_is_absent():
    group = _connection()._convert_event("GROUP_AT_MESSAGE_CREATE", {
        "author": {"id": "ONLY_ID"}, "group_openid": "GROUP_X",
    })

    assert group["user_id"] == "ONLY_ID"


@pytest.mark.parametrize("author", [
    None, {}, "not-a-dict", 42, {"member_openid": ""}, {"member_openid": "   "},
])
def test_missing_or_blank_author_degrades_to_empty_without_raising(author):
    assert pick_actor_id(author, _GROUP_ACTOR_ID_KEYS) == ""
    assert pick_actor_id(author, _C2C_ACTOR_ID_KEYS) == ""


def test_key_order_pins_the_documented_key_first():
    # Reordering these tuples silently reintroduces the bug, and every test
    # above would still pass on payloads that carry only one of the keys.
    assert _GROUP_ACTOR_ID_KEYS[0] == "member_openid"
    assert _C2C_ACTOR_ID_KEYS[0] == "user_openid"
    assert "user_openid" not in _GROUP_ACTOR_ID_KEYS
    assert "member_openid" not in _C2C_ACTOR_ID_KEYS


# ==========================================================================
# D. The manual-assertion surface (design section 2.15.4.3, level 1)
# ==========================================================================


# -- rebinding, and the two ways a "harmless" call is not harmless ----------


# -- which transport the scope describes ------------------------------------
