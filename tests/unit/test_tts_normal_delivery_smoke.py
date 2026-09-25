"""Positive delivery control using the real Qwen worker and runtime handler."""

import asyncio
import base64
import json

import pytest

from main_logic import tts_client
from main_logic.tts_client.workers import qwen
from tests.unit.session_handoff_harness import MemoryConfig, drain_manager, make_full_manager


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("retire_first", [False, True])
@pytest.mark.parametrize("ambient_custom", [False, True])
async def test_normal_owned_text_reaches_audio_with_real_qwen_worker(monkeypatch, retire_first, ambient_custom):
    # Model an unrelated config left in the process before this fixture starts.
    # The full manager and real worker dispatch must both use the fixture config.
    ambient = MemoryConfig()
    ambient.core["DISABLE_TTS"] = False
    if ambient_custom:
        ambient.core.update({
            "ENABLE_CUSTOM_API": True, "ttsModelProvider": "custom",
            "ttsModelUrl": "https://speech.example.invalid/v1",
            "ttsModelId": "vendor-tts", "ttsVoiceId": "vendor-voice",
        })
    ambient.load_json_config = lambda *args: dict(ambient.core)
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: ambient)
    manager, created, clients = await make_full_manager(monkeypatch)
    manager._config_manager.core["DISABLE_TTS"] = False
    sent_provider_text = []
    audio_delivered = asyncio.Event()
    audio_frames = []

    class ProviderSocket:
        def __init__(self):
            self.events = asyncio.Queue()
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            event = await self.events.get()
            if event is None:
                raise StopAsyncIteration
            return json.dumps(event)

        async def send(self, raw):
            message = json.loads(raw)
            if message["type"] == "session.update":
                self.events.put_nowait({"type": "session.updated"})
            elif message["type"] == "input_text_buffer.append":
                sent_provider_text.append(message["text"])
            elif message["type"] == "input_text_buffer.commit":
                # Enough actual 24 kHz PCM to exercise the real resampler and
                # jitter buffer, then flush the tail through the provider done.
                pcm = b"\x01\x00" * 24000
                self.events.put_nowait({
                    "type": "response.audio.delta",
                    "delta": base64.b64encode(pcm).decode("ascii"),
                })
                self.events.put_nowait({"type": "response.done"})

        async def close(self):
            if not self.closed:
                self.closed = True
                self.events.put_nowait(None)

    async def connect(*args, **kwargs):
        return ProviderSocket()

    async def send_bytes(data):
        audio_frames.append(data)
        audio_delivered.set()

    monkeypatch.setattr(qwen.websockets, "connect", connect)
    manager.websocket.send_bytes = send_bytes
    starting = asyncio.create_task(manager.start_session(
        manager.websocket, input_mode="audio", request_id="normal-control",
    ))
    try:
        client = await asyncio.wait_for(created.get(), 2)
        client.allow_connect.set()
        await asyncio.wait_for(starting, 2)
        # Same external TTS route used after native voice promotes to Offline.
        manager.use_tts = True
        await manager.ensure_tts_pipeline_alive()
        if retire_first:
            old = manager._snapshot_tts_runtime()
            manager._retire_tts_runtime(old)
            await manager.ensure_tts_pipeline_alive()
            assert manager._snapshot_tts_runtime() is not old
        async with asyncio.timeout(2):
            while not manager.tts_ready:
                await asyncio.sleep(0.005)
        assert manager._tts_active_provider_key == "qwen"
        await client.on_new_message()
        await client.on_text_delta("This is a normal audible response.", True)
        assert await manager._request_tts_done_for_turn("positive-control") == "queued"
        await asyncio.wait_for(audio_delivered.wait(), 2)
        assert "".join(sent_provider_text) == "This is a normal audible response."
        assert audio_frames and sum(map(len, audio_frames)) > 1000
        assert any(message.get("type") == "audio_chunk" for message in manager.websocket.messages)
    finally:
        await asyncio.wait_for(drain_manager(manager, clients, starting), 4)
        cleanups = tuple(getattr(manager, "_tts_cleanup_tasks", ()))
        if cleanups:
            await asyncio.wait_for(asyncio.gather(*cleanups), 3)
