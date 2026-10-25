"""Memory Browser stays on local recent memories, independent of chat sync."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from main_routers import memory_router as routes


@pytest.fixture
def editor(tmp_path, monkeypatch):
    import httpx
    import utils.config_manager as config
    import services.chat_sync.runtime as runtime_module

    path = tmp_path / "Role" / "recent.json"
    path.parent.mkdir()
    path.write_text(json.dumps([{"type": "human", "data": {"content": "local memory"}}]))
    cm = SimpleNamespace(
        memory_dir=tmp_path,
        project_memory_dir=tmp_path,
        aload_characters=AsyncMock(return_value={"猫娘": {
            "Role": {"_reserved": {"character_uid": "a" * 32}},
            "PhoneOnly": {"_reserved": {"character_uid": "b" * 32}},
        }}),
    )
    sync_runtime = AsyncMock(side_effect=AssertionError("Memory Browser must not access chat sync"))
    monkeypatch.setattr(config, "get_config_manager", lambda: cm)
    monkeypatch.setattr(runtime_module, "get_desktop_chat_sync_runtime", sync_runtime)
    monkeypatch.setattr(routes, "assert_cloudsave_writable", lambda *a, **k: None)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return None

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Client())
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234)) as client:
        yield client, path, cm, sync_runtime
    sync_runtime.assert_not_awaited()


def test_read_returns_only_local_recent_memory_even_with_the_retired_sync_query(editor):
    client, path, _, _ = editor
    before = path.read_bytes()
    response = client.get("/api/memory/recent_file", params={
        "filename": "recent_Role.json", "include_chat_sync": True,
    })
    assert response.status_code == 200
    loaded = response.json()
    assert set(loaded) == {"content", "fingerprint", "identity_token"}
    assert json.loads(loaded["content"])[0]["data"]["content"] == "local memory"
    assert path.read_bytes() == before


def test_file_list_does_not_add_roles_without_local_recent_memory(editor):
    client, _, cm, _ = editor
    response = client.get("/api/memory/recent_files")
    assert response.status_code == 200
    assert response.json() == {"files": ["recent_Role.json"]}
    cm.aload_characters.assert_not_awaited()


def test_missing_recent_file_is_not_replaced_with_phone_history(editor):
    client, path, _, _ = editor
    path.unlink()
    response = client.get("/api/memory/recent_file", params={
        "filename": "recent_Role.json", "include_chat_sync": True,
    })
    assert response.status_code == 404
    assert not path.exists()


@pytest.mark.parametrize("chat", [[{"role": "human", "text": "edited local memory"}], []])
def test_save_and_clear_only_update_the_recent_memory_file(editor, chat):
    client, path, _, _ = editor
    loaded = client.get("/api/memory/recent_file", params={"filename": "recent_Role.json"}).json()
    response = client.post("/api/memory/recent_file/save", json={
        "filename": "recent_Role.json", "chat": chat,
        "fingerprint": loaded["fingerprint"], "identity_token": loaded["identity_token"],
    })
    assert response.status_code == 200
    saved = response.json()
    assert saved["success"] is True
    assert saved["need_refresh"] is True
    assert "chat_deletion" not in saved
    assert [row["data"]["content"] for row in json.loads(path.read_text())] == [
        row["text"] for row in chat
    ]


@pytest.mark.parametrize('openai_format', [False, True])
def test_editor_preserves_server_projection_identity_and_timestamp(editor, openai_format):
    client, path, _, _ = editor
    metadata = {'recent_memory_id': 'd' * 32, 'recent_memory_time': '1791534030123'}
    original = ({'role': 'user', 'content': 'before', 'metadata': metadata} if openai_format else
                {'type': 'human', 'data': {'content': 'before', 'metadata': metadata}})
    path.write_text(json.dumps([original]))
    loaded = client.get('/api/memory/recent_file', params={'filename': 'recent_Role.json'}).json()
    response = client.post('/api/memory/recent_file/save', json={
        'filename': 'recent_Role.json', 'chat': [{'role': 'human', 'text': 'after', 'source_index': 0}],
        'fingerprint': loaded['fingerprint'], 'identity_token': loaded['identity_token'],
    })
    assert response.status_code == 200 and response.json()['success']
    assert json.loads(path.read_text())[0]['data']['metadata'] == metadata
