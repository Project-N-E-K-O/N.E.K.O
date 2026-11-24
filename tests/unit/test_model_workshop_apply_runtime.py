"""Managed bindings pass the real save API and Main's actual refresh entry."""

import copy
import hashlib
import json
import socket
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from main_routers import characters_router, live2d_router, mmd_router, shared_state, vrm_router
from services.model_workshop import catalog
from services.model_workshop.runtime import require_url
from services.model_workshop.paths import WorkshopError
from tests.model_workshop_main_runtime import main_character_runtime
from tests.unit.test_model_workshop_install import bundle_path, install, fail_registration
from tests.unit.test_model_workshop_migration import owner as owner
from tests.unit.test_model_workshop_formats import live2d_files
from utils.config_manager import get_reserved

NAME = 'Workshop runtime'
OTHER = 'Other runtime'


@pytest.fixture
def application(owner, monkeypatch, tmp_path):
    config = owner.config
    characters = config.load_characters()
    source = copy.deepcopy(next(iter(characters['猫娘'].values())))
    source['_reserved']['avatar'] = {
        'model_type': 'live2d', 'live2d': {'model_path': '/static/mao_pro/mao_pro.model3.json'},
        'asset_source': 'builtin', 'asset_source_id': '',
    }
    source['_reserved']['character_origin'] = {'source': 'steam_workshop'}
    characters['猫娘'] = {NAME: source, OTHER: copy.deepcopy(source)}
    characters['当前猫娘'] = NAME
    config.save_characters(characters)
    monkeypatch.setattr(config, '_get_project_root', lambda: tmp_path / 'project')
    for kind in ('live2d', 'vrm', 'mmd'):
        getattr(config, 'ensure_' + kind + '_directory')()
    monkeypatch.setattr(shared_state, '_state', shared_state._state.copy())
    from utils import steam_state
    monkeypatch.setattr(steam_state, '_steamworks', None)
    shared_state.init_shared_state(role_state={}, config_manager=config, steamworks=None, templates=None)
    monkeypatch.setattr(catalog, 'get_config_manager', lambda: config)
    for module in (live2d_router, vrm_router, mmd_router):
        monkeypatch.setattr(module, 'get_config_manager', lambda: config)
        monkeypatch.setattr(module, 'get_subscribed_workshop_items', AsyncMock(return_value={'success': True, 'items': []}))
    from utils import frontend_utils
    monkeypatch.setattr(frontend_utils, '_resolve_workshop_search_dir', lambda: None)
    app = FastAPI()
    app.include_router(characters_router.router)
    return app, config


def binding(row):
    kind = row['model_format']
    path = Path(row['local_entrypoint']).relative_to(kind).as_posix()
    return {
        'model_type': 'live2d' if kind == 'live2d' else 'live3d',
        kind: Path(row['target']).name if kind == 'live2d' else '/user_' + kind + '/' + path,
        'asset_source': 'local_imported',
    }


@pytest.fixture
async def no_network(monkeypatch):
    # Fail even if an unrelated background helper catches a connection error.
    # Install inside the running loop; platform event-loop setup is not model I/O.
    attempts = []
    def reject_network(*args, **kwargs):
        attempts.append(True)
        raise AssertionError('model apply attempted a network connection')
    monkeypatch.setattr(socket.socket, 'connect', reject_network)
    yield
    assert attempts == []


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['live2d', 'vrm', 'pmx', 'pmd'])
@pytest.mark.parametrize('state', ['idle', 'active', 'starting'])
async def test_saved_package_refreshes_main_without_disrupting_other_roles(application, owner, no_network, tmp_path, kind, state):
    app, config = application
    # This chain must not start provider/Memory/Monitor connections, even when
    # exercising actual LLMSessionManager construction and Main initialization.
    async with main_character_runtime(config, {NAME: state, OTHER: 'active'}) as harness:
        shared_state.init_shared_state(role_state=harness.module.role_state,
                                       config_manager=config, steamworks=None, templates=None,
                                       init_one_catgirl=harness.module.init_one_catgirl)
        before = harness.module.lanlan_basic_config
        other = copy.deepcopy(config.load_characters()['猫娘'][OTHER])
        package, _ = bundle_path(tmp_path, kind)
        row = install(owner, package)['result']
        # Installing a package alone does not apply it to any runtime role.
        assert harness.module.lanlan_basic_config is before
        assert get_reserved(before[NAME], 'avatar', 'asset_source') == 'builtin'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            response = await client.put('/api/characters/catgirl/l2d/' + NAME, json=binding(row))
        assert response.status_code == 200, response.text
        assert response.json()['applied_runtime'] is True
        assert harness.facade.lanlan_basic_config is harness.module.lanlan_basic_config
        assert harness.module.lanlan_basic_config is not before
        avatar = get_reserved(harness.module.lanlan_basic_config[NAME], 'avatar')
        saved = config.load_characters()['猫娘']
        assert avatar == get_reserved(saved[NAME], 'avatar')
        assert avatar['asset_source'] == 'local_imported'
        assert not avatar.get('asset_source_id')
        assert get_reserved(saved[NAME], 'character_origin', 'source') == 'steam_workshop'
        assert saved[OTHER] == other
        assert set(harness.module.role_state) == {NAME, OTHER}
        for name, original in harness.initial.items():
            role = harness.module.role_state[name]
            assert role is original.role and role.sync_task is original.connector
            assert not role.sync_task.done()
            assert role.sync_message_queue is original.queue and role.websocket_lock is original.lock
            assert role.session_manager.websocket is original.websocket
            assert role.session_manager.user_language == 'ja'
            assert role.session_manager._user_language_explicit is True
            if name == NAME and state == 'idle':
                assert role.session_manager is not original.manager
                assert get_reserved(role.session_manager.lanlan_basic_config[NAME], 'avatar') == avatar
            else:
                assert role.session_manager is original.manager
                assert role.session_manager.session is original.session
        for asset in row['files']:
            assert hashlib.sha256((config.app_docs_dir / asset['local_path']).read_bytes()).hexdigest() == asset['sha256']


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['live2d', 'vrm', 'pmx', 'pmd'])
async def test_staged_binding_waits_for_explicit_runtime_apply(application, owner, no_network, tmp_path, kind):
    app, config = application
    async with main_character_runtime(config, {NAME: 'active', OTHER: 'active'}) as harness:
        shared_state.init_shared_state(role_state=harness.module.role_state, steamworks=None,
                                       templates=None, config_manager=config,
                                       init_one_catgirl=harness.module.init_one_catgirl)
        before = harness.module.lanlan_basic_config
        package, _ = bundle_path(tmp_path, kind)
        row = install(owner, package)['result']
        payload = binding(row)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            response = await client.put('/api/characters/catgirl/l2d/' + NAME,
                                        json={**payload, 'apply_runtime': False})
            assert response.status_code == 200, response.text
            assert response.json()['applied_runtime'] is False
            assert harness.module.lanlan_basic_config is before
            assert get_reserved(config.load_characters()['猫娘'][NAME], 'avatar', 'asset_source') == 'local_imported'
            response = await client.put('/api/characters/catgirl/l2d/' + NAME, json=payload)
        assert response.status_code == 200, response.text
        assert harness.module.lanlan_basic_config is not before
        assert get_reserved(harness.module.lanlan_basic_config[NAME], 'avatar', 'asset_source') == 'local_imported'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['live2d', 'vrm', 'pmx', 'pmd'])
@pytest.mark.parametrize('fault', ['pending', 'unreadable'])
async def test_unavailable_installation_cannot_change_main_or_saved_binding(application, owner, no_network, monkeypatch, tmp_path, kind, fault):
    app, config = application
    async with main_character_runtime(config, {NAME: 'active', OTHER: 'active'}) as harness:
        shared_state.init_shared_state(role_state=harness.module.role_state, steamworks=None,
                                       templates=None, config_manager=config,
                                       init_one_catgirl=harness.module.init_one_catgirl)
        before = harness.module.lanlan_basic_config
        saved = config.load_characters()
        package, _ = bundle_path(tmp_path, kind)
        if fault == 'pending':
            fail_registration(owner, monkeypatch)
        attempt = install(owner, package)
        if fault == 'pending':
            row = json.loads(owner.installer.journal_path(attempt['task_id']).read_text(encoding='utf-8'))['record']
        else:
            row = attempt['result']
            (owner.paths.private / 'installed.json').write_text('{', encoding='utf-8')
        # Real pending/unreadable state must reject before saving or calling Main.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            response = await client.put('/api/characters/catgirl/l2d/' + NAME, json=binding(row))
        assert response.status_code == 409, response.text
        assert response.json()['error_code'].startswith('model_workshop_')
        assert harness.module.lanlan_basic_config is before
        assert config.load_characters() == saved


@pytest.mark.parametrize('outside', [
    '/static/mao_pro/mao_pro.model3.json', '/workshop/123/Avatar.model3.json',
    'https://example.invalid/Avatar.model3.json',
])
def test_unreadable_binding_guard_preserves_existing_reads_and_external_sources(owner, outside):
    (owner.paths.private / 'installed.json').write_text('{', encoding='utf-8')
    # One local URL has different read/write policies; the Monitor's existing
    # read behavior and sources outside local ownership must remain compatible.
    local = '/user_live2d/Ordinary/Avatar.model3.json'
    require_url(owner.config, local)
    require_url(owner.config, outside, for_binding=True)
    with pytest.raises(WorkshopError, match='model_workshop_unavailable'):
        require_url(owner.config, local, for_binding=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('source,model', [
    ('builtin', 'Builtin'), ('steam_workshop', '/workshop/123/Avatar/Avatar.model3.json'),
    ('steam_workshop', 'Avatar'), ('manual_external', 'https://example.invalid/Avatar.model3.json'),
])
async def test_unreadable_local_registry_keeps_unrelated_live2d_binding_sources(application, owner, no_network, monkeypatch, source, model):
    app, config = application
    # A bare builtin name must be resolved by the real catalog before checking
    # ownership; its persisted relative spelling alone resembles a user model.
    for path, raw in live2d_files().items():
        target = config.project_root / 'static/Builtin' / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    from utils import config_manager
    monkeypatch.setattr(config_manager, 'get_config_manager', lambda *_, **__: config)
    # Legacy discovery scans cwd/static, rather than ConfigManager.project_root.
    monkeypatch.chdir(config.project_root)
    payload = {'model_type': 'live2d', 'live2d': model}
    if source == 'steam_workshop':
        payload['item_id'] = '123'
    (owner.paths.private / 'installed.json').write_text('{', encoding='utf-8')
    async with main_character_runtime(config, {NAME: 'active', OTHER: 'active'}) as harness:
        shared_state.init_shared_state(role_state=harness.module.role_state, steamworks=None,
                                       templates=None, config_manager=config,
                                       init_one_catgirl=harness.module.init_one_catgirl)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            response = await client.put('/api/characters/catgirl/l2d/' + NAME, json=payload)
        assert response.status_code == 200, response.text
        avatar = get_reserved(harness.module.lanlan_basic_config[NAME], 'avatar')
        assert avatar['asset_source'] == source
        assert avatar.get('asset_source_id', '') == ('123' if source == 'steam_workshop' else '')


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,spelling', [
    ('live2d', 'encoded'), ('vrm', 'encoded'), ('pmx', 'encoded'),
    ('pmd', 'encoded'), ('live2d', 'relative'),
    ('vrm', 'other-prefix'), ('live2d', 'other-prefix'),
])
async def test_encoded_local_prefix_escape_cannot_bypass_pending_binding(application, owner, no_network, monkeypatch, tmp_path, kind, spelling):
    app, config = application
    async with main_character_runtime(config, {NAME: 'active', OTHER: 'active'}) as harness:
        shared_state.init_shared_state(role_state=harness.module.role_state, steamworks=None,
                                       templates=None, config_manager=config,
                                       init_one_catgirl=harness.module.init_one_catgirl)
        before = harness.module.lanlan_basic_config
        saved = config.load_characters()
        package, _ = bundle_path(tmp_path, kind)
        fail_registration(owner, monkeypatch)
        attempt = install(owner, package)
        row = json.loads(owner.installer.journal_path(attempt['task_id']).read_text(encoding='utf-8'))['record']
        model_kind = row['model_format']
        prefix = '/user_' + model_kind
        tail = Path(row['local_entrypoint']).relative_to(model_kind).as_posix()
        # A browser removes encoded dot segments from URLs. The filesystem
        # mapper must not treat an unsafe local URL as an unrelated source.
        payload = binding(row)
        payload[model_kind] = prefix + '/%2e%2e/' + prefix.lstrip('/') + '/' + tail
        if spelling == 'relative':
            payload[model_kind] = '../' + prefix.lstrip('/') + '/' + tail
        elif spelling == 'other-prefix':
            payload[model_kind] = '/static/vrm/%2e%2e/%2e%2e/' + prefix.lstrip('/') + '/' + tail
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            response = await client.put('/api/characters/catgirl/l2d/' + NAME, json=payload)
        assert response.status_code == 409, response.text
        assert response.json()['error_code'].startswith('model_workshop_')
        assert config.load_characters() == saved
        assert harness.module.lanlan_basic_config is before
