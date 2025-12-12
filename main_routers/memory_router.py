# -*- coding: utf-8 -*-
"""
Memory Router

Handles memory-related endpoints including:
- Recent files listing
- Memory review configuration
"""

import os
import json
import glob
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .shared_state import get_config_manager

router = APIRouter(prefix="/api/memory", tags=["memory"])
logger = logging.getLogger("Main")


@router.get('/recent_files')
async def get_recent_files():
    """获取 memory 目录下所有 recent*.json 文件名列表"""
    _config_manager = get_config_manager()
    files = glob.glob(str(_config_manager.memory_dir / 'recent*.json'))
    file_names = [os.path.basename(f) for f in files]
    return {"files": file_names}


@router.get('/recent_file')
async def get_recent_file(filename: str):
    """获取指定 recent*.json 文件内容"""
    _config_manager = get_config_manager()
    file_path = str(_config_manager.memory_dir / filename)
    if not (filename.startswith('recent') and filename.endswith('.json')):
        return JSONResponse({"success": False, "error": "文件名不合法"}, status_code=400)
    if not os.path.exists(file_path):
        return JSONResponse({"success": False, "error": "文件不存在"}, status_code=404)
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return {"content": content}


@router.get('/review_config')
async def get_review_config():
    """获取记忆整理配置"""
    try:
        _config_manager = get_config_manager()
        config_path = str(_config_manager.get_config_path('core_config.json'))
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                # 如果配置中没有这个键，默认返回True（开启）
                return {"enabled": config_data.get('recent_memory_auto_review', True)}
        else:
            # 如果配置文件不存在，默认返回True（开启）
            return {"enabled": True}
    except Exception as e:
        logger.error(f"读取记忆整理配置失败: {e}")
        return {"enabled": True}


@router.post('/review_config')
async def update_review_config(request: Request):
    """更新记忆整理配置"""
    try:
        data = await request.json()
        enabled = data.get('enabled', True)
        
        _config_manager = get_config_manager()
        config_path = str(_config_manager.get_config_path('core_config.json'))
        config_data = {}
        
        # 读取现有配置
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
        
        # 更新配置
        config_data['recent_memory_auto_review'] = enabled
        
        # 保存配置
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"记忆整理配置已更新: enabled={enabled}")
        return {"success": True, "enabled": enabled}
    except Exception as e:
        logger.error(f"更新记忆整理配置失败: {e}")
        return {"success": False, "error": str(e)}


