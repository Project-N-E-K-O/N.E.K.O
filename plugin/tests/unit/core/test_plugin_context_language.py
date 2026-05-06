from __future__ import annotations

from pathlib import Path

import pytest

from plugin.core.context import PluginContext

pytestmark = pytest.mark.plugin_unit


def test_set_user_language_strips_and_clears_blank_values() -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=Path("plugin.toml"),
        logger=None,  # type: ignore[arg-type]
        status_queue=None,
    )
    ctx._current_lang = "en"

    ctx.set_user_language(" zh-CN ")
    assert ctx.get_user_language() == "zh-CN"

    ctx.set_user_language("   ")
    assert ctx.get_user_language() == "en"
