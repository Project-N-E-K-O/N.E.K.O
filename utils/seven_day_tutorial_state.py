# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Authoritative persistence for the Day 1-7 tutorial progress."""

from __future__ import annotations

import re
import threading
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json
from utils.prompt_state.core import load_state_file

SEVEN_DAY_TUTORIAL_STATE_FILENAME = "seven_day_tutorial_state.json"
LEGACY_TUTORIAL_PROMPT_STATE_FILENAME = "tutorial_prompt.json"
SEVEN_DAY_TUTORIAL_SCHEMA_VERSION = 2
SEVEN_DAY_TUTORIAL_STORE_VERSION = 1
ROUND_COUNT = 7
RESET_HISTORY_LIMIT = 20

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STATE_LOCK = threading.RLock()


class SevenDayTutorialStateConflict(Exception):
    """Raised when a client replaces a state from a stale revision."""

    def __init__(self, current_store: dict[str, Any]):
        super().__init__("seven-day tutorial state revision conflict")
        self.current_store = deepcopy(current_store)


def _clean_string(value: Any, *, limit: int = 128) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _normalize_round(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        round_number = int(value)
    except (TypeError, ValueError):
        return None
    return round_number if 1 <= round_number <= ROUND_COUNT else None


def _normalize_round_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return sorted({round_number for item in value if (round_number := _normalize_round(item))})


def _normalize_date(value: Any, *, default: str = "") -> str:
    raw = _clean_string(value, limit=10)
    if not _DATE_RE.fullmatch(raw):
        return default
    try:
        date.fromisoformat(raw)
    except ValueError:
        return default
    return raw


def _normalize_reset_history(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value[-RESET_HISTORY_LIMIT:]:
        if not isinstance(item, dict):
            continue
        raw_day = item.get("day")
        day_value: int | str | None = "all" if raw_day == "all" else _normalize_round(raw_day)
        if day_value is None:
            continue
        normalized.append({
            "day": day_value,
            "source": _clean_string(item.get("source"), limit=64),
            "resetAt": _clean_string(item.get("resetAt"), limit=64),
        })
    return normalized


def _normalize_end_state(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    round_number = _normalize_round(value.get("day"))
    if round_number is None:
        return None
    outcome = _clean_string(value.get("outcome"), limit=16).lower()
    if outcome not in {"complete", "skip", "destroy"}:
        outcome = "destroy"
    try:
        ended_at = max(0, int(value.get("endedAt") or 0))
    except (TypeError, ValueError):
        ended_at = 0
    return {
        "day": round_number,
        "ended": bool(value.get("ended")),
        "outcome": outcome,
        "rawReason": _clean_string(value.get("rawReason"), limit=64),
        "isAngryExit": bool(value.get("isAngryExit")),
        "completed": outcome == "complete",
        "skipped": outcome == "skip",
        "source": _clean_string(value.get("source"), limit=64),
        "endedAt": ended_at,
    }


def normalize_seven_day_tutorial_state(raw_state: Any) -> dict[str, Any]:
    raw = raw_state if isinstance(raw_state, dict) else {}
    completed_rounds = _normalize_round_list(raw.get("completedRounds"))
    skipped_rounds = [
        round_number
        for round_number in _normalize_round_list(raw.get("skippedRounds"))
        if round_number not in completed_rounds
    ]
    return {
        "version": SEVEN_DAY_TUTORIAL_SCHEMA_VERSION,
        "firstSeenDate": _normalize_date(raw.get("firstSeenDate"), default=date.today().isoformat()),
        "completedRounds": completed_rounds,
        "skippedRounds": skipped_rounds,
        "currentRound": _normalize_round(raw.get("currentRound")),
        "pendingRound": _normalize_round(raw.get("pendingRound")),
        "manualResetRound": _normalize_round(raw.get("manualResetRound")),
        "lastAutoShownRound": _normalize_round(raw.get("lastAutoShownRound")),
        "lastAutoShownDate": _normalize_date(raw.get("lastAutoShownDate")),
        "lastAutoReservationId": _clean_string(raw.get("lastAutoReservationId"), limit=128),
        "lastEndState": _normalize_end_state(raw.get("lastEndState")),
        "legacyMigrationCompleted": True,
        "updatedAt": _clean_string(raw.get("updatedAt"), limit=64) or None,
        "resetHistory": _normalize_reset_history(raw.get("resetHistory")),
    }


def get_seven_day_tutorial_state_path(config_manager=None) -> Path:
    manager = config_manager or get_config_manager()
    return Path(manager.get_config_path(SEVEN_DAY_TUTORIAL_STATE_FILENAME))


def _default_store() -> dict[str, Any]:
    return {
        "storeVersion": SEVEN_DAY_TUTORIAL_STORE_VERSION,
        "initialized": False,
        "revision": 0,
        "state": None,
    }


def _legacy_first_seen_date(raw_state: dict[str, Any]) -> str:
    for key in ("first_seen_at", "completed_at", "manual_home_tutorial_viewed_at"):
        try:
            timestamp_ms = max(0, int(raw_state.get(key) or 0))
        except (TypeError, ValueError):
            timestamp_ms = 0
        if timestamp_ms > 0:
            try:
                return datetime.fromtimestamp(
                    timestamp_ms / 1000,
                    tz=timezone.utc,
                ).date().isoformat()
            except (OverflowError, OSError, ValueError):
                continue
    return date.today().isoformat()


def _legacy_updated_at(raw_state: dict[str, Any]) -> str | None:
    for key in ("completed_at", "manual_home_tutorial_viewed_at", "first_seen_at"):
        try:
            timestamp_ms = max(0, int(raw_state.get(key) or 0))
        except (TypeError, ValueError):
            timestamp_ms = 0
        if timestamp_ms > 0:
            try:
                return datetime.fromtimestamp(
                    timestamp_ms / 1000,
                    tz=timezone.utc,
                ).isoformat()
            except (OverflowError, OSError, ValueError):
                continue
    return None


def _migrate_legacy_tutorial_state(config_manager=None) -> dict[str, Any] | None:
    manager = config_manager or get_config_manager()
    legacy_path = Path(manager.get_config_path(LEGACY_TUTORIAL_PROMPT_STATE_FILENAME))
    legacy_state = load_state_file(legacy_path)
    if not isinstance(legacy_state, dict):
        return None

    completed = legacy_state.get("home_tutorial_completed") is True
    permanently_suppressed = (
        legacy_state.get("never_remind") is True
        or _clean_string(legacy_state.get("status"), limit=16).lower() == "never"
    )
    if not completed and not permanently_suppressed:
        return None

    migrated_state = normalize_seven_day_tutorial_state({
        "firstSeenDate": _legacy_first_seen_date(legacy_state),
        "completedRounds": [1] if completed else [],
        "skippedRounds": [] if completed else [1],
        "updatedAt": _legacy_updated_at(legacy_state),
    })
    store = {
        "storeVersion": SEVEN_DAY_TUTORIAL_STORE_VERSION,
        "initialized": True,
        "revision": 1,
        "state": migrated_state,
    }
    atomic_write_json(
        get_seven_day_tutorial_state_path(manager),
        store,
        encoding="utf-8",
        ensure_ascii=False,
        indent=2,
    )
    return store


def load_seven_day_tutorial_store(config_manager=None) -> dict[str, Any]:
    with _STATE_LOCK:
        raw = load_state_file(get_seven_day_tutorial_state_path(config_manager))
        if not isinstance(raw, dict) or raw.get("initialized") is not True:
            return _migrate_legacy_tutorial_state(config_manager) or _default_store()
        try:
            revision = max(0, int(raw.get("revision") or 0))
        except (TypeError, ValueError):
            revision = 0
        return {
            "storeVersion": SEVEN_DAY_TUTORIAL_STORE_VERSION,
            "initialized": True,
            "revision": revision,
            "state": normalize_seven_day_tutorial_state(raw.get("state")),
        }


def replace_seven_day_tutorial_state(
    raw_state: Any,
    *,
    expected_revision: Any,
    config_manager=None,
) -> dict[str, Any]:
    if not isinstance(raw_state, dict):
        raise ValueError("state must be an object")
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        raise ValueError("expectedRevision must be a non-negative integer")
    normalized_expected_revision = expected_revision
    if normalized_expected_revision < 0:
        raise ValueError("expectedRevision must be a non-negative integer")
    with _STATE_LOCK:
        current = load_seven_day_tutorial_store(config_manager)
        if normalized_expected_revision != current["revision"]:
            raise SevenDayTutorialStateConflict(current)
        store = {
            "storeVersion": SEVEN_DAY_TUTORIAL_STORE_VERSION,
            "initialized": True,
            "revision": current["revision"] + 1,
            "state": normalize_seven_day_tutorial_state(raw_state),
        }
        atomic_write_json(
            get_seven_day_tutorial_state_path(config_manager),
            store,
            encoding="utf-8",
            ensure_ascii=False,
            indent=2,
        )
        return deepcopy(store)


def get_seven_day_tutorial_state_response(*, config_manager=None) -> dict[str, Any]:
    with _STATE_LOCK:
        store = load_seven_day_tutorial_store(config_manager)
    return {"ok": True, **store}
