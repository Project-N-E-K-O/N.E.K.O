"""Plugin-facing base facade for SDK v2."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from plugin.sdk.shared.constants import EVENT_META_ATTR, NEKO_PLUGIN_META_ATTR, NEKO_PLUGIN_TAG
from plugin.sdk.shared.core.base import DEFAULT_PLUGIN_VERSION as _DEFAULT_PLUGIN_VERSION
from plugin.sdk.shared.core.base import NekoPluginBase as _SharedNekoPluginBase
from plugin.sdk.shared.core.base import PluginMeta as _SharedPluginMeta
from plugin.sdk.shared.core.events import EventHandler, EventMeta
from plugin.sdk.shared.i18n import PluginI18n, load_plugin_i18n_from_meta
from plugin.sdk.shared.models.exceptions import EntryConflictError

from .llm_tool import (
    LlmToolMeta,
    collect_llm_tool_methods,
    entry_id_for_tool,
    validate_tool_name,
)

DEFAULT_PLUGIN_VERSION = _DEFAULT_PLUGIN_VERSION


class PluginMeta(_SharedPluginMeta):
    """Plugin-facing metadata model."""


class NekoPluginBase(_SharedNekoPluginBase):
    """Plugin-facing base class with convenience helpers."""

    def __init__(self, ctx):
        super().__init__(ctx)
        # Promote plugin-facing helper instead of exposing the shared minimal contract.
        from .runtime import Plugins

        self.plugins = Plugins(self.ctx)
        self._memory_client = None
        self._system_info_client = None
        self.i18n = self._load_plugin_i18n()
        self._static_ui_config: dict[str, Any] | None = None
        self._list_actions: list[dict[str, Any]] = []
        self._dynamic_entries: dict[str, dict[str, Any]] = {}
        # plugin_id-scoped registry of LLM tools we've claimed locally.
        # Tracks (name -> LlmToolMeta) so we can re-emit IPC notifications
        # on demand and validate against duplicate registrations.
        # The actual handler lives in ``_dynamic_entries[__llm_tool__name]``.
        self._llm_tools: dict[str, LlmToolMeta] = {}
        # Set to True after the first auto-registration pass so that
        # subsequent lifecycle reloads don't double-register decorator-
        # tagged methods. Manual calls to ``register_llm_tool`` are
        # tracked separately via ``self._llm_tools``.
        self._llm_tools_auto_registered: bool = False
        # Auto-register every method tagged with ``@llm_tool``. Doing
        # this at the end of ``super().__init__()`` means subclasses
        # don't need to remember to call anything — the decorator alone
        # is enough. The actual handler doesn't fire until the LLM
        # picks the tool, so it's safe to register before subclass
        # ``__init__`` finishes setting up state the handler reads
        # (e.g. config dicts, service clients).
        try:
            self._register_decorated_llm_tools()
        except Exception:
            # Never let auto-registration prevent the plugin from
            # constructing. The plugin can still register imperatively
            # later and the host's IPC consumer logs the failure.
            logger = getattr(self, "logger", None)
            if logger is not None:
                try:
                    logger.exception("Auto-registration of @llm_tool methods failed")
                except Exception:
                    # Logger itself failed — nothing left to do; matches
                    # the pattern used in ``_notify_host_comm`` above.
                    pass

    def _load_plugin_i18n(self) -> PluginI18n:
        meta: dict[str, object] = {"config_path": str(self.config_dir / "plugin.toml")}
        metadata = self.metadata
        if isinstance(metadata.get("i18n"), Mapping):
            meta["i18n"] = dict(metadata["i18n"])  # type: ignore[arg-type]
            config_path_obj = metadata.get("config_path")
            if isinstance(config_path_obj, (str, Path)):
                meta["config_path"] = str(config_path_obj)
            return load_plugin_i18n_from_meta(meta)

        config_path = getattr(self.ctx, "config_path", None)
        if isinstance(config_path, (str, Path)):
            meta["config_path"] = str(config_path)
            try:
                with Path(config_path).open("rb") as stream:
                    plugin_section = tomllib.load(stream).get("plugin")
                if isinstance(plugin_section, Mapping) and isinstance(plugin_section.get("i18n"), Mapping):
                    meta["i18n"] = dict(plugin_section["i18n"])  # type: ignore[arg-type]
            except Exception:
                pass
        return load_plugin_i18n_from_meta(meta)

    @property
    def plugin_id(self) -> str:
        return str(getattr(self.ctx, "plugin_id", "plugin"))

    @property
    def config_dir(self) -> Path:
        config_path = getattr(self.ctx, "config_path", None)
        return Path(config_path).parent if config_path is not None else Path.cwd()

    def data_path(self, *parts: str) -> Path:
        base = self.config_dir / "data"
        return base.joinpath(*parts) if parts else base

    @property
    def metadata(self) -> dict[str, Any]:
        value = self.ctx.metadata
        return dict(value) if isinstance(value, Mapping) else {}

    @property
    def bus(self):
        return self.ctx.bus

    @property
    def memory(self):
        if self._memory_client is None:
            from .runtime import MemoryClient

            self._memory_client = MemoryClient(self.ctx)
        return self._memory_client

    @property
    def system_info(self):
        if self._system_info_client is None:
            from .runtime import SystemInfo

            self._system_info_client = SystemInfo(self.ctx)
        return self._system_info_client

    async def run_update(self, **kwargs: Any) -> object:
        return await self.ctx.run_update(**kwargs)

    async def export_push(self, **kwargs: Any) -> object:
        return await self.ctx.export_push(**kwargs)

    async def finish(self, **kwargs: Any) -> Any:
        return await self.ctx.finish(**kwargs)

    def push_message(self, **kwargs: Any) -> object:
        return self.ctx.push_message(**kwargs)

    def include_router(self, router, *, prefix: str = "") -> None:
        super().include_router(router, prefix=prefix)

    def exclude_router(self, router) -> bool:
        return super().exclude_router(router)

    def get_router(self, name: str):
        for router in self._routers:
            router_name = router.name() if callable(getattr(router, "name", None)) else getattr(router, "name", None)
            if router_name == name:
                return router
        return None

    def list_routers(self) -> list[str]:
        names: list[str] = []
        for router in self._routers:
            router_name = router.name() if callable(getattr(router, "name", None)) else getattr(router, "name", None)
            if isinstance(router_name, str):
                names.append(router_name)
        return names

    def _notify_host_comm(self, payload: dict[str, Any]) -> None:
        queue = getattr(self._host_ctx, "message_queue", None)
        if queue is None:
            return
        try:
            queue.put_nowait(payload)
        except Exception:
            logger = getattr(self, "logger", None)
            if logger is not None:
                try:
                    logger.debug("failed to notify host comm: {}", payload.get("type", "unknown"))
                except Exception:
                    pass

    def _notify_static_ui_registered(self, config: dict[str, Any]) -> None:
        self._notify_host_comm({
            "type": "STATIC_UI_REGISTER",
            "plugin_id": self.plugin_id,
            "config": dict(config),
        })

    def _notify_list_actions_updated(self, actions: list[dict[str, Any]]) -> None:
        self._notify_host_comm({
            "type": "LIST_ACTIONS_UPDATE",
            "plugin_id": self.plugin_id,
            "actions": [dict(action) for action in actions],
        })

    def _notify_dynamic_entry_registered(self, entry_id: str, meta: EventMeta, *, enabled: bool = True) -> None:
        meta_dict: dict[str, object] = {
            "id": getattr(meta, "id", entry_id),
            "name": getattr(meta, "name", entry_id),
            "description": getattr(meta, "description", ""),
            "input_schema": dict(getattr(meta, "input_schema", None) or {}),
            "kind": getattr(meta, "kind", "action"),
            "auto_start": bool(getattr(meta, "auto_start", False)),
            "enabled": enabled,
            "metadata": dict(getattr(meta, "metadata", None) or {}),
        }
        llm_fields = getattr(meta, "llm_result_fields", None)
        if llm_fields:
            meta_dict["llm_result_fields"] = list(llm_fields)
        self._notify_host_comm({
            "type": "ENTRY_UPDATE",
            "action": "register",
            "plugin_id": self.plugin_id,
            "entry_id": entry_id,
            "meta": meta_dict,
        })

    def _notify_dynamic_entry_unregistered(self, entry_id: str) -> None:
        self._notify_host_comm({
            "type": "ENTRY_UPDATE",
            "action": "unregister",
            "plugin_id": self.plugin_id,
            "entry_id": entry_id,
        })

    def register_static_ui(self, directory: str = "static", *, index_file: str = "index.html", cache_control: str = "public, max-age=3600") -> bool:
        static_dir = self.config_dir / directory
        index_path = static_dir / index_file
        if not static_dir.is_dir() or not index_path.is_file():
            return False
        self._static_ui_config = {
            "enabled": True,
            "directory": str(static_dir),
            "index_file": index_file,
            "cache_control": cache_control,
            "plugin_id": self.plugin_id,
        }
        self._notify_static_ui_registered(self._static_ui_config)
        return True

    def get_static_ui_config(self) -> dict[str, Any] | None:
        return self._static_ui_config

    def set_list_actions(self, actions: list[Mapping[str, Any]]) -> bool:
        normalized: list[dict[str, Any]] = []
        for index, action in enumerate(actions):
            if not isinstance(action, Mapping):
                raise TypeError(f"list action at index {index} must be a mapping")
            action_id = action.get("id")
            if not isinstance(action_id, str) or not action_id.strip():
                raise ValueError(f"list action at index {index} must define a non-empty 'id'")
            normalized.append({str(key): value for key, value in action.items() if isinstance(key, str)})
        self._list_actions = normalized
        self._notify_list_actions_updated(self._list_actions)
        return True

    def register_list_action(self, action: Mapping[str, Any]) -> bool:
        if not isinstance(action, Mapping):
            raise TypeError("action must be a mapping")
        action_id = action.get("id")
        if not isinstance(action_id, str) or not action_id.strip():
            raise ValueError("action must define a non-empty 'id'")
        action_id = action_id.strip()
        normalized = {str(key): value for key, value in action.items() if isinstance(key, str)}
        normalized["id"] = action_id
        next_actions = [item for item in self._list_actions if item.get("id") != action_id]
        next_actions.append(normalized)
        return self.set_list_actions(next_actions)

    def clear_list_actions(self) -> None:
        self._list_actions = []
        self._notify_list_actions_updated([])

    def get_list_actions(self) -> list[dict[str, Any]]:
        return [dict(action) for action in self._list_actions]

    def register_dynamic_entry(
        self,
        entry_id: str,
        handler,
        name: str = "",
        description: str = "",
        input_schema: dict[str, Any] | None = None,
        kind: str = "action",
        auto_start: bool = False,
        timeout: float | None = None,
        llm_result_fields: list[str] | None = None,
    ) -> bool:
        if not callable(handler):
            raise TypeError("handler must be callable")
        if not isinstance(entry_id, str) or not entry_id.strip():
            raise ValueError("entry_id must be a non-empty string")
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise TypeError("timeout must be a number or None")
            timeout = float(timeout)
        entry_id = entry_id.strip()
        existing_entries = self.collect_entries()
        if entry_id in existing_entries or entry_id in self._dynamic_entries:
            raise EntryConflictError(f"duplicate entry id: {entry_id!r}")
        meta = EventMeta(
            event_type="plugin_entry",
            id=entry_id,
            name=name or entry_id,
            description=description,
            input_schema=input_schema,
            kind=kind,
            auto_start=auto_start,
            timeout=timeout,
            llm_result_fields=llm_result_fields,
            metadata={"dynamic": True, "enabled": True},
        )
        if timeout is not None:
            meta.extra["timeout"] = timeout
        self._dynamic_entries[entry_id] = {"meta": meta, "handler": handler, "enabled": True}
        self._notify_dynamic_entry_registered(entry_id, meta, enabled=True)
        return True

    def unregister_dynamic_entry(self, entry_id: str) -> bool:
        removed = self._dynamic_entries.pop(entry_id, None) is not None
        if removed:
            self._notify_dynamic_entry_unregistered(entry_id)
        return removed

    def enable_entry(self, entry_id: str) -> bool:
        item = self._dynamic_entries.get(entry_id)
        if item is None:
            return False
        item["enabled"] = True
        meta = item.get("meta")
        if meta is not None:
            current = dict(getattr(meta, "metadata", None) or {})
            current["enabled"] = True
            meta.metadata = current
            self._notify_dynamic_entry_registered(entry_id, meta, enabled=True)
        return True

    def disable_entry(self, entry_id: str) -> bool:
        item = self._dynamic_entries.get(entry_id)
        if item is None:
            return False
        item["enabled"] = False
        meta = item.get("meta")
        if meta is not None:
            current = dict(getattr(meta, "metadata", None) or {})
            current["enabled"] = False
            meta.metadata = current
        self._notify_dynamic_entry_unregistered(entry_id)
        return True

    def is_entry_enabled(self, entry_id: str) -> bool | None:
        item = self._dynamic_entries.get(entry_id)
        if item is not None:
            return bool(item.get("enabled", True))
        entries = self.collect_entries()
        if entry_id in entries:
            return True
        return None

    def list_entries(self, include_disabled: bool = False) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        collected_entries = self.collect_entries()
        for entry_id, event_handler in collected_entries.items():
            meta = event_handler.meta
            dynamic_item = self._dynamic_entries.get(entry_id)
            enabled = bool(dynamic_item.get("enabled", True)) if dynamic_item is not None else True
            if enabled is False and not include_disabled:
                continue
            entries.append({
                "id": entry_id,
                "name": getattr(meta, "name", entry_id),
                "description": getattr(meta, "description", ""),
                "event_type": getattr(meta, "event_type", "plugin_entry"),
                "kind": getattr(meta, "kind", "action"),
                "enabled": enabled is not False,
                "dynamic": entry_id in self._dynamic_entries,
                "auto_start": bool(getattr(meta, "auto_start", False)),
                "timeout": getattr(meta, "timeout", None),
                "model_validate": bool(getattr(meta, "model_validate", True)),
                "input_schema": dict(getattr(meta, "input_schema", None) or {}),
                "llm_result_fields": list(getattr(meta, "llm_result_fields", None) or []),
                "llm_result_schema": dict(getattr(meta, "llm_result_schema", None) or {}),
                "metadata": dict(getattr(meta, "metadata", None) or {}),
            })
            seen.add(entry_id)
        if include_disabled:
            for entry_id, item in self._dynamic_entries.items():
                if entry_id in seen:
                    continue
                meta = item.get("meta")
                entries.append({
                    "id": entry_id,
                    "name": getattr(meta, "name", entry_id),
                    "description": getattr(meta, "description", ""),
                    "event_type": getattr(meta, "event_type", "plugin_entry"),
                    "kind": getattr(meta, "kind", "action"),
                    "enabled": bool(item.get("enabled", True)),
                    "dynamic": True,
                    "auto_start": bool(getattr(meta, "auto_start", False)),
                    "timeout": getattr(meta, "timeout", None),
                    "model_validate": bool(getattr(meta, "model_validate", True)),
                    "input_schema": dict(getattr(meta, "input_schema", None) or {}),
                    "llm_result_fields": list(getattr(meta, "llm_result_fields", None) or []),
                    "llm_result_schema": dict(getattr(meta, "llm_result_schema", None) or {}),
                    "metadata": dict(getattr(meta, "metadata", None) or {}),
                })
        return entries

    def collect_entries(self, wrap_with_hooks: bool = True) -> dict[str, EventHandler]:
        entries = super().collect_entries(wrap_with_hooks=wrap_with_hooks)
        for entry_id, item in self._dynamic_entries.items():
            if item.get("enabled", True):
                meta = item.get("meta")
                handler = item.get("handler")
                if meta is not None and callable(handler):
                    entries[entry_id] = EventHandler(meta=meta, handler=handler)
        return entries

    def report_status(self, status: dict[str, Any]) -> None:
        updater = getattr(self.ctx, "update_status", None)
        if callable(updater):
            updater(status)

    # ------------------------------------------------------------------
    # LLM tool registration
    # ------------------------------------------------------------------
    #
    # Plugins can expose model-callable tools two ways:
    #
    # 1. Declaratively via ``@llm_tool`` on an instance method. Auto-
    #    discovered by ``_register_decorated_llm_tools`` during startup.
    #
    # 2. Imperatively via ``self.register_llm_tool(...)`` for tools whose
    #    schema is computed at runtime (e.g. derived from configuration).
    #
    # Both paths funnel through ``_register_llm_tool_internal``: the
    # handler is stored as a dynamic plugin entry under the reserved
    # ``__llm_tool__{name}`` id, and an LLM_TOOL_REGISTER IPC message is
    # emitted so the host can register the tool with main_server.
    # See ``plugin/sdk/plugin/llm_tool.py`` for the on-method metadata
    # shape and ``plugin/server/messaging/llm_tool_registry.py`` for
    # the host-side registration logic.

    def _notify_llm_tool_registered(self, meta: "LlmToolMeta") -> None:
        self._notify_host_comm(meta.to_ipc_payload(plugin_id=self.plugin_id))

    def _notify_llm_tool_unregistered(self, name: str, *, role: str | None = None) -> None:
        self._notify_host_comm({
            "type": "LLM_TOOL_UNREGISTER",
            "plugin_id": self.plugin_id,
            "name": name,
            "role": role,
        })

    def _register_llm_tool_internal(
        self,
        meta: "LlmToolMeta",
        handler: Any,
    ) -> None:
        """Common path used by both the decorator collector and the
        imperative ``register_llm_tool`` instance method.

        Stores the handler as a dynamic plugin entry under the reserved
        id, then notifies the host so main_server gets the
        registration.
        """
        if not callable(handler):
            raise TypeError("LLM tool handler must be callable")
        if meta.name in self._llm_tools:
            raise EntryConflictError(f"duplicate LLM tool name: {meta.name!r}")
        # The dynamic entry path expects an ``input_schema`` (== JSON
        # Schema for arguments) and ``description``. We reuse the same
        # schema we'll send to main_server so a single source of truth
        # drives both surfaces.
        entry_id = entry_id_for_tool(meta.name)
        self.register_dynamic_entry(
            entry_id=entry_id,
            handler=handler,
            name=meta.name,
            description=meta.description,
            input_schema=dict(meta.parameters),
            kind="action",
            timeout=meta.timeout_seconds,
        )
        self._llm_tools[meta.name] = meta
        self._notify_llm_tool_registered(meta)

    def register_llm_tool(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any] | None,
        handler: Any,
        timeout: float = 30.0,
        role: str | None = None,
    ) -> bool:
        """Register an LLM tool at runtime.

        Use this for tools whose schema or set of arguments isn't known
        at class-definition time (e.g. driven by user configuration).
        Otherwise prefer the ``@llm_tool`` decorator which is auto-
        registered during startup.

        Parameters
        ----------
        name:
            Model-visible tool identifier. Validated against the same
            pattern as the decorator (see ``validate_tool_name``).
        description:
            Free-text shown to the LLM. Be specific about behaviour.
        parameters:
            JSON Schema for the tool's arguments. ``None`` means no
            arguments.
        handler:
            Callable invoked when the LLM picks the tool. Receives the
            parsed JSON arguments as kwargs.
        timeout:
            Per-call timeout in seconds. Capped at 300s server-side.
        role:
            Optional role/character to scope to. ``None`` is global.

        Returns ``True`` on success. Raises ``ValueError`` for invalid
        names and ``EntryConflictError`` for duplicates.
        """
        validate_tool_name(name)
        meta = LlmToolMeta(
            name=name,
            description=description or "",
            parameters=dict(parameters) if isinstance(parameters, dict) else {"type": "object", "properties": {}},
            timeout_seconds=float(timeout),
            role=role,
        )
        self._register_llm_tool_internal(meta, handler)
        return True

    def unregister_llm_tool(self, name: str) -> bool:
        """Unregister a previously-registered LLM tool by name.

        Removes the dynamic entry locally and notifies the host so the
        registration is also dropped from main_server. Returns ``True``
        if the tool existed and was removed, ``False`` otherwise.
        """
        meta = self._llm_tools.pop(name, None)
        if meta is None:
            return False
        self.unregister_dynamic_entry(entry_id_for_tool(name))
        self._notify_llm_tool_unregistered(name, role=meta.role)
        return True

    def list_llm_tools(self) -> list[dict[str, Any]]:
        """Return the LLM tools this plugin currently has registered."""
        return [
            {
                "name": meta.name,
                "description": meta.description,
                "parameters": dict(meta.parameters),
                "timeout_seconds": meta.timeout_seconds,
                "role": meta.role,
            }
            for meta in self._llm_tools.values()
        ]

    def _register_decorated_llm_tools(self) -> int:
        """Discover every ``@llm_tool``-decorated method on ``self`` and
        register them as LLM tools.

        Idempotent across calls — the second invocation is a no-op
        because ``self._llm_tools`` already holds the registrations.
        Called automatically from the SDK's startup lifecycle hook.
        Returns the number of tools registered on this call.
        """
        if self._llm_tools_auto_registered:
            return 0
        registered = 0
        for meta, bound in collect_llm_tool_methods(self):
            if meta.name in self._llm_tools:
                continue
            try:
                self._register_llm_tool_internal(meta, bound)
                registered += 1
            except EntryConflictError:
                # Surface but don't crash the plugin — the rest of
                # startup should still proceed.
                logger = getattr(self, "logger", None)
                if logger is not None:
                    try:
                        logger.warning(
                            "Skipping LLM tool '{}' — entry id collision",
                            meta.name,
                        )
                    except Exception:
                        # Logger itself failed — swallow; same idiom as
                        # ``_notify_host_comm`` above.
                        pass
        self._llm_tools_auto_registered = True
        return registered


__all__ = [
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "NekoPluginBase",
    "PluginMeta",
]
