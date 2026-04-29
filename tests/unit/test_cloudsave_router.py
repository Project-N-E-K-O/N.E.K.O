import asyncio
import importlib
import sys
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.responses import JSONResponse

from main_routers.shared_state import init_shared_state


def _make_role_state_for_test(session_managers: dict) -> dict:
    """See tests/unit/test_character_memory_regression.py for rationale."""
    from main_server import RoleState, _SyncMessageQueue
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
    MaintenanceModeError,
    bootstrap_local_cloudsave_environment,
    export_local_cloudsave_snapshot,
)
from utils.file_utils import atomic_write_json


@pytest.fixture(autouse=True)
def _fresh_cloudsave_router_module():
    sys.modules.pop("main_routers.cloudsave_router", None)
    yield
    sys.modules.pop("main_routers.cloudsave_router", None)


def _make_config_manager(tmp_root: Path):
    with patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_root), patch.object(
        ConfigManager,
        "get_legacy_app_root_candidates",
        return_value=[],
    ), patch.object(
        ConfigManager,
        "_get_standard_data_directory_candidates",
        return_value=[tmp_root],
    ):
        config_manager = ConfigManager("N.E.K.O")
    config_manager.get_legacy_app_root_candidates = lambda: []
    config_manager._get_standard_data_directory_candidates = lambda: [tmp_root]
    return config_manager


def _write_runtime_state(cm, *, character_name="小满"):
    from utils.config_manager import set_reserved

    characters = cm.get_default_characters()
    characters["猫娘"] = {
        character_name: characters["猫娘"][next(iter(characters["猫娘"]))]
    }
    characters["当前猫娘"] = character_name
    set_reserved(characters["猫娘"][character_name], "avatar", "model_type", "live2d")
    set_reserved(characters["猫娘"][character_name], "avatar", "asset_source", "steam_workshop")
    set_reserved(characters["猫娘"][character_name], "avatar", "asset_source_id", "123456")
    set_reserved(characters["猫娘"][character_name], "avatar", "live2d", "model_path", "example/example.model3.json")
    cm.save_characters(characters, bypass_write_fence=True)

    character_memory_dir = Path(cm.memory_dir) / character_name
    character_memory_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        character_memory_dir / "recent.json",
        [{"role": "user", "content": "你好"}],
        ensure_ascii=False,
        indent=2,
    )

    workshop_model_dir = Path(cm.workshop_dir) / "123456" / "example"
    workshop_model_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(workshop_model_dir / "example.model3.json", {"Version": 3}, ensure_ascii=False, indent=2)


class _DummyRequest:
    def __init__(self, payload=None, *, json_exception=None):
        self.payload = {} if payload is None else payload
        self._json_exception = json_exception

    async def json(self):
        if self._json_exception is not None:
            raise self._json_exception
        return self.payload


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_router_exposes_summary_and_character_detail():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="小满")
        export_local_cloudsave_snapshot(cm)

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

            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            summary = await cloudsave_router_module.get_cloudsave_summary()
            assert summary["success"] is True
            assert summary["items"][0]["character_name"] == "小满"
            assert summary["items"][0]["relation_state"] == "matched"

            detail = await cloudsave_router_module.get_cloudsave_character_detail("小满")
            assert detail["success"] is True
            assert detail["item"]["character_name"] == "小满"

            missing = await cloudsave_router_module.get_cloudsave_character_detail("不存在角色")
            assert missing.status_code == 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_router_summary_marks_workshop_item_as_needing_resubscribe():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="小满")
        export_local_cloudsave_snapshot(cm)

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

            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")
            with patch.object(
                cloudsave_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(return_value={"success": True, "items": [], "total": 0}),
            ), patch.object(
                cloudsave_router_module,
                "get_workshop_item_details",
                AsyncMock(
                    return_value={
                        "success": True,
                        "item": {
                            "publishedFileId": "123456",
                            "title": "示例工坊物品",
                            "authorName": "Demo Author",
                            "state": {
                                "subscribed": False,
                                "installed": False,
                            },
                        },
                    }
                ),
            ):
                summary = await cloudsave_router_module.get_cloudsave_summary()

        item = summary["items"][0]
        assert item["local_workshop_status"] == "available_needs_resubscribe"
        assert item["cloud_workshop_status"] == "available_needs_resubscribe"
        assert item["local_workshop_title"] == "示例工坊物品"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_router_summary_marks_workshop_item_as_unavailable():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="小满")
        export_local_cloudsave_snapshot(cm)

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

            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")
            with patch.object(
                cloudsave_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(return_value={"success": True, "items": [], "total": 0}),
            ), patch.object(
                cloudsave_router_module,
                "get_workshop_item_details",
                AsyncMock(
                    return_value=JSONResponse(
                        {"success": False, "error": "获取物品详情失败，未找到物品"},
                        status_code=404,
                    )
                ),
            ):
                summary = await cloudsave_router_module.get_cloudsave_summary()

        item = summary["items"][0]
        assert item["local_workshop_status"] == "unavailable"
        assert item["cloud_workshop_status"] == "unavailable"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_router_summary_marks_workshop_item_as_steam_unavailable():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="小满")
        export_local_cloudsave_snapshot(cm)

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

            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")
            with patch.object(
                cloudsave_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value=JSONResponse(
                        {"success": False, "message": "Steamworks未初始化"},
                        status_code=503,
                    )
                ),
            ):
                summary = await cloudsave_router_module.get_cloudsave_summary()

        item = summary["items"][0]
        assert item["local_workshop_status"] == "steam_unavailable"
        assert item["cloud_workshop_status"] == "steam_unavailable"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_router_summary_enriches_workshop_origin_status_for_local_manual_model():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        from utils.config_manager import set_reserved

        characters = cm.get_default_characters()
        characters["猫娘"] = {
            "水水": characters["猫娘"][next(iter(characters["猫娘"]))]
        }
        characters["当前猫娘"] = "水水"
        set_reserved(characters["猫娘"]["水水"], "avatar", "model_type", "live2d")
        set_reserved(characters["猫娘"]["水水"], "avatar", "asset_source", "local")
        set_reserved(characters["猫娘"]["水水"], "avatar", "asset_source_id", "")
        set_reserved(characters["猫娘"]["水水"], "avatar", "live2d", "model_path", "猫娘-YUI-洛丽塔-导出03/猫娘-YUI-洛丽塔-导出03.model3.json")
        set_reserved(characters["猫娘"]["水水"], "character_origin", "source", "steam_workshop")
        set_reserved(characters["猫娘"]["水水"], "character_origin", "source_id", "3671939765")
        set_reserved(characters["猫娘"]["水水"], "character_origin", "display_name", "Blue cat")
        set_reserved(
            characters["猫娘"]["水水"],
            "character_origin",
            "model_ref",
            "Blue cat/Blue cat.model3.json",
        )
        cm.save_characters(characters, bypass_write_fence=True)

        local_model_dir = Path(cm.live2d_dir) / "猫娘-YUI-洛丽塔-导出03"
        local_model_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            local_model_dir / "猫娘-YUI-洛丽塔-导出03.model3.json",
            {"Version": 3},
            ensure_ascii=False,
            indent=2,
        )

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

            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")
            with patch.object(
                cloudsave_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(return_value={"success": True, "items": [], "total": 0}),
            ), patch.object(
                cloudsave_router_module,
                "get_workshop_item_details",
                AsyncMock(
                    return_value={
                        "success": True,
                        "item": {
                            "publishedFileId": "3671939765",
                            "title": "水水",
                            "authorName": "Demo Author",
                            "state": {
                                "subscribed": False,
                                "installed": False,
                            },
                        },
                    }
                ),
            ):
                summary = await cloudsave_router_module.get_cloudsave_summary()

        item = summary["items"][0]
        assert item["local_asset_source"] == "local_imported"
        assert item["local_origin_source"] == "steam_workshop"
        assert item["local_origin_source_id"] == "3671939765"
        assert item["local_origin_workshop_status"] == "available_needs_resubscribe"
        assert item["local_origin_workshop_title"] == "水水"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_router_upload_download_and_blocking_paths():
    with TemporaryDirectory() as td:
        source_cm = _make_config_manager(Path(td) / "source")
        target_cm = _make_config_manager(Path(td) / "target")
        bootstrap_local_cloudsave_environment(source_cm)
        bootstrap_local_cloudsave_environment(target_cm)
        _write_runtime_state(source_cm, character_name="云端角色")
        _write_runtime_state(target_cm, character_name="本地角色")

        from utils.cloudsave_runtime import export_cloudsave_character_unit

        export_cloudsave_character_unit(source_cm, "云端角色")
        shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", target_cm):
            init_shared_state(
                role_state=_make_role_state_for_test({
                    "云端角色": SimpleNamespace(is_active=True, websocket=None),
                }),
                steamworks=None,
                templates=None,
                config_manager=target_cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            blocked = await cloudsave_router_module.post_cloudsave_character_download(
                "云端角色",
                _DummyRequest({"overwrite": False, "backup_before_overwrite": True}),
            )
            blocked_payload = json.loads(blocked.body)
            assert blocked.status_code == 409
            assert blocked_payload["code"] == "ACTIVE_SESSION_BLOCKED"

            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=target_cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            upload = await cloudsave_router_module.post_cloudsave_character_upload(
                "本地角色",
                _DummyRequest({"overwrite": False}),
            )
            assert upload["success"] is True
            assert upload["detail"]["item"]["character_name"] == "本地角色"
            assert upload["detail"]["item"]["relation_state"] == "matched"

            with patch.object(cloudsave_router_module, "_reload_after_character_download", AsyncMock(return_value=(True, ""))):
                download = await cloudsave_router_module.post_cloudsave_character_download(
                    "云端角色",
                    _DummyRequest({"overwrite": False, "backup_before_overwrite": True}),
                )

            assert download["success"] is True
            assert download["detail"]["item"]["character_name"] == "云端角色"
            assert download["detail"]["item"]["relation_state"] == "matched"
            assert "云端角色" in (target_cm.load_characters().get("猫娘") or {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_router_handles_not_found_and_release_failures():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="本地角色")

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

            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            upload_missing = await cloudsave_router_module.post_cloudsave_character_upload(
                "不存在角色",
                _DummyRequest({"overwrite": False}),
            )
            upload_missing_payload = json.loads(upload_missing.body)
            assert upload_missing.status_code == 404
            assert upload_missing_payload["code"] == "LOCAL_CHARACTER_NOT_FOUND"

            download_missing = await cloudsave_router_module.post_cloudsave_character_download(
                "云端不存在角色",
                _DummyRequest({"overwrite": False, "backup_before_overwrite": True}),
            )
            download_missing_payload = json.loads(download_missing.body)
            assert download_missing.status_code == 404
            assert download_missing_payload["code"] == "CLOUD_CHARACTER_NOT_FOUND"

            with patch.object(cloudsave_router_module, "release_memory_server_character", AsyncMock(return_value=False)):
                release_failed = await cloudsave_router_module.post_cloudsave_character_download(
                    "本地角色",
                    _DummyRequest({"overwrite": True, "backup_before_overwrite": True}),
                )
            release_failed_payload = json.loads(release_failed.body)
            assert release_failed.status_code == 503
            assert release_failed_payload["code"] == "MEMORY_SERVER_RELEASE_FAILED"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_router_upload_rejects_invalid_overwrite_and_invalid_json():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="小满")

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

            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            invalid_parameter = await cloudsave_router_module.post_cloudsave_character_upload(
                "小满",
                _DummyRequest({"overwrite": "false"}),
            )
            invalid_parameter_payload = json.loads(invalid_parameter.body)
            assert invalid_parameter.status_code == 400
            assert invalid_parameter_payload["code"] == "INVALID_PARAMETER"

            invalid_json = await cloudsave_router_module.post_cloudsave_character_upload(
                "小满",
                _DummyRequest(json_exception=ValueError("bad json")),
            )
            invalid_json_payload = json.loads(invalid_json.body)
            assert invalid_json.status_code == 400
            assert invalid_json_payload["code"] == "INVALID_JSON_BODY"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_router_download_rejects_invalid_flags_and_invalid_json():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="小满")

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

            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            invalid_overwrite = await cloudsave_router_module.post_cloudsave_character_download(
                "小满",
                _DummyRequest({"overwrite": "false", "backup_before_overwrite": True}),
            )
            invalid_overwrite_payload = json.loads(invalid_overwrite.body)
            assert invalid_overwrite.status_code == 400
            assert invalid_overwrite_payload["code"] == "INVALID_PARAMETER"

            invalid_backup = await cloudsave_router_module.post_cloudsave_character_download(
                "小满",
                _DummyRequest({"overwrite": False, "backup_before_overwrite": "0"}),
            )
            invalid_backup_payload = json.loads(invalid_backup.body)
            assert invalid_backup.status_code == 400
            assert invalid_backup_payload["code"] == "INVALID_PARAMETER"

            invalid_json = await cloudsave_router_module.post_cloudsave_character_download(
                "小满",
                _DummyRequest(json_exception=ValueError("bad json")),
            )
            invalid_json_payload = json.loads(invalid_json.body)
            assert invalid_json.status_code == 400
            assert invalid_json_payload["code"] == "INVALID_JSON_BODY"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_router_download_without_overwrite_returns_conflict_before_release():
    with TemporaryDirectory() as td:
        source_cm = _make_config_manager(Path(td) / "source")
        target_cm = _make_config_manager(Path(td) / "target")
        bootstrap_local_cloudsave_environment(source_cm)
        bootstrap_local_cloudsave_environment(target_cm)
        _write_runtime_state(source_cm, character_name="共享角色")
        _write_runtime_state(target_cm, character_name="共享角色")

        from utils.cloudsave_runtime import export_cloudsave_character_unit

        export_cloudsave_character_unit(source_cm, "共享角色")
        shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", target_cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=target_cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            with patch.object(
                cloudsave_router_module,
                "release_memory_server_character",
                AsyncMock(return_value=True),
            ) as release_mock, patch.object(
                cloudsave_router_module,
                "import_cloudsave_character_unit",
            ) as import_mock:
                blocked = await cloudsave_router_module.post_cloudsave_character_download(
                    "共享角色",
                    _DummyRequest({"overwrite": False, "backup_before_overwrite": True}),
                )

        blocked_payload = json.loads(blocked.body)
        assert blocked.status_code == 409
        assert blocked_payload["code"] == "LOCAL_CHARACTER_EXISTS"
        release_mock.assert_not_awaited()
        import_mock.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_router_upload_overwrite_succeeds_for_diverged_character():
    with TemporaryDirectory() as td:
        source_cm = _make_config_manager(Path(td) / "source")
        target_cm = _make_config_manager(Path(td) / "target")
        bootstrap_local_cloudsave_environment(source_cm)
        bootstrap_local_cloudsave_environment(target_cm)

        _write_runtime_state(source_cm, character_name="共享角色")
        source_characters = source_cm.load_characters()
        source_characters["猫娘"]["共享角色"]["喜欢的食物"] = "鱼干"
        source_cm.save_characters(source_characters, bypass_write_fence=True)

        _write_runtime_state(target_cm, character_name="共享角色")
        target_characters = target_cm.load_characters()
        target_characters["猫娘"]["共享角色"]["喜欢的食物"] = "罐头"
        target_cm.save_characters(target_characters, bypass_write_fence=True)

        from utils.cloudsave_runtime import build_cloudsave_summary, export_cloudsave_character_unit

        export_cloudsave_character_unit(source_cm, "共享角色")
        shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

        pre_summary = build_cloudsave_summary(target_cm)
        assert pre_summary["items"][0]["relation_state"] == "diverged"

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", target_cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=target_cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")
            upload = await cloudsave_router_module.post_cloudsave_character_upload(
                "共享角色",
                _DummyRequest({"overwrite": True}),
            )

        assert upload["success"] is True
        assert upload["detail"]["item"]["relation_state"] == "matched"

        cloud_profile = json.loads((target_cm.cloudsave_dir / "characters" / "共享角色" / "profile.json").read_text(encoding="utf-8"))
        assert cloud_profile["喜欢的食物"] == "罐头"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_router_download_overwrite_succeeds_for_diverged_character():
    with TemporaryDirectory() as td:
        source_cm = _make_config_manager(Path(td) / "source")
        target_cm = _make_config_manager(Path(td) / "target")
        bootstrap_local_cloudsave_environment(source_cm)
        bootstrap_local_cloudsave_environment(target_cm)

        _write_runtime_state(source_cm, character_name="共享角色")
        source_characters = source_cm.load_characters()
        source_characters["猫娘"]["共享角色"]["喜欢的食物"] = "鱼干"
        source_cm.save_characters(source_characters, bypass_write_fence=True)
        atomic_write_json(
            Path(source_cm.memory_dir) / "共享角色" / "recent.json",
            [{"role": "assistant", "content": "来自云端"}],
            ensure_ascii=False,
            indent=2,
        )

        _write_runtime_state(target_cm, character_name="共享角色")
        target_characters = target_cm.load_characters()
        target_characters["猫娘"]["共享角色"]["喜欢的食物"] = "罐头"
        target_cm.save_characters(target_characters, bypass_write_fence=True)

        from utils.cloudsave_runtime import build_cloudsave_summary, export_cloudsave_character_unit

        export_cloudsave_character_unit(source_cm, "共享角色")
        shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

        pre_summary = build_cloudsave_summary(target_cm)
        assert pre_summary["items"][0]["relation_state"] == "diverged"

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", target_cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=target_cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")
            with patch.object(cloudsave_router_module, "_reload_after_character_download", AsyncMock(return_value=(True, ""))), \
                 patch.object(cloudsave_router_module, "release_memory_server_character", AsyncMock(return_value=True)):
                download = await cloudsave_router_module.post_cloudsave_character_download(
                    "共享角色",
                    _DummyRequest({"overwrite": True, "backup_before_overwrite": True}),
                )

        assert download["success"] is True
        assert download["detail"]["item"]["relation_state"] == "matched"
        assert target_cm.load_characters()["猫娘"]["共享角色"]["喜欢的食物"] == "鱼干"
        restored_recent = json.loads((Path(target_cm.memory_dir) / "共享角色" / "recent.json").read_text(encoding="utf-8"))
        assert restored_recent[0]["content"] == "来自云端"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_router_blocks_mutations_when_provider_is_unavailable():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="小满")
        cm.cloudsave_provider_available = False

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

            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            upload = await cloudsave_router_module.post_cloudsave_character_upload(
                "小满",
                _DummyRequest({"overwrite": False}),
            )
            upload_payload = json.loads(upload.body)
            assert upload.status_code == 503
            assert upload_payload["code"] == "CLOUDSAVE_PROVIDER_UNAVAILABLE"

            download = await cloudsave_router_module.post_cloudsave_character_download(
                "小满",
                _DummyRequest({"overwrite": False, "backup_before_overwrite": True}),
            )
            download_payload = json.loads(download.body)
            assert download.status_code == 503
            assert download_payload["code"] == "CLOUDSAVE_PROVIDER_UNAVAILABLE"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_router_preserves_maintenance_mode_conflicts():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="小满")

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

            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            with patch.object(
                cloudsave_router_module,
                "export_cloudsave_character_unit",
                side_effect=MaintenanceModeError("maintenance", operation="export", target="小满"),
            ):
                upload = await cloudsave_router_module.post_cloudsave_character_upload(
                    "小满",
                    _DummyRequest({"overwrite": False}),
                )
            upload_payload = json.loads(upload.body)
            assert upload.status_code == 409
            assert upload_payload["code"] == "CLOUDSAVE_WRITE_FENCE_ACTIVE"

            with patch.object(
                cloudsave_router_module,
                "import_cloudsave_character_unit",
                side_effect=MaintenanceModeError("maintenance", operation="import", target="小满"),
            ), patch.object(
                cloudsave_router_module,
                "release_memory_server_character",
                AsyncMock(return_value=True),
            ):
                download = await cloudsave_router_module.post_cloudsave_character_download(
                    "小满",
                    _DummyRequest({"overwrite": True, "backup_before_overwrite": True}),
                )
            download_payload = json.loads(download.body)
            assert download.status_code == 409
            assert download_payload["code"] == "CLOUDSAVE_WRITE_FENCE_ACTIVE"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_router_download_reload_failure_rolls_back():
    with TemporaryDirectory() as td:
        source_cm = _make_config_manager(Path(td) / "source")
        target_cm = _make_config_manager(Path(td) / "target")
        bootstrap_local_cloudsave_environment(source_cm)
        bootstrap_local_cloudsave_environment(target_cm)
        _write_runtime_state(source_cm, character_name="小满")
        source_characters = source_cm.load_characters()
        source_characters["猫娘"]["小满"]["喜欢的食物"] = "鱼干"
        source_cm.save_characters(source_characters, bypass_write_fence=True)
        atomic_write_json(
            Path(source_cm.memory_dir) / "小满" / "recent.json",
            [{"role": "assistant", "content": "云端版本"}],
            ensure_ascii=False,
            indent=2,
        )

        from utils.cloudsave_runtime import export_cloudsave_character_unit

        export_cloudsave_character_unit(source_cm, "小满", overwrite=True)

        _write_runtime_state(target_cm, character_name="小满")
        target_characters = target_cm.load_characters()
        target_characters["猫娘"]["小满"]["喜欢的食物"] = "本地旧版本"
        target_cm.save_characters(target_characters, bypass_write_fence=True)
        atomic_write_json(
            Path(target_cm.memory_dir) / "小满" / "recent.json",
            [{"role": "assistant", "content": "本地版本"}],
            ensure_ascii=False,
            indent=2,
        )
        original_characters = target_cm.load_characters()
        original_recent = (Path(target_cm.memory_dir) / "小满" / "recent.json").read_text(encoding="utf-8")
        shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", target_cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=target_cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            with patch.object(cloudsave_router_module, "_reload_after_character_download", AsyncMock(return_value=(False, "forced reload failure"))), \
                 patch.object(cloudsave_router_module, "release_memory_server_character", AsyncMock(return_value=True)), \
                 patch.object(cloudsave_router_module, "notify_memory_server_reload", AsyncMock(return_value=True)):
                failed = await cloudsave_router_module.post_cloudsave_character_download(
                    "小满",
                    _DummyRequest({"overwrite": True, "backup_before_overwrite": True}),
                )

            failed_payload = json.loads(failed.body)
            assert failed.status_code == 500
            assert failed_payload["code"] == "LOCAL_RELOAD_FAILED_ROLLED_BACK"
            assert failed_payload["rolled_back"] is True
            assert target_cm.load_characters() == original_characters
            assert (Path(target_cm.memory_dir) / "小满" / "recent.json").read_text(encoding="utf-8") == original_recent


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_router_download_rollback_reports_notify_reload_false():
    with TemporaryDirectory() as td:
        source_cm = _make_config_manager(Path(td) / "source")
        target_cm = _make_config_manager(Path(td) / "target")
        bootstrap_local_cloudsave_environment(source_cm)
        bootstrap_local_cloudsave_environment(target_cm)
        _write_runtime_state(source_cm, character_name="小满")
        source_characters = source_cm.load_characters()
        source_characters["猫娘"]["小满"]["喜欢的食物"] = "鱼干"
        source_cm.save_characters(source_characters, bypass_write_fence=True)

        from utils.cloudsave_runtime import export_cloudsave_character_unit

        export_cloudsave_character_unit(source_cm, "小满", overwrite=True)

        _write_runtime_state(target_cm, character_name="小满")
        target_characters = target_cm.load_characters()
        target_characters["猫娘"]["小满"]["喜欢的食物"] = "本地旧版本"
        target_cm.save_characters(target_characters, bypass_write_fence=True)
        shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", target_cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=target_cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            with patch.object(
                cloudsave_router_module,
                "_reload_after_character_download",
                AsyncMock(return_value=(False, "forced reload failure")),
            ), patch.object(
                cloudsave_router_module,
                "release_memory_server_character",
                AsyncMock(return_value=True),
            ), patch.object(
                cloudsave_router_module,
                "notify_memory_server_reload",
                AsyncMock(return_value=False),
            ):
                failed = await cloudsave_router_module.post_cloudsave_character_download(
                    "小满",
                    _DummyRequest({"overwrite": True, "backup_before_overwrite": True}),
                )

        failed_payload = json.loads(failed.body)
        assert failed.status_code == 500
        assert failed_payload["code"] == "LOCAL_RELOAD_FAILED_ROLLED_BACK"
        assert failed_payload["rolled_back"] is False
        assert failed_payload["rollback_error"] == "notify_memory_server_reload returned False"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_download_does_not_report_rollback_when_no_backup_was_attempted():
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

            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            with patch.object(
                cloudsave_router_module,
                "import_cloudsave_character_unit",
                return_value={
                    "detail": {"item": {"character_name": "云端角色"}},
                    "backup_path": "",
                },
            ), patch.object(
                cloudsave_router_module,
                "_reload_after_character_download",
                AsyncMock(return_value=(False, "forced reload failure")),
            ), patch.object(
                cloudsave_router_module,
                "restore_cloudsave_operation_backup",
            ) as restore_backup_mock:
                failed = await cloudsave_router_module.post_cloudsave_character_download(
                    "云端角色",
                    _DummyRequest({"overwrite": False, "backup_before_overwrite": True}),
                )

        failed_payload = json.loads(failed.body)
        assert failed.status_code == 500
        assert failed_payload["code"] == "LOCAL_RELOAD_FAILED_ROLLED_BACK"
        assert failed_payload["rolled_back"] is False
        assert failed_payload["rollback_error"] == ""
        restore_backup_mock.assert_not_called()


# ======================================================================
# Force-terminate session tests
# ======================================================================


def _make_active_session_mgr():
    """Create a mock LLMSessionManager with is_active=True."""
    mgr = AsyncMock()
    mgr.is_active = True
    mgr.websocket = AsyncMock()
    return mgr


def _setup_force_test_env(tmp_root, *, active_mgr=None):
    """Common setup for force-terminate tests: bootstrap + shared_state init."""
    cm = _make_config_manager(tmp_root)
    bootstrap_local_cloudsave_environment(cm)

    async def _noop_init():
        return None

    async def _noop_any(*args, **kwargs):
        return None

    role_state = _make_role_state_for_test(
        {"小满": active_mgr} if active_mgr else {}
    )

    with patch("utils.config_manager._config_manager", cm):
        init_shared_state(
            role_state=role_state,
            steamworks=None,
            templates=None,
            config_manager=cm,
            logger=None,
            initialize_character_data=_noop_init,
            switch_current_catgirl_fast=_noop_any,
            init_one_catgirl=_noop_any,
            remove_one_catgirl=_noop_any,
        )

    return cm


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_active_session_no_force():
    """Active session + no force → 409 + can_force: true."""
    with TemporaryDirectory() as td:
        mgr = _make_active_session_mgr()
        cm = _setup_force_test_env(Path(td), active_mgr=mgr)

        with patch("utils.config_manager._config_manager", cm):
            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            resp = await cloudsave_router_module.post_cloudsave_character_download(
                "小满",
                _DummyRequest({"overwrite": True, "backup_before_overwrite": True}),
            )

        payload = json.loads(resp.body)
        assert resp.status_code == 409
        assert payload["code"] == "ACTIVE_SESSION_BLOCKED"
        assert payload["can_force"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_active_session_force_bool_coercion():
    """force='true' (string) → still returns 409 (strict bool check)."""
    with TemporaryDirectory() as td:
        mgr = _make_active_session_mgr()
        cm = _setup_force_test_env(Path(td), active_mgr=mgr)

        with patch("utils.config_manager._config_manager", cm):
            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            resp = await cloudsave_router_module.post_cloudsave_character_download(
                "小满",
                _DummyRequest({"overwrite": True, "backup_before_overwrite": True, "force": "true"}),
            )

        payload = json.loads(resp.body)
        assert resp.status_code == 409
        assert payload["code"] == "ACTIVE_SESSION_BLOCKED"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_active_session_force_terminate_ok():
    """force=true + terminate success + memory release success → download proceeds → 200."""
    with TemporaryDirectory() as td:
        mgr = _make_active_session_mgr()
        cm = _setup_force_test_env(Path(td), active_mgr=mgr)
        _write_runtime_state(cm, character_name="小满")
        export_local_cloudsave_snapshot(cm)

        with patch("utils.config_manager._config_manager", cm):
            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            with patch.object(
                cloudsave_router_module,
                "import_cloudsave_character_unit",
                return_value={
                    "detail": {"item": {"character_name": "小满"}},
                    "backup_path": "",
                },
            ), patch.object(
                cloudsave_router_module,
                "_reload_after_character_download",
                AsyncMock(return_value=(True, "")),
            ), patch.object(
                cloudsave_router_module,
                "release_memory_server_character",
                AsyncMock(return_value=True),
            ):
                resp = await cloudsave_router_module.post_cloudsave_character_download(
                    "小满",
                    _DummyRequest({"overwrite": True, "backup_before_overwrite": True, "force": True}),
                )

        # Successful download returns a plain dict, not JSONResponse
        assert isinstance(resp, dict)
        assert resp["success"] is True
        mgr.disconnected_by_server.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_active_session_force_terminate_fail():
    """force=true + terminate fails → 503 SESSION_TERMINATE_FAILED."""
    with TemporaryDirectory() as td:
        mgr = _make_active_session_mgr()
        mgr.disconnected_by_server = AsyncMock(side_effect=RuntimeError("websocket error"))
        cm = _setup_force_test_env(Path(td), active_mgr=mgr)

        with patch("utils.config_manager._config_manager", cm):
            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            with patch.object(
                cloudsave_router_module,
                "release_memory_server_character",
                AsyncMock(return_value=True),
            ) as release_mock:
                resp = await cloudsave_router_module.post_cloudsave_character_download(
                    "小满",
                    _DummyRequest({"overwrite": True, "backup_before_overwrite": True, "force": True}),
                )

        payload = json.loads(resp.body)
        assert resp.status_code == 503
        assert payload["code"] == "SESSION_TERMINATE_FAILED"
        release_mock.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_active_session_force_memory_release_fail():
    """force=true + terminate ok + memory release fails → 503 MEMORY_SERVER_RELEASE_FAILED."""
    with TemporaryDirectory() as td:
        mgr = _make_active_session_mgr()
        cm = _setup_force_test_env(Path(td), active_mgr=mgr)

        with patch("utils.config_manager._config_manager", cm):
            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            with patch.object(
                cloudsave_router_module,
                "release_memory_server_character",
                AsyncMock(return_value=False),
            ), patch.object(
                cloudsave_router_module,
                "import_cloudsave_character_unit",
            ) as import_mock:
                resp = await cloudsave_router_module.post_cloudsave_character_download(
                    "小满",
                    _DummyRequest({"overwrite": True, "backup_before_overwrite": True, "force": True}),
                )

        payload = json.loads(resp.body)
        assert resp.status_code == 503
        assert payload["code"] == "MEMORY_SERVER_RELEASE_FAILED"
        import_mock.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_no_active_session_force_ignored():
    """No active session + force=true → normal download (force ignored)."""
    with TemporaryDirectory() as td:
        cm = _setup_force_test_env(Path(td))
        _write_runtime_state(cm, character_name="小满")
        export_local_cloudsave_snapshot(cm)

        with patch("utils.config_manager._config_manager", cm):
            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            with patch.object(
                cloudsave_router_module,
                "import_cloudsave_character_unit",
                return_value={
                    "detail": {"item": {"character_name": "小满"}},
                    "backup_path": "",
                },
            ), patch.object(
                cloudsave_router_module,
                "_reload_after_character_download",
                AsyncMock(return_value=(True, "")),
            ):
                resp = await cloudsave_router_module.post_cloudsave_character_download(
                    "小满",
                    _DummyRequest({"overwrite": True, "backup_before_overwrite": True, "force": True}),
                )

        assert isinstance(resp, dict)
        assert resp["success"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_after_force_is_active_false():
    """After force terminate, mgr.is_active should be False."""
    with TemporaryDirectory() as td:
        mgr = _make_active_session_mgr()
        cm = _setup_force_test_env(Path(td), active_mgr=mgr)
        _write_runtime_state(cm, character_name="小满")
        export_local_cloudsave_snapshot(cm)

        with patch("utils.config_manager._config_manager", cm):
            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            with patch.object(
                cloudsave_router_module,
                "import_cloudsave_character_unit",
                return_value={
                    "detail": {"item": {"character_name": "小满"}},
                    "backup_path": "",
                },
            ), patch.object(
                cloudsave_router_module,
                "_reload_after_character_download",
                AsyncMock(return_value=(True, "")),
            ), patch.object(
                cloudsave_router_module,
                "release_memory_server_character",
                AsyncMock(return_value=True),
            ):
                await cloudsave_router_module.post_cloudsave_character_download(
                    "小满",
                    _DummyRequest({"overwrite": True, "backup_before_overwrite": True, "force": True}),
                )

        # disconnected_by_server → cleanup → end_session sets is_active=False
        mgr.disconnected_by_server.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_after_force_memory_released():
    """Force terminate should call release_memory_server_character."""
    with TemporaryDirectory() as td:
        mgr = _make_active_session_mgr()
        cm = _setup_force_test_env(Path(td), active_mgr=mgr)
        _write_runtime_state(cm, character_name="小满")
        export_local_cloudsave_snapshot(cm)

        with patch("utils.config_manager._config_manager", cm):
            cloudsave_router_module = importlib.import_module("main_routers.cloudsave_router")

            with patch.object(
                cloudsave_router_module,
                "import_cloudsave_character_unit",
                return_value={
                    "detail": {"item": {"character_name": "小满"}},
                    "backup_path": "",
                },
            ), patch.object(
                cloudsave_router_module,
                "_reload_after_character_download",
                AsyncMock(return_value=(True, "")),
            ), patch.object(
                cloudsave_router_module,
                "release_memory_server_character",
                AsyncMock(return_value=True),
            ) as release_mock:
                await cloudsave_router_module.post_cloudsave_character_download(
                    "小满",
                    _DummyRequest({"overwrite": True, "backup_before_overwrite": True, "force": True}),
                )

        release_mock.assert_awaited_once()
        call_args = release_mock.call_args
        assert call_args[0][0] == "小满"
        assert "强制" in call_args[1]["reason"]
