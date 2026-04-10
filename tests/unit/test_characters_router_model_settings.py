import copy
import importlib
import json

import pytest

characters_router_module = importlib.import_module('main_routers.characters_router')
from main_routers.config_router import _get_live3d_sub_type
from utils.config_manager import flatten_reserved, get_reserved


class DummyRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class DummyConfigManager:
    def __init__(self, characters):
        self.characters = copy.deepcopy(characters)
        self.saved_characters = None

    def load_characters(self):
        return copy.deepcopy(self.characters)

    def save_characters(self, characters):
        self.saved_characters = copy.deepcopy(characters)
        self.characters = copy.deepcopy(characters)


def _build_characters_fixture():
    return {
        '猫娘': {
            '测试角色': {
                '_reserved': {
                    'avatar': {
                        'model_type': 'live3d',
                        'live3d_sub_type': 'vrm',
                        'live2d': {
                            'model_path': 'mao_pro/mao_pro.model3.json',
                        },
                        'asset_source_id': '114514',
                        'asset_source': 'steam_workshop',
                        'vrm': {
                            'model_path': '/user_vrm/models/hero.vrm',
                            'animation': '/user_vrm/animation/pose.vrma',
                            'idle_animation': ['/user_vrm/animation/wait1.vrma'],
                            'lighting': {'ambient': 0.8},
                        },
                        'mmd': {
                            'model_path': '/user_mmd/models/dancer.pmx',
                            'animation': '/user_mmd/animation/dance.vmd',
                            'idle_animation': ['/user_mmd/animation/wait1.vmd'],
                        },
                    }
                }
            }
        }
    }


async def _call_update(monkeypatch, payload, characters=None):
    config_manager = DummyConfigManager(characters or _build_characters_fixture())

    async def _noop_initialize():
        return None

    monkeypatch.setattr(characters_router_module, 'get_config_manager', lambda: config_manager)
    monkeypatch.setattr(characters_router_module, 'get_initialize_character_data', lambda: _noop_initialize)

    response = await characters_router_module.update_catgirl_l2d(
        '测试角色',
        DummyRequest(payload),
    )
    body = json.loads(response.body)
    return response, body, config_manager.saved_characters


@pytest.mark.asyncio
async def test_switching_back_to_live2d_preserves_saved_live3d_configs(monkeypatch):
    response, body, saved = await _call_update(
        monkeypatch,
        {
            'model_type': 'live2d',
            'live2d': 'mao_pro',
        },
    )

    assert response.status_code == 200
    assert body['success'] is True
    catgirl = saved['猫娘']['测试角色']

    assert get_reserved(catgirl, 'avatar', 'model_type') == 'live2d'
    assert get_reserved(catgirl, 'avatar', 'live3d_sub_type') == 'vrm'
    assert get_reserved(catgirl, 'avatar', 'vrm', 'model_path') == '/user_vrm/models/hero.vrm'
    assert get_reserved(catgirl, 'avatar', 'vrm', 'animation') == '/user_vrm/animation/pose.vrma'
    assert get_reserved(catgirl, 'avatar', 'vrm', 'idle_animation') == ['/user_vrm/animation/wait1.vrma']
    assert get_reserved(catgirl, 'avatar', 'mmd', 'model_path') == '/user_mmd/models/dancer.pmx'
    assert get_reserved(catgirl, 'avatar', 'mmd', 'animation') == '/user_mmd/animation/dance.vmd'
    assert get_reserved(catgirl, 'avatar', 'mmd', 'idle_animation') == ['/user_mmd/animation/wait1.vmd']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('payload', 'expected_sub_type', 'preserved_path_key', 'preserved_path'),
    [
        (
            {
                'model_type': 'live3d',
                'vrm': '/user_vrm/models/updated.vrm',
                'vrm_animation': '/user_vrm/animation/new_pose.vrma',
                'idle_animation': ['/user_vrm/animation/new_wait.vrma'],
            },
            'vrm',
            ('avatar', 'mmd', 'model_path'),
            '/user_mmd/models/dancer.pmx',
        ),
        (
            {
                'model_type': 'live3d',
                'mmd': '/user_mmd/models/updated.pmx',
                'mmd_animation': '/user_mmd/animation/new_dance.vmd',
                'mmd_idle_animation': ['/user_mmd/animation/new_wait.vmd'],
            },
            'mmd',
            ('avatar', 'vrm', 'model_path'),
            '/user_vrm/models/hero.vrm',
        ),
    ],
)
async def test_switching_live3d_subtypes_preserves_inactive_model_config(
    monkeypatch,
    payload,
    expected_sub_type,
    preserved_path_key,
    preserved_path,
):
    response, body, saved = await _call_update(monkeypatch, payload)

    assert response.status_code == 200
    assert body['success'] is True
    catgirl = saved['猫娘']['测试角色']

    assert get_reserved(catgirl, 'avatar', 'model_type') == 'live3d'
    assert get_reserved(catgirl, 'avatar', 'live3d_sub_type') == expected_sub_type
    assert get_reserved(catgirl, *preserved_path_key) == preserved_path
    if expected_sub_type == 'vrm':
        assert get_reserved(catgirl, 'avatar', 'vrm', 'model_path') == payload['vrm']
        assert get_reserved(catgirl, 'avatar', 'vrm', 'animation') == payload['vrm_animation']
        assert get_reserved(catgirl, 'avatar', 'vrm', 'idle_animation') == payload['idle_animation']
    else:
        assert get_reserved(catgirl, 'avatar', 'mmd', 'model_path') == payload['mmd']
        assert get_reserved(catgirl, 'avatar', 'mmd', 'animation') == payload['mmd_animation']
        assert get_reserved(catgirl, 'avatar', 'mmd', 'idle_animation') == payload['mmd_idle_animation']


def test_live3d_sub_type_prefers_persisted_active_sub_type_when_both_paths_exist():
    catgirl = _build_characters_fixture()['猫娘']['测试角色']

    assert _get_live3d_sub_type(catgirl) == 'vrm'

    set_reserved_target = catgirl['_reserved']['avatar']
    set_reserved_target['live3d_sub_type'] = 'mmd'
    assert _get_live3d_sub_type(catgirl) == 'mmd'


def test_live3d_sub_type_does_not_fallback_when_persisted_value_is_present():
    catgirl = _build_characters_fixture()['猫娘']['测试角色']

    catgirl['_reserved']['avatar']['live3d_sub_type'] = 'vrm'
    catgirl['_reserved']['avatar']['vrm']['model_path'] = ''
    assert _get_live3d_sub_type(catgirl) == 'vrm'

    catgirl['_reserved']['avatar']['live3d_sub_type'] = 'mmd'
    catgirl['_reserved']['avatar']['mmd']['model_path'] = ''
    assert _get_live3d_sub_type(catgirl) == 'mmd'


def test_flatten_reserved_exposes_live3d_sub_type_for_frontend_consumers():
    catgirl = _build_characters_fixture()['猫娘']['测试角色']

    flattened = flatten_reserved(catgirl)

    assert flattened['model_type'] == 'live3d'
    assert flattened['live3d_sub_type'] == 'vrm'