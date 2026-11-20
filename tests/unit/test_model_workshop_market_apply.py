"""Connected selections use the actual save API, preserving other role data."""

import asyncio
import copy
from unittest.mock import AsyncMock

import pytest

from main_routers import shared_state
from main_routers.model_market_router import _public_result
from services.model_workshop.application import apply_installed
from services.model_workshop.market import Launches
from services.model_workshop.paths import WorkshopError
from tests.model_workshop_main_runtime import main_character_runtime
from tests.unit.test_model_workshop_apply_runtime import application as application, NAME, OTHER, no_network as no_network
from tests.unit.test_model_workshop_install import bundle_path, install
from tests.unit.test_model_workshop_migration import owner as owner
from utils.config_manager import get_reserved
from utils.character_memory import character_config_mutation_lock


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['live2d', 'vrm', 'pmx', 'pmd'])
async def test_installed_selection_saves_without_manual_button(application, owner, no_network, tmp_path, kind):
    _, config = application
    async with main_character_runtime(config, {NAME: 'idle', OTHER: 'active'}) as harness:
        shared_state.init_shared_state(role_state=harness.module.role_state, config_manager=config,
                                       steamworks=None, templates=None,
                                       init_one_catgirl=harness.module.init_one_catgirl)
        before = copy.deepcopy(config.load_characters()['猫娘'])
        launch = Launches().create(config)
        package, _ = bundle_path(tmp_path, kind)
        launch.result = _public_result(config, install(owner, package)['result'])
        launch.phase = 'installed'
        result = await apply_installed(config, launch)
        assert result['phase'] == 'saved'
        saved = config.load_characters()['猫娘']
        assert saved[OTHER] == before[OTHER]
        assert get_reserved(saved[NAME], 'character_origin') == get_reserved(before[NAME], 'character_origin')
        assert get_reserved(saved[NAME], 'avatar', 'asset_source') == 'local_imported'
        assert get_reserved(saved[NAME], 'avatar') != get_reserved(before[NAME], 'avatar')
        with pytest.raises(WorkshopError, match='not_installed'):
            await apply_installed(config, launch)


@pytest.mark.asyncio
async def test_changed_role_file_is_rejected_inside_real_save(application, owner, no_network, tmp_path):
    _, config = application
    async with main_character_runtime(config, {NAME: 'idle', OTHER: 'idle'}) as harness:
        shared_state.init_shared_state(role_state=harness.module.role_state, config_manager=config,
                                       steamworks=None, templates=None,
                                       init_one_catgirl=harness.module.init_one_catgirl)
        launch = Launches().create(config)
        package, _ = bundle_path(tmp_path)
        launch.result = _public_result(config, install(owner, package)['result'])
        launch.phase = 'installed'
        characters = config.load_characters()
        config.save_characters(characters)  # Also catches switch-away-and-back.
        before = config.get_runtime_config_path('characters.json').read_bytes()
        with pytest.raises(WorkshopError, match='target_changed'):
            await apply_installed(config, launch)
        assert config.get_runtime_config_path('characters.json').read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['voice', 'cancel'])
async def test_busy_or_cancelled_application_keeps_original_binding(application, owner, no_network, tmp_path, reason):
    _, config = application
    async with main_character_runtime(config, {NAME: 'active', OTHER: 'idle'}, input_mode='audio' if reason == 'voice' else 'text') as harness:
        shared_state.init_shared_state(role_state=harness.module.role_state, config_manager=config,
                                       steamworks=None, templates=None,
                                       init_one_catgirl=harness.module.init_one_catgirl)
        launch = Launches().create(config)
        package, _ = bundle_path(tmp_path)
        launch.result = _public_result(config, install(owner, package)['result'])
        launch.phase = 'installed'
        if reason == 'cancel': launch.cancel.set()
        before = config.get_runtime_config_path('characters.json').read_bytes()
        with pytest.raises(WorkshopError, match='voice_busy' if reason == 'voice' else 'cancelled'):
            await apply_installed(config, launch)
        assert config.get_runtime_config_path('characters.json').read_bytes() == before


@pytest.mark.asyncio
async def test_runtime_failure_does_not_report_a_persisted_binding_as_rolled_back(application, owner, no_network, tmp_path):
    _, config = application
    async with main_character_runtime(config, {NAME: 'idle', OTHER: 'idle'}) as harness:
        refresh = AsyncMock(side_effect=RuntimeError('runtime refresh unavailable'))
        shared_state.init_shared_state(role_state=harness.module.role_state, config_manager=config,
                                       steamworks=None, templates=None, init_one_catgirl=refresh)
        launch = Launches().create(config)
        package, _ = bundle_path(tmp_path)
        launch.result = _public_result(config, install(owner, package)['result'])
        launch.phase = 'installed'
        result = await apply_installed(config, launch)
        assert result['phase'] == 'saved' and result['error_code'] == 'model_market_refresh_failed'
        avatar = get_reserved(config.load_characters()['猫娘'][NAME], 'avatar')
        assert avatar['asset_source'] == 'local_imported'
        assert launch.result['name'] in avatar['live2d']['model_path']
        refresh.assert_awaited_once_with(NAME, is_new=False)


@pytest.mark.asyncio
async def test_switch_while_waiting_for_save_lock_cannot_race_guard(application, owner, no_network, tmp_path, monkeypatch):
    from main_routers.characters_router import live2d_models as models

    _, config = application
    async with main_character_runtime(config, {NAME: 'idle', OTHER: 'idle'}) as harness:
        shared_state.init_shared_state(role_state=harness.module.role_state, config_manager=config,
                                       steamworks=None, templates=None,
                                       init_one_catgirl=harness.module.init_one_catgirl)
        launch = Launches().create(config)
        package, _ = bundle_path(tmp_path, 'vrm')
        launch.result = _public_result(config, install(owner, package)['result'])
        launch.phase = 'installed'
        checked = asyncio.Event()
        original = models._drain_sync

        async def drained(call):
            value = await original(call)
            checked.set()  # The actual resource preflight finished, before the save lock.
            return value

        monkeypatch.setattr(models, '_drain_sync', drained)
        async with character_config_mutation_lock:
            applying = asyncio.create_task(apply_installed(config, launch))
            await asyncio.wait_for(checked.wait(), 5)
            characters = config.load_characters()
            characters['当前猫娘'] = OTHER
            config.save_characters(characters)
            characters['当前猫娘'] = NAME
            config.save_characters(characters)
            before = config.get_runtime_config_path('characters.json').read_bytes()
        with pytest.raises(WorkshopError, match='target_changed'):
            await applying
        assert config.get_runtime_config_path('characters.json').read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['live2d', 'vrm', 'pmx', 'pmd'])
async def test_native_apply_http_reuses_real_save_and_duplicate_is_read_only(application, owner, no_network, tmp_path, monkeypatch, kind):
    import httpx
    from fastapi import FastAPI
    from main_routers import model_market_router as api
    from main_routers.system_router import _shared
    from services.model_workshop.download import ConnectedDownloads

    _, config = application
    async with main_character_runtime(config, {NAME: 'idle', OTHER: 'active'}) as harness:
        shared_state.init_shared_state(role_state=harness.module.role_state, config_manager=config,
                                       steamworks=None, templates=None, init_one_catgirl=harness.module.init_one_catgirl)
        driver = ConnectedDownloads()
        launch = driver.launches.create(config)
        package, _ = bundle_path(tmp_path, kind)
        launch.result = _public_result(config, install(owner, package)['result'])
        launch.phase = 'installed'
        monkeypatch.setattr(api, 'connected_downloads', driver)
        monkeypatch.setattr(_shared, 'AUTOSTART_CSRF_TOKEN', 'native-apply-only')
        app = FastAPI(); app.include_router(api.router)
        url = '/api/model-market/launch/' + launch.request_id + '/apply'
        headers = {'Origin': 'http://127.0.0.1:48911', 'X-CSRF-Token': 'native-apply-only'}
        before = config.get_runtime_config_path('characters.json').read_bytes()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app, client=('127.0.0.1', 40000)), base_url='http://127.0.0.1:48911') as client:
            assert (await client.post(url, json={})).status_code == 403
            assert (await client.post(url, json={'role': OTHER}, headers=headers)).status_code == 400
            assert config.get_runtime_config_path('characters.json').read_bytes() == before
            # Both accepted callers await one owner; neither can cause a second save.
            first, second = await asyncio.gather(*(client.post(url, json={}, headers=headers) for _ in range(2)))
            assert first.status_code == second.status_code == 200
            assert first.json()['phase'] == second.json()['phase'] == 'saved'
            saved = config.get_runtime_config_path('characters.json').read_bytes()
            assert saved != before
            assert (await client.post(url, json={}, headers=headers)).json()['phase'] == 'saved'
            assert config.get_runtime_config_path('characters.json').read_bytes() == saved
            assert first.headers['cache-control'] == 'no-store'
        await driver.close()
