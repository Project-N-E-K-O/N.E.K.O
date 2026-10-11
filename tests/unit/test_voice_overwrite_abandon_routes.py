"""Abandonment requires current query evidence and explicit HTTP identity."""
import pytest
from tests.unit.test_voice_management_routes import client, management_fixture, _import
from tests.unit.test_voice_management_service import payload
from utils.voice_management import service


async def unknown(client):
    session, cm, adapter = client
    ref = await _import(client)
    cm.update_imported_voice(ref, adapter.resolve_runtime(cm).scope_id, {
        "overwrite_status": "unknown", "overwrite_operation_id": "unknown-owner",
        "overwrite_submission_phase": "submission_possible", "overwrite_previous_revision": "1",
    })
    token = payload(adapter, cm)["context_token"]
    await service.refresh_overwrite_status(adapter, cm, ref, token=token)
    record = cm.get_imported_voice(ref)
    return ref, {"context_token": token, "operation_id": "unknown-owner", "record_revision": record["_record_revision"]}


@pytest.mark.asyncio
async def test_abandon_route_keeps_unknown_evidence_and_does_not_submit(client):
    session, cm, adapter = client
    ref, body = await unknown(client)
    response = await session.post(f"/api/characters/voices/{ref}/abandon_overwrite", json=body)
    assert response.status_code == 200
    assert response.json()["abandoned"]
    assert response.json()["details"]["attempt_outcome"] == "unknown"
    assert cm.get_imported_voice(ref)["overwrite_terminal_reason"] == "user_abandoned_unknown"
    assert adapter.mutations == []
    repeated = await session.post(f"/api/characters/voices/{ref}/abandon_overwrite", json=body)
    assert repeated.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [{"record_revision": True}, {"operation_id": ""}, {"unexpected": True}])
async def test_abandon_route_rejects_invalid_identity(client, patch):
    session, cm, adapter = client
    ref, body = await unknown(client)
    response = await session.post(f"/api/characters/voices/{ref}/abandon_overwrite", json={**body, **patch})
    assert response.status_code == 400
    assert cm.get_imported_voice(ref)["overwrite_status"] == "unknown"
    assert adapter.mutations == []
