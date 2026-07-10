import base64
import io
import json
from functools import partial
from pathlib import Path

import httpx
import pytest

from main_routers import characters_router
from main_logic import tts_client
from utils.doubao_tts import (
    DOUBAO_TTS_DEFAULT_CONTEXT_TEXTS,
    DOUBAO_VOICE_CLONE_RESOURCE_ID,
    DoubaoTtsError,
    DoubaoVoiceCloneClient,
    build_doubao_tts_payload,
    extract_doubao_audio_bytes,
)


@pytest.mark.unit
def test_extract_doubao_audio_bytes_from_ndjson_chunks():
    first = b"RIFF...."
    second = b"data"
    body = "\n".join([
        json.dumps({"code": 0, "data": base64.b64encode(first).decode("ascii")}),
        json.dumps({"code": 0, "data": base64.b64encode(second).decode("ascii")}),
    ])

    assert extract_doubao_audio_bytes(body) == first + second


@pytest.mark.unit
def test_extract_doubao_audio_bytes_from_nested_data_audio():
    audio = b"RIFF....nested"
    body = {
        "code": 0,
        "data": {
            "audio": base64.b64encode(audio).decode("ascii"),
        },
    }

    assert extract_doubao_audio_bytes(body) == audio


@pytest.mark.unit
def test_extract_doubao_audio_bytes_from_adjacent_json_chunks():
    first = b"RIFF...."
    second = b"chunk"
    body = "".join([
        json.dumps({"code": 0, "data": base64.b64encode(first).decode("ascii")}),
        json.dumps({"code": 0, "data": base64.b64encode(second).decode("ascii")}),
    ])

    assert extract_doubao_audio_bytes(body) == first + second


@pytest.mark.unit
def test_extract_doubao_audio_bytes_raises_on_error_code():
    with pytest.raises(DoubaoTtsError, match="requested resource not granted"):
        extract_doubao_audio_bytes({"code": 403, "message": "requested resource not granted"})


@pytest.mark.unit
def test_extract_doubao_audio_bytes_accepts_ok_message_with_nonzero_code():
    audio = b"RIFF....data"
    body = {"code": 3000, "message": "OK", "data": base64.b64encode(audio).decode("ascii")}
    assert extract_doubao_audio_bytes(body) == audio


@pytest.mark.unit
def test_build_doubao_tts_payload_uses_context_texts_additions():
    payload = build_doubao_tts_payload("晚上早点睡喵。", "S_test")

    req = payload["req_params"]
    additions = json.loads(req["additions"])
    assert req["text"] == "晚上早点睡喵。"
    assert req["speaker"] == "S_test"
    assert req["audio_params"]["format"] == "wav"
    assert DOUBAO_TTS_DEFAULT_CONTEXT_TEXTS in additions["context_texts"]
    assert "<" not in req["text"]
    assert "[" not in req["text"]


@pytest.mark.unit
def test_get_tts_worker_routes_explicit_doubao_tts(monkeypatch):
    class _CM:
        def get_core_config(self):
            return {
                "TTS_PROVIDER": "doubao_tts",
                "ttsProvider": "doubao_tts",
                "GPTSOVITS_ENABLED": False,
            }

        def load_json_config(self, filename, default):
            assert filename == "core_config.json"
            return {
                "ttsModelProvider": "doubao_tts",
                "ttsModelUrl": "https://openspeech.bytedance.com",
                "ttsModelId": "seed-icl-2.0",
                "ttsVoiceId": "S_test",
                "ttsModelApiKey": "doubao-key",
            }

        def get_tts_api_key(self, provider):
            assert provider == "doubao_tts"
            return "doubao-key"

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=False,
        voice_id="",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.doubao_tts_worker
    assert worker.keywords["base_url"] == "https://openspeech.bytedance.com"
    assert worker.keywords["resource_id"] == "seed-icl-2.0"
    assert worker.keywords["configured_voice"] == "S_test"
    assert api_key == "doubao-key"
    assert provider_key == "doubao_tts"


@pytest.mark.unit
def test_get_tts_worker_routes_doubao_clone_before_mimo_default(monkeypatch):
    class _CM:
        def get_core_config(self):
            return {
                "assistApi": "mimo",
                "OPENROUTER_URL": "https://api.xiaomimimo.com/v1",
                "TTS_PROVIDER": "",
                "ttsProvider": "",
                "GPTSOVITS_ENABLED": False,
            }

        def load_json_config(self, filename, default):
            assert filename == "core_config.json"
            return {
                "ttsModelProvider": "",
                "ttsModelUrl": "https://openspeech.bytedance.com",
                "ttsModelId": "seed-icl-2.0",
                "ttsVoiceId": "",
                "ttsModelApiKey": "",
            }

        def get_model_api_config(self, model_type):
            return {"is_custom": False}

        def get_tts_api_key(self, provider):
            assert provider == "doubao_tts"
            return "doubao-key"

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())
    monkeypatch.setattr(
        tts_client,
        "_get_voice_meta",
        lambda voice_id: {
            "provider": "doubao_tts",
            "source": "clone",
            "doubao_base_url": "https://openspeech-clone.bytedance.com",
            "doubao_resource_id": "seed-icl-2.0-clone",
        },
    )

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=True,
        voice_id="S_xeC2CDp72",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.doubao_tts_worker
    assert worker.keywords["base_url"] == "https://openspeech-clone.bytedance.com"
    assert worker.keywords["resource_id"] == "seed-icl-2.0-clone"
    assert worker.keywords["configured_voice"] == "S_xeC2CDp72"
    assert api_key == "doubao-key"
    assert provider_key == "doubao_tts"


@pytest.mark.unit
def test_get_tts_worker_doubao_clone_ignores_foreign_shared_tts_key(monkeypatch):
    class _CM:
        def get_core_config(self):
            return {
                "assistApi": "mimo",
                "OPENROUTER_URL": "https://api.xiaomimimo.com/v1",
                "TTS_PROVIDER": "",
                "ttsProvider": "",
                "GPTSOVITS_ENABLED": False,
            }

        def load_json_config(self, filename, default):
            assert filename == "core_config.json"
            return {
                "ttsModelProvider": "vllm_omni",
                "ttsModelUrl": "http://localhost:8091",
                "ttsModelId": "Qwen3-TTS",
                "ttsVoiceId": "Puck",
                "ttsModelApiKey": "sk-vllm-should-not-leak",
                "assistApiKeyDoubaoTts": "doubao-speech-key",
            }

        def get_model_api_config(self, model_type):
            return {"is_custom": False}

        def get_tts_api_key(self, provider):
            assert provider == "doubao_tts"
            return "doubao-speech-key"

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())
    monkeypatch.setattr(
        tts_client,
        "_get_voice_meta",
        lambda voice_id: {
            "provider": "doubao_tts",
            "source": "clone",
            "doubao_base_url": "https://openspeech.bytedance.com",
            "doubao_resource_id": "seed-icl-2.0",
        },
    )

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=True,
        voice_id="S_xeC2CDp72",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.doubao_tts_worker
    assert api_key == "doubao-speech-key"
    assert provider_key == "doubao_tts"


@pytest.mark.unit
def test_doubao_voice_clone_accepts_existing_speaker_id():
    assert characters_router._normalize_doubao_voice_clone_speaker_id(
        " S_xeC2CDp72 "
    ) == "S_xeC2CDp72"


@pytest.mark.unit
async def test_doubao_voice_clone_client_posts_openspeech_payload(monkeypatch):
    requests = []

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            requests.append({
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": json.loads(request.content),
            })
            return httpx.Response(200, json={"code": 0, "data": {"speaker_id": "S_created"}})

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _Transport()
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    client = DoubaoVoiceCloneClient(
        api_key="doubao-key",
        base_url="https://openspeech.bytedance.com",
        resource_id="seed-icl-2.0",
    )
    voice_id = await client.clone_voice(
        io.BytesIO(b"wav-bytes"),
        speaker_id="S_console1234",
        display_name="薄绿",
    )

    assert voice_id == "S_created"
    sent = requests[0]
    assert sent["url"] == "https://openspeech.bytedance.com/api/v3/tts/voice_clone"
    assert sent["headers"]["x-api-key"] == "doubao-key"
    assert "x-api-resource-id" not in sent["headers"]
    assert sent["body"]["speaker_id"] == "S_console1234"
    assert sent["body"]["audio"]["format"] == "wav"
    assert base64.b64decode(sent["body"]["audio"]["data"]) == b"wav-bytes"


@pytest.mark.unit
async def test_doubao_voice_clone_client_requires_returned_voice_id(monkeypatch):
    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 0, "data": {}})

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _Transport()
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    client = DoubaoVoiceCloneClient(
        api_key="doubao-key",
        base_url="https://openspeech.bytedance.com",
        resource_id="seed-icl-2.0",
    )
    with pytest.raises(DoubaoTtsError, match="未返回音色 ID"):
        await client.clone_voice(
            io.BytesIO(b"wav-bytes"),
            speaker_id="S_console1234",
            display_name="薄绿",
        )


@pytest.mark.unit
def test_doubao_tts_frontend_and_config_are_wired():
    config = json.loads(Path("config/api_providers.json").read_text(encoding="utf-8"))
    assert "doubao_tts" not in config["assist_api_providers"]
    assert "\u706b\u5c71\u65b9\u821f" in config["assist_api_providers"]["doubao"]["name"]
    assert "\u706b\u5c71\u5f15\u64ce" in config["keybook_api_providers"]["doubao_tts"]["name"]
    assert config["keybook_api_providers"]["doubao_tts"]["tts_default_model"] == "seed-icl-2.0"
    assert config["keybook_api_providers"]["doubao_tts"]["tts_config_visible"] is False

    settings_js = Path("static/js/api_key_settings.js").read_text(encoding="utf-8")
    voice_clone_js = Path("static/js/voice_clone.js").read_text(encoding="utf-8")
    registry_py = Path("main_logic/tts_client/__init__.py").read_text(encoding="utf-8")
    router_py = Path("main_routers/characters_router.py").read_text(encoding="utf-8")

    assert "isTtsProviderVisibleInModelConfig" in settings_js
    assert "selectedTtsProvider === 'doubao_tts'" not in settings_js
    assert "keybook_api_providers_full" in settings_js
    assert "doubao_tts: 'doubao_tts'" in voice_clone_js
    assert DOUBAO_VOICE_CLONE_RESOURCE_ID == "seed-icl-2.0"
    assert "isDoubaoSpeakerId" in voice_clone_js
    assert "doubaoSpeakerIdRequired" in voice_clone_js
    assert "doubao_speaker_mode" not in voice_clone_js
    assert "cfgHasDoubaoTtsKey" in voice_clone_js
    assert "probe_kind='http_tts'" in registry_py
    assert "tts_config_visible=False" in registry_py
    assert "capabilities=frozenset({'clone'})" in registry_py
    assert "_normalize_doubao_voice_clone_speaker_id(prefix)" in router_py
    assert "custom_speaker_id" not in router_py
    assert "resource_id = DOUBAO_TTS_DEFAULT_RESOURCE_ID" in router_py
