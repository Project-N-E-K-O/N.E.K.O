# -*- coding: utf-8 -*-
"""
Live2D Router

Handles Live2D model-related endpoints including:
- Model listing
- Model configuration
- Model parameters
- Emotion mappings
- Model upload
"""

import os
import json
import logging
import pathlib

from fastapi import APIRouter, Request, File, UploadFile
from fastapi.responses import JSONResponse

from .shared_state import get_config_manager, get_initialize_character_data, get_session_manager
from .workshop_router import get_subscribed_workshop_items
from utils.frontend_utils import find_models, find_model_directory, find_model_by_workshop_item_id, find_workshop_item_by_id
router = APIRouter(tags=["live2d"])
logger = logging.getLogger("Main")


@router.get("/api/live2d/models")
async def get_live2d_models(simple: bool = False):
    """
    获取Live2D模型列表
    Args:
        simple: 如果为True，只返回模型名称列表；如果为False，返回完整的模型信息
    """
    try:
        # 先获取本地模型
        models = find_models()
        
        # 再获取Steam创意工坊模型
        try:
            workshop_items_result = await get_subscribed_workshop_items()
            
            # 处理响应结果
            if isinstance(workshop_items_result, dict) and workshop_items_result.get('success', False):
                items = workshop_items_result.get('items', [])
                logger.info(f"获取到{len(items)}个订阅的创意工坊物品")
                
                # 遍历所有物品，提取已安装的模型
                for item in items:
                    # 直接使用get_subscribed_workshop_items返回的installedFolder
                    installed_folder = item.get('installedFolder')
                    # 从publishedFileId字段获取物品ID，而不是item_id
                    item_id = item.get('publishedFileId')
                    
                    if installed_folder and os.path.exists(installed_folder) and os.path.isdir(installed_folder) and item_id:
                        # 检查安装目录下是否有.model3.json文件
                        for filename in os.listdir(installed_folder):
                            if filename.endswith('.model3.json'):
                                model_name = os.path.splitext(os.path.splitext(filename)[0])[0]
                                
                                # 避免重复添加
                                if model_name not in [m['name'] for m in models]:
                                    # 构建正确的/workshop URL路径，确保没有多余的引号
                                    path_value = f'/workshop/{item_id}/{filename}'
                                    logger.debug(f"添加模型路径: {path_value!r}, item_id类型: {type(item_id)}, filename类型: {type(filename)}")
                                    # 移除可能的额外引号
                                    path_value = path_value.strip('"')
                                    models.append({
                                        'name': model_name,
                                        'path': path_value,
                                        'source': 'steam_workshop',
                                        'item_id': item_id
                                    })
                            
                        # 检查安装目录下的子目录
                        for subdir in os.listdir(installed_folder):
                            subdir_path = os.path.join(installed_folder, subdir)
                            if os.path.isdir(subdir_path):
                                model_name = subdir
                                json_file = os.path.join(subdir_path, f'{model_name}.model3.json')
                                if os.path.exists(json_file):
                                    # 避免重复添加
                                    if model_name not in [m['name'] for m in models]:
                                        # 构建正确的/workshop URL路径，确保没有多余的引号
                                        path_value = f'/workshop/{item_id}/{model_name}/{model_name}.model3.json'
                                        logger.debug(f"添加子目录模型路径: {path_value!r}, item_id类型: {type(item_id)}, model_name类型: {type(model_name)}")
                                        # 移除可能的额外引号
                                        path_value = path_value.strip('"')
                                        models.append({
                                            'name': model_name,
                                            'path': path_value,
                                            'source': 'steam_workshop',
                                            'item_id': item_id
                                        })
        except Exception as e:
            logger.error(f"获取创意工坊模型时出错: {e}")
        
        if simple:
            # 只返回模型名称列表
            model_names = [model["name"] for model in models]
            return {"success": True, "models": model_names}
        else:
            # 返回完整的模型信息（保持向后兼容）
            return models
    except Exception as e:
        logger.error(f"获取Live2D模型列表失败: {e}")
        if simple:
            return {"success": False, "error": str(e)}
        else:
            return []



@router.get("/api/models")
async def get_models_legacy():
    """
    向后兼容的API端点，重定向到新的 /api/live2d/models
    """
    return await get_live2d_models(simple=False)


@router.get('/api/characters/current_live2d_model')
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
                # 查找匹配的模型
                matching_model = next((m for m in all_models if m['name'] == live2d_model_name), None)
                
                if matching_model:
                    # 使用完整的模型信息，包含item_id
                    model_info = matching_model.copy()
                    logger.debug(f"从完整模型列表获取模型信息: {model_info}")
                else:
                    # 如果在完整列表中找不到，回退到原来的逻辑
                    model_dir, url_prefix = find_model_directory(live2d_model_name)
                    if os.path.exists(model_dir):
                        # 查找模型配置文件
                        model_files = [f for f in os.listdir(model_dir) if f.endswith('.model3.json')]
                        if model_files:
                            model_file = model_files[0]
                            
                            # 使用保存的item_id构建model_path
                            # 从之前的逻辑中获取saved_item_id
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
                    if os.path.exists(model_dir):
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


@router.put('/api/characters/catgirl/l2d/{name}')
async def update_catgirl_l2d(name: str, request: Request):
    """更新指定猫娘的Live2D模型设置"""
    try:
        data = await request.json()
        live2d_model = data.get('live2d')
        item_id = data.get('item_id')  # 获取可选的item_id
        
        if not live2d_model:
            return JSONResponse(content={
                'success': False,
                'error': '未提供Live2D模型名称'
            })
        
        # 加载当前角色配置
        _config_manager = get_config_manager()
        characters = _config_manager.load_characters()
        
        # 确保猫娘配置存在
        if '猫娘' not in characters:
            characters['猫娘'] = {}
        
        # 确保指定猫娘的配置存在
        if name not in characters['猫娘']:
            characters['猫娘'][name] = {}
        
        # 更新Live2D模型设置，同时保存item_id（如果有）
        characters['猫娘'][name]['live2d'] = live2d_model
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
        
        return JSONResponse(content={
            'success': True,
            'message': f'已更新角色 {name} 的Live2D模型为 {live2d_model}'
        })
        
    except Exception as e:
        logger.error(f"更新角色Live2D模型失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        })


@router.put('/api/characters/catgirl/voice_id/{name}')
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
            if session_manager[name].websocket:
                try:
                    await session_manager[name].websocket.send_text(json.dumps({
                        "type": "reload_page",
                        "message": "语音已更新，页面即将刷新"
                    }))
                    logger.info(f"已通知 {name} 的前端刷新页面")
                except Exception as e:
                    logger.warning(f"通知前端刷新页面失败: {e}")
            
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
        logger.info(f"配置已重新加载，新的voice_id已生效")
    else:
        # 不是当前猫娘，跳过重新加载，避免影响当前猫娘的session
        logger.info(f"切换的是其他猫娘 {name} 的音色，跳过重新加载以避免影响当前猫娘的session")
    
    return {"success": True, "session_restarted": session_ended}


@router.get("/api/live2d/model_config/{model_name}")
async def get_model_config(model_name: str):
    """获取指定Live2D模型的model3.json配置"""
    try:
        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_directory(model_name)
        if not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # 查找.model3.json文件
        model_json_path = None
        for file in os.listdir(model_dir):
            if file.endswith('.model3.json'):
                model_json_path = os.path.join(model_dir, file)
                break
        
        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})
        
        with open(model_json_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        
        # 检查并自动添加缺失的配置
        config_updated = False
        
        # 确保FileReferences存在
        if 'FileReferences' not in config_data:
            config_data['FileReferences'] = {}
            config_updated = True
        
        # 确保Motions存在
        if 'Motions' not in config_data['FileReferences']:
            config_data['FileReferences']['Motions'] = {}
            config_updated = True
        
        # 确保Expressions存在
        if 'Expressions' not in config_data['FileReferences']:
            config_data['FileReferences']['Expressions'] = []
            config_updated = True
        
        # 如果配置有更新，保存到文件
        if config_updated:
            with open(model_json_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=4)
            logger.info(f"已为模型 {model_name} 自动添加缺失的配置项")
            
        return {"success": True, "config": config_data}
    except Exception as e:
        logger.error(f"获取模型配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post("/api/live2d/model_config/{model_name}")
async def update_model_config(model_name: str, request: Request):
    """更新指定Live2D模型的model3.json配置"""
    try:
        data = await request.json()
        
        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_directory(model_name)
        if not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # 查找.model3.json文件
        model_json_path = None
        for file in os.listdir(model_dir):
            if file.endswith('.model3.json'):
                model_json_path = os.path.join(model_dir, file)
                break
        
        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})
        
        # 为了安全，只允许修改 Motions 和 Expressions
        with open(model_json_path, 'r', encoding='utf-8') as f:
            current_config = json.load(f)
            
        if 'FileReferences' in data and 'Motions' in data['FileReferences']:
            current_config['FileReferences']['Motions'] = data['FileReferences']['Motions']
            
        if 'FileReferences' in data and 'Expressions' in data['FileReferences']:
            current_config['FileReferences']['Expressions'] = data['FileReferences']['Expressions']

        with open(model_json_path, 'w', encoding='utf-8') as f:
            json.dump(current_config, f, ensure_ascii=False, indent=4) # 使用 indent=4 保持格式
            
        return {"success": True, "message": "模型配置已更新"}
    except Exception as e:
        logger.error(f"更新模型配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/api/live2d/emotion_mapping/{model_name}')
async def get_emotion_mapping(model_name: str):
    """获取情绪映射配置"""
    try:
        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_directory(model_name)
        if not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # 查找.model3.json文件
        model_json_path = None
        for file in os.listdir(model_dir):
            if file.endswith('.model3.json'):
                model_json_path = os.path.join(model_dir, file)
                break
        
        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})
        
        with open(model_json_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        # 优先使用 EmotionMapping；若不存在则从 FileReferences 推导
        emotion_mapping = config_data.get('EmotionMapping')
        if not emotion_mapping:
            derived_mapping = {"motions": {}, "expressions": {}}
            file_refs = config_data.get('FileReferences', {}) or {}

            # 从标准 Motions 结构推导
            motions = file_refs.get('Motions', {}) or {}
            for group_name, items in motions.items():
                files = []
                for item in items or []:
                    try:
                        file_path = item.get('File') if isinstance(item, dict) else None
                        if file_path:
                            files.append(file_path.replace('\\', '/'))
                    except Exception:
                        continue
                derived_mapping["motions"][group_name] = files

            # 从标准 Expressions 结构推导（按 Name 的前缀进行分组，如 happy_xxx）
            expressions = file_refs.get('Expressions', []) or []
            for item in expressions:
                if not isinstance(item, dict):
                    continue
                name = item.get('Name') or ''
                file_path = item.get('File') or ''
                if not file_path:
                    continue
                file_path = file_path.replace('\\', '/')
                # 根据第一个下划线拆分分组
                if '_' in name:
                    group = name.split('_', 1)[0]
                else:
                    # 无前缀的归入 neutral 组，避免丢失
                    group = 'neutral'
                derived_mapping["expressions"].setdefault(group, []).append(file_path)

            emotion_mapping = derived_mapping
        
        return {"success": True, "config": emotion_mapping}
    except Exception as e:
        logger.error(f"获取情绪映射配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post('/api/live2d/emotion_mapping/{model_name}')
async def update_emotion_mapping(model_name: str, request: Request):
    """更新情绪映射配置"""
    try:
        data = await request.json()
        
        if not data:
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的数据"})

        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_directory(model_name)
        if not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # 查找.model3.json文件
        model_json_path = None
        for file in os.listdir(model_dir):
            if file.endswith('.model3.json'):
                model_json_path = os.path.join(model_dir, file)
                break
        
        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})

        with open(model_json_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        # 统一写入到标准 Cubism 结构（FileReferences.Motions / FileReferences.Expressions）
        file_refs = config_data.setdefault('FileReferences', {})

        # 处理 motions: data 结构为 { motions: { emotion: ["motions/xxx.motion3.json", ...] }, expressions: {...} }
        motions_input = (data.get('motions') if isinstance(data, dict) else None) or {}
        motions_output = {}
        for group_name, files in motions_input.items():
            # 禁止在"常驻"组配置任何motion
            if group_name == '常驻':
                logger.info("忽略常驻组中的motion配置（只允许expression）")
                continue
            items = []
            for file_path in files or []:
                if not isinstance(file_path, str):
                    continue
                normalized = file_path.replace('\\', '/').lstrip('./')
                items.append({"File": normalized})
            motions_output[group_name] = items
        file_refs['Motions'] = motions_output

        # 处理 expressions: 将按 emotion 前缀生成扁平列表，Name 采用 "{emotion}_{basename}" 的约定
        expressions_input = (data.get('expressions') if isinstance(data, dict) else None) or {}

        # 先保留不属于我们情感前缀的原始表达（避免覆盖用户自定义）
        existing_expressions = file_refs.get('Expressions', []) or []
        emotion_prefixes = set(expressions_input.keys())
        preserved_expressions = []
        for item in existing_expressions:
            try:
                name = (item.get('Name') or '') if isinstance(item, dict) else ''
                prefix = name.split('_', 1)[0] if '_' in name else None
                if not prefix or prefix not in emotion_prefixes:
                    preserved_expressions.append(item)
            except Exception:
                preserved_expressions.append(item)

        new_expressions = []
        for emotion, files in expressions_input.items():
            for file_path in files or []:
                if not isinstance(file_path, str):
                    continue
                normalized = file_path.replace('\\', '/').lstrip('./')
                base = os.path.basename(normalized)
                base_no_ext = base.replace('.exp3.json', '')
                name = f"{emotion}_{base_no_ext}"
                new_expressions.append({"Name": name, "File": normalized})

        file_refs['Expressions'] = preserved_expressions + new_expressions

        # 同时保留一份 EmotionMapping（供管理器读取与向后兼容）
        config_data['EmotionMapping'] = data

        # 保存配置到文件
        with open(model_json_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"模型 {model_name} 的情绪映射配置已更新（已同步到 FileReferences）")
        return {"success": True, "message": "情绪映射配置已保存"}
    except Exception as e:
        logger.error(f"更新情绪映射配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/api/live2d/model_files/{model_name}')
async def get_model_files(model_name: str):
    """获取指定Live2D模型的动作和表情文件列表"""
    try:
        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_directory(model_name)
        
        if not os.path.exists(model_dir):
            return {"success": False, "error": f"模型 {model_name} 不存在"}
        
        motion_files = []
        expression_files = []
        
        # 递归搜索所有子文件夹
        def search_files_recursive(directory, target_ext, result_list):
            """递归搜索指定扩展名的文件"""
            try:
                for item in os.listdir(directory):
                    item_path = os.path.join(directory, item)
                    if os.path.isfile(item_path):
                        if item.endswith(target_ext):
                            # 计算相对于模型根目录的路径
                            relative_path = os.path.relpath(item_path, model_dir)
                            # 转换为正斜杠格式（跨平台兼容）
                            relative_path = relative_path.replace('\\', '/')
                            result_list.append(relative_path)
                    elif os.path.isdir(item_path):
                        # 递归搜索子目录
                        search_files_recursive(item_path, target_ext, result_list)
            except Exception as e:
                logger.warning(f"搜索目录 {directory} 时出错: {e}")
        
        # 搜索动作文件
        search_files_recursive(model_dir, '.motion3.json', motion_files)
        
        # 搜索表情文件
        search_files_recursive(model_dir, '.exp3.json', expression_files)
        
        logger.info(f"模型 {model_name} 文件统计: {len(motion_files)} 个动作文件, {len(expression_files)} 个表情文件")
        return {
            "success": True, 
            "motion_files": motion_files,
            "expression_files": expression_files
        }
    except Exception as e:
        logger.error(f"获取模型文件列表失败: {e}")
        return {"success": False, "error": str(e)}


@router.get('/api/live2d/model_parameters/{model_name}')
async def get_model_parameters(model_name: str):
    """获取指定Live2D模型的参数信息（从.cdi3.json文件）"""
    try:
        # 查找模型目录
        model_dir, url_prefix = find_model_directory(model_name)
        
        if not os.path.exists(model_dir):
            return {"success": False, "error": f"模型 {model_name} 不存在"}
        
        # 查找.cdi3.json文件
        cdi3_file = None
        for file in os.listdir(model_dir):
            if file.endswith('.cdi3.json'):
                cdi3_file = os.path.join(model_dir, file)
                break
        
        if not cdi3_file or not os.path.exists(cdi3_file):
            return {"success": False, "error": "未找到.cdi3.json文件"}
        
        # 读取.cdi3.json文件
        with open(cdi3_file, 'r', encoding='utf-8') as f:
            cdi3_data = json.load(f)
        
        # 提取参数信息
        parameters = []
        if 'Parameters' in cdi3_data and isinstance(cdi3_data['Parameters'], list):
            for param in cdi3_data['Parameters']:
                if isinstance(param, dict) and 'Id' in param:
                    parameters.append({
                        'id': param.get('Id'),
                        'groupId': param.get('GroupId', ''),
                        'name': param.get('Name', param.get('Id'))
                    })
        
        # 提取参数组信息
        parameter_groups = {}
        if 'ParameterGroups' in cdi3_data and isinstance(cdi3_data['ParameterGroups'], list):
            for group in cdi3_data['ParameterGroups']:
                if isinstance(group, dict) and 'Id' in group:
                    parameter_groups[group.get('Id')] = {
                        'id': group.get('Id'),
                        'name': group.get('Name', group.get('Id'))
                    }
        
        return {
            "success": True,
            "parameters": parameters,
            "parameter_groups": parameter_groups
        }
    except Exception as e:
        logger.error(f"获取模型参数信息失败: {e}")
        return {"success": False, "error": str(e)}


@router.post('/api/live2d/save_model_parameters/{model_name}')
async def save_model_parameters(model_name: str, request: Request):
    """保存模型参数到模型目录的parameters.json文件"""
    try:
        # 查找模型目录
        model_dir, url_prefix = find_model_directory(model_name)
        
        if not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": f"模型 {model_name} 不存在"})
        
        # 获取请求体中的参数
        body = await request.json()
        parameters = body.get('parameters', {})
        
        if not isinstance(parameters, dict):
            return JSONResponse(status_code=400, content={"success": False, "error": "参数格式错误"})
        
        # 保存到parameters.json文件
        parameters_file = os.path.join(model_dir, 'parameters.json')
        with open(parameters_file, 'w', encoding='utf-8') as f:
            json.dump(parameters, f, indent=2, ensure_ascii=False)
        
        logger.info(f"已保存模型参数到: {parameters_file}, 参数数量: {len(parameters)}")
        return {"success": True, "message": "参数保存成功"}
    except Exception as e:
        logger.error(f"保存模型参数失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/api/live2d/load_model_parameters/{model_name}')
async def load_model_parameters(model_name: str):
    """从模型目录的parameters.json文件加载参数"""
    try:
        # 查找模型目录
        model_dir, url_prefix = find_model_directory(model_name)
        
        if not os.path.exists(model_dir):
            return {"success": False, "error": f"模型 {model_name} 不存在"}
        
        # 读取parameters.json文件
        parameters_file = os.path.join(model_dir, 'parameters.json')
        
        if not os.path.exists(parameters_file):
            return {"success": True, "parameters": {}}  # 文件不存在时返回空参数
        
        with open(parameters_file, 'r', encoding='utf-8') as f:
            parameters = json.load(f)
        
        if not isinstance(parameters, dict):
            return {"success": True, "parameters": {}}
        
        logger.info(f"已加载模型参数从: {parameters_file}, 参数数量: {len(parameters)}")
        return {"success": True, "parameters": parameters}
    except Exception as e:
        logger.error(f"加载模型参数失败: {e}")
        return {"success": False, "error": str(e), "parameters": {}}


@router.get("/api/live2d/model_config_by_id/{model_id}")
async def get_model_config(model_id: str):
    """获取指定Live2D模型的model3.json配置"""
    try:
        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_by_workshop_item_id(model_id)
        if not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # 查找.model3.json文件
        model_json_path = None
        for file in os.listdir(model_dir):
            if file.endswith('.model3.json'):
                model_json_path = os.path.join(model_dir, file)
                break
        
        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})
        
        with open(model_json_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        
        # 检查并自动添加缺失的配置
        config_updated = False
        
        # 确保FileReferences存在
        if 'FileReferences' not in config_data:
            config_data['FileReferences'] = {}
            config_updated = True
        
        # 确保Motions存在
        if 'Motions' not in config_data['FileReferences']:
            config_data['FileReferences']['Motions'] = {}
            config_updated = True
        
        # 确保Expressions存在
        if 'Expressions' not in config_data['FileReferences']:
            config_data['FileReferences']['Expressions'] = []
            config_updated = True
        
        # 如果配置有更新，保存到文件
        if config_updated:
            with open(model_json_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=4)
            logger.info(f"已为模型 {model_id} 自动添加缺失的配置项")
            
        return {"success": True, "config": config_data}
    except Exception as e:
        logger.error(f"获取模型配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post("/api/live2d/model_config_by_id/{model_id}")
async def update_model_config(model_id: str, request: Request):
    """更新指定Live2D模型的model3.json配置"""
    try:
        data = await request.json()
        
        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_by_workshop_item_id(model_id)
        if not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # 查找.model3.json文件
        model_json_path = None
        for file in os.listdir(model_dir):
            if file.endswith('.model3.json'):
                model_json_path = os.path.join(model_dir, file)
                break
        
        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})
        
        # 为了安全，只允许修改 Motions 和 Expressions
        with open(model_json_path, 'r', encoding='utf-8') as f:
            current_config = json.load(f)
            
        if 'FileReferences' in data and 'Motions' in data['FileReferences']:
            current_config['FileReferences']['Motions'] = data['FileReferences']['Motions']
            
        if 'FileReferences' in data and 'Expressions' in data['FileReferences']:
            current_config['FileReferences']['Expressions'] = data['FileReferences']['Expressions']

        with open(model_json_path, 'w', encoding='utf-8') as f:
            json.dump(current_config, f, ensure_ascii=False, indent=4) # 使用 indent=4 保持格式
            
        return {"success": True, "message": "模型配置已更新"}
    except Exception as e:
        logger.error(f"更新模型配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/api/live2d/model_files_by_id/{model_id}')
async def get_model_files_by_id(model_id: str):
    """获取指定Live2D模型的动作和表情文件列表"""
    try:
        # 直接拒绝无效的model_id
        if not model_id or model_id.lower() == 'undefined':
            logger.warning("接收到无效的model_id请求，返回失败")
            return {"success": False, "error": "无效的模型ID"}
        
        # 尝试通过model_id查找模型
        model_dir = None
        url_prefix = None
        
        # 首先尝试通过workshop item_id查找
        try:
            model_dir, url_prefix = find_workshop_item_by_id(model_id)
            logger.debug(f"通过model_id {model_id} 查找模型目录: {model_dir}")
        except Exception as e:
            logger.warning(f"通过model_id查找失败: {e}")
        
        # 如果通过model_id找不到有效的目录，尝试将model_id当作model_name回退查找
        if not model_dir or not os.path.exists(model_dir):
            logger.info(f"尝试将 {model_id} 作为模型名称回退查找")
            try:
                model_dir, url_prefix = find_model_directory(model_id)
                logger.debug(f"作为模型名称查找的目录: {model_dir}")
            except Exception as e:
                logger.warning(f"作为模型名称查找失败: {e}")
        
        # 添加额外的错误检查
        if not model_dir:
            logger.error(f"获取模型目录失败: 目录路径为空")
            return {"success": False, "error": "获取模型目录失败: 无效的路径"}
            
        if not os.path.exists(model_dir):
            logger.warning(f"模型目录不存在: {model_dir}")
            return {"success": False, "error": "模型不存在"}
        
        motion_files = []
        expression_files = []
        
        # 递归搜索所有子文件夹
        def search_files_recursive(directory, target_ext, result_list):
            """递归搜索指定扩展名的文件"""
            try:
                for item in os.listdir(directory):
                    item_path = os.path.join(directory, item)
                    if os.path.isfile(item_path):
                        if item.endswith(target_ext):
                            # 计算相对于模型根目录的路径
                            relative_path = os.path.relpath(item_path, model_dir)
                            # 转换为正斜杠格式（跨平台兼容）
                            relative_path = relative_path.replace('\\', '/')
                            result_list.append(relative_path)
                    elif os.path.isdir(item_path):
                        # 递归搜索子目录
                        search_files_recursive(item_path, target_ext, result_list)
            except Exception as e:
                logger.warning(f"搜索目录 {directory} 时出错: {e}")
        
        # 搜索动作文件
        search_files_recursive(model_dir, '.motion3.json', motion_files)
        
        # 搜索表情文件
        search_files_recursive(model_dir, '.exp3.json', expression_files)
        
        # 查找模型配置文件（model3.json）
        model_config_file = None
        for file in os.listdir(model_dir):
            if file.endswith('.model3.json'):
                model_config_file = file
                break
        
        # 构建模型配置文件的URL
        model_config_url = None
        if model_config_file and url_prefix:
            # 对于workshop模型，需要在URL中包含item_id
            if url_prefix == '/workshop':
                model_config_url = f"{url_prefix}/{model_id}/{model_config_file}"
            else:
                model_config_url = f"{url_prefix}/{model_config_file}"
            logger.debug(f"为模型 {model_id} 构建的配置URL: {model_config_url}")
        
        logger.info(f"文件统计: {len(motion_files)} 个动作文件, {len(expression_files)} 个表情文件")
        return {
            "success": True, 
            "motion_files": motion_files,
            "expression_files": expression_files,
            "model_config_url": model_config_url
        }
    except Exception as e:
        logger.error(f"获取模型文件列表失败: {e}")
        return {"success": False, "error": str(e)}


# Steam 创意工坊管理相关API路由
# 确保这个路由被正确注册

@router.post('/api/live2d/upload_model')
async def upload_live2d_model(files: list[UploadFile] = File(...)):
    """上传Live2D模型到用户文档目录"""
    import shutil
    import tempfile
    
    try:
        if not files:
            return JSONResponse(status_code=400, content={"success": False, "error": "没有上传文件"})
        
        # 创建临时目录来处理上传的文件
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            
            # 保存所有上传的文件到临时目录，保持目录结构
            for file in files:
                # 从文件的相对路径中提取目录结构
                file_path = file.filename
                # 确保路径安全，移除可能的危险路径字符
                file_path = file_path.replace('\\', '/').lstrip('/')
                
                target_file_path = temp_path / file_path
                target_file_path.parent.mkdir(parents=True, exist_ok=True)
                
                # 保存文件
                with open(target_file_path, 'wb') as f:
                    content = await file.read()
                    f.write(content)
            
            # 在临时目录中递归查找.model3.json文件
            model_json_files = list(temp_path.rglob('*.model3.json'))
            
            if not model_json_files:
                return JSONResponse(status_code=400, content={"success": False, "error": "未找到.model3.json文件"})
            
            if len(model_json_files) > 1:
                return JSONResponse(status_code=400, content={"success": False, "error": "上传的文件中包含多个.model3.json文件"})
            
            model_json_file = model_json_files[0]
            
            # 确定模型根目录（.model3.json文件的父目录）
            model_root_dir = model_json_file.parent
            model_name = model_root_dir.name
            
            # 获取用户文档的live2d目录
            config_mgr = get_config_manager()
            config_mgr.ensure_live2d_directory()
            user_live2d_dir = config_mgr.live2d_dir
            
            # 目标目录
            target_model_dir = user_live2d_dir / model_name
            
            # 如果目标目录已存在，返回错误或覆盖（这里选择返回错误）
            if target_model_dir.exists():
                return JSONResponse(status_code=400, content={
                    "success": False, 
                    "error": f"模型 {model_name} 已存在，请先删除或重命名现有模型"
                })
            
            # 复制模型根目录到用户文档的live2d目录
            shutil.copytree(model_root_dir, target_model_dir)

            # 上传后：遍历模型目录中的所有动作文件（*.motion3.json），
            # 将官方白名单参数及模型自身在 .model3.json 中声明为 LipSync 的参数的 Segments 清空为 []。
            # 这样可以兼顾官方参数与模型声明的口型参数，同时忽略未声明的作者自定义命名（避免误伤）。
            try:
                import json as _json

                # 官方口型参数白名单（尽量全面列出常见和官方命名的嘴部/口型相关参数）
                # 仅包含与嘴巴形状、发音帧（A/I/U/E/O）、下颚/唇动作直接相关的参数，
                # 明确排除头部/身体/表情等其它参数（例如 ParamAngleZ、ParamAngleX 等不应在此）。
                official_mouth_params = {
                    # 五个基本发音帧（A/I/U/E/O）
                    'ParamA', 'ParamI', 'ParamU', 'ParamE', 'ParamO',
                    # 常见嘴部上下/开合/形状参数
                    'ParamMouthUp', 'ParamMouthDown', 'ParamMouthOpen', 'ParamMouthOpenY',
                    'ParamMouthForm', 'ParamMouthX', 'ParamMouthY', 'ParamMouthSmile', 'ParamMouthPucker',
                    'ParamMouthStretch', 'ParamMouthShrug', 'ParamMouthLeft', 'ParamMouthRight',
                    'ParamMouthCornerUpLeft', 'ParamMouthCornerUpRight',
                    'ParamMouthCornerDownLeft', 'ParamMouthCornerDownRight',
                    # 唇相关（部分模型/官方扩展中可能出现）
                    'ParamLipA', 'ParamLipI', 'ParamLipU', 'ParamLipE', 'ParamLipO', 'ParamLipThickness',
                    # 下颚（部分模型以下颚控制口型）
                    'ParamJawOpen', 'ParamJawForward', 'ParamJawLeft', 'ParamJawRight',
                    # 其它口型相关（保守列入）
                    'ParamMouthAngry', 'ParamMouthAngryLine'
                }

                # 尝试读取模型的 .model3.json，提取 Groups -> Name == "LipSync" && Target == "Parameter" 的 Ids
                model_declared_mouth_params = set()
                try:
                    local_model_json = target_model_dir / model_json_file.name
                    if local_model_json.exists():
                        with open(local_model_json, 'r', encoding='utf-8') as mf:
                            try:
                                model_cfg = _json.load(mf)
                                groups = model_cfg.get('Groups') if isinstance(model_cfg, dict) else None
                                if isinstance(groups, list):
                                    for grp in groups:
                                        try:
                                            if not isinstance(grp, dict):
                                                continue
                                            # 仅考虑官方 Group Name 为 LipSync 且 Target 为 Parameter 的条目
                                            if grp.get('Name') == 'LipSync' and grp.get('Target') == 'Parameter':
                                                ids = grp.get('Ids') or []
                                                for pid in ids:
                                                    if isinstance(pid, str) and pid:
                                                        model_declared_mouth_params.add(pid)
                                        except Exception:
                                            continue
                            except Exception:
                                # 解析失败则视为未找到 groups，继续使用官方白名单
                                pass
                except Exception:
                    pass

                # 合并白名单（官方 + 模型声明）
                mouth_param_whitelist = set(official_mouth_params)
                mouth_param_whitelist.update(model_declared_mouth_params)

                for motion_path in target_model_dir.rglob('*.motion3.json'):
                    try:
                        with open(motion_path, 'r', encoding='utf-8') as mf:
                            try:
                                motion_data = _json.load(mf)
                            except Exception:
                                # 非 JSON 或解析失败则跳过
                                continue

                        modified = False
                        curves = motion_data.get('Curves') if isinstance(motion_data, dict) else None
                        if isinstance(curves, list):
                            for curve in curves:
                                try:
                                    if not isinstance(curve, dict):
                                        continue
                                    cid = curve.get('Id')
                                    if not cid:
                                        continue
                                    # 严格按白名单匹配（避免模糊匹配误伤）
                                    if cid in mouth_param_whitelist:
                                        # 清空 Segments（若存在）
                                        if 'Segments' in curve and curve['Segments']:
                                            curve['Segments'] = []
                                            modified = True
                                except Exception:
                                    continue

                        if modified:
                            try:
                                with open(motion_path, 'w', encoding='utf-8') as mf:
                                    _json.dump(motion_data, mf, ensure_ascii=False, indent=4)
                                logger.info(f"已清除口型参数：{motion_path}")
                            except Exception:
                                # 写入失败则记录但不阻止上传
                                logger.exception(f"写入 motion 文件失败: {motion_path}")
                    except Exception:
                        continue
            except Exception:
                logger.exception("处理 motion 文件时发生错误")
            
            logger.info(f"成功上传Live2D模型: {model_name} -> {target_model_dir}")
            
            return JSONResponse(content={
                "success": True,
                "message": f"模型 {model_name} 上传成功",
                "model_name": model_name,
                "model_path": str(target_model_dir)
            })
            
    except Exception as e:
        logger.error(f"上传Live2D模型失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


