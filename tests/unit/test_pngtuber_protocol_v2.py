import json
from pathlib import Path

from main_routers.pngtuber_importers import import_pngtuber_package
from main_routers.pngtuber_protocol import (
    NEKO_PNGTUBER_ADAPTER,
    NEKO_PNGTUBER_METADATA_FORMAT,
    NEKO_PNGTUBER_PACKAGE_FORMAT,
    infer_pngtuber_metadata_from_idle,
    is_neko_pngtuber_v2_model,
    validate_neko_pngtuber_v2_package,
)
from main_routers.pngtuber_router import _normalize_pngtuber_config, _validate_model_package


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PNGTUBER_ROUTER_PATH = PROJECT_ROOT / "main_routers" / "pngtuber_router.py"


def _write_minimal_neko_pngtuber_v2_package(package_dir):
    assets_dir = package_dir / "assets"
    layers_dir = assets_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)
    for rel_path in [
        "assets/idle.png",
        "assets/talking.png",
        "assets/layers/00_body.png",
        "assets/layers/01_eye_open.png",
        "assets/layers/02_eye_closed.png",
        "assets/layers/03_mouth_closed.png",
        "assets/layers/04_mouth_open.png",
    ]:
        (package_dir / rel_path).write_bytes(b"png")

    model_json = {
        "format": NEKO_PNGTUBER_PACKAGE_FORMAT,
        "name": "NEKO PNGTuber v2 Minimal Sample",
        "version": 1,
        "model_type": "pngtuber",
        "source_format": "neko_pngtuber_v2",
        "pngtuber": {
            "adapter": NEKO_PNGTUBER_ADAPTER,
            "idle_image": "assets/idle.png",
            "talking_image": "assets/talking.png",
            "metadata": "metadata.neko-pngtuber.v2.json",
            "layered_metadata": "metadata.neko-pngtuber.v2.json",
            "scale": 1,
            "offset_x": 0,
            "offset_y": 0,
            "mirror": False,
        },
    }
    metadata = {
        "format": NEKO_PNGTUBER_METADATA_FORMAT,
        "runtime": "neko_layered_canvas",
        "canvas": {"width": 192, "height": 192},
        "state_count": 2,
        "emotions": {
            "happy": {"state_index": 1, "duration_ms": 3200},
            "sad": {"state_index": 1, "duration_ms": 3200},
        },
        "layers": [
            {"id": "body", "role": "body", "order": 0, "image": "assets/layers/00_body.png"},
            {"id": "eye_open", "role": "eye", "order": 1, "image": "assets/layers/01_eye_open.png", "showBlink": 1},
            {"id": "eye_closed", "role": "eye", "order": 2, "image": "assets/layers/02_eye_closed.png", "showBlink": 2},
            {"id": "mouth_closed", "role": "mouth", "order": 3, "image": "assets/layers/03_mouth_closed.png", "showTalk": 1},
            {"id": "mouth_open", "role": "mouth", "order": 4, "image": "assets/layers/04_mouth_open.png", "showTalk": 2},
        ],
    }
    (package_dir / "model.json").write_text(json.dumps(model_json), encoding="utf-8")
    (package_dir / "metadata.neko-pngtuber.v2.json").write_text(json.dumps(metadata), encoding="utf-8")
    return model_json


def test_neko_pngtuber_v2_sample_package_validates(tmp_path):
    package_dir = tmp_path / "sample"
    package_dir.mkdir()
    model_json = _write_minimal_neko_pngtuber_v2_package(package_dir)

    assert is_neko_pngtuber_v2_model(model_json) is True
    assert validate_neko_pngtuber_v2_package(package_dir, model_json) == (True, "")
    assert _validate_model_package(package_dir, model_json) == (True, "")


def test_neko_pngtuber_v2_sample_import_and_normalize_contract(tmp_path):
    package_dir = tmp_path / "sample"
    package_dir.mkdir()
    _write_minimal_neko_pngtuber_v2_package(package_dir)

    imported = import_pngtuber_package(package_dir, "fallback")
    normalized = _normalize_pngtuber_config("sample_v2", imported.model_json)

    assert imported.source_format == NEKO_PNGTUBER_PACKAGE_FORMAT
    assert imported.model_name == "NEKO PNGTuber v2 Minimal Sample"
    assert normalized["adapter"] == NEKO_PNGTUBER_ADAPTER
    assert normalized["protocol"] == NEKO_PNGTUBER_METADATA_FORMAT
    assert normalized["metadata"] == "/user_pngtuber/sample_v2/metadata.neko-pngtuber.v2.json"
    assert normalized["layered_metadata"] == normalized["metadata"]
    assert normalized["idle_image"] == "/user_pngtuber/sample_v2/assets/idle.png"


def test_pngtuber_upload_writes_source_format_before_normalizing_config():
    source = PNGTUBER_ROUTER_PATH.read_text(encoding="utf-8")
    upload_block = source[
        source.index("        source_format = str(model_json.get(\"source_format\") or import_result.source_format)"):
        source.index("        with open(temp_dir / \"model.json\"", source.index("        source_format = str(model_json.get(\"source_format\") or import_result.source_format)"))
    ]

    assert upload_block.index("model_json[\"source_format\"] = source_format") < upload_block.index("normalized_config = _normalize_pngtuber_config")
    assert "pngtuber_config[\"source_format\"] = source_format" in upload_block


def test_legacy_layered_canvas_package_still_validates(tmp_path):
    package_dir = tmp_path / "legacy"
    package_dir.mkdir()
    (package_dir / "idle.png").write_bytes(b"png")
    (package_dir / "talking.png").write_bytes(b"png")
    (package_dir / "metadata.pngtube-remix.json").write_text(
        json.dumps({
            "runtime": "layered_canvas",
            "canvas": {"width": 128, "height": 128},
            "layers": [{"id": "body", "image": "idle.png"}],
        }),
        encoding="utf-8",
    )
    model_json = {
        "name": "Legacy PNGTubeRemix",
        "model_type": "pngtuber",
        "source_format": "pngtube_remix_pngremix",
        "pngtuber": {
            "adapter": "layered_canvas_v1",
            "idle_image": "idle.png",
            "talking_image": "talking.png",
            "layered_metadata": "metadata.pngtube-remix.json",
        },
    }

    assert is_neko_pngtuber_v2_model(model_json) is False
    assert _validate_model_package(package_dir, model_json) == (True, "")
    normalized = _normalize_pngtuber_config("legacy", model_json)
    assert normalized["adapter"] == "layered_canvas_v1"
    assert normalized["protocol"] == ""
    assert normalized["metadata"] == "/user_pngtuber/legacy/metadata.pngtube-remix.json"


def test_neko_pngtuber_v2_validator_rejects_missing_layer_asset(tmp_path):
    package_dir = tmp_path / "sample"
    package_dir.mkdir()
    model_json = _write_minimal_neko_pngtuber_v2_package(package_dir)
    missing = package_dir / "assets" / "layers" / "04_mouth_open.png"
    missing.unlink()

    ok, error = validate_neko_pngtuber_v2_package(package_dir, model_json)

    assert ok is False
    assert "metadata.layers[4].image" in error


def test_neko_pngtuber_v2_validator_rejects_non_integer_dimensions(tmp_path):
    package_dir = tmp_path / "sample"
    package_dir.mkdir()
    model_json = _write_minimal_neko_pngtuber_v2_package(package_dir)
    metadata_path = package_dir / "metadata.neko-pngtuber.v2.json"

    invalid_values = [
        ("canvas", "width", 1.5, "canvas"),
        ("canvas", "height", "2", "canvas"),
        ("root", "state_count", True, "state_count"),
    ]
    for section, key, value, expected_error in invalid_values:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if section == "canvas":
            metadata["canvas"][key] = value
        else:
            metadata[key] = value
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        ok, error = validate_neko_pngtuber_v2_package(package_dir, model_json)

        assert ok is False, f"{key} accepted invalid value {value!r}"
        assert expected_error in error

        _write_minimal_neko_pngtuber_v2_package(package_dir)


def test_infer_pngtuber_metadata_rejects_unsafe_user_model_folder(tmp_path):
    pngtuber_dir = tmp_path / "pngtuber"
    outside_dir = tmp_path / "outside"
    pngtuber_dir.mkdir()
    outside_dir.mkdir()
    (outside_dir / "metadata.neko-pngtuber.v2.json").write_text("{}", encoding="utf-8")

    class ConfigManager:
        pass

    config_manager = ConfigManager()
    config_manager.pngtuber_dir = pngtuber_dir

    inferred = infer_pngtuber_metadata_from_idle(
        "/user_pngtuber/../outside/idle.png",
        config_manager,
    )

    assert inferred == ""


def test_neko_pngtuber_v2_validator_rejects_unsafe_package_paths(tmp_path):
    package_dir = tmp_path / "sample"
    package_dir.mkdir()

    unsafe_values = [
        "/assets/idle.png",
        "//evil.example/assets/idle.png",
        "assets/../idle.png",
        "assets//idle.png",
        "assets/./idle.png",
        "https://example.invalid/idle.png",
    ]
    for index, unsafe_path in enumerate(unsafe_values):
        model_json = _write_minimal_neko_pngtuber_v2_package(package_dir)
        model_json["pngtuber"]["idle_image"] = unsafe_path

        ok, error = validate_neko_pngtuber_v2_package(package_dir, model_json)

        assert ok is False, f"unsafe path accepted at case {index}: {unsafe_path}"
        assert "pngtuber.idle_image" in error


def test_neko_pngtuber_v2_validator_requires_emotion_mapping(tmp_path):
    package_dir = tmp_path / "sample"
    package_dir.mkdir()
    model_json = _write_minimal_neko_pngtuber_v2_package(package_dir)
    metadata_path = package_dir / "metadata.neko-pngtuber.v2.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.pop("emotions")
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    ok, error = validate_neko_pngtuber_v2_package(package_dir, model_json)

    assert ok is False
    assert "emotions" in error
