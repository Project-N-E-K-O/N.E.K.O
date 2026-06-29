import copy
import importlib
import json

import pytest

from config import CHARACTER_RESERVED_FIELDS

characters_router_module = importlib.import_module('main_routers.characters_router')
from main_routers.config_router import _get_live3d_sub_type
from utils.config_manager import delete_reserved, flatten_reserved, get_reserved, migrate_catgirl_reserved, set_reserved


def _single_saved_catgirl(saved):
    catgirl_group = next(value for value in saved.values() if isinstance(value, dict) and value)
    return next(iter(catgirl_group.values()))


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

    async def aload_characters(self, character_json_path=None):
        return copy.deepcopy(self.characters)

    def save_characters(self, characters, character_json_path=None):
        self.saved_characters = copy.deepcopy(characters)
        self.characters = copy.deepcopy(characters)

    async def asave_characters(self, characters, character_json_path=None):
        self.save_characters(characters, character_json_path)


def test_live2d_idle_animation_is_reserved_and_hidden_from_editable_fields():
    assert 'live2d_idle_animation' in CHARACTER_RESERVED_FIELDS

    catgirl = {
        '性格': '开朗',
        'live2d_idle_animation': 'surprised1.motion3.json',
    }

    assert migrate_catgirl_reserved(catgirl) is True
    assert 'live2d_idle_animation' not in catgirl
    assert get_reserved(catgirl, 'avatar', 'live2d', 'idle_animation') == 'surprised1.motion3.json'

    flattened = flatten_reserved(catgirl)
    assert flattened['live2d_idle_animation'] == 'surprised1.motion3.json'
    editable_keys = [k for k in flattened if k not in CHARACTER_RESERVED_FIELDS]
    assert 'live2d_idle_animation' not in editable_keys


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

    async def _noop_init_one(name, *, is_new=False):
        return None

    monkeypatch.setattr(characters_router_module, 'get_config_manager', lambda: config_manager)
    monkeypatch.setattr(characters_router_module, 'get_initialize_character_data', lambda: _noop_initialize)
    monkeypatch.setattr(characters_router_module, 'get_init_one_catgirl', lambda: _noop_init_one)

    response = await characters_router_module.update_catgirl_l2d(
        '测试角色',
        DummyRequest(payload),
    )
    body = json.loads(response.body)
    return response, body, config_manager.saved_characters


@pytest.mark.asyncio
async def test_pngtuber_save_preserves_and_bounds_mobile_layout_fields(monkeypatch):
    response, body, saved = await _call_update(
        monkeypatch,
        {
            'model_type': 'pngtuber',
            'pngtuber': {
                'idle_image': '/static/pngtuber/default/idle.png',
                'talking_image': '/static/pngtuber/default/talking.png',
                'scale': 1.4,
                'offset_x': -42,
                'offset_y': 84,
                'mobile_scale': 9,
                'mobile_offset_x': -7000,
                'mobile_offset_y': 7000,
            },
        },
    )

    assert response.status_code == 200
    assert body['success'] is True
    catgirl = _single_saved_catgirl(saved)
    pngtuber = get_reserved(catgirl, 'avatar', 'pngtuber')

    assert pngtuber['scale'] == 1.4
    assert pngtuber['offset_x'] == -42
    assert pngtuber['offset_y'] == 84
    assert pngtuber['mobile_scale'] == 5
    assert pngtuber['mobile_offset_x'] == -5000
    assert pngtuber['mobile_offset_y'] == 5000


@pytest.mark.asyncio
async def test_pngtuber_save_defaults_missing_mobile_layout_fields(monkeypatch):
    response, body, saved = await _call_update(
        monkeypatch,
        {
            'model_type': 'pngtuber',
            'pngtuber': {
                'idle_image': '/static/pngtuber/default/idle.png',
                'scale': 2,
                'offset_x': 12,
                'offset_y': -34,
            },
        },
    )

    assert response.status_code == 200
    assert body['success'] is True
    catgirl = _single_saved_catgirl(saved)
    pngtuber = get_reserved(catgirl, 'avatar', 'pngtuber')

    assert pngtuber['mobile_scale'] == 1
    assert pngtuber['mobile_offset_x'] == 0
    assert pngtuber['mobile_offset_y'] == 0


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


@pytest.mark.asyncio
async def test_vrm_save_omitting_animation_preserves_saved_animation(monkeypatch):
    # 不带 vrm_animation 字段的保存不得动到已存的单动作。
    # 这是前端动作下拉恢复机制（restore -> 下拉显示已存动作 -> 保存回传原值）依赖的契约：
    # 若契约破坏，无关保存会静默清空已存动作。
    response, body, saved = await _call_update(
        monkeypatch,
        {
            'model_type': 'live3d',
            'vrm': '/user_vrm/models/hero.vrm',
        },
    )

    assert response.status_code == 200
    assert body['success'] is True
    catgirl = _single_saved_catgirl(saved)
    assert get_reserved(catgirl, 'avatar', 'vrm', 'animation') == '/user_vrm/animation/pose.vrma'


@pytest.mark.asyncio
async def test_vrm_save_empty_animation_clears_saved_animation(monkeypatch):
    # 只有显式传 vrm_animation='' 才清空，对应用户主动选择"无动作"(_no_motion_)。
    response, body, saved = await _call_update(
        monkeypatch,
        {
            'model_type': 'live3d',
            'vrm': '/user_vrm/models/hero.vrm',
            'vrm_animation': '',
        },
    )

    assert response.status_code == 200
    assert body['success'] is True
    catgirl = _single_saved_catgirl(saved)
    assert get_reserved(catgirl, 'avatar', 'vrm', 'animation') is None


@pytest.mark.asyncio
async def test_switching_workshop_origin_character_to_local_live3d_model_updates_current_asset_source_only(monkeypatch):
    characters = _build_characters_fixture()
    catgirl = characters['猫娘']['测试角色']
    set_reserved(catgirl, 'character_origin', 'source', 'steam_workshop')
    set_reserved(catgirl, 'character_origin', 'source_id', '114514')
    set_reserved(catgirl, 'character_origin', 'display_name', '工坊原始角色')
    set_reserved(catgirl, 'character_origin', 'model_ref', '/workshop/114514/hero.vrm')

    response, body, saved = await _call_update(
        monkeypatch,
        {
            'model_type': 'live3d',
            'vrm': '/user_vrm/models/local-override.vrm',
        },
        characters=characters,
    )

    assert response.status_code == 200
    assert body['success'] is True
    saved_catgirl = saved['猫娘']['测试角色']
    assert get_reserved(saved_catgirl, 'avatar', 'asset_source') == 'local_imported'
    assert get_reserved(saved_catgirl, 'avatar', 'asset_source_id') == ''
    assert get_reserved(saved_catgirl, 'character_origin', 'source') == 'steam_workshop'
    assert get_reserved(saved_catgirl, 'character_origin', 'source_id') == '114514'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('payload', 'expected_source_id'),
    [
        ({'model_type': 'live3d', 'vrm': '/workshop/998877/local/hero.vrm'}, '998877'),
        ({'model_type': 'live3d', 'mmd': '/workshop/998877/local/dancer.pmx'}, '998877'),
    ],
)
async def test_switching_self_created_character_to_workshop_model_marks_current_asset_as_workshop_without_overwriting_origin(
    monkeypatch,
    payload,
    expected_source_id,
):
    characters = _build_characters_fixture()
    catgirl = characters['猫娘']['测试角色']
    delete_reserved(catgirl, 'character_origin', 'source')
    delete_reserved(catgirl, 'character_origin', 'source_id')
    delete_reserved(catgirl, 'character_origin', 'display_name')
    delete_reserved(catgirl, 'character_origin', 'model_ref')
    set_reserved(catgirl, 'avatar', 'asset_source', 'local_imported')
    set_reserved(catgirl, 'avatar', 'asset_source_id', '')

    response, body, saved = await _call_update(
        monkeypatch,
        payload,
        characters=characters,
    )

    assert response.status_code == 200
    assert body['success'] is True
    saved_catgirl = saved['猫娘']['测试角色']
    assert get_reserved(saved_catgirl, 'avatar', 'asset_source') == 'steam_workshop'
    assert get_reserved(saved_catgirl, 'avatar', 'asset_source_id') == expected_source_id
    assert get_reserved(saved_catgirl, 'character_origin', 'source', default='') == ''
    assert get_reserved(saved_catgirl, 'character_origin', 'source_id', default='') == ''


@pytest.mark.asyncio
async def test_current_live2d_model_reports_failure_when_default_fallback_is_missing(monkeypatch):
    characters = {
        '当前猫娘': '测试角色',
        '猫娘': {
            '测试角色': {
                '_reserved': {
                    'avatar': {
                        'live2d': {
                            'model_path': '',
                        },
                    },
                },
            },
        },
    }
    config_manager = DummyConfigManager(characters)

    monkeypatch.setattr(characters_router_module, 'get_config_manager', lambda: config_manager)
    monkeypatch.setattr(characters_router_module, 'find_models', lambda: [])
    monkeypatch.setattr(characters_router_module, 'find_model_directory', lambda _name: (None, ''))

    response = await characters_router_module.get_current_live2d_model('测试角色')
    body = json.loads(response.body)

    assert body['success'] is False
    assert body['model_name'] == characters_router_module.DEFAULT_LIVE2D_MODEL_NAME
    assert body['model_info'] is None
    assert '默认Live2D模型' in body['error']


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


def test_flatten_catgirl_for_response_preserves_numeric_field_creation_order():
    catgirl = {
        '喵喵喵': '文字字段',
        '1': '数字字段',
    }

    flattened = characters_router_module._flatten_catgirl_for_response(catgirl)

    assert get_reserved(flattened, 'field_order') == ['喵喵喵', '1']
    assert '_reserved' not in catgirl


def test_sync_catgirl_field_order_honors_top_level_payload():
    # 工坊上传卡的顺序存在顶层 _field_order（上传时 _reserved 被剥离）；列表/读取入口调 _sync
    # 时不传 payload，必须认这个顶层字段，否则退回 JSON key 枚举顺序让数字 key 被提前。
    catgirl = {
        '1': '数字字段',
        '喵喵喵': '文字字段',
        '_field_order': ['喵喵喵', '1'],
    }

    characters_router_module._sync_catgirl_field_order(catgirl)

    assert get_reserved(catgirl, 'field_order') == ['喵喵喵', '1']


def test_migrate_catgirl_reserved_does_not_persist_empty_live3d_sub_type():
    catgirl = _build_characters_fixture()['猫娘']['测试角色']
    avatar = catgirl['_reserved']['avatar']
    avatar['model_type'] = 'live2d'
    avatar['vrm']['model_path'] = ''
    avatar['mmd']['model_path'] = ''
    avatar.pop('live3d_sub_type', None)

    migrate_catgirl_reserved(catgirl)

    assert get_reserved(catgirl, 'avatar', 'live3d_sub_type', default='') == ''
    assert 'live3d_sub_type' not in catgirl['_reserved']['avatar']


def test_delete_reserved_prunes_empty_parent_nodes():
    catgirl = {'_reserved': {'avatar': {'live3d_sub_type': 'vrm'}}}

    deleted = delete_reserved(catgirl, 'avatar', 'live3d_sub_type')

    assert deleted is True
    assert '_reserved' not in catgirl
