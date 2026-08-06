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

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugin.plugins.qq_auto_reply.dashboard_service import QQDashboardService
from plugin.plugins.qq_auto_reply.message_dispatcher import QQMessageDispatcher
from plugin.plugins.qq_auto_reply.qq_open_plat import (
    QQOpenPlatformConnection,
    _C2C_ACTOR_ID_KEYS,
    _GROUP_ACTOR_ID_KEYS,
    pick_actor_id,
)
from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService


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
# B. The protocol table that gets declared to the trust pool
# ==========================================================================


def test_open_platform_is_declared_per_conversation_on_the_actor_axis():
    channel, actor_scope, conversation_scope = (
        QQSettingsService.IDENTITY_SCOPE_BY_MODE["open_platform"]
    )

    assert channel == "open"
    # Tencent's "unique identity" page: the same person's member_openid
    # differs per group for one and the same bot.
    assert actor_scope == "per_conversation"
    # group_openid is one-per-group, not one-per-group-per-person -- the
    # asymmetry that makes the conversation side rescuable.
    assert conversation_scope == "global"


def test_napcat_stays_global_on_both_axes():
    channel, actor_scope, conversation_scope = (
        QQSettingsService.IDENTITY_SCOPE_BY_MODE["napcat"]
    )

    assert (channel, actor_scope, conversation_scope) == (
        "napcat", "global", "global",
    )


def test_every_declared_mode_names_its_protocol_as_the_asserter():
    """``asserted_by`` must say which protocol, not "code".

    The whole value of this container is that a reader can tell a transcribed
    vendor contract apart from something the process inferred from traffic.
    """
    for mode in QQSettingsService.IDENTITY_SCOPE_BY_MODE:
        asserter = QQSettingsService.IDENTITY_SCOPE_ASSERTED_BY[mode]
        assert asserter.startswith("protocol:")


# ==========================================================================
# C. The pending-claim pool
# ==========================================================================


def _dispatcher():
    dispatcher = QQMessageDispatcher.__new__(QQMessageDispatcher)
    dispatcher.plugin = SimpleNamespace(logger=MagicMock(), _emit_log=MagicMock())
    dispatcher._open_platform_pending_claims = {}
    return dispatcher


def _speak(dispatcher, *, sender, group="GROUP_X", level="none",
           channel="open", nickname=""):
    dispatcher._note_open_platform_pending_claim({
        "message_type": "group",
        "channel": channel,
        "group_id": group,
        "user_id": sender,
        "user_nickname": nickname,
    }, level)


def test_an_unknown_speaker_becomes_a_claim():
    dispatcher = _dispatcher()
    _speak(dispatcher, sender="MEMBER_X", nickname="张三")

    claims = dispatcher.list_open_platform_pending_claims()
    assert [(row["group_id"], row["user_id"], row["nickname"]) for row in claims] == [
        ("GROUP_X", "MEMBER_X", "张三"),
    ]
    assert claims[0]["message_count"] == 1


def test_repeated_speech_counts_up_instead_of_duplicating():
    dispatcher = _dispatcher()
    for _ in range(5):
        _speak(dispatcher, sender="MEMBER_X")

    claims = dispatcher.list_open_platform_pending_claims()
    assert len(claims) == 1
    assert claims[0]["message_count"] == 5


def test_a_claimed_speaker_leaves_the_list():
    dispatcher = _dispatcher()
    _speak(dispatcher, sender="MEMBER_X")
    _speak(dispatcher, sender="MEMBER_X", level="trusted")

    assert dispatcher.list_open_platform_pending_claims() == []


def test_the_same_person_in_two_groups_is_two_claims():
    """Not a bug to be deduplicated: they ARE two different ids.

    Collapsing them here would be an automatic identity merge inferred from a
    shared nickname, which the design rules out on the grounds that a wrong
    merge pollutes the ledger irreversibly.
    """
    dispatcher = _dispatcher()
    _speak(dispatcher, sender="MEMBER_IN_X", group="GROUP_X", nickname="张三")
    _speak(dispatcher, sender="MEMBER_IN_Y", group="GROUP_Y", nickname="张三")

    claims = dispatcher.list_open_platform_pending_claims()
    assert len(claims) == 2


def test_napcat_traffic_never_lands_in_the_pool():
    dispatcher = _dispatcher()
    _speak(dispatcher, sender="123456", channel="napcat")

    assert dispatcher.list_open_platform_pending_claims() == []


def test_the_pool_is_bounded_per_group():
    dispatcher = _dispatcher()
    cap = QQMessageDispatcher.OPEN_PLATFORM_CLAIM_MAX_PER_GROUP
    for index in range(cap + 10):
        _speak(dispatcher, sender=f"MEMBER_{index}")

    claims = dispatcher.list_open_platform_pending_claims()
    assert len(claims) == cap
    # The newest arrivals survive; the pool is a to-do list, not a ledger.
    assert any(row["user_id"] == f"MEMBER_{cap + 9}" for row in claims)


def test_the_pool_is_bounded_across_groups():
    dispatcher = _dispatcher()
    cap = QQMessageDispatcher.OPEN_PLATFORM_CLAIM_MAX_GROUPS
    for index in range(cap + 10):
        _speak(dispatcher, sender="MEMBER", group=f"GROUP_{index}")

    groups = {
        row["group_id"]
        for row in dispatcher.list_open_platform_pending_claims()
    }
    assert len(groups) == cap


def test_a_group_without_an_id_is_ignored():
    dispatcher = _dispatcher()
    _speak(dispatcher, sender="MEMBER_X", group="")

    assert dispatcher.list_open_platform_pending_claims() == []


@pytest.mark.parametrize("message", [None, "", 42, []])
def test_observation_never_propagates_an_exception(message):
    """A diagnostic that can take the message pipeline down is worse than none."""
    dispatcher = _dispatcher()

    dispatcher._note_open_platform_pending_claim(message, "none")  # must not raise

    assert dispatcher.list_open_platform_pending_claims() == []


# ==========================================================================
# D. The manual-assertion surface (design section 2.15.4.3, level 1)
# ==========================================================================


def _dashboard(*, roster, profiles, claims=(), mode="open_platform"):
    service = QQDashboardService.__new__(QQDashboardService)
    dispatcher = _dispatcher()
    for row in claims:
        _speak(dispatcher, sender=row[0], group=row[1], nickname=row[2])
    bridge = SimpleNamespace(
        speaker_account_id=lambda actor: f"qq:{str(actor or '').strip()}",
        fetch_speaker_profile=AsyncMock(
            side_effect=lambda account_id: dict(profiles[account_id]),
        ),
    )
    service.plugin = SimpleNamespace(
        message_dispatcher=dispatcher,
        memory_bridge=bridge,
        permission_mgr=SimpleNamespace(list_users=lambda: list(roster)),
        settings_service=QQSettingsService,
        _qq_settings={"qq_connection_mode": mode},
        i18n=SimpleNamespace(t=lambda key, default="", **kw: default),
    )
    return service


async def test_merge_candidates_are_ranked_by_ledger_weight_only():
    """Never by name similarity, and never pre-selected.

    Ranking by nickname would hand the operator the exact heuristic the design
    rules out (automatic identity merge) dressed up as the default answer, and
    a wrong merge pollutes the ledger irreversibly. So the candidate whose
    nickname matches the claim EXACTLY must still lose to the one carrying the
    heavier ledger.
    """
    service = _dashboard(
        roster=[
            {"qq": "SAME_NICKNAME_STRANGER", "level": "trusted",
             "nickname": "张三"},
            {"qq": "OWNER_PRIVATE_OPENID", "level": "admin", "nickname": "李四"},
        ],
        profiles={
            "qq:SAME_NICKNAME_STRANGER": {
                "entity_id": "entity_stranger",
                "adjustment_sum": 0.0, "account_message_count": 0,
            },
            "qq:OWNER_PRIVATE_OPENID": {
                "entity_id": "entity_owner",
                "adjustment_sum": 0.4, "account_message_count": 900,
            },
        },
        claims=[("MEMBER_IN_X", "GROUP_X", "张三")],
    )

    payload = (await service.list_identity_claims()).value

    assert [row["qq"] for row in payload["candidates"]] == [
        "OWNER_PRIVATE_OPENID", "SAME_NICKNAME_STRANGER",
    ]
    assert payload["claims"][0]["user_id"] == "MEMBER_IN_X"


async def test_a_negative_ledger_still_ranks_by_magnitude():
    """``|adjustment|``: a heavily-corrected account is a strong candidate too."""
    service = _dashboard(
        roster=[
            {"qq": "QUIET", "level": "trusted", "nickname": ""},
            {"qq": "CORRECTED_A_LOT", "level": "trusted", "nickname": ""},
        ],
        profiles={
            "qq:QUIET": {
                "entity_id": "entity_quiet",
                "adjustment_sum": 0.05, "account_message_count": 1,
            },
            "qq:CORRECTED_A_LOT": {
                "entity_id": "entity_corrected",
                "adjustment_sum": -0.6, "account_message_count": 30,
            },
        },
    )

    payload = (await service.list_identity_claims()).value

    assert payload["candidates"][0]["qq"] == "CORRECTED_A_LOT"


async def test_the_roster_still_lists_when_the_server_is_unreachable():
    """Weight is for ordering; losing it must not empty the list.

    The operator recognises "the account I authorised in DMs" by its id and
    level, and that recognition is the whole assertion. Hiding the roster
    because memory_server is down would block the one repair path there is.
    """
    service = _dashboard(
        roster=[{"qq": "OWNER_PRIVATE_OPENID", "level": "admin", "nickname": ""}],
        profiles={},
    )
    service.plugin.memory_bridge.fetch_speaker_profile = AsyncMock(
        side_effect=RuntimeError("connection refused"),
    )

    payload = (await service.list_identity_claims()).value

    assert [row["qq"] for row in payload["candidates"]] == ["OWNER_PRIVATE_OPENID"]
    assert payload["candidates"][0]["entity_id"] is None


async def test_binding_refuses_a_blank_side():
    service = _dashboard(roster=[], profiles={})

    for args in ({"user_id": "", "entity_id": "e1"},
                 {"user_id": "MEMBER_X", "entity_id": " "}):
        result = await service.bind_identity_account(**args)
        assert result.__class__.__name__ == "Err"


async def test_binding_composes_the_account_id_through_the_bridge():
    """The platform prefix lives in exactly one place; callers never spell it."""
    service = _dashboard(roster=[], profiles={})
    service.plugin.memory_bridge.bind_speaker_account = AsyncMock(
        return_value={"entity_id": "entity_owner", "persisted": True},
    )

    await service.bind_identity_account(
        user_id="MEMBER_IN_X", entity_id="entity_owner",
    )

    kwargs = service.plugin.memory_bridge.bind_speaker_account.await_args.kwargs
    assert kwargs["account_id"] == "qq:MEMBER_IN_X"
    assert kwargs["entity_id"] == "entity_owner"
    assert kwargs["bound_by"]


def test_the_dashboard_reports_the_degradation_only_on_the_open_platform():
    open_scope = _dashboard(
        roster=[], profiles={}, mode="open_platform",
    )._identity_scope_payload()
    napcat_scope = _dashboard(
        roster=[], profiles={}, mode="napcat",
    )._identity_scope_payload()

    assert open_scope["actor_scope"] == "per_conversation"
    assert napcat_scope["actor_scope"] == "global"


def test_an_unknown_connection_mode_says_unknown_rather_than_guessing():
    scope = _dashboard(
        roster=[], profiles={}, mode="something_new",
    )._identity_scope_payload()

    assert scope["actor_scope"] == "unknown"
    assert scope["conversation_scope"] == "unknown"
