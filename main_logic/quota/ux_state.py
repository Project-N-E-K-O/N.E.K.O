"""本地 UX 状态（字数累计 / cooldown 时间戳 / 今日已掉落次数）。

文件：``<memory_dir>/quota_ux_state.json``，schema：

    {
        "date": "2026-05-28",        // UTC 当日，跨日自动 reset
        "dropped_count": 2,          // 今日已掉落次数（前端展示用）
        "word_count_accum": 412,     // 自上次 word_count drop 以来累计字数
        "last_drop_at": {            // 各 trigger 的最后掉落时间戳（秒）
            "word_count": 1779880123,
            "keywords":   1779879980
        }
    }

仅一份全局 state（不分 lanlan_name），因为云端配额本就是 user/client 级。
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
    """累加字数；返回累加后的值。"""
    with _lock:
        state = _load()
        state["word_count_accum"] = int(state.get("word_count_accum", 0)) + int(n)
        _save(state)
        return state["word_count_accum"]


def can_trigger(trigger_type: str, cooldown_sec: int) -> bool:
    """同 trigger_type 自上次掉落以来是否超过 cooldown。"""
    with _lock:
        state = _load()
        last = state.get("last_drop_at", {}).get(trigger_type)
        if not isinstance(last, (int, float)):
            return True
        return (time.time() - float(last)) >= cooldown_sec


def record_drop(trigger_type: str, *, reset_word_count: bool = False) -> dict[str, Any]:
    """记录一次掉落（更新 last_drop_at + dropped_count；按需 reset 字数）。返回新 snapshot。"""
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
