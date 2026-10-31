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


def test_source_projection_uses_original_chronology_without_mutating_full_proof():
    ids = ['newest'] + [f'old-{index}' for index in range(200)] + ['unknown', 'wrong-role']
    native = {'facts': [{'id': 'fact', 'entity': 'master', 'text': 'Prefers tea',
                         'source': 'user_observation', 'source_message_ids': ids}], 'persona': {}, 'reflections': [], 'archive': []}
    evidence = {'newest': ('human', 1000), 'wrong-role': ('ai', 2000), 'unknown': ('human', None),
                **{f'old-{index}': ('human', index) for index in range(200)}}
    snapshot = export_document(native, 3000, source_evidence=evidence)
    assert snapshot['entries'][0]['metadata']['sourceMessageIds'] == ids[2:201] + ['newest']
    assert snapshot['entries'][0]['metadata']['sourceCreatedAt'] == 1000
    assert native['facts'][0]['source_message_ids'] == ids
    assert 'source_created_at' not in native['facts'][0]
    restored = import_document(snapshot, native, source_evidence=evidence)
    assert restored['facts'][0]['source_message_ids'] == ids
    assert 'source_created_at' not in restored['facts'][0]
    assert export_document(restored, 3000, source_evidence=evidence) == snapshot


def test_source_projection_refuses_uncommitted_arbitration_handoff():
    native = {'persona': {}, 'reflections': [], 'facts': [{'id': 'fact', 'entity': 'master', 'text': 'Prefers tea',
                         '_near_dup_pending': {'existing_id': 'anchor', 'text_overlap': 0.9}}]}
    with pytest.raises(ValueError, match='pending'):
        export_document(native, 3000)


@pytest.mark.parametrize('change', ['confirmation', 'unknown_time', 'correction', 'source_upgrade'])
def test_source_projection_preserves_only_compatible_local_evidence(change):
    ids = [f'proof-{index}' for index in range(201)]
    previous = {'id': 'same', 'entity': 'master', 'text': 'Prefers tea', 'source_message_ids': ids,
                'source_created_at': '1970-01-01T00:00:00+00:00',
                'source': 'ai_disclosure' if change == 'source_upgrade' else 'user_observation'}
    native = {'persona': {}, 'facts': [previous], 'archive': [], 'reflections': []}
    incoming = export_document(native, 2000)
    row = incoming['entries'][0]
    row['metadata']['sourceMessageIds'] = ['new-proof']
    row['metadata']['source'] = 'user_observation'
    if change != 'unknown_time': row['metadata']['sourceCreatedAt'] = 1000
    if change == 'correction': row['text'] = 'Prefers coffee'
    received = import_document(incoming, native)['facts'][0]
    assert received['source_message_ids'] == (['new-proof'] if change in ('correction', 'source_upgrade') else ids + ['new-proof'])
    if change == 'unknown_time': assert 'source_created_at' not in received
    else:
        from memory.long_term_sync import timestamp
        assert timestamp(received['source_created_at']) == 1000
    assert native['facts'][0] == previous


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


@pytest.mark.parametrize('offset', [0, 8, -5])
@pytest.mark.parametrize('milliseconds', [-1000, 0, 1234])
def test_windows_iso_conversion_preserves_dates_outside_localtime_range(monkeypatch, offset, milliseconds):
    from memory import long_term_sync
    local = timezone(timedelta(hours=offset))
    class WindowsDatetime(datetime):
        def astimezone(self, tz=None):
            if tz is None:
                raise OSError(22, 'Windows localtime is outside the CRT range')
            return super().astimezone(tz)
        def timestamp(self):
            if self.tzinfo is None:
                raise OSError(22, 'Windows mktime is outside the CRT range')
            return super().timestamp()
    monkeypatch.setitem(sys.modules, 'win32timezone', SimpleNamespace(TimeZoneInfo=SimpleNamespace(local=lambda: local)))
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(long_term_sync, 'datetime', WindowsDatetime)
    expected = (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=milliseconds)).astimezone(local).replace(tzinfo=None).isoformat()
    assert long_term_sync.iso(milliseconds) == expected
    assert long_term_sync.timestamp(expected) == milliseconds


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
async def test_owner_uses_the_same_original_chronology_for_capture_and_apply(owner):
    ids = ['newest'] + [f'old-{index}' for index in range(200)]
    proof = {'newest': ('human', 1000), **{f'old-{index}': ('human', index) for index in range(200)}}
    owner.native.runtime.fact_store._time_indexed = SimpleNamespace(
        get_original_source_evidence=lambda name, requested: {key: proof[key] for key in requested},
        replace_fact_index=lambda *args: None)
    atomic_write_json(owner.directory / FILES['facts'], [{'id': 'fact', 'entity': 'master', 'text': 'Prefers tea',
                                                        'source': 'user_observation', 'source_message_ids': ids}])
    assert owner.native._capture('Neko', True)
    local = owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())
    assert local['document']['entries'][0]['metadata']['sourceMessageIds'] == ids[2:] + ['newest']
    assert owner.native._capture('Neko', False) is False
    remote = {'ownerId': OWNER, 'characterSyncId': UID, 'revision': 1,
              'completedAt': local['document']['completedAt'] + 1,
              'document': {**local['document'], 'completedAt': local['document']['completedAt'] + 1}}
    assert owner.native._write_snapshot('Neko', {'binding': owner.binding, 'epoch': list(owner.runtime.writer.epoch()), 'head': remote,
                                                'sessionFingerprint': (await owner.runtime._session())[1]})
    assert read_views(owner.directory)['facts'][0]['source_message_ids'] == ids
    assert owner.native._capture('Neko', False) is False


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
    with pytest.raises(Exception, match='long_term_memory_recovery_required'):
        read_views(owner.directory)
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
@pytest.mark.parametrize('unpublished', [False, True])
async def test_queued_capture_incorporates_downloaded_snapshot_before_publishing(owner, unpublished):
    owner.native._capture('Neko', True)
    previous = owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())
    if unpublished:
        atomic_write_json(owner.directory / FILES['facts'], [
            {'id': 'local-only', 'text': 'new local independent fact', 'entity': 'master', 'importance': 7}])
    await stage(owner, completed=previous['document']['completedAt'] + 1)
    raw = owner.raw.read_bytes()

    owner.native.queue_capture('Neko', force=True)
    await asyncio.wait_for(asyncio.shield(owner.native._capture_tasks['Neko']), 3)

    published = owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())['document']
    assert {entry['id'] for entry in published['entries']} == {'p1', 'f1', 'r1'} | ({'local-only'} if unpublished else set())
    assert not owner.adapter.incoming_path(UID).exists()
    assert owner.raw.read_bytes() == raw


@pytest.mark.asyncio
async def test_snapshot_arriving_between_consume_and_capture_is_not_overwritten(owner, monkeypatch):
    original = owner.native.consume
    arrived = False
    async def consume_then_download(uid):
        nonlocal arrived
        result = await original(uid)
        if not arrived:
            arrived = True
            await stage(owner, completed=1000)
        return result
    monkeypatch.setattr(owner.native, 'consume', consume_then_download)
    owner.native.queue_capture('Neko', force=True)
    await asyncio.wait_for(asyncio.shield(owner.native._capture_tasks['Neko']), 3)
    published = owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())['document']
    assert {entry['id'] for entry in published['entries']} == {'p1', 'f1', 'r1'}
    assert not owner.adapter.incoming_path(UID).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('review_lane', ['correction_tasks', 'compress_backup_tasks'])
async def test_capture_waits_for_review_without_superseding_downloaded_facts(owner, monkeypatch, review_lane):
    from app.memory_server import review
    await stage(owner)
    blocker = asyncio.create_task(asyncio.Event().wait())
    monkeypatch.setattr(review, review_lane, {'Neko': blocker})
    try:
        owner.native.queue_capture('Neko', force=True)
        await asyncio.sleep(.3)
        assert owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch()) is None
        assert owner.adapter.incoming_path(UID).exists()
        blocker.cancel()
        await asyncio.gather(blocker, return_exceptions=True)
        await asyncio.wait_for(asyncio.shield(owner.native._capture_tasks['Neko']), 3)
        published = owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())['document']
        assert {entry['id'] for entry in published['entries']} == {'p1', 'f1', 'r1'}
    finally:
        blocker.cancel()
        await asyncio.gather(blocker, return_exceptions=True)


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
    def fail_once(name, force, expected_uid=None, require_consumed=False):
        calls.append(force)
        if len(calls) == 1:
            loop.call_soon_threadsafe(attempted.set)
            raise OSError('synthetic temporary staging failure')
        result = original(name, force, expected_uid, require_consumed)
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
    # Rollback repairs the derived index from the empty native before-image.
    assert not index.search_similar_facts('Neko', 'obsolete private preference')
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


def test_v2_pending_reflection_stays_pending_and_explicit_empty_replaces_old_pending():
    existing = {'persona': {}, 'facts': [], 'archive': [], 'reflections': []}
    value = learned()
    value.update(schemaVersion=2, evidence={'retirements': [], 'confirmations': []})
    value['entries'][-1]['metadata']['status'] = 'pending'
    native = import_document(value, existing)
    assert native['reflections'][0]['status'] == 'pending'
    assert not native['reflections'][0].get('confirmed_at')
    exported = export_document(native, 3000)
    assert 'confirmedAt' not in exported['entries'][-1]['metadata']
    cleared = import_document({**value, 'entries': []}, native)
    assert not cleared['reflections']


def test_v2_preserves_retired_entries_and_receiver_replay_fences():
    value = learned()
    value.update(schemaVersion=2, evidence={'retirements': [], 'confirmations': []})
    value['entries'][1]['metadata'].update(status='retracted', source='user_observation', eventStartAt=0)
    existing = {'persona': {}, 'facts': [], 'archive': [], 'reflections': [], 'sync': {'evidence': {
        'retirements': [{'id': 'old', 'subject': 'master', 'sourceMessageIds': ['old-source']}],
        'confirmations': [{'fingerprint': '1:2:3', 'factId': 'old'}]}}}
    native = import_document(value, existing)
    assert native['facts'] == []
    snapshot = export_document(native, 3000)
    assert snapshot['evidence'] == existing['sync']['evidence']
    retired = next(row for row in snapshot['entries'] if row['id'] == 'f1')
    assert retired['metadata']['status'] == 'retracted' and retired['metadata']['eventStartAt'] == 0


@pytest.mark.parametrize('kind,status', [('reflection', 'denied'), ('reflection', 'merged'), ('fact', 'retracted')])
@pytest.mark.parametrize('remote_change', ['same', 'changed', 'absent'])
def test_pending_dormant_history_survives_snapshot_without_receipt_baseline(kind, status, remote_change):
    row = {'kind': kind, 'subject': 'master', 'id': 'history', 'text': 'local historical assertion',
           'metadata': {'status': status, 'createdAt': 1000, 'updatedAt': 2000,
                        'sourceFactIds' if kind == 'reflection' else 'sourceMessageIds': ['original-proof']}}
    seed = {'schemaVersion': 2, 'completedAt': 2000, 'entries': [row],
            'evidence': {'retirements': [], 'confirmations': []}}
    existing = import_document(seed, {'persona': {}, 'facts': [], 'archive': [], 'reflections': []})
    before = deepcopy(existing)
    incoming = export_document(existing, 3000)
    if remote_change == 'changed': incoming['entries'][0]['text'] = 'remote historical assertion'
    if remote_change == 'absent': incoming['entries'] = []

    received = import_document(incoming, existing, baseline=[])
    assert received['sync']['dormantEntries'] == [row]
    assert existing == before
    assert received['facts'] == received['archive'] == received['reflections'] == []
    assert export_document(received, 4000)['entries'] == [row]
    repeated = import_document(incoming, received, baseline=[])
    assert repeated['sync']['dormantEntries'] == [row]
    assert export_document(repeated, 5000)['entries'] == [row]


@pytest.mark.parametrize('baseline_mode,expected_texts', [
    ('none', ['remote revision', 'remote revision', 'remote revision', 'remote revision']),
    ('empty', ['unpublished local history', 'local history', 'local history', 'local history']),
    ('previous', ['unpublished local history', 'remote revision', 'remote revision', 'remote revision']),
])
def test_pending_dormant_history_respects_baseline_and_full_entry_identity(baseline_mode, expected_texts):
    identities = [('reflection', 'master', 'shared'), ('reflection', 'neko', 'shared'),
                  ('reflection', 'master', 'other'), ('persona', 'master', 'shared')]
    seed = {'schemaVersion': 2, 'completedAt': 2000, 'evidence': {'retirements': [], 'confirmations': []},
            'entries': [{'kind': kind, 'subject': subject, 'id': identity, 'text': 'local history',
                         'metadata': {'status': 'denied'}} for kind, subject, identity in identities]}
    existing = import_document(seed, {'persona': {}, 'facts': [], 'archive': [], 'reflections': []})
    previous = export_document(existing, 2000)['entries']
    existing['sync']['dormantEntries'][0]['text'] = 'unpublished local history'
    incoming = deepcopy(seed)
    incoming['completedAt'] = 3000
    for row in incoming['entries']:
        row['text'] = 'remote revision'
        row['metadata']['status'] = 'active' if row['kind'] == 'persona' else 'confirmed'
    baseline = {'none': None, 'empty': [], 'previous': previous}[baseline_mode]

    received = import_document(incoming, existing, baseline=baseline)
    exported = export_document(received, 4000)['entries']
    by_key = {(row['kind'], row['subject'], row['id']): row['text'] for row in exported}
    assert len(exported) == 4
    assert [by_key[key] for key in identities] == expected_texts
    dormant = received['sync']['dormantEntries']
    assert len(dormant) == {'none': 0, 'empty': 4, 'previous': 1}[baseline_mode]
    assert all(row['metadata']['status'] == 'denied' for row in dormant)


def test_pending_dormant_fact_keeps_retirement_assertion_after_reexport(tmp_path):
    from memory.long_term_sync import fact_source_evidence, retirement_evidence
    row = {'kind': 'fact', 'subject': 'master', 'id': 'retired', 'text': 'retired assertion',
           'metadata': {'status': 'retracted', 'sourceMessageIds': ['old-source']}}
    seed = {'schemaVersion': 2, 'completedAt': 2000, 'entries': [row], 'evidence': {
        'retirements': [{'id': 'retired', 'subject': 'master', 'sourceMessageIds': ['old-source']}],
        'confirmations': []}}
    existing = import_document(seed, {'persona': {}, 'facts': [], 'archive': [], 'reflections': []})
    incoming = {'schemaVersion': 2, 'completedAt': 3000, 'entries': [],
                'evidence': {'retirements': [], 'confirmations': []}}
    received = import_document(incoming, existing, baseline=[])
    received = import_document(export_document(received, 4000), received, baseline=[])
    atomic_write_json(tmp_path / FILES['sync'], received['sync'])
    retirements = retirement_evidence(tmp_path)
    assert retirements == [{'id': 'retired', 'subject': 'master', 'text': 'retired assertion',
                            'sourceMessageIds': ['old-source']}]
    message = SimpleNamespace(type='human', additional_kwargs={
        'memory_source_id': 'old-source', 'memory_source_created_at': 1000})
    candidate = {'text': 'retired assertion', 'source_indices': [1]}
    assert fact_source_evidence(retirements, candidate, 'master', [message]) is None
    assert fact_source_evidence(retirements, candidate, 'neko', [message])['source_message_ids'] == ['old-source']


@pytest.mark.parametrize('survivor', [None, 'survivor'])
def test_arbitrated_fact_roundtrip_preserves_recall_exclusion_and_manual_restore(survivor):
    from memory.long_term_sync import timestamp
    loser = {'id': 'loser', 'text': 'a duplicate assertion', 'entity': 'master', 'importance': 7,
             'source': 'user_observation', 'source_message_ids': ['original-user'],
             'source_created_at': '1970-01-01T00:00:00+00:00',
             'arbitration_archived_at': '2026-10-28T00:00:00+00:00', 'arbitration_reason': 'dedup_arbitration'}
    if survivor:
        loser['superseded_by'] = survivor
    existing = {'persona': {}, 'facts': [], 'archive': [loser], 'reflections': []}
    portable = export_document(existing, 3000)
    metadata = portable['entries'][0]['metadata']
    assert metadata['status'] == ('superseded' if survivor else 'retracted')
    assert metadata['archived'] is True
    assert metadata['sourceMessageIds'] == ['original-user'] and metadata['sourceCreatedAt'] == 0

    received = import_document(portable, {'persona': {}, 'facts': [], 'archive': [], 'reflections': []})
    assert received['facts'] == []
    assert len(received['archive']) == 1
    restored = received['archive'][0]
    assert timestamp(restored['arbitration_archived_at']) == timestamp(loser['arbitration_archived_at'])
    assert restored.get('superseded_by') == survivor
    assert export_document(received, 4000)['entries'][0]['metadata']['status'] == metadata['status']

    # Mirror FactStore.arestore_arbitrated_fact's explicit restore markers.
    for key in ('arbitration_archived_at', 'arbitration_reason', 'superseded_by'):
        restored.pop(key, None)
    restored['arbitration_restored_at'] = '2026-10-29T00:00:00'
    received.update(facts=[restored], archive=[])
    restored_entry = export_document(received, 5000)['entries'][0]
    assert restored_entry['id'] == 'loser'
    active = restored_entry['metadata']
    assert active['archived'] is False
    assert not {'status', 'archivedAt', 'supersededById', 'arbitrationAction'} & active.keys()


@pytest.mark.parametrize('restored_at,active_wins', [
    (None, False), ('2026-10-27T00:00:00', False), ('2026-10-29T00:00:00', True),
    ('2026-10-26T00:00:00', True),
])
@pytest.mark.parametrize('legacy_duplicate', [False, True])
def test_interrupted_arbitration_export_respects_archive_and_later_explicit_restore(restored_at, active_wins, legacy_duplicate):
    active = {'id': 'loser', 'entity': 'master', 'text': 'duplicate assertion', 'importance': 7,
              'source_message_ids': ['original'], 'source_created_at': '1970-01-01T00:00:00+00:00'}
    archived = {**active, 'arbitration_archived_at': '2026-10-28T00:00:00', 'superseded_by': 'winner'}
    if restored_at:
        active['arbitration_restored_at'] = restored_at
        archived['arbitration_restored_at'] = '2026-10-27T00:00:00'
    views = {'persona': {}, 'facts': [active], 'archive': ([dict(active)] if legacy_duplicate else []) + [archived], 'reflections': []}
    before = deepcopy(views)
    portable = export_document(views, 3000)
    row = portable['entries'][0]
    assert row['id'] == 'loser' and row['metadata']['sourceCreatedAt'] == 0
    assert row['metadata']['archived'] is not active_wins
    assert row['metadata'].get('status') == (None if active_wins else 'superseded')
    native = import_document(portable, {'persona': {}, 'facts': [], 'archive': [], 'reflections': []})
    assert [fact['id'] for fact in native['facts']] == (['loser'] if active_wins else [])
    assert views == before


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['login', 'binding'])
async def test_expired_cloud_head_releases_stale_inbox_without_losing_its_bytes(owner, change):
    from services.chat_sync.ledger import ChatDomain
    await stage(owner)
    path = owner.adapter.incoming_path(UID)
    before = path.read_bytes()
    if change == 'login':
        owner.session['snapshot']['access_token'] = 'synthetic-new-login'
    else:
        await owner.runtime.pause(UID)
        await owner.runtime.enable(UID, 'Neko')
    binding = {**owner.runtime.state.binding(UID), 'service_url': 'https://sync.test'}
    owner.native.queue_capture('Neko', force=True)
    task = owner.native._capture_tasks['Neko']
    await asyncio.sleep(.3)
    assert path.exists() and not task.done()
    async def request(method, url, body):
        assert method == 'GET' and url.endswith('?metadataOnly=true')
        return {'ownerId': OWNER, 'characterSyncId': UID, 'revision': 2, 'completedAt': 2000, 'expired': True}
    async def commit(fn, *args):
        return fn(*args)
    await owner.runtime.long_term_memory.sync(ChatDomain(OWNER, UID), binding, owner.runtime.writer.epoch(), request, commit,
        can_apply=lambda: True, session_fingerprint=(await owner.runtime._session())[1])
    await asyncio.wait_for(asyncio.shield(task), 3)
    assert not path.exists()
    assert any(saved.read_bytes() == before for saved in owner.adapter.directory.glob('*.expired-*.json'))
    assert owner.adapter.read_local(binding, owner.runtime.writer.epoch()) is not None
    assert owner.raw.read_bytes() == b'synthetic raw archive remains byte-identical'


@pytest.mark.asyncio
async def test_unexpired_inbox_is_reauthenticated_before_capture(owner):
    from services.chat_sync.ledger import ChatDomain
    await stage(owner)
    owner.session['snapshot']['access_token'] = 'synthetic-new-login'
    owner.native.queue_capture('Neko', force=True)
    task = owner.native._capture_tasks['Neko']
    await asyncio.sleep(.3)
    remote = {'ownerId': OWNER, 'characterSyncId': UID, 'revision': 1, 'completedAt': 2000}
    async def request(method, url, body):
        return remote if url.endswith('?metadataOnly=true') else {**remote, 'document': learned()}
    async def commit(fn, *args):
        return fn(*args)
    await owner.runtime.long_term_memory.sync(ChatDomain(OWNER, UID), owner.binding, owner.runtime.writer.epoch(), request, commit,
        can_apply=lambda: True, session_fingerprint=(await owner.runtime._session())[1])
    await asyncio.wait_for(asyncio.shield(task), 3)
    published = owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())['document']
    assert {row['id'] for row in published['entries']} == {'p1', 'f1', 'r1'}
    assert not list(owner.adapter.directory.glob('*.expired-*.json'))


@pytest.mark.asyncio
async def test_expiry_cannot_quarantine_newer_inbox_or_commit_after_logout(owner):
    await stage(owner, completed=3000, revision=3)
    path = owner.adapter.incoming_path(UID)
    before = path.read_bytes()
    _, _, commit = await owner.runtime._bound_transport(UID)
    expired = {'ownerId': OWNER, 'characterSyncId': UID, 'revision': 2, 'completedAt': 2000, 'expired': True}
    await commit(owner.adapter.expire_incoming, owner.binding, owner.runtime.writer.epoch(), expired,
                 require_epoch=owner.runtime.writer.require_epoch)
    assert path.read_bytes() == before
    owner.session['logged_in'] = False
    with pytest.raises(ValueError):
        await commit(owner.adapter.expire_incoming, owner.binding, owner.runtime.writer.epoch(), {**expired, 'revision': 4},
                     require_epoch=owner.runtime.writer.require_epoch)
    assert path.read_bytes() == before
    assert not list(owner.adapter.directory.glob('*.expired-*.json'))


@pytest.mark.asyncio
async def test_expiry_rejects_old_writer_epoch(owner):
    await stage(owner)
    path = owner.adapter.incoming_path(UID)
    before = path.read_bytes()
    epoch = owner.runtime.writer.epoch()
    _, _, commit = await owner.runtime._bound_transport(UID)
    expired = {'ownerId': OWNER, 'characterSyncId': UID, 'revision': 2, 'completedAt': 2000, 'expired': True}
    with pytest.raises(ValueError):
        await commit(owner.adapter.expire_incoming, owner.binding, (epoch[0], 'obsolete'), expired,
                     require_epoch=owner.runtime.writer.require_epoch)
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_received_arbitration_archive_can_be_restored_by_native_fact_store(owner, monkeypatch):
    from memory import facts
    from memory.long_term_sync import view_files
    loser = {'id': 'loser', 'text': 'a duplicate assertion', 'entity': 'master', 'importance': 7,
             'arbitration_archived_at': '2026-10-28T00:00:00+00:00', 'superseded_by': 'survivor'}
    portable = export_document({'persona': {}, 'facts': [], 'archive': [loser], 'reflections': []}, 3000)
    received = import_document(portable, read_views(owner.directory))
    for relative, value in view_files(received).items():
        atomic_write_json(owner.directory / relative, value)
    monkeypatch.setattr(facts, 'get_config_manager', lambda: owner.config)
    monkeypatch.setattr(facts, 'assert_cloudsave_writable', lambda *args, **kwargs: None)
    store = facts.FactStore()
    assert await store.aget_unabsorbed_facts('Neko') == []
    assert await store.arestore_arbitrated_fact('Neko', 'loser')
    active = export_document(read_views(owner.directory), 4000)['entries'][0]
    assert active['id'] == 'loser' and active['metadata']['archived'] is False
    assert 'status' not in active['metadata']
    assert owner.raw.read_bytes() == b'synthetic raw archive remains byte-identical'


def test_native_correction_history_exports_only_portable_fields():
    views = {'persona': {'master': {'facts': [{'id': 'p1', 'text': 'new wording', 'version_history': [
        {'text': 'old wording', 'replaced_at': '2026-01-01T00:00:00+00:00', 'reason': 'correction', 'source_fact_id': None}]}]}},
        'facts': [], 'archive': [], 'reflections': []}
    version = export_document(views, 2000)['entries'][0]['metadata']['versionHistory'][0]
    assert version == {'text': 'old wording', 'replacedAt': 1767225600000, 'reason': 'correction'}


@pytest.mark.parametrize('archived', [False, True])
def test_persona_history_is_restored_for_native_readers_and_later_exports(archived):
    incoming = learned()
    entry = incoming['entries'][0]
    history = [
        {'text': 'earlier wording', 'replacedAt': 0, 'reason': 'correction'},
        {'text': 'old wording', 'replacedAt': 1000, 'reason': 'refine_modify', 'detail': 'manual revision'},
    ]
    entry['metadata']['versionHistory'] = history
    if archived:
        entry['metadata'].update(archived=True, archivedAt=1500)
    restored = import_document(incoming, {'persona': {}, 'facts': [], 'archive': [], 'reflections': []})
    native = (next(iter(restored['personaArchives'].values()))[0] if archived
              else restored['persona']['master']['facts'][0])
    assert [item['text'] for item in native['version_history']] == ['earlier wording', 'old wording']
    assert native['version_history'][0]['replaced_at'] is not None
    exported = export_document(restored, 3000)
    assert next(row for row in exported['entries'] if row['id'] == 'p1')['metadata']['versionHistory'] == history
    native['text'] = 'another native edit'
    native['version_history'].append({'text': 'learned preference', 'replaced_at': '1970-01-01T00:00:02+00:00', 'reason': 'refine_modify'})
    edited = export_document(restored, 4000)
    assert next(row for row in edited['entries'] if row['id'] == 'p1')['metadata']['versionHistory'][:2] == history


@pytest.mark.asyncio
async def test_capture_ignores_non_object_fact_and_archive_rows(owner):
    ids = [f'proof-{index}' for index in range(201)]
    proof = {identity: ('human', index) for index, identity in enumerate(ids)}
    owner.native.runtime.fact_store._time_indexed = SimpleNamespace(
        get_original_source_evidence=lambda name, requested: {key: proof[key] for key in requested})
    atomic_write_json(owner.directory / FILES['facts'], [None, 'legacy row', 7,
        {'id': 'fact', 'entity': 'master', 'text': 'Prefers tea', 'source_message_ids': ids}])
    atomic_write_json(owner.directory / FILES['archive'], [None, ['legacy row']])
    assert owner.native._capture('Neko', True)
    local = owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())
    assert local['document']['entries'][0]['id'] == 'fact'
    assert len(local['document']['entries'][0]['metadata']['sourceMessageIds']) == 200


@pytest.mark.asyncio
@pytest.mark.parametrize('original_error', [OSError('synthetic disk failure'), ValueError('synthetic index failure')])
async def test_failed_rollback_retains_journal_without_masking_write_error(owner, monkeypatch, original_error):
    from memory.long_term_sync import JOURNAL, LongTermMemoryRecoveryRequired, require_consistent_views
    await stage(owner)
    write = owner.native.atomic_write_json
    def fail_write(path, *args, **kwargs):
        if path.name == 'facts.json':
            raise original_error
        return write(path, *args, **kwargs)
    monkeypatch.setattr(owner.native, 'atomic_write_json', fail_write)
    restore = owner.native.atomic_write_text
    def fail_restore(path, *args, **kwargs):
        if path.name == 'facts.json':
            raise OSError('synthetic rollback failure')
        return restore(path, *args, **kwargs)
    monkeypatch.setattr(owner.native, 'atomic_write_text', fail_restore)
    with pytest.raises(ValueError) as raised:
        await owner.native.consume(UID)
    if isinstance(original_error, OSError):
        assert str(raised.value) == 'long_term_memory_save_failed'
        assert raised.value.__cause__ is original_error
    else:
        assert raised.value is original_error
    assert json.loads((owner.directory / JOURNAL).read_text())['phase'] == 'pending'
    assert owner.adapter.incoming_path(UID).exists()
    with pytest.raises(LongTermMemoryRecoveryRequired):
        require_consistent_views(owner.directory)


def test_retirement_cutoff_and_source_union_never_regress_on_receive():
    value = learned()
    value.update(schemaVersion=2, evidence={'retirements': [{'id': 'retired', 'subject': 'master', 'sourceMessageIds': ['new-source'], 'retiredThroughAt': 2000, 'retiredThroughAtFromCorrection': True}], 'confirmations': []})
    existing = {'persona': {}, 'facts': [], 'archive': [], 'reflections': [], 'sync': {'evidence': {
        'retirements': [{'id': 'retired', 'subject': 'master', 'sourceMessageIds': ['old-source'], 'retiredThroughAt': 10000, 'retiredThroughAtFromCorrection': True}], 'confirmations': []}}}
    row = import_document(value, existing)['sync']['evidence']['retirements'][0]
    assert row['retiredThroughAt'] == 10000
    assert row['sourceMessageIds'] == ['old-source', 'new-source']


@pytest.mark.parametrize('protected', [True, False])
def test_sync_retains_persona_protection_flag(protected):
    snapshot = learned()
    snapshot['entries'][0]['metadata']['protected'] = protected
    native = import_document(snapshot, {'persona': {}, 'facts': [], 'archive': [], 'reflections': []})
    assert native['persona']['master']['facts'][0]['protected'] is protected
    exported = next(row for row in export_document(native, 3000)['entries'] if row['kind'] == 'persona')
    assert exported['metadata']['protected'] is protected


def test_formal_archives_roundtrip_and_keep_scoped_partitions(tmp_path):
    directory = tmp_path / 'Neko'
    for folder, entry in [
        ('persona_archive', {'id': 'p-old', 'text': 'archived persona', 'entity_key': 'master', 'source': 'reflection', 'archived_at': '2026-10-01T00:00:00'}),
        ('reflection_archive', {'id': 'r-old', 'text': 'archived reflection', 'entity': 'neko', 'status': 'denied', 'source_fact_ids': ['f1'], 'archived_at': '2026-10-01T00:00:00'}),
    ]:
        path = directory / folder / '2026-10-01_aaaaaaaa.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([entry]))
    views = read_views(directory)
    portable = export_document(views, 3000)
    assert {row['id'] for row in portable['entries']} == {'p-old', 'r-old'}
    restored = import_document(portable, {'persona': {}, 'facts': [], 'archive': [], 'reflections': []})
    assert any(row['id'] == 'p-old' for rows in restored['personaArchives'].values() for row in rows)
    assert any(row['id'] == 'r-old' for rows in restored['reflectionArchives'].values() for row in rows)
    assert export_document(restored, 3000) == portable


@pytest.mark.asyncio
async def test_failed_rollback_blocks_reads_and_recovers_without_sync_authority(owner, monkeypatch):
    await stage(owner)
    assert await owner.native.consume(UID)
    before = read_views(owner.directory)
    await stage(owner, completed=3000, revision=2)
    inbox = owner.adapter.incoming_path(UID)
    payload = json.loads(inbox.read_text())
    for row in payload['head']['document']['entries']:
        row['text'] = 'new incoming ' + row['kind']
    inbox.write_text(json.dumps(payload))
    original_json, original_text = owner.native.atomic_write_json, owner.native.atomic_write_text
    def fail_new(path, *args, **kwargs):
        if path.name == 'reflections.json': raise OSError('ongoing write failure')
        return original_json(path, *args, **kwargs)
    def fail_rollback(path, *args, **kwargs):
        if path.name == 'persona.json': raise OSError('rollback failure')
        return original_text(path, *args, **kwargs)
    monkeypatch.setattr(owner.native, 'atomic_write_json', fail_new)
    monkeypatch.setattr(owner.native, 'atomic_write_text', fail_rollback)
    with pytest.raises(Exception, match='long_term_memory_recovery_required'):
        await owner.native.consume_for_name('Neko')
    with pytest.raises(Exception, match='long_term_memory_recovery_required'):
        read_views(owner.directory)
    monkeypatch.setattr(owner.native, 'atomic_write_json', original_json)
    monkeypatch.setattr(owner.native, 'atomic_write_text', original_text)
    monkeypatch.setattr(owner.native, '_authority', lambda: None)
    assert await owner.native.consume_for_name('Neko') is False
    assert read_views(owner.directory) == before



def test_legacy_persona_archive_resolves_subject_from_durable_event(tmp_path):
    directory = tmp_path / 'Neko'
    path = directory / 'persona_archive' / '2026-10-01_aaaaaaaa.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps([{'id': 'old', 'text': 'archived relationship', 'archived_at': '2026-10-01T00:00:00'}]))
    (directory / 'events.ndjson').write_text(json.dumps({'type': 'persona.fact_added', 'payload': {
        'entity_key': 'relationship', 'entry_id': 'old', 'archive_shard_path': path.name}}) + '\n')
    assert export_document(read_views(directory), 3000)['entries'][0]['subject'] == 'relationship'
    assert 'entity_key' not in json.loads(path.read_text())[0]


def test_unresolved_or_corrupt_archive_cannot_be_published(tmp_path):
    path = tmp_path / 'persona_archive' / '2026-10-01_aaaaaaaa.json'
    path.parent.mkdir()
    original = json.dumps([{'id': 'old', 'text': 'unresolved subject'}])
    path.write_text(original)
    with pytest.raises(ValueError, match='long_term_archive_subject_unavailable'):
        read_views(tmp_path)
    assert path.read_text() == original
    path.write_text('{broken')
    with pytest.raises(ValueError):
        read_views(tmp_path)
    assert path.read_text() == '{broken'


@pytest.mark.asyncio
async def test_pending_restore_blocks_native_cached_persona_fact_and_reflection_loaders(owner):
    from memory.long_term_sync import JOURNAL, LongTermMemoryRecoveryRequired
    from memory.facts import FactStore
    from memory.persona import PersonaManager
    from memory.reflection import ReflectionEngine
    (owner.directory / JOURNAL).write_text(json.dumps({'version': 1, 'phase': 'pending'}))
    for cls, methods in [(FactStore, ['load_facts', 'load_facts_full', 'load_facts_strict', 'aload_facts']),
                         (PersonaManager, ['ensure_persona', 'aensure_persona']),
                         (ReflectionEngine, ['load_reflections', 'aload_reflections', '_aload_reflections_full'])]:
        instance = object.__new__(cls)
        instance._config_manager = owner.config
        instance._facts, instance._personas = {'Neko': ['cached mixed fact']}, {'Neko': {'mixed': True}}
        import inspect
        for method in methods:
            with pytest.raises(LongTermMemoryRecoveryRequired):
                result = getattr(instance, method)('Neko')
                if inspect.isawaitable(result):
                    await result


@pytest.mark.asyncio
async def test_closed_restore_marker_never_rolls_back_later_local_memory(owner):
    from memory.long_term_sync import JOURNAL
    path = owner.directory / 'facts.json'
    path.write_text(json.dumps([{'id': 'new-local', 'text': 'after committed snapshot'}]))
    (owner.directory / JOURNAL).write_text(json.dumps({'version': 1, 'phase': 'closed', 'before': {'facts.json': '[]'}}))
    assert await owner.native.consume_for_name('Neko') is False
    assert json.loads(path.read_text())[0]['id'] == 'new-local'
    assert not (owner.directory / JOURNAL).exists()


@pytest.mark.asyncio
async def test_evidence_only_change_is_captured_with_no_active_memories(owner):
    owner.native._capture('Neko', True)
    state = {'evidence': {'retirements': [{'id': 'evicted', 'subject': 'master', 'text': 'retired assertion', 'sourceMessageIds': ['old']}], 'confirmations': []}}
    atomic_write_json(owner.directory / 'long_term_portable.json', state)
    assert owner.native._capture('Neko', False)
    assert not owner.native._capture('Neko', False)


def test_formal_archives_keep_scoped_and_card_entries_and_respect_shard_capacity():
    from config import ARCHIVE_FILE_MAX_ENTRIES
    existing = {'persona': {}, 'facts': [], 'archive': [], 'reflections': [], 'personaArchives': {
        'persona_archive/2026-10-01_aaaaaaaa.json': [
            {'id': 'group', 'entity_key': 'master', 'scope': 'group:one', 'text': 'group memory'},
            {'id': 'card', 'entity_key': 'neko', 'source': 'character_card', 'text': 'static card'},
            {'id': 'old-private', 'entity_key': 'master', 'text': 'obsolete private'}]}}
    incoming = {'schemaVersion': 2, 'completedAt': 3000, 'evidence': {'retirements': [], 'confirmations': []}, 'entries': [
        {'kind': 'persona', 'subject': 'master', 'id': f'archived-{i}', 'text': f'archived {i}',
         'metadata': {'archived': True, 'archivedAt': 1790784000000}} for i in range(ARCHIVE_FILE_MAX_ENTRIES + 1)]}
    result = import_document(incoming, existing)
    rows = [row for shard in result['personaArchives'].values() for row in shard]
    assert {row['id'] for row in rows} == {'group', 'card'} | {f'archived-{i}' for i in range(ARCHIVE_FILE_MAX_ENTRIES + 1)}
    assert all(len(shard) <= ARCHIVE_FILE_MAX_ENTRIES for shard in result['personaArchives'].values())
    assert len(export_document(result, 3000)['entries']) == ARCHIVE_FILE_MAX_ENTRIES + 1


@pytest.mark.asyncio
async def test_formal_archive_apply_rolls_back_all_shards_and_replays_after_index_failure(owner, monkeypatch):
    from memory.long_term_sync import JOURNAL
    old = owner.directory / 'persona_archive' / '2026-10-01_aaaaaaaa.json'
    old.parent.mkdir()
    old.write_text(json.dumps([{'id': 'local-archive', 'entity_key': 'master', 'text': 'local archive', 'archived_at': '2026-10-01T00:00:00'}]))
    old_bytes, raw_bytes = old.read_bytes(), owner.raw.read_bytes()
    await stage(owner)
    payload = json.loads(owner.adapter.incoming_path(UID).read_text())
    for row in payload['head']['document']['entries']:
        if row['kind'] in ('persona', 'reflection'):
            row['metadata'].update(archived=True, archivedAt=1790870400000)
    atomic_write_json(owner.adapter.incoming_path(UID), payload)
    original = owner.native._replace_fact_index
    def failed(*args): raise OSError('persistent index failure')
    monkeypatch.setattr(owner.native, '_replace_fact_index', failed)
    with pytest.raises(ValueError, match='long_term_memory_save_failed'):
        await owner.native.consume(UID)
    assert old.read_bytes() == old_bytes
    assert len(list((owner.directory / 'persona_archive').glob('*.json'))) == 1
    assert not list((owner.directory / 'reflection_archive').glob('*.json'))
    assert (owner.directory / JOURNAL).is_file()
    monkeypatch.setattr(owner.native, '_replace_fact_index', original)
    assert await owner.native.consume(UID)
    exported = export_document(read_views(owner.directory), 3000)
    assert {row['id'] for row in exported['entries']} >= {'p1', 'r1', 'local-archive'}
    assert owner.raw.read_bytes() == raw_bytes
    assert not (owner.directory / JOURNAL).exists()


@pytest.mark.asyncio
async def test_cancelling_snapshot_apply_waits_for_consistent_file_commit(owner, monkeypatch):
    from memory.long_term_sync import JOURNAL
    await stage(owner)
    reached, release = threading.Event(), threading.Event()
    original = owner.native.atomic_write_json
    def paused(path, *args, **kwargs):
        if path.name == 'persona.json':
            reached.set()
            assert release.wait(3)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(owner.native, 'atomic_write_json', paused)
    task = asyncio.create_task(owner.native.consume(UID))
    try:
        assert await asyncio.to_thread(reached.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert read_views(owner.directory)['persona']['master']['facts'][0]['id'] == 'p1'
    assert not (owner.directory / JOURNAL).exists()
    assert not owner.adapter.incoming_path(UID).exists()
    assert owner.raw.read_bytes() == b'synthetic raw archive remains byte-identical'


@pytest.mark.asyncio
async def test_real_native_reflection_archive_is_present_in_sync_snapshot(tmp_path):
    from tests.unit.test_evidence_archive import _install
    _ev, _fs, _pm, engine, _rec, _cm = _install(str(tmp_path))
    await engine.asave_reflections('小天', [{'id': 'native-archive', 'entity': 'master', 'text': 'archived native reflection',
        'status': 'confirmed', 'created_at': '2026-10-01T00:00:00', 'source_fact_ids': []}])
    assert await engine.aarchive_reflection('小天', 'native-archive')
    views = read_views(tmp_path/'小天')
    physical = [row for rows in views['reflectionArchives'].values() for row in rows]
    exported = export_document(views, 3000)['entries']
    received = import_document({'schemaVersion':2, 'completedAt':4000, 'entries':[], 'evidence':{'retirements':[],'confirmations':[]}}, views, baseline=[])
    retained = [row['id'] for rows in received['reflectionArchives'].values() for row in rows]
    print(json.dumps({'probe':'real_archive', 'physical_statuses':[r['status'] for r in physical], 'exported_ids':[r['id'] for r in exported], 'native_ids_after_unrelated_snapshot':retained}))
    assert any(row['id'] == 'native-archive' for row in exported)
    assert 'native-archive' in retained


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['pending','confirmed','full'])
@pytest.mark.parametrize('finish', ['pending','rolled-back'])
async def test_inflight_reflection_read_cannot_return_uncommitted_view(tmp_path, monkeypatch, kind, finish):
    import asyncio
    from memory.long_term_sync import JOURNAL, LongTermMemoryRecoveryRequired
    from memory.reflection import persistence, evidence_flow
    module = evidence_flow if kind == 'full' else persistence
    from tests.unit.test_evidence_archive import _install
    _ev, _fs, _pm, engine, _rec, _cm = _install(str(tmp_path))
    directory = tmp_path/'小天'
    directory.mkdir(exist_ok=True)
    path = directory/'reflections.json'
    path.write_text(json.dumps([{'id':'before','entity':'master','text':'committed reflection','status':'pending'}]))
    entered, release = asyncio.Event(), asyncio.Event()
    original = module.read_json_async
    async def paused(target, *args, **kwargs):
        entered.set()
        await release.wait()
        data = await original(target,*args,**kwargs)
        if finish == 'rolled-back':
            from memory.long_term_sync import advance_view_generation
            advance_view_generation(directory)
            atomic_write_json(path, [{'id':'before','entity':'master','text':'committed reflection','status':'pending'}])
            (directory/JOURNAL).unlink()
        return data
    monkeypatch.setattr(module,'read_json_async',paused)
    task = asyncio.create_task(engine.aget_pending_reflections('小天') if kind == 'pending' else engine.aget_confirmed_reflections('小天') if kind == 'confirmed' else engine._aload_reflections_full('小天'))
    await asyncio.wait_for(entered.wait(),2)
    (directory/JOURNAL).write_text(json.dumps({'version':1,'phase':'pending', 'before': {'reflections.json': path.read_text()}, 'receipt': {'filename':'a'*32+'-'+'b'*16+'.json','before':None}}))
    path.write_text(json.dumps([{'id':'half-committed','entity':'master','text':'uncommitted incoming reflection','status':'confirmed' if kind == 'full' else kind, 'reinforcement':10, 'disputation':0}]))
    release.set()
    try:
        rows = await task
    except LongTermMemoryRecoveryRequired:
        return
    print(json.dumps({'probe':'inflight_reader','returned_ids':[row['id'] for row in rows],'journal_pending':finish == 'pending', 'kind':kind}))
    pytest.fail('inflight reader returned pending snapshot instead of blocking it')



@pytest.mark.asyncio
async def test_full_fact_pool_does_not_mix_versions_across_completed_replacement(tmp_path, monkeypatch):
    from tests.unit.test_evidence_archive import _install
    from memory.long_term_sync import LongTermMemoryRecoveryRequired
    _ev, fs, _pm, _engine, _rec, _cm = _install(str(tmp_path))
    directory = tmp_path/'小天'
    directory.mkdir(exist_ok=True)
    atomic_write_json(directory/'facts.json', [{'id':'old-active','entity':'master','text':'old active'}])
    atomic_write_json(directory/'facts_archive.json', [{'id':'old-archive','entity':'master','text':'old archive'}])
    original = fs._facts_archive_path
    def replaced(name):
        from memory.long_term_sync import advance_view_generation
        advance_view_generation(directory)
        atomic_write_json(directory/'facts.json', [{'id':'new-active','entity':'master','text':'new active'}])
        atomic_write_json(directory/'facts_archive.json', [{'id':'new-archive','entity':'master','text':'new archive'}])
        fs._facts.pop(name, None)
        return original(name)
    monkeypatch.setattr(fs, '_facts_archive_path', replaced)
    with pytest.raises(LongTermMemoryRecoveryRequired):
        fs.load_facts_full('小天')


@pytest.mark.asyncio
async def test_read_error_during_pending_snapshot_cannot_become_legal_empty_reflections(tmp_path, monkeypatch):
    from tests.unit.test_evidence_archive import _install
    from memory.long_term_sync import JOURNAL, LongTermMemoryRecoveryRequired
    from memory.reflection import persistence
    _ev, _fs, _pm, engine, _rec, _cm = _install(str(tmp_path))
    path = tmp_path/'小天'/'reflections.json'
    path.parent.mkdir(exist_ok=True)
    atomic_write_json(path, [{'id':'committed','text':'old','entity':'master','status':'pending'}])
    async def unavailable(target):
        atomic_write_json(path.parent/JOURNAL, {'version':1,'phase':'pending'})
        raise FileNotFoundError('replacement window')
    monkeypatch.setattr(persistence, 'read_json_async', unavailable)
    with pytest.raises(LongTermMemoryRecoveryRequired):
        await engine.aget_pending_reflections('小天')


@pytest.mark.asyncio
async def test_no_inbox_or_journal_does_not_wait_for_global_reload(owner, monkeypatch):
    def unexpected_uid(_name):
        raise AssertionError('idle conversation must not load character files')
    monkeypatch.setattr(owner.native, '_uid', unexpected_uid)
    async with owner.native.runtime._reload_lock:
        assert await asyncio.wait_for(owner.native.consume_for_name('Neko'), 1) is False


def test_ordinary_save_and_unrelated_directory_change_do_not_require_recovery(tmp_path):
    from memory.long_term_sync import consistent_view_read
    path = tmp_path / 'facts.json'
    atomic_write_json(path, [])
    with consistent_view_read(path):
        atomic_write_json(path, [{'id': 'local'}])
    with consistent_view_read(tmp_path / 'missing.json'):
        atomic_write_json(tmp_path / 'unrelated.json', [])


def test_snapshot_keeps_matching_local_diary_import_identity():
    native = {'persona': {}, 'facts': [{'id': 'diary', 'entity': 'master', 'text': 'Walked the dog',
        'hash': 'date-sensitive-hash', 'external_import': {'event_date': '2026-10-20', 'file_path': '/private/diary'},
        'source': 'user_observation'}], 'archive': [], 'reflections': []}
    snapshot = export_document(native, 3000)
    assert '/private/diary' not in json.dumps(snapshot)
    row = import_document(snapshot, native)['facts'][0]
    assert row['hash'] == 'date-sensitive-hash'
    assert row['external_import'] == native['facts'][0]['external_import']
    foreign = import_document(snapshot, {'persona': {}, 'facts': [], 'archive': [], 'reflections': []})['facts'][0]
    assert 'external_import' not in foreign
    snapshot['entries'][0]['text'] = 'Changed assertion'
    assert 'external_import' not in import_document(snapshot, native)['facts'][0]
