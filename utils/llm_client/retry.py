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

def anthropic_retry_error_types() -> tuple[type[BaseException], ...]:
    """Return Anthropic SDK error classes that should follow the chat retry path."""
    global _ANTHROPIC_RETRY_EXCEPTION_TYPES
    if _ANTHROPIC_RETRY_EXCEPTION_TYPES is None:
        try:
            import anthropic as _anthropic
        except Exception:  # pragma: no cover - anthropic may be absent in minimal installs
            _anthropic = None
        _ANTHROPIC_RETRY_EXCEPTION_TYPES = tuple(
            exc_type
            for exc_type in (
                getattr(_anthropic, "APIConnectionError", None),
                getattr(_anthropic, "APITimeoutError", None),
                getattr(_anthropic, "AuthenticationError", None),
                getattr(_anthropic, "InternalServerError", None),
                getattr(_anthropic, "RateLimitError", None),
            )
            if isinstance(exc_type, type)
        )
    return _ANTHROPIC_RETRY_EXCEPTION_TYPES
