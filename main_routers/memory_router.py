# -*- coding: utf-8 -*-
"""
Memory Router

Handles memory-related endpoints including:
- Recent files listing
- Memory review configuration
"""

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
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger
from fastapi.responses import JSONResponse


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


def validate_catgirl_name(name: str, allow_dots: bool = False) -> tuple[bool, str]:
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
    
    for idx, item in enumerate(chat):
        if not isinstance(item, dict):
            return False, f"chat[{idx}] 必须是一个字典"
        
        # Validate required 'role' key
        if 'role' not in item:
            return False, f"chat[{idx}] 缺少必需的 'role' 字段"
        
        if not isinstance(item['role'], str):
            return False, f"chat[{idx}]['role'] 必须是字符串"
        
        # Validate optional 'text' key if present
        if 'text' in item and not isinstance(item['text'], str):
            return False, f"chat[{idx}]['text'] 必须是字符串"
    
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
    """获取 memory 目录下所有 recent*.json 文件名列表"""
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
    """获取指定 recent*.json 文件内容"""
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
    
    with open(resolved_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return {"content": content}


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
        atomic_write_json(resolved_path, arr, ensure_ascii=False, indent=2)
        
        if catgirl_name:
            # 中断 memory_server 的 review 任务
            import httpx
            from config import MEMORY_SERVER_PORT
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
    更新记忆文件中的猫娘名称
    1. 重命名记忆文件
    2. 更新文件内容中的猫娘名称引用
    """
    data = await request.json()
    old_name = data.get('old_name')
    new_name = data.get('new_name')
    
    if not old_name or not new_name:
        return JSONResponse({"success": False, "error": "缺少必要参数"}, status_code=400)
    
    # Validate old_name (allow dots for historical names during migration)
    is_valid, error_msg = validate_catgirl_name(old_name, allow_dots=True)
    if not is_valid:
        logger.warning(f"Invalid old_name rejected: {old_name!r} - {error_msg}")
        return JSONResponse({"success": False, "error": f"旧名称无效: {error_msg}"}, status_code=400)

    # Validate new_name (strict — no dots allowed)
    is_valid, error_msg = validate_catgirl_name(new_name)
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
    """获取记忆整理配置"""
    try:
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        config_data = config_manager.load_json_config('core_config.json', default_value={})
        return {"enabled": config_data.get('recent_memory_auto_review', True)}
    except Exception as e:
        logger.error(f"读取记忆整理配置失败: {e}")
        return {"enabled": True}


@router.post('/review_config')
async def update_review_config(request: Request):
    """更新记忆整理配置"""
    try:
        data = await request.json()
        enabled = data.get('enabled', True)
        
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        config_data = config_manager.load_json_config('core_config.json', default_value={})
        
        # 更新配置
        config_data['recent_memory_auto_review'] = enabled
        
        # 保存配置
        config_manager.save_json_config('core_config.json', config_data)
        
        logger.info(f"记忆整理配置已更新: enabled={enabled}")
        return {"success": True, "enabled": enabled}
    except Exception as e:
        logger.error(f"更新记忆整理配置失败: {e}")
        return {"success": False, "error": str(e)}
