"""SDK v2 root namespace.

The root package is intentionally conservative: it provides namespace-level
navigation for the primary facades plus SDK-wide constants/version metadata.
Developer-facing APIs should normally be imported from one of:
- `plugin.sdk.plugin`   — standard plugin development (most common)
- `plugin.sdk.adapter`   — adapter development (bridge external protocols)

The `shared` subpackage is an internal implementation detail and should NOT be
imported directly by plugin developers.
"""

from __future__ import annotations

from .shared.constants import (
    EVENT_META_ATTR,
    HOOK_META_ATTR,
    NEKO_PLUGIN_META_ATTR,
    NEKO_PLUGIN_TAG,
    PERSIST_ATTR,
)
from .shared.constants import SDK_VERSION

__all__ = [
    "plugin",
    "adapter",
    "SDK_VERSION",
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "EVENT_META_ATTR",
    "HOOK_META_ATTR",
    "PERSIST_ATTR",
]


def __getattr__(name: str):
    # ``plugin`` / ``adapter`` facades load on first access: a plugin process
    # importing ``plugin.sdk.plugin`` should not also pay for the adapter SDK.
    if name in ("plugin", "adapter"):
        import importlib

        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | {"plugin", "adapter"})
