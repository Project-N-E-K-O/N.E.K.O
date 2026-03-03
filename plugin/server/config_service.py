"""
插件配置服务

提供插件配置的读取和更新功能。
"""
import io
import os
import re
import sys
import tempfile
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional
from typing import cast
from datetime import datetime
from contextlib import contextmanager

from loguru import logger

from fastapi import HTTPException

from plugin.settings import PLUGIN_CONFIG_ROOT

# Schema 验证开关（可通过环境变量禁用）
_SCHEMA_VALIDATION_ENABLED = os.getenv("NEKO_CONFIG_SCHEMA_VALIDATION", "true").lower() in ("true", "1", "yes", "on")

# 跨平台文件锁支持
msvcrt = None
fcntl = None
try:
    if sys.platform == 'win32':
        import msvcrt as _msvcrt

        msvcrt = _msvcrt
        _has_file_lock = True
    else:
        import fcntl as _fcntl

        fcntl = _fcntl
        _has_file_lock = True
except ImportError:
    _has_file_lock = False

# 进程级别的配置更新锁(每个插件ID一个锁,避免不同插件之间的不必要阻塞)
_config_update_locks: Dict[str, threading.Lock] = {}
_config_update_locks_lock = threading.Lock()


@contextmanager
def file_lock(file_obj):
    """
    跨平台文件锁上下文管理器
    
    使用文件锁保护文件操作,避免并发写入冲突.
    在 Unix/Linux/macOS 上使用 fcntl,在 Windows 上使用 msvcrt.
    
    Args:
        file_obj: 文件对象
    """
    if not _has_file_lock:
        # 如果没有文件锁支持,直接返回(不锁定)
        logger.warning("File locking is not available on this platform. Concurrent access may cause data corruption.")
        yield
        return
    
    try:
        if sys.platform == 'win32':
            if msvcrt is None:
                yield
                return
            # Windows 使用 msvcrt 锁定整个文件
            # 获取文件大小以锁定整个文件
            file_obj.seek(0, 2)  # 移动到文件末尾
            file_size = file_obj.tell()
            file_obj.seek(0)  # 回到文件开头
            if file_size > 0:
                msvcrt.locking(file_obj.fileno(), msvcrt.LK_LOCK, file_size)
            else:
                # 空文件锁定至少 1 个字节以提供基本保护
                # 注意:这会在文件开头锁定 1 个字节,即使文件是空的
                msvcrt.locking(file_obj.fileno(), msvcrt.LK_LOCK, 1)
        else:
            if fcntl is None:
                yield
                return
            # Unix/Linux/macOS 使用 fcntl
            fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if sys.platform == 'win32':
            if msvcrt is None:
                return
            try:
                file_obj.seek(0, 2)
                file_size = file_obj.tell()
                file_obj.seek(0)
                if file_size > 0:
                    msvcrt.locking(file_obj.fileno(), msvcrt.LK_UNLCK, file_size)
                else:
                    # 解锁空文件的 1 字节锁
                    msvcrt.locking(file_obj.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
        else:
            if fcntl is None:
                return
            try:
                fcntl.flock(file_obj.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

try:
    import tomli_w
except ImportError:
    tomli_w = None


def get_plugin_config_path(plugin_id: str) -> Path:
    """
    获取插件的配置文件路径
    
    安全措施:
    1. 验证 plugin_id 只包含安全字符(字母,数字,下划线,连字符)
    2. 使用 resolve() 和 is_relative_to() 确保路径在安全目录内
    
    Args:
        plugin_id: 插件ID(必须只包含安全字符)
    
    Returns:
        配置文件路径
    
    Raises:
        HTTPException: 如果 plugin_id 不安全或配置文件不存在
    """
    # 验证 plugin_id 只包含安全字符(防止路径遍历攻击)
    if not re.match(r'^[a-zA-Z0-9_-]+$', plugin_id):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid plugin_id: '{plugin_id}'. Only alphanumeric characters, underscores, and hyphens are allowed."
        )
    
    # 构建配置文件路径
    config_file = PLUGIN_CONFIG_ROOT / plugin_id / "plugin.toml"
    
    # 解析路径并验证它在安全目录内(防止路径遍历攻击)
    try:
        resolved_path = config_file.resolve()
        # Python 3.9+ 支持 is_relative_to
        if hasattr(resolved_path, 'is_relative_to'):
            if not resolved_path.is_relative_to(PLUGIN_CONFIG_ROOT.resolve()):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid plugin_id: '{plugin_id}'. Path traversal detected."
                )
        else:
            # Python 3.8 兼容:使用 str.startswith 检查
            root_resolved = PLUGIN_CONFIG_ROOT.resolve()
            resolved_str = str(resolved_path)
            root_str = str(root_resolved)
            if not resolved_str.startswith(root_str):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid plugin_id: '{plugin_id}'. Path traversal detected."
                )
    except (OSError, ValueError) as e:
        # 路径解析失败(例如包含无效字符)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid plugin_id: '{plugin_id}'. {str(e)}"
        ) from e
    
    # 检查文件是否存在
    if not config_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' configuration not found"
        )
    
    return config_file


def load_plugin_config(plugin_id: str, *, validate: bool = True) -> Dict[str, Any]:
    """
    加载插件配置
    
    Args:
        plugin_id: 插件ID
        validate: 是否进行 Schema 验证（默认 True）
    
    Returns:
        配置数据
    """
    if tomllib is None:
        raise HTTPException(
            status_code=500,
            detail="TOML library not available"
        )
    
    config_path = get_plugin_config_path(plugin_id)

    try:
        with open(config_path, 'rb') as f:
            config_data = tomllib.load(f)

        # Schema 验证（可通过环境变量或参数禁用）
        if validate and _SCHEMA_VALIDATION_ENABLED:
            validation_errors = _validate_config_schema(config_data, plugin_id)
            if validation_errors:
                logger.warning(
                    "Plugin {}: config schema validation warnings: {}",
                    plugin_id, validation_errors
                )

        # Apply optional user profile overlay defined in plugin.toml.
        # The [plugin] section remains server-facing and is not overridden by
        # user profiles; all other top-level sections may be customized.
        merged_config = _apply_user_config_profiles(
            plugin_id=plugin_id,
            base_config=config_data,
            config_path=config_path,
        )

        stat = config_path.stat()

        return {
            "plugin_id": plugin_id,
            "config": merged_config,
            "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "config_path": str(config_path)
        }
    except HTTPException:
        # 直接透传由下层抛出的 HTTPException（例如用户 profile 覆盖 plugin 段等配置错误）
        raise
    except Exception as e:
        logger.exception(f"Failed to load config for plugin {plugin_id}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load config: {str(e)}"
        ) from e


def load_plugin_base_config(plugin_id: str) -> Dict[str, Any]:
    if tomllib is None:
        raise HTTPException(
            status_code=500,
            detail="TOML library not available"
        )

    config_path = get_plugin_config_path(plugin_id)

    try:
        with open(config_path, "rb") as f:
            config_data = tomllib.load(f)

        stat = config_path.stat()

        return {
            "plugin_id": plugin_id,
            "config": config_data,
            "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "config_path": str(config_path),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to load base config for plugin {plugin_id}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load base config: {str(e)}",
        ) from e


def load_plugin_config_toml(plugin_id: str) -> Dict[str, Any]:
    """加载插件配置(TOML 原文)"""
    config_path = get_plugin_config_path(plugin_id)
    try:
        with open(config_path, 'r', encoding='utf-8', errors='strict') as f:
            toml_text = f.read()

        stat = config_path.stat()

        return {
            "plugin_id": plugin_id,
            "toml": toml_text,
            "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "config_path": str(config_path)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to load TOML config for plugin {plugin_id}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load config: {str(e)}"
        ) from e


# 特殊标记：用于在 deep_merge 中删除 key
_DELETE_MARKER = "__DELETE__"


def deep_merge(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """深度合并字典
    
    特殊处理：
    - 如果 updates 中的值是空字典 {}，则直接替换而不是合并。
    - 如果 updates 中的值是 "__DELETE__"，则从结果中删除该 key。
    - 如果 updates 中的值是包含 "__replace__": True 的字典，则直接替换而不是合并。
    """
    result = base.copy()
    for key, value in updates.items():
        # 删除标记
        if value == _DELETE_MARKER:
            if key in result:
                del result[key]
            continue
        
        # 替换标记
        if isinstance(value, dict) and value.get("__replace__") is True:
            # 移除 __replace__ 标记，直接替换
            new_value = {k: v for k, v in value.items() if k != "__replace__"}
            result[key] = new_value
            continue
        
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # 如果 value 是空字典，直接替换（用于清空配置部分）
            if len(value) == 0:
                result[key] = value
            else:
                result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate_config_schema(config_data: Dict[str, Any], plugin_id: str) -> Optional[List[Dict[str, Any]]]:
    """
    验证插件配置的 Schema
    
    Args:
        config_data: 配置数据字典
        plugin_id: 插件ID（用于日志）
    
    Returns:
        验证错误列表，如果验证通过则返回 None
    """
    try:
        from plugin.server.config_schema import validate_plugin_config, ConfigValidationError
    except ImportError:
        # Schema 验证模块不可用，跳过验证
        logger.debug("Plugin {}: config_schema module not available, skipping validation", plugin_id)
        return None
    
    try:
        validate_plugin_config(config_data)
        return None  # 验证通过
    except ConfigValidationError as e:
        # 返回详细错误信息（作为警告，不阻止加载）
        return e.details if e.details else [{"msg": e.message, "field": e.field}]
    except Exception as e:
        # 其他错误，记录但不阻止
        logger.debug(
            "Plugin {}: schema validation skipped due to error: {}",
            plugin_id, str(e)
        )
        return None


def validate_config_strict(config_data: Dict[str, Any], plugin_id: str) -> None:
    """
    严格验证插件配置（验证失败会抛出异常）
    
    Args:
        config_data: 配置数据字典
        plugin_id: 插件ID
    
    Raises:
        HTTPException: 验证失败时抛出 400 错误
    """
    try:
        from plugin.server.config_schema import validate_plugin_config, ConfigValidationError
    except ImportError as ie:
        raise HTTPException(
            status_code=500,
            detail="配置验证模块不可用"
        ) from ie
    
    try:
        validate_plugin_config(config_data)
    except ConfigValidationError as e:
        raise HTTPException(
            status_code=400,
            detail=f"配置验证失败: {e.message}" + (f" (字段: {e.field})" if e.field else "")
        ) from e
    except Exception as e:
        logger.warning(
            "Plugin {}: strict schema validation error: {}",
            plugin_id, str(e)
        )
        raise HTTPException(
            status_code=400,
            detail=f"配置验证错误: {str(e)}"
        ) from e


def _resolve_profile_path(path_str: str, base_dir: Path) -> Optional[Path]:
    """解析用户配置 profile 的路径，支持 Python 风格的路径写法。

    支持特性：
    - 环境变量：如 "${HOME}/.neko/profiles/dev.toml"
    - 用户目录："~/.neko/profiles/dev.toml"
    - 绝对路径："/etc/neko/dev.toml" 或 "C:\\neko\\dev.toml"
    - 相对路径："dev.toml" 或 "profiles/dev.toml"（相对于插件配置目录）
    """

    try:
        # 展开环境变量和 ~
        expanded = os.path.expandvars(os.path.expanduser(str(path_str)))
        p = Path(expanded)
        if not p.is_absolute():
            p = base_dir / p
        return p.resolve()
    except Exception:
        logger.warning("Failed to resolve user profile path {!r} for base_dir {}", path_str, base_dir)
        return None


def _load_profiles_cfg_from_file(
    plugin_id: str,
    config_path: Path,
) -> Optional[Dict[str, Any]]:
    base_dir = config_path.parent
    profiles_path = base_dir / "profiles.toml"

    if not profiles_path.exists():
        return None

    if tomllib is None:
        logger.warning("Plugin {}: TOML library not available; cannot load profiles.toml", plugin_id)
        return None

    try:
        with profiles_path.open("rb") as pf:
            data = tomllib.load(pf)
    except Exception as e:
        logger.warning(
            "Plugin {}: failed to load profiles.toml from {}: {}; falling back to plugin.config_profiles",
            plugin_id,
            profiles_path,
            e,
        )
        return None

    if not isinstance(data, dict):
        logger.warning(
            "Plugin {}: profiles.toml at {} is not a TOML table at root; got {!r}; falling back to plugin.config_profiles",
            plugin_id,
            profiles_path,
            type(data).__name__,
        )
        return None

    profiles_cfg = data.get("config_profiles")
    if not isinstance(profiles_cfg, dict):
        logger.warning(
            "Plugin {}: 'config_profiles' table not found or invalid in profiles.toml at {}; falling back to plugin.config_profiles",
            plugin_id,
            profiles_path,
        )
        return None

    logger.info(
        "Plugin {}: using profiles.toml at {} for config_profiles; plugin.toml [plugin.config_profiles] will be ignored",
        plugin_id,
        profiles_path,
    )
    return profiles_cfg


def _apply_user_config_profiles(
    *, plugin_id: str, base_config: Dict[str, Any], config_path: Path
) -> Dict[str, Any]:
    """根据 plugin.toml 中声明的用户 profile 叠加配置。

    约定结构（可选）：

    [plugin.config_profiles]
    active = "default"              # 当前激活的 profile 名称，可被环境变量覆盖

    [plugin.config_profiles.files]
    default = "profiles/default.toml"   # 可以是绝对/相对/~ 路径
    work    = "~/neko/work.toml"

    行为：
    - [plugin] 段保持不变，仅覆盖其他顶层段（如 [load_test]）。
    - 如果未配置 config_profiles，或 active/file 未找到，返回 base_config 原样。
    - 如果 profile 文件不存在或解析失败，记录 warning，返回 base_config。
    """

    if not isinstance(base_config, dict):
        return base_config

    profiles_cfg: Optional[Dict[str, Any]] = _load_profiles_cfg_from_file(
        plugin_id,
        config_path,
    )

    if profiles_cfg is None:
        plugin_section = base_config.get("plugin")
        if not isinstance(plugin_section, dict):
            return base_config

        profiles_cfg = plugin_section.get("config_profiles")
        if not isinstance(profiles_cfg, dict):
            return base_config

    # 解析当前激活的 profile 名称，支持环境变量覆盖
    active_name: Optional[str] = None
    raw_active = profiles_cfg.get("active")
    if isinstance(raw_active, str):
        active_name = raw_active.strip() or None

    env_key = f"NEKO_PLUGIN_{plugin_id.upper()}_PROFILE"
    env_override = os.getenv(env_key)
    if isinstance(env_override, str) and env_override.strip():
        active_name = env_override.strip()

    if not active_name:
        # 未指定激活 profile，直接返回基础配置
        return base_config

    files_map = profiles_cfg.get("files")
    if not isinstance(files_map, dict):
        logger.warning(
            "Plugin {}: [plugin.config_profiles.files] must be a table mapping profile names to paths; got {!r}",
            plugin_id,
            type(files_map).__name__ if files_map is not None else None,
        )
        return base_config

    raw_path = files_map.get(active_name)
    if (not isinstance(raw_path, str) or not raw_path.strip()) and active_name.isdigit():
        # tomllib may parse unquoted numeric keys as int (e.g. 1 = "..."), while active_name is a str.
        # Try int(active_name) for better interoperability.
        raw_path = files_map.get(int(active_name))
    if not isinstance(raw_path, str) or not raw_path.strip():
        logger.warning(
            "Plugin {}: active profile '{}' not found in [plugin.config_profiles.files]",
            plugin_id,
            active_name,
        )
        return base_config

    base_dir = config_path.parent
    profile_path = _resolve_profile_path(raw_path, base_dir)
    if profile_path is None:
        return base_config

    if not profile_path.exists():
        logger.warning(
            "Plugin {}: user profile file '{}' (resolved: {}) does not exist; using base config only",
            plugin_id,
            raw_path,
            profile_path,
        )
        return base_config

    if tomllib is None:
        logger.warning(
            "Plugin {}: TOML library not available; cannot load user profile {}",
            plugin_id,
            profile_path,
        )
        return base_config

    try:
        with profile_path.open("rb") as pf:
            overlay = tomllib.load(pf)
    except Exception as e:
        logger.warning(
            "Plugin {}: failed to load user profile {}: {}; using base config only",
            plugin_id,
            profile_path,
            e,
        )
        return base_config

    if not isinstance(overlay, dict):
        logger.warning(
            "Plugin {}: user profile {} is not a TOML table at root; got {!r}",
            plugin_id,
            profile_path,
            type(overlay).__name__,
        )
        return base_config

    # 安全约束：禁止用户 profile 覆盖 [plugin] 段
    if "plugin" in overlay:
        logger.error(
            "Plugin {}: user profile {} attempts to override [plugin] section; rejecting config",
            plugin_id,
            profile_path,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"User profile for plugin '{plugin_id}' must not define a top-level 'plugin' section; "
                f"found in {profile_path}"
            ),
        )

    # 执行叠加：保留 [plugin]，仅覆盖其他顶层段
    merged: Dict[str, Any] = dict(base_config)
    for key, value in overlay.items():
        if key == "plugin":
            # [plugin] 段由服务器管理，不允许通过用户 profile 覆盖
            continue
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value

    logger.info(
        "Plugin {}: applied user config profile '{}' from {}",
        plugin_id,
        active_name,
        profile_path,
    )

    return merged


def get_plugin_profiles_state(plugin_id: str) -> Dict[str, Any]:
    if tomllib is None:
        raise HTTPException(status_code=500, detail="TOML library not available")

    config_path = get_plugin_config_path(plugin_id)
    base_dir = config_path.parent
    profiles_path = base_dir / "profiles.toml"

    profiles_cfg = _load_profiles_cfg_from_file(plugin_id, config_path)

    active_name: Optional[str] = None
    files_info: Dict[str, Any] = {}

    if isinstance(profiles_cfg, dict):
        raw_active = profiles_cfg.get("active")
        if isinstance(raw_active, str):
            active_name = raw_active.strip() or None

        files_map = profiles_cfg.get("files")
        if isinstance(files_map, dict):
            for name, raw_path in files_map.items():
                if not isinstance(name, str):
                    continue
                if not isinstance(raw_path, str):
                    continue
                resolved = _resolve_profile_path(raw_path, base_dir)
                exists = bool(resolved and resolved.exists())
                files_info[name] = {
                    "path": raw_path,
                    "resolved_path": str(resolved) if resolved is not None else None,
                    "exists": exists,
                }

    return {
        "plugin_id": plugin_id,
        "profiles_path": str(profiles_path),
        "profiles_exists": profiles_path.exists(),
        "config_profiles": {
            "active": active_name,
            "files": files_info,
        }
        if profiles_cfg is not None
        else None,
    }


def get_plugin_profile_config(plugin_id: str, profile_name: str) -> Dict[str, Any]:
    if tomllib is None:
        raise HTTPException(status_code=500, detail="TOML library not available")

    if not profile_name:
        raise HTTPException(status_code=400, detail="profile_name is required")

    config_path = get_plugin_config_path(plugin_id)
    base_dir = config_path.parent

    profiles_cfg = _load_profiles_cfg_from_file(plugin_id, config_path)

    raw_path: Optional[str] = None
    if isinstance(profiles_cfg, dict):
        files_map = profiles_cfg.get("files")
        if isinstance(files_map, dict):
            value = files_map.get(profile_name)
            if isinstance(value, str) and value.strip():
                raw_path = value

    if raw_path is None:
        raw_path = f"profiles/{profile_name}.toml"

    profile_path = _resolve_profile_path(raw_path, base_dir)
    resolved_str: Optional[str] = None
    exists = False
    cfg: Dict[str, Any] = {}

    if profile_path is not None:
        resolved_str = str(profile_path)
        exists = profile_path.exists()
        if exists:
            try:
                with profile_path.open("rb") as pf:
                    data = tomllib.load(pf)
                if isinstance(data, dict):
                    cfg = data
            except Exception as e:
                logger.warning(
                    "Plugin %s: failed to load profile %s at %s: %s",
                    plugin_id,
                    profile_name,
                    profile_path,
                    e,
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to load profile '{profile_name}': {str(e)}",
                ) from e

    return {
        "plugin_id": plugin_id,
        "profile": {
            "name": profile_name,
            "path": raw_path,
            "resolved_path": resolved_str,
            "exists": exists,
        },
        "config": cfg,
    }


def upsert_plugin_profile_config(
    plugin_id: str,
    profile_name: str,
    config: Dict[str, Any],
    make_active: Optional[bool] = None,
) -> Dict[str, Any]:
    if tomllib is None or tomli_w is None:
        raise HTTPException(status_code=500, detail="TOML library not available")

    if not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="config must be an object")

    if "plugin" in config:
        raise HTTPException(
            status_code=400,
            detail="Profile config must not define top-level 'plugin' section.",
        )

    if not profile_name:
        raise HTTPException(status_code=400, detail="profile_name is required")

    with _config_update_locks_lock:
        if plugin_id not in _config_update_locks:
            _config_update_locks[plugin_id] = threading.Lock()
        lock = _config_update_locks[plugin_id]

    with lock:
        config_path = get_plugin_config_path(plugin_id)
        base_dir = config_path.parent
        profiles_path = base_dir / "profiles.toml"

        if profiles_path.exists():
            try:
                with profiles_path.open("rb") as pf:
                    data = tomllib.load(pf)
            except Exception as e:
                logger.warning(
                    "Plugin %s: failed to load profiles.toml from %s for write: %s",
                    plugin_id,
                    profiles_path,
                    e,
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to load profiles.toml: {str(e)}",
                ) from e
            if not isinstance(data, dict):
                data = {}
        else:
            data = {}

        profiles_cfg = data.get("config_profiles")
        if not isinstance(profiles_cfg, dict):
            profiles_cfg = {}

        files_map = profiles_cfg.get("files")
        if not isinstance(files_map, dict):
            files_map = {}
        profiles_cfg["files"] = files_map

        raw_path = files_map.get(profile_name)
        if not isinstance(raw_path, str) or not raw_path.strip():
            raw_path = f"profiles/{profile_name}.toml"
            files_map[profile_name] = raw_path

        if make_active or ("active" not in profiles_cfg or not profiles_cfg.get("active")):
            profiles_cfg["active"] = profile_name

        data["config_profiles"] = profiles_cfg

        profile_path = _resolve_profile_path(raw_path, base_dir)
        if profile_path is None:
            raise HTTPException(status_code=400, detail="Invalid profile path")

        try:
            profile_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        temp_fd_profile, temp_profile_path = tempfile.mkstemp(
            suffix=".toml",
            prefix=".profile_",
            dir=str(profile_path.parent),
        )
        try:
            with os.fdopen(temp_fd_profile, "wb") as temp_file:
                tomli_w.dump(config, temp_file)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            os.replace(temp_profile_path, profile_path)
        except Exception:
            try:
                if os.path.exists(temp_profile_path):
                    os.unlink(temp_profile_path)
            except Exception:
                pass
            raise

        profiles_dir = profiles_path.parent
        temp_fd_profiles, temp_profiles_path = tempfile.mkstemp(
            suffix=".toml",
            prefix=".profiles_",
            dir=str(profiles_dir),
        )
        try:
            with os.fdopen(temp_fd_profiles, "wb") as temp_file:
                tomli_w.dump(data, temp_file)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            os.replace(temp_profiles_path, profiles_path)

            try:
                dir_fd = os.open(profiles_dir, os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except (AttributeError, OSError):
                pass
        except Exception:
            try:
                if os.path.exists(temp_profiles_path):
                    os.unlink(temp_profiles_path)
            except Exception:
                pass
            raise

    return get_plugin_profile_config(plugin_id, profile_name)


def delete_plugin_profile_config(plugin_id: str, profile_name: str) -> Dict[str, Any]:
    if tomllib is None or tomli_w is None:
        raise HTTPException(status_code=500, detail="TOML library not available")

    if not profile_name:
        raise HTTPException(status_code=400, detail="profile_name is required")

    with _config_update_locks_lock:
        if plugin_id not in _config_update_locks:
            _config_update_locks[plugin_id] = threading.Lock()
        lock = _config_update_locks[plugin_id]

    with lock:
        config_path = get_plugin_config_path(plugin_id)
        base_dir = config_path.parent
        profiles_path = base_dir / "profiles.toml"

        if not profiles_path.exists():
            return {
                "plugin_id": plugin_id,
                "profile": profile_name,
                "removed": False,
            }

        try:
            with profiles_path.open("rb") as pf:
                data = tomllib.load(pf)
        except Exception as e:
            logger.warning(
                "Plugin %s: failed to load profiles.toml from %s for delete: %s",
                plugin_id,
                profiles_path,
                e,
            )
            raise HTTPException(
                status_code=400,
                detail=f"Failed to load profiles.toml: {str(e)}",
            ) from e

        if not isinstance(data, dict):
            data = {}

        profiles_cfg = data.get("config_profiles")
        if not isinstance(profiles_cfg, dict):
            profiles_cfg = {}

        files_map = profiles_cfg.get("files")
        if not isinstance(files_map, dict):
            files_map = {}
        profiles_cfg["files"] = files_map

        removed = False
        raw_path: Optional[str] = None
        if profile_name in files_map:
            raw_path = files_map.pop(profile_name)
            removed = True

        active = profiles_cfg.get("active")
        if isinstance(active, str) and active == profile_name:
            profiles_cfg["active"] = None

        data["config_profiles"] = profiles_cfg

        profiles_dir = profiles_path.parent
        temp_fd_profiles, temp_profiles_path = tempfile.mkstemp(
            suffix=".toml",
            prefix=".profiles_",
            dir=str(profiles_dir),
        )
        try:
            with os.fdopen(temp_fd_profiles, "wb") as temp_file:
                tomli_w.dump(data, temp_file)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            os.replace(temp_profiles_path, profiles_path)

            try:
                dir_fd = os.open(profiles_dir, os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except (AttributeError, OSError):
                pass
        except Exception:
            try:
                if os.path.exists(temp_profiles_path):
                    os.unlink(temp_profiles_path)
            except Exception:
                pass
            raise

    return {
        "plugin_id": plugin_id,
        "profile": profile_name,
        "removed": removed,
    }


def set_plugin_active_profile(plugin_id: str, profile_name: str) -> Dict[str, Any]:
    if tomllib is None or tomli_w is None:
        raise HTTPException(status_code=500, detail="TOML library not available")

    if not profile_name:
        raise HTTPException(status_code=400, detail="profile_name is required")

    with _config_update_locks_lock:
        if plugin_id not in _config_update_locks:
            _config_update_locks[plugin_id] = threading.Lock()
        lock = _config_update_locks[plugin_id]

    with lock:
        config_path = get_plugin_config_path(plugin_id)
        base_dir = config_path.parent
        profiles_path = base_dir / "profiles.toml"

        if not profiles_path.exists():
            raise HTTPException(status_code=404, detail="profiles.toml not found")

        try:
            with profiles_path.open("rb") as pf:
                data = tomllib.load(pf)
        except Exception as e:
            logger.warning(
                "Plugin %s: failed to load profiles.toml from %s for set active: %s",
                plugin_id,
                profiles_path,
                e,
            )
            raise HTTPException(
                status_code=400,
                detail=f"Failed to load profiles.toml: {str(e)}",
            ) from e

        if not isinstance(data, dict):
            data = {}

        profiles_cfg = data.get("config_profiles")
        if not isinstance(profiles_cfg, dict):
            profiles_cfg = {}

        files_map = profiles_cfg.get("files")
        if not isinstance(files_map, dict):
            files_map = {}
        profiles_cfg["files"] = files_map

        if profile_name not in files_map:
            raise HTTPException(status_code=404, detail="profile not found in config_profiles.files")

        profiles_cfg["active"] = profile_name
        data["config_profiles"] = profiles_cfg

        profiles_dir = profiles_path.parent
        temp_fd_profiles, temp_profiles_path = tempfile.mkstemp(
            suffix=".toml",
            prefix=".profiles_",
            dir=str(profiles_dir),
        )
        try:
            with os.fdopen(temp_fd_profiles, "wb") as temp_file:
                tomli_w.dump(data, temp_file)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            os.replace(temp_profiles_path, profiles_path)

            try:
                dir_fd = os.open(profiles_dir, os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except (AttributeError, OSError):
                pass
        except Exception:
            try:
                if os.path.exists(temp_profiles_path):
                    os.unlink(temp_profiles_path)
            except Exception:
                pass
            raise

    return get_plugin_profiles_state(plugin_id)


def replace_plugin_config(plugin_id: str, new_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    使用传入的新配置覆盖写入插件配置。

    注意：为兼容前端表单更新逻辑，允许 new_config 不包含 plugin.id / plugin.entry，
    后端会从现有配置中补回这两个受保护字段。
    """
    if tomllib is None or tomli_w is None:
        raise HTTPException(
            status_code=500,
            detail="TOML library not available"
        )

    if not isinstance(new_config, dict):
        raise HTTPException(status_code=400, detail="config must be an object")

    with _config_update_locks_lock:
        if plugin_id not in _config_update_locks:
            _config_update_locks[plugin_id] = threading.Lock()
        lock = _config_update_locks[plugin_id]

    with lock:
        config_path = get_plugin_config_path(plugin_id)

        try:
            with open(config_path, 'r+b') as f:
                with file_lock(f):
                    current_config = tomllib.load(f)

                    plugin_section: Dict[str, Any] = {}
                    plugin_section_raw = new_config.get("plugin")
                    if isinstance(plugin_section_raw, dict):
                        plugin_section = plugin_section_raw
                    else:
                        new_config = {**new_config, "plugin": plugin_section}

                    current_plugin_section = (
                        current_config.get("plugin") if isinstance(current_config.get("plugin"), dict) else {}
                    )

                    _validate_protected_fields_unchanged(current_config, new_config)

                    if plugin_section.get("id") is None and isinstance(current_plugin_section, dict) and "id" in current_plugin_section:
                        plugin_section["id"] = current_plugin_section.get("id")
                    if plugin_section.get("entry") is None and isinstance(current_plugin_section, dict) and "entry" in current_plugin_section:
                        plugin_section["entry"] = current_plugin_section.get("entry")

                    config_dir = config_path.parent
                    temp_fd, temp_path = tempfile.mkstemp(
                        suffix='.toml',
                        prefix='.plugin_config_',
                        dir=config_dir
                    )

                    try:
                        with os.fdopen(temp_fd, 'wb') as temp_file:
                            tomli_w.dump(new_config, temp_file)
                            temp_file.flush()
                            os.fsync(temp_file.fileno())

                        os.replace(temp_path, config_path)

                        try:
                            config_dir_fd = os.open(config_dir, os.O_DIRECTORY)
                            try:
                                os.fsync(config_dir_fd)
                            finally:
                                os.close(config_dir_fd)
                        except (AttributeError, OSError):
                            pass
                    except Exception:
                        try:
                            if os.path.exists(temp_path):
                                os.unlink(temp_path)
                        except Exception:
                            pass
                        raise

            updated = load_plugin_config(plugin_id)

            logger.info(f"Replaced config for plugin {plugin_id}")
            return {
                "success": True,
                "plugin_id": plugin_id,
                "config": updated["config"],
                "requires_reload": True,
                "message": "Config updated successfully"
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to replace config for plugin {plugin_id}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to update config: {str(e)}"
            ) from e


def update_plugin_config(plugin_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    更新插件配置
    
    使用进程级别的锁和文件锁双重保护整个读取-修改-写入周期,防止 TOCTOU 竞态条件.
    每个插件ID有独立的进程内锁,避免不同插件之间的不必要阻塞.
    文件锁提供跨进程保护,适用于多进程部署场景.
    
    Args:
        plugin_id: 插件ID
        updates: 要更新的配置部分
    
    Returns:
        更新后的配置
    """
    if tomllib is None or tomli_w is None:
        raise HTTPException(
            status_code=500,
            detail="TOML library not available"
        )
    
    # 获取插件专属的进程内锁(避免同一进程内的并发访问)
    with _config_update_locks_lock:
        if plugin_id not in _config_update_locks:
            _config_update_locks[plugin_id] = threading.Lock()
        lock = _config_update_locks[plugin_id]
    
    # 在整个读取-修改-写入周期都持有进程内锁和文件锁,防止 TOCTOU 竞态条件
    with lock:
        config_path = get_plugin_config_path(plugin_id)
        
        try:
            # 同时持有进程内锁和跨进程文件锁,保护整个读取-修改-写入周期
            with open(config_path, 'r+b') as f:
                with file_lock(f):
                    # 读取现有配置
                    current_config = tomllib.load(f)

                    # 深度合并
                    merged_config = deep_merge(current_config, updates)

                    # 使用临时文件 + 原子性 rename 的方式,确保配置持久化的可靠性
                    # 这样即使写入过程中出问题,原文件也不会损坏
                    config_dir = config_path.parent
                    temp_fd, temp_path = tempfile.mkstemp(
                        suffix='.toml',
                        prefix='.plugin_config_',
                        dir=config_dir
                    )

                    try:
                        # 写入临时文件
                        with os.fdopen(temp_fd, 'wb') as temp_file:
                            tomli_w.dump(merged_config, temp_file)
                            temp_file.flush()  # 确保数据从 Python 缓冲区写入操作系统
                            os.fsync(temp_file.fileno())  # 确保数据立即写入磁盘

                        # 原子性地替换原文件
                        # 在大多数文件系统上,rename 是原子操作
                        os.replace(temp_path, config_path)

                        # 确保目录的元数据也同步到磁盘(部分平台不支持 O_DIRECTORY)
                        try:
                            config_dir_fd = os.open(config_dir, os.O_DIRECTORY)
                            try:
                                os.fsync(config_dir_fd)
                            finally:
                                os.close(config_dir_fd)
                        except (AttributeError, OSError):
                            # Windows 等平台无 O_DIRECTORY,或目录 fsync 不被支持
                            pass

                    except Exception:
                        # 如果写入失败,清理临时文件
                        try:
                            if os.path.exists(temp_path):
                                os.unlink(temp_path)
                        except Exception:
                            pass
                        raise
            
            # 重新加载配置
            updated = load_plugin_config(plugin_id)
            
            logger.info(f"Updated config for plugin {plugin_id}")
            return {
                "success": True,
                "plugin_id": plugin_id,
                "config": updated["config"],
                "requires_reload": True,  # 配置更新通常需要重载插件
                "message": "Config updated successfully"
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to update config for plugin {plugin_id}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to update config: {str(e)}"
            ) from e


def update_plugin_config_toml(plugin_id: str, toml_text: str) -> Dict[str, Any]:
    """使用 TOML 原文更新插件配置(覆盖写入).

    安全性:
    - 解析 TOML,保证语法正确
    - 禁止修改 plugin.id / plugin.entry(只要值发生变化就拒绝;允许原文包含它们)
    - 使用进程锁 + 文件锁 + 原子替换
    """
    if tomllib is None:
        raise HTTPException(status_code=500, detail="TOML library not available")

    # 获取插件专属的进程内锁
    with _config_update_locks_lock:
        if plugin_id not in _config_update_locks:
            _config_update_locks[plugin_id] = threading.Lock()
        lock = _config_update_locks[plugin_id]

    with lock:
        config_path = get_plugin_config_path(plugin_id)

        if toml_text is None:
            raise HTTPException(status_code=400, detail="toml_text cannot be None")

        try:
            parsed_new = tomllib.loads(toml_text)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid TOML format: {str(e)}") from e

        try:
            with open(config_path, 'r+b') as f:
                with file_lock(f):
                    current_config = tomllib.load(f)

                    def _get_protected(cfg: Dict[str, Any], key: str) -> Any:
                        raw = cfg.get("plugin")
                        plugin_section = raw if isinstance(raw, dict) else {}
                        return plugin_section.get(key)

                    # 只要 value 变化就拒绝
                    current_id = _get_protected(current_config, "id")
                    current_entry = _get_protected(current_config, "entry")
                    new_id = _get_protected(parsed_new, "id")
                    new_entry = _get_protected(parsed_new, "entry")

                    if new_id is not None and current_id is not None and new_id != current_id:
                        raise HTTPException(
                            status_code=400,
                            detail="Cannot modify critical field 'plugin.id'. This field is protected."
                        )
                    if new_entry is not None and current_entry is not None and new_entry != current_entry:
                        raise HTTPException(
                            status_code=400,
                            detail="Cannot modify critical field 'plugin.entry'. This field is protected."
                        )

                    # 原子写入(使用临时文件 + replace)
                    config_dir = config_path.parent
                    temp_fd, temp_path = tempfile.mkstemp(
                        suffix='.toml',
                        prefix='.plugin_config_',
                        dir=config_dir
                    )
                    try:
                        with os.fdopen(temp_fd, 'wb') as temp_file:
                            data = toml_text.encode('utf-8')
                            temp_file.write(data)
                            temp_file.flush()
                            os.fsync(temp_file.fileno())

                        os.replace(temp_path, config_path)

                        try:
                            config_dir_fd = os.open(config_dir, os.O_DIRECTORY)
                            try:
                                os.fsync(config_dir_fd)
                            finally:
                                os.close(config_dir_fd)
                        except (AttributeError, OSError):
                            pass
                    except Exception:
                        try:
                            if os.path.exists(temp_path):
                                os.unlink(temp_path)
                        except Exception:
                            pass
                        raise

            updated = load_plugin_config(plugin_id)

            logger.info(f"Updated TOML config for plugin {plugin_id}")
            return {
                "success": True,
                "plugin_id": plugin_id,
                "config": updated["config"],
                "requires_reload": True,
                "message": "Config updated successfully"
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to update TOML config for plugin {plugin_id}")
            raise HTTPException(status_code=500, detail=f"Failed to update config: {str(e)}") from e


def _validate_protected_fields_unchanged(
    current_config: Dict[str, Any],
    new_config: Dict[str, Any],
) -> None:
    def _get(cfg: Dict[str, Any], key: str) -> Any:
        raw = cfg.get("plugin")
        plugin_section = raw if isinstance(raw, dict) else {}
        return plugin_section.get(key)

    current_id = _get(current_config, "id")
    current_entry = _get(current_config, "entry")
    new_id = _get(new_config, "id")
    new_entry = _get(new_config, "entry")

    if new_id is not None and current_id is not None and new_id != current_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot modify critical field 'plugin.id'. This field is protected.",
        )
    if new_entry is not None and current_entry is not None and new_entry != current_entry:
        raise HTTPException(
            status_code=400,
            detail="Cannot modify critical field 'plugin.entry'. This field is protected.",
        )


def parse_toml_to_config(plugin_id: str, toml_text: str) -> Dict[str, Any]:
    """解析 TOML 原文为配置对象(不落盘).

    - 语法错误返回 400
    - 同 update_plugin_config_toml 一样,禁止修改 plugin.id / plugin.entry(用于表单/源码同步时阻止非法草稿)
    """
    if tomllib is None:
        raise HTTPException(status_code=500, detail="TOML library not available")

    if toml_text is None:
        raise HTTPException(status_code=400, detail="toml_text cannot be None")

    try:
        parsed = tomllib.loads(toml_text)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid TOML format: {str(e)}") from e

    current = load_plugin_config(plugin_id)
    current_config = current.get("config") if isinstance(current, dict) else {}
    if isinstance(current_config, dict):
        _validate_protected_fields_unchanged(current_config, parsed)

    return {
        "plugin_id": plugin_id,
        "config": parsed,
    }


def render_config_to_toml(plugin_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """把配置对象渲染为 TOML 原文(不落盘).

    - 禁止修改 plugin.id / plugin.entry(若传入的 config 尝试改动则 400)
    """
    if tomli_w is None:
        raise HTTPException(status_code=500, detail="TOML library not available")

    if not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="config must be an object")

    current = load_plugin_config(plugin_id)
    current_config = current.get("config") if isinstance(current, dict) else {}
    if isinstance(current_config, dict):
        _validate_protected_fields_unchanged(current_config, config)

    try:
        buf = io.BytesIO()
        tomli_w.dump(config, buf)
        toml_text = buf.getvalue().decode("utf-8")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to render TOML: {str(e)}") from e

    return {
        "plugin_id": plugin_id,
        "toml": toml_text,
    }


async def hot_update_plugin_config(
    plugin_id: str,
    updates: Dict[str, Any],
    mode: str = "temporary",
    profile: Optional[str] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """
    热更新插件配置（不需要重启插件）。
    
    注意：这是一个异步函数，必须在事件循环中调用。
    
    支持两种模式：
    - temporary: 临时更新，只修改插件进程内缓存，不写入文件。
                 插件重启后配置会恢复为文件中的值。
    - permanent: 永久更新，写入 profile 文件，并通知插件进程更新缓存。
    
    Args:
        plugin_id: 插件ID
        updates: 要更新的配置部分（会与现有配置深度合并）
        mode: "temporary" | "permanent"
        profile: profile 名称（permanent 模式时使用，None 表示使用当前激活的 profile）
        timeout: 等待插件响应的超时时间（秒）
    
    Returns:
        {
            "success": bool,
            "plugin_id": str,
            "mode": str,
            "hot_reloaded": bool,
            "requires_reload": bool,
            "message": str,
        }
    """
    import asyncio
    from plugin.core.state import state
    
    # 检查插件是否正在运行（在线程池中执行锁操作，避免阻塞事件循环）
    def _get_host_sync():
        with state.acquire_plugin_hosts_read_lock():
            return state.plugin_hosts.get(plugin_id)
    
    loop = asyncio.get_running_loop()
    host = await loop.run_in_executor(None, _get_host_sync)
    
    if host is None:
        # 插件未运行，只能写入文件
        if mode == "temporary":
            raise HTTPException(
                status_code=400,
                detail=f"Plugin {plugin_id} is not running. Cannot apply temporary config update."
            )
        # permanent 模式：直接写入文件（在线程池中执行避免阻塞）
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, update_plugin_config, plugin_id, updates)
        result["hot_reloaded"] = False
        result["mode"] = mode
        return result
    
    # 如果是 permanent 模式，先写入文件
    if mode == "permanent":
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, update_plugin_config, plugin_id, updates)
            logger.debug(f"Config written to file for plugin {plugin_id}")
        except Exception as e:
            logger.exception(f"Failed to write config for plugin {plugin_id}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to write config: {str(e)}"
            ) from e
    
    # 发送 CONFIG_UPDATE 命令到插件进程
    # 重要：必须通过 host.send_config_update() 发送命令
    # 这会正确地通过 _pending_futures 机制等待响应，避免消费其他请求的响应
    try:
        # 获取合并后的完整配置（用于发送给插件）
        if mode == "permanent":
            loop = asyncio.get_running_loop()
            config_result = await loop.run_in_executor(None, load_plugin_config, plugin_id)
            full_config = config_result.get("config", {})
        else:
            # temporary 模式：只发送更新部分
            full_config = updates
        
        # 检查是否有 send_config_update 方法（异步方法）
        if hasattr(host, 'send_config_update'):
            try:
                # permanent 模式的“持久化”在主进程侧已经完成（update_plugin_config）。
                # 子进程这里只需要刷新内存配置缓存，因此始终用 temporary 触发热更新。
                update_mode = "temporary" if mode == "permanent" else mode
                # 直接 await 异步方法，在同一个事件循环中执行
                result = await host.send_config_update(
                    config=full_config,
                    mode=update_mode,
                    profile=profile,
                    timeout=timeout,
                )
                
                return {
                    "success": True,
                    "plugin_id": plugin_id,
                    "mode": mode,
                    "hot_reloaded": True,
                    "requires_reload": False,
                    "handler_called": result.get("handler_called", False) if isinstance(result, dict) else False,
                    "message": "Config hot-updated successfully",
                }
            except TimeoutError:
                logger.warning(f"Timeout waiting for CONFIG_UPDATE response from plugin {plugin_id}")
                return {
                    "success": True,
                    "plugin_id": plugin_id,
                    "mode": mode,
                    "hot_reloaded": True,
                    "requires_reload": False,
                    "message": "Config update sent (response timeout, may have been applied)",
                }
            except Exception as e:
                logger.warning(f"CONFIG_UPDATE command failed for plugin {plugin_id}: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Plugin config update failed: {str(e)}"
                )
        else:
            # 回退：直接发送命令（不等待响应）
            import uuid
            req_id = str(uuid.uuid4())
            cmd = {
                "type": "CONFIG_UPDATE",
                "config": full_config,
                "mode": mode,
                "profile": profile,
                "req_id": req_id,
            }
            if hasattr(host, 'cmd_queue') and host.cmd_queue is not None:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: host.cmd_queue.put(cmd, timeout=5.0))
                logger.debug(f"CONFIG_UPDATE command sent to plugin {plugin_id} (fallback), req_id={req_id}")
                return {
                    "success": True,
                    "plugin_id": plugin_id,
                    "mode": mode,
                    "hot_reloaded": True,
                    "requires_reload": False,
                    "message": "Config update sent (no response confirmation)",
                }
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Plugin {plugin_id} does not have a command queue"
                )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to hot-update config for plugin {plugin_id}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to hot-update config: {str(e)}"
        ) from e

