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

import asyncio
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


_BOUND = {"entity_id": "entity_owner",
          "entity_accounts": ["qq:MEMBER_IN_X", "qq:OWNER_PRIVATE_OPENID"],
          "bound_by": "qq_auto_reply.dashboard",
          "adjustment_sum": 0.0, "account_message_count": 0}
_STANDALONE_WITH_LEDGER = {"entity_id": "entity_solo",
                           "entity_accounts": ["qq:MEMBER_IN_X"],
                           "bound_by": None,
                           "adjustment_sum": 0.3,
                           "account_message_count": 40}


def _dashboard(*, roster, profiles, claims=(), mode="open_platform"):
    service = QQDashboardService.__new__(QQDashboardService)
    dispatcher = _dispatcher()
    for row in claims:
        _speak(dispatcher, sender=row[0], group=row[1], nickname=row[2])
    levels = {str(user.get("qq")): str(user.get("level")) for user in roster}
    bridge = SimpleNamespace(
        speaker_account_id=lambda actor: f"qq:{str(actor or '').strip()}",
        fetch_speaker_profile=AsyncMock(
            side_effect=lambda account_id, **kw: dict(
                profiles.get(account_id, {}),
            ),
        ),
        ensure_speaker_account=AsyncMock(
            side_effect=lambda account_id, **kw: {
                "account_id": account_id,
                "entity_id": f"entity_for_{account_id}",
            },
        ),
        bind_speaker_account=AsyncMock(
            return_value={"entity_id": "entity_x", "persisted": True},
        ),
        unbind_speaker_account=AsyncMock(
            return_value={"changed": True, "ledger_delta": -0.1,
                          "effective_delta": 0.3, "persisted": True},
        ),
    )
    service.plugin = SimpleNamespace(
        message_dispatcher=dispatcher,
        memory_bridge=bridge,
        permission_mgr=SimpleNamespace(
            list_users=lambda: list(roster),
            get_permission_level=lambda actor: levels.get(str(actor), "none"),
        ),
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


async def test_weights_are_fetched_concurrently_with_a_short_timeout():
    """Serial fetches would blow the frontend's fixed 20s ``call()`` deadline.

    A roster of N behind a stalled connection takes N x timeout serially, and
    the page then reports a load failure instead of degrading to an unordered
    roster -- which defeats the fallback above. Both halves matter: the
    concurrency AND a per-request timeout well under the page deadline.
    """
    roster = [
        {"qq": f"USER_{index}", "level": "trusted", "nickname": ""}
        for index in range(8)
    ]
    service = _dashboard(roster=roster, profiles={})
    in_flight = 0
    peak = 0

    async def _slow(account_id, **kwargs):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0)
            return {"entity_id": None, "adjustment_sum": 0.0,
                    "account_message_count": 0}
        finally:
            in_flight -= 1

    service.plugin.memory_bridge.fetch_speaker_profile = AsyncMock(
        side_effect=_slow,
    )

    await service.list_identity_claims()

    assert peak > 1, "profile fetches ran one after another"
    timeouts = {
        call.kwargs.get("timeout")
        for call in service.plugin.memory_bridge.fetch_speaker_profile.await_args_list
    }
    assert timeouts == {QQDashboardService.IDENTITY_CANDIDATE_TIMEOUT}
    # 20s is the page's own deadline (open_platform.html's ``call()``).
    assert QQDashboardService.IDENTITY_CANDIDATE_TIMEOUT < 20


async def test_a_claimed_id_leaves_the_list_without_waiting_for_another_message():
    """Otherwise the operator refreshes, sees the same row, and claims twice.

    Removal on the message path alone only fires when that person speaks
    again, which may be never.
    """
    service = _dashboard(
        roster=[{"qq": "MEMBER_IN_X", "level": "trusted", "nickname": ""}],
        profiles={"qq:MEMBER_IN_X": {"entity_id": "e1", "adjustment_sum": 0.0,
                                     "account_message_count": 0}},
        claims=[("MEMBER_IN_X", "GROUP_X", ""),
                ("STRANGER", "GROUP_X", "")],
    )

    payload = (await service.list_identity_claims()).value

    assert [row["user_id"] for row in payload["claims"]] == ["STRANGER"]
    # And it is gone from the pool, not merely filtered out of one response.
    assert [
        row["user_id"] for row in
        service.plugin.message_dispatcher.list_open_platform_pending_claims()
    ] == ["STRANGER"]


async def test_binding_refuses_a_blank_side():
    service = _dashboard(roster=[], profiles={})

    for args in ({"user_id": "", "target_user_id": "OWNER"},
                 {"user_id": "MEMBER_X", "target_user_id": " "}):
        result = await service.bind_identity_account(**args)
        assert result.__class__.__name__ == "Err"


async def test_binding_refuses_to_merge_an_id_into_itself():
    service = _dashboard(roster=[], profiles={})

    result = await service.bind_identity_account(
        user_id="MEMBER_X", target_user_id="MEMBER_X",
    )

    assert result.__class__.__name__ == "Err"
    service.plugin.memory_bridge.bind_speaker_account.assert_not_awaited()


async def test_binding_seeds_the_target_entity_before_linking():
    """The owner authorised in DMs usually has NO entity yet.

    Entities are born from ledger activity; ``add_trusted_user`` only touches
    the permission roster. On a fresh install, or with the memory opt-ins off,
    the private-chat admin -- the very account every in-group openid needs to
    merge into -- has none, and ``bind`` 404s on an unknown entity. Taking a
    target ACCOUNT and seeding it first is what keeps that case selectable.
    """
    service = _dashboard(
        roster=[{"qq": "OWNER_PRIVATE_OPENID", "level": "admin",
                 "nickname": ""}],
        profiles={},
    )

    await service.bind_identity_account(
        user_id="MEMBER_IN_X", target_user_id="OWNER_PRIVATE_OPENID",
    )

    ensured = service.plugin.memory_bridge.ensure_speaker_account.await_args.kwargs
    assert ensured["account_id"] == "qq:OWNER_PRIVATE_OPENID"
    bound = service.plugin.memory_bridge.bind_speaker_account.await_args.kwargs
    # The platform prefix lives in exactly one place; callers never spell it.
    assert bound["account_id"] == "qq:MEMBER_IN_X"
    assert bound["entity_id"] == "entity_for_qq:OWNER_PRIVATE_OPENID"
    assert bound["bound_by"]


async def test_unbinding_is_reachable_and_targets_the_in_group_id():
    """A bind that cannot be undone in the same surface is a trap.

    Binding combines both accounts' trust immediately, so the rollback has to
    sit next to it rather than in an internal endpoint the operator would
    have to discover.
    """
    service = _dashboard(roster=[], profiles={"qq:MEMBER_IN_X": _BOUND})

    result = await service.unbind_identity_account(user_id="MEMBER_IN_X")

    kwargs = service.plugin.memory_bridge.unbind_speaker_account.await_args.kwargs
    assert kwargs["account_id"] == "qq:MEMBER_IN_X"
    # Both deltas are passed through untouched: under a clamped aggregate they
    # are legitimately different numbers, and an operator handed only one of
    # them cannot reconcile it with the score.
    assert result.value["unbind"]["ledger_delta"] == -0.1
    assert result.value["unbind"]["effective_delta"] == 0.3


async def test_unbinding_refuses_a_blank_id():
    service = _dashboard(roster=[], profiles={})

    result = await service.unbind_identity_account(user_id="  ")

    assert result.__class__.__name__ == "Err"
    service.plugin.memory_bridge.unbind_speaker_account.assert_not_awaited()


# -- rebinding, and the two ways a "harmless" call is not harmless ----------

async def test_rebinding_an_already_bound_id_is_refused():
    """Rebinding is not "pick a different target" -- it MERGES the two targets.

    ``_bind_locked`` takes the merge branch when the source already belongs to
    an entity, so a second bind fuses candidate A's identity with candidate
    B's: two different people, one identity. Unbind only detaches the source,
    so those two stay fused with no way back. The operator must undo first.
    """
    service = _dashboard(
        roster=[], profiles={"qq:MEMBER_IN_X": _BOUND},
    )

    result = await service.bind_identity_account(
        user_id="MEMBER_IN_X", target_user_id="SOMEONE_ELSE",
    )

    assert result.__class__.__name__ == "Err"
    service.plugin.memory_bridge.bind_speaker_account.assert_not_awaited()
    service.plugin.memory_bridge.ensure_speaker_account.assert_not_awaited()


async def test_unbinding_a_standalone_account_with_a_ledger_is_not_attempted():
    """``changed=false`` only covers the never-registered case.

    ``_unbind_locked`` calls any REGISTERED account changed and moves it into
    a generation+1 entity, stranding rows already resolved under the old one
    and minting a fresh entity on every press. The UI tells operators this
    button is safe on an unmerged ID, so the guard has to be real.
    """
    service = _dashboard(
        roster=[], profiles={"qq:MEMBER_IN_X": _STANDALONE_WITH_LEDGER},
    )

    result = await service.unbind_identity_account(user_id="MEMBER_IN_X")

    assert result.value["unbind"]["changed"] is False
    service.plugin.memory_bridge.unbind_speaker_account.assert_not_awaited()


@pytest.mark.parametrize("profile,why", [
    ({"entity_accounts": ["qq:MEMBER_IN_X", "qq:OWNER"], "bound_by": None},
     "shares its entity with another account"),
    ({"entity_accounts": ["qq:MEMBER_IN_X"],
      "bound_by": "qq_auto_reply.dashboard"},
     "carries bind provenance after the co-tenant was detached"),
])
async def test_either_signal_alone_blocks_a_rebind(profile, why):
    """Both halves of the BIND predicate are load-bearing, so test them apart.

    A co-tenant with no provenance happens when the ledger merged accounts by
    another route; provenance with no co-tenant is what a bind leaves behind
    once the OTHER side is unbound. Checking only one lets a rebind through in
    the case the other one covers -- and a rebind fuses two candidates.
    """
    service = _dashboard(
        roster=[{"qq": "SOMEONE_ELSE", "level": "trusted", "nickname": ""}],
        profiles={"qq:MEMBER_IN_X": profile},
    )

    result = await service.bind_identity_account(
        user_id="MEMBER_IN_X", target_user_id="SOMEONE_ELSE",
    )

    assert result.__class__.__name__ == "Err", why
    service.plugin.memory_bridge.bind_speaker_account.assert_not_awaited()


async def test_a_fresh_unmerged_account_is_not_treated_as_bound():
    """The predicate must not block the ordinary first bind."""
    service = _dashboard(
        roster=[{"qq": "OWNER", "level": "admin", "nickname": ""}],
        profiles={"qq:MEMBER_IN_X": {"entity_accounts": ["qq:MEMBER_IN_X"],
                                     "bound_by": None},
                  "qq:OWNER": {}},
    )

    result = await service.bind_identity_account(
        user_id="MEMBER_IN_X", target_user_id="OWNER",
    )

    assert result.__class__.__name__ == "Ok"
    service.plugin.memory_bridge.bind_speaker_account.assert_awaited_once()


async def test_the_bind_delegates_the_real_guard_to_the_critical_section():
    """The preflight check races; only the trust-store one cannot go stale.

    Two tabs binding the same loose source concurrently both see "unbound",
    and the second bind then takes the merge branch and fuses two candidate
    entities irreversibly. The preflight exists for the error message.
    """
    service = _dashboard(
        roster=[{"qq": "OWNER", "level": "admin", "nickname": ""}],
        profiles={"qq:MEMBER_IN_X": {"entity_accounts": [], "bound_by": None},
                  "qq:OWNER": {}},
    )

    await service.bind_identity_account(
        user_id="MEMBER_IN_X", target_user_id="OWNER",
    )

    kwargs = service.plugin.memory_bridge.bind_speaker_account.await_args.kwargs
    assert kwargs["require_unbound"] is True


async def test_the_merge_target_must_still_be_on_the_roster():
    """A stale tab -- or a typo in the generic entry form -- must not invent one.

    ``ensure_speaker_account`` happily mints an entity for any string, and the
    bind then succeeds, moving the source's ledger into an identity that
    corresponds to nobody.
    """
    service = _dashboard(
        roster=[{"qq": "OWNER", "level": "admin", "nickname": ""}],
        profiles={"qq:MEMBER_IN_X": {"entity_accounts": [], "bound_by": None}},
    )

    result = await service.bind_identity_account(
        user_id="MEMBER_IN_X", target_user_id="REMOVED_OR_TYPOD",
    )

    assert result.__class__.__name__ == "Err"
    service.plugin.memory_bridge.ensure_speaker_account.assert_not_awaited()
    service.plugin.memory_bridge.bind_speaker_account.assert_not_awaited()


async def test_the_merge_TARGET_is_not_a_valid_unbind_subject():
    """Provenance lands on the bound side only -- and the target is clickable.

    After B is merged into A, A's entity holds two accounts but carries no
    ``bound_by``. The merge control now sits on roster rows too, so A is right
    there to be pressed; judging it by co-tenancy would detach the ORIGINAL
    TARGET, moving A's ledger and stranding rows resolved under the old id.
    """
    service = _dashboard(
        roster=[],
        profiles={"qq:OWNER": {"entity_accounts": ["qq:OWNER", "qq:MEMBER_IN_X"],
                               "bound_by": None}},
    )

    result = await service.unbind_identity_account(user_id="OWNER")

    assert result.value["unbind"]["changed"] is False
    service.plugin.memory_bridge.unbind_speaker_account.assert_not_awaited()


async def test_unbinding_a_genuinely_bound_account_goes_through():
    service = _dashboard(roster=[], profiles={"qq:MEMBER_IN_X": _BOUND})

    result = await service.unbind_identity_account(user_id="MEMBER_IN_X")

    assert result.value["unbind"]["changed"] is True
    service.plugin.memory_bridge.unbind_speaker_account.assert_awaited_once()


async def test_an_unreadable_profile_blocks_both_mutations():
    """Fail closed: both misjudgements are irreversible.

    Guessing "not bound" lets a bind fuse two candidate identities; guessing
    it for unbind moves a standalone account into a new entity. Neither is
    recoverable, so an unreachable server must stop the operation.
    """
    service = _dashboard(roster=[], profiles={})
    service.plugin.memory_bridge.fetch_speaker_profile = AsyncMock(
        side_effect=RuntimeError("connection refused"),
    )

    bind = await service.bind_identity_account(
        user_id="MEMBER_IN_X", target_user_id="OWNER",
    )
    unbind = await service.unbind_identity_account(user_id="MEMBER_IN_X")

    assert bind.__class__.__name__ == "Err"
    assert unbind.__class__.__name__ == "Err"
    service.plugin.memory_bridge.bind_speaker_account.assert_not_awaited()
    service.plugin.memory_bridge.unbind_speaker_account.assert_not_awaited()


@pytest.mark.parametrize("operation", ["bind", "unbind"])
async def test_a_write_that_did_not_persist_is_reported_as_a_failure(operation):
    """``persisted: false`` means the draft was discarded -- nothing changed.

    Reporting success there sends the operator off to verify a merge that does
    not exist, and for unbind it would also quote deltas computed from a draft
    that was thrown away.
    """
    service = _dashboard(roster=[], profiles={"qq:MEMBER_IN_X": _BOUND})
    service.plugin.memory_bridge.bind_speaker_account = AsyncMock(
        return_value={"entity_id": "e1", "persisted": False},
    )
    service.plugin.memory_bridge.unbind_speaker_account = AsyncMock(
        return_value={"changed": True, "ledger_delta": -0.1,
                      "effective_delta": 0.3, "persisted": False},
    )

    if operation == "bind":
        service.plugin.memory_bridge.fetch_speaker_profile = AsyncMock(
            return_value={"entity_id": None, "entity_accounts": []},
        )
        result = await service.bind_identity_account(
            user_id="MEMBER_IN_X", target_user_id="OWNER",
        )
    else:
        result = await service.unbind_identity_account(user_id="MEMBER_IN_X")

    assert result.__class__.__name__ == "Err"


# -- which transport the scope describes ------------------------------------


async def test_the_scope_follows_the_running_transport_not_the_saved_config():
    """Saving a mode does not switch the live client; the save says so itself.

    Describing the new mode during that gap hides the claim UI while open
    platform messages are still arriving.
    """
    service = _dashboard(roster=[], profiles={}, mode="napcat")
    service.plugin.qq_client = SimpleNamespace(CHANNEL="open")

    assert service._identity_scope_payload()["actor_scope"] == "per_conversation"


async def test_the_scope_falls_back_to_config_when_nothing_is_connected():
    service = _dashboard(roster=[], profiles={}, mode="open_platform")
    service.plugin.qq_client = None

    assert service._identity_scope_payload()["actor_scope"] == "per_conversation"


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
