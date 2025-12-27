from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from plugin.core.state import state


logger = logging.getLogger("plugin.bus_subscriptions")


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
        except Exception as e:
            logger.exception("Failed to subscribe bus_change_hub: %s", e)

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

        for sub_id, info in subs.items():
            plugin_id = info.get("from_plugin")
            if not isinstance(plugin_id, str) or not plugin_id:
                continue

            with state.plugin_hosts_lock:
                host = state.plugin_hosts.get(plugin_id)
            if not host:
                continue

            args: Dict[str, Any] = {
                "sub_id": sub_id,
                "bus": delta.bus,
                "op": delta.op,
                "delta": dict(delta.payload or {}),
            }

            try:
                await host.trigger_custom_event(
                    event_type="bus",
                    event_id="change",
                    args=args,
                    timeout=float(info.get("timeout", 5.0)),
                )
            except Exception:
                continue
            try:
                logger.info(
                    "Pushed bus.change to plugin=%s sub_id=%s bus=%s op=%s",
                    plugin_id,
                    sub_id,
                    delta.bus,
                    delta.op,
                )
            except Exception:
                pass


bus_subscription_manager = BusSubscriptionManager()


def new_sub_id() -> str:
    return str(uuid.uuid4())
