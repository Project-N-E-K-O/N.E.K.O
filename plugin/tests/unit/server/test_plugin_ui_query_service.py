from __future__ import annotations

import tempfile
from pathlib import Path

from plugin.server.application.plugins.ui_query_service import _get_static_ui_config_from_meta


def test_static_ui_config_infers_from_config_path_when_missing() -> None:
    root = Path(tempfile.mkdtemp())
    plugin_dir = root / "demo_plugin"
    static_dir = plugin_dir / "static"
    static_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    config = _get_static_ui_config_from_meta({
        "id": "demo",
        "config_path": str(plugin_dir / "plugin.toml"),
    })

    assert config is not None
    assert config["enabled"] is True
    assert config["directory"] == str(static_dir)
    assert config["inferred"] is True
