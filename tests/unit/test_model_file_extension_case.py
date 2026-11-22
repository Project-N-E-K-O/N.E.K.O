"""Model and animation files whose extension is not lower case ('Foo.VMD').

The upload endpoints check extensions case-insensitively, so 'Foo.VMD',
'Foo.VRMA' and 'Foo.PMX' are accepted. The lists used to scan with
``glob('*.vmd')`` / ``rglob('*.pmx')``, which is case-sensitive on Linux: such a
file was stored but never listed (and an MMD ZIP holding 'Foo.PMX' failed with
"no model found" after extraction). These tests upload or create synthetic files
under ``tmp_path`` and check that every list (including the VRM part of
``/api/live2d/user_models``) returns them next to the lower-case ones, that the
listed URL is served, and that the MMD animation can be deleted.
"""

import io
import os
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.main_server.web_app import CustomStaticFiles, StaticRootFiles
from main_routers import mmd_router, vrm_router


pytestmark = pytest.mark.unit


def _require_case_sensitive_fs(directory):
    probe = directory / "case-probe.tmp"
    probe.write_bytes(b"x")
    try:
        if (directory / "CASE-PROBE.TMP").exists():
            pytest.skip("file system is case-insensitive; glob already matched any case there")
    finally:
        probe.unlink()


def _request_path(url: str) -> str:
    """Per-segment escaping, the same rule as the frontend toRequestUrl."""
    return "/".join(quote(segment, safe="") for segment in url.split("/"))


@pytest.fixture
def mmd_app(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    static_mmd = project_root / "static" / "mmd"
    static_anim = static_mmd / "animation"
    static_anim.mkdir(parents=True)
    mmd_dir = tmp_path / "mmd"
    user_anim = mmd_dir / "animation"
    user_anim.mkdir(parents=True)
    workshop_item = tmp_path / "workshop" / "123"
    workshop_item.mkdir(parents=True)
    _require_case_sensitive_fs(tmp_path)
    config = SimpleNamespace(project_root=project_root, mmd_dir=mmd_dir, mmd_animation_dir=user_anim)
    monkeypatch.setattr(mmd_router, "get_config_manager", lambda: config)
    monkeypatch.setattr(mmd_router, "get_subscribed_workshop_items", AsyncMock(return_value={
        "success": True, "items": [{"installedFolder": str(workshop_item), "publishedFileId": "123"}],
    }))
    app = FastAPI()
    app.include_router(mmd_router.router)
    # Same mount classes and layout as the main server (app/main_server/web_app.py).
    app.mount("/user_mmd/animation", CustomStaticFiles(directory=str(user_anim)))
    app.mount("/static", StaticRootFiles(directory=str(project_root / "static")))
    return SimpleNamespace(app=app, static_mmd=static_mmd, static_anim=static_anim,
                           mmd_dir=mmd_dir, user_anim=user_anim, workshop_item=workshop_item)


@pytest.fixture
def vrm_app(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    static_anim = project_root / "static" / "vrm" / "animation"
    static_anim.mkdir(parents=True)
    vrm_dir = tmp_path / "vrm"
    user_anim = vrm_dir / "animation"
    user_anim.mkdir(parents=True)
    _require_case_sensitive_fs(tmp_path)
    config = SimpleNamespace(project_root=project_root, vrm_dir=vrm_dir, vrm_animation_dir=user_anim,
                             ensure_vrm_directory=lambda: True)
    monkeypatch.setattr(vrm_router, "get_config_manager", lambda: config)
    app = FastAPI()
    app.include_router(vrm_router.router)
    app.mount("/user_vrm/animation", CustomStaticFiles(directory=str(user_anim)))
    app.mount("/static", StaticRootFiles(directory=str(project_root / "static")))
    return SimpleNamespace(app=app, static_anim=static_anim, user_anim=user_anim,
                           static_vrm=static_anim.parent, vrm_dir=vrm_dir)


MMD_ANIMATION_NAMES = ["lower.vmd", "UPPER.VMD", "Mixed.Vmd"]


def test_mmd_uploaded_animation_with_any_extension_case_is_listed_played_and_deleted(mmd_app):
    (mmd_app.static_anim / "Builtin.VMD").write_bytes(b"static-vmd")
    (mmd_app.static_anim / "builtin.vmd").write_bytes(b"static-vmd")
    (mmd_app.user_anim / "notes.txt").write_bytes(b"not an animation")

    with TestClient(mmd_app.app) as client:
        for name in MMD_ANIMATION_NAMES:
            upload = client.post("/api/model/mmd/upload_animation",
                                 files={"file": (name, b"vmd-" + name.encode(), "application/octet-stream")})
            assert upload.status_code == 200, upload.text
            assert upload.json()["file_path"] == f"/user_mmd/animation/{name}"

        listed = client.get("/api/model/mmd/animations").json()["animations"]
        assert sorted(item["url"] for item in listed) == sorted(
            ["/static/mmd/animation/Builtin.VMD", "/static/mmd/animation/builtin.vmd"]
            + [f"/user_mmd/animation/{name}" for name in MMD_ANIMATION_NAMES]
        )
        assert {item["name"] for item in listed} >= {"lower", "UPPER", "Mixed"}

        # The upload response's file_path equals the list URL, which the model manager
        # matches to auto-select the new animation.
        for name in MMD_ANIMATION_NAMES:
            served = client.get(_request_path(f"/user_mmd/animation/{name}"))
            assert served.status_code == 200
            assert served.content == b"vmd-" + name.encode()
        assert client.get("/static/mmd/animation/Builtin.VMD").status_code == 200

        delete_list = client.get("/api/model/mmd/animations/list").json()["animations"]
        assert sorted(item["filename"] for item in delete_list) == sorted(MMD_ANIMATION_NAMES)

        deleted = client.request("DELETE", "/api/model/mmd/animation",
                                 json={"url": "/user_mmd/animation/UPPER.VMD"})
        assert deleted.status_code == 200, deleted.text
        assert not (mmd_app.user_anim / "UPPER.VMD").exists()
        remaining = client.get("/api/model/mmd/animations").json()["animations"]
        assert "/user_mmd/animation/UPPER.VMD" not in {item["url"] for item in remaining}
        assert "/user_mmd/animation/lower.vmd" in {item["url"] for item in remaining}


def test_mmd_models_with_any_extension_case_are_listed(mmd_app):
    (mmd_app.static_mmd / "Builtin.PMX").write_bytes(b"pmx")
    for folder, name in [("Lower", "lower.pmx"), ("Upper", "UPPER.PMX"), ("Mixed", "Mixed.Pmd")]:
        (mmd_app.mmd_dir / folder).mkdir()
        (mmd_app.mmd_dir / folder / name).write_bytes(b"model")
    (mmd_app.mmd_dir / "Textures").mkdir()
    (mmd_app.mmd_dir / "Textures" / "skin.png").write_bytes(b"png")
    (mmd_app.workshop_item / "Shop.PMD").write_bytes(b"pmd")

    with TestClient(mmd_app.app) as client:
        upload = client.post("/api/model/mmd/upload",
                             files={"file": ("Top.PMX", b"pmx", "application/octet-stream")})
        assert upload.status_code == 200, upload.text
        models = client.get("/api/model/mmd/models").json()["models"]

    by_url = {model["url"]: model for model in models}
    assert set(by_url) == {
        "/static/mmd/Builtin.PMX",
        "/user_mmd/Lower/lower.pmx",
        "/user_mmd/Upper/UPPER.PMX",
        "/user_mmd/Mixed/Mixed.Pmd",
        "/user_mmd/Top.PMX",
        "/user_mmd/Textures",  # a folder without any model is still listed as broken
        "/workshop/123/Shop.PMD",
    }
    assert by_url["/user_mmd/Textures"].get("broken") is True
    # Folders whose only model has an upper-case extension are not import residue.
    assert not by_url["/user_mmd/Upper/UPPER.PMX"].get("broken")
    assert not by_url["/user_mmd/Mixed/Mixed.Pmd"].get("broken")
    # The type is reported in lower case whatever the file name says.
    assert {by_url[url]["type"] for url in by_url if url != "/user_mmd/Textures"} == {"pmx", "pmd"}


def test_mmd_models_treat_reserved_folders_in_any_case_as_reserved(mmd_app):
    # Import and delete compare reserved folder names in lower case; the list must too,
    # otherwise it offers entries that delete then refuses.
    (mmd_app.mmd_dir / "Animation").mkdir()
    (mmd_app.mmd_dir / "Animation" / "stray.pmx").write_bytes(b"pmx")
    (mmd_app.mmd_dir / "Emotion_Config").mkdir()
    (mmd_app.mmd_dir / "Good").mkdir()
    (mmd_app.mmd_dir / "Good" / "Good.pmx").write_bytes(b"pmx")

    with TestClient(mmd_app.app) as client:
        models = client.get("/api/model/mmd/models").json()["models"]

    assert [model["url"] for model in models] == ["/user_mmd/Good/Good.pmx"]


def test_folders_named_like_models_are_not_models(mmd_app):
    # A leftover folder holding only a directory called 'old.PMX' is import residue.
    (mmd_app.mmd_dir / "Miku" / "old.PMX").mkdir(parents=True)
    (mmd_app.mmd_dir / "Residue" / "old.PMX").mkdir(parents=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Miku/Miku.pmx", b"pmx")

    with TestClient(mmd_app.app) as client:
        models = client.get("/api/model/mmd/models").json()["models"]
        assert {model["url"] for model in models if model.get("broken")} == {
            "/user_mmd/Miku", "/user_mmd/Residue",
        }
        # The residue folder can be deleted by its path...
        deleted = client.request("DELETE", "/api/model/mmd/model", json={"url": "/user_mmd/Residue"})
        assert deleted.status_code == 200, deleted.text
        assert not (mmd_app.mmd_dir / "Residue").exists()
        # ...and re-importing over it replaces it.
        response = client.post("/api/model/mmd/upload_zip",
                               files={"file": ("Miku.zip", buffer.getvalue(), "application/zip")})
        assert response.status_code == 200, response.text
        assert response.json()["model_url"] == "/user_mmd/Miku/Miku.pmx"


def test_mmd_zip_with_upper_case_model_extension_is_imported(mmd_app):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Miku/Miku.PMX", b"pmx")
        archive.writestr("Miku/tex.png", b"png")

    with TestClient(mmd_app.app) as client:
        response = client.post("/api/model/mmd/upload_zip",
                               files={"file": ("Miku.zip", buffer.getvalue(), "application/zip")})
        assert response.status_code == 200, response.text
        assert response.json()["model_url"] == "/user_mmd/Miku/Miku.PMX"
        assert (mmd_app.mmd_dir / "Miku" / "Miku.PMX").is_file()

        # A second import of the same folder is refused because a model is already there.
        again = client.post("/api/model/mmd/upload_zip",
                            files={"file": ("Miku.zip", buffer.getvalue(), "application/zip")})
        assert again.status_code == 400
        assert (mmd_app.mmd_dir / "Miku" / "Miku.PMX").is_file()

        # Deleting by folder path is refused for the same reason.
        by_folder = client.request("DELETE", "/api/model/mmd/model", json={"url": "/user_mmd/Miku"})
        assert by_folder.status_code == 400
        assert (mmd_app.mmd_dir / "Miku" / "Miku.PMX").is_file()


VRM_ANIMATION_NAMES = ["lower.vrma", "UPPER.VRMA", "Mixed.Vrma"]


def test_vrm_uploaded_animation_with_any_extension_case_is_listed_and_played(vrm_app):
    (vrm_app.static_anim / "wait01.vrma.gz").write_bytes(b"gz")
    (vrm_app.static_anim / "Idle.VRMA.GZ").write_bytes(b"gz")
    (vrm_app.user_anim / "Pose.VRM").write_bytes(b"vrm")
    (vrm_app.user_anim / "notes.txt").write_bytes(b"not an animation")

    with TestClient(vrm_app.app) as client:
        for name in VRM_ANIMATION_NAMES:
            upload = client.post("/api/model/vrm/upload_animation",
                                 files={"file": (name, b"vrma-" + name.encode(), "application/octet-stream")})
            assert upload.status_code == 200, upload.text
            assert upload.json()["file_path"] == f"/user_vrm/animation/{name}"

        listed = client.get("/api/model/vrm/animations").json()["animations"]
        assert {item["url"]: item["name"] for item in listed} == {
            "/static/vrm/animation/wait01.vrma.gz": "wait01",
            "/static/vrm/animation/Idle.VRMA.GZ": "Idle",
            "/user_vrm/animation/lower.vrma": "lower",
            "/user_vrm/animation/UPPER.VRMA": "UPPER",
            "/user_vrm/animation/Mixed.Vrma": "Mixed",
            "/user_vrm/animation/Pose.VRM": "Pose",
        }
        assert {item["url"]: item["type"] for item in listed}["/user_vrm/animation/Pose.VRM"] == "vrm"

        for name in VRM_ANIMATION_NAMES:
            served = client.get(_request_path(f"/user_vrm/animation/{name}"))
            assert served.status_code == 200
            assert served.content == b"vrma-" + name.encode()


def test_live2d_user_models_list_vrm_files_with_any_extension_case(tmp_path, monkeypatch):
    from main_routers import live2d_router

    live2d_dir = tmp_path / "live2d"
    live2d_dir.mkdir()
    vrm_dir = tmp_path / "vrm"
    vrm_dir.mkdir()
    _require_case_sensitive_fs(tmp_path)
    for name in ["lower.vrm", "UPPER.VRM", "Mixed.Vrm", "notes.txt"]:
        (vrm_dir / name).write_bytes(b"glTF")
    (vrm_dir / "Folder.VRM").mkdir()  # not a model file
    config = SimpleNamespace(live2d_dir=live2d_dir, readable_live2d_dir=None, vrm_dir=vrm_dir,
                             ensure_live2d_directory=lambda: True, ensure_vrm_directory=lambda: True)
    monkeypatch.setattr(live2d_router, "get_config_manager", lambda: config)
    app = FastAPI()
    app.include_router(live2d_router.router)
    with TestClient(app) as client:
        models = client.get("/api/live2d/user_models").json()["models"]
    assert sorted(model["path"] for model in models if model.get("type") == "vrm") == [
        "/user_vrm/Mixed.Vrm", "/user_vrm/UPPER.VRM", "/user_vrm/lower.vrm",
    ]


def test_existing_folder_with_an_uncheckable_model_path_is_kept(mmd_app, monkeypatch):
    # A model-named link into an unreadable folder makes Path.is_file() raise
    # PermissionError on Python 3.11. The folder must count as holding a model:
    # import and delete-by-folder refuse it instead of deleting it.
    from pathlib import Path

    folder = mmd_app.mmd_dir / "Miku"
    folder.mkdir()
    (folder / "Miku.PMX").write_bytes(b"old model")
    (folder / "tex.png").write_bytes(b"old texture")
    real_is_file = Path.is_file

    def is_file(self):
        if self.name == "Miku.PMX":
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Miku/Miku.pmx", b"new model")

    with TestClient(mmd_app.app) as client:
        response = client.post("/api/model/mmd/upload_zip",
                               files={"file": ("Miku.zip", buffer.getvalue(), "application/zip")})
        assert response.status_code == 400, response.text
        by_folder = client.request("DELETE", "/api/model/mmd/model", json={"url": "/user_mmd/Miku"})
        assert by_folder.status_code == 400, by_folder.text

    assert (folder / "Miku.PMX").read_bytes() == b"old model"
    assert (folder / "tex.png").read_bytes() == b"old texture"


def test_models_list_skips_uncheckable_paths_and_keeps_the_rest(mmd_app, monkeypatch):
    # An uncheckable model path (is_file() raising PermissionError) is left out
    # of the list without failing it, in all three sources; its user folder is
    # not offered as deletable residue.
    from pathlib import Path

    (mmd_app.static_mmd / "Builtin.pmx").write_bytes(b"pmx")
    (mmd_app.static_mmd / "Locked.PMX").write_bytes(b"pmx")
    (mmd_app.mmd_dir / "Good").mkdir()
    (mmd_app.mmd_dir / "Good" / "Good.pmx").write_bytes(b"pmx")
    (mmd_app.mmd_dir / "Locked").mkdir()
    (mmd_app.mmd_dir / "Locked" / "Locked.PMX").write_bytes(b"pmx")
    (mmd_app.workshop_item / "Shop.pmx").write_bytes(b"pmx")
    (mmd_app.workshop_item / "Locked.PMX").write_bytes(b"pmx")
    real_is_file = Path.is_file

    def is_file(self):
        if self.name == "Locked.PMX":
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file)
    logger = MagicMock()
    monkeypatch.setattr(mmd_router, "logger", logger)
    with TestClient(mmd_app.app) as client:
        response = client.get("/api/model/mmd/models")

    assert response.status_code == 200, response.text
    assert sorted(model["url"] for model in response.json()["models"]) == [
        "/static/mmd/Builtin.pmx",
        "/user_mmd/Good/Good.pmx",
        "/workshop/123/Shop.pmx",
    ]
    logger.warning.assert_any_call("跳过 3 个无法检查类型的 MMD 模型路径")
    assert (mmd_app.mmd_dir / "Locked" / "Locked.PMX").exists()


@pytest.mark.parametrize("locked", ["A.pmx", "B.PMX"])
def test_zip_import_returns_a_checkable_model(mmd_app, monkeypatch, locked):
    # The returned model_url is loaded by the frontend right away, so it must not
    # be a path whose type cannot be checked, whichever order rglob yields.
    from pathlib import Path

    real_is_file = Path.is_file

    def is_file(self):
        if self.name == locked:
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Miku/A.pmx", b"pmx")
        archive.writestr("Miku/B.PMX", b"pmx")

    with TestClient(mmd_app.app) as client:
        response = client.post("/api/model/mmd/upload_zip",
                               files={"file": ("Miku.zip", buffer.getvalue(), "application/zip")})

    assert response.status_code == 200, response.text
    other = "B.PMX" if locked == "A.pmx" else "A.pmx"
    assert response.json()["model_url"] == f"/user_mmd/Miku/{other}"


def test_live2d_user_models_skip_an_uncheckable_vrm_and_keep_scanning(tmp_path, monkeypatch):
    from pathlib import Path

    from main_routers import live2d_router

    live2d_dir = tmp_path / "live2d"
    live2d_dir.mkdir()
    vrm_dir = tmp_path / "vrm"
    vrm_dir.mkdir()
    for name in ["A.vrm", "Locked.VRM", "Z.VRM"]:
        (vrm_dir / name).write_bytes(b"glTF")
    config = SimpleNamespace(live2d_dir=live2d_dir, readable_live2d_dir=None, vrm_dir=vrm_dir,
                             ensure_live2d_directory=lambda: True, ensure_vrm_directory=lambda: True)
    monkeypatch.setattr(live2d_router, "get_config_manager", lambda: config)
    real_is_file = Path.is_file

    def is_file(self):
        if self.name == "Locked.VRM":
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_file(self)

    real_iterdir = Path.iterdir

    def iterdir(self):
        # Sorted, so 'Z.VRM' comes after the uncheckable entry.
        return iter(sorted(real_iterdir(self)))

    monkeypatch.setattr(Path, "is_file", is_file)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    app = FastAPI()
    app.include_router(live2d_router.router)
    with TestClient(app) as client:
        models = client.get("/api/live2d/user_models").json()["models"]
    assert sorted(model["path"] for model in models if model.get("type") == "vrm") == [
        "/user_vrm/A.vrm", "/user_vrm/Z.VRM",
    ]


def _raise_for_names(monkeypatch, method, names):
    """Make ``Path.<method>()`` raise PermissionError for the given names, as it
    does on Python 3.11 for e.g. a link into an unreadable folder."""
    from pathlib import Path

    real = getattr(Path, method)

    def check(self):
        if self.name in names:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, method, check)


def test_vrm_models_list_skips_uncheckable_paths_and_keeps_the_rest(vrm_app, monkeypatch):
    monkeypatch.setattr(vrm_router, "get_subscribed_workshop_items", AsyncMock(return_value={"success": True, "items": []}))
    (vrm_app.static_vrm / "Builtin.vrm").write_bytes(b"glTF")
    (vrm_app.static_vrm / "Locked.VRM").write_bytes(b"glTF")
    (vrm_app.vrm_dir / "Good.VRM").write_bytes(b"glTF")
    (vrm_app.vrm_dir / "Hidden.vrm").write_bytes(b"glTF")
    _raise_for_names(monkeypatch, "is_file", {"Locked.VRM", "Hidden.vrm"})
    logger = MagicMock()
    monkeypatch.setattr(vrm_router, "logger", logger)

    with TestClient(vrm_app.app) as client:
        response = client.get("/api/model/vrm/models")

    assert response.status_code == 200, response.text
    assert sorted(model["url"] for model in response.json()["models"]) == [
        "/static/vrm/Builtin.vrm", "/user_vrm/Good.VRM",
    ]
    logger.warning.assert_any_call("跳过 2 个无法检查类型的 VRM 模型路径")


def test_vrm_uncheckable_model_still_counts_as_existing(vrm_app, monkeypatch):
    # Like the MMD folder checks: a model whose type cannot be checked keeps
    # protecting its shared mappings instead of failing or reading as absent.
    (vrm_app.vrm_dir / "Avatar.vrm").write_bytes(b"glTF")
    (vrm_app.vrm_dir / "Avatar.VRM").write_bytes(b"glTF")
    (vrm_app.static_vrm / "Solo.vrm").write_bytes(b"glTF")
    _raise_for_names(monkeypatch, "is_file", {"Avatar.vrm", "Solo.vrm"})

    assert vrm_router._vrm_model_stem_exists("solo", [vrm_app.static_vrm])
    assert vrm_router._vrm_model_stem_exists("avatar", [vrm_app.vrm_dir])
    # Lookup skips the uncheckable canonical file and returns the readable variant.
    assert vrm_router._find_vrm_model_file(vrm_app.vrm_dir, "Avatar") == (vrm_app.vrm_dir / "Avatar.VRM").resolve()


def test_mmd_models_list_skips_an_uncheckable_top_folder(mmd_app, monkeypatch):
    # The residue scan over mmd_dir called is_dir() unguarded: one entry whose
    # type cannot be checked failed the whole list with 500.
    (mmd_app.mmd_dir / "Good").mkdir()
    (mmd_app.mmd_dir / "Good" / "Good.pmx").write_bytes(b"pmx")
    (mmd_app.mmd_dir / "Residue").mkdir()
    (mmd_app.mmd_dir / "Residue" / "tex.png").write_bytes(b"png")
    (mmd_app.mmd_dir / "Locked").mkdir()
    _raise_for_names(monkeypatch, "is_dir", {"Locked"})
    logger = MagicMock()
    monkeypatch.setattr(mmd_router, "logger", logger)

    with TestClient(mmd_app.app) as client:
        response = client.get("/api/model/mmd/models")

    assert response.status_code == 200, response.text
    models = response.json()["models"]
    assert sorted(model["url"] for model in models if not model.get("broken")) == ["/user_mmd/Good/Good.pmx"]
    assert sorted(model["url"] for model in models if model.get("broken")) == ["/user_mmd/Residue"]
    logger.warning.assert_any_call("跳过 1 个无法检查类型的 MMD 模型路径")


def test_mmd_delete_by_folder_refuses_an_uncheckable_folder(mmd_app, monkeypatch):
    # is_dir() raising on the folder answered 500; it is now refused like any
    # other non-deletable path, and the folder is kept.
    (mmd_app.mmd_dir / "Locked").mkdir()
    (mmd_app.mmd_dir / "Locked" / "tex.png").write_bytes(b"png")
    _raise_for_names(monkeypatch, "is_dir", {"Locked"})

    with TestClient(mmd_app.app) as client:
        response = client.request("DELETE", "/api/model/mmd/model", json={"url": "/user_mmd/Locked"})

    assert response.status_code == 400, response.text
    assert (mmd_app.mmd_dir / "Locked" / "tex.png").exists()


@pytest.mark.parametrize("url", ["/user_mmd/Residue", "/user_mmd/Model/Model.pmx"])
def test_mmd_delete_counts_around_an_uncheckable_file(mmd_app, monkeypatch, url):
    # The deleted-file count called is_file() unguarded, so one uncheckable entry
    # failed the delete with 500 and left the folder in place.
    for folder, files in {"Residue": ["tex.png", "Locked.png"],
                          "Model": ["Model.pmx", "tex.png", "Locked.png"]}.items():
        (mmd_app.mmd_dir / folder).mkdir()
        for name in files:
            (mmd_app.mmd_dir / folder / name).write_bytes(b"x")
    _raise_for_names(monkeypatch, "is_file", {"Locked.png"})

    with TestClient(mmd_app.app) as client:
        response = client.request("DELETE", "/api/model/mmd/model", json={"url": url})

    assert response.status_code == 200, response.text
    folder = url.split("/")[2]
    assert response.json()["deleted_files"] == (1 if folder == "Residue" else 2)
    assert not (mmd_app.mmd_dir / folder).exists()


@pytest.mark.parametrize("spelling", ["link", "relative"])
def test_mmd_zip_import_into_a_linked_or_relative_mmd_dir(mmd_app, monkeypatch, tmp_path, spelling):
    # The import resolves the extraction folder; relative_to(mmd_dir) with an
    # unresolved mmd_dir raised ValueError, and the error cleanup deleted the
    # model it had just extracted.
    if spelling == "link":
        mmd_dir = tmp_path / "mmd-link"
        try:
            mmd_dir.symlink_to(mmd_app.mmd_dir, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks are not available")
    else:
        monkeypatch.chdir(tmp_path)
        mmd_dir = mmd_app.mmd_dir.relative_to(tmp_path)
    config = SimpleNamespace(project_root=tmp_path / "project", mmd_dir=mmd_dir,
                             mmd_animation_dir=mmd_dir / "animation")
    monkeypatch.setattr(mmd_router, "get_config_manager", lambda: config)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Miku/Miku.pmx", b"pmx")
        archive.writestr("Miku/tex.png", b"png")

    with TestClient(mmd_app.app) as client:
        response = client.post("/api/model/mmd/upload_zip",
                               files={"file": ("Miku.zip", buffer.getvalue(), "application/zip")})

    assert response.status_code == 200, response.text
    assert response.json()["model_url"] == "/user_mmd/Miku/Miku.pmx"
    assert response.json()["file_count"] == 2
    assert (mmd_app.mmd_dir / "Miku" / "Miku.pmx").read_bytes() == b"pmx"


@pytest.fixture
def locked_vrm_link(vrm_app, tmp_path):
    """``vrm_dir/Foo.vrm`` links into a folder without permissions, so its type
    cannot be checked (``is_file()`` raises PermissionError on Python 3.11)."""
    def make(inside: bool):
        locked = (vrm_app.vrm_dir if inside else tmp_path) / "locked"
        locked.mkdir()
        target = locked / "Foo.vrm"
        target.write_bytes(b"glTF-target")
        link = vrm_app.vrm_dir / "Foo.vrm"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are not available")
        os.chmod(locked, 0)
        cleanups.append(locked)
        if vrm_router._file_check(link) is not None:
            pytest.skip("permissions do not block the type check here (e.g. running as root)")
        return link, target

    cleanups = []
    yield make
    for locked in cleanups:
        os.chmod(locked, 0o755)


def _upload_vrm(client, name):
    return client.post("/api/model/vrm/upload", files={"file": (name, b"glTF-new", "application/octet-stream")})


def _assert_names_the_unreachable_entry(error, name="Foo.vrm"):
    # The model list (and so the delete dialog) skips this entry: the upload
    # error has to say what it is and that it must be removed by hand.
    assert "无法访问" in error and f"条目 {name} " in error and "手动删除" in error, error


@pytest.mark.parametrize("route", ["name", "url"])
def test_vrm_delete_removes_an_uncheckable_link_that_blocks_upload(vrm_app, locked_vrm_link, route):
    # Upload counts the uncheckable Foo.vrm as existing ("delete it first"),
    # but deletion skipped it with 404 / failed with 500: it could not be cleared.
    link, target = locked_vrm_link(inside=True)

    with TestClient(vrm_app.app) as client:
        blocked = _upload_vrm(client, "Foo.vrm")
        assert blocked.status_code == 400, blocked.text
        _assert_names_the_unreachable_entry(blocked.json()["error"])
        if route == "name":
            response = client.delete("/api/model/vrm/model/Foo")
        else:
            response = client.request("DELETE", "/api/model/vrm/model", json={"url": "/user_vrm/Foo.vrm"})
        assert response.status_code == 200, response.text
        assert not link.is_symlink()
        uploaded = _upload_vrm(client, "Foo.vrm")

    assert uploaded.status_code == 200, uploaded.text
    # Only the link was removed; the file it pointed to is untouched.
    os.chmod(target.parent, 0o755)
    assert target.read_bytes() == b"glTF-target"
    assert (vrm_app.vrm_dir / "Foo.vrm").read_bytes() == b"glTF-new"


@pytest.mark.parametrize("route", ["name", "url"])
def test_vrm_delete_still_refuses_an_uncheckable_link_out_of_the_folder(vrm_app, locked_vrm_link, route):
    link, target = locked_vrm_link(inside=False)

    with TestClient(vrm_app.app) as client:
        # Deletion keeps refusing it, so the upload error sends the user to remove it by hand.
        blocked = _upload_vrm(client, "foo.vrm")
        assert blocked.status_code == 400, blocked.text
        _assert_names_the_unreachable_entry(blocked.json()["error"])
        if route == "name":
            response = client.delete("/api/model/vrm/model/Foo")
        else:
            response = client.request("DELETE", "/api/model/vrm/model", json={"url": "/user_vrm/Foo.vrm"})

    assert response.status_code == 400 and response.json()["error"] == "路径越界", response.text
    assert link.is_symlink()
    os.chmod(target.parent, 0o755)
    assert target.read_bytes() == b"glTF-target"


@pytest.mark.parametrize("route", ["name", "url"])
def test_vrm_delete_removes_an_uncheckable_link_under_any_user(vrm_app, monkeypatch, route):
    # Same case as above without chmod (which root ignores): the type check of
    # the link and its target is made to report "cannot check".
    target = vrm_app.vrm_dir / "real" / "target.vrm"
    target.parent.mkdir()
    target.write_bytes(b"glTF-target")
    link = vrm_app.vrm_dir / "Foo.vrm"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    real_check = vrm_router._file_check
    monkeypatch.setattr(vrm_router, "_file_check",
                        lambda path: None if path.name in {"Foo.vrm", "target.vrm"} else real_check(path))

    with TestClient(vrm_app.app) as client:
        _assert_names_the_unreachable_entry(_upload_vrm(client, "Foo.vrm").json()["error"])
        if route == "name":
            response = client.delete("/api/model/vrm/model/Foo")
        else:
            response = client.request("DELETE", "/api/model/vrm/model", json={"url": "/user_vrm/Foo.vrm"})

    assert response.status_code == 200, response.text
    assert not link.is_symlink()
    assert target.read_bytes() == b"glTF-target"


def test_vrm_upload_keeps_the_usual_error_when_a_listed_model_takes_the_name(vrm_app, monkeypatch):
    # A checkable Foo.VRM is listed and deletable from the UI: no hint about the link.
    (vrm_app.vrm_dir / "Foo.VRM").write_bytes(b"glTF")
    target = vrm_app.vrm_dir / "real" / "target.vrm"
    target.parent.mkdir()
    target.write_bytes(b"glTF-target")
    try:
        (vrm_app.vrm_dir / "Foo.vrm").symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    real_check = vrm_router._file_check
    monkeypatch.setattr(vrm_router, "_file_check",
                        lambda path: None if path.name in {"Foo.vrm", "target.vrm"} else real_check(path))

    with TestClient(vrm_app.app) as client:
        response = _upload_vrm(client, "Foo.vrm")

    assert response.status_code == 400, response.text
    assert response.json()["error"] == "VRM模型 Foo.vrm 已存在，请先删除或重命名现有模型"
