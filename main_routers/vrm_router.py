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
import asyncio
from pathlib import Path

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

from .shared_state import get_config_manager

router = APIRouter(prefix="/api/model/vrm", tags=["vrm"])
logger = logging.getLogger("Main")

# VRM 模型路径常量
VRM_USER_PATH = "/user_vrm"  
VRM_STATIC_PATH = "/static/vrm"
VRM_STATIC_ANIMATION_PATH = "/static/vrm/animation"

# 文件上传常量
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200MB
CHUNK_SIZE = 1024 * 1024  # 1MB chunks for streaming


def safe_vrm_path(vrm_dir: Path, filename: str) -> tuple[Path | None, str]:
    """安全地构造和验证 VRM 目录内的路径，防止路径穿越攻击。"""
    try:
        # 使用 pathlib 构造路径
        target_path = vrm_dir / filename
        
        # 解析为绝对路径（解析 ..、符号链接等）
        resolved_path = target_path.resolve()
        resolved_vrm_dir = vrm_dir.resolve()
        
        # 验证解析后的路径在 vrm_dir 内
        try:
            if not resolved_path.is_relative_to(resolved_vrm_dir):
                return None, "路径越界：目标路径不在允许的目录内"
        except AttributeError:
            # Python < 3.9 的回退方案
            try:
                resolved_path.relative_to(resolved_vrm_dir)
            except ValueError:
                return None, "路径越界：目标路径不在允许的目录内"
        
        # 确保路径是文件而不是目录
        if resolved_path.exists() and resolved_path.is_dir():
            return None, "目标路径是目录，不是文件"
        
        return resolved_path, ""
    except Exception as e:
        return None, f"路径验证失败: {str(e)}"  


@router.post('/upload')
async def upload_vrm_model(file: UploadFile = File(...)):
    """上传VRM模型到用户文档目录（使用流式读取和异步写入，防止路径穿越）"""
    try:
        if not file:
            return JSONResponse(status_code=400, content={"success": False, "error": "没有上传文件"})
        
        # 检查文件扩展名
        filename = file.filename
        if not filename or not filename.lower().endswith('.vrm'):
            return JSONResponse(status_code=400, content={"success": False, "error": "文件必须是.vrm格式"})
        
        # 获取用户文档的vrm目录
        config_mgr = get_config_manager()
        config_mgr.ensure_vrm_directory()
        user_vrm_dir = config_mgr.vrm_dir
        
        # 使用安全路径函数防止路径穿越
        target_file_path, path_error = safe_vrm_path(user_vrm_dir, filename)
        if target_file_path is None:
            logger.warning(f"路径穿越尝试被阻止: {filename!r} - {path_error}")
            return JSONResponse(status_code=400, content={
                "success": False,
                "error": path_error
            })
        
        # 如果目标文件已存在，返回错误
        if target_file_path.exists():
            return JSONResponse(status_code=400, content={
                "success": False, 
                "error": f"模型 {filename} 已存在，请先删除或重命名现有模型"
            })
        
        # 流式读取文件，在读取过程中检查大小，避免一次性加载到内存
        total_size = 0
        chunks = []
        
        try:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                
                total_size += len(chunk)
                
                # 在每次迭代中检查文件大小，避免读取过大文件
                if total_size > MAX_FILE_SIZE:
                    logger.warning(f"文件过大: {filename} ({total_size / (1024*1024):.2f}MB > {MAX_FILE_SIZE / (1024*1024)}MB)")
                    return JSONResponse(status_code=400, content={
                        "success": False,
                        "error": f"文件过大，最大允许 {MAX_FILE_SIZE // (1024*1024)}MB，当前文件大小: {total_size // (1024*1024)}MB"
                    })
                
                chunks.append(chunk)
        except Exception as e:
            logger.error(f"读取上传文件失败: {e}")
            return JSONResponse(status_code=500, content={
                "success": False,
                "error": f"读取文件失败: {str(e)}"
            })
        
        # 使用异步写入，避免阻塞 I/O
        try:
            # 使用 asyncio.to_thread 在线程池中执行阻塞写入操作
            def write_file():
                with open(target_file_path, 'wb') as f:
                    for chunk in chunks:
                        f.write(chunk)
            
            await asyncio.to_thread(write_file)
        except Exception as e:
            logger.error(f"写入文件失败: {e}")
            # 如果写入失败，尝试清理已创建的文件
            if target_file_path.exists():
                try:
                    target_file_path.unlink()
                except:
                    pass
            return JSONResponse(status_code=500, content={
                "success": False,
                "error": f"保存文件失败: {str(e)}"
            })
        
        # 获取模型名称（去掉扩展名）
        model_name = Path(filename).stem
        
        logger.info(f"成功上传VRM模型: {filename} -> {target_file_path} (大小: {total_size / (1024*1024):.2f}MB)")
        
        return JSONResponse(content={
            "success": True,
            "message": f"模型 {filename} 上传成功",
            "model_name": model_name,
            "model_url": f"{VRM_USER_PATH}/{filename}",
            "file_size": total_size
        })
        
    except Exception as e:
        logger.error(f"上传VRM模型失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/models')
def get_vrm_models():
    """获取VRM模型列表（不暴露绝对文件系统路径）"""
    try:
        config_mgr = get_config_manager()
        config_mgr.ensure_vrm_directory()

        models = []
        seen_urls = set()  # 使用 set 避免重复（基于 URL）

        # 1. 搜索项目目录下的VRM文件 (static/vrm/)
        project_root = config_mgr._get_project_root()
        static_vrm_dir = project_root / "static" / "vrm"
        if static_vrm_dir.exists():
            for vrm_file in static_vrm_dir.glob('*.vrm'):
                url = f"/static/vrm/{vrm_file.name}"
                # 跳过已存在的 URL（避免重复）
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                
                # 移除绝对路径，只返回公共 URL 和相对信息
                if vrm_file.exists():
                    models.append({
                        "name": vrm_file.stem,
                        "filename": vrm_file.name,
                        "url": url,
                        "type": "vrm",
                        "size": vrm_file.stat().st_size,
                        "location": "project"  
                    })

        # 2. 搜索用户目录下的VRM文件 (user_vrm/)
        vrm_dir = config_mgr.vrm_dir
        if vrm_dir.exists():
            for vrm_file in vrm_dir.glob('*.vrm'):
                url = f"{VRM_USER_PATH}/{vrm_file.name}"
                # 跳过已存在的 URL（避免重复）
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                
                # 移除绝对路径，只返回公共 URL 和相对信息
                if vrm_file.exists():
                    models.append({
                        "name": vrm_file.stem,
                        "filename": vrm_file.name,
                        "url": url,
                        "type": "vrm",
                        "size": vrm_file.stat().st_size,
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
    """获取VRM动画文件列表（VRMA文件，不暴露绝对文件系统路径）"""
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
        seen_urls = set()  # 【修复】使用 set 存储已见过的 URL，O(1) 查找，避免 O(n²) 列表检查
        
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
                    url = f"{url_prefix}/{anim_file.name}"
                    # 使用 set 去重，基于 URL（逻辑路径）而不是绝对路径
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    
                    # 移除绝对路径，只返回公共 URL 和相对信息
                    if anim_file.exists():
                        animations.append({
                            "name": anim_file.stem,
                            "filename": anim_file.name,
                            "url": url,
                            "type": "vrma",
                            "size": anim_file.stat().st_size
                        })
                
                # 也支持.vrm文件作为动画（某些情况下）
                for anim_file in anim_dir.glob('*.vrm'):
                    url = f"{url_prefix}/{anim_file.name}"
                    # 使用 set 去重，基于 URL（逻辑路径）
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    
                    # 移除绝对路径，只返回公共 URL 和相对信息
                    if anim_file.exists():
                        animations.append({
                            "name": anim_file.stem,
                            "filename": anim_file.name,
                            "url": url,
                            "type": "vrm",
                            "size": anim_file.stat().st_size
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
