import importlib
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from main_routers.shared_state import init_shared_state
from utils.cloudsave_runtime import bootstrap_local_cloudsave_environment
from utils.config_manager import ConfigManager
from utils.file_utils import atomic_write_json


@pytest.fixture(autouse=True)
def _fresh_cloudsave_router_module():
    sys.modules.pop("main_routers.cloudsave_router", None)
    yield
    sys.modules.pop("main_routers.cloudsave_router", None)


def _make_config_manager(tmp_root: Path):
    with patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_root), patch.object(
        ConfigManager,
        "_get_standard_data_directory_candidates",
        return_value=[tmp_root],
    ), patch.object(
        ConfigManager,
        "get_legacy_app_root_candidates",
        return_value=[],
    ):
        config_manager = ConfigManager("N.E.K.O")
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


def _make_dummy_steamworks(*, running=True, logged_on=True):
    return SimpleNamespace(
        IsSteamRunning=lambda: running,
        Users=SimpleNamespace(LoggedOn=lambda: logged_on),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_router_exposes_steam_autocloud_configuration_payload():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        _write_runtime_state(cm, character_name="小满")

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm), patch.dict(
            "os.environ",
            {"SteamAppId": "4099310"},
            clear=False,
        ):
            init_shared_state(
                role_state={},
                steamworks=_make_dummy_steamworks(),
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
            assert summary["sync_backend"] == "steam_auto_cloud"
            assert summary["steam_autocloud"]["backend"] == "steam_auto_cloud"
            assert summary["steam_autocloud"]["app_id"] == "4099310"
            assert summary["steam_autocloud"]["source_launch"] is False
            assert summary["steam_autocloud"]["steam_available"] is True
            assert summary["steam_autocloud"]["steam_session_ready"] is True
            assert summary["steam_autocloud"]["cloudsave_root"].endswith("cloudsave")

            config_payload = await cloudsave_router_module.get_steam_autocloud_config()
            assert config_payload["success"] is True
            assert config_payload["sync_backend"] == "steam_auto_cloud"
            assert config_payload["steam_autocloud"]["manifest_path"].endswith("manifest.json")
            assert config_payload["steam_autocloud"]["recommended_paths"]["primary_root"]["root"] == "WinAppDataLocal"
            assert config_payload["steam_autocloud"]["recommended_paths"]["primary_root"]["subdirectory"] == "N.E.K.O/cloudsave"
