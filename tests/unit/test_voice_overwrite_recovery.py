"""Recovery revalidates context and never clears a possible remote submission."""

import asyncio
from copy import deepcopy

import pytest

from tests.unit.test_voice_management_service import fixture, imported, payload
from utils.voice_management.overwrite_recovery import recover_prepared_overwrite
from utils.voice_management.service import transition_with_context
from utils.voice_management.types import VoiceManagementError


async def prepared(fixture, phase="prepared"):
    cm, adapter, ref = await imported(fixture)
    record = cm.update_imported_voice(ref, adapter.resolve_runtime(cm).scope_id, {
        "overwrite_operation_id": "first", "overwrite_status": "processing",
        "overwrite_submission_phase": phase,
    })
    return cm, adapter, ref, record


async def recover(cm, adapter, ref, record, **kwargs):
    return await recover_prepared_overwrite(
        adapter, cm, ref, token=payload(adapter, cm)["context_token"],
        operation_id=record["overwrite_operation_id"], record_revision=record["_record_revision"], **kwargs,
    )


@pytest.mark.asyncio
async def test_recovery_retains_identity_binding_and_terminal_owner(fixture):
    cm, adapter, ref, record = await prepared(fixture)
    cm.characters = {"猫娘": {"Test": {"voice_id": ref}}}
    result = await recover(cm, adapter, ref, record)
    assert result["status"] == "failed"
    assert result["details"]["attempt_outcome"] == "not_submitted"
    assert result["details"]["state_sync"] == "saved"
    assert result["details"]["voice_state"]["operation_id"] == "first"
    assert cm.characters["猫娘"]["Test"]["voice_id"] == ref
    assert adapter.mutations == []
    saved = cm.get_imported_voice(ref)
    assert saved["overwrite_terminal_reason"] == "not_submitted_recovered"
    assert not cm.transition_imported_voice_overwrite(
        ref, record["scope_id"], action="submit", expected_operation_id="first",
        expected_record_revision=saved["_record_revision"],
    ).applied


@pytest.mark.asyncio
async def test_recovery_ownership_conflict_does_not_report_a_failed_save(fixture, monkeypatch):
    cm, adapter, ref, record = await prepared(fixture)
    before = deepcopy(cm.storage)

    def ownership_conflict(*args, **kwargs):
        raise ValueError("VOICE_CONTEXT_CHANGED")

    monkeypatch.setattr(cm, "transition_imported_voice_overwrite", ownership_conflict)
    with pytest.raises(VoiceManagementError) as error:
        await recover(cm, adapter, ref, record)
    assert error.value.code == "CONTEXT_CHANGED"
    assert error.value.details["state_sync"] == "unchanged"
    assert cm.storage == before


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [None, "submission_possible"])
async def test_legacy_unknown_and_possible_submission_stay_protected(fixture, phase):
    cm, adapter, ref, record = await prepared(fixture, phase)
    before = deepcopy(cm.storage)
    with pytest.raises(VoiceManagementError) as error:
        await recover(cm, adapter, ref, record)
    assert error.value.code == "VOICE_STATE_CHANGED"
    assert cm.storage == before
    assert adapter.mutations == []


@pytest.mark.asyncio
async def test_stale_recovery_cannot_clear_new_owner(fixture):
    cm, adapter, ref, record = await prepared(fixture)
    cm.update_imported_voice(ref, record["scope_id"], {"overwrite_operation_id": "new"})
    before = deepcopy(cm.storage)
    with pytest.raises(VoiceManagementError) as error:
        await recover(cm, adapter, ref, record)
    assert error.value.code == "VOICE_STATE_CHANGED"
    assert cm.storage == before


@pytest.mark.asyncio
async def test_context_change_at_atomic_boundary_prevents_transition(fixture, monkeypatch):
    from utils.voice_management import service

    cm, adapter, ref, record = await prepared(fixture)
    original = service.transition_with_context

    async def changed_context(adapter, cm, runtime, record, *, action):
        cm.management_secret = "changed"
        return await original(adapter, cm, runtime, record, action=action)

    token = payload(adapter, cm)["context_token"]
    monkeypatch.setattr(service, "transition_with_context", changed_context)
    before = deepcopy(cm.storage)
    with pytest.raises(VoiceManagementError) as error:
        await recover_prepared_overwrite(
            adapter, cm, ref, token=token, operation_id="first", record_revision=record["_record_revision"],
        )
    assert error.value.code == "CONTEXT_CHANGED"
    assert error.value.details["voice_state"] is None
    assert cm.storage == before


@pytest.mark.asyncio
async def test_recovery_save_failure_keeps_prepared_protection(fixture, monkeypatch):
    cm, adapter, ref, record = await prepared(fixture)

    def failure(value):
        raise OSError("controlled failure")

    monkeypatch.setattr(cm, "save_voice_storage", failure)
    with pytest.raises(VoiceManagementError) as error:
        await recover(cm, adapter, ref, record)
    assert error.value.code == "STORAGE_ERROR"
    assert error.value.details["state_sync"] == "failed"
    assert cm.get_imported_voice(ref)["overwrite_status"] == "processing"


@pytest.mark.asyncio
async def test_simultaneous_recovery_and_submit_have_one_receipt(fixture):
    cm, adapter, ref, record = await prepared(fixture)
    runtime = adapter.resolve_runtime(cm)
    results = await asyncio.gather(
        transition_with_context(adapter, cm, runtime, record, action="submit"),
        transition_with_context(adapter, cm, runtime, record, action="recover"),
    )
    assert sum(item.applied for item in results) == 1
    assert cm.get_imported_voice(ref)["_record_revision"] == record["_record_revision"] + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,actions", [
    ("prepared", ["refresh", "recover"]), (None, ["refresh"]),
    ("submission_possible", ["refresh"]),
])
async def test_recovery_advice_requires_persisted_prepared_phase(fixture, phase, actions):
    cm, adapter, ref, _ = await prepared(fixture, phase)
    details = await service_details(adapter, cm, ref)
    assert details["voice_state"]["actions"] == actions
    assert details["voice_state"]["submission_phase"] == phase


async def service_details(adapter, cm, ref):
    from utils.voice_management.service import overwrite_result_details

    return await overwrite_result_details(adapter, cm, ref, token=payload(adapter, cm)["context_token"])


@pytest.mark.asyncio
async def test_context_changed_after_recovery_reports_saved_without_old_actions(fixture, monkeypatch):
    cm, adapter, ref, record = await prepared(fixture)
    original = cm.transition_imported_voice_overwrite

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        cm.management_secret = "changed-after-transition"
        return result

    monkeypatch.setattr(cm, "transition_imported_voice_overwrite", changed)
    with pytest.raises(VoiceManagementError) as error:
        await recover(cm, adapter, ref, record)
    assert error.value.code == "CONTEXT_CHANGED"
    assert error.value.details["state_sync"] == "saved"
    assert error.value.details["voice_state"] is None
    assert cm.get_imported_voice(ref)["overwrite_terminal_reason"] == "not_submitted_recovered"


@pytest.mark.asyncio
@pytest.mark.parametrize("setting", ["key", "management_secret"])
async def test_context_change_during_recovery_state_projection_reports_saved_context_error(fixture, monkeypatch, setting):
    cm, adapter, ref, record = await prepared(fixture)
    token = payload(adapter, cm)["context_token"]
    original = cm.get_imported_voice
    reads = 0

    def changing_read(*args, **kwargs):
        nonlocal reads
        reads += 1
        # The first read validates the recovery request. The second is the
        # response projection, after the transition and its context check.
        if reads == 2:
            setattr(cm, setting, "changed-during-response-projection")
        return original(*args, **kwargs)

    monkeypatch.setattr(cm, "get_imported_voice", changing_read)
    with pytest.raises(VoiceManagementError) as error:
        await recover_prepared_overwrite(
            adapter, cm, ref, token=token, operation_id=record["overwrite_operation_id"],
            record_revision=record["_record_revision"],
        )
    assert error.value.code == "CONTEXT_CHANGED"
    assert error.value.details["state_sync"] == "saved"
    assert error.value.details["voice_state"] is None
    assert original(ref, include_inactive=True)["overwrite_terminal_reason"] == "not_submitted_recovered"
    assert adapter.mutations == []


@pytest.mark.asyncio
async def test_recovery_projection_read_failure_keeps_saved_receipt_without_inventing_context_change(fixture, monkeypatch):
    cm, adapter, ref, record = await prepared(fixture)
    token = payload(adapter, cm)["context_token"]
    original = cm.get_imported_voice
    reads = 0

    def failing_read(*args, **kwargs):
        nonlocal reads
        reads += 1
        if reads == 2:
            raise OSError("controlled projection read failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(cm, "get_imported_voice", failing_read)
    result = await recover_prepared_overwrite(
        adapter, cm, ref, token=token, operation_id=record["overwrite_operation_id"],
        record_revision=record["_record_revision"],
    )
    assert result["success"] is True and result["recovered"] is True
    assert result["details"]["state_sync"] == "saved"
    assert result["details"]["voice_state"] is None
    assert original(ref, include_inactive=True)["overwrite_terminal_reason"] == "not_submitted_recovered"
    assert adapter.mutations == []
