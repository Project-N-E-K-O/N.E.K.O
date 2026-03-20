"""Adapter-facing base facade for SDK v2."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, TypeAlias, cast

from plugin.sdk_v2.shared.core.types import JsonObject, PluginContextProtocol
from plugin.sdk_v2.shared.models import Err, Ok, Result
from plugin.sdk_v2.shared.models.exceptions import CapabilityUnavailableError, SdkError, TransportError

from .decorators import _matches_action_pattern
from .gateway_contracts import LoggerLike
from .types import RouteRule, RouteTarget

_ROUTE_RULE_FIELDS = frozenset(RouteRule.__dataclass_fields__)
AdapterEventHandler: TypeAlias = Callable[[JsonObject], object]


class _RouteRuleLike(Protocol):
    protocol: str
    action: str
    target: object
    plugin_id: str | None
    entry_id: str | None
    priority: int
    pattern: str | None


RouteRuleInput: TypeAlias = RouteRule | Mapping[str, Any] | _RouteRuleLike


def _route_rule_from_mapping(raw: Mapping[str, object]) -> RouteRule | None:
    filtered: dict[str, object] = {
        key: value
        for key, value in raw.items()
        if key in _ROUTE_RULE_FIELDS
    }
    if not filtered:
        return None
    try:
        if "priority" in filtered:
            filtered["priority"] = int(cast(object, filtered["priority"]))
        if "target" in filtered:
            raw_target = filtered["target"]
            if isinstance(raw_target, RouteTarget):
                filtered["target"] = raw_target
            else:
                target_value = getattr(raw_target, "value", raw_target)
                filtered["target"] = RouteTarget(str(target_value))
        return cast(RouteRule, RouteRule(**cast(Any, filtered)))
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class _RegisteredEventHandler:
    event_type: str
    handler: AdapterEventHandler
    protocol: str = "*"
    action: str = "*"
    pattern: str | None = None
    priority: int = 0


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
        priority_raw = raw.get("priority", 0)
        try:
            priority_value = int(cast(int | str | bytes | bytearray, priority_raw))
        except Exception:
            priority_value = 0
        protocols = raw.get("protocols", {})
        routes_raw = raw.get("routes", [])
        routes: list[RouteRule] = []
        if isinstance(routes_raw, list):
            for item in routes_raw:
                if isinstance(item, Mapping):
                    rule = _route_rule_from_mapping(cast(Mapping[str, object], item))
                    if rule is not None:
                        routes.append(rule)
        protocols_value: dict[str, JsonObject] = {}
        if isinstance(protocols, Mapping):
            for key, value in protocols.items():
                if isinstance(key, str) and isinstance(value, dict):
                    protocols_value[key] = cast(JsonObject, dict(value))
        return cls(
            mode=mode_value,
            protocols=protocols_value,
            routes=routes,
            priority=priority_value,
        )


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
        self._event_handlers: dict[str, list[_RegisteredEventHandler]] = {}

    def register_event_handler(
        self,
        event_type: str,
        handler: AdapterEventHandler,
        *,
        protocol: str = "*",
        action: str | None = None,
        pattern: str | None = None,
        priority: int = 0,
    ) -> None:
        if not callable(handler):
            raise TypeError("handler must be callable")
        registration = _RegisteredEventHandler(
            event_type=event_type,
            handler=handler,
            protocol=protocol,
            action=action or event_type,
            pattern=pattern,
            priority=priority,
        )
        self._event_handlers.setdefault(event_type, []).append(registration)

    def get_event_handlers(self, event_type: str, *, protocol: str | None = None) -> list[AdapterEventHandler]:
        matched: list[_RegisteredEventHandler] = []
        candidates: list[_RegisteredEventHandler] = list(self._event_handlers.get(event_type, []))
        seen_ids = {id(item) for item in candidates}
        for registered_event_type, handlers in self._event_handlers.items():
            if registered_event_type == event_type:
                continue
            for item in handlers:
                if id(item) in seen_ids:
                    continue
                if _matches_action_pattern(item.event_type, event_type) or _matches_action_pattern(item.pattern, event_type):
                    candidates.append(item)
                    seen_ids.add(id(item))
        for item in candidates:
            event_matches = (
                item.event_type == event_type
                or _matches_action_pattern(item.event_type, event_type)
                or _matches_action_pattern(item.pattern, event_type)
            )
            if not event_matches:
                continue
            if protocol is not None and item.protocol not in {"*", protocol}:
                continue
            action_matches = (
                item.action == event_type
                or _matches_action_pattern(item.action, event_type)
                or _matches_action_pattern(item.pattern, event_type)
            )
            if action_matches:
                matched.append(item)
        matched.sort(key=lambda item: item.priority, reverse=True)
        return [item.handler for item in matched]

    async def call_plugin(
        self,
        plugin_id: str,
        entry_id: str,
        payload: JsonObject,
        timeout: float = 30.0,
    ) -> Result[JsonObject | None, CapabilityUnavailableError | TransportError]:
        plugin_ctx = self.plugin_ctx
        entry_ref = f"{plugin_id}:{entry_id}"
        if plugin_ctx is None:
            return Err(
                CapabilityUnavailableError(
                    "plugin_ctx is not available",
                    op_name="adapter.call_plugin",
                    capability="plugin_ctx",
                    plugin_id=plugin_id,
                    entry_ref=entry_ref,
                    timeout=timeout,
                )
            )
        entry_caller = getattr(plugin_ctx, "call_plugin_entry", None)
        event_caller = getattr(plugin_ctx, "trigger_plugin_event", None)
        try:
            if callable(entry_caller):
                invoke_result = entry_caller(
                    target_plugin_id=plugin_id,
                    entry_id=entry_id,
                    params=payload,
                    timeout=timeout,
                )
            elif callable(event_caller):
                invoke_result = event_caller(
                    target_plugin_id=plugin_id,
                    event_type="plugin_entry",
                    event_id=entry_id,
                    params=payload,
                    timeout=timeout,
                )
            else:
                return Err(
                    CapabilityUnavailableError(
                        "plugin_ctx.call_plugin_entry / plugin_ctx.trigger_plugin_event is not available",
                        op_name="adapter.call_plugin",
                        capability="plugin_ctx.call_plugin_entry",
                        plugin_id=plugin_id,
                        entry_ref=entry_ref,
                        timeout=timeout,
                    )
                )
            result = await invoke_result if inspect.isawaitable(invoke_result) else invoke_result
        except Exception as error:
            if isinstance(error, (CapabilityUnavailableError, TransportError)):
                return Err(error)
            return Err(TransportError(str(error), op_name="adapter.call_plugin", plugin_id=plugin_id, entry_ref=entry_ref, timeout=timeout))
        return Ok(result if isinstance(result, dict) else None)

    async def broadcast_event(
        self,
        event_type: str,
        payload: JsonObject,
        *,
        protocol: str | None = None,
    ) -> Result[list[JsonObject], TransportError]:
        handlers = self.get_event_handlers(event_type, protocol=protocol)
        outputs: list[JsonObject] = []
        first_error: TransportError | None = None
        for handler in handlers:
            if callable(handler):
                try:
                    result = handler(payload)
                    if inspect.isawaitable(result):
                        result = await result
                    if isinstance(result, dict):
                        outputs.append(result)
                except Exception as error:
                    if first_error is None:
                        first_error = error if isinstance(error, TransportError) else TransportError(
                            str(error),
                            op_name="adapter.broadcast_event",
                            event_type=event_type,
                        )
        if first_error is not None:
            return Err(first_error)
        return Ok(outputs)


class AdapterBase:
    def __init__(self, config: AdapterConfig, adapter_ctx: AdapterContext):
        self.config = AdapterConfig(
            mode=config.mode,
            protocols={key: dict(value) for key, value in config.protocols.items()},
            routes=list(config.routes),
            priority=config.priority,
        )
        self.adapter_ctx = adapter_ctx
        self.adapter_ctx.config = self.config
        self._tools: dict[str, object] = {}
        self._resources: dict[str, object] = {}
        self._routes: list[RouteRule] = list(self.config.routes)

    @property
    def adapter_id(self) -> str:
        return self.adapter_ctx.adapter_id

    @property
    def mode(self) -> AdapterMode:
        return self.config.mode

    def register_tool(self, name: str, handler: object) -> bool:
        if not isinstance(name, str) or name.strip() == "":
            return False
        self._tools[name] = handler
        return True

    def unregister_tool(self, name: str) -> object | None:
        return self._tools.pop(name, None)

    def register_resource(self, name: str, handler: object) -> bool:
        if not isinstance(name, str) or name.strip() == "":
            return False
        self._resources[name] = handler
        return True

    def unregister_resource(self, name: str) -> object | None:
        return self._resources.pop(name, None)

    def get_tool(self, name: str) -> object | None:
        return self._tools.get(name)

    def get_resource(self, name: str) -> object | None:
        return self._resources.get(name)

    def list_tools(self) -> list[str]:
        return sorted(self._tools.keys())

    def list_resources(self) -> list[str]:
        return sorted(self._resources.keys())

    def add_route(self, rule: RouteRuleInput) -> bool:
        if isinstance(rule, RouteRule):
            normalized = rule
        elif isinstance(rule, Mapping):
            normalized = _route_rule_from_mapping(cast(Mapping[str, object], rule))
            if normalized is None:
                return False
        elif all(hasattr(rule, name) for name in ("protocol", "action", "target", "plugin_id", "entry_id", "priority", "pattern")):
            normalized = _route_rule_from_mapping(
                {
                    "protocol": rule.protocol,
                    "action": rule.action,
                    "pattern": rule.pattern,
                    "target": rule.target,
                    "plugin_id": rule.plugin_id,
                    "entry_id": rule.entry_id,
                    "priority": rule.priority,
                }
            )
            if normalized is None:
                return False
        else:
            return False
        self._routes.append(normalized)
        self.config.routes = [*self.config.routes, normalized]
        return True

    def list_routes(self) -> list[RouteRule]:
        return list(self._routes)

    async def forward_to_plugin(self, plugin_id: str, entry_id: str, payload: JsonObject, timeout: float = 30.0) -> Result[JsonObject | None, CapabilityUnavailableError | TransportError]:
        return await self.adapter_ctx.call_plugin(plugin_id, entry_id, payload, timeout=timeout)

    async def broadcast(
        self,
        event_type: str,
        payload: JsonObject,
        *,
        protocol: str | None = None,
    ) -> Result[list[JsonObject], TransportError]:
        return await self.adapter_ctx.broadcast_event(
            event_type,
            payload,
            protocol=protocol,
        )

    async def on_message(self, message: JsonObject) -> Result[JsonObject | None, SdkError]:
        return Ok(message)

    async def on_startup(self) -> Result[None, SdkError]:
        return Ok(None)

    async def on_shutdown(self) -> Result[None, SdkError]:
        return Ok(None)


__all__ = ["AdapterBase", "AdapterConfig", "AdapterContext", "AdapterMode"]
