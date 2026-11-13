"""Exercise real stream_text emission and persisted reply for literal title pipes."""

import asyncio
import base64
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from PIL import Image

from main_logic.omni_offline_client import OmniOfflineClient
from utils.llm_client import SystemMessage, LLMStreamChunk

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("prefix_size", [0, 32])
@pytest.mark.parametrize("visual", ["none", "attachment", "proactive", "plugin"])
def test_attachments_keep_existing_pipe_guard(
    prefix_size, visual, monkeypatch
):
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", lambda: None)

    async def run():
        c = object.__new__(OmniOfflineClient)
        c._conversation_history = [SystemMessage(content="sys")]
        b = io.BytesIO()
        Image.new("RGB", (32, 32), "white").save(b, "PNG")
        pixels = base64.b64encode(b.getvalue()).decode()
        c._pending_images = [pixels] if visual == "attachment" else []
        c._pending_plugin_images = [pixels] if visual == "plugin" else []
        c._proactive_image_to_inject = pixels if visual == "proactive" else None
        c._proactive_image_staged_at = c._proactive_image_history_len = 0
        c.master_name = "M"
        c.lanlan_name = "L"
        c.llm = SimpleNamespace(max_completion_tokens=2000)
        c.model = "test"
        c.vision_model = None
        c.max_response_rerolls = 0
        c.max_response_length = 2000
        c._prefix_buffer_size = prefix_size
        c.enable_response_guard = False
        c._recent_responses = []
        c._max_recent_responses = 5
        c._repetition_threshold = 0.8
        c._is_responding = False
        c._skip_next_response = False
        for hook in (
            "on_input_transcript",
            "on_status_message",
            "on_response_discarded",
            "on_response_done",
            "on_text_delta",
            "on_thinking_active",
            "on_output_transcript",
            "on_repetition_detected",
        ):
            setattr(c, hook, None)
        c.on_text_delta = AsyncMock()
        c._begin_reasoning_stream = MagicMock()
        c._notify_reasoning_done = AsyncMock()
        c.switch_model = AsyncMock()
        c._publish_provider_frames = MagicMock()
        c._fire_bus_task = lambda coro: coro.close()
        parts = ["CS336 |", " Spring 2026 ", "| Lecture 6."]

        async def stream(messages, **kwargs):
            for part in parts:
                yield LLMStreamChunk(content=part)
            yield LLMStreamChunk(content="", finish_reason="stop")

        c._astream_visible_with_tools = stream
        await c.stream_text("Read the title")
        actual = "".join(call.args[0] for call in c.on_text_delta.await_args_list)
        expected = "CS336 | Spring 2026 "
        assert actual == expected
        assert c._conversation_history[-1].content == expected

    asyncio.run(run())


def test_real_tool_loop_carries_overview_window_and_crop_to_final_request(monkeypatch):
    from main_logic.tool_calling import ToolDefinition, ToolImage, ToolResult
    from tests.unit.test_offline_turn_cancellation_e2e import _client, _text, _emitted
    from tests.unit.test_offline_provider_frame_publish import _png_b64

    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", lambda: None)

    async def run():
        names = ["read_current_screen"] * 4

        async def handler(call):
            return ToolResult(
                call_id=call.call_id,
                name=call.name,
                output={"status": "captured"},
                images=[ToolImage(data_b64=_png_b64(32, 32), mime="image/png")],
            )

        client = _client(handler=handler, cap=6)
        client._tool_definitions = [
            ToolDefinition(
                name=name,
                description=name,
                parameters={"type": "object", "properties": {}},
            )
            for name in set(names)
        ]
        client.script = [
            [
                LLMStreamChunk(
                    content="",
                    finish_reason="tool_calls",
                    tool_call_deltas=[
                        {
                            "index": 0,
                            "id": str(i),
                            "type": "function",
                            "function": {"name": name, "arguments": "{}"},
                        }
                    ],
                )
            ]
            for i, name in enumerate(names)
        ]
        client.script.append([_text("Title | Part one | Part two."), _text("", "stop")])
        # Capture immutable request images: production releases slots after the turn.
        provider = client.llm.astream
        image_counts = []

        def record(messages, **kwargs):
            image_counts.append(
                sum(
                    1
                    for message in messages
                    if isinstance(message, dict)
                    for part in (
                        message.get("content", [])
                        if isinstance(message.get("content"), list)
                        else []
                    )
                    if isinstance(part, dict) and part.get("type") == "image_url"
                )
            )
            return provider(messages, **kwargs)

        client.llm.astream = record
        await client.stream_text("Inspect the named app and verify its small text")
        assert image_counts == [0, 1, 2, 3, 4]
        assert "".join(_emitted(client)) == "Title | Part one | Part two."
        assert (
            client._conversation_history[-1].content == "Title | Part one | Part two."
        )

    asyncio.run(run())


def test_screen_source_does_not_add_a_recurring_system_notice():
    from tests.unit.test_offline_turn_cancellation_e2e import _client
    from main_logic.tool_calling import ToolDefinition

    client = _client()
    client._tool_definitions = [
        ToolDefinition(name="read_current_screen", description="read", parameters={})
    ]
    client._screen_inspection_last_source = {
        "source_id": "window:7",
        "title": "Checklist",
        "captured_at": 123,
    }
    original = list(client._conversation_history)
    view = client._dialog_messages_for_provider(original)
    assert view == original
    assert client._conversation_history == original
    client._tool_definitions = []
    assert client._dialog_messages_for_provider(original) == original
