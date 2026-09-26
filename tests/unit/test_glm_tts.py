import io
import json
from functools import partial
from pathlib import Path

import httpx
import pytest

from main_logic import tts_client
from utils.glm_tts import (
    GLM_TTS_DEFAULT_BASE_URL,
    GLM_VOICE_CLONE_MODEL,
    GLM_VOICE_STORAGE_KEY,
    GlmTtsError,
    GlmVoiceCloneClient,
    build_glm_voice_name,
    sanitize_glm_voice_prefix,
)


@pytest.mark.unit
def test_sanitize_glm_voice_prefix_keeps_alnum_only():
    assert sanitize_glm_voice_prefix("薄绿 Cat_01!") == "cat01"
    assert sanitize_glm_voice_prefix("") == ""


@pytest.mark.unit
def test_build_glm_voice_name_is_stable_and_unique_per_audio():
    first = build_glm_voice_name("Miko", "aabbccddeeff00112233445566778899")
    second = build_glm_voice_name("Miko", "aabbccddeeff00112233445566778899")
    other_audio = build_glm_voice_name("Miko", "ffeeddccbbaa00112233445566778899")

    assert first == second
    assert first != other_audio
    assert first.startswith("neko_miko_")
    assert first.endswith("aabbccddeeff")
    assert len(first) <= 64


@pytest.mark.unit
def test_get_tts_worker_routes_glm_clone_voice(monkeypatch):
    class _CM:
        def get_core_config(self):
            return {
                "assistApi": "qwen",
                "TTS_PROVIDER": "",
                "ttsProvider": "",
                "GPTSOVITS_ENABLED": False,
            }

        def load_json_config(self, filename, default):
            assert filename == "core_config.json"
            return {"ttsModelProvider": "", "ttsModelApiKey": ""}

        def get_model_api_config(self, model_type):
            return {"is_custom": False}

        def get_tts_api_key(self, provider):
            assert provider == "glm_tts"
            return "glm-key"

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())
    monkeypatch.setattr(
        tts_client,
        "_get_voice_meta",
        lambda voice_id: {
            "provider": "glm_tts",
            "source": "clone",
            "glm_base_url": GLM_TTS_DEFAULT_BASE_URL,
        },
    )

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=True,
        voice_id="voice_clone_20260926_001",
    )

    assert worker is tts_client.cogtts_tts_worker
    assert api_key == "glm-key"
    assert provider_key == "glm_tts"


@pytest.mark.unit
def test_get_tts_worker_glm_clone_without_key_falls_back_to_dummy(monkeypatch):
    from main_logic.tts_client.workers.dummy import dummy_tts_worker

    class _CM:
        def get_core_config(self):
            return {"TTS_PROVIDER": "", "ttsProvider": "", "GPTSOVITS_ENABLED": False}

        def load_json_config(self, filename, default):
            return {}

        def get_model_api_config(self, model_type):
            return {"is_custom": False}

        def get_tts_api_key(self, provider):
            assert provider == "glm_tts"
            return ""

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())
    monkeypatch.setattr(
        tts_client,
        "_get_voice_meta",
        lambda voice_id: {"provider": "glm_tts", "source": "clone"},
    )

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen",
        has_custom_voice=True,
        voice_id="voice_clone_20260926_001",
    )

    assert worker is dummy_tts_worker
    assert api_key is None
    assert provider_key is None


@pytest.mark.unit
def test_glm_clone_selection_ignores_config_without_voice_meta(monkeypatch):
    """config 选 GLM（ttsModelProvider=glm_tts）但无克隆 voice_meta 时不得被 glm_tts
    注册条目拦截——原生 core_api_type=='glm' 路径仍由 get_tts_worker 的 core 分支
    处理（key 走 tts_custom 槽），保持既有行为。"""
    from utils.tts.provider_registry import DispatchContext

    class _CM:
        def get_tts_api_key(self, provider):
            return "glm-key"

    ctx = DispatchContext(
        core_config={"ttsModelProvider": "glm_tts", "TTS_PROVIDER": "glm_tts"},
        cm=_CM(),
        voice_id="tongtong",
        has_custom_voice=False,
        voice_meta_loader=lambda: None,
    )
    assert tts_client._glm_clone_is_selected(ctx) is False

    ctx_clone = DispatchContext(
        core_config={},
        cm=_CM(),
        voice_id="voice_clone_x",
        has_custom_voice=True,
        voice_meta_loader=lambda: {"provider": "glm_tts"},
    )
    assert tts_client._glm_clone_is_selected(ctx_clone) is True


@pytest.mark.unit
async def test_glm_voice_clone_client_uploads_then_clones(monkeypatch):
    requests = []

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raw = await request.aread()
            body = {}
            content_type = request.headers.get("content-type", "")
            if "json" in content_type:
                body = json.loads(raw)
            requests.append({
                "url": str(request.url),
                "headers": dict(request.headers),
                "content_type": content_type,
                "body": body,
                "raw": raw,
            })
            if str(request.url).endswith("/files"):
                return httpx.Response(200, json={"id": "file_abc123", "object": "file"})
            return httpx.Response(
                200,
                json={"voice": "voice_clone_20260926_001", "file_purpose": "voice-clone-output"},
            )

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _Transport()
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    client = GlmVoiceCloneClient(api_key="glm-key")
    voice_id = await client.clone_voice(
        io.BytesIO(b"wav-bytes"),
        voice_name="neko_miko_aabbcc",
        ref_text="希望你以后能够做的比我还好呦",
    )

    assert voice_id == "voice_clone_20260926_001"

    upload, clone = requests
    assert upload["url"] == f"{GLM_TTS_DEFAULT_BASE_URL}/files"
    assert upload["headers"]["authorization"] == "Bearer glm-key"
    assert "voice-clone-input" in upload["raw"].decode("utf-8", errors="ignore")
    assert b"wav-bytes" in upload["raw"]

    assert clone["url"] == f"{GLM_TTS_DEFAULT_BASE_URL}/voice/clone"
    assert clone["headers"]["authorization"] == "Bearer glm-key"
    assert clone["body"]["model"] == GLM_VOICE_CLONE_MODEL
    assert clone["body"]["voice_name"] == "neko_miko_aabbcc"
    assert clone["body"]["file_id"] == "file_abc123"
    assert clone["body"]["input"]
    assert clone["body"]["text"] == "希望你以后能够做的比我还好呦"


@pytest.mark.unit
async def test_glm_voice_clone_client_requires_returned_voice(monkeypatch):
    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if str(request.url).endswith("/files"):
                return httpx.Response(200, json={"id": "file_abc123"})
            return httpx.Response(200, json={})

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _Transport()
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    client = GlmVoiceCloneClient(api_key="glm-key")
    with pytest.raises(GlmTtsError, match="未返回 voice"):
        await client.clone_voice(io.BytesIO(b"wav-bytes"), voice_name="neko_miko_aabbcc")


@pytest.mark.unit
async def test_glm_voice_clone_client_surfaces_upstream_error_body(monkeypatch):
    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if str(request.url).endswith("/files"):
                return httpx.Response(
                    200,
                    json={"error": {"code": "1210", "message": "api key not valid"}},
                )
            raise AssertionError("clone should not be reached")

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _Transport()
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    client = GlmVoiceCloneClient(api_key="bad-key")
    with pytest.raises(GlmTtsError, match="1210"):
        await client.clone_voice(io.BytesIO(b"wav-bytes"), voice_name="neko_miko_aabbcc")


@pytest.mark.unit
def test_glm_tts_frontend_and_backend_are_wired():
    voice_clone_html = Path("templates/voice_clone.html").read_text(encoding="utf-8")
    voice_clone_js = Path("static/js/voice_clone.js").read_text(encoding="utf-8")
    registry_py = Path("main_logic/tts_client/__init__.py").read_text(encoding="utf-8")
    router_py = Path(
        "main_routers/characters_router/voice_cloning.py"
    ).read_text(encoding="utf-8")
    preview_py = Path(
        "main_routers/characters_router/voice_preview.py"
    ).read_text(encoding="utf-8")
    storage_py = Path(
        "utils/config_manager/voice_storage.py"
    ).read_text(encoding="utf-8")
    zh_locale = json.loads(Path("static/locales/zh-CN.json").read_text(encoding="utf-8"))

    assert 'value="glm_tts"' in voice_clone_html
    assert "glm_tts: 'glm'" in voice_clone_js
    assert "['glm_tts', 'assistApiKeyGlm']" in voice_clone_js
    assert "voice.glmTtsApiRequired" in voice_clone_js
    assert "key='glm_tts'" in registry_py
    assert "_glm_clone_is_selected" in registry_py
    assert "GLM_TTS_API_KEY_MISSING" in router_py
    assert "GlmVoiceCloneClient(api_key=api_key, base_url=base_url)" in router_py
    assert "GLM_TTS_PREVIEW_FAILED" in preview_py
    assert "provider == 'glm_tts'" in preview_py
    assert "get_tts_api_key('glm_tts')" in storage_py
    assert GLM_VOICE_STORAGE_KEY == "__GLM_TTS__"
    assert zh_locale["voice"]["provider"]["glm_tts"] == "智谱GLM声音复刻"
    assert zh_locale["voice"]["glmTtsApiRequired"]
