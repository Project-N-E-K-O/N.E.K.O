"""Borrow credential ownership capabilities without importing application routers."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager

from services.chat_sync import host as credentials


def owner_from_snapshot(snapshot: dict | None) -> dict:
    snapshot = snapshot or {}
    user_id = snapshot.get("local_user_id") if snapshot.get("access_token") else None
    base = snapshot.get("base_url", "") if user_id else ""
    return {"key": hashlib.sha256(f"{base}\n{user_id or ''}".encode()).hexdigest(),
            "user_id": user_id, "community": base or None}


def saved_snapshot() -> dict | None:
    with credentials.saved_session_writer_fence():
        snapshot = credentials.saved_session_snapshot()
        return dict(snapshot) if snapshot else None


@contextmanager
def attribution_context():
    # Preferences acquire their own lock after this context. Keep credential ->
    # preferences ordering identical to recommendation evidence writes.
    with credentials.saved_session_writer_fence():
        yield owner_from_snapshot(credentials.saved_session_snapshot())


async def resolve_session() -> dict:
    return await credentials.resolve_saved_oauth_status()
