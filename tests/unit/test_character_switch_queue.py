"""Queued card switches must finalize the character actually replaced."""

import asyncio
import copy
import importlib
import json
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
    results = await asyncio.gather(*tasks)
    assert [result['success'] for result in results] == [True, True]
    assert results[0]['switch_revision'] < results[1]['switch_revision']
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


@pytest.mark.asyncio
async def test_switch_revision_is_identical_in_ws_frame_and_http_response(monkeypatch):
    characters = {'\u5f53\u524d\u732b\u5a18': 'A', '\u732b\u5a18': {'A': {}, 'B': {}}}

    async def load():
        return copy.deepcopy(characters)

    async def save(value):
        characters.clear()
        characters.update(copy.deepcopy(value))

    websocket = SimpleNamespace(send_text=AsyncMock())
    sessions = {'A': SimpleNamespace(is_active=False, session=None, websocket=websocket)}
    monkeypatch.setattr(crud, 'character_config_mutation_lock', asyncio.Lock())
    monkeypatch.setattr(crud, '_catgirl_switch_revision', 0)
    monkeypatch.setattr(crud, 'get_config_manager', lambda: SimpleNamespace(aload_characters=load, asave_characters=save))
    monkeypatch.setattr(crud, 'get_session_manager', lambda: sessions)
    monkeypatch.setattr(crud, 'get_switch_current_catgirl_fast', lambda: AsyncMock())
    monkeypatch.setattr(crud, 'force_disable_agent_for_character_switch', AsyncMock())
    from utils import external_route_registry
    monkeypatch.setattr(external_route_registry, 'finalize_external_routes_for_character', AsyncMock(return_value=0))

    response = await crud.set_current_catgirl(
        SimpleNamespace(json=AsyncMock(return_value={'catgirl_name': 'B'}))
    )

    assert response['success'] is True
    assert isinstance(response['switch_revision'], int) and response['switch_revision'] > 0
    websocket.send_text.assert_awaited_once()
    frame = json.loads(websocket.send_text.await_args.args[0])
    assert frame['type'] == 'catgirl_switched'
    assert frame['new_catgirl'] == 'B'
    assert frame['old_catgirl'] == 'A'
    assert frame['switch_revision'] == response['switch_revision']


@pytest.mark.asyncio
async def test_current_catgirl_get_exposes_current_switch_revision(monkeypatch):
    lock = asyncio.Lock()
    current = {'name': 'C'}

    async def load():
        assert lock.locked(), 'current character and revision must share one lock snapshot'
        return {'\u5f53\u524d\u732b\u5a18': current['name']}

    monkeypatch.setattr(crud, 'character_config_mutation_lock', lock)
    monkeypatch.setattr(crud, '_catgirl_switch_revision', 4321)
    monkeypatch.setattr(crud, '_catgirl_switch_revision_target', 'C')
    monkeypatch.setattr(crud, 'get_config_manager', lambda: SimpleNamespace(aload_characters=load))

    response = await crud.get_current_catgirl()
    payload = json.loads(response.body)
    assert payload == {'current_catgirl': 'C', 'switch_revision': 4321}

    current['name'] = 'D'
    unrelated = json.loads((await crud.get_current_catgirl()).body)
    assert unrelated['current_catgirl'] == 'D'
    assert unrelated['switch_revision'] > 4321
    assert crud._catgirl_switch_revision_target == 'D'


@pytest.mark.asyncio
async def test_missing_socket_redirect_uses_the_confirmed_current_character(monkeypatch):
    ws_router = importlib.import_module('main_routers.websocket_router')
    manager = SimpleNamespace(aload_characters=AsyncMock(return_value={'当前猫娘': 'C'}))
    monkeypatch.setattr(ws_router, 'get_config_manager', lambda: manager)
    monkeypatch.setattr(ws_router, 'get_session_manager', lambda: {'A': object(), 'C': object()})
    monkeypatch.setattr(crud, 'get_config_manager', lambda: manager)
    current = json.loads((await crud.get_current_catgirl()).body)
    socket = AsyncMock()
    await ws_router.websocket_endpoint(socket, 'retired')
    socket.send_text.assert_awaited_once()
    assert json.loads(socket.send_text.await_args.args[0]) == {
        'type': 'catgirl_switched', 'old_catgirl': 'retired',
        'new_catgirl': current['current_catgirl'], 'switch_revision': current['switch_revision'],
    }
