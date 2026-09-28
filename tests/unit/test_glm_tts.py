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
def test_glm_clone_resolver_uses_glm_tts_model():
    """The official /audio/speech docs list only glm-tts in the model enum, so
    cloned-voice synthesis must send glm-tts while the native CogTTS path keeps
    its cogtts default."""
    import inspect

    from utils.glm_tts import GLM_TTS_SPEECH_MODEL
    from utils.tts.provider_registry import DispatchContext

    class _CM:
        def get_tts_api_key(self, provider):
            return "glm-key"

    ctx = DispatchContext(
        core_config={},
        cm=_CM(),
        voice_id="voice_clone_x",
        has_custom_voice=True,
        voice_meta_loader=lambda: {"provider": "glm_tts"},
    )
    worker, _, _ = tts_client._glm_clone_resolve(ctx)

    assert GLM_TTS_SPEECH_MODEL == "glm-tts"
    assert worker.keywords["model"] == "glm-tts"
    native_default = inspect.signature(tts_client.cogtts_tts_worker).parameters["model"].default
    assert native_default == "cogtts"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("model", "expected_provider"),
    [("glm-tts", "glm_tts"), ("cogtts", "cogtts")],
)
async def test_cogtts_worker_records_telemetry_by_provider(monkeypatch, model, expected_provider):
    """Cloned GLM voices must be attributed to glm_tts in telemetry, while the
    native CogTTS route keeps reporting cogtts."""
    from main_logic.tts_client.workers import cogtts as cogtts_worker_module

    captured = {}
    recorded = []

    def fake_run_sentence_worker(_request_queue, _response_queue, setup, **_kwargs):
        captured["setup"] = setup

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(await request.aread())
            return httpx.Response(200, content=b"")

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _Transport()
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(cogtts_worker_module, "_run_sentence_tts_worker", fake_run_sentence_worker)
    monkeypatch.setattr(
        cogtts_worker_module,
        "_record_tts_telemetry",
        lambda provider, count: recorded.append((provider, count)),
    )
    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    cogtts_worker_module.cogtts_tts_worker(None, None, "glm-key", "voice_x", model=model)
    synthesize, cleanup = await captured["setup"](None)
    try:
        await synthesize("hello", "speech-1")
    finally:
        await cleanup()

    assert captured["body"]["model"] == model
    assert recorded == [(expected_provider, len("hello"))]


@pytest.mark.unit
async def test_glm_voice_clone_client_preview_uses_glm_tts_model(monkeypatch):
    captured = {}

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(await request.aread())
            return httpx.Response(200, content=b"RIFFwav", headers={"content-type": "audio/wav"})

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _Transport()
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    audio = await GlmVoiceCloneClient(api_key="glm-key").synthesize_preview("voice_x", "你好")

    assert audio == b"RIFFwav"
    assert captured["url"] == f"{GLM_TTS_DEFAULT_BASE_URL}/audio/speech"
    assert captured["body"]["model"] == "glm-tts"
    assert captured["body"]["voice"] == "voice_x"
    assert captured["body"]["response_format"] == "wav"


def _patch_glm_voice_clone_route(monkeypatch, normalized_bytes, saved):
    from types import SimpleNamespace

    from main_routers.characters_router import voice_cloning as voice_cloning_router

    class _ConfigManager:
        async def aget_core_config(self):
            return {}

        async def aget_model_api_config(self, _model_type, core_config=None):
            return {}

        def get_tts_api_key(self, provider):
            assert provider == "glm_tts"
            return "glm-key-12345678"

        def find_voice_by_audio_md5(self, *_args):
            return None

        def save_voice_for_api_key(self, storage_key, voice_id, voice_data):
            saved.update(storage_key=storage_key, voice_id=voice_id, voice_data=voice_data)

    async def fake_read_limited_stream(_file, _limit):
        return io.BytesIO(b"reference audio")

    def fake_normalize(_buffer, _filename):
        return io.BytesIO(normalized_bytes), "reference.wav", {
            "original": {"sample_rate": 16000, "channels": 1},
            "normalized": {"sample_rate": 16000},
        }

    monkeypatch.setattr(voice_cloning_router, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(voice_cloning_router, "_read_limited_stream", fake_read_limited_stream)
    monkeypatch.setattr(voice_cloning_router, "normalize_voice_clone_api_audio", fake_normalize)
    monkeypatch.setattr(voice_cloning_router, "_is_local_voice_clone_tts_config", lambda *_args: False)
    return voice_cloning_router, SimpleNamespace(filename="reference.wav")


@pytest.mark.unit
async def test_glm_voice_clone_route_registers_and_saves(monkeypatch):
    saved = {}
    router, upload = _patch_glm_voice_clone_route(monkeypatch, b"normalized wav", saved)
    calls = {}

    async def fake_clone_voice(self, audio_buffer, *, voice_name, filename, **_kwargs):
        calls.update(base_url=self.base_url, voice_name=voice_name, filename=filename,
                     data=audio_buffer.getvalue())
        return "voice_clone_20260928_001"

    monkeypatch.setattr(router.GlmVoiceCloneClient, "clone_voice", fake_clone_voice)

    response = await router.voice_clone(
        file=upload, prefix="Miko", ref_language="ch", provider="glm_tts", ref_text="",
    )

    assert response.status_code == 200
    assert calls["base_url"] == GLM_TTS_DEFAULT_BASE_URL
    assert calls["voice_name"].startswith("neko_miko_ch_")
    assert calls["data"] == b"normalized wav"
    assert saved["storage_key"] == f"{GLM_VOICE_STORAGE_KEY}12345678"
    assert saved["voice_id"] == "voice_clone_20260928_001"
    assert saved["voice_data"]["provider"] == "glm_tts"
    assert saved["voice_data"]["glm_base_url"] == GLM_TTS_DEFAULT_BASE_URL


@pytest.mark.unit
async def test_glm_voice_clone_route_rejects_oversized_audio_with_413(monkeypatch):
    from utils.glm_tts import GLM_VOICE_CLONE_MAX_AUDIO_BYTES

    saved = {}
    router, upload = _patch_glm_voice_clone_route(
        monkeypatch, b"\0" * (GLM_VOICE_CLONE_MAX_AUDIO_BYTES + 1), saved,
    )

    async def reject_clone(*_args, **_kwargs):
        raise AssertionError("oversized audio must not reach the GLM API")

    monkeypatch.setattr(router.GlmVoiceCloneClient, "clone_voice", reject_clone)

    response = await router.voice_clone(
        file=upload, prefix="Miko", ref_language="ch", provider="glm_tts", ref_text="",
    )

    assert response.status_code == 413
    body = json.loads(response.body)
    assert body["code"] == "GLM_TTS_AUDIO_TOO_LARGE"
    assert "10.0MB" in body["error"]
    assert not saved


@pytest.mark.unit
async def test_glm_direct_link_clone_registers_via_two_step_flow(monkeypatch):
    """/voice_clone_direct must accept glm_tts: download → normalize → MD5 dedup
    → GlmVoiceCloneClient.clone_voice → persist into the __GLM_TTS__ bucket."""
    from main_routers.characters_router import voice_cloning as vc

    saved: dict = {}

    class _CM:
        def get_tts_api_key(self, provider):
            assert provider == "glm_tts"
            return "glm-key-1234"

        def find_voice_by_audio_md5(self, storage_key, audio_md5, ref_language):
            assert storage_key == f"{GLM_VOICE_STORAGE_KEY}key-1234"
            return None

        def save_voice_for_api_key(self, storage_key, voice_id, voice_data):
            saved["storage_key"] = storage_key
            saved["voice_id"] = voice_id
            saved["voice_data"] = voice_data

    class _FakeClient:
        def __init__(self, api_key, base_url=None):
            saved["client_args"] = (api_key, base_url)

        async def clone_voice(self, audio_buffer, *, voice_name, filename, **kwargs):
            saved["voice_name"] = voice_name
            saved["audio"] = audio_buffer.getvalue()
            return "voice_clone_direct_001"

    class _FakeHeadResp:
        status_code = 200

        async def aclose(self):
            pass

    monkeypatch.setattr(vc, "get_config_manager", lambda: _CM())
    monkeypatch.setattr(vc, "GlmVoiceCloneClient", _FakeClient)

    async def _noop_validate(url):
        return None

    async def _fake_head(method, url, **kwargs):
        return _FakeHeadResp()

    async def _fake_download(url, max_file_size=None):
        return "sample.wav", b"wav-bytes"

    def _fake_normalize(buffer, filename):
        return io.BytesIO(b"normalized-wav"), "sample_norm.wav", {
            "original": {"sample_rate": 24000, "channels": 1},
            "normalized": {"sample_rate": 24000},
        }

    monkeypatch.setattr(vc, "_validate_direct_link_target", _noop_validate)
    monkeypatch.setattr(vc, "_request_direct_link_follow_redirects", _fake_head)
    monkeypatch.setattr(vc, "_download_direct_link_audio", _fake_download)
    # glm_tts 直链分支在函数内局部导入 normalize_voice_clone_api_audio（与 minimax
    # 分支同构），monkeypatch 必须打到 utils.audio 源头才能被运行时局部导入看到。
    monkeypatch.setattr("utils.audio.normalize_voice_clone_api_audio", _fake_normalize)

    payload = {
        "direct_link": "https://example.com/sample.wav",
        "prefix": "Miko",
        "ref_language": "ch",
        "provider": "glm_tts",
    }

    class _FakeRequest:
        async def json(self):
            return payload

    resp = await vc.voice_clone_direct(_FakeRequest())
    body = json.loads(resp.body)
    assert "voice_id" in body, f"unexpected response: {body} (status={getattr(resp, 'status_code', '?')})"

    assert body["voice_id"] == "voice_clone_direct_001"
    assert body["provider"] == "glm_tts"
    assert body["is_direct_link"] is True
    assert saved["storage_key"] == f"{GLM_VOICE_STORAGE_KEY}key-1234"
    assert saved["voice_data"]["provider"] == "glm_tts"
    assert saved["voice_data"]["is_direct_link"] is True
    assert saved["client_args"] == ("glm-key-1234", GLM_TTS_DEFAULT_BASE_URL)
    assert saved["audio"] == b"normalized-wav"
    assert saved["voice_name"].startswith("neko_miko_")


async def _call_glm_direct_link_clone(monkeypatch, *, download, normalize):
    """Drive /voice_clone_direct for glm_tts with stubbed download/normalize; return (status, body, client_calls)."""
    from main_routers.characters_router import voice_cloning as vc

    client_calls: list = []

    class _CM:
        def get_tts_api_key(self, provider):
            return "glm-key-1234"

        def find_voice_by_audio_md5(self, storage_key, audio_md5, ref_language):
            return None

        def save_voice_for_api_key(self, storage_key, voice_id, voice_data):
            raise AssertionError("must not persist on a rejected clone")

    class _FakeClient:
        def __init__(self, api_key, base_url=None):
            pass

        async def clone_voice(self, *args, **kwargs):
            client_calls.append(kwargs)
            return "should-not-happen"

    class _FakeHeadResp:
        status_code = 200

        async def aclose(self):
            pass

    async def _noop_validate(url):
        return None

    async def _fake_head(method, url, **kwargs):
        return _FakeHeadResp()

    monkeypatch.setattr(vc, "get_config_manager", lambda: _CM())
    monkeypatch.setattr(vc, "GlmVoiceCloneClient", _FakeClient)
    monkeypatch.setattr(vc, "_validate_direct_link_target", _noop_validate)
    monkeypatch.setattr(vc, "_request_direct_link_follow_redirects", _fake_head)
    monkeypatch.setattr(vc, "_download_direct_link_audio", download)
    monkeypatch.setattr("utils.audio.normalize_voice_clone_api_audio", normalize)

    payload = {
        "direct_link": "https://example.com/sample.wav",
        "prefix": "Miko",
        "ref_language": "ch",
        "provider": "glm_tts",
    }

    class _FakeRequest:
        async def json(self):
            return payload

    resp = await vc.voice_clone_direct(_FakeRequest())
    return resp.status_code, json.loads(resp.body), client_calls


@pytest.mark.unit
async def test_glm_direct_link_download_over_limit_returns_413(monkeypatch):
    """A direct link that exceeds the GLM 10MB cap while downloading must get the
    same 413 + GLM_TTS_AUDIO_TOO_LARGE as the post-normalize check, not a 400."""
    from main_routers.characters_router.direct_link import DirectLinkSecurityError

    seen: dict = {}

    async def _too_large(url, max_file_size=None):
        seen["max_file_size"] = max_file_size
        raise DirectLinkSecurityError("音频文件超过10MB限制", "FILE_TOO_LARGE")

    def _never_normalize(buffer, filename):
        raise AssertionError("normalize must not run after a failed download")

    status, body, client_calls = await _call_glm_direct_link_clone(
        monkeypatch, download=_too_large, normalize=_never_normalize,
    )
    assert status == 413
    assert body["code"] == "GLM_TTS_AUDIO_TOO_LARGE"
    assert body["provider"] == "glm_tts"
    assert "10MB" in body["error"]
    assert seen["max_file_size"] == 10 * 1024 * 1024
    assert client_calls == []


@pytest.mark.unit
async def test_glm_direct_link_undecodable_audio_returns_400(monkeypatch):
    """Downloadable but undecodable audio is a client input error (400), matching
    the file-upload route, not a 500 server error."""

    async def _download(url, max_file_size=None):
        return "sample.wav", b"not-audio"

    def _bad_normalize(buffer, filename):
        raise ValueError("无法解析或处理上传音频文件: bad data")

    status, body, client_calls = await _call_glm_direct_link_clone(
        monkeypatch, download=_download, normalize=_bad_normalize,
    )
    assert status == 400
    assert "无法解析" in body["error"]
    assert body["provider"] == "glm_tts"
    assert client_calls == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("max_file_size", "expected"),
    [(10 * 1024 * 1024, "超过10MB限制"), (100 * 1024 * 1024, "超过100MB限制")],
)
async def test_direct_link_download_too_large_message_uses_actual_limit(
    monkeypatch, max_file_size, expected,
):
    """FILE_TOO_LARGE must name the caller's max_file_size, not a hardcoded 100MB."""
    from main_routers.characters_router import direct_link as dl

    class _Content:
        async def iter_chunked(self, size):
            for _ in range(max_file_size // size + 2):
                yield b"x" * size

    class _Resp:
        status = 200
        headers: dict = {}
        url = "https://example.com/a.wav"
        content = _Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Session:
        def get(self, url, allow_redirects=False):
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def _validate(url):
        return dl.DirectLinkValidatedTarget(url=url, hostname="example.com", port=443, addr_info=[])

    monkeypatch.setattr(dl, "_validate_direct_link_target", _validate)
    monkeypatch.setattr(dl, "_open_pinned_direct_link_session", lambda target, timeout: _Session())

    with pytest.raises(dl.DirectLinkSecurityError) as excinfo:
        await dl._download_direct_link_audio("https://example.com/a.wav", max_file_size=max_file_size)
    assert excinfo.value.code == "FILE_TOO_LARGE"
    assert expected in str(excinfo.value)


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
    # 直链克隆已支持：/voice_clone_direct 的 valid_providers 含 glm_tts（下载音频后
    # 走两步注册），前端不得把 glm_tts 列入直链禁用名单。
    direct_link_fn = voice_clone_js.split("function isDirectLinkUnsupportedProvider")[1].split("}")[0]
    assert "'glm_tts'" not in direct_link_fn
    assert "'elevenlabs', 'glm_tts']" in router_py  # valid_providers 直达白名单
    assert "GlmTtsError" in router_py
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
