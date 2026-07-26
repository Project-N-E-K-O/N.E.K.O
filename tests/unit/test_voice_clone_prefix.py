import io
import re

import pytest

from utils.voice_clone import (
    MINIMAX_PREFIX_MAX_LENGTH,
    MinimaxVoiceCloneClient,
    sanitize_minimax_voice_prefix,
)
from utils.tts.providers.minimax import build_minimax_request_voice_id


def test_sanitize_minimax_voice_prefix_keeps_ascii_alnum_only():
    assert sanitize_minimax_voice_prefix("Rabbit01") == "Rabbit01"


def test_sanitize_minimax_voice_prefix_strips_invalid_chars_and_truncates():
    raw = "兔兔_rabbit-01XYZ"
    assert sanitize_minimax_voice_prefix(raw) == "rabbit01XY"[:MINIMAX_PREFIX_MAX_LENGTH]


def test_sanitize_minimax_voice_prefix_falls_back_when_empty():
    assert sanitize_minimax_voice_prefix("兔兔！！！") == "voice"


def test_build_minimax_request_voice_id_applies_only_minimax_constraints():
    original_prefix, voice_id = build_minimax_request_voice_id("兔兔_rabbit-01XYZ", "MiniMax")

    assert original_prefix == "兔兔_rabbit-01XYZ"
    assert re.fullmatch(r"rabbit01XY[0-9a-f]{8}", voice_id)


@pytest.mark.asyncio
async def test_clone_voice_builds_alnum_only_voice_id(monkeypatch):
    client = MinimaxVoiceCloneClient(api_key="test-key")
    captured = {}

    async def fake_upload_file(audio_buffer, filename):
        return "file-123"

    async def fake_create_voice(*, file_id, voice_id, voice_name, language, voice_description):
        captured["file_id"] = file_id
        captured["voice_id"] = voice_id
        captured["voice_name"] = voice_name
        captured["language"] = language
        captured["voice_description"] = voice_description
        return voice_id

    monkeypatch.setattr(client, "upload_file", fake_upload_file)
    monkeypatch.setattr(client, "create_voice", fake_create_voice)

    result = await client.clone_voice(
        audio_buffer=io.BytesIO(b"demo"),
        filename="sample.wav",
        prefix="兔兔_ab12",
        language="zh",
    )

    assert result == "customab12"
    assert captured["file_id"] == "file-123"
    assert captured["voice_id"] == "customab12"
    assert captured["voice_name"] == "ab12"
    assert captured["language"] == "zh"
