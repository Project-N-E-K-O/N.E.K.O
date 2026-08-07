from pathlib import Path

import pytest

from plugin.core.plugin_layout import resolve_plugin_layout


def test_resolve_plugin_layout_separates_payload_from_user_state(tmp_path: Path) -> None:
    installed_dir = tmp_path / "installed-plugins" / "demo"
    storage_root = tmp_path / "runtime-storage"

    layout = resolve_plugin_layout(
        "demo",
        installed_dir,
        storage_root=storage_root,
    )

    assert layout.installed_dir == installed_dir.resolve()
    assert layout.manifest_path == installed_dir.resolve() / "plugin.toml"
    assert layout.vendor_dir == installed_dir.resolve() / "vendor"
    assert layout.config_path == storage_root.resolve() / "plugins" / "demo" / "config" / "plugin.toml"
    assert layout.data_dir == storage_root.resolve() / "plugins" / "demo" / "data"
    assert layout.cache_dir == storage_root.resolve() / "plugins" / "demo" / "cache"
    assert not storage_root.exists()


@pytest.mark.parametrize("plugin_id", ["../other", "nested/plugin", "", "."])
def test_resolve_plugin_layout_rejects_unsafe_plugin_ids(tmp_path: Path, plugin_id: str) -> None:
    with pytest.raises(ValueError, match="plugin_id"):
        resolve_plugin_layout(plugin_id, tmp_path / "installed")
