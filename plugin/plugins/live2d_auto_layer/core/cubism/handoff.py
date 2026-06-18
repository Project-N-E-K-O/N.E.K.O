"""Export a Cubism Editor-friendly handoff package from a layer session."""

from __future__ import annotations

import csv
import json
import shutil
import zipfile
from pathlib import Path

from ..types import LayerArtifact, ProcessResult

HANDOFF_DIR_NAME = "cubism_handoff"
HANDOFF_ZIP_NAME = "cubism_handoff.zip"


def export_cubism_handoff(result: ProcessResult) -> dict[str, object]:
    """Create a Cubism handoff folder and ZIP for an existing layer session."""
    session_dir = Path(result.output_dir)
    if not session_dir.is_dir():
        raise FileNotFoundError(f"Session output directory not found: {session_dir}")
    if not result.layers:
        raise ValueError("Session has no exported layers")

    handoff_dir = session_dir / HANDOFF_DIR_NAME
    if handoff_dir.exists():
        shutil.rmtree(handoff_dir)
    layers_dir = handoff_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    rows = _build_layer_rows(result.layers)
    for row in rows:
        source_path = Path(str(row["source_path"]))
        if not source_path.is_file():
            raise FileNotFoundError(f"Layer PNG not found: {source_path}")
        shutil.copyfile(source_path, layers_dir / str(row["file"]))

    if result.preview_path and Path(result.preview_path).is_file():
        shutil.copyfile(result.preview_path, handoff_dir / "preview.png")

    manifest = _build_manifest(result, rows)
    (handoff_dir / "cubism_handoff_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(handoff_dir / "cubism_layers.csv", rows)
    (handoff_dir / "README.md").write_text(_readme(result), encoding="utf-8")

    zip_path = session_dir / HANDOFF_ZIP_NAME
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(handoff_dir.rglob("*")):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(handoff_dir))

    return {
        "session_id": result.session_id,
        "cubism_handoff_dir": str(handoff_dir),
        "cubism_handoff_zip_path": str(zip_path),
        "layer_count": len(rows),
        "message": f"Exported Cubism handoff package with {len(rows)} layer(s)",
        "warning": "This is a Cubism Editor handoff asset package, not a loadable model3/moc3 model.",
    }


def _build_layer_rows(layers: list[LayerArtifact]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, layer in enumerate(layers):
        part_name = _sanitize_name(layer.name)
        rows.append(
            {
                "order": index,
                "name": layer.name,
                "file": f"{index:02d}_{part_name}.png",
                "source_path": layer.path,
                "width": layer.width,
                "height": layer.height,
                "area": layer.area,
                "suggested_part": _suggested_part(layer.name),
                "suggested_deformer": _suggested_deformer(layer.name),
                "suggested_parameter": _suggested_parameter(layer.name),
            }
        )
    return rows


def _build_manifest(result: ProcessResult, rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "format": "neko.live2d_auto_layer.cubism_handoff.v1",
        "is_loadable_live2d_model": False,
        "session_id": result.session_id,
        "source_manifest": result.manifest_path,
        "preview": "preview.png" if result.preview_path else "",
        "notes": [
            "This package is for Cubism Editor handoff.",
            "It does not contain .model3.json, .moc3, textures, motions, or physics files.",
            "Import or place these PNG layers in Cubism Editor, then create ArtMeshes, deformers, parameters, and model export there.",
        ],
        "layers": [
            {
                key: value
                for key, value in row.items()
                if key != "source_path"
            }
            for row in rows
        ],
    }


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "order",
        "name",
        "file",
        "width",
        "height",
        "area",
        "suggested_part",
        "suggested_deformer",
        "suggested_parameter",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _readme(result: ProcessResult) -> str:
    return f"""# Cubism Editor Handoff

This archive was generated from N.E.K.O Live2D Auto Layer session `{result.session_id}`.

It is **not** a loadable Live2D model package. It does not contain `.model3.json`
or `.moc3` files.

Contents:

- `layers/`: ordered transparent PNG layers
- `cubism_layers.csv`: layer order and suggested Cubism roles
- `cubism_handoff_manifest.json`: machine-readable handoff metadata
- `preview.png`: composite preview when available

Suggested next steps:

1. Import or place the PNG layers in Cubism Editor.
2. Review layer names and merge, split, hide, or redraw layers as needed.
3. Create ArtMeshes and deformers.
4. Bind parameters such as angle, eye open, mouth open, and breathing.
5. Export the real Cubism model package from Cubism Editor.
"""


def _sanitize_name(name: str) -> str:
    clean = "".join(char if char.isalnum() or char in "_-" else "_" for char in name.strip())
    return clean or "Layer"


def _normalized(name: str) -> str:
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def _suggested_part(name: str) -> str:
    normalized = _normalized(name)
    if "hair" in normalized:
        return "Hair"
    if normalized in {"face_skin", "face_detail", "head", "neck", "ears"}:
        return "Head"
    if "eye" in normalized or normalized in {"iris", "eyelash", "eyebrow"}:
        return "Eye"
    if "mouth" in normalized:
        return "Mouth"
    if normalized in {"topwear", "bottomwear", "legwear", "footwear", "body"}:
        return "Body"
    return "Accessory"


def _suggested_deformer(name: str) -> str:
    part = _suggested_part(name)
    if part == "Hair":
        return "WarpDeformer_Hair"
    if part == "Head":
        return "WarpDeformer_Head"
    if part == "Eye":
        return "WarpDeformer_Eye"
    if part == "Mouth":
        return "WarpDeformer_Mouth"
    if part == "Body":
        return "WarpDeformer_Body"
    return "WarpDeformer_Accessory"


def _suggested_parameter(name: str) -> str:
    normalized = _normalized(name)
    if "eye" in normalized or normalized in {"iris", "eyelash"}:
        return "ParamEyeLOpen/ParamEyeROpen"
    if "mouth" in normalized:
        return "ParamMouthOpenY"
    if "hair" in normalized:
        return "ParamAngleX/ParamAngleY + physics"
    if normalized in {"face_skin", "face_detail", "head", "neck", "ears"}:
        return "ParamAngleX/ParamAngleY/ParamAngleZ"
    if normalized in {"topwear", "bottomwear", "legwear", "footwear", "body"}:
        return "ParamBodyAngleX/ParamBreath"
    return ""
