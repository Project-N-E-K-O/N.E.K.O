"""Queued card switches must finalize the character actually replaced."""

import asyncio
import copy
import json
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_routers.characters_router import crud


async def _wait_for_test_start(started: asyncio.Event, task: asyncio.Task, timeout: float = 1.0):
    try:
        await asyncio.wait_for(started.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        if task.done():
            await task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        raise


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
        assert not lock.locked(), 'a document read must not acquire the long lifecycle lock'
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
async def test_readers_return_committed_identity_while_lifecycle_mutation_is_suspended(monkeypatch):
    page_config = importlib.import_module('main_routers.config_router.page_config')
    ws_router = importlib.import_module('main_routers.websocket_router')
    lock = asyncio.Lock()
    # Simulate a saved, provisional B while a rename/delete waits on its memory service.
    manager = SimpleNamespace(
        aload_characters=AsyncMock(return_value={'当前猫娘': 'B'}),
        aget_character_data=AsyncMock(return_value=('master', 'B', {}, {'C': {}, 'B': {}}, {}, {}, {}, {}, {})),
    )
    monkeypatch.setattr(crud, 'character_config_mutation_lock', lock)
    monkeypatch.setattr(crud, '_catgirl_switch_revision', 7654)
    monkeypatch.setattr(crud, '_catgirl_switch_revision_target', 'C')
    for mod in (crud, page_config, ws_router):
        monkeypatch.setattr(mod, 'get_config_manager', lambda: manager)
    monkeypatch.setattr(ws_router, 'get_session_manager', lambda: {'B': object(), 'C': object()})
    async def model(name, _item):
        return SimpleNamespace(body=json.dumps({'model_info': {'path': '/' + name}}).encode())
    monkeypatch.setattr(page_config, 'get_current_live2d_model', model)
    socket, sent = AsyncMock(), asyncio.Event()
    socket.send_text.side_effect = lambda _frame: sent.set()
    async with lock:
        redirect = asyncio.create_task(ws_router.websocket_endpoint(socket, 'retired'))
        current, page, preview, _ = await asyncio.wait_for(asyncio.gather(
            crud.get_current_catgirl(), page_config.get_page_config(SimpleNamespace(headers={})),
            page_config.get_page_config(SimpleNamespace(headers={}), lanlan_name='B'), sent.wait(),
        ), timeout=1)
    await redirect  # normal close includes a post-send pause, not a snapshot delay
    assert json.loads(current.body) == {'current_catgirl': 'C', 'switch_revision': 7654}
    assert (page['lanlan_name'], page['switch_revision'], page['model_path']) == ('C', 7654, '/C')
    assert (preview['lanlan_name'], preview['switch_revision'], preview['model_path']) == ('B', 0, '/B')
    frame = json.loads(socket.send_text.await_args.args[0])
    assert (frame['new_catgirl'], frame['switch_revision']) == ('C', 7654)
    assert crud._catgirl_switch_revision_target == 'C'


@pytest.mark.asyncio
async def test_slow_read_cannot_republish_superseded_role(monkeypatch):
    lock = asyncio.Lock()
    monkeypatch.setattr(crud, 'character_config_mutation_lock', lock)
    monkeypatch.setattr(crud, '_catgirl_switch_revision', 71)
    monkeypatch.setattr(crud, '_catgirl_switch_revision_target', 'A')
    started, finish = asyncio.Event(), asyncio.Event()
    async def read():
        started.set()
        await finish.wait()
        return {'当前猫娘': 'A'}
    in_flight = asyncio.create_task(crud.current_catgirl_switch_snapshot(SimpleNamespace(aload_characters=read)))
    await _wait_for_test_start(started, in_flight)
    async with lock:
        b_revision = crud._advance_current_catgirl_switch_revision('B')
    finish.set()
    assert await in_flight == ('B', b_revision)
    assert (crud._catgirl_switch_revision_target, crud._catgirl_switch_revision) == ('B', b_revision)


@pytest.mark.asyncio
@pytest.mark.parametrize('reader_starts_locked', [True, False])
async def test_aborted_lifecycle_snapshot_cannot_publish_a_provisional_role(monkeypatch, reader_starts_locked):
    # Use the production lock: a rolled-back mutation has no switch revision.
    from utils.character_memory import character_config_mutation_lock
    lock = type(character_config_mutation_lock)()
    monkeypatch.setattr(crud, 'character_config_mutation_lock', lock)
    monkeypatch.setattr(crud, '_catgirl_switch_revision', 71)
    monkeypatch.setattr(crud, '_catgirl_switch_revision_target', 'A')
    started, snapshot, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def read():
        started.set()
        await snapshot.wait()
        await finish.wait()
        return {'当前猫娘': 'B'}  # captured while tentative; disk has since rolled back
    if reader_starts_locked:
        await lock.acquire()
    job = asyncio.create_task(crud.read_current_catgirl_snapshot(read, lambda data: data['当前猫娘']))
    await _wait_for_test_start(started, job)
    if not reader_starts_locked:
        await lock.acquire()
    snapshot.set()
    await asyncio.sleep(0)
    lock.release()
    finish.set()
    _, name, revision = await job
    assert (name, revision) == ('A', 71)
    assert (crud._catgirl_switch_revision_target, crud._catgirl_switch_revision) == ('A', 71)


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_affected_page_finishes_same_read_after_rename_settles(monkeypatch, committed):
    page_config = importlib.import_module('main_routers.config_router.page_config')
    from utils.character_memory import character_config_mutation_lock
    lock = type(character_config_mutation_lock)()
    state = {'name': 'B'}
    two_reads, unrelated_acquired, release_unrelated = (asyncio.Event() for _ in range(3))
    reads = 0

    async def character_data():
        nonlocal reads
        reads += 1
        if reads == 2:
            two_reads.set()
        active = state['name']
        return ('master', active, {}, {active: {}}, {}, {}, {}, {}, {})

    async def unrelated_mutation():
        async with lock:
            unrelated_acquired.set()
            await release_unrelated.wait()

    manager = SimpleNamespace(aget_character_data=character_data)
    monkeypatch.setattr(crud, 'character_config_mutation_lock', lock)
    monkeypatch.setattr(crud, '_catgirl_switch_revision', 71)
    monkeypatch.setattr(crud, '_catgirl_switch_revision_target', 'A')
    monkeypatch.setattr(crud, 'get_config_manager', lambda: manager)
    monkeypatch.setattr(page_config, 'get_config_manager', lambda: manager)
    async def model(name, _item):
        return SimpleNamespace(body=json.dumps({'model_info': {'path': '/' + name}}).encode())
    monkeypatch.setattr(page_config, 'get_current_live2d_model', model)
    async with lock:
        affected = asyncio.create_task(page_config.get_page_config(SimpleNamespace(headers={})))
        await asyncio.wait_for(two_reads.wait(), timeout=1)
        assert not affected.done(), 'a provisional rename must not end the original startup read'
        current = await crud.get_current_catgirl()
        assert json.loads(current.body) == {'current_catgirl': 'A', 'switch_revision': 71}
        preview = await page_config.get_page_config(SimpleNamespace(headers={}), lanlan_name='B')
        assert (preview['switch_revision'], preview['model_path']) == (0, '/B')
        if committed:
            expected = 'B', crud._advance_current_catgirl_switch_revision('B'), '/B'
        else:
            state['name'] = 'A'  # Rollback publishes no new revision or broadcast.
            expected = 'A', 71, '/A'
        unrelated = asyncio.create_task(unrelated_mutation())
        await asyncio.sleep(0)
    try:
        await asyncio.wait_for(unrelated_acquired.wait(), timeout=1)
        page = await asyncio.wait_for(affected, timeout=1)
        assert page['success'] is True
        assert (page['lanlan_name'], page['switch_revision'], page['model_path']) == expected
    finally:
        release_unrelated.set()
        await unrelated


@pytest.mark.asyncio
async def test_page_snapshot_rereads_config_after_revision_changes_during_read(monkeypatch):
    page_config = importlib.import_module('main_routers.config_router.page_config')
    lock = asyncio.Lock()
    first_started, release_first = asyncio.Event(), asyncio.Event()
    reads = 0

    def payload(path):
        role = {
            'model_type': 'live3d',
            'live3d_sub_type': 'mmd',
            '_reserved': {'avatar': {'model_type': 'live3d', 'live3d_sub_type': 'mmd', 'mmd': {'model_path': path}}},
        }
        return ('master', 'B', {}, {'B': role}, {}, {}, {}, {}, {})

    async def character_data():
        nonlocal reads
        reads += 1
        if reads == 1:
            first_started.set()
            await release_first.wait()
            return payload('/old-B.pmx')
        return payload('/new-B.pmx')

    manager = SimpleNamespace(aget_character_data=character_data)
    monkeypatch.setattr(crud, 'character_config_mutation_lock', lock)
    monkeypatch.setattr(crud, '_catgirl_switch_revision', 71)
    monkeypatch.setattr(crud, '_catgirl_switch_revision_target', 'A')
    monkeypatch.setattr(page_config, 'get_config_manager', lambda: manager)
    monkeypatch.setattr(page_config, '_resolve_mmd_path', lambda path, *_args: path)

    job = asyncio.create_task(page_config.get_page_config(SimpleNamespace(headers={})))
    await _wait_for_test_start(first_started, job)
    async with lock:
        revision = crud._advance_current_catgirl_switch_revision('B')
        release_first.set()
        await asyncio.sleep(0)
    page = await job

    assert reads >= 2
    assert (page['lanlan_name'], page['switch_revision'], page['model_path']) == ('B', revision, '/new-B.pmx')


@pytest.mark.asyncio
async def test_snapshot_settlement_does_not_wait_for_a_later_unrelated_writer(monkeypatch):
    from utils.character_memory import character_config_mutation_lock

    lock = type(character_config_mutation_lock)()
    monkeypatch.setattr(crud, 'character_config_mutation_lock', lock)

    await lock.acquire()
    observed_generation = lock.generation
    lock.release()
    await lock.acquire()  # unrelated writer acquired after the observed mutation settled
    try:
        await asyncio.wait_for(
            crud.wait_for_current_catgirl_snapshot_settlement(observed_generation),
            timeout=0.1,
        )
    finally:
        lock.release()


@pytest.mark.asyncio
async def test_current_page_does_not_stamp_default_model_when_second_read_loses_character(monkeypatch):
    page_config = importlib.import_module('main_routers.config_router.page_config')
    state = {'name': 'A'}

    async def character_data():
        active = state['name']
        return ('master', active, {}, {active: {'model_type': 'live2d'}}, {}, {}, {}, {}, {})

    manager = SimpleNamespace(aget_character_data=character_data)
    monkeypatch.setattr(crud, 'character_config_mutation_lock', asyncio.Lock())
    monkeypatch.setattr(crud, '_catgirl_switch_revision', 71)
    monkeypatch.setattr(crud, '_catgirl_switch_revision_target', 'A')
    monkeypatch.setattr(page_config, 'get_config_manager', lambda: manager)

    async def fallback_model(_name, _item):
        state['name'] = 'B'
        return SimpleNamespace(body=json.dumps({
            'model_info': {'path': '/default.model3.json', 'is_fallback': True},
        }).encode())

    monkeypatch.setattr(page_config, 'get_current_live2d_model', fallback_model)
    page = await page_config.get_page_config(SimpleNamespace(headers={}))

    assert page['success'] is False
    assert page['lanlan_name'] == 'A'
    assert page['switch_revision'] == 71


@pytest.mark.asyncio
async def test_synced_delete_notifies_fallback_even_when_cancelled_after_commit(monkeypatch):
    uid = 'a' * 32
    chars = {'当前猫娘': 'A', '猫娘': {'A': {'_reserved': {'character_uid': uid}}, 'B': {}}}
    websocket = AsyncMock()
    manager = SimpleNamespace(aload_characters=AsyncMock(side_effect=lambda: copy.deepcopy(chars)))

    async def save(updated):
        chars.clear()
        chars.update(copy.deepcopy(updated))

    async def committed_delete(name):
        chars['猫娘'].pop(name, None)
        observer = crud._delete_commit_observer.get()
        if observer is not None:
            observer()
        raise asyncio.CancelledError

    manager.asave_characters = save
    monkeypatch.setattr(crud, 'get_config_manager', lambda: manager)
    monkeypatch.setattr(crud, 'get_session_manager', lambda: {'A': SimpleNamespace(websocket=websocket)})
    monkeypatch.setattr(crud, 'get_switch_current_catgirl_fast', lambda: AsyncMock())
    monkeypatch.setattr(crud, 'force_disable_agent_for_character_switch', AsyncMock())
    monkeypatch.setattr(crud, 'is_character_lifecycle_locked', lambda _: False)
    monkeypatch.setattr(crud._chat_sync_host, 'conversation_busy', lambda _: False)
    monkeypatch.setattr(crud, '_delete_catgirl_by_name_serialized', committed_delete)

    with pytest.raises(asyncio.CancelledError):
        await crud._delete_synced_character(uid, AsyncMock())

    assert chars['当前猫娘'] == 'B'
    assert 'A' not in chars['猫娘']
    websocket.send_text.assert_awaited_once()
    frame = json.loads(websocket.send_text.await_args.args[0])
    assert frame['new_catgirl'] == 'B'
    assert frame['switch_revision'] == crud._catgirl_switch_revision
    assert crud._catgirl_switch_revision_target == 'B'


@pytest.mark.asyncio
async def test_synced_delete_rollback_compensates_published_fallback_revision(monkeypatch):
    uid = 'a' * 32
    chars = {'当前猫娘': 'A', '猫娘': {'A': {'_reserved': {'character_uid': uid}}, 'B': {}}}
    websocket = AsyncMock()
    manager = SimpleNamespace(aload_characters=AsyncMock(side_effect=lambda: copy.deepcopy(chars)))
    observed = {}

    async def save(updated):
        chars.clear()
        chars.update(copy.deepcopy(updated))

    async def rolled_back_delete(_name):
        observer = crud._delete_commit_observer.get()
        assert observer is not None
        rollback = observer()
        observed['fallback_revision'] = crud._catgirl_switch_revision
        assert callable(rollback)
        # The inner transaction restored its own snapshot. That snapshot was
        # taken after the sync layer had tentatively made B current, so A exists
        # again but the outer layer must still restore current=A.
        rollback()
        return {'success': False}

    manager.asave_characters = save
    monkeypatch.setattr(crud, '_catgirl_switch_revision', 70)
    monkeypatch.setattr(crud, '_catgirl_switch_revision_target', 'A')
    monkeypatch.setattr(crud, 'get_config_manager', lambda: manager)
    monkeypatch.setattr(crud, 'get_session_manager', lambda: {'A': SimpleNamespace(websocket=websocket)})
    monkeypatch.setattr(crud, 'get_switch_current_catgirl_fast', lambda: AsyncMock())
    monkeypatch.setattr(crud, 'force_disable_agent_for_character_switch', AsyncMock())
    monkeypatch.setattr(crud, 'is_character_lifecycle_locked', lambda _: False)
    monkeypatch.setattr(crud._chat_sync_host, 'conversation_busy', lambda _: False)
    monkeypatch.setattr(crud, '_delete_catgirl_by_name_serialized', rolled_back_delete)

    assert await crud._delete_synced_character(uid, AsyncMock()) is False

    assert chars['当前猫娘'] == 'A'
    assert 'A' in chars['猫娘']
    websocket.send_text.assert_awaited_once()
    frame = json.loads(websocket.send_text.await_args.args[0])
    assert frame['old_catgirl'] == 'B'
    assert frame['new_catgirl'] == 'A'
    assert frame['switch_revision'] > observed['fallback_revision']
    assert frame['switch_revision'] == crud._catgirl_switch_revision
    assert crud._catgirl_switch_revision_target == 'A'


@pytest.mark.asyncio
async def test_synced_current_deletion_stamps_the_fallback_notification(monkeypatch):
    uid = 'a' * 32
    chars = {'当前猫娘': 'A', '猫娘': {'A': {'_reserved': {'character_uid': uid}}, 'B': {}}}
    websocket = AsyncMock()
    sessions = {'A': SimpleNamespace(websocket=websocket)}
    manager = SimpleNamespace(aload_characters=AsyncMock(side_effect=lambda: copy.deepcopy(chars)))
    async def save(updated):
        chars.update(copy.deepcopy(updated))
    manager.asave_characters = save
    monkeypatch.setattr(crud, 'get_config_manager', lambda: manager)
    monkeypatch.setattr(crud, 'get_session_manager', lambda: sessions)
    monkeypatch.setattr(crud, 'get_switch_current_catgirl_fast', lambda: AsyncMock())
    monkeypatch.setattr(crud, 'force_disable_agent_for_character_switch', AsyncMock())
    monkeypatch.setattr(crud, 'is_character_lifecycle_locked', lambda _: False)
    monkeypatch.setattr(crud._chat_sync_host, 'conversation_busy', lambda _: False)
    monkeypatch.setattr(crud, '_delete_catgirl_by_name_serialized', AsyncMock(return_value={'success': True}))
    before = json.loads((await crud.get_current_catgirl()).body)
    assert await crud._delete_synced_character(uid, AsyncMock())
    websocket.send_text.assert_awaited_once()
    frame = json.loads(websocket.send_text.await_args.args[0])
    after = json.loads((await crud.get_current_catgirl()).body)
    assert before['switch_revision'] < frame['switch_revision'] == after['switch_revision']
    assert frame['new_catgirl'] == after['current_catgirl'] == 'B'


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
