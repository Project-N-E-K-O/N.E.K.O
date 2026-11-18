from pathlib import Path
import sys

import pytest

from plugin.core.entry_points import (
    describe_plugin_entry_directory_mismatch,
    normalize_plugin_entry_point,
    plugin_package_matches_directory,
)
from plugin.core.registry import _prepare_plugin_import_roots


def test_normalize_user_installed_canonical_plugin_plugins_entry(tmp_path: Path) -> None:
    builtin_root = tmp_path / "repo" / "plugin" / "plugins"
    config_path = tmp_path / "user" / "N.E.K.O" / "plugins" / "demo" / "plugin.toml"

    assert (
        normalize_plugin_entry_point(
            "plugin.plugins.demo:DemoPlugin",
            config_path=config_path,
            builtin_plugin_root=builtin_root,
        )
        == "plugins.demo:DemoPlugin"
    )


def test_keep_builtin_plugin_plugins_entry(tmp_path: Path) -> None:
    builtin_root = tmp_path / "repo" / "plugin" / "plugins"
    config_path = builtin_root / "demo" / "plugin.toml"

    assert (
        normalize_plugin_entry_point(
            "plugin.plugins.demo:DemoPlugin",
            config_path=config_path,
            builtin_plugin_root=builtin_root,
        )
        == "plugin.plugins.demo:DemoPlugin"
    )


def test_external_plugin_roots_never_enter_main_process_sys_path(tmp_path: Path) -> None:
    user_root = tmp_path / "plugins"
    (user_root / "json").mkdir(parents=True)
    original_sys_path = list(sys.path)

    try:
        _prepare_plugin_import_roots((user_root,), logger=None)
        assert str(user_root) not in sys.path
        assert str(user_root.parent) not in sys.path
    finally:
        sys.path[:] = original_sys_path


@pytest.mark.parametrize(
    ("package", "directory", "expected"),
    [
        ("demo", "demo", True),
        ("demo_plugin", "demo-plugin", True),
        ("demo_plugin", "demo_plugin", True),
        ("demo-plugin", "demo-plugin", True),
        ("demo_plugin", "demo_plugin_v2", False),
        ("demo_plugin", "other", False),
        # Only '-' and '_' are interchangeable; no other normalisation is implied.
        ("demo.plugin", "demo-plugin", False),
        # The package side is what Python imports, so it cannot carry the dash.
        ("demo-plugin", "demo_plugin", False),
        ("Demo_Plugin", "demo_plugin", False),
    ],
)
def test_package_matches_directory_only_equates_dash_and_underscore(
    package: str, directory: str, expected: bool
) -> None:
    assert plugin_package_matches_directory(package, directory) is expected


def test_cloned_kebab_directory_matches_its_snake_package(tmp_path: Path) -> None:
    """A GitHub clone keeps the kebab folder name; the entry must still resolve."""
    config_path = tmp_path / "my-cool-plugin" / "plugin.toml"
    config_path.parent.mkdir()

    assert (
        describe_plugin_entry_directory_mismatch(
            "plugins.my_cool_plugin:MyCoolPlugin", config_path=config_path
        )
        is None
    )


def test_unrelated_directory_still_reports_an_actionable_mismatch(tmp_path: Path) -> None:
    config_path = tmp_path / "somewhere_else" / "plugin.toml"
    config_path.parent.mkdir()

    message = describe_plugin_entry_directory_mismatch(
        "plugins.my_cool_plugin:MyCoolPlugin", config_path=config_path
    )

    assert message is not None
    assert "somewhere_else" in message
    # The kebab-case hint is what turns a dead end into a fix.
    assert "may use '-'" in message
    assert "my-cool-plugin" in message
