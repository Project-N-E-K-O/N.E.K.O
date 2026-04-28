"""User-configurable activity tracker preferences.

Reads the ``activity`` sub-dict from ``user_preferences.json``'s
``__global_conversation__`` entry — see
``utils/preferences.py:GLOBAL_CONVERSATION_KEY`` for the file shape.

Schema (all fields optional; missing fields fall through to code defaults
in ``main_logic/activity/state_machine.py``):

```
{
  "model_path": "__global_conversation__",
  ... existing global settings ...
  "activity": {
    "thresholds": {
      "away_idle_seconds": 900,
      "stale_recovery_seconds": 60,
      "voice_active_window_seconds": 8,
      "focused_work_min_dwell_seconds": 90,
      "focused_work_recent_input_seconds": 300,
      "casual_browsing_min_dwell_seconds": 30,
      "window_switch_transition_threshold": 5,
      "window_history_lookback_seconds": 300,
      "transition_recent_window_seconds": 30,
      "unfinished_thread_window_seconds": 300,
      "unfinished_thread_max_followups": 2,
      "gaming_gpu_threshold_percent": 60,
      "gaming_gpu_max_idle_seconds": 60
    },
    "user_app_overrides": {
      "MyCompanyApp.exe": {"category": "work", "subcategory": "office", "canonical": "MyCompanyApp"},
      "OurGameLauncher.exe": {"category": "gaming", "subcategory": "game"}
    },
    "user_title_overrides": {
      "MyCustomTitle": {"category": "work", "subcategory": "office", "canonical": "Custom"}
    },
    "user_game_overrides": {
      "Elden Ring": {"intensity": "casual", "genre": "rpg"}
    },
    "skip_probability_overrides": {
      "competitive": 0.5,
      "immersive_horror": 1.0,
      "casual": 0.0
    }
  }
}
```

Why a separate loader (not reusing
``utils/preferences.load_global_conversation_settings``):

* That function is whitelist-filtered (``_ALLOWED_CONVERSATION_SETTINGS``)
  and would drop the ``activity`` sub-dict.
* Adding ``activity`` to the whitelist + extending the per-field validator
  to handle nested structure couples this subsystem to the cloudsave
  write path. We don't need a write path yet — users edit the file
  directly. Add one when a UI needs it.

Caching: the file is read at most once per
``_PREFERENCES_RELOAD_INTERVAL_SECONDS`` (default 30s) per process. Edits
to ``user_preferences.json`` take effect on the next reload tick.
``invalidate_activity_preferences_cache()`` is exposed for tests + for
explicit reload after settings UI writes.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from utils.config_manager import get_config_manager

logger = logging.getLogger(__name__)


_PREFERENCES_RELOAD_INTERVAL_SECONDS = 30.0
_GLOBAL_CONVERSATION_KEY = '__global_conversation__'

# Whitelisted intensity / genre values — invalid entries are silently
# dropped from user overrides so a typo doesn't poison classification.
_VALID_INTENSITIES = frozenset({'competitive', 'casual', 'immersive', 'varied'})
_VALID_GENRES = frozenset({
    'fps', 'moba', 'rpg', 'sim', 'horror', 'racing', 'rhythm',
    'strategy', 'sports', 'party', 'action', 'misc',
})
_VALID_CATEGORIES = frozenset({
    'gaming', 'work', 'entertainment', 'communication', 'private', 'own_app',
})


@dataclass(frozen=True, slots=True)
class _AppOverride:
    """One user-supplied app classification override.

    ``subcategory`` and ``canonical`` are optional — the loader falls
    back to the override key (the app's identifier) if canonical is
    missing.
    """
    category: str
    subcategory: str | None = None
    canonical: str | None = None


@dataclass(frozen=True, slots=True)
class _GameOverride:
    """One user-supplied game intensity / genre override."""
    intensity: str | None = None
    genre: str | None = None


@dataclass(frozen=True, slots=True)
class ActivityPreferences:
    """Resolved activity tracker preferences.

    All fields have safe defaults — accessing the dataclass when
    ``user_preferences.json`` is missing or empty returns ``ActivityPreferences()``,
    which in turn means the state machine falls through to its hard-coded
    defaults.
    """

    # Threshold overrides — None means "use code default in state_machine.py".
    # Names map 1:1 to the constants at the top of state_machine.py.
    thresholds: dict[str, float] = field(default_factory=dict)

    # Process-name → override. Lookup is case-insensitive (loader lowercases keys).
    user_app_overrides: dict[str, _AppOverride] = field(default_factory=dict)

    # Window-title-substring → override. Lookup is case-insensitive.
    user_title_overrides: dict[str, _AppOverride] = field(default_factory=dict)

    # Game canonical-name → intensity/genre override. Patches the result
    # of GAME_TITLE_KEYWORDS classification before state machine derivation.
    user_game_overrides: dict[str, _GameOverride] = field(default_factory=dict)

    # Skip probability overrides. Keys are intensity-only ('competitive')
    # or intensity_genre ('immersive_horror'); values in [0, 1].
    skip_probability_overrides: dict[str, float] = field(default_factory=dict)


# ── Module-level cache ────────────────────────────────────────────────

_cache_lock = threading.Lock()
_cached_prefs: ActivityPreferences = ActivityPreferences()
_cached_at: float = 0.0
_cached_path: str | None = None
_cached_mtime: float | None = None


def get_activity_preferences() -> ActivityPreferences:
    """Return cached preferences, reloading if stale or file changed.

    Cheap to call frequently — the actual JSON read happens at most once
    per ``_PREFERENCES_RELOAD_INTERVAL_SECONDS``. Always returns a valid
    object; on parse failure the cache stays on its previous value (or
    defaults if there's never been a successful load).
    """
    global _cached_prefs, _cached_at, _cached_path, _cached_mtime
    now = time.time()
    with _cache_lock:
        if (
            _cached_at
            and now - _cached_at < _PREFERENCES_RELOAD_INTERVAL_SECONDS
        ):
            return _cached_prefs

        path = _resolve_preferences_path()
        if path is None:
            _cached_prefs = ActivityPreferences()
            _cached_at = now
            _cached_path = None
            _cached_mtime = None
            return _cached_prefs

        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None

        # If the path AND mtime are unchanged, skip the parse and just
        # advance the freshness timestamp.
        if path == _cached_path and mtime == _cached_mtime and mtime is not None:
            _cached_at = now
            return _cached_prefs

        prefs = _load_from_file(path)
        _cached_prefs = prefs
        _cached_at = now
        _cached_path = path
        _cached_mtime = mtime
        return prefs


def invalidate_activity_preferences_cache() -> None:
    """Force the next ``get_activity_preferences()`` call to re-read the file.

    Useful for tests + post-settings-UI-write hooks.
    """
    global _cached_at, _cached_path, _cached_mtime
    with _cache_lock:
        _cached_at = 0.0
        _cached_path = None
        _cached_mtime = None


def _resolve_preferences_path() -> str | None:
    """Pick the live preferences file path.

    Mirrors ``utils/preferences.py`` — prefer the runtime (writable)
    path; fall back to the read path only if runtime is missing
    (covers fresh installs that haven't migrated yet).
    """
    try:
        cm = get_config_manager()
        write_path = str(cm.get_runtime_config_path('user_preferences.json'))
        if os.path.exists(write_path):
            return write_path
        read_path = str(cm.get_config_path('user_preferences.json'))
        if os.path.exists(read_path):
            return read_path
    except Exception as e:
        logger.debug('activity_config: cannot resolve preferences path: %s', e)
    return None


def _load_from_file(path: str) -> ActivityPreferences:
    """Read user_preferences.json and extract the ``activity`` sub-dict.

    Returns ``ActivityPreferences()`` (all defaults) on any error or if
    the activity section is absent. Validation is best-effort — invalid
    entries inside the section are silently dropped without rejecting
    the rest of the section. We never want a typo to wedge the tracker.
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logger.debug('activity_config: failed to read %s: %s', path, e)
        return ActivityPreferences()

    if not isinstance(data, list):
        # Legacy dict-shaped preferences file — no global entry to look at.
        return ActivityPreferences()

    activity_dict: dict | None = None
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if entry.get('model_path') == _GLOBAL_CONVERSATION_KEY:
            sub = entry.get('activity')
            if isinstance(sub, dict):
                activity_dict = sub
            break

    if activity_dict is None:
        return ActivityPreferences()

    return _parse_activity_section(activity_dict)


def _parse_activity_section(section: dict) -> ActivityPreferences:
    """Extract + validate fields from a raw activity sub-dict."""
    return ActivityPreferences(
        thresholds=_parse_thresholds(section.get('thresholds')),
        user_app_overrides=_parse_app_overrides(section.get('user_app_overrides')),
        user_title_overrides=_parse_app_overrides(section.get('user_title_overrides')),
        user_game_overrides=_parse_game_overrides(section.get('user_game_overrides')),
        skip_probability_overrides=_parse_skip_overrides(
            section.get('skip_probability_overrides'),
        ),
    )


def _parse_thresholds(raw: Any) -> dict[str, float]:
    """Threshold values must be positive numbers. Drop anything else."""
    out: dict[str, float] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, bool):
            continue  # bool is a subclass of int — exclude explicitly
        if not isinstance(v, (int, float)):
            continue
        if v <= 0:
            continue
        out[k] = float(v)
    return out


def _parse_app_overrides(raw: Any) -> dict[str, _AppOverride]:
    """Process or title overrides: ``{key: {category, subcategory?, canonical?}}``.

    Keys are lowercased for case-insensitive lookup. Categories outside
    ``_VALID_CATEGORIES`` are dropped.
    """
    out: dict[str, _AppOverride] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if not isinstance(k, str) or not k:
            continue
        if not isinstance(v, dict):
            continue
        cat = v.get('category')
        if cat not in _VALID_CATEGORIES:
            continue
        sub = v.get('subcategory')
        canon = v.get('canonical')
        if sub is not None and not isinstance(sub, str):
            sub = None
        if canon is not None and not isinstance(canon, str):
            canon = None
        out[k.lower()] = _AppOverride(
            category=cat,
            subcategory=sub,
            canonical=canon,
        )
    return out


def _parse_game_overrides(raw: Any) -> dict[str, _GameOverride]:
    """Game canonical-name → intensity/genre override.

    Keys preserved as given (matched by canonical name from the keyword
    DB, which is case-sensitive). Invalid intensity / genre values are
    dropped; an entry with both invalid is omitted entirely.
    """
    out: dict[str, _GameOverride] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if not isinstance(k, str) or not k:
            continue
        if not isinstance(v, dict):
            continue
        intensity = v.get('intensity')
        genre = v.get('genre')
        if intensity is not None and intensity not in _VALID_INTENSITIES:
            intensity = None
        if genre is not None and genre not in _VALID_GENRES:
            genre = None
        if intensity is None and genre is None:
            continue
        out[k] = _GameOverride(intensity=intensity, genre=genre)
    return out


def _parse_skip_overrides(raw: Any) -> dict[str, float]:
    """Skip probability overrides: ``{combo_key: float ∈ [0, 1]}``.

    Keys aren't strictly validated — they're consumed by
    ``snapshot.derive_skip_probability`` which has its own format
    expectation. Out-of-range values are clamped.
    """
    out: dict[str, float] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, bool):
            continue
        if not isinstance(v, (int, float)):
            continue
        clamped = max(0.0, min(1.0, float(v)))
        out[k] = clamped
    return out


__all__ = [
    'ActivityPreferences',
    'get_activity_preferences',
    'invalidate_activity_preferences_cache',
]
