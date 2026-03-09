"""SDK v2 root namespace.

The root package is intentionally conservative: it provides namespace-level
navigation for the primary facades plus SDK-wide constants/version metadata.
Developer-facing APIs should normally be imported from one of:
- `plugin.sdk_v2.plugin`
- `plugin.sdk_v2.extension`
- `plugin.sdk_v2.adapter`
- `plugin.sdk_v2.shared` (advanced)
"""

from __future__ import annotations

from . import adapter, extension, plugin, shared
from .shared.constants import (
    CHECKPOINT_ATTR,
    EVENT_META_ATTR,
    HOOK_META_ATTR,
    NEKO_PLUGIN_META_ATTR,
    NEKO_PLUGIN_TAG,
    PERSIST_ATTR,
)
from .shared.constants import SDK_VERSION

__all__ = [
    "plugin",
    "extension",
    "adapter",
    "shared",
    "SDK_VERSION",
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "EVENT_META_ATTR",
    "HOOK_META_ATTR",
    "PERSIST_ATTR",
    "CHECKPOINT_ATTR",
]
