"""Tests for push_chat_content SDK method."""

from __future__ import annotations

from unittest.mock import MagicMock


def test_push_chat_content_calls_push_message():
    """push_chat_content should call push_message with correct params."""
    from plugin.sdk.plugin.base import NekoPluginBase

    mock_ctx = MagicMock()
    mock_ctx.plugin_id = "test_plugin"
    mock_ctx.push_message = MagicMock()

    class FakePlugin:
        plugin_id = "test_plugin"
        ctx = mock_ctx
        def push_message(self, **kwargs):
            self.ctx.push_message(**kwargs)

    plugin = FakePlugin()
    blocks = [{"type": "text", "text": "hello"}]
    NekoPluginBase.push_chat_content(plugin, blocks)

    mock_ctx.push_message.assert_called_once()
    call_kwargs = mock_ctx.push_message.call_args[1]
    assert call_kwargs["message_type"] == "chat_content"
    assert call_kwargs["source"] == "test_plugin"
    assert call_kwargs["metadata"]["chat_content_blocks"] == blocks
    assert call_kwargs["metadata"]["plugin_id"] == "test_plugin"
    assert call_kwargs["content"] == "hello"


def test_push_chat_content_text_fallback():
    """Text fallback should concatenate text blocks."""
    from plugin.sdk.plugin.base import NekoPluginBase

    mock_ctx = MagicMock()
    mock_ctx.plugin_id = "test"
    mock_ctx.push_message = MagicMock()

    class FakePlugin:
        plugin_id = "test"
        ctx = mock_ctx
        def push_message(self, **kwargs):
            self.ctx.push_message(**kwargs)

    plugin = FakePlugin()
    blocks = [
        {"type": "text", "text": "line1"},
        {"type": "card", "title": "Card Title"},
        {"type": "text", "text": "line2"},
    ]
    NekoPluginBase.push_chat_content(plugin, blocks)

    call_kwargs = mock_ctx.push_message.call_args[1]
    assert "line1" in call_kwargs["content"]
    assert "Card Title" in call_kwargs["content"]
    assert "line2" in call_kwargs["content"]


def test_push_chat_content_empty_blocks():
    """Empty blocks should return None without calling push_message."""
    from plugin.sdk.plugin.base import NekoPluginBase

    mock_ctx = MagicMock()
    mock_ctx.plugin_id = "test"
    mock_ctx.push_message = MagicMock()

    class FakePlugin:
        plugin_id = "test"
        ctx = mock_ctx
        def push_message(self, **kwargs):
            self.ctx.push_message(**kwargs)

    plugin = FakePlugin()
    result = NekoPluginBase.push_chat_content(plugin, [])
    assert result is None
    mock_ctx.push_message.assert_not_called()


def test_push_chat_content_filters_invalid_blocks():
    """Invalid blocks (no type) should be filtered out."""
    from plugin.sdk.plugin.base import NekoPluginBase

    mock_ctx = MagicMock()
    mock_ctx.plugin_id = "test"
    mock_ctx.push_message = MagicMock()

    class FakePlugin:
        plugin_id = "test"
        ctx = mock_ctx
        def push_message(self, **kwargs):
            self.ctx.push_message(**kwargs)

    plugin = FakePlugin()
    blocks = [
        {"type": "text", "text": "valid"},
        {"no_type": True},
        "not_a_dict",
    ]
    NekoPluginBase.push_chat_content(plugin, blocks)

    call_kwargs = mock_ctx.push_message.call_args[1]
    assert len(call_kwargs["metadata"]["chat_content_blocks"]) == 1
