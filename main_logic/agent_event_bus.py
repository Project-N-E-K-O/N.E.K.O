# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
ZeroMQ event bus for main_server <-> agent_server communication.

Important: this uses the **synchronous** zmq.Context + zmq.Socket, running
recv on a background daemon thread. The reason is that zmq.asyncio.Socket.recv
relies on the event loop's fd polling (add_reader), which is unavailable on
the Windows ProactorEventLoop. The send side uses zmq.NOBLOCK and is called
from the asyncio thread (local TCP latency is very low).
"""

import asyncio
import os
import threading
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

import orjson

from utils.logger_config import get_module_logger

try:
    import zmq
except Exception:  # pragma: no cover - optional dependency at runtime
    zmq = None

logger = get_module_logger(__name__, "Main")

# ZMQ 地址：支持环境变量覆盖，便于 launcher 在默认端口落入
# Hyper-V 保留区时进行迁移。
def _zmq_addr(env_key: str, default_port: int) -> str:
    raw = os.getenv(env_key, "").strip()
    if raw:
        try:
            val = int(raw)
            if 1 <= val <= 65535:
                return f"tcp://127.0.0.1:{val}"
        except (ValueError, TypeError):
            pass
    return f"tcp://127.0.0.1:{default_port}"

SESSION_PUB_ADDR  = _zmq_addr("NEKO_ZMQ_SESSION_PUB_PORT", 48961)   # main -> agent（PUB/SUB）
AGENT_PUSH_ADDR   = _zmq_addr("NEKO_ZMQ_AGENT_PUSH_PORT", 48962)    # agent -> main（PUSH/PULL）
ANALYZE_PUSH_ADDR = _zmq_addr("NEKO_ZMQ_ANALYZE_PUSH_PORT", 48963)  # main -> agent（PUSH/PULL，可靠分析队列）

_main_bridge_ref: Optional["MainServerAgentBridge"] = None
_ack_waiters: dict[str, asyncio.Future] = {}
_ack_waiters_lock = threading.Lock()


# ---------------------------------------------------------------------------
#  main_server 侧桥接器
# ---------------------------------------------------------------------------

class MainServerAgentBridge:
    """Runs inside the main_server process; binds PUB, PUSH(analyze), PULL(agent→main)."""

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

    # -- 后台接收（agent → main） -------------------------------------------

    def _recv_thread_fn(self) -> None:
        while not self._stop.is_set():
            try:
                msg = orjson.loads(self.pull.recv())
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

    # -- 发送辅助函数（在 asyncio 线程中调用） -------------------------------

    async def publish_session_event(self, event: Dict[str, Any]) -> bool:
        if not self.ready or self.pub is None:
            return False
        try:
            self.pub.send(orjson.dumps(event), zmq.NOBLOCK)
            return True
        except Exception:
            return False

    async def publish_analyze_request(self, event: Dict[str, Any]) -> bool:
        if not self.ready or self.analyze_push is None:
            return False
        try:
            self.analyze_push.send(orjson.dumps(event), zmq.NOBLOCK)
            return True
        except Exception:
            return False

    async def stop(self) -> None:
        """Shut down ZMQ resources and background thread."""
        self._stop.set()
        self.ready = False
        if self._recv_thread is not None:
            await asyncio.to_thread(self._recv_thread.join, 2.0)
        for sock in (self.pull, self.analyze_push, self.pub):
            if sock is not None:
                try:
                    sock.close(linger=0)
                except Exception:
                    pass
        if self.ctx is not None:
            _ctx = self.ctx
            self.ctx = None
            try:
                await asyncio.wait_for(asyncio.to_thread(_ctx.term), timeout=3.0)
            except (asyncio.TimeoutError, Exception):
                logger.debug("[EventBus] Main bridge ctx.term timed out or failed, skipping")
        logger.debug("[EventBus] Main bridge stopped")

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
#  agent_server 侧桥接器
# ---------------------------------------------------------------------------

class AgentServerEventBridge:
    """Runs inside the agent_server process; connects SUB, PULL(analyze), PUSH(agent→main)."""

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

    async def stop(self) -> None:
        """Shut down ZMQ resources and receiver threads."""
        self._stop.set()
        self.ready = False

        recv_threads = [
            thread
            for thread in (self._recv_thread, self._analyze_recv_thread)
            if thread is not None
        ]
        if recv_threads:
            await asyncio.gather(
                *(asyncio.to_thread(thread.join, 2.0) for thread in recv_threads),
                return_exceptions=True,
            )
        self._recv_thread = None
        self._analyze_recv_thread = None

        for sock_name in ("sub", "analyze_pull", "push"):
            sock = getattr(self, sock_name, None)
            if sock is None:
                continue
            try:
                sock.close(linger=0)
            except Exception as exc:
                logger.debug("[EventBus] Agent bridge socket %s close error: %s", sock_name, exc)
            setattr(self, sock_name, None)

        if self.ctx is not None:
            ctx = self.ctx
            self.ctx = None
            try:
                await asyncio.wait_for(asyncio.to_thread(ctx.term), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning("[EventBus] Agent bridge ctx.term timed out, skipping")
            except Exception as exc:
                logger.debug("[EventBus] Agent bridge ctx.term error: %s", exc)

        self._owner_loop = None
        logger.debug("[EventBus] Agent bridge stopped")

    # -- 后台接收线程 -------------------------------------------------------

    def _recv_sub_fn(self) -> None:
        while not self._stop.is_set():
            try:
                msg = orjson.loads(self.sub.recv())
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
                msg = orjson.loads(self.analyze_pull.recv())
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

    # -- 发送辅助函数（在 asyncio 线程中调用） -------------------------------

    async def emit_to_main(self, event: Dict[str, Any]) -> bool:
        if not self.ready or self.push is None:
            return False
        try:
            self.push.send(orjson.dumps(event), zmq.NOBLOCK)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
#  模块级辅助函数（API 保持不变）
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


def notify_voice_bridge_result(event_id: str, result: Dict[str, Any]) -> None:
    """Compatibility sink for old voice bridge replies.

    Voice transcript plugin dispatch is best-effort telemetry now: main never
    waits for, trusts, or applies plugin-produced actions to the current turn.
    Late replies from an older agent are intentionally ignored.
    """
    if event_id:
        logger.debug("[EventBus] ignored voice bridge result: event_id=%s", event_id)


# ---------------------------------------------------------------------------
#  Layering-inversion sinks: lower layers (main_logic) emit, higher layers
#  (plugin / main_routers) register.
#
#  Why this exists
#  ---------------
#  ``main_logic.core`` used to ``import`` from ``plugin.core.state`` and
#  ``main_routers.system_router`` to publish user utterances and to consult
#  the mini-game-invite keyword matcher. Both are layering inversions
#  (main_logic L2 → plugin L4 / main_routers L3) — banned by
#  ``scripts/check_module_layering.py``.
#
#  Now main_logic emits via ``dispatch_*`` and the higher layers attach via
#  ``register_*``. The CONSUMERS self-register at module-import time
#  (plugin/core/state.py and main_routers/system_router.py), so any context
#  that loads those modules — even directly, without going through the
#  ``app`` entrypoint — gets its sink wired automatically. This preserves
#  the side-effect that direct ``main_logic.core`` consumers (testbench /
#  ad-hoc scripts) used to enjoy via the previous chained import. The
#  registries dedupe on identity, so ``app/runtime_bindings.py`` calling
#  ``register_*`` again after the consumer module is loaded is a no-op.
#
#  If nothing is registered (e.g. memory_server entrypoint doesn't ship
#  plugin runtime), the dispatchers silently no-op.
# ---------------------------------------------------------------------------

# Fire-and-forget user-utterance sink. Plugin's user-context bus subscribes
# here. Multiple subscribers are allowed; per-sink errors are swallowed so
# one misbehaving consumer cannot break the chat pipeline.
_user_utterance_sinks: list[Callable[[str, Dict[str, Any]], None]] = []


def register_user_utterance_sink(
    fn: Callable[[str, Dict[str, Any]], None],
) -> None:
    """Subscribe to user-utterance events: ``fn(bucket: str, event: dict)``.

    Dedupes on identity — re-registering the same callable is a no-op.
    Important because both ``plugin.core.state`` (self-register on import)
    and ``app.runtime_bindings`` (explicit wiring) call this for the same
    function; without dedup, every utterance would fire twice.
    """
    if fn in _user_utterance_sinks:
        return
    _user_utterance_sinks.append(fn)


def dispatch_user_utterance(bucket: str, event: Dict[str, Any]) -> None:
    """Fan a user utterance out to every registered sink."""
    for fn in _user_utterance_sinks:
        try:
            fn(bucket, event)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("[EventBus] user_utterance sink raised: %s", exc)


# First-hit-wins text-message hook with optional return value.
# main_routers' mini-game-invite keyword matcher is the canonical consumer.
_text_user_message_hooks: list[
    Callable[[str, str], Optional[Dict[str, Any]]]
] = []


def register_text_user_message_hook(
    fn: Callable[[str, str], Optional[Dict[str, Any]]],
) -> None:
    """Subscribe to text user-message events: ``fn(lanlan_name, text) -> dict?``.

    First hook returning a truthy value wins; later hooks are skipped.
    Dedupes on identity (see ``register_user_utterance_sink`` rationale).
    """
    if fn in _text_user_message_hooks:
        return
    _text_user_message_hooks.append(fn)


def dispatch_text_user_message(
    lanlan_name: str, text: str,
) -> Optional[Dict[str, Any]]:
    """Run hooks in registration order; return the first truthy result."""
    for fn in _text_user_message_hooks:
        try:
            result = fn(lanlan_name, text)
            if result:
                return result
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("[EventBus] text_user_message hook raised: %s", exc)
    return None


async def publish_analyze_request_reliably(
    lanlan_name: str,
    trigger: str,
    messages: list[dict],
    *,
    ack_timeout_s: float = 0.8,
    retries: int = 1,
    conversation_id: Optional[str] = None,
    action_intent: Optional[float] = None,
) -> bool:
    """Reliably publish analyze_request: carries event_id + ack, with short retries.

    ``action_intent`` (0..1, or ``None``) is the cheap pre-gate hint produced by
    the master-emotion call that already ran at input-time. It rides this same
    payload so the agent can cheaply decide whether to skip its expensive
    assessment. ``None`` (reading unavailable / no usable signal) is omitted from
    the event → the agent gate fails open (runs the assessment).
    """
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
        if conversation_id:
            event["conversation_id"] = conversation_id
        # Only an optimization hint; omitted when None so the agent fails open.
        if action_intent is not None:
            event["action_intent"] = action_intent

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
                if bridge.owner_loop.is_closed():
                    logger.debug("[EventBus] owner_loop closed, skipping publish")
                    sent = False
                else:
                    coro = bridge.publish_analyze_request(event)
                    try:
                        cf = asyncio.run_coroutine_threadsafe(coro, bridge.owner_loop)
                        sent = await asyncio.wrap_future(cf)
                    except Exception as e:
                        coro.close()
                        logger.debug("[EventBus] publish_analyze_request threadsafe failed: %s", e)
                        sent = False
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


async def publish_voice_transcript_observed_best_effort(
    lanlan_name: str,
    transcript: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Broadcast a realtime voice transcript to agent/plugins without waiting.

    This is intentionally best-effort. Main voice handling must not be blocked
    or controlled by plugins; plugin handlers may receive the event late, not at
    all, or after it is no longer relevant.
    """
    text = str(transcript or "").strip()
    if not text:
        return False
    event_id = uuid.uuid4().hex
    event = {
        "event_type": "voice_transcript_observed",
        "event_id": event_id,
        "lanlan_name": lanlan_name,
        "transcript": text,
        "metadata": dict(metadata or {}),
    }
    sent = await publish_session_event(event)
    if not sent:
        logger.debug(
            "[EventBus] voice_transcript_observed not sent: no main bridge lanlan=%s",
            lanlan_name,
        )
    return sent


async def publish_voice_transcript_request_reliably(
    lanlan_name: str,
    transcript: str,
    *,
    timeout_s: float = 1.2,
    retries: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Backward-compatible wrapper for the old request API.

    The old API waited for a plugin action. Main no longer waits for or applies
    those actions, so callers get ``None`` after the best-effort broadcast is
    queued. ``timeout_s`` and ``retries`` are accepted only for source
    compatibility.
    """
    await publish_voice_transcript_observed_best_effort(
        lanlan_name,
        transcript,
        metadata=metadata,
    )
    return None
