"""Permission storage: one-time legacy import marker, rate-limited error
logs, and the writable host setting of the master switch."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from plugin import settings
from plugin.core import capability_policy
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure import mutation_auth, permission_switch, plugin_permissions
from plugin.server.infrastructure.plugin_permissions import (
    IMPORT_ALREADY_DONE,
    MARKER_FILENAME,
    PluginPermissionStore,
)

pytestmark = pytest.mark.plugin_unit

MEMORY = "plugin_sdk.main_agent.memory:read"


class ConfigManager:
    def __init__(self, root: Path):
        self.root = root
        self.fail: dict[str, Exception] = {}
        self.saved: list[str] = []

    def get_runtime_config_path(self, name):
        return self.root / name

    def save_json_config(self, name, data):
        if name in self.fail:
            raise self.fail[name]
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_text(json.dumps(data), encoding="utf-8")
        self.saved.append(name)


def _switch(manager, **kwargs):
    """A switch store that loads inline and records pushes."""
    kwargs.setdefault("spawn", lambda load: load())
    pushes = kwargs.pop("pushes", [])
    kwargs.setdefault("on_change", lambda: pushes.append(True))
    return permission_switch.PermissionSwitchStore(manager, **kwargs)


@pytest.fixture
def manager(tmp_path):
    return ConfigManager(tmp_path / "config")


def _legacy(manager, plugins=None):
    manager.root.mkdir(parents=True, exist_ok=True)
    document = {"schema_version": 2, "plugins": plugins or {"a": {"decision": "approved", "approved_capabilities": ["memory.read"]}}}
    (manager.root / "plugin_consents.json").write_text(json.dumps(document), encoding="utf-8")


def _marker(manager) -> Path:
    return manager.root / MARKER_FILENAME


# ---------------------------------------------------------------------------
# Legacy import marker
# ---------------------------------------------------------------------------


def test_import_is_marked_once_saved(manager):
    _legacy(manager)
    store = PluginPermissionStore(manager)

    assert store.read("a")["permissions"][MEMORY]["legacy_any"] is True

    marker = json.loads(_marker(manager).read_text(encoding="utf-8"))
    assert marker["into"] == "plugin_permissions.json"
    assert marker["imported_at"] == store.legacy_import()["imported_at"]
    assert manager.saved == ["plugin_permissions.json", MARKER_FILENAME]


def test_marked_import_is_not_imported_again(manager):
    _legacy(manager)
    _marker(manager).write_text("{}", encoding="utf-8")

    store = PluginPermissionStore(manager)

    assert store.read("a") == {}
    assert store.legacy_import()["status"] == IMPORT_ALREADY_DONE
    assert (manager.root / "plugin_permissions.json").is_file()


def test_store_imported_earlier_is_marked_on_load(manager):
    _legacy(manager)
    manager.root.joinpath("plugin_permissions.json").write_text(
        json.dumps({"schema_version": 1, "plugins": {}}), encoding="utf-8"
    )

    PluginPermissionStore(manager).read("x")

    assert _marker(manager).is_file()


def test_no_marker_without_a_legacy_file(manager):
    PluginPermissionStore(manager).read("x")
    assert not _marker(manager).exists()


def test_import_held_in_memory_is_marked_only_when_saved(manager):
    _legacy(manager)
    manager.fail["plugin_permissions.json"] = OSError("disk full")
    store = PluginPermissionStore(manager)

    assert store.read("a")["permissions"]
    assert not _marker(manager).exists()

    del manager.fail["plugin_permissions.json"]
    store.decide("b", MEMORY, "granted")
    assert _marker(manager).is_file()


def test_failed_marker_write_is_retried_on_the_next_load(manager):
    _legacy(manager)
    manager.fail[MARKER_FILENAME] = OSError("denied")
    PluginPermissionStore(manager).read("a")
    assert not _marker(manager).exists()

    del manager.fail[MARKER_FILENAME]
    store = PluginPermissionStore(manager)
    assert store.read("a")["permissions"]  # the saved import, not a new one
    assert _marker(manager).is_file()


def test_marker_that_cannot_be_checked_fails_the_read(manager, monkeypatch):
    _legacy(manager)
    real_stat = os.stat

    def stat(path, *args, **kwargs):
        if str(path).endswith(MARKER_FILENAME):
            raise PermissionError("denied")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(plugin_permissions.os, "stat", stat)
    with pytest.raises(ServerDomainError) as failure:
        PluginPermissionStore(manager).read("a")
    assert failure.value.code == plugin_permissions.READ_FAILED
    assert not (manager.root / "plugin_permissions.json").exists()


# ---------------------------------------------------------------------------
# Rate-limited error logs
# ---------------------------------------------------------------------------


class Logged:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def error(self, message, *args):
        self.lines.append(("error", message.format(*args)))

    def info(self, message, *args):
        self.lines.append(("info", message.format(*args)))

    def warning(self, message, *args):
        self.lines.append(("warning", message.format(*args)))


@pytest.fixture
def logged(monkeypatch):
    log = Logged()
    monkeypatch.setattr(plugin_permissions, "logger", log)
    return log


def test_repeated_errors_are_reported_once_per_window(logged):
    clock = {"now": 0.0}
    errors = plugin_permissions._ErrorLog(clock=lambda: clock["now"])

    for _ in range(5):
        errors.failed("read", "OSError", "store could not be read: error={}", "OSError")
    assert logged.lines == [("error", "store could not be read: error=OSError")]

    clock["now"] = plugin_permissions.ERROR_LOG_WINDOW + 1
    errors.failed("read", "OSError", "store could not be read: error={}", "OSError")
    assert logged.lines[-1] == (
        "error", "store could not be read: error=OSError (repeated; 4 more since the last report)"
    )

    errors.failed("read", "ValueError", "store could not be read: error={}", "ValueError")
    assert logged.lines[-1] == ("error", "store could not be read: error=ValueError")

    errors.recovered("read", "store can be read again")
    assert logged.lines[-1] == ("info", "store can be read again (after 7 failure(s))")
    errors.recovered("read", "store can be read again")
    assert len(logged.lines) == 4  # nothing to report twice


def test_unreadable_store_logs_once_while_it_stays_unreadable(manager, logged, monkeypatch):
    monkeypatch.setattr(plugin_permissions, "_errors", plugin_permissions._ErrorLog())
    manager.root.mkdir(parents=True)
    path = manager.root / "plugin_permissions.json"
    path.write_text("{broken", encoding="utf-8")
    store = PluginPermissionStore(manager)

    for _ in range(10):
        with pytest.raises(ServerDomainError):
            store.read("a")
    assert [line for line in logged.lines if line[0] == "error"] == [
        ("error", "plugin permission store could not be read: error=JSONDecodeError")
    ]

    path.write_text(json.dumps({"schema_version": 1, "plugins": {}}), encoding="utf-8")
    store.read("a")
    assert logged.lines[-1] == ("info", "plugin permission store can be read again (after 10 failure(s))")


def test_failing_saves_log_once_and_recovery_is_reported(manager, logged, monkeypatch):
    monkeypatch.setattr(plugin_permissions, "_errors", plugin_permissions._ErrorLog())
    store = PluginPermissionStore(manager)
    store.read("a")
    manager.fail["plugin_permissions.json"] = OSError("disk full")
    for _ in range(5):
        with pytest.raises(ServerDomainError):
            store.decide("a", MEMORY, "granted")
    assert sum(1 for level, _ in logged.lines if level == "error") == 1

    del manager.fail["plugin_permissions.json"]
    store.decide("a", MEMORY, "granted")
    assert ("info", "plugin permission store can be saved again (after 5 failure(s))") in logged.lines


# ---------------------------------------------------------------------------
# Master switch: host setting
# ---------------------------------------------------------------------------


def test_switch_setting_defaults_to_checks_on(manager):
    store = _switch(manager)
    assert store.disabled() is False


def test_switch_setting_is_saved_and_read_back(manager):
    store = _switch(manager)
    store.set_disabled(True)

    assert store.disabled() is True
    saved = json.loads((manager.root / permission_switch.FILENAME).read_text(encoding="utf-8"))
    assert saved["checks_disabled"] is True and saved["schema_version"] == 1
    assert _switch(manager).disabled() is True


@pytest.mark.parametrize("content", ["{broken", json.dumps({"checks_disabled": "yes"}), "[]"])
def test_unreadable_switch_setting_keeps_checks_on(manager, content):
    manager.root.mkdir(parents=True)
    (manager.root / permission_switch.FILENAME).write_text(content, encoding="utf-8")
    assert _switch(manager).disabled() is False


def test_unreadable_switch_setting_is_retried_later(manager):
    clock = {"now": 0.0}
    store = _switch(manager, clock=lambda: clock["now"])
    manager.root.mkdir(parents=True)
    path = manager.root / permission_switch.FILENAME
    path.write_text("{broken", encoding="utf-8")
    assert store.disabled() is False
    path.write_text(json.dumps({"checks_disabled": True}), encoding="utf-8")
    assert store.disabled() is False  # not read again yet
    clock["now"] = permission_switch.RETRY_SECONDS + 1
    assert store.disabled() is True


def test_failed_switch_write_raises_and_changes_nothing(manager):
    store = _switch(manager)
    manager.fail[permission_switch.FILENAME] = OSError("disk full")

    with pytest.raises(ServerDomainError) as failure:
        store.set_disabled(True)

    assert failure.value.code == permission_switch.WRITE_FAILED and failure.value.status_code == 503
    assert store.disabled() is False


@pytest.mark.parametrize(
    ("config", "env", "legacy", "sources"),
    [
        (True, False, None, ("host_config",)),
        (True, True, None, ("host_config", "env")),
        (False, True, "off", ("env", "legacy_env")),
        (False, False, None, ()),
    ],
)
def test_host_setting_is_one_source_of_the_switch(monkeypatch, manager, config, env, legacy, sources):
    store = _switch(manager)
    store.set_disabled(config)
    monkeypatch.setattr(permission_switch, "switch_store", store)
    monkeypatch.setattr(settings, "PLUGIN_PERMISSION_CHECKS_DISABLED", env)
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", legacy)

    assert capability_policy.checks_disabled_sources() == sources
    assert capability_policy.checks_disabled() is bool(sources)


# ---------------------------------------------------------------------------
# Master switch: routes
# ---------------------------------------------------------------------------


@pytest.fixture
def switch_env(monkeypatch, manager):
    store = _switch(manager)
    monkeypatch.setattr(permission_switch, "switch_store", store)
    monkeypatch.setattr(settings, "PLUGIN_PERMISSION_CHECKS_DISABLED", False)
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", None)
    from plugin.server.application.plugins import permission_push

    notified = []
    monkeypatch.setattr(permission_push.permission_push_service, "notify", notified.append)
    store_changes = []
    monkeypatch.setattr(plugin_permissions, "_notify_change", store_changes.append)
    yield store, notified
    # The grants did not change: no store change notification.
    assert store_changes == []


async def _request(method, body=None, headers=None):
    from plugin.server.routes.plugin_permission_switch import router

    app = FastAPI()
    app.include_router(router)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:48916") as client:
        return await client.request(method, "/plugin-permissions/master-switch", json=body, headers=headers or {})


TOKEN = {"X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN}


@pytest.mark.asyncio
async def test_get_lists_every_source(switch_env):
    body = (await _request("GET")).json()
    assert body == {
        "disabled": False,
        "sources": [
            {"source": "host_config", "disabled": False, "writable": True},
            {"source": "env", "disabled": False, "writable": False},
            {"source": "legacy_env", "disabled": False, "writable": False},
        ],
    }


@pytest.mark.asyncio
async def test_put_writes_the_host_setting_and_pushes_states(switch_env):
    store, notified = switch_env

    response = await _request("PUT", {"disabled": True}, TOKEN)

    assert response.status_code == 200
    assert response.json()["disabled"] is True
    assert store.disabled() is True
    assert notified == [None]


@pytest.mark.asyncio
async def test_put_reports_a_variable_that_keeps_checks_off(switch_env, monkeypatch):
    monkeypatch.setattr(settings, "PLUGIN_PERMISSION_CHECKS_DISABLED", True)

    body = (await _request("PUT", {"disabled": False}, TOKEN)).json()

    assert body["disabled"] is True
    env = next(row for row in body["sources"] if row["source"] == "env")
    assert env == {"source": "env", "disabled": True, "writable": False}


@pytest.mark.asyncio
async def test_put_reports_a_failed_write(switch_env, manager):
    store, notified = switch_env
    manager.fail[permission_switch.FILENAME] = OSError("disk full")

    response = await _request("PUT", {"disabled": True}, TOKEN)

    assert response.status_code == 503
    assert response.headers["X-Error-Code"] == permission_switch.WRITE_FAILED
    assert store.disabled() is False and notified == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {},  # local caller without a token
        {"X-CSRF-Token": "wrong"},
        {"Origin": "http://127.0.0.1:48916"},  # trusted page without a token
    ],
)
async def test_put_needs_a_valid_token(switch_env, headers):
    store, _ = switch_env
    response = await _request("PUT", {"disabled": True}, headers)
    assert response.status_code == 403
    assert store.disabled() is False


@pytest.mark.asyncio
async def test_put_takes_only_a_boolean(switch_env):
    response = await _request("PUT", {"disabled": "yes"}, TOKEN)
    assert response.status_code == 422


def test_each_report_counts_only_what_followed_the_previous_one(logged):
    clock = {"now": 0.0}
    errors = plugin_permissions._ErrorLog(clock=lambda: clock["now"])
    errors.failed("save", "OSError", "x={}", 1)
    errors.failed("save", "OSError", "x={}", 1)
    clock["now"] = 61.0
    errors.failed("save", "OSError", "x={}", 1)
    errors.failed("save", "OSError", "x={}", 1)
    clock["now"] = 122.0
    errors.failed("save", "OSError", "x={}", 1)
    assert logged.lines[-1] == ("error", "x=1 (repeated; 1 more since the last report)")


def test_a_write_replaces_the_cached_value(manager):
    store = _switch(manager)
    assert store.disabled() is False  # cached
    store.set_disabled(True)
    assert store.disabled() is True


def test_a_read_that_started_before_a_write_does_not_undo_it(manager, monkeypatch):
    store = _switch(manager)
    manager.root.mkdir(parents=True)
    (manager.root / permission_switch.FILENAME).write_text(json.dumps({"checks_disabled": False}), encoding="utf-8")
    real_read = permission_switch.read_json_tolerating_replace

    def read_then_write(path):
        value = real_read(path)
        store.set_disabled(True)  # lands while the read is in flight
        return value

    monkeypatch.setattr(permission_switch, "read_json_tolerating_replace", read_then_write)
    store.disabled()
    assert store.disabled() is True


def test_uncheckable_marker_is_logged(manager, logged, monkeypatch):
    monkeypatch.setattr(plugin_permissions, "_errors", plugin_permissions._ErrorLog())
    _legacy(manager)
    real_stat = os.stat

    def stat(path, *args, **kwargs):
        if str(path).endswith(MARKER_FILENAME):
            raise PermissionError("denied")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(plugin_permissions.os, "stat", stat)
    for _ in range(3):
        with pytest.raises(ServerDomainError):
            PluginPermissionStore(manager).read("a")
    assert logged.lines == [
        ("error", "legacy plugin consent import marker could not be checked: error=PermissionError")
    ]


def test_a_concurrent_reader_does_not_read_the_file_again(manager, monkeypatch):
    store = _switch(manager)
    manager.root.mkdir(parents=True)
    (manager.root / permission_switch.FILENAME).write_text(json.dumps({"checks_disabled": True}), encoding="utf-8")
    real_read = permission_switch.read_json_tolerating_replace
    reads, seen_meanwhile = [], []

    def slow_read(path):
        reads.append(path)
        seen_meanwhile.append(store.disabled())  # another caller while reading
        return real_read(path)

    monkeypatch.setattr(permission_switch, "read_json_tolerating_replace", slow_read)
    assert store.disabled() is True
    assert len(reads) == 1 and seen_meanwhile == [False]


def test_checks_never_read_the_file_on_their_own_thread(manager):
    manager.root.mkdir(parents=True)
    (manager.root / permission_switch.FILENAME).write_text(json.dumps({"checks_disabled": True}), encoding="utf-8")
    started = []
    store = permission_switch.PermissionSwitchStore(manager, spawn=started.append, on_change=lambda: None)

    assert store.disabled() is False  # unknown yet: checks stay on
    assert store.disabled() is False and len(started) == 1  # one load in flight
    started[0]()  # the background load
    assert store.disabled() is True


def test_startup_load_reads_now(manager):
    manager.root.mkdir(parents=True)
    (manager.root / permission_switch.FILENAME).write_text(json.dumps({"checks_disabled": True}), encoding="utf-8")
    store = permission_switch.PermissionSwitchStore(manager, spawn=lambda _load: None, on_change=lambda: None)
    assert store.load() is True and store.disabled() is True


def test_a_load_that_changes_the_effective_value_pushes_states(manager):
    pushes = []
    clock = {"now": 0.0}
    store = _switch(manager, pushes=pushes, clock=lambda: clock["now"])
    manager.root.mkdir(parents=True)
    path = manager.root / permission_switch.FILENAME
    path.write_text("{broken", encoding="utf-8")
    assert store.load() is False and pushes == []  # still off: nothing to push

    path.write_text(json.dumps({"checks_disabled": True}), encoding="utf-8")
    clock["now"] = permission_switch.RETRY_SECONDS + 1
    assert store.disabled() is True
    assert pushes == [True]  # recovered with a different value


def test_a_write_is_pushed_by_its_caller_not_twice(manager):
    pushes = []
    store = _switch(manager, pushes=pushes)
    store.set_disabled(True)
    assert store.disabled() is True and pushes == []


def test_a_pending_import_saved_by_a_bulk_drop_is_marked(manager):
    _legacy(manager, {
        "a": {"decision": "approved", "approved_capabilities": ["memory.read"]},
        "b": {"decision": "denied", "approved_capabilities": []},
    })
    manager.fail["plugin_permissions.json"] = OSError("disk full")
    store = PluginPermissionStore(manager)
    store.read("a")  # import held in memory
    del manager.fail["plugin_permissions.json"]

    assert store.forget_many(["b"]) == ["b"]

    assert _marker(manager).is_file()


def test_each_logged_operation_reports_its_recovery(manager, logged, monkeypatch):
    monkeypatch.setattr(plugin_permissions, "_errors", plugin_permissions._ErrorLog())
    _legacy(manager)
    real_read = plugin_permissions.read_json_tolerating_replace
    failing = {"legacy": True}

    def read(path):
        if str(path).endswith("plugin_consents.json") and failing["legacy"]:
            raise PermissionError("busy")
        return real_read(path)

    monkeypatch.setattr(plugin_permissions, "read_json_tolerating_replace", read)
    with pytest.raises(ServerDomainError):
        PluginPermissionStore(manager).read("a")
    failing["legacy"] = False
    PluginPermissionStore(manager).read("a")
    assert ("info", "legacy plugin consents can be read again (after 1 failure(s))") in logged.lines


def test_listener_recovery_is_tracked_per_listener(manager, logged, monkeypatch):
    monkeypatch.setattr(plugin_permissions, "_errors", plugin_permissions._ErrorLog())
    state = {"fail": True}

    def flaky(_ids):
        if state["fail"]:
            raise RuntimeError("boom")

    def healthy(_ids):
        return None

    monkeypatch.setattr(plugin_permissions, "_change_listeners", [flaky, healthy])
    store = PluginPermissionStore(manager)
    store.decide("a", MEMORY, "granted")
    assert not any(level == "info" for level, _ in logged.lines)  # healthy recovers nothing
    state["fail"] = False
    store.decide("a", MEMORY, "denied")
    infos = [line for level, line in logged.lines if level == "info"]
    assert len(infos) == 1 and infos[0].startswith("plugin permission change listener works again")


def test_marker_check_and_binding_report_recovery(manager, logged, monkeypatch):
    monkeypatch.setattr(plugin_permissions, "_errors", plugin_permissions._ErrorLog())
    plugin_permissions._errors.failed("marker_check", "PermissionError", "m={}", 1)
    plugin_permissions._errors.failed("bind", "X", "b={}", 1)
    store = PluginPermissionStore(manager)
    store.decide("a", MEMORY, "granted")  # loads: the marker check works
    assert ("info", "legacy plugin consent import marker can be checked again (after 1 failure(s))") in logged.lines
    store.sync_source("a", plugin_permissions.PluginSource("market", "m"))  # first binding saved
    assert ("info", "plugin source bindings are saved again (after 1 failure(s))") in logged.lines


def test_marker_check_recovers_when_the_marker_exists(manager, logged, monkeypatch):
    monkeypatch.setattr(plugin_permissions, "_errors", plugin_permissions._ErrorLog())
    _legacy(manager)
    _marker(manager).write_text("{}", encoding="utf-8")
    plugin_permissions._errors.failed("marker_check", "PermissionError", "m={}", 1)
    PluginPermissionStore(manager).read("a")
    assert ("info", "legacy plugin consent import marker can be checked again (after 1 failure(s))") in logged.lines


def test_a_load_after_a_write_does_not_push_again(manager):
    pushes = []
    store = _switch(manager, pushes=pushes)
    store.set_disabled(True)
    assert store.load() is True
    assert pushes == []


def test_an_unreadable_setting_on_a_later_start_turns_checks_back_on(manager):
    pushes = []
    store = _switch(manager, pushes=pushes)
    manager.root.mkdir(parents=True)
    path = manager.root / permission_switch.FILENAME
    path.write_text(json.dumps({"checks_disabled": True}), encoding="utf-8")
    assert store.load() is True
    path.write_text("{broken", encoding="utf-8")  # restart in the same process

    assert store.load() is False
    assert store.disabled() is False
    assert pushes == [True, True]  # on, then back off


def test_a_legacy_file_that_appeared_after_the_store_is_not_marked(manager):
    PluginPermissionStore(manager).read("x")  # created without a legacy file
    _legacy(manager)
    PluginPermissionStore(manager).read("x")
    assert not _marker(manager).exists()


@pytest.mark.asyncio
async def test_a_refused_switch_change_is_not_logged_as_accepted(switch_env, monkeypatch):
    accepted = []
    monkeypatch.setattr(mutation_auth, "_authorize_mutation", lambda *args, **kwargs: accepted.append(True))
    response = await _request("PUT", {"disabled": True}, {})
    assert response.status_code == 403 and accepted == []


@pytest.mark.asyncio
async def test_a_switch_change_is_logged(switch_env, monkeypatch):
    from plugin.server.routes import plugin_permission_switch

    log = Logged()
    monkeypatch.setattr(plugin_permission_switch, "logger", log)
    await _request("PUT", {"disabled": True}, TOKEN)
    assert log.lines == [("warning", "permission master switch host setting set: checks_disabled=True")]


@pytest.mark.parametrize("value", ["imported", 3, ["absent"]])
def test_a_malformed_legacy_import_entry_does_not_break_the_store(manager, logged, monkeypatch, value):
    monkeypatch.setattr(plugin_permissions, "_errors", plugin_permissions._ErrorLog())
    _legacy(manager)
    manager.root.joinpath("plugin_permissions.json").write_text(
        json.dumps({"schema_version": 1, "legacy_import": value, "plugins": {}}), encoding="utf-8"
    )
    store = PluginPermissionStore(manager)

    assert store.read("a") == {}
    store.decide("a", MEMORY, "granted")
    assert store.legacy_import()["imported_at"] is None
    assert _marker(manager).is_file()  # treated as unknown: the legacy file is marked
    errors = [line for level, line in logged.lines if level == "error"]
    assert errors == [f"plugin permission store has a malformed legacy_import entry (ignored): type={type(value).__name__}"]


def test_listeners_with_the_same_name_are_tracked_apart(manager, logged, monkeypatch):
    monkeypatch.setattr(plugin_permissions, "_errors", plugin_permissions._ErrorLog())

    def make(fail):
        def listener(_ids):
            if fail["now"]:
                raise RuntimeError("boom")
        return listener

    failing, working = {"now": True}, {"now": False}
    monkeypatch.setattr(plugin_permissions, "_change_listeners", [make(failing), make(working)])
    store = PluginPermissionStore(manager)
    store.decide("a", MEMORY, "granted")
    store.decide("a", MEMORY, "denied")

    assert not any(level == "info" for level, _ in logged.lines)  # the working one recovers nothing
    assert sum(1 for level, _ in logged.lines if level == "error") == 1  # still rate-limited
