# -*- coding: utf-8 -*-
"""
VRM Router

Handles VRM model-related endpoints including:
- VRM model listing
- VRM model upload
- VRM animation listing
"""

import logging
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
        
        # 只取文件名，避免上传时夹带子目录
        filename = Path(filename).name
        
        # 获取用户文档的vrm目录
        config_mgr = get_config_manager()
        if not config_mgr.ensure_vrm_directory():
            return JSONResponse(status_code=500, content={"success": False, "error": "VRM目录创建失败"})
        user_vrm_dir = config_mgr.vrm_dir
        
        # 使用安全路径函数防止路径穿越
        target_file_path, path_error = safe_vrm_path(user_vrm_dir, filename)
        if target_file_path is None:
            logger.warning(f"路径穿越尝试被阻止: {filename!r} - {path_error}")
            return JSONResponse(status_code=400, content={
                "success": False,
                "error": path_error
            })
        
        # 边读边写，避免将整个文件加载到内存
        total_size = 0
        try:
            # 使用 'xb' 模式：原子操作，如果文件已存在会抛出 FileExistsError
            # 这样可以避免 TOCTOU (Time-of-check Time-of-use) 竞态条件
            with open(target_file_path, 'xb') as f:
                while True:
                    chunk = await file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > MAX_FILE_SIZE:
                        raise ValueError("FILE_TOO_LARGE")
                    f.write(chunk)
        except FileExistsError:
            return JSONResponse(status_code=400, content={
                "success": False,
                "error": f"模型 {filename} 已存在，请先删除或重命名现有模型"
            })
        except ValueError as ve:
            if str(ve) == "FILE_TOO_LARGE":
                # 如果文件过大，尝试清理已创建的文件
                try:
                    target_file_path.unlink(missing_ok=True)
                except Exception:
                    pass
                logger.warning(f"文件过大: {filename} ({total_size / (1024*1024):.2f}MB > {MAX_FILE_SIZE / (1024*1024)}MB)")
                return JSONResponse(status_code=400, content={
                    "success": False,
                    "error": f"文件过大，最大允许 {MAX_FILE_SIZE // (1024*1024)}MB"
                })
            raise
        except Exception as e:
            logger.error(f"读取或写入上传文件失败: {e}")
            # 如果写入失败，尝试清理已创建的文件
            try:
                target_file_path.unlink(missing_ok=True)
            except Exception:
                pass
            return JSONResponse(status_code=500, content={
                "success": False,
                "error": f"保存文件失败: {str(e)}"
            })
        finally:
            # 确保文件流关闭
            try:
                await file.close()
            except Exception:
                pass
        
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
        project_root = config_mgr.project_root
        static_vrm_dir = project_root / "static" / "vrm"
        if static_vrm_dir.exists():
            for vrm_file in static_vrm_dir.glob('*.vrm'):
                url = f"/static/vrm/{vrm_file.name}"
                # 跳过已存在的 URL（避免重复）
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                
                # 移除绝对路径，只返回公共 URL 和相对信息
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
        try:
            config_mgr.ensure_vrm_directory()
        except Exception as ensure_error:
            logger.warning(f"确保VRM目录失败（继续尝试）: {ensure_error}")
        
        # 检查animations目录
        animations_dirs = []
        static_animation_dir = None
        user_animation_dir = None

        # 1. 优先检查项目目录下的static/vrm/animation（实际文件位置）
        try:
            project_root = config_mgr.project_root
            static_animation_dir = project_root / "static" / "vrm" / "animation"
            if static_animation_dir.exists() and static_animation_dir.is_dir():
                animations_dirs.append(static_animation_dir)
                logger.debug(f"找到静态动画目录: {static_animation_dir}")
            else:
                logger.debug(f"静态动画目录不存在或不是目录: {static_animation_dir}")
        except Exception as static_error:
            logger.warning(f"检查静态动画目录失败: {static_error}")
            static_animation_dir = None

        # 2. 检查用户目录下的vrm/animation（兼容旧版）
        try:
            user_animation_dir = config_mgr.vrm_animation_dir
            if user_animation_dir.exists() and user_animation_dir.is_dir():
                animations_dirs.append(user_animation_dir)
                logger.debug(f"找到用户动画目录: {user_animation_dir}")
            else:
                logger.debug(f"用户动画目录不存在或不是目录: {user_animation_dir}")
        except Exception as user_error:
            logger.warning(f"检查用户动画目录失败: {user_error}")
            user_animation_dir = None
        
        animations = []
        seen_urls = set()  # 使用 set 存储已见过的 URL，O(1) 查找，避免 O(n²) 列表检查
        
        logger.info(f"找到 {len(animations_dirs)} 个动画目录")
        
        # 如果没有找到任何目录，直接返回空列表
        if not animations_dirs:
            logger.info("未找到任何动画目录，返回空列表")
            return JSONResponse(content={
                "success": True,
                "animations": []
            })
        
        # 预先计算路径字符串，避免在循环中重复计算
        static_animation_dir_str = str(static_animation_dir) if static_animation_dir else None
        user_animation_dir_str = str(user_animation_dir) if user_animation_dir else None
        
        for anim_dir in animations_dirs:
            try:
                # 根据目录确定URL前缀（使用路径字符串比较更安全）
                anim_dir_str = str(anim_dir)
                
                if static_animation_dir_str and anim_dir_str == static_animation_dir_str:
                    # static/vrm/animation 目录 -> /static/vrm/animation
                    url_prefix = VRM_STATIC_ANIMATION_PATH
                elif user_animation_dir_str and anim_dir_str == user_animation_dir_str:
                    # user_vrm/animation 目录 -> /user_vrm/animation
                    url_prefix = "/user_vrm/animation"
                else:
                    # 默认使用 /user_vrm/animation
                    url_prefix = "/user_vrm/animation"
                
                # 查找.vrma文件
                for anim_file in anim_dir.glob('*.vrma'):
                    try:
                        if not anim_file.exists() or not anim_file.is_file():
                            continue
                        
                        url = f"{url_prefix}/{anim_file.name}"
                        # 使用 set 去重，基于 URL（逻辑路径）而不是绝对路径
                        if url in seen_urls:
                            continue
                        seen_urls.add(url)
                        
                        # 移除绝对路径，只返回公共 URL 和相对信息
                        animations.append({
                            "name": anim_file.stem,
                            "filename": anim_file.name,
                            "url": url,
                            "type": "vrma",
                            "size": anim_file.stat().st_size
                        })
                    except Exception as file_error:
                        logger.warning(f"处理动画文件失败 {anim_file}: {file_error}")
                        continue
                
                # 也支持.vrm文件作为动画（某些情况下）
                for anim_file in anim_dir.glob('*.vrm'):
                    try:
                        if not anim_file.exists() or not anim_file.is_file():
                            continue
                        
                        url = f"{url_prefix}/{anim_file.name}"
                        # 使用 set 去重，基于 URL（逻辑路径）
                        if url in seen_urls:
                            continue
                        seen_urls.add(url)
                        
                        # 移除绝对路径，只返回公共 URL 和相对信息
                        animations.append({
                            "name": anim_file.stem,
                            "filename": anim_file.name,
                            "url": url,
                            "type": "vrm",
                            "size": anim_file.stat().st_size
                        })
                    except Exception as file_error:
                        logger.warning(f"处理动画文件失败 {anim_file}: {file_error}")
                        continue
            except Exception as dir_error:
                logger.warning(f"处理动画目录失败 {anim_dir}: {dir_error}")
                continue
        
        logger.info(f"成功获取VRM动画列表，共 {len(animations)} 个动画文件")
        return JSONResponse(content={
            "success": True,
            "animations": animations
        })
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        logger.exception("获取VRM动画列表失败")
        logger.error(f"错误详情: {error_detail}")
        error_message = str(e)
        return JSONResponse(
            status_code=500, 
            content={
                "success": False, 
                "error": error_message
            }
        )


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
