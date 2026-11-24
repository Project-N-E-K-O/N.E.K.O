"""Base plugin runtime for SDK v2 shared core."""

from __future__ import annotations

from dataclasses import dataclass, field
import functools
import inspect
import types
from pathlib import Path
from typing import Protocol, runtime_checkable

from plugin.sdk.shared.core.base_runtime import (
    resolve_db_config,
    resolve_effective_config,
    resolve_plugin_data_dir,
    resolve_state_backend,
    resolve_store_enabled,
    setup_plugin_file_logging,
)
from plugin.sdk.shared.constants import EVENT_META_ATTR, NEKO_PLUGIN_META_ATTR, NEKO_PLUGIN_TAG, SDK_VERSION
from plugin.sdk.shared.permissions.narrowing import (
    PLUGIN_ENTRY,
    Component,
    component_label,
    read_declared_permission_ids,
    resolve_component_permissions,
    static_entry_hooks,
    with_permissions,
)
from plugin.sdk.shared.logging import LogLevel, LoggerLike, get_plugin_logger, setup_sdk_logging
from .context import ensure_sdk_context
from .events import EventHandler, EventMeta
from .types import InputSchema, PluginContextProtocol, RouterProtocol


DEFAULT_PLUGIN_VERSION = "0.0.0"


_METHOD_LOOKUPS = (
    types.FunctionType,
    functools.partialmethod,
    functools.singledispatchmethod,
    staticmethod,
    classmethod,
)


def is_computed_attribute(instance: object, name: str) -> bool:
    """Whether reading ``name`` would run descriptor code.

    Judged on the class attribute, so a cached property whose value already
    sits in the instance ``__dict__`` still counts. Functions and the method
    wrappers in ``_METHOD_LOOKUPS`` are plain method lookups (a
    ``partialmethod`` or ``classmethod`` is judged by what it wraps); any other
    descriptor on the class (property, cached properties, lazy loaders, data
    descriptors) is computed and is not evaluated just because its name was
    called. Instance attributes and names a class ``__getattr__`` provides
    are not computed.
    """
    try:
        static = inspect.getattr_static(type(instance), name)
    except AttributeError:
        return False
    seen: set[int] = set()
    while id(static) not in seen:
        seen.add(id(static))
        # partialmethod and classmethod hand the attribute lookup on to what
        # they wrap (a property inside one would be evaluated): look inside.
        if isinstance(static, functools.partialmethod):
            static = static.func
        elif isinstance(static, classmethod):
            static = static.__func__
        else:
            break
    if isinstance(static, _METHOD_LOOKUPS) or callable(static):
        # A callable descriptor is a method decorator implemented as a class
        # (e.g. async_lru's alru_cache): a method lookup, not a computed value.
        return False
    return hasattr(type(static), "__get__")


@runtime_checkable
class _EventMetaLike(Protocol):
    id: str


def _router_event_handler(key: str, handler: object) -> EventHandler | None:
    if isinstance(handler, EventHandler):
        return handler
    if not callable(handler):
        return None
    meta = getattr(handler, EVENT_META_ATTR, None)
    if not isinstance(meta, _EventMetaLike):
        meta = EventMeta(event_type="plugin_entry", id=key, name=key)
    return EventHandler(meta=meta, handler=handler)


def _is_plugin_entry(handler: EventHandler) -> bool:
    return (getattr(handler.meta, "event_type", None) or "plugin_entry") == "plugin_entry"


_UNREAD = object()


def _router_handler_replaces(existing: EventHandler, incoming: EventHandler, *, existing_from_class: bool) -> bool:
    """Decide a router id clash the way the host's scan_static_metadata does.

    All event types share one id table here, while the host keys plugin_entry
    separately and always registers one when any side declares it, so a
    plugin_entry wins over other event types. Within one event type the plugin
    class wins over routers, and a later router wins over an earlier one.
    """
    existing_is_entry = _is_plugin_entry(existing)
    if existing_is_entry != _is_plugin_entry(incoming):
        return not existing_is_entry
    return not existing_from_class


@dataclass(slots=True)
class PluginMeta:
    id: str
    name: str
    version: str = DEFAULT_PLUGIN_VERSION
    sdk_version: str = SDK_VERSION
    description: str = ""
    short_description: str = ""  # Short description (<300 characters), used by the agent's two-stage plugin selection.
    keywords: list[str] = field(default_factory=list)  # List of keyword regular expressions.
    passive: bool = False  # Passive plugin; excluded from proactive agent dispatch.
    sdk_recommended: str | None = None
    sdk_supported: str | None = None
    sdk_untested: str | None = None
    sdk_conflicts: list[str] = field(default_factory=list)


class NekoPluginBase:
    """Async-first plugin base.

    The class keeps a synchronous ergonomic surface and delegates transport
    operations to async APIs under `config/plugins/store/db`.
    """

    __freezable__: list[str] = []
    __persist_mode__: str = "off"

    def __init__(self, ctx: PluginContextProtocol):
        self._host_ctx = ctx
        self.ctx = ensure_sdk_context(ctx)
        from plugin.sdk.shared.core.config import PluginConfig
        from plugin.sdk.shared.core.plugins import Plugins

        self.config = PluginConfig(self.ctx)
        self.plugins = Plugins(self.ctx)
        self._routers: list[RouterProtocol] = []
        self.logger: LoggerLike = self.ctx.logger or self.get_logger()
        self.sdk_logger: LoggerLike = self.logger

        from plugin.sdk.shared.storage.database import PluginDatabase
        from plugin.sdk.shared.storage.state import PluginStatePersistence
        from plugin.sdk.shared.storage.store import PluginStore

        plugin_dir = resolve_plugin_data_dir(self.ctx)
        effective_cfg = resolve_effective_config(self.ctx)
        store_enabled = resolve_store_enabled(effective_cfg)
        db_enabled, db_name = resolve_db_config(effective_cfg)
        state_backend = resolve_state_backend(effective_cfg)

        plugin_id = self.ctx.plugin_id
        self.store = PluginStore(plugin_id=plugin_id, plugin_dir=plugin_dir, logger=self.logger, enabled=store_enabled)
        self.db = PluginDatabase(plugin_id=plugin_id, plugin_dir=plugin_dir, logger=self.logger, enabled=db_enabled, db_name=db_name)
        self.state = PluginStatePersistence(plugin_id=plugin_id, plugin_dir=plugin_dir, logger=self.logger, backend=state_backend)
        self._state_persistence = self.state

    def refresh_runtime_config(self, effective_config: dict[str, object] | None = None) -> None:
        """Refresh SDK runtime helpers after the host effective config changes."""
        cfg = effective_config if isinstance(effective_config, dict) else resolve_effective_config(self.ctx)
        store_enabled = resolve_store_enabled(cfg)
        db_enabled, db_name = resolve_db_config(cfg)
        state_backend = resolve_state_backend(cfg)
        configure_db_name = getattr(self.db, "configure_database_name", None)
        normalize_state_backend = getattr(type(self.state), "normalize_backend", None)
        if callable(normalize_state_backend):
            state_backend = normalize_state_backend(state_backend)

        if callable(configure_db_name):
            configure_db_name(db_name)
        elif hasattr(self.db, "db_name"):
            self.db.db_name = db_name
        self.store.enabled = store_enabled
        self.db.enabled = db_enabled
        self.state.backend = state_backend

    def get_input_schema(self) -> InputSchema:
        schema = getattr(self, "input_schema", None)
        if isinstance(schema, dict):
            return schema
        return {}

    def _declared_permission_ids(self) -> frozenset[str] | None:
        """Permission ids plugin.toml declares (read once); ``None`` when unknown.

        Read from the manifest the plugin was loaded from (``ctx.config_path``),
        never from runtime configuration overrides.
        """
        cached = getattr(self, "_declared_permissions_cache", _UNREAD)
        if cached is _UNREAD:
            cached = read_declared_permission_ids(getattr(self.ctx, "config_path", None))
            self._declared_permissions_cache = cached
        return cached  # type: ignore[return-value]

    def _entry_hooks(self) -> list:
        """The class's entry hooks, scanned once (hooks are declared on the class)."""
        cached = self.__dict__.get("_entry_hooks_cache")
        if cached is None:
            cached = static_entry_hooks(type(self))
            self.__dict__["_entry_hooks_cache"] = cached
        return cached

    def _entry_state(self, entry_id: str) -> str:
        """Where the narrowing check leaves ``entry_id`` right now, for the executor.

        ``"registered"``: ``collect_entries`` currently lists it (a valid
        entry registered after an earlier refusal of the same id counts).
        ``"closed"``: the check refused it, or an entry hook with an
        undeclared permission targets it (also an entry only the manifest
        declares, which the executor reaches by attribute name).
        ``"unchecked"``: nothing to say; a plugin that narrows nothing always
        gets this, without collecting anything.
        """
        hooks = self._entry_hooks()
        narrowing_hooks = any(hook.permissions is not None for hook in hooks)
        # ``_narrowing_refused`` is only a hint that something was refused at
        # some point; the decision below is made on the components as they
        # are now (a router may have been excluded, an id registered again).
        if not getattr(self, "_narrowing_refused", None) and not narrowing_hooks:
            return "unchecked"
        try:
            current = self.collect_entries().get(entry_id)
            gathered = self._gather_entries()
        except Exception:
            # Collecting fails (e.g. an id clash inside a router): what the
            # last successful check refused stands in for the components, and
            # the entry hook check below, which needs no collection, still runs.
            refused_now = set(getattr(self, "_narrowing_refused", None) or ())
        else:
            if current is not None and (getattr(current.meta, "event_type", None) or PLUGIN_ENTRY) == PLUGIN_ENTRY:
                return "registered"
            kept = self._apply_permission_narrowing(dict(gathered))
            refused_now = {
                (str(getattr(item.meta, "event_type", None) or PLUGIN_ENTRY), key)
                for key, item in gathered.items()
                if key not in kept
            }
        if (PLUGIN_ENTRY, entry_id) in refused_now:
            return "closed"
        # The executor's attribute fallback resolves a method by its name,
        # which need not be its entry id: a refused component stays refused
        # under its method name too. The executor resolves ``getattr(id)``
        # only; ``entry_<id>`` is still checked here (the first truthy one
        # closes), which only ever closes more.
        for name in (entry_id, f"entry_{entry_id}"):
            # A property is not evaluated here (nor resolved by the executor).
            if is_computed_attribute(self, name):
                continue
            candidate = getattr(self, name, None)
            if not candidate:
                continue
            meta = getattr(candidate, EVENT_META_ATTR, None)
            if meta is not None and (
                str(getattr(meta, "event_type", None) or PLUGIN_ENTRY),
                str(getattr(meta, "id", "")),
            ) in refused_now:
                return "closed"
            break
        if not narrowing_hooks:
            return "unchecked"
        component = Component(
            label=component_label(PLUGIN_ENTRY, entry_id), event_type=PLUGIN_ENTRY, entry_id=entry_id, permissions=None,
        )
        resolution = resolve_component_permissions(
            {entry_id: component}, hooks, self._declared_permission_ids()
        )[entry_id]
        if resolution.registered:
            return "unchecked"
        # Logged once, and only for an id that names something callable: a
        # "*" hook would otherwise log every unknown id a caller tries.
        has_target = any(
            not is_computed_attribute(self, name) and callable(getattr(self, name, None))
            for name in (entry_id, f"entry_{entry_id}")
        )
        reported: set[object] = self.__dict__.setdefault("_narrowing_reported", set())
        if has_target and (PLUGIN_ENTRY, entry_id, "unlisted") not in reported:
            reported.add((PLUGIN_ENTRY, entry_id, "unlisted"))
            for message in resolution.errors:
                self.logger.error("Component not registered: {}", message)
        return "closed"

    def _resolve_dynamic_entry_permissions(self, entry_id: str, narrowed: object) -> object:
        """Registration check for an entry added at runtime; returns the permissions to record.

        Same rules as ``collect_entries``: the narrowing must fit plugin.toml
        and the entry hooks targeting the entry join it. Raises
        ``PermissionDeniedError`` (reason ``not_declared``) when it does not fit.
        """
        hooks = self._entry_hooks()
        if narrowed is None and all(hook.permissions is None for hook in hooks):
            return None
        component = Component(
            label=component_label(PLUGIN_ENTRY, entry_id),
            event_type=PLUGIN_ENTRY,
            entry_id=entry_id,
            permissions=narrowed,  # type: ignore[arg-type]
        )
        declared = self._declared_permission_ids()
        resolution = resolve_component_permissions({entry_id: component}, hooks, declared)[entry_id]
        if resolution.registered:
            return resolution.permissions
        from plugin.sdk.shared.models.exceptions import PermissionDeniedError

        listed = list(narrowed or ()) + [
            permission for hook in hooks if hook.applies_to(component) for permission in (hook.permissions or ())
        ]
        outside = [permission for permission in listed if declared is not None and permission not in declared]
        denied = PermissionDeniedError(
            "; ".join(resolution.errors), permission=outside[0] if outside else None, reason="not_declared",
        )
        self.logger.error("Entry not registered: {}", str(denied))
        raise denied

    @property
    def _components_generation(self) -> int:
        """Grows whenever the registered components may have changed.

        The executor maps entries once and serves later calls from that map;
        a change it did not cause (a router included or excluded, an entry
        added or removed at runtime) makes it rebuild the map first.
        """
        return int(self.__dict__.get("_components_generation_value", 0))

    def _components_changed(self) -> None:
        self.__dict__["_components_generation_value"] = self._components_generation + 1

    def include_router(self, router: RouterProtocol, *, prefix: str = "") -> None:
        # Bind first: binding can fail, and a failed include must not leave
        # the router with a changed prefix.
        binder = getattr(router, "_bind", None)
        if callable(binder):
            binder(self)
        if prefix != "":
            router.set_prefix(prefix)
        self._routers.append(router)
        self._components_changed()
        self._check_router_narrowing(router)

    def _check_router_narrowing(self, router: RouterProtocol) -> None:
        """Run the narrowing check on the router's entries now, when it is included.

        Entries narrowing outside plugin.toml are logged here and left out of
        ``collect_entries`` (the same decision the host's static scan makes
        per entry); the rest of the router is included.
        """
        collect = getattr(router, "collect_entries", None)
        if not callable(collect):
            return
        try:
            collected = dict(collect())
        except Exception:
            # An id clash inside the router surfaces from collect_entries()
            # as before; there is nothing to check until it is fixed.
            return
        self._apply_permission_narrowing(
            {key: item for key, item in collected.items() if isinstance(item, EventHandler)}
        )

    def exclude_router(self, router: RouterProtocol | str) -> bool:
        if isinstance(router, str):
            for item in self._routers:
                if item.name() == router:
                    self._routers.remove(item)
                    unbind = getattr(item, "_unbind", None)
                    if callable(unbind):
                        unbind()
                    self._components_changed()
                    return True
            return False
        if router in self._routers:
            self._routers.remove(router)
            unbind = getattr(router, "_unbind", None)
            if callable(unbind):
                unbind()
            self._components_changed()
            return True
        return False

    def logger_component(self, suffix: str | None = None) -> str:
        plugin_id = str(getattr(self.ctx, "plugin_id", "plugin"))
        from plugin.sdk.shared.logging import build_component_name

        return build_component_name("plugin", plugin_id, suffix)

    def get_logger(self, suffix: str | None = None) -> LoggerLike:
        plugin_id = str(getattr(self.ctx, "plugin_id", "plugin"))
        return get_plugin_logger(plugin_id, suffix=suffix)

    def setup_logger(
        self,
        *,
        level: str | LogLevel | None = None,
        force: bool = False,
        suffix: str | None = None,
    ) -> LoggerLike:
        parsed_level: LogLevel | None
        if level is None:
            parsed_level = None
        elif isinstance(level, LogLevel):
            parsed_level = level
        else:
            try:
                parsed_level = LogLevel(level.strip().upper())
            except ValueError as error:
                raise ValueError(f"invalid log level: {level!r}") from error

        component = self.logger_component(suffix)
        setup_sdk_logging(component=component, level=parsed_level, force=force)
        logger = self.get_logger(suffix)
        if suffix in (None, ""):
            self.logger = logger
            self.sdk_logger = logger
        return logger

    def collect_entries(self, wrap_with_hooks: bool = True) -> dict[str, EventHandler]:
        del wrap_with_hooks
        return self._apply_permission_narrowing(self._gather_entries())

    def _gather_entries(self) -> dict[str, EventHandler]:
        """Class and router components with id conflicts settled, before the narrowing check."""
        entries: dict[str, EventHandler] = {}
        getmembers_static = getattr(inspect, "getmembers_static", inspect.getmembers)
        for attr_name, class_value in getmembers_static(type(self)):
            if attr_name.startswith("_"):
                continue
            target = class_value.__func__ if isinstance(class_value, (staticmethod, classmethod)) else class_value
            if not callable(target):
                continue
            meta = getattr(target, EVENT_META_ATTR, None)
            if isinstance(meta, _EventMetaLike) and meta.id != "":
                value = getattr(self, attr_name, None)
                if callable(value):
                    entries[str(meta.id)] = EventHandler(meta=meta, handler=value)

        class_entry_ids = set(entries)
        for router in self._routers:
            collect_entries = getattr(router, "collect_entries", None)
            if callable(collect_entries):
                router_items = collect_entries().items()
            else:
                router_items = ((key, handler) for key, handler in router.iter_handlers().items() if callable(handler))
            for key, handler in router_items:
                router_handler = _router_event_handler(str(key), handler)
                if router_handler is None:
                    continue
                existing = entries.get(str(key))
                if existing is not None and not _router_handler_replaces(
                    existing, router_handler, existing_from_class=str(key) in class_entry_ids
                ):
                    continue
                entries[str(key)] = router_handler
                class_entry_ids.discard(str(key))
        return entries

    def _apply_permission_narrowing(self, entries: dict[str, EventHandler]) -> dict[str, EventHandler]:
        """Registration check for component narrowing (``permissions=``).

        A component narrowing outside plugin.toml is left out (an entry is
        not registered, an event handler not subscribed) and logged once;
        entry hooks add their permissions to the entries they target. Runs
        after id conflicts are settled, so the permissions follow the handler
        that won. Without any narrowing the entries are returned untouched.
        """
        hooks = self._entry_hooks()
        if all(getattr(item.meta, "permissions", None) is None for item in entries.values()) and all(
            hook.permissions is None for hook in hooks
        ):
            return entries
        components = {
            entry_id: Component(
                label=component_label(event_type, entry_id),
                event_type=event_type,
                entry_id=entry_id,
                permissions=getattr(item.meta, "permissions", None),
            )
            for entry_id, item in entries.items()
            for event_type in [str(getattr(item.meta, "event_type", None) or PLUGIN_ENTRY)]
        }
        resolved = resolve_component_permissions(components, hooks, self._declared_permission_ids())
        reported: set[object] = self.__dict__.setdefault("_narrowing_reported", set())
        refused: set[tuple[str, str]] = self.__dict__.setdefault("_narrowing_refused", set())
        result: dict[str, EventHandler] = {}
        for entry_id, item in entries.items():
            resolution = resolved[entry_id]
            if not resolution.registered:
                refused.add((components[entry_id].event_type, entry_id))
                # Per handler: a refused router entry checked at include time
                # must not hide a later refusal of the class entry that wins its id.
                try:
                    key: object = (components[entry_id].event_type, entry_id, item.handler)
                    hash(key)
                except TypeError:
                    key = (components[entry_id].event_type, entry_id, id(item.handler))
                if key not in reported:
                    reported.add(key)
                    for message in resolution.errors:
                        self.logger.error("Component not registered: {}", message)
                continue
            meta = with_permissions(item.meta, resolution.permissions)
            result[entry_id] = item if meta is item.meta else EventHandler(meta=meta, handler=item.handler)
        return result

    def enable_file_logging(
        self,
        *,
        log_dir: str | Path | None = None,
        log_level: str = "INFO",
        max_bytes: int | None = None,
        backup_count: int | None = None,
    ) -> LoggerLike:
        level_str = log_level.strip().upper()
        try:
            parsed_level = LogLevel(level_str)
        except ValueError as error:
            raise ValueError(f"invalid log_level: {log_level!r}") from error

        if max_bytes is not None and max_bytes <= 0:
            raise ValueError("max_bytes must be > 0")
        if backup_count is not None and backup_count <= 0:
            raise ValueError("backup_count must be > 0")

        component = self.logger_component()
        sink_id = setup_plugin_file_logging(
            component=component,
            parsed_level=parsed_level,
            log_dir=log_dir,
            max_bytes=max_bytes,
            backup_count=backup_count,
            previous_sink_id=getattr(self, "_file_sink_id", None),
        )
        if sink_id is not None:
            setattr(self, "_file_sink_id", sink_id)

        logger = self.get_logger()
        self.logger = logger
        self.sdk_logger = logger
        setattr(self, "file_logger", logger)
        return logger


__all__ = [
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "PluginMeta",
    "NekoPluginBase",
]
