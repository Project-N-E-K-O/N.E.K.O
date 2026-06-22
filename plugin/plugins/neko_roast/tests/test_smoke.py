from __future__ import annotations

import tomllib
from pathlib import Path


def test_neko_roast_manifest_smoke():
    root = Path(__file__).resolve().parents[1]
    with (root / "plugin.toml").open("rb") as handle:
        manifest = tomllib.load(handle)

    assert manifest["plugin"]["id"] == "neko_roast"
    assert manifest["plugin"]["entry"] == "plugin.plugins.neko_roast:NekoRoastPlugin"
    assert (root / "ui" / "panel.tsx").is_file()
