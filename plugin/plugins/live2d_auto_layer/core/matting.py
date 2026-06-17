"""
Alpha 抠图模块
- 背景移除 (rembg)
- Alpha 通道精细化
- 边缘羽化/去噪
"""

import numpy as np
from PIL import Image, ImageFilter

from .config import ALPHA_FEATHER_RADIUS, ALPHA_ERODE_ITERATIONS


class BackgroundRemover:
    """基于 rembg 的背景移除器"""

    def __init__(self):
        self._session = None

    def _get_session(self):
        """懒加载 rembg session"""
        if self._session is None:
            from rembg import new_session

            # u2net 适合大部分场景，isnet-anime 更适合二次元
            try:
                self._session = new_session("isnet-anime")
                print("[BackgroundRemover] 模型: isnet-anime (动漫优化)")
            except Exception:
                self._session = new_session("u2net")
                print("[BackgroundRemover] 模型: u2net (通用)")
        return self._session

    def remove(self, image: Image.Image) -> Image.Image:
        """
        移除背景，返回 RGBA 前景图。

        Args:
            image: 输入 PIL Image

        Returns:
            RGBA 前景图，背景区域 Alpha=0
        """
        from rembg import remove

        # 确保 RGB 输入 (rembg 需要)
        if image.mode == "RGBA":
            rgb = image.convert("RGB")
        else:
            rgb = image.convert("RGB")

        result = remove(rgb, session=self._get_session())

        # 确保 RGBA 输出
        if result.mode != "RGBA":
            result = result.convert("RGBA")

        print(f"[BackgroundRemover] 背景移除完成, 尺寸: {result.size}")
        return result

    def get_alpha_mask(self, image: Image.Image) -> Image.Image:
        """
        获取 Alpha mask (单通道)。
        返回: L 模式 Image，255=前景, 0=背景
        """
        rgba = self.remove(image)
        return rgba.getchannel("A")


class AlphaRefiner:
    """Alpha 通道精细化处理器"""

    def __init__(
        self,
        feather_radius: int = ALPHA_FEATHER_RADIUS,
        erode_iterations: int = ALPHA_ERODE_ITERATIONS,
    ):
        self.feather_radius = feather_radius
        self.erode_iterations = erode_iterations

    def refine(
        self, image: Image.Image, alpha_mask: Image.Image | None = None
    ) -> Image.Image:
        """
        精细处理 Alpha 通道:
        1. 轻微腐蚀 (去除黑边/白边)
        2. 边缘高斯模糊 (自然过渡)
        3. 阈值清理 (去除半透明噪声)

        Args:
            image: RGBA 原图
            alpha_mask: 可选的独立 Alpha mask (L 模式)

        Returns:
            处理后的 RGBA 图像
        """
        # 获取 Alpha 通道
        if alpha_mask is not None:
            alpha = alpha_mask.copy()
            # 将 Alpha mask 应用到 RGB
            rgb = image.convert("RGB")
        else:
            if image.mode != "RGBA":
                image = image.convert("RGBA")
            rgb = image.convert("RGB")
            alpha = image.getchannel("A")

        # Step 1: 轻微腐蚀去黑边
        if self.erode_iterations > 0:
            alpha_np = np.array(alpha)
            from scipy.ndimage import binary_erosion

            # 只腐蚀完全不透明的边缘
            kernel = np.ones((3, 3), dtype=bool)
            opaque = alpha_np > 200
            eroded = binary_erosion(opaque, kernel, iterations=self.erode_iterations)
            # 将腐蚀掉的部分设为半透明而非完全透明
            transition = opaque.astype(np.uint8) * 255
            alpha_np = (alpha_np * 0.7 + eroded.astype(np.uint8) * 255 * 0.3).astype(np.uint8)
            alpha = Image.fromarray(alpha_np, mode="L")

        # Step 2: 边缘羽化
        if self.feather_radius > 0:
            # 创建边缘区域的 mask
            alpha_np = np.array(alpha).astype(np.float32) / 255.0
            from scipy.ndimage import gaussian_filter

            # 对 Alpha 做轻度高斯模糊，只影响边缘过渡区域
            blurred = gaussian_filter(alpha_np, sigma=self.feather_radius * 0.5)
            # 混合：纯前景(>0.95)和纯背景(<0.05)保持不变
            edge_zone = (alpha_np > 0.02) & (alpha_np < 0.98)
            alpha_np[edge_zone] = blurred[edge_zone]
            alpha_np = np.clip(alpha_np * 255, 0, 255).astype(np.uint8)
            alpha = Image.fromarray(alpha_np, mode="L")

        # Step 3: 阈值清理 — 移除极低 Alpha 噪声
        alpha_np = np.array(alpha)
        alpha_np[alpha_np < 5] = 0
        alpha = Image.fromarray(alpha_np, mode="L")

        # 组装 RGBA
        result = Image.merge("RGBA", (*rgb.split(), alpha))
        print(f"[AlphaRefiner] Alpha 精细化完成")
        return result


def extract_alpha_from_rgba(image: Image.Image) -> np.ndarray:
    """从 RGBA 图像提取 Alpha 通道作为 numpy array (0-255)"""
    if image.mode == "RGBA":
        return np.array(image.getchannel("A"))
    elif image.mode == "L":
        return np.array(image)
    else:
        return np.full((image.height, image.width), 255, dtype=np.uint8)


def apply_alpha(image: Image.Image, alpha: np.ndarray) -> Image.Image:
    """将 Alpha 通道应用到 RGB 图像，返回 RGBA"""
    rgb = image.convert("RGB")
    alpha_img = Image.fromarray(alpha.astype(np.uint8), mode="L")
    return Image.merge("RGBA", (*rgb.split(), alpha_img))
