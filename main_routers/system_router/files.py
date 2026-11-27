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

"""Local file utility endpoints (/file-exists, /find-first-image).

Split out of the former monolithic ``main_routers/system_router.py``.
"""

from ._shared import (
    _extract_unc_share,
    _get_app_root,
    _get_system_config_manager,
    _is_path_within_base,
    _is_unsafe_windows_unc_or_device_path,
    logger,
    router,
)
import os
import re
from urllib.parse import unquote
from fastapi import Request
from fastapi.responses import JSONResponse
from main_routers.local_access import validate_local_browser_request
from utils.workshop_utils import get_workshop_library_roots, get_workshop_path_async


async def _get_file_exists_allowed_dirs() -> list[str]:
    """Return canonical trusted base directories for /api/file-exists."""
    base_dir = _get_app_root()
    candidates: list[str] = [
        os.path.join(base_dir, "static"),
        os.path.join(base_dir, "assets"),
    ]

    try:
        workshop_dir = await get_workshop_path_async()
        if workshop_dir:
            candidates.append(str(workshop_dir))
    except Exception as exc:
        logger.warning(f"file-exists: unable to resolve workshop directory: {exc}")

    try:
        for root in get_workshop_library_roots() or ():
            if root:
                candidates.append(str(root))
    except Exception as exc:
        logger.warning(f"file-exists: unable to resolve workshop library roots: {exc}")

    try:
        cm = _get_system_config_manager()
        for attr in ("docs_dir", "app_docs_dir", "original_docs_dir"):
            root = getattr(cm, attr, None)
            if root:
                candidates.append(str(root))
    except Exception as exc:
        logger.warning(f"file-exists: unable to resolve documents directory: {exc}")

    if os.name == "nt":
        xiao8_docs = os.path.join(os.path.expanduser("~"), "Documents", "Xiao8")
        if os.path.exists(xiao8_docs):
            candidates.append(xiao8_docs)

    allowed_dirs: list[str] = []
    for candidate in candidates:
        real_candidate = os.path.realpath(candidate)
        if real_candidate not in allowed_dirs:
            allowed_dirs.append(real_candidate)
    return allowed_dirs


@router.get('/file-exists')
async def check_file_exists(request: Request, path: str = None):
    """Check whether a file exists within trusted application or user data directories."""
    rejection = validate_local_browser_request(request, require_json=False)
    if rejection is not None:
        status_code, error_code, _ = rejection
        logger.warning("Rejected file-exists request: %s", error_code)
        return JSONResponse(
            content={"exists": False, "error_code": error_code},
            status_code=status_code,
        )

    try:
        if not path:
            return JSONResponse(content={"exists": False}, status_code=400)

        decoded_path = unquote(path)
        if not decoded_path or "\x00" in decoded_path:
            return JSONResponse(content={"exists": False}, status_code=400)

        if ".." in re.split(r"[/\\]", decoded_path):
            logger.warning("Rejected path traversal attempt in file-exists")
            return JSONResponse(content={"exists": False}, status_code=400)

        if _is_unsafe_windows_unc_or_device_path(decoded_path, allow_unc=True):
            logger.warning("Rejected device path probe in file-exists")
            return JSONResponse(content={"exists": False}, status_code=403)

        allowed_dirs = await _get_file_exists_allowed_dirs()
        allowed_unc_shares = {
            share for d in allowed_dirs if (share := _extract_unc_share(d)) is not None
        }
        if _is_unsafe_windows_unc_or_device_path(decoded_path, allowed_unc_shares=allowed_unc_shares):
            logger.warning("Rejected UNC path probe in file-exists")
            return JSONResponse(content={"exists": False}, status_code=403)

        if os.name == 'nt':
            decoded_path = decoded_path.replace('/', '\\')

        normalized = os.path.normpath(decoded_path)
        if '..' in re.split(r"[/\\]", normalized):
            logger.warning("Rejected normalized path traversal attempt in file-exists")
            return JSONResponse(content={"exists": False}, status_code=400)

        real_path = os.path.realpath(normalized)
        if not any(_is_path_within_base(allowed_dir, real_path) for allowed_dir in allowed_dirs):
            logger.warning("Rejected out-of-scope path probe in file-exists")
            return JSONResponse(content={"exists": False}, status_code=403)

        exists = os.path.exists(real_path) and os.path.isfile(real_path)
        return JSONResponse(content={"exists": exists})

    except Exception as e:
        logger.error(f"检查文件存在失败: {e}")
        return JSONResponse(content={"exists": False}, status_code=500)


@router.get('/find-first-image')
async def find_first_image(folder: str = None):
    """
    Find a preview image in the given folder — hardened version with strict security checks.
    
    Security notes:
    1. only specific safe directories inside the project may be accessed
    2. prevents path traversal attacks
    3. limits returned info to avoid leaking filesystem details
    4. logs suspicious access attempts
    5. only returns images smaller than 1MB (the Steam Workshop preview size limit)
    """
    MAX_IMAGE_SIZE = 1 * 1024 * 1024  # 1MB
    
    try:
        # Validate parameters
        if not folder:
            logger.warning("收到空的文件夹路径请求")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        # Log security warnings
        logger.warning(f"预览图片查找请求: {folder}")
        
        # Get the base directory and the list of accessible directories
        base_dir = _get_app_root()
        allowed_dirs = [
            os.path.realpath(os.path.join(base_dir, 'static')),
            os.path.realpath(os.path.join(base_dir, 'assets'))
        ]
        
        # Add the 'My Documents/Xiao8' directory to the allowlist
        if os.name == 'nt':  # Windows systems
            documents_path = os.path.join(os.path.expanduser('~'), 'Documents', 'Xiao8')
            if os.path.exists(documents_path):
                real_doc_path = os.path.realpath(documents_path)
                allowed_dirs.append(real_doc_path)
                logger.info(f"find-first-image: 添加允许的文档目录: {real_doc_path}")
        
        # Decode the URL-encoded path
        decoded_folder = unquote(folder)
        
        # Windows path handling
        if os.name == 'nt':
            decoded_folder = decoded_folder.replace('/', '\\')
        
        # Additional security check: reject requests containing path-traversal characters
        if '..' in decoded_folder or '//' in decoded_folder:
            logger.warning(f"检测到潜在的路径遍历攻击: {decoded_folder}")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=403)
        
        # Normalize paths to prevent path-traversal attacks
        try:
            real_folder = os.path.realpath(decoded_folder)
        except Exception as e:
            logger.error(f"路径规范化失败: {e}")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        # Check that the path is inside an allowed directory; commonpath prevents prefix attacks
        is_allowed = any(_is_path_within_base(allowed_dir, real_folder) for allowed_dir in allowed_dirs)
        
        if not is_allowed:
            logger.warning(f"访问被拒绝：路径不在允许的目录内 - {real_folder}")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=403)
        
        # Check whether the folder exists
        if not os.path.exists(real_folder) or not os.path.isdir(real_folder):
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        # Search only the eight specified preview-image names, in priority order
        preview_image_names = [
            'preview.jpg', 'preview.png',
            'thumbnail.jpg', 'thumbnail.png',
            'icon.jpg', 'icon.png',
            'header.jpg', 'header.png'
        ]
        
        for image_name in preview_image_names:
            image_path = os.path.join(real_folder, image_name)
            try:
                # Check whether the file exists
                if os.path.exists(image_path) and os.path.isfile(image_path):
                    # Check whether the file is smaller than 1MB
                    file_size = os.path.getsize(image_path)
                    if file_size >= MAX_IMAGE_SIZE:
                        logger.info(f"跳过大于1MB的图片: {image_name} ({file_size / 1024 / 1024:.2f}MB)")
                        continue
                    
                    # Recheck that the image path is inside an allowed directory; commonpath prevents prefix attacks
                    real_image_path = os.path.realpath(image_path)
                    if any(_is_path_within_base(allowed_dir, real_image_path) for allowed_dir in allowed_dirs):
                        # Return only a relative path or filename, not a full filesystem path, to avoid information disclosure
                        # Calculate the path relative to base_dir
                        try:
                            relative_path = os.path.relpath(real_image_path, base_dir)
                            return JSONResponse(content={"success": True, "imagePath": relative_path})
                        except ValueError:
                            # If a relative path cannot be calculated (such as across drives), return only the filename
                            return JSONResponse(content={"success": True, "imagePath": image_name})
            except Exception as e:
                logger.error(f"检查图片文件 {image_name} 失败: {e}")
                continue
        
        return JSONResponse(content={"success": False, "error": "未找到小于1MB的预览图片文件"})
        
    except Exception as e:
        logger.error(f"查找预览图片文件失败: {e}")
        return JSONResponse(content={"success": False, "error": "服务器内部错误"}, status_code=500)
