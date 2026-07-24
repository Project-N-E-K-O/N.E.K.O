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


def _float(value, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


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
    if state.get("visible", True) is False:
        return False
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
        "shared_movement",
        "updated_follow_movement",
        "rLimitMin",
        "rLimitMax",
        "should_rot_speed",
        "should_rotate",
        "mouse_delay",
        "look_at_mouse_pos",
        "look_at_mouse_pos_y",
        "mouse_rotation",
        "mouse_rotation_max",
        "mouse_scale_x",
        "mouse_scale_y",
        "pos_x_min",
        "pos_x_max",
        "pos_y_min",
        "pos_y_max",
        "pos_swap_x",
        "pos_swap_y",
        "pos_invert_x",
        "pos_invert_y",
        "rot_min",
        "rot_max",
        "rot_swap_x",
        "rot_swap_y",
        "rot_invert_x",
        "rot_invert_y",
        "scale_x_min",
        "scale_x_max",
        "scale_y_min",
        "scale_y_max",
        "scale_swap_x",
        "scale_swap_y",
        "scale_invert_x",
        "scale_invert_y",
        "follow_mouse_velocity",
        "follow_range",
        "follow_strength",
        "follow_eye",
        "gaze_eye",
        "style_eye",
        "follow_mouth",
        "follow_type",
        "follow_type2",
        "follow_type3",
        "snap_pos",
        "snap_rot",
        "snap_scale",
        "rotation_threshold",
        "chain_softness",
        "chain_rot_min",
        "chain_rot_max",
        "bone_length",
        "tip_point",
        "mesh_phys_x",
        "mesh_phys_y",
        "use_object_pos",
        "phys_eff",
        "ignore_bounce",
        "static_obj",
        "drag_snap",
        "mo_xFrq",
        "mo_xAmp",
        "mo_yFrq",
        "mo_yAmp",
        "mo_rdragStr",
        "mo_rot_frq",
        "mo_dragSpeed",
        "mo_stretchAmount",
        "mo_shared_movement",
        "mo_rLimitMin",
        "mo_rLimitMax",
        "mo_should_rot_speed",
        "mo_should_rotate",
        "mo_mouse_delay",
        "mo_look_at_mouse_pos",
        "mo_look_at_mouse_pos_y",
        "mo_mouse_rotation",
        "mo_mouse_rotation_max",
        "mo_mouse_scale_x",
        "mo_mouse_scale_y",
        "mo_pos_x_min",
        "mo_pos_x_max",
        "mo_pos_y_min",
        "mo_pos_y_max",
        "mo_pos_swap_x",
        "mo_pos_swap_y",
        "mo_pos_invert_x",
        "mo_pos_invert_y",
        "mo_rot_min",
        "mo_rot_max",
        "mo_rot_swap_x",
        "mo_rot_swap_y",
        "mo_rot_invert_x",
        "mo_rot_invert_y",
        "mo_scale_x_min",
        "mo_scale_x_max",
        "mo_scale_y_min",
        "mo_scale_y_max",
        "mo_scale_swap_x",
        "mo_scale_swap_y",
        "mo_scale_invert_x",
        "mo_scale_invert_y",
        "mo_follow_mouse_velocity",
        "mo_follow_range",
        "mo_follow_strength",
        "mo_follow_type",
        "mo_follow_type2",
        "mo_follow_type3",
        "mo_snap_pos",
        "mo_snap_rot",
        "mo_snap_scale",
        "mo_rotation_threshold",
        "mo_chain_softness",
        "mo_chain_rot_min",
        "mo_chain_rot_max",
        "mo_bone_length",
        "mo_tip_point",
        "mo_mesh_phys_x",
        "mo_mesh_phys_y",
        "mo_use_object_pos",
        "mo_phys_eff",
        "mo_ignore_bounce",
        "mo_static_obj",
        "mo_drag_snap",
        "scream_xFrq",
        "scream_xAmp",
        "scream_yFrq",
        "scream_yAmp",
        "scream_rdragStr",
        "scream_rot_frq",
        "scream_dragSpeed",
        "scream_stretchAmount",
        "scream_shared_movement",
        "scream_rLimitMin",
        "scream_rLimitMax",
        "scream_should_rot_speed",
        "scream_should_rotate",
        "scream_mouse_delay",
        "scream_look_at_mouse_pos",
        "scream_look_at_mouse_pos_y",
        "scream_mouse_rotation",
        "scream_mouse_rotation_max",
        "scream_mouse_scale_x",
        "scream_mouse_scale_y",
        "scream_pos_x_min",
        "scream_pos_x_max",
        "scream_pos_y_min",
        "scream_pos_y_max",
        "scream_pos_swap_x",
        "scream_pos_swap_y",
        "scream_pos_invert_x",
        "scream_pos_invert_y",
        "scream_rot_min",
        "scream_rot_max",
        "scream_rot_swap_x",
        "scream_rot_swap_y",
        "scream_rot_invert_x",
        "scream_rot_invert_y",
        "scream_scale_x_min",
        "scream_scale_x_max",
        "scream_scale_y_min",
        "scream_scale_y_max",
        "scream_scale_swap_x",
        "scream_scale_swap_y",
        "scream_scale_invert_x",
        "scream_scale_invert_y",
        "scream_follow_mouse_velocity",
        "scream_follow_range",
        "scream_follow_strength",
        "scream_follow_type",
        "scream_follow_type2",
        "scream_follow_type3",
        "scream_snap_pos",
        "scream_snap_rot",
        "scream_snap_scale",
        "scream_rotation_threshold",
        "scream_chain_softness",
        "scream_chain_rot_min",
        "scream_chain_rot_max",
        "scream_bone_length",
        "scream_tip_point",
        "scream_mesh_phys_x",
        "scream_mesh_phys_y",
        "scream_use_object_pos",
        "scream_phys_eff",
        "scream_ignore_bounce",
        "scream_static_obj",
        "scream_drag_snap",
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
        "vframes",
        "frame",
        "non_animated_sheet",
        "animate_to_mouse",
        "animate_to_mouse_speed",
        "animate_to_mouse_track_pos",
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


def _sign(value) -> int:
    number = _float(value, 0.0)
    return 1 if number > 0 else -1 if number < 0 else 0


def _with_updated_follow_fields(sprite: dict, state: dict) -> dict:
    """Mirror PNGTube Remix's legacy follow-field migration."""
    if not isinstance(state, dict):
        return {}
    migrated = dict(state)
    if sprite.get("updated_follow_movement", False):
        migrated["updated_follow_movement"] = True
        return migrated
    look_x = migrated.get("look_at_mouse_pos", 0.0)
    look_y = migrated.get("look_at_mouse_pos_y", 0.0)
    mouse_scale_x = migrated.get("mouse_scale_x", 0.0)
    mouse_scale_y = migrated.get("mouse_scale_y", 0.0)
    migrated["pos_x_min"] = -abs(_float(look_x))
    migrated["pos_x_max"] = abs(_float(look_x))
    migrated["pos_y_min"] = -abs(_float(look_y))
    migrated["pos_y_max"] = abs(_float(look_y))
    migrated["rot_min"] = migrated.get("mouse_rotation", 0.0)
    migrated["rot_max"] = migrated.get("mouse_rotation_max", 0.0)
    migrated["scale_x_min"] = -abs(_float(mouse_scale_x))
    migrated["scale_x_max"] = abs(_float(mouse_scale_x))
    migrated["scale_y_min"] = -abs(_float(mouse_scale_y))
    migrated["scale_y_max"] = abs(_float(mouse_scale_y))
    if _sign(look_x) < 0:
        migrated["pos_invert_x"] = True
    if _sign(look_y) < 0:
        migrated["pos_invert_y"] = True
    return migrated


def _json_safe_vec(value, default=(0.0, 0.0)) -> list[float]:
    x, y = _vec(value, default)
    return [round(x, 3), round(y, 3)]


def _json_safe_value(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    return repr(value)


def _first_present_mapping(container: dict, keys: tuple[str, ...]) -> tuple[str | None, object]:
    for key in keys:
        if key in container:
            return key, container.get(key)
    return None, None


def _normalize_point_array(value) -> list[list[float]]:
    if not isinstance(value, (list, tuple)):
        return []
    points = []
    if value and all(isinstance(item, (int, float)) for item in value):
        for index in range(0, len(value) - 1, 2):
            points.append([round(_float(value[index]), 3), round(_float(value[index + 1]), 3)])
        return points
    for item in value:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            points.append([round(_float(item[0]), 3), round(_float(item[1]), 3)])
        elif isinstance(item, dict):
            x_key = "x" if "x" in item else "u"
            y_key = "y" if "y" in item else "v"
            if x_key in item and y_key in item:
                points.append([round(_float(item[x_key]), 3), round(_float(item[y_key]), 3)])
    return points


def _normalize_triangle_array(value) -> list[list[int]]:
    if not isinstance(value, (list, tuple)):
        return []
    triangles = []
    if value and all(isinstance(item, (int, float)) for item in value):
        for index in range(0, len(value) - 2, 3):
            triangles.append([int(value[index]), int(value[index + 1]), int(value[index + 2])])
        return triangles
    for item in value:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            triangles.append([int(item[0]), int(item[1]), int(item[2])])
        elif isinstance(item, dict):
            values = item.get("indices") or item.get("points") or item.get("vertices")
            if isinstance(values, (list, tuple)) and len(values) >= 3:
                triangles.append([int(values[0]), int(values[1]), int(values[2])])
    return triangles


def _mesh_source_for_sprite(sprite: dict, state: dict) -> tuple[dict, str]:
    mesh_keys = (
        "mesh",
        "mesh_data",
        "meshData",
        "deform_mesh",
        "deformMesh",
        "polygon",
        "polygon_data",
        "mesh_dict",
    )
    key, value = _first_present_mapping(state, mesh_keys)
    if isinstance(value, dict):
        return value, f"state.{key}"
    key, value = _first_present_mapping(sprite, mesh_keys)
    if isinstance(value, dict):
        return value, f"sprite.{key}"
    return {}, ""


def _extract_mesh_geometry(sprite: dict, state: dict) -> dict:
    source, source_path = _mesh_source_for_sprite(sprite, state)
    search_roots = [source, state, sprite]
    source_prefixes = [source_path or "mesh", "state", "sprite"]
    vertex_keys = ("vertices", "vertexes", "points", "mesh_vertices", "mesh_points", "verts")
    uv_keys = ("uvs", "uv", "mesh_uvs", "mesh_uv", "texture_uvs", "tex_uvs")
    triangle_keys = ("triangles", "triangle_indices", "indices", "mesh_triangles", "faces", "polygons")
    binding_keys = ("bindings", "binds", "weights", "bone_weights", "bones", "mesh_bindings")

    vertices = []
    uvs = []
    triangles = []
    bindings = None
    source_fields: dict[str, str] = {}

    for root, prefix in zip(search_roots, source_prefixes):
        if not isinstance(root, dict):
            continue
        if not vertices:
            key, value = _first_present_mapping(root, vertex_keys)
            vertices = _normalize_point_array(value)
            if vertices and key:
                source_fields["vertices"] = f"{prefix}.{key}"
        if not uvs:
            key, value = _first_present_mapping(root, uv_keys)
            uvs = _normalize_point_array(value)
            if uvs and key:
                source_fields["uvs"] = f"{prefix}.{key}"
        if not triangles:
            key, value = _first_present_mapping(root, triangle_keys)
            triangles = _normalize_triangle_array(value)
            if triangles and key:
                source_fields["triangles"] = f"{prefix}.{key}"
        if bindings is None:
            key, value = _first_present_mapping(root, binding_keys)
            if value is not None:
                bindings = _json_safe_value(value)
                source_fields["bindings"] = f"{prefix}.{key}"

    reason = ""
    valid = True
    if len(vertices) < 3:
        valid = False
        reason = "mesh geometry missing vertices"
    elif len(uvs) != len(vertices):
        valid = False
        reason = "mesh geometry missing matching UV coordinates"
    elif not triangles:
        valid = False
        reason = "mesh geometry missing triangles"
    else:
        max_index = len(vertices) - 1
        if any(index < 0 or index > max_index for triangle in triangles for index in triangle):
            valid = False
            reason = "mesh triangle index out of range"

    return {
        "valid": valid,
        "vertices": vertices if vertices else [],
        "uvs": uvs if uvs else [],
        "triangles": triangles if triangles else [],
        "bindings": bindings if bindings is not None else [],
        "source_fields": source_fields,
        "degrade_reason": "" if valid else reason,
    }


def _positive_int(value, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, parsed)


def _frame_grid(state: dict) -> tuple[int, int, int]:
    hframes = _positive_int(state.get("hframes"), 1)
    vframes = _positive_int(state.get("vframes"), 1)
    frames = _positive_int(state.get("frames"), hframes * vframes)
    rows = max(vframes, math.ceil(frames / hframes))
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
        parent_id = current.get("parent_id")
        current = sprite_by_id.get(parent_id) if parent_id is not None else None
    return chain


def _state_positions_for_sprite(sprite: dict, sprite_by_id: dict) -> list[dict]:
    states = sprite.get("states") or []
    if not isinstance(states, list):
        return []
    records = []
    for index, state in enumerate(states):
        if not isinstance(state, dict):
            continue
        state = _with_updated_follow_fields(sprite, state)
        state_by_id = {
            item.get("sprite_id"): _state_for(item, index)
            for item in sprite_by_id.values()
            if isinstance(item, dict) and item.get("sprite_id") is not None
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
            "mesh": _extract_mesh_geometry(sprite, state),
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
    sprite_by_id = {
        sprite.get("sprite_id"): sprite
        for sprite in sprites
        if isinstance(sprite, dict) and sprite.get("sprite_id") is not None
    }
    state_by_id = {
        sprite.get("sprite_id"): _state_for(sprite, 0)
        for sprite in sprites
        if isinstance(sprite, dict) and sprite.get("sprite_id") is not None
    }
    position_cache: dict = {}
    layers = []
    for order, sprite in enumerate(sprites):
        if not isinstance(sprite, dict):
            continue
        if not _sprite_has_visible_state(sprite, sprite_by_id):
            continue
        image = _image_from_sprite(sprite, image_map)
        if image is None:
            continue
        state = _with_updated_follow_fields(sprite, _state_for(sprite, 0))
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
            "mesh": _extract_mesh_geometry(sprite, state),
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
            "mesh": layer.get("mesh") or {"valid": False, "degrade_reason": "mesh metadata missing"},
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
        name = _state_name_from_input(item, index)
        hotkeys.append({
            "state_index": index,
            "state_name": name,
            "key": _hotkey_label(props),
            "keycode": keycode,
            "ctrl": bool(props.get("ctrl_pressed")),
            "shift": bool(props.get("shift_pressed")),
            "alt": bool(props.get("alt_pressed")),
            "meta": bool(props.get("meta_pressed")),
        })
    return hotkeys


def _state_count_for_layers(layers: list[dict], input_array=None, settings: dict | None = None) -> int:
    counts = [len(layer.get("states") or []) for layer in layers]
    if isinstance(input_array, list):
        counts.append(len(input_array))
    settings_states = settings.get("states") if isinstance(settings, dict) else None
    if isinstance(settings_states, list):
        counts.append(len(settings_states))
    return max(counts, default=1)


def _state_name_from_input(item, index: int) -> str:
    if not isinstance(item, dict):
        return ""
    sources = [item]
    props = item.get("properties")
    if isinstance(props, dict):
        sources.append(props)
    hot_key = item.get("hot_key")
    if isinstance(hot_key, dict):
        sources.append(hot_key)
        hot_key_props = hot_key.get("properties")
        if isinstance(hot_key_props, dict):
            sources.append(hot_key_props)
    for source in sources:
        for key in ("state_name", "name", "label", "title", "button_text", "text"):
            value = source.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def _state_catalog(input_array, settings: dict, hotkeys: list[dict], state_count: int) -> list[dict]:
    settings_states = settings.get("states") if isinstance(settings, dict) else []
    if not isinstance(settings_states, list):
        settings_states = []
    catalog = []
    for index in range(max(1, state_count)):
        input_item = input_array[index] if isinstance(input_array, list) and index < len(input_array) else {}
        settings_state = settings_states[index] if index < len(settings_states) and isinstance(settings_states[index], dict) else {}
        name = _state_name_from_input(input_item, index)
        if not name:
            for key in ("state_name", "name", "label", "title", "button_text", "text"):
                value = settings_state.get(key)
                if value is not None and str(value).strip():
                    name = str(value).strip()
                    break
        hotkey = hotkeys[index] if index < len(hotkeys) else {}
        aliases = [
            str(index + 1),
            f"state {index + 1}",
            f"state_{index + 1}",
        ]
        if name:
            aliases.append(name)
        if hotkey.get("key"):
            aliases.append(str(hotkey["key"]))
        catalog.append({
            "state_index": index,
            "state_number": index + 1,
            "name": name or f"State {index + 1}",
            "hotkey": hotkey.get("key") or "",
            "aliases": sorted({alias for alias in aliases if alias}),
        })
    return catalog


def _emotion_mappings(state_catalog: list[dict]) -> dict:
    mappings = {"neutral": {"state_index": 0, "source": "default_state"}}
    explicit_keywords = {
        "happy": ("happy", "joy", "smile", "laugh", "开心", "高兴", "笑", "喜", "嬉", "楽", "笑顔"),
        "sad": ("sad", "cry", "tear", "down", "难过", "傷心", "伤心", "哭", "泪", "淚", "悲", "哀"),
        "angry": ("angry", "rage", "mad", "annoy", "生气", "生氣", "愤怒", "憤怒", "怒"),
        "surprised": ("surprised", "surprise", "shock", "惊", "驚", "吃惊", "吃驚", "惊讶", "驚訝", "びっくり"),
    }
    for emotion, keywords in explicit_keywords.items():
        for record in state_catalog:
            haystack = " ".join(str(value).lower() for value in [record.get("name"), *(record.get("aliases") or [])])
            if any(keyword.lower() in haystack for keyword in keywords):
                mappings[emotion] = {"state_index": int(record.get("state_index") or 0), "source": "state_label"}
                break

    fallback_order = ("happy", "sad", "angry", "surprised")
    if len(state_catalog) >= 5:
        for offset, emotion in enumerate(fallback_order, start=1):
            mappings.setdefault(emotion, {"state_index": offset, "source": "fallback_state_order"})
    return mappings


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
    physics_v2_base_keys = (
        "tip_point",
        "mesh_phys_x",
        "mesh_phys_y",
        "chain_softness",
        "chain_rot_min",
        "chain_rot_max",
        "drag_snap",
    )
    physics_v2_keys = tuple(
        f"{prefix}{key}"
        for prefix in ("", "mo_", "scream_")
        for key in physics_v2_base_keys
    )
    # `_json_safe_state()` emits every allowed key (incl. these v2 physics keys)
    # as None for absent fields, so `key in state` is true for every serialized
    # state. Require an actual non-None value instead, otherwise plain states with
    # no physics would falsely advertise capabilities.physics / physics_v2 and
    # suppress the "physics metadata absent" warning.
    return bool(state.get("physics")) or bool(state.get("wiggle")) or _has_motion_state(state) or any(state.get(key) is not None for key in physics_v2_keys)


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


def _has_mesh_metadata(layers: list[dict]) -> bool:
    mesh_keys = {
        "tip_point",
        "mesh_phys_x",
        "mesh_phys_y",
        "use_object_pos",
        "mo_tip_point",
        "mo_mesh_phys_x",
        "mo_mesh_phys_y",
        "mo_use_object_pos",
        "scream_tip_point",
        "scream_mesh_phys_x",
        "scream_mesh_phys_y",
        "scream_use_object_pos",
    }
    for layer in layers:
        if (layer.get("mesh") or {}).get("valid"):
            return True
        for state in layer.get("states") or []:
            # Serialized states carry every allowed key as None (see
            # _json_safe_state), so `key in state` is always true; require an
            # actual value so plain states without mesh fields report
            # mesh_metadata: false instead of unconditionally true.
            if any(state.get(key) is not None for key in mesh_keys):
                return True
            if (state.get("mesh") or {}).get("valid"):
                return True
    return False


def _has_mesh_runtime(layers: list[dict]) -> bool:
    for layer in layers:
        if (layer.get("mesh") or {}).get("valid") is True:
            return True
        for state in layer.get("states") or []:
            if (state.get("mesh") or {}).get("valid") is True:
                return True
    return False


def _unsupported_features(layers: list[dict], has_mesh_metadata: bool, has_mesh_runtime: bool) -> list[str]:
    unsupported = [
        "remix_editor_timeline",
        "godot_runtime_nodes",
    ]
    if has_mesh_metadata and not has_mesh_runtime:
        unsupported.append("mesh geometry missing; physics metadata preserved only")
    if not has_mesh_metadata:
        unsupported.append("mesh metadata absent")
    if not _has_physics_layers(layers):
        unsupported.append("physics metadata absent")
    return unsupported


def _metadata(remix_data: dict, remix_file: Path, package_dir: Path, warnings: list[str], layers: list[dict], bounds: tuple[int, int, int, int]) -> dict:
    sprites = remix_data.get("sprites_array") or []
    exported_layers = _export_layer_assets(package_dir, layers, bounds)
    _, _, width, height = bounds
    input_array = remix_data.get("input_array")
    settings = remix_data.get("settings_dict")
    has_mesh_metadata = _has_mesh_metadata(layers)
    has_mesh_runtime = _has_mesh_runtime(layers)
    has_physics = _has_physics_layers(layers)
    unsupported_features = _unsupported_features(layers, has_mesh_metadata, has_mesh_runtime)
    state_count = _state_count_for_layers(layers, input_array, settings if isinstance(settings, dict) else {})
    hotkeys = _normalized_hotkeys(input_array)
    state_catalog = _state_catalog(input_array, settings if isinstance(settings, dict) else {}, hotkeys, state_count)
    return {
        "adapter_version": 2,
        "runtime": "layered_canvas",
        "source_format": "pngtube_remix_pngremix",
        "source_file": remix_file.name,
        "warnings": warnings,
        "capabilities": {
            "speech_layers": True,
            "blink_layers": True,
            "hotkeys": False,
            "motion_layers": _has_motion_layers(layers),
            "physics": has_physics,
            "mesh": has_mesh_metadata,
            "mesh_metadata": has_mesh_metadata,
            "mesh_runtime": has_mesh_runtime,
        },
        "canvas": {"width": width, "height": height},
        "runtime_features": {
            "layer_motion": True,
            "sprite_sheet_animation": True,
            "layered_breathing": True,
            "mesh_deformation": has_mesh_runtime,
            "physics_v2": has_physics,
            "unsupported_features": unsupported_features,
        },
        "blink": {"enabled": True, "interval_min_ms": 2800, "interval_max_ms": 5200, "duration_ms": 140},
        "state_count": state_count,
        "state_catalog": state_catalog,
        "emotion_mappings": _emotion_mappings(state_catalog),
        "hotkeys": [],
        "state_hotkeys": hotkeys,
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
        "PNGTubeRemix project was imported through layered_canvas_v1. Speech, blink layers, states, asset actions, and layered physics are supported; source state hotkeys are preserved as metadata but are not bound at runtime, and mesh data is preserved as metadata for later runtime support."
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
        "message": "PNGTubeRemix model imported with layered adapter v1. Speech, blink layers, states, asset actions, and layered physics are enabled; source state hotkeys and mesh data are preserved as metadata only.",
        "warnings": warnings,
    }
