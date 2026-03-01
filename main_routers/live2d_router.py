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
import pathlib

from fastapi import APIRouter, Request, File, UploadFile
from fastapi.responses import JSONResponse

from .shared_state import get_config_manager
from .workshop_router import get_subscribed_workshop_items
from utils.frontend_utils import find_models, find_model_directory, find_workshop_item_by_id
from utils.logger_config import get_module_logger
from utils.url_utils import encode_url_path

router = APIRouter(prefix="/api/live2d", tags=["live2d"])
logger = get_module_logger(__name__, "Main")


def _normalize_model_path(path: str) -> str:
    """Strip any surrounding quotes, then encode a model URL path."""
    return encode_url_path(path.strip('"'))


def _upsert_model(models: list, model_name: str, item_id: str, path: str, source: str = 'steam_workshop') -> None:
    """
    Update existing model with item_id if found, otherwise append new model.
    
    Args:
        models: List of model dictionaries to update
        model_name: Name of the model
        item_id: Steam workshop item ID
        path: Model path URL
        source: Model source (default: 'steam_workshop')
    """
    existing_model = next((m for m in models if m['name'] == model_name), None)
    if existing_model:
        if not existing_model.get('item_id'):
            existing_model['item_id'] = item_id
            existing_model['source'] = source
    else:
        models.append({
            'name': model_name,
            'path': path,
            'source': source,
            'item_id': item_id
        })


def _locate_model_config(model_dir: str):
    """
    Probe *model_dir* (root) and its single-level subdirectories for a
    ``.model3.json`` file.

    Returns ``(actual_model_dir, model_config_file, subdir_name)`` on
    success, or ``(None, None, None)`` when nothing is found.
    """
    if not os.path.isdir(model_dir):
        return None, None, None

    for file in os.listdir(model_dir):
        if file.endswith('.model3.json'):
            return model_dir, file, None

    try:
        for subdir in os.listdir(model_dir):
            subdir_path = os.path.join(model_dir, subdir)
            if not os.path.isdir(subdir_path):
                continue
            for file in os.listdir(subdir_path):
                if file.endswith('.model3.json'):
                    return subdir_path, file, subdir
    except Exception as e:
        logger.warning(f"检查子目录时出错: {e}")

    return None, None, None


@router.get("/models")
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
                
                # 遍历所有物品，提取已安装的模型
                for item in items:
                    # 直接使用get_subscribed_workshop_items返回的installedFolder；从publishedFileId字段获取物品ID，而不是item_id
                    installed_folder = item.get('installedFolder')
                    item_id = item.get('publishedFileId')
                    
                    if installed_folder and os.path.exists(installed_folder) and os.path.isdir(installed_folder) and item_id:
                        # 检查安装目录下是否有.model3.json文件
                        for filename in os.listdir(installed_folder):
                            if filename.endswith('.model3.json'):
                                model_name = os.path.splitext(os.path.splitext(filename)[0])[0]
                                path_value = _normalize_model_path(f'/workshop/{item_id}/{filename}')
                                logger.debug(f"添加模型路径: {path_value!r}, item_id类型: {type(item_id)}, filename类型: {type(filename)}")
                                _upsert_model(models, model_name, item_id, path_value)
                            
                        # 检查安装目录下的子目录
                        for subdir in os.listdir(installed_folder):
                            subdir_path = os.path.join(installed_folder, subdir)
                            if os.path.isdir(subdir_path):
                                model_name = subdir
                                json_file = os.path.join(subdir_path, f'{model_name}.model3.json')
                                if os.path.exists(json_file):
                                    path_value = _normalize_model_path(f'/workshop/{item_id}/{model_name}/{model_name}.model3.json')
                                    logger.debug(f"添加子目录模型路径: {path_value!r}, item_id类型: {type(item_id)}, model_name类型: {type(model_name)}")
                                    _upsert_model(models, model_name, item_id, path_value)
        except Exception as e:
            logger.error(f"获取创意工坊模型时出错: {e}")
        
        if simple:
            # 只返回模型名称列表
            model_names = [model["name"] for model in models]
            return {"success": True, "models": model_names}
        else:
            # 返回完整的模型信息（保持向后兼容）
            for model in models:
                if isinstance(model, dict) and isinstance(model.get('path'), str):
                    model['path'] = _normalize_model_path(model['path'])
            return models
    except Exception as e:
        logger.error(f"获取Live2D模型列表失败: {e}")
        if simple:
            return {"success": False, "error": str(e)}
        else:
            return []

@router.get("/model_config/{model_name}")
def get_model_config(model_name: str):
    """
    获取指定Live2D模型的model3.json配置
    """
    try:
        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_directory(model_name)
        if not model_dir or not os.path.exists(model_dir):
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


@router.post("/model_config/{model_name}")
async def update_model_config(model_name: str, request: Request):
    """
    更新指定Live2D模型的model3.json配置
    """
    try:
        data = await request.json()
        
        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_directory(model_name)
        if not model_dir or not os.path.exists(model_dir):
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
            
        file_refs = current_config.setdefault("FileReferences", {})
        if 'FileReferences' in data and 'Motions' in data['FileReferences']:
            file_refs['Motions'] = data['FileReferences']['Motions']
            
        if 'FileReferences' in data and 'Expressions' in data['FileReferences']:
            file_refs['Expressions'] = data['FileReferences']['Expressions']

        with open(model_json_path, 'w', encoding='utf-8') as f:
            json.dump(current_config, f, ensure_ascii=False, indent=4) # 使用 indent=4 保持格式
            
        return {"success": True, "message": "模型配置已更新"}
    except Exception as e:
        logger.error(f"更新模型配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/emotion_mapping/{model_name}')
def get_emotion_mapping(model_name: str):
    """
    获取指定Live2D模型的情绪映射配置
    """
    try:
        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_directory(model_name)
        if not model_dir or not os.path.exists(model_dir):
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


@router.post('/emotion_mapping/{model_name}')
async def update_emotion_mapping(model_name: str, request: Request):
    """
    更新指定Live2D模型的情绪映射配置
    """
    try:
        data = await request.json()
        
        if not data:
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的数据"})

        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_directory(model_name)
        if not model_dir or not os.path.exists(model_dir):
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
                normalized = file_path.replace('\\', '/')
                p = pathlib.PurePosixPath(normalized)
                if p.is_absolute() or ".." in p.parts:
                    continue
                normalized = str(p)

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
                normalized = file_path.replace('\\', '/')
                p = pathlib.PurePosixPath(normalized)
                if p.is_absolute() or ".." in p.parts:
                    continue
                normalized = str(p)

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


@router.get('/model_files/{model_name}')
def get_model_files(model_name: str):
    """
    获取指定Live2D模型的动作和表情文件列表
    """
    try:
        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_directory(model_name)
        
        if not model_dir or not os.path.exists(model_dir):
            return {"success": False, "error": f"模型 {model_name} 不存在"}
        
        actual_model_dir, model_config_file, _model_name_subdir = _locate_model_config(model_dir)

        if not model_config_file:
            logger.error(
                "模型 %s 未找到 .model3.json 文件，model_dir=%s, actual_model_dir=%s",
                model_name,
                model_dir,
                actual_model_dir,
            )
            return {"success": False, "error": "模型配置文件(.model3.json)不存在"}

        model_dir = actual_model_dir
        motion_files = []
        expression_files = []

        # 递归搜索所有子文件夹
        def search_files_recursive(directory, target_ext, result_list):
            """
            递归搜索指定扩展名的文件
            args:
            - directory: 搜索目录
            - target_ext: 目标文件扩展名
            - result_list: 存储结果的列表
            """
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
        
        logger.debug(f"模型 {model_name} 文件统计: {len(motion_files)} 个动作文件, {len(expression_files)} 个表情文件")
        return {
            "success": True, 
            "motion_files": motion_files,
            "expression_files": expression_files
        }
    except Exception as e:
        logger.error(f"获取模型文件列表失败: {e}")
        return {"success": False, "error": str(e)}


@router.get('/model_parameters/{model_name}')
def get_model_parameters(model_name: str):
    """
    获取指定Live2D模型的参数信息（从.cdi3.json文件）
    args:
    - model_name: 模型名称（不带路径和扩展名）
    returns:
    - success: 是否成功获取参数信息
    - parameters: 参数列表，每个参数包含id、groupId和name
    - parameter_groups: 参数组列表，每个组包含id和name
    """
    try:
        # 查找模型目录
        model_dir, url_prefix = find_model_directory(model_name)
        
        if not model_dir or not os.path.exists(model_dir):
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


@router.post('/save_model_parameters/{model_name}')
async def save_model_parameters(model_name: str, request: Request):
    """
    保存模型参数到模型目录的parameters.json文件
    args:
    - model_name: 模型名称（不带路径和扩展名）
    - request: 请求体，包含参数信息
    """
    try:
        # 查找模型目录
        model_dir, url_prefix = find_model_directory(model_name)
        
        if not model_dir or not os.path.exists(model_dir):
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


@router.get('/load_model_parameters/{model_name}')
def load_model_parameters(model_name: str):
    """
    从模型目录的parameters.json文件加载参数
    args:
    - model_name: 模型名称（不带路径和扩展名）
    returns:
    - success: 是否成功加载参数
    - parameters: 加载的参数字典
    """
    try:
        # 查找模型目录
        model_dir, url_prefix = find_model_directory(model_name)
        
        if not model_dir or not os.path.exists(model_dir):
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


@router.get("/model_config_by_id/{model_id}")
def get_model_config_by_id(model_id: str):
    """
    获取指定Live2D模型的model3.json配置
    args:
    - model_id: 模型ID（从workshop.json中获取）
    returns:
    - success: 是否成功获取配置
    - config: 模型配置字典
    """
    try:
        # 查找模型目录（可能在static或用户文档目录）
        try:
            model_dir, url_prefix = find_workshop_item_by_id(model_id)
            logger.debug(f"通过model_id {model_id} 查找模型目录: {model_dir}")
        except Exception as e:
            model_dir = ""
            logger.warning(f"通过model_id查找失败: {e}")

        if not model_dir or not os.path.exists(model_dir):
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


@router.post("/model_config_by_id/{model_id}")
async def update_model_config_by_id(model_id: str, request: Request):
    """
    更新指定Live2D模型的model3.json配置
    args:
    - model_id: 模型ID（从workshop.json中获取）
    - request: 请求体，包含更新的配置信息
    returns:
    - success: 是否成功更新配置
    - config: 更新后的模型配置字典
    """
    try:
        data = await request.json()
        
        # 查找模型目录（可能在static或用户文档目录）
        try:
            model_dir, url_prefix = find_workshop_item_by_id(model_id)
            logger.debug(f"通过model_id {model_id} 查找模型目录: {model_dir}")
        except Exception as e:
            model_dir = ""
            logger.warning(f"通过model_id查找失败: {e}")

        if not model_dir or not os.path.exists(model_dir):
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
            
        file_refs = current_config.setdefault("FileReferences", {})
        if 'FileReferences' in data and 'Motions' in data['FileReferences']:
            file_refs['Motions'] = data['FileReferences']['Motions']
            
        if 'FileReferences' in data and 'Expressions' in data['FileReferences']:
            file_refs['Expressions'] = data['FileReferences']['Expressions']

        with open(model_json_path, 'w', encoding='utf-8') as f:
            json.dump(current_config, f, ensure_ascii=False, indent=4) # 使用 indent=4 保持格式
            
        return {"success": True, "message": "模型配置已更新"}
    except Exception as e:
        logger.error(f"更新模型配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/model_files_by_id/{model_id}')
def get_model_files_by_id(model_id: str):
    """
    获取指定Live2D模型的动作和表情文件列表
    args:
    - model_id: 模型ID（从workshop.json中获取）
    returns:
    - success: 是否成功获取文件列表
    - motion_files: 动作文件列表
    - expression_files: 表情文件列表
    """
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
            logger.error("获取模型目录失败: 目录路径为空")
            return {"success": False, "error": "获取模型目录失败: 无效的路径"}
            
        if not os.path.exists(model_dir):
            logger.warning(f"模型目录不存在: {model_dir}")
            return {"success": False, "error": "模型不存在"}

        actual_model_dir, model_config_file, model_name_subdir = _locate_model_config(model_dir)

        if not model_config_file:
            logger.warning(f"未找到模型 {model_id} 的 .model3.json 文件: {model_dir}")
            return {"success": False, "error": "未找到模型配置文件(.model3.json)"}
        
        motion_files = []
        expression_files = []
        
        # 递归搜索所有子文件夹
        def search_files_recursive(directory, target_ext, result_list):
            """
            递归搜索指定扩展名的文件
            args:
            - directory: 要搜索的目录路径
            - target_ext: 目标文件扩展名（如'.motion3.json'）
            - result_list: 存储找到的文件路径的列表
            """
            try:
                for item in os.listdir(directory):
                    item_path = os.path.join(directory, item)
                    if os.path.isfile(item_path):
                        if item.endswith(target_ext):
                            # 计算相对于模型根目录的路径
                            relative_path = os.path.relpath(item_path, actual_model_dir)
                            # 转换为正斜杠格式（跨平台兼容）
                            relative_path = relative_path.replace('\\', '/')
                            result_list.append(relative_path)
                    elif os.path.isdir(item_path):
                        # 递归搜索子目录
                        search_files_recursive(item_path, target_ext, result_list)
            except Exception as e:
                logger.warning(f"搜索目录 {directory} 时出错: {e}")
        
        # 搜索动作文件
        search_files_recursive(actual_model_dir, '.motion3.json', motion_files)

        # 搜索表情文件
        search_files_recursive(actual_model_dir, '.exp3.json', expression_files)
        
        # 构建模型配置文件的URL
        model_config_url = None
        if url_prefix:
            # 对于workshop模型，需要根据实际路径结构构建URL
            if url_prefix == '/workshop':
                if model_name_subdir:
                    # 模型在子目录中：workshop/{item_id}/{model_name}/{model_name}.model3.json
                    model_config_url = encode_url_path(f"{url_prefix}/{model_id}/{model_name_subdir}/{model_config_file}")
                else:
                    # 模型直接在item目录中：workshop/{item_id}/{model_name}.model3.json
                    model_config_url = encode_url_path(f"{url_prefix}/{model_id}/{model_config_file}")
            else:
                model_config_url = encode_url_path(f"{url_prefix}/{model_config_file}")
            logger.debug(f"为模型 {model_id} 构建的配置URL: {model_config_url} (模型子目录: {model_name_subdir})")
        
        logger.debug(f"文件统计: {len(motion_files)} 个动作文件, {len(expression_files)} 个表情文件")
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

@router.post('/upload_model')
async def upload_live2d_model(files: list[UploadFile] = File(...)):
    """
    上传Live2D模型到用户文档目录
    """
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
                if not file_path or file_path.startswith(("/", "../")) or "/../" in f"/{file_path}":
                    return JSONResponse(status_code=400, content={"success": False, "error": "非法文件路径"})
                
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


@router.post('/upload_file/{model_name}')
async def upload_file_to_model(model_name: str, file: UploadFile = File(...), file_type: str = "motion"):
    """
    上传单个动作或表情文件到指定模型
    args:
    - model_name: 模型名称（不带路径和扩展名）
    - file: 上传的文件对象
    - file_type: 文件类型，必须是 "motion" 或 "expression"
    """
    try:
        if not file:
            return JSONResponse(status_code=400, content={"success": False, "error": "没有上传文件"})
        
        # 限制文件大小 (例如 50MB)
        MAX_UPLOAD_SIZE = 50 * 1024 * 1024
        chunk_size = 64 * 1024
        file_content = bytearray()
        try:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                file_content.extend(chunk)
                if len(file_content) > MAX_UPLOAD_SIZE:
                    return JSONResponse(status_code=400, content={"success": False, "error": f"文件过大，最大允许 {MAX_UPLOAD_SIZE // (1024*1024)}MB"})
        finally:
            await file.close()
        
        # 验证文件类型和 JSON 格式
        filename = file.filename
        if file_type == "motion":
            if not filename or not filename.lower().endswith('.motion3.json'):
                return JSONResponse(status_code=400, content={"success": False, "error": "动作文件必须是.motion3.json格式"})
            target_subdir = "motions"
        elif file_type == "expression":
            if not filename or not filename.lower().endswith('.exp3.json'):
                return JSONResponse(status_code=400, content={"success": False, "error": "表情文件必须是.exp3.json格式"})
            target_subdir = "expressions"
        else:
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的文件类型，必须是motion或expression"})
        
        # 验证 JSON 格式
        try:
            json.loads(file_content.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse(status_code=400, content={"success": False, "error": "文件内容不是有效的 JSON 格式"})

        # 查找模型目录
        model_dir, _url_prefix = find_model_directory(model_name)
        if not model_dir or not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # 创建目标子目录（如果不存在）
        target_dir = pathlib.Path(model_dir) / target_subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # 只取文件名，避免路径穿越
        safe_filename = pathlib.Path(filename).name
        
        target_file_path = target_dir / safe_filename
        try:
            with open(target_file_path, 'xb') as f:
                f.write(file_content)
        except FileExistsError:
            return JSONResponse(status_code=400, content={"success": False, "error": f"文件 {safe_filename} 已存在"})
        
        logger.info(f"成功上传{file_type}文件到模型 {model_name}: {safe_filename}")
        
        return JSONResponse(content={
            "success": True,
            "message": f"文件 {safe_filename} 上传成功",
            "filename": safe_filename,
            "file_path": str(target_file_path.relative_to(model_dir))
        })
        
    except Exception as e:
        logger.exception(f"上传文件失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/open_model_directory/{model_name}')
def open_model_directory(model_name: str):
    """
    打开指定Live2D模型的目录
    args:
    - model_name: 模型名称（不带路径和扩展名）
    """
    try:
        import sys
        # 查找模型目录
        model_dir, url_prefix = find_model_directory(model_name)
        
        if not model_dir or not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": f"模型目录不存在: {model_dir}"})
        
        # 使用os.startfile在Windows上打开目录
        if os.name == 'nt':  # Windows
            os.startfile(model_dir)
        elif os.name == 'posix':  # macOS or Linux
            import subprocess
            subprocess.Popen(['open', model_dir]) if sys.platform == 'darwin' else subprocess.Popen(['xdg-open', model_dir])
        
        return {"success": True, "message": f"已打开模型目录: {model_dir}", "directory": model_dir}
    except Exception as e:
        logger.error(f"打开模型目录失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.delete('/model/{model_name}')
def delete_model(model_name: str):
    """
    删除指定的Live2D模型
    args:
    - model_name: 模型名称（不带路径和扩展名）
    """
    try:
        # 查找模型目录
        model_dir, _url_prefix = find_model_directory(model_name)
        
        # 如果 find_model_directory 找不到，尝试直接在用户导入目录下查找（作为删除的降级路径）
        if not model_dir or not os.path.exists(model_dir):
            try:
                config_mgr = get_config_manager()
                config_mgr.ensure_live2d_directory()
                fallback_dir = config_mgr.live2d_dir / model_name
                if fallback_dir.exists():
                    # 验证路径安全（防止路径遍历）
                    fallback_real = os.path.realpath(str(fallback_dir))
                    live2d_real = os.path.realpath(str(config_mgr.live2d_dir))
                    if os.path.commonpath([fallback_real, live2d_real]) == live2d_real:
                        model_dir = str(fallback_dir)
                        _url_prefix = '/user_live2d'
                        logger.info(f"通过降级路径找到用户模型: {model_dir}")
            except Exception as e:
                logger.warning(f"降级查找用户模型时出错: {e}")
        
        if not model_dir or not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": f"模型 {model_name} 不存在"})
        
        # 检查是否是用户导入的模型（只能删除用户导入的模型，不能删除内置模型）
        is_user_model = False
        
        # 检查是否在用户文档目录下
        try:
            config_mgr = get_config_manager()
            config_mgr.ensure_live2d_directory()
            user_live2d_dir = os.path.realpath(str(config_mgr.live2d_dir))
            model_dir_real = os.path.realpath(model_dir)
            try:
                common = os.path.commonpath([user_live2d_dir, model_dir_real])
                if common == user_live2d_dir:
                    is_user_model = True
            except ValueError:
                # 不同驱动器/根目录的情况
                pass
        except Exception as e:
            logger.warning(f"检查用户模型目录时出错: {e}")
        
        if not is_user_model:
            return JSONResponse(status_code=403, content={"success": False, "error": "只能删除用户导入的模型，无法删除内置模型"})
        
        # 再次检查路径是否存在
        if not os.path.exists(model_dir):
            logger.info(f"模型目录不存在，视为已删除: {model_name}")
            return {"success": True, "message": f"模型 {model_name} 已成功删除"}
        
        # 递归删除模型目录
        import shutil
        shutil.rmtree(model_dir, ignore_errors=True)
        
        # 验证删除是否成功
        if os.path.exists(model_dir):
            logger.warning(f"删除后文件夹仍存在: {model_dir}，可能被占用或权限不足")
            return JSONResponse(status_code=500, content={"success": False, "error": f"删除模型失败，文件夹仍存在: {model_dir}"})
        else:
            logger.info(f"已删除Live2D模型: {model_name}")
            return {"success": True, "message": f"模型 {model_name} 已成功删除"}
    except Exception as e:
        logger.error(f"删除模型失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/user_models')
def get_user_models():
    """
    获取用户导入的模型列表  
    """
    try:
        user_models = []
        
        # 获取用户文档目录下的live2d模型
        try:
            config_mgr = get_config_manager()
            config_mgr.ensure_live2d_directory()
            docs_live2d_dir = str(config_mgr.live2d_dir)
            if os.path.exists(docs_live2d_dir):
                for root, dirs, files in os.walk(docs_live2d_dir):
                    for file in files:
                        if file.endswith('.model3.json'):
                            model_name = os.path.basename(root)
                            rel_path = os.path.relpath(root, docs_live2d_dir)
                            # Normalize '.' to empty string to avoid '/user_live2d/./' paths
                            if rel_path == '.':
                                rel_path_posix = ''
                            else:
                                rel_path_posix = pathlib.Path(rel_path).as_posix()
                            # Build path without duplicate slash
                            if rel_path_posix:
                                path = f'/user_live2d/{rel_path_posix}/{file}'
                            else:
                                path = f'/user_live2d/{file}'
                            user_models.append({
                                'name': model_name,
                                'path': path,
                                'source': 'user_documents'
                            })
                            # Prune deeper traversal after finding a .model3.json
                            dirs[:] = []
        except Exception as e:
            logger.warning(f"扫描用户文档模型目录时出错: {e}")
        
        return {"success": True, "models": user_models}
    except Exception as e:
        logger.error(f"获取用户模型列表失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


