"""Connected selections use the actual save API, preserving other role data."""

import asyncio
import copy
import hashlib
from unittest.mock import AsyncMock

import pytest

from main_routers import shared_state
from main_routers.model_market_application import apply_installed
from main_routers.model_market_router import _public_result
from services.model_workshop.market import Launches
from services.model_workshop.paths import WorkshopError
from tests.model_workshop_main_runtime import main_character_runtime
from tests.unit.test_model_workshop_apply_runtime import application as application, NAME, OTHER, no_network as no_network
from tests.unit.test_model_workshop_install import bundle_path, install
from tests.unit.test_model_workshop_migration import owner as owner
from utils.config_manager import get_reserved
from utils.character_memory import character_config_mutation_lock


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["Avatar%20name.model3.json", "Avatar%FF.model3.json"])
async def test_active_live2d_literal_percent_name_skips_save_and_voice_checks(application, owner, no_network, tmp_path, monkeypatch, entry):
    from tests.unit.test_model_workshop_formats import live2d_files, make_native_bundle
    from utils import config_manager

    _, config = application
    monkeypatch.setattr(config_manager, 'get_config_manager', lambda *args, **kwargs: config)
    files = live2d_files()
    files[entry] = files.pop('Avatar.model3.json')
    path = make_native_bundle(tmp_path, entry=entry, files=files)
    record = install(owner, path)['result']
    async with main_character_runtime(config, {NAME: 'idle', OTHER: 'idle'}) as harness:
        shared_state.init_shared_state(role_state=harness.module.role_state, config_manager=config,
                                       steamworks=None, templates=None, init_one_catgirl=harness.module.init_one_catgirl)
        first = Launches().create(config)
        first.result, first.phase = _public_result(config, record), 'installed'
        await apply_installed(config, first)
        assert get_reserved(config.load_characters()['猫娘'][NAME], 'avatar', 'live2d', 'model_path').endswith('/' + entry)
        second = Launches().create(config)
        second.result, second.phase = _public_result(config, record), 'installed'
        before = config.get_runtime_config_path('characters.json').read_bytes()

        def unexpected():
            raise AssertionError('already-active choice must precede voice checks')

        monkeypatch.setattr(shared_state, 'get_session_manager', unexpected)
        await apply_installed(config, second)
        assert second.phase == 'completed' and second.result['already_active']
        assert config.get_runtime_config_path('characters.json').read_bytes() == before


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
        driver = ConnectedDownloads(apply_installed)
        launch = driver.launches.create(config)
        driver.launches.select(config, launch.request_id, 1, 'a' * 64, 1, '1' * 32)
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
            first, second = await asyncio.gather(*(client.post(url, json={'operation_id': '1' * 32}, headers=headers) for _ in range(2)))
            assert first.status_code == second.status_code == 200
            assert first.json()['phase'] == second.json()['phase'] == 'saved'
            saved = config.get_runtime_config_path('characters.json').read_bytes()
            assert saved != before
            assert (await client.post(url, json={'operation_id': '1' * 32}, headers=headers)).json()['phase'] == 'saved'
            assert config.get_runtime_config_path('characters.json').read_bytes() == saved
            assert first.headers['cache-control'] == 'no-store'
        await driver.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['live2d', 'vrm', 'pmx', 'pmd'])
async def test_one_pairing_saves_a_b_a_and_active_reselection_is_read_only(application, owner, no_network, tmp_path, monkeypatch, kind):
    """Use real installs, HTTP guards, persistence and runtime refresh for every choice."""
    import httpx
    from fastapi import FastAPI
    from main_routers import model_market_router as api
    from main_routers.system_router import _shared
    from services.model_workshop.download import ConnectedDownloads

    _, config = application
    async with main_character_runtime(config, {NAME: 'idle', OTHER: 'idle'}) as harness:
        shared_state.init_shared_state(role_state=harness.module.role_state, config_manager=config,
                                       steamworks=None, templates=None, init_one_catgirl=harness.module.init_one_catgirl)
        driver = ConnectedDownloads(apply_installed)
        connection = driver.launches.create(config)
        entries = []
        for index, format_name in enumerate((kind, 'vrm' if kind != 'vrm' else 'live2d')):
            folder = tmp_path / str(index); folder.mkdir()
            package, _ = bundle_path(folder, format_name)
            entries.append((hashlib.sha256(package.read_bytes()).hexdigest(), package.stat().st_size,
                            _public_result(config, install(owner, package)['result'])))
        monkeypatch.setattr(api, 'connected_downloads', driver)
        monkeypatch.setattr(_shared, 'AUTOSTART_CSRF_TOKEN', 'continuous-save')
        app = FastAPI(); app.include_router(api.router)
        base = '/api/model-market/launch/' + connection.request_id
        headers = {'Origin': 'http://127.0.0.1:48911', 'X-CSRF-Token': 'continuous-save'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app, client=('127.0.0.1', 40000)), base_url='http://127.0.0.1:48911') as client:
            for index, selected in enumerate((0, 1, 0, 0)):
                digest, size, result = entries[selected]
                operation_id = f'{index + 1:032x}'
                row, fresh = driver.launches.select(config, connection.request_id, selected + 1, digest, size, operation_id)
                assert fresh
                row.result, row.phase = result, 'installed'
                before = config.get_runtime_config_path('characters.json').read_bytes()
                response = await client.post(base + '/apply', json={'operation_id': operation_id}, headers=headers)
                assert response.status_code == 200, response.text
                assert response.json()['phase'] == ('completed' if index == 3 else 'saved'), (
                    index, get_reserved(config.load_characters()['猫娘'][NAME], 'avatar'), result['url'])
                assert not response.json()['can_select']
                if index == 3:
                    assert config.get_runtime_config_path('characters.json').read_bytes() == before
                    assert response.json()['result']['already_active']
                # Only the local manager can release the next selection after its own checks.
                assert (await client.post(base + '/ready', json={'operation_id': operation_id})).status_code == 403
                ready = await client.post(base + '/ready', json={'operation_id': operation_id}, headers=headers)
                assert ready.status_code == 200 and ready.json()['can_select']
                if index:
                    stale = {'operation_id': f'{index:032x}'}
                    assert (await client.post(base + '/cancel', json=stale, headers=headers)).status_code == 200
                    assert (await client.post(base + '/ready', json=stale, headers=headers)).status_code == 409
                    assert driver.status(config, connection.request_id)['can_select']
            assert len(driver.launches.items) == 1
        await driver.close()


@pytest.mark.asyncio
async def test_status_during_own_write_and_ready_after_competing_edit(application, owner, no_network, tmp_path, monkeypatch):
    """Reads cannot revoke an in-flight own save; ready cannot absorb a later unrelated write."""
    from services.model_workshop.download import ConnectedDownloads

    _, config = application
    async with main_character_runtime(config, {NAME: 'idle', OTHER: 'idle'}) as harness:
        shared_state.init_shared_state(role_state=harness.module.role_state, config_manager=config,
                                       steamworks=None, templates=None, init_one_catgirl=harness.module.init_one_catgirl)
        driver = ConnectedDownloads(apply_installed); row = driver.launches.create(config)
        package, _ = bundle_path(tmp_path, 'vrm')
        driver.launches.select(config, row.request_id, 1, 'a' * 64, 100, '1' * 32)
        row.result, row.phase = _public_result(config, install(owner, package)['result']), 'installed'
        written, release = asyncio.Event(), asyncio.Event()
        original = config.asave_characters

        async def paused_write(characters):
            await original(characters)
            written.set()
            await release.wait()

        monkeypatch.setattr(config, 'asave_characters', paused_write)
        applying = asyncio.create_task(driver.apply(config, row.request_id, row.operation_id))
        try:
            await asyncio.wait_for(written.wait(), 5)
            observed = driver.status(config, row.request_id)
            assert observed['phase'] == 'applying' and observed['connection_error'] is None
        finally:
            release.set()
        assert (await applying)['phase'] == 'saved'
        config.save_characters(config.load_characters())
        with pytest.raises(WorkshopError, match='target_changed'):
            await driver.ready(config, row.request_id, row.operation_id)
        assert not driver.status(config, row.request_id)['can_select']
        await driver.close()


@pytest.mark.asyncio
async def test_pending_auth_at_save_is_retryable_without_reinstall(application, owner, no_network, tmp_path, monkeypatch):
    from services.model_workshop.download import ConnectedDownloads
    from services.model_workshop import market
    _, config = application
    async with main_character_runtime(config, {NAME: 'idle', OTHER: 'idle'}) as harness:
        shared_state.init_shared_state(role_state=harness.module.role_state, config_manager=config,
            steamworks=None, templates=None, init_one_catgirl=harness.module.init_one_catgirl)
        driver = ConnectedDownloads(apply_installed); row = driver.launches.create(config)
        row, _ = driver.launches.select(config, row.request_id, 1, 'a' * 64, 1, '1' * 32)
        package, _ = bundle_path(tmp_path)
        row.result, row.phase = _public_result(config, install(owner, package)['result']), 'installed'
        before = config.get_runtime_config_path('characters.json').read_bytes()
        original = market._account_witness
        monkeypatch.setattr(market, '_account_witness', lambda: (_ for _ in ()).throw(WorkshopError('market_auth_pending')))
        with pytest.raises(WorkshopError, match='market_auth_pending'):
            await driver.apply(config, row.request_id, row.operation_id)
        assert row.phase == 'installed' and row.connection.error is None
        assert config.get_runtime_config_path('characters.json').read_bytes() == before
        monkeypatch.setattr(market, '_account_witness', original)
        assert (await driver.apply(config, row.request_id, row.operation_id))['phase'] == 'saved'
        await driver.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['live2d', 'vrm', 'pmx', 'pmd'])
@pytest.mark.parametrize('change', ['deleted', 'reinstalled', 'missing_resource'])
async def test_stale_installation_cannot_replace_the_current_avatar(application, owner, no_network, tmp_path, kind, change):
    from services.model_workshop import runtime
    _, config = application
    package, _ = bundle_path(tmp_path, kind)
    record = install(owner, package)['result']
    launch = Launches().create(config)
    launch.result, launch.phase = _public_result(config, record), 'installed'
    if change == 'missing_resource':
        (owner.paths.root / record['files'][-1]['local_path']).unlink()
    else:
        await asyncio.to_thread(runtime.remove_managed_model, config, record)
        if change == 'reinstalled': assert install(owner, package)['phase'] == 'completed'
    before = config.get_runtime_config_path('characters.json').read_bytes()
    async with main_character_runtime(config, {NAME: 'idle', OTHER: 'idle'}) as harness:
        shared_state.init_shared_state(role_state=harness.module.role_state, config_manager=config,
            steamworks=None, templates=None, init_one_catgirl=harness.module.init_one_catgirl)
        with pytest.raises(WorkshopError, match='model_market_not_installed'):
            await apply_installed(config, launch)
    assert config.get_runtime_config_path('characters.json').read_bytes() == before


@pytest.mark.asyncio
async def test_apply_does_not_wait_on_an_active_model_lifecycle(application, owner, no_network, tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    _, config = application
    package, _ = bundle_path(tmp_path)
    record = install(owner, package)['result']
    launch = Launches().create(config)
    launch.result, launch.phase = _public_result(config, record), 'installed'
    entered, release = threading.Event(), threading.Event()
    def holding():
        with owner.installer.lifecycle(record['package_sha256']):
            entered.set(); release.wait(5)
    before = config.get_runtime_config_path('characters.json').read_bytes()
    async with main_character_runtime(config, {NAME: 'idle', OTHER: 'idle'}) as harness:
        shared_state.init_shared_state(role_state=harness.module.role_state, config_manager=config,
            steamworks=None, templates=None, init_one_catgirl=harness.module.init_one_catgirl)
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(holding)
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                with pytest.raises(WorkshopError, match='model_workshop_busy'):
                    await apply_installed(config, launch)
            finally:
                release.set(); await asyncio.to_thread(running.result, 5)
    assert config.get_runtime_config_path('characters.json').read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_model_verification_keeps_event_loop_live_and_role_gate_on_main(
    application, owner, no_network, tmp_path, monkeypatch, cancel
):
    import threading
    import time
    _, config = application
    package, _ = bundle_path(tmp_path, 'vrm')
    launch = Launches().create(config)
    launch.result, launch.phase = _public_result(config, install(owner, package)['result']), 'installed'
    entered, release = threading.Event(), threading.Event()
    checking_threads, role_threads = [], []
    main_thread = threading.get_ident()
    original = owner.installer.registry.status
    def slow_status(row):
        checking_threads.append(threading.get_ident())
        entered.set(); release.wait(2)
        return original(row)
    monkeypatch.setattr(owner.installer.registry, 'status', slow_status)
    async with main_character_runtime(config, {NAME: 'idle', OTHER: 'idle'}) as harness:
        shared_state.init_shared_state(role_state=harness.module.role_state, config_manager=config,
            steamworks=None, templates=None, init_one_catgirl=harness.module.init_one_catgirl)
        original_managers = shared_state.get_session_manager
        def managers():
            role_threads.append(threading.get_ident())
            assert threading.get_ident() == main_thread
            return original_managers()
        monkeypatch.setattr(shared_state, 'get_session_manager', managers)
        before = config.get_runtime_config_path('characters.json').read_bytes()
        started = time.monotonic()
        applying = asyncio.create_task(apply_installed(config, launch))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            await asyncio.sleep(0)
            assert time.monotonic() - started < 1
            assert not applying.done()
            if cancel: launch.cancel.set()
            release.set()
            if cancel:
                with pytest.raises(WorkshopError, match='cancelled'): await applying
                assert config.get_runtime_config_path('characters.json').read_bytes() == before
            else:
                assert (await applying)['phase'] == 'saved'
                assert role_threads == [main_thread]
            assert all(value != main_thread for value in checking_threads)
        finally:
            release.set()
            await asyncio.gather(applying, return_exceptions=True)
