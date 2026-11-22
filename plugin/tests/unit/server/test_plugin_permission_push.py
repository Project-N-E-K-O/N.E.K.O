"""Permission snapshots for plugin processes and the push that keeps them current."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from plugin import settings
from plugin.sdk.shared.permissions import PERMISSIONS, UNENFORCED_PERMISSIONS
from plugin.sdk.shared.permissions.runtime import PluginPermissions
from plugin.server.application.plugins import capability_gate, permission_push, permission_service
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure import plugin_permissions
from plugin.server.infrastructure.plugin_permissions import (
    CONVERSATIONS_READ,
    MEMORY_READ,
    MESSAGES_PUSH,
    PluginPermissionStore,
    PluginSource,
)

pytestmark = pytest.mark.plugin_unit

NETWORK = "system.network:connect"
ENV = "system.env:read"


class ConfigManager:
    def __init__(self, root: Path):
        self.root = root
        self.fail: Exception | None = None

    def get_runtime_config_path(self, name):
        return self.root / name

    def save_json_config(self, name, data):
        if self.fail is not None:
            raise self.fail
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_text(json.dumps(data), encoding="utf-8")


class Log:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def __getattr__(self, level):
        return lambda message, *args: self.lines.append((level, message.format(*args)))


@pytest.fixture
def manager(tmp_path):
    return ConfigManager(tmp_path / "config")


@pytest.fixture
def store(monkeypatch, manager):
    store = PluginPermissionStore(manager, source_resolver=lambda _pid: None)
    monkeypatch.setattr(permission_service, "permission_store", store)
    monkeypatch.setattr(permission_service, "current_source", lambda _pid: None)
    monkeypatch.setattr(permission_service, "_last_records", {})
    monkeypatch.setattr(permission_service, "_removals", {})
    monkeypatch.setattr(permission_service, "_reloads", 0)
    monkeypatch.setattr(permission_push, "_send_failures", {})
    monkeypatch.setattr(settings, "PLUGIN_PERMISSION_CHECKS_DISABLED", False)
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", None)
    return store


@pytest.fixture
def changes(monkeypatch):
    seen: list[frozenset[str] | None] = []
    monkeypatch.setattr(plugin_permissions, "_change_listeners", [seen.append])
    return seen


def _states(plugin_id):
    perms = PluginPermissions()
    assert perms.apply(permission_service.permission_snapshot(plugin_id))
    return perms


# ---------------------------------------------------------------------------
# Store change notifications: every write reports the plugins it changed
# ---------------------------------------------------------------------------


def test_first_load_reports_every_plugin(store, changes):
    store.read("demo")
    store.read("demo")
    assert changes == [None]


def test_each_write_reports_the_changed_plugin(store, changes):
    store.read("demo")
    changes.clear()
    store.decide("demo", MEMORY_READ, "granted")
    store.clear("demo", MEMORY_READ)
    store.apply_legacy_decision("demo", "approved", ["memory.read"])
    store.apply_legacy_decision("other", "denied")
    previous = store.forget("demo")
    store.restore("demo", previous)
    assert changes == [frozenset({"demo"})] * 3 + [frozenset({"other"})] + [frozenset({"demo"})] * 2


def test_source_reconciliation_reports_only_real_changes(store, changes):
    store.decide("demo", MEMORY_READ, "granted", source=PluginSource("market", "m-1"))
    changes.clear()
    store.sync_source("demo", PluginSource("market", "m-1"))
    store.sync_source("demo", None)
    assert changes == []
    store.sync_source("demo", PluginSource("manual"))
    assert changes == [frozenset({"demo"})]


def test_writes_that_change_nothing_report_nothing(store, changes):
    store.read("demo")
    changes.clear()
    store.clear("demo", MEMORY_READ)
    store.forget("demo")
    store.restore("demo", None)
    store.apply_legacy_decision("demo", None)
    assert changes == []


def test_a_failed_save_reports_nothing(store, manager, changes):
    store.read("demo")
    changes.clear()
    manager.fail = OSError("disk")
    with pytest.raises(ServerDomainError):
        store.decide("demo", MEMORY_READ, "granted")
    assert changes == []


def test_a_failing_listener_does_not_fail_the_write(monkeypatch, store):
    def broken(_ids):
        raise RuntimeError("boom")

    monkeypatch.setattr(plugin_permissions, "_change_listeners", [broken])
    store.decide("demo", MEMORY_READ, "granted")
    assert store.read("demo")["permissions"][MEMORY_READ]["state"] == "granted"


# ---------------------------------------------------------------------------
# Snapshot resolution
# ---------------------------------------------------------------------------


def test_snapshot_covers_every_permission_with_four_states_at_most(store):
    snapshot = permission_service.permission_snapshot("demo")
    assert set(snapshot["states"]) == set(PERMISSIONS)
    assert all(s["status"] == "undecided" for s in snapshot["states"].values())
    assert {p for p, s in snapshot["states"].items() if not s["enforced"]} == UNENFORCED_PERMISSIONS


def test_grant_condition_decides_background(store):
    store.decide("demo", MEMORY_READ, "granted", condition="anytime")
    store.decide("demo", CONVERSATIONS_READ, "granted", condition="in_use")
    store.decide("demo", NETWORK, "denied", condition="anytime")
    perms = _states("demo")
    assert (perms.get(MEMORY_READ).status, perms.get(MEMORY_READ).background) == ("granted", True)
    assert (perms.get(CONVERSATIONS_READ).status, perms.get(CONVERSATIONS_READ).background) == ("granted", False)
    assert (perms.get(NETWORK).status, perms.get(NETWORK).background) == ("denied", False)


def test_legacy_approval_is_granted_in_the_background(store):
    store.apply_legacy_decision("demo", "approved", ["memory.read", "messages.push"])
    perms = _states("demo")
    assert (perms.get(MEMORY_READ).status, perms.get(MEMORY_READ).background) == ("granted", True)
    push = perms.get(MESSAGES_PUSH)
    assert (push.status, push.background, push.enforced) == ("granted", True, False)


def test_legacy_whole_plugin_denial(store):
    store.apply_legacy_decision("demo", "denied")
    perms = _states("demo")
    assert perms.get(MEMORY_READ).status == "denied"
    assert perms.get(CONVERSATIONS_READ).status == "denied"
    # Permissions the earlier flow did not know are undecided.
    assert perms.get(NETWORK).status == "undecided"
    # Disclosure only: granted or undecided, never denied.
    assert perms.get(MESSAGES_PUSH).status == "undecided"
    # An explicit later decision wins over the denial.
    store.clear("demo", MEMORY_READ)
    assert _states("demo").get(MEMORY_READ).status == "undecided"


def test_disclosure_only_permissions_are_never_enforced(store):
    store.decide("demo", ENV, "denied")
    store.decide("demo", MESSAGES_PUSH, "denied")
    perms = _states("demo")
    assert (perms.get(ENV).status, perms.get(ENV).enforced) == ("denied", False)
    assert (perms.get(MESSAGES_PUSH).status, perms.get(MESSAGES_PUSH).enforced) == ("undecided", False)


@pytest.mark.parametrize(
    ("host_setting", "legacy"), [(True, None), (False, "off"), (False, "observe")]
)
def test_master_switch_on_makes_every_permission_unenforced(monkeypatch, store, host_setting, legacy):
    monkeypatch.setattr(settings, "PLUGIN_PERMISSION_CHECKS_DISABLED", host_setting)
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", legacy)
    store.decide("demo", MEMORY_READ, "granted")
    perms = _states("demo")
    assert all(perms.get(p).enforced is False for p in PERMISSIONS)
    assert perms.get(MEMORY_READ).status == "granted"


def test_a_source_change_is_seen_as_no_grants(monkeypatch, store):
    store.decide("demo", MEMORY_READ, "granted", source=PluginSource("market", "m-1"))
    monkeypatch.setattr(permission_service, "current_source", lambda _pid: PluginSource("manual"))
    assert _states("demo").get(MEMORY_READ).status == "undecided"


def test_read_failure_keeps_the_last_record_read(monkeypatch, store):
    store.decide("demo", MEMORY_READ, "granted")
    assert _states("demo").get(MEMORY_READ).status == "granted"
    log = Log()
    monkeypatch.setattr(permission_service, "logger", log)

    def unreadable(_pid):
        raise ServerDomainError(plugin_permissions.READ_FAILED, "x", 503)

    monkeypatch.setattr(permission_service, "permission_record", unreadable)
    assert _states("demo").get(MEMORY_READ).status == "granted"
    assert log.lines and log.lines[0][0] == "error" and "last read" in log.lines[0][1]


def test_read_failure_without_any_earlier_read_denies_enforced_permissions(monkeypatch, store):
    log = Log()
    monkeypatch.setattr(permission_service, "logger", log)

    def unreadable(_pid):
        raise ServerDomainError(plugin_permissions.CORRUPT, "x", 500)

    monkeypatch.setattr(permission_service, "permission_record", unreadable)
    perms = _states("demo")
    assert perms.get(MEMORY_READ).status == "denied"
    assert perms.get(MESSAGES_PUSH).status == "undecided"
    assert "PLUGIN_PERMISSIONS_CORRUPT" in log.lines[0][1]


def _unreadable(monkeypatch):
    def unreadable(_pid):
        raise ServerDomainError(plugin_permissions.READ_FAILED, "x", 503)

    monkeypatch.setattr(permission_service, "permission_record", unreadable)


def test_removed_grants_are_not_reused_after_a_read_failure(monkeypatch, store):
    store.decide("demo", MEMORY_READ, "granted")
    assert _states("demo").get(MEMORY_READ).status == "granted"
    permission_service.forget_permissions("demo")
    monkeypatch.setattr(permission_service, "logger", Log())
    _unreadable(monkeypatch)
    assert _states("demo").get(MEMORY_READ).status == "denied"


@pytest.mark.parametrize("removal", ["forget", "restore_none"])
def test_a_read_started_before_removal_does_not_bring_grants_back(monkeypatch, store, removal):
    store.decide("demo", MEMORY_READ, "granted")
    real_record = permission_service.permission_record

    def record_then_remove(plugin_id):
        record = real_record(plugin_id)  # read before the removal ...
        if removal == "forget":
            permission_service.forget_permissions(plugin_id)
        else:
            permission_service.restore_permissions(plugin_id, None)
        return record  # ... finishes after it

    monkeypatch.setattr(permission_service, "permission_record", record_then_remove)
    permission_service.permission_snapshot("demo")
    assert "demo" not in permission_service._last_records
    monkeypatch.setattr(permission_service, "logger", Log())
    _unreadable(monkeypatch)
    assert _states("demo").get(MEMORY_READ).status == "denied"


def test_an_older_read_does_not_replace_a_newer_remembered_record(monkeypatch, store):
    store.decide("demo", MEMORY_READ, "granted")
    newer = permission_service.permission_snapshot("demo")["revision"]
    # A snapshot numbered earlier that finishes reading later.
    monkeypatch.setattr(permission_service, "_snapshot_revision", iter([newer - 1]))
    monkeypatch.setattr(permission_service, "permission_record", lambda _pid: {"permissions": {}})
    permission_service.permission_snapshot("demo")
    assert permission_service._last_records["demo"][0] == newer


def test_snapshot_revisions_grow(store):
    first = permission_service.permission_snapshot("demo")["revision"]
    second = permission_service.permission_snapshot("demo")["revision"]
    assert second > first


# ---------------------------------------------------------------------------
# Granted capabilities: earlier names and permission ids both normalise
# ---------------------------------------------------------------------------


def test_granted_capabilities_normalises_mixed_names(monkeypatch):
    from plugin.server.infrastructure import plugin_consent

    monkeypatch.setattr(capability_gate, "_consent_failed_at", -10_000.0)
    monkeypatch.setattr(
        plugin_consent.consent_store,
        "read",
        lambda _pid: {
            "decision": "approved",
            "approved_capabilities": ["memory.read", CONVERSATIONS_READ, "unknown.cap"],
        },
    )
    granted, ok = capability_gate.granted_capabilities("demo")
    assert ok and granted == frozenset({MEMORY_READ, CONVERSATIONS_READ})


# ---------------------------------------------------------------------------
# Push to running plugin processes
# ---------------------------------------------------------------------------


class Host:
    """Stands in for PluginHost; applies pushes to a real ctx.permissions copy."""

    def __init__(self, *, alive=True, fail=None):
        self.alive, self.fail = alive, fail
        self.permissions = PluginPermissions()
        self.pushes: list[dict] = []
        self.attempts = 0
        self.seen: list[tuple[str, str]] = []

    def set_initial_permissions(self, snapshot):
        self.permissions.apply(snapshot)
        self.permissions.on_change(lambda permission, state: self.seen.append((permission, state.status)))

    def is_alive(self):
        return self.alive

    async def push_permissions(self, snapshot):
        self.attempts += 1
        if self.fail is not None:
            raise self.fail
        self.pushes.append(snapshot)
        self.permissions.apply({"type": "PERMISSIONS_UPDATE", **snapshot})


@pytest.fixture
def hosts(monkeypatch):
    busy["left"] = 0
    registered: dict[str, Host] = {}
    monkeypatch.setattr(
        permission_push,
        "_running_hosts",
        lambda: None if busy["left"] and _consume(busy) else dict(registered),
    )
    return registered


# How many host-table lookups still find the table busy.
busy = {"left": 0}


def _consume(state):
    state["left"] -= 1
    return True


@pytest.fixture
async def service(monkeypatch, store, hosts):
    monkeypatch.setattr(plugin_permissions, "_change_listeners", [])
    service = permission_push.PermissionPushService()
    service.start()
    yield service
    await service.stop()


async def _until(condition, timeout: float = 5.0):
    """Wait for ``condition()`` to hold; fails the test after ``timeout``."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


def _idle(service) -> bool:
    """Nothing queued or running in the push service."""
    return (
        not service._pending
        and not service._all
        and not service._wakeup.is_set()
        and not service._inflight
    )


async def test_push_reaches_the_running_plugin(store, hosts):
    host = hosts["demo"] = Host()
    host.set_initial_permissions(permission_service.permission_snapshot("demo"))
    store.decide("demo", MEMORY_READ, "granted")
    assert await permission_push.push_permissions("demo")
    assert host.permissions.get(MEMORY_READ).status == "granted"
    assert host.seen == [(MEMORY_READ, "granted")]


async def test_no_push_to_a_stopped_or_missing_plugin(store, hosts):
    hosts["dead"] = Host(alive=False)
    assert await permission_push.push_permissions("dead") is False
    assert await permission_push.push_permissions("missing") is False
    assert hosts["dead"].pushes == []


async def test_a_failed_push_is_logged(monkeypatch, store, hosts):
    log = Log()
    monkeypatch.setattr(permission_push, "logger", log)
    hosts["demo"] = Host(fail=RuntimeError("downlink closed"))
    assert await permission_push.push_permissions("demo") is False
    (level, line), = log.lines
    assert level == "error" and "plugin_id=demo" in line and "RuntimeError" in line
    assert "failures_in_a_row=1" in line


@pytest.mark.parametrize(
    "write",
    [
        "decide_permission",
        "mirror_consent_decision",
        "mirror_consent_revoke",
        "forget_permissions",
        "restore_permissions",
        "reconcile_permission_source",
    ],
)
async def test_every_store_write_reaches_the_plugin(monkeypatch, service, store, hosts, write):
    store.apply_legacy_decision("demo", "approved", ["memory.read"], source=PluginSource("market", "m-1"))
    host = hosts["demo"] = Host()
    host.set_initial_permissions(permission_service.permission_snapshot("demo"))
    assert host.permissions.get(MEMORY_READ).status == "granted"
    if write == "decide_permission":
        await asyncio.to_thread(permission_service.decide_permission, "demo", MEMORY_READ, "denied")
        expected = "denied"
    elif write == "mirror_consent_decision":
        await asyncio.to_thread(permission_service.mirror_consent_decision, "demo", "denied")
        expected = "denied"
    elif write == "mirror_consent_revoke":
        await asyncio.to_thread(permission_service.mirror_consent_decision, "demo", None)
        expected = "undecided"
    elif write == "forget_permissions":
        await asyncio.to_thread(permission_service.forget_permissions, "demo")
        expected = "undecided"
    elif write == "restore_permissions":
        await asyncio.to_thread(permission_service.restore_permissions, "demo", None)
        expected = "undecided"
    else:
        monkeypatch.setattr(permission_service, "current_source", lambda _pid: PluginSource("manual"))
        await asyncio.to_thread(permission_service.reconcile_permission_source, "demo")
        expected = "undecided"
    await _until(lambda: host.permissions.get(MEMORY_READ).status == expected and _idle(service))
    assert (MEMORY_READ, expected) in host.seen
    assert len(host.seen) == len(set(host.seen))  # one notification per change


async def test_first_load_pushes_to_every_running_plugin(service, store, hosts):
    hosts["a"], hosts["b"] = Host(), Host()
    await asyncio.to_thread(store.read, "a")
    await _until(lambda: hosts["a"].pushes and hosts["b"].pushes and _idle(service))
    assert len(hosts["a"].pushes) == 1 and len(hosts["b"].pushes) == 1


async def test_changes_are_coalesced_and_the_last_state_wins(service, store, hosts):
    host = hosts["demo"] = Host()
    host.set_initial_permissions(permission_service.permission_snapshot("demo"))

    def burst():
        for state in ("granted", "denied", "granted", "denied"):
            store.decide("demo", MEMORY_READ, state)

    await asyncio.to_thread(burst)
    await _until(lambda: host.permissions.get(MEMORY_READ).status == "denied" and _idle(service))
    assert host.permissions.get(MEMORY_READ).status == "denied"
    assert 1 <= len(host.pushes) <= 4


async def test_stale_snapshots_arriving_late_are_ignored(store, hosts):
    host = hosts["demo"] = Host()
    old = permission_service.permission_snapshot("demo")
    store.decide("demo", MEMORY_READ, "granted")
    new = permission_service.permission_snapshot("demo")
    host.set_initial_permissions(new)
    await host.push_permissions(old)
    assert host.permissions.get(MEMORY_READ).status == "granted"


async def test_stopped_service_no_longer_listens(store, hosts):
    service = permission_push.PermissionPushService()
    service.start()
    await service.stop()
    assert service.notify not in plugin_permissions._change_listeners
    service.notify(frozenset({"demo"}))  # no loop: nothing scheduled, no error


# ---------------------------------------------------------------------------
# Push service robustness
# ---------------------------------------------------------------------------


async def test_a_busy_host_table_is_retried_not_treated_as_not_running(monkeypatch, service, store, hosts):
    monkeypatch.setattr(permission_push, "HOSTS_BUSY_RETRY_SECONDS", 0.01)
    host = hosts["demo"] = Host()
    host.set_initial_permissions(permission_service.permission_snapshot("demo"))
    await _until(lambda: _idle(service))  # the store's first load is pushed first
    busy["left"] = 3
    await asyncio.to_thread(store.decide, "demo", MEMORY_READ, "granted")
    await _until(lambda: host.permissions.get(MEMORY_READ).status == "granted")
    assert busy["left"] == 0


async def test_a_host_table_that_stays_busy_is_logged_and_still_retried(monkeypatch, service, store, hosts):
    monkeypatch.setattr(permission_push, "HOSTS_BUSY_RETRY_SECONDS", 0.001)
    monkeypatch.setattr(permission_push, "HOSTS_BUSY_MAX_RETRIES", 2)
    monkeypatch.setattr(permission_push, "READ_RETRY_SECONDS", 0.05)
    log = Log()
    monkeypatch.setattr(permission_push, "logger", log)
    host = hosts["demo"] = Host()
    host.set_initial_permissions(permission_service.permission_snapshot("demo"))
    await _until(lambda: _idle(service))  # the store's first load is pushed first
    busy["left"] = 3  # more than the quick retries allow
    await asyncio.to_thread(store.decide, "demo", MEMORY_READ, "granted")
    await _until(lambda: any(level == "warning" and "plugin_id=demo" in line for level, line in log.lines))
    # Not dropped: delivered once the table is free again.
    await _until(lambda: host.permissions.get(MEMORY_READ).status == "granted" and _idle(service))
    assert busy["left"] == 0


async def test_a_stuck_plugin_does_not_hold_up_another(monkeypatch, service, store, hosts):
    release = asyncio.Event()

    class StuckHost(Host):
        async def push_permissions(self, snapshot):
            await release.wait()

    hosts["stuck"] = StuckHost()
    fast = hosts["fast"] = Host()
    fast.set_initial_permissions(permission_service.permission_snapshot("fast"))
    await asyncio.to_thread(store.decide, "stuck", MEMORY_READ, "granted")
    await _until(lambda: "stuck" in service._inflight)
    await asyncio.to_thread(store.decide, "fast", MEMORY_READ, "granted")
    # Well inside PUSH_TIMEOUT_SECONDS: the stuck push is still pending.
    await _until(lambda: fast.permissions.get(MEMORY_READ).status == "granted", timeout=2.0)
    assert "stuck" in service._inflight
    release.set()


async def test_a_change_during_a_running_push_is_pushed_again(monkeypatch, service, store, hosts):
    gate = asyncio.Event()

    class SlowHost(Host):
        waiting = False

        async def push_permissions(self, snapshot):
            if not self.pushes:
                self.waiting = True
                await gate.wait()
            await super().push_permissions(snapshot)

    host = hosts["demo"] = SlowHost()
    host.set_initial_permissions(permission_service.permission_snapshot("demo"))
    await asyncio.to_thread(store.decide, "demo", MEMORY_READ, "granted")
    await _until(lambda: host.waiting)  # the first push is held at the gate
    await asyncio.to_thread(store.decide, "demo", MEMORY_READ, "denied")
    await _until(lambda: "demo" in service._rerun)
    gate.set()
    await _until(lambda: host.permissions.get(MEMORY_READ).status == "denied" and _idle(service))


async def test_an_error_while_collecting_does_not_stop_the_service(monkeypatch, service, store, hosts):
    log = Log()
    monkeypatch.setattr(permission_push, "logger", log)
    host = hosts["demo"] = Host()
    host.set_initial_permissions(permission_service.permission_snapshot("demo"))
    real_take = service._take
    calls = {"n": 0}

    async def take():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return await real_take()

    monkeypatch.setattr(service, "_take", take)
    service.notify(frozenset({"demo"}))
    await _until(lambda: any(level == "error" for level, _ in log.lines))
    await asyncio.to_thread(store.decide, "demo", MEMORY_READ, "granted")
    await _until(lambda: host.permissions.get(MEMORY_READ).status == "granted")


async def test_a_fallback_snapshot_is_rebuilt_once_reads_recover(monkeypatch, service, store, hosts):
    monkeypatch.setattr(permission_push, "READ_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(permission_service, "logger", Log())
    store.decide("demo", MEMORY_READ, "granted")
    host = hosts["demo"] = Host()
    host.set_initial_permissions(permission_service.permission_snapshot("demo"))
    real_record = permission_service.permission_record
    broken = {"on": True}

    def flaky(plugin_id):
        if broken["on"]:
            raise ServerDomainError(plugin_permissions.READ_FAILED, "x", 503)
        return real_record(plugin_id)

    monkeypatch.setattr(permission_service, "permission_record", flaky)
    # The store changes behind a failing read (e.g. edited on disk).
    store._document["plugins"]["demo"]["permissions"].clear()
    service.notify(frozenset({"demo"}))
    await _until(lambda: host.pushes and "demo" in service._timers)
    assert host.permissions.get(MEMORY_READ).status == "granted"  # last read
    broken["on"] = False
    await _until(lambda: host.permissions.get(MEMORY_READ).status == "undecided")
    await _until(lambda: "demo" not in service._timers and _idle(service))


def test_undoing_a_consent_mirror_to_nothing_counts_as_a_removal(store):
    undo = permission_service.mirror_consent_decision("demo", "approved", ["memory.read"])
    assert _states("demo").get(MEMORY_READ).status == "granted"
    before = permission_service._removals.get("demo", 0)
    undo()
    assert "demo" not in permission_service._last_records
    assert permission_service._removals.get("demo", 0) > before


def test_host_lookup_reads_the_live_table_not_a_stale_cache():
    from plugin.core.state import state

    marker = object()
    with state._snapshot_cache_lock:
        saved_cache = dict(state._snapshot_cache["hosts"])
        # A cached table from before the plugin started, still within its TTL.
        state._snapshot_cache["hosts"] = {"data": {}, "timestamp": 10**12}
    with state.acquire_plugin_hosts_write_lock():
        state.plugin_hosts["fresh_plugin"] = marker
    try:
        hosts = permission_push._running_hosts()
        assert hosts is not None and hosts.get("fresh_plugin") is marker
    finally:
        with state.acquire_plugin_hosts_write_lock():
            state.plugin_hosts.pop("fresh_plugin", None)
        with state._snapshot_cache_lock:
            state._snapshot_cache["hosts"] = saved_cache


def test_host_lookup_reports_a_locked_table_as_unknown_quietly(monkeypatch):
    class Locked:
        def acquire_read(self, timeout):
            assert timeout == permission_push.HOSTS_LOCK_TIMEOUT_SECONDS
            return False

    monkeypatch.setattr(permission_push.state, "_plugin_hosts_rwlock", Locked())
    assert permission_push._running_hosts() is None


async def test_one_read_retry_per_plugin_however_many_changes(monkeypatch, service, store, hosts):
    monkeypatch.setattr(permission_push, "READ_RETRY_SECONDS", 30.0)
    monkeypatch.setattr(permission_service, "logger", Log())
    host = hosts["demo"] = Host()
    host.set_initial_permissions(permission_service.permission_snapshot("demo"))
    _unreadable(monkeypatch)
    for count in range(1, 5):
        service.notify(frozenset({"demo"}))
        await _until(lambda count=count: len(host.pushes) == count and _idle(service))
    assert list(service._timers) == ["demo"]


async def test_a_failed_send_keeps_retrying_an_unreadable_store(monkeypatch, service, store, hosts):
    monkeypatch.setattr(permission_push, "READ_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(permission_service, "logger", Log())
    monkeypatch.setattr(permission_push, "logger", Log())
    store.decide("demo", MEMORY_READ, "granted")
    host = hosts["demo"] = Host()
    host.set_initial_permissions(permission_service.permission_snapshot("demo"))
    real_record = permission_service.permission_record
    broken = {"on": True}

    def flaky(plugin_id):
        if broken["on"]:
            raise ServerDomainError(plugin_permissions.READ_FAILED, "x", 503)
        return real_record(plugin_id)

    monkeypatch.setattr(permission_service, "permission_record", flaky)
    store._document["plugins"]["demo"]["permissions"].clear()
    host.fail = RuntimeError("downlink busy")  # the fallback push is not delivered either
    service.notify(frozenset({"demo"}))
    # The failing send happened and a read retry is pending.
    await _until(lambda: host.attempts >= 1 and "demo" in service._timers and _idle(service))
    assert host.pushes == []
    host.fail = None
    broken["on"] = False
    await _until(lambda: host.permissions.get(MEMORY_READ).status == "undecided")
    await _until(lambda: "demo" not in service._timers and _idle(service))


async def test_stopping_cancels_pending_retries(monkeypatch, store, hosts):
    monkeypatch.setattr(plugin_permissions, "_change_listeners", [])
    monkeypatch.setattr(permission_push, "READ_RETRY_SECONDS", 30.0)
    monkeypatch.setattr(permission_service, "logger", Log())
    service = permission_push.PermissionPushService()
    service.start()
    hosts["demo"] = Host()
    _unreadable(monkeypatch)
    service.notify(frozenset({"demo"}))
    await _until(lambda: service._timers and _idle(service))
    (timer,) = service._timers.values()
    await service.stop()
    assert timer.cancelled() and not service._timers


async def test_a_fallback_launch_push_is_retried_by_the_service(monkeypatch, store, hosts):
    """The post-registration push at plugin start goes through
    ``push_permissions`` directly; a fallback snapshot there is rebuilt too."""
    monkeypatch.setattr(plugin_permissions, "_change_listeners", [])
    monkeypatch.setattr(permission_push, "READ_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(permission_service, "logger", Log())
    service = permission_push.permission_push_service
    service.start()
    try:
        store.decide("demo", MEMORY_READ, "granted")
        host = hosts["demo"] = Host()
        host.set_initial_permissions(permission_service.permission_snapshot("demo"))
        real_record = permission_service.permission_record
        broken = {"on": True}

        def flaky(plugin_id):
            if broken["on"]:
                raise ServerDomainError(plugin_permissions.READ_FAILED, "x", 503)
            return real_record(plugin_id)

        monkeypatch.setattr(permission_service, "permission_record", flaky)
        store._document["plugins"]["demo"]["permissions"].clear()
        assert await permission_push.push_permissions("demo", host)
        await _until(lambda: "demo" in service._timers)
        broken["on"] = False
        await _until(lambda: host.permissions.get(MEMORY_READ).status == "undecided")
        await _until(lambda: "demo" not in service._timers and _idle(service))
    finally:
        await service.stop()


# ---------------------------------------------------------------------------
# Undelivered pushes are retried; the fallback never reports stale grants
# ---------------------------------------------------------------------------


async def test_a_failed_send_after_a_good_read_is_retried(monkeypatch, service, store, hosts):
    monkeypatch.setattr(permission_push, "READ_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(permission_push, "logger", Log())
    store.decide("demo", MEMORY_READ, "granted")
    host = hosts["demo"] = Host()
    host.set_initial_permissions(permission_service.permission_snapshot("demo"))
    host.fail = RuntimeError("downlink stuck")
    await asyncio.to_thread(store.clear, "demo", MEMORY_READ)  # the user revokes it
    await _until(lambda: host.attempts >= 1 and "demo" in service._timers and _idle(service))
    assert host.permissions.get(MEMORY_READ).status == "granted"  # not delivered yet
    host.fail = None
    await _until(lambda: host.permissions.get(MEMORY_READ).status == "undecided")
    await _until(lambda: "demo" not in service._timers and _idle(service))


async def test_a_failed_send_after_a_read_retry_keeps_retrying(monkeypatch, service, store, hosts):
    monkeypatch.setattr(permission_push, "READ_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(permission_service, "logger", Log())
    monkeypatch.setattr(permission_push, "logger", Log())
    store.decide("demo", MEMORY_READ, "granted")
    host = hosts["demo"] = Host()
    host.set_initial_permissions(permission_service.permission_snapshot("demo"))
    real_record = permission_service.permission_record
    broken = {"on": True}

    def flaky(plugin_id):
        if broken["on"]:
            raise ServerDomainError(plugin_permissions.READ_FAILED, "x", 503)
        return real_record(plugin_id)

    monkeypatch.setattr(permission_service, "permission_record", flaky)
    store._document["plugins"]["demo"]["permissions"].clear()
    service.notify(frozenset({"demo"}))
    await _until(lambda: host.pushes and "demo" in service._timers and _idle(service))
    # The read recovers but that send fails: a retry must remain.
    broken["on"] = False
    host.fail = RuntimeError("downlink stuck")
    attempts = host.attempts
    await _until(lambda: host.attempts > attempts and "demo" in service._timers and _idle(service))
    host.fail = None
    await _until(lambda: host.permissions.get(MEMORY_READ).status == "undecided")
    await _until(lambda: "demo" not in service._timers and _idle(service))


async def test_a_failed_direct_push_is_handed_to_the_service(monkeypatch, store, hosts):
    monkeypatch.setattr(plugin_permissions, "_change_listeners", [])
    monkeypatch.setattr(permission_push, "READ_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(permission_push, "logger", Log())
    service = permission_push.permission_push_service
    service.start()
    try:
        store.decide("demo", MEMORY_READ, "granted")
        host = hosts["demo"] = Host(fail=RuntimeError("downlink stuck"))
        assert await permission_push.push_permissions("demo", host) is False
        await _until(lambda: "demo" in service._timers)
        host.fail = None
        await _until(lambda: host.permissions.get(MEMORY_READ).status == "granted")
        await _until(lambda: "demo" not in service._timers and _idle(service))
    finally:
        await service.stop()


@pytest.mark.parametrize("fresh", [False, True])
async def test_backoff_grows_only_when_a_retry_is_armed(monkeypatch, service, store, hosts, fresh):
    monkeypatch.setattr(permission_push, "READ_RETRY_SECONDS", 30.0)
    monkeypatch.setattr(permission_service, "logger", Log())
    host = hosts["demo"] = Host()
    host.set_initial_permissions(permission_service.permission_snapshot("demo"))
    await _until(lambda: _idle(service))
    _unreadable(monkeypatch)
    for count in range(1, 5):
        service.notify(frozenset({"demo"}), fresh=fresh)
        await _until(lambda count=count: _idle(service) and len(host.pushes) >= count)
    assert list(service._timers) == ["demo"]
    # One timer armed: the next retry is at the base delay, not stretched.
    assert service._timers["demo"].when() - asyncio.get_running_loop().time() <= 30.5
    if not fresh:
        # Retries (not new changes) doubled the delay once, not four times.
        assert service._read_retry_delay["demo"] == 60.0


@pytest.mark.parametrize("change", ["restore_record", "source_change", "decide"])
def test_any_grant_change_drops_the_remembered_fallback(monkeypatch, store, change):
    monkeypatch.setattr(
        plugin_permissions, "_change_listeners", [permission_service._on_store_change]
    )
    store.decide("demo", MEMORY_READ, "granted", source=PluginSource("market", "m-1"))
    earlier = store.read("demo")
    store.decide("demo", NETWORK, "granted")
    assert _states("demo").get(MEMORY_READ).status == "granted"
    assert "demo" in permission_service._last_records
    if change == "restore_record":
        bare = dict(earlier, permissions={})
        permission_service.restore_permissions("demo", bare)
    elif change == "source_change":
        monkeypatch.setattr(permission_service, "current_source", lambda _pid: PluginSource("manual"))
        permission_service.reconcile_permission_source("demo")
    else:
        store.decide("demo", MEMORY_READ, "denied")
    assert "demo" not in permission_service._last_records
    monkeypatch.setattr(permission_service, "logger", Log())
    _unreadable(monkeypatch)
    assert _states("demo").get(MEMORY_READ).status == "denied"


async def test_push_soon_queues_only_on_a_running_service(monkeypatch, store, hosts):
    monkeypatch.setattr(plugin_permissions, "_change_listeners", [])
    service = permission_push.PermissionPushService()
    assert service.push_soon("demo") is False
    service.start()
    try:
        store.decide("demo", MEMORY_READ, "granted")
        host = hosts["demo"] = Host()
        assert service.push_soon("demo") is True
        await _until(lambda: host.permissions.get(MEMORY_READ).status == "granted" and _idle(service))
    finally:
        await service.stop()


def test_the_first_read_is_remembered_even_though_it_loads_the_store(monkeypatch, manager):
    store = PluginPermissionStore(manager, source_resolver=lambda _pid: None)
    store.decide("demo", MEMORY_READ, "granted")
    fresh = PluginPermissionStore(manager, source_resolver=lambda _pid: None)  # not loaded yet
    monkeypatch.setattr(permission_service, "permission_store", fresh)
    monkeypatch.setattr(permission_service, "current_source", lambda _pid: None)
    monkeypatch.setattr(permission_service, "_last_records", {})
    monkeypatch.setattr(permission_service, "_removals", {})
    monkeypatch.setattr(permission_service, "_reloads", 0)
    monkeypatch.setattr(permission_push, "_send_failures", {})
    monkeypatch.setattr(
        plugin_permissions, "_change_listeners", [permission_service._on_store_change]
    )
    assert _states("demo").get(MEMORY_READ).status == "granted"
    assert "demo" in permission_service._last_records
    monkeypatch.setattr(permission_service, "logger", Log())
    _unreadable(monkeypatch)
    assert _states("demo").get(MEMORY_READ).status == "granted"


async def test_a_new_change_restarts_the_backoff(monkeypatch, service, store, hosts):
    monkeypatch.setattr(permission_push, "READ_RETRY_SECONDS", 30.0)
    monkeypatch.setattr(permission_push, "logger", Log())
    host = hosts["demo"] = Host(fail=RuntimeError("downlink stuck"))
    service._read_retry_delay["demo"] = 60.0  # after a long run of failures
    await asyncio.to_thread(store.decide, "demo", MEMORY_READ, "granted")
    await _until(lambda: host.attempts >= 1 and "demo" in service._timers and _idle(service))
    # The retry for the new change starts again from the base delay.
    timer = service._timers["demo"]
    assert timer.when() - asyncio.get_running_loop().time() <= 30.0 + 0.5
    assert service._read_retry_delay["demo"] == 60.0


def test_a_read_overtaken_by_a_reload_is_read_again_before_it_is_remembered(monkeypatch, store):
    store.decide("demo", MEMORY_READ, "granted")
    real_record = permission_service.permission_record
    reads = []

    def read_then_reload(plugin_id):
        record = real_record(plugin_id)
        reads.append(record["permissions"][MEMORY_READ]["state"])
        if len(reads) == 1:
            # Another thread reloads the store (e.g. from a new path) and the
            # grant is now denied there; this read already saw "granted".
            store._document["plugins"]["demo"]["permissions"][MEMORY_READ]["state"] = "denied"
            permission_service._on_store_change(None)
        return record

    monkeypatch.setattr(permission_service, "permission_record", read_then_reload)
    assert _states("demo").get(MEMORY_READ).status == "denied"
    assert reads == ["granted", "denied"]
    assert permission_service._last_records["demo"][1]["permissions"][MEMORY_READ]["state"] == "denied"


def test_a_read_overtaken_by_reloads_twice_is_not_remembered(monkeypatch, store):
    store.decide("demo", MEMORY_READ, "granted")
    real_record = permission_service.permission_record

    def always_reloading(plugin_id):
        record = real_record(plugin_id)
        permission_service._on_store_change(None)
        return record

    monkeypatch.setattr(permission_service, "permission_record", always_reloading)
    permission_service.permission_snapshot("demo")
    assert "demo" not in permission_service._last_records


async def test_a_busy_stretch_logs_one_warning_and_stays_on_the_backoff(monkeypatch, service, store, hosts):
    monkeypatch.setattr(permission_push, "HOSTS_BUSY_RETRY_SECONDS", 0.001)
    monkeypatch.setattr(permission_push, "HOSTS_BUSY_MAX_RETRIES", 2)
    monkeypatch.setattr(permission_push, "READ_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(permission_push, "READ_RETRY_MAX_SECONDS", 0.01)
    log = Log()
    monkeypatch.setattr(permission_push, "logger", log)
    host = hosts["demo"] = Host()
    host.set_initial_permissions(permission_service.permission_snapshot("demo"))
    await _until(lambda: _idle(service))
    busy["left"] = 8  # 2 quick polls, then several slow retries while still busy
    await asyncio.to_thread(store.decide, "demo", MEMORY_READ, "granted")
    await _until(lambda: host.permissions.get(MEMORY_READ).status == "granted" and _idle(service))
    assert busy["left"] == 0
    warnings = [line for level, line in log.lines if level == "warning"]
    assert len(warnings) == 1
    assert "demo" not in service._busy_retries


def test_a_failed_re_read_after_a_reload_keeps_the_first_read(monkeypatch, store):
    store.decide("demo", MEMORY_READ, "granted")
    real_record = permission_service.permission_record
    calls = {"n": 0}

    def reload_then_fail(plugin_id):
        calls["n"] += 1
        if calls["n"] == 1:
            record = real_record(plugin_id)
            permission_service._on_store_change(None)
            return record
        raise ServerDomainError(plugin_permissions.READ_FAILED, "x", 503)

    monkeypatch.setattr(permission_service, "permission_record", reload_then_fail)
    monkeypatch.setattr(permission_service, "logger", Log())
    snapshot = permission_service.permission_snapshot("demo")
    assert snapshot["states"][MEMORY_READ]["status"] == "granted"
    assert not snapshot.get("read_failed")
    assert "demo" not in permission_service._last_records


async def test_repeated_send_failures_are_logged_sparingly(monkeypatch, store, hosts):
    log = Log()
    monkeypatch.setattr(permission_push, "logger", log)
    host = hosts["demo"] = Host(fail=RuntimeError("downlink stuck"))
    for _ in range(12):
        assert await permission_push.push_permissions("demo") is False
    errors = [line for level, line in log.lines if level == "error"]
    assert len(errors) == 2  # the 1st and the 10th
    assert "failures_in_a_row=10" in errors[1]
    host.fail = None
    assert await permission_push.push_permissions("demo")
    assert any(level == "info" and "again" in line for level, line in log.lines)
