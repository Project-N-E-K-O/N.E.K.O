"""Mesh generation for automatic layer rigs."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True, slots=True)
class MeshData:
    bbox: tuple[int, int, int, int]
    vertices: list[list[float]]
    uvs: list[list[float]]
    triangles: list[list[int]]


def build_grid_mesh(
    image: Image.Image,
    *,
    subdivisions: int = 2,
    padding: int = 4,
    alpha_threshold: int = 10,
) -> MeshData:
    """Build a stable rectangular grid mesh around the visible alpha bounds."""
    if subdivisions < 1:
        raise ValueError("subdivisions must be >= 1")
    alpha_threshold = _clamp_alpha_threshold(alpha_threshold)

    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    bbox = _alpha_bbox(rgba, padding=padding, alpha_threshold=alpha_threshold)
    left, top, width, height = bbox
    right = left + width
    bottom = top + height

    cols = subdivisions + 1
    rows = subdivisions + 1
    vertices: list[list[float]] = []
    uvs: list[list[float]] = []
    image_width = max(rgba.width, 1)
    image_height = max(rgba.height, 1)

    for row in range(rows):
        y = _lerp(top, bottom, row / subdivisions)
        for col in range(cols):
            x = _lerp(left, right, col / subdivisions)
            vertices.append([round(x, 3), round(y, 3)])
            uvs.append([round(x / image_width, 6), round(y / image_height, 6)])

    triangles: list[list[int]] = []
    for row in range(subdivisions):
        for col in range(subdivisions):
            top_left = row * cols + col
            top_right = top_left + 1
            bottom_left = top_left + cols
            bottom_right = bottom_left + 1
            triangles.append([top_left, bottom_left, top_right])
            triangles.append([top_right, bottom_left, bottom_right])

    return MeshData(
        bbox=bbox,
        vertices=vertices,
        uvs=uvs,
        triangles=triangles,
    )


def _alpha_bbox(image: Image.Image, *, padding: int, alpha_threshold: int) -> tuple[int, int, int, int]:
    alpha = image.getchannel("A")
    if alpha_threshold > 0:
        alpha = alpha.point(lambda value: 255 if value > alpha_threshold else 0)
    box = alpha.getbbox()
    if box is None:
        return (0, 0, image.width, image.height)
    left, top, right, bottom = box
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(image.width, right + padding)
    bottom = min(image.height, bottom + padding)
    return (left, top, max(1, right - left), max(1, bottom - top))


def _lerp(start: int, end: int, amount: float) -> float:
    return start + (end - start) * amount


def _clamp_alpha_threshold(value: int) -> int:
    return max(0, min(255, int(value)))
