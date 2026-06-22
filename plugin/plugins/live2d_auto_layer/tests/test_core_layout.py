import importlib.util
import sys
import types
import zipfile
from pathlib import Path

import json
import pytest

from PIL import Image, ImageChops

from plugin.plugins.live2d_auto_layer.core.assembly import Live2DAssembler, classify_layer
from plugin.plugins.live2d_auto_layer.core.auto_rig import export_auto_rig_model, load_auto_rig_model
from plugin.plugins.live2d_auto_layer.core.importing import import_layer_source
from plugin.plugins.live2d_auto_layer.core.pngtuber import export_pngtuber_model, install_pngtuber_package
from plugin.plugins.live2d_auto_layer.core.pngtuber.export import _layer_role
from plugin.plugins.live2d_auto_layer.core.pipeline import process_image, process_layer_source
from plugin.plugins.live2d_auto_layer.core.auto_rig.template import classify_rig_group, infer_bindings
from main_routers.pngtuber_router import _validate_model_package


def _install_optional_cv2_stub() -> None:
    if importlib.util.find_spec("cv2") is None:
        sys.modules.setdefault("cv2", types.ModuleType("cv2"))


def test_legacy_core_imports_reexport_new_packages() -> None:
    _install_optional_cv2_stub()

    from plugin.plugins.live2d_auto_layer.core import anime_face as legacy_anime_face
    from plugin.plugins.live2d_auto_layer.core import export as legacy_export
    from plugin.plugins.live2d_auto_layer.core import grounded_sam as legacy_grounded_sam
    from plugin.plugins.live2d_auto_layer.core import image_utils as legacy_image_utils
    from plugin.plugins.live2d_auto_layer.core import matting as legacy_matting
    from plugin.plugins.live2d_auto_layer.core import preprocess as legacy_preprocess
    from plugin.plugins.live2d_auto_layer.core import segment as legacy_segment
    from plugin.plugins.live2d_auto_layer.core.exporting import LayerExporter
    from plugin.plugins.live2d_auto_layer.core.image import AlphaRefiner, Preprocessor
    from plugin.plugins.live2d_auto_layer.core.image import utils as image_utils
    from plugin.plugins.live2d_auto_layer.core.segmentation import anime_face, grounded_sam
    from plugin.plugins.live2d_auto_layer.core.segmentation import segment_image

    assert legacy_segment.segment_image is segment_image
    assert legacy_anime_face.AnimePartSegmenter is anime_face.AnimePartSegmenter
    assert legacy_grounded_sam.GroundedSAM is grounded_sam.GroundedSAM
    assert legacy_export.LayerExporter is LayerExporter
    assert legacy_preprocess.Preprocessor is Preprocessor
    assert legacy_matting.AlphaRefiner is AlphaRefiner
    assert legacy_image_utils.ensure_rgba is image_utils.ensure_rgba


def test_live2d_assembler_classifies_and_orders_layers() -> None:
    image = Image.new("RGBA", (4, 4), (255, 0, 0, 255))
    assembler = Live2DAssembler()

    layers = assembler.assemble(
        {
            "right_eye": image,
            "front_hair": image,
            "body": image,
            "back_hair": image,
            "unknown_accessory": image,
        }
    )

    assert classify_layer("left eyebrow") == "Eyebrow_Left"
    assert classify_layer("head") == "Face_Skin"
    assert classify_layer("headwear") == "Headwear"
    assert classify_layer("eyewhite") == "Eye_White"
    assert classify_layer("topwear") == "Topwear"
    assert [layer.part_name for layer in layers] == [
        "Hair_Back",
        "Body",
        "Eye_Right",
        "Hair_Front",
        "unknown_accessory",
    ]
    assert all(layer.image.size == image.size for layer in layers)


def test_auto_rig_template_avoids_dynamic_misclassification() -> None:
    assert classify_rig_group("Face_Detail") == "head"
    assert classify_rig_group("Eyebrow") == "head"
    assert classify_rig_group("Headwear") == "head"
    assert classify_rig_group("Hair_Front") == "hair"
    assert classify_rig_group("Topwear") == "body"

    face_detail_bindings = infer_bindings("Face_Detail")
    eyebrow_bindings = infer_bindings("Eyebrow")
    hair_bindings = infer_bindings("Hair_Front")
    eye_bindings = infer_bindings("Eye_White")

    assert not any(binding["parameter"] == "ParamHairSway" for binding in face_detail_bindings)
    assert not any(binding["parameter"] == "ParamEyeBlink" for binding in eyebrow_bindings)
    assert any(binding["parameter"] == "ParamHairSway" for binding in hair_bindings)
    assert any(binding["parameter"] == "ParamEyeBlink" for binding in eye_bindings)


def test_import_layer_source_reads_png_folder(tmp_path) -> None:
    layers_dir = tmp_path / "layers"
    layers_dir.mkdir()
    Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(tmp_path / "preview.png")
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(layers_dir / "front_hair.png")
    Image.new("RGBA", (8, 8), (0, 0, 255, 255)).save(layers_dir / "body.png")
    (tmp_path / "metadata.json").write_text('{"worker":"see-through"}', encoding="utf-8")

    layer_set = import_layer_source(tmp_path)

    assert layer_set.source == "see_through"
    assert layer_set.canvas_size == (8, 8)
    assert [layer.name for layer in layer_set.layers] == ["body", "front_hair"]
    assert layer_set.metadata == {"worker": "see-through"}


def test_import_layer_source_finds_nested_see_through_output(tmp_path) -> None:
    nested_dir = tmp_path / "character_square"
    nested_dir.mkdir()
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(nested_dir / "front hair.png")
    Image.new("RGBA", (8, 8), (0, 0, 0, 255)).save(nested_dir / "front hair_depth.png")
    Image.new("RGBA", (8, 8), (0, 0, 0, 255)).save(nested_dir / "src_img.png")
    Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(nested_dir / "earwear.png")
    Image.new("RGBA", (8, 8), (0, 0, 0, 1)).save(nested_dir / "objects.png")
    (nested_dir / "info.json").write_text('{"canvas": 8}', encoding="utf-8")

    layer_set = import_layer_source(tmp_path)

    assert [layer.name for layer in layer_set.layers] == ["front hair"]
    assert layer_set.metadata == {"canvas": 8}
    assert layer_set.warnings == [
        "Skipped empty alpha layer: earwear.png",
        "Skipped invisible layer: objects.png",
    ]


def test_process_layer_source_exports_manifest(tmp_path) -> None:
    source_dir = tmp_path / "source"
    layers_dir = source_dir / "layers"
    output_dir = tmp_path / "output"
    layers_dir.mkdir(parents=True)
    Image.new("RGBA", (8, 8), (0, 0, 255, 255)).save(layers_dir / "body.png")
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(layers_dir / "front_hair.png")

    result = process_layer_source(
        source_dir,
        output_dir=output_dir,
        session_id="see-through-smoke",
    )

    manifest = json.loads((output_dir / "see-through-smoke" / "manifest.json").read_text(encoding="utf-8"))
    assert result.status == "succeeded"
    assert [layer.name for layer in result.layers] == ["Body", "Hair_Front"]
    assert (output_dir / "see-through-smoke" / "layers" / "Body.png").is_file()
    assert manifest["metrics"]["method"] == "layer_source"
    assert manifest["metrics"]["source"] == "see_through"
    assert manifest["metrics"]["assembly"][0]["source_name"] == "body"


def test_process_image_uses_shared_layer_assembly(monkeypatch, tmp_path) -> None:
    from plugin.plugins.live2d_auto_layer.core import pipeline as pipeline_module

    class NoopBackgroundRemover:
        def remove(self, image):
            return image.convert("RGBA")

    class NoopAlphaRefiner:
        def __init__(self, feather_radius=0):
            self.feather_radius = feather_radius

        def refine(self, image):
            return image.convert("RGBA")

    def fake_segment_image(image, method="anime_face", prompts=None, gsam_instance=None, parts=None, gpt_api_key=""):
        return {
            "eye_l": Image.new("RGBA", image.size, (0, 255, 0, 255)),
            "eyebrow_r": Image.new("RGBA", image.size, (0, 0, 255, 255)),
            "front_hair": Image.new("RGBA", image.size, (255, 0, 0, 255)),
        }

    monkeypatch.setattr(pipeline_module, "BackgroundRemover", NoopBackgroundRemover)
    monkeypatch.setattr(pipeline_module, "AlphaRefiner", NoopAlphaRefiner)
    monkeypatch.setattr(pipeline_module, "segment_image", fake_segment_image)

    result = process_image(
        Image.new("RGBA", (8, 8), (255, 255, 255, 255)),
        output_dir=tmp_path / "output",
        session_id="internal-assembly-smoke",
        method="anime_face",
        parts=["Eye_L", "Eyebrow_R", "Hair"],
        feather_radius=0,
    )

    manifest = json.loads((tmp_path / "output" / "internal-assembly-smoke" / "manifest.json").read_text(encoding="utf-8"))
    assert [layer.name for layer in result.layers] == ["Eye_Left", "Eyebrow_Right", "Hair_Front"]
    assert (tmp_path / "output" / "internal-assembly-smoke" / "layers" / "Eye_Left.png").is_file()
    assert manifest["metrics"]["method"] == "anime_face"
    assert manifest["metrics"]["assembly"][0]["source"] == "internal"
    assert manifest["metrics"]["assembly"][0]["source_name"] == "eye_l"


def test_export_auto_rig_model_package(tmp_path) -> None:
    source_dir = tmp_path / "source"
    layers_dir = source_dir / "layers"
    output_dir = tmp_path / "output"
    layers_dir.mkdir(parents=True)
    head = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    head.putpixel((0, 0), (255, 224, 200, 1))
    for x in range(12, 20):
        for y in range(12, 20):
            head.putpixel((x, y), (255, 224, 200, 255))
    Image.new("RGBA", (32, 32), (255, 0, 0, 255)).save(layers_dir / "front_hair.png")
    head.save(layers_dir / "head.png")

    result = process_layer_source(
        source_dir,
        output_dir=output_dir,
        session_id="auto-rig-smoke",
    )
    unfiltered = export_auto_rig_model(result, mesh_alpha_threshold=0)
    with zipfile.ZipFile(unfiltered["auto_rig_zip_path"]) as archive:
        unfiltered_model = json.loads(archive.read("auto_rig_model.json").decode("utf-8"))

    package = export_auto_rig_model(result, mesh_alpha_threshold=10)

    zip_path = tmp_path / "output" / "auto-rig-smoke" / "auto_rig_model.zip"
    assert package["auto_rig_zip_path"] == str(zip_path)
    assert package["mesh_alpha_threshold"] == 10
    assert package["quality_summary"]["visual_status"] == "preserved"
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        assert "README.md" in names
        assert "auto_rig_model.json" in names
        assert "textures/layers/00_Face_Skin.png" in names
        assert "textures/layers/01_Hair_Front.png" in names
        assert not any(name.endswith(".model3.json") or name.endswith(".moc3") for name in names)
        model = json.loads(archive.read("auto_rig_model.json").decode("utf-8"))

    assert model["format"] == "neko.live2d_auto_layer.auto_rig.v1"
    assert model["quality"]["is_fully_automatic"] is True
    assert model["quality"]["is_cubism_model"] is False
    assert model["quality"]["summary"]["visual_status"] == "preserved"
    assert model["quality"]["visual_composition"]["texture_alpha_preserved"] is True
    assert model["quality"]["visual_composition"]["preview_available"] is True
    assert model["quality"]["rig_geometry"]["mesh_strategy"] == "threshold_alpha_bbox_3x3_grid"
    assert model["quality"]["rig_geometry"]["mesh_alpha_threshold"] == 10
    assert "Face_Skin" in model["quality"]["rig_geometry"]["medium_risk_layers"]
    assert model["canvas_size"] == [32, 32]
    assert len(model["parameters"]) >= 4
    assert len(model["layers"][0]["mesh"]["vertices"]) == 9
    assert len(model["layers"][0]["mesh"]["triangles"]) == 8
    assert model["layers"][0]["bindings"]
    assert model["layers"][0]["metadata"]["rig_group"] == "head"
    assert model["layers"][1]["metadata"]["rig_group"] == "hair"
    assert unfiltered_model["layers"][0]["bbox"] == [0, 0, 24, 24]
    assert model["layers"][0]["bbox"] == [8, 8, 16, 16]
    face_report = model["quality"]["rig_geometry"]["layer_reports"][0]
    assert face_report["name"] == "Face_Skin"
    assert face_report["raw_alpha_bbox"] == [0, 0, 20, 20]
    assert face_report["threshold_alpha_bbox"] == [12, 12, 8, 8]
    assert face_report["rig_risk"] == "medium"

    loaded = load_auto_rig_model(output_dir / "auto-rig-smoke")
    assert loaded["format"] == "neko.live2d_auto_layer.auto_rig.v1"
    assert loaded["canvas_size"] == [32, 32]
    assert loaded["quality_summary"]["visual_status"] == "preserved"
    assert len(loaded["layers"]) == 2
    assert loaded["layers"][0]["name"] == "Face_Skin"
    assert Path(loaded["layers"][0]["texture_path"]).parts[-3:] == (
        "textures",
        "layers",
        "00_Face_Skin.png",
    )

    model_path = output_dir / "auto-rig-smoke" / "auto_rig" / "auto_rig_model.json"
    unsafe = json.loads(model_path.read_text(encoding="utf-8"))
    unsafe["layers"][0]["texture"] = "../preview.png"
    model_path.write_text(json.dumps(unsafe), encoding="utf-8")
    with pytest.raises(ValueError, match="outside model directory"):
        load_auto_rig_model(output_dir / "auto-rig-smoke")


def test_export_pngtuber_model_package(tmp_path) -> None:
    source_dir = tmp_path / "source"
    layers_dir = source_dir / "layers"
    output_dir = tmp_path / "output"
    layers_dir.mkdir(parents=True)
    Image.new("RGBA", (16, 16), (0, 0, 255, 255)).save(layers_dir / "body.png")
    Image.new("RGBA", (16, 16), (255, 0, 0, 255)).save(layers_dir / "front_hair.png")
    Image.new("RGBA", (16, 16), (0, 255, 0, 255)).save(layers_dir / "eye_white.png")
    mouth = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for x in range(6, 10):
        for y in range(9, 11):
            mouth.putpixel((x, y), (80, 0, 0, 255))
    mouth.save(layers_dir / "mouth.png")

    result = process_layer_source(
        source_dir,
        output_dir=output_dir,
        session_id="pngtuber-smoke",
    )
    package = export_pngtuber_model(
        result,
        model_name="Layered Smoke",
        enable_basic_blink=True,
    )

    zip_path = output_dir / "pngtuber-smoke" / "pngtuber_model.zip"
    package_dir = output_dir / "pngtuber-smoke" / "pngtuber_model"
    assert package["pngtuber_zip_path"] == str(zip_path)
    assert package["pngtuber_model_path"] == str(package_dir / "model.json")
    assert package["canvas_size"] == [16, 16]
    assert zip_path.is_file()

    model = json.loads((package_dir / "model.json").read_text(encoding="utf-8"))
    metadata = json.loads((package_dir / "metadata.neko-pngtuber.v1.json").read_text(encoding="utf-8"))
    ok, error = _validate_model_package(package_dir, model)

    assert ok is True, error
    assert model["format"] == "neko.pngtuber.package.v1"
    assert model["model_type"] == "pngtuber"
    assert model["name"] == "Layered Smoke"
    assert model["pngtuber"]["adapter"] == "neko_pngtuber_v1"
    assert model["pngtuber"]["idle_image"] == "idle.png"
    assert model["pngtuber"]["talking_image"] == "talking.png"
    assert model["pngtuber"]["metadata"] == "metadata.neko-pngtuber.v1.json"
    assert model["pngtuber"]["layered_metadata"] == "metadata.neko-pngtuber.v1.json"
    assert metadata["format"] == "neko.pngtuber.v1"
    assert metadata["runtime"] == "neko_layered_canvas"
    assert metadata["source_session_id"] == "pngtuber-smoke"
    assert metadata["canvas"] == {"width": 16, "height": 16}
    assert metadata["fallback"] == {"idle": "idle.png", "talking": "talking.png"}
    assert len(metadata["layers"]) == 5
    assert metadata["state_count"] == 2
    assert metadata["capabilities"]["generated_talking_mouth"] is True
    assert metadata["layers"][0]["id"] == "layer_00_body"
    assert metadata["layers"][0]["image"] == "layers/00_Body.png"
    assert metadata["layers"][1]["image"] == "layers/01_Eye_White.png"
    assert metadata["layers"][1]["showBlink"] == 1
    assert metadata["layers"][2]["image"] == "layers/02_Mouth.png"
    assert metadata["layers"][2]["showTalk"] == 1
    assert metadata["layers"][3]["id"] == "layer_02_mouth_open"
    assert metadata["layers"][3]["image"] == "layers/02_Mouth_Open.png"
    assert metadata["layers"][3]["showTalk"] == 2

    with Image.open(package_dir / "idle.png") as idle_image, Image.open(package_dir / "talking.png") as talking_image:
        assert ImageChops.difference(idle_image.convert("RGB"), talking_image.convert("RGB")).getbbox() is not None
    with Image.open(package_dir / "layers" / "02_Mouth.png") as mouth_closed, Image.open(package_dir / "layers" / "02_Mouth_Open.png") as mouth_open:
        assert ImageChops.difference(mouth_closed.convert("RGB"), mouth_open.convert("RGB")).getbbox() is not None

    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        assert "model.json" in names
        assert "metadata.neko-pngtuber.v1.json" in names
        assert "idle.png" in names
        assert "talking.png" in names
        assert "layers/00_Body.png" in names
        assert "layers/01_Eye_White.png" in names
        assert "layers/02_Mouth.png" in names
        assert "layers/02_Mouth_Open.png" in names
        assert "layers/03_Hair_Front.png" in names
        assert not any(name.endswith(".model3.json") or name.endswith(".moc3") for name in names)


def test_pngtuber_export_detects_multilingual_layer_roles() -> None:
    assert _layer_role("嘴") == "mouth"
    assert _layer_role("口元") == "mouth"
    assert _layer_role("眼睛") == "eye"
    assert _layer_role("目") == "eye"
    assert _layer_role("头发") == "hair"
    assert _layer_role("前髪") == "hair"
    assert _layer_role("身体") == "body"
    assert _layer_role("衣服") == "body"


def test_install_pngtuber_package_from_export(tmp_path) -> None:
    source_dir = tmp_path / "source"
    layers_dir = source_dir / "layers"
    output_dir = tmp_path / "output"
    pngtuber_dir = tmp_path / "pngtuber-library"
    layers_dir.mkdir(parents=True)
    Image.new("RGBA", (12, 12), (0, 0, 255, 255)).save(layers_dir / "body.png")
    Image.new("RGBA", (12, 12), (255, 0, 0, 255)).save(layers_dir / "mouth.png")

    result = process_layer_source(
        source_dir,
        output_dir=output_dir,
        session_id="install-smoke",
    )
    package = export_pngtuber_model(result, model_name="Install Smoke")
    installed = install_pngtuber_package(
        package["pngtuber_dir"],
        model_name="Install Smoke",
        pngtuber_dir=pngtuber_dir,
    )

    target_dir = pngtuber_dir / str(installed["folder"])
    model = json.loads((target_dir / "model.json").read_text(encoding="utf-8"))
    metadata = json.loads((target_dir / "metadata.neko-pngtuber.v1.json").read_text(encoding="utf-8"))

    assert installed["success"] is True
    assert installed["url"] == f"/user_pngtuber/{installed['folder']}/model.json"
    assert model["format"] == "neko.pngtuber.package.v1"
    assert model["model_type"] == "pngtuber"
    assert model["source_format"] == "live2d_auto_layer"
    assert model["pngtuber"]["adapter"] == "neko_pngtuber_v1"
    assert model["pngtuber"]["idle_image"] == "idle.png"
    assert model["pngtuber"]["metadata"] == "metadata.neko-pngtuber.v1.json"
    assert model["pngtuber"]["layered_metadata"] == "metadata.neko-pngtuber.v1.json"
    ok, error = _validate_model_package(target_dir, model)
    assert ok is True, error
    assert installed["pngtuber"]["protocol"] == "neko.pngtuber.v1"
    assert installed["pngtuber"]["idle_image"] == f"/user_pngtuber/{installed['folder']}/idle.png"
    assert installed["pngtuber"]["metadata"] == f"/user_pngtuber/{installed['folder']}/metadata.neko-pngtuber.v1.json"
    assert installed["pngtuber"]["layered_metadata"] == f"/user_pngtuber/{installed['folder']}/metadata.neko-pngtuber.v1.json"
    assert metadata["format"] == "neko.pngtuber.v1"
    assert (target_dir / "layers" / "00_Body.png").is_file()

    second = install_pngtuber_package(
        package["pngtuber_dir"],
        model_name="Install Smoke",
        pngtuber_dir=pngtuber_dir,
    )
    assert second["folder"] != installed["folder"]
    assert (pngtuber_dir / str(second["folder"]) / "model.json").is_file()
