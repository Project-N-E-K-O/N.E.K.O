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
from plugin.server.infrastructure.plugin_permissions import (
    CONVERSATIONS_READ,
    LEGACY_DENIED,
    MEMORY_READ,
    MESSAGES_PUSH,
    PluginPermissionStore,
    PluginSource,
    normalize_permission_id,
)

pytestmark = pytest.mark.plugin_unit

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
    "content",
    [
        "{broken",
        json.dumps({"schema_version": 2, "plugins": {}}),
        json.dumps({"schema_version": 1, "plugins": {"x": []}}),
        json.dumps({"schema_version": 1, "plugins": {"x": {"permissions": []}}}),
    ],
)
def test_unreadable_store_raises_and_is_never_overwritten(manager, content):
    manager.root.mkdir(parents=True)
    _new_file(manager).write_text(content, encoding="utf-8")
    store = PluginPermissionStore(manager)

    for call in (lambda: store.read("x"), lambda: store.decide("x", MEMORY_READ, "granted")):
        with pytest.raises(ServerDomainError) as captured:
            call()
        assert captured.value.code == "PLUGIN_PERMISSIONS_READ_FAILED"
    assert _new_file(manager).read_text(encoding="utf-8") == content
    assert manager.saves == 0


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


def test_normalize_permission_id_maps_legacy_names_only():
    assert normalize_permission_id("memory.read") == MEMORY_READ
    assert normalize_permission_id(MEMORY_READ) == MEMORY_READ
    assert normalize_permission_id("system.fs:read") == "system.fs:read"


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

