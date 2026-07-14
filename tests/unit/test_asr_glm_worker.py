from __future__ import annotations

import asyncio
import io
import wave
from typing import Any

import httpx
import pytest

from main_logic.asr_client._infra import (
    AsrSessionConfig,
    _AsrWorkerEvent,
    _AsrWorkerRequest,
)
from main_logic.asr_client.workers import glm


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.request = httpx.Request("POST", glm.GLM_ASR_URL)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "provider rejected request",
                request=self.request,
                response=httpx.Response(
                    self.status_code,
                    request=self.request,
                ),
            )

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, *responses: _FakeResponse) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected GLM request")
        return self.responses.pop(0)


class _BlockingClient:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.calls = 0
        self.next_response: _FakeResponse | None = None

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        _ = (url, kwargs)
        self.calls += 1
        if self.next_response is not None:
            return self.next_response
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return _FakeResponse({"text": "stale"})


async def _next_event(
    queue: asyncio.Queue[_AsrWorkerEvent],
    kind: str | None = None,
    *,
    timeout: float = 1.0,
) -> _AsrWorkerEvent:
    while True:
        event = await asyncio.wait_for(queue.get(), timeout)
        queue.task_done()
        if kind is None or event.kind == kind:
            return event


async def _stop_worker(
    task: asyncio.Task[None],
    requests: asyncio.Queue[_AsrWorkerRequest],
    responses: asyncio.Queue[_AsrWorkerEvent],
    *,
    generation: int = 0,
    buffer_epoch: int = 0,
    utterance_id: int = 2,
) -> _AsrWorkerEvent:
    await requests.put(
        _AsrWorkerRequest(
            kind="shutdown",
            generation=generation,
            buffer_epoch=buffer_epoch,
            utterance_id=utterance_id,
        )
    )
    closed = await _next_event(responses, "closed")
    await asyncio.wait_for(task, 1)
    await asyncio.wait_for(requests.join(), 1)
    return closed


async def test_glm_commit_posts_pcm16_wav_and_emits_exactly_one_final() -> None:
    client = _FakeClient(_FakeResponse({"text": "\u4f60\u597d\u4e16\u754c"}))
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        glm.glm_asr_worker(
            requests,
            responses,
            "glm-secret",
            AsrSessionConfig(language="zh-CN"),
            http_client=client,
        )
    )

    assert (await _next_event(responses, "ready")).generation == 0
    pcm = b"\x01\x02" * 320
    key = {"generation": 0, "buffer_epoch": 0, "utterance_id": 1}
    await requests.put(_AsrWorkerRequest(kind="audio", audio=pcm, **key))
    await requests.put(_AsrWorkerRequest(kind="commit", **key))

    final = await _next_event(responses, "final")
    assert (final.text, final.generation, final.buffer_epoch, final.utterance_id) == (
        "\u4f60\u597d\u4e16\u754c",
        0,
        0,
        1,
    )
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions"
    assert call["headers"] == {"Authorization": "Bearer glm-secret"}
    assert call["data"] == {"model": "glm-asr-2512"}
    filename, wav_bytes, content_type = call["files"]["file"]
    assert (filename, content_type) == ("audio.wav", "audio/wav")
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16_000
        assert wav_file.readframes(wav_file.getnframes()) == pcm

    # A duplicate commit has no remaining audio and must not create a second
    # provider call or a duplicate final.
    await requests.put(_AsrWorkerRequest(kind="commit", **key))
    await asyncio.sleep(0)
    assert len(client.calls) == 1
    await _stop_worker(task, requests, responses)


async def test_glm_clear_cancels_inflight_old_epoch_and_new_epoch_isolated() -> None:
    blocking = _BlockingClient()
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        glm.glm_asr_worker(
            requests,
            responses,
            "key",
            AsrSessionConfig(),
            http_client=blocking,
        )
    )
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=0,
            utterance_id=1,
            audio=b"\0\0",
        )
    )
    await requests.put(
        _AsrWorkerRequest(kind="commit", generation=0, buffer_epoch=0, utterance_id=1)
    )
    await asyncio.wait_for(blocking.started.wait(), 1)

    await requests.put(
        _AsrWorkerRequest(kind="clear", generation=0, buffer_epoch=1, utterance_id=2)
    )
    await asyncio.wait_for(blocking.cancelled.wait(), 1)

    blocking.next_response = _FakeResponse({"text": "fresh"})
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=1,
            utterance_id=2,
            audio=b"\x03\x04",
        )
    )
    await requests.put(
        _AsrWorkerRequest(kind="commit", generation=0, buffer_epoch=1, utterance_id=2)
    )
    final = await _next_event(responses, "final")
    assert (final.text, final.buffer_epoch, final.utterance_id) == ("fresh", 1, 2)
    await _stop_worker(
        task,
        requests,
        responses,
        buffer_epoch=1,
        utterance_id=3,
    )


async def test_glm_ignores_audio_from_an_old_generation_or_epoch() -> None:
    client = _FakeClient(_FakeResponse({"text": "current"}))
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        glm.glm_asr_worker(
            requests,
            responses,
            "key",
            AsrSessionConfig(),
            http_client=client,
        )
    )
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(kind="clear", generation=1, buffer_epoch=2, utterance_id=3)
    )
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=1,
            utterance_id=1,
            audio=b"old",
        )
    )
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=1,
            buffer_epoch=2,
            utterance_id=3,
            audio=b"\x05\x06",
        )
    )
    await requests.put(
        _AsrWorkerRequest(kind="commit", generation=1, buffer_epoch=2, utterance_id=3)
    )

    final = await _next_event(responses, "final")
    assert (final.generation, final.buffer_epoch, final.utterance_id) == (1, 2, 3)
    _, wav_bytes, _ = client.calls[0]["files"]["file"]
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        assert wav_file.readframes(wav_file.getnframes()) == b"\x05\x06"
    await _stop_worker(
        task,
        requests,
        responses,
        generation=2,
        buffer_epoch=2,
        utterance_id=4,
    )


async def test_glm_rejects_audio_longer_than_28_seconds_before_network() -> None:
    client = _FakeClient()
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        glm.glm_asr_worker(
            requests,
            responses,
            "key",
            AsrSessionConfig(),
            http_client=client,
        )
    )
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            utterance_id=1,
            audio=b"\0\0" * (16_000 * 28 + 1),
        )
    )

    error = await _next_event(responses, "error")
    assert error.error_code == "ASR_GLM_AUDIO_TOO_LONG"
    assert client.calls == []
    assert (await _next_event(responses, "closed")).kind == "closed"
    await asyncio.wait_for(task, 1)


async def test_glm_shutdown_cancels_inflight_request_without_final() -> None:
    client = _BlockingClient()
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        glm.glm_asr_worker(
            requests,
            responses,
            "key",
            AsrSessionConfig(),
            http_client=client,
        )
    )
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(kind="audio", generation=0, utterance_id=1, audio=b"\0\0")
    )
    await requests.put(_AsrWorkerRequest(kind="commit", generation=0, utterance_id=1))
    await asyncio.wait_for(client.started.wait(), 1)

    await requests.put(_AsrWorkerRequest(kind="shutdown", generation=1, utterance_id=2))
    assert (await _next_event(responses, "closed")).generation == 1
    await asyncio.wait_for(client.cancelled.wait(), 1)
    await asyncio.wait_for(task, 1)


@pytest.mark.parametrize(
    ("api_key", "config", "expected_code"),
    [
        ("", AsrSessionConfig(), "ASR_CREDENTIALS_MISSING"),
        (
            "key",
            AsrSessionConfig(endpointing_mode="provider"),
            "ASR_ENDPOINTING_NOT_SUPPORTED",
        ),
    ],
)
async def test_glm_rejects_invalid_startup_before_ready(
    api_key: str,
    config: AsrSessionConfig,
    expected_code: str,
) -> None:
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()

    await glm.glm_asr_worker(
        requests,
        responses,
        api_key,
        config,
        http_client=_FakeClient(),
    )

    assert (await _next_event(responses, "error")).error_code == expected_code
    assert (await _next_event(responses, "closed")).kind == "closed"


async def test_glm_maps_http_auth_rejection_without_exposing_provider_body() -> None:
    client = _FakeClient(_FakeResponse({"error": "secret details"}, status_code=401))
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        glm.glm_asr_worker(
            requests,
            responses,
            "secret-key",
            AsrSessionConfig(),
            http_client=client,
        )
    )
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(kind="audio", generation=0, utterance_id=1, audio=b"\0\0")
    )
    await requests.put(_AsrWorkerRequest(kind="commit", generation=0, utterance_id=1))

    error = await _next_event(responses, "error")
    assert (error.error_code, error.error_message) == (
        "ASR_CREDENTIALS_REJECTED",
        "GLM credentials were rejected",
    )
    assert (await _next_event(responses, "closed")).kind == "closed"
    await asyncio.wait_for(task, 1)
