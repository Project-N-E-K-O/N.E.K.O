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

"""Low-level workshop voice manifest parsing/normalization (no
upward sibling dependencies; consumed by both ugc listing and voice endpoints).

Split out of the former monolithic ``main_routers/workshop_router.py``.
"""

from ._shared import logger
from .config_files import _assert_under_base

import os
import json


WORKSHOP_VOICE_MANIFEST_NAME = 'voice_manifest.json'


WORKSHOP_REFERENCE_AUDIO_EXTENSIONS = {'.mp3', '.wav'}


WORKSHOP_REFERENCE_AUDIO_CONTENT_TYPES = {
    'audio/mpeg': '.mp3',
    'audio/mp3': '.mp3',
    'audio/wav': '.wav',
    'audio/wave': '.wav',
    'audio/x-wav': '.wav',
    'audio/x-pn-wav': '.wav',
}


WORKSHOP_REFERENCE_LANGUAGES = {'ch', 'en', 'fr', 'de', 'ja', 'ko', 'ru'}


WORKSHOP_REFERENCE_PROVIDER_HINTS = {'cosyvoice', 'cosyvoice_intl', 'minimax', 'minimax_intl'}


def _sanitize_voice_prefix(prefix: str, default_prefix: str = 'voice') -> str:
    normalized = ''.join(ch for ch in str(prefix or '') if ch.isascii() and ch.isalnum())[:10]
    if normalized:
        return normalized
    fallback = ''.join(ch for ch in str(default_prefix or '') if ch.isascii() and ch.isalnum())[:10]
    return fallback or 'voice'


def _normalize_workshop_voice_manifest(raw_manifest: dict, *, default_prefix: str = 'voice',
                                       default_display_name: str = '') -> dict:
    if not isinstance(raw_manifest, dict):
        raise ValueError('voice_manifest.json 格式无效')

    reference_audio = os.path.basename(str(raw_manifest.get('reference_audio', '')).strip())
    if not reference_audio:
        raise ValueError('voice_manifest.json 缺少 reference_audio')

    audio_ext = os.path.splitext(reference_audio)[1].lower()
    if audio_ext not in WORKSHOP_REFERENCE_AUDIO_EXTENSIONS:
        raise ValueError('参考语音格式只支持 mp3 或 wav')

    prefix = _sanitize_voice_prefix(raw_manifest.get('prefix', ''), default_prefix=default_prefix)

    ref_language = str(raw_manifest.get('ref_language', 'ch') or 'ch').strip().lower()
    if ref_language not in WORKSHOP_REFERENCE_LANGUAGES:
        ref_language = 'ch'

    provider_hint = str(raw_manifest.get('provider_hint', 'cosyvoice') or 'cosyvoice').strip().lower()
    if provider_hint not in WORKSHOP_REFERENCE_PROVIDER_HINTS:
        provider_hint = 'cosyvoice'

    display_name = str(raw_manifest.get('display_name', '') or '').strip()
    if not display_name:
        display_name = str(default_display_name or prefix).strip() or prefix

    version = raw_manifest.get('version', 1)
    try:
        version = int(version)
    except (TypeError, ValueError):
        version = 1

    return {
        'version': version,
        'reference_audio': reference_audio,
        'prefix': prefix,
        'ref_language': ref_language,
        'display_name': display_name,
        'provider_hint': provider_hint,
    }


def _resolve_workshop_voice_reference(item_dir: str) -> dict | None:
    manifest_path = os.path.join(item_dir, WORKSHOP_VOICE_MANIFEST_NAME)
    if not os.path.exists(manifest_path):
        return None

    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            raw_manifest = json.load(f)
    except Exception as e:
        raise ValueError(f'读取参考语音清单失败: {e}') from e

    manifest = _normalize_workshop_voice_manifest(
        raw_manifest,
        default_prefix=os.path.basename(item_dir),
        default_display_name=os.path.basename(item_dir),
    )
    audio_path = _assert_under_base(os.path.join(item_dir, manifest['reference_audio']), item_dir)
    if not os.path.exists(audio_path) or not os.path.isfile(audio_path):
        raise FileNotFoundError(f'参考语音文件不存在: {manifest["reference_audio"]}')

    return {
        'manifest': manifest,
        'audio_path': audio_path,
        'manifest_path': manifest_path,
    }


def _cleanup_workshop_voice_reference(content_folder: str) -> None:
    manifest_path = os.path.join(content_folder, WORKSHOP_VOICE_MANIFEST_NAME)
    if not os.path.exists(manifest_path):
        return

    try:
        voice_ref = _resolve_workshop_voice_reference(content_folder)
    except Exception as e:
        logger.warning(f'删除旧参考语音时解析 manifest 失败，将仅移除 manifest 文件: {e}')
        voice_ref = None

    if voice_ref:
        audio_path = voice_ref.get('audio_path')
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except OSError as e:
                logger.warning(f'删除旧参考语音文件失败: {audio_path}, {e}')

    try:
        os.remove(manifest_path)
    except OSError as e:
        logger.warning(f'删除旧参考语音清单失败: {manifest_path}, {e}')


def _build_workshop_voice_reference_summary(install_folder: str) -> dict | None:
    try:
        voice_ref = _resolve_workshop_voice_reference(install_folder)
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f'解析工坊参考语音失败: {install_folder}, {e}')
        return None

    if not voice_ref:
        return None

    manifest = voice_ref['manifest']
    return {
        'available': True,
        'displayName': manifest['display_name'],
        'prefix': manifest['prefix'],
        'refLanguage': manifest['ref_language'],
        'providerHint': manifest['provider_hint'],
        'referenceAudio': manifest['reference_audio'],
    }
