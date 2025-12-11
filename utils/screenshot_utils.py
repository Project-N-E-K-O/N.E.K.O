"""
截图分析工具库
提供截图分析功能，包括前端浏览器发送的截图和屏幕分享数据流处理
"""
import os
import base64
import logging
import tempfile
from typing import Optional, Tuple
import asyncio
from io import BytesIO
from PIL import Image

logger = logging.getLogger(__name__)

class ScreenshotUtils:
    """截图和屏幕分享工具类"""
    
    def __init__(self):
        # 不再需要截图目录，因为现在使用前端截图
        pass
    
    async def get_screenshot_description(self, screenshot_path: str) -> Optional[str]:
        """
        获取屏幕截图的文字描述
        完全依赖AI服务分析截图内容
        
        返回: 截图内容的文字描述，如果AI分析失败则返回None
        """
        try:
            logger.info(f"开始分析截图: {screenshot_path}")
            if not os.path.exists(screenshot_path):
                logger.error(f"截图文件不存在: {screenshot_path}")
                return None
            
            # 完全依赖AI服务分析截图
            description = await self._analyze_screenshot_ai(screenshot_path)
            
            if description:
                logger.info(f"AI截图分析成功: {description[:100]}...")
                return description
            else:
                logger.info("AI截图分析失败，放弃本次主动搭话")
                return None
                
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("截图分析异常:")
            return None

    async def process_screen_data(self, data: str) -> Optional[Tuple[str, bytes]]:
        """
        处理前端发送的屏幕分享数据流
        
        参数:
            data: 前端发送的屏幕数据，格式为 'data:image/jpeg;base64,...'
        
        返回: 包含处理后的base64字符串和原始字节数据的元组，如果处理失败则返回None
        """
        try:
            if isinstance(data, str) and data.startswith('data:image/jpeg;base64,'):
                img_data = data.split(',')[1]
                img_bytes = base64.b64decode(img_data)
                
                # Resize to 480p (height=480, keep aspect ratio)
                image = Image.open(BytesIO(img_bytes))
                w, h = image.size
                new_h = 480
                new_w = int(w * (new_h / h))
                image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
                
                buffer = BytesIO()
                image.save(buffer, format='JPEG')
                buffer.seek(0)
                resized_bytes = buffer.read()
                resized_b64 = base64.b64encode(resized_bytes).decode('utf-8')
                
                logger.info(f"屏幕数据处理完成: 原始尺寸 {w}x{h} -> 调整后 {new_w}x{new_h}")
                return resized_b64, img_bytes
            else:
                logger.error(f"无效的屏幕数据格式")
                return None
                
        except ValueError as ve:
            logger.error(f"Base64解码错误 (屏幕数据): {ve}")
            return None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"处理屏幕数据错误: {e}")
            return None
    
    async def _analyze_screenshot_ai(self, screenshot_path: str) -> Optional[str]:
        """使用AI服务分析截图（集成OpenAI Vision API）"""
        try:
            logger.info("开始AI截图分析流程")
            
            # 检查是否配置了API，根据核心API和辅助API厂商选择视觉模型
            from utils.config_manager import get_config_manager
            
            config_manager = get_config_manager()
            core_config = config_manager.get_core_config()
            
            # 获取核心API和辅助API配置
            core_api = core_config.get('CORE_API_TYPE', 'qwen')
            assist_api = core_config.get('assistApi', 'qwen')
            enable_custom_api = core_config.get('ENABLE_CUSTOM_API', False)
            
            logger.info(f"配置信息 - 核心API: {core_api}, 辅助API: {assist_api}, 自定义API: {enable_custom_api}")
            
            # 完全分离两种情况：
            # 1. 开启自定义API：只使用自定义配置，不完整时直接失败
            # 2. 未开启自定义API：只使用辅助API和核心API的默认配置
            
            if enable_custom_api:
                logger.info("检测到自定义API已启用，尝试使用自定义视觉模型配置")
                
                # 开启自定义API：只使用自定义配置
                vision_api_key = core_config.get('VISION_MODEL_API_KEY')
                vision_base_url = core_config.get('VISION_MODEL_URL')
                vision_model = core_config.get('VISION_MODEL')
                
                logger.info(f"自定义API配置 - 密钥配置: {bool(vision_api_key)}, 端点: {vision_base_url}, 模型: {vision_model}")
                
                if vision_api_key and vision_base_url and vision_model:
                    logger.info("使用自定义API视觉模型配置")
                else:
                    logger.info("自定义API视觉模型配置不完整，跳过AI分析")
                    return None
            else:
                # 未开启自定义API：使用全局配置的默认设置
                logger.info("自定义API未启用，使用全局配置的默认视觉模型配置")
                
                # 从全局配置中获取默认的视觉模型设置
                from utils.api_config_loader import get_assist_api_key_fields, get_assist_api_profiles
                from config import DEFAULT_VISION_MODEL
                
                # 获取辅助API配置文件
                assist_api_profiles = get_assist_api_profiles()
                assist_profile = assist_api_profiles.get(assist_api, assist_api_profiles.get('qwen'))
                
                # 获取辅助API对应的API密钥字段名
                assist_api_key_fields = get_assist_api_key_fields()
                key_field = assist_api_key_fields.get(assist_api, 'ASSIST_API_KEY_QWEN')
                vision_api_key = core_config.get(key_field) or core_config.get('CORE_API_KEY')
                
                # 从辅助API配置中获取URL（而不是硬编码OPENROUTER_URL）
                vision_base_url = assist_profile.get('OPENROUTER_URL')
                
                # 从全局配置或辅助API配置中获取默认的视觉模型
                vision_model = core_config.get('VISION_MODEL', DEFAULT_VISION_MODEL)
                if not vision_model or vision_model == DEFAULT_VISION_MODEL:
                    vision_model = assist_profile.get('VISION_MODEL', DEFAULT_VISION_MODEL)
                
                logger.info(f"辅助API配置 - 密钥字段: {key_field}, 模型: {vision_model}, URL: {vision_base_url}")
            
            logger.info(f"最终配置 - 密钥配置: {bool(vision_api_key)}, 模型: {vision_model}")
            
            if not vision_api_key:
                logger.info("未配置视觉模型API密钥，跳过AI分析")
                return None
            
            # 使用视觉模型API分析图片
            from openai import AsyncOpenAI
            
            logger.info(f"使用视觉模型: {vision_model}, API端点: {vision_base_url}")
            
            client = AsyncOpenAI(
                api_key=vision_api_key,
                base_url=vision_base_url if vision_base_url else None
            )
            
            # 读取图片文件并转换为base64
            logger.info("读取截图文件并转换为base64")
            with open(screenshot_path, "rb") as image_file:
                image_data = base64.b64encode(image_file.read()).decode('utf-8')
            
            # 构建提示词，让AI简洁描述屏幕内容
            try:
                logger.info("发送AI分析请求...")
                response = await client.chat.completions.create(
                    model=vision_model if vision_model else DEFAULT_VISION_MODEL,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "请直接描述这张屏幕截图的内容，不要添加任何开场白或总结。重点关注：主要应用程序、界面布局、文字内容、颜色主题和可能的活动。用简洁的中文描述。"
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{image_data}"
                                    }
                                }
                            ]
                        }
                    ],
                    max_tokens=800
                )
                
                logger.info(f"AI分析请求完成，响应状态: {response is not None}")
                
                if response and response.choices and len(response.choices) > 0:
                    description = response.choices[0].message.content
                    logger.info(f"AI分析返回内容长度: {len(description) if description else 0}")
                    
                    if description and description.strip():
                        logger.info("AI图像分析成功")
                        # 清理可能的重复内容
                        cleaned_description = description.strip()
                        # 移除可能的开场白和总结文本，使用循环处理多个前缀
                        prefixes = ["这张截图显示", "截图显示", "图片显示"]
                        for prefix in prefixes:
                            if cleaned_description.startswith(prefix):
                                cleaned_description = cleaned_description[len(prefix):].strip()
                                break
                        
                        logger.info(f"清理后的描述: {cleaned_description[:100]}...")
                        return cleaned_description
                    else:
                        logger.warning("AI分析返回空结果")
                        return None
                else:
                    logger.warning("AI分析响应无效")
                    if response:
                        logger.warning(f"响应结构: choices={len(response.choices) if response.choices else 0}")
                    return None
                    
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("AI API调用失败")
                return None
                
        except asyncio.CancelledError:
            raise
        except ImportError as e:
            logger.warning(f"AI分析相关依赖导入失败: {e}")
            return None
        except Exception:
            logger.exception("AI截图分析失败")
            return None


async def analyze_screenshot_from_data_url(data_url: str) -> Optional[str]:
    """分析前端发送的截图DataURL"""
    try:
        # DataURL格式: data:image/png;base64,<base64数据>
        if not data_url.startswith('data:image/'):
            logger.error(f"无效的DataURL格式: {data_url[:100]}...")
            return None
        
        # 验证DataURL格式，确保包含base64分隔符
        if ',' not in data_url:
            logger.error(f"无效的DataURL格式: 缺少base64分隔符 - {data_url[:100]}...")
            return None
        
        # 提取base64数据
        parts = data_url.split(',')
        if len(parts) < 2:
            logger.error(f"无效的DataURL格式: 缺少base64数据部分 - {data_url[:100]}...")
            return None
        
        base64_data = parts[1]
        
        # 解码base64数据
        image_data = base64.b64decode(base64_data)
        
        # 创建临时文件保存截图
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as temp_file:
            temp_file.write(image_data)
            temp_file_path = temp_file.name
        
        try:
            # 使用截图工具分析图片
            screenshot_utils = ScreenshotUtils()
            description = await screenshot_utils.get_screenshot_description(temp_file_path)
            return description
        finally:
            # 清理临时文件
            try:
                os.unlink(temp_file_path)
            except OSError as e:
                logger.warning(f"清理临时截图文件失败: {e}")
                
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception(f"分析截图DataURL失败: {e}")
        return None