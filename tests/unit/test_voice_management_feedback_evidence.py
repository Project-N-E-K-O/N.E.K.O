"""Result evidence survives faults, CAS conflicts and live physical writes."""
import asyncio
import json
import threading
from dataclasses import replace

import pytest

from tests.unit.test_voice_management_service import fixture as management_fixture, payload  # noqa: F401
from tests.unit.test_voice_management_routes import client, _import, _wav  # noqa: F401
from utils.file_utils import atomic_write_json
from utils.voice_management import service
from utils.voice_management.overwrite_recovery import recover_prepared_overwrite
from utils.voice_management.types import VoiceManagementError


async def attach_disk(cm, monkeypatch, tmp_path):
    storage = tmp_path / "voice_storage.json"
    await asyncio.to_thread(atomic_write_json, storage, cm.storage)
    monkeypatch.setattr(cm, "load_voice_storage", lambda: json.loads(storage.read_text(encoding="utf-8")))
    monkeypatch.setattr(cm, "save_voice_storage", lambda value: atomic_write_json(storage, value))
    return storage


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [RuntimeError, KeyError, OSError, ValueError])
async def test_post_accept_error_keeps_pending_and_blocks_second_submission(client, monkeypatch, tmp_path, fault):
    api, cm, adapter = client
    ref = await _import(client)
    storage = await attach_disk(cm, monkeypatch, tmp_path)
    token = payload(adapter, cm)["context_token"]
    resolve = adapter.resolve_runtime

    def unavailable(*args, **kwargs):
        raise fault("controlled configuration read failure after provider acknowledgement")

    async def accepted():
        monkeypatch.setattr(adapter, "resolve_runtime", unavailable)
        return adapter.remote

    adapter.on_mutation = accepted
    result = await api.post(f"/api/characters/voices/{ref}/overwrite", data={"context_token": token},
                            files={"audio": ("sample.wav", _wav(), "audio/wav")})
    assert result.status_code == 500
    assert len(adapter.mutations) == 1
    monkeypatch.setattr(adapter, "resolve_runtime", resolve)
    record = await asyncio.to_thread(cm.get_imported_voice, ref, include_inactive=True)
    assert record["overwrite_status"] == "processing"
    assert record["overwrite_submission_phase"] == "submission_possible"
    disk_before = await asyncio.to_thread(storage.read_bytes)
    retry = await api.post(f"/api/characters/voices/{ref}/overwrite", data={"context_token": token},
                           files={"audio": ("sample.wav", _wav(), "audio/wav")})
    assert retry.status_code == 409
    assert retry.json()["code"] == "UPDATE_OUTCOME_UNKNOWN"
    assert retry.json()["details"]["voice_state"]["actions"] == ["refresh"]
    recovery = await api.post(f"/api/characters/voices/{ref}/recover_overwrite", json={
        "context_token": token, "operation_id": record["overwrite_operation_id"],
        "record_revision": record["_record_revision"],
    })
    assert recovery.status_code == 409
    assert len(adapter.mutations) == 1
    assert await asyncio.to_thread(storage.read_bytes) == disk_before
    print({"fault": fault.__name__, "feedback": result.json()["details"],
           "mutations": len(adapter.mutations), "retry": retry.status_code, "recovery": recovery.status_code})
    assert result.json()["details"]["attempt_outcome"] == "accepted"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["read", "context", "timeout"])
async def test_real_json_commit_receipt_survives_post_write_check(client, monkeypatch, tmp_path, fault):
    api, cm, adapter = client
    ref = await _import(client)
    storage = await attach_disk(cm, monkeypatch, tmp_path)
    token = payload(adapter, cm)["context_token"]
    before = await asyncio.to_thread(cm.get_imported_voice, ref)
    update = cm.aupdate_imported_voice
    timeouts = []
    original_timeout = asyncio.timeout

    if fault == "timeout":
        def controlled_timeout(seconds):
            deadline = original_timeout(seconds)
            timeouts.append(deadline)
            return deadline
        monkeypatch.setattr(asyncio, "timeout", controlled_timeout)

    def unavailable(*args, **kwargs):
        raise OSError("controlled configuration read failure after JSON commit")

    async def committed(*args, **kwargs):
        receipt = await update(*args, **kwargs)
        assert receipt.applied
        if fault == "read":
            monkeypatch.setattr(adapter, "resolve_runtime", unavailable)
        elif fault == "context":
            cm.key = "controlled-new-account"
        else:
            # Expire the actual query deadline after a confirmed disk commit.
            # The next await delivers cancellation without timing sleeps.
            timeouts[-1].reschedule(asyncio.get_running_loop().time())
        return receipt

    monkeypatch.setattr(cm, "aupdate_imported_voice", committed)
    response = await api.get(f"/api/characters/voices/{ref}/overwrite_status", params={"context_token": token})
    assert response.status_code == {"read": 500, "context": 409, "timeout": 504}[fault]
    if fault == "timeout":
        assert timeouts[-1].expired()
        assert response.json()["code"] == "UPSTREAM_TIMEOUT"
    saved = await asyncio.to_thread(cm.get_imported_voice, ref, include_inactive=True)
    assert saved["_record_revision"] == before.get("_record_revision", 0) + 1
    disk = json.loads(await asyncio.to_thread(storage.read_text, encoding="utf-8"))
    persisted = {key: value for key, value in saved.items() if key != "availability"}
    assert any(value.get(ref) == persisted for value in disk.values() if isinstance(value, dict))
    assert adapter.mutations == []
    print({"fault": fault, "stored_revision": saved["_record_revision"], "feedback": response.json()["details"]})
    assert response.json()["details"]["state_sync"] == "saved"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [False, True])
async def test_initial_read_failure_never_touches_real_json(client, monkeypatch, tmp_path, mutation):
    api, cm, adapter = client
    ref = await _import(client)
    storage = await attach_disk(cm, monkeypatch, tmp_path)
    token = payload(adapter, cm)["context_token"]
    before = await asyncio.to_thread(storage.read_bytes)
    original = cm.get_imported_voice
    calls = 0

    def unreadable(*args, **kwargs):
        nonlocal calls
        calls += 1
        # Route resolves the adapter once before entering the service.
        if calls == 1:
            return original(*args, **kwargs)
        raise OSError("controlled service initial record read failure")

    monkeypatch.setattr(cm, "get_imported_voice", unreadable)
    if mutation:
        response = await api.post(f"/api/characters/voices/{ref}/overwrite", data={"context_token": token},
                                  files={"audio": ("sample.wav", _wav(), "audio/wav")})
    else:
        response = await api.get(f"/api/characters/voices/{ref}/overwrite_status", params={"context_token": token})
    assert response.status_code == 500
    assert response.json()["code"] == "STORAGE_ERROR"
    assert await asyncio.to_thread(storage.read_bytes) == before
    assert adapter.mutations == []
    print({"mutation": mutation, "feedback": response.json()["details"]})
    assert response.json()["details"]["state_sync"] == "unchanged"

async def disk_fixture(client, monkeypatch, tmp_path):
    api, cm, adapter = client
    ref = await _import(client)
    storage = tmp_path / "voice_storage.json"
    await asyncio.to_thread(atomic_write_json, storage, cm.storage)
    monkeypatch.setattr(cm, "load_voice_storage", lambda: json.loads(storage.read_text(encoding="utf-8")))
    monkeypatch.setattr(cm, "save_voice_storage", lambda value: atomic_write_json(storage, value))
    return api, cm, adapter, ref, storage


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["query", "recover"])
@pytest.mark.parametrize("fault", [RuntimeError, KeyError, OSError])
async def test_post_commit_errors_keep_feedback_across_parallel_routes(client, monkeypatch, tmp_path, operation, fault):
    api, cm, adapter, ref, storage = await disk_fixture(client, monkeypatch, tmp_path)
    token = payload(adapter, cm)["context_token"]
    resolve = adapter.resolve_runtime
    scope = resolve(cm).scope_id
    if operation == "recover":
        record = await cm.aupdate_imported_voice(ref, scope, {
            "overwrite_operation_id": "controlled-prepared", "overwrite_status": "processing",
            "overwrite_submission_phase": "prepared",
        })
    before = await asyncio.to_thread(cm.get_imported_voice, ref)

    def unavailable(*args, **kwargs):
        raise fault("controlled post-commit context read failure")

    if operation == "query":
        update = cm.aupdate_imported_voice

        async def committed(*args, **kwargs):
            receipt = await update(*args, **kwargs)
            assert receipt.applied
            monkeypatch.setattr(adapter, "resolve_runtime", unavailable)
            return receipt

        monkeypatch.setattr(cm, "aupdate_imported_voice", committed)
        result = await api.get(f"/api/characters/voices/{ref}/overwrite_status", params={"context_token": token})
    else:
        transition = cm.transition_imported_voice_overwrite

        def committed(*args, **kwargs):
            receipt = transition(*args, **kwargs)
            assert receipt.applied
            monkeypatch.setattr(adapter, "resolve_runtime", unavailable)
            return receipt

        monkeypatch.setattr(cm, "transition_imported_voice_overwrite", committed)
        result = await api.post(f"/api/characters/voices/{ref}/recover_overwrite", json={
            "context_token": token, "operation_id": record["overwrite_operation_id"],
            "record_revision": record["_record_revision"],
        })
    assert result.status_code == 500
    monkeypatch.setattr(adapter, "resolve_runtime", resolve)
    saved = await asyncio.to_thread(cm.get_imported_voice, ref, include_inactive=True)
    assert saved["_record_revision"] == before.get("_record_revision", 0) + 1
    if operation == "recover":
        assert saved["overwrite_status"] == "failed"
        assert saved["overwrite_terminal_reason"] == "not_submitted_recovered"
        monkeypatch.setattr(cm, "transition_imported_voice_overwrite", transition)
        permission = await asyncio.to_thread(cm.transition_imported_voice_overwrite,
            ref, scope, action="submit", expected_operation_id=saved["overwrite_operation_id"],
            expected_record_revision=saved["_record_revision"])
        assert not permission.applied
    assert adapter.mutations == []
    print({"operation": operation, "fault": fault.__name__, "saved_revision": saved["_record_revision"],
           "response": result.json()})
    assert result.json()["details"].get("state_sync") == "saved"
    assert result.json()["details"].get("attempt_outcome") == "not_submitted"


@pytest.mark.asyncio
async def test_query_rejected_cas_does_not_claim_failed_save(client, monkeypatch, tmp_path):
    api, cm, adapter, ref, storage = await disk_fixture(client, monkeypatch, tmp_path)
    token = payload(adapter, cm)["context_token"]
    update = cm.aupdate_imported_voice
    resolve = adapter.resolve_runtime
    winner = None

    def unavailable(*args, **kwargs):
        raise OSError("controlled context read failure after rejected CAS")

    async def competing(*args, **kwargs):
        nonlocal winner
        winner = await update(args[0], args[1], {"remote_revision": "2", "display_name": "concurrent winner"})
        receipt = await update(*args, **kwargs)
        assert not receipt.applied
        assert receipt.record["display_name"] == "concurrent winner"
        monkeypatch.setattr(adapter, "resolve_runtime", unavailable)
        return receipt

    monkeypatch.setattr(cm, "aupdate_imported_voice", competing)
    result = await api.get(f"/api/characters/voices/{ref}/overwrite_status", params={"context_token": token})
    assert result.status_code == 500
    monkeypatch.setattr(adapter, "resolve_runtime", resolve)
    saved = await asyncio.to_thread(cm.get_imported_voice, ref, include_inactive=True)
    assert saved["display_name"] == winner["display_name"]
    assert saved["_record_revision"] == winner["_record_revision"]
    assert saved["remote_revision"] == "2"
    assert adapter.mutations == []
    print({"operation": "query-CAS-conflict", "response": result.json()})
    assert result.json()["details"]["state_sync"] == "unchanged"

@pytest.mark.asyncio
@pytest.mark.parametrize("write_fails", [False, True], ids=["late-commit", "late-failure"])
async def test_query_deadline_does_not_mean_its_local_write_stopped(client, monkeypatch, tmp_path, write_fails):
    api, cm, adapter = client
    ref = await _import(client)
    storage = tmp_path / "voice_storage.json"
    await asyncio.to_thread(atomic_write_json, storage, cm.storage)
    monkeypatch.setattr(cm, "load_voice_storage", lambda: json.loads(storage.read_text(encoding="utf-8")))
    monkeypatch.setattr(cm, "save_voice_storage", lambda value: atomic_write_json(storage, value))
    token = payload(adapter, cm)["context_token"]
    runtime = adapter.resolve_runtime(cm)
    original = await cm.aupdate_imported_voice(ref, runtime.scope_id, {
        "overwrite_operation_id": "controlled-existing-operation", "overwrite_status": "processing",
        "overwrite_submission_phase": "submission_possible", "overwrite_previous_revision": "1",
    })
    adapter.remote = replace(adapter.remote, metadata={**adapter.remote.metadata, "remote_revision": "2"})
    before = await asyncio.to_thread(storage.read_bytes)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    timeout = asyncio.timeout
    deadlines = []

    def capture_timeout(seconds):
        deadline = timeout(seconds)
        deadlines.append(deadline)
        return deadline

    monkeypatch.setattr(asyncio, "timeout", capture_timeout)
    save = cm.save_voice_storage

    def pending_save(value):
        entered.set()
        # Expire the real deadline after entering the write, before publishing.
        loop.call_soon_threadsafe(deadlines[-1].reschedule, loop.time())
        assert release.wait(5), "test must release the physical write"
        try:
            if write_fails:
                raise OSError("controlled late write failure")
            save(value)
        finally:
            finished.set()

    monkeypatch.setattr(cm, "save_voice_storage", pending_save)
    request = asyncio.create_task(api.get(f"/api/characters/voices/{ref}/overwrite_status",
        params={"context_token": token}))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        response = await asyncio.wait_for(request, 2)
        assert response.status_code == 504
        assert response.json()["code"] == "UPSTREAM_TIMEOUT"
        assert deadlines[-1].expired()
        assert not finished.is_set()
        assert await asyncio.to_thread(storage.read_bytes) == before
        details = response.json()["details"]
        assert details["state_sync"] == "unknown"
        assert details["voice_state"]["overwrite_status"] == "processing"
        assert details["voice_state"]["actions"] == ["refresh"]
        with pytest.raises(VoiceManagementError) as blocked:
            await service.overwrite_remote_voice(adapter, cm, ref,
                token=token, audio=b"second", filename="sample.wav")
        assert blocked.value.code == "UPDATE_OUTCOME_UNKNOWN"
        assert adapter.mutations == []
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
        after = await asyncio.to_thread(cm.get_imported_voice, ref, include_inactive=True)
        assert after["_record_revision"] == original["_record_revision"] + int(not write_fails)
        assert after["overwrite_status"] == ("processing" if write_fails else "completed")
        assert after["overwrite_operation_id"] == original["overwrite_operation_id"]
        assert after["local_ref"] == ref
        assert adapter.mutations == []
        print({"response": details, "write_finished_at_response": False,
            "later_revision": after["_record_revision"], "later_status": after["overwrite_status"],
            "mutation_count": len(adapter.mutations)})
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)
        await asyncio.to_thread(finished.wait, 5)


@pytest.mark.asyncio
async def test_route_fallback_after_service_entry_does_not_invent_no_submission(client, monkeypatch):
    api, cm, adapter = client
    ref = await _import(client)

    async def unavailable(*args, **kwargs):
        raise RuntimeError("controlled unclassified service failure")

    monkeypatch.setattr(service, "overwrite_remote_voice", unavailable)
    response = await api.post(f"/api/characters/voices/{ref}/overwrite",
        data={"context_token": payload(adapter, cm)["context_token"]},
        files={"audio": ("sample.wav", _wav(), "audio/wav")})
    assert response.status_code == 500
    assert response.json()["code"] == "LOCAL_OPERATION_FAILED"
    assert response.json()["details"]["attempt_outcome"] == "unknown"
    assert response.json()["details"]["state_sync"] == "unknown"
    assert adapter.mutations == []

@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["accepted-final-save", "unknown-cleanup-save"])
async def test_cancelled_waiter_never_reopens_pending_during_physical_write(client, monkeypatch, tmp_path, boundary):
    _, cm, adapter, ref, storage = await disk_fixture(client, monkeypatch, tmp_path)
    token = payload(adapter, cm)["context_token"]
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    save = cm.save_voice_storage
    terminal = "completed" if boundary == "accepted-final-save" else "unknown"

    def delayed_save(value):
        record = next(bucket[ref] for bucket in value.values() if ref in bucket)
        if record.get("overwrite_status") == terminal:
            entered.set()
            assert release.wait(5), "test must release storage worker"
            try:
                save(value)
            finally:
                finished.set()
        else:
            save(value)

    if boundary == "unknown-cleanup-save":
        async def response_lost():
            raise OSError("controlled lost upstream response")
        adapter.on_mutation = response_lost
    monkeypatch.setattr(cm, "save_voice_storage", delayed_save)
    old = asyncio.create_task(service.overwrite_remote_voice(adapter, cm, ref,
        token=token, audio=b"isolated-audio", filename="sample.wav"))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        old.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(old, 5)
        # The physical commit is blocked, but the previous persisted marker
        # still protects a new request after the cancelled task drops its lock.
        with pytest.raises(VoiceManagementError) as blocked:
            await asyncio.wait_for(service.overwrite_remote_voice(adapter, cm, ref,
                token=token, audio=b"second", filename="sample.wav"), 5)
        assert blocked.value.code == "UPDATE_OUTCOME_UNKNOWN"
        assert len(adapter.mutations) == 1
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
        # Join a real transaction to establish the storage lock was released.
        current = await asyncio.to_thread(cm.get_imported_voice, ref, include_inactive=True)
        receipt = await cm.aupdate_imported_voice(ref, current["scope_id"], {},
            expected_operation_id=current["overwrite_operation_id"],
            expected_record_revision=current["_record_revision"], return_receipt=True)
        assert receipt.applied
        assert receipt.record["overwrite_status"] == terminal
        assert receipt.record["overwrite_submission_phase"] == "submission_possible"
        assert len(adapter.mutations) == 1
        lock = service._OVERWRITE_LOCKS.get(ref)
        assert lock is None or not lock.locked()
        print({"boundary": boundary, "final_status": terminal, "mutations": len(adapter.mutations)})
    finally:
        release.set()
        if not old.done():
            old.cancel()
        await asyncio.gather(old, return_exceptions=True)
        await asyncio.to_thread(finished.wait, 5)


@pytest.mark.asyncio
async def test_cancelled_recovery_commit_still_fences_old_submitter(client, monkeypatch, tmp_path):
    _, cm, adapter, ref, storage = await disk_fixture(client, monkeypatch, tmp_path)
    runtime = adapter.resolve_runtime(cm)
    record = await cm.aupdate_imported_voice(ref, runtime.scope_id, {
        "overwrite_operation_id": "controlled-prepared", "overwrite_status": "processing",
        "overwrite_submission_phase": "prepared",
    })
    token = payload(adapter, cm)["context_token"]
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    save = cm.save_voice_storage

    def delayed_save(value):
        current = next(bucket[ref] for bucket in value.values() if ref in bucket)
        if current.get("overwrite_terminal_reason") == "not_submitted_recovered":
            entered.set()
            assert release.wait(5)
            try:
                save(value)
            finally:
                finished.set()
        else:
            save(value)

    monkeypatch.setattr(cm, "save_voice_storage", delayed_save)
    recovery = asyncio.create_task(recover_prepared_overwrite(adapter, cm, ref,
        token=token, operation_id=record["overwrite_operation_id"], record_revision=record["_record_revision"]))
    submitter = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        recovery.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(recovery, 5)
        submitter = asyncio.create_task(service.transition_with_context(adapter, cm, runtime, record, action="submit"))
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
        permission = await asyncio.wait_for(submitter, 5)
        assert not permission.applied
        saved = await asyncio.to_thread(cm.get_imported_voice, ref, include_inactive=True)
        assert saved["overwrite_status"] == "failed"
        assert saved["overwrite_terminal_reason"] == "not_submitted_recovered"
        assert saved["overwrite_operation_id"] == record["overwrite_operation_id"]
        assert adapter.mutations == []
        print({"boundary": "cancelled-recovery", "permission": permission.applied, "mutations": 0})
    finally:
        release.set()
        await asyncio.gather(recovery, *([submitter] if submitter else []), return_exceptions=True)
        await asyncio.to_thread(finished.wait, 5)
