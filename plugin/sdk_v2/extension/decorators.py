"""Extension decorators for SDK v2.

The extension facade keeps a narrower semantic layer than `plugin.decorators`.
It exposes extension-oriented entry and hook wrappers without leaking internal
implementation details.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

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
    timing: str
    priority: int


def _not_impl(*_args: object, **_kwargs: object) -> None:
    raise NotImplementedError("sdk_v2 contract-only facade: extension.decorators not implemented")


def extension_entry(
    id: str | None = None,
    *,
    name: str | None = None,
    description: str = "",
    timeout: float | None = None,
) -> Callable[[F], F]:
    _not_impl(id, name, description, timeout)

    def decorator(fn: F) -> F:
        _not_impl(fn)
        return fn

    return decorator


def extension_hook(*, target: str = "*", timing: str = "before", priority: int = 0) -> Callable[[F], F]:
    _not_impl(target, timing, priority)

    def decorator(fn: F) -> F:
        _not_impl(fn)
        return fn

    return decorator


class _ExtensionDecorators:
    @staticmethod
    def entry(**kwargs: object) -> Callable[[F], F]:
        return extension_entry(**kwargs)

    @staticmethod
    def hook(**kwargs: object) -> Callable[[F], F]:
        return extension_hook(**kwargs)


extension = _ExtensionDecorators()

__all__ = [
    "EXTENSION_ENTRY_META",
    "EXTENSION_HOOK_META",
    "ExtensionEntryMeta",
    "ExtensionHookMeta",
    "extension_entry",
    "extension_hook",
    "extension",
]
