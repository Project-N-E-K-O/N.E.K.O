import base64
import json
import queue
import threading
import time
from functools import partial

import httpx
import numpy as np
import pytest

from main_logic import tts_client


class ControlledQueue:
    def __init__(self):
        self._queue = queue.Queue()
        self._stop = object()

    def put(self, item):
        self._queue.put(item)

    def get(self, timeout=None):
        item = self._queue.get(timeout=timeout)
        if item is self._stop:
            raise EOFError("queue closed")
        return item

    def close(self):
        self._queue.put(self._stop)


def _wait_for_queue_item(q, predicate, timeout=5.0):
    deadline = time.time() + timeout
    seen = []
    while time.time() < deadline:
        remaining = max(0.01, deadline - time.time())
        try:
            item = q.get(timeout=remaining)
        except queue.Empty:
            continue
        seen.append(item)
        if predicate(item):
            return item, seen
    raise AssertionError(f"Timed out waiting for queue item, seen={seen!r}")


class FakeMiMoTransport(httpx.AsyncBaseTransport):
    def __init__(self, audio_bytes: bytes, status_code: int = 200):
        self.audio_bytes = audio_bytes
        self.status_code = status_code
        self.requests = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append({
            "url": str(request.url),
            "headers": dict(request.headers),
            "body": body,
        })
        if self.status_code != 200:
            return httpx.Response(self.status_code, json={"error": "bad key"})
        event = {
            "choices": [
                {
                    "delta": {
                        "audio": {
                            "data": base64.b64encode(self.audio_bytes).decode("ascii")
                        }
                    }
                }
            ]
        }
        return httpx.Response(
            200,
            content=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n".encode("utf-8"),
            headers={"content-type": "text/event-stream"},
        )


def _start_mimo_worker(request_queue, response_queue, base_url="https://api.xiaomimimo.com/v1"):
    thread = threading.Thread(
        target=tts_client.mimo_tts_worker,
        args=(request_queue, response_queue, "test-mimo-key", "冰糖", base_url),
        daemon=True,
    )
    thread.start()
    return thread


@pytest.mark.unit
def test_get_mimo_chat_completions_url():
    assert (
        tts_client._get_mimo_chat_completions_url("https://api.xiaomimimo.com/v1")
        == "https://api.xiaomimimo.com/v1/chat/completions"
    )
    assert (
        tts_client._get_mimo_chat_completions_url("https://api.xiaomimimo.com")
        == "https://api.xiaomimimo.com/v1/chat/completions"
    )
    assert (
        tts_client._get_mimo_chat_completions_url("wss://api.xiaomimimo.com/v1")
        == "https://api.xiaomimimo.com/v1/chat/completions"
    )


@pytest.mark.unit
def test_extract_mimo_tts_audio_bytes_from_message_audio():
    pcm_bytes = (np.arange(256, dtype=np.int16)).tobytes()
    payload = {
        "choices": [
            {
                "message": {
                    "audio": {
                        "data": base64.b64encode(pcm_bytes).decode("ascii")
                    }
                }
            }
        ]
    }
    assert tts_client._extract_mimo_tts_audio_bytes(payload) == pcm_bytes


@pytest.mark.unit
def test_mimo_worker_sends_chat_completions_tts_request(monkeypatch):
    pcm_bytes = (np.arange(3000, dtype=np.int16)).tobytes()
    transport = FakeMiMoTransport(pcm_bytes)
    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_mimo_worker(request_queue, response_queue)

    _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))
    request_queue.put(("speech-1", "你好"))
    request_queue.put(("speech-1", "世界"))
    request_queue.put((None, None))

    audio_item, _ = _wait_for_queue_item(response_queue, lambda item: isinstance(item, bytes))
    assert len(audio_item) > 0

    assert len(transport.requests) == 1
    sent = transport.requests[0]
    assert sent["url"] == "https://api.xiaomimimo.com/v1/chat/completions"
    assert sent["headers"]["api-key"] == "test-mimo-key"
    assert sent["body"]["model"] == "mimo-v2.5-tts"
    assert "modalities" not in sent["body"]
    assert sent["body"]["audio"] == {"format": "pcm16", "voice": "冰糖"}
    assert sent["body"]["stream"] is True
    assert sent["body"]["messages"] == [{"role": "assistant", "content": "你好世界"}]

    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()


@pytest.mark.unit
def test_get_tts_worker_routes_assist_mimo_to_mimo_worker(monkeypatch):
    class _CM:
        def get_core_config(self):
            return {
                "assistApi": "mimo",
                "OPENROUTER_URL": "https://api.xiaomimimo.com/v1",
                "TTS_PROVIDER": "",
            }

        def get_model_api_config(self, model_type):
            return {"is_custom": False}

        def get_tts_api_key(self, provider):
            assert provider == "mimo"
            return "test-mimo-key"

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=False,
        voice_id="",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.mimo_tts_worker
    assert worker.keywords == {"base_url": "https://api.xiaomimimo.com/v1"}
    assert api_key == "test-mimo-key"
    assert provider_key == "mimo"


@pytest.mark.unit
def test_get_tts_worker_routes_tts_provider_mimo_to_default_endpoint(monkeypatch):
    class _CM:
        def get_core_config(self):
            return {
                "assistApi": "qwen",
                "OPENROUTER_URL": "https://api.xiaomimimo.com/v1",
                "TTS_PROVIDER": "mimo",
            }

        def get_model_api_config(self, model_type):
            return {"is_custom": False}

        def get_tts_api_key(self, provider):
            assert provider == "mimo"
            return "mimo-key-via-provider"

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=False,
        voice_id="",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.mimo_tts_worker
    assert worker.keywords == {"base_url": None}
    assert api_key == "mimo-key-via-provider"
    assert provider_key == "mimo"
