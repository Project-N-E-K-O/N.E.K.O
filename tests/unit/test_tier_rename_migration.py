"""The ``emotion`` tier is now ``recognition`` and ``summary`` is ``memory``.

Saved ``core_config.json`` files keep the old slot fields (``emotionModel*``,
``summaryModel*``) and follow values (``follow_emotion``, ``follow_summary``).
They are read under the new names, persisted once at startup, never shown by
GET, dropped on the next save, and the old ``model_type`` strings still work.
"""

import asyncio
import json

import pytest

from utils.config_manager.tier_renames import migrate_renamed_tier_fields

_OLD_SLOTS = {
    'enableCustomApi': True,
    'coreApi': 'qwen',
    'coreApiKey': 'sk-core',
    'assistApi': 'qwen',
    'emotionModelProvider': 'custom',
    'emotionModelUrl': 'http://127.0.0.1:8001/v1',
    'emotionModelId': 'old-recognition-model',
    'emotionModelApiKey': 'sk-old-recognition',
    'summaryModelProvider': 'custom',
    'summaryModelUrl': 'http://127.0.0.1:8002/v1',
    'summaryModelId': 'old-memory-model',
    'summaryModelApiKey': 'sk-old-memory',
    'agentSmallModelProvider': 'follow_emotion',
    'gameSummaryModelProvider': 'follow_summary',
}


@pytest.fixture()
def config_manager(clean_user_data_dir):
    from utils.config_manager import get_config_manager

    manager = get_config_manager('N.E.K.O')
    manager.config_dir.mkdir(parents=True, exist_ok=True)
    return manager


@pytest.fixture()
def core_config_router(monkeypatch):
    from main_routers.config_router import core_config

    async def _noop(*args, **kwargs):
        return None

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, *args, **kwargs):
            return None

    monkeypatch.setattr(core_config, 'get_session_manager', lambda: {})
    monkeypatch.setattr(core_config, 'get_initialize_character_data', lambda: _noop)
    monkeypatch.setattr(core_config, 'ensure_default_yui_voice_for_free_api', _noop)
    monkeypatch.setattr(core_config, '_auto_resolve_provider_urls_for_save', _noop)

    import httpx

    monkeypatch.setattr(httpx, 'AsyncClient', _FakeAsyncClient)
    return core_config


class _FakeRequest:
    def __init__(self, payload):
        from types import SimpleNamespace
        from starlette.datastructures import Headers
        from main_routers.system_router._shared import AUTOSTART_CSRF_TOKEN

        self._payload = payload
        self.method = "POST"
        self.base_url = "http://testserver/"
        self.url = SimpleNamespace(path="/api/config/core_api")
        self.headers = Headers({
            "content-type": "application/json",
            "origin": "http://testserver",
            "x-csrf-token": AUTOSTART_CSRF_TOKEN,
        })

    async def json(self):
        return self._payload


def _write_core_config(manager, config):
    manager.save_json_config('core_config.json', config)
    manager._core_config_cache = None


def _no_old_names(cfg):
    return not any(k.startswith(('emotionModel', 'summaryModel')) for k in cfg) and not any(
        v in ('follow_emotion', 'follow_summary') for v in cfg.values()
    )


@pytest.mark.unit
def test_migration_moves_fields_and_follow_values():
    cfg = dict(_OLD_SLOTS)

    assert migrate_renamed_tier_fields(cfg) is True

    assert _no_old_names(cfg)
    assert cfg['recognitionModelId'] == 'old-recognition-model'
    assert cfg['recognitionModelApiKey'] == 'sk-old-recognition'
    assert cfg['memoryModelUrl'] == 'http://127.0.0.1:8002/v1'
    assert cfg['agentSmallModelProvider'] == 'follow_recognition'
    assert cfg['gameSummaryModelProvider'] == 'follow_memory'
    assert migrate_renamed_tier_fields(cfg) is False


@pytest.mark.unit
def test_a_field_already_under_the_new_name_wins():
    cfg = {'summaryModelId': 'old', 'memoryModelId': 'new'}

    assert migrate_renamed_tier_fields(cfg) is True

    assert cfg == {'memoryModelId': 'new'}


@pytest.mark.unit
def test_old_file_is_read_under_the_new_names(config_manager):
    _write_core_config(config_manager, _OLD_SLOTS)

    recognition = config_manager.get_model_api_config('recognition')
    memory = config_manager.get_model_api_config('memory')

    assert (recognition['model'], recognition['api_key']) == ('old-recognition-model', 'sk-old-recognition')
    assert (memory['model'], memory['api_key']) == ('old-memory-model', 'sk-old-memory')
    # The migrated follow values keep their meaning.
    assert config_manager.get_model_api_config('agent_small')['model'] == 'old-recognition-model'
    assert config_manager.get_model_api_config('game_summary')['model'] == 'old-memory-model'


@pytest.mark.unit
@pytest.mark.parametrize('old, new', [('emotion', 'recognition'), ('summary', 'memory')])
def test_old_model_type_names_still_resolve(config_manager, old, new):
    _write_core_config(config_manager, _OLD_SLOTS)

    assert config_manager.get_model_api_config(old) == config_manager.get_model_api_config(new)


@pytest.mark.unit
def test_startup_persists_the_new_names(config_manager):
    _write_core_config(config_manager, _OLD_SLOTS)

    assert config_manager.migrate_renamed_tiers() is True

    saved = config_manager.load_json_config('core_config.json', {})
    assert _no_old_names(saved)
    assert saved['memoryModelApiKey'] == 'sk-old-memory'
    assert config_manager.migrate_renamed_tiers() is False


@pytest.mark.unit
def test_get_shows_only_the_new_names(config_manager, core_config_router):
    _write_core_config(config_manager, _OLD_SLOTS)

    response = asyncio.run(core_config_router.get_core_config_api())

    assert response['success'] is True
    assert _no_old_names(response)
    assert response['recognitionModelId'] == 'old-recognition-model'
    assert response['agentSmallModelProvider'] == 'follow_recognition'


@pytest.mark.unit
def test_save_keeps_a_masked_key_saved_under_the_old_name(config_manager, core_config_router):
    # The page shows the key masked under the new field; posting the mask back must keep
    # the real key even though the file still stored it as emotionModelApiKey.
    _write_core_config(config_manager, _OLD_SLOTS)
    shown = asyncio.run(core_config_router.get_core_config_api())
    payload = {k: v for k, v in shown.items() if k not in ('success', 'error')}

    response = asyncio.run(core_config_router.update_core_config(_FakeRequest(payload)))

    assert response['success'] is True
    saved = config_manager.load_json_config('core_config.json', {})
    assert _no_old_names(saved)
    assert saved['recognitionModelApiKey'] == 'sk-old-recognition'
    assert saved['memoryModelApiKey'] == 'sk-old-memory'


@pytest.mark.unit
def test_save_from_a_page_opened_before_the_upgrade(config_manager, core_config_router):
    _write_core_config(config_manager, {k: v for k, v in _OLD_SLOTS.items() if not k.startswith('emotion')})

    response = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        'coreApi': 'qwen', 'assistApi': 'qwen', 'enableCustomApi': True,
        'emotionModelProvider': 'custom',
        'emotionModelUrl': 'http://127.0.0.1:9000/v1',
        'emotionModelId': 'typed-on-old-page',
        'emotionModelApiKey': 'sk-typed',
        'agentSmallModelProvider': 'follow_emotion',
    })))

    assert response['success'] is True
    saved = config_manager.load_json_config('core_config.json', {})
    assert _no_old_names(saved)
    assert saved['recognitionModelId'] == 'typed-on-old-page'
    assert saved['recognitionModelApiKey'] == 'sk-typed'
    assert saved['agentSmallModelProvider'] == 'follow_recognition'


@pytest.mark.unit
def test_partial_save_resolves_connectivity_for_slots_saved_under_old_names(
    config_manager, core_config_router, monkeypatch,
):
    # CodeRabbit: a file restored with the old names, then a save that leaves the model
    # slots out. The provider-URL check must still see the slots under the new names.
    _write_core_config(config_manager, dict(_OLD_SLOTS, emotionModelProvider='openai', summaryModelProvider='glm'))
    seen = {}

    async def _capture(core_cfg, *args, **kwargs):
        seen.update(core_cfg)

    monkeypatch.setattr(core_config_router, '_auto_resolve_provider_urls_for_save', _capture)

    response = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        'coreApi': 'qwen', 'assistApi': 'qwen', 'enableCustomApi': True,
    })))

    assert response['success'] is True
    assert seen['recognitionModelProvider'] == 'openai'
    assert seen['memoryModelProvider'] == 'glm'
    assert _no_old_names(seen)


@pytest.mark.unit
def test_a_provider_file_with_old_field_names_is_read_under_the_new_ones(tmp_path, monkeypatch):
    # Greptile: the connectivity probe and the settings page read the raw profile, not
    # the converted one, so the old names must be normalized where the file is loaded.
    import utils.api_config_loader as loader

    path = tmp_path / 'api_providers.json'
    path.write_text(json.dumps({'assist_api_providers': {
        'old': {'summary_model': 'm-big', 'emotion_model': 'm-small'},
        'both': {'memory_model': 'new', 'summary_model': 'old'},
    }}), encoding='utf-8')
    monkeypatch.setattr(loader, '_get_config_file_path', lambda: path)

    raw = loader._load_json_config()['assist_api_providers']

    assert raw['old'] == {'memory_model': 'm-big', 'recognition_model': 'm-small'}
    assert raw['both'] == {'memory_model': 'new'}
    converted = loader._convert_assist_api_profile(raw['old'])
    assert (converted['MEMORY_MODEL'], converted['RECOGNITION_MODEL']) == ('m-big', 'm-small')


@pytest.mark.unit
def test_bundled_provider_profiles_use_the_new_field_names():
    from pathlib import Path

    raw = (Path(__file__).resolve().parents[2] / 'config' / 'api_providers.json').read_text(encoding='utf-8')
    assert '"summary_model"' not in raw and '"emotion_model"' not in raw
    assert json.loads(raw)


@pytest.mark.unit
@pytest.mark.parametrize('model_type', ['recognition', 'emotion'])
def test_model_list_reuses_a_key_saved_under_the_old_name(model_type):
    from main_routers.config_router.core_config import CORE_CONFIG_SECRET_SENTINEL
    from main_routers.config_router.model_catalog import ModelListRequest, _resolve_custom_target

    target = _resolve_custom_target(ModelListRequest(
        url='http://127.0.0.1:8001/v1', api_key=CORE_CONFIG_SECRET_SENTINEL, model_type=model_type,
    ), dict(_OLD_SLOTS))

    assert target['api_key'] == 'sk-old-recognition'


@pytest.mark.unit
def test_production_code_uses_the_new_tier_names():
    # The aliases exist for saved configs and for nekopower/host (left untouched on purpose);
    # new code naming a tier the old way would keep them alive forever.
    import os
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    old_tier = re.compile(
        r"""(?:get_model_api_config\(\s*|model_type\s*(?::\s*str\s*)?=\s*|_MODEL_(?:TIER|SLOT)\s*=\s*)"""
        r"""['"](?:summary|emotion)['"]"""
    )
    pruned = {'tests', 'node_modules', '.venv', '.git', 'dist', 'build', '__pycache__', 'static', 'frontend'}
    offenders = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root).as_posix()
        dirnames[:] = [d for d in dirnames if d not in pruned and f'{rel}/{d}'.lstrip('./') != 'nekopower/host']
        for name in filenames:
            if name.endswith('.py'):
                text = (Path(dirpath) / name).read_text(encoding='utf-8', errors='ignore')
                offenders += [f'{rel}/{name}:{n}' for n, line in enumerate(text.splitlines(), 1) if old_tier.search(line)]
    assert offenders == []
