import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

try:
    import zmq
    import zmq.asyncio
except Exception:  # pragma: no cover - optional dependency at runtime
    zmq = None

logger = logging.getLogger(__name__)

SESSION_PUB_ADDR = "tcp://127.0.0.1:48961"  # main -> agent
AGENT_PUSH_ADDR = "tcp://127.0.0.1:48962"   # agent -> main

_main_bridge_ref: Optional["MainServerAgentBridge"] = None


class MainServerAgentBridge:
    def __init__(self, on_agent_event: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        self.on_agent_event = on_agent_event
        self.ctx = None
        self.pub = None
        self.pull = None
        self._task: Optional[asyncio.Task] = None
        self.ready = False

    async def start(self) -> None:
        if zmq is None:
            logger.warning("pyzmq not installed, event bus disabled on main_server")
            return
        self.ctx = zmq.asyncio.Context.instance()
        self.pub = self.ctx.socket(zmq.PUB)
        self.pub.bind(SESSION_PUB_ADDR)
        self.pull = self.ctx.socket(zmq.PULL)
        self.pull.bind(AGENT_PUSH_ADDR)
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


class AgentServerEventBridge:
    def __init__(self, on_session_event: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        self.on_session_event = on_session_event
        self.ctx = None
        self.sub = None
        self.push = None
        self._task: Optional[asyncio.Task] = None
        self.ready = False

    async def start(self) -> None:
        if zmq is None:
            logger.warning("pyzmq not installed, event bus disabled on agent_server")
            return
        self.ctx = zmq.asyncio.Context.instance()
        self.sub = self.ctx.socket(zmq.SUB)
        self.sub.connect(SESSION_PUB_ADDR)
        self.sub.setsockopt_string(zmq.SUBSCRIBE, "")
        self.push = self.ctx.socket(zmq.PUSH)
        self.push.connect(AGENT_PUSH_ADDR)
        self.ready = True
        self._task = asyncio.create_task(self._recv_loop())
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
