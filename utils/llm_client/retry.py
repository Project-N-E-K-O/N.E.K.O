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
"""Lazy OpenAI and Anthropic retry exception registries."""

from __future__ import annotations
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_ANTHROPIC_RETRY_EXCEPTION_TYPES: tuple[type[BaseException], ...] | None = None

_OPENAI_RETRY_EXCEPTION_TYPES: tuple[type[BaseException], ...] | None = None

def openai_retry_error_types() -> tuple[type[BaseException], ...]:
    """Return OpenAI SDK error classes that should follow the chat retry path."""
    global _OPENAI_RETRY_EXCEPTION_TYPES
    if _OPENAI_RETRY_EXCEPTION_TYPES is None:
        from openai import APIConnectionError, InternalServerError, RateLimitError

        _OPENAI_RETRY_EXCEPTION_TYPES = (APIConnectionError, InternalServerError, RateLimitError)
    return _OPENAI_RETRY_EXCEPTION_TYPES

def chat_retry_error_types() -> tuple[type[BaseException], ...]:
    """Return the union of OpenAI + Anthropic transient error classes for shared retry loops."""
    return (*openai_retry_error_types(), *anthropic_retry_error_types())

_ANTHROPIC_RETRY_EXCEPTION_NAMES = (
    "APIConnectionError",
    "APITimeoutError",
    "AuthenticationError",
    "InternalServerError",
    "RateLimitError",
)


def anthropic_retry_error_types() -> tuple[type[BaseException], ...]:
    """Return Anthropic SDK error classes that should follow the chat retry path.

    Never imports the SDK. Callers evaluate this in ``except`` clauses, on the
    event loop, for every exception leaving an LLM call (a cancelled stream
    included), while the SDK is only loaded when an Anthropic client is built.
    Until then no Anthropic error can be in flight, so the answer is ``()``.
    Only a complete set is cached, so a client built later is still covered.
    """
    global _ANTHROPIC_RETRY_EXCEPTION_TYPES
    if _ANTHROPIC_RETRY_EXCEPTION_TYPES is not None:
        return _ANTHROPIC_RETRY_EXCEPTION_TYPES
    module = sys.modules.get("anthropic")
    found = tuple(
        exc_type
        for exc_type in (
            getattr(module, name, None) for name in _ANTHROPIC_RETRY_EXCEPTION_NAMES
        )
        if isinstance(exc_type, type)
    )
    # A module still being imported on another thread can be missing classes.
    if len(found) == len(_ANTHROPIC_RETRY_EXCEPTION_NAMES):
        _ANTHROPIC_RETRY_EXCEPTION_TYPES = found
    return found
