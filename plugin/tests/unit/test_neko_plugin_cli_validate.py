from __future__ import annotations

from pathlib import Path

import pytest


from plugin.neko_plugin_cli.cli import main

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
    (plugin_dir / "__init__.py").write_text(
        "\n".join(
            [
                "from plugin.sdk.plugin import NekoPluginBase, lifecycle, neko_plugin",
                "",
                "",
                "@neko_plugin",
                "class ValidDemoPlugin(NekoPluginBase):",
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


def test_validate_reports_missing_entry_class(tmp_path: Path, capsys) -> None:
    plugins_root = tmp_path / "plugins"
    plugin_dir = _make_plugin(plugins_root, "missing_entry_class")
    (plugin_dir / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")

    exit_code = main(["validate", "missing_entry_class", "--plugins-root", str(plugins_root), "--strict"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "plugin.entry class 'ValidDemoPlugin' was not found" in captured.err


def test_validate_reports_entry_class_without_neko_plugin_decorator(tmp_path: Path, capsys) -> None:
    plugins_root = tmp_path / "plugins"
    plugin_dir = _make_plugin(plugins_root, "missing_neko_plugin")
    (plugin_dir / "__init__.py").write_text(
        "\n".join(
            [
                "from plugin.sdk.plugin import NekoPluginBase, lifecycle",
                "",
                "",
                "class ValidDemoPlugin(NekoPluginBase):",
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

    exit_code = main(["validate", "missing_neko_plugin", "--plugins-root", str(plugins_root), "--strict"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "must be decorated with @neko_plugin" in captured.err


def test_validate_reports_decorator_rule_violations(tmp_path: Path, capsys) -> None:
    plugins_root = tmp_path / "plugins"
    plugin_dir = _make_plugin(plugins_root, "bad_decorators")
    (plugin_dir / "bad.py").write_text(
        "\n".join(
            [
                "from plugin.sdk.plugin import lifecycle, plugin_entry, timer_interval",
                "",
                "",
                '@plugin_entry(id="dup")',
                "def first(**_):",
                "    pass",
                "",
                '@plugin_entry(id="dup")',
                "def second(**_):",
                "    pass",
                "",
                '@lifecycle(id="boot")',
                "def bad_lifecycle(**_):",
                "    pass",
                "",
                '@timer_interval(id="tick", seconds=0)',
                "def bad_timer(**_):",
                "    pass",
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(["validate", "bad_decorators", "--plugins-root", str(plugins_root), "--strict"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "duplicate @plugin_entry id 'dup'" in captured.out
    assert "@lifecycle id 'boot'" in captured.err
    assert "seconds > 0" in captured.err


def test_validate_reports_plugin_toml_enum_and_type_violations(tmp_path: Path, capsys) -> None:
    plugins_root = tmp_path / "plugins"
    plugin_dir = _make_plugin(plugins_root, "bad_toml")
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                'id = "bad_toml"',
                'name = "Bad TOML"',
                'version = "1.0.0"',
                'type = "widget"',
                'entry = "plugin.plugins.bad_toml:ValidDemoPlugin"',
                'keywords = "search"',
                'passive = 1',
                "",
                "[plugin.sdk]",
                'conflicts = ">=0.2.0"',
                "",
                "[plugin_runtime]",
                'enabled = "sometimes"',
                "priority = 1.5",
                "",
                "[plugin.ui]",
                "enabled = true",
                "",
                "[[plugin.ui.panel]]",
                'entry = "ui/panel.tsx"',
                'mode = "portal"',
                'open_in = "popup"',
                'context = "somewhere"',
                'permissions = ["state:read", "danger:write"]',
                "",
                "[plugin_state]",
                'backend = "s3"',
                'persist_mode = "forever"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(["validate", "bad_toml", "--plugins-root", str(plugins_root), "--strict"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[plugin].type must be one of" in captured.err
    assert "[plugin].keywords must be a list of strings" in captured.err
    assert "[plugin].passive must be a boolean" in captured.err
    assert "[plugin.sdk].conflicts must be a list of strings" in captured.err
    assert "[plugin_runtime].enabled must be a boolean" in captured.err
    assert "[plugin_runtime].priority must be an integer" in captured.err
    assert "[plugin.ui].panel[0].mode must be one of" in captured.err
    assert "[plugin.ui].panel[0].open_in must be one of" in captured.err
    assert "[plugin.ui].panel[0].context must be one of" in captured.err
    assert "[plugin_state].backend must be one of" in captured.err
    assert "[plugin_state].persist_mode must be one of" in captured.err
    assert "danger:write" in captured.out


def test_validate_reports_plugin_toml_mutually_exclusive_fields(tmp_path: Path, capsys) -> None:
    plugins_root = tmp_path / "plugins"
    plugin_dir = _make_plugin(plugins_root, "bad_dependency")
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                'id = "bad_dependency"',
                'name = "Bad Dependency"',
                'version = "1.0.0"',
                'entry = "plugin.plugins.bad_dependency:ValidDemoPlugin"',
                "",
                "[[plugin.dependency]]",
                'entry = "host:run"',
                'custom_event = "host:event"',
                'untested = ">=0.1.0"',
                "",
                "[[plugin.dependency]]",
                "conflicts = true",
                "",
                "[[plugin.ui.panel]]",
                'entry = "ui/panel.tsx"',
                'url = "https://example.com/panel"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(["validate", "bad_dependency", "--plugins-root", str(plugins_root), "--strict"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "cannot declare both entry and custom_event" in captured.err
    assert "with conflicts=true requires id" in captured.err
    assert "cannot declare both entry and url" in captured.err


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
