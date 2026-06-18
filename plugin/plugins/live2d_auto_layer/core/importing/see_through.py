"""Import see-through worker outputs without running the model in-process."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from ..assembly import RawLayer, RawLayerSet

_IMAGE_EXTS = {".png", ".webp", ".tif", ".tiff"}
_IGNORED_STEMS = {
    "checkerboard",
    "composite",
    "input",
    "preview",
    "reconstruction",
    "src_head",
    "src_img",
}


def import_layer_source(path: str | Path, *, source: str = "see_through") -> RawLayerSet:
    """Import a see-through output directory or layered PSD into RawLayerSet."""
    source_path = Path(path).expanduser().resolve()
    if source_path.is_dir():
        return _import_folder(source_path, source=source)
    if source_path.is_file() and source_path.suffix.lower() == ".psd":
        return _import_psd(source_path, source=source)
    raise FileNotFoundError(f"Layer source must be a directory or PSD file: {source_path}")


def _import_folder(path: Path, *, source: str) -> RawLayerSet:
    metadata = _read_metadata(path)
    candidates = _find_layer_images(path)
    layers: list[RawLayer] = []
    warnings: list[str] = []

    for image_path in candidates:
        try:
            image = Image.open(image_path).convert("RGBA")
        except Exception as exc:
            warnings.append(f"Skipped unreadable layer image: {image_path.name} ({exc})")
            continue
        if image.getchannel("A").getbbox() is None:
            warnings.append(f"Skipped empty alpha layer: {image_path.name}")
            continue
        raw_layer = RawLayer(
            name=_layer_name(image_path),
            image=image,
            source=source,
            metadata={"path": str(image_path)},
        )
        if raw_layer.area == 0:
            warnings.append(f"Skipped invisible layer: {image_path.name}")
            continue
        layers.append(
            raw_layer
        )

    if not layers:
        raise ValueError(f"No readable non-empty layer images found under: {path}")

    return RawLayerSet(
        layers=layers,
        source=source,
        source_path=str(path),
        canvas_size=_largest_canvas(layers),
        warnings=warnings,
        metadata=metadata,
    )


def _import_psd(path: Path, *, source: str) -> RawLayerSet:
    try:
        from psd_tools import PSDImage
    except Exception as exc:
        raise RuntimeError("PSD import requires optional dependency: psd-tools") from exc

    psd = PSDImage.open(path)
    layers: list[RawLayer] = []
    warnings: list[str] = []

    for index, layer in enumerate(psd.descendants()):
        if getattr(layer, "is_group", lambda: False)():
            continue
        if not getattr(layer, "is_visible", lambda: True)():
            continue
        try:
            image = _psd_layer_to_canvas(layer, canvas_size=(int(psd.width), int(psd.height)))
        except Exception as exc:
            warnings.append(f"Skipped unreadable PSD layer {index}: {exc}")
            continue
        if image.getchannel("A").getbbox() is None:
            continue
        name = str(getattr(layer, "name", "") or f"Layer_{index + 1:02d}")
        raw_layer = RawLayer(
            name=name,
            image=image,
            source=source,
            metadata={"psd_layer_index": index},
        )
        if raw_layer.area == 0:
            continue
        layers.append(raw_layer)

    if not layers:
        raise ValueError(f"No visible non-empty PSD layers found: {path}")

    return RawLayerSet(
        layers=layers,
        source=source,
        source_path=str(path),
        canvas_size=(int(psd.width), int(psd.height)),
        warnings=warnings,
        metadata={"format": "psd"},
    )


def _read_metadata(path: Path) -> dict[str, object]:
    for name in ("metadata.json", "manifest.json", "info.json"):
        metadata_path = path / name
        if not metadata_path.is_file():
            continue
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}
    child_dirs = [child for child in sorted(path.iterdir()) if child.is_dir()]
    if len(child_dirs) == 1:
        return _read_metadata(child_dirs[0])
    return {}


def _psd_layer_to_canvas(layer, *, canvas_size: tuple[int, int]) -> Image.Image:
    image = layer.composite().convert("RGBA")
    if image.size == canvas_size:
        return image

    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    bbox = getattr(layer, "bbox", None)
    if bbox is None:
        canvas.alpha_composite(image, (0, 0))
        return canvas

    left = int(getattr(bbox, "x1", 0))
    top = int(getattr(bbox, "y1", 0))
    if not hasattr(bbox, "x1") and isinstance(bbox, tuple) and len(bbox) >= 2:
        left = int(bbox[0])
        top = int(bbox[1])
    canvas.alpha_composite(image, (left, top))
    return canvas


def _find_layer_images(path: Path) -> list[Path]:
    search_roots = _candidate_image_roots(path)
    seen: set[Path] = set()
    images: list[Path] = []
    for root in search_roots:
        if not root.is_dir():
            continue
        for image_path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if image_path in seen or not image_path.is_file():
                continue
            if image_path.suffix.lower() not in _IMAGE_EXTS:
                continue
            if _is_auxiliary_image(image_path):
                continue
            seen.add(image_path)
            images.append(image_path)
    return images


def _candidate_image_roots(path: Path) -> list[Path]:
    roots: list[Path] = []
    for root in (path / "layers", path):
        if root.is_dir():
            roots.append(root)
    for child in sorted(path.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir() or child.name == "optimized":
            continue
        if (child / "layers").is_dir():
            roots.append(child / "layers")
        if _contains_layer_images(child):
            roots.append(child)
    return roots


def _contains_layer_images(path: Path) -> bool:
    return any(
        item.is_file() and item.suffix.lower() in _IMAGE_EXTS and not _is_auxiliary_image(item)
        for item in path.iterdir()
    )


def _is_auxiliary_image(path: Path) -> bool:
    stem = path.stem.strip().lower()
    return stem in _IGNORED_STEMS or stem.endswith("_depth")


def _layer_name(path: Path) -> str:
    return path.stem.strip() or "Layer"


def _largest_canvas(layers: list[RawLayer]) -> tuple[int, int]:
    return (
        max(layer.rgba.width for layer in layers),
        max(layer.rgba.height for layer in layers),
    )
