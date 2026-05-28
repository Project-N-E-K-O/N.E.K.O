from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from ..memory_deck_store import MemoryDeckStore, ensure_memory_schema
from ..mode_manager import normalize_mode
from ..models import (
    STORE_CONFIG,
    STORE_STATE,
    StudyConfig,
    StudyState,
    build_config,
    json_copy,
)

_DEFAULT_APPEND_ONLY_HISTORY_LIMIT = 5000


def safe_float(value: Any, default: Any = 0.0) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


__all__ = [name for name in globals() if not name.startswith("__") and name != "name"]
