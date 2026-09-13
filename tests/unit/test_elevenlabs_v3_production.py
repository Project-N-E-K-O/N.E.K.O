# Copyright 2025-2026 Project N.E.K.O. Team
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

import inspect
from types import SimpleNamespace

import numpy as np
import pytest

from main_logic.tts_client.workers import elevenlabs as elevenlabs_worker
from main_routers.characters_router import voice_providers
from utils.tts.providers.elevenlabs import ELEVENLABS_TTS_DEFAULT_MODEL


class _ConfigManager:
    def get_tts_api_key(self, provider: str) -> str:
        assert provider == "elevenlabs"
        return "test-key"


@pytest.mark.asyncio
async def test_preview_uses_v3_text_to_dialogue(monkeypatch):
    captured = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["request_kwargs"] = kwargs
            return SimpleNamespace(status_code=200, content=b"mp3", text="")

    monkeypatch.setattr(voice_providers.httpx, "AsyncClient", _FakeClient)

    audio, error = await voice_providers._elevenlabs_synthesize_preview(
        _ConfigManager(),
        "eleven:voice-123",
        "正式预览文本",
    )

    assert audio == b"mp3"
    assert error == ""
    assert captured["url"] == "https://api.elevenlabs.io/v1/text-to-dialogue"
    assert captured["request_kwargs"]["params"] == {"output_format": "mp3_44100_128"}
    assert captured["request_kwargs"]["json"] == {
        "inputs": [{"text": "正式预览文本", "voice_id": "voice-123"}],
        "model_id": "eleven_v3_conversational",
    }


def test_worker_uses_v3_text_to_dialogue_protocol():
    assert ELEVENLABS_TTS_DEFAULT_MODEL == "eleven_v3_conversational"
    assert elevenlabs_worker._elevenlabs_dialogue_ws_url(
        "https://api.elevenlabs.io",
        ELEVENLABS_TTS_DEFAULT_MODEL,
        "pcm_24000",
    ) == (
        "wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input"
        "?model_id=eleven_v3_conversational&output_format=pcm_24000"
    )
    assert elevenlabs_worker._elevenlabs_dialogue_init_payload("voice-123") == {
        "voices": ["voice-123"],
    }
    assert elevenlabs_worker._elevenlabs_dialogue_input_payload(
        "voice-123",
        "正式对话文本",
    ) == {
        "inputs": [{
            "text": "正式对话文本",
            "voice_id": "voice-123",
            "new_turn": False,
        }],
    }


def test_worker_classifies_v3_audio_turn_final_and_session_final_sequence():
    assert elevenlabs_worker._elevenlabs_dialogue_event_flags({"audio": "cGNt"}) == (
        False,
        False,
        False,
    )
    assert elevenlabs_worker._elevenlabs_dialogue_event_flags({
        "is_final_audio_for_turn": True,
    }) == (False, True, False)
    assert elevenlabs_worker._elevenlabs_dialogue_event_flags({
        "is_final": True,
    }) == (False, False, True)


def test_worker_drains_streaming_resampler_before_finishing(monkeypatch):
    captured = {}
    sentinel_resampler = object()

    def _fake_resample(audio, src_rate, dst_rate, resampler, *, last=False):
        captured.update({
            "audio": audio,
            "src_rate": src_rate,
            "dst_rate": dst_rate,
            "resampler": resampler,
            "last": last,
        })
        return b"tail-pcm"

    monkeypatch.setattr(elevenlabs_worker, "_resample_audio", _fake_resample)

    assert elevenlabs_worker._drain_elevenlabs_resampler(
        sentinel_resampler,
        24000,
    ) == b"tail-pcm"
    assert captured["audio"].dtype == np.int16
    assert captured["audio"].size == 0
    assert captured["src_rate"] == 24000
    assert captured["dst_rate"] == 48000
    assert captured["resampler"] is sentinel_resampler
    assert captured["last"] is True


def test_worker_enqueues_resampler_tail_before_final_jitter_flush_and_audio_done():
    source = inspect.getsource(elevenlabs_worker.elevenlabs_tts_worker)
    final_start = source.index("if is_final:")
    final_end = source.index("break", final_start)
    final_block = source[final_start:final_end]

    assert final_block.index("_flush_resampler_tail()") < final_block.index(
        "audio_jitter.flush()"
    )
    assert final_block.index("audio_jitter.flush()") < final_block.index(
        "audio_done.emit(speech_id)"
    )
