"""Telemetry transport: WebSocket first, REST as a fallback.

The service pushes on `/ws` and also answers `/all`. We prefer the socket and
fall back to polling, but the two can briefly overlap -- a REST response already
in flight when the socket comes back would otherwise deliver an older frame
after a newer one. Two mechanisms prevent that:

* a **transport epoch** that increments on every mode change, so frames from a
  superseded generation are discarded; and
* the **`(instanceId, seq)` cursor**, which must strictly advance.

The loop lives on a dedicated thread with its own event loop. SDK timers are not
usable here: each tick runs its own `asyncio.run`, which cannot hold a socket
open between ticks.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import aiohttp

MODE_STARTING = "starting"
MODE_WS = "ws"
MODE_REST = "rest"
MODE_STOPPED = "stopped"

DROP_STALE_EPOCH = "stale_epoch"
DROP_DUPLICATE_SEQ = "duplicate_seq"
DROP_MALFORMED = "malformed"

# A run of failed polls is how a service that died mid-battle looks from here.
# One failure is just a hiccup, so wait for a few, then ask at a slow cadence:
# the owner's recovery attempt costs a process launch.
STALL_FAILURES = 3
STALL_INTERVAL_SECONDS = 5.0


@dataclass(frozen=True)
class RawFrame:
    payload: dict[str, Any]
    transport: str
    epoch: int
    received_at: float


@dataclass
class TransportStats:
    mode: str = MODE_STOPPED
    epoch: int = 0
    ws_connects: int = 0
    ws_failures: int = 0
    rest_polls: int = 0
    rest_failures: int = 0
    frames_emitted: int = 0
    # Wall clock, unlike `RawFrame.received_at`: this one is only ever rendered
    # as a time of day, and a monotonic reading means nothing to the user.
    last_frame_at: float | None = None
    last_error: str = ""
    reconnect_delay: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "epoch": self.epoch,
            "ws_connects": self.ws_connects,
            "ws_failures": self.ws_failures,
            "rest_polls": self.rest_polls,
            "rest_failures": self.rest_failures,
            "frames_emitted": self.frames_emitted,
            "last_frame_at": self.last_frame_at,
            "last_error": self.last_error,
            "reconnect_delay": round(self.reconnect_delay, 2),
        }


class CursorGate:
    """Accepts frames only in strict `(epoch, cursor)` order.

    Pure and side-effect free apart from its own high-water marks, so ordering
    behaviour can be tested without any sockets.
    """

    def __init__(self) -> None:
        self._epoch = 0
        self._instance_id: str | None = None
        self._seq = -1
        self.accepted = 0
        self.dropped: dict[str, int] = {}

    @property
    def cursor(self) -> tuple[str | None, int]:
        return (self._instance_id, self._seq)

    def reset(self) -> None:
        self._instance_id = None
        self._seq = -1

    def accept(self, snapshot, epoch: int) -> tuple[bool, str]:
        """Returns `(accepted, reason)`; `reason` is only set on a drop."""
        if epoch < self._epoch:
            return self._drop(DROP_STALE_EPOCH)
        instance_id = getattr(snapshot, "instance_id", "") or ""
        seq = getattr(snapshot, "seq", None)
        if not isinstance(seq, int):
            return self._drop(DROP_MALFORMED)

        if instance_id != self._instance_id:
            # A restarted service resets `seq`, so the cursor cannot be compared
            # across instances; adopt the new one instead of rejecting it.
            self._epoch = epoch
            self._instance_id = instance_id
            self._seq = seq
            self.accepted += 1
            return True, ""

        # A new epoch is only a change of transport. The same service keeps
        # counting, so a lower `seq` here is a frame that overtook us, not a
        # fresh generation to adopt.
        self._epoch = epoch
        if seq <= self._seq:
            return self._drop(DROP_DUPLICATE_SEQ)
        self._seq = seq
        self.accepted += 1
        return True, ""

    def _drop(self, reason: str) -> tuple[bool, str]:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1
        return False, reason

    def as_dict(self) -> dict[str, Any]:
        return {
            "epoch": self._epoch,
            "instance_id": self._instance_id,
            "seq": self._seq,
            "accepted": self.accepted,
            "dropped": dict(self.dropped),
        }


class TelemetryTransport:
    """Feeds raw service payloads to `on_frame` until stopped."""

    def __init__(
        self,
        cfg,
        on_frame: Callable[[RawFrame], None],
        *,
        logger=None,
        on_stall: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self._on_frame = on_frame
        self.logger = logger
        self._on_stall = on_stall
        self._clock = clock
        self._lock = threading.RLock()
        self._stats = TransportStats()
        self._epoch = 0
        self._mode = MODE_STOPPED
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_flag = threading.Event()
        self._rest_failures_in_a_row = 0
        self._last_stall_at = 0.0

    # ------------------------------------------------------------------
    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def stats(self) -> dict[str, Any]:
        with self._lock:
            self._stats.mode = self._mode
            self._stats.epoch = self._epoch
            return self._stats.as_dict()

    def apply_config(self, cfg) -> None:
        with self._lock:
            self.cfg = cfg

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop_flag = threading.Event()
            self._mode = MODE_STARTING
            self._thread = threading.Thread(
                target=self._thread_main, name="wows-transport", daemon=True)
            thread = self._thread
        thread.start()
        return True

    def stop(self, timeout: float = 3.0) -> None:
        with self._lock:
            thread = self._thread
            loop = self._loop
            self._thread = None
        self._stop_flag.set()
        if loop is not None:
            # The loop is blocked on an asyncio primitive; poke it from here.
            loop.call_soon_threadsafe(lambda: None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            self._mode = MODE_STOPPED

    # ------------------------------------------------------------------
    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:  # pragma: no cover - transport thread guard
            self._note_error(f"transport thread crashed: {exc}")
        finally:
            with self._lock:
                self._mode = MODE_STOPPED
                self._loop = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            self._loop = loop
        timeout = aiohttp.ClientTimeout(total=self.cfg.http_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            tasks = [asyncio.create_task(self._rest_loop(session))]
            if self.cfg.transport_prefer_ws:
                tasks.append(asyncio.create_task(self._ws_loop(session)))
            else:
                self._switch_mode(MODE_REST)
            try:
                await self._wait_for_stop()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _wait_for_stop(self) -> None:
        while not self._stop_flag.is_set():
            await asyncio.sleep(0.05)

    # ------------------------------------------------------------------
    async def _ws_loop(self, session: aiohttp.ClientSession) -> None:
        """Keep trying to hold a socket open; REST covers the gaps."""
        delay = self.cfg.ws_reconnect_min_seconds
        url = self.cfg.service_url.rstrip("/") + "/ws"
        while not self._stop_flag.is_set():
            try:
                async with session.ws_connect(url, heartbeat=20.0) as ws:
                    with self._lock:
                        self._stats.ws_connects += 1
                    self._switch_mode(MODE_WS)
                    delay = self.cfg.ws_reconnect_min_seconds
                    await self._pump_ws(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with self._lock:
                    self._stats.ws_failures += 1
                self._note_error(f"ws: {type(exc).__name__}")

            if self._stop_flag.is_set():
                return
            # Socket is gone: polling takes over while we back off and retry.
            self._switch_mode(MODE_REST)
            with self._lock:
                self._stats.reconnect_delay = delay
            await asyncio.sleep(delay)
            delay = min(delay * 2.0, self.cfg.ws_reconnect_max_seconds)

    async def _pump_ws(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        epoch = self.epoch
        async for message in ws:
            if self._stop_flag.is_set():
                return
            if message.type is aiohttp.WSMsgType.TEXT:
                payload = _decode(message.data)
                if payload is None:
                    # An unparseable frame means the socket is not giving us
                    # usable data; drop back to polling rather than hanging on.
                    self._note_error("ws: invalid message")
                    return
                self._emit(payload, MODE_WS, epoch)
            elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                return

    async def _rest_loop(self, session: aiohttp.ClientSession) -> None:
        url = self.cfg.service_url.rstrip("/") + "/all"
        while not self._stop_flag.is_set():
            await asyncio.sleep(self.cfg.rest_poll_interval_seconds)
            if self._stop_flag.is_set():
                return
            if self.mode == MODE_WS:
                continue
            epoch = self.epoch
            try:
                async with session.get(url) as response:
                    if response.status != 200:
                        raise aiohttp.ClientResponseError(
                            response.request_info, response.history,
                            status=response.status, message="bad status")
                    payload = await response.json(content_type=None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with self._lock:
                    self._stats.rest_failures += 1
                self._note_error(f"rest: {type(exc).__name__}")
                await self._maybe_supervise()
                continue
            with self._lock:
                self._stats.rest_polls += 1
                self._rest_failures_in_a_row = 0
            if isinstance(payload, dict):
                self._emit(payload, MODE_REST, epoch)

    def _stall_is_due(self) -> bool:
        """Count one failed poll and say whether the owner should be asked."""
        with self._lock:
            self._rest_failures_in_a_row += 1
            if self._rest_failures_in_a_row < STALL_FAILURES:
                return False
            now = self._clock()
            if now - self._last_stall_at < STALL_INTERVAL_SECONDS:
                return False
            self._last_stall_at = now
            return True

    async def _maybe_supervise(self) -> None:
        """Hand the stall to the owner without stalling the loops.

        Recovery can block on a health probe and a process launch, and the
        socket loop has to keep retrying while that happens.
        """
        if not self._stall_is_due() or self._on_stall is None:
            return
        try:
            await asyncio.to_thread(self._on_stall)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._note_error(f"supervise: {type(exc).__name__}")
            self._log("exception", f"service supervision raised: {exc}")

    # ------------------------------------------------------------------
    def _switch_mode(self, mode: str) -> None:
        with self._lock:
            if self._mode == mode:
                return
            self._mode = mode
            # Every mode change starts a new generation, so anything still in
            # flight from the previous one is recognisably obsolete.
            self._epoch += 1
        self._log("info", f"transport mode -> {mode} (epoch {self.epoch})")

    def _emit(self, payload: dict[str, Any], transport: str, epoch: int) -> None:
        if epoch < self.epoch:
            return
        frame = RawFrame(
            payload=payload,
            transport=transport,
            epoch=epoch,
            received_at=time.monotonic(),
        )
        with self._lock:
            self._stats.frames_emitted += 1
            self._stats.last_frame_at = time.time()
        try:
            self._on_frame(frame)
        except Exception as exc:  # pragma: no cover - never kill the transport
            self._note_error(f"frame handler: {type(exc).__name__}")
            self._log("exception", f"frame handler raised: {exc}")

    def _note_error(self, message: str) -> None:
        with self._lock:
            self._stats.last_error = message

    def _log(self, level: str, message: str) -> None:
        if self.logger is None:
            return
        method = getattr(self.logger, level, None)
        if callable(method):
            try:
                method(message)
            except Exception:
                pass


def _decode(data: Any) -> dict[str, Any] | None:
    import json

    if not isinstance(data, (str, bytes, bytearray)):
        return None
    try:
        payload = json.loads(data)
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


__all__ = [
    "DROP_DUPLICATE_SEQ",
    "DROP_MALFORMED",
    "DROP_STALE_EPOCH",
    "MODE_REST",
    "MODE_STARTING",
    "MODE_STOPPED",
    "MODE_WS",
    "CursorGate",
    "RawFrame",
    "TelemetryTransport",
    "TransportStats",
]
