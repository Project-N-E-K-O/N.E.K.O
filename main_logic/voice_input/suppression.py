"""Revocable, TTL-bounded suppression for host-owned microphone workflows."""

from __future__ import annotations

import asyncio
import math
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


SuppressionCallback = Callable[[str], Awaitable[None]]


class VoiceInputSuppressionError(RuntimeError):
    """Raised when a suppression lease cannot be acquired."""


@dataclass(frozen=True, slots=True)
class VoiceInputSuppressionSnapshot:
    """Public state for diagnostics without exposing the capability token."""

    active: bool
    reason: str | None
    expires_at: float | None


class VoiceInputSuppressionLease:
    """One controller-issued, idempotently releasable suppression capability."""

    __slots__ = ("_controller", "_released", "_token", "expires_at")

    def __init__(
        self,
        controller: VoiceInputSuppressionController,
        token: str,
        expires_at: float,
    ) -> None:
        self._controller = controller
        self._token = token
        self.expires_at = expires_at
        self._released = False

    @property
    def released(self) -> bool:
        return self._released

    async def release(self) -> bool:
        if self._released:
            return False
        self._released = True
        return await self._controller._release(self._token)


class VoiceInputSuppressionController:
    """Serialize one temporary microphone suppression with hard TTL recovery."""

    def __init__(
        self,
        on_suppress: SuppressionCallback,
        on_restore: SuppressionCallback,
        *,
        default_ttl_seconds: float = 30.0,
        hard_ttl_seconds: float = 60.0,
        callback_timeout_seconds: float = 10.0,
    ) -> None:
        if not callable(on_suppress) or not callable(on_restore):
            raise TypeError("suppression callbacks must be callable")
        if (
            not math.isfinite(default_ttl_seconds)
            or not math.isfinite(hard_ttl_seconds)
            or default_ttl_seconds <= 0
            or hard_ttl_seconds <= 0
            or default_ttl_seconds > hard_ttl_seconds
            or not math.isfinite(callback_timeout_seconds)
            or callback_timeout_seconds <= 0
        ):
            raise ValueError("suppression TTLs are invalid")
        self._on_suppress = on_suppress
        self._on_restore = on_restore
        self._default_ttl_seconds = float(default_ttl_seconds)
        self._hard_ttl_seconds = float(hard_ttl_seconds)
        self._callback_timeout_seconds = float(callback_timeout_seconds)
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._reason: str | None = None
        self._expires_at: float | None = None
        self._expiry_task: asyncio.Task[None] | None = None
        self._cleanup_tasks: set[asyncio.Task[bool]] = set()
        self._closed = False

    def snapshot(self) -> VoiceInputSuppressionSnapshot:
        return VoiceInputSuppressionSnapshot(
            active=self._token is not None,
            reason=self._reason,
            expires_at=self._expires_at,
        )

    async def acquire(
        self,
        reason: str,
        *,
        ttl_seconds: float | None = None,
    ) -> VoiceInputSuppressionLease:
        normalized_reason = str(reason or "").strip()
        if not normalized_reason:
            raise ValueError("suppression reason is required")
        ttl = self._default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if not math.isfinite(ttl) or ttl <= 0 or ttl > self._hard_ttl_seconds:
            raise ValueError("suppression TTL exceeds the hard limit")

        async with self._lock:
            if self._closed:
                raise VoiceInputSuppressionError("suppression controller closed")
            if self._token is not None:
                raise VoiceInputSuppressionError("suppression already active")
            token = secrets.token_urlsafe(24)
            self._token = token
            self._reason = normalized_reason
            loop = asyncio.get_running_loop()
            expires_at = loop.time() + float(ttl)
            self._expires_at = expires_at
            self._expiry_task = loop.create_task(
                self._expire(token, float(ttl)),
                name="voice-input-suppression-expiry",
            )
        try:
            await asyncio.wait_for(
                self._on_suppress(normalized_reason),
                timeout=min(float(ttl), self._callback_timeout_seconds),
            )
        except BaseException:
            cleanup = self._schedule_release(token)
            current = asyncio.current_task()
            if current is None or not current.cancelling():
                await asyncio.gather(cleanup, return_exceptions=True)
            raise
        async with self._lock:
            if self._token != token:
                raise VoiceInputSuppressionError(
                    "suppression expired during acquisition"
                )
        return VoiceInputSuppressionLease(self, token, expires_at)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            token = self._token
        if token is not None:
            try:
                await self._release(token)
            except Exception:
                pass
        current = asyncio.current_task()
        cleanup_tasks = tuple(
            task for task in self._cleanup_tasks if task is not current
        )
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)

    async def _expire(self, token: str, ttl_seconds: float) -> None:
        try:
            await asyncio.sleep(ttl_seconds)
            await self._release(token, expiry_task=asyncio.current_task())
        except asyncio.CancelledError:
            raise
        except Exception:
            # The capability is already revoked before restore is attempted.
            # Callers own any additional runtime watchdog/retry policy.
            return

    async def _release(
        self,
        token: str,
        *,
        expiry_task: asyncio.Task[None] | None = None,
    ) -> bool:
        async with self._lock:
            if self._token != token:
                return False
            reason = self._reason or "voice_input_suppression"
            task = self._expiry_task
            self._token = None
            self._reason = None
            self._expires_at = None
            self._expiry_task = None
        if task is not None and task is not expiry_task:
            task.cancel()
        await asyncio.wait_for(
            self._on_restore(reason),
            timeout=self._callback_timeout_seconds,
        )
        return True

    def _schedule_release(self, token: str) -> asyncio.Task[bool]:
        task = asyncio.create_task(
            self._release(token),
            name="voice-input-suppression-failed-acquire-cleanup",
        )
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)
        return task
