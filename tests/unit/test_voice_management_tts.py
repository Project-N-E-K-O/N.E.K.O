"""Imported local refs never leak into synthesis requests."""
import asyncio
import base64
import json
import threading
from queue import Queue
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request

from main_logic import tts_client
from main_logic.tts_client import remote_voice
from main_routers.characters_router import voice_preview
from tests.unit.test_voice_management_storage import doubao_import

LOCAL_REF = "voice_" + "a" * 32


class VoiceManager:
    def __init__(self, provider):
        self.active = True
        self.metadata = {
            "local_ref": LOCAL_REF,
            "origin": "import", "source": "clone", "provider": provider,
            "remote_voice_id": "S_remote123" if provider == "doubao_tts" else "remote123",
            "scope_id": "scope", "clone_model": "cosyvoice-v3.5-plus",
            "dashscope_base_url": "https://dashscope.aliyuncs.com/api/v1",
        }

    def get_imported_voice(self, ref, include_inactive=False):
        if ref == LOCAL_REF and (self.active or include_inactive):
            return dict(self.metadata)
        return None

    def get_core_config(self):
        return {}

    async def aget_core_config(self):
        return {}

    def get_model_api_config(self, tier):
        return {"api_key": "key", "base_url": "https://example.com"}

    async def aget_model_api_config(self, tier):
        return self.get_model_api_config(tier)

    def load_json_config(self, name, default=None):
        return {}

    def get_voices_for_current_api(self):
        return {LOCAL_REF: dict(self.metadata)} if self.active else {}

    def get_tts_api_key(self, provider):
        return "key"

    def get_cosyvoice_clone_runtime(self, provider):
        return {"api_key": "key", "provider": provider, "base_url": "https://example.com"}

    async def aensure_region_resolved(self):
        return True


@pytest.fixture
def manager(monkeypatch):
    from utils.voice_management import providers
    cm = VoiceManager("minimax")
    monkeypatch.setattr(providers, "get_adapter", lambda provider: SimpleNamespace(
        resolve_runtime=lambda manager, voice_data=None: SimpleNamespace(
            provider=provider, scope_id="scope", api_key="key", base_url="https://example.com", model="",
        ),
    ))
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)
    monkeypatch.setattr(remote_voice, "get_config_manager", lambda: cm)
    monkeypatch.setattr(voice_preview, "get_config_manager", lambda: cm)
    return cm


@pytest.mark.parametrize("provider,route", [
    ("minimax", "minimax"), ("minimax_intl", "minimax"),
    ("elevenlabs", "elevenlabs"), ("cosyvoice", "cosyvoice"),
    ("cosyvoice_intl", "cosyvoice"), ("doubao_tts", "doubao_tts"),
    ("glm_tts", "glm_tts"),
])
def test_registry_routes_by_local_metadata_but_worker_receives_remote_id(manager, provider, route):
    manager.metadata["provider"] = provider
    worker, api_key, selected = tts_client.get_tts_worker("qwen", True, LOCAL_REF)
    assert selected == route
    observed = []
    actual_worker = worker.keywords["worker"]
    if provider == "doubao_tts":
        assert actual_worker.keywords["configured_voice"] == manager.metadata["remote_voice_id"]

    def recorder(request, response, key, voice_id):
        observed.append((voice_id, tts_client._get_voice_meta(voice_id)))

    worker.keywords["worker"] = recorder
    worker(Queue(), Queue(), api_key, LOCAL_REF)
    assert observed[0][0] == manager.metadata["remote_voice_id"]
    assert observed[0][1]["clone_model"] == "cosyvoice-v3.5-plus"
    assert remote_voice.active_imported_voice.get() is None


def test_inactive_ref_uses_terminal_error_not_cosyvoice_fallback(manager):
    manager.active = False
    worker, key, provider = tts_client.get_tts_worker("qwen", True, LOCAL_REF)
    response = Queue()
    worker(Queue(), response, key, LOCAL_REF)
    assert json.loads(response.get_nowait()[1]) == {
        "code": "TTS_CONFIG_INVALID", "reason": "IMPORTED_VOICE_UNAVAILABLE",
    }
    assert response.get_nowait() == ("__ready__", False)
    assert provider is None and response.empty()


def test_explicit_disable_tts_keeps_existing_precedence(manager, monkeypatch):
    manager.active = False
    monkeypatch.setattr(manager, "get_core_config", lambda: {"DISABLE_TTS": True})
    worker, _, _ = tts_client.get_tts_worker("qwen", True, LOCAL_REF)
    assert worker is tts_client.dummy_tts_worker


def test_worker_rechecks_scope_after_dispatch(manager):
    worker, key, _ = tts_client.get_tts_worker("qwen", True, LOCAL_REF)
    manager.active = False
    worker.keywords["worker"] = lambda *args: pytest.fail("No remote request after context changes")
    response = Queue()
    worker(Queue(), response, key, LOCAL_REF)
    assert json.loads(response.get_nowait()[1])["code"] == "TTS_CONFIG_INVALID"
    assert response.get_nowait() == ("__ready__", False)


def test_actual_minimax_http_payload_uses_remote_id(manager):
    requests = []

    def transport(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"base_resp": {"status_code": 0}, "data": {}})

    def actual_request(request_queue, response_queue, key, voice_id):
        async def synthesize():
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                await tts_client._minimax_sse_synthesize(
                    client, "https://example.com/v1/t2a_v2", {}, "speech-02-hd",
                    "hello", voice_id, "sid", response_queue, 4800,
                )
        asyncio.run(synthesize())

    worker, key, _ = tts_client.get_tts_worker("qwen", True, LOCAL_REF)
    worker.keywords["worker"] = actual_request
    worker(Queue(), Queue(), key, LOCAL_REF)
    assert requests[0]["voice_setting"]["voice_id"] == "remote123"
    assert LOCAL_REF not in json.dumps(requests)


@pytest.mark.asyncio
async def test_preview_maps_id_and_uses_captured_credentials(manager, monkeypatch):
    from utils.voice_management import providers
    monkeypatch.setattr(providers, "get_adapter", lambda provider: SimpleNamespace(
        resolve_runtime=lambda cm, voice_data=None: SimpleNamespace(
            scope_id="scope", provider=provider, api_key="captured-key", base_url="https://example.com", model="",
        ),
    ))
    observed = []

    class Client:
        def __init__(self, api_key, base_url):
            observed.append(api_key)

        async def synthesize_preview(self, voice_id, text):
            observed.append(voice_id)
            return b"audio"

    monkeypatch.setattr(voice_preview, "MinimaxVoiceCloneClient", Client)
    request = Request({"type": "http", "headers": [], "query_string": b""})
    result = await voice_preview.get_voice_preview(request, LOCAL_REF)
    assert not hasattr(result, 'body'), getattr(result, 'body', b'')
    assert result["success"]
    assert observed == ["captured-key", "remote123"]


@pytest.mark.asyncio
async def test_inactive_preview_returns_explicit_error(manager):
    manager.active = False
    result = await voice_preview.get_voice_preview(
        Request({"type": "http", "headers": [], "query_string": b""}), LOCAL_REF,
    )
    assert result.status_code == 409
    assert json.loads(result.body)["code"] == "IMPORTED_VOICE_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["minimax", "minimax_intl", "elevenlabs", "doubao_tts", "glm_tts"])
async def test_actual_preview_http_payloads_use_remote_id(manager, monkeypatch, provider):
    manager.metadata["provider"] = provider
    manager.metadata["remote_voice_id"] = "S_remote123" if provider == "doubao_tts" else "remote123"
    manager.metadata["elevenlabs_base_url"] = "https://example.com"
    requests = []
    real_client = httpx.AsyncClient

    def transport(request):
        requests.append((request, json.loads(request.content)))
        if provider.startswith("minimax"):
            return httpx.Response(200, json={"base_resp": {"status_code": 0}, "data": {"audio": b"audio".hex()}})
        if provider == "doubao_tts":
            return httpx.Response(200, json={"code": 0, "data": base64.b64encode(b"audio").decode()})
        return httpx.Response(200, content=b"audio", headers={"content-type": "audio/wav"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(
        **{**kwargs, "transport": httpx.MockTransport(transport)},
    ))
    result = await voice_preview.get_voice_preview(
        Request({"type": "http", "headers": [], "query_string": b""}), LOCAL_REF,
    )
    assert not hasattr(result, "body"), getattr(result, "body", b"")
    assert result["success"]
    payload = requests[0][1]
    if provider.startswith("minimax"):
        assert payload["voice_setting"]["voice_id"] == "remote123"
    elif provider == "elevenlabs":
        assert payload["inputs"][0]["voice_id"] == "remote123"
    elif provider == "glm_tts":
        assert payload["voice"] == "remote123"
    else:
        assert payload["req_params"]["speaker"] == "S_remote123"
    assert LOCAL_REF not in json.dumps(payload)


@pytest.mark.asyncio
async def test_cosy_preview_preserves_model_and_remote_voice(manager, monkeypatch):
    from dashscope.audio import tts_v2
    manager.metadata["provider"] = "cosyvoice"
    observed = []

    def synthesizer(**kwargs):
        observed.append(kwargs)
        return SimpleNamespace(call=lambda text: b"audio")

    monkeypatch.setattr(tts_v2, "SpeechSynthesizer", synthesizer)
    result = await voice_preview.get_voice_preview(
        Request({"type": "http", "headers": [], "query_string": b""}), LOCAL_REF,
    )
    assert not hasattr(result, "body"), getattr(result, "body", b"")
    assert result["success"]
    assert observed[0]["voice"] == "remote123"
    assert observed[0]["model"] == "cosyvoice-v3.5-plus"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", [
    "minimax", "minimax_intl", "elevenlabs", "doubao_tts", "glm_tts", "cosyvoice", "cosyvoice_intl",
])
@pytest.mark.parametrize("change", ["unchanged", "delete", "overwrite", "inactive"])
async def test_all_imported_preview_paths_fence_late_audio(manager, monkeypatch, provider, change):
    """Delay actual synthesis delivery, then retire the captured local record."""
    manager.metadata["provider"] = provider
    started, release = asyncio.Event(), asyncio.Event()
    thread_release = threading.Event()
    loop = asyncio.get_running_loop()
    real_client = httpx.AsyncClient

    async def transport(request):
        started.set()
        await release.wait()
        if provider.startswith("minimax"):
            return httpx.Response(200, json={"base_resp": {"status_code": 0}, "data": {"audio": b"audio".hex()}})
        if provider == "doubao_tts":
            return httpx.Response(200, json={"code": 0, "data": base64.b64encode(b"audio").decode()})
        return httpx.Response(200, content=b"audio", headers={"content-type": "audio/wav"})

    def synthesis(text):
        loop.call_soon_threadsafe(started.set)
        assert thread_release.wait(timeout=10)
        return b"audio"

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(
        **{**kwargs, "transport": httpx.MockTransport(transport)},
    ))
    if provider.startswith("cosyvoice"):
        from dashscope.audio import tts_v2
        monkeypatch.setattr(tts_v2, "SpeechSynthesizer", lambda **kwargs: SimpleNamespace(call=synthesis))
    pending = asyncio.create_task(voice_preview.get_voice_preview(
        Request({"type": "http", "headers": [], "query_string": b""}), LOCAL_REF,
    ))
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        if change == "delete":
            monkeypatch.setattr(manager, "get_imported_voice", lambda *args, **kwargs: None)
        elif change == "overwrite":
            manager.metadata.update(overwrite_operation_id="new-operation", overwrite_status="completed", remote_revision="2")
        elif change == "inactive":
            manager.active = False
    finally:
        release.set()
        thread_release.set()
    result = await asyncio.wait_for(pending, timeout=10)
    if change == "unchanged":
        assert result["success"] and base64.b64decode(result["audio"]) == b"audio"
    else:
        assert result.status_code == 409
        assert json.loads(result.body)["code"] == "IMPORTED_VOICE_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["unchanged", "delete_other", "delete", "overwrite", "storage_error"])
async def test_preview_delivery_checks_actual_local_storage(doubao_import, monkeypatch, change):
    cm, adapter, ref, metadata = doubao_import
    monkeypatch.setattr(voice_preview, "get_config_manager", lambda: cm)
    started, release = asyncio.Event(), asyncio.Event()
    real_client = httpx.AsyncClient

    async def transport(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json={"code": 0, "data": base64.b64encode(b"audio").decode()})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(
        **{**kwargs, "transport": httpx.MockTransport(transport)},
    ))
    pending = asyncio.create_task(voice_preview.get_voice_preview(
        Request({"type": "http", "headers": [], "query_string": b""}), ref,
    ))
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        if change == "delete":
            assert await cm.adelete_imported_voice(ref)
        elif change == "delete_other":
            other, _, _ = await cm.aimport_remote_voice(
                metadata["scope_id"], metadata["provider"], "S_another123", adapter.import_metadata(adapter.resolve_runtime(cm)),
            )
            assert await cm.adelete_imported_voice(other)
        elif change == "overwrite":
            await cm.aupdate_imported_voice(ref, metadata["scope_id"], {
                "overwrite_operation_id": "new-operation", "overwrite_status": "completed", "remote_revision": "2",
            })
        elif change == "storage_error":
            def unreadable():
                raise OSError("unreadable voice storage")
            monkeypatch.setattr(cm, "load_voice_storage", unreadable)
    finally:
        release.set()
    result = await asyncio.wait_for(pending, timeout=10)
    if change in {"unchanged", "delete_other"}:
        assert result["success"] and base64.b64decode(result["audio"]) == b"audio"
    else:
        assert result.status_code == 409
        assert json.loads(result.body)["code"] == "IMPORTED_VOICE_UNAVAILABLE"


def test_dispatch_freezes_imported_provider_credentials(manager, monkeypatch):
    monkeypatch.setattr(manager, "get_tts_api_key", lambda provider: "new-context-key")
    _, api_key, _ = tts_client.get_tts_worker("qwen", True, LOCAL_REF)
    assert api_key == "key"


def test_existing_legacy_uuid_shaped_library_id_keeps_original_dispatch(manager, monkeypatch):
    monkeypatch.setattr(manager, "get_imported_voice", lambda *args, **kwargs: None)
    manager.metadata.pop("origin")
    worker, _, provider = tts_client.get_tts_worker("qwen", True, LOCAL_REF)
    assert provider == "minimax"
    assert worker.func is tts_client.minimax_tts_worker


def test_doubao_actual_worker_keeps_imported_endpoint_after_provider_switch(doubao_import, monkeypatch):
    from main_logic.tts_client.workers import doubao
    cm, _, ref, _ = doubao_import
    cm.raw.update(ttsModelProvider="minimax", ttsModelUrl="https://other-provider.example", ttsModelId="speech-02")
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)
    monkeypatch.setattr(remote_voice, "get_config_manager", lambda: cm)
    monkeypatch.setattr(doubao, "_record_tts_telemetry", lambda *args: None)
    captured = []
    real_client = httpx.AsyncClient

    def transport(request):
        captured.append(request)
        return httpx.Response(200, json={"code": 0, "data": base64.b64encode(b"\x00\x00" * 24000).decode()})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(**{**kwargs, "transport": httpx.MockTransport(transport)}))
    worker, key, selected = tts_client.get_tts_worker("qwen", True, ref)
    assert selected == "doubao_tts" and key == "synthesis-key"
    request_queue, response_queue = Queue(), Queue()
    thread = threading.Thread(target=worker, args=(request_queue, response_queue, key, ref), daemon=True)
    thread.start()
    try:
        assert response_queue.get(timeout=5) == ("__ready__", True)
        request_queue.put(("speech-1", "hello."))
        request_queue.put((None, None))
        audio = response_queue.get(timeout=5)
        assert isinstance(audio, bytes) and len(audio) > 0
    finally:
        request_queue.put((tts_client.TTS_SHUTDOWN_SENTINEL, None))
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert len(captured) == 1
    request = captured[0]
    assert request.url.host == "doubao-proxy.example"
    assert request.headers["X-Api-Key"] == "synthesis-key"
    assert request.headers["X-Api-Resource-Id"] == "custom-resource"
    assert json.loads(request.content)["req_params"]["speaker"] == "S_remote123"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_doubao_preview_keeps_record_endpoint_and_rejects_late_key_change(doubao_import, monkeypatch, changed):
    cm, _, ref, _ = doubao_import
    cm.raw.update(ttsModelProvider="minimax", ttsModelUrl="https://other-provider.example", ttsModelId="speech-02")
    monkeypatch.setattr(voice_preview, "get_config_manager", lambda: cm)
    captured = []
    real_client = httpx.AsyncClient

    def transport(request):
        captured.append(request)
        if changed:
            cm.raw["assistApiKeyDoubaoTts"] = "new-account-key"
        return httpx.Response(200, json={"code": 0, "data": base64.b64encode(b"audio").decode()})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(**{**kwargs, "transport": httpx.MockTransport(transport)}))
    result = await voice_preview.get_voice_preview(Request({"type": "http", "headers": [], "query_string": b""}), ref)
    if changed:
        assert result.status_code == 409 and json.loads(result.body)["code"] == "IMPORTED_VOICE_UNAVAILABLE"
    else:
        assert result["success"] and base64.b64decode(result["audio"]) == b"audio"
    assert len(captured) == 1
    request = captured[0]
    assert request.url.host == "doubao-proxy.example"
    assert request.headers["X-Api-Key"] == "synthesis-key"
    assert request.headers["X-Api-Resource-Id"] == "custom-resource"
    assert json.loads(request.content)["req_params"]["speaker"] == "S_remote123"
