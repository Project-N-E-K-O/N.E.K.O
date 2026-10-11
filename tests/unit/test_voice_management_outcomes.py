"""Request outcome and persisted state are independent evidence."""

import asyncio

import pytest

from tests.unit.test_voice_management_service import fixture, imported, payload  # noqa: F401
from tests.unit.test_voice_management_service import fixture as management_fixture  # noqa: F401
from tests.unit.test_voice_management_routes import client, _import  # noqa: F401
from utils.voice_management import service
from utils.voice_management.types import VoiceManagementError


@pytest.mark.asyncio
async def test_route_validation_reports_no_submission_and_allows_correcting_audio(client):
    api, cm, adapter = client
    ref = await _import(client)
    response = await api.post(f"/api/characters/voices/{ref}/overwrite", data={
        "context_token": payload(adapter, cm)["context_token"],
    }, files={"audio": ("empty.wav", b"", "audio/wav")})
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_AUDIO"
    details = response.json()["details"]
    assert details["attempt_outcome"] == "not_submitted"
    assert "overwrite" in details["voice_state"]["actions"]
    assert adapter.mutations == []


@pytest.mark.asyncio
async def test_equal_winner_fields_do_not_fake_an_applied_write_receipt(fixture):
    cm, adapter, ref = await imported(fixture)
    scope = adapter.resolve_runtime(cm).scope_id
    revision = cm.get_imported_voice(ref).get("_record_revision", 0)
    values = {"overwrite_status": "failed", "overwrite_operation_id": "same-owner"}
    first = await cm.aupdate_imported_voice(ref, scope, values,
                                            expected_record_revision=revision, return_receipt=True)
    second = await cm.aupdate_imported_voice(ref, scope, values,
                                             expected_record_revision=revision, return_receipt=True)
    assert first.applied is True
    assert second.applied is False
    assert first.record == second.record


@pytest.mark.asyncio
async def test_overwrite_reports_persisted_cas_winner_not_requested_terminal_status(fixture, monkeypatch):
    cm, adapter, ref = await imported(fixture)
    original = cm.aupdate_imported_voice

    async def competing_snapshot(local_ref, scope, values, **kwargs):
        if values.get("overwrite_status") == "completed":
            await original(local_ref, scope, {"overwrite_status": "processing"})
        return await original(local_ref, scope, values, **kwargs)

    monkeypatch.setattr(cm, "aupdate_imported_voice", competing_snapshot)
    result = await service.overwrite_remote_voice(adapter, cm, ref, token=payload(adapter, cm)["context_token"],
                                                 audio=b"audio", filename="v.wav")
    assert result["status"] == "processing"
    assert result["details"]["voice_state"]["overwrite_status"] == "processing"
    assert result["details"]["voice_state"]["actions"] == ["refresh"]
    assert result["details"]["state_sync"] == "unchanged"


@pytest.mark.asyncio
async def test_pending_record_reports_old_owner_and_not_submitted(fixture):
    cm, adapter, ref = await imported(fixture)
    scope = adapter.resolve_runtime(cm).scope_id
    await cm.aupdate_imported_voice(ref, scope, {
        "overwrite_status": "unknown", "overwrite_operation_id": "previous-operation",
    })
    with pytest.raises(VoiceManagementError) as caught:
        await service.overwrite_remote_voice(adapter, cm, ref, token=payload(adapter, cm)["context_token"],
                                             audio=b"audio", filename="v.wav")
    error = caught.value
    assert error.code == "UPDATE_OUTCOME_UNKNOWN"
    assert error.details["attempt_outcome"] == "not_submitted"
    state = error.details["voice_state"]
    assert state["operation_id"] == "previous-operation"
    assert state["overwrite_status"] == "unknown"
    assert state["record_revision"] == cm.get_imported_voice(ref)["_record_revision"]
    assert state["actions"] == ["refresh"]
    assert error.details["state_sync"] == "unchanged"
    assert adapter.mutations == []


@pytest.mark.asyncio
@pytest.mark.parametrize("save_fails", [False, True])
async def test_evidenced_rejection_unlocks_only_after_persistence(fixture, monkeypatch, save_fails):
    cm, adapter, ref = await imported(fixture)
    original = cm.aupdate_imported_voice

    async def persist(local_ref, scope, values, **kwargs):
        if save_fails and values.get("overwrite_status") == "failed":
            raise OSError("isolated save failure")
        return await original(local_ref, scope, values, **kwargs)

    async def reject():
        raise VoiceManagementError("UPSTREAM_REJECTED", 400, {"attempt_outcome": "rejected"})

    monkeypatch.setattr(cm, "aupdate_imported_voice", persist)
    adapter.on_mutation = reject
    with pytest.raises(VoiceManagementError) as caught:
        await service.overwrite_remote_voice(adapter, cm, ref, token=payload(adapter, cm)["context_token"],
                                             audio=b"audio", filename="v.wav")
    details = caught.value.details
    assert details["attempt_outcome"] == "rejected"
    assert details["state_sync"] == ("failed" if save_fails else "saved")
    assert caught.value.code == "UPSTREAM_REJECTED"
    assert details["voice_state"]["overwrite_status"] == ("processing" if save_fails else "failed")
    assert ("overwrite" in details["voice_state"]["actions"]) is (not save_fails)
    assert len(adapter.mutations) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["UPSTREAM_REJECTED", "AUTH_FAILED", "RATE_LIMITED"])
async def test_diagnostic_code_without_mutation_evidence_cannot_unlock(fixture, code):
    cm, adapter, ref = await imported(fixture)

    async def unclassified():
        raise VoiceManagementError(code, 400)

    adapter.on_mutation = unclassified
    with pytest.raises(VoiceManagementError) as caught:
        await service.overwrite_remote_voice(adapter, cm, ref, token=payload(adapter, cm)["context_token"],
                                             audio=b"audio", filename="v.wav")
    assert caught.value.code == code
    assert caught.value.details["attempt_outcome"] == "unknown"
    assert caught.value.details["voice_state"]["actions"] == ["refresh"]
    assert cm.get_imported_voice(ref)["overwrite_status"] == "unknown"


@pytest.mark.asyncio
async def test_active_operation_returns_processing_advice_without_second_submission(fixture):
    cm, adapter, ref = await imported(fixture)
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold():
        entered.set()
        await release.wait()
        return adapter.remote

    adapter.on_mutation = hold
    kwargs = {"token": payload(adapter, cm)["context_token"], "audio": b"audio", "filename": "v.wav"}
    task = asyncio.create_task(service.overwrite_remote_voice(adapter, cm, ref, **kwargs))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(VoiceManagementError) as caught:
            await service.overwrite_remote_voice(adapter, cm, ref, **kwargs)
        assert caught.value.code == "OPERATION_IN_PROGRESS"
        assert caught.value.details["attempt_outcome"] == "not_submitted"
        assert caught.value.details["voice_state"]["overwrite_status"] == "processing"
        assert caught.value.details["voice_state"]["actions"] == ["refresh"]
        assert len(adapter.mutations) == 1
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_changed_configuration_never_projects_old_actions(fixture):
    cm, adapter, ref = await imported(fixture)

    async def changed():
        cm.key = "new-account"
        return adapter.remote

    adapter.on_mutation = changed
    with pytest.raises(VoiceManagementError) as caught:
        await service.overwrite_remote_voice(adapter, cm, ref, token=payload(adapter, cm)["context_token"],
                                             audio=b"audio", filename="v.wav")
    assert caught.value.code == "CONTEXT_CHANGED"
    assert caught.value.details["voice_state"] is None


@pytest.mark.asyncio
async def test_state_read_failure_returns_no_invented_snapshot(fixture, monkeypatch):
    cm, adapter, ref = await imported(fixture)
    original = cm.get_imported_voice

    def read(local_ref, **kwargs):
        record = original(local_ref, **kwargs)
        if read.failed:
            raise OSError("isolated state read failure")
        return record

    read.failed = False

    async def rejection():
        read.failed = True
        raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN", 502)

    monkeypatch.setattr(cm, "get_imported_voice", read)
    adapter.on_mutation = rejection
    with pytest.raises(VoiceManagementError) as caught:
        await service.overwrite_remote_voice(adapter, cm, ref, token=payload(adapter, cm)["context_token"],
                                             audio=b"audio", filename="v.wav")
    assert caught.value.details["voice_state"] is None
