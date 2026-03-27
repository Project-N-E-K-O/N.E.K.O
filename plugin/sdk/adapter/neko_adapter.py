"""Neko adapter plugin for SDK v2."""

from __future__ import annotations

import inspect
from typing import Any, cast

from plugin.sdk.plugin.base import NekoPluginBase
from plugin.sdk.shared.models import Err, Ok, Result
from plugin.sdk.shared.models.exceptions import AdapterErrorLike, CapabilityUnavailableError, InvalidArgumentError, SdkError, TransportError

from .base import AdapterBase, AdapterConfig, AdapterContext, AdapterMode
from .decorators import ADAPTER_EVENT_META, ADAPTER_LIFECYCLE_META, AdapterEventMeta, _matches_action_pattern
from .types import RouteRule, RouteTarget


class NekoAdapterPlugin(NekoPluginBase):
    __freezable__: list[str] = []

    def __init__(self, plugin_ctx):
        super().__init__(plugin_ctx)
        self._adapter_config = AdapterConfig()
        self._adapter_context = AdapterContext(
            adapter_id=str(getattr(plugin_ctx, "plugin_id", "adapter")),
            config=self._adapter_config,
            logger=self.logger,
            plugin_ctx=plugin_ctx,
        )
        self._adapter_base = AdapterBase(self._adapter_config, adapter_ctx=self._adapter_context)
        self._adapter_lifecycle_handlers: dict[str, list[tuple[int, object]]] = {
            "startup": [],
            "shutdown": [],
        }
        self._discover_adapter_members()

    @property
    def adapter_config(self) -> AdapterConfig:
        return self._adapter_config

    @property
    def adapter_context(self) -> AdapterContext:
        return self._adapter_context

    @property
    def adapter_mode(self) -> AdapterMode:
        return self._adapter_config.mode

    @property
    def adapter_id(self) -> str:
        return self._adapter_context.adapter_id

    async def adapter_startup(self) -> Result[None, SdkError]:
        return await self._run_adapter_lifecycle("startup")

    async def adapter_shutdown(self) -> Result[None, SdkError]:
        return await self._run_adapter_lifecycle("shutdown")

    def _normalize_dynamic_entry_result(self, result: object, *, op_name: str, name: str) -> Result[bool, AdapterErrorLike]:
        if isinstance(result, Err):
            error = result.error
            return Err(
                error
                if isinstance(error, (InvalidArgumentError, CapabilityUnavailableError, TransportError))
                else TransportError(
                    str(error),
                    op_name=op_name,
                    plugin_id=self.adapter_id,
                    entry_ref=f"{self.adapter_id}:{name}",
                )
            )
        if isinstance(result, Ok) or result is None or result is True:
            return Ok(True)
        if result is False:
            return Err(
                TransportError(
                    f"{op_name.rsplit('.', 1)[-1]} returned false",
                    op_name=op_name,
                    plugin_id=self.adapter_id,
                    entry_ref=f"{self.adapter_id}:{name}",
                )
            )
        return Err(
            TransportError(
                f"{op_name.rsplit('.', 1)[-1]} returned unsupported result",
                op_name=op_name,
                plugin_id=self.adapter_id,
                entry_ref=f"{self.adapter_id}:{name}",
            )
        )

    def _iter_adapter_members(self):
        seen: set[str] = set()
        for cls in type(self).__mro__:
            for name, raw in cls.__dict__.items():
                if name in seen:
                    continue
                seen.add(name)
                event_meta = getattr(raw, ADAPTER_EVENT_META, None)
                lifecycle_meta = getattr(raw, ADAPTER_LIFECYCLE_META, None)
                if not isinstance(event_meta, AdapterEventMeta) and not isinstance(lifecycle_meta, dict):
                    continue
                bound = getattr(self, name, None)
                if callable(bound):
                    yield name, raw, bound

    def _discover_adapter_members(self) -> None:
        for name, raw, bound in self._iter_adapter_members():
            event_meta = getattr(raw, ADAPTER_EVENT_META, None)
            if isinstance(event_meta, AdapterEventMeta):
                self._register_discovered_event(name, bound, event_meta)

            lifecycle_meta = getattr(raw, ADAPTER_LIFECYCLE_META, None)
            if isinstance(lifecycle_meta, dict):
                stage = str(lifecycle_meta.get("stage", "")).strip()
                if stage in self._adapter_lifecycle_handlers:
                    priority = int(lifecycle_meta.get("priority", 0) or 0)
                    self._adapter_lifecycle_handlers[stage].append((priority, bound))

        for stage in self._adapter_lifecycle_handlers:
            self._adapter_lifecycle_handlers[stage].sort(key=lambda item: item[0], reverse=True)

    def _register_discovered_event(self, name: str, handler: object, meta: AdapterEventMeta) -> None:
        handler_fn = cast(Any, handler)
        target_name = str(meta.pattern or "").strip()
        if meta.protocol == "mcp" and meta.action == "tool_call":
            self.register_adapter_tool(target_name if target_name not in {"", "*"} else name, handler_fn)
            return
        if meta.protocol == "mcp" and meta.action == "resource_read":
            self.register_adapter_resource(target_name if target_name not in {"", "*"} else name, handler_fn)
            return
        self._adapter_context.register_event_handler(
            meta.action,
            handler_fn,
            protocol=meta.protocol,
            action=meta.action,
            pattern=meta.pattern,
            priority=meta.priority,
        )

    async def _run_adapter_lifecycle(self, stage: str) -> Result[None, SdkError]:
        for _priority, handler in self._adapter_lifecycle_handlers.get(stage, []):
            handler_fn = cast(Any, handler)
            try:
                result = handler_fn()
                if inspect.isawaitable(result):
                    result = await result
            except Exception as error:
                if isinstance(error, SdkError):
                    return Err(error)
                return Err(
                    TransportError(
                        str(error),
                        op_name=f"adapter.{stage}",
                        plugin_id=self.adapter_id,
                        entry_ref=f"{self.adapter_id}:{getattr(handler, '__name__', stage)}",
                    )
                )
            if isinstance(result, Err):
                error = result.error
                if isinstance(error, SdkError):
                    return Err(error)
                return Err(
                    TransportError(
                        str(error),
                        op_name=f"adapter.{stage}",
                        plugin_id=self.adapter_id,
                        entry_ref=f"{self.adapter_id}:{getattr(handler, '__name__', stage)}",
                    )
                )
        return Ok(None)

    def register_adapter_tool(self, name: str, handler: object) -> bool:
        return self._adapter_base.register_tool(name, handler)

    def register_adapter_resource(self, name: str, handler: object) -> bool:
        return self._adapter_base.register_resource(name, handler)

    def get_adapter_tool(self, name: str) -> object | None:
        return self._adapter_base.get_tool(name)

    def get_adapter_resource(self, name: str) -> object | None:
        return self._adapter_base.get_resource(name)

    def list_adapter_tools(self) -> list[str]:
        return self._adapter_base.list_tools()

    def list_adapter_resources(self) -> list[str]:
        return self._adapter_base.list_resources()

    def add_adapter_route(self, rule: RouteRule) -> bool:
        return self._adapter_base.add_route(rule)

    def find_matching_route(self, protocol: str, action: str) -> RouteRule | None:
        matched = sorted(
            [
                rule
                for rule in self._adapter_base.list_routes()
                if rule.protocol in {"*", protocol}
                and (
                    _matches_action_pattern(rule.action, action)
                    or _matches_action_pattern(rule.pattern, action)
                )
            ],
            key=lambda rule: rule.priority,
            reverse=True,
        )
        return matched[0] if matched else None

    async def forward_to_plugin(self, plugin_id: str, entry_id: str, payload: dict[str, object], timeout: float = 30.0) -> Result[dict[str, object] | None, AdapterErrorLike]:
        return cast(
            Result[dict[str, object] | None, AdapterErrorLike],
            await self._adapter_base.forward_to_plugin(plugin_id, entry_id, cast(Any, payload), timeout=timeout),
        )

    async def handle_adapter_message(self, protocol: str, action: str, payload: dict[str, object]) -> Result[dict[str, object] | None, AdapterErrorLike]:
        route = self.find_matching_route(protocol, action)
        if route is None:
            return Ok(None)
        if route.target == RouteTarget.PLUGIN and route.plugin_id and route.entry_id:
            return await self.forward_to_plugin(route.plugin_id, route.entry_id, payload)
        if route.target == RouteTarget.BROADCAST:
            out = await self._adapter_base.broadcast(action, cast(Any, payload), protocol=protocol)
            if isinstance(out, Err):
                return cast(Result[dict[str, object] | None, AdapterErrorLike], out)
            return cast(Result[dict[str, object] | None, AdapterErrorLike], Ok({"responses": out.value}))
        return Ok(payload)

    async def register_adapter_tool_as_entry(self, name: str, handler: object, display_name: str = "", description: str = "") -> Result[bool, AdapterErrorLike]:
        if not isinstance(name, str) or name.strip() == "":
            return Err(InvalidArgumentError("name must be non-empty"))
        if not self.register_adapter_tool(name, handler):
            return Err(InvalidArgumentError("failed to register adapter tool"))
        if hasattr(self, "register_dynamic_entry"):
            try:
                register_dynamic_entry = cast(Any, getattr(self, "register_dynamic_entry"))
                registered = register_dynamic_entry(
                    name,
                    handler,
                    name=display_name or name,
                    description=description,
                )
                if inspect.isawaitable(registered):
                    registered = await registered
            except Exception as error:
                self._adapter_base.unregister_tool(name)
                return Err(
                    error
                    if isinstance(error, (InvalidArgumentError, CapabilityUnavailableError, TransportError))
                    else TransportError(
                        str(error),
                        op_name="adapter.register_adapter_tool_as_entry",
                        plugin_id=self.adapter_id,
                        entry_ref=f"{self.adapter_id}:{name}",
                    )
                )
            normalized = self._normalize_dynamic_entry_result(
                registered,
                op_name="adapter.register_adapter_tool_as_entry",
                name=name,
            )
            if isinstance(normalized, Err):
                self._adapter_base.unregister_tool(name)
                return normalized
            return normalized
        return Ok(True)

    async def unregister_adapter_tool_entry(self, name: str) -> Result[bool, AdapterErrorLike]:
        if hasattr(self, "unregister_dynamic_entry"):
            try:
                unregister_dynamic_entry = cast(Any, getattr(self, "unregister_dynamic_entry"))
                removed = unregister_dynamic_entry(name)
                if inspect.isawaitable(removed):
                    removed = await removed
            except Exception as error:
                return Err(
                    error
                    if isinstance(error, (InvalidArgumentError, CapabilityUnavailableError, TransportError))
                    else TransportError(
                        str(error),
                        op_name="adapter.unregister_adapter_tool_entry",
                        plugin_id=self.adapter_id,
                        entry_ref=f"{self.adapter_id}:{name}",
                    )
                )
            normalized = self._normalize_dynamic_entry_result(
                removed,
                op_name="adapter.unregister_adapter_tool_entry",
                name=name,
            )
            if isinstance(normalized, Err):
                return normalized
            removed_tool = self._adapter_base.unregister_tool(name)
            return Ok(bool(removed_tool))
        removed_tool = self._adapter_base.unregister_tool(name)
        return Ok(bool(removed_tool))

    def list_adapter_routes(self) -> list[RouteRule]:
        return self._adapter_base.list_routes()


__all__ = ["NekoAdapterPlugin"]
