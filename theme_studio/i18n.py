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

"""Localized user-facing text of Theme Studio's backend.

The texts live in ``config/prompts/prompts_theme_studio.py`` (the repository
keeps every multilingual backend string under ``config/prompts``). This module
only resolves them:

* The language of a request is carried in a context variable. The router sets
  it from the ``X-Neko-Locale`` header the panel sends (falling back to
  ``Accept-Language``); ``asyncio.to_thread`` copies the context, so entries
  running in worker threads see it. Without a request (the schedule thread,
  tests) the app's global language is used.
* :func:`msg` looks a message up by id and formats ``{name}`` placeholders.
  ``default`` is used when the id is not in the table, which lets callers pass
  data-derived ids (e.g. per-parameter labels) with their source text.
"""

from __future__ import annotations

import contextvars
from typing import Any

from config.prompts._locale import normalize_prompt_locale
from config.prompts.prompts_theme_studio import THEME_STUDIO_MESSAGES

__all__ = ["current_locale", "msg", "reset_request_locale", "set_request_locale"]

_REQUEST_LOCALE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "theme_studio_request_locale", default=""
)


def set_request_locale(raw: Any) -> contextvars.Token:
    """Remember the request's language (raw tag); returns a token for reset."""
    value = str(raw or "").split(",")[0].split(";")[0].strip()
    return _REQUEST_LOCALE.set(value[:32])


def reset_request_locale(token: contextvars.Token) -> None:
    """Undo :func:`set_request_locale`."""
    _REQUEST_LOCALE.reset(token)


def _global_language() -> str:
    try:
        from utils.language_utils import get_global_language_full

        return get_global_language_full() or ""
    except Exception:  # noqa: BLE001 - language detection must never break a message
        return ""


def current_locale() -> str:
    """Message-table key for the current request (or the app language)."""
    raw = _REQUEST_LOCALE.get() or _global_language()
    return normalize_prompt_locale(raw, default="zh")


class _KeepMissing(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _pick(entry: dict[str, str], lang: str) -> str:
    if lang in entry:
        return entry[lang]
    if lang.startswith("zh"):
        return entry.get("zh") or entry.get("zh-TW") or entry.get("en", "")
    return entry.get("en") or entry.get("zh", "")


def msg(msg_id: str, default: str | None = None, **params: Any) -> str:
    """Localized text for ``msg_id`` with ``{name}`` placeholders filled in."""
    entry = THEME_STUDIO_MESSAGES.get(msg_id)
    if entry:
        text = _pick(entry, current_locale())
    elif default is not None:
        text = default
    else:
        text = msg_id
    if not params:
        return text
    try:
        return text.format_map(_KeepMissing({k: v for k, v in params.items()}))
    except (ValueError, IndexError):
        return text
