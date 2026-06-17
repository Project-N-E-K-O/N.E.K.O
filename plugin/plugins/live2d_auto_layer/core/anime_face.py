"""
动漫面部检测 + SAM 混合分割模块
================================
策略:
1. lbpcascade_animeface 检测面部位置
2. SAM + 面部 bbox → 精确面部 mask
3. 面部比例 → 估算五官位置 → SAM point-prompt 精确分割
4. 减去面部区域 → 头发 + 身体 → 颜色/位置启发式分割
"""

from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image

from .config import MODEL_DIR

# ---- 动漫面部检测器 ----

class AnimeFaceDetector:
    """基于 lbpcascade 的动漫面部检测器"""

    CASCADE_PATH = MODEL_DIR / "lbpcascade_animeface.xml"

    def __init__(self):
        if not self.CASCADE_PATH.exists():
            raise FileNotFoundError(
                f"动漫面部检测模型未找到: {self.CASCADE_PATH}\n"
                f"请从 https://github.com/nagadomi/lbpcascade_animeface 下载"
            )
        self._cascade = cv2.CascadeClassifier(str(self.CASCADE_PATH))

    def detect(self, image: np.ndarray) -> list[tuple[int, int, int, int]]:
        """
        检测图像中的动漫面部。

        Args:
            image: numpy RGB 图像 (H, W, 3)

        Returns:
            [(x, y, w, h), ...] 检测到的面部列表，按面积从大到小
        """
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        # 均衡化提高检测率
        gray = cv2.equalizeHist(gray)

        faces = self._cascade.detectMultiScale(
            gray,
            scaleFactor=1.05,
            minNeighbors=5,
            minSize=(50, 50),
            flags=cv2.CASCADE_SCALE_IMAGE,
        )

        if len(faces) == 0:
            # 降低阈值重试
            faces = self._cascade.detectMultiScale(
                gray,
                scaleFactor=1.02,
                minNeighbors=3,
                minSize=(30, 30),
            )

        # 按面积排序(大→小)，主面部在前
        faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
        return [(int(x), int(y), int(w), int(h)) for x, y, w, h in faces]

    def get_primary_face(
        self, image: np.ndarray
    ) -> Optional[tuple[int, int, int, int]]:
        """获取最大的人脸"""
        faces = self.detect(image)
        return faces[0] if faces else None

    def estimate_facial_landmarks(
        self, face_rect: tuple[int, int, int, int]
    ) -> dict[str, tuple[int, int]]:
        """
        根据面部框比例估算五官位置。

        二次元面部比例 (基于框的中心归一化):
        - 眼睛线: 面部上 35-45%
        - 嘴: 面部下 25-30%
        - 眉毛: 面部上 25-35%

        Returns:
            {"left_eye": (x,y), "right_eye": (x,y), "mouth": (x,y), ...}
        """
        x, y, w, h = face_rect
        cx = x + w // 2
        cy = y + h // 2

        landmarks = {
            # 左眼: 中线偏左，上部 40%
            "left_eye": (cx - int(w * 0.16), cy - int(h * 0.08)),
            # 右眼: 中线偏右，上部 40%
            "right_eye": (cx + int(w * 0.16), cy - int(h * 0.08)),
            # 嘴: 中线，下部 25%
            "mouth": (cx, cy + int(h * 0.22)),
            # 左眉: 左眼上方
            "left_eyebrow": (cx - int(w * 0.16), cy - int(h * 0.20)),
            # 右眉: 右眼上方
            "right_eyebrow": (cx + int(w * 0.16), cy - int(h * 0.20)),
            # 鼻子: 中线，眼睛下方
            "nose": (cx, cy + int(h * 0.08)),
        }
        return landmarks


# ---- SAM 辅助器 ----

class SAMHelper:
    """SAM 分割辅助器 (轻量封装)"""

    def __init__(self, model_type: str = "vit_b"):
        self.model_type = model_type
        self._predictor = None

    def load(self, device: str = "cpu") -> bool:
        """加载 SAM 模型"""
        sam_info = {
            "vit_b": "sam_vit_b_01ec64.pth",
            "vit_l": "sam_vit_l_0b3195.pth",
            "vit_h": "sam_vit_h_4b8939.pth",
        }
        checkpoint = MODEL_DIR / sam_info[self.model_type]

        if not checkpoint.exists():
            print(f"[SAMHelper] 模型未找到: {checkpoint}")
            return False

        from segment_anything import sam_model_registry, SamPredictor

        self._sam_model = sam_model_registry[self.model_type](
            checkpoint=str(checkpoint)
        )
        self._sam_model.to(device=device)
        self._predictor = SamPredictor(self._sam_model)
        return True

    def set_image(self, image: np.ndarray):
        self._predictor.set_image(image)

    def segment_from_box(
        self, box_xyxy: tuple[int, int, int, int]
    ) -> Optional[np.ndarray]:
        """从边界框分割 → 返回 mask (H,W) bool"""
        x1, y1, x2, y2 = box_xyxy
        input_box = np.array([[x1, y1, x2, y2]])
        masks, scores, _ = self._predictor.predict(
            point_coords=None, point_labels=None,
            box=input_box, multimask_output=False,
        )
        return masks[0] if masks is not None and len(masks) > 0 else None

    def segment_from_points(
        self,
        points: list[tuple[int, int]],
        labels: Optional[list[int]] = None,
    ) -> Optional[np.ndarray]:
        """从点提示分割"""
        if labels is None:
            labels = [1] * len(points)  # 默认全部前景

        point_coords = np.array(points)
        point_labels = np.array(labels)

        masks, scores, _ = self._predictor.predict(
            point_coords=point_coords,
            point_labels=point_labels,
            multimask_output=True,
        )
        if masks is not None and len(masks) > 0:
            return masks[np.argmax(scores)]
        return None


# ---- 混合分割器 ----

class AnimePartSegmenter:
    """
    动漫部位混合分割器 v3.0 — 面部放大精拆五宫

    流程:
    1. AnimeFaceDetector 检测面部位置
    2. 裁剪放大面部区域 (2x), SAM 高精度处理
    3. 在放大区域内精确分割眼/嘴/眉
    4. 全图上处理面部皮肤 + 头发 + 身体
    """

    # 可提取的部位列表
    ALL_PARTS = [
        "Face_Skin",
        "Eye_L",
        "Eye_R",
        "Mouth",
        "Eyebrow_L",
        "Eyebrow_R",
        "Nose",
        "Hair",
        "Body",
    ]

    # 面部细节部位 (需要在放大面部区域内提取)
    FACIAL_DETAIL_PARTS = {
        "Eye_L", "Eye_R", "Mouth", "Eyebrow_L", "Eyebrow_R", "Nose",
    }

    def __init__(
        self,
        sam_device: str = "cpu",
        gpt_detector=None,  # Optional[GPTLandmarkDetector]
    ):
        import torch

        self.face_detector = AnimeFaceDetector()
        self.sam = SAMHelper(model_type="vit_b")
        self.sam_device = "mps" if torch.backends.mps.is_available() else "cpu"
        self._gpt_detector = gpt_detector
        self._loaded = False

    def _ensure_loaded(self):
        if not self._loaded:
            self.sam.load(device=self.sam_device)
            self._loaded = True

    def segment(
        self,
        image: Image.Image,
        parts: Optional[list[str]] = None,
    ) -> dict[str, Image.Image]:
        """
        对前景 RGBA 图像执行混合分割。

        Args:
            image: RGBA 前景图
            parts: 要提取的部位列表 (None=全部)

        Returns:
            {layer_name: RGBA Image, ...}
        """
        self._ensure_loaded()

        if parts is None:
            parts = list(self.ALL_PARTS)

        w, h = image.size

        # 获取 RGB + Alpha
        if image.mode == "RGBA":
            rgb_arr = np.array(image.convert("RGB"))
            alpha_arr = np.array(image.getchannel("A"))
        else:
            rgb_arr = np.array(image.convert("RGB"))
            alpha_arr = np.ones((h, w), dtype=np.uint8) * 255

        foreground_mask = alpha_arr > 10

        # ---- Step 1: 检测面部 ----
        print("[AnimePart] 检测动漫面部...")
        face_rect = self.face_detector.get_primary_face(rgb_arr)

        if face_rect is None:
            print("[AnimePart] 未检测到面部，回退到启发式分割")
            return self._fallback_heuristic(image)

        fx, fy, fw, fh = face_rect
        print(f"[AnimePart] 面部: x={fx} y={fy} w={fw} h={fh}")

        # ---- Step 2: SAM 全图分割面部皮肤 ----
        print("[AnimePart] SAM 分割面部...")
        self.sam.set_image(rgb_arr)
        face_mask = self.sam.segment_from_box(
            (fx, fy, fx + fw, fy + fh)
        )

        if face_mask is None:
            print("[AnimePart] SAM 面部分割失败")
            return self._fallback_heuristic(image)

        face_mask = face_mask & foreground_mask

        layers = {}
        need_facial_details = any(p in self.FACIAL_DETAIL_PARTS for p in parts)

        # ---- Step 3: 面部放大 → 精拆五宫 ----
        if need_facial_details:
            print("[AnimePart] 🔍 面部放大精拆五宫...")
            facial_layers = self._extract_facial_details_zoomed(
                rgb_arr, foreground_mask, face_rect
            )

            for name, mask in facial_layers.items():
                if name in parts:
                    layers[name] = self._mask_to_layer(rgb_arr, mask)
                    # 从面部皮肤中减去 (确保形状匹配)
                    if mask.shape == face_mask.shape:
                        face_mask = face_mask & ~mask

        # ---- Step 4: 面部皮肤图层 ----
        if "Face_Skin" in parts:
            layers["Face_Skin"] = self._mask_to_layer(rgb_arr, face_mask)

        # ---- Step 5: 头发 + 身体 ----
        remaining = foreground_mask.copy()
        for layer in layers.values():
            layer_alpha = np.array(layer.getchannel("A")) > 10
            # 轻微侵蚀以免相邻图层出现缝隙
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
            layer_alpha = cv2.erode(layer_alpha.astype(np.uint8), kernel, iterations=1).astype(bool)
            remaining = remaining & ~layer_alpha

        if np.sum(remaining) > 100:
            need_hair = "Hair" in parts
            need_body = "Body" in parts

            if need_hair or need_body:
                hair_mask, body_mask = self._separate_hair_body(
                    rgb_arr, remaining, face_rect
                )

                if need_hair and np.sum(hair_mask) > 100:
                    layers["Hair"] = self._mask_to_layer(rgb_arr, hair_mask)
                    remaining = remaining & ~hair_mask

                if need_body and np.sum(body_mask) > 100:
                    layers["Body"] = self._mask_to_layer(rgb_arr, body_mask)
                    remaining = remaining & ~body_mask

            # 残余作为 Other
            if np.sum(remaining) > 100:
                layers["Other"] = self._mask_to_layer(rgb_arr, remaining)

        # 按面积排序
        layers = dict(
            sorted(layers.items(),
                   key=lambda kv: np.sum(np.array(kv[1].getchannel("A")) > 10),
                   reverse=True)
        )

        print(f"[AnimePart] 分割完成: {list(layers.keys())}")
        return layers

    def _extract_facial_details_zoomed(
        self,
        rgb_full: np.ndarray,
        fg_mask: np.ndarray,
        face_rect: tuple[int, int, int, int],
    ) -> dict[str, np.ndarray]:
        """
        🔍 面部区域放大后再做 SAM 分割，精度大幅提升。
        """
        fx, fy, fw, fh = face_rect
        H, W = rgb_full.shape[:2]
        H, W = int(H), int(W)

        # 裁剪区域: 面部 + 50% 边距
        margin_x = int(fw * 0.5)
        margin_y = int(fh * 0.5)
        crop_x1 = max(0, int(fx) - margin_x)
        crop_y1 = max(0, int(fy) - margin_y)
        crop_x2 = min(W, int(fx) + int(fw) + margin_x)
        crop_y2 = min(H, int(fy) + int(fh) + margin_y)
        crop_w = crop_x2 - crop_x1
        crop_h = crop_y2 - crop_y1

        print(f"[AnimePart] 面部裁剪: ({crop_x1},{crop_y1})-({crop_x2},{crop_y2}), "
              f"尺寸={crop_w}x{crop_h} | 全图={W}x{H}")

        if crop_w < 20 or crop_h < 20:
            print("[AnimePart] ⚠️ 面部区域太小，跳过五官精拆")
            return {}

        face_crop = rgb_full[crop_y1:crop_y2, crop_x1:crop_x2].copy()

        # 放大裁剪区域
        target_size = 1024
        scale = min(target_size / max(crop_w, 1), target_size / max(crop_h, 1))
        new_w = max(64, int(crop_w * scale))
        new_h = max(64, int(crop_h * scale))

        if crop_w < target_size or crop_h < target_size:
            face_zoomed = cv2.resize(face_crop, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
            print(f"[AnimePart] 面部放大: {crop_w}x{crop_h} → {new_w}x{new_h} ({scale:.1f}x)")
        else:
            face_zoomed = face_crop
            new_w, new_h = crop_w, crop_h
            scale = 1.0

        self.sam.set_image(face_zoomed)

        # ---- 获取五官地标 ----
        landmarks = None

        if self._gpt_detector is not None and self._gpt_detector.api_key:
            print("[AnimePart] 🤖 GPT-5.5 Vision 地标检测...")
            try:
                face_pil = Image.fromarray(face_crop)
                gpt_landmarks = self._gpt_detector.detect(face_pil)
                if gpt_landmarks:
                    landmarks = {}
                    for name, (gx, gy) in gpt_landmarks.items():
                        zx = max(5, min(int(gx * scale), new_w - 6))
                        zy = max(5, min(int(gy * scale), new_h - 6))
                        landmarks[name] = (int(zx), int(zy))
                    print("[AnimePart] ✅ GPT 精确坐标")
            except Exception as e:
                print(f"[AnimePart] GPT 出错: {e}, 回退估算")

        if landmarks is None:
            print("[AnimePart] 比例估算坐标")
            est = self.face_detector.estimate_facial_landmarks(face_rect)
            landmarks = {}
            for k, (x, y) in est.items():
                zx = max(5, min(int((x - crop_x1) * scale), new_w - 6))
                zy = max(5, min(int((y - crop_y1) * scale), new_h - 6))
                landmarks[k] = (int(zx), int(zy))

        # ---- SAM 分割五官 + 安全映射回全图 ----
        results = {}
        parts_to_extract = [
            ("Eye_L", "left_eye"),
            ("Eye_R", "right_eye"),
            ("Mouth", "mouth"),
            ("Eyebrow_L", "left_eyebrow"),
            ("Eyebrow_R", "right_eyebrow"),
            ("Nose", "nose"),
        ]

        for layer_name, lm_key in parts_to_extract:
            if lm_key not in landmarks:
                continue
            pt = landmarks[lm_key]
            try:
                mask_z = self.sam.segment_from_points([pt], [1])
            except Exception as e:
                print(f"[AnimePart] SAM {layer_name} 失败: {e}")
                continue

            if mask_z is None:
                continue

            # zoomed mask → full image mask
            try:
                if scale != 1.0:
                    mask_crop = cv2.resize(
                        mask_z.astype(np.uint8), (crop_w, crop_h),
                        interpolation=cv2.INTER_NEAREST,
                    ).astype(bool)
                    # 精确裁剪到 crop 尺寸
                    mask_crop = mask_crop[:crop_h, :crop_w]
                else:
                    mask_crop = mask_z[:crop_h, :crop_w]

                mask_full = np.zeros((H, W), dtype=bool)
                put_h = min(mask_crop.shape[0], crop_y2 - crop_y1)
                put_w = min(mask_crop.shape[1], crop_x2 - crop_x1)
                mask_full[crop_y1:crop_y1 + put_h, crop_x1:crop_x1 + put_w] = \
                    mask_crop[:put_h, :put_w]

                # 交集前景
                results[layer_name] = mask_full & fg_mask
            except Exception as e:
                print(f"[AnimePart] {layer_name} 映射失败: {e}")
                continue

        # ---- 清理: 左右眼不重叠 ----
        if "Eye_L" in results and "Eye_R" in results:
            mid_x = min(W - 1, max(1, int(fx) + int(fw) // 2))
            try:
                for x in range(mid_x, W):
                    results["Eye_L"][:, x] = False
                for x in range(0, mid_x):
                    results["Eye_R"][:, x] = False
            except Exception:
                pass  # 重合清理失败也不影响

        # ---- 形态学清洁 ----
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        for name in list(results.keys()):
            try:
                results[name] = cv2.morphologyEx(
                    results[name].astype(np.uint8), cv2.MORPH_CLOSE, kernel
                ).astype(bool)
            except Exception:
                pass

        return results

    def _separate_hair_body(
        self,
        rgb_arr: np.ndarray,
        mask: np.ndarray,
        face_rect: tuple[int, int, int, int],
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        分离头发和身体。

        启发式:
        - 面部上方的区域 → 大概率是头发
        - 面部下方的区域 → 大概率是身体
        - 颜色暗/鲜艳 + 靠近面部 → 头发
        """
        fx, fy, fw, fh = face_rect
        h, w = mask.shape

        # 获取被 mask 覆盖的像素颜色
        masked_rgb = rgb_arr.copy()
        masked_rgb[~mask] = 0

        # 头部分界线: 面部顶部往上一点
        hair_line_y = max(0, fy - int(fh * 0.2))

        hair_mask = np.zeros_like(mask)
        body_mask = np.zeros_like(mask)

        # 面部上方区域 → 头发
        hair_mask[:hair_line_y, :] = mask[:hair_line_y, :]

        # 面部下方区域 → 身体
        body_y = fy + fh + int(fh * 0.1)
        body_mask[body_y:, :] = mask[body_y:, :]

        # 面部周围 (侧面) → 基于颜色判断
        side_region = mask.copy()
        side_region[:hair_line_y, :] = False
        side_region[body_y:, :] = False

        if np.sum(side_region) > 100:
            # 对面部周围区域做肤色检测
            from .segment import SkinDetector
            skin_mask = SkinDetector.detect(Image.fromarray(rgb_arr))

            for y in range(h):
                for x in range(w):
                    if not side_region[y, x]:
                        continue
                    r, g_, b = rgb_arr[y, x]
                    brightness = (int(r) + int(g_) + int(b)) / 3
                    # 肤色 → 身体
                    if skin_mask[y, x]:
                        body_mask[y, x] = True
                    # 暗色/鲜艳 → 头发
                    elif brightness < 100 or (max(r, g_, b) - min(r, g_, b) > 60 and brightness < 200):
                        hair_mask[y, x] = True
                    # 默认身体
                    else:
                        body_mask[y, x] = True

        # 膨胀头发 mask (头发通常是连续的)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        hair_mask = cv2.dilate(hair_mask.astype(np.uint8), kernel, iterations=1).astype(bool)

        return hair_mask, body_mask

    def _mask_to_layer(self, rgb: np.ndarray, mask: np.ndarray) -> Image.Image:
        """mask → RGBA 图层"""
        alpha = (mask.astype(np.uint8) * 255)
        rgba = np.dstack([rgb, alpha])
        return Image.fromarray(rgba, mode="RGBA")

    def _fallback_heuristic(self, image: Image.Image) -> dict[str, Image.Image]:
        """面部检测失败时的兜底方案"""
        from .segment import ColorSegmenter
        seg = ColorSegmenter(n_clusters=8)
        return seg.segment(image)


# ---- 全局实例 ----

_global_anime_seg: Optional[AnimePartSegmenter] = None


def get_anime_segmenter() -> AnimePartSegmenter:
    global _global_anime_seg
    if _global_anime_seg is None:
        _global_anime_seg = AnimePartSegmenter()
    return _global_anime_seg
