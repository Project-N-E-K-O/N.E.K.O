# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Local CosyVoice worker audio/source speech-id contract."""

import asyncio
import json
import queue
import threading
import time

import numpy as np
import pytest

from main_logic import tts_client
from main_logic.tts_client._infra import TTS_SHUTDOWN_SENTINEL


class _ControlledQueue:
    def __init__(self):
        self._queue = queue.Queue()

    def put(self, item):
        self._queue.put(item)

    def get(self):
        return self._queue.get(timeout=5.0)


def _wait_for_items(response_queue, predicate, count, timeout=5.0):
    deadline = time.time() + timeout
    matched = []
    seen = []
    while time.time() < deadline and len(matched) < count:
        try:
            item = response_queue.get(timeout=max(0.01, deadline - time.time()))
        except queue.Empty:
            continue
        seen.append(item)
        if predicate(item):
            matched.append(item)
    if len(matched) != count:
        raise AssertionError(f"Timed out waiting for {count} items, seen={seen!r}")
    return matched


class _FakeConfigManager:
    def get_model_api_config(self, _model_type):
        return {"base_url": "ws://127.0.0.1:9880"}


class _FakeWebSocket:
    def __init__(self, generation):
        self.generation = generation
        self._incoming = asyncio.Queue()
        self.closed = False

    async def send(self, message):
        payload = json.loads(message)
        if "voice" in payload and self.generation == 0:
            # The eager validation connection has no source speech_id. Even if
            # it unexpectedly emits PCM, the worker must not guess a turn.
            await self._incoming.put((np.arange(80, dtype=np.int16)).tobytes())
            await asyncio.sleep(0)
        elif "text" in payload:
            await self._incoming.put((np.arange(120, dtype=np.int16)).tobytes())
        elif payload.get("event") == "end" and self.generation > 0:
            # Let the old receive loop observe one final frame before the next
            # connection replaces it. Its captured source ID must stay old.
            await self._incoming.put((np.arange(40, dtype=np.int16)).tobytes())
            await asyncio.sleep(0)

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._incoming.get()

    async def close(self):
        self.closed = True


@pytest.mark.unit
def test_local_cosyvoice_audio_keeps_connection_source_id_across_rotation(monkeypatch):
    import main_logic.tts_client.workers.local_cosyvoice as local_cosyvoice_module

    monkeypatch.setattr(
        local_cosyvoice_module,
        "get_config_manager",
        lambda: _FakeConfigManager(),
    )
    sockets = []

    async def fake_connect(*_args, **_kwargs):
        ws = _FakeWebSocket(len(sockets))
        sockets.append(ws)
        return ws

    monkeypatch.setattr(local_cosyvoice_module.websockets, "connect", fake_connect)

    request_queue = _ControlledQueue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.local_cosyvoice_worker,
        args=(request_queue, response_queue, "", "中文女"),
        daemon=True,
    )
    thread.start()

    try:
        assert response_queue.get(timeout=5.0) == ("__ready__", True)
        # Give the initial source-less receive loop a chance to process its eager PCM.
        time.sleep(0.05)
        assert response_queue.empty()

        request_queue.put(("old-speech", "旧回合"))
        first_audio = _wait_for_items(
            response_queue,
            lambda item: isinstance(item, tuple) and item[0] == "__audio__",
            1,
        )
        assert first_audio[0][1] == "old-speech"
        assert isinstance(first_audio[0][2], bytes)

        request_queue.put(("new-speech", "新回合"))
        rotated_audio = _wait_for_items(
            response_queue,
            lambda item: isinstance(item, tuple) and item[0] == "__audio__",
            2,
        )
        assert [item[1] for item in rotated_audio] == ["old-speech", "new-speech"]
        assert all(isinstance(item[2], bytes) for item in rotated_audio)
    finally:
        request_queue.put((TTS_SHUTDOWN_SENTINEL, None))
        thread.join(timeout=5.0)
        assert not thread.is_alive()
