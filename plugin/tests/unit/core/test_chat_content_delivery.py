"""Tests for chat_content direct delivery in communication.py."""

from __future__ import annotations

from queue import Queue
from unittest.mock import MagicMock, patch

import pytest


def test_deliver_chat_content_to_frontend():
    """_deliver_chat_content_to_frontend should write to sync_message_queue."""
    from plugin.core.communication import PluginCommunicationResourceManager

    mgr = PluginCommunicationResourceManager.__new__(PluginCommunicationResourceManager)
    mgr.plugin_id = "weather"
    mgr.logger = MagicMock()

    q = Queue()
    mock_queues = {"test_session": q}

    msg = {
        "message_type": "chat_content",
        "content": "Hello",
        "metadata": {
            "chat_content_blocks": [{"type": "text", "text": "Hello"}],
            "plugin_id": "weather",
        },
    }

    with patch("plugin.core.communication.PluginCommunicationResourceManager._deliver_chat_content_to_frontend") as mock_deliver:
        # Test the actual method
        pass

    # Direct test of the method
    with patch("main_routers.shared_state.get_sync_message_queue", return_value=mock_queues):
        mgr._deliver_chat_content_to_frontend(msg, msg["metadata"])

    assert not q.empty()
    payload = q.get_nowait()
    assert payload["type"] == "json"
    assert payload["data"]["type"] == "plugin_chat_content"
    assert payload["data"]["plugin_id"] == "weather"
    assert payload["data"]["blocks"] == [{"type": "text", "text": "Hello"}]


def test_deliver_chat_content_target_lanlan():
    """Should deliver only to target_lanlan if specified."""
    from plugin.core.communication import PluginCommunicationResourceManager

    mgr = PluginCommunicationResourceManager.__new__(PluginCommunicationResourceManager)
    mgr.plugin_id = "weather"
    mgr.logger = MagicMock()

    q1 = Queue()
    q2 = Queue()
    mock_queues = {"session_a": q1, "session_b": q2}

    msg = {"message_type": "chat_content", "content": "Hi"}
    metadata = {
        "chat_content_blocks": [{"type": "text", "text": "Hi"}],
        "plugin_id": "weather",
        "target_lanlan": "session_a",
    }

    with patch("main_routers.shared_state.get_sync_message_queue", return_value=mock_queues):
        mgr._deliver_chat_content_to_frontend(msg, metadata)

    assert not q1.empty()
    assert q2.empty()
