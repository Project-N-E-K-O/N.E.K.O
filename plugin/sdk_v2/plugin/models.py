"""Plugin-facing runtime model types.

These models are part of the public `plugin` facade surface and should not live
under `public/*`, which is reserved for internal implementation details.
"""

from __future__ import annotations

from typing import TypedDict

from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue


class ErrorDetail(TypedDict, total=False):
    code: str
    message: str
    details: JsonValue | JsonObject | None
    retriable: bool


class OkEnvelope(TypedDict, total=False):
    success: bool
    code: int
    data: JsonValue | JsonObject | None
    message: str
    error: None
    time: str
    trace_id: str | None
    meta: dict[str, JsonValue]


class ErrEnvelope(TypedDict, total=False):
    success: bool
    code: int
    data: None
    message: str
    error: ErrorDetail
    time: str
    trace_id: str | None
    meta: dict[str, JsonValue]


Envelope = OkEnvelope | ErrEnvelope

__all__ = ["ErrorDetail", "OkEnvelope", "ErrEnvelope", "Envelope"]
