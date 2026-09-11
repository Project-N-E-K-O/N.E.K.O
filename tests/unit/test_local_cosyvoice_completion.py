import asyncio
import json
import queue
import threading
from types import SimpleNamespace

import pytest

from main_logic.tts_client._infra import TTS_SHUTDOWN_SENTINEL
from main_logic.tts_client.workers import local_cosyvoice as worker_module


class _ClosingSocket:
    def __init__(self, audio: bytes):
        self.audio = audio
        self.sent = []
        self._end = asyncio.Event()
        self._audio_sent = False
        self._has_text = False

    async def send(self, payload):
        data = json.loads(payload)
        self.sent.append(data)
        if data.get("text"):
            self._has_text = True
        if data.get("event") == "end":
            self._end.set()
            await asyncio.sleep(0)

    async def close(self):
        self._end.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self._end.wait()
        if not self._has_text:
            raise StopAsyncIteration
        if not self._audio_sent:
            self._audio_sent = True
            return self.audio
        raise StopAsyncIteration


@pytest.mark.unit
def test_local_cosyvoice_emits_completion_only_after_armed_normal_close(monkeypatch):
    sockets = []

    async def connect(_url, **_kwargs):
        socket = _ClosingSocket(b"\x00\x00")
        sockets.append(socket)
        return socket

    monkeypatch.setattr(
        worker_module,
        "get_config_manager",
        lambda: SimpleNamespace(
            get_model_api_config=lambda _name: {"base_url": "ws://127.0.0.1:8091"}
        ),
    )
    monkeypatch.setattr(worker_module.websockets, "connect", connect)
    monkeypatch.setattr(
        worker_module,
        "_resample_audio",
        lambda *_args, **kwargs: b"tail" if kwargs.get("last") else b"resampled",
    )
    monkeypatch.setattr(worker_module, "_record_tts_telemetry", lambda *_args: None)

    requests = queue.Queue()
    responses = queue.Queue()
    thread = threading.Thread(
        target=worker_module.local_cosyvoice_worker,
        args=(requests, responses, "", "voice"),
        daemon=True,
    )
    thread.start()
    try:
        assert responses.get(timeout=1) == ("__ready__", True)

        requests.put(("speech-1", "hello"))
        with pytest.raises(queue.Empty):
            responses.get(timeout=0.05)

        requests.put((None, None))
        observed = [responses.get(timeout=1), responses.get(timeout=1), responses.get(timeout=1)]
        assert observed == [
            ("__audio__", "speech-1", b"resampled"),
            ("__audio__", "speech-1", b"tail"),
            ("__audio_done__", "speech-1"),
        ]
        assert sockets[-1].sent[-1] == {"event": "end"}
    finally:
        requests.put((TTS_SHUTDOWN_SENTINEL, None))
        thread.join(timeout=1)
    assert thread.is_alive() is False
