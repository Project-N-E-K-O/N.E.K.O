"""Plugin-facing base facade for SDK v2."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import math
import threading
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any
try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from plugin._types.entry_metadata import (
    ENTRY_PROJECTION_SHAPED_FIELDS,
    check_schema_depth,
    detached_copy,
    unhandled_entry_contract_fields,
)
from plugin.sdk.shared.constants import EVENT_META_ATTR, NEKO_PLUGIN_META_ATTR, NEKO_PLUGIN_TAG
from plugin.sdk.shared.core.base import DEFAULT_PLUGIN_VERSION as _DEFAULT_PLUGIN_VERSION
from plugin.sdk.shared.core.base import NekoPluginBase as _SharedNekoPluginBase
from plugin.sdk.shared.core.base import PluginMeta as _SharedPluginMeta
from plugin.sdk.shared.core.base_runtime import (
    resolve_plugin_cache_dir,
    resolve_plugin_data_dir,
    resolve_plugin_dir,
    resolve_plugin_runtime_config_path,
    resolve_plugin_shared_dir,
    resolve_shared_reads,
    join_shared_path,
    resolve_plugin_storage_dir,
)
from plugin.sdk.shared.core.events import EventHandler, EventMeta
from plugin.sdk.shared.core.types import PushMessageResult
from plugin.sdk.shared.i18n import PluginI18n, load_plugin_i18n_from_meta
from plugin.sdk.shared.models.exceptions import EntryConflictError, ValidationError

from .llm_tool import (
    LlmToolMeta,
    LlmToolRegisterResult,
    LlmToolUnregisterResult,
    build_register_result,
    build_unregister_result,
    collect_llm_tool_methods,
    entry_id_for_tool,
    validate_tool_name,
)

DEFAULT_PLUGIN_VERSION = _DEFAULT_PLUGIN_VERSION


def _grant_waiter(future: asyncio.Future) -> None:
    if not future.done():
        future.set_result(None)


class _CrossLoopLock:
    """FIFO async lock whose waiters may sit on different event loops.

    ``asyncio.Lock`` binds to one loop and is not thread-safe, but plugin
    code runs on several loops at once: entries on the command loop, each
    interval timer on its own thread with its own ``asyncio.run``. State is
    guarded by a ``threading.Lock``; ownership is handed directly to the
    next waiter and its future is resolved on that waiter's own loop.
    """

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._held = False
        # [loop, future, granted]
        self._waiters: collections.deque[list[Any]] = collections.deque()

    async def acquire(self, timeout: float) -> None:
        """Wait at most ``timeout`` seconds; raise ``TimeoutError`` if the
        lock was not obtained in time (nothing is held then)."""
        with self._mutex:
            if not self._held:
                self._held = True
                return
            loop = asyncio.get_running_loop()
            waiter: list[Any] = [loop, loop.create_future(), False]
            self._waiters.append(waiter)
        try:
            async with asyncio.timeout(timeout):
                await waiter[1]
        except BaseException:
            with self._mutex:
                granted = waiter[2]
                if not granted:
                    self._waiters.remove(waiter)
            if granted:
                # Ownership arrived while we were being cancelled or timed
                # out: pass it on instead of keeping it.
                self.release()
            raise

    def release(self) -> None:
        with self._mutex:
            while self._waiters:
                waiter = self._waiters.popleft()
                loop, future, _ = waiter
                waiter[2] = True
                try:
                    loop.call_soon_threadsafe(_grant_waiter, future)
                except RuntimeError:
                    # The waiter's loop is closed; its task is gone.
                    waiter[2] = False
                    continue
                return
            self._held = False


class _LlmToolLockTimeout(Exception):
    """An async LLM tool call waited its whole budget behind earlier calls
    for the same name; nothing was sent to the host."""

# How long register_llm_tool_async / unregister_llm_tool_async wait for the
# host by default. The host's main_server call can spend up to 10s waiting for
# a pooled connection, 2s connecting, 10s writing and 10s reading (see
# ``plugin/server/messaging/llm_tool_registry.py``); stay above that sum so a
# slow but successful registration is not reported as a timeout.
DEFAULT_LLM_TOOL_REQUEST_TIMEOUT = 40.0
# The host's RPC dispatcher caps a request's deadline at 60s
# (``MAX_HANDLER_TIMEOUT_SECONDS`` in ``plugin/core/rpc.py``); a longer
# request_timeout would be cut silently, so it is refused.
_LLM_TOOL_MAX_REQUEST_TIMEOUT = 60.0
# Budget of the unregister a failed register sends to clean up after itself.
_LLM_TOOL_CLEANUP_TIMEOUT = 15.0
# Register errors after which main_server may hold the tool: every role's
# sync failed after it was stored ("rejected"), or the outcome is unknown.
# The other codes mean nothing was stored (bad request, unknown role,
# main_server not reached), where a cleanup would only fail the same way.
_LLM_TOOL_CLEANUP_ERRORS = frozenset({"rejected", "timeout", "host_unavailable"})
# main_server's ToolRegisterRequest bound for a tool's own per-call timeout.
_LLM_TOOL_MAX_TIMEOUT_SECONDS = 300.0
# Host-reported RPC error codes that mean "no answer in time".
_LLM_TOOL_TIMEOUT_CODES = frozenset({"DEADLINE_EXCEEDED", "OPERATION_TIMEOUT", "TRANSPORT_TIMEOUT"})


def _entry_listing(entry_id: str, meta: object, *, enabled: bool, dynamic: bool) -> dict[str, Any]:
    """One ``list_entries`` item; contract fields not shaped here are carried as-is."""
    listing: dict[str, Any] = {
        "id": entry_id,
        "name": getattr(meta, "name", entry_id),
        "description": getattr(meta, "description", ""),
        "event_type": getattr(meta, "event_type", "plugin_entry"),
        "kind": getattr(meta, "kind", "action"),
        "enabled": enabled,
        "dynamic": dynamic,
        "auto_start": bool(getattr(meta, "auto_start", False)),
        "timeout": getattr(meta, "timeout", None),
        "model_validate": bool(getattr(meta, "model_validate", True)),
        "input_schema": dict(getattr(meta, "input_schema", None) or {}),
        "llm_result_fields": list(getattr(meta, "llm_result_fields", None) or []),
        "llm_result_schema": dict(getattr(meta, "llm_result_schema", None) or {}),
        "metadata": dict(getattr(meta, "metadata", None) or {}),
    }
    listing.update(unhandled_entry_contract_fields(meta, ENTRY_PROJECTION_SHAPED_FIELDS))
    return listing


def _dynamic_entry_registration(
    plugin_id: str, entry_id: str, meta: object, *, enabled: bool = True
) -> dict[str, object]:
    """The ``ENTRY_UPDATE`` register message for a dynamic entry (built, not sent)."""
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
    # Forward control fields with registration so the host uses declared budgets and result schemas.
    for name in ("timeout", "model_validate", "persist"):
        value = getattr(meta, name, None)
        if value is not None:
            meta_dict[name] = value
    llm_schema = getattr(meta, "llm_result_schema", None)
    if isinstance(llm_schema, Mapping):
        meta_dict["llm_result_schema"] = dict(llm_schema)
    # Any contract field not shaped above travels as-is, so a field added to
    # ENTRY_CONTRACT_FIELDS reaches the host without editing this payload.
    meta_dict.update(unhandled_entry_contract_fields(meta, ENTRY_PROJECTION_SHAPED_FIELDS))
    return {
        "type": "ENTRY_UPDATE",
        "action": "register",
        "plugin_id": plugin_id,
        "entry_id": entry_id,
        "meta": meta_dict,
    }


class PluginMeta(_SharedPluginMeta):
    """Plugin-facing metadata model."""


class NekoPluginBase(_SharedNekoPluginBase):
    """Plugin-facing base class with convenience helpers."""

    def __init__(self, ctx):
        super().__init__(ctx)
        # Promote plugin-facing helper instead of exposing the shared minimal contract.
        from .runtime import Plugins

        self.plugins = Plugins(self.ctx)
        self._system_info_client = None
        self.i18n = self._load_plugin_i18n()
        self._static_ui_config: dict[str, Any] | None = None
        self._list_actions: list[dict[str, Any]] = []
        self._dynamic_entries: dict[str, dict[str, Any]] = {}
        # Guards the duplicate check + insert of dynamic entries: plugin code
        # runs on several threads (command loop, timer threads), and readers
        # iterate over snapshots of the dict instead of the live view.
        self._dynamic_entries_lock = threading.Lock()
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
        # name -> [lock, users]: serialises register_llm_tool_async /
        # unregister_llm_tool_async for one name from the local read to the
        # local update after the host answered. Dropped when unused.
        self._llm_tool_locks: dict[str, list[Any]] = {}
        self._llm_tool_locks_mutex = threading.Lock()
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
    def plugin_dir(self) -> Path:
        """Return the installed plugin payload directory."""
        return resolve_plugin_dir(self.ctx)

    @property
    def config_dir(self) -> Path:
        """Compatibility alias for :attr:`plugin_dir`."""
        return self.plugin_dir

    @property
    def storage_dir(self) -> Path:
        """Return the user storage directory assigned to this plugin."""
        return resolve_plugin_storage_dir(self.ctx)

    @property
    def runtime_config_path(self) -> Path:
        """Return the plugin's persistent runtime configuration path."""
        return resolve_plugin_runtime_config_path(self.ctx)

    def data_path(self, *parts: str) -> Path:
        base = resolve_plugin_data_dir(self.ctx)
        return base.joinpath(*parts) if parts else base

    def cache_path(self, *parts: str) -> Path:
        base = resolve_plugin_cache_dir(self.ctx)
        return base.joinpath(*parts) if parts else base

    def shared_path(self, *parts: str) -> Path:
        """Return a path in the directory this plugin publishes for other plugins.

        ``<data root>/plugin_shared/<this plugin's id>/``. This plugin writes
        there; other plugins read it with :meth:`shared_path_of`. Unlike the
        private state it is kept when the plugin is removed. The directory is
        not created by this call.
        """
        path = join_shared_path(resolve_plugin_shared_dir(self.plugin_id), parts)
        if path is None:
            raise ValidationError(
                "a shared path must stay inside the plugin's shared directory",
                code="shared_bad_path",
                plugin_id=self.plugin_id,
            )
        return path

    def shared_path_of(self, plugin_id: str, *parts: str) -> Path:
        """Return a path in another plugin's shared directory, for reading.

        The manifest must name that plugin under ``[plugin.shared] reads``;
        otherwise :class:`ValidationError` is raised. The files belong to the
        plugin that published them: read them, do not write them. Nothing
        enforces that (plugins are not sandboxed from each other); it is an
        agreement between plugins, not a security boundary.
        """
        wanted = str(plugin_id or "").strip()
        if not wanted or wanted not in resolve_shared_reads(self.ctx):
            raise ValidationError(
                f"plugin {self.plugin_id!r} does not declare that it reads the shared files of {wanted!r}; "
                'add it to [plugin.shared] reads in plugin.toml',
                code="shared_not_declared",
                plugin_id=self.plugin_id,
            )
        path = join_shared_path(resolve_plugin_shared_dir(wanted), parts)
        if path is None:
            raise ValidationError(
                "a shared path must stay inside the publishing plugin's shared directory",
                code="shared_bad_path",
                plugin_id=self.plugin_id,
            )
        return path

    @property
    def metadata(self) -> dict[str, Any]:
        value = self.ctx.metadata
        return dict(value) if isinstance(value, Mapping) else {}

    @property
    def bus(self):
        return self.ctx.bus

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
        """Mark the current task done and hand a summary back to the host.

        Role-aware text contract: ``summary`` / ``detail`` (and any text in
        ``parts``) MAY contain ``{MASTER_NAME}`` / ``{LANLAN_NAME}``
        placeholders. The host expands them at the LLM-injection boundary
        (and at the verbatim ``direct_reply`` exit), per session. Plugin code
        can't pick the right name itself — visibility filtering decides which
        ``LLMSessionManager`` receives the text, so substitution has to be
        host-side.

        Use this for character-aware text like
        ``\"Immediately describe the latest screen to {MASTER_NAME}...\"`` instead of hardcoded
        ``\"user\"`` / ``\"master\"`` / ``\"owner\"``, which feel generic and
        misbehave on multi-character setups. See docs/plugins/push-message.md
        ("Role-aware text") for details.
        """
        return await self.ctx.finish(**kwargs)

    def push_message(self, **kwargs: Any) -> PushMessageResult:
        """Stream a message from the plugin into the dialog channel.

        Role-aware text contract: ``text`` / ``summary`` / ``detail`` (and
        any text in ``parts``) MAY contain ``{MASTER_NAME}`` /
        ``{LANLAN_NAME}`` placeholders; the host substitutes them at the
        injection boundary, per session. See :meth:`finish` for the rationale
        and docs/plugins/push-message.md ("Role-aware text") for the
        full guide.

        The returned receipt only reports immediate local submission.  It is
        not a host-consumption, generation, or playback acknowledgement.
        """
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
        self._notify_host_comm(_dynamic_entry_registration(self.plugin_id, entry_id, meta, enabled=enabled))

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
        output_schema: dict[str, Any] | None = None,
    ) -> bool:
        if not callable(handler):
            raise TypeError("handler must be callable")
        if output_schema is not None and not isinstance(output_schema, Mapping):
            raise TypeError("output_schema must be a JSON schema mapping or None")
        if output_schema is not None:
            check_schema_depth(output_schema, "output_schema")
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
            # Nested levels too: a caller reusing its dict cannot change the contract.
            output_schema=detached_copy(output_schema) if output_schema is not None else None,
            metadata={"dynamic": True, "enabled": True},
        )
        if timeout is not None:
            meta.extra["timeout"] = timeout
        # Build the host message before touching local state: if building it
        # fails, nothing is half-registered and the same id can be retried.
        registration = _dynamic_entry_registration(self.plugin_id, entry_id, meta, enabled=True)
        with self._dynamic_entries_lock:
            # Checked again under the lock: another thread may have taken the
            # id since the check above.
            if entry_id in self._dynamic_entries:
                raise EntryConflictError(f"duplicate entry id: {entry_id!r}")
            self._dynamic_entries[entry_id] = {"meta": meta, "handler": handler, "enabled": True}
        self._notify_host_comm(registration)
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
            entries.append(_entry_listing(
                entry_id,
                meta,
                enabled=enabled is not False,
                dynamic=entry_id in self._dynamic_entries,
            ))
            seen.add(entry_id)
        if include_disabled:
            for entry_id, item in list(self._dynamic_entries.items()):
                if entry_id in seen:
                    continue
                entries.append(_entry_listing(
                    entry_id,
                    item.get("meta"),
                    enabled=bool(item.get("enabled", True)),
                    dynamic=True,
                ))
        return entries

    def collect_entries(self, wrap_with_hooks: bool = True) -> dict[str, EventHandler]:
        entries = super().collect_entries(wrap_with_hooks=wrap_with_hooks)
        # Snapshot: another thread may add or remove an entry meanwhile.
        for entry_id, item in list(self._dynamic_entries.items()):
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

    def _claim_llm_tool_locally(self, meta: "LlmToolMeta", handler: Any) -> None:
        """Store the handler as a dynamic plugin entry under the reserved
        id and record ``meta`` in ``_llm_tools``. No host notification."""
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

    def _release_llm_tool_locally(self, meta: "LlmToolMeta") -> bool:
        """Drop ``meta`` from ``_llm_tools`` and remove its dynamic entry,
        but only if that exact registration is still current (a
        concurrent unregister + re-register may have replaced it while an
        async request was in flight). No host notification."""
        if self._llm_tools.get(meta.name) is not meta:
            return False
        del self._llm_tools[meta.name]
        self.unregister_dynamic_entry(entry_id_for_tool(meta.name))
        return True

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
        self._claim_llm_tool_locally(meta, handler)
        self._notify_llm_tool_registered(meta)

    @staticmethod
    def _build_llm_tool_meta(
        *,
        name: str,
        description: str,
        parameters: dict[str, Any] | None,
        timeout: float,
        role: str | None,
        reply_tail: bool,
    ) -> "LlmToolMeta":
        validate_tool_name(name)
        return LlmToolMeta(
            name=name,
            description=description or "",
            parameters=dict(parameters) if isinstance(parameters, dict) else {"type": "object", "properties": {}},
            timeout_seconds=float(timeout),
            role=role,
            reply_tail=reply_tail,
        )

    @contextlib.asynccontextmanager
    async def _llm_tool_name_lock(self, name: str, request_timeout: float) -> AsyncIterator[float]:
        """Hold the per-name lock of the async register/unregister calls and
        yield what is left of ``request_timeout`` once it is held.

        Without it, two async calls for one name can interleave at the host
        (both requests are in flight at once) and leave the plugin and
        main_server disagreeing, e.g. ``gather(register, unregister)`` with
        both answering ``ok=True`` while main_server keeps the tool and the
        plugin has no handler. Calls may come from different event loops
        (command loop, timer threads), hence ``_CrossLoopLock``. The wait
        counts against ``request_timeout``; when the budget runs out first,
        ``_LlmToolLockTimeout`` is raised and nothing has been sent. The
        sync calls are not serialised by it.
        """
        started = time.monotonic()
        with self._llm_tool_locks_mutex:
            entry = self._llm_tool_locks.get(name)
            if entry is None:
                entry = [_CrossLoopLock(), 0]
                self._llm_tool_locks[name] = entry
            entry[1] += 1
        try:
            try:
                await entry[0].acquire(request_timeout)
            except TimeoutError:
                raise _LlmToolLockTimeout() from None
            try:
                remaining = request_timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise _LlmToolLockTimeout()
                yield remaining
            finally:
                entry[0].release()
        finally:
            with self._llm_tool_locks_mutex:
                entry[1] -= 1
                if entry[1] == 0 and self._llm_tool_locks.get(name) is entry:
                    del self._llm_tool_locks[name]

    @staticmethod
    def _llm_tool_lock_timeout_result(build_result: Any, name: str, request_timeout: float) -> Any:
        return build_result(
            {}, ok=False, error="timeout",
            message=(
                f"waited {request_timeout}s behind earlier calls for {name!r}; "
                "nothing was sent to the host"
            ),
        )

    @staticmethod
    def _validate_async_llm_tool_name(name: str) -> None:
        validate_tool_name(name)
        # "." / ".." pass the shared rule but are normalised away in the
        # callback URL path, so such a tool could never be called. Only the
        # async calls refuse them; the sync calls keep their behaviour.
        if set(name) == {"."}:
            raise ValueError(f"LLM tool name {name!r} must not consist only of dots")

    @staticmethod
    def _validate_llm_tool_timeout(timeout: float) -> None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= _LLM_TOOL_MAX_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"timeout must be a finite number in (0, {_LLM_TOOL_MAX_TIMEOUT_SECONDS:g}] seconds"
            )

    @staticmethod
    def _validate_llm_tool_request_timeout(request_timeout: float) -> float:
        if (
            isinstance(request_timeout, bool)
            or not isinstance(request_timeout, (int, float))
            or not (0 < float(request_timeout) <= _LLM_TOOL_MAX_REQUEST_TIMEOUT)
        ):
            raise ValueError(
                f"request_timeout must be a number in (0, {_LLM_TOOL_MAX_REQUEST_TIMEOUT:g}] seconds"
            )
        return float(request_timeout)

    async def _request_llm_tool_change(
        self,
        *,
        request_type: str,
        payload: dict[str, Any],
        request_timeout: float,
        build_result: Any,
    ) -> Any:
        """Send one LLM tool request to the host and normalise its reply
        with ``build_result`` (``build_register_result`` or
        ``build_unregister_result``). Never raises for transport
        problems: they come back as ``ok=False`` with an ``error`` code."""
        sender = getattr(self._host_ctx, "_send_request_and_wait_async", None)
        if not callable(sender):
            return build_result(
                {}, ok=False, error="host_unavailable",
                message="the host does not provide asynchronous control requests",
            )
        try:
            reply = await sender(
                method_name=request_type.lower(),
                request_type=request_type,
                request_data=payload,
                timeout=request_timeout,
                wrap_result=False,
            )
        except TimeoutError:
            return build_result(
                {}, ok=False, error="timeout",
                message=f"no answer from the host within {request_timeout}s",
            )
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code in _LLM_TOOL_TIMEOUT_CODES:
                return build_result(
                    {}, ok=False, error="timeout",
                    message=f"no answer from the host within {request_timeout}s ({code})",
                )
            detail = str(exc)
            return build_result(
                {}, ok=False, error="host_unavailable",
                message=f"{type(exc).__name__}"
                + (f" ({code})" if isinstance(code, str) else "")
                + (f": {detail}" if detail else ""),
            )
        if not isinstance(reply, Mapping):
            return build_result({}, ok=False, error="host_unavailable", message="malformed host reply")
        return build_result(
            reply,
            ok=reply.get("ok") is True,
            error=reply.get("error"),
            message=reply.get("message"),
        )

    def register_llm_tool(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any] | None,
        handler: Any,
        timeout: float = 30.0,
        role: str | None = None,
        reply_tail: bool = False,
    ) -> bool:
        """Register an LLM tool at runtime.

        Use this for tools whose schema or set of arguments isn't known
        at class-definition time (e.g. driven by user configuration).
        Otherwise prefer the ``@llm_tool`` decorator which is auto-
        registered during startup.

        This is fire-and-forget: ``True`` means the tool was recorded
        locally and the host was notified, not that main_server accepted
        it. Use :meth:`register_llm_tool_async` to get the real outcome.

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
        meta = self._build_llm_tool_meta(
            name=name, description=description, parameters=parameters,
            timeout=timeout, role=role, reply_tail=reply_tail,
        )
        self._register_llm_tool_internal(meta, handler)
        return True

    def unregister_llm_tool(self, name: str) -> bool:
        """Unregister a previously-registered LLM tool by name.

        Removes the dynamic entry locally and notifies the host so the
        registration is also dropped from main_server. Returns ``True``
        if the tool existed and was removed, ``False`` otherwise.

        Fire-and-forget like :meth:`register_llm_tool`; use
        :meth:`unregister_llm_tool_async` to get the real outcome.
        """
        meta = self._llm_tools.get(name)
        if meta is None or not self._release_llm_tool_locally(meta):
            return False
        self._notify_llm_tool_unregistered(name, role=meta.role)
        return True

    async def register_llm_tool_async(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any] | None,
        handler: Any,
        timeout: float = 30.0,
        role: str | None = None,
        reply_tail: bool = False,
        request_timeout: float = DEFAULT_LLM_TOOL_REQUEST_TIMEOUT,
    ) -> LlmToolRegisterResult:
        """Register an LLM tool and wait for main_server's real answer.

        Same arguments as :meth:`register_llm_tool`, plus
        ``request_timeout`` (seconds to wait for the host). The host
        registers the tool with main_server and returns what it answered;
        see :class:`LlmToolRegisterResult` for the fields and error codes.

        The handler is installed locally before the request is sent (the
        model may call the tool as soon as main_server has it). When the
        result has ``ok=False`` the local registration is rolled back, so
        ``list_llm_tools()`` and the host stay in step and the same name
        can be registered again. When main_server may have stored the tool
        anyway (``rejected``: every role's sync failed after it was stored;
        ``timeout`` / ``host_unavailable``: outcome unknown), the SDK then
        unregisters it once, still in order with this name's other async
        calls, and reports that in ``cleanup`` (up to 15s more than
        ``request_timeout``). If ``cleanup`` is not ``ok``, call
        :meth:`unregister_llm_tool_async` later. After a ``timeout`` the
        cleanup is best effort: main_server may still be processing the
        register and store it after the cleanup ran, so ``removed=False``
        does not prove nothing is left; call
        :meth:`unregister_llm_tool_async` again a little later when that
        matters. Likewise, if the cleanup itself timed out it may still be
        running on main_server, and a register of the same name sent right
        after could be removed by it: wait a little before retrying.
        Stopping the plugin also tries to clear the plugin's tools, but
        only best effort. A cancelled call rolls back locally but sends no
        cleanup.

        Async calls for the same name run one at a time, in call order
        (each waits for the previous one's answer, also across event
        loops such as timer threads; ``request_timeout`` covers that wait
        plus this call's own request), so for example
        ``gather(register_llm_tool_async("t", ...),
        unregister_llm_tool_async("t"))`` ends with ``t`` gone everywhere
        (the ordering covers requests that got an answer; after a register
        ``timeout`` see ``cleanup`` above). The sync calls do not take part in that ordering: use one style
        per tool name.

        Raises ``ValueError`` for invalid or dot-only names, a
        ``request_timeout`` outside (0, 60] (the host's limit) or a
        ``timeout`` outside (0, 300] (main_server's bound),
        ``TypeError`` for a non-callable handler and
        ``EntryConflictError`` when the name is already registered
        locally -- like :meth:`register_llm_tool`. Host and main_server
        failures never raise; they come back in the result.
        """
        request_timeout = self._validate_llm_tool_request_timeout(request_timeout)
        self._validate_llm_tool_timeout(timeout)
        self._validate_async_llm_tool_name(name)
        meta = self._build_llm_tool_meta(
            name=name, description=description, parameters=parameters,
            timeout=timeout, role=role, reply_tail=reply_tail,
        )
        try:
            async with self._llm_tool_name_lock(meta.name, request_timeout) as remaining:
                self._claim_llm_tool_locally(meta, handler)
                try:
                    result = await self._request_llm_tool_change(
                        request_type="LLM_TOOL_REGISTER_REQUEST",
                        payload=meta.to_ipc_payload(plugin_id=self.plugin_id),
                        request_timeout=remaining,
                        build_result=build_register_result,
                    )
                except BaseException:
                    # Cancelled while waiting: the outcome is unknown, so do not
                    # leave a local tool the caller never got a result for.
                    self._release_llm_tool_locally(meta)
                    raise
                if not result["ok"]:
                    self._release_llm_tool_locally(meta)
                    if result["error"] in _LLM_TOOL_CLEANUP_ERRORS:
                        result["cleanup"] = await self._clean_up_failed_llm_tool_register(meta)
                return result
        except _LlmToolLockTimeout:
            return self._llm_tool_lock_timeout_result(build_register_result, meta.name, request_timeout)

    async def _clean_up_failed_llm_tool_register(self, meta: "LlmToolMeta") -> LlmToolUnregisterResult:
        """Unregister a tool whose register reported failure, still under
        the name lock.

        A failed register is not always a no-op on main_server: it stores
        the tool before syncing it to each role and keeps it when every
        role's sync fails, and a timeout says nothing about what was
        applied. The plugin has just dropped its handler, so a stored copy
        would be a tool the model sees but cannot call. The unregister
        carries ``expected_source=plugin:{id}`` on the host, so it never
        touches another source's tool, and holding the lock means no later
        same-name async call of this plugin can slip in between.
        """
        return await self._request_llm_tool_change(
            request_type="LLM_TOOL_UNREGISTER_REQUEST",
            payload={"plugin_id": self.plugin_id, "name": meta.name, "role": meta.role},
            request_timeout=_LLM_TOOL_CLEANUP_TIMEOUT,
            build_result=build_unregister_result,
        )

    async def unregister_llm_tool_async(
        self,
        name: str,
        *,
        request_timeout: float = DEFAULT_LLM_TOOL_REQUEST_TIMEOUT,
    ) -> LlmToolUnregisterResult:
        """Unregister an LLM tool and wait for main_server's real answer.

        The host removes the tool from main_server on behalf of this
        plugin only (``expected_source=plugin:{plugin_id}``): a name
        owned by another source is left alone and reported in
        ``refused_roles``. See :class:`LlmToolUnregisterResult` for the
        fields and error codes.

        The local handler is removed only when the result has
        ``ok=True``. On failure (some roles failed, timeout, host or
        main_server unavailable) the tool stays registered locally, as it
        does on the host, so the model's calls still reach a handler and
        calling this again retries.

        A name that is not registered locally is still sent to the host,
        for every role: after a register ``timeout`` main_server may hold
        the tool although the plugin rolled it back, and this is how the
        plugin removes it. If main_server removed nothing of this
        plugin's, the result is ``ok=False, error="not_registered"``.

        Async calls for the same name run one at a time, in call order;
        see :meth:`register_llm_tool_async`.

        Raises ``ValueError`` for an invalid name or ``request_timeout``.
        """
        request_timeout = self._validate_llm_tool_request_timeout(request_timeout)
        self._validate_async_llm_tool_name(name)
        try:
            async with self._llm_tool_name_lock(name, request_timeout) as remaining:
                meta = self._llm_tools.get(name)
                result = await self._request_llm_tool_change(
                    request_type="LLM_TOOL_UNREGISTER_REQUEST",
                    # Without a local record the role is unknown: all roles.
                    payload={"plugin_id": self.plugin_id, "name": name, "role": meta.role if meta else None},
                    request_timeout=remaining,
                    build_result=build_unregister_result,
                )
                if meta is not None:
                    if result["ok"]:
                        self._release_llm_tool_locally(meta)
                    return result
                if result["ok"] and not result["removed"]:
                    return build_unregister_result(
                        result, ok=False, error="not_registered",
                        message=f"no LLM tool named {name!r} is registered by this plugin",
                    )
                return result
        except _LlmToolLockTimeout:
            return self._llm_tool_lock_timeout_result(build_unregister_result, name, request_timeout)

    def list_llm_tools(self) -> list[dict[str, Any]]:
        """Return the LLM tools this plugin currently has registered."""
        return [
            {
                "name": meta.name,
                "description": meta.description,
                "parameters": dict(meta.parameters),
                "timeout_seconds": meta.timeout_seconds,
                "role": meta.role,
                **({"reply_tail": True} if meta.reply_tail else {}),
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
