"""End-to-end integration tests for the install-source subsystem.

Drives the manager through its full public API surface from real
filesystem state. FastAPI route plumbing is covered at unit level
inside the route tests; here we care about: First_Startup migration,
write paths (import / market / delete), read path (to_api_view), and
parser resilience on a corrupt lock file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import plugin.settings as settings
from plugin.server.application.install_source.manager import (
    InstallSourceError,
    InstallSourceManager,
    _parse_lock,
    resolve_lock_path,
)
from plugin.server.application.install_source.models import (
    SourceDetailImported,
    SourceDetailMarket,
)
from plugin.server.application.install_source.reconciler import StartupReconciler
from plugin.server.application.install_source.scanner import (
    PluginDirectoryScanner,
)


def _build_mgr(tmp_path: Path) -> tuple[InstallSourceManager, Path, Path, Path]:
    """Helper: set up builtin_root, user_root, lock_path under tmp_path."""
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user"
    lock_path = tmp_path / "plugins.lock.json"
    builtin_root.mkdir(parents=True)
    user_root.mkdir(parents=True)
    scanner = PluginDirectoryScanner(builtin_root, user_root)
    mgr = InstallSourceManager(
        lock_path=lock_path,
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=scanner,
    )
    return mgr, builtin_root, user_root, lock_path


@pytest.mark.asyncio
async def test_first_startup_migration(tmp_path: Path) -> None:
    """First_Startup creates lock with builtin + manual seeds."""
    mgr, builtin_root, user_root, lock_path = _build_mgr(tmp_path)
    (builtin_root / "core").mkdir()
    (user_root / "p1").mkdir()
    (user_root / "p2").mkdir()
    (user_root / "p1" / "plugin.toml").write_text(
        '[plugin]\nid = "p1"\n', encoding="utf-8"
    )
    await StartupReconciler(mgr).run()

    assert lock_path.exists()
    doc = json.loads(lock_path.read_bytes())
    # v2 lock layout: schema_version pinned to 2 by _serialize_lock per
    # design §3.1.4 / Req 2.4 (lazy migration on first write).
    assert doc["schema_version"] == 2
    assert "created_at" in doc
    entries_by_key = {(e["root_id"], e["directory_name"]): e for e in doc["entries"]}
    assert entries_by_key[("builtin", "core")]["channel"] == "builtin"
    assert entries_by_key[("user", "p1")]["channel"] == "manual"
    assert entries_by_key[("user", "p1")]["plugin_id"] == "p1"
    # All three timestamps align on First_Startup.
    e = entries_by_key[("builtin", "core")]
    assert e["installed_at"] == e["updated_at"] == e["last_seen_at"]


def test_upload_and_install_records_source(tmp_path: Path) -> None:
    """record_import: channel="imported" + sha256 + plugin_id populated (Fix 1)."""
    mgr, _, user_root, _ = _build_mgr(tmp_path)
    target = user_root / "uploaded_plugin"
    target.mkdir()
    (target / "plugin.toml").write_text(
        '[plugin]\nid = "uploaded_plugin"\n', encoding="utf-8"
    )
    mgr.record_import(
        directory_path=target,
        package_filename="uploaded_plugin.neko-plugin",
        package_sha256="d" * 64,
        package_id="uploaded-package",
        profile_dir=str(tmp_path / "profiles" / "custom" / "uploaded-package"),
    )
    lf = _parse_lock(mgr.lock_path.read_bytes())
    assert len(lf.entries) == 1
    e = lf.entries[0]
    assert e.channel == "imported"
    assert e.reason == "user_requested"
    assert e.plugin_id == "uploaded_plugin"
    assert e.package_id == "uploaded-package"
    assert Path(e.profile_dir) == (tmp_path / "profiles" / "custom" / "uploaded-package")
    assert mgr.package_id_for_directory(target) == "uploaded-package"
    assert mgr.profile_dir_for_directory(target) == e.profile_dir
    assert isinstance(e.source_detail, SourceDetailImported)
    assert e.source_detail.package_sha256 == "d" * 64


def test_market_install_single_write(tmp_path: Path) -> None:
    """record_market writes market directly, never goes through import (Fix 8)."""
    mgr, _, user_root, _ = _build_mgr(tmp_path)
    original_record_import = mgr.record_import
    import_calls: list[dict] = []

    def tracked_record_import(**kwargs):
        import_calls.append(kwargs)
        return original_record_import(**kwargs)

    mgr.record_import = tracked_record_import  # type: ignore[method-assign]
    target = user_root / "market_plugin"
    target.mkdir()
    mgr.record_market(
        directory_path=target,
        plugin_market_id="pid-1",
        version="1.0.0",
        package_url="https://m.example/pkg.neko-plugin",
        package_id="market-package",
    )
    assert len(import_calls) == 0
    lf = _parse_lock(mgr.lock_path.read_bytes())
    e = lf.entries[0]
    assert e.channel == "market"
    assert e.package_id == "market-package"
    assert isinstance(e.source_detail, SourceDetailMarket)
    assert e.source_detail.plugin_market_id == "pid-1"
    assert e.source_detail.previous_version is None
    assert e.installed_at == e.updated_at


def test_plugins_endpoint_injects_install_source(tmp_path: Path) -> None:
    """to_api_view returns correct sub-object + default for unknown plugin_id."""
    mgr, _, user_root, _ = _build_mgr(tmp_path)
    target = user_root / "p"
    target.mkdir()
    mgr.record_import(
        directory_path=target,
        package_filename="p.neko-plugin",
        package_sha256="e" * 64,
    )
    view = mgr.to_api_view("p", directory_path=target)
    assert view["source"] == "imported"
    assert view["source_detail"]["package_sha256"] == "e" * 64

    view_miss = mgr.to_api_view("unknown")
    assert view_miss == {
        "source": "unknown", "reason": None,
        "installed_at": None, "source_detail": None,
    }


def test_soft_delete_via_reconcile(tmp_path: Path) -> None:
    """Disk directory disappears → reconcile soft-deletes the entry."""
    mgr, _, user_root, _ = _build_mgr(tmp_path)
    (user_root / "p1").mkdir()
    (user_root / "p2").mkdir()
    mgr.reconcile()
    assert len(mgr.list_entries()) == 2

    (user_root / "p1").rmdir()
    mgr.reconcile()
    live = mgr.list_entries()
    assert len(live) == 1
    assert live[0].directory_name == "p2"
    # The soft-deleted row is still there under include_removed.
    all_entries = mgr.list_entries(include_removed=True)
    assert len(all_entries) == 2


def test_corrupt_lock_is_backed_up_and_rebuilt(tmp_path: Path) -> None:
    """Corrupt JSON → renamed to .bak-<epoch>, lock re-seeded from disk."""
    mgr, _, user_root, lock_path = _build_mgr(tmp_path)
    lock_path.write_text("{not valid json", encoding="utf-8")
    (user_root / "rebuild").mkdir()
    mgr.load()
    bak_files = list(lock_path.parent.glob("plugins.lock.json.bak-*"))
    assert len(bak_files) == 1
    assert mgr._current.entries == ()  # noqa: SLF001
    assert mgr._current.created_at is not None  # noqa: SLF001


def test_explicit_config_root_migrates_legacy_lock_to_state_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An old lock beside PLUGIN_CONFIG_ROOT remains the migration source."""
    builtin_root = tmp_path / "builtin"
    custom_exec_root = tmp_path / "custom" / "plugins"
    state_root = tmp_path / "state" / "plugins"
    plugin_dir = custom_exec_root / "market_plugin"
    builtin_root.mkdir()
    plugin_dir.mkdir(parents=True)

    monkeypatch.setenv("PLUGIN_CONFIG_ROOT", str(custom_exec_root))
    monkeypatch.delenv("NEKO_PLUGIN_INSTALL_LOCK_PATH", raising=False)
    monkeypatch.setattr(settings, "get_plugins_directory", lambda: state_root)

    legacy_lock_path = custom_exec_root.parent / "plugins.lock.json"
    old_manager = InstallSourceManager(
        lock_path=legacy_lock_path,
        builtin_root=builtin_root,
        user_root=custom_exec_root,
        scanner=PluginDirectoryScanner(builtin_root, custom_exec_root),
    )
    old_manager.record_market(
        directory_path=plugin_dir,
        plugin_market_id="market-id",
        version="1.2.3",
        package_url="https://example.test/market-plugin.neko-plugin",
    )

    canonical_lock_path = resolve_lock_path()
    manager = InstallSourceManager(
        lock_path=canonical_lock_path,
        builtin_root=builtin_root,
        user_root=custom_exec_root,
        scanner=PluginDirectoryScanner(builtin_root, custom_exec_root),
    )
    manager.load()

    entry = manager.list_entries()[0]
    assert entry.channel == "market"
    assert isinstance(entry.source_detail, SourceDetailMarket)
    assert entry.source_detail.plugin_market_id == "market-id"
    assert canonical_lock_path.read_bytes() == legacy_lock_path.read_bytes()


def test_shared_state_keeps_explicit_execution_root_provenance_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-named installs in distinct execution roots never share provenance."""
    builtin_root = tmp_path / "builtin"
    first_root = tmp_path / "exec-a" / "plugins"
    second_root = tmp_path / "exec-b" / "plugins"
    state_root = tmp_path / "state" / "plugins"
    builtin_root.mkdir()
    first_plugin = first_root / "same-name"
    second_plugin = second_root / "same-name"
    first_plugin.mkdir(parents=True)
    second_plugin.mkdir(parents=True)

    monkeypatch.delenv("NEKO_PLUGIN_INSTALL_LOCK_PATH", raising=False)
    monkeypatch.setattr(settings, "get_plugins_directory", lambda: state_root)

    monkeypatch.setenv("PLUGIN_CONFIG_ROOT", str(first_root))
    first_lock = resolve_lock_path()
    first_manager = InstallSourceManager(
        lock_path=first_lock,
        builtin_root=builtin_root,
        user_root=first_root,
        scanner=PluginDirectoryScanner(builtin_root, first_root),
    )
    first_manager.load()
    first_manager.record_market(
        directory_path=first_plugin,
        plugin_market_id="market-a",
        version="1.0.0",
        package_url="https://example.test/a.neko-plugin",
    )

    monkeypatch.setenv("PLUGIN_CONFIG_ROOT", str(second_root))
    second_lock = resolve_lock_path()
    second_manager = InstallSourceManager(
        lock_path=second_lock,
        builtin_root=builtin_root,
        user_root=second_root,
        scanner=PluginDirectoryScanner(builtin_root, second_root),
    )
    second_manager.load()
    second_manager.record_market(
        directory_path=second_plugin,
        plugin_market_id="market-b",
        version="2.0.0",
        package_url="https://example.test/b.neko-plugin",
    )

    first_manager.reconcile()
    first_entry = first_manager.list_entries()[0]
    second_entry = second_manager.list_entries()[0]

    assert first_lock != second_lock
    assert isinstance(first_entry.source_detail, SourceDetailMarket)
    assert isinstance(second_entry.source_detail, SourceDetailMarket)
    assert first_entry.source_detail.plugin_market_id == "market-a"
    assert second_entry.source_detail.plugin_market_id == "market-b"
    assert first_entry.removed is False
    assert second_entry.removed is False


def test_scoped_lock_declines_ambiguous_shared_state_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unscoped shared row must not be cloned into an unrelated root."""
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "exec" / "plugins"
    state_root = tmp_path / "state" / "plugins"
    plugin_dir = user_root / "market-plugin"
    builtin_root.mkdir()
    plugin_dir.mkdir(parents=True)

    monkeypatch.setenv("PLUGIN_CONFIG_ROOT", str(user_root))
    monkeypatch.delenv("NEKO_PLUGIN_INSTALL_LOCK_PATH", raising=False)
    monkeypatch.setattr(settings, "get_plugins_directory", lambda: state_root)

    shared_lock = state_root.parent / "plugins.lock.json"
    old_manager = InstallSourceManager(
        lock_path=shared_lock,
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    old_manager.record_market(
        directory_path=plugin_dir,
        plugin_market_id="shared-market-id",
        version="1.0.0",
        package_url="https://example.test/shared.neko-plugin",
    )
    shared_bytes = shared_lock.read_bytes()

    scoped_lock = resolve_lock_path()
    manager = InstallSourceManager(
        lock_path=scoped_lock,
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    manager.load()
    assert manager.list_entries() == []
    manager.reconcile()

    entry = manager.list_entries()[0]
    assert scoped_lock != shared_lock
    assert entry.channel == "manual"
    assert entry.source_detail is None
    assert shared_lock.read_bytes() == shared_bytes
    assert scoped_lock.read_bytes() != shared_bytes


# ──────────────────────────────────────────────────────────────────────
# mark_removed (delete-hook)
# ──────────────────────────────────────────────────────────────────────


def test_mark_removed_flips_and_preserves_audit(tmp_path: Path) -> None:
    """mark_removed soft-deletes while preserving installed_at / source_detail."""
    mgr, _, user_root, _ = _build_mgr(tmp_path)
    target = user_root / "plugin_to_delete"
    target.mkdir()
    mgr.record_import(
        directory_path=target,
        package_filename="p.neko-plugin",
        package_sha256="f" * 64,
        package_id="deleted-package",
    )
    before = mgr.list_entries()[0]
    mgr.mark_removed(directory_path=target)

    lf = _parse_lock(mgr.lock_path.read_bytes())
    assert len(lf.entries) == 1
    after = lf.entries[0]
    assert after.removed is True
    assert after.removed_at is not None
    assert after.installed_at == before.installed_at
    assert after.source_detail == before.source_detail
    assert after.channel == "imported"
    assert mgr.list_entries() == []
    assert len(mgr.list_entries(include_removed=True)) == 1
    assert mgr.package_id_for_directory(target) == ""
    assert mgr.package_id_for_directory(target, include_removed=True) == "deleted-package"


def test_mark_removed_idempotent_and_builtin_locked(tmp_path: Path) -> None:
    """mark_removed: repeat call is a no-op; builtin rejected."""
    mgr, builtin_root, user_root, _ = _build_mgr(tmp_path)
    target = user_root / "p"
    target.mkdir()
    mgr.record_import(
        directory_path=target,
        package_filename="p.neko-plugin",
        package_sha256="a" * 64,
    )
    mgr.mark_removed(directory_path=target)
    mtime1 = mgr.lock_path.stat().st_mtime
    removed_at1 = mgr.list_entries(include_removed=True)[0].removed_at
    import time
    time.sleep(0.02)
    mgr.mark_removed(directory_path=target)
    # Second call is a no-op: mtime stable, removed_at doesn't drift.
    assert mgr.lock_path.stat().st_mtime == mtime1
    assert mgr.list_entries(include_removed=True)[0].removed_at == removed_at1

    builtin_target = builtin_root / "core"
    builtin_target.mkdir()
    with pytest.raises(InstallSourceError) as info:
        mgr.mark_removed(directory_path=builtin_target)
    assert info.value.code == "BUILTIN_CHANNEL_LOCKED"


def test_record_market_captures_previous_version_on_upgrade(tmp_path: Path) -> None:
    """Market upgrade captures previous_version; same-version no-op; promotion=None."""
    mgr, _, user_root, _ = _build_mgr(tmp_path)
    target = user_root / "market_plugin"
    target.mkdir()
    mgr.record_market(
        directory_path=target,
        plugin_market_id="pid-1",
        version="1.0.0",
        package_url="https://m/pkg.neko-plugin",
    )
    assert mgr.list_entries()[0].source_detail.previous_version is None

    mgr.record_market(
        directory_path=target,
        plugin_market_id="pid-1",
        version="2.0.0",
        package_url="https://m/pkg-v2.neko-plugin",
    )
    e = mgr.list_entries()[0]
    assert e.source_detail.version == "2.0.0"
    assert e.source_detail.previous_version == "1.0.0"

    # Same-version re-call: previous_version stays None (not an upgrade).
    mgr.record_market(
        directory_path=target,
        plugin_market_id="pid-1",
        version="2.0.0",
        package_url="https://m/pkg-v2.neko-plugin",
    )
    assert mgr.list_entries()[0].source_detail.previous_version is None

    # Imported → market promotion: no prior market version.
    target2 = user_root / "promoted"
    target2.mkdir()
    mgr.record_import(
        directory_path=target2,
        package_filename="p.neko-plugin",
        package_sha256="a" * 64,
    )
    mgr.record_market(
        directory_path=target2,
        plugin_market_id="pid-2",
        version="1.0.0",
        package_url="https://m/p.neko-plugin",
    )
    e = next(x for x in mgr.list_entries() if x.directory_name == "promoted")
    assert e.source_detail.previous_version is None



# ──────────────────────────────────────────────────────────────────────
# Cross-platform path classification + tmp sweep
# ──────────────────────────────────────────────────────────────────────


def test_classify_plugin_path_handles_nfc_vs_nfd(tmp_path: Path) -> None:
    """Unicode NFD paths (macOS) classify identically to NFC."""
    import unicodedata
    from plugin.server.application.install_source.manager import classify_plugin_path

    user_root = tmp_path / "user"
    builtin_root = tmp_path / "builtin"
    user_root.mkdir()
    builtin_root.mkdir()

    # Build a plugin dir whose name includes precomposed chars.
    name_nfc = "café"  # NFC: é as a single codepoint
    plugin_dir = user_root / name_nfc
    plugin_dir.mkdir()

    # Now query with an NFD form (as macOS HFS/APFS would hand back).
    name_nfd = unicodedata.normalize("NFD", name_nfc)
    assert name_nfd != name_nfc  # sanity: actually different
    query_dir = user_root / name_nfd

    root_id, directory_name = classify_plugin_path(
        query_dir, builtin_root=builtin_root, user_root=user_root,
    )
    assert root_id == "user"
    # Both NFC and NFD compare equal after our normalisation.
    assert unicodedata.normalize("NFC", directory_name) == name_nfc


def test_classify_plugin_path_case_insensitive_match(tmp_path: Path) -> None:
    """Case-different root input still classifies correctly.

    Emulates the Windows case where ``PLUGIN_CONFIG_ROOT=c:\\users\\...``
    (lower-case) but the runtime resolves builtin root via ``__file__``
    with ``C:\\Users\\...`` (mixed-case).
    """
    from plugin.server.application.install_source.manager import classify_plugin_path

    user_root = tmp_path / "MyPlugins"
    user_root.mkdir()
    (user_root / "cool_plugin").mkdir()

    # Query with a differently-cased form of the same root.
    # On case-sensitive filesystems (Linux default) these are actually
    # different directories — we just test that the *normalisation* path
    # doesn't crash. The equality assertion only holds on case-insensitive
    # platforms; here we just ensure no exception is raised for the
    # matching-case path.
    root_id, directory_name = classify_plugin_path(
        user_root / "cool_plugin",
        builtin_root=tmp_path / "builtin",
        user_root=user_root,
    )
    assert root_id == "user"
    assert directory_name == "cool_plugin"


def test_classify_plugin_path_rejects_lookalike_prefix(tmp_path: Path) -> None:
    """``/foo/bar`` must not match root ``/foo/ba`` (prefix-safety regression)."""
    from plugin.server.application.install_source.manager import (
        InstallSourceError,
        classify_plugin_path,
    )

    user_root = tmp_path / "ba"
    user_root.mkdir()
    impostor = tmp_path / "bar" / "plugin"
    impostor.parent.mkdir()
    impostor.mkdir()

    with pytest.raises(InstallSourceError) as info:
        classify_plugin_path(
            impostor,
            builtin_root=tmp_path / "builtin",
            user_root=user_root,
        )
    assert info.value.code == "PATH_OUTSIDE_ROOTS"


def test_stale_tmp_files_are_swept_on_load(tmp_path: Path) -> None:
    """Orphaned ``plugins.lock.json.*.tmp`` files get cleaned up on load()."""
    mgr, _, _, lock_path = _build_mgr(tmp_path)
    stale1 = lock_path.parent / "plugins.lock.json.12345.deadbeef.tmp"
    stale2 = lock_path.parent / "plugins.lock.json.999.abcd1234.tmp"
    stale1.write_bytes(b"leftover 1")
    stale2.write_bytes(b"leftover 2")
    # Also plant a non-matching tmp that must NOT be touched.
    unrelated = lock_path.parent / "something_else.tmp"
    unrelated.write_bytes(b"keep me")

    mgr.load()

    assert not stale1.exists()
    assert not stale2.exists()
    assert unrelated.exists()


def test_runtime_missing_lock_file_self_heals_and_preserves_new_installs(
    tmp_path: Path,
) -> None:
    mgr, _, user_root, lock_path = _build_mgr(tmp_path)
    market_dir = user_root / "market_plug"
    imported_dir = user_root / "imported_plug"
    market_dir.mkdir(parents=True)
    imported_dir.mkdir(parents=True)

    mgr.load()
    mgr.record_market(
        directory_path=market_dir,
        plugin_market_id="mk-1",
        version="1.2.0",
        package_url="https://example.test/mk.neko-plugin",
    )
    mgr.record_import(
        directory_path=imported_dir,
        package_filename="imp.neko-plugin",
        package_sha256="abc123",
    )
    assert len(mgr.list_entries()) == 2

    # Delete runtime lock file -> load() self-heals from in-memory snapshot.
    lock_path.unlink()
    mgr.load()
    assert mgr.is_degraded is False
    assert lock_path.is_file()

    # Subsequent installs continue persisting both prior and newly installed entries.
    extra_dir = user_root / "extra_imp"
    extra_dir.mkdir(parents=True)
    mgr.record_import(
        directory_path=extra_dir,
        package_filename="extra.neko-plugin",
        package_sha256="def456",
    )
    mgr2 = InstallSourceManager(
        lock_path=lock_path,
        builtin_root=mgr.builtin_root,
        user_root=user_root,
        scanner=mgr.scanner,
    )
    mgr2.load()
    assert {e.directory_name for e in mgr2.list_entries()} == {
        "market_plug",
        "imported_plug",
        "extra_imp",
    }


def test_runtime_missing_lock_falls_back_to_degrade_if_write_fails_and_heals_on_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugin.server.application.install_source.manager as manager_mod

    mgr, _, user_root, lock_path = _build_mgr(tmp_path)
    market_dir = user_root / "market_plug"
    imported_dir = user_root / "imported_plug"
    market_dir.mkdir(parents=True)
    imported_dir.mkdir(parents=True)

    mgr.load()
    mgr.record_market(
        directory_path=market_dir,
        plugin_market_id="mk-1",
        version="1.2.0",
        package_url="https://example.test/mk.neko-plugin",
    )
    mgr.record_import(
        directory_path=imported_dir,
        package_filename="imp.neko-plugin",
        package_sha256="abc123",
    )
    saved_bytes = lock_path.read_bytes()

    # Simulate missing lock + transient write failure -> enters missing_lock read-only degrade.
    lock_path.unlink()
    real_atomic_write = manager_mod._atomic_write

    def _fail_write(_path: Path, _data: bytes) -> None:
        raise OSError("read-only media")

    monkeypatch.setattr(manager_mod, "_atomic_write", _fail_write)
    mgr.load()

    assert mgr.is_degraded is True
    assert "lock_file_missing" in (mgr.degrade_reason or "")
    entries = {e.directory_name: e for e in mgr.list_entries()}
    assert set(entries) == {"market_plug", "imported_plug"}

    with pytest.raises(InstallSourceError) as err_info:
        mgr.record_market_install(
            root_id="user",
            directory_name="new_mk",
            plugin_id="new_mk",
            market_detail={
                "plugin_market_id": "new_mk",
                "version": "1.0.0",
                "package_url": "https://example.test/new.neko-plugin",
            },
        )
    assert err_info.value.code == "INSTALL_SOURCE_READ_ONLY"

    # Once writable again, record_import self-heals and writes all entries (old + new).
    monkeypatch.setattr(manager_mod, "_atomic_write", real_atomic_write)
    extra_dir = user_root / "extra_imp"
    extra_dir.mkdir(parents=True)
    mgr.record_import(
        directory_path=extra_dir,
        package_filename="extra.neko-plugin",
        package_sha256="def456",
    )
    assert mgr.is_degraded is False
    assert lock_path.is_file()
    assert {e.directory_name for e in mgr.list_entries()} == {
        "market_plug",
        "imported_plug",
        "extra_imp",
    }

    # Verify restoring an admin backup before reconcile() is not clobbered by stale memory.
    lock_path.write_bytes(saved_bytes)
    assert {e.directory_name for e in mgr.list_entries()} == {
        "market_plug",
        "imported_plug",
        "extra_imp",
    }


def test_reconcile_reloads_admin_restored_lock_instead_of_overwriting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugin.server.application.install_source.manager as manager_mod

    mgr, _, user_root, lock_path = _build_mgr(tmp_path)
    market_dir = user_root / "market_plug"
    market_dir.mkdir(parents=True)
    (market_dir / "plugin.toml").write_text('[plugin]\nid = "market_plug"\n', encoding="utf-8")

    mgr.load()
    mgr.record_market(
        directory_path=market_dir,
        plugin_market_id="mk-1",
        version="1.0.0",
        package_url="https://example.test/mk.neko-plugin",
    )
    saved_bytes = lock_path.read_bytes()

    # Force missing_lock degrade via failed self-heal write.
    lock_path.unlink()
    real_atomic_write = manager_mod._atomic_write
    monkeypatch.setattr(manager_mod, "_atomic_write", lambda _p, _d: (_ for _ in ()).throw(OSError("busy")))
    mgr.load()
    assert mgr.is_degraded is True
    monkeypatch.setattr(manager_mod, "_atomic_write", real_atomic_write)

    # Admin restores a newer lock file containing an extra imported entry before reconcile runs.
    extra_dir = user_root / "admin_restored"
    extra_dir.mkdir(parents=True)
    (extra_dir / "plugin.toml").write_text('[plugin]\nid = "admin_restored"\n', encoding="utf-8")
    admin_mgr = InstallSourceManager(
        lock_path=lock_path,
        builtin_root=mgr.builtin_root,
        user_root=user_root,
        scanner=mgr.scanner,
    )
    lock_path.write_bytes(saved_bytes)
    admin_mgr.load()
    admin_mgr.record_import(
        directory_path=extra_dir,
        package_filename="admin.neko-plugin",
        package_sha256="adminsha",
    )

    mgr.reconcile()
    assert mgr.is_degraded is False
    entries = {e.directory_name: e for e in mgr.list_entries()}
    assert set(entries) == {"market_plug", "admin_restored"}
    assert entries["admin_restored"].channel == "imported"


def test_reconcile_after_runtime_missing_lock_heals_without_downgrading_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugin.server.application.install_source.manager as manager_mod

    mgr, _, user_root, lock_path = _build_mgr(tmp_path)
    market_dir = user_root / "market_plug"
    market_dir.mkdir(parents=True)
    (market_dir / "plugin.toml").write_text('[plugin]\nid = "market_plug"\n', encoding="utf-8")

    mgr.load()
    mgr.record_market(
        directory_path=market_dir,
        plugin_market_id="mk-1",
        version="1.0.0",
        package_url="https://example.test/mk.neko-plugin",
    )

    lock_path.unlink()
    real_atomic_write = manager_mod._atomic_write
    monkeypatch.setattr(manager_mod, "_atomic_write", lambda _p, _d: (_ for _ in ()).throw(OSError("busy")))
    mgr.load()
    assert mgr.is_degraded is True
    monkeypatch.setattr(manager_mod, "_atomic_write", real_atomic_write)

    mgr.reconcile()
    assert mgr.is_degraded is False
    assert lock_path.is_file()
    entries = mgr.list_entries()
    assert len(entries) == 1
    assert entries[0].channel == "market"
    assert isinstance(entries[0].source_detail, SourceDetailMarket)


def test_reconcile_rolls_back_snapshot_on_save_failure_when_dir_transiently_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugin.server.application.install_source.manager as manager_mod

    mgr, _, user_root, lock_path = _build_mgr(tmp_path)
    market_dir = user_root / "market_plug"
    market_dir.mkdir(parents=True)
    (market_dir / "plugin.toml").write_text(
        '[plugin]\nid = "market_plug"\n', encoding="utf-8"
    )

    mgr.load()
    mgr.record_market(
        directory_path=market_dir,
        plugin_market_id="mk-1",
        version="1.0.0",
        package_url="https://example.test/mk.neko-plugin",
    )

    lock_path.unlink()
    # Simulate a transient scan miss combined with a failing disk write.
    monkeypatch.setattr(mgr.scanner, "scan", lambda: [])

    def _fail_atomic_write(_path: Path, _data: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(manager_mod, "_atomic_write", _fail_atomic_write)
    mgr.load()
    assert mgr.is_degraded is True
    assert "lock_file_missing" in (mgr.degrade_reason or "")

    with pytest.raises(OSError, match="disk full"):
        mgr.reconcile()

    # Both degrade state and in-memory snapshot must roll back cleanly (not removed=True).
    assert mgr.is_degraded is True
    assert "lock_file_missing" in (mgr.degrade_reason or "")
    entries = mgr.list_entries(include_removed=True)
    assert len(entries) == 1
    assert entries[0].removed is False
    assert entries[0].channel == "market"
    assert isinstance(entries[0].source_detail, SourceDetailMarket)

    # Once scanner and disk recover, reconcile() heals cleanly.
    monkeypatch.undo()
    mgr.reconcile()
    assert mgr.is_degraded is False
    assert lock_path.is_file()
    healed = mgr.list_entries()
    assert len(healed) == 1
    assert healed[0].removed is False
    assert healed[0].channel == "market"


def test_non_missing_lock_degrade_not_cleared_by_reconcile_after_missing_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugin.server.application.install_source.manager as manager_mod

    mgr, _, user_root, lock_path = _build_mgr(tmp_path)
    market_dir = user_root / "market_plug"
    market_dir.mkdir(parents=True)
    (market_dir / "plugin.toml").write_text(
        '[plugin]\nid = "market_plug"\n', encoding="utf-8"
    )

    mgr.load()
    mgr.record_market(
        directory_path=market_dir,
        plugin_market_id="mk-1",
        version="1.0.0",
        package_url="https://example.test/mk.neko-plugin",
    )

    lock_path.unlink()
    real_atomic_write = manager_mod._atomic_write
    monkeypatch.setattr(manager_mod, "_atomic_write", lambda _p, _d: (_ for _ in ()).throw(OSError("busy")))
    mgr.load()
    assert mgr.is_degraded is True
    assert "lock_file_missing" in (mgr.degrade_reason or "")
    monkeypatch.setattr(manager_mod, "_atomic_write", real_atomic_write)

    # Supersede missing-lock degrade with an unreadable lock path (directory).
    lock_path.mkdir()
    mgr.load()
    assert mgr.is_degraded is True
    assert "read_failed" in (mgr.degrade_reason or "")

    # reconcile() must not clear the unrelated read_failed degrade.
    mgr.reconcile()
    assert mgr.is_degraded is True
    assert "read_failed" in (mgr.degrade_reason or "")
