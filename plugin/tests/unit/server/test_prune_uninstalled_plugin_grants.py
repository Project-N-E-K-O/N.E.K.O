"""Startup pruning of grant records left behind by uninstalled plugins."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from plugin.core.state import state
from plugin.server.application.install_source.models import LockEntry
from plugin.server.application.install_source.reconciler import StartupReconciler
from plugin.server.application.plugins import operation_lock as operation_lock_module
from plugin.server.application.plugins import permission_service, project_sources
from plugin.server.application.plugins.operation_lock import plugin_operation_lock

pytestmark = pytest.mark.plugin_unit

MEMORY_READ = "plugin_sdk.main_agent.memory:read"
_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _iso(when: datetime) -> str:
    return when.isoformat().replace("+00:00", "Z")


def _entry(
    plugin_id: str,
    *,
    root_id: str = "user",
    directory_name: str | None = None,
    removed_at: datetime | None = None,
) -> LockEntry:
    stamp = _iso(_T0)
    return LockEntry(
        root_id=root_id,
        directory_name=directory_name or plugin_id or "unreadable",
        plugin_id=plugin_id,
        channel="manual",
        reason="user_requested",
        installed_at=stamp,
        updated_at=stamp,
        last_seen_at=stamp,
        removed=removed_at is not None,
        removed_at=_iso(removed_at) if removed_at is not None else None,
    )


class _Lock:
    def __init__(
        self, *entries: LockEntry, degraded: bool = False, root: Path | None = None
    ) -> None:
        self.entries = list(entries)
        self.is_degraded = degraded
        base = root or Path("/nonexistent-plugin-roots")
        self.builtin_root = base / "builtin"
        self.user_root = base / "user"

    def list_entries(self, *, include_removed: bool = False) -> list[LockEntry]:
        if include_removed:
            return list(self.entries)
        return [entry for entry in self.entries if not entry.removed]


def _store_path(tmp_path: Path) -> Path:
    # Location used by the isolated store from plugin/tests/conftest.py.
    return tmp_path / "consent" / "plugin_permissions.json"


def _grant(plugin_id: str) -> None:
    permission_service.permission_store.decide(plugin_id, MEMORY_READ, "granted")


def _grants_on_disk(tmp_path: Path) -> set[str]:
    document = json.loads(_store_path(tmp_path).read_text(encoding="utf-8"))
    return set(document["plugins"])


@pytest.fixture(autouse=True)
def no_projects_or_registered_plugins(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(project_sources, "list_project_sources_sync", lambda: ([], []))
    monkeypatch.setattr(state, "plugins", {})


@pytest.fixture
def removed_after_grants() -> datetime:
    """A removal time later than any grant written by the test."""
    return datetime.now(timezone.utc) + timedelta(minutes=1)


def test_uninstalled_plugin_grants_are_dropped(tmp_path, removed_after_grants):
    _grant("gone")
    _grant("kept")
    lock = _Lock(_entry("gone", removed_at=removed_after_grants), _entry("kept"))

    assert permission_service.prune_uninstalled_plugin_grants(lock) == ["gone"]

    assert _grants_on_disk(tmp_path) == {"kept"}
    assert permission_service.permission_store.read("gone") == {}


def test_several_uninstalled_plugins_are_dropped_in_one_write(
    tmp_path, monkeypatch, removed_after_grants
):
    for plugin_id in ("a", "b", "c"):
        _grant(plugin_id)
    saves = []
    store = permission_service.permission_store
    original = store._save
    monkeypatch.setattr(
        store, "_save", lambda manager, document: saves.append(1) or original(manager, document)
    )
    lock = _Lock(
        _entry("a", removed_at=removed_after_grants),
        _entry("b", removed_at=removed_after_grants),
    )

    assert permission_service.prune_uninstalled_plugin_grants(lock) == ["a", "b"]

    assert len(saves) == 1
    assert _grants_on_disk(tmp_path) == {"c"}


def test_active_entry_in_another_root_keeps_the_grants(tmp_path, removed_after_grants):
    # The user copy was uninstalled; the builtin copy is still installed.
    _grant("shared")
    lock = _Lock(
        _entry("shared", root_id="user", removed_at=removed_after_grants),
        _entry("shared", root_id="builtin"),
    )

    assert permission_service.prune_uninstalled_plugin_grants(lock) == []
    assert _grants_on_disk(tmp_path) == {"shared"}


def test_plugin_absent_from_the_lock_keeps_the_grants(tmp_path):
    # e.g. a workbench project plugin: never in the lock, so no evidence.
    _grant("project_plugin")
    _grant("not_discovered")

    assert permission_service.prune_uninstalled_plugin_grants(_Lock()) == []
    assert _grants_on_disk(tmp_path) == {"project_plugin", "not_discovered"}


def test_workbench_project_with_a_removed_id_keeps_the_grants(
    tmp_path, monkeypatch, removed_after_grants
):
    _grant("demo")
    project = project_sources.ProjectSource("p1", "demo", tmp_path / "project")
    monkeypatch.setattr(project_sources, "list_project_sources_sync", lambda: ([project], []))

    lock = _Lock(_entry("demo", removed_at=removed_after_grants))
    assert permission_service.prune_uninstalled_plugin_grants(lock) == []
    assert _grants_on_disk(tmp_path) == {"demo"}


def test_unreadable_workbench_project_skips_the_pass(
    tmp_path, monkeypatch, removed_after_grants
):
    _grant("demo")
    failure = project_sources.ProjectSourceFailure("p1", tmp_path / "project", "broken")
    monkeypatch.setattr(project_sources, "list_project_sources_sync", lambda: ([], [failure]))

    lock = _Lock(_entry("demo", removed_at=removed_after_grants))
    assert permission_service.prune_uninstalled_plugin_grants(lock) == []
    assert _grants_on_disk(tmp_path) == {"demo"}


def test_registered_plugin_with_a_removed_id_keeps_the_grants(
    tmp_path, monkeypatch, removed_after_grants
):
    _grant("demo")
    monkeypatch.setattr(state, "plugins", {"demo": {"id": "demo"}})

    lock = _Lock(_entry("demo", removed_at=removed_after_grants))
    assert permission_service.prune_uninstalled_plugin_grants(lock) == []
    assert _grants_on_disk(tmp_path) == {"demo"}


def test_active_entry_with_unreadable_metadata_skips_the_pass(
    tmp_path, removed_after_grants
):
    _grant("demo")
    renamed = tmp_path / "roots" / "user" / "renamed"
    renamed.mkdir(parents=True)
    (renamed / "plugin.toml").write_text("not toml [", encoding="utf-8")
    lock = _Lock(
        _entry("demo", removed_at=removed_after_grants),
        _entry("", directory_name="renamed"),
        root=tmp_path / "roots",
    )

    assert permission_service.prune_uninstalled_plugin_grants(lock) == []
    assert _grants_on_disk(tmp_path) == {"demo"}


def test_active_directory_without_plugin_toml_does_not_block(
    tmp_path, removed_after_grants
):
    _grant("demo")
    (tmp_path / "roots" / "user" / "stray").mkdir(parents=True)
    lock = _Lock(
        _entry("demo", removed_at=removed_after_grants),
        _entry("", directory_name="stray"),
        root=tmp_path / "roots",
    )

    assert permission_service.prune_uninstalled_plugin_grants(lock) == ["demo"]


def test_grants_updated_after_the_removal_are_kept(tmp_path):
    _grant("demo")
    lock = _Lock(_entry("demo", removed_at=_T0))

    assert permission_service.prune_uninstalled_plugin_grants(lock) == []
    assert _grants_on_disk(tmp_path) == {"demo"}


def test_latest_removal_decides_when_removed_twice(tmp_path, removed_after_grants):
    _grant("demo")
    lock = _Lock(
        _entry("demo", directory_name="old", removed_at=_T0),
        _entry("demo", directory_name="new", removed_at=removed_after_grants),
    )

    assert permission_service.prune_uninstalled_plugin_grants(lock) == ["demo"]
    assert _grants_on_disk(tmp_path) == set()


def test_degraded_lock_skips_the_pass(tmp_path, removed_after_grants):
    _grant("gone")
    lock = _Lock(_entry("gone", removed_at=removed_after_grants), degraded=True)

    assert permission_service.prune_uninstalled_plugin_grants(lock) == []
    assert permission_service.prune_uninstalled_plugin_grants(None) == []
    assert _grants_on_disk(tmp_path) == {"gone"}


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        json.dumps({"schema_version": 99, "plugins": {"gone": {"permissions": {}}}}),
    ],
    ids=["corrupt", "unsupported_version"],
)
def test_unreadable_store_skips_the_pass_and_is_left_alone(
    tmp_path, content, removed_after_grants
):
    path = _store_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    lock = _Lock(_entry("gone", removed_at=removed_after_grants))

    assert permission_service.prune_uninstalled_plugin_grants(lock) == []
    assert path.read_text(encoding="utf-8") == content


def test_nothing_to_prune_writes_nothing(tmp_path, monkeypatch, removed_after_grants):
    _grant("kept")
    before = _store_path(tmp_path).read_bytes()
    saves = []
    monkeypatch.setattr(
        permission_service.permission_store, "_save", lambda *args: saves.append(1)
    )
    # "gone" was uninstalled but holds no grant record.
    lock = _Lock(_entry("gone", removed_at=removed_after_grants))

    assert permission_service.prune_uninstalled_plugin_grants(lock) == []
    assert saves == []
    assert _store_path(tmp_path).read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("prune", [True, False])
async def test_reconciler_prunes_after_reconcile_under_the_lock(monkeypatch, prune):
    calls: list[str] = []
    manager = type(
        "Manager",
        (),
        {
            "load": lambda self: calls.append("load"),
            "reconcile": lambda self: calls.append("reconcile"),
        },
    )()
    monkeypatch.setattr(
        operation_lock_module, "_reload_install_source_manager_sync", lambda: None
    )
    monkeypatch.setattr(
        permission_service,
        "prune_uninstalled_plugin_grants",
        lambda mgr: calls.append("prune") if mgr is manager else None,
    )

    async with plugin_operation_lock.hold():
        task = asyncio.create_task(
            StartupReconciler(manager, prune_uninstalled_grants=prune).run()
        )
        await asyncio.sleep(0)
        assert calls == []

    await task
    assert calls == (["load", "reconcile", "prune"] if prune else ["load", "reconcile"])


@pytest.mark.asyncio
async def test_reconciler_does_not_prune_after_a_failed_reconcile(monkeypatch):
    calls: list[str] = []

    def fail(self):
        raise RuntimeError("boom")

    manager = type("Manager", (), {"load": lambda self: None, "reconcile": fail})()
    monkeypatch.setattr(
        operation_lock_module, "_reload_install_source_manager_sync", lambda: None
    )
    monkeypatch.setattr(
        permission_service,
        "prune_uninstalled_plugin_grants",
        lambda mgr: calls.append("prune"),
    )

    await StartupReconciler(manager, prune_uninstalled_grants=True).run()
    assert calls == []


@pytest.mark.asyncio
async def test_real_lock_tombstone_prunes_after_reconcile(
    tmp_path, monkeypatch
):
    from plugin.server.application.install_source.manager import InstallSourceManager
    from plugin.server.application.install_source.scanner import PluginDirectoryScanner

    monkeypatch.setattr(
        operation_lock_module, "_reload_install_source_manager_sync", lambda: None
    )
    builtin_root, user_root = tmp_path / "builtin", tmp_path / "user"
    for plugin_id, root in (("gone", user_root), ("kept", user_root), ("core", builtin_root)):
        (root / plugin_id).mkdir(parents=True)
        (root / plugin_id / "plugin.toml").write_text(
            f'[plugin]\nid = "{plugin_id}"\n', encoding="utf-8"
        )
    (user_root / "stray").mkdir()
    manager = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json",
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    await StartupReconciler(manager).run()
    for plugin_id in ("gone", "kept", "core", "project_only"):
        _grant(plugin_id)

    # Uninstalled while the store was unreadable: the directory is gone but
    # its grants stayed; the next startup's reconcile writes the tombstone.
    import shutil

    shutil.rmtree(user_root / "gone")
    await StartupReconciler(manager, prune_uninstalled_grants=True).run()

    assert not manager.is_degraded
    assert any(e.plugin_id == "gone" and e.removed for e in manager.list_entries(include_removed=True))
    assert _grants_on_disk(tmp_path) == {"kept", "core", "project_only"}
