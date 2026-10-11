"""Only a positive, durable submission receipt permits provider mutation."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading

import pytest

from tests.unit.test_voice_management_storage import manager, import_voice


def prepared(cm, **extra):
    ref, _, _ = import_voice(cm)
    record = cm.update_imported_voice(ref, cm.scope, {
        "overwrite_operation_id": "operation-a", "overwrite_status": "processing",
        "overwrite_submission_phase": "prepared", **extra,
    })
    return ref, record


def transition(cm, ref, record, action):
    return cm.transition_imported_voice_overwrite(
        ref, record["scope_id"], action=action,
        expected_operation_id=record["overwrite_operation_id"],
        expected_record_revision=record["_record_revision"],
    )


@pytest.mark.parametrize("action", ["submit", "recover"])
def test_transition_returns_positive_receipt_only_once(manager, action):
    ref, record = prepared(manager)
    result = transition(manager, ref, record, action)
    assert result.applied is True
    assert result.record["_record_revision"] == record["_record_revision"] + 1
    expected = "submission_possible" if action == "submit" else "prepared"
    assert result.record["overwrite_submission_phase"] == expected
    assert result.record["overwrite_status"] == ("processing" if action == "submit" else "failed")
    if action == "recover":
        assert result.record["overwrite_terminal_reason"] == "not_submitted_recovered"
    before = deepcopy(manager.storage)
    loser = transition(manager, ref, record, action)
    assert loser.applied is False
    assert loser.record == result.record
    assert manager.storage == before
    loser.record["overwrite_status"] = "tampered"
    assert manager.get_imported_voice(ref)["overwrite_status"] == result.record["overwrite_status"]


@pytest.mark.parametrize("phase", [None, "submission_possible", "unexpected"])
@pytest.mark.parametrize("action", ["submit", "recover"])
def test_unknown_or_possible_submission_never_acquires_receipt(manager, phase, action):
    ref, record = prepared(manager, overwrite_submission_phase=phase)
    before = deepcopy(manager.storage)
    result = transition(manager, ref, record, action)
    assert result.applied is False
    assert manager.storage == before


@pytest.mark.parametrize("status", ["failed", "completed"])
@pytest.mark.parametrize("action", ["submit", "recover"])
def test_terminal_operation_cannot_revive(manager, status, action):
    ref, record = prepared(manager, overwrite_status=status)
    assert transition(manager, ref, record, action).applied is False


def test_recovery_fences_old_task_but_preserves_voice_identity(manager):
    ref, record = prepared(manager)
    recovered = transition(manager, ref, record, "recover")
    assert recovered.applied
    assert transition(manager, ref, record, "submit").applied is False
    fresh_observation = manager.get_imported_voice(ref)
    assert transition(manager, ref, fresh_observation, "submit").applied is False
    for key in ("local_ref", "provider", "remote_voice_id", "scope_id", "origin", "created_at"):
        assert recovered.record[key] == record[key]
    assert recovered.record["overwrite_operation_id"] == record["overwrite_operation_id"]


def test_submission_winner_cannot_be_recovered_even_with_fresh_revision(manager):
    ref, record = prepared(manager)
    submitted = transition(manager, ref, record, "submit")
    assert submitted.applied
    assert transition(manager, ref, submitted.record, "recover").applied is False


def test_recovery_submission_race_has_exactly_one_winner(manager):
    ref, record = prepared(manager)
    barrier = threading.Barrier(2)

    def competing(action):
        barrier.wait(timeout=5)
        return action, transition(manager, ref, record, action)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(competing, ["submit", "recover"]))
    assert sum(result.applied for _, result in results) == 1
    winner = next((action, result) for action, result in results if result.applied)
    persisted = manager.get_imported_voice(ref)
    assert persisted["_record_revision"] == record["_record_revision"] + 1
    assert persisted["overwrite_status"] == ("processing" if winner[0] == "submit" else "failed")


@pytest.mark.parametrize("action", ["submit", "recover"])
def test_new_operation_cannot_be_modified_by_old_receipt(manager, action):
    ref, record = prepared(manager)
    manager.update_imported_voice(ref, manager.scope, {"overwrite_operation_id": "new-owner"})
    before = deepcopy(manager.storage)
    assert transition(manager, ref, record, action).applied is False
    assert manager.storage == before


def test_failed_save_never_returns_applied_or_allows_submission(manager, monkeypatch):
    ref, record = prepared(manager)
    before = deepcopy(manager.storage)

    def fail_save(value):
        raise OSError("controlled persistence failure")

    with monkeypatch.context() as failure:
        failure.setattr(manager, "save_voice_storage", fail_save)
        with pytest.raises(OSError):
            transition(manager, ref, record, "submit")
    assert manager.storage == before
    assert transition(manager, ref, record, "recover").applied


@pytest.mark.parametrize("invalid", [None, True, -1, "1"])
def test_invalid_revision_is_not_a_transition_receipt(manager, invalid):
    ref, record = prepared(manager)
    manager.storage["__REMOTE_VOICES__scope-a"][ref]["_record_revision"] = invalid
    with pytest.raises(ValueError, match="VOICE_STORAGE_INVALID"):
        transition(manager, ref, record, "submit")


def test_changed_scope_and_invalid_transition_do_not_write(manager):
    ref, record = prepared(manager)
    before = deepcopy(manager.storage)
    with pytest.raises(ValueError):
        manager.transition_imported_voice_overwrite(
            ref, "different-scope", action="recover", expected_operation_id="operation-a",
            expected_record_revision=record["_record_revision"],
        )
    with pytest.raises(ValueError):
        transition(manager, ref, record, "force_unlock")
    assert manager.storage == before


@pytest.mark.asyncio
async def test_async_transition_is_dual(manager):
    ref, record = prepared(manager)
    result = await manager.atransition_imported_voice_overwrite(
        ref, manager.scope, action="recover", expected_operation_id="operation-a",
        expected_record_revision=record["_record_revision"],
    )
    assert result.applied
