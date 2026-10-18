# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
VRM Router

Handles VRM model-related endpoints including:
- VRM model listing
- VRM model upload
- VRM animation listing
- VRM emotion mapping configuration

URL convention: routes declared WITHOUT trailing slash (no ``@router.get('/')``).
See ``main_routers/characters_router.py`` docstring or
``.agent/rules/neko-guide.md`` (API URLs omit trailing slashes) for the rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""

import json
import os
import re
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse

from .shared_state import get_config_manager
from .workshop_router import get_subscribed_workshop_items
from utils.file_utils import atomic_write_json_async, read_json_async
from utils.logger_config import get_module_logger

router = APIRouter(prefix="/api/model/vrm", tags=["vrm"])
logger = get_module_logger(__name__, "Main")

# VRM model-path constants.
VRM_USER_PATH = "/user_vrm"  
VRM_STATIC_PATH = "/static/vrm"
VRM_STATIC_ANIMATION_PATH = "/static/vrm/animation"

# File-upload constants.
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200MB
CHUNK_SIZE = 1024 * 1024  # 1MB chunks for streaming


def safe_vrm_path(vrm_dir: Path, filename: str, subdir: str | None = None) -> tuple[Path | None, str]:
    """Safely construct and validate paths inside the VRM directory, preventing path traversal attacks.
    
    Args:
        vrm_dir: VRM root directory
        filename: file name
        subdir: subdirectory (e.g. 'animation'), optional
    """
    try:
        # Build paths with pathlib.
        if subdir:
            target_path = vrm_dir / subdir / filename
        else:
            target_path = vrm_dir / filename
        
        # Resolve absolute paths (including .. and symlinks).
        resolved_path = target_path.resolve()
        resolved_vrm_dir = vrm_dir.resolve()
        
        # Verify the resolved path is inside vrm_dir.
        try:
            if not resolved_path.is_relative_to(resolved_vrm_dir):
                return None, "路径越界：目标路径不在允许的目录内"
        except AttributeError:
            # Fallback for Python <3.9.
            try:
                resolved_path.relative_to(resolved_vrm_dir)
            except ValueError:
                return None, "路径越界：目标路径不在允许的目录内"
        
        # Ensure the path is a file, not a directory.
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
    """Common streaming logic for handling file uploads, nya~"""
    try:
        if not file:
            return JSONResponse(status_code=400, content={"success": False, "error": "没有上传文件"})
        
        # Check the file extension.
        filename = file.filename
        if not filename or not filename.lower().endswith(allowed_extension):
            return JSONResponse(status_code=400, content={"success": False, "error": f"文件必须是{allowed_extension}格式"})
        
        # Use only the basename, avoiding uploaded subdirectories.
        filename = Path(filename).name

        if allowed_extension == '.vrm':
            model_name = filename[:-len(allowed_extension)]
            if not _is_valid_vrm_model_name(model_name):
                return JSONResponse(status_code=400, content={
                    "success": False,
                    "error": f"无效的模型名称: {model_name!r}"
                })
            builtin_dir = get_config_manager().project_root / 'static' / 'vrm'
            if _vrm_model_stem_exists(model_name, (builtin_dir, target_dir)):
                return JSONResponse(status_code=400, content={
                    "success": False,
                    "error": f"{file_type_name} {filename} 已存在，请先删除或重命名现有模型"
                })
        
        # Use safe path helpers to prevent path traversal.
        target_file_path, path_error = safe_vrm_path(target_dir, filename, subdir)
        if target_file_path is None:
            logger.warning(f"路径穿越尝试被阻止: {filename!r} - {path_error}")
            return JSONResponse(status_code=400, content={
                "success": False,
                "error": path_error
            })
        
        # Stream reads into writes without loading the entire file into memory.
        total_size = 0
        try:
            # 'xb' creation is atomic and raises FileExistsError if the file exists.
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
            if not subdir:  # Add this notice only for the main model.
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
            # Get the model name without extension.
            model_name = Path(filename).stem
            return JSONResponse(content={
                "success": True,
                "message": f"{file_type_name} {filename} 上传成功",
                "model_name": model_name,
                "model_url": f"{VRM_USER_PATH}/{quote(filename, safe='')}",
                "file_size": total_size
            })
            
    except Exception as e:
        logger.error(f"上传{file_type_name}失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post('/upload')
async def upload_vrm_model(file: UploadFile = File(...)):
    """Upload a VRM model to the user documents directory (streamed read + async write, prevents path traversal)."""
    # Get VRM directory under user Documents.
    config_mgr = get_config_manager()
    if not config_mgr.ensure_vrm_directory():
        return JSONResponse(status_code=500, content={"success": False, "error": "VRM目录创建失败"})
    user_vrm_dir = config_mgr.vrm_dir
    
    return await _handle_vrm_file_upload(file, user_vrm_dir, '.vrm', 'VRM模型')


@router.post('/upload_animation')
async def upload_vrm_animation(file: UploadFile = File(...)):
    """Upload a VRM animation file to the user documents directory."""
    # Get VRM directory under user Documents (ensure_vrm_directory also creates animation).
    config_mgr = get_config_manager()
    if not config_mgr.ensure_vrm_directory():
        return JSONResponse(status_code=500, content={"success": False, "error": "VRM目录创建失败"})
    user_vrm_dir = config_mgr.vrm_dir
    
    return await _handle_vrm_file_upload(file, user_vrm_dir, '.vrma', '动作文件', 'animation')


def _iter_vrm_model_files(vrm_dir: Path):
    """Yield model files using the upload endpoint's case-insensitive extension rule."""
    if vrm_dir.exists():
        # Mapping identities are stems; keep the same representative as model lookup.
        for path in sorted(vrm_dir.iterdir(), key=lambda p: (p.stem, p.suffix != '.vrm', p.name)):
            if path.suffix.lower() == '.vrm' and path.is_file():
                yield path


@router.get('/models')
async def get_vrm_models():
    """List VRM models (without exposing absolute filesystem paths)."""
    try:
        config_mgr = get_config_manager()
        config_mgr.ensure_vrm_directory()

        models = []
        seen_urls = set()  # Use a set to deduplicate by URL.

        # 1. Find project VRM files (static/vrm/).
        project_root = config_mgr.project_root
        static_vrm_dir = project_root / "static" / "vrm"
        if static_vrm_dir.exists():
            for vrm_file in _iter_vrm_model_files(static_vrm_dir):
                url = f"/static/vrm/{quote(vrm_file.name, safe='')}"
                # Skip existing URLs to avoid duplicates.
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                
                # Remove absolute paths; return only public URLs and relative information.
                models.append({
                        "name": vrm_file.stem,
                        "filename": vrm_file.name,
                        "path": f"/static/vrm/{vrm_file.name}",
                        "url": url,
                        "type": "vrm",
                        "size": vrm_file.stat().st_size,
                        "location": "project"  
                    })

        # 2. Find user VRM files (user_vrm/).
        vrm_dir = config_mgr.vrm_dir
        if vrm_dir.exists():
            for vrm_file in _iter_vrm_model_files(vrm_dir):
                url = f"{VRM_USER_PATH}/{quote(vrm_file.name, safe='')}"
                # Skip existing URLs to avoid duplicates.
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                
                # Remove absolute paths; return only public URLs and relative information.
                models.append({
                        "name": vrm_file.stem,
                        "filename": vrm_file.name,
                        "path": f"{VRM_USER_PATH}/{vrm_file.name}",
                        "url": url,
                        "type": "vrm",
                        "size": vrm_file.stat().st_size,
                        "location": "user"  
                    })

        # 3. Find Steam Workshop VRM files.
        try:
            workshop_items_result = await get_subscribed_workshop_items()
            if isinstance(workshop_items_result, dict) and workshop_items_result.get('success', False):
                items = workshop_items_result.get('items', [])
                for item in items:
                    installed_folder = item.get('installedFolder')
                    item_id = item.get('publishedFileId')
                    if installed_folder and os.path.exists(installed_folder) and os.path.isdir(installed_folder) and item_id:
                        # Check installation directory for .vrm files.
                        for filename in os.listdir(installed_folder):
                            if filename.lower().endswith('.vrm'):
                                url = f"/workshop/{item_id}/{quote(filename, safe='')}"
                                if url in seen_urls:
                                    continue
                                seen_urls.add(url)
                                vrm_path = os.path.join(installed_folder, filename)
                                models.append({
                                    "name": Path(filename).stem,
                                    "filename": filename,
                                    "path": f"/workshop/{item_id}/{filename}",
                                    "url": url,
                                    "type": "vrm",
                                    "size": os.path.getsize(vrm_path),
                                    "location": "steam_workshop",
                                    "source": "steam_workshop",
                                    "item_id": str(item_id)
                                })
                        # Check subdirectories.
                        for subdir in os.listdir(installed_folder):
                            subdir_path = os.path.join(installed_folder, subdir)
                            if os.path.isdir(subdir_path):
                                for filename in os.listdir(subdir_path):
                                    if filename.lower().endswith('.vrm'):
                                        url = f"/workshop/{item_id}/{quote(subdir, safe='')}/{quote(filename, safe='')}"
                                        if url in seen_urls:
                                            continue
                                        seen_urls.add(url)
                                        vrm_path = os.path.join(subdir_path, filename)
                                        models.append({
                                            "name": Path(filename).stem,
                                            "filename": filename,
                                            "path": f"/workshop/{item_id}/{subdir}/{filename}",
                                            "url": url,
                                            "type": "vrm",
                                            "size": os.path.getsize(vrm_path),
                                            "location": "steam_workshop",
                                            "source": "steam_workshop",
                                            "item_id": str(item_id)
                                        })
        except Exception as e:
            logger.error(f"获取创意工坊VRM模型时出错: {e}")

        return JSONResponse(content={
            "success": True,
            "models": models
        })
    except Exception as e:
        logger.error(f"获取VRM模型列表失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/animations')
def get_vrm_animations():
    """List VRM animation files (VRMA, without exposing absolute filesystem paths)."""
    try:
        config_mgr = get_config_manager()
        try:
            config_mgr.ensure_vrm_directory()
        except Exception as ensure_error:
            logger.warning(f"确保VRM目录失败（继续尝试）: {ensure_error}")
        
        # Check animations directory.
        animations_dirs = []
        static_animation_dir = None
        user_animation_dir = None

        # 1. Prefer project static/vrm/animation (actual file location).
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

        # 2. Check user vrm/animation (legacy compatibility).
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
        seen_urls = set()  # Store seen URLs in a set for O(1) lookup, avoiding O(n²) list checks.
        
        logger.info(f"找到 {len(animations_dirs)} 个动画目录")
        
        # Return an empty list if no directories exist.
        if not animations_dirs:
            logger.info("未找到任何动画目录，返回空列表")
            return JSONResponse(content={
                "success": True,
                "animations": []
            })
        
        # Precompute path strings rather than repeating inside loops.
        static_animation_dir_str = str(static_animation_dir) if static_animation_dir else None
        user_animation_dir_str = str(user_animation_dir) if user_animation_dir else None
        
        for anim_dir in animations_dirs:
            try:
                # Determine URL prefix by directory (path-string comparison is safer).
                anim_dir_str = str(anim_dir)
                
                if static_animation_dir_str and anim_dir_str == static_animation_dir_str:
                    # static/vrm/animation directory -> /static/vrm/animation.
                    url_prefix = VRM_STATIC_ANIMATION_PATH
                elif user_animation_dir_str and anim_dir_str == user_animation_dir_str:
                    # user_vrm/animation directory -> /user_vrm/animation.
                    url_prefix = "/user_vrm/animation"
                else:
                    # Default /user_vrm/animation.
                    url_prefix = "/user_vrm/animation"
                
                # Imported user animations remain standard .vrma; builtins use .vrma.gz, decompressed uniformly
                # by the frontend on first playback. Both transport formats must appear in the animation selector.
                animation_files = list(anim_dir.glob('*.vrma')) + list(anim_dir.glob('*.vrma.gz'))
                for anim_file in animation_files:
                    try:
                        # Internal guided-movement assets need movement playback options and are excluded from ordinary animation selectors.
                        if url_prefix == VRM_STATIC_ANIMATION_PATH and anim_file.name in {
                            "world-walk.vrma", "world-walk.vrma.gz"
                        }:
                            continue
                        if not anim_file.exists() or not anim_file.is_file():
                            continue
                        
                        url = f"{url_prefix}/{anim_file.name}"
                        # Deduplicate with a set by URL (logical path), not absolute path.
                        if url in seen_urls:
                            continue
                        seen_urls.add(url)
                        
                        # Remove absolute paths; return only public URLs and relative information.
                        display_name = anim_file.name[:-len('.vrma.gz')] \
                            if anim_file.name.endswith('.vrma.gz') else anim_file.stem
                        animations.append({
                            "name": display_name,
                            "filename": anim_file.name,
                            "url": url,
                            "type": "vrma",
                            "size": anim_file.stat().st_size
                        })
                    except Exception as file_error:
                        logger.warning(f"处理动画文件失败 {anim_file}: {file_error}")
                        continue
                
                # Also support .vrm files as animations in certain cases.
                for anim_file in anim_dir.glob('*.vrm'):
                    try:
                        if not anim_file.exists() or not anim_file.is_file():
                            continue
                        
                        url = f"{url_prefix}/{anim_file.name}"
                        # Deduplicate with a set by URL (logical path).
                        if url in seen_urls:
                            continue
                        seen_urls.add(url)
                        
                        # Remove absolute paths; return only public URLs and relative information.
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


@router.delete('/model')
async def delete_vrm_model(request: Request):
    """Delete a user-imported VRM model file."""
    try:
        body = await request.json()
        url = body.get('url', '')
        if not url or not isinstance(url, str):
            return JSONResponse(status_code=400, content={"success": False, "error": "缺少 url 参数"})

        # Allow deletion only under /user_vrm/.
        if not url.startswith(VRM_USER_PATH + '/'):
            return JSONResponse(status_code=400, content={"success": False, "error": "只能删除用户导入的 VRM 模型"})

        rel = url[len(VRM_USER_PATH) + 1:]  # Strip '/user_vrm/'.
        config_mgr = get_config_manager()
        vrm_dir = config_mgr.vrm_dir
        # Decode URL paths only once; never infer another filename from encoded text again.
        try:
            filename = unquote(rel, errors='strict')
        except UnicodeDecodeError:
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的模型路径"})
        if not _is_vrm_basename(filename):
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的模型路径"})
        target = (vrm_dir / filename).resolve()
        if not target.is_relative_to(vrm_dir.resolve()):
            return JSONResponse(status_code=400, content={"success": False, "error": "路径越界"})
        if not target.is_file():
            return JSONResponse(status_code=404, content={"success": False, "error": "模型文件不存在"})

        target.unlink()
        await _cleanup_vrm_emotion_mapping(target.stem)
        logger.info(f"已删除 VRM 模型: {target.name}")

        return JSONResponse(content={"success": True, "message": f"VRM 模型 {target.stem} 已删除"})
    except Exception as e:
        logger.error(f"删除 VRM 模型失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


# Add configuration retrieval endpoint.
@router.get('/config')
async def get_vrm_config():
    """Get the path configuration shared between frontend and backend."""
    return JSONResponse(content={
        "success": True,
        "paths": {
            "user_vrm": VRM_USER_PATH,
            "static_vrm": VRM_STATIC_PATH,
            "static_animation": VRM_STATIC_ANIMATION_PATH
        }
    })


# ============== VRM emotion-mapping configuration API ==============

# Default emotion mappings.
DEFAULT_MOOD_MAP = {
    "neutral": ["neutral"],
    "happy": ["happy", "joy", "fun", "smile", "joy_01"],
    "relaxed": ["relaxed", "joy", "fun", "content"],
    "sad": ["sad", "sorrow", "grief"],
    "angry": ["angry", "anger"],
    "surprised": ["surprised", "surprise", "shock", "e", "o"]
}


def _is_valid_vrm_model_name(model_name: str) -> bool:
    """Preserve filename stems while rejecting path syntax and reserved characters."""
    # Preserve original upload names; prohibit separators, control characters, and Windows-reserved characters.
    return model_name not in {'', '.', '..'} and re.search(r'[<>:"/\\|?*\x00-\x1f]', model_name) is None


def _get_emotion_config_path(model_name: str) -> Path | None:
    """Get the emotion config file path for a model."""
    if not _is_valid_vrm_model_name(model_name):
        logger.warning(f"无效的模型名称: {model_name!r}")
        return None

    config_mgr = get_config_manager()

    # Store configuration in static/vrm/configs/.
    config_dir = config_mgr.project_root / "static" / "vrm" / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / f"{model_name}_emotion.json"

    # Verify resolved paths remain inside config_dir.
    try:
        config_path.resolve().relative_to(config_dir.resolve())
    except ValueError:
        logger.warning(f"路径穿越尝试被阻止: {model_name!r}")
        return None

    return config_path


def _get_model_path(model_name: str) -> tuple[Path | None, str]:
    """Get the VRM model file path; returns (path, url_prefix)."""
    if not _is_valid_vrm_model_name(model_name):
        logger.warning(f"无效的模型名称: {model_name!r}")
        return None, ""

    config_mgr = get_config_manager()
    project_root = config_mgr.project_root

    # Prefer project paths and original lowercase extensions, then support existing case variants on disk.
    for vrm_dir, url_prefix in (
        (project_root / "static" / "vrm", VRM_STATIC_PATH),
        (config_mgr.vrm_dir, VRM_USER_PATH),
    ):
        if url_prefix == VRM_USER_PATH:
            config_mgr.ensure_vrm_directory()
        try:
            model_path = _find_vrm_model_file(vrm_dir, model_name)
        except ValueError:
            return None, ""
        if model_path is not None:
            return model_path, url_prefix

    return None, ""


def _find_vrm_model_file(vrm_dir: Path, model_name: str) -> Path | None:
    canonical_path = vrm_dir / f"{model_name}.vrm"
    def candidates():
        yield canonical_path
        yield from (path for path in _iter_vrm_model_files(vrm_dir)
                    if path.stem == model_name and path != canonical_path)
    for candidate in candidates():
        resolved = candidate.resolve()
        if not resolved.is_relative_to(vrm_dir.resolve()):
            logger.warning(f"路径穿越尝试被阻止: {model_name!r}")
            raise ValueError("路径越界")
        if resolved.suffix.lower() == '.vrm' and resolved.is_file():
            return resolved
    return None


def _is_vrm_basename(filename: str) -> bool:
    # Legacy files can violate the upload policy. Keep the directory boundary
    # check in both deletion routes and reject path syntax here.
    return filename.lower().endswith('.vrm') and Path(filename).name == filename and not any(
        char in filename for char in ('/', chr(92), chr(0))
    )


def _vrm_model_stem_exists(model_name: str, directories) -> bool:
    # Conservatively protect mappings shared on case-insensitive filesystems.
    return any(path.stem.casefold() == model_name.casefold()
               for directory in directories for path in _iter_vrm_model_files(directory))


async def _cleanup_vrm_emotion_mapping(model_name: str):
    # Built-in/user models and case-variant extensions intentionally share a mapping.
    try:
        config_mgr = get_config_manager()
        directories = [config_mgr.project_root / 'static' / 'vrm', config_mgr.vrm_dir]
        emotion_config = _get_emotion_config_path(model_name)
        if not emotion_config or not emotion_config.is_file():
            return
        workshop = await get_subscribed_workshop_items()
        if not isinstance(workshop, dict) or not workshop.get('success'):
            raise RuntimeError("无法确认创意工坊的共享映射")
        for item in workshop.get('items', []):
            installed_folder = item.get('installedFolder')
            if not installed_folder or not item.get('publishedFileId'):
                continue
            folder = Path(installed_folder)
            if folder.is_dir():
                directories.append(folder)
                directories.extend(child for child in folder.iterdir() if child.is_dir())
        for directory in directories:
            for model in _iter_vrm_model_files(directory):
                other_config = emotion_config.parent / f"{model.stem}_emotion.json"
                if model.stem == model_name or (other_config.is_file() and other_config.samefile(emotion_config)):
                    return
        emotion_config.unlink()
    except Exception as e:
        logger.warning(f"删除情感映射配置失败: {e}")


@router.delete('/model/{model_name}')
async def delete_vrm_model(model_name: str):
    """Delete the specified user-imported VRM model."""
    try:
        config_mgr = get_config_manager()
        config_mgr.ensure_vrm_directory()
        vrm_dir = config_mgr.vrm_dir

        # Basic safety checks: reject empty names or path separators.
        if not model_name or not _is_vrm_basename(f"{model_name}.vrm"):
            return JSONResponse(status_code=400, content={"success": False, "error": f"无效的模型名称: {model_name!r}"})

        # Allow deleting VRM models only under the user directory.
        try:
            vrm_path = _find_vrm_model_file(vrm_dir, model_name)
        except ValueError as e:
            return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
        if vrm_path is None:
            return JSONResponse(status_code=404, content={"success": False, "error": f"未在用户目录中找到模型 {model_name}，可能是内置模型，无法删除"})

        vrm_path.unlink()

        await _cleanup_vrm_emotion_mapping(model_name)

        logger.info(f"已删除VRM模型: {model_name}")
        return {"success": True, "message": f"模型 {model_name} 已成功删除"}
    except Exception as e:
        logger.error(f"删除VRM模型失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/emotion_mapping/{model_name}')
async def get_emotion_mapping(model_name: str):
    """Get the emotion mapping configuration of a VRM model."""
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
            config = await read_json_async(config_path)
            return {"success": True, "config": config}
        else:
            # Return default config.
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
    """Update the emotion mapping configuration of a VRM model."""
    try:
        data = await request.json()

        # 1. Require a dict/object top-level payload.
        if not isinstance(data, dict) or not data:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "无效的数据结构：必须是包含情感映射的非空对象"}
            )

        # 2. Normalize each emotion-mapping value.
        normalized_data = {}
        for emotion_key, value in data.items():
            # Skip nonstring keys.
            if not isinstance(emotion_key, str) or not emotion_key.strip():
                continue

            # Normalize values to nonempty string arrays.
            if isinstance(value, str):
                # Convert single strings to arrays.
                if value.strip():
                    normalized_data[emotion_key] = [value.strip()]
            elif isinstance(value, list):
                # Filter/convert to strings, discarding empty entries.
                str_items = []
                for item in value:
                    if isinstance(item, str) and item.strip():
                        str_items.append(item.strip())
                if str_items:
                    normalized_data[emotion_key] = str_items
            # Skip other types.

        # 3. Require nonempty normalized data.
        if not normalized_data:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "无效的数据：规范化后无有效情感映射"}
            )

        # Check model existence.
        model_path, _ = _get_model_path(model_name)
        if not model_path:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": "模型不存在"}
            )

        # Save configuration
        config_path = _get_emotion_config_path(model_name)
        if not config_path:
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "无法创建配置目录"}
            )

        await atomic_write_json_async(config_path, normalized_data, ensure_ascii=False, indent=2)

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
    """Get the list of expressions supported by a VRM model (read from config, if present)."""
    # TODO: model_name parameter is intentionally unused. Model-specific expression
    # resolution is not implemented here because VRM files must be parsed on the frontend.
    # The frontend obtains actual expression lists after loading the model.
    # This endpoint returns a common expression list as a reference.
    _ = model_name  # Mark as intentionally unused

    try:
        # VRM parsing happens on the frontend; return common expressions here.
        # Frontend obtains actual expressions after model loading.

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
