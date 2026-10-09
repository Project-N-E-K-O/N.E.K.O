"""Successful updates retain their evidence when feedback reads fail."""

from dataclasses import replace

import pytest

from tests.unit.test_voice_management_service import fixture, imported, payload
from utils.voice_management import service
from utils.voice_management.types import VoiceManagementError


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [True, False], ids=["overwrite", "query"])
async def test_final_record_read_failure_preserves_success(fixture, monkeypatch, mutation):
    cm, adapter, ref = await imported(fixture)
    token = payload(adapter, cm)["context_token"]
    original_read = cm.get_imported_voice
    original_update = cm.aupdate_imported_voice
    projection_unavailable = False

    def read(*args, **kwargs):
        if projection_unavailable:
            raise OSError("isolated final snapshot read failure")
        return original_read(*args, **kwargs)

    async def update(*args, **kwargs):
        nonlocal projection_unavailable
        receipt = await original_update(*args, **kwargs)
        if args[2].get("overwrite_status") == "completed":
            projection_unavailable = True
        return receipt

    monkeypatch.setattr(cm, "get_imported_voice", read)
    monkeypatch.setattr(cm, "aupdate_imported_voice", update)
    if mutation:
        result = await service.overwrite_remote_voice(
            adapter, cm, ref, token=token, audio=b"audio", filename="sample.wav",
        )
    else:
        result = await service.refresh_overwrite_status(adapter, cm, ref, token=token)

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["details"] == {
        "attempt_outcome": "accepted" if mutation else "not_submitted",
        "state_sync": "saved", "voice_state": None,
    }
    assert len(adapter.mutations) == int(mutation)
    assert original_read(ref, include_inactive=True)["overwrite_status"] == "completed"
    assert token == payload(adapter, cm)["context_token"]


@pytest.mark.asyncio
async def test_final_projection_rejects_a_real_context_change(fixture, monkeypatch):
    cm, adapter, ref = await imported(fixture)
    token = payload(adapter, cm)["context_token"]
    original_update = cm.aupdate_imported_voice

    async def update(*args, **kwargs):
        receipt = await original_update(*args, **kwargs)
        if args[2].get("overwrite_status") == "completed":
            cm.key = "isolated-new-account"
        return receipt

    monkeypatch.setattr(cm, "aupdate_imported_voice", update)
    with pytest.raises(VoiceManagementError) as caught:
        await service.overwrite_remote_voice(
            adapter, cm, ref, token=token, audio=b"audio", filename="sample.wav",
        )
    assert caught.value.code == "CONTEXT_CHANGED"
    assert caught.value.details == {
        "attempt_outcome": "accepted", "state_sync": "saved", "voice_state": None,
    }
    assert len(adapter.mutations) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("read_error", [OSError, ValueError])
async def test_post_accept_context_read_failure_keeps_accepted_evidence(fixture, monkeypatch, read_error):
    cm, adapter, ref = await imported(fixture)
    token = payload(adapter, cm)["context_token"]
    original_resolve = adapter.resolve_runtime

    def unavailable(*args, **kwargs):
        raise read_error("isolated post-accept configuration read failure")

    async def accepted():
        monkeypatch.setattr(adapter, "resolve_runtime", unavailable)
        return adapter.remote

    adapter.on_mutation = accepted
    with pytest.raises(VoiceManagementError) as caught:
        await service.overwrite_remote_voice(
            adapter, cm, ref, token=token, audio=b"audio", filename="sample.wav",
        )
    assert caught.value.code == "STORAGE_ERROR"
    assert caught.value.details == {
        "attempt_outcome": "accepted", "state_sync": "unchanged", "voice_state": None,
    }
    assert len(adapter.mutations) == 1
    monkeypatch.setattr(adapter, "resolve_runtime", original_resolve)
    record = cm.get_imported_voice(ref, include_inactive=True)
    assert record["overwrite_status"] == "processing"
    assert record["overwrite_submission_phase"] == "submission_possible"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_response", [False, True], ids=["accepted", "wrong-remote-id"])
async def test_post_accept_owner_conflict_reports_unchanged_storage(fixture, monkeypatch, invalid_response):
    cm, adapter, ref = await imported(fixture)
    token = payload(adapter, cm)["context_token"]
    original_update = cm.aupdate_imported_voice

    async def takeover(*args, **kwargs):
        if args[2].get("overwrite_status") in {"completed", "unknown"}:
            cm.update_imported_voice(ref, args[1], {
                "overwrite_operation_id": "isolated-new-owner", "overwrite_status": "unknown",
            })
        return await original_update(*args, **kwargs)

    monkeypatch.setattr(cm, "aupdate_imported_voice", takeover)
    if invalid_response:
        async def wrong_remote():
            return replace(adapter.remote, voice_id="isolated-unrelated-remote")

        adapter.on_mutation = wrong_remote
    with pytest.raises(VoiceManagementError) as caught:
        await service.overwrite_remote_voice(
            adapter, cm, ref, token=token, audio=b"audio", filename="sample.wav",
        )
    assert caught.value.code == "CONTEXT_CHANGED"
    assert caught.value.details["attempt_outcome"] == ("unknown" if invalid_response else "accepted")
    assert caught.value.details["state_sync"] == "unchanged"
    assert caught.value.details["voice_state"]["operation_id"] == "isolated-new-owner"
    assert caught.value.details["voice_state"]["overwrite_status"] == "unknown"
    assert len(adapter.mutations) == 1
    record = cm.get_imported_voice(ref, include_inactive=True)
    assert record["overwrite_operation_id"] == "isolated-new-owner"
    assert record["overwrite_status"] == "unknown"
