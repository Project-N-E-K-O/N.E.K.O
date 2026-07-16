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
import os
import re
import json
from pathlib import Path

from fastapi import APIRouter, Request
from utils.character_name import validate_character_name
from utils.character_memory import (
    character_memory_exists,
    rename_character_memory_storage,
)
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.file_utils import atomic_write_json_async
from utils.logger_config import get_module_logger
from fastapi.responses import JSONResponse
from memory.external_markdown_import import (
    ExternalMemoryImportError,
    MAX_TOTAL_BYTES,
    build_import_candidates,
    collect_markdown_files,
)


router = APIRouter(prefix="/api/memory", tags=["memory"])

# Pattern for valid recent file names: must start with "recent_", have content, and end with .json
# Uses blacklist approach instead of whitelist to support CJK characters
VALID_RECENT_FILENAME_PATTERN = re.compile(r'^recent_.+\.json$')
PATH_ERROR_INVALID_REQUEST = "INVALID_REQUEST"
PATH_ERROR_NOT_FOUND = "NOT_FOUND"


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

    for flat_file in base_dir.glob('recent_*.json'):
        if flat_file.is_file():
            logical_names.add(flat_file.name)

    for child in base_dir.iterdir():
        if not child.is_dir():
            continue
        recent_file = child / 'recent.json'
        if recent_file.is_file():
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
    
    # offload 同步 read 到线程池：recent.json 单文件可达数 MB
    content = await asyncio.to_thread(_read_text_file, resolved_path)
    return {"content": content}


def _read_text_file(path: str, encoding: str = 'utf-8') -> str:
    with open(path, 'r', encoding=encoding) as f:
        return f.read()


@router.post('/recent_file/save')
async def save_recent_file(request: Request):
    data = await request.json()
    filename = data.get('filename')
    chat = data.get('chat')
    
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
    
    from utils.config_manager import get_config_manager
    cm = get_config_manager()
    catgirl_name = extract_catgirl_name_from_recent_filename(filename)
    if catgirl_name is None:
        logger.warning(f"Failed to extract catgirl name from filename: {filename!r}")
        return JSONResponse({"success": False, "error": "文件名不合法"}, status_code=400)

    assert_cloudsave_writable(
        cm,
        operation="save",
        target=f"memory/{catgirl_name}/recent.json",
    )

    resolved_path, path_error, _path_error_code, catgirl_name = resolve_recent_file_path(cm, filename, create=True)
    if resolved_path is None:
        logger.warning(f"Recent file path resolution failed for filename: {filename!r} - {path_error}")
        return JSONResponse({"success": False, "error": path_error}, status_code=400)
    
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
    try:
        await atomic_write_json_async(resolved_path, arr, ensure_ascii=False, indent=2)
        
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
        return {"success": True, "need_refresh": True, "catgirl_name": catgirl_name}
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
        if character_memory_exists(cm, old_name) or character_memory_exists(cm, new_name):
            assert_cloudsave_writable(
                cm,
                operation="rename",
                target=f"memory/{old_name} -> memory/{new_name}",
            )

        result = rename_character_memory_storage(cm, old_name, new_name)
        logger.info(
            "已更新猫娘名称从 '%s' 到 '%s' 的记忆文件，changed=%s",
            old_name,
            new_name,
            result.get("changed", False),
        )
        return {
            "success": True,
            "changed": bool(result.get("changed", False)),
            "exists_after": bool(result.get("exists_after", False)),
        }
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.exception("更新猫娘名称失败")
        return {"success": False, "error": str(e)}


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
        counts = {
            "persona": sum(1 for item in analysis["candidates"] if item["target"] == "persona"),
            "facts": sum(1 for item in analysis["candidates"] if item["target"] == "facts"),
        }
        return {
            "success": True,
            "character_name": character_name,
            "source_format": analysis["source_format"],
            "files": analysis["files"],
            "counts": counts,
            "candidate_count": len(analysis["candidates"]),
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
        response = await client.post(
            f"http://127.0.0.1:{MEMORY_SERVER_PORT}/internal/memory/import_external_markdown",
            json={
                "character_name": character_name,
                "source_format": analysis["source_format"],
                "imported_files": analysis["files"],
                "candidates": analysis["candidates"],
                "warning_count": len(analysis["warnings"]),
            },
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
