"""Plugin flavor decorators.

The shared layer owns the metadata model and validation rules. This module keeps
plugin-facing names stable and adds plugin-oriented convenience proxies such as
`plugin.entry(...)` and `plugin.hook(...)`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from plugin.sdk_v2.shared.constants import CHECKPOINT_ATTR, EVENT_META_ATTR, HOOK_META_ATTR, PERSIST_ATTR
from plugin.sdk_v2.shared.core.decorators import (
    EntryKind,
    EventDecoratorMeta,
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


def neko_plugin(cls: type[F]) -> type[F]:
    return _neko_plugin(cls)


def on_event(**kwargs: object) -> Callable[[F], F]:
    return _on_event(**kwargs)


def plugin_entry(**kwargs: object) -> Callable[[F], F]:
    return _plugin_entry(**kwargs)


def lifecycle(**kwargs: object) -> Callable[[F], F]:
    return _lifecycle(**kwargs)


def message(**kwargs: object) -> Callable[[F], F]:
    return _message(**kwargs)


def timer_interval(**kwargs: object) -> Callable[[F], F]:
    return _timer_interval(**kwargs)


def custom_event(**kwargs: object) -> Callable[[F], F]:
    return _custom_event(**kwargs)


def hook(**kwargs: object) -> Callable[[F], F]:
    return _hook(**kwargs)


def before_entry(**kwargs: object) -> Callable[[F], F]:
    return _before_entry(**kwargs)


def after_entry(**kwargs: object) -> Callable[[F], F]:
    return _after_entry(**kwargs)


def around_entry(**kwargs: object) -> Callable[[F], F]:
    return _around_entry(**kwargs)


def replace_entry(**kwargs: object) -> Callable[[F], F]:
    return _replace_entry(**kwargs)


class _PluginDecorators:
    @staticmethod
    def entry(**kwargs: object) -> Callable[[F], F]:
        return plugin_entry(**kwargs)

    @staticmethod
    def event(**kwargs: object) -> Callable[[F], F]:
        return on_event(**kwargs)

    @staticmethod
    def hook(**kwargs: object) -> Callable[[F], F]:
        return hook(**kwargs)

    @staticmethod
    def lifecycle(**kwargs: object) -> Callable[[F], F]:
        return lifecycle(**kwargs)

    @staticmethod
    def message(**kwargs: object) -> Callable[[F], F]:
        return message(**kwargs)

    @staticmethod
    def timer(**kwargs: object) -> Callable[[F], F]:
        return timer_interval(**kwargs)

    @staticmethod
    def custom_event(**kwargs: object) -> Callable[[F], F]:
        return custom_event(**kwargs)


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
