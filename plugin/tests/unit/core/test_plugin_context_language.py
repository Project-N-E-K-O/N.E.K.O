from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from plugin.core.context import PluginContext


def _ctx() -> PluginContext:
    return PluginContext(
        plugin_id="demo",
        config_path=Path("plugin.toml"),
        logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
        status_queue=None,
    )


def test_user_language_override_can_be_cleared_back_to_host_language() -> None:
    ctx = _ctx()
    ctx._current_lang = "zh-CN"

    assert ctx.get_user_language() == "zh-CN"

    ctx.set_user_language("en")
    assert ctx.get_user_language() == "en"

    ctx.set_user_language("")
    assert ctx.get_user_language() == "zh-CN"
