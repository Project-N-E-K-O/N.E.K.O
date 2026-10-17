"""Local HTTP controls use synthetic roots, never a desktop login or user chat."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from main_routers import chat_sync_router as routes


UID = "a" * 32
OWNER = "00000000-0000-4000-8000-000000000001"


class SyntheticRuntime:
    """Expose route calls without starting services, HTTP clients or databases."""

    def __init__(self):
        """Keep exact IDs observable for binding and deletion assertions."""
        self.calls = []
        self.flush_calls = []
        self.originals = [{"messageId": "b" * 32, "text": "synthetic"}]
        self.local_reply_records = []

    async def status(self):
        """Return a bound role and an unrelated stale role to check local projection."""
        return {"serviceUrl": "https://sync.test", "deviceId": "c" * 32, "bindings": [{
            "character_uid": UID, "owner_id": OWNER, "binding_id": "d" * 32, "enabled": 0, "generation": 3,
            "pendingOriginals": 0, "pendingDeletions": 0, "cursor": 0, "hasSealedBatch": False,
            "localOnlyOriginals": 0,
        }]}

    async def configure(self, url):
        """Record only explicit service configuration supplied to the endpoint."""
        self.calls.append(("configure", url))

    async def request_catchup(self):
        """Represent a local-only wake across all opted-in roles."""
        self.calls.append(("wake",))

    async def enable(self, uid, name):
        """Return a synthetic service-authoritative owner for the real local UID."""
        self.calls.append(("enable", uid, name))
        return {"owner_id": OWNER}

    async def pause(self, uid):
        """Record a local pause without discarding history or revoking another device."""
        self.calls.append(("pause", uid))

    async def sync(self, uid, **limits):
        """Require callers to supply the bounded pass contract explicitly."""
        self.calls.append(("sync", uid, limits))
        return {"uploaded": 0, "cursor": 0, "hasMore": False}

    async def history(self, uid):
        """Return known records without invoking the memory server."""
        self.calls.append(("history", uid))
        return list(self.originals)

    async def local_replies(self, uid):
        """Return local-only raw replies separately from canonical sync originals."""
        self.calls.append(("local_replies", uid))
        return list(self.local_reply_records)

    async def delete(self, uid, message_ids):
        """Freeze exact known IDs in the same shape as the real deletion outbox."""
        self.calls.append(("delete", uid, list(message_ids)))
        return {"deletionId": "e" * 32, "messageIds": list(message_ids), "pending": True}

    async def clear(self, uid):
        """Represent the runtime's combined source/download boundary, not a UI scan."""
        self.calls.append(("clear", uid))
        return {"deletionIds": ["e" * 32], "messageIds": ["b" * 32],
                "localMessageIds": ["f" * 32], "pending": True}

    def start_deletion_flush(self, uid):
        """Represent detached sending without a real background task in route tests."""
        assert uid == UID
        self.flush_calls.append(uid)


@pytest.fixture
def local_client(monkeypatch):
    """Override only runtime and character loading inside the temporary FastAPI app."""
    runtime = SyntheticRuntime()

    async def characters():
        """Represent a saved UID and a display name from a disposable config manager."""
        return {"猫娘": {"Neko": {"_reserved": {"character_uid": UID}}}}

    monkeypatch.setattr(routes, "get_config_manager", lambda: SimpleNamespace(aload_characters=characters))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes._runtime] = lambda: runtime
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        yield client, runtime


def test_status_exposes_saved_uid_and_local_pause(local_client):
    """A saved role keeps its UID and owner even when this device is paused."""
    client, _ = local_client
    response = client.get("/api/chat-sync/status")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["characters"] == [{
        "characterSyncId": UID, "displayName": "Neko", "syncEnabled": False, "syncPending": False,
        "binding": {"ownerId": OWNER, "bindingId": "d" * 32, "enabled": False, "generation": 3,
                    "pendingOriginals": 0, "pendingDeletions": 0, "cursor": 0, "hasSealedBatch": False,
                    "localOnlyOriginals": 0,
                    "lastSyncError": None, "writerRecoveryRequired": False, "profile": None, "historyBackfill": None},
    }]


@pytest.mark.parametrize('choices,expected', [({}, False), ({UID: False}, False), ({UID: True}, True)])
def test_status_defaults_an_unconfigured_role_to_off(local_client, choices, expected):
    """New and existing roles without a saved choice stay Off until the user opts in."""
    from unittest.mock import AsyncMock
    client, runtime = local_client
    runtime.status = AsyncMock(return_value={'bindings': [], 'syncChoices': choices, 'syncPendingChoices': {}})
    role = client.get('/api/chat-sync/status').json()['characters'][0]
    assert role['syncEnabled'] is expected and role['syncPending'] is False
    assert role['binding'] is None


def test_status_preserves_a_paused_roles_durable_pending_on_choice(local_client):
    """Reopening an editor cannot turn a pending explicit resume into a new Off save."""
    from unittest.mock import AsyncMock
    client, runtime = local_client
    state = client.get('/api/chat-sync/status').json()
    runtime.status = AsyncMock(return_value={
        'bindings': [{'character_uid': UID, 'owner_id': OWNER, 'binding_id': 'd' * 32, 'enabled': 0, 'generation': 3,
                      'pendingOriginals': 0, 'pendingDeletions': 0, 'cursor': 0, 'hasSealedBatch': False,
                      'localOnlyOriginals': 0}],
        'syncChoices': {UID: True}, 'syncPendingChoices': {UID: True},
        'serviceUrl': state['serviceUrl'], 'deviceId': state['deviceId'],
    })
    role = client.get('/api/chat-sync/status').json()['characters'][0]
    assert role['syncEnabled'] and role['syncPending']
    assert role['binding']['enabled'] is False


@pytest.mark.parametrize('error,status', [(None, 200), ('chat_sync_login_required', 401), ('chat_sync_unavailable', 503)])
def test_manual_combined_choice_run_is_local_and_exposes_real_errors(local_client, error, status):
    """The separate run route consumes existing consent and never accepts a caller-supplied owner."""
    from unittest.mock import AsyncMock
    client, runtime = local_client
    runtime.run_character_sync_choice = AsyncMock(return_value={'completed': True, 'pending': False},
                                                 side_effect=ValueError(error) if error else None)
    path = '/api/chat-sync/characters/' + UID + '/sync-choice/run'
    response = client.post(path)
    assert response.status_code == status
    if error:
        assert response.json() == {'detail': error}
    runtime.run_character_sync_choice.assert_awaited_once_with(UID)
    assert client.post(path, headers={'Origin': 'https://foreign.test'}).status_code == 403
    assert client.post('/api/chat-sync/characters/' + 'b' * 32 + '/sync-choice/run').status_code == 404
    assert runtime.run_character_sync_choice.await_count == 1


def test_history_keeps_local_only_replies_outside_canonical_originals(local_client):
    """Passive display can restore local replies without making them sync originals."""
    client, runtime = local_client
    local = {"messageId": "e" * 32, "characterSyncId": UID, "role": "assistant",
             "text": "  synthetic local reply\r\n🙂  ", "originalTime": "1791540000123"}
    runtime.local_reply_records = [local]
    response = client.get(f"/api/chat-sync/characters/{UID}/history")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {"characterSyncId": UID, "originals": runtime.originals, "localReplies": [local]}
    assert runtime.calls == [("history", UID), ("local_replies", UID)]
    runtime.calls.clear()
    assert client.get(f"/api/chat-sync/characters/{'f' * 32}/history").status_code == 404
    assert runtime.calls == []


def test_foreground_wake_is_local_only_and_retains_origin_gate(local_client):
    """Foreground cannot pass a foreign origin or expose a bearer in the response."""
    client, runtime = local_client
    result = client.post("/api/chat-sync/wake")
    assert result.status_code == 200 and result.json() == {"scheduled": True}
    assert result.headers["Cache-Control"] == "no-store"
    assert runtime.calls == [("wake",)]
    assert client.post("/api/chat-sync/wake", headers={"Origin": "https://foreign.test"}).status_code == 403
    assert client.post("/api/chat-sync/wake", json={"ownerId": OWNER}).status_code == 422
    assert runtime.calls == [("wake",)]


def test_foreground_wake_io_failure_exposes_only_fixed_error(local_client):
    """Optional local witness/storage faults must not expose a private path."""
    from unittest.mock import AsyncMock
    client, runtime = local_client
    runtime.request_catchup = AsyncMock(side_effect=OSError("private synthetic path"))
    result = client.post("/api/chat-sync/wake")
    assert result.status_code == 503
    assert result.json() == {"detail": "chat_sync_unavailable"}


def test_profile_consent_accepts_only_reviewed_local_hash_and_revision(local_client):
    """The local UI cannot inject raw settings, account IDs or credentials."""
    from unittest.mock import AsyncMock
    client, runtime = local_client
    runtime.profiles = SimpleNamespace(approve=AsyncMock(return_value={"pending": True}))
    base = f"/api/chat-sync/characters/{UID}/profile/approve"
    for extra in ({"ownerId": OWNER}, {"profile": {}}, {"accessToken": "synthetic"}):
        assert client.post(base, json={"localHash": "e" * 64, "revision": 2, **extra}).status_code == 422
    assert client.post(base, json={"localHash": "e" * 64, "revision": True}).status_code == 422
    result = client.post(base, json={"localHash": "e" * 64, "revision": 2})
    assert result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
    runtime.profiles.approve.assert_awaited_once_with(UID, "e" * 64, 2)


def test_remote_choice_keeps_rename_confirmation_and_backpressure_error(local_client):
    """An unresolved/backpressured choice cannot return a successful UI receipt."""
    from unittest.mock import AsyncMock
    client, runtime = local_client
    runtime.profiles = SimpleNamespace(step=AsyncMock(side_effect=ValueError("chat_sync_retry_later")))
    result = client.post(f"/api/chat-sync/characters/{UID}/profile/resolve", json={
        "localHash": "e" * 64, "revision": 2, "choice": "remote", "confirmRename": True})
    assert result.status_code == 503 and result.json()["detail"] == "chat_sync_retry_later"
    runtime.profiles.step.assert_awaited_once_with(UID, manual=True, choose_remote=True,
                                                 local_hash="e" * 64, revision=2, confirm_rename=True)


def test_cloud_discovery_preview_and_explicit_import_do_not_require_existing_local_uid(local_client):
    """The new-character path is explicit, separate from existing-role controls."""
    from unittest.mock import AsyncMock
    client, runtime = local_client
    cloud_uid = "f" * 32
    runtime.profiles = SimpleNamespace(cloud_roles=AsyncMock(return_value={"characters": []}),
                                      preview=AsyncMock(return_value={"profile": None}),
                                      import_role=AsyncMock(return_value={"existing": False}))
    assert client.get("/api/chat-sync/cloud/characters").status_code == 200
    assert client.get(f"/api/chat-sync/cloud/characters/{cloud_uid}/profile").status_code == 200
    runtime.profiles.import_role.assert_not_awaited()
    result = client.post(f"/api/chat-sync/cloud/characters/{cloud_uid}/import", json={"revision": 1, "profileHash": "d" * 64})
    assert result.status_code == 200
    runtime.profiles.import_role.assert_awaited_once_with(cloud_uid, 1, "d" * 64)
    assert client.post(f"/api/chat-sync/cloud/characters/{cloud_uid}/import", json={
        "revision": 1, "profileHash": "d" * 64, "profile": {}}).status_code == 422


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.test"}, {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"},
])
def test_cross_origin_local_requests_are_rejected(local_client, headers):
    """Loopback transport alone does not authorize a browser to expose originals."""
    client, runtime = local_client
    response = client.get("/api/chat-sync/status", headers=headers)
    assert response.status_code == 403
    assert response.headers["Cache-Control"] == "no-store"
    assert runtime.calls == []


@pytest.mark.parametrize("suffix,method", [("enable", "post"), ("pause", "post"), ("history", "get"), ("clear", "post")])
def test_unknown_local_character_cannot_address_bound_cloud_role(local_client, suffix, method):
    """A path UID must belong to a current saved desktop profile."""
    client, runtime = local_client
    response = getattr(client, method)(f"/api/chat-sync/characters/{'f' * 32}/{suffix}")
    assert response.status_code == 404
    assert runtime.calls == []


def test_enable_uses_config_uid_and_name_not_body_owner(local_client):
    """The frontend cannot replace the role identity or service-authoritative owner."""
    client, runtime = local_client
    response = client.post(f"/api/chat-sync/characters/{UID}/enable")
    assert response.status_code == 200
    assert response.json()["ownerId"] == OWNER
    assert runtime.calls == [("enable", UID, "Neko")]


def test_manual_sync_requires_explicit_limits_and_rejects_extra_owner(local_client):
    """Strict request fields avoid an implicit timer or client-provided authority."""
    client, runtime = local_client
    path = f"/api/chat-sync/characters/{UID}/sync"
    assert client.post(path, json={"byteBudget": 10000, "maxPullPages": 1, "ownerId": OWNER}).status_code == 422
    assert client.post(path, json={"byteBudget": True, "maxPullPages": 1}).status_code == 422
    assert runtime.calls == []
    assert client.post(path, json={"byteBudget": 10000, "maxPullPages": 1}).status_code == 200
    assert runtime.calls == [("sync", UID, {"byte_budget": 10000, "max_pull_pages": 1})]


def test_clear_freezes_only_currently_known_original_ids(local_client):
    """Clearing snapshots stable IDs and cannot target future remote additions by time."""
    client, runtime = local_client
    response = client.post(f"/api/chat-sync/characters/{UID}/clear")
    assert response.status_code == 200
    assert response.json()["messageIds"] == ["b" * 32]
    assert response.json()["localMessageIds"] == ["f" * 32]
    assert runtime.calls == [("clear", UID)]
    assert runtime.flush_calls == [UID]


def test_delete_uses_exact_id_list_without_touching_memory_edit_api(local_client):
    """The reviewed message IDs are passed directly to the durable deletion outbox."""
    client, runtime = local_client
    response = client.post(f"/api/chat-sync/characters/{UID}/deletions", json={"messageIds": ["f" * 32]})
    assert response.status_code == 200
    assert runtime.calls == [("delete", UID, ["f" * 32])]


def test_history_consent_pins_task_without_accepting_ui_owner_or_snapshot(local_client):
    """Only the backend's fixed preview identity can authorize historical members."""
    from unittest.mock import AsyncMock
    client, runtime = local_client
    runtime.preview_history = AsyncMock(return_value={"taskId": "f" * 32, "count": 12})
    runtime.approve_history = AsyncMock(return_value={"status": "approved"})
    runtime.local_replies = AsyncMock(return_value=[{"messageId": "e" * 32}])
    path = f"/api/chat-sync/characters/{UID}/history"
    result = client.post(path + "/preview")
    assert result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
    runtime.preview_history.assert_awaited_once_with(UID, "Neko")
    for extra in ({"ownerId": OWNER}, {"count": 0}, {"messageIds": []}, {"localName": "wrong"}):
        assert client.post(path + "/approve", json={"taskId": "f" * 32, **extra}).status_code == 422
    assert client.post(path + "/approve", json={"taskId": "f" * 32}).status_code == 200
    runtime.approve_history.assert_awaited_once_with(UID, "f" * 32)
    runtime.local_replies.assert_not_awaited()
    assert client.post(f"/api/chat-sync/characters/{'f' * 32}/history/preview").status_code == 404


def test_writer_recovery_routes_keep_history_and_deletion_consent_separate(local_client):
    """Cloud checks only preview; exact reviewed task IDs authorize deletions later."""
    from unittest.mock import AsyncMock
    client, runtime = local_client
    runtime.recover_writer_role = AsyncMock(return_value={
        "history": {"taskId": "f" * 32, "count": 2},
        "deletions": {"taskId": "e" * 32, "count": 1},
    })
    runtime.approve_recovery_deletions = AsyncMock(return_value={"status": "registered"})
    runtime.approve_history = AsyncMock()
    path = f"/api/chat-sync/characters/{UID}/recovery"
    result = client.post(path + "/preview", json={"serviceUrl": "https://sync.nekotech.cn"})
    assert result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
    runtime.recover_writer_role.assert_awaited_once_with(UID, "Neko", service_url="https://sync.nekotech.cn")
    runtime.approve_recovery_deletions.assert_not_awaited()
    runtime.approve_history.assert_not_awaited()
    for extra in ({"ownerId": OWNER}, {"messageIds": ["b" * 32]}, {"count": 1}):
        assert client.post(path + "/deletions/approve", json={"taskId": "e" * 32, **extra}).status_code == 422
    assert client.post(path + "/deletions/approve", json={"taskId": "e" * 32}).status_code == 200
    runtime.approve_recovery_deletions.assert_awaited_once_with(UID, "e" * 32)
    runtime.approve_history.assert_not_awaited()
    assert client.post(f"/api/chat-sync/characters/{'f' * 32}/recovery/preview",
                       json={"serviceUrl": "https://sync.nekotech.cn"}).status_code == 404


@pytest.mark.parametrize('enabled', [True, False])
def test_card_save_choice_is_local_authorized_and_rejects_extra_fields(local_client, enabled):
    from unittest.mock import AsyncMock
    client, runtime = local_client
    runtime.save_character_sync_choice = AsyncMock(return_value={'pending': enabled, 'enabled': enabled})
    path = '/api/chat-sync/characters/' + UID + '/sync-choice'
    response = client.post(path, json={'enabled': enabled})
    assert response.status_code == 200
    runtime.save_character_sync_choice.assert_awaited_once_with(UID, 'Neko', enabled, include_history=False)
    assert client.post(path, json={'enabled': enabled, 'ownerId': OWNER}).status_code == 422
    assert client.post(path, json={'enabled': enabled}, headers={'Origin': 'https://foreign.test'}).status_code == 403
    assert client.post('/api/chat-sync/characters/' + 'b' * 32 + '/sync-choice', json={'enabled': enabled}).status_code == 404
    assert runtime.save_character_sync_choice.await_count == 1


@pytest.mark.parametrize("origin", ["https://untrusted.invalid", "https://sync.nekotech.cn.untrusted.invalid", "http://sync.nekotech.cn"])
def test_browser_configuration_rejects_arbitrary_oauth_destinations(local_client, origin):
    client, runtime = local_client
    assert client.put('/api/chat-sync/configuration', json={'serviceUrl': origin}).status_code == 422
    assert runtime.calls == []



def test_status_and_routes_expose_no_account_character_policy(local_client):
    """Synchronization status and routes never depend on an account policy endpoint."""
    client, runtime = local_client
    response = client.get('/api/chat-sync/status')
    assert response.status_code == 200
    assert 'characterPolicy' not in response.json()
    binding = response.json()['characters'][0]['binding']
    assert 'cloudPolicyError' not in binding and 'retired' not in binding
    assert client.get('/api/chat-sync/cloud/character-policy').status_code == 404
    assert client.post('/api/chat-sync/cloud/character-policy', json={}).status_code == 404
    assert client.post('/api/chat-sync/cloud/characters/' + UID + '/delete', json={}).status_code == 404
    assert runtime.calls == []
