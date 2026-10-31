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


def queued_deletions(ledger, domain):
    """Read fixed marker requests straight from the ledger outbox."""
    with closing(sqlite3.connect(ledger.path)) as conn:
        return [(deletion_id, tuple(json.loads(payload)['messageIds'])) for deletion_id, payload in conn.execute(
            'SELECT deletion_id, payload FROM chat_deletions WHERE domain=? ORDER BY rowid', (domain.key(),))]


def staged_deletion_ids(runtime, domain):
    """Read copied deletion IDs that recovery keeps staged until the role finishes."""
    with closing(sqlite3.connect(runtime.state.path.parent / 'sources.sqlite3')) as conn:
        return [row[0] for row in conn.execute(
            'SELECT message_id FROM source_recovery_delete_ids WHERE uid=? ORDER BY rowid', (domain.character_uid,))]


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


@pytest.mark.asyncio
async def test_witness_failure_keeps_local_replies_only_for_heads_this_process_committed(tmp_path):
    """After witness I/O fails, local replies stay visible only while every file still has this process's head."""
    from services.chat_sync.ledger import ChatOriginal
    from services.chat_sync.runtime import DesktopChatSyncRuntime
    from services.chat_sync.source import ChatOriginalSource
    from tests.unit.test_chat_sync_source import legacy_local_reply
    async def session():
        return {'logged_in': False}
    uid = 'a' * 32
    runtime = DesktopChatSyncRuntime(tmp_path / 'runtime', session, witness_path=tmp_path / 'device' / 'writer.json')
    source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
    legacy_local_reply(source, uid, '5' * 32, ChatOriginal('assistant', 'retained locally'))
    snapshot = tmp_path / 'snapshot.sqlite3'
    backup(source.path, snapshot)
    # This process commits a new head, then loses the witness that records it.
    source.commit_unbound(uid, '6' * 32, ChatOriginal('user', 'later'))
    runtime.writer.path.write_text('{broken')
    assert [reply['messageId'] for reply in await runtime.local_replies(uid)] == ['5' * 32]
    # A new process never observed those heads.
    restarted = DesktopChatSyncRuntime(runtime.state.path.parent, session, witness_path=runtime.writer.path)
    assert await restarted.local_replies(uid) == []
    # A file restored behind this process's own commit is not proven either.
    backup(snapshot, source.path)
    assert await runtime.local_replies(uid) == []


@pytest.mark.parametrize("corrupt", [None, "payload", "domain"])
def test_quarantine_stages_old_authority_and_new_source_uses_new_stream(tmp_path, corrupt):
    from services.chat_sync.long_term import LongTermMemorySync
    from services.chat_sync.runtime import DesktopChatSyncRuntime
    from services.chat_sync.source import ChatOriginalSource
    from services.chat_sync.ledger import ChatDomain, ChatOriginal
    from tests.chat_sync_legacy_ledger import insert_legacy_original
    async def session():
        return {'logged_in': False}
    directory = tmp_path / 'runtime'
    runtime = DesktopChatSyncRuntime(directory, session, witness_path=tmp_path / 'device' / 'writer.json')
    domain = ChatDomain('12345678-1234-4234-8234-123456789abc', 'a' * 32)
    prepared = runtime.state.prepare_binding(domain)
    runtime.state.activate(prepared, 'b' * 32)
    source = ChatOriginalSource(directory / 'sources.sqlite3')
    source.commit(domain.owner_id, domain.character_uid, '1' * 32, ChatOriginal('user', 'old'))
    with closing(sqlite3.connect(source.path)) as conn:
        conn.row_factory = sqlite3.Row
        old = conn.execute('SELECT * FROM source_originals WHERE record_id=?', ('1' * 32,)).fetchone()
    # An original that an earlier release copied from the source into the ledger.
    insert_legacy_original(runtime.ledger.path, domain, ChatOriginal('user', 'old'), ('db', 'generation', '1'),
                           message_id='1' * 32, origin=(old['origin_stream_id'], old['origin_seq']))
    runtime.ledger.queue_deletion(domain, ['2' * 32])
    with closing(runtime.ledger._connect()) as conn, conn:
        # A batch left by an earlier upload lane is copied authority as well.
        conn.execute("INSERT INTO chat_batches VALUES (?,?,?,?)", (domain.key(), 'c' * 32, b'{}', '0' * 64))
        if corrupt:
            conn.execute("UPDATE chat_deletions SET " + corrupt + "=?", ("{broken",))
    # Long-term ACKs, retry deadlines and the device identity are copied authority too.
    LongTermMemorySync(runtime.state.path, None).acknowledge(domain.key(), 'op-1', revision=3)
    runtime.scheduler.failure(domain.key(), 'long_term', runtime.state.binding(domain.character_uid)['generation'],
                              'session', OSError())
    device = runtime.state.settings()['device_id']
    runtime.writer.path.write_text('{broken')
    runtime.prepare_writer_recovery()
    assert runtime.state.binding(domain.character_uid)['enabled'] == 0
    with closing(sqlite3.connect(runtime.state.path)) as conn:
        assert conn.execute('SELECT count(*) FROM long_term_memory_state').fetchone() == (0,)
    with closing(sqlite3.connect(runtime.scheduler.path)) as conn:
        assert conn.execute('SELECT count(*) FROM scheduler_tasks').fetchone() == (0,)
    assert runtime.state.settings()['device_id'] != device
    assert queued_deletions(runtime.ledger, domain) == []
    with closing(sqlite3.connect(runtime.ledger.path)) as conn:
        assert conn.execute('SELECT count(*) FROM chat_batches').fetchone() == (0,)
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
    from services.chat_sync.recovery import save_outage
    async def session():
        return {'logged_in': False}
    runtime = DesktopChatSyncRuntime(tmp_path / 'runtime', session, witness_path=tmp_path / 'device' / 'writer.json')
    runtime.writer.path.write_text('{broken')
    save_outage(runtime.state.path.parent, 'a' * 32, '1' * 32, ChatOriginal('user', 'offline'))
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
async def test_cloud_recovery_checks_copied_ack_and_keeps_unsent_deletions_local(tmp_path):
    from contextlib import nullcontext
    import httpx
    from services.chat_sync.runtime import DesktopChatSyncRuntime
    from services.chat_sync.source import ChatOriginalSource
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
            # Another cloud role listed first does not hide the target role.
            return httpx.Response(200, json={'characters': [{'characterSyncId': 'c' * 32, 'displayName': 'Other Neko'},
                                                            {'characterSyncId': uid, 'displayName': 'Cloud Neko'}]})
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
    domain = ChatDomain(owner, uid)
    runtime.ledger.queue_deletion(domain, ['2' * 32])
    runtime.writer.path.write_text('{broken')
    snapshot = await runtime.recover_writer_role(uid, 'Local Neko')
    assert snapshot == {'deletions': {'count': 1}}
    assert {json.loads(raw)['messageId'] for raw in runtime.ledger.visible_originals(domain)} == {'3' * 32}
    # The unsent marker stays a local tombstone and is never queued for the cloud.
    assert queued_deletions(runtime.ledger, domain) == []
    assert '2' * 32 in runtime.ledger.deleted_message_ids(domain)
    assert staged_deletion_ids(runtime, domain) == []
    assert '/prototype/chat-sync/characters/' + uid + '/recovery/check' in calls
    await runtime.close()


async def recovery_budget_runtime(tmp_path, on_check, *, count=7, copied_deletions=(), cloud_deletions=(), prepare=None):
    """Exercise recovery with real SQLite witnesses and a synthetic authenticated HTTP service.

    The deletion page reads ``cloud_deletions`` on every request, so a caller-owned
    list can report a cloud deletion made between two recovery attempts.
    ``prepare(runtime, domain)`` adds local state while the writer is still healthy.
    """
    from contextlib import nullcontext
    import httpx
    from services.chat_sync.runtime import DesktopChatSyncRuntime
    from services.chat_sync.source import ChatOriginalSource
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
                                           'cursor': count, 'hasMore': False, 'deletedMessageIds': list(cloud_deletions)})
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
    if copied_deletions:
        # A marker left in the old deletion outbox is copied authority as well.
        runtime.ledger.queue_deletion(ChatDomain(owner, uid), list(copied_deletions))
    if prepare is not None:
        prepare(runtime, ChatDomain(owner, uid))
    runtime.writer.path.write_text('{broken')
    return runtime, ChatDomain(owner, uid), identities, checks


async def role_recovery_required(runtime):
    """Read the per-role retry entry the UI shows once the writer itself is healthy again."""
    status = await runtime.status()
    assert status['writerRecoveryRequired'] is False
    binding, = status['bindings']
    return binding['writerRecoveryRequired']


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
        assert result['deletions']['count'] == 0
        assert await role_recovery_required(runtime) is False
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['session', 'binding', 'writer'])
async def test_partial_recovery_cannot_return_stale_authority_after_cloud_name_lookup(tmp_path, monkeypatch, boundary):
    from unittest.mock import AsyncMock
    from services.chat_sync.recovery import role_pending
    runtime, domain, _, _ = await recovery_budget_runtime(tmp_path, AsyncMock(return_value=None), count=1,
                                                          copied_deletions=['f' * 32])
    async def directory(_request, _owner):
        if boundary == 'session':
            runtime.session_presence_provider = AsyncMock(return_value='changed-session')
        elif boundary == 'binding':
            await runtime.pause(domain.character_uid)
        else:
            raise WriterQuarantined()
        return {'characters': [{'characterSyncId': domain.character_uid, 'displayName': 'Cloud Neko'}]}
    monkeypatch.setattr('services.chat_sync.profile_sync.read_character_directory', directory)
    try:
        expected = {'session': 'chat_sync_session_changed', 'binding': 'chat_sync_binding_changed',
                    'writer': 'chat_sync_writer_recovery_required'}[boundary]
        with pytest.raises(ValueError, match=expected):
            await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        assert role_pending(runtime, domain.character_uid)
        if boundary != 'writer':
            # Recovery already repaired the writer, so only this role keeps its retry entry.
            assert await role_recovery_required(runtime) is True
        # The copied marker stays staged and never becomes sendable.
        assert queued_deletions(runtime.ledger, domain) == []
        assert staged_deletion_ids(runtime, domain) == ['f' * 32]
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('directory_failure', ['missing', 'unavailable'])
async def test_recovery_confirms_cloud_target_before_releasing_role_gate(tmp_path, monkeypatch, directory_failure):
    from unittest.mock import AsyncMock
    from services.chat_sync.recovery import role_pending
    runtime, domain, _, _ = await recovery_budget_runtime(tmp_path, AsyncMock(return_value=None), count=1,
                                                          copied_deletions=['f' * 32])
    directory = AsyncMock(return_value={'characters': []}, side_effect=(
        ValueError('chat_sync_unavailable') if directory_failure == 'unavailable' else None))
    monkeypatch.setattr('services.chat_sync.profile_sync.read_character_directory', directory)
    try:
        expected = 'chat_sync_character_not_found' if directory_failure == 'missing' else 'chat_sync_unavailable'
        with pytest.raises(ValueError, match=expected):
            await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        assert role_pending(runtime, domain.character_uid)
        # Recovery already repaired the writer, so only this role keeps its retry entry.
        assert await role_recovery_required(runtime) is True
        # The copied marker stays staged and never becomes sendable.
        assert queued_deletions(runtime.ledger, domain) == []
        assert staged_deletion_ids(runtime, domain) == ['f' * 32]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_recovery_retry_reclaims_a_download_the_cloud_deleted_meanwhile(tmp_path, monkeypatch):
    """A body recovered as delivered keeps only its identity once a later deletion page names it."""
    from unittest.mock import AsyncMock
    cloud_deletions = []
    runtime, domain, ids, _ = await recovery_budget_runtime(tmp_path, AsyncMock(return_value=None), count=1,
                                                            cloud_deletions=cloud_deletions)
    directory = AsyncMock(return_value={'characters': []})
    monkeypatch.setattr('services.chat_sync.profile_sync.read_character_directory', directory)
    try:
        # The first attempt proves delivery, then stops before releasing the role gate.
        with pytest.raises(ValueError, match='chat_sync_character_not_found'):
            await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        with closing(sqlite3.connect(runtime.ledger.path)) as conn:
            assert conn.execute('SELECT domain,message_id FROM chat_downloaded').fetchall() == [(domain.key(), ids[0])]
        cloud_deletions.append(ids[0])
        directory.return_value = {'characters': [{'characterSyncId': domain.character_uid, 'displayName': 'Cloud Neko'}]}
        await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        with closing(sqlite3.connect(runtime.ledger.path)) as conn:
            assert conn.execute('SELECT count(*) FROM chat_downloaded').fetchone() == (0,)
            assert conn.execute('SELECT domain,message_id FROM chat_tombstones').fetchall() == [(domain.key(), ids[0])]
            assert conn.execute('SELECT domain,message_id FROM chat_origin_identities').fetchall() == [(domain.key(), ids[0])]
        assert runtime.ledger.visible_originals(domain) == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_recovery_check_reporting_a_deletion_erases_retained_local_bodies(tmp_path):
    """A message the cloud check reports as deleted keeps only its identity and tombstone locally."""
    async def on_check(records, _runtime, _session, _index):
        return {'results': [{'messageId': row['messageId'], 'state': 'deleted'} for row in records]}
    runtime, domain, ids, _ = await recovery_budget_runtime(tmp_path, on_check, count=1)
    try:
        await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        with closing(sqlite3.connect(runtime.state.path.parent / 'sources.sqlite3')) as conn:
            assert conn.execute('SELECT original,deleted FROM source_originals WHERE record_id=?', (ids[0],)).fetchall() == [(None, 1)]
            assert conn.execute('SELECT original,payload,status FROM source_recovery_candidates WHERE record_id=?',
                                (ids[0],)).fetchall() == [(None, None, 'deleted')]
        with closing(sqlite3.connect(runtime.ledger.path)) as conn:
            assert conn.execute('SELECT domain,message_id FROM chat_tombstones').fetchall() == [(domain.key(), ids[0])]
    finally:
        await runtime.close()


# Unsent local deletions an earlier release could leave for this role, one per
# source, plus rows that belong to another role or another account.
OTHER_ACCOUNT = '12345678-1234-4234-8234-123456789abd'
OTHER_ROLE = 'c' * 32
UNSENT_BOUND, UNSENT_UNKNOWN_OWNER, UNSENT_OUTAGE, UNSENT_UNBOUND = 'a1' * 16, 'a2' * 16, 'a3' * 16, 'a4' * 16
UNSENT_OUTAGE_COPY = 'a5' * 16


def seed_unsent_deletions(runtime, domain):
    """Stage every kind of unsent deletion, sharing IDs with other roles and accounts."""
    from services.chat_sync.ledger import ChatOriginal
    from services.chat_sync.recovery import save_outage
    from services.chat_sync.source import ChatOriginalSource
    from services.chat_sync.writer import store_connection
    uid = domain.character_uid
    path = runtime.state.path.parent / 'sources.sqlite3'
    ChatOriginalSource(path).commit_unbound(uid, UNSENT_UNBOUND, ChatOriginal('user', 'deleted before binding'))
    with closing(store_connection(path)) as conn, conn:
        conn.execute('UPDATE source_unbound SET original=NULL,deleted=1,deletion_pending=1 WHERE record_id=?', (UNSENT_UNBOUND,))
        conn.executemany('INSERT INTO source_recovery_delete_ids VALUES (?,?,?)', [
            (domain.owner_id, uid, UNSENT_BOUND), (None, uid, UNSENT_BOUND), (None, uid, UNSENT_UNKNOWN_OWNER),
            (domain.owner_id, OTHER_ROLE, UNSENT_UNKNOWN_OWNER), (domain.owner_id, OTHER_ROLE, 'b1' * 16),
            (OTHER_ACCOUNT, uid, UNSENT_BOUND), (OTHER_ACCOUNT, uid, 'b2' * 16)])
    with closing(sqlite3.connect(runtime.state.path.parent / 'outage.sqlite3')) as conn, conn:
        # Earlier releases recorded deletions made during an outage in this table.
        conn.execute('CREATE TABLE outage_deletions (uid TEXT NOT NULL,message_id TEXT NOT NULL,PRIMARY KEY(uid,message_id))')
        conn.execute('INSERT INTO outage_deletions VALUES (?,?)', (uid, UNSENT_OUTAGE))
    # An outage copy deleted later on this device kept only its identity in
    # outage_originals, without a row in outage_deletions.
    save_outage(runtime.state.path.parent, uid, UNSENT_OUTAGE_COPY, ChatOriginal('user', 'deleted outage copy'))
    with closing(sqlite3.connect(runtime.state.path.parent / 'outage.sqlite3')) as conn, conn:
        conn.execute('UPDATE outage_originals SET original=NULL,deleted=1 WHERE record_id=?', (UNSENT_OUTAGE_COPY,))


def staged_deletion_rows(runtime):
    with closing(sqlite3.connect(runtime.state.path.parent / 'sources.sqlite3')) as conn:
        return conn.execute('SELECT owner,uid,message_id FROM source_recovery_delete_ids ORDER BY owner,uid,message_id').fetchall()


def tombstone_rows(runtime):
    with closing(sqlite3.connect(runtime.ledger.path)) as conn:
        return conn.execute('SELECT domain,message_id FROM chat_tombstones ORDER BY domain,message_id').fetchall()


def untouched_staged_rows(domain):
    return [(domain.owner_id, OTHER_ROLE, UNSENT_UNKNOWN_OWNER), (domain.owner_id, OTHER_ROLE, 'b1' * 16),
            (OTHER_ACCOUNT, domain.character_uid, UNSENT_BOUND), (OTHER_ACCOUNT, domain.character_uid, 'b2' * 16)]


def staged_for_role(domain):
    uid = domain.character_uid
    return {(domain.owner_id, uid, UNSENT_BOUND), (None, uid, UNSENT_BOUND), (None, uid, UNSENT_UNKNOWN_OWNER),
            (None, uid, UNSENT_OUTAGE), (None, uid, UNSENT_UNBOUND), (None, uid, UNSENT_OUTAGE_COPY)}


def kept_tombstones(domain):
    return [(domain.key(), identity) for identity in sorted((UNSENT_BOUND, UNSENT_UNKNOWN_OWNER, UNSENT_OUTAGE, UNSENT_UNBOUND,
                                                             UNSENT_OUTAGE_COPY))]


@pytest.mark.asyncio
async def test_recovery_keeps_the_roles_unsent_deletions_as_tombstones(tmp_path):
    """Finishing a role records its own unsent deletions once and leaves other roles and accounts alone."""
    from unittest.mock import AsyncMock
    from services.chat_sync.recovery import role_pending
    runtime, domain, _, _ = await recovery_budget_runtime(tmp_path, AsyncMock(return_value=None), count=1,
                                                          prepare=seed_unsent_deletions)
    try:
        assert tombstone_rows(runtime) == []
        result = await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        assert result == {'deletions': {'count': 5}}
        assert tombstone_rows(runtime) == kept_tombstones(domain)
        assert staged_deletion_rows(runtime) == untouched_staged_rows(domain)
        assert queued_deletions(runtime.ledger, domain) == []
        assert not role_pending(runtime, domain.character_uid) and role_pending(runtime, OTHER_ROLE)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_later_quarantine_and_recovery_do_not_count_kept_deletions_again(tmp_path):
    """Kept deletions, including those still listed in the outage store, are reported only once."""
    from unittest.mock import AsyncMock
    from services.chat_sync.recovery import role_pending
    runtime, domain, _, _ = await recovery_budget_runtime(tmp_path, AsyncMock(return_value=None), count=1,
                                                          prepare=seed_unsent_deletions)
    try:
        assert (await runtime.recover_writer_role(domain.character_uid, 'Local Neko')) == {'deletions': {'count': 5}}
        runtime.writer.path.write_text('{broken')
        assert (await runtime.recover_writer_role(domain.character_uid, 'Local Neko')) == {'deletions': {'count': 0}}
        assert tombstone_rows(runtime) == kept_tombstones(domain)
        assert staged_deletion_rows(runtime) == untouched_staged_rows(domain)
        assert not role_pending(runtime, domain.character_uid)
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('crash', ['tombstones', 'staged_ids'])
async def test_interrupted_recovery_finish_converges_when_recovery_runs_again(tmp_path, monkeypatch, crash):
    """A crash before or after the tombstone commit loses no deletion and leaves no duplicate."""
    from unittest.mock import AsyncMock
    from services.chat_sync.recovery import role_pending
    from services.chat_sync.writer import _WriterConnection
    runtime, domain, _, _ = await recovery_budget_runtime(tmp_path, AsyncMock(return_value=None), count=1,
                                                          prepare=seed_unsent_deletions)
    statement = {'tombstones': 'INSERT OR IGNORE INTO chat_tombstones',
                 'staged_ids': 'DELETE FROM source_recovery_delete_ids'}[crash]
    real_executemany = _WriterConnection.executemany

    def interrupted(self, sql, parameters, /):
        parameters = list(parameters)
        if sql.startswith(statement) and parameters:
            raise RuntimeError('synthetic crash while finishing recovery')
        return real_executemany(self, sql, parameters)

    try:
        monkeypatch.setattr(_WriterConnection, 'executemany', interrupted)
        with pytest.raises(RuntimeError, match='synthetic crash'):
            await runtime.recover_writer_role(domain.character_uid, 'Local Neko')
        monkeypatch.setattr(_WriterConnection, 'executemany', real_executemany)
        # The staged IDs outlive the crash; tombstones exist only if their commit finished.
        assert set(staged_deletion_rows(runtime)) == staged_for_role(domain) | set(untouched_staged_rows(domain))
        assert tombstone_rows(runtime) == ([] if crash == 'tombstones' else kept_tombstones(domain))
        assert role_pending(runtime, domain.character_uid)
        assert (await runtime.recover_writer_role(domain.character_uid, 'Local Neko')) == {'deletions': {'count': 5}}
        assert tombstone_rows(runtime) == kept_tombstones(domain)
        assert staged_deletion_rows(runtime) == untouched_staged_rows(domain)
        assert not role_pending(runtime, domain.character_uid)
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


@pytest.mark.parametrize('known', ['origin', 'message'])
def test_recovery_receipt_cannot_rebind_a_known_origin_identity(tmp_path, known):
    """A cloud receipt cannot pair an origin slot or a message ID with a second partner."""
    from services.chat_sync.ledger import IdentityConflict
    from services.chat_sync.protocol import encode_chat_original
    from services.chat_sync.recovery import apply_checked_candidates, pending_candidates
    from services.chat_sync.writer import store_connection
    runtime, domain, _, _ = deletion_recovery_task(tmp_path, 0)
    payload = encode_chat_original({'schemaVersion': 1, 'ownerId': domain.owner_id,
        'characterSyncId': domain.character_uid, 'channel': 'chat', 'scope': 'private-chat',
        'messageId': '1' * 32, 'originStreamId': '2' * 32, 'originSeq': 1, 'role': 'user', 'text': 'copied',
        'originalTime': None, 'timezone': None, 'conversationId': None, 'replyToMessageId': None})
    with closing(store_connection(runtime.ledger.path)) as conn, conn:
        conn.execute('INSERT INTO chat_downloaded VALUES (?,?,?)', (domain.key(), '1' * 32, payload))
    runtime.prepare_writer_recovery()
    candidates = pending_candidates(runtime, domain)
    assert [row['record_id'] for row in candidates] == ['1' * 32]
    # Staging keeps every committed identity. One of them already pairs the
    # candidate's origin slot, or its message ID, with a different partner.
    known_identity = ('2' * 32, 1, '3' * 32) if known == 'origin' else ('2' * 32, 2, '1' * 32)
    with closing(store_connection(runtime.ledger.path)) as conn, conn:
        conn.execute('INSERT INTO chat_origin_identities VALUES (?,?,?,?)', (domain.key(), *known_identity))
    with pytest.raises(IdentityConflict, match='^origin_identity_conflict$'):
        apply_checked_candidates(runtime, domain, candidates, [{'messageId': '1' * 32, 'state': 'received'}])
    # Nothing becomes visible and the candidate stays unchecked for a later decision.
    assert runtime.ledger.visible_originals(domain) == []
    assert [row['record_id'] for row in pending_candidates(runtime, domain)] == ['1' * 32]
    with closing(sqlite3.connect(runtime.ledger.path)) as conn:
        assert conn.execute('SELECT origin_stream_id,origin_seq,message_id FROM chat_origin_identities').fetchall() == [
            known_identity]


@pytest.mark.asyncio
async def test_repeated_outage_recovery_coalesces_identical_bound_and_unbound_proof(tmp_path):
    import hashlib
    from services.chat_sync.ledger import ChatOriginal
    from services.chat_sync.prototype import PrototypeChatSyncClient
    from services.chat_sync.recovery import apply_checked_candidates, finish_role, pending_candidates, save_outage
    from services.chat_sync.source import ChatOriginalSource
    from services.chat_sync.writer import store_connection
    runtime, domain, _, _ = deletion_recovery_task(tmp_path, 0)
    identity = '1' * 32
    save_outage(runtime.state.path.parent, domain.character_uid, identity, ChatOriginal('user', 'outage'))
    runtime.prepare_writer_recovery()
    binding = runtime.state.activate(runtime.state.prepare_binding(domain), 'b' * 32)
    candidates = pending_candidates(runtime, domain)
    apply_checked_candidates(runtime, domain, candidates, [{'messageId': identity, 'state': 'missing'}])
    finish_role(runtime, domain, binding, 'session')
    source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
    with closing(store_connection(source.path)) as conn, conn:
        # An earlier history grant moved the same proven bytes into the bound lane.
        conn.execute('INSERT INTO source_originals (record_id,owner,uid,original,original_hash,deleted,'
                     'queued_at_ms,origin_stream_id,origin_seq) SELECT record_id,?,uid,original,original_hash,0,'
                     'queued_at_ms,origin_stream_id,origin_seq FROM source_unbound WHERE record_id=?', (domain.owner_id, identity))
        conn.execute('UPDATE source_unbound SET original=NULL WHERE record_id=?', (identity,))
    runtime.prepare_writer_recovery()
    candidates = pending_candidates(runtime, domain)
    assert len(candidates) == 1
    requests = []
    async def request(method, path, payload):
        requests.append(json.loads(payload))
        return {'ownerId': domain.owner_id, 'characterSyncId': domain.character_uid, 'cursor': 0,
                'results': [{'messageId': identity, 'state': 'received'}]}
    client = PrototypeChatSyncClient(domain, request)
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
    from services.chat_sync.recovery import pending_candidates, save_outage
    from services.chat_sync.writer import store_connection
    runtime, domain, _, _ = deletion_recovery_task(tmp_path, 0)
    save_outage(runtime.state.path.parent, domain.character_uid, '1' * 32, ChatOriginal('user', 'outage'))
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


# The sources.sqlite3 shape the previous main build left behind: its retired
# tables and columns, plus history backfill rows that name the local and cloud roles.
_EARLIER_SOURCE_TABLES = """
DROP TABLE source_originals;
DROP TABLE source_unbound;
DROP TABLE source_history_tasks;
DROP TABLE source_history_members;
CREATE TABLE source_identity (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    database_id TEXT NOT NULL, generation TEXT NOT NULL);
CREATE TABLE source_originals (
    record_id TEXT PRIMARY KEY, owner TEXT NOT NULL, uid TEXT NOT NULL,
    original TEXT, original_hash TEXT NOT NULL,
    pending INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0,
    queued_at_ms INTEGER, history_task_id TEXT, origin_stream_id TEXT, origin_seq INTEGER);
CREATE TABLE source_unbound (
    record_id TEXT PRIMARY KEY, uid TEXT NOT NULL, scope TEXT NOT NULL,
    original TEXT, original_hash TEXT NOT NULL, queued_at_ms INTEGER NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0, origin_stream_id TEXT, origin_seq INTEGER,
    verified_owner TEXT, deletion_pending INTEGER NOT NULL DEFAULT 0);
CREATE TABLE source_role_reassignments (
    old_uid TEXT PRIMARY KEY, owner TEXT NOT NULL, new_uid TEXT NOT NULL UNIQUE);
CREATE TABLE source_history_tasks (
    task_id TEXT PRIMARY KEY, uid TEXT NOT NULL, binding TEXT NOT NULL,
    service_url TEXT NOT NULL, fingerprint TEXT NOT NULL,
    database_id TEXT NOT NULL, generation TEXT NOT NULL,
    local_name TEXT NOT NULL, cloud_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'preview', count INTEGER NOT NULL, device_id TEXT);
CREATE TABLE source_history_members (
    task_id TEXT NOT NULL, record_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'waiting', original_hash TEXT, origin_stream_id TEXT, origin_seq INTEGER,
    PRIMARY KEY(task_id,record_id));
INSERT INTO source_identity VALUES (1, 'database', 'generation');
INSERT INTO source_history_tasks VALUES ('task-retired', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', '{}', 'https://sync.test',
    'fingerprint', 'database', 'generation', 'Local Neko', 'Cloud Neko', 'approved', 1, 'device');
INSERT INTO source_history_members VALUES ('task-retired', '66666666666666666666666666666666', 0, 'waiting', NULL, NULL, NULL);
INSERT INTO source_history_tasks VALUES ('task-other', 'cccccccccccccccccccccccccccccccc', '{}', 'https://sync.test',
    'fingerprint', 'database', 'generation', 'Other Neko', 'Other Neko', 'preview', 1, NULL);
INSERT INTO source_history_members VALUES ('task-other', '77777777777777777777777777777777', 0, 'waiting', NULL, NULL, NULL);
CREATE TABLE source_recovery_delete_tasks (
    task_id TEXT PRIMARY KEY, uid TEXT NOT NULL, owner TEXT NOT NULL,
    binding TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'preview');
CREATE TABLE source_recovery_delete_members (
    task_id TEXT NOT NULL, message_id TEXT NOT NULL, registered INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(task_id,message_id));
INSERT INTO source_recovery_delete_tasks VALUES ('delete-task', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    '12345678-1234-4234-8234-123456789abc', '{}', 'fingerprint', 'approved');
INSERT INTO source_recovery_delete_members VALUES ('delete-task', '88888888888888888888888888888888', 1);
"""

# The previous main build also kept approved recovery deletion pages in the ledger.
_EARLIER_LEDGER_TABLES = """
CREATE TABLE chat_recovery_deletion_pages (
    domain TEXT NOT NULL, task_id TEXT NOT NULL, page_hash TEXT NOT NULL,
    deletion_id TEXT NOT NULL UNIQUE, payload BLOB NOT NULL, acked INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(domain,task_id,page_hash));
INSERT INTO chat_recovery_deletion_pages VALUES ('["12345678-1234-4234-8234-123456789abc","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","chat","private-chat"]',
    'delete-task', 'page', '99999999999999999999999999999999', '{"messageIds":["88888888888888888888888888888888"]}', 1);
"""


@pytest.mark.asyncio
@pytest.mark.parametrize('earlier_release', [False, True])
async def test_source_store_saves_recovers_and_retires_on_fresh_and_earlier_schemas(tmp_path, earlier_release):
    """Fresh stores never create retired source tables; stores from the previous build keep working unchanged."""
    from contextlib import nullcontext
    from services.chat_sync.ledger import ChatDomain, ChatOriginal
    from services.chat_sync.recovery import apply_checked_candidates, finish_role, pending_candidates
    from services.chat_sync.runtime import DesktopChatSyncRuntime
    from services.chat_sync.source import ChatOriginalSource
    from services.chat_sync.writer import store_connection
    async def session():
        return {'logged_in': False}
    runtime = DesktopChatSyncRuntime(tmp_path / 'runtime', session, witness_path=tmp_path / 'device' / 'writer.json',
                                     session_commit_guard=lambda _: nullcontext())
    try:
        domain = ChatDomain('12345678-1234-4234-8234-123456789abc', 'a' * 32)
        uid = domain.character_uid
        path = runtime.state.path.parent / 'sources.sqlite3'
        if earlier_release:
            with closing(store_connection(path)) as conn, conn:
                conn.executescript(_EARLIER_SOURCE_TABLES)
            with closing(store_connection(runtime.ledger.path)) as conn, conn:
                conn.executescript(_EARLIER_LEDGER_TABLES)
        source = ChatOriginalSource(path)
        binding = runtime.state.activate(runtime.state.prepare_binding(domain), 'b' * 32)
        source.commit(domain.owner_id, uid, '1' * 32, ChatOriginal('user', 'bound'))
        source.commit_unbound(uid, '2' * 32, ChatOriginal('user', 'unbound'))
        source.commit(domain.owner_id, uid, '3' * 32, ChatOriginal('user', 'deleted during outage'))
        with closing(sqlite3.connect(runtime.state.path.parent / 'outage.sqlite3')) as conn, conn:
            conn.execute('CREATE TABLE outage_deletions (uid TEXT NOT NULL,message_id TEXT NOT NULL,PRIMARY KEY(uid,message_id))')
            conn.execute('INSERT INTO outage_deletions VALUES (?,?)', (uid, '3' * 32))
        runtime.prepare_writer_recovery()
        binding = runtime.state.activate(runtime.state.prepare_binding(domain), 'b' * 32)
        candidates = pending_candidates(runtime, domain)
        assert sorted(row['record_id'] for row in candidates) == ['1' * 32, '2' * 32]
        states = {'1' * 32: 'deleted', '2' * 32: 'missing'}
        apply_checked_candidates(runtime, domain, candidates,
                                 [{'messageId': row['record_id'], 'state': states[row['record_id']]} for row in candidates])
        # The outage deletion never reached the cloud; it stays a local tombstone.
        assert finish_role(runtime, domain, binding, 'session') == {'count': 1}
        assert '3' * 32 in runtime.ledger.deleted_message_ids(domain)
        assert staged_deletion_ids(runtime, domain) == []
        with closing(sqlite3.connect(path)) as conn:
            # Outage and cloud deletions erase bound bodies but keep their identities.
            assert conn.execute('SELECT record_id,original,deleted FROM source_originals ORDER BY record_id').fetchall() == [
                ('1' * 32, None, 1), ('3' * 32, None, 1)]
            unbound = conn.execute('SELECT record_id,uid,scope,original,deleted FROM source_unbound').fetchall()
        assert [(row[0], row[1], row[2], json.loads(row[3])['text'], row[4]) for row in unbound] == [
            ('2' * 32, uid, 'private-chat-original-v1', 'unbound', 0)]
        source.commit(domain.owner_id, uid, '4' * 32, ChatOriginal('user', 'after recovery'))
        source.commit_unbound(uid, '5' * 32, ChatOriginal('user', 'after recovery unbound'))
        assert source.stored_ids(uid) == {str(index) * 32 for index in range(1, 6)}
        runtime._purge_retired_character(runtime.state.binding(uid), 'session', runtime.writer.epoch())
        with closing(sqlite3.connect(path)) as conn:
            for table in ('source_originals', 'source_unbound', 'source_local_replies', 'source_origin_streams',
                          'source_history_tasks', 'source_recovery_candidates', 'source_recovery_roles',
                          'source_recovery_delete_ids'):
                assert conn.execute(f'SELECT count(*) FROM {table} WHERE uid=?', (uid,)).fetchone() == (0,), table
            # Only the other role's legacy history task and member are left.
            assert conn.execute('SELECT task_id FROM source_history_tasks').fetchall() == (
                [('task-other',)] if earlier_release else [])
            assert conn.execute('SELECT task_id FROM source_history_members').fetchall() == (
                [('task-other',)] if earlier_release else [])
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            columns = {table: {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
                       for table in ('source_originals', 'source_unbound', 'source_history_tasks', 'source_history_members')}
            retired_columns = {'source_originals': {'pending', 'history_task_id'}, 'source_unbound': {'verified_owner'},
                               'source_history_tasks': {'device_id'},
                               'source_history_members': {'original_hash', 'origin_stream_id', 'origin_seq'}}
            retired_tables = {'source_identity', 'source_role_reassignments',
                              'source_recovery_delete_tasks', 'source_recovery_delete_members'}
            if earlier_release:
                # Leftover tables and columns are not dropped; their rows hold no chat text.
                assert conn.execute('SELECT * FROM source_identity').fetchall() == [(1, 'database', 'generation')]
                assert conn.execute('SELECT count(*) FROM source_role_reassignments').fetchone() == (0,)
                assert all(retired <= columns[table] for table, retired in retired_columns.items())
                # Quarantine, recovery and retirement leave the retired deletion task rows as they were.
                assert conn.execute('SELECT task_id,uid,status FROM source_recovery_delete_tasks').fetchall() == [
                    ('delete-task', uid, 'approved')]
                assert conn.execute('SELECT * FROM source_recovery_delete_members').fetchall() == [
                    ('delete-task', '8' * 32, 1)]
            else:
                assert tables.isdisjoint(retired_tables)
                assert all(retired.isdisjoint(columns[table]) for table, retired in retired_columns.items())
        with closing(sqlite3.connect(runtime.ledger.path)) as conn:
            ledger_tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if earlier_release:
                assert conn.execute('SELECT task_id,deletion_id,acked FROM chat_recovery_deletion_pages').fetchall() == [
                    ('delete-task', '9' * 32, 1)]
            else:
                assert 'chat_recovery_deletion_pages' not in ledger_tables
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_recovery_finish_coalesces_outage_and_bound_marker_ids_into_tombstones(tmp_path):
    from services.chat_sync.recovery import finish_role
    runtime, domain, _, _ = deletion_recovery_task(tmp_path, 0)
    runtime.ledger.queue_deletion(domain, ['1' * 32, '2' * 32])
    runtime.writer.path.write_text('{broken')
    with closing(sqlite3.connect(runtime.state.path.parent / 'outage.sqlite3')) as conn, conn:
        # Earlier releases recorded outage deletions in this table. One ID is
        # shared with the bound outbox; the other exists only in this table.
        conn.execute('CREATE TABLE outage_deletions (uid TEXT NOT NULL,message_id TEXT NOT NULL,PRIMARY KEY(uid,message_id))')
        conn.executemany('INSERT INTO outage_deletions VALUES (?,?)', [(domain.character_uid, '1' * 32), (domain.character_uid, '5' * 32)])
    runtime.prepare_writer_recovery()
    binding = runtime.state.activate(runtime.state.prepare_binding(domain), 'b' * 32)
    assert finish_role(runtime, domain, binding, 'session') == {'count': 3}
    assert sorted(runtime.ledger.deleted_message_ids(domain)) == ['1' * 32, '2' * 32, '5' * 32]
    assert staged_deletion_ids(runtime, domain) == []
    assert queued_deletions(runtime.ledger, domain) == []


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
    assert queued_deletions(runtime.ledger, domain) == []
