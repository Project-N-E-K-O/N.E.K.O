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

    def dump(self, *, timeout: float = 5.0) -> Dict[str, Any]:
        if not hasattr(self.ctx, "get_own_config"):
            raise PluginConfigError("ctx.get_own_config is not available", operation="dump")
        try:
            cfg = self.ctx.get_own_config(timeout=timeout)
        except Exception as e:
            raise PluginConfigError(f"Failed to read config: {e}", operation="dump") from e
        return self._unwrap(cfg, operation="dump")

    def dump_base(self, *, timeout: float = 5.0) -> Dict[str, Any]:
        """Return base config (plugin.toml without profile overlay)."""

        if not hasattr(self.ctx, "get_own_base_config"):
            raise PluginConfigError("ctx.get_own_base_config is not available", operation="dump_base")
        try:
            res = self.ctx.get_own_base_config(timeout=timeout)
        except Exception as e:
            raise PluginConfigError(f"Failed to read base config: {e}", operation="dump_base") from e
        return self._unwrap(res, operation="dump_base")

    def get_profiles_state(self, *, timeout: float = 5.0) -> Dict[str, Any]:
        """Return profiles.toml state (active + files mapping)."""

        if not hasattr(self.ctx, "get_own_profiles_state"):
            raise PluginConfigError("ctx.get_own_profiles_state is not available", operation="get_profiles_state")
        try:
            res = self.ctx.get_own_profiles_state(timeout=timeout)
        except Exception as e:
            raise PluginConfigError(f"Failed to read profiles state: {e}", operation="get_profiles_state") from e
        if not isinstance(res, dict):
            raise PluginConfigError(f"Invalid profiles state type: {type(res)}", operation="get_profiles_state")
        if "data" in res and isinstance(res.get("data"), dict):
            res = res["data"]
        return res

    def get_profile(self, profile_name: str, *, timeout: float = 5.0) -> Dict[str, Any]:
        """Return a single profile overlay config."""

        if not hasattr(self.ctx, "get_own_profile_config"):
            raise PluginConfigError("ctx.get_own_profile_config is not available", operation="get_profile")
        try:
            res = self.ctx.get_own_profile_config(profile_name, timeout=timeout)
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

    def dump_effective(self, profile_name: Optional[str] = None, *, timeout: float = 5.0) -> Dict[str, Any]:
        """Return effective config.

        - profile_name is None: same as dump() (active profile + env override).
        - profile_name is a string: base + that profile overlay.
        """

        if profile_name is None:
            return self.dump(timeout=timeout)

        if not hasattr(self.ctx, "get_own_effective_config"):
            raise PluginConfigError("ctx.get_own_effective_config is not available", operation="dump_effective")
        try:
            res = self.ctx.get_own_effective_config(profile_name, timeout=timeout)
        except Exception as e:
            raise PluginConfigError(
                f"Failed to read effective config for profile '{profile_name}': {e}",
                operation="dump_effective",
            ) from e
        return self._unwrap(res, operation="dump_effective")

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
