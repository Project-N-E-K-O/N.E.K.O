"""Review regressions exercise real adapters and routes with isolated HTTP."""

import json
from types import SimpleNamespace

import httpx
import pytest

from tests.unit.test_voice_management_providers import ConfigSnapshot, transport  # noqa: F401
from tests.unit.test_voice_management_revisions import isolated_api, upstream_transport
from tests.unit.test_voice_management_storage import DoubaoVoiceManager
from utils.dashscope_region import configure_dashscope_sdk_urls
from utils.voice_management import service
from utils.voice_management.providers import get_adapter


pytestmark = pytest.mark.integration_serial


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["cosyvoice", "cosyvoice_intl"])
@pytest.mark.parametrize("configured,host", [
    ("https://dashscope.aliyuncs.com/compatible-mode/v1", "dashscope.aliyuncs.com"),
    ("wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference", "dashscope-intl.aliyuncs.com"),
    ("https://dashscope-us.aliyuncs.com", "dashscope-us.aliyuncs.com"),
    ("https://untrusted-proxy.invalid/compatible-mode/v1", None),
    ("not-a-url", None),
    ("", None),
])
async def test_cosy_management_and_synthesis_share_effective_endpoint(provider, configured, host, monkeypatch, transport):
    cm = ConfigSnapshot()
    monkeypatch.setattr(cm, "get_cosyvoice_clone_runtime", lambda selected: {
        "api_key": "configured-secret", "base_url": configured,
    })
    adapter = get_adapter(provider)
    runtime = adapter.resolve_runtime(cm)
    expected_host = host or ("dashscope-intl.aliyuncs.com" if provider.endswith("_intl") else "dashscope.aliyuncs.com")
    assert runtime.base_url == f"https://{expected_host}/api/v1"
    metadata = adapter.import_metadata(runtime)
    sdk = SimpleNamespace()
    configure_dashscope_sdk_urls(sdk, metadata["dashscope_base_url"])
    assert sdk.base_http_api_url == runtime.base_url
    assert sdk.base_websocket_api_url == f"wss://{expected_host}/api-ws/v1/inference"
    queries = 0

    def upstream(request):
        nonlocal queries
        action = json.loads(request.content)["input"]["action"]
        if action == "list_voice":
            return httpx.Response(200, json={"output": {"voice_list": []}})
        if action == "update_voice":
            return httpx.Response(200, json={"output": {}})
        queries += 1
        return httpx.Response(200, json={"output": {
            "voice_id": "existing-voice", "status": "OK", "target_model": runtime.model,
            "gmt_modified": f"2026-10-06 10:00:0{queries}",
        }})

    seen = transport(upstream)
    from utils.voice_clone import QwenVoiceCloneClient

    async def upload(self, audio, filename):
        assert self.dashscope_base_url == runtime.base_url
        return "https://controlled-upload.invalid/reference.wav"

    monkeypatch.setattr(QwenVoiceCloneClient, "upload_file", upload)
    assert not (await adapter.list_voices(runtime)).voices
    assert (await adapter.get_voice(runtime, "existing-voice")).voice_id == "existing-voice"
    updated = await adapter.overwrite(runtime, "existing-voice", audio=b"controlled", filename="reference.wav")
    assert updated.voice_id == "existing-voice" and updated.status == "ready"
    assert len(seen) == 5
    assert all(request.url.host == expected_host for request in seen)
    assert all(request.headers["authorization"] == "Bearer configured-secret" for request in seen)
    assert all(request.url.path == "/api/v1/services/audio/tts/customization" for request in seen)
    monkeypatch.setattr(cm, "get_cosyvoice_clone_runtime", lambda selected: {
        "api_key": "configured-secret", "base_url": runtime.base_url,
    })
    assert adapter.resolve_runtime(cm).scope_id == runtime.scope_id


@pytest.mark.asyncio
@pytest.mark.parametrize("project", [False, True])
@pytest.mark.parametrize("reply,expected", [
    ("absent", "VOICE_NOT_FOUND"),
    ("forbidden", "unverified"),
    ("unconfigured", "unverified"),
    ("malformed", "UPSTREAM_INVALID_RESPONSE"),
    ("active", "verified"),
])
async def test_doubao_manual_import_distinguishes_absence_from_unavailable_lookup(project, reply, expected, monkeypatch):
    cm = DoubaoVoiceManager()
    if project:
        cm.raw["doubaoVoiceManagementProjectName"] = "controlled-project"
    if reply == "unconfigured":
        cm.raw.pop("doubaoVoiceManagementSecretKey")
    adapter = get_adapter("doubao_tts")
    runtime = adapter.resolve_runtime(cm)
    api = isolated_api(cm, monkeypatch)
    seen = []

    def upstream(request):
        seen.append(request)
        body = json.loads(request.content)
        assert body["SpeakerIDs"] == ["S_requested"]
        assert request.url.params["Action"] == "BatchListMegaTTSTrainStatus"
        assert request.url.host == "open.volcengineapi.com"
        if reply == "forbidden":
            return httpx.Response(200, json={"ResponseMetadata": {"Error": {"Code": "OperationDenied"}}})
        rows = [{"SpeakerID": "S_requested", "State": "Active", "Version": "V1"}] if reply == "active" and (not project or body["State"] == "Active") else []
        return httpx.Response(200, json={"Result": {"Statuses": None if reply == "malformed" else rows}})

    upstream_transport(monkeypatch, upstream)
    async with api:
        result = await api.post("/api/characters/voices/import", json={
            "provider": "doubao_tts", "remote_voice_id": "S_requested",
            "context_token": service.context_token(runtime),
        })
    if expected in {"verified", "unverified"}:
        assert result.status_code == 200 and result.json()["verification"] == expected
        assert cm.storage
        assert result.json()["voice_data"]["remote_voice_id"] == "S_requested"
        assert not result.json()["voice_data"]["can_overwrite"]
    else:
        assert result.status_code == (404 if expected == "VOICE_NOT_FOUND" else 502)
        assert result.json()["code"] == expected
        assert not cm.storage
    assert len(seen) == (0 if reply == "unconfigured" else 6 if project and reply == "absent" else 2 if project and reply == "active" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["minimax", "minimax_intl"])
@pytest.mark.parametrize("rows", [None, []])
async def test_minimax_empty_clone_category_keeps_manual_import_available(provider, rows, monkeypatch):
    from tests.unit.test_voice_management_storage import MemoryVoiceManager

    cm = MemoryVoiceManager()
    monkeypatch.setattr(cm, "get_tts_api_key", lambda selected: "configured-secret")
    adapter = get_adapter(provider)
    runtime = adapter.resolve_runtime(cm)
    api = isolated_api(cm, monkeypatch)
    seen = []

    def upstream(request):
        seen.append(request)
        assert request.url.path == "/v1/get_voice"
        return httpx.Response(200, json={"voice_cloning": rows, "base_resp": {"status_code": 0}})

    upstream_transport(monkeypatch, upstream)
    async with api:
        listing = await api.get("/api/characters/remote_voices", params={
            "provider": provider, "context_token": service.context_token(runtime),
        })
        assert listing.status_code == 200 and listing.json()["voices"] == [] and not cm.storage
        imported = await api.post("/api/characters/voices/import", json={
            "provider": provider, "remote_voice_id": "NotUsedYet", "context_token": service.context_token(runtime),
        })
        assert imported.status_code == 200 and imported.json()["verification"] == "unverified"
    assert len(seen) == 2
