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
import shutil
import tempfile
from typing import List

from fastapi import APIRouter, Request, File, UploadFile
from fastapi.responses import JSONResponse

from .shared_state import get_config_manager, get_initialize_character_data, get_steamworks
from utils.frontend_utils import find_models, find_model_directory, find_model_config_file, find_model_by_workshop_item_id, find_workshop_item_by_id

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
            from .workshop_router import get_subscribed_workshop_items
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
    """向后兼容的API端点"""
    return await get_live2d_models(simple=False)


@router.get('/api/characters/current_live2d_model')
async def get_current_live2d_model(catgirl_name: str = "", item_id: str = ""):
    """获取指定角色或当前角色的Live2D模型信息"""
    _config_manager = get_config_manager()
    
    try:
        characters = _config_manager.load_characters()
        
        if not catgirl_name:
            catgirl_name = characters.get('当前猫娘', '')
        
        live2d_model_name = None
        model_info = None
        
        # 尝试通过item_id查找
        if item_id:
            try:
                all_models = find_models()
                matching_model = next((m for m in all_models if m.get('item_id') == item_id), None)
                if matching_model:
                    model_info = matching_model.copy()
                    live2d_model_name = model_info['name']
            except Exception as e:
                logger.warning(f"通过item_id查找模型失败: {e}")
        
        # 通过角色名称查找
        if not model_info and catgirl_name:
            if '猫娘' in characters and catgirl_name in characters['猫娘']:
                catgirl_data = characters['猫娘'][catgirl_name]
                live2d_model_name = catgirl_data.get('live2d')
                saved_item_id = catgirl_data.get('live2d_item_id')
                
                if saved_item_id:
                    try:
                        all_models = find_models()
                        matching_model = next((m for m in all_models if m.get('item_id') == saved_item_id), None)
                        if matching_model:
                            model_info = matching_model.copy()
                            live2d_model_name = model_info['name']
                    except Exception as e:
                        logger.warning(f"通过保存的item_id查找模型失败: {e}")
        
        # 获取模型信息
        if live2d_model_name and not model_info:
            try:
                all_models = find_models()
                matching_model = next((m for m in all_models if m['name'] == live2d_model_name), None)
                if matching_model:
                    model_info = matching_model.copy()
            except Exception as e:
                logger.warning(f"获取模型信息失败: {e}")
        
        # 回退到默认模型
        if not live2d_model_name or not model_info:
            live2d_model_name = 'mao_pro'
            try:
                all_models = find_models()
                matching_model = next((m for m in all_models if m['name'] == 'mao_pro'), None)
                if matching_model:
                    model_info = matching_model.copy()
                    model_info['is_fallback'] = True
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
    _config_manager = get_config_manager()
    initialize_character_data = get_initialize_character_data()
    
    try:
        data = await request.json()
        live2d_model = data.get('live2d')
        item_id = data.get('item_id')
        
        if not live2d_model:
            return JSONResponse(content={
                'success': False,
                'error': '未提供Live2D模型名称'
            })
        
        characters = _config_manager.load_characters()
        
        if '猫娘' not in characters:
            characters['猫娘'] = {}
        
        if name not in characters['猫娘']:
            characters['猫娘'][name] = {}
        
        characters['猫娘'][name]['live2d'] = live2d_model
        if item_id:
            characters['猫娘'][name]['live2d_item_id'] = item_id
        
        _config_manager.save_characters(characters)
        if initialize_character_data:
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
    """更新猫娘的voice_id"""
    _config_manager = get_config_manager()
    session_manager_getter = lambda: __import__('main_routers.shared_state', fromlist=['get_session_manager']).get_session_manager()
    session_manager = session_manager_getter()
    initialize_character_data = get_initialize_character_data()
    
    data = await request.json()
    if not data:
        return JSONResponse({'success': False, 'error': '无数据'}, status_code=400)
    
    characters = _config_manager.load_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    
    if 'voice_id' in data:
        voice_id = data['voice_id']
        if not _config_manager.validate_voice_id(voice_id):
            voices = _config_manager.get_voices_for_current_api()
            return JSONResponse({
                'success': False, 
                'error': f'voice_id "{voice_id}" 在当前API的音色库中不存在',
                'available_voices': list(voices.keys())
            }, status_code=400)
        characters['猫娘'][name]['voice_id'] = voice_id
    
    _config_manager.save_characters(characters)
    
    is_current_catgirl = (name == characters.get('当前猫娘', ''))
    session_ended = False
    
    if is_current_catgirl and name in session_manager:
        if session_manager[name].is_active:
            if session_manager[name].websocket:
                try:
                    await session_manager[name].websocket.send_text(json.dumps({
                        "type": "reload_page",
                        "message": "语音已更新，页面即将刷新"
                    }))
                except Exception as e:
                    logger.warning(f"通知前端刷新页面失败: {e}")
            
            try:
                await session_manager[name].end_session(by_server=True)
                session_ended = True
            except Exception as e:
                logger.error(f"结束session时出错: {e}")
    
    if is_current_catgirl and initialize_character_data:
        await initialize_character_data()
    
    return {"success": True, "session_restarted": session_ended}


@router.get('/api/live2d/model_config/{model_name}')
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


@router.post('/api/live2d/model_config/{model_name}')
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
            json.dump(current_config, f, ensure_ascii=False, indent=4)
            
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
        
        model_dir, _ = find_model_directory(model_name)
        mapping_file = os.path.join(model_dir, 'emotion_mapping.json')
        
        with open(mapping_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        return JSONResponse(content={
            'success': True,
            'message': '情绪映射已更新'
        })
        
    except Exception as e:
        logger.error(f"更新情绪映射失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        })


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
async def get_model_config_by_id(model_id: str):
    """获取指定Live2D模型的model3.json配置（通过workshop item_id）"""
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
async def update_model_config_by_id(model_id: str, request: Request):
    """更新指定Live2D模型的model3.json配置（通过workshop item_id）"""
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
            json.dump(current_config, f, ensure_ascii=False, indent=4)
            
        return {"success": True, "message": "模型配置已更新"}
    except Exception as e:
        logger.error(f"更新模型配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/api/live2d/model_files_by_id/{model_id}')
async def get_model_files_by_id(model_id: str):
    """获取指定Live2D模型的动作和表情文件列表（通过workshop item_id）"""
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


@router.post('/api/live2d/upload_model')
async def upload_live2d_model(files: List[UploadFile] = File(...)):
    """上传Live2D模型到用户文档目录"""
    _config_manager = get_config_manager()
    
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
            _config_manager.ensure_live2d_directory()
            user_live2d_dir = _config_manager.live2d_dir
            
            # 检查模型是否已存在
            target_model_dir = user_live2d_dir / model_name
            if target_model_dir.exists():
                # 删除已存在的模型目录
                shutil.rmtree(target_model_dir)
                logger.info(f"删除已存在的模型目录: {target_model_dir}")
            
            # 复制模型到用户文档目录
            shutil.copytree(model_root_dir, target_model_dir)
            logger.info(f"模型已复制到: {target_model_dir}")
            
            # 构建模型URL路径
            model_config_file = model_json_file.name
            model_url = f"/user_live2d/{model_name}/{model_config_file}"
            
            return {
                "success": True,
                "message": f"模型 {model_name} 上传成功",
                "model_name": model_name,
                "model_path": model_url
            }
            
    except Exception as e:
        logger.error(f"上传Live2D模型失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})
