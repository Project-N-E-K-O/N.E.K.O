"""Fallback Live2D URLs must point at the real folder, and Monitor must serve them.

Two places built model URLs without the folder's real position under its mount:

* ``/api/characters/current_live2d_model`` when the character's model is not in
  the full model list: it used the model name, or ``<saved item_id>/<basename>``,
  as the URL directory;
* the Monitor page, which mounted ``/workshop`` as a plain ``StaticFiles`` on one
  library and did not mount ``/user_mods`` at all, so other Steam libraries and
  user mods returned by ``find_model_config_file`` 404'd there.

Every request here goes through a real Starlette mount over synthetic folders in
``tmp_path``.
"""

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
import pytest

import utils.config_manager as config_manager_module
from app import monitor, monitor_auth
from main_routers import live2d_router
from utils import frontend_utils
from utils.config_manager.workshop import WorkshopMixin
from utils.workshop_utils import (
    WorkshopStaticFiles,
    create_workshop_static_files,
    register_workshop_roots_from_items,
)


pytestmark = pytest.mark.unit
APP_ID = "4099310"
live2d_models = importlib.import_module("main_routers.characters_router.live2d_models")
workshop_router_pkg = importlib.import_module("main_routers.workshop_router")


class _FakeConfig:
    """What the lookups touch; the Steam library registry is the real implementation."""

    register_steam_workshop_library_roots = WorkshopMixin.register_steam_workshop_library_roots
    get_steam_workshop_library_roots = WorkshopMixin.get_steam_workshop_library_roots

    def __init__(self, tmp_path: Path, primary_root: Path):
        self._primary_root = str(primary_root)
        self._steam_workshop_library_roots = []
        self.live2d_dir = tmp_path / "user_live2d"
        self.live2d_dir.mkdir()
        self.readable_live2d_dir = None
        self.characters = {}

    def get_live2d_lookup_roots(self, prefer_writable=True):
        return [self.live2d_dir]

    def get_workshop_path(self):
        return self._primary_root

    async def aload_characters(self, character_json_path=None):
        return self.characters


def _library(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name / "steamapps" / "workshop" / "content" / APP_ID
    root.mkdir(parents=True)
    return root


def _write_model(folder: Path, name: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.model3.json").write_text(
        '{"Version": 3, "FileReferences": {"Moc": "%s.moc3"}}' % name, encoding="utf-8")


@pytest.fixture
def libraries(tmp_path, monkeypatch):
    """Two Steam libraries; the configured Workshop (and /user_mods) root is the first."""
    lib_a = _library(tmp_path, "LibraryA")
    lib_b = _library(tmp_path, "LibraryB")
    config = _FakeConfig(tmp_path, lib_a)
    monkeypatch.setattr(config_manager_module, "get_config_manager", lambda *a, **k: config)
    monkeypatch.setattr(live2d_models, "get_config_manager", lambda: config)
    # Not in the full model list, so the router takes its fallback branch.
    monkeypatch.setattr(live2d_models, "find_models", lambda: [])

    async def no_subscribed_items():
        return {"success": False, "items": []}

    monkeypatch.setattr(workshop_router_pkg, "get_subscribed_workshop_items", no_subscribed_items)
    return SimpleNamespace(a=lib_a, b=lib_b, config=config)


def _set_character(config, model_name, item_id=""):
    avatar = {"live2d": {"model_path": f"{model_name}.model3.json"}}
    if item_id:
        avatar["asset_source"] = "steam_workshop"
        avatar["asset_source_id"] = item_id
    config.characters = {"当前猫娘": "Neko", "猫娘": {"Neko": {"_reserved": {"avatar": avatar}}}}


def _main_server_client(libs):
    """The model mounts as the main server sets them up."""
    app = FastAPI()
    app.mount("/workshop", create_workshop_static_files(str(libs.a)), name="workshop")
    app.mount("/user_mods", StaticFiles(directory=str(libs.a)), name="user_mods")
    app.mount("/user_live2d", StaticFiles(directory=str(libs.config.live2d_dir)), name="user_live2d")
    return TestClient(app)


async def _fallback_info(config, model_name, item_id=""):
    _set_character(config, model_name, item_id)
    body = json.loads((await live2d_models.get_current_live2d_model("Neko")).body)
    assert body["success"] is True, body
    assert body["model_info"].get("is_fallback") is not True, body
    return body["model_info"]


async def _fallback_url(config, model_name, item_id=""):
    return (await _fallback_info(config, model_name, item_id))["path"]


def _model_files_by_id_client(libs, monkeypatch):
    """Main-server mounts plus the live2d router the model manager asks for files."""
    monkeypatch.setattr(live2d_router, "get_config_manager", lambda: libs.config)
    monkeypatch.setattr(live2d_router, "get_workshop_path", lambda: str(libs.a))
    monkeypatch.setattr(live2d_router, "find_models", lambda: [])
    client = _main_server_client(libs)
    client.app.include_router(live2d_router.router)
    return client


@pytest.mark.asyncio
async def test_character_fallback_url_uses_the_real_workshop_folder(libraries):
    # Model folder one level inside an item folder of the configured library.
    _write_model(libraries.a / "300" / "Charlie", "Charlie")
    # Item folder whose config file carries the model name, in another Steam library.
    _write_model(libraries.b / "200", "Bravo")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])

    charlie = await _fallback_url(libraries.config, "Charlie")
    bravo = await _fallback_url(libraries.config, "Bravo", item_id="200")
    assert charlie == "/workshop/300/Charlie/Charlie.model3.json"
    assert bravo == "/workshop/200/Bravo.model3.json"
    # A saved id that names the folder found is kept; without one nothing is reported.
    assert (await _fallback_info(libraries.config, "Bravo", item_id="200"))["item_id"] == "200"
    assert (await _fallback_info(libraries.config, "Charlie"))["item_id"] == ""
    with _main_server_client(libraries) as client:
        assert client.get(charlie).status_code == 200
        assert client.get(bravo).status_code == 200


@pytest.mark.asyncio
async def test_character_fallback_url_ignores_a_saved_item_id_that_is_not_the_folder(libraries, monkeypatch):
    """The folder is found by name; a stale saved item_id must not end up in the URL or item_id."""
    _write_model(libraries.a / "300" / "Charlie", "Charlie")
    info = await _fallback_info(libraries.config, "Charlie", item_id="999")
    assert info["path"] == "/workshop/300/Charlie/Charlie.model3.json"
    # The model manager loads files by item_id, so it must name the folder found.
    assert info["item_id"] == "300"
    with _model_files_by_id_client(libraries, monkeypatch) as client:
        assert client.get(info["path"]).status_code == 200
        files = client.get(f"/api/live2d/model_files_by_id/{info['item_id']}").json()
        assert files["success"] is True, files
        assert client.get(files["model_config_url"]).status_code == 200


@pytest.mark.asyncio
async def test_character_fallback_url_keeps_the_mod_folder_in_user_mods_urls(libraries, monkeypatch):
    # /user_mods shares its root with the configured Workshop folder, which is searched
    # first; skip that pass so the lookup reaches the /user_mods branch.
    monkeypatch.setattr(frontend_utils, "_resolve_workshop_search_dirs", lambda: [])
    _write_model(libraries.a / "mods" / "Delta", "Delta")
    url = await _fallback_url(libraries.config, "Delta")
    assert url == "/user_mods/mods/Delta/Delta.model3.json"
    with _main_server_client(libraries) as client:
        assert client.get(url).status_code == 200


@pytest.mark.asyncio
async def test_character_fallback_urls_for_ordinary_layouts_are_unchanged(libraries):
    # A model folder directly in the Workshop root, and a user Live2D model.
    _write_model(libraries.a / "Gamma", "Gamma")
    _write_model(libraries.config.live2d_dir / "Echo", "Echo")
    gamma = await _fallback_url(libraries.config, "Gamma")
    echo = await _fallback_url(libraries.config, "Echo")
    assert gamma == "/workshop/Gamma/Gamma.model3.json"
    assert echo == "/user_live2d/Echo/Echo.model3.json"
    with _main_server_client(libraries) as client:
        assert client.get(gamma).status_code == 200
        assert client.get(echo).status_code == 200


def _monitor_client(libs):
    app = FastAPI()
    monitor._mount_model_roots(app, str(libs.a), str(libs.a))
    return TestClient(app)


def test_monitor_serves_workshop_urls_from_every_steam_library(libraries):
    _write_model(libraries.a / "100", "Alpha")
    _write_model(libraries.b / "200", "Bravo")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    alpha = frontend_utils.find_model_config_file("Alpha")
    bravo = frontend_utils.find_model_config_file("Bravo")
    assert alpha == "/workshop/100/Alpha.model3.json"
    assert bravo == "/workshop/200/Bravo.model3.json"
    with _monitor_client(libraries) as client:
        assert client.get(alpha).status_code == 200
        assert client.get(bravo).status_code == 200


def test_monitor_serves_user_mods_urls(libraries, monkeypatch):
    monkeypatch.setattr(frontend_utils, "_resolve_workshop_search_dirs", lambda: [])
    _write_model(libraries.a / "mods" / "Delta", "Delta")
    url = frontend_utils.find_model_config_file("Delta")
    assert url == "/user_mods/mods/Delta/Delta.model3.json"
    with _monitor_client(libraries) as client:
        assert client.get(url).status_code == 200


def test_monitor_mounts_workshop_without_a_usable_root(libraries, tmp_path):
    """Like the main server: later-registered libraries are served even without a root."""
    _write_model(libraries.b / "200", "Bravo")
    app = FastAPI()
    monitor._mount_model_roots(app, None, str(tmp_path / "missing"))
    assert not any(getattr(r, "path", "") == "/user_mods" for r in app.routes)
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    with TestClient(app) as client:
        assert client.get("/workshop/200/Bravo.model3.json").status_code == 200


def test_monitor_user_mods_is_public_like_workshop(libraries, monkeypatch):
    """/user_mods serves the same root as /workshop, so the token gate treats it the same."""
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "route-secret")
    _write_model(libraries.a / "mods" / "Delta", "Delta")
    app = FastAPI()
    app.add_middleware(monitor_auth.MonitorAuthMiddleware)
    monitor._mount_model_roots(app, str(libraries.a), str(libraries.a))
    with TestClient(app) as client:
        assert client.get("/user_mods/mods/Delta/Delta.model3.json").status_code == 200
        assert client.get("/workshop/mods/Delta/Delta.model3.json").status_code == 200


def test_monitor_app_mounts_workshop_with_the_multi_library_app():
    mounts = {getattr(r, "path", None): r for r in monitor.app.routes}
    assert isinstance(mounts["/workshop"].app, WorkshopStaticFiles)


@pytest.mark.asyncio
async def test_character_fallback_drops_a_saved_item_id_outside_the_workshop(libraries):
    """A user Live2D folder is not a Workshop item; the frontend then looks it up by name."""
    _write_model(libraries.config.live2d_dir / "Echo", "Echo")
    info = await _fallback_info(libraries.config, "Echo", item_id="555")
    assert info["path"] == "/user_live2d/Echo/Echo.model3.json"
    assert info["item_id"] == ""
