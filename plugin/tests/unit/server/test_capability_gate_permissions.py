"""The capability gate, the consent dialog and autostart read the permission store.

Covers the gate's verdict for every stored state, an unreadable store, what a
source change keeps (a denial) and drops (an approval) across all readers,
the ``legacy_approved`` marker and the audit ``enforcing`` field.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin import settings
from plugin.core.call_registry import CallLink, CallRegistry, Origin
from plugin.server.application.plugins import (
    call_audit,
    capability_gate,
    consent_service,
    permission_service,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure import plugin_permissions
from plugin.server.infrastructure.plugin_permissions import (
    LEGACY_APPROVED,
    LEGACY_DENIED,
    PluginPermissionStore,
    PluginSource,
)

pytestmark = pytest.mark.plugin_unit

MEMORY = capability_gate.MEMORY_READ
PUSH = capability_gate.MESSAGES_PUSH
FIRST = PluginSource("market", "m-1")
SECOND = PluginSource("market", "m-2")


class Manager:
    def __init__(self, root: Path):
        self.root = root

    def get_runtime_config_path(self, name):
        return self.root / name

    def save_json_config(self, name, data):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", None)
    monkeypatch.setattr(settings, "PLUGIN_PERMISSION_CHECKS_DISABLED", False)
    monkeypatch.setattr(capability_gate, "registry", CallRegistry())
    monkeypatch.setattr(capability_gate, "_last_written", {})
    monkeypatch.setattr(capability_gate, "_consent_failed_at", -60.0)
    monkeypatch.setattr(call_audit, "audit_directory", lambda: tmp_path / "audit")
    monkeypatch.setattr(call_audit, "_ring", call_audit.deque(maxlen=1000))
    monkeypatch.setattr(call_audit, "_decision_ring", call_audit.deque(maxlen=500))
    monkeypatch.setattr(call_audit, "_abandoned", None)
    call_audit.close()
    call_audit.reopen()

    sources: dict[str, PluginSource] = {}
    manager = Manager(tmp_path / "config")
    store = PluginPermissionStore(manager, source_resolver=sources.get)
    monkeypatch.setattr(permission_service, "permission_store", store)
    monkeypatch.setattr(permission_service, "current_source", sources.get)
    monkeypatch.setattr(permission_service, "_last_records", {})
    monkeypatch.setattr(permission_service, "_removals", {})
    monkeypatch.setattr(permission_service, "_reloads", 0)
    monkeypatch.setattr(permission_service, "_read_failures", {})

    report = {"status": "completed", "capabilities": ["memory.read"], "evidence": [], "warnings": []}
    monkeypatch.setattr(consent_service, "plugin_source", lambda _pid: (tmp_path, "1"))
    monkeypatch.setattr(consent_service, "read_report", lambda *_a, **_k: dict(report))

    async def not_running(self, plugin_id, **_kwargs):
        raise ServerDomainError("PLUGIN_NOT_RUNNING", "not running", 409)

    from plugin.server.application.plugins import lifecycle_service

    monkeypatch.setattr(lifecycle_service.PluginLifecycleService, "stop_plugin", not_running)
    yield SimpleNamespace(store=store, manager=manager, sources=sources, report=report)
    call_audit.close()
    call_audit.reopen()


def _legacy(manager: Manager, plugins: dict) -> Path:
    path = manager.get_runtime_config_path("plugin_consents.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 2, "plugins": plugins}), encoding="utf-8")
    return path


def _verdict(plugin_id: str, capability: str = MEMORY) -> dict | None:
    """The gate's audited decision for one use (``None``: allowed, not audited)."""
    capability_gate._last_written.clear()
    before = len(call_audit.entries())
    capability_gate.check(plugin_id, None, capability, "MEMORY_SEARCH")
    entries = call_audit.entries()
    return entries[0] if len(entries) > before else None


def _readers(plugin_id: str) -> dict:
    """What each reader concludes for memory:read of ``plugin_id``."""
    granted, ok = capability_gate.granted_capabilities(plugin_id)
    snapshot = permission_service.permission_snapshot(plugin_id)
    return {
        "gate": MEMORY in granted,
        "read_ok": ok,
        "plugin": snapshot["states"][MEMORY]["status"],
        "dialog": consent_service.consent_details(plugin_id)["decision"],
        "autostart_skips": plugin_id in consent_service.denied_plugin_ids([plugin_id]),
    }


# ---------------------------------------------------------------------------
# The gate's verdict for each stored state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("setup", "gate", "plugin", "dialog", "skips"),
    [
        ("granted", True, "granted", "approved", False),
        ("denied", False, "denied", None, False),
        ("no_record", False, "undecided", None, False),
        ("legacy_approved", True, "granted", "approved", False),
        ("legacy_denied", False, "denied", "denied", True),
    ],
)
def test_every_reader_agrees_on_the_stored_state(env, setup, gate, plugin, dialog, skips):
    env.sources["demo"] = FIRST
    if setup in ("granted", "denied"):
        env.store.decide("demo", MEMORY, setup, source=FIRST)
    elif setup == "legacy_approved":
        _legacy(env.manager, {"demo": {"decision": "approved", "approved_capabilities": ["memory.read"]}})
    elif setup == "legacy_denied":
        _legacy(env.manager, {"demo": {"decision": "denied", "approved_capabilities": []}})

    assert _readers("demo") == {
        "gate": gate, "read_ok": True, "plugin": plugin, "dialog": dialog, "autostart_skips": skips,
    }
    entry = _verdict("demo")
    if gate:
        assert entry is None
    else:
        assert (entry["verdict"], entry["reason"]) == ("would_deny", "self_not_approved")
        assert "consent_unavailable" not in entry["flags"]


def test_a_call_carries_the_rights_read_from_the_permission_store(env):
    env.store.decide("a", MEMORY, "granted")
    env.store.decide("a", PUSH, "denied")
    record = capability_gate.begin_call("a", "r1", "plugin_entry", "x", CallLink(Origin("run")))
    assert record.rights == frozenset({MEMORY})
    capability_gate.end_call(record, "ok", 0)


@pytest.mark.parametrize("damage", ["corrupt", "newer_version"])
def test_an_unreadable_store_grants_nothing_and_is_flagged(env, damage):
    path = env.manager.get_runtime_config_path(plugin_permissions.FILENAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    if damage == "corrupt":
        path.write_text("{not json", encoding="utf-8")
        code = plugin_permissions.CORRUPT
    else:
        path.write_text(json.dumps({"schema_version": 99, "plugins": {}}), encoding="utf-8")
        code = plugin_permissions.UNSUPPORTED_VERSION

    assert capability_gate.granted_capabilities("demo") == (frozenset(), False)
    capability_gate._consent_failed_at = -60.0
    entry = _verdict("demo")
    assert entry["verdict"] == "would_deny" and "consent_unavailable" in entry["flags"]
    assert permission_service.permission_snapshot("demo")["states"][MEMORY]["status"] == "denied"
    with pytest.raises(ServerDomainError) as captured:
        consent_service.consent_details("demo")
    assert captured.value.code == code
    # Lists and autostart keep working on an unreadable store.
    assert consent_service.consent_summary("demo")["state"] == "analyzing"
    assert consent_service.denied_plugin_ids(["demo"]) == set()


def test_a_failed_read_uses_the_last_record_and_backs_off(env, monkeypatch):
    env.store.decide("demo", MEMORY, "granted")
    assert capability_gate.granted_capabilities("demo") == (frozenset({MEMORY}), True)
    reads = []

    def unreadable(plugin_id, _source):
        reads.append(plugin_id)
        raise ServerDomainError(plugin_permissions.READ_FAILED, "busy", 503)

    monkeypatch.setattr(env.store, "view", unreadable)
    assert capability_gate.granted_capabilities("demo") == (frozenset({MEMORY}), False)
    assert capability_gate.granted_capabilities("demo") == (frozenset({MEMORY}), False)
    assert reads == ["demo"]  # the second read waits for the back-off
    # An allowance from the last record is audited and marked, not taken
    # silently like one judged against a readable store.
    entry = _verdict("demo")
    assert entry["verdict"] == "allow" and "consent_unavailable" in entry["flags"]


def test_the_master_switch_does_not_change_the_verdict(env, monkeypatch):
    monkeypatch.setattr(settings, "PLUGIN_PERMISSION_CHECKS_DISABLED", True)
    entry = _verdict("demo")
    assert (entry["verdict"], entry["mode"], entry["enforcing"]) == ("would_deny", "observe", False)
    assert permission_service.permission_snapshot("demo")["states"][MEMORY]["enforced"] is False


# ---------------------------------------------------------------------------
# Source change: approvals are dropped, denials are kept
# ---------------------------------------------------------------------------


def test_after_a_source_change_an_approval_no_longer_applies_anywhere(env):
    _legacy(env.manager, {"demo": {"decision": "approved", "approved_capabilities": ["memory.read"]}})
    env.sources["demo"] = FIRST
    assert _readers("demo")["gate"] is True

    env.sources["demo"] = SECOND
    assert _readers("demo") == {
        "gate": False, "read_ok": True, "plugin": "undecided", "dialog": None, "autostart_skips": False,
    }
    assert consent_service.consent_details("demo")["state"] == "confirm"
    permission_service.reconcile_permission_source("demo")
    stored = env.store.read("demo")
    assert stored["permissions"] == {} and LEGACY_APPROVED not in stored


def test_after_a_source_change_a_denial_still_applies_everywhere(env):
    _legacy(env.manager, {"demo": {"decision": "denied", "approved_capabilities": []}})
    env.sources["demo"] = FIRST
    permission_service.reconcile_permission_source("demo")

    env.sources["demo"] = SECOND
    expected = {
        "gate": False, "read_ok": True, "plugin": "denied", "dialog": "denied", "autostart_skips": True,
    }
    assert _readers("demo") == expected
    permission_service.reconcile_permission_source("demo")
    assert env.store.read("demo")[LEGACY_DENIED] is True
    assert env.store.read("demo")["source"] == SECOND.to_dict()
    assert _readers("demo") == expected


def test_what_a_source_change_keeps_is_decided_in_one_place(env, monkeypatch):
    monkeypatch.setattr(plugin_permissions, "KEPT_ON_SOURCE_CHANGE", frozenset())
    _legacy(env.manager, {"demo": {"decision": "denied", "approved_capabilities": []}})
    env.sources["demo"] = FIRST
    permission_service.reconcile_permission_source("demo")
    env.sources["demo"] = SECOND
    assert _readers("demo")["dialog"] is None
    assert permission_service.forget_plugin_grants(("demo",)) is True
    assert env.store.read("demo") == {}


def test_dropping_grants_without_a_new_source_keeps_a_denial(env):
    _legacy(
        env.manager,
        {
            "denied": {"decision": "denied", "approved_capabilities": []},
            "approved": {"decision": "approved", "approved_capabilities": ["memory.read"]},
        },
    )
    env.sources.update(denied=FIRST, approved=FIRST)
    assert permission_service.forget_plugin_grants(("denied", "approved")) is True

    assert env.store.read("approved") == {}
    kept = env.store.read("denied")
    assert kept[LEGACY_DENIED] is True and kept["permissions"] == {}
    assert kept["source"] == FIRST.to_dict()
    assert _readers("denied")["autostart_skips"] is True
    # Nothing left to drop: no write.
    before = env.manager.get_runtime_config_path(plugin_permissions.FILENAME).read_bytes()
    assert permission_service.drop_grants_on_source_change("denied") == kept
    after = env.manager.get_runtime_config_path(plugin_permissions.FILENAME).read_bytes()
    assert after == before


# ---------------------------------------------------------------------------
# legacy_approved
# ---------------------------------------------------------------------------


def test_an_imported_approval_without_permissions_stays_approved(env):
    env.report.update(status="partial", capabilities=[])
    _legacy(env.manager, {"demo": {"decision": "approved", "approved_capabilities": []}})

    assert env.store.read("demo")[LEGACY_APPROVED] is True
    details = consent_service.consent_details("demo")
    assert (details["decision"], details["state"]) == ("approved", "clear")


async def test_an_approval_without_permissions_is_not_asked_again(env):
    env.report.update(status="partial", capabilities=[])
    details = await asyncio.to_thread(consent_service.consent_details, "demo")
    assert (details["decision"], details["state"]) == (None, "confirm")

    await consent_service.approve_plugin(
        "demo", version="1", unconfirmed=[], analysis_status="partial"
    )
    details = await asyncio.to_thread(consent_service.consent_details, "demo")
    assert (details["decision"], details["state"]) == ("approved", "clear")
    assert env.store.read("demo")[LEGACY_APPROVED] is True

    await consent_service.deny_plugin("demo")
    record = env.store.read("demo")
    assert record[LEGACY_DENIED] is True and LEGACY_APPROVED not in record
    await consent_service.revoke_plugin("demo")
    record = env.store.read("demo")
    assert LEGACY_DENIED not in record and LEGACY_APPROVED not in record


def test_unknown_record_keys_survive_writes(env):
    path = env.manager.get_runtime_config_path(plugin_permissions.FILENAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugins": {
                    "demo": {"source": FIRST.to_dict(), "permissions": {}, "later_marker": True}
                },
            }
        ),
        encoding="utf-8",
    )
    env.store.decide("demo", MEMORY, "granted", source=FIRST)
    env.store.apply_legacy_decision("demo", "approved", [], source=FIRST)
    env.store.sync_source("demo", FIRST)
    assert json.loads(path.read_text(encoding="utf-8"))["plugins"]["demo"]["later_marker"] is True


# ---------------------------------------------------------------------------
# Audit: enforcing
# ---------------------------------------------------------------------------


def test_every_audit_record_says_nothing_was_refused(env):
    env.store.decide("a", MEMORY, "granted")
    record = capability_gate.begin_call("a", "r1", "plugin_entry", "x", CallLink(Origin("run")))
    capability_gate.check("a", "r1", MEMORY, "MEMORY_SEARCH")  # allowed, counted
    capability_gate.check("a", "r1", PUSH, "MESSAGE_PUSH")  # would deny, disclosure only
    capability_gate.check("b", None, MEMORY, "MEMORY_SEARCH")  # would deny, enforced
    capability_gate.end_call(record, "ok", 0)
    entries = call_audit.entries()
    assert {entry["kind"] for entry in entries} == {"decision", "forward", "allow_summary"}
    assert ("b", MEMORY, "would_deny") in {
        (entry.get("plugin"), entry.get("capability"), entry.get("verdict")) for entry in entries
    }
    assert all(entry["enforcing"] is False for entry in entries)
    assert all(entry["mode"] == "enforce" for entry in entries)


@pytest.mark.parametrize(
    ("capability", "checks_disabled", "expected"),
    [(MEMORY, False, True), (PUSH, False, False), (MEMORY, True, False)],
)
def test_enforcing_follows_a_refusal_once_refusals_are_carried_out(
    env, monkeypatch, capability, checks_disabled, expected,
):
    monkeypatch.setattr(capability_gate, "REFUSES_CALLS", True)
    monkeypatch.setattr(settings, "PLUGIN_PERMISSION_CHECKS_DISABLED", checks_disabled)
    assert _verdict("demo", capability)["enforcing"] is expected


def test_the_dialog_shows_when_an_imported_approval_was_decided(env):
    _legacy(
        env.manager,
        {"demo": {"decision": "approved", "approved_capabilities": ["memory.read"],
                  "decided_at": "2026-01-02T03:04:05+00:00"}},
    )
    env.store.sync_source("demo", FIRST)  # a later write does not move it
    assert consent_service.consent_details("demo")["decided_at"] == "2026-01-02T03:04:05+00:00"


def test_dropping_grants_saves_a_first_use_import_or_reports_failure(env, monkeypatch):
    _legacy(env.manager, {"demo": {"decision": "approved", "approved_capabilities": ["memory.read"]}})
    real_save = env.manager.save_json_config

    def cannot_save(_name, _data):
        raise OSError("disk full")

    monkeypatch.setattr(env.manager, "save_json_config", cannot_save)
    assert env.store.read("demo")["permissions"]  # import held in memory only
    assert permission_service.forget_plugin_grants(("demo",)) is False
    assert env.store.read("demo")["permissions"]

    monkeypatch.setattr(env.manager, "save_json_config", real_save)
    assert permission_service.forget_plugin_grants(("demo",)) is True
    on_disk = json.loads(
        env.manager.get_runtime_config_path(plugin_permissions.FILENAME).read_text(encoding="utf-8")
    )
    assert "demo" not in on_disk["plugins"]
