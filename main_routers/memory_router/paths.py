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
"""Path validation and character-resolution helpers for memory_router."""

import os
import re
from pathlib import Path
from utils.character_name import PROFILE_NAME_MAX_UNITS, validate_character_name
from utils.character_memory import is_legacy_vector_store_dir
from fastapi.responses import JSONResponse

from main_routers import memory_router as _mr
from ._shared import (
    PATH_ERROR_INVALID_REQUEST,
    PATH_ERROR_NOT_FOUND,
    VALID_RECENT_FILENAME_PATTERN,
    _RECENT_CHAT_MAX_MESSAGES,
    _RECENT_CHAT_TOTAL_CHARS_MAX,
    _RECENT_MESSAGE_TEXT_MAX_CHARS,
)

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
    - New layout: memory/<neko>/recent.json
    - Legacy layout: memory/recent_<neko>.json
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
    Validate a neko name for safe use in filenames.
    
    Args:
        name: The neko name to validate
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


async def _resolve_memory_character(raw_name: str):
    """``(config_manager, memory key)`` for a panel request, or the error response.

    Same rules the repetition panel applies: a valid name, resolved to its
    configured characters.json key, that has a character or a memory
    directory and does not escape the memory root.
    """
    from utils.config_manager import get_config_manager

    validation = validate_character_name(
        raw_name, allow_dots=True, max_units=PROFILE_NAME_MAX_UNITS,
    )
    if not validation.ok and validation.code != "reserved_route_name":
        return None, JSONResponse(
            {"success": False, "error": "invalid character name"}, status_code=422,
        )
    character_name = validation.normalized
    config_manager = get_config_manager()
    characters = await config_manager.aload_characters()
    configured = characters.get("猫娘", {}) if isinstance(characters, dict) else {}
    configured_key = _configured_character_key(configured, character_name)
    if configured_key is None and not _mr.character_memory_exists(config_manager, character_name):
        return None, JSONResponse(
            {"success": False, "error": "character not found"}, status_code=404,
        )
    if configured_key is not None:
        character_name = configured_key
    if _default_memory_dir_escapes_root(config_manager, character_name):
        return None, JSONResponse(
            {"success": False, "error": "character not found"}, status_code=404,
        )
    return (config_manager, character_name), None
