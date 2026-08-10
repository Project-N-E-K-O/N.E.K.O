"""Short-lived confirmation tokens for chat-initiated mutations."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any


class WriteConfirmationGate:
    """Bind a one-time token to an action and its exact payload."""

    def __init__(self, *, ttl_seconds: float = 300.0) -> None:
        self._ttl_seconds = ttl_seconds
        self._pending: dict[str, tuple[float, str, str]] = {}

    def issue(self, action: str, payload: dict[str, Any]) -> str:
        self._discard_expired()
        token = secrets.token_urlsafe(18)
        self._pending[token] = (
            time.monotonic() + self._ttl_seconds,
            action,
            self._fingerprint(payload),
        )
        return token

    def authorize_or_issue(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        confirmed: bool,
        token: str,
    ) -> tuple[bool, str]:
        """Authorize a valid one-time token, otherwise issue a new token."""
        if confirmed and self.consume(token, action, payload):
            return True, ""
        return False, self.issue(action, payload)

    def consume(self, token: str, action: str, payload: dict[str, Any]) -> bool:
        self._discard_expired()
        record = self._pending.pop(token, None)
        if record is None:
            return False
        expires_at, expected_action, expected_fingerprint = record
        return (
            expires_at >= time.monotonic()
            and expected_action == action
            and expected_fingerprint == self._fingerprint(payload)
        )

    def _discard_expired(self) -> None:
        now = time.monotonic()
        self._pending = {
            token: record
            for token, record in self._pending.items()
            if record[0] >= now
        }

    @staticmethod
    def _fingerprint(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
