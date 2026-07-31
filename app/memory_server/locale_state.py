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
    if name in _locale_cache:
        return _locale_cache[name]

    selected = None
    order = None
    reserved_order = None
    try:
        with open(_locale_path(name), encoding="utf-8") as handle:
            payload = json.load(handle)
        candidate = payload.get("language") if isinstance(payload, dict) else None
        if is_supported_language_code(candidate):
            selected = normalize_language_code(str(candidate), format="full")
        candidate_order = payload.get("order") if isinstance(payload, dict) else None
        if isinstance(candidate_order, int) and not isinstance(candidate_order, bool):
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
    _locale_cache[name] = (selected, order, reserved_order)
    return selected, order, reserved_order


def _persist_locale_state_unlocked(
    name: str,
    language: str | None,
    order: int | None,
    reserved_order: int | None,
) -> None:
    _locale_cache[name] = (language, order, reserved_order)
    try:
        atomic_write_json(
            _locale_path(name),
            {
                "language": language,
                "order": order,
                "reserved_order": reserved_order,
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.warning(
            "[PromptLocale] %s: persist failed: %s",
            name,
            exc,
        )


def _load_subject_locale_state_unlocked(
    name: str,
) -> dict[str, tuple[str | None, int | None, int | None]]:
    if name in _subject_locale_cache:
        return _subject_locale_cache[name]

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
                loaded[key] = (selected, order, reserved_order)
    except (OSError, json.JSONDecodeError):
        pass
    _subject_locale_cache[name] = loaded
    return loaded


def _persist_subject_locale_state_unlocked(
    name: str,
    states: dict[str, tuple[str | None, int | None, int | None]],
) -> None:
    snapshot = dict(states)
    _subject_locale_cache[name] = snapshot
    try:
        atomic_write_json(
            _subject_locale_path(name),
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
    except Exception as exc:
        logger.warning(
            "[PromptLocale] %s: scoped locale persist failed: %s",
            name,
            exc,
        )


def reserve_character_prompt_locale_order(name: str) -> int:
    """Reserve and durably persist the next per-character causal order."""
    with _get_locale_lock(name):
        language, order, reserved_order = _load_locale_state_unlocked(name)
        high_water = max(order or 0, reserved_order or 0)
        selected_order = max(time.time_ns(), high_water + 1)
        _persist_locale_state_unlocked(
            name,
            language,
            order,
            selected_order,
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

    with _get_locale_lock(name):
        current_language, current_order, reserved_order = _load_locale_state_unlocked(name)
        if current_order is not None and (
            selected_order is None or selected_order < current_order
        ):
            return current_language

        next_reserved_order = reserved_order
        if selected_order is not None:
            next_reserved_order = max(reserved_order or selected_order, selected_order)
        _persist_locale_state_unlocked(
            name,
            selected,
            selected_order,
            next_reserved_order,
        )
    return selected


def get_character_prompt_locale(name: str) -> str | None:
    """Load the latest explicit session locale, including after restart."""
    with _get_locale_lock(name):
        selected, _order, _reserved_order = _load_locale_state_unlocked(name)
        return selected


def reserve_subject_prompt_locale_order(name: str, subject) -> int:
    """Reserve the next durable causal order for one scoped memory owner."""
    key = _subject_locale_key(subject)
    with _get_locale_lock(name):
        states = dict(_load_subject_locale_state_unlocked(name))
        language, order, reserved_order = states.get(key, (None, None, None))
        high_water = max(order or 0, reserved_order or 0)
        selected_order = max(time.time_ns(), high_water + 1)
        states[key] = (language, order, selected_order)
        _persist_subject_locale_state_unlocked(name, states)
        return selected_order


def record_subject_prompt_locale(
    name: str,
    subject,
    language: str | None,
    *,
    order: int | None = None,
) -> str | None:
    """Persist the latest explicit locale for one group/member subject."""
    key = _subject_locale_key(subject)
    selected = None
    if is_supported_language_code(language):
        selected = normalize_language_code(str(language), format="full")
    selected_order = (
        order
        if isinstance(order, int) and not isinstance(order, bool)
        else None
    )

    with _get_locale_lock(name):
        states = dict(_load_subject_locale_state_unlocked(name))
        current_language, current_order, reserved_order = states.get(
            key,
            (None, None, None),
        )
        if current_order is not None and (
            selected_order is None or selected_order < current_order
        ):
            return current_language
        next_reserved_order = reserved_order
        if selected_order is not None:
            next_reserved_order = max(
                reserved_order or selected_order,
                selected_order,
            )
        states[key] = (selected, selected_order, next_reserved_order)
        _persist_subject_locale_state_unlocked(name, states)
    return selected


def get_subject_prompt_locale(name: str, subject) -> str | None:
    """Load the latest explicit locale for one scoped memory owner."""
    key = _subject_locale_key(subject)
    with _get_locale_lock(name):
        states = _load_subject_locale_state_unlocked(name)
        selected, _order, _reserved_order = states.get(
            key,
            (None, None, None),
        )
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
