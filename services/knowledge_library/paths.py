"""Resolve the personal domain root without initializing application state."""

from pathlib import Path
from typing import Any

from .models import LibraryError


def library_root(config_manager: Any) -> Path:
    """Use the selected app-data root; never fall back to the source checkout."""
    root = getattr(config_manager, "app_docs_dir", None)
    if root is None:
        raise LibraryError("storage_unavailable")
    return Path(root) / "knowledge_library"
