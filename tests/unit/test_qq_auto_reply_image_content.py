"""主消息图片的 VLM 描述必须注入 content，供回溯补回等消费。

修复背景：纯图片消息的 raw_message 为空串，图片在 message 数组里；旧逻辑靠替换
文本中的 [CQ:image] 注入描述，对纯图片永远注入不进去，导致回溯补回摘要里图片
内容为空。本测试钉死 `_inject_image_descriptions` 的行为：
- 纯图片消息 → content 直接填充 "[Image 描述]"
- 文本 + 图片 → 描述追加到文本后
- 图片描述失败 → content 保持原样
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
    """纯图片消息：content 直接填充图片描述（修复前为空）。"""
    async def describer(url): return f"一张{url.split('/')[-1].split('.')[0]}的图"
    msg = asyncio.run(_run(_img_message(), describer=describer))
    assert "Image" in msg["content"]
    assert "一张1的图" in msg["content"]
    assert "一张2的图" in msg["content"]


def test_text_plus_image_appends_description():
    """文本 + 图片：描述追加到文本后，不丢原文本。"""
    async def describer(url): return "猫的照片"
    msg = asyncio.run(_run(_img_message(has_text=True), describer=describer))
    assert "看看这张图" in msg["content"]
    assert msg["content"].count("Image 猫的照片") == 2


def test_image_description_failure_keeps_content():
    """图片描述失败（返回空/抛错）：content 保持原样，不崩溃。"""

    async def describer(url):
        return ""

    msg = asyncio.run(_run(_img_message(has_text=True), describer=describer))
    assert "看看这张图" in msg["content"]
    assert "Image" not in msg["content"]
