"""N.E.K.O account-session adapter used by the local PetCare mirror.

PetCare depends on the N.E.K.O account contract, not on a community-owned
account.  The current desktop adapter still resolves that contract through
``community_oauth``; keeping the translation here makes the future Verse or
cloud-session provider replaceable without changing PetCare routes/services.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

import main_routers.card_drop_router as card_drop
import main_routers.community_oauth as community_oauth


@dataclass(frozen=True)
class VerifiedAccountSession:
    """Trusted N.E.K.O account context passed into the local PetCare service.

    ``session_id`` is deliberately opaque and is supplied by the unified
    account adapter.  PetCare never receives access tokens or browser time;
    an absent id means the provider has not established a Stage 8 session
    boundary yet, so the service must skip offline reconciliation.
    """

    account_id: str
    session_id: str = ""
    auth_state: str = "valid"
    session_saved: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "session_id": self.session_id,
            "auth_state": self.auth_state,
            "session_saved": self.session_saved,
        }


async def resolve_verified_account_session(request: Request) -> VerifiedAccountSession:
    """Return the verified account and stable login-session identity.

    The local OAuth adapter currently exposes ``session_id`` only for a
    credential publication/login.  Token refreshes preserve it.  When an
    older session has no id, the account remains usable but Stage 8 simply
    performs ordinary reads until the provider can supply the contract.
    """
    if not card_drop.local_request_source_allowed(request):
        raise HTTPException(status_code=403, detail="origin_not_allowed")
    identity = await community_oauth.account_request_identity(request)
    if identity is None:
        raise HTTPException(status_code=403, detail="loopback_only")

    status: dict[str, Any] = await community_oauth.resolve_saved_oauth_status()
    if not status.get("logged_in"):
        if community_oauth.status_session_saved(status):
            raise HTTPException(status_code=503, detail="community_auth_unavailable")
        raise HTTPException(status_code=401, detail="login_required")

    snapshot = status.get("snapshot") or {}
    account_id = card_drop.normalize_local_user_id(
        snapshot.get("local_user_id") or status.get("local_user_id")
    )
    if not account_id:
        raise HTTPException(status_code=503, detail="account_identity_unavailable")
    session_id = str(
        snapshot.get("session_id")
        or status.get("session_id")
        or ""
    ).strip()
    return VerifiedAccountSession(
        account_id=account_id,
        session_id=session_id,
        auth_state="valid",
        session_saved=bool(status.get("session_saved")),
    )


async def resolve_verified_account_id(request: Request) -> str:
    """Return the verified N.E.K.O account id for one local request."""
    return (await resolve_verified_account_session(request)).account_id
