"""Extension decorators contracts for SDK v2."""

from __future__ import annotations

from typing import Callable, TypeVar

F = TypeVar("F", bound=Callable[..., object])


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


__all__ = ["extension_entry", "extension_hook"]
