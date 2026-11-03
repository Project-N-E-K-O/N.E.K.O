"""Exercise the external witness against real SQLite copies and crash gaps."""

from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from services.chat_sync.writer import ChatSyncWriter, WriterQuarantined


def initialized(tmp_path):
    directory = tmp_path / 'sync'
    writer = ChatSyncWriter(directory, tmp_path / 'installation' / 'writer.json')
    with writer.initialize():
        with closing(writer.connect(directory / 'sources.sqlite3')) as conn, conn:
            conn.execute('CREATE TABLE IF NOT EXISTS originals (id TEXT PRIMARY KEY, text TEXT)')
    return writer


def save(writer, identity='1', text='raw'):
    with closing(writer.connect(writer.directory / 'sources.sqlite3')) as conn, conn:
        conn.execute('INSERT INTO originals VALUES (?,?)', (identity, text))


def backup(path, target):
    with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(target)) as copied:
        source.backup(copied)


def test_normal_restart_retains_committed_head_and_rows(tmp_path):
    writer = initialized(tmp_path)
    save(writer)
    restarted = ChatSyncWriter(writer.directory, writer.path)
    restarted.check()
    with closing(restarted.connect(writer.directory / 'sources.sqlite3')) as conn:
        assert conn.execute('SELECT text FROM originals').fetchall() == [('raw',)]


def test_clone_without_external_witness_cannot_write_or_self_bootstrap(tmp_path):
    writer = initialized(tmp_path)
    save(writer)
    clone = tmp_path / 'copy'
    clone.mkdir()
    backup(writer.directory / 'sources.sqlite3', clone / 'sources.sqlite3')
    copied = ChatSyncWriter(clone, tmp_path / 'other-install' / 'writer.json')
    with copied.initialize():
        pass
    with pytest.raises(WriterQuarantined):
        save(copied, '2')


def test_single_database_rollback_is_detected_by_next_live_operation(tmp_path):
    writer = initialized(tmp_path)
    snapshot = tmp_path / 'snapshot.sqlite3'
    backup(writer.directory / 'sources.sqlite3', snapshot)
    save(writer)
    backup(snapshot, writer.directory / 'sources.sqlite3')
    with pytest.raises(WriterQuarantined):
        save(writer, '2')


def test_pending_old_head_quarantines_instead_of_guessing_crash_before_commit(tmp_path):
    writer = initialized(tmp_path)
    state = json.loads(writer.path.read_text(encoding="utf-8"))
    state['pending'] = {'database': 'sources.sqlite3', 'old': state['heads']['sources.sqlite3'],
                        'new': {'lineage': state['lineage'], 'revision': 1, 'token': 'f' * 32}}
    writer.path.write_text(json.dumps(state))
    with pytest.raises(WriterQuarantined):
        ChatSyncWriter(writer.directory, writer.path).check()


def test_pending_new_head_is_finalized_without_replaying_business_work(tmp_path):
    writer = initialized(tmp_path)
    old_state = json.loads(writer.path.read_text(encoding="utf-8"))
    save(writer)
    state = json.loads(writer.path.read_text(encoding="utf-8"))
    new = state['heads']['sources.sqlite3']
    state['heads'] = old_state['heads']
    state['pending'] = {'database': 'sources.sqlite3', 'old': old_state['heads']['sources.sqlite3'], 'new': new}
    writer.path.write_text(json.dumps(state))
    restarted = ChatSyncWriter(writer.directory, writer.path)
    restarted.check()
    assert json.loads(writer.path.read_text(encoding="utf-8"))['pending'] is None
    with closing(restarted.connect(writer.directory / 'sources.sqlite3')) as conn:
        assert conn.execute('SELECT count(*) FROM originals').fetchone()[0] == 1


def test_exception_rolls_back_business_and_reservation_without_false_recovery(tmp_path):
    writer = initialized(tmp_path)
    with pytest.raises(RuntimeError):
        with closing(writer.connect(writer.directory / 'sources.sqlite3')) as conn, conn:
            conn.execute('INSERT INTO originals VALUES (?,?)', ('1', 'raw'))
            raise RuntimeError('abort')
    writer.check()
    save(writer)


def test_corrupt_witness_cannot_be_replaced_by_constructor(tmp_path):
    writer = initialized(tmp_path)
    writer.path.write_text('{broken')
    with pytest.raises(WriterQuarantined):
        ChatSyncWriter(writer.directory, writer.path).check()
    assert writer.path.read_text(encoding="utf-8") == '{broken'


def test_runtime_registers_all_databases_and_blocks_copied_binding(tmp_path):
    from contextlib import nullcontext
    from services.chat_sync.runtime import DesktopChatSyncRuntime
    from services.chat_sync.ledger import ChatDomain
    async def session():
        return {'logged_in': False}
    directory = tmp_path / 'runtime'
    runtime = DesktopChatSyncRuntime(directory, session, witness_path=tmp_path / 'device' / 'writer.json',
                                     session_commit_guard=lambda _: nullcontext())
    runtime.state.configure('https://sync.example')
    domain = ChatDomain('12345678-1234-4234-8234-123456789abc', 'a' * 32)
    prepared = runtime.state.prepare_binding(domain)
    runtime.state.activate(prepared, 'b' * 32)
    assert set(json.loads(runtime.writer.path.read_text(encoding="utf-8"))['heads']) == {
        'state.sqlite3', 'originals.sqlite3', 'sources.sqlite3', 'profiles.sqlite3', 'scheduler.sqlite3'}
    clone = tmp_path / 'copied'
    clone.mkdir()
    for name in json.loads(runtime.writer.path.read_text(encoding="utf-8"))['heads']:
        backup(directory / name, clone / name)
    copied = DesktopChatSyncRuntime(clone, session, witness_path=tmp_path / 'other' / 'writer.json')
    with pytest.raises(WriterQuarantined):
        copied.state.activate(prepared, 'b' * 32)


@pytest.mark.parametrize("corrupt", [None, "payload", "domain"])
def test_quarantine_stages_old_authority_and_new_source_uses_new_stream(tmp_path, corrupt):
    from services.chat_sync.runtime import DesktopChatSyncRuntime
    from services.chat_sync.source import ChatOriginalSource
    from services.chat_sync.ledger import ChatDomain, ChatOriginal
    async def session():
        return {'logged_in': False}
    directory = tmp_path / 'runtime'
    runtime = DesktopChatSyncRuntime(directory, session, witness_path=tmp_path / 'device' / 'writer.json')
    domain = ChatDomain('12345678-1234-4234-8234-123456789abc', 'a' * 32)
    prepared = runtime.state.prepare_binding(domain)
    runtime.state.activate(prepared, 'b' * 32)
    source = ChatOriginalSource(directory / 'sources.sqlite3')
    source.commit(domain.owner_id, domain.character_uid, '1' * 32, ChatOriginal('user', 'old'))
    old, locator = source.pending(200)[0]
    runtime.ledger.capture(domain, locator, ChatOriginal('user', 'old'), message_id='1' * 32,
                           proven_source_origin=(old['origin_stream_id'], old['origin_seq']))
    runtime.ledger.seal_batch(domain, byte_budget=100_000)
    runtime.ledger.queue_deletion(domain, ['2' * 32])
    if corrupt:
        with closing(runtime.ledger._connect()) as conn, conn:
            conn.execute("UPDATE chat_deletions SET " + corrupt + "=?", ("{broken",))
    runtime.writer.path.write_text('{broken')
    runtime.prepare_writer_recovery()
    assert runtime.state.binding(domain.character_uid)['enabled'] == 0
    assert runtime.ledger.stats(domain)['pendingOriginals'] == 0
    assert not runtime.ledger.pending_deletions(domain)
    assert not runtime.ledger.visible_originals(domain)
    source.commit_unbound(domain.character_uid, '3' * 32, ChatOriginal('user', 'new'))
    with sqlite3.connect(source.path) as conn:
        stream = conn.execute('SELECT origin_stream_id FROM source_unbound WHERE record_id=?', ('3' * 32,)).fetchone()[0]
        assert stream != old['origin_stream_id']
        assert conn.execute('SELECT record_id FROM source_recovery_candidates').fetchall() == [('1' * 32,)]
        assert conn.execute('SELECT message_id FROM source_recovery_delete_ids').fetchall() == ([] if corrupt else [('2' * 32,)])


def test_faulted_source_saves_raw_without_old_source_sequence(tmp_path):
    from services.chat_sync.runtime import DesktopChatSyncRuntime
    from services.chat_sync.ledger import ChatOriginal
    async def session():
        return {'logged_in': False}
    runtime = DesktopChatSyncRuntime(tmp_path / 'runtime', session, witness_path=tmp_path / 'device' / 'writer.json')
    runtime.writer.path.write_text('{broken')
    runtime.save_outage_original('a' * 32, '1' * 32, ChatOriginal('user', 'offline'))
    with sqlite3.connect(runtime.state.path.parent / 'outage.sqlite3') as conn:
        rows = conn.execute('SELECT record_id,original,origin_seq FROM outage_originals').fetchall()
    assert rows[0][0] == '1' * 32
    assert json.loads(rows[0][1])['text'] == 'offline'
    assert rows[0][2] == 1
    assert runtime.writer.path.read_text(encoding="utf-8") == '{broken'


def test_failed_sql_constraint_rolls_back_without_losing_writer_authority(tmp_path):
    writer = initialized(tmp_path)
    save(writer)
    with pytest.raises(sqlite3.IntegrityError):
        save(writer)
    writer.check()
    save(writer, '2')


@pytest.mark.asyncio
@pytest.mark.parametrize('runtime_available', [True, False])
async def test_source_primary_save_continues_when_witness_is_corrupt(tmp_path, monkeypatch, runtime_available):
    from types import SimpleNamespace
    from services.chat_sync.runtime import DesktopChatSyncRuntime
    from services.chat_sync.source import save_original
    from services.chat_sync import runtime as chat_sync_runtime
    async def session():
        return {'logged_in': False}
    runtime = DesktopChatSyncRuntime(tmp_path / 'chat_sync', session, witness_path=tmp_path / 'device' / 'writer.json')
    runtime.state.save_choice('a' * 32, True)
    runtime.writer.path.write_text('{broken')
    async def get_runtime(_manager):
        if not runtime_available:
            runtime.writer.check()
        return runtime
    async def characters():
        return {'猫娘': {'Neko': {'_reserved': {'character_uid': 'a' * 32}}}}
    monkeypatch.setattr(chat_sync_runtime, 'get_desktop_chat_sync_runtime', get_runtime)
    manager = SimpleNamespace(aload_characters=characters, app_docs_dir=tmp_path)
    identity = await save_original('Neko', 'user', 'local continues', config_manager=manager)
    assert identity is not None
    with sqlite3.connect(runtime.state.path.parent / 'outage.sqlite3') as conn:
        assert conn.execute('SELECT record_id FROM outage_originals').fetchone()[0] == identity


@pytest.mark.asyncio
@pytest.mark.parametrize('choice', ['unset', 'off', 'other_role_on'])
async def test_quarantined_source_does_not_capture_without_role_consent(tmp_path, monkeypatch, choice):
    from types import SimpleNamespace
    from services.chat_sync.runtime import DesktopChatSyncRuntime
    from services.chat_sync.source import save_original
    from services.chat_sync import runtime as chat_sync_runtime

    async def session():
        return {'logged_in': False}

    runtime = DesktopChatSyncRuntime(tmp_path / 'runtime', session, witness_path=tmp_path / 'device' / 'writer.json')
    if choice == 'off':
        runtime.state.save_choice('a' * 32, False)
    elif choice == 'other_role_on':
        runtime.state.save_choice('b' * 32, True)
    state_before = runtime.state.path.read_bytes()
    runtime.writer.path.write_text('{broken')

    async def get_runtime(_manager):
        return runtime

    monkeypatch.setattr(chat_sync_runtime, 'get_desktop_chat_sync_runtime', get_runtime)
    identity = await save_original('Neko', 'user', 'local memory only', config_manager=SimpleNamespace(),
                                   character_uid='a' * 32)
    assert identity is None
    assert not (runtime.state.path.parent / 'outage.sqlite3').exists()
    assert runtime.state.path.read_bytes() == state_before
    assert runtime.writer.path.read_text(encoding='utf-8') == '{broken'


@pytest.mark.asyncio
@pytest.mark.parametrize('paused', [False, True])
async def test_quarantined_source_retains_bound_role_local_recovery(tmp_path, monkeypatch, paused):
    from types import SimpleNamespace
    from services.chat_sync.ledger import ChatDomain
    from services.chat_sync.runtime import DesktopChatSyncRuntime
    from services.chat_sync.source import save_original
    from services.chat_sync import runtime as chat_sync_runtime

    async def session():
        return {'logged_in': False}

    runtime = DesktopChatSyncRuntime(tmp_path / 'runtime', session, witness_path=tmp_path / 'device' / 'writer.json')
    domain = ChatDomain('12345678-1234-4234-8234-123456789abc', 'a' * 32)
    prepared = runtime.state.prepare_binding(domain)
    runtime.state.activate(prepared, 'b' * 32)
    assert runtime.state.choices() == {}
    if paused:
        runtime.state.pause(domain.character_uid)
    state_before = runtime.state.path.read_bytes()
    runtime.writer.path.write_text('{broken')

    async def get_runtime(_manager):
        return runtime

    monkeypatch.setattr(chat_sync_runtime, 'get_desktop_chat_sync_runtime', get_runtime)
    identity = await save_original('Neko', 'user', 'retained locally', config_manager=SimpleNamespace(),
                                   character_uid=domain.character_uid)
    assert identity is not None
    with sqlite3.connect(runtime.state.path.parent / 'outage.sqlite3') as conn:
        row = conn.execute('SELECT uid,record_id,original FROM outage_originals').fetchone()
    assert row[:2] == (domain.character_uid, identity)
    assert json.loads(row[2])['text'] == 'retained locally'
    assert runtime.state.path.read_bytes() == state_before
    assert runtime.writer.path.read_text(encoding='utf-8') == '{broken'


@pytest.mark.asyncio
@pytest.mark.parametrize('state_condition', ['missing', 'corrupt'])
async def test_quarantined_source_fails_closed_when_saved_consent_is_unreadable(tmp_path, monkeypatch, state_condition):
    from types import SimpleNamespace
    from services.chat_sync.runtime import DesktopChatSyncRuntime
    from services.chat_sync.source import save_original
    from services.chat_sync import runtime as chat_sync_runtime

    async def session():
        return {'logged_in': False}

    runtime = DesktopChatSyncRuntime(tmp_path / 'chat_sync', session, witness_path=tmp_path / 'device' / 'writer.json')
    runtime.state.save_choice('a' * 32, True)
    runtime.writer.path.write_text('{broken')
    if state_condition == 'missing':
        runtime.state.path.rename(tmp_path / 'missing-state.sqlite3')
    else:
        runtime.state.path.write_bytes(b'not a SQLite database')

    async def get_runtime(_manager):
        runtime.writer.check()
        return runtime

    monkeypatch.setattr(chat_sync_runtime, 'get_desktop_chat_sync_runtime', get_runtime)
    identity = await save_original('Neko', 'user', 'local memory only',
                                   config_manager=SimpleNamespace(app_docs_dir=tmp_path), character_uid='a' * 32)
    assert identity is None
    assert not (runtime.state.path.parent / 'outage.sqlite3').exists()
    if state_condition == 'missing':
        assert not runtime.state.path.exists()
    else:
        assert runtime.state.path.read_bytes() == b'not a SQLite database'
    assert runtime.writer.path.read_text(encoding='utf-8') == '{broken'


@pytest.mark.asyncio
async def test_cloud_recovery_checks_copied_ack_and_requires_fresh_history_and_delete_approval(tmp_path):
    from contextlib import nullcontext
    import httpx
    from services.chat_sync.runtime import DesktopChatSyncRuntime
    from services.chat_sync.source import ChatOriginalSource, recover_sources
    from services.chat_sync.ledger import ChatDomain, ChatOriginal
    owner, uid, binding_id = '12345678-1234-4234-8234-123456789abc', 'a' * 32, 'b' * 32
    calls = []
    async def session():
        return {'logged_in': True, 'snapshot': {'access_token': 'synthetic', 'base_url': 'https://community.project-neko.cn'}}
    async def service(request):
        calls.append(request.url.path)
        body = json.loads(request.content) if request.content else None
        if request.url.path == '/v1/chat-sync/identity':
            return httpx.Response(200, json={'ownerId': owner})
        if request.url.path.endswith('/bindings'):
            return httpx.Response(200, json={'ownerId': owner, 'characterSyncId': uid, 'deviceId': body['deviceId'], 'bindingId': binding_id})
        if request.url.path.endswith('/recovery/deletions'):
            return httpx.Response(200, json={'ownerId': owner, 'characterSyncId': uid, 'through': 1, 'cursor': 1, 'hasMore': False, 'deletedMessageIds': ['4' * 32]})
        if request.url.path.endswith('/recovery/check'):
            states = {'1' * 32: 'missing', '3' * 32: 'received'}
            return httpx.Response(200, json={'ownerId': owner, 'characterSyncId': uid, 'cursor': 1,
                'results': [{'messageId': item['messageId'], 'state': states[item['messageId']]} for item in body['records']]})
        if request.url.path == '/prototype/chat-sync/characters':
            return httpx.Response(200, json={'characters': [{'characterSyncId': uid, 'displayName': 'Cloud Neko'}]})
        if request.url.path.endswith('/' + uid):
            return httpx.Response(200, json={'ownerId': owner, 'characterSyncId': uid, **body})
        raise AssertionError('Old outbox must not be sent during recovery')
    runtime = DesktopChatSyncRuntime(tmp_path / 'runtime', session, witness_path=tmp_path / 'device' / 'writer.json',
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(service)), session_commit_guard=lambda _: nullcontext())
    await runtime.configure('https://sync.test')
    await runtime.enable(uid, 'Local Neko')
    source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
    for identity in ('1' * 32, '3' * 32, '4' * 32):
        source.commit(owner, uid, identity, ChatOriginal('user', identity))
    await recover_sources(runtime)
    domain = ChatDomain(owner, uid)
    batch = runtime.ledger.seal_batch(domain, byte_budget=100_000)
    runtime.ledger.ack_batch(domain, {'batchId': batch.batch_id, 'messageIds': [m[0] for m in batch.members], 'cursor': 1})
    runtime.ledger.queue_deletion(domain, ['2' * 32])
    runtime.writer.path.write_text('{broken')
    snapshot = await runtime.recover_writer_role(uid, 'Local Neko')
    assert snapshot['history']['count'] == 1
    assert snapshot['deletions']['count'] == 1
    assert [item['messageId'] for item in await runtime.history(uid)] == ['3' * 32]
    assert runtime.ledger.stats(domain)['pendingOriginals'] == 0
    assert runtime.ledger.pending_deletions(domain) == []
    assert '/prototype/chat-sync/characters/' + uid + '/recovery/check' in calls
    await runtime.approve_history(uid, snapshot['history']['taskId'])
    await runtime._advance_history(uid)
    await recover_sources(runtime)
    assert runtime.ledger.stats(domain)['pendingOriginals'] == 1
    await runtime.approve_recovery_deletions(uid, snapshot['deletions']['taskId'])
    assert runtime.ledger.pending_deletions(domain)[0].message_ids == ('2' * 32,)
    await runtime.close()


async def recovery_budget_runtime(tmp_path, on_check, *, count=7):
    """Exercise recovery with real SQLite witnesses and a synthetic authenticated HTTP service."""
    from contextlib import nullcontext
    import httpx
    from services.chat_sync.runtime import DesktopChatSyncRuntime
    from services.chat_sync.source import ChatOriginalSource, recover_sources
    from services.chat_sync.ledger import ChatDomain, ChatOriginal
    owner, uid = '12345678-1234-4234-8234-123456789abc', 'a' * 32
    session = {'logged_in': True, 'snapshot': {'access_token': 'synthetic', 'base_url': 'https://community.project-neko.cn'}}
    checks, clock = [], [1_000_000]

    async def resolve():
        return session

    async def service(request):
        # Advance only this test clock; production admission/backpressure stays active.
        clock[0] += 10_000
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        if path == '/v1/chat-sync/identity':
            return httpx.Response(200, json={'ownerId': owner})
        if path.endswith('/bindings'):
            return httpx.Response(200, json={'ownerId': owner, 'characterSyncId': uid,
                                           'deviceId': body['deviceId'], 'bindingId': 'b' * 32})
        if path.endswith('/recovery/deletions'):
            return httpx.Response(200, json={'ownerId': owner, 'characterSyncId': uid, 'through': count,
                                           'cursor': count, 'hasMore': False, 'deletedMessageIds': []})
        if path.endswith('/recovery/check'):
            checks.append(body['records'])
            reply = await on_check(body['records'], runtime, session, len(checks))
            if isinstance(reply, httpx.Response):
                return reply
            return httpx.Response(200, json={'ownerId': owner, 'characterSyncId': uid, 'cursor': count,
                'results': [{'messageId': row['messageId'], 'state': 'received'} for row in body['records']], **(reply or {})})
        if path == '/prototype/chat-sync/characters':
            return httpx.Response(200, json={'characters': [{'characterSyncId': uid, 'displayName': 'Cloud Neko'}]})
        if path.endswith('/' + uid):
            return httpx.Response(200, json={'ownerId': owner, 'characterSyncId': uid, **body})
        raise AssertionError('Recovery must not send originals or copied deletion tasks')

    runtime = DesktopChatSyncRuntime(tmp_path / 'runtime', resolve, witness_path=tmp_path / 'device' / 'writer.json',
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(service)), session_commit_guard=lambda _: nullcontext(),
        clock=lambda: clock[0], uniform=lambda *_: 0)
    await runtime.configure('https://sync.test')
    await runtime.enable(uid, 'Local Neko')
    source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
    identities = [f'{seq:032x}' for seq in range(1, count + 1)]
    for identity in identities:
        source.commit(owner, uid, identity, ChatOriginal('user', 'synthetic recovery ' + identity))
    await recover_sources(runtime)
    runtime.writer.path.write_text('{broken')
    return runtime, ChatDomain(owner, uid), identities, checks


@pytest.mark.asyncio
async def test_recovery_body_budget_splits_exact_prefix_and_retains_the_smaller_limit(tmp_path):
    """Every accepted sub-batch is exact; a 413 never advances recovery or resets the learned size."""
    import httpx
    from services.chat_sync.recovery import pending_candidates, role_pending

    async def on_check(records, _runtime, _session, _index):
        return httpx.Response(413) if len(records) > 3 else None

    runtime, domain, identities, checks = await recovery_budget_runtime(tmp_path, on_check)
    try:
        result = await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        assert [[row['messageId'] for row in request] for request in checks] == [identities, identities[:3], identities[3:6], identities[6:]]
        assert {json.loads(raw)['messageId'] for raw in runtime.ledger.visible_originals(domain)} == set(identities)
        assert pending_candidates(runtime, domain) == [] and not role_pending(runtime, domain.character_uid)
        assert result['history']['count'] == 0 and result['deletions']['count'] == 0
        assert runtime.ledger.stats(domain)['pendingOriginals'] == 0 and runtime.ledger.cursor(domain) == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [OSError, sqlite3.OperationalError, ValueError])
async def test_recovery_returns_confirmable_deletions_when_archive_is_unavailable(tmp_path, failure):
    from unittest.mock import AsyncMock
    from services.chat_sync.recovery import role_pending
    from services.chat_sync.source import ChatOriginalSource
    runtime, domain, identities, _ = await recovery_budget_runtime(tmp_path, AsyncMock(return_value=None), count=1)
    try:
        await runtime.delete(domain.character_uid, identities)
        runtime.chat_history_provider = AsyncMock(side_effect=failure('private archive detail'))
        result = await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        assert result['history'] is None and result['historyError'] == 'chat_sync_unavailable'
        assert result['deletions']['count'] == 1
        assert result['deletions']['localName'] == 'Local Neko'
        assert result['deletions']['cloudName'] == 'Cloud Neko'
        assert not role_pending(runtime, domain.character_uid)
        assert runtime.ledger.pending_deletions(domain) == []
        source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
        assert source.task(domain.character_uid) is None
        await runtime.approve_recovery_deletions(domain.character_uid, result['deletions']['taskId'])
        assert runtime.ledger.pending_deletions(domain)[0].message_ids == tuple(identities)
        assert source.approved_tasks(domain.character_uid) == []
        runtime.chat_history_provider = AsyncMock(return_value=[])
        assert (await runtime.preview_history(domain.character_uid, 'Local Neko'))['status'] == 'preview'
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['session', 'binding', 'writer'])
async def test_partial_recovery_cannot_return_stale_authority_after_archive_failure(tmp_path, boundary):
    from unittest.mock import AsyncMock
    from services.chat_sync.recovery import role_pending
    runtime, domain, _, _ = await recovery_budget_runtime(tmp_path, AsyncMock(return_value=None), count=1)
    async def history(uid):
        if boundary == 'session':
            runtime.session_presence_provider = AsyncMock(return_value='changed-session')
        elif boundary == 'binding':
            await runtime.pause(uid)
        else:
            raise WriterQuarantined()
        raise OSError('private archive detail')
    runtime.chat_history_provider = history
    try:
        expected = {'session': 'chat_sync_session_changed', 'binding': 'chat_sync_binding_changed',
                    'writer': 'chat_sync_writer_recovery_required'}[boundary]
        with pytest.raises(ValueError, match=expected):
            await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        assert role_pending(runtime, domain.character_uid)
        assert runtime.ledger.pending_deletions(domain) == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage,failure', [('prepare', ValueError), ('prepare', OSError),
                                         ('prepare', sqlite3.OperationalError), ('metadata', ValueError)])
async def test_failed_recovery_history_preview_keeps_role_recoverable_and_exact_deletion_retry(tmp_path, monkeypatch, stage, failure):
    from unittest.mock import AsyncMock, Mock
    from services.chat_sync.recovery import role_pending
    from services.chat_sync.source import ChatOriginalSource
    runtime, domain, identities, _ = await recovery_budget_runtime(tmp_path, AsyncMock(return_value=None), count=1)
    attribute = 'prepare_history' if stage == 'prepare' else 'metadata'
    original = getattr(ChatOriginalSource, attribute)
    fault = Mock(side_effect=failure('chat_sync_history_changed'))
    monkeypatch.setattr(ChatOriginalSource, attribute, staticmethod(fault) if stage == 'metadata' else fault)
    try:
        await runtime.delete(domain.character_uid, identities)
        expected = WriterQuarantined if failure is OSError else failure
        message = 'chat_sync_writer_recovery_required' if failure is OSError else 'chat_sync_history_changed'
        with pytest.raises(expected, match=message):
            await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        with sqlite3.connect(runtime.state.path.parent / 'sources.sqlite3') as conn:
            assert conn.execute('SELECT status FROM source_recovery_roles WHERE uid=?', (domain.character_uid,)).fetchone()[0] == 'pending'
            assert conn.execute('SELECT count(*) FROM source_recovery_delete_tasks').fetchone()[0] == 0
            assert conn.execute('SELECT message_id FROM source_recovery_delete_ids').fetchall() == [(identities[0],)]
        monkeypatch.setattr(ChatOriginalSource, attribute, staticmethod(original) if stage == 'metadata' else original)
        binding, = (await runtime.status())['bindings']
        assert binding['writerRecoveryRequired']
        result = await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        assert result['deletions']['count'] == 1 and not role_pending(runtime, domain.character_uid)
        assert runtime.ledger.pending_deletions(domain) == []
        await runtime.approve_recovery_deletions(domain.character_uid, result['deletions']['taskId'])
        assert runtime.ledger.pending_deletions(domain)[0].message_ids == tuple(identities)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_session_change_after_history_preview_keeps_recovery_entry_available(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from services.chat_sync.recovery import role_pending
    from services.chat_sync.source import ChatOriginalSource
    runtime, domain, identities, _ = await recovery_budget_runtime(tmp_path, AsyncMock(return_value=None), count=1)
    original, presence = ChatOriginalSource.prepare_history, runtime.session_presence_provider
    def prepare(*args, **kwargs):
        task = original(*args, **kwargs)
        runtime.session_presence_provider = AsyncMock(return_value='changed-session')
        return task
    monkeypatch.setattr(ChatOriginalSource, 'prepare_history', prepare)
    try:
        await runtime.delete(domain.character_uid, identities)
        with pytest.raises(ValueError, match='chat_sync_session_changed'):
            await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        assert role_pending(runtime, domain.character_uid)
        with sqlite3.connect(runtime.state.path.parent / 'sources.sqlite3') as conn:
            assert conn.execute('SELECT count(*) FROM source_recovery_delete_tasks').fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM source_history_tasks WHERE status='approved'").fetchone()[0] == 0
        runtime.session_presence_provider = presence
        monkeypatch.setattr(ChatOriginalSource, 'prepare_history', original)
        assert (await runtime.status())['bindings'][0]['writerRecoveryRequired']
        result = await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        assert result['deletions']['count'] == 1
        assert runtime.ledger.pending_deletions(domain) == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('directory_failure', ['missing', 'unavailable'])
async def test_recovery_confirms_cloud_target_before_releasing_role_gate(tmp_path, monkeypatch, directory_failure):
    from unittest.mock import AsyncMock
    from services.chat_sync.recovery import role_pending
    runtime, domain, _, _ = await recovery_budget_runtime(tmp_path, AsyncMock(return_value=None), count=1)
    directory = AsyncMock(return_value={'characters': []}, side_effect=(
        ValueError('chat_sync_unavailable') if directory_failure == 'unavailable' else None))
    monkeypatch.setattr('services.chat_sync.profile_sync.read_character_directory', directory)
    try:
        expected = 'chat_sync_character_not_found' if directory_failure == 'missing' else 'chat_sync_unavailable'
        with pytest.raises(ValueError, match=expected):
            await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        assert role_pending(runtime, domain.character_uid)
        assert runtime.ledger.pending_deletions(domain) == []
        with sqlite3.connect(runtime.state.path.parent / 'sources.sqlite3') as conn:
            assert conn.execute('SELECT count(*) FROM source_recovery_delete_tasks').fetchone()[0] == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_recovery_body_budget_stops_at_a_rejected_singleton_without_finishing(tmp_path):
    """A single-record rejection is terminal; unchecked records cannot become delivery evidence."""
    import httpx
    from services.chat_sync.runtime import ChatSyncServiceError
    from services.chat_sync.recovery import pending_candidates, role_pending

    async def on_check(_records, _runtime, _session, _index):
        return httpx.Response(413)

    runtime, domain, identities, checks = await recovery_budget_runtime(tmp_path, on_check, count=3)
    try:
        with pytest.raises(ChatSyncServiceError) as error:
            await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        assert error.value.status_code == 413 and [len(request) for request in checks] == [3, 1]
        assert {row['record_id'] for row in pending_candidates(runtime, domain)} == set(identities)
        assert role_pending(runtime, domain.character_uid) and runtime.ledger.visible_originals(domain) == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [401, 403, 409, 422, 429, 'network', 'incomplete', 'reordered', 'owner', 'session', 'binding'])
async def test_recovery_body_budget_retains_exact_partial_progress_when_a_later_sub_batch_fails(tmp_path, fault):
    """Splitting must not turn auth, integrity, lifecycle or connection faults into acknowledgements."""
    import httpx
    from services.chat_sync.runtime import ChatSyncServiceError
    from services.chat_sync.recovery import pending_candidates, role_pending

    async def on_check(records, runtime, session, index):
        if index == 1:
            return httpx.Response(413)
        if index == 2:
            return None
        if isinstance(fault, int):
            return httpx.Response(fault, headers={'Retry-After': '600'} if fault == 429 else {})
        if fault == 'network':
            raise httpx.ConnectError('synthetic connection interruption')
        if fault == 'session':
            session['snapshot']['access_token'] = 'synthetic-changed'
        elif fault == 'binding':
            runtime.state.pause('a' * 32)
        elif fault == 'owner':
            return {'ownerId': '12345678-1234-4234-8234-123456789abd'}
        elif fault in ('incomplete', 'reordered'):
            selected = records[:1] if fault == 'incomplete' else list(reversed(records))
            return {'results': [{'messageId': row['messageId'], 'state': 'received'} for row in selected]}
        return None

    runtime, domain, identities, checks = await recovery_budget_runtime(tmp_path, on_check)
    try:
        with pytest.raises(ValueError) as error:
            await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        if isinstance(fault, int):
            assert isinstance(error.value, ChatSyncServiceError) and error.value.status_code == fault
        assert [len(request) for request in checks] == [7, 3, 3]
        assert {json.loads(raw)['messageId'] for raw in runtime.ledger.visible_originals(domain)} == set(identities[:3])
        assert {row['record_id'] for row in pending_candidates(runtime, domain)} == set(identities[3:])
        assert role_pending(runtime, domain.character_uid)
        if fault == 429:
            assert not runtime.scheduler.origin_ready()
            with pytest.raises(ValueError, match='chat_sync_retry_later'):
                await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
            assert len(checks) == 3
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [400, 401, 403, 409, 422, 429, 500])
async def test_recovery_body_budget_does_not_split_other_http_errors(tmp_path, status):
    """Only a multi-record 413 authorizes another smaller request."""
    import httpx
    from services.chat_sync.runtime import ChatSyncServiceError
    from services.chat_sync.recovery import pending_candidates, role_pending

    async def on_check(_records, _runtime, _session, _index):
        return httpx.Response(status)

    runtime, domain, identities, checks = await recovery_budget_runtime(tmp_path, on_check, count=3)
    try:
        with pytest.raises(ChatSyncServiceError) as error:
            await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        assert error.value.status_code == status and [len(request) for request in checks] == [3]
        assert {row['record_id'] for row in pending_candidates(runtime, domain)} == set(identities)
        assert role_pending(runtime, domain.character_uid) and runtime.ledger.visible_originals(domain) == []
    finally:
        await runtime.close()


def _process_save(directory, witness, identity, crash):
    import os
    writer = ChatSyncWriter(Path(directory), Path(witness))
    original_write = writer._write
    if crash == 'before-reserve':
        os._exit(17)
    if crash == 'after-db-commit':
        def finish(state):
            if state['pending'] is None:
                os._exit(17)
            return original_write(state)
        writer._write = finish
    with closing(writer.connect(Path(directory) / 'sources.sqlite3')) as conn, conn:
        conn.execute('INSERT INTO originals VALUES (?,?)', (identity, 'process raw'))
        if crash == 'after-reserve':
            os._exit(17)


@pytest.mark.parametrize('crash,quarantined,count', [
    ('before-reserve', False, 0), ('after-reserve', True, 0), ('after-db-commit', False, 1),
])
def test_real_process_crash_matrix_preserves_conservative_authority(tmp_path, crash, quarantined, count):
    import multiprocessing
    writer = initialized(tmp_path)
    process = multiprocessing.get_context('spawn').Process(target=_process_save, args=(str(writer.directory), str(writer.path), '1', crash))
    process.start()
    process.join(10)
    assert process.exitcode == 17
    restarted = ChatSyncWriter(writer.directory, writer.path)
    if quarantined:
        with pytest.raises(WriterQuarantined):
            restarted.check()
    else:
        restarted.check()
    with closing(sqlite3.connect(writer.directory / 'sources.sqlite3')) as conn:
        assert conn.execute('SELECT count(*) FROM originals').fetchone()[0] == count


def test_two_processes_serialize_reservation_and_commit_without_lost_heads(tmp_path):
    import multiprocessing
    writer = initialized(tmp_path)
    processes = [multiprocessing.get_context('spawn').Process(target=_process_save, args=(str(writer.directory), str(writer.path), str(i), None)) for i in (1, 2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    writer.check()
    assert json.loads(writer.path.read_text(encoding="utf-8"))['heads']['sources.sqlite3']['revision'] == 2
    with closing(sqlite3.connect(writer.directory / 'sources.sqlite3')) as conn:
        assert conn.execute('SELECT id FROM originals ORDER BY id').fetchall() == [('1',), ('2',)]


@pytest.mark.parametrize('database', ['sources.sqlite3', 'state.sqlite3', 'originals.sqlite3', 'profiles.sqlite3', 'scheduler.sqlite3'])
def test_each_runtime_database_rollback_blocks_other_writers(tmp_path, database):
    from services.chat_sync.runtime import DesktopChatSyncRuntime
    async def session():
        return {'logged_in': False}
    runtime = DesktopChatSyncRuntime(tmp_path / 'runtime', session, witness_path=tmp_path / 'installation' / 'writer.json')
    snapshot = tmp_path / 'snapshot.sqlite3'
    backup(runtime.state.path.parent / database, snapshot)
    # A committed metadata mutation is sufficient to represent any protected
    # business write; rollback of this file must also fence an unrelated store.
    with closing(runtime.writer.connect(runtime.state.path.parent / database)) as conn, conn:
        conn.execute('CREATE TABLE rolled_back_business_state (value TEXT)')
        conn.execute("INSERT INTO rolled_back_business_state VALUES ('new')")
    backup(snapshot, runtime.state.path.parent / database)
    with pytest.raises(WriterQuarantined):
        runtime.state.configure('https://sync.test')


def test_witness_reservation_write_failure_keeps_database_unchanged(tmp_path, monkeypatch):
    writer = initialized(tmp_path)
    real_replace = __import__('os').replace
    def unwritable(source, target):
        if Path(target) == writer.path:
            raise PermissionError('synthetic unwritable witness')
        return real_replace(source, target)
    monkeypatch.setattr('services.chat_sync.writer.os.replace', unwritable)
    with pytest.raises(WriterQuarantined):
        save(writer)
    with closing(sqlite3.connect(writer.directory / 'sources.sqlite3')) as conn:
        assert conn.execute('SELECT count(*) FROM originals').fetchone()[0] == 0


def test_invalid_database_head_uses_fixed_quarantine_error(tmp_path):
    writer = initialized(tmp_path)
    with sqlite3.connect(writer.directory / 'sources.sqlite3') as conn:
        conn.execute("UPDATE chat_sync_writer_head SET token='broken'")
    with pytest.raises(WriterQuarantined):
        writer.check()


def deletion_recovery_task(tmp_path, count):
    from services.chat_sync.runtime import DesktopChatSyncRuntime
    from services.chat_sync.ledger import ChatDomain
    from services.chat_sync.recovery import finish_role
    from services.chat_sync.writer import store_connection
    async def session():
        return {'logged_in': False}
    runtime = DesktopChatSyncRuntime(tmp_path / 'runtime', session, witness_path=tmp_path / 'device' / 'writer.json')
    domain = ChatDomain('12345678-1234-4234-8234-123456789abc', 'a' * 32)
    binding = runtime.state.activate(runtime.state.prepare_binding(domain), 'b' * 32)
    with closing(store_connection(runtime.state.path.parent / 'sources.sqlite3')) as source, source:
        source.executemany('INSERT INTO source_recovery_delete_ids VALUES (?,?,?)', [(domain.owner_id, domain.character_uid, f'{i:032x}') for i in range(1, count + 1)])
    snapshot = finish_role(runtime, domain, binding, 'session')
    return runtime, domain, binding, snapshot


def test_recovery_keeps_unverifiable_donor_bytes_hidden(tmp_path):
    from services.chat_sync.writer import store_connection
    from services.chat_sync.recovery import pending_candidates
    runtime, domain, _, _ = deletion_recovery_task(tmp_path, 0)
    damaged = b'{"synthetic-unknown-original":true}'
    with closing(store_connection(runtime.ledger.path)) as conn, conn:
        conn.execute('INSERT INTO chat_downloaded VALUES (?,?,?)', (domain.key(), '1' * 32, damaged))
    runtime.prepare_writer_recovery()
    with sqlite3.connect(runtime.state.path.parent / 'sources.sqlite3') as conn:
        assert conn.execute('SELECT payload,original,origin_stream_id FROM source_recovery_candidates').fetchall() == [(damaged, None, None)]
    assert pending_candidates(runtime, domain) == []
    assert runtime.ledger.visible_originals(domain) == []


def test_recovery_keeps_same_message_id_in_distinct_domains(tmp_path):
    from services.chat_sync.ledger import ChatDomain
    from services.chat_sync.protocol import encode_chat_original
    from services.chat_sync.recovery import apply_cloud_deletions, apply_checked_candidates, pending_candidates
    from services.chat_sync.writer import store_connection
    runtime, first, _, _ = deletion_recovery_task(tmp_path, 0)
    domains = (first, ChatDomain(first.owner_id, 'b' * 32),
               ChatDomain('12345678-1234-4234-8234-123456789abd', first.character_uid))
    with closing(store_connection(runtime.ledger.path)) as conn, conn:
        for domain in domains:
            payload = encode_chat_original({'schemaVersion': 1, 'ownerId': domain.owner_id,
                'characterSyncId': domain.character_uid, 'channel': 'chat', 'scope': 'private-chat',
                'messageId': '1' * 32, 'originStreamId': '2' * 32, 'originSeq': 1, 'role': 'user',
                'text': domain.owner_id + domain.character_uid, 'originalTime': None, 'timezone': None,
                'conversationId': None, 'replyToMessageId': None})
            conn.execute('INSERT INTO chat_downloaded VALUES (?,?,?)', (domain.key(), '1' * 32, payload))
    for domain in domains:
        runtime.ledger.queue_deletion(domain, ['4' * 32])
    runtime.prepare_writer_recovery()
    for domain in domains:
        candidates = pending_candidates(runtime, domain)
        assert len(candidates) == 1
        assert candidates[0]['owner'] == domain.owner_id and candidates[0]['uid'] == domain.character_uid
    with sqlite3.connect(runtime.state.path.parent / 'sources.sqlite3') as conn:
        assert conn.execute('SELECT count(*) FROM source_recovery_delete_ids').fetchone()[0] == 3
    candidates = pending_candidates(runtime, first)
    apply_checked_candidates(runtime, first, candidates, [{'messageId': '1' * 32, 'state': 'received'}])
    assert pending_candidates(runtime, first) == []
    assert all(len(pending_candidates(runtime, domain)) == 1 for domain in domains[1:])
    apply_cloud_deletions(runtime, first, ['1' * 32, '4' * 32])
    assert all(len(pending_candidates(runtime, domain)) == 1 for domain in domains[1:])
    with sqlite3.connect(runtime.state.path.parent / 'sources.sqlite3') as conn:
        assert conn.execute('SELECT count(*) FROM source_recovery_delete_ids').fetchone()[0] == 2


@pytest.mark.asyncio
async def test_repeated_outage_recovery_coalesces_identical_bound_and_unbound_proof(tmp_path):
    import hashlib
    from services.chat_sync.ledger import ChatOriginal
    from services.chat_sync.prototype import PrototypeChatSyncClient
    from services.chat_sync.recovery import apply_checked_candidates, finish_role, pending_candidates
    from services.chat_sync.source import ChatOriginalSource
    runtime, domain, _, _ = deletion_recovery_task(tmp_path, 0)
    identity = '1' * 32
    runtime.save_outage_original(domain.character_uid, identity, ChatOriginal('user', 'outage'))
    runtime.prepare_writer_recovery()
    binding = runtime.state.activate(runtime.state.prepare_binding(domain), 'b' * 32)
    candidates = pending_candidates(runtime, domain)
    apply_checked_candidates(runtime, domain, candidates, [{'messageId': identity, 'state': 'missing'}])
    finish_role(runtime, domain, binding, 'session')
    source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
    task = source.prepare_history(binding, 'https://sync.test', 'session', 'Local', 'Cloud', device_id='c' * 32)
    task = source.approve_history(domain.character_uid, task['task_id'])
    assert source.advance_history(task, binding) == 1
    row, locator = source.pending(200)[0]
    runtime.ledger.capture(domain, locator, ChatOriginal('user', 'outage'), message_id=identity,
                           proven_source_origin=(row['origin_stream_id'], row['origin_seq']))
    source.captured(identity)
    runtime.prepare_writer_recovery()
    candidates = pending_candidates(runtime, domain)
    assert len(candidates) == 1
    requests = []
    async def request(method, path, payload):
        requests.append(json.loads(payload))
        return {'ownerId': domain.owner_id, 'characterSyncId': domain.character_uid, 'cursor': 0,
                'results': [{'messageId': identity, 'state': 'received'}]}
    client = PrototypeChatSyncClient(runtime.ledger, domain, request)
    checked = await client.check_recovery([{'messageId': row['record_id'], 'sha256': hashlib.sha256(row['payload']).hexdigest()} for row in candidates])
    assert len(requests) == 1 and len(requests[0]['records']) == 1
    apply_checked_candidates(runtime, domain, candidates, checked['results'])
    assert pending_candidates(runtime, domain) == []
    with sqlite3.connect(source.path) as conn:
        assert conn.execute('SELECT owner,status FROM source_recovery_candidates ORDER BY owner').fetchall() == [(None, 'received'), (domain.owner_id, 'received')]


def test_recovery_conflicting_null_and_bound_proof_stays_hidden(tmp_path):
    from dataclasses import asdict
    import hashlib
    from services.chat_sync.ledger import ChatOriginal
    from services.chat_sync.recovery import pending_candidates
    from services.chat_sync.writer import store_connection
    runtime, domain, _, _ = deletion_recovery_task(tmp_path, 0)
    runtime.save_outage_original(domain.character_uid, '1' * 32, ChatOriginal('user', 'outage'))
    runtime.prepare_writer_recovery()
    raw = json.dumps(asdict(ChatOriginal('user', 'different')), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    with closing(store_connection(runtime.state.path.parent / 'sources.sqlite3')) as conn, conn:
        conn.execute('INSERT INTO source_recovery_candidates '
                     '(record_id,owner,uid,original,original_hash,origin_stream_id,origin_seq,kind) '
                     "VALUES (?,?,?,?,?,?,1,'local')", ('1' * 32, domain.owner_id, domain.character_uid, raw,
                     hashlib.sha256(raw.encode()).hexdigest(), '2' * 32))
    assert pending_candidates(runtime, domain) == []
    with sqlite3.connect(runtime.state.path.parent / 'sources.sqlite3') as conn:
        assert conn.execute('SELECT count(*) FROM source_recovery_candidates WHERE status=\'unchecked\'').fetchone()[0] == 2


@pytest.mark.asyncio
async def test_recovery_deletion_preview_coalesces_outage_and_bound_marker_ids(tmp_path):
    from services.chat_sync.recovery import finish_role
    runtime, domain, _, _ = deletion_recovery_task(tmp_path, 0)
    runtime.ledger.queue_deletion(domain, ['1' * 32])
    runtime.writer.path.write_text('{broken')
    await runtime.delete(domain.character_uid, ['1' * 32])
    runtime.prepare_writer_recovery()
    binding = runtime.state.activate(runtime.state.prepare_binding(domain), 'b' * 32)
    snapshot = finish_role(runtime, domain, binding, 'session')
    assert snapshot['count'] == 1
    with sqlite3.connect(runtime.state.path.parent / 'sources.sqlite3') as conn:
        assert conn.execute('SELECT message_id FROM source_recovery_delete_members WHERE task_id=?', (snapshot['taskId'],)).fetchall() == [('1' * 32,)]


def test_approved_recovery_deletion_pages_resume_and_ack_gap_does_not_recreate_request(tmp_path, monkeypatch):
    from services.chat_sync.recovery import approve_deletions
    from services.chat_sync.writer import _WriterConnection
    runtime, domain, binding, snapshot = deletion_recovery_task(tmp_path, 403)
    real_execute = _WriterConnection.executemany
    def fail_progress(self, sql, parameters=()):
        if sql.startswith('UPDATE source_recovery_delete_members SET registered'):
            raise RuntimeError('synthetic gap after ledger commit')
        return real_execute(self, sql, parameters)
    monkeypatch.setattr(_WriterConnection, 'executemany', fail_progress)
    with pytest.raises(RuntimeError):
        approve_deletions(runtime, domain.character_uid, snapshot['taskId'], binding, 'session')
    first = runtime.ledger.pending_deletions(domain)[0]
    runtime.ledger.ack_deletion(domain, {'deletionId': first.deletion_id, 'messageIds': list(first.message_ids), 'cursor': 1})
    monkeypatch.setattr(_WriterConnection, 'executemany', real_execute)
    first_progress = approve_deletions(runtime, domain.character_uid, snapshot['taskId'], binding, 'session')
    assert first_progress['remaining'] == 203
    assert runtime.ledger.pending_deletions(domain) == []
    second = approve_deletions(runtime, domain.character_uid, snapshot['taskId'], binding, 'session')
    third = approve_deletions(runtime, domain.character_uid, snapshot['taskId'], binding, 'session')
    assert second['remaining'] == 3
    assert third['remaining'] == 0
    assert [len(item.message_ids) for item in runtime.ledger.pending_deletions(domain)] == [200, 3]


@pytest.mark.asyncio
async def test_faulted_delete_keeps_exact_local_hide_for_later_fresh_approval(tmp_path):
    from services.chat_sync.ledger import ChatOriginal
    runtime, domain, binding, _ = deletion_recovery_task(tmp_path, 0)
    runtime.save_outage_original(domain.character_uid, '1' * 32, ChatOriginal('user', 'raw outage'))
    runtime.writer.path.write_text('{broken')
    receipt = await runtime.delete(domain.character_uid, ['1' * 32])
    assert receipt == {'deletionIds': [], 'messageIds': ['1' * 32], 'pending': False, 'recoveryRequired': True}
    with sqlite3.connect(runtime.state.path.parent / 'outage.sqlite3') as conn:
        assert conn.execute('SELECT original,deleted FROM outage_originals').fetchone() == (None, 1)
    runtime.prepare_writer_recovery()
    with sqlite3.connect(runtime.state.path.parent / 'sources.sqlite3') as conn:
        assert conn.execute('SELECT message_id FROM source_recovery_delete_ids').fetchone()[0] == '1' * 32
    assert runtime.ledger.pending_deletions(domain) == []


@pytest.mark.asyncio
async def test_faulted_clear_hides_all_known_local_ids_without_authorizing_cloud(tmp_path):
    from services.chat_sync.ledger import ChatOriginal
    runtime, domain, binding, _ = deletion_recovery_task(tmp_path, 0)
    for identity in ('1' * 32, '2' * 32):
        runtime.save_outage_original(domain.character_uid, identity, ChatOriginal('user', 'raw outage'))
    runtime.writer.path.write_text('{broken')
    receipt = await runtime.clear(domain.character_uid)
    assert set(receipt['messageIds']) == {'1' * 32, '2' * 32}
    assert receipt['pending'] is False
    with sqlite3.connect(runtime.state.path.parent / 'outage.sqlite3') as conn:
        assert conn.execute('SELECT count(*) FROM outage_originals WHERE original IS NOT NULL').fetchone()[0] == 0


@pytest.mark.asyncio
async def test_same_process_witness_io_preserves_local_history_but_restart_hides_unproven_copy(tmp_path):
    from services.chat_sync.runtime import DesktopChatSyncRuntime
    from services.chat_sync.ledger import ChatOriginal, SourceLocator
    runtime, domain, _, _ = deletion_recovery_task(tmp_path, 0)
    record = runtime.ledger.capture(domain, SourceLocator('raw', 'generation', '1'), ChatOriginal('user', 'retained locally'))
    assert (await runtime.history(domain.character_uid))[0]['messageId'] == record['messageId']
    runtime.writer.path.write_text('{broken')
    assert (await runtime.history(domain.character_uid))[0]['messageId'] == record['messageId']
    async def session():
        return {'logged_in': False}
    restarted = DesktopChatSyncRuntime(runtime.state.path.parent, session, witness_path=runtime.writer.path)
    assert await restarted.history(domain.character_uid) == []
    with sqlite3.connect(runtime.ledger.path) as conn:
        assert conn.execute('SELECT count(*) FROM chat_originals').fetchone()[0] == 1


@pytest.mark.asyncio
async def test_witness_temp_creation_failure_is_fixed_and_raw_save_continues(tmp_path, monkeypatch):
    from services.chat_sync.source import save_original
    from types import SimpleNamespace
    from services.chat_sync import runtime as chat_sync_runtime
    runtime, domain, _, _ = deletion_recovery_task(tmp_path, 0)
    real_open = __import__('os').open
    def cannot_create(path, *args, **kwargs):
        if str(path).startswith(str(runtime.writer.path)) and str(path).endswith('.tmp'):
            raise PermissionError('synthetic temp creation failure')
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr('services.chat_sync.writer.os.open', cannot_create)
    async def get_runtime(_):
        return runtime
    async def characters():
        return {'猫娘': {'Neko': {'_reserved': {'character_uid': domain.character_uid}}}}
    monkeypatch.setattr(chat_sync_runtime, 'get_desktop_chat_sync_runtime', get_runtime)
    identity = await save_original('Neko', 'user', 'raw', config_manager=SimpleNamespace(aload_characters=characters))
    assert identity is not None
    with sqlite3.connect(runtime.state.path.parent / 'outage.sqlite3') as conn:
        assert conn.execute('SELECT record_id FROM outage_originals').fetchone()[0] == identity


def test_staging_failure_retains_donor_deletion_until_candidate_commit(tmp_path, monkeypatch):
    from services.chat_sync.writer import _WriterConnection
    runtime, domain, binding, _ = deletion_recovery_task(tmp_path, 0)
    original = runtime.ledger.queue_deletion(domain, ['1' * 32])
    runtime.writer.path.write_text('{broken')
    real_execute = _WriterConnection.execute
    def fail_staging(self, sql, parameters=()):
        if 'SELECT record_id,owner,uid,original' in sql:
            raise RuntimeError('synthetic source staging interruption')
        return real_execute(self, sql, parameters)
    monkeypatch.setattr(_WriterConnection, 'execute', fail_staging)
    with pytest.raises(RuntimeError):
        runtime.prepare_writer_recovery()
    with sqlite3.connect(runtime.ledger.path) as conn:
        assert conn.execute('SELECT deletion_id FROM chat_deletions').fetchone()[0] == original.deletion_id
    monkeypatch.setattr(_WriterConnection, 'execute', real_execute)
    runtime.prepare_writer_recovery()
    with sqlite3.connect(runtime.state.path.parent / 'sources.sqlite3') as conn:
        assert conn.execute('SELECT message_id FROM source_recovery_delete_ids').fetchone()[0] == '1' * 32
    assert runtime.ledger.pending_deletions(domain) == []
