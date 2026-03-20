"""Extension decorators for SDK v2."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar, cast

from plugin.sdk_v2.shared.core.hooks import HookTiming

F = TypeVar("F", bound=Callable[..., object])

EXTENSION_ENTRY_META = "__extension_entry_meta__"
EXTENSION_HOOK_META = "__extension_hook_meta__"


@dataclass(slots=True, frozen=True)
class ExtensionEntryMeta:
    id: str | None
    name: str | None
    description: str
    timeout: float | None


@dataclass(slots=True, frozen=True)
class ExtensionHookMeta:
    target: str
    timing: HookTiming
    priority: int


def _not_impl(*_args: object, **_kwargs: object) -> None:
    return None


def extension_entry(id: str | None = None, *, name: str | None = None, description: str = "", timeout: float | None = None) -> Callable[[F], F]:
    def decorator(fn: F) -> F:
        setattr(fn, EXTENSION_ENTRY_META, ExtensionEntryMeta(id=id, name=name, description=description, timeout=timeout))
        return fn
    return decorator


def _normalize_timing(timing: HookTiming | str) -> HookTiming:
    if timing in {"before", "after", "around", "replace"}:
        return cast(HookTiming, timing)
    raise ValueError("timing must be one of: before, after, around, replace")


def extension_hook(*, target: str = "*", timing: HookTiming | str = "before", priority: int = 0) -> Callable[[F], F]:
    normalized_timing = _normalize_timing(timing)

    def decorator(fn: F) -> F:
        setattr(fn, EXTENSION_HOOK_META, ExtensionHookMeta(target=target, timing=normalized_timing, priority=priority))
        return fn
    return decorator


class _ExtensionDecorators:
    @staticmethod
    def entry(
        id: str | None = None,
        *,
        name: str | None = None,
        description: str = "",
        timeout: float | None = None,
    ) -> Callable[[F], F]:
        return cast(Callable[[F], F], extension_entry(id=id, name=name, description=description, timeout=timeout))

    @staticmethod
    def hook(
        *,
        target: str = "*",
        timing: HookTiming | str = "before",
        priority: int = 0,
    ) -> Callable[[F], F]:
        return cast(Callable[[F], F], extension_hook(target=target, timing=timing, priority=priority))


extension = _ExtensionDecorators()

__all__ = [
    "EXTENSION_ENTRY_META",
    "EXTENSION_HOOK_META",
    "ExtensionEntryMeta",
    "ExtensionHookMeta",
    "extension",
    "extension_entry",
    "extension_hook",
]
