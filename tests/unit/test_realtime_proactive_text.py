"""Realtime proactive turns use text injection, never synthetic user audio."""

import json
import os
import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from main_logic.omni_realtime_client import OmniRealtimeClient, TurnDetectionMode
from main_logic.omni_realtime_client import _responses as responses_module
from main_logic.omni_realtime_client import _transport as transport_module


DUMMY_IMAGE_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP/Z"


def _make_client(*, api_type: str = "free", model: str = "free-model"):
    client = OmniRealtimeClient(
        base_url="wss://www.lanlan.tech/api/v1/realtime",
        api_key="test-key",
        model=model,
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
        api_type=api_type,
    )
    client.ws = AsyncMock()
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0
    return client


def _sent_events(client):
    return [
        json.loads(call_args[0][0])
        for call_args in client.ws.send.call_args_list
    ]


def _input_texts(events):
    texts = []
    for event in events:
        if event.get("type") != "conversation.item.create":
            continue
        for content in event.get("item", {}).get("content", []):
            if content.get("type") == "input_text":
                texts.append(content.get("text"))
    return texts


def _ack_pending_input_item(client, events):
    for event in events:
        if event.get("type") != "conversation.item.create":
            continue
        client._response_arbiter.notify_item_created({
            "type": "conversation.item.created",
            "item": event["item"],
        })


async def _prompt_and_complete(client, *args, **kwargs):
    task = asyncio.create_task(client.prompt_ephemeral(*args, **kwargs))
    for _ in range(20):
        events = _sent_events(client)
        _ack_pending_input_item(client, events)
        if any(
            event.get("type") == "response.create"
            for event in events
        ):
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("prompt_ephemeral did not send response.create")
    client._response_arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-proactive"}}
    )
    client._response_arbiter.notify_response_terminal(
        {
            "type": "response.done",
            "response": {"id": "resp-proactive", "status": "completed"},
        }
    )
    return await task


@pytest.mark.unit
async def test_prompt_ephemeral_injects_text_and_never_audio():
    client = _make_client()

    delivered = await _prompt_and_complete(client, language="zh")

    events = _sent_events(client)
    input_texts = _input_texts(events)
    assert delivered is True
    assert any("主动搭话触发" in text for text in input_texts)
    assert any("不要假设刚刚看到了新的画面或事件" in text for text in input_texts)
    assert not any("屏幕主动搭话触发" in text for text in input_texts)
    assert any(event.get("type") == "response.create" for event in events)
    assert not any(
        event.get("type") == "input_audio_buffer.append"
        for event in events
    )
    await client.close()


@pytest.mark.unit
async def test_prompt_ephemeral_selects_screen_prompt_when_visual_context_exists():
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False

    delivered = await _prompt_and_complete(client, language="zh")

    events = _sent_events(client)
    event_types = [event.get("type") for event in events]
    input_texts = _input_texts(events)
    assert delivered is True
    assert event_types.index("input_image_buffer.append") < event_types.index(
        "conversation.item.create"
    )
    assert any("屏幕主动搭话触发" in text for text in input_texts)
    assert any("画面中的具体内容" in text for text in input_texts)
    assert not any("不要假设刚刚看到了新的画面或事件" in text for text in input_texts)
    await client.close()


@pytest.mark.unit
async def test_free_prompt_sends_native_image_before_text():
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False

    delivered = await _prompt_and_complete(client, "describe what you notice")

    events = _sent_events(client)
    event_types = [event.get("type") for event in events]
    assert delivered is True
    assert event_types.index("input_image_buffer.append") < event_types.index(
        "conversation.item.create"
    )
    assert _input_texts(events) == ["describe what you notice"]
    assert client._proactive_image_consumed is True
    await client.close()


@pytest.mark.unit
async def test_free_image_async_rejection_preserves_snapshot_and_cancels_response():
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False
    task = asyncio.create_task(
        client.prompt_ephemeral("describe what you notice")
    )
    image_event = None
    for _ in range(30):
        events = _sent_events(client)
        _ack_pending_input_item(client, events)
        image_event = next(
            (
                event
                for event in events
                if event.get("type") == "input_image_buffer.append"
            ),
            None,
        )
        if image_event is not None and any(
            event.get("type") == "response.create"
            for event in events
        ):
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("native image and proactive response were not sent")

    assert image_event.get("event_id")
    client._response_arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-image-rejected"}}
    )
    client._route_inject_rejection(
        image_event["event_id"],
        "input image rejected",
    )
    for _ in range(30):
        if any(
            event.get("type") == "response.cancel"
            for event in _sent_events(client)
        ):
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("rejected visual response was not cancelled")
    client._response_arbiter.notify_response_terminal(
        {
            "type": "response.done",
            "response": {
                "id": "resp-image-rejected",
                "status": "cancelled",
            },
        }
    )

    assert await task is False
    assert client._proactive_image_consumed is False
    assert client._inject_rejection_handlers == {}
    await client.close()


@pytest.mark.unit
async def test_server_vad_prompt_rotates_tts_sid_before_text_response():
    client = _make_client()
    client.on_sid_rotate = AsyncMock()

    delivered = await _prompt_and_complete(client, "start a new TTS turn")

    assert delivered is True
    client.on_sid_rotate.assert_awaited_once_with()
    await client.close()


@pytest.mark.unit
async def test_delayed_inject_rejection_returns_false_and_preserves_image():
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False

    async def reject_after_send(_text, *, on_rejected, on_completed):
        async def reject():
            # The rejection may arrive well after send_event() returned.
            await asyncio.sleep(0.02)
            on_rejected("response_already_active")

        asyncio.create_task(reject())

    client.inject_text_and_request_response = reject_after_send

    delivered = await client.prompt_ephemeral("describe what you notice")

    assert delivered is False
    assert client._proactive_image_consumed is False
    assert client._latest_image_b64 == DUMMY_IMAGE_B64
    await client.close()


@pytest.mark.unit
async def test_failed_response_done_returns_false_and_preserves_image():
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False

    task = asyncio.create_task(
        client.prompt_ephemeral("describe what you notice")
    )
    for _ in range(20):
        events = _sent_events(client)
        _ack_pending_input_item(client, events)
        if any(
            event.get("type") == "response.create"
            for event in events
        ):
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("prompt_ephemeral did not send response.create")
    client._response_arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-proactive-failed"}}
    )
    client._response_arbiter.notify_response_terminal(
        {
            "type": "response.done",
            "response": {
                "id": "resp-proactive-failed",
                "status": "cancelled",
            },
        }
    )

    assert await task is False
    assert client._proactive_image_consumed is False
    assert client._latest_image_b64 == DUMMY_IMAGE_B64
    await client.close()


@pytest.mark.unit
async def test_inject_completion_waits_for_its_own_queued_response_done():
    client = _make_client()
    earlier = await client._response_arbiter.enqueue(source="earlier")
    await earlier.sent

    completed = asyncio.Event()
    rejected = []
    task = asyncio.create_task(
        client.inject_text_and_request_response(
            "queued proactive turn",
            on_completed=completed.set,
            on_rejected=rejected.append,
        )
    )
    await asyncio.sleep(0)

    client._response_arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-earlier"}}
    )
    client._response_arbiter.notify_response_terminal(
        {
            "type": "response.done",
            "response": {"id": "resp-earlier", "status": "completed"},
        }
    )
    await earlier.done

    for _ in range(20):
        events = _sent_events(client)
        _ack_pending_input_item(client, events)
        response_creates = [
            event for event in events if event.get("type") == "response.create"
        ]
        if len(response_creates) >= 2:
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("queued proactive response.create was not sent")

    returned_ticket = await task
    assert returned_ticket is not None
    assert completed.is_set() is False
    assert rejected == []
    client._response_arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-proactive"}}
    )
    client._response_arbiter.notify_response_terminal(
        {
            "type": "response.done",
            "response": {"id": "resp-proactive", "status": "completed"},
        }
    )
    for _ in range(20):
        if completed.is_set():
            break
        await asyncio.sleep(0)
    assert completed.is_set() is True
    assert rejected == []
    await client.close()


@pytest.mark.unit
async def test_prompt_defers_sid_rotation_while_response_arbiter_is_busy():
    client = _make_client()
    client.on_sid_rotate = AsyncMock()
    earlier = await client._response_arbiter.enqueue(source="earlier")
    await earlier.sent

    delivered = await client.prompt_ephemeral("defer this proactive turn")

    assert delivered is False
    client.on_sid_rotate.assert_not_awaited()
    assert len(
        [
            event
            for event in _sent_events(client)
            if event.get("type") == "response.create"
        ]
    ) == 1
    client._response_arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-earlier"}}
    )
    client._response_arbiter.notify_response_terminal(
        {
            "type": "response.done",
            "response": {"id": "resp-earlier", "status": "completed"},
        }
    )
    await earlier.done
    await client.close()


@pytest.mark.unit
async def test_prompt_rechecks_arbiter_after_visual_await_before_sid_rotation():
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False
    client.on_sid_rotate = AsyncMock()
    visual_started = asyncio.Event()
    release_visual = asyncio.Event()

    async def delayed_stream_image(*_args, **_kwargs):
        visual_started.set()
        await release_visual.wait()

    client.stream_image = delayed_stream_image
    task = asyncio.create_task(client.prompt_ephemeral("do not rotate this SID"))
    await visual_started.wait()
    earlier = await client._response_arbiter.enqueue(source="user-turn", priority=0)
    release_visual.set()

    assert await task is False
    client.on_sid_rotate.assert_not_awaited()
    client._response_arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-user"}}
    )
    client._response_arbiter.notify_response_terminal(
        {
            "type": "response.done",
            "response": {"id": "resp-user", "status": "completed"},
        }
    )
    await earlier.done
    await client.close()


@pytest.mark.unit
async def test_prompt_rechecks_activity_after_sid_rotation_await():
    client = _make_client()
    sid_rotation_started = asyncio.Event()
    release_sid_rotation = asyncio.Event()

    async def delayed_sid_rotation():
        sid_rotation_started.set()
        await release_sid_rotation.wait()

    client.on_sid_rotate = delayed_sid_rotation
    client.inject_text_and_request_response = AsyncMock()
    task = asyncio.create_task(
        client.prompt_ephemeral("do not inject after user activity")
    )
    await sid_rotation_started.wait()
    client._user_recent_activity_time = responses_module.time.time()
    client._client_vad_active = True
    release_sid_rotation.set()

    assert await task is False
    client.inject_text_and_request_response.assert_not_awaited()
    await client.close()


@pytest.mark.unit
async def test_delivery_timeout_cancels_and_quarantines_until_lifecycle(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(
        responses_module,
        "_PROACTIVE_INJECT_DELIVERY_TIMEOUT_SECONDS",
        0.01,
    )

    delivered = await asyncio.wait_for(
        client.prompt_ephemeral("retry after timeout"),
        timeout=3,
    )

    assert delivered is False
    assert _sent_events(client)[-1]["type"] == "response.cancel"
    assert client._proactive_inject_awaiting_outcome is True
    assert client._inject_rejection_handlers

    client._response_arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-timeout"}}
    )
    assert client._proactive_inject_awaiting_outcome is True
    client._response_arbiter.notify_response_terminal(
        {
            "type": "response.done",
            "response": {"id": "resp-timeout", "status": "cancelled"},
        }
    )
    for _ in range(20):
        if not client._proactive_inject_awaiting_outcome:
            break
        await asyncio.sleep(0)
    assert client._proactive_inject_awaiting_outcome is False
    assert client._inject_rejection_handlers == {}
    await client.close()


def _gemini_lifecycle_response(*, turn_complete=False, interrupted=False):
    return SimpleNamespace(
        tool_call=None,
        server_content=SimpleNamespace(
            input_transcription=None,
            model_turn=None,
            output_transcription=None,
            turn_complete=turn_complete,
            interrupted=interrupted,
        ),
    )


@pytest.mark.unit
async def test_gemini_prompt_waits_for_turn_complete():
    client = _make_client(api_type="gemini", model="gemini-live")
    client._gemini_session = AsyncMock()
    client.on_response_done = AsyncMock()

    task = asyncio.create_task(client.prompt_ephemeral("wait for Gemini"))
    for _ in range(20):
        if client._gemini_session.send_client_content.await_count:
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("Gemini proactive turn was not sent")

    assert task.done() is False
    assert client._proactive_inject_awaiting_outcome is True
    await client._process_gemini_response(
        _gemini_lifecycle_response(turn_complete=True)
    )

    assert await task is True
    assert client._proactive_inject_awaiting_outcome is False
    client.on_response_done.assert_awaited_once_with()
    await client.close()


@pytest.mark.unit
async def test_gemini_prompt_rejects_interrupted_lifecycle():
    client = _make_client(api_type="gemini", model="gemini-live")
    client._gemini_session = AsyncMock()

    task = asyncio.create_task(client.prompt_ephemeral("interrupt Gemini"))
    for _ in range(20):
        if client._gemini_session.send_client_content.await_count:
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("Gemini proactive turn was not sent")

    await client._process_gemini_response(
        _gemini_lifecycle_response(interrupted=True)
    )

    assert await task is False
    assert client._proactive_inject_awaiting_outcome is False
    await client.close()


@pytest.mark.unit
async def test_gemini_delivery_timeout_uses_native_client_content_interrupt(monkeypatch):
    client = _make_client(api_type="gemini", model="gemini-live")
    client._gemini_session = AsyncMock()
    monkeypatch.setattr(
        responses_module,
        "_PROACTIVE_INJECT_DELIVERY_TIMEOUT_SECONDS",
        0.01,
    )

    delivered = await client.prompt_ephemeral("time out Gemini")

    assert delivered is False
    assert client._gemini_session.send_client_content.await_count == 2
    cancel_call = client._gemini_session.send_client_content.await_args_list[-1]
    assert cancel_call.kwargs == {"turns": None, "turn_complete": False}
    assert client._proactive_inject_awaiting_outcome is True
    await client._process_gemini_response(
        _gemini_lifecycle_response(interrupted=True)
    )
    assert client._proactive_inject_awaiting_outcome is False
    await client.close()


@pytest.mark.unit
async def test_gemini_cancelled_send_clears_pending_outcome():
    client = _make_client(api_type="gemini", model="gemini-live")
    client._gemini_session = AsyncMock()
    send_started = asyncio.Event()
    hold_send = asyncio.Event()

    async def blocked_send(*_args, **_kwargs):
        send_started.set()
        await hold_send.wait()

    client._gemini_session.send_client_content.side_effect = blocked_send
    task = asyncio.create_task(
        client.inject_text_and_request_response(
            "cancel Gemini SDK send",
            on_rejected=lambda _message: None,
            on_completed=lambda: None,
        )
    )
    await send_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        assert await task is None

    assert client._gemini_proactive_outcome is None
    assert client._proactive_inject_outcome_token is None
    assert client._proactive_inject_awaiting_outcome is False
    await client.close()


@pytest.mark.unit
async def test_delivery_timeout_cancels_only_returned_proactive_ticket(monkeypatch):
    client = _make_client()
    ticket = object()
    client.inject_text_and_request_response = AsyncMock(return_value=ticket)
    client._response_arbiter.cancel_ticket = AsyncMock()
    client.cancel_response = AsyncMock()
    monkeypatch.setattr(
        responses_module,
        "_PROACTIVE_INJECT_DELIVERY_TIMEOUT_SECONDS",
        0.01,
    )

    delivered = await client.prompt_ephemeral("time out exact ticket")

    assert delivered is False
    client._response_arbiter.cancel_ticket.assert_awaited_once_with(
        ticket,
        wait=False,
    )
    client.cancel_response.assert_not_awaited()
    await client.close()


@pytest.mark.unit
async def test_delivery_timeout_acknowledges_exact_ticket_already_completed(monkeypatch):
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False
    ticket_done = asyncio.get_running_loop().create_future()
    ticket_done.set_result(SimpleNamespace())
    ticket = SimpleNamespace(done=ticket_done)
    client.inject_text_and_request_response = AsyncMock(return_value=ticket)
    client._response_arbiter.cancel_ticket = AsyncMock()
    client.cancel_response = AsyncMock()
    monkeypatch.setattr(
        responses_module,
        "_PROACTIVE_INJECT_DELIVERY_TIMEOUT_SECONDS",
        0.01,
    )

    delivered = await client.prompt_ephemeral("complete at timeout boundary")

    assert delivered is True
    assert client._proactive_image_consumed is True
    client._response_arbiter.cancel_ticket.assert_not_awaited()
    client.cancel_response.assert_not_awaited()
    await client.close()


@pytest.mark.unit
async def test_delivery_timeout_rechecks_ticket_after_cancel_noop(monkeypatch):
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False
    ticket_done = asyncio.get_running_loop().create_future()
    ticket = SimpleNamespace(done=ticket_done)
    client.inject_text_and_request_response = AsyncMock(return_value=ticket)

    async def terminal_cancel_noop(*_args, **_kwargs):
        ticket_done.set_result(SimpleNamespace())
        return False

    client._response_arbiter.cancel_ticket = AsyncMock(
        side_effect=terminal_cancel_noop
    )
    client.cancel_response = AsyncMock()
    monkeypatch.setattr(
        responses_module,
        "_PROACTIVE_INJECT_DELIVERY_TIMEOUT_SECONDS",
        0.01,
    )

    delivered = await client.prompt_ephemeral("finish during cancel decision")

    assert delivered is True
    assert client._proactive_image_consumed is True
    client._response_arbiter.cancel_ticket.assert_awaited_once_with(
        ticket,
        wait=False,
    )
    client.cancel_response.assert_not_awaited()
    await client.close()


@pytest.mark.unit
async def test_cancelled_completion_wait_cancels_exact_proactive_ticket():
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False
    task = asyncio.create_task(
        client.prompt_ephemeral("cancel while waiting for response.done")
    )
    for _ in range(20):
        events = _sent_events(client)
        _ack_pending_input_item(client, events)
        if any(event.get("type") == "response.create" for event in events):
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("proactive response.create was not sent")
    client._response_arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-cancel-wait"}}
    )

    task.cancel()
    for _ in range(20):
        if any(
            event.get("type") == "response.cancel"
            for event in _sent_events(client)
        ):
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("targeted proactive cancellation was not sent")
    client._response_arbiter.notify_response_terminal(
        {
            "type": "response.done",
            "response": {
                "id": "resp-cancel-wait",
                "status": "cancelled",
            },
        }
    )

    with pytest.raises(asyncio.CancelledError):
        assert await task is None
    assert client._proactive_inject_awaiting_outcome is False
    assert client._proactive_image_consumed is False
    await client.close()


@pytest.mark.unit
async def test_cancelled_queued_inject_cleans_gate_and_never_dispatches():
    client = _make_client()
    earlier = await client._response_arbiter.enqueue(source="user-turn", priority=0)
    await earlier.sent
    task = asyncio.create_task(
        client.inject_text_and_request_response(
            "cancel this queued proactive",
            on_rejected=lambda _message: None,
            on_completed=lambda: None,
        )
    )
    for _ in range(20):
        if client._proactive_inject_awaiting_outcome:
            break
        await asyncio.sleep(0)
    assert client._proactive_inject_awaiting_outcome is True

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        assert await task is None

    assert client._proactive_inject_awaiting_outcome is False
    assert client._inject_rejection_handlers == {}
    client._response_arbiter.notify_response_created(
        {"type": "response.created", "response": {"id": "resp-user"}}
    )
    client._response_arbiter.notify_response_terminal(
        {
            "type": "response.done",
            "response": {"id": "resp-user", "status": "completed"},
        }
    )
    await earlier.done
    for _ in range(20):
        if not client._response_arbiter.is_busy:
            break
        await asyncio.sleep(0)
    assert len(
        [
            event
            for event in _sent_events(client)
            if event.get("type") == "response.create"
        ]
    ) == 1
    await client.close()


@pytest.mark.unit
async def test_sync_inject_failure_returns_false_and_preserves_image():
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False

    async def fail_inject(_text, *, on_rejected, on_completed):
        raise RuntimeError("websocket disconnected")

    client.inject_text_and_request_response = fail_inject

    delivered = await client.prompt_ephemeral("describe what you notice")

    assert delivered is False
    assert client._proactive_image_consumed is False
    assert client._latest_image_b64 == DUMMY_IMAGE_B64
    await client.close()


@pytest.mark.unit
async def test_prompt_skips_while_another_proactive_inject_awaits_outcome():
    client = _make_client()
    client._proactive_inject_awaiting_outcome = True

    delivered = await client.prompt_ephemeral("do not overlap")

    assert delivered is False
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.unit
async def test_gemini_image_send_failure_preserves_snapshot_and_skips_text():
    client = _make_client(api_type="gemini", model="gemini-live")
    client._gemini_session = AsyncMock()
    client._gemini_session.send_realtime_input.side_effect = RuntimeError(
        "transient image send failure"
    )
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False

    delivered = await client.prompt_ephemeral("describe what you notice")

    assert delivered is False
    assert client._proactive_image_consumed is False
    client._gemini_session.send_client_content.assert_not_awaited()
    await client.close()


@pytest.mark.unit
async def test_oversized_native_image_drop_preserves_snapshot(monkeypatch):
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False
    monkeypatch.setattr(transport_module, "OMNI_WS_FRAME_LIMIT_BYTES", 1)
    monkeypatch.setattr(
        type(client),
        "_try_shrink_image_payload",
        staticmethod(lambda _event, _payload: None),
    )

    delivered = await client.prompt_ephemeral("describe what you notice")

    assert delivered is False
    assert client._proactive_image_consumed is False
    assert not any(
        event.get("type") == "response.create"
        for event in _sent_events(client)
    )
    await client.close()


@pytest.mark.unit
async def test_standard_stepfun_uses_annotation_text_before_trigger():
    client = _make_client(api_type="step", model="step-realtime")
    client._image_recognized_this_turn = True
    client._image_description = "画面里有一只猫。"

    delivered = await _prompt_and_complete(client, "start a conversation")

    events = _sent_events(client)
    assert delivered is True
    assert _input_texts(events) == [
        "画面里有一只猫。",
        "start a conversation",
    ]
    assert not any(
        event.get("type") == "input_image_buffer.append"
        for event in events
    )
    await client.close()


@pytest.mark.unit
async def test_standard_stepfun_defers_prompt_until_annotation_is_ready():
    client = _make_client(api_type="step", model="step-realtime")
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False
    client._image_being_analyzed = True
    client._image_recognized_this_turn = False
    client.inject_text_and_request_response = AsyncMock()

    delivered = await client.prompt_ephemeral(language="zh")

    assert delivered is False
    assert client._proactive_image_consumed is False
    client.inject_text_and_request_response.assert_not_awaited()
    assert _sent_events(client) == []
    await client.close()
