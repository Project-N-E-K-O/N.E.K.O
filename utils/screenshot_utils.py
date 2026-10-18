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

# Security limit: maximum image size (10MB; about 13.3MB after base64 encoding)
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024
MAX_BASE64_SIZE = MAX_IMAGE_SIZE_BYTES * 4 // 3 + 100

# Default screenshot compression settings: aligned with frontend manual screenshots / screen sharing (720p, JPEG quality 80).
# The frontend already reduces frames sent to the backend to 720p. Keep vision analysis and backend pyautogui fallback
# at the same setting if they compress again, avoiding a 720p-versus-1080p mismatch.
COMPRESS_TARGET_HEIGHT = 720
COMPRESS_JPEG_QUALITY = 80

# Maximum width of images sent to the model. 1280 is not a new number: frontend screenshots in static/app/app-state.js
# already use MAX_SCREENSHOT_WIDTH: 1280, so normalize the backend to the same value and keep both ends
# on the same convention instead of allowing 1280 on the frontend and unlimited width on the backend.
# Limiting height alone is insufficient: compress_screenshot's height gate does not trigger for a very wide image such as 16000x400
# (its height already complies); 5120x1440 would only shrink to 2560x720, leaving the width at 2560. Set both width and height
# limits to enforce a single size tier.
MODEL_IMAGE_MAX_WIDTH = 1280

# Base64 prefix length needed only to inspect the image header. PNG / JPEG dimensions are at the beginning of the stream,
# so 64 KiB is ample; formats Pillow cannot parse from a truncated stream (WebP) fall back to full decoding.
# The same approach is already used by _inline_image_data_url_mime in app/main_server/character_runtime.py;
# it measured ~0.1 ms fixed overhead, while decoding an 8 MiB image just to read the header took ~14 ms.
#
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
        # First pass: lightweight format validation
        probe = Image.open(BytesIO(image_bytes))
        probe.verify()  # This object cannot be reused after verify

        # Second pass: fully decode pixels to ensure the data is usable
        image = Image.open(BytesIO(image_bytes))

        # Pixel-count safety check (prevents huge images from exhausting memory)
        max_pixels = min(Image.MAX_IMAGE_PIXELS or LOCAL_MAX_PIXELS, LOCAL_MAX_PIXELS)
        w, h = image.size
        if w * h > max_pixels:
            raise ValueError(
                f"Image too large: {w}x{h} = {w * h} pixels, limit {max_pixels}"
            )

        image.load()  # Force decoding to expose truncation/corruption early
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
        # Keep the byte-for-byte equivalent of the old implementation (calculate ratio first, then multiply); do not change it to int(w * target_h / h)
        # — the floating-point evaluation order differs, so some dimensions can shift by one pixel, and all existing callers here
        # require the result to remain unchanged.
        ratio = target_h / h
        new_w, new_h = int(w * ratio), target_h
    if max_w is not None and max_w > 0 and new_w > max_w:
        ratio = max_w / new_w
        new_w, new_h = max_w, int(new_h * ratio)
    if (new_w, new_h) != (w, h):
        # Clamp to >=1: an extreme strip (such as 1x1000) can produce a width of 0 when scaled proportionally, which PIL cannot save.
        # The old implementation already failed on this input, so this guard does not change any case that previously produced an image.
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
    # Base64 decoding requires a multiple of 4; align the truncated prefix before passing it to b64decode.
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
                # Report **display** dimensions, not stored dimensions. For photos with EXIF orientation 5–8,
                # the stored matrix and display orientation differ by 90 degrees: a portrait image displayed as 1000x3000
                # may be stored as 3000x1000. If we use stored dimensions to decide "already within the target size,"
                # we measure a shape neither the user nor the model sees — rotating portrait and landscape reverses which edge
                # is compared to each limit.
                #
                # getexif() reads the file header and, like size, does not decode pixels, so this remains
                # O(1) and preserves the cost advantage of prefix probing.
                try:
                    orientation = image.getexif().get(0x0112)
                except Exception:
                    orientation = None
                if orientation in (5, 6, 7, 8):
                    w, h = h, w
        except Exception:
            # The prefix is not long enough for this format header (such as WebP) — try the next candidate, the full payload.
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
        # Apply EXIF orientation before resizing. The order matters: resize changes the pixel matrix,
        # and compress_screenshot does not write EXIF when saving JPEG, so the orientation marker is lost
        # — the model would receive a photo rotated 90 degrees with no way to tell.
        # In practice, 3000x1000 + orientation=6 (displayed as 1000x3000 portrait) becomes
        # a 1280x426 landscape image here, with EXIF=None.
        #
        # exif_transpose bakes orientation into the pixels and removes the marker, so the output is naturally "without EXIF
        # and correctly oriented"; probing it again reads its true shape, so fixed points are preserved.
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
        
        if not vision_api_key and not api_config.get('is_custom'):
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
        
        # Validate the image and convert it to JPEG
        try:
            image_bytes = base64.b64decode(base64_data)
            image = await asyncio.to_thread(_validate_image_data, image_bytes)
            if image is None:
                logger.error("无效的图片数据")
                return None

            # Compress to JPEG consistently (including resize)
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
        
        # Call the vision model (JPEG only)
        description = await analyze_image_with_vision_model(base64_data, window_title=window_title)
        
        if description:
            # Do not log AI screenshot analysis results (descriptions of user screen contents)
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
# Avatar annotation overlay — draw Avatar text annotations over the screenshot
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

    # Validate normalized coordinates: values outside [0,1] mean Avatar is outside the visible screenshot area
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

        # Calculate Avatar center coordinates in image pixels
        px = int(cx * iw)
        py = int(cy * ih)

        # Adaptive font size based on image height, with bounds
        font_size = max(12, min(28, int(ih * 0.022)))
        font = _get_avatar_font(font_size)

        # Get i18n text
        tpl = _AVATAR_ANNOTATION_I18N.get(language) or _AVATAR_ANNOTATION_I18N.get(language.split('-')[0]) or _AVATAR_ANNOTATION_I18N['en']
        lines = [t.format(name=lanlan_name) for t in tpl]

        draw = ImageDraw.Draw(img)

        # Measure each line of text
        line_gap = max(2, font_size // 4)
        line_metrics = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_metrics.append((bbox[2] - bbox[0], bbox[3] - bbox[1]))
        total_tw = max(m[0] for m in line_metrics)
        total_th = sum(m[1] for m in line_metrics) + line_gap * (len(lines) - 1)

        # Place text at Avatar center (do not offset downward into the body area, which looks too low)
        text_cx = px
        text_cy = py

        # Background rectangle (semi-transparent)
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

        # Draw semi-transparent background
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

        # Draw text (white)
        draw = ImageDraw.Draw(img)
        y_cur = text_cy - total_th // 2
        for i, line in enumerate(lines):
            tw, th = line_metrics[i]
            draw.text((text_cx - tw // 2, y_cur), line, fill=(255, 255, 255), font=font)
            y_cur += th + line_gap

        # Encode back to JPEG base64
        buf = BytesIO()
        img.save(buf, format='JPEG', quality=COMPRESS_JPEG_QUALITY, optimize=True)
        result_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
        return result_b64

    except Exception as e:
        logger.warning(f"[avatar-annotation] 叠加失败，返回原始截图: {e}")
        return image_b64
