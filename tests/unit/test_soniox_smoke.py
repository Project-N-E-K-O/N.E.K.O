import json
from pathlib import Path

import pytest

from scripts import soniox_realtime_smoke as smoke
from scripts.soniox_realtime_smoke import _render_tokens


def test_smoke_waits_for_end_and_filters_control_tokens():
    final_tokens = []
    text, saw_text, saw_end = _render_tokens(
        {
            "tokens": [
                {"text": "hello ", "is_final": True},
                {"text": "wor", "is_final": False},
            ]
        },
        final_tokens,
    )
    assert (text, saw_text, saw_end) == ("hello wor", True, False)

    text, saw_text, saw_end = _render_tokens(
        {
            "tokens": [
                {"text": "world", "is_final": True},
                {"text": "<end>", "is_final": True},
                {"text": "<fin>", "is_final": True},
            ]
        },
        final_tokens,
    )
    assert (text, saw_text, saw_end) == ("hello world", True, True)
    assert "<end>" not in text and "<fin>" not in text


def test_smoke_preview_can_be_provisional_but_endpoint_uses_stable_tokens():
    final_tokens = ["stable"]
    text, saw_text, saw_end = _render_tokens(
        {
            "tokens": [
                {"text": " temporary", "is_final": False},
                {"text": "<end>", "is_final": True},
            ]
        },
        final_tokens,
    )
    assert (text, saw_text, saw_end) == ("stable temporary", True, True)
    assert "".join(final_tokens).strip() == "stable"


@pytest.mark.asyncio
async def test_bad_wav_returns_a_result_without_stopping_valid_file(
    monkeypatch,
):
    bad = Path("bad.wav")
    good = Path("good.wav")

    def read_pcm_wav(path):
        if path == bad:
            raise ValueError("invalid WAV")
        return b"\0\0" * 320, 16_000, 1, 0.02

    monkeypatch.setattr(smoke, "_read_pcm_wav", read_pcm_wav)

    class FakeWebSocket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def send(self, _payload):
            return None

        async def recv(self):
            return json.dumps(
                {
                    "tokens": [
                        {"text": "valid", "is_final": True},
                        {"text": "<end>", "is_final": True},
                    ]
                }
            )

    monkeypatch.setattr(
        smoke.websockets,
        "connect",
        lambda *_args, **_kwargs: FakeWebSocket(),
    )
    results = [
        await smoke.transcribe_file(
            path,
            api_key="key",
            region="us",
            language_hints=["en"],
            chunk_ms=80,
            trailing_silence_ms=0,
            timeout_s=1,
        )
        for path in (bad, good)
    ]

    assert [result.path for result in results] == [str(bad), str(good)]
    assert results[0].ok is False
    assert results[0].transcript == ""
    assert results[0].duration_s == 0.0
    assert results[0].first_token_ms is None
    assert results[0].endpoint_after_eos_ms is None
    assert results[0].messages == 0
    assert results[0].bytes_sent == 0
    assert results[0].error_type == "ValueError"
    assert results[1].ok is True
    assert results[1].transcript == "valid"
