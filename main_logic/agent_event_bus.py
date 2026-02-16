"""
ZeroMQ event bus for main_server <-> agent_server communication.

IMPORTANT: We use **synchronous** zmq.Context + zmq.Socket with background
daemon threads for recv, because zmq.asyncio.Socket.recv relies on
event-loop fd polling (add_reader) which does NOT work on Windows
ProactorEventLoop.  Sends use zmq.NOBLOCK and are called from the
asyncio thread (fast, microsecond-level for local TCP).
"""

import asyncio
import logging
import os
import threading
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

try:
    import zmq
except Exception:  # pragma: no cover - optional dependency at runtime
    zmq = None

logger = logging.getLogger(__name__)

SESSION_PUB_ADDR = "tcp://127.0.0.1:48961"   # main -> agent  (PUB/SUB)
AGENT_PUSH_ADDR = "tcp://127.0.0.1:48962"    # agent -> main  (PUSH/PULL)
ANALYZE_PUSH_ADDR = "tcp://127.0.0.1:48963"  # main -> agent  (PUSH/PULL, reliable analyze queue)

_main_bridge_ref: Optional["MainServerAgentBridge"] = None
_ack_waiters: dict[str, asyncio.Future] = {}
_ack_waiters_lock = threading.Lock()


# ---------------------------------------------------------------------------
#  Main-server side bridge
# ---------------------------------------------------------------------------

class MainServerAgentBridge:
    """Runs inside main_server process. Binds PUB, PUSH(analyze), PULL(agent→main)."""

    def __init__(self, on_agent_event: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        self.on_agent_event = on_agent_event
        self.ctx: Any = None
        self.pub: Any = None
        self.analyze_push: Any = None
        self.pull: Any = None
        self._recv_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.owner_loop: Optional[asyncio.AbstractEventLoop] = None
        self.owner_thread_id: Optional[int] = None
        self.ready = False

    async def start(self) -> None:
        if zmq is None:
            logger.warning("pyzmq not installed, event bus disabled on main_server")
            return

        self.ctx = zmq.Context()

        self.pub = self.ctx.socket(zmq.PUB)
        self.pub.setsockopt(zmq.LINGER, 1000)
        self.pub.bind(SESSION_PUB_ADDR)

        self.analyze_push = self.ctx.socket(zmq.PUSH)
        self.analyze_push.setsockopt(zmq.LINGER, 1000)
        self.analyze_push.bind(ANALYZE_PUSH_ADDR)

        self.pull = self.ctx.socket(zmq.PULL)
        self.pull.setsockopt(zmq.LINGER, 1000)
        self.pull.setsockopt(zmq.RCVTIMEO, 1000)
        self.pull.bind(AGENT_PUSH_ADDR)

        self.owner_loop = asyncio.get_running_loop()
        self.owner_thread_id = threading.get_ident()
        self.ready = True

        self._recv_thread = threading.Thread(
            target=self._recv_thread_fn, name="zmq-main-recv", daemon=True,
        )
        self._recv_thread.start()
        logger.info("[EventBus] Main bridge started (pid=%s)", os.getpid())

    # -- background recv (agent → main) ------------------------------------

    def _recv_thread_fn(self) -> None:
        while not self._stop.is_set():
            try:
                msg = self.pull.recv_json()
                if isinstance(msg, dict) and self.owner_loop is not None:
                    asyncio.run_coroutine_threadsafe(
                        self.on_agent_event(msg), self.owner_loop,
                    )
            except zmq.Again:
                continue
            except Exception as e:
                if not self._stop.is_set():
                    logger.debug("[EventBus] main recv thread error: %s", e)
                    time.sleep(0.05)

    # -- send helpers (called from asyncio thread) --------------------------

    async def publish_session_event(self, event: Dict[str, Any]) -> bool:
        if not self.ready or self.pub is None:
            return False
        try:
            self.pub.send_json(event, zmq.NOBLOCK)
            return True
        except Exception:
            return False

    async def publish_analyze_request(self, event: Dict[str, Any]) -> bool:
        if not self.ready or self.analyze_push is None:
            return False
        try:
            self.analyze_push.send_json(event, zmq.NOBLOCK)
            return True
        except Exception:
            return False

    async def publish_session_event_threadsafe(self, event: Dict[str, Any]) -> bool:
        if self.owner_loop is None:
            return False
        if threading.get_ident() == self.owner_thread_id:
            return await self.publish_session_event(event)
        try:
            cf = asyncio.run_coroutine_threadsafe(
                self.publish_session_event(event), self.owner_loop,
            )
            return await asyncio.wrap_future(cf)
        except Exception:
            return False


# ---------------------------------------------------------------------------
#  Agent-server side bridge
# ---------------------------------------------------------------------------

class AgentServerEventBridge:
    """Runs inside agent_server process. Connects SUB, PULL(analyze), PUSH(agent→main)."""

    def __init__(self, on_session_event: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        self.on_session_event = on_session_event
        self.ctx: Any = None
        self.sub: Any = None
        self.analyze_pull: Any = None
        self.push: Any = None
        self._recv_thread: Optional[threading.Thread] = None
        self._analyze_recv_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._owner_loop: Optional[asyncio.AbstractEventLoop] = None
        self.ready = False

    async def start(self) -> None:
        if zmq is None:
            logger.warning("pyzmq not installed, event bus disabled on agent_server")
            return

        self.ctx = zmq.Context()

        self.sub = self.ctx.socket(zmq.SUB)
        self.sub.setsockopt(zmq.LINGER, 1000)
        self.sub.setsockopt(zmq.RCVTIMEO, 1000)
        self.sub.connect(SESSION_PUB_ADDR)
        self.sub.setsockopt_string(zmq.SUBSCRIBE, "")

        self.analyze_pull = self.ctx.socket(zmq.PULL)
        self.analyze_pull.setsockopt(zmq.LINGER, 1000)
        self.analyze_pull.setsockopt(zmq.RCVTIMEO, 1000)
        self.analyze_pull.connect(ANALYZE_PUSH_ADDR)

        self.push = self.ctx.socket(zmq.PUSH)
        self.push.setsockopt(zmq.LINGER, 1000)
        self.push.connect(AGENT_PUSH_ADDR)

        self._owner_loop = asyncio.get_running_loop()
        self.ready = True

        self._recv_thread = threading.Thread(
            target=self._recv_sub_fn, name="zmq-agent-sub", daemon=True,
        )
        self._recv_thread.start()

        self._analyze_recv_thread = threading.Thread(
            target=self._recv_analyze_fn, name="zmq-agent-analyze", daemon=True,
        )
        self._analyze_recv_thread.start()
        logger.info("[EventBus] Agent bridge started (pid=%s)", os.getpid())

    # -- background recv threads -------------------------------------------

    def _recv_sub_fn(self) -> None:
        while not self._stop.is_set():
            try:
                msg = self.sub.recv_json()
                if isinstance(msg, dict) and self._owner_loop is not None:
                    asyncio.run_coroutine_threadsafe(
                        self.on_session_event(msg), self._owner_loop,
                    )
            except zmq.Again:
                continue
            except Exception as e:
                if not self._stop.is_set():
                    logger.debug("[EventBus] agent sub recv thread error: %s", e)
                    time.sleep(0.05)

    def _recv_analyze_fn(self) -> None:
        while not self._stop.is_set():
            try:
                msg = self.analyze_pull.recv_json()
                if isinstance(msg, dict):
                    if msg.get("event_type") == "analyze_request":
                        logger.info(
                            "[EventBus] analyze_request dequeued on agent: event_id=%s lanlan=%s trigger=%s",
                            msg.get("event_id"),
                            msg.get("lanlan_name"),
                            msg.get("trigger"),
                        )
                    if self._owner_loop is not None:
                        asyncio.run_coroutine_threadsafe(
                            self.on_session_event(msg), self._owner_loop,
                        )
            except zmq.Again:
                continue
            except Exception as e:
                if not self._stop.is_set():
                    logger.debug("[EventBus] agent analyze recv thread error: %s", e)
                    time.sleep(0.05)

    # -- send helper (called from asyncio thread) --------------------------

    async def emit_to_main(self, event: Dict[str, Any]) -> bool:
        if not self.ready or self.push is None:
            return False
        try:
            self.push.send_json(event, zmq.NOBLOCK)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
#  Module-level helpers (unchanged API)
# ---------------------------------------------------------------------------

def set_main_bridge(bridge: Optional[MainServerAgentBridge]) -> None:
    global _main_bridge_ref
    _main_bridge_ref = bridge


async def publish_session_event(event: Dict[str, Any]) -> bool:
    if _main_bridge_ref is None:
        return False
    return await _main_bridge_ref.publish_session_event(event)


async def publish_session_event_threadsafe(event: Dict[str, Any]) -> bool:
    if _main_bridge_ref is None:
        return False
    bridge = _main_bridge_ref
    if hasattr(bridge, "publish_session_event_threadsafe"):
        return await bridge.publish_session_event_threadsafe(event)
    return await bridge.publish_session_event(event)


def notify_analyze_ack(event_id: str) -> None:
    if not event_id:
        return
    waiter = None
    with _ack_waiters_lock:
        waiter = _ack_waiters.pop(event_id, None)
    if waiter is None or waiter.done():
        return
    loop = waiter.get_loop()

    def _resolve() -> None:
        if not waiter.done():
            waiter.set_result(True)

    loop.call_soon_threadsafe(_resolve)


async def publish_analyze_request_reliably(
    lanlan_name: str,
    trigger: str,
    messages: list[dict],
    *,
    ack_timeout_s: float = 0.5,
    retries: int = 1,
) -> bool:
    """Reliable analyze_request publish with event_id + ack + short retry."""
    event_id = uuid.uuid4().hex
    sent_at = time.perf_counter()

    for attempt in range(max(retries, 0) + 1):
        event = {
            "event_type": "analyze_request",
            "event_id": event_id,
            "trigger": trigger,
            "lanlan_name": lanlan_name,
            "messages": messages,
        }

        loop = asyncio.get_running_loop()
        waiter: asyncio.Future = loop.create_future()
        with _ack_waiters_lock:
            _ack_waiters[event_id] = waiter

        bridge = _main_bridge_ref
        if bridge is None:
            with _ack_waiters_lock:
                _ack_waiters.pop(event_id, None)
            return False
        if bridge.owner_loop is None:
            with _ack_waiters_lock:
                _ack_waiters.pop(event_id, None)
            return False

        if threading.get_ident() == bridge.owner_thread_id:
            sent = await bridge.publish_analyze_request(event)
        else:
            try:
                cf = asyncio.run_coroutine_threadsafe(
                    bridge.publish_analyze_request(event), bridge.owner_loop,
                )
                sent = await asyncio.wrap_future(cf)
            except Exception as e:
                logger.debug("[EventBus] publish_analyze_request threadsafe failed: %s", e)
                sent = False

        if not sent:
            with _ack_waiters_lock:
                _ack_waiters.pop(event_id, None)
            continue

        try:
            await asyncio.wait_for(waiter, timeout=ack_timeout_s)
            logger.info(
                "[EventBus] analyze_request acked: event_id=%s lanlan=%s trigger=%s latency_ms=%.1f",
                event_id,
                lanlan_name,
                trigger,
                (time.perf_counter() - sent_at) * 1000.0,
            )
            return True
        except asyncio.TimeoutError:
            with _ack_waiters_lock:
                _ack_waiters.pop(event_id, None)
            logger.info(
                "[EventBus] analyze_request ack timeout (attempt %d): event_id=%s lanlan=%s trigger=%s",
                attempt + 1,
                event_id,
                lanlan_name,
                trigger,
            )

    return False
