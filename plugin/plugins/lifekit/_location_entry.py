"""Translate location-domain failures into consistent plugin entry results."""

from __future__ import annotations

from typing import Any

from plugin.sdk.plugin import Err, Ok, SdkError

from ._location import (
    LocationError,
    is_location_clarification,
    location_clarification_payload,
    location_error_key,
)


def location_failure_result(
    error: LocationError,
    i18n: Any,
    *,
    field_name: str,
    requested_location: str = "",
    context: dict[str, object] | None = None,
):
    """Return one host-managed clarification or one non-actionable error."""
    error_key = location_error_key(error)
    if is_location_clarification(error):
        return Ok(
            location_clarification_payload(
                i18n.t(error_key),
                error=error,
                field_name=field_name,
                requested_location=requested_location,
                context=context,
            )
        )
    return Err(SdkError(i18n.t(error_key)))
