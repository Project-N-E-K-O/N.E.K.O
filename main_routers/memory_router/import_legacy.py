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
"""External memory import and legacy memory scan/purge endpoints."""

import asyncio
import base64
import binascii
import os
from pathlib import Path
from urllib.parse import quote
import httpx
from fastapi import Request
from utils.character_name import validate_character_name
from utils.cloudsave_runtime import MaintenanceModeError
from utils.language_utils import is_supported_language_code, normalize_language_code
from fastapi.responses import JSONResponse
from memory.external_markdown_import import (
    ExternalMemoryImportError,
    batch_daily_fragments,
    build_import_candidates,
    collect_markdown_files,
)
from memory.external_molili_import import MAX_ZIP_BYTES, build_molili_candidates, detect_molili_archive

from main_routers import memory_router as _mr
from ._shared import (
    logger,
    router,
)

def _collect_legacy_memory_roots(config_manager) -> list[tuple[Path, str]]:
    """
    Collect all legacy memory root directories outside the current runtime (with source tags).

    Returns ``[(Path, source), ...]``, deduplicated and order-preserving:
      - the ``memory/`` subdirectory of each candidate returned by
        ``get_legacy_app_root_candidates()`` (``source="legacy_app_root"``)
      - ``_readable_docs_dir / <app_name> / memory`` (``source="cfa_readable_docs"``)

    The currently active ``memory_dir`` is never included.
    """
    roots: list[tuple[Path, str]] = []
    seen: set[str] = set()

    try:
        runtime_memory = Path(getattr(config_manager, 'memory_dir', '') or '').resolve(strict=False)
    except Exception:
        runtime_memory = None

    def _add(path_obj: Path, source: str) -> None:
        try:
            resolved = path_obj.resolve(strict=False)
        except Exception:
            resolved = path_obj
        key = str(resolved).lower() if os.name == 'nt' else str(resolved)
        if key in seen:
            return
        if runtime_memory is not None:
            try:
                if resolved == runtime_memory:
                    return
            except Exception:
                pass
        seen.add(key)
        roots.append((path_obj, source))

    try:
        legacy_app_roots = list(config_manager.get_legacy_app_root_candidates() or [])
    except Exception as exc:
        logger.warning(f"legacy memory scan: get_legacy_app_root_candidates 失败: {exc}")
        legacy_app_roots = []

    for app_root in legacy_app_roots:
        try:
            _add(Path(app_root) / 'memory', 'legacy_app_root')
        except Exception:
            continue

    readable_docs = getattr(config_manager, '_readable_docs_dir', None)
    if readable_docs:
        try:
            app_name = getattr(config_manager, 'app_name', None) or 'N.E.K.O'
            _add(Path(readable_docs) / app_name / 'memory', 'cfa_readable_docs')
        except Exception:
            pass

    return roots


def _directory_size_safe(path: Path, *, max_entries: int = 50000) -> int:
    """
    Compute a directory's recursive size. Permission errors / vanished files are
    ignored; returns early once max_entries is exceeded to avoid blocking the
    event loop (returns -1 as a "too large / unknown" marker).
    """
    total = 0
    visited = 0
    try:
        stack: list[Path] = [path]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        visited += 1
                        if visited > max_entries:
                            return -1
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_file(follow_symlinks=False):
                                try:
                                    total += entry.stat(follow_symlinks=False).st_size
                                except (FileNotFoundError, PermissionError, OSError):
                                    continue
                            elif entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                        except (FileNotFoundError, PermissionError, OSError):
                            continue
            except (FileNotFoundError, PermissionError, OSError):
                continue
    except Exception as exc:
        logger.debug(f"_directory_size_safe({path}): 汇总大小时出错: {exc}")
        return -1
    return total


def _external_import_error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"success": False, "error": message}, status_code=status_code)


def _decode_external_archive(raw: object, *, max_bytes: int = _mr.MAX_TOTAL_BYTES) -> bytes | None:
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise ExternalMemoryImportError("archive_b64 must be a base64 string")
    value = raw.strip()
    if value.startswith("data:") and "," in value:
        value = value.split(",", 1)[1]
    max_base64_chars = 4 * ((max_bytes + 2) // 3)
    if len(value) > max_base64_chars:
        raise ExternalMemoryImportError("Archive upload is too large")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ExternalMemoryImportError("archive_b64 is not valid base64") from exc


def _prepare_external_import(payload: object) -> tuple[str, dict]:
    if not isinstance(payload, dict):
        raise ExternalMemoryImportError("Request body must be an object")
    # Keep preview and commit aligned with memory_server.validate_lanlan_name.
    validation = validate_character_name(
        payload.get("character_name"), allow_dots=True, max_length=50,
    )
    if validation.code not in {None, "reserved_route_name"}:
        raise ExternalMemoryImportError("Invalid target character name")
    requested_format = str(payload.get("source_format") or "auto").strip().lower()
    # Image attachments dominate Molili archives (~8.6 MB of a ~9 MB sample is attachments/), so the decode
    # cap is widened per format; the 8 MiB text budget is still enforced by each parser on the text it actually parses.
    # auto also decodes up to the Molili cap first: before sniffing we cannot know whether it is Molili; archives that
    # fail the sniff get an extra 8 MiB check, so OpenClaw/Hermes caps are unchanged.
    archive_cap = MAX_ZIP_BYTES if requested_format in {"molili", "auto"} else _mr.MAX_TOTAL_BYTES
    archive_bytes = _decode_external_archive(payload.get("archive_b64"), max_bytes=archive_cap)
    direct_files = payload.get("files")
    if archive_bytes is not None and direct_files:
        raise ExternalMemoryImportError("Choose either a ZIP archive or Markdown files, not both")
    is_molili = requested_format == "molili" or (
        requested_format == "auto"
        and archive_bytes is not None
        and detect_molili_archive(archive_bytes)
    )
    if not is_molili and archive_bytes is not None and len(archive_bytes) > _mr.MAX_TOTAL_BYTES:
        raise ExternalMemoryImportError("Archive upload is too large")
    if is_molili:
        analysis = build_molili_candidates(
            archive_bytes=archive_bytes,
            files=direct_files if isinstance(direct_files, list) else None,
            source_format=requested_format,
        )
    else:
        sources = collect_markdown_files(
            direct_files if isinstance(direct_files, list) else None,
            archive_bytes=archive_bytes,
        )
        analysis = build_import_candidates(
            sources,
            source_format=str(payload.get("source_format") or "auto"),
        )
    return validation.normalized, analysis


@router.post('/external_import/preview')
async def preview_external_memory_import(request: Request):
    """Parse OpenClaw/Hermes Markdown without writing memory files."""
    try:
        payload = await request.json()
        character_name, analysis = await asyncio.to_thread(_mr._prepare_external_import, payload)
        from utils.tokenize import count_tokens
        persona_cands = [item for item in analysis["candidates"] if item["target"] == "persona"]
        counts = {
            "persona": len(persona_cands),
            "facts": sum(1 for item in analysis["candidates"] if item["target"] == "facts"),
            # Daily diaries use commit-stage LLM extraction; preview shows an approximate parsed-fragment count.
            "daily": sum(1 for item in analysis["candidates"] if item.get("kind") == "daily"),
        }
        # ETA inputs (frontend estimates time and labels the 240s limit): persona fusion groups by entity
        # (neko/master), with one LLM roundtrip each; diaries get one extraction per day/source_file;
        # MEMORY.md facts are disk writes without LLM calls. Zero calls makes the frontend
        # use text without an estimate.
        persona_fusion_calls = len({(item.get("entity") or "master") for item in persona_cands})
        daily_cands = [item for item in analysis["candidates"] if item.get("kind") == "daily"]
        daily_by_file: dict[str, list[str]] = {}
        for item in daily_cands:
            daily_by_file.setdefault(str(item.get("source_file") or ""), []).append(item["text"])

        # count_tokens encodes items in batches; imports near 8MiB / 1000 entries block the event
        # loop, so offload like _prepare_external_import above. Count diary calls with the same
        # batch_daily_fragments used at commit (long days split into multiple batches),
        # ensuring estimated call counts never drift from execution.
        def _eta_inputs():
            from config import EXTERNAL_IMPORT_DAILY_INPUT_MAX_TOKENS
            persona_tokens = sum(count_tokens(item["text"]) for item in persona_cands)
            daily_tokens = sum(count_tokens(item["text"]) for item in daily_cands)
            daily_calls = sum(
                len(batch_daily_fragments(texts, EXTERNAL_IMPORT_DAILY_INPUT_MAX_TOKENS))
                for texts in daily_by_file.values()
            )
            return persona_tokens, daily_tokens, daily_calls

        persona_candidate_tokens, daily_candidate_tokens, daily_extraction_calls = (
            await asyncio.to_thread(_eta_inputs)
        )
        response = {
            "success": True,
            "character_name": character_name,
            "source_format": analysis["source_format"],
            "files": analysis["files"],
            "counts": counts,
            "candidate_count": len(analysis["candidates"]),
            "persona_fusion_calls": persona_fusion_calls,
            "persona_candidate_tokens": persona_candidate_tokens,
            "daily_extraction_calls": daily_extraction_calls,
            "daily_candidate_tokens": daily_candidate_tokens,
            "warning_count": len(analysis["warnings"]),
            "warnings": analysis["warnings"][:20],
            "candidates": analysis["candidates"][:100],
            "truncated_preview": len(analysis["candidates"]) > 100,
        }
        if "character_card" in analysis:
            # Molili only: card prefill material and the skip report; the frontend creates the character first, then imports memories
            response["character_card"] = analysis["character_card"]
            response["master_profile"] = analysis.get("master_profile") or {}
            response["skipped"] = analysis["skipped"]
            response["snapshot_time"] = analysis.get("snapshot_time", "")
        return response
    except ExternalMemoryImportError as exc:
        return _external_import_error(str(exc))
    except Exception as exc:
        logger.exception("External memory preview failed")
        return _external_import_error(f"External memory preview failed: {exc}", 500)


@router.post('/external_import/commit')
async def commit_external_memory_import(request: Request):
    """Merge OpenClaw/Hermes Markdown into a character's memory stores."""
    try:
        payload = await request.json()
        character_name, analysis = await asyncio.to_thread(_mr._prepare_external_import, payload)
        if analysis["warnings"] and payload.get("acknowledge_warnings") is not True:
            return _external_import_error(
                "Suspicious instruction patterns were detected; preview and acknowledge warnings before import",
                409,
            )
        from config import MEMORY_SERVER_PORT
        from utils.config_manager import get_config_manager
        from utils.internal_http_client import get_internal_http_client

        _mr.assert_cloudsave_writable(
            get_config_manager(),
            operation="import",
            target=f"memory/{character_name}/external-markdown",
        )
        client = get_internal_http_client()
        memory_payload = {
            "character_name": character_name,
            "source_format": analysis["source_format"],
            "imported_files": analysis["files"],
            "candidates": analysis["candidates"],
            "warning_count": len(analysis["warnings"]),
        }
        render_language = payload.get("render_language")
        if is_supported_language_code(render_language):
            memory_payload["render_language"] = normalize_language_code(
                render_language,
                format="full",
            )
        response = await client.post(
            f"http://127.0.0.1:{MEMORY_SERVER_PORT}/internal/memory/import_external_markdown",
            # Never forward a browser locale as ``language``: that field declares
            # a durable preference. ``render_language`` is a validated, render-only
            # fallback; the memory server still resolves durable state at execution.
            json=memory_payload,
            # Persona import runs synchronous LLM fusion per entity (tens of seconds each), and full
            # first imports such as Molili (30+ days × fragment-capped extraction batches) were
            # measured past 240s: a brand-new character's first import hit ReadTimeout at exactly
            # 240s (the server kept running, but the browser only saw a 500). Allow 480s for fusion
            # plus full daily extraction; the frontend commit timeout (540s) stays slightly higher
            # so backend responses arrive before frontend disconnect.
            timeout=480.0,
        )
        if response.status_code != 200:
            try:
                upstream_error = response.json()
                detail = upstream_error.get("detail") or upstream_error.get("error")
            except Exception:
                upstream_error = {}
                detail = None
            error_code = upstream_error.get("error_code")
            if error_code in ("external_import_partial", "external_import_too_large"):
                # Forward upstream error codes, partial metadata including persisted added_persona, and status
                # (partial=500 / too_large=413), or the frontend loses branch-specific guidance
                # and memory_edited broadcasts (Codex P2).
                return JSONResponse(
                    {
                        "success": False,
                        "error": detail,
                        "error_code": error_code,
                        "partial_import": upstream_error.get("partial_import") or {},
                    },
                    status_code=response.status_code,
                )
            raise ExternalMemoryImportError(
                str(detail or f"Memory service rejected the import (HTTP {response.status_code})")
            )
        result = response.json()
        if result.get("status") != "success":
            raise ExternalMemoryImportError("Memory service did not confirm the import")

        logger.info(
            "External memory import: character=%s format=%s persona=%s facts=%s duplicates=%s warnings=%s",
            character_name,
            result["source_format"],
            result["added_persona"],
            result["added_facts"],
            result["skipped_duplicates"],
            result["warning_count"],
        )
        return {
            "success": True,
            "need_refresh": True,
            "memory_server_reloaded": True,
            **result,
        }
    except MaintenanceModeError:
        raise
    except ExternalMemoryImportError as exc:
        return _external_import_error(str(exc))
    except httpx.TimeoutException:
        # The forwarding window running out is not an import failure: the memory server keeps running and daily fingerprints are
        # idempotent, so a later re-import finishes it. Give the frontend a stable error code: httpx timeout exceptions may str() to
        # an empty string, which the frontend's message regex cannot recognise and would show as a generic failure.
        logger.warning("External memory import forward timed out; memory server keeps running")
        return JSONResponse(
            {
                "success": False,
                "error": "External memory import timed out; the memory server may still be finishing it",
                "error_code": "external_import_timeout",
            },
            status_code=504,
        )
    except Exception as exc:
        logger.exception("External memory import failed")
        return _external_import_error(f"External memory import failed: {exc}", 500)


@router.get('/external_import/progress')
async def get_external_import_progress(request: Request):
    """Forward per-character external-import progress counters from memory_server.

    Polled by the Molili import progress modal; same forwarding convention as
    preview/commit."""
    character_name = request.query_params.get('character_name') or ''
    validation = validate_character_name(
        character_name, allow_dots=True, max_length=50,
    )
    if validation.code not in {None, "reserved_route_name"}:
        return _external_import_error('Invalid target character name')
    from config import MEMORY_SERVER_PORT
    from utils.internal_http_client import get_internal_http_client

    client = get_internal_http_client()
    try:
        response = await client.get(
            f"http://127.0.0.1:{MEMORY_SERVER_PORT}/internal/memory/import_progress/"
            + quote(validation.normalized, safe=''),
            timeout=10.0,
        )
    except Exception as exc:
        logger.warning(f"External import progress forward failed: {exc}")
        return JSONResponse(
            {'success': False, 'error': f'progress forward failed: {exc}'},
            status_code=502,
        )
    try:
        payload = response.json()
    except Exception:
        payload = {'success': False, 'error': f'HTTP {response.status_code}'}
    return JSONResponse(payload, status_code=response.status_code)


@router.get('/legacy/scan')
async def scan_legacy_memory():
    """
    Scan character memory directories under legacy paths and return metadata for
    each entry, used by the frontend "clean up legacy memory" dialog. This
    endpoint is **read-only** — it never deletes or migrates anything.
    """
    try:
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()

        legacy_roots = await asyncio.to_thread(_collect_legacy_memory_roots, config_manager)

        try:
            characters = await asyncio.to_thread(config_manager.load_characters)
        except Exception as exc:
            logger.warning(f"scan_legacy_memory: 加载 characters.json 失败: {exc}")
            characters = {}
        known_names: set[str] = set((characters.get('猫娘') or {}).keys())

        runtime_memory_dir = Path(getattr(config_manager, 'memory_dir', '') or '')
        runtime_existing: set[str] = set()
        try:
            if runtime_memory_dir.is_dir():
                for entry in os.scandir(runtime_memory_dir):
                    if entry.is_dir(follow_symlinks=False):
                        runtime_existing.add(entry.name)
        except Exception as exc:
            logger.debug(f"scan_legacy_memory: 枚举 runtime_memory_dir 失败: {exc}")

        roots_payload: list[dict] = []
        total_entries = 0
        total_size_bytes = 0

        for root_path, source in legacy_roots:
            try:
                exists = await asyncio.to_thread(root_path.is_dir)
            except Exception:
                exists = False
            entries_payload: list[dict] = []
            if exists:
                try:
                    raw_entries = await asyncio.to_thread(
                        lambda p=root_path: list(os.scandir(p))
                    )
                except Exception as exc:
                    logger.debug(f"scan_legacy_memory: 枚举 {root_path} 失败: {exc}")
                    raw_entries = []

                for entry in raw_entries:
                    try:
                        entry_name = entry.name
                        if not entry_name or entry_name.startswith('.') or entry_name.startswith('_'):
                            continue
                        if entry.is_symlink():
                            continue
                        is_dir = False
                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                        except Exception:
                            is_dir = False
                        entry_path = Path(entry.path)
                        if is_dir:
                            size_bytes = await asyncio.to_thread(
                                _directory_size_safe, entry_path
                            )
                        else:
                            try:
                                size_bytes = entry.stat(follow_symlinks=False).st_size
                            except Exception:
                                size_bytes = -1
                        is_unlinked = entry_name not in known_names
                        runtime_has_same_name = entry_name in runtime_existing
                        entries_payload.append({
                            'name': entry_name,
                            'path': str(entry_path),
                            'is_dir': bool(is_dir),
                            'size_bytes': int(size_bytes) if isinstance(size_bytes, (int, float)) else -1,
                            'is_unlinked': bool(is_unlinked),
                            'runtime_has_same_name': bool(runtime_has_same_name),
                        })
                    except Exception as exc:
                        logger.debug(
                            f"scan_legacy_memory: 处理条目 {entry.path} 失败: {exc}"
                        )
                        continue

            total_entries += len(entries_payload)
            for ep in entries_payload:
                sb = ep.get('size_bytes')
                if isinstance(sb, int) and sb > 0:
                    total_size_bytes += sb

            roots_payload.append({
                'root': str(root_path),
                'source': source,
                'exists': bool(exists),
                'entries': entries_payload,
            })

        return {
            'success': True,
            'runtime_memory_dir': str(runtime_memory_dir),
            'legacy_roots': roots_payload,
            'total_entries': total_entries,
            'total_size_bytes': total_size_bytes,
        }
    except MaintenanceModeError:
        raise
    except Exception as exc:
        logger.error(f"扫描 legacy memory 失败: {exc}", exc_info=True)
        return JSONResponse(
            {'success': False, 'error': f'扫描 legacy memory 失败: {exc}'},
            status_code=500,
        )


def _is_path_within(child: Path, parent: Path) -> bool:
    """
    Check whether child is strictly inside parent (parent must be a prefix, and child != parent).
    Both sides are resolved before comparison to prevent ``..`` path escapes.
    """
    try:
        child_resolved = child.resolve(strict=False)
        parent_resolved = parent.resolve(strict=False)
    except Exception:
        return False

    try:
        child_resolved.relative_to(parent_resolved)
    except ValueError:
        return False
    return child_resolved != parent_resolved


@router.post('/legacy/purge')
async def purge_legacy_memory(request: Request):
    """
    Delete exactly the legacy memory entries (paths) the user checked in the frontend.

    Safety checks (ALL must pass before deletion):
      1. Each path must be strictly inside one of the roots returned by
         ``_collect_legacy_memory_roots`` (whitelist prefix comparison after
         resolve), rejecting path escapes.
      2. Must not equal or contain the current runtime ``memory_dir``.
      3. ``..`` / relative paths / empty strings / non-strings → 400.
    """
    try:
        payload = await request.json()
    except Exception as exc:
        return JSONResponse(
            {'success': False, 'error': f'非法请求体: {exc}'}, status_code=400
        )

    raw_paths = payload.get('paths') if isinstance(payload, dict) else None
    if not isinstance(raw_paths, list) or not raw_paths:
        return JSONResponse(
            {'success': False, 'error': 'paths 必须为非空列表'}, status_code=400
        )

    try:
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        legacy_roots = await asyncio.to_thread(_collect_legacy_memory_roots, config_manager)
    except Exception as exc:
        logger.error(f"purge_legacy_memory: 初始化失败: {exc}", exc_info=True)
        return JSONResponse(
            {'success': False, 'error': f'内部错误: {exc}'}, status_code=500
        )

    if not legacy_roots:
        return JSONResponse(
            {'success': False, 'error': '当前无可清理的 legacy 根目录'},
            status_code=409,
        )

    try:
        runtime_memory = Path(getattr(config_manager, 'memory_dir', '') or '').resolve(
            strict=False
        )
    except Exception:
        runtime_memory = None

    normalized_roots: list[Path] = []
    for root_path, _ in legacy_roots:
        try:
            normalized_roots.append(root_path.resolve(strict=False))
        except Exception:
            continue

    removed: list[str] = []
    errors: list[dict] = []

    import shutil
    for raw_path in raw_paths:
        if not isinstance(raw_path, str) or not raw_path.strip():
            errors.append({'path': str(raw_path), 'error': '非法路径（非字符串或空）'})
            continue
        if '..' in raw_path.replace('\\', '/').split('/'):
            errors.append({'path': raw_path, 'error': '路径包含 .. 段，已拒绝'})
            continue

        try:
            target = Path(raw_path)
        except Exception as exc:
            errors.append({'path': raw_path, 'error': f'路径解析失败: {exc}'})
            continue

        if not target.is_absolute():
            errors.append({'path': raw_path, 'error': '必须使用绝对路径'})
            continue

        try:
            target_resolved = target.resolve(strict=False)
        except Exception as exc:
            errors.append({'path': raw_path, 'error': f'resolve 失败: {exc}'})
            continue

        if runtime_memory is not None:
            try:
                if target_resolved == runtime_memory:
                    errors.append({'path': raw_path, 'error': '禁止删除 runtime memory_dir'})
                    continue
            except Exception:
                pass

        allowed = False
        for root in normalized_roots:
            try:
                target_resolved.relative_to(root)
                if target_resolved != root:
                    allowed = True
                    break
            except ValueError:
                continue
        if not allowed:
            errors.append({
                'path': raw_path,
                'error': '路径不在 legacy 白名单根目录之下，已拒绝',
            })
            continue

        # All checks passed: delete, retrying PermissionError once.
        async def _rmtree_once(p: Path) -> None:
            if p.is_dir():
                await asyncio.to_thread(shutil.rmtree, p, ignore_errors=False)
            elif p.exists():
                await asyncio.to_thread(p.unlink)

        try:
            try:
                await _rmtree_once(target_resolved)
            except PermissionError as exc:
                logger.warning(
                    f"purge_legacy_memory: {target_resolved} PermissionError: {exc}，300ms 后重试"
                )
                await asyncio.sleep(0.3)
                await _rmtree_once(target_resolved)
            removed.append(str(target_resolved))
            logger.info(f"purge_legacy_memory: 已删除 {target_resolved}")
        except FileNotFoundError:
            # Already absent counts as success, idempotently.
            removed.append(str(target_resolved))
            logger.debug(f"purge_legacy_memory: {target_resolved} 不存在，跳过（视为已删）")
        except Exception as exc:
            logger.error(
                f"purge_legacy_memory: 删除 {target_resolved} 失败: {exc}", exc_info=True
            )
            errors.append({'path': raw_path, 'error': str(exc)})

    return {
        'success': True,
        'removed': removed,
        'errors': errors,
    }
