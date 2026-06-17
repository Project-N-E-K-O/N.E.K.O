"""
图层导出模块
- 单个 PNG 图层导出
- ZIP 批量打包
- 预览合成图生成
"""

import os
import time
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

from .config import OUTPUT_DIR, EXPORT_ZIP_NAME
from .image_utils import (
    create_zip_export,
    create_checkerboard_bg,
    ensure_rgba,
)


class LayerExporter:
    """图层导出器"""

    def __init__(self, output_dir: str | Path = OUTPUT_DIR):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_pngs(
        self,
        layers: dict[str, Image.Image],
        session_name: Optional[str] = None,
    ) -> Path:
        """
        导出所有图层为独立 PNG 文件。

        Args:
            layers: {layer_name: RGBA_image, ...}
            session_name: 会话名 (用于子目录)

        Returns:
            导出目录路径
        """
        if session_name is None:
            session_name = f"export_{int(time.time())}"

        export_dir = self.output_dir / session_name
        export_dir.mkdir(parents=True, exist_ok=True)

        saved_paths = []
        for name, img in layers.items():
            img = ensure_rgba(img)
            safe_name = self._sanitize_filename(name)
            filepath = export_dir / f"{safe_name}.png"
            img.save(filepath, format="PNG")
            saved_paths.append(filepath)
            print(f"[Export] 保存: {filepath}")

        print(f"[Export] 共导出 {len(saved_paths)} 个图层到: {export_dir}")
        return export_dir

    def export_zip(
        self,
        layers: dict[str, Image.Image],
        zip_name: Optional[str] = None,
    ) -> Path:
        """
        导出所有图层为 ZIP 压缩包。

        Args:
            layers: {layer_name: RGBA_image, ...}
            zip_name: ZIP 文件名

        Returns:
            ZIP 文件路径
        """
        if zip_name is None:
            zip_name = f"live2d_layers_{int(time.time())}.zip"

        zip_path = self.output_dir / zip_name
        return create_zip_export(layers, zip_path)

    def export_preview(
        self,
        layers: dict[str, Image.Image],
        include_checkerboard: bool = True,
    ) -> Image.Image:
        """
        生成图层叠加预览图。

        Args:
            layers: {layer_name: RGBA_image, ...}
            include_checkerboard: 是否用棋盘格显示透明区域

        Returns:
            合成预览图 (RGB)
        """
        if not layers:
            return Image.new("RGB", (512, 512), (50, 50, 50))

        # 取最大图层尺寸
        max_w = max(img.width for img in layers.values())
        max_h = max(img.height for img in layers.values())

        if include_checkerboard:
            base = create_checkerboard_bg((max_w, max_h))
        else:
            base = Image.new("RGB", (max_w, max_h), (50, 50, 50))

        for name, img in layers.items():
            layer_rgba = ensure_rgba(img)
            if layer_rgba.size != (max_w, max_h):
                layer_rgba = layer_rgba.resize((max_w, max_h), Image.LANCZOS)

            # 叠加
            base_rgba = base.convert("RGBA")
            composite = Image.alpha_composite(base_rgba, layer_rgba)
            base = composite.convert("RGB")

        return base

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """清理文件名中的非法字符"""
        return "".join(
            c if c.isalnum() or c in "_- " else "_"
            for c in name
        ).rstrip()


def export_layers_zip(layers: dict[str, Image.Image]) -> str:
    """
    便捷函数: 导出图层为 ZIP 并返回路径字符串。
    用于 Gradio 回调。
    """
    exporter = LayerExporter()
    zip_path = exporter.export_zip(layers)
    return str(zip_path)


def export_preview_image(
    layers: dict[str, Image.Image],
    max_dim: int = 1024,
) -> Image.Image:
    """
    便捷函数: 生成预览图 (限制尺寸)。
    """
    exporter = LayerExporter()
    preview = exporter.export_preview(layers)

    # 缩放预览
    w, h = preview.size
    longest = max(w, h)
    if longest > max_dim:
        ratio = max_dim / longest
        preview = preview.resize(
            (int(w * ratio), int(h * ratio)), Image.LANCZOS
        )

    return preview
