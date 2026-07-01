# -*- coding: utf-8 -*-
"""Converter for PNGTubeRemix .pngRemix packages."""

from __future__ import annotations

import base64
import io
import json
import math
import shutil
from pathlib import Path

from PIL import Image, ImageOps

from .godot_variant import load_variant_file


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PNGTubeRemixConversionError(ValueError):
    pass


def identify_pngtube_remix(path: Path) -> dict:
    data = path.read_bytes()
    png_count = data.count(PNG_SIGNATURE)
    markers = []
    for marker in (b"sprites_array", b"mouth", b"position", b"scale", b"rotation"):
        if marker in data:
            markers.append(marker.decode("ascii"))
    return {
        "source_format": "pngtube_remix_pngremix",
        "file": path.name,
        "embedded_png_count": png_count,
        "markers": markers,
        "warnings": [f"Detected {png_count} embedded PNG signatures"] if png_count else [],
    }


def _vec(value, default=(0.0, 0.0)) -> tuple[float, float]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return float(value[0]), float(value[1])
    return default


def _state_for(sprite: dict, index: int = 0) -> dict:
    states = sprite.get("states") or []
    if isinstance(states, list) and len(states) > index and isinstance(states[index], dict):
        return states[index]
    return {}


def _float_value(value, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _z_as_relative(sprite: dict, state: dict) -> bool:
    raw = state.get("z_as_relative")
    if raw is None:
        raw = sprite.get("z_as_relative")
    return raw is not False


def _effective_z_index(sprite: dict, state_index: int, sprite_by_id: dict) -> float:
    total = 0.0
    current = sprite
    visited = set()
    while isinstance(current, dict):
        sprite_id = current.get("sprite_id")
        if sprite_id in visited:
            break
        visited.add(sprite_id)
        state = _state_for(current, state_index)
        total += _float_value(state.get("z_index"))
        if not _z_as_relative(current, state):
            break
        current = sprite_by_id.get(current.get("parent_id"))
    return total


def _image_from_sprite(sprite: dict, image_map: dict[int, dict]) -> Image.Image | None:
    raw = sprite.get("img")
    if isinstance(raw, str):
        raw = base64.b64decode(raw)
    if not raw and sprite.get("image_id") in image_map:
        raw = image_map[sprite["image_id"]].get("runtime_texture") or image_map[sprite["image_id"]].get("image_data")
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        return None
    image = Image.open(io.BytesIO(raw))
    try:
        image.seek(0)
    except EOFError:
        pass
    return image.convert("RGBA")


def _state_position_with_offset(state: dict) -> tuple[float, float]:
    pos_x, pos_y = _vec(state.get("position"))
    off_x, off_y = _vec(state.get("offset"))
    return pos_x + off_x, pos_y + off_y


def _absolute_position(sprite: dict, state: dict, state_by_id: dict, sprite_by_id: dict, cache: dict, visiting: set) -> tuple[float, float]:
    sprite_id = sprite.get("sprite_id")
    if sprite_id in cache:
        return cache[sprite_id]
    if sprite_id in visiting:
        return _state_position_with_offset(state)
    visiting.add(sprite_id)
    x, y = _state_position_with_offset(state)
    parent_id = sprite.get("parent_id")
    parent = sprite_by_id.get(parent_id)
    if parent is not None:
        px, py = _absolute_position(parent, state_by_id.get(parent_id, {}), state_by_id, sprite_by_id, cache, visiting)
        x += px
        y += py
    cache[sprite_id] = (x, y)
    return x, y


def _has_inactive_asset_ancestor(sprite: dict, sprite_by_id: dict) -> bool:
    current = sprite
    visited = set()
    while isinstance(current, dict):
        sprite_id = current.get("sprite_id")
        if sprite_id in visited:
            return False
        visited.add(sprite_id)
        if current.get("is_asset") and not current.get("was_active_before", False):
            return True
        parent_id = current.get("parent_id")
        current = sprite_by_id.get(parent_id)
    return False


def _has_hidden_ancestor_for_state(sprite: dict, sprite_by_id: dict, state_index: int) -> bool:
    parent_id = sprite.get("parent_id")
    current = sprite_by_id.get(parent_id)
    visited = set()
    while isinstance(current, dict):
        sprite_id = current.get("sprite_id")
        if sprite_id in visited:
            return False
        visited.add(sprite_id)
        parent_state = _state_for(current, state_index)
        if parent_state.get("visible", True) is False:
            return True
        parent_id = current.get("parent_id")
        current = sprite_by_id.get(parent_id)
    return False


def _effective_toggle_for_state(
    sprite: dict,
    state: dict,
    sprite_by_id: dict,
    state_index: int,
    toggle_key: str,
    value_key: str,
    default_value: bool,
) -> tuple[bool, bool]:
    current = sprite
    current_state = state
    visited = set()
    while isinstance(current, dict):
        sprite_id = current.get("sprite_id")
        if sprite_id in visited:
            break
        visited.add(sprite_id)
        if current_state.get(toggle_key):
            return True, bool(current_state.get(value_key, default_value))
        parent_id = current.get("parent_id")
        current = sprite_by_id.get(parent_id)
        current_state = _state_for(current, state_index) if isinstance(current, dict) else {}
    return False, bool(state.get(value_key, default_value))


def _layer_visible_base(sprite: dict, state: dict) -> bool:
    if state.get("folder"):
        return False
    if state.get("visible", True) is False:
        return False
    if sprite.get("is_asset") and not sprite.get("was_active_before", True) and not state.get("visible", False):
        return False
    return True


def _sprite_has_asset_action(sprite: dict) -> bool:
    if isinstance(sprite.get("saved_event"), dict):
        return True
    return any(isinstance(event, dict) for event in (sprite.get("saved_disappear") or []))


def _sprite_has_visible_state(sprite: dict, sprite_by_id: dict) -> bool:
    states = sprite.get("states") or []
    if not isinstance(states, list) or not states:
        return _layer_visible_base(sprite, {}) or _sprite_has_asset_action(sprite)
    for index, state in enumerate(states):
        if not isinstance(state, dict):
            continue
        if not _layer_visible_base(sprite, state):
            continue
        if _has_hidden_ancestor_for_state(sprite, sprite_by_id, index):
            continue
        return True
    return _sprite_has_asset_action(sprite)


def _layer_visible_for_state(layer: dict, mode: str, blink: bool = False) -> bool:
    if layer.get("inactive_asset_ancestor"):
        return False
    state = layer.get("state") or {}
    if state.get("ancestor_visible") is False or layer.get("ancestor_visible") is False:
        return False
    should_talk = bool(state.get("effective_should_talk", state.get("should_talk", False)))
    open_mouth = bool(state.get("effective_open_mouth", state.get("open_mouth", False)))
    if mode == "idle" and should_talk and open_mouth:
        return False
    if mode == "talking" and should_talk and not open_mouth:
        return False

    should_blink = bool(state.get("effective_should_blink", state.get("should_blink", False)))
    if should_blink:
        open_eyes = bool(state.get("effective_open_eyes", state.get("open_eyes", True)))
        if blink and open_eyes:
            return False
        if not blink and not open_eyes:
            return False
    return True


def _json_safe_state(state: dict) -> dict:
    allowed = {}
    for key in (
        "xFrq",
        "xAmp",
        "yFrq",
        "yAmp",
        "rdragStr",
        "dragSpeed",
        "stretchAmount",
        "visible",
        "folder",
        "position",
        "offset",
        "scale",
        "rotation",
        "rot_frq",
        "z_index",
        "z_as_relative",
        "effective_z_index",
        "flip_sprite_h",
        "flip_sprite_v",
        "should_talk",
        "open_mouth",
        "should_blink",
        "open_eyes",
        "physics",
        "wiggle",
        "wiggle_amp",
        "wiggle_freq",
        "wiggle_physics",
        "img_animated",
        "frames",
        "hframes",
        "frame",
        "non_animated_sheet",
        "animation_speed",
        "ancestor_visible",
        "effective_should_talk",
        "effective_open_mouth",
        "effective_should_blink",
        "effective_open_eyes",
    ):
        value = state.get(key)
        if key in ("z_index", "effective_z_index") and value is not None:
            allowed[key] = round(_float_value(value), 3)
            continue
        if isinstance(value, (str, int, float, bool, list, tuple)) or value is None:
            allowed[key] = value
    return allowed


def _json_safe_vec(value, default=(0.0, 0.0)) -> list[float]:
    x, y = _vec(value, default)
    return [round(x, 3), round(y, 3)]


def _positive_int(value, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, parsed)


def _frame_grid(state: dict) -> tuple[int, int, int]:
    hframes = _positive_int(state.get("hframes"), 1)
    frames = _positive_int(state.get("frames"), hframes)
    rows = max(1, math.ceil(frames / hframes))
    try:
        frame = int(state.get("frame") or 0)
    except (TypeError, ValueError):
        frame = 0
    frame = max(0, frame)
    return hframes, rows, min(frame, frames - 1)


def _frame_size(image: Image.Image, state: dict) -> tuple[int, int]:
    hframes, rows, _ = _frame_grid(state)
    return max(1, image.width // hframes), max(1, image.height // rows)


def _parent_chain_for_sprite(sprite: dict, sprite_by_id: dict, state_index: int) -> list[dict]:
    chain = []
    current = sprite
    visited = set()
    while isinstance(current, dict):
        sprite_id = current.get("sprite_id")
        if sprite_id in visited:
            break
        visited.add(sprite_id)
        state = _state_for(current, state_index)
        scale_x, scale_y = _vec(state.get("scale"), (1.0, 1.0))
        chain.append({
            "name": current.get("sprite_name") or "",
            "sprite_id": sprite_id,
            "parent_id": current.get("parent_id"),
            "folder": bool(state.get("folder")),
            "visible": state.get("visible", True) is not False,
            "z_index": round(_float_value(state.get("z_index")), 3),
            "effective_z_index": round(_effective_z_index(current, state_index, sprite_by_id), 3),
            "position": _json_safe_vec(state.get("position")),
            "offset": _json_safe_vec(state.get("offset")),
            "scale": [round(scale_x, 3), round(scale_y, 3)],
            "rotation": round(float(state.get("rotation") or 0), 3),
            "flip_sprite_h": bool(state.get("flip_sprite_h")),
            "flip_sprite_v": bool(state.get("flip_sprite_v")),
        })
        current = sprite_by_id.get(current.get("parent_id"))
    return chain


def _state_positions_for_sprite(sprite: dict, sprite_by_id: dict) -> list[dict]:
    states = sprite.get("states") or []
    if not isinstance(states, list):
        return []
    records = []
    for index, state in enumerate(states):
        if not isinstance(state, dict):
            continue
        state_by_id = {
            item.get("sprite_id"): _state_for(item, index)
            for item in sprite_by_id.values()
            if isinstance(item, dict)
        }
        center_x, center_y = _absolute_position(sprite, state, state_by_id, sprite_by_id, {}, set())
        effective_should_talk, effective_open_mouth = _effective_toggle_for_state(
            sprite, state, sprite_by_id, index, "should_talk", "open_mouth", False
        )
        effective_should_blink, effective_open_eyes = _effective_toggle_for_state(
            sprite, state, sprite_by_id, index, "should_blink", "open_eyes", True
        )
        records.append({
            **_json_safe_state(state),
            "state_index": index,
            "ancestor_visible": not _has_hidden_ancestor_for_state(sprite, sprite_by_id, index),
            "effective_should_talk": effective_should_talk,
            "effective_open_mouth": effective_open_mouth,
            "effective_should_blink": effective_should_blink,
            "effective_open_eyes": effective_open_eyes,
            "effective_z_index": round(_effective_z_index(sprite, index, sprite_by_id), 3),
            "center_x": round(center_x, 3),
            "center_y": round(center_y, 3),
            "parent_chain": _parent_chain_for_sprite(sprite, sprite_by_id, index),
        })
    return records


def _safe_layer_filename(prefix: str, order: int, raw_id) -> str:
    safe_id = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(raw_id or order))
    return f"{prefix}_{order:04d}_{safe_id}.png"


def _prepare_layers(remix_data: dict) -> list[dict]:
    sprites = remix_data.get("sprites_array")
    if not isinstance(sprites, list):
        raise PNGTubeRemixConversionError("PNGTubeRemix model is missing sprites_array")
    image_map = {
        item.get("id"): item
        for item in remix_data.get("image_manager_data", [])
        if isinstance(item, dict) and item.get("id") is not None
    }
    sprite_by_id = {sprite.get("sprite_id"): sprite for sprite in sprites if isinstance(sprite, dict)}
    state_by_id = {sprite.get("sprite_id"): _state_for(sprite, 0) for sprite in sprites if isinstance(sprite, dict)}
    position_cache: dict = {}
    layers = []
    for order, sprite in enumerate(sprites):
        if not isinstance(sprite, dict):
            continue
        state = _state_for(sprite, 0)
        if not _sprite_has_visible_state(sprite, sprite_by_id):
            continue
        image = _image_from_sprite(sprite, image_map)
        if image is None:
            continue
        scale_x, scale_y = _vec(state.get("scale"), (1.0, 1.0))
        if state.get("flip_sprite_h") or sprite.get("flipped_h"):
            image = ImageOps.mirror(image)
        if state.get("flip_sprite_v") or sprite.get("flipped_v"):
            image = ImageOps.flip(image)
        if scale_x != 1.0 or scale_y != 1.0:
            image = image.resize((max(1, round(image.width * abs(scale_x))), max(1, round(image.height * abs(scale_y)))), Image.Resampling.LANCZOS)
        center_x, center_y = _absolute_position(sprite, state, state_by_id, sprite_by_id, position_cache, set())
        ancestor_visible = not _has_hidden_ancestor_for_state(sprite, sprite_by_id, 0)
        effective_should_talk, effective_open_mouth = _effective_toggle_for_state(
            sprite, state, sprite_by_id, 0, "should_talk", "open_mouth", False
        )
        effective_should_blink, effective_open_eyes = _effective_toggle_for_state(
            sprite, state, sprite_by_id, 0, "should_blink", "open_eyes", True
        )
        effective_z_index = _effective_z_index(sprite, 0, sprite_by_id)
        layer_state = {
            **state,
            "ancestor_visible": ancestor_visible,
            "effective_should_talk": effective_should_talk,
            "effective_open_mouth": effective_open_mouth,
            "effective_should_blink": effective_should_blink,
            "effective_open_eyes": effective_open_eyes,
            "effective_z_index": effective_z_index,
        }
        frame_width, frame_height = _frame_size(image, state)
        layers.append({
            "order": order,
            "name": sprite.get("sprite_name") or "",
            "sprite_id": sprite.get("sprite_id"),
            "parent_id": sprite.get("parent_id"),
            "sprite_type": sprite.get("sprite_type"),
            "zindex": _float_value(state.get("z_index")),
            "effective_zindex": effective_z_index,
            "inactive_asset_ancestor": _has_inactive_asset_ancestor(sprite, sprite_by_id),
            "x": center_x - frame_width / 2,
            "y": center_y - frame_height / 2,
            "image": image,
            "frame_width": frame_width,
            "frame_height": frame_height,
            "state": layer_state,
            "ancestor_visible": ancestor_visible,
            "states": _state_positions_for_sprite(sprite, sprite_by_id),
            "parent_chain": _parent_chain_for_sprite(sprite, sprite_by_id, 0),
            "asset_events": {
                "show": sprite.get("saved_event") if isinstance(sprite.get("saved_event"), dict) else None,
                "hide": [
                    event for event in (sprite.get("saved_disappear") or [])
                    if isinstance(event, dict)
                ],
            },
        })
    if not layers:
        raise PNGTubeRemixConversionError("PNGTubeRemix model has no visible PNG layers")
    return layers


def _bounds_for_layers(layers: list[dict]) -> tuple[int, int, int, int]:
    bounded_layers = [
        layer for layer in layers
        if not layer.get("inactive_asset_ancestor") or (layer.get("asset_events") or {}).get("show")
    ] or layers
    rectangles = []
    for layer in bounded_layers:
        layer_has_visible_state = False
        for state in layer.get("states") or []:
            if state.get("folder") or state.get("visible", True) is False or state.get("ancestor_visible") is False:
                continue
            frame_width, frame_height = _frame_size(layer["image"], state)
            x = float(state.get("center_x", 0)) - frame_width / 2
            y = float(state.get("center_y", 0)) - frame_height / 2
            rectangles.append((x, y, x + frame_width, y + frame_height))
            layer_has_visible_state = True
        if not layer_has_visible_state:
            rectangles.append((
                layer["x"],
                layer["y"],
                layer["x"] + layer.get("frame_width", layer["image"].width),
                layer["y"] + layer.get("frame_height", layer["image"].height),
            ))
    min_x = min(x1 for x1, _, _, _ in rectangles)
    min_y = min(y1 for _, y1, _, _ in rectangles)
    max_x = max(x2 for _, _, x2, _ in rectangles)
    max_y = max(y2 for _, _, _, y2 in rectangles)
    return (
        int(round(min_x)),
        int(round(min_y)),
        max(1, int(round(max_x - min_x))),
        max(1, int(round(max_y - min_y))),
    )


def _layer_draw_z_index(layer: dict) -> float:
    state = layer.get("state") or {}
    return _float_value(
        state.get(
            "effective_z_index",
            layer.get("effective_zindex", state.get("z_index", layer.get("zindex", 0))),
        )
    )


def _compose(layers: list[dict], mode: str, out_path: Path, bounds: tuple[int, int, int, int]) -> None:
    included = [layer for layer in layers if _layer_visible_for_state(layer, mode)]
    if not included:
        raise PNGTubeRemixConversionError(f"PNGTubeRemix model has no visible {mode} layers")
    min_x, min_y, width, height = bounds
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for layer in sorted(included, key=lambda item: (_layer_draw_z_index(item), item["order"])):
        state = layer.get("state") or {}
        frame_width, frame_height = layer.get("frame_width", layer["image"].width), layer.get("frame_height", layer["image"].height)
        hframes, _, frame = _frame_grid(state)
        sx = (frame % hframes) * frame_width
        sy = (frame // hframes) * frame_height
        frame_image = layer["image"].crop((sx, sy, sx + frame_width, sy + frame_height))
        canvas.alpha_composite(frame_image, (round(layer["x"] - min_x), round(layer["y"] - min_y)))
    canvas.save(out_path)


def _export_layer_assets(package_dir: Path, layers: list[dict], bounds: tuple[int, int, int, int]) -> list[dict]:
    layers_dir = package_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)
    min_x, min_y, _, _ = bounds
    exported = []
    for layer in layers:
        filename = _safe_layer_filename("remix", layer["order"], layer.get("sprite_id"))
        rel_path = f"layers/{filename}"
        layer["image"].save(package_dir / rel_path)
        state_records = []
        for state in layer.get("states") or []:
            frame_width, frame_height = _frame_size(layer["image"], state)
            state_records.append({
                **state,
                "frame_width": frame_width,
                "frame_height": frame_height,
                "image_width": layer["image"].width,
                "image_height": layer["image"].height,
                "x": round(float(state.get("center_x", 0)) - frame_width / 2 - min_x, 3),
                "y": round(float(state.get("center_y", 0)) - frame_height / 2 - min_y, 3),
            })
        exported.append({
            "image": rel_path,
            "name": layer.get("name") or "",
            "sprite_id": layer.get("sprite_id"),
            "parent_id": layer.get("parent_id"),
            "sprite_type": layer.get("sprite_type"),
            "inactive_asset_ancestor": bool(layer.get("inactive_asset_ancestor")),
            "ancestor_visible": bool(layer.get("ancestor_visible", True)),
            "order": layer["order"],
            "zindex": layer["zindex"],
            "effective_zindex": round(_float_value(layer.get("effective_zindex", layer["zindex"])), 3),
            "x": round(layer["x"] - min_x, 3),
            "y": round(layer["y"] - min_y, 3),
            "width": layer.get("frame_width", layer["image"].width),
            "height": layer.get("frame_height", layer["image"].height),
            "image_width": layer["image"].width,
            "image_height": layer["image"].height,
            "base_scale": list(_vec((layer.get("state") or {}).get("scale"), (1.0, 1.0))),
            "base_flip_h": bool((layer.get("state") or {}).get("flip_sprite_h")),
            "base_flip_v": bool((layer.get("state") or {}).get("flip_sprite_v")),
            "parent_chain": layer.get("parent_chain") or [],
            "state": _json_safe_state(layer.get("state") or {}),
            "states": state_records,
            "asset_events": layer.get("asset_events") or {},
        })
    return exported


def _event_properties(event) -> dict:
    if not isinstance(event, dict):
        return {}
    props = event.get("properties")
    return props if isinstance(props, dict) else {}


def _hotkey_label(props: dict) -> str:
    parts = []
    if props.get("ctrl_pressed"):
        parts.append("Ctrl")
    if props.get("shift_pressed"):
        parts.append("Shift")
    if props.get("alt_pressed"):
        parts.append("Alt")
    if props.get("meta_pressed"):
        parts.append("Meta")
    keycode = int(props.get("keycode") or props.get("physical_keycode") or 0)
    if 48 <= keycode <= 57 or 65 <= keycode <= 90:
        parts.append(chr(keycode))
    elif 4194332 <= keycode <= 4194343:
        parts.append(f"F{keycode - 4194331}")
    elif keycode:
        parts.append(str(keycode))
    return "+".join(parts)


def _input_event_summary(event) -> dict:
    props = _event_properties(event)
    keycode = int(props.get("keycode") or props.get("physical_keycode") or 0)
    return {
        "key": _hotkey_label(props),
        "keycode": keycode,
        "ctrl": bool(props.get("ctrl_pressed")),
        "shift": bool(props.get("shift_pressed")),
        "alt": bool(props.get("alt_pressed")),
        "meta": bool(props.get("meta_pressed")),
    }


def _event_signature(event) -> tuple[int, bool, bool, bool, bool] | None:
    summary = _input_event_summary(event)
    if not summary["keycode"]:
        return None
    return (
        int(summary["keycode"]),
        bool(summary["ctrl"]),
        bool(summary["shift"]),
        bool(summary["alt"]),
        bool(summary["meta"]),
    )


def _asset_actions(layers: list[dict]) -> list[dict]:
    actions: dict[tuple[int, bool, bool, bool, bool], dict] = {}
    ordered_signatures: list[tuple[int, bool, bool, bool, bool]] = []

    def action_for(event) -> dict | None:
        signature = _event_signature(event)
        if signature is None:
            return None
        if signature not in actions:
            actions[signature] = {
                **_input_event_summary(event),
                "show_sprite_ids": [],
                "hide_sprite_ids": [],
            }
            ordered_signatures.append(signature)
        return actions[signature]

    for layer in layers:
        sprite_id = layer.get("sprite_id")
        if sprite_id is None:
            continue
        events = layer.get("asset_events") or {}
        show_action = action_for(events.get("show"))
        if show_action is not None:
            show_action["show_sprite_ids"].append(sprite_id)
        for event in events.get("hide") or []:
            hide_action = action_for(event)
            if hide_action is not None:
                hide_action["hide_sprite_ids"].append(sprite_id)

    return [
        {
            **actions[signature],
            "show_sprite_ids": list(dict.fromkeys(actions[signature]["show_sprite_ids"])),
            "hide_sprite_ids": list(dict.fromkeys(actions[signature]["hide_sprite_ids"])),
        }
        for signature in ordered_signatures
    ]


def _normalized_hotkeys(input_array) -> list[dict]:
    if not isinstance(input_array, list):
        return []
    hotkeys = []
    for index, item in enumerate(input_array):
        if not isinstance(item, dict):
            continue
        event = item.get("hot_key") if isinstance(item.get("hot_key"), dict) else item
        props = _event_properties(event)
        if not props:
            props = item.get("properties") if isinstance(item.get("properties"), dict) else item
        keycode = int(props.get("keycode") or props.get("physical_keycode") or 0)
        hotkeys.append({
            "state_index": index,
            "state_name": item.get("state_name") or item.get("name") or "",
            "key": _hotkey_label(props),
            "keycode": keycode,
            "ctrl": bool(props.get("ctrl_pressed")),
            "shift": bool(props.get("shift_pressed")),
            "alt": bool(props.get("alt_pressed")),
            "meta": bool(props.get("meta_pressed")),
        })
    return hotkeys


def _has_motion_state(state: dict) -> bool:
    return (
        abs(float(state.get("xAmp") or 0)) > 0.0001 and abs(float(state.get("xFrq") or 0)) > 0.0001
    ) or (
        abs(float(state.get("yAmp") or 0)) > 0.0001 and abs(float(state.get("yFrq") or 0)) > 0.0001
    ) or (
        abs(float(state.get("wiggle_amp") or 0)) > 0.0001
        and abs(float(state.get("wiggle_freq") or state.get("rot_frq") or 0)) > 0.0001
    )


def _has_physics_state(state: dict) -> bool:
    return bool(state.get("physics")) or bool(state.get("wiggle")) or _has_motion_state(state)


def _has_motion_layers(layers: list[dict]) -> bool:
    for layer in layers:
        for state in layer.get("states") or []:
            if _has_motion_state(state):
                return True
    return False


def _has_physics_layers(layers: list[dict]) -> bool:
    for layer in layers:
        for state in layer.get("states") or []:
            if _has_physics_state(state):
                return True
    return False


def _metadata(remix_data: dict, remix_file: Path, package_dir: Path, warnings: list[str], layers: list[dict], bounds: tuple[int, int, int, int]) -> dict:
    sprites = remix_data.get("sprites_array") or []
    exported_layers = _export_layer_assets(package_dir, layers, bounds)
    _, _, width, height = bounds
    input_array = remix_data.get("input_array")
    settings = remix_data.get("settings_dict")
    return {
        "adapter_version": 1,
        "runtime": "layered_canvas",
        "source_format": "pngtube_remix_pngremix",
        "source_file": remix_file.name,
        "warnings": warnings,
        "capabilities": {
            "speech_layers": True,
            "blink_layers": True,
            "hotkeys": False,
            "motion_layers": _has_motion_layers(layers),
            "physics": _has_physics_layers(layers),
            "mesh": False,
        },
        "canvas": {"width": width, "height": height},
        "blink": {"enabled": True, "interval_min_ms": 2800, "interval_max_ms": 5200, "duration_ms": 140},
        "state_count": max((len(layer.get("states") or []) for layer in layers), default=1),
        "hotkeys": [],
        "state_hotkeys": _normalized_hotkeys(input_array),
        "raw_hotkeys": input_array if isinstance(input_array, list) else [],
        "asset_actions": _asset_actions(layers),
        "settings": settings if isinstance(settings, dict) else {},
        "layers": exported_layers,
        "sprite_count": len(sprites) if isinstance(sprites, list) else 0,
    }


def import_pngtube_remix_model(package_dir: Path, remix_file: Path, fallback_model_name: str) -> dict:
    try:
        remix_data = load_variant_file(remix_file.read_bytes())
        layers = _prepare_layers(remix_data)
        bounds = _bounds_for_layers(layers)
        _compose(layers, "idle", package_dir / "idle.png", bounds)
        _compose(layers, "talking", package_dir / "talking.png", bounds)
    except Exception as exc:
        raise PNGTubeRemixConversionError(str(exc)) from exc

    source_copy = package_dir / "source.pngRemix"
    if remix_file.resolve() != source_copy.resolve():
        shutil.copy2(remix_file, source_copy)

    warnings = [
        "PNGTubeRemix project was imported through layered_canvas_v1. Speech, blink layers, states, and asset actions are supported; source state hotkeys are preserved as metadata but are not bound at runtime."
    ]
    metadata = _metadata(remix_data, remix_file, package_dir, warnings, layers, bounds)
    with (package_dir / "metadata.pngtube-remix.json").open("w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    model_name = remix_file.stem or fallback_model_name
    model_json = {
        "name": model_name,
        "model_type": "pngtuber",
        "source_format": "pngtube_remix_pngremix",
        "pngtuber": {
            "idle_image": "idle.png",
            "talking_image": "talking.png",
            "layered_metadata": "metadata.pngtube-remix.json",
            "adapter": "layered_canvas_v1",
            "source_type": "pngtube_remix_pngremix",
            "scale": 1,
            "offset_x": 0,
            "offset_y": 0,
            "mirror": False,
        },
    }
    with (package_dir / "model.json").open("w", encoding="utf-8") as f:
        json.dump(model_json, f, ensure_ascii=False, indent=2)

    return {
        "source_format": "pngtube_remix_pngremix",
        "model_name": model_name,
        "model_json": model_json,
        "message": "PNGTubeRemix model imported with layered adapter v1. Speech, blink layers, states, and asset actions are enabled; source state hotkeys are preserved as metadata only.",
        "warnings": warnings,
    }
