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

"""Shared mini-game SDK backend policy for isolated structured LLM attempts.

Games own their output schema and validator.  The SDK owns the bounded retry
lifecycle: every attempt receives a new isolation id and must create/close its
own provider client.  A failed response is never added to the next attempt's
messages.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
import secrets
from typing import Any, Generic, TypeVar


_T = TypeVar("_T")
_MAX_CONTENT_RETRIES = 1
_MAX_VALIDATION_ISSUES = 64


@dataclass(frozen=True)
class StructuredOutputFailure:
    """Bounded diagnostic metadata for one rejected LLM response."""

    attempt: int
    kind: str
    reason: str
    issues: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class StructuredOutputResult(Generic[_T]):
    """Final normalized value plus the attempts that preceded it."""

    value: _T
    attempts: int
    issues: tuple[dict[str, Any], ...]
    failures: tuple[StructuredOutputFailure, ...]

    @property
    def valid(self) -> bool:
        return not self.issues

    @property
    def recovered(self) -> bool:
        return self.valid and bool(self.failures)


class StructuredOutputContentError(ValueError):
    """The provider replied, but its response could not be parsed as required."""

    def __init__(self, reason: str = "invalid_content") -> None:
        self.reason = str(reason or "invalid_content")
        super().__init__(self.reason)


class StructuredOutputAttemptsExhausted(StructuredOutputContentError):
    """All bounded attempts returned content that could not be parsed."""

    def __init__(
        self,
        reason: str,
        failures: Iterable[StructuredOutputFailure],
    ) -> None:
        self.failures = tuple(failures)
        super().__init__(reason)


def _bounded_issues(issues: Iterable[Mapping[str, Any]] | None) -> tuple[dict[str, Any], ...]:
    if issues is None:
        return ()
    bounded: list[dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, Mapping):
            continue
        bounded.append(dict(issue))
        if len(bounded) >= _MAX_VALIDATION_ISSUES:
            break
    return tuple(bounded)


async def run_isolated_structured_output(
    attempt_factory: Callable[[int, str], Awaitable[Any]],
    validator: Callable[[Any], tuple[_T, Iterable[Mapping[str, Any]]]],
    *,
    content_retries: int = 1,
) -> StructuredOutputResult[_T]:
    """Run a structured LLM request with one optional, isolated retry.

    ``attempt_factory`` is invoked separately for every attempt and receives a
    unique isolation id.  It must create and close a fresh provider client and
    construct a fresh message list.  Only content/validation failures retry;
    provider, network, timeout, and cancellation exceptions propagate at once.
    """

    if not isinstance(content_retries, int) or not 0 <= content_retries <= _MAX_CONTENT_RETRIES:
        raise ValueError(f"content_retries must be between 0 and {_MAX_CONTENT_RETRIES}")

    failures: list[StructuredOutputFailure] = []
    total_attempts = content_retries + 1
    for attempt in range(1, total_attempts + 1):
        isolation_id = secrets.token_urlsafe(18)
        try:
            raw_value = await attempt_factory(attempt, isolation_id)
        except StructuredOutputContentError as exc:
            failures.append(StructuredOutputFailure(
                attempt=attempt,
                kind="content",
                reason=exc.reason,
            ))
            if attempt < total_attempts:
                continue
            raise StructuredOutputAttemptsExhausted(exc.reason, failures) from exc

        normalized, raw_issues = validator(raw_value)
        issues = _bounded_issues(raw_issues)
        if not issues:
            return StructuredOutputResult(
                value=normalized,
                attempts=attempt,
                issues=(),
                failures=tuple(failures),
            )

        failures.append(StructuredOutputFailure(
            attempt=attempt,
            kind="validation",
            reason="invalid_fields",
            issues=issues,
        ))
        if attempt == total_attempts:
            return StructuredOutputResult(
                value=normalized,
                attempts=attempt,
                issues=issues,
                failures=tuple(failures),
            )

    raise AssertionError("structured output attempt loop exited unexpectedly")
