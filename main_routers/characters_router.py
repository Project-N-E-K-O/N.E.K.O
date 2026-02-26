# -*- coding: utf-8 -*-
"""
Characters Router

Handles character (catgirl) management endpoints including:
- Character CRUD operations
- Voice settings
- Microphone settings
"""

import json
import io
import os
import asyncio
import copy
import base64
from datetime import datetime
import pathlib
import wave

from fastapi import APIRouter, Request, File, UploadFile, Form
from fastapi.responses import JSONResponse
import httpx
import dashscope
from dashscope.audio.tts_v2 import VoiceEnrollmentService, SpeechSynthesizer

from .shared_state import get_config_manager, get_session_manager, get_initialize_character_data
from main_logic.tts_client import get_custom_tts_voices, CustomTTSVoiceFetchError
from utils.frontend_utils import find_models, find_model_directory, is_user_imported_model
from utils.language_utils import normalize_language_code
from utils.logger_config import get_module_logger
from config import MEMORY_SERVER_PORT, TFLINK_UPLOAD_URL

router = APIRouter(prefix="/api/characters", tags=["characters"])
logger = get_module_logger(__name__, "Main")


PROFILE_NAME_MAX_UNITS = 20


def _profile_name_units(name: str) -> int:
    # 计数规则与前端保持一致：ASCII(<=0x7F) 计 1，其它字符计 2
    return sum(1 if ord(ch) <= 0x7F else 2 for ch in name)


def _validate_profile_name(name: str) -> str | None:
    if name is None:
        return '档案名为必填项'
    name = str(name).strip()
    if not name:
        return '档案名为必填项'
    if _profile_name_units(name) > PROFILE_NAME_MAX_UNITS:
        return f'档案名长度不能超过{PROFILE_NAME_MAX_UNITS}单位（ASCII=1，其他=2；PROFILE_NAME_MAX_UNITS={PROFILE_NAME_MAX_UNITS}）'
    return None


async def send_reload_page_notice(session, message_text: str = "语音已更新，页面即将刷新"):
    """
    发送页面刷新通知给前端（通过 WebSocket）
    
    Args:
        session: LLMSessionManager 实例
        message_text: 要发送的消息文本（会被自动翻译）
    
    Returns:
        bool: 是否成功发送
    """
    if not session or not session.websocket:
        return False
    
    # 检查 WebSocket 连接状态
    if not hasattr(session.websocket, 'client_state') or session.websocket.client_state != session.websocket.client_state.CONNECTED:
        return False
    
    try:
        # 翻译消息
        translated_message = await session.translate_if_needed(message_text)
        await session.websocket.send_text(json.dumps({
            "type": "reload_page",
            "message": translated_message
        }))
        logger.info(f"已通知前端刷新页面: {translated_message}")
        return True
    except Exception as e:
        logger.warning(f"通知前端刷新页面失败: {e}")
        return False


@router.get('/')
async def get_characters(request: Request):
    """获取角色数据，支持根据用户语言自动翻译人设"""
    _config_manager = get_config_manager()
    # 创建深拷贝，避免修改原始配置数据
    characters_data = copy.deepcopy(_config_manager.load_characters())
    
    # 尝试从请求参数或请求头获取用户语言
    user_language = request.query_params.get('language')
    if not user_language:
        accept_lang = request.headers.get('Accept-Language', 'zh-CN')
        # Accept-Language 可能包含多个语言，取第一个
        user_language = accept_lang.split(',')[0].split(';')[0].strip()
    # 使用公共函数归一化语言代码
    user_language = normalize_language_code(user_language, format='full')
    
    # 如果语言是中文，不需要翻译
    if user_language == 'zh-CN':
        return JSONResponse(content=characters_data)
    
    # 需要翻译：翻译人设数据（在深拷贝上进行，不影响原始配置）
    try:
        from utils.language_utils import get_translation_service
        translation_service = get_translation_service(_config_manager)
        
        # 翻译主人数据
        if '主人' in characters_data and isinstance(characters_data['主人'], dict):
            characters_data['主人'] = await translation_service.translate_dict(
                characters_data['主人'],
                user_language,
                fields_to_translate=['档案名', '昵称']
            )
        
        # 翻译猫娘数据（并行翻译以提升性能）
        if '猫娘' in characters_data and isinstance(characters_data['猫娘'], dict):
            async def translate_catgirl(name, data):
                if isinstance(data, dict):
                    return name, await translation_service.translate_dict(
                        data, user_language,
                        fields_to_translate=['档案名', '昵称', '性别']  # 注意：不翻译 system_prompt
                    )
                return name, data
            
            results = await asyncio.gather(*[
                translate_catgirl(name, data)
                for name, data in characters_data['猫娘'].items()
            ])
            characters_data['猫娘'] = dict(results)
        
        return JSONResponse(content=characters_data)
    except Exception as e:
        logger.error(f"翻译人设数据失败: {e}，返回原始数据")
        return JSONResponse(content=characters_data)


@router.get('/current_live2d_model')
async def get_current_live2d_model(catgirl_name: str = "", item_id: str = ""):
    """获取指定角色或当前角色的Live2D模型信息
    
    Args:
        catgirl_name: 角色名称
        item_id: 可选的物品ID，用于直接指定模型
    """
    try:
        _config_manager = get_config_manager()
        characters = _config_manager.load_characters()
        
        # 如果没有指定角色名称，使用当前猫娘
        if not catgirl_name:
            catgirl_name = characters.get('当前猫娘', '')
        
        # 查找指定角色的Live2D模型
        live2d_model_name = None
        model_info = None
        
        # 首先尝试通过item_id查找模型
        if item_id:
            try:
                logger.debug(f"尝试通过item_id {item_id} 查找模型")
                # 获取所有模型
                all_models = find_models()
                # 查找匹配item_id的模型
                matching_model = next((m for m in all_models if m.get('item_id') == item_id), None)
                
                if matching_model:
                    logger.debug(f"通过item_id找到模型: {matching_model['name']}")
                    # 复制模型信息
                    model_info = matching_model.copy()
                    live2d_model_name = model_info['name']
            except Exception as e:
                logger.warning(f"通过item_id查找模型失败: {e}")
        
        # 如果没有通过item_id找到模型，再通过角色名称查找
        if not model_info and catgirl_name:
            # 在猫娘列表中查找
            if '猫娘' in characters and catgirl_name in characters['猫娘']:
                catgirl_data = characters['猫娘'][catgirl_name]
                live2d_model_name = catgirl_data.get('live2d')
                
                # 检查是否有保存的item_id
                saved_item_id = catgirl_data.get('live2d_item_id')
                if saved_item_id:
                    logger.debug(f"发现角色 {catgirl_name} 保存的item_id: {saved_item_id}")
                    try:
                        # 尝试通过保存的item_id查找模型
                        all_models = find_models()
                        matching_model = next((m for m in all_models if m.get('item_id') == saved_item_id), None)
                        if matching_model:
                            logger.debug(f"通过保存的item_id找到模型: {matching_model['name']}")
                            model_info = matching_model.copy()
                            live2d_model_name = model_info['name']
                    except Exception as e:
                        logger.warning(f"通过保存的item_id查找模型失败: {e}")
        
        # 如果找到了模型名称，获取模型信息
        if live2d_model_name:
            try:
                # 先从完整的模型列表中查找，这样可以获取到item_id等完整信息
                all_models = find_models()
                
                # 同时获取工坊模型列表，确保能找到工坊模型
                try:
                    from .workshop_router import get_subscribed_workshop_items
                    workshop_result = await get_subscribed_workshop_items()
                    if isinstance(workshop_result, dict) and workshop_result.get('success', False):
                        for item in workshop_result.get('items', []):
                            installed_folder = item.get('installedFolder')
                            workshop_item_id = item.get('publishedFileId')
                            if installed_folder and os.path.exists(installed_folder) and os.path.isdir(installed_folder) and workshop_item_id:
                                # 检查安装目录下是否有.model3.json文件
                                for filename in os.listdir(installed_folder):
                                    if filename.endswith('.model3.json'):
                                        model_name = os.path.splitext(os.path.splitext(filename)[0])[0]
                                        if model_name not in [m['name'] for m in all_models]:
                                            all_models.append({
                                                'name': model_name,
                                                'path': f'/workshop/{workshop_item_id}/{filename}',
                                                'source': 'steam_workshop',
                                                'item_id': workshop_item_id
                                            })
                                # 检查子目录
                                for subdir in os.listdir(installed_folder):
                                    subdir_path = os.path.join(installed_folder, subdir)
                                    if os.path.isdir(subdir_path):
                                        model_name = subdir
                                        json_file = os.path.join(subdir_path, f'{model_name}.model3.json')
                                        if os.path.exists(json_file):
                                            if model_name not in [m['name'] for m in all_models]:
                                                all_models.append({
                                                    'name': model_name,
                                                    'path': f'/workshop/{workshop_item_id}/{model_name}/{model_name}.model3.json',
                                                    'source': 'steam_workshop',
                                                    'item_id': workshop_item_id
                                                })
                except Exception as we:
                    logger.debug(f"获取工坊模型列表时出错（非关键）: {we}")
                
                # 查找匹配的模型
                matching_model = next((m for m in all_models if m['name'] == live2d_model_name), None)
                
                if matching_model:
                    # 使用完整的模型信息，包含item_id
                    model_info = matching_model.copy()
                    logger.debug(f"从完整模型列表获取模型信息: {model_info}")
                else:
                    # 如果在完整列表中找不到，回退到原来的逻辑
                    model_dir, url_prefix = find_model_directory(live2d_model_name)
                    if model_dir and os.path.exists(model_dir):
                        # 查找模型配置文件
                        model_files = [f for f in os.listdir(model_dir) if f.endswith('.model3.json')]
                        if model_files:
                            model_file = model_files[0]
                            
                            # 使用保存的item_id构建model_path，从之前的逻辑中获取saved_item_id
                            saved_item_id = catgirl_data.get('live2d_item_id', '') if 'catgirl_data' in locals() else ''
                            
                            # 如果有保存的item_id，使用它构建路径
                            if saved_item_id:
                                model_path = f'{url_prefix}/{saved_item_id}/{model_file}'
                                logger.debug(f"使用保存的item_id构建模型路径: {model_path}")
                            else:
                                # 原始路径构建逻辑
                                model_path = f'{url_prefix}/{live2d_model_name}/{model_file}'
                                logger.debug(f"使用模型名称构建路径: {model_path}")
                            
                            model_info = {
                                'name': live2d_model_name,
                                'item_id': saved_item_id,
                                'path': model_path
                            }
            except Exception as e:
                logger.warning(f"获取模型信息失败: {e}")
        
        # 回退机制：如果没有找到模型，使用默认的mao_pro
        if not live2d_model_name or not model_info:
            logger.info(f"猫娘 {catgirl_name} 未设置Live2D模型，回退到默认模型 mao_pro")
            live2d_model_name = 'mao_pro'
            try:
                # 先从完整的模型列表中查找mao_pro
                all_models = find_models()
                matching_model = next((m for m in all_models if m['name'] == 'mao_pro'), None)
                
                if matching_model:
                    model_info = matching_model.copy()
                    model_info['is_fallback'] = True
                else:
                    # 如果找不到，回退到原来的逻辑
                    model_dir, url_prefix = find_model_directory('mao_pro')
                    if model_dir and os.path.exists(model_dir):
                        model_files = [f for f in os.listdir(model_dir) if f.endswith('.model3.json')]
                        if model_files:
                            model_file = model_files[0]
                            model_path = f'{url_prefix}/mao_pro/{model_file}'
                            model_info = {
                                'name': 'mao_pro',
                                'path': model_path,
                                'is_fallback': True  # 标记这是回退模型
                            }
            except Exception as e:
                logger.error(f"获取默认模型mao_pro失败: {e}")
        
        return JSONResponse(content={
            'success': True,
            'catgirl_name': catgirl_name,
            'model_name': live2d_model_name,
            'model_info': model_info
        })
        
    except Exception as e:
        logger.error(f"获取角色Live2D模型失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        })

@router.put('/catgirl/l2d/{name}')
async def update_catgirl_l2d(name: str, request: Request):
    """更新指定猫娘的模型设置（支持Live2D和VRM）"""
    try:
        data = await request.json()
        live2d_model = data.get('live2d')
        vrm_model = data.get('vrm')
        model_type = data.get('model_type', 'live2d')  # 默认为live2d以保持兼容性
        item_id = data.get('item_id')  # 获取可选的item_id
        vrm_animation = data.get('vrm_animation')  # 获取可选的VRM动作
        
        # 根据model_type检查相应的模型字段
        model_type_str = str(model_type).lower() if model_type else 'live2d'
        
        # 【修复】model_type 只允许 {live2d, vrm}，否则 400
        if model_type_str not in ['live2d', 'vrm']:
            return JSONResponse(
                content={
                    'success': False,
                    'error': f'无效的模型类型: {model_type}，只允许 live2d 或 vrm'
                },
                status_code=400
            )
        
        if model_type_str == 'vrm':
            if not vrm_model:
                return JSONResponse(
                    content={
                        'success': False,
                        'error': '未提供VRM模型路径'
                    },
                    status_code=400
                )
            
            # 验证 VRM 模型路径：只允许安全的路径前缀，拒绝 URL 方案和路径遍历
            vrm_model_str = str(vrm_model).strip()
            
            # 检查是否包含 URL 方案
            if '://' in vrm_model_str or vrm_model_str.startswith('data:'):
                return JSONResponse(
                    content={
                        'success': False,
                        'error': 'VRM模型路径不能包含URL方案'
                    },
                    status_code=400
                )
            
            # 检查是否包含路径遍历（..）
            if '..' in vrm_model_str:
                return JSONResponse(
                    content={
                        'success': False,
                        'error': 'VRM模型路径不能包含路径遍历（..）'
                    },
                    status_code=400
                )
            
            # 检查是否以允许的前缀开头
            allowed_prefixes = ['/user_vrm/', '/static/vrm/']
            if not any(vrm_model_str.startswith(prefix) for prefix in allowed_prefixes):
                return JSONResponse(
                    content={
                        'success': False,
                        'error': 'VRM模型路径必须以 /user_vrm/ 或 /static/vrm/ 开头'
                    },
                    status_code=400
                )
            
            # 使用验证后的值
            vrm_model = vrm_model_str
        else:
            if not live2d_model:
                return JSONResponse(
                    content={
                        'success': False,
                        'error': '未提供Live2D模型名称'
                    },
                    status_code=400
                )
        
        # 加载当前角色配置
        _config_manager = get_config_manager()
        characters = _config_manager.load_characters()
        
        # 确保猫娘配置存在
        if '猫娘' not in characters:
            characters['猫娘'] = {}
        
        # 确保指定猫娘的配置存在
        if name not in characters['猫娘']:
            return JSONResponse(
                {'success': False, 'error': '猫娘不存在'}, 
                status_code=404
            )
        
        # 切换模型类型时清理"另一套模型字段"，避免配置残留
        if model_type_str == 'vrm':
            characters['猫娘'][name].pop('live2d', None)
            characters['猫娘'][name].pop('live2d_item_id', None)
            
            # 更新VRM模型设置
            characters['猫娘'][name]['vrm'] = vrm_model
            characters['猫娘'][name]['model_type'] = 'vrm'
            
            # 处理 vrm_animation：支持显式清空（传 null 或空字符串）
            if 'vrm_animation' in data:
                if vrm_animation is None or vrm_animation == '':
                    characters['猫娘'][name].pop('vrm_animation', None)
                    logger.debug(f"已保存角色 {name} 的VRM模型 {vrm_model}，已清空动作")
                else:
                    # 验证 VRM 动画路径：只允许安全的路径前缀，拒绝 URL 方案和路径遍历
                    vrm_animation_str = str(vrm_animation).strip()
                    
                    # 检查是否包含 URL 方案
                    if '://' in vrm_animation_str or vrm_animation_str.startswith('data:'):
                        return JSONResponse(
                            content={
                                'success': False,
                                'error': 'VRM动画路径不能包含URL方案'
                            },
                            status_code=400
                        )
                    
                    # 检查是否包含路径遍历（..）
                    if '..' in vrm_animation_str:
                        return JSONResponse(
                            content={
                                'success': False,
                                'error': 'VRM动画路径不能包含路径遍历（..）'
                            },
                            status_code=400
                        )
                    
                    # 检查是否以允许的前缀开头
                    allowed_animation_prefixes = ['/user_vrm/animation/', '/static/vrm/animation/']
                    if not any(vrm_animation_str.startswith(prefix) for prefix in allowed_animation_prefixes):
                        return JSONResponse(
                            content={
                                'success': False,
                                'error': 'VRM动画路径必须以 /user_vrm/animation/ 或 /static/vrm/animation/ 开头'
                            },
                            status_code=400
                        )
                    
                    # 使用验证后的值
                    characters['猫娘'][name]['vrm_animation'] = vrm_animation_str
                    logger.debug(f"已保存角色 {name} 的VRM模型 {vrm_model} 和动作 {vrm_animation_str}")
            else:
                logger.debug(f"已保存角色 {name} 的VRM模型 {vrm_model}，动作字段未变更")
        else:
            characters['猫娘'][name].pop('vrm', None)
            characters['猫娘'][name].pop('vrm_animation', None)
            characters['猫娘'][name].pop('lighting', None)  # 清理 VRM 打光配置
            
            # 更新Live2D模型设置，同时保存item_id（如果有）
            characters['猫娘'][name]['live2d'] = live2d_model
            characters['猫娘'][name]['model_type'] = 'live2d'
            if item_id:
                characters['猫娘'][name]['live2d_item_id'] = item_id
                logger.debug(f"已保存角色 {name} 的模型 {live2d_model} 和item_id {item_id}")
            else:
                logger.debug(f"已保存角色 {name} 的模型 {live2d_model}")
        
        # 保存配置
        _config_manager.save_characters(characters)
        # 自动重新加载配置
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()
        
        
        if model_type_str == 'vrm':
            message = f'已更新角色 {name} 的VRM模型为 {vrm_model}'
        else:
            message = f'已更新角色 {name} 的Live2D模型为 {live2d_model}'
        
        return JSONResponse(content={
            'success': True,
            'message': message
        })
        
    except Exception as e:
        logger.exception("更新角色模型设置失败")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        })


@router.put('/catgirl/{name}/lighting')
async def update_catgirl_lighting(name: str, request: Request):
    """更新指定猫娘的VRM打光配置
    
    Args:
        name: 角色名称
        request: 请求体包含 lighting (dict) 和可选的 apply_runtime (bool)
                 apply_runtime 也可通过 query param 传递,query param 优先级更高
    """
    try:
        data = await request.json()
        lighting = data.get('lighting')
        
        apply_runtime = data.get('apply_runtime', False)
        query_params = request.query_params
        if 'apply_runtime' in query_params:
            apply_runtime = query_params.get('apply_runtime', '').lower() in ('true', '1', 'yes')

        _config_manager = get_config_manager()
        characters = _config_manager.load_characters()

        if '猫娘' not in characters or name not in characters['猫娘']:
            return JSONResponse(content={
                'success': False,
                'error': '角色不存在'
            }, status_code=404)

        model_type = characters['猫娘'][name].get('model_type', 'live2d')
        # 统一做 .lower() 处理，避免大小写/空值导致误判
        model_type_normalized = str(model_type).lower() if model_type else 'live2d'
        if model_type_normalized != 'vrm':
            logger.warning(f"角色 {name} 不是VRM模型，但仍保存打光配置")
        
        from config import get_default_vrm_lighting
        existing_lighting = characters['猫娘'][name].get('lighting')
        if isinstance(existing_lighting, dict):
            base_lighting = existing_lighting
        else:
            base_lighting = get_default_vrm_lighting()
        
        if not isinstance(lighting, dict):
            return JSONResponse(content={
                'success': False,
                'error': 'lighting 必须是对象'
            }, status_code=400)
        
        lighting = {**base_lighting, **lighting}

        from config import VRM_LIGHTING_RANGES
        lighting_ranges = VRM_LIGHTING_RANGES

        for key, (min_val, max_val) in lighting_ranges.items():
            if key not in lighting:
                return JSONResponse(content={
                    'success': False,
                    'error': f'缺少打光参数: {key}'
                }, status_code=400)

            val = lighting[key]
            if not isinstance(val, (int, float)) or not (min_val <= val <= max_val):
                return JSONResponse(content={
                    'success': False,
                    'error': f'打光参数 {key} 超出范围 ({min_val}-{max_val})'
                }, status_code=400)

        
        characters['猫娘'][name]['lighting'] = {
            key: float(lighting[key]) for key in lighting_ranges.keys()
        }



        logger.info(f"已保存角色 {name} 的打光配置: {characters['猫娘'][name]['lighting']}")

        _config_manager.save_characters(characters)
        
        if apply_runtime:
            initialize_character_data = get_initialize_character_data()
            if initialize_character_data:
                await initialize_character_data()
                logger.info(f"已执行完整配置重载（角色 {name} 的打光配置）")
        else:
            logger.debug("跳过完整配置重载（apply_runtime=False），配置已保存到磁盘，需要刷新页面或调用重载才能生效")

        if apply_runtime:
            message = f'已保存角色 {name} 的打光配置并已应用到运行时'
        else:
            message = f'已保存角色 {name} 的打光配置到磁盘（需要刷新页面或调用重载才能生效）'

        return JSONResponse(content={
            'success': True,
            'message': message,
            'applied_runtime': apply_runtime,
            'needs_reload': not apply_runtime
        })

    except Exception as e:
        logger.error(f"保存打光配置失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        }, status_code=500)



@router.put('/catgirl/voice_id/{name}')
async def update_catgirl_voice_id(name: str, request: Request):
    data = await request.json()
    if not data:
        return JSONResponse({'success': False, 'error': '无数据'}, status_code=400)
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    characters = _config_manager.load_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    if 'voice_id' in data:
        voice_id = data['voice_id']
        # 验证voice_id是否在voice_storage中
        if not _config_manager.validate_voice_id(voice_id):
            voices = _config_manager.get_voices_for_current_api()
            available_voices = list(voices.keys())
            return JSONResponse({
                'success': False, 
                'error': f'voice_id "{voice_id}" 在当前API的音色库中不存在',
                'available_voices': available_voices
            }, status_code=400)
        characters['猫娘'][name]['voice_id'] = voice_id
    _config_manager.save_characters(characters)
    
    # 如果是当前活跃的猫娘，需要先通知前端，再关闭session
    is_current_catgirl = (name == characters.get('当前猫娘', ''))
    session_ended = False
    
    if is_current_catgirl and name in session_manager:
        # 检查是否有活跃的session
        if session_manager[name].is_active:
            logger.info(f"检测到 {name} 的voice_id已更新，准备刷新...")
            
            # 1. 先发送刷新消息（WebSocket还连着）
            await send_reload_page_notice(session_manager[name])
            
            # 2. 立刻关闭session（这会断开WebSocket）
            try:
                await session_manager[name].end_session(by_server=True)
                session_ended = True
                logger.info(f"{name} 的session已结束")
            except Exception as e:
                logger.error(f"结束session时出错: {e}")
    
    # 方案3：条件性重新加载 - 只有当前猫娘才重新加载配置
    if is_current_catgirl:
        # 3. 重新加载配置，让新的voice_id生效
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()
        logger.info("配置已重新加载，新的voice_id已生效")
    else:
        # 不是当前猫娘，跳过重新加载，避免影响当前猫娘的session
        logger.info(f"切换的是其他猫娘 {name} 的音色，跳过重新加载以避免影响当前猫娘的session")
    
    return {"success": True, "session_restarted": session_ended}

@router.get('/catgirl/{name}/voice_mode_status')
async def get_catgirl_voice_mode_status(name: str):
    """检查指定角色是否在语音模式下"""
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    characters = _config_manager.load_characters()
    is_current = characters.get('当前猫娘') == name
    
    if name not in session_manager:
        return JSONResponse({'is_voice_mode': False, 'is_current': is_current, 'is_active': False})
    
    mgr = session_manager[name]
    is_active = mgr.is_active if mgr else False
    
    is_voice_mode = False
    if is_active and mgr:
        # 检查是否是语音模式（通过session类型判断）
        from main_logic.omni_realtime_client import OmniRealtimeClient
        is_voice_mode = mgr.session and isinstance(mgr.session, OmniRealtimeClient)
    
    return JSONResponse({
        'is_voice_mode': is_voice_mode,
        'is_current': is_current,
        'is_active': is_active
    })


@router.post('/catgirl/{old_name}/rename')
async def rename_catgirl(old_name: str, request: Request):
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    data = await request.json()
    new_name = data.get('new_name') if data else None
    if not new_name:
        return JSONResponse({'success': False, 'error': '新档案名不能为空'}, status_code=400)

    new_name = str(new_name).strip()
    err = _validate_profile_name(new_name)
    if err:
        return JSONResponse({'success': False, 'error': err.replace('档案名', '新档案名')}, status_code=400)
    characters = _config_manager.load_characters()
    if old_name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '原猫娘不存在'}, status_code=404)
    if new_name in characters['猫娘']:
        return JSONResponse({'success': False, 'error': '新档案名已存在'}, status_code=400)
    
    # 如果当前猫娘是被重命名的猫娘，需要先保存WebSocket连接并发送通知
    # 必须在 initialize_character_data() 之前发送，因为那个函数会删除旧的 session_manager 条目
    is_current_catgirl = characters.get('当前猫娘') == old_name
    
    # 检查当前角色是否有活跃的语音session
    if is_current_catgirl and old_name in session_manager:
        mgr = session_manager[old_name]
        if mgr.is_active:
            # 检查是否是语音模式（通过session类型判断）
            from main_logic.omni_realtime_client import OmniRealtimeClient
            is_voice_mode = mgr.session and isinstance(mgr.session, OmniRealtimeClient)
            
            if is_voice_mode:
                return JSONResponse({
                    'success': False, 
                    'error': '语音状态下无法修改角色名称，请先停止语音对话后再修改'
                }, status_code=400)
    if is_current_catgirl:
        logger.info(f"开始通知WebSocket客户端：猫娘从 {old_name} 重命名为 {new_name}")
        message = json.dumps({
            "type": "catgirl_switched",
            "new_catgirl": new_name,
            "old_catgirl": old_name
        })
        # 在 initialize_character_data() 之前发送消息，因为之后旧的 session_manager 会被删除
        if old_name in session_manager:
            ws = session_manager[old_name].websocket
            if ws:
                try:
                    await ws.send_text(message)
                    logger.info(f"已向 {old_name} 发送重命名通知")
                except Exception as e:
                    logger.warning(f"发送重命名通知给 {old_name} 失败: {e}")
    
    # 重命名
    characters['猫娘'][new_name] = characters['猫娘'].pop(old_name)
    # 如果当前猫娘是被重命名的猫娘，也需要更新
    if is_current_catgirl:
        characters['当前猫娘'] = new_name
    _config_manager.save_characters(characters)
    # 自动重新加载配置
    initialize_character_data = get_initialize_character_data()
    await initialize_character_data()
    
    return {"success": True}


@router.post('/catgirl/{name}/unregister_voice')
async def unregister_voice(name: str):
    """解除猫娘的声音注册"""
    try:
        _config_manager = get_config_manager()
        characters = _config_manager.load_characters()
        if name not in characters.get('猫娘', {}):
            return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
        
        # 检查是否已有voice_id
        if not characters['猫娘'][name].get('voice_id'):
            return JSONResponse({'success': False, 'error': '该猫娘未注册声音'}, status_code=400)
        
        # 删除voice_id字段
        if 'voice_id' in characters['猫娘'][name]:
            characters['猫娘'][name].pop('voice_id')
        _config_manager.save_characters(characters)
        # 自动重新加载配置
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()
        
        logger.info(f"已解除猫娘 '{name}' 的声音注册")
        return {"success": True, "message": "声音注册已解除"}
        
    except Exception as e:
        logger.error(f"解除声音注册时出错: {e}")
        return JSONResponse({'success': False, 'error': f'解除注册失败: {str(e)}'}, status_code=500)

@router.get('/current_catgirl')
async def get_current_catgirl():
    """获取当前使用的猫娘名称"""
    _config_manager = get_config_manager()
    characters = _config_manager.load_characters()
    current_catgirl = characters.get('当前猫娘', '')
    return JSONResponse(content={'current_catgirl': current_catgirl})

@router.post('/current_catgirl')
async def set_current_catgirl(request: Request):
    """设置当前使用的猫娘"""
    data = await request.json()
    catgirl_name = data.get('catgirl_name', '') if data else ''
    
    if not catgirl_name:
        return JSONResponse({'success': False, 'error': '猫娘名称不能为空'}, status_code=400)
    
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    characters = _config_manager.load_characters()
    if catgirl_name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '指定的猫娘不存在'}, status_code=404)
    
    old_catgirl = characters.get('当前猫娘', '')
    
    # 检查当前角色是否有活跃的语音session
    if old_catgirl and old_catgirl in session_manager:
        mgr = session_manager[old_catgirl]
        if mgr.is_active:
            # 检查是否是语音模式（通过session类型判断）
            from main_logic.omni_realtime_client import OmniRealtimeClient
            is_voice_mode = mgr.session and isinstance(mgr.session, OmniRealtimeClient)
            
            if is_voice_mode:
                return JSONResponse({
                    'success': False, 
                    'error': '语音状态下无法切换角色，请先停止语音对话后再切换'
                }, status_code=400)
    characters['当前猫娘'] = catgirl_name
    _config_manager.save_characters(characters)
    initialize_character_data = get_initialize_character_data()
    # 自动重新加载配置
    await initialize_character_data()
    
    # 通过WebSocket通知所有连接的客户端
    # 使用session_manager中的websocket，但需要确保websocket已设置
    notification_count = 0
    logger.info(f"开始通知WebSocket客户端：猫娘从 {old_catgirl} 切换到 {catgirl_name}")
    
    message = json.dumps({
        "type": "catgirl_switched",
        "new_catgirl": catgirl_name,
        "old_catgirl": old_catgirl
    })
    
    # 遍历所有session_manager，尝试发送消息
    for lanlan_name, mgr in list(session_manager.items()):
        ws = mgr.websocket
        logger.info(f"检查 {lanlan_name} 的WebSocket: websocket存在={ws is not None}")
        
        if ws:
            try:
                await ws.send_text(message)
                notification_count += 1
                logger.info(f"✅ 已通过WebSocket通知 {lanlan_name} 的连接：猫娘已从 {old_catgirl} 切换到 {catgirl_name}")
            except Exception as e:
                logger.warning(f"❌ 通知 {lanlan_name} 的连接失败: {e}")
                # 如果发送失败，可能是连接已断开，清空websocket引用
                if mgr.websocket == ws:
                    mgr.websocket = None
    
    if notification_count > 0:
        logger.info(f"✅ 已通过WebSocket通知 {notification_count} 个连接的客户端：猫娘已从 {old_catgirl} 切换到 {catgirl_name}")
    else:
        logger.warning("⚠️ 没有找到任何活跃的WebSocket连接来通知猫娘切换")
        logger.warning("提示：请确保前端页面已打开并建立了WebSocket连接，且已调用start_session")
    
    return {"success": True}


@router.post('/reload')
async def reload_character_config():
    """重新加载角色配置（热重载）"""
    try:
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()
        return {"success": True, "message": "角色配置已重新加载"}
    except Exception as e:
        logger.error(f"重新加载角色配置失败: {e}")
        return JSONResponse(
            {'success': False, 'error': f'重新加载失败: {str(e)}'}, 
            status_code=500
        )


@router.post('/master')
async def update_master(request: Request):
    data = await request.json()
    if not data:
        return JSONResponse({'success': False, 'error': '档案名为必填项'}, status_code=400)
    profile_name = data.get('档案名')
    err = _validate_profile_name(profile_name)
    if err:
        return JSONResponse({'success': False, 'error': err}, status_code=400)
    data['档案名'] = str(profile_name).strip()
    _config_manager = get_config_manager()
    initialize_character_data = get_initialize_character_data()
    characters = _config_manager.load_characters()
    characters['主人'] = {k: v for k, v in data.items() if v}
    _config_manager.save_characters(characters)
    # 自动重新加载配置
    await initialize_character_data()
    return {"success": True}


@router.post('/catgirl')
async def add_catgirl(request: Request):
    data = await request.json()
    if not data:
        return JSONResponse({'success': False, 'error': '档案名为必填项'}, status_code=400)

    profile_name = data.get('档案名')
    err = _validate_profile_name(profile_name)
    if err:
        return JSONResponse({'success': False, 'error': err}, status_code=400)
    data['档案名'] = str(profile_name).strip()
    
    _config_manager = get_config_manager()
    characters = _config_manager.load_characters()
    key = data['档案名']
    if key in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '该猫娘已存在'}, status_code=400)
    
    if '猫娘' not in characters:
        characters['猫娘'] = {}
    
    # 创建猫娘数据，只保存非空字段
    catgirl_data = {}
    for k, v in data.items():
        if k != '档案名':
            # voice_id 特殊处理：空字符串表示删除该字段
            if k == 'voice_id' and v == '':
                continue  # 不添加该字段，相当于删除
            elif v:  # 只保存非空字段
                catgirl_data[k] = v
    
    characters['猫娘'][key] = catgirl_data
    _config_manager.save_characters(characters)
    initialize_character_data = get_initialize_character_data()
    # 自动重新加载配置
    await initialize_character_data()
    
    # 通知记忆服务器重新加载配置
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"http://127.0.0.1:{MEMORY_SERVER_PORT}/reload", timeout=5.0)
            if resp.status_code == 200:
                result = resp.json()
                if result.get('status') == 'success':
                    logger.info(f"✅ 已通知记忆服务器重新加载配置（新角色: {key}）")
                else:
                    logger.warning(f"⚠️ 记忆服务器重新加载配置返回: {result.get('message')}")
            else:
                logger.warning(f"⚠️ 记忆服务器重新加载配置失败，状态码: {resp.status_code}")
    except Exception as e:
        logger.warning(f"⚠️ 通知记忆服务器重新加载配置时出错: {e}（不影响角色创建）")
    
    return {"success": True}


@router.put('/catgirl/{name}')
async def update_catgirl(name: str, request: Request):
    data = await request.json()
    if not data:
        return JSONResponse({'success': False, 'error': '无数据'}, status_code=400)
    _config_manager = get_config_manager()
    characters = _config_manager.load_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    
    # 记录更新前的voice_id，用于检测是否变更
    old_voice_id = characters['猫娘'][name].get('voice_id', '')
    
    # 如果包含voice_id，验证其有效性
    if 'voice_id' in data:
        voice_id = data['voice_id']
        # 空字符串表示删除voice_id，跳过验证
        if voice_id != '' and not _config_manager.validate_voice_id(voice_id):
            voices = _config_manager.get_voices_for_current_api()
            available_voices = list(voices.keys())
            return JSONResponse({
                'success': False, 
                'error': f'voice_id "{voice_id}" 在当前API的音色库中不存在',
                'available_voices': available_voices
            }, status_code=400)
    
    # 只更新前端传来的字段，未传字段保留原值，且不允许通过此接口修改 system_prompt
    removed_fields = []
    for k, v in characters['猫娘'][name].items():
        if k not in data and k not in ('档案名', 'system_prompt', 'voice_id', 'live2d', 'model_type', 'vrm', 'vrm_animation', 'lighting', 'live2d_item_id'):
            removed_fields.append(k)
    for k in removed_fields:
        characters['猫娘'][name].pop(k)
    
    # 处理voice_id的特殊逻辑：如果传入空字符串，则删除该字段
    if 'voice_id' in data and data['voice_id'] == '':
        characters['猫娘'][name].pop('voice_id', None)
    
    # 更新其他字段
    for k, v in data.items():
        if k not in ('档案名', 'voice_id') and v:
            characters['猫娘'][name][k] = v
        elif k == 'voice_id' and v:  # voice_id非空时才更新
            characters['猫娘'][name][k] = v
    _config_manager.save_characters(characters)
    
    # 获取更新后的voice_id
    new_voice_id = characters['猫娘'][name].get('voice_id', '')
    voice_id_changed = (old_voice_id != new_voice_id)
    
    # 如果是当前活跃的猫娘且voice_id发生了变更，需要先通知前端，再关闭session
    is_current_catgirl = (name == characters.get('当前猫娘', ''))
    session_ended = False
    
    session_manager = get_session_manager()
    if voice_id_changed and is_current_catgirl and name in session_manager:
        # 检查是否有活跃的session
        if session_manager[name].is_active:
            logger.info(f"检测到 {name} 的voice_id已变更（{old_voice_id} -> {new_voice_id}），准备刷新...")
            
            # 1. 先发送刷新消息（WebSocket还连着）
            await send_reload_page_notice(session_manager[name])
            
            # 2. 立刻关闭session（这会断开WebSocket）
            try:
                await session_manager[name].end_session(by_server=True)
                session_ended = True
                logger.info(f"{name} 的session已结束")
            except Exception as e:
                logger.error(f"结束session时出错: {e}")
    
    # 方案3：条件性重新加载 - 只有当前猫娘或voice_id变更时才重新加载配置
    if voice_id_changed and is_current_catgirl:
        # 自动重新加载配置
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()
        logger.info("配置已重新加载，新的voice_id已生效")
    elif voice_id_changed and not is_current_catgirl:
        # 不是当前猫娘，跳过重新加载，避免影响当前猫娘的session
        logger.info(f"切换的是其他猫娘 {name} 的音色，跳过重新加载以避免影响当前猫娘的session")
    
    return {"success": True, "voice_id_changed": voice_id_changed, "session_restarted": session_ended}


@router.delete('/catgirl/{name}')
async def delete_catgirl(name: str):
    import shutil
    _config_manager = get_config_manager()
    characters = _config_manager.load_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    
    # 检查是否是当前正在使用的猫娘
    current_catgirl = characters.get('当前猫娘', '')
    if name == current_catgirl:
        return JSONResponse({'success': False, 'error': '不能删除当前正在使用的猫娘！请先切换到其他猫娘后再删除。'}, status_code=400)
    
    # 删除对应的记忆文件
    try:
        memory_paths = [_config_manager.memory_dir, _config_manager.project_memory_dir]
        files_to_delete = [
            f'semantic_memory_{name}',  # 语义记忆目录
            f'time_indexed_{name}',     # 时间索引数据库文件
            f'settings_{name}.json',    # 设置文件
            f'recent_{name}.json',      # 最近聊天记录文件
        ]
        
        for base_dir in memory_paths:
            for file_name in files_to_delete:
                file_path = base_dir / file_name
                if file_path.exists():
                    try:
                        if file_path.is_dir():
                            shutil.rmtree(file_path)
                        else:
                            file_path.unlink()
                        logger.info(f"已删除: {file_path}")
                    except Exception as e:
                        logger.warning(f"删除失败 {file_path}: {e}")
    except Exception as e:
        logger.error(f"删除记忆文件时出错: {e}")
    
    # 删除角色配置
    del characters['猫娘'][name]
    _config_manager.save_characters(characters)
    initialize_character_data = get_initialize_character_data()
    await initialize_character_data()
    return {"success": True}

@router.post('/clear_voice_ids')
async def clear_voice_ids():
    """清除所有角色的本地Voice ID记录"""
    try:
        _config_manager = get_config_manager()
        characters = _config_manager.load_characters()
        cleared_count = 0
        
        # 清除所有猫娘的voice_id
        if '猫娘' in characters:
            for name in characters['猫娘']:
                if 'voice_id' in characters['猫娘'][name] and characters['猫娘'][name]['voice_id']:
                    characters['猫娘'][name]['voice_id'] = ''
                    cleared_count += 1
        
        _config_manager.save_characters(characters)
        # 自动重新加载配置
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()
        
        return JSONResponse({
            'success': True, 
            'message': f'已清除 {cleared_count} 个角色的Voice ID记录',
            'cleared_count': cleared_count
        })
    except Exception as e:
        return JSONResponse({
            'success': False, 
            'error': f'清除Voice ID记录时出错: {str(e)}'
        }, status_code=500)


@router.get('/custom_tts_voices')
async def list_custom_tts_voices_for_characters():
    """获取自定义 TTS 可用声音列表（用于角色管理页面的音色选择）。

    当前由适配层处理 GPT-SoVITS provider 的路径映射与 voice_id 前缀规则。
    """
    try:
        _config_manager = get_config_manager()
        
        # 使用与 gptsovits_tts_worker 相同的配置解析路径，确保 URL 一致
        tts_config = _config_manager.get_model_api_config('tts_custom')
        base_url = (tts_config.get('base_url') or '').rstrip('/')
        if not base_url or not (base_url.startswith('http://') or base_url.startswith('https://')):
            return JSONResponse({
                'success': False,
                'error': '未配置 GPT-SoVITS API URL，请先在 API 设置中启用并配置自定义 TTS',
                'voices': []
            }, status_code=400)
        
        # SSRF 防护：GPT-SoVITS 仅限 localhost
        from urllib.parse import urlparse
        import ipaddress
        parsed = urlparse(base_url)
        host = parsed.hostname or ''
        try:
            if not ipaddress.ip_address(host).is_loopback:
                return JSONResponse({'success': False, 'error': 'GPT-SoVITS API URL 必须为 localhost', 'voices': []}, status_code=400)
        except ValueError:
            if host not in ('localhost',):
                return JSONResponse({'success': False, 'error': 'GPT-SoVITS API URL 必须为 localhost', 'voices': []}, status_code=400)
        
        # 通过适配层获取并标准化自定义 TTS voices
        voices = await get_custom_tts_voices(base_url, provider='gptsovits')
        
        return JSONResponse({
            'success': True,
            'voices': voices,
            'api_url': base_url
        })
    except (CustomTTSVoiceFetchError, ValueError) as e:
        return JSONResponse({
            'success': False,
            'error': f'连接 GPT-SoVITS API 失败: {str(e)}',
            'voices': []
        }, status_code=502)
    except Exception as e:
        return JSONResponse({
            'success': False,
            'error': f'获取 GPT-SoVITS 声音列表失败: {str(e)}',
            'voices': []
        }, status_code=500)


@router.post('/set_microphone')
async def set_microphone(request: Request):
    try:
        data = await request.json()
        microphone_id = data.get('microphone_id')
        
        # 使用标准的load/save函数
        _config_manager = get_config_manager()
        characters_data = _config_manager.load_characters()
        
        # 添加或更新麦克风选择
        characters_data['当前麦克风'] = microphone_id
        
        # 保存配置
        _config_manager.save_characters(characters_data)
        # 自动重新加载配置
        initialize_character_data = get_initialize_character_data()
        await initialize_character_data()
        
        return {"success": True}
    except Exception as e:
        logger.error(f"保存麦克风选择失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/get_microphone')
async def get_microphone():
    try:
        _config_manager = get_config_manager()
        # 使用配置管理器加载角色配置
        characters_data = _config_manager.load_characters()
        
        # 获取保存的麦克风选择
        microphone_id = characters_data.get('当前麦克风')
        
        return {"microphone_id": microphone_id}
    except Exception as e:
        logger.error(f"获取麦克风选择失败: {e}")
        return {"microphone_id": None}


@router.get('/voices')
async def get_voices():
    """获取当前API key对应的所有已注册音色"""
    _config_manager = get_config_manager()
    result = {"voices": _config_manager.get_voices_for_current_api()}
    
    # 如果是免费版且使用 lanlan.tech，附带免费预设音色
    core_config = _config_manager.get_core_config()
    if core_config.get('IS_FREE_VERSION'):
        core_url = core_config.get('CORE_URL', '')
        openrouter_url = core_config.get('OPENROUTER_URL', '')
        if 'lanlan.tech' in core_url or 'lanlan.tech' in openrouter_url:
            from utils.api_config_loader import get_free_voices
            free_voices = get_free_voices()
            if free_voices:
                result["free_voices"] = free_voices
    
    # 构建 voice_id → 使用该音色的角色名列表，用于前端显示
    characters = _config_manager.load_characters()
    voice_owners = {}
    for catgirl_name, catgirl_config in characters.get('猫娘', {}).items():
        if not isinstance(catgirl_config, dict):
            logger.warning(f"角色配置格式异常，已跳过 voice_owners 统计: {catgirl_name}")
            continue
        vid = catgirl_config.get('voice_id', '')
        if vid:
            voice_owners.setdefault(vid, []).append(catgirl_name)
    result["voice_owners"] = voice_owners
    
    return result


@router.get('/voice_preview')
async def get_voice_preview(voice_id: str):
    """获取音色预览音频"""
    try:
        _config_manager = get_config_manager()
        
        # 优先尝试从 tts_custom 获取 API Key
        try:
            tts_custom_config = _config_manager.get_model_api_config('tts_custom')
            audio_api_key = tts_custom_config.get('api_key', '')
        except Exception:
            audio_api_key = ''
            
        # 如果没有，则回退到核心配置
        if not audio_api_key:
            core_config = _config_manager.get_core_config()
            audio_api_key = core_config.get('AUDIO_API_KEY', '')

        if not audio_api_key:
            return JSONResponse({'success': False, 'error': '未配置 AUDIO_API_KEY'}, status_code=400)

        # 生成音频
        dashscope.api_key = audio_api_key
        logger.info(f"正在为音色 {voice_id} 生成预览音频...")
        
        text = "喵喵喵～这里是neko～很高兴见到你～"
        # 参照 复刻.py 使用 cosyvoice-v3-plus 模型
        try:
            synthesizer = SpeechSynthesizer(model="cosyvoice-v3-plus", voice=voice_id)
            # 使用 asyncio.to_thread 包装同步阻塞调用
            audio_data = await asyncio.to_thread(lambda: synthesizer.call(text))
            
            if not audio_data:
                request_id = getattr(synthesizer, 'get_last_request_id', lambda: 'unknown')()
                logger.error(f"生成音频失败: audio_data 为空. Request ID: {request_id}")
                return JSONResponse({
                    'success': False, 
                    'error': f'生成音频失败 (Request ID: {request_id})。请检查 API Key 额度或音色 ID 是否有效。'
                }, status_code=500)
                
            logger.info(f"音色 {voice_id} 预览音频生成成功，大小: {len(audio_data)} 字节")
                
            # 将音频数据转换为 Base64 字符串
            audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                
            return {
                "success": True, 
                "audio": audio_base64,
                "mime_type": "audio/mpeg"
            }
        except Exception as e:
            error_msg = str(e)
            logger.error(f"SpeechSynthesizer 调用异常: {error_msg}")
            return JSONResponse({
                'success': False, 
                'error': f'语音合成异常: {error_msg}'
            }, status_code=500)
    except Exception as e:
        logger.error(f"生成音色预览失败: {e}")
        return JSONResponse({'success': False, 'error': f'系统错误: {str(e)}'}, status_code=500)


@router.post('/voices')
async def register_voice(request: Request):
    """注册新音色"""
    try:
        data = await request.json()
        voice_id = data.get('voice_id')
        voice_data = data.get('voice_data')
        
        if not voice_id or not voice_data:
            return JSONResponse({
                'success': False,
                'error': '缺少必要参数'
            }, status_code=400)
        
        # 准备音色数据
        complete_voice_data = {
            **voice_data,
            'voice_id': voice_id,
            'created_at': datetime.now().isoformat()
        }
        
        try:
            _config_manager = get_config_manager()
            _config_manager.save_voice_for_current_api(voice_id, complete_voice_data)
        except Exception as e:
            logger.warning(f"保存音色配置失败: {e}")
            return JSONResponse({
                'success': False,
                'error': f'保存音色配置失败: {str(e)}'
            }, status_code=500)
            
        return {"success": True, "message": "音色注册成功"}
    except Exception as e:
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.delete('/voices/{voice_id}')
async def delete_voice(voice_id: str):
    """删除指定音色"""
    try:
        _config_manager = get_config_manager()
        deleted = _config_manager.delete_voice_for_current_api(voice_id)
        
        if deleted:
            # 清理所有角色中使用该音色的引用
            _config_manager = get_config_manager()
            session_manager = get_session_manager()
            characters = _config_manager.load_characters()
            cleaned_count = 0
            affected_active_names = []
            
            if '猫娘' in characters:
                for name in characters['猫娘']:
                    if characters['猫娘'][name].get('voice_id') == voice_id:
                        characters['猫娘'][name]['voice_id'] = ''
                        cleaned_count += 1
                        
                        # 检查该角色是否是当前活跃的 session
                        if name in session_manager and session_manager[name].is_active:
                            affected_active_names.append(name)
            
            if cleaned_count > 0:
                _config_manager.save_characters(characters)
                
                # 对于受影响的活跃角色，通知并结束 session
                for name in affected_active_names:
                    logger.info(f"检测到活跃角色 {name} 的 voice_id 已被删除，准备刷新...")
                    # 1. 发送刷新通知
                    await send_reload_page_notice(session_manager[name], "音色已删除，页面即将刷新")
                    # 2. 结束 session
                    try:
                        await session_manager[name].end_session(by_server=True)
                        logger.info(f"已结束受影响角色 {name} 的 session")
                    except Exception as e:
                        logger.error(f"结束受影响角色 {name} 的 session 时出错: {e}")

                # 自动重新加载配置
                initialize_character_data = get_initialize_character_data()
                await initialize_character_data()
            
            logger.info(f"已删除音色 '{voice_id}'，并清理了 {cleaned_count} 个角色的引用")
            return {
                "success": True,
                "message": f"音色已删除，已清理 {cleaned_count} 个角色的引用"
            }
        else:
            return JSONResponse({
                'success': False,
                'error': '音色不存在或删除失败'
            }, status_code=404)
    except Exception as e:
        logger.error(f"删除音色时出错: {e}")
        return JSONResponse({
            'success': False,
            'error': f'删除音色失败: {str(e)}'
        }, status_code=500)


@router.post('/voice_clone')
async def voice_clone(file: UploadFile = File(...), prefix: str = Form(...), ref_language: str = Form(default="ch")):
    """
    语音克隆接口
    
    参数:
        file: 音频文件
        prefix: 音色前缀名
        ref_language: 参考音频的语言，可选值：ch, en, fr, de, ja, ko, ru
                      注意：这是参考音频的语言，不是目标语音的语言
    """
    # 直接读取到内存
    try:
        file_content = await file.read()
        file_buffer = io.BytesIO(file_content)
    except Exception as e:
        logger.error(f"读取文件到内存失败: {e}")
        return JSONResponse({'error': f'读取文件失败: {e}'}, status_code=500)
    
    # 检测是否使用本地 TTS（ws/wss 协议）
    _config_manager = get_config_manager()
    tts_config = _config_manager.get_model_api_config('tts_custom')
    base_url = tts_config.get('base_url', '')
    is_local_tts = tts_config.get('is_custom') and base_url.startswith(('ws://', 'wss://'))
    
    if is_local_tts:
        # ==================== 本地 TTS 注册流程 ====================
        # 将 ws(s):// 转换为 http(s):// 用于 REST API 调用
        if base_url.startswith('wss://'):
            http_base = 'https://' + base_url[6:]
        else:
            http_base = 'http://' + base_url[5:]
        
        # 移除可能的 /v1/audio/speech/stream 路径，只保留主机部分
        # 例如: ws://127.0.0.1:50000/v1/audio/speech/stream -> http://127.0.0.1:50000
        if '/v1/' in http_base:
            http_base = http_base.split('/v1/')[0]
        
        register_url = f"{http_base}/v1/speakers/register"
        logger.info(f"使用本地 TTS 注册: {register_url}")
        
        try:
            file_buffer.seek(0)
            
            # 根据用户 demo，API 格式：
            # POST /v1/speakers/register
            # multipart/form-data: speaker_id, prompt_text, prompt_audio
            files = {
                'prompt_audio': (file.filename, file_buffer, 'audio/wav')
            }
            data = {
                'speaker_id': prefix,
                'prompt_text': f"<|{ref_language}|>" if ref_language != 'ch' else "希望你以后能够做的比我还好呦。"
            }
            
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(register_url, data=data, files=files)
                
                if resp.status_code == 200:
                    result = resp.json()
                    voice_id = prefix  # 本地 TTS 使用 speaker_id 作为 voice_id
                    
                    # 保存到本地音色库（使用特殊的 key 标识本地 TTS）
                    voice_data = {
                        'voice_id': voice_id,
                        'prefix': prefix,
                        'is_local': True,
                        'created_at': datetime.now().isoformat()
                    }
                    try:
                        _config_manager.save_voice_for_current_api(voice_id, voice_data)
                        logger.info(f"本地 TTS voice_id 已保存: {voice_id}")
                    except Exception as save_error:
                        logger.warning(f"保存 voice_id 到音色库失败（本地 TTS 仍可用）: {save_error}")
                    
                    return JSONResponse({
                        'voice_id': voice_id,
                        'message': result.get('message', '本地音色注册成功'),
                        'is_local': True
                    })
                else:
                    error_text = resp.text
                    logger.error(f"本地 TTS 注册失败: {error_text}")
                    return JSONResponse({
                        'error': f'本地 TTS 注册失败: {error_text[:200]}'
                    }, status_code=resp.status_code)
                    
        except httpx.ConnectError as e:
            logger.error(f"无法连接本地 TTS 服务器: {e}")
            return JSONResponse({
                'error': f'无法连接本地 TTS 服务器: {http_base}，请确保服务器已启动'
            }, status_code=503)
        except Exception as e:
            logger.error(f"本地 TTS 注册时发生错误: {e}")
            return JSONResponse({
                'error': f'本地 TTS 注册失败: {str(e)}'
            }, status_code=500)
    
    # ==================== 阿里云 TTS 注册流程（原有逻辑） ====================
    
    # 根据参考音频语言计算 language_hints
    # 对于中文 (ch)，language_hints 为空列表
    # 对于其他语言，language_hints 为包含该语言代码的单元素列表
    valid_languages = ['ch', 'en', 'fr', 'de', 'ja', 'ko', 'ru']
    if ref_language not in valid_languages:
        logger.warning(f"无效的语言代码 '{ref_language}'，使用默认值 'ch'")
        ref_language = 'ch'
    
    language_hints = [] if ref_language == 'ch' else [ref_language]
    logger.info(f"参考音频语言（阿里云）: {ref_language}, language_hints: {language_hints}")


    def validate_audio_file(file_buffer: io.BytesIO, filename: str) -> tuple[str, str]:

        """
        验证音频文件类型和格式
        返回: (mime_type, error_message)
        """
        file_path_obj = pathlib.Path(filename)
        file_extension = file_path_obj.suffix.lower()
        
        # 检查文件扩展名
        if file_extension not in ['.wav', '.mp3', '.m4a']:
            return "", f"不支持的文件格式: {file_extension}。仅支持 WAV、MP3 和 M4A 格式。"
        
        # 根据扩展名确定MIME类型
        if file_extension == '.wav':
            mime_type = "audio/wav"
            # 检查WAV文件是否为16bit
            try:
                file_buffer.seek(0)
                with wave.open(file_buffer, 'rb') as wav_file:
                    # 检查采样宽度（bit depth）
                    if wav_file.getsampwidth() != 2:  # 2 bytes = 16 bits
                        return "", f"WAV文件必须是16bit格式，当前文件是{wav_file.getsampwidth() * 8}bit。"
                    
                    # 检查声道数（建议单声道）
                    channels = wav_file.getnchannels()
                    if channels > 1:
                        return "", f"建议使用单声道WAV文件，当前文件有{channels}个声道。"
                    
                    # 检查采样率
                    sample_rate = wav_file.getframerate()
                    if sample_rate not in [8000, 16000, 22050, 44100, 48000]:
                        return "", f"建议使用标准采样率(8000, 16000, 22050, 44100, 48000)，当前文件采样率: {sample_rate}Hz。"
                file_buffer.seek(0)
            except Exception as e:
                return "", f"WAV文件格式错误: {str(e)}。请确认您的文件是合法的WAV文件。"
                
        elif file_extension == '.mp3':
            mime_type = "audio/mpeg"
            try:
                file_buffer.seek(0)
                # 读取更多字节以支持不同的MP3格式
                header = file_buffer.read(32)
                file_buffer.seek(0)

                # 检查文件大小是否合理
                file_size = len(file_buffer.getvalue())
                if file_size < 1024:  # 至少1KB
                    return "", "MP3文件太小，可能不是有效的音频文件。"
                if file_size > 1024 * 1024 * 10:  # 10MB
                    return "", "MP3文件太大，可能不是有效的音频文件。"
                
                # 更宽松的MP3文件头检查
                # MP3文件通常以ID3标签或帧同步字开头
                # 检查是否以ID3标签开头 (ID3v2)
                has_id3_header = header.startswith(b'ID3')
                # 检查是否有帧同步字 (FF FA, FF FB, FF F2, FF F3, FF E3等)
                has_frame_sync = False
                for i in range(len(header) - 1):
                    if header[i] == 0xFF and (header[i+1] & 0xE0) == 0xE0:
                        has_frame_sync = True
                        break
                
                # 如果既没有ID3标签也没有帧同步字，则认为文件可能无效
                # 但这只是一个警告，不应该严格拒绝
                if not has_id3_header and not has_frame_sync:
                    return mime_type, f"警告: MP3文件可能格式不标准，文件头: {header[:4].hex()}"
                        
            except Exception as e:
                return "", f"MP3文件读取错误: {str(e)}。请确认您的文件是合法的MP3文件。"
                
        elif file_extension == '.m4a':
            mime_type = "audio/mp4"
            try:
                file_buffer.seek(0)
                # 读取文件头来验证M4A格式
                header = file_buffer.read(32)
                file_buffer.seek(0)
                
                # M4A文件应该以'ftyp'盒子开始，通常在偏移4字节处
                # 检查是否包含'ftyp'标识
                if b'ftyp' not in header:
                    return "", "M4A文件格式无效或已损坏。请确认您的文件是合法的M4A文件。"
                
                # 进一步验证：检查是否包含常见的M4A类型标识
                # M4A通常包含'mp4a', 'M4A ', 'M4V '等类型
                valid_types = [b'mp4a', b'M4A ', b'M4V ', b'isom', b'iso2', b'avc1']
                has_valid_type = any(t in header for t in valid_types)
                
                if not has_valid_type:
                    return mime_type,  "警告: M4A文件格式无效或已损坏。请确认您的文件是合法的M4A文件。"
                        
            except Exception as e:
                return "", f"M4A文件读取错误: {str(e)}。请确认您的文件是合法的M4A文件。"
        
        return mime_type, ""

    try:
        # 1. 验证音频文件
        mime_type, error_msg = validate_audio_file(file_buffer, file.filename)
        if not mime_type:
            return JSONResponse({'error': error_msg}, status_code=400)
        
        # 检查文件大小（tfLink支持最大100MB）
        file_size = len(file_content)
        if file_size > 100 * 1024 * 1024:  # 100MB
            return JSONResponse({'error': '文件大小超过100MB，超过tfLink的限制'}, status_code=400)
        
        # 2. 上传到 tfLink - 直接使用内存中的内容
        file_buffer.seek(0)
        # 根据tfLink API文档，使用multipart/form-data上传文件
        # 参数名应为'file'
        files = {'file': (file.filename, file_buffer, mime_type)}
        
        # 添加更多的请求头，确保兼容性
        headers = {
            'Accept': 'application/json'
        }
        
        logger.info(f"正在上传文件到tfLink，文件名: {file.filename}, 大小: {file_size} bytes, MIME类型: {mime_type}")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(TFLINK_UPLOAD_URL, files=files, headers=headers)

            # 检查响应状态
            if resp.status_code != 200:
                logger.error(f"上传到tfLink失败，状态码: {resp.status_code}, 响应内容: {resp.text}")
                return JSONResponse({'error': f'上传到tfLink失败，状态码: {resp.status_code}, 详情: {resp.text[:200]}'}, status_code=500)
            
            try:
                # 解析JSON响应
                data = resp.json()
                logger.info(f"tfLink原始响应: {data}")
                
                # 获取下载链接
                tmp_url = None
                possible_keys = ['downloadLink', 'download_link', 'url', 'direct_link', 'link', 'download_url']
                for key in possible_keys:
                    if key in data:
                        tmp_url = data[key]
                        logger.info(f"找到下载链接键: {key}")
                        break
                
                if not tmp_url:
                    logger.error(f"无法从响应中提取URL: {data}")
                    return JSONResponse({'error': '上传成功但无法从响应中提取URL'}, status_code=500)
                
                # 确保URL有效
                if not tmp_url.startswith(('http://', 'https://')):
                    logger.error(f"无效的URL格式: {tmp_url}")
                    return JSONResponse({'error': f'无效的URL格式: {tmp_url}'}, status_code=500)
                    
                # 测试URL是否可访问
                test_resp = await client.head(tmp_url, timeout=10)
                if test_resp.status_code >= 400:
                    logger.error(f"生成的URL无法访问: {tmp_url}, 状态码: {test_resp.status_code}")
                    return JSONResponse({'error': '生成的临时URL无法访问，请重试'}, status_code=500)
                    
                logger.info(f"成功获取临时URL并验证可访问性: {tmp_url}")
                
            except ValueError:
                raw_text = resp.text
                logger.error(f"上传成功但响应格式无法解析: {raw_text}")
                return JSONResponse({'error': f'上传成功但响应格式无法解析: {raw_text[:200]}'}, status_code=500)
        
        # 3. 用直链注册音色
        # 使用 get_model_api_config('tts_custom') 获取正确的 API 配置
        # tts_custom 会优先使用自定义 TTS API，其次是 Qwen Cosyvoice API（目前唯一支持 voice clone 的服务）
        _config_manager = get_config_manager()
        tts_config = _config_manager.get_model_api_config('tts_custom')
        audio_api_key = tts_config.get('api_key', '')
        
        if not audio_api_key:
            logger.error("未配置 AUDIO_API_KEY")
            return JSONResponse({
                'error': '未配置音频API密钥，请在设置中配置AUDIO_API_KEY',
                'suggestion': '请前往设置页面配置音频API密钥'
            }, status_code=400)
        
        dashscope.api_key = audio_api_key
        service = VoiceEnrollmentService()
        target_model = "cosyvoice-v3-plus"
        
        # 重试配置
        max_retries = 3
        retry_delay = 3  # 重试前等待的秒数
        
        for attempt in range(max_retries):
            try:
                logger.info(f"开始音色注册（尝试 {attempt + 1}/{max_retries}），使用URL: {tmp_url}")
                
                # 尝试执行音色注册
                voice_id = service.create_voice(target_model=target_model, prefix=prefix, url=tmp_url, language_hints=language_hints)
                    
                logger.info(f"音色注册成功，voice_id: {voice_id}")
                voice_data = {
                    'voice_id': voice_id,
                    'prefix': prefix,
                    'file_url': tmp_url,
                    'created_at': datetime.now().isoformat()
                }
                try:
                    _config_manager.save_voice_for_current_api(voice_id, voice_data)
                    logger.info(f"voice_id已保存到音色库: {voice_id}")
                    
                    # 验证voice_id是否能够被正确读取（添加短暂延迟，避免文件系统延迟）
                    await asyncio.sleep(0.1)  # 等待100ms，确保文件写入完成
                    
                    # 最多验证3次，每次间隔100ms
                    validation_success = False
                    for validation_attempt in range(3):
                        if _config_manager.validate_voice_id(voice_id):
                            validation_success = True
                            logger.info(f"voice_id保存验证成功: {voice_id} (尝试 {validation_attempt + 1})")
                            break
                        if validation_attempt < 2:
                            await asyncio.sleep(0.1)
                    
                    if not validation_success:
                        logger.warning(f"voice_id保存后验证失败，但可能已成功保存: {voice_id}")
                        # 不返回错误，因为保存可能已成功，只是验证失败
                        # 继续返回成功，让用户尝试使用
                    
                except Exception as save_error:
                    logger.error(f"保存voice_id到音色库失败: {save_error}")
                    return JSONResponse({
                        'error': f'音色注册成功但保存到音色库失败: {str(save_error)}',
                        'voice_id': voice_id,
                        'file_url': tmp_url
                    }, status_code=500)
                    
                return JSONResponse({
                    'voice_id': voice_id,
                    'request_id': service.get_last_request_id(),
                    'file_url': tmp_url,
                    'message': '音色注册成功并已保存到音色库'
                })
                
            except Exception as e:
                logger.error(f"音色注册失败（尝试 {attempt + 1}/{max_retries}）: {str(e)}")
                error_detail = str(e)
                
                # 检查是否是超时错误
                is_timeout = ("ResponseTimeout" in error_detail or 
                             "response timeout" in error_detail.lower() or
                             "timeout" in error_detail.lower())
                
                # 检查是否是文件下载失败错误
                is_download_failed = ("download audio failed" in error_detail or 
                                     "415" in error_detail)
                
                # 如果是超时或下载失败，且还有重试机会，则重试
                if (is_timeout or is_download_failed) and attempt < max_retries - 1:
                    logger.warning(f"检测到{'超时' if is_timeout else '文件下载失败'}错误，等待 {retry_delay} 秒后重试...")
                    await asyncio.sleep(retry_delay)
                    continue  # 重试
                
                # 如果是最后一次尝试或非可重试错误，返回错误
                if is_timeout:
                    return JSONResponse({
                        'error': f'音色注册超时，已尝试{max_retries}次',
                        'detail': error_detail,
                        'file_url': tmp_url,
                        'suggestion': '请检查您的网络连接，或稍后再试。如果问题持续，可能是服务器繁忙。'
                    }, status_code=408)
                elif is_download_failed:
                    return JSONResponse({
                        'error': f'音色注册失败: 无法下载音频文件，已尝试{max_retries}次',
                        'detail': error_detail,
                        'file_url': tmp_url,
                        'suggestion': '请检查文件URL是否可访问，或稍后重试'
                    }, status_code=415)
                else:
                    # 其他错误直接返回
                    return JSONResponse({
                        'error': f'音色注册失败: {error_detail}',
                        'file_url': tmp_url,
                        'attempt': attempt + 1,
                        'max_retries': max_retries
                    }, status_code=500)
    except Exception as e:
        # 确保tmp_url在出现异常时也有定义
        tmp_url = locals().get('tmp_url', '未获取到URL')
        logger.error(f"注册音色时发生未预期的错误: {str(e)}")
        return JSONResponse({'error': f'注册音色时发生错误: {str(e)}', 'file_url': tmp_url}, status_code=500)
    
@router.get('/character-card/list')
async def get_character_cards():
    """获取character_cards文件夹中的所有角色卡"""
    try:
        # 获取config_manager实例
        config_mgr = get_config_manager()
        
        # 确保character_cards目录存在
        config_mgr.ensure_chara_directory()
        
        character_cards = []
        
        # 遍历character_cards目录下的所有.chara.json文件
        for filename in os.listdir(config_mgr.chara_dir):
            if filename.endswith('.chara.json'):
                try:
                    file_path = os.path.join(config_mgr.chara_dir, filename)
                    
                    # 读取文件内容
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    
                    # 检查是否包含基本信息
                    if data and data.get('name'):
                        character_cards.append({
                            'id': filename[:-11],  # 去掉.chara.json后缀
                            'name': data['name'],
                            'description': data.get('description', ''),
                            'tags': data.get('tags', []),
                            'rawData': data,
                            'path': file_path
                        })
                except Exception as e:
                    logger.error(f"读取角色卡文件 {filename} 时出错: {e}")
        
        logger.info(f"已加载 {len(character_cards)} 个角色卡")
        return {"success": True, "character_cards": character_cards}
    except Exception as e:
        logger.error(f"获取角色卡列表失败: {e}")
        return {"success": False, "error": str(e)}


@router.post('/catgirl/save-to-model-folder')
async def save_catgirl_to_model_folder(request: Request):
    """将角色卡保存到模型所在文件夹"""
    try:
        data = await request.json()
        chara_data = data.get('charaData')
        model_name = data.get('modelName')  # 接收模型名称而不是路径
        file_name = data.get('fileName')
        
        if not chara_data or not model_name or not file_name:
            return JSONResponse({"success": False, "error": "缺少必要参数"}, status_code=400)
        
        # 使用find_model_directory函数查找模型的实际文件系统路径
        model_folder_path, _ = find_model_directory(model_name)
        
        # 检查模型目录是否存在
        if not model_folder_path:
            return JSONResponse({"success": False, "error": f"无法找到模型目录: {model_name}"}, status_code=404)
        
        # 检查是否是用户导入的模型，只允许写入用户目录的模型，不允许写入 workshop/static
        config_mgr = get_config_manager()
        is_user_model = is_user_imported_model(model_folder_path, config_mgr)
        
        if not is_user_model:
            return JSONResponse(
                status_code=403,
                content={
                    "success": False,
                    "error": "只能保存到用户导入的模型目录。请先导入模型到用户模型目录后再保存。"
                }
            )
        
        # 确保模型文件夹存在
        if not os.path.exists(model_folder_path):
            os.makedirs(model_folder_path, exist_ok=True)
            logger.info(f"已创建模型文件夹: {model_folder_path}")
        
        # 防路径穿越：只允许文件名，不允许路径
        safe_name = os.path.basename(file_name)
        if safe_name != file_name or ".." in safe_name or safe_name.startswith(("/", "\\")):
            return JSONResponse({"success": False, "error": "非法文件名"}, status_code=400)
            
        # 保存角色卡到模型文件夹
        file_path = os.path.join(model_folder_path, safe_name)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(chara_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"角色卡已成功保存到模型文件夹: {file_path}")
        return {"success": True, "path": file_path, "modelFolderPath": model_folder_path}
    except Exception as e:
        logger.error(f"保存角色卡到模型文件夹失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post('/character-card/save')
async def save_character_card(request: Request):
    """保存角色卡到characters.json文件"""
    try:
        data = await request.json()
        chara_data = data.get('charaData')
        character_card_name = data.get('character_card_name')
        
        if not chara_data or not character_card_name:
            return JSONResponse({"success": False, "error": "缺少必要参数"}, status_code=400)
        
        # 获取config_manager实例
        _config_manager = get_config_manager()
        
        # 加载现有的characters.json
        characters = _config_manager.load_characters()
        
        # 确保'猫娘'键存在
        if '猫娘' not in characters:
            characters['猫娘'] = {}
        
        # 获取角色卡名称（档案名）
        # 兼容中英文字段名
        chara_name = chara_data.get('档案名') or chara_data.get('name') or character_card_name
        
        # 创建猫娘数据，只保存非空字段
        catgirl_data = {}
        for k, v in chara_data.items():
            if k != '档案名' and k != 'name':
                # voice_id 特殊处理：空字符串表示删除该字段
                if k == 'voice_id' and v == '':
                    continue  # 不添加该字段，相当于删除
                elif v:  # 只保存非空字段
                    catgirl_data[k] = v
        
        # 更新或创建猫娘数据
        characters['猫娘'][chara_name] = catgirl_data
        
        # 保存到characters.json
        _config_manager.save_characters(characters)
        
        # 自动重新加载配置
        initialize_character_data = get_initialize_character_data()
        if initialize_character_data:
            await initialize_character_data()
        
        logger.info(f"角色卡已成功保存到characters.json: {chara_name}")
        return {"success": True, "character_card_name": chara_name}
    except Exception as e:
        logger.error(f"保存角色卡到characters.json失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)