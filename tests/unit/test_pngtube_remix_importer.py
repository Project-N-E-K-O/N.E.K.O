import io

from PIL import Image

from main_routers.pngtuber_importers.pngtube_remix import (
    _asset_actions,
    _bounds_for_layers,
    _float_value,
    _metadata,
    _prepare_layers,
    _state_positions_for_sprite,
)


def _png_bytes():
    buffer = io.BytesIO()
    Image.new("RGBA", (1, 1), (255, 0, 0, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _key_event(keycode):
    return {
        "__object__": "InputEventKey",
        "properties": {
            "keycode": keycode,
            "physical_keycode": keycode,
            "key_label": keycode,
            "ctrl_pressed": False,
            "shift_pressed": False,
            "alt_pressed": False,
            "meta_pressed": False,
        },
    }


def test_metadata_keeps_state_names_without_enabling_original_state_hotkeys(tmp_path):
    remix_data = {
        "input_array": [
            {
                "state_name": "正常",
                "hot_key": {
                    "__object__": "InputEventKey",
                    "properties": {
                        "keycode": 49,
                        "physical_keycode": 49,
                        "ctrl_pressed": True,
                        "shift_pressed": False,
                        "alt_pressed": False,
                        "meta_pressed": False,
                    },
                },
            }
        ],
        "sprites_array": [
            {
                "sprite_id": 10,
                "sprite_name": "face",
                "img": _png_bytes(),
                "states": [{"visible": True, "folder": False, "z_index": 0, "position": [0, 0], "offset": [0, 0]}],
            }
        ],
    }
    layers = _prepare_layers(remix_data)
    metadata = _metadata(remix_data, tmp_path / "model.pngRemix", tmp_path, [], layers, _bounds_for_layers(layers))

    assert metadata["hotkeys"] == []
    assert metadata["capabilities"]["hotkeys"] is False
    assert metadata["state_hotkeys"] == [
        {
            "state_index": 0,
            "state_name": "正常",
            "key": "Ctrl+1",
            "keycode": 49,
            "ctrl": True,
            "shift": False,
            "alt": False,
            "meta": False,
        }
    ]


def test_asset_actions_preserve_pngtube_remix_show_and_hide_events():
    f5 = 4194336
    f6 = 4194337
    actions = _asset_actions([
        {
            "sprite_id": 100,
            "asset_events": {
                "show": _key_event(f6),
                "hide": [_key_event(f5)],
            },
        },
        {
            "sprite_id": 101,
            "asset_events": {
                "show": _key_event(f5),
                "hide": [_key_event(f6)],
            },
        },
    ])

    assert actions == [
        {
            "key": "F6",
            "keycode": f6,
            "ctrl": False,
            "shift": False,
            "alt": False,
            "meta": False,
            "show_sprite_ids": [100],
            "hide_sprite_ids": [101],
        },
        {
            "key": "F5",
            "keycode": f5,
            "ctrl": False,
            "shift": False,
            "alt": False,
            "meta": False,
            "show_sprite_ids": [101],
            "hide_sprite_ids": [100],
        },
    ]


def test_float_value_rejects_non_finite_numbers():
    assert _float_value(float("nan"), 7.0) == 7.0
    assert _float_value(float("inf"), 7.0) == 7.0
    assert _float_value("-inf", 7.0) == 7.0
    assert _float_value("3.5") == 3.5


def test_state_positions_include_parent_relative_z_index():
    parent = {
        "sprite_id": 1,
        "sprite_name": "body",
        "states": [
            {"z_index": -1, "position": [0, 0], "offset": [0, 0]},
            {"z_index": -1, "position": [0, 0], "offset": [0, 0]},
        ],
    }
    child = {
        "sprite_id": 2,
        "sprite_name": "sleeve",
        "parent_id": 1,
        "states": [
            {"z_index": -2, "position": [0, 0], "offset": [0, 0]},
            {"z_index": -1, "position": [0, 0], "offset": [0, 0]},
        ],
    }

    states = _state_positions_for_sprite(child, {1: parent, 2: child})

    assert [state["z_index"] for state in states] == [-2, -1]
    assert [state["effective_z_index"] for state in states] == [-3, -2]


def test_prepare_layers_keeps_sprite_visible_in_non_default_expression_state():
    layers = _prepare_layers({
        "sprites_array": [
            {
                "sprite_id": 10,
                "sprite_name": "expression",
                "img": _png_bytes(),
                "states": [
                    {"visible": False, "folder": False, "z_index": 1, "position": [0, 0], "offset": [0, 0]},
                    {"visible": True, "folder": False, "z_index": 3, "position": [0, 0], "offset": [0, 0]},
                ],
            }
        ]
    })

    assert [layer["name"] for layer in layers] == ["expression"]
    assert layers[0]["state"]["visible"] is False
    assert layers[0]["states"][1]["visible"] is True
    assert layers[0]["states"][1]["effective_z_index"] == 3


def test_bounds_include_layers_visible_only_in_non_default_state(tmp_path):
    layers = _prepare_layers({
        "sprites_array": [
            {
                "sprite_id": 1,
                "sprite_name": "body",
                "img": _png_bytes(),
                "states": [
                    {"visible": True, "folder": False, "z_index": 0, "position": [0, 0], "offset": [0, 0]},
                    {"visible": True, "folder": False, "z_index": 0, "position": [0, 0], "offset": [0, 0]},
                ],
            },
            {
                "sprite_id": 2,
                "sprite_name": "expression",
                "img": _png_bytes(),
                "states": [
                    {"visible": False, "folder": False, "z_index": 1, "position": [0, 0], "offset": [0, 0]},
                    {"visible": True, "folder": False, "z_index": 1, "position": [100, 0], "offset": [0, 0]},
                ],
            },
        ]
    })

    _, _, width, _ = _bounds_for_layers(layers)
    metadata = _metadata({}, tmp_path / "model.pngRemix", tmp_path, [], layers, _bounds_for_layers(layers))
    expression = next(layer for layer in metadata["layers"] if layer["name"] == "expression")
    visible_expression_state = expression["states"][1]

    assert width >= 101
    assert 0 <= visible_expression_state["x"] <= metadata["canvas"]["width"] - visible_expression_state["frame_width"]


def test_asset_action_layers_are_exported_and_included_in_bounds(tmp_path):
    f6 = 4194337
    layers = _prepare_layers({
        "sprites_array": [
            {
                "sprite_id": 1,
                "sprite_name": "body",
                "img": _png_bytes(),
                "states": [
                    {"visible": True, "folder": False, "z_index": 0, "position": [0, 0], "offset": [0, 0]},
                ],
            },
            {
                "sprite_id": 2,
                "sprite_name": "action_prop",
                "is_asset": True,
                "was_active_before": False,
                "saved_event": _key_event(f6),
                "img": _png_bytes(),
                "states": [
                    {"visible": False, "folder": False, "z_index": 1, "position": [100, 0], "offset": [0, 0]},
                ],
            },
        ]
    })

    _, _, width, _ = _bounds_for_layers(layers)
    metadata = _metadata({}, tmp_path / "model.pngRemix", tmp_path, [], layers, _bounds_for_layers(layers))
    action_prop = next(layer for layer in metadata["layers"] if layer["name"] == "action_prop")

    assert width >= 101
    assert metadata["asset_actions"][0]["show_sprite_ids"] == [2]
    assert action_prop["inactive_asset_ancestor"] is True
    assert 0 <= action_prop["x"] <= metadata["canvas"]["width"] - action_prop["width"]
