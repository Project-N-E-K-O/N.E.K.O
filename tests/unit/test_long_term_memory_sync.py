"""Learned-memory replacement must retain raw chat and unrelated memory partitions."""

import asyncio
from collections import OrderedDict
from contextlib import closing
from copy import deepcopy
import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from memory.long_term_sync import DesktopLongTermAdapter, FILES, export_document, import_document, read_views
from memory.event_log import EventLog, Reconciler
from services.chat_sync.long_term import LongTermMemorySync
from services.chat_sync.long_term_protocol import document, mutation
from services.chat_sync.writer import store_connection
from utils.file_utils import atomic_write_json
from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401
from tests.unit.test_chat_sync_runtime import setup_runtime, OWNER, UID


def learned(completed=2000, text='learned preference'):
    return {'schemaVersion': 1, 'completedAt': completed, 'entries': [
        {'kind': 'persona', 'subject': 'master', 'id': 'p1', 'text': text,
         'metadata': {'createdAt': 1000, 'updatedAt': completed, 'sourceSyncId': 'mobile:p1', 'reinforcement': 3}},
        {'kind': 'fact', 'subject': 'relationship', 'id': 'f1', 'text': 'shared experience',
         'metadata': {'createdAt': 1000, 'importance': 8, 'sourceMessageIds': ['local-source-1']}},
        {'kind': 'reflection', 'subject': 'neko', 'id': 'r1', 'text': 'confirmed impression',
         'metadata': {'createdAt': 1000, 'sourceFactIds': ['f1']}},
    ]}


@pytest.mark.parametrize('milliseconds', [1000, 1234])
def test_epoch_day_roundtrip_avoids_windows_naive_timestamp_limit(monkeypatch, milliseconds):
    from memory import long_term_sync
    value = long_term_sync.iso(milliseconds)
    local = datetime.fromtimestamp(milliseconds / 1000, timezone.utc).astimezone().tzinfo
    monkeypatch.setitem(sys.modules, 'win32timezone', SimpleNamespace(TimeZoneInfo=SimpleNamespace(local=lambda: local)))
    monkeypatch.setattr(sys, 'platform', 'win32')
    class WindowsDatetime(datetime):
        def timestamp(self):
            if self.tzinfo is None and self.date() == datetime(1970, 1, 1).date():
                raise OSError(22, 'Windows fold probe precedes the epoch')
            return super().timestamp()
    monkeypatch.setattr(long_term_sync, 'datetime', WindowsDatetime)
    assert long_term_sync.timestamp(value) == milliseconds


@pytest.mark.parametrize('offset,expected', [(0, -875), (8, -28_800_875), (-5, 17_999_125)])
def test_windows_pre_epoch_local_date_does_not_use_mktime(monkeypatch, offset, expected):
    from memory import long_term_sync
    import time
    class WindowsDatetime(datetime):
        def timestamp(self):
            if self.tzinfo is None:
                raise OSError(22, 'Windows local timestamp is outside the CRT range')
            return super().timestamp()
    local = timezone(timedelta(hours=offset))
    monkeypatch.setitem(sys.modules, 'win32timezone', SimpleNamespace(TimeZoneInfo=SimpleNamespace(local=lambda: local)))
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(long_term_sync, 'datetime', WindowsDatetime)
    def unavailable(_):
        raise OSError(22, 'Windows mktime rejects pre-epoch dates')
    monkeypatch.setattr(time, 'mktime', unavailable)
    assert long_term_sync.timestamp('1969-12-31T23:59:59.125') == expected


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows timezone and CRT regression')
def test_native_windows_exports_pre_epoch_local_naive_date():
    from win32timezone import TimeZoneInfo
    parsed = datetime(1969, 12, 31, 23, 59, 59, 125000)
    expected = -875 - int(TimeZoneInfo.local().utcoffset(parsed).total_seconds() * 1000)
    views = {'persona': {'master': {'facts': [{'id': 'old', 'text': 'historical preference',
              'created_at': parsed.isoformat()}]}}, 'facts': [], 'archive': [], 'reflections': []}
    exported = export_document(views, 2000)['entries'][0]
    assert exported['metadata']['createdAt'] == expected
    assert exported['metadata']['updatedAt'] == expected


def test_portable_replacement_preserves_static_cards_group_memory_and_evidence():
    existing = {'persona': {'master': {'facts': [
        {'id': 'old', 'text': 'old learned state'}, {'id': 'card', 'text': 'static', 'source': 'character_card'}]},
        'someone-else': {'facts': ['unchanged']}},
        'facts': [{'id': 'group', 'entity': 'master', 'scope': 'group:one', 'text': 'group state'}],
        'archive': [], 'reflections': []}
    native = import_document(learned(), existing)
    assert native['persona']['someone-else'] == existing['persona']['someone-else']
    assert native['persona']['master']['facts'][0]['id'] == 'card'
    assert native['facts'][0] == existing['facts'][0]
    assert native['facts'][1]['signal_processed'] is True
    portable = export_document(native, 3000)
    assert len(portable['entries']) == 3
    assert portable['entries'][1]['metadata']['reinforcement'] == 3
    assert portable['entries'][1]['metadata']['sourceSyncId'] == 'mobile:p1'
    assert 'group state' not in json.dumps(portable) and 'static' not in json.dumps(portable)


@pytest.mark.parametrize('source', ['external_import', pytest.param('manual', id='hand-entered'), 'settings'])
def test_replacement_retains_receiver_persona_origin_and_local_state(source):
    local = {'id': 'p1', 'text': 'old local wording', 'source': source, 'source_id': 'receiver-origin',
             'suppress': True, 'suppressed_at': datetime.now().isoformat(), 'recent_mentions': ['receiver-mention'],
             'sub_zero_days': 3, 'sub_zero_last_increment_date': datetime.now().date().isoformat()}
    if source == 'external_import':
        local['external_import'] = {'folded_fingerprints': ['receiver-fingerprint'], 'format': 'openclaw'}
    scoped = {**deepcopy(local), 'scope': 'group:one', 'source_id': 'group-origin'}
    existing = {'persona': {'master': {'facts': [scoped, local]}}, 'facts': [], 'archive': [], 'reflections': []}
    original = deepcopy(existing)
    result = import_document(learned(text='new remote wording'), existing)
    restored = result['persona']['master']['facts'][1]
    assert result['persona']['master']['facts'][0] == scoped
    for key in local.keys() - {'id', 'text'}:
        assert restored[key] == local[key]
    assert restored['text'] == 'new remote wording'
    assert restored['reinforcement'] == 3
    assert existing == original


def test_local_persona_origin_does_not_drop_incoming_reflection_links():
    local = {'id': 'p1', 'text': 'local wording', 'source': 'external_import', 'source_id': 'receiver-origin'}
    existing = {'persona': {'master': {'facts': [local]}}, 'facts': [], 'archive': [], 'reflections': []}
    incoming = learned()
    incoming['entries'][0]['metadata']['sourceReflectionIds'] = ['remote-primary', 'remote-merged']
    result = import_document(incoming, existing)
    restored = result['persona']['master']['facts'][0]
    assert restored['source'] == 'external_import'
    assert restored['source_id'] == 'receiver-origin'
    portable = next(row for row in export_document(result, 3000)['entries'] if row['kind'] == 'persona')
    assert portable['metadata']['sourceReflectionIds'] == ['remote-primary', 'remote-merged']


@pytest.mark.asyncio
async def test_snapshot_roundtrip_keeps_local_external_import_idempotency():
    from tests.unit.test_persona_external_import import _FusionHarness, _candidates
    harness = _FusionHarness({'master': {'facts': []}}, stub_fused=[{'text': 'Fused impression.', 'importance': 8}])
    candidates = _candidates('raw external observation')
    await harness.afuse_external_facts('Neko', 'master', candidates, 'openclaw')
    existing = {'persona': harness.persona, 'facts': [], 'archive': [], 'reflections': []}
    snapshot = export_document(existing, int(datetime.now().timestamp() * 1000))
    restored = import_document(snapshot, existing)
    harness.persona = restored['persona']
    assert await harness.afuse_external_facts('Neko', 'master', candidates, 'openclaw') == {
        'added': 0, 'skipped': 1, 'fused': False}
    assert harness.llm_call_count == 1
    # The portable schema carries content, not the sender's import ledger.
    empty = {'persona': {}, 'facts': [], 'archive': [], 'reflections': []}
    foreign = import_document(snapshot, empty)['persona']['master']['facts'][0]
    assert foreign['source'] == 'manual'
    assert 'external_import' not in foreign


@pytest.mark.parametrize('invalid', [[], {}, {'rawMessages': []}, {'embeddings': []}])
def test_protocol_rejects_arbitrary_native_or_archive_payloads(invalid):
    with pytest.raises(ValueError):
        document(invalid)


@pytest.fixture
async def owner(tmp_path, monkeypatch):
    from app.memory_server import long_term_sync as native
    from app.memory_server import review
    for name, value in [('_review_spawn_locks', {}), ('_derived_task_admission_claims', {}),
                        ('_released_derived_task_claim_tokens', OrderedDict()),
                        ('_retired_derived_task_names', set()), ('_publication_held_derived_task_names', set())]:
        monkeypatch.setattr(review, name, value)
    config = SimpleNamespace(app_docs_dir=tmp_path, memory_dir=tmp_path / 'memory', local_state_dir=tmp_path / 'local')
    characters = {'猫娘': {'Neko': {'_reserved': {'character_uid': UID}}}}
    config.load_characters = lambda: deepcopy(characters)
    async def load():
        return deepcopy(characters)
    config.aload_characters = load
    runtime, service, session = setup_runtime(tmp_path, long_term_memory_adapter=DesktopLongTermAdapter(config))
    await runtime.configure('https://sync.test')
    await runtime.enable(UID, 'Neko')
    locks = {}
    def alock(name):
        return locks.setdefault(name, asyncio.Lock())
    file_lock = threading.RLock()
    facts = SimpleNamespace(_get_persist_alock=lambda _: alock('facts'), _get_lock=lambda _: file_lock, _facts={'Neko': []})
    personas = SimpleNamespace(_get_alock=lambda _: alock('persona'), _personas={'Neko': {}})
    reflections = SimpleNamespace(_get_alock=lambda _: alock('reflection'))
    log = EventLog()
    log._config_manager = config
    replay = Reconciler(log)
    for key, value in {'_config_manager': config, 'fact_store': facts, 'persona_manager': personas,
                       'reflection_engine': reflections, '_get_settle_lock': lambda _: alock('settle'),
                       '_reload_lock': asyncio.Lock(), 'event_log': log, 'reconciler': replay}.items():
        monkeypatch.setattr(native.runtime, key, value)
    monkeypatch.setattr(native, '_authority', lambda: runtime.writer)
    monkeypatch.setattr(native, '_session_guard', runtime.session_commit_guard, raising=False)
    monkeypatch.setattr(native, 'assert_cloudsave_writable', lambda *args, **kwargs: None)
    native.register_replay(replay)
    directory = config.memory_dir / 'Neko'
    directory.mkdir(parents=True)
    for key, filename in FILES.items():
        atomic_write_json(directory / filename, {} if key == 'persona' else [])
    raw = directory / 'time_indexed.db'
    raw.write_bytes(b'synthetic raw archive remains byte-identical')
    binding = {**runtime.state.binding(UID), 'service_url': 'https://sync.test'}
    try:
        yield SimpleNamespace(native=native, runtime=runtime, config=config, binding=binding,
                              adapter=runtime.long_term_memory.adapter, directory=directory, raw=raw, replay=replay, session=session,
                              characters=characters)
    finally:
        await native.close()
        await runtime.close()


async def stage(owner, completed=2000, revision=1):
    remote = {'ownerId': OWNER, 'characterSyncId': UID, 'revision': revision,
              'completedAt': completed, 'document': learned(completed)}
    local = owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())
    assert owner.adapter.stage_remote(owner.binding, remote, owner.runtime.writer.epoch(), local['operationId'] if local else None,
                                      session_fingerprint=(await owner.runtime._session())[1])


@pytest.mark.asyncio
async def test_native_owner_captures_only_after_completion_and_applies_without_archive_changes(owner):
    raw = owner.raw.read_bytes()
    await stage(owner)
    assert await owner.native.consume(UID)
    state = read_views(owner.directory)
    assert state['persona']['master']['facts'][0]['text'] == 'learned preference'
    assert owner.raw.read_bytes() == raw
    assert not owner.adapter.incoming_path(UID).exists()
    assert owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())['operationId'] == 'download:1'
    assert owner.native._capture('Neko', False) is False
    assert owner.native._capture('Neko', True) is True
    current = owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())
    assert current['document']['completedAt'] > 2000
    assert owner.native._capture('Neko', False) is False
    assert owner.raw.read_bytes() == raw


@pytest.mark.asyncio
@pytest.mark.parametrize('stored_service', [None, ''])
async def test_native_owner_uses_default_service_for_capture_and_download(owner, monkeypatch, stored_service):
    from services.chat_sync import runtime as sync_runtime

    monkeypatch.setattr(sync_runtime, '_BUILTIN_SERVICE_URL', owner.binding['service_url'])
    with closing(store_connection(owner.runtime.state.path)) as connection, connection:
        connection.execute('UPDATE sync_settings SET service_url=?', (stored_service,))
    assert owner.runtime.state.settings()['service_url'] == owner.binding['service_url']
    assert owner.native._capture('Neko', True)
    local = owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())
    assert local is not None
    await stage(owner, completed=local['document']['completedAt'] + 1)
    assert await owner.native.consume(UID)
    assert read_views(owner.directory)['persona']['master']['facts'][0]['text'] == 'learned preference'
    assert owner.raw.read_bytes() == b'synthetic raw archive remains byte-identical'


@pytest.mark.asyncio
async def test_native_failed_multi_file_save_rolls_back_and_journal_replays_exact_snapshot(owner, monkeypatch):
    await stage(owner)
    before = {path.name: path.read_bytes() for path in owner.directory.iterdir()}
    original = owner.native.atomic_write_json
    failed = False
    def fail_once(path, *args, **kwargs):
        nonlocal failed
        if path.name == 'facts.json' and not failed:
            failed = True
            raise OSError('synthetic disk failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(owner.native, 'atomic_write_json', fail_once)
    with pytest.raises(ValueError, match='long_term_memory_save_failed'):
        await owner.native.consume(UID)
    for name, raw in before.items():
        assert (owner.directory / name).read_bytes() == raw
    assert owner.adapter.incoming_path(UID).exists()
    await owner.replay.areconcile('Neko')
    assert read_views(owner.directory)['facts'][0]['text'] == 'shared experience'
    assert await owner.native.consume(UID)
    assert owner.raw.read_bytes() == before['time_indexed.db']


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['newer-local', 'paused-binding', 'changed-epoch'])
async def test_pending_remote_never_overwrites_a_newer_completion_or_revoked_grant(owner, change):
    await stage(owner)
    before = (owner.directory / 'persona.json').read_bytes()
    if change == 'newer-local':
        assert owner.native._capture('Neko', True)
    elif change == 'paused-binding':
        owner.runtime.state.pause(UID)
    else:
        payload = json.loads(owner.adapter.incoming_path(UID).read_text())
        payload['epoch'] = ['0' * 32, '0' * 32]
        atomic_write_json(owner.adapter.incoming_path(UID), payload)
    assert not await owner.native.consume(UID)
    assert (owner.directory / 'persona.json').read_bytes() == before


@pytest.mark.asyncio
async def test_obsolete_inbox_is_removed_without_appending_replacement_events(owner):
    await stage(owner)
    assert owner.native._capture('Neko', True)
    before = {filename: (owner.directory / filename).read_bytes() for filename in FILES.values()}
    events = owner.directory / 'events.ndjson'
    original_events = events.read_bytes() if events.exists() else b''
    for _ in range(3):
        assert not await owner.native.consume(UID)
    assert not owner.adapter.incoming_path(UID).exists()
    assert (events.read_bytes() if events.exists() else b'') == original_events
    assert before == {filename: (owner.directory / filename).read_bytes() for filename in FILES.values()}


@pytest.mark.asyncio
@pytest.mark.parametrize('published', [False, True])
async def test_remote_replacement_preserves_unpublished_native_entries_until_capture(owner, published):
    if published:
        assert owner.native._capture('Neko', True)
    fact = {'id': 'unpublished', 'entity': 'master', 'text': 'new local observation', 'source': 'user_fact'}
    persona = {'id': 'local-persona', 'text': 'local manual preference', 'source': 'manual', 'suppress': True}
    reflection = {'id': 'local-reflection', 'entity': 'master', 'text': 'new confirmed impression',
                  'status': 'confirmed', 'confirmed_at': datetime.now().isoformat()}
    atomic_write_json(owner.directory / 'facts.json', [fact])
    atomic_write_json(owner.directory / 'persona.json', {'master': {'facts': [persona]}})
    atomic_write_json(owner.directory / 'reflections.json', [reflection])
    baseline = owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())
    completed = (baseline['document']['completedAt'] if baseline else 2000) + 1
    for revision in (1, 2):
        await stage(owner, completed=completed + revision, revision=revision)
        assert await owner.native.consume(UID)
        views = read_views(owner.directory)
        assert fact in views['facts']
        assert persona in views['persona']['master']['facts']
        assert reflection in views['reflections']
        local = owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())
        assert 'unpublished' not in {row['id'] for row in local['nativeEntries']}
    assert owner.native._capture('Neko', False)
    captured = owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())
    assert 'unpublished' in {row['id'] for row in captured['document']['entries']}
    assert not owner.native._capture('Neko', False)


@pytest.mark.asyncio
async def test_remote_replacement_preserves_local_edits_and_deletions_against_published_baseline(owner):
    await stage(owner)
    assert await owner.native.consume(UID)
    current = read_views(owner.directory)
    current['persona']['master']['facts'][0]['text'] = 'locally edited preference'
    current['facts'] = []
    for key, filename in FILES.items():
        atomic_write_json(owner.directory / filename, current[key])
    await stage(owner, completed=3000, revision=2)
    assert await owner.native.consume(UID)
    result = read_views(owner.directory)
    assert result['persona']['master']['facts'] == current['persona']['master']['facts']
    assert result['facts'] == []
    assert owner.native._capture('Neko', False)


@pytest.mark.asyncio
async def test_remote_still_replaces_unchanged_published_entries(owner):
    await stage(owner)
    assert await owner.native.consume(UID)
    await stage(owner, completed=3000, revision=2)
    path = owner.adapter.incoming_path(UID)
    payload = json.loads(path.read_text())
    payload['head']['document']['entries'] = [row for row in payload['head']['document']['entries'] if row['kind'] != 'fact']
    payload['head']['document']['entries'][0]['text'] = 'new remote preference'
    atomic_write_json(path, payload)
    assert await owner.native.consume(UID)
    views = read_views(owner.directory)
    assert views['facts'] == []
    assert views['persona']['master']['facts'][0]['text'] == 'new remote preference'
    assert not owner.native._capture('Neko', False)


@pytest.mark.asyncio
async def test_failed_replacement_replays_without_losing_unpublished_entries(owner, monkeypatch):
    fact = {'id': 'local-only', 'entity': 'master', 'text': 'unpublished local fact'}
    atomic_write_json(owner.directory / 'facts.json', [fact])
    await stage(owner)
    original = owner.native._replace_fact_index
    def fail(name, views):
        raise OSError('synthetic index commit failure')
    monkeypatch.setattr(owner.native, '_replace_fact_index', fail)
    with pytest.raises(ValueError, match='long_term_memory_save_failed'):
        await owner.native.consume(UID)
    assert read_views(owner.directory)['facts'] == [fact]
    monkeypatch.setattr(owner.native, '_replace_fact_index', original)
    await owner.replay.areconcile('Neko')
    assert fact in read_views(owner.directory)['facts']
    assert await owner.native.consume(UID)
    assert owner.native._capture('Neko', False)


@pytest.mark.asyncio
async def test_frozen_operation_retries_identical_bytes_after_receipt_loss(owner):
    assert owner.native._capture('Neko', True)
    sync = owner.runtime.long_term_memory
    requests = []
    attempts = 0
    async def request(method, path, body):
        nonlocal attempts
        if method == 'PUT':
            requests.append(body)
            attempts += 1
            value = mutation(json.loads(body))
            if attempts == 1:
                raise OSError('lost receipt')
            return {'ownerId': OWNER, 'characterSyncId': UID, 'operationId': value['operationId'], 'revision': 1, 'accepted': True}
        return {'ownerId': OWNER, 'characterSyncId': UID, 'revision': 0, 'completedAt': None}
    async def commit(fn, *args):
        return fn(*args)
    from services.chat_sync.ledger import ChatDomain
    domain = ChatDomain(OWNER, UID)
    args = (domain, owner.binding, owner.runtime.writer.epoch(), request, commit)
    with pytest.raises(OSError):
        await sync.sync(*args, can_apply=lambda: True, session_fingerprint=(await owner.runtime._session())[1])
    await sync.sync(*args, can_apply=lambda: True, session_fingerprint=(await owner.runtime._session())[1])
    assert requests[0] == requests[1]
    assert b'time_indexed' not in requests[0]
    assert isinstance(sync, LongTermMemorySync)


@pytest.mark.asyncio
async def test_runtime_uses_bound_long_term_lane_and_keeps_raw_chat_endpoints_idle(owner, monkeypatch):
    assert owner.native._capture('Neko', True)
    local = owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())
    requests = []
    async def request(value):
        if value.url.path.endswith('/long-term-memory'):
            requests.append(value)
            assert value.headers['X-Neko-Chat-Binding'] == owner.binding['binding_id']
            if value.method == 'PUT':
                body = json.loads(value.content)
                assert body == {'operationId': local['operationId'], 'document': local['document']}
                return httpx.Response(200, json={'ownerId': OWNER, 'characterSyncId': UID,
                    'operationId': body['operationId'], 'accepted': True, 'revision': 1})
            return httpx.Response(200, json={'ownerId': OWNER, 'characterSyncId': UID, 'revision': 1,
                'completedAt': local['document']['completedAt'],
                **({} if value.url.query else {'document': local['document']})})
        assert value.url.path == '/v1/chat-sync/identity'
        return httpx.Response(200, json={'ownerId': OWNER})
    await owner.runtime.http.aclose()
    owner.runtime.http = httpx.AsyncClient(transport=httpx.MockTransport(request))
    async def flush(uid):
        return await owner.native.consume(uid)
    monkeypatch.setattr(owner.adapter, 'flush', flush)
    await owner.runtime._channel_pass(UID, 'long_term', manual=True)
    assert [request.method for request in requests] == ['PUT', 'GET']
    await owner.runtime._channel_pass(UID, 'long_term', manual=True)
    assert [request.method for request in requests] == ['PUT', 'GET', 'GET']
    assert owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())['operationId'] == local['operationId']


@pytest.mark.asyncio
async def test_pending_capture_finishes_before_incoming_replacement(owner):
    await stage(owner)
    marker = asyncio.create_task(asyncio.sleep(10))
    owner.native._capture_tasks['Neko'] = marker
    try:
        assert not await owner.native.consume(UID)
        assert owner.adapter.incoming_path(UID).exists()
    finally:
        owner.native._capture_tasks.pop('Neko', None)
        marker.cancel()
        await asyncio.gather(marker, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('before_first_step', [True, False])
async def test_character_release_drains_capture_and_removes_pending_request(owner, before_first_step):
    from app.memory_server.post_turn import cancel_character_post_turn_tasks
    owner.native.queue_capture('Neko', force=True)
    if not before_first_step:
        await asyncio.sleep(0)
    assert await cancel_character_post_turn_tasks('Neko') == 1
    assert 'Neko' not in owner.native._capture_tasks
    assert 'Neko' not in owner.native._capture_requests
    assert owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome,retrying,force', [
    ('renamed', False, True), ('rollback', False, True), ('renamed-with-reused-name', False, True),
    ('renamed', True, True), ('rollback', True, True), ('renamed-with-reused-name', True, True),
    ('renamed', False, False), ('deleted', False, True), ('deleted', True, True),
    ('renamed-with-requeue', False, True),
])
async def test_capture_intent_survives_rename_or_rollback_reload(owner, monkeypatch, outcome, retrying, force):
    from app.memory_server import post_turn, review
    rt = owner.native.runtime
    # Keep production reload coordination, using only disposable owner managers.
    for name in ('recent_history_manager', 'settings_manager', 'time_manager', 'cursor_store', 'outbox', 'fact_dedup_resolver'):
        monkeypatch.setattr(rt, name, None)
    monkeypatch.setattr(rt, 'CompressedRecentHistoryManager', lambda: SimpleNamespace())
    monkeypatch.setattr(rt, 'ImportantSettingsManager', lambda: SimpleNamespace())
    monkeypatch.setattr(rt, 'TimeIndexedMemory', lambda *args, **kwargs: SimpleNamespace())
    for constructor, instance in [('FactStore', rt.fact_store), ('PersonaManager', rt.persona_manager),
                                  ('ReflectionEngine', rt.reflection_engine)]:
        monkeypatch.setattr(rt, constructor, lambda *args, _instance=instance, **kwargs: _instance)
    monkeypatch.setattr(rt, 'CursorStore', lambda: SimpleNamespace())
    monkeypatch.setattr(rt, 'Outbox', lambda: SimpleNamespace())
    monkeypatch.setattr(rt, '_register_evidence_handlers', lambda *args: None)
    loop = asyncio.get_running_loop()
    attempted, published = asyncio.Event(), asyncio.Event()
    calls = []
    original = owner.native._capture
    def capture(name, force, *args):
        calls.append((name, force))
        if retrying and len(calls) == 1:
            loop.call_soon_threadsafe(attempted.set)
            raise OSError('temporary snapshot staging failure')
        result = original(name, force, *args)
        if result:
            loop.call_soon_threadsafe(published.set)
        return result
    monkeypatch.setattr(owner.native, '_capture', capture)
    current_name = 'Neko' if outcome in ('rollback', 'deleted') else 'Renamed'
    try:
        if not force:
            atomic_write_json(owner.directory / 'facts.json', [{'id': 'new', 'entity': 'master', 'text': 'fresh observation'}])
        owner.native.queue_capture('Neko', force=force)
        if retrying:
            await asyncio.wait_for(attempted.wait(), 2)
        await review.cancel_character_derived_tasks('Neko', hold_until_publication=True, claim_token='capture-rename')
        assert await post_turn.cancel_character_post_turn_tasks('Neko') == 1
        if current_name != 'Neko':
            owner.characters['猫娘'][current_name] = owner.characters['猫娘'].pop('Neko')
            owner.directory.rename(owner.config.memory_dir / current_name)
        if outcome == 'renamed-with-reused-name':
            owner.characters['猫娘']['Neko'] = {'_reserved': {'character_uid': 'b' * 32}}
        if outcome == 'renamed-with-requeue':
            owner.native.queue_capture(current_name)
        if outcome == 'deleted':
            owner.characters['猫娘'].pop('Neko')
        if outcome == 'rollback':
            def fail_reload():
                raise OSError('reload failed before rollback recovery')
            with monkeypatch.context() as temporary:
                temporary.setattr(rt, 'CompressedRecentHistoryManager', fail_reload)
                assert not await rt.reload_memory_components()
            assert owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch()) is None
        assert await rt.reload_memory_components(
            resume_derived_task_names={current_name} if current_name != 'Neko' else set(),
            release_derived_task_claims={'Neko': {'capture-rename'}} if outcome == 'rollback' else {},
        )
        if outcome == 'deleted':
            await asyncio.sleep(.3)
            assert not published.is_set()
            assert UID not in owner.native._capture_intents
            assert owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch()) is None
            return
        await asyncio.wait_for(published.wait(), 2)
        assert calls[-1] == (current_name, force)
        assert owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch()) is not None
    finally:
        await review.release_character_derived_task_admission_claim('Neko', 'capture-rename')
        await review.resume_character_derived_task_admission('Neko')
        await review.resume_character_derived_task_admission('Renamed')


@pytest.mark.asyncio
async def test_character_release_fence_blocks_incoming_native_writes(owner, monkeypatch):
    monkeypatch.setattr(owner.native.runtime, '_is_character_publication_admitted', lambda _: False)
    await stage(owner)
    before = (owner.directory / 'persona.json').read_bytes()
    with pytest.raises(ValueError, match='character_release_in_progress'):
        await owner.native.consume(UID)
    assert (owner.directory / 'persona.json').read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('peer,headers,status', [
    ('127.0.0.1', [], 200), ('::1', [], 200), ('192.0.2.1', [], 403),
    ('127.0.0.1', [(b'x-forwarded-for', b'192.0.2.1')], 403),
])
async def test_owner_wake_endpoint_accepts_only_direct_local_requests(owner, peer, headers, status, monkeypatch):
    from fastapi import HTTPException, Request
    from app.memory_server.routes import apply_synced_long_term_memory
    from unittest.mock import AsyncMock
    consume = AsyncMock(return_value=True)
    monkeypatch.setattr(owner.native, 'consume', consume)
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': headers, 'client': (peer, 1234)})
    if status == 200:
        assert await apply_synced_long_term_memory(UID, request) == {'applied': True}
        consume.assert_awaited_once_with(UID)
    else:
        with pytest.raises(HTTPException) as error:
            await apply_synced_long_term_memory(UID, request)
        assert error.value.status_code == status
        consume.assert_not_called()


@pytest.mark.parametrize('status', ['pending', 'denied', 'superseded', 'retracted'])
def test_replacement_retains_reflections_outside_export_scope(status):
    existing = {'persona': {}, 'facts': [], 'archive': [], 'reflections': [
        {'id': 'local-only', 'entity': 'master', 'text': 'local workflow', 'status': status},
        {'id': 'old-confirmed', 'entity': 'master', 'text': 'old learned state', 'status': 'confirmed'}]}
    result = import_document(learned(), existing)
    assert result['reflections'][0] == existing['reflections'][0]
    assert [row['id'] for row in result['reflections']] == ['local-only', 'r1']
    existing['reflections'][0].update(id='r1', entity='neko')
    assert len(import_document(learned(), existing)['reflections']) == 1


def test_confirmation_clock_round_trips_and_legacy_snapshot_starts_a_fresh_window():
    created = datetime.now() - timedelta(days=60)
    confirmed = datetime.now() - timedelta(days=1)
    views = {'persona': {}, 'facts': [], 'archive': [], 'reflections': [
        {'id': 'r1', 'entity': 'neko', 'text': 'recently confirmed', 'status': 'confirmed',
         'created_at': created.isoformat(), 'confirmed_at': confirmed.isoformat()}]}
    completed = int(datetime.now().timestamp() * 1000)
    result = import_document(export_document(views, completed), views)['reflections'][0]
    assert abs((datetime.fromisoformat(result['confirmed_at']) - confirmed).total_seconds()) < .002
    legacy = import_document(learned(completed), views)['reflections'][0]
    assert abs(datetime.fromisoformat(legacy['confirmed_at']).timestamp() * 1000 - completed) < .002


@pytest.mark.parametrize('field', ['createdAt', 'eventAt', 'promotedAt', 'evidenceReviewedAt'])
@pytest.mark.parametrize('value', [9_007_199_254_740_991, -9_007_199_254_740_991])
def test_protocol_rejects_unrepresentable_calendar_timestamps(field, value):
    incoming = learned()
    incoming['entries'][0]['metadata'][field] = value
    with pytest.raises(ValueError, match='invalid_long_term_memory'):
        document(incoming)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['logout', 'switch-account', 'missing-session'])
async def test_staged_snapshot_and_event_replay_recheck_login_session(owner, change):
    await stage(owner)
    payload = json.loads(owner.adapter.incoming_path(UID).read_text())
    before = {key: (owner.directory / filename).read_bytes() for key, filename in FILES.items()}
    if change == 'logout':
        owner.session['logged_in'] = False
    elif change == 'switch-account':
        owner.session['snapshot']['access_token'] = 'synthetic-other-account'
    else:
        payload.pop('sessionFingerprint')
        atomic_write_json(owner.adapter.incoming_path(UID), payload)
    assert not await owner.native.consume(UID)
    assert owner.native._write_snapshot('Neko', payload) is False
    assert before == {key: (owner.directory / filename).read_bytes() for key, filename in FILES.items()}


@pytest.mark.asyncio
async def test_failed_capture_retries_and_retains_force_request(owner, monkeypatch):
    original = owner.native._capture
    attempted, completed = asyncio.Event(), asyncio.Event()
    calls = []
    loop = asyncio.get_running_loop()
    def fail_once(name, force, expected_uid=None):
        calls.append(force)
        if len(calls) == 1:
            loop.call_soon_threadsafe(attempted.set)
            raise OSError('synthetic temporary staging failure')
        result = original(name, force, expected_uid)
        loop.call_soon_threadsafe(completed.set)
        return result
    monkeypatch.setattr(owner.native, '_capture', fail_once)
    owner.native.queue_capture('Neko', force=True)
    await asyncio.wait_for(attempted.wait(), 2)
    owner.native.queue_capture('Neko')
    await asyncio.wait_for(completed.wait(), 4)
    assert calls == [True, True]
    assert owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch()) is not None


@pytest.mark.asyncio
async def test_snapshot_replaces_only_fact_search_index_and_replay_repairs_failed_index(owner, monkeypatch):
    from memory import timeindex
    from unittest.mock import MagicMock
    config = MagicMock(memory_dir=str(owner.config.memory_dir))
    config.get_character_data.return_value = ('Master', 'Neko', {}, {}, {}, {}, {}, {}, {})
    monkeypatch.setattr(timeindex, 'get_config_manager', lambda: config)
    monkeypatch.setattr(timeindex, 'assert_cloudsave_writable', lambda *args, **kwargs: None)
    owner.raw.unlink()  # Disposable fixture placeholder; build a real archive for this test.
    index = timeindex.TimeIndexedMemory(recent_history_manager=None)
    owner.native.runtime.fact_store._time_indexed = index
    index.backfill_fact_index('Neko', [('old', 'obsolete private preference')])
    from config import TIME_ORIGINAL_TABLE_NAME
    from utils.llm_client import HumanMessage
    index.store_conversation_strict('synthetic-session', [HumanMessage(content='raw history remains')], 'Neko')
    from sqlalchemy import event, text
    with index.engines['Neko'].connect() as connection:
        raw_before = connection.execute(text(f'SELECT * FROM {TIME_ORIGINAL_TABLE_NAME}')).fetchall()
    await stage(owner)
    def fail_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith(f'INSERT INTO {index.FACTS_FTS_TABLE}'):
            raise OSError('synthetic index failure after DELETE')
    event.listen(index.engines['Neko'], 'before_cursor_execute', fail_insert)
    with pytest.raises(ValueError, match='long_term_memory_save_failed'):
        await owner.native.consume(UID)
    assert owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch()) is None
    assert 'old' in dict(index.search_similar_facts('Neko', 'obsolete private preference'))
    event.remove(index.engines['Neko'], 'before_cursor_execute', fail_insert)
    await owner.replay.areconcile('Neko')
    assert 'f1' in dict(index.search_similar_facts('Neko', 'shared experience'))
    assert not index.search_similar_facts('Neko', 'obsolete private preference')
    index.replace_fact_index('Neko', [('old', 'obsolete private preference')])
    payload = json.loads(owner.adapter.incoming_path(UID).read_text())
    assert owner.native._write_snapshot('Neko', payload)
    assert 'f1' in dict(index.search_similar_facts('Neko', 'shared experience'))
    assert not index.search_similar_facts('Neko', 'obsolete private preference')
    with index.engines['Neko'].connect() as connection:
        assert connection.execute(text(f'SELECT * FROM {TIME_ORIGINAL_TABLE_NAME}')).fetchall() == raw_before
    for engine in index.engines.values():
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('accepted,newer', [(True, False), (True, True), (False, False)])
async def test_upload_receipt_skips_own_head_but_pulls_a_rejected_or_newer_remote(owner, accepted, newer):
    from services.chat_sync.ledger import ChatDomain
    assert owner.native._capture('Neko', True)
    local = owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())
    calls = []
    revision = 2 if newer else 1
    remote = {'ownerId': OWNER, 'characterSyncId': UID, 'revision': revision,
              'completedAt': local['document']['completedAt'] + 1,
              'document': learned(local['document']['completedAt'] + 1, 'remote learned state')}
    async def request(method, path, body):
        calls.append((method, path))
        if method == 'PUT':
            return {'ownerId': OWNER, 'characterSyncId': UID, 'operationId': local['operationId'],
                    'revision': 1, 'accepted': accepted}
        return {key: value for key, value in remote.items() if key != 'document'} if '?' in path else remote
    async def commit(fn, *args):
        return fn(*args)
    domain = ChatDomain(OWNER, UID)
    result = await owner.runtime.long_term_memory.sync(domain, owner.binding, owner.runtime.writer.epoch(),
        request, commit, can_apply=lambda: True, session_fingerprint=(await owner.runtime._session())[1])
    assert result['changed'] is (newer or not accepted)
    assert len(calls) == (3 if newer or not accepted else 2)
    assert owner.runtime.long_term_memory.state(domain.key()) == {'acknowledged': local['operationId'], 'revision': revision}


@pytest.mark.asyncio
async def test_imported_recent_confirmation_does_not_trigger_weak_memory_promotion():
    from memory.reflection import ReflectionEngine
    from unittest.mock import AsyncMock
    confirmed = datetime.now() - timedelta(days=1)
    views = {'persona': {}, 'facts': [], 'archive': [], 'reflections': [
        {'id': 'r1', 'entity': 'neko', 'text': 'recently confirmed', 'status': 'confirmed',
         'created_at': (confirmed - timedelta(days=60)).isoformat(), 'confirmed_at': confirmed.isoformat()}]}
    restored = import_document(export_document(views, int(datetime.now().timestamp() * 1000)), views)['reflections']
    engine = object.__new__(ReflectionEngine)
    engine._alocks, engine._alocks_guard = {}, threading.Lock()
    engine._aload_reflections_full = AsyncMock(return_value=restored)
    engine._persona_manager = SimpleNamespace(aadd_fact=AsyncMock())
    assert await engine.aauto_promote_time_driven('Neko') == 0
    engine._persona_manager.aadd_fact.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_timestamp_is_rejected_before_journaling_and_old_invalid_event_is_skipped(owner):
    await stage(owner)
    payload = json.loads(owner.adapter.incoming_path(UID).read_text())
    payload['head']['document']['entries'][0]['metadata']['createdAt'] = 9_007_199_254_740_991
    atomic_write_json(owner.adapter.incoming_path(UID), payload)
    events_path = owner.directory / 'events.ndjson'
    assert not events_path.exists()
    assert not await owner.native.consume(UID)
    assert not events_path.exists()
    assert owner.native._write_snapshot('Neko', payload) is False
    def fail_save(name, frozen):
        raise OSError('synthetic historical apply failure')
    with pytest.raises(OSError):
        owner.native.runtime.event_log.record_and_save('Neko', owner.native._EVENT, payload,
            sync_load_view=lambda _: deepcopy(payload), sync_mutate_view=lambda _: None, sync_save_view=fail_save)
    assert await owner.replay.areconcile('Neko') == 0
    event_id = json.loads(events_path.read_text().splitlines()[-1])['event_id']
    assert owner.native.runtime.event_log.read_sentinel('Neko') == event_id
    # A later valid completion can still be consumed.
    await stage(owner, completed=3000, revision=2)
    assert await owner.native.consume(UID)


@pytest.mark.asyncio
async def test_native_apply_holds_the_logout_writers_fence_until_commit(owner, monkeypatch):
    from contextlib import contextmanager
    from main_routers import card_drop_router  # noqa: F401
    from services.chat_sync import host
    from services.chat_sync.runtime import _saved_session_commit_guard
    lock = threading.Lock()
    @contextmanager
    def fence():
        with lock:
            yield
    monkeypatch.setattr(host, 'saved_session_writer_fence', fence)
    monkeypatch.setattr(host, 'saved_session_snapshot',
                        lambda: owner.session['snapshot'] if owner.session['logged_in'] else None)
    monkeypatch.setattr(owner.native, '_session_guard', _saved_session_commit_guard)
    await stage(owner)
    payload = json.loads(owner.adapter.incoming_path(UID).read_text())
    writing, release, logout_attempted, logged_out = (threading.Event() for _ in range(4))
    original = owner.native.atomic_write_json
    def paused_write(path, *args, **kwargs):
        if path.name == 'persona.json':
            writing.set()
            assert release.wait(5)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(owner.native, 'atomic_write_json', paused_write)
    def logout():
        logout_attempted.set()
        with lock:
            owner.session['logged_in'] = False
            logged_out.set()
    applying = asyncio.create_task(asyncio.to_thread(owner.native._write_snapshot, 'Neko', payload))
    logout_task = None
    try:
        assert await asyncio.to_thread(writing.wait, 2)
        logout_task = asyncio.create_task(asyncio.to_thread(logout))
        assert await asyncio.to_thread(logout_attempted.wait, 2)
        assert not logged_out.is_set()
    finally:
        release.set()
        assert await applying
        if logout_task is not None:
            await logout_task
    assert logged_out.is_set()
    assert owner.native._write_snapshot('Neko', payload) is False


@pytest.mark.asyncio
async def test_new_session_redownloads_a_staged_revision_before_applying_it(owner):
    from services.chat_sync.ledger import ChatDomain
    await stage(owner)
    sync = owner.runtime.long_term_memory
    domain = ChatDomain(OWNER, UID)
    sync.applied(domain.key(), 1)
    owner.session['logged_in'] = False
    assert not await owner.native.consume(UID)
    assert owner.adapter.incoming_path(UID).exists()
    owner.session['logged_in'] = True
    owner.session['snapshot']['access_token'] = 'synthetic-new-session'
    remote = {'ownerId': OWNER, 'characterSyncId': UID, 'revision': 1,
              'completedAt': 2000, 'document': learned()}
    calls = []
    async def request(method, path, body):
        calls.append((method, path))
        assert method == 'GET'
        return {key: value for key, value in remote.items() if key != 'document'} if '?' in path else remote
    async def commit(fn, *args):
        return fn(*args)
    assert (await sync.sync(domain, owner.binding, owner.runtime.writer.epoch(), request, commit,
        can_apply=lambda: True, session_fingerprint=(await owner.runtime._session())[1]))['changed']
    assert len(calls) == 2
    assert await owner.native.consume(UID)
    assert read_views(owner.directory)['facts'][0]['text'] == 'shared experience'
