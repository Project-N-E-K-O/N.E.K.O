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
    assert path == os.path.join(str(libraries.a), "300")


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
