"""Segmented SDK/HTTP dispatch is an attempt, not definitive non-delivery."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from main_logic.asr_client._infra import AsrSessionConfig, _AsrWorkerRequest, _RealtimeAsrSessionImpl
from main_logic.asr_client.delivery import delivery_evidence
from main_logic.asr_client.runtime import IndependentAsrRuntime
from main_logic.asr_client.workers import gemini, glm
from main_logic.asr_client.workers.gemini import gemini_asr_worker
from main_logic.asr_client.workers.glm import glm_asr_worker
from tests.unit.test_asr_glm_worker import _FakeResponse


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["gemini", "glm"])
async def test_connected_session_prefix_overflow_is_definite_non_delivery(provider):
    from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
    from main_logic.voice_turn.contracts import AsrSubmitStatus
    from tests.support.asr_delivery_fakes import _cold_runtime, _close

    dispatch = AsyncMock(side_effect=AssertionError("prefix must not be dispatched"))
    worker_entry_evidence = []

    async def worker(requests, responses, api_key, config):
        # Observe before the provider worker gets a chance to initialize it.
        worker_entry_evidence.append(
            getattr(requests, "_transport_delivery_evidence", None)
        )
        if provider == "gemini":
            client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=dispatch)))
            await gemini_asr_worker(requests, responses, api_key, config, client=client)
        else:
            await glm_asr_worker(requests, responses, api_key, config, http_client=SimpleNamespace(post=dispatch))

    session = _RealtimeAsrSessionImpl(
        worker_fn=worker, api_key="key", config=AsrSessionConfig(endpointing_mode="manual"),
        on_input_transcript=AsyncMock(), on_connection_error=AsyncMock(),
    )
    manager, lifecycle, detector, token, prefix, *_ = _cold_runtime()
    try:
        assert session.transport_write_attempted is None
        await session.connect()
        assert worker_entry_evidence[0] is not None
        assert session.transport_write_attempted is False
        assert not worker_entry_evidence[0].protected
        manager._asr_runtime._asr_session = session
        assert manager._asr_runtime._protected_delivery_failure_code() == "ASR_INPUT_DELIVERY_FAILED"

        # Exercise the actual overflow path before protect_audio_delivery can
        # create evidence as a side effect or any audio can reach the worker.
        lifecycle._pre_roll.append(b"\x01\x00" * 160)
        lifecycle._pending_connect.append(bytes(lifecycle.prefix_capacity_bytes))
        result = await manager._asr_runtime.submit(
            ProcessedVoiceFrame(bytes(320), 16000, .9, True),
            ingress_token=token, preserve_prefix=prefix,
        )
        assert result.status is AsrSubmitStatus.UNAVAILABLE
        codes = [json.loads(call.args[0]).get("code") for call in manager.send_status.await_args_list]
        assert codes.count("ASR_INPUT_DELIVERY_FAILED") == 1
        assert "ASR_INPUT_DELIVERY_UNCERTAIN" not in codes
        assert not lifecycle.prefix_protected
        detector.feed.assert_not_awaited()
        dispatch.assert_not_awaited()
    finally:
        await session.close()
        await _close(manager)


@pytest.mark.parametrize("session", [SimpleNamespace(), SimpleNamespace(transport_write_attempted=None)])
def test_missing_transport_evidence_remains_uncertain(session):
    host = SimpleNamespace(_asr_session=session)
    assert IndependentAsrRuntime._protected_delivery_failure_code(host) == "ASR_INPUT_DELIVERY_UNCERTAIN"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["gemini", "glm"])
@pytest.mark.parametrize("outcome", ["success", "error", "cancel", "timeout", "encode_error"])
async def test_segmented_dispatch_records_attempt_before_await(provider, outcome, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    requests, responses = asyncio.Queue(), asyncio.Queue()
    pcm = b"\x01\x00" * 1600

    if outcome == "timeout" and provider == "gemini":
        monkeypatch.setattr(gemini, "_REQUEST_TIMEOUT_SECONDS", 0.1)
    if outcome == "encode_error":
        def fail_encoding(_pcm):
            raise ValueError("cannot encode local audio")
        monkeypatch.setattr(gemini if provider == "gemini" else glm, "encode_pcm16_wav", fail_encoding)

    async def dispatch(*args, **kwargs):
        entered.set()
        await release.wait()
        if outcome in {"error", "timeout"}:
            raise httpx.ReadTimeout("response lost after dispatch")
        return SimpleNamespace(parsed={"transcript": "hello"}) if provider == "gemini" else _FakeResponse({"text": "hello"})

    config = AsrSessionConfig(endpointing_mode="manual")
    if provider == "gemini":
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=dispatch)))
        worker = gemini_asr_worker(requests, responses, "key", config, client=client)
    else:
        worker = glm_asr_worker(requests, responses, "key", config, http_client=SimpleNamespace(post=dispatch))
    task = asyncio.create_task(worker)
    evidence = delivery_evidence(requests)
    session = _RealtimeAsrSessionImpl(
        worker_fn=gemini_asr_worker if provider == "gemini" else glm_asr_worker,
        api_key="key", config=config, on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
    )
    session._request_queue = requests
    session.protect_audio_delivery()
    host = SimpleNamespace(_asr_session=session)
    try:
        assert (await asyncio.wait_for(responses.get(), 1)).kind == "ready"
        requests.put_nowait(_AsrWorkerRequest("clear", 1, 0))
        requests.put_nowait(_AsrWorkerRequest("audio", 1, 0, 1, pcm))
        await asyncio.wait_for(requests.join(), 1)
        assert not evidence.attempted and evidence.written_audio_bytes == 0
        assert IndependentAsrRuntime._protected_delivery_failure_code(host) == "ASR_INPUT_DELIVERY_FAILED"
        requests.put_nowait(_AsrWorkerRequest("commit", 1, 0, 1))
        if outcome == "encode_error":
            assert (await asyncio.wait_for(responses.get(), 1)).kind == "error"
            assert not entered.is_set() and not evidence.attempted
            assert IndependentAsrRuntime._protected_delivery_failure_code(host) == "ASR_INPUT_DELIVERY_FAILED"
            return
        await asyncio.wait_for(entered.wait(), 1)
        assert evidence.attempted and evidence.written_audio_bytes == 0
        assert IndependentAsrRuntime._protected_delivery_failure_code(host) == "ASR_INPUT_DELIVERY_UNCERTAIN"
        if outcome == "cancel":
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        else:
            if outcome != "timeout" or provider != "gemini":
                release.set()
            event = await asyncio.wait_for(responses.get(), 1)
            assert event.kind == ("final" if outcome == "success" else "error")
        assert evidence.written_audio_bytes == (len(pcm) if outcome == "success" else 0)
        assert not delivery_evidence(asyncio.Queue()).attempted
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

pytestmark = pytest.mark.runtime
