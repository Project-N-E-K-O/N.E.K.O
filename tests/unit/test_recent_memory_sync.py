"""Recent-memory exchange must never enumerate or erase original archives."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
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
        assert path in (f"/v1/chat-sync/characters/{UID}/recent-memory", f"/v1/chat-sync/characters/{UID}/recent-memory/view")
        if method == "GET" or path.endswith("/view"):
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


def test_recent_projection_reuses_the_sent_media_identity_without_reading_archives(devices):
    identity = "e" * 32
    sync, path, archive, digest = devices("media", [row("human", "sent image",
        chat_original_source={"recordId": identity, "originalTime": "1792700000000"})])
    assert sync.adapter.read("Role")["entries"] == [{"id": identity, "role": "user",
        "text": "sent image", "originalTime": "1792700000000"}]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest


def test_recent_projection_keeps_iso_original_time_usable_for_fact_extraction(devices):
    from datetime import datetime
    from app.memory_server.rows import _extract_role_tagged_messages_from_rows
    from utils.llm_client import convert_to_messages
    identity = 'e' * 32
    original_time = '2026-10-01T12:00:00+08:00'
    sync, path, archive, digest = devices('iso-source', [row('human', 'real original',
        chat_original_source={'recordId': identity, 'originalTime': original_time})])
    assert sync.adapter.read('Role')['entries'][0]['originalTime'] == '1790827200000'
    stored = json.loads(path.read_text())[0]
    assert stored['data']['metadata']['chat_original_source']['originalTime'] == original_time
    messages = convert_to_messages(_extract_role_tagged_messages_from_rows([
        (datetime(2026, 10, 26), 'restored-session', json.dumps(stored))]))
    assert messages[0].additional_kwargs == {
        'memory_source_id': 'sync:' + identity, 'memory_source_created_at': 1790827200000}
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize('reply', ['REPLY_02', 'corrected reply'])
def test_review_retained_text_keeps_sync_identity_and_send_time(tmp_path, monkeypatch, reply):
    from tests.unit.test_recent_file_lock import _make_manager, _write_disk
    from utils.llm_client import HumanMessage, AIMessage
    monkeypatch.setattr('memory.recent.assert_cloudsave_writable', lambda *args, **kwargs: None)
    monkeypatch.setattr('memory.recent_sync.assert_cloudsave_writable', lambda *args, **kwargs: None)
    path = tmp_path / 'Role' / 'recent.json'
    path.parent.mkdir()
    manager, name, _ = _make_manager(tmp_path, lanlan_name='Role', path=str(path))
    adapter = RecentMemoryAdapter(SimpleNamespace(memory_dir=tmp_path, project_memory_dir=tmp_path))
    original = [HumanMessage('ORDER_02', metadata={'recent_memory_id': 'a' * 32, 'recent_memory_time': '2000'}),
                AIMessage('REPLY_02', metadata={'recent_memory_id': 'b' * 32, 'recent_memory_time': '2001'})]
    _write_disk(str(path), original)
    manager._commit_review_locked(str(path), name, original, [HumanMessage('ORDER_02'), AIMessage(reply)])
    assert adapter.read(name)['entries'] == [
        {'id': 'a' * 32, 'role': 'user', 'text': 'ORDER_02', 'originalTime': '2000'},
        {'id': 'b' * 32, 'role': 'assistant', 'text': reply, 'originalTime': '2001'},
    ]


def test_review_still_removes_empty_image_message(tmp_path, monkeypatch):
    from tests.unit.test_recent_file_lock import _make_manager, _write_disk
    from utils.llm_client import HumanMessage, AIMessage
    monkeypatch.setattr('memory.recent.assert_cloudsave_writable', lambda *args, **kwargs: None)
    monkeypatch.setattr('memory.recent_sync.assert_cloudsave_writable', lambda *args, **kwargs: None)
    path = tmp_path / 'Role' / 'recent.json'
    path.parent.mkdir()
    manager, name, _ = _make_manager(tmp_path, lanlan_name='Role', path=str(path))
    original = [HumanMessage('', metadata={'recent_memory_id': 'a' * 32}),
                AIMessage('reply', metadata={'recent_memory_id': 'b' * 32})]
    _write_disk(str(path), original)
    manager._commit_review_locked(str(path), name, original, [AIMessage('reply')])
    adapter = RecentMemoryAdapter(SimpleNamespace(memory_dir=tmp_path, project_memory_dir=tmp_path))
    assert adapter.read(name)['entries'] == [
        {'id': 'b' * 32, 'role': 'assistant', 'text': 'reply', 'originalTime': None},
    ]


def _identified_review_messages():
    from utils.llm_client import HumanMessage, AIMessage

    return [
        (HumanMessage if index % 2 == 0 else AIMessage)(text, metadata={
            'recent_memory_id': 'abcdef'[index] * 32,
            'recent_memory_time': str(1000 + index),
            'chat_original_source': {'recordId': 'abcdef'[index] * 32, 'originalTime': str(1000 + index)},
        })
        for index, text in enumerate(['user-A', 'reply-A', 'user-B', 'reply-B', 'user-C', 'reply-C'])
    ]


@pytest.fixture
def review_identity_case(tmp_path, monkeypatch):
    from tests.unit.test_recent_file_lock import _make_manager, _write_disk, _read_disk

    monkeypatch.setattr('memory.recent.assert_cloudsave_writable', lambda *args, **kwargs: None)
    monkeypatch.setattr('memory.recent_sync.assert_cloudsave_writable', lambda *args, **kwargs: None)
    path = tmp_path / 'Role' / 'recent.json'
    path.parent.mkdir()
    manager, name, _ = _make_manager(tmp_path, lanlan_name='Role', path=str(path))
    adapter = RecentMemoryAdapter(SimpleNamespace(memory_dir=tmp_path, project_memory_dir=tmp_path))

    def run(original, corrected, *, current=None, **kwargs):
        _write_disk(str(path), original if current is None else current)
        before = path.read_bytes()
        status, _, _ = manager._commit_review_locked(str(path), name, original, corrected, **kwargs)
        if status != 'patched':
            assert path.read_bytes() == before
        return status, _read_disk(str(path)), adapter.read(name)['entries']

    return run


@pytest.mark.parametrize('kept', [(0, 1), (2, 3, 4, 5), (0, 1, 4, 5), (4, 5, 0, 1)])
def test_review_deletion_and_reordering_keep_content_identity(review_identity_case, kept):
    from utils.llm_client import messages_to_dict

    original = _identified_review_messages()
    corrected = [type(original[index])(original[index].content) for index in kept]
    status, saved, entries = review_identity_case(original, corrected)
    assert status == 'patched'
    assert messages_to_dict(saved) == messages_to_dict([original[index] for index in kept])
    assert [(entry['id'], entry['originalTime']) for entry in entries] == [
        (original[index].metadata['recent_memory_id'], original[index].metadata['recent_memory_time']) for index in kept
    ]


def test_review_rewritten_earlier_pair_uses_explicit_sources(review_identity_case):
    from utils.llm_client import HumanMessage, AIMessage

    original = _identified_review_messages()
    status, saved, _ = review_identity_case(original, [HumanMessage('edited user-A'), AIMessage('edited reply-A')],
                                          source_indexes=[0, 1])
    assert status == 'patched'
    assert [message.content for message in saved] == ['edited user-A', 'edited reply-A']
    assert [message.metadata for message in saved] == [message.metadata for message in original[:2]]


@pytest.mark.parametrize('source_index', [0, 2])
def test_review_duplicate_text_uses_explicit_source_identity(review_identity_case, source_index):
    from utils.llm_client import HumanMessage

    original = _identified_review_messages()
    original[0].content = original[2].content = 'same user text'
    status, saved, _ = review_identity_case(original, [HumanMessage('same user text')], source_indexes=[source_index])
    assert status == 'patched'
    assert saved[0].metadata == original[source_index].metadata


def test_review_ambiguous_legacy_duplicate_leaves_disk_unchanged(review_identity_case):
    from utils.llm_client import HumanMessage, messages_to_dict

    original = _identified_review_messages()
    original[0].content = original[2].content = 'same user text'
    status, saved, _ = review_identity_case(original, [HumanMessage('same user text')])
    assert status == 'failed'
    assert messages_to_dict(saved) == messages_to_dict(original)


@pytest.mark.parametrize('sources', [[-1], [99], [True], ['0'], [1], [0, 0]])
def test_review_invalid_sources_leave_disk_unchanged(review_identity_case, sources):
    from utils.llm_client import HumanMessage, messages_to_dict

    original = _identified_review_messages()
    corrected = [HumanMessage('edited user') for _ in sources]
    status, saved, _ = review_identity_case(original, corrected, source_indexes=sources)
    assert status == 'failed'
    assert messages_to_dict(saved) == messages_to_dict(original)


def test_review_same_role_neighbors_keep_their_own_identity(review_identity_case):
    from utils.llm_client import HumanMessage

    original = [HumanMessage(message.content, metadata=message.metadata) for message in _identified_review_messages()]
    status, saved, _ = review_identity_case(original, [HumanMessage(original[index].content) for index in (0, 2, 5)])
    assert status == 'patched'
    assert [message.metadata for message in saved] == [original[index].metadata for index in (0, 2, 5)]


def test_review_keeps_concurrent_append_and_theater_capsule(review_identity_case):
    from utils.llm_client import HumanMessage, AIMessage, SystemMessage, messages_to_dict

    original = _identified_review_messages()
    capsule = SystemMessage('theater', metadata={'source': 'theater_numeric_v2', 'memory_tier': 'episode_summary',
                                               'story_id': 'story', 'session_id': 'session'})
    appended = HumanMessage('new concurrent message', metadata={'recent_memory_id': '9' * 32})
    current = original[:2] + [capsule] + original[2:] + [appended]
    status, saved, _ = review_identity_case(original, [HumanMessage('user-A'), AIMessage('reply-A')], current=current)
    assert status == 'patched'
    assert messages_to_dict(saved) == messages_to_dict([capsule, *original[:2], appended])


def test_review_compressed_prefix_cannot_resurrect_outdated_sources(review_identity_case):
    from utils.llm_client import HumanMessage, AIMessage, SystemMessage, messages_to_dict

    original = _identified_review_messages()
    memo = SystemMessage('new compressed memo')
    current = [memo, *original[2:]]
    corrected = [HumanMessage('edited user-C'), AIMessage('edited reply-C'), HumanMessage('user-A'), AIMessage('reply-A')]
    status, saved, _ = review_identity_case(original, corrected, current=current, source_indexes=[4, 5, 0, 1])
    assert status == 'patched'
    assert isinstance(saved[0], SystemMessage) and saved[0].content == memo.content
    assert [message.content for message in saved[1:]] == ['edited user-C', 'edited reply-C']
    assert [message.metadata for message in saved[1:]] == [message.metadata for message in original[4:]]
    assert messages_to_dict(original)[0]['data']['content'] == 'user-A'


@pytest.mark.parametrize('time', [None, '1000'])
def test_review_preserves_original_proof_without_recent_projection_metadata(review_identity_case, time):
    from utils.llm_client import HumanMessage

    original = _identified_review_messages()
    for message in original:
        message.metadata = {'chat_original_source': message.metadata['chat_original_source']}
    original[0].metadata['chat_original_source']['originalTime'] = time
    status, saved, entries = review_identity_case(original, [HumanMessage('user-A')])
    assert status == 'patched'
    assert saved[0].metadata == original[0].metadata
    assert entries == [{'id': 'a' * 32, 'role': 'user', 'text': 'user-A', 'originalTime': time}]


def test_review_does_not_overwrite_a_changed_source_identity(review_identity_case):
    from copy import deepcopy
    from utils.llm_client import HumanMessage, messages_to_dict

    original = _identified_review_messages()
    current = deepcopy(original)
    current[0].metadata['recent_memory_id'] = '9' * 32
    status, saved, _ = review_identity_case(original, [HumanMessage('user-A')], current=current, source_indexes=[0])
    assert status == 'failed'
    assert messages_to_dict(saved) == messages_to_dict(current)


@pytest.mark.parametrize('source', [0, 2, None, True, '0', 99])
@pytest.mark.asyncio
async def test_review_model_response_carries_source_index_to_persistence(tmp_path, monkeypatch, source):
    from tests.unit.test_recent_file_lock import _make_manager, _write_disk, _read_disk, _ReviewLLM
    from utils.llm_client import messages_to_dict

    monkeypatch.setattr('memory.recent.assert_cloudsave_writable', lambda *args, **kwargs: None)
    manager, name, path = _make_manager(tmp_path)
    original = _identified_review_messages()
    _write_disk(path, original)
    prompts = []
    llm = _ReviewLLM([{'source_index': source, 'role': 'Master', 'content': 'edited user'}])
    invoke = llm.ainvoke

    async def capture_prompt(prompt):
        prompts.append(prompt)
        return await invoke(prompt)

    monkeypatch.setattr(llm, 'ainvoke', capture_prompt)
    monkeypatch.setattr(manager, '_get_review_llm', lambda: llm)
    status, _ = await manager.review_history(name, snapshot=original)
    saved = _read_disk(path)
    if type(source) is int and source in (0, 2):
        assert status == 'patched'
        assert saved[0].content == 'edited user'
        assert saved[0].metadata == original[source].metadata
    else:
        assert status == 'failed'
        assert messages_to_dict(saved) == messages_to_dict(original)
    assert all(f'[source_index={index}]' in prompts[0] for index in range(len(original)))
    assert 'recent_memory_id' not in prompts[0] and 'chat_original_source' not in prompts[0]


@pytest.mark.parametrize('memo_position', [0, 1, None])
@pytest.mark.asyncio
async def test_review_source_indices_follow_memo_normalization(tmp_path, monkeypatch, memo_position):
    from tests.unit.test_recent_file_lock import _make_manager, _write_disk, _read_disk, _ReviewLLM
    from utils.llm_client import SystemMessage

    monkeypatch.setattr('memory.recent.assert_cloudsave_writable', lambda *args, **kwargs: None)
    manager, name, path = _make_manager(tmp_path)
    original = [SystemMessage('memo'), *_identified_review_messages()]
    _write_disk(path, original)
    corrected = [{'source_index': 1, 'role': 'Master', 'content': 'edited user'},
                 {'source_index': 2, 'role': name, 'content': 'edited reply'}]
    if memo_position is not None:
        corrected.insert(memo_position, {'source_index': 0, 'role': 'SYSTEM_MESSAGE', 'content': 'edited memo'})
    monkeypatch.setattr(manager, '_get_review_llm', lambda: _ReviewLLM(corrected))
    status, _ = await manager.review_history(name, snapshot=original)
    saved = _read_disk(path)
    assert status == 'patched'
    assert saved[0].content == ('memo' if memo_position is None else 'edited memo')
    assert [message.metadata for message in saved[1:]] == [message.metadata for message in original[1:3]]


@pytest.mark.parametrize('locale', ['zh', 'zh-TW', 'en', 'es', 'ja', 'ko', 'pt', 'ru'])
def test_review_prompt_requires_source_indices_in_each_locale(locale):
    from config.prompts.prompts_memory import get_history_review_prompt

    prompt = get_history_review_prompt(locale) % ('Master', 'Neko', 'history', 'Master', 'Neko')
    assert '[source_index=N]' in prompt
    assert '"source_index": 0' in prompt


@pytest.mark.asyncio
async def test_review_sync_keeps_existing_ids_and_times_without_touching_archives(devices, tmp_path, monkeypatch):
    from tests.unit.test_recent_file_lock import _make_manager
    from utils.llm_client import HumanMessage, AIMessage, messages_to_dict

    monkeypatch.setattr('memory.recent.assert_cloudsave_writable', lambda *args, **kwargs: None)
    original = _identified_review_messages()
    left = devices('review-left', messages_to_dict(original))
    right = devices('review-right', [])
    server = Server()
    await left[0].sync(DOMAIN, 'Role', server.request, commit)
    await right[0].sync(DOMAIN, 'Role', server.request, commit)
    manager, name, path = _make_manager(tmp_path, lanlan_name='Role', path=str(left[1]))
    status, _, _ = manager._commit_review_locked(path, name, original,
        [HumanMessage('user-A'), AIMessage('edited reply-A')], source_indexes=[0, 1])
    assert status == 'patched'
    await left[0].sync(DOMAIN, 'Role', server.request, commit)
    await right[0].sync(DOMAIN, 'Role', server.request, commit)
    expected = [{'id': 'a' * 32, 'role': 'user', 'text': 'user-A', 'originalTime': '1000'},
                {'id': 'b' * 32, 'role': 'assistant', 'text': 'edited reply-A', 'originalTime': '1001'}]
    assert server.rows == expected
    assert right[0].adapter.read('Role')['entries'] == expected
    for _, _, archive, digest in (left, right):
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest


def test_recent_merge_orders_delayed_known_times_and_keeps_unknown_slots():
    first = {**entry('a', 'first'), 'originalTime': '1000'}
    second = {**entry('b', 'second'), 'originalTime': '2000'}
    third = {**entry('c', 'third'), 'originalTime': '3000'}
    unknown = entry('d', 'unknown')
    memo = entry(role='memo', text='memo')
    result, _ = merge([memo, first, unknown, third], [],
        {'operationId': 'e' * 32, 'upserts': [second], 'removedIds': []})
    assert result == [memo, first, unknown, second, third]


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
@pytest.mark.parametrize('failure', ['before_native', 'native_replace', 'after_replace', 'after_native', 'database'])
@pytest.mark.parametrize('local_edit', ['none', 'append', 'memo', 'clear'])
async def test_interrupted_download_recovers_before_uploading_local_changes(devices, monkeypatch, failure, local_edit):
    from services.chat_sync import recent as recent_module
    from utils.recent_file import write_recent_payload

    left = devices('interrupted-left', [row('system', 'base X')])
    right = devices('interrupted-right', [])
    server = Server()
    await left[0].sync(DOMAIN, 'Role', server.request, commit)
    await right[0].sync(DOMAIN, 'Role', server.request, commit)
    write_recent_payload(right[1], [row('system', 'remote A')])
    await right[0].sync(DOMAIN, 'Role', server.request, commit)
    original_apply = left[0].adapter.apply
    original_connect = recent_module.store_connection
    from utils import recent_file
    original_write = recent_file.atomic_write_json
    written = False

    class InterruptedProcess(BaseException):
        pass

    def interrupted_apply(*args, **kwargs):
        nonlocal written
        if failure == 'before_native':
            raise OSError('synthetic interruption before native write')
        applied = original_apply(*args, **kwargs)
        written = True
        if failure == 'after_native':
            raise OSError('synthetic interruption after native write')
        return applied

    def unavailable_database(*args):
        if written and failure == 'database':
            raise sqlite3.OperationalError('synthetic database unavailable after native write')
        return original_connect(*args)

    def interrupted_replace(path, *args, **kwargs):
        if failure == 'native_replace' and Path(path) == left[1]:
            raise OSError('synthetic interruption before atomic replacement')
        result = original_write(path, *args, **kwargs)
        if failure == 'after_replace' and Path(path) == left[1]:
            raise InterruptedProcess()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(left[0].adapter, 'apply', interrupted_apply)
        patch.setattr(recent_module, 'store_connection', unavailable_database)
        patch.setattr(recent_file, 'atomic_write_json', interrupted_replace)
        with pytest.raises((OSError, sqlite3.OperationalError, InterruptedProcess)):
            await left[0].sync(DOMAIN, 'Role', server.request, commit)
    native_text = 'base X' if failure in ('before_native', 'native_replace') else 'remote A'
    assert left[0].adapter.read('Role')['entries'][0]['text'] == native_text
    assert left[0].state(DOMAIN.key())['base'][0]['text'] == 'base X'
    if local_edit != 'none':
        payload = json.loads(left[1].read_text())
        if local_edit == 'append':
            payload.append(row('human', 'genuine local addition', recent_memory_id='e' * 32))
        elif local_edit == 'memo':
            payload[0]['data']['content'] = 'genuine local memo'
        else:
            payload = []
        write_recent_payload(left[1], payload)
    write_recent_payload(right[1], [row('system', 'newer remote B')])
    await right[0].sync(DOMAIN, 'Role', server.request, commit)
    requests_before = len(server.requests)
    adapter = RecentMemoryAdapter(left[0].adapter.config)
    restarted = RecentMemorySync(left[0].path, adapter)
    await restarted.sync(DOMAIN, 'Role', server.request, commit)
    expected = [] if local_edit == 'clear' else [entry(role='memo', text=(
        'genuine local memo' if local_edit == 'memo' else 'newer remote B'))]
    if local_edit == 'append':
        expected.append(entry('e', 'genuine local addition'))
    assert server.rows == expected
    assert restarted.adapter.read('Role')['entries'] == expected
    if local_edit == 'none':
        assert len(server.requests) == requests_before
    for _, _, archive, digest in (left, right):
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest


@pytest.mark.asyncio
async def test_download_and_separate_memory_process_preserve_both_saves(devices, monkeypatch, tmp_path):
    import subprocess
    import sys
    import time
    from memory import recent_sync as native

    sync, path, archive, digest = devices('separate-writer', [row('system', 'base memo')])
    server = Server()
    await sync.sync(DOMAIN, 'Role', server.request, commit)
    await server.request('PUT', f'/v1/chat-sync/characters/{UID}/recent-memory', canonical({
        'operationId': 'f' * 32, 'upserts': [entry(role='memo', text='remote memo')], 'removedIds': []}))
    ready, done = tmp_path / 'writer-ready', tmp_path / 'writer-done'
    child = '''
import json,sys
from pathlib import Path
from utils.recent_file import recent_file_access,read_recent_text_unlocked,write_recent_payload_unlocked
Path(sys.argv[2]).touch()
with recent_file_access(sys.argv[1]) as path:
    rows=json.loads(read_recent_text_unlocked(path))
    rows.append({'type':'human','data':{'content':'native process chat','metadata':{'recent_memory_id':'e'*32,'recent_memory_time':None}}})
    write_recent_payload_unlocked(path,rows)
Path(sys.argv[3]).touch()
'''
    original = native.write_recent_payload_unlocked
    processes = []

    def download_write(target, payload, **kwargs):
        process = subprocess.Popen([sys.executable, '-c', child, str(path), str(ready), str(done)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        processes.append(process)
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), 'separate writer did not start'
        time.sleep(0.15)
        return original(target, payload, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(native, 'write_recent_payload_unlocked', download_write)
            await sync.sync(DOMAIN, 'Role', server.request, commit)
        for process in processes:
            _, stderr = process.communicate(timeout=5)
            assert process.returncode == 0, stderr
        assert done.exists()
        expected = [entry(role='memo', text='remote memo'), entry('e', 'native process chat')]
        assert sync.adapter.read('Role')['entries'] == expected
        await sync.sync(DOMAIN, 'Role', server.request, commit)
        assert server.rows == expected
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def test_native_download_receipts_survive_later_writes_and_remain_domain_scoped(devices):
    sync, path, _, _ = devices('native-receipts', [row('system', 'old memo')])
    from utils.recent_file import write_recent_payload

    first = (DOMAIN.key(), '1' * 32)
    other = (DOMAIN.key() + '/other-owner', '2' * 32)
    local = sync.adapter.read('Role')
    assert sync.adapter.apply('Role', [entry(role='memo', text='download A')], local['fingerprint'], receipt=first)
    local = sync.adapter.read('Role')
    assert sync.adapter.apply('Role', [entry(role='memo', text='download B')], local['fingerprint'], receipt=other)
    write_recent_payload(path, [])
    adapter = RecentMemoryAdapter(sync.adapter.config)
    assert adapter.applied('Role', first) is True
    assert adapter.applied('Role', other) is True
    assert adapter.applied('Role', (first[0], '3' * 32)) is False


def test_native_write_receipts_do_not_cross_legacy_files_in_the_same_directory(tmp_path):
    from utils import recent_file

    first = tmp_path / 'recent_One.json'
    second = tmp_path / 'recent_Two.json'
    receipt = ('one', '1' * 32)
    with recent_file.recent_file_access(first) as path:
        recent_file.write_recent_payload_unlocked(path, [row('system', 'download A')], receipt=receipt)
    recent_file.write_recent_payload(second, [row('system', 'unrelated local edit')])
    with recent_file.recent_file_access(first) as path:
        assert recent_file.read_recent_write_receipt_unlocked(path, *receipt) is True


def test_native_write_receipt_moves_with_a_legacy_character_rename(tmp_path):
    from utils import recent_file
    from utils.character_memory import rename_character_memory_storage

    config = SimpleNamespace(memory_dir=tmp_path, project_memory_dir=tmp_path)
    old = tmp_path / 'recent_Old.json'
    receipt = ('stable-domain', '1' * 32)
    with recent_file.recent_file_access(old) as path:
        recent_file.write_recent_payload_unlocked(path, [row('system', 'download A')], receipt=receipt)
    rename_character_memory_storage(config, 'Old', 'New')
    adapter = RecentMemoryAdapter(config)
    assert adapter.applied('New', receipt) is True
    assert adapter.read('New')['entries'][0]['text'] == 'download A'


def test_uncertain_native_receipt_blocks_sync_but_allows_a_local_save(tmp_path):
    from utils import recent_file

    path = tmp_path / 'recent.json'
    receipt = ('domain', '1' * 32)
    with recent_file.recent_file_access(path) as resolved:
        recent_file.write_recent_payload_unlocked(resolved, [row('system', 'download A')], receipt=receipt)
    path.write_text(json.dumps([row('system', 'externally restored memory')]))
    recent_file.write_recent_payload(path, [row('system', 'normal local save')])
    assert json.loads(path.read_text())[0]['data']['content'] == 'normal local save'
    with recent_file.recent_file_access(path) as resolved:
        with pytest.raises(ValueError, match='recent_write_receipt_conflict'):
            recent_file.read_recent_write_receipt_unlocked(resolved, *receipt)


@pytest.mark.asyncio
async def test_recovered_download_keeps_shared_ids_out_of_a_later_exclusion(devices, monkeypatch):
    sync, _, _, _ = devices('excluded-after-failure', [])
    server = Server()
    server.rows = [entry('c', 'already downloaded from cloud')]
    original = sync.adapter.apply

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('synthetic database confirmation interruption')

    with monkeypatch.context() as patch:
        patch.setattr(sync.adapter, 'apply', interrupted)
        with pytest.raises(OSError):
            await sync.sync(DOMAIN, 'Role', server.request, commit)
    sync.exclude(DOMAIN.key(), ['c' * 32])
    restarted = RecentMemorySync(sync.path, RecentMemoryAdapter(sync.adapter.config))
    await restarted.sync(DOMAIN, 'Role', server.request, commit)
    assert server.rows == [entry('c', 'already downloaded from cloud')]
    assert server.requests == []
    assert restarted.state(DOMAIN.key())['excluded'] == []


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
        if method == 'GET' or endpoint.endswith('/view'):
            busy[0] = True
        return value

    result = await sync.sync(DOMAIN, 'Role', request, commit, can_apply=lambda: not busy[0])
    assert result['hasMore'] and json.loads(path.read_text()) == []

    async def late_edit(method, endpoint, body):
        value = await server.request(method, endpoint, body)
        if method == 'GET' or endpoint.endswith('/view'):
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
            if request.url.path.endswith(('/recent-memory', '/recent-memory/view')):
                self.requests.append(request)
                assert request.headers['X-Neko-Chat-Binding'] == 'c' * 32
                value = await remote.request(request.method, request.url.path, request.content or None)
                if request.method == 'GET' or request.url.path.endswith('/view'):
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


@pytest.mark.asyncio
async def test_cloud_expiry_keeps_local_rows_memo_and_archive_and_does_not_reupload(devices):
    sync, path, archive, before = devices('expiry', [row('system', '先前对话的备忘录: keep memo'), row('human', 'keep chat')])
    server = Server()
    await sync.sync(DOMAIN, 'Role', server.request, commit)
    local = sync.adapter.read('Role')['entries']
    uploaded_before = list(server.requests)
    server.rows = []
    async def expired(method, endpoint, body):
        value = await server.request(method, endpoint, body)
        if endpoint.endswith('/view'):
            value['expiredIds'] = [entry['id'] for entry in local]
        return value
    result = await sync.sync(DOMAIN, 'Role', expired, commit)
    assert not result['changed'] and not result['hasMore']
    assert sync.adapter.read('Role')['entries'] == local
    assert sync.state(DOMAIN.key())['base'] == local
    await sync.sync(DOMAIN, 'Role', expired, commit)
    assert server.requests == uploaded_before
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == before
