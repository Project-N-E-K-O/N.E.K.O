"""Bounded, one-use authorization to inspect microphone PCM in isolation.

This registry never owns audio or starts a recorder. Its single reservation
blocks all registered Core producers until the requesting operation releases
it or its deadline expires. Expiry never reopens a retired microphone route.
"""

from __future__ import annotations

from dataclasses import dataclass
import secrets
import time
from typing import Callable
import weakref


class VoicePreviewIsolationError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(eq=False, slots=True)
class VoicePreviewTicket:
    token: str
    request_id: str
    noise_reduction_enabled: bool
    deadline: float
    registry: "VoicePreviewIsolationRegistry"
    owner: weakref.ReferenceType | None = None
    current: Callable[[], bool] | None = None
    ready: bool = False
    claimed: bool = False
    connection_id: str | None = None

    def as_dict(self) -> dict:
        self.validate_current()
        return {"token": self.token,
                "ttl_seconds": max(0.0, self.deadline - self.registry.now()),
                "noise_reduction_enabled": self.noise_reduction_enabled}

    def validate_current(self) -> None:
        self.registry.validate(self)


class VoicePreviewIsolationRegistry:
    """Event-loop-owned reservation, with weak manager lifetime tracking."""

    TTL_SECONDS = 30.0

    def __init__(self, *, now: Callable[[], float] = time.monotonic) -> None:
        self.now = now
        self._managers: weakref.WeakSet = weakref.WeakSet()
        self._ticket: VoicePreviewTicket | None = None
        # HTTP cleanup can release a claimed or cancelled ticket before the
        # producer's matching preview_end arrives. Keep a bounded cleanup receipt,
        # never the ticket/current callback that would retain its manager.
        self._consumed_releases: dict[str, tuple[weakref.ReferenceType, float]] = {}

    def _prune_consumed_releases(self) -> None:
        now = self.now()
        for token, (owner, deadline) in tuple(self._consumed_releases.items()):
            if owner() is None or now >= deadline:
                del self._consumed_releases[token]

    def register(self, manager: object) -> None:
        self._managers.add(manager)

    def _live_ticket(self) -> VoicePreviewTicket | None:
        ticket = self._ticket
        if ticket is not None and self.now() >= ticket.deadline:
            self._ticket = None
            return None
        return ticket

    def is_manager_isolated(self, manager: object) -> bool:
        del manager
        return self._live_ticket() is not None

    def _reserve(self, request_id: str, noise_reduction_enabled: bool) -> VoicePreviewTicket:
        if (type(request_id) is not str or not request_id.strip()
                or len(request_id) > 128 or type(noise_reduction_enabled) is not bool):
            raise VoicePreviewIsolationError("preview_request_invalid")
        if self._live_ticket() is not None:
            raise VoicePreviewIsolationError("preview_busy")
        ticket = VoicePreviewTicket(secrets.token_urlsafe(32), request_id,
                                    noise_reduction_enabled,
                                    self.now() + self.TTL_SECONDS, self)
        self._ticket = ticket
        return ticket

    @staticmethod
    def _active(manager: object) -> bool:
        # Route and lease describe actual ingress authority, independent of
        # the display window and whether the downstream transport is idle.
        return (manager._asr_route_mode != "blocked"
                or (manager._voice_lease_synchronized
                    and manager._voice_lease_owner not in {None, "none"}
                    and not manager._voice_lease_hard_muted
                    and not manager._voice_lease_focus_suppressed))

    def begin_inactive(self, request_id: str, *,
                       noise_reduction_enabled: bool = True) -> VoicePreviewTicket:
        if any(self._active(manager) for manager in tuple(self._managers)):
            raise VoicePreviewIsolationError("preview_owner_active")
        ticket = self._reserve(request_id, noise_reduction_enabled)
        ticket.ready = True
        return ticket

    def begin(self, manager: object, request_id: str, *,
              noise_reduction_enabled: bool,
              current: Callable[[], bool], connection_id: str | None = None) -> VoicePreviewTicket:
        if any(other is not manager and self._active(other)
               for other in tuple(self._managers)):
            raise VoicePreviewIsolationError("preview_owner_active")
        ticket = self._reserve(request_id, noise_reduction_enabled)
        ticket.owner = weakref.ref(manager)
        ticket.current = current
        ticket.connection_id = connection_id
        return ticket

    def mark_ready(self, ticket: VoicePreviewTicket) -> None:
        self.validate(ticket, require_ready=False)
        if any(self._active(manager) for manager in tuple(self._managers)):
            raise VoicePreviewIsolationError("preview_owner_active")
        ticket.ready = True

    def validate(self, ticket: VoicePreviewTicket, *, require_ready: bool = True) -> None:
        if self._live_ticket() is not ticket:
            raise VoicePreviewIsolationError("preview_expired")
        if ticket.owner is not None and ticket.owner() is None:
            raise VoicePreviewIsolationError("preview_owner_changed")
        if ticket.current is not None and not ticket.current():
            raise VoicePreviewIsolationError("preview_owner_changed")
        if require_ready and not ticket.ready:
            raise VoicePreviewIsolationError("preview_not_ready")

    def claim(self, token: str) -> VoicePreviewTicket:
        ticket = self._live_ticket()
        if (type(token) is not str or not token.isascii() or len(token) > 128 or ticket is None
                or not secrets.compare_digest(ticket.token, token)):
            raise VoicePreviewIsolationError("preview_invalid")
        self.validate(ticket)
        if ticket.claimed:
            raise VoicePreviewIsolationError("preview_consumed")
        ticket.claimed = True
        return ticket

    def release(self, ticket_or_token: VoicePreviewTicket | str) -> bool:
        ticket = self._live_ticket()
        matches = (ticket is ticket_or_token if isinstance(ticket_or_token, VoicePreviewTicket)
                   else ticket is not None and isinstance(ticket_or_token, str)
                   and ticket_or_token.isascii() and len(ticket_or_token) <= 128
                   and secrets.compare_digest(ticket.token, ticket_or_token))
        if not matches:
            return False
        if ticket.owner is not None and ticket.owner() is not None:
            self._prune_consumed_releases()
            self._consumed_releases[ticket.token] = (ticket.owner, ticket.deadline)
            while len(self._consumed_releases) > 32:
                del self._consumed_releases[next(iter(self._consumed_releases))]
        self._ticket = None
        return True

    def release_connection(self, manager: object, connection_id: str) -> bool:
        ticket = self._live_ticket()
        if (ticket is None or ticket.owner is None or ticket.owner() is not manager
                or ticket.connection_id != connection_id):
            return False
        return self.release(ticket)

    def release_owned(self, token: str, manager: object) -> bool:
        if type(token) is not str or not token.isascii() or len(token) > 128:
            raise VoicePreviewIsolationError("preview_invalid")
        self._prune_consumed_releases()
        receipt = self._consumed_releases.get(token)
        if receipt is not None and receipt[0]() is manager:
            # Acknowledge only this owner's genuinely released ticket. An old
            # cleanup must not release a new reservation or restore PCM input.
            return True
        ticket = self._live_ticket()
        if ticket is None or ticket.owner is None or ticket.owner() is not manager:
            raise VoicePreviewIsolationError("preview_invalid")
        if not self.release(token):
            raise VoicePreviewIsolationError("preview_invalid")
        return True


preview_isolation_registry = VoicePreviewIsolationRegistry()
