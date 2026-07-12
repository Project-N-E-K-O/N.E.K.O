import asyncio
import copy
import importlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from main_routers.shared_state import init_shared_state


def _make_role_state_for_test(session_managers: dict) -> dict:
    """Seed role_state with pre-existing session_managers (post-#855 + cross_server async).

    The legacy 6-dict layout (sync_message_queue / sync_shutdown_event /
    session_manager / session_id / sync_process / websocket_locks) was
    consolidated into RoleState on main. ``sync_shutdown_event`` /
    ``sync_process`` were further removed when cross_server moved from
    daemon thread to a main-loop ``asyncio.Task`` (now ``sync_task``).
    Tests that only care about seeding session_manager construct stub
    RoleState entries with live Queue / asyncio.Lock so adapters don't
    crash on attribute access.
    """
    # Import lazily to avoid circular import at module load time
    from app.main_server import RoleState, _SyncMessageQueue
    return {
        name: RoleState(
            sync_message_queue=_SyncMessageQueue(),
            websocket_lock=asyncio.Lock(),
            session_manager=session_manager,
        )
        for name, session_manager in session_managers.items()
    }
from utils.config_manager import ConfigManager
from utils.cloudsave_runtime import (
    CLOUDSAVE_DISABLED_ENV,
    MaintenanceModeError,
    ROOT_MODE_BOOTSTRAP_IMPORTING,
    bootstrap_local_cloudsave_environment,
)


def _make_config_manager(tmp_root: Path):
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


def reload_module(module_name: str):
    module = importlib.import_module(module_name)
    return importlib.reload(module)


class _DummyRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class _DummyGetRequest:
    def __init__(self, query_params=None, headers=None):
        self.query_params = query_params or {}
        self.headers = headers or {}


class _FakeTranslationService:
    async def translate_dict(self, data, target_lang, fields_to_translate=None):
        result = dict(data)
        for field in fields_to_translate or []:
            value = result.get(field)
            if isinstance(value, str) and value:
                result[field] = f"{target_lang}:{value}"
        return result


@pytest.mark.unit
def test_character_router_profile_name_validation_maps_dot_error_codes():
    router_module = reload_module("main_routers.characters_router.crud")

    assert "点号" in router_module._validate_profile_name(".")
    assert "点号" in router_module._validate_profile_name("foo.")
    assert "路径分隔符" in router_module._validate_profile_name("..")
    assert "点号" in router_module._validate_profile_name("N.E.K.O")
    assert "保留" in router_module._validate_profile_name("api")

    assert router_module._validate_existing_character_path_name(".") is not None
    assert router_module._validate_existing_character_path_name("foo.") is not None
    assert router_module._validate_existing_character_path_name("..") is not None
    assert router_module._validate_existing_character_path_name("N.E.K.O") is None
    assert router_module._validate_existing_character_path_name("api") is None


@pytest.mark.unit
def test_profile_rename_event_prompt_i18n_is_complete_and_first_person():
    from config.prompts.prompts_memory import (
        PROFILE_RENAME_EVENT_FIELD,
        PROFILE_RENAME_EVENT_TEXT,
        render_profile_rename_event_context,
    )

    expected_langs = {"zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}
    assert set(PROFILE_RENAME_EVENT_FIELD) == expected_langs
    assert set(PROFILE_RENAME_EVENT_TEXT) == expected_langs

    zh_label, zh_text = render_profile_rename_event_context("zh-CN", "旧角色", "新角色")
    assert zh_label == "我的改名记录"
    assert "我曾用名" in zh_text
    assert "旧角色" in zh_text
    assert "新角色" in zh_text
    assert "只代表改名前的历史称呼" not in zh_text

    en_label, en_text = render_profile_rename_event_context("en", "Old", "New")
    assert en_label == "My Profile Rename Record"
    assert "formerly known as" in en_text
    assert "Old" in en_text
    assert "New" in en_text
    assert "historical name before the rename" not in en_text


@pytest.mark.unit
def test_profile_rename_event_master_is_person_neutral():
    """主人改名记录进的是猫娘 persona 的 master section，读者是猫娘、
    改名的是用户。第一人称会让猫娘误以为是自己改名，所以这里去掉人称、
    用中性陈述，既不能出现「我」也不带「你」。"""
    from config.prompts.prompts_memory import (
        PROFILE_RENAME_EVENT_FIELD_MASTER,
        PROFILE_RENAME_EVENT_TEXT_MASTER,
        render_profile_rename_event_context,
    )

    expected_langs = {"zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}
    assert set(PROFILE_RENAME_EVENT_FIELD_MASTER) == expected_langs
    assert set(PROFILE_RENAME_EVENT_TEXT_MASTER) == expected_langs

    zh_label, zh_text = render_profile_rename_event_context("zh-CN", "旧名", "新名", entity="master")
    assert zh_label == "改名记录"
    assert "旧名" in zh_text and "新名" in zh_text
    # 去人称：既无第一人称「我」也无第二人称「你」。
    assert "我" not in zh_text
    assert "你" not in zh_text

    en_label, en_text = render_profile_rename_event_context("en", "Old", "New", entity="master")
    assert en_label == "Profile Rename Record"
    assert "Old" in en_text and "New" in en_text
    assert "My " not in en_text and "Your " not in en_text

    # 缺省（neko）仍是第一人称，主人变体不影响默认行为。
    _, neko_text = render_profile_rename_event_context("zh-CN", "旧名", "新名")
    assert "我曾用名" in neko_text


@pytest.mark.unit
def test_master_effective_payload_rename_context_is_person_neutral(monkeypatch):
    monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "zh-CN")
    from utils.config_manager import _build_effective_character_payload

    payload = {
        "档案名": "新主人名",
        "_reserved": {
            "ai_context": {
                "rename_events": [
                    {"type": "profile_rename", "old_name": "旧主人名", "new_name": "新主人名"},
                ]
            }
        },
    }
    effective = _build_effective_character_payload(payload, entity="master")
    context = effective["__ai_context.profile_rename_events"]
    assert "旧主人名" in context and "新主人名" in context
    assert "我" not in context
    assert "你" not in context


@pytest.mark.unit
def test_profile_rename_event_uses_collision_safe_synthetic_key(monkeypatch):
    monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "zh-CN")
    from utils.config_manager import _build_effective_character_payload

    payload = {
        "档案名": "新角色",
        "我的改名记录": "用户自己写的字段",
        "_reserved": {
            "ai_context": {
                "rename_events": [
                    {
                        "type": "profile_rename",
                        "old_name": "旧角色",
                        "new_name": "临时角色",
                    },
                    {
                        "type": "profile_rename",
                        "old_name": "临时角色",
                        "new_name": "新角色",
                    }
                ]
            }
        },
    }
    effective = _build_effective_character_payload(payload)

    assert effective["我的改名记录"] == "用户自己写的字段"
    hidden_context = effective["__ai_context.profile_rename_events"]
    assert "我的改名记录" in hidden_context
    assert "我曾用名" in hidden_context
    assert "旧角色" in hidden_context
    assert "临时角色" in hidden_context
    assert "新角色" in hidden_context
    assert hidden_context.count("我的改名记录") == 1

    payload["__ai_context.profile_rename_events"] = "用户内部命名字段"
    effective_with_internal_collision = _build_effective_character_payload(payload)
    assert effective_with_internal_collision["__ai_context.profile_rename_events"] == "用户内部命名字段"
    collision_values = [
        value
        for key, value in effective_with_internal_collision.items()
        if key.startswith("__ai_context.profile_rename_events.")
    ]
    assert any("我曾用名" in str(value) for value in collision_values)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_management_and_recent_save_regression():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        # Simulate a crashed import run and verify bootstrap can recover on next start.
        root_state = cm.load_root_state()
        root_state["mode"] = ROOT_MODE_BOOTSTRAP_IMPORTING
        cm.save_root_state(root_state)
        bootstrap_local_cloudsave_environment(cm)
        assert cm.load_root_state()["mode"] == "normal"

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            memory_router_module = reload_module("main_routers.memory_router")
            initial_name = next(iter(cm.load_characters().get("猫娘", {}).keys()))

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client):
                add_result = await characters_router_module.add_catgirl(
                    _DummyRequest({"档案名": "测试角色"})
                )
            assert add_result["success"] is True
            assert "测试角色" in cm.load_characters().get("猫娘", {})

            switch_result = await characters_router_module.set_current_catgirl(
                _DummyRequest({"catgirl_name": "测试角色"})
            )
            assert switch_result["success"] is True
            assert cm.load_characters()["当前猫娘"] == "测试角色"

            save_recent_result = await memory_router_module.save_recent_file(
                _DummyRequest(
                    {
                        "filename": "recent_测试角色.json",
                        "chat": [{"role": "user", "text": "你好"}],
                    }
                )
            )
            assert save_recent_result["success"] is True
            assert (Path(cm.memory_dir) / "测试角色" / "recent.json").is_file()

            switch_back_result = await characters_router_module.set_current_catgirl(
                _DummyRequest({"catgirl_name": initial_name})
            )
            assert switch_back_result["success"] is True
            assert cm.load_characters()["当前猫娘"] == initial_name

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client):
                delete_result = await characters_router_module.delete_catgirl("测试角色")
            assert delete_result["success"] is True
            assert "测试角色" not in cm.load_characters().get("猫娘", {})
            assert not (Path(cm.memory_dir) / "测试角色").exists()
            tombstones = cm.load_character_tombstones_state().get("tombstones") or []
            assert any(entry.get("character_name") == "测试角色" for entry in tombstones)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_switch_clears_theater_session_for_outgoing_catgirl():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router")
            from services.theater import runtime, session_store

            characters = cm.load_characters()
            old_catgirl = characters["当前猫娘"]
            new_catgirl = "测试切换猫娘"
            characters["猫娘"][new_catgirl] = copy.deepcopy(characters["猫娘"][old_catgirl])
            cm.save_characters(characters)

            theater_root = Path(cm.app_docs_dir) / "theater"
            theater_session = await runtime.start_session(theater_root, lanlan_name=old_catgirl)
            switch_result = await characters_router_module.set_current_catgirl(
                _DummyRequest({"catgirl_name": new_catgirl})
            )

            session = await session_store.load_session(theater_root, theater_session["session_id"])
            active_index = await session_store.load_active_sessions(theater_root)
            assert switch_result["success"] is True
            assert cm.load_characters()["当前猫娘"] == new_catgirl
            assert session["phase"] == "ended"
            assert session["ended_at"]
            assert active_index == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_switch_reloads_config_after_theater_wait():
    """等待小剧场边界后必须合并最新角色配置，不能覆盖并发更新。"""  # noqa: DOCSTRING_CJK
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )
            characters_router_module = reload_module("main_routers.characters_router")
            characters = cm.load_characters()
            old_catgirl = characters["当前猫娘"]
            new_catgirl = "等待后切换猫娘"
            characters["猫娘"][new_catgirl] = copy.deepcopy(characters["猫娘"][old_catgirl])
            cm.save_characters(characters)

            async def _mutate_during_theater_wait(_config_manager, _old_catgirl, publish):
                """在发布回调前模拟另一请求新增角色并完成写盘。"""  # noqa: DOCSTRING_CJK
                latest = await cm.aload_characters()
                latest["猫娘"]["并发新增猫娘"] = copy.deepcopy(latest["猫娘"][old_catgirl])
                await cm.asave_characters(latest)
                await publish()
                return {"cleared": False}

            # 公开 Router 包只导出接口，切换函数的全局依赖实际定义在 crud 子模块。
            with patch(
                "main_routers.characters_router.crud._publish_character_switch_with_theater_boundary",
                _mutate_during_theater_wait,
            ):
                result = await characters_router_module.set_current_catgirl(
                    _DummyRequest({"catgirl_name": new_catgirl})
                )

            saved = cm.load_characters()
            assert result["success"] is True
            assert saved["当前猫娘"] == new_catgirl
            assert "并发新增猫娘" in saved["猫娘"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_add_catgirl_rejects_unsafe_dot_profile_name():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            response = await characters_router_module.add_catgirl(_DummyRequest({"档案名": "."}))

            assert response.status_code == 400
            payload = json.loads(response.body.decode("utf-8"))
            assert payload["success"] is False
            assert "点号" in payload["error"]
            assert "." not in cm.load_characters().get("猫娘", {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_body_delete_rejects_non_object_json_payload():
    characters_router_module = reload_module("main_routers.characters_router.crud")

    response = await characters_router_module.delete_catgirl_by_body(_DummyRequest(["."]))

    assert response.status_code == 400
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["success"] is False
    assert "JSON" in payload["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_body_delete_rescues_unsafe_dot_character_without_touching_memory_paths():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters = cm.load_characters()
            characters.setdefault("猫娘", {})["正常角色"] = {"昵称": "正常角色"}
            characters.setdefault("猫娘", {})["."] = {"昵称": "坏角色"}
            characters["当前猫娘"] = "正常角色"
            cm.save_characters(characters, bypass_write_fence=True)

            sentinel = Path(cm.memory_dir) / "sentinel.txt"
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text("keep", encoding="utf-8")

            characters_router_module = reload_module("main_routers.characters_router.crud")
            mock_notify_reload = AsyncMock(return_value=True)
            with (
                patch.object(characters_router_module, "notify_memory_server_reload", mock_notify_reload),
                patch.object(characters_router_module, "delete_character_memory_storage") as mock_delete_memory,
            ):
                result = await characters_router_module.delete_catgirl_by_body(_DummyRequest({"name": "."}))

            assert result["success"] is True
            assert result["unsafe_name_rescue"] is True
            assert result["memory_deleted"] is False
            mock_notify_reload.assert_awaited_once()
            assert "." not in cm.load_characters().get("猫娘", {})
            assert sentinel.read_text(encoding="utf-8") == "keep"
            mock_delete_memory.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_read_endpoints_disable_caching():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")

            characters_response = await characters_router_module.get_characters(
                _DummyGetRequest(headers={"Accept-Language": "zh-CN"})
            )
            current_response = await characters_router_module.get_current_catgirl()

            assert characters_response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate, max-age=0"
            assert characters_response.headers["Pragma"] == "no-cache"
            assert current_response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate, max-age=0"
            assert current_response.headers["Pragma"] == "no-cache"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_characters_preserves_profile_names_when_translating_display_fields():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        characters = cm.load_characters()
        characters["主人"] = {"档案名": "主人原名", "昵称": "主人昵称"}
        characters["猫娘"] = {
            "猫娘原名": {
                "档案名": "猫娘原名",
                "昵称": "猫娘昵称",
                "性别": "女",
            }
        }
        characters["当前猫娘"] = "猫娘原名"
        cm.save_characters(characters)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm), patch(
            "utils.language_utils.get_translation_service",
            return_value=_FakeTranslationService(),
        ):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            response = await characters_router_module.get_characters(
                _DummyGetRequest(headers={"Accept-Language": "en-US"})
            )
            payload = json.loads(response.body.decode("utf-8"))

            assert payload["主人"]["档案名"] == "主人原名"
            assert payload["主人"]["昵称"] == "en:主人昵称"
            assert "猫娘原名" in payload["猫娘"]
            assert payload["猫娘"]["猫娘原名"]["档案名"] == "猫娘原名"
            assert payload["猫娘"]["猫娘原名"]["昵称"] == "en:猫娘昵称"
            assert payload["猫娘"]["猫娘原名"]["性别"] == "en:女"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rename_catgirl_moves_runtime_and_legacy_memory_storage(monkeypatch):
    monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "zh-CN")
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            memory_router_module = reload_module("main_routers.memory_router")

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client):
                add_result = await characters_router_module.add_catgirl(
                    _DummyRequest({"档案名": "旧角色"})
                )
            assert add_result["success"] is True

            # 把待改名角色设为当前猫娘并启动演出，验证改名后不会遗留旧名称的恢复入口。
            from services.theater import runtime as theater_runtime, session_store as theater_session_store

            characters = cm.load_characters()
            characters["当前猫娘"] = "旧角色"
            cm.save_characters(characters)
            theater_root = Path(cm.app_docs_dir) / "theater"
            theater_session = await theater_runtime.start_session(theater_root, lanlan_name="旧角色")

            old_memory_dir = Path(cm.memory_dir) / "旧角色"
            old_memory_dir.mkdir(parents=True, exist_ok=True)
            (Path(cm.project_memory_dir)).mkdir(parents=True, exist_ok=True)

            (old_memory_dir / "persona.json").write_text('{"traits":["温柔"]}', encoding="utf-8")
            (old_memory_dir / "recent.json").write_text(
                json.dumps(
                    [
                        {
                            "speaker": "旧角色",
                            "data": {"content": "旧角色说：你好"},
                        }
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (Path(cm.project_memory_dir) / "facts_旧角色.json").write_text(
                '[{"id":"fact-1","text":"旧记忆"}]',
                encoding="utf-8",
            )

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client):
                rename_result = await characters_router_module.rename_catgirl(
                    "旧角色",
                    _DummyRequest({"new_name": "新角色"}),
                )

            assert rename_result["success"] is True
            assert rename_result["memory_renamed"] is True
            saved_characters = cm.load_characters()
            assert "新角色" in saved_characters.get("猫娘", {})
            assert "旧角色" not in saved_characters.get("猫娘", {})
            saved_theater_session = await theater_session_store.load_session(
                theater_root,
                theater_session["session_id"],
            )
            assert saved_theater_session["ended_at"]
            assert await theater_session_store.load_active_sessions(theater_root) == {}
            saved_profile = saved_characters["猫娘"]["新角色"]
            assert "我的改名记录" not in saved_profile
            rename_events = saved_profile["_reserved"]["ai_context"]["rename_events"]
            assert rename_events[-1]["old_name"] == "旧角色"
            assert rename_events[-1]["new_name"] == "新角色"
            assert "text" not in rename_events[-1]

            _, _, _, effective_character_data, _, _, _, _, _ = cm.get_character_data()
            hidden_context = effective_character_data["新角色"]["__ai_context.profile_rename_events"]
            assert "我的改名记录" in hidden_context
            assert "我曾用名" in hidden_context
            assert "旧角色" in hidden_context
            assert "新角色" in hidden_context
            from memory.persona import PersonaManager
            persona_md = PersonaManager().render_persona_markdown("新角色")
            # 合成字段的内部裸键不能泄漏进渲染给模型的 persona 文本，只保留本地化标签。
            assert "__ai_context.profile_rename_events" not in persona_md
            assert "我的改名记录" in persona_md
            assert "我曾用名" in persona_md
            assert "旧角色" in persona_md
            assert "新角色" in persona_md
            assert not (Path(cm.memory_dir) / "旧角色").exists()
            assert (Path(cm.memory_dir) / "新角色" / "persona.json").is_file()
            assert (Path(cm.memory_dir) / "新角色" / "facts.json").is_file()

            recent_payload = json.loads(
                (Path(cm.memory_dir) / "新角色" / "recent.json").read_text(encoding="utf-8")
            )
            assert recent_payload[0]["speaker"] == "新角色"
            assert recent_payload[0]["data"]["content"].startswith("新角色说：")

            memory_rename_result = await memory_router_module.update_catgirl_name(
                _DummyRequest({"old_name": "旧角色", "new_name": "新角色"})
            )
            assert memory_rename_result["success"] is True
            assert (Path(cm.memory_dir) / "新角色" / "recent.json").is_file()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rename_master_adds_hidden_ai_context_and_master_save_preserves_it(monkeypatch):
    monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "zh-CN")
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            old_master_name = cm.load_characters()["主人"]["档案名"]
            current_catgirl = cm.load_characters()["当前猫娘"]

            rename_result = await characters_router_module.rename_master(
                old_master_name,
                _DummyRequest({"new_name": "新主人"}),
            )

            assert rename_result["success"] is True
            saved_master = cm.load_characters()["主人"]
            assert "我的改名记录" not in saved_master
            rename_events = saved_master["_reserved"]["ai_context"]["rename_events"]
            assert rename_events[-1]["old_name"] == old_master_name
            assert rename_events[-1]["new_name"] == "新主人"
            assert "text" not in rename_events[-1]

            _, _, master_basic_config, _, _, _, _, _, _ = cm.get_character_data()
            hidden_context = master_basic_config["__ai_context.profile_rename_events"]
            # 主人改名记录进的是猫娘 persona 的 master section，去掉人称用中性陈述，
            # 既不能第一人称「我」（否则猫娘会以为是自己改了名），也不带第二人称「你」。
            assert "改名记录" in hidden_context
            assert "我" not in hidden_context
            assert "你" not in hidden_context
            assert old_master_name in hidden_context
            assert "新主人" in hidden_context

            from memory.persona import PersonaManager
            persona_md = PersonaManager().render_persona_markdown(current_catgirl)
            # 同上：裸键不泄漏，且主人段无人称。
            assert "__ai_context.profile_rename_events" not in persona_md
            assert "改名记录" in persona_md
            assert old_master_name in persona_md
            assert "新主人" in persona_md

            update_result = await characters_router_module.update_master(
                _DummyRequest({"档案名": "新主人", "昵称": "柚希"})
            )
            assert update_result["success"] is True
            saved_after_update = cm.load_characters()["主人"]
            assert saved_after_update["档案名"] == "新主人"
            assert saved_after_update["_reserved"]["ai_context"]["rename_events"][-1]["new_name"] == "新主人"
            initial_count = len(saved_after_update["_reserved"]["ai_context"]["rename_events"])

            bypass_result = await characters_router_module.update_master(
                _DummyRequest({"档案名": "绕过改名", "昵称": "柚希"})
            )
            assert bypass_result["success"] is True
            saved_after_bypass = cm.load_characters()["主人"]
            assert saved_after_bypass["档案名"] == "新主人"
            assert saved_after_bypass["_reserved"]["ai_context"]["rename_events"][-1]["new_name"] == "新主人"
            assert len(saved_after_bypass["_reserved"]["ai_context"]["rename_events"]) == initial_count

            same_name_result = await characters_router_module.rename_master(
                "新主人",
                _DummyRequest({"new_name": "新主人"}),
            )
            assert same_name_result["success"] is True
            saved_after_same_name = cm.load_characters()["主人"]
            assert len(saved_after_same_name["_reserved"]["ai_context"]["rename_events"]) == initial_count

            legacy_conflict_characters = cm.load_characters()
            legacy_conflict_characters.setdefault("猫娘", {})["新主人"] = {"档案名": "新主人"}
            cm.save_characters(legacy_conflict_characters)
            legacy_conflict_update = await characters_router_module.update_master(
                _DummyRequest({"昵称": "柚希2"})
            )
            assert legacy_conflict_update["success"] is True
            assert cm.load_characters()["主人"]["档案名"] == "新主人"
            empty_update = await characters_router_module.update_master(_DummyRequest({}))
            assert empty_update["success"] is True
            saved_after_empty_update = cm.load_characters()["主人"]
            assert saved_after_empty_update["档案名"] == "新主人"
            assert "昵称" not in saved_after_empty_update
            assert len(saved_after_empty_update["_reserved"]["ai_context"]["rename_events"]) == initial_count

            rename_conflict_characters = cm.load_characters()
            rename_conflict_characters.setdefault("猫娘", {})["主人同名猫娘"] = {"档案名": "主人同名猫娘"}
            cm.save_characters(rename_conflict_characters)
            cross_namespace_rename = await characters_router_module.rename_master(
                "新主人",
                _DummyRequest({"new_name": "主人同名猫娘"}),
            )
            assert cross_namespace_rename["success"] is True
            saved_after_cross_namespace_rename = cm.load_characters()["主人"]
            assert saved_after_cross_namespace_rename["档案名"] == "主人同名猫娘"
            assert saved_after_cross_namespace_rename["_reserved"]["ai_context"]["rename_events"][-1]["new_name"] == "主人同名猫娘"

            conflict_characters = cm.load_characters()
            conflict_characters["主人"] = {}
            conflict_characters.setdefault("猫娘", {})["占用名"] = {"档案名": "占用名"}
            cm.save_characters(conflict_characters)
            conflict_result = await characters_router_module.update_master(
                _DummyRequest({"档案名": "占用名", "昵称": "柚希"})
            )
            assert conflict_result["success"] is True
            assert cm.load_characters()["主人"]["档案名"] == "占用名"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_master_body_rename_fallback_repairs_legacy_path_name(monkeypatch):
    monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "zh-CN")
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            legacy_characters = cm.load_characters()
            legacy_characters["主人"]["档案名"] = "旧/主人"
            legacy_characters["主人"]["昵称"] = "旧昵称"
            cm.save_characters(legacy_characters)

            repair_result = await characters_router_module.update_master(
                _DummyRequest({"档案名": "修复主人", "昵称": "柚希"})
            )
            assert repair_result["success"] is True
            saved_after_repair = cm.load_characters()["主人"]
            assert saved_after_repair["档案名"] == "修复主人"
            assert saved_after_repair["昵称"] == "柚希"
            rename_events = saved_after_repair["_reserved"]["ai_context"]["rename_events"]
            assert rename_events[-1]["old_name"] == "旧/主人"
            assert rename_events[-1]["new_name"] == "修复主人"
            initial_count = len(rename_events)

            bypass_result = await characters_router_module.update_master(
                _DummyRequest({"档案名": "再次绕过", "昵称": "柚希2"})
            )
            assert bypass_result["success"] is True
            saved_after_bypass = cm.load_characters()["主人"]
            assert saved_after_bypass["档案名"] == "修复主人"
            assert saved_after_bypass["昵称"] == "柚希2"
            assert len(saved_after_bypass["_reserved"]["ai_context"]["rename_events"]) == initial_count


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rename_catgirl_rolls_back_memory_and_suppresses_switch_notice_on_persist_failure():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        websocket = AsyncMock()

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state=_make_role_state_for_test({
                    "旧角色": SimpleNamespace(is_active=False, websocket=websocket, session=None),
                }),
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client):
                add_result = await characters_router_module.add_catgirl(
                    _DummyRequest({"档案名": "旧角色"})
                )
            assert add_result["success"] is True

            characters = cm.load_characters()
            characters["当前猫娘"] = "旧角色"
            cm.save_characters(characters, bypass_write_fence=True)

            old_memory_dir = Path(cm.memory_dir) / "旧角色"
            old_memory_dir.mkdir(parents=True, exist_ok=True)
            (old_memory_dir / "recent.json").write_text(
                json.dumps(
                    [
                        {
                            "speaker": "旧角色",
                            "data": {"content": "旧角色说：你好"},
                        }
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            original_save_characters = cm.save_characters

            def _fail_primary_save(data, character_json_path=None, *, bypass_write_fence=False):
                if not bypass_write_fence and "新角色" in (data.get("猫娘") or {}):
                    raise OSError("disk full")
                return original_save_characters(
                    data,
                    character_json_path=character_json_path,
                    bypass_write_fence=bypass_write_fence,
                )

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client), patch.object(
                cm,
                "save_characters",
                side_effect=_fail_primary_save,
            ):
                rename_result = await characters_router_module.rename_catgirl(
                    "旧角色",
                    _DummyRequest({"new_name": "新角色"}),
                )

            assert rename_result.status_code == 500
            payload = json.loads(rename_result.body.decode("utf-8"))
            assert payload["success"] is False
            assert "disk full" in payload["error"]

            current_characters = cm.load_characters()
            assert "旧角色" in current_characters.get("猫娘", {})
            assert "新角色" not in current_characters.get("猫娘", {})
            assert current_characters["当前猫娘"] == "旧角色"
            assert old_memory_dir.exists()
            assert not (Path(cm.memory_dir) / "新角色").exists()

            restored_recent_payload = json.loads((old_memory_dir / "recent.json").read_text(encoding="utf-8"))
            assert restored_recent_payload[0]["speaker"] == "旧角色"
            websocket.send_text.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rename_catgirl_returns_503_and_keeps_disk_unchanged_when_memory_release_fails():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        websocket = AsyncMock()

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state=_make_role_state_for_test({
                    "旧角色": SimpleNamespace(is_active=False, websocket=websocket, session=None),
                }),
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client):
                add_result = await characters_router_module.add_catgirl(
                    _DummyRequest({"档案名": "旧角色"})
                )
            assert add_result["success"] is True

            characters = cm.load_characters()
            characters["当前猫娘"] = "旧角色"
            cm.save_characters(characters, bypass_write_fence=True)

            old_memory_dir = Path(cm.memory_dir) / "旧角色"
            old_memory_dir.mkdir(parents=True, exist_ok=True)
            (old_memory_dir / "recent.json").write_text(
                json.dumps(
                    [
                        {
                            "speaker": "旧角色",
                            "data": {"content": "旧角色说：你好"},
                        }
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            with patch.object(
                characters_router_module,
                "release_memory_server_character",
                AsyncMock(return_value=False),
            ) as mock_release:
                rename_result = await characters_router_module.rename_catgirl(
                    "旧角色",
                    _DummyRequest({"new_name": "新角色"}),
                )

            assert rename_result.status_code == 503
            payload = json.loads(rename_result.body.decode("utf-8"))
            assert payload["success"] is False
            assert payload["code"] == "MEMORY_SERVER_RELEASE_FAILED"
            mock_release.assert_awaited_once()

            current_characters = cm.load_characters()
            assert "旧角色" in current_characters.get("猫娘", {})
            assert "新角色" not in current_characters.get("猫娘", {})
            assert current_characters["当前猫娘"] == "旧角色"
            assert old_memory_dir.exists()
            assert (old_memory_dir / "recent.json").is_file()
            assert not (Path(cm.memory_dir) / "新角色").exists()
            websocket.send_text.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rename_catgirl_maintenance_error_preserves_original_exception_type_when_rollback_reports_string():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            characters = cm.load_characters()
            characters.setdefault("猫娘", {})["维护重命名角色"] = {"昵称": "维护重命名角色"}
            cm.save_characters(characters, bypass_write_fence=True)

            maintenance_error = MaintenanceModeError(
                "maintenance_readonly",
                operation="rename",
                target="characters/维护重命名角色 -> 新角色",
            )
            original_save_characters = cm.save_characters

            def _raise_maintenance_on_primary_save(data, character_json_path=None, *, bypass_write_fence=False):
                if not bypass_write_fence and "新角色" in (data.get("猫娘") or {}):
                    raise maintenance_error
                return original_save_characters(
                    data,
                    character_json_path=character_json_path,
                    bypass_write_fence=bypass_write_fence,
                )

            with (
                patch.object(
                    characters_router_module,
                    "release_memory_server_character",
                    AsyncMock(return_value=True),
                ),
                patch.object(cm, "save_characters", side_effect=_raise_maintenance_on_primary_save),
                patch.object(
                    characters_router_module,
                    "_rollback_character_operation",
                    AsyncMock(return_value="notify_memory_server_reload failed: returned False"),
                ),
            ):
                with pytest.raises(MaintenanceModeError) as exc_info:
                    await characters_router_module.rename_catgirl(
                        "维护重命名角色",
                        _DummyRequest({"new_name": "新角色"}),
                    )

            assert exc_info.value is maintenance_error
            assert isinstance(exc_info.value.__cause__, RuntimeError)
            assert "notify_memory_server_reload failed: returned False" in str(exc_info.value.__cause__)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deleted_workshop_character_is_not_restored_by_startup_sync():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            characters = cm.load_characters()
            initial_name = next(iter(characters.get("猫娘", {})))
            characters["猫娘"]["工坊角色"] = {"昵称": "会复活吗"}
            cm.save_characters(characters, bypass_write_fence=True)

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client):
                delete_result = await characters_router_module.delete_catgirl("工坊角色")
            assert delete_result["success"] is True
            assert "工坊角色" not in cm.load_characters().get("猫娘", {})

            installed_folder = Path(td) / "mock_workshop_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "工坊角色", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result["added"] == 0
            assert sync_result["skipped"] >= 1
            current_characters = cm.load_characters()
            assert "工坊角色" not in current_characters.get("猫娘", {})
            assert current_characters["当前猫娘"] == initial_name


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_catgirl_skips_tombstone_state_when_cloudsave_local_state_is_unavailable(monkeypatch):
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")
            importlib.import_module('main_routers.workshop_router.meta')._session_deleted_names.clear()
            characters = cm.load_characters()
            initial_name = next(iter(characters.get("猫娘", {})))
            characters["猫娘"]["禁用云存档删除角色"] = {"昵称": "禁用云存档删除角色"}
            characters["当前猫娘"] = initial_name
            cm.save_characters(characters, bypass_write_fence=True)

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            monkeypatch.setenv(CLOUDSAVE_DISABLED_ENV, "local_state_unavailable")
            with (
                patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client),
                patch.object(
                    cm,
                    "load_character_tombstones_state",
                    side_effect=AssertionError("disabled cloudsave delete path should not load tombstones"),
                ),
                patch.object(
                    cm,
                    "save_character_tombstones_state",
                    side_effect=AssertionError("disabled cloudsave delete path should not save tombstones"),
                ),
                patch.object(
                    characters_router_module,
                    "_build_character_tombstones_state",
                    side_effect=AssertionError("disabled cloudsave delete path should not build tombstones"),
                ),
            ):
                delete_result = await characters_router_module.delete_catgirl("禁用云存档删除角色")

            assert delete_result["success"] is True
            current_characters = cm.load_characters()
            assert "禁用云存档删除角色" not in current_characters.get("猫娘", {})
            assert current_characters["当前猫娘"] == initial_name

            installed_folder = Path(td) / "disabled_cloudsave_workshop_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "禁用云存档删除角色", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result["added"] == 0
            assert sync_result["skipped"] >= 1
            current_characters = cm.load_characters()
            assert "禁用云存档删除角色" not in current_characters.get("猫娘", {})
            importlib.import_module('main_routers.workshop_router.meta')._session_deleted_names.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_manual_workshop_character_sync_restores_deleted_character_and_clears_tombstone():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            deleted_name = "手动恢复工坊角色"
            cm.save_character_tombstones_state({
                "version": cm.CHARACTER_TOMBSTONES_STATE_VERSION,
                "tombstones": [
                    {
                        "character_name": deleted_name,
                        "deleted_at": "2026-05-25T00:00:00Z",
                        "sequence_number": 1,
                    }
                ],
            })

            installed_folder = Path(td) / "mock_workshop_manual_restore_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": deleted_name, "昵称": "来自手动恢复"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            subscribed_items_mock = AsyncMock(
                return_value={
                    "success": True,
                    "items": [
                        {
                            "publishedFileId": "123456",
                            "installedFolder": str(installed_folder),
                        }
                    ],
                }
            )
            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                subscribed_items_mock,
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards(
                    target_item_id="123456",
                    restore_deleted=True,
                )
                second_result = await workshop_router_module.sync_workshop_character_cards(
                    target_item_id="123456",
                    restore_deleted=True,
                )

            assert sync_result["added"] == 1
            assert sync_result["added_character_names"] == [deleted_name]
            assert sync_result["restored_deleted_names"] == [deleted_name]
            current_characters = cm.load_characters()
            assert deleted_name in current_characters.get("猫娘", {})
            tombstones = cm.load_character_tombstones_state().get("tombstones") or []
            assert not any(entry.get("character_name") == deleted_name for entry in tombstones)

            assert second_result["added"] == 0
            assert second_result["existing_character_names"] == [deleted_name]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_manual_workshop_character_sync_clears_tombstone_for_existing_character():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            restored_name = "已存在但有墓碑角色"
            characters = cm.load_characters()
            characters.setdefault("猫娘", {})[restored_name] = {
                "昵称": "已存在",
                "_reserved": {
                    "character_origin": {
                        "source": "steam_workshop",
                        "source_id": "123456",
                    }
                },
            }
            cm.save_characters(characters, bypass_write_fence=True)
            cm.save_character_tombstones_state({
                "version": cm.CHARACTER_TOMBSTONES_STATE_VERSION,
                "tombstones": [
                    {
                        "character_name": restored_name,
                        "deleted_at": "2026-05-25T00:00:00Z",
                        "sequence_number": 1,
                    }
                ],
            })

            installed_folder = Path(td) / "mock_workshop_existing_restore_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": restored_name, "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards(
                    target_item_id="123456",
                    restore_deleted=True,
                )

            assert sync_result["added"] == 0
            assert sync_result["existing_character_names"] == [restored_name]
            assert sync_result["restored_deleted_names"] == [restored_name]
            tombstones = cm.load_character_tombstones_state().get("tombstones") or []
            assert not any(entry.get("character_name") == restored_name for entry in tombstones)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_manual_workshop_character_sync_clears_tombstone_for_avatar_only_bound_character():
    # 回归：旧数据 / 半迁移数据可能只有 avatar.asset_source 绑定（例如 live2d_item_id
    # 迁移只写 avatar.asset_source_id，或用户在模型设置里手动绑定 Workshop 模型），
    # 没有 character_origin。退订路径已按 avatar 命中删除它并打 tombstone，恢复路径
    # 也必须按 avatar 命中并清理 tombstone，否则该角色会永远卡在 409。
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            restored_name = "仅头像绑定角色"
            characters = cm.load_characters()
            characters.setdefault("猫娘", {})[restored_name] = {
                "昵称": "已存在",
                "_reserved": {
                    "avatar": {
                        "asset_source": "steam_workshop",
                        "asset_source_id": "123456",
                    }
                },
            }
            cm.save_characters(characters, bypass_write_fence=True)
            cm.save_character_tombstones_state({
                "version": cm.CHARACTER_TOMBSTONES_STATE_VERSION,
                "tombstones": [
                    {
                        "character_name": restored_name,
                        "deleted_at": "2026-05-25T00:00:00Z",
                        "sequence_number": 1,
                    }
                ],
            })

            installed_folder = Path(td) / "mock_workshop_avatar_only_restore_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": restored_name, "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards(
                    target_item_id="123456",
                    restore_deleted=True,
                )

            assert sync_result["added"] == 0
            assert sync_result["existing_character_names"] == [restored_name]
            assert sync_result["restored_deleted_names"] == [restored_name]
            tombstones = cm.load_character_tombstones_state().get("tombstones") or []
            assert not any(entry.get("character_name") == restored_name for entry in tombstones)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_manual_workshop_character_sync_keeps_tombstone_for_nonmatching_existing_character():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            restored_name = "同名本地角色"
            characters = cm.load_characters()
            characters.setdefault("猫娘", {})[restored_name] = {"昵称": "本地角色"}
            cm.save_characters(characters, bypass_write_fence=True)
            cm.save_character_tombstones_state({
                "version": cm.CHARACTER_TOMBSTONES_STATE_VERSION,
                "tombstones": [
                    {
                        "character_name": restored_name,
                        "deleted_at": "2026-05-25T00:00:00Z",
                        "sequence_number": 1,
                    }
                ],
            })

            installed_folder = Path(td) / "mock_workshop_nonmatching_restore_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": restored_name, "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards(
                    target_item_id="123456",
                    restore_deleted=True,
                )

            assert sync_result["added"] == 0
            assert sync_result["existing_character_names"] == [restored_name]
            assert sync_result["restored_deleted_names"] == []
            tombstones = cm.load_character_tombstones_state().get("tombstones") or []
            assert any(entry.get("character_name") == restored_name for entry in tombstones)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_manual_workshop_character_sync_defers_tombstone_cleanup_after_successful_save():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            restored_name = "延后清理墓碑角色"
            characters = cm.load_characters()
            characters.setdefault("猫娘", {})[restored_name] = {
                "昵称": "已存在",
                "_reserved": {
                    "character_origin": {
                        "source": "steam_workshop",
                        "source_id": "123456",
                    }
                },
            }
            cm.save_characters(characters, bypass_write_fence=True)
            cm.save_character_tombstones_state({
                "version": cm.CHARACTER_TOMBSTONES_STATE_VERSION,
                "tombstones": [
                    {
                        "character_name": restored_name,
                        "deleted_at": "2026-05-25T00:00:00Z",
                        "sequence_number": 1,
                    }
                ],
            })

            installed_folder = Path(td) / "mock_workshop_deferred_restore_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "existing.chara.json").write_text(
                json.dumps({"档案名": restored_name, "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (installed_folder / "new.chara.json").write_text(
                json.dumps({"档案名": "新工坊角色", "昵称": "新角色"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with (
                patch.object(
                    workshop_router_module,
                    "get_subscribed_workshop_items",
                    AsyncMock(
                        return_value={
                            "success": True,
                            "items": [
                                {
                                    "publishedFileId": "123456",
                                    "installedFolder": str(installed_folder),
                                }
                            ],
                        }
                    ),
                ),
                patch.object(workshop_router_module, "_ensure_workshop_card_face_from_preview", return_value=False),
                patch.object(workshop_router_module, "_ensure_workshop_card_face_meta", return_value=False),
                patch.object(
                    workshop_router_module,
                    "is_write_fence_active",
                    side_effect=[False, False, False, False, False, False, True],
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards(
                    target_item_id="123456",
                    restore_deleted=True,
                )

            assert sync_result["added"] == 1
            assert sync_result["tombstone_cleanup_deferred"] is True
            assert "新工坊角色" in cm.load_characters().get("猫娘", {})
            tombstones = cm.load_character_tombstones_state().get("tombstones") or []
            assert any(entry.get("character_name") == restored_name for entry in tombstones)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_single_workshop_character_card_treats_restored_existing_as_success():
    workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")
    sync_result = {
        "added": 0,
        "backfilled_faces": 0,
        "skipped": 1,
        "errors": 0,
        "target_found": True,
        "found_character_names": ["恢复角色"],
        "existing_character_names": ["恢复角色"],
        "restored_deleted_names": ["恢复角色"],
    }

    with patch.object(
        workshop_router_module,
        "sync_workshop_character_cards",
        AsyncMock(return_value=sync_result),
    ):
        response = await workshop_router_module.api_sync_single_workshop_character_card("123456")

    assert response["success"] is True
    assert response["restored_deleted_names"] == ["恢复角色"]
    assert response["message"] == "已加入角色卡：恢复角色"
    # 前端成功提示只读 added_character_names，仅恢复场景也必须带上恢复角色名，
    # 否则会被 formatWorkshopCharacterNameList 回退成“未知角色卡”。
    assert response["added_character_names"] == ["恢复角色"]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sync_result", "expected_status", "expected_code"),
    [
        (
            {
                "added": 0,
                "backfilled_faces": 0,
                "skipped": 0,
                "errors": 0,
                "target_found": False,
                "code": "WORKSHOP_ITEM_NOT_FOUND",
            },
            404,
            "WORKSHOP_ITEM_NOT_FOUND",
        ),
        (
            {
                "added": 0,
                "backfilled_faces": 0,
                "skipped": 1,
                "errors": 0,
                "target_found": True,
                "found_character_names": ["已存在角色"],
                "existing_character_names": ["已存在角色"],
            },
            409,
            "WORKSHOP_CHARACTER_ALREADY_EXISTS",
        ),
        (
            {
                "added": 0,
                "backfilled_faces": 0,
                "skipped": 0,
                "errors": 0,
                "target_found": True,
                "found_character_names": [],
                "existing_character_names": [],
            },
            404,
            "WORKSHOP_CHARACTER_NOT_FOUND",
        ),
        (
            {
                "added": 0,
                "backfilled_faces": 0,
                "skipped": 0,
                "errors": 0,
                "target_found": True,
                "found_character_names": ["未加入角色"],
                "existing_character_names": [],
            },
            422,
            "WORKSHOP_CHARACTER_NOT_ADDED",
        ),
        (
            # 真实后端异常被显式标记为 WORKSHOP_SYNC_FAILED 时，必须回 500，
            # 不能因 target_found / found_character_names 的残留值被误判成业务态。
            {
                "added": 0,
                "backfilled_faces": 0,
                "skipped": 0,
                "errors": 1,
                "target_found": True,
                "found_character_names": [],
                "existing_character_names": [],
                "code": "WORKSHOP_SYNC_FAILED",
            },
            500,
            "WORKSHOP_SYNC_FAILED",
        ),
    ],
)
async def test_sync_single_workshop_character_card_uses_error_status_codes(
    sync_result,
    expected_status,
    expected_code,
):
    workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

    with patch.object(
        workshop_router_module,
        "sync_workshop_character_cards",
        AsyncMock(return_value=sync_result),
    ):
        response = await workshop_router_module.api_sync_single_workshop_character_card("123456")

    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == expected_status
    assert payload["success"] is False
    assert payload["code"] == expected_code
    assert "error" in payload


@pytest.mark.unit
@pytest.mark.asyncio
async def test_batch_workshop_character_sync_reports_subscription_unavailable():
    workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")
    sync_result = {
        "added": 0,
        "backfilled_faces": 0,
        "skipped": 0,
        "errors": 1,
        "code": "WORKSHOP_SUBSCRIPTIONS_UNAVAILABLE",
    }

    with patch.object(
        workshop_router_module,
        "sync_workshop_character_cards",
        AsyncMock(return_value=sync_result),
    ):
        response = await workshop_router_module.api_sync_workshop_character_cards()

    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 503
    assert payload["success"] is False
    assert payload["code"] == "WORKSHOP_SUBSCRIPTIONS_UNAVAILABLE"
    assert payload["errors"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_batch_workshop_character_sync_reports_internal_failure_as_500():
    # 后端异常被标记为 WORKSHOP_SYNC_FAILED 时，批量入口也要回 500，
    # 不能伪装成 success 的“同步完成”。
    workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")
    sync_result = {
        "added": 0,
        "backfilled_faces": 0,
        "skipped": 0,
        "errors": 1,
        "code": "WORKSHOP_SYNC_FAILED",
    }

    with patch.object(
        workshop_router_module,
        "sync_workshop_character_cards",
        AsyncMock(return_value=sync_result),
    ):
        response = await workshop_router_module.api_sync_workshop_character_cards()

    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 500
    assert payload["success"] is False
    assert payload["code"] == "WORKSHOP_SYNC_FAILED"
    assert payload["errors"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_skips_save_when_maintenance_fence_turns_on():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_maintenance_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "维护态工坊角色", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            maintenance_error = MaintenanceModeError(
                "maintenance_readonly",
                operation="save",
                target="characters.json",
            )
            assert_saved_mock = AsyncMock(side_effect=maintenance_error)

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ), patch.object(cm, "asave_characters", assert_saved_mock):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result == {
                "added": 0,
                "backfilled_faces": 0,
                "skipped": 0,
                "errors": 0,
                "blocked_by_write_fence": True,
            }
            assert_saved_mock.assert_awaited_once()
            assert "维护态工坊角色" not in cm.load_characters().get("猫娘", {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_preserves_persona_override_written_during_scan():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_persona_race_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "启动竞态工坊角色", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            current_name = cm.load_characters()["当前猫娘"]

            def _write_persona_override_during_scan(_installed_folder, _chara_name=None, _chara_file_stem=None):
                latest = cm.load_characters()
                latest["猫娘"][current_name].setdefault("_reserved", {})["persona_override"] = {
                    "preset_id": "classic_genki",
                    "source": "onboarding",
                    "selected_at": "2026-05-08T12:00:00Z",
                    "prompt_guidance": "保持测试人格",
                    "profile": {
                        "性格原型": "经典元气猫娘",
                    },
                }
                cm.save_characters(latest, bypass_write_fence=True)
                return None

            with (
                patch.object(
                    workshop_router_module,
                    "get_subscribed_workshop_items",
                    AsyncMock(
                        return_value={
                            "success": True,
                            "items": [
                                {
                                    "publishedFileId": "123456",
                                    "installedFolder": str(installed_folder),
                                }
                            ],
                        }
                    ),
                ),
                patch.object(
                    workshop_router_module,
                    "find_preview_image_in_folder",
                    side_effect=_write_persona_override_during_scan,
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result["added"] == 1
            saved_characters = cm.load_characters()
            assert "启动竞态工坊角色" in saved_characters.get("猫娘", {})
            saved_override = saved_characters["猫娘"][current_name].get("_reserved", {}).get("persona_override")
            assert saved_override["preset_id"] == "classic_genki"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_does_not_write_orphan_face_when_pending_add_is_skipped():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_orphan_face_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "并发工坊角色", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            duplicate_dir = installed_folder / "duplicate"
            duplicate_dir.mkdir()
            (duplicate_dir / "重复角色卡.chara.json").write_text(
                json.dumps({"档案名": "并发工坊角色", "昵称": "重复工坊卡"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            preview_path = installed_folder / "preview.png"
            Image.new("RGBA", (1024, 1024), (80, 160, 220, 255)).save(preview_path)

            def _create_same_character_during_scan(_installed_folder, _chara_name=None, _chara_file_stem=None):
                latest = cm.load_characters()
                latest.setdefault("猫娘", {})["并发工坊角色"] = {"昵称": "并发创建"}
                cm.save_characters(latest, bypass_write_fence=True)
                return str(preview_path)

            with (
                patch.object(
                    workshop_router_module,
                    "get_subscribed_workshop_items",
                    AsyncMock(
                        return_value={
                            "success": True,
                            "items": [
                                {
                                    "publishedFileId": "123456",
                                    "installedFolder": str(installed_folder),
                                }
                            ],
                        }
                    ),
                ),
                patch.object(
                    workshop_router_module,
                    "find_preview_image_in_folder",
                    side_effect=_create_same_character_during_scan,
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result["added"] == 0
            assert sync_result["skipped"] >= 1
            saved_characters = cm.load_characters()
            assert saved_characters["猫娘"]["并发工坊角色"]["昵称"] == "并发创建"
            assert not (cm.card_faces_dir / "并发工坊角色.png").exists()
            assert not cm.card_face_meta_path("并发工坊角色").exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_aborts_when_latest_catgirl_map_is_malformed():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_bad_latest_characters"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "坏结构保护角色", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            initial_characters = cm.load_characters()
            malformed_latest = {**initial_characters, "猫娘": []}
            save_mock = AsyncMock()

            with (
                patch.object(
                    workshop_router_module,
                    "get_subscribed_workshop_items",
                    AsyncMock(
                        return_value={
                            "success": True,
                            "items": [
                                {
                                    "publishedFileId": "123456",
                                    "installedFolder": str(installed_folder),
                                }
                            ],
                        }
                    ),
                ),
                patch.object(cm, "aload_characters", AsyncMock(side_effect=[initial_characters, malformed_latest])),
                patch.object(cm, "asave_characters", save_mock),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result["added"] == 0
            assert sync_result["errors"] == 1
            save_mock.assert_not_awaited()
            assert "坏结构保护角色" not in cm.load_characters().get("猫娘", {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_skips_face_writes_when_maintenance_fence_turns_on_mid_scan():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_face_fence_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "围栏封面角色", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            Image.new("RGBA", (1024, 1024), (80, 160, 220, 255)).save(installed_folder / "preview.png")

            fence_states = iter([False, True])

            def _fake_write_fence(_config_mgr):
                return next(fence_states, True)

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ), patch.object(workshop_router_module, "is_write_fence_active", side_effect=_fake_write_fence):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result == {
                "added": 0,
                "backfilled_faces": 0,
                "skipped": 0,
                "errors": 0,
                "blocked_by_write_fence": True,
            }
            assert "围栏封面角色" not in cm.load_characters().get("猫娘", {})
            assert not (cm.card_faces_dir / "围栏封面角色.png").exists()
            assert not cm.card_face_meta_path("围栏封面角色").exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_counts_errors_when_new_face_backfill_fails():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_face_error_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "封面失败角色", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ), patch.object(
                workshop_router_module,
                "_ensure_workshop_card_face_from_preview",
                side_effect=RuntimeError("preview render failed"),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result["added"] == 1
            assert sync_result["errors"] == 1
            assert "封面失败角色" in cm.load_characters().get("猫娘", {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_counts_errors_when_existing_face_backfill_fails():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            characters = cm.load_characters()
            characters.setdefault("猫娘", {})["已有工坊角色"] = {
                "昵称": "已存在",
                "_reserved": {
                    "character_origin": {
                        "source": "steam_workshop",
                        "source_id": "123456",
                    }
                },
            }
            cm.save_characters(characters, bypass_write_fence=True)

            installed_folder = Path(td) / "mock_workshop_existing_face_error_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "已有工坊角色", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ), patch.object(
                workshop_router_module,
                "_ensure_workshop_card_face_from_preview",
                side_effect=RuntimeError("preview render failed"),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result["added"] == 0
            assert sync_result["skipped"] >= 1
            assert sync_result["errors"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_uses_character_specific_preview_in_multi_card_item():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_multi_card_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "Alice.chara.json").write_text(
                json.dumps({"档案名": "Alice", "昵称": "from workshop"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (installed_folder / "Bob.chara.json").write_text(
                json.dumps({"档案名": "Bob", "昵称": "from workshop"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            Image.new("RGBA", (1024, 1024), (80, 160, 220, 255)).save(installed_folder / "Alice.png")
            Image.new("RGBA", (1024, 1024), (120, 80, 180, 255)).save(installed_folder / "Bob.png")

            preview_by_character = {}

            def _capture_preview(_config_mgr, chara_name, preview_image_path, _item):
                preview_by_character[chara_name] = Path(preview_image_path).name if preview_image_path else None
                return True

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ), patch.object(
                workshop_router_module,
                "_ensure_workshop_card_face_from_preview",
                side_effect=_capture_preview,
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result["added"] == 2
            assert preview_by_character == {
                "Alice": "Alice.png",
                "Bob": "Bob.png",
            }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_persists_character_origin_metadata():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_origin_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps(
                    {
                        "档案名": "工坊同步角色",
                        "昵称": "来自创意工坊",
                        "model_type": "live2d",
                        "live2d": "Blue cat",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "3671939765",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

        assert sync_result["added"] == 1

        from utils.config_manager import get_reserved

        current_characters = cm.load_characters()
        payload = current_characters.get("猫娘", {}).get("工坊同步角色")
        assert isinstance(payload, dict)
        assert payload["昵称"] == "来自创意工坊"
        assert get_reserved(payload, "avatar", "asset_source", default="") == "steam_workshop"
        assert get_reserved(payload, "avatar", "asset_source_id", default="") == "3671939765"
        assert get_reserved(payload, "avatar", "live2d", "model_path", default="") == "/workshop/3671939765/Blue cat/Blue cat.model3.json"
        assert get_reserved(payload, "character_origin", "source", default="") == "steam_workshop"
        assert get_reserved(payload, "character_origin", "source_id", default="") == "3671939765"
        assert get_reserved(payload, "character_origin", "display_name", default="") == "Blue cat"
        assert get_reserved(payload, "character_origin", "model_ref", default="") == "/workshop/3671939765/Blue cat/Blue cat.model3.json"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("card_payload", "expected_model_field", "expected_model_ref", "expected_display_name"),
    (
        (
            {
                "档案名": "工坊VRM角色",
                "昵称": "来自创意工坊 VRM",
                "model_type": "vrm",
                "vrm": "/workshop/3671939765/avatar/BlueCat.vrm",
            },
            "vrm",
            "/workshop/3671939765/avatar/BlueCat.vrm",
            "BlueCat",
        ),
        (
            {
                "档案名": "工坊MMD角色",
                "昵称": "来自创意工坊 MMD",
                "model_type": "mmd",
                "mmd": "/workshop/3671939765/miku/Miku.pmx",
            },
            "mmd",
            "/workshop/3671939765/miku/Miku.pmx",
            "Miku",
        ),
    ),
)
async def test_sync_workshop_character_cards_persists_live3d_workshop_origin_metadata(
    card_payload,
    expected_model_field,
    expected_model_ref,
    expected_display_name,
):
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_live3d_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps(card_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "3671939765",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

        assert sync_result["added"] == 1

        from utils.config_manager import get_reserved

        current_characters = cm.load_characters()
        payload = current_characters.get("猫娘", {}).get(card_payload["档案名"])
        assert isinstance(payload, dict)
        assert get_reserved(payload, "avatar", "asset_source", default="") == "steam_workshop"
        assert get_reserved(payload, "avatar", "asset_source_id", default="") == "3671939765"
        assert get_reserved(payload, "avatar", "model_type", default="") == "live3d"
        assert get_reserved(payload, "avatar", expected_model_field, "model_path", default="") == expected_model_ref
        assert get_reserved(payload, "character_origin", "source", default="") == "steam_workshop"
        assert get_reserved(payload, "character_origin", "source_id", default="") == "3671939765"
        assert get_reserved(payload, "character_origin", "display_name", default="") == expected_display_name
        assert get_reserved(payload, "character_origin", "model_ref", default="") == expected_model_ref


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_catgirl_returns_error_when_memory_cleanup_fails():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")

            characters = cm.load_characters()
            characters.setdefault("猫娘", {})["删除失败角色"] = {"昵称": "删除失败角色"}
            cm.save_characters(characters, bypass_write_fence=True)

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            with (
                patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client),
                patch(
                    "main_routers.characters_router.crud.delete_character_memory_storage",
                    side_effect=OSError("time_indexed.db is locked"),
                ),
            ):
                delete_result = await characters_router_module.delete_catgirl("删除失败角色")

            assert delete_result.status_code == 500
            payload = json.loads(delete_result.body.decode("utf-8"))
            assert payload["success"] is False
            assert "time_indexed.db is locked" in payload["error"]
            assert payload["memory_server_released"] is True
            assert "删除失败角色" in cm.load_characters().get("猫娘", {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_catgirl_returns_503_when_memory_handle_release_fails_before_disk_changes():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            characters = cm.load_characters()
            characters.setdefault("猫娘", {})["删除句柄失败角色"] = {"昵称": "删除句柄失败角色"}
            cm.save_characters(characters, bypass_write_fence=True)

            with (
                patch.object(
                    characters_router_module,
                    "release_memory_server_character",
                    AsyncMock(return_value=False),
                ),
                patch.object(characters_router_module, "delete_character_memory_storage") as mock_delete_memory,
            ):
                delete_result = await characters_router_module.delete_catgirl("删除句柄失败角色")

            assert delete_result.status_code == 503
            payload = json.loads(delete_result.body.decode("utf-8"))
            assert payload["success"] is False
            assert payload["memory_server_released"] is False
            assert "删除句柄失败角色" in cm.load_characters().get("猫娘", {})
            mock_delete_memory.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_catgirl_rolls_back_tombstone_and_memory_when_persist_failure_occurs():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")

            characters = cm.load_characters()
            characters.setdefault("猫娘", {})["删除回滚角色"] = {"昵称": "删除回滚角色"}
            cm.save_characters(characters, bypass_write_fence=True)

            memory_dir = Path(cm.memory_dir) / "删除回滚角色"
            memory_dir.mkdir(parents=True, exist_ok=True)
            (memory_dir / "recent.json").write_text(
                json.dumps([{"speaker": "删除回滚角色", "content": "你好"}], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            original_save_characters = cm.save_characters

            def _fail_primary_save(data, character_json_path=None, *, bypass_write_fence=False):
                if not bypass_write_fence and "删除回滚角色" not in (data.get("猫娘") or {}):
                    raise OSError("disk full")
                return original_save_characters(
                    data,
                    character_json_path=character_json_path,
                    bypass_write_fence=bypass_write_fence,
                )

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client), patch.object(
                cm,
                "save_characters",
                side_effect=_fail_primary_save,
            ):
                delete_result = await characters_router_module.delete_catgirl("删除回滚角色")

            assert delete_result.status_code == 500
            payload = json.loads(delete_result.body.decode("utf-8"))
            assert payload["success"] is False
            assert "disk full" in payload["error"]
            assert payload["memory_server_released"] is True
            assert "删除回滚角色" in cm.load_characters().get("猫娘", {})
            assert (memory_dir / "recent.json").is_file()
            tombstones = cm.load_character_tombstones_state().get("tombstones") or []
            assert not any(entry.get("character_name") == "删除回滚角色" for entry in tombstones)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_catgirl_rolls_back_when_notify_reload_returns_false():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            characters = cm.load_characters()
            characters.setdefault("猫娘", {})["删除重载失败角色"] = {"昵称": "删除重载失败角色"}
            cm.save_characters(characters, bypass_write_fence=True)

            memory_dir = Path(cm.memory_dir) / "删除重载失败角色"
            memory_dir.mkdir(parents=True, exist_ok=True)
            recent_path = memory_dir / "recent.json"
            recent_path.write_text("[]", encoding="utf-8")

            with (
                patch.object(
                    characters_router_module,
                    "release_memory_server_character",
                    AsyncMock(return_value=True),
                ),
                patch.object(
                    characters_router_module,
                    "notify_memory_server_reload",
                    AsyncMock(side_effect=[False, True]),
                ),
            ):
                delete_result = await characters_router_module.delete_catgirl("删除重载失败角色")

            assert delete_result.status_code == 500
            payload = json.loads(delete_result.body.decode("utf-8"))
            assert payload["success"] is False
            assert "notify_memory_server_reload returned False" in payload["error"]
            assert payload["memory_server_released"] is True

            reloaded_characters = cm.load_characters()
            assert "删除重载失败角色" in reloaded_characters.get("猫娘", {})
            assert recent_path.is_file()
            tombstones = cm.load_character_tombstones_state().get("tombstones") or []
            assert not any(entry.get("character_name") == "删除重载失败角色" for entry in tombstones)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_catgirl_maintenance_error_preserves_original_exception_type_when_rollback_reports_string():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            characters = cm.load_characters()
            characters.setdefault("猫娘", {})["维护删除角色"] = {"昵称": "维护删除角色"}
            cm.save_characters(characters, bypass_write_fence=True)

            maintenance_error = MaintenanceModeError(
                "maintenance_readonly",
                operation="delete",
                target="characters/维护删除角色",
            )
            original_save_characters = cm.save_characters

            def _raise_maintenance_on_primary_save(data, character_json_path=None, *, bypass_write_fence=False):
                if not bypass_write_fence and "维护删除角色" not in (data.get("猫娘") or {}):
                    raise maintenance_error
                return original_save_characters(
                    data,
                    character_json_path=character_json_path,
                    bypass_write_fence=bypass_write_fence,
                )

            with (
                patch.object(
                    characters_router_module,
                    "release_memory_server_character",
                    AsyncMock(return_value=True),
                ),
                patch.object(cm, "save_characters", side_effect=_raise_maintenance_on_primary_save),
                patch.object(
                    characters_router_module,
                    "_rollback_character_operation",
                    AsyncMock(return_value="tombstones restore failed: readonly"),
                ),
            ):
                with pytest.raises(MaintenanceModeError) as exc_info:
                    await characters_router_module.delete_catgirl("维护删除角色")

            assert exc_info.value is maintenance_error
            assert isinstance(exc_info.value.__cause__, RuntimeError)
            assert "tombstones restore failed: readonly" in str(exc_info.value.__cause__)


@pytest.mark.unit
def test_resolve_live2d_model_binding_keeps_manual_external_url_without_catalog_rebind():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")

            with patch.object(
                importlib.import_module('main_routers.characters_router.live2d_models'),
                "find_models",
                side_effect=AssertionError("manual_external should skip local model lookup"),
            ):
                model_ref = "https://example.com/live2d/neko/neko.model3.json"
                model_path, source_id, source = importlib.import_module('main_routers.characters_router.live2d_models')._resolve_live2d_model_binding(model_ref)

            assert model_path == model_ref
            assert source == "manual_external"
            assert source_id == ""


@pytest.mark.unit
def test_character_memory_regression_fixture_isolates_project_memory_dir(tmp_path):
    cm = _make_config_manager(tmp_path)

    assert cm.project_memory_dir == tmp_path / "memory" / "store"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_catgirl_l2d_marks_builtin_live2d_as_builtin():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            characters = cm.load_characters()
            characters["当前猫娘"] = "测试内置模型"
            characters["猫娘"]["测试内置模型"] = json.loads(
                json.dumps(characters["猫娘"][next(iter(characters["猫娘"]))], ensure_ascii=False)
            )
            cm.save_characters(characters, bypass_write_fence=True)

            with patch.object(
                importlib.import_module('main_routers.characters_router.live2d_models'),
                "find_models",
                return_value=[
                    {
                        "name": "mao_pro",
                        "path": "/static/mao_pro/mao_pro.model3.json",
                        "source": "static",
                    }
                ],
            ):
                response = await importlib.import_module('main_routers.characters_router.live2d_models').update_catgirl_l2d(
                    "测试内置模型",
                    _DummyRequest({"live2d": "mao_pro", "model_type": "live2d"}),
                )

            assert response.status_code == 200

            from utils.config_manager import get_reserved

            payload = cm.load_characters()["猫娘"]["测试内置模型"]
            assert get_reserved(payload, "avatar", "live2d", "model_path", default="") == "mao_pro/mao_pro.model3.json"
            assert get_reserved(payload, "avatar", "asset_source", default="") == "builtin"
            assert get_reserved(payload, "avatar", "asset_source_id", default="") == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_rollback_reports_notify_reload_false_as_failure():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            characters_snapshot = cm.load_characters()

            with patch.object(
                characters_router_module,
                "notify_memory_server_reload",
                AsyncMock(return_value=False),
            ):
                rollback_error = await characters_router_module._rollback_character_operation(
                    cm,
                    characters_snapshot=characters_snapshot,
                    memory_snapshot_records=[],
                    reason="unit-test rollback",
                )

        assert "notify_memory_server_reload failed: returned False" in rollback_error


@pytest.mark.unit
def test_rewrite_recent_file_character_name_does_not_rewrite_role_fields(tmp_path):
    from utils.character_memory import rewrite_recent_file_character_name

    recent_path = tmp_path / "recent.json"
    recent_path.write_text(
        json.dumps(
            [
                {
                    "role": "旧角色",
                    "speaker": "旧角色",
                    "data": {
                        "role": "旧角色",
                        "speaker": "旧角色",
                        "content": "旧角色说：你好",
                    },
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    assert rewrite_recent_file_character_name(recent_path, "旧角色", "新角色") is True

    payload = json.loads(recent_path.read_text(encoding="utf-8"))
    assert payload[0]["role"] == "旧角色"
    assert payload[0]["speaker"] == "新角色"
    assert payload[0]["data"]["role"] == "旧角色"
    assert payload[0]["data"]["speaker"] == "新角色"
    assert payload[0]["data"]["content"].startswith("新角色说：")


@pytest.mark.unit
def test_move_path_raises_when_target_file_exists(tmp_path):
    from utils.character_memory import _move_path

    source_path = tmp_path / "source.json"
    target_path = tmp_path / "target.json"
    source_path.write_text("source", encoding="utf-8")
    target_path.write_text("target", encoding="utf-8")

    with pytest.raises(FileExistsError):
        _move_path(source_path, target_path)

    assert source_path.is_file()
    assert target_path.is_file()


@pytest.mark.unit
def test_timeindexed_dispose_engine_also_clears_sql_chat_engine_cache(monkeypatch):
    from memory.timeindex import TimeIndexedMemory
    from utils.llm_client import SQLChatMessageHistory

    class _DummyEngine:
        def __init__(self):
            self.dispose_calls = 0

        def dispose(self):
            self.dispose_calls += 1

    primary_engine = _DummyEngine()
    cached_engine = _DummyEngine()
    normalized_path = os.path.abspath("D:/tmp/test-time-indexed.db").replace("\\", "/")
    connection_string = f"sqlite:///{normalized_path}"

    original_cache = dict(SQLChatMessageHistory._engine_cache)
    try:
        monkeypatch.setitem(SQLChatMessageHistory._engine_cache, connection_string, cached_engine)

        fake_config_manager = SimpleNamespace(
            get_character_data=lambda: ({}, {}, {}, {}, {}, {}, {}, {}, {}),
        )
        monkeypatch.setattr("memory.timeindex.get_config_manager", lambda: fake_config_manager)

        manager = TimeIndexedMemory(recent_history_manager=None)
        manager.engines = {"测试角色": primary_engine}
        manager.db_paths = {"测试角色": "D:/tmp/test-time-indexed.db"}

        manager.dispose_engine("测试角色")

        assert primary_engine.dispose_calls == 1
        assert cached_engine.dispose_calls == 1
        assert "测试角色" not in manager.engines
        assert "测试角色" not in manager.db_paths
        assert connection_string not in SQLChatMessageHistory._engine_cache
    finally:
        SQLChatMessageHistory._engine_cache.clear()
        SQLChatMessageHistory._engine_cache.update(original_cache)


@pytest.mark.unit
def test_timeindexed_engine_init_failure_disposes_engine_and_clears_temp_cache(monkeypatch, tmp_path):
    from memory.timeindex import TimeIndexedMemory
    from utils.llm_client import SQLChatMessageHistory

    class _DummyEngine:
        def __init__(self):
            self.dispose_calls = 0

        def dispose(self):
            self.dispose_calls += 1

    created_engine = _DummyEngine()
    cached_engine = _DummyEngine()
    db_path = (tmp_path / "time_indexed.db").resolve()
    connection_string = f"sqlite:///{db_path.as_posix()}"

    original_cache = dict(SQLChatMessageHistory._engine_cache)
    try:
        fake_config_manager = SimpleNamespace(
            get_character_data=lambda: ({}, {}, {}, {}, {}, {}, {}, {}, {}),
        )
        monkeypatch.setattr("memory.timeindex.get_config_manager", lambda: fake_config_manager)
        monkeypatch.setattr("memory.timeindex.create_engine", lambda _connection_string: created_engine)

        manager = TimeIndexedMemory(recent_history_manager=None)
        monkeypatch.setattr(manager, "_assert_timeindex_writable", lambda _lanlan_name: None)

        def _explode_after_cache(_engine, _connection_string, _lanlan_name):
            SQLChatMessageHistory._engine_cache[_connection_string] = cached_engine
            raise RuntimeError("force init failure")

        monkeypatch.setattr(manager, "_ensure_tables_exist_with", _explode_after_cache)

        assert manager._ensure_engine_exists("测试角色", db_path=str(db_path), readonly=False) is False
        assert created_engine.dispose_calls == 1
        assert cached_engine.dispose_calls == 1
        assert connection_string not in SQLChatMessageHistory._engine_cache
        assert "测试角色" not in manager.engines
        assert "测试角色" not in manager.db_paths
    finally:
        SQLChatMessageHistory._engine_cache.clear()
        SQLChatMessageHistory._engine_cache.update(original_cache)


@pytest.mark.unit
def test_timeindexed_readonly_open_still_runs_writable_bootstrap_on_first_write(monkeypatch, tmp_path):
    from memory.timeindex import TimeIndexedMemory

    class _DummyEngine:
        def __init__(self, name):
            self.name = name
            self.dispose_calls = 0

        def dispose(self):
            self.dispose_calls += 1

    db_path = (tmp_path / "time_indexed.db").resolve()
    db_path.write_text("", encoding="utf-8")
    readonly_engine = _DummyEngine("readonly")
    writable_engine = _DummyEngine("writable")
    created_engines = [readonly_engine, writable_engine]
    ensure_calls = []
    migrate_calls = []

    fake_config_manager = SimpleNamespace(
        get_character_data=lambda: ({}, {}, {}, {}, {}, {}, {}, {}, {}),
    )
    monkeypatch.setattr("memory.timeindex.get_config_manager", lambda: fake_config_manager)
    monkeypatch.setattr("memory.timeindex.create_engine", lambda _connection_string: created_engines.pop(0))

    manager = TimeIndexedMemory(recent_history_manager=None)
    monkeypatch.setattr(manager, "_assert_timeindex_writable", lambda _lanlan_name: None)
    monkeypatch.setattr(
        manager,
        "_ensure_tables_exist_with",
        lambda _engine, _connection_string, _lanlan_name: ensure_calls.append((_lanlan_name, _engine)),
    )
    monkeypatch.setattr(
        manager,
        "_check_and_migrate_schema",
        lambda _engine, _lanlan_name: migrate_calls.append((_lanlan_name, _engine)),
    )

    assert manager._ensure_engine_exists("测试角色", db_path=str(db_path), readonly=True) is True
    assert ensure_calls == []
    assert migrate_calls == []
    assert manager.engines["测试角色"] is readonly_engine
    assert manager._engine_readonly_flags["测试角色"] is True

    assert manager._ensure_engine_exists("测试角色", db_path=str(db_path), readonly=False) is True
    assert ensure_calls == [("测试角色", writable_engine)]
    assert migrate_calls == [("测试角色", writable_engine)]
    assert readonly_engine.dispose_calls == 1
    assert manager.engines["测试角色"] is writable_engine
    assert manager._engine_readonly_flags["测试角色"] is False

    assert manager._ensure_engine_exists("测试角色", db_path=str(db_path), readonly=False) is True
    assert ensure_calls == [("测试角色", writable_engine)]
    assert migrate_calls == [("测试角色", writable_engine)]


def test_timeindexed_dispose_and_rebuild_when_memory_dir_drifts(monkeypatch, tmp_path):
    """``TimeIndexedMemory.db_paths`` 是 per-character path cache，cache 命中后
    短路 return 不会重新校核当前 ``memory_dir``。罕见但可能：``/reload``
    期间底层 ``storage_policy`` 被改写，或测试 monkeypatch 了 memory_dir，
    cached 路径就和实际目标分叉。老 SQLAlchemy engine 还连着旧文件，新
    数据全飘到老位置——``/process`` 的 ``except Exception`` 又把 SQL
    错误吞掉，表象是 db 永远不更新（time perception 错乱）。

    本用例验证 ``_ensure_engine_exists`` 检测到 cached vs expected 漂移
    后会 dispose 旧 engine + 用 expected 路径重建。
    """
    from memory.timeindex import TimeIndexedMemory

    class _DummyEngine:
        def __init__(self, name):
            self.name = name
            self.dispose_calls = 0

        def dispose(self):
            self.dispose_calls += 1

    old_db_path = (tmp_path / "old" / "测试角色" / "time_indexed.db").resolve()
    old_db_path.parent.mkdir(parents=True, exist_ok=True)
    old_db_path.write_text("", encoding="utf-8")
    new_db_path = (tmp_path / "new" / "测试角色" / "time_indexed.db").resolve()
    new_db_path.parent.mkdir(parents=True, exist_ok=True)
    new_db_path.write_text("", encoding="utf-8")

    old_engine = _DummyEngine("old")
    new_engine = _DummyEngine("new")
    created_engines = [old_engine, new_engine]
    ensure_calls: list = []
    migrate_calls: list = []

    # 受控的 time_store——第一次返 old，第二次返 new，模拟 memory_dir 漂移。
    current_time_store = {"测试角色": str(old_db_path)}

    def _fake_character_data():
        return ({}, {}, {}, {}, {}, {}, dict(current_time_store), {}, {})

    fake_config_manager = SimpleNamespace(get_character_data=_fake_character_data)
    monkeypatch.setattr("memory.timeindex.get_config_manager", lambda: fake_config_manager)
    monkeypatch.setattr(
        "memory.timeindex.create_engine",
        lambda _connection_string: created_engines.pop(0),
    )

    manager = TimeIndexedMemory(recent_history_manager=None)
    monkeypatch.setattr(manager, "_assert_timeindex_writable", lambda _lanlan_name: None)
    monkeypatch.setattr(
        manager,
        "_ensure_tables_exist_with",
        lambda _engine, _connection_string, _lanlan_name: ensure_calls.append((_lanlan_name, _engine)),
    )
    monkeypatch.setattr(
        manager,
        "_check_and_migrate_schema",
        lambda _engine, _lanlan_name: migrate_calls.append((_lanlan_name, _engine)),
    )

    # 第一次初始化：从 time_store 解析到 old_db_path，engine 缓存
    assert manager._ensure_engine_exists("测试角色") is True
    assert manager.engines["测试角色"] is old_engine
    assert os.path.normcase(str(manager.db_paths["测试角色"])) == os.path.normcase(str(old_db_path))
    assert old_engine.dispose_calls == 0

    # 模拟 memory_dir 漂移——time_store 现在指向 new_db_path
    current_time_store["测试角色"] = str(new_db_path)

    # 第二次 _ensure_engine_exists：cache 命中但 expected 已变；应 dispose + 重建
    assert manager._ensure_engine_exists("测试角色") is True
    assert old_engine.dispose_calls == 1
    assert manager.engines["测试角色"] is new_engine
    assert os.path.normcase(str(manager.db_paths["测试角色"])) == os.path.normcase(str(new_db_path))
    # 新 engine 走完整 writable 初始化（确保表结构在新文件里就位）
    assert ensure_calls[-1] == ("测试角色", new_engine)
    assert migrate_calls[-1] == ("测试角色", new_engine)


def test_timeindexed_short_circuits_when_memory_dir_unchanged(monkeypatch, tmp_path):
    """对偶用例：cached 与 expected 一致时 drift 检测不该误伤——cache 命中
    应仍然短路，不重建 engine。
    """
    from memory.timeindex import TimeIndexedMemory

    class _DummyEngine:
        def __init__(self):
            self.dispose_calls = 0

        def dispose(self):
            self.dispose_calls += 1

    db_path = (tmp_path / "测试角色" / "time_indexed.db").resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_text("", encoding="utf-8")

    engine = _DummyEngine()
    created_engines = [engine]
    create_calls: list = []
    ensure_calls: list = []
    migrate_calls: list = []

    fake_config_manager = SimpleNamespace(
        get_character_data=lambda: ({}, {}, {}, {}, {}, {}, {"测试角色": str(db_path)}, {}, {}),
    )
    monkeypatch.setattr("memory.timeindex.get_config_manager", lambda: fake_config_manager)

    def _fake_create_engine(connection_string):
        create_calls.append(connection_string)
        return created_engines.pop(0)

    monkeypatch.setattr("memory.timeindex.create_engine", _fake_create_engine)

    manager = TimeIndexedMemory(recent_history_manager=None)
    monkeypatch.setattr(manager, "_assert_timeindex_writable", lambda _lanlan_name: None)
    monkeypatch.setattr(
        manager,
        "_ensure_tables_exist_with",
        lambda _engine, _connection_string, _lanlan_name: ensure_calls.append(_lanlan_name),
    )
    monkeypatch.setattr(
        manager,
        "_check_and_migrate_schema",
        lambda _engine, _lanlan_name: migrate_calls.append(_lanlan_name),
    )

    assert manager._ensure_engine_exists("测试角色") is True
    assert manager._ensure_engine_exists("测试角色") is True
    assert manager._ensure_engine_exists("测试角色") is True
    # 仅第一次创建 engine + bootstrap，后续短路
    assert len(create_calls) == 1
    assert ensure_calls == ["测试角色"]
    assert migrate_calls == ["测试角色"]
    assert engine.dispose_calls == 0
