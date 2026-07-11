from __future__ import annotations

from pathlib import Path
from typing import Iterable, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
from scipy.spatial import ConvexHull, QhullError


def imread(path: str | Path) -> np.ndarray:
    image = Image.open(path).convert("RGB")
    return np.asarray(image)[..., ::-1].copy()


def imwrite(path: str | Path, img: np.ndarray) -> None:
    array = np.asarray(img)
    if array.ndim == 3 and array.shape[2] == 3:
        array = array[..., ::-1]
    Image.fromarray(array).save(path)


def resize(img: np.ndarray, size: Tuple[int, int]) -> np.ndarray:
    dst_w, dst_h = int(size[0]), int(size[1])
    src_h, src_w = img.shape[:2]
    if dst_w <= 0 or dst_h <= 0:
        raise ValueError("resize dimensions must be positive")

    x = (np.arange(dst_w, dtype=np.float64) + 0.5) * (src_w / dst_w) - 0.5
    y = (np.arange(dst_h, dtype=np.float64) + 0.5) * (src_h / dst_h) - 0.5

    x0_unclipped = np.floor(x).astype(np.int64)
    y0_unclipped = np.floor(y).astype(np.int64)
    x_alpha = x - x0_unclipped
    y_alpha = y - y0_unclipped

    x0 = np.clip(x0_unclipped, 0, src_w - 1)
    x1 = np.clip(x0_unclipped + 1, 0, src_w - 1)
    y0 = np.clip(y0_unclipped, 0, src_h - 1)
    y1 = np.clip(y0_unclipped + 1, 0, src_h - 1)

    top_left = img[y0[:, None], x0[None, :]]
    top_right = img[y0[:, None], x1[None, :]]
    bottom_left = img[y1[:, None], x0[None, :]]
    bottom_right = img[y1[:, None], x1[None, :]]

    x_alpha = x_alpha.reshape(1, dst_w, *([1] * (img.ndim - 2)))
    y_alpha = y_alpha.reshape(dst_h, 1, *([1] * (img.ndim - 2)))

    top = top_left * (1.0 - x_alpha) + top_right * x_alpha
    bottom = bottom_left * (1.0 - x_alpha) + bottom_right * x_alpha
    resized = top * (1.0 - y_alpha) + bottom * y_alpha

    if np.issubdtype(img.dtype, np.integer):
        info = np.iinfo(img.dtype)
        resized = np.clip(np.rint(resized), info.min, info.max)
    return resized.astype(img.dtype, copy=False)


def gray_to_bgr(img: np.ndarray) -> np.ndarray:
    gray = np.squeeze(img)
    return np.stack((gray, gray, gray), axis=-1)


def rgb_to_bgr(img: np.ndarray) -> np.ndarray:
    return img[..., ::-1].copy()


def bitwise_not(img: np.ndarray) -> np.ndarray:
    return (255 - img).astype(img.dtype, copy=False)


def bitwise_and_with_mask(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
    expanded_mask = mask > 0
    if img.ndim == 3:
        expanded_mask = expanded_mask[..., np.newaxis]
    return np.where(expanded_mask, img, 0).astype(img.dtype, copy=False)


def add_uint8(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    return np.clip(left.astype(np.int32) + right.astype(np.int32), 0, 255).astype(
        np.uint8
    )


def rotate_180(img: np.ndarray) -> np.ndarray:
    return np.rot90(img, 2).copy()


def copy_make_border(
    img: np.ndarray,
    top: int,
    bottom: int,
    left: int,
    right: int,
    value: int | Tuple[int, ...] = 0,
) -> np.ndarray:
    h, w = img.shape[:2]
    out_shape = (h + top + bottom, w + left + right, *img.shape[2:])
    padded = np.empty(out_shape, dtype=img.dtype)
    padded[...] = value
    padded[top : top + h, left : left + w, ...] = img
    return padded


def perspective_transform_matrix(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    matrix_rows = []
    target = []
    for (x, y), (u, v) in zip(src, dst):
        matrix_rows.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y])
        matrix_rows.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y])
        target.extend([u, v])
    solution = np.linalg.solve(
        np.asarray(matrix_rows, dtype=np.float64),
        np.asarray(target, dtype=np.float64),
    )
    return np.append(solution, 1.0).reshape(3, 3)


def invert_matrix(matrix: np.ndarray) -> np.ndarray:
    return np.linalg.inv(matrix)


def warp_perspective(img: np.ndarray, matrix: np.ndarray, size: Tuple[int, int]) -> np.ndarray:
    width, height = int(size[0]), int(size[1])
    inverse = np.linalg.inv(matrix)
    inverse = inverse / inverse[2, 2]
    src_start = inverse @ np.array([0.0, 0.0, 1.0])
    src_end = inverse @ np.array([float(width), 0.0, 1.0])
    src_start = src_start[:2] / src_start[2]
    src_end = src_end[:2] / src_end[2]
    src_delta = src_end - src_start
    angle = abs(np.degrees(np.arctan2(src_delta[1], src_delta[0])))
    resample = Image.Resampling.BICUBIC if angle >= 8.0 else Image.Resampling.BILINEAR
    coeffs = (
        inverse[0, 0],
        inverse[0, 1],
        inverse[0, 2],
        inverse[1, 0],
        inverse[1, 1],
        inverse[1, 2],
        inverse[2, 0],
        inverse[2, 1],
    )
    image = Image.fromarray(img)
    warped = image.transform(
        (width, height),
        Image.Transform.PERSPECTIVE,
        coeffs,
        resample,
    )
    return np.asarray(warped).astype(img.dtype, copy=False)


def dilate(mask: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    structure = np.asarray(kernel).astype(bool)
    origin = tuple(-1 if size % 2 == 0 else 0 for size in structure.shape)
    return ndimage.binary_dilation(
        mask.astype(bool),
        structure=structure,
        origin=origin,
    ).astype(mask.dtype)


def connected_contours(mask: np.ndarray) -> list[np.ndarray]:
    labels, count = ndimage.label(mask.astype(bool), structure=np.ones((3, 3)))
    contours: list[np.ndarray] = []
    for label in range(1, count + 1):
        ys, xs = np.nonzero(labels == label)
        if len(xs) == 0:
            continue
        points = np.column_stack((xs, ys)).astype(np.float32)
        points = convex_hull_points(points)
        contours.append(points.reshape((-1, 1, 2)))
    return contours


def convex_hull_points(points: np.ndarray) -> np.ndarray:
    points = np.unique(np.asarray(points, dtype=np.float32).reshape((-1, 2)), axis=0)
    if len(points) <= 2 or np.linalg.matrix_rank(points - points[0]) < 2:
        return points
    try:
        hull = ConvexHull(points)
    except QhullError:
        return points
    return points[hull.vertices]


def min_area_box(points: np.ndarray) -> tuple[np.ndarray, float]:
    hull_points = convex_hull_points(points)
    if len(hull_points) == 0:
        box = np.zeros((4, 2), dtype=np.float32)
        return box, 0.0

    if len(hull_points) == 1:
        box = np.repeat(hull_points, 4, axis=0).astype(np.float32)
        return box, 0.0

    if len(hull_points) == 2 or np.linalg.matrix_rank(hull_points - hull_points[0]) < 2:
        min_x, min_y = hull_points.min(axis=0)
        max_x, max_y = hull_points.max(axis=0)
        box = np.array(
            [[min_x, min_y], [max_x, min_y], [max_x, max_y], [min_x, max_y]],
            dtype=np.float32,
        )
        return box, float(min(max_x - min_x, max_y - min_y))

    edges = np.roll(hull_points, -1, axis=0) - hull_points
    angles = np.arctan2(edges[:, 1], edges[:, 0])

    best_area = float("inf")
    best_box = None
    best_side = 0.0
    for angle in angles:
        c, s = np.cos(-angle), np.sin(-angle)
        rotation = np.array([[c, -s], [s, c]], dtype=np.float64)
        rotated = hull_points @ rotation.T

        min_x, max_x = rotated[:, 0].min(), rotated[:, 0].max()
        min_y, max_y = rotated[:, 1].min(), rotated[:, 1].max()
        width = max_x - min_x
        height = max_y - min_y
        area = width * height
        if area < best_area:
            corners = np.array(
                [[min_x, min_y], [max_x, min_y], [max_x, max_y], [min_x, max_y]]
            )
            best_area = area
            best_side = float(min(width, height))
            best_box = corners @ rotation

    return np.asarray(best_box, dtype=np.float32), best_side


def fill_polygon(mask_shape: Tuple[int, int], points: np.ndarray) -> np.ndarray:
    image = Image.new("L", (int(mask_shape[1]), int(mask_shape[0])), 0)
    point_list = [tuple(map(float, point)) for point in np.asarray(points).reshape((-1, 2))]
    if point_list:
        ImageDraw.Draw(image).polygon(point_list, fill=1)
    return np.asarray(image, dtype=np.uint8)


def masked_mean(values: np.ndarray, mask: np.ndarray) -> float:
    selected = values[np.asarray(mask) > 0]
    if selected.size == 0:
        return 0.0
    return float(np.mean(selected))


def polygon_area(points: np.ndarray) -> float:
    pts = np.asarray(points, dtype=np.float64).reshape((-1, 2))
    if len(pts) < 3:
        return 0.0
    x = pts[:, 0]
    y = pts[:, 1]
    return float(0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))))


def polygon_perimeter(points: np.ndarray) -> float:
    pts = np.asarray(points, dtype=np.float64).reshape((-1, 2))
    if len(pts) < 2:
        return 0.0
    edges = np.roll(pts, -1, axis=0) - pts
    return float(np.sum(np.sqrt(np.sum(edges**2, axis=1))))


def draw_polyline(
    img: np.ndarray,
    points: Iterable[Iterable[float]],
    color: Tuple[int, int, int],
    thickness: int = 1,
) -> np.ndarray:
    image = Image.fromarray(img)
    point_list = [tuple(map(float, point)) for point in points]
    if point_list:
        point_list.append(point_list[0])
        ImageDraw.Draw(image).line(point_list, fill=tuple(color), width=thickness)
    return np.asarray(image)


def draw_text(
    img: np.ndarray,
    text: str,
    point: Tuple[int, int],
    color: Tuple[int, int, int],
) -> np.ndarray:
    image = Image.fromarray(img)
    draw = ImageDraw.Draw(image)
    draw.text(point, text, fill=tuple(color), font=ImageFont.load_default())
    return np.asarray(image)
