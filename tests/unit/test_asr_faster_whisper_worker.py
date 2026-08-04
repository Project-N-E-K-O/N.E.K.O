from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from main_logic.asr_client._infra import (
    AsrSessionConfig,
    _AsrWorkerEvent,
    _AsrWorkerRequest,
)
from main_logic.asr_client.workers import faster_whisper


class _FakeModel:
    def __init__(self, text: str = "你好世界") -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, audio: Any, **kwargs: Any) -> tuple[list[Any], Any]:
        self.calls.append({"audio_len": len(audio), **kwargs})
        return [SimpleNamespace(text=self.text)], SimpleNamespace()


async def _next_event(
    queue: asyncio.Queue[_AsrWorkerEvent],
    kind: str | None = None,
    *,
    timeout: float = 2.0,
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
) -> None:
    await requests.put(
        _AsrWorkerRequest(
            kind="shutdown",
            generation=0,
            buffer_epoch=0,
            utterance_id=2,
        )
    )
    await _next_event(responses, "closed")
    await asyncio.wait_for(task, 2)
    await asyncio.wait_for(requests.join(), 2)


async def test_faster_whisper_commit_emits_one_final() -> None:
    model = _FakeModel("本地识别")
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        faster_whisper.faster_whisper_asr_worker(
            requests,
            responses,
            "",
            AsrSessionConfig(language="zh-CN"),
            model_factory=lambda: model,
        )
    )

    assert (await _next_event(responses, "ready")).generation == 0
    # >=350ms of non-silent PCM so speech gates do not drop the utterance.
    pcm = b"\x00\x40" * 8000
    key = {"generation": 0, "buffer_epoch": 0, "utterance_id": 1}
    await requests.put(_AsrWorkerRequest(kind="audio", audio=pcm, **key))
    await requests.put(_AsrWorkerRequest(kind="commit", **key))

    final = await _next_event(responses, "final")
    assert (final.text, final.generation, final.buffer_epoch, final.utterance_id) == (
        "本地识别",
        0,
        0,
        1,
    )
    assert len(model.calls) == 1
    assert model.calls[0]["language"] == "zh"
    assert model.calls[0]["vad_filter"] is False
    assert model.calls[0]["beam_size"] == 5
    assert model.calls[0]["without_timestamps"] is True

    await requests.put(_AsrWorkerRequest(kind="commit", **key))
    await asyncio.wait_for(requests.join(), 1)
    await asyncio.sleep(0)
    assert len(model.calls) == 1
    await _stop_worker(task, requests, responses)


async def test_faster_whisper_missing_dependency_errors_on_commit() -> None:
    def _missing() -> Any:
        raise RuntimeError("ASR_LOCAL_DEPENDENCY_MISSING: pip install faster-whisper")

    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        faster_whisper.faster_whisper_asr_worker(
            requests,
            responses,
            "",
            AsrSessionConfig(),
            model_factory=_missing,
        )
    )

    assert (await _next_event(responses, "ready")).kind == "ready"
    key = {"generation": 0, "buffer_epoch": 0, "utterance_id": 1}
    await requests.put(_AsrWorkerRequest(kind="audio", audio=b"\x01\x02" * 320, **key))
    await requests.put(_AsrWorkerRequest(kind="commit", **key))
    error = await _next_event(responses, "error")
    assert error.error_code == "ASR_LOCAL_DEPENDENCY_MISSING"
    closed = await _next_event(responses, "closed")
    assert closed.kind == "closed"
    await asyncio.wait_for(task, 2)


def test_run_transcribe_skips_short_or_noise_only_audio() -> None:
    class _Model:
        def __init__(self) -> None:
            self.calls = 0

        def transcribe(self, audio: Any, **kwargs: Any):
            self.calls += 1

            def _segments():
                if False:
                    yield None

            return _segments(), SimpleNamespace()

    model = _Model()
    assert faster_whisper._run_transcribe(model, b"\x00\x40" * 100, "zh") == ""
    assert model.calls == 0
    assert faster_whisper._is_noise_only_transcript("谢谢观看")
    assert not faster_whisper._is_noise_only_transcript("今天天气不错")


def test_default_model_factory_falls_back_to_cpu_when_cuda_probe_fails(monkeypatch) -> None:
    faster_whisper._MODEL_CACHE.clear()
    calls: list[tuple[str, str]] = []

    class _ProbeModel:
        def __init__(self, device: str, compute_type: str) -> None:
            self.device = device
            self.compute_type = compute_type

        def transcribe(self, audio: Any, **kwargs: Any) -> tuple[list[Any], Any]:
            if self.device == "cuda":
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")

            def _segments():
                if False:
                    yield None

            return _segments(), SimpleNamespace(language="zh")

    def _fake_whisper_model(model_size: str, device: str = "cpu", compute_type: str = "int8"):
        calls.append((device, compute_type))
        return _ProbeModel(device, compute_type)

    monkeypatch.setenv("NEKO_WHISPER_MODEL", "small")
    monkeypatch.setenv("NEKO_WHISPER_DEVICE", "auto")
    monkeypatch.setattr(
        faster_whisper,
        "_resolve_device",
        lambda _requested: ("cuda", "float16"),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=_fake_whisper_model),
    )

    model = faster_whisper._default_model_factory()
    assert isinstance(model, _ProbeModel)
    assert model.device == "cpu"
    assert calls == [
        ("cuda", "float16"),
        ("cuda", "int8_float16"),
        ("cpu", "int8"),
    ]
    faster_whisper._MODEL_CACHE.clear()


async def test_faster_whisper_rejects_provider_endpointing() -> None:
    model = _FakeModel()
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        faster_whisper.faster_whisper_asr_worker(
            requests,
            responses,
            "",
            AsrSessionConfig(endpointing_mode="provider"),
            model_factory=lambda: model,
        )
    )
    error = await _next_event(responses, "error")
    assert error.error_code == "ASR_ENDPOINTING_NOT_SUPPORTED"
    await _next_event(responses, "closed")
    await asyncio.wait_for(task, 2)
