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

"""Minimal stand-in for the plugin SDK used by Theme Studio.

Theme Studio used to be an N.E.K.O plugin: its business code is a
``NekoPluginBase`` subclass with ``@plugin_entry`` methods, and the plugin host
(port 48916) did routing and result wrapping. Inside the host there is no
plugin host any more, but the business code (``theme_studio/service.py``) is
only loosely coupled to the SDK: the ``Ok`` / ``Err`` / ``SdkError`` result
wrappers, a few decorators and ``logger``.

This module provides a behaviour-equivalent subset so that code stays readable:

* ``Ok`` / ``Err``: result containers, unwrapped into HTTP responses by the router.
* ``SdkError``: business error; its message is shown to the user as is.
* ``plugin_entry`` / ``lifecycle`` / ``ui`` / ``neko_plugin`` / ``tr``: decorators
  that only register metadata; ``plugin_entry`` fills :data:`ENTRY_REGISTRY`,
  which is the only allow-list ``/run/{entry_id}`` dispatches from.

Progress reporting, events and the permission system of the plugin host are
deliberately not reproduced; Theme Studio never used them.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

__all__ = [
    "ENTRY_REGISTRY",
    "Err",
    "NekoPluginBase",
    "Ok",
    "SdkError",
    "lifecycle",
    "neko_plugin",
    "plugin_entry",
    "tr",
    "ui",
]


class SdkError(Exception):
    """Business error; ``str(err)`` is the user-facing reason."""

    def __init__(self, message: str = "", *, code: str = "sdk_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code

    def __str__(self) -> str:  # pragma: no cover - readability only
        return self.message or self.code


class _Result:
    """Common base of ``Ok`` / ``Err``."""

    __slots__ = ("value", "error")

    def __init__(self, value: Any = None, error: Exception | None = None) -> None:
        self.value = value
        self.error = error

    @property
    def ok(self) -> bool:
        return self.error is None

    def __repr__(self) -> str:  # pragma: no cover - debugging
        if self.ok:
            return f"Ok({self.value!r})"
        return f"Err({self.error!r})"


class Ok(_Result):
    """Successful result."""

    def __init__(self, value: Any = None) -> None:
        super().__init__(value=value, error=None)


class Err(_Result):
    """Failed result; accepts an ``SdkError``, any exception or a message string."""

    def __init__(self, error: Exception | str | None = None) -> None:
        if isinstance(error, str):
            error = SdkError(error)
        elif error is None:
            from .i18n import msg  # local import: the compat layer stays dependency-free

            error = SdkError(msg("core.unknownError"))
        super().__init__(value=None, error=error)


#: entry_id -> implementation (unbound function). The only dispatch allow-list.
ENTRY_REGISTRY: dict[str, Callable[..., Any]] = {}


def _noop_decorator(*_args: Any, **_kwargs: Any) -> Callable[[Any], Any]:
    def wrap(obj: Any) -> Any:
        return obj

    return wrap


def tr(key: str, default: str | None = None, **_kwargs: Any) -> str:
    """Translation placeholder: returns ``default`` (or the key when absent)."""
    if default:
        return default
    return key


def neko_plugin(cls: Any) -> Any:
    """Class decorator placeholder (nothing to hand over to a plugin host)."""
    return cls


def plugin_entry(*_args: Any, **kwargs: Any) -> Callable[[Any], Any]:
    """Method decorator: register ``id=`` and the function in :data:`ENTRY_REGISTRY`."""

    entry_id = str(kwargs.get("id") or "")

    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        if entry_id:
            ENTRY_REGISTRY[entry_id] = fn
            setattr(fn, "__entry_id__", entry_id)
        return fn

    return wrap


def lifecycle(*_args: Any, **_kwargs: Any) -> Callable[[Any], Any]:
    return _noop_decorator()


class _UiNamespace:
    """Placeholder namespace for ``ui.context`` / ``ui.panel`` decorators."""

    @staticmethod
    def context(*_args: Any, **_kwargs: Any) -> Callable[[Any], Any]:
        return _noop_decorator()

    @staticmethod
    def panel(*_args: Any, **_kwargs: Any) -> Callable[[Any], Any]:
        return _noop_decorator()


ui = _UiNamespace()


class NekoPluginBase:
    """Minimal base class providing ``logger`` and file logging."""

    plugin_id: str = "neko_theme_studio"

    def __init__(self, ctx: Any = None) -> None:
        self.ctx = ctx
        self.logger: logging.Logger = getattr(ctx, "logger", None) or logging.getLogger(
            "neko.theme_studio"
        )

    def enable_file_logging(self, log_level: str = "INFO") -> logging.Logger:
        """Equivalent stand-in: return the stdlib logger at the requested level."""
        logger = logging.getLogger("neko.theme_studio")
        level = getattr(logging, str(log_level).upper(), logging.INFO)
        try:
            logger.setLevel(level)
        except Exception:  # noqa: BLE001 - a bad log level must not break the feature
            pass
        return logger
