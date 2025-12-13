"""
截图分析工具库
提供截图分析功能，包括前端浏览器发送的截图和屏幕分享数据流处理
"""
import base64
import logging
from typing import Optional
import asyncio
from io import BytesIO
from PIL import Image
from openai import AsyncOpenAI
from config import get_extra_body

logger = logging.getLogger(__name__)

# 安全限制：最大图片大小 (10MB，base64编码后约13.3MB)
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024
MAX_BASE64_SIZE = MAX_IMAGE_SIZE_BYTES * 4 // 3 + 100

def _validate_image_data(image_bytes: bytes) -> Optional[Image.Image]:
    """验证图片数据有效性"""
    try:
        image = Image.open(BytesIO(image_bytes))
        image.verify()
        image = Image.open(BytesIO(image_bytes))
        return image
    except Exception as e:
        logger.warning(f"图片验证失败: {e}")
        return None


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
        
        image = _validate_image_data(img_bytes)
        if image is None:
            logger.error("无效的图片数据")
            return None
        
        w, h = image.size
        logger.info(f"屏幕数据验证完成: 尺寸 {w}x{h}")
        
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
    max_tokens: int = 500
) -> Optional[str]:
    """
    使用视觉模型分析图片
    
    参数:
        image_b64: 图片的base64编码（不含data:前缀）
        max_tokens: 最大输出token数，默认 500
        
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

        client = AsyncOpenAI(
            api_key=vision_api_key,
            base_url=vision_base_url if vision_base_url else None
        )
        
        response = await client.chat.completions.create(
            model=vision_model,
            messages = [
                {
                    "role": "system",
                    "content": "你是一个图像描述助手, 请简洁地描述图片中的主要内容、关键细节和你觉得有趣的地方。你的回答不能超过250字。"
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
                            "text": "请描述这张图片的内容。"
                        }
                    ]
                }
            ],
            max_tokens=max_tokens,
            extra_body=get_extra_body(vision_model) or None
        )
        
        if response and response.choices and len(response.choices) > 0:
            description = response.choices[0].message.content
            if description and description.strip():
                logger.info("✅ Image analysis complete")
                return description.strip()
        
        logger.warning("Vision model returned empty result")
        return None
        
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception(f"Vision model analysis failed: {e}")
        return None


async def analyze_screenshot_from_data_url(data_url: str) -> Optional[str]:
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
            image = _validate_image_data(image_bytes)
            if image is None:
                logger.error("无效的图片数据")
                return None
            
            # 如果不是JPEG格式，转换为JPEG
            if not data_url.startswith('data:image/jpeg'):
                logger.info(f"将图片从其他格式转换为JPEG: {image.size[0]}x{image.size[1]}")
                buffer = BytesIO()
                # 确保转换为RGB模式（JPEG不支持透明通道）
                if image.mode in ('RGBA', 'LA', 'P'):
                    image = image.convert('RGB')
                image.save(buffer, format='JPEG', quality=85)
                buffer.seek(0)
                base64_data = base64.b64encode(buffer.read()).decode('utf-8')
            
            logger.info(f"截图验证成功: {image.size[0]}x{image.size[1]}")
        except Exception as e:
            logger.error(f"图片数据解码/验证失败: {e}")
            return None
        
        # 调用视觉模型分析（只使用JPEG）
        description = await analyze_image_with_vision_model(base64_data)
        
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