"""``/plugins/install-sources`` read-only endpoint (Req 16 / design §12).

Exposes the install-source lock as a paginated, filterable list so that
operators and UIs can answer questions like "which plugins did I install
from the market?" or "which entries have been soft-deleted?". The
implementation is deliberately thin: all validation and filtering happens
inside :meth:`InstallSourceManager.list_entries`, and the route only
translates ``InstallSourceError`` codes into the HTTP status codes
required by Req 16.7 (422) and Req 17.3 (503 with ``retry_after``).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from plugin.logging_config import get_logger
from plugin.server.application.install_source import (
    InstallSourceError,
    get_install_source_manager,
)
from plugin.server.application.install_source.manager import (
    _serialize_source_detail_for_json,
)
from plugin.server.application.install_source.models import LockEntry
from plugin.server.infrastructure.auth import require_admin

router = APIRouter()
logger = get_logger("server.routes.install_sources")

# Matches the Fix 9 window in ``install_source/__init__.py``.
# Surfaced to clients so they know when to retry after a 503.
_RETRY_AFTER_SECONDS = 60


class InstallSourceEntryResponse(BaseModel):
    """One row in the ``/plugins/install-sources`` response.

    Fields mirror the on-disk lock schema directly — see design §5.2 and
    :class:`LockEntry`. ``source`` mirrors ``channel`` (Req 3.6) and is
    always present so clients that only check ``source`` keep working.
    """

    root_id: str
    directory_name: str
    plugin_id: str
    channel: str
    source: str
    reason: str
    installed_at: str
    updated_at: str
    last_seen_at: str
    removed: bool
    removed_at: str | None = None
    bundle_ref: Any | None = None
    source_detail: dict[str, Any] | None = None


class InstallSourcesListResponse(BaseModel):
    schema_version: int = 1
    entries: list[InstallSourceEntryResponse] = Field(default_factory=list)
    updated_at: str


def _entry_to_response(entry: LockEntry) -> dict[str, Any]:
    """Flatten a :class:`LockEntry` into the JSON shape the API returns.

    ``source`` is synthesised from ``channel`` per Req 3.6, ``removed_at``
    is emitted as ``None`` when the entry is live (the on-disk serializer
    omits the key entirely, but API clients benefit from an explicit
    ``null``), and ``source_detail`` goes through the shared serializer
    so that ``extra_fields`` and ``previous_version`` round-trip
    correctly.
    """

    return {
        "root_id": entry.root_id,
        "directory_name": entry.directory_name,
        "plugin_id": entry.plugin_id,
        "channel": entry.channel,
        "source": entry.channel,
        "reason": entry.reason,
        "installed_at": entry.installed_at,
        "updated_at": entry.updated_at,
        "last_seen_at": entry.last_seen_at,
        "removed": entry.removed,
        "removed_at": entry.removed_at if entry.removed else None,
        "bundle_ref": entry.bundle_ref,
        "source_detail": _serialize_source_detail_for_json(entry.source_detail),
    }


@router.get(
    "/plugins/install-sources",
    response_model=InstallSourcesListResponse,
)
async def list_install_sources(
    include_removed: bool = Query(default=False),
    source: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    reason: str | None = Query(default=None),
    root_id: str | None = Query(default=None),
    _: str = require_admin,
) -> dict[str, object]:
    """Return the filtered install-source lock as JSON.

    Status codes:

    * ``200`` — happy path.
    * ``422`` — any filter value is outside its legal enumeration
      (Req 16.7). Body: ``{"error": "INVALID_FILTER", "field": "..."}``.
    * ``503`` — the install-source manager is not yet initialised or
      is currently degraded (Req 17.3 + Fix 9). Body:
      ``{"error": "install_source_manager_unavailable",
      "reason": "<degrade reason>", "retry_after": 60}``.

    The actual filtering happens inside ``list_entries`` (which is
    sync); we offload it to a thread because the endpoint is
    ``async def`` and the call reads an in-memory snapshot under GIL
    only — no blocking IO, so the offload is mostly about playing
    nicely with the rest of the async call chain.
    """

    mgr = get_install_source_manager()  # Fix 9: internally attempts try_recover.
    if mgr is None or mgr.is_degraded:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "install_source_manager_unavailable",
                "reason": mgr.degrade_reason if mgr else "not_initialized",
                "retry_after": _RETRY_AFTER_SECONDS,
            },
        )

    try:
        entries = await asyncio.to_thread(
            mgr.list_entries,
            include_removed=include_removed,
            channel=channel,
            source=source,
            reason=reason,
            root_id=root_id,
        )
    except InstallSourceError as exc:
        if exc.code == "INVALID_FILTER":
            raise HTTPException(
                status_code=422,
                detail={
                    "error": exc.code,
                    "field": exc.details.get("field", ""),
                    "value": exc.details.get("value", ""),
                },
            ) from exc
        # Any other InstallSourceError on the read path is unexpected;
        # surface it as a 500 so the bug is obvious.
        logger.warning(
            "list_install_sources: unexpected InstallSourceError code=%s",
            exc.code,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": exc.code, "message": exc.message},
        ) from exc

    return {
        "schema_version": 1,
        "entries": [_entry_to_response(e) for e in entries],
        "updated_at": mgr.current_updated_at,
    }
