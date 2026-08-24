"""Local quota UX state for word counts, cooldowns, and daily drop counts.

File: ``<memory_dir>/quota_ux_state.json``. Schema:

    {
        "date": "2026-05-28",        // current UTC date; resets daily
        "dropped_count": 2,          // drops recorded today for frontend display
        "word_count_accum": 412,     // words accumulated since the last drop
        "last_drop_at": {            // last drop timestamp per trigger type
            "word_count": 1779880123,
            "keywords":   1779879980
        }
    }

The state is global rather than per-character because cloud quotas are scoped to
the user/client.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("neko.quota.ux_state")

_lock = threading.Lock()


def _utc_today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _empty_state() -> dict[str, Any]:
    return {
        "date": _utc_today_str(),
        "dropped_count": 0,
        "word_count_accum": 0,
        "last_drop_at": {},
    }


def _state_path() -> Path | None:
    try:
        from utils.config_manager import get_config_manager

        cm = get_config_manager()
        memory_dir = Path(cm.memory_dir)
        memory_dir.mkdir(parents=True, exist_ok=True)
        return memory_dir / "quota_ux_state.json"
    except Exception as exc:  # noqa: BLE001
        logger.debug("ux_state: cannot resolve memory_dir: %s", exc)
        return None


def _load() -> dict[str, Any]:
    path = _state_path()
    if path is None or not path.exists():
        return _empty_state()
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _empty_state()
        # 跨日 reset
        if data.get("date") != _utc_today_str():
            return _empty_state()
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("ux_state: load failed: %s", exc)
        return _empty_state()


def _save(state: dict[str, Any]) -> None:
    path = _state_path()
    if path is None:
        return
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ux_state: save failed: %s", exc)


# ---- public API ----


def get_snapshot() -> dict[str, Any]:
    with _lock:
        return _load()


def add_word_count(n: int) -> int:
    """Add words to the accumulator and return the new value."""
    with _lock:
        state = _load()
        state["word_count_accum"] = int(state.get("word_count_accum", 0)) + int(n)
        _save(state)
        return state["word_count_accum"]


def can_trigger(trigger_type: str, cooldown_sec: int) -> bool:
    """Return whether this trigger type has passed its cooldown."""
    with _lock:
        state = _load()
        last = state.get("last_drop_at", {}).get(trigger_type)
        if not isinstance(last, (int, float)):
            return True
        return (time.time() - float(last)) >= cooldown_sec


def record_drop(trigger_type: str, *, reset_word_count: bool = False) -> dict[str, Any]:
    """Record one drop and return the updated state snapshot."""
    with _lock:
        state = _load()
        last_drop_at = dict(state.get("last_drop_at") or {})
        last_drop_at[trigger_type] = int(time.time())
        state["last_drop_at"] = last_drop_at
        state["dropped_count"] = int(state.get("dropped_count", 0)) + 1
        if reset_word_count:
            state["word_count_accum"] = 0
        _save(state)
        return state
