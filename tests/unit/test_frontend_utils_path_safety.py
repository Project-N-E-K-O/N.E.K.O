"""Path-safety tests for find_model_directory, delete_model, and Workshop item lookup (F0528, F0529)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers import live2d_router
from utils import frontend_utils


def _setup_live2d_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    live2d_dir = tmp_path / "user_live2d"
    live2d_dir.mkdir(parents=True)
    existing_model = live2d_dir / "haru"
    existing_model.mkdir()
    (existing_model / "haru.model3.json").write_text("{}", encoding="utf-8")

    fake_cm = SimpleNamespace(
        live2d_dir=live2d_dir,
        readable_live2d_dir=None,
        get_live2d_lookup_roots=lambda prefer_writable=True: [live2d_dir],
        get_workshop_path=lambda: None,
        ensure_live2d_directory=lambda: None,
    )
    monkeypatch.setattr("utils.config_manager.get_config_manager", lambda: fake_cm)
    monkeypatch.setattr(live2d_router, "get_config_manager", lambda: fake_cm)
    monkeypatch.setattr(frontend_utils, "_resolve_workshop_search_dir", lambda: None)
    monkeypatch.setattr(frontend_utils, "_resolve_workshop_search_dirs", lambda: [])
    return live2d_dir


@pytest.mark.parametrize("bad_name", [".", " . ", "..", "a/b", "a\\b", "C:haru", "haru\x00"])
def test_find_model_directory_rejects_dot_and_traversal_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_name: str):
    """F0528: find_model_directory must reject '.' and traversal/drive inputs instead of returning live2d_dir."""
    _setup_live2d_env(tmp_path, monkeypatch)
    assert frontend_utils.find_model_directory(bad_name) == (None, None)


def test_find_model_directory_rejects_symlink_pointing_to_root_or_outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """F0528: symlinks inside live2d_dir escaping outside must not resolve."""
    live2d_dir = _setup_live2d_env(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = live2d_dir / "escaped"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    assert frontend_utils.find_model_directory("escaped") == (None, None)


def test_delete_model_route_rejects_dot_and_preserves_live2d_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """F0528: DELETE /api/live2d/model/. must not fall back to deleting config_mgr.live2d_dir."""
    live2d_dir = _setup_live2d_env(tmp_path, monkeypatch)
    app = FastAPI()
    app.include_router(live2d_router.router)
    client = TestClient(app)

    # Note: Starlette normalizes raw "/." URL segments, so test URL-encoded "%2e" and direct handler call.
    resp_encoded = client.delete("/api/live2d/model/%2e")
    assert resp_encoded.status_code in (400, 404)
    assert live2d_dir.exists()
    assert (live2d_dir / "haru" / "haru.model3.json").exists()

    direct_resp = live2d_router.delete_model(".")
    assert getattr(direct_resp, "status_code", 200) in (400, 404)
    assert live2d_dir.exists()
    assert (live2d_dir / "haru" / "haru.model3.json").exists()


def test_delete_model_allows_embedded_double_dots_and_rejects_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """F0528: DELETE /api/live2d/model/<name> must delete legitimate names with '..' substrings while blocking traversal."""
    live2d_dir = _setup_live2d_env(tmp_path, monkeypatch)
    for valid_name in ("a..b", "Hiyori..v2"):
        model_dir = live2d_dir / valid_name
        model_dir.mkdir()
        (model_dir / f"{valid_name}.model3.json").write_text("{}", encoding="utf-8")

    app = FastAPI()
    app.include_router(live2d_router.router)
    client = TestClient(app)

    for valid_name in ("a..b", "Hiyori..v2"):
        resp = client.delete(f"/api/live2d/model/{valid_name}")
        assert resp.status_code == 200
        assert resp.json().get("success") is True
        assert not (live2d_dir / valid_name).exists()

    assert (live2d_dir / "haru" / "haru.model3.json").exists()

    for bad_name in (".", "..", " .. ", "../haru", "a/b", "a\\b", "a\x00b"):
        bad_resp = live2d_router.delete_model(bad_name)
        assert getattr(bad_resp, "status_code", 200) == 400
    assert (live2d_dir / "haru" / "haru.model3.json").exists()


@pytest.mark.parametrize("bad_id", ["", "   ", ".", "..", "../etc", "123/../../etc", "123\\evil", "C:\\Windows", "C:123", "123\x00"])
def test_find_workshop_item_by_id_rejects_invalid_and_traversal_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_id: str,
):
    """F0529: find_workshop_item_by_id and find_model_by_workshop_item_id must reject traversal/drive IDs."""
    workshop_dir = tmp_path / "workshop"
    workshop_dir.mkdir()
    monkeypatch.setattr(frontend_utils, "_resolve_workshop_search_dir", lambda: str(workshop_dir))
    monkeypatch.setattr(frontend_utils, "_resolve_workshop_search_dirs", lambda: [str(workshop_dir)])

    assert frontend_utils.find_workshop_item_by_id(bad_id) == (None, None)
    assert frontend_utils.find_model_by_workshop_item_id(bad_id) is None


def test_find_workshop_item_by_id_does_not_fallback_to_static_when_workshop_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """F0529: missing Workshop roots still reject invalid/symlink-escaping IDs while keeping valid plain ID /static fallback."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "static").mkdir()
    outside = tmp_path / "outside_static_target"
    outside.mkdir()
    monkeypatch.setattr(frontend_utils, "_resolve_workshop_search_dir", lambda: None)
    monkeypatch.setattr(frontend_utils, "_resolve_workshop_search_dirs", lambda: [])

    for bad_id in ("..", ".", "../etc", "C:\\Windows", "   ", " . "):
        assert frontend_utils.find_workshop_item_by_id(bad_id) == (None, None)
        assert frontend_utils.find_model_by_workshop_item_id(bad_id) is None

    escape_link = tmp_path / "static" / "99999"
    try:
        escape_link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pass
    else:
        assert frontend_utils.find_workshop_item_by_id("99999") == (None, None)
        assert frontend_utils.find_model_by_workshop_item_id("99999") is None

    assert frontend_utils.find_workshop_item_by_id("123456") == (str(Path("static") / "123456"), "/static")
    assert frontend_utils.find_model_by_workshop_item_id("123456") is None


def test_find_workshop_item_by_id_rejects_symlink_escape_and_accepts_valid_item(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """F0529: symlinked item folders escaping workshop_dir are rejected, while normal items resolve."""
    workshop_dir = tmp_path / "workshop"
    workshop_dir.mkdir()
    monkeypatch.setattr(frontend_utils, "_resolve_workshop_search_dir", lambda: str(workshop_dir))
    monkeypatch.setattr(frontend_utils, "_resolve_workshop_search_dirs", lambda: [str(workshop_dir)])

    valid_dir = workshop_dir / "12345"
    valid_dir.mkdir()
    (valid_dir / "12345.model3.json").write_text("{}", encoding="utf-8")
    assert frontend_utils.find_workshop_item_by_id("12345") == (str(valid_dir), "/workshop")
    assert frontend_utils.find_model_by_workshop_item_id("12345") == "/workshop/12345/12345.model3.json"

    outside = tmp_path / "outside_item"
    outside.mkdir()
    (outside / "evil.model3.json").write_text("{}", encoding="utf-8")
    escape_link = workshop_dir / "99999"
    try:
        escape_link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    assert frontend_utils.find_workshop_item_by_id("99999") == (None, None)
    assert frontend_utils.find_model_by_workshop_item_id("99999") is None
