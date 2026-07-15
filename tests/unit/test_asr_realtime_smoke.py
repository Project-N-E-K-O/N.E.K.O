import asyncio
from argparse import Namespace
from pathlib import Path
import sys

import pytest

from scripts import asr_realtime_smoke as smoke
from main_logic.asr_client._infra import AsrSessionConfig, _AsrWorkerRequest


def test_openai_defaults_to_standard_environment_key(monkeypatch) -> None:
    import utils.config_manager as config_manager

    monkeypatch.setenv("OPENAI_API_KEY", "standard-openai-key")
    monkeypatch.delenv("ASSIST_API_KEY_OPENAI", raising=False)
    monkeypatch.setattr(
        config_manager,
        "get_config_manager",
        lambda: pytest.fail("keybook fallback should not be needed"),
    )

    assert smoke._resolve_api_key("openai", "") == "standard-openai-key"

    monkeypatch.setenv("CUSTOM_OPENAI_API_KEY", "override-openai-key")
    assert (
        smoke._resolve_api_key("openai", "CUSTOM_OPENAI_API_KEY")
        == "override-openai-key"
    )

    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("ASSIST_API_KEY_OPENAI", "legacy-openai-key")
    assert smoke._resolve_api_key("openai", "") == "legacy-openai-key"


def test_openai_environment_resolution_falls_back_to_keybook(monkeypatch) -> None:
    import utils.config_manager as config_manager

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ASSIST_API_KEY_OPENAI", raising=False)

    class FakeConfigManager:
        value = "keybook-openai-key"

        def get_core_config(self):
            return {"ASSIST_API_KEY_OPENAI": self.value}

    manager = FakeConfigManager()
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: manager)

    assert smoke._resolve_api_key("openai", "") == "keybook-openai-key"

    manager.value = ""
    with pytest.raises(
        RuntimeError,
        match="ASR_CREDENTIALS_MISSING: ASSIST_API_KEY_OPENAI",
    ):
        smoke._resolve_api_key("openai", "")


@pytest.mark.parametrize(
    ("provider", "credential_field", "worker_name"),
    [
        ("glm", "ASSIST_API_KEY_GLM", "glm_asr_worker"),
        ("gemini", "ASSIST_API_KEY_GEMINI", "gemini_asr_worker"),
    ],
)
def test_segmented_providers_are_available_to_smoke_cli(
    monkeypatch,
    provider: str,
    credential_field: str,
    worker_name: str,
) -> None:
    monkeypatch.setattr(sys, "argv", ["asr_realtime_smoke.py", provider, "turn.wav"])

    args = smoke.parse_args()

    assert args.provider == provider
    assert args.endpointing_mode == "manual"
    assert smoke._CREDENTIAL_FIELDS[provider] == credential_field
    assert smoke._resolve_provider(provider).__name__ == worker_name


def test_smart_turn_auto_cli_accepts_expected_final_count(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "asr_realtime_smoke.py",
            "openai",
            "turn.wav",
            "--smart-turn-auto",
            "--smart-turn-silence-ms",
            "800",
            "--expected-finals",
            "2",
        ],
    )

    args = smoke.parse_args()

    assert args.smart_turn_auto is True
    assert args.smart_turn_silence_ms == 800
    assert args.expected_finals == 2


@pytest.mark.asyncio
async def test_smart_turn_auto_streams_silence_without_manual_commit() -> None:
    turn = smoke._AudioTurn(Path("turn.wav"), b"\x01\x00" * 160, 16_000, 0.01)

    class FakeSession:
        def __init__(self) -> None:
            self.audio: list[bytes] = []
            self.manual_commits = 0

        async def stream_audio(self, audio: bytes, *, sample_rate_hz: int) -> None:
            assert sample_rate_hz == 16_000
            self.audio.append(audio)

        async def signal_user_activity_end(self) -> None:
            self.manual_commits += 1

    session = FakeSession()

    await smoke._stream_turn(
        session,
        turn,
        chunk_ms=10,
        endpointing_mode="manual",
        realtime=False,
        vad_silence_ms=0,
        smart_turn_auto=True,
        smart_turn_silence_ms=320,
    )

    assert session.manual_commits == 0
    assert sum(map(len, session.audio)) == len(turn.pcm) + 16_000 * 2 * 320 // 1000


@pytest.mark.asyncio
async def test_realtime_stream_uses_absolute_deadline_pacing(monkeypatch) -> None:
    turn = smoke._AudioTurn(Path("turn.wav"), b"\x01\x00" * 320, 16_000, 0.02)
    deadlines: list[float] = []

    class FakeSession:
        async def stream_audio(self, _audio: bytes, *, sample_rate_hz: int) -> None:
            assert sample_rate_hz == 16_000

        async def signal_user_activity_end(self) -> None:
            return None

    async def capture_deadline(deadline: float) -> None:
        deadlines.append(deadline)

    monkeypatch.setattr(smoke, "_sleep_until", capture_deadline)

    await smoke._stream_turn(
        FakeSession(),
        turn,
        chunk_ms=10,
        endpointing_mode="manual",
        realtime=True,
        vad_silence_ms=0,
    )

    assert len(deadlines) == 2
    assert deadlines[1] - deadlines[0] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_worker_observer_counts_normalized_audio_and_commits() -> None:
    observation = smoke._Observation(started_at=0.0)

    async def worker(request_queue, _response_queue, _api_key, _config) -> None:
        while True:
            request = await request_queue.get()
            request_queue.task_done()
            if request.kind == "shutdown":
                return

    request_queue = asyncio.Queue()
    response_queue = asyncio.Queue()
    observed = smoke._observe_worker(worker, observation)
    await request_queue.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=0,
            utterance_id=1,
            audio=b"\x01\x00" * 160,
        )
    )
    await request_queue.put(
        _AsrWorkerRequest(
            kind="commit",
            generation=0,
            buffer_epoch=0,
            utterance_id=1,
        )
    )
    await request_queue.put(
        _AsrWorkerRequest(
            kind="shutdown",
            generation=0,
            buffer_epoch=0,
            utterance_id=2,
        )
    )

    await observed(
        request_queue,
        response_queue,
        "key",
        AsrSessionConfig(endpointing_mode="manual"),
    )

    assert observation.audio_bytes == 320
    assert len(observation.commit_at) == 1


@pytest.mark.asyncio
async def test_provider_smoke_rejects_mixed_sample_rates_before_session(
    monkeypatch,
) -> None:
    first_path = Path("first.wav")
    second_path = Path("second.wav")
    turns = {
        first_path: smoke._AudioTurn(first_path, b"\0\0", 16_000, 1 / 16_000),
        second_path: smoke._AudioTurn(second_path, b"\0\0", 48_000, 1 / 48_000),
    }
    monkeypatch.setattr(smoke, "_read_wav_pcm16", turns.__getitem__)

    with pytest.raises(ValueError, match="same sample rate"):
        await smoke._run_provider_smoke(Namespace(audio=list(turns)))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_result", "expected_ok", "expected_error"),
    [
        ("ASR_CREDENTIALS_REJECTED: invalid API key", True, None),
        (None, False, "ASR_INVALID_CREDENTIAL_ACCEPTED"),
    ],
)
async def test_invalid_credential_waits_for_first_provider_result(
    monkeypatch,
    provider_result: str | None,
    expected_ok: bool,
    expected_error: str | None,
) -> None:
    audio_path = Path("turn.wav")
    turn = smoke._AudioTurn(audio_path, b"\0\0", 16_000, 1 / 16_000)
    session_callbacks = {}

    class FakeSession:
        is_ready = True

        def __init__(self, **kwargs) -> None:
            session_callbacks.update(kwargs)

        async def connect(self) -> None:
            return None

        async def clear_audio_buffer(self) -> None:
            return None

        async def close(self) -> None:
            return None

    async def stream_first_turn(*args, **kwargs) -> None:
        if provider_result is None:
            await session_callbacks["on_input_transcript"]("accepted")
        else:
            await session_callbacks["on_connection_error"](provider_result)

    monkeypatch.setattr(smoke, "_read_wav_pcm16", lambda path: turn)
    monkeypatch.setattr(smoke, "_RealtimeAsrSessionImpl", FakeSession)
    monkeypatch.setattr(smoke, "_stream_turn", stream_first_turn)

    result = await smoke._run_provider_smoke(
        Namespace(
            provider="glm",
            audio=[audio_path],
            endpointing_mode="manual",
            invalid_credential=True,
            api_key_env="",
            language="zh",
            show_transcripts=False,
            skip_clear=False,
            chunk_ms=100,
            no_realtime=True,
            vad_silence_ms=0,
            timeout_s=0.1,
        )
    )

    assert result.ok is expected_ok
    assert result.auth_failure_observed is expected_ok
    if expected_error is not None:
        assert expected_error in result.errors


@pytest.mark.asyncio
async def test_smart_turn_auto_uses_adapter_and_expected_business_finals(
    monkeypatch,
) -> None:
    audio_path = Path("split-turn.wav")
    turn = smoke._AudioTurn(audio_path, b"\0\0", 16_000, 1 / 16_000)
    session_kwargs = {}
    observation = smoke._Observation(started_at=0.0)

    class FakeSession:
        is_ready = True

        def __init__(self, **kwargs) -> None:
            session_kwargs.update(kwargs)

        async def connect(self) -> None:
            return None

        async def clear_audio_buffer(self) -> None:
            return None

        async def close(self) -> None:
            return None

    async def stream_split_turn(*_args, **_kwargs) -> None:
        observation.final_at.extend((0.01, 0.02))
        await session_kwargs["on_input_transcript"]("first")
        await session_kwargs["on_input_transcript"]("second")

    monkeypatch.setattr(smoke, "_read_wav_pcm16", lambda _path: turn)
    monkeypatch.setattr(smoke, "_resolve_api_key", lambda *_args: "test-key")
    monkeypatch.setattr(smoke, "_Observation", lambda **_kwargs: observation)
    monkeypatch.setattr(smoke, "_RealtimeAsrSessionImpl", FakeSession)
    monkeypatch.setattr(smoke, "_stream_turn", stream_split_turn)

    result = await smoke._run_provider_smoke(
        Namespace(
            provider="openai",
            audio=[audio_path],
            endpointing_mode="manual",
            invalid_credential=False,
            api_key_env="",
            language="zh",
            show_transcripts=False,
            skip_clear=False,
            chunk_ms=10,
            no_realtime=True,
            vad_silence_ms=0,
            timeout_s=0.1,
            smart_turn_auto=True,
            smart_turn_silence_ms=320,
            expected_finals=2,
        )
    )

    assert callable(session_kwargs["voice_turn_factory"])
    assert result.ok is True
    assert result.business_finals == 2
