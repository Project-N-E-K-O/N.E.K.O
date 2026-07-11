import json
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import main_routers.pngtuber_router as pngtuber_router
from main_routers.pngtuber_importers import pngtube_remix


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_normalize_pngtuber_config_preserves_mobile_layout_fields():
    result = pngtuber_router._normalize_pngtuber_config(
        "avatar",
        {
            "model_type": "pngtuber",
            "pngtuber": {
                "idle_image": "idle.png",
                "scale": 1.25,
                "offset_x": 12,
                "offset_y": -24,
                "mobile_scale": 0.8,
                "mobile_offset_x": 3,
                "mobile_offset_y": -6,
            },
        },
    )

    assert result["idle_image"] == "/user_pngtuber/avatar/idle.png"
    assert result["scale"] == 1.25
    assert result["offset_x"] == 12
    assert result["offset_y"] == -24
    assert result["mobile_scale"] == 0.8
    assert result["mobile_offset_x"] == 3
    assert result["mobile_offset_y"] == -6


def test_normalize_pngtuber_config_defaults_mobile_scale_from_desktop_scale_string():
    result = pngtuber_router._normalize_pngtuber_config(
        "avatar",
        {
            "model_type": "pngtuber",
            "pngtuber": {
                "idle_image": "idle.png",
                "scale": "0.75",
            },
        },
    )

    assert result["mobile_scale"] == 0.75
    assert result["mobile_offset_x"] == 0
    assert result["mobile_offset_y"] == 0


@pytest.mark.parametrize("scale", ["nan", "inf", "-inf"])
def test_normalize_pngtuber_config_defaults_mobile_scale_for_non_finite_desktop_scale(scale):
    result = pngtuber_router._normalize_pngtuber_config(
        "avatar",
        {
            "model_type": "pngtuber",
            "pngtuber": {
                "idle_image": "idle.png",
                "scale": scale,
            },
        },
    )

    assert result["mobile_scale"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"folder": "avatar(1)"},
        {"url": "/user_pngtuber/avatar(1)/model.json"},
    ],
)
async def test_delete_pngtuber_model_preserves_existing_folder_name(monkeypatch, tmp_path, payload):
    target_dir = tmp_path / "avatar(1)"
    target_dir.mkdir()
    (target_dir / "model.json").write_text('{"model_type":"pngtuber"}', encoding="utf-8")
    config_manager = SimpleNamespace(
        pngtuber_dir=tmp_path,
        ensure_pngtuber_directory=lambda: True,
    )
    monkeypatch.setattr(pngtuber_router, "get_config_manager", lambda: config_manager)

    response = await pngtuber_router.delete_pngtuber_model(payload)
    body = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert body["success"] is True
    assert not target_dir.exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"folder": "avatar(1)/nested"},
        {"url": "/user_pngtuber/../avatar(1)/model.json"},
        {"url": "/user_pngtuber/avatar(1)/nested/model.json"},
    ],
)
async def test_delete_pngtuber_model_rejects_non_folder_keys(monkeypatch, tmp_path, payload):
    config_manager = SimpleNamespace(
        pngtuber_dir=tmp_path,
        ensure_pngtuber_directory=lambda: True,
    )
    monkeypatch.setattr(pngtuber_router, "get_config_manager", lambda: config_manager)

    response = await pngtuber_router.delete_pngtuber_model(payload)
    body = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 400
    assert body["success"] is False


class FakeUploadFile:
    def __init__(self, filename: str, data: bytes = b"fake"):
        self.filename = filename
        self._data = data
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._data):
            return b""
        if size is None or size < 0:
            size = len(self._data) - self._offset
        chunk = self._data[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk


async def test_single_file_pngtuber_upload_uses_filename_before_default_collision(monkeypatch, tmp_path):
    (tmp_path / "pngtuber_model").mkdir()
    config_manager = SimpleNamespace(
        pngtuber_dir=tmp_path,
        ensure_pngtuber_directory=lambda: True,
    )
    monkeypatch.setattr(pngtuber_router, "get_config_manager", lambda: config_manager)

    def fake_import(package_dir, fallback_model_name):
        (package_dir / "idle.png").write_bytes(b"png")
        return SimpleNamespace(
            source_format="pngtube_remix_pngremix",
            model_name=fallback_model_name,
            model_json={
                "name": fallback_model_name,
                "model_type": "pngtuber",
                "pngtuber": {
                    "idle_image": "idle.png",
                    "talking_image": "idle.png",
                },
            },
            warnings=[],
            message="ok",
        )

    monkeypatch.setattr(pngtuber_router, "import_pngtuber_package", fake_import)

    response = await pngtuber_router.upload_pngtuber_model([FakeUploadFile("yui03.pngRemix")])
    body = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert body["success"] is True
    assert body["folder"] == "yui03"
    assert (tmp_path / "yui03" / "model.json").is_file()


def test_yui03_pngremix_metadata_preserves_official_follow_fields(tmp_path):
    remix_file = PROJECT_ROOT / "yui03.pngRemix"
    if not remix_file.is_file():
        pytest.skip("local yui03.pngRemix fixture is not present")

    remix_data = pngtube_remix.load_variant_file(remix_file.read_bytes())
    layers = pngtube_remix._prepare_layers(remix_data)
    bounds = pngtube_remix._bounds_for_layers(layers)
    metadata = pngtube_remix._metadata(remix_data, remix_file, tmp_path, [], layers, bounds)
    states = [((layer.get("states") or [{}])[0] or {}) for layer in metadata["layers"]]

    assert metadata["adapter_version"] == 2
    assert any("eye_follow" in state for state in states) is False
    assert sum(1 for state in states if state.get("follow_type") == 0) == 37
    assert sum(1 for state in states if state.get("follow_type2") == 0) == 19
    assert sum(1 for state in states if state.get("follow_type3") == 0) == 0
    assert metadata["settings"]["states"][0]["state_param_mc"]
    assert metadata["settings"]["states"][0]["state_param_mo"]
    assert metadata["runtime_features"]["layer_motion"] is True
    assert metadata["runtime_features"]["sprite_sheet_animation"] is True
    assert metadata["runtime_features"]["mesh_deformation"] is False
    assert metadata["runtime_features"]["physics_v2"] is True
    assert metadata["state_catalog"][0]["state_index"] == 0
    assert metadata["emotion_mappings"]["neutral"]["state_index"] == 0
    assert "mesh geometry missing; physics metadata preserved only" in metadata["runtime_features"]["unsupported_features"]
    assert metadata["capabilities"]["mesh"] is True
    assert metadata["capabilities"]["mesh_metadata"] is True
    assert metadata["capabilities"]["mesh_runtime"] is False
    assert max(len(state.get("parent_chain") or []) for state in states) == 4
    for field in ("pos_x_min", "pos_x_max", "pos_y_min", "pos_y_max", "rot_min", "rot_max", "phys_eff"):
        assert all(field in state for state in states)
    for field in ("tip_point", "mesh_phys_x", "mesh_phys_y", "use_object_pos"):
        assert all(field in state for state in states)
    assert all((state.get("mesh") or {}).get("valid") is False for state in states)


def test_pngtube_remix_metadata_marks_real_mesh_geometry_runtime(tmp_path):
    image = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    remix_data = {
        "sprites_array": [
            {
                "sprite_id": 1,
                "sprite_name": "mesh layer",
                "img": buffer.getvalue(),
                "states": [
                    {
                        "visible": True,
                        "position": (8, 8),
                        "scale": (1, 1),
                        "mesh": {
                            "vertices": [(0, 0), (16, 0), (0, 16)],
                            "uvs": [(0, 0), (1, 0), (0, 1)],
                            "triangles": [0, 1, 2],
                            "bindings": [{"bone": "tip", "weight": 1}],
                        },
                        "tip_point": (0.5, 0),
                        "mesh_phys_x": 60,
                        "mesh_phys_y": 40,
                    }
                ],
            }
        ],
        "image_manager_data": [],
        "settings_dict": {},
        "input_array": [],
    }

    layers = pngtube_remix._prepare_layers(remix_data)
    bounds = pngtube_remix._bounds_for_layers(layers)
    metadata = pngtube_remix._metadata(remix_data, Path("mesh.pngRemix"), tmp_path, [], layers, bounds)
    mesh = metadata["layers"][0]["mesh"]

    assert metadata["adapter_version"] == 2
    assert metadata["capabilities"]["mesh_metadata"] is True
    assert metadata["capabilities"]["mesh_runtime"] is True
    assert metadata["runtime_features"]["mesh_deformation"] is True
    assert metadata["runtime_features"]["physics_v2"] is True
    assert mesh["valid"] is True
    assert mesh["vertices"] == [[0.0, 0.0], [16.0, 0.0], [0.0, 16.0]]
    assert mesh["uvs"] == [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
    assert mesh["triangles"] == [[0, 1, 2]]
    assert mesh["source_fields"]["vertices"] == "state.mesh.vertices"


def test_pngtube_remix_metadata_maps_five_state_packages_to_emotion_fallbacks(tmp_path):
    image = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    remix_data = {
        "sprites_array": [
            {
                "sprite_id": 1,
                "sprite_name": "five states",
                "img": buffer.getvalue(),
                "states": [
                    {"visible": True, "position": (8, 8), "scale": (1, 1)}
                    for _ in range(5)
                ],
            }
        ],
        "image_manager_data": [],
        "settings_dict": {"states": [{} for _ in range(5)]},
        "input_array": [
            {
                "__object__": "InputEventKey",
                "properties": {
                    "keycode": code,
                    "physical_keycode": code,
                    "ctrl_pressed": True,
                },
            }
            for code in range(49, 54)
        ],
    }

    layers = pngtube_remix._prepare_layers(remix_data)
    bounds = pngtube_remix._bounds_for_layers(layers)
    metadata = pngtube_remix._metadata(remix_data, Path("five.pngRemix"), tmp_path, [], layers, bounds)

    assert metadata["state_count"] == 5
    assert [item["hotkey"] for item in metadata["state_catalog"]] == ["Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5"]
    assert metadata["emotion_mappings"]["neutral"]["state_index"] == 0
    assert metadata["emotion_mappings"]["happy"]["state_index"] == 1
    assert metadata["emotion_mappings"]["sad"]["state_index"] == 2
    assert metadata["emotion_mappings"]["angry"]["state_index"] == 3
    assert metadata["emotion_mappings"]["surprised"]["state_index"] == 4


def test_pngtube_remix_metadata_counts_global_states_and_nested_hotkey_labels(tmp_path):
    image = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    remix_data = {
        "sprites_array": [
            {
                "sprite_id": 1,
                "sprite_name": "single state layer",
                "img": buffer.getvalue(),
                "states": [
                    {"visible": True, "position": (8, 8), "scale": (1, 1)}
                ],
            }
        ],
        "image_manager_data": [],
        "settings_dict": {"states": [{}, {}, {"name": "Angry"}]},
        "input_array": [
            {
                "hot_key": {
                    "properties": {
                        "keycode": 49,
                        "physical_keycode": 49,
                        "ctrl_pressed": True,
                        "state_name": "Happy",
                    }
                }
            },
            {},
            {},
            {
                "hot_key": {
                    "properties": {
                        "keycode": 52,
                        "physical_keycode": 52,
                        "label": "Surprised",
                    }
                }
            },
        ],
    }

    layers = pngtube_remix._prepare_layers(remix_data)
    bounds = pngtube_remix._bounds_for_layers(layers)
    metadata = pngtube_remix._metadata(remix_data, Path("global-states.pngRemix"), tmp_path, [], layers, bounds)

    assert metadata["state_count"] == 4
    assert metadata["state_catalog"][0]["name"] == "Happy"
    assert metadata["state_catalog"][2]["name"] == "Angry"
    assert metadata["state_catalog"][3]["name"] == "Surprised"
    assert metadata["emotion_mappings"]["happy"]["state_index"] == 0
    assert metadata["emotion_mappings"]["angry"]["state_index"] == 2
    assert metadata["emotion_mappings"]["surprised"]["state_index"] == 3


def test_pngtube_remix_keeps_layers_visible_only_in_non_default_states(tmp_path):
    def png_bytes(color):
        image = Image.new("RGBA", (8, 8), color)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    remix_data = {
        "sprites_array": [
            {
                "sprite_id": 1,
                "sprite_name": "base",
                "img": png_bytes((255, 0, 0, 255)),
                "states": [
                    {"visible": True, "position": (4, 4), "scale": (1, 1), "z_index": 0},
                    {"visible": True, "position": (4, 4), "scale": (1, 1), "z_index": 0},
                ],
            },
            {
                "sprite_id": 2,
                "sprite_name": "expression-only",
                "img": png_bytes((0, 255, 0, 255)),
                "states": [
                    {"visible": False, "position": (20, 4), "scale": (1, 1), "z_index": 10},
                    {"visible": True, "position": (20, 4), "scale": (1, 1), "z_index": 10},
                ],
            },
        ],
        "image_manager_data": [],
        "settings_dict": {"states": [{}, {}]},
        "input_array": [],
    }

    layers = pngtube_remix._prepare_layers(remix_data)
    expression_layer = next(layer for layer in layers if layer["name"] == "expression-only")

    assert {layer["name"] for layer in layers} == {"base", "expression-only"}
    assert expression_layer["state"]["visible"] is False
    assert pngtube_remix._layer_visible_for_state(expression_layer, "idle") is False

    bounds = pngtube_remix._bounds_for_layers(layers)
    metadata = pngtube_remix._metadata(remix_data, Path("expression.pngRemix"), tmp_path, [], layers, bounds)
    metadata_layer = next(layer for layer in metadata["layers"] if layer["name"] == "expression-only")
    states = {state["state_index"]: state for state in metadata_layer["states"]}
    assert states[0]["visible"] is False
    assert states[1]["visible"] is True
    assert states[0]["folder"] is not True
    assert states[1]["folder"] is not True


def test_pngtube_remix_child_layers_inherit_parent_z_index(tmp_path):
    def png_bytes(color):
        image = Image.new("RGBA", (8, 8), color)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    remix_data = {
        "sprites_array": [
            {
                "sprite_id": "face",
                "sprite_name": "face",
                "img": png_bytes((255, 220, 220, 255)),
                "states": [{"visible": True, "position": (4, 4), "scale": (1, 1), "z_index": 0}],
            },
            {
                "sprite_id": "back-hair",
                "sprite_name": "back-hair",
                "img": png_bytes((0, 0, 255, 255)),
                "states": [{"visible": True, "position": (4, 4), "scale": (1, 1), "z_index": -1}],
            },
            {
                "sprite_id": "braid",
                "sprite_name": "braid",
                "parent_id": "back-hair",
                "img": png_bytes((0, 255, 255, 255)),
                "states": [{"visible": True, "position": (4, 4), "scale": (1, 1), "z_index": 0}],
            },
        ],
        "image_manager_data": [],
        "settings_dict": {"states": [{}]},
        "input_array": [],
    }

    layers = pngtube_remix._prepare_layers(remix_data)
    by_name = {layer["name"]: layer for layer in layers}
    # Under the #2130 z-order model, "zindex" keeps each sprite's own raw z while
    # "effective_zindex" carries the parent-inherited value that drives draw order.
    assert by_name["back-hair"]["zindex"] == -1
    assert by_name["braid"]["zindex"] == 0
    assert by_name["braid"]["effective_zindex"] == -1
    assert [layer["name"] for layer in sorted(layers, key=lambda item: (item["effective_zindex"], item["order"]))] == [
        "back-hair",
        "braid",
        "face",
    ]

    bounds = pngtube_remix._bounds_for_layers(layers)
    metadata = pngtube_remix._metadata(remix_data, Path("relative-z.pngRemix"), tmp_path, [], layers, bounds)
    metadata_by_name = {layer["name"]: layer for layer in metadata["layers"]}
    assert metadata_by_name["braid"]["states"][0]["z_index"] == 0
    assert metadata_by_name["braid"]["states"][0]["effective_z_index"] == -1


def test_pngtube_remix_root_layers_do_not_inherit_anonymous_z_index(tmp_path):
    def png_bytes(color):
        image = Image.new("RGBA", (8, 8), color)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    remix_data = {
        "sprites_array": [
            {
                "sprite_name": "anonymous",
                "img": png_bytes((0, 0, 255, 255)),
                "states": [{"visible": True, "position": (4, 4), "scale": (1, 1), "z_index": 50}],
            },
            {
                "sprite_id": "root",
                "sprite_name": "root",
                "img": png_bytes((255, 0, 0, 255)),
                "states": [{"visible": True, "position": (4, 4), "scale": (1, 1), "z_index": 1}],
            },
        ],
        "image_manager_data": [],
        "settings_dict": {"states": [{}]},
        "input_array": [],
    }

    layers = pngtube_remix._prepare_layers(remix_data)
    by_name = {layer["name"]: layer for layer in layers}

    assert by_name["root"]["zindex"] == 1


def test_local_orange_yukiri_fixture_exposes_state_emotion_mapping(tmp_path):
    remix_files = sorted(PROJECT_ROOT.glob("*251004.pngRemix"))
    if not remix_files:
        pytest.skip("local orange yukiri pngRemix fixture is not present")

    remix_file = remix_files[0]
    remix_data = pngtube_remix.load_variant_file(remix_file.read_bytes())
    layers = pngtube_remix._prepare_layers(remix_data)
    bounds = pngtube_remix._bounds_for_layers(layers)
    metadata = pngtube_remix._metadata(remix_data, remix_file, tmp_path, [], layers, bounds)

    assert metadata["state_count"] >= 5
    assert metadata["hotkeys"][0]["key"] == "Ctrl+1"
    assert metadata["emotion_mappings"]["happy"]["state_index"] == 1
    assert metadata["emotion_mappings"]["surprised"]["state_index"] == 4


def test_pngtube_remix_importer_migrates_legacy_mouse_follow_fields():
    sprite = {"updated_follow_movement": False}
    state = {
        "look_at_mouse_pos": -14,
        "look_at_mouse_pos_y": 6,
        "mouse_rotation": -12,
        "mouse_rotation_max": 18,
        "mouse_scale_x": 0.2,
        "mouse_scale_y": -0.3,
    }

    migrated = pngtube_remix._with_updated_follow_fields(sprite, state)

    assert migrated["pos_x_min"] == -14
    assert migrated["pos_x_max"] == 14
    assert migrated["pos_y_min"] == -6
    assert migrated["pos_y_max"] == 6
    assert migrated["pos_invert_x"] is True
    assert "pos_invert_y" not in migrated
    assert migrated["rot_min"] == -12
    assert migrated["rot_max"] == 18
    assert migrated["scale_x_min"] == -0.2
    assert migrated["scale_x_max"] == 0.2
    assert migrated["scale_y_min"] == -0.3
    assert migrated["scale_y_max"] == 0.3


def test_pngtube_remix_importer_keeps_updated_follow_fields():
    sprite = {"updated_follow_movement": True}
    state = {
        "look_at_mouse_pos": 99,
        "pos_x_min": -2,
        "pos_x_max": 4,
    }

    migrated = pngtube_remix._with_updated_follow_fields(sprite, state)

    assert migrated["pos_x_min"] == -2
    assert migrated["pos_x_max"] == 4
    assert migrated["updated_follow_movement"] is True
