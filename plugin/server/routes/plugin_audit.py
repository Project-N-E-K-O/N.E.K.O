"""Local-only, read-only view of the plugin call-chain audit.

Only native local clients (command line, scripts) may read it: the client and
the Host must be loopback, no forwarding headers, and no browser ``Origin``.
See docs/design/plugin-call-chain.md section 3.9.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from plugin.core.capability_policy import current_mode
from plugin.server.infrastructure.mutation_auth import _local_request

router = APIRouter(prefix="/plugin-audit", tags=["plugin-audit"])

MAX_LIMIT = 1000


def require_native_local_client(request: Request) -> None:
    if request.headers.get("origin") is not None or not _local_request(request):
        raise HTTPException(status_code=403, detail={"code": "LOCAL_ONLY"})


@router.get("/call-chain")
async def get_call_chain_audit(
    _: None = Depends(require_native_local_client),
    limit: int = Query(200, ge=1, le=MAX_LIMIT),
    trace_id: str | None = Query(None, max_length=128),
    plugin_id: str | None = Query(None, max_length=256),
    kind: str | None = Query(None, max_length=32),
    verdict: str | None = Query(None, max_length=32),
) -> dict[str, object]:
    from plugin.server.application.plugins import capability_gate

    mode = current_mode()
    filters = {"trace_id": trace_id, "kind": kind, "verdict": verdict}
    entries = capability_gate.audit.entries(limit=MAX_LIMIT if plugin_id else limit, filters=filters)
    if plugin_id:
        # A plugin appears as the user, the target or the caller of an entry.
        entries = [
            entry for entry in entries
            if plugin_id in (entry.get("plugin"), entry.get("target"), entry.get("caller"))
        ][:limit]
    return {
        "mode": mode,
        "entries": entries,
        "registry_full": capability_gate.registry.registry_full,
        **capability_gate.audit.stats(),
    }
