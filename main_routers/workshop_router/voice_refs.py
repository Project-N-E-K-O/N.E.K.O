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
    resolve_voice_reference_serialized,
    _sanitize_voice_prefix,
    voice_reference_lock,
)

import os
import asyncio
import tempfile
from contextlib import suppress
import mimetypes
from urllib.parse import unquote
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from utils.file_utils import atomic_write_json
from utils.workshop_utils import (
    get_workshop_path,
)


def _replace_voice_reference(
    content_folder: str,
    audio_path: str,
    audio_bytes: bytes,
    manifest_path: str,
    manifest: dict,
) -> None:
    """Swap in a new reference-audio/manifest pair as one blocking unit.

    Kept synchronous on purpose so the caller can hand the whole swap to a
    single worker thread: the steps have no await between them, so a cancelled
    request can never observe a half-replaced pair. The per-folder lock covers
    the other direction — two workers racing the same folder.

    Nothing is deleted until the replacement is durable. Writing the new audio
    first (staged, fsynced, then renamed into place) means a disk-full or
    permission failure leaves the previous pair exactly as it was, instead of
    destroying a reference the user cannot get back.
    """
    with voice_reference_lock(content_folder):
        # 1) 新音频先落到同目录的 tmp，fsync 之后再 os.replace 顶上去。
        fd, temp_audio = tempfile.mkstemp(dir=content_folder, suffix='.tmp')
        backup_audio = None
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(audio_bytes)
                f.flush()
                os.fsync(f.fileno())

            # 同扩展名替换时，os.replace 会把旧音频原地顶掉 —— 而 manifest 还没写。
            # 万一第 2 步失败，盘上就是「新音频 + 旧 manifest」，偏偏文件名没变，
            # _resolve_workshop_voice_reference 会认为这对有效：新音频配旧的
            # prefix / 语言 / display_name / provider，而用户收到的是 500。静默的
            # 不一致比响亮的失败糟得多，所以先把旧音频原子挪到一边留作回滚。
            if os.path.exists(audio_path):
                backup_audio = f'{temp_audio}.bak'
                os.replace(audio_path, backup_audio)

            os.replace(temp_audio, audio_path)

            # 2) manifest 也是原子替换。走到这里新的一对才算完整可用。
            atomic_write_json(manifest_path, manifest, ensure_ascii=False, indent=2)
        except BaseException:
            # 回滚到进来时的状态：旧音频挪回原位，旧 manifest 本来就没动过。
            if backup_audio is not None and os.path.exists(backup_audio):
                with suppress(OSError):
                    os.replace(backup_audio, audio_path)
            with suppress(OSError):
                os.remove(temp_audio)
            raise
        finally:
            if backup_audio is not None:
                with suppress(OSError):
                    os.remove(backup_audio)

        # 3) 最后才清掉「换了扩展名」留下的旧音频（mp3 → wav 这种）。同名的那次
        #    已经被上面的 os.replace 顶掉了。删失败只是留个孤儿文件，不影响这对
        #    引用可用，所以吞掉。
        for ext in WORKSHOP_REFERENCE_AUDIO_EXTENSIONS:
            stale = os.path.join(content_folder, f'voice_sample{ext}')
            if os.path.abspath(stale) == os.path.abspath(audio_path):
                continue
            if os.path.exists(stale):
                with suppress(OSError):
                    os.remove(stale)


def _remove_voice_reference(content_folder: str) -> None:
    """Drop the reference pair under the same per-folder lock as the swap."""
    with voice_reference_lock(content_folder):
        _cleanup_workshop_voice_reference(content_folder)


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

        reference_audio_name = f'voice_sample{file_ext}'
        manifest = _normalize_workshop_voice_manifest({
            'version': 1,
            'reference_audio': reference_audio_name,
            'prefix': prefix,
            'ref_language': ref_language,
            'display_name': display_name,
            'provider_hint': provider_hint,
        }, default_prefix=prefix, default_display_name=display_name)

        # 先把整个请求体读完，再一次性把「删旧 → 写音频 → 写 manifest」交给一个
        # 线程。这样做不只是为了别在循环上写几 MB：
        #
        # 这三步必须成对成套，而它们之间的任何一个 await 都是取消点 ——
        # 客户端一断开，FastAPI 就取消 handler，CancelledError 是 BaseException，
        # 下面那个 `except Exception` 接不住，也就没人回滚。改动前这里是
        # 「删旧（同步）→ await file.read() → 写音频 → 写 manifest（同步）」，
        # 取消落在那个 read 上就会留下「旧的删了、新的没写」。
        #
        # 收成一个 to_thread 单元之后，唯一的取消点落在任何写盘之前：线程一旦
        # 启动，取消等待方并不会杀掉线程，三步照样跑完。中间态从此不可达 ——
        # 比改动前更严格，不只是把新引入的那个窗口补回去。
        audio_bytes = await file.read()
        await asyncio.to_thread(
            _replace_voice_reference,
            content_folder,
            os.path.join(content_folder, reference_audio_name),
            audio_bytes,
            os.path.join(content_folder, WORKSHOP_VOICE_MANIFEST_NAME),
            manifest,
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
            # 也走同一把 per-folder 锁：上传的 swap 现在跑在 worker 上，删除要是
            # 留在事件循环上做，就能插进「写音频」和「写 manifest」之间，留下一个
            # 指不到文件的 manifest。
            await asyncio.to_thread(_remove_voice_reference, content_folder)

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
        voice_ref = await asyncio.to_thread(resolve_voice_reference_serialized, install_folder)
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
        voice_ref = await asyncio.to_thread(resolve_voice_reference_serialized, install_folder)
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
