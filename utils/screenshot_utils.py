# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Screenshot analysis utility library
Provides screenshot analysis, including screenshots sent from the frontend browser and screen-share data stream handling
"""
import base64
import sys
from typing import Optional, Dict
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type
import asyncio
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageOps
from utils.llm_client import create_chat_llm_async

logger = get_module_logger(__name__)

# 安全限制：最大图片大小 (10MB，base64编码后约13.3MB)
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024
MAX_BASE64_SIZE = MAX_IMAGE_SIZE_BYTES * 4 // 3 + 100

# 截图压缩默认参数：与前端手动截图 / 屏幕分享口径对齐（720p, JPEG quality 80）。
# 前端已统一把发给后端的画面压到 720p，这里的 vision 分析、后端 pyautogui 兜底
# 等再压也保持同一档位，避免一边 720 一边 1080 的不一致。
COMPRESS_TARGET_HEIGHT = 720
COMPRESS_JPEG_QUALITY = 80

# 送进模型的图片宽度上限。1280 不是新拍的数：static/app/app-state.js 里前端截图
# 用的就是 MAX_SCREENSHOT_WIDTH: 1280，所以后端归一化到同一个数，等于让两端讲
# 同一套口径，而不是前端 1280 / 后端不限宽地各走各的。
# 只约束高度是不够的：compress_screenshot 的高度闸对 16000x400 这种超宽图完全
# 不触发（高度本来就合规），5120x1440 也只压到 2560x720——宽度仍是 2560。宽高
# 两个上限一起给，才真的是「一个尺寸档位」。
MODEL_IMAGE_MAX_WIDTH = 1280

# 只探测图片头部所需的 base64 前缀长度。PNG / JPEG 的尺寸字段都在流的最前面，
# 64 KiB 绰绰有余；Pillow 无法从截断流里解析的格式（WebP）会回落到整段解码。
# 同一手法在 app/main_server/character_runtime.py 的 _inline_image_data_url_mime
# 里已经用过——那里量到的是 ~0.1 ms 固定开销，而整段解码一张 8 MiB 的图只为读
# 一个头部要 ~14 ms。
_MODEL_PROFILE_HEADER_PREFIX_B64_CHARS = 64 * 1024

_LANCZOS = getattr(Image, 'LANCZOS', getattr(Image, 'ANTIALIAS', 1))

LOCAL_MAX_PIXELS = 100_000_000

def _validate_image_data(image_bytes: bytes) -> Optional[Image.Image]:
    """Validate image data integrity

    First verify() checks the format, then the image is reopened and load() forces full
    pixel decoding, ensuring the data is complete and usable for later processing (the
    Image object after verify cannot be reused).
    """
    try:
        # 第一遍: 轻量格式校验
        probe = Image.open(BytesIO(image_bytes))
        probe.verify()  # verify 后此对象不可再用

        # 第二遍: 完整解码像素, 保证数据可用
        image = Image.open(BytesIO(image_bytes))

        # 像素数安全检查 (防止超大图片耗尽内存)
        max_pixels = min(Image.MAX_IMAGE_PIXELS or LOCAL_MAX_PIXELS, LOCAL_MAX_PIXELS)
        w, h = image.size
        if w * h > max_pixels:
            raise ValueError(
                f"Image too large: {w}x{h} = {w * h} pixels, limit {max_pixels}"
            )

        image.load()  # 强制解码, 提前暴露截断/损坏问题
        return image
    except Exception as e:
        logger.warning(f"图片验证失败: {e}")
        return None


def compress_screenshot(
    img: Image.Image,
    target_h: int = COMPRESS_TARGET_HEIGHT,
    quality: int = COMPRESS_JPEG_QUALITY,
    max_w: Optional[int] = None,
) -> bytes:
    """Bound the image to *target_h* (and optionally *max_w*) and encode as JPEG.

    Downscale only, aspect ratio preserved, and a single resize even when both
    bounds bite -- resizing twice would cost a second resampling pass on the
    same pixels for nothing.

    ``max_w`` defaults to None, and with it None this is HEIGHT ONLY, exactly
    as it always was: width is whatever the aspect ratio makes it and is never
    clamped, so the call is not the general size bound its name suggests.
    5120x1440 comes out 2560x720 -- still 2560 wide -- and 16000x400 comes out
    16000x400 completely untouched, because its height already fits and the
    resize branch never runs. An ultrawide or a stitched multi-monitor grab
    therefore passes through at full width and the JPEG can stay large even
    though the call "compressed" it.

    Pass ``max_w`` and the result is inside BOTH bounds, which is what a caller
    handing the image to a model wants (see ``normalize_image_for_model``). The
    default stays None on purpose: brain/computer_use.py asks for target_h=1080
    deliberately and every existing caller must keep coming out byte-identical,
    so the new bound has to be opt-in rather than a new floor everyone silently
    inherits.

    Callers that need a BYTE ceiling (WebSocket frame limits, upload limits,
    per-turn image budgets) still have to impose it themselves -- a pixel bound
    is not a byte bound.
    """
    w, h = img.size
    new_w, new_h = w, h
    if h > target_h:
        # 与旧实现逐字节一致的写法（先算 ratio 再乘），别改成 int(w * target_h / h)
        # ——两者的浮点求值顺序不同，个别尺寸会差 1 像素，而这条路上所有既有调用
        # 方都要求结果不变。
        ratio = target_h / h
        new_w, new_h = int(w * ratio), target_h
    if max_w is not None and max_w > 0 and new_w > max_w:
        ratio = max_w / new_w
        new_w, new_h = max_w, int(new_h * ratio)
    if (new_w, new_h) != (w, h):
        # 夹到 >=1：极端长条（比如 1x1000）按比例算出来会是 0 宽，PIL 存不了。
        # 旧实现在这种输入上本来就会炸，所以这层保护不改变任何原本能出图的用例。
        img = img.resize((max(1, new_w), max(1, new_h)), _LANCZOS)
    buf = BytesIO()
    if img.mode == "RGBA":
        img = img.convert("RGB")
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def decode_and_compress_screenshot_b64(
    b64_raw: str,
    target_h: int = COMPRESS_TARGET_HEIGHT,
    quality: int = COMPRESS_JPEG_QUALITY,
) -> str:
    """Decode a base64-encoded screenshot, normalize to RGB, and return a
    base64 JPEG string (without the ``data:`` prefix).

    Entirely synchronous and CPU/IO-bound — callers in async contexts MUST
    invoke via ``await asyncio.to_thread(...)`` to keep the event loop free.
    """
    img = Image.open(BytesIO(base64.b64decode(b64_raw)))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    jpg_bytes = compress_screenshot(img, target_h=target_h, quality=quality)
    return base64.b64encode(jpg_bytes).decode("utf-8")


def _probe_image_profile(b64: str) -> Optional[tuple]:
    """Read ``(format, (width, height))`` from a base64 image's HEADER only.

    Decodes a bounded base64 prefix instead of the whole payload, so the cost
    does not scale with the image: the dimensions and the format marker of PNG
    and JPEG sit in the first few dozen bytes. Formats Pillow cannot open from
    a truncated stream (WebP) fall back to decoding the full payload, which is
    harmless because that fallback is the rare case, not the hot one.

    Returns None when nothing can be established -- a caller that cannot read
    the header should treat the payload as "unknown", never as "already fine".
    """
    head = b64[:_MODEL_PROFILE_HEADER_PREFIX_B64_CHARS]
    # base64 解码要求 4 的整数倍，截断出来的前缀先对齐再喂给 b64decode。
    head = head[: len(head) - (len(head) % 4)]
    candidates = [head] if head and head != b64 else []
    candidates.append(b64)
    for candidate in candidates:
        if not candidate:
            continue
        try:
            raw = base64.b64decode(candidate)
        except Exception:
            return None
        try:
            with Image.open(BytesIO(raw)) as image:
                w, h = image.size
                fmt = str(image.format or "").upper()
                # 报的是**显示**尺寸，不是存储尺寸。带 EXIF orientation 5-8 的
                # 照片，存储矩阵与显示方向差一个 90 度：一张显示为 1000x3000 的
                # 竖图，存储的是 3000x1000。拿存储尺寸去判「已经在档位内了吗」，
                # 判的是一个用户和模型都看不到的形状——横竖一颠倒，宽高各自对
                # 哪条边界也就跟着错了。
                #
                # getexif() 读的是文件头，和 size 一样不需要解码像素，所以这里
                # 仍然是 O(1)，前缀探测的成本优势没有丢。
                try:
                    orientation = image.getexif().get(0x0112)
                except Exception:
                    orientation = None
                if orientation in (5, 6, 7, 8):
                    w, h = h, w
        except Exception:
            # 前缀不够这个格式开头（WebP 之类）——落到下一个候选，也就是整段。
            continue
        if w > 0 and h > 0:
            return fmt, (w, h)
    return None


def normalize_image_for_model(b64: str) -> str:
    """Re-encode a base64 image to the profile we send models, and return it.

    The profile is JPEG q80, at most ``MODEL_IMAGE_MAX_WIDTH`` wide and at most
    ``COMPRESS_TARGET_HEIGHT`` high. Nothing used to guarantee that:
    ``fit_images_to_turn_budget`` was a pure CEILING that returned early
    whenever the payload already fit, and a plugin image normalized by the SDK
    arrives at 2048x1536 / ~49 KiB -- far under any byte budget, so it reached
    the model at 1536px high with nothing ever touching it. That function now
    calls this one as its rung 0, before any budget test.

    FIXED POINT, and that property is the whole reason the header probe exists.
    Images ride ``_conversation_history`` for several more turns, so this runs
    over the SAME payload again and again; a version that re-encoded every time
    would degrade the picture generationally, one JPEG round-trip per turn, for
    no benefit at all. So an input that is ALREADY JPEG and already inside both
    bounds is returned as the identical string object, untouched. The output of
    this function always satisfies that test, which is what makes feeding the
    output back a no-op.

    Deliberately NOT part of the skip test: how many bytes the payload is. A
    byte threshold would break the fixed point -- a normalized image that still
    sat above it would be re-encoded forever, once per turn. Bytes belong to
    the budget ladder in main_logic/proactive_delivery.py, which owns them.

    On any decode/encode failure the input is returned unchanged: losing the
    image entirely is far worse than sending it at the wrong resolution.

    Entirely synchronous and CPU-bound -- callers in async contexts MUST invoke
    via ``await asyncio.to_thread(...)``, the same contract
    ``decode_and_compress_screenshot_b64`` carries.
    """
    if not isinstance(b64, str) or not b64:
        return b64

    profile = _probe_image_profile(b64)
    if profile is not None:
        fmt, (w, h) = profile
        if fmt == "JPEG" and w <= MODEL_IMAGE_MAX_WIDTH and h <= COMPRESS_TARGET_HEIGHT:
            return b64

    try:
        img = Image.open(BytesIO(base64.b64decode(b64)))
        # 先按 EXIF 摆正，再谈缩放。顺序不能反：resize 之后像素矩阵变了、
        # 而 compress_screenshot 存 JPEG 时不写 EXIF，orientation 标记就此丢失
        # ——模型拿到的是一张躺倒 90 度的照片，而且它没有任何线索能看出来。
        # 实测：3000x1000 + orientation=6（显示应为 1000x3000 竖图）经这里出来
        # 是 1280x426 的横图、EXIF 为 None。
        #
        # exif_transpose 把方向烘进像素并去掉该标记，所以输出天然是「无 EXIF
        # 且已摆正」，再喂回本函数时探测读到的就是它的真实形状，不动点不受影响。
        img = ImageOps.exif_transpose(img) or img
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        jpg_bytes = compress_screenshot(
            img,
            target_h=COMPRESS_TARGET_HEIGHT,
            quality=COMPRESS_JPEG_QUALITY,
            max_w=MODEL_IMAGE_MAX_WIDTH,
        )
    except Exception as e:
        logger.warning(f"图片归一化到模型档位失败，按原样送出: {e}")
        return b64
    return base64.b64encode(jpg_bytes).decode("utf-8")


async def process_screen_data(data: str) -> Optional[str]:
    """
    Handle the screen-share data stream sent by the frontend
    The frontend already compresses uniformly to 720p JPEG; this method only validates, with no second downscale
    
    Args:
        data: screen data sent by the frontend, in the form 'data:image/jpeg;base64,...'
    
    Returns: the validated base64 string (without the data: prefix), or None when validation fails
    """
    try:
        if not isinstance(data, str) or not data.startswith('data:image/jpeg;base64,'):
            logger.error("无效的屏幕数据格式")
            return None
        
        img_b64 = data.split(',')[1]
        
        if len(img_b64) > MAX_BASE64_SIZE:
            logger.error(f"屏幕数据过大: {len(img_b64)} 字节，超过限制 {MAX_BASE64_SIZE}")
            return None
        
        img_bytes = base64.b64decode(img_b64)

        image = await asyncio.to_thread(_validate_image_data, img_bytes)
        if image is None:
            logger.error("无效的图片数据")
            return None

        w, h = image.size
        logger.debug(f"屏幕数据验证完成: 尺寸 {w}x{h}")
        
        return img_b64
            
    except ValueError as ve:
        logger.error(f"Base64解码错误 (屏幕数据): {ve}")
        return None
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"处理屏幕数据错误: {e}")
        return None


async def analyze_image_with_vision_model(
    image_b64: str,
    max_completion_tokens: int | None = None,
    window_title: str = '',
) -> Optional[str]:
    """
    Analyze an image with the vision model

    Args:
        image_b64: base64 of the image (without the data: prefix)
        max_completion_tokens: maximum output tokens; None takes the
            config.VISION_ANALYSIS_MAX_TOKENS default
        window_title: optional window title; when given it is added to the prompt to enrich context

    Returns: the image description text, or None on failure
    """
    if max_completion_tokens is None:
        from config import VISION_ANALYSIS_MAX_TOKENS
        max_completion_tokens = VISION_ANALYSIS_MAX_TOKENS
    try:
        from utils.config_manager import get_config_manager
        
        config_manager = get_config_manager()
        api_config = await config_manager.aget_model_api_config('vision')
        
        vision_model = api_config['model']
        vision_api_key = api_config['api_key']
        vision_base_url = api_config['base_url']
        
        if not vision_model:
            logger.warning("VISION_MODEL not configured, skipping image analysis")
            return None
        
        if not vision_api_key:
            logger.warning("Vision API key not configured, skipping image analysis")
            return None
        
        if api_config['is_custom']:
            logger.info(f"🖼️ Using custom VISION_MODEL ({vision_model}) to analyze image")
        else:
            logger.info(f"🖼️ Using VISION_MODEL ({vision_model}) to analyze image")

        from config.prompts.prompts_sys import (
            _loc, VISION_WATERMARK,
            VISION_SYSTEM_WITH_TITLE, VISION_SYSTEM_NO_TITLE,
            VISION_USER_WITH_TITLE, VISION_USER_NO_TITLE,
            get_avatar_annotation_ignore_hint,
        )
        from utils.language_utils import get_global_language
        lang = get_global_language()

        ignore_hint = get_avatar_annotation_ignore_hint(lang)
        if window_title:
            system_content = VISION_WATERMARK + _loc(VISION_SYSTEM_WITH_TITLE, lang) + ' ' + ignore_hint
            user_text = _loc(VISION_USER_WITH_TITLE, lang).format(window_title=window_title)
        else:
            system_content = VISION_WATERMARK + _loc(VISION_SYSTEM_NO_TITLE, lang) + ' ' + ignore_hint
            user_text = _loc(VISION_USER_NO_TITLE, lang)

        set_call_type("vision")
        llm = await create_chat_llm_async(
            model=vision_model,
            base_url=vision_base_url or None,
            api_key=vision_api_key,
            max_retries=0,
            max_completion_tokens=max_completion_tokens,
            timeout=30,  # hang-guard for vision/screenshot analysis
            provider_type=api_config.get('provider_type'),
        )
        messages = [
            {
                "role": "system",
                "content": system_content
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        }
                    },
                    {
                        "type": "text",
                        "text": user_text
                    }
                ]
            }
        ]
        async with llm:
            result = await llm.ainvoke(messages)

        if result and result.content and result.content.strip():
            logger.info("✅ Image analysis complete")
            return result.content.strip()

        logger.warning("Vision model returned empty result")
        return None
        
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception(f"Vision model analysis failed: {e}")
        return None


async def analyze_screenshot_from_data_url(data_url: str, window_title: str = '') -> Optional[str]:
    """
    Analyze a screenshot DataURL sent by the frontend
    Only JPEG is supported; other formats are converted to JPEG automatically
    """
    try:
        if not data_url.startswith('data:image/'):
            logger.error(f"无效的DataURL格式: {data_url[:100]}...")
            return None
        
        if ',' not in data_url:
            logger.error("无效的DataURL格式: 缺少base64分隔符")
            return None
        
        _, base64_data = data_url.split(',', 1)
        
        if not base64_data:
            logger.error("无效的DataURL格式: 缺少base64数据部分")
            return None
        
        if len(base64_data) > MAX_BASE64_SIZE:
            logger.error(f"截图数据过大: {len(base64_data)} 字节")
            return None
        
        # 验证图片有效性并转换为JPEG
        try:
            image_bytes = base64.b64decode(base64_data)
            image = await asyncio.to_thread(_validate_image_data, image_bytes)
            if image is None:
                logger.error("无效的图片数据")
                return None

            # 统一压缩为 JPEG（含 resize）
            if image.mode in ('RGBA', 'LA', 'P'):
                image = image.convert('RGB')
            orig_w, orig_h = image.size
            jpg_bytes = await asyncio.to_thread(
                compress_screenshot,
                image,
                target_h=COMPRESS_TARGET_HEIGHT,
                quality=COMPRESS_JPEG_QUALITY,
            )
            base64_data = base64.b64encode(jpg_bytes).decode('utf-8')
            new_size = len(jpg_bytes)
            logger.info(f"截图验证成功: {orig_w}x{orig_h} → 压缩后 {new_size//1024}KB")
        except Exception as e:
            logger.error(f"图片数据解码/验证失败: {e}")
            return None
        
        # 调用视觉模型分析（只使用JPEG）
        description = await analyze_image_with_vision_model(base64_data, window_title=window_title)
        
        if description:
            # AI 截图分析结果（描述用户屏幕内容）不写 logger
            logger.info(f"AI截图分析成功 (description_len={len(description)})")
            print(f"AI截图分析: {description[:100]}...")
        else:
            logger.info("AI截图分析失败")
        
        return description
            
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception(f"分析截图DataURL失败: {e}")
        return None


# ============================================================================
# Avatar annotation overlay — 在截图上叠加 Avatar 文字注解
# ============================================================================

from config.prompts.prompts_sys import AVATAR_ANNOTATION_TEXT as _AVATAR_ANNOTATION_I18N

# Lazy-loaded CJK font cache
_avatar_font_cache: Dict[int, ImageFont.FreeTypeFont] = {}
_avatar_font_path: Optional[str] = None
_avatar_font_searched: bool = False


def _find_cjk_font() -> Optional[str]:
    """Search for a suitable CJK font on the system."""
    global _avatar_font_path, _avatar_font_searched
    if _avatar_font_searched:
        return _avatar_font_path
    _avatar_font_searched = True

    candidates = []
    if sys.platform == 'darwin':
        candidates = [
            '/System/Library/Fonts/PingFang.ttc',
            '/System/Library/Fonts/STHeiti Light.ttc',
            '/Library/Fonts/Arial Unicode.ttf',
        ]
    elif sys.platform == 'win32':
        candidates = [
            r'C:\Windows\Fonts\msyh.ttc',
            r'C:\Windows\Fonts\simhei.ttf',
            r'C:\Windows\Fonts\meiryo.ttc',
        ]
    else:
        candidates = [
            '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        ]

    import os
    for path in candidates:
        if os.path.isfile(path):
            _avatar_font_path = path
            logger.info(f"[avatar-annotation] 找到字体: {path}")
            return path

    logger.warning("[avatar-annotation] 未找到 CJK 字体，将使用 PIL 默认字体")
    return None


def _get_avatar_font(size: int) -> ImageFont.FreeTypeFont:
    """Get or create a font at the given size, with caching."""
    if size in _avatar_font_cache:
        return _avatar_font_cache[size]

    font_path = _find_cjk_font()
    try:
        if font_path:
            font = ImageFont.truetype(font_path, size)
        else:
            font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    _avatar_font_cache[size] = font
    return font


def overlay_avatar_annotation(
    image_b64: str,
    avatar_position: Optional[Dict] = None,
    lanlan_name: str = '',
    language: str = 'zh',
) -> str:
    """
    Overlay a text annotation on the screenshot's Avatar area; returns a new base64 string (without the data: prefix).

    Parameters:
        image_b64:       plain base64-encoded JPEG (without the data:image/... prefix)
        avatar_position: normalized coordinates from the frontend {centerX, centerY, width, height}, range 0-1
        lanlan_name:     character name, used to fill the text template
        language:        language code ('zh', 'zh-CN', 'zh-TW', 'en', 'ja', 'ko', 'ru')

    Returns:
        The overlaid base64 string (without prefix); returns the original image_b64 when overlay is impossible
    """
    if not avatar_position or not lanlan_name:
        return image_b64

    cx = avatar_position.get('centerX')
    cy = avatar_position.get('centerY')
    if cx is None or cy is None:
        return image_b64

    # 归一化坐标校验：超出 [0,1] 说明 Avatar 不在截图可见区域
    try:
        cx = float(cx)
        cy = float(cy)
    except (TypeError, ValueError):
        return image_b64
    if not (0.0 <= cx <= 1.0 and 0.0 <= cy <= 1.0):
        return image_b64

    try:
        img_bytes = base64.b64decode(image_b64)
        img = Image.open(BytesIO(img_bytes))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        iw, ih = img.size

        # 计算 Avatar 中心点在图片上的像素坐标
        px = int(cx * iw)
        py = int(cy * ih)

        # 自适应字号：基于图片高度，但限制范围
        font_size = max(12, min(28, int(ih * 0.022)))
        font = _get_avatar_font(font_size)

        # 获取 i18n 文字
        tpl = _AVATAR_ANNOTATION_I18N.get(language) or _AVATAR_ANNOTATION_I18N.get(language.split('-')[0]) or _AVATAR_ANNOTATION_I18N['en']
        lines = [t.format(name=lanlan_name) for t in tpl]

        draw = ImageDraw.Draw(img)

        # 测量每行文字尺寸
        line_gap = max(2, font_size // 4)
        line_metrics = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_metrics.append((bbox[2] - bbox[0], bbox[3] - bbox[1]))
        total_tw = max(m[0] for m in line_metrics)
        total_th = sum(m[1] for m in line_metrics) + line_gap * (len(lines) - 1)

        # 文字放在 Avatar 中心（不再向下偏移到身体区域，否则显得太靠下）
        text_cx = px
        text_cy = py

        # 背景矩形（半透明）
        pad_x = max(6, font_size // 2)
        pad_y = max(3, font_size // 4)
        bg_x1 = text_cx - total_tw // 2 - pad_x
        bg_y1 = text_cy - total_th // 2 - pad_y
        bg_x2 = text_cx + total_tw // 2 + pad_x
        bg_y2 = text_cy + total_th // 2 + pad_y

        # Clamp to image bounds
        if bg_x1 < 0:
            shift = -bg_x1
            bg_x1 += shift
            bg_x2 += shift
            text_cx += shift
        if bg_x2 > iw:
            shift = bg_x2 - iw
            bg_x1 -= shift
            bg_x2 -= shift
            text_cx -= shift
        if bg_y1 < 0:
            shift = -bg_y1
            bg_y1 += shift
            bg_y2 += shift
            text_cy += shift
        if bg_y2 > ih:
            shift = bg_y2 - ih
            bg_y1 -= shift
            bg_y2 -= shift
            text_cy -= shift

        # 绘制半透明背景
        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rounded_rectangle(
            [bg_x1, bg_y1, bg_x2, bg_y2],
            radius=max(3, font_size // 3),
            fill=(0, 0, 0, 140),
        )
        img = img.convert('RGBA')
        img = Image.alpha_composite(img, overlay)
        img = img.convert('RGB')

        # 绘制文字（白色）
        draw = ImageDraw.Draw(img)
        y_cur = text_cy - total_th // 2
        for i, line in enumerate(lines):
            tw, th = line_metrics[i]
            draw.text((text_cx - tw // 2, y_cur), line, fill=(255, 255, 255), font=font)
            y_cur += th + line_gap

        # 编码回 JPEG base64
        buf = BytesIO()
        img.save(buf, format='JPEG', quality=COMPRESS_JPEG_QUALITY, optimize=True)
        result_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
        return result_b64

    except Exception as e:
        logger.warning(f"[avatar-annotation] 叠加失败，返回原始截图: {e}")
        return image_b64
