"""Default parameter and binding templates for automatic rigs."""

from __future__ import annotations


DEFAULT_PARAMETERS = [
    {"id": "ParamAngleX", "name": "Head X", "min": -30, "max": 30, "default": 0},
    {"id": "ParamAngleY", "name": "Head Y", "min": -30, "max": 30, "default": 0},
    {"id": "ParamAngleZ", "name": "Head Z", "min": -30, "max": 30, "default": 0},
    {"id": "ParamBodyAngleX", "name": "Body X", "min": -10, "max": 10, "default": 0},
    {"id": "ParamBodyAngleY", "name": "Body Y", "min": -10, "max": 10, "default": 0},
    {"id": "ParamBreath", "name": "Breath", "min": 0, "max": 1, "default": 0},
    {"id": "ParamEyeBlink", "name": "Eye Blink", "min": 0, "max": 1, "default": 1},
    {"id": "ParamMouthOpenY", "name": "Mouth Open", "min": 0, "max": 1, "default": 0},
    {"id": "ParamHairSway", "name": "Hair Sway", "min": -1, "max": 1, "default": 0},
]


def infer_bindings(layer_name: str) -> list[dict[str, object]]:
    """Infer coarse automatic deformation bindings from a normalized part name."""
    normalized = layer_name.strip().lower().replace(" ", "_").replace("-", "_")
    bindings: list[dict[str, object]] = []

    if any(token in normalized for token in ["hair", "tail"]):
        bindings.extend([
            {"parameter": "ParamAngleX", "type": "offset_x", "scale": 0.12},
            {"parameter": "ParamAngleY", "type": "offset_y", "scale": 0.08},
            {"parameter": "ParamHairSway", "type": "sway", "scale": 0.18},
        ])
    elif normalized in {"face_skin", "face_detail", "ears", "neck", "eye_white", "iris", "eyelash", "eyebrow", "nose"} or "eye" in normalized:
        bindings.extend([
            {"parameter": "ParamAngleX", "type": "offset_x", "scale": 0.08},
            {"parameter": "ParamAngleY", "type": "offset_y", "scale": 0.08},
            {"parameter": "ParamAngleZ", "type": "rotate", "scale": 0.2},
        ])
    elif "mouth" in normalized:
        bindings.extend([
            {"parameter": "ParamAngleX", "type": "offset_x", "scale": 0.06},
            {"parameter": "ParamMouthOpenY", "type": "scale_y", "scale": 0.18},
        ])
    elif any(token in normalized for token in ["body", "wear", "foot", "leg"]):
        bindings.extend([
            {"parameter": "ParamBodyAngleX", "type": "offset_x", "scale": 0.06},
            {"parameter": "ParamBodyAngleY", "type": "offset_y", "scale": 0.04},
            {"parameter": "ParamBreath", "type": "scale_y", "scale": 0.02},
        ])
    else:
        bindings.append({"parameter": "ParamAngleX", "type": "offset_x", "scale": 0.03})

    if "eye" in normalized or normalized in {"iris", "eyelash"}:
        bindings.append({"parameter": "ParamEyeBlink", "type": "mask_y", "scale": 1.0})

    return bindings
