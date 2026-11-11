"""Learned-memory replacement must retain raw chat and unrelated memory partitions."""

import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager, closing
from copy import deepcopy
import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from memory.long_term_merge import merge_documents
from memory.long_term_sync import DesktopLongTermAdapter, FILES, export_document, import_document, read_views
from memory.event_log import EventLog, Reconciler
from services.chat_sync.long_term import LongTermMemorySync
from utils.long_term_memory_protocol import document, mutation
from services.chat_sync.writer import store_connection
from utils.file_utils import atomic_write_json
from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401
from tests.unit.test_chat_sync_runtime import setup_runtime, DOMAIN, OWNER, UID


def merged_import(incoming, existing, *, baseline=..., **options):
    """Merge a published document with this device's views and write the result, as the memory owner does.

    baseline is the entry list both sides started from, or None for a device
    without a confirmed base. Left out, the device changed nothing since its base.
    """
    current = export_document(existing, incoming['completedAt'])
    if baseline is ...:
        baseline = current['entries']
    base = None if baseline is None else {'document': {**current, 'entries': baseline}, 'nativeEntries': baseline}
    merged = merge_documents(base, current, incoming, incoming['completedAt'])
    return import_document(merged, existing, merged=True, **options)


def as_older_document(value):
    """The same snapshot as a schema 2 writer would have published it."""
    newer = ('signalJudged', 'userConfirmCount', 'countedFactIds', 'factSourceCounts')
    return {'schemaVersion': 2, 'completedAt': value['completedAt'], 'evidence': deepcopy(value['evidence']),
            'entries': [{**row, 'metadata': {key: item for key, item in row['metadata'].items()
                                             if key not in newer and not (item is None and key in ('eventWhen', 'temporalScope'))}}
                        for row in deepcopy(value['entries'])]}


def local_state(owner):
    return owner.adapter.read_local(owner.binding, owner.runtime.writer.epoch())


def learned(completed=2000, text='learned preference'):
    return {'schemaVersion': 1, 'completedAt': completed, 'entries': [
        {'kind': 'persona', 'subject': 'master', 'id': 'p1', 'text': text,
         'metadata': {'createdAt': 1000, 'updatedAt': completed, 'sourceSyncId': 'mobile:p1', 'reinforcement': 3}},
        {'kind': 'fact', 'subject': 'relationship', 'id': 'f1', 'text': 'shared experience',
         'metadata': {'createdAt': 1000, 'importance': 8, 'sourceMessageIds': ['local-source-1']}},
        {'kind': 'reflection', 'subject': 'neko', 'id': 'r1', 'text': 'confirmed impression',
         'metadata': {'createdAt': 1000, 'sourceFactIds': ['f1']}},
    ]}


@pytest.mark.parametrize('active_count,dormant_id,accepted', [
    (19999, 'dormant', True), (20000, 'f0', True), (20000, 'dormant', False), (20001, None, False),
])
def test_snapshot_capacity_counts_combined_unique_active_and_dormant_entries(active_count, dormant_id, accepted):
    views = {'persona': {}, 'reflections': [], 'archive': [], 'facts': [
        {'id': f'f{index}', 'entity': 'master', 'text': 'A learned preference'} for index in range(active_count)]}
    if dormant_id:
        views['sync'] = {'dormantEntries': [{'kind': 'fact', 'subject': 'master', 'id': dormant_id,
                         'text': 'A retired preference', 'metadata': {'status': 'retracted', 'voidedAt': 1000}}]}
    before = deepcopy(views)
    if accepted:
        result = export_document(views, 3000)
        assert len(result['entries']) == 20000
        if dormant_id == 'f0':
            assert next(row for row in result['entries'] if row['id'] == 'f0')['text'] == 'A learned preference'
    else:
        with pytest.raises(ValueError, match='^long_term_memory_capacity_exceeded$'):
            export_document(views, 3000)
    assert views == before


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


@pytest.mark.parametrize('kind', ['persona', 'reflection'])
@pytest.mark.parametrize('change', [None, 'text', 'subject', 'id'])
def test_replacement_preserves_covered_fact_audit_only_for_the_same_memory(kind, change):
    incoming = learned()
    existing = import_document(incoming, {'persona': {}, 'facts': [], 'archive': [], 'reflections': []})
    local = existing['persona']['master']['facts'][0] if kind == 'persona' else existing['reflections'][0]
    local['covered_fact_ids'] = ['receiver-fact']
    before = deepcopy(existing)
    row = next(row for row in incoming['entries'] if row['kind'] == kind)
    if change:
        row[change] = {'text': 'different memory', 'subject': 'relationship', 'id': 'different-id'}[change]
    result = import_document(incoming, existing)
    restored = result['persona'][row['subject']]['facts'][0] if kind == 'persona' else result['reflections'][0]
    assert restored.get('covered_fact_ids') == (None if change else ['receiver-fact'])
    assert export_document(result, incoming['completedAt']) == export_document(
        import_document(incoming, {'persona': {}, 'facts': [], 'archive': [], 'reflections': []}), incoming['completedAt'])
    assert existing == before


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
    # Tests drive capture themselves; what a merge leaves to publish is recorded instead of queued.
    republished = []
    monkeypatch.setattr(native, '_publish_merge', republished.append)
    monkeypatch.setattr(native, '_unpublished_after_apply', set())
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
                              characters=characters, republished=republished)
    finally:
        await native.close()
        await runtime.close()


async def stage(owner, completed=2000, revision=1, leftover=False):
    """Stage a published revision; leftover writes an inbox the base already reached, as a crash leaves one."""
    remote = {'ownerId': OWNER, 'characterSyncId': UID, 'revision': revision,
              'completedAt': completed, 'document': learned(completed)}
    epoch = owner.runtime.writer.epoch()
    fingerprint = (await owner.runtime._session())[1]
    if leftover:
        assert not owner.adapter.stage_remote(owner.binding, remote, epoch, owner.adapter.base_revision(owner.binding, epoch),
                                              session_fingerprint=fingerprint)
        atomic_write_json(owner.adapter.incoming_path(UID), {
            'binding': owner.binding, 'epoch': list(epoch), 'head': remote, 'sessionFingerprint': fingerprint})
        return
    assert owner.adapter.stage_remote(owner.binding, remote, epoch, owner.adapter.base_revision(owner.binding, epoch),
                                      session_fingerprint=fingerprint)


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
    local = local_state(owner)['pending']
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
    assert local_state(owner)['base']['revision'] == 1
    assert owner.native._capture('Neko', False) is False
    assert owner.native._capture('Neko', True) is True
    current = local_state(owner)['pending']
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
    local = local_state(owner)['pending']
    assert local is not None
    await stage(owner, completed=local['document']['completedAt'] + 1)
    assert await owner.native.consume(UID)
    assert read_views(owner.directory)['persona']['master']['facts'][0]['text'] == 'learned preference'
    assert owner.raw.read_bytes() == b'synthetic raw archive remains byte-identical'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['rename', 'pause'])
async def test_stale_capture_does_not_report_a_capacity_failure_for_a_changed_role(owner, monkeypatch, change):
    import memory.long_term_sync as portable
    monkeypatch.setattr(portable, 'MAX_ENTRIES', 1)
    atomic_write_json(owner.directory / FILES['facts'], [
        {'id': key, 'entity': 'master', 'text': 'A learned preference'} for key in ('one', 'two')])
    original = owner.native.export_document
    def changed(*args, **kwargs):
        if change == 'rename':
            owner.characters['猫娘']['Renamed'] = owner.characters['猫娘'].pop('Neko')
        else:
            owner.runtime.state.pause(UID)
        return original(*args, **kwargs)
    monkeypatch.setattr(owner.native, 'export_document', changed)
    assert owner.native._capture('Neko', True, UID) is None
    assert owner.runtime.scheduler.last_error(DOMAIN.key()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['io', 'sqlite'])
@pytest.mark.parametrize('scenario', ['archive', 'capacity', 'publish'])
async def test_capture_diagnostic_storage_failure_preserves_capture_outcome(owner, monkeypatch, scenario, failure):
    import sqlite3
    import memory.long_term_sync as portable

    if scenario == 'archive':
        archive = owner.directory / 'persona_archive' / '2026-10-01_aaaaaaaa.json'
        archive.parent.mkdir()
        atomic_write_json(archive, [{'id': 'unknown', 'text': 'Unknown archive subject'}])
    else:
        atomic_write_json(owner.directory / FILES['facts'], [
            {'id': key, 'entity': 'master', 'text': 'A learned preference'} for key in ('one', 'two')])
        if scenario == 'capacity':
            monkeypatch.setattr(portable, 'MAX_ENTRIES', 1)
    expected = {'archive': 'long_term_archive_subject_unavailable',
                'capacity': 'long_term_memory_capacity_exceeded', 'publish': None}[scenario]
    attempts = []
    def denied(domain, generation, code):
        attempts.append((domain, generation, code))
        raise {'io': OSError, 'sqlite': sqlite3.OperationalError}[failure]('diagnostic unavailable')
    monkeypatch.setattr(owner.native, '_capture_scheduler', lambda _: owner.runtime.scheduler)
    monkeypatch.setattr(owner.runtime.scheduler, 'record_long_term_capture_error', denied)
    receipt = owner.adapter.local_path(owner.binding)
    if expected is None:
        assert owner.native._capture('Neko', True)
        assert receipt.is_file()
    else:
        with pytest.raises(ValueError, match=f'^{expected}$'):
            owner.native._capture('Neko', True)
        assert not receipt.exists()
    assert attempts == [(DOMAIN.key(), owner.binding['generation'], expected)]
    owner.runtime.writer.check()


@pytest.mark.asyncio
async def test_over_capacity_capture_retains_memory_and_receipt_then_recovers_on_a_later_update(owner, monkeypatch):
    import memory.long_term_sync as portable
    monkeypatch.setattr(portable, 'MAX_ENTRIES', 1)
    first = {'id': 'one', 'entity': 'master', 'text': 'First learned preference'}
    atomic_write_json(owner.directory / FILES['facts'], [first])
    assert owner.native._capture('Neko', True)
    receipt = owner.adapter.local_path(owner.binding)
    saved_receipt = receipt.read_bytes()
    atomic_write_json(owner.directory / FILES['facts'], [first, {**first, 'id': 'two'}])
    before = {name: (owner.directory / name).read_bytes() for name in FILES.values()}
    captures = []
    original = owner.native._capture
    def observed(*args, **kwargs):
        captures.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(owner.native, '_capture', observed)
    owner.native.queue_capture('Neko')
    await asyncio.wait_for(asyncio.shield(owner.native._capture_tasks['Neko']), 3)
    assert len(captures) == 1
    assert before == {name: (owner.directory / name).read_bytes() for name in FILES.values()}
    assert receipt.read_bytes() == saved_receipt
    assert owner.runtime.scheduler.local_memory_diagnostics(DOMAIN.key()) == ['long_term_memory_capacity_exceeded']
    binding, = (await owner.runtime.status())['bindings']
    assert binding['lastSyncError'] is None
    assert binding['localMemoryDiagnostics'] == ['long_term_memory_capacity_exceeded']
    atomic_write_json(owner.directory / FILES['facts'], [first])
    owner.native.queue_capture('Neko')
    await asyncio.wait_for(asyncio.shield(owner.native._capture_tasks['Neko']), 3)
    assert receipt.read_bytes() == saved_receipt
    assert (await owner.runtime.status())['bindings'][0]['lastSyncError'] is None
    assert (await owner.runtime.status())['bindings'][0]['localMemoryDiagnostics'] == []
    heads = owner.runtime.writer.check()['heads']
    assert not original('Neko', False)
    assert owner.runtime.writer.check()['heads'] == heads


@pytest.mark.asyncio
async def test_recover_pending_snapshot_settles_the_journal_or_raises(owner, monkeypatch):
    from app.memory_server import long_term_sync as native
    from memory.long_term_sync import JOURNAL, LongTermMemoryRecoveryRequired
    await native.recover_pending_snapshot('Neko')
    atomic_write_json(owner.directory / FILES['facts'], [{'id': 'scoped', 'text': 'forget me'}])
    atomic_write_json(owner.directory / JOURNAL, {'version': 1, 'phase': 'pending', 'before': {FILES['facts']: '[]'},
                                                  'receipt': {'filename': 'a' * 32 + '-' + 'b' * 16 + '.json', 'before': None}})
    await native.recover_pending_snapshot('Neko')
    assert not (owner.directory / JOURNAL).exists()
    assert json.loads((owner.directory / FILES['facts']).read_text(encoding='utf-8')) == []

    atomic_write_json(owner.directory / JOURNAL, {'version': 1, 'phase': 'pending', 'before': {FILES['facts']: '[]'}, 'receipt': {'filename': 'a' * 32 + '-' + 'b' * 16 + '.json', 'before': None}})
    def fail(name):
        raise LongTermMemoryRecoveryRequired()
    monkeypatch.setattr(native, '_recover_snapshot', fail)
    with pytest.raises(LongTermMemoryRecoveryRequired):
        await native.recover_pending_snapshot('Neko')


@pytest.mark.asyncio
async def test_conflicting_retirement_inbox_never_blocks_later_local_events(owner):
    import hashlib
    from memory.event_log import EVT_FACT_ADDED
    retired = {'id': 'same-retirement', 'subject': 'master', 'sourceMessageIds': ['old-source']}
    atomic_write_json(owner.directory / FILES['sync'], {'evidence': {'retirements': [retired], 'confirmations': []}})
    incoming = learned()
    incoming.update(schemaVersion=2, evidence={'retirements': [{**retired, 'subject': 'neko'}], 'confirmations': []})
    remote = {'ownerId': OWNER, 'characterSyncId': UID, 'revision': 1, 'completedAt': 2000, 'document': incoming}
    assert owner.adapter.stage_remote(owner.binding, remote, owner.runtime.writer.epoch(), 0,
                                      session_fingerprint=(await owner.runtime._session())[1])
    before = {filename: (owner.directory / filename).read_bytes() for filename in FILES.values()}
    assert not await owner.native.consume(UID)
    assert not (owner.directory / 'events.ndjson').exists()
    inbox = owner.adapter.incoming_path(UID)
    payload = {'characterSyncId': UID, 'inboxSha256': hashlib.sha256(inbox.read_text(encoding='utf-8').encode('utf-8')).hexdigest()}
    log = owner.native.runtime.event_log
    log.append('Neko', 'memory.long_term_replaced', payload)
    final_id = log.append('Neko', EVT_FACT_ADDED, {'fact_id': 'later-local-fact'})
    applied = []
    owner.replay.register(EVT_FACT_ADDED, lambda name, row: (applied.append(row['fact_id']) or True))
    assert await owner.replay.areconcile('Neko') == 1
    assert applied == ['later-local-fact']
    assert log.read_sentinel('Neko') == final_id
    assert inbox.exists()
    assert before == {filename: (owner.directory / filename).read_bytes() for filename in FILES.values()}


@pytest.mark.asyncio
async def test_waiting_inbox_status_coexists_with_errors_without_mutating_or_accepting_the_conflict(owner):
    retired = {'id': 'same-retirement', 'subject': 'master', 'sourceMessageIds': ['old-source']}
    atomic_write_json(owner.directory / FILES['sync'], {'evidence': {'retirements': [retired], 'confirmations': []}})
    incoming = learned()
    incoming.update(schemaVersion=2, evidence={'retirements': [{**retired, 'subject': 'neko'}], 'confirmations': []})
    remote = {'ownerId': OWNER, 'characterSyncId': UID, 'revision': 1, 'completedAt': 2000, 'document': incoming}
    assert owner.adapter.stage_remote(owner.binding, remote, owner.runtime.writer.epoch(), 0,
                                      session_fingerprint=(await owner.runtime._session())[1])
    assert not await owner.native.consume(UID)
    inbox = owner.adapter.incoming_path(UID)
    before = {path: path.read_bytes() for path in [inbox, *(owner.directory / name for name in FILES.values())]}
    owner.runtime.scheduler.record_long_term_capture_error(DOMAIN.key(), owner.binding['generation'], 'long_term_memory_capacity_exceeded')
    owner.runtime.scheduler.failure(DOMAIN.key(), 'pull', owner.binding['generation'], 'session',
                                    ValueError('chat_sync_login_required'))
    with pytest.raises(ValueError, match='^long_term_memory_incoming_pending$'):
        owner.native._capture('Neko', False, UID, True)
    binding, = (await owner.runtime.status())['bindings']
    assert binding['lastSyncError'] == 'chat_sync_login_required'
    assert binding['localMemoryDiagnostics'] == ['long_term_memory_capacity_exceeded', 'long_term_memory_incoming_pending']
    assert before == {path: path.read_bytes() for path in before}
    assert not (owner.directory / 'events.ndjson').exists()
    remote.update(revision=2, document={**incoming, 'evidence': {'retirements': [retired], 'confirmations': []}})
    assert owner.adapter.stage_remote(owner.binding, remote, owner.runtime.writer.epoch(), 0,
                                      session_fingerprint=(await owner.runtime._session())[1])
    assert await owner.native.consume(UID)
    assert 'long_term_memory_incoming_pending' not in (await owner.runtime.status())['bindings'][0]['localMemoryDiagnostics']
    owner.runtime.state.pause(UID)
    assert (await owner.runtime.status())['bindings'][0]['localMemoryDiagnostics'] == []


@pytest.mark.asyncio
async def test_unavailable_inbox_diagnostic_does_not_fail_status_or_expose_the_io_error(owner, monkeypatch):
    def unavailable(_uid):
        raise OSError('private local path')
    monkeypatch.setattr(owner.adapter, 'incoming_path', unavailable)
    binding, = (await owner.runtime.status())['bindings']
    assert binding['localMemoryDiagnostics'] == []
    assert binding['lastSyncError'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('windows_newlines', [False, True])
async def test_native_failed_multi_file_save_rolls_back_and_journal_replays_exact_snapshot(owner, monkeypatch, windows_newlines):
    await stage(owner)
    if windows_newlines:
        inbox = owner.adapter.incoming_path(UID)
        inbox.write_bytes(inbox.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
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
    import hashlib
    event = json.loads((owner.directory / 'events.ndjson').read_text().splitlines()[-1])
    assert event['payload'] == {'characterSyncId': UID,
                               'inboxSha256': hashlib.sha256(owner.adapter.incoming_path(UID).read_text(encoding='utf-8').encode('utf-8')).hexdigest()}
    await owner.replay.areconcile('Neko')
    assert read_views(owner.directory)['facts'][0]['text'] == 'shared experience'
    assert await owner.native.consume(UID)
    assert owner.raw.read_bytes() == before['time_indexed.db']


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['paused-binding', 'changed-epoch'])
async def test_pending_remote_never_overwrites_a_revoked_grant(owner, change):
    await stage(owner)
    before = (owner.directory / 'persona.json').read_bytes()
    if change == 'paused-binding':
        owner.runtime.state.pause(UID)
    else:
        payload = json.loads(owner.adapter.incoming_path(UID).read_text())
        payload['epoch'] = ['0' * 32, '0' * 32]
        atomic_write_json(owner.adapter.incoming_path(UID), payload)
    assert not await owner.native.consume(UID)
    assert (owner.directory / 'persona.json').read_bytes() == before


@pytest.mark.asyncio
async def test_snapshot_waiting_for_upload_is_merged_with_a_newer_revision_instead_of_blocking_it(owner):
    """A local snapshot the server never accepted is this device's edit, not a newer revision."""
    fact = {'id': 'local-only', 'entity': 'master', 'text': 'picked here, never uploaded', 'source': 'user_observation'}
    atomic_write_json(owner.directory / 'facts.json', [fact])
    assert owner.native._capture('Neko', True)
    refused = local_state(owner)['pending']
    assert 'local-only' in {row['id'] for row in refused['document']['entries']}
    # Another device published first, with a completion time after this capture.
    await stage(owner, completed=refused['document']['completedAt'] + 1000)
    assert await owner.native.consume(UID)
    views = read_views(owner.directory)
    assert {row['id'] for row in views['facts']} == {'local-only', 'f1'}
    assert views['persona']['master']['facts'][0]['text'] == 'learned preference'
    state = local_state(owner)
    assert state['base']['revision'] == 1 and state['pending'] is None
    assert 'local-only' not in {row['id'] for row in state['base']['nativeEntries']}
    assert owner.native._capture('Neko', False)
    upload = local_state(owner)['pending']
    assert upload['baseRevision'] == 1
    assert {row['id'] for row in upload['document']['entries']} == {'local-only', 'f1', 'p1', 'r1'}
    assert upload['document']['evidence']['retirements'] == []


@pytest.mark.asyncio
async def test_obsolete_inbox_is_removed_without_appending_replacement_events(owner):
    await stage(owner, revision=2)
    assert await owner.native.consume(UID)
    await stage(owner, revision=1, leftover=True)
    before = {filename: (owner.directory / filename).read_bytes() for filename in FILES.values()}
    events = owner.directory / 'events.ndjson'
    original_events = events.read_bytes() if events.exists() else b''
    for _ in range(3):
        assert not await owner.native.consume(UID)
    assert not owner.adapter.incoming_path(UID).exists()
    assert (events.read_bytes() if events.exists() else b'') == original_events
    assert before == {filename: (owner.directory / filename).read_bytes() for filename in FILES.values()}


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['logout', 'switch-account', 'paused-binding', 'changed-epoch'])
async def test_waiting_inbox_is_retained_without_growing_the_journal(owner, change):
    await stage(owner)
    if change == 'logout':
        owner.session['logged_in'] = False
    elif change == 'switch-account':
        owner.session['snapshot']['access_token'] = 'synthetic-other-account'
    elif change == 'paused-binding':
        owner.runtime.state.pause(UID)
    else:
        payload = json.loads(owner.adapter.incoming_path(UID).read_text())
        payload['epoch'] = ['0' * 32, '0' * 32]
        atomic_write_json(owner.adapter.incoming_path(UID), payload)
    for _ in range(3):
        assert not await owner.native.consume(UID)
    assert not (owner.directory / 'events.ndjson').exists()
    assert owner.adapter.incoming_path(UID).exists()


@pytest.mark.asyncio
async def test_journal_references_the_inbox_instead_of_copying_the_snapshot(owner):
    await stage(owner)
    assert await owner.native.consume(UID)
    lines = (owner.directory / 'events.ndjson').read_text(encoding='utf-8').splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert set(record['payload']) == {'characterSyncId', 'inboxSha256'}
    assert 'learned preference' not in lines[0] and 'sessionFingerprint' not in lines[0]


@pytest.mark.asyncio
async def test_replay_skips_a_reference_whose_inbox_was_replaced(owner, monkeypatch):
    await stage(owner)
    original = owner.native.atomic_write_json
    def fail_facts(path, *args, **kwargs):
        if path.name == 'facts.json':
            raise OSError('synthetic disk failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(owner.native, 'atomic_write_json', fail_facts)
    with pytest.raises(ValueError, match='long_term_memory_save_failed'):
        await owner.native.consume(UID)
    monkeypatch.setattr(owner.native, 'atomic_write_json', original)
    before = {filename: (owner.directory / filename).read_bytes() for filename in FILES.values()}
    await stage(owner, completed=3000, revision=2)
    await owner.replay.areconcile('Neko')
    assert before == {filename: (owner.directory / filename).read_bytes() for filename in FILES.values()}
    assert await owner.native.consume(UID)
    assert local_state(owner)['base']['revision'] == 2


@pytest.mark.asyncio
async def test_unreadable_inbox_is_skipped_without_stalling_replay_and_applies_later(owner, monkeypatch):
    await stage(owner)
    original = owner.native.atomic_write_json
    def fail_facts(path, *args, **kwargs):
        if path.name == 'facts.json':
            raise OSError('synthetic disk failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(owner.native, 'atomic_write_json', fail_facts)
    with pytest.raises(ValueError, match='long_term_memory_save_failed'):
        await owner.native.consume(UID)
    monkeypatch.setattr(owner.native, 'atomic_write_json', original)
    inbox = owner.adapter.incoming_path(UID)
    read_text = type(inbox).read_text
    blocked = [True]
    def locked_inbox(path, *args, **kwargs):
        if blocked[0] and path == inbox:
            raise PermissionError('inbox is being replaced')
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(type(inbox), 'read_text', locked_inbox)
    await owner.replay.areconcile('Neko')
    # The handler returned instead of failing, so later events are not held back.
    event_id = json.loads((owner.directory / 'events.ndjson').read_text().splitlines()[-1])['event_id']
    assert owner.native.runtime.event_log.read_sentinel('Neko') == event_id
    blocked[0] = False
    assert await owner.native.consume(UID)
    assert local_state(owner)['base']['revision'] == 1


@pytest.mark.asyncio
async def test_replay_still_applies_an_event_that_carries_the_whole_snapshot(owner):
    await stage(owner)
    payload = json.loads(owner.adapter.incoming_path(UID).read_text())
    def fail_save(name, frozen):
        raise OSError('synthetic interrupted apply')
    with pytest.raises(OSError):
        owner.native.runtime.event_log.record_and_save('Neko', owner.native._EVENT, payload,
            sync_load_view=lambda _: deepcopy(payload), sync_mutate_view=lambda _: None, sync_save_view=fail_save)
    # Events journaled before inbox references must not depend on the inbox.
    owner.adapter.incoming_path(UID).unlink()
    assert await owner.replay.areconcile('Neko') == 1
    assert read_views(owner.directory)['persona']['master']['facts'][0]['text'] == 'learned preference'
    assert local_state(owner)['base']['revision'] == 1


@pytest.mark.asyncio
async def test_unsynced_character_capture_never_waits_for_owner_locks(owner, monkeypatch):
    owner.runtime.state.pause(UID)
    entered = []
    @asynccontextmanager
    async def locked(name):
        entered.append(name)
        yield
    monkeypatch.setattr(owner.native, '_views_locked', locked)
    owner.native.queue_capture('Neko', force=True)
    await asyncio.wait_for(owner.native._capture_tasks['Neko'], 2)
    assert entered == []
    assert UID not in owner.native._capture_intents


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
    baseline = local_state(owner)
    completed = (baseline['pending']['document']['completedAt'] if baseline else 2000) + 1
    for revision in (1, 2):
        await stage(owner, completed=completed + revision, revision=revision)
        assert await owner.native.consume(UID)
        views = read_views(owner.directory)
        assert fact in views['facts']
        assert persona in views['persona']['master']['facts']
        assert reflection in views['reflections']
        assert 'unpublished' not in {row['id'] for row in local_state(owner)['base']['nativeEntries']}
    assert owner.native._capture('Neko', False)
    captured = local_state(owner)['pending']
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
    # The remote only refreshed the row; the wording edited here and the deletion both hold.
    assert [row['text'] for row in result['persona']['master']['facts']] == ['locally edited preference']
    assert result['facts'] == []
    assert owner.republished == ['Neko']
    assert owner.native._capture('Neko', False)
    upload = local_state(owner)['pending']
    assert upload['baseRevision'] == 2
    assert {row['id']: row['text'] for row in upload['document']['entries']} == {
        'p1': 'locally edited preference', 'r1': 'confirmed impression'}


@pytest.mark.asyncio
async def test_unpublished_local_retirement_is_still_published_after_a_remote_replacement(owner):
    """Introspection retired p1 here and a remote snapshot arrives before the
    retirement is published: it survives the import and stays out of the
    receipt baseline, so the next capture publishes it as superseded."""
    await stage(owner)
    assert await owner.native.consume(UID)
    current = read_views(owner.directory)
    current['persona']['master']['facts'][0]['permanent_suppression'] = {
        'at': datetime.now().isoformat(), 'reason': 'hollow', 'issue': 'hollow',
    }
    atomic_write_json(owner.directory / 'persona.json', current['persona'])
    await stage(owner, completed=3000, revision=2)
    assert await owner.native.consume(UID)

    assert 'permanent_suppression' in read_views(owner.directory)['persona']['master']['facts'][0]
    receipt = local_state(owner)['base']
    assert {r['id']: r['metadata'] for r in receipt['nativeEntries']}['p1'].get('status') != 'superseded'
    assert owner.native._capture('Neko', False)
    captured = local_state(owner)['pending']
    assert {r['id']: r['metadata'] for r in captured['document']['entries']}['p1']['status'] == 'superseded'
    assert not owner.native._capture('Neko', False)


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
    local = local_state(owner)['pending']
    requests = []
    async def request(value):
        if value.url.path.endswith('/long-term-memory'):
            requests.append(value)
            assert value.headers['X-Neko-Chat-Binding'] == owner.binding['binding_id']
            if value.method == 'PUT':
                body = json.loads(value.content)
                assert body == {'operationId': local['operationId'], 'baseRevision': 0, 'document': local['document']}
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
    presence = await owner.runtime.session_presence_provider()
    await owner.runtime._channel_pass(UID, 'long_term', presence=presence)
    assert [request.method for request in requests] == ['PUT', 'GET']
    # Nothing is left to upload, so the next check waits for the periodic poll.
    assert await owner.runtime._channel_pass(UID, 'long_term', presence=presence) is None
    assert [request.method for request in requests] == ['PUT', 'GET']
    from services.chat_sync.scheduler import PULL_INTERVAL_MS
    owner.runtime.test_clock[0] += PULL_INTERVAL_MS
    await owner.runtime._channel_pass(UID, 'long_term', presence=presence)
    assert [request.method for request in requests] == ['PUT', 'GET', 'GET']
    # The accepted upload is now the base the next merge starts from, and nothing waits for upload.
    assert local_state(owner) == {'pending': None, 'unseenRevision': False, 'sent': [], 'republish': False, 'base': {
        'revision': 1, 'document': local['document'], 'nativeEntries': local['document']['entries']}}


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
    previous = local_state(owner)['pending']
    if unpublished:
        atomic_write_json(owner.directory / FILES['facts'], [
            {'id': 'local-only', 'text': 'new local independent fact', 'entity': 'master', 'importance': 7}])
    await stage(owner, completed=previous['document']['completedAt'] + 1)
    raw = owner.raw.read_bytes()

    owner.native.queue_capture('Neko', force=True)
    await asyncio.wait_for(asyncio.shield(owner.native._capture_tasks['Neko']), 3)

    published = local_state(owner)['pending']['document']
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
    published = local_state(owner)['pending']['document']
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
        published = local_state(owner)['pending']['document']
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
@pytest.mark.parametrize('outcome', ['accepted', 'newer', 'conflict', 'refused'])
async def test_upload_receipt_skips_own_head_but_pulls_after_a_conflict_or_a_newer_remote(owner, outcome):
    from services.chat_sync.ledger import ChatDomain
    accepted, newer = outcome in ('accepted', 'newer'), outcome == 'newer'
    assert owner.native._capture('Neko', True)
    local = local_state(owner)['pending']
    calls = []
    revision = 2 if newer else 1
    remote = {'ownerId': OWNER, 'characterSyncId': UID, 'revision': revision,
              'completedAt': local['document']['completedAt'] + 1,
              'document': learned(local['document']['completedAt'] + 1, 'remote learned state')}
    async def request(method, path, body):
        calls.append((method, path))
        if method == 'PUT':
            assert json.loads(body)['baseRevision'] == 0
            return {'ownerId': OWNER, 'characterSyncId': UID, 'operationId': local['operationId'],
                    'revision': 1, 'accepted': accepted, **({'conflict': True} if outcome == 'conflict' else {})}
        return {key: value for key, value in remote.items() if key != 'document'} if '?' in path else remote
    async def commit(fn, *args):
        return fn(*args)
    domain = ChatDomain(OWNER, UID)
    arguments = (domain, owner.binding, owner.runtime.writer.epoch(), request, commit)
    fingerprint = (await owner.runtime._session())[1]
    if outcome == 'refused':
        # Neither accepted nor a conflict: the snapshot is not recorded as uploaded and is retried afresh.
        # An OSError: the scheduler backs off and retries instead of blocking the binding.
        with pytest.raises(OSError, match='^long_term_memory_upload_refused$'):
            await owner.runtime.long_term_memory.sync(*arguments, can_apply=lambda: True, session_fingerprint=fingerprint)
        retried = local_state(owner)
        assert retried['base'] is None and retried['pending']['operationId'] != local['operationId']
        assert retried['pending']['document']['completedAt'] > local['document']['completedAt']
        assert retried['pending']['document']['entries'] == local['document']['entries']
        assert len(calls) == 1
        return
    result = await owner.runtime.long_term_memory.sync(*arguments, can_apply=lambda: True, session_fingerprint=fingerprint)
    assert result['changed'] is (newer or not accepted)
    assert len(calls) == (3 if newer or not accepted else 2)
    state = local_state(owner)
    if accepted:
        assert state['pending'] is None and state['base'] == {
            'revision': 1, 'document': local['document'], 'nativeEntries': local['document']['entries']}
    else:
        # A refused upload is never a base: the published revision waits to be merged with it.
        assert state['base'] is None and state['pending'] == local
    assert owner.adapter.incoming_path(UID).exists() is (newer or not accepted)


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

    # Equal standing keeps the published wording; a row the remote lacks is still kept.
    kept = incoming['entries'][0] if remote_change == 'changed' else row
    received = merged_import(incoming, existing, baseline=None)
    assert received['sync']['dormantEntries'] == [kept]
    assert existing == before
    assert received['facts'] == received['archive'] == received['reflections'] == []
    assert export_document(received, 4000)['entries'] == [kept]
    repeated = merged_import(incoming, received, baseline=None)
    assert repeated['sync']['dormantEntries'] == [kept]
    assert export_document(repeated, 5000)['entries'] == [kept]


@pytest.mark.parametrize('baseline_mode,expected_texts', [
    ('unchanged', ['remote revision', 'remote revision', 'remote revision', 'remote revision']),
    ('no-base', ['unpublished local history', 'local history', 'local history', 'local history']),
    # The remote revived the row; a wording both sides rewrote follows the side that did.
    ('previous', ['remote revision', 'remote revision', 'remote revision', 'remote revision']),
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
    baseline = {'unchanged': ..., 'no-base': None, 'previous': previous}[baseline_mode]

    received = merged_import(incoming, existing, baseline=baseline)
    exported = export_document(received, 4000)['entries']
    by_key = {(row['kind'], row['subject'], row['id']): row['text'] for row in exported}
    assert len(exported) == 4
    assert [by_key[key] for key in identities] == expected_texts
    dormant = received['sync']['dormantEntries']
    assert len(dormant) == {'unchanged': 0, 'no-base': 4, 'previous': 0}[baseline_mode]
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
    received = merged_import(incoming, existing, baseline=None)
    received = merged_import(export_document(received, 4000), received, baseline=None)
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
    published = local_state(owner)['pending']['document']
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
    local = local_state(owner)['pending']
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


@pytest.mark.parametrize('remote_time,proven', [(2000, True), (20000, False)])
def test_retirement_cutoff_and_source_union_never_regress_on_receive(remote_time, proven):
    value = learned()
    remote = {'id': 'retired', 'subject': 'master', 'sourceMessageIds': ['new-source'], 'retiredThroughAt': remote_time}
    if proven:
        remote['retiredThroughAtFromCorrection'] = True
    value.update(schemaVersion=2, evidence={'retirements': [remote], 'confirmations': []})
    existing = {'persona': {}, 'facts': [], 'archive': [], 'reflections': [], 'sync': {'evidence': {
        'retirements': [{'id': 'retired', 'subject': 'master', 'sourceMessageIds': ['old-source'], 'retiredThroughAt': 10000, 'retiredThroughAtFromCorrection': True}], 'confirmations': []}}}
    row = import_document(value, existing)['sync']['evidence']['retirements'][0]
    assert row['retiredThroughAt'] == 10000
    assert row['sourceMessageIds'] == ['old-source', 'new-source']


@pytest.mark.parametrize('subject', ['neko', 'relationship'])
def test_retirement_subject_collision_rejects_the_snapshot_without_changing_local_memory(subject):
    value = learned()
    local = {'id': 'retired', 'subject': 'master', 'sourceMessageIds': ['old-source'], 'retiredThroughAt': 10000}
    existing = {'persona': {}, 'facts': [], 'archive': [], 'reflections': [], 'sync': {'evidence': {
        'retirements': [local], 'confirmations': []}}}
    value.update(schemaVersion=2, evidence={'retirements': [{**local, 'subject': subject}], 'confirmations': []})
    before = deepcopy(existing)
    with pytest.raises(ValueError, match='^invalid_long_term_memory$'):
        import_document(value, existing)
    assert existing == before


@pytest.mark.parametrize('protected', [True, False])
def test_sync_retains_persona_protection_flag(protected):
    snapshot = learned()
    snapshot['entries'][0]['metadata']['protected'] = protected
    native = import_document(snapshot, {'persona': {}, 'facts': [], 'archive': [], 'reflections': []})
    assert native['persona']['master']['facts'][0]['protected'] is protected
    exported = next(row for row in export_document(native, 3000)['entries'] if row['kind'] == 'persona')
    assert exported['metadata']['protected'] is protected


@pytest.mark.parametrize('kind', ['persona', 'reflection'])
@pytest.mark.parametrize('active_duplicate', [False, True])
def test_archived_memory_replacement_retains_local_coverage_audit(kind, active_duplicate):
    entry = {'id': 'archived', 'text': 'unchanged impression', 'source': 'reflection',
             'entity_key' if kind == 'persona' else 'entity': 'master', 'status': 'confirmed',
             'covered_fact_ids': ['archive-audit'], 'archived_at': '2026-10-01T00:00:00'}
    field = 'personaArchives' if kind == 'persona' else 'reflectionArchives'
    existing = {'persona': {}, 'facts': [], 'archive': [], 'reflections': [], field: {'old.json': [entry]}}
    incoming = export_document(existing, 3000)
    if active_duplicate:
        active = {**entry, 'covered_fact_ids': ['active-audit']}
        if kind == 'persona':
            existing['persona'] = {'master': {'facts': [active]}}
        else:
            existing['reflections'] = [active]
        incoming = export_document(existing, 3000)
    before = deepcopy(existing)
    result = merged_import(incoming, existing, baseline=incoming['entries'])
    restored = [row for rows in result[field].values() for row in rows]
    assert len(restored) == 1
    # A snapshot that changes nothing leaves every row, and its audit, exactly as it was.
    assert restored[0]['covered_fact_ids'] == ['archive-audit']
    assert result[field] == existing[field]
    if active_duplicate:
        assert (result['persona']['master']['facts'] if kind == 'persona' else result['reflections']) == [active]
    assert 'covered_fact_ids' not in json.dumps(export_document(result, 3000))
    assert existing == before


@pytest.mark.parametrize('kind', ['persona', 'reflection'])
def test_import_keeps_a_retired_row_that_was_already_archived(kind):
    """An archived row retired by introspection stays on this device: the
    snapshot entry for it is skipped, so dropping the local copy would lose it."""
    entry = {'id': 'archived', 'text': 'retired impression', 'source': 'reflection',
             'entity_key' if kind == 'persona' else 'entity': 'master', 'status': 'confirmed',
             'archived_at': '2026-10-01T00:00:00',
             'permanent_suppression': {'at': '2026-10-02T00:00:00', 'reason': 'hollow', 'issue': 'hollow'}}
    field = 'personaArchives' if kind == 'persona' else 'reflectionArchives'
    existing = {'persona': {}, 'facts': [], 'archive': [], 'reflections': [], field: {'old.json': [entry]}}
    incoming = export_document(existing, 3000)
    result = import_document(incoming, existing)
    assert [row['id'] for rows in result[field].values() for row in rows] == ['archived']
    assert 'permanent_suppression' in result[field]['old.json'][0]


def test_import_keeps_local_accounting_outside_the_protocol():
    """Source counts decide whether an AI-only line waits for the user; dropping
    them on import would make every synced line count as backed."""
    fact = {'id': 'f1', 'text': 'shared experience', 'entity': 'neko', 'source': 'ai_disclosure',
            'extraction_path': 'path_b', 'hash': 'x', 'created_at': '2026-10-01T00:00:00'}
    persona = {'id': 'p1', 'text': 'learned preference', 'source': 'reflection', 'source_id': 'r0',
               'fact_source_counts': {'user': 0, 'ai': 2}}
    reflection = {'id': 'r1', 'text': 'confirmed impression', 'entity': 'neko', 'status': 'confirmed',
                  'source_fact_ids': ['f1'], 'fact_source_counts': {'user': 0, 'ai': 1},
                  'counted_fact_ids': ['f1'], 'user_confirm_count': 2,
                  'source_facts_oldest_at': '2026-10-01T00:00:00'}
    existing = {'persona': {'master': {'facts': [persona]}}, 'facts': [fact], 'archive': [], 'reflections': [reflection]}
    incoming = export_document(existing, 3000)
    result = import_document(incoming, existing)
    assert result['facts'][0]['extraction_path'] == 'path_b'
    assert result['persona']['master']['facts'][0]['fact_source_counts'] == {'user': 0, 'ai': 2}
    kept = result['reflections'][0]
    assert kept['fact_source_counts'] == {'user': 0, 'ai': 1}
    assert kept['counted_fact_ids'] == ['f1']
    assert kept['user_confirm_count'] == 2
    assert kept['source_facts_oldest_at'] == '2026-10-01T00:00:00'
    # Device-only records stay off the wire; the accounting a receiver needs travels under protocol names.
    wire = json.dumps(export_document(result, 3000))
    for field in ('fact_source_counts', 'counted_fact_ids', 'user_confirm_count', 'extraction_path', 'source_facts_oldest_at'):
        assert field not in wire
    sent = next(row for row in export_document(result, 3000)['entries'] if row['kind'] == 'reflection')['metadata']
    assert (sent['userConfirmCount'], sent['countedFactIds'], sent['factSourceCounts']) == (2, ['f1'], {'user': 0, 'ai': 1})

    # A peer's rewrite rests on other facts and says so: its accounting replaces this
    # device's, and the evidence age recorded here no longer applies.
    rewritten = deepcopy(incoming)
    row = next(row for row in rewritten['entries'] if row['kind'] == 'reflection')
    row['text'] = 'rewritten impression'
    row['metadata'].update(sourceFactIds=['f1', 'f2'], countedFactIds=['f1', 'f2'], factSourceCounts={'user': 1, 'ai': 1})
    moved = import_document(rewritten, existing)['reflections'][0]
    assert moved['counted_fact_ids'] == ['f1', 'f2'] and moved['fact_source_counts'] == {'user': 1, 'ai': 1}
    assert 'source_facts_oldest_at' not in moved and moved['user_confirm_count'] == 2

    # An older writer's rewrite carries no accounting: the old one no longer holds,
    # while an explicit confirmation still belongs to the row.
    rewritten = as_older_document(rewritten)
    moved = import_document(rewritten, existing)['reflections'][0]
    for field in ('fact_source_counts', 'counted_fact_ids', 'source_facts_oldest_at'):
        assert field not in moved
    assert moved['user_confirm_count'] == 2


def test_first_import_counts_the_facts_the_snapshot_carries():
    """An AI-only line new to this device must not count as backed."""
    from memory.evidence import awaits_user_signal
    entries = [
        {'kind': 'fact', 'subject': 'neko', 'id': 'f-ai', 'text': 'ai line', 'metadata': {'createdAt': 1000, 'source': 'ai_disclosure'}},
        {'kind': 'fact', 'subject': 'master', 'id': 'f-user', 'text': 'user line', 'metadata': {'createdAt': 1000, 'source': 'user_observation'}},
    ]
    def reflection(rid, facts, status='pending'):
        return {'kind': 'reflection', 'subject': 'neko', 'id': rid, 'text': rid,
                'metadata': {'createdAt': 1000, 'status': status, 'sourceFactIds': facts}}
    incoming = {'schemaVersion': 2, 'completedAt': 3000, 'evidence': {'retirements': [], 'confirmations': []}, 'entries': entries + [
        reflection('ai-only', ['f-ai']), reflection('backed', ['f-ai', 'f-user']), reflection('unknown', ['f-ai', 'f-gone']),
        reflection('confirmed', ['f-ai'], 'confirmed'),
    ]}
    existing = {'persona': {}, 'facts': [], 'archive': [], 'reflections': []}
    rows = {row['id']: row for row in import_document(incoming, existing)['reflections']}
    assert rows['ai-only']['fact_source_counts'] == {'user': 0, 'ai': 1}
    assert rows['ai-only']['counted_fact_ids'] == ['f-ai']
    assert awaits_user_signal(rows['ai-only'])
    assert rows['backed']['fact_source_counts'] == {'user': 1, 'ai': 1}
    assert not awaits_user_signal(rows['backed'])
    # A fact the snapshot does not carry cannot be counted, so the line stays unaccounted.
    assert 'fact_source_counts' not in rows['unknown']
    # The sender's confirmation is not in the protocol; holding the line back here would strand it.
    assert not awaits_user_signal(rows['confirmed'])

    # A local row from before the accounting stays unaccounted too.
    legacy = {'id': 'ai-only', 'text': 'ai-only', 'entity': 'neko', 'status': 'pending', 'source_fact_ids': ['f-ai']}
    kept = import_document(incoming, {**existing, 'reflections': [legacy]})['reflections']
    assert 'fact_source_counts' not in next(row for row in kept if row['id'] == 'ai-only')


def test_first_import_counts_a_retained_local_upgrade_as_the_users_fact():
    """A pending local upgrade outlives the import, so the older remote label must not count."""
    from memory.evidence import awaits_user_signal
    fact = {'id': 'f1', 'text': 'likes tea', 'entity': 'neko', 'source': 'ai_disclosure', 'hash': 'x', 'created_at': '2026-10-01T00:00:00'}
    views = {'persona': {}, 'facts': [fact], 'archive': [], 'reflections': []}
    baseline = export_document(views, 3000)
    incoming = deepcopy(baseline)
    incoming['entries'].append({'kind': 'reflection', 'subject': 'neko', 'id': 'r1', 'text': 'new line',
                                'metadata': {'createdAt': 1000, 'status': 'pending', 'sourceFactIds': ['f1']}})
    existing = {**views, 'facts': [{**fact, 'source': 'user_observation'}]}
    result = merged_import(incoming, existing, baseline=baseline['entries'])
    assert result['facts'][0]['source'] == 'user_observation'
    assert result['reflections'][0]['fact_source_counts'] == {'user': 1, 'ai': 0}
    assert not awaits_user_signal(result['reflections'][0])

    # An interrupted archive write can leave an older copy; the active row is the one that holds.
    interrupted = {**existing, 'archive': [fact]}
    result = merged_import(incoming, interrupted, baseline=baseline['entries'])
    assert result['reflections'][0]['fact_source_counts'] == {'user': 1, 'ai': 0}


def test_a_line_confirmed_by_the_sender_is_not_held_back_by_local_counts():
    from memory.evidence import awaits_user_signal
    fact = {'id': 'f1', 'text': 'ai line', 'entity': 'neko', 'source': 'ai_disclosure', 'hash': 'x', 'created_at': '2026-10-01T00:00:00'}
    local = {'id': 'r1', 'text': 'line', 'entity': 'neko', 'status': 'pending', 'source_fact_ids': ['f1'],
             'fact_source_counts': {'user': 0, 'ai': 1}, 'counted_fact_ids': ['f1'], 'source_facts_oldest_at': '2026-10-01T00:00:00'}
    existing = {'persona': {}, 'facts': [fact], 'archive': [], 'reflections': [local]}
    incoming = export_document(existing, 3000)
    assert awaits_user_signal(import_document(incoming, existing)['reflections'][0])
    # The sender's user confirmed it: the confirmation travels, so the line is not held back here.
    confirmed = deepcopy(incoming)
    next(row for row in confirmed['entries'] if row['kind'] == 'reflection')['metadata'].update(status='confirmed', userConfirmCount=1)
    row = import_document(confirmed, existing)['reflections'][0]
    assert row['user_confirm_count'] == 1 and not awaits_user_signal(row)
    assert row['source_facts_oldest_at'] == '2026-10-01T00:00:00'
    # An older writer cannot say why it confirmed the line; stale local counts must not hold it.
    older = as_older_document(incoming)
    next(row for row in older['entries'] if row['kind'] == 'reflection')['metadata']['status'] = 'confirmed'
    row = import_document(older, existing)['reflections'][0]
    assert not awaits_user_signal(row)
    assert row['source_facts_oldest_at'] == '2026-10-01T00:00:00'


@pytest.mark.parametrize('status', ['superseded', 'retracted'])
def test_an_archived_retired_reflection_survives_a_reexport(status):
    entry = {'kind': 'reflection', 'subject': 'neko', 'id': 'r1', 'text': 'old line',
             'metadata': {'createdAt': 1000, 'status': status, 'archivedAt': 2000, 'sourceFactIds': []}}
    incoming = {'schemaVersion': 2, 'completedAt': 3000, 'evidence': {'retirements': [], 'confirmations': []}, 'entries': [entry]}
    imported = import_document(incoming, {'persona': {}, 'facts': [], 'archive': [], 'reflections': []})
    [row] = export_document(imported, 4000)['entries']
    assert row['id'] == 'r1' and row['metadata']['status'] == status


def test_export_removes_repeated_source_ids_before_the_transfer_limit():
    unique = [f'source-{index}' for index in range(150)]
    fact = {'id': 'f1', 'text': 'likes tea', 'entity': 'master', 'source': 'user_observation', 'hash': 'x',
            'created_at': '2026-10-01T00:00:00', 'source_message_ids': unique + unique}
    [row] = export_document({'persona': {}, 'facts': [fact], 'archive': [], 'reflections': []}, 3000)['entries']
    assert row['metadata']['sourceMessageIds'] == unique


def test_a_consistent_read_detects_a_commit_right_after_its_journal_check(tmp_path, monkeypatch):
    from memory import long_term_sync
    from memory.long_term_sync import LongTermMemoryRecoveryRequired, advance_view_generation, consistent_view_read
    original = long_term_sync.require_consistent_views
    calls = []
    def commit_after_first_check(directory):
        original(directory)
        calls.append(directory)
        if len(calls) == 1:
            advance_view_generation(directory)
    monkeypatch.setattr(long_term_sync, 'require_consistent_views', commit_after_first_check)
    with pytest.raises(LongTermMemoryRecoveryRequired):
        with consistent_view_read(tmp_path / 'facts.json'):
            pass


def test_dropped_citations_stay_dropped_after_a_reimport():
    """A source upgrade without a citation removes the character lines as proof;
    the copy kept from the import must not publish them under the user's source."""
    fact = {'id': 'f1', 'text': 'likes tea', 'entity': 'neko', 'source': 'ai_disclosure', 'hash': 'x',
            'created_at': '2026-10-01T00:00:00', 'source_message_ids': ['raw-ai'], 'source_created_at': '2026-10-01T00:00:00'}
    views = {'persona': {}, 'facts': [fact], 'archive': [], 'reflections': []}
    imported = import_document(export_document(views, 3000), {'persona': {}, 'facts': [], 'archive': [], 'reflections': []})
    row = imported['facts'][0]
    assert row['_sync_metadata']['sourceMessageIds'] == ['raw-ai']
    assert export_document(imported, 3000)['entries'][0]['metadata']['sourceMessageIds'] == ['raw-ai']
    row['source'] = 'user_observation'
    row.pop('source_message_ids')
    row.pop('source_created_at', None)
    metadata = export_document(imported, 3000)['entries'][0]['metadata']
    assert metadata['source'] == 'user_observation'
    assert 'sourceMessageIds' not in metadata and 'sourceCreatedAt' not in metadata


def test_event_when_uses_the_native_field_through_a_roundtrip():
    when = {'start': {'offset': -1, 'unit': 'day'}, 'end': None}
    fact = {'id': 'f1', 'text': 'went hiking', 'entity': 'master', 'source': 'user_observation',
            'hash': 'x', 'created_at': '2026-10-01T00:00:00', 'event_when_raw': when}
    views = {'persona': {}, 'facts': [fact], 'archive': [], 'reflections': []}
    exported = export_document(views, 3000)
    assert exported['entries'][0]['metadata']['eventWhen'] == when
    fresh = {'persona': {}, 'facts': [], 'archive': [], 'reflections': []}
    assert import_document(exported, fresh)['facts'][0]['event_when_raw'] == when


@pytest.mark.asyncio
@pytest.mark.parametrize('subject,role,resolved', [('neko', 'human', True), ('neko', 'ai', True),
                                                   ('master', 'human', True), ('master', 'ai', False)])
async def test_correction_time_accepts_the_roles_that_may_correct_the_subject(subject, role, resolved):
    from memory.long_term_sync import resolve_retirement_cutoffs
    identity = 'sync:' + 'c' * 32
    row = {'id': 'r1', 'subject': subject, 'sourceMessageIds': ['old'], 'retiredBySourceMessageIds': [identity]}
    async def lookup(ids):
        return {identity: (role, 2000)}
    [result] = await resolve_retirement_cutoffs([row], [], lookup=lookup)
    assert result.get('retiredThroughAt') == (2000 if resolved else None)


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


@pytest.mark.asyncio
async def test_native_capture_makes_legacy_subject_durable_before_journal_compaction(owner):
    from datetime import datetime
    from memory.evidence_handlers import make_archive_copy_check
    from tests.unit.test_event_log_compaction import _fresh_log, _knobs
    path = owner.directory / 'persona_archive' / '2026-10-01_aaaaaaaa.json'
    path.parent.mkdir()
    row = {'id': 'legacy', 'text': 'An archived relationship', 'archived_at': '2026-10-01T00:00:00'}
    atomic_write_json(path, [row])
    event = {'event_id': 'archived', 'ts': '2026-10-01T00:00:00', 'type': 'persona.fact_added',
             'payload': {'entity_key': 'relationship', 'entry_id': row['id'], 'archive_shard_path': path.name,
                         'entry_snapshot': row}}
    journal = owner.directory / 'events.ndjson'
    journal.write_text('\n'.join(json.dumps(item) for item in [event,
        {'event_id': 'noise', 'ts': '2026-10-01T00:00:00', 'type': 'old', 'payload': {}},
        {'event_id': 'anchor', 'ts': '2026-10-02T00:00:00', 'type': 'anchor', 'payload': {}}]) + '\n', encoding='utf-8')
    log = _fresh_log(str(owner.config.memory_dir))
    log.advance_sentinel('Neko', 'anchor')
    before_sentinel = (owner.directory / 'events_applied.json').read_bytes()
    with _knobs():
        check = make_archive_copy_check(str(owner.directory))
        first = log.compact_applied_history('Neko', needs_journal_copy=check, sources_unchanged=check.sources_unchanged,
                                           now=datetime(2026, 11, 3))
        assert first.pinned == 1
        assert '"archived"' in journal.read_text(encoding='utf-8')
        assert owner.native._capture('Neko', True)
        assert json.loads(path.read_text(encoding='utf-8')) == [{**row, 'entity_key': 'relationship'}]
        check = make_archive_copy_check(str(owner.directory))
        second = log.compact_applied_history('Neko', needs_journal_copy=check, sources_unchanged=check.sources_unchanged,
                                            now=datetime(2026, 11, 3))
        assert second.pinned == 0 and second.dropped_lines == 1
    assert '"archived"' not in journal.read_text(encoding='utf-8')
    assert export_document(read_views(owner.directory), 3000)['entries'][0]['subject'] == 'relationship'
    assert (owner.directory / 'events_applied.json').read_bytes() == before_sentinel


@pytest.mark.asyncio
async def test_unrecoverable_archive_preserves_data_and_reports_backup_recovery(owner):
    path = owner.directory / 'persona_archive' / '2026-10-01_aaaaaaaa.json'
    path.parent.mkdir()
    row = {'id': 'unknown', 'text': 'Unknown archived subject', 'archived_at': '2026-10-01T00:00:00'}
    atomic_write_json(path, [row])
    before = {file: file.read_bytes() for file in [path, *(owner.directory / name for name in FILES.values())]}
    with pytest.raises(ValueError, match='^long_term_archive_subject_unavailable$'):
        owner.native._capture('Neko', True)
    assert not owner.adapter.local_path(owner.binding).exists()
    assert (await owner.runtime.status())['bindings'][0]['localMemoryDiagnostics'] == ['long_term_archive_subject_unavailable']
    await stage(owner)
    with pytest.raises(ValueError, match='^long_term_archive_subject_unavailable$'):
        await owner.native.consume(UID)
    assert owner.adapter.incoming_path(UID).exists()
    assert not (owner.directory / 'events.ndjson').exists()
    assert before == {file: file.read_bytes() for file in before}
    # Old unusable sync references must not block later local memory events.
    import hashlib
    from memory.event_log import EVT_FACT_ADDED
    inbox = owner.adapter.incoming_path(UID)
    owner.native.runtime.event_log.append('Neko', 'memory.long_term_replaced', {
        'characterSyncId': UID, 'inboxSha256': hashlib.sha256(inbox.read_text(encoding='utf-8').encode('utf-8')).hexdigest()})
    owner.native.runtime.event_log.append('Neko', EVT_FACT_ADDED, {'fact_id': 'later-local-fact'})
    applied = []
    owner.replay.register(EVT_FACT_ADDED, lambda name, row: (applied.append(row['fact_id']) or True))
    assert await owner.replay.areconcile('Neko') == 1
    assert applied == ['later-local-fact']
    assert owner.adapter.incoming_path(UID).exists()
    # A verified backup supplies the missing metadata; no subject is inferred.
    atomic_write_json(path, [{**row, 'entity_key': 'master'}])
    assert await owner.native.consume(UID)
    assert (await owner.runtime.status())['bindings'][0]['localMemoryDiagnostics'] == []
    owner.native._capture('Neko', True)
    assert (await owner.runtime.status())['bindings'][0]['localMemoryDiagnostics'] == []


@pytest.mark.asyncio
async def test_incoming_archive_failure_records_diagnostic_without_prior_capture(owner, monkeypatch):
    path = owner.directory / 'persona_archive' / '2026-10-01_aaaaaaaa.json'
    path.parent.mkdir()
    atomic_write_json(path, [{'id': 'unknown', 'text': 'Unknown archive subject'}])
    monkeypatch.setattr(owner.native, '_capture_scheduler', lambda _: owner.runtime.scheduler)
    await stage(owner)
    with pytest.raises(ValueError, match='^long_term_archive_subject_unavailable$'):
        await owner.native.consume(UID)
    assert (await owner.runtime.status())['bindings'][0]['localMemoryDiagnostics'] == [
        'long_term_archive_subject_unavailable', 'long_term_memory_incoming_pending']
    assert not owner.adapter.local_path(owner.binding).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['sqlite', 'io', 'value'])
async def test_archive_diagnostic_storage_failure_preserves_original_error_and_later_replay(owner, monkeypatch, failure):
    import sqlite3
    from memory.event_log import EVT_FACT_ADDED
    path = owner.directory / 'persona_archive' / '2026-10-01_aaaaaaaa.json'
    path.parent.mkdir()
    atomic_write_json(path, [{'id': 'unknown', 'text': 'Unknown archive subject'}])
    await stage(owner)
    inbox = owner.adapter.incoming_path(UID)
    payload = json.loads(inbox.read_text(encoding='utf-8'))
    def denied(*args):
        raise {'sqlite': sqlite3.OperationalError, 'io': OSError, 'value': ValueError}[failure]('diagnostic unavailable')
    monkeypatch.setattr(owner.native, '_capture_scheduler', lambda _: owner.runtime.scheduler)
    monkeypatch.setattr(owner.runtime.scheduler, 'record_long_term_capture_error', denied)
    with pytest.raises(ValueError, match='^long_term_archive_subject_unavailable$'):
        owner.native._write_snapshot('Neko', payload, check_only=True)
    assert owner.native._write_snapshot('Neko', payload) is False
    owner.native.runtime.event_log.append('Neko', 'memory.long_term_replaced', {
        'characterSyncId': UID, 'inboxSha256': owner.native._inbox_digest(inbox.read_text(encoding='utf-8'))})
    owner.native.runtime.event_log.append('Neko', EVT_FACT_ADDED, {'fact_id': 'later-local'})
    applied = []
    owner.replay.register(EVT_FACT_ADDED, lambda name, row: (applied.append(row['fact_id']) or True))
    assert await owner.replay.areconcile('Neko') == 1
    assert applied == ['later-local']
    assert inbox.exists() and not owner.adapter.local_path(owner.binding).exists()
    owner.runtime.writer.check()


@pytest.mark.asyncio
@pytest.mark.parametrize('check_only', [False, True])
async def test_archive_subject_error_survives_authority_loss_after_binding_validation(owner, monkeypatch, check_only):
    path = owner.directory / 'persona_archive' / '2026-10-01_aaaaaaaa.json'
    path.parent.mkdir()
    atomic_write_json(path, [{'id': 'unknown', 'text': 'Unknown archive subject'}])
    await stage(owner)
    payload = json.loads(owner.adapter.incoming_path(UID).read_text(encoding='utf-8'))
    original_binding = owner.native._binding
    available, reads = [True], []
    def binding(uid):
        result = original_binding(uid)
        reads.append(uid)
        if len(reads) == 2:
            available[0] = False
        return result
    monkeypatch.setattr(owner.native, '_binding', binding)
    monkeypatch.setattr(owner.native, '_authority', lambda: owner.runtime.writer if available[0] else None)
    before = path.read_bytes()
    if check_only:
        with pytest.raises(ValueError, match='^long_term_archive_subject_unavailable$'):
            owner.native._write_snapshot('Neko', payload, check_only=True)
    else:
        assert owner.native._write_snapshot('Neko', payload) is False
    assert reads == [UID, UID]
    assert path.read_bytes() == before
    assert not owner.adapter.local_path(owner.binding).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('replacement', [False, True])
@pytest.mark.parametrize('code', ['long_term_archive_subject_unavailable', 'long_term_memory_capacity_exceeded'])
async def test_consumed_inbox_clears_only_the_repaired_archive_diagnostic(owner, monkeypatch, replacement, code):
    await stage(owner)
    owner.runtime.scheduler.record_long_term_capture_error(DOMAIN.key(), owner.binding['generation'], code)
    inbox = owner.adapter.incoming_path(UID)
    pending = json.loads(inbox.read_text(encoding='utf-8'))
    if replacement:
        original = owner.native.runtime.event_log.record_and_save
        def replace(*args, **kwargs):
            result = original(*args, **kwargs)
            pending['head']['revision'] += 1
            pending['head']['completedAt'] += 1
            pending['head']['document']['completedAt'] += 1
            atomic_write_json(inbox, pending)
            return result
        monkeypatch.setattr(owner.native.runtime.event_log, 'record_and_save', replace)
    assert await owner.native.consume(UID)
    expected = [] if not replacement and code == 'long_term_archive_subject_unavailable' else [code]
    assert (await owner.runtime.status())['bindings'][0]['localMemoryDiagnostics'] == (
        expected + (['long_term_memory_incoming_pending'] if replacement else []))
    assert inbox.exists() == replacement


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['sqlite', 'io', 'value'])
async def test_archive_notice_cleanup_failure_retries_durably_without_reapplying(owner, monkeypatch, failure):
    import sqlite3
    from memory.long_term_sync import JOURNAL
    from services.chat_sync.scheduler import ChatSyncScheduler
    await stage(owner)
    owner.runtime.scheduler.record_long_term_capture_error(DOMAIN.key(), owner.binding['generation'],
                                                          'long_term_archive_subject_unavailable')
    def denied(*args):
        raise {'sqlite': sqlite3.OperationalError, 'io': OSError, 'value': ValueError}[failure]('diagnostic unavailable')
    monkeypatch.setattr(owner.native, '_capture_scheduler', lambda _: owner.runtime.scheduler)
    monkeypatch.setattr(owner.runtime.scheduler, 'clear_archive_subject_error', denied)
    assert await owner.native.consume(UID)
    assert not owner.adapter.incoming_path(UID).exists()
    assert local_state(owner)['base']['revision'] == 1
    journal = owner.directory / JOURNAL
    assert json.loads(journal.read_text(encoding='utf-8'))['phase'] == 'closed'
    assert read_views(owner.directory)['facts'][0]['id'] == 'f1'
    before = {path: path.read_bytes() for path in [
        *(owner.directory / name for name in FILES.values()), owner.directory / 'events.ndjson',
        owner.directory / 'events_applied.json', owner.adapter.local_path(owner.binding)]}
    assert await owner.native.consume_for_name('Neko') is False
    assert journal.is_file()
    assert owner.runtime.scheduler.local_memory_diagnostics(DOMAIN.key()) == ['long_term_archive_subject_unavailable']
    owner.runtime.writer.check()
    # Reopen the on-disk scheduler, as after a process restart.
    scheduler = ChatSyncScheduler(owner.runtime.writer.directory / 'scheduler.sqlite3')
    monkeypatch.setattr(owner.native, '_capture_scheduler', lambda _: scheduler)
    assert await owner.native.consume_for_name('Neko') is False
    assert not journal.exists()
    assert scheduler.local_memory_diagnostics(DOMAIN.key()) == []
    assert before == {path: path.read_bytes() for path in before}
    owner.runtime.writer.check()


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['owner', 'generation', 'epoch', 'receipt', 'inbox', 'archive'])
async def test_archive_cleanup_retry_cannot_clear_a_newer_or_unrelated_problem(owner, monkeypatch, changed):
    from memory.long_term_sync import JOURNAL
    await stage(owner)
    owner.runtime.scheduler.record_long_term_capture_error(DOMAIN.key(), owner.binding['generation'],
                                                          'long_term_archive_subject_unavailable')
    original = owner.runtime.scheduler.clear_archive_subject_error
    def denied(*args):
        raise OSError('diagnostic unavailable')
    monkeypatch.setattr(owner.native, '_capture_scheduler', lambda _: owner.runtime.scheduler)
    monkeypatch.setattr(owner.runtime.scheduler, 'clear_archive_subject_error', denied)
    assert await owner.native.consume(UID)
    attempts = []
    def observed(*args):
        attempts.append(args)
        return original(*args)
    monkeypatch.setattr(owner.runtime.scheduler, 'clear_archive_subject_error', observed)
    journal = owner.directory / JOURNAL
    value = json.loads(journal.read_text(encoding='utf-8'))
    cleanup = value['archiveDiagnosticCleanup']
    if changed == 'owner':
        cleanup['binding']['owner_id'] = 'another-owner'
    elif changed == 'generation':
        cleanup['binding']['generation'] += 1
    elif changed == 'epoch':
        cleanup['epoch'][1] = '0' * 32
    elif changed == 'receipt':
        receipt = owner.adapter.local_path(owner.binding)
        local = json.loads(receipt.read_text(encoding='utf-8'))
        local['operationId'] = 'newer-local'
        atomic_write_json(receipt, local)
    elif changed == 'inbox':
        await stage(owner, completed=3000, revision=2)
    else:
        path = owner.directory / 'persona_archive' / '2026-10-01_aaaaaaaa.json'
        path.parent.mkdir()
        atomic_write_json(path, [{'id': 'new-unknown', 'text': 'A later unresolved archive'}])
    atomic_write_json(journal, value)
    before = {path: path.read_bytes() for path in owner.directory.rglob('*') if path.is_file() and path != journal}
    if changed in {'owner', 'generation'}:
        def unexpected_receipt(*args):
            pytest.fail('A stale binding must be rejected before accessing its receipt')
        monkeypatch.setattr(type(owner.adapter), 'read_local', unexpected_receipt)
    owner.native._recover_snapshot('Neko')
    assert attempts == []
    assert owner.runtime.scheduler.local_memory_diagnostics(DOMAIN.key()) == ['long_term_archive_subject_unavailable']
    assert journal.exists() == (changed in {'inbox', 'archive'})
    assert before == {path: path.read_bytes() for path in before}
    owner.runtime.writer.check()


@pytest.mark.asyncio
async def test_malformed_cleanup_marker_is_retained_without_opening_writer(owner, monkeypatch):
    from memory.long_term_sync import JOURNAL

    journal = owner.directory / JOURNAL
    atomic_write_json(journal, {'version': 1, 'phase': 'closed', 'archiveDiagnosticCleanup': None})
    original = journal.read_bytes()
    def unexpected_writer():
        pytest.fail('Malformed cleanup metadata must not reach the writer')
    monkeypatch.setattr(owner.native, '_authority', unexpected_writer)
    assert await owner.native.consume_for_name('Neko') is False
    assert journal.read_bytes() == original
    assert read_views(owner.directory)['facts'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('existing', ['missing', 'null', 'incomplete'])
async def test_repeated_snapshot_restores_cleanup_marker_before_removing_inbox(owner, monkeypatch, existing):
    import sqlite3
    from memory.long_term_sync import JOURNAL

    await stage(owner)
    assert await owner.native.consume(UID)
    await stage(owner, leftover=True)
    inbox = owner.adapter.incoming_path(UID)
    journal = owner.directory / JOURNAL
    assert not journal.exists()
    if existing != 'missing':
        cleanup = None if existing == 'null' else {'binding': owner.binding}
        atomic_write_json(journal, {'version': 1, 'phase': 'closed', 'archiveDiagnosticCleanup': cleanup})
    original_marker = journal.read_bytes() if journal.is_file() else None
    owner.runtime.scheduler.record_long_term_capture_error(DOMAIN.key(), owner.binding['generation'],
                                                          'long_term_archive_subject_unavailable')
    monkeypatch.setattr(owner.native, '_capture_scheduler', lambda _: owner.runtime.scheduler)
    before = {path: path.read_bytes() for path in [
        *(owner.directory / name for name in FILES.values()), owner.directory / '.long_term_generation',
        owner.adapter.local_path(owner.binding)]}
    attempts = []
    original_clear = owner.runtime.scheduler.clear_archive_subject_error
    def denied_clear(*args):
        attempts.append(args)
        raise sqlite3.OperationalError('diagnostic unavailable')
    monkeypatch.setattr(owner.runtime.scheduler, 'clear_archive_subject_error', denied_clear)
    original_write = owner.native.atomic_write_json
    def denied_marker(path, *args, **kwargs):
        assert path == journal
        raise OSError('marker unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(owner.native, 'atomic_write_json', denied_marker)
        assert await owner.native.consume(UID) is False
    assert inbox.is_file()
    assert (journal.read_bytes() if journal.is_file() else None) == original_marker
    assert not attempts
    owner.runtime.writer.check()

    def only_marker(path, *args, **kwargs):
        assert path == journal
        return original_write(path, *args, **kwargs)
    monkeypatch.setattr(owner.native, 'atomic_write_json', only_marker)
    assert await owner.native.consume(UID)
    assert not inbox.exists() and journal.is_file()
    assert attempts == [(DOMAIN.key(), owner.binding['generation'])]
    assert owner.runtime.scheduler.local_memory_diagnostics(DOMAIN.key()) == ['long_term_archive_subject_unavailable']
    monkeypatch.setattr(owner.runtime.scheduler, 'clear_archive_subject_error', original_clear)
    assert await owner.native.consume_for_name('Neko') is False
    assert not journal.exists()
    assert owner.runtime.scheduler.local_memory_diagnostics(DOMAIN.key()) == []
    assert before == {path: path.read_bytes() for path in before}


@pytest.mark.asyncio
@pytest.mark.parametrize('marker', [b'{"version":1,"phase":"pending"}', b'{unreadable'])
async def test_consume_preserves_recovery_journal_changed_before_inbox_removal(owner, monkeypatch, marker):
    from memory.long_term_sync import JOURNAL

    await stage(owner)
    journal = owner.directory / JOURNAL
    inbox = owner.adapter.incoming_path(UID)
    original_inbox = inbox.read_bytes()
    log = owner.native.runtime.event_log
    original = log.record_and_save
    def save_and_interrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        journal.write_bytes(marker)
        return result
    monkeypatch.setattr(log, 'record_and_save', save_and_interrupt)
    assert await owner.native.consume(UID) is False
    assert journal.read_bytes() == marker
    assert inbox.read_bytes() == original_inbox
    owner.runtime.writer.check()


@pytest.mark.asyncio
async def test_committed_snapshot_cleanup_survives_crash_before_inbox_removal(owner, monkeypatch):
    from memory.long_term_sync import JOURNAL
    await stage(owner)
    owner.runtime.scheduler.record_long_term_capture_error(DOMAIN.key(), owner.binding['generation'],
                                                          'long_term_archive_subject_unavailable')
    monkeypatch.setattr(owner.native, '_capture_scheduler', lambda _: owner.runtime.scheduler)
    inbox = owner.adapter.incoming_path(UID)
    payload = json.loads(inbox.read_text(encoding='utf-8'))
    assert owner.native._write_snapshot('Neko', payload)
    journal = owner.directory / JOURNAL
    assert journal.is_file()
    owner.native._recover_snapshot('Neko')
    assert journal.is_file()
    assert owner.runtime.scheduler.local_memory_diagnostics(DOMAIN.key()) == ['long_term_archive_subject_unavailable']
    before = {path: path.read_bytes() for path in [
        *(owner.directory / name for name in FILES.values()), owner.directory / '.long_term_generation',
        owner.adapter.local_path(owner.binding)]}
    def no_reapply(*args, **kwargs):
        pytest.fail('A committed snapshot must not be written again')
    monkeypatch.setattr(owner.native, 'atomic_write_json', no_reapply)
    assert await owner.native.consume(UID)
    assert not inbox.exists() and not journal.exists()
    assert owner.runtime.scheduler.local_memory_diagnostics(DOMAIN.key()) == []
    assert before == {path: path.read_bytes() for path in before}


@pytest.mark.asyncio
async def test_cleanup_marker_unlink_failure_does_not_quarantine_writer_or_repeat_apply(owner, monkeypatch):
    from memory.long_term_sync import JOURNAL
    await stage(owner)
    journal = owner.directory / JOURNAL
    original = type(journal).unlink
    def denied(path, *args, **kwargs):
        if path == journal:
            raise OSError('cleanup marker unavailable')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(type(journal), 'unlink', denied)
    assert await owner.native.consume(UID)
    assert journal.is_file() and not owner.adapter.incoming_path(UID).exists()
    owner.runtime.writer.check()
    monkeypatch.setattr(type(journal), 'unlink', original)
    assert await owner.native.consume_for_name('Neko') is False
    assert not journal.exists()
    owner.runtime.writer.check()


@pytest.mark.parametrize('conflicting', [False, True])
def test_legacy_subject_recovery_matches_the_archived_copy_and_refuses_ambiguous_events(tmp_path, conflicting):
    path = tmp_path / 'persona_archive' / '2026-10-01_aaaaaaaa.json'
    path.parent.mkdir()
    row = {'id': 'same-id', 'text': 'An old archive', 'archived_at': '2026-10-01T00:00:00'}
    atomic_write_json(path, [row])
    def event(subject, stamp):
        return {'type': 'persona.fact_added', 'payload': {'entry_id': row['id'], 'entity_key': subject,
                'archive_shard_path': path.name, 'archived_at': stamp}}
    events = [event('master', row['archived_at']), event('neko', row['archived_at'] if conflicting else '2026-10-02T00:00:00')]
    (tmp_path / 'events.ndjson').write_text('{broken\n[]\n' + '\n'.join(map(json.dumps, events)), encoding='utf-8')
    if conflicting:
        with pytest.raises(ValueError, match='^long_term_archive_subject_unavailable$'):
            read_views(tmp_path)
    else:
        assert read_views(tmp_path)['personaArchives']['persona_archive/' + path.name][0]['entity_key'] == 'master'
    assert json.loads(path.read_text(encoding='utf-8')) == [row]


def test_subjectless_legacy_event_cannot_assign_one_subject_to_two_copies(tmp_path):
    path = tmp_path / 'persona_archive' / '2026-10-01_aaaaaaaa.json'
    path.parent.mkdir()
    rows = [{'id': 'same', 'text': text} for text in ('First archive', 'Second archive')]
    atomic_write_json(path, rows)
    (tmp_path / 'events.ndjson').write_text(json.dumps({'type': 'persona.fact_added', 'payload': {
        'entry_id': 'same', 'entity_key': 'master', 'archive_shard_path': path.name}}), encoding='utf-8')
    with pytest.raises(ValueError, match='^long_term_archive_subject_unavailable$'):
        read_views(tmp_path)
    assert json.loads(path.read_text(encoding='utf-8')) == rows


@pytest.mark.asyncio
async def test_failed_subject_backfill_keeps_the_original_shard_and_journal_evidence(owner, monkeypatch):
    from memory.evidence_handlers import make_archive_copy_check
    path = owner.directory / 'persona_archive' / '2026-10-01_aaaaaaaa.json'
    path.parent.mkdir()
    row = {'id': 'old', 'text': 'Legacy archive', 'archived_at': '2026-10-01T00:00:00'}
    atomic_write_json(path, [row])
    event = {'type': 'persona.fact_added', 'payload': {'entry_id': 'old', 'entity_key': 'master',
             'archive_shard_path': path.name, 'entry_snapshot': row}}
    journal = owner.directory / 'events.ndjson'
    journal.write_text(json.dumps(event) + '\n', encoding='utf-8')
    before = {file: file.read_bytes() for file in (path, journal)}
    def denied(*args, **kwargs):
        raise OSError('write unavailable')
    monkeypatch.setattr(owner.native, 'atomic_write_text', denied)
    with pytest.raises(ValueError, match='^long_term_memory_save_failed$'):
        owner.native._capture('Neko', True)
    assert before == {file: file.read_bytes() for file in before}
    assert not owner.adapter.local_path(owner.binding).exists()
    assert make_archive_copy_check(str(owner.directory))(event)
    owner.runtime.writer.check()


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
    received = merged_import({'schemaVersion':2, 'completedAt':4000, 'entries':[], 'evidence':{'retirements':[],'confirmations':[]}}, views, baseline=None)
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


class PublishingServer:
    """The server contract for learned memory: a write lands only on the revision it names."""

    def __init__(self, *, compare_completion_time=False):
        self.revision, self.document, self.compare_completion_time = 0, None, compare_completion_time
        self.calls = []

    def publish(self, document):
        """Another device's accepted write."""
        self.revision, self.document = self.revision + 1, deepcopy(document)

    async def request(self, method, path, body):
        self.calls.append(method if '?' not in path else 'HEAD')
        if method == 'PUT':
            value = json.loads(body)
            stale = ((self.document is not None and value['document']['completedAt'] <= self.document['completedAt'])
                     if self.compare_completion_time or 'baseRevision' not in value else value['baseRevision'] != self.revision)
            if stale:
                return {'ownerId': OWNER, 'characterSyncId': UID, 'operationId': value['operationId'],
                        'revision': self.revision, 'accepted': False,
                        **({'conflict': True} if 'baseRevision' in value and not self.compare_completion_time else {})}
            self.publish(value['document'])
            return {'ownerId': OWNER, 'characterSyncId': UID, 'operationId': value['operationId'],
                    'revision': self.revision, 'accepted': True}
        head = {'ownerId': OWNER, 'characterSyncId': UID, 'revision': self.revision,
                'completedAt': self.document['completedAt'] if self.document else None}
        return head if '?' in path else {**head, 'document': deepcopy(self.document)}

    async def exchange(self, owner):
        from services.chat_sync.ledger import ChatDomain
        async def commit(fn, *args):
            return fn(*args)
        return await owner.runtime.long_term_memory.sync(
            ChatDomain(OWNER, UID), owner.binding, owner.runtime.writer.epoch(), self.request, commit,
            can_apply=lambda: True, session_fingerprint=(await owner.runtime._session())[1])


def native_fact(identity, text, **fields):
    return {'id': identity, 'entity': 'master', 'text': text, 'source': 'user_observation', 'importance': 5, **fields}


def published_facts(server):
    return {row['id'] for row in server.document['entries'] if row['kind'] == 'fact'}


@pytest.mark.asyncio
async def test_a_later_snapshot_from_a_device_that_never_saw_a_fact_does_not_remove_it(owner):
    """Device A published shared + here. This device never pulled it and uploads shared alone, later."""
    server = PublishingServer()
    server.publish(export_document({'persona': {}, 'archive': [], 'reflections': [], 'facts': [
        native_fact('shared', 'known to both devices'), native_fact('here', 'picked and published by the other device')]}, 1000))
    atomic_write_json(owner.directory / 'facts.json', [native_fact('shared', 'known to both devices')])
    assert owner.native._capture('Neko', True)
    assert local_state(owner)['pending']['document']['completedAt'] > 1000

    assert (await server.exchange(owner))['changed']
    assert server.revision == 1 and published_facts(server) == {'shared', 'here'}
    assert await owner.native.consume(UID)
    assert {row['id'] for row in read_views(owner.directory)['facts']} == {'shared', 'here'}
    # Nothing this device holds is missing from the server, so there is nothing to upload.
    assert owner.republished == [] and not owner.native._capture('Neko', False)
    await server.exchange(owner)
    assert server.revision == 1 and published_facts(server) == {'shared', 'here'}
    assert server.calls == ['PUT', 'HEAD', 'GET', 'HEAD']


@pytest.mark.asyncio
async def test_both_devices_keep_what_each_added_from_one_base(owner):
    server = PublishingServer()
    atomic_write_json(owner.directory / 'facts.json', [native_fact('shared', 'known to both devices')])
    assert owner.native._capture('Neko', True)
    await server.exchange(owner)
    assert server.revision == 1 and local_state(owner)['base']['revision'] == 1
    # Both start from revision 1: the other device adds one fact and publishes first.
    other = deepcopy(server.document)
    other['completedAt'] += 10
    other['entries'].append({**other['entries'][0], 'id': 'there', 'text': 'added by the other device'})
    server.publish(other)
    atomic_write_json(owner.directory / 'facts.json', [native_fact('shared', 'known to both devices'),
                                                      native_fact('here', 'added by this device')])
    assert owner.native._capture('Neko', False)
    assert (await server.exchange(owner))['changed'] and server.revision == 2
    assert await owner.native.consume(UID)
    assert {row['id'] for row in read_views(owner.directory)['facts']} == {'shared', 'here', 'there'}
    assert owner.republished == ['Neko'] and owner.native._capture('Neko', False)
    assert local_state(owner)['pending']['baseRevision'] == 2
    await server.exchange(owner)
    assert server.revision == 3 and published_facts(server) == {'shared', 'here', 'there'}
    assert server.document['evidence']['retirements'] == []
    assert local_state(owner)['pending'] is None and not owner.native._capture('Neko', False)


@pytest.mark.asyncio
async def test_unchanged_rows_keep_every_native_field_when_a_revision_is_applied(owner):
    """Fields the protocol does not carry survive a snapshot that leaves their row alone."""
    from config import MEMORY_SCHEMA_VERSION_CURRENT
    untouched = native_fact('untouched', 'likes green tea', schema_version=MEMORY_SCHEMA_VERSION_CURRENT, signal_processed=False,
                            embedding=[0.1, 0.2], embedding_text_sha256='a' * 64, embedding_model_id='model',
                            hash='keep-this-hash', tags=['local'], created_at='2026-11-01T00:00:00')
    edited = native_fact('edited', 'has a cat', schema_version=MEMORY_SCHEMA_VERSION_CURRENT, signal_processed=False,
                         embedding=[0.3], embedding_text_sha256='b' * 64, embedding_model_id='model',
                         created_at='2026-11-01T00:00:00')
    reflection = {'id': 'ref', 'entity': 'master', 'text': 'enjoys calm routines', 'status': 'confirmed',
                  'confirmed_at': '2026-11-02T00:00:00', 'created_at': '2026-11-01T00:00:00',
                  'schema_version': MEMORY_SCHEMA_VERSION_CURRENT, 'temporal_scope': 'pattern',
                  'source_fact_ids': ['untouched'], 'recheck_attempts': 2}
    atomic_write_json(owner.directory / 'facts.json', [untouched, edited])
    atomic_write_json(owner.directory / 'reflections.json', [reflection])
    server = PublishingServer()
    assert owner.native._capture('Neko', True)
    await server.exchange(owner)
    before = read_views(owner.directory)
    assert before['facts'] == [untouched, edited] and before['reflections'] == [reflection]

    other = deepcopy(server.document)
    other['completedAt'] += 10
    next(row for row in other['entries'] if row['id'] == 'edited')['metadata'].update(importance=9, signalJudged=True)
    server.publish(other)
    assert (await server.exchange(owner))['changed'] and await owner.native.consume(UID)
    after = read_views(owner.directory)
    assert next(row for row in after['facts'] if row['id'] == 'untouched') == untouched
    assert after['reflections'] == [reflection]
    rebuilt = next(row for row in after['facts'] if row['id'] == 'edited')
    # The rewritten row follows the published judgement and keeps what its unchanged wording still supports.
    assert rebuilt['importance'] == 9 and rebuilt['signal_processed'] is True
    assert rebuilt['schema_version'] == MEMORY_SCHEMA_VERSION_CURRENT
    assert (rebuilt['embedding'], rebuilt['embedding_text_sha256']) == ([0.3], 'b' * 64)
    assert owner.republished == [] and not owner.native._capture('Neko', False)


def test_a_judged_and_examined_fact_round_trips_without_another_model_pass():
    from config import MEMORY_SCHEMA_VERSION_CURRENT
    views = {'persona': {}, 'archive': [], 'reflections': [], 'facts': [
        native_fact('waiting', 'not judged yet', schema_version=MEMORY_SCHEMA_VERSION_CURRENT, signal_processed=False),
        native_fact('legacy', 'never examined for event time', signal_processed=True),
        native_fact('old', 'written before the judging marker')]}
    exported = {row['id']: row['metadata'] for row in export_document(views, 2000)['entries']}
    assert exported['waiting']['signalJudged'] is False and exported['waiting']['eventWhen'] is None
    assert exported['legacy']['signalJudged'] is True and 'eventWhen' not in exported['legacy']
    assert exported['old']['signalJudged'] is True
    received = import_document(export_document(views, 2000), {'persona': {}, 'facts': [], 'archive': [], 'reflections': []})
    facts = {row['id']: row for row in received['facts']}
    assert facts['waiting']['signal_processed'] is False and facts['waiting']['schema_version'] == MEMORY_SCHEMA_VERSION_CURRENT
    assert facts['legacy']['signal_processed'] is True and 'schema_version' not in facts['legacy']
    older = {'schemaVersion': 2, 'completedAt': 2000, 'evidence': {'retirements': [], 'confirmations': []}, 'entries': [
        {'kind': 'fact', 'subject': 'master', 'id': 'f', 'text': 'from an older writer', 'metadata': {}}]}
    assert import_document(older, {'persona': {}, 'facts': [], 'archive': [], 'reflections': []})['facts'][0]['signal_processed'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,revision', [('download:3', 3), ('0123456789abcdef0123456789abcdef', 0)])
async def test_state_written_before_base_revisions_is_read_without_trusting_an_unconfirmed_upload(owner, operation, revision):
    epoch = owner.runtime.writer.epoch()
    legacy = {'schemaVersion': 2, 'completedAt': 1500, 'evidence': {'retirements': [], 'confirmations': []}, 'entries': [
        {'kind': 'fact', 'subject': 'master', 'id': 'old', 'text': 'held before the upgrade', 'metadata': {'importance': 5}}]}
    atomic_write_json(owner.adapter.local_path(owner.binding), {
        'ownerId': OWNER, 'characterSyncId': UID, 'serviceUrl': owner.binding['service_url'], 'epoch': list(epoch),
        'operationId': operation, 'document': legacy, 'nativeEntries': legacy['entries']})
    state = local_state(owner)
    assert state['pending'] is None and owner.adapter.base_revision(owner.binding, epoch) == revision
    assert (state['base'] is None) is (revision == 0)


@pytest.mark.asyncio
async def test_a_device_without_a_base_joins_with_the_server_and_does_not_revive_retired_facts(owner):
    """After the upgrade an old upload proves nothing: both sides are kept, except what the server retired."""
    epoch = owner.runtime.writer.epoch()
    atomic_write_json(owner.directory / 'facts.json', [native_fact('mine', 'only on this device'),
                                                      native_fact('gone', 'deleted on the other device')])
    legacy = {'schemaVersion': 2, 'completedAt': 1500, 'evidence': {'retirements': [], 'confirmations': []}, 'entries': [
        {'kind': 'fact', 'subject': 'master', 'id': identity, 'text': text, 'metadata': {'importance': 5}}
        for identity, text in (('gone', 'deleted on the other device'), ('mine', 'only on this device'))]}
    atomic_write_json(owner.adapter.local_path(owner.binding), {
        'ownerId': OWNER, 'characterSyncId': UID, 'serviceUrl': owner.binding['service_url'], 'epoch': list(epoch),
        'operationId': 'f' * 32, 'document': legacy, 'nativeEntries': legacy['entries']})
    server = PublishingServer()
    remote = export_document({'persona': {}, 'archive': [], 'reflections': [],
                              'facts': [native_fact('theirs', 'only on the other device')],
                              'sync': {'evidence': {'confirmations': [], 'retirements': [
                                  {'id': 'gone', 'subject': 'master', 'sourceMessageIds': ['source-1']}]}}}, 2000)
    for _ in range(5):
        server.publish(remote)
    assert owner.native._capture('Neko', False)
    assert (await server.exchange(owner))['changed'] and server.revision == 5
    assert await owner.native.consume(UID)
    assert {row['id'] for row in read_views(owner.directory)['facts']} == {'mine', 'theirs'}
    assert owner.native._capture('Neko', False)
    await server.exchange(owner)
    assert server.revision == 6 and published_facts(server) == {'mine', 'theirs'}
    assert [row['id'] for row in server.document['evidence']['retirements']] == ['gone']


@pytest.mark.asyncio
async def test_an_expired_revision_is_followed_and_the_next_upload_says_it_was_never_read(owner):
    from services.chat_sync.ledger import ChatDomain
    atomic_write_json(owner.directory / 'facts.json', [native_fact('mine', 'only on this device')])
    assert owner.native._capture('Neko', True)
    stale = local_state(owner)['pending']
    puts = []
    async def request(method, path, body):
        if method == 'PUT':
            puts.append(json.loads(body))
            return {'ownerId': OWNER, 'characterSyncId': UID, 'operationId': puts[-1]['operationId'],
                    'revision': 7, 'accepted': False, 'conflict': True}
        return {'ownerId': OWNER, 'characterSyncId': UID, 'revision': 7, 'completedAt': 1000, 'expired': True}
    async def commit(fn, *args):
        return fn(*args)
    arguments = (ChatDomain(OWNER, UID), owner.binding, owner.runtime.writer.epoch(), request, commit)
    fingerprint = (await owner.runtime._session())[1]
    await owner.runtime.long_term_memory.sync(*arguments, can_apply=lambda: True, session_fingerprint=fingerprint)
    state = local_state(owner)
    assert state == {'base': {'revision': 7, 'document': None, 'nativeEntries': []}, 'pending': None,
                     'unseenRevision': True, 'sent': [], 'republish': True}
    assert owner.native._capture('Neko', False)
    upload = local_state(owner)
    assert upload['unseenRevision'] is False and upload['pending']['baseRevision'] == 7
    assert upload['pending']['document']['lineage'] == 1
    assert [row['id'] for row in upload['pending']['document']['entries']] == ['mine']
    assert upload['pending']['operationId'] != stale['operationId']
    # The mark is part of this device's memory now, so later snapshots keep it.
    assert not owner.native._capture('Neko', False)
    assert json.loads((owner.directory / FILES['sync']).read_text(encoding='utf-8'))['lineage'] == 1


@pytest.mark.asyncio
async def test_a_server_that_restarted_its_revisions_is_treated_as_having_no_shared_base(owner):
    server = PublishingServer()
    atomic_write_json(owner.directory / 'facts.json', [native_fact('mine', 'only on this device')])
    assert owner.native._capture('Neko', True)
    await server.exchange(owner)
    assert local_state(owner)['base']['revision'] == 1
    server.revision, server.document = 0, None
    await server.exchange(owner)
    assert local_state(owner) == {'base': None, 'pending': None, 'unseenRevision': False, 'sent': [], 'republish': True}
    assert owner.native._capture('Neko', False)
    await server.exchange(owner)
    assert server.revision == 1 and published_facts(server) == {'mine'}


@pytest.mark.asyncio
async def test_repeated_conflicts_back_off_instead_of_spinning(owner):
    from services.chat_sync.long_term import MAX_CONSECUTIVE_CONFLICTS
    atomic_write_json(owner.directory / 'facts.json', [native_fact('mine', 'only on this device')])
    assert owner.native._capture('Neko', True)
    pending = local_state(owner)['pending']
    server = PublishingServer()
    server.publish(export_document({'persona': {}, 'archive': [], 'reflections': [], 'facts': []}, 1000))
    for attempt in range(MAX_CONSECUTIVE_CONFLICTS):
        result = await server.exchange(owner)
        assert result['changed'] and local_state(owner)['pending'] == pending
    # The published revision is downloaded once and then left for the memory owner.
    assert server.calls.count('GET') == 1
    with pytest.raises(OSError, match='^long_term_memory_conflict_unresolved$'):
        await server.exchange(owner)
    assert local_state(owner)['pending'] == pending and local_state(owner)['base'] is None


@pytest.mark.asyncio
async def test_confirmed_base_is_not_read_under_another_installation_epoch(owner):
    """A copied or recovered installation starts without a base instead of trusting the old one."""
    atomic_write_json(owner.directory / 'facts.json', [native_fact('mine', 'only on this device')])
    server = PublishingServer()
    assert owner.native._capture('Neko', True)
    await server.exchange(owner)
    epoch = owner.runtime.writer.epoch()
    assert owner.adapter.base_revision(owner.binding, epoch) == 1
    assert owner.adapter.read_local(owner.binding, (epoch[0], 'f' * 32)) is None
    assert owner.adapter.base_revision(owner.binding, (epoch[0], 'f' * 32)) == 0


def test_confirmations_and_counted_facts_travel_with_a_reflection():
    """The receiver keeps the "backed by the user" gate instead of losing it with the counts."""
    empty = {'persona': {}, 'facts': [], 'archive': [], 'reflections': []}
    reflection = {'id': 'ref', 'entity': 'neko', 'text': 'says she likes rain', 'status': 'pending',
                  'source_fact_ids': ['said', 'batchmate'], 'counted_fact_ids': ['said'],
                  'fact_source_counts': {'user': 0, 'ai': 1}, 'user_confirm_count': 2}
    views = {**empty, 'reflections': [reflection], 'facts': [
        {'id': 'said', 'entity': 'neko', 'text': 'she said she likes rain', 'source': 'ai_disclosure'},
        {'id': 'batchmate', 'entity': 'master', 'text': 'user was there', 'source': 'user_observation'}]}
    exported = export_document(views, 2000)
    metadata = next(row for row in exported['entries'] if row['id'] == 'ref')['metadata']
    assert (metadata['userConfirmCount'], metadata['countedFactIds'], metadata['factSourceCounts']) == (2, ['said'], {'user': 0, 'ai': 1})

    received = import_document(exported, empty)['reflections'][0]
    assert received['user_confirm_count'] == 2 and received['counted_fact_ids'] == ['said']
    # Only the counted fact is counted, not the whole absorbed batch.
    assert received['fact_source_counts'] == {'user': 0, 'ai': 1}

    # The counted fact is not in the snapshot: the sender's counts stand in for it.
    without = {**exported, 'entries': [row for row in exported['entries'] if row['id'] != 'said']}
    assert import_document(without, empty)['reflections'][0]['fact_source_counts'] == {'user': 0, 'ai': 1}

    # The fact became the user's own words since: counted as it stands now.
    upgraded = deepcopy(exported)
    next(row for row in upgraded['entries'] if row['id'] == 'said')['metadata']['source'] = 'user_observation'
    assert import_document(upgraded, empty)['reflections'][0]['fact_source_counts'] == {'user': 1, 'ai': 0}

    # A withdrawn fact no longer backs anything.
    withdrawn = deepcopy(exported)
    next(row for row in withdrawn['entries'] if row['id'] == 'said')['metadata'].update(status='retracted', voidedAt=1500)
    assert import_document(withdrawn, empty)['reflections'][0]['fact_source_counts'] == {'user': 0, 'ai': 0}


def test_an_older_snapshot_keeps_the_local_confirmation_count():
    empty = {'persona': {}, 'facts': [], 'archive': [], 'reflections': []}
    older = {'schemaVersion': 2, 'completedAt': 2000, 'evidence': {'retirements': [], 'confirmations': []}, 'entries': [
        {'kind': 'reflection', 'subject': 'master', 'id': 'ref', 'text': 'reworded there', 'metadata': {'status': 'confirmed'}}]}
    existing = {**empty, 'reflections': [{'id': 'ref', 'entity': 'master', 'text': 'worded here', 'status': 'confirmed',
                                          'user_confirm_count': 3}]}
    assert import_document(older, existing)['reflections'][0]['user_confirm_count'] == 3



def _confirmed_reflection(confirmations):
    return {'id': 'ref', 'entity': 'master', 'text': 'enjoys calm routines', 'status': 'confirmed',
            'confirmed_at': '2026-11-02T00:00:00', 'created_at': '2026-11-01T00:00:00', 'source_fact_ids': [],
            'user_confirm_count': confirmations}


def _published_confirmations(server):
    return next(row for row in server.document['entries'] if row['id'] == 'ref')['metadata']['userConfirmCount']


@pytest.mark.asyncio
async def test_a_capture_landing_during_an_upload_does_not_count_the_uploaded_change_twice(owner):
    """Snapshot A is on its way when a capture replaces it with B; the server then accepts A."""
    server = PublishingServer()
    atomic_write_json(owner.directory / 'reflections.json', [_confirmed_reflection(0)])
    assert owner.native._capture('Neko', True)
    await server.exchange(owner)
    atomic_write_json(owner.directory / 'reflections.json', [_confirmed_reflection(1)])
    assert owner.native._capture('Neko', False)
    first = local_state(owner)['pending']
    plain = server.request

    async def during_upload(method, path, body):
        if method == 'PUT' and json.loads(body)['operationId'] == first['operationId']:
            atomic_write_json(owner.directory / 'reflections.json', [_confirmed_reflection(2)])
            assert owner.native._capture('Neko', False)
        return await plain(method, path, body)
    server.request = during_upload
    await server.exchange(owner)
    state = local_state(owner)
    # What the server took is the base; the newer snapshot rests on it and waits its turn.
    assert state['base']['revision'] == 2 and state['base']['document'] == first['document']
    assert state['pending']['baseRevision'] == 2 and state['pending']['operationId'] != first['operationId']
    assert not owner.adapter.incoming_path(UID).exists()
    await server.exchange(owner)
    assert server.revision == 3 and _published_confirmations(server) == 2
    assert local_state(owner)['pending'] is None and not owner.native._capture('Neko', False)


@pytest.mark.asyncio
async def test_an_upload_whose_reply_was_lost_comes_back_as_this_devices_own_revision(owner):
    """The server took snapshot A, the reply never arrived, and a capture replaced A before the retry."""
    server = PublishingServer()
    atomic_write_json(owner.directory / 'reflections.json', [_confirmed_reflection(0)])
    assert owner.native._capture('Neko', True)
    await server.exchange(owner)
    atomic_write_json(owner.directory / 'reflections.json', [_confirmed_reflection(1)])
    assert owner.native._capture('Neko', False)
    plain = server.request
    lost = [True]

    async def lose_reply(method, path, body):
        answer = await plain(method, path, body)
        if method == 'PUT' and lost[0]:
            lost[0] = False
            raise OSError('reply lost')
        return answer
    server.request = lose_reply
    with pytest.raises(OSError):
        await server.exchange(owner)
    assert server.revision == 2 and local_state(owner)['base']['revision'] == 1
    atomic_write_json(owner.directory / 'reflections.json', [_confirmed_reflection(2)])
    assert owner.native._capture('Neko', False)
    # The retry is refused, and the revision fetched instead is the lost upload itself.
    assert (await server.exchange(owner))['changed'] and server.revision == 2
    before = (owner.directory / 'reflections.json').read_bytes()
    assert await owner.native.consume(UID)
    assert (owner.directory / 'reflections.json').read_bytes() == before
    assert local_state(owner)['base']['revision'] == 2 and owner.republished == ['Neko']
    assert owner.native._capture('Neko', False)
    await server.exchange(owner)
    assert server.revision == 3 and _published_confirmations(server) == 2


def test_an_untouched_reflection_still_follows_its_facts_when_they_change():
    """The snapshot only upgrades a fact to the user's own words; the line resting on it is no longer held back."""
    from memory.evidence import awaits_user_signal
    fact = {'id': 'f1', 'text': 'likes rain', 'entity': 'neko', 'source': 'ai_disclosure', 'hash': 'x', 'created_at': '2026-10-01T00:00:00'}
    reflection = {'id': 'r1', 'text': 'line', 'entity': 'neko', 'status': 'pending', 'source_fact_ids': ['f1'],
                  'fact_source_counts': {'user': 0, 'ai': 1}, 'counted_fact_ids': ['f1'], 'recheck_attempts': 1}
    existing = {'persona': {}, 'facts': [fact], 'archive': [], 'reflections': [reflection]}
    incoming = export_document(existing, 3000)
    next(row for row in incoming['entries'] if row['id'] == 'f1')['metadata']['source'] = 'user_observation'
    kept = merged_import(incoming, existing)['reflections'][0]
    assert kept['fact_source_counts'] == {'user': 1, 'ai': 0} and not awaits_user_signal(kept)
    # Everything else about the untouched row is still exactly as it was.
    assert {**kept, 'fact_source_counts': reflection['fact_source_counts']} == reflection
    unchanged = merged_import(export_document(existing, 3000), existing)['reflections'][0]
    assert unchanged == reflection



@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['long_term_memory_upload_refused', 'long_term_memory_conflict_unresolved',
                                  'long_term_memory_upload_unsupported'])
async def test_an_upload_that_cannot_land_backs_off_instead_of_blocking_the_binding(owner, code):
    from services.chat_sync.long_term import LongTermMemoryRetryLater
    generation, session = owner.binding['generation'], 'session'
    assert owner.runtime.scheduler.claim(DOMAIN.key(), 'long_term', generation, session)
    owner.runtime.scheduler.failure(DOMAIN.key(), 'long_term', generation, session, LongTermMemoryRetryLater(code))
    with closing(store_connection(owner.runtime.scheduler.path)) as connection:
        blocked, retry, error = connection.execute(
            "SELECT blocked_generation,retry_at,last_error FROM scheduler_tasks WHERE domain=? AND channel='long_term'",
            (DOMAIN.key(),)).fetchone()
    assert blocked is None and retry > 0


@pytest.mark.asyncio
async def test_a_server_without_conditional_writes_still_serves_pulls_and_keeps_the_upload_waiting(owner):
    from services.chat_sync.long_term import LongTermMemoryRetryLater
    from services.chat_sync.runtime import ChatSyncServiceError
    server = PublishingServer()
    server.publish(export_document({'persona': {}, 'archive': [], 'reflections': [], 'facts': [
        native_fact('theirs', 'only on the other device')]}, 1000))
    atomic_write_json(owner.directory / 'facts.json', [native_fact('mine', 'only on this device')])
    assert owner.native._capture('Neko', True)
    waiting = local_state(owner)['pending']
    plain = server.request

    async def older_server(method, path, body):
        if method == 'PUT':
            server.calls.append('PUT')
            raise ChatSyncServiceError(400)
        return await plain(method, path, body)
    server.request = older_server
    with pytest.raises(LongTermMemoryRetryLater, match='^long_term_memory_upload_unsupported$'):
        await server.exchange(owner)
    # The refusal is not recorded as an upload, and what the server holds was still fetched.
    assert local_state(owner)['pending'] == waiting and local_state(owner)['base'] is None
    assert server.calls == ['PUT', 'HEAD', 'GET'] and owner.adapter.incoming_path(UID).exists()
    with pytest.raises(ChatSyncServiceError):
        async def broken(method, path, body):
            raise ChatSyncServiceError(403)
        server.request = broken
        await server.exchange(owner)


@pytest.mark.asyncio
async def test_holding_the_newest_snapshot_is_recorded_only_when_nothing_newer_is_published(owner):
    server = PublishingServer()
    atomic_write_json(owner.directory / 'facts.json', [native_fact('mine', 'only on this device')])
    assert owner.adapter.checked_at(UID) is None
    assert owner.native._capture('Neko', True)
    await server.exchange(owner)
    first = owner.adapter.checked_at(UID)
    assert type(first) is int and first > 0
    owner.adapter.mark_checked(UID, 5)
    other = deepcopy(server.document)
    other['completedAt'] += 10
    other['progress'] = {'a': ['c' * 32], 'b': []}
    server.publish(other)
    # A newer revision is downloaded but not applied yet: this device may be missing progress.
    assert (await server.exchange(owner))['changed'] and owner.adapter.checked_at(UID) == 5
    assert await owner.native.consume(UID)
    await server.exchange(owner)
    assert owner.adapter.checked_at(UID) > 5


@pytest.mark.asyncio
async def test_retiring_a_role_removes_every_copy_this_adapter_keeps(owner):
    directory = owner.adapter.directory
    directory.mkdir(parents=True, exist_ok=True)
    other = 'b' * 32
    kept = [directory / f'{other}-0123456789abcdef.json', directory / f'{other}.expired-{"0" * 32}.json',
            directory / f'{other}.organizer.json', directory / f'{other}.checked.json']
    dropped = [directory / f'{UID}-0123456789abcdef.json', directory / f'{UID}.expired-{"0" * 32}.json',
               directory / f'{UID}.organizer.json', directory / f'{UID}.checked.json', owner.adapter.incoming_path(UID)]
    for path in kept + dropped:
        path.write_text('{}', encoding='utf-8')
    from memory import received_messages
    received_messages.keep_arrivals(owner.adapter.received_directory(UID), [
        {'id': 'c' * 32, 'role': 'user', 'text': 'said elsewhere', 'originalTime': None}], 1000)
    owner.adapter.forget(UID)
    assert [path.exists() for path in kept] == [True] * 4 and [path.exists() for path in dropped] == [False] * 5
    assert not owner.adapter.received_directory(UID).exists()



@pytest.mark.asyncio
async def test_a_merge_that_still_owes_an_upload_survives_a_process_exit(owner, monkeypatch):
    """The process exits after the merged views are saved and before their capture is queued."""
    server = PublishingServer()
    atomic_write_json(owner.directory / 'facts.json', [native_fact('shared', 'known to both devices')])
    assert owner.native._capture('Neko', True)
    await server.exchange(owner)
    other = deepcopy(server.document)
    other['completedAt'] += 10
    other['entries'].append({**other['entries'][0], 'id': 'there', 'text': 'added by the other device'})
    server.publish(other)
    atomic_write_json(owner.directory / 'facts.json', [native_fact('shared', 'known to both devices'),
                                                      native_fact('here', 'added by this device')])
    assert (await server.exchange(owner))['changed']
    payload = json.loads(owner.adapter.incoming_path(UID).read_text(encoding='utf-8'))
    # Applied directly: nothing after the saved merge runs, as when the process dies there.
    assert owner.native._write_snapshot('Neko', payload)
    owner.native._unpublished_after_apply.clear()
    assert owner.republished == [] and local_state(owner)['republish'] is True
    queued = []
    monkeypatch.setattr(owner.native, 'queue_capture', queued.append)
    assert await owner.native.resume_owed_capture('Neko') is True and queued == ['Neko']
    assert owner.native._capture('Neko', False)
    state = local_state(owner)
    assert state['republish'] is False and state['pending']['baseRevision'] == 2
    assert {row['id'] for row in state['pending']['document']['entries']} == {'shared', 'here', 'there'}
    assert await owner.native.resume_owed_capture('Neko') is False


@pytest.mark.asyncio
async def test_a_merge_that_changed_nothing_here_owes_no_upload(owner):
    server = PublishingServer()
    server.publish(export_document({'persona': {}, 'archive': [], 'reflections': [], 'facts': [
        native_fact('theirs', 'only on the other device')]}, 1000))
    await server.exchange(owner)
    assert await owner.native.consume(UID)
    assert local_state(owner)['republish'] is False and await owner.native.resume_owed_capture('Neko') is False
