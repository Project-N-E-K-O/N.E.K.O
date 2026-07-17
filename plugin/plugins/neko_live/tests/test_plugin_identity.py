from __future__ import annotations

from importlib import import_module
from pathlib import Path
import tomllib

import pytest

pytestmark = pytest.mark.plugin_unit


def test_neko_live_uses_one_permanent_internal_identity() -> None:
    plugin_dir = Path(__file__).resolve().parents[1]
    manifest = tomllib.loads((plugin_dir / "plugin.toml").read_text(encoding="utf-8"))

    assert plugin_dir.name == "neko_live"
    assert manifest["plugin"]["id"] == "neko_live"
    assert manifest["plugin"]["name"] == "NEKO Live"
    assert manifest["plugin"]["entry"] == "plugin.plugins.neko_live:NekoLivePlugin"
    assert manifest["plugin"]["previous_ids"] == ["neko_roast"]
    assert "neko_live" in manifest
    assert "neko_roast" not in manifest

    module = import_module("plugin.plugins.neko_live")
    entry_class = getattr(module, "NekoLivePlugin")
    assert entry_class.name == "neko_live"
