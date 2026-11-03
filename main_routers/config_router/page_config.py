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

"""Character page config: reserved fields, avatar path resolution
(VRM / MMD / PNGTuber) and the /page_config endpoint.

Split out of the former monolithic ``main_routers/config_router.py``.
"""

from ._shared import logger, router

import json
import urllib.parse
from fastapi import Response
from ..shared_state import get_config_manager
from ..characters_router import get_current_live2d_model
from utils.config_manager import get_reserved
from config import (
    AUTOSTART_CSRF_TOKEN,
    CHARACTER_SYSTEM_RESERVED_FIELDS,
    CHARACTER_WORKSHOP_RESERVED_FIELDS,
    CHARACTER_RESERVED_FIELDS,
)


# VRM model-path constants.
VRM_STATIC_PATH = "/static/vrm"  # VRM model path in the project directory.


VRM_USER_PATH = "/user_vrm"  # VRM model path in the user's Documents directory.


# MMD model-path constants.
MMD_STATIC_PATH = "/static/mmd"  # MMD model path in the project directory.


MMD_USER_PATH = "/user_mmd"  # MMD model path in the user's Documents directory.


PNGTUBER_USER_PATH = "/user_pngtuber"


PNGTUBER_EXTENSIONS = {'.png', '.gif', '.jpg', '.jpeg', '.webp'}


def _resolve_master_display_name(master_basic_config: dict, fallback_name: str = "") -> str:
    nickname = str(master_basic_config.get('昵称', '') or '').strip()
    if nickname:
        first_nickname = nickname.split(',')[0].split('，')[0].strip()
        if first_nickname:
            return first_nickname
    profile_name = str(master_basic_config.get('档案名', '') or '').strip()
    if profile_name:
        return profile_name
    return str(fallback_name or '').strip()


@router.get("/character_reserved_fields")
async def get_character_reserved_fields():
    """Return the character profile reserved-field config (shared by the frontend and routers)."""
    return {
        "success": True,
        "system_reserved_fields": list(CHARACTER_SYSTEM_RESERVED_FIELDS),
        "workshop_reserved_fields": list(CHARACTER_WORKSHOP_RESERVED_FIELDS),
        "all_reserved_fields": list(CHARACTER_RESERVED_FIELDS),
    }


# MMD file extensions.
_MMD_EXTENSIONS = {'.pmx', '.pmd'}


def _get_live3d_sub_type(catgirl_config: dict) -> str:
    """Decide whether Live3D mode should use the VRM or the MMD renderer.
    Prefers the persisted sub-type; falls back to model-path-based detection when it is missing or invalid."""
    stored_sub_type = str(
        get_reserved(
            catgirl_config,
            'avatar',
            'live3d_sub_type',
            default='',
            legacy_keys=('live3d_sub_type',),
        )
        or ''
    ).strip().lower()
    if stored_sub_type in {'mmd', 'vrm'}:
        return stored_sub_type

    mmd_path = get_reserved(catgirl_config, 'avatar', 'mmd', 'model_path', default='')
    if mmd_path:
        return 'mmd'
    vrm_path = get_reserved(catgirl_config, 'avatar', 'vrm', 'model_path', default='', legacy_keys=('vrm',))
    if vrm_path:
        return 'vrm'
    return ''


def _resolve_vrm_path(vrm_path: str, _config_manager, target_name: str) -> str:
    """Resolve the VRM model path, verify the file exists, and return a usable URL or an empty string."""
    if vrm_path.startswith('http://') or vrm_path.startswith('https://'):
        logger.debug(f"获取页面配置 - 角色: {target_name}, VRM模型HTTP路径: {vrm_path}")
        return vrm_path
    elif vrm_path.startswith('/'):
        _vrm_file_verified = False
        if vrm_path.startswith(VRM_USER_PATH + '/'):
            _fname = vrm_path[len(VRM_USER_PATH) + 1:]
            _vrm_file_verified = (_config_manager.vrm_dir / _fname).exists()
        elif vrm_path.startswith(VRM_STATIC_PATH + '/'):
            _fname = vrm_path[len(VRM_STATIC_PATH) + 1:]
            _vrm_file_verified = (_config_manager.project_root / 'static' / 'vrm' / _fname).exists()
        elif vrm_path.startswith('/workshop/'):
            return urllib.parse.quote(vrm_path, safe='/')
        else:
            # Custom routes are URLs already, including query/fragment syntax.
            return vrm_path
        if _vrm_file_verified:
            logger.debug(f"获取页面配置 - 角色: {target_name}, VRM模型绝对路径: {vrm_path}")
            return urllib.parse.quote(vrm_path, safe='/')
        else:
            logger.warning(f"获取页面配置 - 角色: {target_name}, VRM模型文件未找到: {vrm_path}")
            return ""
    else:
        from pathlib import PurePosixPath
        safe_rel = PurePosixPath(vrm_path)
        if safe_rel.is_absolute() or '..' in safe_rel.parts:
            logger.warning(f"获取页面配置 - 角色: {target_name}, VRM路径不合法: {vrm_path}")
            return ""
        project_vrm_path = _config_manager.project_root / 'static' / 'vrm' / str(safe_rel)
        if project_vrm_path.exists():
            result = urllib.parse.quote(f'{VRM_STATIC_PATH}/{safe_rel}', safe='/')
            logger.debug(f"获取页面配置 - 角色: {target_name}, VRM模型在项目目录: {vrm_path} -> {result}")
            return result
        user_vrm_path = _config_manager.vrm_dir / str(safe_rel)
        if user_vrm_path.exists():
            result = urllib.parse.quote(f'{VRM_USER_PATH}/{safe_rel}', safe='/')
            logger.debug(f"获取页面配置 - 角色: {target_name}, VRM模型在用户目录: {vrm_path} -> {result}")
            return result
        logger.warning(f"获取页面配置 - 角色: {target_name}, VRM模型文件未找到: {vrm_path}")
        return ""


def _resolve_mmd_path(mmd_path: str, _config_manager, target_name: str) -> str:
    """Resolve the MMD model path, verify the file exists, and return a usable URL or an empty string."""
    if mmd_path.startswith('http://') or mmd_path.startswith('https://'):
        logger.debug(f"获取页面配置 - 角色: {target_name}, MMD模型HTTP路径: {mmd_path}")
        return mmd_path
    elif mmd_path.startswith('/'):
        _mmd_file_verified = False
        if mmd_path.startswith(MMD_USER_PATH + '/'):
            _fname = mmd_path[len(MMD_USER_PATH) + 1:]
            _mmd_file_verified = (_config_manager.mmd_dir / _fname).exists()
        elif mmd_path.startswith(MMD_STATIC_PATH + '/'):
            _fname = mmd_path[len(MMD_STATIC_PATH) + 1:]
            _mmd_file_verified = (_config_manager.project_root / 'static' / 'mmd' / _fname).exists()
        else:
            _mmd_file_verified = True
        if _mmd_file_verified:
            logger.debug(f"获取页面配置 - 角色: {target_name}, MMD模型绝对路径: {mmd_path}")
            return mmd_path
        else:
            logger.warning(f"获取页面配置 - 角色: {target_name}, MMD模型文件未找到: {mmd_path}")
            return ""
    else:
        from pathlib import PurePosixPath
        safe_rel = PurePosixPath(mmd_path)
        if safe_rel.is_absolute() or '..' in safe_rel.parts:
            logger.warning(f"获取页面配置 - 角色: {target_name}, MMD路径不合法: {mmd_path}")
            return ""
        project_mmd_path = _config_manager.project_root / 'static' / 'mmd' / str(safe_rel)
        if project_mmd_path.exists():
            result = f'{MMD_STATIC_PATH}/{safe_rel}'
            logger.debug(f"获取页面配置 - 角色: {target_name}, MMD模型在项目目录: {mmd_path} -> {result}")
            return result
        user_mmd_path = _config_manager.mmd_dir / str(safe_rel)
        if user_mmd_path.exists():
            result = f'{MMD_USER_PATH}/{safe_rel}'
            logger.debug(f"获取页面配置 - 角色: {target_name}, MMD模型在用户目录: {mmd_path} -> {result}")
            return result
        logger.warning(f"获取页面配置 - 角色: {target_name}, MMD模型文件未找到: {mmd_path}")
        return ""


def _resolve_pngtuber_image_path(image_path: str, _config_manager, target_name: str) -> str:
    """Resolve a PNGTuber image reference to a browser-loadable URL."""
    image_path = str(image_path or '').strip().replace('\\', '/')
    if not image_path or image_path.lower() in {'undefined', 'null'}:
        return ""
    if image_path.startswith('http://') or image_path.startswith('https://'):
        return image_path
    if image_path.startswith('//'):
        logger.warning(f"Invalid PNGTuber protocol-relative image path for {target_name}: {image_path}")
        return ""
    lookup_path = urllib.parse.urlsplit(image_path).path
    if image_path.startswith('/'):
        if lookup_path.startswith(PNGTUBER_USER_PATH + '/'):
            rel = lookup_path[len(PNGTUBER_USER_PATH) + 1:]
            from pathlib import PurePosixPath
            safe_rel = PurePosixPath(rel)
            if safe_rel.is_absolute() or '..' in safe_rel.parts:
                logger.warning(f"Invalid PNGTuber image path for {target_name}: {image_path}")
                return ""
            if (_config_manager.pngtuber_dir / rel).exists():
                return image_path
            logger.warning(f"PNGTuber image not found for {target_name}: {image_path}")
            return ""
        return image_path

    from pathlib import PurePosixPath
    safe_rel = PurePosixPath(lookup_path)
    if safe_rel.is_absolute() or '..' in safe_rel.parts:
        logger.warning(f"Invalid PNGTuber image path for {target_name}: {image_path}")
        return ""
    if safe_rel.suffix.lower() not in PNGTUBER_EXTENSIONS:
        logger.warning(f"Unsupported PNGTuber image extension for {target_name}: {image_path}")
        return ""
    user_path = _config_manager.pngtuber_dir / str(safe_rel)
    if user_path.exists():
        return f'{PNGTUBER_USER_PATH}/{safe_rel}'
    logger.warning(f"PNGTuber image not found for {target_name}: {image_path}")
    return ""


@router.get("/page_config")
async def get_page_config(response: Response, lanlan_name: str = ""):
    """Get page config (lanlan_name and model_path); supports Live2D, VRM and MMD (Live3D) models."""
    try:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"

        # Retrieve character data.
        _config_manager = get_config_manager()
        master_name, her_name, master_basic_config, lanlan_basic_config, _, _, _, _, _ = await _config_manager.aget_character_data()
        master_display_name = _resolve_master_display_name(master_basic_config, master_name)
        
        # Use lanlan_name when provided; otherwise use the current character.
        target_name = lanlan_name if lanlan_name else her_name
        
        # Retrieve character configuration.
        catgirl_config = lanlan_basic_config.get(target_name, {})
        model_type = get_reserved(catgirl_config, 'avatar', 'model_type', default='live2d', legacy_keys=('model_type',))
        model_type = str(model_type or 'live2d').strip().lower()
        # Normalize legacy 'vrm' configuration to 'live3d'.
        if model_type == 'vrm':
            model_type = 'live3d'
        
        model_path = ""
        lighting = None
        # live3d_sub_type tells the frontend whether Live3D should load the VRM or MMD renderer.
        live3d_sub_type = ""
        pngtuber_config = None
        
        # Get the model path by model type.
        if model_type == 'pngtuber':
            raw_pngtuber = get_reserved(catgirl_config, 'avatar', 'pngtuber', default={})
            if not isinstance(raw_pngtuber, dict):
                raw_pngtuber = {}
            pngtuber_config = dict(raw_pngtuber)
            for key in ('idle_image', 'talking_image', 'drag_image', 'click_image', 'happy_image', 'sad_image', 'angry_image', 'surprised_image'):
                pngtuber_config[key] = _resolve_pngtuber_image_path(
                    str(raw_pngtuber.get(key) or ''),
                    _config_manager,
                    target_name,
                )
            model_path = pngtuber_config.get('idle_image', '') or ''
        elif model_type == 'live3d' and _get_live3d_sub_type(catgirl_config) == 'vrm':
            live3d_sub_type = 'vrm'
            # VRM model: convert the path.
            vrm_path = get_reserved(catgirl_config, 'avatar', 'vrm', 'model_path', default='', legacy_keys=('vrm',))
            if vrm_path:
                model_path = _resolve_vrm_path(vrm_path, _config_manager, target_name)
            else:
                logger.warning(f"角色 {target_name} 的VRM模型路径为空")
            saved_lighting = get_reserved(
                catgirl_config,
                'avatar',
                'vrm',
                'lighting',
                default=None,
                legacy_keys=('lighting',),
            )
            if isinstance(saved_lighting, dict):
                lighting = dict(saved_lighting)
        elif model_type == 'live3d' and _get_live3d_sub_type(catgirl_config) == 'mmd':
            live3d_sub_type = 'mmd'
            # MMD model: convert the path.
            mmd_path = get_reserved(catgirl_config, 'avatar', 'mmd', 'model_path', default='')
            if mmd_path:
                model_path = _resolve_mmd_path(mmd_path, _config_manager, target_name)
            else:
                logger.warning(f"角色 {target_name} 的MMD模型路径为空")
        elif model_type == 'live3d':
            # live3d with unknown subtype (both paths empty): return an empty path.
            live3d_sub_type = ''
            logger.warning(f"角色 {target_name} 的Live3D模型路径均为空")
        else:
            # Live2D model: use existing behavior.
            live2d = get_reserved(catgirl_config, 'avatar', 'live2d', 'model_path', default='yui-lolita/yui-lolita.model3.json', legacy_keys=('live2d',))
            live2d_item_id = get_reserved(
                catgirl_config,
                'avatar',
                'asset_source_id',
                default='',
                legacy_keys=('live2d_item_id', 'item_id'),
            )
            
            logger.debug(f"获取页面配置 - 角色: {target_name}, Live2D模型: {live2d}, item_id: {live2d_item_id}")
        
            model_response = await get_current_live2d_model(target_name, live2d_item_id)
            # Extract JSONResponse content.
            model_data = model_response.body.decode('utf-8')
            model_json = json.loads(model_data)
            model_info = model_json.get('model_info') or {}
            model_path = model_info.get('path', '')
        
        result = {
            "success": True,
            "lanlan_name": target_name,
            "master_name": master_name or "",
            "master_profile_name": str(master_basic_config.get('档案名', '') or ''),
            "master_nickname": str(master_basic_config.get('昵称', '') or ''),
            "master_display_name": master_display_name or "",
            "autostart_csrf_token": AUTOSTART_CSRF_TOKEN,
            "model_path": model_path,
            "model_type": model_type,
            "lighting": lighting,
        }
        if model_type == 'live3d':
            result["live3d_sub_type"] = live3d_sub_type
        if model_type == 'pngtuber':
            result["pngtuber"] = pngtuber_config or {}
        return result
    except Exception as e:
        logger.error(f"获取页面配置失败: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "lanlan_name": "",
            "master_name": "",
            "master_profile_name": "",
            "master_nickname": "",
            "master_display_name": "",
            "autostart_csrf_token": AUTOSTART_CSRF_TOKEN,
            "model_path": "",
            "model_type": ""
        }
