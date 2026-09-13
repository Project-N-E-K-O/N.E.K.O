"""Segmented SDK/HTTP dispatch is an attempt, not definitive non-delivery."""

import asyncio
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
