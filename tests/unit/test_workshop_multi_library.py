"""Workshop items installed in a second Steam library must load like the first.

Each Steam library has its own ``steamapps/workshop/content/<appid>`` folder.
``/workshop`` used to serve only the folder derived from the first subscribed
item, so models installed in another library were listed but their URLs 404'd,
and saving a VRM emotion mapping could not find them. Every library here is a
synthetic folder under ``tmp_path``.
"""

import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
import pytest

import utils.config_manager as config_manager_module
from app.main_server import workshop_runtime
from main_routers import live2d_router, mmd_router, vrm_router
from main_routers import workshop_router
from main_routers.workshop_router import items as wr_items
from main_routers.workshop_router import ugc as wr_ugc
from utils import frontend_utils
from utils.config_manager.workshop import WorkshopMixin
from utils.workshop_utils import (
    WorkshopStaticFiles,
    create_workshop_static_files,
    extract_workshop_roots_from_items,
    register_workshop_roots_from_items,
)


pytestmark = pytest.mark.unit
APP_ID = "4099310"


class _FakeConfig:
    """Only what the routers touch; the library registry is the real implementation."""

    register_steam_workshop_library_roots = WorkshopMixin.register_steam_workshop_library_roots
    get_steam_workshop_library_roots = WorkshopMixin.get_steam_workshop_library_roots

    def __init__(self, tmp_path: Path, primary_root: Path):
        self._primary_root = str(primary_root)
        self._steam_workshop_path = str(primary_root)
        self._steam_workshop_library_roots = []
        self.project_root = tmp_path / "project"
        (self.project_root / "static" / "vrm").mkdir(parents=True)
        self.live2d_dir = tmp_path / "user_live2d"
        self.live2d_dir.mkdir()
        self.readable_live2d_dir = None
        self.vrm_dir = tmp_path / "user_vrm"
        self.vrm_dir.mkdir()
        self.mmd_dir = tmp_path / "user_mmd"
        self.mmd_dir.mkdir()
        self.mmd_animation_dir = self.mmd_dir / "animation"

    def ensure_live2d_directory(self):
        return True

    def ensure_vrm_directory(self):
        return True

    def get_live2d_lookup_roots(self, prefer_writable=True):
        return [self.live2d_dir]

    def get_workshop_path(self):
        return self._primary_root

    def get_steam_workshop_path(self):
        return self._steam_workshop_path


def _library(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name / "steamapps" / "workshop" / "content" / APP_ID
    root.mkdir(parents=True)
    return root


def _write_model(item: Path, kind: str, name: str) -> None:
    item.mkdir(parents=True, exist_ok=True)
    if kind == "live2d":
        (item / f"{name}.model3.json").write_text(
            '{"Version": 3, "FileReferences": {"Moc": "%s.moc3"}}' % name, encoding="utf-8")
    elif kind == "mmd":
        (item / f"{name}.pmx").write_bytes(f"pmx {name}".encode())
    else:
        (item / f"{name}.vrm").write_bytes(f"glTF {name}".encode())


@pytest.fixture
def libraries(tmp_path, monkeypatch):
    """Two Steam libraries; /workshop is mounted on the first, as startup does."""
    lib_a = _library(tmp_path, "LibraryA")
    lib_b = _library(tmp_path, "LibraryB")
    config = _FakeConfig(tmp_path, lib_a)
    # Module-level wrappers (utils.config_manager.get_*) all go through this.
    monkeypatch.setattr(config_manager_module, "get_config_manager", lambda *a, **k: config)
    for router in (live2d_router, mmd_router, vrm_router):
        monkeypatch.setattr(router, "get_config_manager", lambda: config)
    monkeypatch.setattr(live2d_router, "get_workshop_path", lambda: str(lib_a))
    monkeypatch.setattr(live2d_router, "find_models", lambda: [])
    return SimpleNamespace(a=lib_a, b=lib_b, config=config, tmp_path=tmp_path)


def _client(libs, items, *, static_cls=WorkshopStaticFiles):
    """App with the three model routers and /workshop mounted on library A."""
    async def subscribed_items():
        # The real endpoint registers the libraries of the items it returns.
        register_workshop_roots_from_items(items)
        return {"success": True, "items": items}

    for router in (live2d_router, mmd_router, vrm_router):
        router.get_subscribed_workshop_items = subscribed_items
    app = FastAPI()
    for router in (live2d_router, mmd_router, vrm_router):
        app.include_router(router.router)
    app.mount("/workshop", static_cls(directory=str(libs.a)), name="workshop")
    return TestClient(app)


@pytest.fixture(autouse=True)
def _restore_subscribed_items():
    originals = {r: r.get_subscribed_workshop_items for r in (live2d_router, mmd_router, vrm_router)}
    yield
    for router, original in originals.items():
        router.get_subscribed_workshop_items = original


def _listed_urls(client, kind):
    if kind == "live2d":
        models = client.get("/api/live2d/models").json()
        return {m["name"]: m["path"] for m in models}
    models = client.get(f"/api/model/{kind}/models").json()["models"]
    return {m["name"]: m["url"] for m in models}


def _save_mapping(client, kind, name):
    if kind == "live2d":
        return client.post(f"/api/live2d/emotion_mapping/{quote(name, safe='')}",
                           json={"motions": {}, "expressions": {"happy": ["smile.exp3.json"]}})
    if kind == "mmd":
        return client.post("/api/model/mmd/emotion_mapping",
                           json={"model": name, "mapping": {"happy": ["smile"]}})
    return client.post(f"/api/model/vrm/emotion_mapping/{quote(name, safe='')}",
                       json={"happy": ["smile"]})


KINDS = ["live2d", "mmd", "vrm"]


def _two_items(libs, kind):
    _write_model(libs.a / "100", kind, "Alpha")
    _write_model(libs.b / "200", kind, "Bravo")
    return [
        {"installedFolder": str(libs.a / "100"), "publishedFileId": "100"},
        {"installedFolder": str(libs.b / "200"), "publishedFileId": "200"},
    ]


@pytest.mark.parametrize("kind", KINDS)
def test_items_in_both_libraries_list_load_and_save_mappings(libraries, kind):
    items = _two_items(libraries, kind)
    with _client(libraries, items) as client:
        urls = _listed_urls(client, kind)
        assert set(urls) == {"Alpha", "Bravo"}
        assert urls["Bravo"].startswith("/workshop/200/")
        for name, url in urls.items():
            response = client.get(url)
            assert response.status_code == 200, (name, url)
            assert name.encode() in response.content
        for name in ("Alpha", "Bravo"):
            response = _save_mapping(client, kind, name)
            assert response.status_code == 200, (name, response.text)
    if kind == "live2d":
        assert "smile.exp3.json" in (libraries.b / "200" / "Bravo.model3.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", KINDS)
def test_plain_mount_reproduces_the_404(libraries, kind):
    """Control: the pre-fix mount serves only library A."""
    items = _two_items(libraries, kind)
    with _client(libraries, items, static_cls=StaticFiles) as client:
        urls = _listed_urls(client, kind)
        assert client.get(urls["Alpha"]).status_code == 200
        assert client.get(urls["Bravo"]).status_code == 404


def test_libraries_registered_later_are_served(libraries):
    """Libraries found after startup (a new subscription) are served without remounting."""
    _write_model(libraries.b / "200", "vrm", "Bravo")
    with _client(libraries, []) as client:
        assert client.get("/workshop/200/Bravo.vrm").status_code == 404
        register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
        assert client.get("/workshop/200/Bravo.vrm").status_code == 200


@pytest.fixture
def escape_links(libraries):
    outside = libraries.tmp_path / "outside"
    outside.mkdir()
    for name in ("Secret.model3.json", "Secret.pmx", "Secret.vrm", "secret.txt"):
        (outside / name).write_text("outside sentinel", encoding="utf-8")
    try:
        # Whole item folder links out of library B.
        (libraries.b / "300").symlink_to(outside, target_is_directory=True)
        # Single file links out of an item in library B.
        (libraries.b / "200").mkdir()
        (libraries.b / "200" / "Leak.vrm").symlink_to(outside / "Secret.vrm")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    register_workshop_roots_from_items([
        {"installedFolder": str(libraries.a / "100")},
        {"installedFolder": str(libraries.b / "200")},
    ])
    return outside


@pytest.mark.parametrize("url", [
    "/workshop/300/secret.txt",
    "/workshop/300/Secret.vrm",
    "/workshop/200/Leak.vrm",
    "/workshop/%2e%2e/%2e%2e/%2e%2e/%2e%2e/%2e%2e/outside/secret.txt",
    "/workshop/200/%2e%2e%2f%2e%2e%2f%2e%2e%2f%2e%2e%2f%2e%2e%2f%2e%2e%2foutside%2fsecret.txt",
    "/workshop/%2f" + quote(str(Path("/") / "etc" / "hostname").lstrip("/"), safe=""),
])
def test_second_library_keeps_path_boundaries(libraries, escape_links, url):
    (libraries.a / "100").mkdir(exist_ok=True)
    with _client(libraries, []) as client:
        response = client.get(url)
    assert response.status_code == 404
    assert b"outside sentinel" not in response.content


def test_resolvers_refuse_escapes_from_second_library(libraries, escape_links):
    assert vrm_router._get_model_path("Leak") == (None, "")
    assert vrm_router._get_model_path("Secret") == (None, "")
    assert frontend_utils.find_model_directory("Secret") == (None, None)
    config = libraries.config
    assert live2d_router._resolve_sync_model_file("/workshop/300/Secret.model3.json", config) is None
    assert live2d_router._resolve_sync_model_file("/workshop/200/Leak.vrm", config) is None
    # The linked item is skipped; the fallback stays inside the configured root.
    path, prefix = frontend_utils.find_workshop_item_by_id("300")
    assert (path, prefix) == (os.path.join(str(libraries.a), "300"), "/workshop")


@pytest.mark.parametrize("item_id", ["..", ".", "", "../200", "..\\200", "a\x00b", "C:x"])
def test_item_id_that_is_not_one_path_component_is_rejected(libraries, item_id):
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    assert frontend_utils.find_workshop_item_by_id(item_id) == (None, None)
    assert frontend_utils.find_model_by_workshop_item_id(item_id) is None


def test_by_id_lookup_reaches_second_library_when_configured_root_is_missing(libraries):
    """The static fallback is only for 'no Workshop directory at all'."""
    libraries.config._primary_root = str(libraries.tmp_path / "missing_root")
    _write_model(libraries.b / "200", "live2d", "Bravo")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    assert frontend_utils.find_workshop_item_by_id("200") == (str(libraries.b / "200"), "/workshop")
    assert frontend_utils.find_model_by_workshop_item_id("200") == "/workshop/200/Bravo.model3.json"
    with _client(libraries, []) as client:
        response = client.get("/api/live2d/model_config_by_id/200")
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    # No Workshop directory at all: the old static fallback is kept.
    libraries.config._steam_workshop_library_roots = []
    assert frontend_utils.find_workshop_item_by_id("200") == (os.path.join("static", "200"), "/static")


def test_by_id_lookup_finds_nested_layout_in_second_library(libraries):
    nested = libraries.b / "200" / "assets" / "Avatar"
    _write_model(nested, "live2d", "Avatar")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    assert frontend_utils.find_workshop_item_by_id("200") == (str(libraries.b / "200"), "/workshop")
    url = frontend_utils.find_model_by_workshop_item_id("200")
    assert url == "/workshop/200/assets/Avatar/Avatar.model3.json"
    with _client(libraries, []) as client:
        assert client.get(url).status_code == 200


def test_by_id_lookup_does_not_follow_links_out_of_an_item(libraries):
    outside = libraries.tmp_path / "outside_model"
    _write_model(outside, "live2d", "Secret")
    (libraries.b / "300").mkdir()
    try:
        (libraries.b / "300" / "linked").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "300")}])
    path, _ = frontend_utils.find_workshop_item_by_id("300")
    # The item folder itself is real and inside library B, but its only model is
    # reached through a link out of the item, so no model is taken from it.
    assert path == str(libraries.b / "300")
    assert frontend_utils.find_workshop_item_model_file(path) is None
    with _client(libraries, []) as client:
        assert client.get("/api/live2d/model_config_by_id/300").json()["success"] is False
        assert client.get("/api/live2d/model_files_by_id/300").json()["success"] is False


@pytest.mark.parametrize("workshop_path", [None, "missing"])
def test_mount_without_usable_root_serves_libraries_registered_later(libraries, workshop_path):
    """Startup without a usable root still mounts /workshop for later libraries."""
    if workshop_path:
        workshop_path = str(libraries.tmp_path / workshop_path)
    _write_model(libraries.b / "200", "vrm", "Bravo")
    app = FastAPI()
    app.mount("/workshop", create_workshop_static_files(workshop_path), name="workshop")
    with TestClient(app) as client:
        assert client.get("/workshop/200/Bravo.vrm").status_code == 404
        register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
        response = client.get("/workshop/200/Bravo.vrm")
        assert response.status_code == 200
        assert client.get("/workshop/%2e%2e/x").status_code == 404


def test_mount_with_usable_root_is_rooted_there(libraries):
    mount = create_workshop_static_files(str(libraries.a))
    assert mount.directory == str(libraries.a)
    assert mount.all_directories == [str(libraries.a)]


def test_live2d_resolvers_find_models_in_second_library(libraries):
    _write_model(libraries.b / "200", "live2d", "Bravo")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    model_file = libraries.b / "200" / "Bravo.model3.json"
    assert frontend_utils.find_workshop_item_by_id("200") == (str(libraries.b / "200"), "/workshop")
    assert frontend_utils.find_model_directory("Bravo") == (str(libraries.b / "200"), "/workshop")
    assert live2d_router._resolve_sync_model_file("/workshop/200/Bravo.model3.json", libraries.config) == model_file.resolve()
    assert live2d_router._resolve_model_url_to_file_path("/workshop/200/Bravo.model3.json", libraries.config) == model_file.resolve()


@pytest.mark.parametrize("kind", KINDS)
def test_single_library_behaviour_is_unchanged(libraries, kind):
    items = [{"installedFolder": str(libraries.a / "100"), "publishedFileId": "100"}]
    responses = {}
    for static_cls in (StaticFiles, WorkshopStaticFiles):
        # Saving a Live2D mapping rewrites the model file; start each run from scratch.
        _write_model(libraries.a / "100", kind, "Alpha")
        libraries.config._steam_workshop_library_roots = []
        with _client(libraries, items, static_cls=static_cls) as client:
            urls = _listed_urls(client, kind)
            responses[static_cls] = (
                urls,
                [(client.get(u).status_code, client.get(u).content) for u in (
                    urls["Alpha"], "/workshop/100/missing.bin", "/workshop/%2e%2e/x", "/workshop/200/x")],
                _save_mapping(client, kind, "Alpha").status_code,
            )
    assert responses[StaticFiles] == responses[WorkshopStaticFiles]
    assert libraries.config.get_steam_workshop_library_roots() == [str(libraries.a)]
    mount = WorkshopStaticFiles(directory=str(libraries.a))
    assert mount.all_directories == [str(libraries.a)]


def test_extract_roots_dedupes_and_skips_missing(libraries):
    items = [
        {"installedFolder": str(libraries.a / "100")},
        {"installedFolder": str(libraries.b / "200")},
        {"installedFolder": str(libraries.a / "101")},
        {"installedFolder": str(libraries.tmp_path / "gone" / "content" / "300")},
        {"installedFolder": None},
        {},
        "not a dict",
    ]
    assert extract_workshop_roots_from_items(items) == [str(libraries.a), str(libraries.b)]
    register_workshop_roots_from_items(items)
    register_workshop_roots_from_items(items)
    assert libraries.config.get_steam_workshop_library_roots() == [str(libraries.a), str(libraries.b)]


class _FakeWorkshop:
    def __init__(self, folders):
        self._folders = folders

    def GetNumSubscribedItems(self):
        return len(self._folders)

    def GetSubscribedItems(self):
        return list(self._folders)

    def GetItemState(self, item_id):
        return workshop_router._ITEM_STATE_SUBSCRIBED | 4  # installed

    def GetItemInstallInfo(self, item_id):
        return {"folder": self._folders[item_id], "disk_size": 1}

    def GetItemDownloadInfo(self, item_id):
        return {}


@pytest.mark.asyncio
async def test_subscribed_items_endpoint_registers_every_library(libraries, monkeypatch):
    (libraries.a / "100").mkdir()
    (libraries.b / "200").mkdir()
    steamworks = SimpleNamespace(Workshop=_FakeWorkshop({
        100: str(libraries.a / "100"), 200: str(libraries.b / "200"),
    }))
    monkeypatch.setattr(wr_items, "get_steamworks", lambda: steamworks)
    monkeypatch.setattr(wr_ugc, "get_steamworks", lambda: steamworks)
    monkeypatch.setattr(wr_ugc, "_request_workshop_item_download", lambda *a, **k: False)
    monkeypatch.setattr(wr_ugc, "_ugc_details_cache", {})

    response = await workshop_router.get_subscribed_workshop_items()

    assert response["success"] is True
    assert {item["installedFolder"] for item in response["items"]} == {
        str(libraries.a / "100"), str(libraries.b / "200"),
    }
    assert libraries.config.get_steam_workshop_library_roots() == [str(libraries.a), str(libraries.b)]


def test_startup_root_registers_every_library(libraries, monkeypatch):
    from utils import workshop_utils

    (libraries.a / "100").mkdir()
    (libraries.b / "200").mkdir()
    monkeypatch.setattr(workshop_utils, "save_workshop_path", lambda path: None)
    monkeypatch.setattr(workshop_utils, "persist_user_workshop_folder", lambda path: None)
    monkeypatch.setattr(workshop_utils, "ensure_workshop_folder_exists", lambda path: True)
    root = workshop_utils.get_workshop_root([
        {"installedFolder": str(libraries.a / "100")},
        {"installedFolder": str(libraries.b / "200")},
    ])
    assert root == str(libraries.a)
    assert libraries.config.get_steam_workshop_library_roots() == [str(libraries.a), str(libraries.b)]
    assert os.path.isdir(root)


@pytest.mark.parametrize("fallback", [False, True])
def test_startup_mount_is_created_even_without_a_usable_root(libraries, monkeypatch, fallback):
    app = FastAPI()
    monkeypatch.setattr(workshop_runtime, "app", app)
    workshop_runtime._mount_workshop(str(libraries.tmp_path / "missing"), fallback=fallback)
    _write_model(libraries.b / "200", "mmd", "Bravo")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    with TestClient(app) as client:
        assert client.get("/workshop/200/Bravo.pmx").status_code == 200


def _link(link: Path, target: Path, *, is_dir: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")


def test_by_id_fallback_never_returns_an_item_folder_linked_out_of_its_root(libraries):
    """Configured-root item '42' links outside and no library has it: nothing is returned."""
    outside = libraries.tmp_path / "outside_item"
    _write_model(outside, "live2d", "Secret")
    _link(libraries.a / "42", outside, is_dir=True)
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    assert frontend_utils.find_workshop_item_by_id("42") == (None, None)
    assert frontend_utils.find_model_by_workshop_item_id("42") is None
    with _client(libraries, []) as client:
        response = client.get("/api/live2d/model_config_by_id/42")
    assert response.status_code == 404
    assert "Secret" not in response.text


def test_by_id_ignores_model_file_links_but_keeps_real_models_in_the_item(libraries):
    outside = libraries.tmp_path / "outside_file"
    _write_model(outside, "live2d", "Secret")
    item = libraries.b / "200"
    item.mkdir()
    # Listed first, but links out of the item.
    _link(item / "A_Secret.model3.json", outside / "Secret.model3.json")
    _write_model(item / "nested", "live2d", "Good")
    register_workshop_roots_from_items([{"installedFolder": str(item)}])
    assert frontend_utils.find_workshop_item_model_file(str(item)) == str(item / "nested" / "Good.model3.json")
    assert frontend_utils.find_model_by_workshop_item_id("200") == "/workshop/200/nested/Good.model3.json"
    with _client(libraries, []) as client:
        response = client.get("/api/live2d/model_config_by_id/200")
    assert response.status_code == 200
    assert "Good.moc3" in response.text and "Secret" not in response.text


def test_by_id_item_with_only_a_linked_model_file_is_not_found(libraries):
    outside = libraries.tmp_path / "outside_file"
    _write_model(outside, "live2d", "Secret")
    (libraries.b / "200").mkdir()
    _link(libraries.b / "200" / "Secret.model3.json", outside / "Secret.model3.json")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    with _client(libraries, []) as client:
        response = client.get("/api/live2d/model_config_by_id/200")
    assert response.status_code == 404
    assert "Secret" not in response.text


@pytest.mark.parametrize("layout", ["flat", "nested"])
def test_by_id_config_read_and_update_support_both_layouts(libraries, layout):
    item = libraries.b / "200"
    model_dir = item if layout == "flat" else item / "assets" / "Avatar"
    _write_model(model_dir, "live2d", "Avatar")
    if layout == "flat":
        # A nested model must not take precedence over the one directly in the item.
        _write_model(item / "zz_other", "live2d", "Other")
    register_workshop_roots_from_items([{"installedFolder": str(item)}])
    model_file = model_dir / "Avatar.model3.json"
    with _client(libraries, []) as client:
        read = client.get("/api/live2d/model_config_by_id/200")
        assert read.status_code == 200, read.text
        assert read.json()["config"]["FileReferences"]["Moc"] == "Avatar.moc3"
        update = client.post("/api/live2d/model_config_by_id/200", json={
            "FileReferences": {"Motions": {"Idle": []}, "Expressions": [{"Name": "x", "File": "x.exp3.json"}]},
        })
        assert update.status_code == 200, update.text
    saved = model_file.read_text(encoding="utf-8")
    assert "x.exp3.json" in saved and "Avatar.moc3" in saved


def test_mount_serves_other_libraries_when_primary_vanishes_before_first_request(libraries):
    for library, content in ((libraries.a, b"from A"), (libraries.b, b"from B")):
        (library / "100").mkdir()
        (library / "100" / "Same.vrm").write_bytes(content)
    _write_model(libraries.b / "200", "vrm", "Bravo")
    app = FastAPI()
    app.mount("/workshop", create_workshop_static_files(str(libraries.a)), name="workshop")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    moved = libraries.tmp_path / "moved_away"
    libraries.a.rename(moved)
    with TestClient(app) as client:
        assert client.get("/workshop/200/Bravo.vrm").status_code == 200
        assert client.get("/workshop/100/Same.vrm").content == b"from B"
        # Primary back in place: it wins again for a file present in both libraries.
        moved.rename(libraries.a)
        assert client.get("/workshop/100/Same.vrm").content == b"from A"


def test_mount_with_existing_primary_still_checks_it(libraries, monkeypatch):
    mount = create_workshop_static_files(str(libraries.a))
    calls = []

    async def fake_check(self):
        calls.append(self.directory)

    monkeypatch.setattr(StaticFiles, "check_config", fake_check)
    import asyncio
    asyncio.run(mount.check_config())
    assert calls == [str(libraries.a)]


def test_by_id_url_and_config_routes_pick_the_same_nested_model(libraries, monkeypatch):
    real_walk = os.walk

    def reversed_walk(*args, **kwargs):
        # Directory order is filesystem-specific; make it the unfavourable one.
        for root, dirs, files in real_walk(*args, **kwargs):
            dirs.sort(reverse=True)
            files.sort(reverse=True)
            yield root, dirs, files

    monkeypatch.setattr(frontend_utils.os, "walk", reversed_walk)
    item = libraries.b / "200"
    for sub in ("b_second", "a_first"):
        _write_model(item / sub, "live2d", sub)
    register_workshop_roots_from_items([{"installedFolder": str(item)}])
    url = frontend_utils.find_model_by_workshop_item_id("200")
    assert url == "/workshop/200/a_first/a_first.model3.json"
    with _client(libraries, []) as client:
        config = client.get("/api/live2d/model_config_by_id/200").json()["config"]
    assert config["FileReferences"]["Moc"] == "a_first.moc3"


def test_url_and_config_routes_share_the_folder_named_preference(libraries):
    """A folder-named model nested later still wins for the URL and both config routes."""
    item = libraries.b / "200"
    _write_model(item / "a_first", "live2d", "Other")
    _write_model(item / "z_last", "live2d", "200")
    register_workshop_roots_from_items([{"installedFolder": str(item)}])
    target = item / "z_last" / "200.model3.json"
    assert frontend_utils.find_workshop_item_model_file(str(item)) == str(target)
    assert frontend_utils.find_model_by_workshop_item_id("200") == "/workshop/200/z_last/200.model3.json"
    with _client(libraries, []) as client:
        assert client.get("/api/live2d/model_config_by_id/200").json()["config"]["FileReferences"]["Moc"] == "200.moc3"
        assert client.post("/api/live2d/model_config_by_id/200", json={
            "FileReferences": {"Expressions": [{"Name": "x", "File": "x.exp3.json"}]},
        }).status_code == 200
    assert "x.exp3.json" in target.read_text(encoding="utf-8")
    assert "x.exp3.json" not in (item / "a_first" / "Other.model3.json").read_text(encoding="utf-8")


def test_flat_folder_named_model_is_chosen_by_url_and_config_alike(libraries):
    item = libraries.b / "200"
    _write_model(item, "live2d", "Aaa")
    # Only an exact folder-named file counts, not one that merely ends with the id.
    _write_model(item, "live2d", "Bravo200")
    _write_model(item, "live2d", "200")
    register_workshop_roots_from_items([{"installedFolder": str(item)}])
    assert frontend_utils.find_model_by_workshop_item_id("200") == "/workshop/200/200.model3.json"
    assert frontend_utils.find_workshop_item_model_file(str(item)) == str(item / "200.model3.json")


def test_find_models_lists_live2d_items_from_every_library(libraries):
    # find_models names a model after its folder, here the item id.
    _write_model(libraries.a / "100", "live2d", "Alpha")
    _write_model(libraries.b / "200", "live2d", "Bravo")
    before = {m["name"]: m for m in frontend_utils.find_models() if m["source"] == "workshop"}
    assert set(before) == {"100"}
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    models = {m["name"]: m for m in frontend_utils.find_models() if m["source"] == "workshop"}
    assert models["100"]["path"] == "/workshop/100/Alpha.model3.json"
    assert models["200"]["path"] == "/workshop/200/Bravo.model3.json"
    assert models["200"]["item_id"] == "200"
    with _client(libraries, []) as client:
        assert client.get(models["200"]["path"]).status_code == 200


def test_concurrent_registration_keeps_every_root(libraries):
    import threading

    roots = []
    for index in range(40):
        root = libraries.tmp_path / f"Lib{index}" / "content"
        (root / "1").mkdir(parents=True)
        roots.append(root)
    barrier = threading.Barrier(len(roots))

    def register(root):
        barrier.wait()
        register_workshop_roots_from_items([{"installedFolder": str(root / "1")}])

    threads = [threading.Thread(target=register, args=(root,)) for root in roots]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(libraries.config.get_steam_workshop_library_roots()) == sorted(str(r) for r in roots)


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="needs a non-root POSIX user")
def test_denied_file_in_one_library_does_not_hide_the_next(libraries):
    locked = libraries.a / "100"
    locked.mkdir()
    (locked / "Same.vrm").write_bytes(b"from A")
    (libraries.b / "100").mkdir()
    (libraries.b / "100" / "Same.vrm").write_bytes(b"from B")
    locked.chmod(0)
    try:
        with _client(libraries, [], static_cls=StaticFiles) as client:
            assert client.get("/workshop/100/Same.vrm").status_code == 401
        with _client(libraries, []) as client:
            # Only library A known: same 401 as before.
            assert client.get("/workshop/100/Same.vrm").status_code == 401
            register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "100")}])
            response = client.get("/workshop/100/Same.vrm")
            assert response.status_code == 200
            assert response.content == b"from B"
    finally:
        locked.chmod(0o755)


def test_model_files_by_id_uses_the_same_model_as_the_config_routes(libraries):
    item = libraries.b / "200"
    _write_model(item, "live2d", "Other")
    (item / "Other.motion3.json").write_text("{}", encoding="utf-8")
    nested = item / "nested"
    _write_model(nested, "live2d", "200")
    (nested / "motions").mkdir()
    (nested / "motions" / "idle.motion3.json").write_text("{}", encoding="utf-8")
    register_workshop_roots_from_items([{"installedFolder": str(item)}])
    with _client(libraries, []) as client:
        files = client.get("/api/live2d/model_files_by_id/200").json()
        config = client.get("/api/live2d/model_config_by_id/200").json()["config"]
        assert files["success"] is True
        assert files["model_config_url"] == "/workshop/200/nested/200.model3.json"
        assert files["motion_files"] == ["motions/idle.motion3.json"]
        assert config["FileReferences"]["Moc"] == "200.moc3"
        assert client.get(files["model_config_url"]).status_code == 200


def test_model_files_by_id_flat_item_is_unchanged(libraries):
    _write_model(libraries.b / "200", "live2d", "Bravo")
    (libraries.b / "200" / "idle.motion3.json").write_text("{}", encoding="utf-8")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    with _client(libraries, []) as client:
        files = client.get("/api/live2d/model_files_by_id/200").json()
    assert files["model_config_url"] == "/workshop/200/Bravo.model3.json"
    assert files["motion_files"] == ["idle.motion3.json"]


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="needs a non-root POSIX user")
def test_unreadable_item_in_first_library_does_not_stop_the_search(libraries):
    locked = libraries.a / "200"
    _write_model(locked, "live2d", "Locked")
    _write_model(libraries.b / "200", "live2d", "Bravo")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    locked.chmod(0)
    try:
        assert frontend_utils.find_workshop_item_by_id("200") == (str(libraries.b / "200"), "/workshop")
    finally:
        locked.chmod(0o755)
    # Readable again: the configured library wins.
    assert frontend_utils.find_workshop_item_by_id("200") == (str(libraries.a / "200"), "/workshop")


def test_find_model_config_file_builds_workshop_urls_from_the_item_folder(libraries):
    _write_model(libraries.a / "100", "live2d", "Alpha")
    _write_model(libraries.a / "Gamma", "live2d", "Gamma")
    _write_model(libraries.b / "200", "live2d", "Bravo")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    assert frontend_utils.find_model_config_file("Alpha") == "/workshop/100/Alpha.model3.json"
    assert frontend_utils.find_model_config_file("Bravo") == "/workshop/200/Bravo.model3.json"
    # A model folder directly in the root keeps its old URL.
    assert frontend_utils.find_model_config_file("Gamma") == "/workshop/Gamma/Gamma.model3.json"
    with _client(libraries, []) as client:
        assert client.get("/workshop/200/Bravo.model3.json").status_code == 200


def test_model_files_by_id_static_fallback_uses_the_shared_choice(libraries, monkeypatch):
    """No Workshop directory at all: the by-id /static fallback still agrees with the config route."""
    libraries.config._primary_root = str(libraries.tmp_path / "missing_root")
    work = libraries.tmp_path / "cwd"
    item = work / "static" / "200"
    _write_model(item, "live2d", "100")
    _write_model(item, "live2d", "200")
    monkeypatch.chdir(work)
    real_listdir = os.listdir
    # Make "100" the first listed file, which the old first-listed rule would pick.
    monkeypatch.setattr(os, "listdir", lambda path=".": sorted(real_listdir(path)))
    assert frontend_utils.find_workshop_item_by_id("200") == (os.path.join("static", "200"), "/static")
    with _client(libraries, []) as client:
        files = client.get("/api/live2d/model_files_by_id/200").json()
        config = client.get("/api/live2d/model_config_by_id/200").json()["config"]
    assert files["success"] is True
    # The URL keeps the item folder that /static serves it from.
    assert files["model_config_url"] == "/static/200/200.model3.json"
    assert config["FileReferences"]["Moc"] == "200.moc3"


@pytest.mark.parametrize("kind", ["vrm", "mmd"])
def test_by_id_lookup_returns_the_library_holding_a_non_live2d_item(libraries, kind):
    """Unsubscribe falls back to this path when Steam cannot report the install folder."""
    _write_model(libraries.b / "300", kind, "Charlie")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "300")}])
    assert frontend_utils.find_workshop_item_by_id("300") == (str(libraries.b / "300"), "/workshop")
    # Present in both: the configured library still wins.
    _write_model(libraries.a / "300", kind, "Charlie")
    assert frontend_utils.find_workshop_item_by_id("300") == (str(libraries.a / "300"), "/workshop")
    # Absent everywhere: the old default path in the configured library.
    assert frontend_utils.find_workshop_item_by_id("400") == (str(libraries.a / "400"), "/workshop")


def test_by_id_lookup_skips_a_non_live2d_item_folder_linked_out_of_its_library(libraries):
    outside = libraries.tmp_path / "outside" / "300"
    _write_model(outside, "vrm", "Charlie")
    _link(libraries.b / "300", outside, is_dir=True)
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    (libraries.b / "200").mkdir()
    assert frontend_utils.find_workshop_item_by_id("300") == (str(libraries.a / "300"), "/workshop")


def test_cloudsave_binding_roots_cover_every_library(libraries):
    from utils.cloudsave_runtime.bindings import (
        _collect_binding_workshop_roots,
        _infer_binding_source_from_resolved_path,
    )

    _write_model(libraries.b / "200", "vrm", "Bravo")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    roots = _collect_binding_workshop_roots(libraries.config)
    assert roots[0] == libraries.a
    assert libraries.b in roots
    assert _infer_binding_source_from_resolved_path(
        libraries.config,
        resolved_path=libraries.b / "200" / "Bravo.vrm",
        asset_source="",
        asset_source_id="",
    ) == ("steam_workshop", "200")


def _vrm_character(item_id: str, name: str) -> dict:
    return {"_reserved": {"avatar": {
        "model_type": "vrm",
        "asset_source": "steam_workshop",
        "asset_source_id": item_id,
        "vrm": {"model_path": f"/workshop/{item_id}/{name}.vrm"},
    }}}


@pytest.mark.parametrize("library", ["a", "b"])
def test_cloudsave_binding_survives_an_unreadable_model_in_any_library(libraries, monkeypatch, library):
    from utils.cloudsave_runtime import bindings

    _write_model(getattr(libraries, library) / "200", "vrm", "Bravo")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    payload = _vrm_character("200", "Bravo")

    readable = bindings._derive_character_binding_summary(libraries.config, "Neko", payload)
    assert readable["asset_state"] == "ready"
    assert readable["asset_fingerprint"]

    def _denied(path):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(bindings, "_sha256_file", _denied)
    summary = bindings._derive_character_binding_summary(libraries.config, "Neko", payload)
    # Same as a model that is not on disk: still backed up, marked for download.
    assert summary["asset_state"] == "downloadable"
    assert summary["asset_fingerprint"] == ""
    assert summary["asset_source"] == "steam_workshop"
    assert summary["asset_source_id"] == "200"
    assert summary["last_verified_at"] == ""


@pytest.mark.parametrize("kind", ["vrm", "live2d"])
def test_cloudsave_binding_skips_a_library_it_cannot_stat(libraries, monkeypatch, kind):
    """A library that raises on stat (no traverse permission) must not hide the next one."""
    from utils.cloudsave_runtime import bindings

    _write_model(libraries.a / "200", kind, "Bravo")
    _write_model(libraries.b / "200", kind, "Bravo")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    if kind == "vrm":
        payload = _vrm_character("200", "Bravo")
    else:
        payload = {"_reserved": {"avatar": {
            "model_type": "live2d",
            "asset_source": "steam_workshop",
            "asset_source_id": "200",
            "live2d": {"model_path": "Bravo.model3.json"},
        }}}

    locked = str(libraries.a)
    real_is_file, real_is_dir = Path.is_file, Path.is_dir

    def _guard(real):
        def check(self):
            if str(self).startswith(locked):
                raise PermissionError(13, "Permission denied", str(self))
            return real(self)
        return check

    monkeypatch.setattr(Path, "is_file", _guard(real_is_file))
    monkeypatch.setattr(Path, "is_dir", _guard(real_is_dir))
    summary = bindings._derive_character_binding_summary(libraries.config, "Neko", payload)
    assert summary["asset_state"] == "ready"
    assert summary["asset_source"] == "steam_workshop"
    assert summary["asset_source_id"] == "200"
    assert summary["asset_fingerprint"]

    # Nothing readable anywhere: unresolved, not an error.
    (libraries.b / "200" / ("Bravo.vrm" if kind == "vrm" else "Bravo.model3.json")).unlink()
    summary = bindings._derive_character_binding_summary(libraries.config, "Neko", payload)
    assert summary["asset_state"] == "downloadable"
    assert summary["asset_fingerprint"] == ""


def test_cloudsave_binding_moves_on_when_a_copy_exists_but_cannot_be_opened(libraries, monkeypatch):
    from utils.cloudsave_runtime import bindings
    import builtins

    _write_model(libraries.a / "200", "vrm", "Bravo")
    (libraries.b / "200").mkdir()
    (libraries.b / "200" / "Bravo.vrm").write_bytes(b"glTF from B")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    payload = _vrm_character("200", "Bravo")

    # Both readable: library A (the configured root) wins.
    resolved = bindings._resolve_binding_file_path(
        libraries.config, model_type="vrm", model_ref="/workshop/200/Bravo.vrm",
        asset_source="steam_workshop", asset_source_id="200")
    assert resolved == libraries.a / "200" / "Bravo.vrm"

    locked = str(libraries.a)
    real_open = builtins.open

    def _open(file, *args, **kwargs):
        if str(file).startswith(locked):
            raise PermissionError(13, "Permission denied", str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)
    summary = bindings._derive_character_binding_summary(libraries.config, "Neko", payload)
    assert summary["asset_state"] == "ready"
    assert summary["fallback_model_ref"] == ""
    expected = bindings._sha256_file(libraries.b / "200" / "Bravo.vrm")
    assert summary["asset_fingerprint"] == expected


def test_cloudsave_registered_libraries_come_before_the_local_workshop_dir(libraries, tmp_path):
    from utils.cloudsave_runtime.bindings import _collect_binding_workshop_roots

    libraries.config.workshop_dir = tmp_path / "local_workshop"
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    assert _collect_binding_workshop_roots(libraries.config) == [
        libraries.a, libraries.b, tmp_path / "local_workshop"]


def test_by_id_lookup_prefers_a_non_empty_item_folder_over_an_empty_leftover(libraries):
    (libraries.a / "300").mkdir()
    _write_model(libraries.b / "300", "vrm", "Charlie")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "300")}])
    assert frontend_utils.find_workshop_item_by_id("300") == (str(libraries.b / "300"), "/workshop")
    # Only empty folders: the first one, as before.
    (libraries.b / "300" / "Charlie.vrm").unlink()
    assert frontend_utils.find_workshop_item_by_id("300") == (str(libraries.a / "300"), "/workshop")


@pytest.mark.parametrize("asset_source", ["steam_workshop", ""])
def test_cloudsave_live2d_fallbacks_try_the_next_ranked_copy(libraries, monkeypatch, asset_source):
    """Nested Live2D model in both libraries: when the best-ranked copy cannot be
    opened, the workshop fallback (stored source) and the local fallback (no source)
    both move on to the other copy."""
    from utils.cloudsave_runtime import bindings
    import builtins

    for lib in (libraries.a, libraries.b):
        _write_model(lib / "200" / "Bravo", "live2d", "Bravo")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])

    def resolve():
        return bindings._resolve_binding_file_path(
            libraries.config, model_type="live2d", model_ref="Bravo.model3.json",
            asset_source=asset_source, asset_source_id="200" if asset_source else "")

    first = resolve()
    assert first is not None and first.name == "Bravo.model3.json"
    blocked_root = libraries.a if str(first).startswith(str(libraries.a)) else libraries.b
    other_root = libraries.b if blocked_root is libraries.a else libraries.a

    real_open = builtins.open

    def _open(file, *args, **kwargs):
        if str(file).startswith(str(blocked_root)):
            raise PermissionError(13, "Permission denied", str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)
    assert resolve() == other_root / "200" / "Bravo" / "Bravo.model3.json"


@pytest.mark.parametrize("model_ref", ["/workshop/200/Bravo.model3.json", "Bravo.model3.json"])
def test_cloudsave_live2d_fallback_never_swaps_in_a_different_model(libraries, monkeypatch, model_ref):
    """The bound model exists but cannot be read: another model in the same item
    must not be reported in its place (wrong fingerprint, hidden read failure)."""
    from utils.cloudsave_runtime import bindings
    import builtins

    item = libraries.a / "200"
    _write_model(item, "live2d", "Bravo")
    _write_model(item, "live2d", "Other")
    blocked = item / "Bravo.model3.json"
    real_open = builtins.open

    def _open(file, *args, **kwargs):
        if str(file) == str(blocked):
            raise PermissionError(13, "Permission denied", str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)
    resolved = bindings._resolve_binding_file_path(
        libraries.config, model_type="live2d", model_ref=model_ref,
        asset_source="steam_workshop", asset_source_id="200")
    assert resolved is None
    payload = {"_reserved": {"avatar": {
        "model_type": "live2d",
        "asset_source": "steam_workshop",
        "asset_source_id": "200",
        "live2d": {"model_path": model_ref},
    }}}
    summary = bindings._derive_character_binding_summary(libraries.config, "Neko", payload)
    assert summary["asset_state"] == "downloadable"
    assert summary["asset_fingerprint"] == ""


def test_cloudsave_local_live2d_fallback_never_swaps_in_a_different_model(libraries, monkeypatch):
    """Same as above through the local fallback (no stored source, nested folder)."""
    from utils.cloudsave_runtime import bindings
    import builtins

    folder = libraries.a / "200" / "Bravo"
    _write_model(folder, "live2d", "Bravo")
    _write_model(folder, "live2d", "Other")
    blocked = folder / "Bravo.model3.json"
    real_open = builtins.open

    def _open(file, *args, **kwargs):
        if str(file) == str(blocked):
            raise PermissionError(13, "Permission denied", str(file))
        return real_open(file, *args, **kwargs)

    resolve = lambda: bindings._resolve_binding_file_path(  # noqa: E731
        libraries.config, model_type="live2d", model_ref="Bravo.model3.json",
        asset_source="", asset_source_id="")
    assert resolve() == blocked
    monkeypatch.setattr(builtins, "open", _open)
    assert resolve() is None


def test_vrm_workshop_roots_skip_a_library_that_raises_on_stat(libraries, monkeypatch):
    """A registered library that later loses access must not turn into a 500."""
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    locked = libraries.b.resolve()
    real_is_dir = Path.is_dir

    def _is_dir(self):
        if self == locked:
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", _is_dir)
    roots = vrm_router._workshop_roots()
    assert locked not in roots
    assert libraries.a.resolve() in roots


def _model_files_url(client, model_id):
    files = client.get(f"/api/live2d/model_files_by_id/{model_id}").json()
    assert files["success"] is True, files
    return files["model_config_url"]


def test_model_files_by_name_fallback_uses_the_item_folder_in_workshop_urls(libraries):
    """A model name that is not an item id falls back to a by-name lookup under /workshop."""
    # Item folder whose config file carries the model name.
    _write_model(libraries.b / "200", "live2d", "Bravo")
    # Model folder one level inside an item folder.
    _write_model(libraries.a / "300" / "Charlie", "live2d", "Charlie")
    register_workshop_roots_from_items([{"installedFolder": str(libraries.b / "200")}])
    with _client(libraries, []) as client:
        bravo = _model_files_url(client, "Bravo")
        charlie = _model_files_url(client, "Charlie")
        assert bravo == "/workshop/200/Bravo.model3.json"
        assert charlie == "/workshop/300/Charlie/Charlie.model3.json"
        assert client.get(bravo).status_code == 200
        assert client.get(charlie).status_code == 200


def test_model_files_by_name_fallback_keeps_both_levels_of_user_mods_urls(libraries, monkeypatch):
    """A /user_mods model two levels deep keeps its mod folder in the URL."""
    # /user_mods shares its root with the configured Workshop folder, which is searched
    # first; skip that pass so the lookup reaches the /user_mods branch.
    monkeypatch.setattr(frontend_utils, "_resolve_workshop_search_dirs", lambda: [])
    _write_model(libraries.a / "mods" / "Delta", "live2d", "Delta")
    _write_model(libraries.a / "Foxtrot", "live2d", "Foxtrot")
    with _client(libraries, []) as client:
        client.app.mount("/user_mods", StaticFiles(directory=str(libraries.a)), name="user_mods")
        delta = _model_files_url(client, "Delta")
        foxtrot = _model_files_url(client, "Foxtrot")
        assert delta == "/user_mods/mods/Delta/Delta.model3.json"
        # A model folder directly in the mod root keeps its old URL.
        assert foxtrot == "/user_mods/Foxtrot/Foxtrot.model3.json"
        assert client.get(delta).status_code == 200
        assert client.get(foxtrot).status_code == 200


def test_find_model_config_file_keeps_the_mod_folder_in_user_mods_urls(libraries, monkeypatch):
    # Same setup as above: skip the Workshop pass so the /user_mods branch is reached.
    monkeypatch.setattr(frontend_utils, "_resolve_workshop_search_dirs", lambda: [])
    _write_model(libraries.a / "mods" / "Delta", "live2d", "Delta")
    # A mod folder that is itself the model folder, named differently from the model.
    _write_model(libraries.a / "pack", "live2d", "Kilo")
    _write_model(libraries.a / "Foxtrot", "live2d", "Foxtrot")
    assert frontend_utils.find_model_config_file("Delta") == "/user_mods/mods/Delta/Delta.model3.json"
    assert frontend_utils.find_model_config_file("Kilo") == "/user_mods/pack/Kilo.model3.json"
    # A model folder directly in the mod root keeps its old URL.
    assert frontend_utils.find_model_config_file("Foxtrot") == "/user_mods/Foxtrot/Foxtrot.model3.json"


def test_model_files_urls_for_ordinary_layouts_are_unchanged(libraries):
    # By item id, flat and nested.
    _write_model(libraries.a / "100", "live2d", "Alpha")
    _write_model(libraries.a / "400" / "Hotel", "live2d", "Hotel")
    # By name, a model folder directly in the Workshop root.
    _write_model(libraries.a / "Gamma", "live2d", "Gamma")
    # By name, a user Live2D model with its config in a subfolder.
    _write_model(libraries.config.live2d_dir / "Echo" / "runtime", "live2d", "Echo")
    with _client(libraries, []) as client:
        assert _model_files_url(client, "100") == "/workshop/100/Alpha.model3.json"
        assert _model_files_url(client, "400") == "/workshop/400/Hotel/Hotel.model3.json"
        assert _model_files_url(client, "Gamma") == "/workshop/Gamma/Gamma.model3.json"
        assert _model_files_url(client, "Echo") == "/user_live2d/Echo/runtime/Echo.model3.json"
