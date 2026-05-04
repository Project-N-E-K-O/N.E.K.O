from __future__ import annotations

import sys
from pathlib import Path

import pytest

CLI_ROOT = Path(__file__).resolve().parents[2] / "neko_plugin_cli"
_SRC_DIR = str(CLI_ROOT / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from neko_plugin_cli.cli import main

pytestmark = pytest.mark.plugin_unit


def _make_release_ready_plugin(root: Path, plugin_id: str = "release_demo") -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True)
    class_name = "".join(part.capitalize() for part in plugin_id.split("_")) + "Plugin"
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                f'id = "{plugin_id}"',
                'name = "Release Demo"',
                'version = "0.1.0"',
                'type = "plugin"',
                f'entry = "plugin.plugins.{plugin_id}:{class_name}"',
                "",
                "[plugin.sdk]",
                'recommended = ">=0.1.0,<0.2.0"',
                'supported = ">=0.1.0,<0.3.0"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "\n".join(
            [
                "from plugin.sdk.plugin import NekoPluginBase, lifecycle, neko_plugin",
                "",
                "",
                "@neko_plugin",
                f"class {class_name}(NekoPluginBase):",
                '    @lifecycle(id="startup")',
                "    def startup(self, **_):",
                "        pass",
                "",
                '    @lifecycle(id="shutdown")',
                "    def shutdown(self, **_):",
                "        pass",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (plugin_dir / "README.md").write_text("# Release Demo\n", encoding="utf-8")
    tests_dir = plugin_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_smoke.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8")
    vscode_dir = plugin_dir / ".vscode"
    vscode_dir.mkdir()
    (vscode_dir / "settings.json").write_text("{}", encoding="utf-8")
    (vscode_dir / "tasks.json").write_text('{"version":"2.0.0","tasks":[]}', encoding="utf-8")
    workflow_dir = plugin_dir / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "verify.yml").write_text("name: Verify N.E.K.O Plugin\n", encoding="utf-8")
    (plugin_dir / ".gitignore").write_text("__pycache__/\n.pytest_cache/\nstore.db\n", encoding="utf-8")
    return plugin_dir


def test_doctor_reports_repository_warnings_without_failing(tmp_path: Path, capsys) -> None:
    plugins_root = tmp_path / "plugins"
    _make_release_ready_plugin(plugins_root)

    exit_code = main(["doctor", "release_demo", "--plugins-root", str(plugins_root)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "doctor found" in captured.out
    assert "not a standalone git repository" in captured.out
    assert "fix: cd " in captured.out
    assert "git init" in captured.out


def test_release_check_runs_tests_and_creates_verified_package(tmp_path: Path, capsys) -> None:
    plugins_root = tmp_path / "plugins"
    target_dir = tmp_path / "target"
    _make_release_ready_plugin(plugins_root)

    exit_code = main(
        [
            "release-check",
            "release_demo",
            "--plugins-root",
            str(plugins_root),
            "--target-dir",
            str(target_dir),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "release-check passed" in captured.out
    assert "tests=passed" in captured.out
    assert "payload_hash_verified=True" in captured.out
    assert (target_dir / "release_demo.neko-plugin").is_file()


def test_release_check_blocks_when_strict_support_files_are_missing(tmp_path: Path, capsys) -> None:
    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "missing_release_files"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                'id = "missing_release_files"',
                'name = "Missing Release Files"',
                'version = "0.1.0"',
                'type = "plugin"',
                'entry = "plugin.plugins.missing_release_files:MissingReleaseFilesPlugin"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")

    exit_code = main(["release-check", "missing_release_files", "--plugins-root", str(plugins_root)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "release-check blocked" in captured.err
    assert "README.md is missing" in captured.err
    assert "fix: neko-plugin setup-repo missing_release_files --github-actions" in captured.err
