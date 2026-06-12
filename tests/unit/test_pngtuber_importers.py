import base64
import io
import json
import struct
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from main_routers.pngtuber_router import router
from main_routers.shared_state import init_shared_state
from utils.config_manager import get_config_manager


def _png_base64(color, size=(12, 12)):
    image = Image.new("RGBA", size, color)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _png_bytes(color, size=(12, 12)):
    image = Image.new("RGBA", size, color)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _sheet_png_bytes(left_color, right_color, frame_size=(4, 4)):
    image = Image.new("RGBA", (frame_size[0] * 2, frame_size[1]), (0, 0, 0, 0))
    left = Image.new("RGBA", frame_size, left_color)
    right = Image.new("RGBA", frame_size, right_color)
    image.alpha_composite(left, (0, 0))
    image.alpha_composite(right, (frame_size[0], 0))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _pad4(raw: bytes) -> bytes:
    return raw + (b"\x00" * ((4 - len(raw) % 4) % 4))


def _godot_string_payload(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<I", len(raw)) + _pad4(raw)


def _godot_variant(value) -> bytes:
    if value is None:
        return struct.pack("<I", 0)
    if isinstance(value, bool):
        return struct.pack("<II", 1, 1 if value else 0)
    if isinstance(value, int):
        return struct.pack("<Ii", 2, value)
    if isinstance(value, float):
        return struct.pack("<If", 3, value)
    if isinstance(value, str):
        return struct.pack("<I", 4) + _godot_string_payload(value)
    if isinstance(value, bytes):
        return struct.pack("<II", 29, len(value)) + _pad4(value)
    if isinstance(value, tuple) and len(value) == 2:
        return struct.pack("<Iff", 5, float(value[0]), float(value[1]))
    if isinstance(value, list):
        return struct.pack("<II", 28, len(value)) + b"".join(_godot_variant(item) for item in value)
    if isinstance(value, dict):
        chunks = [struct.pack("<II", 27, len(value))]
        for key, item in value.items():
            chunks.append(_godot_variant(str(key)))
            chunks.append(_godot_variant(item))
        return b"".join(chunks)
    raise TypeError(f"Unsupported test variant value: {value!r}")


def _godot_variant_file(value) -> bytes:
    payload = _godot_variant(value)
    return struct.pack("<I", len(payload)) + payload


def _state(position=(0, 0), *, z_index=0, should_talk=False, open_mouth=False, state_count=1, x_amp=0, y_amp=0, y_frq=0, folder=False, offset=(0, 0), hframes=1, frames=1, frame=0, animation_speed=1, non_animated_sheet=False):
    states = []
    for index in range(state_count):
        states.append({
            "visible": True,
            "xFrq": 0,
            "xAmp": x_amp,
            "yFrq": y_frq,
            "yAmp": y_amp,
            "folder": folder,
            "position": (position[0] + index, position[1] + index),
            "offset": offset,
            "scale": (1, 1),
            "rotation": 0.0,
            "z_index": z_index,
            "should_talk": should_talk,
            "open_mouth": open_mouth,
            "should_blink": False,
            "open_eyes": True,
            "physics": bool(x_amp or y_amp),
            "wiggle": False,
            "wiggle_amp": 0,
            "wiggle_freq": 0,
            "flip_sprite_h": False,
            "flip_sprite_v": False,
            "hframes": hframes,
            "frames": frames,
            "frame": frame,
            "animation_speed": animation_speed,
            "non_animated_sheet": non_animated_sheet,
        })
    return states


def _sprite(name, image, *, should_talk=False, open_mouth=False, position=(0, 0), offset=(0, 0), z_index=0, state_count=1, x_amp=0, y_amp=0, y_frq=0, parent_id=None, is_asset=False, was_active_before=True, hframes=1, frames=1, frame=0, animation_speed=1, non_animated_sheet=False):
    sprite_id = abs(hash(name)) % 100000
    return {
        "img": image,
        "states": _state(position, offset=offset, z_index=z_index, should_talk=should_talk, open_mouth=open_mouth, state_count=state_count, x_amp=x_amp, y_amp=y_amp, y_frq=y_frq, hframes=hframes, frames=frames, frame=frame, animation_speed=animation_speed, non_animated_sheet=non_animated_sheet),
        "sprite_name": name,
        "sprite_id": sprite_id,
        "parent_id": parent_id,
        "sprite_type": "Sprite2D",
        "is_asset": is_asset,
        "was_active_before": was_active_before,
    }


def _folder_sprite(name, *, position=(0, 0), offset=(0, 0), parent_id=None, is_asset=False, was_active_before=True, state_count=1):
    sprite_id = abs(hash(name)) % 100000
    return {
        "states": _state(position, offset=offset, state_count=state_count, folder=True),
        "sprite_name": name,
        "sprite_id": sprite_id,
        "parent_id": parent_id,
        "sprite_type": "Node2D",
        "is_asset": is_asset,
        "was_active_before": was_active_before,
    }


def _open_multipart_folder_files(folder: Path):
    files = []
    opened = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(folder.parent).as_posix()
        handle = path.open("rb")
        opened.append(handle)
        files.append(("files", (rel, handle, "application/octet-stream")))
    return files, opened


def _post_folder(client, folder: Path):
    files, opened = _open_multipart_folder_files(folder)
    try:
        return client.post("/api/model/pngtuber/upload_model", files=files)
    finally:
        for handle in opened:
            handle.close()


def _make_client(clean_user_data_dir):
    init_shared_state({}, None, None, get_config_manager(), None)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_pngtuber_plus_save_folder_converts_to_simple_package(clean_user_data_dir, tmp_path):
    client = _make_client(clean_user_data_dir)
    imported_dir = get_config_manager().pngtuber_dir / "夏凌岚"
    if imported_dir.exists():
        import shutil
        shutil.rmtree(imported_dir)
    folder = tmp_path / "夏凌岚"
    folder.mkdir()
    save_data = {
        "0": {
            "type": "sprite",
            "identification": 100,
            "parentId": "",
            "path": "user://plus/body.png",
            "pos": "Vector2(0, 0)",
            "zindex": 0,
            "showTalk": 0,
            "showBlink": 0,
            "frames": 1,
            "imageData": _png_base64((255, 0, 0, 255), (20, 20)),
        },
        "1": {
            "type": "sprite",
            "identification": 101,
            "parentId": "100",
            "path": "user://plus/closed.png",
            "pos": "Vector2(2, 3)",
            "zindex": 1,
            "showTalk": 1,
            "showBlink": 0,
            "frames": 1,
            "imageData": _png_base64((0, 255, 0, 255), (4, 4)),
        },
        "2": {
            "type": "sprite",
            "identification": 102,
            "parentId": "100",
            "path": "user://plus/open.png",
            "pos": "Vector2(2, 3)",
            "zindex": 1,
            "showTalk": 2,
            "showBlink": 0,
            "frames": 1,
            "imageData": _png_base64((0, 0, 255, 255), (4, 4)),
        },
    }
    (folder / "夏凌岚.save").write_text(json.dumps(save_data), encoding="utf-8")

    response = _post_folder(client, folder)

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert data["source_format"] == "pngtuber_plus_save"
    assert data["folder"] == "夏凌岚"
    assert data["pngtuber"]["idle_image"] == "/user_pngtuber/夏凌岚/idle.png"
    assert data["pngtuber"]["talking_image"] == "/user_pngtuber/夏凌岚/talking.png"
    assert "PNGTuber Plus" in data["message"]

    assert (imported_dir / "model.json").exists()
    assert (imported_dir / "idle.png").exists()
    assert (imported_dir / "talking.png").exists()
    assert (imported_dir / "source.save").exists()
    assert (imported_dir / "metadata.pngtuber-plus.json").exists()
    assert (imported_dir / "layers").is_dir()

    idle = Image.open(imported_dir / "idle.png").convert("RGBA")
    talking = Image.open(imported_dir / "talking.png").convert("RGBA")
    assert idle.getpixel((2, 3)) == (0, 255, 0, 255)
    assert talking.getpixel((2, 3)) == (0, 0, 255, 255)

    model_json = json.loads((imported_dir / "model.json").read_text(encoding="utf-8"))
    assert model_json["pngtuber"]["adapter"] == "layered_canvas_v1"
    assert model_json["pngtuber"]["layered_metadata"].endswith("/metadata.pngtuber-plus.json")
    metadata = json.loads((imported_dir / "metadata.pngtuber-plus.json").read_text(encoding="utf-8"))
    assert metadata["runtime"] == "layered_canvas"
    assert metadata["capabilities"]["speech_layers"] is True
    assert metadata["capabilities"]["blink_layers"] is True
    assert all((imported_dir / layer["image"]).exists() for layer in metadata["layers"])

    list_response = client.get("/api/model/pngtuber/models")
    assert list_response.status_code == 200
    models = list_response.json()["models"]
    assert any(m["folder"] == "夏凌岚" and m["source_format"] == "pngtuber_plus_save" for m in models)
    plus_model = next(m for m in models if m["folder"] == "夏凌岚")
    assert plus_model["pngtuber"]["adapter"] == "layered_canvas_v1"
    assert plus_model["pngtuber"]["layered_metadata"].endswith("/metadata.pngtuber-plus.json")


def test_pngtube_remix_upload_converts_to_simple_package(clean_user_data_dir, tmp_path):
    client = _make_client(clean_user_data_dir)
    imported_dir = get_config_manager().pngtuber_dir / "橘雪梨251004"
    if imported_dir.exists():
        import shutil
        shutil.rmtree(imported_dir)
    folder = tmp_path / "橘雪梨"
    folder.mkdir()
    remix = {
        "sprites_array": [],
        "settings_dict": {},
        "input_array": [
            {"properties": {"keycode": 49, "ctrl_pressed": True, "shift_pressed": False, "alt_pressed": False, "meta_pressed": False}},
            {"properties": {"keycode": 50, "ctrl_pressed": True, "shift_pressed": False, "alt_pressed": False, "meta_pressed": False}},
        ],
    }
    inactive_asset = _folder_sprite("inactive-action", is_asset=True, was_active_before=False, state_count=2)
    expression_folder = _folder_sprite("expression-state-two", state_count=2)
    expression_folder["states"][0]["visible"] = False
    expression_folder["states"][1]["visible"] = True
    expression_folder["states"][1]["should_talk"] = True
    expression_folder["states"][1]["open_mouth"] = True
    anchored_folder = _folder_sprite("anchored-expression", position=(100, 100), offset=(-80, -90), state_count=2)
    remix["sprites_array"] = [
            _sprite("body", _png_bytes((255, 0, 0, 255), (20, 20)), position=(0, 0), z_index=0, state_count=2, y_amp=2, y_frq=0.02),
            _sprite("closed", _png_bytes((0, 255, 0, 255), (4, 4)), should_talk=True, open_mouth=False, position=(2, 3), z_index=1, state_count=2),
            _sprite("open", _png_bytes((0, 0, 255, 255), (4, 4)), should_talk=True, open_mouth=True, position=(2, 3), z_index=1, state_count=2),
            inactive_asset,
            _sprite("inactive-hand", _png_bytes((255, 0, 255, 255), (4, 4)), position=(12, 13), z_index=5, state_count=2, parent_id=inactive_asset["sprite_id"]),
            expression_folder,
            _sprite("expression-child", _png_bytes((255, 255, 0, 255), (4, 4)), position=(24, 4), z_index=2, state_count=2, parent_id=expression_folder["sprite_id"]),
            anchored_folder,
            _sprite("anchored-effect", _png_bytes((0, 255, 255, 255), (4, 4)), position=(10, 20), offset=(1, 2), z_index=3, state_count=2, parent_id=anchored_folder["sprite_id"]),
            _sprite("sheet-highlight", _sheet_png_bytes((10, 20, 30, 255), (200, 210, 220, 255)), position=(40, 50), z_index=4, state_count=2, hframes=2, frames=2, frame=0, animation_speed=4),
    ]
    (folder / "橘雪梨251004.pngRemix").write_bytes(_godot_variant_file(remix))

    response = _post_folder(client, folder)

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["source_format"] == "pngtube_remix_pngremix"
    assert data["folder"] == "橘雪梨251004"
    assert data["pngtuber"]["idle_image"] == "/user_pngtuber/橘雪梨251004/idle.png"
    assert data["pngtuber"]["talking_image"] == "/user_pngtuber/橘雪梨251004/talking.png"
    assert "PNGTubeRemix" in data["message"]

    assert (imported_dir / "model.json").exists()
    assert (imported_dir / "idle.png").exists()
    assert (imported_dir / "talking.png").exists()
    assert (imported_dir / "source.pngRemix").exists()
    assert (imported_dir / "metadata.pngtube-remix.json").exists()
    assert (imported_dir / "layers").is_dir()

    idle = Image.open(imported_dir / "idle.png").convert("RGBA")
    talking = Image.open(imported_dir / "talking.png").convert("RGBA")
    assert idle.getpixel((12, 13)) == (0, 255, 0, 255)
    assert talking.getpixel((12, 13)) == (0, 0, 255, 255)
    assert idle.getpixel((14, 15)) != (255, 0, 255, 255)

    model_json = json.loads((imported_dir / "model.json").read_text(encoding="utf-8"))
    assert model_json["pngtuber"]["adapter"] == "layered_canvas_v1"
    assert model_json["pngtuber"]["layered_metadata"].endswith("/metadata.pngtube-remix.json")
    metadata = json.loads((imported_dir / "metadata.pngtube-remix.json").read_text(encoding="utf-8"))
    assert metadata["runtime"] == "layered_canvas"
    assert metadata["capabilities"]["speech_layers"] is True
    assert metadata["capabilities"]["blink_layers"] is True
    assert metadata["capabilities"]["motion_layers"] is True
    assert metadata["capabilities"]["physics"] is True
    assert metadata["state_count"] == 2
    assert metadata["hotkeys"][0]["key"] == "Ctrl+1"
    assert metadata["hotkeys"][1]["state_index"] == 1
    assert len(metadata["layers"][0]["states"]) == 2
    assert metadata["layers"][0]["states"][0]["yAmp"] == 2
    assert metadata["layers"][0]["states"][0]["physics"] is True
    assert metadata["layers"][0]["base_scale"] == [1.0, 1.0]
    assert metadata["layers"][0]["base_flip_h"] is False
    assert metadata["layers"][0]["states"][0]["flip_sprite_h"] is False
    inactive_layer = next(layer for layer in metadata["layers"] if layer["name"] == "inactive-hand")
    assert inactive_layer["inactive_asset_ancestor"] is True
    expression_layer = next(layer for layer in metadata["layers"] if layer["name"] == "expression-child")
    assert expression_layer["ancestor_visible"] is False
    assert expression_layer["state"]["ancestor_visible"] is False
    assert expression_layer["states"][0]["ancestor_visible"] is False
    assert expression_layer["states"][1]["ancestor_visible"] is True
    assert expression_layer["states"][1]["effective_should_talk"] is True
    assert expression_layer["states"][1]["effective_open_mouth"] is True
    anchored_layer = next(layer for layer in metadata["layers"] if layer["name"] == "anchored-effect")
    assert anchored_layer["states"][0]["center_x"] == 31
    assert anchored_layer["states"][0]["center_y"] == 32
    assert anchored_layer["states"][0]["x"] == anchored_layer["x"]
    assert anchored_layer["states"][0]["y"] == anchored_layer["y"]
    assert anchored_layer["parent_chain"][0]["name"] == "anchored-effect"
    assert anchored_layer["parent_chain"][1]["name"] == "anchored-expression"
    assert anchored_layer["parent_chain"][1]["position"] == [100.0, 100.0]
    assert anchored_layer["parent_chain"][1]["offset"] == [-80.0, -90.0]
    assert anchored_layer["states"][0]["parent_chain"][1]["offset"] == [-80.0, -90.0]
    sheet_layer = next(layer for layer in metadata["layers"] if layer["name"] == "sheet-highlight")
    assert sheet_layer["image_width"] == 8
    assert sheet_layer["image_height"] == 4
    assert sheet_layer["width"] == 4
    assert sheet_layer["height"] == 4
    assert sheet_layer["states"][0]["frame_width"] == 4
    assert sheet_layer["states"][0]["frame_height"] == 4
    assert sheet_layer["states"][0]["hframes"] == 2
    assert sheet_layer["states"][0]["frames"] == 2
    assert sheet_layer["states"][0]["x"] == sheet_layer["x"]
    assert sheet_layer["states"][0]["y"] == sheet_layer["y"]
    assert all((imported_dir / layer["image"]).exists() for layer in metadata["layers"])


def test_damaged_pngtube_remix_upload_has_actionable_error(clean_user_data_dir, tmp_path):
    client = _make_client(clean_user_data_dir)
    folder = tmp_path / "broken-remix"
    folder.mkdir()
    (folder / "broken.pngRemix").write_bytes(b"sprites_array mouth position scale rotation" + b"\x89PNG\r\n\x1a\n" + b"fake")

    response = _post_folder(client, folder)

    assert response.status_code == 400
    data = response.json()
    assert data["source_format"] == "pngtube_remix_pngremix"
    assert "PNGTubeRemix" in data["error"]
    assert "转换失败" in data["error"]
    assert "model.json" not in data["error"]


def test_veadotube_upload_is_recognized_with_actionable_error(clean_user_data_dir, tmp_path):
    client = _make_client(clean_user_data_dir)
    folder = tmp_path / "veado-model"
    folder.mkdir()
    (folder / "avatar.veado").write_bytes(b"unknown veadotube model")

    response = _post_folder(client, folder)

    assert response.status_code == 400
    data = response.json()
    assert data["source_format"] == "veadotube"
    assert "veadotube" in data["error"]
    assert "model.json" not in data["error"]
