"""Alibaba Bailian Token Plan TTS (qwen-audio-3.0-tts-plus): protocol, worker, dispatch, probe."""

import asyncio
import base64
import json
import logging
from functools import partial

import httpx
import numpy as np
import pytest

from main_logic import tts_client
from main_logic.tts_client.workers import qwen_token_plan as worker_module
from main_routers.config_router.connectivity import _test_qwen_token_plan_tts_connectivity
from utils.qwen_token_plan_tts import (
    QWEN_TOKEN_PLAN_TTS_DEFAULT_VOICE,
    build_qwen_token_plan_tts_payload,
    extract_qwen_token_plan_audio,
    is_qwen_token_plan_url,
    normalize_qwen_token_plan_tts_voice,
    qwen_token_plan_audio_download_url,
    qwen_token_plan_tts_url,
    select_qwen_token_plan_tts_voice,
    QwenTokenPlanTtsError,
)

_ENDPOINT = "https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"


# ─── URL whitelist ───────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("url", [
    "https://token-plan.cn-beijing.maas.aliyuncs.com",
    "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1",
    "HTTPS://Token-Plan.CN-Beijing.maas.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer",
])
def test_token_plan_hosts_over_https_are_accepted(url):
    assert is_qwen_token_plan_url(url)


@pytest.mark.unit
@pytest.mark.parametrize("url", [
    "http://token-plan.cn-beijing.maas.aliyuncs.com",
    "http://token-plan.maas.qianwenaiapi.com/compatible-mode/v1",
    "https://dashscope.aliyuncs.com/api/v1",
    "https://token-plan.cn-beijing.maas.aliyuncs.com.evil.test",
    "https://token-plan.cn-beijing.maas.aliyuncs.com@evil.test/api",
    "wss://token-plan.cn-beijing.maas.aliyuncs.com",
    "",
])
def test_other_urls_are_rejected(url):
    assert not is_qwen_token_plan_url(url)


@pytest.mark.unit
def test_endpoint_keeps_only_the_host():
    # The console shows the text-model /compatible-mode/v1 address; TTS lives elsewhere.
    assert qwen_token_plan_tts_url("https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1") == (
        "https://token-plan.maas.qianwenaiapi.com/api/v1/services/audio/tts/SpeechSynthesizer"
    )
    assert qwen_token_plan_tts_url("") == _ENDPOINT
    with pytest.raises(ValueError):
        qwen_token_plan_tts_url("http://token-plan.cn-beijing.maas.aliyuncs.com")


@pytest.mark.unit
@pytest.mark.parametrize("url, expected", [
    ("https://oss.example/a.pcm?Signature=x", "https://oss.example/a.pcm?Signature=x"),
    # The documented non-streaming example uses an http OSS link.
    ("http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a/b.pcm?Expires=1&Signature=s%2B",
     "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a/b.pcm?Expires=1&Signature=s%2B"),
    ("http://evil.test/a.pcm", ""),
    ("http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com.evil.test/a.pcm", ""),
    ("http://user:pw@dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a.pcm", ""),
    ("ftp://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a.pcm", ""),
    ("http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com:abc/a.pcm", ""),
])
def test_signed_audio_url_download_rules(url, expected):
    assert qwen_token_plan_audio_download_url(url) == expected


# ─── voice fallback (paired cases) ───────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("voice, expected", [
    ("longanlingxin", "longanlingxin"),
    ("longanlufeng", "longanlufeng"),
    ("LongAnLuFeng", "longanlufeng"),
    ("qwen-audio-3.0-tts-plus-abc", "qwen-audio-3.0-tts-plus-abc"),
])
def test_voices_of_this_model_are_sent_as_is(voice, expected):
    assert normalize_qwen_token_plan_tts_voice(voice) == (expected, True)


@pytest.mark.unit
@pytest.mark.parametrize("voice", [
    "Cherry",                       # Qwen realtime voice
    "cosyvoice-v3-plus-neko-abc",   # a clone id from another provider
    "longanhuan_v3.6",              # a qwen-audio-3.0-tts-flash voice
    "qwen-audio-3.0-tts-plus-",     # bare prefix
    "",
])
def test_other_voices_fall_back_to_the_default(voice):
    assert normalize_qwen_token_plan_tts_voice(voice) == (QWEN_TOKEN_PLAN_TTS_DEFAULT_VOICE, False)


@pytest.mark.unit
def test_voice_selection_order():
    assert select_qwen_token_plan_tts_voice("longanlufeng", "longanlingxin") == ("longanlufeng", False)
    # Character voice from another provider: the configured fallback voice is used.
    assert select_qwen_token_plan_tts_voice("Cherry", "longanlufeng") == ("longanlufeng", False)
    assert select_qwen_token_plan_tts_voice("Cherry", "Momo") == (QWEN_TOKEN_PLAN_TTS_DEFAULT_VOICE, True)
    assert select_qwen_token_plan_tts_voice("", "") == (QWEN_TOKEN_PLAN_TTS_DEFAULT_VOICE, False)


# ─── response parsing ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_extract_audio_chunk_and_finish():
    pcm = b"\x01\x00\x02\x00"
    chunk, url, finished = extract_qwen_token_plan_audio({
        "output": {"type": "sentence-synthesis", "finish_reason": None,
                   "audio": {"data": base64.b64encode(pcm).decode()}},
    })
    assert (chunk, url, finished) == (pcm, "", False)
    chunk, url, finished = extract_qwen_token_plan_audio({
        "output": {"finish_reason": "stop", "audio": {"data": "", "url": "https://oss.example/a.pcm"}},
    })
    assert (chunk, url, finished) == (b"", "https://oss.example/a.pcm", True)


@pytest.mark.unit
def test_extract_raises_on_error_object():
    with pytest.raises(QwenTokenPlanTtsError, match="InvalidParameter"):
        extract_qwen_token_plan_audio({"code": "InvalidParameter", "message": "voice not found"})


# ─── worker ──────────────────────────────────────────────────────────────────

class _Queue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


def _sse(events):
    return "".join(f"id:{i}\nevent:result\ndata:{json.dumps(e)}\n\n" for i, e in enumerate(events)).encode()


def _audio_event(pcm: bytes, finish=None):
    return {"output": {"type": "sentence-synthesis", "finish_reason": finish,
                       "audio": {"data": base64.b64encode(pcm).decode()}}}


def _run_worker(monkeypatch, handler, *, api_key="sk-sp-test", voice_id="longanlufeng", **kwargs):
    """Drive the worker's synthesize() against a mocked HTTP transport."""
    requests = []

    def recording(request):
        requests.append(request)
        return handler(request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(recording), **kw),
    )
    captured = {}
    monkeypatch.setattr(
        worker_module, "_run_sentence_tts_worker",
        lambda _rq, _resp, setup, label: captured.setdefault("setup", setup),
    )
    worker_module.qwen_token_plan_tts_worker(None, None, api_key, voice_id, **kwargs)
    queue = _Queue()

    async def scenario():
        synthesize, cleanup = await captured["setup"](queue)
        try:
            await synthesize("你好喵。", "speech-1")
        finally:
            await cleanup()

    asyncio.run(scenario())
    return requests, queue.items


def _audio_bytes(items):
    return b"".join(item for item in items if isinstance(item, bytes))


@pytest.mark.unit
def test_worker_posts_documented_request_and_streams_audio(monkeypatch):
    pcm = np.full(2400, 1000, dtype="<i2").tobytes()

    def handler(request):
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content=_sse([_audio_event(pcm[:2401]), _audio_event(pcm[2401:]),
                          {"output": {"finish_reason": "stop", "audio": {"url": "https://oss.example/x"}}}]),
        )

    requests, items = _run_worker(
        monkeypatch, handler,
        base_url="https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1",
        model="qwen-audio-3.0-tts-plus",
    )

    assert len(requests) == 1, "the final signed URL must not be fetched after streamed audio"
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://token-plan.maas.qianwenaiapi.com/api/v1/services/audio/tts/SpeechSynthesizer"
    assert request.headers["authorization"] == "Bearer sk-sp-test"
    assert request.headers["x-dashscope-sse"] == "enable"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == build_qwen_token_plan_tts_payload(
        "你好喵。", "longanlufeng", model="qwen-audio-3.0-tts-plus",
    ) == {
        "model": "qwen-audio-3.0-tts-plus",
        "input": {"text": "你好喵。", "voice": "longanlufeng", "format": "pcm", "sample_rate": 24000},
    }
    # 24 kHz in, 48 kHz out: roughly twice the samples, split chunk boundary included.
    out = _audio_bytes(items)
    assert abs(len(out) - 2 * len(pcm)) <= 64
    assert not [item for item in items if isinstance(item, tuple) and item[0] == "__error__"]


@pytest.mark.unit
def test_worker_parses_crlf_frames_and_multi_line_data(monkeypatch):
    pcm = b"\x05\x00" * 240
    event = json.dumps(_audio_event(pcm, finish="stop"), indent=1)  # JSON spread over lines
    body = ":keepalive\r\n" + "".join(f"data:{line}\r\n" for line in event.split("\n")) + "\r\n"

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    _, items = _run_worker(monkeypatch, handler)

    assert _audio_bytes(items)
    assert not [item for item in items if isinstance(item, tuple) and item[0] == "__error__"]


@pytest.mark.unit
def test_worker_replaces_foreign_voice_without_logging_it(monkeypatch, caplog):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=_sse([_audio_event(b"\x00\x00" * 100, finish="stop")]))

    with caplog.at_level(logging.WARNING):
        requests, _ = _run_worker(monkeypatch, handler, voice_id="Cherry-secret-clone", configured_voice="")

    assert json.loads(requests[0].content)["input"]["voice"] == QWEN_TOKEN_PLAN_TTS_DEFAULT_VOICE
    assert "fell back" in caplog.text
    assert "Cherry-secret-clone" not in caplog.text


@pytest.mark.unit
def test_worker_accepts_own_voice_without_fallback_log(monkeypatch, caplog):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=_sse([_audio_event(b"\x00\x00" * 100, finish="stop")]))

    with caplog.at_level(logging.WARNING):
        requests, _ = _run_worker(monkeypatch, handler, voice_id="qwen-audio-3.0-tts-plus-xyz")

    assert json.loads(requests[0].content)["input"]["voice"] == "qwen-audio-3.0-tts-plus-xyz"
    assert "fell back" not in caplog.text


@pytest.mark.unit
def test_worker_downloads_signed_url_without_the_key_for_a_non_streaming_reply(monkeypatch):
    pcm = b"\x10\x00" * 480

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, content=pcm)
        return httpx.Response(200, json={"output": {"finish_reason": "stop", "audio": {
            "data": "", "url": "http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a.pcm?sig=1"}}})

    requests, items = _run_worker(monkeypatch, handler)

    assert [r.method for r in requests] == ["POST", "GET"]
    assert str(requests[1].url) == "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a.pcm?sig=1"
    assert "authorization" not in requests[1].headers
    assert _audio_bytes(items)


@pytest.mark.unit
def test_worker_reports_error_event(monkeypatch):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=_sse([{"code": "InvalidParameter", "message": "bad voice"}]))

    _, items = _run_worker(monkeypatch, handler)

    errors = [item for item in items if isinstance(item, tuple) and item[0] == "__error__"]
    assert errors and "InvalidParameter" in str(errors[0])
    assert not _audio_bytes(items)


@pytest.mark.unit
def test_worker_reports_http_error(monkeypatch):
    def handler(request):
        return httpx.Response(401, json={"code": "InvalidApiKey", "message": "Invalid API-key provided."})

    _, items = _run_worker(monkeypatch, handler)

    errors = [item for item in items if isinstance(item, tuple) and item[0] == "__error__"]
    assert errors and "401" in str(errors[0]) and "InvalidApiKey" in str(errors[0])


@pytest.mark.unit
def test_worker_http_error_does_not_echo_the_raw_body(monkeypatch):
    def handler(request):
        return httpx.Response(500, text="upstream echo: 你好喵。")

    _, items = _run_worker(monkeypatch, handler)

    errors = [item for item in items if isinstance(item, tuple) and item[0] == "__error__"]
    assert errors and "500" in str(errors[0])
    assert "你好喵" not in str(errors[0])


@pytest.mark.unit
def test_worker_setup_fails_without_key_or_with_plain_http(monkeypatch):
    for kwargs in ({"api_key": ""}, {"base_url": "http://token-plan.cn-beijing.maas.aliyuncs.com"}):
        with pytest.raises(RuntimeError):
            _run_worker(monkeypatch, lambda request: httpx.Response(500), **kwargs)


# ─── dispatch ────────────────────────────────────────────────────────────────

class _CM:
    def __init__(self, raw, *, key="sk-sp-test", core=None):
        self.raw = raw
        self.key = key
        self.core = core or {}

    def get_core_config(self):
        return {"TTS_PROVIDER": "", "ttsProvider": "", "GPTSOVITS_ENABLED": False, **self.core}

    def load_json_config(self, filename, default):
        assert filename == "core_config.json"
        return self.raw

    def get_tts_api_key(self, provider):
        assert provider == "qwen_token_plan"
        return self.key

    def voice_id_exists_in_any_storage(self, voice_id):
        return False


_RAW = {
    "ttsModelProvider": "qwen_token_plan",
    "ttsModelUrl": "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1",
    "ttsModelId": "qwen-audio-3.0-tts-plus",
    "ttsVoiceId": "longanlufeng",
}


@pytest.mark.unit
def test_get_tts_worker_routes_explicit_token_plan_choice_ahead_of_mimo_assist(monkeypatch):
    cm = _CM(_RAW, core={"assistApi": "mimo", "OPENROUTER_URL": "https://api.xiaomimimo.com/v1"})
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen", has_custom_voice=False, voice_id="",
    )

    assert isinstance(worker, partial)
    assert worker.func is tts_client.qwen_token_plan_tts_worker
    assert worker.keywords == {
        "base_url": _RAW["ttsModelUrl"],
        "model": "qwen-audio-3.0-tts-plus",
        "configured_voice": "longanlufeng",
    }
    assert api_key == "sk-sp-test"
    assert provider_key == "qwen_token_plan"
    assert "qwen_token_plan" in tts_client.TTS_PROVIDER_REGISTRY


@pytest.mark.unit
@pytest.mark.parametrize("url", [
    "http://token-plan.cn-beijing.maas.aliyuncs.com",
    "https://evil.test/v1",          # left over from the custom slot
])
def test_get_tts_worker_replaces_off_whitelist_url_with_the_default_host(monkeypatch, url):
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM({**_RAW, "ttsModelUrl": url}))

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen", has_custom_voice=False, voice_id="",
    )

    assert worker.func is tts_client.qwen_token_plan_tts_worker
    assert worker.keywords["base_url"] == "https://token-plan.cn-beijing.maas.aliyuncs.com"
    assert (api_key, provider_key) == ("sk-sp-test", "qwen_token_plan")


@pytest.mark.unit
def test_get_tts_worker_without_key_uses_dummy(monkeypatch):
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM(_RAW, key=""))

    worker, api_key, provider_key = tts_client.get_tts_worker(
        core_api_type="qwen", has_custom_voice=False, voice_id="",
    )

    assert worker is tts_client.dummy_tts_worker
    assert api_key is None and provider_key is None


@pytest.mark.unit
@pytest.mark.parametrize("voice_meta, selected", [
    ({"provider": "doubao_tts"}, False),   # another provider's clone keeps its provider
    ({"provider": "glm_tts"}, False),
    (None, True),                          # e.g. Cherry: no stored voice, fall back inside the worker
])
def test_foreign_custom_voice_keeps_its_provider(monkeypatch, voice_meta, selected):
    from utils.tts.provider_registry import DispatchContext

    ctx = DispatchContext(
        core_config={"TTS_PROVIDER": ""}, cm=_CM(_RAW), voice_id="S_clone",
        has_custom_voice=True, voice_meta_loader=lambda: voice_meta,
    )

    assert worker_module._qwen_token_plan_is_selected(ctx) is selected


@pytest.mark.unit
def test_get_tts_worker_ignores_token_plan_when_not_selected(monkeypatch):
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: _CM({**_RAW, "ttsModelProvider": "custom"}))

    _, _, provider_key = tts_client.get_tts_worker(core_api_type="qwen", has_custom_voice=False, voice_id="")

    assert provider_key != "qwen_token_plan"


# ─── connectivity probe ──────────────────────────────────────────────────────

def _probe(monkeypatch, handler, url, api_key, voice_id=""):
    requests = []

    def recording(request):
        requests.append(request)
        return handler(request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(recording), **kw),
    )
    result = asyncio.run(_test_qwen_token_plan_tts_connectivity(url, api_key, model="", voice_id=voice_id))
    return requests, result


@pytest.mark.unit
@pytest.mark.parametrize("url", [
    "http://token-plan.cn-beijing.maas.aliyuncs.com",
    "https://evil.test/api/v1/services/audio/tts/SpeechSynthesizer",
])
def test_probe_refuses_off_whitelist_urls_without_sending_the_key(monkeypatch, url):
    requests, result = _probe(monkeypatch, lambda r: httpx.Response(200), url, "sk-sp-test")
    assert requests == []
    assert result == {"success": False, "error": result["error"], "error_code": "token_plan_url_invalid"}


@pytest.mark.unit
def test_probe_sends_non_streaming_request_and_accepts_audio_url(monkeypatch):
    requests, result = _probe(
        monkeypatch,
        lambda r: httpx.Response(200, json={"output": {"finish_reason": "stop", "audio": {"url": "https://oss.example/a"}}}),
        "https://token-plan.cn-beijing.maas.aliyuncs.com", "sk-sp-test", voice_id="Cherry",
    )
    assert result == {"success": True}
    assert str(requests[0].url) == _ENDPOINT
    assert requests[0].headers["authorization"] == "Bearer sk-sp-test"
    assert "x-dashscope-sse" not in requests[0].headers
    body = json.loads(requests[0].content)
    assert body["model"] == "qwen-audio-3.0-tts-plus"
    assert body["input"]["voice"] == QWEN_TOKEN_PLAN_TTS_DEFAULT_VOICE


@pytest.mark.unit
@pytest.mark.parametrize("api_key, error_code", [
    ("sk-0123456789abcdef", "token_plan_key_mismatch"),  # an ordinary Bailian key
    ("sk-sp-0123456789abcdef", "auth_failed"),
])
def test_probe_hints_at_an_ordinary_bailian_key(monkeypatch, api_key, error_code):
    _, result = _probe(
        monkeypatch, lambda r: httpx.Response(401, json={"code": "InvalidApiKey"}),
        "https://token-plan.cn-beijing.maas.aliyuncs.com", api_key,
    )
    assert result["success"] is False
    assert result["error_code"] == error_code
    assert api_key not in result["error"]


@pytest.mark.unit
def test_probe_is_routed_from_the_http_tts_dispatch(monkeypatch):
    from main_routers.config_router import connectivity

    calls = []

    async def fake_probe(url, api_key, model="", voice_id=""):
        calls.append((url, api_key, model, voice_id))
        return {"success": True}

    monkeypatch.setattr(connectivity, "_test_qwen_token_plan_tts_connectivity", fake_probe)
    result = asyncio.run(connectivity._test_connectivity_candidates(
        ["https://token-plan.cn-beijing.maas.aliyuncs.com"], "sk-sp-test", "qwen-audio-3.0-tts-plus",
        "tts", False, sub_type="qwen_token_plan", voice_id="longanlingxin",
    ))
    assert result["success"] is True
    assert calls == [("https://token-plan.cn-beijing.maas.aliyuncs.com", "sk-sp-test",
                      "qwen-audio-3.0-tts-plus", "longanlingxin")]


@pytest.mark.unit
def test_registry_and_settings_metadata_are_wired():
    from utils.tts import provider_registry

    provider = provider_registry.get("qwen_token_plan")
    assert provider is not None
    assert provider.tts_config_visible and provider.editable_endpoint
    # The settings page reads this to show the Key Book key read-only in the TTS slot.
    assert provider.api_key_field == "assistApiKeyQwenTokenPlan"
    assert (provider.probe_kind, provider.probe_sub_type) == ("http_tts", "qwen_token_plan")
    # The two named voices are presets of the registry entry (character voice validation).
    assert provider.preset_catalog is not None
    assert provider_registry.is_preset_voice("qwen_token_plan", "longanlufeng")
    assert not provider_registry.is_preset_voice("qwen_token_plan", "Cherry")
    # Explicit dropdown choice must beat MiMo's assistApi default (priority 60).
    assert provider.priority < provider_registry.get("mimo").priority

    with open("config/api_providers.json", encoding="utf-8") as f:
        providers = json.load(f)
    assert providers["api_key_registry"]["qwen_token_plan"]["config_field"] == "assistApiKeyQwenTokenPlan"
    entry = providers["keybook_api_providers"]["qwen_token_plan"]
    assert is_qwen_token_plan_url(entry["openrouter_url"])
    assert entry["probe_sub_type"] == provider.probe_sub_type
