# -*- coding: utf-8 -*-
"""Converter for PNGTuber Plus .save projects."""

from __future__ import annotations

import base64
import io
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


VECTOR_RE = re.compile(r"Vector2\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")


@dataclass
class PlusLayer:
    key: str
    order: int
    identification: str
    parent_id: str
    path: str
    pos: tuple[float, float]
    absolute_pos: tuple[float, float]
    zindex: int
    show_talk: int
    show_blink: int
    frames: int
    image: Image.Image
    metadata: dict


def _parse_vector2(value) -> tuple[float, float]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return float(value[0]), float(value[1])
    match = VECTOR_RE.search(str(value or ""))
    if not match:
        return 0.0, 0.0
    return float(match.group(1)), float(match.group(2))


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_external_path(package_dir: Path, raw_path: str) -> Path | None:
    if not raw_path:
        return None
    normalized = raw_path.replace("\\", "/")
    filename = normalized.split("/")[-1]
    if not filename:
        return None
    direct = package_dir / filename
    if direct.exists():
        return direct
    matches = sorted(package_dir.rglob(filename))
    return matches[0] if matches else None


def _load_layer_image(package_dir: Path, layer_data: dict) -> Image.Image | None:
    image_data = layer_data.get("imageData")
    if isinstance(image_data, str) and image_data.strip():
        raw = base64.b64decode(image_data)
        return Image.open(io.BytesIO(raw)).convert("RGBA")

    external_path = _resolve_external_path(package_dir, str(layer_data.get("path") or ""))
    if external_path and external_path.exists():
        return Image.open(external_path).convert("RGBA")
    return None


def _first_frame(image: Image.Image, frames: int) -> Image.Image:
    if frames <= 1:
        return image
    width, height = image.size
    if width >= height * frames:
        return image.crop((0, 0, max(1, width // frames), height))
    if height >= width * frames:
        return image.crop((0, 0, width, max(1, height // frames)))
    return image


def _build_layers(package_dir: Path, save_data: dict) -> list[PlusLayer]:
    raw_layers = []
    for key, value in save_data.items():
        if not str(key).isdigit() or not isinstance(value, dict):
            continue
        image = _load_layer_image(package_dir, value)
        if image is None:
            continue
        frames = max(1, _to_int(value.get("frames"), 1))
        image = _first_frame(image, frames)
        raw_layers.append({
            "key": str(key),
            "order": int(key),
            "identification": str(value.get("identification") or ""),
            "parent_id": str(value.get("parentId") or ""),
            "path": str(value.get("path") or ""),
            "pos": _parse_vector2(value.get("pos")),
            "zindex": _to_int(value.get("zindex")),
            "show_talk": _to_int(value.get("showTalk")),
            "show_blink": _to_int(value.get("showBlink")),
            "frames": frames,
            "image": image,
            "metadata": {
                item_key: item_value
                for item_key, item_value in value.items()
                if item_key != "imageData"
            },
        })

    by_identification = {layer["identification"]: layer for layer in raw_layers if layer["identification"]}
    absolute_cache: dict[str, tuple[float, float]] = {}

    def absolute_position(layer: dict, visiting: set[str] | None = None) -> tuple[float, float]:
        key = layer["key"]
        if key in absolute_cache:
            return absolute_cache[key]
        visiting = visiting or set()
        if key in visiting:
            return layer["pos"]
        visiting.add(key)
        x, y = layer["pos"]
        parent = by_identification.get(layer["parent_id"])
        if parent:
            px, py = absolute_position(parent, visiting)
            x += px
            y += py
        absolute_cache[key] = (x, y)
        return x, y

    layers = []
    for raw in raw_layers:
        layers.append(PlusLayer(
            key=raw["key"],
            order=raw["order"],
            identification=raw["identification"],
            parent_id=raw["parent_id"],
            path=raw["path"],
            pos=raw["pos"],
            absolute_pos=absolute_position(raw),
            zindex=raw["zindex"],
            show_talk=raw["show_talk"],
            show_blink=raw["show_blink"],
            frames=raw["frames"],
            image=raw["image"],
            metadata=raw["metadata"],
        ))
    return layers


def _included_for_state(layer: PlusLayer, state: str) -> bool:
    if layer.show_talk == 0:
        return True
    if state == "idle":
        return layer.show_talk == 1
    if state == "talking":
        return layer.show_talk == 2
    return False


def _bounds_for_layers(layers: list[PlusLayer]) -> tuple[int, int, int, int]:
    bounds = []
    for layer in layers:
        x, y = layer.absolute_pos
        w, h = layer.image.size
        bounds.append((x, y, x + w, y + h))
    min_x = int(min(item[0] for item in bounds))
    min_y = int(min(item[1] for item in bounds))
    max_x = int(max(item[2] for item in bounds))
    max_y = int(max(item[3] for item in bounds))
    return min_x, min_y, max(1, max_x - min_x), max(1, max_y - min_y)


def _compose_state(layers: list[PlusLayer], state: str, out_path: Path, bounds: tuple[int, int, int, int]) -> None:
    included = [layer for layer in layers if _included_for_state(layer, state)]
    if not included:
        raise ValueError(f"PNGTuber Plus .save has no visible {state} layers")

    min_x, min_y, width, height = bounds
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for layer in sorted(included, key=lambda item: (item.zindex, item.order)):
        x, y = layer.absolute_pos
        canvas.alpha_composite(layer.image, (int(round(x - min_x)), int(round(y - min_y))))
    canvas.save(out_path)


def _safe_layer_filename(order: int, raw_id: str) -> str:
    safe_id = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(raw_id or order))
    return f"plus_{order:04d}_{safe_id}.png"


def _export_layer_assets(package_dir: Path, layers: list[PlusLayer], bounds: tuple[int, int, int, int]) -> list[dict]:
    layers_dir = package_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)
    min_x, min_y, _, _ = bounds
    exported = []
    for layer in layers:
        filename = _safe_layer_filename(layer.order, layer.identification or layer.key)
        rel_path = f"layers/{filename}"
        layer.image.save(package_dir / rel_path)
        x, y = layer.absolute_pos
        exported.append({
            "image": rel_path,
            "key": layer.key,
            "identification": layer.identification,
            "parentId": layer.parent_id,
            "path": layer.path,
            "order": layer.order,
            "zindex": layer.zindex,
            "x": round(x - min_x, 3),
            "y": round(y - min_y, 3),
            "width": layer.image.width,
            "height": layer.image.height,
            "showTalk": layer.show_talk,
            "showBlink": layer.show_blink,
            "frames": layer.frames,
            "metadata": layer.metadata,
        })
    return exported


def _metadata_for(package_dir: Path, layers: list[PlusLayer], save_file: Path, warnings: list[str], bounds: tuple[int, int, int, int]) -> dict:
    _, _, width, height = bounds
    return {
        "adapter_version": 1,
        "runtime": "layered_canvas",
        "source_format": "pngtuber_plus_save",
        "source_file": save_file.name,
        "warnings": warnings,
        "capabilities": {
            "speech_layers": True,
            "blink_layers": True,
            "hotkeys": False,
            "physics": False,
            "mesh": False,
        },
        "canvas": {"width": width, "height": height},
        "blink": {"enabled": True, "interval_min_ms": 2800, "interval_max_ms": 5200, "duration_ms": 140},
        "layers": _export_layer_assets(package_dir, layers, bounds),
    }


def import_pngtuber_plus_save(package_dir: Path, save_file: Path, fallback_model_name: str) -> dict:
    with save_file.open("r", encoding="utf-8") as f:
        save_data = json.load(f)
    if not isinstance(save_data, dict):
        raise ValueError("PNGTuber Plus .save is not a valid JSON object")

    layers = _build_layers(package_dir, save_data)
    if not layers:
        raise ValueError("PNGTuber Plus .save did not contain decodable layers")

    bounds = _bounds_for_layers(layers)
    _compose_state(layers, "idle", package_dir / "idle.png", bounds)
    _compose_state(layers, "talking", package_dir / "talking.png", bounds)

    source_copy = package_dir / "source.save"
    if save_file.resolve() != source_copy.resolve():
        shutil.copy2(save_file, source_copy)

    warnings = [
        "PNGTuber Plus project was imported through layered_canvas_v1. Speech and blink layers are supported first; physics and multi-frame animation are preserved as metadata for later runtime support."
    ]
    metadata = _metadata_for(package_dir, layers, save_file, warnings, bounds)
    with (package_dir / "metadata.pngtuber-plus.json").open("w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    model_name = save_file.stem or fallback_model_name
    model_json = {
        "name": model_name,
        "model_type": "pngtuber",
        "source_format": "pngtuber_plus_save",
        "pngtuber": {
            "idle_image": "idle.png",
            "talking_image": "talking.png",
            "layered_metadata": "metadata.pngtuber-plus.json",
            "adapter": "layered_canvas_v1",
            "source_type": "pngtuber_plus_save",
            "scale": 1,
            "offset_x": 0,
            "offset_y": 0,
            "mirror": False,
        },
    }
    with (package_dir / "model.json").open("w", encoding="utf-8") as f:
        json.dump(model_json, f, ensure_ascii=False, indent=2)

    return {
        "source_format": "pngtuber_plus_save",
        "model_name": model_name,
        "model_json": model_json,
        "message": "PNGTuber Plus model imported with layered adapter v1. Speech and blink layers are enabled; physics and multi-frame animation are preserved for future support.",
        "warnings": warnings,
    }
