"""Queued card switches must finalize the character actually replaced."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_routers.characters_router import crud


@pytest.mark.asyncio
async def test_queued_switches_use_latest_outgoing_character(monkeypatch):
    characters = {'当前猫娘': 'A', '猫娘': {'A': {}, 'B': {}, 'C': {}}}
    reads = 0
    queued = asyncio.Event()

    async def load():
        nonlocal reads
        reads += 1
        if reads == 2:
            queued.set()
        return copy.deepcopy(characters)

    async def save(value):
        characters.clear()
        characters.update(copy.deepcopy(value))

    lock = asyncio.Lock()
    disabled = AsyncMock()
    finalized = AsyncMock(return_value=0)
    monkeypatch.setattr(crud, 'character_config_mutation_lock', lock)
    monkeypatch.setattr(crud, 'get_config_manager', lambda: SimpleNamespace(aload_characters=load, asave_characters=save))
    monkeypatch.setattr(crud, 'get_session_manager', lambda: {})
    monkeypatch.setattr(crud, 'get_switch_current_catgirl_fast', lambda: AsyncMock())
    monkeypatch.setattr(crud, 'force_disable_agent_for_character_switch', disabled)
    from utils import external_route_registry
    monkeypatch.setattr(external_route_registry, 'finalize_external_routes_for_character', finalized)
    await lock.acquire()
    tasks = [asyncio.create_task(crud.set_current_catgirl(
        SimpleNamespace(json=AsyncMock(return_value={'catgirl_name': name})),
    )) for name in ('B', 'C')]
    await asyncio.wait_for(queued.wait(), 2)
    lock.release()
    assert await asyncio.gather(*tasks) == [{'success': True}, {'success': True}]
    assert [call.args for call in disabled.await_args_list] == [('B', 'A'), ('C', 'B')]
    assert [call.args for call in finalized.await_args_list] == [('A',), ('B',)]
    assert characters['当前猫娘'] == 'C'


@pytest.mark.asyncio
async def test_switch_rechecks_voice_state_after_waiting_for_lock(monkeypatch):
    from main_logic.omni_realtime_client import OmniRealtimeClient
    characters = {'当前猫娘': 'A', '猫娘': {'A': {}, 'B': {}, 'C': {}}}
    loaded = asyncio.Event()

    async def load():
        loaded.set()
        return copy.deepcopy(characters)

    save = AsyncMock()
    lock = asyncio.Lock()
    monkeypatch.setattr(crud, 'character_config_mutation_lock', lock)
    monkeypatch.setattr(crud, 'get_config_manager', lambda: SimpleNamespace(aload_characters=load, asave_characters=save))
    monkeypatch.setattr(crud, 'get_session_manager', lambda: {
        'B': SimpleNamespace(is_active=True, session=object.__new__(OmniRealtimeClient)),
    })
    await lock.acquire()
    task = asyncio.create_task(crud.set_current_catgirl(
        SimpleNamespace(json=AsyncMock(return_value={'catgirl_name': 'C'})),
    ))
    await asyncio.wait_for(loaded.wait(), 2)
    characters['当前猫娘'] = 'B'
    lock.release()
    response = await task
    assert response.status_code == 400
    assert characters['当前猫娘'] == 'B'
    save.assert_not_awaited()
