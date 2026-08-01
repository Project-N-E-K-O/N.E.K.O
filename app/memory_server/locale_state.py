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

"""Durable per-character prompt locale for long-lived maintenance tasks."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time

from utils.cloudsave_runtime import MaintenanceModeError
from utils.file_utils import atomic_write_json
from utils.language_utils import (
    is_supported_language_code,
    language_context,
    normalize_language_code,
)
from utils.logger_config import get_module_logger


logger = get_module_logger(__name__, "Memory")
_locale_cache: dict[str, tuple[str | None, int | None, int | None]] = {}
_subject_locale_cache: dict[
    str,
    dict[str, tuple[str | None, int | None, int | None]],
] = {}
_locale_locks: dict[str, threading.Lock] = {}
_locale_locks_guard = threading.Lock()
_locale_cache_guard = threading.Lock()
_locale_reload_guard = threading.RLock()
_subject_locale_forget_cutoffs_guard = threading.RLock()
_locale_cache_generation = 0
_subject_locale_forget_cutoffs: dict[tuple[str, str], int] = {}
_subject_locale_forget_cutoffs_loaded = False
_character_locale_admission_orders: dict[str, int] = {}
_subject_locale_admission_orders: dict[tuple[str, str], int] = {}
_locale_admission_orders_guard = threading.Lock()
_character_locale_capture_order = 0
_character_locale_capture_offsets: dict[str, int] = {}


class PromptLocalePersistenceError(RuntimeError):
    """Raised when a prompt-locale sidecar update was not committed."""


class PromptLocaleInvalidatedError(PromptLocalePersistenceError):
    """Raised when cache invalidation races a staged sidecar write."""


def invalidate_prompt_locale_caches() -> None:
    """Force the next locale lookup to reload both durable sidecars."""
    global _locale_cache_generation
    with _locale_reload_guard:
        with _locale_cache_guard:
            _locale_cache_generation += 1
            _locale_cache.clear()
            _subject_locale_cache.clear()
        with _locale_admission_orders_guard:
            # A cloud restore can replace the durable high-water mark.  The
            # next validated request must establish a fresh process-token
            # offset against that state instead of reusing the old epoch.
            _character_locale_capture_offsets.clear()


def _locale_path(name: str) -> str:
    from memory import ensure_character_dir
    from utils.config_manager import get_config_manager

    config_manager = get_config_manager()
    return os.path.join(
        ensure_character_dir(config_manager.memory_dir, name),
        "prompt_locale.json",
    )


def _subject_locale_path(name: str) -> str:
    from memory import ensure_character_dir
    from utils.config_manager import get_config_manager

    config_manager = get_config_manager()
    return os.path.join(
        ensure_character_dir(config_manager.memory_dir, name),
        "scoped_prompt_locales.json",
    )


def _subject_locale_forget_cutoff_path() -> str:
    """Return the local-only tombstone store excluded from cloud snapshots."""
    from utils.config_manager import get_config_manager

    config_manager = get_config_manager()
    config_manager.ensure_local_state_directory()
    return os.path.join(
        config_manager.local_state_dir,
        "scoped_prompt_locale_forget_cutoffs.json",
    )


def _load_subject_locale_forget_cutoffs_unlocked() -> None:
    global _subject_locale_forget_cutoffs_loaded
    with _subject_locale_forget_cutoffs_guard:
        if _subject_locale_forget_cutoffs_loaded:
            return
        loaded: dict[tuple[str, str], int] = {}
        try:
            with open(_subject_locale_forget_cutoff_path(), encoding="utf-8") as handle:
                payload = json.load(handle)
            characters = payload.get("characters") if isinstance(payload, dict) else None
            if isinstance(characters, dict):
                for name, rows in characters.items():
                    if not isinstance(name, str) or not isinstance(rows, dict):
                        continue
                    for key, cutoff in rows.items():
                        if (
                            isinstance(key, str)
                            and isinstance(cutoff, int)
                            and not isinstance(cutoff, bool)
                        ):
                            loaded[(name, key)] = cutoff
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError) as exc:
            # A tombstone read failure must fail closed: treating it as an
            # empty set could revive a scoped locale restored from the cloud.
            raise PromptLocalePersistenceError(
                "scoped prompt locale forget cutoffs could not be loaded"
            ) from exc
        _subject_locale_forget_cutoffs.clear()
        _subject_locale_forget_cutoffs.update(loaded)
        _subject_locale_forget_cutoffs_loaded = True


def _persist_subject_locale_forget_cutoffs_unlocked() -> None:
    with _subject_locale_forget_cutoffs_guard:
        characters: dict[str, dict[str, int]] = {}
        for (name, key), cutoff in _subject_locale_forget_cutoffs.items():
            characters.setdefault(name, {})[key] = cutoff
        try:
            atomic_write_json(
                _subject_locale_forget_cutoff_path(),
                {"version": 1, "characters": characters},
                ensure_ascii=False,
            )
        except Exception as exc:
            raise PromptLocalePersistenceError(
                "scoped prompt locale forget cutoff was not persisted"
            ) from exc


def _assert_prompt_locale_writable(target: str) -> None:
    from utils.cloudsave_runtime import assert_cloudsave_writable
    from utils.config_manager import get_config_manager

    assert_cloudsave_writable(
        get_config_manager(),
        operation="save",
        target=target,
    )


def _prompt_locale_write_transaction(target: str):
    from utils.cloudsave_runtime import cloudsave_writable_transaction
    from utils.config_manager import get_config_manager

    return cloudsave_writable_transaction(
        get_config_manager(),
        operation="save",
        target=target,
    )


def _subject_locale_key(subject) -> str:
    from memory.scopes import coerce_subject

    normalized = coerce_subject(subject)
    if normalized is None:
        raise ValueError("scoped prompt locale requires an explicit subject")
    return json.dumps(
        [normalized.kind, normalized.subject_id, normalized.scope],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _get_locale_lock(name: str) -> threading.Lock:
    if name not in _locale_locks:
        with _locale_locks_guard:
            if name not in _locale_locks:
                _locale_locks[name] = threading.Lock()
    return _locale_locks[name]


def _load_locale_state_unlocked(
    name: str,
) -> tuple[str | None, int | None, int | None]:
    while True:
        with _locale_cache_guard:
            if name in _locale_cache:
                return _locale_cache[name]
            generation = _locale_cache_generation

        selected = None
        order = None
        reserved_order = None
        try:
            with open(_locale_path(name), encoding="utf-8") as handle:
                payload = json.load(handle)
            candidate = payload.get("language") if isinstance(payload, dict) else None
            if is_supported_language_code(candidate):
                selected = normalize_language_code(str(candidate), format="full")
            candidate_order = (
                payload.get("order") if isinstance(payload, dict) else None
            )
            if isinstance(candidate_order, int) and not isinstance(
                candidate_order,
                bool,
            ):
                order = candidate_order
            candidate_reserved = (
                payload.get("reserved_order") if isinstance(payload, dict) else None
            )
            if isinstance(candidate_reserved, int) and not isinstance(
                candidate_reserved,
                bool,
            ):
                reserved_order = candidate_reserved
        except (OSError, json.JSONDecodeError):
            # A missing or partially-written sidecar is equivalent to no saved locale.
            pass
        if order is not None:
            reserved_order = max(reserved_order or order, order)
        loaded = (selected, order, reserved_order)
        with _locale_cache_guard:
            if generation != _locale_cache_generation:
                continue
            _locale_cache[name] = loaded
            return loaded


def _persist_locale_state_unlocked(
    name: str,
    language: str | None,
    order: int | None,
    reserved_order: int | None,
) -> bool:
    path = _locale_path(name)
    with _locale_cache_guard:
        generation = _locale_cache_generation
    staging_path = (
        f"{path}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.pending"
    )
    try:
        atomic_write_json(
            staging_path,
            {
                "language": language,
                "order": order,
                "reserved_order": reserved_order,
            },
            ensure_ascii=False,
        )
        with _prompt_locale_write_transaction("prompt_locale.json"):
            with _locale_cache_guard:
                if generation != _locale_cache_generation:
                    raise PromptLocaleInvalidatedError(
                        "prompt locale write was invalidated"
                    )
                os.replace(staging_path, path)
                _locale_cache[name] = (language, order, reserved_order)
                return True
    except (MaintenanceModeError, PromptLocaleInvalidatedError):
        raise
    except Exception as exc:
        logger.warning(
            "[PromptLocale] %s: persist failed: %s",
            name,
            exc,
        )
        return False
    finally:
        try:
            os.remove(staging_path)
        except FileNotFoundError:
            # Successful os.replace already consumed the staging path.
            pass
        except OSError as exc:
            logger.debug("[PromptLocale] stale staging cleanup failed: %s", exc)


def _load_subject_locale_state_unlocked(
    name: str,
) -> dict[str, tuple[str | None, int | None, int | None]]:
    _load_subject_locale_forget_cutoffs_unlocked()
    while True:
        with _locale_cache_guard:
            if name in _subject_locale_cache:
                return _subject_locale_cache[name]
            generation = _locale_cache_generation

        loaded: dict[str, tuple[str | None, int | None, int | None]] = {}
        try:
            with open(_subject_locale_path(name), encoding="utf-8") as handle:
                payload = json.load(handle)
            rows = payload.get("subjects") if isinstance(payload, dict) else None
            if isinstance(rows, dict):
                for key, row in rows.items():
                    if not isinstance(key, str) or not isinstance(row, dict):
                        continue
                    language = row.get("language")
                    selected = (
                        normalize_language_code(str(language), format="full")
                        if is_supported_language_code(language)
                        else None
                    )
                    order = row.get("order")
                    if not isinstance(order, int) or isinstance(order, bool):
                        order = None
                    reserved_order = row.get("reserved_order")
                    if not isinstance(reserved_order, int) or isinstance(
                        reserved_order,
                        bool,
                    ):
                        reserved_order = None
                    if order is not None:
                        reserved_order = max(reserved_order or order, order)
                    forget_cutoff = _subject_locale_forget_cutoffs.get((name, key))
                    if forget_cutoff is not None and (
                        order is None or order <= forget_cutoff
                    ):
                        continue
                    loaded[key] = (selected, order, reserved_order)
        except (OSError, json.JSONDecodeError):
            # Missing or partially-written scoped state starts as an empty map.
            pass
        with _locale_cache_guard:
            if generation != _locale_cache_generation:
                continue
            _subject_locale_cache[name] = loaded
            return loaded


def _persist_subject_locale_state_unlocked(
    name: str,
    states: dict[str, tuple[str | None, int | None, int | None]],
) -> bool:
    snapshot = dict(states)
    path = _subject_locale_path(name)
    with _locale_cache_guard:
        generation = _locale_cache_generation
    staging_path = (
        f"{path}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.pending"
    )
    try:
        atomic_write_json(
            staging_path,
            {
                "subjects": {
                    key: {
                        "language": language,
                        "order": order,
                        "reserved_order": reserved_order,
                    }
                    for key, (language, order, reserved_order) in snapshot.items()
                },
            },
            ensure_ascii=False,
        )
        with _prompt_locale_write_transaction("scoped_prompt_locales.json"):
            with _locale_cache_guard:
                if generation != _locale_cache_generation:
                    raise PromptLocaleInvalidatedError(
                        "scoped prompt locale write was invalidated"
                    )
                os.replace(staging_path, path)
                _subject_locale_cache[name] = snapshot
                return True
    except (MaintenanceModeError, PromptLocaleInvalidatedError):
        raise
    except Exception as exc:
        logger.warning(
            "[PromptLocale] %s: scoped locale persist failed: %s",
            name,
            exc,
        )
        return False
    finally:
        try:
            os.remove(staging_path)
        except FileNotFoundError:
            # Successful os.replace already consumed the staging path.
            pass
        except OSError as exc:
            logger.debug("[PromptLocale] stale staging cleanup failed: %s", exc)


def allocate_character_prompt_locale_order(name: str) -> int:
    """Allocate a process-local causal order at request admission time."""
    global _character_locale_capture_order
    with _locale_reload_guard, _get_locale_lock(name):
        language, order, reserved_order = _load_locale_state_unlocked(name)
        with _locale_admission_orders_guard:
            high_water = max(
                order or 0,
                reserved_order or 0,
                _character_locale_admission_orders.get(name, 0),
            )
            selected_order = max(time.time_ns(), high_water + 1)
            _character_locale_admission_orders[name] = selected_order
            _character_locale_capture_order = max(
                _character_locale_capture_order,
                selected_order,
            )
        return selected_order


def capture_character_prompt_locale_order(_name: str) -> int:
    """Capture a monotonic token without touching per-character state."""
    global _character_locale_capture_order
    with _locale_admission_orders_guard:
        selected_order = max(
            time.time_ns(),
            _character_locale_capture_order + 1,
        )
        _character_locale_capture_order = selected_order
        return selected_order


def rebase_character_prompt_locale_order(name: str, captured_order: int) -> int:
    """Map a side-effect-free admission token onto durable character order.

    The per-character offset is fixed for one cache epoch, so requests retain
    their original admission ordering even when character validation completes
    out of order.  Cache invalidation clears the offset because a cloud restore
    may install a different durable high-water mark.
    """
    if not isinstance(captured_order, int) or isinstance(captured_order, bool):
        raise ValueError("invalid captured prompt locale order")

    with _locale_reload_guard, _get_locale_lock(name):
        _language, order, reserved_order = _load_locale_state_unlocked(name)
        with _locale_admission_orders_guard:
            offset = _character_locale_capture_offsets.get(name)
            if offset is None:
                high_water = max(
                    order or 0,
                    reserved_order or 0,
                    _character_locale_admission_orders.get(name, 0),
                )
                offset = max(0, high_water + 1 - captured_order)
                _character_locale_capture_offsets[name] = offset
            selected_order = captured_order + offset
            _character_locale_admission_orders[name] = max(
                _character_locale_admission_orders.get(name, 0),
                selected_order,
            )
            return selected_order


def reserve_character_prompt_locale_order(
    name: str,
    *,
    order: int | None = None,
) -> int:
    """Durably reserve a per-character causal order."""
    selected_order = (
        order
        if isinstance(order, int) and not isinstance(order, bool)
        else allocate_character_prompt_locale_order(name)
    )
    with _locale_reload_guard, _get_locale_lock(name):
        _assert_prompt_locale_writable("prompt_locale.json")
        language, current_order, reserved_order = _load_locale_state_unlocked(name)
        with _locale_admission_orders_guard:
            _character_locale_admission_orders[name] = max(
                _character_locale_admission_orders.get(name, 0),
                selected_order,
            )
        if not _persist_locale_state_unlocked(
            name,
            language,
            current_order,
            max(reserved_order or selected_order, selected_order),
        ):
            raise PromptLocalePersistenceError(
                "prompt locale order reservation was not persisted"
            )
        return selected_order


def record_character_prompt_locale(
    name: str,
    language: str | None,
    *,
    order: int | None = None,
) -> str | None:
    """Persist the latest explicit session locale, or clear stale state."""
    selected = None
    if is_supported_language_code(language):
        selected = normalize_language_code(str(language), format="full")
    selected_order = order if isinstance(order, int) and not isinstance(order, bool) else None

    with _locale_reload_guard, _get_locale_lock(name):
        _assert_prompt_locale_writable("prompt_locale.json")
        current_language, current_order, reserved_order = _load_locale_state_unlocked(name)
        if current_order is not None and (
            selected_order is None or selected_order < current_order
        ):
            return current_language

        next_reserved_order = reserved_order
        if selected_order is not None:
            next_reserved_order = max(reserved_order or selected_order, selected_order)
        if not _persist_locale_state_unlocked(
            name,
            selected,
            selected_order,
            next_reserved_order,
        ):
            raise PromptLocalePersistenceError(
                "prompt locale update was not persisted"
            )
    return selected


def get_character_prompt_locale(name: str) -> str | None:
    """Load the latest explicit session locale, including after restart."""
    with _get_locale_lock(name):
        selected, _order, _reserved_order = _load_locale_state_unlocked(name)
        return selected


def allocate_subject_prompt_locale_order(name: str, subject) -> int:
    """Allocate a request-admission order for one scoped memory owner."""
    return allocate_subject_prompt_locale_orders(name, [subject])[0]


def allocate_subject_prompt_locale_orders(name: str, subjects) -> list[int]:
    """Allocate request-admission orders for scoped memory owners."""
    keys = [_subject_locale_key(subject) for subject in subjects]
    if not keys:
        return []
    with _locale_reload_guard, _get_locale_lock(name):
        _load_subject_locale_forget_cutoffs_unlocked()
        states = _load_subject_locale_state_unlocked(name)
        with _locale_admission_orders_guard:
            selected_orders = []
            for key in keys:
                _language, order, reserved_order = states.get(
                    key,
                    (None, None, None),
                )
                high_water = max(
                    order or 0,
                    reserved_order or 0,
                    _subject_locale_forget_cutoffs.get((name, key), 0),
                    _subject_locale_admission_orders.get((name, key), 0),
                )
                selected_order = max(time.time_ns(), high_water + 1)
                _subject_locale_admission_orders[(name, key)] = selected_order
                selected_orders.append(selected_order)
            return selected_orders


def reserve_subject_prompt_locale_order(
    name: str,
    subject,
    *,
    order: int | None = None,
) -> int:
    """Reserve the next durable causal order for one scoped memory owner."""
    orders = [order] if isinstance(order, int) and not isinstance(order, bool) else None
    return reserve_subject_prompt_locale_orders(name, [subject], orders=orders)[0]


def reserve_subject_prompt_locale_orders(
    name: str,
    subjects,
    *,
    orders: list[int] | None = None,
) -> list[int]:
    """Reserve causal orders for multiple subjects with one sidecar write."""
    keys = [_subject_locale_key(subject) for subject in subjects]
    if not keys:
        return []
    selected_orders = orders
    if (
        selected_orders is None
        or len(selected_orders) != len(keys)
        or any(
            not isinstance(order, int) or isinstance(order, bool)
            for order in selected_orders
        )
    ):
        selected_orders = allocate_subject_prompt_locale_orders(name, subjects)
    with _locale_reload_guard, _get_locale_lock(name):
        _assert_prompt_locale_writable("scoped_prompt_locales.json")
        _load_subject_locale_forget_cutoffs_unlocked()
        states = dict(_load_subject_locale_state_unlocked(name))
        for key, selected_order in zip(keys, selected_orders):
            language, order, reserved_order = states.get(
                key,
                (None, None, None),
            )
            states[key] = (
                language,
                order,
                max(reserved_order or selected_order, selected_order),
            )
        with _locale_admission_orders_guard:
            for key, selected_order in zip(keys, selected_orders):
                _subject_locale_admission_orders[(name, key)] = max(
                    _subject_locale_admission_orders.get((name, key), 0),
                    selected_order,
                )
        if not _persist_subject_locale_state_unlocked(name, states):
            raise PromptLocalePersistenceError(
                "scoped prompt locale order reservation was not persisted"
            )
        return selected_orders


def record_subject_prompt_locale(
    name: str,
    subject,
    language: str | None,
    *,
    order: int | None = None,
) -> str | None:
    """Persist the latest explicit locale for one group/member subject."""
    return record_subject_prompt_locales(
        name,
        [(subject, language, order)],
    )[0]


def record_subject_prompt_locales(name: str, updates) -> list[str | None]:
    """Record multiple scoped locales with one sidecar write."""
    prepared = []
    for subject, language, order in updates:
        selected = None
        if is_supported_language_code(language):
            selected = normalize_language_code(str(language), format="full")
        selected_order = (
            order
            if isinstance(order, int) and not isinstance(order, bool)
            else None
        )
        prepared.append((_subject_locale_key(subject), selected, selected_order))
    if not prepared:
        return []

    with _locale_reload_guard, _get_locale_lock(name):
        _assert_prompt_locale_writable("scoped_prompt_locales.json")
        _load_subject_locale_forget_cutoffs_unlocked()
        states = dict(_load_subject_locale_state_unlocked(name))
        results = []
        changed = False
        for key, selected, selected_order in prepared:
            current_language, current_order, reserved_order = states.get(
                key,
                (None, None, None),
            )
            forget_cutoff = _subject_locale_forget_cutoffs.get((name, key))
            if forget_cutoff is not None and (
                selected_order is None or selected_order <= forget_cutoff
            ):
                results.append(current_language)
                continue
            if current_order is not None and (
                selected_order is None or selected_order < current_order
            ):
                results.append(current_language)
                continue
            next_reserved_order = reserved_order
            if selected_order is not None:
                next_reserved_order = max(
                    reserved_order or selected_order,
                    selected_order,
                )
            states[key] = (selected, selected_order, next_reserved_order)
            results.append(selected)
            changed = True
        if changed:
            if not _persist_subject_locale_state_unlocked(name, states):
                raise PromptLocalePersistenceError(
                    "scoped prompt locale update was not persisted"
                )
        return results


def forget_subject_prompt_locale(name: str, subject) -> int:
    """Erase one scoped locale and reject records reserved before the erase."""
    key = _subject_locale_key(subject)
    with _locale_reload_guard, _get_locale_lock(name):
        _assert_prompt_locale_writable("scoped_prompt_locales.json")
        _load_subject_locale_forget_cutoffs_unlocked()
        states = dict(_load_subject_locale_state_unlocked(name))
        previous = states.pop(key, None)
        previous_order = previous[1] if previous is not None else None
        previous_reserved = previous[2] if previous is not None else None
        cutoff_key = (name, key)
        previous_cutoff = _subject_locale_forget_cutoffs.get(cutoff_key)
        _subject_locale_forget_cutoffs[cutoff_key] = max(
            time.time_ns(),
            previous_order or 0,
            previous_reserved or 0,
            previous_cutoff or 0,
        )
        try:
            _persist_subject_locale_forget_cutoffs_unlocked()
        except Exception:
            if previous_cutoff is None:
                _subject_locale_forget_cutoffs.pop(cutoff_key, None)
            else:
                _subject_locale_forget_cutoffs[cutoff_key] = previous_cutoff
            raise
        if previous is None:
            return 0
        if not _persist_subject_locale_state_unlocked(name, states):
            raise PromptLocalePersistenceError(
                "scoped prompt locale erase was not persisted"
            )
        return 1


def get_subject_prompt_locale(name: str, subject) -> str | None:
    """Load the latest explicit locale for one scoped memory owner."""
    key = _subject_locale_key(subject)
    with _get_locale_lock(name), _subject_locale_forget_cutoffs_guard:
        _load_subject_locale_forget_cutoffs_unlocked()
        states = _load_subject_locale_state_unlocked(name)
        selected, _order, _reserved_order = states.get(
            key,
            (None, None, None),
        )
        forget_cutoff = _subject_locale_forget_cutoffs.get((name, key))
        if forget_cutoff is not None and (
            _order is None or _order <= forget_cutoff
        ):
            return None
        return selected


async def aget_subject_prompt_locale(name: str, subject) -> str | None:
    """Async wrapper for deferred scoped-memory jobs."""
    return await asyncio.to_thread(get_subject_prompt_locale, name, subject)


async def run_with_character_prompt_locale(
    name: str,
    operation,
    *args,
    **kwargs,
):
    """Run one async operation with the latest durable character locale."""
    selected = await asyncio.to_thread(get_character_prompt_locale, name)
    with language_context(selected):
        return await operation(*args, **kwargs)
