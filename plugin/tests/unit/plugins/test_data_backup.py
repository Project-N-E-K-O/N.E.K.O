from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import stat
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from plugin.plugins.data_backup import DataBackupPlugin
from plugin.plugins.data_backup.backup import BackupEngine, BackupError
from plugin.plugins.data_backup.schedule import ScheduleState


class _DataBackupContext:
    plugin_id = "data_backup"
    logger = None

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.metadata = {}
        self.bus = {}
        self._effective_config = {"plugin": {"store": {"enabled": False}}}

    async def get_own_config(self, timeout: float = 5.0) -> dict:
        return {"config": {}}


def _engine(tmp_path: Path) -> BackupEngine:
    data_root = tmp_path / "data"
    data_root.mkdir()
    return BackupEngine(
        data_root,
        data_root / "plugins" / "data_backup" / "data" / "snapshots",
    )


def test_schedule_is_disabled_by_default_and_starts_from_save_time() -> None:
    now = datetime(2026, 8, 7, 6, tzinfo=UTC)
    schedule = ScheduleState.from_config({})

    assert schedule.enabled is False
    assert schedule.next_run_at is None

    enabled = schedule.reconfigured(
        enabled=True, interval_days=7, groups=["core"], now=now
    )

    assert enabled.is_due(now=now + timedelta(days=6)) is False
    assert enabled.is_due(now=now + timedelta(days=7)) is True
    assert (
        ScheduleState.from_config(
            enabled.to_dict(), now=now + timedelta(days=1)
        ).next_run_at
        == enabled.next_run_at
    )


def test_schedule_success_and_failure_advance_persisted_plan() -> None:
    now = datetime(2026, 8, 7, 6, tzinfo=UTC)
    schedule = ScheduleState().reconfigured(
        enabled=True, interval_days=3, groups=["core", "assets"], now=now
    )

    succeeded = schedule.succeeded(
        now=now + timedelta(days=3), warning="retention cleanup delayed"
    )
    failed = schedule.failed("disk unavailable", now=now + timedelta(days=3))

    assert succeeded.last_run_at == (now + timedelta(days=3)).isoformat()
    assert succeeded.next_run_at == (now + timedelta(days=6)).isoformat()
    assert succeeded.last_error is None
    assert succeeded.last_warning == "retention cleanup delayed"
    assert (
        ScheduleState.from_config(succeeded.to_dict()).last_warning
        == "retention cleanup delayed"
    )
    assert failed.last_run_at is None
    assert failed.next_run_at == (now + timedelta(days=4)).isoformat()
    assert failed.last_error == "disk unavailable"


@pytest.mark.asyncio
async def test_plugin_startup_registers_ui_and_uses_selected_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin_dir = Path(__file__).parents[3] / "plugins" / "data_backup"
    data_root = tmp_path / "runtime-data"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(data_root))
    plugin = DataBackupPlugin(_DataBackupContext(plugin_dir / "plugin.toml"))

    await plugin.startup()

    assert plugin._engine is not None
    assert plugin._engine.data_root == data_root.resolve(strict=False)
    assert plugin.get_static_ui_config()["plugin_id"] == "data_backup"
    assert plugin.get_list_actions() == [
        {
            "id": "open_ui",
            "label": "打开备份管理",
            "kind": "ui",
            "target": "/plugin/data_backup/ui/",
            "open_in": "new_tab",
        }
    ]

    await plugin.shutdown()
    assert plugin._engine is None


def test_snapshot_and_restore_exact_core_state(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    config_file = engine.data_root / "config" / "config.json"
    memory_file = engine.data_root / "memory" / "cat" / "memory.db"
    config_file.parent.mkdir(parents=True)
    memory_file.parent.mkdir(parents=True)
    config_file.write_text('{"name":"before"}', encoding="utf-8")
    memory_file.write_bytes(b"memory-before")

    snapshot = engine.create_snapshot("core")
    config_file.write_text('{"name":"after"}', encoding="utf-8")
    memory_file.unlink()
    new_file = engine.data_root / "character_cards" / "new.json"
    new_file.parent.mkdir()
    new_file.write_text("new", encoding="utf-8")

    result = engine.restore_snapshot("core", snapshot["id"])

    assert config_file.read_text(encoding="utf-8") == '{"name":"before"}'
    assert memory_file.read_bytes() == b"memory-before"
    assert not new_file.exists()
    assert result["restored"] == snapshot["id"]
    assert result["safety_snapshot"] != snapshot["id"]
    assert result["restart_required"] is True
    assert stat.S_IMODE(config_file.stat().st_mode) & stat.S_IWRITE
    config_file.write_text('{"name":"writable"}', encoding="utf-8")


def test_snapshot_and_restore_preserve_nested_empty_directories(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    nested = engine.data_root / "config" / "empty" / "nested"
    nested.mkdir(parents=True)

    snapshot = engine.create_snapshot("core")
    shutil.rmtree(engine.data_root / "config")
    engine.restore_snapshot("core", snapshot["id"])

    assert nested.is_dir()


def test_restore_accepts_version_one_snapshot_manifests(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    source = engine.data_root / "config" / "value.txt"
    source.parent.mkdir(parents=True)
    source.write_text("before", encoding="utf-8")
    snapshot = engine.create_snapshot("core")
    manifest_path = engine.backup_root / "core" / snapshot["id"] / "manifest.json"
    manifest_path.chmod(stat.S_IMODE(manifest_path.stat().st_mode) | stat.S_IWRITE)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = 1
    manifest.pop("directories")
    for metadata in manifest["files"].values():
        metadata.pop("mode")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    source.write_text("after", encoding="utf-8")

    engine.restore_snapshot("core", snapshot["id"])

    assert source.read_text(encoding="utf-8") == "before"
    assert stat.S_IMODE(source.stat().st_mode) & stat.S_IWRITE


def test_restore_locked_memory_directory_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(tmp_path)
    database = engine.data_root / "memory" / "cat" / "memory.db"
    database.parent.mkdir(parents=True)

    with closing(sqlite3.connect(database)) as live_connection:
        live_connection.execute("PRAGMA journal_mode=WAL")
        live_connection.execute("CREATE TABLE facts (value TEXT)")
        live_connection.execute("INSERT INTO facts VALUES ('before')")
        live_connection.commit()
        snapshot = engine.create_snapshot("core")

        live_connection.execute("UPDATE facts SET value = 'after'")
        live_connection.commit()
        extra = database.parent / "extra.txt"
        extra.write_text("remove me", encoding="utf-8")

        original_replace = Path.replace

        def deny_memory_move(path: Path, target: Path) -> Path:
            if path == engine.data_root / "memory":
                raise PermissionError(5, "directory is in use")
            return original_replace(path, target)

        monkeypatch.setattr(Path, "replace", deny_memory_move)
        result = engine.restore_snapshot("core", snapshot["id"])

        assert live_connection.execute("SELECT value FROM facts").fetchone() == (
            "before",
        )
        assert not extra.exists()
        assert result["restart_required"] is True


def test_snapshot_retention_keeps_latest(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    source = engine.data_root / "config" / "value.txt"
    source.parent.mkdir(parents=True)

    created = []
    for value in ("one", "two", "three", "four"):
        source.write_text(value, encoding="utf-8")
        created.append(engine.create_snapshot("core")["id"])

    assert [item["id"] for item in engine.list_snapshots("core")] == list(
        reversed(created[-3:])
    )


def test_rejects_backup_directory_inside_backed_up_data(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()

    with pytest.raises(BackupError, match="inside a backed-up data directory"):
        BackupEngine(data_root, data_root / "memory" / "snapshots")


def test_restore_safety_snapshot_counts_toward_retention(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    source = engine.data_root / "config" / "value.txt"
    source.parent.mkdir(parents=True)

    snapshots = []
    for value in ("one", "two", "three"):
        source.write_text(value, encoding="utf-8")
        snapshots.append(engine.create_snapshot("core"))

    result = engine.restore_snapshot("core", snapshots[0]["id"])
    remaining = {item["id"] for item in engine.list_snapshots("core")}

    assert len(remaining) == 3
    assert snapshots[0]["id"] in remaining
    assert result["safety_snapshot"] in remaining


def test_restore_empty_group_without_safety_snapshot(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    source = engine.data_root / "config" / "value.txt"
    source.parent.mkdir(parents=True)
    source.write_text("recover me", encoding="utf-8")
    snapshot = engine.create_snapshot("core")
    shutil.rmtree(source.parent)

    result = engine.restore_snapshot("core", snapshot["id"])

    assert source.read_text(encoding="utf-8") == "recover me"
    assert result["safety_snapshot"] is None


def test_restore_rejects_symbolic_link_group_root(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    source_root = engine.data_root / "config"
    source_root.mkdir()
    (source_root / "value.txt").write_text("snapshot", encoding="utf-8")
    snapshot = engine.create_snapshot("core")
    shutil.rmtree(source_root)

    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "value.txt"
    outside_file.write_text("outside", encoding="utf-8")
    try:
        source_root.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")

    with pytest.raises(BackupError, match="symbolic-link backup roots"):
        engine.restore_snapshot("core", snapshot["id"])
    assert outside_file.read_text(encoding="utf-8") == "outside"


def test_unchanged_files_are_hard_linked_when_supported(tmp_path: Path) -> None:
    probe = tmp_path / "hard-link-probe"
    probe_link = tmp_path / "hard-link-probe-link"
    probe.write_text("probe", encoding="utf-8")
    try:
        os.link(probe, probe_link)
    except (OSError, NotImplementedError, AttributeError) as exc:
        pytest.skip(f"filesystem does not support hard links: {exc}")

    engine = _engine(tmp_path)
    source = engine.data_root / "config" / "same.txt"
    source.parent.mkdir(parents=True)
    source.write_text("same", encoding="utf-8")
    first = engine.create_snapshot("core")
    second = engine.create_snapshot("core")
    first_file = (
        engine.backup_root / "core" / first["id"] / "files" / "config" / "same.txt"
    )
    second_file = (
        engine.backup_root / "core" / second["id"] / "files" / "config" / "same.txt"
    )

    if os.stat(first_file).st_ino == 0:
        pytest.skip("filesystem does not expose inode identifiers")
    assert os.path.samefile(first_file, second_file)
    assert not stat.S_IMODE(first_file.stat().st_mode) & stat.S_IWRITE
    assert not stat.S_IMODE(second_file.stat().st_mode) & stat.S_IWRITE


def test_rejects_unknown_group_and_snapshot_traversal(tmp_path: Path) -> None:
    engine = _engine(tmp_path)

    with pytest.raises(BackupError, match="unknown backup group"):
        engine.create_snapshot("../config")
    with pytest.raises(BackupError, match="invalid snapshot id"):
        engine.delete_snapshot("core", "../../outside")


def test_restore_rejects_tampered_snapshot(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    source = engine.data_root / "config" / "value.txt"
    source.parent.mkdir(parents=True)
    source.write_text("original", encoding="utf-8")
    snapshot = engine.create_snapshot("core")
    archived = (
        engine.backup_root / "core" / snapshot["id"] / "files" / "config" / "value.txt"
    )
    archived.chmod(stat.S_IMODE(archived.stat().st_mode) | stat.S_IWRITE)
    archived.write_text("tampered", encoding="utf-8")

    with pytest.raises(BackupError, match="checksum mismatch"):
        engine.restore_snapshot("core", snapshot["id"])
    assert source.read_text(encoding="utf-8") == "original"


def test_restore_rejects_unlisted_snapshot_file(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    source = engine.data_root / "config" / "value.txt"
    source.parent.mkdir(parents=True)
    source.write_text("original", encoding="utf-8")
    snapshot = engine.create_snapshot("core")
    injected = (
        engine.backup_root / "core" / snapshot["id"] / "files" / "config" / "extra.txt"
    )
    injected.write_text("unexpected", encoding="utf-8")

    with pytest.raises(BackupError, match="do not match"):
        engine.restore_snapshot("core", snapshot["id"])


def test_new_snapshot_does_not_link_tampered_previous_file(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    source = engine.data_root / "config" / "value.txt"
    source.parent.mkdir(parents=True)
    source.write_text("original", encoding="utf-8")
    first = engine.create_snapshot("core")
    first_file = (
        engine.backup_root / "core" / first["id"] / "files" / "config" / "value.txt"
    )
    first_file.chmod(stat.S_IMODE(first_file.stat().st_mode) | stat.S_IWRITE)
    first_file.write_text("tampered", encoding="utf-8")

    second = engine.create_snapshot("core")
    second_file = (
        engine.backup_root / "core" / second["id"] / "files" / "config" / "value.txt"
    )

    assert second_file.read_text(encoding="utf-8") == "original"
    assert not os.path.samefile(first_file, second_file)


def test_sqlite_snapshot_includes_uncheckpointed_wal_data(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    database = engine.data_root / "memory" / "cat" / "time_indexed.db"
    database.parent.mkdir(parents=True)

    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE facts (value TEXT)")
        connection.execute("INSERT INTO facts VALUES ('remember me')")
        connection.commit()
        snapshot = engine.create_snapshot("core")

    archived = (
        engine.backup_root
        / "core"
        / snapshot["id"]
        / "files"
        / "memory"
        / "cat"
        / "time_indexed.db"
    )
    assert not archived.with_name("time_indexed.db-wal").exists()
    with closing(sqlite3.connect(archived)) as connection:
        assert connection.execute("SELECT value FROM facts").fetchone() == (
            "remember me",
        )


def test_prune_failure_does_not_report_snapshot_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(tmp_path)
    source = engine.data_root / "config" / "value.txt"
    source.parent.mkdir(parents=True)
    snapshots = []
    for value in ("one", "two", "three"):
        source.write_text(value, encoding="utf-8")
        snapshots.append(engine.create_snapshot("core"))

    oldest = engine.backup_root / "core" / snapshots[0]["id"]
    original_rmtree = shutil.rmtree

    def deny_oldest_cleanup(path: Path, *args, **kwargs) -> None:
        if Path(path) == oldest:
            raise PermissionError(5, "snapshot is in use")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", deny_oldest_cleanup)
    source.write_text("four", encoding="utf-8")

    created = engine.create_snapshot("core")

    assert created["id"] in {item["id"] for item in engine.list_snapshots("core")}
    assert created["warnings"]
    assert oldest.exists()
    status = engine.status()
    assert status["groups"]["core"]["retention_exceeded"] is True
    assert status["warnings"]


def test_restore_reports_incomplete_rollback_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(tmp_path)
    source = engine.data_root / "config" / "value.txt"
    source.parent.mkdir(parents=True)
    source.write_text("before", encoding="utf-8")
    snapshot = engine.create_snapshot("core")
    source.write_text("after", encoding="utf-8")
    original_replace = Path.replace

    def fail_install_and_rollback(path: Path, target: Path) -> Path:
        parent_name = path.parent.name
        if parent_name.startswith(".data-backup-restore-"):
            raise OSError("install failed")
        if parent_name.startswith(".data-backup-old-"):
            raise OSError("rollback failed")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_install_and_rollback)

    with pytest.raises(BackupError, match=r"\.data-backup-old-.*rollback failed"):
        engine.restore_snapshot("core", snapshot["id"])

    assert list(engine.data_root.glob(".data-backup-old-*/config/value.txt"))


@pytest.mark.asyncio
async def test_directory_switch_waits_for_running_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(tmp_path)
    source = engine.data_root / "config" / "value.txt"
    source.parent.mkdir(parents=True)
    source.write_text("value", encoding="utf-8")
    started = threading.Event()
    release = threading.Event()
    original_create = engine.create_snapshot

    def slow_create(group: str, **kwargs):
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test did not release snapshot")
        return original_create(group, **kwargs)

    class Config:
        async def set(self, *_args, **_kwargs) -> None:
            return None

    plugin = object.__new__(DataBackupPlugin)
    plugin._engine = engine
    plugin._operation_lock = asyncio.Lock()
    plugin._schedule_lock = threading.RLock()
    plugin._schedule = ScheduleState()
    plugin._schedule_running = False
    plugin.config = Config()
    plugin.data_path = lambda *parts: tmp_path.joinpath("plugin-data", *parts)
    monkeypatch.setattr(engine, "create_snapshot", slow_create)

    create_task = asyncio.create_task(plugin.backup_create("core"))
    assert await asyncio.to_thread(started.wait, 2)
    new_root = tmp_path / "new-backups"
    switch_task = asyncio.create_task(plugin.backup_set_directory(str(new_root)))
    await asyncio.sleep(0.05)
    try:
        assert switch_task.done() is False
        assert plugin._engine is engine
    finally:
        release.set()

    await create_task
    await switch_task
    assert plugin._engine is not engine
    assert plugin._engine.backup_root == new_root.resolve(strict=False)


@pytest.mark.asyncio
async def test_scheduled_backup_persists_snapshot_warnings(tmp_path: Path) -> None:
    class WarningEngine:
        def create_snapshot(self, group: str) -> dict:
            return {
                "id": f"snapshot-{group}",
                "warnings": ["retention cleanup delayed"],
            }

    class Config:
        saved: dict | None = None

        async def set(self, _key: str, value: dict, **_kwargs) -> None:
            self.saved = value

    plugin = object.__new__(DataBackupPlugin)
    plugin._engine = WarningEngine()
    plugin._operation_lock = asyncio.Lock()
    plugin._schedule_lock = threading.RLock()
    plugin._schedule_running = False
    plugin._schedule_revision = 0
    plugin._schedule = ScheduleState(
        enabled=True,
        interval_days=7,
        groups=("core",),
        next_run_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    )
    plugin.config = Config()

    await plugin.scheduled_backup()

    assert plugin._schedule.last_warning == "retention cleanup delayed"
    assert plugin.config.saved is not None
    assert plugin.config.saved["last_warning"] == "retention cleanup delayed"


def test_data_backup_ui_surfaces_snapshot_and_schedule_warnings() -> None:
    plugin_dir = Path(__file__).parents[3] / "plugins" / "data_backup"
    script = (plugin_dir / "static" / "main.js").read_text(encoding="utf-8")
    styles = (plugin_dir / "static" / "style.css").read_text(encoding="utf-8")

    assert "schedule.last_warning" in script
    assert "warningText(snapshot)" in script
    assert "warningText(result)" in script
    assert ".notice.warning" in styles
