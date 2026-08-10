"""Short-lived confirmation tokens for chat-initiated mutations."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class _PendingConfirmation:
    expires_at: float
    action: str
    scope: str
    fingerprint: str
    payload: dict[str, Any]


class WriteConfirmationGate:
    """Bind a one-time token to an action and its exact payload."""

    def __init__(self, *, ttl_seconds: float = 300.0, max_pending: int = 256) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_pending = max(1, max_pending)
        self._pending: dict[str, _PendingConfirmation] = {}

    def issue(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        scope: str = "",
    ) -> str:
        self._discard_expired()
        while len(self._pending) >= self._max_pending:
            oldest = min(
                self._pending,
                key=lambda item: self._pending[item].expires_at,
            )
            self._pending.pop(oldest, None)
        token = secrets.token_urlsafe(18)
        self._pending[token] = _PendingConfirmation(
            expires_at=time.monotonic() + self._ttl_seconds,
            action=action,
            scope=scope,
            fingerprint=self._fingerprint(payload),
            payload=deepcopy(payload),
        )
        return token

    def authorize_or_issue(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        confirmed: bool,
        token: str,
        scope: str = "",
    ) -> tuple[bool, str]:
        """Authorize a valid one-time token, otherwise issue a new token."""
        if confirmed and self.consume(token, action, payload, scope=scope):
            return True, ""
        return False, self.issue(action, payload, scope=scope)

    def consume(
        self,
        token: str,
        action: str,
        payload: dict[str, Any],
        *,
        scope: str = "",
    ) -> bool:
        self._discard_expired()
        record = self._pending.pop(token, None)
        if record is None:
            return False
        return (
            record.expires_at >= time.monotonic()
            and record.action == action
            and record.scope == scope
            and record.fingerprint == self._fingerprint(payload)
        )

    def authorize_or_issue_opaque(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        confirmed: bool,
        token: str,
        scope: str = "",
    ) -> tuple[bool, str, dict[str, Any]]:
        """Authorize using a server-held payload so secrets never round-trip."""
        self._discard_expired()
        if confirmed:
            record = self._pending.pop(token, None)
            if (
                record is not None
                and record.expires_at >= time.monotonic()
                and record.action == action
                and record.scope == scope
            ):
                return True, "", deepcopy(record.payload)
        return False, self.issue(action, payload, scope=scope), payload

    def _discard_expired(self) -> None:
        now = time.monotonic()
        self._pending = {
            token: record
            for token, record in self._pending.items()
            if record.expires_at >= now
        }

    @staticmethod
    def _fingerprint(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def confirmation_scope(context: object) -> str:
    if not isinstance(context, dict):
        return ""
    value = context.get("conversation_id")
    return str(value).strip() if value is not None else ""
