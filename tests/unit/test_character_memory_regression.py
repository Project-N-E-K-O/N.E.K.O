import importlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from main_routers.shared_state import init_shared_state
from utils.config_manager import ConfigManager
from utils.cloudsave_runtime import (
    MaintenanceModeError,
    ROOT_MODE_BOOTSTRAP_IMPORTING,
    bootstrap_local_cloudsave_environment,
)


def _make_config_manager(tmp_root: Path):
    with patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_root), patch.object(
        ConfigManager,
        "get_legacy_app_root_candidates",
        return_value=[],
    ), patch.object(
        ConfigManager,
        "_get_project_root",
        return_value=tmp_root,
    ):
        config_manager = ConfigManager("N.E.K.O")
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

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                sync_message_queue={},
                sync_shutdown_event={},
                session_manager={},
                session_id={},
                sync_process={},
                websocket_locks={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
            )

            characters_router_module = reload_module("main_routers.characters_router")
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

            with patch("main_routers.characters_router.httpx.AsyncClient", return_value=fake_client):
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

            with patch("main_routers.characters_router.httpx.AsyncClient", return_value=fake_client):
                delete_result = await characters_router_module.delete_catgirl("测试角色")
            assert delete_result["success"] is True
            assert "测试角色" not in cm.load_characters().get("猫娘", {})
            assert not (Path(cm.memory_dir) / "测试角色").exists()
            tombstones = cm.load_character_tombstones_state().get("tombstones") or []
            assert any(entry.get("character_name") == "测试角色" for entry in tombstones)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_read_endpoints_disable_caching():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                sync_message_queue={},
                sync_shutdown_event={},
                session_manager={},
                session_id={},
                sync_process={},
                websocket_locks={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
            )

            characters_router_module = reload_module("main_routers.characters_router")

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
async def test_rename_catgirl_moves_runtime_and_legacy_memory_storage():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                sync_message_queue={},
                sync_shutdown_event={},
                session_manager={},
                session_id={},
                sync_process={},
                websocket_locks={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
            )

            characters_router_module = reload_module("main_routers.characters_router")
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

            with patch("main_routers.characters_router.httpx.AsyncClient", return_value=fake_client):
                add_result = await characters_router_module.add_catgirl(
                    _DummyRequest({"档案名": "旧角色"})
                )
            assert add_result["success"] is True

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

            with patch("main_routers.characters_router.httpx.AsyncClient", return_value=fake_client):
                rename_result = await characters_router_module.rename_catgirl(
                    "旧角色",
                    _DummyRequest({"new_name": "新角色"}),
                )

            assert rename_result["success"] is True
            assert rename_result["memory_renamed"] is True
            assert "新角色" in cm.load_characters().get("猫娘", {})
            assert "旧角色" not in cm.load_characters().get("猫娘", {})
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
async def test_rename_catgirl_rolls_back_memory_and_suppresses_switch_notice_on_persist_failure():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        websocket = AsyncMock()

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                sync_message_queue={},
                sync_shutdown_event={},
                session_manager={
                    "旧角色": SimpleNamespace(is_active=False, websocket=websocket, session=None),
                },
                session_id={},
                sync_process={},
                websocket_locks={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
            )

            characters_router_module = reload_module("main_routers.characters_router")

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            with patch("main_routers.characters_router.httpx.AsyncClient", return_value=fake_client):
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

            with patch("main_routers.characters_router.httpx.AsyncClient", return_value=fake_client), patch.object(
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

        websocket = AsyncMock()

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                sync_message_queue={},
                sync_shutdown_event={},
                session_manager={
                    "旧角色": SimpleNamespace(is_active=False, websocket=websocket, session=None),
                },
                session_id={},
                sync_process={},
                websocket_locks={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
            )

            characters_router_module = reload_module("main_routers.characters_router")

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            with patch("main_routers.characters_router.httpx.AsyncClient", return_value=fake_client):
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

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                sync_message_queue={},
                sync_shutdown_event={},
                session_manager={},
                session_id={},
                sync_process={},
                websocket_locks={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
            )

            characters_router_module = reload_module("main_routers.characters_router")
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

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                sync_message_queue={},
                sync_shutdown_event={},
                session_manager={},
                session_id={},
                sync_process={},
                websocket_locks={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
            )

            characters_router_module = reload_module("main_routers.characters_router")
            workshop_router_module = reload_module("main_routers.workshop_router")

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

            with patch("main_routers.characters_router.httpx.AsyncClient", return_value=fake_client):
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
async def test_sync_workshop_character_cards_persists_character_origin_metadata():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                sync_message_queue={},
                sync_shutdown_event={},
                session_manager={},
                session_id={},
                sync_process={},
                websocket_locks={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
            )

            workshop_router_module = reload_module("main_routers.workshop_router")

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

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                sync_message_queue={},
                sync_shutdown_event={},
                session_manager={},
                session_id={},
                sync_process={},
                websocket_locks={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
            )

            workshop_router_module = reload_module("main_routers.workshop_router")

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

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                sync_message_queue={},
                sync_shutdown_event={},
                session_manager={},
                session_id={},
                sync_process={},
                websocket_locks={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
            )

            characters_router_module = reload_module("main_routers.characters_router")

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
                patch("main_routers.characters_router.httpx.AsyncClient", return_value=fake_client),
                patch(
                    "main_routers.characters_router.delete_character_memory_storage",
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

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                sync_message_queue={},
                sync_shutdown_event={},
                session_manager={},
                session_id={},
                sync_process={},
                websocket_locks={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
            )

            characters_router_module = reload_module("main_routers.characters_router")
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

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                sync_message_queue={},
                sync_shutdown_event={},
                session_manager={},
                session_id={},
                sync_process={},
                websocket_locks={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
            )

            characters_router_module = reload_module("main_routers.characters_router")

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

            with patch("main_routers.characters_router.httpx.AsyncClient", return_value=fake_client), patch.object(
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
async def test_delete_catgirl_maintenance_error_preserves_original_exception_type_when_rollback_reports_string():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                sync_message_queue={},
                sync_shutdown_event={},
                session_manager={},
                session_id={},
                sync_process={},
                websocket_locks={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
            )

            characters_router_module = reload_module("main_routers.characters_router")
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

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                sync_message_queue={},
                sync_shutdown_event={},
                session_manager={},
                session_id={},
                sync_process={},
                websocket_locks={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
            )

            characters_router_module = reload_module("main_routers.characters_router")
            characters = cm.load_characters()
            characters["当前猫娘"] = "测试内置模型"
            characters["猫娘"]["测试内置模型"] = json.loads(
                json.dumps(characters["猫娘"][next(iter(characters["猫娘"]))], ensure_ascii=False)
            )
            cm.save_characters(characters, bypass_write_fence=True)

            with patch.object(
                characters_router_module,
                "find_models",
                return_value=[
                    {
                        "name": "mao_pro",
                        "path": "/static/mao_pro/mao_pro.model3.json",
                        "source": "static",
                    }
                ],
            ):
                response = await characters_router_module.update_catgirl_l2d(
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

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                sync_message_queue={},
                sync_shutdown_event={},
                session_manager={},
                session_id={},
                sync_process={},
                websocket_locks={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
            )

            characters_router_module = reload_module("main_routers.characters_router")
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
        pass

    db_path = (tmp_path / "time_indexed.db").resolve()
    db_path.write_text("", encoding="utf-8")
    created_engine = _DummyEngine()
    ensure_calls = []
    migrate_calls = []

    fake_config_manager = SimpleNamespace(
        get_character_data=lambda: ({}, {}, {}, {}, {}, {}, {}, {}, {}),
    )
    monkeypatch.setattr("memory.timeindex.get_config_manager", lambda: fake_config_manager)
    monkeypatch.setattr("memory.timeindex.create_engine", lambda _connection_string: created_engine)

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

    assert manager._ensure_engine_exists("测试角色", db_path=str(db_path), readonly=True) is True
    assert ensure_calls == []
    assert migrate_calls == []

    assert manager._ensure_engine_exists("测试角色", db_path=str(db_path), readonly=False) is True
    assert ensure_calls == ["测试角色"]
    assert migrate_calls == ["测试角色"]

    assert manager._ensure_engine_exists("测试角色", db_path=str(db_path), readonly=False) is True
    assert ensure_calls == ["测试角色"]
    assert migrate_calls == ["测试角色"]
