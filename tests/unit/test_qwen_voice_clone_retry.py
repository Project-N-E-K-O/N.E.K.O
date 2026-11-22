"""Unit tests for F0551: Qwen/CosyVoice timeout reconciliation avoids duplicate voice creation."""

from __future__ import annotations

import io
import pytest

from utils.voice_clone import QwenVoiceCloneClient, QwenVoiceCloneError


def _make_client() -> QwenVoiceCloneClient:
    client = QwenVoiceCloneClient(
        api_key="test-api-key",
        tflink_upload_url="https://example.test/upload",
        dashscope_base_url="https://dashscope.aliyuncs.com/api/v1",
    )
    client.RETRY_DELAY = 0
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [
    "OK", "DEPLOYING", "empty", "wrong_audio", "wrong_model", "UNDEPLOYED",
    "detail_rejected", "ambiguous", "missing_link",
])
async def test_timeout_reconciles_only_the_uploaded_audio(monkeypatch, outcome):
    import httpx

    client = _make_client()
    audio_url = "https://example.test/this-upload.wav"
    model = "cosyvoice-v3-plus"
    calls = []

    async def upload(*args):
        return audio_url

    def post(endpoint, *, headers, json, timeout):
        assert endpoint == "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
        assert headers["Authorization"] == "Bearer test-api-key"
        fields = json["input"]
        action = fields["action"]
        calls.append(action)
        if action == "create_voice":
            assert fields["url"] == audio_url
            raise httpx.ReadTimeout("connection interrupted after commit")
        if action == "list_voice":
            rows = [{"voice_id": "old", "status": "OK"}]
            if calls.count(action) == 2 and outcome != "empty":
                rows.extend([
                    {"voice_id": "another-upload", "status": "OK"},
                    {"voice_id": "candidate", "status": outcome if outcome == "UNDEPLOYED" else "OK"},
                ])
            return httpx.Response(200, json={"output": {"voice_list": rows}})
        assert action == "query_voice"
        assert fields["voice_id"] != "old"
        matches_upload = fields["voice_id"] == "candidate" or outcome == "ambiguous"
        output = {
            "resource_link": audio_url if matches_upload and outcome != "wrong_audio" else "different-url",
            "target_model": "other-model" if outcome == "wrong_model" else model,
            "status": {"detail_rejected": "UNDEPLOYED", "DEPLOYING": "DEPLOYING"}.get(outcome, "OK"),
        }
        if outcome == "missing_link":
            output.pop("resource_link")
        return httpx.Response(200, json={"output": output})

    monkeypatch.setattr(client, "upload_file", upload)
    monkeypatch.setattr(httpx, "post", post)
    result = client.clone_voice(io.BytesIO(b"audio"), "sample.wav", "neko01", [], target_model=model)
    if outcome in ("OK", "DEPLOYING"):
        assert await result == ("candidate", audio_url, None)
    else:
        with pytest.raises(QwenVoiceCloneError, match="已停止重试"):
            await result
    assert calls.count("create_voice") == 1
    assert calls.count("list_voice") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["baseline_failed", "reconcile_failed"])
async def test_timeout_fails_closed_when_remote_outcome_is_unknown(monkeypatch, failure_mode):
    """If baseline or post-timeout list_voices fails, do not retry non-idempotent create_voice."""
    client = _make_client()
    create_calls = 0
    list_calls = 0

    async def fake_upload_file(*args, **kwargs):
        return "https://example.test/audio.wav"

    def fake_list_voices_by_prefix(prefix: str):
        nonlocal list_calls
        list_calls += 1
        if failure_mode == "baseline_failed" or list_calls >= 2:
            raise RuntimeError("list_voices unavailable")
        return []

    def fake_create_voice(**kwargs):
        nonlocal create_calls
        create_calls += 1
        raise QwenVoiceCloneError("timeout while waiting for response")

    monkeypatch.setattr(client, "upload_file", fake_upload_file)
    monkeypatch.setattr(client, "_list_voices_by_prefix", fake_list_voices_by_prefix)
    monkeypatch.setattr(client, "create_voice", fake_create_voice)

    with pytest.raises(QwenVoiceCloneError, match="超时"):
        await client.clone_voice(
            io.BytesIO(b"audio"),
            "sample.wav",
            "neko01",
            ["zh"],
        )

    assert create_calls == 1


@pytest.mark.asyncio
async def test_download_failed_still_retries_up_to_max_retries(monkeypatch):
    """Preserve F0552 download-failure retry path independently of timeout reconciliation."""
    client = _make_client()
    create_calls = 0

    async def fake_upload_file(*args, **kwargs):
        return "https://example.test/audio.wav"

    def fake_list_voices_by_prefix(prefix: str):
        return []

    def fake_create_voice(**kwargs):
        nonlocal create_calls
        create_calls += 1
        if create_calls < 3:
            raise QwenVoiceCloneError("download audio failed: 415")
        return ("voice-after-download-retry", "req-3")

    monkeypatch.setattr(client, "upload_file", fake_upload_file)
    monkeypatch.setattr(client, "_list_voices_by_prefix", fake_list_voices_by_prefix)
    monkeypatch.setattr(client, "create_voice", fake_create_voice)

    voice_id, _, req_id = await client.clone_voice(
        io.BytesIO(b"audio"),
        "sample.wav",
        "neko01",
        ["zh"],
    )

    assert voice_id == "voice-after-download-retry"
    assert req_id == "req-3"
    assert create_calls == 3


@pytest.mark.parametrize(
    ("mode", "expected_marker"),
    [
        ("http_408", "超时"),
        ("http_504", "超时"),
        ("transport_timeout", "超时"),
        ("json_timeout", "超时"),
        ("http_415", "下载"),
        ("json_download_failed", "下载"),
    ],
)
def test_create_voice_preserves_direct_link_chinese_classification_markers(monkeypatch, mode, expected_marker):
    """Direct-link create_voice callers classify HTTP 408/415 via Chinese markers alongside clone_voice."""
    import httpx

    client = _make_client()

    def post(endpoint, *, headers, json, timeout):
        if mode == "http_408":
            return httpx.Response(408, text="timeout")
        if mode == "http_504":
            return httpx.Response(504, text="gateway timeout")
        if mode == "transport_timeout":
            raise httpx.ReadTimeout("read timed out")
        if mode == "json_timeout":
            return httpx.Response(500, json={"code": "ResponseTimeout", "message": "upstream timeout"})
        if mode == "http_415":
            return httpx.Response(415, text="unsupported media")
        return httpx.Response(400, json={"code": "Audio.DownloadFailed", "message": "Download audio failed"})

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(QwenVoiceCloneError) as exc_info:
        client.create_voice("neko01", "https://example.test/audio.wav", [], target_model="cosyvoice-v3-plus")

    message = str(exc_info.value)
    assert expected_marker in message
    lower = message.lower()
    if expected_marker == "超时":
        assert "timeout" in lower or "responsetimeout" in lower
    else:
        assert "download audio failed" in lower


@pytest.mark.asyncio
async def test_clone_voice_retries_http_415_and_reconciles_http_504(monkeypatch):
    """Real create_voice HTTP 415 retries to success, while HTTP 504 reconciles without blind retry."""
    import httpx

    client = _make_client()
    audio_url = "https://example.test/audio.wav"
    model = "cosyvoice-v3-plus"

    async def upload(*args):
        return audio_url

    monkeypatch.setattr(client, "upload_file", upload)

    # 1) HTTP 415 retries up to success.
    create_attempts = 0

    def post_415_then_ok(endpoint, *, headers, json, timeout):
        nonlocal create_attempts
        action = json["input"]["action"]
        if action == "list_voice":
            return httpx.Response(200, json={"output": {"voice_list": []}})
        assert action == "create_voice"
        create_attempts += 1
        if create_attempts < 3:
            return httpx.Response(415, text="download failed")
        return httpx.Response(200, json={"output": {"voice_id": "v-415-ok"}, "request_id": "req-415"})

    monkeypatch.setattr(httpx, "post", post_415_then_ok)
    voice_id, _, req_id = await client.clone_voice(
        io.BytesIO(b"audio"), "sample.wav", "neko01", [], target_model=model,
    )
    assert voice_id == "v-415-ok"
    assert req_id == "req-415"
    assert create_attempts == 3

    # 2) HTTP 504 triggers reconciliation once without repeating create_voice.
    calls = []

    def post_504_then_reconcile(endpoint, *, headers, json, timeout):
        fields = json["input"]
        action = fields["action"]
        calls.append(action)
        if action == "create_voice":
            return httpx.Response(504, text="gateway timeout")
        if action == "list_voice":
            rows = [] if calls.count("list_voice") == 1 else [{"voice_id": "v-504-reconciled", "status": "OK"}]
            return httpx.Response(200, json={"output": {"voice_list": rows}})
        assert action == "query_voice"
        return httpx.Response(
            200,
            json={"output": {"resource_link": audio_url, "target_model": model, "status": "OK"}},
        )

    monkeypatch.setattr(httpx, "post", post_504_then_reconcile)
    adopted_id, _, adopted_req = await client.clone_voice(
        io.BytesIO(b"audio"), "sample.wav", "neko01", [], target_model=model,
    )
    assert adopted_id == "v-504-reconciled"
    assert adopted_req is None
    assert calls.count("create_voice") == 1

