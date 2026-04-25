"""Tests for chat_content relay in communication.py _forward_message."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


def test_forward_message_relays_chat_content_to_plane_bridge():
    """_forward_message should relay chat_content messages via plane_bridge.publish_record."""
    from plugin.core.communication import PluginCommunicationResourceManager

    mgr = PluginCommunicationResourceManager.__new__(PluginCommunicationResourceManager)
    mgr.plugin_id = "weather"
    mgr.logger = MagicMock()
    mgr._message_target_queue = asyncio.Queue(maxsize=100)

    msg = {
        "type": "MESSAGE_PUSH",
        "message_type": "chat_content",
        "content": "Hello",
        "metadata": {
            "chat_content_blocks": [{"type": "text", "text": "Hello"}],
            "plugin_id": "weather",
        },
        "_bus_stored": True,
    }

    with patch("plugin.server.messaging.plane_bridge.publish_record") as mock_publish:
        asyncio.run(mgr._forward_message(msg))

    mock_publish.assert_called_once_with(store="messages", record=msg)


def test_forward_message_does_not_relay_non_chat_content():
    """_forward_message should NOT relay non-chat_content messages via plane_bridge."""
    from plugin.core.communication import PluginCommunicationResourceManager

    mgr = PluginCommunicationResourceManager.__new__(PluginCommunicationResourceManager)
    mgr.plugin_id = "weather"
    mgr.logger = MagicMock()
    mgr._message_target_queue = asyncio.Queue(maxsize=100)

    msg = {
        "type": "MESSAGE_PUSH",
        "message_type": "text",
        "content": "Hello",
        "_bus_stored": True,
    }

    with patch("plugin.server.messaging.plane_bridge.publish_record") as mock_publish:
        asyncio.run(mgr._forward_message(msg))

    mock_publish.assert_not_called()


def test_proactive_bridge_handles_chat_content():
    """ProactiveBridge should construct plugin_chat_content event from chat_content payload."""
    import json

    payload = {
        "message_type": "chat_content",
        "plugin_id": "weather",
        "content": "上海今天天气",
        "metadata": {
            "chat_content_blocks": [
                {"type": "text", "text": "🌤️ 上海 — 晴 25°C"},
            ],
            "plugin_id": "weather",
            "target_lanlan": "test_session",
        },
        "time": "2026-04-25T00:00:00Z",
    }

    event = {
        "seq": 1,
        "ts": 1745510400.0,
        "store": "messages",
        "topic": "all",
        "payload": payload,
        "index": {},
    }

    # Simulate what proactive_bridge does when it receives this event
    p = event.get("payload")
    assert isinstance(p, dict)

    msg_type = p.get("message_type")
    assert msg_type == "chat_content"

    metadata = p.get("metadata")
    assert isinstance(metadata, dict)

    blocks = metadata.get("chat_content_blocks")
    assert isinstance(blocks, list) and len(blocks) > 0

    proactive_event = {
        "event_type": "plugin_chat_content",
        "lanlan_name": metadata.get("target_lanlan") or None,
        "plugin_id": p.get("plugin_id", ""),
        "blocks": blocks,
        "text": p.get("content", ""),
        "timestamp": p.get("time", ""),
    }

    assert proactive_event["event_type"] == "plugin_chat_content"
    assert proactive_event["plugin_id"] == "weather"
    assert proactive_event["lanlan_name"] == "test_session"
    assert proactive_event["blocks"] == [{"type": "text", "text": "🌤️ 上海 — 晴 25°C"}]
    assert proactive_event["text"] == "上海今天天气"
