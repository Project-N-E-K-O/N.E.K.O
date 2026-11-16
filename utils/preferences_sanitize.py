# -*- coding: utf-8 -*-
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

"""M-001 sanitation for ``user_preferences.json`` payloads.

This module is a **leaf**: importing it must not initialise the configuration
manager. ``utils.preferences`` runs ``get_config_manager()`` at import time with
``migrate=True``, which settles the runtime root; the launcher imports the legacy
root migration during phase-0, *before* that root is final, so pulling
``utils.preferences`` from there would migrate into the old location and mark the
manager as already migrated.

Dependencies are therefore limited to the standard library plus
``utils.file_utils`` (also side-effect free). The project logger is resolved on
first use, because ``utils.logger_config`` imports ``config``.
"""

import json
import math
import threading
from typing import Any, Dict, List

from utils.file_utils import scrub_non_finite_numbers

_LOGGER = None


def _log():
    """Resolve the project logger on first use, keeping the import side-effect free."""
    global _LOGGER
    if _LOGGER is None:
        from utils.logger_config import get_module_logger

        _LOGGER = get_module_logger(__name__)
    return _LOGGER


GLOBAL_CONVERSATION_KEY = "__global_conversation__"


# ===========================================================================
# M-001 — NaN / Infinity sanitation for user_preferences.json
#
# ``json.load`` accepts the non-standard ``NaN`` / ``Infinity`` literals that
# ``json.dumps`` emits by default, so a poisoned value survives the read and only
# fails later, when the response is serialised with ``allow_nan=False`` (Starlette
# does this) -- a permanent HTTP 500 on GET /api/config/preferences.
#
# Sanitation therefore happens here, in memory, so that every reader is protected.
# The file itself is repaired by the next ordinary save, never by a read:
# ``load_user_preferences`` is called by ``_update_model_preferences_unlocked``
# while it already holds the store lock, so writing here would re-enter it.
#
# The write side needs the same pass for the same reason (M-001 review): a global
# settings writer loads the raw file through
# ``_load_preferences_data_for_write_unlocked`` and hands the whole payload back
# to ``_save_user_preferences_unlocked``, which serialises with
# ``allow_nan=False``. Without ``sanitize_preferences_payload`` one poisoned model
# entry makes every settings save fail, so the file can never be repaired through
# that door. Sanitising the loaded snapshot heals those entries on the next write
# and leaves the write itself untouched: it is still read-modify-write of the same
# file, with the same lock.
#
# Deliberately *not* done here:
# - the ``__global_conversation__`` entry is never dropped, and its settings
#   journal is never discarded (that journal is the pending-settings queue; losing
#   it silently loses user settings). Only the non-finite numbers inside the
#   entry -- including inside the journal's nested payload/settings -- are
#   removed, because a single NaN anywhere in the payload is enough to break
#   serialisation again; the journal's own version/revision/entries structure is
#   preserved so the handoff still reads it.
# - on the read path the journal never reaches this code anyway:
#   ``_without_settings_journal`` strips it from the returned copies before
#   sanitation.
# ===========================================================================

# JavaScript has no integer type; ``JSON.parse`` maps anything above
# ``Number.MAX_SAFE_INTEGER`` to a float and eventually to ``Infinity``, which is
# exactly the failure mode we are removing. So reject by magnitude instead of
# handing a huge int to ``math.isfinite`` (which raises ``OverflowError``).
_MAX_SAFE_INT = 2 ** 53

_PARAMETERS_MAX_KEY_LENGTH = 256

# Optional per-model fields whose values are numeric mappings.
_OPTIONAL_NUMERIC_FIELDS = ('rotation', 'viewport', 'camera_position', 'display')

# Keys that carry numbers inside those mappings. Unknown keys are passed through
# untouched: this code only removes non-finite numbers, it does not police shape.
_NUMERIC_KEYS = frozenset({
    'x', 'y', 'z',
    'qx', 'qy', 'qz', 'qw',
    'targetX', 'targetY', 'targetZ',
    'width', 'height',
    'screenX', 'screenY',
})

# B-4: warn once per (model_path, field) per process. The set is bounded so a
# long-lived, permanently-dirty profile cannot grow it without limit, and it is
# guarded because ``load_user_preferences`` runs both on the event loop (via
# ``asyncio.to_thread``) and on the calling thread while holding the store lock.
_SANITIZE_WARNED: set = set()
_SANITIZE_WARNED_MAX = 64
_SANITIZE_LOCK = threading.Lock()
_SANITIZE_SUPPRESSED = [0]


def is_finite_json_number(value: Any) -> bool:
    """Return True only for a finite JSON number.

    Three cases are rejected explicitly:

    - ``bool``: it is an ``int`` subclass and ``math.isfinite(True)`` is ``True``,
      so ``{"scale": {"x": true}}`` would otherwise be persisted as ``true``.
    - non-finite ``float`` (``NaN`` / ``Infinity`` / ``-Infinity``).
    - ``int`` beyond ``2**53``: ``math.isfinite`` raises ``OverflowError`` for
      these, and JavaScript cannot represent them either.
    """
    try:
        if isinstance(value, bool):
            return False
        if isinstance(value, float):
            return math.isfinite(value)
        if isinstance(value, int):
            return abs(value) <= _MAX_SAFE_INT
        return False
    except (OverflowError, TypeError):
        # Belt and braces: the predicate must never raise for a hostile value on
        # a deserialised payload. ``math.isfinite`` raises ``OverflowError`` for
        # integers past float range, and a subclass can raise ``TypeError`` from
        # ``__abs__``; both mean "not a usable JSON number" here.
        return False


def _note_sanitize_drop(model_path: Any, field: str) -> None:
    """Record one dropped field, at most once per (model_path, field).

    Only the model path and the field name are logged -- never the offending
    value, which may be user data.
    """
    key = (str(model_path), field)
    with _SANITIZE_LOCK:
        if key in _SANITIZE_WARNED:
            return
        if len(_SANITIZE_WARNED) >= _SANITIZE_WARNED_MAX:
            _SANITIZE_SUPPRESSED[0] += 1
            if _SANITIZE_SUPPRESSED[0] == 1:
                _log().warning(
                    "user_preferences.json: further sanitation warnings suppressed "
                    "(tracking limit %s)",
                    _SANITIZE_WARNED_MAX,
                )
            return
        _SANITIZE_WARNED.add(key)
    _log().warning(
        "user_preferences.json: dropped non-finite value (model_path=%r field=%r)",
        key[0],
        field,
    )


def _scrub_numeric_mapping(value: Any, field: str, model_path: Any) -> Any:
    """Copy a numeric mapping without non-finite numbers.

    Only the offending numeric key is removed; nothing else is a reason to drop
    the field or the entry (M-001). A missing ``y``/``z`` therefore leaves the
    other coordinates in place, and a non-mapping value (``None``, a list, a
    string) is returned unchanged instead of being deleted: the read path only
    removes non-finite numbers, it never polices shape.
    """
    if not isinstance(value, dict):
        return value

    cleaned: Dict[str, Any] = {}
    for key, item in value.items():
        if key in _NUMERIC_KEYS and not is_finite_json_number(item):
            _note_sanitize_drop(model_path, f'{field}.{key}')
        else:
            cleaned[key] = item
    return cleaned


def _sanitize_parameters_group(entry: Dict[str, Any], model_path: Any) -> None:
    """Drop ``parameters`` and ``parameters_mode`` together, or leave both.

    ``parameters_mode == 'replace'`` promises that ``parameters`` is a *complete*
    snapshot, so the two fields are one invariant group:

    - a partially kept snapshot would silently change which parameters the user
      has overridden, therefore an invalid entry drops the whole group;
    - a ``parameters_mode`` left behind without ``parameters`` can never validate
      again, so it is dropped with it.
    """
    has_parameters = 'parameters' in entry
    has_mode = 'parameters_mode' in entry
    if not has_parameters and not has_mode:
        return

    valid = has_parameters
    value = entry.get('parameters')
    if valid and not isinstance(value, dict):
        valid = False
    if valid:
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or len(key) > _PARAMETERS_MAX_KEY_LENGTH
                or not is_finite_json_number(item)
            ):
                valid = False
                break

    if valid:
        return

    if has_parameters:
        _note_sanitize_drop(model_path, 'parameters')
        entry.pop('parameters', None)
    if has_mode:
        _note_sanitize_drop(model_path, 'parameters_mode')
        entry.pop('parameters_mode', None)


def _scrub_non_finite(node: Any, model_path: Any, prefix: str = '') -> Any:
    """Recursively remove non-finite numbers while keeping the surrounding shape.

    Used for the ``__global_conversation__`` entry (which must survive, only its
    bad numbers must not) and as the last-resort fallback below. Numbers are
    judged by :func:`is_finite_json_number`, so a value that is fine for JSON but
    not for JavaScript (a boolean, an integer past ``2**53``) is dropped too.
    """
    return scrub_non_finite_numbers(
        node,
        prefix=prefix,
        is_finite=is_finite_json_number,
        on_drop=lambda path: _note_sanitize_drop(model_path, path),
    )


def _sanitize_model_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Return a cleaned model entry.

    Sanitising a read never discards an entry: a partially usable entry still
    belongs to the user, and silently losing it on a *read* is the worse failure
    (M-001). Only the non-finite numbers inside it are removed; a field that is
    absent or not a mapping stays exactly as it was.
    """
    model_path = entry.get('model_path')
    cleaned = dict(entry)

    for field in ('position', 'scale') + _OPTIONAL_NUMERIC_FIELDS:
        if field not in cleaned:
            continue
        cleaned[field] = _scrub_numeric_mapping(cleaned.get(field), field, model_path)

    _sanitize_parameters_group(cleaned, model_path)
    return cleaned


def _ensure_serialisable(entries: List[Any], *, fallback_empty: bool = True) -> List[Dict[str, Any]]:
    """Guarantee that the returned payload cannot fail ``allow_nan=False``.

    This is the post-condition that makes "GET never 500s again" a provable
    property instead of an enumeration of known writers: if anything non-finite
    survived the targeted passes above, strip it now.

    ``fallback_empty`` chooses what happens when even that is not enough, which
    only a payload that did not come from ``json.load`` can cause: a *read*
    reports an empty list (the file on disk is untouched, so nothing is lost),
    while a *write* raises instead of handing back "no entries" -- a writer must
    never blank the user's file because one value was unrepresentable.
    """
    try:
        json.dumps(entries, allow_nan=False)
        return entries
    except (ValueError, TypeError) as exc:
        _log().error(
            "user_preferences.json: sanitised payload is still not serialisable (%s); "
            "stripping remaining non-finite numbers",
            type(exc).__name__,
        )

    stripped = _scrub_non_finite(entries, None)
    try:
        json.dumps(stripped, allow_nan=False)
        return stripped
    except (ValueError, TypeError) as exc:
        if not fallback_empty:
            raise ValueError(
                "preferences payload is not JSON-serialisable even after sanitising"
            ) from exc
        _log().error(
            "user_preferences.json: payload unserialisable after fallback strip; "
            "reporting empty preferences instead of failing the request"
        )
        return []


def _sanitize_entries(entries: Any, *, fallback_empty: bool) -> List[Dict[str, Any]]:
    """Shared entry walk behind the read sanitiser and ``sanitize_preferences_payload``."""
    if not isinstance(entries, list):
        return []

    result: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get('model_path') == GLOBAL_CONVERSATION_KEY:
            result.append(_scrub_non_finite(entry, entry.get('model_path')))
            continue
        result.append(_sanitize_model_entry(entry))
    return _ensure_serialisable(result, fallback_empty=fallback_empty)


def _sanitize_preferences_entries(entries: Any) -> List[Dict[str, Any]]:
    """Sanitize a decoded preferences list in memory.

    Per-field behaviour (see M-001-fix-plan section P0-3):
      - ``position`` / ``scale`` / ``rotation`` / ``viewport`` /
        ``camera_position`` / ``display`` -> drop only the non-finite numeric
        keys; missing keys and non-mapping values are left untouched
      - an entry is never dropped by sanitation: a half-broken entry is still
        the user's data, and the read path must not lose it silently
      - ``parameters`` group invalid -> drop ``parameters`` + ``parameters_mode``
      - ``__global_conversation__`` -> keep the entry, drop only bad numbers
      - the settings journal is *preserved* on the write path (see
        :func:`sanitize_preferences_payload`): only its non-finite numbers are
        removed, its ``version`` / ``revision`` / ``entries`` structure stays

    A read fails closed to an empty list if the payload is still not
    serialisable, because the file itself is untouched; the write path raises
    instead (see ``fallback_empty``).
    """
    return _sanitize_entries(entries, fallback_empty=True)


def sanitize_preferences_payload(payload: Any) -> List[Dict[str, Any]]:
    """Return a cleaned copy of a raw ``user_preferences.json`` payload (M-001).

    ``payload`` is whatever ``json.load`` returned for the file: a list of
    entries, the legacy single-dict form, or ``None`` when the file is unreadable
    or absent. The result is always a list that ``json.dumps(allow_nan=False)``
    accepts, so a caller that reads a payload and writes it back (a migration
    merge, a global-settings update) can no longer fail as a whole because one
    entry still carries a ``NaN`` from an older build.

    What survives matters as much as what is removed:

    - every model entry survives, and so does ``__global_conversation__``; only
      the non-finite numbers inside them are deleted (``_sanitize_model_entry``
      / ``_scrub_non_finite``);
    - the ``_settings_mutation_journal`` inside the global entry is scrubbed in
      place but never dropped: ``payload`` / ``settings`` and friends may carry
      poisoned numbers, while the journal's own ``version`` / ``revision`` /
      ``entries`` structure must stay intact for the settings handoff to work;
    - the ``parameters`` / ``parameters_mode`` invariant is kept, exactly as on
      the read path.

    The input is not modified; containers are copied.

    Unlike the read path, this never answers "no entries" for a payload it could
    not clean: a write that swallowed that answer would replace the user's file
    with an empty list, so such a payload raises ``ValueError`` instead.
    """
    if payload is None:
        return []
    if isinstance(payload, dict):
        payload = [payload]
    elif not isinstance(payload, list):
        return []
    return _sanitize_entries(payload, fallback_empty=False)
