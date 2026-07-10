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

"""ElevenLabs voice design (text description → generated voice).

design lands as a normal ElevenLabs voice (source='design') and reuses the
existing ElevenLabs clone dispatch path, so no separate worker is needed.
"""

import json
from functools import partial

import httpx
import pytest

from main_logic import tts_client


# ── dispatch: a design voice routes through the ElevenLabs clone path ─────────

@pytest.mark.unit
def test_get_tts_worker_routes_design_voice_via_elevenlabs(monkeypatch):
    class _CM:
        def get_core_config(self):
            return {"assistApi": "qwen", "TTS_PROVIDER": "", "GPTSOVITS_ENABLED": False}

        def get_model_api_config(self, model_type):
            return {"is_custom": False}

        def get_tts_api_key(self, provider):
            return "el-key" if provider == "elevenlabs" else None

        def get_voices_for_current_api(self, for_listing=False):
            return {"eleven:designed1": {"provider": "elevenlabs", "source": "design"}}

    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM())

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen", has_custom_voice=True, voice_id="eleven:designed1",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.elevenlabs_tts_worker
    assert provider_key == "elevenlabs"
    assert api_key == "el-key"


@pytest.mark.unit
def test_registry_declares_design_for_elevenlabs():
    import utils.tts_provider_registry as reg
    el = reg.get("elevenlabs")
    assert el is not None and "design" in el.capabilities and "clone" in el.capabilities
    # design is advertised in the UI metadata the source-first picker reads
    meta = {m["key"]: m for m in reg.ui_metadata()}
    assert "design" in meta["elevenlabs"]["capabilities"]


@pytest.mark.unit
def test_registry_declares_design_for_cosyvoice():
    import main_logic.tts_client  # noqa: F401 - registers providers
    import utils.tts_provider_registry as reg

    cosy = reg.get("cosyvoice")
    assert cosy is not None and "design" in cosy.capabilities and "clone" in cosy.capabilities
    meta = {m["key"]: m for m in reg.ui_metadata()}
    assert "design" in meta["cosyvoice"]["capabilities"]
    assert "cosyvoice_intl" not in meta["cosyvoice"]["aliases"]
    assert reg.get("cosyvoice_intl") is None


@pytest.mark.unit
def test_registry_declares_design_for_minimax_and_mimo():
    import main_logic.tts_client  # noqa: F401 - registers providers
    import utils.tts_provider_registry as reg

    minimax = reg.get("minimax")
    assert minimax is not None and "design" in minimax.capabilities and "clone" in minimax.capabilities
    mimo = reg.get("mimo")
    assert mimo is not None and "design" in mimo.capabilities and "clone" in mimo.capabilities
    meta = {m["key"]: m for m in reg.ui_metadata()}
    assert "design" in meta["minimax"]["capabilities"]
    assert "minimax_intl" in meta["minimax"]["aliases"]
    assert reg.get("minimax_intl") is minimax
    assert "design" in meta["mimo"]["capabilities"]


@pytest.mark.unit
def test_cosyvoice_design_language_hints_are_limited_to_zh_en():
    from main_routers import characters_router as cr

    assert cr._cosyvoice_design_language_hints("ch") == ["zh"]
    assert cr._cosyvoice_design_language_hints("zh") == ["zh"]
    assert cr._cosyvoice_design_language_hints("en") == ["en"]
    assert cr._cosyvoice_design_language_hints("ru") == ["zh"]
    assert cr._cosyvoice_design_default_preview_text("en") == cr.VOICE_PREVIEW_TEXTS["en"]
    assert cr._cosyvoice_design_default_preview_text("ru") == cr.VOICE_PREVIEW_TEXTS["zh-CN"]
    assert cr._voice_design_preview_text("ru", "ch") == cr.VOICE_PREVIEW_TEXTS["ru"]
    assert cr._voice_design_preview_text(None, "en") == cr.VOICE_PREVIEW_TEXTS["en"]


# ── router design helpers: design previews → create-from-preview ──────────────

class _FakeElevenTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.requests = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        path = request.url.path
        self.requests.append({"path": path, "body": body, "headers": dict(request.headers)})
        if path.endswith("/text-to-voice/design"):
            return httpx.Response(200, json={
                "previews": [
                    {"generated_voice_id": "gen-1", "audio_base_64": "QUJD", "media_type": "audio/mpeg", "duration_secs": 1.2},
                    {"generated_voice_id": "gen-2", "audio_base_64": "REVG", "media_type": "audio/mpeg", "duration_secs": 1.1},
                ],
                "text": "hello there",
            })
        if path.endswith("/text-to-voice"):
            return httpx.Response(200, json={"voice_id": "vox123"})
        return httpx.Response(404, json={"error": "unexpected"})


@pytest.mark.unit
async def test_elevenlabs_design_previews_and_create(monkeypatch):
    from main_routers import characters_router as cr

    transport = _FakeElevenTransport()
    original = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(cr.httpx, "AsyncClient", patched)

    previews = await cr._elevenlabs_design_previews(
        api_key="el-key", base_url="https://api.elevenlabs.io",
        voice_description="a warm, gentle young woman with a soft voice",
    )
    assert [p["generated_voice_id"] for p in previews] == ["gen-1", "gen-2"]
    design_req = transport.requests[0]
    assert design_req["path"].endswith("/v1/text-to-voice/design")
    assert design_req["body"]["voice_description"].startswith("a warm")
    # text (≥100 chars) must be sent so previews carry audible audio; auto_generate_text
    # (ids-only, no audio) must NOT be used.
    assert "auto_generate_text" not in design_req["body"]
    assert len(design_req["body"]["text"]) >= 100
    assert design_req["headers"]["xi-api-key"] == "el-key"

    voice_id = await cr._elevenlabs_create_voice_from_preview(
        api_key="el-key", base_url="https://api.elevenlabs.io",
        voice_name="Aria", voice_description="a warm, gentle young woman",
        generated_voice_id="gen-1",
    )
    # create-from-preview yields a normal (prefixed) ElevenLabs voice id
    assert voice_id == "eleven:vox123"
    create_req = transport.requests[1]
    assert create_req["path"].endswith("/v1/text-to-voice")
    assert create_req["body"]["generated_voice_id"] == "gen-1"
    assert create_req["body"]["voice_name"] == "Aria"


@pytest.mark.unit
def test_voice_design_description_validation():
    from main_routers import characters_router as cr
    _, too_short = cr._validate_voice_design_description("short")
    assert too_short is not None and too_short.status_code == 400
    desc, ok = cr._validate_voice_design_description("a warm gentle young woman voice")
    assert ok is None and desc.startswith("a warm")
    _, too_long = cr._validate_voice_design_description("x" * 1001)
    assert too_long is not None and too_long.status_code == 400


class _FakeCosyVoiceDesignTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.requests = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append({
            "url": str(request.url),
            "body": body,
            "headers": dict(request.headers),
        })
        return httpx.Response(200, json={
            "output": {
                "voice_id": "cosyvoice-v3.5-plus-demo",
                "preview_audio": {
                    "data": "UklGRg==",
                    "response_format": "wav",
                    "sample_rate": 24000,
                },
            },
            "request_id": "req-cosy-design",
        })


class _FailingCosyVoiceDesignTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 11001] getaddrinfo failed", request=request)


class _UrlCosyVoiceDesignTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "output": {
                "voice_id": "cosyvoice-v3.5-plus-demo",
                "preview_audio": {
                    "audio_url": "https://dashscope-preview.example.com/preview.wav",
                    "response_format": "wav",
                },
            },
            "request_id": "req-cosy-url",
        })


class _FakeMiniMaxDesignTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.requests = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append({
            "url": str(request.url),
            "body": body,
            "headers": dict(request.headers),
        })
        return httpx.Response(200, json={
            "voice_id": "mini-design-1",
            "trial_audio": "UklGRg==",
            "base_resp": {"status_code": 0, "status_msg": "success"},
            "request_id": "req-minimax-design",
        })


@pytest.mark.unit
async def test_cosyvoice_design_payload_and_parse(monkeypatch):
    from main_routers import characters_router as cr

    transport = _FakeCosyVoiceDesignTransport()
    async with httpx.AsyncClient(transport=transport) as client:
        voice_id, preview_audio, media_type, request_id = await cr._cosyvoice_design_voice(
            api_key="cosy-key",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            prefix="aria",
            voice_prompt="a warm clear voice",
            preview_text="hello there",
            ref_language="ch",
            target_model="cosyvoice-v3.5-plus",
            http_client=client,
        )

    assert voice_id == "cosyvoice-v3.5-plus-demo"
    assert preview_audio == "UklGRg=="
    assert media_type == "audio/wav"
    assert request_id == "req-cosy-design"

    req = transport.requests[0]
    assert req["url"] == "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
    assert req["headers"]["authorization"] == "Bearer cosy-key"
    assert req["body"]["model"] == "voice-enrollment"
    assert req["body"]["input"] == {
        "action": "create_voice",
        "target_model": "cosyvoice-v3.5-plus",
        "voice_prompt": "a warm clear voice",
        "preview_text": "hello there",
        "prefix": "aria",
        "language_hints": ["zh"],
    }
    assert req["body"]["parameters"] == {"sample_rate": 24000, "response_format": "wav"}


@pytest.mark.unit
async def test_cosyvoice_design_parses_preview_audio_url():
    from main_routers import characters_router as cr

    async with httpx.AsyncClient(transport=_UrlCosyVoiceDesignTransport()) as client:
        voice_id, preview_audio, media_type, request_id = await cr._cosyvoice_design_voice(
            api_key="cosy-key",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            prefix="aria",
            voice_prompt="a warm clear voice",
            preview_text="hello there",
            ref_language="ch",
            target_model="cosyvoice-v3.5-plus",
            http_client=client,
        )

    assert voice_id == "cosyvoice-v3.5-plus-demo"
    assert preview_audio == "https://dashscope-preview.example.com/preview.wav"
    assert media_type == "audio/wav"
    assert request_id == "req-cosy-url"


@pytest.mark.unit
async def test_minimax_design_payload_and_parse():
    from main_routers import characters_router as cr

    transport = _FakeMiniMaxDesignTransport()
    async with httpx.AsyncClient(transport=transport) as client:
        voice_id, request_id = await cr._minimax_design_voice(
            api_key="mini-key",
            base_url="https://api.minimax.io",
            voice_id="aria12345678",
            voice_prompt="a warm clear voice",
            preview_text="hello there",
            http_client=client,
        )

    assert voice_id == "mini-design-1"
    assert request_id == "req-minimax-design"
    req = transport.requests[0]
    assert req["url"] == "https://api.minimax.io/v1/voice_design"
    assert req["headers"]["authorization"] == "Bearer mini-key"
    assert req["body"] == {
        "prompt": "a warm clear voice",
        "preview_text": "hello there",
    }


@pytest.mark.unit
async def test_cosyvoice_design_network_error_is_actionable():
    from main_routers import characters_router as cr

    async with httpx.AsyncClient(transport=_FailingCosyVoiceDesignTransport()) as client:
        with pytest.raises(cr.QwenVoiceCloneError) as exc_info:
            await cr._cosyvoice_design_voice(
                api_key="cosy-key",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                prefix="aria",
                voice_prompt="a warm clear voice",
                preview_text="hello there",
                ref_language="ch",
                target_model="cosyvoice-v3.5-plus",
                http_client=client,
            )

    message = str(exc_info.value)
    assert "dashscope.aliyuncs.com" in message
    assert "DNS" in message
    assert "proxy" in message


class _JsonRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


@pytest.mark.unit
async def test_cosyvoice_design_endpoint_saves_source_design(monkeypatch):
    from main_routers import characters_router as cr

    saved = {}

    class _CM:
        def get_cosyvoice_clone_runtime(self, provider):
            assert provider == "cosyvoice"
            return {
                "api_key": "cosy-key",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "storage_key": "cosy-key",
                "provider_label": "Alibaba Bailian CosyVoice",
            }

        async def asave_voice_for_api_key(self, storage_key, voice_id, voice_data):
            saved["storage_key"] = storage_key
            saved["voice_id"] = voice_id
            saved["voice_data"] = voice_data

    async def fake_design(**kwargs):
        assert kwargs["api_key"] == "cosy-key"
        assert kwargs["prefix"] == "aria"
        assert kwargs["preview_text"] == cr.VOICE_PREVIEW_TEXTS["zh-CN"]
        return "cosy-design-1", "UklGRg==", "audio/wav", "req-1"

    monkeypatch.setattr(cr, "get_config_manager", lambda: _CM())
    monkeypatch.setattr(cr, "_cosyvoice_design_voice", fake_design)

    response = await cr.voice_design(_JsonRequest({
        "provider": "cosyvoice",
        "prefix": "aria",
        "voice_prompt": "a warm clear voice",
        "preview_text": "This caller-supplied text must be ignored.",
        "ref_language": "ch",
    }))
    body = json.loads(response.body)

    assert response.status_code == 200
    assert body["voice_id"] == "cosy-design-1"
    assert body["source"] == "design"
    assert "preview_audio" not in body
    assert saved["storage_key"] == "cosy-key"
    assert saved["voice_id"] == "cosy-design-1"
    assert saved["voice_data"]["source"] == "design"
    assert saved["voice_data"]["provider"] == "cosyvoice"
    assert saved["voice_data"]["design_prompt"] == "a warm clear voice"
    assert saved["voice_data"]["preview_text"] == cr.VOICE_PREVIEW_TEXTS["zh-CN"]


@pytest.mark.unit
async def test_cosyvoice_intl_design_endpoint_is_rejected():
    from main_routers import characters_router as cr

    response = await cr.voice_design(_JsonRequest({
        "provider": "cosyvoice_intl",
        "prefix": "aria",
        "voice_prompt": "a warm clear voice",
        "ref_language": "en",
    }))
    body = json.loads(response.body)

    assert response.status_code == 400
    assert body["code"] == "VOICE_DESIGN_PROVIDER_UNSUPPORTED"


@pytest.mark.unit
async def test_minimax_design_endpoint_saves_source_design(monkeypatch):
    from main_routers import characters_router as cr

    saved = {}

    class _CM:
        def get_tts_api_key(self, provider):
            assert provider == "minimax_intl"
            return "minimax-intl-key"

        async def asave_voice_for_api_key(self, storage_key, voice_id, voice_data):
            saved["storage_key"] = storage_key
            saved["voice_id"] = voice_id
            saved["voice_data"] = voice_data

    async def fake_design(**kwargs):
        assert kwargs["api_key"] == "minimax-intl-key"
        assert kwargs["base_url"] == "https://api.minimax.io"
        assert kwargs["voice_prompt"] == "a warm clear voice"
        assert kwargs["preview_text"] == cr.VOICE_PREVIEW_TEXTS["en"]
        return "mini-design-1", "req-mini"

    monkeypatch.setattr(cr, "get_config_manager", lambda: _CM())
    monkeypatch.setattr(cr, "_minimax_design_voice", fake_design)

    response = await cr.voice_design(_JsonRequest({
        "provider": "minimax_intl",
        "prefix": "aria",
        "voice_prompt": "a warm clear voice",
        "ref_language": "en",
    }))
    body = json.loads(response.body)

    assert response.status_code == 200
    assert body["voice_id"] == "mini-design-1"
    assert body["provider"] == "minimax_intl"
    assert saved["storage_key"].startswith("__MINIMAX_INTL__")
    assert saved["voice_data"]["provider"] == "minimax_intl"
    assert saved["voice_data"]["source"] == "design"
    assert saved["voice_data"]["minimax_base_url"] == "https://api.minimax.io"


@pytest.mark.unit
async def test_elevenlabs_design_endpoint_saves_source_design(monkeypatch):
    from main_routers import characters_router as cr

    saved = {}

    class _CM:
        def get_tts_api_key(self, provider):
            assert provider == "elevenlabs"
            return "elevenlabs-key"

        async def asave_voice_for_api_key(self, storage_key, voice_id, voice_data):
            saved.update(storage_key=storage_key, voice_id=voice_id, voice_data=voice_data)

    async def fake_base_url(_cm):
        return "https://api.elevenlabs.io"

    async def fake_previews(**kwargs):
        assert kwargs["voice_description"] == "a warm and clear young adult voice"
        return [{"generated_voice_id": "generated-1"}]

    async def fake_create(**kwargs):
        assert kwargs["generated_voice_id"] == "generated-1"
        return "eleven:designed-1"

    monkeypatch.setattr(cr, "get_config_manager", lambda: _CM())
    monkeypatch.setattr(cr, "_get_elevenlabs_base_url", fake_base_url)
    monkeypatch.setattr(cr, "_elevenlabs_design_previews", fake_previews)
    monkeypatch.setattr(cr, "_elevenlabs_create_voice_from_preview", fake_create)

    response = await cr.voice_design(_JsonRequest({
        "provider": "elevenlabs",
        "prefix": "aria",
        "voice_prompt": "a warm and clear young adult voice",
        "ref_language": "en",
    }))
    body = json.loads(response.body)

    assert response.status_code == 200
    assert body == {
        "voice_id": "eleven:designed-1",
        "message": "ElevenLabs voice design succeeded",
        "provider": "elevenlabs",
        "source": "design",
    }
    assert saved["voice_data"]["source"] == "design"
    assert saved["voice_data"]["provider"] == "elevenlabs"
    assert saved["storage_key"] == "__ELEVENLABS__labs-key"
    assert saved["voice_id"] == "eleven:designed-1"
    assert saved["voice_data"]["raw_voice_id"] == "designed-1"
    assert saved["voice_data"]["design_description"] == "a warm and clear young adult voice"
    assert saved["voice_data"]["generated_voice_id"] == "generated-1"
    assert saved["voice_data"]["elevenlabs_base_url"] == "https://api.elevenlabs.io"


@pytest.mark.unit
async def test_mimo_design_endpoint_saves_source_design(monkeypatch):
    from main_routers import characters_router as cr

    saved = {}
    validated = {}

    class _CM:
        def get_tts_api_key(self, provider):
            assert provider == "mimo"
            return "mimo-api-key"

        def get_core_config(self):
            return {"assistApi": "free"}

        async def asave_voice_for_api_key(self, storage_key, voice_id, voice_data):
            saved.update(storage_key=storage_key, voice_id=voice_id, voice_data=voice_data)

    class _MimoClient:
        def __init__(self, *, api_key, base_url=None):
            assert api_key == "mimo-api-key"
            assert base_url is None

        async def validate_design_prompt(self, prompt, *, sample_text):
            validated.update(prompt=prompt, sample_text=sample_text)

    monkeypatch.setattr(cr, "get_config_manager", lambda: _CM())
    monkeypatch.setattr(cr, "MimoVoiceCloneClient", _MimoClient)

    response = await cr.voice_design(_JsonRequest({
        "provider": "mimo",
        "prefix": "aria",
        "voice_prompt": "a bright, energetic young adult voice",
        "ref_language": "en",
    }))
    body = json.loads(response.body)

    assert response.status_code == 200
    assert body["provider"] == "mimo"
    assert body["source"] == "design"
    assert body["voice_id"].startswith("mimo-design-aria-")
    assert validated["sample_text"] == cr.VOICE_PREVIEW_TEXTS["en"]
    assert saved["voice_data"]["design_prompt"] == "a bright, energetic young adult voice"


@pytest.mark.unit
async def test_voice_design_endpoint_requires_prompt():
    from main_routers import characters_router as cr

    response = await cr.voice_design(_JsonRequest({
        "provider": "cosyvoice",
        "prefix": "aria",
    }))
    body = json.loads(response.body)

    assert response.status_code == 400
    assert body["code"] == "VOICE_DESIGN_PROMPT_REQUIRED"


@pytest.mark.unit
async def test_voice_design_endpoint_rejects_vllm_omni_provider():
    from main_routers import characters_router as cr

    response = await cr.voice_design(_JsonRequest({
        "provider": "vllm_omni",
        "prefix": "aria",
        "voice_prompt": "a warm clear voice",
        "preview_text": "hello there",
    }))
    body = json.loads(response.body)

    assert response.status_code == 400
    assert body["code"] == "VOICE_DESIGN_PROVIDER_UNSUPPORTED"


@pytest.mark.unit
async def test_cosyvoice_design_endpoint_rejects_underscore_prefix():
    from main_routers import characters_router as cr

    response = await cr.voice_design(_JsonRequest({
        "provider": "cosyvoice",
        "prefix": "cosy_test6",
        "voice_prompt": "a warm clear voice",
        "preview_text": "hello there",
    }))
    body = json.loads(response.body)

    assert response.status_code == 400
    assert body["code"] == "VOICE_DESIGN_PREFIX_INVALID"
    assert "Underscores" in body["message"]
