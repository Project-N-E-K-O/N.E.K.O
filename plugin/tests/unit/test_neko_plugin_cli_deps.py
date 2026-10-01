"""Unit tests for the neko-plugin sync command."""

from __future__ import annotations

import errno
import os
import subprocess
from pathlib import Path
from unittest.mock import patch
import sys

import pytest

from plugin.neko_plugin_cli.commands.deps_cmd import (
    _clean_vendor,
    _filter_external,
    _find_uv as real_find_uv,
    _lock_dir as real_lock_dir,
    _windows_known_folder as real_windows_known_folder,
    _read_dependencies,
    _replace_vendor,
    handle_sync,
)


@pytest.fixture(autouse=True)
def _private_lock_dir(monkeypatch, tmp_path_factory):
    """Keep sync locks out of the real home/temp; tests that fake another
    uid would otherwise hit the real lock dir's ownership check."""
    from plugin.neko_plugin_cli.commands import deps_cmd

    locks = tmp_path_factory.mktemp("locks")
    monkeypatch.setattr(deps_cmd, "_lock_dir", lambda: locks)


@pytest.fixture(autouse=True)
def _no_host_installer_settings(monkeypatch):
    """Keep the developer's own uv/pip settings, and whether uv is on this
    machine, out of these tests: uv is "found" unless a test says otherwise."""
    from plugin.neko_plugin_cli.commands import deps_cmd

    for name in list(os.environ):
        if name.upper() == "UV" or name.upper().startswith(("PIP_", "UV_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(deps_cmd, "_find_uv", lambda: "uv")


@pytest.mark.parametrize("uv_found", [True, False])
@pytest.mark.parametrize("error_type", [FileNotFoundError, PermissionError])
def test_installer_start_failure_is_reported(tmp_path, monkeypatch, capsys, uv_found, error_type):
    from plugin.neko_plugin_cli.commands import deps_cmd

    if not uv_found:
        monkeypatch.setattr(deps_cmd, "_find_uv", lambda: None)

    def run(command, **kwargs):
        raise error_type("installer cannot execute")

    monkeypatch.setattr(deps_cmd.subprocess, "run", run)
    assert deps_cmd._install_to_vendor(
        ["httpx"], vendor_dir=tmp_path / "vendor", python="missing-python",
    ) == 1
    error = capsys.readouterr().err
    label = "uv pip install" if uv_found else "pip install"
    assert f"{label} could not start" in error
    assert "installer cannot execute" in error


def test_uv_from_uv_run_is_preferred_over_path(tmp_path, monkeypatch):
    # `uv run` exports UV; uv may not be on PATH (pipx, `py -m uv`).
    from plugin.neko_plugin_cli.commands import deps_cmd

    uv_exe = str(tmp_path / "tools" / "uv.exe")
    monkeypatch.setenv("UV", uv_exe)
    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: name if name == uv_exe else None)

    assert real_find_uv() == uv_exe


@pytest.mark.parametrize("uv_value", [None, "uv", "/no/such/uv"])
def test_bare_or_missing_uv_value_falls_back_to_path(tmp_path, monkeypatch, uv_value):
    from plugin.neko_plugin_cli.commands import deps_cmd

    on_path = str(tmp_path / "bin" / "uv.exe")
    if uv_value is not None:
        monkeypatch.setenv("UV", uv_value)
    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: on_path if name == "uv" else None)

    assert real_find_uv() == on_path


def test_uv_not_found_anywhere(monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: None)
    assert real_find_uv() is None


def _cmd_name(command):
    """The program a command runs, without directory or .exe (uv is pinned
    to an absolute path before it runs)."""
    return Path(command[0]).stem


def test_overlapping_sync_rejected_across_processes(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    # The child process uses the real lock dir; so must this one.
    monkeypatch.setattr(deps_cmd, "_lock_dir", real_lock_dir)
    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    real_run = subprocess.run
    child_code = '''
import argparse, sys
from pathlib import Path
from plugin.neko_plugin_cli.commands.deps_cmd import handle_sync
from plugin.neko_plugin_cli.paths import CliDefaults
p = Path(sys.argv[1])
d = CliDefaults(plugin_root=p.parent, target_dir=p.parent / 'target',
                plugins_root=p.parent, profiles_root=p.parent / 'profiles')
sys.exit(handle_sync(argparse.Namespace(plugin=str(p), python=sys.executable,
                                       clean=True, _defaults=d)))
'''

    def install(command, **kwargs):
        # The first sync holds its OS lock while invoking the installer.
        # Run from the repo root so `import plugin` works wherever pytest runs.
        child = real_run([sys.executable, "-c", child_code, str(plugin_dir)],
                         capture_output=True, text=True, timeout=30,
                         cwd=Path(__file__).resolve().parents[3])
        assert child.returncode == 1
        assert "sync already in progress" in child.stderr, child.stderr
        target = Path(command[command.index("--target") + 1])
        (target / "fresh.py").write_text("complete")
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    monkeypatch.setattr(deps_cmd.subprocess, "run", install)
    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 0
    assert (plugin_dir / "vendor" / "fresh.py").read_text() == "complete"


def test_recovery_marker_write_failure_rolls_back_before_swap(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor"
    staging = tmp_path / ".vendor.staging-test"
    vendor.mkdir()
    staging.mkdir()
    (vendor / "old.py").write_text("keep")
    (staging / "fresh.py").write_text("new")

    real_touch = Path.touch

    def fail_marker(path, *args, **kwargs):
        if path.name.endswith(".pending"):
            raise PermissionError("marker is locked")
        return real_touch(path, *args, **kwargs)

    monkeypatch.setattr(Path, "touch", fail_marker)
    assert _replace_vendor(vendor, staging) is False

    assert (vendor / "old.py").read_text() == "keep"
    assert not (vendor / "fresh.py").exists()
    assert not list(tmp_path.glob(".vendor.backup-*"))


@pytest.mark.parametrize("discard_backups", [False, True])
def test_default_clean_never_discards_a_pending_backup(tmp_path, monkeypatch, capsys, discard_backups):
    # publish always syncs with clean=True; only an explicit `sync --clean`
    # (discard_backups) may drop the only complete copy of the old vendor/.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    (plugin_dir / "vendor").mkdir()
    backup = plugin_dir / ".vendor.backup-0000aaaa"
    backup.mkdir()
    (backup / "old.py").write_text("only copy")
    backup.with_name(backup.name + ".pending").touch()
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="ok"),
    )
    args = TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path, clean=True)
    args.discard_backups = discard_backups

    assert handle_sync(args) == (0 if discard_backups else 1)
    assert backup.exists() is (not discard_backups)
    if not discard_backups:
        assert "unreconciled dependency backup" in capsys.readouterr().err


@pytest.mark.parametrize("mid_swap", [False, True])
def test_success_cleanup_skips_only_another_users_live_swap(tmp_path, monkeypatch, mid_swap):
    # The lock is per user. A backup another user is mid-swap on (their
    # pending marker) is left alone; a finished leftover is cleaned whoever
    # owns it.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    (plugin_dir / "vendor").mkdir()
    theirs = plugin_dir / ".vendor.backup-0000ffff"
    theirs.mkdir()
    marker = theirs.with_name(theirs.name + ".pending")
    owner = theirs.stat().st_uid
    monkeypatch.setattr(deps_cmd.os, "getuid", lambda: owner + 1, raising=False)
    # Their swap starts only after this sync's preflight.
    monkeypatch.setattr(deps_cmd, "_unreconciled_backups", lambda *a: [])

    def install(command, **kwargs):
        if mid_swap:
            marker.touch()
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    monkeypatch.setattr(deps_cmd.subprocess, "run", install)

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 0
    assert theirs.exists() is mid_swap


def test_own_interrupted_swap_on_another_users_vendor_is_not_foreign(tmp_path, monkeypatch):
    # Renaming vendor/ keeps its owner (user A) on the backup; the syncing
    # user (B) is identified by the pending marker they created.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    backup = plugin_dir / ".vendor.backup-0000aaaa"
    backup.mkdir()
    (backup / "old.py").write_text("old")
    marker = backup.with_name(backup.name + ".pending")
    marker.touch()
    me = marker.stat().st_uid
    real_stat = Path.stat

    def stat_with_other_owner_for_backup(self, *args, **kwargs):
        st = real_stat(self, *args, **kwargs)
        if self == backup:
            fields = list(st[:10])
            fields[4] = me + 1  # st_uid
            return os.stat_result(fields)
        return st

    monkeypatch.setattr(Path, "stat", stat_with_other_owner_for_backup)
    monkeypatch.setattr(deps_cmd.os, "getuid", lambda: me, raising=False)
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="ok"),
    )

    # B's own explicit --clean may discard B's interrupted swap.
    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path, clean=True)) == 0
    assert not backup.exists()


def test_backup_is_marked_before_vendor_is_renamed(tmp_path, monkeypatch):
    # Otherwise another user's sync could see an unmarked live backup in the
    # window after the rename and delete it as a finished leftover.
    vendor = tmp_path / "vendor"
    staging = tmp_path / ".vendor.staging-0000abcd"
    vendor.mkdir()
    staging.mkdir()
    real_replace = Path.replace
    marked_at_rename = []

    def watch(source, target):
        if source == vendor:
            marked_at_rename.append(Path(str(target) + ".pending").is_file())
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", watch)
    assert _replace_vendor(vendor, staging) is True
    assert marked_at_rename == [True]
    assert not list(tmp_path.glob("*.pending"))


def test_failed_first_rename_leaves_no_marker(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor"
    staging = tmp_path / ".vendor.staging-0000abcd"
    vendor.mkdir()
    (vendor / "old.py").write_text("keep")
    staging.mkdir()
    real_replace = Path.replace

    def fail_vendor_rename(source, target):
        if source == vendor:
            raise PermissionError("vendor in use")
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_vendor_rename)
    assert _replace_vendor(vendor, staging) is False
    assert (vendor / "old.py").read_text() == "keep"
    assert not list(tmp_path.glob(".vendor.backup-*"))


def test_vendor_that_is_a_file_is_refused_before_any_change(tmp_path, monkeypatch, capsys):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    (plugin_dir / "vendor").write_text("not a directory")
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("installer must not run"),
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 1
    assert (plugin_dir / "vendor").read_text() == "not a directory"
    assert not list(plugin_dir.glob(".vendor.*"))
    assert "is not a directory" in capsys.readouterr().err


def test_swapped_by_other_user_tolerates_a_vanished_marker(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.setattr(deps_cmd.os, "getuid", lambda: 12345, raising=False)
    assert deps_cmd._swapped_by_other_user(tmp_path / ".vendor.backup-0000abcd") is False


@pytest.mark.parametrize("clean", [False, True])
def test_another_users_pending_backup_blocks_with_a_usable_hint(tmp_path, monkeypatch, capsys, clean):
    # --clean cannot clear it (it is never removed here), so the hint must
    # not send the user in a loop.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    (plugin_dir / "vendor").mkdir()
    theirs = plugin_dir / ".vendor.backup-0000ffff"
    theirs.mkdir()
    theirs.with_name(theirs.name + ".pending").touch()
    owner = theirs.stat().st_uid
    monkeypatch.setattr(deps_cmd.os, "getuid", lambda: owner + 1, raising=False)
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("installer must not run"),
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path, clean=clean)) == 1
    assert theirs.exists()
    error = capsys.readouterr().err
    assert "belongs to another user" in error
    assert "sync --clean" not in error


def test_lock_name_accepts_an_undecodable_plugin_path(tmp_path, monkeypatch):
    # A POSIX file name may hold bytes that decode to surrogate escapes;
    # strict UTF-8 encoding of the identity would raise before the lock.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = tmp_path / "plugin-\udcff"
    monkeypatch.setattr(
        deps_cmd, "resolve_plugin_dir_candidate", lambda plugin, defaults: plugin_dir
    )
    # Printing such a name is a separate matter (a strict UTF-8 stdout);
    # only the lock is under test here.
    printed = []
    monkeypatch.setattr(deps_cmd, "print", lambda *args, **kwargs: printed.append(args), raising=False)

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 0
    assert any("no external dependencies" in str(args[0]) for args in printed)
    assert list(deps_cmd._lock_dir().glob("neko-plugin-sync-*.lock"))


def test_swapped_backup_with_a_new_mount_is_not_deleted(tmp_path, monkeypatch):
    # vendor/ is checked for mounts before the install, but one can be added
    # before the swap; recheck right before deleting the swapped backup.
    from plugin.neko_plugin_cli.commands import deps_cmd

    vendor = tmp_path / "vendor"
    staging = tmp_path / ".vendor.staging-0000abcd"
    vendor.mkdir()
    (vendor / "old.py").write_text("old")
    staging.mkdir()
    monkeypatch.setattr(
        deps_cmd, "_mounted_inside", lambda path: path.name.startswith(".vendor.backup-")
    )

    assert _replace_vendor(vendor, staging) is True
    backup, = [p for p in tmp_path.glob(".vendor.backup-*") if p.is_dir()]
    assert (backup / "old.py").read_text() == "old"


def test_successful_retry_cleans_retained_backup(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    backup = plugin_dir / ".vendor.backup-0000aaaa"
    backup.mkdir()
    (backup / "old.py").write_text("backup")
    monkeypatch.setattr(deps_cmd.subprocess, "run",
                        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="ok"))
    assert handle_sync(
        TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path, clean=True)
    ) == 0
    assert not backup.exists()


def test_non_clean_retry_refuses_orphaned_backup(tmp_path, monkeypatch, capsys):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    backup = plugin_dir / ".vendor.backup-0000aaaa"
    backup.mkdir()
    (backup / "old.py").write_text("backup")
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("installer must not run before recovery"),
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 1
    assert backup.exists()
    assert "run `neko-plugin sync --clean` explicitly" in capsys.readouterr().err


def test_rollback_never_deletes_a_vendor_that_reappeared(tmp_path, monkeypatch, capsys):
    vendor = tmp_path / "vendor"
    staging = tmp_path / ".vendor.staging-test"
    vendor.mkdir()
    staging.mkdir()
    (vendor / "old.py").write_text("keep")

    real_replace = Path.replace

    def fail_staging_replace(source, target):
        if source == staging:
            target.mkdir(exist_ok=True)
            (target / "partial.py").write_text("partial")
            raise OSError("rename failed")
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_staging_replace)

    assert _replace_vendor(vendor, staging) is False
    backup, = [p for p in tmp_path.glob(".vendor.backup-*") if p.is_dir()]
    assert (backup / "old.py").read_text() == "keep"
    assert (vendor / "partial.py").read_text() == "partial"
    assert backup.with_name(backup.name + ".pending").is_file()
    assert "reappeared after the failed swap" in capsys.readouterr().err


def test_failed_rollback_rename_leaves_backup_that_blocks_retry(tmp_path, monkeypatch, capsys):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    staging = plugin_dir / ".vendor.staging-test"
    vendor.mkdir()
    staging.mkdir()
    (vendor / "old.py").write_text("keep")

    real_replace = Path.replace

    def fail_after_backup(source, target):
        if source == staging or source.name.startswith(".vendor.backup-"):
            raise PermissionError("locked")
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_after_backup)
    assert _replace_vendor(vendor, staging) is False
    monkeypatch.setattr(Path, "replace", real_replace)

    backup, = [p for p in plugin_dir.glob(".vendor.backup-*") if p.is_dir()]
    assert not vendor.exists()
    assert (backup / "old.py").read_text() == "keep"
    assert backup.with_name(backup.name + ".pending").is_file()
    assert "Could not roll back vendor" in capsys.readouterr().err
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("installer must not run before recovery"),
    )
    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 1
    # Still blocked when something recreates vendor/ before the retry.
    vendor.mkdir()
    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 1
    assert backup.exists()


@pytest.mark.parametrize("clean", [False, True])
def test_linked_vendor_is_synced_in_place(tmp_path, monkeypatch, capsys, clean):
    # Renaming would turn the link to another disk into a real directory;
    # install through it instead, as before the swap existed.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    real_vendor = tmp_path / "other_disk_vendor"
    real_vendor.mkdir()
    (real_vendor / "old.py").write_text("old")
    link = plugin_dir / "vendor"
    if sys.platform == "win32":
        # Junctions need no privilege, unlike symlinks.
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real_vendor)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(real_vendor, target_is_directory=True)
    targets = []

    def install(command, **kwargs):
        target = Path(command[command.index("--target") + 1])
        targets.append(target)
        (target / "fresh.py").write_text("new")
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    monkeypatch.setattr(deps_cmd.subprocess, "run", install)

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path, clean=clean)) == 0
    # --clean installs into staging first and refills vendor/ only on success;
    # the staging dir is inside vendor/, on the disk the link leads to.
    if clean:
        assert len(targets) == 1 and targets[0].name.startswith(".vendor.staging-")
        assert targets[0].parent == link
    else:
        assert targets == [link]
    assert not list(real_vendor.glob(".vendor.*"))
    assert os.readlink(link)
    assert (real_vendor / "fresh.py").read_text() == "new"
    assert (real_vendor / "old.py").exists() is (not clean)
    assert not list(plugin_dir.glob(".vendor.*"))
    assert "updating it in place" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions are Windows-only")
def test_non_clean_sync_refuses_nested_junction_before_copying(tmp_path, monkeypatch, capsys):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    (vendor / "pkg").mkdir(parents=True)
    external = tmp_path / "external_tree"
    external.mkdir()
    (external / "big.bin").write_text("external")
    junction = vendor / "pkg" / "linked"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(external)],
                   check=True, capture_output=True)
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("installer must not run"),
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 1
    assert os.readlink(junction)
    assert not list(plugin_dir.glob(".vendor.*"))
    assert "directory junction" in capsys.readouterr().err


def test_unusable_lock_dir_fails_with_a_message_not_a_traceback(tmp_path, monkeypatch, capsys):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)

    def taken():
        raise PermissionError("lock directory is owned by another user")

    monkeypatch.setattr(deps_cmd, "_lock_dir", taken)
    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 1
    assert "[FAIL] Could not sync dependencies" in capsys.readouterr().err


def test_lock_location_ignores_per_process_cache_settings(tmp_path, monkeypatch):
    # Two syncs by one user with different XDG_CACHE_HOME must still share
    # one lock, or both would run and one could delete the other's staging.
    from plugin.neko_plugin_cli.commands import deps_cmd

    import types

    account_home = tmp_path / "account-home"
    fake_pwd = types.SimpleNamespace(
        getpwuid=lambda uid: types.SimpleNamespace(pw_dir=str(account_home))
    )
    monkeypatch.setitem(sys.modules, "pwd", fake_pwd)
    monkeypatch.setattr(deps_cmd.os, "getuid", lambda: 1000, raising=False)
    # Per-process settings that must not move the lock.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "per-process-cache"))
    monkeypatch.setenv("HOME", str(tmp_path / "per-process-home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "per-process-home"))

    assert deps_cmd._lock_cache_base() == account_home / ".cache"


def test_uid_without_account_record_uses_the_shared_fallback(tmp_path, monkeypatch):
    # Containers often run a uid unknown to pwd; falling back to HOME would
    # let two processes with different HOME values take different locks.
    import types

    from plugin.neko_plugin_cli.commands import deps_cmd

    def unknown(uid):
        raise KeyError(uid)

    monkeypatch.setitem(sys.modules, "pwd", types.SimpleNamespace(getpwuid=unknown))
    me = tmp_path.stat().st_uid
    monkeypatch.setattr(deps_cmd.os, "getuid", lambda: me, raising=False)
    shared = tmp_path / "shared-tmp"
    shared.mkdir()
    monkeypatch.setattr(deps_cmd, "_shared_tmp", lambda: shared)

    locks = set()
    for home in ("home-a", "home-b"):
        monkeypatch.setenv("HOME", str(tmp_path / home))
        monkeypatch.setenv("USERPROFILE", str(tmp_path / home))
        locks.add(real_lock_dir())
    assert locks == {shared / f"neko-plugin-sync-{me}"}


@pytest.mark.parametrize("tmp_usable", [True, False])
def test_shared_tmp_prefers_a_usable_posix_tmp(tmp_path, monkeypatch, tmp_usable):
    from plugin.neko_plugin_cli.commands import deps_cmd

    other = tmp_path / "tmpdir"
    monkeypatch.setattr(deps_cmd, "gettempdir", lambda: str(other))
    real_is_dir = Path.is_dir
    monkeypatch.setattr(
        Path, "is_dir", lambda self: True if self == Path("/tmp") else real_is_dir(self)
    )
    monkeypatch.setattr(
        deps_cmd.os, "access", lambda path, mode: tmp_usable if Path(path) == Path("/tmp") else True
    )

    assert deps_cmd._shared_tmp() == (Path("/tmp") if tmp_usable else other)


def test_mount_found_in_finished_staging_stops_the_sync(tmp_path, monkeypatch, capsys):
    # _clean_vendor would recurse into it and the swap would expose it as
    # vendor/; keep staging as it is and leave vendor/ alone.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    (vendor / "old.py").write_text("old")
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="ok"),
    )
    monkeypatch.setattr(
        deps_cmd,
        "_find_mount",
        lambda path: path / "mnt" if path.name.startswith(".vendor.staging-") else None,
    )
    monkeypatch.setattr(
        deps_cmd, "_clean_vendor", lambda path: pytest.fail("must not clean a tree with a mount")
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path, clean=True)) == 1
    assert (vendor / "old.py").read_text() == "old"
    assert [p for p in plugin_dir.glob(".vendor.staging-*") if p.is_dir()]
    assert "got mounted inside" in capsys.readouterr().err


def test_posix_lock_dir_is_a_private_cache_dir(tmp_path, monkeypatch):
    # Not directly in a shared, sticky /tmp, where another user could
    # pre-create the lock file and block this user's syncs for good.
    from plugin.neko_plugin_cli.commands import deps_cmd

    cache = tmp_path / "cache"
    me = tmp_path.stat().st_uid
    monkeypatch.setattr(deps_cmd.os, "getuid", lambda: me, raising=False)
    monkeypatch.setattr(deps_cmd, "_lock_cache_base", lambda: cache)
    monkeypatch.setattr(deps_cmd, "_shared_tmp", lambda: tmp_path / "shared-tmp")

    assert real_lock_dir() == cache / "neko-plugin" / "sync-locks"


def test_posix_lock_dir_falls_back_to_a_per_user_temp_dir(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    unusable = tmp_path / "not-a-dir"
    unusable.write_text("x")  # e.g. a read-only or broken home cache
    shared = tmp_path / "shared-tmp"
    shared.mkdir()
    me = tmp_path.stat().st_uid
    monkeypatch.setattr(deps_cmd.os, "getuid", lambda: me, raising=False)
    monkeypatch.setattr(deps_cmd, "_lock_cache_base", lambda: unusable)
    monkeypatch.setattr(deps_cmd, "_shared_tmp", lambda: shared)

    assert real_lock_dir() == shared / f"neko-plugin-sync-{me}"


def test_posix_lock_dir_refuses_a_fallback_owned_by_someone_else(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    unusable = tmp_path / "not-a-dir"
    unusable.write_text("x")
    shared = tmp_path / "shared-tmp"
    shared.mkdir()
    owner = tmp_path.stat().st_uid
    # Another user pre-created our fallback dir.
    (shared / f"neko-plugin-sync-{owner + 1}").mkdir()
    monkeypatch.setattr(deps_cmd.os, "getuid", lambda: owner + 1, raising=False)
    monkeypatch.setattr(deps_cmd, "_lock_cache_base", lambda: unusable)
    monkeypatch.setattr(deps_cmd, "_shared_tmp", lambda: shared)

    with pytest.raises(PermissionError):
        real_lock_dir()


@pytest.mark.parametrize("as_dir", [False, True])
def test_package_data_named_like_a_marker_survives_sync(tmp_path, monkeypatch, as_dir):
    # The recovery marker lives beside the backup, never inside vendor/, so
    # a package's own top-level path of any name is left alone.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)

    def install(command, **kwargs):
        target = Path(command[command.index("--target") + 1])
        data = target / ".recovery-pending"
        if as_dir:
            data.mkdir()
            (data / "x").write_text("pkg")
        else:
            data.write_text("pkg")
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    monkeypatch.setattr(deps_cmd.subprocess, "run", install)
    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 0
    assert (plugin_dir / "vendor" / ".recovery-pending").exists()


def test_stale_staging_of_another_user_is_left_alone(tmp_path, monkeypatch):
    # The sync lock is per user, so another user's staging may be live.
    from plugin.neko_plugin_cli.commands import deps_cmd

    stale = tmp_path / ".vendor.staging-0000dddd"
    stale.mkdir()
    owner = stale.stat().st_uid
    monkeypatch.setattr(deps_cmd.os, "getuid", lambda: owner + 1, raising=False)
    deps_cmd._remove_stale_staging(tmp_path)
    assert stale.exists()
    monkeypatch.setattr(deps_cmd.os, "getuid", lambda: owner, raising=False)
    deps_cmd._remove_stale_staging(tmp_path)
    assert not stale.exists()


def test_parse_mountinfo_points_unescapes_octal():
    from plugin.neko_plugin_cli.commands.deps_cmd import _parse_mountinfo_points

    lines = [
        "22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw\n",
        "40 22 8:1 /data /srv/my\\040plugin/vendor/pkg rw - ext4 /dev/sda1 rw\n",
    ]
    assert _parse_mountinfo_points(lines) == ["/", "/srv/my plugin/vendor/pkg"]


def test_mountinfo_does_not_flag_vendor_itself_or_outside_mounts(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    vendor = tmp_path / "vendor"
    (vendor / "pkg").mkdir(parents=True)
    real = os.path.realpath(vendor)
    monkeypatch.setattr(deps_cmd.sys, "platform", "linux")
    monkeypatch.setattr(
        deps_cmd, "_linux_mount_points",
        lambda: ["/", real, real + "-sibling", os.path.dirname(real)],
    )
    assert deps_cmd._find_foreign_subdir(vendor, junctions=True) is None


def test_stale_staging_that_vanishes_is_skipped(tmp_path, monkeypatch, capsys):
    from plugin.neko_plugin_cli.commands import deps_cmd

    gone = tmp_path / ".vendor.staging-0000eeee"
    gone.mkdir()
    monkeypatch.setattr(deps_cmd.os, "getuid", lambda: 0, raising=False)
    real_stat = Path.stat

    calls = []

    def vanish(path, *args, **kwargs):
        # is_dir() still sees it; the ownership check right after does not.
        if path == gone and not kwargs.get("follow_symlinks", True) is False:
            calls.append(path)
            if len(calls) > 1:
                raise FileNotFoundError(errno.ENOENT, "moved away by another user's sync")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", vanish)
    deps_cmd._remove_stale_staging(tmp_path)  # must not raise
    assert len(calls) == 2  # the ownership check did hit the vanished dir
    assert capsys.readouterr().err == ""  # skipped quietly, not a cleanup failure


@pytest.mark.parametrize("detected_by", ["ismount", "mountinfo"])
@pytest.mark.parametrize("clean", [False, True])
def test_sync_refuses_mount_point_inside_vendor(tmp_path, monkeypatch, capsys, clean, detected_by):
    # Removing the old vendor/ backup would delete the mounted tree's files.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    mount = plugin_dir / "vendor" / "pkg" / "mnt"
    mount.mkdir(parents=True)
    (mount / "external.dat").write_text("keep")
    monkeypatch.setattr(deps_cmd.sys, "platform", "linux")
    if detected_by == "ismount":
        monkeypatch.setattr(deps_cmd, "_linux_mount_points", lambda: None)
        monkeypatch.setattr(deps_cmd.os.path, "ismount", lambda p: Path(p) == mount)
    else:
        # A same-filesystem bind mount: ismount() says no, mountinfo says yes.
        monkeypatch.setattr(deps_cmd.os.path, "ismount", lambda p: False)
        monkeypatch.setattr(
            deps_cmd, "_linux_mount_points", lambda: ["/", os.path.realpath(mount)]
        )
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("installer must not run"),
    )

    assert handle_sync(
        TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path, clean=clean)
    ) == 1
    assert (mount / "external.dat").read_text() == "keep"
    assert not list(plugin_dir.glob(".vendor.*"))
    assert "mount point" in capsys.readouterr().err


@pytest.mark.parametrize("detected_by", ["mountinfo", "ismount"])
@pytest.mark.parametrize("where", ["nested", "root"])
@pytest.mark.parametrize("kind", ["staging", "backup"])
def test_leftover_work_dir_with_mount_is_not_deleted(
    tmp_path, monkeypatch, capsys, kind, where, detected_by,
):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    (plugin_dir / "vendor").mkdir()
    leftover = plugin_dir / f".vendor.{kind}-0000cccc"
    mount = leftover / "pkg" / "mnt" if where == "nested" else leftover
    mount.mkdir(parents=True)
    (mount / "external.dat").write_text("keep")
    if detected_by == "mountinfo":
        monkeypatch.setattr(deps_cmd.sys, "platform", "linux")
        monkeypatch.setattr(deps_cmd, "_linux_mount_points", lambda: ["/", os.path.realpath(mount)])
        monkeypatch.setattr(deps_cmd.os.path, "ismount", lambda p: False)
    else:
        # Other POSIX systems have no mount table here; ismount decides.
        monkeypatch.setattr(deps_cmd.sys, "platform", "darwin")
        monkeypatch.setattr(deps_cmd, "_linux_mount_points", lambda: None)
        monkeypatch.setattr(deps_cmd.os.path, "ismount", lambda p: Path(p) == mount)
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="ok"),
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 0
    assert (mount / "external.dat").read_text() == "keep"
    assert "is a mount point" in capsys.readouterr().err


def test_plugin_dirs_that_only_share_the_prefix_are_never_touched(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    own = [plugin_dir / ".vendor.staging-assets", plugin_dir / ".vendor.backup-notes"]
    for path in own:
        path.mkdir()
        (path / "data.txt").write_text("user data")
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="ok"),
    )

    # A look-alike backup must neither block the sync nor be cleaned up.
    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 0
    assert all((path / "data.txt").read_text() == "user data" for path in own)


def test_generated_gitignore_anchors_sync_dirs_to_plugin_root():
    from plugin.neko_plugin_cli.templates.generator import _render_gitignore

    lines = _render_gitignore().splitlines()
    # Anchored to the root; no trailing "/" so the backup's marker file is
    # ignored too.
    hex8 = "[0-9a-f]" * 8
    assert f"/.vendor.staging-{hex8}" in lines
    assert f"/.vendor.backup-{hex8}" in lines
    assert f"/.vendor.backup-{hex8}.pending" in lines
    assert not any(line.startswith(".vendor.") for line in lines)


def test_failed_install_keeps_staging_with_a_mount_inside(tmp_path, monkeypatch):
    # The install ran inside staging; a mount there must not be emptied by
    # the cleanup of a failed sync.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    monkeypatch.setattr(
        deps_cmd, "_mounted_inside", lambda path: path.name.startswith(".vendor.staging-")
    )
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="build failed"),
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 1
    assert [p for p in plugin_dir.glob(".vendor.staging-*") if p.is_dir()]


@pytest.mark.parametrize("clean", [False, True])
def test_sync_installs_into_a_mounted_vendor_root_in_place(tmp_path, monkeypatch, capsys, clean):
    # A mount point (a Docker volume) can not be renamed; install into it.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    (vendor / "old.py").write_text("old")
    monkeypatch.setattr(deps_cmd, "_is_mount_point", lambda p: Path(p) == vendor)

    def install(command, **kwargs):
        (Path(command[command.index("--target") + 1]) / "fresh.py").write_text("new")
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    monkeypatch.setattr(deps_cmd.subprocess, "run", install)

    assert handle_sync(
        TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path, clean=clean)
    ) == 0
    assert vendor.is_dir() and not deps_cmd._is_link(vendor)
    assert (vendor / "fresh.py").read_text() == "new"
    assert (vendor / "old.py").exists() is (not clean)
    assert not list(plugin_dir.glob(".vendor.*"))
    assert "updating it in place" in capsys.readouterr().err


def test_in_place_clean_refuses_a_mount_inside(tmp_path, monkeypatch, capsys):
    # Emptying vendor/ recurses; a mount inside it would lose its files.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    mount = vendor / "pkg" / "mnt"
    mount.mkdir(parents=True)
    (mount / "external.dat").write_text("keep")
    monkeypatch.setattr(deps_cmd, "_is_mount_point", lambda p: Path(p) == vendor)
    monkeypatch.setattr(deps_cmd, "_find_foreign_subdir", lambda root, junctions: mount)
    monkeypatch.setattr(
        deps_cmd.subprocess, "run", lambda *args, **kwargs: pytest.fail("installer must not run")
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path, clean=True)) == 1
    assert (mount / "external.dat").read_text() == "keep"
    assert "--clean would delete what it holds" in capsys.readouterr().err


@pytest.mark.parametrize("reason", ["nested", "no-mount-table"])
def test_in_place_cleanup_skips_a_tree_with_a_mount_inside(tmp_path, monkeypatch, capsys, reason):
    # Removing caches recurses; it must not reach into a mount inside vendor/.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    mount = vendor / "pkg" / "mnt"
    (mount / "__pycache__").mkdir(parents=True)
    (mount / "__pycache__" / "x.pyc").write_text("external")
    monkeypatch.setattr(deps_cmd, "_is_mount_point", lambda p: Path(p) == vendor)
    if reason == "nested":
        monkeypatch.setattr(deps_cmd, "_find_foreign_subdir", lambda root, junctions: mount)
    else:
        monkeypatch.setattr(deps_cmd.sys, "platform", "linux")
        monkeypatch.setattr(deps_cmd, "_linux_mount_points", lambda: None)
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="ok"),
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 0
    assert (mount / "__pycache__" / "x.pyc").read_text() == "external"
    assert "Skipped removing __pycache__" in capsys.readouterr().err


def test_mount_in_in_place_clean_staging_stops_before_cleanup(tmp_path, monkeypatch, capsys):
    # _clean_vendor recurses: a mount that appeared in staging during the
    # install must not lose its caches, nor be moved into vendor/.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    (vendor / "old.py").write_text("old")
    monkeypatch.setattr(deps_cmd, "_is_mount_point", lambda p: Path(p) == vendor)
    mounts = []

    def install(command, **kwargs):
        staging = Path(command[command.index("--target") + 1])
        mount = staging / "mnt"
        (mount / "__pycache__").mkdir(parents=True)
        (mount / "__pycache__" / "x.pyc").write_text("external")
        mounts.append(mount)
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    monkeypatch.setattr(deps_cmd.subprocess, "run", install)
    monkeypatch.setattr(
        deps_cmd,
        "_find_mount",
        lambda path: mounts[0] if mounts and path.name.startswith(".vendor.staging-") else None,
    )
    monkeypatch.setattr(
        deps_cmd, "_mounted_inside", lambda path: path.name.startswith(".vendor.staging-")
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path, clean=True)) == 1
    assert (mounts[0] / "__pycache__" / "x.pyc").read_text() == "external"
    assert (vendor / "old.py").read_text() == "old"
    assert "got mounted inside" in capsys.readouterr().err


@pytest.mark.parametrize("change", ["mount", "retarget", "replaced"])
def test_in_place_clean_rechecks_vendor_before_emptying(tmp_path, monkeypatch, capsys, change):
    # The install can take minutes; vendor/ may change under it.
    import shutil

    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    (vendor / "old.py").write_text("old")
    monkeypatch.setattr(deps_cmd, "_is_mount_point", lambda p: Path(p) == vendor)
    installed = []
    real_find = deps_cmd._find_foreign_subdir

    def find(root, junctions):
        if change == "mount" and installed and Path(root) == vendor:
            return vendor / "pkg" / "new-mount"
        return real_find(root, junctions=junctions)

    def install(command, **kwargs):
        staging = Path(command[command.index("--target") + 1])
        (staging / "fresh.py").write_text("new")
        installed.append(staging)
        if change == "retarget":
            # vendor/ now leads somewhere the staging dir is not.
            shutil.rmtree(staging)
        if change == "replaced":
            # Another directory now sits at vendor/, even holding a dir of
            # the staging name: only its identity tells them apart.
            vendor.rename(plugin_dir / "moved-away")
            (vendor / staging.name).mkdir(parents=True)
            (vendor / "other.py").write_text("other")
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    monkeypatch.setattr(deps_cmd, "_find_foreign_subdir", find)
    monkeypatch.setattr(deps_cmd.subprocess, "run", install)
    cleaned = []
    real_clean = deps_cmd._clean_vendor
    monkeypatch.setattr(deps_cmd, "_clean_vendor", lambda path: (cleaned.append(path), real_clean(path)))

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path, clean=True)) == 1
    if change == "replaced":
        assert (vendor / "other.py").read_text() == "other"
        assert (plugin_dir / "moved-away" / "old.py").read_text() == "old"
        # The same-named dir in the replacement is not ours to clean or remove.
        assert (vendor / installed[0].name).is_dir()
        assert cleaned == []
    else:
        assert (vendor / "old.py").read_text() == "old"
    assert "changed during the sync" in capsys.readouterr().err


def test_in_place_sync_refuses_a_vendor_retargeted_after_locking(tmp_path, monkeypatch, capsys):
    # The lock was taken for one target; vendor/ now leads elsewhere.
    from plugin.neko_plugin_cli.commands import deps_cmd

    vendor = tmp_path / "vendor"
    vendor.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(
        deps_cmd.subprocess, "run", lambda *args, **kwargs: pytest.fail("installer must not run")
    )
    args = TestTransactionalDependencyInstall()._args(tmp_path, tmp_path)

    assert deps_cmd._sync_in_place(vendor, ["httpx"], args, other.stat()) == 1
    assert "changed during the sync" in capsys.readouterr().err


def test_non_clean_in_place_install_reports_a_retarget_during_it(tmp_path, monkeypatch, capsys):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    (vendor / "old.py").write_text("old")
    monkeypatch.setattr(deps_cmd, "_is_mount_point", lambda p: Path(p) == vendor)

    def install(command, **kwargs):
        vendor.rename(plugin_dir / "moved-away")
        vendor.mkdir()
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    monkeypatch.setattr(deps_cmd.subprocess, "run", install)

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 1
    assert "changed during the sync" in capsys.readouterr().err


def test_swap_path_skips_recursive_cleanup_without_linux_mount_table(tmp_path, monkeypatch, capsys):
    # ismount() misses a same-filesystem bind mount the cleanup would enter.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    monkeypatch.setattr(deps_cmd.sys, "platform", "linux")
    monkeypatch.setattr(deps_cmd, "_linux_mount_points", lambda: None)
    monkeypatch.setattr(deps_cmd.os.path, "ismount", lambda p: False)
    monkeypatch.setattr(deps_cmd, "_mounted_inside", lambda path: False)

    def install(command, **kwargs):
        target = Path(command[command.index("--target") + 1])
        cache = target / "pkg" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "x.pyc").write_text("cache")
        (target / "bin").mkdir()
        (target / "bin" / "tool").write_text("script")
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    monkeypatch.setattr(deps_cmd.subprocess, "run", install)

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 0
    assert (plugin_dir / "vendor" / "pkg" / "__pycache__" / "x.pyc").exists()
    # build and pack would ship bin/; it is still removed, without recursing.
    assert not (plugin_dir / "vendor" / "bin").exists()
    assert "Skipped removing __pycache__" in capsys.readouterr().err


def test_installer_bin_that_is_a_mount_point_is_left(tmp_path, monkeypatch, capsys):
    # Its files belong to the mounted tree, even when there are only files.
    from plugin.neko_plugin_cli.commands import deps_cmd

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "tool").write_text("external")
    monkeypatch.setattr(deps_cmd, "_is_mount_point", lambda p: Path(p) == bin_dir)

    deps_cmd._remove_installer_bin(tmp_path)

    assert (bin_dir / "tool").read_text() == "external"
    assert "is a mount point" in capsys.readouterr().err


def test_in_place_sync_without_linux_mount_table_keeps_vendor_bin(tmp_path, monkeypatch, capsys):
    # vendor/ is the user's tree; its bin/ may be a bind mount ismount misses.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    (vendor / "bin").mkdir(parents=True)
    (vendor / "bin" / "tool").write_text("external")
    monkeypatch.setattr(deps_cmd.sys, "platform", "linux")
    monkeypatch.setattr(deps_cmd, "_linux_mount_points", lambda: None)
    monkeypatch.setattr(deps_cmd, "_is_mount_point", lambda p: Path(p) == vendor)
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="ok"),
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 0
    assert (vendor / "bin" / "tool").read_text() == "external"
    assert "Remove vendor/bin by hand" in capsys.readouterr().err


def test_installer_bin_with_a_directory_inside_is_left(tmp_path, capsys):
    from plugin.neko_plugin_cli.commands import deps_cmd

    (tmp_path / "bin" / "sub").mkdir(parents=True)
    (tmp_path / "bin" / "sub" / "keep").write_text("keep")
    (tmp_path / "bin" / "tool").write_text("script")

    deps_cmd._remove_installer_bin(tmp_path)

    assert (tmp_path / "bin" / "sub" / "keep").read_text() == "keep"
    assert "contains directories" in capsys.readouterr().err


@pytest.mark.parametrize("clean", [False, True])
@pytest.mark.parametrize("points_at", ["plugin", "parent"])
def test_vendor_leading_to_the_plugin_or_a_parent_is_refused(tmp_path, monkeypatch, capsys, clean, points_at):
    # vendor -> .. would have the refill move the plugin aside and delete it.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    target = plugin_dir if points_at == "plugin" else tmp_path
    link = plugin_dir / "vendor"
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(
        deps_cmd.subprocess, "run", lambda *args, **kwargs: pytest.fail("installer must not run")
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path, clean=clean)) == 1
    assert (plugin_dir / "plugin.toml").is_file()
    assert "leads to the plugin directory" in capsys.readouterr().err


def test_zero_inode_targets_are_compared_by_path(tmp_path, monkeypatch):
    # Some filesystems report st_ino 0 for every directory; identity alone
    # would call a separate shared vendor target "the plugin".
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    shared = tmp_path / "shared_vendor"
    shared.mkdir()
    zero = os.stat_result((0o40755, 0, 1, 1, 0, 0, 0, 0, 0, 0))

    link = tmp_path / "vendor_link"
    monkeypatch.setattr(deps_cmd, "_is_link", lambda path: path in {shared, tmp_path})
    assert deps_cmd._contains_plugin(zero, shared, plugin_dir) is False
    assert deps_cmd._contains_plugin(zero, tmp_path, plugin_dir) is True
    # A mount point (not a link) can not be resolved to what it mounts.
    assert deps_cmd._contains_plugin(zero, link, plugin_dir) is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction pinning")
def test_windows_refill_pins_the_junction_against_retargeting(tmp_path, monkeypatch):
    # Without directory handles, the link itself is held open so another
    # process can not delete or retarget it mid-refill.
    from plugin.neko_plugin_cli.commands import deps_cmd

    target = tmp_path / "target"
    target.mkdir()
    (target / "old.py").write_text("old")
    staging = target / ".vendor.staging-0000abcd"
    staging.mkdir()
    (staging / "new.py").write_text("new")
    vendor = tmp_path / "vendor"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(vendor), str(target)],
                   check=True, capture_output=True)
    monkeypatch.setattr(deps_cmd, "_find_foreign_subdir", lambda root, junctions: None)
    attempts = []
    real_empty = deps_cmd._empty_directory

    def empty(directory, *, keep):
        attempts.append(subprocess.run(["cmd", "/c", "rmdir", str(vendor)], capture_output=True))
        real_empty(directory, keep=keep)

    monkeypatch.setattr(deps_cmd, "_empty_directory", empty)

    assert deps_cmd._refill_in_place(vendor, staging, vendor.stat()) == 0
    assert attempts[0].returncode != 0
    assert os.readlink(vendor)
    assert (target / "new.py").read_text() == "new"
    assert not (target / "old.py").exists()


def test_plugin_dir_replaced_before_the_lock_is_refused(tmp_path, monkeypatch, capsys):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    samples = iter([b"1:1", b"2:2"])
    monkeypatch.setattr(deps_cmd, "_lock_identity", lambda path, info=None: next(samples))
    monkeypatch.setattr(
        deps_cmd, "_remove_stale_staging", lambda path: pytest.fail("work dirs must not be touched")
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 1
    assert "was replaced while the sync started" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions are Windows-only")
def test_plugins_sharing_a_vendor_target_share_a_lock(tmp_path, monkeypatch, capsys):
    # Two plugins linking vendor/ to one target are one writer's business.
    from plugin.neko_plugin_cli.commands import deps_cmd

    shared = tmp_path / "shared_vendor"
    shared.mkdir()
    plugins = []
    for name in ("first", "second"):
        plugin_dir = tmp_path / name
        plugin_dir.mkdir()
        (plugin_dir / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "1"\ndependencies = ["httpx"]\n', encoding="utf-8"
        )
        subprocess.run(["cmd", "/c", "mklink", "/J", str(plugin_dir / "vendor"), str(shared)],
                       check=True, capture_output=True)
        plugins.append(plugin_dir)
    monkeypatch.setattr(
        deps_cmd, "resolve_plugin_dir_candidate", lambda plugin, defaults: Path(plugin)
    )
    nested = []

    def install(command, **kwargs):
        if not nested:
            nested.append(
                handle_sync(TestTransactionalDependencyInstall()._args(plugins[1], tmp_path))
            )
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    monkeypatch.setattr(deps_cmd.subprocess, "run", install)
    assert handle_sync(TestTransactionalDependencyInstall()._args(plugins[0], tmp_path)) == 0
    assert nested == [1]
    assert "already in progress" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="directory-handle refill is POSIX only")
def test_in_place_refill_leaves_another_users_staging(tmp_path, monkeypatch):
    # The lock is per user: another user's staging in a shared target may be
    # a live install.
    from plugin.neko_plugin_cli.commands import deps_cmd

    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "old.py").write_text("old")
    theirs = vendor / ".vendor.staging-1111abcd"
    theirs.mkdir()
    (theirs / "half.py").write_text("theirs")
    staging = vendor / ".vendor.staging-0000abcd"
    staging.mkdir()
    (staging / "new.py").write_text("new")
    identity = vendor.stat()
    monkeypatch.setattr(deps_cmd, "_find_foreign_subdir", lambda root, junctions: None)
    monkeypatch.setattr(deps_cmd, "_mounted_inside", lambda path: False)
    me = os.getuid()
    real_stat = os.stat

    def stat(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if path == theirs.name:
            values = list(info)
            values[4] = me + 1  # st_uid
            return os.stat_result(values)
        return info

    monkeypatch.setattr(deps_cmd.os, "stat", stat)

    assert deps_cmd._refill_in_place(vendor, staging, identity) == 0
    assert (theirs / "half.py").read_text() == "theirs"
    assert (vendor / "new.py").read_text() == "new"
    assert not (vendor / "old.py").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="directory-handle refill is POSIX only")
def test_in_place_refill_keeps_old_contents_with_a_new_mount(tmp_path, monkeypatch, capsys):
    # A mount added under vendor/ while the install ran must not be deleted:
    # the old contents are moved aside and checked right before deletion.
    from plugin.neko_plugin_cli.commands import deps_cmd

    vendor = tmp_path / "vendor"
    (vendor / "oldpkg").mkdir(parents=True)
    (vendor / "oldpkg" / "mnt.dat").write_text("external")
    staging = vendor / ".vendor.staging-0000abcd"
    staging.mkdir()
    (staging / "new.py").write_text("new")
    identity = vendor.stat()
    monkeypatch.setattr(deps_cmd, "_find_foreign_subdir", lambda root, junctions: None)
    monkeypatch.setattr(deps_cmd, "_mounted_inside", lambda path: True)

    assert deps_cmd._refill_in_place(vendor, staging, identity) == 0
    assert (vendor / "new.py").read_text() == "new"
    kept = [p for p in vendor.glob(".vendor.staging-*") if p != staging]
    assert len(kept) == 1 and (kept[0] / "oldpkg" / "mnt.dat").read_text() == "external"


def test_failed_in_place_clean_keeps_the_old_dependencies(tmp_path, monkeypatch, capsys):
    # publish always cleans; a resolver failure must not empty vendor/.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    (vendor / "old.py").write_text("old")
    monkeypatch.setattr(deps_cmd, "_is_mount_point", lambda p: Path(p) == vendor)
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="resolver error"),
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path, clean=True)) == 1
    assert (vendor / "old.py").read_text() == "old"
    assert not list(plugin_dir.glob(".vendor.*"))
    assert not list(vendor.glob(".vendor.*"))


@pytest.mark.parametrize("clean", [False, True])
def test_in_place_sync_removes_staging_left_inside_vendor(tmp_path, monkeypatch, clean):
    # A killed in-place --clean leaves its staging dir inside vendor/, which
    # would otherwise ship with the plugin.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    stale = vendor / ".vendor.staging-0000abcd"
    stale.mkdir(parents=True)
    (stale / "half.py").write_text("half")
    monkeypatch.setattr(deps_cmd, "_is_mount_point", lambda p: Path(p) == vendor)
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="ok"),
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path, clean=clean)) == 0
    assert not stale.exists()


def test_in_place_clean_without_linux_mount_table_is_refused(tmp_path, monkeypatch, capsys):
    # ismount() misses a same-filesystem bind mount; emptying could follow it.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    (vendor / "old.py").write_text("old")
    monkeypatch.setattr(deps_cmd.sys, "platform", "linux")
    monkeypatch.setattr(deps_cmd, "_linux_mount_points", lambda: None)
    monkeypatch.setattr(deps_cmd, "_is_mount_point", lambda p: Path(p) == vendor)
    monkeypatch.setattr(
        deps_cmd.subprocess, "run", lambda *args, **kwargs: pytest.fail("installer must not run")
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path, clean=True)) == 1
    assert (vendor / "old.py").read_text() == "old"
    assert "mountinfo is unavailable" in capsys.readouterr().err


def test_failed_in_place_install_is_reported(tmp_path, monkeypatch, capsys):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    monkeypatch.setattr(deps_cmd, "_is_mount_point", lambda p: Path(p) == vendor)
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="resolver error"),
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 1
    assert vendor.is_dir()
    assert "uv pip install failed" in capsys.readouterr().err


@pytest.mark.parametrize("owner", ["mine", "theirs", "fresh"])
def test_orphan_pending_marker_is_removed(tmp_path, monkeypatch, owner):
    # Left by a run killed between creating the marker and the rename, or by
    # a rollback that could not delete it; nothing else would remove it.
    from plugin.neko_plugin_cli.commands import deps_cmd

    orphan = tmp_path / ".vendor.backup-0000abcd.pending"
    orphan.touch()
    if owner != "fresh":
        # A fresh marker may be another sync's, right before its rename
        # (Windows can not tell whose it is).
        old = orphan.stat().st_mtime - 3600
        os.utime(orphan, (old, old))
    live = tmp_path / ".vendor.backup-1111abcd"
    live.mkdir()
    live_marker = tmp_path / ".vendor.backup-1111abcd.pending"
    live_marker.touch()
    look_alike = tmp_path / ".vendor.backup-notes.pending"
    look_alike.touch()
    # Another user's marker may precede their rename right now.
    monkeypatch.setattr(deps_cmd, "_owned_by_other_user", lambda path: owner == "theirs")

    deps_cmd._remove_orphan_markers(tmp_path)

    assert orphan.exists() is (owner != "mine")
    assert live_marker.exists()
    assert look_alike.exists()


def test_unwritable_private_lock_dir_falls_back(tmp_path, monkeypatch):
    # An existing read-only dir (mode 0500, a read-only mount) must not be
    # returned: every lock open would fail.
    from plugin.neko_plugin_cli.commands import deps_cmd

    cache = tmp_path / "cache"
    private = cache / "neko-plugin" / "sync-locks"
    private.mkdir(parents=True)
    shared = tmp_path / "shared-tmp"
    shared.mkdir()
    me = tmp_path.stat().st_uid
    monkeypatch.setattr(deps_cmd.os, "getuid", lambda: me, raising=False)
    monkeypatch.setattr(deps_cmd, "_lock_cache_base", lambda: cache)
    monkeypatch.setattr(deps_cmd, "_shared_tmp", lambda: shared)
    real_access = os.access
    monkeypatch.setattr(
        deps_cmd.os, "access", lambda path, mode: Path(path) != private and real_access(path, mode)
    )

    assert real_lock_dir() == shared / f"neko-plugin-sync-{me}"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink and permission bits")
def test_symlinked_lock_dir_is_not_used_or_chmodded(tmp_path, monkeypatch):
    # chmod would follow the link and close a shared directory to others.
    from plugin.neko_plugin_cli.commands import deps_cmd

    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o775)
    cache = tmp_path / "cache"
    (cache / "neko-plugin").mkdir(parents=True)
    (cache / "neko-plugin" / "sync-locks").symlink_to(shared, target_is_directory=True)
    monkeypatch.setattr(deps_cmd, "_lock_cache_base", lambda: cache)
    monkeypatch.setattr(deps_cmd, "_shared_tmp", lambda: tmp_path / "tmp")
    (tmp_path / "tmp").mkdir()

    lock_dir = real_lock_dir()
    assert lock_dir == tmp_path / "tmp" / f"neko-plugin-sync-{os.getuid()}"
    assert shared.stat().st_mode & 0o777 == 0o775


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_lock_dir_that_is_open_to_others_is_closed(tmp_path, monkeypatch):
    # mkdir(mode=0o700) leaves an existing, world-writable dir as it is.
    from plugin.neko_plugin_cli.commands import deps_cmd

    cache = tmp_path / "cache"
    locks = cache / "neko-plugin" / "sync-locks"
    locks.mkdir(parents=True)
    os.chmod(locks, 0o777)
    monkeypatch.setattr(deps_cmd, "_lock_cache_base", lambda: cache)

    assert real_lock_dir() == locks
    assert locks.stat().st_mode & 0o777 == 0o700


def test_linux_without_mount_table_keeps_leftovers(tmp_path, monkeypatch, capsys):
    # ismount() misses same-filesystem bind mounts, so without
    # /proc/self/mountinfo nothing can be ruled out: keep, do not rmtree.
    from plugin.neko_plugin_cli.commands import deps_cmd

    leftover = tmp_path / ".vendor.backup-0000cccc"
    (leftover / "pkg").mkdir(parents=True)
    monkeypatch.setattr(deps_cmd.sys, "platform", "linux")
    monkeypatch.setattr(deps_cmd, "_linux_mount_points", lambda: None)
    monkeypatch.setattr(deps_cmd.os.path, "ismount", lambda p: False)

    assert deps_cmd._mounted_inside(leftover) is True
    assert "mountinfo is unavailable" in capsys.readouterr().err


def test_no_dependency_sync_still_cleans_finished_backups(tmp_path, monkeypatch):
    # A finished swap whose backup could not be deleted must not linger
    # forever just because the plugin has no dependencies now.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "my_plugin"\nversion = "1.0.0"\ndependencies = []\n',
        encoding="utf-8",
    )
    (plugin_dir / "vendor").mkdir()
    leftover = plugin_dir / ".vendor.backup-0000dddd"
    leftover.mkdir()
    (leftover / "big.py").write_text("x")

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 0
    assert not leftover.exists()


def test_stale_staging_cleanup_failure_warns(tmp_path, monkeypatch, capsys):
    from plugin.neko_plugin_cli.commands import deps_cmd

    stale = tmp_path / ".vendor.staging-0000bbbb"
    stale.mkdir()

    def fail(path, *args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(deps_cmd.shutil, "rmtree", fail)
    deps_cmd._remove_stale_staging(tmp_path)
    assert "Could not remove stale staging dir" in capsys.readouterr().err


def test_non_clean_retry_refuses_partial_vendor_with_pending_backup(
    tmp_path, monkeypatch, capsys
):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    (vendor / "partial.py").write_text("partial")
    backup = plugin_dir / ".vendor.backup-0000aaaa"
    backup.mkdir()
    (backup / "old.py").write_text("backup")
    backup.with_name(backup.name + ".pending").touch()
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("installer must not run before recovery"),
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 1
    assert (vendor / "partial.py").read_text() == "partial"
    assert backup.exists()
    assert "unreconciled dependency backup" in capsys.readouterr().err


def test_successful_sync_warns_when_retained_backup_cleanup_fails(tmp_path, monkeypatch, capsys):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    (vendor / "old.py").write_text("old")
    backup = plugin_dir / ".vendor.backup-0000aaaa"
    backup.mkdir()
    (backup / "old.py").write_text("backup")
    real_rmtree = deps_cmd.shutil.rmtree

    def fail_backup_cleanup(path, *args, **kwargs):
        if Path(path) == backup:
            raise PermissionError("backup is locked")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(deps_cmd.shutil, "rmtree", fail_backup_cleanup)
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="ok"),
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 0
    assert backup.exists()
    assert "Could not remove old dependency backup" in capsys.readouterr().err


def test_non_clean_sync_preserves_links_including_dangling(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    try:
        (vendor / "linked.txt").symlink_to(outside)
        (vendor / "dangling.txt").symlink_to(tmp_path / "missing.txt")
    except OSError:
        pytest.skip("symlink creation requires OS permission")
    monkeypatch.setattr(deps_cmd.subprocess, "run",
                        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="ok"))
    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 0
    assert (vendor / "linked.txt").is_symlink()
    assert (vendor / "linked.txt").resolve() == outside.resolve()
    assert (vendor / "dangling.txt").is_symlink()
    assert outside.read_text() == "outside"


def test_clean_vendor_removes_bin_directory_symlink(tmp_path: Path) -> None:
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    target = tmp_path / "bin-target"
    target.mkdir()
    (target / "script").write_text("x")
    try:
        (vendor / "bin").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation requires OS permission")

    _clean_vendor(vendor)

    assert not (vendor / "bin").exists()
    assert (target / "script").exists()


@pytest.mark.plugin_unit
class TestHelpers:
    def test_read_dependencies(self, tmp_path: Path) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "test"\ndependencies = ["httpx>=0.27", "pydantic"]\n',
            encoding="utf-8",
        )
        assert _read_dependencies(pyproject) == ["httpx>=0.27", "pydantic"]

    def test_read_dependencies_empty(self, tmp_path: Path) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "test"\ndependencies = []\n', encoding="utf-8")
        assert _read_dependencies(pyproject) == []

    def test_read_dependencies_missing_field(self, tmp_path: Path) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "test"\n', encoding="utf-8")
        assert _read_dependencies(pyproject) == []

    def test_filter_external(self) -> None:
        deps = ["httpx>=0.27", "N.E.K.O", "pydantic>=2.0"]
        assert _filter_external(deps) == ["httpx>=0.27", "pydantic>=2.0"]

    def test_filter_external_case_insensitive(self) -> None:
        deps = ["n-e-k-o>=1.0", "httpx"]
        assert _filter_external(deps) == ["httpx"]

    def test_clean_vendor(self, tmp_path: Path) -> None:
        vendor = tmp_path / "vendor"
        vendor.mkdir()
        (vendor / "__pycache__").mkdir()
        (vendor / "__pycache__" / "foo.pyc").write_text("x")
        (vendor / "bin").mkdir()
        (vendor / "bin" / "script").write_text("x")
        (vendor / "httpx").mkdir()
        (vendor / "httpx" / "__init__.py").write_text("x")

        _clean_vendor(vendor)

        assert not (vendor / "__pycache__").exists()
        assert not (vendor / "bin").exists()

        assert (vendor / "httpx" / "__init__.py").exists()


@pytest.mark.plugin_unit
class TestHandleSync:
    def _make_plugin(self, tmp_path: Path) -> Path:
        plugin_dir = tmp_path / "my_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            '[plugin]\nid = "my_plugin"\nname = "My Plugin"\nversion = "1.0.0"\n'
            'entry = "plugin.plugins.my_plugin:MyPlugin"\n',
            encoding="utf-8",
        )
        (plugin_dir / "pyproject.toml").write_text(
            '[project]\nname = "my_plugin"\nversion = "1.0.0"\n'
            'dependencies = ["httpx>=0.27", "N.E.K.O"]\n',
            encoding="utf-8",
        )
        return plugin_dir

    def test_sync_installs_external_deps_only(self, tmp_path: Path) -> None:
        plugin_dir = self._make_plugin(tmp_path)

        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n")
        with patch("plugin.neko_plugin_cli.commands.deps_cmd.subprocess.run", return_value=fake_result) as mock_run:
            import argparse
            from plugin.neko_plugin_cli.paths import CliDefaults

            defaults = CliDefaults(
                plugin_root=tmp_path,
                target_dir=tmp_path / "target",
                plugins_root=tmp_path,
                profiles_root=tmp_path / "profiles",
            )
            args = argparse.Namespace(
                plugin=str(plugin_dir),
                python="python",
                clean=False,
                _defaults=defaults,
            )
            exit_code = handle_sync(args)

        assert exit_code == 0
        assert mock_run.called
        # Should only install httpx, not N.E.K.O
        call_args = mock_run.call_args[0][0]
        assert "httpx>=0.27" in call_args
        assert "N.E.K.O" not in call_args

    def test_sync_no_deps(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "empty_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            '[plugin]\nid = "empty_plugin"\nname = "X"\nversion = "1.0.0"\n'
            'entry = "plugin.plugins.empty_plugin:X"\n',
            encoding="utf-8",
        )
        (plugin_dir / "pyproject.toml").write_text(
            '[project]\nname = "empty_plugin"\nversion = "1.0.0"\ndependencies = []\n',
            encoding="utf-8",
        )

        import argparse
        from plugin.neko_plugin_cli.paths import CliDefaults

        defaults = CliDefaults(
            plugin_root=tmp_path,
            target_dir=tmp_path / "target",
            plugins_root=tmp_path,
            profiles_root=tmp_path / "profiles",
        )
        args = argparse.Namespace(
            plugin=str(plugin_dir),
            python="python",
            clean=False,
            _defaults=defaults,
        )
        exit_code = handle_sync(args)
        assert exit_code == 0


@pytest.mark.parametrize("clean", [False, True])
def test_sync_no_deps_refuses_unreconciled_backup(tmp_path, clean, capsys):
    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    (vendor / "partial.py").write_text("partial")
    backup = plugin_dir / ".vendor.backup-0000aaaa"
    backup.mkdir()
    (backup / "old.py").write_text("backup")
    backup.with_name(backup.name + ".pending").touch()
    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "my_plugin"\nversion = "1.0.0"\ndependencies = []\n',
        encoding="utf-8",
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path, clean=clean)) == 1
    assert (vendor / "partial.py").read_text() == "partial"
    assert backup.exists()
    assert "unreconciled dependency backup" in capsys.readouterr().err

@pytest.mark.plugin_unit
class TestTransactionalDependencyInstall:
    def _defaults(self, tmp_path: Path):
        from plugin.neko_plugin_cli.paths import CliDefaults

        return CliDefaults(
            plugin_root=tmp_path,
            target_dir=tmp_path / "target",
            plugins_root=tmp_path,
            profiles_root=tmp_path / "profiles",
        )

    def _args(self, plugin_dir: Path, tmp_path: Path, *, clean: bool = False):
        import argparse

        return argparse.Namespace(
            plugin=str(plugin_dir),
            python="target-python",
            clean=clean,
            _defaults=self._defaults(tmp_path),
        )


    def test_uv_installs_for_the_target_python(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The target needs no pip of its own: uv installs for it.
        plugin_dir = TestHandleSync()._make_plugin(tmp_path)
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="ok\n")

        monkeypatch.setattr("plugin.neko_plugin_cli.commands.deps_cmd.subprocess.run", fake_run)

        assert handle_sync(self._args(plugin_dir, tmp_path)) == 0
        assert len(calls) == 1
        assert calls[0][:5] == ["uv", "pip", "install", "--python", "target-python"]
        assert "--target" in calls[0]
        assert "uv was not found" not in capsys.readouterr().err

    def test_falls_back_to_pip_with_a_warning_when_uv_is_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        plugin_dir = TestHandleSync()._make_plugin(tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr("plugin.neko_plugin_cli.commands.deps_cmd._find_uv", lambda: None)

        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="ok\n")

        monkeypatch.setattr("plugin.neko_plugin_cli.commands.deps_cmd.subprocess.run", fake_run)

        assert handle_sync(self._args(plugin_dir, tmp_path)) == 0
        assert len(calls) == 1
        assert calls[0][:3] == ["target-python", "-m", "pip"]
        assert "--no-user" in calls[0]
        error = capsys.readouterr().err
        for text in ("This project requires uv", "本项目强制要求使用 uv", "uv の使用を必須"):
            assert text in error
        # Repeated at the end, after any installer output.
        assert error.rstrip().endswith("上の警告を参照してください。")

    def test_uv_failure_does_not_fall_back_to_pip(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        plugin_dir = TestHandleSync()._make_plugin(tmp_path)
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 2, stdout="uv resolver error\n")

        monkeypatch.setattr("plugin.neko_plugin_cli.commands.deps_cmd.subprocess.run", fake_run)

        assert handle_sync(self._args(plugin_dir, tmp_path)) == 1
        assert len(calls) == 1
        error = capsys.readouterr().err
        assert "uv pip install failed (exit 2)" in error
        assert "uv resolver error" in error
        assert not (plugin_dir / "vendor").exists()

    def test_reports_missing_uv_and_pip_clearly(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        plugin_dir = TestHandleSync()._make_plugin(tmp_path)
        vendor = plugin_dir / "vendor"
        vendor.mkdir()
        marker = vendor / "old.txt"
        marker.write_text("keep", encoding="utf-8")
        monkeypatch.setattr("plugin.neko_plugin_cli.commands.deps_cmd._find_uv", lambda: None)
        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.subprocess.run",
            lambda command, **kwargs: subprocess.CompletedProcess(
                command, 1, stdout="target-python: No module named pip\n"
            ),
        )

        assert handle_sync(self._args(plugin_dir, tmp_path, clean=True)) == 1
        assert marker.read_text(encoding="utf-8") == "keep"
        error = capsys.readouterr().err
        assert "uv was not found" in error
        assert "https://docs.astral.sh/uv/" in error
        assert "No module named pip" in error
        # The reminder comes after pip's error output, not before it.
        assert error.index("No module named pip") < error.index("This sync used pip, not uv")

    @pytest.mark.parametrize("clean", [False, True])
    def test_install_failure_preserves_existing_vendor(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        clean: bool,
    ) -> None:
        plugin_dir = TestHandleSync()._make_plugin(tmp_path)
        vendor = plugin_dir / "vendor"
        vendor.mkdir()
        marker = vendor / "old.txt"
        marker.write_text("keep", encoding="utf-8")
        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.shutil.which",
            lambda name: "uv" if name == "uv" else None,
        )
        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.subprocess.run",
            lambda command, **kwargs: subprocess.CompletedProcess(
                command, 2, stdout="download failed\\n"
            ),
        )

        assert handle_sync(self._args(plugin_dir, tmp_path, clean=clean)) == 1
        assert marker.read_text(encoding="utf-8") == "keep"

    def test_clean_success_removes_stale_dependencies(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin_dir = TestHandleSync()._make_plugin(tmp_path)
        vendor = plugin_dir / "vendor"
        vendor.mkdir()
        (vendor / "stale.py").write_text("stale", encoding="utf-8")
        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.shutil.which",
            lambda name: "uv" if name == "uv" else None,
        )

        def install(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            target = Path(command[command.index("--target") + 1])
            (target / "fresh.py").write_text("fresh", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="ok\\n")

        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.subprocess.run", install
        )
        assert handle_sync(self._args(plugin_dir, tmp_path, clean=True)) == 0
        assert (vendor / "fresh.py").exists()
        assert not (vendor / "stale.py").exists()

    def test_success_cleans_python_artifacts_from_staging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin_dir = TestHandleSync()._make_plugin(tmp_path)
        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.shutil.which",
            lambda name: "uv" if name == "uv" else None,
        )

        def install(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            target = Path(command[command.index("--target") + 1])
            (target / "package" / "__pycache__").mkdir(parents=True)
            (target / "package" / "__pycache__" / "module.pyc").write_text("x")
            (target / "module.pyc").write_text("x")
            (target / "bin").mkdir()
            (target / "bin" / "tool").write_text("x")
            (target / "package" / "__init__.py").parent.mkdir(exist_ok=True)
            (target / "package" / "__init__.py").write_text("x")
            return subprocess.CompletedProcess(command, 0, stdout="ok\\n")

        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.subprocess.run", install
        )
        assert handle_sync(self._args(plugin_dir, tmp_path, clean=True)) == 0
        vendor = plugin_dir / "vendor"
        assert (vendor / "package" / "__init__.py").exists()
        assert not (vendor / "package" / "__pycache__").exists()
        assert not (vendor / "module.pyc").exists()
        assert not (vendor / "bin").exists()


    def test_non_clean_success_retains_existing_extra_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin_dir = TestHandleSync()._make_plugin(tmp_path)
        vendor = plugin_dir / "vendor"
        vendor.mkdir()
        (vendor / "extra.py").write_text("keep", encoding="utf-8")
        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.shutil.which", lambda name: "uv" if name == "uv" else None
        )

        def install(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            target = Path(command[command.index("--target") + 1])
            (target / "fresh.py").write_text("fresh", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="ok")

        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.subprocess.run", install
        )
        assert handle_sync(
            TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)
        ) == 0
        assert (vendor / "extra.py").read_text(encoding="utf-8") == "keep"
        assert (vendor / "fresh.py").exists()

    @pytest.mark.parametrize("permission_error", [False, True])
    def test_second_rename_failure_restores_old_vendor(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        permission_error: bool,
    ) -> None:
        vendor = tmp_path / "vendor"
        staging = tmp_path / ".vendor.staging"
        vendor.mkdir()
        staging.mkdir()
        (vendor / "old.py").write_text("keep", encoding="utf-8")
        (staging / "fresh.py").write_text("new", encoding="utf-8")
        real_replace = Path.replace

        def fail_staging_replace(source: Path, destination: Path) -> Path:
            if source == staging:
                if permission_error:
                    raise PermissionError("file locked")
                raise OSError("rename failed")
            return real_replace(source, destination)

        monkeypatch.setattr(Path, "replace", fail_staging_replace)
        assert _replace_vendor(vendor, staging) is False
        assert (vendor / "old.py").read_text(encoding="utf-8") == "keep"
        assert not (vendor / "fresh.py").exists()
        # Both failures roll back by renaming the backup, never by copying it.
        assert not list(tmp_path.glob(".vendor.backup-*"))
        assert not list(tmp_path.glob("*.pending"))
        error = capsys.readouterr().err
        if permission_error:
            assert "files are in use" in error
        else:
            assert "rename failed" in error

    def test_sync_removes_staging_left_by_killed_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin_dir = TestHandleSync()._make_plugin(tmp_path)
        stale = plugin_dir / ".vendor.staging-0000bbbb"
        stale.mkdir()
        (stale / "big_dependency.py").write_text("x", encoding="utf-8")
        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.subprocess.run",
            lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="ok"),
        )

        assert handle_sync(self._args(plugin_dir, tmp_path)) == 0
        assert not list(plugin_dir.glob(".vendor.staging-*"))
        assert (plugin_dir / "vendor").is_dir()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
    @pytest.mark.parametrize("clean", [False, True])
    def test_new_vendor_follows_umask_not_private_tempdir_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean: bool
    ) -> None:
        import os

        plugin_dir = TestHandleSync()._make_plugin(tmp_path)
        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.subprocess.run",
            lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="ok"),
        )
        old_umask = os.umask(0o022)
        try:
            assert handle_sync(self._args(plugin_dir, tmp_path, clean=clean)) == 0
        finally:
            os.umask(old_umask)
        assert (plugin_dir / "vendor").stat().st_mode & 0o777 == 0o755


def test_lock_identity_is_the_directory_not_its_name(tmp_path, monkeypatch):
    # Two paths to one directory (bind-mount aliases) must share one lock.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    real_stat = Path.stat
    alias = tmp_path / "alias"

    def stat(path, *args, **kwargs):
        return real_stat(plugin_dir if path == alias else path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    assert deps_cmd._lock_identity(alias) == deps_cmd._lock_identity(plugin_dir)
    assert deps_cmd._lock_identity(other) != deps_cmd._lock_identity(plugin_dir)


def test_windows_lock_dir_ignores_per_process_temp(tmp_path, monkeypatch):
    # Two syncs by one user with different TEMP/TMP must share one lock, or
    # one could delete the other's live staging dir as stale.
    import tempfile

    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.delattr(deps_cmd.os, "getuid", raising=False)
    monkeypatch.setattr(deps_cmd, "_windows_known_folder", lambda folder_id: tmp_path / "local")
    seen = []
    for temp in ("temp-a", "temp-b"):
        monkeypatch.setenv("TEMP", str(tmp_path / temp))
        monkeypatch.setenv("TMP", str(tmp_path / temp))
        monkeypatch.setattr(tempfile, "tempdir", None)
        seen.append(real_lock_dir())

    assert seen == [tmp_path / "local" / "neko-plugin" / "sync-locks"] * 2


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shell API")
def test_windows_local_appdata_comes_from_the_shell_not_the_environment(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "per-process"))
    local = real_windows_known_folder(deps_cmd._FOLDERID_LOCAL_APPDATA)

    assert local is not None and local.is_dir()
    assert local != tmp_path / "per-process"
