"""VLM descriptions for message images must be injected into content for retroactive review.

Background: a pure-image message has an empty raw_message (images live in the message array),
so the old logic that replaced [CQ:image] in the text never injected a description, leaving the
retroactive-review summary empty of image content. Pins `_inject_image_descriptions`:
- pure-image message -> content filled with "[Image description]"
- text + image -> description appended after the text
- image description failure -> content unchanged
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.message_dispatcher import QQMessageDispatcher


class _Plugin:
    def __init__(self, *, describer=None):
        self.qq_client = SimpleNamespace(_image_describer=describer)
        self._emit_log = lambda *a, **k: None


def _img_message(*, content="", has_text=False):
    # 有文本时，content 带一个 [CQ:image] 占位（与生产 raw_message 一致）
    if has_text and not content:
        content = "看看这张图[CQ:image,file=f1]"
    segs = [{"type": "image", "data": {"url": "http://img/1.jpg", "file": "f1"}}]
    segs.append({"type": "image", "data": {"url": "http://img/2.jpg", "file": "f2"}})
    return {
        "content": content,
        "raw_message": content,
        "message_id": "1419151035",
        "raw": {"message": segs},
    }


async def _run(message, *, describer=None):
    dispatcher = QQMessageDispatcher(_Plugin(describer=describer))
    await dispatcher._inject_image_descriptions(message)
    return message


def test_pure_image_message_gets_description():
    """A pure-image message fills content with image descriptions (was empty before the fix)."""
    async def describer(url): return f"一张{url.split('/')[-1].split('.')[0]}的图"
    msg = asyncio.run(_run(_img_message(), describer=describer))
    assert "Image" in msg["content"]
    assert "一张1的图" in msg["content"]
    assert "一张2的图" in msg["content"]


def test_text_plus_image_appends_description():
    """Text plus images appends descriptions after the text without dropping it."""
    async def describer(url): return "猫的照片"
    msg = asyncio.run(_run(_img_message(has_text=True), describer=describer))
    assert "看看这张图" in msg["content"]
    assert msg["content"].count("Image 猫的照片") == 2


def test_image_description_failure_keeps_content():
    """When image description fails (empty/throws), content stays unchanged without crashing."""

    async def describer(url):
        return ""

    msg = asyncio.run(_run(_img_message(has_text=True), describer=describer))
    assert "看看这张图" in msg["content"]
    assert "Image" not in msg["content"]
