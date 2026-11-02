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
from .content_gate import ContentFolderBusy, claim_reference_pair
from .ugc import _find_subscribed_item_by_id
from .voice_manifest import (
    WORKSHOP_REFERENCE_AUDIO_CONTENT_TYPES,
    WORKSHOP_REFERENCE_AUDIO_EXTENSIONS,
    WORKSHOP_REFERENCE_LANGUAGES,
    WORKSHOP_REFERENCE_PROVIDER_HINTS,
    WORKSHOP_MANAGED_REFERENCE_AUDIO_KEY,
    WORKSHOP_VOICE_MANIFEST_NAME,
    _cleanup_workshop_voice_reference,
    _normalize_workshop_voice_manifest,
    _reference_is_managed,
    resolve_voice_reference_serialized,
    _sanitize_voice_prefix,
    voice_reference_lock,
)

import os
import json
import asyncio
import uuid
import tempfile
from contextlib import suppress
import mimetypes
from urllib.parse import unquote
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from utils.file_utils import atomic_write_json
from utils.workshop_utils import (
    get_workshop_path_async,
)


def _current_reference_audio_path(content_folder: str) -> str | None:
    """The audio path the folder's manifest currently points at, if any.

    Best-effort and never raises: a missing or malformed manifest just means
    "nothing is claimed", which is the safe answer for a caller about to
    delete something.
    """
    manifest_path = os.path.join(content_folder, WORKSHOP_VOICE_MANIFEST_NAME)
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
    except Exception:
        return None
    reference = manifest.get('reference_audio') if isinstance(manifest, dict) else None
    if not isinstance(reference, str) or not reference:
        return None
    if not _reference_is_managed(manifest):
        # A manifest is valid input for playback/publishing, but it is not by
        # itself proof that this process created (and therefore owns) the file.
        # Imported and hand-edited manifests commonly point at user assets.
        return None
    # A manifest reference is not necessarily audio. A manually edited or malformed manifest may point to
    # assets such as preview.png in the same directory; os.remove below would delete user Workshop material.
    # An unexpected extension means the file is not ours.
    # References containing directory components are never ours: this module writes only
    # voice_sample_<hex>.<ext> directly into the content directory. assets/theme.mp3 passes
    # containment checks below but would delete the user's material in a subdirectory.
    if os.path.basename(reference) != reference:
        logger.warning('voice_manifest 的 reference_audio 带目录分量，拒绝清理: %r', reference)
        return None
    if os.path.splitext(reference)[1].lower() not in WORKSHOP_REFERENCE_AUDIO_EXTENSIONS:
        logger.warning('voice_manifest 的 reference_audio 不是支持的音频格式，拒绝清理: %r', reference)
        return None
    # ⚠️ Manifest content is untrusted: subscribed or manually edited reference_audio may contain
    # an absolute path or ../../x, making os.remove below delete outside the content directory.
    # Prove containment before deleting anything.
    try:
        return _assert_under_base(os.path.join(content_folder, reference), content_folder)
    except (PermissionError, ValueError, OSError):
        logger.warning('voice_manifest 的 reference_audio 指向内容目录之外，拒绝清理: %r', reference)
        return None


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

    The manifest write is the single commit point. The new audio goes to its
    own filename, so nothing the current manifest points at is ever
    overwritten or deleted before that commit: any failure up to it leaves the
    previous pair byte-for-byte intact, and any state after it is the complete
    new pair. There is no window in which the two halves come from different
    uploads, so there is nothing to roll back.
    """
    with claim_reference_pair(content_folder), voice_reference_lock(content_folder):
        # On entry, the only audio provably ours is the file referenced by the current manifest.
        # This establishes ownership. Guessing from filename shape (voice_sample*, or even
        # voice_sample_<12 hex digits>) is probabilistic: this is the user's directory, and a matching
        # user file would be silently deleted. The rationale matches file_utils' temporary-file ownership markers.
        previous_audio = _current_reference_audio_path(content_folder)

        # 1) Write new audio to a same-directory temporary file, fsync, then os.replace to its own filename.
        #    This name differs from the current manifest reference (the handler generates a fresh token),
        #    so this step neither overwrites nor deletes anything in use.
        fd, temp_audio = tempfile.mkstemp(dir=content_folder, suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(audio_bytes)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_audio, audio_path)

            # 2) The sole commit point. atomic_write_json uses temporary file + os.replace: either the complete old
            #    or complete new manifest. Failure before this step leaves the complete
            #    old pair on disk.
            atomic_write_json(manifest_path, manifest, ensure_ascii=False, indent=2)
        except BaseException:
            with suppress(OSError):
                os.remove(temp_audio)
            # If commit fails, remove the newly written audio too: nothing references it, but publish hands the entire
            # content directory to SetItemContent, so leaving it would publish an upload
            # reported as failed.
            with suppress(OSError):
                os.remove(audio_path)
            raise

        # 3) Only after commit does the previous manifest's audio become unreferenced and deletable.
        #    An extension change (mp3 → wav) produces a distinct name; if names theoretically match,
        #    os.replace above already replaced it, so skip here. Deletion failure merely consumes disk space.
        if previous_audio and os.path.normcase(os.path.abspath(previous_audio)) != os.path.normcase(os.path.abspath(audio_path)):
            try:
                os.remove(previous_audio)
            except OSError as exc:
                # Failure to delete (antivirus, indexer, or another open handle) does not break the reference pair,
                # but must not be silent: the superseded recording remains in the content directory, and publish
                # sends the whole directory to SetItemContent, including that recording. Log it for traceability.
                logger.warning(
                    '被顶替的旧参考语音删除失败，仍留在内容目录里: %s (%s)',
                    previous_audio, exc,
                )


def _remove_voice_reference(content_folder: str) -> None:
    """Drop the reference pair while no whole-folder consumer owns the item."""
    with claim_reference_pair(content_folder), voice_reference_lock(content_folder):
        _cleanup_workshop_voice_reference(content_folder)


@router.post('/upload-reference-audio')
async def upload_reference_audio(request: Request):
    """Upload reference audio and generate voice_manifest.json in the content directory."""
    try:
        form = await request.form()
        file = form.get('file')
        content_folder = unquote(str(form.get('content_folder', '') or '').strip())
        workshop_export_dir = os.path.join(
            await get_workshop_path_async(), 'WorkshopExport'
        )

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

        # Every upload uses a fresh filename, so new audio never overwrites the current manifest's file
        # and the manifest write remains the sole commit point (see _replace_voice_reference).
        # All consumers read reference_audio from the manifest rather than assuming voice_sample.<ext>.
        reference_audio_name = f'voice_sample_{uuid.uuid4().hex[:12]}{file_ext}'
        manifest = _normalize_workshop_voice_manifest({
            'version': 1,
            'reference_audio': reference_audio_name,
            WORKSHOP_MANAGED_REFERENCE_AUDIO_KEY: reference_audio_name,
            'prefix': prefix,
            'ref_language': ref_language,
            'display_name': display_name,
            'provider_hint': provider_hint,
        }, default_prefix=prefix, default_display_name=display_name)

        # Read the entire request body first, then submit the complete delete-old → write-audio → write-manifest
        # operation to one thread. This does more than move several MB of writes off the event loop:
        #
        # these steps must form one unit, and every intervening await is a cancellation point.
        # On disconnect, FastAPI cancels the handler; CancelledError derives from BaseException,
        # bypassing except Exception below and leaving nobody to roll back. Previously the order was
        # synchronous old-file deletion → await file.read() → synchronous audio and manifest writes;
        # cancellation during read left the old file deleted with no replacement written.
        #
        # A single to_thread unit moves the sole cancellation point before any disk write: once the thread
        # starts, cancelling its waiter does not kill it, so all three steps finish. The intermediate state
        # is now unreachable, a stronger guarantee than merely closing the newly introduced window.
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
    except ContentFolderBusy as e:
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=409)
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
        workshop_export_dir = os.path.join(
            await get_workshop_path_async(), 'WorkshopExport'
        )
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
            # Use the same per-folder lock: upload swaps now run on a worker. Deletion on the event loop
            # could otherwise interleave between audio and manifest writes, leaving a manifest
            # pointing to a nonexistent file.
            await asyncio.to_thread(_remove_voice_reference, content_folder)

        return JSONResponse({
            "success": True,
            "message": "参考语音已清理",
        })
    except ContentFolderBusy as e:
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=409)
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
