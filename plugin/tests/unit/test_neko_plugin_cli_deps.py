"""Unit tests for the neko-plugin sync command."""

from __future__ import annotations

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


@pytest.mark.parametrize("uv", [None, "uv"])
@pytest.mark.parametrize("error_type", [FileNotFoundError, PermissionError])
def test_installer_start_failure_is_not_missing_pip(
    tmp_path, monkeypatch, capsys, uv, error_type,
):
    from plugin.neko_plugin_cli.commands import deps_cmd

    monkeypatch.setattr(deps_cmd.shutil, "which", lambda _: uv)

    def fail(*args, **kwargs):
        raise error_type("interpreter cannot execute")

    monkeypatch.setattr(deps_cmd.subprocess, "run", fail)
    assert deps_cmd._pip_install_to_vendor(
        ["httpx"], vendor_dir=tmp_path / "vendor", python="missing-python",
    ) == 1
    error = capsys.readouterr().err
    assert "could not start" in error
    assert "interpreter cannot execute" in error
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
        child = real_run([sys.executable, "-c", child_code, str(plugin_dir)],
                         capture_output=True, text=True, timeout=30)
        assert child.returncode == 1
        assert "sync already in progress" in child.stderr
        target = Path(command[command.index("--target") + 1])
        (target / "fresh.py").write_text("complete")
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    monkeypatch.setattr(deps_cmd.subprocess, "run", install)
    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 0
    assert (plugin_dir / "vendor" / "fresh.py").read_text() == "complete"


def test_failed_recovery_copy_never_exposes_partial_vendor(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    vendor = tmp_path / "vendor"
    staging = tmp_path / ".vendor.staging-test"
    vendor.mkdir()
    staging.mkdir()
    (vendor / "old.py").write_text("keep")
    real_replace = Path.replace

    def fail_replace(source, target):
        if source == staging:
            raise PermissionError("locked")
        return real_replace(source, target)

    def fail_copy(source, target, **kwargs):
        Path(target).mkdir(exist_ok=True)
        (Path(target) / "partial.py").write_text("partial")
        raise OSError("recovery copy interrupted")

    monkeypatch.setattr(Path, "replace", fail_replace)
    monkeypatch.setattr(deps_cmd.shutil, "copytree", fail_copy)
    assert _replace_vendor(vendor, staging) is False
    assert not vendor.exists()
    assert not list(tmp_path.glob(".vendor.restore-*"))
    backup, = tmp_path.glob(".vendor.backup-*")
    assert (backup / "old.py").read_text() == "keep"


def test_successful_retry_cleans_retained_backup(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands import deps_cmd

    plugin_dir = TestHandleSync()._make_plugin(tmp_path)
    backup = plugin_dir / ".vendor.backup-previous"
    backup.mkdir()
    (backup / "old.py").write_text("backup")
    monkeypatch.setattr(deps_cmd.subprocess, "run",
                        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="ok"))
    assert handle_sync(TestTransactionalDependencyInstall()._args(plugin_dir, tmp_path)) == 0
    assert not backup.exists()


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
    assert (vendor / "linked.txt").readlink() == outside
    assert (vendor / "dangling.txt").is_symlink()
    assert outside.read_text() == "outside"


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
            return subprocess.CompletedProcess(command, 0, stdout="ok\\n")

        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.subprocess.run", fake_run
        )

        assert handle_sync(self._args(plugin_dir, tmp_path)) == 0
        assert calls
        assert calls[0][:4] == ["uv.exe", "pip", "install", "--python"]
        assert "target-python" in calls[0]
        assert "--target" in calls[0]

    def test_falls_back_to_target_python_pip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin_dir = TestHandleSync()._make_plugin(tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.shutil.which",
            lambda _: None,
        )

        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="ok\\n")

        monkeypatch.setattr(
            "plugin.neko_plugin_cli.commands.deps_cmd.subprocess.run", fake_run
        )

        assert handle_sync(self._args(plugin_dir, tmp_path)) == 0
        assert calls[0][:3] == ["target-python", "-m", "pip"]
        assert "--no-user" in calls[0]

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
        backups = list(tmp_path.glob(".vendor.backup-*"))
        error = capsys.readouterr().err
        if permission_error:
            assert len(backups) == 1
            assert (backups[0] / "old.py").read_text(encoding="utf-8") == "keep"
            assert "files are in use" in error
        else:
            assert backups == []
            assert "rename failed" in error
