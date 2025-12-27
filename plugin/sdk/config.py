"""Plugin config access helpers.

This module provides a small, developer-friendly API for reading/updating the
plugin's own `plugin.toml` via the main process.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


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

    ctx: Any

    def _unwrap(self, value: Any, *, operation: str) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise PluginConfigError(f"Invalid config type: {type(value)}", operation=operation)
        # The runtime returns a wrapper like:
        # {"success": ..., "plugin_id": ..., "config": <toml_root>, ...}
        # SDK exposes only the toml root to plugin authors.
        inner = value.get("config")
        if inner is None:
            return value
        if not isinstance(inner, dict):
            raise PluginConfigError(f"Invalid config inner type: {type(inner)}", operation=operation)
        return inner

    def dump(self, *, timeout: float = 5.0) -> Dict[str, Any]:
        if not hasattr(self.ctx, "get_own_config"):
            raise PluginConfigError("ctx.get_own_config is not available", operation="dump")
        try:
            cfg = self.ctx.get_own_config(timeout=timeout)
        except Exception as e:
            raise PluginConfigError(f"Failed to read config: {e}", operation="dump") from e
        return self._unwrap(cfg, operation="dump")

    def get(self, path: str, default: Any = _MISSING, *, timeout: float = 5.0) -> Any:
        cfg = self.dump(timeout=timeout)
        try:
            return _get_by_path(cfg, path)
        except PluginConfigError:
            if default is _MISSING:
                raise
            return default

    def require(self, path: str, *, timeout: float = 5.0) -> Any:
        cfg = self.dump(timeout=timeout)
        return _get_by_path(cfg, path)

    def update(self, patch: Dict[str, Any], *, timeout: float = 10.0) -> Dict[str, Any]:
        if not isinstance(patch, dict):
            raise PluginConfigError("patch must be a dict", operation="update")
        if not hasattr(self.ctx, "update_own_config"):
            raise PluginConfigError("ctx.update_own_config is not available", operation="update")
        try:
            updated = self.ctx.update_own_config(updates=patch, timeout=timeout)
        except Exception as e:
            raise PluginConfigError(f"Failed to update config: {e}", operation="update") from e
        return self._unwrap(updated, operation="update")

    def set(self, path: str, value: Any, *, timeout: float = 10.0) -> Dict[str, Any]:
        patch: Dict[str, Any] = {}
        _set_by_path(patch, path, value)
        return self.update(patch, timeout=timeout)

    def get_section(self, path: str, *, timeout: float = 5.0) -> Dict[str, Any]:
        value = self.get(path, default=None, timeout=timeout)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise PluginConfigError(
                f"Config section '{path}' is not a dict",
                path=path,
                operation="get_section",
            )
        return value
