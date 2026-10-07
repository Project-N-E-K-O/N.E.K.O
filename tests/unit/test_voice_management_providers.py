"""Actual adapter HTTP requests with a transport that cannot reach real vendors."""

import asyncio
import hashlib
import json

import httpx
import pytest

from utils.voice_management.providers import get_adapter
from utils.voice_management.providers._shared import numeric_cursor, remote_date, runtime_for
from utils.voice_management.providers.doubao import _parse_cursor, _signed_headers
from utils.voice_management.types import VoiceManagementError


def runtime(provider, *, settings=None):
    base = {
        "cosyvoice": "https://dashscope.aliyuncs.com/api/v1",
        "cosyvoice_intl": "https://dashscope-intl.aliyuncs.com/api/v1",
        "minimax": "https://api.minimaxi.com", "minimax_intl": "https://api.minimax.io",
        "elevenlabs": "https://api.elevenlabs.io", "doubao_tts": "https://openspeech.bytedance.com",
        "glm_tts": "https://open.bigmodel.cn/api/paas/v4",
    }[provider]
    model = "cosyvoice-v3.5-plus" if provider.startswith("cosyvoice") else ""
    resource = "seed-icl-2.0" if provider == "doubao_tts" else ""
    return runtime_for(provider, "secret-synthesis-key", base, model=model, resource_id=resource, settings=settings)


@pytest.fixture
def transport(monkeypatch):
    original = httpx.AsyncClient

    def install(handler):
        seen = []

        def intercept(request):
            seen.append(request)
            return handler(request)

        monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: original(transport=httpx.MockTransport(intercept)))
        return seen

    return install


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["minimax", "minimax_intl"])
async def test_minimax_request_clone_filter_and_manual_absence(provider, transport):
    rt = runtime(provider)
    seen = transport(lambda req: httpx.Response(200, json={"system_voice": [{"voice_id": "system"}], "voice_cloning": [{"voice_id": "Existing123", "created_time": "2025-08-20"}], "base_resp": {"status_code": 0}}))
    adapter = get_adapter(provider)
    page = await adapter.list_voices(rt, query="existing")
    assert [v.voice_id for v in page.voices] == ["Existing123"]
    assert page.voices[0].created_at == "2025-08-20"
    assert str(seen[0].url) == rt.base_url + "/v1/get_voice"
    assert json.loads(seen[0].content) == {"voice_type": "voice_cloning"}
    assert seen[0].headers["authorization"] == "Bearer secret-synthesis-key"
    assert await adapter.get_voice(rt, "NotUsedYet") is None
    assert (await adapter.get_voice(rt, "Existing123")).status == "ready"
    assert all("clone" not in req.url.path for req in seen)
    assert adapter.import_metadata(rt) == {"minimax_base_url": rt.base_url}


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,code", [
    ({"base_resp": {"status_code": 1004}}, "AUTH_FAILED"),
    ({"base_resp": {"status_code": 1002}}, "RATE_LIMITED"),
    ({"base_resp": {"status_code": 999}}, "UPSTREAM_REJECTED"),
    ({"base_resp": "secret"}, "UPSTREAM_REJECTED"),
    ({"base_resp": {"status_code": 0}, "voice_cloning": {}}, "UPSTREAM_INVALID_RESPONSE"),
])
async def test_minimax_body_failures_are_safe(payload, code, transport):
    transport(lambda req: httpx.Response(200, json=payload))
    with pytest.raises(VoiceManagementError) as caught:
        await get_adapter("minimax").list_voices(runtime("minimax"))
    assert caught.value.code == code
    assert caught.value.details == {}


@pytest.mark.asyncio
async def test_glm_private_wire_schema_and_details(transport):
    seen = transport(lambda req: httpx.Response(200, json={"voice_list": [{"voice": "raw-glm", "voice_name": "名称", "voice_type": "PRIVATE", "create_time": "2024-03-15 14:30:52"}, {"voice": "official", "voice_type": "OFFICIAL"}]}))
    adapter, rt = get_adapter("glm_tts"), runtime("glm_tts")
    page = await adapter.list_voices(rt, query="raw-glm")
    assert page.voices[0].voice_id == "raw-glm"
    assert page.voices[0].name == "名称"
    assert seen[0].method == "GET"
    assert seen[0].url.params["voiceType"] == "PRIVATE"
    assert seen[0].url.path == "/api/paas/v4/voice/list"
    assert (await adapter.get_voice(rt, "raw-glm")).created_at == "2024-03-15 14:30:52"
    with pytest.raises(VoiceManagementError, match="VOICE_NOT_FOUND"):
        await adapter.get_voice(rt, "missing")


@pytest.mark.asyncio
async def test_elevenlabs_filters_defaults_and_keeps_cursor(transport):
    seen = transport(lambda req: httpx.Response(200, json={"voices": [{"voice_id": "premade", "category": "premade"}, {"voice_id": "clone", "category": "cloned", "created_at_unix": 1714204800}, {"voice_id": "pvc", "category": "professional", "fine_tuning": {"state": {"eleven_v3": "fine_tuned"}}}, {"voice_id": "pending", "category": "professional", "fine_tuning": {"state": {"eleven_v3": "queued"}}}], "has_more": True, "next_page_token": "page-two"}))
    rt, adapter = runtime("elevenlabs"), get_adapter("elevenlabs")
    page = await adapter.list_voices(rt)
    assert [(v.voice_id, v.status) for v in page.voices] == [("clone", "ready"), ("pvc", "ready"), ("pending", "processing")]
    assert page.next_cursor == "page-two"
    assert seen[0].url.path == "/v2/voices"
    assert seen[0].url.params["voice_type"] == "non-community"
    assert page.voices[0].created_at.endswith("+00:00")
    assert all(not voice.can_overwrite for voice in page.voices)
    await adapter.list_voices(rt, cursor="different-page", query="pending")
    assert seen[-1].url.params["next_page_token"] == "different-page"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"voices": [], "has_more": True}, {"voices": [], "has_more": True, "next_page_token": "same"}, {"voices": [{"voice_id": "clone", "category": "cloned", "fine_tuning": "wrong"}]}])
async def test_elevenlabs_bad_pagination_and_schema(payload, transport):
    transport(lambda req: httpx.Response(200, json=payload))
    with pytest.raises(VoiceManagementError, match="UPSTREAM_INVALID_RESPONSE"):
        await get_adapter("elevenlabs").list_voices(runtime("elevenlabs"), cursor="same")


@pytest.mark.asyncio
@pytest.mark.parametrize("status,payload", [(404, {}), (200, {"voice_id": "system", "category": "premade"})])
async def test_elevenlabs_details_reject_missing_or_system(status, payload, transport):
    seen = transport(lambda req: httpx.Response(status, json=payload))
    with pytest.raises(VoiceManagementError, match="VOICE_NOT_FOUND"):
        await get_adapter("elevenlabs").get_voice(runtime("elevenlabs"), "remote-id")
    assert seen[0].url.raw_path == b"/v1/voices/remote-id"


@pytest.mark.parametrize("url,expected", [
    ("https://dashscope.aliyuncs.com/compatible-mode/v1", "https://dashscope.aliyuncs.com/api/v1"),
    ("wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference", "https://dashscope-intl.aliyuncs.com/api/v1"),
    ("https://dashscope-us.aliyuncs.com", "https://dashscope-us.aliyuncs.com/api/v1"),
    ("http://proxy.local/api/v1", "https://dashscope.aliyuncs.com/api/v1"),
])
def test_cosy_region_http_endpoint(url, expected, monkeypatch):
    cm = ConfigSnapshot()
    monkeypatch.setattr(cm, "get_cosyvoice_clone_runtime", lambda provider: {
        "api_key": "configured-secret", "base_url": url,
    })
    assert get_adapter("cosyvoice").resolve_runtime(cm).base_url == expected


@pytest.mark.asyncio
async def test_cosy_list_pagination_and_detail_model(transport):
    def handler(req):
        body = json.loads(req.content)["input"]
        if body["action"] == "list_voice":
            assert body["page_index"] == 3
            return httpx.Response(200, json={"output": {"voice_list": [{"voice_id": f"id{i}", "status": "OK"} for i in range(100)]}})
        return httpx.Response(200, json={"output": {"status": "OK", "target_model": "cosyvoice-v3.5-flash", "gmt_create": "2026-01-01", "gmt_modified": "revision1"}})

    seen = transport(handler)
    adapter, rt = get_adapter("cosyvoice_intl"), runtime("cosyvoice_intl")
    page = await adapter.list_voices(rt, cursor="3", query="id10")
    assert page.next_cursor == "4"
    assert [v.voice_id for v in page.voices] == ["id10"]
    voice = await adapter.get_voice(rt, "Existing-remote")
    assert voice.metadata["clone_model"] == "cosyvoice-v3.5-flash"
    assert voice.metadata["remote_revision"] == "revision1"
    assert voice.can_overwrite
    assert all(req.url.host == "dashscope-intl.aliyuncs.com" for req in seen)


@pytest.mark.asyncio
async def test_cosy_overwrite_retains_id_and_checks_after_upload(monkeypatch, transport):
    from utils import voice_clone
    actions, guards = [], []

    def handler(req):
        body = json.loads(req.content)["input"]
        actions.append(body)
        if body["action"] == "update_voice":
            assert guards == ["checked"]
            assert body["voice_id"] == "raw-cosy"
            return httpx.Response(200, json={"output": {}})
        revision = "2026-10-06 10:00:01" if len(actions) == 1 else "2026-10-06 10:00:02"
        return httpx.Response(200, json={"output": {"status": "OK", "target_model": "cosyvoice-v3.5-plus", "gmt_modified": revision}})

    class Uploader:
        def __init__(self, *args):
            pass

        async def upload_file(self, audio, filename):
            assert audio.read() == b"sample"
            assert guards == []
            return "https://sample.test/audio.wav"

    async def guard(current):
        assert current.voice_id == "raw-cosy" and current.metadata["remote_revision"] == "2026-10-06 10:00:01"
        guards.append("checked")

    monkeypatch.setattr(voice_clone, "QwenVoiceCloneClient", Uploader)
    transport(handler)
    result = await get_adapter("cosyvoice").overwrite(runtime("cosyvoice", settings={"upload_url": "https://upload.test"}), "raw-cosy", audio=b"sample", filename="sample.wav", before_mutation=guard)
    assert result.voice_id == "raw-cosy"
    assert result.status == "ready"
    assert [body["action"] for body in actions] == ["query_voice", "update_voice", "query_voice"]


@pytest.mark.asyncio
async def test_cosy_context_changed_after_upload_prevents_update(monkeypatch, transport):
    from utils import voice_clone
    seen = transport(lambda req: httpx.Response(200, json={"output": {"status": "OK", "target_model": "cosyvoice-v3.5-plus"}}))

    class Uploader:
        def __init__(self, *args):
            pass

        async def upload_file(self, *args):
            return "https://sample.test/audio.wav"

    async def guard(current):
        raise VoiceManagementError("CONTEXT_CHANGED", 409)

    monkeypatch.setattr(voice_clone, "QwenVoiceCloneClient", Uploader)
    with pytest.raises(VoiceManagementError, match="CONTEXT_CHANGED"):
        await get_adapter("cosyvoice").overwrite(runtime("cosyvoice", settings={"upload_url": "https://upload.test"}), "raw-cosy", audio=b"sample", filename="sample.wav", before_mutation=guard)
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("followup", ["same_revision", "failed_query", "missing_revision", "missing_previous_revision"])
async def test_cosy_acknowledged_update_does_not_claim_old_ready_completed(monkeypatch, transport, followup):
    from utils import voice_clone
    actions = []

    def handler(req):
        action = json.loads(req.content)["input"]["action"]
        actions.append(action)
        if action == "update_voice":
            return httpx.Response(200, json={"output": {}})
        if len(actions) == 3 and followup == "failed_query":
            return httpx.Response(503, json={"secret": "must not leak"})
        output = {"target_model": "cosyvoice-v3.5-plus", "status": "OK", "gmt_modified": "2026-10-06 10:00:01"}
        if followup == "missing_revision" and len(actions) == 3 or followup == "missing_previous_revision" and len(actions) == 1:
            output.pop("gmt_modified")
        return httpx.Response(200, json={"output": output})

    class Uploader:
        def __init__(self, *args):
            pass

        async def upload_file(self, *args):
            return "https://sample.test/audio.wav"

    monkeypatch.setattr(voice_clone, "QwenVoiceCloneClient", Uploader)
    transport(handler)
    updated = await get_adapter("cosyvoice").overwrite(runtime("cosyvoice", settings={"upload_url": "https://upload.test"}), "raw", audio=b"sample", filename="sample.wav")
    assert updated.status == "processing"
    assert len(actions) == 3


def management_settings(*, project=False):
    return {"access_key": "test-AK", "secret_key": "test-SK", "app_id": "app123" if not project else "", "project_name": "test-project" if project else ""}


@pytest.mark.asyncio
async def test_doubao_legacy_app_request_and_voice_state(transport):
    seen = transport(lambda req: httpx.Response(200, json={"ResponseMetadata": {}, "Result": {"Statuses": [{"SpeakerID": "S_ready", "State": "Success", "Alias": "name", "CreateTime": 1700727790000, "AvailableTrainingTimes": 2, "Version": "v1"}, {"SpeakerID": "S_active", "State": "Active", "AvailableTrainingTimes": 2}], "NextToken": "next"}}))
    adapter, rt = get_adapter("doubao_tts"), runtime("doubao_tts", settings=management_settings())
    page = await adapter.list_voices(rt)
    body = json.loads(seen[0].content)
    assert body["AppID"] == "app123" and "ProjectName" not in body
    assert seen[0].url.params["Version"] == "2023-11-07"
    assert "cn-north-1/speech_saas_prod/request" in seen[0].headers["authorization"]
    assert seen[0].headers["x-content-sha256"] == hashlib.sha256(seen[0].content).hexdigest()
    assert page.next_cursor == "next"
    assert page.voices[0].can_overwrite and not page.voices[1].can_overwrite
    assert page.voices[0].metadata["remote_revision"] == "v1"
    assert page.voices[0].created_at.startswith("2023-")
    assert "secret" not in json.dumps(page.voices[0].metadata)


@pytest.mark.asyncio
async def test_doubao_new_project_cursor_traverses_active_state(transport):
    seen_states = []

    def handler(req):
        body = json.loads(req.content)
        assert body["ProjectName"] == "test-project" and "AppID" not in body
        assert req.url.params["Version"] == "2025-05-21"
        assert "cn-beijing/speech_saas_prod/request" in req.headers["authorization"]
        seen_states.append(body["State"])
        rows = [] if body["State"] == "Success" else [{"SpeakerID": "S_active", "State": "Active"}]
        return httpx.Response(200, json={"Result": {"Statuses": rows, "NextToken": ""}})

    transport(handler)
    rt, adapter = runtime("doubao_tts", settings=management_settings(project=True)), get_adapter("doubao_tts")
    page = await adapter.list_voices(rt)
    assert seen_states == ["Success", "Active"]
    assert [v.voice_id for v in page.voices] == ["S_active"]
    assert _parse_cursor(page.next_cursor) == (2, None)
    assert not page.voices[0].can_overwrite
    assert (await adapter.get_voice(rt, "S_active")).status == "ready"


@pytest.mark.asyncio
async def test_doubao_missing_management_allows_manual_import(transport):
    seen = transport(lambda req: pytest.fail("must not request with missing management credentials"))
    rt, adapter = runtime("doubao_tts"), get_adapter("doubao_tts")
    caps = adapter.capabilities_for(rt)
    assert caps.manual_import and caps.overwrite and not caps.details and not caps.list_voices
    assert await adapter.get_voice(rt, "S_manual") is None
    with pytest.raises(VoiceManagementError, match="MANAGEMENT_CONFIG_MISSING"):
        await adapter.list_voices(rt)
    assert seen == []
    assert adapter.import_metadata(rt)["doubao_resource_id"] == "seed-icl-2.0"


@pytest.mark.asyncio
async def test_doubao_overwrite_uses_existing_client_and_never_changes_speaker(transport):
    calls, guards = [], []

    def handler(req):
        body = json.loads(req.content)
        calls.append(req.url.path)
        if req.url.path == "/api/v3/tts/voice_clone":
            assert body["speaker_id"] == "S_existing"
            assert body["audio"]["format"] == "wav"
            assert guards == [True]
            return httpx.Response(200, json={"code": 0, "data": {"speaker_id": "S_existing"}})
        return httpx.Response(200, json={"Result": {"Statuses": [{"SpeakerID": "S_existing", "State": "Success", "Version": "v1" if len(calls) == 1 else "v2", "AvailableTrainingTimes": 2}], "NextToken": ""}})

    async def guard(current):
        assert current.voice_id == "S_existing" and current.metadata["remote_revision"] == "v1"
        guards.append(True)

    transport(handler)
    voice = await get_adapter("doubao_tts").overwrite(runtime("doubao_tts", settings=management_settings()), "S_existing", audio=b"sample", filename="sample.wav", before_mutation=guard)
    assert voice.voice_id == "S_existing" and voice.status == "ready"
    assert calls == ["/", "/api/v3/tts/voice_clone", "/"]


@pytest.mark.asyncio
@pytest.mark.parametrize("http_status,expected", [(401, "AUTH_FAILED"), (403, "PERMISSION_DENIED"), (429, "RATE_LIMITED"), (500, "UPSTREAM_UNAVAILABLE"), (302, "UPSTREAM_REJECTED"), (400, "UPSTREAM_REJECTED")])
async def test_common_http_errors_never_forward_upstream_secrets(http_status, expected, transport):
    transport(lambda req: httpx.Response(http_status, text="secret-key secret-response", headers={"location": "https://danger.test"}))
    with pytest.raises(VoiceManagementError) as caught:
        await get_adapter("glm_tts").list_voices(runtime("glm_tts"))
    assert caught.value.code == expected
    assert "secret" not in str(caught.value) and caught.value.details == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"invalid-json", b"[]", b"null"])
async def test_invalid_json_is_not_empty_success(payload, transport):
    transport(lambda req: httpx.Response(200, content=payload))
    with pytest.raises(VoiceManagementError, match="UPSTREAM_INVALID_RESPONSE"):
        await get_adapter("glm_tts").list_voices(runtime("glm_tts"))


@pytest.mark.asyncio
async def test_timeout_does_not_retry_mutation(transport):
    def handler(req):
        raise httpx.ReadTimeout("private upstream info", request=req)

    seen = transport(handler)
    adapter, rt = get_adapter("cosyvoice"), runtime("cosyvoice")
    with pytest.raises(VoiceManagementError, match="UPSTREAM_TIMEOUT"):
        await adapter.list_voices(rt)
    with pytest.raises(VoiceManagementError, match="UPDATE_OUTCOME_UNKNOWN"):
        await adapter._call(rt, "update_voice", mutation=True, voice_id="raw", url="https://sample.test")
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_server_error_after_mutation_is_unknown(transport):
    seen = transport(lambda req: httpx.Response(503, text="server may have committed before error"))
    with pytest.raises(VoiceManagementError, match="UPDATE_OUTCOME_UNKNOWN"):
        await get_adapter("cosyvoice")._call(runtime("cosyvoice"), "update_voice", mutation=True, voice_id="raw", url="https://sample.test")
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_cancellation_is_propagated(transport):
    def handler(req):
        raise asyncio.CancelledError

    transport(handler)
    with pytest.raises(asyncio.CancelledError):
        await get_adapter("glm_tts").list_voices(runtime("glm_tts"))


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["minimax", "minimax_intl", "glm_tts", "elevenlabs"])
async def test_unsupported_overwrite_never_calls_remote(provider, transport):
    seen = transport(lambda req: pytest.fail("unsupported overwrite must not call vendor"))
    with pytest.raises(VoiceManagementError, match="OVERWRITE_UNSUPPORTED"):
        await get_adapter(provider).overwrite(runtime(provider), "raw", audio=b"sample", filename="sample.wav")
    assert seen == []


@pytest.mark.parametrize("value", ["bad", "-1", "01", "10001"])
def test_invalid_numeric_cursors(value):
    with pytest.raises(VoiceManagementError, match="INVALID_CURSOR"):
        numeric_cursor(value)


@pytest.mark.parametrize("value", ["bad", "db:not-json", "db:WzYsbnVsbF0", "db:WzAsMTIzXQ"])
def test_invalid_doubao_composite_cursors(value):
    with pytest.raises(VoiceManagementError, match="INVALID_CURSOR"):
        _parse_cursor(value)


def test_credentials_and_endpoint_isolate_scope_without_model_or_key_fragment():
    original = runtime_for("minimax", "same-last8-secret", "https://first.test", model="model1")
    model_changed = runtime_for("minimax", "same-last8-secret", "https://first.test", model="model2")
    assert original.scope_id == model_changed.scope_id
    assert original.scope_id != runtime_for("minimax", "other-last8-secret", "https://first.test").scope_id
    assert original.scope_id != runtime_for("minimax", "same-last8-secret", "https://second.test").scope_id
    assert original.scope_id != runtime_for("minimax_intl", "same-last8-secret", "https://first.test").scope_id
    assert "last8" not in original.scope_id and "secret" not in repr(original)
    assert get_adapter("mimo") is None
    assert remote_date(None) is None
    assert remote_date(10**100) is None
    assert remote_date({}) is None
    with pytest.raises(VoiceManagementError, match="CONFIG_MISSING"):
        runtime_for("minimax", "***", "https://first.test")


class ConfigSnapshot:
    def __init__(self, raw=None):
        self.raw = raw or {}

    def get_tts_api_key(self, provider):
        return "configured-secret"

    def get_core_config(self):
        # Runtime config intentionally omits raw management settings.
        return {"TTS_MODEL": "cosyvoice-v3.5-flash"}

    def load_json_config(self, path, default):
        assert path == "core_config.json"
        return dict(self.raw)

    def get_cosyvoice_clone_runtime(self, provider):
        return {"api_key": "configured-secret", "base_url": "https://dashscope-us.aliyuncs.com/compatible-mode/v1" if provider.endswith("_intl") else "https://dashscope.aliyuncs.com/compatible-mode/v1"}


@pytest.mark.parametrize("provider", ["cosyvoice", "cosyvoice_intl", "minimax", "minimax_intl", "elevenlabs", "glm_tts"])
def test_resolve_runtime_matches_config_and_manual_contract(provider, monkeypatch):
    from utils import api_config_loader
    monkeypatch.setattr(api_config_loader, "get_cosyvoice_clone_model", lambda provider: "cosyvoice-v3-plus")
    adapter = get_adapter(provider)
    rt = adapter.resolve_runtime(ConfigSnapshot())
    assert rt.api_key == "configured-secret"
    assert rt.provider == provider
    assert "configured-secret" not in repr(rt)
    assert adapter.import_metadata(rt)
    if provider == "cosyvoice":
        assert rt.model == "cosyvoice-v3.5-flash"
    if provider == "cosyvoice_intl":
        assert rt.base_url == "https://dashscope-us.aliyuncs.com/api/v1"
        assert rt.model == "cosyvoice-v3-plus"
    assert bool(adapter.manual_fields(rt)) == provider.startswith("cosyvoice")


def test_doubao_raw_management_settings_and_scope_identity():
    raw = {"ttsModelProvider": "doubao_tts", "ttsModelUrl": "https://proxy.test/", "ttsModelId": "custom-resource", "doubaoVoiceManagementAccessKey": "management-ak", "doubaoVoiceManagementSecretKey": "management-sk", "doubaoVoiceManagementAppId": "test-app", "doubaoVoiceManagementProjectName": "test-project"}
    adapter = get_adapter("doubao_tts")
    rt = adapter.resolve_runtime(ConfigSnapshot(raw))
    assert rt.settings["project_name"] == "test-project"
    assert rt.settings["secret_key"] == "management-sk"
    assert rt.base_url == "https://proxy.test"
    assert rt.resource_id == "custom-resource"
    assert adapter.capabilities_for(rt).details
    assert adapter.manual_fields(rt)[0]["default_value"] == "custom-resource"
    raw["doubaoVoiceManagementSecretKey"] = "rotated-management-sk"
    assert adapter.resolve_runtime(ConfigSnapshot(raw)).scope_id == rt.scope_id
    raw["ttsModelId"] = "different-synthesis-resource"
    assert adapter.resolve_runtime(ConfigSnapshot(raw)).scope_id != rt.scope_id


def test_doubao_project_and_legacy_app_are_independent_ownership_scopes():
    adapter = get_adapter("doubao_tts")
    original = {"doubaoVoiceManagementAccessKey": "ak", "doubaoVoiceManagementSecretKey": "sk", "doubaoVoiceManagementProjectName": "workspace-a"}
    project_a = adapter.resolve_runtime(ConfigSnapshot(original))
    project_b = adapter.resolve_runtime(ConfigSnapshot({**original, "doubaoVoiceManagementProjectName": "workspace-b"}))
    rotated = adapter.resolve_runtime(ConfigSnapshot({**original, "doubaoVoiceManagementAccessKey": "rotated-ak", "doubaoVoiceManagementSecretKey": "rotated-sk"}))
    assert project_a.scope_id != project_b.scope_id
    assert project_a.scope_id == rotated.scope_id
    app_a = adapter.resolve_runtime(ConfigSnapshot({**original, "doubaoVoiceManagementProjectName": "", "doubaoVoiceManagementAppId": "workspace-a"}))
    app_b = adapter.resolve_runtime(ConfigSnapshot({**original, "doubaoVoiceManagementProjectName": "", "doubaoVoiceManagementAppId": "workspace-b"}))
    assert app_a.scope_id != app_b.scope_id
    assert app_a.scope_id != project_a.scope_id
    assert "workspace-a" not in project_a.scope_id


def test_doubao_imported_endpoint_survives_selection_change_but_not_identity_change():
    adapter = get_adapter("doubao_tts")
    raw = {"ttsModelProvider": "doubao_tts", "ttsModelUrl": "https://proxy.test", "ttsModelId": "custom-resource", "doubaoVoiceManagementProjectName": "workspace-a"}
    cm = ConfigSnapshot(raw)
    original = adapter.resolve_runtime(cm)
    metadata = {**adapter.import_metadata(original), "provider": "doubao_tts", "scope_id": original.scope_id}
    cm.raw = {**raw, "ttsModelProvider": "minimax", "ttsModelUrl": "https://another-provider.test", "ttsModelId": "speech-02"}
    bound = adapter.resolve_runtime(cm, voice_data=metadata)
    assert (bound.scope_id, bound.base_url, bound.resource_id) == (original.scope_id, original.base_url, original.resource_id)
    assert adapter.resolve_runtime(cm).base_url != bound.base_url
    cm.raw["doubaoVoiceManagementProjectName"] = "workspace-b"
    assert adapter.resolve_runtime(cm, voice_data=metadata).scope_id != original.scope_id
    cm.raw = {**raw, "ttsModelUrl": "https://explicit-new-endpoint.test"}
    assert adapter.resolve_runtime(cm, voice_data=metadata).scope_id != original.scope_id


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,code", [
    ({"code": "InvalidApiKey", "message": "secret"}, "AUTH_FAILED"),
    ({"code": "AccessDenied", "message": "secret"}, "PERMISSION_DENIED"),
    ({"code": "VoiceNotFound"}, "VOICE_NOT_FOUND"),
    ({"code": "SomeOtherFailure"}, "UPSTREAM_REJECTED"),
    ({"output": []}, "UPSTREAM_INVALID_RESPONSE"),
    ({"output": {}}, "VOICE_NOT_FOUND"),
])
async def test_cosy_detail_errors(payload, code, transport):
    transport(lambda req: httpx.Response(200, json=payload))
    with pytest.raises(VoiceManagementError) as caught:
        await get_adapter("cosyvoice").get_voice(runtime("cosyvoice"), "raw")
    assert caught.value.code == code


@pytest.mark.asyncio
async def test_cosy_upload_failure_does_not_write_voice(monkeypatch, transport):
    from utils import voice_clone
    seen = transport(lambda req: httpx.Response(200, json={"output": {"status": "OK", "target_model": "cosyvoice-v3.5-plus"}}))

    class Uploader:
        def __init__(self, *args):
            pass

        async def upload_file(self, *args):
            raise voice_clone.QwenVoiceCloneError("secret-response")

    monkeypatch.setattr(voice_clone, "QwenVoiceCloneClient", Uploader)
    with pytest.raises(VoiceManagementError, match="UPLOAD_FAILED"):
        await get_adapter("cosyvoice").overwrite(runtime("cosyvoice", settings={"upload_url": "https://upload.test"}), "raw", audio=b"sample", filename="sample.wav")
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,code", [
    ({"ResponseMetadata": []}, "UPSTREAM_INVALID_RESPONSE"),
    ({"ResponseMetadata": {"Error": "bad"}}, "UPSTREAM_INVALID_RESPONSE"),
    ({"ResponseMetadata": {"Error": {"Code": "OperationDenied.InvalidSpeakerID"}}}, "PERMISSION_DENIED"),
    ({"ResponseMetadata": {"Error": {"Code": "SignatureInvalid"}}}, "AUTH_FAILED"),
    ({"ResponseMetadata": {"Error": {"Code": "InternalError"}}}, "UPSTREAM_REJECTED"),
    ({"Result": []}, "UPSTREAM_INVALID_RESPONSE"),
    ({"Result": {"Statuses": [], "NextToken": "same"}}, "UPSTREAM_INVALID_RESPONSE"),
])
async def test_doubao_management_errors(payload, code, transport):
    # Nonempty wrong types must fail, rather than being mistaken for empty state.
    if payload.get("ResponseMetadata") == []:
        payload["ResponseMetadata"] = ["malformed"]
    transport(lambda req: httpx.Response(200, json=payload))
    with pytest.raises(VoiceManagementError) as caught:
        await get_adapter("doubao_tts").list_voices(runtime("doubao_tts", settings=management_settings()), cursor="same")
    assert caught.value.code == code


@pytest.mark.asyncio
async def test_doubao_overwrite_uncertain_response_does_not_retry(transport):
    def handler(req):
        if req.url.path == "/api/v3/tts/voice_clone":
            raise httpx.ReadTimeout("private-response", request=req)
        return httpx.Response(200, json={"Result": {"Statuses": [{"SpeakerID": "S_existing", "State": "Success", "AvailableTrainingTimes": 2, "Version": "v1"}]}})

    seen = transport(handler)
    with pytest.raises(VoiceManagementError, match="UPDATE_OUTCOME_UNKNOWN"):
        await get_adapter("doubao_tts").overwrite(runtime("doubao_tts", settings=management_settings()), "S_existing", audio=b"sample", filename="sample.wav")
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_doubao_update_without_revision_does_not_claim_completed(transport):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path == "/api/v3/tts/voice_clone":
            return httpx.Response(200, json={"code": 0, "speaker_id": "S_existing"})
        row = {"SpeakerID": "S_existing", "State": "Success", "AvailableTrainingTimes": 2}
        if len(calls) == 1:
            row["Version"] = "v1"
        return httpx.Response(200, json={"Result": {"Statuses": [row]}})

    transport(handler)
    updated = await get_adapter("doubao_tts").overwrite(runtime("doubao_tts", settings=management_settings()), "S_existing", audio=b"sample", filename="sample.wav")
    assert updated.status == "processing"
    assert calls == ["/", "/api/v3/tts/voice_clone", "/"]


def test_custom_management_registration_is_shared_with_tts_lookup(monkeypatch):
    from utils.tts import provider_registry
    from utils.voice_management import providers

    # Both mutable registries are restored, so built-in configuration tests
    # cannot observe this invented provider after the test completes.
    monkeypatch.setattr(providers, "_ADAPTERS", dict(providers._ADAPTERS))
    monkeypatch.setattr(provider_registry, "_REGISTRY", dict(provider_registry._REGISTRY))
    adapter = object()
    providers.register_adapter("custom_remote", adapter)
    assert providers.get_adapter("custom_remote") is adapter
    assert provider_registry.get_voice_management("custom_remote") is adapter


def test_tts_provider_explicit_adapter_is_registered_for_storage_lookup(monkeypatch):
    from utils.tts import provider_registry
    from utils.voice_management import providers
    from utils.config_manager.imported_voices import ImportedVoiceStorageMixin

    monkeypatch.setattr(providers, "_ADAPTERS", dict(providers._ADAPTERS))
    monkeypatch.setattr(provider_registry, "_REGISTRY", dict(provider_registry._REGISTRY))
    class CustomAdapter:
        def resolve_runtime(self, cm, *, voice_data=None):
            return runtime_for("custom_remote", "custom-credential", "https://custom.test")

    adapter = CustomAdapter()
    declaration = provider_registry.TTSProvider(
        key="custom_remote", kind="clone", priority=999,
        capabilities=frozenset({"clone"}), is_selected=lambda ctx: False,
        resolve=lambda ctx: None, voice_management=adapter,
    )
    provider_registry.register(declaration)
    assert providers.get_adapter("custom_remote") is adapter
    assert provider_registry.get_voice_management("custom_remote") is adapter
    assert ImportedVoiceStorageMixin._current_imported_scope(object(), "custom_remote") == adapter.resolve_runtime(None).scope_id


@pytest.mark.asyncio
@pytest.mark.parametrize("voice_id,state", [("bad-id", "Success"), ("S_existing", "Active")])
async def test_doubao_nonmodifiable_voice_is_not_uploaded(voice_id, state, transport):
    seen = transport(lambda req: httpx.Response(200, json={"Result": {"Statuses": [{"SpeakerID": voice_id, "State": state, "AvailableTrainingTimes": 2}]}}))
    with pytest.raises(VoiceManagementError):
        await get_adapter("doubao_tts").overwrite(runtime("doubao_tts", settings=management_settings()), voice_id, audio=b"sample", filename="sample.wav")
    assert all(req.url.path == "/" for req in seen)


def test_doubao_signature_uses_documented_scope_and_body():
    query = {"Action": "BatchListMegaTTSTrainStatus", "Version": "2023-11-07"}
    body = b'{"AppID":"test-app","PageNumber":1,"PageSize":100}'
    headers = _signed_headers(management_settings(), body, query, timestamp="20261005T080000Z")
    assert headers["X-Date"] == "20261005T080000Z"
    assert headers["X-Content-Sha256"] == hashlib.sha256(body).hexdigest()
    assert "Credential=test-AK/20261005/cn-north-1/speech_saas_prod/request" in headers["Authorization"]
    assert "SignedHeaders=host;x-content-sha256;x-date" in headers["Authorization"]
    assert "test-SK" not in json.dumps(headers)
    other = _signed_headers(management_settings(), body + b" ", query, timestamp="20261005T080000Z")
    assert headers["Authorization"] != other["Authorization"]


@pytest.mark.parametrize("provider,value", [
    ("minimax", "short"), ("minimax", "1Invalid123"), ("minimax", "Valid123_"),
    ("minimax", "English-123-"), ("minimax", "a" * 257), ("minimax", "Voice中文123"),
    ("minimax_intl", "Invalid.123"), ("doubao_tts", "S_"), ("doubao_tts", "plain-voice"),
    ("elevenlabs", "remote/id"), ("elevenlabs", "remote%2Fid"), ("elevenlabs", "eleven:raw"),
    ("elevenlabs", "has space"), ("glm_tts", "has\nnewline"), ("cosyvoice", "voice?query"),
    ("cosyvoice", "voice#fragment"), ("cosyvoice", "voice\\path"),
])
def test_provider_manual_ids_reject_invalid_known_syntax(provider, value):
    with pytest.raises(VoiceManagementError, match="INVALID_VOICE_ID"):
        get_adapter(provider).validate_voice_id(value)


@pytest.mark.parametrize("provider,value", [
    ("minimax", "Voice_123-good"), ("minimax_intl", "Voice123"),
    ("doubao_tts", "S_existing"), ("elevenlabs", "raw-voice123"),
    ("glm_tts", "voice_clone_20260101"), ("cosyvoice", "cosyvoice-v3.5-plus-prefix-raw"),
])
def test_provider_manual_id_validation_keeps_original_id(provider, value):
    assert get_adapter(provider).validate_voice_id(" " + value + " ") == value


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["minimax", "minimax_intl", "elevenlabs", "cosyvoice", "cosyvoice_intl", "doubao_tts", "glm_tts"])
async def test_details_invalid_id_is_rejected_before_http(provider, transport):
    seen = transport(lambda req: pytest.fail("invalid ID must not reach the remote API"))
    with pytest.raises(VoiceManagementError, match="INVALID_VOICE_ID"):
        await get_adapter(provider).get_voice(runtime(provider), "bad/id")
    assert seen == []
