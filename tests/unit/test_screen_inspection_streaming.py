"""Exercise real stream_text emission and persisted reply for literal title pipes."""

import asyncio
import base64
import copy
import io
import json
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


@pytest.mark.parametrize("reason", ["disabled", "scope_changed", "superseded"])
@pytest.mark.parametrize("same_round", [False, True])
@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_screen_denial_releases_evidence_before_followup(reason, same_round, provider, monkeypatch):
    import main_logic.omni_offline_client._genai_support as genai
    from main_logic.tool_calling import ToolDefinition, ToolImage, ToolResult
    from tests.unit.test_offline_turn_cancellation_e2e import _client, _text
    from tests.unit.test_offline_provider_frame_publish import _png_b64
    from tests.unit.test_tool_calling import _GenaiChunk, _GenaiPart, _GenaiFunctionCall

    monkeypatch.setattr(genai, "_GENAI_AVAILABLE", True)
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", lambda: None)
    pixels = _png_b64(32, 32)
    private_title = "private-window-title-before-revocation"

    async def handler(call):
        if call.call_id == "overview":
            return ToolResult(
                call_id=call.call_id, name=call.name,
                output={
                    "status": "captured", "snapshot_id": "old-snapshot",
                    "windows": [{"window_id": "window:9", "title": private_title}],
                },
                images=[ToolImage(data_b64=pixels, mime="image/png")],
            )
        return ToolResult(
            call_id=call.call_id, name=call.name,
            output={"status": "unavailable", "reason": reason}, is_error=True,
        )

    def tool_calls(*ids):
        if provider == "gemini":
            return _GenaiChunk([
                _GenaiPart(function_call=_GenaiFunctionCall("read_current_screen", id_=call_id))
                for call_id in ids
            ])
        return LLMStreamChunk(
            content="", finish_reason="tool_calls",
            tool_call_deltas=[
                {
                    "index": index, "id": call_id, "type": "function",
                    "function": {"name": "read_current_screen", "arguments": "{}"},
                }
                for index, call_id in enumerate(ids)
            ],
        )

    client = _client(provider, handler=handler, cap=4)
    client._tool_definitions = [ToolDefinition(
        name="read_current_screen", description="read",
        parameters={"type": "object", "properties": {}},
    )]
    answer = ([_text("Reading is unavailable."), _text("", "stop")] if provider == "openai"
              else [_GenaiChunk([_GenaiPart(text="Reading is unavailable.")])])
    client.script = (
        [[tool_calls("overview", "denied")]] if same_round else
        [[tool_calls("overview")], [tool_calls("denied")]]
    ) + [answer]
    requests = []
    if provider == "openai":
        original = client.llm.astream

        def record(messages, **kwargs):
            requests.append(copy.deepcopy(messages))
            return original(messages, **kwargs)

        client.llm.astream = record
    else:
        original = client._genai_client.aio.models.generate_content_stream

        async def record(**kwargs):
            requests.append(copy.deepcopy(kwargs["contents"]))
            return await original(**kwargs)

        client._genai_client.aio.models.generate_content_stream = record
    await client.stream_text("Read the current screen")

    def image_data(view):
        if provider == "gemini":
            return [base64.b64encode(part.inline_data.data).decode()
                    for content in view for part in content.parts if part.inline_data]
        return [part["image_url"]["url"].split(",", 1)[1]
                for message in view if isinstance(message, dict)
                and isinstance(message.get("content"), list)
                for part in message["content"] if part.get("type") == "image_url"]

    def request_text(view):
        if provider == "gemini":
            view = [content.model_dump() for content in view]
        return json.dumps(view, ensure_ascii=False, default=repr)

    assert len(requests) == (2 if same_round else 3)
    if not same_round:
        assert pixels in image_data(requests[1]) and private_title in request_text(requests[1])
    after = request_text(requests[-1])
    assert not image_data(requests[-1]) and private_title not in after
    assert "old-snapshot" not in after
    if provider == "openai":
        replies = [message for message in requests[-1]
                   if isinstance(message, dict) and message.get("role") == "tool"]
        assert [message["tool_call_id"] for message in replies] == ["overview", "denied"]
        outputs = [json.loads(message["content"]) for message in replies]
    else:
        replies = [part.function_response for content in requests[-1]
                   for part in content.parts if part.function_response]
        assert [reply.name for reply in replies] == ["read_current_screen"] * 2
        outputs = [reply.response for reply in replies]
    assert outputs[0]["status"] == "released"
    assert outputs[1]["reason"] == reason


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
