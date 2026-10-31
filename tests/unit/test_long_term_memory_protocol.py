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
