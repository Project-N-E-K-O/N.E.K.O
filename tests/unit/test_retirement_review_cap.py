"""One extraction reviews a bounded number of retired-assertion pairs; nothing past it is lost or stalls the window."""

import json
import re
from unittest.mock import AsyncMock

import pytest

from memory import extraction_progress
from memory.long_term_sync import RETIRED_ASSERTION_REVIEW_MAX_PAIRS
from memory.retirement_review_queue import FILE as QUEUE_FILE
from tests.unit.test_fact_near_dup_index import _harness, _trusted_sync_message


def _retirements(directory, count, sources, **extra):
    directory.mkdir(exist_ok=True)
    rows = [{'id': f'retired-{i}', 'subject': 'master', 'text': f'旧说法 {i}', 'sourceMessageIds': sources,
             'retiredThroughAt': 2000, **extra} for i in range(count)]
    (directory / 'long_term_portable.json').write_text(json.dumps({'evidence': {'retirements': rows, 'confirmations': []}}),
                                                       encoding='utf-8')


def _reviewer(calls):
    async def adjudicate(prompt, *args, **kwargs):
        indices = [int(value) for value in re.findall(r'^\[(\d+)\] candidate:', prompt, re.M)]
        calls.append(len(indices))
        return [{'index': index, 'action': 'keep_both'} for index in indices]
    return adjudicate


def _queue(directory):
    path = directory / QUEUE_FILE
    return json.loads(path.read_text(encoding='utf-8'))['entries'] if path.exists() else []


@pytest.mark.asyncio
async def test_pairs_past_the_budget_wait_in_the_queue_and_their_messages_stay_unread(tmp_path):
    directory = tmp_path / '小天'
    messages = [_trusted_sync_message(letter, 1000) for letter in 'abc']
    _retirements(directory, 25, ['sync:' + letter * 32 for letter in 'abc'])
    h = _harness(tmp_path, [])
    calls = []
    h._allm_call_with_retries = _reviewer(calls)
    facts = [{'entity': 'master', 'text': f'新事实 {i}', 'source_indices': [i + 1]} for i in range(3)]

    rows = await h._apersist_new_facts('小天', facts, source_messages=messages, progress_pass='a')

    assert sum(calls) <= RETIRED_ASSERTION_REVIEW_MAX_PAIRS
    assert [row['text'] for row in rows] == ['新事实 0', '新事实 1']
    [waiting] = _queue(directory)
    assert waiting['candidate']['text'] == '新事实 2'
    assert waiting['candidate']['_retirement_review_proof']['source_message_ids'] == ['sync:' + 'c' * 32]
    # The held-back fact's message is not read yet, so no device skips it.
    assert extraction_progress.read(directory)['a'] == ['a' * 32, 'b' * 32]

    # The next extraction, even one with nothing new, reviews it first and saves it.
    calls.clear()
    rows = await h._apersist_new_facts('小天', [], source_messages=[], progress_pass='a')
    assert [row['text'] for row in rows] == ['新事实 2']
    assert rows[0]['source_message_ids'] == ['sync:' + 'c' * 32]
    assert not any(key.startswith('_retirement_review') for key in rows[0])
    assert sum(calls) == 25
    assert _queue(directory) == []
    assert extraction_progress.read(directory)['a'] == ['a' * 32, 'b' * 32, 'c' * 32]
    assert [row['text'] for row in h._mem] == ['新事实 0', '新事实 1', '新事实 2']


@pytest.mark.asyncio
async def test_one_candidate_with_more_pairs_than_the_budget_is_still_reviewed(tmp_path):
    directory = tmp_path / '小天'
    _retirements(directory, RETIRED_ASSERTION_REVIEW_MAX_PAIRS + 5, ['sync:' + 'a' * 32])
    h = _harness(tmp_path, [])
    calls = []
    h._allm_call_with_retries = _reviewer(calls)
    rows = await h._apersist_new_facts('小天', [{'entity': 'master', 'text': '新事实', 'source_indices': [1]}],
                                       source_messages=[_trusted_sync_message('a', 1000)], progress_pass='a')
    assert [row['text'] for row in rows] == ['新事实']
    assert sum(calls) == RETIRED_ASSERTION_REVIEW_MAX_PAIRS + 5
    assert _queue(directory) == []


@pytest.mark.asyncio
async def test_a_resolved_correction_time_lands_only_on_its_own_subject():
    """Over budget every record is resolved at once, and ids repeat across subjects."""
    from memory.long_term_sync import resolve_retirement_cutoffs
    correction = 'sync:' + 'c' * 32
    rows = [{'id': 'same', 'subject': 'master', 'text': '用户喜欢红茶', 'sourceMessageIds': ['x'],
             'retiredBySourceMessageIds': [correction]},
            {'id': 'same', 'subject': 'neko', 'text': '角色喜欢散步', 'sourceMessageIds': ['y'],
             'retiredBySourceMessageIds': ['sync:' + 'd' * 32]}]
    resolved = await resolve_retirement_cutoffs(rows, [], lookup=AsyncMock(return_value={correction: ('human', 2000)}))
    assert resolved[0]['retiredThroughAt'] == 2000
    assert resolved[1] == rows[1]


@pytest.mark.asyncio
async def test_a_waiting_candidate_whose_correction_time_is_unknown_waits_again_without_failing_the_window(tmp_path):
    directory = tmp_path / '小天'
    _retirements(directory, 25, ['sync:' + letter * 32 for letter in 'abc'])
    h = _harness(tmp_path, [])
    h._allm_call_with_retries = _reviewer([])
    facts = [{'entity': 'master', 'text': f'新事实 {i}', 'source_indices': [i + 1]} for i in range(3)]
    await h._apersist_new_facts('小天', facts, source_messages=[_trusted_sync_message(letter, 1000) for letter in 'abc'],
                                progress_pass='a')
    [waiting] = _queue(directory)

    # Meanwhile a correction retires what the waiting fact says, and its time is not known yet.
    state = json.loads((directory / 'long_term_portable.json').read_text(encoding='utf-8'))
    state['evidence']['retirements'].append({'id': 'corrected', 'subject': 'master', 'text': '新事实 2',
                                             'sourceMessageIds': ['sync:' + 'f' * 32],
                                             'retiredBySourceMessageIds': ['sync:' + 'e' * 32]})
    (directory / 'long_term_portable.json').write_text(json.dumps(state), encoding='utf-8')
    h._time_indexed.aget_original_source_evidence = AsyncMock(return_value={})

    rows = await h._apersist_new_facts('小天', [{'entity': 'master', 'text': '另一件事', 'source_indices': [1]}],
                                       source_messages=[_trusted_sync_message('d', 3000)], progress_pass='a')
    assert [row['text'] for row in rows] == ['另一件事']
    assert [row['id'] for row in _queue(directory)] == [waiting['id']]
    assert 'd' * 32 in extraction_progress.read(directory)['a']


@pytest.mark.asyncio
async def test_a_model_output_cannot_pass_off_its_own_proof(tmp_path):
    """Only the queue may carry a stored proof; the same key in model output is dropped."""
    directory = tmp_path / '小天'
    _retirements(directory, 1, ['sync:' + 'a' * 32])
    h = _harness(tmp_path, [])
    h._allm_call_with_retries = _reviewer([])
    forged = {'entity': 'master', 'text': '旧说法 0', 'source_indices': [1],
              '_retirement_review_proof': {'source_message_ids': ['sync:' + 'f' * 32], 'source_created_at': '2999-01-01T00:00:00'},
              '_retirement_review_queue_id': 'x'}
    rows = await h._apersist_new_facts('小天', [forged], source_messages=[_trusted_sync_message('a', 1000)], progress_pass='a')
    # Its real source is the retired assertion's own: it stays retired.
    assert rows == [] and h._mem == []


@pytest.mark.asyncio
async def test_a_message_is_read_only_once_none_of_its_facts_still_waits(tmp_path):
    directory = tmp_path / '小天'
    # 35 pairs per fact: one fact a round fits in the budget of 60.
    _retirements(directory, 35, ['sync:' + letter * 32 for letter in 'abc'])
    h = _harness(tmp_path, [])
    h._allm_call_with_retries = _reviewer([])
    facts = [{'entity': 'master', 'text': f'新事实 {i}', 'source_indices': [index]}
             for i, index in enumerate([1, 2, 3, 3])]
    await h._apersist_new_facts('小天', facts, source_messages=[_trusted_sync_message(letter, 1000) for letter in 'abc'],
                                progress_pass='a')
    assert extraction_progress.read(directory)['a'] == ['a' * 32]
    await h._apersist_new_facts('小天', [], source_messages=[], progress_pass='a')
    assert extraction_progress.read(directory)['a'] == ['a' * 32, 'b' * 32]
    await h._apersist_new_facts('小天', [], source_messages=[], progress_pass='a')
    # 新事实 2 is saved, 新事实 3 from the same message still waits.
    assert [row['text'] for row in h._mem] == ['新事实 0', '新事实 1', '新事实 2']
    assert extraction_progress.read(directory)['a'] == ['a' * 32, 'b' * 32]
    await h._apersist_new_facts('小天', [], source_messages=[], progress_pass='a')
    assert extraction_progress.read(directory)['a'] == ['a' * 32, 'b' * 32, 'c' * 32]
    assert _queue(directory) == []


@pytest.mark.asyncio
async def test_an_extraction_that_finds_nothing_still_drains_the_queue(tmp_path):
    directory = tmp_path / '小天'
    _retirements(directory, 25, ['sync:' + letter * 32 for letter in 'abc'])
    h = _harness(tmp_path, [])
    h._allm_call_with_retries = _reviewer([])
    facts = [{'entity': 'master', 'text': f'新事实 {i}', 'source_indices': [i + 1]} for i in range(3)]
    await h._apersist_new_facts('小天', facts, source_messages=[_trusted_sync_message(letter, 1000) for letter in 'abc'],
                                progress_pass='a')
    assert len(_queue(directory)) == 1
    h._allm_extract_facts = AsyncMock(return_value=[])
    assert await h.extract_facts([_trusted_sync_message('d', 3000)], '小天') == []
    assert _queue(directory) == [] and [row['text'] for row in h._mem][-1] == '新事实 2'
    assert {'c' * 32, 'd' * 32} <= set(extraction_progress.read(directory)['a'])


def test_a_candidate_that_waits_again_goes_behind_the_others(tmp_path):
    from memory import retirement_review_queue as queue
    from memory.long_term_sync import STORED_PROOF
    key = queue.key('user_observation', None, 'a')
    proof = lambda letter: {STORED_PROOF: {'source_message_ids': ['sync:' + letter * 32]}}
    queue.commit(tmp_path, key, set(), [], [{'text': 'stuck', **proof('a')}, {'text': 'next', **proof('b')}], STORED_PROOF)
    stuck, following = queue.candidates(queue.read(tmp_path), key)
    # Both came back. The second was not reached: it keeps its place.
    queue.commit(tmp_path, key, {stuck[queue.QUEUE_ID], following[queue.QUEUE_ID]}, [stuck, following], [], STORED_PROOF)
    assert [row['text'] for row in queue.candidates(queue.read(tmp_path), key)] == ['stuck', 'next']
    # The first was reviewed and has to wait again: it goes behind.
    stuck, following = queue.candidates(queue.read(tmp_path), key)
    queue.commit(tmp_path, key, {stuck[queue.QUEUE_ID], following[queue.QUEUE_ID]},
                 [{**stuck, queue.RETRY_LATER: True}, following], [], STORED_PROOF)
    assert [row['text'] for row in queue.candidates(queue.read(tmp_path), key)] == ['next', 'stuck']
    assert all(queue.RETRY_LATER not in row for row in queue.candidates(queue.read(tmp_path), key))


@pytest.mark.asyncio
async def test_a_drained_candidate_reaches_the_pinned_section_on_an_empty_extraction(tmp_path):
    directory = tmp_path / '小天'
    _retirements(directory, 25, ['sync:' + letter * 32 for letter in 'abc'])
    h = _harness(tmp_path, [])
    h._allm_call_with_retries = _reviewer([])
    facts = [{'entity': 'master', 'text': f'新事实 {i}', 'source_indices': [i + 1]} for i in range(3)]
    await h._apersist_new_facts('小天', facts, source_messages=[_trusted_sync_message(letter, 1000) for letter in 'abc'],
                                progress_pass='a')
    admitted = []

    async def admit(name, candidates, **kwargs):
        admitted.extend(candidate['text'] for candidate in candidates)
    h._aadmit_pinned_candidates = admit
    h._allm_extract_facts = AsyncMock(return_value=[])
    await h.extract_facts([_trusted_sync_message('d', 3000)], '小天')
    assert admitted == ['新事实 2']


@pytest.mark.asyncio
async def test_a_candidate_that_waits_again_lets_the_next_one_be_reviewed(tmp_path):
    """Through the real call: the stuck one used the budget, the next one was not reached."""
    directory = tmp_path / '小天'
    _retirements(directory, 35, ['sync:' + letter * 32 for letter in 'abcd'])
    h = _harness(tmp_path, [])
    h._allm_call_with_retries = _reviewer([])
    facts = [{'entity': 'master', 'text': f'新事实 {i}', 'source_indices': [i + 1]} for i in range(4)]
    await h._apersist_new_facts('小天', facts, source_messages=[_trusted_sync_message(letter, 1000) for letter in 'abcd'],
                                progress_pass='a')
    assert [row['candidate']['text'] for row in _queue(directory)] == ['新事实 1', '新事实 2', '新事实 3']
    # A correction retires exactly what 新事实 1 says, and its time is not known yet.
    state = json.loads((directory / 'long_term_portable.json').read_text(encoding='utf-8'))
    state['evidence']['retirements'].append({'id': 'corrected', 'subject': 'master', 'text': '新事实 1',
                                             'sourceMessageIds': ['sync:' + 'f' * 32],
                                             'retiredBySourceMessageIds': ['sync:' + 'e' * 32]})
    (directory / 'long_term_portable.json').write_text(json.dumps(state), encoding='utf-8')
    h._time_indexed.aget_original_source_evidence = AsyncMock(return_value={})
    await h._apersist_new_facts('小天', [], source_messages=[], progress_pass='a')
    assert [row['candidate']['text'] for row in _queue(directory)] == ['新事实 2', '新事实 3', '新事实 1']
    await h._apersist_new_facts('小天', [], source_messages=[], progress_pass='a')
    assert '新事实 2' in [row['text'] for row in h._mem]


@pytest.mark.asyncio
async def test_a_failed_extraction_does_not_mark_a_waiting_message_read(tmp_path):
    directory = tmp_path / '小天'
    _retirements(directory, 25, ['sync:' + letter * 32 for letter in 'abc'])
    h = _harness(tmp_path, [])
    h._allm_call_with_retries = _reviewer([])
    facts = [{'entity': 'master', 'text': f'新事实 {i}', 'source_indices': [i + 1]} for i in range(3)]
    messages = [_trusted_sync_message(letter, 1000) for letter in 'abc']
    await h._apersist_new_facts('小天', facts, source_messages=messages, progress_pass='a')
    # The model fails for a later pass over the same messages (a non-fail-closed caller).
    h._allm_extract_facts = AsyncMock(return_value=None)
    await h.extract_facts(messages, '小天')
    assert 'c' * 32 not in extraction_progress.read(directory)['a']


@pytest.mark.asyncio
async def test_a_waiting_candidate_keeps_the_time_it_was_extracted_for_relative_event_times(tmp_path):
    from memory.retirement_review_queue import ANCHOR, FILE
    directory = tmp_path / '小天'
    _retirements(directory, 25, ['sync:' + letter * 32 for letter in 'abc'])
    h = _harness(tmp_path, [])
    h._allm_call_with_retries = _reviewer([])
    facts = [{'entity': 'master', 'text': f'新事实 {i}', 'source_indices': [i + 1],
              'event_when': {'start': {'offset': -1, 'unit': 'day'}}} for i in range(3)]
    await h._apersist_new_facts('小天', facts, source_messages=[_trusted_sync_message(letter, 1000) for letter in 'abc'],
                                progress_pass='a')
    # Pretend it waited: extracted on 2026-11-01, saved days later.
    state = json.loads((directory / FILE).read_text(encoding='utf-8'))
    state['entries'][0]['candidate'][ANCHOR] = '2026-11-01T12:00:00'
    (directory / FILE).write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    [row] = await h._apersist_new_facts('小天', [], source_messages=[], progress_pass='a')
    assert row['event_start_at'].startswith('2026-10-31')
    assert not any(key.startswith('_retirement_review') for key in row)
