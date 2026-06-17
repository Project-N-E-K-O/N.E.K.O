"""
图像预处理模块
- 分辨率标准化
- 对比度/锐度增强
- 可选：超分辨率 (ESRGAN 占位)
"""

from PIL import Image

from .config import MAX_IMAGE_SIZE, ENHANCE_CONTRAST, CONTRAST_ALPHA, SHARPEN_RADIUS
from .image_utils import (
    ensure_rgba,
    resize_to_standard,
    enhance_contrast,
    sharpen,
)


class Preprocessor:
    """图像预处理器"""

    def __init__(
        self,
        max_size: int = MAX_IMAGE_SIZE,
        enhance: bool = ENHANCE_CONTRAST,
        contrast_alpha: float = CONTRAST_ALPHA,
        sharpen_radius: float = SHARPEN_RADIUS,
    ):
        self.max_size = max_size
        self.enhance = enhance
        self.contrast_alpha = contrast_alpha
        self.sharpen_radius = sharpen_radius

    def preprocess(self, image: Image.Image) -> Image.Image:
        """
        完整预处理流水线:
        1. RGBA 标准化
        2. 分辨率缩放
        3. 对比度增强
        4. 轻度锐化
        """
        # Step 1: 统一 RGBA
        image = ensure_rgba(image)

        # Step 2: 标准化尺寸
        original_size = image.size
        image = resize_to_standard(image, self.max_size)
        if image.size != original_size:
            print(f"[Preprocess] 尺寸调整: {original_size} → {image.size}")

        # Step 3: 对比度增强
        if self.enhance:
            # 只对 RGB 通道做增强，保留 Alpha
            rgb = image.convert("RGB")
            alpha = image.getchannel("A")
            rgb = enhance_contrast(rgb, self.contrast_alpha)
            image = Image.merge("RGBA", (*rgb.split(), alpha))

        # Step 4: 轻度锐化
        if self.sharpen_radius > 0:
            rgb = image.convert("RGB")
            alpha = image.getchannel("A")
            rgb = sharpen(rgb, self.sharpen_radius)
            image = Image.merge("RGBA", (*rgb.split(), alpha))

        print(f"[Preprocess] 预处理完成, 输出尺寸: {image.size}")
        return image


# ---- 超分辨率增强 (V1.0 预留接口) ----

class SuperResolution:
    """
    ESRGAN / Real-ESRGAN 超分辨率增强。
    MVP 阶段为占位实现，后续可替换为真实模型。
    """

    def __init__(self, model_type: str = "anime"):
        """
        model_type: "anime" | "general"
        MVP: 不加载实际模型，仅做 Lanczos 放大
        """
        self.model_type = model_type
        self._model = None
        print(f"[SuperResolution] 模型类型: {model_type} (MVP: Lanczos fallback)")

    def load_model(self):
        """懒加载模型权重 (占位)"""
        if self._model is not None:
            return
        # TODO: 加载 Real-ESRGAN 动漫模型
        # from realesrgan import RealESRGANer
        # self._model = RealESRGANer(...)
        print("[SuperResolution] 模型已就绪 (Lanczos fallback)")

    def enhance(self, image: Image.Image, scale: int = 2) -> Image.Image:
        """
        超分辨率放大。
        MVP: 使用 Lanczos 插值作为 fallback
        """
        self.load_model()

        if self._model is not None:
            # TODO: 调用真实 ESRGAN 推理
            pass

        # Fallback: Lanczos 插值放大
        w, h = image.size
        new_size = (w * scale, h * scale)
        result = image.resize(new_size, Image.LANCZOS)
        print(f"[SuperResolution] Lanczos 放大: {image.size} → {result.size}")
        return result
