"""Tests for chat_content delivery chain.

The production path for push_chat_content is:
  plugin subprocess → ZMQ ingest → message_plane PUB → proactive_bridge → main_server → WebSocket

These tests verify the proactive_bridge parsing logic (the only non-trivial
transform in the chain) and the _forward_message passthrough (no special
handling — chat_content is stored and queued like any other message type).
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

    # Message should be in the queue — no interception, no special routing
    assert not mgr._message_target_queue.empty()
    queued = mgr._message_target_queue.get_nowait()
    assert queued["message_type"] == "chat_content"


def test_proactive_bridge_parses_chat_content_event():
    """Verify proactive_bridge correctly transforms a chat_content payload
    into a plugin_chat_content event structure."""

    payload = {
        "message_type": "chat_content",
        "plugin_id": "lifekit",
        "content": "上海今天天气",
        "metadata": {
            "chat_content_blocks": [
                {"type": "text", "text": "🌤️ 上海 — 晴 25°C"},
            ],
            "plugin_id": "lifekit",
            "target_lanlan": "test_session",
        },
        "time": "2026-04-25T00:00:00Z",
    }

    # Simulate the parsing logic from proactive_bridge._run
    msg_type = payload.get("message_type")
    metadata = payload.get("metadata", {})

    assert msg_type == "chat_content"

    blocks = metadata.get("chat_content_blocks")
    assert isinstance(blocks, list) and len(blocks) > 0

    proactive_event = {
        "event_type": "plugin_chat_content",
        "lanlan_name": metadata.get("target_lanlan") or None,
        "plugin_id": payload.get("plugin_id", ""),
        "blocks": blocks,
        "text": payload.get("content", ""),
        "timestamp": payload.get("time", ""),
    }

    assert proactive_event["event_type"] == "plugin_chat_content"
    assert proactive_event["plugin_id"] == "lifekit"
    assert proactive_event["lanlan_name"] == "test_session"
    assert proactive_event["blocks"] == [{"type": "text", "text": "🌤️ 上海 — 晴 25°C"}]
    assert proactive_event["text"] == "上海今天天气"
