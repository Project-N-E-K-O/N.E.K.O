"""
图像工具函数 — 加载、转换、合成等基础操作
"""

import io
import zipfile
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter


def ensure_rgba(image: Image.Image) -> Image.Image:
    """统一转为 RGBA 模式"""
    if image.mode == "RGBA":
        return image
    if image.mode == "RGB":
        return image.convert("RGBA")
    if image.mode == "L":
        return image.convert("RGBA")
    if image.mode == "P":
        return image.convert("RGBA")
    # 其他模式先转 RGB 再转 RGBA
    return image.convert("RGB").convert("RGBA")


def resize_to_standard(
    image: Image.Image, max_size: int = 2048
) -> Image.Image:
    """
    标准化图像尺寸，保持宽高比。
    仅当最长边超过 max_size 时才缩放。
    """
    w, h = image.size
    longest = max(w, h)
    if longest <= max_size:
        return image.copy()

    ratio = max_size / longest
    new_w = int(w * ratio)
    new_h = int(h * ratio)
    return image.resize((new_w, new_h), Image.LANCZOS)


def enhance_contrast(image: Image.Image, alpha: float = 1.15) -> Image.Image:
    """轻度增强对比度"""
    enhancer = ImageEnhance.Contrast(image)
    return enhancer.enhance(alpha)


def sharpen(image: Image.Image, radius: float = 0.5) -> Image.Image:
    """轻度锐化"""
    if radius <= 0:
        return image
    return image.filter(ImageFilter.UnsharpMask(radius=radius, percent=80, threshold=2))


def pil_to_numpy(image: Image.Image) -> np.ndarray:
    """PIL Image → numpy array (RGBA)"""
    return np.array(image)


def numpy_to_pil(array: np.ndarray) -> Image.Image:
    """numpy array → PIL Image"""
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    if array.shape[-1] == 4:
        return Image.fromarray(array, mode="RGBA")
    elif array.shape[-1] == 3:
        return Image.fromarray(array, mode="RGB")
    elif len(array.shape) == 2:
        return Image.fromarray(array, mode="L")
    return Image.fromarray(array)


def create_composite_preview(
    layers: dict[str, Image.Image],
    background_color: tuple[int, int, int, int] = (50, 50, 50, 255),
) -> Image.Image:
    """
    将多个图层叠加到棋盘格/纯色背景上，生成预览图。
    返回一张合成后的 RGBA 预览图（带背景色）。
    """
    if not layers:
        return Image.new("RGBA", (512, 512), background_color)

    # 取第一层的尺寸为基准
    first_layer = next(iter(layers.values()))
    base = Image.new("RGBA", first_layer.size, background_color)

    for layer_name, layer_img in layers.items():
        layer_rgba = ensure_rgba(layer_img)
        if layer_rgba.size != base.size:
            layer_rgba = layer_rgba.resize(base.size, Image.LANCZOS)
        base = Image.alpha_composite(base, layer_rgba)

    return base


def create_checkerboard_bg(
    size: tuple[int, int], tile_size: int = 16
) -> Image.Image:
    """创建棋盘格背景，用于预览透明区域"""
    w, h = size
    bg = Image.new("L", size, 255)
    tiles_x = (w + tile_size - 1) // tile_size
    tiles_y = (h + tile_size - 1) // tile_size

    for y in range(tiles_y):
        for x in range(tiles_x):
            if (x + y) % 2 == 0:
                x0 = x * tile_size
                y0 = y * tile_size
                x1 = min(x0 + tile_size, w)
                y1 = min(y0 + tile_size, h)
                tile = Image.new("L", (x1 - x0, y1 - y0), 220)
                bg.paste(tile, (x0, y0))

    return Image.merge("RGB", (bg, bg, bg))


def create_zip_export(
    layers: dict[str, Image.Image],
    output_path: str | Path,
) -> Path:
    """
    将图层字典打包为 ZIP 文件。
    layers: {"LayerName": PIL.Image(RGBA), ...}
    返回 ZIP 文件路径。
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for layer_name, layer_img in layers.items():
            # 确保 RGBA
            img = ensure_rgba(layer_img)
            # 写入 PNG 到 ZIP
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            zf.writestr(f"{layer_name}.png", buf.read())

    return output_path


def estimate_layer_complexity(mask: np.ndarray) -> float:
    """
    估算图层复杂度 (基于边缘长度与面积的比值)。
    用于自动分类：复杂度高的可能是头发。
    """
    import cv2

    binary = (mask > 128).astype(np.uint8) * 255
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        return 0.0

    total_perimeter = sum(
        cv2.arcLength(cnt, True) for cnt in contours
    )
    total_area = np.sum(binary > 0)

    if total_area == 0:
        return 0.0

    # 周长/面积比 → 复杂度指标
    return total_perimeter / (total_area ** 0.5)

