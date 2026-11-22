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
