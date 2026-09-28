"""Contract tests for the local faster-whisper ASR worker and its selection.

No real model is downloaded: every test injects a fake loader, a fake
``WhisperModel`` class, or a fake ``faster_whisper`` module.
"""

from __future__ import annotations

import ast
import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import main_logic.asr_client as asr_client
from main_logic.asr_client._infra import (
    AsrSessionConfig,
    _AsrWorkerEvent,
    _AsrWorkerRequest,
)
from main_logic.asr_client._registry_meta import (
    ASR_PROVIDER_REGISTRY,
    AsrProviderAvailability,
)
from main_logic.asr_client.delivery import delivery_evidence
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.asr_client.workers import faster_whisper
from utils import preferences
from utils.conversation_settings_constants import (
    INDEPENDENT_ASR_PROVIDER_PREFERENCES,
)


ROOT = Path(__file__).resolve().parents[2]
PCM = b"\x00\x10" * 4_000  # 0.25 s of 16 kHz PCM16


def _segment(text: str, *, no_speech_prob: float = 0.01, avg_logprob: float = -0.2):
    return SimpleNamespace(
        text=text,
        no_speech_prob=no_speech_prob,
        avg_logprob=avg_logprob,
    )


class _FakeModel:
    def __init__(self, *segments: Any, error: Exception | None = None) -> None:
        self.segments = list(segments) or [_segment("你好")]
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.thread_ids: list[int] = []
        self.release = threading.Event()
        self.release.set()

    def transcribe(self, audio: Any, **kwargs: Any):
        self.thread_ids.append(threading.get_ident())
        self.calls.append({"audio_len": len(audio), **kwargs})
        if not self.release.wait(5):
            raise TimeoutError("test model was never released")
        if self.error is not None:
            raise self.error
        return iter(self.segments), SimpleNamespace()


class _RecordingLoader:
    def __init__(self, model: Any) -> None:
        self.model = model
        self.calls = 0
        self.thread_ids: list[int] = []

    def __call__(self, spec: faster_whisper._ModelSpec) -> Any:
        self.calls += 1
        self.thread_ids.append(threading.get_ident())
        return self.model


@pytest.fixture
def pool() -> faster_whisper._WhisperModelPool:
    return faster_whisper._WhisperModelPool(idle_release_seconds=60.0)


@pytest.fixture(autouse=True)
def _clear_model_env(monkeypatch) -> None:
    for name in ("NEKO_WHISPER_MODEL", "NEKO_WHISPER_DEVICE", "NEKO_WHISPER_COMPUTE"):
        monkeypatch.delenv(name, raising=False)


async def _next_event(
    queue: asyncio.Queue[_AsrWorkerEvent],
    kind: str | None = None,
    *,
    timeout: float = 3.0,
) -> _AsrWorkerEvent:
    while True:
        event = await asyncio.wait_for(queue.get(), timeout)
        if kind is None or event.kind == kind:
            return event


def _start_worker(
    config: AsrSessionConfig,
    loader: Any,
    pool: faster_whisper._WhisperModelPool,
) -> tuple[
    asyncio.Task[None],
    asyncio.Queue[_AsrWorkerRequest],
    asyncio.Queue[_AsrWorkerEvent],
]:
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        faster_whisper.faster_whisper_asr_worker(
            requests,
            responses,
            "",
            config,
            model_loader=loader,
            model_pool=pool,
        )
    )
    return task, requests, responses


async def _send_utterance(
    requests: asyncio.Queue[_AsrWorkerRequest],
    *,
    generation: int = 0,
    buffer_epoch: int = 0,
    utterance_id: int = 1,
    audio: bytes = PCM,
) -> None:
    key = {
        "generation": generation,
        "buffer_epoch": buffer_epoch,
        "utterance_id": utterance_id,
    }
    await requests.put(_AsrWorkerRequest(kind="audio", audio=audio, **key))
    await requests.put(_AsrWorkerRequest(kind="commit", **key))


async def _shutdown(
    task: asyncio.Task[None],
    requests: asyncio.Queue[_AsrWorkerRequest],
    responses: asyncio.Queue[_AsrWorkerEvent],
    *,
    generation: int = 0,
    buffer_epoch: int = 0,
) -> None:
    await requests.put(
        _AsrWorkerRequest(
            kind="shutdown",
            generation=generation,
            buffer_epoch=buffer_epoch,
        )
    )
    await _next_event(responses, "closed")
    await asyncio.wait_for(task, 3)


# ---------------------------------------------------------------------------
# Worker state machine
# ---------------------------------------------------------------------------


async def test_commit_emits_one_final_and_duplicate_commit_is_ignored(pool) -> None:
    model = _FakeModel(_segment("本地识别"))
    loader = _RecordingLoader(model)
    task, requests, responses = _start_worker(
        AsrSessionConfig(language="zh-CN"), loader, pool
    )

    assert (await _next_event(responses)).kind == "ready"
    await _send_utterance(requests)
    final = await _next_event(responses, "final")
    assert (final.text, final.generation, final.buffer_epoch, final.utterance_id) == (
        "本地识别",
        0,
        0,
        1,
    )
    call = model.calls[0]
    assert call["audio_len"] == len(PCM) // 2
    assert call["language"] == "zh"
    assert call["vad_filter"] is False
    assert call["condition_on_previous_text"] is False
    # No language-specific priming text is ever injected.
    assert "initial_prompt" not in call

    await requests.put(
        _AsrWorkerRequest(kind="commit", generation=0, buffer_epoch=0, utterance_id=1)
    )
    await asyncio.wait_for(requests.join(), 2)
    await asyncio.sleep(0.05)
    assert len(model.calls) == 1
    await _shutdown(task, requests, responses)


@pytest.mark.parametrize(
    ("session_language", "expected"),
    [("zh-TW", "zh"), ("ja", "ja"), ("en-US", "en"), ("auto", None)],
)
async def test_language_follows_session_config(pool, session_language, expected) -> None:
    model = _FakeModel(_segment("x"))
    task, requests, responses = _start_worker(
        AsrSessionConfig(language=session_language), _RecordingLoader(model), pool
    )
    await _next_event(responses, "ready")
    await _send_utterance(requests)
    await _next_event(responses, "final")
    assert model.calls[0]["language"] == expected
    await _shutdown(task, requests, responses)


async def test_single_character_result_is_not_dropped(pool) -> None:
    model = _FakeModel(_segment("好"))
    task, requests, responses = _start_worker(
        AsrSessionConfig(language="zh-CN"), _RecordingLoader(model), pool
    )
    await _next_event(responses, "ready")
    await _send_utterance(requests)
    assert (await _next_event(responses, "final")).text == "好"
    await _shutdown(task, requests, responses)


async def test_new_buffer_epoch_drops_inflight_final_of_old_epoch(pool) -> None:
    model = _FakeModel(_segment("旧"))
    model.release.clear()
    task, requests, responses = _start_worker(
        AsrSessionConfig(language="zh-CN"), _RecordingLoader(model), pool
    )
    await _next_event(responses, "ready")
    await _send_utterance(requests, buffer_epoch=0, utterance_id=1)
    for _ in range(100):
        if model.calls:
            break
        await asyncio.sleep(0.01)
    assert model.calls, "old utterance never reached the decoder"

    # A newer epoch clears the old scope while its decode is still running.
    await _send_utterance(requests, buffer_epoch=1, utterance_id=2)
    model.segments = [_segment("新")]
    model.release.set()

    final = await _next_event(responses, "final")
    assert (final.buffer_epoch, final.utterance_id, final.text) == (1, 2, "新")
    await asyncio.sleep(0.05)
    assert responses.empty()
    await _shutdown(task, requests, responses, buffer_epoch=1)


async def test_decoding_is_serialized_per_session(pool) -> None:
    model = _FakeModel(_segment("x"))
    model.release.clear()
    task, requests, responses = _start_worker(
        AsrSessionConfig(language="zh-CN"), _RecordingLoader(model), pool
    )
    await _next_event(responses, "ready")
    await _send_utterance(requests, utterance_id=1)
    await _send_utterance(requests, utterance_id=2)
    for _ in range(100):
        if model.calls:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.1)
    # The second utterance waits for the first decoder thread to finish.
    assert len(model.calls) == 1

    model.release.set()
    finals = [await _next_event(responses, "final") for _ in range(2)]
    assert sorted(event.utterance_id for event in finals) == [1, 2]
    await _shutdown(task, requests, responses)


async def test_cancelled_decode_keeps_the_next_one_waiting_for_its_thread(pool) -> None:
    # A new buffer epoch cancels the old task, but its decoder thread keeps
    # running; the next utterance must not decode beside it.
    model = _FakeModel(_segment("x"))
    model.release.clear()
    task, requests, responses = _start_worker(
        AsrSessionConfig(language="zh-CN"), _RecordingLoader(model), pool
    )
    await _next_event(responses, "ready")
    await _send_utterance(requests, buffer_epoch=0, utterance_id=1)
    for _ in range(100):
        if model.calls:
            break
        await asyncio.sleep(0.01)
    await _send_utterance(requests, buffer_epoch=1, utterance_id=2)
    await asyncio.wait_for(requests.join(), 2)
    await asyncio.sleep(0.1)
    assert len(model.calls) == 1

    model.release.set()
    final = await _next_event(responses, "final")
    assert (final.buffer_epoch, final.utterance_id) == (1, 2)
    assert len(model.calls) == 2
    await _shutdown(task, requests, responses, buffer_epoch=1)


def test_backlog_counts_only_decodes_still_in_flight() -> None:
    loop = asyncio.new_event_loop()
    try:
        finished = loop.create_future()
        finished.set_result(None)
        waiting = loop.create_future()
        pending = {finished: "a", waiting: "b"}
        assert faster_whisper._decodes_in_flight(pending) == 1
        waiting.cancel()
    finally:
        loop.close()


async def test_decode_backlog_is_bounded(pool) -> None:
    model = _FakeModel(_segment("x"))
    model.release.clear()
    task, requests, responses = _start_worker(
        AsrSessionConfig(language="zh-CN"), _RecordingLoader(model), pool
    )
    await _next_event(responses, "ready")
    try:
        for utterance_id in range(1, faster_whisper._MAX_PENDING_DECODES + 2):
            await _send_utterance(requests, utterance_id=utterance_id)
        error = await _next_event(responses, "error")
        assert error.error_code == "ASR_LOCAL_DECODE_BACKLOG"
        assert error.utterance_id == faster_whisper._MAX_PENDING_DECODES + 1
    finally:
        model.release.set()
    await _next_event(responses, "closed")
    await asyncio.wait_for(task, 3)
    # Only the admitted utterances ever reached the decoder.
    assert len(model.calls) <= faster_whisper._MAX_PENDING_DECODES


async def test_rejects_provider_endpointing(pool) -> None:
    loader = _RecordingLoader(_FakeModel())
    task, requests, responses = _start_worker(
        AsrSessionConfig(endpointing_mode="provider"), loader, pool
    )
    error = await _next_event(responses, "error")
    assert error.error_code == "ASR_ENDPOINTING_NOT_SUPPORTED"
    await _next_event(responses, "closed")
    await asyncio.wait_for(task, 3)
    assert loader.calls == 0


# ---------------------------------------------------------------------------
# Transport evidence (#3078 contract)
# ---------------------------------------------------------------------------


async def test_transport_evidence_marks_handoff_and_written_audio(pool) -> None:
    model = _FakeModel(_segment("你好"))
    task, requests, responses = _start_worker(
        AsrSessionConfig(language="zh-CN"), _RecordingLoader(model), pool
    )
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(
            kind="audio", generation=0, buffer_epoch=0, utterance_id=1, audio=PCM
        )
    )
    await asyncio.wait_for(requests.join(), 2)
    # Buffered but not yet handed to the decoder: definite non-delivery.
    assert delivery_evidence(requests).attempted is False

    await requests.put(
        _AsrWorkerRequest(kind="commit", generation=0, buffer_epoch=0, utterance_id=1)
    )
    await _next_event(responses, "final")
    evidence = delivery_evidence(requests)
    assert evidence.attempted is True
    assert evidence.written_audio_bytes == len(PCM)
    await _shutdown(task, requests, responses)


async def test_decoder_failure_is_attempted_but_not_written(pool) -> None:
    model = _FakeModel(error=RuntimeError("decoder exploded"))
    task, requests, responses = _start_worker(
        AsrSessionConfig(language="zh-CN"), _RecordingLoader(model), pool
    )
    await _next_event(responses, "ready")
    await _send_utterance(requests)
    error = await _next_event(responses, "error")
    assert error.error_code == "ASR_LOCAL_TRANSCRIBE_FAILED"
    assert (error.generation, error.buffer_epoch, error.utterance_id) == (0, 0, 1)
    evidence = delivery_evidence(requests)
    assert evidence.attempted is True
    assert evidence.written_audio_bytes == 0
    await _next_event(responses, "closed")
    await asyncio.wait_for(task, 3)


# ---------------------------------------------------------------------------
# Threading: nothing heavy on the event loop
# ---------------------------------------------------------------------------


async def test_import_load_and_decode_run_off_the_event_loop(monkeypatch, pool) -> None:
    loop_thread = threading.get_ident()
    import_threads: list[int] = []
    model = _FakeModel(_segment("线程"))
    constructed: list[int] = []

    class _FakeWhisperModel:
        def __new__(cls, *_args: Any, **_kwargs: Any):
            constructed.append(threading.get_ident())
            return model

    def fake_import() -> Any:
        import_threads.append(threading.get_ident())
        return SimpleNamespace(WhisperModel=_FakeWhisperModel)

    monkeypatch.setattr(faster_whisper, "_import_faster_whisper", fake_import)
    monkeypatch.setenv("NEKO_WHISPER_DEVICE", "cpu")
    # Default loader (no model_loader injection): exercises the real import path.
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        faster_whisper.faster_whisper_asr_worker(
            requests,
            responses,
            "",
            AsrSessionConfig(language="zh-CN"),
            model_pool=pool,
        )
    )
    await _next_event(responses, "ready")
    await _send_utterance(requests)
    assert (await _next_event(responses, "final")).text == "线程"

    assert import_threads and loop_thread not in import_threads
    assert constructed and loop_thread not in constructed
    assert model.thread_ids and loop_thread not in model.thread_ids
    await _shutdown(task, requests, responses)


def test_worker_module_has_no_top_level_heavy_imports() -> None:
    source = (ROOT / "main_logic/asr_client/workers/faster_whisper.py").read_text(
        encoding="utf-8"
    )
    heavy = {"faster_whisper", "ctranslate2", "torch", "huggingface_hub"}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            names = {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names = {str(node.module or "").split(".")[0]}
        else:
            continue
        assert not names & heavy, names
    # No personal path hacks or hard-coded download mirrors.
    for forbidden in ("sys.path", "prepare_cuda_asr_path", "hf-mirror", "D:\\"):
        assert forbidden not in source


# ---------------------------------------------------------------------------
# Missing dependency
# ---------------------------------------------------------------------------


async def test_import_failure_reports_dependency_missing(monkeypatch, pool) -> None:
    # A None entry makes ``import faster_whisper`` raise ImportError.
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", None)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        faster_whisper.faster_whisper_asr_worker(
            requests,
            responses,
            "",
            AsrSessionConfig(),
            model_pool=pool,
        )
    )
    error = await _next_event(responses, "error")
    assert error.error_code == "ASR_LOCAL_DEPENDENCY_MISSING"
    await _next_event(responses, "closed")
    await asyncio.wait_for(task, 3)
    assert pool.loaded_count() == 0


def test_selection_reports_missing_dependency_without_importing(monkeypatch) -> None:
    probed: list[str] = []

    def fake_find_spec(name: str):
        probed.append(name)
        return None

    monkeypatch.delenv("ASR_PROVIDER", raising=False)
    monkeypatch.setattr(asr_client.importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(asr_client, "_load_core_config", lambda: {})

    selection = asr_client._resolve_asr_selection(
        "qwen", provider_preference="faster_whisper"
    )

    assert probed == ["faster_whisper"]
    assert selection.provider_key == "faster_whisper"
    assert selection.availability is AsrProviderAvailability.MISSING_DEPENDENCY
    with pytest.raises(RuntimeError, match="ASR_DEPENDENCY_MISSING"):
        asr_client._create_asr_session_from_selection(
            "qwen",
            selection=selection,
            on_input_transcript=AsyncMock(),
            on_connection_error=AsyncMock(),
        )


# ---------------------------------------------------------------------------
# Selection: explicit, credential-free, and gated by Core capability
# ---------------------------------------------------------------------------


def test_selection_honors_preference_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("ASR_PROVIDER", raising=False)
    monkeypatch.setattr(
        asr_client.importlib.util, "find_spec", lambda _name: object()
    )
    # Even with a Soniox key and an intl region, the explicit choice wins.
    monkeypatch.setattr(
        asr_client,
        "_load_core_config",
        lambda: {"SONIOX_API_KEY": "k", "ASR_USER_REGION": "intl"},
    )

    selection = asr_client._resolve_asr_selection(
        "qwen", provider_preference="faster_whisper"
    )

    assert selection.provider_key == "faster_whisper"
    assert selection.endpointing_mode == "manual"
    assert selection.availability is AsrProviderAvailability.IMPLEMENTED
    assert selection._api_key == ""
    session = asr_client._create_asr_session_from_selection(
        "qwen",
        selection=selection,
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
    )
    assert session is not None


@pytest.mark.parametrize("installed", [True, False])
def test_local_asr_availability_uses_the_selection_probe(monkeypatch, installed) -> None:
    probed: list[str] = []

    def fake_find_spec(name: str):
        probed.append(name)
        return object() if installed else None

    monkeypatch.setattr(asr_client.importlib.util, "find_spec", fake_find_spec)

    assert asr_client.is_local_asr_available() is installed
    assert probed == ["faster_whisper"]


def test_free_core_ignores_local_preference(monkeypatch) -> None:
    monkeypatch.delenv("ASR_PROVIDER", raising=False)
    monkeypatch.setattr(
        asr_client.importlib.util, "find_spec", lambda _name: object()
    )
    monkeypatch.setattr(asr_client, "_load_core_config", lambda: {})

    selection = asr_client._resolve_asr_selection(
        "free", provider_preference="faster_whisper"
    )

    assert selection.provider_key == "free"
    assert selection.availability is AsrProviderAvailability.BLOCKED_BACKEND


@pytest.mark.parametrize("preference", [None, "auto", "dummy", "qwen", "garbage"])
def test_non_selectable_preferences_follow_the_core_route(monkeypatch, preference) -> None:
    monkeypatch.delenv("ASR_PROVIDER", raising=False)
    monkeypatch.delenv("SONIOX_API_KEY", raising=False)
    monkeypatch.setattr(
        asr_client, "_load_core_config", lambda: {"ASSIST_API_KEY_GLM": "k"}
    )

    selection = asr_client._resolve_asr_selection(
        "glm", provider_preference=preference
    )

    assert selection.provider_key == "glm"


def test_registry_meta_and_policy_for_local_provider() -> None:
    meta = ASR_PROVIDER_REGISTRY["faster_whisper"]
    assert meta.category == "segmented_request"
    assert meta.supported_endpointing_modes == {"manual"}
    assert meta.requires_credential is False
    assert meta.user_selectable is True
    assert meta.optional_dependency == "faster_whisper"
    # Only the local provider is selectable by users; cloud providers stay on
    # Core routes and keep requiring credentials.
    assert {
        key for key, value in ASR_PROVIDER_REGISTRY.items() if value.user_selectable
    } == {"faster_whisper"}
    for key, value in ASR_PROVIDER_REGISTRY.items():
        if key not in {"faster_whisper"}:
            assert value.requires_credential is True, key

    policy = resolve_provider_policy("faster_whisper", "manual")
    assert policy.transport == "segmented"
    assert policy.smart_turn_required is True
    assert policy.provider_final_timeout_ms >= 60_000
    # Model preparation has its own budget; cloud providers never warm up.
    assert policy.provider_warmup_timeout_ms > policy.provider_final_timeout_ms
    for key, value in ASR_PROVIDER_REGISTRY.items():
        if key != "faster_whisper":
            assert value.provider_warmup_timeout_ms == 0, key


def test_persisted_preference_values_match_registry() -> None:
    selectable = {
        key for key, value in ASR_PROVIDER_REGISTRY.items() if value.user_selectable
    }
    assert INDEPENDENT_ASR_PROVIDER_PREFERENCES == {"auto"} | selectable


def test_preferences_validation_keeps_only_known_provider_preferences() -> None:
    validate = preferences._validate_conversation_settings
    assert validate({"independentAsrProviderPreference": "faster_whisper"}) == {
        "independentAsrProviderPreference": "faster_whisper"
    }
    assert validate({"independentAsrProviderPreference": "auto"}) == {
        "independentAsrProviderPreference": "auto"
    }
    for bad in ("qwen", "dummy", "", True, 1, None):
        assert validate({"independentAsrProviderPreference": bad}) == {}


@pytest.mark.parametrize(
    ("language", "expected"),
    [("auto", None), ("zh-CN", "zh"), ("zh-TW", "zh"), ("pt", "pt"), ("nb", "no")],
)
def test_whisper_language_mapping(language, expected) -> None:
    assert faster_whisper._whisper_language_code(language) == expected


def test_unsupported_language_falls_back_to_auto_detection() -> None:
    with pytest.raises(ValueError):
        faster_whisper._whisper_language_code("tlh")
    assert asr_client._resolve_session_language("faster_whisper", "tlh") == "auto"
    assert asr_client._resolve_session_language("faster_whisper", "ko") == "ko"
    assert asr_client._resolve_session_language("faster_whisper", None) == "auto"


# ---------------------------------------------------------------------------
# Hallucination filter
# ---------------------------------------------------------------------------


def test_exact_low_confidence_hallucination_is_dropped() -> None:
    model = _FakeModel(_segment(" Thank you.", no_speech_prob=0.55, avg_logprob=-0.4))
    assert faster_whisper._transcribe_pcm16(model, PCM, None) == ""

    model = _FakeModel(_segment("字幕由Amara.org社区提供", no_speech_prob=0.05, avg_logprob=-1.2))
    assert faster_whisper._transcribe_pcm16(model, PCM, "zh") == ""


def test_confident_or_partial_phrases_are_kept() -> None:
    # The user really said "thank you": high confidence, keep it.
    model = _FakeModel(_segment(" Thank you.", no_speech_prob=0.02, avg_logprob=-0.15))
    assert faster_whisper._transcribe_pcm16(model, PCM, "en") == "Thank you."

    # Low confidence but not an exact match: never filtered by substring.
    model = _FakeModel(
        _segment("谢谢观看今天的节目", no_speech_prob=0.9, avg_logprob=-1.5)
    )
    assert faster_whisper._transcribe_pcm16(model, PCM, "zh") == "谢谢观看今天的节目"


def test_segments_keep_word_spacing() -> None:
    model = _FakeModel(_segment(" Hello there."), _segment(" How are you?"))
    assert faster_whisper._transcribe_pcm16(model, PCM, "en") == "Hello there. How are you?"
    model = _FakeModel(_segment("今天"), _segment("天气不错"))
    assert faster_whisper._transcribe_pcm16(model, PCM, "zh") == "今天天气不错"


# ---------------------------------------------------------------------------
# CUDA fallback
# ---------------------------------------------------------------------------


def _install_fake_whisper(monkeypatch, *, cuda_ok: bool) -> list[tuple[str, str, str]]:
    constructed: list[tuple[str, str, str]] = []

    class _ProbeModel:
        def __init__(self, name: str, device: str, compute_type: str) -> None:
            self.name = name
            self.device = device
            self.compute_type = compute_type

        def transcribe(self, audio: Any, **kwargs: Any):
            if self.device == "cuda" and not cuda_ok:
                raise RuntimeError("Library cublas64_12.dll is not found")
            return iter(()), SimpleNamespace()

    def whisper_model(name: str, *, device: str, compute_type: str) -> _ProbeModel:
        constructed.append((name, device, compute_type))
        return _ProbeModel(name, device, compute_type)

    monkeypatch.setattr(
        faster_whisper,
        "_import_faster_whisper",
        lambda: SimpleNamespace(WhisperModel=whisper_model),
    )
    monkeypatch.setattr(faster_whisper, "_cuda_device_count", lambda: 1)
    return constructed


def test_cuda_probe_failure_falls_back_to_cpu(monkeypatch) -> None:
    constructed = _install_fake_whisper(monkeypatch, cuda_ok=False)

    model = faster_whisper._load_whisper_model(faster_whisper._model_spec_from_env())

    assert model.device == "cpu"
    assert constructed == [
        ("medium", "cuda", "float16"),
        ("medium", "cuda", "int8_float16"),
        ("base", "cpu", "int8"),
    ]


def test_working_cuda_is_kept(monkeypatch) -> None:
    constructed = _install_fake_whisper(monkeypatch, cuda_ok=True)
    model = faster_whisper._load_whisper_model(faster_whisper._model_spec_from_env())
    assert model.device == "cuda"
    assert constructed == [("medium", "cuda", "float16")]


def test_explicit_cpu_never_touches_cuda(monkeypatch) -> None:
    constructed = _install_fake_whisper(monkeypatch, cuda_ok=True)
    monkeypatch.setenv("NEKO_WHISPER_DEVICE", "cpu")
    monkeypatch.setenv("NEKO_WHISPER_MODEL", "small")
    model = faster_whisper._load_whisper_model(faster_whisper._model_spec_from_env())
    assert model.device == "cpu"
    assert constructed == [("small", "cpu", "int8")]


@pytest.mark.parametrize(
    ("compute_env", "expected_compute"),
    [(None, "int8"), ("int8_float32", "int8_float32"), ("float32", "float32")],
)
def test_auto_device_without_gpu_honors_explicit_compute(
    monkeypatch, compute_env, expected_compute
) -> None:
    constructed = _install_fake_whisper(monkeypatch, cuda_ok=True)
    monkeypatch.setattr(faster_whisper, "_cuda_device_count", lambda: 0)
    monkeypatch.setenv("NEKO_WHISPER_DEVICE", "auto")
    if compute_env is not None:
        monkeypatch.setenv("NEKO_WHISPER_COMPUTE", compute_env)

    model = faster_whisper._load_whisper_model(faster_whisper._model_spec_from_env())

    assert model.device == "cpu"
    assert constructed == [("base", "cpu", expected_compute)]


def test_all_candidates_failing_reports_model_load_failure(monkeypatch) -> None:
    def broken(*_args: Any, **_kwargs: Any):
        raise OSError("download failed")

    monkeypatch.setattr(
        faster_whisper,
        "_import_faster_whisper",
        lambda: SimpleNamespace(WhisperModel=broken),
    )
    monkeypatch.setattr(faster_whisper, "_cuda_device_count", lambda: 0)
    with pytest.raises(faster_whisper._LocalAsrFailure) as excinfo:
        faster_whisper._load_whisper_model(faster_whisper._model_spec_from_env())
    assert excinfo.value.code == "ASR_LOCAL_MODEL_LOAD_FAILED"


# ---------------------------------------------------------------------------
# Model lifetime
# ---------------------------------------------------------------------------


async def test_model_is_shared_across_workers_and_leases_are_returned(pool) -> None:
    model = _FakeModel(_segment("一"))
    loader = _RecordingLoader(model)
    spec = faster_whisper._model_spec_from_env()

    for _ in range(2):
        task, requests, responses = _start_worker(
            AsrSessionConfig(language="zh-CN"), loader, pool
        )
        await _next_event(responses, "ready")
        await _send_utterance(requests)
        await _next_event(responses, "final")
        assert pool.lease_count(spec) == 1
        await _shutdown(task, requests, responses)
        assert pool.lease_count(spec) == 0

    # Reconnects within the idle window reuse the loaded model.
    assert loader.calls == 1
    assert pool.loaded_count() == 1
    assert await asyncio.to_thread(pool.release_idle) == 1
    assert pool.loaded_count() == 0


def test_idle_model_is_released_after_timeout() -> None:
    pool = faster_whisper._WhisperModelPool(idle_release_seconds=0.05)
    spec = faster_whisper._ModelSpec(model=None, device="cpu", compute_type=None)
    loader = _RecordingLoader(object())

    pool.acquire(spec, loader)
    pool.release(spec)
    deadline = time.monotonic() + 2
    while pool.loaded_count() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pool.loaded_count() == 0

    # Re-acquiring before the timer fires cancels the release.
    pool = faster_whisper._WhisperModelPool(idle_release_seconds=0.2)
    pool.acquire(spec, loader)
    pool.release(spec)
    pool.acquire(spec, loader)
    time.sleep(0.4)
    assert pool.loaded_count() == 1
    assert pool.lease_count(spec) == 1


async def test_worker_publishes_model_warmup_on_its_queue(pool) -> None:
    from main_logic.asr_client._infra import _RealtimeAsrSessionImpl
    from main_logic.asr_client.warmup import provider_warmup_state

    gate = threading.Event()
    model = _FakeModel(_segment("好"))

    def slow_loader(_spec: faster_whisper._ModelSpec) -> Any:
        gate.wait(5)
        return model

    task, requests, responses = _start_worker(
        AsrSessionConfig(language="zh-CN"), slow_loader, pool
    )
    await _next_event(responses, "ready")
    # Ready is reported before the model exists; the session can tell.
    session_view = SimpleNamespace(_request_queue=requests)
    state = provider_warmup_state(requests)
    assert state is not None and state.pending is True
    assert _RealtimeAsrSessionImpl.provider_warmup_pending.fget(session_view) is True
    assert (
        _RealtimeAsrSessionImpl.provider_warmup_completed_at.fget(session_view)
        is None
    )

    await _send_utterance(requests)
    before_ready = time.monotonic()
    gate.set()
    assert (await _next_event(responses, "final")).text == "好"
    assert _RealtimeAsrSessionImpl.provider_warmup_pending.fget(session_view) is False
    completed_at = _RealtimeAsrSessionImpl.provider_warmup_completed_at.fget(
        session_view
    )
    assert completed_at is not None and completed_at >= before_ready
    await _shutdown(task, requests, responses)


async def test_shutdown_during_load_returns_the_abandoned_lease(pool) -> None:
    gate = threading.Event()
    model = _FakeModel()

    def slow_loader(_spec: faster_whisper._ModelSpec) -> Any:
        gate.wait(5)
        return model

    spec = faster_whisper._model_spec_from_env()
    task, requests, responses = _start_worker(AsrSessionConfig(), slow_loader, pool)
    await _next_event(responses, "ready")
    await _shutdown(task, requests, responses)

    gate.set()
    deadline = time.monotonic() + 2
    while pool.loaded_count() == 0 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    assert pool.loaded_count() == 1
    assert pool.lease_count(spec) == 0
