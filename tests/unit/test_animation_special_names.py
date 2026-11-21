"""Animation files whose names carry URL syntax ('#', '?', '%', ...).

The lists keep raw file names in ``url``: character config stores those raw
strings and the model manager matches saved idle selections against them, so
encoding the list would split one file into two identities. The players escape
the name only for the request (MMDCore.toRequestUrl / VRMAnimation.toRequestUrl,
covered by ``animation_request_url.test.js``). These tests pin the server side
of that contract with synthetic files under ``tmp_path``:

* the list returns the raw name;
* the name escaped per path segment is served by the real static mount class;
* the MMD delete endpoint (URL in the JSON body) deletes such a file.
"""

from types import SimpleNamespace
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.main_server.web_app import CustomStaticFiles
from main_routers import mmd_router, vrm_router


pytestmark = pytest.mark.unit

SPECIAL_STEMS = ["Dance #1", "what?", "50%", "two words", "全身展示", "a+b", "比 V 手势#2?%"]


def _request_path(url: str) -> str:
    """Per-segment escaping, the same rule as the frontend toRequestUrl."""
    return "/".join(quote(segment, safe="") for segment in url.split("/"))


@pytest.fixture
def mmd_app(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    static_anim = project_root / "static" / "mmd" / "animation"
    static_anim.mkdir(parents=True)
    mmd_dir = tmp_path / "mmd"
    user_anim = mmd_dir / "animation"
    user_anim.mkdir(parents=True)
    config = SimpleNamespace(project_root=project_root, mmd_dir=mmd_dir, mmd_animation_dir=user_anim)
    monkeypatch.setattr(mmd_router, "get_config_manager", lambda: config)
    app = FastAPI()
    app.include_router(mmd_router.router)
    # Same mount layout as the main server (web_app.py).
    app.mount("/user_mmd/animation", CustomStaticFiles(directory=str(user_anim)))
    app.mount("/static", CustomStaticFiles(directory=str(project_root / "static")))
    return app, static_anim, user_anim


@pytest.fixture
def vrm_app(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    static_anim = project_root / "static" / "vrm" / "animation"
    static_anim.mkdir(parents=True)
    user_anim = tmp_path / "vrm" / "animation"
    user_anim.mkdir(parents=True)
    config = SimpleNamespace(project_root=project_root, vrm_animation_dir=user_anim,
                             ensure_vrm_directory=lambda: True)
    monkeypatch.setattr(vrm_router, "get_config_manager", lambda: config)
    app = FastAPI()
    app.include_router(vrm_router.router)
    app.mount("/user_vrm/animation", CustomStaticFiles(directory=str(user_anim)))
    app.mount("/static", CustomStaticFiles(directory=str(project_root / "static")))
    return app, static_anim, user_anim


@pytest.mark.parametrize("stem", SPECIAL_STEMS)
def test_mmd_list_keeps_raw_names_and_escaped_url_is_served(mmd_app, stem):
    app, static_anim, user_anim = mmd_app
    (static_anim / f"{stem}.vmd").write_bytes(b"static-vmd")
    (user_anim / f"{stem}.vmd").write_bytes(b"user-vmd")

    with TestClient(app) as client:
        animations = client.get("/api/model/mmd/animations").json()["animations"]
        urls = sorted(a["url"] for a in animations)
        assert urls == sorted([f"/static/mmd/animation/{stem}.vmd", f"/user_mmd/animation/{stem}.vmd"])
        for url, body in zip(urls, [b"static-vmd", b"user-vmd"]):
            response = client.get(_request_path(url))
            assert response.status_code == 200, url
            assert response.content == body


@pytest.mark.parametrize("stem", SPECIAL_STEMS)
def test_vrm_list_keeps_raw_names_and_escaped_url_is_served(vrm_app, stem):
    app, static_anim, user_anim = vrm_app
    (static_anim / f"{stem}.vrma.gz").write_bytes(b"static-vrma")
    (user_anim / f"{stem}.vrma").write_bytes(b"user-vrma")

    with TestClient(app) as client:
        animations = client.get("/api/model/vrm/animations").json()["animations"]
        urls = sorted(a["url"] for a in animations)
        assert urls == sorted([f"/static/vrm/animation/{stem}.vrma.gz", f"/user_vrm/animation/{stem}.vrma"])
        for url, body in zip(urls, [b"static-vrma", b"user-vrma"]):
            response = client.get(_request_path(url))
            assert response.status_code == 200, url
            assert response.content == body


def test_raw_hash_name_is_not_requestable_without_escaping(mmd_app):
    """Guard the guard: the unescaped URL really loses everything after '#'."""
    app, _, user_anim = mmd_app
    (user_anim / "Dance #1.vmd").write_bytes(b"vmd")
    with TestClient(app) as client:
        assert client.get("/user_mmd/animation/Dance #1.vmd").status_code == 404
        assert client.get(_request_path("/user_mmd/animation/Dance #1.vmd")).status_code == 200


@pytest.mark.parametrize("stem", SPECIAL_STEMS)
def test_mmd_delete_handles_url_syntax_in_names(mmd_app, stem):
    app, _, user_anim = mmd_app
    target = user_anim / f"{stem}.vmd"
    target.write_bytes(b"vmd")
    keep = user_anim / "Wave.vmd"
    keep.write_bytes(b"vmd")

    with TestClient(app) as client:
        listed = client.get("/api/model/mmd/animations/list").json()["animations"]
        url = next(a["url"] for a in listed if a["filename"] == target.name)
        assert url == f"/user_mmd/animation/{stem}.vmd"
        response = client.request("DELETE", "/api/model/mmd/animation", json={"url": url})

    assert response.status_code == 200, response.json()
    assert not target.exists()
    assert keep.exists()
