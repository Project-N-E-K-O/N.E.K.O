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
    ("session_language", "primed"),
    [
        ("zh-TW", True), ("zh-HK", True), ("zh-Hant-TW", True),
        ("zh-CN", False), ("zh", False), ("ja", False), ("auto", False),
    ],
)
async def test_traditional_chinese_sessions_prime_traditional_script(
    pool, session_language, primed
) -> None:
    from config.prompts.prompts_voice import WHISPER_TRADITIONAL_CHINESE_INITIAL_PROMPT

    model = _FakeModel(_segment("你好"))
    task, requests, responses = _start_worker(
        AsrSessionConfig(language=session_language), _RecordingLoader(model), pool
    )
    await _next_event(responses, "ready")
    await _send_utterance(requests)
    await _next_event(responses, "final")
    if primed:
        assert model.calls[0]["initial_prompt"] == WHISPER_TRADITIONAL_CHINESE_INITIAL_PROMPT
    else:
        assert "initial_prompt" not in model.calls[0]
    await _shutdown(task, requests, responses)


@pytest.mark.parametrize(("confident", "expected_empty"), [(False, True), (True, False)])
async def test_echoed_priming_text_is_dropped_only_when_unconfident(
    pool, confident, expected_empty
) -> None:
    # Silence often comes back as the priming sentence itself; a user who
    # really says it, recognized with confidence, is still heard.
    from config.prompts.prompts_voice import WHISPER_TRADITIONAL_CHINESE_INITIAL_PROMPT

    segment = (
        _segment(WHISPER_TRADITIONAL_CHINESE_INITIAL_PROMPT)
        if confident
        else _segment(WHISPER_TRADITIONAL_CHINESE_INITIAL_PROMPT, no_speech_prob=0.8)
    )
    model = _FakeModel(segment)
    task, requests, responses = _start_worker(
        AsrSessionConfig(language="zh-TW"), _RecordingLoader(model), pool
    )
    await _next_event(responses, "ready")
    await _send_utterance(requests)
    text = (await _next_event(responses, "final")).text
    assert (text == "") is expected_empty
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


def _install_downloading_whisper(
    monkeypatch, *, download_error=None, cuda_ok=False, cached=()
):
    downloads: list[str] = []
    constructed: list[tuple[str, str, str]] = []

    def download_model(name: str, local_files_only: bool = False) -> str:
        downloads.append(name + (" (cache)" if local_files_only else ""))
        if local_files_only:
            if name not in cached:
                raise FileNotFoundError(name + " is not cached")
            return "/cache/" + name
        if download_error is not None:
            raise download_error
        return "/cache/" + name

    class _ProbeModel:
        def __init__(self, device: str) -> None:
            self.device = device

        def transcribe(self, audio: Any, **kwargs: Any):
            if self.device == "cuda" and not cuda_ok:
                raise RuntimeError("Library cublas64_12.dll is not found")
            return iter(()), SimpleNamespace()

    def whisper_model(path: str, *, device: str, compute_type: str) -> _ProbeModel:
        constructed.append((path, device, compute_type))
        return _ProbeModel(device)

    monkeypatch.setattr(
        faster_whisper,
        "_import_faster_whisper",
        lambda: SimpleNamespace(WhisperModel=whisper_model, download_model=download_model),
    )
    monkeypatch.setattr(faster_whisper, "_cuda_device_count", lambda: 1)
    return downloads, constructed


def test_download_failure_ends_the_load_without_trying_other_candidates(monkeypatch) -> None:
    downloads, constructed = _install_downloading_whisper(
        monkeypatch, download_error=ConnectionError("huggingface.co unreachable")
    )
    with pytest.raises(faster_whisper._LocalAsrFailure) as excinfo:
        faster_whisper._load_whisper_model(faster_whisper._model_spec_from_env())
    assert excinfo.value.code == "ASR_LOCAL_MODEL_LOAD_FAILED"
    # One network attempt; the CPU model is only looked up in the local cache.
    assert downloads == ["medium", "base (cache)"]
    assert constructed == []


def test_download_failure_still_uses_a_cached_cpu_model(monkeypatch) -> None:
    downloads, constructed = _install_downloading_whisper(
        monkeypatch,
        download_error=ConnectionError("huggingface.co unreachable"),
        cached=("base",),
    )
    model = faster_whisper._load_whisper_model(faster_whisper._model_spec_from_env())
    assert model.device == "cpu"
    assert downloads == ["medium", "base (cache)"]
    assert constructed == [("/cache/base", "cpu", "int8")]


def test_device_failure_reuses_the_downloaded_weights(monkeypatch) -> None:
    downloads, constructed = _install_downloading_whisper(monkeypatch, cuda_ok=False)
    model = faster_whisper._load_whisper_model(faster_whisper._model_spec_from_env())
    assert model.device == "cpu"
    # medium is fetched once for both CUDA attempts; base only for the CPU one.
    assert downloads == ["medium", "base"]
    assert constructed == [
        ("/cache/medium", "cuda", "float16"),
        ("/cache/medium", "cuda", "int8_float16"),
        ("/cache/base", "cpu", "int8"),
    ]


def test_local_model_directory_is_not_downloaded(monkeypatch, tmp_path) -> None:
    downloads, constructed = _install_downloading_whisper(monkeypatch, cuda_ok=True)
    monkeypatch.setenv("NEKO_WHISPER_MODEL", str(tmp_path))
    faster_whisper._load_whisper_model(faster_whisper._model_spec_from_env())
    assert downloads == []
    assert constructed == [(str(tmp_path), "cuda", "float16")]


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


def test_failed_load_thread_start_does_not_leave_a_stuck_load(monkeypatch) -> None:
    pool = faster_whisper._WhisperModelPool(idle_release_seconds=60.0)
    spec = faster_whisper._ModelSpec(model=None, device="cpu", compute_type=None)
    original_start = threading.Thread.start
    calls = {"n": 0}

    def flaky_start(self) -> None:
        if self.name == "faster-whisper-load" and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("can't start new thread")
        original_start(self)

    monkeypatch.setattr(threading.Thread, "start", flaky_start)
    with pytest.raises(RuntimeError):
        pool.ensure_loading(spec, _RecordingLoader(object()))
    assert spec not in pool._inflight
    # Once threads are available again, the same spec loads normally.
    pool.ensure_loading(spec, _RecordingLoader(object())).result(timeout=5)
    assert pool.loaded_count() == 1


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


class _ReadyProbeQueue(asyncio.Queue):
    """Response queue that records the warm-up state the moment "ready" is put.

    That is before the worker yields to the loop, so before its background
    load task has run at all.
    """

    def __init__(self, requests: asyncio.Queue, *, fail_ready: bool = False) -> None:
        super().__init__()
        self._requests = requests
        self._fail_ready = fail_ready
        self.pending_at_ready: bool | None = None

    async def put(self, item: _AsrWorkerEvent) -> None:
        if item.kind == "ready":
            from main_logic.asr_client.warmup import provider_warmup_state

            state = provider_warmup_state(self._requests)
            self.pending_at_ready = state is not None and state.pending
            if self._fail_ready:
                raise RuntimeError("ready could not be delivered")
        await super().put(item)


def _start_probed_worker(pool, loader, *, fail_ready: bool = False):
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses = _ReadyProbeQueue(requests, fail_ready=fail_ready)
    task = asyncio.create_task(
        faster_whisper.faster_whisper_asr_worker(
            requests, responses, "", AsrSessionConfig(language="zh-CN"),
            model_loader=loader, model_pool=pool,
        )
    )
    return task, requests, responses


async def test_warmup_is_published_before_ready_only_when_the_model_must_load(pool) -> None:
    gate = threading.Event()
    model = _FakeModel(_segment("好"))

    def slow_loader(_spec: faster_whisper._ModelSpec) -> Any:
        gate.wait(5)
        return model

    task, requests, responses = _start_probed_worker(pool, slow_loader)
    await _next_event(responses, "ready")
    assert responses.pending_at_ready is True
    gate.set()
    await _send_utterance(requests)
    await _next_event(responses, "final")
    await _shutdown(task, requests, responses)

    # The model is now loaded: a new session starts without any warm-up.
    task, requests, responses = _start_probed_worker(pool, slow_loader)
    await _next_event(responses, "ready")
    assert responses.pending_at_ready is False
    await _shutdown(task, requests, responses)


async def test_warmup_reason_tells_a_first_load_from_a_reload(monkeypatch) -> None:
    from main_logic.asr_client.warmup import provider_warmup_reason

    pool = faster_whisper._WhisperModelPool(idle_release_seconds=0.05)
    model = _FakeModel(_segment("好"))
    loader = _RecordingLoader(model)

    task, requests, responses = _start_probed_worker(pool, loader)
    await _next_event(responses, "ready")
    assert provider_warmup_reason(requests) == "ASR_LOCAL_MODEL_LOADING"
    await _send_utterance(requests)
    await _next_event(responses, "final")
    await _shutdown(task, requests, responses)

    # Dropped after idling: loading it again is a reload, not a first use.
    for _ in range(200):
        if pool.loaded_count() == 0:
            break
        await asyncio.sleep(0.01)
    assert pool.loaded_count() == 0
    task, requests, responses = _start_probed_worker(pool, loader)
    await _next_event(responses, "ready")
    assert responses.pending_at_ready is True
    assert provider_warmup_reason(requests) == "ASR_LOCAL_MODEL_RELOADING"
    await _shutdown(task, requests, responses)


async def test_warmup_taken_before_ready_ends_even_if_the_load_never_ran(pool) -> None:
    # "ready" fails to go out, so the worker ends before its load task ever
    # ran: that task never reaches its own cleanup, the worker's must.
    from main_logic.asr_client.warmup import provider_warmup_state

    gate = threading.Event()

    def slow_loader(_spec: faster_whisper._ModelSpec) -> Any:
        gate.wait(5)
        return _FakeModel()

    task, requests, responses = _start_probed_worker(pool, slow_loader, fail_ready=True)
    try:
        await asyncio.wait_for(task, 3)
        assert responses.pending_at_ready is True
        assert provider_warmup_state(requests).pending is False
    finally:
        gate.set()


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


async def test_session_churn_during_a_download_shares_one_load(pool) -> None:
    # Sessions that start and end while the model downloads must share one
    # load on the pool's own thread, not each park a default-executor thread.
    gate = threading.Event()
    model = _FakeModel()
    load_threads: list[str] = []

    load_daemons: list[bool] = []

    def slow_loader(_spec: faster_whisper._ModelSpec) -> Any:
        load_threads.append(threading.current_thread().name)
        load_daemons.append(threading.current_thread().daemon)
        gate.wait(5)
        return model

    load_jobs: list[faster_whisper._ModelSpec] = []
    original_load = pool._load_unleased

    def counting_load(spec_: faster_whisper._ModelSpec, loader_: Any) -> None:
        load_jobs.append(spec_)
        original_load(spec_, loader_)

    pool._load_unleased = counting_load
    spec = faster_whisper._model_spec_from_env()
    try:
        for _ in range(5):
            task, requests, responses = _start_worker(AsrSessionConfig(), slow_loader, pool)
            await _next_event(responses, "ready")
            await _shutdown(task, requests, responses)
    finally:
        gate.set()
    deadline = time.monotonic() + 2
    while pool.loaded_count() == 0 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)

    await asyncio.sleep(0.05)
    assert len(load_jobs) == 1  # one shared job, not one queued per session
    assert len(load_threads) == 1
    assert load_threads[0].startswith("faster-whisper-load")
    # A stalled first-use download must not hold up interpreter exit, which
    # joins executor threads but not daemon threads.
    assert load_daemons == [True]
    assert pool.loaded_count() == 1
    assert pool.lease_count(spec) == 0

    # The next session leases the already loaded model without loading again.
    task, requests, responses = _start_worker(AsrSessionConfig(), slow_loader, pool)
    await _next_event(responses, "ready")
    await _send_utterance(requests)
    await _next_event(responses, "final")
    assert len(load_threads) == 1
    assert pool.lease_count(spec) == 1
    await _shutdown(task, requests, responses)


async def test_local_decodes_are_bounded_across_sessions(pool) -> None:
    # Two sessions (e.g. an ended one whose decode still runs and its
    # successor) share the pool's single decode thread: never two native
    # decodes at once, whatever the session churn.
    model = _FakeModel(_segment("x"))
    model.release.clear()
    decode_threads: list[str] = []
    original = model.transcribe

    def recording_transcribe(audio: Any, **kwargs: Any):
        decode_threads.append(threading.current_thread().name)
        return original(audio, **kwargs)

    model.transcribe = recording_transcribe
    loader = _RecordingLoader(model)
    first = _start_worker(AsrSessionConfig(language="zh-CN"), loader, pool)
    second = _start_worker(AsrSessionConfig(language="zh-CN"), loader, pool)
    try:
        for task, requests, responses in (first, second):
            await _next_event(responses, "ready")
            await _send_utterance(requests)
        await asyncio.sleep(0.2)
        assert len(model.calls) == 1
    finally:
        model.release.set()
    for task, requests, responses in (first, second):
        await _next_event(responses, "final")
    assert len(model.calls) == 2
    assert all(name.startswith("faster-whisper-decode") for name in decode_threads)
    for task, requests, responses in (first, second):
        await _shutdown(task, requests, responses)


async def test_decode_cancelled_while_queued_stays_a_definite_non_delivery(pool) -> None:
    # A second session's decode waits behind the first on the shared decode
    # thread; cancelled before it starts, its audio never reached the model.
    model = _FakeModel(_segment("x"))
    model.release.clear()
    loader = _RecordingLoader(model)
    first = _start_worker(AsrSessionConfig(language="zh-CN"), loader, pool)
    second = _start_worker(AsrSessionConfig(language="zh-CN"), loader, pool)
    try:
        await _next_event(first[2], "ready")
        await _send_utterance(first[1])
        for _ in range(100):
            if model.calls:
                break
            await asyncio.sleep(0.01)
        await _next_event(second[2], "ready")
        await _send_utterance(second[1])
        await asyncio.wait_for(second[1].join(), 2)
        await asyncio.sleep(0.05)
        await _shutdown(*second)
        assert delivery_evidence(second[1]).attempted is False
    finally:
        model.release.set()
    await _next_event(first[2], "final")
    await _shutdown(*first)
    assert len(model.calls) == 1


async def test_running_decode_is_already_reported_as_attempted(pool) -> None:
    # While the model is decoding, a reader on the loop (e.g. a revocation)
    # must see the audio as handed over, not as a definite non-delivery.
    model = _FakeModel(_segment("x"))
    model.release.clear()
    task, requests, responses = _start_worker(
        AsrSessionConfig(language="zh-CN"), _RecordingLoader(model), pool
    )
    try:
        await _next_event(responses, "ready")
        await _send_utterance(requests)
        for _ in range(100):
            if model.calls:
                break
            await asyncio.sleep(0.01)
        assert model.calls
        assert delivery_evidence(requests).attempted is True
        assert delivery_evidence(requests).written_audio_bytes == 0
    finally:
        model.release.set()
    await _next_event(responses, "final")
    await _shutdown(task, requests, responses)


async def test_process_wide_decode_slots_bound_all_sessions(pool) -> None:
    # Other sessions already hold every process-wide slot: a new commit is
    # refused before its PCM is copied or queued.
    for _ in range(faster_whisper._MAX_PROCESS_DECODES):
        assert pool.try_reserve_decode() is True
    assert pool.try_reserve_decode() is False

    model = _FakeModel(_segment("x"))
    task, requests, responses = _start_worker(
        AsrSessionConfig(language="zh-CN"), _RecordingLoader(model), pool
    )
    await _next_event(responses, "ready")
    await _send_utterance(requests)
    error = await _next_event(responses, "error")
    assert error.error_code == "ASR_LOCAL_DECODE_BACKLOG"
    await _next_event(responses, "closed")
    await asyncio.wait_for(task, 3)
    assert model.calls == []

    for _ in range(faster_whisper._MAX_PROCESS_DECODES):
        pool.release_decode()


async def test_decode_slot_is_returned_when_the_decode_finishes(pool) -> None:
    model = _FakeModel(_segment("x"))
    task, requests, responses = _start_worker(
        AsrSessionConfig(language="zh-CN"), _RecordingLoader(model), pool
    )
    await _next_event(responses, "ready")
    for utterance_id in range(1, faster_whisper._MAX_PROCESS_DECODES + 3):
        await _send_utterance(requests, utterance_id=utterance_id)
        await _next_event(responses, "final")
    assert pool._decode_slots_used == 0
    await _shutdown(task, requests, responses)


def test_failed_cuda_candidate_is_released_before_the_next_one(monkeypatch) -> None:
    # A model that loaded but failed its CUDA probe must be gone before the
    # lower-memory candidate is built, or its VRAM makes that retry fail too.
    import gc
    import weakref

    alive_at_construction: list[bool] = []
    previous: list[weakref.ref] = []

    class _ProbeModel:
        def __init__(self, name: str, device: str, compute_type: str) -> None:
            self.device = device

        def transcribe(self, audio: Any, **kwargs: Any):
            if self.device == "cuda":
                raise RuntimeError("CUDA out of memory")
            return iter(()), SimpleNamespace()

    def whisper_model(name: str, *, device: str, compute_type: str) -> _ProbeModel:
        gc.collect()
        alive_at_construction.append(any(ref() is not None for ref in previous))
        model = _ProbeModel(name, device, compute_type)
        previous.append(weakref.ref(model))
        return model

    monkeypatch.setattr(
        faster_whisper,
        "_import_faster_whisper",
        lambda: SimpleNamespace(WhisperModel=whisper_model),
    )
    monkeypatch.setattr(faster_whisper, "_cuda_device_count", lambda: 1)

    model = faster_whisper._load_whisper_model(faster_whisper._model_spec_from_env())

    assert model.device == "cpu"
    # No failed candidate was still referenced when the next one was built.
    assert alive_at_construction == [False, False, False]


async def test_cancelled_queued_decode_keeps_its_slot_until_dequeued(pool) -> None:
    # The executor keeps a cancelled work item (and its PCM) queued until the
    # single decode thread reaches it, so its process-wide slot stays taken
    # until then; otherwise commit/end/start churn could pile up PCM.
    model = _FakeModel(_segment("x"))
    model.release.clear()
    loader = _RecordingLoader(model)
    first = _start_worker(AsrSessionConfig(language="zh-CN"), loader, pool)
    second = _start_worker(AsrSessionConfig(language="zh-CN"), loader, pool)
    try:
        await _next_event(first[2], "ready")
        await _send_utterance(first[1])
        for _ in range(100):
            if model.calls:
                break
            await asyncio.sleep(0.01)
        await _next_event(second[2], "ready")
        await _send_utterance(second[1])
        await asyncio.wait_for(second[1].join(), 2)
        await asyncio.sleep(0.05)
        await _shutdown(*second)
        assert pool._decode_slots_used == 2
    finally:
        model.release.set()
    await _next_event(first[2], "final")
    await _shutdown(*first)
    deadline = time.monotonic() + 2
    while pool._decode_slots_used and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert pool._decode_slots_used == 0
    assert len(model.calls) == 1  # the cancelled job was skipped, not decoded


async def test_slot_is_returned_when_the_task_is_cancelled_before_it_runs(pool) -> None:
    # A task cancelled before its first step never enters its body, so its own
    # finally cannot give the slot back; the done callback must.
    import functools

    assert pool.try_reserve_decode() is True
    handoff = faster_whisper._DecodeHandoff()

    async def never_runs() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(never_runs())
    task.add_done_callback(
        functools.partial(faster_whisper._return_slot_unless_handed_off, pool, handoff)
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert pool._decode_slots_used == 0


async def test_handed_off_slot_is_not_returned_twice(pool) -> None:
    import functools

    assert pool.try_reserve_decode() is True
    handoff = faster_whisper._DecodeHandoff(submitted=True)

    async def done() -> None:
        return None

    task = asyncio.create_task(done())
    task.add_done_callback(
        functools.partial(faster_whisper._return_slot_unless_handed_off, pool, handoff)
    )
    await task
    await asyncio.sleep(0)
    # The executor owns this slot now; only the decode thread gives it back.
    assert pool._decode_slots_used == 1
    pool.release_decode()


async def test_waiting_behind_another_sessions_decode_counts_as_warmup(pool) -> None:
    # A job queued behind another session's decode must not burn its own
    # per-utterance final timeout: it is published as warming up until it
    # reaches the decoder.
    from main_logic.asr_client.warmup import provider_warmup_state

    model = _FakeModel(_segment("x"))
    model.release.clear()
    loader = _RecordingLoader(model)
    first = _start_worker(AsrSessionConfig(language="zh-CN"), loader, pool)
    second = _start_worker(AsrSessionConfig(language="zh-CN"), loader, pool)
    try:
        await _next_event(first[2], "ready")
        await _send_utterance(first[1])
        for _ in range(100):
            if model.calls:
                break
            await asyncio.sleep(0.01)
        await _next_event(second[2], "ready")
        await _send_utterance(second[1])
        await asyncio.wait_for(second[1].join(), 2)
        await asyncio.sleep(0.05)
        state = provider_warmup_state(second[1])
        assert state is not None and state.pending is True
    finally:
        model.release.set()
    await _next_event(second[2], "final")
    state = provider_warmup_state(second[1])
    assert state.pending is False and state.completed_at is not None
    await _next_event(first[2], "final")
    for task, requests, responses in (first, second):
        await _shutdown(task, requests, responses)


def test_session_reads_warmup_state_under_its_lock() -> None:
    # A decode thread ends a wait by setting ``pending`` and ``completed_at``
    # together under the state's lock. The watchdog's reads must take it too,
    # or they can see the wait over with no (or a stale) completion time.
    from main_logic.asr_client._infra import _RealtimeAsrSessionImpl
    from main_logic.asr_client.warmup import (
        begin_provider_warmup,
        complete_provider_warmup,
        provider_warmup_state,
    )

    queue: asyncio.Queue[Any] = asyncio.Queue()
    token = begin_provider_warmup(queue)
    state = provider_warmup_state(queue)
    session_view = SimpleNamespace(_request_queue=queue)
    for prop, expected in (
        (_RealtimeAsrSessionImpl.provider_warmup_pending, False),
        (_RealtimeAsrSessionImpl.provider_warmup_completed_at, None),
    ):
        results: list[Any] = []
        with state.lock:
            reader = threading.Thread(
                target=lambda: results.append(prop.fget(session_view))
            )
            reader.start()
            reader.join(0.1)
            assert reader.is_alive()  # blocked while the writer holds the lock
            state.pending = False
        reader.join(2)
        assert results == [expected]
        state.pending = True
    complete_provider_warmup(queue, token)
    assert state.pending is False and state.completed_at is not None


async def test_running_decode_keeps_the_model_leased_after_its_session_ends(pool) -> None:
    # A native decode cannot be interrupted and may outlive its session. Until
    # it has left the decoder it holds a lease of its own, so the idle timer
    # cannot drop the model under it and a new session does not load a copy.
    model = _FakeModel(_segment("x"))
    model.release.clear()
    spec = faster_whisper._model_spec_from_env()
    task, requests, responses = _start_worker(
        AsrSessionConfig(language="zh-CN"), _RecordingLoader(model), pool
    )
    try:
        await _next_event(responses, "ready")
        await _send_utterance(requests)
        for _ in range(200):
            if model.calls:
                break
            await asyncio.sleep(0.01)
        assert model.calls
        await _shutdown(task, requests, responses)
        assert pool.lease_count(spec) == 1
    finally:
        model.release.set()
    for _ in range(200):
        if pool.lease_count(spec) == 0:
            break
        await asyncio.sleep(0.01)
    assert pool.lease_count(spec) == 0
    assert pool.loaded_count() == 1


def test_warmup_snapshot_is_taken_under_the_lock() -> None:
    from main_logic.asr_client._infra import _RealtimeAsrSessionImpl
    from main_logic.asr_client.warmup import (
        begin_provider_warmup,
        complete_provider_warmup,
        provider_warmup_state,
    )

    queue: asyncio.Queue[Any] = asyncio.Queue()
    token = begin_provider_warmup(queue)
    state = provider_warmup_state(queue)
    session_view = SimpleNamespace(_request_queue=queue)
    results: list[Any] = []
    with state.lock:
        reader = threading.Thread(
            target=lambda: results.append(
                _RealtimeAsrSessionImpl.provider_warmup_snapshot.fget(session_view)
            )
        )
        reader.start()
        reader.join(0.1)
        assert reader.is_alive()  # waits for the writer's whole update
        state.completed_at = 123.0
        state.pending = False
    reader.join(2)
    assert results == [(False, 123.0)]
    state.pending = True
    complete_provider_warmup(queue, token)
    assert _RealtimeAsrSessionImpl.provider_warmup_snapshot.fget(session_view)[0] is False


def test_warmup_ends_only_when_every_wait_has_ended() -> None:
    # A cancelled older job leaving the decode queue must not clear the pending
    # state of a newer job still waiting on the same session queue.
    from main_logic.asr_client.warmup import (
        begin_provider_warmup,
        complete_provider_warmup,
        provider_warmup_state,
    )

    queue: asyncio.Queue[Any] = asyncio.Queue()
    older = begin_provider_warmup(queue)
    newer = begin_provider_warmup(queue)
    complete_provider_warmup(queue, older)
    state = provider_warmup_state(queue)
    assert state.pending is True and state.completed_at is None
    complete_provider_warmup(queue, older)  # ending the same wait twice is harmless
    assert state.pending is True
    complete_provider_warmup(queue, newer)
    assert state.pending is False and state.completed_at is not None


async def test_waiting_behind_own_earlier_decode_does_not_pause_its_watchdog(pool) -> None:
    # Warm-up state is session-wide. A later turn queued behind this session's
    # own running decode must not publish it, or it would pause the earlier
    # turn's final watchdog and a stuck decode would run on the warm-up budget.
    from main_logic.asr_client.warmup import provider_warmup_state

    model = _FakeModel(_segment("x"))
    model.release.clear()
    task, requests, responses = _start_worker(
        AsrSessionConfig(language="zh-CN"), _RecordingLoader(model), pool
    )
    try:
        await _next_event(responses, "ready")
        await _send_utterance(requests, utterance_id=1)
        for _ in range(100):
            if model.calls:
                break
            await asyncio.sleep(0.01)
        await _send_utterance(requests, utterance_id=2)
        await asyncio.wait_for(requests.join(), 2)
        await asyncio.sleep(0.05)
        state = provider_warmup_state(requests)
        assert state is not None and state.pending is False
    finally:
        model.release.set()
    for _ in range(2):
        await _next_event(responses, "final")
    await _shutdown(task, requests, responses)


async def test_later_segment_left_behind_another_session_counts_as_warmup(pool) -> None:
    # A long utterance split into segments: segment 2 is queued while segment
    # 1 decodes, with another session's job in between. Once segment 1 leaves
    # the decoder, segment 2 waits only on the other session and must be
    # published as warming up until it reaches the decoder.
    from main_logic.asr_client.warmup import provider_warmup_state

    gates = [threading.Event() for _ in range(3)]
    started: list[int] = []

    class _GatedModel:
        def transcribe(self, audio: Any, **kwargs: Any):
            index = len(started)
            started.append(index)
            if not gates[index].wait(5):
                raise TimeoutError("test model was never released")
            return iter([_segment("x")]), SimpleNamespace()

    async def wait_started(count: int) -> None:
        for _ in range(200):
            if len(started) >= count:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"decoder never started job {count}")

    loader = _RecordingLoader(_GatedModel())
    own = _start_worker(AsrSessionConfig(language="zh-CN"), loader, pool)
    other = _start_worker(AsrSessionConfig(language="zh-CN"), loader, pool)
    try:
        await _next_event(own[2], "ready")
        await _next_event(other[2], "ready")
        await _send_utterance(own[1], utterance_id=1)
        await wait_started(1)
        await _send_utterance(other[1])
        await asyncio.wait_for(other[1].join(), 2)
        await _send_utterance(own[1], utterance_id=2)
        await asyncio.wait_for(own[1].join(), 2)
        await asyncio.sleep(0.05)
        state = provider_warmup_state(own[1])
        # Segment 1 is this session's own decode: no warm-up over it.
        assert state is not None and state.pending is False

        gates[0].set()
        await _next_event(own[2], "final")
        await wait_started(2)
        await asyncio.sleep(0.05)
        # Segment 2 now waits only on the other session's decode.
        assert state.pending is True

        gates[1].set()
        await _next_event(other[2], "final")
        await wait_started(3)
        await asyncio.sleep(0.05)
        assert state.pending is False and state.completed_at is not None
    finally:
        for gate in gates:
            gate.set()
    await _next_event(own[2], "final")
    for worker in (own, other):
        await _shutdown(*worker)
