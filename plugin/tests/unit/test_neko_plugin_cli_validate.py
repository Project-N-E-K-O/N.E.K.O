from __future__ import annotations

import sys
from pathlib import Path

import pytest

CLI_ROOT = Path(__file__).resolve().parents[2] / "neko-plugin-cli"
_SRC_DIR = str(CLI_ROOT / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from neko_plugin_cli.cli import main

pytestmark = pytest.mark.plugin_unit


def _make_plugin(root: Path, plugin_id: str = "valid_demo") -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                f'id = "{plugin_id}"',
                'name = "Valid Demo"',
                'version = "0.1.0"',
                f'entry = "plugin.plugins.{plugin_id}:ValidDemoPlugin"',
                "",
                "[plugin.sdk]",
                'recommended = ">=0.1.0,<0.2.0"',
                'supported = ">=0.1.0,<0.3.0"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (plugin_dir / "README.md").write_text("# Valid Demo\n", encoding="utf-8")
    tests_dir = plugin_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_smoke.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8")
    vscode_dir = plugin_dir / ".vscode"
    vscode_dir.mkdir()
    (vscode_dir / "settings.json").write_text("{}", encoding="utf-8")
    (vscode_dir / "tasks.json").write_text('{"version":"2.0.0","tasks":[]}', encoding="utf-8")
    workflow_dir = plugin_dir / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "verify.yml").write_text("name: Verify\n", encoding="utf-8")
    (plugin_dir / ".gitignore").write_text("__pycache__/\n.pytest_cache/\nstore.db\n", encoding="utf-8")
    return plugin_dir


def test_validate_strict_passes_for_setup_repo_shape(tmp_path: Path, capsys) -> None:
    plugins_root = tmp_path / "plugins"
    _make_plugin(plugins_root)

    exit_code = main(["validate", "valid_demo", "--plugins-root", str(plugins_root), "--strict"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "validation passed" in captured.out


def test_validate_strict_fails_when_support_files_are_missing(tmp_path: Path, capsys) -> None:
    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "missing_support"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        '[plugin]\nid = "missing_support"\nname = "Missing"\nversion = "0.1.0"\nentry = "plugin.plugins.missing_support:MissingSupportPlugin"\n',
        encoding="utf-8",
    )

    exit_code = main(["validate", "missing_support", "--plugins-root", str(plugins_root), "--strict"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "README.md is missing" in captured.err


def test_validate_reports_invalid_json(tmp_path: Path, capsys) -> None:
    plugins_root = tmp_path / "plugins"
    plugin_dir = _make_plugin(plugins_root, "bad_json")
    (plugin_dir / ".vscode" / "tasks.json").write_text("{bad", encoding="utf-8")

    exit_code = main(["validate", "bad_json", "--plugins-root", str(plugins_root), "--strict"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "invalid JSON" in captured.err


def test_validate_pack_runs_package_roundtrip(tmp_path: Path) -> None:
    plugins_root = tmp_path / "plugins"
    target_dir = tmp_path / "target"
    _make_plugin(plugins_root, "pack_demo")

    exit_code = main(
        [
            "validate",
            "pack_demo",
            "--plugins-root",
            str(plugins_root),
            "--strict",
            "--pack",
            "--target-dir",
            str(target_dir),
        ]
    )

    assert exit_code == 0
    assert (target_dir / "pack_demo.neko-plugin").is_file()
