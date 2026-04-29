"""Plugin flavor decorators.

The shared layer owns the metadata model and validation rules. This module keeps
plugin-facing names stable and adds plugin-oriented convenience proxies such as
`plugin.entry(...)` and `plugin.hook(...)`.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Literal, TypeVar, cast

from plugin.sdk.shared.constants import EVENT_META_ATTR, HOOK_META_ATTR, PERSIST_ATTR
from plugin.sdk.shared.core.decorators import (
    EntryKind,
    EventMeta,
    HookDecoratorMeta,
    after_entry as _after_entry,
    around_entry as _around_entry,
    before_entry as _before_entry,
    custom_event as _custom_event,
    hook as _hook,
    lifecycle as _lifecycle,
    message as _message,
    neko_plugin as _neko_plugin,
    on_event as _on_event,
    plugin_entry as _plugin_entry,
    replace_entry as _replace_entry,
    timer_interval as _timer_interval,
)

F = TypeVar("F", bound=Callable[..., object])
C = TypeVar("C", bound=type[object])


def _capture_declaration_locals(stack_depth: int = 2) -> dict[str, object] | None:
    declaration_locals: dict[str, object] | None = None
    current_frame = inspect.currentframe()
    caller_frame = current_frame
    try:
        for _ in range(stack_depth):
            caller_frame = caller_frame.f_back if caller_frame is not None else None
        if caller_frame is not None:
            declaration_locals = dict(caller_frame.f_locals)
    finally:
        del caller_frame
        del current_frame
    return declaration_locals


def neko_plugin(cls: C) -> C:
    return cast(C, _neko_plugin(cls))


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
    mode: str | None = None,
    seconds: int | None = None,
    extra: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> Callable[[F], F]:
    return _on_event(
        event_type=event_type,
        id=id,
        name=name,
        description=description,
        input_schema=input_schema,
        kind=kind,
        auto_start=auto_start,
        persist=persist,
        mode=mode,
        seconds=seconds,
        extra=extra,
        metadata=metadata,
    )


def plugin_entry(
    *,
    id: str | None = None,
    name: object | None = None,
    description: object = "",
    input_schema: dict[str, object] | None = None,
    params: type | None = None,
    kind: EntryKind = "action",
    auto_start: bool = False,
    persist: bool | None = None,
    model_validate: bool = True,
    timeout: float | None = None,
    llm_result_fields: list[str] | None = None,
    llm_result_model: type | None = None,
    fields: type | None = None,
    metadata: dict[str, object] | None = None,
    _localns: dict[str, object] | None = None,
) -> Callable[[F], F]:
    return _plugin_entry(
        id=id,
        name=name,
        description=description,
        input_schema=input_schema,
        params=params,
        kind=kind,
        auto_start=auto_start,
        persist=persist,
        model_validate=model_validate,
        timeout=timeout,
        llm_result_fields=llm_result_fields,
        llm_result_model=llm_result_model,
        fields=fields,
        metadata=metadata,
        _localns=_localns if _localns is not None else _capture_declaration_locals(),
    )


def lifecycle(
    *,
    id: Literal["startup", "shutdown", "reload", "freeze", "unfreeze", "config_change"],
    name: str | None = None,
    description: str = "",
    metadata: dict[str, object] | None = None,
) -> Callable[[F], F]:
    return _lifecycle(id=id, name=name, description=description, metadata=metadata)


def message(
    *,
    id: str,
    name: str | None = None,
    description: str = "",
    input_schema: dict[str, object] | None = None,
    source: str | None = None,
    metadata: dict[str, object] | None = None,
) -> Callable[[F], F]:
    return _message(
        id=id,
        name=name,
        description=description,
        input_schema=input_schema,
        source=source,
        metadata=metadata,
    )


def timer_interval(
    *,
    id: str,
    seconds: int,
    name: str | None = None,
    description: str = "",
    auto_start: bool = True,
    metadata: dict[str, object] | None = None,
) -> Callable[[F], F]:
    return _timer_interval(
        id=id,
        seconds=seconds,
        name=name,
        description=description,
        auto_start=auto_start,
        metadata=metadata,
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
) -> Callable[[F], F]:
    return _custom_event(
        event_type=event_type,
        id=id,
        name=name,
        description=description,
        input_schema=input_schema,
        kind=kind,
        auto_start=auto_start,
        trigger_method=trigger_method,
        metadata=metadata,
    )


def hook(
    *,
    target: str = "*",
    timing: str = "before",
    priority: int = 0,
    condition: str | None = None,
) -> Callable[[F], F]:
    return _hook(target=target, timing=timing, priority=priority, condition=condition)


def before_entry(
    *,
    target: str = "*",
    priority: int = 0,
    condition: str | None = None,
) -> Callable[[F], F]:
    return _before_entry(target=target, priority=priority, condition=condition)


def after_entry(
    *,
    target: str = "*",
    priority: int = 0,
    condition: str | None = None,
) -> Callable[[F], F]:
    return _after_entry(target=target, priority=priority, condition=condition)


def around_entry(
    *,
    target: str = "*",
    priority: int = 0,
    condition: str | None = None,
) -> Callable[[F], F]:
    return _around_entry(target=target, priority=priority, condition=condition)


def replace_entry(
    *,
    target: str = "*",
    priority: int = 0,
    condition: str | None = None,
) -> Callable[[F], F]:
    return _replace_entry(target=target, priority=priority, condition=condition)


class _PluginDecorators:
    @staticmethod
    def entry(
        *,
        id: str | None = None,
        name: object | None = None,
        description: object = "",
        input_schema: dict[str, object] | None = None,
        params: type | None = None,
        kind: EntryKind = "action",
        auto_start: bool = False,
        persist: bool | None = None,
        model_validate: bool = True,
        timeout: float | None = None,
        llm_result_fields: list[str] | None = None,
        llm_result_model: type | None = None,
        fields: type | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Callable[[F], F]:
        declaration_locals = _capture_declaration_locals()
        return plugin_entry(
            id=id,
            name=name,
            description=description,
            input_schema=input_schema,
            params=params,
            kind=kind,
            auto_start=auto_start,
            persist=persist,
            model_validate=model_validate,
            timeout=timeout,
            llm_result_fields=llm_result_fields,
            llm_result_model=llm_result_model,
            fields=fields,
            metadata=metadata,
            _localns=declaration_locals,
        )

    @staticmethod
    def event(
        *,
        event_type: str,
        id: str | None = None,
        name: str | None = None,
        description: str = "",
        input_schema: dict[str, object] | None = None,
        kind: EntryKind = "action",
        auto_start: bool = False,
        persist: bool | None = None,
        mode: str | None = None,
        seconds: int | None = None,
        extra: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Callable[[F], F]:
        return on_event(
            event_type=event_type,
            id=id,
            name=name,
            description=description,
            input_schema=input_schema,
            kind=kind,
            auto_start=auto_start,
            persist=persist,
            mode=mode,
            seconds=seconds,
            extra=extra,
            metadata=metadata,
        )

    @staticmethod
    def hook(
        *,
        target: str = "*",
        timing: str = "before",
        priority: int = 0,
        condition: str | None = None,
    ) -> Callable[[F], F]:
        return hook(target=target, timing=timing, priority=priority, condition=condition)

    @staticmethod
    def lifecycle(
        *,
        id: Literal["startup", "shutdown", "reload", "freeze", "unfreeze", "config_change"],
        name: str | None = None,
        description: str = "",
        metadata: dict[str, object] | None = None,
    ) -> Callable[[F], F]:
        return lifecycle(id=id, name=name, description=description, metadata=metadata)

    @staticmethod
    def message(
        *,
        id: str,
        name: str | None = None,
        description: str = "",
        input_schema: dict[str, object] | None = None,
        source: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Callable[[F], F]:
        return message(
            id=id,
            name=name,
            description=description,
            input_schema=input_schema,
            source=source,
            metadata=metadata,
        )

    @staticmethod
    def timer(
        *,
        id: str,
        seconds: int,
        name: str | None = None,
        description: str = "",
        auto_start: bool = True,
        metadata: dict[str, object] | None = None,
    ) -> Callable[[F], F]:
        return timer_interval(
            id=id,
            seconds=seconds,
            name=name,
            description=description,
            auto_start=auto_start,
            metadata=metadata,
        )

    @staticmethod
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
    ) -> Callable[[F], F]:
        return custom_event(
            event_type=event_type,
            id=id,
            name=name,
            description=description,
            input_schema=input_schema,
            kind=kind,
            auto_start=auto_start,
            trigger_method=trigger_method,
            metadata=metadata,
        )


plugin = _PluginDecorators()

__all__ = [
    "EVENT_META_ATTR",
    "EntryKind",
    "EventMeta",
    "HOOK_META_ATTR",
    "HookDecoratorMeta",
    "PERSIST_ATTR",
    "after_entry",
    "around_entry",
    "before_entry",
    "custom_event",
    "hook",
    "lifecycle",
    "message",
    "neko_plugin",
    "on_event",
    "plugin",
    "plugin_entry",
    "replace_entry",
    "timer_interval",
]
