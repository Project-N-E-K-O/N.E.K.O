"""Memory Browser saves freeze exact chat IDs without touching real user data."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from main_routers import memory_router as routes
from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401

UID = "a" * 32
MID = "b" * 32
LOCAL = "c" * 32
BINDING = {"owner_id": "owner", "binding_id": "binding", "generation": 2}


@pytest.fixture(autouse=True)
def isolated_conversation_state(monkeypatch):
    monkeypatch.setattr("main_routers.shared_state.get_session_manager", lambda: {})


@pytest.fixture
def editor(tmp_path, monkeypatch):
    import httpx
    import utils.config_manager as config
    import services.chat_sync.runtime as runtime_module
    import services.chat_sync.source as sources

    path = tmp_path / "Role" / "recent.json"
    path.parent.mkdir()
    path.write_text(json.dumps([{"type": "human", "data": {"content": "same"}}]))
    runtime = SimpleNamespace(
        state=SimpleNamespace(binding=lambda uid: dict(BINDING)),
        history=AsyncMock(return_value=[{"messageId": MID, "role": "user", "text": "same"}]),
        local_replies=AsyncMock(return_value=[{"messageId": LOCAL, "role": "assistant", "text": "tool"}]),
        delete=AsyncMock(return_value={"messageIds": [MID], "deletionIds": ["d" * 32], "pending": True}),
        start_deletion_flush=lambda uid: None,
    )
    cm = SimpleNamespace(memory_dir=tmp_path, project_memory_dir=tmp_path,
        aload_characters=AsyncMock(return_value={"猫娘": {"Role": {"_reserved": {"character_uid": UID}}}}))
    clear = AsyncMock(return_value=[LOCAL])
    monkeypatch.setattr(config, "get_config_manager", lambda: cm)
    monkeypatch.setattr(runtime_module, "get_desktop_chat_sync_runtime", AsyncMock(return_value=runtime))
    monkeypatch.setattr(sources, "clear_local_replies", clear)
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
        yield client, path, runtime, clear


def snapshot(client):
    response = client.get("/api/memory/recent_file", params={"filename": "recent_Role.json", "include_chat_sync": True})
    assert response.status_code == 200
    return response.json()


def save_payload(loaded):
    return {"filename": "recent_Role.json", "chat": [], "fingerprint": loaded["fingerprint"],
        "identity_token": loaded["identity_token"], "chat_sync": {
            "characterSyncId": UID, "binding": loaded["chat_sync"]["binding"],
            "messageIds": [MID], "localMessageIds": [LOCAL]}}


def test_snapshot_separates_originals_from_local_memory_and_local_tool_replies(editor):
    client, path, runtime, clear = editor
    before = path.read_bytes()
    loaded = snapshot(client)
    assert loaded["chat_sync"]["originals"][0]["messageId"] == MID
    assert loaded["chat_sync"]["localReplies"][0]["messageId"] == LOCAL
    assert json.loads(loaded["content"])[0]["data"]["content"] == "same"
    assert path.read_bytes() == before
    runtime.delete.assert_not_called()
    clear.assert_not_called()


def test_save_commits_exact_ids_and_keeps_tool_deletion_local(editor):
    client, path, runtime, clear = editor
    payload = save_payload(snapshot(client))
    response = client.post("/api/memory/recent_file/save", json=payload)
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["chat_deletion"]["localMessageIds"] == [LOCAL]
    runtime.delete.assert_awaited_once_with(UID, [MID], expected_binding=payload['chat_sync']['binding'])
    clear.assert_awaited_once_with(runtime, UID, [LOCAL])
    assert json.loads(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("change", ["content", "binding"])
def test_stale_save_never_queues_deletions(editor, change):
    client, path, runtime, clear = editor
    payload = save_payload(snapshot(client))
    if change == "content":
        path.write_text("[]")
    else:
        runtime.state.binding = lambda uid: {**BINDING, "generation": 3}
    response = client.post("/api/memory/recent_file/save", json=payload)
    assert response.status_code == 409
    runtime.delete.assert_not_called()
    clear.assert_not_called()


def test_missing_recent_file_can_manage_phone_history_without_creating_memory_on_read(editor):
    client, path, _, _ = editor
    path.unlink()
    assert "recent_Role.json" in client.get("/api/memory/recent_files").json()["files"]
    loaded = snapshot(client)
    assert json.loads(loaded["content"]) == []
    assert not path.exists()
    assert client.post("/api/memory/recent_file/save", json=save_payload(loaded)).json()["success"]


def test_foreign_origin_cannot_read_or_delete_chat_originals(editor):
    client, _, runtime, clear = editor
    payload = save_payload(snapshot(client))
    headers = {"Origin": "https://foreign.invalid"}
    assert client.get("/api/memory/recent_file?filename=recent_Role.json&include_chat_sync=true", headers=headers).status_code == 403
    assert client.post("/api/memory/recent_file/save", json=payload, headers=headers).status_code == 403
    runtime.delete.assert_not_called()
    clear.assert_not_called()


@pytest.mark.parametrize('field,value', [('messageIds', ['bad']), ('localMessageIds', [MID, MID])])
def test_invalid_deletion_ids_do_not_write_memory_or_queue_work(editor, field, value):
    client, path, runtime, clear = editor
    payload = save_payload(snapshot(client))
    payload['chat_sync'][field] = value
    before = path.read_bytes()
    assert client.post('/api/memory/recent_file/save', json=payload).status_code == 400
    assert path.read_bytes() == before
    runtime.delete.assert_not_called()
    clear.assert_not_called()


def test_json_write_failure_reports_durable_deletion_and_retains_editor_snapshot(editor, monkeypatch):
    client, path, runtime, _ = editor
    loaded = snapshot(client)
    before = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError('synthetic replacement failure')

    monkeypatch.setattr(routes, 'write_recent_payload_unlocked', fail)
    response = client.post('/api/memory/recent_file/save', json=save_payload(loaded))
    assert response.status_code == 503
    assert response.json()['code'] == 'CHAT_DELETION_SAVED_MEMORY_FAILED'
    assert response.json()['chat_deletion']['messageIds'] == [MID]
    assert path.read_bytes() == before
    runtime.delete.assert_awaited_once_with(UID, [MID], expected_binding=loaded['chat_sync']['binding'])


def test_clear_save_cannot_expand_to_chat_arriving_after_preview(editor):
    client, _, runtime, _ = editor
    loaded = snapshot(client)
    runtime.history.return_value.append({'messageId': 'e' * 32, 'role': 'user', 'text': 'later'})
    assert client.post('/api/memory/recent_file/save', json=save_payload(loaded)).json()['success']
    runtime.delete.assert_awaited_once_with(UID, [MID], expected_binding=loaded['chat_sync']['binding'])


@pytest.mark.asyncio
async def test_saved_deletions_survive_restart_and_reach_a_second_device(tmp_path, monkeypatch):
    """Use real SQLite and the normal marker transport with synthetic accounts."""
    import httpx
    from starlette.requests import Request
    import services.chat_sync.runtime as runtime_module
    import utils.config_manager as config
    from services.chat_sync.ledger import ChatOriginal, SourceLocator
    from services.chat_sync.protocol import encode_chat_original
    from services.chat_sync.source import ChatOriginalSource
    from tests.unit.test_chat_sync_runtime import DOMAIN, setup_runtime

    desktop, service, _ = setup_runtime(tmp_path / 'desktop')
    phone, _, _ = setup_runtime(tmp_path / 'phone', service)
    reopened = None
    for runtime in (desktop, phone):
        await runtime.configure('https://sync.test')
        await runtime.enable(UID, 'Role')
    try:
        records = [desktop.ledger.capture(DOMAIN, SourceLocator('synthetic-db', 'generation', str(i)),
            ChatOriginal('user', 'same'), message_id=char * 32) for i, char in enumerate('bef')]
        raw = [encode_chat_original(record).decode() for record in records]
        service.records = raw
        phone.ledger.apply_changes(DOMAIN, {'cursor': 1, 'hasMore': False, 'originals': raw,
                                           'deletedMessageIds': []}, after=0)
        source = ChatOriginalSource(desktop.state.path.parent / 'sources.sqlite3')
        source.commit_local_reply(UID, LOCAL, ChatOriginal('assistant', 'local tool'), 'tool')
        memory = tmp_path / 'memory'
        path = memory / 'Role' / 'recent.json'
        path.parent.mkdir(parents=True)
        path.write_text('[]')
        cm = SimpleNamespace(memory_dir=memory, project_memory_dir=memory,
            aload_characters=AsyncMock(return_value={'猫娘': {'Role': {'_reserved': {'character_uid': UID}}}}))
        monkeypatch.setattr(config, 'get_config_manager', lambda: cm)
        monkeypatch.setattr(runtime_module, 'get_desktop_chat_sync_runtime', AsyncMock(return_value=desktop))
        monkeypatch.setattr(routes, 'assert_cloudsave_writable', lambda *a, **k: None)
        monkeypatch.setattr(desktop, 'start_deletion_flush', lambda uid: None)

        def request(payload=None):
            async def receive():
                return {'type': 'http.request', 'body': json.dumps(payload).encode(), 'more_body': False}
            return Request({'type': 'http', 'method': 'POST', 'scheme': 'http',
                'path': '/api/memory/recent_file/save', 'query_string': b'', 'headers': [],
                'server': ('127.0.0.1', 80), 'client': ('127.0.0.1', 1234)}, receive)

        loaded = json.loads((await routes.get_recent_file('recent_Role.json', request(), True)).body)
        payload = save_payload(loaded)
        payload['chat_sync']['messageIds'] = [MID, 'e' * 32]
        requests_before = len(service.requests)

        class NoCorrectionNetwork:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, *args, **kwargs):
                return None

        with monkeypatch.context() as local:
            local.setattr(httpx, 'AsyncClient', lambda **kwargs: NoCorrectionNetwork())
            result = await routes.save_recent_file(request(payload))
        assert result['success']
        assert len(service.requests) == requests_before, 'Save must not wait for remote I/O'
        assert [item['messageId'] for item in await desktop.history(UID)] == ['f' * 32]
        assert await desktop.local_replies(UID) == []
        await desktop.close()
        reopened, _, _ = setup_runtime(tmp_path / 'desktop', service)
        assert {mid for row in reopened.ledger.pending_deletions(DOMAIN) for mid in row.message_ids} == {MID, 'e' * 32}
        assert await reopened.local_replies(UID) == []
        await reopened.flush_deletions(UID)
        await phone._channel_pass(UID, 'pull', manual=True)
        assert [item['messageId'] for item in await phone.history(UID)] == ['f' * 32]
        assert set(service.deleted) == {MID, 'e' * 32}
        assert LOCAL not in service.deleted
    finally:
        await desktop.close()
        await phone.close()
        if reopened is not None:
            await reopened.close()


@pytest.mark.asyncio
async def test_binding_change_during_recovery_cannot_retarget_saved_deletion(tmp_path, monkeypatch):
    """Recheck at the actual marker commit, after all async recovery work."""
    from tests.unit.test_chat_sync_runtime import DOMAIN, setup_runtime
    runtime, _, _ = setup_runtime(tmp_path)
    try:
        await runtime.configure('https://sync.test')
        await runtime.enable(UID, 'Role')
        reviewed = routes._memory_chat_binding(runtime.state.binding(UID))

        async def rebound():
            runtime.state.pause(UID)

        monkeypatch.setattr(runtime, '_recover_originals', rebound)
        with pytest.raises(ValueError, match='chat_sync_binding_changed'):
            await runtime.delete(UID, [MID], expected_binding=reviewed)
        assert runtime.ledger.pending_deletions(DOMAIN) == []
        assert runtime.ledger.deleted_message_ids(DOMAIN) == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_save_with_one_default_pool_worker_cannot_deadlock_its_deletion_callback(editor):
    from concurrent.futures import ThreadPoolExecutor
    import asyncio
    client, path, runtime, clear = editor
    payload = save_payload(snapshot(client))
    from starlette.requests import Request
    request = Request({"type": "http", "method": "POST", "path": "/api/memory/recent_file/save",
        "scheme": "http", "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 80),
        "headers": [(b"host", b"127.0.0.1")]})
    request._body = json.dumps(payload).encode()
    # Call the real coroutine on a loop whose shared worker pool has one slot.
    loop = asyncio.get_running_loop()
    pool = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(pool)
    try:
        result = await asyncio.wait_for(routes.save_recent_file(request), 5)
        assert result['success'] is True
        runtime.delete.assert_awaited_once()
    finally:
        # Restore an independent pool before retiring this test-owned executor.
        loop.set_default_executor(ThreadPoolExecutor())
        pool.shutdown(wait=True)


def test_unfinished_reply_blocks_canonical_deletion_without_writing_memory(editor, monkeypatch):
    from services.chat_sync.profile_sync import DesktopProfileAdapter
    client, path, runtime, clear = editor
    payload = save_payload(snapshot(client))
    before = path.read_bytes()
    monkeypatch.setattr(DesktopProfileAdapter, 'busy', lambda self, name: True)
    result = client.post('/api/memory/recent_file/save', json=payload)
    assert result.status_code == 409
    assert result.json()['detail'] == 'chat_sync_reply_in_progress'
    assert path.read_bytes() == before
    runtime.delete.assert_not_called()
    clear.assert_not_called()


@pytest.mark.parametrize('missing_translation', [False, True])
def test_sync_delete_confirmation_remains_explicit_without_a_loaded_translation(missing_translation):
    """A missing i18n loader/key cannot turn permanent multi-device deletion into an empty prompt."""
    source_path = Path(__file__).parents[2] / 'static/js/memory_browser.js'
    script = r"""
        const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
        const source=fs.readFileSync(process.argv[1],'utf8');
        let prompt;
        global.window={confirm(text){prompt=text;return false;}};
        if(MISSING_KEY)window.t=key=>key;
        global.currentMemoryFile='recent_Synthetic.json';
        global.currentMemoryFingerprint='synthetic-fingerprint';
        global.currentMemoryIdentityToken='synthetic-token';
        global.memoryFileRequestId=1;global.memorySaveRequestId=0;global.memoryEditRevision=1;
        global.chatData=[];global.chatSyncSnapshot={characterSyncId:'a'.repeat(32),binding:{}};
        global.deletedOriginalIds=new Set(['b'.repeat(32),'c'.repeat(32)]);
        global.deletedLocalReplyIds=new Set();
        global.fetch=()=>{throw new Error('Cancelled deletion must not send anything');};
        vm.runInThisContext(source.slice(source.indexOf('    function interpolateText('),
            source.indexOf('    function setElementText(')));
        vm.runInThisContext(source.slice(source.indexOf('    async function saveCurrentMemoryOnce('),
            source.indexOf('    function announceChatDeletion(')));
        (async()=>{
            assert.equal(await saveCurrentMemoryOnce(),false);
            assert.equal(prompt,'Permanently delete 2 original chats from all synced devices?');
        })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace('MISSING_KEY', json.dumps(missing_translation))
    from tests.node_harness import run_node_script
    result = run_node_script('node', script.replace('process.argv[1]', json.dumps(str(source_path))),
                             capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    for language in ('en', 'es', 'ja', 'ko', 'pt', 'ru', 'zh-CN', 'zh-TW'):
        locale = json.loads((source_path.parents[1] / 'locales' / (language + '.json')).read_text(encoding="utf-8"))
        assert '{{count}}' in locale['memory']['chatDeleteConfirm']
