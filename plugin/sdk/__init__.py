"""SDK v2 root namespace.

The root package is intentionally conservative: it provides namespace-level
navigation for the primary facades plus SDK-wide constants/version metadata.
Developer-facing APIs should normally be imported from one of:
- `plugin.sdk.plugin`   — standard plugin development (most common)
- `plugin.sdk.extension` — deprecated compatibility surface for existing extensions
- `plugin.sdk.adapter`   — adapter development (bridge external protocols)

The `shared` subpackage is an internal implementation detail and should NOT be
imported directly by plugin developers.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

from . import adapter, plugin
from .shared.constants import (
    EVENT_META_ATTR,
    HOOK_META_ATTR,
    NEKO_PLUGIN_META_ATTR,
    NEKO_PLUGIN_TAG,
    PERSIST_ATTR,
)
from .shared.constants import SDK_VERSION


def __getattr__(name: str) -> ModuleType:
    if name == "extension":
        module = import_module(".extension", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "plugin",
    "extension",
    "adapter",
    "SDK_VERSION",
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "EVENT_META_ATTR",
    "HOOK_META_ATTR",
    "PERSIST_ATTR",
]
