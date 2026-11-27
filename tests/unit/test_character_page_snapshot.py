"""Focused page-config / renderer snapshot ordering regressions."""

import asyncio
import copy
import importlib
import json
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
async def test_revision_zero_snapshot_crossing_mutation_never_publishes_provisional_name(monkeypatch):
    lock = type(crud.character_config_mutation_lock)()
    monkeypatch.setattr(crud, 'character_config_mutation_lock', lock)
    monkeypatch.setattr(crud, '_catgirl_switch_revision', 0)
    monkeypatch.setattr(crud, '_catgirl_switch_revision_target', '')

    await lock.acquire()
    try:
        data, name, revision, generation = await crud.read_current_catgirl_snapshot(
            AsyncMock(return_value=('payload', 'Provisional')), lambda value: value[1],
        )
    finally:
        lock.release()

    assert data is None
    assert (name, revision) == ('', 0)
    assert generation == 0
    assert crud._catgirl_switch_revision == 0
    assert crud._catgirl_switch_revision_target == ''


@pytest.mark.asyncio
async def test_snapshot_reports_generation_of_writer_that_caused_final_inconsistent_read(monkeypatch):
    lock = type(crud.character_config_mutation_lock)()
    monkeypatch.setattr(crud, 'character_config_mutation_lock', lock)
    monkeypatch.setattr(crud, '_catgirl_switch_revision', 71)
    monkeypatch.setattr(crud, '_catgirl_switch_revision_target', 'A')
    reads = 0

    async def read():
        nonlocal reads
        reads += 1
        if reads == 1:
            await lock.acquire()
            crud._advance_current_catgirl_switch_revision('B')
            lock.release()  # generation 0 -> 1
            return ('payload', 'B')
        await lock.acquire()  # a later writer now owns generation 1
        return ('payload', 'C')

    try:
        data, name, revision, generation = await crud.read_current_catgirl_snapshot(
            read, lambda value: value[1],
        )
        assert data == ('payload', 'C')
        assert name == 'B'
        assert revision == crud._catgirl_switch_revision > 71
        assert generation == 1
        entered_wait = asyncio.Event()
        original_wait_for_release = lock.wait_for_release

        async def observed_wait_for_release(expected_generation):
            entered_wait.set()
            return await original_wait_for_release(expected_generation)

        monkeypatch.setattr(lock, 'wait_for_release', observed_wait_for_release)
        waiter = asyncio.create_task(crud.wait_for_current_catgirl_snapshot_settlement(generation))
        await asyncio.wait_for(entered_wait.wait(), timeout=1)
        await asyncio.sleep(0)
        assert waiter.done() is False
    finally:
        if lock.locked():
            lock.release()
    await asyncio.wait_for(waiter, timeout=1)


@pytest.mark.asyncio
async def test_page_config_only_model_boot_waits_for_snapshot_settlement_and_failure_keeps_defaults(monkeypatch):
    page_config = importlib.import_module('main_routers.config_router.page_config')
    manager = SimpleNamespace(aget_character_data=AsyncMock())
    snapshot = AsyncMock(return_value=(None, 'A', 0, 7))
    settlement = AsyncMock()
    monkeypatch.setattr(page_config, 'get_config_manager', lambda: manager)
    monkeypatch.setattr(page_config, 'read_current_catgirl_snapshot', snapshot)
    monkeypatch.setattr(page_config, 'wait_for_current_catgirl_snapshot_settlement', settlement)

    fast = await page_config.get_page_config(SimpleNamespace(headers={}))
    settlement.assert_not_awaited()
    assert fast['success'] is False
    assert fast['lanlan_name'] == 'A'
    assert fast['switch_revision'] == 0
    assert fast['autostart_csrf_token']
    assert fast['model_path'] == '' and fast['model_type'] == ''
    assert fast['master_name'] == '' and fast['master_display_name'] == ''

    snapshot.reset_mock()
    settlement.reset_mock()
    boot = await page_config.get_page_config(SimpleNamespace(headers={}), model_boot=True)
    assert boot['success'] is False
    settlement.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_page_config_returns_committed_identity_while_lifecycle_mutation_is_suspended(monkeypatch):
    page_config = importlib.import_module('main_routers.config_router.page_config')
    lock = asyncio.Lock()
    # Simulate a saved, provisional B while a rename/delete waits on its memory service.
    manager = SimpleNamespace(
        aload_characters=AsyncMock(return_value={'当前猫娘': 'B'}),
        aget_character_data=AsyncMock(return_value=('master', 'B', {}, {'C': {}, 'B': {}}, {}, {}, {}, {}, {})),
    )
    monkeypatch.setattr(crud, 'character_config_mutation_lock', lock)
    monkeypatch.setattr(crud, '_catgirl_switch_revision', 7654)
    monkeypatch.setattr(crud, '_catgirl_switch_revision_target', 'C')
    monkeypatch.setattr(page_config, 'get_config_manager', lambda: manager)

    async def model(name, _item, _characters):
        return SimpleNamespace(body=json.dumps({'model_info': {'path': '/' + name}}).encode())

    monkeypatch.setattr(page_config, 'resolve_live2d_model_snapshot', model)
    async with lock:
        page, preview = await asyncio.wait_for(asyncio.gather(
            page_config.get_page_config(SimpleNamespace(headers={})),
            page_config.get_page_config(SimpleNamespace(headers={}), lanlan_name='B'),
        ), timeout=1)

    assert (page['lanlan_name'], page['switch_revision'], page['model_path']) == ('C', 7654, '/C')
    assert (preview['lanlan_name'], preview['switch_revision'], preview['model_path']) == ('B', 0, '/B')
    assert crud._catgirl_switch_revision_target == 'C'


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
async def test_page_config_uses_the_live2d_asset_from_its_committed_identity_snapshot(monkeypatch):
    page = importlib.import_module('main_routers.config_router.page_config')
    model = importlib.import_module('main_routers.characters_router.live2d_models')
    workshop = importlib.import_module('main_routers.workshop_router')
    lock = asyncio.Lock()
    monkeypatch.setattr(crud, 'character_config_mutation_lock', lock)
    monkeypatch.setattr(crud, '_catgirl_switch_revision', 71)
    monkeypatch.setattr(crud, '_catgirl_switch_revision_target', 'A')
    paths = {name: f'/static/{name}/{name}.model3.json' for name in ('oldcard', 'newcard')}
    registry = {'A': {'model_type': 'live2d', 'live2d': paths['oldcard']},
                'D': {'model_type': 'live2d', 'live2d': paths['newcard']}}

    async def page_read():
        return ('master', 'A', {}, copy.deepcopy(registry), {}, {}, {}, {}, {})

    async def model_read():
        # An independently read registry may already have committed A -> C
        # and reused the name A for another profile. Both paths are real.
        async with lock:
            registry['C'] = registry.pop('A')
            crud._advance_current_catgirl_switch_revision('C')
            registry['A'] = registry.pop('D')
            crud._advance_current_catgirl_switch_revision('A')
        return {'当前猫娘': 'A', '猫娘': copy.deepcopy(registry)}

    manager = SimpleNamespace(aget_character_data=page_read, aload_characters=model_read)
    monkeypatch.setattr(page, 'get_config_manager', lambda: manager)
    monkeypatch.setattr(model, 'get_config_manager', lambda: manager)
    monkeypatch.setattr(model, 'find_models', lambda: [
        {'name': name, 'path': path, 'source': 'static'} for name, path in paths.items()
    ])
    monkeypatch.setattr(workshop, 'get_subscribed_workshop_items', AsyncMock(return_value={'success': True, 'items': []}))

    result = await page.get_page_config(SimpleNamespace(headers={}))
    assert result['success'] is True
    assert (result['lanlan_name'], result['switch_revision'], result['model_path']) == ('A', 71, paths['oldcard'])
