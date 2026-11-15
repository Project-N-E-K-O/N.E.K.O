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
    saved = gate.PERMISSION_GATE_MODE
    gate.PERMISSION_GATE_MODE = mode
    try:
        yield
    finally:
        gate.PERMISSION_GATE_MODE = saved


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
    monkeypatch.setattr(gate, "PERMISSION_APPROVER_ENABLED", False)
    with _mode(PermissionMode.ENFORCE):
        decision = asyncio.run(gate.enforce("t", "external", _actor("qq:FRIEND")))
    assert decision.action is PermissionAction.DENY
    assert decision.blocks is True


def test_confirm_times_out_as_deny(monkeypatch):
    monkeypatch.setattr(gate, "PERMISSION_APPROVER_ENABLED", True)
    monkeypatch.setattr(gate, "PERMISSION_CONFIRM_TIMEOUT_SECONDS", 0.05)
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
    monkeypatch.setattr(gate, "PERMISSION_APPROVER_ENABLED", True)
    monkeypatch.setattr(gate, "PERMISSION_CONFIRM_TIMEOUT_SECONDS", 5.0)

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
    monkeypatch.setattr(gate, "PERMISSION_APPROVER_ENABLED", True)
    monkeypatch.setattr(gate, "PERMISSION_CONFIRM_TIMEOUT_SECONDS", 5.0)

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
    assert gate.PERMISSION_GATE_MODE is PermissionMode.OBSERVE, "the shipped default"
    with _speaker("qq:A1"), _captured_logs() as records:
        result = asyncio.run(registry.execute(
            ToolCall(name="write_tool", arguments={}, call_id="m1")))
    assert called == [1], "observe must not intercept"
    assert result.is_error is False
    assert any("would-deny" in line and "mode=observe" in line for line in records), records


def test_observe_mode_never_parks_a_confirm(monkeypatch):
    """A question nobody can act on is a stall, so observe must not ask it."""
    monkeypatch.setattr(gate, "PERMISSION_APPROVER_ENABLED", True)
    monkeypatch.setattr(gate, "PERMISSION_CONFIRM_TIMEOUT_SECONDS", 30.0)
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
    monkeypatch.setattr(gate, "PERMISSION_GATE_MODE", "sideways")
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
