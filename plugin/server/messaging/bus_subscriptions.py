from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from plugin.core.state import state
from plugin.settings import PLUGIN_LOG_BUS_SUBSCRIPTIONS, PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS


@dataclass(frozen=True)
class BusDelta:
    bus: str
    op: str
    payload: Dict[str, Any]
    at: float


class BusSubscriptionManager:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._queue: asyncio.Queue[BusDelta] = asyncio.Queue(maxsize=1000)
        self._unsubs: list[Any] = []
        self._last_log_key: Optional[tuple] = None
        self._last_log_time: float = 0.0
        self._last_log_repeat_count: int = 0
        # Dispatch control: bounded concurrency + slow subscriber isolation.
        self._dispatch_sem = asyncio.Semaphore(64)
        self._push_timeout_s: float = 1.0
        self._fail_threshold: int = 3
        self._pause_seconds: float = 5.0
        self._sub_failures: Dict[Tuple[str, str], int] = {}
        self._sub_paused_until: Dict[Tuple[str, str], float] = {}

    async def start(self) -> None:
        if self._task is not None:
            return

        def _on_change_factory(bus: str):
            def _on_change(op: str, payload: Dict[str, Any]) -> None:
                try:
                    self._queue.put_nowait(BusDelta(bus=bus, op=str(op), payload=dict(payload or {}), at=time.time()))
                except Exception:
                    return

            return _on_change

        try:
            self._unsubs.append(state.bus_change_hub.subscribe("messages", _on_change_factory("messages")))
            self._unsubs.append(state.bus_change_hub.subscribe("events", _on_change_factory("events")))
            self._unsubs.append(state.bus_change_hub.subscribe("lifecycle", _on_change_factory("lifecycle")))
            self._unsubs.append(state.bus_change_hub.subscribe("runs", _on_change_factory("runs")))
            self._unsubs.append(state.bus_change_hub.subscribe("export", _on_change_factory("export")))
        except Exception as e:
            logger.opt(exception=True).exception("Failed to subscribe bus_change_hub: {}", e)

        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        for u in list(self._unsubs):
            try:
                u()
            except Exception:
                pass
        self._unsubs.clear()

        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                delta = await self._queue.get()
                try:
                    await self._dispatch(delta)
                except Exception:
                    logger.exception("Error dispatching bus delta")
            except asyncio.CancelledError:
                break

    async def _dispatch(self, delta: BusDelta) -> None:
        subs = state.get_bus_subscriptions(delta.bus)
        if not subs:
            return

        async def _send_one(sub_id: str, info: Dict[str, Any]) -> None:
            plugin_id = info.get("from_plugin")
            if not isinstance(plugin_id, str) or not plugin_id:
                return

            key2 = (plugin_id, str(sub_id))
            now_m = time.monotonic()
            try:
                until = float(self._sub_paused_until.get(key2, 0.0))
            except Exception:
                until = 0.0
            if until > now_m:
                return

            # 使用缓存快照避免锁竞争
            hosts_snapshot = state.get_plugin_hosts_snapshot_cached(timeout=1.0)
            host = hosts_snapshot.get(plugin_id)
            if not host:
                return

            d: Dict[str, Any] = dict(delta.payload or {})
            try:
                if "rev" not in d:
                    d["rev"] = int(state.get_bus_rev(delta.bus))
            except Exception:
                pass

            async with self._dispatch_sem:
                try:
                    await asyncio.wait_for(
                        host.push_bus_change(
                            sub_id=str(sub_id or ""),
                            bus=str(delta.bus or ""),
                            op=str(delta.op or ""),
                            delta=d,
                        ),
                        timeout=float(self._push_timeout_s),
                    )
                except Exception:
                    # Failure tracking + circuit breaker
                    try:
                        nfail = int(self._sub_failures.get(key2, 0)) + 1
                        self._sub_failures[key2] = nfail
                        if nfail >= int(self._fail_threshold):
                            self._sub_paused_until[key2] = time.monotonic() + float(self._pause_seconds)
                            self._sub_failures[key2] = 0
                    except Exception:
                        pass
                    return

            # Success -> reset failures
            try:
                self._sub_failures[key2] = 0
            except Exception:
                pass

            if PLUGIN_LOG_BUS_SUBSCRIPTIONS:
                try:
                    window = PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS
                    if window and window > 0:
                        now_ts = time.monotonic()
                        key = (plugin_id, sub_id, delta.bus, delta.op)
                        last_key = self._last_log_key
                        last_ts = self._last_log_time
                        if last_key == key and last_ts > 0.0 and (now_ts - last_ts) <= window:
                            self._last_log_repeat_count += 1
                            return

                        if last_key is not None and self._last_log_repeat_count > 0:
                            logger.info(
                                "Pushed bus.change (suppressed {} duplicate entries for plugin={} sub_id={} bus={} op={})",
                                self._last_log_repeat_count,
                                last_key[0],
                                last_key[1],
                                last_key[2],
                                last_key[3],
                            )

                        self._last_log_key = key
                        self._last_log_time = now_ts
                        self._last_log_repeat_count = 0

                    logger.info(
                        "Pushed bus.change to plugin={} sub_id={} bus={} op={}",
                        plugin_id,
                        sub_id,
                        delta.bus,
                        delta.op,
                    )
                except Exception:
                    pass

        tasks = []
        for sid, info in subs.items():
            if not isinstance(info, dict):
                continue
            tasks.append(asyncio.create_task(_send_one(str(sid), dict(info)), name="bus-dispatch-one"))
        if not tasks:
            return
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            return


bus_subscription_manager = BusSubscriptionManager()


def new_sub_id() -> str:
    return str(uuid.uuid4())
