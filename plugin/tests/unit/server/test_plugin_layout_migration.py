from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.server.application.plugins import layout_migration as migration_module
from plugin.server.application.plugins.layout_migration import (
    LAYOUT_LEDGER_FILENAME,
    migrate_legacy_plugin_layout,
)
from plugin.settings import PLUGIN_EXEC_STATE_ROOT_COLLISION

pytestmark = pytest.mark.plugin_unit


def _write_plugin(root: Path, plugin_id: str, *, manifest_id: str | None = None) -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True)
    declared_id = manifest_id or plugin_id
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            (
                "[plugin]",
                f'id = "{declared_id}"',
                f'entry = "plugin.plugins.{declared_id}:Plugin"',
                'version = "1.0.0"',
                "",
            )
        ),
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text("class Plugin:\n    pass\n", encoding="utf-8")
    return plugin_dir


def test_reparse_point_is_detected_without_path_is_junction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reparse_attribute = 0x400
    monkeypatch.setattr(
        migration_module.stat,
        "FILE_ATTRIBUTE_REPARSE_POINT",
        reparse_attribute,
        raising=False,
    )
    path = SimpleNamespace(
        lstat=lambda: SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=reparse_attribute,
        )
    )

    assert migration_module._is_link_or_junction(path) is True


@pytest.mark.asyncio
async def test_migration_is_atomic_idempotent_and_does_not_resurrect(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "user" / "plugins"
    exec_root = tmp_path / "user" / ".neko-plugin-installations" / "plugins"
    builtin_root = tmp_path / "builtin" / "plugins"
    source = _write_plugin(state_root, "study_companion")
    database = source / "data" / "study.db"
    database.parent.mkdir()
    database.write_bytes(b"active database bytes")
    wal = source / "data" / "study.db-wal"
    shm = source / "data" / "study.db-shm"
    wal.write_bytes(b"active wal bytes")
    shm.write_bytes(b"active shm bytes")
    (source / "config").mkdir()
    (source / "config" / "settings.json").write_text("{}", encoding="utf-8")
    (source / "cache").mkdir()
    (source / "cache" / "temporary.bin").write_bytes(b"cache")
    state_files = (database, wal, shm)
    before_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in state_files
    }
    (source / "static" / "index.html").parent.mkdir()
    (source / "static" / "index.html").write_text("ok", encoding="utf-8")

    first = await migrate_legacy_plugin_layout(
        state_root=state_root,
        exec_root=exec_root,
        builtin_root=builtin_root,
    )

    assert first.migrated == ("study_companion",)
    assert not first.blocked
    destination = exec_root / "study_companion"
    assert (destination / "plugin.toml").is_file()
    assert (destination / "static" / "index.html").read_text(encoding="utf-8") == "ok"
    assert not (destination / "data").exists()
    assert not (destination / "config").exists()
    assert not (destination / "cache").exists()
    assert source.is_dir()
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in state_files
    } == before_hashes
    ledger_path = state_root.parent / LAYOUT_LEDGER_FILENAME
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["entries"][0]["plugin_id"] == "study_companion"
    assert ledger["entries"][0]["old_path"] == str(source.resolve())
    assert ledger["entries"][0]["new_path"] == str(destination.resolve())
    assert len(ledger["entries"][0]["manifest_sha256"]) == 64

    second = await migrate_legacy_plugin_layout(
        state_root=state_root,
        exec_root=exec_root,
        builtin_root=builtin_root,
    )
    assert second.migrated == ()
    assert second.skipped == ("study_companion",)

    shutil.rmtree(destination)
    third = await migrate_legacy_plugin_layout(
        state_root=state_root,
        exec_root=exec_root,
        builtin_root=builtin_root,
    )
    assert third.migrated == ()
    assert third.skipped == ("study_companion",)
    assert not destination.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("state_name", ["Config", "Data", "Cache"])
async def test_migration_excludes_state_directories_case_insensitively(
    tmp_path: Path,
    state_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "plugins"
    source = _write_plugin(state_root, "study_companion")
    state_file = source / state_name / "state.sqlite"
    state_file.parent.mkdir()
    state_file.write_bytes(b"persistent-state")
    exec_root = tmp_path / "exec"
    monkeypatch.setattr(migration_module.os.path, "samefile", lambda *_paths: True)

    result = await migrate_legacy_plugin_layout(
        state_root=state_root,
        exec_root=exec_root,
        builtin_root=tmp_path / "builtin",
    )

    assert result.migrated == ("study_companion",)
    assert state_file.read_bytes() == b"persistent-state"
    assert not (exec_root / "study_companion" / state_name).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("asset_name", ["Config", "Data", "Cache"])
async def test_migration_preserves_case_distinct_directories_on_sensitive_filesystems(
    tmp_path: Path,
    asset_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "plugins"
    source = _write_plugin(state_root, "study_companion")
    asset = source / asset_name / "asset.bin"
    asset.parent.mkdir()
    asset.write_bytes(b"package-asset")
    monkeypatch.setattr(migration_module.os.path, "samefile", lambda *_paths: False)

    result = await migrate_legacy_plugin_layout(
        state_root=state_root,
        exec_root=tmp_path / "exec",
        builtin_root=tmp_path / "builtin",
    )

    assert result.migrated == ("study_companion",)
    assert (tmp_path / "exec" / "study_companion" / asset_name / "asset.bin").read_bytes() == b"package-asset"


@pytest.mark.asyncio
@pytest.mark.parametrize("asset_name", ["config", "data", "cache"])
async def test_migration_preserves_same_named_regular_files(
    tmp_path: Path,
    asset_name: str,
) -> None:
    state_root = tmp_path / "plugins"
    source = _write_plugin(state_root, "study_companion")
    (source / asset_name).write_bytes(b"package-asset")

    result = await migrate_legacy_plugin_layout(
        state_root=state_root,
        exec_root=tmp_path / "exec",
        builtin_root=tmp_path / "builtin",
    )

    assert result.migrated == ("study_companion",)
    assert (tmp_path / "exec" / "study_companion" / asset_name).read_bytes() == b"package-asset"


@pytest.mark.asyncio
async def test_shared_ledger_scopes_migrations_to_each_execution_root(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "user" / "plugins"
    source = _write_plugin(state_root, "study_companion")
    ledger_path = state_root.parent / LAYOUT_LEDGER_FILENAME
    first_exec_root = tmp_path / "exec-a"
    second_exec_root = tmp_path / "exec-b"
    builtin_root = tmp_path / "builtin"

    first = await migrate_legacy_plugin_layout(
        state_root=state_root,
        exec_root=first_exec_root,
        ledger_path=ledger_path,
        builtin_root=builtin_root,
    )
    second = await migrate_legacy_plugin_layout(
        state_root=state_root,
        exec_root=second_exec_root,
        ledger_path=ledger_path,
        builtin_root=builtin_root,
    )

    assert first.migrated == ("study_companion",)
    assert second.migrated == ("study_companion",)
    assert (first_exec_root / "study_companion" / "plugin.toml").is_file()
    assert (second_exec_root / "study_companion" / "plugin.toml").is_file()
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert {entry["new_path"] for entry in ledger["entries"]} == {
        str((first_exec_root / "study_companion").resolve()),
        str((second_exec_root / "study_companion").resolve()),
    }
    assert source.is_dir()


@pytest.mark.asyncio
async def test_pure_state_directory_is_not_migrated(tmp_path: Path) -> None:
    state_root = tmp_path / "plugins"
    state_dir = state_root / "state_only"
    for name in ("config", "data", "cache"):
        (state_dir / name).mkdir(parents=True)
    exec_root = tmp_path / "exec" / "plugins"

    result = await migrate_legacy_plugin_layout(state_root=state_root, exec_root=exec_root)

    assert result.migrated == ()
    assert result.blocked == ()
    assert not (exec_root / "state_only").exists()


@pytest.mark.asyncio
async def test_manifest_id_mismatch_is_blocked(tmp_path: Path) -> None:
    state_root = tmp_path / "plugins"
    _write_plugin(state_root, "directory_name", manifest_id="different_id")
    exec_root = tmp_path / "exec"

    result = await migrate_legacy_plugin_layout(state_root=state_root, exec_root=exec_root)

    assert result.migrated == ()
    assert [issue.code for issue in result.blocked] == ["PLUGIN_LAYOUT_MIGRATION_ID_MISMATCH"]
    assert not (exec_root / "directory_name").exists()


@pytest.mark.asyncio
async def test_invalid_entry_is_blocked(tmp_path: Path) -> None:
    state_root = tmp_path / "plugins"
    source = _write_plugin(state_root, "broken")
    (source / "plugin.toml").write_text(
        '[plugin]\nid = "broken"\nentry = "plugins.broken.missing:Plugin"\n',
        encoding="utf-8",
    )

    result = await migrate_legacy_plugin_layout(state_root=state_root, exec_root=tmp_path / "exec")

    assert [issue.code for issue in result.blocked] == ["PLUGIN_LAYOUT_MIGRATION_ENTRY_INVALID"]


@pytest.mark.asyncio
@pytest.mark.parametrize("plugin_id", ["my-plugin", "123plugin"])
async def test_migration_accepts_supported_non_identifier_plugin_ids(
    tmp_path: Path,
    plugin_id: str,
) -> None:
    state_root = tmp_path / "plugins"
    _write_plugin(state_root, plugin_id)
    exec_root = tmp_path / "exec"

    result = await migrate_legacy_plugin_layout(
        state_root=state_root,
        exec_root=exec_root,
    )

    assert result.migrated == (plugin_id,)
    assert result.blocked == ()
    assert (exec_root / plugin_id / "__init__.py").is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["main:Plugin", "broken:Plugin", "broken.main:Plugin"])
async def test_package_local_entry_is_blocked_before_migration(
    tmp_path: Path,
    entry: str,
) -> None:
    state_root = tmp_path / "plugins"
    source = _write_plugin(state_root, "broken")
    (source / "plugin.toml").write_text(
        f'[plugin]\nid = "broken"\nentry = "{entry}"\n',
        encoding="utf-8",
    )
    (source / "main.py").write_text("class Plugin: pass\n", encoding="utf-8")
    exec_root = tmp_path / "exec"

    result = await migrate_legacy_plugin_layout(state_root=state_root, exec_root=exec_root)

    assert result.migrated == ()
    assert [issue.code for issue in result.blocked] == ["PLUGIN_LAYOUT_MIGRATION_ENTRY_INVALID"]
    assert source.is_dir()
    assert not (exec_root / "broken").exists()


@pytest.mark.asyncio
async def test_stale_staging_is_cleaned_on_next_start(tmp_path: Path) -> None:
    state_root = tmp_path / "plugins"
    exec_root = tmp_path / "exec"
    profiles_root = tmp_path / "profiles"
    tokens = tuple(char * 32 for char in "abcdef0")
    stale_paths = (
        exec_root / f".neko-layout-v1-old-plugin-{tokens[0]}.staging",
        exec_root / f".neko_override_staging_{tokens[1]}",
        exec_root / f".neko_override_unpack_{tokens[2]}",
        exec_root / f".neko_staging_{tokens[3]}",
        profiles_root / f".neko_override_staging_{tokens[4]}",
        profiles_root / f".neko_override_unpack_{tokens[5]}",
        profiles_root / f".neko_staging_{tokens[6]}",
    )
    for stale in stale_paths:
        stale.mkdir(parents=True)
        (stale / "partial").write_text("partial", encoding="utf-8")

    preserved_paths = (
        exec_root / ".neko_override_staging_backup",
        exec_root / ".neko_override_unpack_saved",
        exec_root / ".neko_staging_keep",
        profiles_root / ".neko_override_staging_backup",
        profiles_root / ".neko_override_unpack_saved",
        profiles_root / ".neko_staging_keep",
        # Layout migration staging is never generated in the package-profile
        # root, even when its name otherwise has a valid migration token.
        profiles_root / f".neko-layout-v1-valid-package-{tokens[0]}.staging",
    )
    for preserved in preserved_paths:
        preserved.mkdir(parents=True)
        (preserved / "user-data").write_text("keep", encoding="utf-8")

    result = await migrate_legacy_plugin_layout(
        state_root=state_root,
        exec_root=exec_root,
        profiles_root=profiles_root,
    )

    assert set(result.cleaned_staging) == {str(path.resolve()) for path in stale_paths}
    assert all(not path.exists() for path in stale_paths)
    assert all((path / "user-data").read_text(encoding="utf-8") == "keep" for path in preserved_paths)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["is_dir", "rmtree"])
async def test_staging_cleanup_failure_does_not_block_other_cleanup_or_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    state_root = tmp_path / "plugins"
    exec_root = tmp_path / "exec"
    profiles_root = tmp_path / "profiles"
    _write_plugin(state_root, "legacy")
    locked = exec_root / f".neko_override_staging_{'a' * 32}"
    other_exec = exec_root / f".neko_override_unpack_{'b' * 32}"
    other_profile = profiles_root / f".neko_staging_{'c' * 32}"
    for path in (locked, other_exec, other_profile):
        path.mkdir(parents=True)
        (path / "partial").write_text("partial", encoding="utf-8")

    original_is_dir = Path.is_dir
    original_rmtree = shutil.rmtree

    def sharing_violation(path: Path) -> PermissionError:
        return PermissionError(32, "Windows sharing violation", str(path))

    if failure_point == "is_dir":

        def fail_locked_is_dir(path: Path) -> bool:
            if path == locked:
                raise sharing_violation(path)
            return original_is_dir(path)

        monkeypatch.setattr(Path, "is_dir", fail_locked_is_dir)
    else:

        def fail_locked_rmtree(path: str | Path, *args: object, **kwargs: object) -> None:
            if Path(path) == locked:
                raise sharing_violation(Path(path))
            original_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(shutil, "rmtree", fail_locked_rmtree)

    result = await migrate_legacy_plugin_layout(
        state_root=state_root,
        exec_root=exec_root,
        profiles_root=profiles_root,
    )

    cleanup_issues = [
        issue
        for issue in result.blocked
        if issue.code == "PLUGIN_LAYOUT_MIGRATION_STAGING_CLEANUP_FAILED"
    ]
    assert [(issue.path, "Windows sharing violation" in issue.message) for issue in cleanup_issues] == [
        (str(locked), True)
    ]
    assert original_is_dir(locked)
    assert not other_exec.exists()
    assert not other_profile.exists()
    assert result.migrated == ("legacy",)
    assert (exec_root / "legacy" / "plugin.toml").is_file()


@pytest.mark.asyncio
async def test_unreadable_state_root_is_reported_after_staging_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "plugins"
    state_root.mkdir()
    exec_root = tmp_path / "exec"
    stale = exec_root / f".neko_override_staging_{'a' * 32}"
    stale.mkdir(parents=True)
    original_iterdir = Path.iterdir

    def fail_state_root_iterdir(path: Path):
        if path == state_root:
            raise PermissionError(13, "access denied", str(path))
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_state_root_iterdir)

    result = await migrate_legacy_plugin_layout(
        state_root=state_root,
        exec_root=exec_root,
    )

    assert result.migrated == ()
    assert [(issue.code, issue.path) for issue in result.blocked] == [
        ("PLUGIN_LAYOUT_MIGRATION_IO_FAILED", str(state_root.resolve()))
    ]
    assert result.cleaned_staging == (str(stale.resolve()),)
    assert not stale.exists()


@pytest.mark.asyncio
async def test_exec_state_collision_fails_closed_without_writes(tmp_path: Path) -> None:
    shared_root = tmp_path / "plugins"
    _write_plugin(shared_root, "legacy")

    result = await migrate_legacy_plugin_layout(
        state_root=shared_root,
        exec_root=shared_root,
    )

    assert [issue.code for issue in result.blocked] == [PLUGIN_EXEC_STATE_ROOT_COLLISION]
    assert not (tmp_path / LAYOUT_LEDGER_FILENAME).exists()
    assert not list(shared_root.glob(".neko-layout-v1-*.staging"))


@pytest.mark.asyncio
@pytest.mark.parametrize("writable_kind", ["exec", "profiles"])
@pytest.mark.parametrize("relation", ["equal", "writable_child", "builtin_child"])
async def test_migration_rejects_writable_roots_colliding_with_builtin_before_writes(
    tmp_path: Path,
    writable_kind: str,
    relation: str,
) -> None:
    state_root = tmp_path / "state" / "plugins"
    _write_plugin(state_root, "legacy")
    shared = tmp_path / "managed"
    if relation == "equal":
        writable_root = builtin_root = shared
    elif relation == "writable_child":
        builtin_root, writable_root = shared, shared / "writable"
    else:
        writable_root, builtin_root = shared, shared / "builtin"
    builtin_plugin = _write_plugin(builtin_root, "builtin")
    exec_root = writable_root if writable_kind == "exec" else tmp_path / "exec"
    profiles_root = writable_root if writable_kind == "profiles" else tmp_path / "profiles"
    ledger_path = tmp_path / "layout-ledger.json"

    result = await migrate_legacy_plugin_layout(
        state_root=state_root,
        exec_root=exec_root,
        profiles_root=profiles_root,
        builtin_root=builtin_root,
        ledger_path=ledger_path,
    )

    assert [issue.code for issue in result.blocked] == [PLUGIN_EXEC_STATE_ROOT_COLLISION]
    assert not (exec_root / "legacy").exists()
    assert (builtin_plugin / "plugin.toml").is_file()
    assert not ledger_path.exists()
    assert not list(shared.rglob(".neko-layout-v1-*.staging"))


@pytest.mark.asyncio
async def test_migration_blocks_legacy_plugin_that_collides_with_builtin(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state" / "plugins"
    source = _write_plugin(state_root, "study_companion")
    exec_root = tmp_path / "user" / "plugins"
    builtin_root = tmp_path / "builtin" / "plugins"
    builtin = _write_plugin(builtin_root, "study_companion")
    ledger_path = tmp_path / "layout-ledger.json"

    result = await migrate_legacy_plugin_layout(
        state_root=state_root,
        exec_root=exec_root,
        builtin_root=builtin_root,
        ledger_path=ledger_path,
    )

    assert result.migrated == ()
    assert [issue.code for issue in result.blocked] == [
        "PLUGIN_LAYOUT_MIGRATION_BUILTIN_CONFLICT"
    ]
    assert source.is_dir()
    assert (builtin / "plugin.toml").is_file()
    assert not (exec_root / "study_companion").exists()
    assert not ledger_path.exists()


@pytest.mark.asyncio
async def test_linked_legacy_tree_is_blocked_when_links_are_supported(tmp_path: Path) -> None:
    state_root = tmp_path / "plugins"
    source = _write_plugin(state_root, "linked")
    external = tmp_path / "external.txt"
    external.write_text("outside", encoding="utf-8")
    link = source / "outside.txt"
    try:
        os.symlink(external, link)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable for this test account")

    result = await migrate_legacy_plugin_layout(state_root=state_root, exec_root=tmp_path / "exec")

    assert [issue.code for issue in result.blocked] == ["PLUGIN_LAYOUT_MIGRATION_SYMLINK"]


@pytest.mark.asyncio
async def test_ledger_write_failure_removes_promoted_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "plugins"
    _write_plugin(state_root, "rollback_me")
    exec_root = tmp_path / "exec"

    def _fail_ledger_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(migration_module, "_atomic_write_ledger", _fail_ledger_write)

    result = await migrate_legacy_plugin_layout(state_root=state_root, exec_root=exec_root)

    assert [issue.code for issue in result.blocked] == [
        "PLUGIN_LAYOUT_MIGRATION_LEDGER_WRITE_FAILED"
    ]
    assert not (exec_root / "rollback_me").exists()
    assert not list(exec_root.glob(".neko-layout-v1-*.staging"))


@pytest.mark.asyncio
async def test_invalid_ledger_blocks_migration_without_overwrite(tmp_path: Path) -> None:
    state_root = tmp_path / "plugins"
    _write_plugin(state_root, "legacy")
    ledger = state_root.parent / LAYOUT_LEDGER_FILENAME
    ledger.write_text("not-json", encoding="utf-8")
    exec_root = tmp_path / "exec"

    result = await migrate_legacy_plugin_layout(state_root=state_root, exec_root=exec_root)

    assert [issue.code for issue in result.blocked] == [
        "PLUGIN_LAYOUT_MIGRATION_LEDGER_INVALID"
    ]
    assert ledger.read_text(encoding="utf-8") == "not-json"
    assert not (exec_root / "legacy").exists()
