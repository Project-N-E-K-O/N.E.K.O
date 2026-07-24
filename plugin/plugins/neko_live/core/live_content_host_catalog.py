"""Compatibility aggregate for idle-hosting beat candidates."""

from __future__ import annotations

from json import JSONDecodeError, loads
from pathlib import Path
from typing import Any

from .live_content_host_catalog_choice import CHOICE_IDLE_HOSTING_BEAT_CANDIDATES
from .live_content_host_catalog_callback import (
    VIEWER_CALLBACK_IDLE_HOSTING_BEAT_CANDIDATES,
)
from .live_content_host_catalog_tease import TEASE_IDLE_HOSTING_BEAT_CANDIDATES
from .live_content_host_catalog_challenge import (
    MICRO_CHALLENGE_IDLE_HOSTING_BEAT_CANDIDATES,
)
from .live_content_host_catalog_mood import MOOD_IDLE_HOSTING_BEAT_CANDIDATES

DEFAULT_IDLE_HOSTING_BEAT_CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "idle_hosting_beats.json"
)
_REQUIRED_IDLE_HOSTING_BEAT_FIELDS = (
    "key",
    "live_column",
    "shape",
    "fun_axis",
    "title",
    "hint",
    "reply_affordance",
)

_ALL_IDLE_HOSTING_BEATS: tuple[dict[str, Any], ...] = (
    *CHOICE_IDLE_HOSTING_BEAT_CANDIDATES,
    *VIEWER_CALLBACK_IDLE_HOSTING_BEAT_CANDIDATES,
    *TEASE_IDLE_HOSTING_BEAT_CANDIDATES,
    *MICRO_CHALLENGE_IDLE_HOSTING_BEAT_CANDIDATES,
    *MOOD_IDLE_HOSTING_BEAT_CANDIDATES,
)

_IDLE_HOSTING_BEATS_BY_KEY: dict[str, dict[str, Any]] = {
    item["key"]: item for item in _ALL_IDLE_HOSTING_BEATS
}

_IDLE_HOSTING_BEAT_KEYS: tuple[str, ...] = (
    "idle:soft-observation",
    "idle:tiny-choice",
    "idle:light-tease",
    "idle:small-mood",
    "idle:one-word-call",
    "idle:micro-challenge",
    "idle:prop-choice",
    "idle:cat-radio",
    "idle:screen-blink",
    "idle:cat-paw-button",
    "idle:three-word-password",
    "idle:serious-three-seconds",
    "idle:reverse-tease",
    "idle:quiet-stamp",
    "idle:temperature-word",
    "idle:corner-snack",
    "idle:tail-state-choice",
    "idle:steady-three-sec",
    "idle:keyboard-patrol",
    "idle:one-char-command",
    "idle:air-purr",
    "idle:half-open-drawer",
    "idle:unreliable-award",
    "idle:light-filter-word",
)
_REQUIRED_IDLE_HOSTING_STAGES = frozenset({"settle", "column", "callback"})
_REQUIRED_IDLE_HOSTING_SHAPES = frozenset(
    item["shape"] for item in _ALL_IDLE_HOSTING_BEATS
)
_REQUIRED_IDLE_HOSTING_AXES = frozenset(
    item["fun_axis"] for item in _ALL_IDLE_HOSTING_BEATS
)
_MIN_IDLE_HOSTING_CATALOG_SIZE = len(_IDLE_HOSTING_BEAT_KEYS)


def load_idle_hosting_beat_catalog(path: str | Path = DEFAULT_IDLE_HOSTING_BEAT_CATALOG_PATH) -> tuple[dict[str, Any], ...]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = loads(raw)
    except (OSError, JSONDecodeError, TypeError, ValueError):
        return ()

    raw_entries = data.get("beats") if isinstance(data, dict) else data
    if not isinstance(raw_entries, list):
        return ()

    beats: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for raw_entry in raw_entries:
        beat = _coerce_idle_hosting_beat(raw_entry)
        if not beat:
            return ()
        key = beat["key"]
        if key in seen_keys:
            return ()
        seen_keys.add(key)
        beats.append(beat)
    if not _is_complete_idle_hosting_beat_catalog(beats):
        return ()
    return tuple(beats)


def _is_complete_idle_hosting_beat_catalog(beats: list[dict[str, Any]]) -> bool:
    stages = {_safe_str(beat.get("idle_stage")) for beat in beats}
    shapes = {_safe_str(beat.get("shape")) for beat in beats}
    axes = {_safe_str(beat.get("fun_axis")) for beat in beats}
    return (
        len(beats) >= _MIN_IDLE_HOSTING_CATALOG_SIZE
        and _REQUIRED_IDLE_HOSTING_STAGES <= stages
        and _REQUIRED_IDLE_HOSTING_SHAPES <= shapes
        and _REQUIRED_IDLE_HOSTING_AXES <= axes
    )


def _coerce_idle_hosting_beat(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    beat = {
        field: _safe_str(value.get(field))
        for field in _REQUIRED_IDLE_HOSTING_BEAT_FIELDS
    }
    if any(not beat[field] for field in _REQUIRED_IDLE_HOSTING_BEAT_FIELDS):
        return {}
    idle_stage = _safe_str(value.get("idle_stage"))
    if idle_stage:
        beat["idle_stage"] = idle_stage
    meme_query = _safe_str(value.get("meme_query"))
    if meme_query:
        beat["meme_query"] = meme_query
    return beat


def _safe_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


IDLE_HOSTING_BEAT_CANDIDATES: tuple[dict[str, Any], ...] = tuple(
    load_idle_hosting_beat_catalog(DEFAULT_IDLE_HOSTING_BEAT_CATALOG_PATH)
    or (_IDLE_HOSTING_BEATS_BY_KEY[key] for key in _IDLE_HOSTING_BEAT_KEYS)
)
