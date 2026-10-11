"""The recovery HTTP boundary requires the exact prepared owner and context."""

from copy import deepcopy

import pytest

from tests.unit.test_voice_management_routes import (
    client, management_fixture, _assert_private, _import,
)
from tests.unit.test_voice_management_service import payload


async def pending(client, phase="prepared"):
    _, cm, adapter = client
    ref = await _import(client)
    patch = {"overwrite_status": "processing", "overwrite_operation_id": "prepared-owner"}
    if phase is not None:
        patch["overwrite_submission_phase"] = phase
    record = cm.update_imported_voice(ref, adapter.resolve_runtime(cm).scope_id, patch)
    body = {"context_token": payload(adapter, cm)["context_token"],
            "operation_id": record["overwrite_operation_id"],
            "record_revision": record["_record_revision"]}
    return ref, record, body


@pytest.mark.asyncio
async def test_recovery_route_retains_reference_binding_and_terminal_evidence(client):
    session, cm, adapter = client
    ref, record, body = await pending(client)
    cm.characters = {"猫娘": {"Test": {"voice_id": ref}}}
    response = await session.post(f"/api/characters/voices/{ref}/recover_overwrite", json=body)
    assert response.status_code == 200
    data = response.json()
    assert data["voice_id"] == ref and data["status"] == "failed"
    assert data["details"]["attempt_outcome"] == "not_submitted"
    assert data["details"]["state_sync"] == "saved"
    assert data["details"]["voice_state"]["operation_id"] == body["operation_id"]
    saved = cm.get_imported_voice(ref)
    assert saved["overwrite_terminal_reason"] == "not_submitted_recovered"
    assert saved["_record_revision"] == record["_record_revision"] + 1
    assert cm.characters["猫娘"]["Test"]["voice_id"] == ref
    assert adapter.mutations == []
    _assert_private(response, cm)
    repeated = await session.post(f"/api/characters/voices/{ref}/recover_overwrite", json=body)
    assert repeated.status_code == 409 and repeated.json()["code"] == "VOICE_STATE_CHANGED"
    assert cm.get_imported_voice(ref) == saved


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [None, "submission_possible", "unrecognized"])
async def test_recovery_route_keeps_unknown_or_possible_submission_protected(client, phase):
    session, cm, adapter = client
    ref, _, body = await pending(client, phase)
    before = deepcopy(cm.storage)
    response = await session.post(f"/api/characters/voices/{ref}/recover_overwrite", json=body)
    assert response.status_code == 409 and response.json()["code"] == "VOICE_STATE_CHANGED"
    assert cm.storage == before and adapter.mutations == []
    _assert_private(response, cm)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["operation", "revision", "context", "account"])
async def test_recovery_route_revalidates_owner_revision_and_context(client, change):
    session, cm, adapter = client
    ref, record, body = await pending(client)
    if change == "operation":
        cm.update_imported_voice(ref, record["scope_id"], {"overwrite_operation_id": "replacement"})
    elif change == "revision":
        cm.update_imported_voice(ref, record["scope_id"], {"display_name": "New display name"})
    elif change == "context":
        cm.management_secret = "changed-management-secret"
    else:
        cm.key = "changed-account-key"
    before = deepcopy(cm.storage)
    response = await session.post(f"/api/characters/voices/{ref}/recover_overwrite", json=body)
    assert response.status_code == 409
    expected = "VOICE_STATE_CHANGED" if change in {"operation", "revision"} else "CONTEXT_CHANGED"
    assert response.json()["code"] == expected
    assert cm.storage == before and adapter.mutations == []
    if change in {"context", "account"}:
        assert response.json()["details"]["voice_state"] is None
    _assert_private(response, cm)


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [
    {"operation_id": ""}, {"operation_id": 4}, {"operation_id": "x" * 129},
    {"record_revision": True}, {"record_revision": -1}, {"record_revision": "1"},
    {"record_revision": None}, {"unexpected": True},
])
async def test_recovery_route_rejects_invalid_identity_without_writing(client, patch):
    session, cm, adapter = client
    ref, _, body = await pending(client)
    before = deepcopy(cm.storage)
    response = await session.post(f"/api/characters/voices/{ref}/recover_overwrite", json={**body, **patch})
    assert response.status_code == 400 and response.json()["code"] == "INVALID_METADATA"
    assert cm.storage == before and adapter.mutations == []
    _assert_private(response, cm)


@pytest.mark.asyncio
@pytest.mark.parametrize("body,code", [(b"{", "INVALID_JSON"), (b"[]", "INVALID_METADATA"), (b"{}", "INVALID_METADATA")])
async def test_recovery_route_rejects_invalid_json_shape(client, body, code):
    session, cm, adapter = client
    ref, _, _ = await pending(client)
    before = deepcopy(cm.storage)
    response = await session.post(f"/api/characters/voices/{ref}/recover_overwrite",
                                  content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 400 and response.json()["code"] == code
    assert cm.storage == before and adapter.mutations == []
    _assert_private(response, cm)


@pytest.mark.asyncio
async def test_recovery_route_storage_failure_never_reports_unlocked(client, monkeypatch):
    session, cm, adapter = client
    ref, _, body = await pending(client)
    before = deepcopy(cm.storage)

    def fail_save(storage):
        raise OSError("controlled storage failure")

    monkeypatch.setattr(cm, "save_voice_storage", fail_save)
    response = await session.post(f"/api/characters/voices/{ref}/recover_overwrite", json=body)
    assert response.status_code == 500 and response.json()["code"] == "STORAGE_ERROR"
    assert response.json()["details"]["state_sync"] == "failed"
    assert response.json()["details"]["voice_state"]["overwrite_status"] == "processing"
    assert cm.storage == before and adapter.mutations == []
    _assert_private(response, cm)


@pytest.mark.asyncio
async def test_recovery_route_missing_record_is_not_recreated(client):
    session, cm, adapter = client
    body = {"context_token": payload(adapter, cm)["context_token"], "operation_id": "missing", "record_revision": 0}
    response = await session.post("/api/characters/voices/voice_" + "f" * 32 + "/recover_overwrite", json=body)
    assert response.status_code == 404 and response.json()["code"] == "VOICE_NOT_FOUND"
    assert cm.storage == {} and adapter.mutations == []
    _assert_private(response, cm)
