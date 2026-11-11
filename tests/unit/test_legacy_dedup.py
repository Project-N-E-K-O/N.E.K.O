"""One-time cleanup of old duplicate facts: nominate leniently, let the model decide, archive, never delete."""

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory import legacy_dedup

NAME = '小天'
SOURCE = 'sync:' + 'a' * 32


def _fact(fid, text, *, created, absorbed=False, sources=None, entity='master'):
    row = {'id': fid, 'text': text, 'entity': entity, 'importance': 5, 'tags': [], 'hash': fid + 'h',
           'created_at': created, 'absorbed': absorbed, 'source': 'user_observation', 'signal_processed': True}
    if sources:
        row['source_message_ids'] = list(sources)
    return row


def _vectors(table):
    return lambda row: table.get(row['id'])


def test_nomination_reaches_below_everyday_dedup_and_covers_absorbed_facts():
    active = [
        _fact('a', '博士是一名主播', created='2026-06-12T21:00:00', absorbed=True),
        _fact('c', '博士喜欢吃辣', created='2026-06-12T23:00:00'),
        _fact('d', '博士是一名主播。', created='2026-06-13T00:00:00'),
        _fact('e', '猫娘喜欢晒太阳', created='2026-06-13T01:00:00', entity='neko', sources=[SOURCE]),
        _fact('f', '猫娘每天午睡', created='2026-06-13T02:00:00', entity='neko', sources=[SOURCE]),
    ]
    archive = [
        # Moved here by age after a reflection absorbed it; everyday dedup never sees it.
        _fact('b', '博士在直播间读弹幕', created='2026-06-12T22:00:00', absorbed=True),
        {**_fact('g', '博士是一名主播', created='2026-06-01T00:00:00'), 'arbitration_archived_at': '2026-06-02T00:00:00'},
    ]
    # 0.82: below the everyday 0.85, above the one-time 0.80.
    vectors = {'a': [1.0, 0.0], 'b': [0.82, 0.5724], 'c': [0.0, 1.0]}
    rows = legacy_dedup.candidates(active, archive)
    pairs = {(p['candidate'], p['existing']): p['evidence'] for p in legacy_dedup.nominate(rows, decode=_vectors(vectors))}
    assert pairs[('b', 'a')].startswith('cosine=0.82')
    assert pairs[('d', 'a')] == 'text_overlap=1.000'           # same wording, punctuation aside
    assert pairs[('f', 'e')] == 'extracted_from_same_messages'  # same chat line, read twice
    assert not any('c' in key for key in pairs)                 # unrelated
    assert not any('g' in key for key in pairs)                 # already folded away once
    assert set(pairs) == {('b', 'a'), ('d', 'a'), ('f', 'e')}   # newer as candidate, as the prompt says


def test_an_earlier_decision_wins_a_conflict():
    pairs = [{'candidate': 'b', 'existing': 'a'}, {'candidate': 'a', 'existing': 'b'}, {'candidate': 'c', 'existing': 'b'}]
    decisions = {'b|a': 'merge', 'a|b': 'replace', 'c|b': 'replace'}
    assert legacy_dedup.losers(pairs, decisions) == {'b': 'a'}


def test_survivors_follow_a_chain_of_merges():
    assert legacy_dedup.survivors({'x': 'y', 'y': 'z'}) == {'x': 'z', 'y': 'z'}


def _cm(tmpdir):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    cm.aget_character_data = AsyncMock(return_value=('主人', NAME, {}, {}, {'human': '主人', 'system': 'SYS'}, {}, {}, {}, {}))
    cm.get_character_data = MagicMock(return_value=('主人', NAME, {}, {}, {'human': '主人', 'system': 'SYS'}, {}, {}, {}, {}))
    cm.get_model_api_config = MagicMock(return_value={'model': 'fake', 'base_url': 'http://fake', 'api_key': 'sk-fake'})
    cm.aget_model_api_config = AsyncMock(side_effect=lambda mt, **_: cm.get_model_api_config(mt))
    return cm


class _Model:
    """Calls a pair the same thing only when both texts are in one ``same`` group."""

    def __init__(self, same):
        self.same, self.calls = same, 0

    async def __call__(self, prompt, *_args, **_kwargs):
        self.calls += 1
        decisions = []
        for index, candidate, existing in re.findall(r'^\[(\d+)\] candidate: (.*?) \| existing: (.*?) \|', prompt, re.M):
            same = any(candidate in group and existing in group for group in self.same)
            decisions.append({'index': int(index), 'action': 'merge' if same else 'keep_both'})
        return decisions


@pytest.fixture
def memory(tmp_path, monkeypatch):
    from memory.event_log import EventLog
    from memory.facts import FactStore
    from memory.persona import PersonaManager
    from memory.reflection import ReflectionEngine
    from app.memory_server import runtime
    from memory import organizer

    cm = _cm(str(tmp_path))
    with patch('memory.event_log.get_config_manager', return_value=cm), \
         patch('memory.facts.get_config_manager', return_value=cm), \
         patch('memory.persona.manager.get_config_manager', return_value=cm), \
         patch('memory.reflection.manager.get_config_manager', return_value=cm):
        events = EventLog()
        events._config_manager = cm
        facts = FactStore()
        facts._config_manager = cm
        persona = PersonaManager(event_log=events)
        persona._config_manager = cm
        reflections = ReflectionEngine(facts, persona, event_log=events)
        reflections._config_manager = cm
    for attribute, value in [('_config_manager', cm), ('fact_store', facts),
                             ('reflection_engine', reflections), ('persona_manager', persona)]:
        monkeypatch.setattr(runtime, attribute, value, raising=False)
    monkeypatch.setattr(organizer, '_gate', None)
    from app.memory_server import legacy_dedup as runner
    monkeypatch.setattr(runner, '_decode', _vectors({'old': [1.0, 0.0], 'dup': [0.9, 0.4359],
                                                     'yes': [0.0, 1.0], 'no': [0.05, 0.9987]}))
    monkeypatch.setattr('app.memory_server.long_term_sync.queue_capture_after_extraction', lambda name: None)
    return tmp_path / NAME, facts


async def _seed(directory, facts):
    from datetime import datetime, timedelta
    # Recent: absorbed facts older than a week move to the archive on save.
    day = lambda hours: (datetime.now() - timedelta(days=1) + timedelta(hours=hours)).isoformat()
    rows = [
        _fact('old', '博士是一名主播', created=day(0)),
        _fact('dup', '博士是一名主播，会日常直播', created=day(1), absorbed=True),
        _fact('yes', '博士会说韩语', created=day(0.5), absorbed=True),
        _fact('no', '博士不会说韩语', created=day(1.5), absorbed=True),
    ]
    facts._facts[NAME] = rows
    await facts.asave_facts(NAME)
    (directory / 'reflections.json').write_text(json.dumps([
        {'id': 'r1', 'text': '博士以直播为业', 'entity': 'master', 'status': 'confirmed',
         'source_fact_ids': ['dup', 'yes'], 'counted_fact_ids': ['dup'], 'created_at': '2026-06-13T00:00:00'},
    ], ensure_ascii=False), encoding='utf-8')
    (directory / 'persona.json').write_text(json.dumps({'master': {'facts': [
        {'id': 'p1', 'text': '博士是主播', 'source': 'reflection', 'source_id': 'r1', 'counted_fact_ids': ['dup']},
    ]}}, ensure_ascii=False), encoding='utf-8')
    (directory / 'long_term_portable.json').write_text(json.dumps({'evidence': {'retirements': [], 'confirmations': []}}),
                                                       encoding='utf-8')


def _run(model, still_idle=lambda: True):
    from app.memory_server import legacy_dedup as runner
    from app.memory_server import runtime
    runtime.fact_store._allm_call_with_retries = model
    return runner.run(NAME, still_idle=still_idle)


@pytest.mark.asyncio
async def test_cleanup_archives_only_what_the_model_merges_and_repoints_its_evidence(memory):
    directory, facts = memory
    await _seed(directory, facts)
    before = (directory / 'facts.json').read_text(encoding='utf-8')
    model = _Model([{'博士是一名主播', '博士是一名主播，会日常直播'}])

    marker = await _run(model)

    assert marker['stage'] == 'done' and marker['archived'] == {'dup': 'old'}
    # Backed up first, whole.
    backup = directory / marker['backup']
    assert backup.parent.name == legacy_dedup.BACKUP_DIR and (backup / 'facts.json').read_text(encoding='utf-8') == before
    live = {row['id']: row for row in json.loads((directory / 'facts.json').read_text(encoding='utf-8'))}
    assert set(live) == {'old', 'yes', 'no'}            # 会 / 不会 stay apart: the model said keep_both
    assert live['old']['absorbed'] is True              # stands for the absorbed fact it replaced
    [archived] = json.loads((directory / 'facts_archive.json').read_text(encoding='utf-8'))
    assert archived['id'] == 'dup' and archived['superseded_by'] == 'old' and archived['arbitration_archived_at']
    [reflection] = json.loads((directory / 'reflections.json').read_text(encoding='utf-8'))
    assert reflection['source_fact_ids'] == ['old', 'yes'] and reflection['counted_fact_ids'] == ['old']
    persona = json.loads((directory / 'persona.json').read_text(encoding='utf-8'))
    assert persona['master']['facts'][0]['counted_fact_ids'] == ['old']
    # Recorded on this device and in the snapshot for every other one.
    assert legacy_dedup.local_version(directory) == legacy_dedup.published_version(directory) == legacy_dedup.VERSION

    # The archived fact travels as an arbitration archive, so no peer revives it or reads it as retired.
    from memory.long_term_sync import export_document, read_views
    sent = next(row for row in export_document(read_views(directory), 5000)['entries'] if row['id'] == 'dup')
    assert sent['metadata']['archived'] is True and sent['metadata']['status'] == 'superseded'
    assert sent['metadata']['archivedAt'] and sent['metadata']['supersededById'] == 'old'

    calls = model.calls
    assert await _run(model) is None and model.calls == calls  # runs once


@pytest.mark.asyncio
async def test_a_shared_source_alone_never_merges(memory):
    directory, facts = memory
    facts._facts[NAME] = [_fact('e', '猫娘喜欢晒太阳', created='2026-06-13T01:00:00', sources=[SOURCE]),
                          _fact('f', '猫娘每天午睡', created='2026-06-13T02:00:00', sources=[SOURCE])]
    await facts.asave_facts(NAME)
    model = _Model([])
    marker = await _run(model)
    assert model.calls == 1 and marker['archived'] == {}
    assert {row['id'] for row in json.loads((directory / 'facts.json').read_text(encoding='utf-8'))} == {'e', 'f'}


@pytest.mark.asyncio
async def test_only_the_organizer_runs_it_and_a_published_run_is_not_repeated(memory, monkeypatch):
    from memory import organizer
    directory, facts = memory
    await _seed(directory, facts)
    model = _Model([{'博士是一名主播', '博士是一名主播，会日常直播'}])
    monkeypatch.setattr(organizer, '_gate', lambda name: False)
    assert await _run(model) is None
    assert model.calls == 0 and legacy_dedup.read_marker(directory) is None
    assert not (directory / legacy_dedup.BACKUP_DIR).exists()

    monkeypatch.setattr(organizer, '_gate', lambda name: True)
    state = json.loads((directory / 'long_term_portable.json').read_text(encoding='utf-8'))
    state['maintenance'] = {legacy_dedup.MAINTENANCE_KEY: legacy_dedup.VERSION}
    (directory / 'long_term_portable.json').write_text(json.dumps(state), encoding='utf-8')
    assert await _run(model) is None and model.calls == 0


@pytest.mark.asyncio
async def test_a_device_that_cleaned_before_syncing_publishes_the_mark_once_it_syncs(memory):
    directory, facts = memory
    await _seed(directory, facts)
    (directory / 'long_term_portable.json').unlink()
    await _run(_Model([]))
    assert legacy_dedup.local_version(directory) == legacy_dedup.VERSION and legacy_dedup.published_version(directory) == 0
    (directory / 'long_term_portable.json').write_text(json.dumps({'evidence': {'retirements': [], 'confirmations': []}}),
                                                       encoding='utf-8')
    assert await _run(_Model([])) is None
    assert legacy_dedup.published_version(directory) == legacy_dedup.VERSION


@pytest.mark.asyncio
async def test_restore_brings_folded_facts_back_under_new_ids(memory):
    from app.memory_server import legacy_dedup as runner
    directory, facts = memory
    await _seed(directory, facts)
    await _run(_Model([{'博士是一名主播', '博士是一名主播，会日常直播'}]))

    assert await runner.restore(NAME) == 1
    live = json.loads((directory / 'facts.json').read_text(encoding='utf-8'))
    [back] = [row for row in live if row.get('restored_from') == 'dup']
    assert back['id'] != 'dup' and back['text'] == '博士是一名主播，会日常直播'
    assert 'superseded_by' not in back and 'arbitration_archived_at' not in back
    # The archived row stays archived, so a peer's copy of it agrees.
    archived = {row['id']: row for row in json.loads((directory / 'facts_archive.json').read_text(encoding='utf-8'))}
    assert archived['dup']['superseded_by'] == 'old'
    assert await runner.restore(NAME) == 0


@pytest.mark.asyncio
async def test_backup_copies_the_live_database_consistently(tmp_path):
    import sqlite3
    directory = tmp_path / NAME
    directory.mkdir()
    (directory / 'facts.json').write_text('[]', encoding='utf-8')
    database = sqlite3.connect(directory / 'time_indexed.db')
    database.execute('CREATE TABLE t (x)')
    database.execute('INSERT INTO t VALUES (1)')
    database.commit()
    (directory / legacy_dedup.BACKUP_DIR / 'legacy-dedup-older').mkdir(parents=True)
    copy = legacy_dedup.backup(directory, 'now')
    database.close()
    with sqlite3.connect(copy / 'time_indexed.db') as check:
        assert check.execute('SELECT x FROM t').fetchall() == [(1,)]
    check.close()
    assert (copy / 'facts.json').exists() and not (copy / legacy_dedup.BACKUP_DIR).exists()
    assert Path(copy).name == 'legacy-dedup-now'


@pytest.mark.asyncio
async def test_duplicates_age_already_archived_are_folded_in_place(memory):
    """Absorbed facts move to the archive after a week, so most old duplicates sit there."""
    directory, facts = memory
    facts._facts[NAME] = [_fact('live', '博士喜欢吃辣', created='2026-06-12T23:00:00')]
    await facts.asave_facts(NAME)
    (directory / 'facts_archive.json').write_text(json.dumps([
        _fact('old', '博士是一名主播', created='2026-06-12T21:00:00', absorbed=True),
        _fact('dup', '博士是一名主播，会日常直播', created='2026-06-12T22:00:00', absorbed=True),
    ], ensure_ascii=False), encoding='utf-8')
    (directory / 'reflections.json').write_text(json.dumps([
        {'id': 'r1', 'text': '博士以直播为业', 'entity': 'master', 'status': 'confirmed',
         'source_fact_ids': ['old', 'dup'], 'created_at': '2026-06-13T00:00:00'}], ensure_ascii=False), encoding='utf-8')

    marker = await _run(_Model([{'博士是一名主播', '博士是一名主播，会日常直播'}]))

    assert marker['archived'] == {'dup': 'old'}
    archive = {row['id']: row for row in json.loads((directory / 'facts_archive.json').read_text(encoding='utf-8'))}
    assert archive['dup']['superseded_by'] == 'old' and archive['dup']['arbitration_archived_at']
    assert 'arbitration_archived_at' not in archive['old']
    [reflection] = json.loads((directory / 'reflections.json').read_text(encoding='utf-8'))
    assert reflection['source_fact_ids'] == ['old']


@pytest.mark.asyncio
async def test_an_interrupted_run_resumes_without_asking_again(memory):
    directory, facts = memory
    await _seed(directory, facts)
    model = _Model([{'博士是一名主播', '博士是一名主播，会日常直播'}])
    assert await _run(model, still_idle=lambda: False) is None
    marker = legacy_dedup.read_marker(directory)
    assert marker['stage'] == 'deciding' and model.calls == 0
    backup = marker['backup']
    done = await _run(model)
    assert done['stage'] == 'done' and done['backup'] == backup and model.calls == 1


@pytest.mark.asyncio
async def test_a_failing_model_waits_and_finally_keeps_both(memory):
    directory, facts = memory
    await _seed(directory, facts)
    failing = AsyncMock(return_value=None)
    from app.memory_server.legacy_dedup import MAX_FAILURES
    for _ in range(MAX_FAILURES - 1):
        assert await _run(failing) is None
    marker = await _run(failing)
    assert marker['stage'] == 'done' and marker['archived'] == {}
    assert {row['id'] for row in json.loads((directory / 'facts.json').read_text(encoding='utf-8'))} == {'old', 'dup', 'yes', 'no'}


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', [1, 0])
async def test_a_batch_over_the_prompt_budget_is_split_and_an_oversized_pair_left_alone(memory, monkeypatch, budget):
    from app.memory_server import legacy_dedup as runner
    directory, facts = memory
    await _seed(directory, facts)
    seen = []
    model = _Model([{'博士是一名主播', '博士是一名主播，会日常直播'}])

    async def counting(prompt, *args, **kwargs):
        seen.append(prompt.count(' candidate: '))
        return await model(prompt)
    # The "token count" of a prompt is its number of pairs.
    monkeypatch.setattr(runner, 'PROMPT_TOKEN_BUDGET', budget)
    monkeypatch.setattr('utils.tokenize.acount_tokens', AsyncMock(side_effect=lambda text: text.count(' candidate: ')))
    marker = await _run(counting)
    assert marker['stage'] == 'done'
    if budget:
        assert seen and set(seen) == {1}            # split down to one pair per call
        assert marker['archived'] == {'dup': 'old'}
    else:
        assert seen == [] and marker['archived'] == {}   # even one pair is too long: left as two facts


@pytest.mark.asyncio
async def test_a_fold_is_dropped_when_its_facts_changed_while_the_model_answered(memory):
    directory, facts = memory
    await _seed(directory, facts)
    model = _Model([{'博士是一名主播', '博士是一名主播，会日常直播'}])

    async def answer_while_sync_rewrites(prompt, *args, **kwargs):
        decisions = await model(prompt)
        # A snapshot lands meanwhile and rewrites the fact that was to stay.
        row = next(row for row in facts._facts[NAME] if row['id'] == 'old')
        row['text'] = '博士不再直播了'
        await facts.asave_facts(NAME)
        return decisions
    marker = await _run(answer_while_sync_rewrites)
    assert marker['archived'] == {}
    assert {row['id'] for row in json.loads((directory / 'facts.json').read_text(encoding='utf-8'))} == {'old', 'dup', 'yes', 'no'}
