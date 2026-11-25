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

"""Provider-agnostic decision primitives, response types, and protocol helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Union
import urllib.parse

from .openrouter_catalog import is_openrouter_decision_model


NATIVE_DECISION_PROTOCOLS = frozenset({
    "systemone",
    "openai_decisions",
    "gemini_decisions",
    "openrouter_decisions",
})

ALL_DECISION_PROTOCOLS = NATIVE_DECISION_PROTOCOLS | frozenset({"chat_fallback"})

# Model IDs served by a vendor's own Decisions API. OpenRouter decision model
# IDs are recognized by utils.decision_client.openrouter_catalog instead.
_KNOWN_NATIVE_MODEL_PREFIXES: tuple[tuple[str, str], ...] = (
    ("decision-model-", "systemone"),
    ("gpt-6-luna", "openai_decisions"),
    ("gemini-decision-", "gemini_decisions"),
)


def is_native_decision_protocol(protocol: str | None) -> bool:
    """Return True when *protocol* targets a native zero-output Decisions API."""
    return str(protocol or "").strip().lower() in NATIVE_DECISION_PROTOCOLS


def infer_decision_protocol(
    model: str | None,
    base_url: str | None = "",
    *,
    profile_protocol: str | None = None,
    profile_model: str | None = None,
) -> str:
    """Resolve the decision wire protocol from URL, model ID, and profile metadata.

    Precedence:
      1. Explicit native decision endpoint suffix in *base_url* (e.g. ``/systemone``,
         ``/v1/decisions``, ``/v1beta/decisions``, ``/alpha/decisions``).
      2. Known native decision model IDs: vendor prefixes (e.g.
         ``decision-model-preview``, ``gpt-6-luna``, ``gemini-decision-1``) and the
         OpenRouter decision model catalog (e.g. ``google/gemini-decision-1``).
      3. Profile-declared native protocol when *model* matches *profile_model* (or is empty).
      4. ``chat_fallback`` for every other chat/generative model.
    """
    m = str(model or "").strip().lower()
    url = str(base_url or "").strip().rstrip("/").lower()
    parsed_url = urllib.parse.urlsplit(url) if url else None
    url_path = (parsed_url.path or "").rstrip("/").lower() if parsed_url else ""
    url_host = (parsed_url.hostname or "").lower() if parsed_url else ""
    prof_proto = str(profile_protocol or "").strip().lower()
    prof_model = str(profile_model or "").strip().lower()

    if url_path.endswith("/systemone"):
        return "systemone"
    if url_path.endswith("/alpha/decisions") or (
        url_host == "openrouter.ai" and url_path.endswith("/decisions")
    ):
        return "openrouter_decisions"
    if url_path.endswith("/v1beta/decisions") or (
        url_host == "generativelanguage.googleapis.com" and url_path.endswith("/decisions")
    ):
        return "gemini_decisions"
    if url_path.endswith("/v1/decisions") or url_path.endswith("/decisions"):
        return "openai_decisions"

    if is_openrouter_decision_model(m):
        return "openrouter_decisions"
    for prefix, proto in _KNOWN_NATIVE_MODEL_PREFIXES:
        if m.startswith(prefix):
            if url_host == "openrouter.ai" and proto != "openrouter_decisions":
                return "openrouter_decisions"
            return proto

    if prof_proto in NATIVE_DECISION_PROTOCOLS:
        if not m or not prof_model or m == prof_model:
            return prof_proto

    return "chat_fallback"


def resolve_decision_endpoint(
    protocol: str,
    base_url: str | None,
    decision_url: str | None = "",
) -> str:
    """Compute the final HTTP POST URL for a decision request."""
    proto = str(protocol or "chat_fallback").strip().lower()
    explicit = str(decision_url or "").strip().rstrip("/")
    base = str(base_url or "").strip().rstrip("/")

    if proto == "chat_fallback":
        return base or explicit

    if proto == "systemone":
        target = explicit or base
        if not target:
            return ""
        if target.lower().endswith("/systemone"):
            return target
        return f"{target}/systemone"

    if proto == "openai_decisions":
        target = explicit or base
        if not target:
            return ""
        if target.lower().endswith("/decisions"):
            return target
        return f"{target}/decisions"

    if proto == "gemini_decisions":
        if explicit:
            if explicit.lower().endswith("/decisions"):
                return explicit
            return f"{explicit}/decisions"
        if not base:
            return "https://generativelanguage.googleapis.com/v1beta/decisions"
        lower = base.lower()
        if lower.endswith("/decisions"):
            return base
        if lower.endswith("/v1beta/openai"):
            return base[: -len("/openai")] + "/decisions"
        parsed = urllib.parse.urlsplit(base)
        if (parsed.hostname or "").lower() == "generativelanguage.googleapis.com":
            return "https://generativelanguage.googleapis.com/v1beta/decisions"
        return f"{base}/decisions"

    if proto == "openrouter_decisions":
        if explicit:
            return explicit
        if not base:
            return "https://openrouter.ai/api/alpha/decisions"
        lower = base.lower()
        if lower.endswith("/decisions"):
            return base
        if lower.endswith("/api/v1"):
            return base[: -len("/api/v1")] + "/api/alpha/decisions"
        return f"{base}/alpha/decisions"

    return explicit or base


@dataclass(frozen=True)
class BoolQuestion:
    """Binary yes/no decision primitive (``noul`` / ``predicate``)."""

    instructions: str
    true_description: str = ""
    false_description: str = ""


@dataclass(frozen=True)
class ChoiceQuestion:
    """Closed-set single-select decision primitive (``choice``)."""

    instructions: str
    criteria: Mapping[str, str]

    def __post_init__(self) -> None:
        if len(self.criteria) < 2:
            raise ValueError("ChoiceQuestion requires at least 2 options in criteria")


@dataclass(frozen=True)
class ScoreQuestion:
    """Ordered rubric scoring decision primitive (``score``).

    ``levels`` must be ordered from lowest to highest (2 to 255 levels).
    """

    instructions: str
    levels: tuple[str, ...] | list[str]

    def __post_init__(self) -> None:
        if len(self.levels) < 2:
            raise ValueError("ScoreQuestion requires at least 2 ordered levels")
        if not isinstance(self.levels, tuple):
            object.__setattr__(self, "levels", tuple(self.levels))


DecisionQuestion = Union[BoolQuestion, ChoiceQuestion, ScoreQuestion]


@dataclass(frozen=True)
class DecisionAnswer:
    """Normalized answer for a single question across all protocols.

    - ``bool``: ``value`` is ``bool``; ``probability`` is P(True) in ``[0, 1]``.
    - ``choice``: ``value`` is the selected option key ``str``; ``probabilities`` maps option -> prob.
    - ``score``: ``value`` is expected 0-based rubric score in ``[0, len(levels) - 1]``;
      ``score_norm`` is normalized into ``[0, 1]``; ``probabilities`` maps level label -> prob.
    """

    question_type: str  # "bool" | "choice" | "score"
    value: Any
    probability: float | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float | None = None
    score_norm: float | None = None


@dataclass(frozen=True)
class DecisionResponse:
    """Normalized response returned by :class:`DecisionClient`."""

    answers: dict[str, DecisionAnswer]
    model: str
    protocol: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    used_fallback: bool = False


class DecisionProtocolError(RuntimeError):
    """Raised when a decision protocol request fails or returns an invalid payload."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        protocol: str = "",
        permanent_route_error: bool = False,
        error_code: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.protocol = protocol
        self.permanent_route_error = permanent_route_error
        # Machine-readable cause the settings page can explain (e.g. "workspace_mismatch").
        self.error_code = error_code
