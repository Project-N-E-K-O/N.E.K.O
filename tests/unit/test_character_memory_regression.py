import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import pytest

from main_routers.shared_state import init_shared_state
from utils.config_manager import ConfigManager
from utils.cloudsave_runtime import (
    ROOT_MODE_BOOTSTRAP_IMPORTING,
    bootstrap_local_cloudsave_environment,
)


def _make_config_manager(tmp_root: Path):
    with patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_root):
        config_manager = ConfigManager("N.E.K.O")
    config_manager.get_legacy_app_root_candidates = lambda: []
    return config_manager


class _DummyRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


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

            characters_router_module = importlib.import_module("main_routers.characters_router")
            memory_router_module = importlib.import_module("main_routers.memory_router")
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

            characters_router_module = importlib.import_module("main_routers.characters_router")
            memory_router_module = importlib.import_module("main_routers.memory_router")

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

            characters_router_module = importlib.import_module("main_routers.characters_router")
            workshop_router_module = importlib.import_module("main_routers.workshop_router")

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

            workshop_router_module = importlib.import_module("main_routers.workshop_router")

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
        assert get_reserved(payload, "avatar", "live2d", "model_path", default="") == "Blue cat/Blue cat.model3.json"
        assert get_reserved(payload, "character_origin", "source", default="") == "steam_workshop"
        assert get_reserved(payload, "character_origin", "source_id", default="") == "3671939765"
        assert get_reserved(payload, "character_origin", "display_name", default="") == "Blue cat"
        assert get_reserved(payload, "character_origin", "model_ref", default="") == "Blue cat/Blue cat.model3.json"


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

            characters_router_module = importlib.import_module("main_routers.characters_router")

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
def test_timeindexed_dispose_engine_also_clears_sql_chat_engine_cache():
    from memory.timeindex import TimeIndexedMemory
    from utils.llm_client import SQLChatMessageHistory

    class _DummyEngine:
        def __init__(self):
            self.dispose_calls = 0

        def dispose(self):
            self.dispose_calls += 1

    primary_engine = _DummyEngine()
    cached_engine = _DummyEngine()
    connection_string = "sqlite:///D:/tmp/test-time-indexed.db"

    original_cache = dict(SQLChatMessageHistory._engine_cache)
    try:
        SQLChatMessageHistory._engine_cache[connection_string] = cached_engine

        manager = object.__new__(TimeIndexedMemory)
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
