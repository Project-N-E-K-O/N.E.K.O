"""Exercise recovery races against the actual service submission boundary."""

import asyncio
import threading

import pytest

from tests.unit.test_voice_management_service import fixture, imported, payload
from utils.voice_management import overwrite_recovery, service
from utils.voice_management.types import VoiceManagementError


async def overwrite(cm, adapter, ref):
    return await service.overwrite_remote_voice(
        adapter, cm, ref, token=payload(adapter, cm)["context_token"], audio=b"sample", filename="sample.wav",
    )


async def recover(cm, adapter, ref):
    record = cm.get_imported_voice(ref)
    return await overwrite_recovery.recover_prepared_overwrite(
        adapter, cm, ref, token=payload(adapter, cm)["context_token"],
        operation_id=record["overwrite_operation_id"], record_revision=record["_record_revision"],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("new_operation", [False, True])
async def test_recovery_winner_fences_resumed_old_task(fixture, monkeypatch, new_operation):
    cm, adapter, ref = await imported(fixture)
    prepared, release = asyncio.Event(), asyncio.Event()
    original = service.transition_with_context
    first = True

    async def blocked(adapter, cm, runtime, record, *, action):
        nonlocal first
        if action == "submit" and first:
            first = False
            prepared.set()
            await release.wait()
        return await original(adapter, cm, runtime, record, action=action)

    monkeypatch.setattr(service, "transition_with_context", blocked)
    old = asyncio.create_task(overwrite(cm, adapter, ref))
    try:
        await asyncio.wait_for(prepared.wait(), timeout=5)
        result = await recover(cm, adapter, ref)
        assert result["recovered"] is True
        recovered_owner = cm.get_imported_voice(ref)["overwrite_operation_id"]
        # A live old request still owns the per-ref lock. Recovery is independent
        # of it, but a new overwrite must wait for that request to finish.
        if new_operation:
            with pytest.raises(VoiceManagementError) as in_progress:
                await overwrite(cm, adapter, ref)
            assert in_progress.value.code == "OPERATION_IN_PROGRESS"
        release.set()
        with pytest.raises(VoiceManagementError) as stopped:
            await old
        assert stopped.value.code == "VOICE_STATE_CHANGED"
        assert adapter.mutations == []
        record = cm.get_imported_voice(ref)
        assert record["overwrite_status"] == "failed"
        assert record["overwrite_terminal_reason"] == "not_submitted_recovered"
        if new_operation:
            replacement = await overwrite(cm, adapter, ref)
            assert replacement["status"] == "completed"
            assert len(adapter.mutations) == 1
            assert cm.get_imported_voice(ref)["overwrite_operation_id"] != recovered_owner
    finally:
        release.set()
        if not old.done():
            old.cancel()
        await asyncio.gather(old, return_exceptions=True)


@pytest.mark.asyncio
async def test_submission_winner_rejects_recovery_while_old_task_waits(fixture, monkeypatch):
    cm, adapter, ref = await imported(fixture)
    submitted, release = asyncio.Event(), asyncio.Event()
    original = service.transition_with_context

    async def blocked(adapter, cm, runtime, record, *, action):
        receipt = await original(adapter, cm, runtime, record, action=action)
        if action == "submit" and receipt.applied:
            submitted.set()
            await release.wait()
        return receipt

    monkeypatch.setattr(service, "transition_with_context", blocked)
    old = asyncio.create_task(overwrite(cm, adapter, ref))
    try:
        await asyncio.wait_for(submitted.wait(), timeout=5)
        with pytest.raises(VoiceManagementError) as stopped:
            await recover(cm, adapter, ref)
        assert stopped.value.code == "VOICE_STATE_CHANGED"
        assert cm.get_imported_voice(ref)["overwrite_status"] == "processing"
        release.set()
        result = await old
        assert result["status"] == "completed"
        assert len(adapter.mutations) == 1
    finally:
        release.set()
        if not old.done():
            old.cancel()
        await asyncio.gather(old, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_permission_waiter_joins_write_and_keeps_unknown(fixture, monkeypatch):
    cm, adapter, ref = await imported(fixture)
    persisted, release = threading.Event(), threading.Event()
    original = cm.transition_imported_voice_overwrite

    def blocked(*args, **kwargs):
        receipt = original(*args, **kwargs)
        if kwargs["action"] == "submit" and receipt.applied:
            persisted.set()
            assert release.wait(timeout=5)
        return receipt

    monkeypatch.setattr(cm, "transition_imported_voice_overwrite", blocked)
    old = asyncio.create_task(overwrite(cm, adapter, ref))
    try:
        assert await asyncio.to_thread(persisted.wait, 5)
        old.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await old
        record = cm.get_imported_voice(ref)
        assert record["overwrite_submission_phase"] == "submission_possible"
        assert record["overwrite_status"] == "unknown"
        assert adapter.mutations == []
        with pytest.raises(VoiceManagementError) as protected:
            await overwrite(cm, adapter, ref)
        assert protected.value.code == "UPDATE_OUTCOME_UNKNOWN"
    finally:
        release.set()
        if not old.done():
            old.cancel()
        await asyncio.gather(old, return_exceptions=True)


@pytest.mark.asyncio
async def test_owner_conflict_during_error_cleanup_is_unchanged(fixture):
    cm, adapter, ref = await imported(fixture)
    successor = None

    async def replace_then_fail():
        nonlocal successor
        successor = cm.update_imported_voice(ref, adapter.resolve_runtime(cm).scope_id, {
            "overwrite_operation_id": "successor", "overwrite_status": "processing",
        })
        raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN", 502)

    adapter.on_mutation = replace_then_fail
    with pytest.raises(VoiceManagementError) as stopped:
        await overwrite(cm, adapter, ref)
    assert stopped.value.code == "UPDATE_OUTCOME_UNKNOWN"
    assert stopped.value.details["state_sync"] == "unchanged"
    assert stopped.value.details["voice_state"]["operation_id"] == "successor"
    assert stopped.value.details["voice_state"]["actions"] == ["refresh"]
    assert cm.get_imported_voice(ref)["_record_revision"] == successor["_record_revision"]
