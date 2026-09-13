"""Only real downstream admission may acknowledge protected activation PCM."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic.voice_input.activation import ActivationState
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
from main_logic.voice_turn.contracts import AsrSubmitStatus
from tests.unit.test_external_visual_delivery import _make_client
from tests.unit.test_voice_activation_cold_prefix import _cold_harness, _feed, _until
from tests.unit.test_voice_activation_handoff import _harness


pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("endpointing", ["provider", "manual"], ids=["provider-endpoint", "manual-endpoint"])
@pytest.mark.parametrize("caller", ["activation-writer", "direct-submit"])
async def test_lost_readiness_cannot_silently_accept_protected_frame(endpointing, caller):
    async with _cold_harness(endpointing) as h:
        for marker in range(1, 16):
            await _feed(h, marker)
        await _until(h.started.is_set)
        h.release.set()
        await _until(lambda: sum(map(len, h.deliveries)) == 48000)
        activation = h.factory.runtimes[0]
        session = h.sessions[0]
        session.is_ready = False  # Failure callback has not reached Core yet.
        if caller == "activation-writer":
            await _feed(h, 99)
        else:
            result = await h.manager._asr_runtime.submit(
                ProcessedVoiceFrame((99).to_bytes(2, "little") * 1600, 16000, .9, True),
                ingress_token=h.manager._capture_ingress_token(),
                preserve_prefix=h.manager._voice_activation_delivery_prefix[2],
            )
            assert result.status is AsrSubmitStatus.UNAVAILABLE
        await _until(lambda: h.manager._asr_route_mode == "blocked")
        assert sum(map(len, h.deliveries)) == 48000
        assert len(h.sessions) == 1
        await _until(lambda: any(
            json.loads(call.args[0]).get("code") == "ASR_INPUT_DELIVERY_UNCERTAIN"
            for call in h.manager.send_status.await_args_list
        ))
        codes = [json.loads(call.args[0]).get("code")
                 for call in h.manager.send_status.await_args_list]
        assert "ASR_INPUT_DELIVERY_UNCERTAIN" in codes
        await _feed(h, 100)
        assert sum(map(len, h.deliveries)) == 48000
        assert activation.state is not ActivationState.ACTIVE


async def test_unprotected_submit_retains_existing_restart_behavior():
    async with _cold_harness() as h:
        for marker in range(1, 16):
            await _feed(h, marker)
        await _until(h.started.is_set)
        h.release.set()
        await _until(lambda: sum(map(len, h.deliveries)) == 48000)
        assert not h.lifecycle.prefix_protected
        old_session = h.sessions[0]
        old_session.is_ready = False
        # No PreserveUnsentPrefix: the ordinary submit API keeps its existing
        # restart policy; the new fail-closed rule belongs to protected delivery.
        result = await h.manager._asr_runtime.submit(
            ProcessedVoiceFrame(bytes(3200), 16000, .9, True),
            ingress_token=h.manager._capture_ingress_token(),
        )
        assert result.status is AsrSubmitStatus.ACCEPTED
        await _until(lambda: len(h.sessions) == 2 and h.sessions[1].is_ready)
        old_session.close.assert_awaited_once()
        assert h.manager._asr_route_mode == "independent"
        assert sum(map(len, h.deliveries)) == 48000


@pytest.mark.parametrize("outcome", ["success", "transient", "closed", "missing"])
async def test_gemini_activation_uses_actual_send_outcome(outcome):
    async with _harness("native", active=False) as h:
        client = _make_client("gemini", "gemini-test")
        client._audio_processor = None
        client._connection_generation = 1
        sent = []

        async def send(**kwargs):
            if outcome != "success":
                raise RuntimeError("connection closed" if outcome == "closed" else "transient send failure")
            sent.append(kwargs["audio"]["data"])

        client._gemini_session = None if outcome == "missing" else SimpleNamespace(
            send_realtime_input=AsyncMock(side_effect=send),
        )
        client.close = AsyncMock()
        h.manager.handle_connection_error = AsyncMock()
        h.manager.session = client
        h.manager.session_closed_by_server = False
        for marker in range(3000, 3015):
            await h.feed(marker)
        runtime = h.manager._voice_session_activation_runtime
        await _until(lambda: runtime.state in {
            ActivationState.ACTIVE, ActivationState.UNAVAILABLE,
        })
        if outcome == "success":
            assert runtime.state is ActivationState.ACTIVE
            assert len(sent) == 15
            client.close.assert_not_awaited()
        else:
            assert runtime.state is ActivationState.UNAVAILABLE
            assert not sent
            await _until(lambda: client.close.await_count == 1)
            await h.manager.set_voice_session_activation_factory(
                None, activation_generation="disabled",
            )
            await h.feed(4000)
            assert not sent


@pytest.mark.parametrize("receipt", [True, False], ids=["written", "not-written"])
async def test_non_gemini_activation_uses_actual_send_receipt(receipt):
    async with _harness("native") as h:
        client = _make_client("openai", "gpt-4o-realtime")
        client._audio_processor = None
        client._connection_generation = 1
        client.send_event = AsyncMock(return_value=receipt)
        client.close = AsyncMock()
        h.manager.handle_connection_error = AsyncMock()
        h.manager.session = client
        h.manager.session_closed_by_server = False

        for index in range(15):
            h.clock.value += 0.1
            await h.feed(3_000 + index)

        runtime = h.manager._voice_session_activation_runtime
        expected = ActivationState.ACTIVE if receipt else ActivationState.UNAVAILABLE
        await _until(lambda: runtime.state is expected)
        assert client.send_event.await_count == (15 if receipt else 2)
        if not receipt:
            await _until(lambda: client.close.await_count == 1)


async def test_non_gemini_local_buffer_is_not_reported_as_transport_write():
    async with _harness("native") as h:
        client = _make_client("openai", "gpt-4o-realtime")
        client._audio_processor = None
        client._connection_generation = 1
        client._resample_uplink = MagicMock(return_value=b"")
        client.send_event = AsyncMock()
        h.manager.session = client
        h.manager.session_closed_by_server = False
        transport_written_bytes = h.manager._omni_mic_audio_bytes

        for index in range(15):
            h.clock.value += 0.1
            await h.feed(4_000 + index)

        runtime = h.manager._voice_session_activation_runtime
        await _until(lambda: runtime.state is ActivationState.ACTIVE)
        client.send_event.assert_not_awaited()
        assert h.manager._omni_mic_audio_bytes == transport_written_bytes


@pytest.mark.parametrize("outcome", ["success", "closed"])
async def test_gemini_old_send_cannot_mutate_successor_connection(outcome):
    client = _make_client("gemini", "gemini-test")
    entered, release = asyncio.Event(), asyncio.Event()
    error = RuntimeError("old connection closed")

    async def send(**kwargs):
        entered.set()
        await release.wait()
        if outcome == "closed":
            raise error

    client._gemini_session = SimpleNamespace(send_realtime_input=send)
    task = asyncio.create_task(client._stream_audio_gemini(bytes(3200)))
    await entered.wait()
    client._gemini_session = SimpleNamespace(send_realtime_input=AsyncMock())
    client._connection_generation += 1
    client._fatal_error_occurred = False
    client._last_speech_time = 123
    release.set()
    if outcome == "closed":
        with pytest.raises(RuntimeError) as raised:
            await task
        assert raised.value is error
    else:
        await task
    assert not client._fatal_error_occurred
    assert client._last_speech_time == 123
