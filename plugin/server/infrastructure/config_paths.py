from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException

from plugin.settings import PLUGIN_CONFIG_ROOT


def get_plugin_config_path(plugin_id: str) -> Path:
    if not re.match(r"^[a-zA-Z0-9_-]+$", plugin_id):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid plugin_id: '{plugin_id}'. Only alphanumeric characters, "
                "underscores, and hyphens are allowed."
            ),
        )

    config_file = PLUGIN_CONFIG_ROOT / plugin_id / "plugin.toml"
    try:
        resolved_path = config_file.resolve()
        root_resolved = PLUGIN_CONFIG_ROOT.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid plugin_id: '{plugin_id}'. {str(exc)}",
        ) from exc

    if hasattr(resolved_path, "is_relative_to"):
        if not resolved_path.is_relative_to(root_resolved):  # type: ignore[attr-defined]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid plugin_id: '{plugin_id}'. Path traversal detected.",
            )
    else:
        resolved_str = str(resolved_path)
        root_str = str(root_resolved)
        if not resolved_str.startswith(root_str):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid plugin_id: '{plugin_id}'. Path traversal detected.",
            )

    if not config_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' configuration not found",
        )
    return config_file
