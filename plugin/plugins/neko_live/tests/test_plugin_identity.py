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


def test_neko_live_executable_sources_do_not_restore_legacy_identity() -> None:
    plugin_dir = Path(__file__).resolve().parents[1]
    source_suffixes = {".html", ".ps1", ".py", ".toml", ".tsx"}
    forbidden_markers = (
        "plugin.plugins.neko_roast",
        "NekoRoastPlugin",
        "NekoRoastPanel",
        "neko-roast",
    )
    violations: list[str] = []

    for path in plugin_dir.rglob("*"):
        if not path.is_file() or path.suffix not in source_suffixes or path == Path(__file__):
            continue
        source = path.read_text(encoding="utf-8")
        if path.name == "plugin.toml":
            source = source.replace('previous_ids = ["neko_roast"]', "")
        if "neko_roast" in source or any(marker in source for marker in forbidden_markers):
            violations.append(path.relative_to(plugin_dir).as_posix())

    assert violations == []
