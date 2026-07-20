# -*- coding: utf-8 -*-
"""Converter for PNGTuber Plus .save projects."""

from __future__ import annotations

import base64
import binascii
import io
import ast
import json
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError


VECTOR_RE = re.compile(r"Vector2\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")
PLUS_VISIBLE_VALUES = {0, 10, 20, 30, 1, 21, 12, 32, 3, 13, 4, 15, 26, 36, 27, 38}
PLUS_COSTUME_COUNT = 10
PLUS_DEFAULT_COSTUME_KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]
PLUS_DEFAULT_BLINK_SPEED = 1.0
PLUS_DEFAULT_BLINK_CHANCE = 200


@dataclass
class PlusLayer:
    key: str
    order: int
    identification: str
    parent_id: str
    path: str
    pos: tuple[float, float]
    offset: tuple[float, float]
    absolute_pos: tuple[float, float]
    draw_pos: tuple[float, float]
    zindex: int
    drag: int
    rot_drag: float
    rot_limit_min: float
    rot_limit_max: float
    show_talk: int
    show_blink: int
    frames: int
    anim_speed: float
    frame_width: int
    frame_height: int
    stretch_amount: float
    ignore_bounce: bool
    clipped: bool
    toggle: str
    costume_layers: list[int]
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


def _to_float(value, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _to_optional_id(value) -> str:
    if value is None:
        return ""
    text = str(value)
    return "" if text.lower() in {"", "none", "null"} else text


def _normalize_toggle(value) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"", "none", "null"} else text


def _normalize_costume_layers(value) -> list[int]:
    if isinstance(value, str):
        try:
            value = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            value = []
    if not isinstance(value, list):
        value = []
    normalized = [1 if _to_int(item, 0) == 1 else 0 for item in value[:PLUS_COSTUME_COUNT]]
    if len(normalized) < PLUS_COSTUME_COUNT:
        normalized.extend([1] * (PLUS_COSTUME_COUNT - len(normalized)))
    return normalized


def _load_plus_settings(package_dir: Path, asset_dir: Path | None = None) -> dict:
    roots = [asset_dir, package_dir] if asset_dir and asset_dir.resolve() != package_dir.resolve() else [package_dir]
    candidates = []
    for root in roots:
        candidates.append(root / "settings.pngtp")
        candidates.extend(sorted(root.rglob("settings.pngtp")))
    seen = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or not candidate.exists():
            continue
        seen.add(resolved)
        try:
            with candidate.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def _keycode_for_key(key: str) -> int:
    if len(key) == 1:
        return ord(key.upper())
    return 0


def _normalized_costume_hotkeys(settings: dict) -> list[dict]:
    raw_keys = settings.get("costumeKeys") if isinstance(settings, dict) else None
    if not isinstance(raw_keys, list):
        raw_keys = PLUS_DEFAULT_COSTUME_KEYS
    keys = list(raw_keys[:PLUS_COSTUME_COUNT])
    if len(keys) < PLUS_COSTUME_COUNT:
        keys.extend(PLUS_DEFAULT_COSTUME_KEYS[len(keys):])

    seen = set()
    hotkeys = []
    for index, raw_key in enumerate(keys):
        key = _normalize_toggle(raw_key)
        normalized = key.lower()
        if not key or normalized in seen:
            continue
        seen.add(normalized)
        hotkeys.append({
            "state_index": index,
            "key": key,
            "keycode": _keycode_for_key(key),
            "ctrl": False,
            "shift": False,
            "alt": False,
            "meta": False,
            "label": f"Costume {index + 1}",
            "name": f"Costume {index + 1}",
        })
    return hotkeys


def _normalized_plus_settings(settings: dict) -> dict:
    raw_settings = settings if isinstance(settings, dict) else {}
    raw_keys = raw_settings.get("costumeKeys")
    keys = list(raw_keys[:PLUS_COSTUME_COUNT]) if isinstance(raw_keys, list) else PLUS_DEFAULT_COSTUME_KEYS[:]
    if len(keys) < PLUS_COSTUME_COUNT:
        keys.extend(PLUS_DEFAULT_COSTUME_KEYS[len(keys):])
    keys = [str(key) if key is not None else "null" for key in keys[:PLUS_COSTUME_COUNT]]
    return {
        "settings_loaded": bool(raw_settings),
        "costumeKeys": keys,
        "blinkSpeed": _to_float(raw_settings.get("blinkSpeed"), PLUS_DEFAULT_BLINK_SPEED),
        "blinkChance": max(1, _to_int(raw_settings.get("blinkChance"), PLUS_DEFAULT_BLINK_CHANCE)),
        "bounceOnCostumeChange": bool(raw_settings.get("bounceOnCostumeChange", False)),
    }


def _blink_config_from_plus_settings(settings: dict) -> dict:
    plus_settings = _normalized_plus_settings(settings)
    blink_speed = max(0.1, _to_float(plus_settings.get("blinkSpeed"), PLUS_DEFAULT_BLINK_SPEED))
    blink_chance = max(1, _to_int(plus_settings.get("blinkChance"), PLUS_DEFAULT_BLINK_CHANCE))
    base_ms = int(round((420 / 60) * blink_speed * 1000))
    spread_ms = int(round((blink_chance / 60) * 1000))
    interval_min_ms = max(500, base_ms)
    interval_max_ms = max(interval_min_ms, base_ms + spread_ms * 2)
    return {
        "enabled": True,
        "interval_min_ms": interval_min_ms,
        "interval_max_ms": interval_max_ms,
        "duration_ms": 200,
        "source_blink_speed": blink_speed,
        "source_blink_chance": blink_chance,
    }


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _asset_roots(package_dir: Path, asset_dir: Path | None = None) -> list[Path]:
    package_root = package_dir.resolve()
    roots = []
    if asset_dir is not None:
        asset_root = asset_dir.resolve()
        if _path_is_within(asset_root, package_root):
            roots.append(asset_root)
    roots.append(package_root)
    deduped = []
    seen = set()
    for root in roots:
        if root not in seen:
            deduped.append(root)
            seen.add(root)
    return deduped


def _allowed_asset_path(candidate: Path, roots: list[Path]) -> Path | None:
    resolved = candidate.resolve()
    if any(_path_is_within(resolved, root) for root in roots):
        return resolved
    return None


def _resolve_external_path(package_dir: Path, raw_path: str, asset_dir: Path | None = None) -> Path | None:
    if not raw_path:
        return None
    normalized = raw_path.replace("\\", "/")
    filename = normalized.split("/")[-1]
    if not filename:
        return None
    roots = _asset_roots(package_dir, asset_dir)
    for root in roots:
        direct = root / normalized
        allowed_direct = _allowed_asset_path(direct, roots)
        if allowed_direct and allowed_direct.exists():
            return allowed_direct
        basename_match = root / filename
        allowed_basename = _allowed_asset_path(basename_match, roots)
        if allowed_basename and allowed_basename.exists():
            return allowed_basename
    matches = []
    for root in roots:
        matches.extend(sorted(root.rglob(filename)))
    for match in matches:
        allowed_match = _allowed_asset_path(match, roots)
        if allowed_match and allowed_match.exists():
            return allowed_match
    return None


def _load_layer_image(package_dir: Path, layer_data: dict, asset_dir: Path | None = None) -> Image.Image | None:
    image_data = layer_data.get("imageData")
    if isinstance(image_data, str) and image_data.strip():
        payload = image_data.strip()
        if payload.lower().startswith("data:") and "," in payload:
            payload = payload.split(",", 1)[1].strip()
        try:
            raw = base64.b64decode(payload, validate=True)
            return Image.open(io.BytesIO(raw)).convert("RGBA")
        except (binascii.Error, OSError, UnidentifiedImageError, ValueError):
            return None

    external_path = _resolve_external_path(package_dir, str(layer_data.get("path") or ""), asset_dir)
    if external_path and external_path.exists():
        try:
            return Image.open(external_path).convert("RGBA")
        except (OSError, UnidentifiedImageError, ValueError):
            return None
    return None


def _frame_size(image: Image.Image, frames: int) -> tuple[int, int]:
    frames = max(1, frames)
    return max(1, image.width // frames), image.height


def _first_frame(image: Image.Image, frame_width: int, frame_height: int) -> Image.Image:
    return image.crop((0, 0, frame_width, frame_height))


def _draw_position(center: tuple[float, float], offset: tuple[float, float], frame_size: tuple[int, int]) -> tuple[float, float]:
    return center[0] + offset[0] - frame_size[0] / 2, center[1] + offset[1] - frame_size[1] / 2


def _raw_layer_from_save_key(package_dir: Path, key: str, value: dict, asset_dir: Path | None = None) -> dict | None:
    image = _load_layer_image(package_dir, value, asset_dir)
    if image is None:
        return None
    frames = max(1, _to_int(value.get("frames"), 1))
    frame_width, frame_height = _frame_size(image, frames)
    return {
        "key": str(key),
        "order": int(key),
        "identification": _to_optional_id(value.get("identification")),
        "parent_id": _to_optional_id(value.get("parentId")),
        "path": str(value.get("path") or ""),
        "pos": _parse_vector2(value.get("pos")),
        "offset": _parse_vector2(value.get("offset")),
        "zindex": _to_int(value.get("zindex")),
        "drag": _to_int(value.get("drag")),
        "rot_drag": _to_float(value.get("rotDrag")),
        "rot_limit_min": _to_float(value.get("rLimitMin"), -180.0),
        "rot_limit_max": _to_float(value.get("rLimitMax"), 180.0),
        "show_talk": _to_int(value.get("showTalk")),
        "show_blink": _to_int(value.get("showBlink")),
        "frames": frames,
        "anim_speed": _to_float(value.get("animSpeed")),
        "frame_width": frame_width,
        "frame_height": frame_height,
        "stretch_amount": _to_float(value.get("stretchAmount")),
        "ignore_bounce": bool(value.get("ignoreBounce", False)),
        "clipped": bool(value.get("clipped", False)),
        "toggle": _normalize_toggle(value.get("toggle")),
        "costume_layers": _normalize_costume_layers(value.get("costumeLayers")),
        "image": image,
        "metadata": {item_key: item_value for item_key, item_value in value.items() if item_key != "imageData"},
    }


def _build_layers(package_dir: Path, save_data: dict, asset_dir: Path | None = None) -> list[PlusLayer]:
    raw_layers = []
    for key, value in save_data.items():
        if not str(key).isdigit() or not isinstance(value, dict):
            continue
        raw_layer = _raw_layer_from_save_key(package_dir, str(key), value, asset_dir)
        if raw_layer is not None:
            raw_layers.append(raw_layer)

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
        absolute_pos = absolute_position(raw)
        frame_size = (raw["frame_width"], raw["frame_height"])
        layers.append(PlusLayer(
            key=raw["key"],
            order=raw["order"],
            identification=raw["identification"],
            parent_id=raw["parent_id"],
            path=raw["path"],
            pos=raw["pos"],
            offset=raw["offset"],
            absolute_pos=absolute_pos,
            draw_pos=_draw_position(absolute_pos, raw["offset"], frame_size),
            zindex=raw["zindex"],
            drag=raw["drag"],
            rot_drag=raw["rot_drag"],
            rot_limit_min=raw["rot_limit_min"],
            rot_limit_max=raw["rot_limit_max"],
            show_talk=raw["show_talk"],
            show_blink=raw["show_blink"],
            frames=raw["frames"],
            anim_speed=raw["anim_speed"],
            frame_width=raw["frame_width"],
            frame_height=raw["frame_height"],
            stretch_amount=raw["stretch_amount"],
            ignore_bounce=raw["ignore_bounce"],
            clipped=raw["clipped"],
            toggle=raw["toggle"],
            costume_layers=raw["costume_layers"],
            image=raw["image"],
            metadata=raw["metadata"],
        ))
    return layers


def _plus_visible(show_talk: int, show_blink: int, speaking: bool, blinking: bool) -> bool:
    value = show_talk + (show_blink * 3) + (10 if speaking else 0) + (20 if blinking else 0)
    return value in PLUS_VISIBLE_VALUES


def _included_for_state(layer: PlusLayer, state: str, *, blinking: bool = False) -> bool:
    return _plus_visible(layer.show_talk, layer.show_blink, state == "talking", blinking)


def _layer_by_identification(layers: list[PlusLayer]) -> dict[str, PlusLayer]:
    return {layer.identification: layer for layer in layers if layer.identification}


def _parent_chain(layer: PlusLayer, by_identification: dict[str, PlusLayer]) -> list[str]:
    chain = []
    visited = {layer.identification} if layer.identification else set()
    parent_id = layer.parent_id
    while parent_id and parent_id not in visited:
        parent = by_identification.get(parent_id)
        if not parent:
            break
        chain.append(parent_id)
        visited.add(parent_id)
        parent_id = parent.parent_id
    return chain


def _costume_visible(layer: PlusLayer, state_index: int, by_identification: dict[str, PlusLayer]) -> bool:
    if layer.costume_layers[state_index] != 1:
        return False
    for parent_id in _parent_chain(layer, by_identification):
        parent = by_identification.get(parent_id)
        if parent and parent.costume_layers[state_index] != 1:
            return False
    return True


def _included_for_state_with_ancestors(layer: PlusLayer, state: str, by_identification: dict[str, PlusLayer]) -> bool:
    if not _included_for_state(layer, state):
        return False
    for parent_id in _parent_chain(layer, by_identification):
        parent = by_identification.get(parent_id)
        if parent and not _included_for_state(parent, state):
            return False
    return True


def _bounds_for_layers(layers: list[PlusLayer]) -> tuple[int, int, int, int]:
    bounds = []
    for layer in layers:
        x, y = layer.draw_pos
        bounds.append((x, y, x + layer.frame_width, y + layer.frame_height))
    min_x = int(min(item[0] for item in bounds))
    min_y = int(min(item[1] for item in bounds))
    max_x = int(max(item[2] for item in bounds))
    max_y = int(max(item[3] for item in bounds))
    return min_x, min_y, max(1, max_x - min_x), max(1, max_y - min_y)


def _compose_state(layers: list[PlusLayer], state: str, out_path: Path, bounds: tuple[int, int, int, int]) -> None:
    by_identification = _layer_by_identification(layers)
    included = [
        layer
        for layer in layers
        if _costume_visible(layer, 0, by_identification)
        and _included_for_state_with_ancestors(layer, state, by_identification)
    ]
    if not included:
        raise ValueError(f"PNGTuber Plus .save has no visible {state} layers")

    min_x, min_y, width, height = bounds
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for layer in sorted(included, key=lambda item: (item.zindex, item.order)):
        x, y = layer.draw_pos
        frame = _first_frame(layer.image, layer.frame_width, layer.frame_height)
        canvas.alpha_composite(frame, (int(round(x - min_x)), int(round(y - min_y))))
    canvas.save(out_path)


def _safe_layer_filename(order: int, raw_id: str) -> str:
    safe_id = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(raw_id or order))
    return f"plus_{order:04d}_{safe_id}.png"


def _state_for_layer(layer: PlusLayer, min_x: int, min_y: int) -> dict:
    x, y = layer.draw_pos
    node_origin = (layer.absolute_pos[0] - min_x, layer.absolute_pos[1] - min_y)
    sprite_offset = layer.offset
    draw_offset = (
        sprite_offset[0] - layer.frame_width / 2,
        sprite_offset[1] - layer.frame_height / 2,
    )
    animation_speed = round(layer.anim_speed / 24, 4) if layer.anim_speed > 0 else 0
    return {
        "x": round(x - min_x, 3),
        "y": round(y - min_y, 3),
        "position": [round(layer.pos[0], 3), round(layer.pos[1], 3)],
        "local_position": [round(layer.pos[0], 3), round(layer.pos[1], 3)],
        "node_origin": [round(node_origin[0], 3), round(node_origin[1], 3)],
        "sprite_offset": [round(sprite_offset[0], 3), round(sprite_offset[1], 3)],
        "draw_offset": [round(draw_offset[0], 3), round(draw_offset[1], 3)],
        "plus_transform": True,
        "center_x": round(layer.absolute_pos[0], 3),
        "center_y": round(layer.absolute_pos[1], 3),
        "offset": [round(layer.offset[0], 3), round(layer.offset[1], 3)],
        "xFrq": layer.metadata.get("xFrq", 0),
        "xAmp": layer.metadata.get("xAmp", 0),
        "yFrq": layer.metadata.get("yFrq", 0),
        "yAmp": layer.metadata.get("yAmp", 0),
        "rdragStr": layer.rot_drag,
        "dragSpeed": layer.drag,
        "stretchAmount": layer.stretch_amount,
        "rLimitMin": layer.rot_limit_min,
        "rLimitMax": layer.rot_limit_max,
        "frames": layer.frames,
        "hframes": layer.frames,
        "vframes": 1,
        "frame": 0,
        "frame_width": layer.frame_width,
        "frame_height": layer.frame_height,
        "image_width": layer.image.width,
        "image_height": layer.image.height,
        "animation_speed": animation_speed,
        "source_anim_speed": layer.anim_speed,
        "physics": bool(layer.drag or layer.rot_drag or layer.stretch_amount),
        "ignore_bounce": layer.ignore_bounce,
        "visible": True,
        "costumeLayers": layer.costume_layers,
        "toggle": layer.toggle,
        "clipped": layer.clipped,
    }


def _states_for_layer(layer: PlusLayer, min_x: int, min_y: int, by_identification: dict[str, PlusLayer]) -> list[dict]:
    base_state = _state_for_layer(layer, min_x, min_y)
    states = []
    for index in range(PLUS_COSTUME_COUNT):
        layer_visible = layer.costume_layers[index] == 1
        ancestor_visible = _costume_visible(layer, index, by_identification) if layer_visible else True
        states.append({
            **base_state,
            "state_index": index,
            "costume_index": index,
            "costume_number": index + 1,
            "visible": layer_visible,
            "ancestor_visible": ancestor_visible,
        })
    return states


def _export_layer_assets(package_dir: Path, layers: list[PlusLayer], bounds: tuple[int, int, int, int]) -> list[dict]:
    layers_dir = package_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)
    min_x, min_y, _, _ = bounds
    by_identification = _layer_by_identification(layers)
    exported = []
    for layer in layers:
        filename = _safe_layer_filename(layer.order, layer.identification or layer.key)
        rel_path = f"layers/{filename}"
        layer.image.save(package_dir / rel_path)
        x, y = layer.draw_pos
        states = _states_for_layer(layer, min_x, min_y, by_identification)
        state = states[0]
        parent_chain = _parent_chain(layer, by_identification)
        exported.append({
            "image": rel_path,
            "key": layer.key,
            "identification": layer.identification,
            "parentId": layer.parent_id,
            "parent_id": layer.parent_id,
            "parent_chain": parent_chain,
            "local_position": state["local_position"],
            "node_origin": state["node_origin"],
            "sprite_offset": state["sprite_offset"],
            "draw_offset": state["draw_offset"],
            "plus_transform": True,
            "path": layer.path,
            "order": layer.order,
            "zindex": layer.zindex,
            "x": round(x - min_x, 3),
            "y": round(y - min_y, 3),
            "width": layer.frame_width,
            "height": layer.frame_height,
            "image_width": layer.image.width,
            "image_height": layer.image.height,
            "frame_width": layer.frame_width,
            "frame_height": layer.frame_height,
            "hframes": layer.frames,
            "vframes": 1,
            "base_scale": [1, 1],
            "base_flip_h": False,
            "base_flip_v": False,
            "showTalk": layer.show_talk,
            "showBlink": layer.show_blink,
            "frames": layer.frames,
            "animation_speed": state["animation_speed"],
            "source_anim_speed": layer.anim_speed,
            "costumeLayers": layer.costume_layers,
            "toggle": layer.toggle,
            "clipped": layer.clipped,
            "state": state,
            "states": states,
            "metadata": layer.metadata,
        })
    return exported


def _has_motion_layers(layers: list[PlusLayer]) -> bool:
    return any(
        (abs(_to_float(layer.metadata.get("xAmp"))) > 0.0001 and abs(_to_float(layer.metadata.get("xFrq"))) > 0.0001)
        or (abs(_to_float(layer.metadata.get("yAmp"))) > 0.0001 and abs(_to_float(layer.metadata.get("yFrq"))) > 0.0001)
        for layer in layers
    )


def _has_physics_layers(layers: list[PlusLayer]) -> bool:
    return any(layer.drag or layer.rot_drag or layer.stretch_amount for layer in layers)


def _has_sprite_sheet_layers(layers: list[PlusLayer]) -> bool:
    return any(layer.frames > 1 and layer.anim_speed > 0 for layer in layers)


def _toggle_map(layers: list[PlusLayer]) -> dict[str, list[str]]:
    toggles: dict[str, list[str]] = {}
    for layer in layers:
        if not layer.toggle:
            continue
        layer_id = layer.identification or layer.key
        toggles.setdefault(layer.toggle, []).append(layer_id)
    return toggles


def _settings_for_costumes() -> dict:
    return {
        "states": [
            {"name": f"Costume {index + 1}", "label": f"Costume {index + 1}"}
            for index in range(PLUS_COSTUME_COUNT)
        ]
    }


def _metadata_for(package_dir: Path, layers: list[PlusLayer], save_file: Path, warnings: list[str], bounds: tuple[int, int, int, int]) -> dict:
    _, _, width, height = bounds
    has_motion = _has_motion_layers(layers)
    has_physics = _has_physics_layers(layers)
    has_sprite_sheets = _has_sprite_sheet_layers(layers)
    settings = _load_plus_settings(package_dir, save_file.parent)
    plus_settings = _normalized_plus_settings(settings)
    hotkeys = _normalized_costume_hotkeys(settings)
    toggles = _toggle_map(layers)
    return {
        "adapter_version": 2,
        "runtime": "layered_canvas",
        "source_format": "pngtuber_plus_save",
        "source_application": "pngtuber-plus",
        "source_file": save_file.name,
        "warnings": warnings,
        "capabilities": {
            "speech_layers": True,
            "blink_layers": True,
            "hotkeys": bool(hotkeys),
            "toggles": bool(toggles),
            "costumes": True,
            "motion_layers": has_motion,
            "sprite_sheet_animation": has_sprite_sheets,
            "physics": has_physics,
            "mesh": False,
        },
        "runtime_features": {
            "layer_motion": has_motion,
            "sprite_sheet_animation": has_sprite_sheets,
            "layered_breathing": True,
            "plus_transform_stack": True,
            "plus_physics": has_motion or has_physics,
            "clip_children_rect": any(layer.clipped for layer in layers),
            "costume_change_bounce": bool(plus_settings["bounceOnCostumeChange"]),
            "mesh_deformation": False,
            "physics_v2": False,
            "unsupported_features": [
                "godot_collision_polygons",
                "clip_children is approximated with rectangular sprite clipping, not alpha masks",
            ],
        },
        "canvas": {"width": width, "height": height},
        "blink": _blink_config_from_plus_settings(settings),
        "plus_settings": plus_settings,
        "state_count": PLUS_COSTUME_COUNT,
        "settings": _settings_for_costumes(),
        "hotkeys": hotkeys,
        "toggles": toggles,
        "layers": _export_layer_assets(package_dir, layers, bounds),
    }


def import_pngtuber_plus_save(package_dir: Path, save_file: Path, fallback_model_name: str) -> dict:
    with save_file.open("r", encoding="utf-8") as f:
        save_data = json.load(f)
    if not isinstance(save_data, dict):
        raise ValueError("PNGTuber Plus .save is not a valid JSON object")

    layers = _build_layers(package_dir, save_data, save_file.parent)
    if not layers:
        raise ValueError("PNGTuber Plus .save did not contain decodable layers")

    bounds = _bounds_for_layers(layers)
    _compose_state(layers, "idle", package_dir / "idle.png", bounds)
    _compose_state(layers, "talking", package_dir / "talking.png", bounds)

    source_copy = package_dir / "source.save"
    if save_file.resolve() != source_copy.resolve():
        shutil.copy2(save_file, source_copy)

    warnings = [
        "PNGTuber Plus project was imported through layered_canvas_v2. Speech, blink, costumes, hotkeys, visibility toggles, layer offsets, parent positions, wobble, and sprite-sheet metadata are supported; Godot collision/clip details are preserved as metadata."
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
        "message": "PNGTuber Plus model imported with layered adapter v2. Speech, blink, costumes, hotkeys, toggles, offsets, parent positions, and sprite-sheet metadata are enabled.",
        "warnings": warnings,
    }
