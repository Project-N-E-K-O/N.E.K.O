"""Shared filesystem locations for the isolated Theater runtimes."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def theater_root(config_manager: Any) -> Path:
    """Resolve the one private root shared by Theater subsystems."""

    app_docs_dir = getattr(config_manager, "app_docs_dir", None)
    if app_docs_dir:
        return Path(app_docs_dir) / "theater"
    config_dir = getattr(config_manager, "config_dir", None)
    if config_dir:
        return Path(config_dir).parent / "theater"
    return Path("data") / "theater"


__all__ = ["theater_root"]
