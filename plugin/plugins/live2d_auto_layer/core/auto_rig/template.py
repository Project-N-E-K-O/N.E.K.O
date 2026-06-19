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


def classify_rig_group(layer_name: str) -> str:
    """Classify a layer into a coarse runtime deformer group."""
    normalized = _normalize_name(layer_name)
    if normalized in {"headwear", "head_accessory", "face_accessory"}:
        return "head"
    if _has_token(normalized, {"hair", "tail"}):
        return "hair"
    if (
        normalized in {
            "face_skin",
            "face_detail",
            "ears",
            "neck",
            "eye_white",
            "iris",
            "eyelash",
            "eyebrow",
            "mouth",
            "nose",
        }
        or normalized.startswith("eye_")
        or normalized.startswith("eyebrow_")
    ):
        return "head"
    if (
        _has_token(normalized, {"body", "foot", "leg", "hand", "arm"})
        or normalized.endswith("wear")
        or normalized in {"topwear", "bottomwear", "legwear", "footwear", "handwear"}
    ):
        return "body"
    return "accessory"


def infer_bindings(layer_name: str) -> list[dict[str, object]]:
    """Infer coarse automatic deformation bindings from a normalized part name."""
    normalized = _normalize_name(layer_name)
    group = classify_rig_group(layer_name)
    bindings: list[dict[str, object]] = []

    if group == "hair":
        bindings.append({"parameter": "ParamHairSway", "type": "sway", "scale": 0.18})
    elif normalized == "mouth" or normalized.startswith("mouth_"):
        bindings.append({"parameter": "ParamMouthOpenY", "type": "scale_y", "scale": 0.18})
    elif group == "body":
        bindings.append({"parameter": "ParamBreath", "type": "scale_y", "scale": 0.02})
    else:
        bindings.append({"parameter": "ParamAngleX", "type": "group_follow", "scale": 1.0})

    if normalized in {"eye_white", "iris", "eyelash"} or normalized.startswith("eye_"):
        bindings.append({"parameter": "ParamEyeBlink", "type": "mask_y", "scale": 1.0})

    return bindings


def _normalize_name(layer_name: str) -> str:
    return layer_name.strip().lower().replace(" ", "_").replace("-", "_")


def _has_token(normalized: str, tokens: set[str]) -> bool:
    parts = set(part for part in normalized.split("_") if part)
    return bool(parts & tokens)
