"""Shared decorators for SDK v2.

This module contains the real decorator behavior and metadata binding.
Plugin-facing layers should re-export from here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Mapping, TypeVar

from plugin.sdk_v2.shared.constants import (
    CHECKPOINT_ATTR,
    EVENT_META_ATTR,
    HOOK_META_ATTR,
    NEKO_PLUGIN_TAG,
    PERSIST_ATTR,
)

F = TypeVar("F", bound=Callable[..., object])
EntryKind = Literal["service", "action", "hook", "custom", "lifecycle", "consumer", "timer"]


@dataclass(slots=True)
class EventDecoratorMeta:
    event_type: str
    id: str
    name: str
    description: str
    input_schema: dict[str, object] | None
    kind: EntryKind
    auto_start: bool
    persist: bool | None
    checkpoint: bool | None
    params: type | None
    model_validate: bool
    timeout: float | None
    metadata: dict[str, object] = field(default_factory=dict)
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class HookDecoratorMeta:
    target: str
    timing: str
    priority: int
    condition: str | None


def _attach_event_meta(fn: F, meta: EventDecoratorMeta) -> F:
    setattr(fn, EVENT_META_ATTR, meta)
    if meta.persist is not None:
        setattr(fn, PERSIST_ATTR, bool(meta.persist))
    elif meta.checkpoint is not None:
        setattr(fn, PERSIST_ATTR, bool(meta.checkpoint))
    return fn


def _attach_hook_meta(fn: F, meta: HookDecoratorMeta) -> F:
    setattr(fn, HOOK_META_ATTR, meta)
    return fn


def _normalize_mapping(value: Mapping[str, object] | None) -> dict[str, object]:
    return dict(value) if value is not None else {}


def neko_plugin(cls: type) -> type:
    """Class marker for plugin discovery."""
    setattr(cls, NEKO_PLUGIN_TAG, True)
    return cls


def on_event(
    *,
    event_type: str,
    id: str | None = None,
    name: str | None = None,
    description: str = "",
    input_schema: dict[str, object] | None = None,
    kind: EntryKind = "action",
    auto_start: bool = False,
    persist: bool | None = None,
    checkpoint: bool | None = None,
    metadata: dict[str, object] | None = None,
    extra: dict[str, object] | None = None,
) -> Callable[[F], F]:
    event_type_clean = event_type.strip()
    if event_type_clean == "":
        raise ValueError("event_type must be non-empty")

    def _decorator(fn: F) -> F:
        event_id = (id or fn.__name__).strip()
        if event_id == "":
            raise ValueError("event id must be non-empty")
        event_name = (name or event_id).strip() or event_id
        meta = EventDecoratorMeta(
            event_type=event_type_clean,
            id=event_id,
            name=event_name,
            description=description,
            input_schema=input_schema,
            kind=kind,
            auto_start=auto_start,
            persist=persist,
            checkpoint=checkpoint,
            params=None,
            model_validate=True,
            timeout=None,
            metadata=_normalize_mapping(metadata),
            extra=_normalize_mapping(extra),
        )
        return _attach_event_meta(fn, meta)

    return _decorator


def plugin_entry(
    id: str | None = None,
    name: str | None = None,
    description: str = "",
    input_schema: dict[str, object] | None = None,
    params: type | None = None,
    kind: EntryKind = "action",
    auto_start: bool = False,
    persist: bool | None = None,
    checkpoint: bool | None = None,
    model_validate: bool = True,
    timeout: float | None = None,
    metadata: dict[str, object] | None = None,
    extra: dict[str, object] | None = None,
) -> Callable[[F], F]:
    def _decorator(fn: F) -> F:
        event_id = (id or fn.__name__).strip()
        if event_id == "":
            raise ValueError("entry id must be non-empty")
        event_name = (name or event_id).strip() or event_id
        meta = EventDecoratorMeta(
            event_type="plugin_entry",
            id=event_id,
            name=event_name,
            description=description,
            input_schema=input_schema,
            kind=kind,
            auto_start=auto_start,
            persist=persist,
            checkpoint=checkpoint,
            params=params,
            model_validate=model_validate,
            timeout=timeout,
            metadata=_normalize_mapping(metadata),
            extra=_normalize_mapping(extra),
        )
        return _attach_event_meta(fn, meta)

    return _decorator


def lifecycle(
    *,
    id: Literal["startup", "shutdown", "reload", "freeze", "unfreeze", "config_change"],
    name: str | None = None,
    description: str = "",
    metadata: dict[str, object] | None = None,
    extra: dict[str, object] | None = None,
) -> Callable[[F], F]:
    return on_event(
        event_type="lifecycle",
        id=id,
        name=name,
        description=description,
        kind="lifecycle",
        metadata=metadata,
        extra=extra,
    )


def message(
    *,
    id: str,
    name: str | None = None,
    description: str = "",
    input_schema: dict[str, object] | None = None,
    source: str | None = None,
    metadata: dict[str, object] | None = None,
    extra: dict[str, object] | None = None,
) -> Callable[[F], F]:
    merged_extra = _normalize_mapping(extra)
    if source is not None:
        merged_extra["source"] = source
    return on_event(
        event_type="message",
        id=id,
        name=name,
        description=description,
        input_schema=input_schema,
        kind="consumer",
        metadata=metadata,
        extra=merged_extra,
    )


def timer_interval(
    *,
    id: str,
    seconds: int,
    name: str | None = None,
    description: str = "",
    auto_start: bool = True,
    metadata: dict[str, object] | None = None,
    extra: dict[str, object] | None = None,
) -> Callable[[F], F]:
    if seconds <= 0:
        raise ValueError("seconds must be > 0")
    merged_extra = _normalize_mapping(extra)
    merged_extra["seconds"] = seconds
    return on_event(
        event_type="timer",
        id=id,
        name=name,
        description=description,
        kind="timer",
        auto_start=auto_start,
        metadata=metadata,
        extra=merged_extra,
    )


def custom_event(
    *,
    event_type: str,
    id: str,
    name: str | None = None,
    description: str = "",
    input_schema: dict[str, object] | None = None,
    kind: EntryKind = "custom",
    auto_start: bool = False,
    trigger_method: str = "message",
    metadata: dict[str, object] | None = None,
    extra: dict[str, object] | None = None,
) -> Callable[[F], F]:
    merged_extra = _normalize_mapping(extra)
    merged_extra["trigger_method"] = trigger_method
    return on_event(
        event_type=event_type,
        id=id,
        name=name,
        description=description,
        input_schema=input_schema,
        kind=kind,
        auto_start=auto_start,
        metadata=metadata,
        extra=merged_extra,
    )


def hook(*, target: str = "*", timing: str = "before", priority: int = 0, condition: str | None = None) -> Callable[[F], F]:
    if timing not in {"before", "after", "around", "replace"}:
        raise ValueError("timing must be one of: before, after, around, replace")

    def _decorator(fn: F) -> F:
        return _attach_hook_meta(
            fn,
            HookDecoratorMeta(target=target, timing=timing, priority=priority, condition=condition),
        )

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
    def entry(**kwargs: object) -> Callable[[F], F]:
        return plugin_entry(**kwargs)


plugin = _PluginDecorators()


__all__ = [
    "EntryKind",
    "PERSIST_ATTR",
    "CHECKPOINT_ATTR",
    "EVENT_META_ATTR",
    "HOOK_META_ATTR",
    "EventDecoratorMeta",
    "HookDecoratorMeta",
    "neko_plugin",
    "on_event",
    "plugin_entry",
    "lifecycle",
    "message",
    "timer_interval",
    "custom_event",
    "hook",
    "before_entry",
    "after_entry",
    "around_entry",
    "replace_entry",
    "plugin",
]
