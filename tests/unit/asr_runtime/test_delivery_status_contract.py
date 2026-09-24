import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock
import pytest
from main_logic.asr_client.lifecycle import VoiceTurnToken
from main_logic.voice_turn.contracts import AsrLifecycleNotification, AsrStatusEvent, VoicePartialEvent

from tests.support.asr_fakes import (
    _Runtime,
)

from tests.support.core_asr_harness import (
    _install_active_smart_turn,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


async def test_partial_preview_is_display_only_and_epoch_guarded() -> None:
    runtime = _Runtime()
    websocket = type("WebSocket", (), {})()
    websocket.send_json = AsyncMock()
    runtime.websocket = websocket
    runtime.current_speech_id = "speech-current"
    runtime._set_microphone_route("independent")
    await _install_active_smart_turn(runtime)
    epoch = runtime._asr_session_epoch
    token = runtime._asr_runtime._asr_partial_turn_token
    assert token is not None
    assert runtime._activate_asr_audio_dispatcher(runtime._asr_lifecycle, token)

    await runtime._send_independent_asr_preview(" draft ", epoch)
    await runtime._send_independent_asr_preview("stale", epoch + 1)

    websocket.send_json.assert_awaited_once_with(
        {
            "type": "user_transcript_preview",
            "text": "draft",
            "turn_id": "speech-current",
            "asr_turn_id": f"asr-{epoch}-1",
        }
    )
    runtime.handle_input_transcript.assert_not_awaited()


async def test_partial_preview_requires_current_core_lease() -> None:
    runtime = _Runtime()
    websocket = type("WebSocket", (), {})()
    websocket.send_json = AsyncMock()
    runtime.websocket = websocket
    runtime._set_microphone_route("independent")
    epoch = runtime._asr_session_epoch
    token = VoiceTurnToken(
        ingress=runtime._capture_ingress_token(),
        turn_id=1,
    )
    stale_token = VoiceTurnToken(
        ingress=replace(token.ingress, session_epoch=epoch + 1),
        turn_id=token.turn_id,
    )

    runtime._voice_lease_owner = "game"
    await runtime._send_core_asr_preview(
        VoicePartialEvent(turn_token=token, text="game")
    )
    runtime._voice_lease_owner = "core"
    runtime._voice_lease_hard_muted = True
    await runtime._send_core_asr_preview(
        VoicePartialEvent(turn_token=token, text="muted")
    )
    runtime._voice_lease_hard_muted = False
    runtime._voice_lease_focus_suppressed = True
    await runtime._send_core_asr_preview(
        VoicePartialEvent(turn_token=token, text="focused")
    )
    runtime._voice_lease_focus_suppressed = False
    await runtime._send_core_asr_preview(
        VoicePartialEvent(turn_token=stale_token, text="stale")
    )
    await runtime._send_core_asr_preview(
        VoicePartialEvent(turn_token=token, text="current")
    )

    websocket.send_json.assert_awaited_once_with(
        {
            "type": "user_transcript_preview",
            "text": "current",
            "turn_id": f"asr-preview-{epoch}",
        }
    )


async def test_old_notifications_cannot_override_new_generation() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    first_send_entered = asyncio.Event()
    release_first_send = asyncio.Event()
    payloads = []

    async def ordered_send_status(payload: str) -> None:
        payloads.append(json.loads(payload))
        if len(payloads) == 1:
            first_send_entered.set()
            await release_first_send.wait()

    runtime.send_status = AsyncMock(side_effect=ordered_send_status)
    old_epoch = runtime._asr_session_epoch
    old_event = AsrLifecycleNotification(
        state="local_listen",
        provider="old-provider",
        session_epoch=old_epoch,
    )
    old_delivery = asyncio.create_task(runtime._send_core_asr_lifecycle(old_event))
    await asyncio.wait_for(first_send_entered.wait(), 1)

    runtime._asr_session_epoch += 1
    new_epoch = runtime._asr_session_epoch
    new_event = AsrLifecycleNotification(
        state="blocked",
        provider="new-provider",
        session_epoch=new_epoch,
    )
    new_delivery = asyncio.create_task(runtime._send_core_asr_lifecycle(new_event))
    release_first_send.set()
    await asyncio.wait_for(
        asyncio.gather(old_delivery, new_delivery),
        1,
    )
    await runtime._send_core_asr_lifecycle(old_event)
    await runtime._send_core_asr_status(
        AsrStatusEvent(
            code="ASR_OLD_READY",
            provider="old-provider",
            session_epoch=old_epoch,
        )
    )
    await runtime._send_core_asr_status(
        AsrStatusEvent(
            code="ASR_NEW_READY",
            provider="new-provider",
            session_epoch=new_epoch,
        )
    )

    assert [
        payload["details"]["state"]
        for payload in payloads
        if payload["code"] == "ASR_LIFECYCLE_STATE"
    ] == ["local_listen", "blocked"]
    assert payloads[-1] == {
        "code": "ASR_NEW_READY",
        "details": {
            "provider": "new-provider",
            "session_epoch": new_epoch,
        },
    }
    assert payloads[1]["details"]["session_epoch"] == new_epoch


async def test_blocked_text_notice_commits_only_for_current_connection() -> None:
    runtime = _Runtime()
    runtime.input_mode = "text"
    assert runtime._begin_voice_input_connection("chat-window") is True
    runtime._set_microphone_route("blocked")
    send_started = asyncio.Event()
    release_send = asyncio.Event()

    async def block_send(_message: str) -> None:
        send_started.set()
        await release_send.wait()

    runtime.send_status = AsyncMock(side_effect=block_send)
    first = asyncio.create_task(
        runtime._maybe_signal_blocked_text_mode_microphone()
    )
    await asyncio.wait_for(send_started.wait(), 1)

    assert runtime._begin_voice_input_connection("pet-window") is True
    release_send.set()
    await asyncio.wait_for(first, 1)

    assert runtime._blocked_text_mode_microphone_signal_state is None
    runtime.send_status = AsyncMock()
    await runtime._maybe_signal_blocked_text_mode_microphone()

    runtime.send_status.assert_awaited_once()
    assert runtime._blocked_text_mode_microphone_signal_state is not None


async def test_blocked_text_episode_keeps_session_identity_reference() -> None:
    runtime = _Runtime()
    runtime.input_mode = "text"
    runtime._set_microphone_route("blocked")
    session = runtime.session

    episode = runtime._blocked_text_mode_microphone_episode()

    assert episode is not None
    assert episode[-1] is session
