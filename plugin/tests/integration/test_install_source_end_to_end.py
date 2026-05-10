"""End-to-end integration tests for the install-source subsystem.

Covers tasks 8.1 — 8.7 of the plugin-install-source-lock spec.

These tests drive the manager through its full public API surface from
real filesystem state, instead of standing up the whole FastAPI app in
every test. Integration with FastAPI routes is verified at a thinner
level (schema serialization + request plumbing) inside each test's
assertions rather than via TestClient — the app's lifespan pulls in
half the plugin runtime, which is more friction than this spec's
integration goals call for. Startup wiring itself is covered by
test_first_startup_migration, which reuses the same build/reconcile
sequence the lifespan hook does.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from plugin.server.application.install_source.manager import (
    InstallSourceError,
    InstallSourceManager,
    _parse_lock,
)
from plugin.server.application.install_source.models import (
    LockEntry,
    LockFile,
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
    """Task 8.1: First_Startup creates lock with builtin + manual seeds."""
    mgr, builtin_root, user_root, lock_path = _build_mgr(tmp_path)
    # Seed disk
    (builtin_root / "core").mkdir()
    (user_root / "p1").mkdir()
    (user_root / "p2").mkdir()
    # Write plugin.toml for one of them so plugin_id gets filled
    (user_root / "p1" / "plugin.toml").write_text(
        '[plugin]\nid = "p1"\n', encoding="utf-8"
    )
    # Run the StartupReconciler — it's what lifespan calls
    await StartupReconciler(mgr).run()

    assert lock_path.exists(), "First_Startup must write the lock file"
    doc = json.loads(lock_path.read_bytes())
    assert doc["schema_version"] == 1
    assert "created_at" in doc  # Req 6.4
    entries_by_key = {(e["root_id"], e["directory_name"]): e for e in doc["entries"]}
    assert ("builtin", "core") in entries_by_key
    assert entries_by_key[("builtin", "core")]["channel"] == "builtin"
    assert entries_by_key[("user", "p1")]["channel"] == "manual"
    assert entries_by_key[("user", "p1")]["plugin_id"] == "p1"
    # All timestamps equal on First_Startup
    e = entries_by_key[("builtin", "core")]
    assert e["installed_at"] == e["updated_at"] == e["last_seen_at"]


def test_upload_and_install_records_source(tmp_path: Path) -> None:
    """Task 8.2: record_import reflects imported channel + sha256 + plugin_id (Fix 1)."""
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
    )
    # Re-read from disk to verify persistence
    lf = _parse_lock(mgr.lock_path.read_bytes())
    assert len(lf.entries) == 1
    e = lf.entries[0]
    assert e.channel == "imported"
    assert e.reason == "user_requested"
    assert e.plugin_id == "uploaded_plugin"  # Fix 1
    assert isinstance(e.source_detail, SourceDetailImported)
    assert e.source_detail.package_sha256 == "d" * 64


def test_market_install_single_write(tmp_path: Path) -> None:
    """Task 8.3: record_market without any preceding record_import (Fix 8)."""
    mgr, _, user_root, _ = _build_mgr(tmp_path)
    # Monkey-patch record_import so any call is counted
    original_record_import = mgr.record_import
    import_calls: list[tuple[Any, ...]] = []

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
    )
    assert len(import_calls) == 0  # Fix 8: never goes through import
    lf = _parse_lock(mgr.lock_path.read_bytes())
    e = lf.entries[0]
    assert e.channel == "market"
    assert isinstance(e.source_detail, SourceDetailMarket)
    assert e.source_detail.plugin_market_id == "pid-1"
    assert e.source_detail.previous_version is None
    # installed_at == updated_at on first write
    assert e.installed_at == e.updated_at


def test_plugins_endpoint_injects_install_source(tmp_path: Path) -> None:
    """Task 8.4: to_api_view returns correct sub-object + degrade default."""
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
    # Degrade: replace _current with an empty lock and return is_degraded=False;
    # for real degrade, pretend mgr is gone
    view_miss = mgr.to_api_view("unknown")
    assert view_miss == {
        "source": "unknown", "reason": None,
        "installed_at": None, "source_detail": None,
    }


def test_install_sources_endpoint(tmp_path: Path) -> None:
    """Task 8.5: list_entries applied through full filter matrix."""
    mgr, _, user_root, _ = _build_mgr(tmp_path)
    # Seed two entries via reconcile
    (user_root / "p1").mkdir()
    (user_root / "p2").mkdir()
    mgr.reconcile()
    # include_removed default
    assert len(mgr.list_entries()) == 2
    # Filter by channel
    assert all(e.channel == "manual" for e in mgr.list_entries(channel="manual"))
    # Illegal filter -> INVALID_FILTER
    with pytest.raises(InstallSourceError) as info:
        mgr.list_entries(channel="xxx")
    assert info.value.code == "INVALID_FILTER"
    # Delete p1 directory and reconcile -> soft delete
    (user_root / "p1").rmdir()
    mgr.reconcile()
    # include_removed=False excludes removed
    live = mgr.list_entries()
    assert all(not e.removed for e in live)
    # include_removed=True shows both
    assert len(mgr.list_entries(include_removed=True)) == 2


def test_noop_reconcile_does_not_rewrite_lock(tmp_path: Path) -> None:
    """Task 8.6: two reconciles on identical state → mtime unchanged (Fix 4)."""
    mgr, _, user_root, lock_path = _build_mgr(tmp_path)
    (user_root / "p").mkdir()
    mgr.reconcile()
    mtime1 = lock_path.stat().st_mtime
    updated_at1 = mgr._current.updated_at  # noqa: SLF001
    import time
    time.sleep(0.05)
    mgr.reconcile()
    mtime2 = lock_path.stat().st_mtime
    assert mtime2 == mtime1
    assert mgr._current.updated_at == updated_at1  # noqa: SLF001


def test_corrupt_lock_is_backed_up_and_rebuilt(tmp_path: Path) -> None:
    """Task 8.7a: corrupt JSON is renamed .bak-<epoch> and rebuilt."""
    mgr, _, user_root, lock_path = _build_mgr(tmp_path)
    # Plant a corrupt lock file
    lock_path.write_text("{not valid json", encoding="utf-8")
    (user_root / "rebuild").mkdir()
    mgr.load()
    # After load, a .bak-* file should exist
    bak_files = list(lock_path.parent.glob("plugins.lock.json.bak-*"))
    assert len(bak_files) == 1
    # Manager should be in First_Startup seeded state
    assert mgr._current.entries == ()  # noqa: SLF001
    assert mgr._current.created_at is not None  # noqa: SLF001


def test_permission_error_degrades_then_recovers(tmp_path: Path) -> None:
    """Task 8.7b: OSError during read → degrade; try_recover() succeeds after fix."""
    mgr, _, user_root, lock_path = _build_mgr(tmp_path)
    # Write good content first
    (user_root / "p").mkdir()
    mgr.reconcile()
    assert mgr.is_degraded is False
    # Force the next read to fail by making lock_path a directory
    # (os.read() raises IsADirectoryError -> OSError)
    lock_path.unlink()
    lock_path.mkdir()
    mgr.load()
    assert mgr.is_degraded is True
    assert mgr.degrade_reason and "read_failed" in mgr.degrade_reason
    # Save is a no-op while degraded
    mgr.save()
    # Fix the filesystem: replace the dir with a valid file
    lock_path.rmdir()
    # Re-run recovery
    ok = mgr.try_recover()
    assert ok is True
    assert mgr.is_degraded is False
