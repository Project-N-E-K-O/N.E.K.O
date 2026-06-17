"""
Grounded-SAM 集成模块
GroundingDINO (文本提示检测) + SAM (精确分割)
==============================================
用于对动漫角色进行语义级别的部位分割:
通过自然语言提示 (如 "face", "hair", "left eye") 检测并分割每个部位。
"""

import os
import urllib.request
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import torch
from PIL import Image

from config import MODEL_DIR


# ---- 模型权重下载 ----

SAM_CHECKPOINTS = {
    "vit_b": {
        "url": "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth",
        "path": "sam_vit_b_01ec64.pth",
    },
    "vit_l": {
        "url": "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_l_0b3195.pth",
        "path": "sam_vit_l_0b3195.pth",
    },
    "vit_h": {
        "url": "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth",
        "path": "sam_vit_h_4b8939.pth",
    },
}

GROUNDINGDINO_CHECKPOINTS = {
    "swin_t": {
        "url": "https://huggingface.co/ShilongLiu/GroundingDINO/resolve/main/groundingdino_swint_ogc.pth",
        "config": "GroundingDINO_SwinT_OGC.py",
        "path": "groundingdino_swint_ogc.pth",
    },
    "swin_b": {
        "url": "https://huggingface.co/ShilongLiu/GroundingDINO/resolve/main/groundingdino_swinb_ogc.pth",
        "config": "GroundingDINO_SwinB_OGC.py",
        "path": "groundingdino_swinb_ogc.pth",
    },
}


def download_file(url: str, dest: Path, desc: str = "") -> Path:
    """下载文件，带进度显示"""
    if dest.exists():
        print(f"[Download] {desc} 已存在: {dest}")
        return dest

    print(f"[Download] 下载 {desc}...")
    print(f"  URL: {url}")
    print(f"  目标: {dest}")

    def progress_hook(block_num, block_size, total_size):
        downloaded = block_num * block_size
        if total_size > 0:
            pct = min(100, downloaded * 100 // total_size)
            if block_num % 50 == 0:
                print(f"  {desc}: {pct}% ({downloaded//(1024*1024)}MB / {total_size//(1024*1024)}MB)")

    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, str(dest), progress_hook)
    print(f"  完成: {dest}")
    return dest


# ---- Grounded-SAM 分割器 ----

class GroundedSAM:
    """
    GroundingDINO + SAM 联合分割器。

    用法:
        gsam = GroundedSAM()
        gsam.load_models()  # 首次会下载 ~1GB 模型权重
        layers = gsam.segment(image, prompts=["face", "hair", "body"])
    """

    # Live2D 部位的文字提示
    DEFAULT_PROMPTS = [
        "face",
        "hair",
        "left eye",
        "right eye",
        "mouth",
        "nose",
        "left eyebrow",
        "right eyebrow",
        "body",
        "left arm",
        "right arm",
        "left hand",
        "right hand",
    ]

    def __init__(
        self,
        sam_model_type: str = "vit_b",
        gd_model_type: str = "swin_t",
        box_threshold: float = 0.25,
        text_threshold: float = 0.2,
        device: Optional[str] = None,
    ):
        """
        Args:
            sam_model_type: "vit_b" | "vit_l" | "vit_h"
            gd_model_type: "swin_t" | "swin_b"
            box_threshold: GroundingDINO 检测框置信度阈值
            text_threshold: GroundingDINO 文本匹配阈值
            device: "cuda" | "cpu" | "mps" | None (auto)
        """
        self.sam_model_type = sam_model_type
        self.gd_model_type = gd_model_type
        self.box_threshold = box_threshold
        self.text_threshold = text_threshold

        if device is None:
            # GroundingDINO 的自定义 C++ 算子是 CUDA-only，
            # Mac MPS 不兼容 → GD 用 CPU, SAM 用 MPS
            self.gd_device = "cpu"
            self.sam_device = "mps" if torch.backends.mps.is_available() else "cpu"
        else:
            self.gd_device = device
            self.sam_device = device

        self._gd_model = None
        self._sam_model = None
        self._sam_predictor = None
        self._is_loaded = False

        print(f"[GroundedSAM] GD 设备: {self.gd_device}, SAM 设备: {self.sam_device}")
        print(f"[GroundedSAM] SAM: {sam_model_type}, GD: {gd_model_type}")

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded

    def load_models(self) -> bool:
        """加载 (并下载) 模型权重"""
        if self._is_loaded:
            return True

        try:
            # ---- 加载 GroundingDINO ----
            gd_info = GROUNDINGDINO_CHECKPOINTS[self.gd_model_type]
            gd_checkpoint = MODEL_DIR / gd_info["path"]

            # 下载 GroundingDINO 权重
            if not gd_checkpoint.exists():
                print(f"\n[GroundedSAM] 需要下载 GroundingDINO 权重 (~700MB)...")
                download_file(gd_info["url"], gd_checkpoint, "GroundingDINO")

            # GroundingDINO config 在 package 内
            from groundingdino.util.inference import load_model as load_gd

            # config 路径: groundingdino/config/GroundingDINO_SwinT_OGC.py
            import groundingdino
            gd_pkg_path = Path(groundingdino.__file__).parent
            gd_config = str(gd_pkg_path / "config" / gd_info["config"])

            print(f"[GroundedSAM] 加载 GroundingDINO: {gd_info['config']}")
            self._gd_model = load_gd(
                model_config_path=gd_config,
                model_checkpoint_path=str(gd_checkpoint),
                device=self.gd_device,
            )
            print("[GroundedSAM] GroundingDINO 加载完成")

            # ---- 加载 SAM ----
            sam_info = SAM_CHECKPOINTS[self.sam_model_type]
            sam_checkpoint = MODEL_DIR / sam_info["path"]

            if not sam_checkpoint.exists():
                print(f"\n[GroundedSAM] 需要下载 SAM 权重 (~380MB for vit_b)...")
                download_file(sam_info["url"], sam_checkpoint, f"SAM {self.sam_model_type}")

            from segment_anything import sam_model_registry, SamPredictor

            print(f"[GroundedSAM] 加载 SAM: {self.sam_model_type}")
            self._sam_model = sam_model_registry[self.sam_model_type](
                checkpoint=str(sam_checkpoint)
            )
            self._sam_model.to(device=self.sam_device)
            self._sam_predictor = SamPredictor(self._sam_model)
            print("[GroundedSAM] SAM 加载完成")

            self._is_loaded = True
            print("[GroundedSAM] ✅ 所有模型就绪\n")
            return True

        except Exception as e:
            print(f"[GroundedSAM] ❌ 模型加载失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def segment(
        self,
        image: Image.Image,
        prompts: Optional[list[str]] = None,
    ) -> dict[str, Image.Image]:
        """
        执行语义分割。

        Args:
            image: 输入 PIL Image (RGB)
            prompts: 部位文字提示列表，默认使用 DEFAULT_PROMPTS

        Returns:
            {prompt_name: RGBA_layer, ...}
            图层按面积从大到小排列
        """
        if not self._is_loaded:
            ok = self.load_models()
            if not ok:
                return {}

        if prompts is None:
            prompts = self.DEFAULT_PROMPTS

        # PIL → numpy (RGB)
        image_np = np.array(image.convert("RGB"))
        h, w = image_np.shape[:2]

        print(f"[GroundedSAM] 检测 {len(prompts)} 个部位: {prompts}")

        # ---- Step 1: GroundingDINO 检测 ----
        # 预处理图像 (GroundingDINO 需要特定的 transform)
        from groundingdino.datasets import transforms as T
        transform = T.Compose([
            T.RandomResize([800], max_size=1333),
            T.ToTensor(),
            T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])
        image_transformed, _ = transform(Image.fromarray(image_np), None)

        from groundingdino.util.inference import predict as gd_predict

        boxes, logits, phrases = gd_predict(
            model=self._gd_model,
            image=image_transformed,
            caption=". ".join(prompts),
            box_threshold=self.box_threshold,
            text_threshold=self.text_threshold,
            device=self.gd_device,
        )

        print(f"[GroundedSAM] 检测到 {len(boxes)} 个对象:")
        for box, logit, phrase in zip(boxes, logits, phrases):
            print(f"  • {phrase}: conf={logit:.3f}, box={box.tolist()}")

        if len(boxes) == 0:
            print("[GroundedSAM] 未检测到任何对象，尝试降低阈值...")
            boxes, logits, phrases = gd_predict(
                model=self._gd_model,
                image=image_transformed,
                caption=". ".join(prompts),
                box_threshold=0.15,
                text_threshold=0.1,
                device=self.gd_device,
            )
            if len(boxes) == 0:
                print("[GroundedSAM] 仍然未检测到对象")
                return {}

        # ---- Step 2: SAM 分割 ----
        self._sam_predictor.set_image(image_np)

        layers = {}
        for i, (box, phrase) in enumerate(zip(boxes, phrases)):
            # GroundingDINO 返回 (cx, cy, w, h) 归一化坐标
            # 转为 (x1, y1, x2, y2) 像素坐标
            cx, cy, bw, bh = box.tolist()
            x1 = int((cx - bw / 2) * w)
            y1 = int((cy - bh / 2) * h)
            x2 = int((cx + bw / 2) * w)
            y2 = int((cy + bh / 2) * h)

            # 裁剪到图像范围内
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(w, x2)
            y2 = min(h, y2)

            if x2 <= x1 or y2 <= y1:
                continue

            input_box = np.array([[x1, y1, x2, y2]])

            # SAM 预测
            masks, scores, _ = self._sam_predictor.predict(
                point_coords=None,
                point_labels=None,
                box=input_box,
                multimask_output=False,
            )

            if masks is None or len(masks) == 0:
                continue

            mask = masks[0]  # (H, W) bool
            score = scores[0] if len(scores) > 0 else 0.0

            # 创建 RGBA 图层
            alpha = (mask * 255).astype(np.uint8)
            rgba = np.dstack([image_np, alpha])
            layer_img = Image.fromarray(rgba, mode="RGBA")

            # 清理图层名
            clean_name = phrase.strip().lower().replace(" ", "_")
            layers[clean_name] = layer_img

            print(f"  ✓ {clean_name}: mask_size={np.sum(mask)}px, sam_score={score:.3f}")

        # ---- Step 3: 去重 ----
        # 同一部位可能被多个框检测到，保留质量最好的
        layers = self._deduplicate_layers(layers)

        # 按面积从大到小排序
        layers = dict(
            sorted(layers.items(), key=lambda kv: self._mask_area(kv[1]), reverse=True)
        )

        print(f"[GroundedSAM] 最终图层: {list(layers.keys())}")
        return layers

    def _deduplicate_layers(
        self, layers: dict[str, Image.Image], iou_threshold: float = 0.7
    ) -> dict[str, Image.Image]:
        """合并高度重叠的图层 (IoU > threshold)"""
        if len(layers) <= 1:
            return layers

        names = list(layers.keys())
        masks = {}

        for name in names:
            img = layers[name]
            alpha = np.array(img.getchannel("A"))
            masks[name] = alpha > 128

        merged = set()
        result = {}

        for i, name_i in enumerate(names):
            if name_i in merged:
                continue
            best_name = name_i
            best_mask = masks[name_i]
            best_area = np.sum(best_mask)

            for j, name_j in enumerate(names):
                if i == j or name_j in merged:
                    continue

                mask_i = masks[name_i]
                mask_j = masks[name_j]

                intersection = np.sum(mask_i & mask_j)
                union = np.sum(mask_i | mask_j)
                iou = intersection / union if union > 0 else 0

                if iou > iou_threshold:
                    # 保留面积更大的
                    area_j = np.sum(mask_j)
                    if area_j > best_area:
                        best_name = name_j
                        best_mask = mask_j
                        best_area = area_j
                    merged.add(name_j)

            merged.add(best_name)
            result[best_name] = layers[best_name]

        return result

    @staticmethod
    def _mask_area(layer: Image.Image) -> int:
        """计算图层有效像素数"""
        if layer.mode == "RGBA":
            alpha = np.array(layer.getchannel("A"))
            return int(np.sum(alpha > 10))
        return layer.width * layer.height


# ---- 便捷函数 ----

_global_gsam: Optional[GroundedSAM] = None


def get_gsam(
    sam_type: str = "vit_b",
    gd_type: str = "swin_t",
    force_reload: bool = False,
) -> GroundedSAM:
    """获取全局 GroundedSAM 实例 (单例)"""
    global _global_gsam
    if _global_gsam is None or force_reload:
        _global_gsam = GroundedSAM(
            sam_model_type=sam_type,
            gd_model_type=gd_type,
        )
        _global_gsam.load_models()
    return _global_gsam
