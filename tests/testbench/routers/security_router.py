"""Security / prompt-injection detection API (P21.3 F3).

Exposes a read-only endpoint the frontend editors (persona / script /
schema / save-modal) can call to flag suspicious patterns in
user-editable text. Pure advisory channel — this router never mutates
any session or on-disk state; it only runs the detector in
:mod:`tests.testbench.pipeline.prompt_injection_detect` and returns the
aggregated findings.

Endpoints
---------

* ``POST /api/security/prompt_injection/scan``
    Body: ``{"text": "..."}`` OR ``{"fields": {"name": "text", ...}}``.
    Returns the detector's summary shape (``{count, by_category,
    by_severity, top_hits}`` for the single-text form; ``{fields:
    {name: summary}}`` for the multi-field form).

Design notes
------------
* **No filtering / rejection**: the testbench deliberately allows
  adversarial content as input; see ``PLAN.md §13`` core principle
  \u201c\u6c38\u4e0d\u8fc7\u6ee4\u7528\u6237\u5185\u5bb9\u201d. This
  endpoint only describes what's there.
* **No session lock**: scanning is stateless; multiple concurrent calls
  are fine even mid-chat / mid-eval.
* **Stable payload shape**: UI badges parse ``count`` / ``by_category``
  / ``by_severity`` keys directly, so any additions go through the
  ``top_hits`` list rather than adding new root keys.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tests.testbench.pipeline import prompt_injection_detect as pid


router = APIRouter(prefix="/api/security", tags=["security"])


class ScanRequest(BaseModel):
    """Request body for ``POST /prompt_injection/scan``.

    Exactly one of ``text`` / ``fields`` must be populated. Both
    populated or neither → 400.
    """

    text: str | None = Field(
        default=None,
        description="Single blob to scan. Use for per-field editor badges.",
    )
    fields: dict[str, str] | None = Field(
        default=None,
        description=(
            "Map of field-name -> text for scanning a whole form / "
            "session at once. Empty map is treated the same as "
            "``text=''`` (no hits returned)."
        ),
    )


@router.post("/prompt_injection/scan")
async def scan_prompt_injection(body: ScanRequest) -> dict[str, Any]:
    """Scan ``text`` or ``fields`` for prompt-injection patterns.

    Returns the detector summary directly (single-text form) or
    ``{fields: {name: summary}}`` (multi-field form). Non-string field
    values are silently skipped.
    """
    if body.text is not None and body.fields is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": "MutuallyExclusive",
                "message": "Provide exactly one of `text` or `fields`.",
            },
        )
    if body.text is None and body.fields is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": "MissingInput",
                "message": "Either `text` or `fields` is required.",
            },
        )

    if body.text is not None:
        hits = pid.detect(body.text)
        return pid.summarize(hits)

    return {"fields": pid.detect_bulk(body.fields or {})}


@router.get("/prompt_injection/patterns")
async def list_patterns() -> dict[str, Any]:
    """Return the detector's pattern library (id + category + severity +
    human-readable description). Used by the UI to render a "legend"
    tooltip for the ``\u26a0 N`` badge without re-implementing the
    pattern list client-side.

    We deliberately do **not** return the raw regex strings — they're
    part of the threat model we don't want to leak through the API
    (easier for an attacker to tune around). Category / id alone are
    enough for the UI.
    """
    return {
        "categories": [
            pid.CATEGORY_CHATML,
            pid.CATEGORY_LLAMA,
            pid.CATEGORY_ROLE_MARKER,
            pid.CATEGORY_JAILBREAK,
            pid.CATEGORY_SYSTEM_IMPERSONATION,
        ],
        "patterns": [
            {
                "id": p.id,
                "category": p.category,
                "severity": p.severity,
                "description": p.description,
            }
            for p in pid.SUSPICIOUS_PATTERNS
        ],
        "max_hits_per_pattern": pid.MAX_HITS_PER_PATTERN,
    }
