"""Host side of the plugin process's system.* audit: SYSTEM_AUDIT batches
reach the call-chain audit, bounded and reduced to fixed vocabularies."""

from __future__ import annotations

import pytest

from plugin import settings
from plugin.core.communication import PluginCommunicationResourceManager
from plugin.server.application.plugins import call_audit, capability_gate
from tests.fake_clock import patch_module_clock

pytestmark = pytest.mark.plugin_unit

CONNECT = "system.network:connect"


@pytest.fixture
def written(monkeypatch):
    entries = []
    monkeypatch.setattr(call_audit, "record", entries.append)
    monkeypatch.setattr(capability_gate, "_system_use_budget", {})
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", None)
    monkeypatch.setattr(settings, "PLUGIN_PERMISSION_CHECKS_DISABLED", False)
    return entries


def _item(**overrides):
    item = {
        "permission": CONNECT, "event": "socket.connect", "target": "example.invalid:443",
        "verdict": "would_deny", "reason": "undecided", "enforcing": False, "flags": [],
    }
    item.update(overrides)
    return item


def test_batch_is_written_as_system_use_entries(written):
    count = capability_gate.record_system_uses("demo", {"type": "SYSTEM_AUDIT", "items": [_item()], "dropped": 0})
    assert count == 1
    (entry,) = written
    assert entry == {
        "kind": "system_use", "mode": "enforce", "enforcing": False, "plugin": "demo",
        "capability": CONNECT, "operation": "socket.connect", "target": "example.invalid:443",
        "verdict": "would_deny", "reason": "undecided", "flags": ["plugin_reported"],
    }


def test_allowed_use_and_master_switch(written, monkeypatch):
    monkeypatch.setattr(settings, "PLUGIN_PERMISSION_CHECKS_DISABLED", True)
    capability_gate.record_system_uses("demo", {"items": [_item(verdict="allow", reason=None, flags=["loopback"])]})
    (entry,) = written
    assert (entry["verdict"], entry["reason"], entry["mode"]) == ("allow", None, "observe")
    assert entry["flags"] == ["loopback", "plugin_reported"]


def test_refusal_reported_by_the_process_sets_enforcing(written):
    capability_gate.record_system_uses("demo", {"items": [_item(enforcing=True), _item(verdict="allow", enforcing=True)]})
    assert [entry["enforcing"] for entry in written] == [True, False]


@pytest.mark.parametrize(
    "item",
    [
        _item(permission="system.env:read"),
        _item(permission="plugin_sdk.main_agent.memory:read"),
        "not a dict",
    ],
)
def test_items_outside_the_vocabulary_are_skipped(written, item):
    assert capability_gate.record_system_uses("demo", {"items": [item]}) == 0
    assert written == []


def test_fields_are_reduced_to_safe_values(written):
    capability_gate.record_system_uses("demo", {"items": [_item(
        event="open\n{\"kind\":\"forward\"}", target="a\nb", reason="because", flags=["x", "loopback"],
    )]})
    (entry,) = written
    assert entry["operation"] == "<invalid>" and entry["target"] == "<invalid>"
    assert entry["reason"] is None and entry["flags"] == ["loopback", "plugin_reported"]


def test_malformed_messages_are_ignored(written):
    for message in (None, [], {"items": "x"}, {"items": None}):
        assert capability_gate.record_system_uses("demo", message) == 0
    assert written == []


def test_lost_records_and_the_per_minute_budget(written, monkeypatch):
    monkeypatch.setattr(capability_gate, "_SYSTEM_USE_PER_MINUTE", 2)
    items = [_item(target=f"h{index}:1") for index in range(3)]
    assert capability_gate.record_system_uses("demo", {"items": items, "dropped": 4}) == 2
    assert written[-1] == {"kind": "system_use_lost", "plugin": "demo", "count": 5}
    # Another plugin has its own budget.
    assert capability_gate.record_system_uses("other", {"items": items[:1]}) == 1


def test_loss_is_reported_once_a_window_and_carried_over(written, monkeypatch):
    clock = [1000.0]
    patch_module_clock(monkeypatch, capability_gate, monotonic=lambda: clock[0])
    for _ in range(50):
        capability_gate.record_system_uses("demo", {"items": [], "dropped": 1})
    lost = [e for e in written if e["kind"] == "system_use_lost"]
    assert lost == [{"kind": "system_use_lost", "plugin": "demo", "count": 1}]
    clock[0] += capability_gate._SYSTEM_USE_WINDOW
    capability_gate.record_system_uses("demo", {"items": []})
    lost = [e for e in written if e["kind"] == "system_use_lost"]
    assert lost[-1]["count"] == 49 and len(lost) == 2


def test_batch_size_is_capped_and_the_rest_counted(written, monkeypatch):
    monkeypatch.setattr(capability_gate, "_SYSTEM_USE_BATCH_LIMIT", 2)
    assert capability_gate.record_system_uses("demo", {"items": [_item()] * 5}) == 2
    assert written[-1] == {"kind": "system_use_lost", "plugin": "demo", "count": 3}


@pytest.mark.parametrize(
    "bad",
    [
        _item(permission=[]),
        _item(permission={"a": 1}),
        _item(flags=[[1], "loopback"]),
        _item(reason=["x"]),
        _item(event=["x"], target={"x": 1}),
    ],
)
def test_unhashable_fields_skip_or_clean_one_item_and_keep_the_budget(written, monkeypatch, bad):
    monkeypatch.setattr(capability_gate, "_SYSTEM_USE_PER_MINUTE", 3)
    batch = {"items": [_item(target="a:1"), bad, _item(target="b:1")]}
    first = capability_gate.record_system_uses("demo", batch)
    assert first >= 2
    # The budget was saved: a repeated batch cannot write past it.
    for _ in range(5):
        capability_gate.record_system_uses("demo", batch)
    assert len([e for e in written if e["kind"] == "system_use"]) == 3
    for entry in written:
        if entry["kind"] == "system_use":
            assert entry["flags"] in (["plugin_reported"], ["loopback", "plugin_reported"])
            assert entry["reason"] in (None, "undecided")


def test_budget_is_saved_when_writing_fails(monkeypatch):
    monkeypatch.setattr(capability_gate, "_system_use_budget", {})
    calls = []

    def failing(entry):
        calls.append(entry)
        if len(calls) == 2:
            raise RuntimeError("disk")

    monkeypatch.setattr(call_audit, "record", failing)
    capability_gate.record_system_uses("demo", {"items": [_item(target="a:1"), _item(target="b:1")]})
    assert capability_gate._system_use_budget["demo"][1] == 2


def test_system_use_entries_cannot_evict_decisions(monkeypatch, tmp_path):
    monkeypatch.setattr(call_audit, "audit_directory", lambda: tmp_path)
    monkeypatch.setattr(call_audit, "_ring", call_audit.deque(maxlen=3))
    monkeypatch.setattr(call_audit, "_decision_ring", call_audit.deque(maxlen=3))
    monkeypatch.setattr(call_audit, "_system_use_ring", call_audit.deque(maxlen=3))
    monkeypatch.setattr(call_audit, "_listener", None)
    monkeypatch.setattr(call_audit, "_abandoned", None)
    call_audit.close()
    call_audit.reopen()
    try:
        call_audit.record({"kind": "decision", "plugin": "demo"})
        call_audit.record({"kind": "forward"})
        for index in range(10):
            call_audit.record({"kind": "system_use", "plugin": "demo", "n": index})
        for index in range(10):
            call_audit.record({"kind": "system_use_lost", "plugin": "demo", "count": index})
        kinds = [entry["kind"] for entry in call_audit.entries()]
        assert kinds.count("decision") == 1 and kinds.count("forward") == 1
        assert len(call_audit._system_use_ring) == 3
        assert kinds[0] == "system_use_lost"
    finally:
        call_audit.close()
        call_audit.reopen()


async def test_uplink_routes_system_audit_under_the_connection_identity(monkeypatch):
    calls = []
    monkeypatch.setattr(capability_gate, "record_system_uses", lambda plugin_id, msg: calls.append((plugin_id, msg)))
    manager = PluginCommunicationResourceManager(plugin_id="demo", transport=object())  # type: ignore[arg-type]
    await manager._route_message({"type": "SYSTEM_AUDIT", "plugin_id": "someone-else", "items": []})
    ((plugin_id, msg),) = calls
    assert plugin_id == "demo" and msg["plugin_id"] == "demo"


def test_loss_still_pending_is_written_when_the_plugin_disconnects(written):
    capability_gate.record_system_uses("demo", {"items": [], "dropped": 1})
    capability_gate.record_system_uses("demo", {"items": [], "dropped": 4})  # same window: held
    assert [e["count"] for e in written if e["kind"] == "system_use_lost"] == [1]
    capability_gate.flush_system_use_loss("demo")
    assert [e["count"] for e in written if e["kind"] == "system_use_lost"] == [1, 4]
    capability_gate.flush_system_use_loss("demo")  # nothing left: nothing written
    assert len([e for e in written if e["kind"] == "system_use_lost"]) == 2


async def test_communication_shutdown_flushes_pending_loss(monkeypatch):
    flushed = []
    monkeypatch.setattr(capability_gate, "flush_system_use_loss", flushed.append)
    manager = PluginCommunicationResourceManager(plugin_id="demo", transport=object())  # type: ignore[arg-type]

    async def nothing(**_kwargs):
        return None

    monkeypatch.setattr(manager, "_shutdown_local", nothing)
    await manager.shutdown(timeout=0.1)
    assert flushed == ["demo"]
