from __future__ import annotations

import io
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import main_routers.avatar_tool_router as avatar_tool_router
from main_routers.cookies_login_router import verify_local_access


class _ConfigManager:
    def __init__(self, root: Path):
        self.avatar_tools_dir = root

    def ensure_avatar_tools_directory(self):
        self.avatar_tools_dir.mkdir(parents=True, exist_ok=True)
        return True


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (8, 8), (200, 80, 30, 255)).save(output, format="PNG")
    return output.getvalue()


def _mp3() -> bytes:
    return (
        Path(__file__).resolve().parents[2]
        / "static"
        / "sounds"
        / "avatar-tools"
        / "lollipop"
        / "bite.mp3"
    ).read_bytes()


def _client(tmp_path, monkeypatch, *, allow_mutation: bool):
    manager = _ConfigManager(tmp_path / "avatar_tools")
    monkeypatch.setattr(avatar_tool_router, "get_config_manager", lambda: manager)
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    if allow_mutation:
        monkeypatch.setattr(avatar_tool_router, "_validate_local_mutation_request", lambda _request: None)
    app = FastAPI()
    app.dependency_overrides[verify_local_access] = lambda: None
    app.include_router(avatar_tool_router.router)
    return TestClient(app), manager


def test_post_then_get_returns_authoritative_item_without_meaning(tmp_path, monkeypatch):
    client, manager = _client(tmp_path, monkeypatch, allow_mutation=True)
    response = client.post(
        "/api/avatar-tools",
        files=[
            ("name", (None, "Feather")),
            ("change_mode", (None, "click-advance")),
            ("change_meanings", (None, "a gentle feather touch")),
            ("change_meanings", (None, "a playful feather touch")),
            ("default_image", ("default.png", _png(), "image/png")),
            ("change_images", ("first.png", _png(), "image/png")),
            ("change_images", ("second.png", _png(), "image/png")),
            ("normal_sound", ("interaction.mp3", _mp3(), "audio/mpeg")),
        ],
    )
    assert response.status_code == 201
    item = response.json()["item"]
    listing = client.get("/api/avatar-tools")
    assert listing.status_code == 200
    assert listing.json()["items"] == [item]
    assert "a gentle feather touch" not in listing.text
    assert item["changeMode"] == "click-advance"
    assert len(item["changeUrls"]) == 2
    assert "/normal.mp3?v=" in item["normalSoundUrl"]
    assert listing.json()["limits"]["maxChangeImages"] == 16
    assert listing.json()["limits"]["maxAudioBytes"] == 5 * 1024 * 1024
    assert listing.json()["limits"]["maxAudioDurationMs"] == 10_000
    assert (manager.avatar_tools_dir / item["id"] / "record.json").is_file()
    assert (manager.avatar_tools_dir / item["id"] / "normal.mp3").is_file()


def test_post_rejects_request_without_mutation_security(tmp_path, monkeypatch):
    client, _manager = _client(tmp_path, monkeypatch, allow_mutation=False)
    response = client.post(
        "/api/avatar-tools",
        data={"name": "Feather", "change_mode": "press-swap", "change_meanings": "gentle"},
        files=[
            ("default_image", ("default.png", _png(), "image/png")),
            ("change_images", ("change.png", _png(), "image/png")),
        ],
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "csrf_validation_failed"
