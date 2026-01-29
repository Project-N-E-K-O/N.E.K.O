"""Plugin config access helpers.

This module provides a small, developer-friendly API for reading/updating the
plugin's own `plugin.toml` via the main process.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Coroutine, Dict, Optional, Union, overload

if TYPE_CHECKING:
    from .types import PluginContextProtocol


class PluginConfigError(RuntimeError):
    def __init__(self, message: str, *, path: Optional[str] = None, operation: Optional[str] = None):
        self.path = path
        self.operation = operation
        super().__init__(message)


_MISSING = object()


def _get_by_path(data: Any, path: str) -> Any:
    if path == "" or path is None:
        return data
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            raise PluginConfigError(
                f"Config path '{path}' is invalid (encountered non-dict at '{part}')",
                path=path,
                operation="get",
            )
        if part not in cur:
            raise PluginConfigError(
                f"Config key '{path}' not found",
                path=path,
                operation="get",
            )
        cur = cur[part]
    return cur


def _set_by_path(root: Dict[str, Any], path: str, value: Any) -> Dict[str, Any]:
    if path == "" or path is None:
        if not isinstance(value, dict):
            raise PluginConfigError("Root update requires a dict", path=path, operation="set")
        return value

    parts = path.split(".")
    cur: Dict[str, Any] = root
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return root


@dataclass
class PluginConfig:
    """High-level wrapper around `PluginContext.get_own_config/update_own_config`."""

    ctx: "PluginContextProtocol"

    def _unwrap(self, value: Any, *, operation: str) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise PluginConfigError(f"Invalid config type: {type(value)}", operation=operation)
        if "data" in value and isinstance(value.get("data"), dict):
            value = value["data"]
        # The runtime returns a wrapper like:
        # {"success": ..., "plugin_id": ..., "config": <toml_root>, ...}
        # SDK exposes only the toml root to plugin authors.
        inner = value.get("config")
        if inner is None:
            return value
        if not isinstance(inner, dict):
            raise PluginConfigError(f"Invalid config inner type: {type(inner)}", operation=operation)
        return inner

    def _is_in_event_loop(self) -> bool:
        """检测当前是否在事件循环中运行。
        
        Returns:
            True 如果当前在事件循环中，False 如果在 worker 线程或无事件循环环境
        """
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def _run_sync(self, coro: Any, *, operation: str) -> Any:
        """Run an async config coroutine from sync context.

        This is a convenience for plugin authors who are not in an async function.
        It is intentionally strict: if called while an event loop is running,
        we raise to avoid deadlocks and "coroutine was never awaited" mistakes.
        """

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        raise PluginConfigError(
            f"{operation}_sync cannot be used inside a running event loop; use 'await {operation}(...)' instead",
            operation=operation,
        )

    async def _dump_async(self, *, timeout: float = 5.0) -> Dict[str, Any]:
        if not hasattr(self.ctx, "get_own_config"):
            raise PluginConfigError("ctx.get_own_config is not available", operation="dump")
        try:
            cfg = await self.ctx.get_own_config(timeout=timeout)
        except Exception as e:
            raise PluginConfigError(f"Failed to read config: {e}", operation="dump") from e
        return self._unwrap(cfg, operation="dump")

    @overload
    def dump(self, *, timeout: float = ...) -> Coroutine[Any, Any, Dict[str, Any]]: ...  # 异步环境
    @overload
    def dump(self, *, timeout: float = ...) -> Dict[str, Any]: ...  # 同步环境
    
    def dump(self, *, timeout: float = 5.0) -> "Union[Dict[str, Any], Coroutine[Any, Any, Dict[str, Any]]]":
        """智能代理：自动检测执行环境，选择同步或异步执行方式。
        
        Returns:
            在事件循环中返回 Coroutine，否则返回 Dict
        """
        coro = self._dump_async(timeout=timeout)
        if self._is_in_event_loop():
            return coro
        return self._run_sync(coro, operation="dump")

    def dump_sync(self, *, timeout: float = 5.0) -> Dict[str, Any]:
        return self._run_sync(self._dump_async(timeout=timeout), operation="dump")

    async def dump_base(self, *, timeout: float = 5.0) -> Dict[str, Any]:
        """Return base config (plugin.toml without profile overlay)."""

        if not hasattr(self.ctx, "get_own_base_config"):
            raise PluginConfigError("ctx.get_own_base_config is not available", operation="dump_base")
        try:
            res = await self.ctx.get_own_base_config(timeout=timeout)
        except Exception as e:
            raise PluginConfigError(f"Failed to read base config: {e}", operation="dump_base") from e
        return self._unwrap(res, operation="dump_base")

    def dump_base_sync(self, *, timeout: float = 5.0) -> Dict[str, Any]:
        return self._run_sync(self.dump_base(timeout=timeout), operation="dump_base")

    async def get_profiles_state(self, *, timeout: float = 5.0) -> Dict[str, Any]:
        """Return profiles.toml state (active + files mapping)."""

        if not hasattr(self.ctx, "get_own_profiles_state"):
            raise PluginConfigError("ctx.get_own_profiles_state is not available", operation="get_profiles_state")
        try:
            res = await self.ctx.get_own_profiles_state(timeout=timeout)
        except Exception as e:
            raise PluginConfigError(f"Failed to read profiles state: {e}", operation="get_profiles_state") from e
        if not isinstance(res, dict):
            raise PluginConfigError(f"Invalid profiles state type: {type(res)}", operation="get_profiles_state")
        if "data" in res and isinstance(res.get("data"), dict):
            res = res["data"]
        return res

    def get_profiles_state_sync(self, *, timeout: float = 5.0) -> Dict[str, Any]:
        return self._run_sync(self.get_profiles_state(timeout=timeout), operation="get_profiles_state")

    async def get_profile(self, profile_name: str, *, timeout: float = 5.0) -> Dict[str, Any]:
        """Return a single profile overlay config."""

        if not hasattr(self.ctx, "get_own_profile_config"):
            raise PluginConfigError("ctx.get_own_profile_config is not available", operation="get_profile")
        try:
            res = await self.ctx.get_own_profile_config(profile_name, timeout=timeout)
        except Exception as e:
            raise PluginConfigError(f"Failed to read profile '{profile_name}': {e}", operation="get_profile") from e
        if not isinstance(res, dict):
            raise PluginConfigError(f"Invalid profile response type: {type(res)}", operation="get_profile")
        if "data" in res and isinstance(res.get("data"), dict):
            res = res["data"]
        cfg = res.get("config")
        if cfg is None:
            return {}
        if not isinstance(cfg, dict):
            raise PluginConfigError(f"Invalid profile config type: {type(cfg)}", operation="get_profile")
        return cfg

    def get_profile_sync(self, profile_name: str, *, timeout: float = 5.0) -> Dict[str, Any]:
        return self._run_sync(self.get_profile(profile_name, timeout=timeout), operation="get_profile")

    async def dump_effective(self, profile_name: Optional[str] = None, *, timeout: float = 5.0) -> Dict[str, Any]:
        """Return effective config.

        - profile_name is None: same as dump() (active profile + env override).
        - profile_name is a string: base + that profile overlay.
        """

        if profile_name is None:
            return await self._dump_async(timeout=timeout)

        if not hasattr(self.ctx, "get_own_effective_config"):
            raise PluginConfigError("ctx.get_own_effective_config is not available", operation="dump_effective")
        try:
            res = await self.ctx.get_own_effective_config(profile_name, timeout=timeout)
        except Exception as e:
            raise PluginConfigError(
                f"Failed to read effective config for profile '{profile_name}': {e}",
                operation="dump_effective",
            ) from e
        return self._unwrap(res, operation="dump_effective")

    def dump_effective_sync(self, profile_name: Optional[str] = None, *, timeout: float = 5.0) -> Dict[str, Any]:
        return self._run_sync(self.dump_effective(profile_name, timeout=timeout), operation="dump_effective")

    async def _get_async(self, path: str, default: Any = _MISSING, *, timeout: float = 5.0) -> Any:
        cfg = await self._dump_async(timeout=timeout)
        try:
            return _get_by_path(cfg, path)
        except PluginConfigError:
            if default is _MISSING:
                raise
            return default

    @overload
    def get(self, path: str, default: Any = ..., *, timeout: float = ...) -> Coroutine[Any, Any, Any]: ...
    @overload
    def get(self, path: str, default: Any = ..., *, timeout: float = ...) -> Any: ...
    
    def get(self, path: str, default: Any = _MISSING, *, timeout: float = 5.0) -> "Union[Any, Coroutine[Any, Any, Any]]":
        """智能代理：自动检测执行环境，选择同步或异步执行方式。
        
        Args:
            path: 配置路径，例如 "debug.enabled"
            default: 默认值（未找到时返回）
            timeout: 超时时间
        """
        coro = self._get_async(path, default=default, timeout=timeout)
        if self._is_in_event_loop():
            return coro
        return self._run_sync(coro, operation="get")

    def get_sync(self, path: str, default: Any = _MISSING, *, timeout: float = 5.0) -> Any:
        return self._run_sync(self._get_async(path, default=default, timeout=timeout), operation="get")

    async def require(self, path: str, *, timeout: float = 5.0) -> Any:
        cfg = await self._dump_async(timeout=timeout)
        return _get_by_path(cfg, path)

    def require_sync(self, path: str, *, timeout: float = 5.0) -> Any:
        return self._run_sync(self.require(path, timeout=timeout), operation="require")

    async def _update_async(self, patch: Dict[str, Any], *, timeout: float = 10.0) -> Dict[str, Any]:
        if not isinstance(patch, dict):
            raise PluginConfigError("patch must be a dict", operation="update")
        if not hasattr(self.ctx, "update_own_config"):
            raise PluginConfigError("ctx.update_own_config is not available", operation="update")
        try:
            updated = await self.ctx.update_own_config(updates=patch, timeout=timeout)
        except Exception as e:
            raise PluginConfigError(f"Failed to update config: {e}", operation="update") from e
        return self._unwrap(updated, operation="update")

    @overload
    def update(self, patch: Dict[str, Any], *, timeout: float = ...) -> Coroutine[Any, Any, Dict[str, Any]]: ...
    @overload
    def update(self, patch: Dict[str, Any], *, timeout: float = ...) -> Dict[str, Any]: ...
    
    def update(self, patch: Dict[str, Any], *, timeout: float = 10.0) -> "Union[Dict[str, Any], Coroutine[Any, Any, Dict[str, Any]]]":
        """智能代理：自动检测执行环境，选择同步或异步执行方式。
        
        Args:
            patch: 要更新的配置字典
            timeout: 超时时间
        """
        coro = self._update_async(patch, timeout=timeout)
        if self._is_in_event_loop():
            return coro
        return self._run_sync(coro, operation="update")

    def update_sync(self, patch: Dict[str, Any], *, timeout: float = 10.0) -> Dict[str, Any]:
        return self._run_sync(self._update_async(patch, timeout=timeout), operation="update")

    async def _set_async(self, path: str, value: Any, *, timeout: float = 10.0) -> Dict[str, Any]:
        patch: Dict[str, Any] = {}
        _set_by_path(patch, path, value)
        return await self._update_async(patch, timeout=timeout)

    @overload
    def set(self, path: str, value: Any, *, timeout: float = ...) -> Coroutine[Any, Any, Dict[str, Any]]: ...
    @overload
    def set(self, path: str, value: Any, *, timeout: float = ...) -> Dict[str, Any]: ...
    
    def set(self, path: str, value: Any, *, timeout: float = 10.0) -> "Union[Dict[str, Any], Coroutine[Any, Any, Dict[str, Any]]]":
        """智能代理：自动检测执行环境，选择同步或异步执行方式。
        
        Args:
            path: 配置路径，例如 "debug.enabled"
            value: 要设置的值
            timeout: 超时时间
        """
        coro = self._set_async(path, value, timeout=timeout)
        if self._is_in_event_loop():
            return coro
        return self._run_sync(coro, operation="set")

    def set_sync(self, path: str, value: Any, *, timeout: float = 10.0) -> Dict[str, Any]:
        return self._run_sync(self._set_async(path, value, timeout=timeout), operation="set")

    async def get_section(self, path: str, *, timeout: float = 5.0) -> Dict[str, Any]:
        value = await self._get_async(path, default=None, timeout=timeout)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise PluginConfigError(
                f"Config section '{path}' is not a dict",
                path=path,
                operation="get_section",
            )
        return value

    def get_section_sync(self, path: str, *, timeout: float = 5.0) -> Dict[str, Any]:
        return self._run_sync(self.get_section(path, timeout=timeout), operation="get_section")
