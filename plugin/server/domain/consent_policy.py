"""Pure consent-state derivation shared by details, summaries, and notices."""

from __future__ import annotations

from dataclasses import dataclass

ANALYZING_STATUSES = frozenset({"queued", "running", "unavailable"})
INCOMPLETE_STATUSES = frozenset({"partial", "failed"})


@dataclass(frozen=True)
class ConsentView:
    state: str
    unconfirmed: list[str]
    upgraded: bool
    decision: str | None


def derive_consent(record: dict, report: dict) -> ConsentView:
    status = report.get("status")
    decision = record.get("decision")
    approved_capabilities = (
        set(record.get("approved_capabilities") or [])
        if decision == "approved"
        else set()
    )
    unconfirmed = sorted(
        set(report.get("capabilities") or []) - approved_capabilities
    )
    if status in ANALYZING_STATUSES:
        state = "analyzing"
    elif (
        decision == "denied"
        or unconfirmed
        or (decision != "approved" and status in INCOMPLETE_STATUSES)
    ):
        state = "confirm"
    else:
        state = "clear"
    return ConsentView(
        state=state,
        unconfirmed=unconfirmed,
        upgraded=decision == "approved" and bool(unconfirmed),
        decision=decision,
    )
