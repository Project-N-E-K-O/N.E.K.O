"""Export fully automatic NEKO auto-rig packages."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from PIL import Image

from ..types import LayerArtifact, ProcessResult
from .mesh import build_grid_mesh
from .model import AutoRigLayer, AutoRigModel
from .template import DEFAULT_PARAMETERS, infer_bindings

AUTO_RIG_DIR_NAME = "auto_rig"
AUTO_RIG_ZIP_NAME = "auto_rig_model.zip"
AUTO_RIG_MODEL_NAME = "auto_rig_model.json"


DEFAULT_MESH_ALPHA_THRESHOLD = 10


def export_auto_rig_model(
    result: ProcessResult,
    *,
    mesh_alpha_threshold: int = DEFAULT_MESH_ALPHA_THRESHOLD,
) -> dict[str, object]:
    """Create a loadable NEKO auto-rig v1 package for an existing layer session."""
    mesh_alpha_threshold = _clamp_alpha_threshold(mesh_alpha_threshold)
    session_dir = Path(result.output_dir)
    if not session_dir.is_dir():
        raise FileNotFoundError(f"Session output directory not found: {session_dir}")
    if not result.layers:
        raise ValueError("Session has no exported layers")

    rig_dir = session_dir / AUTO_RIG_DIR_NAME
    if rig_dir.exists():
        shutil.rmtree(rig_dir)
    texture_dir = rig_dir / "textures" / "layers"
    texture_dir.mkdir(parents=True, exist_ok=True)

    canvas_size = _canvas_size(result.layers)
    auto_layers, layer_quality = _build_layers(
        result.layers,
        texture_dir,
        mesh_alpha_threshold=mesh_alpha_threshold,
    )
    if result.preview_path and Path(result.preview_path).is_file():
        shutil.copyfile(result.preview_path, rig_dir / "preview.png")

    model = AutoRigModel(
        format="neko.live2d_auto_layer.auto_rig.v1",
        version=1,
        session_id=result.session_id,
        canvas_size=canvas_size,
        coordinate_space="pixel_top_left",
        preview="preview.png" if result.preview_path else "",
        parameters=list(DEFAULT_PARAMETERS),
        layers=auto_layers,
        quality=_quality_report(
            result.layers,
            auto_layers,
            layer_quality,
            mesh_alpha_threshold=mesh_alpha_threshold,
            preview_available=bool(result.preview_path and Path(result.preview_path).is_file()),
        ),
        source_manifest=result.manifest_path,
    )
    model_path = rig_dir / AUTO_RIG_MODEL_NAME
    model_path.write_text(
        json.dumps(model.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (rig_dir / "README.md").write_text(_readme(result), encoding="utf-8")

    zip_path = session_dir / AUTO_RIG_ZIP_NAME
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(rig_dir.rglob("*")):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(rig_dir))

    return {
        "session_id": result.session_id,
        "auto_rig_dir": str(rig_dir),
        "auto_rig_model_path": str(model_path),
        "auto_rig_zip_path": str(zip_path),
        "layer_count": len(auto_layers),
        "mesh_alpha_threshold": mesh_alpha_threshold,
        "quality_summary": model.quality.get("summary", {}),
        "message": f"Exported NEKO auto-rig package with {len(auto_layers)} layer(s)",
        "warning": "This is a NEKO auto-rig package, not a Cubism model3/moc3 package.",
    }


def _build_layers(
    layers: list[LayerArtifact],
    texture_dir: Path,
    *,
    mesh_alpha_threshold: int,
) -> tuple[list[AutoRigLayer], list[dict[str, object]]]:
    auto_layers: list[AutoRigLayer] = []
    layer_quality: list[dict[str, object]] = []
    for index, layer in enumerate(layers):
        source_path = Path(layer.path)
        if not source_path.is_file():
            raise FileNotFoundError(f"Layer PNG not found: {source_path}")
        with Image.open(source_path) as image:
            rgba = image.convert("RGBA")
            safe_name = _sanitize_name(layer.name)
            texture_name = f"{index:02d}_{safe_name}.png"
            rgba.save(texture_dir / texture_name, format="PNG")
            mesh = build_grid_mesh(
                rgba,
                subdivisions=2,
                alpha_threshold=mesh_alpha_threshold,
            )
            quality = _layer_quality_metrics(
                layer.name,
                rgba,
                mesh_bbox=mesh.bbox,
                mesh_alpha_threshold=mesh_alpha_threshold,
            )

        auto_layers.append(
            AutoRigLayer(
                id=f"layer_{index:02d}_{safe_name}",
                name=layer.name,
                texture=f"textures/layers/{texture_name}",
                draw_order=index,
                width=layer.width,
                height=layer.height,
                bbox=mesh.bbox,
                mesh={
                    "type": "grid",
                    "vertices": mesh.vertices,
                    "uvs": mesh.uvs,
                    "triangles": mesh.triangles,
                },
                bindings=infer_bindings(layer.name),
                metadata={"area": layer.area},
            )
        )
        layer_quality.append(quality)
    return auto_layers, layer_quality


def _canvas_size(layers: list[LayerArtifact]) -> tuple[int, int]:
    return (
        max(layer.width for layer in layers),
        max(layer.height for layer in layers),
    )


def _quality_report(
    layers: list[LayerArtifact],
    auto_layers: list[AutoRigLayer],
    layer_quality: list[dict[str, object]],
    *,
    mesh_alpha_threshold: int,
    preview_available: bool,
) -> dict[str, object]:
    missing_bindings = [
        layer.name
        for layer in auto_layers
        if not layer.bindings
    ]
    high_risk_layers = [
        str(layer["name"])
        for layer in layer_quality
        if layer.get("rig_risk") == "high"
    ]
    medium_risk_layers = [
        str(layer["name"])
        for layer in layer_quality
        if layer.get("rig_risk") == "medium"
    ]
    rig_status = "ok"
    if high_risk_layers:
        rig_status = "needs_review"
    elif medium_risk_layers:
        rig_status = "watch"
    return {
        "status": "prototype",
        "is_fully_automatic": True,
        "is_cubism_model": False,
        "summary": {
            "visual_status": "preserved",
            "rig_geometry_status": rig_status,
            "high_risk_layer_count": len(high_risk_layers),
            "medium_risk_layer_count": len(medium_risk_layers),
        },
        "visual_composition": {
            "status": "preserved",
            "texture_alpha_preserved": True,
            "preview_available": preview_available,
            "layer_count": len(layers),
            "notes": [
                "Original PNG layer alpha is preserved for visual composition.",
                "Mesh alpha threshold affects only rig geometry, not texture pixels.",
            ],
        },
        "rig_geometry": {
            "status": rig_status,
            "mesh_strategy": "threshold_alpha_bbox_3x3_grid",
            "mesh_alpha_threshold": mesh_alpha_threshold,
            "high_risk_layers": high_risk_layers,
            "medium_risk_layers": medium_risk_layers,
            "missing_bindings": missing_bindings,
            "layer_reports": layer_quality,
            "notes": [
                "High risk usually means low-alpha pixels greatly expand the raw bbox compared with the thresholded mesh bbox.",
                "Small facial layers are not automatically bad; risk is based on geometry instability, not visual size alone.",
            ],
        },
        "notes": [
            "This v1 package is intended for a NEKO runtime/previewer.",
            "Deformations are coarse template bindings and can be improved without changing the layer import stage.",
        ],
    }


def _layer_quality_metrics(
    name: str,
    image: Image.Image,
    *,
    mesh_bbox: tuple[int, int, int, int],
    mesh_alpha_threshold: int,
) -> dict[str, object]:
    alpha = image.getchannel("A")
    raw_box = alpha.getbbox()
    threshold_alpha = alpha.point(lambda value: 255 if value > mesh_alpha_threshold else 0)
    threshold_box = threshold_alpha.getbbox()
    raw_bbox = _box_to_bbox(raw_box)
    threshold_bbox = _box_to_bbox(threshold_box)
    raw_bbox_area = _bbox_area(raw_bbox)
    threshold_bbox_area = _bbox_area(threshold_bbox)
    mesh_bbox_area = _bbox_area(mesh_bbox)
    canvas_area = max(1, image.width * image.height)
    alpha_histogram = alpha.histogram()
    weak_alpha_pixels = sum(alpha_histogram[1:mesh_alpha_threshold + 1])
    strong_alpha_pixels = sum(alpha_histogram[mesh_alpha_threshold + 1:])
    bbox_shrink_ratio = 0.0
    if raw_bbox_area > 0:
        bbox_shrink_ratio = 1.0 - (threshold_bbox_area / raw_bbox_area)
    weak_to_strong_ratio = weak_alpha_pixels / max(1, strong_alpha_pixels)
    rig_risk = _rig_risk(
        bbox_shrink_ratio=bbox_shrink_ratio,
        weak_to_strong_ratio=weak_to_strong_ratio,
        strong_alpha_pixels=strong_alpha_pixels,
    )
    return {
        "name": name,
        "rig_risk": rig_risk,
        "raw_alpha_bbox": list(raw_bbox),
        "threshold_alpha_bbox": list(threshold_bbox),
        "mesh_bbox": list(mesh_bbox),
        "weak_alpha_pixels": weak_alpha_pixels,
        "strong_alpha_pixels": strong_alpha_pixels,
        "weak_to_strong_ratio": round(weak_to_strong_ratio, 4),
        "bbox_shrink_ratio": round(bbox_shrink_ratio, 4),
        "mesh_canvas_ratio": round(mesh_bbox_area / canvas_area, 4),
    }


def _rig_risk(
    *,
    bbox_shrink_ratio: float,
    weak_to_strong_ratio: float,
    strong_alpha_pixels: int,
) -> str:
    if strong_alpha_pixels <= 0:
        return "high"
    if bbox_shrink_ratio >= 0.9 or weak_to_strong_ratio >= 2.0:
        return "high"
    if bbox_shrink_ratio >= 0.5 or weak_to_strong_ratio >= 0.5:
        return "medium"
    return "low"


def _box_to_bbox(box: tuple[int, int, int, int] | None) -> tuple[int, int, int, int]:
    if box is None:
        return (0, 0, 0, 0)
    left, top, right, bottom = box
    return (left, top, max(0, right - left), max(0, bottom - top))


def _bbox_area(bbox: tuple[int, int, int, int]) -> int:
    return max(0, bbox[2]) * max(0, bbox[3])


def _readme(result: ProcessResult) -> str:
    return f"""# NEKO Auto-Rig Package

This archive was generated from N.E.K.O Live2D Auto Layer session `{result.session_id}`.

It is a fully automatic NEKO auto-rig v1 package. It is not a Cubism `.model3.json`
or `.moc3` package.

Contents:

- `auto_rig_model.json`: machine-readable model, meshes, parameters, and bindings
- `textures/layers/`: ordered transparent PNG textures
- `preview.png`: composite preview when available

The current rig uses a conservative rectangular grid mesh per layer. It is meant
to make the no-manual-step pipeline executable first; later versions can replace
the mesh and binding strategy while keeping the same imported layer source.
"""


def _sanitize_name(name: str) -> str:
    clean = "".join(char if char.isalnum() or char in "_-" else "_" for char in name.strip())
    return clean or "Layer"


def _clamp_alpha_threshold(value: int) -> int:
    return max(0, min(255, int(value)))
