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
``.agent/rules/neko-guide.md`` (§"API URL 末尾不带斜杠") for the rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""

import asyncio
import base64
import binascii
import hashlib
import os
import re
import json
from contextlib import suppress
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Request
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
# merged 单进程（发行版默认）下，本模块与 memory_server 的写者同处一个进程，
# 共用 utils.recent_file 的 per-path 锁；裸 atomic_write_json_async 会绕过它。
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


# 单条消息文本上限(字符)。现场触发的内存尖峰复盘:用户从外部
# 复制一坨长文本粘贴进 recent → 整段以单条 message 形式落盘 → 后续
# memory pipeline 把这条当成「stale entry」喂给 embedder → batch 内
# pad-to-longest 把激活内存顶到多 GB(虽然 embedder 侧已加 token 预算
# 兜底,这里仍在边界堵住「单条 megablob」的入口,避免把异常大对象
# 漫到 ndjson / db / recall 等所有下游)。32K 字符 ≈ 32K token(中文)
# 对正常对话足够宽松(单条 5K 中文 = 一篇较长文章),又把 worst-case
# 入站体积钉住。
_RECENT_MESSAGE_TEXT_MAX_CHARS = 32 * 1024
# 整个 chat payload 的累计文本上限。控制「一次粘贴 1000 条 30K 文本」
# 这种总量攻击/误操作。2 MB 对真实长会话仍宽裕,异常体积会被打回。
_RECENT_CHAT_TOTAL_CHARS_MAX = 2 * 1024 * 1024
# 消息条数上限。冗余防御:即使每条都很短,几十万条也能把后续
# scan/embed/render 全拖死。
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


def _read_recent_browser_text_unlocked(path: Path) -> str:
    """Build one editable disk-plus-pending snapshot while its lock is held."""
    content = read_recent_text_unlocked(path)
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


def _read_recent_browser_snapshot(path: Path) -> tuple[str, str]:
    """Read editable content and its opaque identity while holding one lock."""
    with recent_file_access(path) as resolved_path:
        return (
            _read_recent_browser_text_unlocked(resolved_path),
            _recent_browser_identity_token(Path(resolved_path)),
        )


def _write_recent_browser_payload(
    path: Path,
    payload: list[dict],
    *,
    expected_fingerprint: str | None,
    expected_identity_token: str | None,
    expected_generation: tuple[str, int],
) -> tuple[bool, str, str]:
    """Replace a browser snapshot unless disk or pending state changed since read."""
    with recent_file_access(
        path, expected_generation=expected_generation,
    ) as resolved_path:
        current_identity_token = _recent_browser_identity_token(Path(resolved_path))
        current_text = _read_recent_browser_text_unlocked(resolved_path)
        current_fingerprint = _recent_browser_fingerprint(current_text)
        if (
            expected_identity_token is not None
            and expected_identity_token != current_identity_token
        ) or (
            expected_fingerprint is not None
            and expected_fingerprint != current_fingerprint
        ):
            return False, current_fingerprint, current_identity_token
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

    return {"files": sorted(file_names)}


@router.get('/recent_file')
async def get_recent_file(filename: str):
    """Get the content of the specified recent*.json file."""
    # Reject path traversal attempts
    if '/' in filename or '\\' in filename or '..' in filename:
        return JSONResponse({"success": False, "error": "文件名不能包含路径分隔符或目录遍历字符"}, status_code=400)
    
    if not (filename.startswith('recent') and filename.endswith('.json')):
        return JSONResponse({"success": False, "error": "文件名不合法"}, status_code=400)
    
    from utils.config_manager import get_config_manager
    cm = get_config_manager()

    resolved_path, path_error, path_error_code, _catgirl_name = resolve_recent_file_path(cm, filename)
    if resolved_path is None:
        status_code = path_error_status_code(path_error_code)
        return JSONResponse({"success": False, "error": path_error}, status_code=status_code)
    
    # offload 同步 read 到线程池：recent.json 单文件可达数 MB。
    # 走文件锁：Windows 上一个裸 open() 就能让并发的 os.replace 抛 PermissionError。
    try:
        content, identity_token = await asyncio.to_thread(
            _read_recent_browser_snapshot, resolved_path,
        )
    except RecentFileDeletedError:
        return JSONResponse(
            {"success": False, "error": "文件不存在"},
            status_code=path_error_status_code(PATH_ERROR_NOT_FOUND),
        )
    return {
        "content": content,
        "fingerprint": _recent_browser_fingerprint(content),
        "identity_token": identity_token,
    }


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

    # 保存到读取时会解析到的同一布局；旧版 flat/project 文件不能被悄悄
    # 改写到一个尚不存在的 runtime nested 路径，否则 CAS 比较失去对象。
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
    async def _commit_browser_save():
        try:
            saved, saved_fingerprint, saved_identity_token = await asyncio.to_thread(
                _write_recent_browser_payload,
                resolved_path,
                arr,
                expected_fingerprint=snapshot_fingerprint,
                expected_identity_token=snapshot_identity_token,
                expected_generation=admission_generation,
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
            # 中断 memory_server 的 review 任务
            import httpx
            from config import MEMORY_SERVER_PORT
            # per-call AsyncClient: 用户手动保存最近对话触发，冷路径
            try:
                async with httpx.AsyncClient(proxy=None, trust_env=False) as client:
                    await client.post(
                        f"http://127.0.0.1:{MEMORY_SERVER_PORT}/cancel_correction/{catgirl_name}",
                        timeout=2.0
                    )
                    logger.info(f"已发送取消 {catgirl_name} 记忆整理任务的请求")
            except Exception as e:
                logger.warning(f"Failed to cancel correction task: {e}")
        
        # 返回成功并提示需要刷新上下文
        return {
            "success": True,
            "need_refresh": True,
            "catgirl_name": catgirl_name,
            "fingerprint": saved_fingerprint,
            "identity_token": saved_identity_token,
        }

    try:
        result, save_cancelled = await _await_browser_save_transaction(
            _commit_browser_save()
        )
        if save_cancelled:
            raise asyncio.CancelledError
        return result
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.error(f"Failed to save recent file: {e}")
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

        # 兼容旧客户端在 canonical rename 成功后重复调用本端点的幂等路径。
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

        # 单独移动 memory 会绕过角色改名事务的 task drain、配置发布和回滚。
        # 统一委托 canonical route，避免旧派生任务沿 recent redirect 写进新角色。
        from .characters_router.crud import rename_catgirl

        return await rename_catgirl(old_name, request)
    except MaintenanceModeError:
        raise
    except Exception as exc:
        logger.exception("更新猫娘名称失败")
        return {"success": False, "error": str(exc)}


@router.get('/review_config')
async def get_review_config():
    """Get the memory review configuration."""
    try:
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        config_data = await asyncio.to_thread(
            config_manager.load_json_config, 'core_config.json', default_value={}
        )
        return {"enabled": config_data.get('recent_memory_auto_review', True)}
    except Exception as e:
        logger.error(f"读取记忆整理配置失败: {e}")
        return {"enabled": True}


@router.post('/review_config')
async def update_review_config(request: Request):
    """Update the memory review configuration."""
    try:
        data = await request.json()
        enabled = data.get('enabled', True)

        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        config_data = await asyncio.to_thread(
            config_manager.load_json_config, 'core_config.json', default_value={}
        )

        # 更新配置
        config_data['recent_memory_auto_review'] = enabled

        # 保存配置
        await asyncio.to_thread(
            config_manager.save_json_config, 'core_config.json', config_data
        )

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
        config_data = await asyncio.to_thread(
            config_manager.load_json_config, 'core_config.json', default_value={}
        )
        return {"enabled": config_data.get('powerful_memory_enabled', True)}
    except Exception as e:
        logger.error(f"读取强力记忆配置失败: {e}")
        return {"enabled": True}


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
        enabled = data.get('enabled', True)

        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        config_data = await asyncio.to_thread(
            config_manager.load_json_config, 'core_config.json', default_value={}
        )

        prev_enabled = config_data.get('powerful_memory_enabled', True)
        config_data['powerful_memory_enabled'] = enabled

        # 开→关切换：先跑 migration（重置所有角色 confirmed reflection 的
        # confirmed_at 到 now，让 time-driven fallback 走完整 14 天计时），
        # **成功后**再 save config。否则 migration 失败后 config 已经
        # `False`，下一次用户点关也不会再进 prev_enabled and not enabled 分
        # 支，旧 confirmed_at 锚点永久漏迁移，旧 confirmed 可能立刻被 time-
        # driven 抓走 promote。必须原子：要么两者都成功，要么都失败。
        # 必须走 HTTP 调 memory_server——本 router 在 main_server 进程，直接
        # `from memory_server import ...` 拿到的是 fresh 副本，reflection_engine
        # 是 None，migration 会静默 no-op。memory_server 跑在独立进程
        # (MEMORY_SERVER_PORT)，那里 reflection_engine 由 startup hook 初始化。
        if prev_enabled and not enabled:
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

        # Migration 成功（或非 ON→OFF 切换）才落盘配置——保证用户从前端
        # 视角看到的 toggle 状态与 reflection_engine 实际状态一致。
        await asyncio.to_thread(
            config_manager.save_json_config, 'core_config.json', config_data
        )

        logger.info(f"强力记忆配置已更新: enabled={enabled} (prev={prev_enabled})")
        return {"success": True, "enabled": enabled}
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.error(f"更新强力记忆配置失败: {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------
# Legacy memory 扫描 / 手动清理（对应前端"清理遗留记忆"按钮）
# ---------------------------------------------------------------
#
# 设计目标：列出不在当前 runtime ``memory_dir`` 下、但可能有历史遗留角色
# 记忆的根目录（Documents / CFA 回退原路径 / 历史可读 Documents 候选），让
# 用户主动勾选清理。默认不自动删，任何删除必须由 POST /legacy/purge 带
# 明确路径列表触发，且路径必须落在 scan 返回的 ``legacy_roots[].root``
# 白名单下（防路径逃逸）。


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
            # daily 日记走 commit 阶段 LLM 抽取，preview 显示的是解析出的片段数（近似）。
            "daily": sum(1 for item in analysis["candidates"] if item.get("kind") == "daily"),
        }
        # ETA 估算用料（前端据此估时、标注 240s 上限）：persona 融合按 entity
        # (neko / master) 分组，每组一次 LLM 往返；daily 日记按天（=source_file）
        # 各一次 LLM 抽取；MEMORY.md facts 走纯写盘、不调 LLM。0 次调用 → 前端
        # 回退到无预估文案。
        persona_fusion_calls = len({(item.get("entity") or "master") for item in persona_cands})
        daily_cands = [item for item in analysis["candidates"] if item.get("kind") == "daily"]
        daily_by_file: dict[str, list[str]] = {}
        for item in daily_cands:
            daily_by_file.setdefault(str(item.get("source_file") or ""), []).append(item["text"])

        # count_tokens / 分批逐条编码；接近 8 MiB / 1000 条上限的导入会阻塞事件
        # 循环，与上面 _prepare_external_import 一致 offload 到线程池。daily 调用
        # 次数用与 commit 侧同一个 batch_daily_fragments 算（超长天会拆多批），
        # 保证 ETA 的调用计数与实际执行永不漂移。
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
            # persona 导入现在按 entity 同步跑 LLM 融合（每 entity 可数十秒），
            # 30s 不够；放宽到 240s 覆盖 master+neko 两段融合。前端 commit 超时
            # (memory_browser.js, 270s) 再略大于此，保证后端先返回而非前端先断。
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
                # 透传上游错误码 + partial 元数据（含已落盘的 added_persona）+ 状态码
                # （partial=500 / too_large=413），否则前端拿不到对应分支的引导与
                # memory_edited 广播（Codex P2）。
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

        # 通过所有校验，执行删除（PermissionError 重试一次）
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
            # 已经不存在，视为成功（幂等）
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
