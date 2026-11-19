"""Read-only call-chain audit for local native clients (docs/design/plugin-call-chain.md 3.9).

Allowed only when the client and the Host are loopback, there are no
forwarding headers and no browser page is asking: ``Origin`` is refused, and
so is any ``Sec-Fetch-Site`` other than ``none`` (a same-origin page omits
``Origin`` on GET but sends ``Sec-Fetch-Site: same-origin``). Node and
Electron clients send ``Sec-Fetch-Mode`` only, so they are not refused.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from plugin.core.capability_policy import current_mode
from plugin.server.application.plugins import call_audit, capability_gate
from plugin.server.infrastructure.mutation_auth import _local_request

router = APIRouter()


@router.get("/plugin-audit/call-chain")
async def get_call_chain_audit(request: Request) -> dict[str, object]:
    fetch_site = request.headers.get("sec-fetch-site")
    from_browser = "origin" in request.headers or (fetch_site is not None and fetch_site != "none")
    if from_browser or not _local_request(request):
        raise HTTPException(status_code=403, detail={"code": "LOCAL_ONLY"})
    mode = current_mode()
    return {
        "mode": mode,
        "entries": [] if mode == "off" else call_audit.entries(),  # both rings are bounded (1500 total)
        "dropped": call_audit.dropped,
        "registry_full": capability_gate.registry.registry_full,
    }
