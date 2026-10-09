import asyncio
import io
import json
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from main_routers.characters_router import chat_avatar as route
from main_routers.system_router import _shared as security
from utils import chat_avatar_store as store
from utils.character_memory import character_config_mutation_lock
from utils.chat_avatar_connections import (
    notify_chat_avatar_changed, register_chat_avatar_connection, unregister_chat_avatar_connection,
)
from utils.config_manager import ConfigManager, assign_new_character_uid, get_character_uid

UID = "a" * 32
OTHER_UID = "b" * 32
URL = f"/api/characters/by-uid/{UID}/chat-avatar"


def png():
    output = io.BytesIO()
    Image.new("RGBA", (320, 320), (12, 24, 48, 72)).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def backend(tmp_path, monkeypatch):
    with patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_path), patch.object(
        ConfigManager, "_get_standard_data_directory_candidates", return_value=[tmp_path],
    ), patch.object(ConfigManager, "get_legacy_app_root_candidates", return_value=[]), patch.object(
        ConfigManager, "_get_project_root", return_value=tmp_path,
    ):
        manager = ConfigManager("AvatarTests")
    manager._get_standard_data_directory_candidates = lambda: [tmp_path]
    manager.get_legacy_app_root_candidates = lambda: []
    manager.save_characters({"当前猫娘": "A", "猫娘": {
        "A": {"昵称": "A", "_reserved": {"character_uid": UID}},
        "B": {"昵称": "B", "_reserved": {"character_uid": OTHER_UID}},
    }})
    monkeypatch.setattr(route, "get_config_manager", lambda: manager)
    monkeypatch.setattr(security, "AUTOSTART_CSRF_TOKEN", "avatar-test-csrf")
    app = FastAPI()
    app.include_router(route.router)
    headers = {"Origin": "http://testserver", "X-Neko-Autostart-CSRF": "avatar-test-csrf"}
    # Use the shared helper's real configured header, rather than assuming its spelling.
    headers[security._AUTOSTART_CSRF_HEADER] = "avatar-test-csrf"
    return TestClient(app), manager, headers, app


def save(client, headers, revision="0", operation="save", image=None):
    return client.put(URL, headers=headers, data={"base_revision": revision, "operation_id": operation},
                      files={"image": ("untrusted-name.png", png() if image is None else image, "image/png")})


def test_get_save_delete_and_reload(backend):
    client, manager, headers, _ = backend
    initial = client.get(URL)
    assert initial.status_code == 200 and initial.json()["data_url"] is None
    assert initial.json()["limits"]["normalized_size"] == 320
    saved = save(client, headers)
    assert saved.status_code == 200, saved.text
    record = saved.json()
    assert client.get(URL).json() == record
    cleared = client.request("DELETE", URL, headers=headers, json={"base_revision": record["revision"], "operation_id": "restore"})
    assert cleared.status_code == 200 and cleared.json()["data_url"] is None
    assert cleared.json()["revision"] != record["revision"]
    assert save(client, headers, record["revision"], "old-window").status_code == 409
    assert (manager.app_docs_dir / "chat_avatars" / f"{UID}.json").is_file()


def test_write_requires_real_csrf_and_origin(backend):
    client, _, headers, _ = backend
    for invalid in ({}, {"Origin": "http://testserver"}, {**headers, "Origin": "https://evil.example"}):
        response = save(client, invalid)
        assert response.status_code == 403
    assert client.get(URL).json()["revision"] == "0"


def test_invalid_uid_missing_role_and_unset_are_distinct(backend):
    client, _, headers, _ = backend
    assert client.get("/api/characters/by-uid/not-a-uid/chat-avatar").status_code == 400
    missing = client.get(f"/api/characters/by-uid/{'f' * 32}/chat-avatar")
    assert missing.status_code == 404 and missing.json()["code"] == "chat_avatar_character_not_found"
    assert client.get(URL).status_code == 200


@pytest.mark.parametrize("image,status", [(b"fake png header", 422), (b"x" * (store.NORMALIZED_MAX_BYTES + 1), 413)], ids=["invalid-image", "large-image"])
def test_upload_validation(backend, image, status):
    client, _, headers, _ = backend
    assert save(client, headers, image=image).status_code == status
    assert client.get(URL).json()["data_url"] is None


def test_chunked_body_limit_before_multipart_decode(backend):
    client, _, headers, _ = backend
    response = client.put(URL, headers={**headers, "Content-Type": "multipart/form-data; boundary=test"},
                          content=iter([b"x" * 100_000] * 12))
    assert response.status_code == 413


@pytest.mark.parametrize("payload", [{}, [], {"base_revision": "0", "operation_id": "x", "extra": True}, {"base_revision": "0", "operation_id": "../x"}])
def test_invalid_delete_fields(backend, payload):
    client, _, headers, _ = backend
    assert client.request("DELETE", URL, headers=headers, json=payload).status_code == 400


def test_deeply_nested_delete_body_has_stable_invalid_request_error(backend):
    client, _, headers, _ = backend
    response = client.request("DELETE", URL, headers=headers, content="[" * 1500 + "0" + "]" * 1500)
    assert response.status_code == 400
    assert response.json()["code"] == "chat_avatar_invalid_request"
    assert client.get(URL).json()["revision"] == "0"


def test_same_operation_retry_and_conflict(backend):
    client, _, headers, _ = backend
    first = save(client, headers)
    assert save(client, headers).json() == first.json()
    restored = client.request("DELETE", URL, headers=headers, json={"base_revision": first.json()["revision"], "operation_id": "restore"})
    assert restored.status_code == 200
    assert save(client, headers).status_code == 409


def test_corruption_not_overwritten(backend):
    client, manager, headers, _ = backend
    directory = manager.app_docs_dir / "chat_avatars"
    directory.mkdir()
    path = directory / f"{UID}.json"
    path.write_text("broken")
    assert client.get(URL).json()["code"] == "chat_avatar_record_corrupt"
    assert save(client, headers).status_code == 500
    assert path.read_text() == "broken"


def test_maintenance_fence_uses_existing_error(backend):
    client, manager, headers, _ = backend
    state = manager.load_root_state()
    state["mode"] = "maintenance_readonly"
    manager.save_root_state(state)
    response = save(client, headers)
    assert response.status_code == 409 and response.json()["code"] == "CLOUDSAVE_WRITE_FENCE_ACTIVE"
    assert not (manager.app_docs_dir / "chat_avatars" / f"{UID}.json").exists()


def test_rename_retains_uid_recreated_name_does_not_inherit(backend):
    client, manager, headers, _ = backend
    first = save(client, headers).json()
    characters = manager.load_characters()
    characters["猫娘"]["Renamed"] = characters["猫娘"].pop("A")
    characters["当前猫娘"] = "Renamed"
    manager.save_characters(characters)
    assert client.get(URL).json() == first
    characters["猫娘"].pop("Renamed")
    characters["猫娘"]["A"] = {"昵称": "A"}
    assign_new_character_uid(characters["猫娘"]["A"])
    characters["当前猫娘"] = "A"
    manager.save_characters(characters)
    assert client.get(URL).status_code == 404
    new_uid = manager.load_characters()["猫娘"]["A"]["_reserved"]["character_uid"]
    assert new_uid != UID
    assert client.get(f"/api/characters/by-uid/{new_uid}/chat-avatar").json()["data_url"] is None


@pytest.mark.asyncio
async def test_cancelled_commit_holds_character_lock_until_worker_finishes(backend, monkeypatch):
    _, manager, _, _ = backend
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    real_commit = route._commit

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        result = real_commit(*args)
        finished.set()
        return result

    monkeypatch.setattr(route, "_commit", blocked)
    task = asyncio.create_task(route._save(manager, UID, Path(manager.app_docs_dir), None, "0", "cancelled"))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert character_config_mutation_lock.locked() and not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set() and not character_config_mutation_lock.locked()
    assert store.read_record(manager.app_docs_dir / "chat_avatars", UID)["last_operation_id"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("base_revision,committed", [("0", True), ("stale", False)], ids=["committed", "rejected"])
async def test_cancelled_request_still_announces_a_commit_that_landed(backend, monkeypatch, base_revision, committed):
    _, manager, _, _ = backend
    entered, release = threading.Event(), threading.Event()
    real_commit = route._commit
    notify = AsyncMock()

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return real_commit(*args)

    monkeypatch.setattr(route, "_commit", blocked)
    monkeypatch.setattr(route, "notify_chat_avatar_changed", notify)
    task = asyncio.create_task(route._save(manager, UID, Path(manager.app_docs_dir), None, base_revision, "cancelled"))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    record = store.read_record(manager.app_docs_dir / "chat_avatars", UID)
    if committed:
        notify.assert_awaited_once_with(UID, record["revision"])
    else:
        assert record["revision"] == "0"
        notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_overlap_same_revision_exactly_one_wins(backend):
    _, _, headers, app = backend
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        async def save_one(operation):
            return await client.put(URL, headers=headers, data={"base_revision": "0", "operation_id": operation},
                                    files={"image": ("x.png", png(), "image/png")})
        responses = await asyncio.gather(save_one("one"), save_one("two"))
    assert sorted(response.status_code for response in responses) == [200, 409]


@pytest.mark.asyncio
async def test_role_deleted_during_decode_rejected_before_commit(backend, monkeypatch):
    client, manager, headers, _ = backend
    real_normalize = route.normalize_png

    def remove_role(payload):
        characters = manager.load_characters()
        characters["猫娘"].pop("A")
        characters["当前猫娘"] = "B"
        manager.save_characters(characters)
        return real_normalize(payload)

    monkeypatch.setattr(route, "normalize_png", remove_role)
    response = await asyncio.to_thread(save, client, headers)
    assert response.status_code == 404
    assert not (manager.app_docs_dir / "chat_avatars").exists()


def test_storage_root_changed_during_decode_rejected(backend, monkeypatch, tmp_path):
    client, manager, headers, _ = backend
    real_normalize = route.normalize_png

    def change_root(payload):
        manager.app_docs_dir = tmp_path / "different-root"
        return real_normalize(payload)

    monkeypatch.setattr(route, "normalize_png", change_root)
    assert save(client, headers).json()["code"] == "chat_avatar_storage_changed"
    assert not (manager.app_docs_dir / "chat_avatars").exists()


@pytest.mark.asyncio
async def test_all_registered_sockets_receive_invalidation_and_bad_peer_does_not_rollback(backend):
    client, manager, headers, _ = backend

    class Socket:
        def __init__(self, fail=False):
            self.messages = []
            self.fail = fail

        async def send_text(self, message):
            if self.fail:
                raise OSError("connection closed")
            self.messages.append(json.loads(message))

    peers = [Socket(), Socket(), Socket(fail=True)]
    for peer in peers:
        register_chat_avatar_connection(peer)
    try:
        result = await route._save(manager, UID, Path(manager.app_docs_dir), None, "0", "notify")
        assert result.status_code == 200
        for peer in peers[:2]:
            assert peer.messages == [{"type": "chat_avatar_changed", "character_uid": UID,
                                      "revision": json.loads(result.body)["revision"]}]
    finally:
        for peer in peers:
            unregister_chat_avatar_connection(peer)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["Gone", "."])
async def test_character_delete_cleans_avatar_only_after_transaction_commits(tmp_path, name):
    from tests.unit.test_character_uid import _backfilled_manager, _init_router_state
    manager, _ = _backfilled_manager(tmp_path, {"Current": {"昵称": "Current"}, name: {"昵称": name}})
    uid = get_character_uid(manager.load_characters()["猫娘"][name])
    directory = manager.app_docs_dir / "chat_avatars"
    record = store.write_record(directory, uid, store.normalize_png(png()), "0", "original")
    with patch("utils.config_manager._config_manager", manager):
        crud = _init_router_state(manager)

        async def reload_before_commit(**kwargs):
            assert store.read_record(directory, uid) == record
            return True

        with patch.object(crud, "release_memory_server_character", AsyncMock(return_value=True)), patch.object(
            crud, "notify_memory_server_reload", reload_before_commit,
        ):
            response = await crud.delete_catgirl(name)
    body = response if isinstance(response, dict) else json.loads(response.body)
    assert body["success"] is True
    assert not (directory / f"{uid}.json").exists()


@pytest.mark.asyncio
async def test_character_delete_rollback_preserves_avatar(tmp_path):
    from tests.unit.test_character_uid import _backfilled_manager, _init_router_state
    manager, _ = _backfilled_manager(tmp_path, {"Current": {"昵称": "Current"}, "Gone": {"昵称": "Gone"}})
    uid = get_character_uid(manager.load_characters()["猫娘"]["Gone"])
    directory = manager.app_docs_dir / "chat_avatars"
    record = store.write_record(directory, uid, store.normalize_png(png()), "0", "original")
    with patch("utils.config_manager._config_manager", manager):
        crud = _init_router_state(manager)
        with patch.object(crud, "release_memory_server_character", AsyncMock(return_value=True)), patch.object(
            crud, "notify_memory_server_reload", AsyncMock(return_value=False),
        ):
            response = await crud.delete_catgirl("Gone")
    assert response.status_code == 500
    assert "Gone" in manager.load_characters()["猫娘"]
    assert store.read_record(directory, uid) == record


@pytest.mark.asyncio
async def test_character_delete_finalize_failure_rolls_back_and_preserves_avatar(tmp_path):
    from tests.unit.test_character_uid import _backfilled_manager, _init_router_state
    manager, _ = _backfilled_manager(tmp_path, {"Current": {"昵称": "Current"}, "Gone": {"昵称": "Gone"}})
    uid = get_character_uid(manager.load_characters()["猫娘"]["Gone"])
    directory = manager.app_docs_dir / "chat_avatars"
    record = store.write_record(directory, uid, store.normalize_png(png()), "0", "original")
    with patch("utils.config_manager._config_manager", manager):
        crud = _init_router_state(manager)
        with patch.object(crud, "release_memory_server_character", AsyncMock(return_value=True)), patch.object(
            crud, "notify_memory_server_reload", AsyncMock(return_value=True),
        ), patch.object(crud, "finalize_character_recent_delete", side_effect=PermissionError("finalize failed")):
            response = await crud.delete_catgirl("Gone")
    assert response.status_code == 500
    assert "Gone" in manager.load_characters()["猫娘"]
    assert store.read_record(directory, uid) == record


@pytest.mark.asyncio
async def test_cancel_after_character_delete_commit_still_cleans_avatar(tmp_path, monkeypatch):
    from tests.unit.test_character_uid import _backfilled_manager, _init_router_state
    manager, _ = _backfilled_manager(tmp_path, {"Current": {"昵称": "Current"}, "Gone": {"昵称": "Gone"}})
    uid = get_character_uid(manager.load_characters()["猫娘"]["Gone"])
    directory = manager.app_docs_dir / "chat_avatars"
    store.write_record(directory, uid, store.normalize_png(png()), "0", "original")
    entered, release = threading.Event(), threading.Event()
    with patch("utils.config_manager._config_manager", manager):
        crud = _init_router_state(manager)
        finalize = crud.finalize_character_recent_delete

        def blocked_finalize(*args):
            entered.set()
            assert release.wait(5)
            return finalize(*args)

        monkeypatch.setattr(crud, "finalize_character_recent_delete", blocked_finalize)
        with patch.object(crud, "release_memory_server_character", AsyncMock(return_value=True)), patch.object(
            crud, "notify_memory_server_reload", AsyncMock(return_value=True),
        ):
            task = asyncio.create_task(crud.delete_catgirl("Gone"))
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert "Gone" not in manager.load_characters()["猫娘"]
    assert not (directory / f"{uid}.json").exists()


@pytest.mark.asyncio
async def test_failed_delete_cleanup_is_finished_by_next_save_without_quota_debt(tmp_path, monkeypatch):
    from tests.unit.test_character_uid import _backfilled_manager, _init_router_state
    manager, _ = _backfilled_manager(tmp_path, {"Current": {"昵称": "Current"}, "Gone": {"昵称": "Gone"}})
    characters = manager.load_characters()["猫娘"]
    uid, gone = get_character_uid(characters["Current"]), get_character_uid(characters["Gone"])
    directory = manager.app_docs_dir / "chat_avatars"
    data = store.normalize_png(png())
    store.write_record(directory, gone, data, "0", "original")
    orphan = directory / f"{gone}.json"
    with patch("utils.config_manager._config_manager", manager):
        crud = _init_router_state(manager)
        with patch.object(crud, "release_memory_server_character", AsyncMock(return_value=True)), patch.object(
            crud, "notify_memory_server_reload", AsyncMock(return_value=True),
        ), patch.object(crud, "remove_chat_avatar_record",
                        side_effect=store.ChatAvatarError("chat_avatar_write_failed", 503)):
            response = await crud.delete_catgirl("Gone")
        body = response if isinstance(response, dict) else json.loads(response.body)
        assert body["success"] is True and body["chat_avatar_cleanup_failed"] is True
        assert orphan.is_file()

        # Counted, the orphan alone would leave no room for the live character's avatar.
        monkeypatch.setattr(store, "STORAGE_QUOTA_BYTES", orphan.stat().st_size + 10)
        saved = await route._save(manager, uid, Path(manager.app_docs_dir), data, "0", "after-delete")
    assert saved.status_code == 200, saved.body
    assert not orphan.exists()
    assert store.read_record(directory, uid)["last_operation_id"] == "after-delete"


def test_migration_copies_chat_records_and_retains_original_backup(backend, tmp_path):
    from utils.storage_migration import create_pending_storage_migration, run_pending_storage_migration
    from utils.cloudsave_runtime import runtime_root_has_user_content
    _, manager, _, _ = backend
    source = manager.app_docs_dir
    record = store.write_record(source / "chat_avatars", UID, store.normalize_png(png()), "0", "original")
    assert runtime_root_has_user_content(source, config_manager=manager)
    target = tmp_path / "migrated" / "AvatarTests"
    create_pending_storage_migration(manager, source_root=source, target_root=target, selection_source="custom")
    result = run_pending_storage_migration(manager)
    assert result["completed"] is True, result
    assert store.read_record(source / "chat_avatars", UID) == record
    assert store.read_record(target / "chat_avatars", UID) == record
    assert result["payload"]["retained_source_root"] == str(source.resolve())


def test_target_with_only_chat_avatar_requires_confirmation(backend, tmp_path):
    from utils.storage_migration import create_pending_storage_migration, run_pending_storage_migration
    from utils.cloudsave_runtime import runtime_root_has_user_content
    _, manager, _, _ = backend
    target = tmp_path / "existing-target" / "AvatarTests"
    record = store.write_record(target / "chat_avatars", UID, store.normalize_png(png()), "0", "existing")
    assert runtime_root_has_user_content(target, config_manager=manager)
    create_pending_storage_migration(manager, source_root=manager.app_docs_dir, target_root=target, selection_source="custom")
    result = run_pending_storage_migration(manager)
    assert result["error_code"] == "target_confirmation_required"
    assert store.read_record(target / "chat_avatars", UID) == record


def test_unexpected_notify_failure_preserves_success_response(backend, monkeypatch):
    client, _, headers, _ = backend
    monkeypatch.setattr(route, "notify_chat_avatar_changed", AsyncMock(side_effect=RuntimeError("notification unavailable")))
    saved = save(client, headers)
    assert saved.status_code == 200
    assert client.get(URL).json() == saved.json()


def test_unavailable_fence_storage_has_typed_error(backend, monkeypatch):
    client, _, headers, _ = backend
    monkeypatch.setattr(route, "cloudsave_writable_transaction", lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("state unavailable")))
    response = save(client, headers)
    assert response.status_code == 503 and response.json()["code"] == "chat_avatar_write_failed"
    assert client.get(URL).json()["revision"] == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_commit", [False, True], ids=["success", "commit-failure"])
async def test_real_workshop_unsubscribe_avatar_cleanup_after_commit(tmp_path, monkeypatch, fail_commit):
    from tests.unit.test_character_uid import _backfilled_manager, _DummyRequest
    from tests.unit.test_workshop_unsubscribe_theater_cascade import _install_unsubscribe, ITEM_ID
    manager, path = _backfilled_manager(tmp_path, {
        "Current": {"昵称": "Current"},
        "Gone": {"昵称": "Gone", "_reserved": {"character_origin": {
            "source": "steam_workshop", "source_id": str(ITEM_ID),
        }}},
    })
    uid = get_character_uid(manager.load_characters()["猫娘"]["Gone"])
    directory = manager.app_docs_dir / "chat_avatars"
    record = store.write_record(directory, uid, store.normalize_png(png()), "0", "original")
    unsubscribe, steam_calls = _install_unsubscribe(monkeypatch, manager, candidate="Gone")
    if fail_commit:
        import utils.config_manager.characters as character_storage
        real_atomic_write = character_storage.atomic_write_json

        def fail_character_file(target, *args, **kwargs):
            if Path(target) == path:
                raise PermissionError("characters file occupied")
            return real_atomic_write(target, *args, **kwargs)

        monkeypatch.setattr(character_storage, "atomic_write_json", fail_character_file)
    response = await unsubscribe.unsubscribe_workshop_item(_DummyRequest({"item_id": str(ITEM_ID)}))
    if fail_commit:
        assert response.status_code == 500
        assert "Gone" in manager.load_characters()["猫娘"]
        assert store.read_record(directory, uid) == record
        assert steam_calls == []
    else:
        assert response["success"] is True, response
        assert "Gone" not in manager.load_characters()["猫娘"]
        assert not (directory / f"{uid}.json").exists()
        assert steam_calls == [ITEM_ID]


@pytest.mark.asyncio
async def test_cancel_started_workshop_commit_finishes_avatar_cleanup(tmp_path, monkeypatch):
    from tests.unit.test_character_uid import _backfilled_manager, _DummyRequest
    from tests.unit.test_workshop_unsubscribe_theater_cascade import _install_unsubscribe, ITEM_ID
    manager, _ = _backfilled_manager(tmp_path, {
        "Current": {"昵称": "Current"},
        "Gone": {"昵称": "Gone", "_reserved": {"character_origin": {
            "source": "steam_workshop", "source_id": str(ITEM_ID),
        }}},
    })
    uid = get_character_uid(manager.load_characters()["猫娘"]["Gone"])
    directory = manager.app_docs_dir / "chat_avatars"
    store.write_record(directory, uid, store.normalize_png(png()), "0", "original")
    unsubscribe, steam_calls = _install_unsubscribe(monkeypatch, manager, candidate="Gone")
    entered, release = threading.Event(), threading.Event()
    real_remove = unsubscribe.remove_chat_avatar_record

    def blocked_remove(*args):
        entered.set()
        assert release.wait(5)
        return real_remove(*args)

    monkeypatch.setattr(unsubscribe, "remove_chat_avatar_record", blocked_remove)
    task = asyncio.create_task(unsubscribe.unsubscribe_workshop_item(_DummyRequest({"item_id": str(ITEM_ID)})))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert unsubscribe.character_config_mutation_lock.locked() and not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "Gone" not in manager.load_characters()["猫娘"]
    assert not (directory / f"{uid}.json").exists()
    assert steam_calls == [ITEM_ID]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["before_commit", "config_write", "remove_runtime", "reload"])
async def test_unsafe_name_delete_cancellation_cleans_only_committed_avatar(tmp_path, monkeypatch, stage):
    from tests.unit.test_character_uid import _backfilled_manager, _init_router_state
    manager, _ = _backfilled_manager(tmp_path, {"Current": {"昵称": "Current"}, ".": {"昵称": "."}})
    uid = get_character_uid(manager.load_characters()["猫娘"]["."])
    directory = manager.app_docs_dir / "chat_avatars"
    original_record = store.write_record(directory, uid, None, "0", "original")
    entered, release = threading.Event(), threading.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    with patch("utils.config_manager._config_manager", manager):
        crud = _init_router_state(manager)
        monkeypatch.setattr(crud, "notify_memory_server_reload", AsyncMock(return_value=True))
        if stage == "before_commit":
            monkeypatch.setattr(crud, "purge_numeric_v2_character_data", blocked)
        elif stage == "remove_runtime":
            monkeypatch.setattr(crud, "get_remove_one_catgirl", lambda: blocked)
        elif stage == "reload":
            monkeypatch.setattr(crud, "notify_memory_server_reload", blocked)
        else:
            save_characters = manager.save_characters

            def blocked_save(data, *args, **kwargs):
                result = save_characters(data, *args, **kwargs)
                entered.set()
                assert release.wait(5)
                return result

            monkeypatch.setattr(manager, "save_characters", blocked_save)

        task = asyncio.create_task(crud.delete_catgirl("."))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            if stage == "config_write":
                await asyncio.sleep(0)
                assert character_config_mutation_lock.locked() and not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    if stage == "before_commit":
        assert "." in manager.load_characters()["猫娘"]
        assert store.read_record(directory, uid) == original_record
    else:
        assert "." not in manager.load_characters()["猫娘"]
        assert not (directory / f"{uid}.json").exists()


@pytest.mark.asyncio
async def test_unsafe_name_delete_rollback_preserves_avatar(tmp_path):
    from tests.unit.test_character_uid import _backfilled_manager, _init_router_state
    manager, _ = _backfilled_manager(tmp_path, {"Current": {"昵称": "Current"}, ".": {"昵称": "."}})
    uid = get_character_uid(manager.load_characters()["猫娘"]["."])
    directory = manager.app_docs_dir / "chat_avatars"
    original_record = store.write_record(directory, uid, None, "0", "original")
    with patch("utils.config_manager._config_manager", manager):
        crud = _init_router_state(manager)
        with patch.object(crud, "notify_memory_server_reload", AsyncMock(return_value=False)):
            response = await crud.delete_catgirl(".")
    assert response.status_code == 500
    assert "." in manager.load_characters()["猫娘"]
    assert store.read_record(directory, uid) == original_record
