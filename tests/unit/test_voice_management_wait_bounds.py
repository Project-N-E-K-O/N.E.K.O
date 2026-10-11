"""Bound request waiters without treating unfinished physical IO as rollback."""

import asyncio
import threading

import pytest

from tests.unit.test_voice_management_feedback_evidence import attach_disk, disk_fixture
from tests.unit.test_voice_management_routes import client, _wav  # noqa: F401
from tests.unit.test_voice_management_service import fixture, imported, payload  # noqa: F401
from tests.unit.test_voice_management_service import fixture as management_fixture  # noqa: F401
from utils.voice_management import service
from utils.voice_management.overwrite_recovery import recover_prepared_overwrite
from utils.voice_management.types import AttemptOutcome, StateSync, VoiceManagementError


@pytest.mark.asyncio
@pytest.mark.parametrize("late_failure", [False, True])
async def test_projection_deadline_preserves_result_evidence(fixture, monkeypatch, tmp_path, late_failure):
    cm, adapter, ref = await imported(fixture)
    await attach_disk(cm, monkeypatch, tmp_path)
    entered, release, finished = (threading.Event() for _ in range(3))
    read = cm.get_imported_voice

    def stalled_read(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(5)
            if late_failure:
                raise OSError("controlled late read failure")
            cm.key = "changed-after-projection-deadline"
            return read(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(cm, "get_imported_voice", stalled_read)
    task = asyncio.create_task(service.overwrite_result_details(
        adapter, cm, ref, token=payload(adapter, cm)["context_token"],
        attempt_outcome=AttemptOutcome.ACCEPTED, state_sync=StateSync.SAVED,
        strict_context=True, deadline=asyncio.get_running_loop().time() + .1,
    ))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        done, _ = await asyncio.wait({task}, timeout=1)
        assert task in done, "projection waiter must finish before physical read is released"
        assert task.result() == {"attempt_outcome": "accepted", "state_sync": "saved", "voice_state": None}
        assert adapter.mutations == []
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("expires_in_query", [False, True])
async def test_query_feedback_uses_remaining_budget(client, monkeypatch, tmp_path, expires_in_query):
    api, cm, adapter, ref, disk = await disk_fixture(client, monkeypatch, tmp_path)
    token = payload(adapter, cm)["context_token"]
    before = disk.read_bytes()
    original_timeout = asyncio.timeout
    deadlines = []

    def capture_timeout(seconds):
        assert seconds == 30
        deadline = original_timeout(seconds)
        deadlines.append(deadline)
        return deadline

    monkeypatch.setattr(asyncio, "timeout", capture_timeout)
    entered, release, finished = (threading.Event() for _ in range(3))
    remote_entered, remote_release = asyncio.Event(), asyncio.Event()
    update, read = cm.aupdate_imported_voice, cm.get_imported_voice
    project = False
    reads_after_expiry = []

    async def committed(*args, **kwargs):
        nonlocal project
        receipt = await update(*args, **kwargs)
        deadlines[0].reschedule(asyncio.get_running_loop().time() + .1)
        project = True
        return receipt

    async def blocked_remote(*args):
        remote_entered.set()
        await remote_release.wait()
        return adapter.remote

    def blocked_projection(*args, **kwargs):
        if deadlines and deadlines[0].expired():
            reads_after_expiry.append(True)
        if project:
            entered.set()
            try:
                assert release.wait(5)
            finally:
                finished.set()
        return read(*args, **kwargs)

    monkeypatch.setattr(cm, "get_imported_voice", blocked_projection)
    if expires_in_query:
        monkeypatch.setattr(adapter, "get_voice", blocked_remote)
    else:
        monkeypatch.setattr(cm, "aupdate_imported_voice", committed)
    request = asyncio.create_task(api.get(f"/api/characters/voices/{ref}/overwrite_status",
                                         params={"context_token": token}))
    try:
        if expires_in_query:
            await asyncio.wait_for(remote_entered.wait(), 3)
            deadlines[0].reschedule(asyncio.get_running_loop().time())
        else:
            assert await asyncio.to_thread(entered.wait, 3)
        done, _ = await asyncio.wait({request}, timeout=1)
        assert request in done, "feedback must not restart the exhausted query budget"
        response = request.result()
        assert response.status_code == (504 if expires_in_query else 200)
        if expires_in_query:
            assert response.json()["code"] == "UPSTREAM_TIMEOUT"
            assert disk.read_bytes() == before
        assert response.json()["details"] == {
            "attempt_outcome": "not_submitted", "voice_state": None,
            "state_sync": "unchanged" if expires_in_query else "saved",
        }
        assert reads_after_expiry == []
        assert adapter.mutations == []
    finally:
        release.set()
        remote_release.set()
        await asyncio.gather(request, return_exceptions=True)
        if project:
            assert await asyncio.to_thread(finished.wait, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary,replacement", [
    ("pre-transition", False), ("pre-transition", True),
    ("pre-publish", False), ("post-publish", False),
])
async def test_cancel_permission_retires_waiter_and_fences_late_io(
    fixture, monkeypatch, tmp_path, boundary, replacement,
):
    cm, adapter, ref = await imported(fixture)
    await attach_disk(cm, monkeypatch, tmp_path)
    token = payload(adapter, cm)["context_token"]
    entered, release = threading.Event(), threading.Event()
    helper, save = service.transition_with_context, cm.save_voice_storage
    first = True

    def gate():
        entered.set()
        assert release.wait(5)

    async def paused_transition(adapter, cm, runtime, record, *, action):
        nonlocal first
        if action == "submit" and first:
            first = False
            await asyncio.to_thread(gate)
        return await helper(adapter, cm, runtime, record, action=action)

    def paused_save(value):
        record = next(bucket[ref] for bucket in value.values() if ref in bucket)
        permission = (record.get("overwrite_submission_phase") == "submission_possible"
                      and record.get("overwrite_status") == "processing")
        if permission and boundary == "pre-publish":
            gate()
        save(value)
        if permission and boundary == "post-publish":
            gate()

    if boundary == "pre-transition":
        monkeypatch.setattr(service, "transition_with_context", paused_transition)
    else:
        monkeypatch.setattr(cm, "save_voice_storage", paused_save)
    old = asyncio.create_task(service.overwrite_remote_voice(
        adapter, cm, ref, token=token, audio=b"sample", filename="sample.wav"))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        lock = service._OVERWRITE_LOCKS[ref]
        old.cancel()
        old.cancel()
        done, _ = await asyncio.wait({old}, timeout=1)
        assert old in done, "cancellation must not join stalled permission IO"
        assert old.cancelled()
        assert not lock.locked()
        record = cm.get_imported_voice(ref, include_inactive=True)
        assert record["overwrite_status"] == "processing"
        assert record["overwrite_submission_phase"] == (
            "submission_possible" if boundary == "post-publish" else "prepared")
        assert adapter.mutations == []
        with pytest.raises(VoiceManagementError) as pending:
            await service.overwrite_remote_voice(adapter, cm, ref, token=token, audio=b"new", filename="new.wav")
        assert pending.value.code == "UPDATE_OUTCOME_UNKNOWN"
        if boundary == "pre-transition":
            recovered = await recover_prepared_overwrite(adapter, cm, ref, token=token,
                operation_id=record["overwrite_operation_id"], record_revision=record["_record_revision"])
            assert recovered["recovered"] is True
            assert "overwrite" in recovered["details"]["voice_state"]["actions"]
            if replacement:
                new = await service.overwrite_remote_voice(
                    adapter, cm, ref, token=token, audio=b"replacement", filename="new.wav")
                assert new["status"] == "completed"
            winner = cm.get_imported_voice(ref, include_inactive=True)
        settlements = tuple(service._CANCELLED_SUBMISSIONS)
        assert len(settlements) == 1
        release.set()
        await asyncio.wait_for(asyncio.gather(*settlements), 3)
        after = cm.get_imported_voice(ref, include_inactive=True)
        assert len(adapter.mutations) == int(replacement)
        if boundary == "pre-transition":
            assert after == winner, "late permission/cleanup must preserve recovery or replacement"
        else:
            assert after["overwrite_status"] == "unknown"
            assert after["overwrite_submission_phase"] == "submission_possible"
            assert after["overwrite_operation_id"] == record["overwrite_operation_id"]
    finally:
        release.set()
        await asyncio.gather(old, return_exceptions=True)
        if hasattr(service, "_CANCELLED_SUBMISSIONS"):
            await asyncio.gather(*tuple(service._CANCELLED_SUBMISSIONS), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("rejected", [False, True])
async def test_overwrite_feedback_default_bound_keeps_committed_evidence(client, monkeypatch, tmp_path, rejected):
    api, cm, adapter, ref, _ = await disk_fixture(client, monkeypatch, tmp_path)
    token = payload(adapter, cm)["context_token"]
    monkeypatch.setattr(service, "_FEEDBACK_TIMEOUT", .1)
    entered, release, finished = (threading.Event() for _ in range(3))
    update, read = cm.aupdate_imported_voice, cm.get_imported_voice
    project = False

    async def committed(*args, **kwargs):
        nonlocal project
        receipt = await update(*args, **kwargs)
        if args[2].get("overwrite_status") in {"completed", "failed"}:
            assert receipt.applied
            project = True
        return receipt

    async def reject():
        raise VoiceManagementError("UPSTREAM_REJECTED", 400, {"attempt_outcome": "rejected"})

    def paused_read(*args, **kwargs):
        if project:
            entered.set()
            try:
                assert release.wait(5)
            finally:
                finished.set()
        return read(*args, **kwargs)

    monkeypatch.setattr(cm, "aupdate_imported_voice", committed)
    monkeypatch.setattr(cm, "get_imported_voice", paused_read)
    if rejected:
        adapter.on_mutation = reject
    request = asyncio.create_task(api.post(f"/api/characters/voices/{ref}/overwrite",
        data={"context_token": token}, files={"audio": ("sample.wav", _wav(), "audio/wav")}))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        done, _ = await asyncio.wait({request}, timeout=1)
        assert request in done
        response = request.result()
        assert response.status_code == (400 if rejected else 200)
        if rejected:
            assert response.json()["code"] == "UPSTREAM_REJECTED"
        assert response.json()["details"] == {
            "attempt_outcome": "rejected" if rejected else "accepted", "state_sync": "saved", "voice_state": None,
        }
        assert len(adapter.mutations) == 1
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)
        if entered.is_set():
            assert await asyncio.to_thread(finished.wait, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["permission-error", "cleanup-error", "context-change", "owner-change"])
async def test_cancelled_permission_late_fault_cannot_unlock_or_damage_successor(fixture, monkeypatch, tmp_path, fault):
    cm, adapter, ref = await imported(fixture)
    await attach_disk(cm, monkeypatch, tmp_path)
    token = payload(adapter, cm)["context_token"]
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.transition_with_context

    async def paused(adapter, cm, runtime, record, *, action):
        receipt = await original(adapter, cm, runtime, record, action=action)
        entered.set()
        await release.wait()
        if fault == "permission-error":
            raise OSError("controlled lost permission receipt")
        return receipt

    monkeypatch.setattr(service, "transition_with_context", paused)
    task = asyncio.create_task(service.overwrite_remote_voice(
        adapter, cm, ref, token=token, audio=b"sample", filename="sample.wav"))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=1)
        assert task in done and task.cancelled()
        runtime = adapter.resolve_runtime(cm)
        if fault == "cleanup-error":
            def fail_save(value):
                raise OSError("controlled late cleanup failure")
            monkeypatch.setattr(cm, "save_voice_storage", fail_save)
        elif fault == "context-change":
            cm.key = "new-account-before-settlement"
        elif fault == "owner-change":
            await cm.aupdate_imported_voice(ref, runtime.scope_id, {
                "overwrite_operation_id": "successor", "overwrite_status": "processing",
            })
        winner = cm.get_imported_voice(ref, include_inactive=True)
        settlements = tuple(service._CANCELLED_SUBMISSIONS)
        release.set()
        await asyncio.wait_for(asyncio.gather(*settlements), 3)
        assert cm.get_imported_voice(ref, include_inactive=True) == winner
        assert winner["overwrite_status"] == "processing"
        assert winner["overwrite_submission_phase"] == "submission_possible"
        assert adapter.mutations == []
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*tuple(service._CANCELLED_SUBMISSIONS), return_exceptions=True)
