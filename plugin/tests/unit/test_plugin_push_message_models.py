from __future__ import annotations

from plugin._types.models import PluginPushMessage, PluginPushMessageRequest


def test_push_message_request_preserves_complete_v1_and_v2_input_surface() -> None:
    request = PluginPushMessageRequest.model_validate(
        {
            "plugin_id": "demo",
            "parts": [{"type": "text", "text": "v2"}],
            "visibility": ["chat"],
            "ai_behavior": "blind",
            "coalesce_key": "demo:notice",
            "message_type": "text",
            "description": "legacy",
            "content": "v1",
            "binary_data": b"data",
            "binary_url": "https://example.test/image.png",
            "mime": "image/png",
            "delivery": "passive",
            "reply": False,
            "unsafe": True,
            "fast_mode": True,
        }
    )

    assert request.coalesce_key == "demo:notice"
    assert request.delivery == "passive"
    assert request.reply is False
    assert request.fast_mode is True


def test_push_message_record_preserves_complete_wire_compat_surface() -> None:
    message = PluginPushMessage.model_validate(
        {
            "plugin_id": "demo",
            "message_id": "m1",
            "timestamp": "2026-07-12T00:00:00Z",
            "schema": "push_message.v2",
            "coalesce_key": "demo:notice",
            "parts": [{"type": "text", "text": "v2"}],
            "message_type": "text",
            "content": "v1",
            "binary_data": b"data",
            "binary_url": "https://example.test/image.png",
            "mime": "image/png",
            "unsafe": True,
            "delivery": "silent",
            "reply": False,
        }
    )

    dumped = message.model_dump(by_alias=True)
    assert dumped["schema"] == "push_message.v2"
    assert dumped["coalesce_key"] == "demo:notice"
    assert dumped["mime"] == "image/png"
    assert dumped["unsafe"] is True
    assert dumped["delivery"] == "silent"
    assert dumped["reply"] is False
