from pathlib import Path

from plugin.core.entry_points import normalize_plugin_entry_point


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
