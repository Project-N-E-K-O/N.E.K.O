from __future__ import annotations

import errno
import io
import json
import os
import shutil
import stat as stat_module
import threading
import uuid
from pathlib import Path

import pytest
from PIL import Image

import utils.avatar_tool_store as avatar_tool_store

from utils.avatar_tool_store import (
    AvatarToolStore,
    AvatarToolStoreError,
    is_public_avatar_tool_resource_path,
)
from utils.cloudsave_runtime import MaintenanceModeError


@pytest.fixture(autouse=True)
def _isolate_store_process_state():
    """Keep the module-level quarantine / pending sets from leaking across tests."""
    import utils.avatar_tool_store as store_module

    quarantined = dict(store_module._QUARANTINED_TOOL_IDS)
    pending = set(store_module._RECOVERY_PENDING_ROOTS)
    try:
        yield
    finally:
        store_module._QUARANTINED_TOOL_IDS.clear()
        store_module._QUARANTINED_TOOL_IDS.update(quarantined)
        store_module._RECOVERY_PENDING_ROOTS.clear()
        store_module._RECOVERY_PENDING_ROOTS.update(pending)


class _ConfigManager:
    def __init__(self, root: Path):
        self.avatar_tools_dir = root

    def ensure_avatar_tools_directory(self):
        self.avatar_tools_dir.mkdir(parents=True, exist_ok=True)
        return True


def _png(*, alpha: int = 255, size=(8, 8)) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", size, (40, 100, 180, alpha)).save(output, format="PNG")
    return output.getvalue()


def _expanding_png() -> bytes:
    image = Image.new("1", (512, 512))
    image.putdata([(x + y) % 2 for y in range(512) for x in range(512)])
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
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


def _create_tool(store: AvatarToolStore, **kwargs):
    kwargs.setdefault("tool_id", f"local-{uuid.uuid4()}")
    return store.create_tool(**kwargs)


def _v3_manifest(tool_id: str, *, sources=None, name="Flow tool") -> dict:
    image_sources = sources or [{"kind": "upload", "index": 0}]
    return {
        "recordVersion": 3,
        "id": tool_id,
        "name": name,
        "images": [
            {
                "id": f"img-{index + 1}",
                "name": "" if index == 0 else f"State {index + 1}",
                "source": source,
                "meaning": "" if index == 0 else "changed state",
            }
            for index, source in enumerate(image_sources)
        ],
        "initialImageId": "img-1",
        "imageInteractions": {
            "initialImagePosition": {"x": 20, "y": 40},
            "initialLinks": [{
                "to": "ix-click",
                "sourceSide": "right",
                "targetSide": "left",
            }],
            "items": [{
                "id": "ix-click",
                "name": "",
                "trigger": {"kind": "mouse-click"},
                "actions": {
                    "press": {"kind": "keep"},
                    "release": ({"kind": "show", "imageId": "img-2"}
                                if len(image_sources) > 1 else {"kind": "keep"}),
                },
                "editorPosition": {"x": 320, "y": 40},
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


def test_v3_create_and_reopen_preserves_the_complete_editor_graph(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    manifest = _v3_manifest(tool_id, sources=[
        {"kind": "upload", "index": 0},
        {"kind": "upload", "index": 1},
    ])

    item = store.create_tool_v3(manifest=manifest, uploads=[_png(), _png(size=(12, 10))])
    detail = store.get_detail(tool_id)
    record = store.read_record(tool_id)

    assert item["recordVersion"] == 3
    assert item["revision"].startswith("3-")
    assert "imageInteractions" not in item
    assert item["initialImageUrl"].startswith(f"/user_avatar_tools/{tool_id}/image-000.png?v=")
    assert item["runtime"] == {
        "images": [
            {
                "id": "img-1",
                "url": item["initialImageUrl"],
                "hasMeaning": False,
            },
            {
                "id": "img-2",
                "url": next(
                    image["url"] for image in store.get_detail(tool_id)["images"]
                    if image["id"] == "img-2"
                ),
                "hasMeaning": True,
            },
        ],
        "initialImageId": "img-1",
        "initialInteractionIds": ["ix-click"],
        "interactions": [{
            "id": "ix-click",
            "trigger": {"kind": "mouse-click"},
            "actions": {
                "press": {"kind": "keep"},
                "release": {"kind": "show", "imageId": "img-2"},
            },
        }],
        "links": [{"from": "ix-click", "to": "ix-click"}],
    }
    assert detail["recordVersion"] == 3
    assert detail["initialImageId"] == "img-1"
    assert detail["imageInteractions"] == manifest["imageInteractions"]
    assert [image["resource"] for image in detail["images"]] == ["image-000.png", "image-001.png"]
    assert set(record) == {
        "recordVersion", "id", "name", "images", "initialImageId",
        "imageInteractions", "interaction", "resourceDigests",
    }
    assert store.list_items() == [item]


def test_v3_create_reopen_and_retained_update_preserve_optional_media(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    manifest = _v3_manifest(tool_id)
    manifest["interaction"] = {
        "normalSound": {"kind": "upload", "index": 1},
        "special": {
            "probability": 0.2,
            "image": {"kind": "upload", "index": 2},
            "meaning": "sparkles appear",
            "sound": {"kind": "upload", "index": 3},
        },
    }

    created = store.create_tool_v3(
        manifest=manifest,
        uploads=[_png(), _mp3(), _png(size=(12, 10)), _mp3()],
    )
    detail = store.get_detail(tool_id)

    assert detail["normalSound"]["resource"] == "normal.mp3"
    assert detail["special"]["image"]["resource"] == "special.png"
    assert detail["special"]["sound"]["resource"] == "special.mp3"
    assert detail["special"]["meaning"] == "sparkles appear"
    assert created["runtime"]["normalSoundUrl"] == detail["normalSound"]["url"]
    assert created["runtime"]["special"] == {
        "probability": 0.2,
        "imageUrl": detail["special"]["image"]["url"],
        "hasMeaning": True,
        "soundUrl": detail["special"]["sound"]["url"],
    }

    retained = _v3_manifest(tool_id, sources=[{"kind": "resource", "name": "image-000.png"}])
    retained["interaction"] = {
        "normalSound": {"kind": "resource", "name": "normal.mp3"},
        "special": {
            "probability": 0.3,
            "image": {"kind": "resource", "name": "special.png"},
            "meaning": "sparkles return",
            "sound": {"kind": "resource", "name": "special.mp3"},
        },
    }
    store.update_tool_v3(
        tool_id,
        base_revision=created["revision"],
        manifest=retained,
        uploads=[],
    )

    reopened = store.get_detail(tool_id)
    assert reopened["special"]["probability"] == 0.3
    assert reopened["special"]["meaning"] == "sparkles return"
    assert set(path.name for path in (store.root / tool_id).iterdir()) == {
        "record.json", "image-000.png", "normal.mp3", "special.png", "special.mp3",
    }


def test_v3_rejects_coordinates_too_large_for_the_json_client_without_publishing(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    manifest = _v3_manifest(tool_id)
    manifest["imageInteractions"]["initialImagePosition"]["x"] = 10 ** 400

    with pytest.raises(AvatarToolStoreError) as failure:
        store.create_tool_v3(manifest=manifest, uploads=[_png()])

    assert failure.value.code == "manifest_invalid"
    assert not store.root.exists() or not list(store.root.iterdir())


def test_v3_update_reuses_owned_resources_and_replaces_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    created = store.create_tool_v3(
        manifest=_v3_manifest(tool_id, sources=[
            {"kind": "upload", "index": 0},
            {"kind": "upload", "index": 1},
        ]),
        uploads=[_png(size=(8, 8)), _png(size=(9, 9))],
    )
    replacement = _png(size=(15, 11))
    updated_manifest = _v3_manifest(tool_id, name="Updated flow", sources=[
        {"kind": "resource", "name": "image-000.png"},
        {"kind": "upload", "index": 0},
    ])

    updated = store.update_tool_v3(
        tool_id,
        base_revision=created["revision"],
        manifest=updated_manifest,
        uploads=[replacement],
    )

    assert updated["revision"] != created["revision"]
    assert store.get_detail(tool_id)["name"] == "Updated flow"
    directory = store.root / tool_id
    with Image.open(directory / "image-001.png") as saved_replacement:
        assert saved_replacement.size == (15, 11)
    assert set(path.name for path in directory.iterdir()) == {
        "record.json", "image-000.png", "image-001.png",
    }
    assert not list(store.root.glob(f".{tool_id}.*"))


def test_v2_can_be_upgraded_to_v3_under_the_same_id_without_runtime_projection(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    v2 = _create_tool(
        store,
        tool_id=tool_id,
        name="Legacy",
        change_mode="press-swap",
        change_meanings=["pressed"],
        default_image=_png(),
        change_images=[_png(size=(9, 9))],
    )

    v3 = store.update_tool_v3(
        tool_id,
        base_revision=v2["revision"],
        manifest=_v3_manifest(tool_id, sources=[
            {"kind": "resource", "name": "default.png"},
            {"kind": "resource", "name": "change-000.png"},
        ]),
        uploads=[],
    )

    assert v3["id"] == tool_id
    assert v3["recordVersion"] == 3
    assert "changeMode" not in v3
    assert store.get_detail(tool_id)["recordVersion"] == 3
    assert set(path.name for path in (store.root / tool_id).iterdir()) == {
        "record.json", "image-000.png", "image-001.png",
    }


def test_v3_invalid_graph_and_stale_revision_leave_the_published_tool_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    created = store.create_tool_v3(
        manifest=_v3_manifest(tool_id),
        uploads=[_png()],
    )
    before = store.read_record(tool_id)
    invalid = _v3_manifest(tool_id, sources=[{"kind": "resource", "name": "image-000.png"}])
    invalid["imageInteractions"]["initialLinks"] = []

    with pytest.raises(AvatarToolStoreError) as invalid_error:
        store.update_tool_v3(
            tool_id,
            base_revision=created["revision"],
            manifest=invalid,
            uploads=[],
        )
    assert invalid_error.value.code == "manifest_invalid"

    with pytest.raises(AvatarToolStoreError) as conflict_error:
        store.update_tool_v3(
            tool_id,
            base_revision="3-0",
            manifest=_v3_manifest(tool_id, sources=[{"kind": "resource", "name": "image-000.png"}]),
            uploads=[],
        )
    assert conflict_error.value.code == "tool_revision_conflict"
    assert store.read_record(tool_id) == before


def test_v3_update_rejects_one_retained_resource_used_by_two_images(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    created = store.create_tool_v3(
        manifest=_v3_manifest(tool_id, sources=[
            {"kind": "upload", "index": 0},
            {"kind": "upload", "index": 1},
        ]),
        uploads=[_png(size=(8, 8)), _png(size=(9, 9))],
    )
    duplicate = _v3_manifest(tool_id, sources=[
        {"kind": "resource", "name": "image-000.png"},
        {"kind": "resource", "name": "image-000.png"},
    ])

    with pytest.raises(AvatarToolStoreError) as failure:
        store.update_tool_v3(
            tool_id,
            base_revision=created["revision"],
            manifest=duplicate,
            uploads=[],
        )

    assert failure.value.code == "resource_reference_invalid"
    assert failure.value.field == "image"
    assert failure.value.index == 1


def test_v3_image_names_use_the_same_normalized_comparison_as_the_editor(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    manifest = _v3_manifest(tool_id, sources=[
        {"kind": "upload", "index": 0},
        {"kind": "upload", "index": 1},
    ])
    manifest["images"][0]["name"] = "  Caf\u00e9  au lait  "
    manifest["images"][1]["name"] = "cafe\u0301 au   LAIT"

    with pytest.raises(AvatarToolStoreError) as failure:
        store.create_tool_v3(manifest=manifest, uploads=[_png(), _png(size=(9, 9))])

    assert failure.value.code == "image_name_duplicate"
    assert failure.value.field == "image_name"
    assert failure.value.index == 1


def test_v3_interaction_names_use_the_same_normalized_comparison_as_the_editor(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    manifest = _v3_manifest(tool_id)
    manifest["imageInteractions"]["items"][0]["name"] = "  Caf\u00e9  au lait  "
    manifest["imageInteractions"]["items"].append({
        "id": "ix-delay",
        "name": "cafe\u0301 au   LAIT",
        "trigger": {"kind": "after", "delayMs": 100},
        "actions": {"complete": {"kind": "keep"}},
        "editorPosition": {"x": 520, "y": 40},
    })
    manifest["imageInteractions"]["initialLinks"].append({
        "to": "ix-delay",
        "sourceSide": "bottom",
        "targetSide": "left",
    })

    with pytest.raises(AvatarToolStoreError) as failure:
        store.create_tool_v3(manifest=manifest, uploads=[_png()])

    assert failure.value.code == "interaction_name_duplicate"
    assert failure.value.field == "interaction_name"
    assert failure.value.index == 1


def test_create_publishes_ordered_public_dto_but_keeps_meanings_private(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))

    item = _create_tool(
        store,
        name="  小 羽毛__01  ",
        change_mode="click-advance",
        change_meanings=["像羽毛一样\r\n轻轻挠一下", "轻轻扫过脸颊"],
        default_image=_png(),
        change_images=[_png(size=(12, 9)), _png(size=(10, 11))],
    )

    assert item["id"].startswith("local-")
    assert item["revision"] == store.get_detail(item["id"])["revision"]
    assert item["name"] == "小 羽毛__01"
    assert "meaning" not in json.dumps(item, ensure_ascii=False).lower()
    assert item["changeMode"] == "click-advance"
    assert item["defaultUrl"].startswith(
        f"/user_avatar_tools/{item['id']}/default.png?v="
    )
    assert len(item["changeUrls"]) == 2
    assert "/change-000.png?v=" in item["changeUrls"][0]
    assert "/change-001.png?v=" in item["changeUrls"][1]
    record = store.read_record(item["id"])
    assert record["recordVersion"] == 2
    assert record["imageChange"] == {
        "mode": "click-advance",
        "items": [
            {"image": "change-000.png", "meaning": "像羽毛一样\n轻轻挠一下"},
            {"image": "change-001.png", "meaning": "轻轻扫过脸颊"},
        ],
    }
    assert store.list_items() == [item]
    assert not list(store.root.glob(".*.uploading"))


def test_create_publishes_optional_normal_sound_without_exposing_private_meanings(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))

    item = _create_tool(
        store,
        name="Lollipop",
        change_mode="press-swap",
        change_meanings=["takes a bite"],
        default_image=_png(),
        change_images=[_png()],
        normal_sound=_mp3(),
    )

    directory = store.root / item["id"]
    assert "/normal.mp3?v=" in item["normalSoundUrl"]
    assert (directory / "normal.mp3").read_bytes() == _mp3()
    assert store.read_record(item["id"])["interaction"] == {"normalSound": "normal.mp3"}
    assert "takes a bite" not in json.dumps(item)


def test_create_publishes_complete_special_runtime_projection_but_keeps_meaning_private(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))

    item = _create_tool(
        store,
        name="Surprise feather",
        change_mode="press-swap",
        change_meanings=["a gentle touch"],
        default_image=_png(),
        change_images=[_png()],
        normal_sound=_mp3(),
        special_probability=0.1,
        special_image=_png(size=(13, 9)),
        special_meaning="feathers suddenly scatter everywhere",
        special_sound=_mp3(),
    )

    directory = store.root / item["id"]
    assert item["special"]["probability"] == 0.1
    assert "/special.png?v=" in item["special"]["imageUrl"]
    assert "/special.mp3?v=" in item["special"]["soundUrl"]
    assert "feathers suddenly scatter everywhere" not in json.dumps(item)
    assert (directory / "special.png").is_file()
    assert (directory / "special.mp3").read_bytes() == _mp3()
    assert store.read_record(item["id"])["interaction"] == {
        "normalSound": "normal.mp3",
        "special": {
            "probability": 0.1,
            "image": "special.png",
            "meaning": "feathers suddenly scatter everywhere",
            "sound": "special.mp3",
        },
    }


@pytest.mark.parametrize(
    ("special", "expected_code"),
    [
        ({"special_probability": 0, "special_image": _png(), "special_meaning": "surprise"}, "special_probability_invalid"),
        ({"special_probability": 1.01, "special_image": _png(), "special_meaning": "surprise"}, "special_probability_invalid"),
        ({"special_probability": 0.1, "special_meaning": "surprise"}, "special_image_required"),
        ({"special_probability": 0.1, "special_image": b"image"}, "special_meaning_required"),
        ({"special_image": b"image", "special_meaning": "surprise"}, "special_probability_required"),
    ],
)
def test_create_rejects_incomplete_or_invalid_special_configuration(
    tmp_path,
    monkeypatch,
    special,
    expected_code,
):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))

    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store,
            name="bad special",
            change_mode="press-swap",
            change_meanings=["meaning"],
            default_image=_png(),
            change_images=[_png()],
            **special,
        )

    assert raised.value.code == expected_code
    assert not store.root.exists() or not list(store.root.iterdir())


@pytest.mark.parametrize(
    "audio, duration_limit, expected_code",
    [
        pytest.param(b"not-an-mp3", 10_000, "audio_decode_failed", id="invalid_audio"),
        pytest.param(_mp3(), 10, "audio_too_long", id="audio_too_long"),
    ],
)
def test_create_rejects_invalid_or_too_long_audio(
    tmp_path,
    monkeypatch,
    audio,
    duration_limit,
    expected_code,
):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    store.limits["maxAudioDurationMs"] = duration_limit

    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store,
            name="bad sound",
            change_mode="press-swap",
            change_meanings=["meaning"],
            default_image=_png(),
            change_images=[_png()],
            normal_sound=audio,
        )

    assert raised.value.code == expected_code
    assert not store.root.exists() or not list(store.root.iterdir())


def test_create_reports_invalid_special_audio_on_the_special_field(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))

    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store,
            name="bad surprise sound",
            change_mode="press-swap",
            change_meanings=["meaning"],
            default_image=_png(),
            change_images=[_png()],
            special_probability=0.1,
            special_image=_png(),
            special_meaning="surprise",
            special_sound=b"not-an-mp3",
        )

    assert raised.value.code == "special_audio_decode_failed"
    assert not store.root.exists() or not list(store.root.iterdir())


@pytest.mark.parametrize(
    "interaction",
    [
        {"normalSound": None},
        {"special": None},
        {
            "special": {
                "probability": "0.1",
                "image": "special.png",
                "meaning": "surprise",
            },
        },
    ],
)
def test_read_record_rejects_null_options_and_non_numeric_probability(
    tmp_path,
    monkeypatch,
    interaction,
):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    item = _create_tool(
        store,
        name="strict record",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    directory = store.root / item["id"]
    if "special" in interaction and isinstance(interaction["special"], dict):
        (directory / "special.png").write_bytes(_png())
    record_path = directory / "record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["interaction"] = interaction
    record_path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(AvatarToolStoreError) as raised:
        store.read_record(item["id"])

    assert raised.value.code == "record_invalid"


def test_read_record_rejects_explicit_null_special_sound(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    item = _create_tool(
        store,
        name="strict special sound",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
        special_probability=0.1,
        special_image=_png(),
        special_meaning="surprise",
    )
    record_path = store.root / item["id"] / "record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["interaction"]["special"]["sound"] = None
    record_path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(AvatarToolStoreError) as raised:
        store.read_record(item["id"])

    assert raised.value.code == "record_invalid"


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"not-png", "image_decode_failed"),
        (_png(alpha=0), "image_fully_transparent"),
    ],
)
def test_create_rejects_unsafe_images_without_publishing(tmp_path, monkeypatch, data, code):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))

    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store,
            name="bad",
            change_mode="press-swap",
            change_meanings=["meaning"],
            default_image=data,
            change_images=[_png()],
        )

    assert raised.value.code == code
    assert not store.root.exists() or not list(store.root.iterdir())


def test_create_translates_pillow_decompression_bomb_errors(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    monkeypatch.setattr(
        "utils.avatar_tool_store.Image.open",
        lambda *_a, **_k: (_ for _ in ()).throw(Image.DecompressionBombError("too many pixels")),
    )

    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store,
            name="bad",
            change_mode="press-swap",
            change_meanings=["meaning"],
            default_image=_png(),
            change_images=[_png()],
        )

    assert raised.value.code == "image_pixels_exceeded"


def test_create_reapplies_image_limit_after_canonical_encoding(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    store.limits["maxImageBytes"] = 1024
    source = _expanding_png()
    assert len(source) < store.limits["maxImageBytes"]

    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store,
            name="expanding",
            change_mode="press-swap",
            change_meanings=["meaning"],
            default_image=source,
            change_images=[_png()],
        )

    assert raised.value.code == "image_too_large"
    assert not store.root.exists() or not list(store.root.iterdir())


def test_initialize_cleans_only_owned_transient_directories_and_list_stays_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    root = tmp_path / "avatar_tools"
    root.mkdir()
    owned_upload = root / ".local-12345678-1234-4123-8123-123456789abc.uploading"
    owned_delete = root / ".local-22345678-1234-4123-8123-123456789abc.deleting"
    owned_upload.mkdir()
    owned_delete.mkdir()
    unrelated = root / ".keep-me"
    unrelated.mkdir()
    invalid = root / "local-12345678-1234-4123-8123-123456789abc"
    invalid.mkdir()
    (invalid / "record.json").write_text("{}", encoding="utf-8")

    store = AvatarToolStore(_ConfigManager(root))

    assert store.list_items() == []
    assert owned_upload.exists()
    assert owned_delete.exists()
    store.initialize()
    assert not owned_upload.exists()
    assert not owned_delete.exists()
    assert unrelated.exists()


def test_list_skips_a_record_with_invalid_utf8_without_hiding_valid_tools(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    valid = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    invalid_id = "local-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    invalid_directory = store.root / invalid_id
    invalid_directory.mkdir()
    (invalid_directory / "record.json").write_bytes(b"\xff\xfe")

    assert [item["id"] for item in store.list_items()] == [valid["id"]]


def test_corrupt_hidden_records_do_not_consume_the_visible_tool_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    store.ensure()
    store.limits["maxTools"] = 1
    invalid = store.root / "local-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    invalid.mkdir()
    (invalid / "record.json").write_bytes(b"not-json")

    created = _create_tool(
        store,
        name="Visible",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )

    assert [item["id"] for item in store.list_items()] == [created["id"]]


def test_list_isolates_a_tool_when_a_persisted_resource_fails_integrity(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    valid = _create_tool(
        store,
        name="Valid",
        change_mode="press-swap",
        change_meanings=["valid"],
        default_image=_png(),
        change_images=[_png()],
    )
    damaged = _create_tool(
        store,
        name="Damaged",
        change_mode="press-swap",
        change_meanings=["damaged"],
        default_image=_png(size=(9, 9)),
        change_images=[_png(size=(10, 10))],
    )
    assert {item["id"] for item in store.list_items()} == {valid["id"], damaged["id"]}
    (store.root / damaged["id"] / "default.png").write_bytes(b"truncated")

    # Listing no longer recomputes bytewise digests (too costly; the frontend lists on every focus),
    # so tampered tools initially remain visible in listings...
    assert {item["id"] for item in store.list_items()} == {valid["id"], damaged["id"]}
    # ...but actual consumers reject them immediately: detail / edit pages perform full validation.
    with pytest.raises(AvatarToolStoreError) as raised:
        store.get_detail(damaged["id"])
    assert raised.value.code == "record_invalid"

    # Detail validation removes it from the public catalog immediately, without waiting for restart.
    try:
        assert raised.value.integrity_mismatch is True
        assert damaged["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS[store._root_key()]
        assert [item["id"] for item in store.list_items()] == [valid["id"]]
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(store._root_key(), None)


def test_initialize_and_list_wrap_unreadable_store_errors(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    store.ensure()

    def reject_listing(_path):
        raise PermissionError("blocked")

    monkeypatch.setattr(Path, "iterdir", reject_listing)

    for operation in (store.initialize, store.list_items):
        with pytest.raises(AvatarToolStoreError) as raised:
            operation()
        assert raised.value.code == "avatar_tools_directory_unavailable"
        assert raised.value.status_code == 503


def test_public_resource_allowlist_rejects_private_and_unsafe_paths(tmp_path):
    root = tmp_path / "avatar_tools"
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    directory = root / tool_id
    directory.mkdir(parents=True)
    (directory / "change-000.png").write_bytes(_png())
    (directory / "change-015.png").write_bytes(_png())
    (directory / "normal.mp3").write_bytes(_mp3())
    (directory / "special.png").write_bytes(_png())
    (directory / "special.mp3").write_bytes(_mp3())
    (directory / "record.json").write_text("{}", encoding="utf-8")

    assert is_public_avatar_tool_resource_path(root, f"{tool_id}/change-000.png")
    assert is_public_avatar_tool_resource_path(root, f"{tool_id}/change-015.png")
    assert is_public_avatar_tool_resource_path(root, f"{tool_id}/normal.mp3")
    assert is_public_avatar_tool_resource_path(root, f"{tool_id}/special.png")
    assert is_public_avatar_tool_resource_path(root, f"{tool_id}/special.mp3")
    assert not is_public_avatar_tool_resource_path(root, f"{tool_id}/change-16.png")
    assert not is_public_avatar_tool_resource_path(root, f"{tool_id}/record.json")
    assert not is_public_avatar_tool_resource_path(root, f"{tool_id}/.hidden.png")
    assert not is_public_avatar_tool_resource_path(root, f"../{tool_id}/change-000.png")
    assert not is_public_avatar_tool_resource_path(root, f"{tool_id}/change-000.svg")

    (directory / "default.png").symlink_to(directory / "change-000.png")
    assert not is_public_avatar_tool_resource_path(root, f"{tool_id}/default.png")


def test_create_rejects_control_characters(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))

    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store,
            name="two\nlines",
            change_mode="press-swap",
            change_meanings=["meaning"],
            default_image=_png(),
            change_images=[_png()],
        )

    assert raised.value.code == "name_invalid"
    assert raised.value.field == "name"

    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store,
            name="Feather",
            change_mode="click-advance",
            change_meanings=["first", "invalid\x07meaning"],
            default_image=_png(),
            change_images=[_png(), _png()],
        )

    assert raised.value.code == "change_meaning_invalid"
    assert raised.value.field == "change_meaning"
    assert raised.value.index == 1


@pytest.mark.parametrize(
    ("name", "expected_code"),
    [
        ("a" * 21, "name_too_long"),
        ("羽毛!", "name_invalid"),
        ("羽毛🪶", "name_invalid"),
    ],
)
def test_create_enforces_new_name_rules(tmp_path, monkeypatch, name, expected_code):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))

    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store,
            name=name,
            change_mode="press-swap",
            change_meanings=["meaning"],
            default_image=_png(),
            change_images=[_png()],
        )

    assert raised.value.code == expected_code
    assert raised.value.field == "name"


def test_create_reports_change_meaning_error_location(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))

    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store,
            name="Feather",
            change_mode="click-advance",
            change_meanings=["first", "x" * 101],
            default_image=_png(),
            change_images=[_png(), _png()],
        )

    assert raised.value.code == "change_meaning_too_long"
    assert raised.value.field == "change_meaning"
    assert raised.value.index == 1


@pytest.mark.parametrize(
    ("field", "expected_code"),
    [
        ("name", "name_too_long"),
        ("meaning", "meaning_too_long"),
    ],
)
def test_read_enforces_current_v2_text_limits(tmp_path, monkeypatch, field, expected_code):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    item = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    record_path = store.root / item["id"] / "record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if field == "name":
        record["name"] = "n" * 21
    else:
        record["imageChange"]["items"][0]["meaning"] = "m" * 101
    record_path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(AvatarToolStoreError) as raised:
        store.read_record(item["id"])

    # Persisted-field validation reuses the form validator, but reads normalize it to
    # record_invalid so quarantine recognizes it and oversized tools do not remain counted against quota
    # while hidden from listings. Original field codes remain in the exception chain.
    assert raised.value.code == "record_invalid"
    assert raised.value.transient is False
    assert isinstance(raised.value.__cause__, AvatarToolStoreError)
    assert raised.value.__cause__.code == expected_code


def test_create_counts_record_in_total_storage_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    default_image = _png()
    change_image = _png()
    store.limits["maxTotalBytes"] = len(default_image) + len(change_image)

    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store,
            name="Feather",
            change_mode="press-swap",
            change_meanings=["gentle"],
            default_image=default_image,
            change_images=[change_image],
        )

    assert raised.value.code == "storage_limit_reached"
    assert not list(store.root.iterdir())


def test_create_checks_the_write_fence_before_creating_the_store_directory(tmp_path, monkeypatch):
    root = tmp_path / "avatar_tools"
    store = AvatarToolStore(_ConfigManager(root))

    def reject_write(*_args, **_kwargs):
        raise RuntimeError("maintenance")

    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", reject_write)

    with pytest.raises(RuntimeError, match="maintenance"):
        _create_tool(
            store,
            name="Feather",
            change_mode="press-swap",
            change_meanings=["gentle"],
            default_image=_png(),
            change_images=[_png()],
        )

    assert not root.exists()


def test_press_swap_requires_exactly_one_change_item(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))

    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store,
            name="Feather",
            change_mode="press-swap",
            change_meanings=["one", "two"],
            default_image=_png(),
            change_images=[_png(), _png()],
        )

    assert raised.value.code == "change_items_invalid"


def test_old_development_record_is_skipped_without_deletion(tmp_path):
    root = tmp_path / "avatar_tools"
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    directory = root / tool_id
    directory.mkdir(parents=True)
    (directory / "default.png").write_bytes(_png())
    (directory / "pressed.png").write_bytes(_png())
    (directory / "record.json").write_text(json.dumps({
        "recordVersion": 1,
        "id": tool_id,
        "name": "old",
        "images": {"default": "default.png", "pressed": "pressed.png"},
        "interaction": {"normalMeaning": "old"},
    }), encoding="utf-8")

    store = AvatarToolStore(_ConfigManager(root))

    assert store.list_items() == []
    assert directory.is_dir()


def test_delete_removes_only_the_requested_tool_directory(tmp_path, monkeypatch):
    fence_calls = []
    monkeypatch.setattr(
        "utils.avatar_tool_store.assert_cloudsave_writable",
        lambda *_a, **kwargs: fence_calls.append(kwargs),
    )
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    first = _create_tool(
        store,
        name="First",
        change_mode="press-swap",
        change_meanings=["first"],
        default_image=_png(),
        change_images=[_png()],
    )
    second = _create_tool(
        store,
        name="Second",
        change_mode="press-swap",
        change_meanings=["second"],
        default_image=_png(),
        change_images=[_png()],
    )
    fence_calls.clear()

    assert store.delete_tool(first["id"]) == first["id"]

    assert not (store.root / first["id"]).exists()
    assert (store.root / second["id"] / "record.json").is_file()
    assert [item["id"] for item in store.list_items()] == [second["id"]]
    assert fence_calls == [{
        "operation": "delete",
        "target": f"avatar_tools/{first['id']}",
    }]


@pytest.mark.parametrize(
    ("tool_id", "expected_code", "expected_status"),
    [
        ("lollipop", "invalid_tool_id", 400),
        ("local-12345678-1234-4123-8123-123456789abc", "tool_not_found", 404),
    ],
)
def test_delete_rejects_invalid_or_missing_tool(tmp_path, tool_id, expected_code, expected_status):
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))

    with pytest.raises(AvatarToolStoreError) as raised:
        store.delete_tool(tool_id)

    assert raised.value.code == expected_code
    assert raised.value.status_code == expected_status


def test_delete_rejects_symlink_without_touching_its_target(tmp_path):
    root = tmp_path / "avatar_tools"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    root.mkdir()
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    (root / tool_id).symlink_to(outside, target_is_directory=True)
    store = AvatarToolStore(_ConfigManager(root))

    with pytest.raises(AvatarToolStoreError) as raised:
        store.delete_tool(tool_id)

    assert raised.value.code == "tool_not_found"
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("replacement", ("directory", "record"))
def test_delete_preserves_a_new_version_published_after_initial_observation(
    tmp_path, monkeypatch, replacement
):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id, name="Original"), uploads=[_png()])
    external_store = AvatarToolStore(_ConfigManager(tmp_path / "external_avatar_tools"))
    newest = external_store.create_tool_v3(
        manifest=_v3_manifest(tool_id, name="Replaced"), uploads=[_png()]
    )
    final = store.root / tool_id
    new_directory = external_store.root / tool_id
    new_record = (new_directory / "record.json").read_bytes()

    def publish_new_version(*_args, **kwargs):
        assert kwargs["operation"] == "delete"
        if replacement == "directory":
            os.replace(final, tmp_path / "original")
            os.replace(new_directory, final)
        else:
            (final / "record.json").write_bytes(new_record)

    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", publish_new_version)

    with pytest.raises(AvatarToolStoreError) as raised:
        store.delete_tool(tool_id)

    assert raised.value.code == "tool_delete_failed"
    assert raised.value.status_code == 409
    assert store.get_detail(tool_id)["revision"] == newest["revision"]
    assert (final / "record.json").read_bytes() == new_record
    assert not (store.root / f".{tool_id}.deleting").exists()


@pytest.mark.parametrize("replacement", ("directory", "record"))
@pytest.mark.parametrize("republish_final", (False, True))
@pytest.mark.parametrize("crash_after_move", (False, True))
def test_delete_preserves_an_unconfirmed_moved_version_across_restart(
    tmp_path, monkeypatch, replacement, republish_final, crash_after_move
):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id, name="Original"), uploads=[_png()])
    external_store = AvatarToolStore(_ConfigManager(tmp_path / "external_avatar_tools"))
    external_store.create_tool_v3(
        manifest=_v3_manifest(tool_id, name="Replaced"), uploads=[_png()]
    )
    final = store.root / tool_id
    newest = external_store.root / tool_id
    new_record = (newest / "record.json").read_bytes()
    deleting = store.root / f".{tool_id}.deleting"
    real_replace = os.replace

    class SimulatedCrash(BaseException):
        pass

    def replace_after_last_probe(source, destination, *args, **kwargs):
        if Path(source) == final.resolve() and Path(destination) == deleting:
            if replacement == "directory":
                real_replace(final, tmp_path / "original")
                real_replace(newest, final)
            else:
                (final / "record.json").write_bytes(new_record)
            result = real_replace(source, destination, *args, **kwargs)
            if republish_final:
                final.mkdir()
                (final / "synced-note.txt").write_bytes(b"another publication")
            if crash_after_move:
                raise SimulatedCrash()
            return result
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", replace_after_last_probe)

    with pytest.raises(SimulatedCrash if crash_after_move else AvatarToolStoreError) as raised:
        store.delete_tool(tool_id)

    # Without a crash, if the official path is free, deletion moves the unauthorized rename back: effectively no deletion.
    restored_inline = not crash_after_move and not republish_final
    if not crash_after_move:
        assert raised.value.code == "tool_delete_failed"
        assert raised.value.status_code == 409
        assert (store._root_key() in avatar_tool_store._RECOVERY_PENDING_ROOTS) is not restored_inline
    if restored_inline:
        assert not deleting.exists()
        assert not (store.root / f".{tool_id}.deleting.unverified").exists()
        assert (final / "record.json").read_bytes() == new_record
    else:
        assert (deleting / "record.json").read_bytes() == new_record

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", real_replace)
    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())
    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()

    # Unconfirmed retained copies block only their own ID, no longer the entire store awaiting recovery.
    assert restarted._root_key() not in avatar_tool_store._RECOVERY_PENDING_ROOTS
    if restored_inline:
        assert restarted.get_detail(tool_id)["name"] == "Replaced"
        return
    if not republish_final:
        # Official path free: startup recovery undoes the unprovable deletion by restoring the copy unchanged;
        # the tool reappears and can be deleted again rather than indefinitely occupying its ID and quota.
        assert not deleting.exists()
        assert not (store.root / f".{tool_id}.deleting.unverified").exists()
        assert (final / "record.json").read_bytes() == new_record, "startup lost the moved version"
        assert restarted.get_detail(tool_id)["name"] == "Replaced"
        assert restarted.delete_tool(tool_id) == tool_id
        return
    assert (deleting / "record.json").is_file(), "startup deleted an unconfirmed moved version"
    assert (deleting / "record.json").read_bytes() == new_record
    assert restarted._current_storage_bytes() >= sum(
        path.stat().st_size for path in deleting.iterdir() if path.is_file()
    )
    assert (final / "synced-note.txt").read_bytes() == b"another publication"
    with pytest.raises(AvatarToolStoreError) as blocked:
        restarted.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    assert (blocked.value.code, blocked.value.status_code) == ("tool_delete_pending", 409)
    other = restarted.create_tool_v3(
        manifest=_v3_manifest(f"local-{uuid.uuid4()}", name="Other"), uploads=[_png()]
    )
    assert restarted.delete_tool(other["id"]) == other["id"]
    assert (deleting / "record.json").read_bytes() == new_record


@pytest.mark.parametrize("move_first", (False, True))
def test_delete_restart_recovers_authorized_move_or_unused_marker(tmp_path, monkeypatch, move_first):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    original = store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    real_replace = os.replace

    class SimulatedCrash(BaseException):
        pass

    def interrupt_move(source, destination, *args, **kwargs):
        if Path(source) == final.resolve() and Path(destination) == deleting:
            if move_first:
                real_replace(source, destination, *args, **kwargs)
            raise SimulatedCrash()
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", interrupt_move)
    with pytest.raises(SimulatedCrash):
        store.delete_tool(tool_id)

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", real_replace)
    real_iterdir = Path.iterdir

    def deleting_before_marker(path):
        return iter(sorted(real_iterdir(path), key=lambda entry: entry.name))

    monkeypatch.setattr(Path, "iterdir", deleting_before_marker)
    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()

    assert not marker.exists()
    assert not deleting.exists()
    assert restarted._root_key() not in avatar_tool_store._RECOVERY_PENDING_ROOTS
    assert restarted.list_items() == ([] if move_first else [original])


def test_delete_persists_authorization_directory_entry_before_move(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    calls = []
    real_replace = os.replace

    monkeypatch.setattr(
        avatar_tool_store,
        "_fsync_directory",
        lambda path: calls.append(("fsync-directory", Path(path))),
    )

    def record_replace(source, destination, *args, **kwargs):
        if Path(source) == final.resolve() and Path(destination) == deleting:
            calls.append(("move", Path(destination).parent))
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", record_replace)

    assert store.delete_tool(tool_id) == tool_id
    assert calls[:2] == [
        ("fsync-directory", store.root),
        ("move", store.root),
    ]


@pytest.mark.parametrize("failure", ("directory-probe", "record-probe", "marker-probe", "marker-json"))
def test_delete_keeps_unreadable_authorization_or_moved_objects_for_recovery(
    tmp_path, monkeypatch, failure
):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    record_bytes = (final / "record.json").read_bytes()
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    real_replace, real_lstat = os.replace, os.lstat
    moved = False
    authorization_bytes = None

    def move_then_interrupt_verification(source, destination, *args, **kwargs):
        nonlocal moved, authorization_bytes
        result = real_replace(source, destination, *args, **kwargs)
        if Path(destination) == deleting:
            moved = True
            authorization_bytes = marker.read_bytes()
            if failure == "marker-json":
                marker.write_bytes(b"{")
        return result

    locked_path = {
        "directory-probe": deleting,
        "record-probe": deleting / "record.json",
        "marker-probe": marker,
    }.get(failure)

    def unreadable_after_move(path, *args, **kwargs):
        if moved and locked_path is not None and Path(path) == locked_path:
            raise OSError(errno.EIO, "temporarily unreadable")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", move_then_interrupt_verification)
    monkeypatch.setattr("utils.avatar_tool_store.os.lstat", unreadable_after_move)
    with pytest.raises(AvatarToolStoreError) as raised:
        store.delete_tool(tool_id)
    assert raised.value.status_code == (409 if failure == "marker-json" else 503)
    if failure == "marker-json":
        # Unreadable authorization cannot prove the moved object was authorized: restore it and cancel deletion.
        assert not deleting.exists()
        assert not marker.exists()
        assert (final / "record.json").read_bytes() == record_bytes
        assert store._root_key() not in avatar_tool_store._RECOVERY_PENDING_ROOTS
        return
    assert (deleting / "record.json").read_bytes() == record_bytes
    assert store._root_key() in avatar_tool_store._RECOVERY_PENDING_ROOTS

    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()
    assert (deleting / "record.json").read_bytes() == record_bytes
    assert restarted._root_key() in avatar_tool_store._RECOVERY_PENDING_ROOTS

    monkeypatch.setattr("utils.avatar_tool_store.os.lstat", real_lstat)
    monkeypatch.setattr("utils.avatar_tool_store.os.replace", real_replace)
    if failure == "marker-json":
        marker.write_bytes(authorization_bytes)
    restarted.initialize()
    assert not deleting.exists()
    assert not marker.exists()
    assert restarted._root_key() not in avatar_tool_store._RECOVERY_PENDING_ROOTS


def test_delete_restores_an_unauthorized_move_and_keeps_the_store_writable(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    target_id, other_id, new_id = (f"local-{uuid.uuid4()}" for _ in range(3))
    store.create_tool_v3(manifest=_v3_manifest(target_id, name="Target"), uploads=[_png()])
    other = store.create_tool_v3(manifest=_v3_manifest(other_id, name="Other"), uploads=[_png()])
    final = store.root / target_id
    deleting = store.root / f".{target_id}.deleting"
    real_replace = os.replace

    def sync_rewrites_record_before_move(source, destination, *args, **kwargs):
        if Path(destination) == deleting:
            record_path = final / "record.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["name"] = "Synced"
            record_path.write_text(json.dumps(record), encoding="utf-8")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", sync_rewrites_record_before_move)
    with pytest.raises(AvatarToolStoreError) as raised:
        store.delete_tool(target_id)
    monkeypatch.setattr("utils.avatar_tool_store.os.replace", real_replace)

    assert (raised.value.code, raised.value.status_code) == ("tool_delete_failed", 409)
    assert sorted(path.name for path in store.root.iterdir()) == sorted([target_id, other_id])
    assert store._root_key() not in avatar_tool_store._RECOVERY_PENDING_ROOTS
    assert {item["id"]: item["name"] for item in store.list_items()} == {
        target_id: "Synced",
        other_id: "Other",
    }
    assert store.get_detail(target_id)["name"] == "Synced"

    updated = store.update_tool_v3(
        other_id,
        base_revision=other["revision"],
        manifest=_v3_manifest(other_id, name="Other 2"),
        uploads=[_png()],
    )
    assert updated["name"] == "Other 2"
    store.create_tool_v3(manifest=_v3_manifest(new_id, name="New"), uploads=[_png()])
    assert store.delete_tool(other_id) == other_id

    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()
    assert restarted._root_key() not in avatar_tool_store._RECOVERY_PENDING_ROOTS
    assert sorted(item["id"] for item in restarted.list_items()) == sorted([target_id, new_id])
    assert restarted.delete_tool(target_id) == target_id


def test_delete_keeps_an_unauthorized_move_for_recovery_when_it_cannot_be_restored(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    real_replace = os.replace

    def touch_record_then_refuse_restore(source, destination, *args, **kwargs):
        if Path(destination) == deleting:
            (final / "record.json").write_bytes((final / "record.json").read_bytes())
        elif Path(source) == deleting:
            raise OSError(errno.EACCES, "restore refused")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", touch_record_then_refuse_restore)
    with pytest.raises(AvatarToolStoreError) as raised:
        store.delete_tool(tool_id)

    assert (raised.value.code, raised.value.status_code) == ("tool_delete_failed", 409)
    assert (deleting / "record.json").is_file()
    assert marker.is_file()
    assert not final.exists()
    assert store._root_key() in avatar_tool_store._RECOVERY_PENDING_ROOTS


@pytest.mark.parametrize(
    "flavour",
    ("identity-mismatch", "corrupt-marker", "deeply-nested-marker", "directory-marker"),
)
def test_a_retained_unconfirmed_deletion_blocks_only_its_own_tool_id(tmp_path, monkeypatch, flavour):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    blocked_id, other_id, new_id = (f"local-{uuid.uuid4()}" for _ in range(3))
    store.create_tool_v3(manifest=_v3_manifest(blocked_id, name="Blocked"), uploads=[_png()])
    other = store.create_tool_v3(manifest=_v3_manifest(other_id, name="Other"), uploads=[_png()])
    final = store.root / blocked_id
    deleting = store.root / f".{blocked_id}.deleting"
    marker = store.root / f".{blocked_id}.deleting.unverified"
    real_replace = os.replace

    class SimulatedCrash(BaseException):
        pass

    def crash_after_move(source, destination, *args, **kwargs):
        result = real_replace(source, destination, *args, **kwargs)
        if Path(destination) == deleting:
            raise SimulatedCrash()
        return result

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", crash_after_move)
    with pytest.raises(SimulatedCrash):
        store.delete_tool(blocked_id)
    monkeypatch.setattr("utils.avatar_tool_store.os.replace", real_replace)
    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())

    # After a crash (for example, migrating a copied store root), the moved object no longer matches authorization.
    if flavour == "identity-mismatch":
        record_path = deleting / "record.json"
        record_path.write_bytes(record_path.read_bytes())
    elif flavour == "corrupt-marker":
        marker.write_bytes(b"{")
    elif flavour == "directory-marker":
        # A sync client or filesystem corruption turned the authorization location into a directory.
        marker.unlink()
        marker.mkdir()
        (marker / "stray").write_bytes(b"x")
    else:
        # Under 4 KiB can nest deeply enough for json.loads to raise RecursionError; treat it as
        # an authorization mismatch rather than crashing the entire recovery pass.
        marker.write_bytes(b"[" * 3000)
    retained_record = (deleting / "record.json").read_bytes()
    # A sync client published another object at the official path: recovery must retain the copy rather than overwrite it.
    shutil.copytree(deleting, final)

    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()
    assert restarted._root_key() not in avatar_tool_store._RECOVERY_PENDING_ROOTS
    assert (deleting / "record.json").read_bytes() == retained_record

    # When authorization is a directory, even explicit deletion must preserve its contents; the UI offers no deletion prompt.
    pending_code = "tool_recovery_pending" if flavour == "directory-marker" else "tool_delete_pending"

    def assert_pending(operation):
        with pytest.raises(AvatarToolStoreError) as raised:
            operation()
        assert (raised.value.code, raised.value.status_code) == (pending_code, 409)

    assert_pending(lambda: restarted.create_tool_v3(
        manifest=_v3_manifest(blocked_id), uploads=[_png()]
    ))
    republished = restarted.get_detail(blocked_id)
    assert_pending(lambda: restarted.update_tool_v3(
        blocked_id,
        base_revision=republished["revision"],
        manifest=_v3_manifest(blocked_id, name="Changed"),
        uploads=[_png()],
    ))

    updated = restarted.update_tool_v3(
        other_id,
        base_revision=other["revision"],
        manifest=_v3_manifest(other_id, name="Other 2"),
        uploads=[_png()],
    )
    assert updated["name"] == "Other 2"
    restarted.create_tool_v3(manifest=_v3_manifest(new_id, name="New"), uploads=[_png()])
    assert restarted.delete_tool(other_id) == other_id
    assert (deleting / "record.json").read_bytes() == retained_record
    assert marker.exists()
    if flavour == "directory-marker":
        assert_pending(lambda: restarted.delete_tool(blocked_id, base_revision=republished["revision"]))
        assert (deleting / "record.json").read_bytes() == retained_record
        assert (marker / "stray").read_bytes() == b"x"
        return

    # Rejecting deletion with a stale revision must not discard the copy first.
    with pytest.raises(AvatarToolStoreError) as conflict:
        restarted.delete_tool(blocked_id, base_revision="1-1")
    assert (conflict.value.code, conflict.value.status_code) == ("tool_revision_conflict", 409)
    assert (deleting / "record.json").read_bytes() == retained_record
    assert marker.exists()

    # The user explicitly deletes this ID: remove the retained copy with the official directory, freeing the ID
    # instead of leaving it stuck forever in tool_delete_pending.
    assert restarted.delete_tool(blocked_id, base_revision=republished["revision"]) == blocked_id
    assert not deleting.exists()
    assert not marker.exists()
    assert not final.exists()
    restarted.create_tool_v3(manifest=_v3_manifest(blocked_id, name="Reborn"), uploads=[_png()])
    assert restarted.get_detail(blocked_id)["name"] == "Reborn"


def test_a_delete_rejected_by_the_identity_recheck_keeps_the_retained_copy(tmp_path, monkeypatch):
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    shutil.copytree(final, deleting)
    marker.write_bytes(b"{")
    retained_record = (deleting / "record.json").read_bytes()

    def republish_during_fence(*_args, **kwargs):
        # The write fence falls between initial identity observation and revalidation: a sync client replaces record exactly there.
        if kwargs.get("operation") == "delete":
            record_path = final / "record.json"
            replacement = final / "record.json.synced"
            replacement.write_bytes(record_path.read_bytes())
            os.replace(replacement, record_path)

    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", republish_during_fence)
    with pytest.raises(AvatarToolStoreError) as raised:
        store.delete_tool(tool_id)

    assert (raised.value.code, raised.value.status_code) == ("tool_delete_failed", 409)
    assert (deleting / "record.json").read_bytes() == retained_record
    assert marker.exists()
    assert final.is_dir()


@pytest.mark.parametrize("failing_step", ("fsync", "open"))
# Windows cannot open directory handles, so _fsync_directory already skips directory syncing there;
# neither injected failure can occur.
@pytest.mark.skipif(os.name == "nt", reason="directory fsync is unsupported on Windows")
def test_discarding_a_retained_deletion_stops_when_revoking_it_is_not_durable(
    tmp_path, monkeypatch, failing_step
):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    shutil.copytree(final, deleting)
    marker.write_bytes(b"{")
    retained_record = (deleting / "record.json").read_bytes()

    real_fsync = os.fsync
    real_open = os.open

    def failing_fsync(_fd):
        raise OSError("simulated I/O error")

    def failing_directory_open(path, *args, **kwargs):
        # POSIX supports opening directories: do not swallow EMFILE or EIO as an unsupported platform.
        if Path(path) == store.root:
            raise OSError(errno.EMFILE, "simulated descriptor exhaustion")
        return real_open(path, *args, **kwargs)

    if failing_step == "fsync":
        monkeypatch.setattr("utils.avatar_tool_store.os.fsync", failing_fsync)
    else:
        monkeypatch.setattr("utils.avatar_tool_store.os.open", failing_directory_open)
    with pytest.raises(AvatarToolStoreError) as raised:
        store.delete_tool(tool_id)

    # Authorization revocation did not persist: preserve both copy and official directory. Directory syncing keeps failing, so restoring
    # authorization cannot be confirmed durable either; leave the copy parked after restoring the original authorization.
    assert (raised.value.code, raised.value.status_code) == ("tool_delete_failed", 500)
    parked = store.root / f".{tool_id}.retained"
    assert (parked / "record.json").read_bytes() == retained_record
    assert not deleting.exists()
    assert final.is_dir()
    assert marker.read_bytes() == b"{"
    assert store._root_key() in avatar_tool_store._RECOVERY_PENDING_ROOTS
    # Retrying deletion in the same process: recovery first moves the copy and original authorization back to retained-copy state, then discards normally,
    # without remaining tool_delete_pending until restart.
    monkeypatch.setattr("utils.avatar_tool_store.os.fsync", real_fsync)
    monkeypatch.setattr("utils.avatar_tool_store.os.open", real_open)
    assert store.delete_tool(tool_id) == tool_id
    assert not deleting.exists()
    assert not marker.exists()
    assert not parked.exists()
    assert not final.exists()


@pytest.mark.skipif(os.name == "nt", reason="directories cannot be opened on Windows")
@pytest.mark.parametrize("strict", (False, True))
def test_closing_a_synced_directory_only_fails_a_strict_sync(tmp_path, monkeypatch, strict):
    # Best-effort syncing sits between paired steps (such as just parking a retained copy): handle-close errors must not skip caller rollback.
    from utils.avatar_tool_store import _fsync_directory

    real_close = os.close
    closed = []

    def failing_close(fd):
        real_close(fd)
        closed.append(fd)
        raise OSError(errno.EIO, "simulated close failure")

    monkeypatch.setattr("utils.avatar_tool_store.os.close", failing_close)
    if strict:
        with pytest.raises(OSError):
            _fsync_directory(tmp_path, strict=True)
    else:
        _fsync_directory(tmp_path)
    assert len(closed) == 1


@pytest.mark.parametrize("failing_step", ("fsync", "open"))
@pytest.mark.parametrize("code", ("EINVAL", "EBADF", "ENOTSUP", "EOPNOTSUPP"))
def test_discarding_a_retained_deletion_works_where_directories_cannot_be_synced(
    tmp_path, monkeypatch, failing_step, code
):
    # Some CIFS/SMB and FUSE mounts consistently return these errno values for directory fsync: this means unsupported,
    # not a durability failure this time. Treating it as failure would make retained copies prevent this ID's deletion forever.
    if not hasattr(errno, code):
        pytest.skip(f"errno.{code} is not defined on this platform")
    unsupported = getattr(errno, code)
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    shutil.copytree(final, deleting)
    marker.write_bytes(b"{")

    real_fsync = os.fsync
    real_open = os.open

    def unsupported_directory_fsync(fd):
        if stat_module.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(unsupported, "directory sync unsupported")
        return real_fsync(fd)

    def unsupported_directory_open(path, *args, **kwargs):
        if Path(path) == store.root:
            raise OSError(unsupported, "directory open unsupported")
        return real_open(path, *args, **kwargs)

    if failing_step == "fsync":
        monkeypatch.setattr("utils.avatar_tool_store.os.fsync", unsupported_directory_fsync)
    else:
        monkeypatch.setattr("utils.avatar_tool_store.os.open", unsupported_directory_open)

    assert store.delete_tool(tool_id) == tool_id
    assert not deleting.exists()
    assert not marker.exists()
    assert not final.exists()


@pytest.mark.parametrize(
    "replaced",
    ("copy", "record-in-place", "resource-in-place", "nested-in-place", "marker"),
)
def test_a_retained_copy_replaced_after_it_was_observed_is_not_discarded(tmp_path, monkeypatch, replaced):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    shutil.copytree(final, deleting)
    if replaced == "nested-in-place":
        # Valid tool directories are flat, but retained copies are abnormal leftovers and may contain subdirectories.
        (deleting / "nested").mkdir()
        (deleting / "nested" / "stray.bin").write_bytes(b"old")
    marker.write_bytes(b"{")
    synced_record = b'{"synced": "newer version"}'

    def sync_during_fence(*_args, **kwargs):
        # The write fence falls between observing and discarding a copy: a sync client replaces the copy or authorization there.
        if kwargs.get("operation") != "delete":
            return
        if replaced == "copy":
            shutil.rmtree(deleting)
            deleting.mkdir()
            (deleting / "record.json").write_bytes(synced_record)
        elif replaced == "record-in-place":
            # Rewriting the same file in place leaves directory identity unchanged; only record.json changes.
            (deleting / "record.json").write_bytes(synced_record)
        elif replaced == "nested-in-place":
            # Rewriting a nested file leaves both the copy directory and subdirectory identities unchanged.
            (deleting / "nested" / "stray.bin").write_bytes(b"synced newer")
        elif replaced == "resource-in-place":
            # Rewriting an image in place leaves both directory and record.json identities unchanged.
            resource = next(path for path in deleting.iterdir() if path.suffix == ".png")
            resource.write_bytes(resource.read_bytes() + b"synced")
        else:
            marker.unlink()
            marker.write_bytes(b"[]")

    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", sync_during_fence)
    with pytest.raises(AvatarToolStoreError) as raised:
        store.delete_tool(tool_id)

    # The replacement might be newer: reject the entire deletion, leaving copy, authorization, and official directory intact.
    assert (raised.value.code, raised.value.status_code) == ("tool_delete_failed", 409)
    assert deleting.is_dir()
    assert marker.is_file()
    assert final.is_dir()
    if replaced in ("copy", "record-in-place"):
        assert (deleting / "record.json").read_bytes() == synced_record
    if replaced == "resource-in-place":
        assert any(path.read_bytes().endswith(b"synced") for path in deleting.glob("*.png"))
    if replaced == "nested-in-place":
        assert (deleting / "nested" / "stray.bin").read_bytes() == b"synced newer"


def test_discarding_a_retained_deletion_survives_a_crash_after_revoking_it(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    shutil.copytree(final, deleting)
    marker.write_bytes(b"{")
    real_rmtree = shutil.rmtree

    class SimulatedCrash(BaseException):
        pass

    def crash_on_discard(path, *args, **kwargs):
        # The copy is parked under .retained and deleted only after staging the official directory; crash at this step.
        if Path(path).name.endswith(".retained"):
            raise SimulatedCrash()
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("utils.avatar_tool_store.shutil.rmtree", crash_on_discard)
    with pytest.raises(SimulatedCrash):
        store.delete_tool(tool_id)
    monkeypatch.setattr("utils.avatar_tool_store.shutil.rmtree", real_rmtree)

    # Official deletion is staged: confirmed deletion and a parked copy remain, so recovery simply removes them.
    assert not marker.exists()
    assert not final.exists()
    assert list(store.root.glob(".*.retained"))
    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())
    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()
    assert not deleting.exists()
    assert not list(store.root.glob(".*.retained"))
    restarted.create_tool_v3(manifest=_v3_manifest(tool_id, name="Reborn"), uploads=[_png()])


def test_a_crash_before_the_published_delete_is_staged_keeps_the_retained_copy(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    shutil.copytree(final, deleting)
    marker.write_bytes(b"{")
    retained_record = (deleting / "record.json").read_bytes()

    class SimulatedCrash(BaseException):
        pass

    def crash_before_staging(*_args, **_kwargs):
        # Copy and original authorization are parked, but official deletion is not staged: the process exits here.
        raise SimulatedCrash()

    monkeypatch.setattr(AvatarToolStore, "_stage_delete_locked", crash_before_staging)
    with pytest.raises(SimulatedCrash):
        store.delete_tool(tool_id)
    monkeypatch.undo()
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)

    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())
    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()
    # Official directory still exists; deletion never happened: restore copy and original authorization to retained-copy state, not orphan cleanup.
    assert final.is_dir()
    assert (deleting / "record.json").read_bytes() == retained_record
    assert marker.read_bytes() == b"{"
    assert not list(store.root.glob(".*.retained"))
    assert restarted.delete_tool(tool_id) == tool_id
    assert not deleting.exists()
    assert not final.exists()


@pytest.mark.parametrize("state", ("unresolved-deleting", "final-not-a-directory"))
def test_recovery_keeps_a_parked_copy_it_cannot_place_and_blocks_only_its_id(tmp_path, monkeypatch, state):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    parked = store.root / f".{tool_id}.retained"
    # Copy parked before the crash, containing its original authorization.
    shutil.copytree(final, parked)
    (store.root / f".{tool_id}.retained.unverified").write_bytes(b"{")
    parked_record = (parked / "record.json").read_bytes()
    if state == "unresolved-deleting":
        # Staging moved a sync-replaced directory to .deleting; verification fails, and the official path is
        # occupied again, preventing restoration: this deletion is unproven and did not happen.
        shutil.copytree(final, deleting)
        (store.root / f".{tool_id}.deleting.unverified").write_bytes(b"{")
    else:
        # The official path was synced into a regular file.
        shutil.rmtree(final)
        final.write_bytes(b"not a directory")

    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())
    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()

    # Deletion cannot be determined: neither discard the parked copy as confirmed deletion nor let it
    # silently consume quota while the ID remains writable.
    assert (parked / "record.json").read_bytes() == parked_record
    assert restarted._root_key() not in avatar_tool_store._RECOVERY_PENDING_ROOTS
    with pytest.raises(AvatarToolStoreError) as blocked:
        restarted.delete_tool(tool_id)
    assert (blocked.value.code, blocked.value.status_code) == ("tool_recovery_pending", 409)
    other = restarted.create_tool_v3(manifest=_v3_manifest(f"local-{uuid.uuid4()}", name="Other"), uploads=[_png()])
    assert restarted.delete_tool(other["id"]) == other["id"]


def test_a_crash_while_parking_beside_a_directory_marker_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    parked = store.root / f".{tool_id}.retained"
    # Crash after parking the copy but before moving in authorization, which a sync client replaced with a directory.
    shutil.copytree(final, parked)
    marker.mkdir()
    (marker / "stray.bin").write_bytes(b"synced")
    parked_record = (parked / "record.json").read_bytes()

    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())
    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()

    # Recovery must not stall on undeletable directory authorization: restore copy and original authorization to retained-copy state.
    assert restarted._root_key() not in avatar_tool_store._RECOVERY_PENDING_ROOTS
    assert (deleting / "record.json").read_bytes() == parked_record
    assert (marker / "stray.bin").read_bytes() == b"synced"
    assert not parked.exists()
    # Even explicit deletion preserves directory-authorization contents, blocking only this ID.
    with pytest.raises(AvatarToolStoreError) as blocked:
        restarted.delete_tool(tool_id)
    assert blocked.value.code == "tool_recovery_pending"
    assert (marker / "stray.bin").read_bytes() == b"synced"
    assert (deleting / "record.json").read_bytes() == parked_record


def test_recovery_never_recursively_deletes_a_directory_at_an_orphan_marker_path(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    marker = store.root / f".{tool_id}.deleting.unverified"
    # A sync client placed a directory at the authorization location, with neither .deleting nor a parked copy nearby.
    marker.mkdir()
    (marker / "unknown.bin").write_bytes(b"not ours")

    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())
    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()

    # Its contents are unknown: do not recursively delete; recovery completes normally and blocks only this ID.
    assert restarted._root_key() not in avatar_tool_store._RECOVERY_PENDING_ROOTS
    assert (marker / "unknown.bin").read_bytes() == b"not ours"
    with pytest.raises(AvatarToolStoreError) as blocked:
        restarted.delete_tool(tool_id)
    assert (blocked.value.code, blocked.value.status_code) == ("tool_recovery_pending", 409)
    other = restarted.create_tool_v3(manifest=_v3_manifest(f"local-{uuid.uuid4()}", name="Other"), uploads=[_png()])
    assert restarted.delete_tool(other["id"]) == other["id"]


@pytest.mark.skipif(os.name == "nt", reason="directory fsync is unsupported on Windows")
def test_a_rolled_back_copy_waits_until_its_restored_marker_is_durable(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    parked = store.root / f".{tool_id}.retained"
    shutil.copytree(final, deleting)
    marker.write_bytes(b"{")
    retained_record = (deleting / "record.json").read_bytes()
    real_replace = os.replace
    real_fsync = os.fsync
    state = {"staging_failed": False}

    def fail_staging(source, destination, *args, **kwargs):
        if Path(source) == final and Path(destination) == deleting:
            state["staging_failed"] = True
            raise OSError(errno.ENOSPC, "simulated disk full")
        return real_replace(source, destination, *args, **kwargs)

    def directory_sync_fails_after_staging(fd):
        # Store-root directory syncing fails during rollback: restoring authorization may not be durable.
        if state["staging_failed"] and stat_module.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "simulated I/O error")
        return real_fsync(fd)

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", fail_staging)
    monkeypatch.setattr("utils.avatar_tool_store.os.fsync", directory_sync_fails_after_staging)
    with pytest.raises(AvatarToolStoreError):
        store.delete_tool(tool_id)
    monkeypatch.setattr("utils.avatar_tool_store.os.replace", real_replace)
    monkeypatch.setattr("utils.avatar_tool_store.os.fsync", real_fsync)

    # Without confirmed authorization durability, do not restore the copy to .deleting: a crash would leave unauthorized .deleting subject to cleanup.
    assert final.is_dir()
    assert not deleting.exists()
    assert (parked / "record.json").read_bytes() == retained_record
    assert store._root_key() in avatar_tool_store._RECOVERY_PENDING_ROOTS
    # Later recovery restores copy and original authorization to retained-copy state.
    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())
    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()
    assert (deleting / "record.json").read_bytes() == retained_record
    assert marker.read_bytes() == b"{"
    assert not parked.exists()


def test_parking_is_durable_before_the_marker_moves(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    shutil.copytree(final, deleting)
    marker.write_bytes(b"{")
    real_replace = os.replace
    real_fsync = os.fsync
    events = []

    def record_replace(source, destination, *args, **kwargs):
        events.append(("replace", Path(source).name, Path(destination).name))
        return real_replace(source, destination, *args, **kwargs)

    def record_fsync(fd):
        if stat_module.S_ISDIR(os.fstat(fd).st_mode):
            events.append(("dir-fsync",))
        return real_fsync(fd)

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", record_replace)
    monkeypatch.setattr("utils.avatar_tool_store.os.fsync", record_fsync)
    assert store.delete_tool(tool_id) == tool_id

    park = events.index(("replace", deleting.name, f".{tool_id}.retained"))
    move_marker = next(
        index for index, event in enumerate(events)
        if event[0] == "replace" and event[1] == marker.name
    )
    # Touch authorization only after parking is durable: persisting only the later step before a crash restores an unauthorized .deleting copy.
    assert park < move_marker
    if os.name != "nt":
        assert ("dir-fsync",) in events[park + 1:move_marker]
    # Park authorization in the store root beside the copy (rename within one directory), and strictly persist the root
    # before staging the official directory.
    assert events[move_marker][2] == f".{tool_id}.retained.unverified"
    stage = events.index(("replace", final.name, deleting.name))
    assert move_marker < stage
    if os.name != "nt":
        assert ("dir-fsync",) in events[move_marker + 1:stage]


def test_a_rolled_back_marker_that_authorizes_the_copy_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    parked = store.root / f".{tool_id}.retained"
    shutil.copytree(final, deleting)
    marker.write_bytes(b"{")
    retained_record = (deleting / "record.json").read_bytes()
    real_replace = os.replace
    rewritten = []

    def rollback_with_rewritten_marker(source, destination, *args, **kwargs):
        if Path(source) == final and Path(destination) == deleting:
            raise OSError(errno.ENOSPC, "simulated disk full")
        result = real_replace(source, destination, *args, **kwargs)
        if not rewritten and Path(source).name.endswith(".retained.unverified") and Path(destination) == marker:
            # A sync client rewrites original authorization while parked: the restored authorization happens to authorize the copy.
            rewritten.append(True)
            _, _, directory_identity, _ = avatar_tool_store._probe_entry_state(parked)
            record_kind, _, record_identity, _ = avatar_tool_store._probe_entry_state(parked / "record.json")
            marker.write_text(json.dumps({
                "directoryIdentity": list(directory_identity[:-1]),
                "recordKind": record_kind,
                "recordIdentity": list(record_identity),
            }), encoding="utf-8")
        return result

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", rollback_with_rewritten_marker)
    with pytest.raises(AvatarToolStoreError):
        store.delete_tool(tool_id)
    monkeypatch.setattr("utils.avatar_tool_store.os.replace", real_replace)

    assert rewritten
    assert final.is_dir()
    # Never return a copy to .deleting beside authorization that matches it: recovery would clean it as confirmed deletion.
    assert not (deleting.exists() and store._delete_authorization_matches(deleting, marker))
    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())
    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()
    assert (deleting / "record.json").read_bytes() == retained_record
    assert not restarted._delete_authorization_matches(deleting, marker)


def test_a_parked_copy_becomes_resolvable_without_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    parked = store.root / f".{tool_id}.retained"
    shutil.copytree(final, parked)
    (store.root / f".{tool_id}.retained.unverified").write_bytes(b"{")
    published = tmp_path / "published"
    shutil.move(str(final), str(published))
    # The official path was synced into a regular file: startup cannot decide, retains the parked copy, and blocks only this ID.
    final.write_bytes(b"not a directory")
    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())
    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()
    recovery_runs = []
    real_recover = AvatarToolStore._recover_interrupted_mutations

    def counting_recover(self):
        recovery_runs.append(True)
        return real_recover(self)

    monkeypatch.setattr(AvatarToolStore, "_recover_interrupted_mutations", counting_recover)
    for _ in range(3):
        with pytest.raises(AvatarToolStoreError) as blocked:
            restarted.delete_tool(tool_id)
        assert blocked.value.code == "tool_recovery_pending"
    # Surrounding state is unchanged and the copy remains indeterminate: repeated operations do not rerun all recovery.
    assert recovery_runs == []

    # A sync client later restores the official directory: without restart, the next deletion reruns recovery, returns the copy
    # to retained-copy state, then discards both normally.
    final.unlink()
    shutil.move(str(published), str(final))
    assert restarted.delete_tool(tool_id) == tool_id
    assert not parked.exists()
    assert not deleting.exists()
    assert not final.exists()


def test_a_parked_copy_beside_a_kept_deleting_copy_becomes_resolvable_without_a_restart(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    parked = store.root / f".{tool_id}.retained"
    shutil.copytree(final, parked)
    (store.root / f".{tool_id}.retained.unverified").write_bytes(b"{")
    # Another .deleting has mismatched authorization while the official directory exists: startup cannot determine either side.
    shutil.copytree(final, deleting)
    marker.write_bytes(b"{")
    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())
    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()
    recovery_runs = []
    real_recover = AvatarToolStore._recover_interrupted_mutations

    def counting_recover(self):
        recovery_runs.append(True)
        return real_recover(self)

    monkeypatch.setattr(AvatarToolStore, "_recover_interrupted_mutations", counting_recover)
    for _ in range(3):
        with pytest.raises(AvatarToolStoreError) as blocked:
            restarted.delete_tool(tool_id)
        assert blocked.value.code == "tool_recovery_pending"
    assert recovery_runs == []

    # A sync client later removes the official directory: .deleting can return to its original path, making the parked copy
    # decidable too. Without restart, the next deletion reruns recovery and discards both normally.
    shutil.move(str(final), str(tmp_path / "moved-away"))
    assert restarted.delete_tool(tool_id) == tool_id
    assert recovery_runs
    assert not parked.exists()
    assert not deleting.exists()
    assert not marker.exists()
    assert not final.exists()


@pytest.mark.parametrize("marker_beside_copy", (True, False))
def test_recovery_never_takes_an_entry_of_the_parked_copy_for_its_authorization(
    tmp_path, monkeypatch, marker_beside_copy
):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    parked = store.root / f".{tool_id}.retained"
    parked_marker = store.root / f".{tool_id}.retained.unverified"
    shutil.copytree(final, parked)
    # A sync client placed an authorization-named entry inside the copy; real authorization is either parked
    # beside the copy or already lost in the crash.
    lookalike = parked / f".retained-{uuid.uuid4()}.unverified"
    lookalike.write_bytes(b"synced user data")
    if marker_beside_copy:
        parked_marker.write_bytes(b"{")
    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())
    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()

    # Restore the copy unchanged to retained-copy state, preserving all entries, beside authorization that does not match it.
    assert not parked.exists()
    assert not parked_marker.exists()
    assert (deleting / lookalike.name).read_bytes() == b"synced user data"
    assert marker.is_file()
    assert not restarted._delete_authorization_matches(deleting, marker)
    if marker_beside_copy:
        assert marker.read_bytes() == b"{"
    assert restarted.delete_tool(tool_id) == tool_id
    assert not deleting.exists()
    assert not marker.exists()
    assert not parked_marker.exists()


@pytest.mark.parametrize("kind", ("file", "dir"))
def test_recovery_clears_the_marker_of_a_parked_copy_that_is_gone(tmp_path, monkeypatch, kind):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    parked_marker = store.root / f".{tool_id}.retained.unverified"
    store.initialize()
    # Deletion completed and the parked copy was removed; the crash only preceded cleanup of adjacent authorization.
    if kind == "file":
        parked_marker.write_bytes(b"{")
    else:
        parked_marker.mkdir()
        (parked_marker / "stray.bin").write_bytes(b"synced")
    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())
    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()

    assert restarted._root_key() not in avatar_tool_store._RECOVERY_PENDING_ROOTS
    if kind == "file":
        assert not parked_marker.exists()
    else:
        # This module did not create the directory; do not recursively delete it.
        assert (parked_marker / "stray.bin").read_bytes() == b"synced"


def test_a_retained_copy_is_put_back_when_staging_the_delete_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    shutil.copytree(final, deleting)
    marker.write_bytes(b"{")
    retained_record = (deleting / "record.json").read_bytes()
    real_replace = os.replace

    def fail_staging(source, destination, *args, **kwargs):
        # Moving the official directory to .deleting fails, for example due to a full disk.
        if Path(source) == final and Path(destination) == deleting:
            raise OSError(errno.ENOSPC, "simulated disk full")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", fail_staging)
    with pytest.raises(AvatarToolStoreError) as raised:
        store.delete_tool(tool_id)
    monkeypatch.setattr("utils.avatar_tool_store.os.replace", real_replace)

    # Failed deletion means it never happened: restore the copy beside mismatched authorization; the official directory remains.
    assert raised.value.code == "tool_delete_failed"
    assert (deleting / "record.json").read_bytes() == retained_record
    assert marker.exists()
    assert final.is_dir()
    assert not list(store.root.glob(".*.retained"))
    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())
    assert store.delete_tool(tool_id) == tool_id
    assert not deleting.exists()
    assert not final.exists()


def test_a_retained_copy_synced_in_just_before_it_is_parked_is_not_discarded(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    shutil.copytree(final, deleting)
    marker.write_bytes(b"{")
    synced_record = b'{"synced": "newer version"}'
    real_replace = os.replace
    swapped = []

    def sync_right_before_parking(source, destination, *args, **kwargs):
        # Between verification and rename, a sync client replaces the whole copy. Verify-then-rename leaves
        # this window; claim by renaming first, then verify the claimed object.
        if not swapped and Path(source) == deleting and Path(destination).name.endswith(".retained"):
            swapped.append(True)
            shutil.rmtree(deleting)
            shutil.copytree(final, deleting)
            (deleting / "record.json").write_bytes(synced_record)
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", sync_right_before_parking)
    with pytest.raises(AvatarToolStoreError) as raised:
        store.delete_tool(tool_id)
    monkeypatch.setattr("utils.avatar_tool_store.os.replace", real_replace)

    assert swapped
    assert (raised.value.code, raised.value.status_code) == ("tool_delete_failed", 409)
    assert (deleting / "record.json").read_bytes() == synced_record
    assert marker.read_bytes() == b"{"
    assert final.is_dir()
    assert not list(store.root.glob(".*.retained"))


def test_a_retained_copy_survives_a_failed_delete_when_no_marker_can_be_written(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    shutil.copytree(final, deleting)
    marker.write_bytes(b"{")
    retained_record = (deleting / "record.json").read_bytes()
    real_open = Path.open

    def disk_full_for_markers(self, mode="r", *args, **kwargs):
        # Full disk: cannot write authorization for the official directory, nor replacement authorization after failure.
        if "x" in mode and self.name.endswith(".deleting.unverified"):
            raise OSError(errno.ENOSPC, "simulated disk full")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", disk_full_for_markers)
    with pytest.raises(AvatarToolStoreError) as raised:
        store.delete_tool(tool_id)

    # Rollback uses renames alone: restore the original authorization and copy unchanged without writing new files.
    assert (raised.value.code, raised.value.status_code) == ("tool_delete_failed", 500)
    assert (deleting / "record.json").read_bytes() == retained_record
    assert marker.read_bytes() == b"{"
    assert final.is_dir()
    assert not list(store.root.glob(".*.retained"))
    # Restart recovery still sees mismatched authorization and an existing official directory: retain the copy, never clean it as confirmed deletion.
    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())
    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()
    assert (deleting / "record.json").read_bytes() == retained_record
    monkeypatch.setattr(Path, "open", real_open)
    assert restarted.delete_tool(tool_id) == tool_id
    assert not deleting.exists()
    assert not marker.exists()
    assert not final.exists()


def test_a_rolled_back_copy_is_never_left_without_its_marker(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    shutil.copytree(final, deleting)
    marker.write_bytes(b"{")
    real_open = Path.open
    real_replace = os.replace

    def disk_full_for_markers(self, mode="r", *args, **kwargs):
        if "x" in mode and self.name.endswith(".deleting.unverified"):
            raise OSError(errno.ENOSPC, "simulated disk full")
        return real_open(self, mode, *args, **kwargs)

    def marker_cannot_move_back(source, destination, *args, **kwargs):
        # Staging fails, renaming original authorization back also fails, and writing new authorization fails too.
        if Path(source).name.endswith(".retained.unverified") and Path(destination) == marker:
            raise OSError(errno.EIO, "simulated I/O error")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(Path, "open", disk_full_for_markers)
    monkeypatch.setattr("utils.avatar_tool_store.os.replace", marker_cannot_move_back)
    with pytest.raises(AvatarToolStoreError) as raised:
        store.delete_tool(tool_id)

    assert (raised.value.code, raised.value.status_code) == ("tool_delete_failed", 500)
    assert final.is_dir()
    # Recovery treats unauthorized .deleting as confirmed deletion: prefer leaving the copy parked.
    assert not (deleting.exists() and not marker.exists())
    assert not deleting.exists()
    assert list(store.root.glob(".*.retained"))
    assert store._root_key() in avatar_tool_store._RECOVERY_PENDING_ROOTS
    # After the fault clears, recovery sees the official directory and unstaged deletion: restore copy and original authorization.
    monkeypatch.setattr(Path, "open", real_open)
    monkeypatch.setattr("utils.avatar_tool_store.os.replace", real_replace)
    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())
    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()
    assert (deleting / "record.json").is_file()
    assert marker.read_bytes() == b"{"
    assert not list(store.root.glob(".*.retained"))


def test_a_transient_probe_error_during_rollback_keeps_the_retained_copy(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    shutil.copytree(final, deleting)
    marker.write_bytes(b"{")
    retained_record = (deleting / "record.json").read_bytes()
    real_open = Path.open
    real_probe = avatar_tool_store._probe_entry
    state = {"staging_failed": False, "probe_failed": False}

    def staging_marker_fails_once(self, mode="r", *args, **kwargs):
        if "x" in mode and self == marker and not state["staging_failed"]:
            state["staging_failed"] = True
            raise OSError(errno.ENOSPC, "simulated disk full")
        return real_open(self, mode, *args, **kwargs)

    def marker_probe_fails_once_during_rollback(path):
        # During rollback after staging failure, probing the authorization location hits a transient error.
        if state["staging_failed"] and not state["probe_failed"] and Path(path) == marker:
            state["probe_failed"] = True
            return "unknown", 0, OSError(errno.EIO, "simulated transient error")
        return real_probe(path)

    monkeypatch.setattr(Path, "open", staging_marker_fails_once)
    monkeypatch.setattr("utils.avatar_tool_store._probe_entry", marker_probe_fails_once_during_rollback)
    with pytest.raises(AvatarToolStoreError):
        store.delete_tool(tool_id)

    # Unreadable authorization location proves neither presence nor absence: restore original authorization; the copy either
    # returns to .deleting after confirmed mismatch or stays parked, but is never discarded.
    assert state["probe_failed"]
    assert marker.read_bytes() == b"{"
    assert final.is_dir()
    parked = store.root / f".{tool_id}.retained"
    holder = deleting if deleting.exists() else parked
    assert (holder / "record.json").read_bytes() == retained_record
    # Recovery restores the copy and original authorization to retained-copy state.
    monkeypatch.setattr(Path, "open", real_open)
    monkeypatch.setattr("utils.avatar_tool_store._probe_entry", real_probe)
    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())
    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()
    assert (deleting / "record.json").read_bytes() == retained_record
    assert marker.read_bytes() == b"{"
    assert not parked.exists()


def test_a_marker_synced_in_during_rollback_is_replaced_by_the_original(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    shutil.copytree(final, deleting)
    marker.write_bytes(b"{")
    retained_record = (deleting / "record.json").read_bytes()
    real_replace = os.replace
    synced = []

    def sync_marker_after_parking(source, destination, *args, **kwargs):
        result = real_replace(source, destination, *args, **kwargs)
        if not synced and Path(destination).name.endswith(".retained.unverified"):
            # Just after parking original authorization, a sync client replaces it at the original location with chosen content
            # that might authorize this copy, preventing this deletion from writing its own authorization.
            synced.append(True)
            marker.write_bytes(b'{"synced": true}')
        return result

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", sync_marker_after_parking)
    with pytest.raises(AvatarToolStoreError):
        store.delete_tool(tool_id)
    monkeypatch.setattr("utils.avatar_tool_store.os.replace", real_replace)

    assert synced
    assert final.is_dir()
    # Restore the copy beside its known-mismatching original authorization; never leave external authorization beside it.
    assert (deleting / "record.json").read_bytes() == retained_record
    assert marker.read_bytes() == b"{"
    assert not list(store.root.glob(".*.retained"))
    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())
    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()
    assert (deleting / "record.json").read_bytes() == retained_record


def test_an_unreadable_retained_copy_still_blocks_saves_with_delete_pending(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    created = store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    deleting = store.root / f".{tool_id}.deleting"
    shutil.copytree(store.root / tool_id, deleting)
    (store.root / f".{tool_id}.deleting.unverified").write_bytes(b"{")

    def unreadable(_path):
        # The copy contains an unreadable subdirectory. Saving only needs to identify a retained copy, not traverse it.
        raise PermissionError(errno.EACCES, "simulated unreadable subdirectory")

    monkeypatch.setattr("utils.avatar_tool_store._retained_copy_state", unreadable)
    with pytest.raises(AvatarToolStoreError) as blocked:
        store.update_tool_v3(
            tool_id,
            base_revision=created["revision"],
            manifest=_v3_manifest(tool_id, name="Renamed"),
            uploads=[_png()],
        )

    assert (blocked.value.code, blocked.value.status_code) == ("tool_delete_pending", 409)


def test_a_deleting_entry_that_deleting_cannot_clear_reports_recovery_pending(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    created = store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    # Sync or corruption turned .deleting into a regular file that explicit deletion cannot clear; the UI must not
    # suggest deleting first and recreating.
    deleting = store.root / f".{tool_id}.deleting"
    deleting.write_bytes(b"stray")

    with pytest.raises(AvatarToolStoreError) as update_blocked:
        store.update_tool_v3(
            tool_id,
            base_revision=created["revision"],
            manifest=_v3_manifest(tool_id, name="Renamed"),
            uploads=[_png()],
        )
    with pytest.raises(AvatarToolStoreError) as delete_blocked:
        store.delete_tool(tool_id)

    assert (update_blocked.value.code, update_blocked.value.status_code) == ("tool_recovery_pending", 409)
    assert (delete_blocked.value.code, delete_blocked.value.status_code) == ("tool_recovery_pending", 409)
    assert deleting.read_bytes() == b"stray"


def _replace_probabilities(value, replacement):
    if isinstance(value, dict):
        return {
            key: replacement if key == "probability" else _replace_probabilities(item, replacement)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_probabilities(item, replacement) for item in value]
    return value


@pytest.mark.parametrize("digits", (400, 5000))
@pytest.mark.parametrize("record_version", (2, 3))
def test_an_overflowing_special_probability_is_invalid_not_a_server_error(
    tmp_path, monkeypatch, record_version, digits
):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    # JSON integer literals have no length cap; float() conversion can raise OverflowError.
    # At 400 digits float() raises OverflowError; 5000 digits exceeds the interpreter's integer-digit limit,
    # causing json.loads to raise ordinary ValueError before validation.
    huge = 10 ** digits
    special_uploads = [_png(), _png(size=(12, 10))]

    def special_manifest(tool_id, probability):
        manifest = _v3_manifest(tool_id)
        manifest["interaction"] = {
            "special": {
                "probability": probability,
                "image": {"kind": "upload", "index": 1},
                "meaning": "sparkles appear",
            },
        }
        return manifest

    with pytest.raises(AvatarToolStoreError) as raised:
        store.create_tool_v3(manifest=special_manifest(f"local-{uuid.uuid4()}", huge), uploads=special_uploads)
    assert raised.value.code == "special_probability_invalid"
    assert raised.value.status_code == 400

    if record_version == 3:
        tool_id = f"local-{uuid.uuid4()}"
        store.create_tool_v3(manifest=special_manifest(tool_id, 0.2), uploads=special_uploads)
    else:
        tool_id = _create_tool(
            store,
            name="Tampered",
            change_mode="press-swap",
            change_meanings=["a gentle touch"],
            default_image=_png(),
            change_images=[_png()],
            special_probability=0.1,
            special_image=_png(size=(13, 9)),
            special_meaning="feathers scatter",
        )["id"]
    other_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(other_id, name="Other"), uploads=[_png()])
    record_path = store.root / tool_id / "record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    tampered = _replace_probabilities(record, "__HUGE__")
    assert tampered != record
    # json.dumps also cannot serialize integers beyond the digit limit; splice them directly into text.
    record_path.write_text(
        json.dumps(tampered).replace('"__HUGE__"', "1" + "0" * digits), encoding="utf-8"
    )

    # One corrupt on-disk record invalidates only itself; it must not make the entire listing raise.
    listed = {item["id"] for item in store.list_items()}
    assert other_id in listed
    assert tool_id not in listed


def test_delete_unpublishes_before_cleanup_and_initialize_retries_residue(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    deleted = _create_tool(
        store,
        name="Deleted",
        change_mode="press-swap",
        change_meanings=["deleted"],
        default_image=_png(),
        change_images=[_png()],
    )
    retained = _create_tool(
        store,
        name="Retained",
        change_mode="press-swap",
        change_meanings=["retained"],
        default_image=_png(),
        change_images=[_png()],
    )
    deleting = store.root / f".{deleted['id']}.deleting"
    real_rmtree = shutil.rmtree

    def interrupt_cleanup(path, *args, **kwargs):
        if Path(path).resolve(strict=False) == deleting.resolve(strict=False):
            (deleting / "record.json").unlink()
            raise OSError("cleanup interrupted")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("utils.avatar_tool_store.shutil.rmtree", interrupt_cleanup)

    assert store.delete_tool(deleted["id"]) == deleted["id"]
    assert not (store.root / deleted["id"]).exists()
    assert deleting.is_dir()
    assert [item["id"] for item in store.list_items()] == [retained["id"]]
    assert store._current_storage_bytes() == (
        store._directory_bytes(store.root / retained["id"])
        + store._directory_bytes(deleting)
    )

    monkeypatch.setattr("utils.avatar_tool_store.shutil.rmtree", real_rmtree)
    store.initialize()

    assert not deleting.exists()
    assert (store.root / retained["id"] / "record.json").is_file()


def test_detail_exposes_editable_meanings_without_changing_public_projection(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    item = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["a gentle touch"],
        default_image=_png(),
        change_images=[_png(size=(9, 8))],
        special_probability=0.2,
        special_image=_png(size=(10, 8)),
        special_meaning="a surprise appears",
    )

    detail = store.get_detail(item["id"])

    assert detail["id"] == item["id"]
    assert detail["defaultImage"]["resource"] == "default.png"
    assert detail["changeItems"][0]["meaning"] == "a gentle touch"
    assert detail["special"]["meaning"] == "a surprise appears"
    assert "meaning" not in json.dumps(store.list_items())


def test_create_reuses_the_same_client_tool_id_after_a_lost_response(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = "local-12345678-1234-4123-8123-123456789abc"

    def create():
        return _create_tool(
            store,
            tool_id=tool_id,
            name="Feather",
            change_mode="press-swap",
            change_meanings=["a gentle touch"],
            default_image=_png(),
            change_images=[_png(size=(9, 8))],
        )

    first = create()
    second = create()

    assert first == second
    assert [item["id"] for item in store.list_items()] == [tool_id]


def test_create_rejects_a_different_submission_for_an_existing_client_tool_id(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    _create_tool(
        store,
        tool_id=tool_id,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["a gentle touch"],
        default_image=_png(),
        change_images=[_png(size=(9, 8))],
    )

    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store,
            tool_id=tool_id,
            name="Changed feather",
            change_mode="press-swap",
            change_meanings=["a different touch"],
            default_image=_png(),
            change_images=[_png(size=(9, 8))],
        )

    assert raised.value.code == "tool_id_conflict"
    assert raised.value.status_code == 409
    assert store.read_record(tool_id)["name"] == "Feather"


def test_update_keeps_id_reorders_retained_images_and_removes_optional_resources(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    created = _create_tool(
        store,
        name="Feather",
        change_mode="click-advance",
        change_meanings=["first", "second"],
        default_image=_png(size=(8, 8)),
        change_images=[_png(size=(9, 8)), _png(size=(10, 8))],
        normal_sound=_mp3(),
        special_probability=0.1,
        special_image=_png(size=(11, 8)),
        special_meaning="surprise",
        special_sound=_mp3(),
    )
    tool_id = created["id"]
    base_revision = store.get_detail(tool_id)["revision"]

    updated = store.update_tool(
        tool_id,
        base_revision=base_revision,
        name="Soft Feather",
        change_mode="click-advance",
        change_meanings=["second retained", "new image"],
        default_resource="default.png",
        default_image=None,
        change_resources=["change-001.png", ""],
        change_images=[_png(size=(12, 8))],
    )

    assert updated["id"] == tool_id
    assert updated["name"] == "Soft Feather"
    assert "normalSoundUrl" not in updated
    assert "special" not in updated
    record = store.read_record(tool_id)
    assert record["imageChange"]["items"] == [
        {"image": "change-000.png", "meaning": "second retained"},
        {"image": "change-001.png", "meaning": "new image"},
    ]
    directory = store.root / tool_id
    assert not (directory / "normal.mp3").exists()
    assert not (directory / "special.png").exists()
    assert not (directory / "special.mp3").exists()
    with Image.open(directory / "change-000.png") as retained:
        assert retained.size == (10, 8)
    with Image.open(directory / "change-001.png") as replacement:
        assert replacement.size == (12, 8)
    assert not (store.root / f".{tool_id}.updating").exists()
    assert not (store.root / f".{tool_id}.backup").exists()


def test_update_rejects_foreign_resource_and_preserves_published_tool(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    created = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    before = store.read_record(created["id"])
    base_revision = store.get_detail(created["id"])["revision"]

    with pytest.raises(AvatarToolStoreError) as raised:
        store.update_tool(
            created["id"],
            base_revision=base_revision,
            name="Changed",
            change_mode="press-swap",
            change_meanings=["changed"],
            default_resource="../default.png",
            default_image=None,
            change_resources=["change-000.png"],
            change_images=[],
        )

    assert raised.value.code == "resource_reference_invalid"
    assert store.read_record(created["id"]) == before


def test_update_rejects_a_stale_edit_revision(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    created = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )

    with pytest.raises(AvatarToolStoreError) as raised:
        store.update_tool(
            created["id"],
            base_revision="1-1",
            name="Changed",
            change_mode="press-swap",
            change_meanings=["changed"],
            default_resource="default.png",
            default_image=None,
            change_resources=["change-000.png"],
            change_images=[],
        )

    assert raised.value.code == "tool_revision_conflict"
    assert raised.value.status_code == 409
    assert store.read_record(created["id"])["name"] == "Feather"


def test_asset_only_update_changes_revision_independently_of_record_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    created = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png(size=(9, 8))],
    )
    tool_id = created["id"]
    before_record = store.read_record(tool_id)
    before_revision = store.get_detail(tool_id)["revision"]
    before_default_size = (store.root / tool_id / "default.png").stat().st_size

    before_default_url = created["defaultUrl"]
    updated = store.update_tool(
        tool_id,
        base_revision=before_revision,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_resource=None,
        default_image=_png(alpha=254),
        change_resources=["change-000.png"],
        change_images=[],
    )

    after_record = store.read_record(tool_id)
    assert {
        key: value for key, value in after_record.items() if key != "resourceDigests"
    } == {
        key: value for key, value in before_record.items() if key != "resourceDigests"
    }
    assert store.get_detail(tool_id)["revision"] != before_revision
    assert updated["revision"] == store.get_detail(tool_id)["revision"]
    assert (store.root / tool_id / "default.png").stat().st_size == before_default_size
    assert updated["defaultUrl"] != before_default_url
    assert updated["defaultUrl"].endswith(after_record["resourceDigests"]["default.png"])
    with pytest.raises(AvatarToolStoreError) as raised:
        store.update_tool(
            tool_id,
            base_revision=before_revision,
            name="Stale edit",
            change_mode="press-swap",
            change_meanings=["stale"],
            default_resource="default.png",
            default_image=None,
            change_resources=["change-000.png"],
            change_images=[],
        )
    assert raised.value.code == "tool_revision_conflict"


def test_initialize_restores_a_valid_backup_after_interrupted_update(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    created = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    tool_id = created["id"]
    final = store.root / tool_id
    backup = store.root / f".{tool_id}.backup"
    updating = store.root / f".{tool_id}.updating"
    shutil.copytree(final, backup)
    shutil.copytree(final, updating)
    shutil.rmtree(final)

    store.initialize()

    assert store.read_record(tool_id)["name"] == "Feather"
    assert not backup.exists()
    assert not updating.exists()


def test_initialize_defers_recovery_while_the_write_fence_is_active(tmp_path, monkeypatch):
    root = tmp_path / "avatar_tools"
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(root))
    created = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    tool_id = created["id"]
    final = root / tool_id
    backup = root / f".{tool_id}.backup"
    updating = root / f".{tool_id}.updating"
    shutil.copytree(final, backup)
    shutil.copytree(final, updating)
    shutil.rmtree(final)

    def reject_recovery(*_args, **_kwargs):
        raise MaintenanceModeError("maintenance_readonly", operation="recover", target="avatar_tools")

    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", reject_recovery)
    store.initialize()

    assert not final.exists()
    assert backup.is_dir()
    assert updating.is_dir()

    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    assert [item["id"] for item in store.list_items()] == [tool_id]
    assert final.is_dir()
    assert not backup.exists()
    assert not updating.exists()


@pytest.mark.parametrize("read_operation", ("list", "record"))
def test_deferred_recovery_does_not_create_a_missing_root_while_fenced(
    tmp_path, monkeypatch, read_operation
):
    root = tmp_path / "avatar_tools"
    store = AvatarToolStore(_ConfigManager(root))

    def reject_recovery(*_args, **_kwargs):
        raise MaintenanceModeError("maintenance_readonly", operation="recover", target="avatar_tools")

    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", reject_recovery)
    store.initialize()
    assert not root.exists()

    with pytest.raises(MaintenanceModeError):
        if read_operation == "list":
            store.list_items()
        else:
            store.read_record("local-12345678-1234-4123-8123-123456789abc")

    assert not root.exists()


@pytest.mark.parametrize("first_operation", ("list", "detail", "delete"))
@pytest.mark.parametrize("valid_final", (False, True))
def test_first_available_request_retries_a_failed_startup_recovery(
    tmp_path, monkeypatch, first_operation, valid_final
):
    fence_calls = []
    monkeypatch.setattr(
        "utils.avatar_tool_store.assert_cloudsave_writable",
        lambda *_a, **kwargs: fence_calls.append(kwargs),
    )
    root = tmp_path / "avatar_tools"
    store = AvatarToolStore(_ConfigManager(root))
    created = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    tool_id = created["id"]
    final = root / tool_id
    backup = root / f".{tool_id}.backup"
    shutil.copytree(final, backup)
    if not valid_final:
        shutil.rmtree(final)

    class FlakyConfigManager(_ConfigManager):
        available = False

        def ensure_avatar_tools_directory(self):
            if not self.available:
                return False
            return super().ensure_avatar_tools_directory()

    config_manager = FlakyConfigManager(root)
    recovering_store = AvatarToolStore(config_manager)
    with pytest.raises(AvatarToolStoreError) as raised:
        recovering_store.initialize()
    assert raised.value.code == "avatar_tools_directory_unavailable"

    fence_calls.clear()
    config_manager.available = True
    if first_operation == "list":
        assert [item["id"] for item in recovering_store.list_items()] == [tool_id]
        assert final.is_dir()
    elif first_operation == "detail":
        assert recovering_store.get_detail(tool_id)["id"] == tool_id
        assert final.is_dir()
    else:
        assert recovering_store.delete_tool(tool_id) == tool_id
        assert not final.exists()
    assert not backup.exists()
    assert any(call.get("operation") == "recover" for call in fence_calls)


def test_delete_cleans_a_stale_update_backup_without_resurrection(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    created = _create_tool(
        store,
        name="Before",
        change_mode="press-swap",
        change_meanings=["before"],
        default_image=_png(),
        change_images=[_png()],
    )
    tool_id = created["id"]
    backup = store.root / f".{tool_id}.backup"
    real_rmtree = shutil.rmtree

    def leave_update_backup(path, *args, **kwargs):
        if Path(path) == backup:
            raise OSError("backup busy")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("utils.avatar_tool_store.shutil.rmtree", leave_update_backup)
    store.update_tool(
        tool_id,
        base_revision=store.get_detail(tool_id)["revision"],
        name="After",
        change_mode="press-swap",
        change_meanings=["after"],
        default_resource="default.png",
        default_image=None,
        change_resources=["change-000.png"],
        change_images=[],
    )
    assert backup.is_dir()

    monkeypatch.setattr("utils.avatar_tool_store.shutil.rmtree", real_rmtree)
    store.delete_tool(tool_id)
    assert not backup.exists()
    store.initialize()
    assert not (store.root / tool_id).exists()
    assert store.list_items() == []


def test_stale_update_backup_counts_toward_the_total_storage_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    created = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    final = store.root / created["id"]
    backup = store.root / f".{created['id']}.backup"
    shutil.copytree(final, backup)
    directory_size = store._directory_bytes(final)
    store.limits["maxTotalBytes"] = directory_size * 2

    assert store._current_storage_bytes() == directory_size * 2
    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store,
            name="Feather",
            change_mode="press-swap",
            change_meanings=["meaning"],
            default_image=_png(),
            change_images=[_png()],
        )

    assert raised.value.code == "storage_limit_reached"


@pytest.mark.parametrize("operation", ("create", "update"))
def test_failed_staging_cleanup_blocks_more_mutations_until_recovery(
    tmp_path, monkeypatch, operation
):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    created = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["before"],
        default_image=_png(),
        change_images=[_png()],
    )
    staged_tool_id = (
        "local-12345678-1234-4123-8123-123456789abc"
        if operation == "create"
        else created["id"]
    )
    suffix = "uploading" if operation == "create" else "updating"
    staging = store.root / f".{staged_tool_id}.{suffix}"
    real_rmtree = shutil.rmtree

    def reject_staging_cleanup(path, *args, **kwargs):
        if Path(path) == staging:
            raise OSError("staging directory is busy")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("utils.avatar_tool_store.shutil.rmtree", reject_staging_cleanup)
    monkeypatch.setattr(store, "_current_storage_bytes", lambda: store.limits["maxTotalBytes"])

    with pytest.raises(AvatarToolStoreError) as failed_mutation:
        if operation == "create":
            _create_tool(
                store,
                tool_id=staged_tool_id,
                name="Blocked create",
                change_mode="press-swap",
                change_meanings=["blocked"],
                default_image=_png(),
                change_images=[_png()],
            )
        else:
            store.update_tool(
                created["id"],
                base_revision=store.get_detail(created["id"])["revision"],
                name="Blocked update",
                change_mode="press-swap",
                change_meanings=["blocked"],
                default_resource="default.png",
                default_image=None,
                change_resources=["change-000.png"],
                change_images=[],
            )

    assert failed_mutation.value.code == "storage_limit_reached"
    assert staging.is_dir()

    with pytest.raises(AvatarToolStoreError) as blocked_create:
        _create_tool(
            store,
            name="Wait for recovery",
            change_mode="press-swap",
            change_meanings=["wait"],
            default_image=_png(),
            change_images=[_png()],
        )

    assert blocked_create.value.code == "avatar_tools_directory_unavailable"
    monkeypatch.setattr("utils.avatar_tool_store.shutil.rmtree", real_rmtree)
    assert [item["id"] for item in store.list_items()] == [created["id"]]
    assert not staging.exists()


def test_failed_update_rollback_blocks_create_until_backup_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    created = _create_tool(
        store,
        name="Before",
        change_mode="press-swap",
        change_meanings=["before"],
        default_image=_png(),
        change_images=[_png()],
    )
    tool_id = created["id"]
    final = store.root / tool_id
    updating = store.root / f".{tool_id}.updating"
    backup = store.root / f".{tool_id}.backup"
    base_revision = store.get_detail(tool_id)["revision"]
    real_replace = os.replace
    publish_error = OSError("publish interrupted")
    rollback_error = OSError("rollback interrupted")

    def interrupt_publish_and_rollback(source, destination, *args, **kwargs):
        source_path = Path(source)
        destination_path = Path(destination)
        if source_path == updating and destination_path == final:
            raise publish_error
        if source_path == backup and destination_path == final:
            raise rollback_error
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", interrupt_publish_and_rollback)
    with pytest.raises(OSError) as failed_update:
        store.update_tool(
            tool_id,
            base_revision=base_revision,
            name="After",
            change_mode="press-swap",
            change_meanings=["after"],
            default_resource="default.png",
            default_image=None,
            change_resources=["change-000.png"],
            change_images=[],
        )

    assert failed_update.value is rollback_error
    assert failed_update.value.__context__ is publish_error
    assert not final.exists()
    assert not updating.exists()
    assert backup.is_dir()

    with pytest.raises(AvatarToolStoreError) as blocked_create:
        _create_tool(
            store,
            name="Wait for rollback",
            change_mode="press-swap",
            change_meanings=["wait"],
            default_image=_png(),
            change_images=[_png()],
        )

    assert blocked_create.value.code == "avatar_tools_directory_unavailable"
    monkeypatch.setattr("utils.avatar_tool_store.os.replace", real_replace)
    assert [item["name"] for item in store.list_items()] == ["Before"]
    assert final.is_dir()
    assert not backup.exists()


def test_initialize_replaces_a_corrupt_final_directory_with_a_valid_backup(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    created = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    tool_id = created["id"]
    final = store.root / tool_id
    backup = store.root / f".{tool_id}.backup"
    updating = store.root / f".{tool_id}.updating"
    shutil.copytree(final, backup)
    shutil.copytree(final, updating)
    (final / "record.json").write_bytes(b"\xff\xfe")

    store.initialize()

    assert store.read_record(tool_id)["name"] == "Feather"
    assert not backup.exists()
    assert not updating.exists()


def test_failed_recovery_cleanup_stays_pending_until_the_directory_can_be_removed(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    created = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    updating = store.root / f".{created['id']}.updating"
    shutil.copytree(store.root / created["id"], updating)
    real_rmtree = shutil.rmtree

    def reject_updating_cleanup(path, *args, **kwargs):
        if Path(path) == updating:
            raise OSError("updating directory is busy")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("utils.avatar_tool_store.shutil.rmtree", reject_updating_cleanup)
    with pytest.raises(AvatarToolStoreError) as raised:
        store.initialize()

    assert raised.value.code == "avatar_tools_directory_unavailable"
    assert updating.is_dir()

    monkeypatch.setattr("utils.avatar_tool_store.shutil.rmtree", real_rmtree)
    assert [item["id"] for item in store.list_items()] == [created["id"]]
    assert not updating.exists()


def test_list_waits_for_update_publication_instead_of_observing_an_empty_gap(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    created = _create_tool(
        store,
        name="Before",
        change_mode="press-swap",
        change_meanings=["before"],
        default_image=_png(),
        change_images=[_png()],
    )
    tool_id = created["id"]
    final = store.root / tool_id
    backup = store.root / f".{tool_id}.backup"
    base_revision = store.get_detail(tool_id)["revision"]
    backup_published = threading.Event()
    release_update = threading.Event()
    reader_done = threading.Event()
    update_errors = []
    reader_errors = []
    listed_items = []
    real_replace = os.replace

    def paused_replace(source, destination):
        real_replace(source, destination)
        if Path(source) == final and Path(destination) == backup:
            backup_published.set()
            release_update.wait()

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", paused_replace)

    def update():
        try:
            store.update_tool(
                tool_id,
                base_revision=base_revision,
                name="After",
                change_mode="press-swap",
                change_meanings=["after"],
                default_resource="default.png",
                default_image=None,
                change_resources=["change-000.png"],
                change_images=[],
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            update_errors.append(exc)

    def read_list():
        try:
            listed_items.extend(store.list_items())
        except BaseException as exc:  # pragma: no cover - asserted below
            reader_errors.append(exc)
        finally:
            reader_done.set()

    update_thread = threading.Thread(target=update, daemon=True)
    reader_thread = threading.Thread(target=read_list, daemon=True)
    update_thread.start()
    assert backup_published.wait(5)
    reader_thread.start()
    try:
        assert not reader_done.wait(0.1)
    finally:
        release_update.set()
    update_thread.join(5)
    reader_thread.join(5)

    assert not update_thread.is_alive()
    assert not reader_thread.is_alive()
    assert update_errors == []
    assert reader_errors == []
    assert [item["name"] for item in listed_items] == ["After"]


def test_detail_and_revision_are_from_one_snapshot_during_update(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    created = _create_tool(
        store,
        name="Before",
        change_mode="press-swap",
        change_meanings=["before"],
        default_image=_png(),
        change_images=[_png()],
    )
    tool_id = created["id"]
    before = store.get_detail(tool_id)
    record_read = threading.Event()
    release_detail = threading.Event()
    update_done = threading.Event()
    detail_errors = []
    update_errors = []
    details = []
    original_read_record = store.read_record

    def paused_read_record(requested_tool_id, **kwargs):
        record = original_read_record(requested_tool_id, **kwargs)
        if threading.current_thread().name == "detail-reader":
            record_read.set()
            release_detail.wait()
        return record

    monkeypatch.setattr(store, "read_record", paused_read_record)

    def read_detail():
        try:
            details.append(store.get_detail(tool_id))
        except BaseException as exc:  # pragma: no cover - asserted below
            detail_errors.append(exc)

    def update():
        try:
            store.update_tool(
                tool_id,
                base_revision=before["revision"],
                name="After",
                change_mode="press-swap",
                change_meanings=["after"],
                default_resource="default.png",
                default_image=None,
                change_resources=["change-000.png"],
                change_images=[],
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            update_errors.append(exc)
        finally:
            update_done.set()

    detail_thread = threading.Thread(target=read_detail, name="detail-reader", daemon=True)
    update_thread = threading.Thread(target=update, daemon=True)
    detail_thread.start()
    assert record_read.wait(5)
    update_thread.start()
    try:
        assert not update_done.wait(0.1)
    finally:
        release_detail.set()
    detail_thread.join(5)
    update_thread.join(5)

    assert not detail_thread.is_alive()
    assert not update_thread.is_alive()
    assert detail_errors == []
    assert update_errors == []
    assert details[0]["name"] == "Before"
    assert details[0]["changeItems"][0]["meaning"] == "before"
    assert details[0]["revision"] == before["revision"]
    assert store.get_detail(tool_id)["name"] == "After"


@pytest.mark.unit
def test_delete_cleanup_failure_registers_recovery_and_self_heals_without_restart(tmp_path, monkeypatch):
    """A leaked .deleting directory still counts against the storage budget."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    deleted = _create_tool(
        store,
        name="Deleted",
        change_mode="press-swap",
        change_meanings=["deleted"],
        default_image=_png(),
        change_images=[_png()],
    )
    retained = _create_tool(
        store,
        name="Retained",
        change_mode="press-swap",
        change_meanings=["retained"],
        default_image=_png(),
        change_images=[_png()],
    )
    deleting = store.root / f".{deleted['id']}.deleting"
    retained_bytes = store._directory_bytes(store.root / retained["id"])
    real_rmtree = shutil.rmtree

    def refuse_cleanup(path, *args, **kwargs):
        if Path(path).resolve(strict=False) == deleting.resolve(strict=False):
            raise OSError("cleanup refused")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("utils.avatar_tool_store.shutil.rmtree", refuse_cleanup)
    try:
        assert store.delete_tool(deleted["id"]) == deleted["id"]
        assert deleting.is_dir()
        # Leftovers still consume budget, so register recovery or their bytes remain unavailable in this process.
        assert store._current_storage_bytes() > retained_bytes
        assert store._root_key() in avatar_tool_store._RECOVERY_PENDING_ROOTS

        # Without restart or explicit initialize(), the next ordinary call should remove leftovers.
        monkeypatch.setattr("utils.avatar_tool_store.shutil.rmtree", real_rmtree)
        assert [item["id"] for item in store.list_items()] == [retained["id"]]
        assert not deleting.exists()
        assert store._current_storage_bytes() == retained_bytes
        assert store._root_key() not in avatar_tool_store._RECOVERY_PENDING_ROOTS
    finally:
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())


@pytest.mark.unit
def test_neither_list_nor_startup_rehashes_but_consumers_do(tmp_path, monkeypatch):
    """Focus-path list and cold start must both stay O(tools), not O(bytes)."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    created = [
        _create_tool(
            store,
            name=f"Tool {index}",
            change_mode="press-swap",
            change_meanings=["meaning"],
            default_image=_png(),
            change_images=[_png()],
        )
        for index in range(3)
    ]

    digests = []
    real_digest = AvatarToolStore._file_digest

    def counting_digest(path, maximum):
        digests.append(str(path))
        return real_digest(path, maximum)

    monkeypatch.setattr(AvatarToolStore, "_file_digest", staticmethod(counting_digest))
    try:
        assert len(store.list_items()) == 3
        assert digests == [], "list_items must not recompute resource digests"

        # Startup also skips full verification: the original startup hashes no files; restoring hashing would impose
        # O(total bytes) on every cold start.
        store.initialize()
        assert digests == [], "startup must not recompute resource digests"

        # Only actual resource consumers verify byte by byte.
        store.get_detail(created[0]["id"])
        assert digests, "get_detail must verify resource digests"
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(store._root_key(), None)

@pytest.mark.unit
def test_transient_read_failure_spares_the_tool_but_a_digest_mismatch_quarantines(tmp_path, monkeypatch):
    """Only proven corruption may hide a tool; a locked file must not."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    root_key = store._root_key()

    def locked_digest(path, maximum):
        raise OSError("file locked by another process")

    try:
        # The file is intact but unreadable this time: one antivirus scan must not permanently hide a good tool.
        monkeypatch.setattr(AvatarToolStore, "_file_digest", staticmethod(locked_digest))
        with pytest.raises(AvatarToolStoreError) as raised:
            store.get_detail(tool["id"])
        assert raised.value.integrity_mismatch is False
        assert tool["id"] not in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())
        monkeypatch.undo()
        monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
        assert [item["id"] for item in store.list_items()] == [tool["id"]]

        # Bytes were read but mismatch the digest: this is deterministic corruption.
        (store.root / tool["id"] / "default.png").write_bytes(b"truncated")
        with pytest.raises(AvatarToolStoreError) as raised:
            store.get_detail(tool["id"])
        assert raised.value.integrity_mismatch is True
        assert tool["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS[root_key]
        assert store.list_items() == []

        # Corrupt tools cannot be updated (update fully verifies), only deleted; deletion must clear quarantine.
        assert store.delete_tool(tool["id"]) == tool["id"]
        assert tool["id"] not in avatar_tool_store._QUARANTINED_TOOL_IDS[root_key]
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)

@pytest.mark.unit
def test_update_rejects_retained_bytes_swapped_after_the_record_was_verified(tmp_path, monkeypatch):
    """A retained resource must match the digest, not merely exist."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png(size=(9, 9))],
    )

    real_read_record = AvatarToolStore.read_record

    def swap_after_verification(self, tool_id, *, verify_resources=False):
        record = real_read_record(self, tool_id, verify_resources=verify_resources)
        if verify_resources:
            # After validation but before retained_bytes opens the file, an external writer replaces its contents.
            (self.root / tool_id / "default.png").write_bytes(_png(size=(31, 31)))
        return record

    monkeypatch.setattr(AvatarToolStore, "read_record", swap_after_verification)

    with pytest.raises(AvatarToolStoreError) as raised:
        store.update_tool(
            tool["id"],
            base_revision=tool["revision"],
            name="Feather",
            change_mode="press-swap",
            change_meanings=["meaning"],
            default_resource="default.png",
            default_image=None,
            change_resources=["change-000.png"],
            change_images=[],
        )
    assert raised.value.code == "resource_reference_invalid"
    assert raised.value.field == "default_image"


@pytest.mark.unit
def test_update_maps_a_retained_read_failure_to_a_controlled_error(tmp_path, monkeypatch):
    """A locked retained file must not escape as a bare OSError."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png(size=(9, 9))],
    )

    # Fail only retained_bytes's open: update_tool begins with
    # read_record(verify_resources=True), which opens the same file to hash it and must pass first.
    stage = {"verified": False}
    real_read_record = AvatarToolStore.read_record

    def marking_read_record(self, tool_id, *, verify_resources=False):
        record = real_read_record(self, tool_id, verify_resources=verify_resources)
        if verify_resources:
            stage["verified"] = True
        return record

    real_open = Path.open

    def locked_open(self, *args, **kwargs):
        if stage["verified"] and self.name == "default.png":
            raise OSError("file locked by another process")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(AvatarToolStore, "read_record", marking_read_record)
    monkeypatch.setattr(Path, "open", locked_open)
    root_key = store._root_key()
    try:
        with pytest.raises(AvatarToolStoreError) as raised:
            store.update_tool(
                tool["id"],
                base_revision=tool["revision"],
                name="Feather",
                change_mode="press-swap",
                change_meanings=["meaning"],
                default_resource="default.png",
                default_image=None,
                change_resources=["change-000.png"],
                change_images=[],
            )
        # The route handles only AvatarToolStoreError / MaintenanceModeError; bare OSError becomes 500.
        assert raised.value.code == "resource_read_failed"
        assert raised.value.status_code == 503
        assert raised.value.field == "default_image"
        # Unreadable does not mean corrupt; do not quarantine.
        assert raised.value.integrity_mismatch is False
        assert tool["id"] not in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)


@pytest.mark.unit
def test_record_read_is_bounded_yet_fits_the_largest_legal_record(tmp_path, monkeypatch):
    """A damaged multi-GB record must not be pulled into memory on every focus."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    cap = avatar_tool_store.AVATAR_TOOL_MAX_RECORD_BYTES

    # Derive the cap from limits rather than guess: increasing maxChangeImages /
    # maxMeaningChars / maxNameChars without adjusting the cap must fail here first.
    worst_case = {
        "recordVersion": 2,
        "id": "local-00000000-0000-4000-8000-000000000000",
        "name": "羽" * store.limits["maxNameChars"],
        "defaultImage": "default.png",
        "imageChange": {
            "mode": "click-advance",
            "items": [
                {"image": f"change-{index:03d}.png", "meaning": "描" * store.limits["maxMeaningChars"]}
                for index in range(store.limits["maxChangeImages"])
            ],
        },
        "interaction": {
            "normalSound": "normal.mp3",
            "special": {
                "probability": 0.1,
                "image": "special.png",
                "meaning": "彩" * store.limits["maxMeaningChars"],
                "sound": "special.mp3",
            },
        },
        "resourceDigests": {
            name: "a" * 64
            for name in ["default.png", "normal.mp3", "special.png", "special.mp3"]
            + [f"change-{index:03d}.png" for index in range(store.limits["maxChangeImages"])]
        },
    }
    encoded = json.dumps(worst_case, ensure_ascii=False, indent=2).encode("utf-8")
    assert len(encoded) < cap, f"cap {cap} leaves no room for a legal record of {len(encoded)} bytes"

    tool = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    record_path = store.root / tool["id"] / "record.json"
    # Append abundant whitespace to a valid record: JSON and schema remain valid, so only
    # bounded reading rejects it, distinguishing size rejection from structural rejection.
    record_path.write_bytes(
        record_path.read_bytes() + b" " * (cap * 2)
    )

    try:
        with pytest.raises(AvatarToolStoreError) as raised:
            store.read_record(tool["id"])
    finally:
        pass
    assert raised.value.code == "record_invalid"
    # Oversize is deterministically invalid, not transiently unreadable, so quarantine it; otherwise it is absent from
    # the public catalog and has no deletion UI while still occupying a slot and quota.
    assert raised.value.transient is False
    assert tool["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS[store._root_key()]
    avatar_tool_store._QUARANTINED_TOOL_IDS.pop(store._root_key(), None)


@pytest.mark.unit
def test_update_rejects_a_retained_resource_that_outgrew_its_limit(tmp_path, monkeypatch):
    """An externally swapped-in giant file must be refused before it is read."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png(size=(9, 9))],
    )
    stored = (store.root / tool["id"] / "default.png").stat().st_size

    # retained_bytes preflight is the second defense: _file_digest already has its own size preflight during
    # update_tool's initial full validation, so only resources becoming oversized after verification
    # can reach this TOCTOU path. Tighten the limit to simulate that moment without writing 8 MiB.
    real_read_record = AvatarToolStore.read_record

    def shrink_after_verification(self, tool_id, *, verify_resources=False):
        record = real_read_record(self, tool_id, verify_resources=verify_resources)
        if verify_resources:
            self.limits["maxImageBytes"] = stored - 1
        return record

    monkeypatch.setattr(AvatarToolStore, "read_record", shrink_after_verification)

    with pytest.raises(AvatarToolStoreError) as raised:
        store.update_tool(
            tool["id"],
            base_revision=tool["revision"],
            name="Feather",
            change_mode="press-swap",
            change_meanings=["meaning"],
            default_resource="default.png",
            default_image=None,
            change_resources=["change-000.png"],
            change_images=[],
        )
    assert raised.value.code == "resource_reference_invalid"
    assert raised.value.field == "default_image"
    assert raised.value.integrity_mismatch is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("resource", "limit"),
    (("image-000.png", "maxImageBytes"), ("normal.mp3", "maxAudioBytes")),
)
def test_list_quarantines_oversized_resources_without_hashing(tmp_path, monkeypatch, resource, limit):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    manifest = _v3_manifest(tool_id)
    manifest["interaction"] = {"normalSound": {"kind": "upload", "index": 1}}
    store.create_tool_v3(manifest=manifest, uploads=[_png(), _mp3()])
    asset = store.root / tool_id / resource
    with asset.open("r+b") as stream:
        stream.truncate(store.limits[limit] + 1)

    def unexpected_digest(*_args, **_kwargs):
        pytest.fail("listing must reject oversized resources using metadata alone")

    monkeypatch.setattr(AvatarToolStore, "_file_digest", staticmethod(unexpected_digest))

    assert store.list_items() == []
    assert tool_id in avatar_tool_store._QUARANTINED_TOOL_IDS.get(store._root_key(), set())
    assert store._occupied_tool_slots() == 0
    assert store._current_storage_bytes() == 0
    assert asset.stat().st_size == store.limits[limit] + 1


@pytest.mark.unit
def test_verification_refuses_an_oversized_resource_before_hashing_it(tmp_path, monkeypatch):
    """Hashing runs under _STORE_LOCK, so a swapped-in giant must be refused first."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png(size=(9, 9))],
    )
    asset = store.root / tool["id"] / "default.png"
    stored = asset.stat().st_size
    consumed = {"bytes": 0}
    real_open = Path.open

    def counting_open(self, *args, **kwargs):
        stream = real_open(self, *args, **kwargs)
        if self.name == "default.png" and "b" in (args[0] if args else kwargs.get("mode", "r")):
            real_read = stream.read

            def counting_read(size=-1):
                chunk = real_read(size)
                consumed["bytes"] += len(chunk)
                return chunk

            stream.read = counting_read
        return stream

    store.limits["maxImageBytes"] = stored - 1
    monkeypatch.setattr(Path, "open", counting_open)
    root_key = store._root_key()
    try:
        with pytest.raises(AvatarToolStoreError) as raised:
            store.read_record(tool["id"], verify_resources=True)
        assert raised.value.code == "record_invalid"
        # Not a single byte should be read.
        assert consumed["bytes"] == 0, f"hashed {consumed['bytes']} bytes of an oversized asset"
        # Oversize is a deterministic content violation and must be quarantined.
        assert raised.value.integrity_mismatch is True
        assert tool["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS[root_key]
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)


@pytest.mark.unit
@pytest.mark.parametrize("kind", ("directory", "symlink"))
def test_record_rejects_non_file_entries_in_the_tool_directory(tmp_path, monkeypatch, kind):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    directory = store.root / tool["id"]
    assert store.read_record(tool["id"])["id"] == tool["id"]

    intruder = directory / "intruder"
    if kind == "directory":
        intruder.mkdir()
    else:
        outside = tmp_path / "outside.png"
        outside.write_bytes(_png())
        try:
            intruder.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation requires privileges on this platform")

    # The closure previously counted only regular files, silently ignoring added subdirectories/symlinks.
    with pytest.raises(AvatarToolStoreError) as raised:
        store.read_record(tool["id"])
    assert raised.value.code == "record_invalid"


@pytest.mark.unit
def test_digest_stops_reading_when_a_file_grows_past_the_fstat_snapshot(tmp_path, monkeypatch):
    """fstat is only a snapshot; an appending writer must not make the read unbounded."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png(size=(9, 9))],
    )
    asset = store.root / tool["id"] / "default.png"
    asset.write_bytes(b"x" * (6 * 1024 * 1024))
    store.limits["maxImageBytes"] = 1024

    class _SmallSnapshot:
        st_size = 0

    # Lie about the snapshot, equivalent to an external writer appending after fstat.
    monkeypatch.setattr(avatar_tool_store.os, "fstat", lambda _fd: _SmallSnapshot())

    consumed = {"bytes": 0}
    real_open = Path.open

    def counting_open(self, *args, **kwargs):
        stream = real_open(self, *args, **kwargs)
        if self.name == "default.png":
            real_read = stream.read

            def counting_read(size=-1):
                chunk = real_read(size)
                consumed["bytes"] += len(chunk)
                return chunk

            stream.read = counting_read
        return stream

    monkeypatch.setattr(Path, "open", counting_open)
    root_key = store._root_key()
    try:
        with pytest.raises(AvatarToolStoreError) as raised:
            store.read_record(tool["id"], verify_resources=True)
        assert raised.value.integrity_mismatch is True
        # Stop reading immediately after exceeding the cap, rather than continuing to EOF at 6 MiB.
        assert consumed["bytes"] <= 1024 * 1024 + store.limits["maxImageBytes"], consumed["bytes"]
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)


@pytest.mark.unit
def test_a_temporarily_unreadable_tool_still_holds_its_slot(tmp_path, monkeypatch):
    """A locked record fails refresh and still keeps its storage slot."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    store.limits["maxTools"] = 1
    existing = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )

    real_open = Path.open

    def locked_record(self, *args, **kwargs):
        if self.name == "record.json" and existing["id"] in self.parts:
            raise OSError("record.json is locked by another process")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", locked_record)
    # Transient unreadability must not yield an incomplete successful listing, or the frontend mistakes unreadable items
    # for deleted ones. Fail the entire refresh so the frontend retains its previous authoritative snapshot.
    with pytest.raises(AvatarToolStoreError) as list_error:
        store.list_items()
    assert list_error.value.transient is True
    # It remains on disk and must still occupy a slot, preventing silent limit bypass.
    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store,
            name="Second",
            change_mode="press-swap",
            change_meanings=["meaning"],
            default_image=_png(),
            change_images=[_png()],
        )
    assert raised.value.code == "tool_limit_reached"


@pytest.mark.unit
def test_a_provably_corrupt_record_does_not_hold_a_slot(tmp_path, monkeypatch):
    """The dual of the above: a record proven invalid must free its slot."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    store.ensure()
    store.limits["maxTools"] = 1
    corrupt = store.root / "local-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    corrupt.mkdir()
    (corrupt / "record.json").write_bytes(b"not-json")

    created = _create_tool(
        store,
        name="Visible",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    assert [item["id"] for item in store.list_items()] == [created["id"]]


@pytest.mark.unit
def test_recovery_never_lets_a_stale_backup_overwrite_an_unreadable_final(tmp_path, monkeypatch):
    """A transiently unreadable final must not be replaced by the old backup."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Updated",
        change_mode="press-swap",
        change_meanings=["the version the user just saved"],
        default_image=_png(size=(12, 12)),
        change_images=[_png(size=(13, 13))],
    )
    final = store.root / tool["id"]
    current_revision = store.record_revision(store.read_record(tool["id"]))

    # The previous update published a new final but failed to remove backup, leaving the old version.
    backup = store.root / f".{tool['id']}.backup"
    shutil.copytree(final, backup)
    (backup / "record.json").write_text(
        (backup / "record.json").read_text(encoding="utf-8").replace(
            "the version the user just saved", "the stale backup"
        ),
        encoding="utf-8",
    )
    # Make backup self-consistent with matching digests, or it would be corrupt independently of this case.
    stale = json.loads((backup / "record.json").read_text(encoding="utf-8"))
    stale["resourceDigests"] = {
        name: AvatarToolStore._file_digest(backup / name, 32 * 1024 * 1024)
        for name in stale["resourceDigests"]
    }
    (backup / "record.json").write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")

    real_open = Path.open

    def unreadable_final(self, *args, **kwargs):
        if self.name == "record.json" and str(final) in str(self):
            raise OSError("final record is locked by another process")
        return real_open(self, *args, **kwargs)

    root_key = store._root_key()
    monkeypatch.setattr(Path, "open", unreadable_final)
    try:
        store.initialize()
        # Preserve final unchanged; the old backup must never replace it.
        assert final.is_dir()
        assert backup.is_dir(), "the backup was consumed despite an unproven final"
        # Recovery is incomplete; the store root must remain pending recovery.
        assert root_key in avatar_tool_store._RECOVERY_PENDING_ROOTS

        monkeypatch.undo()
        monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
        store.initialize()

        # Only after the lock releases and final proves valid may leftover backup be removed.
        assert not backup.exists()
        assert store.record_revision(store.read_record(tool["id"])) == current_revision
        assert store.get_detail(tool["id"])["changeItems"][0]["meaning"] == (
            "the version the user just saved"
        )
    finally:
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)


@pytest.mark.unit
def test_a_quarantined_tool_stops_holding_its_slot(tmp_path, monkeypatch):
    """Quarantine means proven corruption, so it must free the slot like bad JSON."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    store.limits["maxTools"] = 1
    damaged = _create_tool(
        store,
        name="Damaged",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    root_key = store._root_key()
    try:
        # All slots are occupied, preventing creation of a second tool.
        with pytest.raises(AvatarToolStoreError) as raised:
            _create_tool(
                store, name="Second", change_mode="press-swap", change_meanings=["m"],
                default_image=_png(), change_images=[_png()],
            )
        assert raised.value.code == "tool_limit_reached"

        # Content was tampered with; consumer verification disproves validity and quarantines it.
        (store.root / damaged["id"] / "default.png").write_bytes(b"truncated")
        with pytest.raises(AvatarToolStoreError):
            store.get_detail(damaged["id"])
        assert damaged["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS[root_key]

        # Once disproven, it must no longer occupy a slot.
        replacement = _create_tool(
            store, name="Second", change_mode="press-swap", change_meanings=["m"],
            default_image=_png(), change_images=[_png()],
        )
        assert replacement["name"] == "Second"
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)


@pytest.mark.unit
def test_recovery_defers_when_a_resource_file_is_unreadable_not_just_the_record(tmp_path, monkeypatch):
    """The dual of the record.json case: an unreadable asset must not condemn final."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Updated",
        change_mode="press-swap",
        change_meanings=["the version the user just saved"],
        default_image=_png(size=(12, 12)),
        change_images=[_png(size=(13, 13))],
    )
    final = store.root / tool["id"]
    backup = store.root / f".{tool['id']}.backup"
    shutil.copytree(final, backup)

    real_open = Path.open

    def unreadable_asset(self, *args, **kwargs):
        # Block the resource file this time, not record.json.
        if self.name == "default.png" and str(final) in str(self):
            raise OSError("asset is locked by another process")
        return real_open(self, *args, **kwargs)

    root_key = store._root_key()
    monkeypatch.setattr(Path, "open", unreadable_asset)
    try:
        store.initialize()
        assert backup.is_dir(), "the backup was consumed despite an unproven final"
        assert root_key in avatar_tool_store._RECOVERY_PENDING_ROOTS

        monkeypatch.undo()
        monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
        store.initialize()

        assert not backup.exists()
        assert store.get_detail(tool["id"])["changeItems"][0]["meaning"] == (
            "the version the user just saved"
        )
    finally:
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)


@pytest.mark.unit
def test_recovery_removes_a_provably_invalid_backup_so_it_stops_eating_the_quota(tmp_path, monkeypatch):
    """A condemned backup is invisible and undeletable in the UI; recovery owns it."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    store.ensure()
    tool_id = "local-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    # Interrupted update left no valid final, and .backup itself is deterministically corrupt.
    backup = store.root / f".{tool_id}.backup"
    backup.mkdir()
    (backup / "record.json").write_bytes(b"not-json")
    (backup / "default.png").write_bytes(b"x" * 4096)
    assert store._current_storage_bytes() >= 4096

    root_key = store._root_key()
    try:
        store.initialize()
        assert not backup.exists(), "a condemned backup kept occupying the quota"
        assert store._current_storage_bytes() == 0
        assert root_key not in avatar_tool_store._RECOVERY_PENDING_ROOTS
    finally:
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)


@pytest.mark.unit
def test_a_condemned_tool_stops_eating_the_storage_quota(tmp_path, monkeypatch):
    """A proven-corrupt tool has no UI delete path, so it must not hold quota."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Damaged",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    root_key = store._root_key()
    occupied = store._current_storage_bytes()
    assert occupied > 0

    (store.root / tool["id"] / "default.png").write_bytes(b"truncated")
    try:
        with pytest.raises(AvatarToolStoreError):
            store.get_detail(tool["id"])
        assert tool["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS[root_key]
        # Users cannot see it or enter its editor, so it must no longer consume quota.
        assert store._current_storage_bytes() == 0
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)


@pytest.mark.unit
def test_recovery_quarantines_a_condemned_final_without_deleting_it(tmp_path, monkeypatch):
    """Closure violations count as condemned; the user's files must survive."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Damaged",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    final = store.root / tool["id"]
    # Evidence of interrupted update makes recovery visit this tool.
    (store.root / f".{tool['id']}.updating").mkdir()
    # Broken closure: a user placed an extra file in the directory.
    (final / "notes.txt").write_bytes(b"something the user dropped in")

    root_key = store._root_key()
    try:
        store.initialize()
        assert tool["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS[root_key]
        # Crucially, the directory and user-added file remain: recovery does not decide deletion for the user.
        assert final.is_dir()
        assert (final / "notes.txt").is_file()
        assert (final / "default.png").is_file()
        # But it no longer consumes quota.
        assert store._current_storage_bytes() == 0
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)


@pytest.mark.unit
def test_a_stale_backup_never_rolls_back_a_condemned_but_present_final(tmp_path, monkeypatch):
    """Without .updating the backup is leftover, not a rollback target."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Latest",
        change_mode="press-swap",
        change_meanings=["the newest version"],
        default_image=_png(size=(12, 12)),
        change_images=[_png(size=(13, 13))],
    )
    final = store.root / tool["id"]
    # The previous update succeeded but backup cleanup failed, leaving an old version. Note the absence of .updating.
    backup = store.root / f".{tool['id']}.backup"
    shutil.copytree(final, backup)
    # A sync client added a file to the published directory, breaking closure and disproving final's validity.
    (final / "synced-note.txt").write_bytes(b"added by a sync client")

    root_key = store._root_key()
    try:
        store.initialize()
        # Preserve final unchanged: neither roll back to an old version nor delete the user's file with it.
        assert final.is_dir()
        assert (final / "synced-note.txt").is_file()
        assert (final / "default.png").is_file()
        assert tool["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS[root_key]
        # Remove leftover backup, or it consumes quota indefinitely with no deletion UI.
        assert not backup.exists()
        assert store._current_storage_bytes() == 0
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)


@pytest.mark.unit
def test_restoring_a_backup_clears_the_quarantine_it_set(tmp_path, monkeypatch):
    """An interrupted update that rolls back must not leave the tool quarantined."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Rolled back",
        change_mode="press-swap",
        change_meanings=["the version to restore"],
        default_image=_png(size=(12, 12)),
        change_images=[_png(size=(13, 13))],
    )
    final = store.root / tool["id"]
    backup = store.root / f".{tool['id']}.backup"
    shutil.copytree(final, backup)  # Preserve a valid copy before corruption.
    root_key = store._root_key()

    # First let consumer verification detect corruption and quarantine it: this is the marker recovery must clear.
    (final / "default.png").write_bytes(b"truncated")
    try:
        with pytest.raises(AvatarToolStoreError):
            store.get_detail(tool["id"])
        assert tool["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS[root_key]

        # Interruption evidence: the update never completed, so backup is the state to restore.
        (store.root / f".{tool['id']}.updating").mkdir()
        store.initialize()
        assert final.is_dir()
        assert not backup.exists()
        # The restored copy just passed full verification; it must not retain quarantine and remain hidden from listings.
        assert tool["id"] not in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())
        assert [item["id"] for item in store.list_items()] == [tool["id"]]
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)


# --- Exhaustive recovery state space ---
# Previously each review exposed one combination for a separate fix. Pin every final × backup ×
# .updating combination here to reveal remaining defects together rather than waiting for one-by-one reports.


def _condemn(directory):
    """Break the closure so the record is provably invalid (not merely unreadable)."""
    (directory / "intruder.txt").write_bytes(b"closure violation")


@pytest.mark.unit
@pytest.mark.parametrize("updating_present", (True, False))
@pytest.mark.parametrize("backup_state", ("valid", "condemned", "missing"))
@pytest.mark.parametrize("final_state", ("valid", "condemned", "missing"))
def test_recovery_state_matrix(tmp_path, monkeypatch, final_state, backup_state, updating_present):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Matrix",
        change_mode="press-swap",
        change_meanings=["published"],
        default_image=_png(size=(12, 12)),
        change_images=[_png(size=(13, 13))],
    )
    tool_id = tool["id"]
    final = store.root / tool_id
    backup = store.root / f".{tool_id}.backup"
    updating = store.root / f".{tool_id}.updating"
    root_key = store._root_key()

    if backup_state != "missing":
        shutil.copytree(final, backup)
        if backup_state == "condemned":
            _condemn(backup)
    if final_state == "condemned":
        _condemn(final)
    elif final_state == "missing":
        shutil.rmtree(final)
    if updating_present:
        updating.mkdir()

    # Recovery visits a tool only if .updating or .backup remains.
    visited = updating_present or backup_state != "missing"
    # Rollback is allowed exactly when no final is sacrificed or .updating proves interruption.
    may_restore = updating_present or final_state == "missing"
    restorable = backup_state == "valid" and may_restore

    try:
        store.initialize()

        assert not updating.exists(), "an interrupted staging directory was left behind"

        if final_state == "valid":
            # Never replace a valid published directory under any circumstances.
            assert final.is_dir()
            assert not (final / "intruder.txt").exists()
            assert [item["id"] for item in store.list_items()] == [tool_id]
            assert tool_id not in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())
        elif restorable:
            # Roll back only with no final to sacrifice or evidence of interruption; the restored
            # copy must be clean, without quarantine.
            assert final.is_dir()
            assert not (final / "intruder.txt").exists()
            assert tool_id not in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())
            assert [item["id"] for item in store.list_items()] == [tool_id]
        elif final_state == "condemned":
            # Invalidated but still on disk: preserve the user's files and only register quarantine.
            assert final.is_dir()
            assert (final / "intruder.txt").is_file(), "recovery destroyed the user's file"
            if visited:
                assert tool_id in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())
            assert store.list_items() == []
        else:
            assert not final.exists()
            assert store.list_items() == []

        if visited:
            # Remove every backup unused for rollback: it cannot enter the public catalog or be deleted through the UI.
            assert not backup.exists(), "an unusable backup kept occupying the quota"

        # Invisible, undeletable objects must not hold quota. Even without recovery entry markers
        # (.updating / .backup), list_items's lightweight closure check quarantines invalidated
        # tools, so quota is always zero here.
        visible = store.list_items()
        expected_bytes = store._directory_bytes(final) if visible else 0
        assert store._current_storage_bytes() == expected_bytes
        if final_state == "condemned" and not restorable:
            # Quarantine invalidated finals not rolled back; restored copies are already valid versions.
            assert tool_id in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)


@pytest.mark.unit
def test_a_missing_record_in_an_existing_tool_is_quarantined_and_frees_quota(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    directory = store.root / tool_id
    (directory / "record.json").unlink()

    with pytest.raises(AvatarToolStoreError) as raised:
        store.read_record(tool_id)

    assert raised.value.code == "record_invalid"
    assert raised.value.transient is False
    assert tool_id in avatar_tool_store._QUARANTINED_TOOL_IDS.get(store._root_key(), set())
    assert store.list_items() == []
    assert store._occupied_tool_slots() == 0
    assert store._current_storage_bytes() == 0
    assert (directory / "image-000.png").is_file()


@pytest.mark.unit
def test_a_missing_tool_is_not_quarantined(tmp_path, monkeypatch):
    """Only proven-invalid records are quarantined; absence is not invalidity."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    store.ensure()
    absent = "local-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    with pytest.raises(AvatarToolStoreError) as raised:
        store.read_record(absent, verify_resources=True)
    assert raised.value.code == "tool_not_found"
    assert absent not in avatar_tool_store._QUARANTINED_TOOL_IDS.get(store._root_key(), set())


@pytest.mark.unit
def test_a_plain_file_named_updating_does_not_authorize_a_rollback(tmp_path, monkeypatch):
    """Only a real staging directory proves an update was interrupted."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Latest",
        change_mode="press-swap",
        change_meanings=["the newest version"],
        default_image=_png(size=(12, 12)),
        change_images=[_png(size=(13, 13))],
    )
    final = store.root / tool["id"]
    backup = store.root / f".{tool['id']}.backup"
    shutil.copytree(final, backup)
    (final / "synced-note.txt").write_bytes(b"added by a sync client")
    # A regular file with the same name, not a staging directory.
    (store.root / f".{tool['id']}.updating").write_bytes(b"not a staging directory")

    root_key = store._root_key()
    store.initialize()

    # Do not use it to roll final back to an old version or delete user-added files.
    assert final.is_dir()
    assert (final / "synced-note.txt").is_file()
    assert tool["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())
    assert not backup.exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("name", "!!!"),
        ("name", "x" * 100),
        ("meaning", "x" * 500),
    ),
)
def test_a_field_level_record_failure_still_quarantines_and_frees_the_quota(
    tmp_path, monkeypatch, field, value
):
    """Persisted-record validation reuses form error codes; they are still proof."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    record_path = store.root / tool["id"] / "record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if field == "name":
        record["name"] = value
    else:
        record["imageChange"]["items"][0]["meaning"] = value
    record_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    root_key = store._root_key()
    try:
        with pytest.raises(AvatarToolStoreError) as raised:
            store.get_detail(tool["id"])
        # Normalize it so the quarantine criterion recognizes it.
        assert raised.value.code == "record_invalid"
        assert raised.value.transient is False
        assert tool["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS[root_key]
        assert store.list_items() == []
        # Invisible and undeletable in the UI, so release its quota.
        assert store._current_storage_bytes() == 0
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)


# --- Exhaustive quarantine-criterion input space ---
# Two recent invalidity edge cases involved broken closure and field error codes. Enumerate all persisted-record
# corruption shapes together: quarantine invalidated records and release quota; preserve unreadable ones unchanged.

def _corrupt_record(directory, mutate):
    path = directory / "record.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    mutate(record)
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


_PROVEN_INVALID = {
    "json-not-parseable": lambda d: (d / "record.json").write_bytes(b"{not json"),
    "record-too-large": lambda d: (d / "record.json").write_bytes(
        (d / "record.json").read_bytes() + b" " * (128 * 1024)
    ),
    "unknown-version": lambda d: _corrupt_record(d, lambda r: r.__setitem__("recordVersion", 4)),
    "extra-key": lambda d: _corrupt_record(d, lambda r: r.__setitem__("surprise", 1)),
    "missing-key": lambda d: _corrupt_record(d, lambda r: r.pop("interaction")),
    "id-mismatch": lambda d: _corrupt_record(
        d, lambda r: r.__setitem__("id", "local-00000000-0000-4000-8000-000000000000")
    ),
    "name-illegal": lambda d: _corrupt_record(d, lambda r: r.__setitem__("name", "!!!")),
    "name-too-long": lambda d: _corrupt_record(d, lambda r: r.__setitem__("name", "n" * 999)),
    "meaning-blank": lambda d: _corrupt_record(
        d, lambda r: r["imageChange"]["items"][0].__setitem__("meaning", "   ")
    ),
    "mode-illegal": lambda d: _corrupt_record(
        d, lambda r: r["imageChange"].__setitem__("mode", "teleport")
    ),
    "digest-format": lambda d: _corrupt_record(
        d, lambda r: r["resourceDigests"].__setitem__("default.png", "nope")
    ),
    "digest-key-mismatch": lambda d: _corrupt_record(
        d, lambda r: r["resourceDigests"].pop("default.png")
    ),
    "closure-extra-file": lambda d: (d / "stray.txt").write_bytes(b"x"),
    "resource-missing": lambda d: (d / "change-000.png").unlink(),
    "content-tampered": lambda d: (d / "default.png").write_bytes(b"truncated"),
}


@pytest.mark.unit
@pytest.mark.parametrize("flavour", sorted(_PROVEN_INVALID))
def test_every_proven_invalid_record_is_quarantined_and_frees_the_quota(
    tmp_path, monkeypatch, flavour
):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    root_key = store._root_key()
    _PROVEN_INVALID[flavour](store.root / tool["id"])

    try:
        with pytest.raises(AvatarToolStoreError) as raised:
            store.get_detail(tool["id"])
        assert raised.value.code == "record_invalid", flavour
        assert raised.value.transient is False, flavour
        assert tool["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS[root_key], flavour
        assert store.list_items() == [], flavour
        # Invalidated tools cannot be seen or deleted in the UI, so release both slots and quota.
        assert store._current_storage_bytes() == 0, flavour
        assert store._occupied_tool_slots() == 0, flavour
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)


@pytest.mark.unit
@pytest.mark.parametrize("locked", ("record.json", "default.png", "directory"))
def test_a_transient_read_failure_never_quarantines_whatever_is_locked(
    tmp_path, monkeypatch, locked
):
    """The dual of the matrix above: unreadable must never be mistaken for invalid."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Feather",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    root_key = store._root_key()
    final = store.root / tool["id"]
    occupied = store._current_storage_bytes()

    real_open = Path.open
    real_iterdir = Path.iterdir

    def locked_open(self, *args, **kwargs):
        if self.name == locked and str(final) in str(self):
            raise OSError("locked by another process")
        return real_open(self, *args, **kwargs)

    def locked_iterdir(self):
        if str(self) == str(final):
            raise OSError("directory listing failed")
        return real_iterdir(self)

    if locked == "directory":
        monkeypatch.setattr(Path, "iterdir", locked_iterdir)
    else:
        monkeypatch.setattr(Path, "open", locked_open)

    try:
        with pytest.raises(AvatarToolStoreError) as raised:
            store.get_detail(tool["id"])
        assert raised.value.transient is True, locked
        assert tool["id"] not in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set()), locked

        monkeypatch.undo()
        monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
        # Once the lock releases, the unchanged tool returns to listings with its slot and quota intact.
        assert [item["id"] for item in store.list_items()] == [tool["id"]], locked
        assert store._current_storage_bytes() == occupied, locked
        assert store._occupied_tool_slots() == 1, locked
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)


def test_recovery_treats_a_failed_directory_probe_as_transient_not_absent(tmp_path, monkeypatch):
    """A probe failure must not read as "the final is gone" - that unlocks rollback."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Updated",
        change_mode="press-swap",
        change_meanings=["the version the user just saved"],
        default_image=_png(size=(12, 12)),
        change_images=[_png(size=(13, 13))],
    )
    final = store.root / tool["id"]
    current_revision = store.record_revision(store.read_record(tool["id"]))

    # The last update published the new final but failed backup cleanup, leaving the old version.
    backup = store.root / f".{tool['id']}.backup"
    shutil.copytree(final, backup)
    (backup / "record.json").write_text(
        (backup / "record.json").read_text(encoding="utf-8").replace(
            "the version the user just saved", "the stale backup"
        ),
        encoding="utf-8",
    )
    stale = json.loads((backup / "record.json").read_text(encoding="utf-8"))
    stale["resourceDigests"] = {
        name: AvatarToolStore._file_digest(backup / name, 32 * 1024 * 1024)
        for name in stale["resourceDigests"]
    }
    (backup / "record.json").write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
    # No .updating: the last update completed; this backup is a leftover, not rollback evidence.
    assert not (store.root / f".{tool['id']}.updating").exists()

    real_stat, real_lstat = os.stat, os.lstat

    def flaky(real):
        def probe(path, *args, **kwargs):
            if str(path) == str(final):
                raise OSError(errno.EBUSY, "metadata temporarily unavailable")
            return real(path, *args, **kwargs)

        return probe

    root_key = store._root_key()
    # Block both: is_dir() uses stat, is_symlink() uses lstat. Blocking one still lets an
    # is_dir() implementation read the directory, so the test would miss regression.
    monkeypatch.setattr(os, "lstat", flaky(real_lstat))
    monkeypatch.setattr(os, "stat", flaky(real_stat))
    try:
        store.initialize()
        # Metadata is merely unreadable this time; preserve final unchanged.
        assert stat_module.S_ISDIR(real_lstat(str(final)).st_mode), (
            "the live final was rolled back on a transient probe failure"
        )
        assert stat_module.S_ISDIR(real_lstat(str(backup)).st_mode)
        assert root_key in avatar_tool_store._RECOVERY_PENDING_ROOTS

        monkeypatch.undo()
        monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
        store.initialize()

        # Only after metadata becomes readable and final proves valid may leftover backup be removed.
        assert not backup.exists()
        assert store.record_revision(store.read_record(tool["id"])) == current_revision
        assert store.get_detail(tool["id"])["changeItems"][0]["meaning"] == (
            "the version the user just saved"
        )
    finally:
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)


def test_public_resource_allowlist_accepts_a_symlinked_storage_root(tmp_path, monkeypatch):
    """The write side never rejects a symlinked root, so the serving side must not either."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    real_root = tmp_path / "real_avatar_tools"
    store = AvatarToolStore(_ConfigManager(real_root))
    tool = _create_tool(
        store,
        name="Linked",
        change_mode="press-swap",
        change_meanings=["served through a symlinked root"],
        default_image=_png(size=(12, 12)),
        change_images=[_png(size=(13, 13))],
    )

    link = tmp_path / "linked_avatar_tools"
    try:
        os.symlink(real_root, link, target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("creating a symlink needs privileges on this platform")

    assert is_public_avatar_tool_resource_path(link, f"{tool['id']}/default.png")

    # Always reject symlinks inside the root: those are the ones that can point outside it.
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png())
    inner = real_root / tool["id"] / "default.png"
    inner.unlink()
    os.symlink(outside, inner)
    assert not is_public_avatar_tool_resource_path(link, f"{tool['id']}/default.png")


def _flaky_lstat(monkeypatch, *targets):
    """Make os.lstat fail for exactly these paths, as a busy network root would."""
    real_lstat = os.lstat
    wanted = {str(target) for target in targets}

    def probe(path, *args, **kwargs):
        if str(path) in wanted:
            raise OSError(errno.EBUSY, "metadata temporarily unavailable")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", probe)


def test_a_transient_record_probe_failure_never_condemns_a_healthy_tool(tmp_path, monkeypatch):
    """The inner record probe must not report absence when the metadata read failed."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Healthy",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    record = store.root / tool["id"] / "record.json"
    root_key = store._root_key()

    _flaky_lstat(monkeypatch, record)
    with pytest.raises(AvatarToolStoreError) as raised:
        store.read_record(tool["id"])
    # Unreadable metadata must report transient failure: tool_not_found would make startup classify this healthy
    # tool as invalid, quarantine it, or even replace it with old backup if interruption evidence exists.
    assert raised.value.transient is True
    assert tool["id"] not in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())

    monkeypatch.undo()
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    assert [item["id"] for item in store.list_items()] == [tool["id"]]


def test_a_failed_staging_probe_keeps_the_root_recovery_pending(tmp_path, monkeypatch):
    """Skipping an unprobeable orphan while reporting success drops the retry."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    store.initialize()
    orphan = store.root / ".local-12345678-1234-4123-8123-123456789abc.uploading"
    orphan.mkdir()
    root_key = store._root_key()

    _flaky_lstat(monkeypatch, orphan)
    try:
        store.initialize()
        # Recovery cannot claim completion while an orphan remains, or this process never retries and it keeps
        # bypassing quota accounting while occupying the same ID.
        assert root_key in avatar_tool_store._RECOVERY_PENDING_ROOTS
        assert orphan.is_dir()

        monkeypatch.undo()
        monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
        store.initialize()
        assert not orphan.exists()
    finally:
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)


def test_a_failed_slot_probe_still_holds_the_tool_slot(tmp_path, monkeypatch):
    """An unprobeable directory must not free a slot - absence is not proof of absence."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    store.limits["maxTools"] = 1
    tool = _create_tool(
        store,
        name="First",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )

    _flaky_lstat(monkeypatch, store.root / tool["id"])
    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store, name="Second", change_mode="press-swap", change_meanings=["m"],
            default_image=_png(), change_images=[_png()],
        )
    # Undercounting one slot permits a 65th tool, exceeding the cap when the directory becomes readable. Slot checks precede
    # quota checks, so assert slot rejection here rather than substituting quota rejection.
    assert raised.value.code == "tool_limit_reached"


def test_a_failed_quota_probe_refuses_to_publish(tmp_path, monkeypatch):
    """A total that cannot be established authoritatively must not authorise a write."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="First",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    store.limits["maxTools"] = 99

    _flaky_lstat(monkeypatch, store.root / tool["id"] / "default.png")
    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store, name="Second", change_mode="press-swap", change_meanings=["m"],
            default_image=_png(), change_images=[_png()],
        )
    # Missing bytes nullify maxTotalBytes; reject writes when accounting is uncertain.
    assert raised.value.code == "avatar_tools_directory_unavailable"
    assert raised.value.transient is True


@pytest.mark.parametrize("target", ["directory", "resource"])
def test_a_transient_probe_failure_inside_validation_never_condemns(tmp_path, monkeypatch, target):
    """Probe failures inside record validation must stay transient, not proof of damage."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Healthy",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    final = store.root / tool["id"]
    root_key = store._root_key()

    _flaky_lstat(monkeypatch, final if target == "directory" else final / "default.png")
    with pytest.raises(AvatarToolStoreError) as raised:
        store.read_record(tool["id"], verify_resources=True)
    # Reporting nontransient record_invalid tells recovery the record is corrupt: it would quarantine
    # this healthy tool and replace it with old backup when interruption evidence exists.
    assert raised.value.transient is True
    assert tool["id"] not in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())

    monkeypatch.undo()
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    assert [item["id"] for item in store.list_items()] == [tool["id"]]


def test_a_transient_closure_probe_failure_is_not_a_closure_violation(tmp_path, monkeypatch):
    """An unreadable entry must not be reported as "this tool has foreign content"."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Healthy",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    intruder = store.root / tool["id"] / "extra.txt"
    intruder.write_text("dropped in by a sync client", encoding="utf-8")

    # Establish the baseline: when the extra file is readable, closure mismatch deterministically disproves validity.
    with pytest.raises(AvatarToolStoreError) as proven:
        store.read_record(tool["id"], verify_resources=True)
    assert proven.value.transient is False

    # The same file is merely unreadable this time: downgrade from invalid to temporarily unreadable.
    _flaky_lstat(monkeypatch, intruder)
    with pytest.raises(AvatarToolStoreError) as raised:
        store.read_record(tool["id"], verify_resources=True)
    assert raised.value.transient is True


def test_a_failed_staging_size_probe_refuses_the_update(tmp_path, monkeypatch):
    """Understating the staged size would authorise an update that busts the quota."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="First",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    final = store.root / tool["id"]
    base_revision = store.record_revision(store.read_record(tool["id"]))

    real_lstat = os.lstat

    def probe(path, *args, **kwargs):
        # This process just wrote staging-directory files; simulate a network-drive glitch at that instant.
        if ".updating" in str(path) and str(path).endswith(".png"):
            raise OSError(errno.EBUSY, "metadata temporarily unavailable")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", probe)
    with pytest.raises(AvatarToolStoreError) as raised:
        store.update_tool(
            tool["id"],
            base_revision=base_revision,
            name="Renamed",
            change_mode="press-swap",
            change_meanings=["meaning"],
            default_resource=None,
            default_image=_png(size=(9, 9)),
            change_resources=[""],
            change_images=[_png(size=(10, 10))],
        )
    # Whichever layer rejects it must report temporary unavailability rather than a corrupt record,
    # preserving the original record intact: one failed read must not lose the user's saved copy.
    assert raised.value.transient is True

    monkeypatch.undo()
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    assert final.is_dir()
    assert store.read_record(tool["id"])["name"] == "First"


def test_directory_bytes_refuses_to_understate_the_total(tmp_path, monkeypatch):
    """Silently dropping an unreadable file would authorise a write past the quota."""
    directory = tmp_path / "staged"
    directory.mkdir()
    (directory / "default.png").write_bytes(b"x" * 128)

    # Baseline: count normally when readable.
    assert AvatarToolStore._directory_bytes(directory) == 128

    _flaky_lstat(monkeypatch, directory / "default.png")
    with pytest.raises(AvatarToolStoreError) as raised:
        AvatarToolStore._directory_bytes(directory)
    assert raised.value.code == "avatar_tools_directory_unavailable"
    assert raised.value.transient is True


@pytest.mark.parametrize("occupant", ["file", "symlink"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_a_foreign_occupant_at_the_final_path_defers_instead_of_being_deleted(
    tmp_path, monkeypatch, occupant, interrupted
):
    """A non-directory squatting the final name is neither overwritten nor cleaned up."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Original",
        change_mode="press-swap",
        change_meanings=["the only surviving copy"],
        default_image=_png(size=(12, 12)),
        change_images=[_png(size=(13, 13))],
    )
    final = store.root / tool["id"]
    backup = store.root / f".{tool['id']}.backup"
    shutil.copytree(final, backup)

    # A sync client or manual action replaced the official directory with something else.
    shutil.rmtree(final)
    if occupant == "file":
        final.write_bytes(b"replaced by a sync client")
    else:
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        try:
            os.symlink(outside, final, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            pytest.skip("creating a symlink needs privileges on this platform")

    updating = store.root / f".{tool['id']}.updating"
    if interrupted:
        shutil.copytree(backup, updating)

    root_key = store._root_key()
    try:
        store.initialize()

        # Replacing it with backup requires deleting the occupying object, violating the rule against recovery deleting official directories for users.
        kind, _, _ = avatar_tool_store._probe_entry(final)
        assert kind != "absent", "recovery deleted whatever was sitting at the final path"
        assert kind != "dir", "the stale backup was published over a foreign occupant"
        # Backup might also be the tool's only remaining copy and cannot simply be removed.
        assert backup.is_dir(), "the only surviving copy was cleaned up"
        # Neither path works, but this persistent state concerns only one ID: do not keep the entire store root
        # pending recovery; block writes to this ID alone.
        assert root_key not in avatar_tool_store._RECOVERY_PENDING_ROOTS
        with pytest.raises(AvatarToolStoreError) as blocked:
            store.create_tool_v3(manifest=_v3_manifest(tool["id"]), uploads=[_png()])
        assert (blocked.value.code, blocked.value.status_code) == ("tool_recovery_pending", 409)
        assert backup.is_dir()
    finally:
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)


def test_recovery_rechecks_the_final_before_replacing_it_with_a_backup(tmp_path, monkeypatch):
    """Validating a backup is slow enough for a sync client to publish a new final."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Original",
        change_mode="press-swap",
        change_meanings=["the version the backup holds"],
        default_image=_png(size=(12, 12)),
        change_images=[_png(size=(13, 13))],
    )
    final = store.root / tool["id"]
    backup = store.root / f".{tool['id']}.backup"
    shutil.copytree(final, backup)

    # Initial state: no official directory (left by failed rollback), with backup the only copy;
    # this authorizes recovery to roll back.
    newest = tmp_path / "newest"
    shutil.copytree(final, newest)
    (newest / "record.json").write_text(
        (newest / "record.json").read_text(encoding="utf-8").replace(
            "the version the backup holds", "what the sync client just published"
        ),
        encoding="utf-8",
    )
    fresh = json.loads((newest / "record.json").read_text(encoding="utf-8"))
    fresh["resourceDigests"] = {
        name: AvatarToolStore._file_digest(newest / name, 32 * 1024 * 1024)
        for name in fresh["resourceDigests"]
    }
    (newest / "record.json").write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(final)

    real_digest = AvatarToolStore.__dict__["_file_digest"].__func__
    raced = []

    def digest_and_race(path, maximum):
        # While backup is being validated, another process creates the official directory.
        if not raced:
            raced.append(True)
            shutil.copytree(newest, final)
        return real_digest(path, maximum)

    monkeypatch.setattr(AvatarToolStore, "_file_digest", staticmethod(digest_and_race))
    root_key = store._root_key()
    try:
        store.initialize()
        assert raced, "the race never happened; this test proves nothing"

        # Authorization depended on the official directory being absent. If that premise changes during validation, abstain this pass:
        # neither delete the newly appeared official directory nor consume backup.
        assert backup.is_dir(), "the backup was consumed on a stale authorization"
        assert root_key in avatar_tool_store._RECOVERY_PENDING_ROOTS

        monkeypatch.undo()
        monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
        # The next operation rechecks the premise: the official directory is now valid and authoritative,
        # so backup becomes a leftover to remove; the old backup never erases the newly published version.
        assert store.get_detail(tool["id"])["changeItems"][0]["meaning"] == (
            "what the sync client just published"
        )
        assert not backup.exists()
    finally:
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)


def test_recovery_recheck_detects_one_directory_replaced_by_another(tmp_path, monkeypatch):
    """A same-kind replacement is still a different final and must survive recovery."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Backup",
        change_mode="press-swap",
        change_meanings=["the backup version"],
        default_image=_png(size=(12, 12)),
        change_images=[_png(size=(13, 13))],
    )
    final = store.root / tool["id"]
    backup = store.root / f".{tool['id']}.backup"
    updating = store.root / f".{tool['id']}.updating"
    shutil.copytree(final, backup)
    shutil.copytree(final, updating)
    (final / "record.json").write_bytes(b"not-json")

    external_store = AvatarToolStore(_ConfigManager(tmp_path / "external_avatar_tools"))
    _create_tool(
        external_store,
        tool_id=tool["id"],
        name="Newest",
        change_mode="press-swap",
        change_meanings=["the sync client version"],
        default_image=_png(size=(14, 14)),
        change_images=[_png(size=(15, 15))],
    )
    newest = external_store.root / tool["id"]

    real_digest = AvatarToolStore.__dict__["_file_digest"].__func__
    raced = []

    def digest_and_replace_final(path, maximum):
        if not raced and str(backup) in str(path):
            raced.append(True)
            shutil.rmtree(final)
            shutil.copytree(newest, final)
        return real_digest(path, maximum)

    monkeypatch.setattr(AvatarToolStore, "_file_digest", staticmethod(digest_and_replace_final))
    root_key = store._root_key()
    try:
        store.initialize()
        assert raced, "the same-kind replacement race never happened"
        assert json.loads((final / "record.json").read_text(encoding="utf-8"))["name"] == "Newest"
        assert backup.is_dir(), "recovery consumed a backup after its authorization became stale"
        assert root_key in avatar_tool_store._RECOVERY_PENDING_ROOTS

        monkeypatch.undo()
        monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
        assert store.get_detail(tool["id"])["name"] == "Newest"
        assert not backup.exists()
        assert not updating.exists()
    finally:
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)


def test_recovery_recheck_detects_a_valid_version_written_in_place(tmp_path, monkeypatch):
    """Directory identity alone cannot detect a record rewritten inside the same directory."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="Backup",
        change_mode="press-swap",
        change_meanings=["shared resources"],
        default_image=_png(size=(12, 12)),
        change_images=[_png(size=(13, 13))],
    )
    final = store.root / tool["id"]
    backup = store.root / f".{tool['id']}.backup"
    updating = store.root / f".{tool['id']}.updating"
    shutil.copytree(final, backup)
    shutil.copytree(final, updating)

    newest_record = json.loads((final / "record.json").read_text(encoding="utf-8"))
    newest_record["name"] = "Newest in place"
    newest_record_text = json.dumps(newest_record, ensure_ascii=False)
    (final / "record.json").write_bytes(b"not-json")

    real_digest = AvatarToolStore.__dict__["_file_digest"].__func__
    raced = []

    def digest_and_rewrite_record(path, maximum):
        if not raced and str(backup) in str(path):
            raced.append(True)
            # A sync client can update an existing file without replacing the parent
            # directory, so lstat identity for `final` remains unchanged.
            (final / "record.json").write_text(newest_record_text, encoding="utf-8")
        return real_digest(path, maximum)

    monkeypatch.setattr(AvatarToolStore, "_file_digest", staticmethod(digest_and_rewrite_record))
    root_key = store._root_key()
    try:
        store.initialize()
        assert raced, "the in-place update race never happened"
        assert json.loads((final / "record.json").read_text(encoding="utf-8"))["name"] == (
            "Newest in place"
        )
        assert backup.is_dir(), "recovery consumed a backup after the final became valid"
        assert root_key in avatar_tool_store._RECOVERY_PENDING_ROOTS

        monkeypatch.undo()
        monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
        assert store.get_detail(tool["id"])["name"] == "Newest in place"
        assert not backup.exists()
        assert not updating.exists()
    finally:
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)


def test_update_does_not_overwrite_a_version_published_while_staging(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    original = _create_tool(
        store,
        name="Original",
        change_mode="press-swap",
        change_meanings=["original"],
        default_image=_png(size=(12, 12)),
        change_images=[_png(size=(13, 13))],
    )
    final = store.root / original["id"]

    external_store = AvatarToolStore(_ConfigManager(tmp_path / "external_avatar_tools"))
    _create_tool(
        external_store,
        tool_id=original["id"],
        name="External",
        change_mode="press-swap",
        change_meanings=["newest"],
        default_image=_png(size=(14, 14)),
        change_images=[_png(size=(15, 15))],
    )
    external = external_store.root / original["id"]
    real_write = store._write_staged_tool
    raced = []

    def stage_then_publish_external(directory, record, resources):
        real_write(directory, record, resources)
        if not raced:
            raced.append(True)
            shutil.rmtree(final)
            shutil.copytree(external, final)

    monkeypatch.setattr(store, "_write_staged_tool", stage_then_publish_external)
    with pytest.raises(AvatarToolStoreError) as raised:
        store.update_tool(
            original["id"],
            base_revision=original["revision"],
            name="Local edit",
            change_mode="press-swap",
            change_meanings=["local edit"],
            default_resource="default.png",
            default_image=None,
            change_resources=["change-000.png"],
            change_images=[],
        )

    assert raised.value.code == "tool_revision_conflict"
    assert store.get_detail(original["id"])["name"] == "External"
    assert not (store.root / f".{original['id']}.updating").exists()
    assert not (store.root / f".{original['id']}.backup").exists()


def test_incomplete_recovery_blocks_a_new_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    store.ensure()
    root_key = store._root_key()
    avatar_tool_store._RECOVERY_PENDING_ROOTS.add(root_key)
    monkeypatch.setattr(store, "_recover_interrupted_mutations", lambda: False)
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    try:
        with pytest.raises(AvatarToolStoreError) as raised:
            _create_tool(
                store,
                tool_id=tool_id,
                name="Blocked",
                change_mode="press-swap",
                change_meanings=["wait"],
                default_image=_png(),
                change_images=[_png()],
            )
        assert raised.value.code == "avatar_tools_directory_unavailable"
        assert raised.value.transient is True
        assert not (store.root / tool_id).exists()
        assert root_key in avatar_tool_store._RECOVERY_PENDING_ROOTS
    finally:
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)


@pytest.mark.parametrize("operation", ("list", "create"))
def test_entry_probe_failures_are_reported_as_temporary_storage_errors(
    tmp_path, monkeypatch, operation
):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    existing = _create_tool(
        store,
        name="Existing",
        change_mode="press-swap",
        change_meanings=["state"],
        default_image=_png(),
        change_images=[_png()],
    )
    target = (
        store.root / existing["id"]
        if operation == "list"
        else store.root / "local-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    )
    real_lstat, real_stat = os.lstat, os.stat

    def fail_target(real):
        def probe(path, *args, **kwargs):
            if str(path) == str(target):
                raise OSError(errno.EBUSY, "metadata temporarily unavailable")
            return real(path, *args, **kwargs)

        return probe

    monkeypatch.setattr(os, "lstat", fail_target(real_lstat))
    monkeypatch.setattr(os, "stat", fail_target(real_stat))

    with pytest.raises(AvatarToolStoreError) as raised:
        if operation == "list":
            store.list_items()
        else:
            _create_tool(
                store,
                tool_id=target.name,
                name="New",
                change_mode="press-swap",
                change_meanings=["state"],
                default_image=_png(),
                change_images=[_png()],
            )
    assert raised.value.code == "avatar_tools_directory_unavailable"
    assert raised.value.transient is True


def test_a_failed_rollback_probe_keeps_the_root_recovery_pending(tmp_path, monkeypatch):
    """After final was renamed to .backup, that backup is the tool's only copy."""
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool = _create_tool(
        store,
        name="First",
        change_mode="press-swap",
        change_meanings=["meaning"],
        default_image=_png(),
        change_images=[_png()],
    )
    final = store.root / tool["id"]
    base_revision = store.record_revision(store.read_record(tool["id"]))
    root_key = store._root_key()

    real_replace, real_lstat = os.replace, os.lstat
    replaces = []
    in_rollback_window = []

    def failing_replace(src, dst, **kwargs):
        replaces.append((str(src), str(dst)))
        # Target publication exactly (.updating -> final), not by call order:
        # atomic_write_json also calls os.replace when writing record.json.
        # Fail here after the official directory becomes .backup, leaving that as the sole copy.
        if str(dst) == str(final) and str(src).endswith(".updating"):
            in_rollback_window.append(True)
            raise OSError(errno.EIO, "publish failed")
        return real_replace(src, dst, **kwargs)

    def flaky_lstat(path, *args, **kwargs):
        # Inject glitches only during rollback, or the update gets interrupted before reaching it.
        if in_rollback_window and str(path) == str(final):
            raise OSError(errno.EBUSY, "metadata temporarily unavailable")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "replace", failing_replace)
    monkeypatch.setattr(os, "lstat", flaky_lstat)
    try:
        with pytest.raises(OSError):
            store.update_tool(
                tool["id"],
                base_revision=base_revision,
                name="Renamed",
                change_mode="press-swap",
                change_meanings=["meaning"],
                default_resource=None,
                default_image=_png(size=(9, 9)),
                change_resources=[""],
                change_images=[_png(size=(10, 10))],
            )
        assert in_rollback_window, "the failure did not land in the rollback window"
        # Inability to determine whether rollback is needed must not mean no rollback; that would leave the tool
        # missing for this process until recovery at the next startup.
        assert root_key in avatar_tool_store._RECOVERY_PENDING_ROOTS
    finally:
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)


def _poison_initial_image_id(record):
    record["initialImageId"] = ["img-1"]


def _poison_link_target(record):
    record["imageInteractions"]["links"][0]["to"] = {"id": "ix-click"}


def _poison_release_image(record):
    record["imageInteractions"]["items"][0]["actions"]["release"] = {
        "kind": "show",
        "imageId": ["img-1"],
    }


def _poison_source_side(record):
    record["imageInteractions"]["initialLinks"][0]["sourceSide"] = ["right"]


@pytest.mark.parametrize(
    "poison",
    (_poison_initial_image_id, _poison_link_target, _poison_release_image, _poison_source_side, "nested"),
)
def test_an_untyped_on_disk_v3_value_is_skipped_like_any_invalid_record(tmp_path, monkeypatch, poison):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    poisoned_id, healthy_id = (f"local-{uuid.uuid4()}" for _ in range(2))
    store.create_tool_v3(manifest=_v3_manifest(poisoned_id), uploads=[_png()])
    healthy = store.create_tool_v3(manifest=_v3_manifest(healthy_id, name="Healthy"), uploads=[_png()])
    record_path = store.root / poisoned_id / "record.json"
    if poison == "nested":
        # Under 64 KiB can contain nesting deep enough to overflow json.loads recursion.
        record_path.write_text("[" * 5000 + "]" * 5000, encoding="utf-8")
    else:
        _corrupt_record(store.root / poisoned_id, poison)

    assert store.list_items() == [healthy]
    assert store._occupied_tool_slots() == 1
    with pytest.raises(AvatarToolStoreError) as raised:
        store.get_detail(poisoned_id)
    assert raised.value.code == "record_invalid"
    assert raised.value.transient is False
    store.create_tool_v3(manifest=_v3_manifest(f"local-{uuid.uuid4()}"), uploads=[_png()])


def test_initialize_survives_an_untyped_final_next_to_an_update_backup(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    backup = store.root / f".{tool_id}.backup"
    shutil.copytree(store.root / tool_id, backup)
    _corrupt_record(store.root / tool_id, lambda record: record.update(initialImageId={"k": 1}))

    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()

    # Without .updating interruption evidence, remove leftover backup; quarantine the invalid official directory without deleting it.
    assert restarted._root_key() not in avatar_tool_store._RECOVERY_PENDING_ROOTS
    assert not backup.exists()
    assert (store.root / tool_id / "record.json").is_file()
    assert restarted.list_items() == []


def test_initialize_keeps_the_root_pending_after_an_unexpected_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))

    def unexpected():
        raise RuntimeError("unexpected recovery failure")

    monkeypatch.setattr(store, "_recover_interrupted_mutations", unexpected)
    with pytest.raises(RuntimeError):
        store.initialize()
    assert store._root_key() in avatar_tool_store._RECOVERY_PENDING_ROOTS


@pytest.mark.parametrize(
    ("location", "expected_code", "expected_index"),
    (
        ("image_meaning", "image_meaning_invalid", 1),
        ("image_name", "image_name_invalid", 1),
        ("interaction_name", "interaction_name_invalid", 0),
        ("special_meaning", "special_meaning_invalid", None),
    ),
)
def test_v3_rejects_lone_surrogates_as_field_errors_without_publishing(
    tmp_path, monkeypatch, location, expected_code, expected_index
):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    manifest = _v3_manifest(
        tool_id,
        sources=[{"kind": "upload", "index": 0}, {"kind": "upload", "index": 1}],
    )
    uploads = [_png(), _png(size=(9, 9))]
    if location == "image_meaning":
        manifest["images"][1]["meaning"] = "changed \ud83d"
    elif location == "image_name":
        manifest["images"][1]["name"] = "State \udc00"
    elif location == "interaction_name":
        manifest["imageInteractions"]["items"][0]["name"] = "Click \ud800"
    else:
        manifest["interaction"] = {"special": {
            "probability": 0.5,
            "image": {"kind": "upload", "index": 2},
            "meaning": "sparkles \udfff",
        }}
        uploads.append(_png(size=(10, 10)))

    with pytest.raises(AvatarToolStoreError) as raised:
        store.create_tool_v3(manifest=manifest, uploads=uploads)

    assert raised.value.code == expected_code
    assert raised.value.status_code == 400
    assert raised.value.index == expected_index
    assert not store.root.exists() or not list(store.root.iterdir())


def test_v2_rejects_a_lone_surrogate_in_a_change_meaning(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))

    with pytest.raises(AvatarToolStoreError) as raised:
        _create_tool(
            store,
            name="Feather",
            change_mode="click-advance",
            change_meanings=["fine", "broken \ud83d"],
            default_image=_png(),
            change_images=[_png(), _png(size=(9, 9))],
        )

    assert (raised.value.code, raised.value.field, raised.value.index) == (
        "change_meaning_invalid", "change_meaning", 1,
    )
    assert not store.root.exists() or not list(store.root.iterdir())


def test_detail_rejects_a_closure_violation_before_hashing_any_resource(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    manifest = _v3_manifest(tool_id)
    manifest["interaction"] = {"normalSound": {"kind": "upload", "index": 1}}
    store.create_tool_v3(manifest=manifest, uploads=[_png(), _mp3()])
    (store.root / tool_id / ".DS_Store").write_bytes(b"\0" * 6148)

    def unexpected_digest(*_args, **_kwargs):
        pytest.fail("a closure-invalid tool must be rejected before any resource is hashed")

    monkeypatch.setattr(AvatarToolStore, "_file_digest", staticmethod(unexpected_digest))
    with pytest.raises(AvatarToolStoreError) as raised:
        store.get_detail(tool_id)

    assert raised.value.code == "record_invalid"
    assert raised.value.transient is False
    assert tool_id in avatar_tool_store._QUARANTINED_TOOL_IDS[store._root_key()]


@pytest.mark.parametrize(
    "poison",
    (_poison_initial_image_id, _poison_link_target, _poison_release_image, _poison_source_side),
)
def test_v3_validator_type_checks_before_membership_tests(tmp_path, monkeypatch, poison):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    record = json.loads((store.root / tool_id / "record.json").read_text(encoding="utf-8"))
    poison(record)

    with pytest.raises(AvatarToolStoreError) as raised:
        store._validate_record_v3(record, expected_id=tool_id, structure_only=True)
    assert raised.value.code == "record_invalid"


def test_an_unexpected_validator_error_is_normalized_to_an_invalid_record(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    broken_id, healthy_id = (f"local-{uuid.uuid4()}" for _ in range(2))
    store.create_tool_v3(manifest=_v3_manifest(broken_id), uploads=[_png()])
    healthy = store.create_tool_v3(manifest=_v3_manifest(healthy_id), uploads=[_png()])
    real_validate = AvatarToolStore._validate_record

    def validator_with_a_gap(self, payload, *, expected_id, **kwargs):
        if expected_id == broken_id:
            raise TypeError("unhashable type: 'list'")
        return real_validate(self, payload, expected_id=expected_id, **kwargs)

    monkeypatch.setattr(AvatarToolStore, "_validate_record", validator_with_a_gap)

    assert store.list_items() == [healthy]
    assert store._occupied_tool_slots() == 1
    assert broken_id in avatar_tool_store._QUARANTINED_TOOL_IDS[store._root_key()]


def test_delete_with_a_stale_base_revision_keeps_the_newer_version(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    opened = store.create_tool_v3(manifest=_v3_manifest(tool_id, name="r1"), uploads=[_png()])
    # Another window saved r2; the old editor still holds r1.
    saved = store.update_tool_v3(
        tool_id,
        base_revision=opened["revision"],
        manifest=_v3_manifest(tool_id, name="r2"),
        uploads=[_png()],
    )
    assert saved["revision"] != opened["revision"]

    for stale in (opened["revision"], "", "not-a-revision"):
        with pytest.raises(AvatarToolStoreError) as raised:
            store.delete_tool(tool_id, base_revision=stale)
        assert (raised.value.code, raised.value.status_code) == ("tool_revision_conflict", 409)
    assert store.get_detail(tool_id)["name"] == "r2"
    assert sorted(path.name for path in store.root.iterdir()) == [tool_id]

    assert store.delete_tool(tool_id, base_revision=saved["revision"]) == tool_id
    assert not (store.root / tool_id).exists()

    # Omitting base_revision preserves existing behavior.
    legacy = store.create_tool_v3(manifest=_v3_manifest(tool_id, name="legacy"), uploads=[_png()])
    assert store.delete_tool(legacy["id"]) == tool_id


def test_delete_with_a_base_revision_still_removes_a_provably_invalid_record(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    opened = store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])
    (store.root / tool_id / "record.json").write_bytes(b"{not json")

    assert store.delete_tool(tool_id, base_revision=opened["revision"]) == tool_id
    assert not (store.root / tool_id).exists()


def test_delete_with_a_base_revision_refuses_when_the_record_is_temporarily_unreadable(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id = f"local-{uuid.uuid4()}"
    opened = store.create_tool_v3(manifest=_v3_manifest(tool_id), uploads=[_png()])

    def unreadable(*_args, **_kwargs):
        raise avatar_tool_store._record_temporarily_unreadable()

    monkeypatch.setattr(store, "_read_record_from_directory", unreadable)
    with pytest.raises(AvatarToolStoreError) as raised:
        store.delete_tool(tool_id, base_revision=opened["revision"])
    assert (raised.value.code, raised.value.status_code) == ("avatar_tools_directory_unavailable", 503)
    assert (store.root / tool_id / "record.json").is_file()


@pytest.mark.parametrize("artifact", ("backup", "updating"))
def test_a_foreign_final_next_to_update_artifacts_blocks_only_its_own_tool_id(
    tmp_path, monkeypatch, artifact
):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    blocked_id, other_id, new_id = (f"local-{uuid.uuid4()}" for _ in range(3))
    store.create_tool_v3(manifest=_v3_manifest(blocked_id, name="Blocked"), uploads=[_png()])
    other = store.create_tool_v3(manifest=_v3_manifest(other_id, name="Other"), uploads=[_png()])
    final = store.root / blocked_id
    leftover = store.root / f".{blocked_id}.{artifact}"
    shutil.copytree(final, leftover)
    shutil.rmtree(final)
    final.write_bytes(b"placed by a sync client")

    restarted = AvatarToolStore(_ConfigManager(store.root))
    restarted.initialize()
    assert restarted._root_key() not in avatar_tool_store._RECOVERY_PENDING_ROOTS

    recovery_runs = 0
    real_recover = AvatarToolStore._recover_interrupted_mutations

    def counting_recover(self):
        nonlocal recovery_runs
        recovery_runs += 1
        return real_recover(self)

    monkeypatch.setattr(AvatarToolStore, "_recover_interrupted_mutations", counting_recover)
    for _ in range(3):
        assert [item["id"] for item in restarted.list_items()] == [other_id]
    updated = restarted.update_tool_v3(
        other_id,
        base_revision=other["revision"],
        manifest=_v3_manifest(other_id, name="Other 2"),
        uploads=[_png()],
    )
    assert updated["name"] == "Other 2"
    restarted.create_tool_v3(manifest=_v3_manifest(new_id, name="New"), uploads=[_png()])
    assert restarted.delete_tool(new_id) == new_id
    assert recovery_runs == 0, "a per-tool anomaly kept re-running the full recovery"

    def assert_blocked(operation):
        with pytest.raises(AvatarToolStoreError) as raised:
            operation()
        assert (raised.value.code, raised.value.status_code) == ("tool_recovery_pending", 409)

    assert_blocked(lambda: restarted.create_tool_v3(
        manifest=_v3_manifest(blocked_id, name="Blocked"), uploads=[_png()]
    ))
    assert_blocked(lambda: restarted.update_tool_v3(
        blocked_id,
        base_revision=other["revision"],
        manifest=_v3_manifest(blocked_id, name="Changed"),
        uploads=[_png()],
    ))
    assert_blocked(lambda: restarted.delete_tool(blocked_id))
    assert leftover.is_dir()
    assert final.read_bytes() == b"placed by a sync client"
    assert restarted._root_key() not in avatar_tool_store._RECOVERY_PENDING_ROOTS

    # After removing the occupying file, the next same-ID write runs recovery first: backup restores the original tool
    # (retrying the same creation returns it directly); lone .updating leftovers are removed before normal creation.
    final.unlink()
    recreated = restarted.create_tool_v3(
        manifest=_v3_manifest(blocked_id, name="Blocked"), uploads=[_png()]
    )
    assert recreated["name"] == "Blocked"
    assert not leftover.exists()
    assert sorted(item["id"] for item in restarted.list_items()) == sorted([blocked_id, other_id])


@pytest.mark.parametrize("flavour", ("identity-mismatch", "corrupt-marker"))
@pytest.mark.parametrize("restore_fails_once", (False, True))
def test_recovery_undoes_an_unverifiable_deletion_when_the_final_path_is_free(
    tmp_path, monkeypatch, flavour, restore_fails_once
):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    tool_id, other_id = (f"local-{uuid.uuid4()}" for _ in range(2))
    store.create_tool_v3(manifest=_v3_manifest(tool_id, name="Moved"), uploads=[_png()])
    store.create_tool_v3(manifest=_v3_manifest(other_id, name="Other"), uploads=[_png()])
    final = store.root / tool_id
    deleting = store.root / f".{tool_id}.deleting"
    marker = store.root / f".{tool_id}.deleting.unverified"
    real_replace = os.replace

    class SimulatedCrash(BaseException):
        pass

    def crash_after_move(source, destination, *args, **kwargs):
        result = real_replace(source, destination, *args, **kwargs)
        if Path(destination) == deleting:
            raise SimulatedCrash()
        return result

    monkeypatch.setattr("utils.avatar_tool_store.os.replace", crash_after_move)
    with pytest.raises(SimulatedCrash):
        store.delete_tool(tool_id)
    monkeypatch.setattr("utils.avatar_tool_store.os.replace", real_replace)
    avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(store._root_key())

    # For example, copying/migrating the store root changes st_dev/st_ino, permanently mismatching authorization.
    if flavour == "identity-mismatch":
        record_path = deleting / "record.json"
        record_path.write_bytes(record_path.read_bytes())
    else:
        marker.write_bytes(b"{")
    moved_files = {path.name: path.read_bytes() for path in deleting.iterdir()}

    def refuse_restore(source, destination, *args, **kwargs):
        if Path(source) == deleting:
            raise OSError(errno.EACCES, "restore refused")
        return real_replace(source, destination, *args, **kwargs)

    restarted = AvatarToolStore(_ConfigManager(store.root))
    if restore_fails_once:
        monkeypatch.setattr("utils.avatar_tool_store.os.replace", refuse_restore)
        restarted.initialize()
        # Failure to move back is transient: preserve copy and authorization, pending recovery for retry.
        assert restarted._root_key() in avatar_tool_store._RECOVERY_PENDING_ROOTS
        assert {path.name: path.read_bytes() for path in deleting.iterdir()} == moved_files
        assert marker.exists()
        assert not final.exists()
        monkeypatch.setattr("utils.avatar_tool_store.os.replace", real_replace)
        items = restarted.list_items()
    else:
        restarted.initialize()
        items = restarted.list_items()

    # Unprovable deletion is undone: the unchanged tool returns and users can delete again, rather than indefinitely occupying
    # this ID and invisible quota.
    assert restarted._root_key() not in avatar_tool_store._RECOVERY_PENDING_ROOTS
    assert not deleting.exists()
    assert not marker.exists()
    assert {path.name: path.read_bytes() for path in final.iterdir()} == moved_files
    assert sorted(item["id"] for item in items) == sorted([tool_id, other_id])
    assert restarted.delete_tool(tool_id) == tool_id
    assert sorted(path.name for path in store.root.iterdir()) == [other_id]


@pytest.mark.parametrize("failing_step", ("write-fence", "ensure-directory"))
def test_initialize_keeps_the_root_pending_after_any_unexpected_failure(
    tmp_path, monkeypatch, failing_step
):
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))

    def unexpected(*_args, **_kwargs):
        raise RuntimeError(f"unexpected {failing_step} failure")

    if failing_step == "write-fence":
        monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", unexpected)
    else:
        monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
        monkeypatch.setattr(store, "_ensure_directory", unexpected)
    with pytest.raises(RuntimeError):
        store.initialize()
    assert store._root_key() in avatar_tool_store._RECOVERY_PENDING_ROOTS
