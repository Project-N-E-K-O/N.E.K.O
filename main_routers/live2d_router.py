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
Live2D Router

Handles Live2D model-related endpoints including:
- Model listing
- Model configuration
- Model parameters
- Emotion mappings
- Model upload

URL convention: routes declared WITHOUT trailing slash (no ``@router.get('/')``).
See ``main_routers/characters_router.py`` docstring or
``.agent/rules/neko-guide.md`` (§\"No trailing slash in API URLs\") for the rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""

import asyncio
import os
import json
import pathlib
import hashlib
from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, Request, File, UploadFile
from fastapi.responses import JSONResponse

from .shared_state import get_config_manager
from .workshop_router import get_subscribed_workshop_items
from contextlib import suppress

from utils.file_utils import atomic_write_json_async, read_json_async
from utils.frontend_utils import (
    _is_within,
    _resolve_workshop_search_dirs,
    find_model_directory,
    find_models,
    find_workshop_item_by_id,
    find_workshop_item_model_file,
)
from utils.live2d_model_refs import is_unsafe_model_ref as _is_unsafe_model_ref
from utils.live2d_model_refs import sanitize_emotion_mapping as _sanitize_emotion_mapping
from utils.logger_config import get_module_logger
from utils.url_utils import drop_non_utf8_entries, encode_url_path, is_utf8_text
from utils.workshop_utils import get_workshop_path

router = APIRouter(prefix="/api/live2d", tags=["live2d"])
logger = get_module_logger(__name__, "Main")


# Three POSTs rewrite the same .model3.json: /model_config/{name},
# /model_config_by_id/{id}, /emotion_mapping/{name}. All read/modify/replace whole file,
# without merges; unlocked concurrent writers silently erase earlier edits. Concurrent
# os.replace on same Windows target also produces PermissionError(WinError 5).
# Thus critical section starts before read; locking writes only misses full read-to-write race.
#
# Bucket by resolved file path, not model_name/model_id: by-name/by-ID routes
# can target same file, so argument-based locks fail mutual exclusion.
#
# Why asyncio.Lock rather than threading.Lock used in similar repository fixes?
# All writers must run on event loop: true here for three async POSTs; both
# synchronous GETs in anyio thread pools no longer persist. Any writing GET requires switching
# entirely to threading.Lock + asyncio.to_thread: workers cannot acquire asyncio.Lock,
# which is not thread-safe. Mixing locks is a false fix.
# ⚠️ Cost: contended module asyncio.Lock binds to current loop. Production has one
# loop (merged single process), but contention tests must clear dictionary between cases,
# or replacement function-scoped loops raise RuntimeError on second case.
#
# Module-level locks outlive router instances: imported once, rebuilding APIRouter /
# include_router never creates another exclusion domain. Dictionary accessed only on loop thread,
# with no await between lookup/insertion, so get-or-create is atomic without extra guard.
_MODEL_CONFIG_WRITE_LOCKS: dict[str, asyncio.Lock] = {}


async def _persist_model_config(model_json_path, config, *, indent: int) -> None:
    """Write the config out without letting cancellation release the caller's lock early."""
    # to_thread cannot cancel dispatched work; thread runs through os.replace. Direct await cancellation
    # (disconnect/timeout middleware) lets async with release per-file lock as CancelledError
    # passes, while os.replace still runs; concurrent handlers can again replace
    # same target, producing WinError 5, precisely the issue this PR fixes.
    # shield contains cancellation outside writer; explicitly await completion before propagating CancelledError.
    writer = asyncio.ensure_future(
        atomic_write_json_async(model_json_path, config, ensure_ascii=False, indent=indent)
    )
    try:
        await asyncio.shield(writer)
    except asyncio.CancelledError:
        # Loop, not single wait: second cancellation (shutdown after timeout) interrupts
        # asyncio.wait too, releasing lock early. Writer thread necessarily completes,
        # so loop terminates.
        while not writer.done():
            with suppress(asyncio.CancelledError):
                await asyncio.wait({writer})
        raise


def _model_config_write_lock(model_json_path: str | os.PathLike) -> asyncio.Lock:
    """Return the per-file write lock for a resolved ``.model3.json``."""
    # realpath folds junction/symlink/dot spellings; normcase folds Windows case,
    # giving every spelling of one file the same lock.
    key = os.path.normcase(os.path.realpath(os.fspath(model_json_path)))
    lock = _MODEL_CONFIG_WRITE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _MODEL_CONFIG_WRITE_LOCKS[key] = lock
    return lock


def _normalize_model_path(path: str) -> str:
    """Strip any surrounding quotes, then encode a model URL path."""
    return encode_url_path(path.strip('"'))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_model_url_to_file_path(path: str, config_manager) -> Path | None:
    normalized_path = str(path or "").strip().replace("\\", "/")
    if not normalized_path:
        return None

    decoded_path = unquote(normalized_path)
    parts = [part for part in decoded_path.split("/") if part]
    if not parts:
        return None

    if parts[0] == "user_live2d":
        local_root = getattr(config_manager, "readable_live2d_dir", None) or getattr(config_manager, "live2d_dir", None)
        return Path(local_root) / Path(*parts[1:]) if local_root and len(parts) > 1 else None
    if parts[0] == "user_live2d_local":
        local_root = getattr(config_manager, "live2d_dir", None)
        return Path(local_root) / Path(*parts[1:]) if local_root and len(parts) > 1 else None
    if parts[0] == "static":
        return Path("static") / Path(*parts[1:]) if len(parts) > 1 else None
    if parts[0] == "workshop":
        roots = _workshop_url_roots()
        if not roots or len(parts) <= 1:
            return None
        relative = Path(*parts[1:])
        # /workshop serves every Steam library; use the one that holds the file.
        found = _find_workshop_url_file(relative, roots)
        return found if found is not None else Path(roots[0]) / relative
    return None


def _workshop_url_roots() -> list[str]:
    """Directories a /workshop URL can resolve to: the configured root, then the other Steam libraries."""
    roots = []
    primary = get_workshop_path()
    if primary:
        roots.append(str(primary))
    for root in _resolve_workshop_search_dirs():
        if root not in roots:
            roots.append(root)
    return roots


def _find_workshop_url_file(relative: Path, roots: list[str]) -> Path | None:
    """First existing file ``root/relative`` whose real path stays inside its root."""
    for root in roots:
        try:
            root_real = Path(root).resolve()
            candidate = (Path(root) / relative).resolve()
            if candidate.is_relative_to(root_real) and candidate.is_file():
                return candidate
        except (OSError, RuntimeError, ValueError):
            continue
    return None


def _resolve_sync_model_file(path: str, config_manager) -> Path | None:
    """Resolve saved catalog selections through the same directory lookup as the renderer."""
    normalized = unquote(str(path or "").strip().replace("\\", "/"))
    if normalized.startswith('/'):
        if not normalized.startswith(('/user_live2d/', '/user_live2d_local/', '/static/', '/workshop/')):
            return None
        normalized = normalized[1:]
    parts = normalized.split("/")
    if not normalized or ":" in normalized or any(part in {".", ".."} for part in parts):
        return None
    if parts[0] == "workshop":
        # Every Steam library /workshop serves; each candidate stays inside its own root.
        if len(parts) <= 1:
            return None
        return _find_workshop_url_file(Path(*parts[1:]), _workshop_url_roots())
    direct = _resolve_model_url_to_file_path(path, config_manager)
    if direct is not None:
        roots = {
            'user_live2d': getattr(config_manager, 'readable_live2d_dir', None) or getattr(config_manager, 'live2d_dir', None),
            'user_live2d_local': getattr(config_manager, 'live2d_dir', None),
            'static': 'static',
        }
        root = roots.get(parts[0])
        if root is None:
            return None
        candidate = direct.resolve()
        return candidate if candidate.is_relative_to(Path(root).resolve()) and candidate.is_file() else None
    directory, _ = find_model_directory(parts[0])
    if not directory:
        return None
    root = Path(directory).resolve()
    relative = Path(*parts[1:]) if len(parts) > 1 else Path(parts[0] + ".model3.json")
    candidate = (root / relative).resolve()
    return candidate if candidate.is_relative_to(root) and candidate.is_file() else None


from services.chat_sync import host as _sync_host
_sync_host.install_model_file_resolver(_resolve_sync_model_file)


def _extract_local_shadow_key(path: str) -> str:
    decoded_path = unquote(str(path or "").strip().replace("\\", "/"))
    parts = [part for part in decoded_path.split("/") if part]
    if len(parts) < 3 or parts[0] not in {"user_live2d", "user_live2d_local"}:
        return ""
    return "/".join(parts[1:])


def _extract_workshop_shadow_key(path: str) -> tuple[str, bool]:
    decoded_path = unquote(str(path or "").strip().replace("\\", "/"))
    parts = [part for part in decoded_path.split("/") if part]
    if len(parts) < 3 or parts[0] != "workshop":
        return "", False
    if parts[1] == "WorkshopExport":
        if len(parts) < 5:
            return "", True
        return "/".join(parts[3:]), True
    return "/".join(parts[2:]), False


def _dedupe_live2d_models_for_display(models: list[dict]) -> list[dict]:
    config_manager = get_config_manager()
    normalized_entries: list[dict] = []
    seen_exact_paths: set[str] = set()
    local_groups: dict[tuple[str, str], list[int]] = {}
    workshop_actual_groups: dict[tuple[str, str], list[int]] = {}
    workshop_export_groups: dict[tuple[str, str], list[int]] = {}

    for model in models or []:
        if not isinstance(model, dict):
            continue

        normalized_model = deepcopy(model)
        raw_path = str(normalized_model.get("path") or "").strip()
        normalized_path = _normalize_model_path(raw_path) if raw_path else ""
        if normalized_path and normalized_path in seen_exact_paths:
            continue
        if normalized_path:
            seen_exact_paths.add(normalized_path)
            normalized_model["path"] = normalized_path

        file_path = _resolve_model_url_to_file_path(normalized_path or raw_path, config_manager)
        fingerprint = ""
        if file_path is not None and file_path.is_file():
            try:
                fingerprint = _sha256_file(file_path)
            except Exception:
                fingerprint = ""

        local_shadow_key = _extract_local_shadow_key(normalized_path or raw_path)
        workshop_shadow_key, is_workshop_export = _extract_workshop_shadow_key(normalized_path or raw_path)

        normalized_entries.append(
            {
                "model": normalized_model,
                "local_shadow_key": local_shadow_key,
                "workshop_shadow_key": workshop_shadow_key,
                "is_workshop_export": is_workshop_export,
                "fingerprint": fingerprint,
                "source_priority": 0 if local_shadow_key and str(normalized_model.get("source") or "") == "documents" else 1,
            }
        )
        entry_index = len(normalized_entries) - 1

        if local_shadow_key and fingerprint:
            local_groups.setdefault((local_shadow_key, fingerprint), []).append(entry_index)
        if workshop_shadow_key and fingerprint:
            target_groups = workshop_export_groups if is_workshop_export else workshop_actual_groups
            target_groups.setdefault((workshop_shadow_key, fingerprint), []).append(entry_index)

    filtered_indexes = set(range(len(normalized_entries)))

    for group_indexes in local_groups.values():
        if len(group_indexes) <= 1:
            continue
        best_index = min(
            group_indexes,
            key=lambda idx: (
                normalized_entries[idx]["source_priority"],
                idx,
            ),
        )
        for group_index in group_indexes:
            if group_index != best_index:
                filtered_indexes.discard(group_index)

    for shadow_group_key, export_indexes in workshop_export_groups.items():
        actual_indexes = workshop_actual_groups.get(shadow_group_key) or []
        if actual_indexes:
            for export_index in export_indexes:
                filtered_indexes.discard(export_index)
            continue
        if len(export_indexes) <= 1:
            continue
        for export_index in export_indexes[1:]:
            filtered_indexes.discard(export_index)

    return [
        normalized_entries[index]["model"]
        for index in range(len(normalized_entries))
        if index in filtered_indexes
    ]


def _upsert_model(
    models: list,
    model_name: str,
    item_id: str,
    path: str,
    source: str = 'steam_workshop',
    *,
    merge_by_name: bool = True,
) -> None:
    """
    Update an existing model by name, or append a distinct entry when requested.
    
    Args:
        models: List of model dictionaries to update
        model_name: Name of the model
        item_id: Steam workshop item ID
        path: Model path URL
        source: Model source (default: 'steam_workshop')
        merge_by_name: When False, preserve same-name variants as separate entries
    """
    if merge_by_name:
        existing_model = next((m for m in models if m['name'] == model_name), None)
        if existing_model:
            if not existing_model.get('item_id'):
                existing_model['item_id'] = item_id
                existing_model['source'] = source
            return

    display_name = model_name
    existing_names = [str(model.get('name') or '') for model in models if isinstance(model, dict)]
    if existing_names.count(model_name) >= 1:
        disambiguator = str(source or "").strip() or "variant"
        if item_id:
            disambiguator = f"{disambiguator} {item_id}"
        display_name = f"{display_name} ({disambiguator})"

    models.append({
        'name': model_name,
        'display_name': display_name,
        'path': path,
        'source': source,
        'item_id': item_id
    })


def _is_inside_model_dir(model_dir_real: str, path: str) -> bool:
    """
    Whether the real path of *path* (links resolved) stays inside *model_dir_real*,
    the real path of the model folder, checked with ``utils.frontend_utils._is_within``
    (the same boundary rule as ``find_workshop_item_model_file``): links
    pointing inside the folder keep working, entries whose real path leaves it are
    skipped. Skipped entries are logged so a missing motion or expression can be traced.
    """
    try:
        inside = _is_within(model_dir_real, os.path.realpath(path))
    except (OSError, ValueError):
        inside = False
    if not inside:
        logger.info("跳过真实路径不在模型目录内的条目: %s (模型目录: %s)", path, model_dir_real)
    return inside


def _locate_model_config(model_dir: str):
    """
    Probe *model_dir* (root) and its single-level subdirectories for a
    ``.model3.json`` file. Entries whose real path leaves *model_dir*
    (file or directory links pointing elsewhere) are skipped.

    Returns ``(actual_model_dir, model_config_file, subdir_name)`` on
    success, or ``(None, None, None)`` when nothing is found.
    """
    if not os.path.isdir(model_dir):
        return None, None, None
    model_dir_real = os.path.realpath(model_dir)

    for file in os.listdir(model_dir):
        if file.endswith('.model3.json') and _is_inside_model_dir(model_dir_real, os.path.join(model_dir, file)):
            return model_dir, file, None

    try:
        for subdir in os.listdir(model_dir):
            subdir_path = os.path.join(model_dir, subdir)
            if not os.path.isdir(subdir_path) or not _is_inside_model_dir(model_dir_real, subdir_path):
                continue
            for file in os.listdir(subdir_path):
                if file.endswith('.model3.json') and _is_inside_model_dir(model_dir_real, os.path.join(subdir_path, file)):
                    return subdir_path, file, subdir
    except Exception as e:
        logger.warning(f"检查子目录时出错: {e}")

    return None, None, None


# Error body for a config file whose real path leaves the model folder: refused, never read or written.
_OUTSIDE_MODEL_DIR_ERROR = {"success": False, "error": "模型配置文件不在模型目录内"}


def _find_model_root_file(model_dir: str, suffix: str) -> tuple[str | None, bool]:
    """
    Return ``(path, escaped)`` for the first file directly in *model_dir* whose name
    ends with *suffix* and whose real path stays inside *model_dir*.

    Candidates leaving the folder are skipped. ``escaped`` is True when nothing
    inside was found but at least one candidate was skipped for leaving the folder,
    so callers can refuse with a 4xx instead of reporting the file as missing.
    """
    model_dir_real = os.path.realpath(model_dir)
    escaped = False
    for file in os.listdir(model_dir):
        if not file.endswith(suffix):
            continue
        path = os.path.join(model_dir, file)
        if _is_inside_model_dir(model_dir_real, path):
            return path, False
        escaped = True
    return None, escaped


def _find_invalid_file_reference(data, config_dir: str, model_root_real: str) -> str | None:
    """
    Check the ``FileReferences`` a ``model_config`` POST would store. Return a short
    reason when the request is not an object, when ``Motions`` / ``Expressions`` are not
    the Cubism shape (``{group: [{"File": str, "Sound": str, ...}]}`` /
    ``[{"File": str, ...}]``; ``File`` required, ``Sound`` optional and ``null`` or ``""``
    meaning absent), or when a path would point outside the model folder; else None.
    Sections the request does not send are not stored, so they are not checked.
    """
    if not isinstance(data, dict):
        return "request is not an object"
    if 'FileReferences' not in data:
        return None
    file_refs = data['FileReferences']
    if not isinstance(file_refs, dict):
        return "FileReferences is not an object"
    entries = []
    if 'Motions' in file_refs:
        motions = file_refs['Motions']
        if not isinstance(motions, dict) or not all(isinstance(items, list) for items in motions.values()):
            return "Motions is not {group: [entry, ...]}"
        for items in motions.values():
            entries.extend(items)
    if 'Expressions' in file_refs:
        expressions = file_refs['Expressions']
        if not isinstance(expressions, list):
            return "Expressions is not a list"
        entries.extend(expressions)
    for item in entries:
        if not isinstance(item, dict):
            return "entry is not an object"
        for key in ('File', 'Sound'):
            file_path = item.get(key)
            if key == 'Sound' and file_path in (None, ''):
                continue  # Sound is optional (null / "" mean no audio); File is required
            if not isinstance(file_path, str):
                return f"{key} is not a string"
            if _is_unsafe_model_ref(file_path, config_dir, model_root_real):
                return f"{key} points outside the model folder"
    return None


def _find_invalid_emotion_mapping_reference(data, config_dir: str, model_root_real: str) -> str | None:
    """
    Check an emotion-mapping request (``{"motions": {group: [path, ...]}, "expressions": {...}}``).
    Return a short reason when a present section (an explicit ``null`` included) is not
    ``{group: [str, ...]}`` or a path would point outside the model folder; else None.
    The whole request is also stored as ``EmotionMapping``, so every path under these two
    sections is checked, not only the ones written to FileReferences; other top-level
    keys are stored as sent and are not interpreted as paths here.
    """
    if not isinstance(data, dict):
        return "request is not an object"
    for section in ('motions', 'expressions'):
        if section not in data:
            continue
        groups = data[section]
        if not isinstance(groups, dict):
            return f"{section} is not an object"
        for files in groups.values():
            if not isinstance(files, list):
                return f"{section} group is not a list"
            for file_path in files:
                if not isinstance(file_path, str):
                    return f"{section} entry is not a string"
                if _is_unsafe_model_ref(file_path, config_dir, model_root_real):
                    return f"{section} entry points outside the model folder"
    return None


# Upper bounds for the motion / expression listing walk. Links between inside folders
# are followed (alias folders are listed twice), so stacked aliases can grow the walk
# exponentially; real models stay far below both limits. The entry limit counts every
# directory entry visited (textures, sounds and skipped links included), per listing walk.
_MODEL_SCAN_MAX_DEPTH = 32
_MODEL_SCAN_MAX_ENTRIES = 10000
_MODEL_SCAN_LIMIT_ERROR = "模型目录层级或条目数量超过上限"


class _ModelScanLimitExceeded(Exception):
    """The listing walk exceeded ``_MODEL_SCAN_MAX_DEPTH`` or ``_MODEL_SCAN_MAX_ENTRIES``."""


def _search_model_files(model_root_real: str, start_dir: str, target_ext: str) -> list[str]:
    """
    List files ending with *target_ext* under *start_dir*, as slash-separated paths
    relative to *start_dir*.

    Entries whose real path leaves *model_root_real* are skipped. Links back to a
    directory on the current recursion chain (e.g. ``loop -> .``) are not entered;
    links to other inside directories are still listed, so alias folders show up
    twice. Raises ``_ModelScanLimitExceeded`` once the walk goes deeper than
    ``_MODEL_SCAN_MAX_DEPTH`` or visits more than ``_MODEL_SCAN_MAX_ENTRIES`` entries.
    """
    result: list[str] = []
    visited = 0

    def walk(directory: str, ancestors: tuple[str, ...]) -> None:
        nonlocal visited
        if len(ancestors) > _MODEL_SCAN_MAX_DEPTH:
            raise _ModelScanLimitExceeded(f"depth > {_MODEL_SCAN_MAX_DEPTH}")
        try:
            chain = ancestors + (os.path.normcase(os.path.realpath(directory)),)
            # scandir streams names, so the entry limit also bounds what is read from a huge folder.
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > _MODEL_SCAN_MAX_ENTRIES:
                        raise _ModelScanLimitExceeded(f"entries > {_MODEL_SCAN_MAX_ENTRIES}")
                    item = entry.name
                    item_path = os.path.join(directory, item)
                    if not _is_inside_model_dir(model_root_real, item_path):
                        continue
                    if os.path.isfile(item_path):
                        if item.endswith(target_ext):
                            # Normalize forward slashes across platforms.
                            result.append(os.path.relpath(item_path, start_dir).replace('\\', '/'))
                    elif os.path.isdir(item_path):
                        if os.path.normcase(os.path.realpath(item_path)) in chain:
                            continue
                        walk(item_path, chain)
        except _ModelScanLimitExceeded:
            raise
        except Exception as e:
            logger.warning(f"搜索目录 {directory} 时出错: {e}")

    walk(start_dir, ())
    return result


@router.get("/models")
async def get_live2d_models(simple: bool = False):
    """
    Get the list of Live2D models.
    Args:
        simple: if True, return only the list of model names; if False, return full model info
    """
    try:
        # Fetch local models first.
        models = find_models()
        skipped = 0
        
        # Then Steam Workshop models.
        try:
            workshop_items_result = await get_subscribed_workshop_items()
            
            # Process response.
            if isinstance(workshop_items_result, dict) and workshop_items_result.get('success', False):
                items = workshop_items_result.get('items', [])
                
                # Iterate items, extracting installed models.
                for item in items:
                    # Use get_subscribed_workshop_items installedFolder directly; item ID comes from publishedFileId, not item_id.
                    installed_folder = item.get('installedFolder')
                    item_id = item.get('publishedFileId')
                    
                    if installed_folder and os.path.exists(installed_folder) and os.path.isdir(installed_folder) and item_id:
                        # Check installation directory for .model3.json.
                        for filename in os.listdir(installed_folder):
                            if filename.endswith('.model3.json'):
                                if not is_utf8_text(filename):
                                    skipped += 1
                                    continue
                                model_name = os.path.splitext(os.path.splitext(filename)[0])[0]
                                path_value = _normalize_model_path(f'/workshop/{item_id}/{filename}')
                                logger.debug(f"添加模型路径: {path_value!r}, item_id类型: {type(item_id)}, filename类型: {type(filename)}")
                                _upsert_model(
                                    models,
                                    model_name,
                                    item_id,
                                    path_value,
                                    merge_by_name=False,
                                )
                            
                        # Check installation subdirectories.
                        for subdir in os.listdir(installed_folder):
                            subdir_path = os.path.join(installed_folder, subdir)
                            if os.path.isdir(subdir_path):
                                model_name = subdir
                                json_file = os.path.join(subdir_path, f'{model_name}.model3.json')
                                if os.path.exists(json_file):
                                    if not is_utf8_text(model_name):
                                        skipped += 1
                                        continue
                                    path_value = _normalize_model_path(f'/workshop/{item_id}/{model_name}/{model_name}.model3.json')
                                    logger.debug(f"添加子目录模型路径: {path_value!r}, item_id类型: {type(item_id)}, model_name类型: {type(model_name)}")
                                    _upsert_model(
                                        models,
                                        model_name,
                                        item_id,
                                        path_value,
                                        merge_by_name=False,
                                    )
        except Exception as e:
            logger.error(f"获取创意工坊模型时出错: {e}")

        # One bad name must not empty the whole list (#3230); count only, names may be private.
        models, dropped = drop_non_utf8_entries(models)
        skipped += dropped
        if skipped:
            logger.warning(f"跳过 {skipped} 个文件名不是合法 UTF-8 的 Live2D 模型")
        models = _dedupe_live2d_models_for_display(models)
        
        if simple:
            # Return model names only.
            model_names = [model["name"] for model in models]
            return {"success": True, "models": model_names}
        else:
            # Return full model information, preserving backward compatibility.
            for model in models:
                if isinstance(model, dict) and isinstance(model.get('path'), str):
                    model['path'] = _normalize_model_path(model['path'])
            return models
    except Exception as e:
        logger.error(f"获取Live2D模型列表失败: {e}")
        if simple:
            return {"success": False, "error": str(e)}
        else:
            return []

@router.get("/model_config/{model_name}")
def get_model_config(model_name: str):
    """
    Get the model3.json config of the specified Live2D model.
    """
    try:
        # Find model directory under static or user documents.
        model_dir, url_prefix = find_model_directory(model_name)
        if not model_dir or not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # Find .model3.json; a file whose real path leaves the model folder is refused.
        model_json_path, escaped = _find_model_root_file(model_dir, '.model3.json')
        if escaped:
            return JSONResponse(status_code=403, content=_OUTSIDE_MODEL_DIR_ERROR)

        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})
        
        with open(model_json_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        
        # Add missing config in memory/response only, never persist.
        #
        # Synchronous endpoint runs in anyio workers; writing converts read traffic to writes,
        # outside three POSTs' asyncio lock domain (not thread-safe, inaccessible to workers).
        # Two windows first-reading old model concurrently would os.replace same target,
        # triggering Windows PermissionError(WinError 5). POST owns persistence.
        #
        # Defaults are empty; all consumers already read optionally (get_emotion_mapping here,
        # app/monitor.py's `.get('FileReferences', {}) or {}`, POST setdefault),
        # none requires disk presence; docs/api/rest/live2d.md also declares GET read-only.
        file_refs = config_data.setdefault('FileReferences', {})
        file_refs.setdefault('Motions', {})
        file_refs.setdefault('Expressions', [])

        return {"success": True, "config": config_data}
    except Exception as e:
        logger.error(f"获取模型配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post("/model_config/{model_name}")
async def update_model_config(model_name: str, request: Request):
    """
    Update the model3.json config of the specified Live2D model.
    """
    try:
        data = await request.json()
        
        # Find model directory under static or user documents.
        model_dir, url_prefix = find_model_directory(model_name)
        if not model_dir or not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # Find .model3.json; a file whose real path leaves the model folder is refused.
        model_json_path, escaped = _find_model_root_file(model_dir, '.model3.json')
        if escaped:
            return JSONResponse(status_code=403, content=_OUTSIDE_MODEL_DIR_ERROR)

        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})
        
        # References from the request must stay inside the model folder; refuse before any read or write.
        invalid_ref = _find_invalid_file_reference(
            data, os.path.dirname(model_json_path), os.path.realpath(model_dir)
        )
        if invalid_ref is not None:
            logger.info("拒绝无效的模型文件引用: %s", invalid_ref)
            return JSONResponse(status_code=400, content={"success": False, "error": "文件引用无效或超出模型目录"})

        # Safety: modify Motions/Expressions only.
        # Lock entire read/modify/write; write-only locks let both requests read same base,
        # then last writer overwrites whole file, silently losing earlier edits.
        async with _model_config_write_lock(model_json_path):
            current_config = await read_json_async(model_json_path)

            file_refs = current_config.setdefault("FileReferences", {})
            if 'FileReferences' in data and 'Motions' in data['FileReferences']:
                file_refs['Motions'] = data['FileReferences']['Motions']

            if 'FileReferences' in data and 'Expressions' in data['FileReferences']:
                file_refs['Expressions'] = data['FileReferences']['Expressions']

            await _persist_model_config(model_json_path, current_config, indent=4)  # indent=4 preserves formatting.

        return {"success": True, "message": "模型配置已更新"}
    except Exception as e:
        logger.error(f"更新模型配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/emotion_mapping/{model_name}')
def get_emotion_mapping(model_name: str):
    """
    Get the emotion mapping config of the specified Live2D model.
    """
    try:
        # Find model directory under static or user documents.
        model_dir, url_prefix = find_model_directory(model_name)
        if not model_dir or not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # Find .model3.json; a file whose real path leaves the model folder is refused.
        model_json_path, escaped = _find_model_root_file(model_dir, '.model3.json')
        if escaped:
            return JSONResponse(status_code=403, content=_OUTSIDE_MODEL_DIR_ERROR)

        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})
        
        with open(model_json_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        # Prefer EmotionMapping; derive from FileReferences if absent.
        emotion_mapping = config_data.get('EmotionMapping') if isinstance(config_data, dict) else None
        # A stored mapping that is not an object (a corrupted file) is ignored like a missing one.
        if not isinstance(emotion_mapping, dict) or not emotion_mapping:
            derived_mapping = {"motions": {}, "expressions": {}}
            # Unexpected shapes on disk are skipped, never a 500.
            file_refs = config_data.get('FileReferences') if isinstance(config_data, dict) else None
            if not isinstance(file_refs, dict):
                file_refs = {}

            # Derive from standard Motions structure.
            motions = file_refs.get('Motions')
            if not isinstance(motions, dict):
                motions = {}
            for group_name, items in motions.items():
                files = []
                for item in items if isinstance(items, list) else []:
                    try:
                        file_path = item.get('File') if isinstance(item, dict) else None
                        if file_path:
                            files.append(file_path.replace('\\', '/'))
                    except Exception:
                        continue
                derived_mapping["motions"][group_name] = files

            # Derive standard Expressions, grouping by Name prefixes such as happy_xxx.
            expressions = file_refs.get('Expressions')
            if not isinstance(expressions, list):
                expressions = []
            for item in expressions:
                if not isinstance(item, dict):
                    continue
                name = item.get('Name')
                if not isinstance(name, str):
                    name = ''
                file_path = item.get('File') or ''
                if not isinstance(file_path, str) or not file_path:
                    continue
                file_path = file_path.replace('\\', '/')
                # Split groups at first underscore.
                if '_' in name:
                    group = name.split('_', 1)[0]
                else:
                    # Unprefixed entries go neutral, avoiding losses.
                    group = 'neutral'
                derived_mapping["expressions"].setdefault(group, []).append(file_path)

            emotion_mapping = derived_mapping

        # Leave out what the POST would refuse, so GET -> edit -> POST always round-trips.
        emotion_mapping = _sanitize_emotion_mapping(
            emotion_mapping, os.path.dirname(model_json_path), os.path.realpath(model_dir)
        )
        return {"success": True, "config": emotion_mapping}
    except Exception as e:
        logger.error(f"获取情绪映射配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post('/emotion_mapping/{model_name}')
async def update_emotion_mapping(model_name: str, request: Request):
    """
    Update the emotion mapping config of the specified Live2D model.
    """
    try:
        data = await request.json()
        
        if not data:
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的数据"})

        # Find model directory under static or user documents.
        model_dir, url_prefix = find_model_directory(model_name)
        if not model_dir or not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # Find .model3.json; a file whose real path leaves the model folder is refused.
        model_json_path, escaped = _find_model_root_file(model_dir, '.model3.json')
        if escaped:
            return JSONResponse(status_code=403, content=_OUTSIDE_MODEL_DIR_ERROR)

        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})

        # Every submitted path must stay inside the model folder; refuse the whole request
        # before any read or write (the request is also stored verbatim as EmotionMapping).
        invalid_ref = _find_invalid_emotion_mapping_reference(
            data, os.path.dirname(model_json_path), os.path.realpath(model_dir)
        )
        if invalid_ref is not None:
            logger.info("拒绝无效的情绪映射引用: %s", invalid_ref)
            return JSONResponse(status_code=400, content={"success": False, "error": "文件引用无效或超出模型目录"})

        # Lock entire read/modify/write as update_model_config. Cubism transformation below is pure CPU
        # with no await; enclosing it changes no logic (git diff -w shows indentation-only changes).
        async with _model_config_write_lock(model_json_path):
            config_data = await read_json_async(model_json_path)

            # Write standard Cubism structure (FileReferences.Motions / FileReferences.Expressions).
            file_refs = config_data.setdefault('FileReferences', {})

            # Motions input: { motions: { emotion: ["motions/xxx.motion3.json", ...] }, expressions: {...} }.
            motions_input = (data.get('motions') if isinstance(data, dict) else None) or {}
            motions_output = {}
            for group_name, files in motions_input.items():
                # Forbid motions in "常驻" group.
                if group_name == '常驻':
                    logger.info("忽略常驻组中的motion配置（只允许expression）")
                    continue
                items = []
                for file_path in files or []:
                    if not isinstance(file_path, str):
                        continue
                    normalized = str(pathlib.PurePosixPath(file_path.replace('\\', '/')))

                    items.append({"File": normalized})
                motions_output[group_name] = items
            file_refs['Motions'] = motions_output

            # Flatten expressions by emotion prefix, with Name convention "{emotion}_{basename}".
            expressions_input = (data.get('expressions') if isinstance(data, dict) else None) or {}

            # Preserve original expressions outside our emotion prefixes, avoiding custom-data overwrites.
            existing_expressions = file_refs.get('Expressions', []) or []
            emotion_prefixes = set(expressions_input.keys())
            preserved_expressions = []
            for item in existing_expressions:
                try:
                    name = (item.get('Name') or '') if isinstance(item, dict) else ''
                    prefix = name.split('_', 1)[0] if '_' in name else None
                    if not prefix or prefix not in emotion_prefixes:
                        preserved_expressions.append(item)
                except Exception:
                    preserved_expressions.append(item)

            new_expressions = []
            for emotion, files in expressions_input.items():
                for file_path in files or []:
                    if not isinstance(file_path, str):
                        continue
                    normalized = str(pathlib.PurePosixPath(file_path.replace('\\', '/')))

                    base = os.path.basename(normalized)
                    base_no_ext = base.replace('.exp3.json', '')
                    name = f"{emotion}_{base_no_ext}"
                    new_expressions.append({"Name": name, "File": normalized})

            file_refs['Expressions'] = preserved_expressions + new_expressions

            # Retain EmotionMapping copy for manager reads/backward compatibility.
            config_data['EmotionMapping'] = data

            # Save configuration file.
            await _persist_model_config(model_json_path, config_data, indent=2)

        logger.info(f"模型 {model_name} 的情绪映射配置已更新（已同步到 FileReferences）")
        return {"success": True, "message": "情绪映射配置已保存"}
    except Exception as e:
        logger.error(f"更新情绪映射配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/model_files/{model_name}')
def get_model_files(model_name: str):
    """
    Get the motion and expression file lists of the specified Live2D model.
    """
    try:
        # Find model directory under static or user documents.
        model_dir, url_prefix = find_model_directory(model_name)
        
        if not model_dir or not os.path.exists(model_dir):
            return {"success": False, "error": f"模型 {model_name} 不存在"}
        
        actual_model_dir, model_config_file, _model_name_subdir = _locate_model_config(model_dir)

        if not model_config_file:
            logger.error(
                "模型 %s 未找到 .model3.json 文件，model_dir=%s, actual_model_dir=%s",
                model_name,
                model_dir,
                actual_model_dir,
            )
            return {"success": False, "error": "模型配置文件(.model3.json)不存在"}

        # Listings stay inside the model folder: entries whose real path leaves it are skipped.
        model_root_real = os.path.realpath(model_dir)
        try:
            motion_files = _search_model_files(model_root_real, actual_model_dir, '.motion3.json')
            expression_files = _search_model_files(model_root_real, actual_model_dir, '.exp3.json')
        except _ModelScanLimitExceeded as e:
            logger.warning("模型 %s 文件扫描超过上限，已停止: %s", model_name, e)
            return {"success": False, "error": _MODEL_SCAN_LIMIT_ERROR}

        logger.debug(f"模型 {model_name} 文件统计: {len(motion_files)} 个动作文件, {len(expression_files)} 个表情文件")
        return {
            "success": True, 
            "motion_files": motion_files,
            "expression_files": expression_files
        }
    except Exception as e:
        logger.error(f"获取模型文件列表失败: {e}")
        return {"success": False, "error": str(e)}


@router.get('/model_parameters/{model_name}')
def get_model_parameters(model_name: str):
    """
    Get parameter info of the specified Live2D model (from the .cdi3.json file).
    args:
    - model_name: model name (without path or extension)
    returns:
    - success: whether the parameter info was retrieved successfully
    - parameters: parameter list; each parameter contains id, groupId and name
    - parameter_groups: parameter group list; each group contains id and name
    """
    try:
        # Find model directory.
        model_dir, url_prefix = find_model_directory(model_name)
        
        if not model_dir or not os.path.exists(model_dir):
            return {"success": False, "error": f"模型 {model_name} 不存在"}
        
        # Find .cdi3.json; a file whose real path leaves the model folder is refused.
        cdi3_file, escaped = _find_model_root_file(model_dir, '.cdi3.json')
        if escaped:
            return JSONResponse(status_code=403, content=_OUTSIDE_MODEL_DIR_ERROR)

        if not cdi3_file or not os.path.exists(cdi3_file):
            return {"success": False, "error": "未找到.cdi3.json文件"}
        
        # Read .cdi3.json.
        with open(cdi3_file, 'r', encoding='utf-8') as f:
            cdi3_data = json.load(f)
        
        # Extract parameter information.
        parameters = []
        if 'Parameters' in cdi3_data and isinstance(cdi3_data['Parameters'], list):
            for param in cdi3_data['Parameters']:
                if isinstance(param, dict) and 'Id' in param:
                    parameters.append({
                        'id': param.get('Id'),
                        'groupId': param.get('GroupId', ''),
                        'name': param.get('Name', param.get('Id'))
                    })
        
        # Extract parameter groups.
        parameter_groups = {}
        if 'ParameterGroups' in cdi3_data and isinstance(cdi3_data['ParameterGroups'], list):
            for group in cdi3_data['ParameterGroups']:
                if isinstance(group, dict) and 'Id' in group:
                    parameter_groups[group.get('Id')] = {
                        'id': group.get('Id'),
                        'name': group.get('Name', group.get('Id'))
                    }
        
        return {
            "success": True,
            "parameters": parameters,
            "parameter_groups": parameter_groups
        }
    except Exception as e:
        logger.error(f"获取模型参数信息失败: {e}")
        return {"success": False, "error": str(e)}


@router.post('/save_model_parameters/{model_name}')
async def save_model_parameters(model_name: str, request: Request):
    """
    Save model parameters to parameters.json in the model directory.
    args:
    - model_name: model name (without path or extension)
    - request: request body containing the parameter info
    """
    try:
        # Find model directory.
        model_dir, url_prefix = find_model_directory(model_name)
        
        if not model_dir or not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": f"模型 {model_name} 不存在"})
        
        # Get request-body parameters.
        body = await request.json()
        parameters = body.get('parameters', {})
        
        if not isinstance(parameters, dict):
            return JSONResponse(status_code=400, content={"success": False, "error": "参数格式错误"})
        
        # Save parameters.json; an existing entry whose real path leaves the model folder is refused.
        parameters_file = os.path.join(model_dir, 'parameters.json')
        if os.path.lexists(parameters_file) and not _is_inside_model_dir(os.path.realpath(model_dir), parameters_file):
            return JSONResponse(status_code=403, content={"success": False, "error": "参数文件不在模型目录内"})
        await atomic_write_json_async(parameters_file, parameters, indent=2, ensure_ascii=False)
        
        logger.info(f"已保存模型参数到: {parameters_file}, 参数数量: {len(parameters)}")
        return {"success": True, "message": "参数保存成功"}
    except Exception as e:
        logger.error(f"保存模型参数失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/load_model_parameters/{model_name}')
def load_model_parameters(model_name: str):
    """
    Load parameters from parameters.json in the model directory.
    args:
    - model_name: model name (without path or extension)
    returns:
    - success: whether the parameters were loaded successfully
    - parameters: loaded parameter dict
    """
    try:
        # Find model directory.
        model_dir, url_prefix = find_model_directory(model_name)
        
        if not model_dir or not os.path.exists(model_dir):
            return {"success": False, "error": f"模型 {model_name} 不存在"}
        
        # Read parameters.json.
        parameters_file = os.path.join(model_dir, 'parameters.json')
        
        if not os.path.exists(parameters_file):
            return {"success": True, "parameters": {}}  # Missing file returns empty parameters.

        # A parameters.json whose real path leaves the model folder is refused, not read.
        if not _is_inside_model_dir(os.path.realpath(model_dir), parameters_file):
            return JSONResponse(
                status_code=403,
                content={"success": False, "error": "参数文件不在模型目录内", "parameters": {}},
            )

        with open(parameters_file, 'r', encoding='utf-8') as f:
            parameters = json.load(f)
        
        if not isinstance(parameters, dict):
            return {"success": True, "parameters": {}}
        
        logger.info(f"已加载模型参数从: {parameters_file}, 参数数量: {len(parameters)}")
        return {"success": True, "parameters": parameters}
    except Exception as e:
        logger.error(f"加载模型参数失败: {e}")
        return {"success": False, "error": str(e), "parameters": {}}


@router.get("/model_config_by_id/{model_id}")
def get_model_config_by_id(model_id: str):
    """
    Get the model3.json config of the specified Live2D model.
    args:
    - model_id: model ID (from workshop.json)
    returns:
    - success: whether the config was retrieved successfully
    - config: model config dict
    """
    try:
        # Find model directory under static or user documents.
        try:
            model_dir, url_prefix = find_workshop_item_by_id(model_id)
            logger.debug(f"通过model_id {model_id} 查找模型目录: {model_dir}")
        except Exception as e:
            model_dir = ""
            logger.warning(f"通过model_id查找失败: {e}")

        if not model_dir or not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # Find .model3.json (directly in the item first, then nested), inside the item only.
        model_json_path = find_workshop_item_model_file(model_dir)
        
        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})
        
        with open(model_json_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        
        # Add missing config in memory/response only, never persist, as get_model_config.
        # Especially urgent: previously lacked try/except, so read-only Workshop directories/CFA
        # made a read endpoint return 500 for failed writes.
        file_refs = config_data.setdefault('FileReferences', {})
        file_refs.setdefault('Motions', {})
        file_refs.setdefault('Expressions', [])

        return {"success": True, "config": config_data}
    except Exception as e:
        logger.error(f"获取模型配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post("/model_config_by_id/{model_id}")
async def update_model_config_by_id(model_id: str, request: Request):
    """
    Update the model3.json config of the specified Live2D model.
    args:
    - model_id: model ID (from workshop.json)
    - request: request body containing the updated config
    returns:
    - success: whether the config was updated successfully
    - config: updated model config dict
    """
    try:
        data = await request.json()
        
        # Find model directory under static or user documents.
        try:
            model_dir, url_prefix = find_workshop_item_by_id(model_id)
            logger.debug(f"通过model_id {model_id} 查找模型目录: {model_dir}")
        except Exception as e:
            model_dir = ""
            logger.warning(f"通过model_id查找失败: {e}")

        if not model_dir or not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # Find .model3.json (directly in the item first, then nested), inside the item only.
        model_json_path = find_workshop_item_model_file(model_dir)
        
        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})
        
        # References from the request must stay inside the model folder; refuse before any read or write.
        invalid_ref = _find_invalid_file_reference(
            data, os.path.dirname(model_json_path), os.path.realpath(model_dir)
        )
        if invalid_ref is not None:
            logger.info("拒绝无效的模型文件引用: %s", invalid_ref)
            return JSONResponse(status_code=400, content={"success": False, "error": "文件引用无效或超出模型目录"})

        # Safety: modify Motions/Expressions only, locking entire read/modify/write,
        # symmetric with update_model_config. Resolved-file lock keys let by-name/by-ID
        # routes share one lock for the same Workshop model.
        async with _model_config_write_lock(model_json_path):
            current_config = await read_json_async(model_json_path)

            file_refs = current_config.setdefault("FileReferences", {})
            if 'FileReferences' in data and 'Motions' in data['FileReferences']:
                file_refs['Motions'] = data['FileReferences']['Motions']

            if 'FileReferences' in data and 'Expressions' in data['FileReferences']:
                file_refs['Expressions'] = data['FileReferences']['Expressions']

            await _persist_model_config(model_json_path, current_config, indent=4)  # indent=4 preserves formatting.

        return {"success": True, "message": "模型配置已更新"}
    except Exception as e:
        logger.error(f"更新模型配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/model_files_by_id/{model_id}')
def get_model_files_by_id(model_id: str):
    """
    Get the motion and expression file lists of the specified Live2D model.
    args:
    - model_id: model ID (from workshop.json)
    returns:
    - success: whether the file lists were retrieved successfully
    - motion_files: motion file list
    - expression_files: expression file list
    """
    try:
        # Reject invalid model_id directly.
        if not model_id or model_id.lower() == 'undefined':
            logger.warning("接收到无效的model_id请求，返回失败")
            return {"success": False, "error": "无效的模型ID"}
        
        # Try model lookup by model_id.
        model_dir = None
        url_prefix = None
        
        # First try Workshop item_id.
        try:
            model_dir, url_prefix = find_workshop_item_by_id(model_id)
            logger.debug(f"通过model_id {model_id} 查找模型目录: {model_dir}")
        except Exception as e:
            logger.warning(f"通过model_id查找失败: {e}")
        # Any directory the by-id lookup returned (/workshop or its /static fallback)
        # uses the shared model choice; only the by-name fallback below keeps the old rule.
        found_by_item_id = bool(model_dir and os.path.exists(model_dir))
        
        # If model_id yields no valid directory, fall back treating it as model_name.
        if not model_dir or not os.path.exists(model_dir):
            logger.info(f"尝试将 {model_id} 作为模型名称回退查找")
            try:
                model_dir, url_prefix = find_model_directory(model_id)
                logger.debug(f"作为模型名称查找的目录: {model_dir}")
            except Exception as e:
                logger.warning(f"作为模型名称查找失败: {e}")
        
        # Additional error checks.
        if not model_dir:
            logger.error("获取模型目录失败: 目录路径为空")
            return {"success": False, "error": "获取模型目录失败: 无效的路径"}
            
        if not os.path.exists(model_dir):
            logger.warning(f"模型目录不存在: {model_dir}")
            return {"success": False, "error": "模型不存在"}

        if found_by_item_id:
            # Same file the by-id config routes and the model URL use, so motion and
            # expression paths saved through those routes belong to this model.
            chosen = find_workshop_item_model_file(model_dir)
            if chosen:
                actual_model_dir, model_config_file = os.path.split(chosen)
                subdir = os.path.relpath(actual_model_dir, model_dir)
                model_name_subdir = None if subdir == os.curdir else subdir.replace(os.sep, '/')
            else:
                actual_model_dir, model_config_file, model_name_subdir = None, None, None
        else:
            actual_model_dir, model_config_file, model_name_subdir = _locate_model_config(model_dir)

        if not model_config_file:
            logger.warning(f"未找到模型 {model_id} 的 .model3.json 文件: {model_dir}")
            return {"success": False, "error": "未找到模型配置文件(.model3.json)"}
        
        # Listings stay inside the model folder: entries whose real path leaves it are skipped.
        model_root_real = os.path.realpath(model_dir)
        try:
            motion_files = _search_model_files(model_root_real, actual_model_dir, '.motion3.json')
            expression_files = _search_model_files(model_root_real, actual_model_dir, '.exp3.json')
        except _ModelScanLimitExceeded as e:
            logger.warning("模型 %s 文件扫描超过上限，已停止: %s", model_id, e)
            return {"success": False, "error": _MODEL_SCAN_LIMIT_ERROR}

        # Build model-config URL.
        model_config_url = None
        if url_prefix:
            # Workshop model URLs follow actual directory structure.
            if url_prefix == '/workshop':
                if model_name_subdir:
                    # Model in subdirectory: workshop/{item_id}/{model_name}/{model_name}.model3.json.
                    model_config_url = encode_url_path(f"{url_prefix}/{model_id}/{model_name_subdir}/{model_config_file}")
                else:
                    # Model directly in item directory: workshop/{item_id}/{model_name}.model3.json.
                    model_config_url = encode_url_path(f"{url_prefix}/{model_id}/{model_config_file}")
            else:
                # Other prefixes serve the folder that holds model_dir, so the URL keeps
                # the model folder name and any subfolder the config file sits in.
                model_folder = os.path.basename(os.path.normpath(model_dir))
                if model_name_subdir:
                    model_config_url = encode_url_path(f"{url_prefix}/{model_folder}/{model_name_subdir}/{model_config_file}")
                else:
                    model_config_url = encode_url_path(f"{url_prefix}/{model_folder}/{model_config_file}")
            logger.debug(f"为模型 {model_id} 构建的配置URL: {model_config_url} (模型子目录: {model_name_subdir})")
        
        logger.debug(f"文件统计: {len(motion_files)} 个动作文件, {len(expression_files)} 个表情文件")
        return {
            "success": True, 
            "motion_files": motion_files,
            "expression_files": expression_files,
            "model_config_url": model_config_url
        }
    except Exception as e:
        logger.error(f"获取模型文件列表失败: {e}")
        return {"success": False, "error": str(e)}


# Steam Workshop management API routes.
# Ensure route registered correctly.

@router.post('/upload_model')
async def upload_live2d_model(files: list[UploadFile] = File(...)):
    """
    Upload a Live2D model to the user documents directory.
    """
    import shutil
    import tempfile
    
    target_model_dir = None
    try:
        if not files:
            return JSONResponse(status_code=400, content={"success": False, "error": "没有上传文件"})
        
        # Create temporary directory for uploaded files.
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            
            # Save all uploads there, preserving directory layout.
            for file in files:
                # Extract directory layout from file-relative paths.
                file_path = file.filename
                # Ensure safe paths, removing dangerous components.
                file_path = file_path.replace('\\', '/').lstrip('/')
                if not file_path or file_path.startswith(("/", "../")) or "/../" in f"/{file_path}":
                    return JSONResponse(status_code=400, content={"success": False, "error": "非法文件路径"})
                
                target_file_path = temp_path / file_path
                target_file_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Save file.
                with open(target_file_path, 'wb') as f:
                    content = await file.read()
                    f.write(content)
            
            # Recursively find .model3.json in temp directory.
            model_json_files = list(temp_path.rglob('*.model3.json'))
            
            if not model_json_files:
                return JSONResponse(status_code=400, content={"success": False, "error": "未找到.model3.json文件"})
            
            if len(model_json_files) > 1:
                return JSONResponse(status_code=400, content={"success": False, "error": "上传的文件中包含多个.model3.json文件"})
            
            model_json_file = model_json_files[0]
            
            # Determine model root, parent of .model3.json.
            model_root_dir = model_json_file.parent
            model_name = model_root_dir.name
            
            # Get user-documents live2d directory.
            config_mgr = get_config_manager()
            config_mgr.ensure_live2d_directory()
            user_live2d_dir = config_mgr.live2d_dir
            
            # Target directory.
            target_model_dir = user_live2d_dir / model_name
            
            # Existing target: error or overwrite; choose error here.
            if target_model_dir.exists():
                # Check valid model file; absent means empty residue, automatically clean.
                has_valid_model = any(target_model_dir.rglob('*.model3.json'))
                if has_valid_model:
                    return JSONResponse(status_code=400, content={
                        "success": False, 
                        "error": f"模型 {model_name} 已存在，请先删除或重命名现有模型"
                    })
                else:
                    logger.info(f"清理残留的无效模型目录: {target_model_dir}")
                    await asyncio.to_thread(shutil.rmtree, target_model_dir, ignore_errors=True)

            # Copy model root to user-documents live2d directory.
            await asyncio.to_thread(shutil.copytree, model_root_dir, target_model_dir)

            # After upload iterate all *.motion3.json files,
            # empty Segments=[] for official whitelist and model-declared .model3.json LipSync parameters.
            # Covers official/model-declared lip sync while ignoring undeclared custom names, avoiding false matches.
            try:
                import json as _json

                # Official mouth/lip-sync whitelist, covering common and official names thoroughly.
                # Only mouth shape, phoneme A/I/U/E/O, jaw/lip movement parameters;
                # explicitly exclude head/body/expression parameters, e.g. ParamAngleZ/ParamAngleX.
                official_mouth_params = {
                    # Five basic phonemes A/I/U/E/O.
                    'ParamA', 'ParamI', 'ParamU', 'ParamE', 'ParamO',
                    # Common mouth vertical/open/shape parameters.
                    'ParamMouthUp', 'ParamMouthDown', 'ParamMouthOpen', 'ParamMouthOpenY',
                    'ParamMouthForm', 'ParamMouthX', 'ParamMouthY', 'ParamMouthSmile', 'ParamMouthPucker',
                    'ParamMouthStretch', 'ParamMouthShrug', 'ParamMouthLeft', 'ParamMouthRight',
                    'ParamMouthCornerUpLeft', 'ParamMouthCornerUpRight',
                    'ParamMouthCornerDownLeft', 'ParamMouthCornerDownRight',
                    # Lip-related (some models/official extensions).
                    'ParamLipA', 'ParamLipI', 'ParamLipU', 'ParamLipE', 'ParamLipO', 'ParamLipThickness',
                    # Jaw (some models control lip sync through jaw).
                    'ParamJawOpen', 'ParamJawForward', 'ParamJawLeft', 'ParamJawRight',
                    # Other lip-sync parameters, conservatively included.
                    'ParamMouthAngry', 'ParamMouthAngryLine'
                }

                # Read .model3.json Groups IDs where Name=="LipSync" and Target=="Parameter".
                model_declared_mouth_params = set()
                try:
                    local_model_json = target_model_dir / model_json_file.name
                    if local_model_json.exists():
                        with open(local_model_json, 'r', encoding='utf-8') as mf:
                            try:
                                model_cfg = _json.load(mf)
                                groups = model_cfg.get('Groups') if isinstance(model_cfg, dict) else None
                                if isinstance(groups, list):
                                    for grp in groups:
                                        try:
                                            if not isinstance(grp, dict):
                                                continue
                                            # Consider official LipSync groups targeting Parameter only.
                                            if grp.get('Name') == 'LipSync' and grp.get('Target') == 'Parameter':
                                                ids = grp.get('Ids') or []
                                                for pid in ids:
                                                    if isinstance(pid, str) and pid:
                                                        model_declared_mouth_params.add(pid)
                                        except Exception:
                                            continue
                            except Exception:
                                # Parse failure means no groups found; continue official whitelist.
                                pass
                except Exception:
                    pass

                # Merge official/model-declared whitelists.
                mouth_param_whitelist = set(official_mouth_params)
                mouth_param_whitelist.update(model_declared_mouth_params)

                for motion_path in target_model_dir.rglob('*.motion3.json'):
                    try:
                        with open(motion_path, 'r', encoding='utf-8') as mf:
                            try:
                                motion_data = _json.load(mf)
                            except Exception:
                                # Skip non-JSON/parse failures.
                                continue

                        modified = False
                        curves = motion_data.get('Curves') if isinstance(motion_data, dict) else None
                        if isinstance(curves, list):
                            for curve in curves:
                                try:
                                    if not isinstance(curve, dict):
                                        continue
                                    cid = curve.get('Id')
                                    if not cid:
                                        continue
                                    # Exact whitelist matches only, avoiding fuzzy false positives.
                                    if cid in mouth_param_whitelist:
                                        # Clear Segments if present.
                                        if 'Segments' in curve and curve['Segments']:
                                            curve['Segments'] = []
                                            modified = True
                                except Exception:
                                    continue

                        if modified:
                            try:
                                await atomic_write_json_async(motion_path, motion_data, ensure_ascii=False, indent=4)
                                logger.info(f"已清除口型参数：{motion_path}")
                            except Exception:
                                # Log write failures without blocking upload.
                                logger.exception(f"写入 motion 文件失败: {motion_path}")
                    except Exception:
                        continue
            except Exception:
                logger.exception("处理 motion 文件时发生错误")
            
            logger.info(f"成功上传Live2D模型: {model_name} -> {target_model_dir}")
            
            return JSONResponse(content={
                "success": True,
                "message": f"模型 {model_name} 上传成功",
                "model_name": model_name,
                "model_path": str(target_model_dir)
            })
            
    except Exception as e:
        # Clean possible leftover directory.
        try:
            if target_model_dir and target_model_dir.exists():
                await asyncio.to_thread(shutil.rmtree, target_model_dir)
                logger.info(f"已清理导入失败的残留目录: {target_model_dir}")
        except Exception as cleanup_err:
            logger.warning(f"清理导入失败的残留目录时出错: {cleanup_err}")
        logger.error(f"上传Live2D模型失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post('/upload_file/{model_name}')
async def upload_file_to_model(model_name: str, file: UploadFile = File(...), file_type: str = "motion"):
    """
    Upload a single motion or expression file to the specified model.
    args:
    - model_name: model name (without path or extension)
    - file: uploaded file object
    - file_type: file type, must be "motion" or "expression"
    """
    try:
        if not file:
            return JSONResponse(status_code=400, content={"success": False, "error": "没有上传文件"})
        
        # File-size limit, e.g. 50MB.
        MAX_UPLOAD_SIZE = 50 * 1024 * 1024
        chunk_size = 64 * 1024
        file_content = bytearray()
        try:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                file_content.extend(chunk)
                if len(file_content) > MAX_UPLOAD_SIZE:
                    return JSONResponse(status_code=400, content={"success": False, "error": f"文件过大，最大允许 {MAX_UPLOAD_SIZE // (1024*1024)}MB"})
        finally:
            await file.close()
        
        # Validate file type/JSON format.
        filename = file.filename
        if file_type == "motion":
            if not filename or not filename.lower().endswith('.motion3.json'):
                return JSONResponse(status_code=400, content={"success": False, "error": "动作文件必须是.motion3.json格式"})
            target_subdir = "motions"
        elif file_type == "expression":
            if not filename or not filename.lower().endswith('.exp3.json'):
                return JSONResponse(status_code=400, content={"success": False, "error": "表情文件必须是.exp3.json格式"})
            target_subdir = "expressions"
        else:
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的文件类型，必须是motion或expression"})
        
        # Validate JSON.
        try:
            json.loads(file_content.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse(status_code=400, content={"success": False, "error": "文件内容不是有效的 JSON 格式"})

        # Find model directory.
        model_dir, _url_prefix = find_model_directory(model_name)
        if not model_dir or not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # Create target subdirectory if absent.
        target_dir = pathlib.Path(model_dir) / target_subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # Basename only, avoiding traversal.
        safe_filename = pathlib.Path(filename).name
        
        target_file_path = target_dir / safe_filename
        try:
            with open(target_file_path, 'xb') as f:
                f.write(file_content)
        except FileExistsError:
            return JSONResponse(status_code=400, content={"success": False, "error": f"文件 {safe_filename} 已存在"})
        
        logger.info(f"成功上传{file_type}文件到模型 {model_name}: {safe_filename}")
        
        return JSONResponse(content={
            "success": True,
            "message": f"文件 {safe_filename} 上传成功",
            "filename": safe_filename,
            "file_path": str(target_file_path.relative_to(model_dir))
        })
        
    except Exception as e:
        logger.exception(f"上传文件失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/open_model_directory/{model_name}')
def open_model_directory(model_name: str):
    """
    Open the directory of the specified Live2D model.
    args:
    - model_name: model name (without path or extension)
    """
    try:
        import sys
        # Find model directory.
        model_dir, url_prefix = find_model_directory(model_name)
        
        if not model_dir or not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": f"模型目录不存在: {model_dir}"})
        
        # Open Windows directory with os.startfile.
        if os.name == 'nt':  # Windows
            os.startfile(model_dir)
        elif os.name == 'posix':  # macOS or Linux
            import subprocess
            subprocess.Popen(['open', model_dir]) if sys.platform == 'darwin' else subprocess.Popen(['xdg-open', model_dir])
        
        return {"success": True, "message": f"已打开模型目录: {model_dir}", "directory": model_dir}
    except Exception as e:
        logger.error(f"打开模型目录失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.delete('/model/{model_name}')
def delete_model(model_name: str):
    """
    Delete the specified Live2D model.
    args:
    - model_name: model name (without path or extension)
    """
    try:
        # Find model directory.
        model_dir, _url_prefix = find_model_directory(model_name)
        
        # If find_model_directory fails, search user-import directory directly as deletion fallback.
        if not model_dir or not os.path.exists(model_dir):
            try:
                config_mgr = get_config_manager()
                config_mgr.ensure_live2d_directory()
                fallback_dir = config_mgr.live2d_dir / model_name
                if fallback_dir.exists():
                    # Validate path safety against traversal.
                    fallback_real = os.path.realpath(str(fallback_dir))
                    live2d_real = os.path.realpath(str(config_mgr.live2d_dir))
                    if os.path.commonpath([fallback_real, live2d_real]) == live2d_real:
                        model_dir = str(fallback_dir)
                        _url_prefix = '/user_live2d'
                        logger.info(f"通过降级路径找到用户模型: {model_dir}")
            except Exception as e:
                logger.warning(f"降级查找用户模型时出错: {e}")
        
        if not model_dir or not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": f"模型 {model_name} 不存在"})
        
        # Only user-imported models deletable, never builtins.
        is_user_model = False
        _model_in_readonly_dir = False  # CFA: model inside protected read-only directory.

        # Check user documents, including CFA fallback/original Documents.
        try:
            config_mgr = get_config_manager()
            config_mgr.ensure_live2d_directory()
            model_dir_real = os.path.realpath(model_dir)

            # Check writable live2d_dir, possibly AppData fallback.
            user_live2d_dir = os.path.realpath(str(config_mgr.live2d_dir))
            try:
                common = os.path.commonpath([user_live2d_dir, model_dir_real])
                if common == user_live2d_dir:
                    is_user_model = True
            except ValueError:
                pass

            # CFA: also check original Documents live2d.
            if not is_user_model:
                readable_live2d = config_mgr.readable_live2d_dir
                if readable_live2d:
                    readable_live2d_real = os.path.realpath(str(readable_live2d))
                    try:
                        common = os.path.commonpath([readable_live2d_real, model_dir_real])
                        if common == readable_live2d_real:
                            is_user_model = True
                            _model_in_readonly_dir = True  # Inside CFA-protected read-only directory.
                    except ValueError:
                        pass
        except Exception as e:
            logger.warning(f"检查用户模型目录时出错: {e}")

        if not is_user_model:
            return JSONResponse(status_code=403, content={"success": False, "error": "只能删除用户导入的模型，无法删除内置模型"})

        # CFA: protected model cannot be deleted.
        if _model_in_readonly_dir:
            return JSONResponse(status_code=403, content={
                "success": False,
                "error": f"模型 {model_name} 位于受Windows安全策略保护的目录中，无法自动删除。请在文件资源管理器中手动删除: {model_dir}"
            })

        # Recheck path existence.
        if not os.path.exists(model_dir):
            logger.info(f"模型目录不存在，视为已删除: {model_name}")
            return {"success": True, "message": f"模型 {model_name} 已成功删除"}

        # Recursively delete model directory.
        import shutil
        shutil.rmtree(model_dir, ignore_errors=True)

        # Verify deletion succeeded.
        if os.path.exists(model_dir):
            logger.warning(f"删除后文件夹仍存在: {model_dir}，可能被占用或权限不足")
            return JSONResponse(status_code=500, content={"success": False, "error": f"删除模型失败，文件夹仍存在: {model_dir}"})
        else:
            logger.info(f"已删除Live2D模型: {model_name}")
            return {"success": True, "message": f"模型 {model_name} 已成功删除"}
    except Exception as e:
        logger.error(f"删除模型失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/user_models')
def get_user_models():
    """
    Get the list of user-imported models.
    """
    try:
        user_models = []
        
        # Get user-documents Live2D models.
        # CFA-aware: scan writable and original Documents paths.
        try:
            config_mgr = get_config_manager()
            config_mgr.ensure_live2d_directory()
            readable_live2d = config_mgr.readable_live2d_dir

            # Build scan list: (directory path, URL prefix).
            _scan_dirs = []
            if readable_live2d:
                # CFA: original Documents uses /user_live2d; writable fallback /user_live2d_local.
                _scan_dirs.append((str(readable_live2d), '/user_live2d'))
                docs_live2d_dir = str(config_mgr.live2d_dir)
                if docs_live2d_dir != str(readable_live2d):
                    _scan_dirs.append((docs_live2d_dir, '/user_live2d_local'))
            else:
                # Normal case.
                _scan_dirs.append((str(config_mgr.live2d_dir), '/user_live2d'))

            existing_keys = set()
            for scan_dir, url_prefix in _scan_dirs:
                if not os.path.exists(scan_dir):
                    continue
                found_dirs = set()  # Record directories recognized as valid models.
                for root, dirs, files in os.walk(scan_dir):
                    for file in files:
                        if file.endswith('.model3.json'):
                            model_name = os.path.basename(root)
                            # Deduplicate by (model_name, url_prefix),
                            # avoiding same-name models overwriting across CFA directories.
                            dedup_key = (model_name, url_prefix)
                            if dedup_key in existing_keys:
                                dirs[:] = []
                                break
                            existing_keys.add(dedup_key)
                            rel_path = os.path.relpath(root, scan_dir)
                            # Normalize '.' to empty string to avoid '/user_live2d/./' paths
                            if rel_path == '.':
                                rel_path_posix = ''
                            else:
                                rel_path_posix = pathlib.Path(rel_path).as_posix()
                            # Build path without duplicate slash
                            if rel_path_posix:
                                path = f'{url_prefix}/{rel_path_posix}/{file}'
                            else:
                                path = f'{url_prefix}/{file}'
                            # Record top-level directory name.
                            top_dir = rel_path.split(os.sep)[0] if rel_path != '.' else model_name
                            found_dirs.add(top_dir)
                            user_models.append({
                                'name': model_name,
                                'path': path,
                                'source': 'user_documents'
                            })
                            # Prune deeper traversal after finding a .model3.json
                            dirs[:] = []
                
                # Find incomplete top-level directories lacking .model3.json.
                # Usually failed-import residue; list for user deletion.
                for item in os.listdir(scan_dir):
                    item_path = os.path.join(scan_dir, item)
                    if os.path.isdir(item_path) and item not in found_dirs:
                        user_models.append({
                            'name': item,
                            'path': '',
                            'source': 'user_documents',
                            'broken': True
                        })
        except Exception as e:
            logger.warning(f"扫描用户文档模型目录时出错: {e}")

        # Scan user-imported VRM models.
        try:
            config_mgr = get_config_manager()
            config_mgr.ensure_vrm_directory()
            vrm_dir = config_mgr.vrm_dir
            if vrm_dir.exists():
                for vrm_file in vrm_dir.iterdir():
                    # Any letter case, like the VRM upload check ('Foo.VRM'); folders
                    # and dangling links are skipped as in the VRM model list.
                    if vrm_file.suffix.lower() != '.vrm':
                        continue
                    try:
                        if not vrm_file.is_file():
                            continue
                    except OSError:
                        # Type cannot be checked (e.g. EACCES on Python 3.11): skip
                        # this entry and keep scanning the rest.
                        continue
                    user_models.append({
                        'name': vrm_file.stem,
                        'path': f'/user_vrm/{vrm_file.name}',
                        'source': 'user_documents',
                        'type': 'vrm'
                    })
        except Exception as e:
            logger.warning(f"扫描用户VRM模型目录时出错: {e}")

        user_models, skipped = drop_non_utf8_entries(user_models)
        if skipped:
            logger.warning(f"跳过 {skipped} 个文件名不是合法 UTF-8 的用户模型")
        return {"success": True, "models": user_models}
    except Exception as e:
        logger.error(f"获取用户模型列表失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})
