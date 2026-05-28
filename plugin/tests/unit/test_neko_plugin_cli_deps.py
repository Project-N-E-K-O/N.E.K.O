"""Unit tests for neko-plugin add / sync commands."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from plugin.neko_plugin_cli.commands.deps_cmd import (
    _clean_vendor,
    _filter_external,
    _merge_new_packages,
    _read_dependencies,
    _update_pyproject_dependencies,
    handle_add,
    handle_sync,
)


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

    def test_merge_new_packages_adds(self) -> None:
        existing = ["httpx>=0.27"]
        result = _merge_new_packages(existing, ["pydantic>=2.0"])
        assert "httpx>=0.27" in result
        assert "pydantic>=2.0" in result

    def test_merge_new_packages_replaces_version(self) -> None:
        existing = ["httpx>=0.25"]
        result = _merge_new_packages(existing, ["httpx>=0.27"])
        assert result == ["httpx>=0.27"]

    def test_merge_new_packages_skips_host(self) -> None:
        existing = ["httpx>=0.27"]
        result = _merge_new_packages(existing, ["N.E.K.O>=1.0"])
        assert result == ["httpx>=0.27"]

    def test_update_pyproject_dependencies(self, tmp_path: Path) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "test"\nversion = "1.0"\ndependencies = ["httpx>=0.27"]\n',
            encoding="utf-8",
        )
        _update_pyproject_dependencies(pyproject, ["httpx>=0.27", "pydantic>=2.0"])
        content = pyproject.read_text(encoding="utf-8")
        assert '"httpx>=0.27"' in content
        assert '"pydantic>=2.0"' in content

    def test_update_pyproject_dependencies_preserves_extras_array_bounds(
        self,
        tmp_path: Path,
    ) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "\n".join(
                [
                    "[project]",
                    'name = "test"',
                    "dependencies = [",
                    '  "requests[security]>=2",',
                    "]",
                    "",
                    "[tool.demo]",
                    'value = "keep"',
                    "",
                ]
            ),
            encoding="utf-8",
        )

        _update_pyproject_dependencies(
            pyproject,
            ["requests[security]>=2", "pydantic>=2.0"],
        )

        content = pyproject.read_text(encoding="utf-8")
        assert '"requests[security]>=2"' in content
        assert '"pydantic>=2.0"' in content
        assert '[tool.demo]\nvalue = "keep"' in content

    def test_update_pyproject_dependencies_escapes_marker_quotes(
        self,
        tmp_path: Path,
    ) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "test"\ndependencies = []\n',
            encoding="utf-8",
        )
        dep = 'importlib-metadata; python_version < "3.10"'

        _update_pyproject_dependencies(pyproject, [dep])

        content = pyproject.read_text(encoding="utf-8")
        assert 'python_version < \\"3.10\\"' in content
        assert _read_dependencies(pyproject) == [dep]

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
class TestHandleAdd:
    def _make_plugin(self, tmp_path: Path) -> Path:
        plugin_dir = tmp_path / "my_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            '[plugin]\nid = "my_plugin"\nname = "My Plugin"\nversion = "1.0.0"\n'
            'entry = "plugin.plugins.my_plugin:MyPlugin"\n',
            encoding="utf-8",
        )
        (plugin_dir / "pyproject.toml").write_text(
            '[project]\nname = "my_plugin"\nversion = "1.0.0"\ndependencies = []\n',
            encoding="utf-8",
        )
        return plugin_dir

    def test_add_installs_and_updates_pyproject(self, tmp_path: Path) -> None:
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
                packages=["httpx>=0.27"],
                python="python",
                _defaults=defaults,
            )
            # Create vendor dir to simulate pip success
            (plugin_dir / "vendor").mkdir()

            exit_code = handle_add(args)

        assert exit_code == 0
        assert mock_run.called
        # Check pyproject was updated
        content = (plugin_dir / "pyproject.toml").read_text()
        assert "httpx>=0.27" in content

    def test_add_fails_without_pyproject(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "no_pyproject"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            '[plugin]\nid = "no_pyproject"\nname = "X"\nversion = "1.0.0"\n'
            'entry = "plugin.plugins.no_pyproject:X"\n',
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
            packages=["httpx"],
            python="python",
            _defaults=defaults,
        )
        exit_code = handle_add(args)
        assert exit_code == 1


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
