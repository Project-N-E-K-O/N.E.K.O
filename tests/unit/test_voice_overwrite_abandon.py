"""Explicit unknown-result abandonment retains identity and fences stale evidence."""
from dataclasses import replace

import pytest

from tests.unit.test_voice_management_service import fixture, imported, payload
from utils.voice_management import service
from utils.voice_management.overwrite_recovery import abandon_unknown_overwrite
from utils.voice_management.types import VoiceManagementError


async def unknown(fixture, refresh=True):
    cm, adapter, ref = await imported(fixture)
    record = cm.update_imported_voice(ref, adapter.resolve_runtime(cm).scope_id, {
        "overwrite_status": "unknown", "overwrite_operation_id": "unknown-owner",
        "overwrite_submission_phase": "submission_possible", "overwrite_previous_revision": "1",
    })
    if refresh:
        result = await service.refresh_overwrite_status(adapter, cm, ref, token=payload(adapter, cm)["context_token"])
        assert "abandon" in result["details"]["voice_state"]["actions"]
        record = cm.get_imported_voice(ref)
    return cm, adapter, ref, cm.get_imported_voice(ref)


async def abandon(cm, adapter, ref, record, **kwargs):
    return await abandon_unknown_overwrite(
        adapter, cm, ref, token=payload(adapter, cm)["context_token"],
        operation_id=record["overwrite_operation_id"], record_revision=record["_record_revision"], **kwargs,
    )


@pytest.mark.asyncio
async def test_explicit_abandon_retains_terminal_owner_and_binding(fixture):
    cm, adapter, ref, record = await unknown(fixture)
    cm.characters = {"猫娘": {"Test": {"voice_id": ref}}}
    result = await abandon(cm, adapter, ref, record)
    assert result["abandoned"]
    assert result["details"]["attempt_outcome"] == "unknown"
    assert result["details"]["state_sync"] == "saved"
    assert "overwrite" in result["details"]["voice_state"]["actions"]
    saved = cm.get_imported_voice(ref)
    assert saved["overwrite_status"] == "failed"
    assert saved["overwrite_terminal_reason"] == "user_abandoned_unknown"
    assert saved["overwrite_operation_id"] == "unknown-owner"
    assert cm.characters["猫娘"]["Test"]["voice_id"] == ref
    assert adapter.mutations == []
    with pytest.raises(VoiceManagementError, match="VOICE_STATE_CHANGED"):
        await abandon(cm, adapter, ref, record)


@pytest.mark.asyncio
async def test_no_refresh_proof_cannot_abandon(fixture):
    cm, adapter, ref, record = await unknown(fixture, False)
    with pytest.raises(VoiceManagementError, match="VOICE_STATE_CHANGED"):
        await abandon(cm, adapter, ref, record)
    assert cm.get_imported_voice(ref) == record


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revision", "operation", "context", "account", "save"])
async def test_abandon_fences_changes_during_remote_recheck(fixture, monkeypatch, change):
    cm, adapter, ref, record = await unknown(fixture)
    def changed():
        if change in {"revision", "operation"}:
            cm.update_imported_voice(ref, record["scope_id"], {
                "overwrite_operation_id": "successor" if change == "operation" else "unknown-owner",
            })
        elif change == "context":
            cm.management_secret = "new-secret"
        elif change == "account":
            cm.key = "new-key"
        else:
            def failed_save(value):
                raise OSError("controlled failure")
            monkeypatch.setattr(cm, "save_voice_storage", failed_save)
    adapter.on_detail = changed
    with pytest.raises(VoiceManagementError) as error:
        await abandon(cm, adapter, ref, record)
    assert error.value.code == ("CONTEXT_CHANGED" if change in {"context", "account"} else
                                "STORAGE_ERROR" if change == "save" else "VOICE_STATE_CHANGED")
    assert cm.get_imported_voice(ref, include_inactive=True)["overwrite_status"] == "unknown"
    assert adapter.mutations == []


@pytest.mark.asyncio
@pytest.mark.parametrize("revision,status", [("2", "ready"), ("0", "ready"), (None, "ready"), ("1", "processing")])
async def test_abandon_rechecks_remote_revision_and_readiness(fixture, revision, status):
    cm, adapter, ref, record = await unknown(fixture)
    adapter.remote = replace(adapter.remote, metadata={"remote_revision": revision}, status=status)
    with pytest.raises(VoiceManagementError, match="VOICE_STATE_CHANGED"):
        await abandon(cm, adapter, ref, record)
    assert cm.get_imported_voice(ref) == record
    if revision == "2" and status == "ready":
        result = await service.refresh_overwrite_status(adapter, cm, ref, token=payload(adapter, cm)["context_token"])
        assert result["status"] == "completed"
        assert "abandon" not in result["details"]["voice_state"]["actions"]


@pytest.mark.asyncio
async def test_processing_never_gets_abandon_action(fixture):
    cm, adapter, ref, record = await unknown(fixture, False)
    cm.update_imported_voice(ref, record["scope_id"], {"overwrite_status": "processing"})
    result = await service.refresh_overwrite_status(adapter, cm, ref, token=payload(adapter, cm)["context_token"])
    assert "abandon" not in result["details"]["voice_state"]["actions"]


@pytest.mark.asyncio
async def test_abandon_recheck_failure_preserves_unknown(fixture):
    cm, adapter, ref, record = await unknown(fixture)
    adapter.detail_error = VoiceManagementError("AUTH_FAILED", 401)
    with pytest.raises(VoiceManagementError, match="AUTH_FAILED"):
        await abandon(cm, adapter, ref, record)
    assert cm.get_imported_voice(ref) == record
    assert adapter.mutations == []


@pytest.mark.asyncio
async def test_abandon_uses_durable_json_storage(fixture, tmp_path, monkeypatch):
    import json
    cm, adapter = fixture
    storage = tmp_path / "voices.json"
    storage.write_text("{}")
    monkeypatch.setattr(cm, "load_voice_storage", lambda: json.loads(storage.read_text()))
    monkeypatch.setattr(cm, "save_voice_storage", lambda value: storage.write_text(json.dumps(value)))
    cm, adapter, ref, record = await unknown(fixture)
    await abandon(cm, adapter, ref, record)
    assert cm.get_imported_voice(ref)["overwrite_terminal_reason"] == "user_abandoned_unknown"
    assert "user_abandoned_unknown" in storage.read_text()


@pytest.mark.asyncio
async def test_abandon_context_change_at_atomic_boundary_is_fenced(fixture, monkeypatch):
    cm, adapter, ref, record = await unknown(fixture)
    original = service.transition_with_context
    async def change(*args, **kwargs):
        cm.management_secret = "changed-at-commit"
        return await original(*args, **kwargs)
    monkeypatch.setattr(service, "transition_with_context", change)
    with pytest.raises(VoiceManagementError, match="CONTEXT_CHANGED"):
        await abandon(cm, adapter, ref, record)
    assert cm.get_imported_voice(ref)["overwrite_status"] == "unknown"
@pytest.mark.asyncio
async def test_old_result_write_cannot_revive_abandoned_owner(fixture):
    cm, adapter, ref, record = await unknown(fixture)
    await abandon(cm, adapter, ref, record)
    receipt = await cm.aupdate_imported_voice(
        ref, record["scope_id"], {"overwrite_status": "completed"},
        expected_operation_id=record["overwrite_operation_id"],
        expected_record_revision=record["_record_revision"], return_receipt=True,
    )
    assert not receipt.applied
    assert receipt.record["overwrite_terminal_reason"] == "user_abandoned_unknown"


@pytest.mark.asyncio
async def test_active_overwrite_lock_blocks_abandon(fixture):
    import asyncio
    cm, adapter, ref, record = await unknown(fixture)
    lock = service._OVERWRITE_LOCKS.setdefault(ref, asyncio.Lock())
    async with lock:
        with pytest.raises(VoiceManagementError, match="OPERATION_IN_PROGRESS"):
            await abandon(cm, adapter, ref, record)
    assert cm.get_imported_voice(ref) == record


@pytest.mark.asyncio
async def test_unreadable_feedback_does_not_invent_unlocked_actions(fixture, monkeypatch):
    cm, adapter, ref, record = await unknown(fixture)
    original = cm.get_imported_voice
    reads = 0
    def failed_projection(*args, **kwargs):
        nonlocal reads
        reads += 1
        if reads > 1:
            raise OSError("controlled projection error")
        return original(*args, **kwargs)
    monkeypatch.setattr(cm, "get_imported_voice", failed_projection)
    result = await abandon(cm, adapter, ref, record)
    assert result["details"]["voice_state"] is None
    assert result["details"]["state_sync"] == "saved"
    assert result["details"]["attempt_outcome"] == "unknown"
    assert original(ref)["overwrite_terminal_reason"] == "user_abandoned_unknown"
