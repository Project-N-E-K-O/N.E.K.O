"""sdk_v2.plugin.base

SDD contract-only facade for plugin base types.

This module defines stable API contracts (names, signatures, data shape hints,
boundary semantics) but intentionally has no runtime implementation yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

NEKO_PLUGIN_META_ATTR = "__neko_plugin_meta__"
NEKO_PLUGIN_TAG = "__neko_plugin__"


@dataclass(slots=True)
class PluginMeta:
    """Contract metadata for a plugin declaration.

    Boundaries:
    - `id` and `name` are required non-empty logical identifiers.
    - version fields are semantic-version-like strings (validation in implementation).
    - compatibility fields are declarative and evaluated by host/runtime.
    """

    id: str
    name: str
    version: str = "0.0.0"
    sdk_version: str = "2.0.0a0"
    description: str = ""
    sdk_recommended: Optional[str] = None
    sdk_supported: Optional[str] = None
    sdk_untested: Optional[str] = None
    sdk_conflicts: List[str] = field(default_factory=list)


class NekoPluginBase:
    """Contract-only plugin base class.

    Core lifecycle contract:
    - Subclasses are instantiated with a context object.
    - Async entry/lifecycle methods are discovered from decorators.
    - State/store/db/logger helpers are lazily wired during startup.

    Boundary expectations:
    - `__freezable__` contains serializable attribute names.
    - `__persist_mode__` in {"off", "manual", "auto"}.
    - Unknown modes are rejected by implementation.
    """

    __freezable__: List[str] = []
    __persist_mode__: str = "off"

    # Attached by runtime wiring in concrete implementation.
    ctx: Any
    config: Any
    plugins: Any
    store: Any
    db: Any

    def __init__(self, ctx: Any):
        self.ctx = ctx
        raise NotImplementedError("sdk_v2 contract-only facade: base runtime not implemented")

    def get_input_schema(self) -> Dict[str, Any]:
        """Return plugin-level input schema.

        Returns:
        - JSON-schema-like object, default `{}` when unspecified.
        """
        raise NotImplementedError("sdk_v2 contract-only facade: base runtime not implemented")

    def include_router(self, router: Any, *, prefix: str = "") -> None:
        """Mount a router onto this plugin.

        Boundary constraints:
        - `prefix` is optional but must be a string.
        - duplicate entry ids are conflict-handled by implementation policy.
        """
        raise NotImplementedError("sdk_v2 contract-only facade: base runtime not implemented")

    def exclude_router(self, router: Any | str) -> bool:
        """Unmount a router by instance or name.

        Returns:
        - `True` if removed, `False` if not found.
        """
        raise NotImplementedError("sdk_v2 contract-only facade: base runtime not implemented")

    def enable_file_logging(
        self,
        *,
        log_dir: str | Path | None = None,
        log_level: str = "INFO",
        max_bytes: int | None = None,
        backup_count: int | None = None,
    ) -> Any:
        """Create or return plugin-scoped file logger.

        Boundary constraints:
        - `log_level` must be supported by logger backend.
        - size/rotation fields must be positive when provided.
        """
        raise NotImplementedError("sdk_v2 contract-only facade: base runtime not implemented")


__all__ = [
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "NekoPluginBase",
    "PluginMeta",
]
