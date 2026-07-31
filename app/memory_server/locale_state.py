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

import json
import os
import threading

from utils.file_utils import atomic_write_json
from utils.language_utils import (
    is_supported_language_code,
    language_context,
    normalize_language_code,
)
from utils.logger_config import get_module_logger


logger = get_module_logger(__name__, "Memory")
_locale_cache: dict[str, str | None] = {}
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


def _get_locale_lock(name: str) -> threading.Lock:
    if name not in _locale_locks:
        with _locale_locks_guard:
            if name not in _locale_locks:
                _locale_locks[name] = threading.Lock()
    return _locale_locks[name]


def record_character_prompt_locale(name: str, language: str | None) -> str | None:
    """Persist the latest explicit session locale, or clear stale state."""
    selected = None
    if is_supported_language_code(language):
        selected = normalize_language_code(str(language), format="full")

    with _get_locale_lock(name):
        _locale_cache[name] = selected
        try:
            atomic_write_json(
                _locale_path(name),
                {"language": selected},
                ensure_ascii=False,
            )
        except Exception as exc:
            logger.warning(
                "[PromptLocale] %s: persist failed: %s",
                name,
                exc,
            )
    return selected


def get_character_prompt_locale(name: str) -> str | None:
    """Load the latest explicit session locale, including after restart."""
    with _get_locale_lock(name):
        if name in _locale_cache:
            return _locale_cache[name]
        selected = None
        try:
            with open(_locale_path(name), encoding="utf-8") as handle:
                payload = json.load(handle)
            candidate = payload.get("language") if isinstance(payload, dict) else None
            if is_supported_language_code(candidate):
                selected = normalize_language_code(str(candidate), format="full")
        except (OSError, json.JSONDecodeError):
            pass
        _locale_cache[name] = selected
        return selected


async def run_with_character_prompt_locale(
    name: str,
    operation,
    *args,
    **kwargs,
):
    """Run one async operation with the latest durable character locale."""
    with language_context(get_character_prompt_locale(name)):
        return await operation(*args, **kwargs)
