"""Name mapping for turning extracted layers into Live2D-oriented parts."""

DEFAULT_LAYER_ORDER = [
    "Hair_Back",
    "Tail",
    "Wings",
    "Body",
    "Legwear",
    "Bottomwear",
    "Footwear",
    "Topwear",
    "Neck",
    "Neckwear",
    "Face_Skin",
    "Face_Detail",
    "Ears",
    "Eye_White",
    "Iris",
    "Eyelash",
    "Eyebrow",
    "Mouth",
    "Nose",
    "Eye_Left",
    "Eye_Right",
    "Eyebrow_Left",
    "Eyebrow_Right",
    "Hair_Front",
    "Headwear",
    "Eyewear",
    "Earwear",
    "Handwear",
    "Accessory",
]

_EXACT_ALIASES = {
    "back_hair": "Hair_Back",
    "hair_back": "Hair_Back",
    "backhair": "Hair_Back",
    "front_hair": "Hair_Front",
    "hair_front": "Hair_Front",
    "fronthair": "Hair_Front",
    "body": "Body",
    "head": "Face_Skin",
    "face": "Face_Detail",
    "skin": "Face_Skin",
    "mouth": "Mouth",
    "nose": "Nose",
    "ears": "Ears",
    "ear": "Ears",
    "eyebrow": "Eyebrow",
    "eyelash": "Eyelash",
    "eyewhite": "Eye_White",
    "eye_white": "Eye_White",
    "irides": "Iris",
    "iris": "Iris",
    "topwear": "Topwear",
    "bottomwear": "Bottomwear",
    "legwear": "Legwear",
    "footwear": "Footwear",
    "handwear": "Handwear",
    "headwear": "Headwear",
    "neckwear": "Neckwear",
    "earwear": "Earwear",
    "eyewear": "Eyewear",
    "neck": "Neck",
    "tail": "Tail",
    "wings": "Wings",
    "objects": "Accessory",
    "object": "Accessory",
    "left_eyebrow": "Eyebrow_Left",
    "eyebrow_left": "Eyebrow_Left",
    "right_eyebrow": "Eyebrow_Right",
    "eyebrow_right": "Eyebrow_Right",
    "left_eye": "Eye_Left",
    "eye_left": "Eye_Left",
    "right_eye": "Eye_Right",
    "eye_right": "Eye_Right",
}


def classify_layer(name: str) -> str:
    """Map a raw segmentation layer name to a stable Live2D part name."""
    normalized = name.strip().lower().replace(" ", "_").replace("-", "_")
    if normalized in _EXACT_ALIASES:
        return _EXACT_ALIASES[normalized]
    for key, part_name in _EXACT_ALIASES.items():
        if key in normalized:
            return part_name
    return name
