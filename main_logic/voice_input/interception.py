"""Provider-neutral bridge for ACTIVE-session speaker interception.

The bridge deliberately owns no speaker model.  A caller supplies a
session-scoped runtime which performs calibrated identity and target-speaker
extraction.  The Core side only accepts an explicit ``KEEP`` result carrying
PCM; every other result is a drop/gap.  This makes the production ASR boundary
fail closed while keeping model code below the Core layer.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Callable, Protocol

from main_logic.voice_turn.interception_events import (
    InterceptionDeliveryReceipt, InterceptionDeliveryStage,
    InterceptionOutputEvent, InterceptionOutputIdentity, InterceptionOutputKind,
)


class InterceptionInstallationState(str, Enum):
    PENDING = "pending"
    INSTALLED = "installed"
    INVALIDATED = "invalidated"
    REVOKED = "revoked"


@dataclass(eq=False, slots=True)
class InterceptionInstallation:
    """One installation attempt, shared by Core and its application owner.

    Temporary invalidation permits the owner to retry its current authority.
    Revocation requires an explicit new installation request. Notifications
    are synchronous, nonblocking and carry this exact attempt identity.
    """

    on_invalidated: Callable[[InterceptionInstallation], None]
    state: InterceptionInstallationState = InterceptionInstallationState.PENDING

    def invalidate(self, *, recoverable: bool) -> None:
        if self.state is InterceptionInstallationState.REVOKED:
            return
        state = (
            InterceptionInstallationState.INVALIDATED if recoverable
            else InterceptionInstallationState.REVOKED
        )
        if self.state is state:
            return
        self.state = state
        self.on_invalidated(self)


class InterceptionDecision(str, Enum):
    """Decision returned by a session-scoped interception runtime."""

    KEEP = "keep"
    DROP = "drop"
    PENDING = "pending"
    UNCERTAIN = "uncertain"
    UNAVAILABLE = "unavailable"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class InterceptionResult:
    """A filtered result ready for the provider boundary.

    ``pcm16`` is meaningful only for ``KEEP``.  The bridge rejects PCM on any
    other decision so a model failure cannot accidentally become a raw-audio
    fallback.
    """

    decision: InterceptionDecision
    pcm16: bytes = b""
    reason: str = ""
    events: tuple[InterceptionOutputEvent, ...] = ()

    def __post_init__(self) -> None:
        if type(self.decision) is not InterceptionDecision:
            raise TypeError("interception decision must be InterceptionDecision")
        if type(self.pcm16) is not bytes:
            raise TypeError("interception pcm16 must be bytes")
        if self.decision is InterceptionDecision.KEEP and not self.pcm16:
            raise ValueError("KEEP requires non-empty filtered PCM")
        if self.decision is not InterceptionDecision.KEEP and self.pcm16:
            raise ValueError("non-KEEP interception result cannot carry PCM")
        if type(self.events) is not tuple or any(
            type(event) is not InterceptionOutputEvent for event in self.events
        ):
            raise TypeError("interception events must be an immutable event tuple")
        if self.events:
            audio = b"".join(event.pcm16 for event in self.events)
            if audio != self.pcm16:
                raise ValueError("interception PCM must exactly match event audio")
            identity = self.events[0].identity
            for previous, current in zip(self.events, self.events[1:]):
                if (current.identity != identity
                        or current.sequence != previous.sequence + 1
                        or current.start_sample != previous.end_sample
                        or previous.kind is InterceptionOutputKind.END):
                    raise ValueError("interception events must be ordered and contiguous")


class ActiveSessionInterceptionRuntime(Protocol):
    """One runtime owned by one activation/session generation."""

    async def process(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
        generation: object,
        ingress_token: object | None,
        captured_at: float | None,
    ) -> InterceptionResult: ...

    async def finish(self) -> InterceptionResult: ...

    def record_delivery(self, receipt: InterceptionDeliveryReceipt) -> None: ...

    async def close(self, reason: str = "retired") -> None: ...


class ActiveSessionInterceptionFactory(Protocol):
    """Creates a model-backed runtime for one session/generation."""

    def create(
        self,
        generation: object,
        *,
        ingress_token: object | None,
    ) -> ActiveSessionInterceptionRuntime: ...

    def close(self) -> None: ...


class ActiveSessionInterceptionBridge:
    """Serialize model decisions and fence late results before ASR delivery."""

    def __init__(
        self,
        factory: ActiveSessionInterceptionFactory,
        *,
        required: bool = True,
        process_timeout_s: float = 1.0,
        close_timeout_s: float = 1.0,
        max_inflight: int = 1,
        terminal_on_unavailable: bool = False,
    ) -> None:
        # A declared availability value is authority evidence, not a model
        # allocation or a promise that the next runtime will start. Legacy
        # injected factories need not declare it; known retired authority must
        # be rejected before Core can publish an installed receipt.
        availability = getattr(factory, "is_available", True)
        if type(availability) is not bool:
            raise TypeError("factory is_available must be bool")
        if not availability:
            raise RuntimeError("interception_factory_unavailable")
        self._factory = factory
        self._required = required
        if (isinstance(process_timeout_s, bool)
                or not isinstance(process_timeout_s, (int, float))
                or not math.isfinite(float(process_timeout_s))
                or process_timeout_s <= 0):
            raise ValueError("process_timeout_s must be positive")
        if (isinstance(close_timeout_s, bool)
                or not isinstance(close_timeout_s, (int, float))
                or not math.isfinite(float(close_timeout_s))
                or close_timeout_s <= 0):
            raise ValueError("close_timeout_s must be positive")
        if type(max_inflight) is not int or max_inflight <= 0:
            raise ValueError("max_inflight must be positive")
        self._process_timeout_s = float(process_timeout_s)
        self._close_timeout_s = float(close_timeout_s)
        self._runtime: ActiveSessionInterceptionRuntime | None = None
        self._generation: object | None = None
        self._ingress_token: object | None = None
        self._lock = asyncio.Lock()
        self._max_inflight = max_inflight
        if type(terminal_on_unavailable) is not bool:
            raise TypeError("terminal_on_unavailable must be bool")
        self._terminal_on_unavailable = terminal_on_unavailable
        self._inflight = 0
        self._retirement_task: asyncio.Task[bool] | None = None
        self._retiring_runtime: ActiveSessionInterceptionRuntime | None = None
        self._retiring = False
        self._closed = False
        self._has_identity = False
        self._finished = False
        self._terminal_identity: tuple[object, object | None] | None = None
        self._terminal_reason = ""

    @property
    def required(self) -> bool:
        return self._required

    @property
    def generation(self) -> object | None:
        return self._generation

    async def process(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
        generation: object,
        ingress_token: object | None,
        captured_at: float | None,
    ) -> InterceptionResult:
        if type(pcm16) is not bytes or not pcm16:
            return InterceptionResult(InterceptionDecision.DROP, reason="empty_pcm")
        if self._closed:
            return InterceptionResult(InterceptionDecision.STALE, reason="bridge_closed")
        if self._terminal_identity == (generation, ingress_token):
            return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason=self._terminal_reason)
        if self._inflight >= self._max_inflight:
            return self._unavailable(generation, ingress_token, "interception_capacity")
        self._inflight += 1
        runtime = None
        try:
          async with self._lock:
            if self._closed:
                return InterceptionResult(InterceptionDecision.STALE, reason="bridge_closed")
            if self._terminal_identity == (generation, ingress_token):
                return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason=self._terminal_reason)
            if (not self._has_identity or self._generation != generation
                    or self._ingress_token != ingress_token
                    or getattr(self._runtime, "is_closed", False) is True):
                if not await self._replace_runtime(generation, ingress_token):
                    return self._unavailable(generation, ingress_token, "interception_runtime_retirement_pending")
            runtime = self._runtime
            if runtime is None:
                return self._unavailable(generation, ingress_token, "interception_runtime_unavailable")
            if self._finished:
                return InterceptionResult(InterceptionDecision.STALE, reason="capture_finished")
          process_task = asyncio.create_task(runtime.process(
              pcm16, sample_rate_hz=sample_rate_hz, generation=generation,
              ingress_token=ingress_token, captured_at=captured_at,
          ), name="active-session-interception-process")
          try:
            result = await asyncio.wait_for(asyncio.shield(process_task), timeout=self._process_timeout_s)
          except asyncio.TimeoutError:
            failure = (self._unavailable(generation, ingress_token, "interception_process_timeout")
                       if self._runtime is runtime else InterceptionResult(InterceptionDecision.UNAVAILABLE, reason="interception_process_timeout"))
            await self._abort_process(process_task, runtime, "interception_process_timeout")
            return failure
          except asyncio.CancelledError:
            await self._abort_process(process_task, runtime, "interception_process_cancelled")
            raise
          except Exception as exc:
            failure = (self._unavailable(generation, ingress_token, f"interception_runtime_failed:{type(exc).__name__}")
                       if self._runtime is runtime else InterceptionResult(InterceptionDecision.UNAVAILABLE, reason=f"interception_runtime_failed:{type(exc).__name__}"))
            await self._retire_and_wait("interception_runtime_failed", expected_runtime=runtime)
            return failure
          if type(result) is not InterceptionResult:
              failure = (self._unavailable(generation, ingress_token, "invalid_interception_result")
                         if self._runtime is runtime else InterceptionResult(InterceptionDecision.UNAVAILABLE, reason="invalid_interception_result"))
              await self._retire_and_wait("invalid_interception_result", expected_runtime=runtime)
              return failure
          if self._closed or self._runtime is not runtime or self._generation != generation or self._ingress_token != ingress_token:
              return InterceptionResult(InterceptionDecision.STALE, reason="stale_generation")
          if result.decision is InterceptionDecision.UNAVAILABLE and self._terminal_on_unavailable:
              failure = self._unavailable(generation, ingress_token, result.reason or "interception_runtime_unavailable")
              await self._retire_and_wait(failure.reason, expected_runtime=runtime)
              return failure
          return result
        except asyncio.CancelledError:
            # Retire only this call's captured runtime. A call cancelled before
            # acquiring one must not revoke a concurrent successor's owner.
            await self._retire_and_wait("interception_process_cancelled", expected_runtime=runtime)
            raise
        finally:
            self._inflight -= 1

    def _unavailable(self, generation: object, ingress_token: object | None, reason: str) -> InterceptionResult:
        """The ASR owner can require a new capture after an explicit failure."""
        if self._terminal_on_unavailable:
            self._terminal_identity = (generation, ingress_token)
            self._terminal_reason = reason
        return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason=reason)

    async def finish(
        self, *, generation: object, ingress_token: object | None,
    ) -> InterceptionResult:
        """Drain one still-authorized stream once; never create a new owner."""
        if self._closed or self._generation != generation or self._ingress_token != ingress_token:
            return InterceptionResult(InterceptionDecision.STALE, reason="stale_generation")
        if self._finished:
            return InterceptionResult(InterceptionDecision.DROP, reason="capture_finished")
        if self._inflight:
            return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason="interception_capacity")
        self._inflight += 1
        runtime = self._runtime
        self._finished = True
        try:
            if runtime is None:
                return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason="interception_runtime_unavailable")
            task = asyncio.create_task(runtime.finish(), name="active-session-interception-finish")
            try:
                result = await asyncio.wait_for(asyncio.shield(task), self._process_timeout_s)
            except asyncio.CancelledError:
                await self._abort_process(task, runtime, "interception_finish_cancelled")
                raise
            except asyncio.TimeoutError:
                await self._abort_process(task, runtime, "interception_finish_timeout")
                return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason="interception_finish_timeout")
            except Exception as exc:
                await self._retire_and_wait("interception_finish_failed", expected_runtime=runtime)
                return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason=f"interception_finish_failed:{type(exc).__name__}")
            if (self._closed or self._runtime is not runtime
                    or self._generation != generation or self._ingress_token != ingress_token):
                return InterceptionResult(InterceptionDecision.STALE, reason="stale_generation")
            if type(result) is not InterceptionResult:
                await self._retire_and_wait("invalid_interception_result", expected_runtime=runtime)
                return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason="invalid_interception_result")
            return result
        finally:
            self._inflight -= 1

    def delivery_callback(
        self, result: InterceptionResult, *, generation: object, ingress_token: object | None,
    ) -> Callable[[InterceptionDeliveryReceipt], None]:
        """Bind settlement to its original owner, including after retirement.

        The callback retains only this bounded batch. It cannot settle another
        generation or arbitrary intervals. Consumers release it with their
        pending commands; a successor bridge is never consulted.
        """
        runtime = self._runtime
        if (runtime is None or self._generation != generation
                or self._ingress_token != ingress_token):
            raise RuntimeError("stale_interception_delivery_owner")
        events = {event.sequence: event for event in result.events}
        seen: dict[int, set[InterceptionDeliveryStage]] = {}

        def record(receipt: InterceptionDeliveryReceipt) -> None:
            if (type(receipt) is not InterceptionDeliveryReceipt
                    or events.get(receipt.event.sequence) != receipt.event):
                raise ValueError("interception receipt does not belong to this batch")
            stages = seen.setdefault(receipt.event.sequence, set())
            if receipt.stage in stages:
                return
            # Only an actual provider acknowledgement can resolve UNKNOWN.
            # Local late success must not resurrect a cancelled output.
            if (receipt.stage is InterceptionDeliveryStage.NOT_SENT
                    and (InterceptionDeliveryStage.TRANSPORT_WRITTEN in stages
                         or InterceptionDeliveryStage.PROVIDER_CONFIRMED in stages)):
                raise ValueError("cannot roll back written interception output")
            if receipt.event.kind is not InterceptionOutputKind.AUDIO:
                # Control events have no PCM delivery debt. This exact batch
                # owns their local application; never send them to the model's
                # audio ledger or manufacture a provider acknowledgement.
                if receipt.stage not in {
                    InterceptionDeliveryStage.LOCAL_ACCEPTED, InterceptionDeliveryStage.QUEUED,
                    InterceptionDeliveryStage.NOT_SENT, InterceptionDeliveryStage.UNKNOWN,
                }:
                    raise ValueError("control boundaries cannot acquire audio write receipts")
            else:
                if (InterceptionDeliveryStage.UNKNOWN in stages and receipt.stage in {
                    InterceptionDeliveryStage.TRANSPORT_WRITTEN, InterceptionDeliveryStage.TRANSPORT_OWNED,
                }):
                    raise ValueError("uncertain interception output cannot acquire late local ownership")
                if (InterceptionDeliveryStage.NOT_SENT in stages and receipt.stage in {
                    InterceptionDeliveryStage.TRANSPORT_WRITTEN, InterceptionDeliveryStage.TRANSPORT_OWNED,
                    InterceptionDeliveryStage.PROVIDER_CONFIRMED,
                }):
                    raise ValueError("unsent interception output cannot become written")
                if runtime.record_delivery(receipt) is False:
                    raise RuntimeError("interception delivery owner rejected its receipt")
            stages.add(receipt.stage)

        return record

    async def retire(self, reason: str = "retired") -> None:
        async with self._lock:
            await self._retire_runtime(reason)
            if not await self._wait_retirement():
                raise RuntimeError("interception_runtime_retirement_timeout")

    async def close(self, reason: str = "closed") -> None:
        async with self._lock:
            self._closed = True
            await self._retire_runtime(reason)
            if not await self._wait_retirement():
                raise RuntimeError("interception_runtime_retirement_timeout")
            # The application owns the factory; this bridge retires only its
            # session runtime. Prewire's single physical slot permits serial
            # handover between managers, with unavailable output until the
            # previous runtime has physically retired.

    async def _replace_runtime(self, generation: object, ingress_token: object | None) -> bool:
        await self._retire_runtime("generation_replaced")
        if not await self._wait_retirement():
            return False
        if self._closed:
            return False
        try:
            if getattr(self._factory, "is_available", True) is not True:
                raise RuntimeError("interception_factory_unavailable")
            runtime = self._factory.create(generation, ingress_token=ingress_token)
        except Exception:
            runtime = None
        if runtime is None:
            self._retiring = True
            self._generation = generation
            self._ingress_token = ingress_token
            return False
        self._runtime = runtime
        self._generation = generation
        self._ingress_token = ingress_token
        self._retiring = False
        self._has_identity = True
        self._finished = False
        return True

    async def _retire_runtime(self, reason: str) -> None:
        runtime = self._runtime
        self._runtime = None
        self._generation = None
        self._ingress_token = None
        self._has_identity = False
        if runtime is None:
            return
        self._retiring = True
        self._retiring_runtime = runtime
        self._retirement_task = asyncio.create_task(
            self._close_runtime(runtime, reason),
            name="active-session-interception-retire",
        )

    async def _wait_retirement(self) -> bool:
        runtime = self._retiring_runtime
        if runtime is None:
            return True
        task = self._retirement_task
        if task is None or (task.done() and (
            task.cancelled() or task.exception() is not None or not task.result()
        )):
            task = asyncio.create_task(
                self._close_runtime(runtime, "retirement_retry"),
                name="active-session-interception-retire",
            )
            self._retirement_task = task
        done, _ = await asyncio.wait({task}, timeout=self._close_timeout_s)
        complete = bool(done) and not task.cancelled() and task.exception() is None and task.result()
        if complete and self._retiring_runtime is runtime and self._retirement_task is task:
            self._retirement_task = None
            self._retiring_runtime = None
            self._retiring = False
        return bool(complete)

    async def _retire_and_wait(self, reason: str, *, expected_runtime: ActiveSessionInterceptionRuntime | None) -> bool:
        async with self._lock:
            if self._runtime is not expected_runtime:
                return False
            await self._retire_runtime(reason)
        return await self._wait_retirement()

    async def _abort_process(self, process_task: asyncio.Task[Any], runtime: ActiveSessionInterceptionRuntime, reason: str) -> None:
        async with self._lock:
            if self._runtime is runtime:
                await self._retire_runtime(reason)
        process_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(process_task), timeout=self._close_timeout_s)
        except asyncio.CancelledError:
            pass
        except (asyncio.TimeoutError, Exception):
            pass
        await self._wait_retirement()

    async def _close_runtime(
        self, runtime: ActiveSessionInterceptionRuntime, reason: str
    ) -> bool:
        try:
            result = await runtime.close(reason)
            if result is False or getattr(runtime, "retirement_confirmed", True) is False:
                return False
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            return False


__all__ = [
    "ActiveSessionInterceptionBridge",
    "ActiveSessionInterceptionFactory",
    "ActiveSessionInterceptionRuntime",
    "InterceptionDecision",
    "InterceptionResult",
    "InterceptionDeliveryReceipt", "InterceptionDeliveryStage",
    "InterceptionOutputEvent", "InterceptionOutputIdentity", "InterceptionOutputKind",
]
