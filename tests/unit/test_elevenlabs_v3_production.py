# Copyright 2025-2026 Project N.E.K.O. Team
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

from types import SimpleNamespace

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
