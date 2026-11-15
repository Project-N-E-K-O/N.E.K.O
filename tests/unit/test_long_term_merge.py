"""The shared three-way merge samples, plus limits too large to keep as samples."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from memory.long_term_merge import PROGRESS_MAX, merge_documents, merge_retirement, same_content
from utils.long_term_memory_protocol import document

CASES = json.loads((Path(__file__).parents[1] / 'fixtures/long_term_merge_cases.json').read_text(encoding='utf-8'))['cases']


@pytest.mark.parametrize('case', CASES, ids=[case['name'] for case in CASES])
def test_shared_merge_case(case):
    inputs = deepcopy([case['base'], case['local'], case['remote']])
    merged = merge_documents(case['base'], case['local'], case['remote'], case['completedAt'])
    assert merged == case['expected']
    # Every merged document must itself be publishable, and merging never edits its inputs.
    assert document(merged) == merged
    assert [case['base'], case['local'], case['remote']] == inputs


@pytest.mark.parametrize('case', CASES, ids=[case['name'] for case in CASES])
def test_merging_the_result_again_changes_nothing(case):
    """Once the merged document is published and becomes the base, there is nothing left to publish."""
    merged = merge_documents(case['base'], case['local'], case['remote'], case['completedAt'])
    base = {'document': merged, 'nativeEntries': merged['entries']}
    assert merge_documents(base, merged, merged, case['completedAt']) == merged


def _document(**parts):
    return {'schemaVersion': 3, 'completedAt': 1000, 'entries': [], 'evidence': {'retirements': [], 'confirmations': []},
            'progress': {'a': [], 'b': []}, **parts}


def test_progress_drops_the_oldest_published_entries_first():
    published = [f'{index:032x}' for index in range(PROGRESS_MAX)]
    added = [f'{index:032x}' for index in range(PROGRESS_MAX, PROGRESS_MAX + 3)]
    base = {'document': _document(progress={'a': published, 'b': []}), 'nativeEntries': []}
    merged = merge_documents(base, _document(progress={'a': published + added, 'b': []}),
                             _document(progress={'a': published, 'b': []}), 9000)
    assert merged['progress']['a'] == published[3:] + added
    assert document(merged) == merged


def test_identifier_lists_keep_the_newest_within_their_limit():
    def entry(ids):
        return {'kind': 'reflection', 'subject': 'master', 'id': 'r1', 'text': 'text', 'metadata': {'sourceFactIds': ids}}
    old = [f'f{index}' for index in range(199)]
    base = {'document': _document(entries=[entry(old)]), 'nativeEntries': [entry(old)]}
    merged = merge_documents(base, _document(entries=[entry([*old, 'here'])]), _document(entries=[entry([*old, 'there'])]), 9000)
    assert merged['entries'][0]['metadata']['sourceFactIds'] == [*old[1:], 'there', 'here']
    assert document(merged) == merged


def test_retirement_join_keeps_an_unproven_cutoff_only_when_neither_side_has_a_correction():
    old = {'id': 'r1', 'subject': 'master', 'sourceMessageIds': ['s1'], 'retiredThroughAt': 1000}
    new = {'id': 'r1', 'subject': 'master', 'sourceMessageIds': ['s2'], 'retiredThroughAt': 2000}
    assert merge_retirement(old, new)['retiredThroughAt'] == 2000
    proven = {**new, 'retiredBySourceMessageIds': ['c1'], 'retiredThroughAtFromCorrection': True}
    joined = merge_retirement(old, proven)
    assert joined['retiredThroughAt'] == 2000 and joined['retiredThroughAtFromCorrection'] is True
    # The unproven processing time never outlasts a correction that carries its own clock.
    assert merge_retirement({**old, 'retiredThroughAt': 5000}, proven)['retiredThroughAt'] == 2000


def test_joined_evidence_never_exceeds_what_a_snapshot_may_carry(monkeypatch):
    from memory import long_term_merge
    monkeypatch.setattr(long_term_merge, 'EVIDENCE_MAX', 3)

    def retired(prefix):
        return [{'id': f'{prefix}{index}', 'subject': 'master', 'sourceMessageIds': ['s']} for index in range(3)]
    mine = _document(evidence={'retirements': retired('here'), 'confirmations': []})
    theirs = _document(evidence={'retirements': retired('there'), 'confirmations': []})
    merged = merge_documents(None, mine, theirs, 9000)
    # The published records come first, so they are the oldest and go first.
    assert [row['id'] for row in merged['evidence']['retirements']] == ['here0', 'here1', 'here2']
    assert document(merged) == merged


def test_same_content_ignores_only_the_completion_time():
    assert same_content(_document(), {**_document(), 'completedAt': 2000})
    assert not same_content(_document(), _document(progress={'a': ['a' * 32], 'b': []}))
    older = {'schemaVersion': 2, 'completedAt': 1, 'entries': [], 'evidence': {'retirements': [], 'confirmations': []}}
    assert same_content(older, _document())
    rows = [{'kind': kind, 'subject': 'master', 'id': 'x', 'text': 'text', 'metadata': {}} for kind in ('persona', 'reflection')]
    assert same_content(_document(entries=rows), _document(entries=rows[::-1]))


def test_evidence_is_trimmed_oldest_first_long_before_the_protocol_limit():
    from memory.long_term_merge import EVIDENCE_PRUNE_ABOVE, EVIDENCE_PRUNE_TO, RETIREMENT_SOURCES_MAX, prune_evidence
    small = {'retirements': [{'id': 'r', 'subject': 'master', 'sourceMessageIds': ['s']}], 'confirmations': []}
    assert prune_evidence(small) is small
    retirements = [{'id': f'r{index}', 'subject': 'master', 'sourceMessageIds': ['s']} for index in range(EVIDENCE_PRUNE_ABOVE + 1)]
    confirmations = [{'fingerprint': f'1:{index}:1', 'factId': 'f'} for index in range(EVIDENCE_PRUNE_ABOVE + 1)]
    pruned = prune_evidence({'retirements': retirements, 'confirmations': confirmations})
    assert pruned['retirements'] == retirements[-EVIDENCE_PRUNE_TO:]
    assert pruned['confirmations'] == confirmations[-EVIDENCE_PRUNE_TO:]
    at_limit = {'retirements': retirements[:EVIDENCE_PRUNE_ABOVE], 'confirmations': []}
    assert prune_evidence(at_limit) is at_limit
    sources = [f's{index}' for index in range(RETIREMENT_SOURCES_MAX + 5)]
    wide = prune_evidence({'retirements': [{'id': 'r', 'subject': 'master', 'sourceMessageIds': sources}], 'confirmations': []})
    assert wide['retirements'][0]['sourceMessageIds'] == sources[5:]
    assert document(_document(evidence=pruned)) and document(_document(evidence=wide))


def test_fitting_merged_evidence_reports_only_records_this_device_held(monkeypatch):
    from memory import long_term_merge

    def retirement(identity):
        return {'id': identity, 'subject': 'master', 'sourceMessageIds': [f'source-{identity}']}

    def held(*identities):
        return _document(evidence={'retirements': [retirement(identity) for identity in identities], 'confirmations': []})
    monkeypatch.setattr(long_term_merge, 'EVIDENCE_MAX', 3)
    base = {'document': held('a', 'b'), 'nativeEntries': []}
    # Each side adds one record to the two both started from: four do not fit, the oldest goes.
    report = {}
    merged = merge_documents(base, held('a', 'b', 'mine'), held('a', 'b', 'theirs'), 9000, report=report)
    assert [row['id'] for row in merged['evidence']['retirements']] == ['b', 'theirs', 'mine']
    assert report == {'localEvidenceDropped': True}
    # The lists fit: nothing is cut, whatever the three-way merge itself removed.
    report = {}
    merged = merge_documents(base, held('a', 'b', 'mine'), held('b'), 9000, report=report)
    assert [row['id'] for row in merged['evidence']['retirements']] == ['b', 'mine']
    assert report == {'localEvidenceDropped': False}
    # A cut that takes only records this device never held is not this device's loss.
    report = {}
    merged = merge_documents(None, held('mine'), held('x', 'y', 'z'), 9000, report=report)
    assert [row['id'] for row in merged['evidence']['retirements']] == ['y', 'z', 'mine']
    assert report == {'localEvidenceDropped': False}
    # Without a report the merge is what it always was.
    assert merge_documents(base, held('a', 'b', 'mine'), held('a', 'b', 'theirs'), 9000) == merge_documents(
        base, held('a', 'b', 'mine'), held('a', 'b', 'theirs'), 9000, report={})
