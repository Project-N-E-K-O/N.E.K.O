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


def test_get_attachments_requires_active_run() -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=Path("plugin.toml"),
        logger=None,  # type: ignore[arg-type]
        status_queue=None,
    )

    class Instance:
        _last_attachments = [{"type": "image_url", "url": "leaked"}]
        _run_attachments = {"run-1": [{"type": "image_url", "url": "current"}]}

    ctx._instance = Instance()

    assert ctx.get_attachments() == []
    with ctx._run_scope("run-1"):
        assert ctx.get_attachments() == [{"type": "image_url", "url": "current"}]
