from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException

from plugin.core.state import state
from plugin.settings import PLUGIN_CONFIG_ROOTS


def _resolve_registered_plugin_config_path(plugin_id: str) -> Path | None:
    candidates: list[object] = []

    with state.acquire_plugins_read_lock():
        meta = state.plugins.get(plugin_id)
        if isinstance(meta, dict):
            candidates.append(meta.get("config_path"))
        elif meta is not None:
            candidates.append(getattr(meta, "config_path", None))

    with state.acquire_plugin_hosts_read_lock():
        host = state.plugin_hosts.get(plugin_id)
        if host is not None:
            candidates.append(getattr(host, "config_path", None))

    for candidate in candidates:
        if candidate is None:
            continue
        try:
            path = Path(candidate).resolve()
        except (TypeError, ValueError, OSError, RuntimeError):
            continue
        if path.is_file() and path.name == "plugin.toml":
            return path

    return None


def get_plugin_config_path(plugin_id: str) -> Path:
    if not re.match(r"^[a-zA-Z0-9_-]+$", plugin_id):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid plugin_id: '{plugin_id}'. Only alphanumeric characters, "
                "underscores, and hyphens are allowed."
            ),
        )

    registered_path = _resolve_registered_plugin_config_path(plugin_id)
    if registered_path is not None:
        return registered_path

    for root in PLUGIN_CONFIG_ROOTS:
        config_file = root / plugin_id / "plugin.toml"
        try:
            resolved_path = config_file.resolve()
            root_resolved = root.resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid plugin_id: '{plugin_id}'. {str(exc)}",
            ) from exc

        if hasattr(resolved_path, "is_relative_to"):
            if not resolved_path.is_relative_to(root_resolved):  # type: ignore[attr-defined]
                continue
        else:
            resolved_str = str(resolved_path)
            root_str = str(root_resolved)
            if not resolved_str.startswith(root_str):
                continue

        if config_file.exists():
            return config_file

    raise HTTPException(
        status_code=404,
        detail=f"Plugin '{plugin_id}' configuration not found",
    )
