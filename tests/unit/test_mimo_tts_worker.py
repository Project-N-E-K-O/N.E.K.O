import asyncio
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
def test_get_tts_worker_routes_explicit_vllm_before_assist_mimo(monkeypatch):
    class _CM:
        def get_core_config(self):
            return {
                "assistApi": "mimo",
                "OPENROUTER_URL": "https://api.xiaomimimo.com/v1",
                "TTS_PROVIDER": "",
                "ENABLE_CUSTOM_API": True,
                "GPTSOVITS_ENABLED": False,
            }

        def load_json_config(self, filename, default):
            assert filename == "core_config.json"
            return {
                "ttsModelProvider": "vllm_omni",
                "ttsModelUrl": "http://localhost:8091",
                "ttsModelId": "Qwen3-TTS",
                "ttsVoiceId": "global-vllm-voice",
                "ttsModelApiKey": "",
            }

        def get_model_api_config(self, model_type):
            assert model_type == "tts_custom"
            return {"is_custom": False, "base_url": "https://fallback.invalid"}

        def get_tts_api_key(self, provider):
            pytest.fail("explicit vllm_omni should bypass assistApi=mimo fallback")

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=False,
        voice_id="",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.vllm_omni_tts_worker
    assert worker.keywords == {
        "base_url": "http://localhost:8091",
        "model": "Qwen3-TTS",
        "voice": "global-vllm-voice",
    }
    assert api_key == ""
    assert provider_key == "vllm_omni"


@pytest.mark.unit
def test_get_tts_worker_routes_explicit_vllm_before_cloned_voice(monkeypatch):
    """Explicit vllm_omni as default TTS + voice_meta is NOT a vllm_omni clone
    → should route to the preset path.

    The old test used pytest.fail to block _get_voice_meta calls, asserting
    that "voice_meta must not be accessed when vllm_omni is explicitly
    selected" (short-circuit contract).  That caused a bug: when the user
    both selected vllm_omni as default provider AND chose a vllm_omni clone
    voice, resolve skipped the clone check and took the preset path, sending
    the clone's internal ID (e.g. vllm-omni-clone-ch-xxx) as the voice
    parameter — the server rejected it as Invalid Voice.  Aligned with
    MiMo's _mimo_resolve: clone check always takes priority over preset,
    regardless of config_selected.  So resolve now accesses voice_meta, but
    when voice_meta is not a vllm_omni clone it still falls through to
    preset — the correct behaviour.
    """
    class _CM:
        def get_core_config(self):
            return {
                "assistApi": "qwen",
                "TTS_PROVIDER": "",
                "ENABLE_CUSTOM_API": True,
                "GPTSOVITS_ENABLED": False,
            }

        def load_json_config(self, filename, default):
            assert filename == "core_config.json"
            return {
                "ttsModelProvider": "vllm_omni",
                "ttsModelUrl": "http://localhost:8091",
                "ttsModelId": "Qwen3-TTS",
                "ttsVoiceId": "global-vllm-voice",
                "ttsModelApiKey": "vllm-key",
            }

        def get_model_api_config(self, model_type):
            assert model_type == "tts_custom"
            return {"is_custom": False, "base_url": "https://fallback.invalid"}

        def get_tts_api_key(self, provider):
            pytest.fail("explicit vllm_omni should bypass cloned voice providers")

    # voice_meta 返回 None（非 vllm_omni 克隆）→ resolve 检查后走 preset 路径。
    # 不再用 pytest.fail 拦截：resolve 必须检查 voice_meta 以区分 clone vs preset，
    # 与 MiMo 的 _mimo_resolve 对齐（clone 优先于 config-selected preset）。
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())
    monkeypatch.setattr(
        tts_client,
        "_get_voice_meta",
        lambda voice_id: None,
    )

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=True,
        voice_id="cloned-voice",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.vllm_omni_tts_worker
    assert worker.keywords == {
        "base_url": "http://localhost:8091",
        "model": "Qwen3-TTS",
        "voice": "global-vllm-voice",
    }
    assert api_key == "vllm-key"
    assert provider_key == "vllm_omni"


@pytest.mark.unit
def test_get_tts_worker_routes_vllm_clone_when_config_selected(monkeypatch):
    """Explicit vllm_omni as default TTS + vllm_omni clone voice selected
    → should route to the clone path.

    Bug-fix: previously the config_selected=True guard caused resolve to skip
    the clone check, sending the clone's internal ID (e.g. vllm-omni-clone-ch-xxx)
    as the voice parameter to the vLLM-Omni server, which rejected it as
    Invalid Voice.  Aligned with MiMo's _mimo_resolve: clone takes priority
    over preset, not blocked by the config_selected guard.
    """
    CLONE_B64 = "UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA="  # 1-byte WAV

    class _CM:
        def get_core_config(self):
            return {
                "assistApi": "qwen",
                "TTS_PROVIDER": "",
                "ENABLE_CUSTOM_API": True,
                "GPTSOVITS_ENABLED": False,
            }

        def load_json_config(self, filename, default):
            assert filename == "core_config.json"
            return {
                "ttsModelProvider": "vllm_omni",
                "ttsModelUrl": "http://localhost:8091",
                "ttsModelId": "Qwen3-TTS",
                "ttsVoiceId": "global-vllm-voice",
                "ttsModelApiKey": "vllm-key",
            }

        def get_model_api_config(self, model_type):
            assert model_type == "tts_custom"
            return {"is_custom": False, "base_url": "https://fallback.invalid"}

        def get_tts_api_key(self, provider):
            pytest.fail("vllm_omni clone should not query tts api key")

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())
    # voice_meta 返回 vllm_omni 克隆音色 → resolve 应走 clone 路径
    monkeypatch.setattr(
        tts_client,
        "_get_voice_meta",
        lambda voice_id: {
            "provider": "vllm_omni",
            "clone_sample_b64": CLONE_B64,
            "clone_sample_mime": "audio/wav",
            "clone_ref_text": "你好",
        },
    )

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=True,
        voice_id="vllm-omni-clone-ch-abc123",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.vllm_omni_tts_worker
    # clone 路径：voice='default'（不传克隆 ID），ref_audio 带 data URI，ref_text 有值
    assert worker.keywords.get("voice") == "default"
    assert worker.keywords.get("ref_audio") == f"data:audio/wav;base64,{CLONE_B64}"
    assert worker.keywords.get("ref_text") == "你好"
    # clone 路径读取 ttsModelApiKey 用于 WS 鉴权（与 preset 路径一致，见 _vllm_omni_clone_resolve L688）
    assert api_key == "vllm-key"
    assert provider_key == "vllm_omni"
    class _CM:
        def get_core_config(self):
            return {
                "assistApi": "qwen",
                "TTS_PROVIDER": "",
                "ENABLE_CUSTOM_API": True,
                "GPTSOVITS_ENABLED": False,
                "TTS_MODEL": "qwen3-tts-flash-realtime-2025-11-27",
                "TTS_MODEL_API_KEY": "assist-key",
                "TTS_VOICE_ID": "assistant-voice",
            }

        def load_json_config(self, filename, default):
            assert filename == "core_config.json"
            return {
                "ttsModelProvider": "vllm_omni",
                "ttsModelUrl": "",
                "ttsModelId": "",
                "ttsVoiceId": "",
            }

        def get_model_api_config(self, model_type):
            assert model_type == "tts_custom"
            return {
                "is_custom": False,
                "base_url": "https://assist.invalid/v1",
                "api_key": "custom-key",
            }

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=False,
        voice_id="",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.vllm_omni_tts_worker
    assert worker.keywords == {
        "base_url": tts_client.VLLM_OMNI_DEFAULT_BASE_URL,
        "model": tts_client.VLLM_OMNI_DEFAULT_MODEL,
        "voice": "default",
    }
    assert api_key == ""
    assert provider_key == "vllm_omni"


@pytest.mark.unit
def test_get_tts_worker_ignores_stale_vllm_when_custom_api_disabled(monkeypatch):
    class _CM:
        def get_core_config(self):
            return {
                "assistApi": "qwen",
                "TTS_PROVIDER": "",
                "ENABLE_CUSTOM_API": False,
                "GPTSOVITS_ENABLED": False,
            }

        def load_json_config(self, filename, default):
            assert filename == "core_config.json"
            return {
                "ttsModelProvider": "vllm_omni",
                "ttsModelUrl": "http://stale-vllm.local",
                "ttsModelId": "Qwen3-TTS",
                "ttsVoiceId": "stale-voice",
            }

        def get_model_api_config(self, model_type):
            assert model_type == "tts_custom"
            return {"is_custom": False, "base_url": "https://fallback.invalid"}

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())

    worker, _, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=False,
        voice_id="",
    )

    assert provider_key != "vllm_omni"
    assert not (isinstance(worker, partial) and worker.func is tts_client.vllm_omni_tts_worker)


@pytest.mark.unit
def test_get_tts_worker_ignores_stale_vllm_when_custom_api_string_false(monkeypatch):
    class _CM:
        def get_core_config(self):
            return {
                "assistApi": "qwen",
                "TTS_PROVIDER": "",
                "ENABLE_CUSTOM_API": "false",
                "GPTSOVITS_ENABLED": False,
            }

        def load_json_config(self, filename, default):
            assert filename == "core_config.json"
            return {
                "ttsModelProvider": "vllm_omni",
                "ttsModelUrl": "http://stale-vllm.local",
                "ttsModelId": "Qwen3-TTS",
                "ttsVoiceId": "stale-voice",
            }

        def get_model_api_config(self, model_type):
            assert model_type == "tts_custom"
            return {"is_custom": False, "base_url": "https://fallback.invalid"}

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())

    worker, _, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=False,
        voice_id="",
    )

    assert provider_key != "vllm_omni"
    assert not (isinstance(worker, partial) and worker.func is tts_client.vllm_omni_tts_worker)


@pytest.mark.unit
def test_get_tts_worker_keeps_gptsovits_ahead_of_explicit_vllm(monkeypatch):
    class _CM:
        def get_core_config(self):
            return {
                "assistApi": "qwen",
                "TTS_PROVIDER": "",
                "GPTSOVITS_ENABLED": True,
            }

        def load_json_config(self, filename, default):
            assert filename == "core_config.json"
            return {"ttsModelProvider": "vllm_omni"}

        def get_model_api_config(self, model_type):
            assert model_type == "tts_custom"
            return {"is_custom": True, "base_url": "http://gsv.local"}

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=False,
        voice_id="",
    )

    assert worker is tts_client.gptsovits_tts_worker
    assert api_key is None
    assert provider_key == "gptsovits"


@pytest.mark.unit
def test_vllm_omni_worker_prefers_character_voice_over_provider_fallback(monkeypatch):
    sent_messages = []

    class _FakeWS:
        async def send(self, payload):
            sent_messages.append(json.loads(payload))

        async def close(self):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(60)
            raise StopAsyncIteration

    async def _connect(*args, **kwargs):
        return _FakeWS()

    monkeypatch.setattr(tts_client.websockets, "connect", _connect)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.vllm_omni_tts_worker,
        kwargs={
            "request_queue": request_queue,
            "response_queue": response_queue,
            "audio_api_key": "",
            "voice_id": "character-voice",
            "base_url": "http://localhost:8091",
            "model": "Qwen3-TTS",
            "voice": "global-default",
        },
    )
    thread.start()

    assert response_queue.get(timeout=3.0) == ("__ready__", True)
    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()

    assert sent_messages[0]["type"] == "session.config"
    assert sent_messages[0]["voice"] == "character-voice"


@pytest.mark.unit
def test_vllm_omni_worker_marks_not_ready_on_server_error_event(monkeypatch):
    class _FakeWS:
        def __init__(self):
            self._sent_error = False

        async def send(self, payload):
            pass

        async def close(self):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._sent_error:
                self._sent_error = True
                return json.dumps({
                    "type": "error",
                    "code": "BAD_MODEL",
                    "message": "invalid model",
                })
            await asyncio.sleep(60)
            raise StopAsyncIteration

    async def _connect(*args, **kwargs):
        return _FakeWS()

    monkeypatch.setattr(tts_client.websockets, "connect", _connect)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.vllm_omni_tts_worker,
        kwargs={
            "request_queue": request_queue,
            "response_queue": response_queue,
            "audio_api_key": "",
            "voice_id": "",
            "base_url": "http://localhost:8091",
            "model": "bad-model",
            "voice": "global-default",
        },
    )
    thread.start()

    _, seen = _wait_for_queue_item(
        response_queue,
        lambda item: item == ("__ready__", False),
        timeout=3.0,
    )
    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()

    assert ("__ready__", True) in seen
    assert any(
        isinstance(item, tuple)
        and item[0] == "__error__"
        and "BAD_MODEL" in str(item[1])
        for item in seen
    )


@pytest.mark.unit
def test_vllm_omni_worker_marks_not_ready_when_socket_closes_before_session_done(monkeypatch):
    class _FakeWS:
        def __init__(self):
            self.done_sent = False

        async def send(self, payload):
            event = json.loads(payload)
            if event.get("type") == "input.done":
                self.done_sent = True

        async def close(self):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            while not self.done_sent:
                await asyncio.sleep(0.01)
            raise tts_client.websockets.exceptions.ConnectionClosedError(None, None)

    async def _connect(*args, **kwargs):
        return _FakeWS()

    monkeypatch.setattr(tts_client.websockets, "connect", _connect)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.vllm_omni_tts_worker,
        kwargs={
            "request_queue": request_queue,
            "response_queue": response_queue,
            "audio_api_key": "",
            "voice_id": "",
            "base_url": "http://localhost:8091",
            "model": "Qwen3-TTS",
            "voice": "global-default",
        },
    )
    thread.start()

    assert response_queue.get(timeout=3.0) == ("__ready__", True)
    request_queue.put(("sid-a", "hello"))
    request_queue.put((None, None))
    _, seen = _wait_for_queue_item(
        response_queue,
        lambda item: item == ("__ready__", False),
        timeout=3.0,
    )
    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()

    assert any(
        isinstance(item, tuple)
        and item[0] == "__error__"
        and "TTS_CONNECTION_FAILED" in str(item[1])
        for item in seen
    )


@pytest.mark.unit
def test_vllm_omni_worker_reports_initial_connection_failure(monkeypatch):
    async def _connect(*args, **kwargs):
        raise OSError("server unavailable")

    monkeypatch.setattr(tts_client.websockets, "connect", _connect)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.vllm_omni_tts_worker,
        kwargs={
            "request_queue": request_queue,
            "response_queue": response_queue,
            "audio_api_key": "",
            "voice_id": "",
            "base_url": "http://localhost:8091",
            "model": "Qwen3-TTS",
            "voice": "global-default",
        },
    )
    thread.start()

    _, seen = _wait_for_queue_item(
        response_queue,
        lambda item: item == ("__ready__", False),
        timeout=3.0,
    )
    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()

    assert any(
        isinstance(item, tuple)
        and item[0] == "__error__"
        and "TTS_CONNECTION_FAILED" in str(item[1])
        for item in seen
    )


@pytest.mark.unit
def test_vllm_omni_worker_rebuilds_when_sid_changes_after_flush(monkeypatch):
    connections = []

    class _FakeWS:
        def __init__(self):
            self.messages = []

        async def send(self, payload):
            self.messages.append(json.loads(payload))

        async def close(self):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(60)
            raise StopAsyncIteration

    async def _connect(*args, **kwargs):
        ws = _FakeWS()
        connections.append(ws)
        return ws

    def _wait_until(predicate, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError("condition was not reached")

    monkeypatch.setattr(tts_client.websockets, "connect", _connect)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.vllm_omni_tts_worker,
        kwargs={
            "request_queue": request_queue,
            "response_queue": response_queue,
            "audio_api_key": "",
            "voice_id": "",
            "base_url": "http://localhost:8091",
            "model": "Qwen3-TTS",
            "voice": "global-default",
        },
    )
    thread.start()

    assert response_queue.get(timeout=3.0) == ("__ready__", True)
    request_queue.put(("sid-a", "hello"))
    _wait_until(lambda: len(connections[0].messages) >= 2)
    request_queue.put((None, None))
    _wait_until(lambda: any(msg.get("type") == "input.done" for msg in connections[0].messages))
    request_queue.put(("sid-b", "world"))
    _wait_until(lambda: len(connections) >= 2 and len(connections[1].messages) >= 2)
    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()

    assert [msg["type"] for msg in connections[0].messages] == [
        "session.config",
        "input.text",
        "input.done",
    ]
    assert connections[0].messages[1]["text"] == "hello"
    assert [msg["type"] for msg in connections[1].messages[:2]] == [
        "session.config",
        "input.text",
    ]
    assert connections[1].messages[1]["text"] == "world"


@pytest.mark.unit
def test_vllm_omni_worker_rebuilds_when_sid_changes_before_flush(monkeypatch):
    connections = []

    class _FakeWS:
        def __init__(self):
            self.messages = []

        async def send(self, payload):
            self.messages.append(json.loads(payload))

        async def close(self):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(60)
            raise StopAsyncIteration

    async def _connect(*args, **kwargs):
        ws = _FakeWS()
        connections.append(ws)
        return ws

    def _wait_until(predicate, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError("condition was not reached")

    monkeypatch.setattr(tts_client.websockets, "connect", _connect)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.vllm_omni_tts_worker,
        kwargs={
            "request_queue": request_queue,
            "response_queue": response_queue,
            "audio_api_key": "",
            "voice_id": "",
            "base_url": "http://localhost:8091",
            "model": "Qwen3-TTS",
            "voice": "global-default",
        },
    )
    thread.start()

    assert response_queue.get(timeout=3.0) == ("__ready__", True)
    request_queue.put(("sid-a", "hello"))
    _wait_until(lambda: len(connections[0].messages) >= 2)
    request_queue.put(("sid-b", "world"))
    _wait_until(lambda: len(connections) >= 2 and len(connections[1].messages) >= 2)
    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()

    assert [msg["type"] for msg in connections[0].messages] == [
        "session.config",
        "input.text",
    ]
    assert connections[0].messages[1]["text"] == "hello"
    assert [msg["type"] for msg in connections[1].messages[:2]] == [
        "session.config",
        "input.text",
    ]
    assert connections[1].messages[1]["text"] == "world"


@pytest.mark.unit
def test_vllm_omni_worker_replays_pending_text_after_same_sid_reconnect(monkeypatch):
    connections = []

    class _FakeWS:
        def __init__(self):
            self.messages = []
            self._drop_after_text = asyncio.Event()
            self.failed = False

        async def send(self, payload):
            event = json.loads(payload)
            self.messages.append(event)
            if len(connections) == 1 and event.get("type") == "input.text":
                self._drop_after_text.set()

        async def close(self):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            await self._drop_after_text.wait()
            await asyncio.sleep(0)
            self.failed = True
            raise RuntimeError("socket dropped mid-stream")

    async def _connect(*args, **kwargs):
        ws = _FakeWS()
        connections.append(ws)
        return ws

    def _wait_until(predicate, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError("condition was not reached")

    monkeypatch.setattr(tts_client.websockets, "connect", _connect)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.vllm_omni_tts_worker,
        kwargs={
            "request_queue": request_queue,
            "response_queue": response_queue,
            "audio_api_key": "",
            "voice_id": "",
            "base_url": "http://localhost:8091",
            "model": "Qwen3-TTS",
            "voice": "global-default",
        },
    )
    thread.start()

    assert response_queue.get(timeout=3.0) == ("__ready__", True)
    request_queue.put(("sid-a", "hello"))
    _wait_until(lambda: connections and connections[0].failed)
    request_queue.put(("sid-a", " world"))
    _wait_until(lambda: len(connections) >= 2 and len(connections[1].messages) >= 3)
    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()

    assert [msg["type"] for msg in connections[1].messages[:3]] == [
        "session.config",
        "input.text",
        "input.text",
    ]
    assert connections[1].messages[1]["text"] == "hello"
    assert connections[1].messages[2]["text"] == " world"


@pytest.mark.unit
def test_vllm_omni_worker_marks_not_ready_when_reconnect_fails(monkeypatch):
    calls = {"connect": 0}

    class _FakeWS:
        async def send(self, payload):
            pass

        async def close(self):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(60)
            raise StopAsyncIteration

    async def _connect(*args, **kwargs):
        calls["connect"] += 1
        if calls["connect"] > 1:
            raise OSError("server unavailable")
        return _FakeWS()

    monkeypatch.setattr(tts_client.websockets, "connect", _connect)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.vllm_omni_tts_worker,
        kwargs={
            "request_queue": request_queue,
            "response_queue": response_queue,
            "audio_api_key": "",
            "voice_id": "",
            "base_url": "http://localhost:8091",
            "model": "Qwen3-TTS",
            "voice": "global-default",
        },
    )
    thread.start()

    assert response_queue.get(timeout=3.0) == ("__ready__", True)
    request_queue.put(("__interrupt__", None))
    request_queue.put(("sid-a", "hello"))
    _, seen = _wait_for_queue_item(
        response_queue,
        lambda item: item == ("__ready__", False),
        timeout=3.0,
    )
    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()

    assert any(
        isinstance(item, tuple)
        and item[0] == "__error__"
        and "TTS_CONNECTION_FAILED" in str(item[1])
        for item in seen
    )


@pytest.mark.unit
def test_vllm_omni_worker_marks_not_ready_when_flush_reconnect_fails(monkeypatch):
    calls = {"connect": 0}
    sent_messages = []

    class _FakeWS:
        async def send(self, payload):
            event = json.loads(payload)
            sent_messages.append(event)
            if event.get("type") == "input.done":
                raise OSError("flush failed")

        async def close(self):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(60)
            raise StopAsyncIteration

    async def _connect(*args, **kwargs):
        calls["connect"] += 1
        if calls["connect"] > 1:
            raise OSError("server unavailable")
        return _FakeWS()

    def _wait_until(predicate, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError("condition was not reached")

    monkeypatch.setattr(tts_client.websockets, "connect", _connect)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.vllm_omni_tts_worker,
        kwargs={
            "request_queue": request_queue,
            "response_queue": response_queue,
            "audio_api_key": "",
            "voice_id": "",
            "base_url": "http://localhost:8091",
            "model": "Qwen3-TTS",
            "voice": "global-default",
        },
    )
    thread.start()

    assert response_queue.get(timeout=3.0) == ("__ready__", True)
    request_queue.put(("sid-a", "hello"))
    _wait_until(lambda: any(item.get("type") == "input.text" for item in sent_messages))
    request_queue.put((None, None))
    _, seen = _wait_for_queue_item(
        response_queue,
        lambda item: item == ("__ready__", False),
        timeout=3.0,
    )
    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()

    assert calls["connect"] == 2
    assert [item["type"] for item in sent_messages] == [
        "session.config",
        "input.text",
        "input.done",
    ]
    assert any(
        isinstance(item, tuple)
        and item[0] == "__error__"
        and "TTS_CONNECTION_FAILED" in str(item[1])
        for item in seen
    )


@pytest.mark.unit
def test_vllm_omni_worker_marks_not_ready_when_pending_replay_fails(monkeypatch):
    connections = []

    class _FakeWS:
        def __init__(self, index):
            self.index = index
            self.messages = []

        async def send(self, payload):
            event = json.loads(payload)
            self.messages.append(event)
            if (
                self.index == 0
                and event.get("type") == "input.text"
                and event.get("text") == "new"
            ):
                raise OSError("send current failed")
            if (
                self.index == 1
                and event.get("type") == "input.text"
                and event.get("text") == "old"
            ):
                raise OSError("replay failed")

        async def close(self):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(60)
            raise StopAsyncIteration

    async def _connect(*args, **kwargs):
        ws = _FakeWS(len(connections))
        connections.append(ws)
        return ws

    def _wait_until(predicate, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError("condition was not reached")

    monkeypatch.setattr(tts_client.websockets, "connect", _connect)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.vllm_omni_tts_worker,
        kwargs={
            "request_queue": request_queue,
            "response_queue": response_queue,
            "audio_api_key": "",
            "voice_id": "",
            "base_url": "http://localhost:8091",
            "model": "Qwen3-TTS",
            "voice": "global-default",
        },
    )
    thread.start()

    assert response_queue.get(timeout=3.0) == ("__ready__", True)
    request_queue.put(("sid-a", "old"))
    _wait_until(lambda: len(connections[0].messages) >= 2)
    request_queue.put(("sid-a", "new"))
    _, seen = _wait_for_queue_item(
        response_queue,
        lambda item: item == ("__ready__", False),
        timeout=3.0,
    )
    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()

    assert [item["type"] for item in connections[0].messages] == [
        "session.config",
        "input.text",
        "input.text",
    ]
    assert connections[0].messages[1]["text"] == "old"
    assert connections[0].messages[2]["text"] == "new"
    assert [item["type"] for item in connections[1].messages] == [
        "session.config",
        "input.text",
    ]
    assert connections[1].messages[1]["text"] == "old"
    assert any(
        isinstance(item, tuple)
        and item[0] == "__error__"
        and "TTS_CONNECTION_FAILED" in str(item[1])
        for item in seen
    )


@pytest.mark.unit
def test_get_tts_worker_routes_mimo_before_core_native_voice(monkeypatch):
    class _CM:
        def get_core_config(self):
            return {
                "assistApi": "mimo",
                "OPENROUTER_URL": "https://api.xiaomimimo.com/v1",
                "TTS_PROVIDER": "",
                "CORE_API_KEY": "gemini-core-key",
                "GPTSOVITS_ENABLED": False,
            }

        def get_model_api_config(self, model_type):
            return {"is_custom": False, "api_key": "tts-default-key"}

        def get_tts_api_key(self, provider):
            assert provider == "mimo"
            return "mimo-key-over-native"

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())
    monkeypatch.setattr(
        tts_client,
        "get_native_tts_worker",
        lambda *args, **kwargs: pytest.fail(
            "assistApi=mimo should not enter the core native voice branch"
        ),
    )

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="gemini",
        has_custom_voice=False,
        voice_id="any-native-voice",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.mimo_tts_worker
    assert worker.keywords == {"base_url": "https://api.xiaomimimo.com/v1"}
    assert api_key == "mimo-key-over-native"
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


@pytest.mark.unit
def test_get_tts_worker_does_not_fallback_to_core_key_when_mimo_key_missing(monkeypatch):
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
            return None

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=False,
        voice_id="",
    )

    assert worker is tts_client.dummy_tts_worker
    assert api_key is None
    assert provider_key is None


@pytest.mark.unit
def test_get_tts_worker_routes_mimo_before_custom_voice_fallback(monkeypatch):
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
            return "mimo-key-with-voice"

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())
    monkeypatch.setattr(tts_client, "_get_voice_meta", lambda voice_id: None)

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=True,
        voice_id="Milo",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.mimo_tts_worker
    assert worker.keywords == {"base_url": None}
    assert api_key == "mimo-key-with-voice"
    assert provider_key == "mimo"
