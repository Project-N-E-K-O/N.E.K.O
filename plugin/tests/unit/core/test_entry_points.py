from pathlib import Path
import sys

from plugin.core.entry_points import normalize_plugin_entry_point
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


def test_external_plugin_roots_never_enter_main_process_sys_path(tmp_path: Path) -> None:
    managed_root = tmp_path / "plugin-installations"
    (managed_root / "json").mkdir(parents=True)
    (managed_root / "json" / "__init__.py").write_text(
        "SHADOWED_BY_PLUGIN = True\n",
        encoding="utf-8",
    )
    original_sys_path = list(sys.path)

    try:
        _prepare_plugin_import_roots((managed_root,), logger=None)
        assert str(managed_root) not in sys.path
        assert str(managed_root.parent) not in sys.path
    finally:
        sys.path[:] = original_sys_path


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
