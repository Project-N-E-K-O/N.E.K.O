"""Shared SDK v2 building blocks.

`shared` contains reusable lower-level primitives. Some subpackages already have
real implementations, while a few subpackages are still evolving.
"""

from importlib import import_module

# These lightweight errors/Result primitives re-export their child modules.
# Complete the parent before different lazy SDK helpers can acquire its child
# module locks concurrently. Runtime/storage/core implementations stay lazy.
import_module(".models", __name__)

__all__ = [
    "constants",
    "core",
    "i18n",
    "logging",
    "models",
    "runtime",
    "runtime_common",
    "storage",
    "transport",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = import_module(f".{name}", __name__)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
