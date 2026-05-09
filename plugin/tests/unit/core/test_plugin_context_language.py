from __future__ import annotations

import asyncio
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
        attachments = ctx.get_attachments()
        assert attachments == [{"type": "image_url", "url": "current"}]
        attachments[0]["url"] = "mutated"
        assert Instance._run_attachments["run-1"][0]["url"] == "current"


@pytest.mark.asyncio
async def test_export_push_image_forces_image_media_type() -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=Path("plugin.toml"),
        logger=None,  # type: ignore[arg-type]
        status_queue=None,
    )
    captured: dict[str, object] = {}

    async def fake_export_push_async(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"ok": True}

    ctx._export_push_async = fake_export_push_async  # type: ignore[method-assign]

    result = await ctx._export_push_image_async(
        image_url="https://example.test/image.png",
        metadata={"media_type": "video", "source": "test"},
    )

    assert result == {"ok": True}
    assert captured["metadata"] == {"media_type": "image", "source": "test"}


@pytest.mark.asyncio
async def test_user_language_is_isolated_per_async_task() -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=Path("plugin.toml"),
        logger=None,  # type: ignore[arg-type]
        status_queue=None,
    )

    async def read_language(lang: str) -> tuple[str, str]:
        with ctx._language_scope(lang=lang, lanlan=f"{lang}-lanlan"):
            before = ctx.get_user_language()
            await asyncio.sleep(0)
            after = ctx.get_user_language()
            return before, after

    assert await asyncio.gather(read_language("zh-CN"), read_language("en")) == [
        ("zh-CN", "zh-CN"),
        ("en", "en"),
    ]
