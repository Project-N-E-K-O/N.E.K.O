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
    _lock_dir as real_lock_dir,
    _pip_config_files as real_pip_config_files,
    _probe_target as real_probe_target,
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
def _no_host_package_index_config(monkeypatch):
    """Keep the developer's own pip/uv settings out of these tests."""
    from plugin.neko_plugin_cli.commands import deps_cmd

    for name in list(os.environ):
        if name.upper() == "UV" or name.upper().startswith(("PIP_", "UV_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(deps_cmd, "_pip_config_files", lambda target: [])
    monkeypatch.setattr(deps_cmd, "_probe_target", lambda python: _target())


def _target(*, has_pip=False, in_venv=True, env=None, cwd=None, home=None):
    """What a pip-less venv target reports, seeing the test's environment."""
    from plugin.neko_plugin_cli.commands import deps_cmd

    return deps_cmd._TargetPython(
        has_pip=has_pip,
        prefix=Path("/target-env"),
        in_venv=in_venv,
        env=dict(os.environ) if env is None else env,
        cwd=Path.cwd() if cwd is None else cwd,
        home=Path.home() if home is None else home,
    )


def _cmd_name(command):
    """The program a command runs, without directory or .exe (uv is pinned
    to an absolute path before it runs)."""
    return Path(command[0]).stem


def _missing_pip_then_uv(calls):
    def run(command, **kwargs):
        calls.append(command)
        if _cmd_name(command) == "uv":
            return subprocess.CompletedProcess(command, 0, stdout="ok")
        return subprocess.CompletedProcess(command, 1, stdout="No module named pip")
    return run


@pytest.mark.parametrize(
    ("env", "config_text", "uses_uv"),
    [
        ({}, None, True),
        ({"PIP_INDEX_URL": "https://private/simple"}, None, False),
        ({"PIP_EXTRA_INDEX_URL": "https://private/simple"}, None, False),
        ({"PIP_INDEX_URL": "https://private/simple", "UV_DEFAULT_INDEX": "https://private/simple"}, None, True),
        ({}, "[global]\nindex-url = https://private/simple\n", False),
        ({}, "[install]\nextra_index_url = https://private/simple\n", False),
        ({"PIP_NO_INDEX": "1", "PIP_FIND_LINKS": "/wheels"}, None, False),
        ({"PIP_FIND_LINKS": "/wheels", "UV_FIND_LINKS": "/wheels"}, None, True),
        # pip's no-index is passed to uv as --no-index (uv has no environment
        # variable for it); with no index pip ignores its index settings too.
        ({"PIP_NO_INDEX": "1"}, None, True),
        ({"PIP_NO_INDEX": "1", "UV_FIND_LINKS": "/wheels"}, None, True),
        ({"PIP_NO_INDEX": "1", "PIP_INDEX_URL": "https://private/simple"}, None, True),
        ({"UV_FIND_LINKS": "/wheels"}, "[global]\nno-index = true\nfind-links = /wheels\n", True),
        # UV_NO_INDEX is not a uv variable; it covers nothing.
        ({"PIP_INDEX_URL": "https://private/simple", "UV_NO_INDEX": "1"}, None, False),
        ({"PIP_FIND_LINKS": "/wheels", "UV_NO_INDEX": "1"}, None, False),
        # Each pip source kind needs a uv setting of the matching kind.
        ({"PIP_INDEX_URL": "https://private/simple", "UV_FIND_LINKS": "/wheels"}, None, False),
        ({"PIP_FIND_LINKS": "/wheels", "UV_DEFAULT_INDEX": "https://private/simple"}, None, False),
        ({"PIP_INDEX_URL": "https://private/simple", "PIP_FIND_LINKS": "/wheels",
          "UV_INDEX": "https://private/simple"}, None, False),
        ({"PIP_INDEX_URL": "https://private/simple", "PIP_FIND_LINKS": "/wheels",
          "UV_DEFAULT_INDEX": "https://private/simple", "UV_FIND_LINKS": "/wheels"}, None, True),
        # pip's index-url replaces PyPI; uv's additive indexes do not.
        ({"PIP_INDEX_URL": "https://private/simple", "UV_INDEX": "https://private/simple"}, None, False),
        ({"PIP_INDEX_URL": "https://private/simple",
          "UV_EXTRA_INDEX_URL": "https://private/simple"}, None, False),
        ({"PIP_INDEX_URL": "https://private/simple", "UV_INDEX_URL": "https://private/simple"}, None, True),
        ({"PIP_EXTRA_INDEX_URL": "https://private/simple", "UV_INDEX": "https://private/simple"}, None, True),
        # An explicitly disabled no-index is not a restriction.
        ({"PIP_NO_INDEX": "false"}, None, True),
        # pip's hash-checking policy must carry over, or uv installs unhashed.
        ({"PIP_REQUIRE_HASHES": "1"}, None, False),
        ({}, "[install]\nrequire-hashes = true\n", False),
        ({"PIP_REQUIRE_HASHES": "1", "UV_REQUIRE_HASHES": "0"}, None, False),
        ({"PIP_REQUIRE_HASHES": "1", "UV_REQUIRE_HASHES": "true"}, None, True),
        ({"PIP_REQUIRE_HASHES": "off"}, None, True),
        # Any other pip policy has no checked uv counterpart: fail closed.
        ({"PIP_ONLY_BINARY": ":all:"}, None, False),
        ({"PIP_CONSTRAINT": "/ci/constraints.txt"}, None, False),
        ({"PIP_ONLY_BINARY": ":all:", "UV_DEFAULT_INDEX": "https://private/simple"}, None, False),
        ({}, "[install]\nconstraint = c.txt\n", False),
        # Output, caching and connection settings are harmless to drop.
        ({"PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_CACHE_DIR": "1",
          "PIP_DEFAULT_TIMEOUT": "60", "PIP_TRUSTED_HOST": "mirror"}, None, True),
        ({}, "[global]\nprogress-bar = off\nretries = 5\n", True),
        # Sections for other pip commands do not affect `pip install`.
        ({}, "[list]\nformat = columns\n[freeze]\nexclude = pip\n", True),
        ({}, "[list]\nformat = columns\n[install]\nonly-binary = :all:\n", False),
        ({}, "[global]\nno-index = off\n", True),
        ({"UV_DEFAULT_INDEX": "https://private/simple"},
         "[global]\nno-index = 0\nindex-url = https://private/simple\n", True),
        ({}, "[global]\nno-index = true\nfind-links = /wheels\n", False),
        ({}, "[global]\ntimeout = 60\n", True),
        ({}, "not an ini file\n", False),
    ],
)
def test_uv_fallback_refuses_when_pip_has_an_index_uv_cannot_see(
    tmp_path, monkeypatch, capsys, env, config_text, uses_uv,
):
    # uv never reads pip's index settings; falling back would resolve a
    # private package name against public PyPI.
    from plugin.neko_plugin_cli.commands import deps_cmd

    for name, value in env.items():
        monkeypatch.setenv(name, value)
    if config_text is not None:
        config = tmp_path / "pip.ini"
        config.write_text(config_text, encoding="utf-8")
        monkeypatch.setattr(deps_cmd, "_pip_config_files", lambda target: [config])
    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: "uv" if name == "uv" else None)
    calls = []
    monkeypatch.setattr(deps_cmd.subprocess, "run", _missing_pip_then_uv(calls))

    result = deps_cmd._pip_install_to_vendor(
        ["private-pkg"], vendor_dir=tmp_path / "vendor", python="target-python",
    )

    assert result == (0 if uses_uv else 1)
    assert [_cmd_name(command) for command in calls] == (
        ["target-python", "uv"] if uses_uv else ["target-python"]
    )
    if not uses_uv:
        assert "uv does not read" in capsys.readouterr().err


@pytest.mark.parametrize("pip_no_index", [True, False])
def test_pip_no_index_is_passed_to_uv_as_the_flag(tmp_path, monkeypatch, pip_no_index):
    # uv binds no environment variable to --no-index, so only the flag keeps
    # uv off PyPI.
    from plugin.neko_plugin_cli.commands import deps_cmd

    if pip_no_index:
        monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: "uv" if name == "uv" else None)
    calls = []
    monkeypatch.setattr(deps_cmd.subprocess, "run", _missing_pip_then_uv(calls))

    assert deps_cmd._pip_install_to_vendor(
        ["pkg"], vendor_dir=tmp_path / "vendor", python="target-python",
    ) == 0
    uv_command = calls[-1]
    assert _cmd_name(uv_command) == "uv"
    assert ("--no-index" in uv_command) is pip_no_index


def test_pip_config_file_devnull_disables_config_files(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    config = tmp_path / "pip.ini"
    config.write_text("[global]\nindex-url = https://private/simple\n", encoding="utf-8")
    monkeypatch.setattr(deps_cmd, "_pip_config_files", lambda target: [config])
    assert deps_cmd._pip_settings(_target()) == {"index-url": [str(config)]}
    monkeypatch.setenv("PIP_CONFIG_FILE", os.devnull)
    assert deps_cmd._pip_settings(_target()) == {}


def test_no_module_named_pip_inside_a_real_pip_log_is_not_missing_pip(tmp_path, monkeypatch):
    # pip ran and failed; a build step merely printed the same words.
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: "uv" if name == "uv" else None)
    monkeypatch.setattr(deps_cmd, "_probe_target", lambda python: _target(has_pip=True))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 1,
            stdout="Collecting pkg\n  Building wheel\n  No module named pip\nerror: subprocess failed\n",
        )

    monkeypatch.setattr(deps_cmd.subprocess, "run", run)
    assert deps_cmd._pip_install_to_vendor(
        ["pkg"], vendor_dir=tmp_path / "vendor", python="target-python",
    ) == 1
    assert [_cmd_name(command) for command in calls] == ["target-python"]


@pytest.mark.parametrize("failing_installer", ["pip", "uv"])
@pytest.mark.parametrize("error_type", [FileNotFoundError, PermissionError])
def test_installer_start_failure_is_not_missing_pip(
    tmp_path, monkeypatch, capsys, failing_installer, error_type,
):
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: "uv" if name == "uv" else None)

    def run(command, **kwargs):
        if _cmd_name(command) == "uv" or failing_installer == "pip":
            raise error_type("installer cannot execute")
        return subprocess.CompletedProcess(command, 1, stdout="No module named pip")

    monkeypatch.setattr(deps_cmd.subprocess, "run", run)
    assert deps_cmd._pip_install_to_vendor(
        ["httpx"], vendor_dir=tmp_path / "vendor", python="missing-python",
    ) == 1
    error = capsys.readouterr().err
    label = "target Python 'missing-python'" if failing_installer == "pip" else "uv pip install"
    assert f"{label} could not start" in error
    assert "installer cannot execute" in error
    assert "ensurepip" not in error


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


@pytest.mark.parametrize(
    ("env", "blocked"),
    [
        # "off" is a real value (a file name) for non-boolean options.
        ({"PIP_CONSTRAINT": "off"}, True),
        ({"PIP_FIND_LINKS": "0"}, True),
        # Boolean options switched off are not set at all.
        ({"PIP_NO_DEPS": "false"}, False),
        ({"PIP_PRE": "0"}, False),
        ({"PIP_NO_DEPS": "1"}, True),
        ({"PIP_NO_CLEAN": "false", "PIP_UPGRADE": "off"}, False),
        # store_false "no-*" flags: pip sets build_isolation=False, i.e. a
        # false value turns isolation off, so the setting is active.
        ({"PIP_NO_BUILD_ISOLATION": "false"}, True),
        ({"PIP_NO_COMPILE": "0"}, True),
        # ... but one that only silences a warning stays harmless.
        ({"PIP_NO_WARN_CONFLICTS": "false"}, False),
        # use-pep517 defaults to unset: false is a policy (legacy builds, or
        # refusing a declared backend) that uv would not follow.
        ({"PIP_USE_PEP517": "false"}, True),
    ],
)
def test_false_values_only_switch_off_boolean_pip_options(tmp_path, monkeypatch, env, blocked):
    from plugin.neko_plugin_cli.commands import deps_cmd

    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: "uv" if name == "uv" else None)
    calls = []
    monkeypatch.setattr(deps_cmd.subprocess, "run", _missing_pip_then_uv(calls))

    result = deps_cmd._pip_install_to_vendor(
        ["pkg"], vendor_dir=tmp_path / "vendor", python="target-python",
    )
    assert result == (1 if blocked else 0)


@pytest.mark.parametrize("in_venv", [True, False])
def test_require_virtualenv_is_honored_like_pip(tmp_path, monkeypatch, capsys, in_venv):
    # pip refuses to install outside a venv; uv would not check.
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.setenv("PIP_REQUIRE_VIRTUALENV", "true")
    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: "uv" if name == "uv" else None)
    monkeypatch.setattr(deps_cmd, "_probe_target", lambda python: _target(in_venv=in_venv))
    calls = []
    monkeypatch.setattr(deps_cmd.subprocess, "run", _missing_pip_then_uv(calls))
    result = deps_cmd._pip_install_to_vendor(
        ["pkg"], vendor_dir=tmp_path / "vendor", python="target-python",
    )
    assert result == (0 if in_venv else 1)
    if not in_venv:
        assert "not a virtual environment" in capsys.readouterr().err
        assert all(_cmd_name(command) != "uv" for command in calls)


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


def test_probe_asks_the_target_itself(tmp_path, monkeypatch):
    # A pyenv/asdf shim or wrapper does not live in the environment it runs,
    # and may export PIP_* before exec'ing the real interpreter.
    import json

    from plugin.neko_plugin_cli.commands import deps_cmd

    report = {
        "has_pip": False,
        "prefix": str(tmp_path / "real-env"),
        "in_venv": True,
        "env": {"PIP_INDEX_URL": "https://private/simple"},
        "cwd": str(tmp_path / "launcher-dir"),
        "home": str(tmp_path / "target-home"),
    }

    def run(command, **kwargs):
        assert command[1] == "-c"
        # A sitecustomize banner may precede the JSON line.
        return subprocess.CompletedProcess(command, 0, stdout="hello from sitecustomize\n" + json.dumps(report) + "\n")

    monkeypatch.setattr(deps_cmd.subprocess, "run", run)
    target = real_probe_target(str(tmp_path / "shims" / "python"))
    assert target == deps_cmd._TargetPython(
        has_pip=False,
        prefix=tmp_path / "real-env",
        in_venv=True,
        env={"PIP_INDEX_URL": "https://private/simple"},
        cwd=tmp_path / "launcher-dir",
        home=tmp_path / "target-home",
    )


def test_relative_config_bases_resolve_from_the_targets_cwd(tmp_path, monkeypatch):
    # A launcher may cd and export a relative XDG_CONFIG_HOME / HOME; pip
    # resolves those from its own cwd, so must the scan.
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.setattr(deps_cmd, "_pip_config_files", real_pip_config_files)
    launcher_dir = tmp_path / "launcher-dir"
    if sys.platform == "win32":
        env = {"APPDATA": "appdata", "USERPROFILE": "profile"}
        config = launcher_dir / "appdata" / "pip" / "pip.ini"
    else:
        env = {"XDG_CONFIG_HOME": "xdg", "HOME": "home"}
        config = launcher_dir / "xdg" / "pip" / "pip.conf"
    config.parent.mkdir(parents=True)
    config.write_text("[global]\nindex-url = https://private/simple\n", encoding="utf-8")
    # The target expands a relative HOME to a relative "~" as well.
    target = _target(env=env, cwd=launcher_dir, home=Path(env.get("HOME") or env["USERPROFILE"]))

    assert config in real_pip_config_files(target)
    assert deps_cmd._pip_settings(target) == {"index-url": [str(config)]}


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


def test_home_config_comes_from_the_targets_own_home(tmp_path, monkeypatch):
    # The target expands "~" itself (a launcher may drop HOME / USERPROFILE,
    # leaving HOMEPATH or the account record); this process's home may be
    # another directory altogether.
    monkeypatch.setenv("HOME", str(tmp_path / "parent-home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "parent-home"))
    home = tmp_path / "target-home"
    if sys.platform == "win32":
        expected = home / "pip" / "pip.ini"
    else:
        expected = home / ".pip" / "pip.conf"
    target = _target(env={}, cwd=tmp_path / "launcher-dir", home=home)

    assert expected in real_pip_config_files(target)
    assert not any("parent-home" in str(path) for path in real_pip_config_files(target))


def test_relative_pip_config_file_resolves_from_the_targets_cwd(tmp_path, monkeypatch):
    # pip reads a relative PIP_CONFIG_FILE from its own working directory,
    # which a launcher may have changed.
    from plugin.neko_plugin_cli.commands import deps_cmd

    launcher_dir = tmp_path / "launcher-dir"
    launcher_dir.mkdir()
    (launcher_dir / "relative-pip.conf").write_text(
        "[global]\nindex-url = https://private/simple\n", encoding="utf-8"
    )
    target = _target(env={"PIP_CONFIG_FILE": "relative-pip.conf"}, cwd=launcher_dir)

    assert deps_cmd._pip_settings(target) == {
        "index-url": [str(launcher_dir / "relative-pip.conf")]
    }


@pytest.mark.parametrize(
    "outcome",
    ["start-failure", "exit-1", "no-json"],
)
def test_probe_that_cannot_answer_returns_none(tmp_path, monkeypatch, outcome):
    from plugin.neko_plugin_cli.commands import deps_cmd

    def run(command, **kwargs):
        if outcome == "start-failure":
            raise FileNotFoundError("no interpreter")
        if outcome == "exit-1":
            return subprocess.CompletedProcess(command, 1, stdout="")
        return subprocess.CompletedProcess(command, 0, stdout="just a banner\n")

    monkeypatch.setattr(deps_cmd.subprocess, "run", run)
    assert real_probe_target("python") is None


def test_unknown_pip_availability_does_not_fall_back(tmp_path, monkeypatch, capsys):
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: "uv" if name == "uv" else None)
    monkeypatch.setattr(deps_cmd, "_probe_target", lambda python: None)
    calls = []
    monkeypatch.setattr(deps_cmd.subprocess, "run", _missing_pip_then_uv(calls))

    assert deps_cmd._pip_install_to_vendor(
        ["pkg"], vendor_dir=tmp_path / "vendor", python="target-python",
    ) == 1
    assert [_cmd_name(command) for command in calls] == ["target-python"]
    assert "pip install failed" in capsys.readouterr().err


def test_pip_settings_come_from_the_targets_environment(tmp_path, monkeypatch, capsys):
    # The CLI's own environment is clean; the target's launcher sets a
    # private index that uv (run from the CLI's environment) would miss.
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: "uv" if name == "uv" else None)
    monkeypatch.setattr(
        deps_cmd,
        "_probe_target",
        lambda python: _target(env={"PIP_INDEX_URL": "https://private/simple"}),
    )
    calls = []
    monkeypatch.setattr(deps_cmd.subprocess, "run", _missing_pip_then_uv(calls))

    assert deps_cmd._pip_install_to_vendor(
        ["pkg"], vendor_dir=tmp_path / "vendor", python="target-python",
    ) == 1
    assert [_cmd_name(command) for command in calls] == ["target-python"]
    assert "index-url from PIP_INDEX_URL" in capsys.readouterr().err


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


def test_uv_fallback_leaves_uv_index_configuration_alone(tmp_path, monkeypatch):
    # UV_* environment variables outrank uv.toml / [tool.uv], so injecting
    # pip's mirror would silently override a user's configured uv index.
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: "uv" if name == "uv" else None)
    monkeypatch.setenv("PIP_INDEX_URL", "https://mirror/simple")
    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://uv/simple")
    calls = []

    def run(command, **kwargs):
        calls.append(kwargs)
        if _cmd_name(command) == "uv":
            return subprocess.CompletedProcess(command, 0, stdout="ok")
        return subprocess.CompletedProcess(command, 1, stdout="No module named pip")

    monkeypatch.setattr(deps_cmd.subprocess, "run", run)
    assert deps_cmd._pip_install_to_vendor(
        ["httpx"], vendor_dir=tmp_path / "vendor", python="target-python",
    ) == 0
    assert len(calls) == 2
    # pip runs with the inherited environment; uv gets it unchanged apart
    # from pinning SSL_CERT_FILE -- no UV_* index variables injected.
    assert calls[0].get("env") is None
    assert calls[1]["env"] == dict(os.environ)


def test_symlinked_vendor_is_refused_before_any_change(tmp_path, monkeypatch, capsys):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    real_vendor = tmp_path / "other_disk_vendor"
    real_vendor.mkdir()
    (real_vendor / "old.py").write_text("keep")
    link = plugin_dir / "vendor"
    if sys.platform == "win32":
        # Junctions need no privilege, unlike symlinks.
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real_vendor)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(real_vendor, target_is_directory=True)
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("installer must not run for a symlinked vendor"),
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 1
    assert os.readlink(link)
    assert (real_vendor / "old.py").read_text() == "keep"
    assert not list(plugin_dir.glob(".vendor.*"))
    assert "is a symlink" in capsys.readouterr().err


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
    monkeypatch.setattr(deps_cmd, "_probe_target", lambda python: _target(has_pip=True))

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 1
    assert [p for p in plugin_dir.glob(".vendor.staging-*") if p.is_dir()]


def test_uv_runs_in_the_targets_working_directory(tmp_path, monkeypatch):
    # The pip attempt ran through the launcher in its cwd; relative
    # requirements ("pkg @ file:./pkg") must resolve the same under uv.
    from plugin.neko_plugin_cli.commands import deps_cmd

    launcher_dir = tmp_path / "launcher-dir"
    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: "uv" if name == "uv" else None)
    monkeypatch.setattr(deps_cmd, "_probe_target", lambda python: _target(cwd=launcher_dir))
    seen = []

    def run(command, **kwargs):
        seen.append((_cmd_name(command), kwargs.get("cwd")))
        if _cmd_name(command) == "uv":
            return subprocess.CompletedProcess(command, 0, stdout="ok")
        return subprocess.CompletedProcess(command, 1, stdout="No module named pip")

    monkeypatch.setattr(deps_cmd.subprocess, "run", run)
    assert deps_cmd._pip_install_to_vendor(
        ["pkg @ file:./pkg"], vendor_dir=tmp_path / "vendor", python="target-python",
    ) == 0
    assert ("uv", launcher_dir) in seen


@pytest.mark.parametrize(
    ("env", "uses_uv"),
    [
        # pip's proxy is a policy (e.g. a filtering proxy); uv honors only the
        # standard proxy variables, per URL scheme, so both must be covered.
        ({"PIP_PROXY": "http://corp-proxy:3128"}, False),
        ({"PIP_PROXY": "http://corp-proxy:3128", "ALL_PROXY": "http://corp-proxy:3128"}, True),
        ({"PIP_PROXY": "http://corp-proxy:3128", "HTTPS_PROXY": "http://corp-proxy:3128",
          "HTTP_PROXY": "http://corp-proxy:3128"}, True),
        # An http index or direct reference would go direct with HTTPS_PROXY
        # alone, and an https one with HTTP_PROXY alone.
        ({"PIP_PROXY": "http://corp-proxy:3128", "HTTPS_PROXY": "http://corp-proxy:3128"}, False),
        ({"PIP_PROXY": "http://corp-proxy:3128", "HTTP_PROXY": "http://corp-proxy:3128"}, False),
        # pip's explicit proxy ignores NO_PROXY; uv would bypass the proxy for
        # the listed hosts, and which hosts uv contacts can not be listed.
        ({"PIP_PROXY": "http://corp-proxy:3128", "HTTPS_PROXY": "http://corp-proxy:3128",
          "NO_PROXY": "*"}, False),
        ({"PIP_PROXY": "http://corp-proxy:3128", "HTTPS_PROXY": "http://corp-proxy:3128",
          "NO_PROXY": "localhost"}, False),
        ({"PIP_PROXY": "http://corp-proxy:3128", "ALL_PROXY": "http://corp-proxy:3128",
          "NO_PROXY": " "}, True),
        # pip's cert replaces the default CA bundle; uv would otherwise trust
        # its bundled roots, and ignores an SSL_CERT_FILE that does not exist.
        ({"PIP_CERT": "/corp/ca.pem"}, False),
        ({"PIP_CERT": "/corp/ca.pem", "SSL_CERT_FILE": "<ca>"}, True),
        ({"PIP_CERT": "/corp/ca.pem", "SSL_CERT_FILE": "/missing/ca.pem"}, False),
        # uv treats an empty or malformed bundle like an unset one.
        ({"PIP_CERT": "/corp/ca.pem", "SSL_CERT_FILE": "<malformed>"}, False),
        ({"PIP_CERT": "/corp/ca.pem", "SSL_CERT_FILE": "<empty>"}, False),
        # An index may serve other packages to anonymous clients; uv ignores
        # an unusable SSL_CLIENT_CERT with only a warning.
        ({"PIP_CLIENT_CERT": "/corp/client.pem"}, False),
        ({"PIP_CLIENT_CERT": "/corp/client.pem", "SSL_CLIENT_CERT": "<identity>"}, True),
        ({"PIP_CLIENT_CERT": "/corp/client.pem", "SSL_CLIENT_CERT": "/missing/client.pem"}, False),
        # Both installers are given --target/--upgrade explicitly, and pip
        # --no-user (uv's --target never installs to the user site).
        ({"PIP_TARGET": "/elsewhere", "PIP_UPGRADE": "1"}, True),
        ({"PIP_NO_USER": "1"}, True),
    ],
)
def test_proxy_and_overridden_pip_settings(tmp_path, monkeypatch, env, uses_uv):
    from plugin.neko_plugin_cli.commands import deps_cmd

    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy",
                 "NO_PROXY", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR", "SSL_CLIENT_CERT"):
        monkeypatch.delenv(name, raising=False)
    malformed = tmp_path / "malformed.pem"
    malformed.write_text("pem")
    empty = tmp_path / "empty.pem"
    empty.write_text("")
    placeholders = {
        "<ca>": str(_write_client_identity(tmp_path / "ca.pem", key=None)),
        "<malformed>": str(malformed),
        "<empty>": str(empty),
        "<identity>": str(_write_client_identity(tmp_path / "client.pem")),
    }
    for name, value in env.items():
        monkeypatch.setenv(name, placeholders.get(value, value))
    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: "uv" if name == "uv" else None)
    calls = []
    monkeypatch.setattr(deps_cmd.subprocess, "run", _missing_pip_then_uv(calls))

    assert deps_cmd._pip_install_to_vendor(
        ["pkg"], vendor_dir=tmp_path / "vendor", python="target-python",
    ) == (0 if uses_uv else 1)


def test_uv_gets_paths_pinned_to_this_processes_cwd(tmp_path, monkeypatch):
    # uv runs in target.cwd; a relative --python or --target must still mean
    # what it meant here, where the pip attempt resolved it.
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: "uv" if name == "uv" else None)
    monkeypatch.setattr(
        deps_cmd, "_probe_target", lambda python: _target(cwd=tmp_path / "launcher-dir")
    )
    calls = []
    monkeypatch.setattr(deps_cmd.subprocess, "run", _missing_pip_then_uv(calls))
    relative_python = os.path.join("env", "bin", "python")

    assert deps_cmd._pip_install_to_vendor(
        ["pkg"], vendor_dir=Path("staging"), python=relative_python,
    ) == 0
    uv_command = calls[-1]
    assert uv_command[uv_command.index("--python") + 1] == str(tmp_path / relative_python)
    assert uv_command[uv_command.index("--target") + 1] == str(tmp_path / "staging")


def _write_client_identity(
    path: Path, *, cert: bool = True, key: str | None = "matching", encrypted: bool = False,
) -> Path:
    """A PEM file with a self-signed certificate and/or a private key."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    own_key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "neko-plugin test client")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(own_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(own_key, hashes.SHA256())
    )
    pem = b""
    if cert:
        pem += certificate.public_bytes(serialization.Encoding.PEM)
    if key is not None:
        written = own_key if key == "matching" else ec.generate_private_key(ec.SECP256R1())
        pem += written.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"secret")
            if encrypted
            else serialization.NoEncryption(),
        )
    path.write_bytes(pem)
    return path


@pytest.mark.parametrize(
    "case", ["not-pem", "cert-only", "key-only", "other-key", "encrypted-key", "directory"],
)
def test_unusable_client_cert_does_not_cover_pips(tmp_path, monkeypatch, case):
    # uv warns about an identity it can not load and connects anonymously.
    from plugin.neko_plugin_cli.commands import deps_cmd

    path = tmp_path / "client.pem"
    if case == "not-pem":
        path.write_text("pem")
    elif case == "cert-only":
        _write_client_identity(path, key=None)
    elif case == "key-only":
        _write_client_identity(path, cert=False)
    elif case == "other-key":
        _write_client_identity(path, key="other")
    elif case == "encrypted-key":
        _write_client_identity(path, encrypted=True)
    else:
        path.mkdir()
    monkeypatch.setenv("PIP_CLIENT_CERT", "/corp/client.pem")
    monkeypatch.setenv("SSL_CLIENT_CERT", str(path))
    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: "uv" if name == "uv" else None)
    calls = []
    monkeypatch.setattr(deps_cmd.subprocess, "run", _missing_pip_then_uv(calls))

    assert deps_cmd._pip_install_to_vendor(
        ["pkg"], vendor_dir=tmp_path / "vendor", python="target-python",
    ) == 1
    assert all(_cmd_name(command) != "uv" for command in calls)


def test_uv_gets_an_absolute_client_cert(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.chdir(tmp_path)
    (tmp_path / "client.pem").write_text("pem")
    monkeypatch.setenv("SSL_CLIENT_CERT", "client.pem")
    assert deps_cmd._uv_env()["SSL_CLIENT_CERT"] == str(tmp_path / "client.pem")


@pytest.mark.parametrize("clean", [False, True])
def test_sync_refuses_a_mounted_vendor_root(tmp_path, monkeypatch, capsys, clean):
    # A mount point cannot be renamed to the backup name; copying it into
    # staging would also follow it into the mounted tree.
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    (vendor / "external.dat").write_text("keep")
    monkeypatch.setattr(deps_cmd, "_is_mount_point", lambda p: Path(p) == vendor)
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("installer must not run"),
    )

    assert handle_sync(
        TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path, clean=clean)
    ) == 1
    assert (vendor / "external.dat").read_text() == "keep"
    assert not list(plugin_dir.glob(".vendor.*"))
    assert "is a mount point" in capsys.readouterr().err


def test_ssl_cert_dir_does_not_cover_pips_cert(tmp_path, monkeypatch):
    # uv's default TLS backend does not read SSL_CERT_DIR.
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setenv("PIP_CERT", "/corp/ca.pem")
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))
    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: "uv" if name == "uv" else None)
    calls = []
    monkeypatch.setattr(deps_cmd.subprocess, "run", _missing_pip_then_uv(calls))

    assert deps_cmd._pip_install_to_vendor(
        ["pkg"], vendor_dir=tmp_path / "vendor", python="target-python",
    ) == 1


def test_uv_gets_an_absolute_ca_file_and_an_absolute_uv(tmp_path, monkeypatch):
    # uv runs in target.cwd: a relative SSL_CERT_FILE (checked here) would
    # not exist there and uv would silently use its bundled roots; a uv found
    # through a relative PATH entry would be another program there.
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.chdir(tmp_path)
    _write_client_identity(tmp_path / "ca.pem", key=None)
    monkeypatch.setenv("PIP_CERT", "/corp/ca.pem")
    monkeypatch.setenv("SSL_CERT_FILE", "ca.pem")
    relative_uv = os.path.join("bin", "uv")
    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: relative_uv if name == "uv" else None)
    monkeypatch.setattr(deps_cmd, "_probe_target", lambda python: _target(cwd=tmp_path / "elsewhere"))
    seen = []

    def run(command, **kwargs):
        seen.append((command, kwargs))
        if _cmd_name(command) == "uv":
            return subprocess.CompletedProcess(command, 0, stdout="ok")
        return subprocess.CompletedProcess(command, 1, stdout="No module named pip")

    monkeypatch.setattr(deps_cmd.subprocess, "run", run)
    assert deps_cmd._pip_install_to_vendor(
        ["pkg"], vendor_dir=tmp_path / "vendor", python="target-python",
    ) == 0
    uv_command, uv_kwargs = seen[-1]
    assert uv_command[0] == str(tmp_path / relative_uv)
    assert uv_kwargs["env"]["SSL_CERT_FILE"] == str(tmp_path / "ca.pem")


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


def test_posix_pip_environment_names_are_case_sensitive(monkeypatch):
    # pip ignores "pip_constraint" on POSIX; Windows names are case-insensitive.
    from plugin.neko_plugin_cli.commands import deps_cmd

    target = _target(env={"pip_constraint": "notes"})
    monkeypatch.setattr(deps_cmd.sys, "platform", "linux")
    assert deps_cmd._pip_settings(target) == {}
    monkeypatch.setattr(deps_cmd.sys, "platform", "win32")
    assert deps_cmd._pip_settings(target) == {"constraint": ["pip_constraint"]}


def test_uv_from_uv_run_is_preferred_over_path(tmp_path, monkeypatch):
    # `uv run` exports UV; uv may not be on PATH (pipx, `py -m uv`).
    from plugin.neko_plugin_cli.commands import deps_cmd

    uv_exe = tmp_path / "tools" / "uv.exe"
    uv_exe.parent.mkdir()
    uv_exe.write_text("")
    monkeypatch.setenv("UV", str(uv_exe))
    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: None)
    calls = []
    monkeypatch.setattr(deps_cmd.subprocess, "run", _missing_pip_then_uv(calls))

    assert deps_cmd._pip_install_to_vendor(
        ["pkg"], vendor_dir=tmp_path / "vendor", python="target-python",
    ) == 0
    assert calls[-1][0] == str(uv_exe)


@pytest.mark.parametrize("uv_value", ["uv", "/no/such/uv"])
def test_bare_or_missing_uv_value_falls_back_to_path(tmp_path, monkeypatch, uv_value):
    # UV=uv is a PATH lookup, not ./uv; a UV naming no file falls back too.
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.chdir(tmp_path)
    on_path = tmp_path / "bin" / "uv.exe"
    on_path.parent.mkdir()
    on_path.write_text("")
    monkeypatch.setenv("UV", uv_value)
    monkeypatch.setattr(
        deps_cmd.shutil, "which", lambda name: str(on_path) if name == "uv" else None
    )
    calls = []
    monkeypatch.setattr(deps_cmd.subprocess, "run", _missing_pip_then_uv(calls))

    assert deps_cmd._pip_install_to_vendor(
        ["pkg"], vendor_dir=tmp_path / "vendor", python="target-python",
    ) == 0
    assert calls[-1][0] == str(on_path)


def test_bare_python_name_is_resolved_through_path_here(tmp_path, monkeypatch):
    # A relative PATH entry would mean something else in target.cwd, so the
    # bare name is resolved in this process before uv changes directory.
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.chdir(tmp_path)
    found = os.path.join("bin", "python3")
    monkeypatch.setattr(deps_cmd.shutil, "which", lambda name: found if name == "python3" else None)
    assert deps_cmd._absolute_if_path("python3") == str(tmp_path / found)
    # Not found here: leave it to uv's own lookup.
    assert deps_cmd._absolute_if_path("python9") == "python9"


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

    def test_uv_installs_when_target_python_has_no_pip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin_dir = TestHandleSync()._make_plugin(tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.shutil.which",
            lambda name: "uv.exe" if name == "uv" else None,
        )

        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            if command[0] == "target-python":
                return subprocess.CompletedProcess(
                    command, 1, stdout="target-python: No module named pip\n"
                )
            return subprocess.CompletedProcess(command, 0, stdout="ok\n")

        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.subprocess.run", fake_run
        )

        assert handle_sync(self._args(plugin_dir, tmp_path)) == 0
        assert len(calls) == 2
        assert calls[0][:3] == ["target-python", "-m", "pip"]
        assert [_cmd_name(calls[1]), *calls[1][1:4]] == ["uv", "pip", "install", "--python"]
        assert "target-python" in calls[1]
        assert "--target" in calls[1]

    def test_prefers_target_python_pip_even_when_uv_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # uv ignores pip.conf / PIP_INDEX_URL, so pip stays first whenever the
        # target interpreter has it.
        plugin_dir = TestHandleSync()._make_plugin(tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.shutil.which",
            lambda name: "uv.exe" if name == "uv" else None,
        )

        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="ok\n")

        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.subprocess.run", fake_run
        )

        assert handle_sync(self._args(plugin_dir, tmp_path)) == 0
        assert len(calls) == 1
        assert calls[0][:3] == ["target-python", "-m", "pip"]
        assert "--no-user" in calls[0]

    def test_pip_failure_other_than_missing_pip_does_not_try_uv(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        plugin_dir = TestHandleSync()._make_plugin(tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.shutil.which",
            lambda name: "uv.exe" if name == "uv" else None,
        )
        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd._probe_target",
            lambda python: _target(has_pip=True),
        )

        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 1, stdout="No matching distribution\n")

        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.subprocess.run", fake_run
        )

        assert handle_sync(self._args(plugin_dir, tmp_path)) == 1
        assert len(calls) == 1
        assert "pip install failed (exit 1)" in capsys.readouterr().err

    def test_reports_uv_failure_after_missing_pip(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        plugin_dir = TestHandleSync()._make_plugin(tmp_path)
        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.shutil.which",
            lambda name: "uv.exe" if name == "uv" else None,
        )

        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if command[0] == "target-python":
                return subprocess.CompletedProcess(command, 1, stdout="No module named pip\n")
            return subprocess.CompletedProcess(command, 2, stdout="uv resolver error\n")

        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.subprocess.run", fake_run
        )

        assert handle_sync(self._args(plugin_dir, tmp_path)) == 1
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
        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.shutil.which",
            lambda _: None,
        )
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
        assert "ensurepip" in error

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
