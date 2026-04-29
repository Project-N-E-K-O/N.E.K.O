from __future__ import annotations

import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from main_routers.shared_state import init_shared_state
from utils.file_utils import atomic_write_json
from utils.config_manager import ConfigManager
from utils.cloudsave_runtime import bootstrap_local_cloudsave_environment


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """These unit tests do not need the repo-level mock memory server."""
    yield


def _make_config_manager(tmp_root: Path) -> ConfigManager:
    with patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_root), patch.object(
        ConfigManager,
        "_get_standard_data_directory_candidates",
        return_value=[tmp_root],
    ), patch.object(
        ConfigManager,
        "get_legacy_app_root_candidates",
        return_value=[],
    ), patch.object(
        ConfigManager,
        "_get_project_root",
        return_value=tmp_root,
    ):
        config_manager = ConfigManager("N.E.K.O")
    config_manager._get_standard_data_directory_candidates = lambda: [tmp_root]
    config_manager.get_legacy_app_root_candidates = lambda: []
    config_manager.project_memory_dir = tmp_root / "memory" / "store"
    return config_manager


class _DummyRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class _InvalidJsonRequest:
    async def json(self):
        raise ValueError("invalid json")


def _parse_json_response(response):
    if isinstance(response, dict):
        return response
    body = getattr(response, "body", b"") or b"{}"
    return json.loads(body.decode("utf-8"))


@pytest.mark.unit
def test_get_character_data_uses_persona_override_in_runtime_view():
    with TemporaryDirectory() as td:
        config_manager = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(config_manager)

        characters = config_manager.load_characters()
        current_name = characters["当前猫娘"]
        characters["猫娘"][current_name].setdefault("_reserved", {})["persona_override"] = {
            "preset_id": "classic_genki",
            "source": "onboarding",
            "selected_at": "2026-04-29T12:00:00Z",
            "prompt_guidance": "Speak like an energetic, affectionate cat companion.",
            "profile": {
                "性格原型": "经典元气猫娘",
                "性格": "永远元气满格的小太阳",
                "口癖": "太棒了喵！",
                "爱好": "陪伴、温暖",
                "雷点": "冷漠敷衍",
                "隐藏设定": "情感价值优先",
                "一句话台词": "今天也让我陪着你吧。",
            },
        }
        config_manager.save_characters(characters)

        _, _, _, character_data, _, prompt_map, _, _, _ = config_manager.get_character_data()

        assert character_data[current_name]["性格原型"] == "经典元气猫娘"
        assert character_data[current_name]["一句话台词"] == "今天也让我陪着你吧。"
        assert "energetic, affectionate cat companion" in prompt_map[current_name]


@pytest.mark.unit
def test_get_character_data_ignores_stale_persona_selection_system_prompt_when_override_exists():
    with TemporaryDirectory() as td:
        config_manager = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(config_manager)

        characters = config_manager.load_characters()
        current_name = characters["当前猫娘"]
        characters["猫娘"][current_name].setdefault("_reserved", {}).update({
            "system_prompt": (
                "<NEKO_PERSONA_SELECTION>\n"
                "- 当前人格名称：傲娇毒舌小猫\n"
                "- 代表台词：哼，这种事也要问吗，笨蛋人类。\n"
                "</NEKO_PERSONA_SELECTION>"
            ),
            "persona_override": {
                "preset_id": "elegant_butler",
                "source": "manual_reselect",
                "selected_at": "2026-04-29T12:00:00Z",
                "prompt_guidance": "Speak with elegant, steady, professional composure.",
                "profile": {
                    "性格原型": "优雅全能管家",
                    "性格": "极致优雅的绅士管家",
                    "口癖": "谨遵命喵",
                    "爱好": "周全、稳妥",
                    "雷点": "失礼措辞",
                    "隐藏设定": "永远提前一步想到阁下未说出口的需求。",
                    "一句话台词": "谨遵命喵。为您妥善安排一切。",
                },
            },
        })
        config_manager.save_characters(characters)

        _, _, _, character_data, _, prompt_map, _, _, _ = config_manager.get_character_data()

        assert character_data[current_name]["性格原型"] == "优雅全能管家"
        assert "elegant, steady, professional composure" in prompt_map[current_name]
        assert "<NEKO_PERSONA_SELECTION>" not in prompt_map[current_name]
        assert "笨蛋人类" not in prompt_map[current_name]


@pytest.mark.unit
def test_get_character_data_keeps_custom_system_prompt_when_override_exists():
    with TemporaryDirectory() as td:
        config_manager = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(config_manager)

        characters = config_manager.load_characters()
        current_name = characters["当前猫娘"]
        characters["猫娘"][current_name].setdefault("_reserved", {}).update({
            "system_prompt": "You are a reserved fox spirit who speaks softly about moonlight.",
            "persona_override": {
                "preset_id": "classic_genki",
                "source": "manual_reselect",
                "selected_at": "2026-04-29T12:00:00Z",
                "prompt_guidance": "Speak like an energetic, affectionate cat companion.",
                "profile": {
                    "性格原型": "经典元气猫娘",
                    "性格": "永远元气满格的小太阳",
                    "口癖": "太棒了喵！",
                    "爱好": "陪伴、温暖",
                    "雷点": "冷漠敷衍",
                    "隐藏设定": "情感价值优先",
                    "一句话台词": "今天也让我陪着你吧。",
                },
            },
        })
        config_manager.save_characters(characters)

        _, _, _, character_data, _, prompt_map, _, _, _ = config_manager.get_character_data()

        assert character_data[current_name]["性格原型"] == "经典元气猫娘"
        assert "reserved fox spirit" in prompt_map[current_name]
        assert "energetic, affectionate cat companion" in prompt_map[current_name]


@pytest.mark.unit
def test_get_character_data_strips_legacy_persona_block_but_keeps_custom_system_prompt():
    with TemporaryDirectory() as td:
        config_manager = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(config_manager)

        characters = config_manager.load_characters()
        current_name = characters["当前猫娘"]
        characters["猫娘"][current_name].setdefault("_reserved", {}).update({
            "system_prompt": (
                "You are a reserved fox spirit.\n\n"
                "<NEKO_PERSONA_SELECTION>\n"
                "- 当前人格名称：傲娇毒舌小猫\n"
                "- 代表台词：哼，这种事也要问吗，笨蛋人类。\n"
                "</NEKO_PERSONA_SELECTION>\n\n"
                "Always speak softly about moonlight."
            ),
            "persona_override": {
                "preset_id": "classic_genki",
                "source": "manual_reselect",
                "selected_at": "2026-04-29T12:00:00Z",
                "prompt_guidance": "Speak like an energetic, affectionate cat companion.",
                "profile": {
                    "性格原型": "经典元气猫娘",
                    "性格": "永远元气满格的小太阳",
                    "口癖": "太棒了喵！",
                    "爱好": "陪伴、温暖",
                    "雷点": "冷漠敷衍",
                    "隐藏设定": "情感价值优先",
                    "一句话台词": "今天也让我陪着你吧。",
                },
            },
        })
        config_manager.save_characters(characters)

        _, _, _, character_data, _, prompt_map, _, _, _ = config_manager.get_character_data()

        assert character_data[current_name]["性格原型"] == "经典元气猫娘"
        assert "reserved fox spirit" in prompt_map[current_name]
        assert "moonlight" in prompt_map[current_name]
        assert "<NEKO_PERSONA_SELECTION>" not in prompt_map[current_name]
        assert "笨蛋人类" not in prompt_map[current_name]
        assert "energetic, affectionate cat companion" in prompt_map[current_name]


@pytest.mark.unit
def test_get_character_data_strips_legacy_persona_block_without_override():
    with TemporaryDirectory() as td:
        config_manager = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(config_manager)

        characters = config_manager.load_characters()
        current_name = characters["当前猫娘"]
        characters["猫娘"][current_name].setdefault("_reserved", {})["system_prompt"] = (
            "You are a reserved fox spirit.\n\n"
            "<NEKO_PERSONA_SELECTION>\n"
            "- 当前人格名称：傲娇毒舌小猫\n"
            "- 代表台词：哼，这种事也要问吗，笨蛋人类。\n"
            "</NEKO_PERSONA_SELECTION>\n\n"
            "Always speak softly about moonlight."
        )
        config_manager.save_characters(characters)

        _, _, _, _, _, prompt_map, _, _, _ = config_manager.get_character_data()

        assert "reserved fox spirit" in prompt_map[current_name]
        assert "moonlight" in prompt_map[current_name]
        assert "<NEKO_PERSONA_SELECTION>" not in prompt_map[current_name]
        assert "笨蛋人类" not in prompt_map[current_name]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_persona_routes_save_clear_and_track_onboarding_state():
    with TemporaryDirectory() as td:
        config_manager = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(config_manager)

        async def _noop(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", config_manager):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=config_manager,
                logger=None,
                initialize_character_data=_noop,
                switch_current_catgirl_fast=_noop,
                init_one_catgirl=_noop,
                remove_one_catgirl=_noop,
            )

            router_module = importlib.reload(importlib.import_module("main_routers.characters_router"))

            presets_response = await router_module.list_persona_presets_route()
            presets_body = _parse_json_response(presets_response)
            assert presets_body["success"] is True
            assert [preset["preset_id"] for preset in presets_body["presets"]] == [
                "classic_genki",
                "tsundere_helper",
                "elegant_butler",
            ]

            current_name = config_manager.load_characters()["当前猫娘"]
            save_result = await router_module.update_character_persona_selection(
                current_name,
                _DummyRequest({"preset_id": "classic_genki", "source": "onboarding"}),
            )
            assert save_result["success"] is True
            assert save_result["selection"]["mode"] == "override"

            characters = config_manager.load_characters()
            override = characters["猫娘"][current_name]["_reserved"]["persona_override"]
            assert override["preset_id"] == "classic_genki"

            selection_response = await router_module.get_character_persona_selection(current_name)
            selection_body = _parse_json_response(selection_response)
            assert selection_body["success"] is True
            assert selection_body["selection"]["preset_id"] == "classic_genki"

            clear_result = await router_module.clear_character_persona_selection(current_name)
            assert clear_result["success"] is True
            assert clear_result["selection"]["mode"] == "default"
            assert "persona_override" not in config_manager.load_characters()["猫娘"][current_name].get("_reserved", {})

            onboarding_response = await router_module.get_persona_onboarding_state()
            onboarding_body = _parse_json_response(onboarding_response)
            assert onboarding_body["state"]["status"] == "completed"

            update_onboarding_result = await router_module.set_persona_onboarding_state(
                _DummyRequest({"status": "completed"}),
            )
            assert update_onboarding_result["success"] is True
            assert update_onboarding_result["state"]["status"] == "completed"

            reopen_result = await router_module.request_current_character_persona_reselect()
            assert reopen_result["success"] is True
            assert reopen_result["state"]["manual_reselect_character_name"] == current_name

            onboarding_response = await router_module.get_persona_onboarding_state()
            onboarding_body = _parse_json_response(onboarding_response)
            assert onboarding_body["state"]["manual_reselect_character_name"] == current_name

            clear_reopen_result = await router_module.clear_current_character_persona_reselect()
            assert clear_reopen_result["success"] is True
            assert clear_reopen_result["state"]["manual_reselect_character_name"] == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_persona_selection_change_clears_stale_recent_history():
    with TemporaryDirectory() as td:
        config_manager = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(config_manager)

        async def _noop(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", config_manager):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=config_manager,
                logger=None,
                initialize_character_data=_noop,
                switch_current_catgirl_fast=_noop,
                init_one_catgirl=_noop,
                remove_one_catgirl=_noop,
            )

            router_module = importlib.reload(importlib.import_module("main_routers.characters_router"))

            current_name = config_manager.load_characters()["当前猫娘"]
            recent_path = config_manager.memory_dir / current_name / "recent.json"
            atomic_write_json(
                recent_path,
                [
                    {
                        "type": "ai",
                        "data": {"content": "哼，这种事也要问吗，笨蛋人类。"},
                    }
                ],
                ensure_ascii=False,
                indent=2,
            )

            save_result = await router_module.update_character_persona_selection(
                current_name,
                _DummyRequest({"preset_id": "classic_genki", "source": "onboarding"}),
            )
            assert save_result["success"] is True
            assert json.loads(recent_path.read_text(encoding="utf-8")) == []

            atomic_write_json(
                recent_path,
                [
                    {
                        "type": "ai",
                        "data": {"content": "下不为例喵。"},
                    }
                ],
                ensure_ascii=False,
                indent=2,
            )

            clear_result = await router_module.clear_character_persona_selection(current_name)
            assert clear_result["success"] is True
            assert json.loads(recent_path.read_text(encoding="utf-8")) == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_character_persona_selection_restarts_active_current_session():
    with TemporaryDirectory() as td:
        config_manager = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(config_manager)

        async def _noop(*args, **kwargs):
            return None

        current_name = config_manager.load_characters()["当前猫娘"]
        current_session = SimpleNamespace(
            is_active=True,
            websocket=object(),
            end_session=AsyncMock(),
        )
        role_state = {
            current_name: SimpleNamespace(session_manager=current_session),
        }
        init_one_catgirl = AsyncMock()

        with patch("utils.config_manager._config_manager", config_manager):
            init_shared_state(
                role_state=role_state,
                steamworks=None,
                templates=None,
                config_manager=config_manager,
                logger=None,
                initialize_character_data=_noop,
                switch_current_catgirl_fast=_noop,
                init_one_catgirl=init_one_catgirl,
                remove_one_catgirl=_noop,
            )

            router_module = importlib.reload(importlib.import_module("main_routers.characters_router"))
            with patch.object(router_module, "send_reload_page_notice", AsyncMock(return_value=True)) as reload_notice:
                save_result = await router_module.update_character_persona_selection(
                    current_name,
                    _DummyRequest({"preset_id": "classic_genki", "source": "manual_reselect"}),
                )

        assert save_result["success"] is True
        reload_notice.assert_awaited_once_with(current_session, "人格设定已更新，页面即将刷新")
        current_session.end_session.assert_awaited_once_with(by_server=True)
        init_one_catgirl.assert_awaited_once_with(current_name, is_new=False)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clear_character_persona_selection_restarts_active_current_session():
    with TemporaryDirectory() as td:
        config_manager = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(config_manager)

        async def _noop(*args, **kwargs):
            return None

        current_name = config_manager.load_characters()["当前猫娘"]
        current_session = SimpleNamespace(
            is_active=True,
            websocket=object(),
            end_session=AsyncMock(),
        )
        role_state = {
            current_name: SimpleNamespace(session_manager=current_session),
        }
        init_one_catgirl = AsyncMock()

        with patch("utils.config_manager._config_manager", config_manager):
            init_shared_state(
                role_state=role_state,
                steamworks=None,
                templates=None,
                config_manager=config_manager,
                logger=None,
                initialize_character_data=_noop,
                switch_current_catgirl_fast=_noop,
                init_one_catgirl=init_one_catgirl,
                remove_one_catgirl=_noop,
            )

            router_module = importlib.reload(importlib.import_module("main_routers.characters_router"))
            await router_module.update_character_persona_selection(
                current_name,
                _DummyRequest({"preset_id": "classic_genki", "source": "manual_reselect"}),
            )

            current_session.end_session.reset_mock()
            init_one_catgirl.reset_mock()

            with patch.object(router_module, "send_reload_page_notice", AsyncMock(return_value=True)) as reload_notice:
                clear_result = await router_module.clear_character_persona_selection(current_name)

        assert clear_result["success"] is True
        reload_notice.assert_awaited_once_with(current_session, "人格设定已更新，页面即将刷新")
        current_session.end_session.assert_awaited_once_with(by_server=True)
        init_one_catgirl.assert_awaited_once_with(current_name, is_new=False)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_character_persona_selection_finalizes_onboarding_and_manual_reselect_state():
    with TemporaryDirectory() as td:
        config_manager = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(config_manager)

        async def _noop(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", config_manager):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=config_manager,
                logger=None,
                initialize_character_data=_noop,
                switch_current_catgirl_fast=_noop,
                init_one_catgirl=_noop,
                remove_one_catgirl=_noop,
            )

            router_module = importlib.reload(importlib.import_module("main_routers.characters_router"))
            current_name = config_manager.load_characters()["当前猫娘"]

            onboarding_result = await router_module.update_character_persona_selection(
                current_name,
                _DummyRequest({"preset_id": "classic_genki", "source": "onboarding"}),
            )
            assert onboarding_result["success"] is True

            onboarding_state = await router_module.get_persona_onboarding_state()
            onboarding_body = _parse_json_response(onboarding_state)
            assert onboarding_body["state"]["status"] == "completed"

            pending_reselect = await router_module.request_current_character_persona_reselect()
            assert pending_reselect["success"] is True
            assert pending_reselect["state"]["manual_reselect_character_name"] == current_name

            manual_reselect_result = await router_module.update_character_persona_selection(
                current_name,
                _DummyRequest({"preset_id": "elegant_butler", "source": "manual_reselect"}),
            )
            assert manual_reselect_result["success"] is True

            finalized_state = await router_module.get_persona_onboarding_state()
            finalized_body = _parse_json_response(finalized_state)
            assert finalized_body["state"]["manual_reselect_character_name"] == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_persona_selection_routes_remove_stale_generated_system_prompt():
    with TemporaryDirectory() as td:
        config_manager = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(config_manager)

        async def _noop(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", config_manager):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=config_manager,
                logger=None,
                initialize_character_data=_noop,
                switch_current_catgirl_fast=_noop,
                init_one_catgirl=_noop,
                remove_one_catgirl=_noop,
            )

            router_module = importlib.reload(importlib.import_module("main_routers.characters_router"))

            current_name = config_manager.load_characters()["当前猫娘"]
            characters = config_manager.load_characters()
            characters["猫娘"][current_name].setdefault("_reserved", {})["system_prompt"] = (
                "<NEKO_PERSONA_SELECTION>\n"
                "- 当前人格名称：傲娇毒舌小猫\n"
                "- 代表台词：哼，这种事也要问吗，笨蛋人类。\n"
                "</NEKO_PERSONA_SELECTION>"
            )
            config_manager.save_characters(characters)

            save_result = await router_module.update_character_persona_selection(
                current_name,
                _DummyRequest({"preset_id": "elegant_butler", "source": "manual_reselect"}),
            )
            assert save_result["success"] is True
            saved_reserved = config_manager.load_characters()["猫娘"][current_name].get("_reserved", {})
            assert "system_prompt" not in saved_reserved

            characters = config_manager.load_characters()
            characters["猫娘"][current_name].setdefault("_reserved", {})["system_prompt"] = (
                "<NEKO_PERSONA_SELECTION>\n"
                "- 当前人格名称：经典元气猫娘\n"
                "- 代表台词：太棒了喵！\n"
                "</NEKO_PERSONA_SELECTION>"
            )
            config_manager.save_characters(characters)

            clear_result = await router_module.clear_character_persona_selection(current_name)
            assert clear_result["success"] is True
            cleared_reserved = config_manager.load_characters()["猫娘"][current_name].get("_reserved", {})
            assert "system_prompt" not in cleared_reserved


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_persona_selection_routes_preserve_custom_system_prompt_around_legacy_block():
    with TemporaryDirectory() as td:
        config_manager = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(config_manager)

        async def _noop(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", config_manager):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=config_manager,
                logger=None,
                initialize_character_data=_noop,
                switch_current_catgirl_fast=_noop,
                init_one_catgirl=_noop,
                remove_one_catgirl=_noop,
            )

            router_module = importlib.reload(importlib.import_module("main_routers.characters_router"))

            current_name = config_manager.load_characters()["当前猫娘"]
            characters = config_manager.load_characters()
            characters["猫娘"][current_name].setdefault("_reserved", {})["system_prompt"] = (
                "You are a reserved fox spirit.\n\n"
                "<NEKO_PERSONA_SELECTION>\n"
                "- 当前人格名称：傲娇毒舌小猫\n"
                "- 代表台词：哼，这种事也要问吗，笨蛋人类。\n"
                "</NEKO_PERSONA_SELECTION>\n\n"
                "Always speak softly about moonlight."
            )
            config_manager.save_characters(characters)

            save_result = await router_module.update_character_persona_selection(
                current_name,
                _DummyRequest({"preset_id": "elegant_butler", "source": "manual_reselect"}),
            )
            assert save_result["success"] is True
            saved_reserved = config_manager.load_characters()["猫娘"][current_name].get("_reserved", {})
            assert saved_reserved["system_prompt"] == (
                "You are a reserved fox spirit.\n\n"
                "Always speak softly about moonlight."
            )

            characters = config_manager.load_characters()
            characters["猫娘"][current_name].setdefault("_reserved", {})["system_prompt"] = (
                "You are a reserved fox spirit.\n\n"
                "<NEKO_PERSONA_SELECTION>\n"
                "- 当前人格名称：经典元气猫娘\n"
                "- 代表台词：太棒了喵！\n"
                "</NEKO_PERSONA_SELECTION>\n\n"
                "Always speak softly about moonlight."
            )
            config_manager.save_characters(characters)

            clear_result = await router_module.clear_character_persona_selection(current_name)
            assert clear_result["success"] is True
            cleared_reserved = config_manager.load_characters()["猫娘"][current_name].get("_reserved", {})
            assert cleared_reserved["system_prompt"] == (
                "You are a reserved fox spirit.\n\n"
                "Always speak softly about moonlight."
            )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_persona_routes_reject_invalid_json_and_normalize_non_object_payloads():
    with TemporaryDirectory() as td:
        config_manager = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(config_manager)

        async def _noop(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", config_manager):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=config_manager,
                logger=None,
                initialize_character_data=_noop,
                switch_current_catgirl_fast=_noop,
                init_one_catgirl=_noop,
                remove_one_catgirl=_noop,
            )

            router_module = importlib.reload(importlib.import_module("main_routers.characters_router"))
            current_name = config_manager.load_characters()["当前猫娘"]

            invalid_onboarding = await router_module.set_persona_onboarding_state(_InvalidJsonRequest())
            invalid_onboarding_body = _parse_json_response(invalid_onboarding)
            assert invalid_onboarding.status_code == 400
            assert invalid_onboarding_body == {
                "success": False,
                "error": "请求体必须是合法的JSON格式",
            }

            non_object_onboarding = await router_module.set_persona_onboarding_state(
                _DummyRequest(["completed"]),
            )
            assert non_object_onboarding["success"] is True
            assert non_object_onboarding["state"]["status"] == "pending"

            invalid_selection = await router_module.update_character_persona_selection(
                current_name,
                _InvalidJsonRequest(),
            )
            invalid_selection_body = _parse_json_response(invalid_selection)
            assert invalid_selection.status_code == 400
            assert invalid_selection_body == {
                "success": False,
                "error": "请求体必须是合法的JSON格式",
            }

            non_object_selection = await router_module.update_character_persona_selection(
                current_name,
                _DummyRequest(["classic_genki"]),
            )
            non_object_selection_body = _parse_json_response(non_object_selection)
            assert non_object_selection.status_code == 400
            assert non_object_selection_body == {
                "success": False,
                "error": "无效的人格预设",
            }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_character_persona_selection_rolls_back_if_recent_clear_fails():
    with TemporaryDirectory() as td:
        config_manager = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(config_manager)

        async def _noop(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", config_manager):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=config_manager,
                logger=None,
                initialize_character_data=_noop,
                switch_current_catgirl_fast=_noop,
                init_one_catgirl=_noop,
                remove_one_catgirl=_noop,
            )

            router_module = importlib.reload(importlib.import_module("main_routers.characters_router"))
            current_name = config_manager.load_characters()["当前猫娘"]

            async def _boom(*args, **kwargs):
                raise RuntimeError("recent clear failed")

            with patch.object(router_module, "_clear_character_recent_history", _boom):
                with pytest.raises(RuntimeError, match="recent clear failed"):
                    await router_module.update_character_persona_selection(
                        current_name,
                        _DummyRequest({"preset_id": "classic_genki", "source": "manual_reselect"}),
                    )

            saved_reserved = config_manager.load_characters()["猫娘"][current_name].get("_reserved", {})
            assert "persona_override" not in saved_reserved


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clear_character_persona_selection_rolls_back_if_recent_clear_fails():
    with TemporaryDirectory() as td:
        config_manager = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(config_manager)

        async def _noop(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", config_manager):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=config_manager,
                logger=None,
                initialize_character_data=_noop,
                switch_current_catgirl_fast=_noop,
                init_one_catgirl=_noop,
                remove_one_catgirl=_noop,
            )

            router_module = importlib.reload(importlib.import_module("main_routers.characters_router"))
            current_name = config_manager.load_characters()["当前猫娘"]

            await router_module.update_character_persona_selection(
                current_name,
                _DummyRequest({"preset_id": "classic_genki", "source": "manual_reselect"}),
            )

            async def _boom(*args, **kwargs):
                raise RuntimeError("recent clear failed")

            with patch.object(router_module, "_clear_character_recent_history", _boom):
                with pytest.raises(RuntimeError, match="recent clear failed"):
                    await router_module.clear_character_persona_selection(current_name)

            saved_reserved = config_manager.load_characters()["猫娘"][current_name].get("_reserved", {})
            assert saved_reserved["persona_override"]["preset_id"] == "classic_genki"
