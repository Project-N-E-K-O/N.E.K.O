import importlib.util
import sys
import types
import zipfile
from pathlib import Path

import json
import pytest

from PIL import Image

from plugin.plugins.live2d_auto_layer.core.assembly import Live2DAssembler, classify_layer
from plugin.plugins.live2d_auto_layer.core.auto_rig import export_auto_rig_model, load_auto_rig_model
from plugin.plugins.live2d_auto_layer.core.cubism import export_cubism_handoff
from plugin.plugins.live2d_auto_layer.core.importing import import_layer_source
from plugin.plugins.live2d_auto_layer.core.pipeline import process_layer_source
from plugin.plugins.live2d_auto_layer.core.auto_rig.template import classify_rig_group, infer_bindings
from plugin.plugins.live2d_auto_layer.services.layer_service import LayerService


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


def test_layer_service_ui_result_can_auto_export_cubism_handoff(tmp_path) -> None:
    source_dir = tmp_path / "source"
    layers_dir = source_dir / "layers"
    output_dir = tmp_path / "output"
    layers_dir.mkdir(parents=True)
    Image.new("RGBA", (8, 8), (0, 0, 255, 255)).save(layers_dir / "body.png")
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(layers_dir / "front_hair.png")
    service = LayerService(output_dir=output_dir)

    result = service.import_layer_source(source_dir, session_id="auto-handoff-smoke")
    ui_result = service.result_to_ui_dict(result, include_cubism_handoff=True)

    zip_path = output_dir / "auto-handoff-smoke" / "cubism_handoff.zip"
    assert ui_result["cubism_handoff_zip_path"] == str(zip_path)
    assert zip_path.is_file()


def test_export_cubism_handoff_package(tmp_path) -> None:
    source_dir = tmp_path / "source"
    layers_dir = source_dir / "layers"
    output_dir = tmp_path / "output"
    layers_dir.mkdir(parents=True)
    Image.new("RGBA", (8, 8), (0, 0, 255, 255)).save(layers_dir / "head.png")
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(layers_dir / "front_hair.png")

    result = process_layer_source(
        source_dir,
        output_dir=output_dir,
        session_id="handoff-smoke",
    )
    handoff = export_cubism_handoff(result)

    zip_path = tmp_path / "output" / "handoff-smoke" / "cubism_handoff.zip"
    assert handoff["cubism_handoff_zip_path"] == str(zip_path)
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        assert "README.md" in names
        assert "cubism_handoff_manifest.json" in names
        assert "cubism_layers.csv" in names
        assert "layers/00_Face_Skin.png" in names
        assert "layers/01_Hair_Front.png" in names
        assert not any(name.endswith(".model3.json") or name.endswith(".moc3") for name in names)
        manifest = json.loads(archive.read("cubism_handoff_manifest.json").decode("utf-8"))
    assert manifest["is_loadable_live2d_model"] is False
    assert manifest["layers"][0]["suggested_deformer"] == "WarpDeformer_Head"


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
