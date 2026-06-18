"""
语义分割模块 v2.0
=================
- GroundedSAM: GroundingDINO + SAM 文字提示语义分割 (主力)
- SkinDetector: HSV 肤色检测 (辅助)
- ColorSegmenter: K-Means 聚类 (兜底)
"""

from typing import Optional

import numpy as np
from PIL import Image


# ---- 肤色检测 (辅助) ----

class SkinDetector:
    """
    动漫角色肤色检测器。
    使用 HSV + LAB 双空间阈值，针对二次元肤色优化。
    """

    # 动漫肤色 HSV 范围 (偏粉/偏白)
    SKIN_HSV_LOW = np.array([0, 10, 60], dtype=np.uint8)
    SKIN_HSV_HIGH = np.array([25, 120, 255], dtype=np.uint8)

    # 动漫肤色 LAB 范围 (辅助判定)
    SKIN_LAB_LOW = np.array([0, 130, 120], dtype=np.uint8)
    SKIN_LAB_HIGH = np.array([255, 190, 170], dtype=np.uint8)

    @classmethod
    def detect(cls, image: Image.Image) -> np.ndarray:
        """
        检测肤色区域。

        Returns:
            bool mask (H, W), True=肤色
        """
        rgb = image.convert("RGB")
        arr = np.array(rgb)

        # HSV 检测
        import cv2
        hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
        hsv_mask = cv2.inRange(hsv, cls.SKIN_HSV_LOW, cls.SKIN_HSV_HIGH)

        # LAB 检测
        lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
        lab_mask = cv2.inRange(lab, cls.SKIN_LAB_LOW, cls.SKIN_LAB_HIGH)

        # 组合
        combined = hsv_mask & lab_mask

        # 形态学清洁
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel, iterations=2)
        combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, kernel, iterations=1)

        return combined > 0


# ---- 主要分割 API ----

def segment_image(
    image: Image.Image,
    method: str = "anime_face",
    prompts: Optional[list[str]] = None,
    gsam_instance=None,
    parts: Optional[list[str]] = None,
    gpt_api_key: str = "",
) -> dict[str, Image.Image]:
    """
    一站式图像分割。

    Args:
        image: RGBA/RGB 前景图
        method: "anime_face" (推荐) | "grounded_sam" | "color"
        prompts: 部位文字提示 (仅 grounded_sam 模式)
        gsam_instance: GroundedSAM 实例
        parts: 要提取的部位列表 (仅 anime_face 模式)
        gpt_api_key: Rightcode API key (用于 GPT-5.5 视觉精确定位五宫)

    Returns:
        {layer_name: RGBA_layer, ...}
    """
    if method == "anime_face":
        return _segment_with_anime_face(image, parts=parts, gpt_api_key=gpt_api_key)
    elif method == "grounded_sam":
        return _segment_with_gsam(image, prompts, gsam_instance)
    elif method == "color":
        return _segment_with_color(image)
    else:
        raise ValueError(f"未知分割方法: {method}")


def _segment_with_anime_face(
    image: Image.Image,
    parts: Optional[list[str]] = None,
    gpt_api_key: str = "",
) -> dict[str, Image.Image]:
    """使用动漫面部检测 + SAM 混合分割，可选 GPT 地标"""
    from .anime_face import AnimePartSegmenter

    # 如果提供了 API key，创建 GPT 检测器
    gpt_detector = None
    if gpt_api_key and gpt_api_key.strip():
        from ..vision_landmarks import GPTLandmarkDetector
        gpt_detector = GPTLandmarkDetector(api_key=gpt_api_key.strip())

    segmenter = AnimePartSegmenter(gpt_detector=gpt_detector)
    return segmenter.segment(image, parts=parts)


def _segment_with_gsam(
    image: Image.Image,
    prompts: Optional[list[str]] = None,
    gsam_instance=None,
) -> dict[str, Image.Image]:
    """使用 GroundedSAM 分割"""

    if gsam_instance is None:
        from .grounded_sam import get_gsam
        gsam_instance = get_gsam()

    if not gsam_instance.is_loaded:
        print("[Segment] GroundedSAM 模型不可用，回退到颜色分割")
        return _segment_with_color(image)

    # 确保 RGB
    if image.mode == "RGBA":
        rgb_img = image.convert("RGB")
    else:
        rgb_img = image.convert("RGB")

    layers = gsam_instance.segment(rgb_img, prompts=prompts)

    if not layers:
        print("[Segment] GroundedSAM 未检测到部位，尝试降低阈值...")
        # 降低阈值重试
        old_box = gsam_instance.box_threshold
        old_text = gsam_instance.text_threshold
        gsam_instance.box_threshold = 0.15
        gsam_instance.text_threshold = 0.1
        layers = gsam_instance.segment(rgb_img, prompts=prompts)
        gsam_instance.box_threshold = old_box
        gsam_instance.text_threshold = old_text

    if not layers:
        print("[Segment] GroundedSAM 仍未检测到，回退到颜色分割")
        return _segment_with_color(image)

    return layers


def _segment_with_color(image: Image.Image) -> dict[str, Image.Image]:
    """颜色聚类分割 (兜底方案)"""
    segmenter = ColorSegmenter()
    return segmenter.segment(image)


# ---- K-Means 颜色分割 (兜底) ----

class ColorSegmenter:
    """保留旧版颜色聚类作为兜底，但增加了肤色识别辅助"""

    def __init__(self, n_clusters: int = 10):
        self.n_clusters = n_clusters

    def segment(self, image: Image.Image) -> dict[str, Image.Image]:
        """K-Means 聚类分割 (仅作兜底)"""
        from ..config import SPATIAL_WEIGHT, MIN_CLUSTER_AREA_RATIO

        w, h = image.size
        total_pixels = w * h

        # Alpha mask
        if image.mode == "RGBA":
            alpha = np.array(image.getchannel("A"))
            rgb = np.array(image.convert("RGB"))
        else:
            alpha = np.ones((h, w), dtype=np.uint8) * 255
            rgb = np.array(image.convert("RGB"))

        foreground_mask = alpha > 10

        if np.sum(foreground_mask) < 100:
            return {}

        # LAB + 空间坐标
        from skimage.color import rgb2lab
        lab = rgb2lab(rgb / 255.0)

        xs, ys = np.meshgrid(np.arange(w), np.arange(h))
        xs_norm = xs.astype(np.float32) / max(w, 1)
        ys_norm = ys.astype(np.float32) / max(h, 1)

        features = np.stack(
            [
                lab[:, :, 0],
                lab[:, :, 1],
                lab[:, :, 2],
                xs_norm * SPATIAL_WEIGHT,
                ys_norm * SPATIAL_WEIGHT,
            ],
            axis=-1,
        ).astype(np.float32, copy=False)

        fg_features = features[foreground_mask]
        n_fg = fg_features.shape[0]
        actual_k = min(self.n_clusters, max(2, n_fg // 100))

        max_fit_pixels = 80_000
        if n_fg > max_fit_pixels:
            from sklearn.cluster import MiniBatchKMeans

            rng = np.random.default_rng(42)
            sample_idx = rng.choice(n_fg, size=max_fit_pixels, replace=False)
            fit_features = fg_features[sample_idx]
            print(
                f"[ColorSegmenter] K-Means sampling: fitting {max_fit_pixels}/{n_fg} foreground pixels"
            )
            kmeans = MiniBatchKMeans(
                n_clusters=actual_k,
                random_state=42,
                n_init=3,
                max_iter=100,
                batch_size=8192,
            )
            kmeans.fit(fit_features)
            labels = kmeans.predict(fg_features)
        else:
            from sklearn.cluster import KMeans

            print(f"[ColorSegmenter] K-Means fitting {n_fg} foreground pixels")
            kmeans = KMeans(
                n_clusters=actual_k,
                random_state=42,
                n_init=3,
                max_iter=100,
                algorithm="elkan",
            )
            labels = kmeans.fit_predict(fg_features)

        cluster_labels = np.full((h, w), -1, dtype=np.int32)
        cluster_labels[foreground_mask] = labels

        # 使用肤色检测辅助命名
        skin_mask = SkinDetector.detect(image)
        skin_clusters = set()

        layers = {}
        for label_id in range(actual_k):
            mask = (cluster_labels == label_id).astype(np.uint8) * 255
            mask = self._clean_mask(mask, total_pixels)
            if mask is None:
                continue

            layer_rgba = self._mask_to_layer(rgb, mask)
            if layer_rgba is None:
                continue

            # 检查这个 cluster 是否主要为肤色
            cluster_skin_overlap = np.sum((mask > 128) & skin_mask)
            cluster_area = np.sum(mask > 128)
            if cluster_area > 0 and cluster_skin_overlap / cluster_area > 0.5:
                skin_clusters.add(label_id)

            name = self._auto_name_with_skin(layer_rgba, label_id, layers, skin_clusters, label_id)
            layers[name] = layer_rgba

        layers = dict(
            sorted(layers.items(), key=lambda kv: self._layer_area(kv[1]), reverse=True)
        )

        return layers

    def _clean_mask(self, mask, total_pixels):
        import cv2
        binary = (mask > 128).astype(np.uint8)
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        if num_labels <= 1:
            return None

        areas = stats[1:, cv2.CC_STAT_AREA]
        if len(areas) == 0:
            return None

        min_area = total_pixels * 0.005
        max_idx = np.argmax(areas)
        if areas[max_idx] < min_area:
            return None

        keep_label = max_idx + 1
        clean = (labels == keep_label).astype(np.uint8) * 255
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        clean = cv2.morphologyEx(clean, cv2.MORPH_CLOSE, kernel, iterations=1)
        clean = cv2.morphologyEx(clean, cv2.MORPH_OPEN, kernel, iterations=1)
        return clean

    def _mask_to_layer(self, rgb, mask):
        alpha = mask.astype(np.uint8)
        rgba = np.dstack([rgb, alpha])
        return Image.fromarray(rgba, mode="RGBA")

    def _layer_area(self, layer):
        if layer.mode == "RGBA":
            return int(np.sum(np.array(layer.getchannel("A")) > 10))
        return layer.width * layer.height

    def _auto_name_with_skin(self, layer, label_id, existing, skin_clusters, cluster_id):
        rgba = np.array(layer)
        alpha = rgba[:, :, 3]
        fg = alpha > 10
        if np.sum(fg) == 0:
            return f"Empty_{label_id + 1:02d}"

        avg_color = rgba[fg][:, :3].mean(axis=0)
        r, g, b = avg_color

        # 优先用肤色信息
        if cluster_id in skin_clusters:
            base = "Face_Skin"
        elif max(r, g, b) - min(r, g, b) > 50:
            base = f"Part_Color_{label_id + 1:02d}"
        elif (r + g + b) / 3 < 100:
            base = f"Hair_Dark_{label_id + 1:02d}"
        elif (r + g + b) / 3 > 220:
            base = f"Part_Light_{label_id + 1:02d}"
        else:
            base = f"Part_{label_id + 1:02d}"

        if base not in existing:
            return base
        i = 2
        while f"{base}_v{i}" in existing:
            i += 1
        return f"{base}_v{i}"
