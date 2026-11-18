"""The single provenance record that both permission trails share.

The queue this file pins:

- one trace id per turn, extended by a plugin hop instead of restarted;
- the wire shape #3780's ``CallOrigin`` decodes, so "who spoke" and "which plugin
  called" join on a field rather than on a timestamp;
- one audit writer: the sink replaces the log, it does not duplicate it;
- the local owner stamp resolves through the same band grammar as a QQ id.
"""

from __future__ import annotations

import pytest

from config.permission_settings import TrustBand
from utils import provenance as prov
from utils.provenance import (
    ORIGIN_CONNECTOR,
    ORIGIN_LOCAL_UI,
    ORIGIN_LOCAL_VOICE,
    ORIGIN_PLUGIN,
    Provenance,
    current_provenance,
    emit_audit,
    local_owner_provenance,
    new_trace_id,
    provenance_for_actor,
    provenance_scope,
    set_audit_sink,
)
from utils.turn_actor import TurnActor, actor_from_inbound


def _actor(user="A1", channel="open", kind="private", group=""):
    return TurnActor(user_id=user, channel=channel, message_type=kind, group_id=group)


def test_a_local_owner_stamp_is_owner_through_the_same_grammar():
    record = local_owner_provenance(ORIGIN_LOCAL_UI)
    assert record.actor is not None
    assert record.actor.account_id == "webui:owner"
    assert record.is_local is True
    payload = record.actor_payload()
    assert payload["account_id"] == "webui:owner"
    from config.permission_settings import band_for_account

    assert band_for_account(payload["account_id"]) is TrustBand.OWNER


def test_only_local_origins_may_build_an_owner_stamp():
    with pytest.raises(ValueError):
        local_owner_provenance(ORIGIN_CONNECTOR)
    with pytest.raises(ValueError):
        local_owner_provenance(ORIGIN_PLUGIN)


def test_the_wire_shape_is_what_the_plugin_view_decodes():
    """``CallOrigin(kind, ref, speaker)`` reads exactly these three fields."""
    record = provenance_for_actor(_actor("A1"), origin=ORIGIN_CONNECTOR)
    wire = record.to_wire()
    assert set(wire) == {"trace_id", "speaker", "channel", "origin", "chain"}
    assert wire["speaker"] == "qq:A1"
    assert wire["origin"] == {"kind": ORIGIN_CONNECTOR, "ref": None}
    assert wire["chain"] == []
    assert wire["trace_id"] == record.trace_id


def test_a_child_hop_keeps_the_trace_and_appends_one_plugin():
    record = provenance_for_actor(_actor("A1"), origin=ORIGIN_CONNECTOR)
    child = record.child("plugin:a").child("plugin:b")
    assert child.trace_id == record.trace_id
    assert child.chain == ("plugin:a", "plugin:b")
    assert child.origin == ORIGIN_PLUGIN
    assert child.actor is record.actor
    # An empty hop is a no-op rather than a blank link in the chain.
    assert child.child("") is child


def test_a_wire_record_without_a_trace_is_not_adopted():
    """No trace means it cannot be joined, so it is not a record at all."""
    assert prov.Provenance.from_wire({}) is None
    assert prov.Provenance.from_wire({"trace_id": "  "}) is None
    assert prov.Provenance.from_wire(None) is None


def test_a_wire_record_survives_a_round_trip():
    record = provenance_for_actor(_actor("A1", channel="open"), origin=ORIGIN_CONNECTOR)
    back = prov.Provenance.from_wire(record.to_wire())
    assert back is not None
    assert back.trace_id == record.trace_id
    assert back.origin == record.origin
    assert back.actor is not None and back.actor.account_id == "qq:A1"


def test_the_audit_sink_replaces_the_log_instead_of_duplicating_it(caplog):
    """One destination. Two writers would be the parallel trail this replaces."""
    seen = []
    previous = set_audit_sink(seen.append)
    try:
        with caplog.at_level("WARNING"):
            emit_audit({"kind": "tool_permission", "trace_id": "t", "label": "deny"})
    finally:
        set_audit_sink(previous)
    assert seen and seen[0]["trace_id"] == "t"
    assert not [r for r in caplog.records if "[permission]" in r.getMessage()]


def test_the_default_destination_is_one_line_on_the_main_log(caplog):
    previous = set_audit_sink(None)
    try:
        with caplog.at_level("WARNING"):
            emit_audit({"kind": "tool_permission", "trace_id": "t", "label": "deny",
                        "tool": "x", "chain": ["plugin:a"]})
    finally:
        set_audit_sink(previous)
    lines = [r.getMessage() for r in caplog.records if "[permission]" in r.getMessage()]
    assert len(lines) == 1
    assert "trace=t" in lines[0] and "chain=plugin:a" in lines[0]


def test_a_broken_sink_does_not_raise_into_the_caller():
    def _boom(_record):
        raise RuntimeError("disk full")

    previous = set_audit_sink(_boom)
    try:
        emit_audit({"kind": "tool_permission", "trace_id": "t"})  # must not raise
    finally:
        set_audit_sink(previous)


def test_a_connector_scope_sets_and_clears_the_carrier():
    actor = actor_from_inbound({"message_type": "private", "user_id": "A1", "channel": "open"})
    assert current_provenance() is None
    with provenance_scope(provenance_for_actor(actor, origin=ORIGIN_CONNECTOR)) as record:
        assert current_provenance() is record
        assert record.actor is actor
    assert current_provenance() is None


def test_two_turns_get_two_traces():
    """A trace is per turn; reusing one would merge unrelated conversations."""
    first = provenance_for_actor(_actor("A1"), origin=ORIGIN_CONNECTOR)
    second = provenance_for_actor(_actor("A1"), origin=ORIGIN_CONNECTOR)
    assert first.trace_id != second.trace_id
    assert new_trace_id() != new_trace_id()


def test_a_connector_cannot_claim_the_local_ui_channel():
    """``webui`` resolves to OWNER, so only host code may stamp it.

    A connector reports ``channel`` from its platform. If one passed "webui"
    through, the speaker would build ``webui:<id>`` and land on OWNER by writing a
    single field. ``actor_from_inbound`` refuses the actor instead, and the turn
    falls to DEFAULT_BAND.
    """
    assert actor_from_inbound(
        {"message_type": "private", "user_id": "A1", "channel": "webui"}
    ) is None
    # The real connector labels still resolve normally.
    actor = actor_from_inbound(
        {"message_type": "private", "user_id": "A1", "channel": "open"}
    )
    assert actor is not None and actor.account_id == "qq:A1"
