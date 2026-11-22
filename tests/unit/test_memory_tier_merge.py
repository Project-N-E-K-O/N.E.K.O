"""The summary and correction model tiers are one tier (``summary``).

The correction tier was merged into the summary tier: the summary slot always
wins, so an old ``correction*`` slot in ``core_config.json`` is ignored on
read, never returned by GET, and dropped on the next save. A few
latency-bound light jobs moved to the small ``emotion`` tier, and the break
reminder moved to the ``conversation`` tier.
"""

import asyncio
import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_OLD_CORRECTION_SLOT = {
    'correctionModelProvider': 'custom',
    'correctionModelUrl': 'http://127.0.0.1:11434/v1',
    'correctionModelId': 'old-correction-model',
    'correctionModelApiKey': 'sk-old-correction-key',
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
        self._payload = payload

    async def json(self):
        return self._payload


def _write_core_config(manager, config):
    manager.save_json_config('core_config.json', config)
    manager._core_config_cache = None


def _custom_config(**overrides):
    config = {
        'coreApi': 'qwen',
        'coreApiKey': 'sk-core',
        'assistApi': 'qwen',
        'enableCustomApi': True,
        'summaryModelProvider': 'custom',
        'summaryModelUrl': 'http://127.0.0.1:8000/v1',
        'summaryModelId': 'kept-summary-model',
        'summaryModelApiKey': 'sk-summary',
    }
    config.update(_OLD_CORRECTION_SLOT)
    config.update(overrides)
    return config


@pytest.mark.unit
def test_correction_is_no_longer_a_model_type(config_manager):
    with pytest.raises(ValueError, match='Unknown model_type: correction'):
        config_manager.get_model_api_config('correction')


@pytest.mark.unit
def test_old_correction_slot_is_ignored_and_summary_slot_wins(config_manager):
    _write_core_config(config_manager, _custom_config())

    snapshot = config_manager.get_core_config()
    assert not any(key.startswith('CORRECTION_MODEL') for key in snapshot)
    assert not any(key.startswith('correctionModel') for key in snapshot)

    summary = config_manager.get_model_api_config('summary')
    assert summary['model'] == 'kept-summary-model'
    assert summary['base_url'] == 'http://127.0.0.1:8000/v1'
    assert summary['api_key'] == 'sk-summary'


@pytest.mark.unit
def test_only_correction_was_customised_summary_keeps_its_default(config_manager):
    """Summary wins even when only the old correction slot was filled in."""
    _write_core_config(config_manager, {
        'coreApi': 'qwen',
        'coreApiKey': 'sk-core',
        'assistApi': 'qwen',
        'enableCustomApi': True,
        **_OLD_CORRECTION_SLOT,
    })

    summary = config_manager.get_model_api_config('summary')
    assert summary['model'] == 'qwen3.8-flash'
    assert summary['api_key'] != 'sk-old-correction-key'


@pytest.mark.unit
def test_profiles_have_no_correction_model_and_keep_the_cheaper_summary_model():
    from utils.api_config_loader import get_assist_api_profiles

    profiles = get_assist_api_profiles(force_reload=True)
    assert not any('CORRECTION_MODEL' in profile for profile in profiles.values())
    # The two providers whose old correction model differed keep the summary one.
    assert profiles['gemini']['SUMMARY_MODEL'] == 'gemini-3.1-flash-lite'
    assert profiles['doubao']['SUMMARY_MODEL'] == 'doubao-seed-2-0-lite-260428'

    raw = json.loads((PROJECT_ROOT / 'config' / 'api_providers.json').read_text(encoding='utf-8'))
    assert 'correction_model' not in json.dumps(raw)
    # The built-in fallback profiles mirror api_providers.json.
    fallback = (PROJECT_ROOT / 'config' / 'api_profiles.py').read_text(encoding='utf-8')
    assert 'CORRECTION_MODEL' not in fallback


@pytest.mark.unit
def test_get_never_returns_old_correction_fields(config_manager, core_config_router):
    _write_core_config(config_manager, _custom_config())

    response = asyncio.run(core_config_router.get_core_config_api())

    assert response['success'] is True
    assert not any(key.startswith('correctionModel') for key in response)
    assert 'sk-old-correction-key' not in json.dumps(response)


@pytest.mark.unit
def test_save_drops_old_correction_fields(config_manager, core_config_router):
    _write_core_config(config_manager, _custom_config())

    response = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        'coreApi': 'qwen',
        'assistApi': 'qwen',
        'enableCustomApi': True,
    })))

    assert response['success'] is True
    saved = config_manager.load_json_config('core_config.json', {})
    assert not any(key.startswith('correctionModel') for key in saved)
    assert saved['summaryModelId'] == 'kept-summary-model'


@pytest.mark.unit
def test_save_drops_correction_fields_posted_by_an_old_page(config_manager, core_config_router):
    _write_core_config(config_manager, _custom_config(**{
        key: '' for key in _OLD_CORRECTION_SLOT
    }))

    response = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        'coreApi': 'qwen',
        'assistApi': 'qwen',
        'enableCustomApi': True,
        **_OLD_CORRECTION_SLOT,
    })))

    assert response['success'] is True
    saved = config_manager.load_json_config('core_config.json', {})
    assert not any(key.startswith('correctionModel') for key in saved)


def _tier_calls(relative_path):
    source = (PROJECT_ROOT / relative_path).read_text(encoding='utf-8')
    return re.findall(r"get_model_api_config\(\s*['\"]([a-z_]+)['\"]", source)


@pytest.mark.unit
@pytest.mark.parametrize('relative_path', [
    'memory/recent.py',
    'memory/refine.py',
    'memory/behavior_notes.py',
    'memory/introspection.py',
    'memory/persona/corrections.py',
    'memory/persona/fusion.py',
])
def test_former_correction_callers_use_the_summary_tier(relative_path):
    calls = _tier_calls(relative_path)
    assert calls, relative_path
    assert set(calls) == {'summary'}


@pytest.mark.unit
def test_memory_setting_tiers_name_existing_tiers():
    from config import memory_settings

    assert memory_settings.PINNED_FACT_RESOLVE_MODEL_TIER == 'summary'
    assert memory_settings.EVIDENCE_PROMOTION_MERGE_MODEL_TIER == 'summary'
    tiers = {
        value for name, value in vars(memory_settings).items()
        if name.endswith('_MODEL_TIER')
    }
    assert 'correction' not in tiers


@pytest.mark.unit
@pytest.mark.parametrize('relative_path, tiers', [
    # recall tries the native decision rerank first; its chat-LLM fallback is emotion.
    ('memory/recall.py', ['decision', 'emotion']),
    ('main_routers/galgame_router.py', ['emotion']),
    ('utils/web_scraper/window_context.py', ['emotion']),
    ('main_logic/proactive_chat/break_reminders.py', ['conversation']),
])
def test_moved_light_jobs_use_their_new_tier(relative_path, tiers):
    assert _tier_calls(relative_path) == tiers


@pytest.mark.unit
def test_no_production_code_asks_for_the_correction_tier():
    offenders = []
    for path in PROJECT_ROOT.rglob('*.py'):
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        if relative.startswith(('tests/', '.venv/', 'node_modules/', '.git/')):
            continue
        try:
            source = path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        if re.search(r"get_model_api_config\(\s*['\"]correction['\"]", source):
            offenders.append(relative)
    assert offenders == []


class _RecordingLogger:
    def __init__(self, real):
        self._real = real
        self.infos = []

    def info(self, message, *args, **kwargs):
        self.infos.append(message % args if args else message)

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.mark.unit
def test_dropping_old_correction_slot_logs_provider_and_model_but_not_key(
    monkeypatch, core_config_router,
):
    recorder = _RecordingLogger(core_config_router.logger)
    monkeypatch.setattr(core_config_router, 'logger', recorder)

    stored = dict(_OLD_CORRECTION_SLOT)
    core_config_router._drop_retired_model_fields(stored)

    assert stored == {}
    dropped_logs = [line for line in recorder.infos if 'retired correction' in line]
    assert len(dropped_logs) == 1
    assert "'custom'" in dropped_logs[0] and "'old-correction-model'" in dropped_logs[0]
    assert 'sk-old-correction-key' not in dropped_logs[0]
    assert '11434' not in dropped_logs[0]


@pytest.mark.unit
def test_nothing_to_drop_logs_nothing(monkeypatch, core_config_router):
    recorder = _RecordingLogger(core_config_router.logger)
    monkeypatch.setattr(core_config_router, 'logger', recorder)

    stored = {'summaryModelId': 'kept', 'correctionModelProvider': ''}
    core_config_router._drop_retired_model_fields(stored)

    assert stored == {'summaryModelId': 'kept'}
    assert recorder.infos == []
