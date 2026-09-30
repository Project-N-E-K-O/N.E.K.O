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
    # Why the provider is preparing, as an ``ASR_*`` code the client can
    # explain (a first load that may download, or a reload after idling).
    reason: str = ""


def provider_warmup_state(queue: object) -> ProviderWarmupState | None:
    """Return the queue's warm-up state, or None when no worker published one."""

    state = getattr(queue, _ATTRIBUTE, None)
    return state if isinstance(state, ProviderWarmupState) else None


def provider_warmup_snapshot(queue: object) -> tuple[bool, float | None]:
    """``(pending, completed_at)`` read together under the state's lock.

    A wait may begin or end on a worker thread between two separate reads, so
    a reader that needs both must take them in one snapshot.
    """
    state = provider_warmup_state(queue)
    if state is None:
        return False, None
    with state.lock:
        return bool(state.pending), state.completed_at


def ensure_provider_warmup_state(queue: object) -> ProviderWarmupState:
    """Return the queue's warm-up state, creating it if needed.

    Call on the event loop. Once it exists, waits may begin and end on any
    thread: the state is only mutated under its lock.
    """
    state = provider_warmup_state(queue)
    if state is None:
        state = ProviderWarmupState()
        setattr(queue, _ATTRIBUTE, state)
    return state


def begin_provider_warmup(queue: object, reason: str = "") -> object:
    """Start one warm-up wait and return the token that ends it.

    ``reason`` (an ``ASR_*`` code) replaces the published reason when given.
    Call on the event loop unless ``ensure_provider_warmup_state`` already ran
    there: the state object is created lazily here.
    """
    state = ensure_provider_warmup_state(queue)
    token = object()
    with state.lock:
        state.waiters.add(token)
        state.pending = True
        if reason:
            state.reason = reason
    return token


def provider_warmup_reason(queue: object) -> str:
    """The published reason of the queue's warm-up, or ``""``."""
    state = provider_warmup_state(queue)
    if state is None:
        return ""
    with state.lock:
        return state.reason


def complete_provider_warmup(queue: object, token: object) -> None:
    """End the wait ``token`` began; warm-up is over once no wait remains.

    ``completed_at`` is stamped (monotonic) when the last outstanding wait ends,
    successfully or not.
    """
    state = provider_warmup_state(queue)
    if state is None:
        return
    with state.lock:
        if token not in state.waiters:
            # Already ended (e.g. by both the load task and the worker's own
            # cleanup) or never began: nothing to end, no new completion time.
            return
        state.waiters.discard(token)
        if not state.waiters:
            # Stamp first: a reader seeing ``pending`` False must also see
            # the new completion time, not a stale or missing one.
            state.completed_at = time.monotonic()
            state.pending = False
