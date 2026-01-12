# -*- coding: utf-8 -*-
"""
VRM Router

Handles VRM model-related endpoints including:
- VRM model listing
- VRM model upload
- VRM animation listing
"""

import logging
import pathlib

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

from .shared_state import get_config_manager

router = APIRouter(prefix="/api/model/vrm", tags=["vrm"])
logger = logging.getLogger("Main")

# VRM 模型路径常量
VRM_USER_PATH = "/user_vrm"  
VRM_STATIC_PATH = "/static/vrm"
VRM_STATIC_ANIMATION_PATH = "/static/vrm/animation"  


@router.post('/upload')
async def upload_vrm_model(file: UploadFile = File(...)):
    """上传VRM模型到用户文档目录"""
    try:
        if not file:
            return JSONResponse(status_code=400, content={"success": False, "error": "没有上传文件"})
        
        # 检查文件扩展名
        filename = file.filename
        if not filename or not filename.lower().endswith('.vrm'):
            return JSONResponse(status_code=400, content={"success": False, "error": "文件必须是.vrm格式"})
        
        # 获取模型名称（去掉扩展名）
        model_name = pathlib.Path(filename).stem
        
        # 获取用户文档的vrm目录
        config_mgr = get_config_manager()
        config_mgr.ensure_vrm_directory()
        user_vrm_dir = config_mgr.vrm_dir
        
        # 目标文件路径
        target_file_path = user_vrm_dir / filename
        
        # 如果目标文件已存在，返回错误
        if target_file_path.exists():
            return JSONResponse(status_code=400, content={
                "success": False, 
                "error": f"模型 {filename} 已存在，请先删除或重命名现有模型"
            })
        
        # 保存文件
        with open(target_file_path, 'wb') as f:
            content = await file.read()
            f.write(content)
        
        logger.info(f"成功上传VRM模型: {filename} -> {target_file_path}")
        
        return JSONResponse(content={
            "success": True,
            "message": f"模型 {filename} 上传成功",
            "model_name": model_name,
            "model_path": str(target_file_path),
            "model_url": f"{VRM_USER_PATH}/{filename}"
        })
        
    except Exception as e:
        logger.error(f"上传VRM模型失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/models')
def get_vrm_models():
    """获取VRM模型列表"""
    try:
        config_mgr = get_config_manager()
        config_mgr.ensure_vrm_directory()

        models = []

        # 1. 搜索项目目录下的VRM文件 (static/vrm/)
        project_root = config_mgr._get_project_root()
        static_vrm_dir = project_root / "static" / "vrm"
        if static_vrm_dir.exists():
            for vrm_file in static_vrm_dir.glob('*.vrm'):
                models.append({
                    "name": vrm_file.stem,
                    "filename": vrm_file.name,
                    "path": str(vrm_file),
                    "url": f"/static/vrm/{vrm_file.name}", 
                    "type": "vrm",
                    "size": vrm_file.stat().st_size if vrm_file.exists() else 0,
                    "location": "project"  
                })

        # 2. 搜索用户目录下的VRM文件 (user_vrm/)
        vrm_dir = config_mgr.vrm_dir
        if vrm_dir.exists():
            for vrm_file in vrm_dir.glob('*.vrm'):
                models.append({
                    "name": vrm_file.stem,
                    "filename": vrm_file.name,
                    "path": str(vrm_file),
                    "url": f"{VRM_USER_PATH}/{vrm_file.name}",  
                    "type": "vrm",
                    "size": vrm_file.stat().st_size if vrm_file.exists() else 0,
                    "location": "user"  
                })

        return JSONResponse(content={
            "success": True,
            "models": models
        })
    except Exception as e:
        logger.error(f"获取VRM模型列表失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/animations')
def get_vrm_animations():
    """获取VRM动画文件列表（VRMA文件）"""
    try:
        config_mgr = get_config_manager()
        config_mgr.ensure_vrm_directory()
        
        # 检查animations目录
        animations_dirs = []

        # 1. 优先检查项目目录下的static/vrm/animation（实际文件位置）
        project_root = config_mgr._get_project_root()
        static_animation_dir = project_root / "static" / "vrm" / "animation"
        if static_animation_dir.exists():
            animations_dirs.append(static_animation_dir)

        # 2. 检查用户目录下的vrm/animation（兼容旧版）
        if config_mgr.vrm_animation_dir.exists():
            animations_dirs.append(config_mgr.vrm_animation_dir)
        
        animations = []
        for anim_dir in animations_dirs:
            if anim_dir.exists():
                # 根据目录确定URL前缀
                if anim_dir == static_animation_dir:
                    # static/vrm/animation 目录 -> /static/vrm/animation/
                    url_prefix = "/static/vrm/animation"
                elif anim_dir == config_mgr.vrm_animation_dir:
                    url_prefix = "/user_vrm/animation"
                else:
                    url_prefix = "/user_vrm/animation"

                # 查找.vrma文件
                for anim_file in anim_dir.glob('*.vrma'):
                    animations.append({
                        "name": anim_file.stem,
                        "filename": anim_file.name,
                        "path": str(anim_file),
                        "url": f"{url_prefix}/{anim_file.name}",
                        "type": "vrma",
                        "size": anim_file.stat().st_size if anim_file.exists() else 0
                    })
                # 也支持.vrm文件作为动画（某些情况下）
                for anim_file in anim_dir.glob('*.vrm'):
                    if anim_file not in [pathlib.Path(a["path"]) for a in animations]:
                        animations.append({
                            "name": anim_file.stem,
                            "filename": anim_file.name,
                            "path": str(anim_file),
                            "url": f"{url_prefix}/{anim_file.name}",
                            "type": "vrm",
                            "size": anim_file.stat().st_size if anim_file.exists() else 0
                        })
        
        return JSONResponse(content={
            "success": True,
            "animations": animations
        })
    except Exception as e:
        logger.error(f"获取VRM动画列表失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


# 新增配置获取接口 
@router.get('/config')
async def get_vrm_config():
    """获取前后端统一的路径配置"""
    return JSONResponse(content={
        "success": True,
        "paths": {
            "user_vrm": VRM_USER_PATH,
            "static_vrm": VRM_STATIC_PATH,
            "static_animation": VRM_STATIC_ANIMATION_PATH
        }
    })
