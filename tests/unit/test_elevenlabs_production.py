# Copyright 2025-2026 Project N.E.K.O. Team
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

import inspect
import io
import json
import wave
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import UploadFile

from main_logic.tts_client.workers import elevenlabs as elevenlabs_worker
from main_routers.characters_router import voice_cloning, voice_providers
from utils.tts.providers.elevenlabs import ELEVENLABS_TTS_DEFAULT_MODEL


class _ConfigManager:
    def get_tts_api_key(self, provider: str) -> str:
        assert provider == "elevenlabs"
        return "test-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["upload", "direct"])
async def test_clone_is_normalized_registered_and_saved(monkeypatch, source):
    captured = {}

    class CloneConfig(_ConfigManager):
        async def aget_core_config(self):
            return {"enableCustomApi": False}

        async def aget_model_api_config(self, model_type, **kwargs):
            return {}

        def find_voice_by_audio_md5(self, storage_key, audio_md5, ref_language):
            return None

        def save_voice_for_api_key(self, storage_key, voice_id, metadata):
            captured["saved_voice_id"] = voice_id
            captured["metadata"] = metadata

    async def fake_clone(**kwargs):
        captured["clone_args"] = kwargs
        kwargs["audio_buffer"].seek(0)
        with wave.open(kwargs["audio_buffer"], "rb") as audio:
            assert audio.getnchannels() == 1
            assert audio.getsampwidth() == 2
            assert audio.getnframes() > 0
        return "eleven:created-123"

    sample = io.BytesIO()
    with wave.open(sample, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(b"\0\0" * 48000 * 8)
    sample.seek(0)
    monkeypatch.setattr(voice_cloning, "get_config_manager", lambda: CloneConfig())
    monkeypatch.setattr(voice_cloning, "_elevenlabs_clone_voice", fake_clone)

    if source == "upload":
        response = await voice_cloning.voice_clone(
            file=UploadFile(file=sample, filename="reference.wav"),
            prefix="V4Turbo", ref_language="ch", provider="elevenlabs", ref_text="",
        )
    else:
        direct_link = "https://example.com/reference.wav"

        async def request_json():
            return {
                "direct_link": direct_link, "prefix": "V4Turbo",
                "ref_language": "ch", "provider": "elevenlabs",
            }

        async def validate_link(url):
            assert url == direct_link

        async def close_response():
            pass

        async def request_link(method, url):
            assert method == "HEAD"
            assert url == direct_link
            return SimpleNamespace(status_code=200, aclose=close_response)

        async def download_audio(url, *, max_file_size):
            assert url == direct_link
            return "reference.wav", sample.getvalue()

        monkeypatch.setattr(voice_cloning, "_validate_direct_link_target", validate_link)
        monkeypatch.setattr(voice_cloning, "_request_direct_link_follow_redirects", request_link)
        monkeypatch.setattr(voice_cloning, "_download_direct_link_audio", download_audio)
        response = await voice_cloning.voice_clone_direct(SimpleNamespace(json=request_json))

    assert response.status_code == 200
    assert json.loads(response.body)["voice_id"] == "eleven:created-123"
    assert captured["clone_args"]["base_url"] == "https://api.elevenlabs.io"
    assert captured["clone_args"]["api_key"] == "test-key"
    assert captured["saved_voice_id"] == "eleven:created-123"
    assert captured["metadata"]["raw_voice_id"] == "created-123"
    assert captured["metadata"]["provider"] == "elevenlabs"
    if source == "upload":
        assert captured["metadata"]["source"] == "clone"
    else:
        assert captured["metadata"]["is_direct_link"] is True
        assert captured["metadata"]["direct_link"] == direct_link


@pytest.mark.asyncio
async def test_preview_uses_v4_text_to_dialogue(monkeypatch):
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
        "model_id": "eleven_v4",
    }


def test_worker_uses_v4_turbo_text_to_dialogue_protocol():
    assert ELEVENLABS_TTS_DEFAULT_MODEL == "eleven_v4_turbo"
    assert elevenlabs_worker._elevenlabs_dialogue_ws_url(
        "https://api.elevenlabs.io",
        ELEVENLABS_TTS_DEFAULT_MODEL,
        "pcm_24000",
    ) == (
        "wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input"
        "?model_id=eleven_v4_turbo&output_format=pcm_24000"
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


def test_worker_classifies_audio_turn_final_and_session_final_sequence():
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
