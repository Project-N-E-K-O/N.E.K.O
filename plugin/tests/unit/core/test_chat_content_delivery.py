"""Tests for chat_content delivery chain.

The production path for push_chat_content is:
  plugin subprocess → ZMQ ingest → message_plane PUB → proactive_bridge → main_server → WebSocket

These tests verify the proactive_bridge parsing logic (via the extracted
parse_chat_content_event function) and the _forward_message passthrough.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


def test_forward_message_stores_chat_content_normally():
    """_forward_message should store chat_content like any other message — no special routing."""
    from plugin.core.communication import PluginCommunicationResourceManager

    mgr = PluginCommunicationResourceManager.__new__(PluginCommunicationResourceManager)
    mgr.plugin_id = "lifekit"
    mgr.logger = MagicMock()
    mgr._message_target_queue = asyncio.Queue(maxsize=100)

    msg = {
        "type": "MESSAGE_PUSH",
        "message_type": "chat_content",
        "content": "Hello",
        "metadata": {
            "chat_content_blocks": [{"type": "text", "text": "Hello"}],
            "plugin_id": "lifekit",
        },
        "_bus_stored": True,
    }

    asyncio.run(mgr._forward_message(msg))

    assert not mgr._message_target_queue.empty()
    queued = mgr._message_target_queue.get_nowait()
    assert queued["message_type"] == "chat_content"


def test_parse_chat_content_event_valid():
    """parse_chat_content_event should produce a correct event from valid payload."""
    from plugin.server.messaging.proactive_bridge import parse_chat_content_event

    payload = {
        "message_type": "chat_content",
        "plugin_id": "lifekit",
        "content": "上海今天天气",
        "time": "2026-04-25T00:00:00Z",
    }
    metadata = {
        "chat_content_blocks": [
            {"type": "text", "text": "🌤️ 上海 — 晴 25°C"},
        ],
        "plugin_id": "lifekit",
        "target_lanlan": "test_session",
    }

    event = parse_chat_content_event(payload, metadata, "lifekit")

    assert event is not None
    assert event["event_type"] == "plugin_chat_content"
    assert event["plugin_id"] == "lifekit"
    assert event["lanlan_name"] == "test_session"
    assert event["blocks"] == [{"type": "text", "text": "🌤️ 上海 — 晴 25°C"}]
    assert event["text"] == "上海今天天气"


def test_parse_chat_content_event_empty_blocks():
    """parse_chat_content_event should return None for empty blocks."""
    from plugin.server.messaging.proactive_bridge import parse_chat_content_event

    event = parse_chat_content_event(
        {"content": "x"}, {"chat_content_blocks": []}, "test"
    )
    assert event is None


def test_parse_chat_content_event_filters_invalid_blocks():
    """parse_chat_content_event should filter out non-dict and typeless blocks."""
    from plugin.server.messaging.proactive_bridge import parse_chat_content_event

    metadata = {
        "chat_content_blocks": [
            "not a dict",
            42,
            {"no_type_key": True},
            {"type": "text", "text": "valid"},
        ],
    }
    event = parse_chat_content_event({"content": ""}, metadata, "test")

    assert event is not None
    assert len(event["blocks"]) == 1
    assert event["blocks"][0]["text"] == "valid"


def test_parse_chat_content_event_all_invalid_blocks():
    """parse_chat_content_event should return None if all blocks are invalid."""
    from plugin.server.messaging.proactive_bridge import parse_chat_content_event

    metadata = {"chat_content_blocks": ["bad", 123, {"no_type": True}]}
    event = parse_chat_content_event({"content": ""}, metadata, "test")
    assert event is None
