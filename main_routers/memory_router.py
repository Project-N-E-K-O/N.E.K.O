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
Memory Router

Handles memory-related endpoints including:
- Recent files listing
- Memory review configuration

URL convention: routes declared WITHOUT trailing slash (no ``@router.get('/')``).
See ``main_routers/characters_router.py`` docstring or
``.agent/rules/neko-guide.md`` (API URLs omit trailing slashes) for the rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""

import asyncio
import base64
import binascii
import hashlib
import os
import re
import json
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from contextlib import suppress
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from utils.character_name import PROFILE_NAME_MAX_UNITS, validate_character_name
from utils.character_memory import (
    is_legacy_vector_store_dir,
    character_memory_exists,
    iter_character_memory_roots,
)
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.language_utils import is_supported_language_code, normalize_language_code
from utils.logger_config import get_module_logger
# In merged single-process mode (release default), this module and memory_server writers share a process
# and utils.recent_file's per-path lock; bare atomic_write_json_async bypasses it.
from utils.recent_file import (
    RecentFileDeletedError,
    capture_recent_generation,
    get_recent_pending_unlocked,
    read_recent_text_unlocked,
    recent_file_access,
    set_recent_pending_unlocked,
    write_recent_payload_unlocked,
)
from fastapi.responses import JSONResponse
from memory.external_markdown_import import (
    ExternalMemoryImportError,
    MAX_TOTAL_BYTES,
    batch_daily_fragments,
    build_import_candidates,
    collect_markdown_files,
)


router = APIRouter(prefix="/api/memory", tags=["memory"])
# Request-level serialization for memory switches differs from core_config.json's file-write lock:
# - config_manager.aupdate_json_config guarantees file consistency, preventing stale-snapshot overwrite
#   across read-modify-write. /core_api saves and startup migration use the same lock.
# - This additional lock covers the up-to-10-second memory_server migration when disabling strong memory, making
#   switch requests and frontend-recovery GET reads wait for the in-flight operation's final result.
# Slow migration runs outside the file lock, leaving /core_api and other writers unblocked during those ten seconds.
_memory_toggle_write_lock = asyncio.Lock()

# Pattern for valid recent file names: must start with "recent_", have content, and end with .json
# Uses blacklist approach instead of whitelist to support CJK characters
VALID_RECENT_FILENAME_PATTERN = re.compile(r'^recent_.+\.json$')
PATH_ERROR_INVALID_REQUEST = "INVALID_REQUEST"
PATH_ERROR_NOT_FOUND = "NOT_FOUND"
REPETITION_INSIGHT_LANGUAGES = frozenset(
    {"en", "es", "pt", "ru", "ja", "ko", "zh-CN", "zh-TW"}
)


class RepetitionInsightsRequest(BaseModel):
    character_name: str
    language: str
    assistant_message_limit: int = Field(default=100, ge=3, le=100)
    # Kept only for compatibility with older clients. Message-scoped reports
    # no longer use this value.
    effect_days: Literal[7, 30, 90] = 30


class RepetitionEffectsResetRequest(BaseModel):
    character_name: str


def _empty_repetition_effects(days: int) -> dict:
    return {
        "schema_version": "anti-repeat-effects/v1",
        "source_available": False,
        "started_at": 0.0,
        "period_days": days,
        "totals": {
            "soft_hint_injected": 0,
            "detected": 0,
            "regen_triggered": 0,
            "regen_guard_passed": 0,
            "blocked_delivery": 0,
            "break_reminder_suppressed": 0,
            "abandoned_user_interaction": 0,
            "unattributed": 0,
        },
        "reason_counts": {
            "bm25": 0,
            "literal_similarity": 0,
            "unanswered_repeat": 0,
        },
        "bm25": {
            "pair_count": 0,
            "average_before": 0.0,
            "average_after": 0.0,
            "reduction_ratio": 0.0,
        },
        "patterns": [],
    }


def _empty_message_scoped_repetition_effects(limit: int) -> dict:
    effects = _empty_repetition_effects(30)
    effects.pop("period_days", None)
    effects.update(
        {
            "scope_type": "assistant_messages",
            "assistant_message_limit": limit,
            "linked_message_count": 0,
        }
    )
    return effects


def _is_safe_containment_phrase(language: str, phrase: str) -> bool:
    compact = re.sub(r"\s+", "", phrase)
    if language in {"ja", "ko", "zh-CN", "zh-TW"}:
        return len(compact) >= 4
    return len(phrase) >= 4 and len(phrase.split()) >= 2


def _is_runtime_detector_signature(language: str, phrase: str, reasons: object) -> bool:
    if not isinstance(reasons, dict) or not any(
        int(reasons.get(reason, 0)) > 0 for reason in ("bm25", "unanswered_repeat")
    ):
        return False
    compact = re.sub(r"\s+", "", phrase)
    if language in {"ja", "ko", "zh-CN", "zh-TW"}:
        return len(compact) in {2, 3}
    return len(phrase) >= 2 and len(phrase.split()) == 1


def _repetition_association_language(language: str) -> str:
    """Use one comparison key for legacy Simplified Chinese effect records."""
    return "zh-CN" if language in {"zh", "zh-CN"} else language


def _phrases_contain_each_other(language: str, left: str, right: str) -> bool:
    left_tokens = left.split()
    right_tokens = right.split()
    use_token_boundaries = language in {"en", "es", "pt", "ru"} or (
        language == "ko" and len(left_tokens) > 1 and len(right_tokens) > 1
    )
    if not use_token_boundaries:
        return left in right or right in left

    shorter, longer = sorted((left_tokens, right_tokens), key=len)
    width = len(shorter)
    return any(
        longer[start : start + width] == shorter
        for start in range(len(longer) - width + 1)
    )


def _aggregate_repetition_associations(associations: list[dict]) -> list[dict]:
    """Fold per-pattern associations into one row per candidate.

    The panel only ever reduces these to four totals plus an "any at all?"
    test (``static/js/memory_browser.js`` at 1208 and 1461); no consumer reads
    the per-pattern fields. Shipping one row per (candidate, pattern) pair made
    the payload the PRODUCT of two capped lists -- 200 candidates against up to
    1920 window patterns -- measured at 5,366 rows / 1.62 MiB at that cap, and
    24,036 rows / 7.94 MiB for a character whose n-grams all contain one
    another. Folding bounds it by the candidate count instead, and every
    displayed number stays identical because the panel was summing anyway.

    Capping the list instead would have been wrong: a truncated array turns
    those sums into silently WRONG totals on the card, which is worse than a
    large payload.

    Nothing becomes unrecoverable. Associations are derived per request from
    the mined candidates and the effects sidecar; no store and no export keeps
    them, so restoring per-pattern detail later is a server-side change and a
    re-run, not a migration.
    """
    folded: dict[tuple[str, str], dict] = {}
    for association in associations:
        key = (association["language"], association["normalized_phrase"])
        row = folded.get(key)
        if row is None:
            row = {
                "normalized_phrase": association["normalized_phrase"],
                "language": association["language"],
                # "exact" wins over "contained": the card's meaning is "the
                # runtime has handled this phrase", and an exact hit is the
                # stronger claim.
                "association_type": association["association_type"],
                "effect_pattern_count": 0,
                "detected_count": 0,
                "regen_triggered_count": 0,
                "regen_guard_passed_count": 0,
                "blocked_count": 0,
                "residual_occurrence_count": association[
                    "residual_occurrence_count"
                ],
                "residual_message_count": association["residual_message_count"],
            }
            folded[key] = row
        if association["association_type"] == "exact":
            row["association_type"] = "exact"
        row["effect_pattern_count"] += 1
        for field in (
            "detected_count",
            "regen_triggered_count",
            "regen_guard_passed_count",
            "blocked_count",
        ):
            row[field] += association[field]
    return list(folded.values())


def _associate_repetition_effects(
    candidates: list,
    patterns: list,
) -> list[dict]:
    associations: list[dict] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        language = candidate.get("language")
        candidate_phrase = candidate.get("normalized_phrase")
        if not isinstance(language, str) or not isinstance(candidate_phrase, str):
            continue
        association_language = _repetition_association_language(language)
        for pattern in patterns:
            if not isinstance(pattern, dict):
                continue
            pattern_language = pattern.get("language")
            if (
                not isinstance(pattern_language, str)
                or _repetition_association_language(pattern_language)
                != association_language
            ):
                continue
            effect_phrase = pattern.get("normalized_phrase")
            if not isinstance(effect_phrase, str):
                continue
            association_type = None
            if effect_phrase == candidate_phrase:
                association_type = "exact"
            elif (
                _is_safe_containment_phrase(language, candidate_phrase)
                and (
                    _is_safe_containment_phrase(language, effect_phrase)
                    or _is_runtime_detector_signature(
                        language,
                        effect_phrase,
                        pattern.get("reasons"),
                    )
                )
                and _phrases_contain_each_other(
                    association_language,
                    candidate_phrase,
                    effect_phrase,
                )
            ):
                association_type = "contained"
            if association_type is None:
                continue
            associations.append(
                {
                    "normalized_phrase": candidate_phrase,
                    "language": language,
                    "effect_normalized_phrase": effect_phrase,
                    "association_type": association_type,
                    "detected_count": int(pattern.get("detected_count", 0)),
                    "regen_triggered_count": int(
                        pattern.get("regen_triggered_count", 0)
                    ),
                    "regen_guard_passed_count": int(
                        pattern.get("regen_guard_passed_count", 0)
                    ),
                    "blocked_count": int(pattern.get("blocked_count", 0)),
                    "residual_occurrence_count": int(
                        candidate.get("occurrence_count", 0)
                    ),
                    "residual_message_count": int(candidate.get("message_count", 0)),
                }
            )
    return associations


async def _await_browser_save_transaction(coro):
    """Finish a committed browser save before propagating request cancellation."""
    operation = asyncio.create_task(coro)
    try:
        return await asyncio.shield(operation), False
    except asyncio.CancelledError:
        while not operation.done():
            with suppress(asyncio.CancelledError):
                await asyncio.wait({operation})
        try:
            result = operation.result()
        except BaseException as exc:
            raise asyncio.CancelledError from exc
        return result, True


def extract_catgirl_name_from_recent_filename(filename: str) -> str | None:
    """Convert a logical recent filename (recent_<name>.json) to a character name."""
    if not isinstance(filename, str):
        return None
    match = re.match(r'^recent_(.+)\.json$', filename)
    return match.group(1) if match else None


def build_recent_filename(catgirl_name: str) -> str:
    """Build the legacy logical filename used by the memory browser UI."""
    return f"recent_{catgirl_name}.json"


def iter_recent_memory_files(base_dir: Path) -> list[str]:
    """List logical recent filenames from both legacy flat files and character dirs."""
    if not base_dir.exists():
        return []

    logical_names: set[str] = set()

    # REAL entries only, on both branches. ``is_file()`` and ``is_dir()``
    # follow links, and this list is what the insights selector and the
    # memory browser both enumerate from -- so a link in the memory root
    # was offered as a character and its target read from outside the root.
    # Filtering only the caller's own directory scan left this path to
    # re-admit it, by BOTH shapes: a linked directory and a linked
    # recent_<name>.json.
    for flat_file in base_dir.glob('recent_*.json'):
        if flat_file.is_file() and not flat_file.is_symlink():
            logical_names.add(flat_file.name)

    for child in base_dir.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        # And not another character's vector store. This is the SECOND door
        # into the same candidate set: filtering only the selector's own
        # directory scan left "semantic_memory_Alice/recent.json" to come
        # back through here as "recent_semantic_memory_Alice.json" -- the
        # exact re-admission the symlink note above already warns about, one
        # shape later.
        if is_legacy_vector_store_dir(base_dir, child.name):
            continue
        recent_file = child / 'recent.json'
        if recent_file.is_file() and not recent_file.is_symlink():
            logical_names.add(build_recent_filename(child.name))

    return sorted(logical_names)


def resolve_recent_file_path(
    config_manager,
    filename: str,
    *,
    create: bool = False,
) -> tuple[Path | None, str, str, str | None]:
    """
    Resolve a logical recent filename to the actual storage path.

    Supports both:
    - New layout: memory/<catgirl>/recent.json
    - Legacy layout: memory/recent_<catgirl>.json
    """
    catgirl_name = extract_catgirl_name_from_recent_filename(filename)
    if not catgirl_name:
        return None, "文件名格式不合法，必须以 recent_ 开头并以 .json 结尾", PATH_ERROR_INVALID_REQUEST, None

    memory_dir = Path(config_manager.memory_dir)
    project_memory_dir = Path(config_manager.project_memory_dir)

    if create:
        target_dir = memory_dir / catgirl_name
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / 'recent.json', "", "", catgirl_name

    candidates = [
        memory_dir / catgirl_name / 'recent.json',
        memory_dir / filename,
        project_memory_dir / catgirl_name / 'recent.json',
        project_memory_dir / filename,
    ]

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            return candidate, "", "", catgirl_name

    return None, "文件不存在", PATH_ERROR_NOT_FOUND, catgirl_name


def path_error_status_code(error_code: str) -> int:
    if error_code == PATH_ERROR_NOT_FOUND:
        return 404
    return 400


def validate_catgirl_name(name: str, allow_dots: bool = False, *, reject_reserved_route: bool = True) -> tuple[bool, str]:
    """
    Validate a catgirl name for safe use in filenames.
    
    Args:
        name: The catgirl name to validate
        allow_dots: If True, permit dots in the name (for historical names during migration).
                    Path traversal via '..' is still rejected.
    
    Returns:
        tuple: (is_valid, error_message)
    """
    result = validate_character_name(name, allow_dots=allow_dots, max_length=100)
    if result.code == "empty":
        return False, "名称不能为空"
    if result.code in {"contains_path_separator", "path_traversal"}:
        return False, "名称不能包含路径分隔符或目录遍历字符"
    if result.code == "contains_dot":
        return False, "名称不能包含点号(.)"
    if result.code == "unsafe_dot":
        return False, "名称不能仅由点号组成或以点号结尾"
    if result.code == "reserved_device_name":
        return False, "名称不能使用 Windows 保留设备名"
    if reject_reserved_route and result.code == "reserved_route_name":
        return False, "此名称是系统保留的路由名称，不能用作名称"
    if result.code == "invalid_character":
        return False, "名称只能包含文字、数字、空格、下划线、连字符、括号、间隔号(·/・)和撇号"
    if result.code == "too_long_length":
        return False, "名称长度不能超过100个字符"
    return True, ""


# Per-message text limit in characters. Incident: a user pasted a large external text
# into recent, persisted as one message. The memory pipeline later fed it
# to the embedder as a stale entry; pad-to-longest across the batch pushed
# activation memory into multiple gigabytes. Even with embedder token-budget
# safeguards, block single huge blobs at this boundary so oversized objects cannot
# spread through NDJSON / database / recall. 32K characters is about 32K Chinese tokens,
# generous for normal chats (5K Chinese characters is already a long article) while bounding
# worst-case inbound size.
_RECENT_MESSAGE_TEXT_MAX_CHARS = 32 * 1024
# Cumulative chat-payload text limit prevents accidents/attacks such as pasting 1000 messages of 30K characters.
# 2MB still accommodates real long chats while rejecting abnormal volume.
_RECENT_CHAT_TOTAL_CHARS_MAX = 2 * 1024 * 1024
# Message-count limit provides redundant protection: hundreds of thousands of short messages
# can still overwhelm downstream scan/embed/render work.
_RECENT_CHAT_MAX_MESSAGES = 10000


def validate_chat_payload(chat: any) -> tuple[bool, str]:
    """
    Validate the chat payload structure.

    Args:
        chat: The chat payload to validate

    Returns:
        tuple: (is_valid, error_message)
    """
    if not isinstance(chat, list):
        return False, "chat 必须是一个列表"

    if len(chat) > _RECENT_CHAT_MAX_MESSAGES:
        return False, f"chat 消息数 {len(chat)} 超过上限 {_RECENT_CHAT_MAX_MESSAGES}"

    total_chars = 0
    for idx, item in enumerate(chat):
        if not isinstance(item, dict):
            return False, f"chat[{idx}] 必须是一个字典"

        # Validate required 'role' key
        if 'role' not in item:
            return False, f"chat[{idx}] 缺少必需的 'role' 字段"

        if not isinstance(item['role'], str):
            return False, f"chat[{idx}]['role'] 必须是字符串"

        # Validate optional 'text' key if present
        if 'text' in item:
            if not isinstance(item['text'], str):
                return False, f"chat[{idx}]['text'] 必须是字符串"
            text_len = len(item['text'])
            if text_len > _RECENT_MESSAGE_TEXT_MAX_CHARS:
                return False, (
                    f"chat[{idx}]['text'] 长度 {text_len} 超过单条上限 "
                    f"{_RECENT_MESSAGE_TEXT_MAX_CHARS}(粘贴超长文本请拆分)"
                )
            total_chars += text_len
            if total_chars > _RECENT_CHAT_TOTAL_CHARS_MAX:
                return False, (
                    f"chat 累计文本超过总量上限 {_RECENT_CHAT_TOTAL_CHARS_MAX}"
                )

    return True, ""


def validate_recent_filename(filename: str) -> tuple[bool, str]:
    """
    Validate a recent file filename for safe use.
    
    Args:
        filename: The filename to validate
        
    Returns:
        tuple: (is_valid, error_message)
    """
    if not filename:
        return False, "文件名不能为空"
    
    if not isinstance(filename, str):
        return False, "文件名必须是字符串"
    
    # Reject path separators and parent directory references
    if os.path.sep in filename or '/' in filename or '\\' in filename or '..' in filename:
        return False, "文件名不能包含路径分隔符或目录遍历字符"
    
    # Ensure filename matches strict pattern
    if not VALID_RECENT_FILENAME_PATTERN.match(filename):
        return False, "文件名格式不合法，必须以 recent_ 开头并以 .json 结尾"
    
    # Ensure Path(filename).name == filename (no directory components)
    if Path(filename).name != filename:
        return False, "文件名不能包含目录路径"
    
    return True, ""


def safe_memory_path(memory_dir: Path, filename: str) -> tuple[Path | None, str]:
    """
    Safely construct and validate a path within the memory directory.
    
    Args:
        memory_dir: The base memory directory
        filename: The filename to add to the path
        
    Returns:
        tuple: (resolved_path or None, error_message)
    """
    try:
        # Construct path using pathlib
        target_path = memory_dir / filename
        
        # Resolve to absolute path (resolves .., symlinks, etc.)
        resolved_path = target_path.resolve()
        resolved_memory_dir = memory_dir.resolve()
        
        # Verify the resolved path is inside memory_dir
        # Use is_relative_to for Python 3.9+, otherwise check common path
        try:
            if not resolved_path.is_relative_to(resolved_memory_dir):
                return None, "路径越界：目标路径不在允许的目录内"
        except AttributeError:
            # Fallback for Python < 3.9
            try:
                resolved_path.relative_to(resolved_memory_dir)
            except ValueError:
                return None, "路径越界：目标路径不在允许的目录内"
        
        return resolved_path, ""
    except Exception as e:
        return None, f"路径验证失败: {str(e)}"

logger = get_module_logger(__name__, "Main")


@router.post('/repetition_insights')
async def repetition_insights(request: RepetitionInsightsRequest):
    """Run an explicit, local-only review of persisted assistant text."""
    # The same cap the INTERNAL analysis route enforces. Without it an
    # over-long name passed here, failed there with 400, and got remapped
    # to "local memory analysis unavailable" -- a 503 that sends the user
    # hunting a memory-server fault that does not exist.
    validation = validate_character_name(
        request.character_name,
        allow_dots=True,
        max_units=PROFILE_NAME_MAX_UNITS,
    )
    if not validation.ok and validation.code != "reserved_route_name":
        return JSONResponse(
            {"success": False, "error": "invalid character name"},
            status_code=422,
        )
    character_name = validation.normalized
    if request.language not in REPETITION_INSIGHT_LANGUAGES:
        return JSONResponse(
            {"success": False, "error": "unsupported analysis language"},
            status_code=422,
        )

    try:
        from config import MEMORY_SERVER_PORT
        from utils.config_manager import get_config_manager
        from utils.internal_http_client import get_internal_http_client

        config_manager = get_config_manager()
        characters = await config_manager.aload_characters()
        configured_characters = (
            characters.get("猫娘", {}) if isinstance(characters, dict) else {}
        )
        # The configured KEY, not the normalized request name: they differ
        # when a hand-edited characters.json carries padding, and reading
        # the normalized form there lands on an unrelated orphan directory.
        configured_key = _configured_character_key(
            configured_characters, character_name
        )
        if configured_key is None and not character_memory_exists(
            config_manager, character_name
        ):
            return JSONResponse(
                {"success": False, "error": "character not found"},
                status_code=404,
            )
        if configured_key is not None:
            character_name = configured_key
        if _default_memory_dir_escapes_root(config_manager, character_name):
            # Reported as absent rather than as a link: what is on the
            # far end is not this panel's to describe either.
            return JSONResponse(
                {"success": False, "error": "character not found"},
                status_code=404,
            )

        response = await get_internal_http_client().post(
            "http://127.0.0.1:"
            f"{MEMORY_SERVER_PORT}/internal/memory/"
            f"{quote(character_name, safe='')}/repetition_insights",
            json={
                "language": request.language,
                "assistant_message_limit": request.assistant_message_limit,
            },
            timeout=30.0,
        )
        if response.status_code != 200:
            status_code = response.status_code
            if status_code not in {404, 422, 503}:
                status_code = 503
            return JSONResponse(
                {"success": False, "error": "local memory analysis unavailable"},
                status_code=status_code,
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("invalid local memory analysis response")
        response_ids = payload.pop("_anti_repeat_response_ids", None)
        message_scoped = isinstance(response_ids, list)
        # The local budget can narrow the window, so the requested limit is what
        # the user asked for while `analyzed_message_count` is what was actually
        # mined. The effect scope must be labelled with the latter, or the panel
        # says "the latest 100 replies" over an aggregate covering ten.
        payload_summary = payload.get("summary")
        analyzed_limit = request.assistant_message_limit
        if isinstance(payload_summary, dict):
            analyzed = payload_summary.get("analyzed_message_count")
            if isinstance(analyzed, int) and analyzed > 0:
                analyzed_limit = analyzed
        effects = (
            _empty_message_scoped_repetition_effects(analyzed_limit)
            if message_scoped
            else _empty_repetition_effects(request.effect_days)
        )
        try:
            from memory.anti_repeat_effects import get_anti_repeat_effect_store

            effect_store = get_anti_repeat_effect_store()
            if message_scoped:
                queried_effects = await asyncio.to_thread(
                    effect_store.query_effects_for_responses,
                    character_name,
                    response_ids,
                    analyzed_limit,
                )
            else:
                queried_effects = await asyncio.to_thread(
                    effect_store.query_effects,
                    character_name,
                    request.effect_days,
                )
            if isinstance(queried_effects, dict):
                effects = queried_effects
            else:
                effects["query_failed"] = True
        except Exception as exc:
            effects["query_failed"] = True
            logger.warning(
                "Local anti-repeat effects unavailable for %s: %s",
                character_name,
                type(exc).__name__,
            )
        candidates = payload.get("candidates")
        patterns = effects.get("patterns")
        payload["effectiveness"] = effects
        payload["associations"] = _aggregate_repetition_associations(
            _associate_repetition_effects(
                candidates if isinstance(candidates, list) else [],
                patterns if isinstance(patterns, list) else [],
            )
        )
        return payload
    except Exception as exc:
        logger.warning(
            "Local repetition analysis unavailable for %s: %s",
            character_name,
            type(exc).__name__,
        )
        return JSONResponse(
            {"success": False, "error": "local memory analysis unavailable"},
            status_code=503,
        )


@router.post('/repetition_effects/reset')
async def reset_repetition_effects(request: RepetitionEffectsResetRequest):
    """Clear only local anti-repeat aggregates for one existing character."""
    validation = validate_character_name(request.character_name, allow_dots=True)
    if not validation.ok and validation.code != "reserved_route_name":
        return JSONResponse(
            {"success": False, "error": "invalid character name"},
            status_code=422,
        )
    character_name = validation.normalized
    try:
        from memory.anti_repeat_effects import get_anti_repeat_effect_store
        from utils.config_manager import get_config_manager

        config_manager = get_config_manager()
        characters = await config_manager.aload_characters()
        configured_characters = (
            characters.get("猫娘", {}) if isinstance(characters, dict) else {}
        )
        # The configured KEY, not the normalized request name: they differ
        # when a hand-edited characters.json carries padding, and reading
        # the normalized form there lands on an unrelated orphan directory.
        configured_key = _configured_character_key(
            configured_characters, character_name
        )
        if configured_key is None and not character_memory_exists(
            config_manager, character_name
        ):
            return JSONResponse(
                {"success": False, "error": "character not found"},
                status_code=404,
            )
        if configured_key is not None:
            character_name = configured_key
        await asyncio.to_thread(
            get_anti_repeat_effect_store().clear_effects,
            character_name,
        )
        logger.info("Cleared anti-repeat effects for character=%s", character_name)
        return {
            "success": True,
            "character_name": character_name,
            "cleared": True,
        }
    except Exception as exc:
        logger.warning(
            "Could not clear anti-repeat effects for %s: %s",
            character_name,
            type(exc).__name__,
        )
        return JSONResponse(
            {"success": False, "error": "local anti-repeat effects unavailable"},
            status_code=503,
        )

def _recent_browser_fingerprint(content: str) -> str:
    """Return the optimistic-concurrency token for one browser snapshot."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _recent_browser_identity_token(path: Path) -> str:
    """Return an opaque token binding an editor snapshot to one path identity."""
    key, generation = capture_recent_generation(path)
    material = f"{key}\0{generation}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _read_recent_browser_text_unlocked(path: Path, *, allow_missing: bool = False) -> str:
    """Build one editable disk-plus-pending snapshot while its lock is held."""
    try:
        content = read_recent_text_unlocked(path)
    except FileNotFoundError:
        if not allow_missing:
            raise
        content = '[]'
    pending = get_recent_pending_unlocked(path)
    if not pending:
        return content
    payload = json.loads(content)
    if not isinstance(payload, list):
        raise ValueError(f"recent history is not a list: {path}")
    if all(isinstance(message, dict) for message in pending):
        pending_payload = list(pending)
    else:
        from utils.llm_client import messages_to_dict

        pending_payload = messages_to_dict(pending)
    return json.dumps(payload + pending_payload, ensure_ascii=False, indent=2)


def _read_recent_browser_text(path: Path) -> str:
    """Read one editable disk-plus-pending snapshot under the recent lock."""
    with recent_file_access(path) as resolved_path:
        return _read_recent_browser_text_unlocked(resolved_path)


def _read_recent_browser_snapshot(path: Path, *, allow_missing: bool = False) -> tuple[str, str]:
    """Read editable content and its opaque identity while holding one lock."""
    with recent_file_access(path) as resolved_path:
        return (
            _read_recent_browser_text_unlocked(resolved_path, allow_missing=allow_missing),
            _recent_browser_identity_token(Path(resolved_path)),
        )


class _RecentBrowserTheaterEditRejected(Exception):
    """A browser save may carry an edited theater capsule as ordinary text."""


_RECENT_BROWSER_ROLE_TYPES = {"user": "human", "assistant": "ai", "system": "system"}


def _recent_payload_type_and_content(item: object) -> tuple[object, object]:
    """Return the ``(type, content)`` pair of one on-disk or role-style message."""
    if not isinstance(item, dict):
        return None, None
    data = item.get("data")
    if isinstance(data, dict):
        return item.get("type"), data.get("content")
    return (
        item.get("type") or _RECENT_BROWSER_ROLE_TYPES.get(item.get("role")),
        item.get("content"),
    )


def _merge_browser_payload_with_theater(
    current_text: str,
    payload: list[dict],
    browser_chat: list | None,
) -> list[dict]:
    """Carry theater messages over verbatim when the browser rewrites recent history.

    The browser posts only ``{role, text}`` (plus an optional ``source_index``
    pointing into the snapshot it loaded), which would strip the theater
    metadata and turn a capsule into ordinary memory. Every theater message of
    the locked snapshot is therefore copied back untouched at its original
    position relative to the surviving ordinary messages, and posted items that
    stand for a theater message are dropped. Without theater messages the
    payload is returned unchanged.
    """
    from memory.message_sources import is_theater_memory_message

    try:
        current = json.loads(current_text)
    except (TypeError, ValueError):
        return payload
    if not isinstance(current, list):
        return payload
    theater_indexes = [
        index
        for index, message in enumerate(current)
        if is_theater_memory_message(message)
    ]
    if not theater_indexes:
        return payload
    theater_index_set = set(theater_indexes)
    chat = browser_chat if isinstance(browser_chat, list) else []

    merged: list[dict] = []
    next_theater = 0
    cursor = 0
    has_unresolved_item = False
    matched_theater: set[int] = set()
    for position, item in enumerate(payload):
        chat_item = chat[position] if position < len(chat) else {}
        if not isinstance(chat_item, dict):
            chat_item = {}
        source = chat_item.get("source_index")
        if not (
            isinstance(source, int)
            and not isinstance(source, bool)
            and cursor <= source < len(current)
        ):
            # Legacy clients return only {role, text}: align forward by type and original text; edited entries will not match.
            source = None
            item_key = _recent_payload_type_and_content(item)
            for index in range(cursor, len(current)):
                if _recent_payload_type_and_content(current[index]) == item_key:
                    source = index
                    break
        if source is None:
            if chat_item.get("theater"):
                # Browser-labeled theater entries must never be saved as ordinary text; restore originals from snapshots.
                continue
            has_unresolved_item = True
            merged.append(item)
            continue
        cursor = source + 1
        while (
            next_theater < len(theater_indexes)
            and theater_indexes[next_theater] <= source
        ):
            merged.append(current[theater_indexes[next_theater]])
            next_theater += 1
        if source in theater_index_set:
            matched_theater.add(source)
            continue
        if chat_item.get("theater"):
            continue
        merged.append(item)
    merged.extend(current[index] for index in theater_indexes[next_theater:])
    if has_unresolved_item and len(matched_theater) < len(theater_indexes):
        # Unmatched theater capsules alongside entries differing from original text may be capsules edited by old pages;
        # writing them back turns theater fiction into everyday memory. Reject and require reload instead.
        raise _RecentBrowserTheaterEditRejected()
    return merged


def _write_recent_browser_payload(
    path: Path,
    payload: list[dict],
    *,
    expected_fingerprint: str | None,
    expected_identity_token: str | None,
    expected_generation: tuple[str, int],
    browser_chat: list | None = None,
    allow_missing: bool = False,
    before_write=None,
) -> tuple[bool, str, str]:
    """Replace a browser snapshot unless disk or pending state changed since read."""
    with recent_file_access(
        path, expected_generation=expected_generation,
    ) as resolved_path:
        current_identity_token = _recent_browser_identity_token(Path(resolved_path))
        current_text = _read_recent_browser_text_unlocked(resolved_path, allow_missing=allow_missing)
        current_fingerprint = _recent_browser_fingerprint(current_text)
        if (
            expected_identity_token is not None
            and expected_identity_token != current_identity_token
        ) or (
            expected_fingerprint is not None
            and expected_fingerprint != current_fingerprint
        ):
            return False, current_fingerprint, current_identity_token
        # Maintain theater capsules only through /cache and /theater/forget; browser saves preserve them unchanged.
        payload = _merge_browser_payload_with_theater(
            current_text, payload, browser_chat,
        )
        # Register exact deletion intent only after CAS and theater validation.
        # The callback commits local outboxes, never remote I/O. It runs before
        # JSON replacement so a crash cannot silently lose an approved deletion.
        if before_write is not None:
            before_write()
        write_recent_payload_unlocked(resolved_path, payload)
        set_recent_pending_unlocked(resolved_path, [])
        saved_text = json.dumps(payload, ensure_ascii=False, indent=2)
        return (
            True,
            _recent_browser_fingerprint(saved_text),
            current_identity_token,
        )


def _read_recent_browser_conflict_tokens(
    path: Path,
) -> tuple[str | None, str | None]:
    """Best-effort current tokens for a generation-race conflict response."""
    try:
        content, identity_token = _read_recent_browser_snapshot(path)
    except Exception:
        return None, None
    return _recent_browser_fingerprint(content), identity_token


def _recent_browser_conflict_response(
    fingerprint: str | None,
    identity_token: str | None,
) -> JSONResponse:
    """Return the browser editor's uniform optimistic-concurrency conflict."""
    return JSONResponse(
        {
            "success": False,
            "code": "RECENT_FILE_CONFLICT",
            "error": "近期记忆已在其他任务中更新，请重新加载并合并后再保存",
            "fingerprint": fingerprint,
            "identity_token": identity_token,
        },
        status_code=409,
    )


def _default_memory_dir_escapes_root(config_manager, name: str) -> bool:
    """Whether this character's DEFAULT directory resolves outside the root.

    A CONFIGURED character skips the existence check entirely, so the
    symlink filter in the selector never sees it -- configured names are
    added before directory enumeration. The read-only lookup then builds
    memory_dir/<name>/time_indexed.db and follows the link, and the panel
    renders and exports assistant-shaped rows from a database outside the
    memory root.

    The sidecar stores are not reachable this way because they already
    ask ``_is_within_memory_root``, which resolves both sides with
    realpath. The read-only time index does not, because it has to honour
    ``time_store`` -- where a character can be deliberately pointed
    elsewhere. So the same rule is asked here instead, and only about the
    DEFAULT path: an explicitly registered one is a choice, not a leak.

    MEMBERSHIP in ``time_store`` is not that choice. ``get_character_data``
    builds it as ``{name: memory_dir/name/time_indexed.db}`` for every
    configured character, so treating a present key as an override made
    this return False for exactly the case it exists to catch -- the
    check never ran outside its own test, which supplied an empty store.
    What counts is the path being DIFFERENT from the default one.

    Compared without resolving links, deliberately: realpath on a linked
    default path lands on the far end, which then reads as "somewhere
    else on purpose" and waves through precisely the case in hand.
    """
    try:
        time_store = config_manager.get_character_data()[6]
    except Exception:
        # Unreadable configuration is not evidence of a deliberate
        # override, so fall through to the containment check.
        time_store = {}
    registered = time_store.get(name) if isinstance(time_store, dict) else None
    if registered:
        default = os.path.join(
            str(config_manager.memory_dir), name, "time_indexed.db"
        )
        if os.path.normcase(os.path.abspath(str(registered))) != os.path.normcase(
            os.path.abspath(default)
        ):
            return False
    from memory import character_dir_is_within_memory_root

    try:
        if not character_dir_is_within_memory_root(
            config_manager.memory_dir, name
        ):
            return True
        # And the DATABASE under it. A real memory/<name>/ directory
        # passes containment while holding "time_indexed.db -> /outside",
        # and the read-only path follows a file link exactly as it would
        # a directory one. Resolved rather than islink-tested, so an
        # intermediate link is caught the same way.
        character_dir = os.path.join(str(config_manager.memory_dir), name)
        database = os.path.join(character_dir, "time_indexed.db")
        return os.path.normcase(
            os.path.dirname(os.path.realpath(database))
        ) != os.path.normcase(os.path.realpath(character_dir))
    except Exception:
        # Cannot resolve it, so cannot vouch for it. Refusing costs a
        # panel; waving it through is the direction that leaks, and this
        # module treats that as the only unacceptable one.
        return True


def _configured_character_key(configured, character_name: str) -> str | None:
    """The characters.json key this request name identifies, if any.

    Both routes normalize what they are asked for, and the panel offers
    what ``_insight_selectable_name`` returns, which is normalized too --
    the frontend trims it again before posting. characters.json keys are
    NOT normalized: nothing in this repo writes a padded one, but nothing
    rejects a hand-edited config either. Such a key was offered as its
    trimmed form and then failed the raw membership test, 404ing on a name
    the panel had just listed -- the drift the selector docstring says
    cannot happen.

    Worse than the 404: an unrelated memory/<trimmed>/ left over from a
    delete satisfied the existence arm instead, so the panel read that
    orphan and the reset button cleared ITS aggregates rather than the
    configured character's.

    Exactly inverts the offering rule, so the two cannot drift again, and
    an exact key always wins -- with both "Bob" and " Bob" configured, the
    request for "Bob" means "Bob".
    """
    if character_name in configured:
        return character_name
    for key in configured:
        if not isinstance(key, str) or not key:
            continue
        if _insight_selectable_name(key) == character_name:
            return key
    return None


def _insight_selectable_name(name: str) -> str | None:
    """Return the name the analysis route would accept, or None.

    The selector and the route have to share one admission rule. Building
    the list from any non-empty configured string offered names the route
    then rejected with 422 -- a historical unsafe name such as "." is a
    supported state that the delete route deliberately keeps a rescue path
    for, so it really can still be in characters.json. The reserved-route
    exception is intentional and shared with the route.
    """
    validation = validate_character_name(
        name, allow_dots=True, max_units=PROFILE_NAME_MAX_UNITS
    )
    if not validation.ok and validation.code != "reserved_route_name":
        return None
    return validation.normalized or None


@router.get('/insight_characters')
async def get_insight_characters():
    """List the identities the repetition-insights route will accept.

    The panel selector used to be built from the recent-memory file list,
    which only knows characters that have a ``recent.json``. A configured
    character, or one restored from a cloud snapshot carrying time-indexed
    history without the optional recent file, was therefore missing from the
    panel even though the analysis route supports it.

    Reuses that route's own admission rule -- configured OR has character
    memory on disk -- so the two cannot drift into offering a name the route
    rejects, or hiding one it accepts.
    """
    from utils.config_manager import get_config_manager

    config_manager = get_config_manager()
    characters = await config_manager.aload_characters()
    configured = (
        characters.get("猫娘", {}) if isinstance(characters, dict) else {}
    )
    names = {
        selectable
        for name in configured
        if isinstance(name, str) and name
        for selectable in (_insight_selectable_name(name),)
        if selectable
    }

    # Raw, not selectable: this is compared against directory names on disk,
    # which carry the character name as configured.
    configured_names = {
        name for name in configured if isinstance(name, str) and name
    }

    # Enumerate through the same roots the predicate reads, so a root added
    # there cannot silently become invisible here.
    candidates: set[str] = set()
    for base_dir in iter_character_memory_roots(config_manager):
        if not base_dir.exists():
            continue
        for child in base_dir.iterdir():
            # A REAL directory. ``Path.is_dir()`` follows links, so a
            # symlink in the memory root was offered as a character and
            # ``character_memory_exists`` confirmed it -- the panel would
            # then read, render and export assistant-shaped rows from
            # whatever database the link points at, outside the memory root
            # entirely. Measured with a link to a sibling directory.
            #
            # Fixed HERE rather than in the shared reader on purpose:
            # ``_resolve_expected_db_path`` honours ``time_store``, which
            # exists so a character CAN register a database outside
            # memory_dir, and a blanket containment check there would break
            # that. What is new on this branch is enumerating the root and
            # offering what it finds, so that is what learns to be careful.
            #
            # And a REAL character directory, not merely a namesake. A legacy
            # "semantic_memory_Alice/" vector store is one of the paths
            # ``character_memory_exists`` checks for a character of that
            # name, so it confirms itself: the selector offered
            # "semantic_memory_Alice", and analysing it read
            # memory/semantic_memory_Alice/time_indexed.db rather than
            # Alice's, reporting no history for a character that has plenty.
            #
            # Configured characters keep their own path below and are not
            # subject to this -- an empty configured character is still hers.
            #
            # And not another character's vector store. A legacy
            # "semantic_memory_Alice/" is one of the paths
            # ``character_memory_exists`` checks for a character of that
            # name, so it confirms itself: the selector offered
            # "semantic_memory_Alice", and analysing it read that vector
            # store rather than Alice's history. Only the ENUMERATING side
            # can tell the difference, because only it can see the owner.
            if (
                child.is_dir()
                and not child.is_symlink()
                and not is_legacy_vector_store_dir(base_dir, child.name)
            ):
                candidates.add(child.name)
            # No legacy decoding here. The flat layout was retired in
            # 2026-03 together with the startup migration that replaces it,
            # and that migration now covers unconfigured owners as well --
            # so by the time this runs, a legacy root file has already
            # become memory/<name>/ and the directory branch above sees it.
            # Decoding here made the READ path carry a second layout, and
            # got it wrong: "time_indexed_Carol.db-wal" decoded to a
            # character named "Carol.db-wal", which the existence check then
            # confirmed, so the panel offered it.
        for logical_name in iter_recent_memory_files(base_dir):
            candidate = extract_catgirl_name_from_recent_filename(logical_name)
            if candidate:
                candidates.add(candidate)

    for candidate in candidates - names:
        selectable = _insight_selectable_name(candidate)
        if selectable and character_memory_exists(config_manager, selectable):
            names.add(selectable)

    return {"characters": sorted(names)}


@router.get('/recent_files')
async def get_recent_files():
    """List all recent*.json filenames under the memory directory."""
    from utils.config_manager import get_config_manager
    cm = get_config_manager()
    file_names: list[str] = []
    seen: set[str] = set()

    for base_dir in (Path(cm.memory_dir), Path(cm.project_memory_dir)):
        for logical_name in iter_recent_memory_files(base_dir):
            if logical_name in seen:
                continue
            seen.add(logical_name)
            file_names.append(logical_name)

    # A role with phone-only history may not have a recent-memory file yet.
    # Listing its logical editor target never writes memory or binds a role.
    loader = getattr(cm, 'aload_characters', None)
    if loader is not None:
        from utils.config_manager.reserved_schema import get_character_uid
        characters = await loader()
        for name, profile in characters.get('猫娘', {}).items():
            logical_name = f'recent_{name}.json'
            if (isinstance(profile, dict) and get_character_uid(profile)
                    and validate_recent_filename(logical_name)[0] and logical_name not in seen):
                seen.add(logical_name)
                file_names.append(logical_name)

    return {"files": sorted(file_names)}


async def _memory_chat_uid(cm, name: str) -> str | None:
    """Resolve saved role identity; never associate raw chats by display text."""
    from utils.config_manager.reserved_schema import get_character_uid
    loader = getattr(cm, 'aload_characters', None)
    if loader is None:
        return None
    data = await loader()
    profile = data.get('猫娘', {}).get(name)
    return get_character_uid(profile) if isinstance(profile, dict) else None


def _memory_chat_binding(binding) -> dict | None:
    """Fence an editor against account, binding and generation changes."""
    if binding is None:
        return None
    return {key: binding[key] for key in ('owner_id', 'binding_id', 'generation')}


async def _memory_chat_snapshot(cm, uid: str) -> dict:
    from services.chat_sync.runtime import get_desktop_chat_sync_runtime
    runtime = await get_desktop_chat_sync_runtime(cm)
    binding = _memory_chat_binding(await asyncio.to_thread(runtime.state.binding, uid))
    originals = await runtime.history(uid)
    local_replies = await runtime.local_replies(uid)
    if binding != _memory_chat_binding(await asyncio.to_thread(runtime.state.binding, uid)):
        raise HTTPException(409, 'chat_sync_binding_changed')
    return {'characterSyncId': uid, 'binding': binding,
            'originals': originals, 'localReplies': local_replies}


@router.get('/recent_file')
async def get_recent_file(filename: str, request: Request = None, include_chat_sync: bool = False):
    """Get the content of the specified recent*.json file."""
    # Reject path traversal attempts
    if '/' in filename or '\\' in filename or '..' in filename:
        return JSONResponse({"success": False, "error": "文件名不能包含路径分隔符或目录遍历字符"}, status_code=400)
    
    if not (filename.startswith('recent') and filename.endswith('.json')):
        return JSONResponse({"success": False, "error": "文件名不合法"}, status_code=400)
    
    from utils.config_manager import get_config_manager
    cm = get_config_manager()

    uid = None
    if include_chat_sync:
        from main_routers.chat_sync_router import _local_access
        await _local_access(request, Response())
        uid = await _memory_chat_uid(cm, extract_catgirl_name_from_recent_filename(filename))

    resolved_path, path_error, path_error_code, catgirl_name = resolve_recent_file_path(cm, filename)
    if resolved_path is None and uid and path_error_code == PATH_ERROR_NOT_FOUND:
        resolved_path = Path(cm.memory_dir) / catgirl_name / 'recent.json'
    if resolved_path is None:
        status_code = path_error_status_code(path_error_code)
        return JSONResponse({"success": False, "error": path_error}, status_code=status_code)
    
    # Offload synchronous reads: recent.json can reach several megabytes.
    # Hold the file lock: on Windows even a bare open() can make concurrent os.replace raise PermissionError.
    try:
        content, identity_token = await asyncio.to_thread(
            _read_recent_browser_snapshot, resolved_path, **({'allow_missing': True} if uid else {}),
        )
    except RecentFileDeletedError:
        return JSONResponse(
            {"success": False, "error": "文件不存在"},
            status_code=path_error_status_code(PATH_ERROR_NOT_FOUND),
        )
    result = {
        "content": content,
        "fingerprint": _recent_browser_fingerprint(content),
        "identity_token": identity_token,
    }
    if include_chat_sync:
        result['chat_sync'] = await _memory_chat_snapshot(cm, uid) if uid else None
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})
    return result


@router.post('/recent_file/save')
async def save_recent_file(request: Request):
    data = await request.json()
    filename = data.get('filename')
    chat = data.get('chat')
    snapshot_fingerprint = data.get('fingerprint')
    snapshot_identity_token = data.get('identity_token')
    
    # Validate filename
    is_valid, error_msg = validate_recent_filename(filename)
    if not is_valid:
        logger.warning(f"Invalid filename rejected: {filename!r} - {error_msg}")
        return JSONResponse({"success": False, "error": error_msg}, status_code=400)
    
    # Validate chat payload
    is_valid, error_msg = validate_chat_payload(chat)
    if not is_valid:
        logger.warning(f"Invalid chat payload rejected: {error_msg}")
        return JSONResponse({"success": False, "error": error_msg}, status_code=400)
    if snapshot_fingerprint is not None and not isinstance(snapshot_fingerprint, str):
        return JSONResponse(
            {"success": False, "error": "文件快照指纹格式不合法"},
            status_code=400,
        )
    if snapshot_identity_token is not None and not isinstance(snapshot_identity_token, str):
        return JSONResponse(
            {"success": False, "error": "文件身份令牌格式不合法"},
            status_code=400,
        )
    if snapshot_fingerprint is None or snapshot_identity_token is None:
        return JSONResponse(
            {"success": False, "error": "文件身份令牌缺失，请重新加载后再保存"},
            status_code=409,
        )
    
    from utils.config_manager import get_config_manager
    cm = get_config_manager()
    catgirl_name = extract_catgirl_name_from_recent_filename(filename)
    if catgirl_name is None:
        logger.warning(f"Failed to extract catgirl name from filename: {filename!r}")
        return JSONResponse({"success": False, "error": "文件名不合法"}, status_code=400)

    sync_edit = data.get('chat_sync')
    runtime = None
    if sync_edit is not None:
        from main_routers.chat_sync_router import _local_access
        from services.chat_sync.ledger import _message_ids
        from services.chat_sync.runtime import get_desktop_chat_sync_runtime
        await _local_access(request, Response())
        try:
            if (not isinstance(sync_edit, dict)
                    or set(sync_edit) != {'characterSyncId', 'binding', 'messageIds', 'localMessageIds'}):
                raise ValueError('chat_sync_invalid_editor_save')
            _message_ids(sync_edit['messageIds'], allow_empty=True)
            _message_ids(sync_edit['localMessageIds'], allow_empty=True)
        except (ValueError, TypeError):
            raise HTTPException(400, 'chat_sync_invalid_editor_save') from None
        uid = await _memory_chat_uid(cm, catgirl_name)
        if not uid or uid != sync_edit['characterSyncId']:
            raise HTTPException(409, 'chat_sync_binding_changed')
        runtime = await get_desktop_chat_sync_runtime(cm)

    # Save into the same layout reads resolve; never silently move legacy flat/project files into nonexistent
    # runtime nested paths, or CAS comparisons lose their target.
    resolved_path, _path_error, path_error_code, _ = resolve_recent_file_path(
        cm, filename,
    )
    if resolved_path is None:
        if path_error_code != PATH_ERROR_NOT_FOUND:
            return JSONResponse(
                {"success": False, "error": _path_error},
                status_code=path_error_status_code(path_error_code),
            )
        resolved_path = Path(cm.memory_dir) / catgirl_name / 'recent.json'
    admission_generation = capture_recent_generation(resolved_path)
    assert_cloudsave_writable(
        cm,
        operation="save",
        target=f"memory/{catgirl_name}/recent.json",
    )

    arr = []
    for msg in chat:
        t = msg.get('role')
        text = msg.get('text', '')
        arr.append({
            "type": t,
            "data": {
                "content": text,
                "additional_kwargs": {},
                "response_metadata": {},
                "type": t,
                "name": None,
                "id": None,
                "example": False,
                **({"tool_calls": [], "invalid_tool_calls": [], "usage_metadata": None} if t == "ai" else {})
            }
        })
    deletion_receipt = None
    loop = asyncio.get_running_loop()

    async def _register_chat_deletions():
        nonlocal deletion_receipt
        from services.chat_sync.source import clear_local_replies
        from services.chat_sync.profile_sync import DesktopProfileAdapter
        if (sync_edit['messageIds'] or sync_edit['localMessageIds']) and DesktopProfileAdapter(cm).busy(catgirl_name):
            raise HTTPException(409, 'chat_sync_reply_in_progress')
        # Recheck inside the locked save, after the recent-file CAS succeeded.
        if (uid != await _memory_chat_uid(cm, catgirl_name)
                or sync_edit['binding'] != _memory_chat_binding(await asyncio.to_thread(runtime.state.binding, uid))):
            raise HTTPException(409, 'chat_sync_binding_changed')
        ids = sync_edit['messageIds']
        try:
            deletion_receipt = (await runtime.delete(uid, ids, expected_binding=sync_edit['binding']) if ids else
                                {'messageIds': [], 'deletionIds': [], 'pending': False})
        except ValueError as exc:
            if str(exc) == 'chat_sync_binding_changed':
                raise HTTPException(409, 'chat_sync_binding_changed') from None
            raise
        deletion_receipt['localMessageIds'] = []
        deletion_receipt['localMessageIds'] = await clear_local_replies(runtime, uid, sync_edit['localMessageIds'])
        if ids:
            runtime.start_deletion_flush(uid)

    def _before_write():
        # The event loop remains free while the recent-file worker owns its lock.
        # Reuse the normal durable deletion/outage pipeline; never infer IDs from
        # edited memory, source indexes, text, or timestamps. Tool reply deletion
        # stays local until a future explicit tool-sync protocol is approved.
        asyncio.run_coroutine_threadsafe(_register_chat_deletions(), loop).result()

    async def _commit_browser_save():
        try:
            # The locked file worker must not occupy the pool used by its
            # async deletion callback; even a one-worker default pool can finish.
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="memory-save") as save_executor:
                saved, saved_fingerprint, saved_identity_token = await loop.run_in_executor(
                    save_executor, partial(
                        _write_recent_browser_payload,
                        resolved_path,
                        arr,
                        expected_fingerprint=snapshot_fingerprint,
                        expected_identity_token=snapshot_identity_token,
                        expected_generation=admission_generation,
                        browser_chat=chat,
                        allow_missing=runtime is not None,
                        before_write=_before_write if runtime is not None else None,
                    ),
                )

        except _RecentBrowserTheaterEditRejected:
            return JSONResponse(
                {
                    "success": False,
                    "code": "RECENT_FILE_THEATER_READONLY",
                    "error": "小剧场记忆不能在记忆浏览器中编辑，请重新加载后再保存",
                },
                status_code=409,
            )
        except RecentFileDeletedError:
            saved_fingerprint, saved_identity_token = await asyncio.to_thread(
                _read_recent_browser_conflict_tokens,
                resolved_path,
            )
            return _recent_browser_conflict_response(
                saved_fingerprint,
                saved_identity_token,
            )
        if not saved:
            return _recent_browser_conflict_response(
                saved_fingerprint,
                saved_identity_token,
            )
        
        if catgirl_name:
            # Interrupt memory_server review tasks.
            import httpx
            from config import MEMORY_SERVER_PORT
            # Per-call AsyncClient: cold path triggered by manually saving recent conversations.
            try:
                async with httpx.AsyncClient(proxy=None, trust_env=False) as client:
                    await client.post(
                        f"http://127.0.0.1:{MEMORY_SERVER_PORT}/cancel_correction/{catgirl_name}",
                        timeout=2.0
                    )
                    logger.info(f"已发送取消 {catgirl_name} 记忆整理任务的请求")
            except Exception as e:
                logger.warning(f"Failed to cancel correction task: {e}")
        
        # Return success with a context-refresh hint.
        result = {
            "success": True,
            "need_refresh": True,
            "catgirl_name": catgirl_name,
            "fingerprint": saved_fingerprint,
            "identity_token": saved_identity_token,
        }
        if deletion_receipt is not None:
            result['chat_deletion'] = deletion_receipt
            result['characterSyncId'] = uid
        return result

    try:
        result, save_cancelled = await _await_browser_save_transaction(
            _commit_browser_save()
        )
        if save_cancelled:
            raise asyncio.CancelledError
        return result
    except MaintenanceModeError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to save recent file: {e}")
        if deletion_receipt is not None:
            # The outbox is durable even if JSON replacement fails. Keep this
            # partial outcome visible; retries of the same exact IDs are safe.
            return JSONResponse({'success': False, 'code': 'CHAT_DELETION_SAVED_MEMORY_FAILED',
                                 'chat_deletion': deletion_receipt, 'characterSyncId': uid}, status_code=503)
        return {"success": False, "error": str(e)}


@router.post('/update_catgirl_name')
async def update_catgirl_name(request: Request):
    """
    Update the catgirl name in memory files.
    1. Rename the memory files
    2. Update name references inside the file contents
    """
    data = await request.json()
    old_name = data.get('old_name')
    new_name = data.get('new_name')
    
    if not old_name or not new_name:
        return JSONResponse({"success": False, "error": "缺少必要参数"}, status_code=400)
    
    # Validate old_name (allow dots for historical names during migration)
    is_valid, error_msg = validate_catgirl_name(old_name, allow_dots=True, reject_reserved_route=False)
    if not is_valid:
        logger.warning(f"Invalid old_name rejected: {old_name!r} - {error_msg}")
        return JSONResponse({"success": False, "error": f"旧名称无效: {error_msg}"}, status_code=400)

    # Validate new_name (strict — no dots allowed)
    is_valid, error_msg = validate_catgirl_name(new_name, reject_reserved_route=True)
    if not is_valid:
        logger.warning(f"Invalid new_name rejected: {new_name!r} - {error_msg}")
        return JSONResponse({"success": False, "error": f"新名称无效: {error_msg}"}, status_code=400)
    
    try:
        from utils.config_manager import get_config_manager
        cm = get_config_manager()
        characters = await cm.aload_characters()
        catgirls = characters.get('猫娘', {}) if isinstance(characters, dict) else {}

        # Idempotent compatibility for old clients calling this endpoint after canonical rename succeeds.
        if old_name not in catgirls and new_name in catgirls:
            if character_memory_exists(cm, old_name):
                return JSONResponse(
                    {
                        "success": False,
                        "error": "角色配置已改名但旧记忆仍存在，请通过角色管理接口修复",
                    },
                    status_code=409,
                )
            return {
                "success": True,
                "changed": False,
                "exists_after": character_memory_exists(cm, new_name),
                "already_renamed": True,
            }

        # Moving memory separately bypasses rename-transaction task draining, configuration publication, and rollback.
        # Delegate to the canonical route so old derived tasks cannot follow recent redirects into a new character.
        from .characters_router.crud import rename_catgirl

        return await rename_catgirl(old_name, request)
    except MaintenanceModeError:
        raise
    except Exception as exc:
        logger.exception("更新猫娘名称失败")
        return {"success": False, "error": str(exc)}


class _InvalidMemoryToggleValue(ValueError):
    """A readable configuration contains a setting that can be explicitly repaired."""


def _load_memory_toggle_config(config_manager):
    """Default a missing file to an empty object without hiding read errors."""
    from utils.config_manager.json_update import load_json_config_snapshot
    return load_json_config_snapshot(config_manager, 'core_config.json')


def _load_memory_toggle_enabled(config_manager, key):
    """Default missing settings to on, but reject invalid stored values."""
    config_data = _load_memory_toggle_config(config_manager)
    enabled = config_data.get(key, True)
    if not isinstance(enabled, bool):
        raise _InvalidMemoryToggleValue('Invalid memory setting value')
    return enabled


@router.get('/review_config')
async def get_review_config():
    """Get the memory review configuration."""
    try:
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        async with _memory_toggle_write_lock:
            enabled = await asyncio.to_thread(
                _load_memory_toggle_enabled, config_manager, 'recent_memory_auto_review'
            )
        return {"enabled": enabled}
    except Exception as e:
        logger.error(f"读取记忆整理配置失败: {e}")
        error = {"error": "Failed to read memory review configuration"}
        if isinstance(e, _InvalidMemoryToggleValue):
            error["code"] = "invalid_memory_setting"
        return JSONResponse(error, status_code=503)


@router.post('/review_config')
async def update_review_config(request: Request):
    """Update the memory review configuration."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"success": False, "error": "Request body must be JSON"}, status_code=400)
    if not isinstance(data, dict) or not isinstance(data.get('enabled'), bool):
        return JSONResponse({"success": False, "error": "enabled must be a boolean"}, status_code=400)
    enabled = data['enabled']

    try:
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        async with _memory_toggle_write_lock:
            def _set_review(config_data):
                config_data['recent_memory_auto_review'] = enabled

            await config_manager.aupdate_json_config('core_config.json', _set_review)

            logger.info(f"记忆整理配置已更新: enabled={enabled}")
            return {"success": True, "enabled": enabled}
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.error(f"更新记忆整理配置失败: {e}")
        return {"success": False, "error": str(e)}


@router.get('/powerful_memory_config')
async def get_powerful_memory_config():
    """Get the powerful-memory toggle. Defaults to True (for backward compatibility with existing users)."""
    try:
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        async with _memory_toggle_write_lock:
            enabled = await asyncio.to_thread(
                _load_memory_toggle_enabled, config_manager, 'powerful_memory_enabled'
            )
        return {"enabled": enabled}
    except Exception as e:
        logger.error(f"读取强力记忆配置失败: {e}")
        error = {"error": "Failed to read powerful memory configuration"}
        if isinstance(e, _InvalidMemoryToggleValue):
            error["code"] = "invalid_memory_setting"
        return JSONResponse(error, status_code=503)


@router.post('/powerful_memory_config')
async def update_powerful_memory_config(request: Request):
    """Update the powerful-memory toggle.

    Turning it off stops all new LLM paths introduced by the evidence RFC
    (Stage-2 / promote_merge / rebuttal / negative-keyword / fact_dedup /
    persona corrections), keeping check_feedback for proactive-chat responses
    as the only evidence channel. When switching on→off, reset confirmed_at of
    all confirmed reflections to now to avoid an immediate bulk promote.
    """
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"success": False, "error": "Request body must be JSON"}, status_code=400)
    if not isinstance(data, dict) or not isinstance(data.get('enabled'), bool):
        return JSONResponse({"success": False, "error": "enabled must be a boolean"}, status_code=400)
    enabled = data['enabled']

    try:
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        async with _memory_toggle_write_lock:
            config_data = await asyncio.to_thread(
                _load_memory_toggle_config, config_manager
            )

            prev_enabled = config_data.get('powerful_memory_enabled', True)

            # ON -> OFF: migrate first, resetting every character's confirmed-reflection confirmed_at
            # to now so time-driven fallback gets a full 14-day countdown;
            # save configuration only after success. Otherwise failed migration leaves configuration
            # False, and later disable clicks never enter prev_enabled and not enabled again,
            # permanently missing old confirmed_at anchors; old confirmed reflections may immediately
            # promote through time-driven fallback. Require atomic success or failure of both.
            # Call memory_server via HTTP: this router lives in main_server; directly importing
            # memory_server gets a fresh copy with reflection_engine None, making migration
            # silently noop. memory_server runs separately at MEMORY_SERVER_PORT,
            # where startup hooks initialize reflection_engine.
            # Only a confirmed false may skip migration; legacy values are unknown.
            if prev_enabled is not False and not enabled:
                try:
                    from config import MEMORY_SERVER_PORT
                    from utils.internal_http_client import get_internal_http_client
                    client = get_internal_http_client()
                    resp = await client.post(
                        f"http://127.0.0.1:{MEMORY_SERVER_PORT}/internal/memory/reset_confirmed_at",
                        timeout=10.0,
                    )
                    if resp.status_code != 200:
                        logger.warning(
                            f"强力记忆切换 migration HTTP 状态码 {resp.status_code}，配置未保存"
                        )
                        return {
                            "success": False,
                            "error": f"migration HTTP {resp.status_code}",
                        }
                    payload = resp.json()
                    if not isinstance(payload, dict) or not payload.get('ok'):
                        err = payload.get('error', 'migration returned ok=false') if isinstance(payload, dict) else 'migration payload invalid'
                        logger.warning(f"强力记忆切换 migration 失败，配置未保存: {err}")
                        return {"success": False, "error": err}
                    migrated = int(payload.get('count', 0))
                    logger.info(
                        f"强力记忆切换 ON→OFF：已重置 {migrated} 条 confirmed "
                        f"reflection 的 confirmed_at 锚点"
                    )
                except Exception as e:
                    logger.warning(f"强力记忆切换 migration 异常，配置未保存: {e}")
                    return {"success": False, "error": str(e)}

            # Persist configuration only after successful migration or a non-ON->OFF change, keeping
            # frontend toggle state consistent with actual reflection_engine state.
            # config_data above determines prev_enabled only. Reread the file after migration before
            # writing, preserving fields others saved during the migration seconds.
            # Only this route writes the key and _memory_toggle_write_lock serializes it,
            # so prev_enabled remains valid at persistence time.
            def _set_powerful(fresh_config):
                fresh_config['powerful_memory_enabled'] = enabled

            await config_manager.aupdate_json_config('core_config.json', _set_powerful)

            logger.info(f"强力记忆配置已更新: enabled={enabled} (prev={prev_enabled})")
            return {"success": True, "enabled": enabled}
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.error(f"更新强力记忆配置失败: {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------
# Legacy memory scanning / manual cleanup for the frontend's clean-legacy-memory button.
# ---------------------------------------------------------------
#
# List roots outside current runtime memory_dir that may hold historical character memories
# (Documents / former CFA fallback path / historical readable Documents candidates), letting
# users explicitly select cleanup. Never delete automatically: POST /legacy/purge must
# supply explicit paths under scan's legacy_roots[].root whitelist
# to prevent path escape.


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


def _decode_external_archive(raw: object) -> bytes | None:
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise ExternalMemoryImportError("archive_b64 must be a base64 string")
    value = raw.strip()
    if value.startswith("data:") and "," in value:
        value = value.split(",", 1)[1]
    max_base64_chars = 4 * ((MAX_TOTAL_BYTES + 2) // 3)
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
    if not validation.ok:
        raise ExternalMemoryImportError("Invalid target character name")
    archive_bytes = _decode_external_archive(payload.get("archive_b64"))
    direct_files = payload.get("files")
    if archive_bytes is not None and direct_files:
        raise ExternalMemoryImportError("Choose either a ZIP archive or Markdown files, not both")
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
        character_name, analysis = await asyncio.to_thread(_prepare_external_import, payload)
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
        return {
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
        character_name, analysis = await asyncio.to_thread(_prepare_external_import, payload)
        if analysis["warnings"] and payload.get("acknowledge_warnings") is not True:
            return _external_import_error(
                "Suspicious instruction patterns were detected; preview and acknowledge warnings before import",
                409,
            )
        from config import MEMORY_SERVER_PORT
        from utils.config_manager import get_config_manager
        from utils.internal_http_client import get_internal_http_client

        assert_cloudsave_writable(
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
            # Persona import now runs synchronous LLM fusion per entity, potentially tens of seconds each;
            # 30s is insufficient. Allow 240s for master+neko; frontend commit timeout
            # (memory_browser.js, 270s) stays slightly higher so backend responses arrive before frontend disconnect.
            timeout=240.0,
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
    except Exception as exc:
        logger.exception("External memory import failed")
        return _external_import_error(f"External memory import failed: {exc}", 500)


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
