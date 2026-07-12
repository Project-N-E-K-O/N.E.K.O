from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_launcher_collects_current_plugin_sdk_tree() -> None:
    spec = (ROOT / "specs" / "launcher.spec").read_text(encoding="utf-8")

    assert "collect_submodules('plugin.sdk')" in spec
    for removed_module in (
        "plugin.sdk.base",
        "plugin.sdk.decorators",
        "plugin.sdk.events",
        "plugin.sdk.logger",
        "plugin.sdk.version",
    ):
        assert f"'{removed_module}'" not in spec


def test_removed_extension_sdk_has_no_importable_module() -> None:
    assert importlib.util.find_spec("plugin.sdk.extension") is None
