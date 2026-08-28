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

    # 落盘记录的字段校验复用了表单校验器，但读取路径会把它归一化成
    # record_invalid —— 只有这样隔离判据才认得它，超限的道具才不会一边被列表
    # 隐藏、一边继续占着配额。原始字段码保留在异常链里。
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

    try:
        with pytest.raises(AvatarToolStoreError) as raised:
            store.read_record(tool["id"])
    finally:
        pass
    assert raised.value.code == "record_invalid"
    # 超限是确定性的不合法（不是这一轮读不到），所以要被隔离 —— 否则它既进不了
    # 公开目录，也没有任何界面入口能删掉，却一直挂着名额和配额。
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

    # 上一次更新已经发布了新 final，但清理 backup 失败，残留了旧版本。
    backup = store.root / f".{tool['id']}.backup"
    shutil.copytree(final, backup)
    (backup / "record.json").write_text(
        (backup / "record.json").read_text(encoding="utf-8").replace(
            "the version the user just saved", "the stale backup"
        ),
        encoding="utf-8",
    )
    # 让 backup 自洽（摘要对得上），否则它本来就会被判损坏而与本用例无关。
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
        # final 必须原样保留，绝不能被旧 backup 顶掉。
        assert final.is_dir()
        assert backup.is_dir(), "the backup was consumed despite an unproven final"
        # 恢复没走完，存储根必须留在待恢复状态。
        assert root_key in avatar_tool_store._RECOVERY_PENDING_ROOTS

        monkeypatch.undo()
        monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
        store.initialize()

        # 锁一放开，final 被证明有效，残留 backup 才被清掉。
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
        # 名额被占满，此时建不了第二个。
        with pytest.raises(AvatarToolStoreError) as raised:
            _create_tool(
                store, name="Second", change_mode="press-swap", change_meanings=["m"],
                default_image=_png(), change_images=[_png()],
            )
        assert raised.value.code == "tool_limit_reached"

        # 内容被篡改，消费点核验时证伪并隔离它。
        (store.root / damaged["id"] / "default.png").write_bytes(b"truncated")
        with pytest.raises(AvatarToolStoreError):
            store.get_detail(damaged["id"])
        assert damaged["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS[root_key]

        # 被证伪之后就不该再占着名额。
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
        # 这次挡的是资源文件，不是 record.json。
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

    # 中断的更新没有留下有效 final，而 .backup 本身是确定性损坏的。
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
        # 用户看不到它、也进不了它的编辑页，所以它不能继续扣着配额。
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
    # 中断更新的痕迹，让恢复会走到这个道具。
    (store.root / f".{tool['id']}.updating").mkdir()
    # 闭包被破坏：用户往目录里放了一个额外文件。
    (final / "notes.txt").write_bytes(b"something the user dropped in")

    root_key = store._root_key()
    try:
        store.initialize()
        assert tool["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS[root_key]
        # 关键：目录和用户放进去的文件都还在，恢复不替用户做删除决定。
        assert final.is_dir()
        assert (final / "notes.txt").is_file()
        assert (final / "default.png").is_file()
        # 但它不再占配额。
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
    # 上一次更新已经成功，只是清理 backup 失败，留下了旧版本。注意没有 .updating。
    backup = store.root / f".{tool['id']}.backup"
    shutil.copytree(final, backup)
    # 同步客户端往已发布目录里塞了个文件，闭包被破坏 —— final 因此被证伪。
    (final / "synced-note.txt").write_bytes(b"added by a sync client")

    root_key = store._root_key()
    try:
        store.initialize()
        # final 必须原样保留：既不能回滚成旧版本，也不能连带删掉用户的文件。
        assert final.is_dir()
        assert (final / "synced-note.txt").is_file()
        assert (final / "default.png").is_file()
        assert tool["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS[root_key]
        # 残留的 backup 该清掉，否则它一直占配额又没有任何入口能删。
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
    shutil.copytree(final, backup)  # 破坏之前先留下有效副本
    root_key = store._root_key()

    # 先让消费点核验发现损坏并把它隔离 —— 这才是恢复时需要解除的那个标记。
    (final / "default.png").write_bytes(b"truncated")
    try:
        with pytest.raises(AvatarToolStoreError):
            store.get_detail(tool["id"])
        assert tool["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS[root_key]

        # 中断证据：更新没走完，backup 才是该回到的状态。
        (store.root / f".{tool['id']}.updating").mkdir()
        store.initialize()
        assert final.is_dir()
        assert not backup.exists()
        # 回滚出来的这一份刚通过完整核验，不该背着隔离标记继续被列表跳过。
        assert tool["id"] not in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())
        assert [item["id"] for item in store.list_items()] == [tool["id"]]
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)


# --- 恢复状态空间穷举 ---
# 之前每一轮都是被 review 指出一个组合、修一个组合。这里把 final × backup ×
# .updating 的全部组合钉死，剩余缺陷一次暴露，而不是继续一条条等人喂。


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

    # 该道具只有留下 .updating 或 .backup 才会被恢复遍历到。
    visited = updating_present or backup_state != "missing"
    # 可以回滚，当且仅当没有 final 会被牺牲，或有 .updating 这个中断证据。
    may_restore = updating_present or final_state == "missing"
    restorable = backup_state == "valid" and may_restore

    try:
        store.initialize()

        assert not updating.exists(), "an interrupted staging directory was left behind"

        if final_state == "valid":
            # 有效的已发布目录任何情况下都不许被顶掉。
            assert final.is_dir()
            assert not (final / "intruder.txt").exists()
            assert [item["id"] for item in store.list_items()] == [tool_id]
            assert tool_id not in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())
        elif restorable:
            # 只有「没有 final 可牺牲」或「有中断证据」时才回滚，且回滚出来的
            # 那一份必须是干净的、不带隔离标记。
            assert final.is_dir()
            assert not (final / "intruder.txt").exists()
            assert tool_id not in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())
            assert [item["id"] for item in store.list_items()] == [tool_id]
        elif final_state == "condemned":
            # 被证伪但仍在盘上：保留用户的文件，只登记隔离。
            assert final.is_dir()
            assert (final / "intruder.txt").is_file(), "recovery destroyed the user's file"
            if visited:
                assert tool_id in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())
            assert store.list_items() == []
        else:
            assert not final.exists()
            assert store.list_items() == []

        if visited:
            # 没被用于回滚的 backup 一律清掉：它进不了公开目录、UI 也删不掉。
            assert not backup.exists(), "an unusable backup kept occupying the quota"

        # 配额不能被「看不见又删不掉」的东西挂住。即便恢复的遍历入口
        # （.updating / .backup）都不在，list_items 的轻量闭包核验也会把被证伪的
        # 道具隔离掉，所以这里一律归零。
        visible = store.list_items()
        expected_bytes = store._directory_bytes(final) if visible else 0
        assert store._current_storage_bytes() == expected_bytes
        if final_state == "condemned" and not restorable:
            # 没被回滚掉的被证伪 final 必须进隔离；被回滚的那份已经是有效版本。
            assert tool_id in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)


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
    # 不是暂存目录，只是一个同名的普通文件。
    (store.root / f".{tool['id']}.updating").write_bytes(b"not a staging directory")

    root_key = store._root_key()
    store.initialize()

    # 不得据此把 final 回滚成旧版本，也不得删掉用户放进去的文件。
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
        # 归一化之后隔离判据才认得它。
        assert raised.value.code == "record_invalid"
        assert raised.value.transient is False
        assert tool["id"] in avatar_tool_store._QUARANTINED_TOOL_IDS[root_key]
        assert store.list_items() == []
        # 界面上既看不到也删不掉，所以配额必须释放。
        assert store._current_storage_bytes() == 0
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)


# --- 隔离判据的输入空间穷举 ---
# 「什么算被证伪」最近连着出了两次边界（闭包不符、字段错误码）。这里把落盘记录
# 的各种损坏形态一次列全：被证伪的必须隔离并释放配额，读不出来的必须原样保留。

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
    "unknown-version": lambda d: _corrupt_record(d, lambda r: r.__setitem__("recordVersion", 3)),
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
        # 被证伪的道具在界面上看不到也删不掉，名额和配额都必须放开。
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
        # 锁一放开，道具原样回到列表，名额和配额都还在。
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

    # 上一次更新已经把新版本发布成 final，只是清理 backup 那步失败，旧版本残留。
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
    # 没有 .updating —— 上一次更新是走完了的，这个 backup 只是残留，不是回滚证据。
    assert not (store.root / f".{tool['id']}.updating").exists()

    real_stat, real_lstat = os.stat, os.lstat

    def flaky(real):
        def probe(path, *args, **kwargs):
            if str(path) == str(final):
                raise OSError(errno.EBUSY, "metadata temporarily unavailable")
            return real(path, *args, **kwargs)

        return probe

    root_key = store._root_key()
    # 两个都挡住：is_dir() 走 stat，is_symlink() 走 lstat。只挡一个的话，改回
    # is_dir() 的写法仍然读得到目录，这个用例就抓不到回归了。
    monkeypatch.setattr(os, "lstat", flaky(real_lstat))
    monkeypatch.setattr(os, "stat", flaky(real_stat))
    try:
        store.initialize()
        # 只是一次读不到元数据，final 必须原样还在。
        assert stat_module.S_ISDIR(real_lstat(str(final)).st_mode), (
            "the live final was rolled back on a transient probe failure"
        )
        assert stat_module.S_ISDIR(real_lstat(str(backup)).st_mode)
        assert root_key in avatar_tool_store._RECOVERY_PENDING_ROOTS

        monkeypatch.undo()
        monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
        store.initialize()

        # 元数据能读了，final 被证明有效，残留 backup 这才被清掉。
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

    # 根「里面」的软链接仍然一律拒绝：那才是能指到根外面去的那一类。
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
    # 读不到元数据必须报成暂时性失败：报 tool_not_found 会让启动恢复把这个健康
    # 道具判成「被证伪」，轻则隔离，重则在有中断证据时拿旧 backup 顶掉它。
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
        # 孤儿没被清掉，就不能宣称恢复完成 —— 否则本进程内不会再重试，它继续
        # 绕过配额计费并占着同一个 ID。
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
    # 少算一个名额就能建出第 65 个，等目录重新可读时已经超限了。名额检查排在
    # 配额检查之前，所以这里必须是名额拒绝，不能拿配额拒绝顶替。
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
    # 漏掉的字节会让 maxTotalBytes 形同虚设，所以算不准就必须拒绝写入。
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
    # 报成非 transient 的 record_invalid，等于告诉恢复「这份已经坏了」——它会隔离
    # 这个健康道具，有中断证据时还会拿旧 backup 顶掉它。
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

    # 先确认基线：能读到这个多余文件时，闭包不符是确定性的证伪。
    with pytest.raises(AvatarToolStoreError) as proven:
        store.read_record(tool["id"], verify_resources=True)
    assert proven.value.transient is False

    # 同一个文件，只是这一轮读不到 —— 结论必须从「被证伪」退回「暂时读不出来」。
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
        # 暂存目录里的文件是本进程刚写出来的，这里模拟网络盘在那一刻抖了一下。
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
    # 无论被哪一层拦下，都必须是「暂时不可用」而不是「你的记录坏了」，
    # 而且原记录必须完好无损 —— 一次读不到不能让用户丢掉已保存的那一份。
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

    # 基线：读得到就照常统计。
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

    # 正式目录被同步客户端或手工操作换成了别的东西。
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

        # 拿 backup 覆盖要先删掉占位的东西，那违反「恢复不替用户删除正式目录」。
        kind, _, _ = avatar_tool_store._probe_entry(final)
        assert kind != "absent", "recovery deleted whatever was sitting at the final path"
        assert kind != "dir", "the stale backup was published over a foreign occupant"
        # 而 backup 可能是这个道具仅存的副本，也不能顺手清掉。
        assert backup.is_dir(), "the only surviving copy was cleaned up"
        # 两条路都走不了，就必须留在待恢复状态，别宣称恢复完成。
        assert root_key in avatar_tool_store._RECOVERY_PENDING_ROOTS
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

    # 起点：正式目录不在（一次失败的回滚留下的状态），backup 是唯一副本 ——
    # 恢复据此获得「可以回滚」的授权。
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
        # 校验 backup 的中途，别的进程把正式目录建了出来。
        if not raced:
            raced.append(True)
            shutil.copytree(newest, final)
        return real_digest(path, maximum)

    monkeypatch.setattr(AvatarToolStore, "_file_digest", staticmethod(digest_and_race))
    root_key = store._root_key()
    try:
        store.initialize()
        assert raced, "the race never happened; this test proves nothing"

        # 授权是基于「正式目录不在」发出的。前提在校验期间变了，这一轮就必须弃权：
        # 既不能删掉刚出现的正式目录，也不能消耗掉 backup。
        assert backup.is_dir(), "the backup was consumed on a stale authorization"
        assert root_key in avatar_tool_store._RECOVERY_PENDING_ROOTS

        monkeypatch.undo()
        monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
        # 下一次操作重新确认前提：正式目录这次是有效的，于是它说了算，
        # 而 backup 退化成残留被清掉 —— 新发布的那一版没有被旧 backup 抹掉。
        assert store.get_detail(tool["id"])["changeItems"][0]["meaning"] == (
            "what the sync client just published"
        )
        assert not backup.exists()
    finally:
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)


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
        # 精确命中发布那一步（.updating -> final），不能按调用次序数：
        # atomic_write_json 写 record.json 时自己也会调 os.replace。
        # 让它在这里失败，正式目录已经改名成 .backup，只剩那一份副本。
        if str(dst) == str(final) and str(src).endswith(".updating"):
            in_rollback_window.append(True)
            raise OSError(errno.EIO, "publish failed")
        return real_replace(src, dst, **kwargs)

    def flaky_lstat(path, *args, **kwargs):
        # 只在回滚窗口里抖，否则更新还没走到那一步就被打断了。
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
        # 回滚该不该做判不出来时，不能当成「不用回滚」——那样道具会在本进程内
        # 一直消失，要等下次启动恢复才回来。
        assert root_key in avatar_tool_store._RECOVERY_PENDING_ROOTS
    finally:
        avatar_tool_store._RECOVERY_PENDING_ROOTS.discard(root_key)

