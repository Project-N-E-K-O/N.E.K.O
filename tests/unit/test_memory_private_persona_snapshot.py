from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from memory.persona.persistence import PersistenceMixin


class SnapshotManager(PersistenceMixin):
    def __init__(self, persona):
        self.persona = persona
        self.lock = asyncio.Lock()

    def _get_alock(self, name):
        return self.lock

    async def _aensure_persona_locked(self, name, *, strict_read=False):
        assert self.lock.locked() and strict_read
        return self.persona


@pytest.mark.asyncio
async def test_snapshot_is_detached_private_and_preserves_retained_fields():
    original = {
        'master': {'facts': [
            {'id': 'p1', 'text': 'resident', 'source': 'manual', 'protected': True,
             'suppress': True, 'embedding': [1, 2], 'history': [{'text': 'secret'}]},
            'legacy', {'text': 'unresident'},
            {'text': 'scoped', 'subject_kind': 'group'},
            {'text': 'partially scoped', 'scope': 'group'},
        ]},
        '@subject/group:abc': {'facts': ['secret']},
        '@subject/broken': {'facts': ['also secret']},
        'new_entity': {'facts': [{'id': '', 'text': 'unknown source', 'source': 'future'}]},
    }
    before = copy.deepcopy(original)
    manager = SnapshotManager(original)
    result = await manager.aget_private_persona_snapshot('cat')
    assert result == {'sections': [
        {'entity': 'master', 'entries': [
            {'id': 'p1', 'text': 'resident', 'source': 'manual', 'protected': True, 'suppressed': True},
            {'id': None, 'text': 'legacy', 'source': 'unknown', 'protected': False, 'suppressed': False},
            {'id': None, 'text': 'unresident', 'source': 'unknown', 'protected': False, 'suppressed': False},
        ]},
        {'entity': 'new_entity', 'entries': [
            {'id': None, 'text': 'unknown source', 'source': 'future', 'protected': False, 'suppressed': False},
        ]},
    ]}
    assert original == before
    original['master']['facts'][0]['text'] = 'changed'
    assert result['sections'][0]['entries'][0]['text'] == 'resident'


@pytest.mark.asyncio
async def test_snapshot_observes_character_lock_and_empty_is_success():
    manager = SnapshotManager({'master': {'facts': ['before']}})
    async with manager.lock:
        task = asyncio.create_task(manager.aget_private_persona_snapshot('cat'))
        await asyncio.sleep(0)
        assert not task.done()
        manager.persona['master']['facts'].clear()
    assert await task == {'sections': []}


@pytest.mark.asyncio
async def test_missing_source_metadata_does_not_discard_retained_knowledge():
    manager = SnapshotManager({'master': {'facts': [
        {'text': 'known preference', 'source': None},
        {'text': 'another preference', 'source': '  '},
    ]}})
    result = await manager.aget_private_persona_snapshot('cat')
    entries = result['sections'][0]['entries']
    assert [entry['source'] for entry in entries] == ['unknown', 'unknown']
    assert [entry['text'] for entry in entries] == ['known preference', 'another preference']


@pytest.mark.asyncio
async def test_snapshot_omits_empty_knowledge_and_normalizes_blank_ids_for_sdk():
    from plugin.sdk.shared.contracts.memory import MemoryTarget, decode_persona

    original = {'master': {'facts': [
        '', '  ', {'text': ''}, {'text': '\t', 'id': 'empty'},
        {'text': 'retained', 'id': '  ', 'protected': True, 'suppress': True},
    ]}}
    before = copy.deepcopy(original)
    result = await SnapshotManager(original).aget_private_persona_snapshot('cat')
    decoded = decode_persona({**result, 'target': MemoryTarget.private('cat').to_wire()})
    assert len(decoded.sections) == 1
    assert len(decoded.sections[0].entries) == 1
    entry = decoded.sections[0].entries[0]
    assert entry.id is None and entry.text == 'retained'
    assert entry.protected and entry.suppressed
    assert original == before


@pytest.mark.asyncio
@pytest.mark.parametrize('persona', [
    {'master': {'facts': [{'text': 7}]}}, {'master': {'facts': [{'protected': 1}]}},
    {'master': {'facts': [None]}}, {'master': {'facts': {}}}, {'master': []},
])
async def test_snapshot_rejects_malformed_data(persona):
    with pytest.raises(ValueError):
        await SnapshotManager(persona).aget_private_persona_snapshot('cat')


@pytest.mark.asyncio
@pytest.mark.parametrize('facts', [['x'] * 10001, ['x' * (1024 * 1024)]])
async def test_snapshot_rejects_oversize(facts):
    with pytest.raises(OverflowError):
        await SnapshotManager({'master': {'facts': facts}}).aget_private_persona_snapshot('cat')


@pytest.mark.asyncio
async def test_strict_cold_read_does_not_replace_corrupt_storage(tmp_path):
    path = tmp_path / 'persona.json'
    path.write_text('{broken', encoding='utf-8')
    manager = PersistenceMixin()
    manager._personas = {}
    manager._persona_path = lambda name: str(path)
    with pytest.raises(ValueError):
        await manager._aensure_persona_locked('cat', strict_read=True)
    assert path.read_text(encoding='utf-8') == '{broken'
    assert not manager._personas


@pytest.mark.asyncio
async def test_route_maps_errors_and_uses_current_manager(monkeypatch):
    from app.memory_server import routes

    monkeypatch.setattr(routes.runtime, 'persona_manager', None)
    with pytest.raises(HTTPException) as error:
        await routes.get_private_persona_snapshot('cat')
    assert error.value.status_code == 503
    for exc, status, code in [(ValueError(), 500, 'MEMORY_PROTOCOL_ERROR'),
                              (OverflowError(), 413, 'MEMORY_RESPONSE_TOO_LARGE'),
                              (OSError(), 503, None)]:
        manager = SimpleNamespace(aget_private_persona_snapshot=AsyncMock(side_effect=exc))
        monkeypatch.setattr(routes.runtime, 'persona_manager', manager)
        with pytest.raises(HTTPException) as error:
            await routes.get_private_persona_snapshot('cat')
        assert error.value.status_code == status
        if code:
            assert error.value.detail['code'] == code
    manager = SimpleNamespace(aget_private_persona_snapshot=AsyncMock(return_value={'sections': []}))
    monkeypatch.setattr(routes.runtime, 'persona_manager', manager)
    assert await routes.get_private_persona_snapshot('cat') == {'sections': []}

@pytest.mark.asyncio
async def test_strict_cold_read_does_not_treat_stat_failure_as_absent(tmp_path, monkeypatch):
    import memory.persona.persistence as persistence
    path = tmp_path / 'persona.json'
    path.write_text('{"master":{"facts":["keep"]}}', encoding='utf-8')
    manager = PersistenceMixin()
    manager._personas = {}
    manager._persona_path = lambda name: str(path)
    def inaccessible(_path):
        raise PermissionError('storage temporarily inaccessible')
    monkeypatch.setattr(persistence, 'os', SimpleNamespace(stat=inaccessible))
    with pytest.raises(PermissionError):
        await manager._aensure_persona_locked('cat', strict_read=True)
    assert path.read_text(encoding='utf-8') == '{"master":{"facts":["keep"]}}'
    assert not manager._personas

@pytest.mark.asyncio
@pytest.mark.parametrize('contents', ['{"master": []}', '{"master": {"facts": 4}}'])
async def test_strict_cold_read_rejects_malformed_containers_before_migration(tmp_path, contents):
    path = tmp_path / 'persona.json'
    path.write_text(contents, encoding='utf-8')
    manager = PersistenceMixin()
    manager._personas = {}
    manager._persona_path = lambda name: str(path)
    manager._async_migrate_from_settings = AsyncMock()
    manager._async_sync_character_card = AsyncMock(return_value=False)
    manager.asave_persona = AsyncMock()
    with pytest.raises(ValueError):
        await manager._aensure_persona_locked('cat', strict_read=True)
    manager._async_migrate_from_settings.assert_not_awaited()
    manager._async_sync_character_card.assert_not_awaited()
    manager.asave_persona.assert_not_awaited()
    assert path.read_text(encoding='utf-8') == contents
