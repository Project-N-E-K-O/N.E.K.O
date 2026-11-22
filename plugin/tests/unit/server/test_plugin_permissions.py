from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from plugin.server.application import install_source as install_source_module
from plugin.server.application.install_source.models import (
    LockEntry,
    SourceDetailImported,
    SourceDetailMarket,
)
from plugin.server.application.plugins import permission_service
from plugin.server.domain.errors import ServerDomainError
from plugin.sdk.shared.permissions import LEGACY_PERMISSION_NAMES
from plugin.server.infrastructure.plugin_permissions import (
    LEGACY_DENIED,
    PluginPermissionStore,
    PluginSource,
)

pytestmark = pytest.mark.plugin_unit

MEMORY_READ = LEGACY_PERMISSION_NAMES["memory.read"]
CONVERSATIONS_READ = LEGACY_PERMISSION_NAMES["conversations.read"]
MESSAGES_PUSH = LEGACY_PERMISSION_NAMES["messages.push"]

MARKET = PluginSource("market", "m-1")
MANUAL = PluginSource("manual")


class ConfigManager:
    def __init__(self, root: Path):
        self.root = root
        self.fail: Exception | None = None
        self.saves = 0

    def get_runtime_config_path(self, name):
        return self.root / name

    def save_json_config(self, name, data):
        if self.fail is not None:
            raise self.fail
        self.saves += 1
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def manager(tmp_path):
    return ConfigManager(tmp_path / "config")


def _write_legacy(manager: ConfigManager, document) -> Path:
    manager.root.mkdir(parents=True, exist_ok=True)
    path = manager.root / "plugin_consents.json"
    payload = document if isinstance(document, str) else json.dumps(document)
    path.write_text(payload, encoding="utf-8")
    return path


def _legacy(plugins: dict) -> dict:
    return {"schema_version": 2, "plugins": plugins}


def _new_file(manager: ConfigManager) -> Path:
    return manager.root / "plugin_permissions.json"


# ---------------------------------------------------------------------------
# Legacy import
# ---------------------------------------------------------------------------


def test_legacy_approval_imports_as_anytime_legacy_any_with_new_ids(manager):
    legacy_path = _write_legacy(
        manager,
        _legacy(
            {
                "reader": {
                    "decision": "approved",
                    "approved_capabilities": [
                        "memory.read",
                        "conversations.read",
                        "messages.push",
                    ],
                    "version": "1.0",
                    "decided_at": "2026-01-02T03:04:05+00:00",
                }
            }
        ),
    )
    store = PluginPermissionStore(manager, source_resolver=lambda _pid: MARKET)

    record = store.read("reader")

    assert set(record["permissions"]) == {MEMORY_READ, CONVERSATIONS_READ, MESSAGES_PUSH}
    for grant in record["permissions"].values():
        assert grant == {
            "state": "granted",
            "condition": "anytime",
            "legacy_any": True,
            "scope": "all",
            "decided_at": "2026-01-02T03:04:05+00:00",
        }
    assert record["source"] == {"channel": "market", "fingerprint": "m-1"}
    summary = store.legacy_import()
    assert summary["status"] == "imported"
    assert summary["plugins"] == 1
    assert summary["imported_at"]
    on_disk = json.loads(_new_file(manager).read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == 1
    assert on_disk["imported_at"] == summary["imported_at"]
    assert legacy_path.exists()


def test_legacy_file_is_left_byte_for_byte_unchanged(manager):
    original = json.dumps(
        _legacy(
            {
                "a": {"decision": "approved", "approved_capabilities": ["memory.read"]},
                "": {"decision": "approved", "approved_capabilities": ["memory.read"]},
            }
        ),
        indent=4,
    )
    legacy_path = _write_legacy(manager, original)
    before = legacy_path.read_bytes()
    stat_before = legacy_path.stat().st_mtime_ns
    store = PluginPermissionStore(manager)

    store.read("a")
    store.decide("a", MEMORY_READ, "denied")
    store.forget("a")

    assert legacy_path.read_bytes() == before
    assert legacy_path.stat().st_mtime_ns == stat_before


def test_empty_plugin_id_is_not_imported(manager):
    _write_legacy(
        manager,
        _legacy(
            {
                "": {"decision": "approved", "approved_capabilities": ["memory.read"]},
                "ok": {"decision": "approved", "approved_capabilities": ["memory.read"]},
            }
        ),
    )
    store = PluginPermissionStore(manager)

    assert store.read("") == {}
    on_disk = json.loads(_new_file(manager).read_text(encoding="utf-8"))
    assert set(on_disk["plugins"]) == {"ok"}
    assert store.legacy_import()["skipped"] == 1


def test_legacy_denial_marks_the_whole_plugin_denied(manager):
    _write_legacy(
        manager,
        _legacy({"blocked": {"decision": "denied", "approved_capabilities": []}}),
    )
    store = PluginPermissionStore(manager)

    record = store.read("blocked")

    assert record[LEGACY_DENIED] is True
    assert record["permissions"] == {}


def test_unknown_names_and_malformed_records_are_skipped_not_fatal(manager):
    _write_legacy(
        manager,
        _legacy(
            {
                "mixed": {
                    "decision": "approved",
                    "approved_capabilities": ["memory.read", "unknown.thing", 3],
                },
                "not_a_dict": ["approved"],
                "no_decision": {"approved_capabilities": ["memory.read"]},
                "bad_list": {"decision": "approved", "approved_capabilities": "memory.read"},
            }
        ),
    )
    store = PluginPermissionStore(manager)

    assert set(store.read("mixed")["permissions"]) == {MEMORY_READ}
    for plugin_id in ("not_a_dict", "no_decision", "bad_list"):
        assert store.read(plugin_id) == {}
    summary = store.legacy_import()
    assert (summary["status"], summary["plugins"], summary["skipped"]) == ("imported", 1, 3)


def test_import_happens_once_even_if_legacy_file_changes_later(manager):
    legacy_path = _write_legacy(manager, _legacy({}))
    PluginPermissionStore(manager).read("x")
    legacy_path.write_text(
        json.dumps(_legacy({"x": {"decision": "approved", "approved_capabilities": ["memory.read"]}})),
        encoding="utf-8",
    )

    assert PluginPermissionStore(manager).read("x") == {}


def test_missing_legacy_file_records_absent(manager):
    store = PluginPermissionStore(manager)

    assert store.read("x") == {}
    assert store.legacy_import()["status"] == "absent"
    assert _new_file(manager).exists()


@pytest.mark.parametrize(
    "content",
    ["{not json", json.dumps({"schema_version": 1, "plugins": {}}), json.dumps([1])],
)
def test_unreadable_legacy_content_is_reported_and_left_alone(manager, content):
    legacy_path = _write_legacy(manager, content)
    store = PluginPermissionStore(manager)

    assert store.read("x") == {}
    assert store.legacy_import()["status"] == "invalid"
    assert legacy_path.read_text(encoding="utf-8") == content


def test_legacy_io_error_fails_loudly_and_retries_next_time(manager):
    manager.root.mkdir(parents=True)
    # A directory where the file should be: reading it raises an OSError.
    (manager.root / "plugin_consents.json").mkdir()
    store = PluginPermissionStore(manager)

    with pytest.raises(ServerDomainError) as captured:
        store.read("x")
    assert captured.value.code == "PLUGIN_PERMISSIONS_READ_FAILED"
    assert captured.value.status_code == 503
    assert not _new_file(manager).exists()

    (manager.root / "plugin_consents.json").rmdir()
    _write_legacy(
        manager,
        _legacy({"x": {"decision": "approved", "approved_capabilities": ["memory.read"]}}),
    )
    assert set(store.read("x")["permissions"]) == {MEMORY_READ}


def test_import_is_served_from_memory_when_first_save_fails(manager):
    _write_legacy(
        manager,
        _legacy({"x": {"decision": "approved", "approved_capabilities": ["memory.read"]}}),
    )
    manager.fail = OSError("disk full")
    store = PluginPermissionStore(manager)

    assert set(store.read("x")["permissions"]) == {MEMORY_READ}
    assert not _new_file(manager).exists()

    manager.fail = None
    store.decide("y", MEMORY_READ, "granted")
    on_disk = json.loads(_new_file(manager).read_text(encoding="utf-8"))
    assert set(on_disk["plugins"]) == {"x", "y"}
    assert on_disk["legacy_import"]["status"] == "imported"


def test_source_resolver_failure_imports_without_source(manager):
    _write_legacy(
        manager,
        _legacy({"x": {"decision": "approved", "approved_capabilities": ["memory.read"]}}),
    )

    def boom(_plugin_id):
        raise RuntimeError("lock unavailable")

    record = PluginPermissionStore(manager, source_resolver=boom).read("x")

    assert record["source"] is None
    assert set(record["permissions"]) == {MEMORY_READ}


# ---------------------------------------------------------------------------
# New store read / write failures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("{broken", "PLUGIN_PERMISSIONS_CORRUPT"),
        (json.dumps([1]), "PLUGIN_PERMISSIONS_CORRUPT"),
        (json.dumps({"plugins": {}}), "PLUGIN_PERMISSIONS_CORRUPT"),
        (json.dumps({"schema_version": 0, "plugins": {}}), "PLUGIN_PERMISSIONS_CORRUPT"),
        (json.dumps({"schema_version": True, "plugins": {}}), "PLUGIN_PERMISSIONS_CORRUPT"),
        (json.dumps({"schema_version": 1, "plugins": {"x": []}}), "PLUGIN_PERMISSIONS_CORRUPT"),
        (
            json.dumps({"schema_version": 1, "plugins": {"x": {"permissions": []}}}),
            "PLUGIN_PERMISSIONS_CORRUPT",
        ),
        (
            json.dumps({"schema_version": 1, "plugins": {"x": {"legacy_denied": True, "permissions": {"p": "granted"}}}}),
            "PLUGIN_PERMISSIONS_CORRUPT",
        ),
        (
            json.dumps({"schema_version": 1, "plugins": {"x": {"permissions": {"p": {"state": "maybe"}}}}}),
            "PLUGIN_PERMISSIONS_CORRUPT",
        ),
        (
            json.dumps({"schema_version": 1, "plugins": {"x": {"permissions": {"p": {"state": []}}}}}),
            "PLUGIN_PERMISSIONS_CORRUPT",
        ),
        (
            json.dumps({"schema_version": 1, "plugins": {"x": {"permissions": {"p": {"state": {}}}}}}),
            "PLUGIN_PERMISSIONS_CORRUPT",
        ),
        (json.dumps({"schema_version": 2, "plugins": {}}), "PLUGIN_PERMISSIONS_UNSUPPORTED_VERSION"),
    ],
)
def test_unreadable_store_raises_and_is_never_overwritten(manager, content, code):
    manager.root.mkdir(parents=True)
    _new_file(manager).write_text(content, encoding="utf-8")
    store = PluginPermissionStore(manager)

    for call in (lambda: store.read("x"), lambda: store.decide("x", MEMORY_READ, "granted")):
        with pytest.raises(ServerDomainError) as captured:
            call()
        assert captured.value.code == code
        assert captured.value.status_code == 500
        assert captured.value.details["retryable"] is False
        assert captured.value.details["file"] == "plugin_permissions.json"
    assert _new_file(manager).read_text(encoding="utf-8") == content
    assert manager.saves == 0


def test_newer_store_version_reports_found_and_supported_versions(manager):
    manager.root.mkdir(parents=True)
    _new_file(manager).write_text(
        json.dumps({"schema_version": 7, "plugins": {}}), encoding="utf-8"
    )

    with pytest.raises(ServerDomainError) as captured:
        PluginPermissionStore(manager).read("x")

    assert captured.value.details["schema_version"] == 7
    assert captured.value.details["supported_schema_version"] == 1


def test_store_io_error_is_a_retryable_read_failure(manager):
    manager.root.mkdir(parents=True)
    _new_file(manager).mkdir()  # reading a directory raises an OSError

    with pytest.raises(ServerDomainError) as captured:
        PluginPermissionStore(manager).read("x")

    assert captured.value.code == "PLUGIN_PERMISSIONS_READ_FAILED"
    assert captured.value.status_code == 503
    assert captured.value.details == {"file": "plugin_permissions.json", "retryable": True}


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


async def _get(path: str):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from plugin.server.routes import plugin_permissions as routes

    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        return await client.get(path)


@pytest.fixture
def api_store(monkeypatch, manager):
    store = PluginPermissionStore(manager, source_resolver=lambda _pid: None)
    monkeypatch.setattr(permission_service, "permission_store", store)
    monkeypatch.setattr(permission_service, "current_source", lambda _pid: None)
    return store


@pytest.mark.asyncio
async def test_read_api_returns_the_plugin_record(api_store):
    api_store.decide("demo", MEMORY_READ, "granted")

    response = await _get("/plugin-permissions/plugins/demo")

    assert response.status_code == 200
    body = response.json()
    assert body["plugin_id"] == "demo"
    assert body["permissions"][MEMORY_READ]["state"] == "granted"
    assert body["source_confirmed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "status", "code"),
    [
        ("{broken", 500, "PLUGIN_PERMISSIONS_CORRUPT"),
        (json.dumps({"schema_version": 9, "plugins": {}}), 500, "PLUGIN_PERMISSIONS_UNSUPPORTED_VERSION"),
    ],
)
async def test_read_api_reports_unreadable_store_with_a_stable_code(
    api_store, manager, content, status, code
):
    manager.root.mkdir(parents=True)
    _new_file(manager).write_text(content, encoding="utf-8")

    response = await _get("/plugin-permissions/plugins/demo")

    assert response.status_code == status
    assert response.headers["X-Error-Code"] == code
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert detail["message"]
    assert detail["details"]["retryable"] is False
    assert _new_file(manager).read_text(encoding="utf-8") == content


@pytest.mark.asyncio
async def test_read_api_reports_io_failure_as_retryable(api_store, manager):
    manager.root.mkdir(parents=True)
    _new_file(manager).mkdir()

    response = await _get("/plugin-permissions/plugins/demo")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "PLUGIN_PERMISSIONS_READ_FAILED"
    assert response.json()["detail"]["details"]["retryable"] is True


def test_write_failure_raises_and_keeps_previous_state(manager):
    store = PluginPermissionStore(manager)
    store.decide("x", MEMORY_READ, "granted")
    manager.fail = PermissionError("locked")

    with pytest.raises(ServerDomainError) as captured:
        store.decide("x", MEMORY_READ, "denied")

    assert captured.value.code == "PLUGIN_PERMISSIONS_WRITE_FAILED"
    assert store.read("x")["permissions"][MEMORY_READ]["state"] == "granted"


def test_non_os_write_errors_are_reported_too(manager):
    store = PluginPermissionStore(manager)
    store.read("x")
    manager.fail = RuntimeError("maintenance mode")

    with pytest.raises(ServerDomainError) as captured:
        store.decide("x", MEMORY_READ, "granted")
    assert captured.value.code == "PLUGIN_PERMISSIONS_WRITE_FAILED"
    assert store.read("x") == {}


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def test_user_decision_replaces_legacy_any_grant(manager):
    _write_legacy(
        manager,
        _legacy({"x": {"decision": "approved", "approved_capabilities": ["memory.read"]}}),
    )
    store = PluginPermissionStore(manager)

    record = store.decide("x", "memory.read", "granted", condition="anytime")

    grant = record["permissions"][MEMORY_READ]
    assert grant["condition"] == "anytime"
    assert "legacy_any" not in grant
    assert "memory.read" not in record["permissions"]


def test_decide_validates_input(manager):
    store = PluginPermissionStore(manager)
    with pytest.raises(ValueError):
        store.decide("x", MEMORY_READ, "once")
    with pytest.raises(ValueError):
        store.decide("x", MEMORY_READ, "granted", condition="sometimes")
    with pytest.raises(ValueError):
        store.decide("", MEMORY_READ, "granted")


def test_clear_returns_a_permission_to_undecided(manager):
    store = PluginPermissionStore(manager)
    store.decide("x", MEMORY_READ, "granted")

    assert store.clear("x", MEMORY_READ)["permissions"] == {}


def test_decide_and_clear_reject_ids_outside_the_vocabulary(manager):
    store = PluginPermissionStore(manager)

    for permission_id in ("system.unknown:read", "", None):
        with pytest.raises(ValueError):
            store.decide("x", permission_id, "granted")
        with pytest.raises(ValueError):
            store.clear("x", permission_id)
    assert store.read("x") == {}

    # Ids from the vocabulary pass through unchanged.
    record = store.decide("x", "system.fs:read", "granted")
    assert list(record["permissions"]) == ["system.fs:read"]


# ---------------------------------------------------------------------------
# Source binding
# ---------------------------------------------------------------------------


def test_unknown_source_keeps_last_known_source_and_grants(manager):
    store = PluginPermissionStore(manager)
    store.decide("x", MEMORY_READ, "granted", source=MARKET)

    record = store.sync_source("x", None)

    assert record["source"] == MARKET.to_dict()
    assert MEMORY_READ in record["permissions"]


def test_first_known_source_binds_without_invalidating(manager):
    store = PluginPermissionStore(manager)
    store.decide("x", MEMORY_READ, "granted", source=None)

    record = store.sync_source("x", MARKET)

    assert record["source"] == MARKET.to_dict()
    assert MEMORY_READ in record["permissions"]
    assert "source_changed_at" not in record


def test_changed_source_discards_grants_and_never_restores_them(manager):
    _write_legacy(
        manager,
        _legacy({"x": {"decision": "denied", "approved_capabilities": []}}),
    )
    store = PluginPermissionStore(manager, source_resolver=lambda _pid: MARKET)
    store.decide("x", MEMORY_READ, "granted", source=MARKET)

    switched = store.sync_source("x", MANUAL)
    assert switched["permissions"] == {}
    assert LEGACY_DENIED not in switched
    assert switched["source"] == MANUAL.to_dict()
    assert switched["source_changed_at"]

    back = store.sync_source("x", MARKET)
    assert back["permissions"] == {}
    assert back["source"] == MARKET.to_dict()


def test_fingerprint_change_alone_is_a_source_change(manager):
    store = PluginPermissionStore(manager)
    store.decide("x", MEMORY_READ, "granted", source=MARKET)

    record = store.sync_source("x", PluginSource("market", "m-2"))

    assert record["permissions"] == {}


def test_decide_under_a_new_source_starts_from_scratch(manager):
    store = PluginPermissionStore(manager)
    store.decide("x", MEMORY_READ, "granted", source=MARKET)

    record = store.decide("x", CONVERSATIONS_READ, "denied", source=MANUAL)

    assert set(record["permissions"]) == {CONVERSATIONS_READ}


def test_sync_source_does_not_create_records(manager):
    store = PluginPermissionStore(manager)
    assert store.sync_source("x", MARKET) == {}
    assert store.read("x") == {}


# ---------------------------------------------------------------------------
# Forget / restore
# ---------------------------------------------------------------------------


def test_forget_removes_all_grants_and_restore_puts_them_back(manager):
    store = PluginPermissionStore(manager)
    store.decide("x", MEMORY_READ, "granted", source=MARKET)
    store.decide("y", MEMORY_READ, "granted")

    removed = store.forget("x")

    assert store.read("x") == {}
    assert MEMORY_READ in store.read("y")["permissions"]
    assert store.forget("x") is None
    store.restore("x", removed)
    assert store.read("x") == removed


# ---------------------------------------------------------------------------
# Source resolution from the install-source lock
# ---------------------------------------------------------------------------


def _lock_entry(**overrides) -> LockEntry:
    values = dict(
        root_id="user",
        directory_name="demo",
        plugin_id="demo",
        channel="manual",
        reason="user_requested",
        installed_at="2026-01-01T00:00:00.000000Z",
        updated_at="2026-01-01T00:00:00.000000Z",
        last_seen_at="2026-01-01T00:00:00.000000Z",
    )
    values.update(overrides)
    return LockEntry(**values)


class FakeInstallSources:
    def __init__(self, entries, *, by_directory=None, degraded=False):
        self.entries = list(entries)
        self.by_directory = by_directory or {}
        self.is_degraded = degraded

    def entry_for_directory(self, directory_path, *, include_removed=False):
        entry = self.by_directory.get(Path(directory_path).name)
        if entry is None or (entry.removed and not include_removed):
            return None
        return entry

    def list_entries(self, *, include_removed=False):
        return [e for e in self.entries if include_removed or not e.removed]


@pytest.fixture
def install_sources(monkeypatch):
    holder = {"manager": None, "directory": None}
    monkeypatch.setattr(
        install_source_module, "get_install_source_manager", lambda: holder["manager"]
    )
    monkeypatch.setattr(
        permission_service, "_plugin_directory", lambda _pid: holder["directory"]
    )
    return holder


def test_current_source_unknown_without_manager_or_when_degraded(install_sources):
    assert permission_service.current_source("demo") is None
    install_sources["manager"] = FakeInstallSources([_lock_entry()], degraded=True)
    assert permission_service.current_source("demo") is None


def test_current_source_market_and_imported_fingerprints(install_sources):
    market = _lock_entry(
        channel="market",
        source_detail=SourceDetailMarket(
            plugin_market_id="mk-7",
            version="1",
            package_url="",
            package_sha256="",
            payload_hash=None,
            channel="stable",
            published_at="",
        ),
    )
    install_sources["manager"] = FakeInstallSources([market])
    assert permission_service.current_source("demo") == PluginSource("market", "mk-7")

    imported = _lock_entry(
        channel="imported",
        source_detail=SourceDetailImported(package_filename="d.neko", package_sha256="ab" * 32),
    )
    install_sources["manager"] = FakeInstallSources([imported])
    assert permission_service.current_source("demo") == PluginSource("imported", "ab" * 32)

    install_sources["manager"] = FakeInstallSources([_lock_entry(channel="builtin", root_id="builtin")])
    assert permission_service.current_source("demo") == PluginSource("builtin", None)


def test_current_source_prefers_the_loaded_directory(install_sources):
    by_dir = _lock_entry(directory_name="dir", plugin_id="", channel="manual")
    other = _lock_entry(directory_name="other", channel="imported")
    install_sources["manager"] = FakeInstallSources([by_dir, other], by_directory={"dir": by_dir})
    install_sources["directory"] = Path("/plugins/dir")

    assert permission_service.current_source("demo") == PluginSource("manual", None)


def test_current_source_rejects_directory_entry_of_another_plugin(install_sources):
    foreign = _lock_entry(directory_name="dir", plugin_id="someone_else")
    install_sources["manager"] = FakeInstallSources([foreign], by_directory={"dir": foreign})
    install_sources["directory"] = Path("/plugins/dir")

    assert permission_service.current_source("demo") is None


def test_current_source_scan_skips_removed_and_requires_a_unique_match(install_sources):
    live = _lock_entry(directory_name="a", channel="manual")
    removed = replace(_lock_entry(directory_name="b", channel="imported"), removed=True)
    install_sources["manager"] = FakeInstallSources([live, removed])
    assert permission_service.current_source("demo") == PluginSource("manual", None)

    twin = _lock_entry(directory_name="c", channel="imported")
    install_sources["manager"] = FakeInstallSources([live, twin])
    assert permission_service.current_source("demo") is None

    install_sources["manager"] = FakeInstallSources([_lock_entry(plugin_id="")])
    assert permission_service.current_source("demo") is None


def test_permission_record_falls_back_to_last_known_source(
    install_sources, monkeypatch, manager
):
    store = PluginPermissionStore(manager, source_resolver=permission_service.current_source)
    monkeypatch.setattr(permission_service, "permission_store", store)
    install_sources["manager"] = FakeInstallSources([_lock_entry(channel="manual")])
    permission_service.decide_permission("demo", MEMORY_READ, "granted")

    install_sources["manager"] = None  # e.g. lock not initialised yet
    view = permission_service.permission_record("demo")
    assert view["source_confirmed"] is False
    assert view["source"] == MANUAL.to_dict()
    assert MEMORY_READ in view["permissions"]

    install_sources["manager"] = FakeInstallSources([_lock_entry(channel="manual")])
    view = permission_service.permission_record("demo")
    assert view["source_confirmed"] is True
    assert MEMORY_READ in view["permissions"]

    install_sources["manager"] = FakeInstallSources(
        [
            _lock_entry(
                channel="imported",
                source_detail=SourceDetailImported(package_filename="p", package_sha256="cd" * 32),
            )
        ]
    )
    view = permission_service.permission_record("demo")
    assert view["source_confirmed"] is True
    assert view["permissions"] == {}


def test_legacy_import_takes_source_from_install_record(install_sources, manager):
    install_sources["manager"] = FakeInstallSources([_lock_entry(channel="manual")])
    _write_legacy(
        manager,
        _legacy({"demo": {"decision": "approved", "approved_capabilities": ["messages.push"]}}),
    )
    store = PluginPermissionStore(manager, source_resolver=permission_service.current_source)

    assert store.read("demo")["source"] == MANUAL.to_dict()



def test_current_source_without_fingerprint_is_unknown(install_sources):
    install_sources["manager"] = FakeInstallSources([_lock_entry(channel="market")])
    assert permission_service.current_source("demo") is None

    empty_hash = _lock_entry(
        channel="imported",
        source_detail=SourceDetailImported(package_filename="p", package_sha256=""),
    )
    install_sources["manager"] = FakeInstallSources([empty_hash])
    assert permission_service.current_source("demo") is None


def test_known_directory_without_lock_entry_is_unknown(install_sources):
    # Another root holds an active entry with the same plugin id; it must not
    # be picked when the loaded plugin's own directory has no entry.
    other_root = _lock_entry(root_id="builtin", channel="builtin", directory_name="demo")
    install_sources["manager"] = FakeInstallSources([other_root])
    install_sources["directory"] = Path("/user/plugins/demo")

    assert permission_service.current_source("demo") is None


def test_registry_lock_timeout_is_an_unknown_source(monkeypatch):
    from plugin.server.infrastructure import config_paths

    def busy(_plugin_id):
        raise TimeoutError("plugins_read")

    monkeypatch.setattr(config_paths, "_resolve_registered_plugin_config_path", busy)
    # A unique entry exists: without the timeout handling the scan would use it.
    manager = FakeInstallSources([_lock_entry(channel="manual")])
    monkeypatch.setattr(install_source_module, "get_install_source_manager", lambda: manager)

    assert permission_service.current_source("demo") is None


def test_first_binding_write_failure_still_returns_the_view(manager):
    store = PluginPermissionStore(manager)
    store.decide("x", MEMORY_READ, "granted", source=None)
    manager.fail = OSError("disk full")

    record = store.sync_source("x", MARKET)

    assert record["source"] == MARKET.to_dict()
    assert MEMORY_READ in record["permissions"]
    manager.fail = None
    assert store.read("x")["source"] is None  # not cached; retried next time
    assert store.sync_source("x", MARKET)["source"] == MARKET.to_dict()
    assert store.read("x")["source"] == MARKET.to_dict()


def test_source_change_write_failure_is_reported(manager):
    store = PluginPermissionStore(manager)
    store.decide("x", MEMORY_READ, "granted", source=MARKET)
    manager.fail = OSError("disk full")

    with pytest.raises(ServerDomainError) as captured:
        store.sync_source("x", MANUAL)
    assert captured.value.code == "PLUGIN_PERMISSIONS_WRITE_FAILED"


def test_unchanged_source_does_not_write(manager):
    store = PluginPermissionStore(manager)
    store.decide("x", MEMORY_READ, "granted", source=MARKET)
    saves = manager.saves

    store.sync_source("x", MARKET)

    assert manager.saves == saves


def test_clear_on_legacy_denied_record_stores_an_undecided_exception(manager):
    _write_legacy(
        manager,
        _legacy({"x": {"decision": "denied", "approved_capabilities": []}}),
    )
    store = PluginPermissionStore(manager)
    store.decide("x", MEMORY_READ, "granted")

    record = store.clear("x", MEMORY_READ)

    assert record[LEGACY_DENIED] is True
    assert record["permissions"][MEMORY_READ]["state"] == "undecided"
    assert CONVERSATIONS_READ not in record["permissions"]  # still implicitly denied

    # Clearing a never-decided permission of a denied plugin also lifts it.
    record = store.clear("x", CONVERSATIONS_READ)
    assert record["permissions"][CONVERSATIONS_READ]["state"] == "undecided"


def test_clear_on_ordinary_record_deletes_the_entry(manager):
    store = PluginPermissionStore(manager)
    store.decide("x", MEMORY_READ, "granted")
    saves = manager.saves

    assert store.clear("x", MEMORY_READ)["permissions"] == {}
    assert store.clear("x", MEMORY_READ)["permissions"] == {}
    assert manager.saves == saves + 1


def test_permission_record_never_saves(install_sources, monkeypatch, manager):
    store = PluginPermissionStore(manager, source_resolver=permission_service.current_source)
    monkeypatch.setattr(permission_service, "permission_store", store)
    store.decide("demo", MEMORY_READ, "granted", source=MARKET)
    store.decide("unbound", MEMORY_READ, "granted", source=None)
    saves = manager.saves

    # A different source is shown as "no grants" but not persisted.
    install_sources["manager"] = FakeInstallSources([_lock_entry(channel="manual")])
    assert permission_service.permission_record("demo")["permissions"] == {}
    assert MEMORY_READ in store.read("demo")["permissions"]
    # A first binding is shown but not persisted either.
    unbound = _lock_entry(plugin_id="unbound", directory_name="unbound", channel="manual")
    install_sources["manager"] = FakeInstallSources([unbound])
    assert permission_service.permission_record("unbound")["source"] == MANUAL.to_dict()
    assert store.read("unbound")["source"] is None
    assert manager.saves == saves


def test_stored_undecided_entries_load_and_repeat_clears_do_not_write(manager):
    manager.root.mkdir(parents=True)
    _new_file(manager).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugins": {
                    "x": {
                        "legacy_denied": True,
                        "permissions": {MEMORY_READ: {"state": "undecided", "scope": "all"}},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    store = PluginPermissionStore(manager)

    assert store.clear("x", MEMORY_READ)["permissions"][MEMORY_READ]["state"] == "undecided"
    assert manager.saves == 0


def test_view_reports_source_mismatch(install_sources, monkeypatch, manager):
    store = PluginPermissionStore(manager, source_resolver=permission_service.current_source)
    monkeypatch.setattr(permission_service, "permission_store", store)
    store.decide("demo", MEMORY_READ, "granted", source=MARKET)

    install_sources["manager"] = FakeInstallSources([_lock_entry(channel="manual")])
    assert permission_service.permission_record("demo")["source_mismatch"] is True

    install_sources["manager"] = None
    assert permission_service.permission_record("demo")["source_mismatch"] is False
    assert permission_service.permission_record("nobody")["source_mismatch"] is False


# ---------------------------------------------------------------------------
# Start-time source reconciliation
# ---------------------------------------------------------------------------


def _with_service_store(monkeypatch, manager):
    store = PluginPermissionStore(manager, source_resolver=permission_service.current_source)
    monkeypatch.setattr(permission_service, "permission_store", store)
    return store


def test_reconcile_persists_a_source_change_and_never_revives_old_grants(
    install_sources, monkeypatch, manager
):
    store = _with_service_store(monkeypatch, manager)
    store.decide("demo", MEMORY_READ, "granted", source=MANUAL)
    imported = _lock_entry(
        channel="imported",
        source_detail=SourceDetailImported(package_filename="p", package_sha256="ef" * 32),
    )

    install_sources["manager"] = FakeInstallSources([imported])  # A -> B
    permission_service.reconcile_permission_source("demo")
    on_disk = json.loads(_new_file(manager).read_text(encoding="utf-8"))["plugins"]["demo"]
    assert on_disk["permissions"] == {}
    assert on_disk["source"] == {"channel": "imported", "fingerprint": "ef" * 32}

    install_sources["manager"] = FakeInstallSources([_lock_entry(channel="manual")])  # B -> A
    permission_service.reconcile_permission_source("demo")
    assert store.read("demo")["permissions"] == {}
    assert permission_service.permission_record("demo")["permissions"] == {}


def test_reconcile_persists_the_first_known_source(install_sources, monkeypatch, manager):
    store = _with_service_store(monkeypatch, manager)
    store.decide("demo", MEMORY_READ, "granted", source=None)

    install_sources["manager"] = FakeInstallSources([_lock_entry(channel="manual")])
    permission_service.reconcile_permission_source("demo")

    on_disk = json.loads(_new_file(manager).read_text(encoding="utf-8"))["plugins"]["demo"]
    assert on_disk["source"] == MANUAL.to_dict()
    assert MEMORY_READ in on_disk["permissions"]


def test_reconcile_with_unknown_source_keeps_grants(install_sources, monkeypatch, manager):
    store = _with_service_store(monkeypatch, manager)
    store.decide("demo", MEMORY_READ, "granted", source=MARKET)
    saves = manager.saves

    permission_service.reconcile_permission_source("demo")  # no install-source manager

    assert manager.saves == saves
    assert MEMORY_READ in store.read("demo")["permissions"]


@pytest.mark.parametrize("broken", ["corrupt", "write"])
def test_reconcile_never_raises(install_sources, monkeypatch, manager, broken):
    store = _with_service_store(monkeypatch, manager)
    if broken == "corrupt":
        manager.root.mkdir(parents=True)
        _new_file(manager).write_text("{broken", encoding="utf-8")
    else:
        store.decide("demo", MEMORY_READ, "granted", source=MARKET)
        manager.fail = OSError("disk full")
    install_sources["manager"] = FakeInstallSources([_lock_entry(channel="manual")])

    permission_service.reconcile_permission_source("demo")  # must not raise


# ---------------------------------------------------------------------------
# Consent flow mirror (transitional)
# ---------------------------------------------------------------------------


class SelectiveConfigManager(ConfigManager):
    """Fails saves of one file only."""

    def __init__(self, root):
        super().__init__(root)
        self.fail_name: str | None = None

    def save_json_config(self, name, data):
        if name == self.fail_name:
            raise OSError("disk full")
        super().save_json_config(name, data)


@pytest.fixture
def consent_flow(monkeypatch, tmp_path):
    from plugin.server.application.plugins import consent_service
    from plugin.server.infrastructure.plugin_consent import PluginConsentStore

    shared = SelectiveConfigManager(tmp_path / "shared")
    consents = PluginConsentStore(shared)
    permissions = PluginPermissionStore(shared, source_resolver=lambda _pid: None)
    report = {"status": "completed", "capabilities": [], "evidence": [], "warnings": []}
    monkeypatch.setattr(consent_service, "consent_store", consents)
    monkeypatch.setattr(consent_service, "plugin_source", lambda _pid: (tmp_path, "1"))
    monkeypatch.setattr(consent_service, "read_report", lambda *_a, **_k: dict(report))
    monkeypatch.setattr(permission_service, "permission_store", permissions)
    monkeypatch.setattr(permission_service, "current_source", lambda _pid: None)

    async def _not_running(self, plugin_id, **_kwargs):
        raise ServerDomainError("PLUGIN_NOT_RUNNING", "not running", 409)

    from plugin.server.application.plugins import lifecycle_service

    monkeypatch.setattr(lifecycle_service.PluginLifecycleService, "stop_plugin", _not_running)
    return {
        "service": consent_service,
        "consents": consents,
        "permissions": permissions,
        "report": report,
        "manager": shared,
    }


@pytest.mark.asyncio
async def test_revoke_after_import_is_mirrored(consent_flow):
    consent_flow["consents"].decide("demo", "approved", ["memory.read"], "1")
    assert consent_flow["permissions"].read("demo")["permissions"][MEMORY_READ]["state"] == "granted"

    await consent_flow["service"].revoke_plugin("demo")

    assert consent_flow["consents"].read("demo") == {}
    record = consent_flow["permissions"].read("demo")
    assert MEMORY_READ not in record["permissions"]
    assert LEGACY_DENIED not in record


@pytest.mark.asyncio
async def test_approve_and_deny_are_mirrored_and_keep_permission_flow_decisions(consent_flow):
    service, permissions = consent_flow["service"], consent_flow["permissions"]
    permissions.read("demo")  # import happens before any consent decision
    permissions.decide("demo", CONVERSATIONS_READ, "denied")
    consent_flow["report"]["capabilities"] = ["memory.read", "conversations.read"]

    await service.approve_plugin(
        "demo",
        version="1",
        unconfirmed=["conversations.read", "memory.read"],
        analysis_status="completed",
    )
    record = permissions.read("demo")
    assert record["permissions"][MEMORY_READ]["legacy_any"] is True
    assert record["permissions"][MEMORY_READ]["condition"] == "anytime"
    # A decision made in the permission flow is not overwritten.
    assert record["permissions"][CONVERSATIONS_READ]["state"] == "denied"
    assert "legacy_any" not in record["permissions"][CONVERSATIONS_READ]

    await service.deny_plugin("demo")
    record = permissions.read("demo")
    assert record[LEGACY_DENIED] is True
    assert MEMORY_READ not in record["permissions"]
    assert record["permissions"][CONVERSATIONS_READ]["state"] == "denied"


@pytest.mark.asyncio
async def test_mirror_write_failure_fails_the_operation_and_a_retry_converges(consent_flow):
    service = consent_flow["service"]
    consent_flow["consents"].decide("demo", "approved", ["memory.read"], "1")
    consent_flow["permissions"].read("demo")  # imported
    consent_flow["manager"].fail_name = "plugin_permissions.json"

    with pytest.raises(ServerDomainError) as captured:
        await service.revoke_plugin("demo")

    assert captured.value.code == "PLUGIN_PERMISSIONS_WRITE_FAILED"
    # Neither store changed.
    assert consent_flow["consents"].read("demo")["decision"] == "approved"
    assert MEMORY_READ in consent_flow["permissions"].read("demo")["permissions"]

    consent_flow["manager"].fail_name = None
    await service.revoke_plugin("demo")
    assert consent_flow["consents"].read("demo") == {}
    assert MEMORY_READ not in consent_flow["permissions"].read("demo")["permissions"]


def test_apply_legacy_decision_is_idempotent(manager):
    store = PluginPermissionStore(manager)
    store.apply_legacy_decision("x", "approved", ["memory.read"])
    saves = manager.saves

    store.apply_legacy_decision("x", "approved", ["memory.read"])
    store.apply_legacy_decision("nobody", None)

    assert manager.saves == saves


@pytest.mark.asyncio
async def test_mirror_first_loads_and_imports_inside_the_consent_operation(consent_flow):
    consent_flow["consents"].decide("demo", "approved", ["memory.read"], "1")

    # The permission store has not been read yet: the revoke itself imports.
    await consent_flow["service"].revoke_plugin("demo")

    assert MEMORY_READ not in consent_flow["permissions"].read("demo")["permissions"]
    assert consent_flow["permissions"].legacy_import()["plugins"] == 1


@pytest.mark.asyncio
async def test_deny_mirror_failure_leaves_both_stores_unchanged(consent_flow):
    consent_flow["consents"].decide("demo", "approved", ["memory.read"], "1")
    consent_flow["permissions"].read("demo")
    consent_flow["manager"].fail_name = "plugin_permissions.json"

    with pytest.raises(ServerDomainError):
        await consent_flow["service"].deny_plugin("demo")

    assert consent_flow["consents"].read("demo")["decision"] == "approved"
    assert LEGACY_DENIED not in consent_flow["permissions"].read("demo")


def test_mirror_applies_source_rules(manager):
    store = PluginPermissionStore(manager)
    store.decide("x", CONVERSATIONS_READ, "granted", source=MARKET)

    record = store.apply_legacy_decision("x", "approved", ["memory.read"], source=MANUAL)

    assert set(record["permissions"]) == {MEMORY_READ}  # old source's grants dropped
    assert record["source"] == MANUAL.to_dict()
    assert record["source_changed_at"]
    created = store.apply_legacy_decision("y", "denied", source=MARKET)
    assert created["source"] == MARKET.to_dict()
    assert created[LEGACY_DENIED] is True



@pytest.mark.asyncio
async def test_approval_after_a_source_change_grants_only_what_was_asked(consent_flow):
    service, permissions = consent_flow["service"], consent_flow["permissions"]
    consent_flow["consents"].decide("demo", "approved", ["memory.read"], "1")
    permissions.read("demo")  # imported: memory:read granted
    permissions.sync_source("demo", MARKET)  # first binding
    permissions.sync_source("demo", MANUAL)  # source change discards grants
    consent_flow["report"]["capabilities"] = ["memory.read", "messages.push"]

    await service.approve_plugin(
        "demo", version="1", unconfirmed=["messages.push"], analysis_status="completed"
    )

    assert set(permissions.read("demo")["permissions"]) == {MESSAGES_PUSH}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["approve", "deny", "revoke"])
async def test_failed_consent_write_rolls_the_permission_store_back(consent_flow, operation):
    service, permissions = consent_flow["service"], consent_flow["permissions"]
    consent_flow["consents"].decide("demo", "approved", ["memory.read"], "1")
    consent_flow["report"]["capabilities"] = ["memory.read", "conversations.read"]
    before = permissions.read("demo")
    consent_flow["manager"].fail_name = "plugin_consents.json"

    with pytest.raises(ServerDomainError) as captured:
        if operation == "approve":
            await service.approve_plugin(
                "demo",
                version="1",
                unconfirmed=["conversations.read"],
                analysis_status="completed",
            )
        elif operation == "deny":
            await service.deny_plugin("demo")
        else:
            await service.revoke_plugin("demo")

    assert captured.value.code == "PLUGIN_CONSENT_WRITE_FAILED"
    assert permissions.read("demo") == before
    assert consent_flow["consents"].read("demo")["decision"] == "approved"


def test_writes_persist_an_import_held_in_memory_or_fail(manager):
    _write_legacy(
        manager,
        _legacy({"other": {"decision": "approved", "approved_capabilities": ["memory.read"]}}),
    )
    manager.fail = OSError("disk full")
    store = PluginPermissionStore(manager)
    assert store.read("demo") == {}  # reads degrade to memory

    # No record for "demo": forget has nothing to remove, but the store is
    # still not on disk, so the write must fail rather than report success.
    with pytest.raises(ServerDomainError) as captured:
        store.forget("demo")
    assert captured.value.code == "PLUGIN_PERMISSIONS_WRITE_FAILED"

    manager.fail = None
    assert store.forget("demo") is None
    on_disk = json.loads(_new_file(manager).read_text(encoding="utf-8"))
    assert set(on_disk["plugins"]) == {"other"}


def test_consent_approval_replaces_an_explicit_undecided_entry(manager):
    _write_legacy(manager, _legacy({"x": {"decision": "denied"}}))
    store = PluginPermissionStore(manager)
    store.clear("x", MEMORY_READ)  # legacy_denied record: explicit undecided
    store.decide("x", CONVERSATIONS_READ, "denied")

    record = store.apply_legacy_decision(
        "x", "approved", ["memory.read", "conversations.read"],
        newly_approved=["memory.read", "conversations.read"],
    )

    assert record["permissions"][MEMORY_READ]["state"] == "granted"
    assert record["permissions"][MEMORY_READ]["legacy_any"] is True
    # A real decision from the permission flow is still kept.
    assert record["permissions"][CONVERSATIONS_READ]["state"] == "denied"


@pytest.mark.asyncio
async def test_failed_undo_is_reported_on_the_consent_error(consent_flow, monkeypatch):
    service, permissions = consent_flow["service"], consent_flow["permissions"]
    consent_flow["consents"].decide("demo", "approved", ["memory.read"], "1")
    permissions.read("demo")
    consent_flow["manager"].fail_name = "plugin_consents.json"

    def _restore_fails(*_args, **_kwargs):
        raise ServerDomainError("PLUGIN_PERMISSIONS_WRITE_FAILED", "disk full", 503)

    monkeypatch.setattr(permissions, "restore", _restore_fails)

    with pytest.raises(ServerDomainError) as captured:
        await service.revoke_plugin("demo")

    assert captured.value.code == "PLUGIN_CONSENT_WRITE_FAILED"
    assert captured.value.details["permission_rollback"] == "incomplete"


def test_sync_source_with_a_pending_import_only_fails_a_needed_write(manager):
    _write_legacy(
        manager,
        _legacy({"x": {"decision": "approved", "approved_capabilities": ["memory.read"]}}),
    )
    manager.fail = OSError("disk full")
    store = PluginPermissionStore(manager)

    # Nothing to write: no error even though the import is not saved yet.
    assert MEMORY_READ in store.sync_source("x", None)["permissions"]
    assert store.sync_source("nobody", MARKET) == {}
    # A first binding is logged, not raised.
    assert store.sync_source("x", MARKET)["source"] == MARKET.to_dict()
    # A real source change still reports the failed write.
    manager.fail = None
    store.sync_source("x", MARKET)
    manager.fail = OSError("disk full")
    with pytest.raises(ServerDomainError):
        store.sync_source("x", MANUAL)


@pytest.mark.parametrize(
    ("content", "status", "restored"),
    [
        (None, "taken", True),
        ("{broken", "unusable", True),
        ("dir", "unavailable", False),
    ],
)
def test_permission_snapshot_statuses(monkeypatch, manager, content, status, restored):
    store = PluginPermissionStore(manager)
    monkeypatch.setattr(permission_service, "permission_store", store)
    if content == "dir":
        manager.root.mkdir(parents=True)
        _new_file(manager).mkdir()  # transient-style read failure (OSError)
    elif content is not None:
        manager.root.mkdir(parents=True)
        _new_file(manager).write_text(content, encoding="utf-8")
    else:
        store.decide("x", MEMORY_READ, "granted")

    snapshot = permission_service.snapshot_permissions("x")

    assert snapshot.status == status
    assert snapshot.restore("x") is restored
    if status == "taken":
        assert MEMORY_READ in store.read("x")["permissions"]


def test_restore_of_an_unchanged_record_does_not_write(manager):
    _write_legacy(
        manager,
        _legacy({"x": {"decision": "approved", "approved_capabilities": ["memory.read"]}}),
    )
    manager.fail = OSError("disk full")
    store = PluginPermissionStore(manager)
    record = store.read("x")

    store.restore("x", record)  # import still pending, record unchanged: no write


def test_unavailable_snapshot_only_matters_when_the_store_may_have_changed():
    snapshot = permission_service.PermissionSnapshot("unavailable")

    assert snapshot.restore("x", store_may_have_changed=False) is True
    assert snapshot.restore("x") is False
