import json
from pathlib import Path
import pytest
from utils.long_term_memory_protocol import document, head
FIXTURE = json.loads((Path(__file__).parents[1] / 'fixtures/long_term_memory_v2.json').read_text(encoding='utf-8'))
def test_shared_v2_contract():
    assert document(FIXTURE['valid']) == FIXTURE['valid']
@pytest.mark.parametrize('value', FIXTURE['invalid'])
def test_invalid_envelope(value):
    with pytest.raises((ValueError, TypeError)): document(value)
@pytest.mark.parametrize('metadata', FIXTURE['invalidMetadata'])
def test_invalid_metadata(metadata):
    value = {**FIXTURE['valid'], 'entries': [{**FIXTURE['valid']['entries'][0], 'metadata': metadata}]}
    with pytest.raises((ValueError, TypeError)): document(value)
def test_expired_head():
    assert head({'ownerId': 'owner', 'characterSyncId': 'uid', 'revision': 3, 'completedAt': 2000, 'expired': True, 'document': None}, 'owner', 'uid')['document'] is None


THREE = json.loads((Path(__file__).parents[1] / 'fixtures/long_term_memory_v3.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('value', [THREE['valid'], *THREE['validVariants']])
def test_shared_schema_three_contract(value):
    assert document(value) == value


@pytest.mark.parametrize('value', THREE['invalid'])
def test_invalid_schema_three_envelope(value):
    with pytest.raises((ValueError, TypeError)):
        document(value)


@pytest.mark.parametrize('metadata', THREE['invalidMetadata'])
def test_invalid_schema_three_metadata(metadata):
    value = {**THREE['valid'], 'entries': [{**THREE['valid']['entries'][0], 'metadata': metadata}]}
    with pytest.raises((ValueError, TypeError)):
        document(value)


@pytest.mark.parametrize('metadata', THREE['schemaThreeOnlyMetadata'])
def test_new_metadata_is_not_accepted_inside_older_documents(metadata):
    entry = {**THREE['valid']['entries'][0], 'metadata': metadata}
    assert document({**THREE['valid'], 'entries': [entry]})['entries'] == [entry]
    with pytest.raises(ValueError):
        document({**FIXTURE['valid'], 'entries': [entry]})


def test_progress_and_counted_fact_limits():
    from utils.long_term_memory_protocol import PROGRESS_MAX
    identities = [f'{index:032x}' for index in range(PROGRESS_MAX + 1)]
    assert document({**THREE['valid'], 'progress': {'a': identities[:-1], 'b': []}})
    with pytest.raises(ValueError):
        document({**THREE['valid'], 'progress': {'a': identities, 'b': []}})
    entry = {**THREE['valid']['entries'][1], 'metadata': {'countedFactIds': [f'f{index}' for index in range(201)]}}
    with pytest.raises(ValueError):
        document({**THREE['valid'], 'entries': [entry]})


@pytest.mark.parametrize('base', [-1, True, 1.0, '1', None, 9_007_199_254_740_992])
def test_base_revision_must_be_a_plain_revision(base):
    from utils.long_term_memory_protocol import mutation
    with pytest.raises(ValueError):
        mutation({'operationId': 'a' * 32, 'baseRevision': base, 'document': THREE['valid']})


def test_schema_three_is_only_written_conditionally():
    from utils.long_term_memory_protocol import mutation
    value = {'operationId': 'a' * 32, 'baseRevision': 0, 'document': THREE['valid']}
    assert mutation(value) == value
    with pytest.raises(ValueError):
        mutation({'operationId': 'a' * 32, 'document': THREE['valid']})
    with pytest.raises(ValueError):
        mutation({**value, 'conflict': True})
    older = {'operationId': 'a' * 32, 'baseRevision': 3, 'document': FIXTURE['valid']}
    assert mutation(older) == older


def test_write_receipt_distinguishes_acceptance_conflict_and_refusal():
    from utils.long_term_memory_protocol import receipt
    accepted = {'ownerId': 'owner', 'characterSyncId': 'uid', 'operationId': 'a' * 32, 'revision': 4, 'accepted': True}
    assert receipt(accepted, 'owner', 'uid', 'a' * 32) == accepted
    conflict = {**accepted, 'accepted': False, 'conflict': True}
    assert receipt(conflict, 'owner', 'uid', 'a' * 32) == conflict
    refused = {**accepted, 'accepted': False, 'revision': 0}
    assert receipt(refused, 'owner', 'uid', 'a' * 32) == refused
    for invalid in ({**accepted, 'conflict': True}, {**conflict, 'conflict': False}, {**accepted, 'revision': 0},
                    {**accepted, 'operationId': 'b' * 32}, {**accepted, 'ownerId': 'other'}, {**accepted, 'extra': 1},
                    {**accepted, 'accepted': 1}, {**accepted, 'revision': 1.0}, None):
        with pytest.raises(ValueError):
            receipt(invalid, 'owner', 'uid', 'a' * 32)


@pytest.mark.parametrize('cold', THREE['invalidCold'])
def test_invalid_cold_list_is_refused_as_the_sync_service_refuses_it(cold):
    with pytest.raises((ValueError, TypeError)):
        document({**THREE['valid'], 'cold': cold})
