from pathlib import Path

from PIL import Image

from main_routers.pngtuber_importers.pngtube_remix import _layer_role, _metadata


def test_pngtube_remix_metadata_declares_format_and_layer_roles(tmp_path):
    layers = [
        {
            "image": Image.new("RGBA", (8, 8), (255, 0, 0, 255)),
            "name": "Open Mouth",
            "order": 0,
            "zindex": 0,
            "x": 0,
            "y": 0,
            "state": {
                "effective_should_talk": True,
                "effective_open_mouth": True,
            },
            "states": [],
        },
        {
            "image": Image.new("RGBA", (8, 8), (0, 0, 255, 255)),
            "name": "Blink Eye",
            "order": 1,
            "zindex": 1,
            "x": 0,
            "y": 0,
            "state": {
                "effective_should_blink": True,
                "effective_open_eyes": False,
            },
            "states": [],
        },
    ]

    metadata = _metadata({}, Path("avatar.pngRemix"), tmp_path, [], layers, (0, 0, 16, 16))

    assert metadata["format"] == "pngtube_remix_pngremix"
    assert metadata["runtime"] == "layered_canvas"
    assert [layer["role"] for layer in metadata["layers"]] == ["mouth", "eye"]


def test_pngtube_remix_layer_role_falls_back_to_generic_layer():
    assert _layer_role({"name": "hair", "state": {}}) == "layer"
