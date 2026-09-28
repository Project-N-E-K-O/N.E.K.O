"""Provider warm-up state owned by one worker queue.

Some providers need a one-off preparation step before they can transcribe
(a local model that is loaded, or downloaded on first use). That time is not
part of recognizing an utterance, so the runtime's provider-final watchdog
must not charge it against the per-utterance deadline. Like delivery evidence,
the state rides on the request queue the session and its worker share, so a
worker can publish it without a new event kind.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

_ATTRIBUTE = "_provider_warmup_state"


@dataclass(slots=True)
class ProviderWarmupState:
    pending: bool = False
    completed_at: float | None = None


def provider_warmup_state(queue: object) -> ProviderWarmupState | None:
    """Return the queue's warm-up state, or None when no worker published one."""

    state = getattr(queue, _ATTRIBUTE, None)
    return state if isinstance(state, ProviderWarmupState) else None


def begin_provider_warmup(queue: object) -> ProviderWarmupState:
    state = provider_warmup_state(queue)
    if state is None:
        state = ProviderWarmupState()
        setattr(queue, _ATTRIBUTE, state)
    state.pending = True
    return state


def complete_provider_warmup(queue: object) -> None:
    """Mark warm-up finished (successfully or not) at the current monotonic time."""

    state = provider_warmup_state(queue)
    if state is None:
        state = ProviderWarmupState()
        setattr(queue, _ATTRIBUTE, state)
    state.pending = False
    state.completed_at = time.monotonic()
