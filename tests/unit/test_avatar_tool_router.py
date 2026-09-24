from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

import main_routers.avatar_tool_router as avatar_tool_router
from main_routers.cookies_login_router import verify_local_access
from utils.cloudsave_runtime import MaintenanceModeError


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


def _v3_manifest(tool_id: str, *, source: dict | None = None, name="Flow tool") -> dict:
    return {
        "recordVersion": 3,
        "id": tool_id,
        "name": name,
        "images": [{
            "id": "img-1",
            "name": "",
            "source": source or {"kind": "upload", "index": 0},
            "meaning": "",
        }],
        "initialImageId": "img-1",
        "imageInteractions": {
            "initialImagePosition": {"x": 10, "y": 20},
            "initialLinks": [{
                "to": "ix-click",
                "sourceSide": "right",
                "targetSide": "left",
            }],
            "items": [{
                "id": "ix-click",
                "name": "",
                "trigger": {"kind": "mouse-click"},
                "actions": {"press": {"kind": "keep"}, "release": {"kind": "keep"}},
                "editorPosition": {"x": 300, "y": 20},
            }],
            "links": [{
                "from": "ix-click",
                "to": "ix-click",
                "sourceSide": "right",
                "targetSide": "right",
            }],
        },
        "interaction": {},
    }


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


def test_shared_local_access_accepts_ipv4_mapped_loopback():
    request = SimpleNamespace(client=SimpleNamespace(host="::ffff:127.0.0.1"))

    verify_local_access(request)


def test_shared_local_access_rejects_ipv4_mapped_public_address():
    request = SimpleNamespace(client=SimpleNamespace(host="::ffff:8.8.8.8"))

    with pytest.raises(HTTPException) as raised:
        verify_local_access(request)

    assert raised.value.status_code == 403


def test_post_then_get_returns_authoritative_item_without_meaning(tmp_path, monkeypatch):
    client, manager = _client(tmp_path, monkeypatch, allow_mutation=True)
    response = client.post(
        "/api/avatar-tools",
        files=[
            ("tool_id", (None, "local-12345678-1234-4123-8123-123456789abc")),
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


def test_v3_post_get_put_and_list_complete_the_editor_persistence_chain(tmp_path, monkeypatch):
    client, manager = _client(tmp_path, monkeypatch, allow_mutation=True)
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    create_manifest = _v3_manifest(tool_id)
    create_manifest["interaction"] = {
        "normalSound": {"kind": "upload", "index": 1},
        "special": {
            "probability": 0.2,
            "image": {"kind": "upload", "index": 2},
            "meaning": "sparkles appear",
            "sound": {"kind": "upload", "index": 3},
        },
    }
    created_response = client.post(
        "/api/avatar-tools",
        files=[
            ("record_version", (None, "3")),
            ("manifest", (None, json.dumps(create_manifest))),
            ("uploads", ("state.png", _png(), "image/png")),
            ("uploads", ("normal.mp3", _mp3(), "audio/mpeg")),
            ("uploads", ("special.png", _png(), "image/png")),
            ("uploads", ("special.mp3", _mp3(), "audio/mpeg")),
        ],
    )

    assert created_response.status_code == 201
    created = created_response.json()["item"]
    assert created["recordVersion"] == 3
    assert created["revision"].startswith("3-")
    assert "imageInteractions" not in created
    assert created["runtime"]["initialImageId"] == "img-1"
    assert created["runtime"]["initialInteractionIds"] == ["ix-click"]
    assert created["runtime"]["interactions"][0]["trigger"] == {"kind": "mouse-click"}
    assert created["runtime"]["normalSoundUrl"].startswith(
        f"/user_avatar_tools/{tool_id}/normal.mp3?v="
    )
    assert created["runtime"]["special"]["hasMeaning"] is True
    detail_response = client.get(f"/api/avatar-tools/{tool_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()["detail"]
    assert detail["imageInteractions"] == _v3_manifest(tool_id)["imageInteractions"]
    assert detail["normalSound"]["resource"] == "normal.mp3"
    assert detail["special"]["image"]["resource"] == "special.png"
    assert detail["special"]["sound"]["resource"] == "special.mp3"
    assert detail_response.json()["limits"]["maxLinks"] == 32

    updated_manifest = _v3_manifest(
        tool_id,
        source={"kind": "resource", "name": "image-000.png"},
        name="Renamed flow",
    )
    updated_manifest["interaction"] = {
        "normalSound": {"kind": "resource", "name": "normal.mp3"},
        "special": {
            "probability": 0.3,
            "image": {"kind": "resource", "name": "special.png"},
            "meaning": "sparkles return",
            "sound": {"kind": "resource", "name": "special.mp3"},
        },
    }
    updated_response = client.put(
        f"/api/avatar-tools/{tool_id}",
        files=[
            ("base_revision", (None, created["revision"])),
            ("record_version", (None, "3")),
            ("manifest", (None, json.dumps(updated_manifest))),
        ],
    )

    assert updated_response.status_code == 200
    updated = updated_response.json()["item"]
    assert updated["name"] == "Renamed flow"
    assert client.get("/api/avatar-tools").json()["items"] == [updated]
    assert set(path.name for path in (manager.avatar_tools_dir / tool_id).iterdir()) == {
        "record.json", "image-000.png", "normal.mp3", "special.png", "special.mp3",
    }


def test_v3_transport_rejects_unrepresentable_editor_coordinates_without_500(tmp_path, monkeypatch):
    client, manager = _client(tmp_path, monkeypatch, allow_mutation=True)
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    manifest = _v3_manifest(tool_id)
    manifest["imageInteractions"]["items"][0]["editorPosition"]["x"] = 10 ** 400

    response = client.post(
        "/api/avatar-tools",
        files=[
            ("record_version", (None, "3")),
            ("manifest", (None, json.dumps(manifest))),
            ("uploads", ("state.png", _png(), "image/png")),
        ],
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "manifest_invalid"
    assert not manager.avatar_tools_dir.exists() or not list(manager.avatar_tools_dir.iterdir())


def test_v3_transport_rejects_mixed_v2_fields_without_publishing(tmp_path, monkeypatch):
    client, manager = _client(tmp_path, monkeypatch, allow_mutation=True)
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    response = client.post(
        "/api/avatar-tools",
        files=[
            ("record_version", (None, "3")),
            ("manifest", (None, json.dumps(_v3_manifest(tool_id)))),
            ("uploads", ("state.png", _png(), "image/png")),
            ("name", (None, "must not be mixed")),
        ],
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "request_fields_invalid"
    assert not manager.avatar_tools_dir.exists() or not list(manager.avatar_tools_dir.iterdir())


def test_v3_transport_rejects_duplicate_single_value_fields(tmp_path, monkeypatch):
    client, manager = _client(tmp_path, monkeypatch, allow_mutation=True)
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    response = client.post(
        "/api/avatar-tools",
        files=[
            ("record_version", (None, "3")),
            ("manifest", (None, json.dumps(_v3_manifest(tool_id)))),
            ("manifest", (None, json.dumps(_v3_manifest(tool_id, name="Second")))),
            ("uploads", ("state.png", _png(), "image/png")),
        ],
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "request_fields_invalid"
    assert not manager.avatar_tools_dir.exists() or not list(manager.avatar_tools_dir.iterdir())


def test_read_endpoints_report_a_deferred_recovery_write_fence(tmp_path, monkeypatch):
    client, _manager = _client(tmp_path, monkeypatch, allow_mutation=False)

    class FencedStore:
        limits = {"maxChangeImages": 16}

        @staticmethod
        def _raise_fence():
            raise MaintenanceModeError(
                "maintenance_readonly",
                operation="recover",
                target="avatar_tools",
            )

        def list_items(self):
            self._raise_fence()

        def get_detail(self, _tool_id):
            self._raise_fence()

    monkeypatch.setattr(avatar_tool_router, "get_avatar_tool_store", lambda _manager: FencedStore())

    for path in (
        "/api/avatar-tools",
        "/api/avatar-tools/local-12345678-1234-4123-8123-123456789abc",
    ):
        response = client.get(path)
        assert response.status_code == 409
        assert response.json()["code"] == "CLOUDSAVE_WRITE_FENCE_ACTIVE"
        assert response.json()["operation"] == "recover"


def test_post_retry_with_the_same_tool_id_returns_the_original_creation(tmp_path, monkeypatch):
    client, _manager = _client(tmp_path, monkeypatch, allow_mutation=True)
    tool_id = "local-12345678-1234-4123-8123-123456789abc"

    def create():
        return client.post(
            "/api/avatar-tools",
            files=[
                ("tool_id", (None, tool_id)),
                ("name", (None, "Feather")),
                ("change_mode", (None, "press-swap")),
                ("change_meanings", (None, "a gentle touch")),
                ("default_image", ("default.png", _png(), "image/png")),
                ("change_images", ("change.png", _png(), "image/png")),
            ],
        )

    first = create()
    second = create()

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["item"] == second.json()["item"]
    assert [item["id"] for item in client.get("/api/avatar-tools").json()["items"]] == [tool_id]


def test_post_rejects_a_non_local_client_tool_id_before_reading_uploads(tmp_path, monkeypatch):
    client, manager = _client(tmp_path, monkeypatch, allow_mutation=True)
    upload_reads = []

    async def record_upload_read(*args, **kwargs):
        upload_reads.append((args, kwargs))
        raise AssertionError("invalid tool IDs must be rejected before upload reads")

    monkeypatch.setattr(avatar_tool_router, "_read_upload_limited", record_upload_read)
    response = client.post(
        "/api/avatar-tools",
        files=[
            ("tool_id", (None, "local-not-a-uuid")),
            ("name", (None, "Feather")),
            ("change_mode", (None, "press-swap")),
            ("change_meanings", (None, "a gentle touch")),
            ("default_image", ("default.png", _png(), "image/png")),
            ("change_images", ("change.png", _png(), "image/png")),
        ],
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_tool_id"
    assert upload_reads == []
    assert not manager.avatar_tools_dir.exists()


def test_post_accepts_complete_special_block_and_keeps_meaning_private(tmp_path, monkeypatch):
    client, manager = _client(tmp_path, monkeypatch, allow_mutation=True)
    response = client.post(
        "/api/avatar-tools",
        files=[
            ("tool_id", (None, "local-12345678-1234-4123-8123-123456789abc")),
            ("name", (None, "Surprise feather")),
            ("change_mode", (None, "press-swap")),
            ("change_meanings", (None, "a gentle touch")),
            ("default_image", ("default.png", _png(), "image/png")),
            ("change_images", ("change.png", _png(), "image/png")),
            ("special_probability", (None, "0.1")),
            ("special_image", ("surprise.png", _png(), "image/png")),
            ("special_meaning", (None, "feathers scatter everywhere")),
            ("special_sound", ("surprise.mp3", _mp3(), "audio/mpeg")),
        ],
    )

    assert response.status_code == 201
    item = response.json()["item"]
    assert item["special"]["probability"] == 0.1
    assert "/special.png?v=" in item["special"]["imageUrl"]
    assert "/special.mp3?v=" in item["special"]["soundUrl"]
    assert "feathers scatter everywhere" not in response.text
    directory = manager.avatar_tools_dir / item["id"]
    assert (directory / "special.png").is_file()
    assert (directory / "special.mp3").is_file()


def test_post_rejects_partial_special_block_without_publishing(tmp_path, monkeypatch):
    client, manager = _client(tmp_path, monkeypatch, allow_mutation=True)
    response = client.post(
        "/api/avatar-tools",
        files=[
            ("tool_id", (None, "local-12345678-1234-4123-8123-123456789abc")),
            ("name", (None, "Partial surprise")),
            ("change_mode", (None, "press-swap")),
            ("change_meanings", (None, "a gentle touch")),
            ("default_image", ("default.png", _png(), "image/png")),
            ("change_images", ("change.png", _png(), "image/png")),
            ("special_probability", (None, "0.1")),
            ("special_meaning", (None, "missing image")),
        ],
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "special_image_required"
    assert response.json()["field"] == "special_image"
    assert not manager.avatar_tools_dir.exists() or not list(manager.avatar_tools_dir.iterdir())


def test_post_returns_field_and_index_for_invalid_change_meaning(tmp_path, monkeypatch):
    client, _manager = _client(tmp_path, monkeypatch, allow_mutation=True)
    response = client.post(
        "/api/avatar-tools",
        files=[
            ("tool_id", (None, "local-12345678-1234-4123-8123-123456789abc")),
            ("name", (None, "Feather")),
            ("change_mode", (None, "click-advance")),
            ("change_meanings", (None, "first")),
            ("change_meanings", (None, "x" * 101)),
            ("default_image", ("default.png", _png(), "image/png")),
            ("change_images", ("first.png", _png(), "image/png")),
            ("change_images", ("second.png", _png(), "image/png")),
        ],
    )

    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "error_code": "change_meaning_too_long",
        "error": "change_meaning is too long",
        "field": "change_meaning",
        "index": 1,
    }


def test_post_rejects_request_without_mutation_security(tmp_path, monkeypatch):
    client, _manager = _client(tmp_path, monkeypatch, allow_mutation=False)
    response = client.post(
        "/api/avatar-tools",
        data={
            "tool_id": "local-12345678-1234-4123-8123-123456789abc",
            "name": "Feather",
            "change_mode": "press-swap",
            "change_meanings": "gentle",
        },
        files=[
            ("default_image", ("default.png", _png(), "image/png")),
            ("change_images", ("change.png", _png(), "image/png")),
        ],
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "csrf_validation_failed"


def test_delete_removes_the_created_tool_and_returns_its_id(tmp_path, monkeypatch):
    client, manager = _client(tmp_path, monkeypatch, allow_mutation=True)
    created = client.post(
        "/api/avatar-tools",
        files=[
            ("tool_id", (None, "local-12345678-1234-4123-8123-123456789abc")),
            ("name", (None, "Feather")),
            ("change_mode", (None, "press-swap")),
            ("change_meanings", (None, "gentle")),
            ("default_image", ("default.png", _png(), "image/png")),
            ("change_images", ("change.png", _png(), "image/png")),
        ],
    ).json()["item"]

    response = client.delete(f"/api/avatar-tools/{created['id']}")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "deletedId": created["id"]}
    assert not (manager.avatar_tools_dir / created["id"]).exists()
    assert client.get("/api/avatar-tools").json()["items"] == []


def test_delete_reports_missing_and_invalid_ids(tmp_path, monkeypatch):
    client, _manager = _client(tmp_path, monkeypatch, allow_mutation=True)
    missing = "local-12345678-1234-4123-8123-123456789abc"

    missing_response = client.delete(f"/api/avatar-tools/{missing}")
    invalid_response = client.delete("/api/avatar-tools/lollipop")

    assert missing_response.status_code == 404
    assert missing_response.json()["error_code"] == "tool_not_found"
    assert invalid_response.status_code == 400
    assert invalid_response.json()["error_code"] == "invalid_tool_id"


def test_delete_requires_mutation_security(tmp_path, monkeypatch):
    client, _manager = _client(tmp_path, monkeypatch, allow_mutation=False)
    response = client.delete(
        "/api/avatar-tools/local-12345678-1234-4123-8123-123456789abc"
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "csrf_validation_failed"


def test_targeted_detail_returns_meanings_without_polluting_list(tmp_path, monkeypatch):
    client, _manager = _client(tmp_path, monkeypatch, allow_mutation=True)
    created = client.post(
        "/api/avatar-tools",
        files=[
            ("tool_id", (None, "local-12345678-1234-4123-8123-123456789abc")),
            ("name", (None, "Feather")),
            ("change_mode", (None, "press-swap")),
            ("change_meanings", (None, "a private meaning")),
            ("default_image", ("default.png", _png(), "image/png")),
            ("change_images", ("change.png", _png(), "image/png")),
        ],
    ).json()["item"]

    detail = client.get(f"/api/avatar-tools/{created['id']}")
    listing = client.get("/api/avatar-tools")

    assert detail.status_code == 200
    assert detail.json()["detail"]["changeItems"][0]["meaning"] == "a private meaning"
    assert detail.json()["detail"]["defaultImage"]["resource"] == "default.png"
    assert "a private meaning" not in listing.text


def test_put_updates_same_id_and_can_remove_optional_resources(tmp_path, monkeypatch):
    client, manager = _client(tmp_path, monkeypatch, allow_mutation=True)
    created = client.post(
        "/api/avatar-tools",
        files=[
            ("tool_id", (None, "local-12345678-1234-4123-8123-123456789abc")),
            ("name", (None, "Feather")),
            ("change_mode", (None, "press-swap")),
            ("change_meanings", (None, "old meaning")),
            ("default_image", ("default.png", _png(), "image/png")),
            ("change_images", ("change.png", _png(), "image/png")),
            ("normal_sound", ("normal.mp3", _mp3(), "audio/mpeg")),
            ("special_probability", (None, "0.1")),
            ("special_image", ("special.png", _png(), "image/png")),
            ("special_meaning", (None, "old surprise")),
        ],
    ).json()["item"]
    revision = client.get(f"/api/avatar-tools/{created['id']}").json()["detail"]["revision"]

    response = client.put(
        f"/api/avatar-tools/{created['id']}",
        files=[
            ("base_revision", (None, revision)),
            ("name", (None, "Soft Feather")),
            ("change_mode", (None, "press-swap")),
            ("change_meanings", (None, "new meaning")),
            ("change_resources", (None, "change-000.png")),
            ("default_resource", (None, "default.png")),
        ],
    )

    assert response.status_code == 200
    updated = response.json()["item"]
    assert updated["id"] == created["id"]
    assert updated["name"] == "Soft Feather"
    assert "normalSoundUrl" not in updated
    assert "special" not in updated
    directory = manager.avatar_tools_dir / created["id"]
    assert not (directory / "normal.mp3").exists()
    assert not (directory / "special.png").exists()
    assert client.get(f"/api/avatar-tools/{created['id']}").json()["detail"]["changeItems"][0]["meaning"] == "new meaning"


def test_put_rejects_unowned_resource_reference_and_keeps_old_item(tmp_path, monkeypatch):
    client, _manager = _client(tmp_path, monkeypatch, allow_mutation=True)
    created = client.post(
        "/api/avatar-tools",
        files=[
            ("tool_id", (None, "local-12345678-1234-4123-8123-123456789abc")),
            ("name", (None, "Feather")),
            ("change_mode", (None, "press-swap")),
            ("change_meanings", (None, "old meaning")),
            ("default_image", ("default.png", _png(), "image/png")),
            ("change_images", ("change.png", _png(), "image/png")),
        ],
    ).json()["item"]
    revision = client.get(f"/api/avatar-tools/{created['id']}").json()["detail"]["revision"]

    response = client.put(
        f"/api/avatar-tools/{created['id']}",
        files=[
            ("base_revision", (None, revision)),
            ("name", (None, "Changed")),
            ("change_mode", (None, "press-swap")),
            ("change_meanings", (None, "changed")),
            ("change_resources", (None, "change-000.png")),
            ("default_resource", (None, "../default.png")),
        ],
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "resource_reference_invalid"
    assert client.get("/api/avatar-tools").json()["items"][0]["name"] == "Feather"


def test_put_rejects_too_many_replacement_uploads_before_reading_them(tmp_path, monkeypatch):
    client, _manager = _client(tmp_path, monkeypatch, allow_mutation=True)
    uploads = [
        ("change_images", (f"change-{index}.png", b"not-read", "image/png"))
        for index in range(17)
    ]

    response = client.put(
        "/api/avatar-tools/local-12345678-1234-4123-8123-123456789abc",
        files=[
            ("base_revision", (None, "100-200")),
            ("name", (None, "Feather")),
            ("change_mode", (None, "click-advance")),
            ("change_meanings", (None, "meaning")),
            ("change_resources", (None, "")),
            *uploads,
        ],
    )

    assert response.status_code == 413
    assert response.json()["error_code"] == "change_items_invalid"
