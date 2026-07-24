from __future__ import annotations

import asyncio
import io
import wave
from types import SimpleNamespace
from typing import Any

import pytest

from main_logic.asr_client._infra import (
    AsrSessionConfig,
    _AsrWorkerEvent,
    _AsrWorkerRequest,
)
from main_logic.asr_client.workers import gemini
from main_logic.asr_client.workers.gemini import gemini_asr_worker


class _FakeModels:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if callable(response):
            return await response()
        if isinstance(response, BaseException):
            raise response
        return response


class _FakeClient:
    def __init__(self, *responses: Any) -> None:
        self.models = _FakeModels(list(responses))
        self.aio = SimpleNamespace(models=self.models)


def test_transcription_prompt_is_inline_prompt_hygiene_compatible() -> None:
    prompt = gemini._GEMINI_TRANSCRIPTION_PROMPT
    assert not any("\u4e00" <= char <= "\u9fff" for char in prompt)
    assert "[inaudible]" in prompt


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


async def _start_worker(
    client: _FakeClient,
    *,
    api_key: str = "gemini-key",
    config: AsrSessionConfig | None = None,
) -> tuple[
    asyncio.Task[None],
    asyncio.Queue[_AsrWorkerRequest],
    asyncio.Queue[_AsrWorkerEvent],
]:
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        gemini_asr_worker(
            requests,
            responses,
            api_key,
            config or AsrSessionConfig(language="zh-CN"),
            client=client,
        )
    )
    return task, requests, responses


async def _shutdown(
    task: asyncio.Task[None],
    requests: asyncio.Queue[_AsrWorkerRequest],
    responses: asyncio.Queue[_AsrWorkerEvent],
    *,
    generation: int = 1,
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


async def test_commit_sends_wav_structured_request_and_one_final() -> None:
    client = _FakeClient(SimpleNamespace(parsed={"transcript": "你好，世界。"}))
    task, requests, responses = await _start_worker(client)

    assert (await _next_event(responses, "ready")).generation == 0
    first = b"\x01\x00" * 160
    second = b"\x02\x00" * 320
    for pcm in (first, second):
        await requests.put(
            _AsrWorkerRequest(
                kind="audio",
                generation=0,
                buffer_epoch=0,
                utterance_id=1,
                audio=pcm,
            )
        )
    commit = _AsrWorkerRequest(
        kind="commit",
        generation=0,
        buffer_epoch=0,
        utterance_id=1,
    )
    await requests.put(commit)

    final = await _next_event(responses, "final")
    assert (
        final.text,
        final.generation,
        final.buffer_epoch,
        final.utterance_id,
    ) == ("你好，世界。", 0, 0, 1)
    assert len(client.models.calls) == 1

    call = client.models.calls[0]
    assert call["model"] == "gemini-3.1-flash-lite"
    assert "tools" not in call["config"]
    assert "system_instruction" not in call["config"]
    assert call["config"]["response_mime_type"] == "application/json"
    assert call["config"]["response_json_schema"]["required"] == ["transcript"]
    content = call["contents"][0]
    assert content["role"] == "user"
    assert "do not answer questions" in content["parts"][0]["text"]
    audio_part = content["parts"][1]["inline_data"]
    assert audio_part["mime_type"] == "audio/wav"
    with wave.open(io.BytesIO(audio_part["data"]), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16_000
        assert wav_file.readframes(wav_file.getnframes()) == first + second

    await requests.put(commit)
    await asyncio.wait_for(requests.join(), 1)
    assert len(client.models.calls) == 1
    with pytest.raises(asyncio.TimeoutError):
        await _next_event(responses, "final", timeout=0.02)

    closed = await _shutdown(task, requests, responses)
    assert closed.generation == 1


async def test_clear_cancels_inflight_and_stale_epoch_cannot_emit() -> None:
    first_started = asyncio.Event()
    first_cancelled = asyncio.Event()

    async def blocked_response() -> Any:
        first_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            first_cancelled.set()
            raise

    client = _FakeClient(
        blocked_response,
        SimpleNamespace(text='{"transcript":"新句子"}'),
    )
    task, requests, responses = await _start_worker(client)
    await _next_event(responses, "ready")

    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=0,
            utterance_id=1,
            audio=b"\x01\x00" * 160,
        )
    )
    await requests.put(
        _AsrWorkerRequest(kind="commit", generation=0, buffer_epoch=0, utterance_id=1)
    )
    await asyncio.wait_for(first_started.wait(), 1)
    await requests.put(
        _AsrWorkerRequest(kind="clear", generation=0, buffer_epoch=1, utterance_id=2)
    )
    await asyncio.wait_for(first_cancelled.wait(), 1)

    # A delayed producer must not revive an utterance invalidated by clear.
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=0,
            utterance_id=1,
            audio=b"\x09\x00" * 80,
        )
    )
    await requests.put(
        _AsrWorkerRequest(kind="commit", generation=0, buffer_epoch=0, utterance_id=1)
    )
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=1,
            utterance_id=2,
            audio=b"\x02\x00" * 160,
        )
    )
    await requests.put(
        _AsrWorkerRequest(kind="commit", generation=0, buffer_epoch=1, utterance_id=2)
    )

    final = await _next_event(responses, "final")
    assert (final.text, final.buffer_epoch, final.utterance_id) == ("新句子", 1, 2)
    assert len(client.models.calls) == 2
    await _shutdown(
        task,
        requests,
        responses,
        buffer_epoch=1,
        utterance_id=3,
    )


async def test_shutdown_cancels_inflight_without_late_final() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked_response() -> Any:
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    client = _FakeClient(blocked_response)
    task, requests, responses = await _start_worker(client)
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=0,
            utterance_id=1,
            audio=b"\x01\x00" * 160,
        )
    )
    await requests.put(
        _AsrWorkerRequest(kind="commit", generation=0, buffer_epoch=0, utterance_id=1)
    )
    await asyncio.wait_for(started.wait(), 1)

    closed = await _shutdown(task, requests, responses)
    assert closed.generation == 1
    assert cancelled.is_set()
    with pytest.raises(asyncio.TimeoutError):
        await _next_event(responses, "final", timeout=0.02)


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(parsed={"answer": "这不是转写"}),
        SimpleNamespace(text="not-json"),
        SimpleNamespace(parsed={"transcript": "   "}),
    ],
)
async def test_invalid_structured_response_emits_scoped_error(response: Any) -> None:
    client = _FakeClient(response)
    task, requests, responses = await _start_worker(client)
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=0,
            utterance_id=7,
            audio=b"\x01\x00" * 160,
        )
    )
    await requests.put(
        _AsrWorkerRequest(kind="commit", generation=0, buffer_epoch=0, utterance_id=7)
    )

    error = await _next_event(responses, "error")
    assert error.error_code == "ASR_GEMINI_INVALID_RESPONSE"
    assert (error.generation, error.buffer_epoch, error.utterance_id) == (0, 0, 7)
    await _shutdown(task, requests, responses)


class _ProviderFailure(RuntimeError):
    def __init__(self, status_code: int | None = None) -> None:
        super().__init__("provider failure with sensitive details")
        self.status_code = status_code


class _GoogleProviderFailure(RuntimeError):
    def __init__(
        self,
        *,
        code: int | None,
        status: str | None,
        details: object,
    ) -> None:
        super().__init__("google provider failure with sensitive details")
        self.code = code
        self.status = status
        self.details = details


def _google_error_info(
    reason: str,
    *,
    domain: str = "googleapis.com",
    wrapped: bool = True,
) -> dict[str, object]:
    error = {
        "code": 400,
        "status": "INVALID_ARGUMENT",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": reason,
                "domain": domain,
            }
        ],
    }
    return {"error": error} if wrapped else error


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (_ProviderFailure(), "ASR_GEMINI_REQUEST_FAILED"),
        (_ProviderFailure(401), "ASR_CREDENTIALS_REJECTED"),
    ],
)
async def test_provider_failure_is_sanitized_and_scoped(
    failure: BaseException,
    expected_code: str,
) -> None:
    client = _FakeClient(failure)
    task, requests, responses = await _start_worker(client)
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=0,
            utterance_id=4,
            audio=b"\x01\x00" * 160,
        )
    )
    await requests.put(
        _AsrWorkerRequest(kind="commit", generation=0, buffer_epoch=0, utterance_id=4)
    )

    error = await _next_event(responses, "error")
    assert error.error_code == expected_code
    assert "sensitive" not in error.error_message
    assert (error.generation, error.buffer_epoch, error.utterance_id) == (0, 0, 4)
    await _shutdown(task, requests, responses)


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        pytest.param(
            _GoogleProviderFailure(
                code=400,
                status="INVALID_ARGUMENT",
                details=_google_error_info("API_KEY_INVALID"),
            ),
            "ASR_CREDENTIALS_REJECTED",
            id="api_key_invalid",
        ),
        pytest.param(
            _GoogleProviderFailure(
                code=400,
                status="INVALID_ARGUMENT",
                details=_google_error_info(
                    "API_KEY_SERVICE_BLOCKED",
                    wrapped=False,
                ),
            ),
            "ASR_CREDENTIALS_REJECTED",
            id="api_key_service_blocked_unwrapped",
        ),
        pytest.param(
            _GoogleProviderFailure(
                code=403,
                status="PERMISSION_DENIED",
                details={},
            ),
            "ASR_CREDENTIALS_REJECTED",
            id="permission_denied_code",
        ),
        pytest.param(
            _GoogleProviderFailure(
                code=400,
                status="INVALID_ARGUMENT",
                details=_google_error_info(
                    "API_KEY_INVALID",
                    domain="example.invalid",
                ),
            ),
            "ASR_GEMINI_REQUEST_FAILED",
            id="api_key_invalid_wrong_domain",
        ),
        pytest.param(
            _GoogleProviderFailure(
                code=400,
                status="INVALID_ARGUMENT",
                details={
                    "error": {
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.rpc.BadRequest",
                                "fieldViolations": [],
                            }
                        ]
                    }
                },
            ),
            "ASR_GEMINI_REQUEST_FAILED",
            id="bad_request_error_info_type",
        ),
        pytest.param(
            _GoogleProviderFailure(
                code=400,
                status="INVALID_ARGUMENT",
                details={"error": {"details": "malformed"}},
            ),
            "ASR_GEMINI_REQUEST_FAILED",
            id="malformed_details",
        ),
        pytest.param(
            _GoogleProviderFailure(
                code=429,
                status="RESOURCE_EXHAUSTED",
                details={},
            ),
            "ASR_GEMINI_REQUEST_FAILED",
            id="resource_exhausted",
        ),
        pytest.param(
            _GoogleProviderFailure(code=None, status=None, details={}),
            "ASR_GEMINI_REQUEST_FAILED",
            id="missing_status",
        ),
    ],
)
async def test_google_provider_failure_classification_is_structural_and_sanitized(
    failure: BaseException,
    expected_code: str,
) -> None:
    client = _FakeClient(failure)
    task, requests, responses = await _start_worker(client)
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=0,
            utterance_id=5,
            audio=b"\x01\x00" * 160,
        )
    )
    await requests.put(
        _AsrWorkerRequest(kind="commit", generation=0, buffer_epoch=0, utterance_id=5)
    )

    error = await _next_event(responses, "error")
    assert error.error_code == expected_code
    assert "sensitive" not in error.error_message
    assert "API_KEY" not in error.error_message
    assert (error.generation, error.buffer_epoch, error.utterance_id) == (0, 0, 5)
    await _shutdown(task, requests, responses)


async def test_rejects_missing_credentials_and_provider_endpointing() -> None:
    missing_task, _, missing_responses = await _start_worker(_FakeClient(), api_key="")
    missing = await _next_event(missing_responses, "error")
    assert missing.error_code == "ASR_CREDENTIALS_MISSING"
    assert (await _next_event(missing_responses, "closed")).kind == "closed"
    await asyncio.wait_for(missing_task, 1)

    provider_task, _, provider_responses = await _start_worker(
        _FakeClient(),
        config=AsrSessionConfig(endpointing_mode="provider"),
    )
    invalid = await _next_event(provider_responses, "error")
    assert invalid.error_code == "ASR_INVALID_CONFIG"
    assert (await _next_event(provider_responses, "closed")).kind == "closed"
    await asyncio.wait_for(provider_task, 1)


async def test_rejects_utterance_longer_than_28_seconds() -> None:
    client = _FakeClient()
    task, requests, responses = await _start_worker(client)
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=0,
            utterance_id=1,
            audio=b"\x00\x00" * (16_000 * 28 + 1),
        )
    )

    error = await _next_event(responses, "error")
    assert error.error_code == "ASR_GEMINI_AUDIO_TOO_LONG"
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=0,
            utterance_id=1,
            audio=b"\x01\x00" * 160,
        )
    )
    await requests.put(
        _AsrWorkerRequest(
            kind="commit",
            generation=0,
            buffer_epoch=0,
            utterance_id=1,
        )
    )
    await asyncio.wait_for(requests.join(), 1)
    assert client.models.calls == []
    await _shutdown(task, requests, responses)
