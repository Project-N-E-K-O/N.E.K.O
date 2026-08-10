"""Consistent failed-entry results for LifeKit provider outages."""

from __future__ import annotations

from typing import Any

from plugin.sdk.plugin import Err, SdkError


def unavailable_error(
    summary: str,
    *,
    code: str,
    details: dict[str, Any] | None = None,
):
    """Return an SDK failure while retaining structured retry context."""
    payload: dict[str, Any] = {
        "status": "unavailable",
        "summary": summary,
        "error_code": code,
        "retriable": True,
    }
    if details:
        payload.update(details)
    payload["error_code"] = code
    return Err(SdkError(summary, code=code, details=payload))
