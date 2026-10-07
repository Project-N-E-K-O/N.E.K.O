"""A worker's own failure code, published on the queue it shares with its session.

A worker that fails right after reporting "ready" may return before the
session has consumed its error event. Recording the code on the request queue
first (like delivery evidence and warm-up state) lets the session keep the
real reason instead of classifying the exit generically, without reading the
response queue's internals or racing its consumer.
"""

from __future__ import annotations

_ATTRIBUTE = "_provider_worker_failure"


def record_worker_failure(queue: object, code: str, message: str) -> None:
    """Remember the first failure a worker reports; later ones are ignored."""
    if getattr(queue, _ATTRIBUTE, None) is None:
        setattr(queue, _ATTRIBUTE, (str(code or ""), str(message or "")))


def recorded_worker_failure(queue: object) -> tuple[str, str] | None:
    """``(code, message)`` the worker recorded, or None."""
    failure = getattr(queue, _ATTRIBUTE, None)
    if isinstance(failure, tuple) and len(failure) == 2:
        return failure
    return None
