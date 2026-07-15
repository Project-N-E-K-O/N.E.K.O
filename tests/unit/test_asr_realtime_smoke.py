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
