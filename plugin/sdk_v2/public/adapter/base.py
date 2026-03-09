"""Adapter base implementation for SDK v2."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from plugin.sdk_v2.shared.core.types import JsonObject, PluginContextProtocol
from plugin.sdk_v2.shared.models import Err, Ok, Result

from .gateway_contracts import LoggerLike
from .types import RouteRule


class AdapterMode(str, Enum):
    GATEWAY = "gateway"
    ROUTER = "router"
    BRIDGE = "bridge"
    HYBRID = "hybrid"


@dataclass(slots=True)
class AdapterConfig:
    mode: AdapterMode = AdapterMode.HYBRID
    protocols: dict[str, JsonObject] = field(default_factory=dict)
    routes: list[RouteRule] = field(default_factory=list)
    priority: int = 0

    @classmethod
    def from_dict(cls, raw: JsonObject) -> "AdapterConfig":
        mode = raw.get("mode", AdapterMode.HYBRID)
        try:
            mode_value = mode if isinstance(mode, AdapterMode) else AdapterMode(str(mode))
        except Exception:
            mode_value = AdapterMode.HYBRID
        protocols = raw.get("protocols", {})
        routes_raw = raw.get("routes", [])
        routes: list[RouteRule] = []
        if isinstance(routes_raw, list):
            for item in routes_raw:
                if isinstance(item, dict):
                    routes.append(RouteRule(**item))
        return cls(mode=mode_value, protocols=dict(protocols) if isinstance(protocols, dict) else {}, routes=routes, priority=int(raw.get("priority", 0) or 0))


class AdapterContext:
    def __init__(
        self,
        adapter_id: str,
        config: AdapterConfig,
        logger: LoggerLike,
        plugin_ctx: PluginContextProtocol | None = None,
    ) -> None:
        self.adapter_id = adapter_id
        self.config = config
        self.logger = logger
        self.plugin_ctx = plugin_ctx
        self._event_handlers: dict[str, list[object]] = {}

    def register_event_handler(self, event_type: str, handler: object) -> None:
        self._event_handlers.setdefault(event_type, []).append(handler)

    def get_event_handlers(self, event_type: str) -> list[object]:
        return list(self._event_handlers.get(event_type, []))

    async def call_plugin(
        self,
        plugin_id: str,
        entry: str,
        payload: JsonObject,
        timeout: float = 30.0,
    ) -> Result[JsonObject | None, Exception]:
        ctx = self.plugin_ctx
        if ctx is None:
            return Err(RuntimeError("plugin_ctx is not available"))
        caller = getattr(ctx, "trigger_plugin_event_async", None)
        if not callable(caller):
            return Err(RuntimeError("ctx.trigger_plugin_event_async is not available"))
        try:
            result = await caller(target_plugin_id=plugin_id, event_type="plugin_entry", event_id=entry, params=payload, timeout=timeout)
        except Exception as error:
            return Err(error)
        return Ok(result if isinstance(result, dict) else None)

    async def broadcast_event(self, event_type: str, payload: JsonObject) -> Result[list[JsonObject], Exception]:
        handlers = self.get_event_handlers(event_type)
        outputs: list[JsonObject] = []
        for handler in handlers:
            if callable(handler):
                try:
                    result = handler(payload)
                    if hasattr(result, "__await__"):
                        result = await result
                    if isinstance(result, dict):
                        outputs.append(result)
                except Exception as error:
                    return Err(error)
        return Ok(outputs)


class AdapterBase:
    def __init__(self, config: AdapterConfig, ctx: AdapterContext):
        self.config = config
        self.ctx = ctx

    @property
    def adapter_id(self) -> str:
        return self.ctx.adapter_id

    @property
    def mode(self) -> AdapterMode:
        return self.config.mode

    async def on_message(self, message: JsonObject) -> Result[JsonObject | None, Exception]:
        return Ok(message)

    async def on_startup(self) -> Result[None, Exception]:
        return Ok(None)

    async def on_shutdown(self) -> Result[None, Exception]:
        return Ok(None)


__all__ = ["AdapterMode", "AdapterConfig", "AdapterContext", "AdapterBase"]
