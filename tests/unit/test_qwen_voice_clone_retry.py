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
async def test_timeout_adopts_newly_created_voice_without_retrying_create(monkeypatch):
    """When create_voice times out after succeeding remotely, adopt the new voice without retrying."""
    client = _make_client()
    create_calls = 0
    list_calls = 0

    async def fake_upload_file(*args, **kwargs):
        return "https://example.test/audio.wav"

    def fake_list_voices_by_prefix(prefix: str):
        nonlocal list_calls
        list_calls += 1
        if list_calls == 1:
            return [{"voice_id": "preexisting-v1", "gmt_create": "2026-01-01 00:00:00"}]
        return [
            {"voice_id": "preexisting-v1", "gmt_create": "2026-01-01 00:00:00"},
            {"voice_id": "new-voice-v2", "gmt_create": "2026-11-22 00:00:00"},
        ]

    def fake_create_voice(**kwargs):
        nonlocal create_calls
        create_calls += 1
        raise QwenVoiceCloneError("ResponseTimeout: request timed out")

    monkeypatch.setattr(client, "upload_file", fake_upload_file)
    monkeypatch.setattr(client, "_list_voices_by_prefix", fake_list_voices_by_prefix)
    monkeypatch.setattr(client, "create_voice", fake_create_voice)

    voice_id, file_url, _ = await client.clone_voice(
        io.BytesIO(b"audio"),
        "sample.wav",
        "neko01",
        ["zh"],
    )

    assert voice_id == "new-voice-v2"
    assert file_url == "https://example.test/audio.wav"
    assert create_calls == 1
    assert list_calls == 2


@pytest.mark.asyncio
async def test_timeout_retries_only_when_list_voices_confirms_no_new_voice(monkeypatch):
    """When list_voices confirms no new voice was created, create_voice may retry cleanly."""
    client = _make_client()
    create_calls = 0

    async def fake_upload_file(*args, **kwargs):
        return "https://example.test/audio.wav"

    def fake_list_voices_by_prefix(prefix: str):
        return [{"voice_id": "preexisting-v1", "gmt_create": "2026-01-01 00:00:00"}]

    def fake_create_voice(**kwargs):
        nonlocal create_calls
        create_calls += 1
        if create_calls == 1:
            raise QwenVoiceCloneError("ResponseTimeout")
        return ("created-on-retry-v3", "req-2")

    monkeypatch.setattr(client, "upload_file", fake_upload_file)
    monkeypatch.setattr(client, "_list_voices_by_prefix", fake_list_voices_by_prefix)
    monkeypatch.setattr(client, "create_voice", fake_create_voice)

    voice_id, file_url, req_id = await client.clone_voice(
        io.BytesIO(b"audio"),
        "sample.wav",
        "neko01",
        ["zh"],
    )

    assert voice_id == "created-on-retry-v3"
    assert file_url == "https://example.test/audio.wav"
    assert req_id == "req-2"
    assert create_calls == 2


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
