# -*- coding: utf-8 -*-
"""Security tests for character cards, Live2D, VRM uploads, and Godot Variant bounds (F0015, F0021, F0022, F0211, F0223)."""

from __future__ import annotations

import os
import struct
import tracemalloc
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


pytestmark = pytest.mark.unit


def _csrf_token() -> str:
    from main_routers.system_router import _shared as system_shared

    return system_shared.AUTOSTART_CSRF_TOKEN


# ---------------------------------------------------------------------------
# F0015: main_routers/characters_router/cards.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("post", "/api/characters/catgirl/save-to-model-folder", {"json": {"charaData": {}}}),
        ("post", "/api/characters/character-card/save", {"json": {"charaData": {}}}),
        ("post", "/api/characters/import-card", {"files": {"zip_file": ("card.zip", b"zip")}}),
        ("put", "/api/characters/catgirl/Neko/card-meta", {"json": {"author": "x"}}),
        ("put", "/api/characters/catgirl/Neko/card-face", {"files": {"image": ("face.png", b"png", "image/png")}}),
        ("post", "/api/characters/catgirl/Neko/export-with-portrait", {"files": {"portrait": ("p.png", b"png", "image/png")}}),
    ],
)
def test_cards_mutations_reject_unauthenticated_and_cross_origin(method, path, kwargs):
    from main_routers import characters_router

    app = FastAPI()
    app.include_router(characters_router.router)
    with TestClient(app) as client:
        r1 = getattr(client, method)(path, **kwargs)
        assert r1.status_code == 403
        assert r1.json()["error_code"] == "csrf_validation_failed"

        r2 = getattr(client, method)(
            path,
            headers={"Origin": "https://evil.example", "X-CSRF-Token": _csrf_token()},
            **kwargs,
        )
        assert r2.status_code == 403
        assert r2.json()["error_code"] == "csrf_validation_failed"


def test_cards_json_mutations_reject_text_plain():
    from main_routers import characters_router

    app = FastAPI()
    app.include_router(characters_router.router)
    with TestClient(app) as client:
        for method, path in (
            ("post", "/api/characters/catgirl/save-to-model-folder"),
            ("post", "/api/characters/character-card/save"),
            ("put", "/api/characters/catgirl/Neko/card-meta"),
        ):
            resp = getattr(client, method)(
                path,
                content='{"author": "x"}',
                headers={
                    "Content-Type": "text/plain",
                    "Origin": "http://testserver",
                    "X-CSRF-Token": _csrf_token(),
                },
            )
            assert resp.status_code == 415


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/characters/catgirl/save-to-model-folder"),
        ("post", "/api/characters/character-card/save"),
        ("put", "/api/characters/catgirl/Neko/card-meta"),
    ],
)
def test_cards_json_mutations_reject_deeply_nested_json_with_400(method, path):
    from main_routers import characters_router

    deep_json = ('{"a":' * 1500) + "1" + ("}" * 1500)
    app = FastAPI()
    app.include_router(characters_router.router)
    with TestClient(app) as client:
        resp = getattr(client, method)(
            path,
            content=deep_json,
            headers={
                "Content-Type": "application/json",
                "Origin": "http://testserver",
                "X-CSRF-Token": _csrf_token(),
            },
        )
    assert resp.status_code == 400
    assert resp.json()["success"] is False


def test_cards_save_to_model_folder_rejects_dot_model_name_and_root_dir(tmp_path, monkeypatch):
    from main_routers import characters_router
    from main_routers.characters_router import cards

    user_dir = tmp_path / "user_live2d"
    user_dir.mkdir()
    monkeypatch.setattr(
        cards,
        "get_config_manager",
        lambda: SimpleNamespace(live2d_dir=user_dir, readable_live2d_dir=user_dir),
    )
    monkeypatch.setattr(cards, "find_model_directory", lambda name: (str(user_dir), "/user_live2d"))
    monkeypatch.setattr(cards, "is_user_imported_model", lambda *_a, **_kw: True)

    app = FastAPI()
    app.include_router(characters_router.router)
    with TestClient(
        app,
        headers={"Origin": "http://testserver", "X-CSRF-Token": _csrf_token()},
    ) as client:
        resp = client.post(
            "/api/characters/catgirl/save-to-model-folder",
            json={"charaData": {"name": "Neko"}, "modelName": ".", "fileName": "evil.chara.json"},
        )
    assert resp.status_code in (400, 403)
    assert resp.json()["success"] is False
    assert not (user_dir / "evil.chara.json").exists()


# ---------------------------------------------------------------------------
# F0021: main_routers/live2d_router.py
# ---------------------------------------------------------------------------

def test_live2d_uploads_require_csrf_and_origin():
    from main_routers import live2d_router

    app = FastAPI()
    app.include_router(live2d_router.router)
    with TestClient(app) as client:
        r1 = client.post(
            "/api/live2d/upload_model",
            files=[("files", ("ModelA/ModelA.model3.json", b"{}", "application/json"))],
        )
        assert r1.status_code == 403
        assert r1.json()["error_code"] == "csrf_validation_failed"

        r2 = client.post(
            "/api/live2d/upload_file/ModelA",
            files={"file": ("a.motion3.json", b"{}", "application/json")},
            headers={"Origin": "https://evil.example", "X-CSRF-Token": _csrf_token()},
        )
        assert r2.status_code == 403
        assert r2.json()["error_code"] == "csrf_validation_failed"


@pytest.mark.parametrize(
    "malicious_filename",
    [
        "../escape.txt",
        "a/../../escape.txt",
        "a/..",
        "a/.",
        "/etc/passwd",
        "\\Windows\\evil.txt",
        "foo\x00bar/a.json",
    ],
)
def test_live2d_upload_model_rejects_drive_letters_and_traversal(tmp_path, monkeypatch, malicious_filename):
    from main_routers import live2d_router

    user_dir = tmp_path / "user_live2d"
    user_dir.mkdir()
    monkeypatch.setattr(
        live2d_router,
        "get_config_manager",
        lambda: SimpleNamespace(ensure_live2d_directory=lambda: True, live2d_dir=user_dir),
    )

    app = FastAPI()
    app.include_router(live2d_router.router)
    with TestClient(
        app,
        headers={"Origin": "http://testserver", "X-CSRF-Token": _csrf_token()},
    ) as client:
        resp = client.post(
            "/api/live2d/upload_model",
            files=[("files", (malicious_filename, b"evil", "application/octet-stream"))],
        )
    assert resp.status_code == 400
    assert resp.json()["success"] is False
    assert list(user_dir.iterdir()) == []


@pytest.mark.parametrize(
    "windows_only_filename",
    [
        "C:/Users/Public/evil.py",
        "C:\\Users\\Public\\evil.py",
        "D:evil.py",
        "Model/bad?.json",
        "Model/bad*.json",
    ],
)
def test_live2d_upload_model_rejects_windows_drive_and_reserved_chars_on_windows(
    tmp_path, monkeypatch, windows_only_filename
):
    from main_routers import live2d_router

    user_dir = tmp_path / "user_live2d"
    user_dir.mkdir()
    monkeypatch.setattr(live2d_router, "_is_windows", lambda: True)
    monkeypatch.setattr(
        live2d_router,
        "get_config_manager",
        lambda: SimpleNamespace(ensure_live2d_directory=lambda: True, live2d_dir=user_dir),
    )

    app = FastAPI()
    app.include_router(live2d_router.router)
    with TestClient(
        app,
        headers={"Origin": "http://testserver", "X-CSRF-Token": _csrf_token()},
    ) as client:
        resp = client.post(
            "/api/live2d/upload_model",
            files=[("files", (windows_only_filename, b"evil", "application/octet-stream"))],
        )
    assert resp.status_code == 400
    assert resp.json()["success"] is False
    assert list(user_dir.iterdir()) == []


def test_live2d_upload_model_succeeds_for_valid_model_directory(tmp_path, monkeypatch):
    from main_routers import live2d_router

    user_dir = tmp_path / "user_live2d"
    user_dir.mkdir()
    monkeypatch.setattr(
        live2d_router,
        "get_config_manager",
        lambda: SimpleNamespace(ensure_live2d_directory=lambda: True, live2d_dir=user_dir),
    )

    app = FastAPI()
    app.include_router(live2d_router.router)
    with TestClient(
        app,
        headers={"Origin": "http://testserver", "X-CSRF-Token": _csrf_token()},
    ) as client:
        resp = client.post(
            "/api/live2d/upload_model",
            files=[("files", ("MyModel/MyModel.model3.json", b'{"Version": 3}', "application/json"))],
        )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert (user_dir / "MyModel" / "MyModel.model3.json").is_file()


def test_live2d_upload_file_rejects_deeply_nested_json_with_400(tmp_path, monkeypatch):
    from main_routers import live2d_router

    model_dir = tmp_path / "user_live2d" / "ModelA"
    model_dir.mkdir(parents=True)
    monkeypatch.setattr(
        live2d_router,
        "get_config_manager",
        lambda: SimpleNamespace(live2d_dir=tmp_path / "user_live2d", readable_live2d_dir=None),
    )
    monkeypatch.setattr(live2d_router, "find_model_directory", lambda name: (str(model_dir), "/user_live2d/ModelA"))

    deep_json = (('{"a":' * 1500) + "1" + ("}" * 1500)).encode("utf-8")
    app = FastAPI()
    app.include_router(live2d_router.router)
    with TestClient(
        app,
        headers={"Origin": "http://testserver", "X-CSRF-Token": _csrf_token()},
    ) as client:
        resp = client.post(
            "/api/live2d/upload_file/ModelA",
            files={"file": ("a.motion3.json", deep_json, "application/json")},
        )
    assert resp.status_code == 400
    assert resp.json()["success"] is False


@pytest.mark.parametrize("encoded_name", ["%2e", "%2e%2e", "%20.%20", "..."])
def test_live2d_upload_file_rejects_dot_and_invalid_model_names(tmp_path, monkeypatch, encoded_name):
    from main_routers import live2d_router

    user_dir = tmp_path / "user_live2d"
    legit_dir = user_dir / "LegitModel"
    legit_dir.mkdir(parents=True)
    monkeypatch.setattr(
        live2d_router,
        "get_config_manager",
        lambda: SimpleNamespace(
            ensure_live2d_directory=lambda: True,
            live2d_dir=user_dir,
            readable_live2d_dir=user_dir,
        ),
    )
    # Simulate vulnerable find_model_directory(".") returning user_live2d root itself.
    monkeypatch.setattr(
        live2d_router,
        "find_model_directory",
        lambda name: (str(user_dir / name), f"/user_live2d/{name}"),
    )

    app = FastAPI()
    app.include_router(live2d_router.router)
    with TestClient(
        app,
        headers={"Origin": "http://testserver", "X-CSRF-Token": _csrf_token()},
    ) as client:
        resp = client.post(
            f"/api/live2d/upload_file/{encoded_name}",
            files={"file": ("a.motion3.json", b"{}", "application/json")},
        )
    assert resp.status_code in (400, 404)
    assert resp.json()["success"] is False
    assert (user_dir / "motions").exists() is False


def test_live2d_upload_file_rejects_symlinked_subdir_and_target_file(tmp_path, monkeypatch):
    from main_routers import live2d_router

    user_dir = tmp_path / "user_live2d"
    model_dir = user_dir / "LegitModel"
    outside_dir = tmp_path / "outside"
    model_dir.mkdir(parents=True)
    outside_dir.mkdir(parents=True)

    try:
        # Bypass #2a: symlinked motions subdirectory escaping model_dir
        (model_dir / "motions").symlink_to(outside_dir, target_is_directory=True)
        # Bypass #2b: symlinked file inside expressions subdirectory escaping model_dir
        (model_dir / "expressions").mkdir()
        (model_dir / "expressions" / "trap.exp3.json").symlink_to(outside_dir / "trap.exp3.json")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks are not supported in this test environment: {exc}")

    monkeypatch.setattr(
        live2d_router,
        "get_config_manager",
        lambda: SimpleNamespace(
            ensure_live2d_directory=lambda: True,
            live2d_dir=user_dir,
            readable_live2d_dir=None,
        ),
    )
    monkeypatch.setattr(
        live2d_router,
        "find_model_directory",
        lambda name: (str(model_dir), "/user_live2d/LegitModel") if name == "LegitModel" else (None, None),
    )

    app = FastAPI()
    app.include_router(live2d_router.router)
    with TestClient(
        app,
        headers={"Origin": "http://testserver", "X-CSRF-Token": _csrf_token()},
    ) as client:
        r_sym_dir = client.post(
            "/api/live2d/upload_file/LegitModel",
            params={"file_type": "motion"},
            files={"file": ("escaped.motion3.json", b"{}", "application/json")},
        )
        assert r_sym_dir.status_code == 400
        assert not (outside_dir / "escaped.motion3.json").exists()

        r_sym_file = client.post(
            "/api/live2d/upload_file/LegitModel",
            params={"file_type": "expression"},
            files={"file": ("trap.exp3.json", b"{}", "application/json")},
        )
        assert r_sym_file.status_code == 400
        assert not (outside_dir / "trap.exp3.json").exists()

        # Positive case: valid upload inside LegitModel/expressions succeeds.
        r_ok = client.post(
            "/api/live2d/upload_file/LegitModel",
            params={"file_type": "expression"},
            files={"file": ("ok.exp3.json", b"{}", "application/json")},
        )
        assert r_ok.status_code == 200
        assert (model_dir / "expressions" / "ok.exp3.json").is_file()


def test_live2d_delete_and_save_parameters_reject_dot_model_name(tmp_path, monkeypatch):
    from main_routers import live2d_router

    user_dir = tmp_path / "user_live2d"
    legit_dir = user_dir / "LegitModel"
    legit_dir.mkdir(parents=True)
    (legit_dir / "LegitModel.model3.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        live2d_router,
        "get_config_manager",
        lambda: SimpleNamespace(
            ensure_live2d_directory=lambda: True,
            live2d_dir=user_dir,
            readable_live2d_dir=None,
        ),
    )
    monkeypatch.setattr(
        live2d_router,
        "find_model_directory",
        lambda name: (str(user_dir / name), "/user_live2d"),
    )

    app = FastAPI()
    app.include_router(live2d_router.router)
    with TestClient(
        app,
        headers={"Origin": "http://testserver", "X-CSRF-Token": _csrf_token()},
    ) as client:
        r_del = client.delete("/api/live2d/model/%2e")
        assert 400 <= r_del.status_code < 500
        assert legit_dir.is_dir()

        r_params = client.post(
            "/api/live2d/save_model_parameters/%2e",
            json={"parameters": {"ParamAngleX": 1}},
        )
        assert 400 <= r_params.status_code < 500
        assert not (user_dir / "parameters.json").exists()


# ---------------------------------------------------------------------------
# F0022 + F0211: main_routers/pngtuber_importers/godot_variant.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("variant_type", [30, 31, 32, 33, 35, 36, 24, 27, 28])
def test_godot_variant_rejects_oversized_counts_without_oom(variant_type):
    from main_routers.pngtuber_importers.godot_variant import GodotVariantReader

    if variant_type == 24:
        # Type 24 header + empty class name (len=0) + huge property count
        payload = struct.pack("<III", variant_type, 0, 0x7FFFFFFF)
    else:
        payload = struct.pack("<II", variant_type, 0xFFFFFFFF if variant_type >= 30 else 0x7FFFFFFF)

    was_tracing = tracemalloc.is_tracing()
    if not was_tracing:
        tracemalloc.start()
    try:
        with pytest.raises(ValueError):
            GodotVariantReader(payload).read_variant()
        if not was_tracing:
            _current, peak = tracemalloc.get_traced_memory()
    finally:
        if not was_tracing:
            tracemalloc.stop()
    if not was_tracing:
        assert peak < 1_000_000


def test_godot_variant_rejects_oversized_string_and_bytes_lengths():
    from main_routers.pngtuber_importers.godot_variant import GodotVariantReader

    for vtype in (4, 29):
        payload = struct.pack("<II", vtype, 0xFFFFFFFF)
        with pytest.raises(ValueError):
            GodotVariantReader(payload).read_variant()


def test_godot_variant_valid_packed_arrays_still_parse():
    from main_routers.pngtuber_importers.godot_variant import GodotVariantReader

    payload_i32 = struct.pack("<IIiii", 30, 3, 1, -2, 3)
    assert GodotVariantReader(payload_i32).read_variant() == [1, -2, 3]

    payload_empty = struct.pack("<II", 32, 0)
    assert GodotVariantReader(payload_empty).read_variant() == []


def test_godot_variant_accepts_large_bytes_and_strings_when_backed_by_data():
    from main_routers.pngtuber_importers.godot_variant import GodotVariantReader

    large_len = 10_000_004
    bytes_payload = struct.pack("<II", 29, large_len) + (b"A" * large_len)
    parsed_bytes = GodotVariantReader(bytes_payload).read_variant()
    assert isinstance(parsed_bytes, bytes) and len(parsed_bytes) == large_len

    str_payload = struct.pack("<II", 4, large_len) + (b"x" * large_len)
    parsed_str = GodotVariantReader(str_payload).read_variant()
    assert isinstance(parsed_str, str) and len(parsed_str) == large_len

    with pytest.raises(ValueError):
        GodotVariantReader(struct.pack("<II", 29, large_len) + b"short").read_variant()
    with pytest.raises(ValueError):
        GodotVariantReader(struct.pack("<II", 4, large_len) + b"short").read_variant()


# ---------------------------------------------------------------------------
# F0223: main_routers/vrm_router.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path,filename",
    [
        ("/api/model/vrm/upload", "avatar.vrm"),
        ("/api/model/vrm/upload_animation", "dance.vrma"),
    ],
)
def test_vrm_uploads_reject_unauthenticated_and_cross_origin(tmp_path, monkeypatch, path, filename):
    from main_routers import vrm_router

    user_vrm = tmp_path / "user_vrm"
    user_vrm.mkdir()
    monkeypatch.setattr(
        vrm_router,
        "get_config_manager",
        lambda: SimpleNamespace(ensure_vrm_directory=lambda: True, vrm_dir=user_vrm),
    )

    app = FastAPI()
    app.include_router(vrm_router.router)
    with TestClient(app) as client:
        r_no_auth = client.post(path, files={"file": (filename, b"glTF")})
        assert r_no_auth.status_code == 403
        assert r_no_auth.json()["error_code"] == "csrf_validation_failed"

        r_evil = client.post(
            path,
            files={"file": (filename, b"glTF")},
            headers={"Origin": "https://evil.example", "X-CSRF-Token": _csrf_token()},
        )
        assert r_evil.status_code == 403
        assert r_evil.json()["error_code"] == "csrf_validation_failed"

    assert list(user_vrm.iterdir()) == []


def test_live2d_model_name_and_upload_relpath_respect_platform_chars(monkeypatch):
    from main_routers import live2d_router
    from utils import frontend_utils

    monkeypatch.setattr(frontend_utils, "_is_windows", lambda: False)
    monkeypatch.setattr(live2d_router, "_is_windows", lambda: False)
    assert frontend_utils.is_valid_live2d_model_name("Blue:Night", for_upload=True) is True
    assert live2d_router._is_valid_live2d_model_name("Blue:Night", for_upload=True) is True
    assert live2d_router._is_valid_live2d_model_name("Blue:Night") is True
    assert live2d_router._is_valid_live2d_model_name("../Blue:Night") is False
    assert live2d_router._is_valid_live2d_model_name("Blue/Night") is False
    assert live2d_router._is_valid_live2d_model_name("Blue\\Night") is False
    assert str(live2d_router._sanitize_live2d_upload_relpath("Model:v2/tex?.png")) == "Model:v2/tex?.png"
    assert str(live2d_router._sanitize_live2d_upload_relpath("Model:v2/a<b|c>.json")) == "Model:v2/a<b|c>.json"

    monkeypatch.setattr(frontend_utils, "_is_windows", lambda: True)
    monkeypatch.setattr(live2d_router, "_is_windows", lambda: True)
    assert frontend_utils.is_valid_live2d_model_name("Blue:Night", for_upload=True) is False
    assert live2d_router._is_valid_live2d_model_name("Blue:Night", for_upload=True) is False
    assert live2d_router._is_valid_live2d_model_name("Blue:Night") is False
    assert live2d_router._sanitize_live2d_upload_relpath("Model:v2/tex?.png") is None
    assert live2d_router._sanitize_live2d_upload_relpath("Model/a<b|c>.json") is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX filename characters")
def test_live2d_upload_model_and_file_allow_colon_and_question_on_posix(tmp_path, monkeypatch):
    from main_routers import live2d_router

    user_dir = tmp_path / "user_live2d"
    user_dir.mkdir()
    monkeypatch.setattr(
        live2d_router,
        "get_config_manager",
        lambda: SimpleNamespace(
            ensure_live2d_directory=lambda: True,
            live2d_dir=user_dir,
            readable_live2d_dir=None,
        ),
    )
    monkeypatch.setattr(
        live2d_router,
        "find_model_directory",
        lambda name: (str(user_dir / name), f"/user_live2d/{name}") if name == "Model:v2" else (None, None),
    )

    app = FastAPI()
    app.include_router(live2d_router.router)
    with TestClient(
        app,
        headers={"Origin": "http://testserver", "X-CSRF-Token": _csrf_token()},
    ) as client:
        r_model = client.post(
            "/api/live2d/upload_model",
            files=[
                ("files", ("Model:v2/Model:v2.model3.json", b'{"Version": 3}', "application/json")),
                ("files", ("Model:v2/tex?.png", b"png-bytes", "image/png")),
            ],
        )
        assert r_model.status_code == 200
        assert r_model.json()["success"] is True
        assert (user_dir / "Model:v2" / "Model:v2.model3.json").is_file()
        assert (user_dir / "Model:v2" / "tex?.png").is_file()

        r_file = client.post(
            "/api/live2d/upload_file/Model:v2",
            params={"file_type": "motion"},
            files={"file": ("idle:01.motion3.json", b"{}", "application/json")},
        )
        assert r_file.status_code == 200
        assert r_file.json()["success"] is True
        assert (user_dir / "Model:v2" / "motions" / "idle:01.motion3.json").is_file()


@pytest.mark.asyncio
async def test_write_staged_upload_bytes_waits_for_worker_on_cancellation(tmp_path):
    import asyncio
    import threading
    from pathlib import Path
    from main_routers import live2d_router

    writer_started = threading.Event()
    allow_finish = threading.Event()
    dest = tmp_path / "staged.bin"

    class _SlowTarget:
        def __init__(self, real_path: Path):
            self._real_path = real_path

        def write_bytes(self, data: bytes) -> int:
            writer_started.set()
            assert allow_finish.wait(timeout=2.0)
            return self._real_path.write_bytes(data)

    task = asyncio.create_task(live2d_router._write_staged_upload_bytes(_SlowTarget(dest), b"payload"))
    while not writer_started.is_set():
        await asyncio.sleep(0.01)

    task.cancel()
    await asyncio.sleep(0.05)
    assert not task.done(), "cancellation returned before worker write completed"

    allow_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert dest.read_bytes() == b"payload"


def test_cards_and_publish_live2d_name_validation_matches_posix_and_windows(monkeypatch):
    from main_routers import live2d_router
    from main_routers.characters_router import cards
    from main_routers.workshop_router import publish
    from utils import frontend_utils

    monkeypatch.setattr(frontend_utils, "_is_windows", lambda: False)
    assert live2d_router._is_valid_live2d_model_name("Blue:Night", for_upload=True) is True
    assert cards._is_valid_live2d_model_name("Blue:Night") is True
    assert publish._is_valid_live2d_model_name("Blue:Night") is True
    assert cards._is_valid_live2d_model_name("../Blue:Night") is False
    assert publish._is_valid_live2d_model_name("Blue/Night") is False

    monkeypatch.setattr(frontend_utils, "_is_windows", lambda: True)
    assert live2d_router._is_valid_live2d_model_name("Blue:Night", for_upload=True) is False
    assert cards._is_valid_live2d_model_name("Blue:Night") is False
    assert publish._is_valid_live2d_model_name("Blue:Night") is False


def test_find_model_directory_rejects_library_root_alias_but_allows_subdir(tmp_path, monkeypatch):
    from utils import config_manager as cm_mod
    from utils import frontend_utils

    user_docs = tmp_path / "docs_live2d"
    user_docs.mkdir()
    lib2 = tmp_path / "steam_lib2" / "workshop" / "content" / "123"
    lib2.mkdir(parents=True)
    real_item = lib2 / "999"
    real_item.mkdir()
    (real_item / "LegitModel.model3.json").write_text("{}", encoding="utf-8")

    alias_to_root = lib2 / "Alias"
    try:
        alias_to_root.symlink_to(lib2, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks are not supported in this test environment: {exc}")

    fake_cm = SimpleNamespace(
        live2d_dir=user_docs,
        readable_live2d_dir=user_docs,
        get_live2d_lookup_roots=lambda prefer_writable=True: [user_docs],
        get_workshop_path=lambda: "",
    )
    monkeypatch.setattr(cm_mod, "get_config_manager", lambda: fake_cm)
    monkeypatch.setattr(frontend_utils, "_resolve_workshop_search_dirs", lambda: [str(lib2)])

    assert frontend_utils.find_model_directory("Alias") == (None, None)
    assert frontend_utils.find_model_directory("LegitModel") == (str(real_item), "/workshop")


@pytest.mark.asyncio
async def test_run_cancellation_safe_io_waits_for_worker_on_cancellation():
    import asyncio
    import threading
    from main_routers import live2d_router

    started = threading.Event()
    release = threading.Event()
    finished = False

    def _slow_io(marker):
        nonlocal finished
        started.set()
        assert release.wait(timeout=2.0)
        finished = marker
        return marker

    task = asyncio.create_task(live2d_router._run_cancellation_safe_io(_slow_io, "done"))
    while not started.is_set():
        await asyncio.sleep(0.01)

    task.cancel()
    await asyncio.sleep(0.05)
    assert not task.done(), "cancellation returned before worker I/O finished"

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished == "done"
