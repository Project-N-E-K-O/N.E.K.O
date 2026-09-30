"""Unit tests for the neko-plugin sync command."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch
import sys

import pytest

from plugin.neko_plugin_cli.commands.deps_cmd import (
    _clean_vendor,
    _filter_external,
    _read_dependencies,
    _replace_vendor,
    handle_sync,
)


@pytest.fixture(autouse=True)
def _no_host_package_index_config(monkeypatch):
    """Keep the developer's own pip/uv index settings out of these tests."""
    from plugin.neko_plugin_cli.commands import deps_cmd

    for name in (*deps_cmd._PIP_INDEX_ENV, *deps_cmd._UV_INDEX_ENV, "PIP_CONFIG_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(deps_cmd, "_pip_config_files", lambda python: [])


def _missing_pip_then_uv(calls):
    def run(command, **kwargs):
        calls.append(command)
        if command[0] == "uv":
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
        ({"PIP_FIND_LINKS": "/wheels", "UV_FIND_LINKS": "/wheels", "UV_NO_INDEX": "1"}, None, True),
        ({"PIP_FIND_LINKS": "/wheels", "UV_FIND_LINKS": "/wheels"}, None, True),
        # Extra indexes and find-links leave uv's PyPI on; only UV_NO_INDEX
        # matches pip's no-index.
        ({"PIP_NO_INDEX": "1", "UV_FIND_LINKS": "/wheels"}, None, False),
        ({"PIP_NO_INDEX": "1", "UV_EXTRA_INDEX_URL": "https://private/simple"}, None, False),
        ({"PIP_NO_INDEX": "1", "UV_NO_INDEX": "1", "UV_FIND_LINKS": "/wheels"}, None, True),
        ({"UV_FIND_LINKS": "/wheels"}, "[global]\nno-index = true\nfind-links = /wheels\n", False),
        # Each pip source kind needs a uv setting of the matching kind.
        ({"PIP_INDEX_URL": "https://private/simple", "UV_FIND_LINKS": "/wheels"}, None, False),
        ({"PIP_FIND_LINKS": "/wheels", "UV_DEFAULT_INDEX": "https://private/simple"}, None, False),
        ({"PIP_INDEX_URL": "https://private/simple", "UV_NO_INDEX": "1"}, None, True),
        ({"PIP_INDEX_URL": "https://private/simple", "PIP_FIND_LINKS": "/wheels",
          "UV_INDEX": "https://private/simple"}, None, False),
        ({"PIP_INDEX_URL": "https://private/simple", "PIP_FIND_LINKS": "/wheels",
          "UV_INDEX": "https://private/simple", "UV_FIND_LINKS": "/wheels"}, None, True),
        # An explicitly disabled no-index is not a restriction.
        ({"PIP_NO_INDEX": "false"}, None, True),
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
        monkeypatch.setattr(deps_cmd, "_pip_config_files", lambda python: [config])
    monkeypatch.setattr(deps_cmd.shutil, "which", lambda _: "uv")
    calls = []
    monkeypatch.setattr(deps_cmd.subprocess, "run", _missing_pip_then_uv(calls))

    result = deps_cmd._pip_install_to_vendor(
        ["private-pkg"], vendor_dir=tmp_path / "vendor", python="target-python",
    )

    assert result == (0 if uses_uv else 1)
    assert [command[0] for command in calls] == (
        ["target-python", "uv"] if uses_uv else ["target-python"]
    )
    if not uses_uv:
        assert "uv does not read" in capsys.readouterr().err


def test_pip_config_file_devnull_disables_config_files(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    config = tmp_path / "pip.ini"
    config.write_text("[global]\nindex-url = https://private/simple\n", encoding="utf-8")
    monkeypatch.setattr(deps_cmd, "_pip_config_files", lambda python: [config])
    assert deps_cmd._pip_package_sources("python") == ([str(config)], {"index"})
    monkeypatch.setenv("PIP_CONFIG_FILE", os.devnull)
    assert deps_cmd._pip_package_sources("python") == ([], set())


@pytest.mark.parametrize("failing_installer", ["pip", "uv"])
@pytest.mark.parametrize("error_type", [FileNotFoundError, PermissionError])
def test_installer_start_failure_is_not_missing_pip(
    tmp_path, monkeypatch, capsys, failing_installer, error_type,
):
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.setattr(deps_cmd.shutil, "which", lambda _: "uv")

    def run(command, **kwargs):
        if command[0] == "uv" or failing_installer == "pip":
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
        if path.name == ".recovery-pending":
            raise PermissionError("marker is locked")
        return real_touch(path, *args, **kwargs)

    monkeypatch.setattr(Path, "touch", fail_marker)
    assert _replace_vendor(vendor, staging) is False

    assert (vendor / "old.py").read_text() == "keep"
    assert not (vendor / "fresh.py").exists()
    assert not list(tmp_path.glob(".vendor.backup-*"))


def test_successful_retry_cleans_retained_backup(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    backup = plugin_dir / ".vendor.backup-previous"
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
    backup = plugin_dir / ".vendor.backup-previous"
    backup.mkdir()
    (backup / "old.py").write_text("backup")
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("installer must not run before recovery"),
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 1
    assert backup.exists()
    assert "recover it or use explicit --clean" in capsys.readouterr().err


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
    backup, = tmp_path.glob(".vendor.backup-*")
    assert (backup / "old.py").read_text() == "keep"
    assert (vendor / "partial.py").read_text() == "partial"
    assert (backup / ".recovery-pending").is_file()
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

    backup, = plugin_dir.glob(".vendor.backup-*")
    assert not vendor.exists()
    assert (backup / "old.py").read_text() == "keep"
    assert (backup / ".recovery-pending").is_file()
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

    monkeypatch.setattr(deps_cmd.shutil, "which", lambda _: "uv")
    monkeypatch.setenv("PIP_INDEX_URL", "https://mirror/simple")
    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://uv/simple")
    calls = []

    def run(command, **kwargs):
        calls.append(kwargs)
        if command[0] == "uv":
            return subprocess.CompletedProcess(command, 0, stdout="ok")
        return subprocess.CompletedProcess(command, 1, stdout="No module named pip")

    monkeypatch.setattr(deps_cmd.subprocess, "run", run)
    assert deps_cmd._pip_install_to_vendor(
        ["httpx"], vendor_dir=tmp_path / "vendor", python="target-python",
    ) == 0
    assert len(calls) == 2
    assert all(kwargs.get("env") is None for kwargs in calls)


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


def test_sync_lock_file_is_per_user(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    monkeypatch.setattr(deps_cmd.os, "getuid", lambda: 4242, raising=False)
    monkeypatch.setattr(deps_cmd, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="ok"),
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 0
    lock, = tmp_path.glob("neko-plugin-sync-*.lock")
    assert lock.name.startswith("neko-plugin-sync-4242-")


def test_non_clean_sync_drops_marker_left_in_vendor(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    (vendor / "old.py").write_text("keep")
    (vendor / ".recovery-pending").touch()
    monkeypatch.setattr(
        deps_cmd.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="ok"),
    )

    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 0
    assert (vendor / "old.py").read_text() == "keep"
    assert not (vendor / ".recovery-pending").exists()


def test_stale_staging_cleanup_failure_warns(tmp_path, monkeypatch, capsys):
    from plugin.neko_plugin_cli.commands import deps_cmd

    stale = tmp_path / ".vendor.staging-killed"
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
    backup = plugin_dir / ".vendor.backup-previous"
    backup.mkdir()
    (backup / "old.py").write_text("backup")
    (backup / ".recovery-pending").touch()
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
    backup = plugin_dir / ".vendor.backup-previous"
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
    backup = plugin_dir / ".vendor.backup-previous"
    backup.mkdir()
    (backup / "old.py").write_text("backup")
    (backup / ".recovery-pending").touch()
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
        assert calls[1][:4] == ["uv.exe", "pip", "install", "--python"]
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
            lambda _: "uv.exe",
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
            lambda _: "uv.exe",
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
            lambda _: "uv.exe",
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
                command, 1, stdout="target-python: No module named pip\\n"
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
            lambda _: "uv",
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
            lambda _: "uv",
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
            lambda _: "uv",
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
            "plugin.neko_plugin_cli.commands.deps_cmd.shutil.which", lambda _: "uv"
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
        assert not (vendor / ".recovery-pending").exists()
        error = capsys.readouterr().err
        if permission_error:
            assert "files are in use" in error
        else:
            assert "rename failed" in error

    def test_sync_removes_staging_left_by_killed_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin_dir = TestHandleSync()._make_plugin(tmp_path)
        stale = plugin_dir / ".vendor.staging-killed"
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
