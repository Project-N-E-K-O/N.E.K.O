import base64
import io
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from main_routers.pngtuber_importers import PNGTuberImportError, import_pngtuber_package
from main_routers.pngtuber_importers.pngtuber_plus import import_pngtuber_plus_save


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _png_data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _solid(size: tuple[int, int], color: tuple[int, int, int, int]) -> Image.Image:
    return Image.new("RGBA", size, color)


def test_pngtuber_plus_import_preserves_godot_center_offsets_blink_and_sheets(tmp_path):
    sheet = Image.new("RGBA", (20, 10), (0, 0, 0, 0))
    sheet.alpha_composite(_solid((10, 10), (0, 0, 255, 255)), (0, 0))
    sheet.alpha_composite(_solid((10, 10), (255, 0, 255, 255)), (10, 0))
    save_data = {
        "0": {
            "type": "sprite",
            "identification": 1,
            "parentId": None,
            "pos": "Vector2(0, 0)",
            "offset": "Vector2(0, 0)",
            "zindex": -1,
            "showTalk": 0,
            "showBlink": 0,
            "frames": 1,
            "imageData": _png_data_url(_solid((100, 80), (255, 0, 0, 255))),
        },
        "1": {
            "type": "sprite",
            "identification": 2,
            "parentId": 1,
            "pos": "Vector2(10, -20)",
            "offset": "Vector2(5, 0)",
            "zindex": 0,
            "showTalk": 0,
            "showBlink": 0,
            "frames": 2,
            "animSpeed": 12,
            "imageData": _png_data_url(sheet),
        },
        "2": {
            "type": "sprite",
            "identification": 3,
            "parentId": 1,
            "pos": "Vector2(0, 0)",
            "offset": "Vector2(0, 0)",
            "zindex": 1,
            "showTalk": 0,
            "showBlink": 1,
            "frames": 1,
            "imageData": _png_data_url(_solid((10, 10), (0, 255, 0, 255))),
        },
        "3": {
            "type": "sprite",
            "identification": 4,
            "parentId": 1,
            "pos": "Vector2(0, 0)",
            "offset": "Vector2(0, 0)",
            "zindex": 2,
            "showTalk": 0,
            "showBlink": 2,
            "frames": 1,
            "imageData": _png_data_url(_solid((10, 10), (255, 255, 0, 255))),
        },
    }
    save_file = tmp_path / "avatar.save"
    save_file.write_text(json.dumps(save_data), encoding="utf-8")

    result = import_pngtuber_plus_save(tmp_path, save_file, "fallback")

    assert result["source_format"] == "pngtuber_plus_save"
    metadata = json.loads((tmp_path / "metadata.pngtuber-plus.json").read_text(encoding="utf-8"))
    assert metadata["adapter_version"] == 2
    assert metadata["state_count"] == 10
    assert metadata["capabilities"]["hotkeys"] is True
    assert metadata["runtime_features"]["sprite_sheet_animation"] is True
    assert metadata["runtime_features"]["plus_transform_stack"] is True
    assert metadata["runtime_features"]["clip_children_rect"] is False
    assert [item["key"] for item in metadata["hotkeys"]] == ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]
    assert metadata["settings"]["states"][0]["name"] == "Costume 1"
    assert metadata["plus_settings"]["settings_loaded"] is False
    assert metadata["plus_settings"]["blinkSpeed"] == 1.0
    assert metadata["blink"]["source_blink_chance"] == 200

    layers = {layer["identification"]: layer for layer in metadata["layers"]}
    body = layers["1"]
    child_sheet = layers["2"]
    assert body["x"] == 0
    assert body["y"] == 0
    assert body["local_position"] == [0.0, 0.0]
    assert body["node_origin"] == [50.0, 40.0]
    assert body["draw_offset"] == [-50.0, -40.0]
    assert body["plus_transform"] is True
    assert child_sheet["x"] == 60
    assert child_sheet["y"] == 15
    assert child_sheet["local_position"] == [10.0, -20.0]
    assert child_sheet["node_origin"] == [60.0, 20.0]
    assert child_sheet["sprite_offset"] == [5.0, 0.0]
    assert child_sheet["draw_offset"] == [0.0, -5.0]
    assert child_sheet["width"] == 10
    assert child_sheet["height"] == 10
    assert child_sheet["image_width"] == 20
    assert child_sheet["state"]["hframes"] == 2
    assert child_sheet["state"]["animation_speed"] > 0
    assert child_sheet["state"]["source_anim_speed"] == 12
    assert len(child_sheet["states"]) == 10
    assert child_sheet["states"][0]["costume_number"] == 1

    exported_sheet = Image.open(tmp_path / child_sheet["image"]).convert("RGBA")
    assert exported_sheet.size == (20, 10)

    idle = Image.open(tmp_path / "idle.png").convert("RGBA")
    assert idle.getpixel((46, 36)) == (0, 255, 0, 255)


def test_pngtuber_plus_import_maps_costume_ancestors_hotkeys_and_toggles(tmp_path):
    save_data = {
        "0": {
            "type": "sprite",
            "identification": 10,
            "parentId": None,
            "pos": "Vector2(0, 0)",
            "offset": "Vector2(0, 0)",
            "zindex": 0,
            "costumeLayers": "[1, 0]",
            "toggle": "T",
            "clipped": True,
            "imageData": _png_data_url(_solid((20, 20), (255, 0, 0, 255))),
        },
        "1": {
            "type": "sprite",
            "identification": 11,
            "parentId": 10,
            "pos": "Vector2(0, 0)",
            "offset": "Vector2(0, 0)",
            "zindex": 1,
            "costumeLayers": "[1, 1]",
            "imageData": _png_data_url(_solid((10, 10), (0, 255, 0, 255))),
        },
        "2": {
            "type": "sprite",
            "identification": 12,
            "parentId": None,
            "pos": "Vector2(30, 0)",
            "offset": "Vector2(0, 0)",
            "zindex": 2,
            "costumeLayers": "[0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]",
            "toggle": "null",
            "imageData": _png_data_url(_solid((10, 10), (0, 0, 255, 255))),
        },
    }
    (tmp_path / "settings.pngtp").write_text(
        json.dumps({
            "costumeKeys": ["1", "null", "", "1", "5"],
            "blinkSpeed": 2.0,
            "blinkChance": 120,
            "bounceOnCostumeChange": True,
        }),
        encoding="utf-8",
    )
    save_file = tmp_path / "avatar.save"
    save_file.write_text(json.dumps(save_data), encoding="utf-8")

    import_pngtuber_plus_save(tmp_path, save_file, "fallback")

    metadata = json.loads((tmp_path / "metadata.pngtuber-plus.json").read_text(encoding="utf-8"))
    layers = {layer["identification"]: layer for layer in metadata["layers"]}

    assert metadata["state_count"] == 10
    assert metadata["toggles"] == {"T": ["10"]}
    assert metadata["runtime_features"]["clip_children_rect"] is True
    assert metadata["runtime_features"]["costume_change_bounce"] is True
    assert metadata["plus_settings"]["settings_loaded"] is True
    assert metadata["plus_settings"]["blinkSpeed"] == 2.0
    assert metadata["plus_settings"]["blinkChance"] == 120
    assert metadata["plus_settings"]["bounceOnCostumeChange"] is True
    assert metadata["blink"]["source_blink_speed"] == 2.0
    assert metadata["blink"]["source_blink_chance"] == 120
    assert metadata["blink"]["interval_min_ms"] == 14000
    assert metadata["blink"]["interval_max_ms"] == 18000
    assert [item["key"] for item in metadata["hotkeys"]] == ["1", "5", "6", "7", "8", "9", "0"]
    assert [item["state_index"] for item in metadata["hotkeys"]] == [0, 4, 5, 6, 7, 8, 9]

    root = layers["10"]
    child = layers["11"]
    costume_two = layers["12"]
    assert root["costumeLayers"] == [1, 0, 1, 1, 1, 1, 1, 1, 1, 1]
    assert root["toggle"] == "T"
    assert root["clipped"] is True
    assert child["parent_chain"] == ["10"]
    assert child["states"][0]["visible"] is True
    assert child["states"][0]["ancestor_visible"] is True
    assert child["states"][1]["visible"] is True
    assert child["states"][1]["ancestor_visible"] is False
    assert costume_two["costumeLayers"] == [0, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    assert costume_two["states"][0]["visible"] is False
    assert costume_two["states"][1]["visible"] is True


def test_pngtuber_plus_import_resolves_nested_save_relative_assets(tmp_path):
    root_assets = tmp_path / "assets"
    nested_assets = tmp_path / "avatar" / "assets"
    root_assets.mkdir()
    nested_assets.mkdir(parents=True)
    _solid((8, 8), (255, 0, 0, 255)).save(root_assets / "layer.png")
    _solid((8, 8), (0, 255, 0, 255)).save(nested_assets / "layer.png")
    save_data = {
        "0": {
            "type": "sprite",
            "identification": 1,
            "parentId": None,
            "pos": "Vector2(0, 0)",
            "offset": "Vector2(0, 0)",
            "path": "assets/layer.png",
        }
    }
    (tmp_path / "avatar" / "avatar.save").write_text(json.dumps(save_data), encoding="utf-8")

    result = import_pngtuber_package(tmp_path, "avatar")

    assert result.source_format == "pngtuber_plus_save"
    idle = Image.open(tmp_path / "idle.png").convert("RGBA")
    assert idle.getpixel((4, 4)) == (0, 255, 0, 255)


def test_pngtuber_plus_import_rejects_external_paths_outside_package(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}_outside.png"
    _solid((10, 10), (0, 255, 0, 255)).save(outside)
    save_data = {
        "0": {
            "type": "sprite",
            "identification": 1,
            "parentId": None,
            "pos": "Vector2(0, 0)",
            "offset": "Vector2(0, 0)",
            "zindex": 0,
            "imageData": _png_data_url(_solid((10, 10), (255, 0, 0, 255))),
        },
        "1": {
            "type": "sprite",
            "identification": 2,
            "parentId": None,
            "pos": "Vector2(0, 0)",
            "offset": "Vector2(0, 0)",
            "zindex": 1,
            "path": f"../{outside.name}",
        },
    }
    save_file = tmp_path / "avatar.save"
    save_file.write_text(json.dumps(save_data), encoding="utf-8")

    import_pngtuber_plus_save(tmp_path, save_file, "fallback")

    metadata = json.loads((tmp_path / "metadata.pngtuber-plus.json").read_text(encoding="utf-8"))
    assert [layer["identification"] for layer in metadata["layers"]] == ["1"]
    idle = Image.open(tmp_path / "idle.png").convert("RGBA")
    assert idle.getpixel((5, 5)) == (255, 0, 0, 255)


def test_pngtuber_plus_import_skips_bad_image_data_and_accepts_data_urls(tmp_path):
    save_data = {
        "0": {
            "type": "sprite",
            "identification": 1,
            "parentId": None,
            "pos": "Vector2(0, 0)",
            "offset": "Vector2(0, 0)",
            "zindex": 0,
            "imageData": "data:image/png;base64,not-valid-image-data",
        },
        "1": {
            "type": "sprite",
            "identification": 2,
            "parentId": None,
            "pos": "Vector2(0, 0)",
            "offset": "Vector2(0, 0)",
            "zindex": 1,
            "imageData": f"data:image/png;base64,{_png_data_url(_solid((10, 10), (0, 0, 255, 255)))}",
        },
    }
    save_file = tmp_path / "avatar.save"
    save_file.write_text(json.dumps(save_data), encoding="utf-8")

    import_pngtuber_plus_save(tmp_path, save_file, "fallback")

    metadata = json.loads((tmp_path / "metadata.pngtuber-plus.json").read_text(encoding="utf-8"))
    assert [layer["identification"] for layer in metadata["layers"]] == ["2"]
    idle = Image.open(tmp_path / "idle.png").convert("RGBA")
    assert idle.getpixel((5, 5)) == (0, 0, 255, 255)


def test_pngtuber_plus_import_sanitizes_non_finite_numbers(tmp_path):
    save_data = {
        "0": {
            "type": "sprite",
            "identification": 1,
            "parentId": None,
            "pos": "Vector2(0, 0)",
            "offset": "Vector2(0, 0)",
            "rotDrag": "Infinity",
            "stretchAmount": "NaN",
            "imageData": _png_data_url(_solid((10, 10), (255, 0, 0, 255))),
        }
    }
    save_file = tmp_path / "avatar.save"
    save_file.write_text(json.dumps(save_data), encoding="utf-8")

    import_pngtuber_plus_save(tmp_path, save_file, "fallback")

    metadata = json.loads((tmp_path / "metadata.pngtuber-plus.json").read_text(encoding="utf-8"))
    state = metadata["layers"][0]["state"]
    assert state["rdragStr"] == 0.0
    assert state["stretchAmount"] == 0.0


def test_pngtuber_plus_composite_inherits_parent_talk_visibility(tmp_path):
    save_data = {
        "0": {
            "type": "sprite",
            "identification": 1,
            "parentId": None,
            "pos": "Vector2(0, 0)",
            "offset": "Vector2(0, 0)",
            "zindex": -1,
            "showTalk": 0,
            "imageData": _png_data_url(_solid((30, 30), (0, 0, 255, 255))),
        },
        "1": {
            "type": "sprite",
            "identification": 2,
            "parentId": None,
            "pos": "Vector2(0, 0)",
            "offset": "Vector2(0, 0)",
            "zindex": 0,
            "showTalk": 1,
            "imageData": _png_data_url(_solid((20, 20), (255, 0, 0, 255))),
        },
        "2": {
            "type": "sprite",
            "identification": 3,
            "parentId": 2,
            "pos": "Vector2(0, 0)",
            "offset": "Vector2(0, 0)",
            "zindex": 1,
            "showTalk": 0,
            "imageData": _png_data_url(_solid((10, 10), (0, 255, 0, 255))),
        },
    }
    save_file = tmp_path / "avatar.save"
    save_file.write_text(json.dumps(save_data), encoding="utf-8")

    import_pngtuber_plus_save(tmp_path, save_file, "fallback")

    talking = Image.open(tmp_path / "talking.png").convert("RGBA")
    assert talking.getpixel((15, 15)) == (0, 0, 255, 255)


def test_pngtuber_plus_import_prefers_unique_root_save_matching_upload_name(tmp_path):
    save_data = {
        "0": {
            "type": "sprite",
            "identification": 1,
            "parentId": None,
            "pos": "Vector2(0, 0)",
            "offset": "Vector2(0, 0)",
            "imageData": _png_data_url(_solid((10, 10), (255, 0, 0, 255))),
        }
    }
    nested = tmp_path / "backup"
    nested.mkdir()
    (tmp_path / "avatar.save").write_text(json.dumps(save_data), encoding="utf-8")
    (nested / "older.save").write_text(json.dumps(save_data), encoding="utf-8")

    result = import_pngtuber_package(tmp_path, "avatar")

    assert result.source_format == "pngtuber_plus_save"
    assert result.model_name == "avatar"


def test_pngtuber_plus_import_rejects_ambiguous_multiple_save_files(tmp_path):
    save_data = {
        "0": {
            "type": "sprite",
            "identification": 1,
            "parentId": None,
            "pos": "Vector2(0, 0)",
            "offset": "Vector2(0, 0)",
            "imageData": _png_data_url(_solid((10, 10), (255, 0, 0, 255))),
        }
    }
    nested = tmp_path / "backup"
    nested.mkdir()
    (tmp_path / "one.save").write_text(json.dumps(save_data), encoding="utf-8")
    (nested / "two.save").write_text(json.dumps(save_data), encoding="utf-8")

    with pytest.raises(PNGTuberImportError) as exc_info:
        import_pngtuber_package(tmp_path, "avatar")

    assert exc_info.value.source_format == "pngtuber_plus_save"
    assert "多个 PNGTuber Plus .save" in str(exc_info.value)
    assert sorted(exc_info.value.warnings) == ["backup/two.save", "one.save"]


def test_pngtuber_plus_official_default_avatar_sample_when_available(tmp_path):
    official_save = PROJECT_ROOT.parent / "PNGTuber-Plus" / "autoload" / "defaultAvatar.save"
    if not official_save.exists():
        pytest.skip("sibling PNGTuber-Plus official defaultAvatar.save is not available")
    package_dir = tmp_path / "official"
    shutil.copytree(official_save.parent, package_dir)

    import_pngtuber_plus_save(package_dir, package_dir / "defaultAvatar.save", "official")

    metadata = json.loads((package_dir / "metadata.pngtuber-plus.json").read_text(encoding="utf-8"))
    assert metadata["source_format"] == "pngtuber_plus_save"
    assert metadata["state_count"] == 10
    assert metadata["runtime_features"]["plus_transform_stack"] is True
    assert "clip_children_rect" in metadata["runtime_features"]
    assert "toggles" in metadata
    assert len(metadata["layers"]) >= 1
    assert all(layer["plus_transform"] is True for layer in metadata["layers"])
    assert all(len(layer["costumeLayers"]) == 10 for layer in metadata["layers"])
    assert all("parent_chain" in layer for layer in metadata["layers"])
    assert any(layer["state"]["hframes"] >= 1 for layer in metadata["layers"])
    assert (package_dir / "idle.png").exists()
    assert (package_dir / "talking.png").exists()
