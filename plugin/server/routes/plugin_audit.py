"""Read-only call-chain audit for local native clients (docs/design/plugin-call-chain.md 3.9).

Allowed only when the client and the Host are loopback, there are no
forwarding headers and no browser ``Origin``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from plugin.core.capability_policy import current_mode
from plugin.server.application.plugins import call_audit, capability_gate
from plugin.server.infrastructure.mutation_auth import _local_request

router = APIRouter()


@router.get("/plugin-audit/call-chain")
async def get_call_chain_audit(request: Request) -> dict[str, object]:
    if request.headers.get("origin") is not None or not _local_request(request):
        raise HTTPException(status_code=403, detail={"code": "LOCAL_ONLY"})
    return {
        "mode": current_mode(),
        "entries": call_audit.entries(),
        "dropped": call_audit.dropped,
        "registry_full": capability_gate.registry.registry_full,
    }
