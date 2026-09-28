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
    first = build_glm_voice_name("Miko", "aabbccddeeff00112233445566778899", "ch")
    second = build_glm_voice_name("Miko", "aabbccddeeff00112233445566778899", "ch")
    other_audio = build_glm_voice_name("Miko", "ffeeddccbbaa00112233445566778899", "ch")
    # 同音频换 ref_language：本地 MD5 去重不命中（ref_language 是去重键的一部分），
    # voice_name 必须随之变化，否则 GLM 按账号内唯一性拒绝第二次注册。
    other_language = build_glm_voice_name("Miko", "aabbccddeeff00112233445566778899", "ja")

    assert first == second
    assert first != other_audio
    assert first != other_language
    assert first.startswith("neko_miko_ch_")
    assert first.endswith("aabbccddeeff")
    assert len(first) <= 64


@pytest.mark.unit
def test_build_glm_voice_name_without_language_keeps_legacy_shape():
    # 不传 ref_language 时保持旧形状（无语言段），纯函数向后兼容。
    assert build_glm_voice_name("Miko", "aabbccddeeff00112233") == "neko_miko_aabbccddeeff"


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

    assert isinstance(worker, partial)
    assert worker.func is tts_client.cogtts_tts_worker
    assert worker.keywords["base_url"] == GLM_TTS_DEFAULT_BASE_URL
    assert api_key == "glm-key"
    assert provider_key == "glm_tts"


@pytest.mark.unit
def test_glm_clone_resolver_passes_persisted_base_url_to_worker(monkeypatch):
    """The glm_base_url persisted in voice_meta must be forwarded to the cogtts
    worker: historical clone entries keep synthesizing against their
    registration endpoint instead of silently dropping back to the official one."""

    class _CM:
        def get_tts_api_key(self, provider):
            return "glm-key"

    from utils.tts.provider_registry import DispatchContext

    ctx = DispatchContext(
        core_config={},
        cm=_CM(),
        voice_id="voice_clone_x",
        has_custom_voice=True,
        voice_meta_loader=lambda: {
            "provider": "glm_tts",
            "source": "clone",
            "glm_base_url": "https://glm-proxy.example.com/api/paas/v4",
        },
    )
    worker, api_key, provider_key = tts_client._glm_clone_resolve(ctx)

    assert isinstance(worker, partial)
    assert worker.func is tts_client.cogtts_tts_worker
    assert worker.keywords["base_url"] == "https://glm-proxy.example.com/api/paas/v4"
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
    """Config-selecting GLM (ttsModelProvider=glm_tts) without a clone voice_meta
    must NOT be intercepted by the glm_tts registry entry — the native
    core_api_type=='glm' path is still handled by get_tts_worker's core branch
    (key from the tts_custom slot), preserving existing behavior."""
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
def test_glm_tts_registry_meta_matches_cogtts_runtime_behavior():
    """glm_tts must have a TTSProviderMeta entry (wehos review): the resolver
    returns provider_key='glm_tts', and tts_runtime derives replay-progress /
    normalize behavior from the meta table. Without the entry a GLM cloned voice
    loses per-sentence replay on worker failover, diverging from native cogtts."""
    from main_logic.tts_client import TTS_PROVIDER_REGISTRY

    meta = TTS_PROVIDER_REGISTRY.get("glm_tts")
    cogtts_meta = TTS_PROVIDER_REGISTRY.get("cogtts")
    assert meta is not None, "glm_tts must be present in TTS_PROVIDER_REGISTRY"
    assert meta.category == "http_sentence"
    # 与 cogtts 同 worker：运行时行为位必须一致（replay progress / normalizer）。
    assert meta.category == cogtts_meta.category
    assert meta.input_streaming == cogtts_meta.input_streaming
    assert meta.output_streaming == cogtts_meta.output_streaming
    assert meta.client_sentence_split == cogtts_meta.client_sentence_split


@pytest.mark.unit
def test_glm_tts_frontend_and_backend_are_wired():
    voice_clone_html = Path("templates/voice_clone.html").read_text(encoding="utf-8")
    voice_clone_js = Path("static/js/voice_clone.js").read_text(encoding="utf-8")
    registry_py = Path("main_logic/tts_client/__init__.py").read_text(encoding="utf-8")
    registry_meta_py = Path("main_logic/tts_client/_registry_meta.py").read_text(encoding="utf-8")
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
    # 直链克隆禁用：/voice_clone_direct 的 valid_providers 不含 glm_tts，前端必须
    # 像 mimo/doubao_tts 一样禁用直链方式，否则提交会吃到 TTS_PROVIDER_INVALID。
    direct_link_fn = voice_clone_js.split("function isDirectLinkUnsupportedProvider")[1].split("}")[0]
    assert "'glm_tts'" in direct_link_fn
    assert "key='glm_tts'" in registry_py
    assert "_glm_clone_is_selected" in registry_py
    # TTS_PROVIDER_REGISTRY 元数据（wehos review）：resolver 返回 provider_key='glm_tts'，
    # tts_runtime 按 meta 决定逐句重放进度等运行时行为；缺失会让克隆音色在 worker
    # 故障切换时拿不到逐句确认（原生 cogtts 有）。
    assert '"glm_tts": TTSProviderMeta(' in registry_meta_py
    assert "GLM_TTS_API_KEY_MISSING" in router_py
    assert "GlmVoiceCloneClient(api_key=api_key, base_url=base_url)" in router_py
    # 10MB 超限预检（CodeRabbit review）：超限走 413 而不是 500，且不打远端 API。
    assert "GLM_VOICE_CLONE_MAX_AUDIO_BYTES" in router_py
    assert "GLM_TTS_AUDIO_TOO_LARGE" in router_py
    # 本地 WS TTS 激活时不得把 glm_tts 克隆误送进 /v1/speakers/register 本地注册流。
    assert "provider not in ('vllm_omni', 'glm_tts')" in router_py
    assert "GLM_TTS_PREVIEW_FAILED" in preview_py
    assert "provider == 'glm_tts'" in preview_py
    assert "get_tts_api_key('glm_tts')" in storage_py
    # 删除白名单必须覆盖 __GLM_TTS__ 分桶，否则标准删除接口删不掉已注册的 GLM 音色。
    assert "storage_key.startswith(GLM_VOICE_STORAGE_KEY)" in storage_py
    assert GLM_VOICE_STORAGE_KEY == "__GLM_TTS__"
    assert zh_locale["voice"]["provider"]["glm_tts"] == "智谱GLM声音复刻"
    assert zh_locale["voice"]["glmTtsApiRequired"]
