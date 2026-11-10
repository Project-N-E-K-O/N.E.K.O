import base64
import io
import wave

import pytest

from utils.tts.providers.gemini import (
    GEMINI_TTS_SAMPLE_RATE,
    build_gemini_tts_payload,
    extract_gemini_tts_pcm,
)


def _response(audio: bytes) -> dict:
    return {
        "candidates": [{
            "content": {"parts": [{"inlineData": {"data": base64.b64encode(audio).decode()}}]}
        }]
    }


def test_payload_keeps_text_verbatim_and_requests_raw_pcm() -> None:
    payload = build_gemini_tts_payload("你好，今天过得怎么样？", "Leda")

    part = payload["contents"][0]["parts"][0]
    assert part["text"] == "你好，今天过得怎么样？"
    assert part["speechMetadata"]["style"]

    config = payload["generationConfig"]
    assert config["responseModalities"] == ["AUDIO"]
    assert config["responseFormat"]["audio"] == {
        "mimeType": "AUDIO_L16",
        "sampleRate": GEMINI_TTS_SAMPLE_RATE,
    }
    assert config["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"] == {"voiceName": "Leda"}


def test_extract_returns_raw_pcm_unchanged() -> None:
    pcm = b"\x01\x00\x02\x00\x03\x00"

    assert extract_gemini_tts_pcm(_response(pcm)) == pcm


def test_extract_strips_wav_header() -> None:
    pcm = b"\x01\x00\x02\x00\x03\x00"
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(GEMINI_TTS_SAMPLE_RATE)
        wav.writeframes(pcm)

    assert extract_gemini_tts_pcm(_response(buf.getvalue())) == pcm


@pytest.mark.parametrize(
    ("channels", "sample_width", "frame_rate"),
    [(2, 2, GEMINI_TTS_SAMPLE_RATE), (1, 1, GEMINI_TTS_SAMPLE_RATE), (1, 2, 16000)],
)
def test_extract_rejects_unexpected_wav_format(channels: int, sample_width: int, frame_rate: int) -> None:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(frame_rate)
        wav.writeframes(b"\x00" * channels * sample_width * 4)

    with pytest.raises(ValueError):
        extract_gemini_tts_pcm(_response(buf.getvalue()))


def test_extract_returns_none_without_audio() -> None:
    assert extract_gemini_tts_pcm({}) is None
    assert extract_gemini_tts_pcm({"candidates": [{"content": {"parts": []}}]}) is None
    assert extract_gemini_tts_pcm({"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}) is None
