"""Provider warm-up state owned by one worker queue.

Some providers need a one-off preparation step before they can transcribe
(a local model that is loaded, or downloaded on first use). That time is not
part of recognizing an utterance, so the runtime's provider-final watchdog
must not charge it against the per-utterance deadline. Like delivery evidence,
the state rides on the request queue the session and its worker share, so a
worker can publish it without a new event kind.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

_ATTRIBUTE = "_provider_warmup_state"


@dataclass(slots=True)
class ProviderWarmupState:
    pending: bool = False
    completed_at: float | None = None
    # One token per outstanding wait (model load, a decode queued behind other
    # sessions). A job finishing must only end its own wait: an older, cancelled
    # job leaving the queue cannot clear a newer job's pending state.
    waiters: set[object] = field(default_factory=set)
    # Waits begin on the event loop and may end on a worker thread.
    lock: threading.Lock = field(default_factory=threading.Lock)


def provider_warmup_state(queue: object) -> ProviderWarmupState | None:
    """Return the queue's warm-up state, or None when no worker published one."""

    state = getattr(queue, _ATTRIBUTE, None)
    return state if isinstance(state, ProviderWarmupState) else None


def begin_provider_warmup(queue: object) -> object:
    """Start one warm-up wait and return the token that ends it.

    Call on the event loop: the state object is created lazily here, so a
    worker thread only ever mutates an existing one.
    """
    state = provider_warmup_state(queue)
    if state is None:
        state = ProviderWarmupState()
        setattr(queue, _ATTRIBUTE, state)
    token = object()
    with state.lock:
        state.waiters.add(token)
        state.pending = True
    return token


def complete_provider_warmup(queue: object, token: object) -> None:
    """End the wait ``token`` began; warm-up is over once no wait remains.

    ``completed_at`` is stamped (monotonic) when the last outstanding wait ends,
    successfully or not.
    """
    state = provider_warmup_state(queue)
    if state is None:
        return
    with state.lock:
        state.waiters.discard(token)
        if not state.waiters:
            state.pending = False
            state.completed_at = time.monotonic()
