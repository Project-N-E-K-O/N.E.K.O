"""Translate location-domain failures into consistent plugin entry results."""

from __future__ import annotations

from typing import Any

from plugin.sdk.plugin import Err, Ok, SdkError

from ._location import (
    LocationAssumption,
    LocationError,
    is_location_clarification,
    location_clarification_payload,
    location_error_key,
)


def apply_location_assumption(
    payload: dict[str, Any],
    location: dict[str, Any],
    i18n: Any,
) -> dict[str, Any]:
    """Disclose a best-effort location in scalar fields and the summary."""
    return apply_location_assumptions(payload, (location,), i18n)


def apply_location_assumptions(
    payload: dict[str, Any],
    locations: tuple[dict[str, Any], ...],
    i18n: Any,
) -> dict[str, Any]:
    """Disclose every assumed location used by a read-only operation."""
    assumed_locations = [
        location
        for location in locations
        if isinstance(location.get("_location_assumption"), LocationAssumption)
    ]
    if not assumed_locations:
        payload.setdefault("assumed", False)
        payload.setdefault("assumed_location", "")
        payload.setdefault("ambiguity_warning", "")
        return payload
    selected_labels: list[str] = []
    warnings: list[str] = []
    for location in assumed_locations:
        assumption = location["_location_assumption"]
        if not isinstance(assumption, LocationAssumption):
            continue
        selected = assumption.selected_label
        selected_labels.append(selected)
        alternatives = list(assumption.alternatives)
        alternatives_text = i18n.t("location.no_alternatives")
        if alternatives:
            alternatives_text = i18n.t("nearby.list_separator").join(alternatives)
        warnings.append(
            i18n.t(
                "location.assumption",
                selected=selected,
                alternatives=alternatives_text,
            )
        )
    warning = "\n".join(warnings)
    payload.update(
        {
            "assumed": True,
            "assumed_location": i18n.t("nearby.list_separator").join(selected_labels),
            "ambiguity_warning": warning,
            "summary": f"{payload.get('summary', '')}\n{warning}".strip(),
        }
    )
    return payload


def location_unavailable_result(error: LocationError, i18n: Any):
    """Return a non-blocking result when no read-only query can be executed."""
    detail = i18n.t(location_error_key(error))
    return Ok(
        {
            "status": "unavailable",
            "summary": i18n.t("location.unavailable", detail=detail),
            "assumed": False,
            "assumed_location": "",
            "ambiguity_warning": "",
        }
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
