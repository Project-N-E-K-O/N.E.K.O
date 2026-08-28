from __future__ import annotations

import io
import json
import os
import shutil
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

    # 列表这一层不再逐字节重算 digest（太贵，前端每次 focus 都会拉列表），
    # 所以内容被篡改的道具会先继续出现在列表里……
    assert {item["id"] for item in store.list_items()} == {valid["id"], damaged["id"]}
    # ……但真正消费它的地方立刻拒绝：详情页 / 编辑页走全量校验。
    with pytest.raises(AvatarToolStoreError) as raised:
        store.get_detail(damaged["id"])
    assert raised.value.code == "record_invalid"

    # 详情页那次核验就地把它摘出公开目录，不用等重启。
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

    assert raised.value.code == expected_code


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
        # 残留仍占预算，所以必须登记恢复，否则本进程内这份字节数要不回来。
        assert store._current_storage_bytes() > retained_bytes
        assert store._root_key() in avatar_tool_store._RECOVERY_PENDING_ROOTS

        # 不重启、不显式调 initialize()：下一次普通调用就该把残留清掉。
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

        # 启动同样不做全量复核：作者原来的启动路径一个文件都不 hash，加回去会给
        # 每次冷启动摊上 O(总字节数)。
        store.initialize()
        assert digests == [], "startup must not recompute resource digests"

        # 只有真正消费资源的地方才逐字节核验。
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
        # 文件没坏，只是这一轮读不到 —— 一次杀软扫描不该永久藏掉好道具。
        monkeypatch.setattr(AvatarToolStore, "_file_digest", staticmethod(locked_digest))
        with pytest.raises(AvatarToolStoreError) as raised:
            store.get_detail(tool["id"])
        assert raised.value.integrity_mismatch is False
        assert tool["id"] not in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())
        monkeypatch.undo()
        monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
        assert [item["id"] for item in store.list_items()] == [tool["id"]]

        # 读到了字节、但和摘要对不上 —— 这才是确定性损坏。
        (store.root / tool["id"] / "default.png").write_bytes(b"truncated")
        with pytest.raises(AvatarToolStoreError) as raised:
            store.get_detail(tool["id"])
        assert raised.value.integrity_mismatch is True
        assert tool["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS[root_key]
        assert store.list_items() == []

        # 损坏的道具改不动（update 自己就会全量核验），只能删；删掉要解除隔离。
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
            # 校验通过之后、retained_bytes 打开文件之前，外部写者换掉了内容。
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

    # 只让 retained_bytes 那次打开失败：update_tool 开头的
    # read_record(verify_resources=True) 也会打开同一个文件算摘要，得先放行。
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
        # 路由只接 AvatarToolStoreError / MaintenanceModeError；裸 OSError 会变 500。
        assert raised.value.code == "resource_read_failed"
        assert raised.value.status_code == 503
        assert raised.value.field == "default_image"
        # 读不到不等于损坏，不能隔离。
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

    # 上限必须从 limits 推出来，而不是拍脑袋：改大 maxChangeImages /
    # maxMeaningChars / maxNameChars 而忘了调上限，这里要先红。
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
    # 合法 record 后面缀上大量空白：JSON 依然可解析、schema 依然通过，所以只有
    # 「读取有上限」这一条能让它出局 —— 否则断言分不清是被大小拒的还是被结构拒的。
    record_path.write_bytes(
        record_path.read_bytes() + b" " * (cap * 2)
    )

    with pytest.raises(AvatarToolStoreError) as raised:
        store.read_record(tool["id"])
    assert raised.value.code == "record_invalid"
    # 文件过大不等于内容损坏，不该触发隔离。
    assert raised.value.integrity_mismatch is False
    assert tool["id"] not in avatar_tool_store._QUARANTINED_TOOL_IDS.get(store._root_key(), set())


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

    # retained_bytes 的预检是第二道防线：update_tool 开头的全量核验里
    # _file_digest 已经有自己的大小预检，所以只有「核验通过之后资源才变超限」
    # 这条 TOCTOU 路径能走到它。用收紧上限模拟那一刻，省去往盘上写 8 MiB。
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
        # 一个字节都不该被读进来。
        assert consumed["bytes"] == 0, f"hashed {consumed['bytes']} bytes of an oversized asset"
        # 大小越界是确定性的内容异常，应当隔离。
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

    # 之前闭包只统计普通文件，塞进来的子目录/符号链接会被无声忽略。
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

    # 谎报快照，等价于「fstat 之后外部写者又往文件里追加」。
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
        # 读取必须在越限后立刻停下，而不是一路读到 6 MiB 的 EOF。
        assert consumed["bytes"] <= 1024 * 1024 + store.limits["maxImageBytes"], consumed["bytes"]
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)


@pytest.mark.unit
def test_a_temporarily_unreadable_tool_still_holds_its_slot(tmp_path, monkeypatch):
    """List absence is not proof of absence: a locked record must keep its slot."""
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
    # 这一轮它读不出来，所以不会出现在列表里……
    assert store.list_items() == []
    # ……但它还在盘上，名额必须照占，否则上限会被悄悄突破。
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
