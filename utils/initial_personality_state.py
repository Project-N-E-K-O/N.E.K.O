from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json

DEFAULT_INITIAL_PERSONALITY_STATE = {
    "version": 1,
    "status": "pending",
    "handled_at": "",
    "manual_reselect_character_name": "",
    "manual_reselect_requested_at": "",
}

VALID_INITIAL_PERSONALITY_STATUSES = {
    "pending",
    "completed",
    "skipped",
}


def _normalize_state(raw_state: object) -> dict:
    state = deepcopy(DEFAULT_INITIAL_PERSONALITY_STATE)
    if isinstance(raw_state, dict):
        state.update(raw_state)
    state["version"] = 1
    status = str(state.get("status") or "").strip().lower()
    state["status"] = status if status in VALID_INITIAL_PERSONALITY_STATUSES else "pending"
    state["handled_at"] = str(state.get("handled_at") or "").strip()
    state["manual_reselect_character_name"] = str(state.get("manual_reselect_character_name") or "").strip()
    state["manual_reselect_requested_at"] = str(state.get("manual_reselect_requested_at") or "").strip()
    return state


def get_initial_personality_state_path(config_manager=None) -> Path:
    config_manager = config_manager or get_config_manager()
    return Path(config_manager.local_state_dir) / "initial_personality_prompt.json"


def load_initial_personality_state(config_manager=None) -> dict:
    path = get_initial_personality_state_path(config_manager)
    if not path.exists():
        return deepcopy(DEFAULT_INITIAL_PERSONALITY_STATE)
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw_state = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return deepcopy(DEFAULT_INITIAL_PERSONALITY_STATE)
    return _normalize_state(raw_state)


def save_initial_personality_state(state: dict, config_manager=None) -> dict:
    config_manager = config_manager or get_config_manager()
    normalized_state = _normalize_state(state)
    if hasattr(config_manager, "ensure_local_state_directory"):
        config_manager.ensure_local_state_directory()
    atomic_write_json(
        get_initial_personality_state_path(config_manager),
        normalized_state,
        ensure_ascii=False,
        indent=2,
    )
    return normalized_state


def mark_initial_personality_state(
    status: str,
    *,
    config_manager=None,
    now_iso: str | None = None,
) -> dict:
    state = load_initial_personality_state(config_manager)
    state["status"] = str(status or "").strip().lower()
    state["handled_at"] = (
        str(now_iso).strip()
        if now_iso
        else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    return save_initial_personality_state(state, config_manager)


def mark_manual_personality_reselect(
    character_name: str,
    *,
    config_manager=None,
    now_iso: str | None = None,
) -> dict:
    state = load_initial_personality_state(config_manager)
    state["manual_reselect_character_name"] = str(character_name or "").strip()
    state["manual_reselect_requested_at"] = (
        str(now_iso).strip()
        if now_iso
        else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    return save_initial_personality_state(state, config_manager)


def clear_manual_personality_reselect(*, config_manager=None) -> dict:
    state = load_initial_personality_state(config_manager)
    state["manual_reselect_character_name"] = ""
    state["manual_reselect_requested_at"] = ""
    return save_initial_personality_state(state, config_manager)
