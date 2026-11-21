"""Prepare wardrobe sprite sheets and load existing Spine texture overrides."""
from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_SAVED_SPRITESHEET_BYTES = 32 * 1024 * 1024
MAX_SPRITESHEET_REGIONS = 3
# Percentages of the square canvas, including gutters and outer margins.
SPRITESHEET_CELLS = {
    1: ((8, 8, 92, 92),),
    2: ((5, 8, 45, 92), (55, 8, 95, 92)),
    3: ((5, 5, 45, 45), (55, 5, 95, 45), (5, 55, 45, 95)),
}
Annotation = tuple[float, float, float, float]


class WardrobeError(ValueError):
    """Stable error code suitable for the editor. Never carries prompt data."""


def _png(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def _read_image(data: bytes, *, max_edge: int = 4096) -> Image.Image:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise WardrobeError("invalid_image")
    try:
        with Image.open(io.BytesIO(data), formats=["PNG", "JPEG", "WEBP"]) as image:
            if (max(image.size) > max_edge or image.width * image.height > 32_000_000
                    or getattr(image, "n_frames", 1) != 1):
                raise WardrobeError("invalid_image")
            return ImageOps.exif_transpose(image).convert("RGBA")
    except (OSError, Image.DecompressionBombError, SyntaxError):
        raise WardrobeError("invalid_image") from None


def read_library_spritesheet(data: bytes, *, preview: bool = False) -> bytes:
    """Read a static library image and return PNG bytes for storage or preview."""
    if not data or len(data) > MAX_SAVED_SPRITESHEET_BYTES:
        raise WardrobeError("invalid_spritesheet_file")
    try:
        with Image.open(io.BytesIO(data), formats=["PNG", "JPEG", "WEBP"]) as image:
            if max(image.size) > 2048 or getattr(image, "n_frames", 1) != 1:
                raise WardrobeError("invalid_spritesheet_file")
            image.load()
            if image.format == "PNG":
                if not preview:
                    return data
                normalized = image.convert("RGBA")
            else:
                normalized = ImageOps.exif_transpose(image).convert("RGBA")
                normalized.info.clear()
            if preview:
                normalized.thumbnail((240, 240), Image.Resampling.LANCZOS)
            return _png(normalized)
    except (OSError, Image.DecompressionBombError, SyntaxError, ValueError) as exc:
        raise WardrobeError("invalid_spritesheet_file") from exc


def parse_annotations(raw: str) -> tuple[Annotation, ...]:
    if not raw:
        return ()
    if len(raw) > 2048:
        raise WardrobeError("invalid_annotations")
    try:
        items = json.loads(raw)
    except (ValueError, RecursionError):
        raise WardrobeError("invalid_annotations") from None
    if not isinstance(items, list):
        raise WardrobeError("invalid_annotations")
    if not 1 <= len(items) <= MAX_SPRITESHEET_REGIONS:
        raise WardrobeError("invalid_spritesheet_regions")
    for box in items:
        if (not isinstance(box, list) or len(box) != 4
                or not all(type(value) in (int, float) and 0 <= value <= 1 for value in box)
                or box[2] - box[0] < 0.005 or box[3] - box[1] < 0.005):
            raise WardrobeError("invalid_annotations")
    return tuple(tuple(box) for box in items)


def spritesheet_crop_aspect_limit(protocol: str, model: str) -> int | None:
    if protocol != "dashscope":
        return None
    if model.startswith(("qwen-image-2.1", "qwen-image-3.0")):
        return 10
    if model.startswith("wan2.7-image"):
        return 8
    return None


def prepare_spritesheet_crops(
    data: bytes, regions: tuple[Annotation, ...], aspect_limit: int | None = None,
) -> tuple[tuple[bytes, ...], int, dict[int, bytes]]:
    if not 1 <= len(regions) <= MAX_SPRITESHEET_REGIONS:
        raise WardrobeError("invalid_spritesheet_regions")
    image = _read_image(data, max_edge=8192)
    crops = []
    padded_count = 0
    cel_references = {}
    for index, (x1, y1, x2, y2) in enumerate(regions):
        left = min(int(x1 * image.width + 0.5), image.width - 1)
        top = min(int(y1 * image.height + 0.5), image.height - 1)
        right, bottom = int(x2 * image.width + 0.5), int(y2 * image.height + 0.5)
        crop = image.crop((left, top, max(left + 1, right), max(top + 1, bottom)))
        sharpness = _edge_sharpness(crop)
        needs_padding = aspect_limit is not None and max(crop.size) > aspect_limit * min(crop.size)
        crop.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        if aspect_limit is not None and (needs_padding or max(crop.size) > aspect_limit * min(crop.size)):
            width, height = crop.size
            minimum = max(min(width, height) + 1, (max(width, height) + aspect_limit - 1) // aspect_limit)
            padded = Image.new("RGBA", (max(width, minimum), max(height, minimum)), "white")
            padded.paste(crop, ((padded.width - width) // 2, (padded.height - height) // 2))
            crop = padded
            padded_count += 1
        crop.info.clear()
        original = _png(crop)
        crops.append(original)
        if sharpness < .55:
            cel_references[index] = _cel_reference(crop, aspect_limit) if sharpness < .42 else original
    return tuple(crops), padded_count, cel_references


def _cel_reference(crop: Image.Image, aspect_limit: int | None = None) -> bytes:
    reference = crop.copy()
    reference.thumbnail((192, 192), Image.Resampling.LANCZOS)
    alpha = reference.getchannel("A")
    rgb = Image.alpha_composite(Image.new("RGBA", reference.size, "white"), reference).convert("RGB")
    filter_size = min(7, 2 * (min(reference.size) // 16) + 1)
    smooth = rgb.filter(ImageFilter.MedianFilter(filter_size)) if filter_size >= 3 else rgb
    # Use the opaque part for the palette so transparent padding cannot dominate the colors.
    opaque = np.asarray(smooth)[np.asarray(alpha) >= 224]
    palette_source = Image.fromarray(opaque.reshape(1, -1, 3)) if len(opaque) else smooth
    palette = palette_source.quantize(colors=3, dither=Image.Dither.NONE)
    flat = smooth.quantize(palette=palette, dither=Image.Dither.NONE).convert("RGB")
    if filter_size >= 3:
        flat = flat.filter(ImageFilter.ModeFilter(min(5, filter_size)))
    if alpha.getextrema()[0] < 128 and min(reference.size) >= 3:
        mask = alpha.point(lambda value: 255 if value >= 128 else 0)
        outline = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(3)))
        flat.paste((25, 24, 24), mask=outline)
    flat.putalpha(alpha)
    if aspect_limit is not None and max(flat.size) > aspect_limit * min(flat.size):
        width, height = flat.size
        minimum = (max(width, height) + aspect_limit - 1) // aspect_limit
        padded = Image.new("RGBA", (max(width, minimum), max(height, minimum)), "white")
        padded.paste(flat, ((padded.width - width) // 2, (padded.height - height) // 2))
        flat = padded
    return _png(flat)


def _edge_sharpness(image: Image.Image) -> float:
    sample = image.copy()
    sample.thumbnail((512, 512), Image.Resampling.LANCZOS)
    matte = Image.new("RGBA", sample.size, (128, 128, 128))
    luminance = np.asarray(Image.alpha_composite(matte, sample).convert("L"), dtype=np.float32)
    ratios = []
    for rows in (luminance, luminance.T):
        if len(rows) <= 4:
            continue
        span = np.abs(rows[4:] - rows[:-4])
        strong = span >= max(12, np.percentile(span, 90))
        if not strong.any():
            continue
        steps = np.abs(np.diff(rows, axis=0))
        peak = np.maximum.reduce([steps[offset:offset + len(span)] for offset in range(4)])
        ratios.append(peak[strong] / span[strong])
    # At a sharp edge most of the contrast spans one pixel; blur spreads it across several.
    return float(np.percentile(np.concatenate(ratios), 80)) if ratios else 1.0


def _spritesheet_boxes(alpha: Image.Image, *, vertical: bool, count: int) -> list[tuple[int, int, int, int]]:
    pixels = np.asarray(alpha)
    occupied = np.any(pixels, axis=1 if vertical else 0)
    positions = np.flatnonzero(occupied)
    gap = max(2, len(occupied) // 160)
    bands = np.split(positions, np.flatnonzero(np.diff(positions) > gap) + 1)
    if len(bands) != count or any(not band.size for band in bands):
        return []
    width, height = alpha.size
    return [(0, int(band[0]), width, int(band[-1]) + 1) if vertical
            else (int(band[0]), 0, int(band[-1]) + 1, height) for band in bands]


def _repack_spritesheet(image: Image.Image, alpha: Image.Image, count: int) -> tuple[Image.Image, bool]:
    width, height = image.size
    bounds = alpha.getbbox()
    if bounds[0] == 0 or bounds[1] == 0 or bounds[2] == width or bounds[3] == height:
        return image, False
    if count == 1:
        return image, True
    cells = [
        (x1 * width // 100, y1 * height // 100, x2 * width // 100, y2 * height // 100)
        for x1, y1, x2, y2 in SPRITESHEET_CELLS[count]
    ]
    occupancy = [sum(alpha.crop(cell).histogram()[1:]) for cell in cells]
    if all(occupancy) and sum(occupancy) == width * height - alpha.histogram()[0]:
        return image, True

    # Cut only through transparent gaps. If sprites touch or have ambiguous
    # spacing, let the user keep the preview and decide what to crop.
    boxes = _spritesheet_boxes(alpha, vertical=False, count=count) or _spritesheet_boxes(
        alpha, vertical=True, count=count,
    )
    if not boxes and count == 3:
        rows = _spritesheet_boxes(alpha, vertical=True, count=2)
        if rows:
            top = _spritesheet_boxes(alpha.crop(rows[0]), vertical=False, count=2)
            bottom = _spritesheet_boxes(alpha.crop(rows[1]), vertical=False, count=1)
            if top and bottom:
                boxes = [(left, y1 + row[1], right, y2 + row[1])
                         for row, sections in zip(rows, (top, bottom)) for left, y1, right, y2 in sections]
    if not boxes or any(alpha.crop(box).getextrema()[1] < 32 for box in boxes):
        return image, False

    result = Image.new("RGBA", image.size)
    for box, (left, top, right, bottom) in zip(boxes, cells):
        sprite = image.crop(box)
        sprite = sprite.crop(sprite.getchannel("A").getbbox())
        sprite.thumbnail((right - left, bottom - top), Image.Resampling.LANCZOS)
        result.paste(sprite, (left + (right - left - sprite.width) // 2, top + (bottom - top - sprite.height) // 2))
    return result, True


def prepare_spritesheet_result(data: bytes, count: int) -> tuple[bytes, bool]:
    try:
        image = _read_image(data, max_edge=2048)
    except WardrobeError:
        raise WardrobeError("invalid_spritesheet_result") from None
    # Keep native alpha, including the anti-aliased edges of the generated sprites.
    # A flat backdrop can be removed by the shared wardrobe transparency pass.
    alpha = image.getchannel("A")
    if alpha.getextrema()[0] > 4:
        try:
            image = _generated_rgba(image)
        except WardrobeError:
            raise WardrobeError("invalid_spritesheet_result") from None
        alpha = image.getchannel("A")
    alpha = alpha.point([0] * 5 + list(range(5, 256)))
    transparent_pixels = alpha.histogram()[0]
    if transparent_pixels < image.width * image.height * .05 or alpha.getextrema()[1] < 32:
        raise WardrobeError("invalid_spritesheet_result")
    # Edge contact is a layout issue. Keep the preview so other sprites can
    # still be selected, and let the layout check report the crowded edge.
    image.putalpha(alpha)
    sheet, separated = _repack_spritesheet(image, alpha, count)
    return _png(sheet), separated


def _generated_rgba(image: Image.Image) -> Image.Image:
    source_alpha = image.getchannel("A")
    has_alpha = source_alpha.getextrema()[0] < 255
    if has_alpha:
        # A nearly opaque RGBA image can still have a solid chroma backdrop.
        # Preserve native transparency when the canvas border is transparent.
        alpha_pixels = np.asarray(source_alpha)
        border = np.concatenate((alpha_pixels[0], alpha_pixels[-1], alpha_pixels[:, 0], alpha_pixels[:, -1]))
        if np.mean(border >= 224) < 0.95:
            return image
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    corners = np.concatenate([
        rgb[:32, :32].reshape(-1, 3), rgb[:32, -32:].reshape(-1, 3),
        rgb[-32:, :32].reshape(-1, 3), rgb[-32:, -32:].reshape(-1, 3),
    ])
    background = np.median(corners, axis=0)
    strength = min(background[0], background[2]) - background[1]
    if has_alpha:
        if strength < 90:
            return image
        matching = (np.max(np.abs(rgb - background), axis=2) <= 32) & (alpha_pixels >= 224)
        border = np.concatenate((matching[0], matching[-1], matching[:, 0], matching[:, -1]))
        if np.mean(border) < 0.95:
            return image
    if strength >= 90:
        chroma = np.minimum(rgb[:, :, 0], rgb[:, :, 2]) - rgb[:, :, 1]
        alpha = np.clip((0.84 * strength - chroma) / (0.70 * strength), 0, 1)
    else:
        alpha = _flat_background_alpha(rgb, background)
    edge = (alpha > 0) & (alpha < 1)
    rgb[edge] = (rgb[edge] - background * (1 - alpha[edge, None])) / alpha[edge, None]
    if has_alpha:
        alpha *= alpha_pixels / 255
    pixels = np.dstack((np.clip(rgb, 0, 255), alpha * 255)).round().astype(np.uint8)
    pixels[pixels[:, :, 3] == 0, :3] = 0
    return Image.fromarray(pixels)


def _flat_background_alpha(rgb: np.ndarray, background: np.ndarray) -> np.ndarray:
    distance = np.max(np.abs(rgb - background), axis=2)
    border = np.concatenate((distance[0, :], distance[-1, :], distance[:, 0], distance[:, -1]))
    if np.mean(border <= 12) < 0.95:
        raise WardrobeError("part_background")
    # Only remove background-colored pixels connected to the outside. This
    # keeps the same color inside enclosed features such as outlined eyes.
    close = Image.fromarray(np.where(distance <= 24, 255, 0).astype(np.uint8))
    connected = ImageOps.expand(close, border=1, fill=255)
    ImageDraw.floodfill(connected, (0, 0), 128)
    removable = np.asarray(connected)[1:-1, 1:-1] == 128
    alpha = np.ones(distance.shape, dtype=np.float32)
    alpha[removable] = np.clip((distance[removable] - 6) / 16, 0, 1)
    return alpha


def _silhouette_metrics(foreground: np.ndarray, expected: np.ndarray, allowed: np.ndarray) -> tuple[float, float]:
    count = np.count_nonzero(foreground)
    if not count:
        return 0.0, 1.0
    overlap = np.count_nonzero(foreground & expected) / np.count_nonzero(foreground | expected)
    overflow = np.count_nonzero(foreground & ~allowed) / count
    return overlap, overflow


@dataclass
class WardrobePart:
    id: str
    slot: str
    attachment: str
    original: Image.Image
    uv: Image.Image
    offsets: tuple[int, int, int, int] | None

    @property
    def storage_path(self) -> str:
        return f"GeneratedParts/{self.id}.png"

    def _check(self, alpha: Image.Image, original: Image.Image, uv: Image.Image) -> None:
        overlap, overflow = _silhouette_metrics(np.asarray(alpha) > 127, np.asarray(original) > 127, np.asarray(uv) > 127)
        if overlap < 0.76 or overflow > 0.015:
            raise WardrobeError("part_outside_mesh")

    def validate_result(self, data: bytes) -> bytes:
        image = _read_image(data)
        if image.size != self.original.size:
            raise WardrobeError("part_outside_mesh")
        self._check(image.getchannel("A"), self.original.getchannel("A"), self.uv)
        return _png(image)


class EmoteWardrobe:
    """Read back textures saved by earlier wardrobe versions."""

    def __init__(self, archive: bytes, package: dict, parts: list[dict]):
        self.archive = archive
        self.skeleton_path = package["model"]["skeleton"]["path"]
        self.atlas_path = package["model"]["atlas"]["path"]
        pages = package["model"]["atlas"]["pages"]
        if len(pages) != 1:
            raise WardrobeError("wardrobe_unavailable")
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            self.atlas = source.read(self.atlas_path).decode("utf-8-sig")
            model = json.loads(source.read(self.skeleton_path))
            with Image.open(io.BytesIO(source.read(pages[0]["path"]))) as texture:
                original = texture.convert("RGBA")
        regions = self._regions(self.atlas, pages[0]["name"])
        skin = next(item for item in model["skins"] if item["name"] == "default")
        self.parts: dict[str, WardrobePart] = {}
        for choice in parts:
            slot = choice["slot"]
            attachment = choice.get("attachment", slot)
            part_id = self.part_id(choice)
            if not re.fullmatch(r"[A-Za-z0-9_]+--[A-Za-z0-9_]+", part_id) or part_id in self.parts:
                raise WardrobeError("wardrobe_unavailable")
            definition = skin["attachments"][slot][attachment]
            if definition.get("type") != "mesh" or definition.get("sequence"):
                raise WardrobeError("wardrobe_unavailable")
            region = regions[definition.get("path", attachment)]
            x, y, width, height = region["bounds"]
            offsets = region.get("offsets")
            self.parts[part_id] = WardrobePart(
                part_id, slot, attachment,
                original.crop((x, y, x + width, y + height)),
                self._mesh_mask(definition, width, height, offsets), offsets,
            )

    @staticmethod
    def part_id(choice: dict) -> str:
        return f"{choice['slot']}--{choice.get('textureId', choice.get('attachment', choice['slot']))}"

    @staticmethod
    def _regions(atlas: str, page: str) -> dict:
        regions = {}
        name = None
        for line in atlas.splitlines():
            line = line.strip()
            if not line:
                continue
            if ":" not in line:
                name = line if line != page else None
                if name:
                    regions[name] = {}
            elif name:
                field, value = line.split(":", 1)
                if field in {"bounds", "offsets"}:
                    regions[name][field] = tuple(map(int, value.split(",")))
                elif field == "rotate" and value.strip() not in {"false", "0"}:
                    raise WardrobeError("wardrobe_unavailable")
        return regions

    @staticmethod
    def _mesh_mask(definition: dict, width: int, height: int, offsets) -> Image.Image:
        left, bottom, original_width, original_height = offsets or (0, 0, width, height)
        top = original_height - height - bottom
        scale = 2
        uv = definition["uvs"]
        vertices = [((u * original_width - left) * scale, (v * original_height - top) * scale)
                    for u, v in zip(uv[::2], uv[1::2])]
        mask = Image.new("L", (width * scale, height * scale))
        draw = ImageDraw.Draw(mask)
        triangles = definition["triangles"]
        for index in range(0, len(triangles), 3):
            draw.polygon([vertices[i] for i in triangles[index:index + 3]], fill=255)
        return mask.resize((width, height), Image.Resampling.BOX)

    def build_archive(self, parts: dict[str, bytes]) -> bytes:
        """Use new atlas regions while keeping attachment keys, UVs and weights."""
        with zipfile.ZipFile(io.BytesIO(self.archive)) as source:
            files = {name: source.read(name) for name in source.namelist()}
        model = json.loads(files[self.skeleton_path])
        skin = next(item for item in model["skins"] if item["name"] == "default")
        atlas = self.atlas.rstrip()
        for part_id, data in sorted(parts.items()):
            part = self.parts[part_id]
            width, height = part.original.size
            region = f"generated-{part_id}"
            page = region + ".png"
            atlas += f"\n\n{page}\nsize:{width},{height}\nfilter:Linear,Linear\n{region}\nbounds:0,0,{width},{height}"
            if part.offsets:
                atlas += "\noffsets:" + ",".join(map(str, part.offsets))
            skin["attachments"][part.slot][part.attachment]["path"] = region
            files[page] = data
        files[self.atlas_path] = (atlas + "\n").encode()
        files[self.skeleton_path] = json.dumps(model, ensure_ascii=False, separators=(",", ":")).encode()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as target:
            for name, data in files.items():
                target.writestr(name, data)
        return stream.getvalue()
