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
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock
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
    return SimpleNamespace(app=app, static_anim=static_anim, user_anim=user_anim)


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
    with TestClient(mmd_app.app) as client:
        response = client.get("/api/model/mmd/models")

    assert response.status_code == 200, response.text
    assert sorted(model["url"] for model in response.json()["models"]) == [
        "/static/mmd/Builtin.pmx",
        "/user_mmd/Good/Good.pmx",
        "/workshop/123/Shop.pmx",
    ]
    assert (mmd_app.mmd_dir / "Locked" / "Locked.PMX").exists()
