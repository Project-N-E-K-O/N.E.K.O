from argparse import Namespace
from pathlib import Path
import sys

import pytest

from scripts import asr_realtime_smoke as smoke


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
