"""Export NEKO PNGTuber packages from Live2D Auto Layer sessions."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from PIL import Image

from ..types import LayerArtifact, ProcessResult

PNGTUBER_DIR_NAME = "pngtuber_model"
PNGTUBER_ZIP_NAME = "pngtuber_model.zip"
PNGTUBER_MODEL_NAME = "model.json"
PNGTUBER_METADATA_NAME = "metadata.live2d-auto-layer.json"


def export_pngtuber_model(
    result: ProcessResult,
    *,
    model_name: str = "",
    enable_basic_blink: bool = False,
) -> dict[str, object]:
    """Create an importable NEKO PNGTuber package for an existing layer session."""
    session_dir = Path(result.output_dir)
    if not session_dir.is_dir():
        raise FileNotFoundError(f"Session output directory not found: {session_dir}")
    if not result.layers:
        raise ValueError("Session has no exported layers")

    package_dir = session_dir / PNGTUBER_DIR_NAME
    if package_dir.exists():
        shutil.rmtree(package_dir)
    layers_dir = package_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    canvas_size = _canvas_size(result.layers)
    rows = _copy_layers(result.layers, layers_dir)
    idle = _composite_layers(rows, canvas_size, state="idle")
    talking = _composite_layers(rows, canvas_size, state="talking")
    idle.save(package_dir / "idle.png", format="PNG")
    talking.save(package_dir / "talking.png", format="PNG")
    if result.preview_path and Path(result.preview_path).is_file():
        shutil.copyfile(result.preview_path, package_dir / "preview.png")

    metadata = _build_layered_metadata(
        result,
        rows,
        canvas_size=canvas_size,
        enable_basic_blink=enable_basic_blink,
    )
    (package_dir / PNGTUBER_METADATA_NAME).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    model_json = _build_model_json(
        result,
        model_name=model_name,
        enable_basic_blink=enable_basic_blink,
    )
    (package_dir / PNGTUBER_MODEL_NAME).write_text(
        json.dumps(model_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (package_dir / "README.md").write_text(_readme(result), encoding="utf-8")

    zip_path = session_dir / PNGTUBER_ZIP_NAME
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(package_dir.rglob("*")):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(package_dir))

    return {
        "session_id": result.session_id,
        "pngtuber_dir": str(package_dir),
        "pngtuber_zip_path": str(zip_path),
        "pngtuber_model_path": str(package_dir / PNGTUBER_MODEL_NAME),
        "pngtuber_metadata_path": str(package_dir / PNGTUBER_METADATA_NAME),
        "layer_count": len(rows),
        "canvas_size": list(canvas_size),
        "message": f"Exported NEKO PNGTuber package with {len(rows)} layer(s)",
        "warning": "This is a NEKO PNGTuber layered_canvas_v1 package, not a Cubism model3/moc3 package.",
    }


def _copy_layers(layers: list[LayerArtifact], layers_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, layer in enumerate(layers):
        source_path = Path(layer.path)
        if not source_path.is_file():
            raise FileNotFoundError(f"Layer PNG not found: {source_path}")
        safe_name = _sanitize_name(layer.name)
        filename = f"{index:02d}_{safe_name}.png"
        target_path = layers_dir / filename
        shutil.copyfile(source_path, target_path)
        rows.append(
            {
                "order": index,
                "name": layer.name,
                "file": f"layers/{filename}",
                "path": str(target_path),
                "width": layer.width,
                "height": layer.height,
                "area": layer.area,
                "role": _layer_role(layer.name),
            }
        )
    return rows


def _build_layered_metadata(
    result: ProcessResult,
    rows: list[dict[str, object]],
    *,
    canvas_size: tuple[int, int],
    enable_basic_blink: bool,
) -> dict[str, object]:
    metadata_layers: list[dict[str, object]] = []
    for row in rows:
        role = str(row["role"])
        layer_state = {
            "visible": True,
            "x": 0,
            "y": 0,
            "z_index": int(row["order"]),
            "scale": [1, 1],
            "rotation": 0,
        }
        layer: dict[str, object] = {
            "order": int(row["order"]),
            "name": str(row["name"]),
            "image": str(row["file"]),
            "width": int(row["width"]),
            "height": int(row["height"]),
            "x": 0,
            "y": 0,
            "zindex": int(row["order"]),
            "state": layer_state,
            "role": role,
            "source_area": int(row["area"]),
        }
        if enable_basic_blink and role == "eye":
            layer["showBlink"] = 1
        metadata_layers.append(layer)

    return {
        "runtime": "layered_canvas",
        "format": "neko.pngtuber.layered_canvas.v1",
        "source_format": "live2d_auto_layer",
        "source_session_id": result.session_id,
        "source_manifest": result.manifest_path,
        "canvas": {"width": canvas_size[0], "height": canvas_size[1]},
        "capabilities": {
            "layered": True,
            "speech_state": True,
            "generated_talking_mouth": any(str(row["role"]) == "mouth" for row in rows),
            "blink_layers": bool(enable_basic_blink),
            "hotkeys": False,
            "physics": False,
        },
        "blink": {
            "enabled": bool(enable_basic_blink),
            "interval_min_ms": 2800,
            "interval_max_ms": 5200,
            "duration_ms": 140,
        },
        "state_count": 2,
        "states": {
            "idle": {"image": "idle.png"},
            "talking": {
                "image": "talking.png",
                "mouth_transform": "vertical_open_v1",
            },
        },
        "layers": metadata_layers,
        "notes": [
            "Generated from Live2D Auto Layer transparent parts.",
            "The package targets NEKO PNGTuber layered_canvas_v1 runtime.",
            "It is not a Cubism model package.",
        ],
    }


def _build_model_json(
    result: ProcessResult,
    *,
    model_name: str,
    enable_basic_blink: bool,
) -> dict[str, object]:
    clean_name = model_name.strip() or result.session_id or "Live2D Auto Layer PNGTuber"
    return {
        "name": clean_name,
        "version": 1,
        "model_type": "pngtuber",
        "source_format": "live2d_auto_layer",
        "description": "Generated by N.E.K.O Live2D Auto Layer as a layered PNGTuber package.",
        "capabilities": {
            "idle_state": True,
            "talking_state": True,
            "layered_canvas": True,
            "basic_blink": bool(enable_basic_blink),
        },
        "pngtuber": {
            "idle_image": "idle.png",
            "talking_image": "talking.png",
            "adapter": "layered_canvas_v1",
            "layered_metadata": PNGTUBER_METADATA_NAME,
            "source_type": "live2d_auto_layer",
            "scale": 1,
            "offset_x": 0,
            "offset_y": 0,
            "mirror": False,
        },
    }


def _composite_layers(
    rows: list[dict[str, object]],
    canvas_size: tuple[int, int],
    *,
    state: str,
) -> Image.Image:
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    talking_mouth_layers: list[Image.Image] = []
    for row in sorted(rows, key=lambda item: int(item["order"])):
        with Image.open(str(row["path"])) as image:
            rgba = image.convert("RGBA")
            if state == "talking" and str(row.get("role") or "") == "mouth":
                talking_mouth_layers.append(_open_mouth_layer(rgba))
                continue
            if rgba.size != canvas.size:
                padded = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
                padded.alpha_composite(rgba, (0, 0))
                rgba = padded
            canvas = Image.alpha_composite(canvas, rgba)
    for mouth_layer in talking_mouth_layers:
        canvas = Image.alpha_composite(canvas, mouth_layer)
    return canvas


def _open_mouth_layer(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    box = alpha.getbbox()
    if box is None:
        return rgba

    left, top, right, bottom = box
    width = max(1, right - left)
    height = max(1, bottom - top)
    crop = rgba.crop(box)
    target_height = max(height + 2, int(round(height * 1.45)))
    opened = crop.resize((width, target_height), Image.BICUBIC)
    delta = target_height - height
    next_top = max(0, top - int(round(delta * 0.35)))
    if next_top + target_height > rgba.height:
        next_top = max(0, rgba.height - target_height)

    canvas = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    canvas.alpha_composite(opened, (left, next_top))
    return canvas


def _canvas_size(layers: list[LayerArtifact]) -> tuple[int, int]:
    return (
        max(1, max(layer.width for layer in layers)),
        max(1, max(layer.height for layer in layers)),
    )


def _layer_role(name: str) -> str:
    normalized = name.strip().lower().replace(" ", "_").replace("-", "_")
    if "mouth" in normalized:
        return "mouth"
    if "eye" in normalized or normalized in {"iris", "eyelash", "eyebrow"}:
        return "eye"
    if "hair" in normalized:
        return "hair"
    if normalized in {"face_skin", "face_detail", "head", "neck", "ears", "nose"}:
        return "head"
    if "body" in normalized or normalized.endswith("wear"):
        return "body"
    return "accessory"


def _sanitize_name(name: str) -> str:
    clean = "".join(char if char.isalnum() or char in "_-" else "_" for char in name.strip())
    return clean or "Layer"


def _readme(result: ProcessResult) -> str:
    return f"""# NEKO PNGTuber Package

This archive was generated from N.E.K.O Live2D Auto Layer session `{result.session_id}`.

It is an importable NEKO PNGTuber package using the `layered_canvas_v1` adapter.
It is not a Cubism `.model3.json` or `.moc3` package.

Contents:

- `model.json`: PNGTuber model manifest
- `{PNGTUBER_METADATA_NAME}`: layered canvas metadata
- `layers/`: ordered transparent PNG layers
- `idle.png` and `talking.png`: composite fallback images
- `preview.png`: source preview when available
"""
