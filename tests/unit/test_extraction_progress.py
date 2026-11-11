"""Extraction progress: a message one pass already read is not read again on any device."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from memory import extraction_progress
from tests.unit.test_evidence_extraction import _install_factstore
from utils.llm_client import convert_to_messages
from utils.long_term_memory_protocol import PROGRESS_MAX

NAME = '小天'
A, B, C = 'a' * 32, 'b' * 32, 'c' * 32


def message(text, identity=None, *, role='human', metadata=None):
    data = {'content': text}
    if identity is not None:
        data['additional_kwargs'] = {'memory_source_id': f'sync:{identity}', 'memory_source_created_at': 1000}
    if metadata is not None:
        data['metadata'] = metadata
    return convert_to_messages([{'type': role, 'data': data}])[0]


def progress(directory):
    return extraction_progress.read(Path(directory) / NAME)


def test_each_identity_source_gives_the_name_other_devices_know_the_message_by():
    assert extraction_progress.message_identity(message('typed', A)) == A
    assert extraction_progress.message_identity(message('typed', metadata={'chat_original_source': {'recordId': B}})) == B
    assert extraction_progress.message_identity(message('received', metadata={'recent_memory_id': C})) == C
    assert extraction_progress.message_identity(message('spoken', metadata={'memory_message_id': A})) == A
    # A row stored before messages had a shared identity never left this device under one.
    assert extraction_progress.message_identity(message('legacy')) is None
    legacy = convert_to_messages([{'type': 'human', 'data': {
        'content': 'legacy', 'additional_kwargs': {'memory_source_id': 'desktop:' + 'f' * 64}}}])[0]
    assert extraction_progress.message_identity(legacy) is None


def test_record_keeps_order_skips_known_and_drops_the_oldest_first(tmp_path):
    assert extraction_progress.read(tmp_path) == {'a': [], 'b': []}
    (tmp_path / extraction_progress.STATE_FILE).write_text('[]', encoding='utf-8')
    assert extraction_progress.read(tmp_path) == {'a': [], 'b': []}
    assert extraction_progress.record_processed(tmp_path, 'a', [A, B, A])
    assert not extraction_progress.record_processed(tmp_path, 'a', [B])
    assert extraction_progress.record_processed(tmp_path, 'b', [C])
    assert extraction_progress.read(tmp_path) == {'a': [A, B], 'b': [C]}
    many = [f'{index:032x}' for index in range(PROGRESS_MAX)]
    assert extraction_progress.record_processed(tmp_path, 'a', many)
    assert extraction_progress.read(tmp_path)['a'] == many
    with pytest.raises(ValueError):
        extraction_progress.record_processed(tmp_path, 'c', [A])


def test_record_leaves_the_rest_of_the_sync_state_alone(tmp_path):
    state = {'evidence': {'retirements': [{'id': 'old', 'subject': 'master', 'sourceMessageIds': ['s']}], 'confirmations': []},
             'dormantEntries': [], 'organizer': {'deviceId': 'd' * 32, 'renewedAt': 1, 'rank': 2}}
    (tmp_path / extraction_progress.STATE_FILE).write_text(json.dumps(state), encoding='utf-8')
    extraction_progress.record_processed(tmp_path, 'a', [A])
    assert json.loads((tmp_path / extraction_progress.STATE_FILE).read_text(encoding='utf-8')) == {
        **state, 'progress': {'a': [A], 'b': []}}


def test_unprocessed_always_reads_a_message_without_a_shared_identity():
    read, unread, local = message('read', A), message('unread', B), message('local only')
    assert extraction_progress.unprocessed([read, unread, local], {A}) == [unread, local]


@pytest.mark.asyncio
@pytest.mark.parametrize('found', [False, True])
async def test_user_pass_records_the_window_whether_or_not_it_yields_facts(tmp_path, found):
    fs, _ = _install_factstore(str(tmp_path))
    extracted = [{'text': '主人喜欢绿茶', 'importance': 6, 'entity': 'master', 'source_indices': [1]}] if found else []
    with patch.object(fs, '_allm_extract_facts', new=AsyncMock(return_value=extracted)):
        persisted, _, _ = await fs.aextract_facts_and_detect_signals(NAME, [message('我喜欢绿茶', A), message('今天有点累', B)])
    assert len(persisted) == (1 if found else 0)
    assert progress(tmp_path) == {'a': [A, B], 'b': []}
    assert await fs.aextraction_progress(NAME) == {'a': [A, B], 'b': []}


@pytest.mark.asyncio
async def test_progress_is_written_inside_the_lock_that_guards_the_facts(tmp_path):
    """A snapshot is exported under this lock, so it never carries one without the other."""
    fs, _ = _install_factstore(str(tmp_path))
    seen = []
    original = extraction_progress.record_processed

    def recording(directory, passage, processed):
        facts = json.loads((Path(directory) / 'facts.json').read_text(encoding='utf-8'))
        seen.append((fs._get_persist_alock(NAME).locked(), [row['text'] for row in facts]))
        return original(directory, passage, processed)

    extracted = [{'text': '主人喜欢绿茶', 'importance': 6, 'entity': 'master', 'source_indices': [1]}]
    with patch.object(extraction_progress, 'record_processed', new=recording), \
            patch.object(fs, '_allm_extract_facts', new=AsyncMock(return_value=extracted)):
        await fs.aextract_facts_and_detect_signals(NAME, [message('我喜欢绿茶', A)])
    # The facts are already on disk when the progress over their messages is written.
    assert seen == [(True, ['主人喜欢绿茶'])]


@pytest.mark.asyncio
async def test_a_failed_extraction_records_nothing(tmp_path):
    from memory.facts import FactExtractionFailed
    fs, _ = _install_factstore(str(tmp_path))
    with patch.object(fs, '_allm_extract_facts', new=AsyncMock(return_value=None)):
        with pytest.raises(FactExtractionFailed):
            await fs.aextract_facts_and_detect_signals(NAME, [message('我喜欢绿茶', A)])
    assert progress(tmp_path) == {'a': [], 'b': []}


@pytest.mark.asyncio
@pytest.mark.parametrize('found', [False, True])
async def test_per_turn_extraction_with_the_evidence_pipeline_off_records_the_user_pass(tmp_path, found):
    fs, _ = _install_factstore(str(tmp_path))
    extracted = [{'text': '主人养了一只猫', 'importance': 6, 'entity': 'master', 'source_indices': [1]}] if found else []
    with patch.object(fs, '_allm_extract_facts', new=AsyncMock(return_value=extracted)):
        created = await fs.extract_facts([message('我养了一只猫', A)], NAME)
    assert len(created) == (1 if found else 0)
    assert progress(tmp_path) == {'a': [A], 'b': []}


@pytest.mark.asyncio
@pytest.mark.parametrize('found', [False, True])
async def test_both_sides_pass_records_under_its_own_name(tmp_path, found):
    fs, _ = _install_factstore(str(tmp_path))
    extracted = [{'text': '小天说自己喜欢下雨天', 'importance': 5, 'entity': 'neko', 'source': 'ai_disclosure',
                  'source_indices': [2]}] if found else []
    window = [message('你喜欢什么天气', A), message('我喜欢下雨天', B, role='ai')]
    with patch.object(fs, '_allm_extract_facts_with_known_pool', new=AsyncMock(return_value=extracted)):
        await fs.aextract_facts_with_known_pool(NAME, window, [])
    assert progress(tmp_path) == {'a': [], 'b': [A, B]}


@pytest.mark.asyncio
async def test_extraction_loop_drops_messages_any_device_already_read(monkeypatch):
    from app.memory_server import signal_extraction
    read, unread, local = message('read', A), message('unread', B), message('local only')
    store = SimpleNamespace(aextraction_progress=AsyncMock(return_value={'a': [A], 'b': [A, B]}))
    monkeypatch.setattr(signal_extraction.runtime, 'fact_store', store)
    assert await signal_extraction._unread_messages(NAME, 'a', [read, unread, local]) == [unread, local]
    assert await signal_extraction._unread_messages(NAME, 'b', [read, unread, local]) == [local]
    assert await signal_extraction._unread_messages(NAME, 'b', [read, unread]) == []
    # A record that cannot be read skips nothing: extraction is never lost to a bad file.
    store.aextraction_progress = AsyncMock(side_effect=OSError('unreadable'))
    assert await signal_extraction._unread_messages(NAME, 'a', [read, unread]) == [read, unread]


@pytest.mark.asyncio
async def test_progress_travels_in_the_snapshot_and_merges_with_what_another_device_read(tmp_path):
    from memory.long_term_merge import merge_documents
    from memory.long_term_sync import export_document, import_document, read_views
    directory = tmp_path / NAME
    directory.mkdir()
    extraction_progress.record_processed(directory, 'a', [A])
    local = export_document(read_views(directory), 2000)
    assert local['progress'] == {'a': [A], 'b': []}
    remote = {**local, 'completedAt': 3000, 'progress': {'a': [B], 'b': [C]}}
    merged = merge_documents(None, local, remote, 3000)
    assert merged['progress'] == {'a': [B, A], 'b': [C]}
    applied = import_document(merged, read_views(directory), merged=True)
    assert applied['sync']['progress'] == {'a': [B, A], 'b': [C]}
    # An older document knows nothing of progress, so it cannot clear it.
    older = {'schemaVersion': 2, 'completedAt': 4000, 'entries': [], 'evidence': {'retirements': [], 'confirmations': []}}
    assert import_document(older, applied)['sync']['progress'] == {'a': [B, A], 'b': [C]}


@pytest.mark.asyncio
async def test_facts_citing_the_same_message_are_arbitrated_never_merged_on_that_ground_alone(tmp_path):
    """Two passes over one message (another device, or progress that aged out) may word a fact twice."""
    from memory.facts import SHARED_SOURCE_OVERLAP
    fs, _ = _install_factstore(str(tmp_path))
    fs._dedup_resolver = object()
    queued = []

    async def enqueue(name, pairs, durable=False):
        queued.extend((candidate['text'], existing['text'], overlap) for candidate, existing, overlap in pairs)
        return True

    def extracted(*texts):
        return [{'text': text, 'importance': 6, 'entity': 'master', 'source_indices': [1]} for text in texts]

    with patch.object(fs, '_aenqueue_near_dup_pairs', new=enqueue):
        # One message, two facts in one pass: siblings, not repeats.
        with patch.object(fs, '_allm_extract_facts', new=AsyncMock(return_value=extracted('主人养了一只猫', '主人住在京都'))):
            await fs.aextract_facts_and_detect_signals(NAME, [message('我养了猫，住在京都', A)], judge=False)
        assert queued == []
        # A later pass over the same message words one of them differently.
        with patch.object(fs, '_allm_extract_facts', new=AsyncMock(return_value=extracted('主人家里有只猫咪'))):
            await fs.aextract_facts_and_detect_signals(NAME, [message('我养了猫，住在京都', A)], judge=False)
        assert queued == [('主人家里有只猫咪', '主人养了一只猫', SHARED_SOURCE_OVERLAP)]
        # A fact from another message is nobody's repeat. (This stand-in queue never
        # acknowledges, so the one pending pair is offered again; no new pair appears.)
        with patch.object(fs, '_allm_extract_facts', new=AsyncMock(return_value=extracted('主人喜欢爬山'))):
            await fs.aextract_facts_and_detect_signals(NAME, [message('周末我常去爬山', B)], judge=False)
        assert set(queued) == {('主人家里有只猫咪', '主人养了一只猫', SHARED_SOURCE_OVERLAP)}
    # All of them are still stored: only the arbitration verdict may fold two together.
    assert {row['text'] for row in await fs.aload_facts(NAME)} == {'主人养了一只猫', '主人住在京都', '主人家里有只猫咪', '主人喜欢爬山'}
