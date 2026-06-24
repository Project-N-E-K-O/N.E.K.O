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

"""User-configurable activity tracker preferences.

Reads the ``activity`` sub-dict from ``user_preferences.json``'s
``__global_conversation__`` entry — see
``utils/preferences.py:GLOBAL_CONVERSATION_KEY`` for the file shape.

The on-disk file is always a JSON ARRAY whose entries are dicts; one
entry has ``model_path == "__global_conversation__"`` and holds the
global settings. The activity sub-dict lives there:

```json
[
  {
    "model_path": "__global_conversation__",
    "proactiveChatEnabled": true,
    "...other existing global settings...": "...",
    "activity": {
      "thresholds": {
        "away_idle_seconds": 900,
        "stale_recovery_seconds": 60,
        "voice_active_window_seconds": 8,
        "focused_work_min_dwell_seconds": 90,
        "focused_work_recent_input_seconds": 300,
        "casual_browsing_min_dwell_seconds": 30,
        "focused_video_min_dwell_seconds": 120,
        "window_switch_transition_threshold": 5,
        "window_history_lookback_seconds": 300,
        "transition_recent_window_seconds": 30,
        "unfinished_thread_window_seconds": 300,
        "unfinished_thread_max_followups": 2,
        "gaming_gpu_threshold_percent": 60,
        "gaming_gpu_max_idle_seconds": 60,
        "work_break_minutes": 30,
        "work_break_pending_window_seconds": 300,
        "anti_slack_min_focus_minutes": 5,
        "anti_slack_cooldown_minutes": 15,
        "anti_slack_pending_window_seconds": 300
      },
      "work_break_game_invite_probability": 0.5,
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
  },
  {"model_path": "...some 3D model path...", "...": "..."}
]
```

All fields are optional; missing entries fall through to code defaults
in ``main_logic/activity/state_machine.py``.

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
import math
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

# Categories users may target via ``user_app_overrides`` /
# ``user_title_overrides``. ``private`` and ``own_app`` are intentionally
# EXCLUDED — those are static-keyword-only categories with security /
# correctness contracts that user-supplied overrides shouldn't be able
# to forge:
#   * ``private`` — privacy lockdown comes with extra guarantees
#     (window data scrubbed, LLM enrichment bypassed). The state machine
#     additionally treats static-DB private hits as "locked" so user
#     overrides can't downgrade them. Letting users mint NEW private
#     entries via override would be confusing — accept it here, but
#     state_machine's private-locked semantics only apply to static-DB
#     hits, so the override would never actually trigger the privacy
#     bypass. Reject at load time so the asymmetry doesn't surprise.
#   * ``own_app`` — same reasoning. The catgirl-app exclusion is a
#     codebase invariant, not a user setting.
_VALID_CATEGORIES = frozenset({
    'gaming', 'work', 'entertainment', 'communication',
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

    # Probability that a fired water-break reminder pivots into a "rest +
    # mini-game invite" branch instead of the regular drink/stretch nudge.
    # Lives outside ``thresholds`` because 0 is a meaningful value here
    # ("disable the game-invite branch entirely") and ``_parse_thresholds``
    # rejects non-positive numbers. None == use code default (0.5).
    work_break_game_invite_probability: float | None = None


# ── Module-level cache ────────────────────────────────────────────────
#
# Bundled into one mutable holder rather than four module-level globals
# so each cache update is an attribute write — this way the cross-call
# state is structurally explicit, and intra-function flow analysis (e.g.
# CodeQL's "unused global" rule) doesn't keep flagging the assignments
# as dead just because they aren't read again in the same function body.


class _CacheState:
    """Cross-invocation cache for ``get_activity_preferences``.

    Single-instance holder. ``prefs`` always returns the most recently
    successfully loaded value (or the all-defaults fallback). The other
    three fields gate when to skip an expensive reload.
    """

    __slots__ = ('prefs', 'fetched_at', 'path', 'mtime')

    def __init__(self) -> None:
        self.prefs: ActivityPreferences = ActivityPreferences()
        self.fetched_at: float = 0.0
        self.path: str | None = None
        self.mtime: float | None = None

    def reset_metadata(self) -> None:
        """Force the next ``get_activity_preferences`` call to re-read the file."""
        self.fetched_at = 0.0
        self.path = None
        self.mtime = None


_cache_lock = threading.Lock()
_cache = _CacheState()


def get_activity_preferences() -> ActivityPreferences:
    """Return cached preferences, reloading if stale or file changed.

    Cheap to call frequently — the actual JSON read happens at most once
    per ``_PREFERENCES_RELOAD_INTERVAL_SECONDS``. Always returns a valid
    object; on parse failure the cache stays on its previous value (or
    defaults if there's never been a successful load).
    """
    now = time.time()
    with _cache_lock:
        if (
            _cache.fetched_at
            and now - _cache.fetched_at < _PREFERENCES_RELOAD_INTERVAL_SECONDS
        ):
            return _cache.prefs

        path = _resolve_preferences_path()
        if path is None:
            _cache.prefs = ActivityPreferences()
            _cache.fetched_at = now
            _cache.path = None
            _cache.mtime = None
            return _cache.prefs

        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None

        # If the path AND mtime are unchanged, skip the parse and just
        # advance the freshness timestamp.
        if path == _cache.path and mtime == _cache.mtime and mtime is not None:
            _cache.fetched_at = now
            return _cache.prefs

        prefs = _load_from_file(path)
        if prefs is None:
            # Parse / read failed (file mid-edit, malformed JSON, etc).
            # Keep the previously cached prefs intact rather than wiping
            # them with defaults — a transient bad write shouldn't
            # silently disable all the user's overrides until the next
            # successful save. Advance ``fetched_at`` so we don't retry
            # on every call; let the next mtime change trigger a real
            # reload.
            _cache.fetched_at = now
            _cache.path = path
            _cache.mtime = mtime
            return _cache.prefs
        _cache.prefs = prefs
        _cache.fetched_at = now
        _cache.path = path
        _cache.mtime = mtime
        return prefs


def invalidate_activity_preferences_cache() -> None:
    """Force the next ``get_activity_preferences()`` call to re-read the file.

    Useful for tests + post-settings-UI-write hooks.
    """
    with _cache_lock:
        _cache.reset_metadata()


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


def _load_from_file(path: str) -> ActivityPreferences | None:
    """Read user_preferences.json and extract the ``activity`` sub-dict.

    Returns:
      * ``ActivityPreferences()`` (all defaults) when the file parses
        successfully but has no activity section — a legitimate "user
        hasn't configured anything" state.
      * Parsed ``ActivityPreferences`` when the activity section is
        present.
      * ``None`` when the read or JSON parse FAILED — caller's contract
        is to keep the previous cached value rather than overwriting
        with defaults. Distinguishing parse-failure from
        no-activity-section is what prevents a transient corrupt write
        from wiping the user's overrides.

    Validation is best-effort within the activity section — invalid
    entries inside it are silently dropped without rejecting the rest.
    We never want a typo to wedge the tracker.
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logger.debug('activity_config: failed to read %s: %s', path, e)
        return None

    if not isinstance(data, list):
        # Legacy dict-shaped preferences file — no global entry to look at,
        # but the file IS valid (just an old shape). Treat as "no activity
        # section configured" → defaults.
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
        work_break_game_invite_probability=_parse_unit_probability(
            section.get('work_break_game_invite_probability'),
        ),
    )


def _parse_unit_probability(raw: Any) -> float | None:
    """Probability value in [0, 1]. None / non-numeric / out-of-range → None.

    Distinct from ``_parse_thresholds`` because 0 is a meaningful value
    (disabled), so the >0 invariant doesn't apply. Returns None when the
    user didn't supply this key, signalling "fall through to the code
    default" rather than "disabled".

    Out-of-range (``< 0`` / ``> 1``) is rejected, NOT clamped: a typo
    like ``2`` or ``-1`` should fall through to the default (0.5) rather
    than silently flip to "always invite" (1.0) or "never invite" (0.0).
    Same fail-soft contract the rest of this module uses for invalid
    user input. Codex P2 review: PR #1226.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    # NaN/Inf would slip past the range checks below (NaN comparisons are
    # always False; +Inf > 1 catches it but -Inf < 0 also does, leaving
    # NaN as the real concern). Reject them up front. CodeRabbit Minor: PR #1226.
    if not math.isfinite(value):
        return None
    if value < 0.0 or value > 1.0:
        return None
    return value


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
        # Fall back to the user's override key when no explicit canonical
        # was supplied, matching the docstring contract on _AppOverride.
        # This keeps the canonical stable across the loader → state machine
        # → snapshot path: downstream displays the user's intended
        # identifier rather than the raw foreground process basename or
        # full window title.
        if canon is None:
            canon = k
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
