import json
import importlib

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

import memory_server
from utils.character_name import PROFILE_NAME_MAX_UNITS, validate_character_name


characters_router = importlib.import_module("main_routers.characters_router")
memory_router = importlib.import_module("main_routers.memory_router")


pytestmark = pytest.mark.unit


class _DummyRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class _DummyConfigManager:
    def __init__(self):
        self.saved = None

    def load_characters(self):
        return {"主人": {}, "猫娘": {}}

    def save_characters(self, data):
        self.saved = data


def test_character_name_accepts_middle_dot_and_multilingual_letters():
    assert validate_character_name("1·2·121·2", max_units=PROFILE_NAME_MAX_UNITS).ok
    assert validate_character_name("Тянь", max_units=PROFILE_NAME_MAX_UNITS).ok
    assert validate_character_name("냥이・二号", max_units=PROFILE_NAME_MAX_UNITS).ok

    assert characters_router._validate_profile_name("1·2·121·2") is None
    assert characters_router._validate_profile_name("Тянь") is None

    assert memory_server.validate_lanlan_name("1·2·121·2") == "1·2·121·2"
    assert memory_server.validate_lanlan_name("Тянь") == "Тянь"

    assert memory_router.validate_catgirl_name("1·2·121·2") == (True, "")
    assert memory_router.validate_catgirl_name("Тянь") == (True, "")


def test_character_name_rejects_windows_unsafe_chars():
    assert (
        characters_router._validate_profile_name("Bad:Name")
        == "档案名只能包含文字、数字、空格、下划线、连字符、括号、间隔号(·/・)和撇号"
    )

    is_valid, error = memory_router.validate_catgirl_name("Bad:Name")
    assert is_valid is False
    assert error == "名称只能包含文字、数字、空格、下划线、连字符、括号、间隔号(·/・)和撇号"

    with pytest.raises(HTTPException) as exc_info:
        memory_server.validate_lanlan_name("Bad:Name")
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid characters in lanlan_name"


def test_memory_router_keeps_legacy_dot_escape_hatch():
    assert memory_router.validate_catgirl_name("legacy.name") == (False, "名称不能包含点号(.)")
    assert memory_router.validate_catgirl_name("legacy.name", allow_dots=True) == (True, "")


@pytest.mark.asyncio
async def test_save_character_card_rejects_invalid_profile_name(monkeypatch):
    config_manager = _DummyConfigManager()
    monkeypatch.setattr(characters_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr(characters_router, "get_initialize_character_data", lambda: None)

    response = await characters_router.save_character_card(
        _DummyRequest(
            {
                "charaData": {"档案名": "Bad:Name", "性格": "测试"},
                "character_card_name": "Bad:Name",
            }
        )
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 400
    payload = json.loads(response.body)
    assert payload["success"] is False
    assert "角色名称无效" in payload["error"]
    assert config_manager.saved is None


@pytest.mark.asyncio
async def test_save_character_card_accepts_middle_dot_name(monkeypatch):
    config_manager = _DummyConfigManager()
    monkeypatch.setattr(characters_router, "get_config_manager", lambda: config_manager)
    monkeypatch.setattr(characters_router, "get_initialize_character_data", lambda: None)

    response = await characters_router.save_character_card(
        _DummyRequest(
            {
                "charaData": {"档案名": "1·2·121·2", "性格": "测试"},
                "character_card_name": "1·2·121·2",
            }
        )
    )

    assert response["success"] is True
    assert response["character_card_name"] == "1·2·121·2"
    assert config_manager.saved["猫娘"]["1·2·121·2"]["性格"] == "测试"
