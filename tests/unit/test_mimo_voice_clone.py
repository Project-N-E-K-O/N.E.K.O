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

"""MiMo voiceclone enrollment + dispatch (dual to cosyvoice/minimax).

MiMo has no remote cloned voice id: the reference sample is persisted locally and
inlined per synthesis request via ``audio.voice`` against the voiceclone model.
"""

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
from utils.config_manager import get_config_manager


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
        self.requests.append({"url": str(request.url), "body": body})
        if self.status_code != 200:
            return httpx.Response(self.status_code, json={"error": "bad"})
        event = {"choices": [{"delta": {"audio": {"data": base64.b64encode(self.audio_bytes).decode("ascii")}}}]}
        return httpx.Response(
            200,
            content=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n".encode("utf-8"),
            headers={"content-type": "text/event-stream"},
        )


# ── worker: clone uses the voiceclone model + inlines the reference data URI ──

@pytest.mark.unit
def test_mimo_worker_clone_uses_voiceclone_model_and_inlines_sample(monkeypatch):
    pcm_bytes = (np.arange(3000, dtype=np.int16)).tobytes()
    transport = FakeMiMoTransport(pcm_bytes)
    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    clone_uri = "data:audio/wav;base64,QUJDRA=="
    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.mimo_tts_worker,
        kwargs={
            "request_queue": request_queue,
            "response_queue": response_queue,
            "audio_api_key": "mimo-key",
            "voice_id": "mimo-clone-abc",  # local id, ignored in clone mode
            "base_url": None,
            "clone_voice": clone_uri,
        },
        daemon=True,
    )
    thread.start()

    _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))
    request_queue.put(("speech-1", "你好"))
    request_queue.put((None, None))
    _wait_for_queue_item(response_queue, lambda item: isinstance(item, bytes))

    assert len(transport.requests) == 1
    body = transport.requests[0]["body"]
    assert body["model"] == "mimo-v2.5-tts-voiceclone"
    assert body["audio"] == {"format": "pcm16", "voice": clone_uri}

    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()


@pytest.mark.unit
def test_mimo_worker_design_preserves_text_without_persona_rewrite(monkeypatch):
    """MiMo Voice Design must not let the voice description rewrite dialogue.

    The design prompt is an acoustic/style descriptor only. Runtime TTS must
    send the character's assistant text exactly and must not enable MiMo's text
    preview optimization, otherwise voice style can leak into persona/content.
    """
    pcm_bytes = (np.arange(3000, dtype=np.int16)).tobytes()
    transport = FakeMiMoTransport(pcm_bytes)
    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.mimo_tts_worker,
        kwargs={
            "request_queue": request_queue,
            "response_queue": response_queue,
            "audio_api_key": "mimo-key",
            "voice_id": "mimo-design-abc",
            "base_url": None,
            "design_prompt": "an energetic sports girl voice",
        },
        daemon=True,
    )
    thread.start()

    _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))
    request_queue.put(("speech-1", "今天我们继续聊项目。"))
    request_queue.put((None, None))
    _wait_for_queue_item(response_queue, lambda item: isinstance(item, bytes))

    assert len(transport.requests) == 1
    body = transport.requests[0]["body"]
    assert body["model"] == "mimo-v2.5-tts-voicedesign"
    assert body["audio"] == {"format": "pcm16"}
    assert "optimize_text_preview" not in body
    assert body["messages"] == [
        {"role": "user", "content": "an energetic sports girl voice"},
        {"role": "assistant", "content": "今天我们继续聊项目。"},
    ]

    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()


# ── dispatch: a MiMo clone voice routes to the voiceclone worker ──────────────

def _mimo_clone_meta(sample: bytes, **extra):
    """A MiMo clone voice_meta with the reference sample stored as base64 (the
    B-storage model: clone identity lives entirely in voice_storage.json, dual to
    MiniMax's remote voice_id)."""
    meta = {
        "provider": "mimo",
        "source": "clone",
        "clone_sample_b64": base64.b64encode(sample).decode("ascii"),
        "clone_sample_mime": "audio/wav",
    }
    meta.update(extra)
    return meta


@pytest.mark.unit
def test_get_tts_worker_routes_mimo_clone_voice(monkeypatch):
    sample = (np.arange(256, dtype=np.int16)).tobytes()

    class _CM:
        def get_core_config(self):
            return {"assistApi": "qwen", "TTS_PROVIDER": "", "GPTSOVITS_ENABLED": False}

        def get_model_api_config(self, model_type):
            return {"is_custom": False}

        def get_tts_api_key(self, provider):
            assert provider == "mimo"
            return "mimo-key"

        def get_voices_for_current_api(self, for_listing=False):
            return {"mimo-clone-abc": _mimo_clone_meta(sample)}

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen", has_custom_voice=True, voice_id="mimo-clone-abc",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.mimo_tts_worker
    assert provider_key == "mimo"
    assert api_key == "mimo-key"
    assert worker.keywords["base_url"] is None
    # the stored value is already base64 → the data URI just frames it (no re-encode)
    expected_uri = "data:audio/wav;base64," + base64.b64encode(sample).decode("ascii")
    assert worker.keywords["clone_voice"] == expected_uri


@pytest.mark.unit
def test_get_tts_worker_routes_mimo_design_voice(monkeypatch):
    class _CM:
        def get_core_config(self):
            return {"assistApi": "qwen", "TTS_PROVIDER": "", "GPTSOVITS_ENABLED": False}

        def get_model_api_config(self, model_type):
            return {"is_custom": False}

        def get_tts_api_key(self, provider):
            assert provider == "mimo"
            return "mimo-key"

        def get_voices_for_current_api(self, for_listing=False):
            return {
                "mimo-design-aria-1234": {
                    "provider": "mimo",
                    "source": "design",
                    "design_prompt": "a bright energetic anime girl voice",
                    "mimo_base_url": "https://custom.mimo.example/v1",
                }
            }

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen", has_custom_voice=True, voice_id="mimo-design-aria-1234",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.mimo_tts_worker
    assert provider_key == "mimo"
    assert api_key == "mimo-key"
    assert worker.keywords["base_url"] == "https://custom.mimo.example/v1"
    assert worker.keywords["design_prompt"] == "a bright energetic anime girl voice"
    assert "clone_voice" not in worker.keywords


@pytest.mark.unit
def test_mimo_design_payload_preserves_assistant_text_without_text_optimization():
    from utils.tts.providers.mimo import MIMO_TTS_VOICEDESIGN_MODEL
    from utils.voice_clone import MimoVoiceCloneClient

    payload = MimoVoiceCloneClient(api_key="mimo-key")._build_design_payload(
        "a bright energetic anime girl voice",
        "hello there",
    )

    assert payload["model"] == MIMO_TTS_VOICEDESIGN_MODEL
    assert payload["audio"]["format"] == "wav"
    assert "optimize_text_preview" not in payload["audio"]
    assert "optimize_text_preview" not in payload
    assert payload["messages"] == [
        {"role": "user", "content": "a bright energetic anime girl voice"},
        {"role": "assistant", "content": "hello there"},
    ]


@pytest.mark.unit
def test_get_tts_worker_mimo_clone_uses_token_plan_base_url(monkeypatch):
    """When MiMo Token Plan is active (assistApi=mimo), get_core_config resolves
    OPENROUTER_URL to the token-plan endpoint and get_tts_api_key returns the
    token-plan key — the clone path must pair the two (not hit the default host)."""
    sample = (np.arange(64, dtype=np.int16)).tobytes()

    class _CM:
        def get_core_config(self):
            return {
                "assistApi": "mimo",
                "OPENROUTER_URL": "https://token-plan-cn.xiaomimimo.com/v1",
                "TTS_PROVIDER": "",
                "GPTSOVITS_ENABLED": False,
            }

        def get_model_api_config(self, model_type):
            return {"is_custom": False}

        def get_tts_api_key(self, provider):
            return "mimo-token-plan-key"

        def get_voices_for_current_api(self, for_listing=False):
            # stale stored base_url must be ignored in favor of the fresh token-plan endpoint
            return {"mimo-clone-tp": _mimo_clone_meta(sample, mimo_base_url="https://api.xiaomimimo.com/v1")}

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen", has_custom_voice=True, voice_id="mimo-clone-tp",
    )
    assert provider_key == "mimo"
    assert api_key == "mimo-token-plan-key"
    assert worker.keywords["base_url"] == "https://token-plan-cn.xiaomimimo.com/v1"
    assert worker.keywords["clone_voice"].startswith("data:audio/wav;base64,")


@pytest.mark.unit
def test_get_tts_worker_mimo_clone_uses_stored_base_url_when_not_assist(monkeypatch):
    """When MiMo isn't the assist API, the clone path uses the base_url stored in
    voice_meta (dual to minimax_base_url)."""
    sample = (np.arange(32, dtype=np.int16)).tobytes()

    class _CM:
        def get_core_config(self):
            return {"assistApi": "qwen", "TTS_PROVIDER": "", "GPTSOVITS_ENABLED": False}

        def get_model_api_config(self, model_type):
            return {"is_custom": False}

        def get_tts_api_key(self, provider):
            return "mimo-key"

        def get_voices_for_current_api(self, for_listing=False):
            return {"mimo-clone-s": _mimo_clone_meta(sample, mimo_base_url="https://custom.mimo.example/v1")}

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())
    worker, _, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen", has_custom_voice=True, voice_id="mimo-clone-s",
    )
    assert provider_key == "mimo"
    assert worker.keywords["base_url"] == "https://custom.mimo.example/v1"


@pytest.mark.unit
def test_get_tts_worker_mimo_clone_missing_sample_falls_back_to_dummy(monkeypatch):
    class _CM:
        def get_core_config(self):
            return {"assistApi": "qwen", "TTS_PROVIDER": "", "GPTSOVITS_ENABLED": False}

        def get_model_api_config(self, model_type):
            return {"is_custom": False}

        def get_tts_api_key(self, provider):
            return "mimo-key"

        def get_voices_for_current_api(self, for_listing=False):
            return {"mimo-clone-x": {"provider": "mimo", "source": "clone"}}  # no sample b64

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen", has_custom_voice=True, voice_id="mimo-clone-x",
    )
    assert worker is tts_client.dummy_tts_worker
    assert provider_key is None


# ── config_manager: __MIMO__ bucket merges into the current-API voice list ────

@pytest.mark.unit
def test_get_voices_merges_mimo_bucket(monkeypatch):
    cm = get_config_manager()
    monkeypatch.setattr(cm, "get_tts_api_key", lambda p: "mimokey12345678" if p == "mimo" else None)
    monkeypatch.setattr(cm, "load_voice_storage", lambda: {
        "__MIMO__12345678": {"mimo-clone-x": {"source": "clone"}}  # provider stamped by merge
    })
    monkeypatch.setattr(cm, "get_model_api_config", lambda t: {})
    monkeypatch.setattr(cm, "get_core_config", lambda: {})
    monkeypatch.setattr(cm, "_is_local_tts_storage_active", lambda *a, **k: False)
    monkeypatch.setattr(cm, "is_free_voice", lambda: False)
    monkeypatch.setattr(cm, "_get_cosyvoice_storage_keys", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cm, "_get_minimax_storage_keys", lambda: [])
    monkeypatch.setattr(cm, "_get_elevenlabs_storage_keys", lambda: [])

    voices = cm.get_voices_for_current_api()
    assert "mimo-clone-x" in voices
    assert voices["mimo-clone-x"]["provider"] == "mimo"


@pytest.mark.unit
@pytest.mark.parametrize("key_field, api_key, bucket, expected_provider", [
    ("ASSIST_API_KEY_QWEN", "domestic-key-12345678", "domestic-key-12345678", "cosyvoice"),
    ("ASSIST_API_KEY_QWEN_INTL", "intl-key-12345678", "__COSYVOICE_INTL__12345678", "cosyvoice_intl"),
])
@pytest.mark.parametrize("tts_config, core_api_type", [
    ({}, "free"),
    ({"is_custom": True, "base_url": "ws://127.0.0.1:9880", "api_key": ""}, "openai"),
])
def test_get_voices_merges_cosyvoice_provider_bucket_for_listing_when_main_cloud_hidden(
    monkeypatch,
    key_field,
    api_key,
    bucket,
    expected_provider,
    tts_config,
    core_api_type,
):
    cm = get_config_manager()
    voice_id = f"{expected_provider}-clone-x"
    main_voice_id = "domestic-main-voice"
    core_config = {
        "CORE_API_TYPE": core_api_type,
        "AUDIO_API_KEY": "domestic-main-key",
        key_field: api_key,
    }
    monkeypatch.setattr(cm, "get_model_api_config", lambda t: dict(tts_config))
    monkeypatch.setattr(cm, "get_core_config", lambda: dict(core_config))
    monkeypatch.setattr(cm, "load_voice_storage", lambda: {
        "domestic-main-key": {main_voice_id: {"source": "main"}},
        bucket: {voice_id: {"source": "clone"}},  # provider stamped by merge
    })
    monkeypatch.setattr(cm, "_get_minimax_storage_keys", lambda: [])
    monkeypatch.setattr(cm, "_get_elevenlabs_storage_keys", lambda: [])
    monkeypatch.setattr(cm, "_get_mimo_storage_keys", lambda: [])

    voices = cm.get_voices_for_current_api(for_listing=True)

    assert voice_id in voices
    assert voices[voice_id]["provider"] == expected_provider
    assert main_voice_id not in voices


@pytest.mark.unit
def test_registry_declares_clone_and_preset_for_mimo():
    from utils.tts import provider_registry as reg
    mimo = reg.get("mimo")
    assert mimo is not None and "clone" in mimo.capabilities and "preset" in mimo.capabilities
    # clone is advertised in the UI metadata the source-first picker reads
    meta = {m["key"]: m for m in reg.ui_metadata()}
    assert "clone" in meta["mimo"]["capabilities"]


@pytest.mark.unit
def test_mimo_chat_completions_url_maps_ws_to_https_not_plaintext():
    from utils.tts.providers.mimo import mimo_chat_completions_url
    # ws:// must NOT downgrade to plaintext http:// (the api-key header would leak);
    # it maps to https:// just like wss://.
    assert mimo_chat_completions_url("ws://api.xiaomimimo.com/v1").startswith("https://")
    assert mimo_chat_completions_url("wss://api.xiaomimimo.com/v1").startswith("https://")
    # an explicitly-configured http:// (local proxy) is left as-is
    assert mimo_chat_completions_url("http://localhost:8000/v1").startswith("http://localhost")


@pytest.mark.unit
def test_extract_mimo_audio_bytes_tolerates_non_string_audio():
    from utils.voice_clone import _extract_mimo_audio_bytes
    # a malformed upstream payload (audio.data is a number/list) must not raise TypeError
    assert _extract_mimo_audio_bytes({"choices": [{"message": {"audio": {"data": 12345}}}]}) == b""
    assert _extract_mimo_audio_bytes({"choices": [{"message": {"audio": {"data": ["x"]}}}]}) == b""


@pytest.mark.unit
async def test_mimo_validate_sample_requires_audio(monkeypatch):
    from utils.voice_clone import MimoVoiceCloneClient, MimoVoiceCloneError

    class _Transport(httpx.AsyncBaseTransport):
        def __init__(self, with_audio):
            self.with_audio = with_audio

        async def handle_async_request(self, request):
            if self.with_audio:
                return httpx.Response(200, json={
                    "choices": [{"message": {"audio": {"data": base64.b64encode(b"abc").decode()}}}]
                })
            return httpx.Response(200, json={"choices": [{"message": {"content": "no audio here"}}]})

    original = httpx.AsyncClient

    # 200 but no audio → enrollment must fail
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: original(*a, **{**k, "transport": _Transport(False)}))
    with pytest.raises(MimoVoiceCloneError):
        await MimoVoiceCloneClient(api_key="k").validate_sample(b"s", "audio/wav")

    # 200 with audio → passes
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: original(*a, **{**k, "transport": _Transport(True)}))
    await MimoVoiceCloneClient(api_key="k").validate_sample(b"s", "audio/wav")


@pytest.mark.unit
def test_mimo_voice_clone_data_uri_falls_back_on_blank_mime():
    from utils.tts.providers.mimo import mimo_voice_clone_data_uri
    # whitespace-only / empty mime must fall back to audio/wav, never "data:;base64,"
    assert mimo_voice_clone_data_uri(b"x", "   ").startswith("data:audio/wav;base64,")
    assert mimo_voice_clone_data_uri(b"x", "").startswith("data:audio/wav;base64,")
    assert mimo_voice_clone_data_uri(b"x", "audio/mpeg").startswith("data:audio/mpeg;base64,")


@pytest.mark.unit
def test_infer_provider_from_mimo_storage_key():
    cm = get_config_manager()
    assert cm._infer_provider_from_storage_key("__MIMO__abcd1234") == "mimo"


# ── config_manager: heavy sample base64 is stripped from the /voices listing ──

@pytest.mark.unit
def test_get_voices_strips_sample_b64_for_listing(monkeypatch):
    """dispatch (for_listing=False) needs the sample base64; the UI list
    (for_listing=True) must not ship the MB-sized blob to the frontend."""
    cm = get_config_manager()
    monkeypatch.setattr(cm, "get_tts_api_key", lambda p: "mimokey12345678" if p == "mimo" else None)
    monkeypatch.setattr(cm, "load_voice_storage", lambda: {
        "__MIMO__12345678": {"mimo-clone-x": {"source": "clone", "clone_sample_b64": "QUJDRA==", "clone_sample_mime": "audio/wav"}}
    })
    monkeypatch.setattr(cm, "get_model_api_config", lambda t: {})
    monkeypatch.setattr(cm, "get_core_config", lambda: {})
    monkeypatch.setattr(cm, "_is_local_tts_storage_active", lambda *a, **k: False)
    monkeypatch.setattr(cm, "is_free_voice", lambda: False)
    monkeypatch.setattr(cm, "_get_cosyvoice_storage_keys", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cm, "_get_minimax_storage_keys", lambda: [])
    monkeypatch.setattr(cm, "_get_elevenlabs_storage_keys", lambda: [])

    full = cm.get_voices_for_current_api(for_listing=False)
    assert full["mimo-clone-x"]["clone_sample_b64"] == "QUJDRA=="

    listing = cm.get_voices_for_current_api(for_listing=True)
    assert "clone_sample_b64" not in listing["mimo-clone-x"]
    # other metadata survives
    assert listing["mimo-clone-x"]["provider"] == "mimo"
    assert listing["mimo-clone-x"]["clone_sample_mime"] == "audio/wav"


# ── MiMo preview client (dual to MiniMax's synthesize_preview) ────────────────

@pytest.mark.unit
async def test_mimo_synthesize_preview_returns_audio(monkeypatch):
    from utils.voice_clone import MimoVoiceCloneClient

    pcm = (np.arange(128, dtype=np.int16)).tobytes()

    class _FakeTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.body = None

        async def handle_async_request(self, request):
            self.body = json.loads(request.content)
            return httpx.Response(200, json={
                "choices": [{"message": {"audio": {"data": base64.b64encode(pcm).decode("ascii")}}}]
            })

    transport = _FakeTransport()
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: original(*a, **{**k, "transport": transport}))

    client = MimoVoiceCloneClient(api_key="mimo-key")
    audio = await client.synthesize_preview(b"sample-bytes", "audio/wav", text="测试")
    assert audio == pcm
    # preview is a non-stream wav request against the voiceclone model
    assert transport.body["model"] == "mimo-v2.5-tts-voiceclone"
    assert transport.body["stream"] is False
    assert transport.body["audio"]["format"] == "wav"
    assert transport.body["audio"]["voice"].startswith("data:audio/wav;base64,")


@pytest.mark.unit
async def test_mimo_design_voice_preview_uses_voice_preview_template(monkeypatch):
    """Voice Design previews must follow the same template as VoiceClone.

    Even if an older saved design voice carries ``preview_text`` metadata,
    provider-specific preview paths must speak the localized template from
    ``config/prompts/prompts_voice.py``.
    """
    from starlette.requests import Request
    from main_routers.characters_router import voice_preview as cr

    voice_id = "mimo-design-aria-1234"
    saved_preview_text = "Hello, this is the saved MiMo design preview template."
    captured = {}

    class _CM:
        def get_voices_for_current_api(self, for_listing=False):
            return {
                voice_id: {
                    "provider": "mimo",
                    "source": "design",
                    "design_prompt": "an energetic sports girl",
                    "preview_text": saved_preview_text,
                    "mimo_base_url": "https://stored.mimo.example/v1",
                }
            }

        def get_model_api_config(self, model_type):
            return {"api_key": ""}

        async def aget_core_config(self):
            return {"assistApi": "qwen", "OPENROUTER_URL": "https://active.example/v1"}

        def get_tts_api_key(self, provider):
            assert provider == "mimo"
            return "mimo-key"

        def voice_id_exists_in_any_storage(self, _voice_id):
            return _voice_id == voice_id

    class _FakeMimoClient:
        def __init__(self, api_key, base_url=None):
            captured["api_key"] = api_key
            captured["base_url"] = base_url

        async def synthesize_design_preview(self, design_prompt, *, text):
            captured["design_prompt"] = design_prompt
            captured["text"] = text
            return b"RIFFfake-wav"

    monkeypatch.setattr(cr, "get_config_manager", lambda: _CM())
    monkeypatch.setattr(cr, "MimoVoiceCloneClient", _FakeMimoClient)

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/preview",
        "query_string": b"language=zh-CN",
        "headers": [],
        "server": ("testserver", 80),
    })
    result = await cr.get_voice_preview(request, voice_id=voice_id, language="zh-CN")

    assert result["success"] is True
    assert captured["api_key"] == "mimo-key"
    assert captured["base_url"] == "https://stored.mimo.example/v1"
    assert captured["design_prompt"] == "an energetic sports girl"
    assert captured["text"] == cr.VOICE_PREVIEW_TEXTS["zh-CN"]
