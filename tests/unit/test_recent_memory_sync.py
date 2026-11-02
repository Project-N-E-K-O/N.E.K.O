"""Recent-memory exchange must never enumerate or erase original archives."""

from copy import deepcopy
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401

from memory.recent_sync import RecentMemoryAdapter
from services.chat_sync.ledger import ChatDomain
from services.chat_sync.recent import RecentMemorySync
from services.chat_sync.recent_protocol import MEMO_ID, canonical, changes, merge, mutation, snapshot
from services.chat_sync.writer import ChatSyncWriter


OWNER = "12345678-1234-1234-1234-123456789abc"
UID = "a" * 32
DOMAIN = ChatDomain(OWNER, UID)


def entry(key="b", text="same", role="user"):
    return {"id": MEMO_ID if role == "memo" else key * 32, "role": role, "text": text, "originalTime": None}


class Server:
    def __init__(self):
        self.rows, self.hidden, self.receipts, self.requests = [], [], {}, []
        self.lose_receipt = False

    async def request(self, method, path, body):
        assert path == f"/v1/chat-sync/characters/{UID}/recent-memory"
        if method == "GET":
            return {"schemaVersion": 1, "ownerId": OWNER, "characterSyncId": UID,
                    "revision": len(self.receipts), "entries": deepcopy(self.rows)}
        value = mutation(json.loads(body))
        self.requests.append(body)
        operation = value["operationId"]
        if operation not in self.receipts:
            self.rows, self.hidden = merge(self.rows, self.hidden, value)
            self.receipts[operation] = {"ownerId": OWNER, "characterSyncId": UID,
                                        "operationId": operation, "revision": len(self.receipts) + 1}
        if self.lose_receipt:
            self.lose_receipt = False
            raise OSError("synthetic response loss")
        return deepcopy(self.receipts[operation])


async def commit(operation, *args):
    return operation(*args)


@pytest.fixture
def devices(tmp_path, monkeypatch):
    monkeypatch.setattr("memory.recent_sync.assert_cloudsave_writable", lambda *args, **kwargs: None)

    def create(label, rows):
        root = tmp_path / label
        path = root / "memory" / "Role" / "recent.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(rows))
        archive = path.with_name("time_indexed.db")
        with sqlite3.connect(archive) as conn:
            conn.execute("CREATE TABLE time_indexed_original(message TEXT)")
            conn.execute("INSERT INTO time_indexed_original VALUES('old history outside recent memory')")
        before = hashlib.sha256(archive.read_bytes()).hexdigest()
        directory = root / "sync"
        directory.mkdir()
        writer = ChatSyncWriter(directory, root / "installation-witness.json", database_names=("state.sqlite3",))
        adapter = RecentMemoryAdapter(SimpleNamespace(memory_dir=root / "memory", project_memory_dir=root / "memory"))
        with writer.initialize():
            sync = RecentMemorySync(directory / "state.sqlite3", adapter)
        return sync, path, archive, before

    return create


def row(role, text, **metadata):
    return {"type": role, "data": {"content": text, "metadata": metadata}}


@pytest.mark.asyncio
async def test_two_devices_share_recent_dialogue_memo_and_removal_without_touching_archives(devices):
    left = devices("left", [row("system", "先前对话的备忘录: summary"), row("human", "visible recent chat")])
    right = devices("right", [])
    server = Server()
    await left[0].sync(DOMAIN, "Role", server.request, commit)
    await right[0].sync(DOMAIN, "Role", server.request, commit)
    assert [item["text"] for item in server.rows] == ["先前对话的备忘录: summary", "visible recent chat"]
    assert "old history" not in canonical(server.rows).decode()
    right[1].write_text(json.dumps([row("system", "先前对话的备忘录: edited summary")]))
    await right[0].sync(DOMAIN, "Role", server.request, commit)
    await left[0].sync(DOMAIN, "Role", server.request, commit)
    for sync, path, archive, before in [left, right]:
        assert [item["text"] for item in sync.adapter.read("Role")["entries"]] == ["先前对话的备忘录: edited summary"]
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == before


@pytest.mark.asyncio
async def test_lost_receipt_retries_identical_projection_bytes(devices):
    sync, _, _, _ = devices("left", [row("human", "visible")])
    server = Server()
    server.lose_receipt = True
    with pytest.raises(OSError):
        await sync.sync(DOMAIN, "Role", server.request, commit)
    await sync.sync(DOMAIN, "Role", server.request, commit)
    assert len(server.receipts) == 1
    assert server.requests[0] == server.requests[1]


@pytest.mark.asyncio
async def test_concurrent_equal_text_messages_keep_separate_identities(devices):
    left = devices("left", [row("human", "same")])[0]
    right = devices("right", [row("human", "same")])[0]
    server = Server()
    await left.sync(DOMAIN, "Role", server.request, commit)
    await right.sync(DOMAIN, "Role", server.request, commit)
    await left.sync(DOMAIN, "Role", server.request, commit)
    assert len(server.rows) == 2
    assert len({item["id"] for item in server.rows}) == 2


def test_removed_projection_cannot_be_resurrected_by_a_late_device():
    original = entry()
    rows, hidden = merge([original], [], {"operationId": "c" * 32, "upserts": [], "removedIds": [original["id"]]})
    rows, _ = merge(rows, hidden, {"operationId": "d" * 32, "upserts": [original], "removedIds": []})
    assert rows == []


def test_protocol_rejects_archive_metadata_and_wrong_owner():
    with pytest.raises(ValueError):
        changes([], [{**entry(), "metadata": {"archive": "private"}}])
    with pytest.raises(ValueError):
        snapshot({"schemaVersion": 1, "ownerId": "other", "characterSyncId": UID, "revision": 1, "entries": []}, OWNER, UID)


@pytest.mark.asyncio
async def test_opted_out_existing_memory_stays_local_until_explicitly_included(devices):
    sync, _, _, _ = devices('private', [row('system', 'private memo'), row('human', 'private chat')])
    local = sync.adapter.read('Role')['entries']
    sync.exclude(DOMAIN.key(), [item['id'] for item in local])
    server = Server()
    server.rows = [entry(role='memo', text='remote memo'), entry('c', 'remote chat')]
    await sync.sync(DOMAIN, 'Role', server.request, commit)
    assert server.requests == []
    assert {item['text'] for item in sync.adapter.read('Role')['entries']} == {'private memo', 'private chat', 'remote chat'}
    await sync.sync(DOMAIN, 'Role', server.request, commit)
    assert server.requests == []
    sync.exclude(DOMAIN.key(), [])
    await sync.sync(DOMAIN, 'Role', server.request, commit)
    assert {item['text'] for item in server.rows} == {'private memo', 'private chat', 'remote chat'}


@pytest.mark.asyncio
async def test_late_edit_and_active_conversation_defer_download_without_losing_changes(devices):
    sync, path, _, _ = devices('race', [])
    server = Server()
    server.rows = [entry('c', 'from phone')]
    busy = [False]

    async def request(method, endpoint, body):
        value = await server.request(method, endpoint, body)
        if method == 'GET':
            busy[0] = True
        return value

    result = await sync.sync(DOMAIN, 'Role', request, commit, can_apply=lambda: not busy[0])
    assert result['hasMore'] and json.loads(path.read_text()) == []

    async def late_edit(method, endpoint, body):
        value = await server.request(method, endpoint, body)
        if method == 'GET':
            path.write_text(json.dumps([row('human', 'local edit during download')]))
        return value

    result = await sync.sync(DOMAIN, 'Role', late_edit, commit)
    assert result['hasMore']
    await sync.sync(DOMAIN, 'Role', server.request, commit)
    assert {item['text'] for item in server.rows} == {'from phone', 'local edit during download'}


@pytest.mark.asyncio
async def test_remote_timestamp_survives_projection_roundtrip(devices):
    sync, _, _, _ = devices('timestamps', [])
    server = Server()
    server.rows = [{**entry('c', 'known time'), 'originalTime': '1791534030123'}]
    await sync.sync(DOMAIN, 'Role', server.request, commit)
    await sync.sync(DOMAIN, 'Role', server.request, commit)
    assert server.requests == []
    assert sync.adapter.read('Role')['entries'] == server.rows


@pytest.mark.asyncio
async def test_visible_legacy_and_multimodal_text_sync_without_exporting_or_stripping_local_images(devices):
    image = {'type': 'image_url', 'image_url': {'url': 'local private image'}}
    sync, path, _, _ = devices('mixed', [
        {'role': 'user', 'content': 'legacy chat'},
        row('human', [{'type': 'text', 'text': 'photo caption'}, image]),
    ])
    server = Server()
    server.rows = [entry('e', 'remote addition')]
    await sync.sync(DOMAIN, 'Role', server.request, commit)
    assert {item['text'] for item in server.rows} == {'legacy chat', 'photo caption', 'remote addition'}
    assert 'local private image' not in canonical(server.rows).decode()
    saved = json.loads(path.read_text())
    assert any(isinstance(item.get('data', {}).get('content'), list) and image in item['data']['content'] for item in saved)


@pytest.mark.asyncio
@pytest.mark.parametrize('pause_during_download', [False, True])
async def test_runtime_uses_only_recent_endpoint_and_checks_binding_before_applying(tmp_path, monkeypatch, pause_during_download):
    import httpx
    from tests.unit.test_chat_sync_runtime import SyntheticService, setup_runtime

    monkeypatch.setattr('memory.recent_sync.assert_cloudsave_writable', lambda *args, **kwargs: None)
    path = tmp_path / 'memory' / 'Neko' / 'recent.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps([row('system', 'memo'), row('human', 'current chat')]))
    remote = Server()

    class RecentService(SyntheticService):
        async def request(self, request):
            if request.url.path.endswith('/recent-memory'):
                self.requests.append(request)
                assert request.headers['X-Neko-Chat-Binding'] == 'c' * 32
                value = await remote.request(request.method, request.url.path, request.content or None)
                if request.method == 'GET':
                    value['entries'].append(entry('f', 'from phone'))
                    if pause_during_download:
                        await runtime.pause(UID)
                return httpx.Response(200, json=value)
            assert '/batches/' not in request.url.path and not request.url.path.endswith('/changes')
            return await super().request(request)

    service = RecentService()
    service.owner = OWNER
    adapter = RecentMemoryAdapter(SimpleNamespace(memory_dir=tmp_path / 'memory', project_memory_dir=tmp_path / 'memory'))
    runtime, _, _ = setup_runtime(tmp_path, service, recent_memory_adapter=adapter)
    async def forbidden_archive_provider(uid):
        raise AssertionError('recent sync must never enumerate archived history')
    runtime.chat_history_provider = forbidden_archive_provider
    try:
        await runtime.configure('https://sync.test')
        await runtime.enable(UID, 'Neko')
        if pause_during_download:
            with pytest.raises(ValueError, match='chat_sync_binding_changed'):
                await runtime._channel_pass(UID, 'pull', manual=True)
            assert [item['data']['content'] for item in json.loads(path.read_text())] == ['memo', 'current chat']
        else:
            result = await runtime._channel_pass(UID, 'pull', manual=True)
            assert result['uploaded'] == 2
            assert [item['data']['content'] for item in json.loads(path.read_text())] == ['memo', 'current chat', 'from phone']
            assert runtime.recent_memory.state(DOMAIN.key())['revision'] == 1
    finally:
        await runtime.close()
