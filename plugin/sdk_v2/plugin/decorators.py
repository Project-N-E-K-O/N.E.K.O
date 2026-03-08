"""sdk_v2.plugin.decorators

SDD contract-only decorator surface.

This file defines complete decorator signatures and boundary semantics.
All decorator factories are non-runnable placeholders in this phase.
"""

from __future__ import annotations

from typing import Any, Callable, Literal, TypeVar

F = TypeVar("F", bound=Callable[..., Any])
EntryKind = Literal["service", "action", "hook", "custom", "lifecycle", "consumer", "timer"]

PERSIST_ATTR = "_neko_persist"
CHECKPOINT_ATTR = PERSIST_ATTR


def _not_impl(*_args: Any, **_kwargs: Any) -> None:
    raise NotImplementedError("sdk_v2 contract-only facade: decorators not implemented")


def neko_plugin(cls: type) -> type:
    """Class marker for plugin discovery.

    Boundary constraints:
    - target must be a class type.
    - implementation will mark class with `NEKO_PLUGIN_TAG`.
    """
    _not_impl(cls)
    return cls


def on_event(
    *,
    event_type: str,
    id: str | None = None,
    name: str | None = None,
    description: str = "",
    input_schema: dict | None = None,
    kind: EntryKind = "action",
    auto_start: bool = False,
    persist: bool | None = None,
    checkpoint: bool | None = None,
    metadata: dict | None = None,
    extra: dict | None = None,
) -> Callable[[F], F]:
    """Generic event decorator contract.

    Input/output contract:
    - input: function `fn`
    - output: same function object with attached event metadata

    Boundary constraints:
    - `event_type` must be non-empty.
    - `id` defaults to function name.
    - `input_schema` must be JSON-schema-like mapping when provided.
    - `checkpoint` is alias of `persist` for backward compatibility.
    """
    _not_impl(event_type, id, name, description, input_schema, kind, auto_start, persist, checkpoint, metadata, extra)

    def _decorator(fn: F) -> F:
        _not_impl(fn)
        return fn

    return _decorator


def plugin_entry(
    id: str | None = None,
    name: str | None = None,
    description: str = "",
    input_schema: dict | None = None,
    params: type | None = None,
    kind: EntryKind = "action",
    auto_start: bool = False,
    persist: bool | None = None,
    checkpoint: bool | None = None,
    model_validate: bool = True,
    timeout: float | None = None,
    metadata: dict | None = None,
    extra: dict | None = None,
) -> Callable[[F], F]:
    """Public callable entry decorator contract.

    Data/boundary contract:
    - if `params` provided, implementation derives `input_schema` from model.
    - if `model_validate=True`, runtime performs automatic parameter validation.
    - if `timeout` provided, runtime enforces per-entry timeout policy.
    - `kind` defaults to "action".
    """
    _not_impl(id, name, description, input_schema, params, kind, auto_start, persist, checkpoint, model_validate, timeout, metadata, extra)

    def _decorator(fn: F) -> F:
        _not_impl(fn)
        return fn

    return _decorator


def lifecycle(
    *,
    id: Literal["startup", "shutdown", "reload", "freeze", "unfreeze", "config_change"],
    name: str | None = None,
    description: str = "",
    metadata: dict | None = None,
    extra: dict | None = None,
) -> Callable[[F], F]:
    """Lifecycle event decorator contract."""
    _not_impl(id, name, description, metadata, extra)

    def _decorator(fn: F) -> F:
        _not_impl(fn)
        return fn

    return _decorator


def message(
    *,
    id: str,
    name: str | None = None,
    description: str = "",
    input_schema: dict | None = None,
    source: str | None = None,
    metadata: dict | None = None,
    extra: dict | None = None,
) -> Callable[[F], F]:
    """Message consumer decorator contract."""
    _not_impl(id, name, description, input_schema, source, metadata, extra)

    def _decorator(fn: F) -> F:
        _not_impl(fn)
        return fn

    return _decorator


def timer_interval(
    *,
    id: str,
    seconds: int,
    name: str | None = None,
    description: str = "",
    auto_start: bool = True,
    metadata: dict | None = None,
    extra: dict | None = None,
) -> Callable[[F], F]:
    """Interval timer decorator contract.

    Boundary constraints:
    - `seconds` must be > 0.
    """
    _not_impl(id, seconds, name, description, auto_start, metadata, extra)

    def _decorator(fn: F) -> F:
        _not_impl(fn)
        return fn

    return _decorator


def custom_event(
    *,
    event_type: str,
    id: str,
    name: str | None = None,
    description: str = "",
    input_schema: dict | None = None,
    kind: EntryKind = "custom",
    auto_start: bool = False,
    trigger_method: str = "message",
    metadata: dict | None = None,
    extra: dict | None = None,
) -> Callable[[F], F]:
    """Custom event decorator contract."""
    _not_impl(event_type, id, name, description, input_schema, kind, auto_start, trigger_method, metadata, extra)

    def _decorator(fn: F) -> F:
        _not_impl(fn)
        return fn

    return _decorator


def hook(*, target: str = "*", timing: str = "before", priority: int = 0, condition: str | None = None) -> Callable[[F], F]:
    """Hook decorator contract.

    Boundary constraints:
    - `timing` in {before, after, around, replace}
    - `priority` is integer; higher runs earlier for same timing.
    """
    _not_impl(target, timing, priority, condition)

    def _decorator(fn: F) -> F:
        _not_impl(fn)
        return fn

    return _decorator


def before_entry(*, target: str = "*", priority: int = 0, condition: str | None = None) -> Callable[[F], F]:
    return hook(target=target, timing="before", priority=priority, condition=condition)


def after_entry(*, target: str = "*", priority: int = 0, condition: str | None = None) -> Callable[[F], F]:
    return hook(target=target, timing="after", priority=priority, condition=condition)


def around_entry(*, target: str = "*", priority: int = 0, condition: str | None = None) -> Callable[[F], F]:
    return hook(target=target, timing="around", priority=priority, condition=condition)


def replace_entry(*, target: str = "*", priority: int = 0, condition: str | None = None) -> Callable[[F], F]:
    return hook(target=target, timing="replace", priority=priority, condition=condition)


class _PluginDecorators:
    @staticmethod
    def entry(**kwargs: Any) -> Callable[[F], F]:
        return plugin_entry(**kwargs)


plugin = _PluginDecorators()

__all__ = [
    "neko_plugin",
    "plugin_entry",
    "lifecycle",
    "on_event",
    "message",
    "timer_interval",
    "custom_event",
    "plugin",
    "hook",
    "before_entry",
    "after_entry",
    "around_entry",
    "replace_entry",
    "EntryKind",
    "PERSIST_ATTR",
    "CHECKPOINT_ATTR",
]
