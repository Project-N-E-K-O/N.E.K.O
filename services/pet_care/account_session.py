"""N.E.K.O account-session adapter used by the local PetCare mirror.

PetCare depends on the N.E.K.O account contract, not on a community-owned
account.  The current desktop adapter still resolves that contract through
``community_oauth``; keeping the translation here makes the future Verse or
cloud-session provider replaceable without changing PetCare routes/services.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

import main_routers.card_drop_router as card_drop
import main_routers.community_oauth as community_oauth


async def resolve_verified_account_id(request: Request) -> str:
    """Return the verified N.E.K.O account id for one local request."""
    if not card_drop._local_request_source_allowed(request):
        raise HTTPException(status_code=403, detail="origin_not_allowed")
    identity = await community_oauth._account_request_identity(request)
    if identity is None:
        raise HTTPException(status_code=403, detail="loopback_only")

    status: dict[str, Any] = await community_oauth.resolve_saved_oauth_status()
    if not status.get("logged_in"):
        if community_oauth.status_session_saved(status):
            raise HTTPException(status_code=503, detail="community_auth_unavailable")
        raise HTTPException(status_code=401, detail="login_required")

    snapshot = status.get("snapshot") or {}
    account_id = card_drop._normalize_local_user_id(
        snapshot.get("local_user_id") or status.get("local_user_id")
    )
    if not account_id:
        raise HTTPException(status_code=503, detail="account_identity_unavailable")
    return account_id
