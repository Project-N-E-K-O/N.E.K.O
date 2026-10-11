"""Regression coverage for maintainer review 5979068917."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_identity import numeric_v2_character_ids
from tests.unit.test_theater_numeric_v2_router import _client
from tests.unit.theater_workshop.numeric_v2_fixture import numeric_v2_setup
from theater_workshop.sdk.numeric_v2_project_store import _setup_fields
from utils.cloudsave_runtime import MaintenanceModeError


@pytest.mark.parametrize('offer', ['一起去长街吗？', '（指向长街）一起去长街吗？'])
def test_authored_invitation_survives_followup_dialogue(offer):
    assert workflow._authored_offer_visible({'performance': offer + '（歪头）你觉得呢？'}, offer)
    assert not workflow._authored_offer_visible({'performance': '（一起去长街吗？）你觉得呢？'}, offer)


def test_character_index_preserves_maintenance_response():
    class Config:
        def load_characters(self, **kwargs):
            raise MaintenanceModeError('maintenance_readonly', target='characters.json')
    with pytest.raises(MaintenanceModeError):
        numeric_v2_character_ids(Config())


@pytest.mark.parametrize('edit', ['threshold', 'label', 'delete', 'duplicate'])
def test_legacy_band_extras_do_not_block_edits(edit):
    setup = numeric_v2_setup()
    bands = setup['metrics'][0]['bands']
    for index, band in enumerate(bands):
        band['color'] = str(index)
    if edit == 'duplicate':
        bands[1].update({key: bands[0][key] for key in ('min', 'max', 'label')})
    incoming = deepcopy(setup)
    changed = incoming['metrics'][0]['bands']
    if edit == 'threshold':
        changed[0]['max'] += 10
        changed[1]['min'] += 10
    elif edit == 'label':
        changed[0]['label'] = '非常戒备'
    elif edit == 'delete':
        changed.pop(0)
        changed[0]['min'] = 0
    cleaned = _setup_fields(incoming, legacy=setup)
    assert all('color' not in band for band in cleaned['metrics'][0]['bands'])
    changed[0]['new_key'] = 'new'
    with pytest.raises(ValueError, match='unsupported_metric_band_field'):
        _setup_fields(incoming, legacy=setup)


@pytest.mark.parametrize('description', [123, {'x': 1}, ['a'], '字' * 2001])
def test_band_description_rejects_bad_type_or_size(description):
    setup = numeric_v2_setup()
    setup['metrics'][0]['bands'][0]['description'] = description
    with pytest.raises(ValueError, match='invalid_metric_band_description'):
        _setup_fields(setup)


def test_cancelled_opening_cannot_enqueue_speech(tmp_path, monkeypatch):
    from main_routers import numeric_theater_router as router
    queued = []
    async def speech(*args, **kwargs):
        queued.append(args)
        return {'audio_queued': True}
    monkeypatch.setattr(router, 'speak_committed_line', speech)
    with _client(tmp_path, monkeypatch) as client:
        scope = {'story_id': 'numeric_v2_contract', 'session_id': 'cancelled_speech'}
        owner = {'X-Neko-Theater-Activity': 'cancelled-speech-owner'}
        assert client.post('/api/theater-numeric/session/start', headers=owner, json=scope).status_code == 200
        ended = client.post('/api/theater-numeric/session/end', headers=owner, json={**scope,
            'base_revision': 0, 'base_lifecycle_revision': 0, 'cancelled_start': True})
        assert ended.status_code == 200, ended.text
        assert not ended.json().get('end_receipt_id')
        result = client.post('/api/theater-numeric/session/speak-block', json={**scope,
            'revision': 0, 'lifecycle_revision': 1, 'block_index': 1,
            'playback_request_id': 'cancelled-speech'})
        assert result.status_code == 409, result.text
        assert result.json()['reason'] == 'session_already_ended'
        assert not queued


def test_imported_band_extensions_are_discarded_but_metric_fields_remain_strict():
    setup = numeric_v2_setup()
    setup['metrics'][0]['bands'][0]['color'] = '#ccc'
    package_metrics = deepcopy(setup['metrics'])
    cleaned = _setup_fields(setup, legacy={'metrics': package_metrics})
    assert 'color' not in cleaned['metrics'][0]['bands'][0]
    setup['metrics'][0]['unit'] = '点'
    with pytest.raises(ValueError, match='unsupported_metric_field'):
        _setup_fields(setup, legacy={'metrics': [{'id': package_metrics[0]['id'], 'bands': package_metrics[0]['bands']}]})


def test_import_story_author_snapshot_roundtrip_cleans_band_extensions(tmp_path):
    from contextlib import nullcontext
    from tests.unit.theater_workshop.numeric_v2_fixture import numeric_v2_story
    from theater_workshop.sdk.numeric_v2_project_store import NumericV2ProjectStore
    from theater_workshop.sdk.numeric_v2 import NumericV2Compiler
    from theater_workshop.host import InProcessPackageGateway
    story = numeric_v2_story()
    for definition in story['metric_schema'].values():
        for band in definition['bands']:
            band['color'] = '#ccc'
    compiler = NumericV2Compiler(InProcessPackageGateway())
    store = NumericV2ProjectStore(tmp_path / 'first', transaction=nullcontext, compiler=compiler)
    source = store.import_story(story)
    assert all('color' not in band for metric in source['setup']['metrics'] for band in metric['bands'])
    # An existing author snapshot may still contain the package's extra fields.
    for metric in source['setup']['metrics']:
        metric['bands'] = deepcopy(story['metric_schema'][metric['id']]['bands'])
    second = NumericV2ProjectStore(tmp_path / 'second', transaction=nullcontext, compiler=compiler)
    imported = second.import_project(source)
    assert all('color' not in band for metric in imported['setup']['metrics'] for band in metric['bands'])
    assert imported['story'] == story
