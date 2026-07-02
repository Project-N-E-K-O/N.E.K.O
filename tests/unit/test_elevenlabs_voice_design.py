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
    from main_routers.characters_router import voice_providers as cr

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
    from main_routers.characters_router import voice_providers as cr
    _, too_short = __import__('importlib').import_module('main_routers.characters_router.voice_cloning')._validate_voice_design_description("short")
    assert too_short is not None and too_short.status_code == 400
    desc, ok = __import__('importlib').import_module('main_routers.characters_router.voice_cloning')._validate_voice_design_description("a warm gentle young woman voice")
    assert ok is None and desc.startswith("a warm")
    _, too_long = __import__('importlib').import_module('main_routers.characters_router.voice_cloning')._validate_voice_design_description("x" * 1001)
    assert too_long is not None and too_long.status_code == 400
