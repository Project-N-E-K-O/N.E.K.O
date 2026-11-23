"""Dynamic router contract for SDK v2 shared core."""

from __future__ import annotations

import dataclasses
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Mapping, Protocol

from plugin._types.entry_metadata import MAX_SCHEMA_DEPTH, detached_copy, schema_depth_exceeds
from plugin.sdk.shared.constants import EVENT_META_ATTR
from plugin.sdk.shared.models import Err, Ok, Result
from plugin.sdk.shared.models.exceptions import (
    EntryConflictError,
    PermissionDeniedError,
    PluginRouterError,
    RouterErrorLike,
)
from plugin.sdk.shared.permissions.narrowing import Narrowing, describe_issue, narrowing_errors, normalize_narrowing
from .decorators import _deferred_entry_inference
from .events import EventHandler, EventMeta
from .types import EntryHandler, JsonObject, JsonValue


class RouteHandler(Protocol):
    """Async route handler contract."""

    def __call__(self, payload: Mapping[str, JsonValue]) -> Awaitable[Result[JsonObject | JsonValue | None, RouterErrorLike]]: ...


@dataclass(slots=True)
class _EntryRecord:
    meta: EventMeta
    handler: RouteHandler


def _entry_owner(fn: object, router_cls: type) -> type:
    """The class in ``router_cls``'s MRO whose body defines ``fn``.

    Forward references resolve against the namespace the entry was written
    in; an entry inherited by a router subclass still names its base's models.
    """
    for cls in router_cls.__mro__:
        for value in vars(cls).values():
            target = value.__func__ if isinstance(value, (staticmethod, classmethod)) else value
            if target is fn:
                return cls
    return router_cls


def router_entry_inference(fn: object, router_cls: type) -> dict[str, object]:
    """llm_result_* / output_schema ``@plugin_entry`` could not infer, resolved against the router class.

    A return annotation naming a model defined later in the router class body
    is a forward reference the decorator cannot resolve while the class body
    runs; ``@neko_plugin`` finishes that for plugin classes, and this does the
    same for routers (runtime entries and the host's static preview). Only
    pending inferences run, so explicit contracts are never replaced. Params
    inference stays with the decorator: switching a router entry to model
    validation of its inputs would change which calls it accepts.
    """
    if not callable(fn):
        return {}
    return _deferred_entry_inference(fn, _entry_owner(fn, router_cls), include_params=False)


def router_entry_meta(meta: EventMeta, fn: object, router_cls: type) -> EventMeta:
    """``meta`` with the router-class inference applied, as a copy; the shared metadata is untouched."""
    inferred = router_entry_inference(fn, router_cls)
    return dataclasses.replace(meta, **inferred) if inferred else meta


def router_permissions(router_cls: type) -> Narrowing | None:
    """The router-level narrowing a router class declares (``permissions = [...]``)."""
    return normalize_narrowing(getattr(router_cls, "permissions", None), where=f"{router_cls.__name__}.permissions")


def router_entry_permissions(meta: EventMeta, router_level: Narrowing | None) -> EventMeta:
    """``meta`` with the router's narrowing filled in when the entry leaves it out."""
    if getattr(meta, "permissions", None) is not None or router_level is None:
        return meta
    return dataclasses.replace(meta, permissions=router_level)


def denied_narrowing(declared: frozenset[str], permissions: Narrowing, label: str) -> PermissionDeniedError | None:
    """The permission error for a narrowing outside ``declared``; None if it fits."""
    errors = narrowing_errors(permissions, declared, label)
    if not errors:
        return None
    outside = [permission for permission in permissions if permission not in declared]
    return PermissionDeniedError(
        "; ".join(describe_issue(issue) for issue in errors),
        permission=outside[0] if outside else None,
        reason="not_declared",
    )


class PluginRouter:
    """Async-first router with light plugin-bound convenience accessors.

    ``permissions`` (class attribute or constructor argument) narrows every
    entry of the router that does not narrow itself; ``None`` inherits all
    of plugin.toml's declarations. Entries are checked when the router is
    included; one narrowing outside the declarations is not registered.
    """

    # Router-level narrowing for subclasses; a class attribute so the host's
    # static scan of ``__routers__`` classes sees it too.
    permissions: list[str] | tuple[str, ...] | None = None

    def __init__(
        self,
        *,
        prefix: str = "",
        tags: list[str] | None = None,
        name: str | None = None,
        permissions: list[str] | tuple[str, ...] | None = None,
    ):
        self.permissions = (
            normalize_narrowing(permissions, where=f"{type(self).__name__}(permissions=...)")
            if permissions is not None
            else router_permissions(type(self))
        )
        self._prefix = prefix
        self._tags = tags or []
        self._name = name or self.__class__.__name__
        self._entries: dict[str, _EntryRecord] = {}
        # Store methods decorated with @plugin_entry (original id + original meta + bound method, meow).
        # Do not insert them into self._entries in __init__, because Base.include_router may subsequently call
        # set_prefix() to change prefix; early insertion would prevent that later prefix from taking effect.
        # Instead resolve lazily in collect_entries() using the current prefix.
        self._decorated_entries: list[tuple[str, EventMeta, RouteHandler]] = []
        # Contract fields inferred against this router class, one per decorated
        # entry, kept apart from the shared metadata (caching rules: see
        # _inferred_contract_fields). Keyed by id() so an unhashable callable
        # works; the object is stored alongside, so its id cannot be reused.
        self._deferred_inference: dict[int, tuple[object, dict[str, object]]] = {}
        self._main_plugin: object | None = None
        self._collect_decorated_entries()

    def _collect_decorated_entries(self) -> None:
        """Scan subclass methods carrying EVENT_META_ATTR and retain them in `_decorated_entries`.

        Rationale:
        - Subclasses normally declare entries using `@plugin_entry(id=...)`; `PluginRouter.collect_entries`
          previously returned only `self._entries`, completely ignoring decorator metadata, meow.
        - Perform one static scan here while preserving dynamic `add_entry` registration; defer prefix resolution and
          ID normalization to `collect_entries()`.
        """
        for attr_name, class_value in inspect.getmembers_static(type(self)):
            if attr_name.startswith("_"):
                continue
            target = class_value.__func__ if isinstance(class_value, (staticmethod, classmethod)) else class_value
            if not callable(target):
                continue
            meta = getattr(target, EVENT_META_ATTR, None)
            if meta is None or getattr(meta, "id", "") == "":
                continue
            bound = getattr(self, attr_name, None)
            if not callable(bound):
                continue
            self._decorated_entries.append((str(meta.id), meta, bound))

    @property
    def prefix(self) -> str:
        return self._prefix

    @prefix.setter
    def prefix(self, value: str) -> None:
        self._prefix = value

    @property
    def tags(self) -> list[str]:
        return list(self._tags)

    @property
    def is_bound(self) -> bool:
        return self._main_plugin is not None

    @property
    def entry_ids(self) -> list[str]:
        # IDs dynamically registered by add_entry + decorator IDs resolved under the current prefix
        return list(self._resolved_entries().keys())

    @property
    def ctx(self) -> object | None:
        return getattr(self._main_plugin, "ctx", None)

    @property
    def config(self) -> object | None:
        return getattr(self._main_plugin, "config", None)

    @property
    def plugins(self) -> object | None:
        return getattr(self._main_plugin, "plugins", None)

    @property
    def logger(self) -> Any | None:
        return getattr(self._main_plugin, "logger", None)

    @property
    def file_logger(self) -> Any | None:
        return getattr(self._main_plugin, "file_logger", None)

    @property
    def store(self) -> object | None:
        return getattr(self._main_plugin, "store", None)

    @property
    def db(self) -> object | None:
        return getattr(self._main_plugin, "db", None)

    @property
    def plugin_id(self) -> str:
        if self._main_plugin is None:
            return self._name
        plugin_id = getattr(self._main_plugin, "plugin_id", None)
        if plugin_id is not None:
            return str(plugin_id)
        ctx = getattr(self._main_plugin, "ctx", None)
        return str(getattr(ctx, "plugin_id", self._name))

    @property
    def main_plugin(self) -> object:
        if self._main_plugin is None:
            raise PluginRouterError(f"router {self._name!r} is not bound to plugin")
        return self._main_plugin

    def _bind(self, plugin: object) -> None:
        # Finish the router-class inference once, when the plugin includes the
        # router: a failure (an inferred schema over the depth bound) surfaces
        # here, like @neko_plugin raises at class definition, instead of on
        # every collect_entries() call.
        # Nothing is cached until every entry succeeds, so a failed bind leaves
        # the router as it was and a later bind infers again.
        pending: dict[int, tuple[object, dict[str, object]]] = {}
        for _, _, handler in self._decorated_entries:
            fn = getattr(handler, "__func__", handler)
            if id(fn) not in self._deferred_inference:
                pending[id(fn)] = (fn, router_entry_inference(fn, type(self)))
        self._deferred_inference.update(pending)
        self._main_plugin = plugin

    def _unbind(self) -> None:
        self._main_plugin = None

    def _resolve_entry_id(self, entry_id: str) -> str:
        candidate = entry_id.strip()
        if candidate.startswith(self._prefix):
            return candidate
        return f"{self._prefix}{candidate}"

    def name(self) -> str:
        return self._name

    def set_prefix(self, prefix: str) -> None:
        self._prefix = prefix

    def iter_handlers(self) -> Mapping[str, EntryHandler]:
        return {entry_id: record.handler for entry_id, record in self._resolved_entries().items()}

    def get_plugin_attr(self, name: str, default: object | None = None) -> object | None:
        return getattr(self._main_plugin, name, default)

    def has_plugin_attr(self, name: str) -> bool:
        return hasattr(self._main_plugin, name)

    def get_dependency(self, name: str, default: object | None = None) -> object | None:
        value = getattr(self, name, None)
        if value is not None:
            return value
        return getattr(self._main_plugin, name, default)

    def report_status(self, status: dict[str, Any]) -> None:
        plugin = self._main_plugin
        if plugin is not None and hasattr(plugin, "report_status"):
            plugin.report_status(status)

    def collect_entries(self) -> Mapping[str, EventHandler]:
        return {
            entry_id: EventHandler(meta=record.meta, handler=record.handler)
            for entry_id, record in self._resolved_entries().items()
        }

    def _resolved_entries(self) -> dict[str, _EntryRecord]:
        """Merge dynamic (add_entry) entries with @plugin_entry-decorated entries.

        Only now resolve decorator-entry IDs using the current prefix and normalize meta.id and dictionary keys;
        duplicate IDs (between decorators or between a decorator and add_entry) raise EntryConflictError,
        matching `add_entry` conflict semantics, meow.
        """
        resolved: dict[str, _EntryRecord] = dict(self._entries)
        for raw_id, meta, handler in self._decorated_entries:
            entry_id = self._resolve_entry_id(raw_id)
            if entry_id in resolved:
                raise EntryConflictError(f"duplicate entry id: {entry_id!r}")
            # Normalize meta.id and dictionary keys to match, including prefix in both
            # Copy every EventMeta field (dataclasses.replace), then override
            # identity and detach the mutable containers. Spelling fields out
            # one by one silently dropped any field added to EventMeta later.
            inferred = self._inferred_contract_fields(handler)
            source = dataclasses.replace(meta, **inferred) if inferred else meta
            source = router_entry_permissions(source, self.permissions)
            normalized = dataclasses.replace(
                source,
                id=entry_id,
                name=source.name if source.name else entry_id,
                input_schema=dict(source.input_schema) if source.input_schema is not None else None,
                llm_result_fields=list(source.llm_result_fields) if source.llm_result_fields else None,
                llm_result_schema=dict(source.llm_result_schema) if source.llm_result_schema else None,
                output_schema=detached_copy(source.output_schema) if source.output_schema is not None else None,
                extra=dict(source.extra),
                metadata=dict(source.metadata),
            )
            resolved[entry_id] = _EntryRecord(meta=normalized, handler=handler)
        return resolved

    def _inferred_contract_fields(self, handler: RouteHandler) -> dict[str, object]:
        """Contract fields the decorator left pending, resolved against this router (cached per entry).

        Before the router is bound, an empty result is not cached: a model
        defined after the router may still resolve later. From binding on
        (module import is complete by then) every result is cached.
        """
        fn = getattr(handler, "__func__", handler)
        cached = self._deferred_inference.get(id(fn))
        if cached is not None:
            return cached[1]
        inferred = router_entry_inference(fn, type(self))
        if inferred or self.is_bound:
            self._deferred_inference[id(fn)] = (fn, inferred)
        return inferred

    def _declared_permissions(self) -> frozenset[str] | None:
        reader = getattr(self._main_plugin, "_declared_permission_ids", None)
        return reader() if callable(reader) else None

    def on_mount(self) -> None:
        return None

    def on_unmount(self) -> None:
        return None

    async def add_entry(
        self,
        entry_id: str,
        handler: RouteHandler,
        *,
        name: str | None = None,
        description: str = "",
        input_schema: Mapping[str, JsonValue] | None = None,
        replace: bool = False,
        output_schema: Mapping[str, JsonValue] | None = None,
        permissions: list[str] | tuple[str, ...] | None = None,
    ) -> Result[bool, RouterErrorLike]:
        """Register an entry at runtime.

        ``permissions`` narrows the entry; left out, it inherits the router's.
        On a bound router the narrowing must be a subset of plugin.toml's
        declarations and entry hooks targeting the id join it, otherwise
        ``Err(PermissionDeniedError)`` with reason ``not_declared``. An
        unbound router is checked when it is included.
        """
        trimmed = entry_id.strip()
        if trimmed == "":
            return Err(PluginRouterError("entry_id must be non-empty"))
        full_entry_id = self._resolve_entry_id(trimmed)
        if full_entry_id in self._entries and not replace:
            return Err(EntryConflictError(f"duplicate entry id: {full_entry_id!r}"))
        if output_schema is not None and schema_depth_exceeds(output_schema):
            return Err(PluginRouterError(f"output_schema nests deeper than {MAX_SCHEMA_DEPTH} levels"))
        try:
            narrowed = normalize_narrowing(permissions, where=f"add_entry({full_entry_id!r})")
        except TypeError as exc:
            return Err(PluginRouterError(str(exc)))
        effective = narrowed if narrowed is not None else self.permissions
        resolve = getattr(self._main_plugin, "_resolve_dynamic_entry_permissions", None) if self.is_bound else None
        if callable(resolve):
            # The plugin's check for runtime entries: the narrowing must fit
            # plugin.toml, and entry hooks targeting the id join it or close it
            # (it logs the refusal).
            try:
                # Validation only: the entry records its own narrowing; hooks
                # are joined again by collect_entries() of the plugin that
                # includes the router.
                resolve(full_entry_id, effective)
            except PermissionDeniedError as denied:
                return Err(denied)
        elif self.is_bound and effective is not None:
            declared = self._declared_permissions()
            denied = denied_narrowing(declared, effective, f"router {self._name!r} entry {full_entry_id!r}") if (
                declared is not None
            ) else None
            if denied is not None:
                logger = self.logger
                if logger is not None:
                    logger.error("Entry not registered: {}", str(denied))
                return Err(denied)
        meta = EventMeta(
            event_type="plugin_entry",
            id=full_entry_id,
            name=name or full_entry_id,
            description=description,
            input_schema=dict(input_schema) if input_schema is not None else None,
            output_schema=detached_copy(output_schema) if output_schema is not None else None,
            permissions=effective,
        )
        self._entries[full_entry_id] = _EntryRecord(meta=meta, handler=handler)
        return Ok(True)

    async def remove_entry(self, entry_id: str) -> Result[bool, RouterErrorLike]:
        full_entry_id = self._resolve_entry_id(entry_id.strip())
        if full_entry_id in self._entries:
            del self._entries[full_entry_id]
            return Ok(True)
        return Ok(False)

    async def list_entries(self) -> Result[list[EventMeta], RouterErrorLike]:
        return Ok([record.meta for record in self._resolved_entries().values()])


__all__ = ["RouteHandler", "PluginRouter", "PluginRouterError", "EntryConflictError", "router_permissions"]
