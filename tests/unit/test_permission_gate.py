"""Unit tests for the agent tool-call permission gate.

Covers the six things the brief asks for: every cell of the matrix, the group rule
that takes the lower band, a tool that never declared a risk, the BLOCKED band, and a
confirm that times out. Plus the two rules the rollout needs: identity arrives only on
the turn (a claim written on the call is ignored), and observe mode -- the shipped
default -- records a decision without intercepting the call.

The cell tests are driven from the table itself instead of a hand-written list. A
hand-written list keeps passing when someone deletes a row, and totality is exactly
the property under test.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import pytest

from config import permission_settings as ps
from config.permission_settings import (
    DEFAULT_BAND,
    PermissionAction,
    PermissionMode,
    ToolRisk,
    TrustBand,
    decide,
    lower_band,
)
from main_logic import tool_permissions as gate
from utils.turn_actor import (
    TurnActor,
    actor_from_inbound,
    current_turn_actor,
    turn_for_message,
    turn_scope,
)

BANDS = list(TrustBand)
RISKS = list(ToolRisk)


@contextlib.contextmanager
def _mode(mode):
    """Run one test in a given gate mode, then put the default back."""
    saved = ps.PERMISSION_GATE_MODE
    ps.PERMISSION_GATE_MODE = mode
    try:
        yield
    finally:
        ps.PERMISSION_GATE_MODE = saved


@contextlib.contextmanager
def _speaker(account="qq:A1", group=None, kind="private", channel="open"):
    """Stand one speaker at the microphone for the block.

    This is the only way identity reaches the gate: the turn carrier, written by host
    code. Tests use it because the production writers use it, not because it is
    convenient -- a test that set ``provider_meta`` would prove the wrong property.
    """
    user_id = account.split(":", 1)[1] if ":" in account else account
    actor = TurnActor(
        user_id=user_id,
        group_id=(group.split(":", 1)[1] if group and ":" in group else (group or "")),
        message_type=kind,
        channel=channel,
    )
    with turn_scope(actor):
        yield actor


@contextlib.contextmanager
def _bands(blocked=(), trusted=(), owners=()):
    """Pin account ids into bands for one test, then put the maps back."""
    saved = (ps.PERMISSION_BLOCKED_ACCOUNTS, ps.PERMISSION_TRUSTED_ACCOUNTS,
             ps.PERMISSION_OWNER_ACCOUNTS)
    ps.PERMISSION_BLOCKED_ACCOUNTS = tuple(blocked)
    ps.PERMISSION_TRUSTED_ACCOUNTS = tuple(trusted)
    ps.PERMISSION_OWNER_ACCOUNTS = tuple(owners)
    try:
        yield
    finally:
        (ps.PERMISSION_BLOCKED_ACCOUNTS, ps.PERMISSION_TRUSTED_ACCOUNTS,
         ps.PERMISSION_OWNER_ACCOUNTS) = saved


@contextlib.contextmanager
def _captured_logs():
    logger = logging.getLogger("main_logic.tool_permissions")
    records: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Handler()
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


def _actor(account="qq:A1", group=None, kind="private"):
    data = {"account_id": account, "message_type": kind}
    if group:
        data["group_id"] = group
    return data


# ------------------------------------------------------------------ the table


def test_matrix_is_total_and_blocked_denies_everything():
    assert set(ps.PERMISSION_MATRIX) == set(BANDS), "one row per band"
    for band in BANDS:
        for risk in RISKS:
            assert ps.PERMISSION_MATRIX[band][risk] in set(PermissionAction), (band, risk)
    for risk in RISKS:
        assert decide(TrustBand.BLOCKED, risk) is PermissionAction.DENY


def test_owner_allows_every_class():
    for risk in RISKS:
        assert decide(TrustBand.OWNER, risk) is PermissionAction.ALLOW


@pytest.mark.parametrize("band", [b.value for b in BANDS])
@pytest.mark.parametrize("risk", [r.value for r in RISKS])
def test_each_cell_answers_from_the_table(band, risk):
    assert decide(TrustBand(band), ToolRisk(risk)) is \
        ps.PERMISSION_MATRIX[TrustBand(band)][ToolRisk(risk)]


def test_external_below_owner_is_never_a_silent_allow():
    for band in (TrustBand.TRUSTED, TrustBand.NORMAL, TrustBand.STRANGER,
                 TrustBand.BLOCKED):
        assert decide(band, ToolRisk.EXTERNAL) in (
            PermissionAction.CONFIRM, PermissionAction.DENY), band


def test_a_hole_in_the_table_is_an_error_not_a_default():
    row = ps.PERMISSION_MATRIX[TrustBand.NORMAL]
    saved = dict(row)
    try:
        del row[ToolRisk.WRITE]
        with pytest.raises(KeyError):
            decide(TrustBand.NORMAL, ToolRisk.WRITE)
    finally:
        ps.PERMISSION_MATRIX[TrustBand.NORMAL] = saved
        ps._validate_matrix()


# ------------------------------------------------------------------ band resolution


def test_unmapped_account_is_a_stranger():
    assert ps.band_for_account("qq:nobody-mapped") is DEFAULT_BAND
    assert ps.band_for_account("") is DEFAULT_BAND
    assert ps.band_for_account(None) is DEFAULT_BAND


def test_owner_and_blocked_maps_win_over_the_source_default():
    with _bands(owners=("qq:BOSS",), blocked=("qq:TROLL",)):
        assert ps.band_for_account("qq:BOSS") is TrustBand.OWNER
        assert ps.band_for_account("qq:TROLL") is TrustBand.BLOCKED


def test_group_takes_the_lower_of_group_and_speaker():
    with _bands(trusted=("qq:FRIEND",)):
        actor = _actor("qq:FRIEND", group="qq:G1", kind="group")
        band, account, group = gate.resolve_band(actor)
        # The group itself is unmapped, so it falls to STRANGER and pulls the
        # trusted speaker down with it. That is the rule, not an oversight.
        assert band is TrustBand.STRANGER
        assert (account, group) == ("qq:FRIEND", "qq:G1")

        ps.PERMISSION_GROUP_BANDS["qq:G1"] = TrustBand.TRUSTED
        try:
            assert gate.resolve_band(actor)[0] is TrustBand.TRUSTED
            with _bands(blocked=("qq:TROLL",)):
                blocked_actor = _actor("qq:TROLL", group="qq:G1", kind="group")
                assert gate.resolve_band(blocked_actor)[0] is TrustBand.BLOCKED
        finally:
            ps.PERMISSION_GROUP_BANDS.pop("qq:G1", None)


def test_private_chat_uses_the_speaker_band_only():
    with _bands(trusted=("qq:FRIEND",)):
        assert gate.resolve_band(_actor("qq:FRIEND"))[0] is TrustBand.TRUSTED


def test_missing_or_malformed_actor_fails_closed():
    for actor in (None, {}, "not a dict", {"account_id": ""}, {"group_id": "qq:G9"}):
        assert gate.resolve_band(actor)[0] is DEFAULT_BAND


def test_lower_band_is_the_less_trusted_side():
    assert lower_band(TrustBand.OWNER, TrustBand.STRANGER) is TrustBand.STRANGER
    assert lower_band(TrustBand.BLOCKED, TrustBand.TRUSTED) is TrustBand.BLOCKED
    assert lower_band(TrustBand.NORMAL, TrustBand.NORMAL) is TrustBand.NORMAL


# ------------------------------------------------------------------ risk declaration


def test_missing_or_bad_risk_is_refused():
    for bad in (None, "", "   ", "readonly", 3, ["read"], "dangerous"):
        with pytest.raises(gate.PermissionConfigError):
            gate.parse_risk(bad)
    # Case is normalised here, not guessed at: the wire edge stays strict.
    # ToolRegisterRequest uses Literal["read", ...], so "Read" is a 422 before it
    # ever reaches this parser, and an SDK caller that wrote "READ" gets the class it
    # meant rather than a refusal for typography.
    assert gate.parse_risk("read") is ToolRisk.READ
    assert gate.parse_risk("Read") is ToolRisk.READ
    assert gate.parse_risk("EXTERNAL") is ToolRisk.EXTERNAL
    assert gate.parse_risk(ToolRisk.WRITE) is ToolRisk.WRITE


def test_registration_without_a_risk_label_is_refused():
    from main_logic.tool_calling import ToolDefinition, ToolRegistry, ToolRegistryError

    registry = ToolRegistry()
    with pytest.raises(ToolRegistryError):
        registry.register(ToolDefinition(name="silent", description=""))
    registry.register(ToolDefinition(name="declared", description="", risk="read"))
    assert registry.get("declared").risk is ToolRisk.READ
    registry.register(ToolDefinition(
        name="via-metadata", description="", metadata={"risk": "write"}))
    assert registry.get("via-metadata").risk is ToolRisk.WRITE
    with pytest.raises(ToolRegistryError):
        registry.register(ToolDefinition(name="bogus", description="", risk="readonly"))


# ------------------------------------------------------------------ enforcement


def test_denied_call_never_reaches_the_handler():
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolRegistry

    called = []
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="write_tool", description="", risk="write",
        handler=lambda _args: called.append(1)))
    with _mode(PermissionMode.ENFORCE), _speaker("qq:A1"), _captured_logs() as records:
        result = asyncio.run(registry.execute(
            ToolCall(name="write_tool", arguments={}, call_id="c1")))
    assert called == []
    assert result.is_error is True
    assert result.output["error"] == "permission_denied"
    assert result.output["band"] == "stranger"
    assert result.output["risk"] == "write"
    assert any(" deny " in line for line in records), records


def test_allowed_call_runs_the_handler():
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolRegistry

    registry = ToolRegistry()
    registry.register(ToolDefinition(name="peek", description="", risk="read",
                                     handler=lambda _args: {"ok": 1}))
    with _mode(PermissionMode.ENFORCE), _speaker("qq:ANYONE"), _captured_logs() as records:
        result = asyncio.run(registry.execute(
            ToolCall(name="peek", arguments={}, call_id="c2")))
    assert result.is_error is False
    assert result.output == {"ok": 1}
    assert records == [], "an allow must not add log noise"


def test_blocked_speaker_is_denied_a_read():
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolRegistry

    registry = ToolRegistry()
    registry.register(ToolDefinition(name="peek", description="", risk="read",
                                     handler=lambda _args: "ran"))
    with _mode(PermissionMode.ENFORCE), _bands(blocked=("qq:TROLL",)):
        with _speaker("qq:TROLL", group="qq:G1", kind="group"):
            result = asyncio.run(registry.execute(
                ToolCall(name="peek", arguments={}, call_id="c3")))
    assert result.is_error is True
    assert result.output["band"] == "blocked"


def test_confirm_without_an_approver_channel_degrades_to_deny(monkeypatch):
    """A CONFIRM with nowhere to ask must become a deny, and say which one it was.

    The speaker has to sit in TRUSTED for this to test anything: an unmapped account is
    a STRANGER, the table denies it in the EXTERNAL column, and the degrade block below
    that never runs -- which is how this test passed while the code path it names was
    dead (review round: "unreachable from any test").
    """
    monkeypatch.setattr(ps, "PERMISSION_APPROVER_ENABLED", False)
    with _mode(PermissionMode.ENFORCE), _bands(trusted=("qq:FRIEND",)):
        assert decide(TrustBand.TRUSTED, ToolRisk.EXTERNAL) is PermissionAction.CONFIRM
        with _captured_logs() as records:
            decision = asyncio.run(gate.enforce("t", "external", _actor("qq:FRIEND")))
    assert decision.action is PermissionAction.DENY
    assert decision.blocks is True
    assert "PERMISSION_APPROVER_ENABLED is false" in decision.reason, decision.reason
    assert gate.approvals.pending_count() == 0, "a degrade must not park the call"
    assert any("deny" in line for line in records), records


def test_an_owner_refusal_is_not_reported_as_a_cancellation(monkeypatch):
    """The model reads this string. "cancelled" invites a retry; "refused" does not."""
    monkeypatch.setattr(ps, "PERMISSION_APPROVER_ENABLED", True)
    monkeypatch.setattr(ps, "PERMISSION_CONFIRM_TIMEOUT_SECONDS", 5.0)

    async def go():
        task = asyncio.ensure_future(gate.enforce(
            "external_tool", "external", _actor("qq:FRIEND")))
        for _ in range(200):
            await asyncio.sleep(0.01)
            pending = gate.approvals.snapshot()
            if pending:
                gate.approvals.resolve(pending[0]["request_id"], False)
                break
        return await task

    with _mode(PermissionMode.ENFORCE), _bands(trusted=("qq:FRIEND",)):
        decision = asyncio.run(go())
    assert decision.action is PermissionAction.DENY
    assert "(owner refused)" in decision.reason, decision.reason


def test_an_answered_request_leaves_the_pending_queue(monkeypatch):
    """A row nobody will ever answer makes the operator's reading of the log wrong."""
    monkeypatch.setattr(ps, "PERMISSION_APPROVER_ENABLED", True)
    monkeypatch.setattr(ps, "PERMISSION_CONFIRM_TIMEOUT_SECONDS", 5.0)

    async def go():
        task = asyncio.ensure_future(gate.enforce(
            "external_tool", "external", _actor("qq:FRIEND")))
        for _ in range(200):
            await asyncio.sleep(0.01)
            pending = gate.approvals.snapshot()
            if pending:
                assert gate.approvals.resolve(pending[0]["request_id"], True) is True
                # resolve hands the answer to the waiter and drops the row at once.
                assert gate.approvals.snapshot() == []
                assert gate.approvals.resolve(pending[0]["request_id"], True) is False
                break
        return await task

    with _mode(PermissionMode.ENFORCE), _bands(trusted=("qq:FRIEND",)):
        asyncio.run(go())
    assert gate.approvals.pending_count() == 0


def test_resolving_an_orphan_row_still_deletes_it():
    """The pop inside ``resolve`` is the only cleaner for a row nobody waits on.

    A live waiter removes its own entry in ``request_approval``'s finally, so deleting on
    answer looks redundant until a task dies between the two. Then the row would sit in
    the map for the life of the process and keep ageing in ``snapshot``, which is the
    table an operator reads before switching to enforce. ``_pending`` is the queue's own
    storage, and this test is of that queue, so it is the honest thing to assert on.
    """
    orphan = gate.approvals.open("orphan_tool", gate.Decision(
        action=PermissionAction.CONFIRM, band=TrustBand.NORMAL, risk=ToolRisk.WRITE))
    assert gate.approvals.pending_count() == 1
    assert gate.approvals.resolve(orphan.request_id, True) is True
    assert gate.approvals.pending_count() == 0
    assert gate.approvals._pending == {}, "an answered row must not linger"


def test_a_gate_error_denies_instead_of_taking_the_tool_path_down(monkeypatch):
    """The never-raises promise, tested at the gate rather than assumed in a docstring.

    A garbage mode is the reachable way to break the gate from configuration. The old
    code let that raise out of ToolRegistry.execute, which would have failed every tool
    call in the app instead of the one call that hit it.
    """
    monkeypatch.setattr(ps, "PERMISSION_GATE_MODE", "sideways")
    with _captured_logs() as records:
        decision = asyncio.run(gate.enforce("t", "read", _actor("qq:A1")))
    assert decision.action is PermissionAction.DENY
    assert decision.blocks is True
    assert "gate error" in decision.reason, decision.reason
    assert any("gate error" in line for line in records), records


def test_a_late_group_map_entry_never_raises_inside_a_turn():
    """A map edited after import lands on a band or on DEFAULT_BAND, never on a crash.

    Import validation is for the shipped file. A deployment or a test that adds an entry
    later cannot be caught at boot, so ``_as_band`` decides: a name that IS a band gets
    honoured (a string that reads "trusted" means trusted, and refusing it would be
    pedantry that surprises nobody), and anything else falls back to DEFAULT_BAND. The
    thing under test here is that neither shape reaches ``.value`` on a bare string,
    which is what used to blow up mid-turn.
    """
    ps.PERMISSION_GROUP_BANDS["qq:GOK"] = "trusted"
    ps.PERMISSION_GROUP_BANDS["qq:GBAD"] = "kinda-trusted"
    ps.PERMISSION_GROUP_BANDS["qq:GBLANK"] = ""
    try:
        assert ps.band_for_group("qq:GOK") is TrustBand.TRUSTED
        assert ps.band_for_group("qq:GBAD") is DEFAULT_BAND
        assert ps.band_for_group("qq:GBLANK") is DEFAULT_BAND
        # And the resolver, which is the caller inside a turn, keeps working on both.
        assert gate.resolve_band(_actor("qq:A1", group="qq:GBAD", kind="group"))[0] \
            is DEFAULT_BAND
    finally:
        for key in ("qq:GOK", "qq:GBAD", "qq:GBLANK"):
            ps.PERMISSION_GROUP_BANDS.pop(key, None)


def test_a_decision_built_from_strings_still_blocks():
    """Replays and payloads carry plain strings; a string deny must behave like the member.

    The fail-open direction here is silent: comparing with ``is`` against a member would
    let a string DENY through as "not a deny", and the tool would run.
    """
    decision = gate.Decision(
        action="deny", band="stranger", risk="write", mode="enforce")
    assert decision.blocks is True
    with _captured_logs() as records:
        gate.audit(decision, "some_tool")
    assert records and "deny" in records[0], records


def test_confirm_times_out_as_deny(monkeypatch):
    monkeypatch.setattr(ps, "PERMISSION_APPROVER_ENABLED", True)
    monkeypatch.setattr(ps, "PERMISSION_CONFIRM_TIMEOUT_SECONDS", 0.05)
    with _mode(PermissionMode.ENFORCE), _bands(trusted=("qq:FRIEND",)):
        # TRUSTED x external is the CONFIRM cell. An unmapped account would be
        # STRANGER and deny outright, which would test the table, not the timeout.
        assert decide(TrustBand.TRUSTED, ToolRisk.EXTERNAL) is PermissionAction.CONFIRM
        with _captured_logs() as records:
            decision = asyncio.run(gate.enforce(
                "external_tool", "external", _actor("qq:FRIEND")))
    assert decision.action is PermissionAction.DENY
    assert any("timed out" in line for line in records), records
    assert gate.approvals.pending_count() == 0, "nothing may stay parked"


def test_confirm_resolved_yes_allows(monkeypatch):
    monkeypatch.setattr(ps, "PERMISSION_APPROVER_ENABLED", True)
    monkeypatch.setattr(ps, "PERMISSION_CONFIRM_TIMEOUT_SECONDS", 5.0)

    async def go():
        task = asyncio.ensure_future(gate.enforce(
            "external_tool", "external", _actor("qq:FRIEND")))
        for _ in range(100):
            await asyncio.sleep(0.01)
            pending = gate.approvals.snapshot()
            if pending:
                assert gate.approvals.resolve(pending[0]["request_id"], True) is True
                break
        return await task

    with _mode(PermissionMode.ENFORCE), _bands(trusted=("qq:FRIEND",)):
        decision = asyncio.run(go())
    assert decision.action is PermissionAction.ALLOW
    assert "confirmed" in decision.reason
    assert decision.blocks is False
    assert gate.approvals.pending_count() == 0


def test_confirm_resolved_no_denies(monkeypatch):
    monkeypatch.setattr(ps, "PERMISSION_APPROVER_ENABLED", True)
    monkeypatch.setattr(ps, "PERMISSION_CONFIRM_TIMEOUT_SECONDS", 5.0)

    async def go():
        task = asyncio.ensure_future(gate.enforce(
            "external_tool", "external", _actor("qq:FRIEND")))
        for _ in range(100):
            await asyncio.sleep(0.01)
            pending = gate.approvals.snapshot()
            if pending:
                gate.approvals.resolve(pending[0]["request_id"], False)
                break
        return await task

    with _mode(PermissionMode.ENFORCE), _bands(trusted=("qq:FRIEND",)):
        decision = asyncio.run(go())
    assert decision.action is PermissionAction.DENY


# ------------------------------------------------------------------ gate mode


def test_observe_mode_records_a_deny_and_runs_the_tool():
    """The default. A stranger's write tool must still work while nobody is stamped."""
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolRegistry

    called = []
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="write_tool", description="", risk="write",
        handler=lambda _args: called.append(1) or "done"))
    assert ps.PERMISSION_GATE_MODE is PermissionMode.OBSERVE, "the shipped default"
    with _speaker("qq:A1"), _captured_logs() as records:
        result = asyncio.run(registry.execute(
            ToolCall(name="write_tool", arguments={}, call_id="m1")))
    assert called == [1], "observe must not intercept"
    assert result.is_error is False
    assert any("would-deny" in line and "mode=observe" in line for line in records), records


def test_observe_mode_never_parks_a_confirm(monkeypatch):
    """A question nobody can act on is a stall, so observe must not ask it."""
    monkeypatch.setattr(ps, "PERMISSION_APPROVER_ENABLED", True)
    monkeypatch.setattr(ps, "PERMISSION_CONFIRM_TIMEOUT_SECONDS", 30.0)
    with _bands(trusted=("qq:FRIEND",)):
        with _captured_logs() as records:
            decision = asyncio.run(gate.enforce(
                "external_tool", "external", _actor("qq:FRIEND")))
    assert decision.action is PermissionAction.CONFIRM
    assert decision.blocks is False
    assert gate.approvals.pending_count() == 0
    assert any("would-confirm" in line for line in records), records


def test_enforce_mode_blocks_the_same_call_the_default_only_records():
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolRegistry

    called = []
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="write_tool", description="", risk="write",
        handler=lambda _args: called.append(1)))
    with _speaker("qq:A1"):
        with _mode(PermissionMode.ENFORCE):
            asyncio.run(registry.execute(ToolCall(
                name="write_tool", arguments={}, call_id="m2")))
    assert called == []


def test_a_bad_mode_value_is_refused_not_defaulted(monkeypatch):
    """A typo must not boot a deployment into observe while it means enforce."""
    monkeypatch.setattr(ps, "PERMISSION_GATE_MODE", "sideways")
    with pytest.raises(ValueError):
        gate._mode()
    monkeypatch.setenv("NEKO_PERMISSION_MODE", "enforce")
    assert ps._mode_from_env() is PermissionMode.ENFORCE
    monkeypatch.setenv("NEKO_PERMISSION_MODE", "EnForce")
    assert ps._mode_from_env() is PermissionMode.ENFORCE
    monkeypatch.setenv("NEKO_PERMISSION_MODE", "off")
    with pytest.raises(ValueError):
        ps._mode_from_env()
    monkeypatch.delenv("NEKO_PERMISSION_MODE", raising=False)
    assert ps._mode_from_env() is PermissionMode.OBSERVE


# ------------------------------------------------------------------ audit shape


def test_audit_names_who_which_tool_and_which_band():
    with _captured_logs() as records:
        gate.audit(gate.Decision(
            action=PermissionAction.CONFIRM, band=TrustBand.NORMAL,
            risk=ToolRisk.WRITE, account_id="qq:A9", group_id="qq:G9"),
            "some_tool")
    assert len(records) == 1
    for needle in ("confirm", "some_tool", "normal", "write", "qq:A9", "qq:G9"):
        assert needle in records[0], records[0]


def test_unusable_risk_is_denied_without_inventing_a_class():
    with _captured_logs() as records:
        decision = asyncio.run(gate.enforce("broken_tool", "not-a-class", None))
    assert decision.action is PermissionAction.DENY
    assert decision.risk is None
    assert any("unlabelled" in line for line in records), records


def test_registration_can_be_a_stranger_free_path():
    # Registration carries no speaker, so the label check must not need one.
    from main_logic.tool_calling import ToolDefinition, ToolRegistry

    registry = ToolRegistry()
    registry.register(ToolDefinition(name="ok", description="", risk="self"))
    assert registry.get("ok").risk is ToolRisk.SELF


# ------------------------------------------------------------------ turn carrier


def test_group_turn_names_both_the_speaker_and_the_conversation():
    message = {"message_type": "group", "channel": "open",
               "user_id": "OPENID-USER", "group_id": "GROUP-1"}
    with turn_scope(actor_from_inbound(message)) as actor:
        assert actor.account_id == "qq:OPENID-USER"
        assert actor.group_account_id == "qq:GROUP-1"
        assert actor.is_group is True
        assert current_turn_actor() is actor
    assert current_turn_actor() is None, "the scope must not outlive the turn"


def test_private_turn_carries_no_conversation():
    message = {"message_type": "private", "channel": "open",
               "user_id": "OPENID-USER", "group_id": ""}
    with turn_scope(actor_from_inbound(message)) as actor:
        assert actor.account_id == "qq:OPENID-USER"
        assert actor.group_account_id == ""
        assert actor.is_group is False


def test_a_message_with_no_speaker_declares_no_actor():
    # A poke notice is not a request to run a tool, and an empty user id is not an
    # identity. Both must clear the carrier instead of inheriting the last speaker.
    for message in (
        {"message_type": "notice", "channel": "open", "user_id": "OPERATOR"},
        {"message_type": "private", "channel": "open", "user_id": ""},
        {"message_type": "private", "channel": "open"},
        "not a message",
        None,
    ):
        assert actor_from_inbound(message) is None, message


def test_an_unmapped_channel_produces_no_account_id():
    """No prefix means no lookup, which means DEFAULT_BAND. A guess is worse."""
    actor = TurnActor(user_id="X", message_type="private", channel="carrier-pigeon")
    assert actor.account_id == ""
    band, account, _group = gate.resolve_band(actor.as_dict())
    assert band is DEFAULT_BAND
    assert account == ""


def test_nested_scopes_put_the_outer_speaker_back():
    outer = TurnActor(user_id="OUT", message_type="private", channel="open")
    inner = TurnActor(user_id="IN", message_type="private", channel="open")
    with turn_scope(outer):
        with turn_scope(inner):
            assert current_turn_actor() is inner
        assert current_turn_actor() is outer


def test_a_claim_on_the_call_cannot_raise_a_band():
    """The rule that makes the carrier worth having: the speaker cannot pick a band.

    OWNER is the highest claim a caller could invent, against a write tool an unmapped
    speaker may not touch. The claim is ignored and said out loud, because a silent
    ignore would read as a broken table.
    """
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolRegistry

    called = []
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="write_tool", description="", risk="write",
        handler=lambda _args: called.append(1)))
    claim = {"account_id": "qq:BOSS", "message_type": "private", "group_id": ""}
    with _mode(PermissionMode.ENFORCE), _bands(owners=("qq:BOSS",)):
        with _captured_logs() as records:
            result = asyncio.run(registry.execute(ToolCall(
                name="write_tool", arguments={}, call_id="k1",
                provider_meta={"actor": claim})))
    assert called == []
    assert result.is_error is True
    assert result.output["band"] == "stranger", "the claim must not be believed"
    assert any("ignored an actor claim" in line for line in records), records


def test_the_sink_delivery_is_the_stamp_point():
    """What the connector guarantees: inside a sink, this turn has a speaker.

    Driven through the real seam (ConnectionBase._run_inbound_sink) rather than a
    hand-built TurnActor, so the test breaks if the stamp is moved or dropped.
    """
    import types

    from utils.connection.base import ConnectionBase

    seen = {}
    message = {"message_type": "group", "channel": "open",
               "user_id": "OPENID-USER", "group_id": "GROUP-1"}

    async def sink(payload):
        seen["inside"] = current_turn_actor()

    asyncio.run(ConnectionBase._run_inbound_sink(
        types.SimpleNamespace(), sink, message))
    assert seen["inside"] is not None
    assert seen["inside"].account_id == "qq:OPENID-USER"
    assert current_turn_actor() is None


def test_a_pulled_message_can_be_bound_by_the_consumer():
    """The pull path gets the same rule, through the one public binding API."""
    message = {"message_type": "private", "channel": "onebot", "user_id": "U9"}
    assert current_turn_actor() is None
    with turn_for_message(message) as actor:
        assert actor.account_id == "qq:U9"
    assert current_turn_actor() is None


# ----------------------------------------------------------- pinned table values


#: The policy, written by hand. Every other test in this file asks the table what it
#: says; this one says what the table must say. Without it, flipping a cell in config
#: keeps the suite green: the parametrized cell test compares decide() against the same
#: table it reads, so it tests routing, not values. Nine of the twenty cells had no
#: independent expectation at all (review round: "7 of 20 cells are unprotected"; the
#: external column is covered only as "not a silent allow", which both CONFIRM and DENY
#: satisfy). Change a cell here only together with the brief that fixed it.
EXPECTED_POLICIES: dict[str, dict[str, str]] = {
    "owner": {"read": "allow", "self": "allow", "write": "allow", "external": "allow"},
    "trusted": {"read": "allow", "self": "allow", "write": "allow",
                "external": "confirm"},
    "normal": {"read": "allow", "self": "allow", "write": "confirm",
               "external": "confirm"},
    "stranger": {"read": "allow", "self": "deny", "write": "deny", "external": "deny"},
    "blocked": {"read": "deny", "self": "deny", "write": "deny", "external": "deny"},
}


def test_the_table_says_exactly_these_twenty_things():
    assert set(EXPECTED_POLICIES) == {b.value for b in TrustBand}
    for band in TrustBand:
        assert set(EXPECTED_POLICIES[band.value]) == {r.value for r in ToolRisk}
        for risk in ToolRisk:
            want = EXPECTED_POLICIES[band.value][risk.value]
            got = ps.PERMISSION_MATRIX[band][risk]
            assert type(got) is PermissionAction, (band, risk, type(got).__name__)
            assert got.value == want, f"{band.value}/{risk.value}: {got.value} != {want}"
            assert decide(band, risk).value == want


def test_a_plain_string_cell_in_the_table_is_a_startup_error():
    """The type check is the only thing that catches this class of typo.

    StrEnum members compare and hash by value on this interpreter, so ``"deny" in
    set(PermissionAction)`` answers True and an equality check lets a bare string into
    the table -- where it survives import and then raises on ``.value`` inside a turn,
    or worse gets read as neither a deny nor a confirm.
    """
    row = ps.PERMISSION_MATRIX[TrustBand.NORMAL]
    saved = dict(row)
    try:
        row[ToolRisk.WRITE] = "allow"
        with pytest.raises(ValueError, match="not a PermissionAction member"):
            ps._validate_matrix()
    finally:
        ps.PERMISSION_MATRIX[TrustBand.NORMAL] = saved
        ps._validate_matrix()


def test_a_stray_column_or_a_group_map_string_is_a_startup_error():
    row = ps.PERMISSION_MATRIX[TrustBand.OWNER]
    saved = dict(row)
    saved_groups = dict(ps.PERMISSION_GROUP_BANDS)
    try:
        row["reads"] = PermissionAction.ALLOW
        with pytest.raises(ValueError, match="columns outside the risk vocabulary"):
            ps._validate_matrix()
        del row["reads"]
        ps.PERMISSION_GROUP_BANDS["qq:G1"] = "owner"
        with pytest.raises(ValueError, match="not a TrustBand member"):
            ps._validate_matrix()
    finally:
        ps.PERMISSION_MATRIX[TrustBand.OWNER] = saved
        ps.PERMISSION_GROUP_BANDS.clear()
        ps.PERMISSION_GROUP_BANDS.update(saved_groups)
        ps._validate_matrix()


# ------------------------------------------------- unlabelled third-party tools


def test_an_unlabelled_plugin_tool_registers_and_is_refused_at_call_time():
    """The fleet rule, in one test.

    29 installed plugin packages define tools and none of them has the keyword yet. A
    required label at import or at registration switches those plugins off; the gate
    reaching the same answer at call time does not. The label stays mandatory in
    effect -- an unlabelled tool is the most restricted thing in the system.
    """
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolRegistry

    called = []
    registry = ToolRegistry()
    tool = ToolDefinition(
        name="legacy_tool", description="", handler=lambda _a: called.append(1),
        metadata={"source": "plugin:legacy"})
    registry.register(tool, risk_required=False)  # the wire path
    assert tool.risk is None

    with _mode(PermissionMode.ENFORCE), _speaker("qq:A1"):
        result = asyncio.run(registry.execute(ToolCall(
            name="legacy_tool", arguments={}, call_id="u1")))
    assert called == [], "enforce must refuse an unlabelled tool"
    assert result.is_error is True
    assert result.output["risk"] == "unlabelled"

    called.clear()
    with _speaker("qq:A1"):  # observe is the shipped default
        result = asyncio.run(registry.execute(ToolCall(
            name="legacy_tool", arguments={}, call_id="u2")))
    assert called == [1], "observe records the refusal, it does not apply it"
    assert result.is_error is False


def test_a_host_registration_still_refuses_an_unlabelled_tool():
    """The other half of the rule: the lenient path is the wire, and only the wire."""
    from main_logic.tool_calling import ToolDefinition, ToolRegistry, ToolRegistryError

    registry = ToolRegistry()
    with pytest.raises(ToolRegistryError):
        registry.register(ToolDefinition(name="host_tool", description=""))
    with pytest.raises(ToolRegistryError):
        registry.register(
            ToolDefinition(name="host_tool", description="",
                           metadata={"source": "plugin:x"}),
            risk_required=True)


def test_the_sdk_accepts_a_missing_label_and_refuses_a_wrong_one():
    """Decoration must not break an old plugin, and must not invent a class either."""
    from plugin.sdk.plugin.llm_tool import LLM_TOOL_META_ATTR, LlmToolMeta, llm_tool

    @llm_tool(name="legacy_decorated", description="no label here")
    def _legacy(args):  # pragma: no cover - never called
        return None

    legacy_meta = getattr(_legacy, LLM_TOOL_META_ATTR, None)
    assert isinstance(legacy_meta, LlmToolMeta)
    assert legacy_meta.risk is None
    assert legacy_meta.to_ipc_payload(plugin_id="p").get("risk") is None

    with pytest.raises(ValueError, match="unusable|risk"):
        @llm_tool(name="typo_decorated", description="bad label", risk="readonly")
        def _typo(args):  # pragma: no cover
            return None

    # A declared label still travels unchanged, lower-cased.
    @llm_tool(name="labelled", description="ok", risk="Write")
    def _labelled(args):  # pragma: no cover
        return None

    assert getattr(_labelled, LLM_TOOL_META_ATTR).risk == "write"
