"""
GPT-5.5 Vision 面部地标检测模块
===============================
将面部裁剪图发给 GPT-5.5，让它直接「看」出五官的精确像素坐标。
替换原来基于面部比例的粗糙估算。
"""

import base64
import io
import json
import re
from typing import Optional

import numpy as np
from PIL import Image


# ---- GPT Vision API 调用 ----

class GPTLandmarkDetector:
    """
    使用 GPT Vision 检测动漫面部的精确五官位置。

    用法:
        detector = GPTLandmarkDetector(api_key="...")
        landmarks = detector.detect(face_crop_image)
        # => {"left_eye": (x,y), "right_eye": (x,y), ...}
    """

    # 标准五官 Key
    LANDMARK_KEYS = [
        "left_eye",
        "right_eye",
        "mouth",
        "left_eyebrow",
        "right_eyebrow",
        "nose",
    ]

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://right.codes/codex/v1",
        model: str = "gpt-5.5",
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def detect(
        self,
        face_image: Image.Image,
        description: str = "",
    ) -> Optional[dict[str, tuple[int, int]]]:
        """
        让 GPT 看图，输出五官坐标。

        Args:
            face_image: 面部裁剪图 (RGB)
            description: 可选的额外描述 (如 "侧脸朝左")

        Returns:
            {"left_eye": (x,y), "right_eye": (x,y), "mouth": (x,y), ...}
            失败返回 None
        """
        if not self.api_key:
            print("[GPT-Landmark] 未设置 API Key，使用估算坐标")
            return None

        w, h = face_image.size
        print(f"[GPT-Landmark] 发送面部图片 ({w}x{h}) 给 {self.model}...")

        # 编码为 base64
        buf = io.BytesIO()
        face_image.save(buf, format="JPEG", quality=85)
        img_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        prompt = self._build_prompt(w, h, description)

        try:
            response = self._call_api(img_base64, prompt)
            landmarks = self._parse_response(response, w, h)
            if landmarks:
                print(f"[GPT-Landmark] ✅ 检测到 {len(landmarks)} 个地标:")
                for name, (x, y) in landmarks.items():
                    print(f"  • {name}: ({x}, {y})")
            return landmarks
        except Exception as e:
            print(f"[GPT-Landmark] ❌ API 调用失败: {e}")
            return None

    def _build_prompt(self, w: int, h: int, description: str) -> str:
        desc_text = f"\n额外信息: {description}" if description else ""

        return f"""You are analyzing an anime/game character illustration face crop.
Image size: {w} x {h} pixels.{desc_text}

Please identify the EXACT pixel coordinates of these facial features:

1. left_eye — center of the LEFT eye (character's left, your right side of image)
2. right_eye — center of the RIGHT eye (character's right, your left side of image)
3. mouth — center of the mouth
4. left_eyebrow — center of the LEFT eyebrow
5. right_eyebrow — center of the RIGHT eyebrow
6. nose — tip of the nose (or center-bottom of nose bridge)

Rules:
- If a feature is NOT VISIBLE (e.g., side profile hiding one eye), set its coordinates to [-1, -1].
- The character's LEFT means the eye on YOUR RIGHT when looking at the image.
- Be PRECISE — pixel-level accuracy matters.

Return ONLY a JSON object, no other text:

{{
  "left_eye": [x, y],
  "right_eye": [x, y],
  "mouth": [x, y],
  "left_eyebrow": [x, y],
  "right_eyebrow": [x, y],
  "nose": [x, y]
}}"""

    def _call_api(self, img_base64: str, prompt: str) -> str:
        import urllib.request
        import urllib.error

        url = f"{self.base_url}/chat/completions"

        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_base64}",
                                "detail": "high",
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
            "max_tokens": 300,
            "temperature": 0.0,
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        content = data["choices"][0]["message"]["content"]
        return content

    def _parse_response(
        self,
        content: str,
        img_w: int,
        img_h: int,
    ) -> Optional[dict[str, tuple[int, int]]]:
        """从 GPT 返回的文本中提取 JSON 坐标"""
        # 尝试直接 JSON 解析
        try:
            coords = json.loads(content)
        except json.JSONDecodeError:
            # 尝试从 markdown code block 中提取
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
            if json_match:
                try:
                    coords = json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    print(f"[GPT-Landmark] 无法解析 JSON: {content[:200]}")
                    return None
            else:
                # 尝试提取任何 {...} 块
                brace_match = re.search(r'\{[\s\S]*\}', content)
                if brace_match:
                    try:
                        coords = json.loads(brace_match.group(0))
                    except json.JSONDecodeError:
                        print(f"[GPT-Landmark] JSON 解析失败: {content[:200]}")
                        return None
                else:
                    print(f"[GPT-Landmark] 未找到 JSON: {content[:200]}")
                    return None

        # 验证坐标
        result = {}
        for key in self.LANDMARK_KEYS:
            if key not in coords:
                continue
            val = coords[key]
            if not isinstance(val, (list, tuple)) or len(val) != 2:
                continue
            x, y = val

            # 负值 = 不可见
            if x < 0 or y < 0:
                continue

            # 裁剪到图像内
            x = max(0, min(int(x), img_w - 1))
            y = max(0, min(int(y), img_h - 1))
            result[key] = (x, y)

        return result if result else None


# ---- 便捷函数 ----

def convert_landmarks_to_full_image(
    landmarks: dict[str, tuple[int, int]],
    crop_offset: tuple[int, int],
    scale: float,
) -> dict[str, tuple[int, int]]:
    """
    将裁剪图中的地标坐标映射回全图坐标。

    Args:
        landmarks: {"left_eye": (cx, cy), ...} in crop space
        crop_offset: (crop_x1, crop_y1) — 裁剪区域在全图中的偏移
        scale: 裁剪图放大倍数 (>1 = 放大)

    Returns:
        全图坐标的 landmarks
    """
    ox, oy = crop_offset
    result = {}
    for name, (x, y) in landmarks.items():
        # 先除以 scale 回到裁剪前
        orig_x = int(x / scale)
        orig_y = int(y / scale)
        # 再加回偏移
        result[name] = (orig_x + ox, orig_y + oy)
    return result
