from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from utils.avatar_tool_store import (
    AvatarToolStore,
    AvatarToolStoreError,
    is_public_avatar_tool_resource_path,
)


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


def _mp3() -> bytes:
    return (
        Path(__file__).resolve().parents[2]
        / "static"
        / "sounds"
        / "avatar-tools"
        / "lollipop"
        / "bite.mp3"
    ).read_bytes()


def test_create_publishes_ordered_public_dto_but_keeps_meanings_private(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))

    item = store.create_tool(
        name="小羽毛",
        change_mode="click-advance",
        change_meanings=["像羽毛一样轻轻挠一下", "轻轻扫过脸颊"],
        default_image=_png(),
        change_images=[_png(size=(12, 9)), _png(size=(10, 11))],
    )

    assert item["id"].startswith("local-")
    assert item["name"] == "小羽毛"
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
            {"image": "change-000.png", "meaning": "像羽毛一样轻轻挠一下"},
            {"image": "change-001.png", "meaning": "轻轻扫过脸颊"},
        ],
    }
    assert store.list_items() == [item]
    assert not list(store.root.glob(".*.uploading"))


def test_create_publishes_optional_normal_sound_without_exposing_private_meanings(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))

    item = store.create_tool(
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


@pytest.mark.parametrize("audio, duration_limit, expected_code", [
    (b"not-an-mp3", 10_000, "audio_decode_failed"),
    (_mp3(), 10, "audio_too_long"),
])
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
        store.create_tool(
            name="bad sound",
            change_mode="press-swap",
            change_meanings=["meaning"],
            default_image=_png(),
            change_images=[_png()],
            normal_sound=audio,
        )

    assert raised.value.code == expected_code
    assert not store.root.exists() or not list(store.root.iterdir())


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
        store.create_tool(
            name="bad",
            change_mode="press-swap",
            change_meanings=["meaning"],
            default_image=data,
            change_images=[_png()],
        )

    assert raised.value.code == code
    assert not store.root.exists() or not list(store.root.iterdir())


def test_list_skips_bad_records_and_cleans_only_owned_upload_directories(tmp_path):
    root = tmp_path / "avatar_tools"
    root.mkdir()
    owned = root / ".local-12345678-1234-4123-8123-123456789abc.uploading"
    owned.mkdir()
    unrelated = root / ".keep-me"
    unrelated.mkdir()
    invalid = root / "local-12345678-1234-4123-8123-123456789abc"
    invalid.mkdir()
    (invalid / "record.json").write_text("{}", encoding="utf-8")

    store = AvatarToolStore(_ConfigManager(root))

    assert store.list_items() == []
    assert not owned.exists()
    assert unrelated.exists()


def test_public_resource_allowlist_rejects_private_and_unsafe_paths(tmp_path):
    root = tmp_path / "avatar_tools"
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    directory = root / tool_id
    directory.mkdir(parents=True)
    (directory / "change-000.png").write_bytes(_png())
    (directory / "change-015.png").write_bytes(_png())
    (directory / "normal.mp3").write_bytes(_mp3())
    (directory / "record.json").write_text("{}", encoding="utf-8")

    assert is_public_avatar_tool_resource_path(root, f"{tool_id}/change-000.png")
    assert is_public_avatar_tool_resource_path(root, f"{tool_id}/change-015.png")
    assert is_public_avatar_tool_resource_path(root, f"{tool_id}/normal.mp3")
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
        store.create_tool(
            name="two\nlines",
            change_mode="press-swap",
            change_meanings=["meaning"],
            default_image=_png(),
            change_images=[_png()],
        )

    assert raised.value.code == "name_invalid"


def test_create_counts_record_in_total_storage_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))
    default_image = _png()
    change_image = _png()
    store.limits["maxTotalBytes"] = len(default_image) + len(change_image)

    with pytest.raises(AvatarToolStoreError) as raised:
        store.create_tool(
            name="Feather",
            change_mode="press-swap",
            change_meanings=["gentle"],
            default_image=default_image,
            change_images=[change_image],
        )

    assert raised.value.code == "storage_limit_reached"
    assert not list(store.root.iterdir())


def test_press_swap_requires_exactly_one_change_item(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.avatar_tool_store.assert_cloudsave_writable", lambda *_a, **_k: None)
    store = AvatarToolStore(_ConfigManager(tmp_path / "avatar_tools"))

    with pytest.raises(AvatarToolStoreError) as raised:
        store.create_tool(
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
