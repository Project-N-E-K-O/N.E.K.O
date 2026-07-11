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

"""Character card import/export, card meta and card face endpoints.

Split out of the former monolithic ``main_routers/characters_router.py``.
"""

from ._shared import (
    MAX_CARD_FACE_SIZE,
    MAX_UPLOAD_SIZE,
    _UploadTooLargeError,
    _read_limited_stream,
    _validate_existing_character_path_name,
    _validate_profile_name,
    logger,
    router,
)
from .crud import (
    _catgirl_prompt_fields_changed,
    _filter_mutable_catgirl_fields,
    _mark_new_character_greeting_pending_safe,
    _refresh_catgirl_context_after_profile_change,
    _sync_catgirl_field_order,
)
from .pngtuber_assets import (
    _PNGTUBER_CARD_MODEL_DIR,
    _add_pngtuber_assets_to_character_zip,
    _copy_imported_pngtuber_assets,
    _restore_imported_pngtuber_avatar_config,
    _rewrite_imported_pngtuber_refs,
)

import json
import io
import os
import shutil
import asyncio
import copy
import struct
import zlib
from datetime import datetime
from fastapi import Request, File, UploadFile, Form
from fastapi.responses import JSONResponse, Response
from ..shared_state import (
    get_config_manager,
    get_initialize_character_data,
    get_init_one_catgirl,
)
from ..workshop_router import _ugc_sync_lock
from utils.config_manager import (
    get_reserved,
)
from utils.file_utils import atomic_write_json_async, read_json_async
from utils.frontend_utils import find_model_directory, is_user_imported_model
from utils.cloudsave_runtime import MaintenanceModeError
from config import (
    DEFAULT_LIVE2D_MODEL_NAME,
)


def _embed_zip_in_png_chunk(png_data: bytes, zip_data: bytes) -> bytes:
    """Embed ZIP data into a PNG ancillary private chunk (the neKo chunk), inserted before IEND.

    The resulting file is still a valid PNG; any image viewer / Electron can preview it normally.
    """
    # PNG IEND 块固定 12 字节: 00 00 00 00  49 45 4E 44  AE 42 60 82
    if len(png_data) < 12 or png_data[-12:-4] != b'\x00\x00\x00\x00IEND':
        raise ValueError("Invalid PNG: IEND chunk not found at end of file")

    iend = png_data[-12:]
    before_iend = png_data[:-12]

    # 构建 neKo 块: length(4B, big-endian) + type(4B) + data + CRC32(4B)
    chunk_type = b'neKo'
    chunk_length = struct.pack('>I', len(zip_data))
    chunk_crc = struct.pack('>I', zlib.crc32(chunk_type + zip_data) & 0xFFFFFFFF)

    neko_chunk = chunk_length + chunk_type + zip_data + chunk_crc
    return before_iend + neko_chunk + iend


@router.get('/character-card/list')
async def get_character_cards():
    """Get all character cards in the character_cards folder."""
    try:
        # 获取config_manager实例
        config_mgr = get_config_manager()

        # 确保character_cards目录存在
        config_mgr.ensure_chara_directory()

        # 遍历 character_cards 目录下的所有 .chara.json 文件，并行读取
        # （角色卡多时串行 await 会 N 次线程切换 + JSON 解析，整条接口延迟线性增长）
        candidate_filenames = [f for f in os.listdir(config_mgr.chara_dir) if f.endswith('.chara.json')]

        async def _read_one_card(filename: str):
            file_path = os.path.join(config_mgr.chara_dir, filename)
            try:
                data = await read_json_async(file_path)
                if data and data.get('name'):
                    _sync_catgirl_field_order(data)
                    return {
                        'id': filename[:-11],  # 去掉 .chara.json 后缀
                        'name': data['name'],
                        'description': data.get('description', ''),
                        'tags': data.get('tags', []),
                        'rawData': data,
                        'path': file_path,
                    }
            except Exception as e:
                logger.error(f"读取角色卡文件 {filename} 时出错: {e}")
            return None

        results = await asyncio.gather(
            *(_read_one_card(fn) for fn in candidate_filenames),
            return_exceptions=False,
        )
        character_cards = [r for r in results if r is not None]

        logger.info(f"已加载 {len(character_cards)} 个角色卡")
        return {"success": True, "character_cards": character_cards}
    except Exception as e:
        logger.error(f"获取角色卡列表失败: {e}")
        return {"success": False, "error": str(e)}


@router.post('/catgirl/save-to-model-folder')
async def save_catgirl_to_model_folder(request: Request):
    """Save the character card into the model's folder."""
    try:
        data = await request.json()
        chara_data = data.get('charaData')
        model_name = data.get('modelName')  # 接收模型名称而不是路径
        file_name = data.get('fileName')

        if not chara_data or not model_name or not file_name:
            return JSONResponse({"success": False, "error": "缺少必要参数"}, status_code=400)

        # 使用find_model_directory函数查找模型的实际文件系统路径
        model_folder_path, _ = find_model_directory(model_name)

        # 检查模型目录是否存在
        if not model_folder_path:
            return JSONResponse({"success": False, "error": f"无法找到模型目录: {model_name}"}, status_code=404)

        # 检查是否是用户导入的模型，只允许写入用户目录的模型，不允许写入 workshop/static
        config_mgr = get_config_manager()
        is_user_model = is_user_imported_model(model_folder_path, config_mgr)

        if not is_user_model:
            return JSONResponse(
                status_code=403,
                content={
                    "success": False,
                    "error": "只能保存到用户导入的模型目录。请先导入模型到用户模型目录后再保存。"
                }
            )

        # 确保模型文件夹存在
        if not os.path.exists(model_folder_path):
            os.makedirs(model_folder_path, exist_ok=True)
            logger.info(f"已创建模型文件夹: {model_folder_path}")

        # 防路径穿越：只允许文件名，不允许路径
        safe_name = os.path.basename(file_name)
        if safe_name != file_name or ".." in safe_name or safe_name.startswith(("/", "\\")):
            return JSONResponse({"success": False, "error": "非法文件名"}, status_code=400)

        # 保存角色卡到模型文件夹
        file_path = os.path.join(model_folder_path, safe_name)
        await atomic_write_json_async(file_path, chara_data, ensure_ascii=False, indent=2)

        logger.info(f"角色卡已成功保存到模型文件夹: {file_path}")
        return {"success": True, "path": file_path, "modelFolderPath": model_folder_path}
    except Exception as e:
        logger.error(f"保存角色卡到模型文件夹失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post('/character-card/save')
async def save_character_card(request: Request):
    """Save the character card to characters.json."""
    try:
        data = await request.json()
        chara_data = data.get('charaData')
        character_card_name = data.get('character_card_name')

        if not chara_data or not character_card_name:
            return JSONResponse({"success": False, "error": "缺少必要参数"}, status_code=400)

        # 获取config_manager实例
        _config_manager = get_config_manager()

        # 加载现有的characters.json
        characters = await _config_manager.aload_characters()

        # 确保'猫娘'键存在
        if '猫娘' not in characters:
            characters['猫娘'] = {}

        # 获取角色卡名称（档案名）
        # 兼容中英文字段名
        chara_name = chara_data.get('档案名') or chara_data.get('name') or character_card_name
        name_error = _validate_profile_name(chara_name)
        if name_error:
            return JSONResponse({"success": False, "error": f"角色名称无效: {name_error}"}, status_code=400)
        chara_name = str(chara_name).strip()
        is_new_character = chara_name not in characters['猫娘']
        previous_catgirl_data = copy.deepcopy(characters['猫娘'].get(chara_name, {}))
        filtered_chara_data = _filter_mutable_catgirl_fields(chara_data)

        # 创建猫娘数据，只保存非空字段
        catgirl_data = {}
        for k, v in filtered_chara_data.items():
            if k != '档案名' and k != 'name':
                if v:  # 只保存非空字段
                    catgirl_data[k] = v

        # 更新或创建猫娘数据
        characters['猫娘'][chara_name] = catgirl_data

        # 保存到characters.json
        await _config_manager.asave_characters(characters)
        prompt_fields_changed = (
            is_new_character
            or _catgirl_prompt_fields_changed(previous_catgirl_data, catgirl_data)
        )

        if is_new_character:
            pending_mark_ok, pending_mark_error = await _mark_new_character_greeting_pending_safe(_config_manager, chara_name, "character_card_save")
        else:
            pending_mark_ok = True
            pending_mark_error = ""

        if prompt_fields_changed:
            context_refresh_result = await _refresh_catgirl_context_after_profile_change(
                _config_manager,
                chara_name,
                characters,
                is_new=is_new_character,
            )
        else:
            init_one_catgirl = get_init_one_catgirl()
            await init_one_catgirl(chara_name, is_new=is_new_character)
            context_refresh_result = {
                "context_refreshed": False,
                "recent_history_cleared": False,
                "reload_notified": False,
                "session_restarted": False,
            }

        logger.info(f"角色卡已成功保存到characters.json: {chara_name}")
        result: dict = {
            "success": True,
            "character_card_name": chara_name,
            **context_refresh_result,
        }
        if not pending_mark_ok:
            result["partial_success"] = True
            result["pending_mark_ok"] = False
            result["pending_mark_failed"] = True
            result["pending_mark_error"] = pending_mark_error
        return result
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.error(f"保存角色卡到characters.json失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get('/catgirl/{name}/export')
async def export_catgirl_card(name: str):
    """Export a catgirl character card as a PNG image (with embedded archive data of the model and profile).

    Export flow:
    1. Fetch the catgirl's profile data
    2. If a non-default model is in use, pack the model files into the archive
    3. Append the archive data to the PNG image
    4. Return the PNG image for download

    Note: the default model (DEFAULT_LIVE2D_MODEL_NAME) is never included in the export.
    """
    import zipfile
    import tempfile
    from pathlib import Path
    from urllib.parse import quote

    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if name not in characters.get('猫娘', {}):
            return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

        catgirl_data = characters['猫娘'][name]

        # 创建临时目录
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            zip_path = temp_path / 'character_data.zip'

            # 创建压缩包（使用UTF-8编码支持中文文件名）
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                # 1. 添加角色设定JSON（包含所有字段，但省略指定字段）
                # 定义要省略的字段
                FIELDS_TO_EXCLUDE = {'cursor_follow', 'physics', 'voice_id'}

                def filter_excluded_fields(data):
                    """Recursively filter out the specified fields."""
                    if isinstance(data, dict):
                        return {
                            k: filter_excluded_fields(v)
                            for k, v in data.items()
                            if k not in FIELDS_TO_EXCLUDE
                        }
                    elif isinstance(data, list):
                        return [filter_excluded_fields(item) for item in data]
                    else:
                        return data

                chara_json = {
                    '档案名': name,
                    **filter_excluded_fields(catgirl_data)
                }
                zf.writestr('character.json', json.dumps(chara_json, ensure_ascii=False, indent=2))

                # 2. 检查并添加模型文件
                model_type = get_reserved(catgirl_data, 'avatar', 'model_type', default='live2d', legacy_keys=('model_type',))
                model_added = False

                if model_type == 'live2d':
                    # 获取Live2D模型路径
                    live2d_path = get_reserved(
                        catgirl_data,
                        'avatar',
                        'live2d',
                        'model_path',
                        default='',
                        legacy_keys=('live2d',)
                    )

                    if live2d_path and live2d_path.strip():
                        # 解析模型名称
                        live2d_name = live2d_path.replace('\\', '/').rstrip('/')
                        if live2d_name.endswith('.model3.json'):
                            live2d_name = live2d_name.split('/')[-2] if '/' in live2d_name else live2d_name.replace('.model3.json', '')
                        else:
                            live2d_name = live2d_name.split('/')[-1]

                        # 检查是否是默认模型
                        if live2d_name == DEFAULT_LIVE2D_MODEL_NAME:
                            logger.info(
                                f'猫娘 {name} 使用的是默认模型 '
                                f'{DEFAULT_LIVE2D_MODEL_NAME}，跳过模型打包'
                            )
                        else:
                            # 查找模型目录
                            model_dir, _ = find_model_directory(live2d_name)
                            if model_dir and os.path.exists(model_dir):
                                # 检查是否是用户导入的模型
                                if is_user_imported_model(model_dir, _config_manager):
                                    # 添加模型文件到压缩包
                                    model_files_added = 0
                                    for root, _dirs, files in os.walk(model_dir):
                                        for file in files:
                                            file_path = Path(root) / file
                                            arc_name = f"model/{live2d_name}/{file_path.relative_to(model_dir)}"
                                            zf.write(file_path, arc_name)
                                            model_files_added += 1
                                    logger.info(f'已添加模型 {live2d_name} 的 {model_files_added} 个文件到压缩包')
                                    model_added = True
                                else:
                                    logger.warning(f'模型 {live2d_name} 不是用户导入的模型，跳过打包')
                            else:
                                logger.warning(f'找不到模型目录: {live2d_name}')

                elif model_type in ('vrm', 'live3d'):
                    # 处理VRM/MMD模型
                    vrm_path = get_reserved(catgirl_data, 'avatar', 'vrm', 'model_path', default='')
                    mmd_path = get_reserved(catgirl_data, 'avatar', 'mmd', 'model_path', default='')

                    # 优先处理MMD模型（需要导出整个文件夹）
                    if mmd_path and mmd_path.strip():
                        # 解析MMD模型路径
                        mmd_path = mmd_path.replace('\\', '/')
                        if mmd_path.startswith('/user_mmd/'):
                            model_file_name = mmd_path.replace('/user_mmd/', '')
                            model_full_path = _config_manager.mmd_dir / model_file_name

                            if model_full_path and model_full_path.exists():
                                # 对于MMD模型，导出整个文件夹（包含贴图等依赖文件）
                                model_parent_dir = model_full_path.parent
                                model_folder_name = model_parent_dir.name

                                # 添加整个模型文件夹到压缩包
                                model_files_added = 0
                                for root, _dirs, files in os.walk(model_parent_dir):
                                    for file in files:
                                        file_path = Path(root) / file
                                        arc_name = f"model/{model_folder_name}/{file_path.relative_to(model_parent_dir)}"
                                        zf.write(file_path, arc_name)
                                        model_files_added += 1
                                logger.info(f'已添加MMD模型文件夹 {model_folder_name} 的 {model_files_added} 个文件到压缩包')
                                model_added = True
                            else:
                                logger.warning(f'找不到MMD模型文件: {mmd_path}')

                    # 处理VRM模型（单个文件）
                    elif vrm_path and vrm_path.strip():
                        vrm_path = vrm_path.replace('\\', '/')
                        if vrm_path.startswith('/user_vrm/'):
                            model_file_name = vrm_path.replace('/user_vrm/', '')
                            model_full_path = _config_manager.vrm_dir / model_file_name

                            if model_full_path and model_full_path.exists():
                                arc_name = f"model/{model_full_path.name}"
                                zf.write(model_full_path, arc_name)
                                logger.info(f'已添加VRM模型到压缩包: {model_full_path.name}')
                                model_added = True
                            else:
                                logger.warning(f'找不到VRM模型文件: {vrm_path}')

                elif model_type == 'pngtuber':
                    if _add_pngtuber_assets_to_character_zip(zf, catgirl_data, _config_manager):
                        model_added = True

                # 3. 读取卡面元数据 sidecar（作者 / 创建时间）
                _sidecar_meta_path = _config_manager.card_face_meta_path(name)
                _sidecar_existed = await asyncio.to_thread(_sidecar_meta_path.exists)
                _sidecar_meta = await asyncio.to_thread(_read_card_meta, _sidecar_meta_path)
                now_iso = datetime.now().isoformat(timespec='seconds')
                # 优先使用已有的创建时间；未设置时使用当前时间并回写 sidecar，
                # 以确保后续导出不会重复刷新。
                _author = str(_sidecar_meta.get('author') or '').strip()[:64]
                _existing_created_at = str(_sidecar_meta.get('created_at') or '').strip()
                _created_at = _existing_created_at or now_iso
                if not _existing_created_at:
                    try:
                        _config_manager.ensure_card_faces_directory()
                        _new_meta = dict(_sidecar_meta)
                        _new_meta['created_at'] = _created_at
                        if not _new_meta.get('updated_at'):
                            _new_meta['updated_at'] = now_iso
                        # 仅当 sidecar 原本不存在且 origin 为默认值（None/空/'self'）时才推断来源，
                        # 避免覆盖已有的 self 默认值或用户设定。
                        if not _sidecar_existed and _new_meta.get('origin') in (None, '', 'self'):
                            _new_meta['origin'] = _detect_card_origin_from_character(catgirl_data or {})
                        await asyncio.to_thread(_write_card_meta, _sidecar_meta_path, _new_meta)
                    except Exception as _meta_persist_err:
                        logger.warning(f"[导出角色卡] 回写创建时间到 sidecar 失败: {_meta_persist_err}")

                # 4. 添加元数据文件
                metadata = {
                    'version': '1.0',
                    'export_time': now_iso,
                    'character_name': name,
                    'author': _author,
                    'created_at': _created_at,
                    'model_included': model_added,
                    'model_type': model_type
                }
                zf.writestr('metadata.json', json.dumps(metadata, ensure_ascii=False, indent=2))

            # 5. 获取卡面图：优先使用保存的 card_faces/{name}.png，
            #    不存在时才回退到合成图。
            from utils.screenshot_utils import _validate_image_data
            saved_face_path = _config_manager.card_faces_dir / f"{name}.png"
            png_data = None
            if saved_face_path.exists():
                try:
                    png_data = await asyncio.to_thread(saved_face_path.read_bytes)
                    validated = await asyncio.to_thread(_validate_image_data, png_data)
                    if validated is None:
                        logger.warning(f"[导出角色卡] 已保存卡面验证失败，回退到合成图")
                        png_data = None
                    elif not png_data.startswith(b'\x89PNG\r\n\x1a\n'):
                        try:
                            from PIL import Image
                            from io import BytesIO
                            img = await asyncio.to_thread(Image.open, BytesIO(png_data))
                            buf = BytesIO()
                            await asyncio.to_thread(img.save, buf, format='PNG')
                            png_data = buf.getvalue()
                        except Exception as _conv_err:
                            logger.warning(f"[导出角色卡] 卡面非 PNG 且重新编码失败，回退到合成图: {_conv_err}")
                            png_data = None
                    if png_data is not None:
                        png_data = await asyncio.to_thread(_strip_legacy_card_face_header, png_data)
                except Exception as _read_err:
                    logger.warning(f"[导出角色卡] 读取已保存卡面失败，回退到合成图: {_read_err}")
                    png_data = None

            if png_data is None:
                # 回退：合成一张默认长方形角色卡图片
                from PIL import Image
                width, height = 600, 800
                img = Image.new('RGB', (width, height), color='#E8F4F8')
                png_path = temp_path / 'character_card.png'
                img.save(png_path, 'PNG')
                with open(png_path, 'rb') as f:
                    png_data = f.read()

            # 6. 将压缩包数据嵌入 PNG 的 neKo 块（合法 PNG chunk，Electron 可正常预览）
            with open(zip_path, 'rb') as f:
                zip_data = f.read()

            combined_data = _embed_zip_in_png_chunk(png_data, zip_data)

            # 7. 返回文件下载
            from urllib.parse import quote

            safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_', '·', '•') or '\u4e00' <= c <= '\u9fff').strip()
            if not safe_name:
                safe_name = "character_card"
            original_filename = f"{safe_name}.png"
            encoded_filename = quote(original_filename, safe='')
            content_disposition = f"attachment; filename*=UTF-8''{encoded_filename}"
            try:
                ascii_filename = original_filename.encode('ascii').decode('ascii')
            except UnicodeEncodeError:
                ascii_filename = "character_card.png"

            return Response(
                content=combined_data,
                # 用 octet-stream 避免浏览器将响应作为图片在新标签中预览，
                # 配合前端 <a download> 开启下载流程。
                media_type='application/octet-stream',
                headers={
                    'Content-Disposition': content_disposition,
                    'X-Filename': ascii_filename,
                    'Cache-Control': 'no-store',
                }
            )

    except Exception as e:
        logger.exception(f"导出角色卡失败: {e}")
        return JSONResponse({'success': False, 'error': f'导出失败: {str(e)}'}, status_code=500)


@router.get('/catgirl/{name}/export-settings')
async def export_catgirl_settings_only(name: str):
    """Export only the catgirl profile (obfuscated, without model files).

    Export flow:
    1. Fetch the catgirl's profile data
    2. Filter out the specified fields
    3. Apply simple XOR obfuscation
    4. Return the obfuscated JSON file directly
    """
    from urllib.parse import quote

    # XOR混淆密钥（仅用于防止意外编辑，非安全加密）
    XOR_KEY = b'NEKOCHARA2024'

    def xor_obfuscate(data: bytes, key: bytes) -> bytes:
        """Simple XOR data obfuscation/restoration (only to prevent accidental edits; not secure encryption).

        Note: this is not real encryption, just simple reversible obfuscation to
        keep users from accidentally editing the data. Use a proper encryption
        scheme if real security protection is needed.
        """
        return bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))

    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if name not in characters.get('猫娘', {}):
            return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

        catgirl_data = characters['猫娘'][name]

        # 定义要省略的字段（仅导出设定时不包含模型相关信息）
        FIELDS_TO_EXCLUDE = {'cursor_follow', 'physics', 'voice_id', '_reserved'}

        def filter_excluded_fields(data):
            """Recursively filter out the specified fields."""
            if isinstance(data, dict):
                return {
                    k: filter_excluded_fields(v)
                    for k, v in data.items()
                    if k not in FIELDS_TO_EXCLUDE
                }
            elif isinstance(data, list):
                return [filter_excluded_fields(item) for item in data]
            else:
                return data

        # 准备角色设定JSON（过滤字段，不包含模型信息）
        chara_json = {
            '档案名': name,
            **filter_excluded_fields(catgirl_data)
        }
        json_data = json.dumps(chara_json, ensure_ascii=False, indent=2).encode('utf-8')

        # 加密JSON数据
        encrypted_data = xor_obfuscate(json_data, XOR_KEY)

        # 构建文件名
        safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_', '·', '•') or '\u4e00' <= c <= '\u9fff').strip()
        if not safe_name:
            safe_name = "character_card"
        original_filename = f"{safe_name}_设定.nekocfg"
        encoded_filename = quote(original_filename, safe='')
        content_disposition = f"attachment; filename*=UTF-8''{encoded_filename}"

        try:
            ascii_filename = original_filename.encode('ascii').decode('ascii')
        except UnicodeEncodeError:
            ascii_filename = "character_settings.nekocfg"

        return Response(
            content=encrypted_data,
            media_type='application/octet-stream',
            headers={
                'Content-Disposition': content_disposition,
                'X-Filename': ascii_filename
            }
        )

    except Exception as e:
        logger.exception(f"导出设定失败: {e}")
        return JSONResponse({'success': False, 'error': f'导出失败: {str(e)}'}, status_code=500)


@router.post('/import-card')
async def import_character_card(
    zip_file: UploadFile = File(...),
    card_image: UploadFile = File(None),
):
    """Import a character card (a ZIP extracted from a PNG image).

    Optional parameters:
      - card_image: the original carrier PNG. If provided and no card face of the
        same name exists locally yet, it is stored directly as the character's
        card-face, following the legacy convention that the cover image is the card face.
    """
    import zipfile
    import tempfile
    import shutil
    from pathlib import Path

    # XOR混淆密钥（与导出时相同，用于防止意外编辑）
    XOR_KEY = b'NEKOCHARA2024'

    def xor_deobfuscate(data: bytes, key: bytes) -> bytes:
        """XOR data restoration (the same operation as xor_obfuscate; named for consistency).

        Note: this is not real decryption, just reversal of the simple reversible obfuscation.
        """
        return bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))

    temp_dir = None
    try:
        # 创建临时目录
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)
        zip_path = temp_path / 'imported.zip'

        # 保存上传的文件（使用流式读取并限制大小）
        try:
            file_buffer = await _read_limited_stream(zip_file, MAX_UPLOAD_SIZE)
            with open(zip_path, 'wb') as f:
                f.write(file_buffer.getvalue())
        except _UploadTooLargeError as e:
            logger.warning(f"[导入角色卡] 文件过大: {e}")
            return JSONResponse({'success': False, 'error': str(e)}, status_code=400)

        # 检查是否是加密的 .nekocfg 文件（直接是加密数据，不是ZIP）
        is_neko_file = zip_file.filename and zip_file.filename.endswith('.nekocfg')

        if is_neko_file:
            # 直接解密 .nekocfg 文件
            with open(zip_path, 'rb') as f:
                encrypted_data = f.read()
            try:
                decrypted_data = xor_deobfuscate(encrypted_data, XOR_KEY)
                character_data = json.loads(decrypted_data.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning(f"[导入角色卡] 解析 .nekocfg 文件失败: {e}")
                return JSONResponse({'success': False, 'error': f'角色卡解析失败: {str(e)}'}, status_code=400)
            if not isinstance(character_data, dict):
                return JSONResponse({'success': False, 'error': '角色卡数据格式无效'}, status_code=400)
            # .nekocfg is settings-only by design (export strips _reserved and
            # ships no model assets), so give the shared PNGTuber-restore tail
            # an empty source instead of the raw card: a hand-crafted file
            # carrying _reserved.avatar would otherwise restore image paths
            # that were never extracted locally.
            imported_card_character_data = {}
            character_data = _filter_mutable_catgirl_fields(character_data)
            character_name = str(character_data.get('档案名', '')).strip()
            character_data['档案名'] = character_name
            name_error = _validate_profile_name(character_name)
            if name_error:
                return JSONResponse({'success': False, 'error': f'角色名称无效: {name_error}'}, status_code=400)
            metadata = {'encrypted': True, 'model_included': False}
        else:
            # 解压ZIP文件（PNG角色卡格式）- 使用安全的解压方式防止 Zip Slip 攻击
            MAX_TOTAL_UNCOMPRESSED = 500 * 1024 * 1024  # 500 MB 总解压大小限制
            MAX_MEMBER_UNCOMPRESSED = 100 * 1024 * 1024  # 100 MB 单个文件大小限制
            extract_path = temp_path / 'extracted'
            extract_path.mkdir()

            total_uncompressed_size = 0
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for member in zf.namelist():
                    member_path = Path(member)
                    if member_path.is_absolute() or '..' in member_path.parts or '\\' in member:
                        logger.warning(f"[导入角色卡] 跳过不安全的路径: {member}")
                        continue

                    zip_info = zf.getinfo(member)
                    member_size = zip_info.file_size
                    if total_uncompressed_size + member_size > MAX_TOTAL_UNCOMPRESSED:
                        logger.warning(f"[导入角色卡] 跳过文件，大小超出总限制: {member}")
                        continue
                    if member_size > MAX_MEMBER_UNCOMPRESSED:
                        logger.warning(f"[导入角色卡] 跳过文件，单文件大小超限: {member}")
                        continue

                    dest_path = extract_path / member_path
                    try:
                        dest_path.resolve().relative_to(extract_path.resolve())
                    except ValueError:
                        logger.warning(f"[导入角色卡] 跳过路径验证失败: {member}")
                        continue
                    if member.endswith('/'):
                        dest_path.mkdir(parents=True, exist_ok=True)
                    else:
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        total_uncompressed_size += member_size
                        with zf.open(member) as src, open(dest_path, 'wb') as dst:
                            await asyncio.to_thread(shutil.copyfileobj, src, dst, length=8192)

            # 读取角色设定（支持加密和非加密格式）
            character_json_path = extract_path / 'character.json'
            character_json_encrypted_path = extract_path / 'character.json.encrypted'
            imported_card_character_data = {}

            if character_json_path.exists():
                # 非加密格式
                try:
                    character_data = await read_json_async(character_json_path)
                except json.JSONDecodeError as e:
                    logger.warning(f"[导入角色卡] 解析 character.json 失败: {e}")
                    return JSONResponse({'success': False, 'error': f'角色卡解析失败: {str(e)}'}, status_code=400)
                if not isinstance(character_data, dict):
                    return JSONResponse({'success': False, 'error': '角色卡数据格式无效'}, status_code=400)
                imported_card_character_data = copy.deepcopy(character_data)
                character_data = _filter_mutable_catgirl_fields(character_data)
                character_name = str(character_data.get('档案名', '')).strip()
                character_data['档案名'] = character_name
                name_error = _validate_profile_name(character_name)
                if name_error:
                    return JSONResponse({'success': False, 'error': f'角色名称无效: {name_error}'}, status_code=400)
            elif character_json_encrypted_path.exists():
                # 加密格式，需要解密
                try:
                    with open(character_json_encrypted_path, 'rb') as f:
                        encrypted_data = f.read()
                    decrypted_data = xor_deobfuscate(encrypted_data, XOR_KEY)
                    character_data = json.loads(decrypted_data.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    logger.warning(f"[导入角色卡] 解析加密 character.json 失败: {e}")
                    return JSONResponse({'success': False, 'error': f'角色卡解析失败: {str(e)}'}, status_code=400)
                if not isinstance(character_data, dict):
                    return JSONResponse({'success': False, 'error': '角色卡数据格式无效'}, status_code=400)
                imported_card_character_data = copy.deepcopy(character_data)
                character_data = _filter_mutable_catgirl_fields(character_data)
                character_name = str(character_data.get('档案名', '')).strip()
                character_data['档案名'] = character_name
                name_error = _validate_profile_name(character_name)
                if name_error:
                    return JSONResponse({'success': False, 'error': f'角色名称无效: {name_error}'}, status_code=400)
            else:
                return JSONResponse({'success': False, 'error': '角色卡文件损坏：缺少character.json'}, status_code=400)

            # 读取元数据
            metadata_path = extract_path / 'metadata.json'
            metadata = {}
            if metadata_path.exists():
                metadata = await read_json_async(metadata_path)

        character_name = character_data.get('档案名', '未命名角色')

        _config_manager = get_config_manager()

        async with _ugc_sync_lock:
            characters = await _config_manager.aload_characters()

            # 检查是否已存在同名角色，使用 Windows 风格的命名 (x)
            if character_name in characters.get('猫娘', {}):
                # 生成新名称
                base_name = character_name
                counter = 1
                while f"{base_name}({counter})" in characters.get('猫娘', {}):
                    counter += 1
                character_name = f"{base_name}({counter})"
                character_data['档案名'] = character_name

            # 处理模型文件（仅当不是 .nekocfg 文件时）
            imported_model_info = None  # 记录导入的模型信息，用于自动使用
            pngtuber_rel_map: dict[str, str] = {}

            def _find_model3_json(directory):
                """Recursively find the .model3.json file."""
                for item in directory.iterdir():
                    if item.is_file() and item.name.lower().endswith('.model3.json'):
                        return item
                    elif item.is_dir():
                        result = _find_model3_json(item)
                        if result:
                            return result
                return None

            if not is_neko_file:
                model_dir = extract_path / 'model'
                if model_dir.exists() and model_dir.is_dir():
                    model_type = metadata.get('model_type', 'live2d')
                    pngtuber_rel_map = await asyncio.to_thread(
                        _copy_imported_pngtuber_assets,
                        model_dir,
                        _config_manager,
                    )
                    if pngtuber_rel_map:
                        character_data = _rewrite_imported_pngtuber_refs(character_data, pngtuber_rel_map)

                    for model_item in model_dir.iterdir():
                        if model_item.name == _PNGTUBER_CARD_MODEL_DIR:
                            continue
                        if model_item.is_dir():
                            # 检查是 Live2D 还是 MMD 模型文件夹
                            # MMD 模型文件夹通常包含 .pmx, .pmd 文件
                            has_mmd_file = any(f.suffix.lower() in ('.pmx', '.pmd') for f in model_item.iterdir() if f.is_file())
                            # Live2D 模型文件夹通常包含 .model3.json 文件（递归搜索）
                            model3_file = _find_model3_json(model_item)
                            has_live2d_file = model3_file is not None

                            if has_mmd_file:
                                # MMD 模型（文件夹形式，包含贴图等依赖文件）
                                original_model_name = model_item.name

                                # 检查模型是否已存在，如果存在则使用 Windows 风格的命名 (x)
                                model_name = original_model_name
                                target_model_dir = _config_manager.mmd_dir / model_name
                                counter = 1

                                while target_model_dir.exists():
                                    model_name = f"{original_model_name}({counter})"
                                    target_model_dir = _config_manager.mmd_dir / model_name
                                    counter += 1

                                # 复制整个模型文件夹
                                await asyncio.to_thread(shutil.copytree, model_item, target_model_dir)
                                logger.info(f'已导入MMD模型文件夹: {original_model_name} -> {model_name}')

                                # 查找文件夹中的主模型文件（.pmx 或 .pmd）
                                main_model_file = None
                                for f in target_model_dir.iterdir():
                                    if f.is_file() and f.suffix.lower() in ('.pmx', '.pmd'):
                                        main_model_file = f
                                        break

                                if main_model_file:
                                    imported_model_info = {
                                        'type': 'mmd',
                                        'name': model_name,
                                        'original_name': original_model_name,
                                        'path': f'/user_mmd/{model_name}/{main_model_file.name}'
                                    }
                                else:
                                    logger.warning(f'MMD模型文件夹中没有找到主模型文件: {model_name}')

                            elif has_live2d_file:
                                # Live2D 模型（文件夹形式）
                                original_model_name = model_item.name

                                # 检查模型是否已存在，如果存在则使用 Windows 风格的命名 (x)
                                model_name = original_model_name
                                target_model_dir = _config_manager.live2d_dir / model_name
                                counter = 1

                                while target_model_dir.exists():
                                    model_name = f"{original_model_name}({counter})"
                                    target_model_dir = _config_manager.live2d_dir / model_name
                                    counter += 1

                                # 复制模型文件
                                await asyncio.to_thread(shutil.copytree, model_item, target_model_dir)
                                logger.info(f'已导入Live2D模型: {original_model_name} -> {model_name}')

                                # 查找复制后的 .model3.json 文件，保留相对路径
                                model3_file = _find_model3_json(target_model_dir)
                                if model3_file:
                                    model3_filename = str(model3_file.relative_to(target_model_dir))
                                else:
                                    model3_filename = f'{model_name}.model3.json'
                                logger.info(f'找到 Live2D 模型文件: {model3_filename}')

                                # 记录导入的模型信息
                                imported_model_info = {
                                    'type': 'live2d',
                                    'name': model_name,
                                    'original_name': original_model_name,
                                    'model3_filename': model3_filename
                                }

                        elif model_item.is_file():
                            # VRM 模型（文件形式）
                            model_file = model_item
                            original_model_name = model_file.stem  # 不含扩展名的文件名
                            model_ext = model_file.suffix.lower()

                            if model_ext == '.vrm':
                                # VRM 模型
                                # 检查模型是否已存在，如果存在则使用 Windows 风格的命名 (x)
                                model_name = original_model_name
                                target_model_path = _config_manager.vrm_dir / f"{model_name}{model_ext}"
                                counter = 1

                                while target_model_path.exists():
                                    model_name = f"{original_model_name}({counter})"
                                    target_model_path = _config_manager.vrm_dir / f"{model_name}{model_ext}"
                                    counter += 1

                                await asyncio.to_thread(shutil.copy2, model_file, target_model_path)
                                logger.info(f'已导入VRM模型: {original_model_name} -> {model_name}')

                                # 记录导入的模型信息
                                imported_model_info = {
                                    'type': 'vrm',
                                    'name': model_name,
                                    'original_name': original_model_name,
                                    'path': f'/user_vrm/{model_name}{model_ext}'
                                }
                else:
                    logger.warning(f"[导入角色卡] model 目录不存在或不是目录: {model_dir}")

                # 自动给猫娘使用导入的模型
                # 使用 _reserved 字段存储模型配置（这是系统内部使用的字段）
                if imported_model_info:
                    character_data['_reserved'] = character_data.get('_reserved', {})
                    character_data['_reserved']['avatar'] = character_data['_reserved'].get('avatar', {})

                    if imported_model_info['type'] == 'live2d':
                        model_name = imported_model_info['name']
                        model3_filename = imported_model_info.get('model3_filename', f'{model_name}.model3.json')
                        # 保留现有的 live2d 设置，只更新 model_path
                        character_data['_reserved']['avatar']['live2d'] = character_data['_reserved']['avatar'].get('live2d', {})
                        character_data['_reserved']['avatar']['live2d']['model_path'] = f'{model_name}/{model3_filename}'
                        character_data['_reserved']['avatar']['model_type'] = 'live2d'
                        logger.info(f'已自动为角色 {character_name} 设置Live2D模型: {model_name}, 文件: {model3_filename}')

                    elif imported_model_info['type'] == 'vrm':
                        character_data['_reserved']['avatar']['vrm'] = character_data['_reserved']['avatar'].get('vrm', {})
                        character_data['_reserved']['avatar']['vrm']['model_path'] = imported_model_info['path']
                        character_data['_reserved']['avatar']['model_type'] = 'live3d'
                        logger.info(f'已自动为角色 {character_name} 设置VRM模型: {imported_model_info["name"]}')

                    elif imported_model_info['type'] == 'mmd':
                        # 保留现有的 mmd 设置（捏脸、动画等），只更新 model_path
                        character_data['_reserved']['avatar']['mmd'] = character_data['_reserved']['avatar'].get('mmd', {})
                        character_data['_reserved']['avatar']['mmd']['model_path'] = imported_model_info['path']
                        character_data['_reserved']['avatar']['model_type'] = 'live3d'
                        logger.info(f'已自动为角色 {character_name} 设置MMD模型: {imported_model_info["name"]}')
                elif not pngtuber_rel_map:
                    logger.warning("[导入角色卡] 没有找到可导入的模型")

            character_data = _restore_imported_pngtuber_avatar_config(
                character_data,
                imported_card_character_data,
                pngtuber_rel_map,
            )

            # 添加角色到characters.json
            if '猫娘' not in characters:
                characters['猫娘'] = {}

            # 移除档案名键（因为已经用作字典键）
            chara_data_to_save = {k: v for k, v in character_data.items() if k != '档案名'}
            characters['猫娘'][character_name] = chara_data_to_save

            # 保存到文件
            await _config_manager.asave_characters(characters)
            pending_mark_ok, pending_mark_error = await _mark_new_character_greeting_pending_safe(_config_manager, character_name, "import")

            # 刷新内存中的角色数据，确保磁盘和内存同步
            initialize_character_data = get_initialize_character_data()
            if initialize_character_data:
                await initialize_character_data()

            # 写入卡面元数据 sidecar（origin=imported）
            try:
                _config_manager.ensure_card_faces_directory()
                meta_path = _config_manager.card_face_meta_path(character_name)
                imported_author = ''
                imported_created_at = ''
                if isinstance(metadata, dict):
                    imported_author = str(metadata.get('author', '') or '').strip()[:64]
                    imported_created_at = str(metadata.get('created_at', '') or '').strip()[:32]
                    if not imported_created_at:
                        imported_created_at = str(metadata.get('export_time', '') or '').strip()[:32]
                now_iso = datetime.now().isoformat(timespec='seconds')
                # 优先使用源卡中的创建时间，未提供时才赋为当前时间
                created_at = imported_created_at or now_iso
                meta = {
                    'author': imported_author,
                    'origin': 'imported',
                    'created_at': created_at,
                    'updated_at': now_iso,
                }
                await asyncio.to_thread(_write_card_meta, meta_path, meta)
            except Exception as meta_err:
                logger.warning(f"[导入角色卡] 写入卡面元数据失败: {meta_err}")
                partial_result = {
                    "success": True,
                    "partial_success": True,
                    "error": f"角色数据已导入，但卡面元数据写入失败: {meta_err}",
                    "card_meta_saved": False,
                    "character_name": character_name,
                    "pending_mark_ok": pending_mark_ok,
                }
                if not pending_mark_ok:
                    partial_result["pending_mark_failed"] = True
                    partial_result["pending_mark_error"] = pending_mark_error
                return JSONResponse(partial_result, status_code=200)

            # 老角色卡兼容：如果前端上传了载体 PNG，且本地还没有同名卡面，
            # 则直接使用该 PNG 作为卡面（带 neKo chunk 不影响质量）。
            try:
                if card_image is not None and card_image.filename:
                    face_path = _config_manager.card_faces_dir / f"{character_name}.png"
                    if not face_path.exists():
                        try:
                            # 先用更大的上传限制读取载体 PNG（可能嵌入 ZIP）
                            face_buffer = await _read_limited_stream(card_image, MAX_UPLOAD_SIZE)
                            face_bytes = face_buffer.getvalue()
                        except _UploadTooLargeError as e:
                            logger.warning(f"[导入角色卡] 载体 PNG 超过上传限制，跳过保存: {e}")
                            face_bytes = b''
                        if face_bytes:
                            try:
                                from utils.screenshot_utils import _validate_image_data

                                validated = await asyncio.to_thread(_validate_image_data, face_bytes)
                                if validated is None:
                                    logger.warning(f"[导入角色卡] 载体 PNG 验证失败，跳过保存")
                                else:
                                    if validated.mode not in ('RGB', 'RGBA', 'L'):
                                        validated = validated.convert('RGB')
                                    out = io.BytesIO()
                                    validated.save(out, format='PNG')
                                    valid_png = out.getvalue()
                                    if len(valid_png) > MAX_CARD_FACE_SIZE:
                                        logger.warning(f"[导入角色卡] 重编码后的卡面图 ({len(valid_png)} bytes) 超过最大限制 ({MAX_CARD_FACE_SIZE} bytes)，跳过保存")
                                    else:
                                        await asyncio.to_thread(face_path.write_bytes, valid_png)
                                        logger.info(f"[导入角色卡] 已将载体 PNG 存为卡面: {face_path}")
                            except Exception as pil_err:
                                logger.warning(f"[导入角色卡] 卡面图 PNG 处理失败，跳过保存: {pil_err}")
            except Exception as face_err:
                logger.warning(f"[导入角色卡] 保存载体 PNG 为卡面失败: {face_err}")

        import_result: dict = {
            'success': True,
            'character_name': character_name,
            'message': f'角色卡 "{character_name}" 导入成功',
        }
        if not pending_mark_ok:
            import_result['partial_success'] = True
            import_result['pending_mark_ok'] = False
            import_result['pending_mark_failed'] = True
            import_result['pending_mark_error'] = pending_mark_error
        return JSONResponse(import_result)

    except zipfile.BadZipFile:
        logger.error("导入角色卡失败：无效的ZIP文件")
        return JSONResponse({'success': False, 'error': '无效的角色卡文件格式'}, status_code=400)
    except Exception as e:
        logger.exception(f"导入角色卡失败: {e}")
        return JSONResponse({'success': False, 'error': f'导入失败: {str(e)}'}, status_code=500)
    finally:
        # 清理临时目录
        if temp_dir and os.path.exists(temp_dir):
            await asyncio.to_thread(shutil.rmtree, temp_dir, ignore_errors=True)


# ====== 角色卡卡面（Card Face）存储 ======

# 卡面元数据 sidecar 默认结构
def _default_card_meta(origin: str = 'self') -> dict:
    """Return the default card-face metadata."""
    return {
        'author': '',
        'origin': origin,  # self / imported / steam
        'created_at': None,
        'updated_at': None,
    }


def _read_card_meta(meta_path) -> dict:
    """Read the sidecar JSON; returns defaults when the file is missing or corrupted."""
    try:
        if meta_path.exists():
            with open(meta_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning(f"卡面元数据内容无效（非字典）{meta_path}: {type(data).__name__}")
                return _default_card_meta(origin=None)
            # 合并默认字段，保证字段完整
            merged = _default_card_meta()
            merged.update({k: v for k, v in data.items() if k in merged})
            return merged
    except Exception as e:
        logger.warning(f"读取卡面元数据失败 {meta_path}: {e}")
        return _default_card_meta(origin=None)
    return _default_card_meta()


def _write_card_meta(meta_path, meta: dict) -> None:
    """Write the sidecar JSON (atomic write). The caller must run ensure_card_faces_directory() first."""
    from utils.file_utils import atomic_write_json
    atomic_write_json(meta_path, meta, ensure_ascii=False, indent=2)


def _detect_card_origin_from_character(catgirl_data: dict) -> str:
    """Infer origin from the catgirl config (fallback when no sidecar exists).
    Based on the card's own source (character_origin.source), not the model source (avatar.asset_source),
    so swapping models never changes the card's origin label."""
    try:
        char_source = get_reserved(catgirl_data, 'character_origin', 'source', default='')
        if char_source == 'steam_workshop':
            return 'steam'
    except Exception:
        pass
    return 'self'


@router.get('/card-faces')
async def list_card_faces():
    """Return the names of all catgirls with a custom card face set (lets the frontend avoid pointless 404 requests)."""
    _config_manager = get_config_manager()
    faces_dir = _config_manager.card_faces_dir
    names: list[str] = []
    orphans: list[str] = []
    try:
        characters = await _config_manager.aload_characters()
        valid_names = set(characters.get('猫娘', {}).keys())
        if faces_dir.exists():
            for p in await asyncio.to_thread(lambda: list(faces_dir.glob('*.png'))):
                stem = p.stem
                if stem in valid_names:
                    names.append(stem)
                else:
                    orphans.append(stem)
        if orphans:
            logger.info(f"[list_card_faces] 孤儿卡面文件（无对应角色）: {orphans}")
    except Exception:
        logger.exception("list_card_faces failed")
        return JSONResponse({'success': False, 'error': '读取卡面列表失败'}, status_code=500)

    return JSONResponse({'success': True, 'names': names}, status_code=200)


@router.get('/card-metas')
async def list_card_metas():
    """Return card-face metadata for all catgirls in bulk.

    For legacy character cards without a sidecar JSON, the origin is inferred
    from the catgirl config and defaults are returned, so the frontend still
    shows card-face info after upgrading from older versions.
    """
    _config_manager = get_config_manager()
    faces_dir = _config_manager.card_faces_dir
    metas: dict = {}
    try:
        # 先加载角色列表，构建有效名称集合
        characters = await _config_manager.aload_characters()
        valid_names = set((characters.get('猫娘', {}) or {}).keys())
        # 只读取属于有效角色的 sidecar
        if faces_dir.exists():
            json_files = await asyncio.to_thread(lambda: list(faces_dir.glob('*.json')))
            for p in json_files:
                if p.stem in valid_names:
                    meta = await asyncio.to_thread(_read_card_meta, p)
                    if meta.get('origin') is None:
                        # sidecar 损坏：当作缺失处理，重新推断 origin
                        meta = _default_card_meta(_detect_card_origin_from_character(characters['猫娘'][p.stem]))
                    metas[p.stem] = meta
        # 补齐缺失 sidecar 的猫娘：按配置推断 origin，返回默认值
        for cname, cdata in (characters.get('猫娘', {}) or {}).items():
            if cname in metas:
                continue
            inferred = _default_card_meta(_detect_card_origin_from_character(cdata or {}))
            metas[cname] = inferred
    except Exception as e:
        logger.warning(f"批量读取卡面元数据失败: {e}")
        return JSONResponse({'success': False, 'error': '批量读取卡面元数据失败', 'details': str(e)}, status_code=500)

    return JSONResponse({'success': True, 'metas': metas})


@router.get('/catgirl/{name}/card-meta')
async def get_card_meta(name: str):
    """Get a single catgirl's card-face metadata. Without a sidecar, infers origin from the catgirl config and returns defaults."""
    _config_manager = get_config_manager()
    name_error = _validate_existing_character_path_name(name)
    if name_error:
        return JSONResponse({'success': False, 'error': f'无效的角色名: {name_error}'}, status_code=400)

    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

    meta_path = _config_manager.card_face_meta_path(name)
    meta = await asyncio.to_thread(_read_card_meta, meta_path)
    if not meta_path.exists() or meta.get('origin') is None:
        # 无 sidecar 或读取失败：根据猫娘配置推断 origin
        meta['origin'] = _detect_card_origin_from_character(characters['猫娘'][name])
    return JSONResponse({'success': True, 'meta': meta})


@router.put('/catgirl/{name}/card-meta')
async def put_card_meta(name: str, request: Request):
    """Update card-face metadata (currently only the author field, and only when origin=self)."""
    _config_manager = get_config_manager()
    name_error = _validate_existing_character_path_name(name)
    if name_error:
        return JSONResponse({'success': False, 'error': f'无效的角色名: {name_error}'}, status_code=400)

    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({'success': False, 'error': '请求体必须是合法的JSON格式'}, status_code=400)

    new_author = data.get('author') if isinstance(data, dict) else None
    if new_author is None:
        return JSONResponse({'success': False, 'error': '缺少 author 字段'}, status_code=400)
    new_author = str(new_author).strip()
    if len(new_author) > 64:
        return JSONResponse({'success': False, 'error': '作者名称过长（最长64字符）'}, status_code=400)

    meta_path = _config_manager.card_face_meta_path(name)
    existing = await asyncio.to_thread(_read_card_meta, meta_path)
    if not meta_path.exists() or existing.get('origin') is None:
        existing['origin'] = _detect_card_origin_from_character(characters['猫娘'][name])

    if existing.get('origin') != 'self':
        return JSONResponse({'success': False, 'error': '仅本地创作的卡面可修改作者'}, status_code=403)

    existing['author'] = new_author
    now_iso = datetime.now().isoformat(timespec='seconds')
    existing['updated_at'] = now_iso
    if not existing.get('created_at'):
        existing['created_at'] = now_iso

    _config_manager.ensure_card_faces_directory()
    await asyncio.to_thread(_write_card_meta, meta_path, existing)
    return JSONResponse({'success': True, 'meta': existing})


def _strip_legacy_card_face_header(image_data: bytes) -> bytes:
    """Return old saved card faces without the obsolete blue name header."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_data)) as img:
            img.load()
            width, height = img.size
            header_height = height // 6
            if width <= 0 or header_height <= 0:
                return image_data

            rgb = img.convert('RGB')
            header_region = rgb.crop((0, 0, width, header_height))
            top_mean = header_region.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
            header_color = (64, 197, 241)
            if max(abs(top_mean[i] - header_color[i]) for i in range(3)) > 24:
                return image_data

            # Avoid mistaking an ordinary blue illustration/background for the
            # legacy solid-color name header.
            sample = header_region.resize((16, 16), Image.Resampling.BOX)
            pixels = list(sample.getdata())
            channel_spread = max(
                max(px[i] for px in pixels) - min(px[i] for px in pixels)
                for i in range(3)
            )
            if channel_spread > 28:
                return image_data

            # The old header usually has a visible color break at the body.
            # If the next band is effectively the same blue, keep the image.
            body_band = rgb.crop((0, header_height, width, min(height, header_height * 2)))
            if body_band.size[1] > 0:
                body_mean = body_band.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
                if max(abs(body_mean[i] - header_color[i]) for i in range(3)) < 10:
                    return image_data

            cropped = img.convert('RGBA').crop((0, header_height, width, height))
            normalized = cropped.resize((width, height), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            normalized.save(out, 'PNG')
            return out.getvalue()
    except Exception as exc:
        logger.warning("legacy card face normalization failed: %s", exc)
        return image_data


@router.get('/catgirl/{name}/card-face')
async def get_card_face(name: str):
    """Get the character's custom card-face image."""
    _config_manager = get_config_manager()
    name_error = _validate_existing_character_path_name(name)
    if name_error:
        return JSONResponse({'success': False, 'error': f'无效的角色名: {name_error}'}, status_code=400)

    face_path = _config_manager.card_faces_dir / f"{name}.png"
    if not face_path.exists():
        return JSONResponse({'success': False, 'error': '卡面不存在'}, status_code=404)

    image_data = await asyncio.to_thread(face_path.read_bytes)
    image_data = await asyncio.to_thread(_strip_legacy_card_face_header, image_data)
    return Response(content=image_data, media_type='image/png', headers={'Cache-Control': 'no-store'})


@router.put('/catgirl/{name}/card-face')
async def put_card_face(name: str, image: UploadFile = File(...)):
    """Save the character's custom card-face image."""
    _config_manager = get_config_manager()
    name_error = _validate_existing_character_path_name(name)
    if name_error:
        return JSONResponse({'success': False, 'error': f'无效的角色名: {name_error}'}, status_code=400)

    characters = await _config_manager.aload_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

    # 验证文件类型
    content_type = image.content_type or ''
    if not content_type.startswith('image/'):
        return JSONResponse({'success': False, 'error': '文件类型无效，请上传图片'}, status_code=400)

    # 流式读取并限制大小
    try:
        image_buffer = await _read_limited_stream(image, MAX_CARD_FACE_SIZE)
    except _UploadTooLargeError:
        return JSONResponse({'success': False, 'error': '图片文件过大（最大 10MB）'}, status_code=400)

    # 在线程中验证并重新编码为 PNG
    try:
        from utils.screenshot_utils import _validate_image_data

        image_buffer.seek(0)
        validated_img = await asyncio.to_thread(_validate_image_data, image_buffer.getvalue())
        if validated_img is None:
            return JSONResponse({'success': False, 'error': '无效的图片文件'}, status_code=400)

        def _reencode(img) -> bytes:
            if img.mode not in ('RGB', 'RGBA', 'L'):
                img = img.convert('RGB')
            out = io.BytesIO()
            img.save(out, format='PNG')
            return out.getvalue()

        png_bytes = await asyncio.to_thread(_reencode, validated_img)
    except Exception:
        return JSONResponse({'success': False, 'error': '无效的图片文件'}, status_code=400)

    # 重编码后再次校验大小（压缩后仍可能超过限制）
    if len(png_bytes) > MAX_CARD_FACE_SIZE:
        return JSONResponse({'success': False, 'error': '文件过大（重编码后超过10MB）'}, status_code=413)

    # 确保目录存在
    _config_manager.ensure_card_faces_directory()

    face_path = _config_manager.card_faces_dir / f"{name}.png"
    await asyncio.to_thread(face_path.write_bytes, png_bytes)

    # 同步更新 sidecar 元数据
    meta_path = _config_manager.card_face_meta_path(name)
    try:
        meta = await asyncio.to_thread(_read_card_meta, meta_path)
        now_iso = datetime.now().isoformat(timespec='seconds')
        # 上传即视为本地创作；若此前是导入的，刷新创建时间
        previous_origin = meta.get('origin')
        meta['origin'] = 'self'
        if previous_origin != 'self' or not meta.get('created_at'):
            meta['created_at'] = now_iso
        meta['updated_at'] = now_iso
        await asyncio.to_thread(_write_card_meta, meta_path, meta)
    except Exception as meta_err:
        logger.warning(f"[上传卡面] 写入 sidecar 元数据失败: {meta_err}")
        return JSONResponse({
            'success': True,
            'partial_success': True,
            'error': f"卡面已保存，但元数据写入失败: {meta_err}",
        }, status_code=200)

    return JSONResponse({'success': True})


class _InvalidPortraitError(ValueError):
    """Raised by the character-card render helper when the user-supplied
    portrait fails PIL's verify() check. Caught at the endpoint to map
    to a 400 response (vs. 500 for genuine render errors)."""


@router.post('/catgirl/{name}/export-with-portrait')
async def export_catgirl_with_portrait(
    name: str,
    portrait: UploadFile = File(...),
    include_model: bool = Form(True)
):
    """Export a character card (including the portrait image).

    Export flow:
    1. Receive the portrait image from the frontend
    2. Composite the portrait onto the character card template
    3. Pack the character profile and model files (optional)
    4. Return the composited PNG character card
    """
    import zipfile
    import tempfile
    from pathlib import Path
    from urllib.parse import quote
    from PIL import Image

    temp_dir = None
    try:
        _config_manager = get_config_manager()
        characters = await _config_manager.aload_characters()

        if name not in characters.get('猫娘', {}):
            return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)

        catgirl_data = characters['猫娘'][name]

        # 创建临时目录
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir)
        zip_path = temp_path / 'character_data.zip'

        # 1. 创建ZIP压缩包（包含角色设定和模型）
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # 准备角色设定JSON
            export_data = {'档案名': name, **catgirl_data}

            # 过滤掉运行时字段
            def _filter_export_fields(data, keep_model_paths=False):
                """Filter fields on export."""
                result = {}
                for key, value in data.items():
                    if key in ('cursor_follow', 'physics', 'voice_id'):
                        continue
                    if key == '_reserved' and isinstance(value, dict):
                        reserved_copy = copy.deepcopy(value)
                        avatar = reserved_copy.get('avatar', {})
                        if not keep_model_paths:
                            for model_type in ('live2d', 'vrm', 'mmd', 'live3d'):
                                if model_type in avatar and isinstance(avatar[model_type], dict):
                                    avatar[model_type].pop('model_path', None)
                        result[key] = _filter_export_fields(reserved_copy, keep_model_paths)
                    elif isinstance(value, dict):
                        result[key] = _filter_export_fields(value, keep_model_paths)
                    elif isinstance(value, list):
                        result[key] = [
                            _filter_export_fields(item, keep_model_paths) if isinstance(item, dict) else item
                            for item in value
                        ]
                    else:
                        result[key] = value
                return result

            chara_json = _filter_export_fields(export_data, keep_model_paths=include_model)
            zf.writestr('character.json', json.dumps(chara_json, ensure_ascii=False, indent=2))

            # 如果需要包含模型，添加模型文件
            model_added = False
            model_type = get_reserved(catgirl_data, 'avatar', 'model_type', default='live2d')

            if include_model:
                if model_type == 'live2d':
                    live2d_path = get_reserved(catgirl_data, 'avatar', 'live2d', 'model_path', default='')
                    if live2d_path and live2d_path.strip():
                        live2d_name = live2d_path.split('/')[0] if '/' in live2d_path else live2d_path.replace('.model3.json', '')
                        if live2d_name and live2d_name != DEFAULT_LIVE2D_MODEL_NAME:
                            model_dir, _ = find_model_directory(live2d_name)
                            if model_dir and os.path.exists(model_dir):
                                if is_user_imported_model(model_dir, _config_manager):
                                    model_files_added = 0
                                    for root, _dirs, files in os.walk(model_dir):
                                        for file in files:
                                            file_path = Path(root) / file
                                            arc_name = f"model/{live2d_name}/{file_path.relative_to(model_dir)}"
                                            zf.write(file_path, arc_name)
                                            model_files_added += 1
                                    logger.info(f'已添加模型 {live2d_name} 的 {model_files_added} 个文件到压缩包')
                                    model_added = True

                elif model_type in ('vrm', 'live3d'):
                    vrm_path = get_reserved(catgirl_data, 'avatar', 'vrm', 'model_path', default='')
                    mmd_path = get_reserved(catgirl_data, 'avatar', 'mmd', 'model_path', default='')

                    if mmd_path and mmd_path.strip():
                        mmd_path = mmd_path.replace('\\', '/')
                        if mmd_path.startswith('/user_mmd/'):
                            model_file_name = mmd_path.replace('/user_mmd/', '')
                            model_full_path = _config_manager.mmd_dir / model_file_name
                            if model_full_path and model_full_path.exists():
                                model_parent_dir = model_full_path.parent
                                model_folder_name = model_parent_dir.name
                                model_files_added = 0
                                for root, _dirs, files in os.walk(model_parent_dir):
                                    for file in files:
                                        file_path = Path(root) / file
                                        arc_name = f"model/{model_folder_name}/{file_path.relative_to(model_parent_dir)}"
                                        zf.write(file_path, arc_name)
                                        model_files_added += 1
                                logger.info(f'已添加MMD模型文件夹 {model_folder_name} 的 {model_files_added} 个文件到压缩包')
                                model_added = True

                    elif vrm_path and vrm_path.strip():
                        vrm_path = vrm_path.replace('\\', '/')
                        if vrm_path.startswith('/user_vrm/'):
                            model_file_name = vrm_path.replace('/user_vrm/', '')
                            model_full_path = _config_manager.vrm_dir / model_file_name
                            if model_full_path and model_full_path.exists():
                                arc_name = f"model/{model_full_path.name}"
                                zf.write(model_full_path, arc_name)
                                logger.info(f'已添加VRM模型到压缩包: {model_full_path.name}')
                                model_added = True

                elif model_type == 'pngtuber':
                    if _add_pngtuber_assets_to_character_zip(zf, catgirl_data, _config_manager):
                        model_added = True

            # 添加元数据文件
            metadata = {
                'version': '1.0',
                'export_time': datetime.now().isoformat(),
                'character_name': name,
                'model_included': model_added,
                'model_type': model_type,
                'has_portrait': True
            }
            zf.writestr('metadata.json', json.dumps(metadata, ensure_ascii=False, indent=2))

        # 2. 读取立绘图片（带大小限制和验证）
        MAX_PORTRAIT_SIZE = 50 * 1024 * 1024  # 50 MB
        portrait_data = await portrait.read(MAX_PORTRAIT_SIZE + 1)
        if len(portrait_data) > MAX_PORTRAIT_SIZE:
            return JSONResponse({'success': False, 'error': f'图片大小超过限制 ({MAX_PORTRAIT_SIZE // (1024 * 1024)} MB)'}, status_code=400)

        logger.info(f"[导出角色卡] 接收到立绘图片，大小: {len(portrait_data)} bytes")

        png_path = temp_path / 'character_card.png'

        # 整段 PIL 渲染链（图片校验 + 卡片合成 + 字体扫描 + PNG 编码）放进 worker
        # 线程，避免阻塞事件循环。校验失败用专属异常，让外层回 400 而不是 500。
        def _render_card_png(_portrait_data: bytes, _name: str, _png_path) -> None:
            try:
                Image.MAX_IMAGE_PIXELS = 100_000_000  # 限制最大像素数防止解压炸弹
                portrait_img = Image.open(io.BytesIO(_portrait_data))
                portrait_img.verify()
                portrait_img = Image.open(io.BytesIO(_portrait_data))
                portrait_img.load()  # 强制解码：把截断/损坏的像素错误提前到这里，与 _InvalidPortraitError 一起回 400 而不是后续 resize/save 时回 500
            except Exception as exc:
                raise _InvalidPortraitError(str(exc)) from exc

            logger.info(f"[导出角色卡] 立绘图片尺寸: {portrait_img.size}, 模式: {portrait_img.mode}")

            if portrait_img.mode != 'RGBA':
                portrait_img = portrait_img.convert('RGBA')

            width, height = 600, 800
            card_img = Image.new('RGBA', (width, height), color='#E8F4F8')

            portrait_area_y = 0
            portrait_area_width = width
            portrait_area_height = height

            # 前端已按完整卡面尺寸渲染立绘，直接缩放到目标尺寸后粘贴
            portrait_resized = portrait_img.resize((portrait_area_width, portrait_area_height), Image.Resampling.LANCZOS)
            logger.info(f"[导出角色卡] 立绘调整后尺寸: {portrait_resized.size}, 粘贴位置: (0, {portrait_area_y})")

            # 粘贴立绘（使用alpha通道）
            card_img.paste(portrait_resized, (0, portrait_area_y), portrait_resized)
            logger.info("[导出角色卡] 立绘粘贴完成")

            final_img = Image.new('RGB', (width, height), color='#E8F4F8')
            final_img.paste(card_img, (0, 0), card_img)

            final_img.save(_png_path, 'PNG')

        try:
            await asyncio.to_thread(_render_card_png, portrait_data, name, png_path)
        except _InvalidPortraitError as e:
            logger.warning(f"[导出角色卡] 图片验证失败: {e}")
            return JSONResponse({'success': False, 'error': f'无效的图片文件: {str(e)}'}, status_code=400)

        # 6. 将压缩包数据嵌入 PNG 的 neKo 块（合法 PNG chunk，Electron 可正常预览）
        with open(png_path, 'rb') as f:
            png_data = f.read()

        with open(zip_path, 'rb') as f:
            zip_data = f.read()

        combined_data = _embed_zip_in_png_chunk(png_data, zip_data)

        # 7. 返回图片文件
        safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_', '·', '•') or '\u4e00' <= c <= '\u9fff').strip()
        if not safe_name:
            safe_name = "character_card"
        original_filename = f"{safe_name}.png"
        encoded_filename = quote(original_filename, safe='')
        content_disposition = f"attachment; filename*=UTF-8''{encoded_filename}"

        try:
            ascii_filename = original_filename.encode('ascii').decode('ascii')
        except UnicodeEncodeError:
            ascii_filename = "character_card.png"

        return Response(
            content=combined_data,
            media_type='image/png',
            headers={
                'Content-Disposition': content_disposition,
                'X-Filename': ascii_filename
            }
        )

    except Exception as e:
        logger.exception(f"导出带立绘的角色卡失败: {e}")
        return JSONResponse({'success': False, 'error': f'导出失败: {str(e)}'}, status_code=500)
    finally:
        if temp_dir and os.path.exists(temp_dir):
            await asyncio.to_thread(shutil.rmtree, temp_dir, ignore_errors=True)
