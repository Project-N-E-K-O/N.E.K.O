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
import threading


WORKSHOP_VOICE_MANIFEST_NAME = 'voice_manifest.json'


WORKSHOP_MANAGED_REFERENCE_AUDIO_KEY = '_neko_managed_reference_audio'


WORKSHOP_REFERENCE_AUDIO_EXTENSIONS = {'.mp3', '.wav'}


# Frozen legacy-compatibility set: before markers, upload used fixed reference-audio names voice_sample<ext>,
# and these two literals are the **only** names old code wrote. Existing manifests have no marker; failing to recognize
# them would permanently leave generated recordings in content folders on the first post-upgrade reference replacement/removal (publish
# passes the whole directory to SetItemContent, so those files would be published too).
# ⚠️ This preserves pre-change deletion behavior, not ownership guesses from filename patterns. Never broaden it to
# prefixes or wildcards (voice_sample*): content folders belong to users, and their own same-prefix files would
# be silently deleted, precisely the problem markers eliminate.
WORKSHOP_LEGACY_MANAGED_REFERENCE_NAMES = frozenset(
    f'voice_sample{ext}' for ext in WORKSHOP_REFERENCE_AUDIO_EXTENSIONS
)


def _reference_is_managed(manifest: dict) -> bool:
    """Whether this module may delete the audio the manifest points at.

    Deletion needs proof of ownership, not merely a usable reference: imported
    and hand-edited manifests routinely point at the user's own assets.

    Two things count as proof, and nothing else. The private marker, written
    only when this route commits a file it generated, and a frozen two-name
    legacy set for manifests written before the marker existed. A manifest that
    carries a marker is not pre-marker, so a mismatched marker never falls
    through to the legacy branch.
    """
    reference = manifest.get('reference_audio')
    if not isinstance(reference, str) or not reference:
        return False
    if manifest.get(WORKSHOP_MANAGED_REFERENCE_AUDIO_KEY) == reference:
        return True
    return (
        WORKSHOP_MANAGED_REFERENCE_AUDIO_KEY not in manifest
        and reference in WORKSHOP_LEGACY_MANAGED_REFERENCE_NAMES
    )


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

    normalized = {
        'version': version,
        'reference_audio': reference_audio,
        'prefix': prefix,
        'ref_language': ref_language,
        'display_name': display_name,
        'provider_hint': provider_hint,
    }
    # This private marker is ownership metadata, not merely a second spelling
    # of reference_audio. Only upload-reference-audio writes it, and only
    # ``_reference_is_managed`` interprets it.
    #
    # Presence is carried through even when the value disagrees with the live
    # reference. Dropping a mismatched marker here would erase the difference
    # between "written before the marker existed" and "carries a marker that
    # does not match", and the first of those is the only one the legacy
    # allowlist may forgive. Keeping it makes the predicate read the same on a
    # raw manifest and on a normalized one.
    if WORKSHOP_MANAGED_REFERENCE_AUDIO_KEY in raw_manifest:
        normalized[WORKSHOP_MANAGED_REFERENCE_AUDIO_KEY] = str(
            raw_manifest.get(WORKSHOP_MANAGED_REFERENCE_AUDIO_KEY) or ''
        ).strip()
    return normalized


# Serialize whole-pair replacement and reads by content directory. Two upload swaps run in different workers
# and can genuinely interleave at OS level (A audio, B audio, A manifest → disk holds B audio with A
# manifest). Likewise, publishing may read between old-file deletion and new-file creation. Previously
# all steps ran on the event-loop thread without intervening awaits and could not overlap; after moving to threads,
# explicitly supply this serialization.
#
# Only workers hold the lock: writes use asyncio.to_thread, and publish's read call site already uses
# await asyncio.to_thread(...). The event loop never acquires it, avoiding propagation from workers holding locks
# to the event loop waiting for them.
_VOICE_REFERENCE_LOCKS: dict[str, threading.Lock] = {}
_VOICE_REFERENCE_LOCKS_GUARD = threading.Lock()


def voice_reference_lock(content_folder: str) -> threading.Lock:
    # Use realpath, not abspath: abspath normalizes lexically without resolving symlinks/junctions.
    # Write-side content_folder is normalized by _assert_under_base, while read-side
    # install_folder comes directly from Steam subscription metadata. Different paths to the same directory
    # would otherwise acquire separate locks, **silently** breaking serialization without errors. Cross-drive Steam libraries commonly use
    # junctions on Windows, so this is real. For nonexistent paths, realpath falls back to lexical normalization without raising.
    key = os.path.normcase(os.path.realpath(content_folder))
    with _VOICE_REFERENCE_LOCKS_GUARD:
        lock = _VOICE_REFERENCE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _VOICE_REFERENCE_LOCKS[key] = lock
    return lock


def resolve_voice_reference_serialized(item_dir: str) -> dict | None:
    """``_resolve_workshop_voice_reference`` that cannot observe a half-swap.

    The rule this file follows: **everyone who reads or writes the pair in a
    directory takes that directory's lock.** Stating it that way — rather than
    "only the publish reader needs it, because the other readers happen to look
    at Steam's install tree while uploads only ever write under
    WorkshopExport" — keeps it from rotting the day those paths converge.

    One exception, and it is structural: callers already inside the lock
    (``_cleanup_workshop_voice_reference``, reached from the swap) must keep
    using the unlocked form. ``threading.Lock`` is not reentrant.
    """
    with voice_reference_lock(item_dir):
        return _resolve_workshop_voice_reference(item_dir)


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
        manifest = voice_ref.get('manifest') or {}
        audio_path = voice_ref.get('audio_path')
        if _reference_is_managed(manifest) and audio_path and os.path.exists(audio_path):
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
        # Use serialized reads; see resolve_voice_reference_serialized notes. This function is called only by
        # ugc.py's listing flow, already inside to_thread and outside the lock, so there is no reentrancy issue.
        voice_ref = resolve_voice_reference_serialized(install_folder)
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
