"""
截图分析工具库
提供截图分析功能，包括前端浏览器发送的截图和屏幕分享数据流处理
"""
import base64
import sys
from typing import Optional, Dict
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type
import asyncio
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from utils.llm_client import create_chat_llm

logger = get_module_logger(__name__)

# 安全限制：最大图片大小 (10MB，base64编码后约13.3MB)
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024
MAX_BASE64_SIZE = MAX_IMAGE_SIZE_BYTES * 4 // 3 + 100

# 截图压缩默认参数（供 computer_use 等模块复用）
COMPRESS_TARGET_HEIGHT = 1080
COMPRESS_JPEG_QUALITY = 75
_LANCZOS = getattr(Image, 'LANCZOS', getattr(Image, 'ANTIALIAS', 1))

LOCAL_MAX_PIXELS = 100_000_000

def _validate_image_data(image_bytes: bytes) -> Optional[Image.Image]:
    """验证图片数据有效性

    先用 verify() 做格式校验, 再重新打开并调用 load() 强制解码全部像素,
    确保图片数据完整且可用于后续处理 (verify 之后的 Image 对象不可再使用).
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
) -> bytes:
    """Resize to *target_h*p (keep aspect ratio) and encode as JPEG."""
    w, h = img.size
    if h > target_h:
        ratio = target_h / h
        img = img.resize((int(w * ratio), target_h), _LANCZOS)
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


async def process_screen_data(data: str) -> Optional[str]:
    """
    处理前端发送的屏幕分享数据流
    前端已统一压缩到720p JPEG，此方法只做验证，不再二次缩放
    
    参数:
        data: 前端发送的屏幕数据，格式为 'data:image/jpeg;base64,...'
    
    返回: 验证后的base64字符串（不含data:前缀），如果验证失败则返回None
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
    max_tokens: int = 500,
    window_title: str = '',
) -> Optional[str]:
    """
    使用视觉模型分析图片
    
    参数:
        image_b64: 图片的base64编码（不含data:前缀）
        max_tokens: 最大输出token数，默认 500
        window_title: 可选的窗口标题，提供时会加入提示词以丰富上下文
        
    返回: 图片描述文本，失败则返回 None
    """
    try:
        from utils.config_manager import get_config_manager
        
        config_manager = get_config_manager()
        api_config = config_manager.get_model_api_config('vision')
        
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

        from config.prompts_sys import (
            _loc, VISION_WATERMARK,
            VISION_SYSTEM_WITH_TITLE, VISION_SYSTEM_NO_TITLE,
            VISION_USER_WITH_TITLE, VISION_USER_NO_TITLE,
        )
        from utils.language_utils import get_global_language
        lang = get_global_language()

        if window_title:
            system_content = VISION_WATERMARK + _loc(VISION_SYSTEM_WITH_TITLE, lang)
            user_text = _loc(VISION_USER_WITH_TITLE, lang).format(window_title=window_title)
        else:
            system_content = VISION_WATERMARK + _loc(VISION_SYSTEM_NO_TITLE, lang)
            user_text = _loc(VISION_USER_NO_TITLE, lang)

        set_call_type("vision")
        llm = create_chat_llm(
            model=vision_model,
            base_url=vision_base_url or None,
            api_key=vision_api_key,
            max_retries=0,
            max_completion_tokens=max_tokens,
            temperature=0,
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
    分析前端发送的截图DataURL
    只支持JPEG格式，其他格式会自动转换为JPEG
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
            logger.info(f"AI截图分析成功: {description[:100]}...")
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

from config.prompts_sys import AVATAR_ANNOTATION_TEXT as _AVATAR_ANNOTATION_I18N

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
    在截图的 Avatar 区域叠加文字注解，返回新的 base64 字符串（不含 data: 前缀）。

    Parameters:
        image_b64:       纯 base64 编码的 JPEG 图片（不含 data:image/... 前缀）
        avatar_position: 前端传来的归一化坐标 {centerX, centerY, width, height}，值域 0-1
        lanlan_name:     角色名称，用于填充文字模板
        language:        语言代码 ('zh', 'zh-CN', 'zh-TW', 'en', 'ja', 'ko', 'ru')

    Returns:
        叠加后的 base64 字符串（不含前缀），如果无法叠加则返回原始 image_b64
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
        model_h = int(avatar_position.get('height', 0.3) * ih)

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

        # 文字放在 Avatar 中心偏下（模型身体区域）
        text_cx = px
        text_cy = py + int(model_h * 0.15)

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