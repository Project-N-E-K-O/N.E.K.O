# -*- coding: utf-8 -*-
"""
VRM Router

Handles VRM model-related endpoints including:
- VRM model listing
- VRM model upload
- VRM animation listing
- VRM emotion mapping configuration
"""

import json
import re
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse

from .shared_state import get_config_manager
from utils.logger_config import get_module_logger

router = APIRouter(prefix="/api/model/vrm", tags=["vrm"])
logger = get_module_logger(__name__, "Main")

# VRM 模型路径常量
VRM_USER_PATH = "/user_vrm"  
VRM_STATIC_PATH = "/static/vrm"
VRM_STATIC_ANIMATION_PATH = "/static/vrm/animation"

# 文件上传常量
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200MB
CHUNK_SIZE = 1024 * 1024  # 1MB chunks for streaming


def safe_vrm_path(vrm_dir: Path, filename: str, subdir: str | None = None) -> tuple[Path | None, str]:
    """安全地构造和验证 VRM 目录内的路径，防止路径穿越攻击。
    
    Args:
        vrm_dir: VRM根目录
        filename: 文件名
        subdir: 子目录（如 'animation'），可选
    """
    try:
        # 使用 pathlib 构造路径
        if subdir:
            target_path = vrm_dir / subdir / filename
        else:
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


async def _handle_vrm_file_upload(
    file: UploadFile, 
    target_dir: Path, 
    allowed_extension: str, 
    file_type_name: str, 
    subdir: str | None = None
) -> JSONResponse:
    """处理文件上传的通用流式逻辑喵~"""
    try:
        if not file:
            return JSONResponse(status_code=400, content={"success": False, "error": "没有上传文件"})
        
        # 检查文件扩展名
        filename = file.filename
        if not filename or not filename.lower().endswith(allowed_extension):
            return JSONResponse(status_code=400, content={"success": False, "error": f"文件必须是{allowed_extension}格式"})
        
        # 只取文件名，避免上传时夹带子目录
        filename = Path(filename).name
        
        # 使用安全路径函数防止路径穿越
        target_file_path, path_error = safe_vrm_path(target_dir, filename, subdir)
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
            error_msg = f"{file_type_name} {filename} 已存在"
            if not subdir:  # 只有主模型才加这个提示
                error_msg += "，请先删除或重命名现有模型"
            return JSONResponse(status_code=400, content={
                "success": False,
                "error": error_msg
            })
        except ValueError as ve:
            if str(ve) == "FILE_TOO_LARGE":
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
            try:
                target_file_path.unlink(missing_ok=True)
            except Exception:
                pass
            return JSONResponse(status_code=500, content={
                "success": False,
                "error": f"保存文件失败: {str(e)}"
            })
        finally:
            try:
                await file.close()
            except Exception:
                pass
        
        logger.info(f"成功上传{file_type_name}: {filename} -> {target_file_path} (大小: {total_size / (1024*1024):.2f}MB)")
        
        if subdir == 'animation':
            return JSONResponse(content={
                "success": True,
                "message": f"{file_type_name} {filename} 上传成功",
                "filename": filename,
                "file_path": f"{VRM_USER_PATH}/animation/{filename}"
            })
        else:
            # 获取模型名称（去掉扩展名）
            model_name = Path(filename).stem
            return JSONResponse(content={
                "success": True,
                "message": f"{file_type_name} {filename} 上传成功",
                "model_name": model_name,
                "model_url": f"{VRM_USER_PATH}/{filename}",
                "file_size": total_size
            })
            
    except Exception as e:
        logger.error(f"上传{file_type_name}失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post('/upload')
async def upload_vrm_model(file: UploadFile = File(...)):
    """上传VRM模型到用户文档目录（使用流式读取和异步写入，防止路径穿越）"""
    # 获取用户文档的vrm目录
    config_mgr = get_config_manager()
    if not config_mgr.ensure_vrm_directory():
        return JSONResponse(status_code=500, content={"success": False, "error": "VRM目录创建失败"})
    user_vrm_dir = config_mgr.vrm_dir
    
    return await _handle_vrm_file_upload(file, user_vrm_dir, '.vrm', 'VRM模型')


@router.post('/upload_animation')
async def upload_vrm_animation(file: UploadFile = File(...)):
    """上传VRM动作文件到用户文档目录"""
    # 获取用户文档的vrm目录（ensure_vrm_directory 也会创建 animation 子目录）
    config_mgr = get_config_manager()
    if not config_mgr.ensure_vrm_directory():
        return JSONResponse(status_code=500, content={"success": False, "error": "VRM目录创建失败"})
    user_vrm_dir = config_mgr.vrm_dir
    
    return await _handle_vrm_file_upload(file, user_vrm_dir, '.vrma', '动作文件', 'animation')


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


# ============== VRM 情感映射配置 API ==============

# 默认情感映射表
DEFAULT_MOOD_MAP = {
    "neutral": ["neutral"],
    "happy": ["happy", "joy", "fun", "smile", "joy_01"],
    "relaxed": ["relaxed", "joy", "fun", "content"],
    "sad": ["sad", "sorrow", "grief"],
    "angry": ["angry", "anger"],
    "surprised": ["surprised", "surprise", "shock", "e", "o"]
}


def _get_emotion_config_path(model_name: str) -> Path | None:
    """获取模型情感配置文件路径"""
    # 允许 Unicode 单词字符（包括 CJK）、下划线、连字符
    # \w 在 Python3 中支持 Unicode，包含字母、数字、下划线（含中日韩字符）
    safe_name = re.sub(r'[^\w-]', '', model_name, flags=re.UNICODE)
    if not safe_name:
        logger.warning(f"无效的模型名称: {model_name!r}")
        return None

    config_mgr = get_config_manager()

    # 配置文件存储在 static/vrm/configs/ 目录下
    config_dir = config_mgr.project_root / "static" / "vrm" / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / f"{safe_name}_emotion.json"

    # 验证解析后的路径仍在 config_dir 内
    try:
        config_path.resolve().relative_to(config_dir.resolve())
    except ValueError:
        logger.warning(f"路径穿越尝试被阻止: {model_name!r}")
        return None

    return config_path


def _get_model_path(model_name: str) -> tuple[Path | None, str]:
    """获取VRM模型文件路径，返回 (path, url_prefix)"""
    # 仅允许字母、数字、点、下划线、连字符（含 CJK 等 Unicode 单词字符）
    safe_name = re.sub(r'[^\w.\-]', '', model_name, flags=re.UNICODE)
    if not safe_name or safe_name != model_name:
        logger.warning(f"无效的模型名称: {model_name!r}")
        return None, ""

    config_mgr = get_config_manager()
    project_root = config_mgr.project_root

    # 1. 检查项目目录
    static_vrm_dir = project_root / "static" / "vrm"
    static_vrm_path = static_vrm_dir / f"{safe_name}.vrm"
    try:
        resolved = static_vrm_path.resolve()
        resolved.relative_to(static_vrm_dir.resolve())
    except ValueError:
        logger.warning(f"路径穿越尝试被阻止: {model_name!r}")
        return None, ""
    if resolved.suffix == '.vrm' and resolved.is_file():
        return resolved, "/static/vrm"

    # 2. 检查用户目录
    config_mgr.ensure_vrm_directory()
    user_vrm_path = config_mgr.vrm_dir / f"{safe_name}.vrm"
    try:
        resolved = user_vrm_path.resolve()
        resolved.relative_to(config_mgr.vrm_dir.resolve())
    except ValueError:
        logger.warning(f"路径穿越尝试被阻止: {model_name!r}")
        return None, ""
    if resolved.suffix == '.vrm' and resolved.is_file():
        return resolved, VRM_USER_PATH

    return None, ""


@router.get('/emotion_mapping/{model_name}')
async def get_emotion_mapping(model_name: str):
    """获取VRM模型的情感映射配置"""
    try:
        config_path = _get_emotion_config_path(model_name)

        # Check if model_name is invalid (returns None from _get_emotion_config_path)
        if config_path is None:
            error_msg = f"Invalid model name: {model_name!r}"
            logger.error(f"获取VRM情感映射配置失败: {error_msg}")
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": error_msg}
            )

        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            return {"success": True, "config": config}
        else:
            # 返回默认配置
            return {"success": True, "config": DEFAULT_MOOD_MAP}

    except json.JSONDecodeError as e:
        logger.warning(f"情感配置文件 JSON 损坏，回退到默认配置: {e}")
        return {"success": True, "config": DEFAULT_MOOD_MAP}

    except Exception as e:
        logger.error(f"获取VRM情感映射配置失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )


@router.post('/emotion_mapping/{model_name}')
async def update_emotion_mapping(model_name: str, request: Request):
    """更新VRM模型的情感映射配置"""
    try:
        data = await request.json()

        # 1. 验证顶级 payload 是 dict/object
        if not isinstance(data, dict) or not data:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "无效的数据结构：必须是包含情感映射的非空对象"}
            )

        # 2. 规范化每个情感映射值
        normalized_data = {}
        for emotion_key, value in data.items():
            # 跳过非字符串的 key
            if not isinstance(emotion_key, str) or not emotion_key.strip():
                continue

            # 规范化 value 为非空字符串数组
            if isinstance(value, str):
                # 单个字符串转换为数组
                if value.strip():
                    normalized_data[emotion_key] = [value.strip()]
            elif isinstance(value, list):
                # 过滤并转换为字符串，丢弃空条目
                str_items = []
                for item in value:
                    if isinstance(item, str) and item.strip():
                        str_items.append(item.strip())
                if str_items:
                    normalized_data[emotion_key] = str_items
            # 其他类型跳过

        # 3. 验证规范化后的数据不为空
        if not normalized_data:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "无效的数据：规范化后无有效情感映射"}
            )

        # 验证模型是否存在
        model_path, _ = _get_model_path(model_name)
        if not model_path:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": "模型不存在"}
            )

        # 保存配置
        config_path = _get_emotion_config_path(model_name)
        if not config_path:
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "无法创建配置目录"}
            )

        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(normalized_data, f, ensure_ascii=False, indent=2)

        logger.info(f"已保存VRM模型 {model_name} 的情感映射配置")

        return {"success": True, "message": "配置保存成功"}

    except json.JSONDecodeError:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "无效的JSON格式"}
        )
    except Exception as e:
        logger.error(f"保存VRM情感映射配置失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )


@router.get('/expressions/{model_name}')
async def get_model_expressions(model_name: str):
    """获取VRM模型支持的表情列表（从配置中读取，如果有的话）"""
    # TODO: model_name parameter is intentionally unused. Model-specific expression
    # resolution is not implemented here because VRM files must be parsed on the frontend.
    # The frontend obtains actual expression lists after loading the model.
    # This endpoint returns a common expression list as a reference.
    _ = model_name  # Mark as intentionally unused

    try:
        # 由于VRM文件需要前端解析，这里返回常见表情列表
        # 前端会在加载模型后获取实际表情列表

        common_expressions = [
            "neutral", "happy", "joy", "fun", "smile", "joy_01",
            "relaxed", "content",
            "sad", "sorrow", "grief",
            "angry", "anger",
            "surprised", "surprise", "shock",
            "blink", "blink_l", "blink_r",
            "aa", "ih", "ou", "ee", "oh",
            "lookUp", "lookDown", "lookLeft", "lookRight"
        ]

        return {
            "success": True,
            "expressions": common_expressions,
            "note": "这是常见表情列表，实际表情以模型为准"
        }

    except Exception as e:
        logger.error(f"获取VRM表情列表失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )
