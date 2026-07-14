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

"""Live2D/MMD model binding endpoints: current model resolution,
l2d update, touch set, lighting and MMD settings.

Split out of the former monolithic ``main_routers/characters_router.py``.
"""

from ._shared import logger, router
from .voice_runtime import _config_value_is_enabled

import re
import os
import math
from urllib.parse import urlparse
from fastapi import Request
from fastapi.responses import JSONResponse
from ..shared_state import (
    get_config_manager,
    get_init_one_catgirl,
)
from utils.config_manager import (
    get_reserved,
    set_reserved,
)
from utils.frontend_utils import find_models, find_model_directory
from utils.url_utils import encode_url_path
from utils.cloudsave_runtime import MaintenanceModeError
from config import (
    DEFAULT_LIVE2D_MODEL_NAME,
)


def _derive_live2d_model_name(model_ref: str) -> str:
    raw_ref = str(model_ref or "").strip()
    if not raw_ref:
        return ""
    parsed_ref = urlparse(raw_ref)
    is_http_url = parsed_ref.scheme in {"http", "https"} and bool(parsed_ref.netloc)
    model_ref_source = parsed_ref.path if is_http_url and parsed_ref.path else raw_ref
    normalized_ref = model_ref_source.strip().replace("\\", "/")
    if not normalized_ref:
        return ""
    if normalized_ref.endswith(".model3.json"):
        parts = [part for part in normalized_ref.split("/") if part]
        if len(parts) >= 2:
            return parts[-2]
        filename = parts[-1] if parts else normalized_ref
        return filename[:-len(".model3.json")]
    return normalized_ref.rsplit("/", 1)[-1]


def _normalize_live2d_catalog_path(model_path: str) -> str:
    normalized_path = str(model_path or "").strip().replace("\\", "/")
    if not normalized_path:
        return ""
    if normalized_path.startswith("/workshop/"):
        parts = [part for part in normalized_path.split("/") if part]
        return "/".join(parts[2:]) if len(parts) >= 3 else ""
    for prefix in ("/user_live2d/", "/user_live2d_local/", "/static/"):
        if normalized_path.startswith(prefix):
            return normalized_path[len(prefix):]
    return normalized_path.lstrip("/")


def _is_same_live2d_catalog_model_path(candidate_path: str, target_path: str) -> bool:
    candidate_normalized = _normalize_live2d_catalog_path(candidate_path)
    target_normalized = _normalize_live2d_catalog_path(target_path)
    if not candidate_normalized or not target_normalized:
        return False
    if candidate_normalized == target_normalized:
        return True
    candidate_tail = "/".join(candidate_normalized.split("/")[-2:])
    target_tail = "/".join(target_normalized.split("/")[-2:])
    return bool(candidate_tail and candidate_tail == target_tail)


def _derive_live2d_asset_source(model_path: str) -> str:
    normalized_path = str(model_path or "").strip().replace("\\", "/")
    if normalized_path.startswith(("http://", "https://")):
        return "manual_external"
    if normalized_path.startswith("/workshop/"):
        return "steam_workshop"
    if normalized_path.startswith("/static/"):
        return "builtin"
    if normalized_path.startswith(("/user_live2d/", "/user_live2d_local/")):
        return "local_imported"
    return ""


def _derive_model_asset_binding(model_path: str, *, item_id: str = "") -> tuple[str, str]:
    normalized_path = str(model_path or "").strip().replace("\\", "/")
    normalized_item_id = str(item_id or "").strip()

    if not normalized_item_id and normalized_path.startswith("/workshop/"):
        parts = normalized_path.split("/")
        if len(parts) >= 3:
            normalized_item_id = str(parts[2] or "").strip()

    if normalized_item_id or normalized_path.startswith("/workshop/"):
        return "steam_workshop", normalized_item_id
    if normalized_path.startswith(("/user_live2d/", "/user_live2d_local/", "/user_vrm/", "/user_mmd/")):
        return "local_imported", ""
    if normalized_path.startswith(("http://", "https://")):
        return "manual_external", ""
    if normalized_path.startswith("/static/") or (normalized_path and not normalized_path.startswith("/")):
        return "builtin", ""
    return "", ""


def _find_live2d_model_catalog_entry(
    all_models: list[dict],
    *,
    model_name: str = "",
    model_path: str = "",
    asset_source: str = "",
    item_id: str = "",
):
    normalized_name = str(model_name or "").strip()
    normalized_path = _normalize_live2d_catalog_path(model_path)
    normalized_source = str(asset_source or "").strip().lower()
    normalized_item_id = str(item_id or "").strip()

    if normalized_item_id:
        item_matches = [
            model
            for model in all_models
            if str(model.get("item_id") or "").strip() == normalized_item_id
        ]
        item_name_matches = item_matches
        if normalized_name:
            item_name_matches = [
                model
                for model in item_matches
                if str(model.get("name") or "").strip() == normalized_name
            ]

        if normalized_path:
            strict_candidates = item_name_matches if item_name_matches else item_matches
            strict_item_match = next(
                (
                    model
                    for model in strict_candidates
                    if _is_same_live2d_catalog_model_path(
                        str(model.get("path") or "").strip().replace("\\", "/"),
                        normalized_path,
                    )
                ),
                None,
            )
            if strict_item_match is not None:
                return strict_item_match

        if len(item_name_matches) == 1:
            return item_name_matches[0]
        if len(item_matches) == 1:
            return item_matches[0]

    if normalized_path:
        expected_prefixes: tuple[str, ...] = ()
        if normalized_source == "builtin":
            expected_prefixes = ("/static/",)
        elif normalized_source in {"local", "local_imported"}:
            expected_prefixes = ("/user_live2d/", "/user_live2d_local/")
        elif normalized_source == "steam_workshop":
            expected_prefixes = ("/workshop/",)

        for model in all_models:
            candidate_path = str(model.get("path") or "").strip().replace("\\", "/")
            if expected_prefixes and not candidate_path.startswith(expected_prefixes):
                continue
            if _is_same_live2d_catalog_model_path(candidate_path, normalized_path):
                return model

    if normalized_name:
        return next(
            (model for model in all_models if str(model.get("name") or "").strip() == normalized_name),
            None,
        )

    return None


def _resolve_live2d_model_binding(model_identifier: str, *, item_id: str = "") -> tuple[str, str, str]:
    normalized_model = str(model_identifier or "").strip().replace("\\", "/")
    normalized_item_id = str(item_id or "").strip()
    live2d_name = _derive_live2d_model_name(normalized_model)

    resolved_model_path = _normalize_live2d_catalog_path(normalized_model)
    if not resolved_model_path and live2d_name:
        resolved_model_path = f"{live2d_name}/{live2d_name}.model3.json"

    resolved_source = "steam_workshop" if normalized_item_id else (_derive_live2d_asset_source(normalized_model) or "local_imported")
    resolved_source_id = normalized_item_id

    # 外部链接保持原始绑定，不回绑到本地目录/创意工坊目录。
    if resolved_source == "manual_external":
        return normalized_model or resolved_model_path, resolved_source_id, resolved_source

    try:
        all_models = find_models()
        matching_model = _find_live2d_model_catalog_entry(
            all_models,
            model_name=live2d_name,
            model_path=normalized_model,
            asset_source=resolved_source,
            item_id=normalized_item_id,
        )
        if matching_model is not None:
            matched_path = str(matching_model.get("path") or "").strip().replace("\\", "/")
            resolved_model_path = _normalize_live2d_catalog_path(matched_path) or resolved_model_path
            resolved_source = _derive_live2d_asset_source(matched_path) or resolved_source
            resolved_source_id = normalized_item_id or str(matching_model.get("item_id") or "").strip()
    except Exception as exc:
        logger.debug("解析 Live2D 模型绑定时查找模型目录失败: %s", exc)

    return resolved_model_path, resolved_source_id, resolved_source


@router.get('/current_live2d_model')
async def get_current_live2d_model(catgirl_name: str = "", item_id: str = ""):
    """Get Live2D model info for the specified or current character.

    Args:
        catgirl_name: character name
        item_id: optional item ID to directly specify the model
    """
    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        # 如果没有指定角色名称，使用当前猫娘
        if not catgirl_name:
            catgirl_name = characters.get('当前猫娘', '')

        # 查找指定角色的Live2D模型
        live2d_model_name = None
        model_info = None
        saved_model_path = ""
        saved_asset_source = ""
        saved_item_id = ""

        # 首先尝试通过item_id查找模型
        if item_id:
            try:
                logger.debug(f"尝试通过item_id {item_id} 查找模型")
                # 获取所有模型
                all_models = find_models()
                # 查找匹配item_id的模型
                matching_model = next((m for m in all_models if m.get('item_id') == item_id), None)

                if matching_model:
                    logger.debug(f"通过item_id找到模型: {matching_model['name']}")
                    # 复制模型信息
                    model_info = matching_model.copy()
                    live2d_model_name = model_info['name']
            except Exception as e:
                logger.warning(f"通过item_id查找模型失败: {e}")

        # 如果没有通过item_id找到模型，再通过角色名称查找
        if not model_info and catgirl_name:
            # 在猫娘列表中查找
            if '猫娘' in characters and catgirl_name in characters['猫娘']:
                catgirl_data = characters['猫娘'][catgirl_name]
                saved_model_path = get_reserved(
                    catgirl_data,
                    'avatar',
                    'live2d',
                    'model_path',
                    default='',
                    legacy_keys=('live2d',),
                )
                live2d_model_name = _derive_live2d_model_name(saved_model_path)
                saved_asset_source = get_reserved(
                    catgirl_data,
                    'avatar',
                    'asset_source',
                    default='',
                )

                # 检查是否有保存的item_id
                saved_item_id = get_reserved(
                    catgirl_data,
                    'avatar',
                    'asset_source_id',
                    default='',
                    legacy_keys=('live2d_item_id', 'item_id'),
                )
                if saved_item_id:
                    logger.debug(f"发现角色 {catgirl_name} 保存的item_id: {saved_item_id}")
                    try:
                        # 尝试通过保存的item_id查找模型
                        all_models = find_models()
                        matching_model = _find_live2d_model_catalog_entry(
                            all_models,
                            model_name=live2d_model_name,
                            model_path=saved_model_path,
                            asset_source=saved_asset_source,
                            item_id=saved_item_id,
                        )
                        if matching_model:
                            logger.debug(f"通过保存的item_id找到模型: {matching_model['name']}")
                            model_info = matching_model.copy()
                            live2d_model_name = model_info['name']
                    except Exception as e:
                        logger.warning(f"通过保存的item_id查找模型失败: {e}")

        # 如果找到了模型名称，获取模型信息
        if live2d_model_name:
            try:
                # 先从完整的模型列表中查找，这样可以获取到item_id等完整信息
                all_models = find_models()

                # 同时获取工坊模型列表，确保能找到工坊模型
                try:
                    from ..workshop_router import get_subscribed_workshop_items
                    workshop_result = await get_subscribed_workshop_items()
                    if isinstance(workshop_result, dict) and workshop_result.get('success', False):
                        for item in workshop_result.get('items', []):
                            installed_folder = item.get('installedFolder')
                            workshop_item_id = item.get('publishedFileId')
                            if installed_folder and os.path.exists(installed_folder) and os.path.isdir(installed_folder) and workshop_item_id:
                                # 检查安装目录下是否有.model3.json文件
                                for filename in os.listdir(installed_folder):
                                    if filename.endswith('.model3.json'):
                                        model_name = os.path.splitext(os.path.splitext(filename)[0])[0]
                                        if model_name not in [m['name'] for m in all_models]:
                                            all_models.append({
                                                'name': model_name,
                                                'path': f'/workshop/{workshop_item_id}/{filename}',
                                                'source': 'steam_workshop',
                                                'item_id': workshop_item_id
                                            })
                                # 检查子目录
                                for subdir in os.listdir(installed_folder):
                                    subdir_path = os.path.join(installed_folder, subdir)
                                    if os.path.isdir(subdir_path):
                                        model_name = subdir
                                        model3_files = [f for f in os.listdir(subdir_path) if f.endswith('.model3.json')]
                                        if model3_files:
                                            model_file = model3_files[0]
                                            if model_name not in [m['name'] for m in all_models]:
                                                all_models.append({
                                                    'name': model_name,
                                                    'path': encode_url_path(f'/workshop/{workshop_item_id}/{model_name}/{model_file}'),
                                                    'source': 'steam_workshop',
                                                    'item_id': workshop_item_id
                                                })
                except Exception as we:
                    logger.debug(f"获取工坊模型列表时出错（非关键）: {we}")

                matching_model = model_info.copy() if model_info else None
                if matching_model is None:
                    # 保留前面已命中的 item_id 结果；仅在没有现成匹配时再做目录级回退查找。
                    matching_model = _find_live2d_model_catalog_entry(
                        all_models,
                        model_name=live2d_model_name,
                        model_path=saved_model_path,
                        asset_source=saved_asset_source,
                        item_id=saved_item_id,
                    )
                elif not item_id and not saved_item_id:
                    fallback_model = _find_live2d_model_catalog_entry(
                        all_models,
                        model_name=live2d_model_name,
                        model_path=saved_model_path,
                        asset_source=saved_asset_source,
                        item_id='',
                    )
                    if fallback_model is not None:
                        matching_model = fallback_model

                if matching_model:
                    # 使用完整的模型信息，包含item_id
                    model_info = matching_model.copy()
                    logger.debug(f"从完整模型列表获取模型信息: {model_info}")
                else:
                    # 如果在完整列表中找不到，回退到原来的逻辑
                    model_dir, url_prefix = find_model_directory(live2d_model_name)
                    if model_dir and os.path.exists(model_dir):
                        # 查找模型配置文件
                        model_files = [f for f in os.listdir(model_dir) if f.endswith('.model3.json')]
                        if model_files:
                            model_file = model_files[0]

                            # 使用保存的item_id构建model_path，从之前的逻辑中获取saved_item_id
                            saved_item_id = (
                                get_reserved(
                                    catgirl_data,
                                    'avatar',
                                    'asset_source_id',
                                    default='',
                                    legacy_keys=('live2d_item_id', 'item_id'),
                                ) if 'catgirl_data' in locals() else ''
                            )

                            # 如果有保存的item_id，使用它构建路径
                            if saved_item_id:
                                if url_prefix == '/workshop':
                                    model_subdir = os.path.basename(model_dir.rstrip('/\\'))
                                    model_path = encode_url_path(f'{url_prefix}/{saved_item_id}/{model_subdir}/{model_file}')
                                else:
                                    model_path = encode_url_path(f'{url_prefix}/{saved_item_id}/{model_file}')
                                logger.debug(f"使用保存的item_id构建模型路径: {model_path}")
                            else:
                                # 原始路径构建逻辑
                                model_path = encode_url_path(f'{url_prefix}/{live2d_model_name}/{model_file}')
                                logger.debug(f"使用模型名称构建路径: {model_path}")

                            model_info = {
                                'name': live2d_model_name,
                                'item_id': saved_item_id,
                                'path': model_path
                            }
            except Exception as e:
                logger.warning(f"获取模型信息失败: {e}")

        # 回退机制：如果没有找到模型，使用默认模型 (DEFAULT_LIVE2D_MODEL_NAME)
        if not live2d_model_name or not model_info:
            logger.info(
                f"猫娘 {catgirl_name} 未设置Live2D模型，回退到默认模型 "
                f"{DEFAULT_LIVE2D_MODEL_NAME}"
            )
            live2d_model_name = DEFAULT_LIVE2D_MODEL_NAME
            try:
                # 先从完整的模型列表中查找内置/static 默认模型，避免误匹配用户/工坊同名模型
                all_models = find_models()
                matching_model = next(
                    (
                        m for m in all_models
                        if m.get('name') == DEFAULT_LIVE2D_MODEL_NAME
                        and m.get('source') in ('static', 'builtin')
                    ),
                    None,
                )
                if matching_model is None:
                    matching_model = next(
                        (m for m in all_models if m.get('name') == DEFAULT_LIVE2D_MODEL_NAME),
                        None,
                    )

                if matching_model:
                    model_info = matching_model.copy()
                    model_info['is_fallback'] = True
                else:
                    # 如果找不到，回退到原来的逻辑
                    model_dir, url_prefix = find_model_directory(DEFAULT_LIVE2D_MODEL_NAME)
                    if model_dir and os.path.exists(model_dir):
                        model_files = [f for f in os.listdir(model_dir) if f.endswith('.model3.json')]
                        if model_files:
                            model_file = model_files[0]
                            model_path = f'{url_prefix}/{DEFAULT_LIVE2D_MODEL_NAME}/{model_file}'
                            model_info = {
                                'name': DEFAULT_LIVE2D_MODEL_NAME,
                                'path': model_path,
                                'is_fallback': True  # 标记这是回退模型
                            }
            except Exception as e:
                logger.error(
                    f"获取默认模型 {DEFAULT_LIVE2D_MODEL_NAME} 失败: {e}"
                )

        if model_info and isinstance(model_info.get('path'), str):
            model_info['path'] = encode_url_path(model_info['path'])

        if not model_info or not model_info.get('path'):
            error_message = f"默认Live2D模型 {DEFAULT_LIVE2D_MODEL_NAME} 不可用"
            logger.error(error_message)
            return JSONResponse(content={
                'success': False,
                'catgirl_name': catgirl_name,
                'model_name': live2d_model_name or DEFAULT_LIVE2D_MODEL_NAME,
                'model_info': None,
                'error': error_message,
            })

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


@router.put('/catgirl/l2d/{name}')
async def update_catgirl_l2d(name: str, request: Request):
    """Update the specified catgirl's model settings (supports Live2D and VRM)."""
    try:
        data = await request.json()
        live2d_model = data.get('live2d')
        vrm_model = data.get('vrm')
        mmd_model = data.get('mmd')
        model_type = data.get('model_type', 'live2d')  # 默认为live2d以保持兼容性
        item_id = data.get('item_id')  # 获取可选的item_id
        vrm_animation = data.get('vrm_animation')  # 获取可选的VRM动作
        idle_animation = data.get('idle_animation')  # 获取可选的VRM待机动作
        mmd_animation = data.get('mmd_animation')  # 获取可选的MMD动作
        mmd_idle_animation = data.get('mmd_idle_animation')  # 获取可选的MMD待机动作

        # 根据model_type检查相应的模型字段
        model_type_str = str(model_type).lower() if model_type else 'live2d'

        # 【修复】model_type 只允许 {live2d, vrm, live3d, pngtuber}，否则 400
        if model_type_str not in ['live2d', 'vrm', 'live3d', 'pngtuber']:
            return JSONResponse(
                content={
                    'success': False,
                    'error': f'无效的模型类型: {model_type}，只允许 live2d、vrm、live3d 或 pngtuber'
                },
                status_code=400
            )

        # 归一化：旧客户端发送的 'vrm' 统一为 'live3d'（走 Live3D VRM 子分支处理）
        if model_type_str == 'vrm':
            model_type_str = 'live3d'

        if model_type_str == 'pngtuber':
            raw_pngtuber = data.get('pngtuber') if isinstance(data.get('pngtuber'), dict) else {}
            pngtuber_payload = dict(raw_pngtuber)
            for key in ('idle_image', 'talking_image', 'drag_image', 'click_image', 'happy_image', 'sad_image', 'angry_image', 'surprised_image'):
                if key not in pngtuber_payload and key in data:
                    pngtuber_payload[key] = data.get(key)
            allowed_prefixes = ('/user_pngtuber/', '/static/', '/workshop/')
            allowed_exts = ('.png', '.gif', '.jpg', '.jpeg', '.webp')
            idle_image = str(pngtuber_payload.get('idle_image') or '').strip().replace('\\', '/')
            if not idle_image:
                return JSONResponse(content={'success': False, 'error': '未提供PNGTuber idle_image'}, status_code=400)
            for key in ('idle_image', 'talking_image', 'drag_image', 'click_image', 'happy_image', 'sad_image', 'angry_image', 'surprised_image'):
                image_path = str(pngtuber_payload.get(key) or '').strip().replace('\\', '/')
                if not image_path:
                    pngtuber_payload[key] = ''
                    continue
                if image_path.startswith('data:'):
                    return JSONResponse(content={'success': False, 'error': f'PNGTuber图片路径不能使用data URL: {key}'}, status_code=400)
                if '..' in image_path:
                    return JSONResponse(content={'success': False, 'error': f'PNGTuber图片路径不能包含路径遍历（..）: {key}'}, status_code=400)
                is_remote_image = image_path.startswith('http://') or image_path.startswith('https://')
                if not is_remote_image and not any(image_path.startswith(prefix) for prefix in allowed_prefixes):
                    return JSONResponse(content={'success': False, 'error': f'PNGTuber图片路径必须以 /user_pngtuber/、/static/ 或 /workshop/ 开头: {key}'}, status_code=400)
                extension_path = image_path.lower().split('?', 1)[0].split('#', 1)[0]
                if not extension_path.endswith(allowed_exts):
                    return JSONResponse(content={'success': False, 'error': f'PNGTuber图片格式必须是 PNG/GIF/JPG/JPEG/WebP: {key}'}, status_code=400)
                pngtuber_payload[key] = image_path

            metadata_path = str(
                pngtuber_payload.get('layered_metadata')
                or pngtuber_payload.get('metadata')
                or ''
            ).strip().replace('\\', '/')

            def _infer_pngtuber_metadata_from_idle(idle_path: str) -> str:
                parts = [part for part in idle_path.split('/') if part]
                if len(parts) < 3:
                    return ''
                source_prefix = parts[0]
                model_folder = parts[1]
                try:
                    config_manager = get_config_manager()
                    if source_prefix == 'user_pngtuber':
                        root = config_manager.pngtuber_dir / model_folder
                        url_prefix = '/user_pngtuber'
                    elif source_prefix == 'static':
                        root = config_manager.project_root / 'static' / model_folder
                        url_prefix = '/static'
                    elif source_prefix == 'workshop':
                        root = config_manager.workshop_dir / model_folder
                        url_prefix = '/workshop'
                    else:
                        return ''
                except Exception:
                    return ''
                for filename in (
                    'metadata.pngtube-remix.json',
                    'metadata.pngtuber-plus.json',
                    'metadata.json',
                ):
                    if (root / filename).is_file():
                        return f'{url_prefix}/{model_folder}/{filename}'
                return ''

            if not metadata_path:
                metadata_path = _infer_pngtuber_metadata_from_idle(idle_image)

            if metadata_path:
                if metadata_path.startswith('data:'):
                    return JSONResponse(content={'success': False, 'error': 'PNGTuber分层metadata路径不能使用data URL'}, status_code=400)
                if '..' in metadata_path:
                    return JSONResponse(content={'success': False, 'error': 'PNGTuber分层metadata路径不能包含路径遍历（..）'}, status_code=400)
                is_remote_metadata = metadata_path.startswith('http://') or metadata_path.startswith('https://')
                if not is_remote_metadata and not any(metadata_path.startswith(prefix) for prefix in allowed_prefixes):
                    return JSONResponse(content={'success': False, 'error': 'PNGTuber分层metadata路径必须以 /user_pngtuber/、/static/ 或 /workshop/ 开头'}, status_code=400)
                metadata_ext_path = metadata_path.lower().split('?', 1)[0].split('#', 1)[0]
                if not metadata_ext_path.endswith('.json'):
                    return JSONResponse(content={'success': False, 'error': 'PNGTuber分层metadata必须是 JSON 文件'}, status_code=400)
                pngtuber_payload['layered_metadata'] = metadata_path
                pngtuber_payload['adapter'] = 'layered_canvas_v1'
            else:
                pngtuber_payload['layered_metadata'] = ''
                pngtuber_payload['adapter'] = ''

            for key in ('source_type', 'source_format'):
                value = str(pngtuber_payload.get(key) or '').strip()
                pngtuber_payload[key] = value

            def _bounded_number(value, default, min_value, max_value):
                try:
                    parsed = float(value)
                except (TypeError, ValueError):
                    return default
                if not math.isfinite(parsed):
                    raise ValueError('数值字段必须是有限值')
                return max(min_value, min(max_value, parsed))

            try:
                pngtuber_payload['scale'] = _bounded_number(pngtuber_payload.get('scale'), 1, 0.1, 5)
                pngtuber_payload['offset_x'] = _bounded_number(pngtuber_payload.get('offset_x'), 0, -5000, 5000)
                pngtuber_payload['offset_y'] = _bounded_number(pngtuber_payload.get('offset_y'), 0, -5000, 5000)
                pngtuber_payload['mobile_scale'] = _bounded_number(
                    pngtuber_payload.get('mobile_scale'),
                    min(pngtuber_payload['scale'], 1),
                    0.1,
                    5,
                )
                pngtuber_payload['mobile_offset_x'] = _bounded_number(pngtuber_payload.get('mobile_offset_x'), 0, -5000, 5000)
                pngtuber_payload['mobile_offset_y'] = _bounded_number(pngtuber_payload.get('mobile_offset_y'), 0, -5000, 5000)
            except ValueError as exc:
                return JSONResponse(content={'success': False, 'error': str(exc)}, status_code=400)
            pngtuber_payload['mirror'] = _config_value_is_enabled(pngtuber_payload.get('mirror'))

        if model_type_str == 'live3d':
            # Live3D 模式：接受 VRM 或 MMD 模型
            if vrm_model and mmd_model:
                return JSONResponse(content={'success': False, 'error': '不能同时提供VRM和MMD模型，请选择其中一个'}, status_code=400)
            if vrm_model:
                # 验证 VRM 路径
                vrm_model_str = str(vrm_model).strip()
                if '://' in vrm_model_str or vrm_model_str.startswith('data:'):
                    return JSONResponse(content={'success': False, 'error': 'VRM模型路径不能包含URL方案'}, status_code=400)
                if '..' in vrm_model_str:
                    return JSONResponse(content={'success': False, 'error': 'VRM模型路径不能包含路径遍历（..）'}, status_code=400)
                allowed_prefixes = ['/user_vrm/', '/static/vrm/', '/workshop/']
                if not any(vrm_model_str.startswith(prefix) for prefix in allowed_prefixes):
                    return JSONResponse(content={'success': False, 'error': 'VRM模型路径必须以 /user_vrm/、/static/vrm/ 或 /workshop/ 开头'}, status_code=400)
                vrm_model = vrm_model_str
            elif mmd_model:
                # 验证 MMD 路径
                mmd_model_str = str(mmd_model).strip()
                if '://' in mmd_model_str or mmd_model_str.startswith('data:'):
                    return JSONResponse(content={'success': False, 'error': 'MMD模型路径不能包含URL方案'}, status_code=400)
                if '..' in mmd_model_str:
                    return JSONResponse(content={'success': False, 'error': 'MMD模型路径不能包含路径遍历（..）'}, status_code=400)
                allowed_mmd_prefixes = ['/user_mmd/', '/static/mmd/', '/workshop/']
                if not any(mmd_model_str.startswith(prefix) for prefix in allowed_mmd_prefixes):
                    return JSONResponse(content={'success': False, 'error': 'MMD模型路径必须以 /user_mmd/、/static/mmd/ 或 /workshop/ 开头'}, status_code=400)
                mmd_model = mmd_model_str
            else:
                return JSONResponse(content={'success': False, 'error': '未提供VRM或MMD模型路径'}, status_code=400)
        elif model_type_str != 'pngtuber':
            if not live2d_model:
                return JSONResponse(
                    content={
                        'success': False,
                        'error': '未提供Live2D模型名称'
                    },
                    status_code=400
                )

        # 加载当前角色配置
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        # 确保猫娘配置存在
        if '猫娘' not in characters:
            characters['猫娘'] = {}

        # 确保指定猫娘的配置存在
        if name not in characters['猫娘']:
            return JSONResponse(
                {'success': False, 'error': '猫娘不存在'},
                status_code=404
            )

        # 切换模型类型时保留非当前模型配置，避免来回切换后丢失待机动作/光照等设置
        if model_type_str == 'live3d':
            set_reserved(characters['猫娘'][name], 'avatar', 'model_type', 'live3d')
            active_model_binding_path = ""

            if vrm_model:
                # Live3D + VRM：更新当前激活的 VRM 配置，保留 MMD 配置便于切回
                set_reserved(characters['猫娘'][name], 'avatar', 'live3d_sub_type', 'vrm')
                set_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'model_path', vrm_model)
                active_model_binding_path = vrm_model

                # 处理 VRM 动画（复用同样的验证逻辑）
                if 'vrm_animation' in data:
                    if vrm_animation is None or vrm_animation == '':
                        set_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'animation', None)
                    else:
                        vrm_animation_str = str(vrm_animation).strip()
                        if '://' in vrm_animation_str or vrm_animation_str.startswith('data:'):
                            return JSONResponse(content={'success': False, 'error': 'VRM动画路径不能包含URL方案'}, status_code=400)
                        if '..' in vrm_animation_str:
                            return JSONResponse(content={'success': False, 'error': 'VRM动画路径不能包含路径遍历（..）'}, status_code=400)
                        allowed_animation_prefixes = ['/user_vrm/animation/', '/static/vrm/animation/']
                        if not any(vrm_animation_str.startswith(prefix) for prefix in allowed_animation_prefixes):
                            return JSONResponse(content={'success': False, 'error': 'VRM动画路径必须以 /user_vrm/animation/ 或 /static/vrm/animation/ 开头'}, status_code=400)
                        set_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'animation', vrm_animation_str)

                if 'idle_animation' in data:
                    if idle_animation is None or idle_animation == '' or idle_animation == []:
                        set_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'idle_animation', [])
                    elif isinstance(idle_animation, str):
                        idle_list = [idle_animation]
                    elif isinstance(idle_animation, list):
                        idle_list = idle_animation
                    else:
                        return JSONResponse(content={'success': False, 'error': 'idle_animation must be a string or list of strings'}, status_code=400)
                    if isinstance(idle_animation, (str, list)) and idle_animation:
                        allowed_animation_prefixes = ['/user_vrm/animation/', '/static/vrm/animation/']
                        for item in idle_list:
                            item_str = str(item).strip()
                            if '://' in item_str or item_str.startswith('data:'):
                                return JSONResponse(content={'success': False, 'error': '待机动作路径不能包含URL方案'}, status_code=400)
                            if '..' in item_str:
                                return JSONResponse(content={'success': False, 'error': '待机动作路径不能包含路径遍历（..）'}, status_code=400)
                            if not any(item_str.startswith(prefix) for prefix in allowed_animation_prefixes):
                                return JSONResponse(content={'success': False, 'error': '待机动作路径必须以 /user_vrm/animation/ 或 /static/vrm/animation/ 开头'}, status_code=400)
                        set_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'idle_animation', [str(x).strip() for x in idle_list])

                logger.debug(f"已保存角色 {name} 的Live3D(VRM)模型 {vrm_model}")
            elif mmd_model:
                # Live3D + MMD：更新当前激活的 MMD 配置，保留 VRM 配置便于切回
                set_reserved(characters['猫娘'][name], 'avatar', 'live3d_sub_type', 'mmd')
                set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'model_path', mmd_model)
                active_model_binding_path = mmd_model

                # 处理 MMD 动画
                if 'mmd_animation' in data:
                    if mmd_animation is None or mmd_animation == '':
                        set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'animation', None)
                    else:
                        mmd_animation_str = str(mmd_animation).strip()
                        if '://' in mmd_animation_str or mmd_animation_str.startswith('data:'):
                            return JSONResponse(content={'success': False, 'error': 'MMD动画路径不能包含URL方案'}, status_code=400)
                        if '..' in mmd_animation_str:
                            return JSONResponse(content={'success': False, 'error': 'MMD动画路径不能包含路径遍历（..）'}, status_code=400)
                        allowed_mmd_anim_prefixes = ['/user_mmd/animation/', '/static/mmd/animation/']
                        if not any(mmd_animation_str.startswith(prefix) for prefix in allowed_mmd_anim_prefixes):
                            return JSONResponse(content={'success': False, 'error': 'MMD动画路径必须以 /user_mmd/animation/ 或 /static/mmd/animation/ 开头'}, status_code=400)
                        set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'animation', mmd_animation_str)

                if 'mmd_idle_animation' in data:
                    if mmd_idle_animation is None or mmd_idle_animation == '' or mmd_idle_animation == []:
                        set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'idle_animation', [])
                    elif isinstance(mmd_idle_animation, str):
                        mmd_idle_list = [mmd_idle_animation]
                    elif isinstance(mmd_idle_animation, list):
                        mmd_idle_list = mmd_idle_animation
                    else:
                        return JSONResponse(content={'success': False, 'error': 'mmd_idle_animation must be a string or list of strings'}, status_code=400)
                    if isinstance(mmd_idle_animation, (str, list)) and mmd_idle_animation:
                        allowed_mmd_anim_prefixes = ['/user_mmd/animation/', '/static/mmd/animation/']
                        for item in mmd_idle_list:
                            mmd_idle_str = str(item).strip()
                            if '://' in mmd_idle_str or mmd_idle_str.startswith('data:'):
                                return JSONResponse(content={'success': False, 'error': 'MMD待机动作路径不能包含URL方案'}, status_code=400)
                            if '..' in mmd_idle_str:
                                return JSONResponse(content={'success': False, 'error': 'MMD待机动作路径不能包含路径遍历（..）'}, status_code=400)
                            if not any(mmd_idle_str.startswith(prefix) for prefix in allowed_mmd_anim_prefixes):
                                return JSONResponse(content={'success': False, 'error': 'MMD待机动作路径必须以 /user_mmd/animation/ 或 /static/mmd/animation/ 开头'}, status_code=400)
                        set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'idle_animation', [str(x).strip() for x in mmd_idle_list])

                logger.debug(f"已保存角色 {name} 的Live3D(MMD)模型 {mmd_model}")

            current_asset_source, current_asset_source_id = _derive_model_asset_binding(
                active_model_binding_path,
                item_id=str(item_id or ""),
            )
            set_reserved(characters['猫娘'][name], 'avatar', 'asset_source_id', current_asset_source_id)
            set_reserved(
                characters['猫娘'][name],
                'avatar',
                'asset_source',
                current_asset_source or 'local_imported',
            )
        elif model_type_str == 'pngtuber':
            set_reserved(characters['猫娘'][name], 'avatar', 'model_type', 'pngtuber')
            set_reserved(characters['猫娘'][name], 'avatar', 'live3d_sub_type', '')
            set_reserved(characters['猫娘'][name], 'avatar', 'pngtuber', pngtuber_payload)
            pngtuber_binding_path = str(
                idle_image
                or pngtuber_payload.get('layered_metadata')
                or ''
            ).strip()
            pngtuber_binding_item_id = str(item_id or "").strip()
            if not pngtuber_binding_path.startswith('/workshop/'):
                pngtuber_binding_item_id = ''
            current_asset_source, current_asset_source_id = _derive_model_asset_binding(
                pngtuber_binding_path,
                item_id=pngtuber_binding_item_id,
            )
            set_reserved(characters['猫娘'][name], 'avatar', 'asset_source_id', current_asset_source_id)
            set_reserved(characters['猫娘'][name], 'avatar', 'asset_source', current_asset_source or 'local_imported')
            logger.debug(f"已保存角色 {name} 的PNGTuber配置")
        else:
            # 更新Live2D模型设置，同时保存item_id（如果有）
            live2d_model_path, resolved_item_id, resolved_asset_source = _resolve_live2d_model_binding(
                live2d_model,
                item_id=str(item_id or ""),
            )
            set_reserved(
                characters['猫娘'][name],
                'avatar',
                'live2d',
                'model_path',
                live2d_model_path,
            )
            set_reserved(characters['猫娘'][name], 'avatar', 'model_type', 'live2d')

            if 'live2d_idle_animation' in data:
                live2d_idle_animation = data.get('live2d_idle_animation')
                logger.info(f"[Live2D Save] 收到 live2d_idle_animation 请求: {live2d_idle_animation}")

                if live2d_idle_animation is None:
                    set_reserved(characters['猫娘'][name], 'avatar', 'live2d', 'idle_animation', None)
                    logger.info(f"[Live2D Save] 已清空 idle_animation")
                elif isinstance(live2d_idle_animation, str):
                    live2d_idle_str = live2d_idle_animation.strip()
                    if not live2d_idle_str:
                        set_reserved(characters['猫娘'][name], 'avatar', 'live2d', 'idle_animation', None)
                        logger.info(f"[Live2D Save] 已清空 idle_animation")
                    else:
                        if '://' in live2d_idle_str or live2d_idle_str.startswith('data:'):
                            return JSONResponse(content={'success': False, 'error': 'Live2D待机动作路径不能包含URL方案'}, status_code=400)
                        if '..' in live2d_idle_str:
                            return JSONResponse(content={'success': False, 'error': 'Live2D待机动作路径不能包含路径遍历（..）'}, status_code=400)
                        if live2d_idle_str.startswith('/') or live2d_idle_str.startswith('\\') or re.match(r'^[A-Za-z]:', live2d_idle_str):
                            return JSONResponse(content={'success': False, 'error': 'Live2D待机动作路径必须是相对路径，不能是绝对路径'}, status_code=400)
                        if not live2d_idle_str.lower().endswith('.motion3.json'):
                            return JSONResponse(content={'success': False, 'error': 'Live2D待机动作必须是 .motion3.json 文件'}, status_code=400)
                        set_reserved(characters['猫娘'][name], 'avatar', 'live2d', 'idle_animation', live2d_idle_str)
                        logger.info(f"[Live2D Save] 已保存 idle_animation: {live2d_idle_str}")
                else:
                    return JSONResponse(content={'success': False, 'error': 'live2d_idle_animation 必须是字符串或 null'}, status_code=400)
            else:
                logger.info(f"[Live2D Save] 请求中未包含 live2d_idle_animation 字段, data keys: {list(data.keys())}")

            if resolved_item_id:
                set_reserved(characters['猫娘'][name], 'avatar', 'asset_source_id', str(resolved_item_id))
                set_reserved(characters['猫娘'][name], 'avatar', 'asset_source', 'steam_workshop')
                logger.debug(f"已保存角色 {name} 的模型 {live2d_model} 和item_id {resolved_item_id}")
            else:
                set_reserved(characters['猫娘'][name], 'avatar', 'asset_source_id', '')
                set_reserved(characters['猫娘'][name], 'avatar', 'asset_source', resolved_asset_source or 'local_imported')
                logger.debug(f"已保存角色 {name} 的模型 {live2d_model}，asset_source={resolved_asset_source or 'local_imported'}")

        # 保存配置
        await _config_manager.asave_characters(characters)
        # Fast path：只刷新被编辑角色的 session_manager（avatar 配置），不遍历其它 N-1 个。
        init_one_catgirl = get_init_one_catgirl()
        await init_one_catgirl(name, is_new=False)


        if model_type_str == 'live3d':
            active_model = vrm_model or mmd_model
            sub_type = 'VRM' if vrm_model else 'MMD'
            message = f'已更新角色 {name} 的Live3D({sub_type})模型为 {active_model}'
        elif model_type_str == 'pngtuber':
            message = f'已更新角色 {name} 的PNGTuber配置'
        else:
            message = f'已更新角色 {name} 的Live2D模型为 {live2d_model}'

        return JSONResponse(content={
            'success': True,
            'message': message
        })

    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.exception("更新角色模型设置失败")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        })


@router.patch('/catgirl/{name}/touch_set')
async def update_catgirl_touch_set(name: str, request: Request):
    """Fully replace the touch animation config of the specified catgirl's current model.

    Request body format:
    {
        "model_name": "model name",
        "touch_set": {
            "default": {"motions": [], "expressions": []},
            "HitArea1": {"motions": ["motion1"], "expressions": ["exp1"]}
        }
    }
    """
    try:
        data = await request.json()

        model_name = data.get('model_name')
        touch_set_data = data.get('touch_set')

        if not isinstance(model_name, str) or not model_name.strip():
            return JSONResponse(
                content={'success': False, 'error': 'model_name 必须是非空字符串'},
                status_code=400
            )
        model_name = model_name.strip()

        if touch_set_data is None:
            return JSONResponse(
                content={'success': False, 'error': '缺少 touch_set 参数'},
                status_code=400
            )

        if not isinstance(touch_set_data, dict):
            return JSONResponse(
                content={'success': False, 'error': 'touch_set 必须是对象'},
                status_code=400
            )

        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if '猫娘' not in characters or name not in characters['猫娘']:
            return JSONResponse(
                content={'success': False, 'error': '角色不存在'},
                status_code=404
            )

        existing_touch_set = get_reserved(characters['猫娘'][name], 'touch_set', default={})

        if not existing_touch_set:
            existing_touch_set = {}

        existing_touch_set[model_name] = touch_set_data

        set_reserved(characters['猫娘'][name], 'touch_set', existing_touch_set)
        await _config_manager.asave_characters(characters)

        # Fast path：只刷新被编辑角色的 session_manager（touch_set），不遍历其它 N-1 个。
        init_one_catgirl = get_init_one_catgirl()
        if init_one_catgirl:
            await init_one_catgirl(name, is_new=False)

        logger.debug(f"已更新角色 {name} 模型 {model_name} 的触摸配置")

        return JSONResponse(content={
            'success': True,
            'message': f'已更新角色 {name} 的触摸配置',
            'touch_set': existing_touch_set
        })

    except Exception as e:
        logger.exception("更新触摸配置失败")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.put('/catgirl/{name}/lighting')
async def update_catgirl_lighting(name: str, request: Request):
    """Update the specified catgirl's VRM lighting config.

    Args:
        name: character name
        request: body containing lighting (dict) and an optional apply_runtime (bool);
                 apply_runtime can also be passed as a query param, which takes precedence
    """
    try:
        data = await request.json()
        lighting = data.get('lighting')

        apply_runtime = data.get('apply_runtime', False)
        query_params = request.query_params
        if 'apply_runtime' in query_params:
            apply_runtime = query_params.get('apply_runtime', '').lower() in ('true', '1', 'yes')

        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if '猫娘' not in characters or name not in characters['猫娘']:
            return JSONResponse(content={
                'success': False,
                'error': '角色不存在'
            }, status_code=404)

        model_type = get_reserved(
            characters['猫娘'][name],
            'avatar',
            'model_type',
            default='live2d',
            legacy_keys=('model_type',),
        )
        # 统一做 .lower() 处理，避免大小写/空值导致误判
        model_type_normalized = str(model_type).lower() if model_type else 'live2d'
        if model_type_normalized not in ('vrm', 'live3d'):
            logger.warning(f"角色 {name} 不是VRM/Live3D模型，但仍保存打光配置")

        from config import get_default_vrm_lighting
        existing_lighting = get_reserved(
            characters['猫娘'][name],
            'avatar',
            'vrm',
            'lighting',
            default=None,
            legacy_keys=('lighting',),
        )
        if isinstance(existing_lighting, dict):
            base_lighting = existing_lighting
        else:
            base_lighting = get_default_vrm_lighting()

        if not isinstance(lighting, dict):
            return JSONResponse(content={
                'success': False,
                'error': 'lighting 必须是对象'
            }, status_code=400)

        lighting = {**base_lighting, **lighting}

        from config import VRM_LIGHTING_RANGES
        lighting_ranges = VRM_LIGHTING_RANGES

        for key, (min_val, max_val) in lighting_ranges.items():
            if key not in lighting:
                return JSONResponse(content={
                    'success': False,
                    'error': f'缺少打光参数: {key}'
                }, status_code=400)

            val = lighting[key]
            if not isinstance(val, (int, float)) or not (min_val <= val <= max_val):
                return JSONResponse(content={
                    'success': False,
                    'error': f'打光参数 {key} 超出范围 ({min_val}-{max_val})'
                }, status_code=400)


        set_reserved(
            characters['猫娘'][name],
            'avatar',
            'vrm',
            'lighting',
            {key: float(lighting[key]) for key in lighting_ranges.keys()},
        )



        logger.info(
            "已保存角色 %s 的打光配置: %s",
            name,
            get_reserved(characters['猫娘'][name], 'avatar', 'vrm', 'lighting', default=None),
        )

        await _config_manager.asave_characters(characters)

        if apply_runtime:
            # Fast path：只刷新被编辑角色的 session_manager（lighting），不遍历其它 N-1 个。
            init_one_catgirl = get_init_one_catgirl()
            if init_one_catgirl:
                await init_one_catgirl(name, is_new=False)
                logger.info(f"已应用到运行时（角色 {name} 的打光配置）")
        else:
            logger.debug("跳过运行时刷新（apply_runtime=False），配置已保存到磁盘，需要刷新页面或调用重载才能生效")

        if apply_runtime:
            message = f'已保存角色 {name} 的打光配置并已应用到运行时'
        else:
            message = f'已保存角色 {name} 的打光配置到磁盘（需要刷新页面或调用重载才能生效）'

        return JSONResponse(content={
            'success': True,
            'message': message,
            'applied_runtime': apply_runtime,
            'needs_reload': not apply_runtime
        })

    except Exception as e:
        logger.error(f"保存打光配置失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.put('/catgirl/{name}/mmd_settings')
async def update_catgirl_mmd_settings(name: str, request: Request):
    """Update the specified character's MMD model settings (lighting, rendering, physics, mouse tracking)."""
    def _to_bool(val):
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ('true', '1', 'yes')
        return bool(val)

    try:
        data = await request.json()

        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if '猫娘' not in characters or name not in characters['猫娘']:
            return JSONResponse(content={
                'success': False,
                'error': '角色不存在'
            }, status_code=404)

        from config import (
            get_default_mmd_settings,
            MMD_LIGHTING_RANGES,
            MMD_RENDERING_RANGES,
            MMD_PHYSICS_RANGES,
            MMD_CURSOR_FOLLOW_RANGES,
        )

        defaults = get_default_mmd_settings()

        # --- 光照 ---
        if 'lighting' in data and isinstance(data['lighting'], dict):
            lighting = {**defaults['lighting'], **data['lighting']}
            for key, (min_val, max_val) in MMD_LIGHTING_RANGES.items():
                if key in lighting:
                    val = lighting[key]
                    if isinstance(val, (int, float)):
                        lighting[key] = max(min_val, min(max_val, float(val)))
            set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'lighting', lighting)

        # --- 渲染 ---
        if 'rendering' in data and isinstance(data['rendering'], dict):
            rendering = {**defaults['rendering'], **data['rendering']}
            for key, (min_val, max_val) in MMD_RENDERING_RANGES.items():
                if key in rendering:
                    val = rendering[key]
                    if isinstance(val, (int, float)):
                        rendering[key] = max(min_val, min(max_val, float(val)))
            if 'outline' in rendering:
                rendering['outline'] = _to_bool(rendering['outline'])
            set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'rendering', rendering)

        # --- 物理 ---
        if 'physics' in data and isinstance(data['physics'], dict):
            physics = {**defaults['physics'], **data['physics']}
            if 'enabled' in physics:
                physics['enabled'] = _to_bool(physics['enabled'])
            for key, (min_val, max_val) in MMD_PHYSICS_RANGES.items():
                if key in physics:
                    val = physics[key]
                    if isinstance(val, (int, float)):
                        physics[key] = max(min_val, min(max_val, float(val)))
            set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'physics', physics)

        # --- 鼠标跟踪 ---
        # 前端发送 camelCase（cursorFollow），兼容 snake_case（cursor_follow）
        cursor_follow_data = data.get('cursorFollow') or data.get('cursor_follow')
        if cursor_follow_data and isinstance(cursor_follow_data, dict):
            cursor_follow = {**defaults['cursor_follow'], **cursor_follow_data}
            for key, (min_val, max_val) in MMD_CURSOR_FOLLOW_RANGES.items():
                if key in cursor_follow:
                    val = cursor_follow[key]
                    if isinstance(val, (int, float)):
                        cursor_follow[key] = max(min_val, min(max_val, float(val)))
            if 'enabled' in cursor_follow:
                cursor_follow['enabled'] = _to_bool(cursor_follow['enabled'])
            set_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'cursor_follow', cursor_follow)

        await _config_manager.asave_characters(characters)

        logger.info("已保存角色 %s 的MMD模型设置", name)
        return JSONResponse(content={
            'success': True,
            'message': f'已保存角色 {name} 的MMD模型设置'
        })

    except Exception as e:
        logger.error(f"保存MMD设置失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.get('/catgirl/{name}/mmd_settings')
async def get_catgirl_mmd_settings(name: str):
    """Get the specified character's MMD model settings."""
    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if '猫娘' not in characters or name not in characters['猫娘']:
            return JSONResponse(content={
                'success': False,
                'error': '角色不存在'
            }, status_code=404)

        from config import get_default_mmd_settings
        defaults = get_default_mmd_settings()

        lighting = get_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'lighting', default=None)
        rendering = get_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'rendering', default=None)
        physics = get_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'physics', default=None)
        cursor_follow = get_reserved(characters['猫娘'][name], 'avatar', 'mmd', 'cursor_follow', default=None)

        return JSONResponse(content={
            'success': True,
            'settings': {
                'lighting': lighting if isinstance(lighting, dict) else defaults['lighting'],
                'rendering': rendering if isinstance(rendering, dict) else defaults['rendering'],
                'physics': physics if isinstance(physics, dict) else defaults['physics'],
                # 使用 camelCase 与前端保持一致
                'cursorFollow': cursor_follow if isinstance(cursor_follow, dict) else defaults['cursor_follow'],
            }
        })

    except Exception as e:
        logger.error(f"获取MMD设置失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        }, status_code=500)
