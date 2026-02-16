import asyncio
import logging
import threading
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

try:
    import zmq
    import zmq.asyncio
except Exception:  # pragma: no cover - optional dependency at runtime
    zmq = None

logger = logging.getLogger(__name__)

SESSION_PUB_ADDR = "tcp://127.0.0.1:48961"  # main -> agent
AGENT_PUSH_ADDR = "tcp://127.0.0.1:48962"   # agent -> main
ANALYZE_PUSH_ADDR = "tcp://127.0.0.1:48963"  # main -> agent (reliable analyze queue)

_main_bridge_ref: Optional["MainServerAgentBridge"] = None
_ack_waiters: dict[str, asyncio.Future] = {}
_ack_waiters_lock = threading.Lock()


class MainServerAgentBridge:
    def __init__(self, on_agent_event: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        self.on_agent_event = on_agent_event
        self.ctx = None
        self.pub = None
        self.analyze_push = None
        self.pull = None
        self._task: Optional[asyncio.Task] = None
        self.owner_loop: Optional[asyncio.AbstractEventLoop] = None
        self.owner_thread_id: Optional[int] = None
        self.ready = False

    async def start(self) -> None:
        if zmq is None:
            logger.warning("pyzmq not installed, event bus disabled on main_server")
            return
        self.ctx = zmq.asyncio.Context.instance()
        self.pub = self.ctx.socket(zmq.PUB)
        self.pub.bind(SESSION_PUB_ADDR)
        self.analyze_push = self.ctx.socket(zmq.PUSH)
        self.analyze_push.bind(ANALYZE_PUSH_ADDR)
        self.pull = self.ctx.socket(zmq.PULL)
        self.pull.bind(AGENT_PUSH_ADDR)
        self.owner_loop = asyncio.get_running_loop()
        self.owner_thread_id = threading.get_ident()
        self.ready = True
        self._task = asyncio.create_task(self._recv_loop())
        logger.info("[EventBus] Main bridge started")

    async def _recv_loop(self) -> None:
        while True:
            try:
                msg = await self.pull.recv_json()
                if isinstance(msg, dict):
                    await self.on_agent_event(msg)
            except Exception as e:
                logger.debug(f"[EventBus] main recv loop error: {e}")
                await asyncio.sleep(0.05)

    async def publish_session_event(self, event: Dict[str, Any]) -> bool:
        if not self.ready or self.pub is None:
            return False
        try:
            await self.pub.send_json(event)
            return True
        except Exception as e:
            logger.debug(f"[EventBus] publish_session_event failed: {e}")
            return False

    async def publish_analyze_request(self, event: Dict[str, Any]) -> bool:
        if not self.ready or self.analyze_push is None:
            return False
        try:
            await self.analyze_push.send_json(event)
            return True
        except Exception as e:
            logger.debug(f"[EventBus] publish_analyze_request failed: {e}")
            return False

    async def publish_session_event_threadsafe(self, event: Dict[str, Any]) -> bool:
        """
        Publish via bridge owner loop to avoid cross-thread socket usage.
        """
        if self.owner_loop is None:
            return False
        # Fast path: already running in bridge owner thread.
        if threading.get_ident() == self.owner_thread_id:
            return await self.publish_session_event(event)
        try:
            cf = asyncio.run_coroutine_threadsafe(self.publish_session_event(event), self.owner_loop)
            return await asyncio.wrap_future(cf)
        except Exception as e:
            logger.debug(f"[EventBus] publish_session_event_threadsafe failed: {e}")
            return False


class AgentServerEventBridge:
    def __init__(self, on_session_event: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        self.on_session_event = on_session_event
        self.ctx = None
        self.sub = None
        self.analyze_pull = None
        self.push = None
        self._task: Optional[asyncio.Task] = None
        self._analyze_task: Optional[asyncio.Task] = None
        self.ready = False

    async def start(self) -> None:
        if zmq is None:
            logger.warning("pyzmq not installed, event bus disabled on agent_server")
            return
        self.ctx = zmq.asyncio.Context.instance()
        self.sub = self.ctx.socket(zmq.SUB)
        self.sub.connect(SESSION_PUB_ADDR)
        self.sub.setsockopt_string(zmq.SUBSCRIBE, "")
        self.analyze_pull = self.ctx.socket(zmq.PULL)
        self.analyze_pull.connect(ANALYZE_PUSH_ADDR)
        self.push = self.ctx.socket(zmq.PUSH)
        self.push.connect(AGENT_PUSH_ADDR)
        self.ready = True
        self._task = asyncio.create_task(self._recv_loop())
        self._analyze_task = asyncio.create_task(self._recv_analyze_loop())
        logger.info("[EventBus] Agent bridge started")

    async def _recv_loop(self) -> None:
        while True:
            try:
                msg = await self.sub.recv_json()
                if isinstance(msg, dict):
                    await self.on_session_event(msg)
            except Exception as e:
                logger.debug(f"[EventBus] agent recv loop error: {e}")
                await asyncio.sleep(0.05)

    async def _recv_analyze_loop(self) -> None:
        while True:
            try:
                if self.analyze_pull is None:
                    await asyncio.sleep(0.05)
                    continue
                msg = await self.analyze_pull.recv_json()
                if isinstance(msg, dict):
                    if msg.get("event_type") == "analyze_request":
                        logger.info(
                            "[EventBus] analyze_request dequeued on agent: event_id=%s lanlan=%s trigger=%s",
                            msg.get("event_id"),
                            msg.get("lanlan_name"),
                            msg.get("trigger"),
                        )
                    await self.on_session_event(msg)
            except Exception as e:
                logger.debug(f"[EventBus] agent analyze recv loop error: {e}")
                await asyncio.sleep(0.05)

    async def emit_to_main(self, event: Dict[str, Any]) -> bool:
        if not self.ready or self.push is None:
            return False
        try:
            await self.push.send_json(event)
            return True
        except Exception as e:
            logger.debug(f"[EventBus] emit_to_main failed: {e}")
            return False


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
    # Backward-compatible fallback for tests/mocks.
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
    def _resolve_waiter() -> None:
        if not waiter.done():
            waiter.set_result(True)
    loop.call_soon_threadsafe(_resolve_waiter)


async def publish_analyze_request_reliably(
    lanlan_name: str,
    trigger: str,
    messages: list[dict],
    *,
    ack_timeout_s: float = 0.5,
    retries: int = 1,
) -> bool:
    """
    Reliable analyze_request publish with event_id + ack + short retry.
    """
    event_id = uuid.uuid4().hex
    sent_at = time.perf_counter()
    for _ in range(max(retries, 0) + 1):
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
                cf = asyncio.run_coroutine_threadsafe(bridge.publish_analyze_request(event), bridge.owner_loop)
                sent = await asyncio.wrap_future(cf)
            except Exception as e:
                logger.debug(f"[EventBus] publish_analyze_request threadsafe failed: {e}")
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
                "[EventBus] analyze_request ack timeout: event_id=%s lanlan=%s trigger=%s",
                event_id,
                lanlan_name,
                trigger,
            )
    # Compatibility fallback: if agent side is still on old PUB/SUB consumer path,
    # retry once via session broadcast with the same event_id.
    fallback_waiter: asyncio.Future = asyncio.get_running_loop().create_future()
    with _ack_waiters_lock:
        _ack_waiters[event_id] = fallback_waiter
    sent_fallback = await publish_session_event_threadsafe(event)
    if sent_fallback:
        logger.info(
            "[EventBus] analyze_request fallback via pubsub: event_id=%s lanlan=%s trigger=%s",
            event_id,
            lanlan_name,
            trigger,
        )
        try:
            await asyncio.wait_for(fallback_waiter, timeout=ack_timeout_s)
            logger.info(
                "[EventBus] analyze_request acked via pubsub fallback: event_id=%s lanlan=%s trigger=%s latency_ms=%.1f",
                event_id,
                lanlan_name,
                trigger,
                (time.perf_counter() - sent_at) * 1000.0,
            )
            return True
        except asyncio.TimeoutError:
            pass
    with _ack_waiters_lock:
        _ack_waiters.pop(event_id, None)
    return False
