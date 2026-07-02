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

"""Reference-audio upload/removal and voice-reference endpoints.

Split out of the former monolithic ``main_routers/workshop_router.py``.
"""

from ._shared import logger, router
from .config_files import _assert_under_base
from .ugc import _find_subscribed_item_by_id
from .voice_manifest import (
    WORKSHOP_REFERENCE_AUDIO_CONTENT_TYPES,
    WORKSHOP_REFERENCE_AUDIO_EXTENSIONS,
    WORKSHOP_REFERENCE_LANGUAGES,
    WORKSHOP_REFERENCE_PROVIDER_HINTS,
    WORKSHOP_VOICE_MANIFEST_NAME,
    _cleanup_workshop_voice_reference,
    _normalize_workshop_voice_manifest,
    _resolve_workshop_voice_reference,
    _sanitize_voice_prefix,
)

import os
import asyncio
import mimetypes
from urllib.parse import unquote
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from utils.file_utils import atomic_write_json
from utils.workshop_utils import (
    get_workshop_path,
)


@router.post('/upload-reference-audio')
async def upload_reference_audio(request: Request):
    """Upload reference audio and generate voice_manifest.json in the content directory."""
    try:
        form = await request.form()
        file = form.get('file')
        content_folder = unquote(str(form.get('content_folder', '') or '').strip())
        workshop_export_dir = os.path.join(get_workshop_path(), 'WorkshopExport')

        if not file:
            return JSONResponse({
                "success": False,
                "error": "没有选择参考语音",
            }, status_code=400)

        if not content_folder:
            return JSONResponse({
                "success": False,
                "error": "缺少内容目录",
            }, status_code=400)

        try:
            content_folder = _assert_under_base(content_folder, workshop_export_dir)
        except PermissionError:
            return JSONResponse({
                "success": False,
                "error": "参考语音只能上传到工坊临时目录",
            }, status_code=403)

        if not os.path.exists(content_folder) or not os.path.isdir(content_folder):
            return JSONResponse({
                "success": False,
                "error": "内容目录不存在",
            }, status_code=404)

        file_name = getattr(file, 'filename', '') or ''
        file_ext = os.path.splitext(file_name)[1].lower()
        if file_ext not in WORKSHOP_REFERENCE_AUDIO_EXTENSIONS:
            file_ext = WORKSHOP_REFERENCE_AUDIO_CONTENT_TYPES.get(getattr(file, 'content_type', ''), '')

        if file_ext not in WORKSHOP_REFERENCE_AUDIO_EXTENSIONS:
            return JSONResponse({
                "success": False,
                "error": "参考语音格式只支持 mp3 或 wav",
            }, status_code=400)

        prefix = _sanitize_voice_prefix(
            form.get('prefix', ''),
            default_prefix=os.path.basename(content_folder),
        )
        display_name = str(form.get('display_name', '') or '').strip() or prefix
        ref_language = str(form.get('ref_language', 'ch') or 'ch').strip().lower()
        if ref_language not in WORKSHOP_REFERENCE_LANGUAGES:
            ref_language = 'ch'

        provider_hint = str(form.get('provider_hint', 'cosyvoice') or 'cosyvoice').strip().lower()
        if provider_hint not in WORKSHOP_REFERENCE_PROVIDER_HINTS:
            provider_hint = 'cosyvoice'

        _cleanup_workshop_voice_reference(content_folder)

        reference_audio_name = f'voice_sample{file_ext}'
        reference_audio_path = os.path.join(content_folder, reference_audio_name)
        with open(reference_audio_path, 'wb') as f:
            f.write(await file.read())

        manifest = _normalize_workshop_voice_manifest({
            'version': 1,
            'reference_audio': reference_audio_name,
            'prefix': prefix,
            'ref_language': ref_language,
            'display_name': display_name,
            'provider_hint': provider_hint,
        }, default_prefix=prefix, default_display_name=display_name)
        atomic_write_json(
            os.path.join(content_folder, WORKSHOP_VOICE_MANIFEST_NAME),
            manifest,
            ensure_ascii=False,
            indent=2,
        )

        return JSONResponse({
            "success": True,
            "manifest": manifest,
            "message": "参考语音已写入工坊内容目录",
        })
    except Exception as e:
        logger.error(f"上传参考语音失败: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


@router.post('/remove-reference-audio')
async def remove_reference_audio(request: Request):
    """Delete the reference audio and voice_manifest.json from the content directory."""
    try:
        data = await request.json()
        content_folder = unquote(str(data.get('content_folder', '') or '').strip())
        workshop_export_dir = os.path.join(get_workshop_path(), 'WorkshopExport')
        if not content_folder:
            return JSONResponse({
                "success": False,
                "error": "缺少内容目录",
            }, status_code=400)

        try:
            content_folder = _assert_under_base(content_folder, workshop_export_dir)
        except PermissionError:
            return JSONResponse({
                "success": False,
                "error": "内容目录不在允许范围内",
            }, status_code=403)

        if os.path.exists(content_folder) and os.path.isdir(content_folder):
            _cleanup_workshop_voice_reference(content_folder)

        return JSONResponse({
            "success": True,
            "message": "参考语音已清理",
        })
    except Exception as e:
        logger.error(f"删除参考语音失败: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


@router.get('/voice-reference/{item_id}')
async def get_workshop_voice_reference(item_id: str):
    """Return the reference-voice manifest inside a subscribed workshop item, by publishedFileId."""
    try:
        item = await _find_subscribed_item_by_id(item_id)
    except RuntimeError as e:
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=503)

    if not item:
        return JSONResponse({
            "success": False,
            "available": False,
            "error": "未找到对应的订阅工坊物品",
        }, status_code=404)

    install_folder = item.get('installedFolder')
    if not install_folder or not os.path.exists(install_folder):
        return JSONResponse({
            "success": False,
            "available": False,
            "error": "工坊物品尚未安装",
        }, status_code=404)

    try:
        voice_ref = await asyncio.to_thread(_resolve_workshop_voice_reference, install_folder)
    except FileNotFoundError as e:
        return JSONResponse({
            "success": False,
            "available": False,
            "error": str(e),
        }, status_code=404)
    except ValueError as e:
        return JSONResponse({
            "success": False,
            "available": False,
            "error": str(e),
        }, status_code=400)

    if not voice_ref:
        return JSONResponse({
            "success": True,
            "available": False,
            "item_id": str(item_id),
            "title": item.get('title') or '',
        })

    return JSONResponse({
        "success": True,
        "available": True,
        "item_id": str(item_id),
        "title": item.get('title') or '',
        "manifest": voice_ref['manifest'],
    })


@router.get('/voice-reference/{item_id}/audio')
async def get_workshop_voice_reference_audio(item_id: str):
    """Return the reference-voice audio stream from a subscribed workshop item."""
    try:
        item = await _find_subscribed_item_by_id(item_id)
    except RuntimeError as e:
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=503)

    if not item:
        return JSONResponse({
            "success": False,
            "error": "未找到对应的订阅工坊物品",
        }, status_code=404)

    install_folder = item.get('installedFolder')
    if not install_folder or not os.path.exists(install_folder):
        return JSONResponse({
            "success": False,
            "error": "工坊物品尚未安装",
        }, status_code=404)

    try:
        voice_ref = await asyncio.to_thread(_resolve_workshop_voice_reference, install_folder)
    except FileNotFoundError as e:
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=404)
    except ValueError as e:
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=400)

    if not voice_ref:
        return JSONResponse({
            "success": False,
            "error": "该工坊物品没有参考语音",
        }, status_code=404)

    audio_path = voice_ref['audio_path']
    media_type = mimetypes.guess_type(audio_path)[0] or 'application/octet-stream'
    return FileResponse(
        audio_path,
        media_type=media_type,
        filename=os.path.basename(audio_path),
    )
