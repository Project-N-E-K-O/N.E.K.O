from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PluginCliRootId = Literal["builtin", "user"]


@dataclass(frozen=True, slots=True)
class PluginCliPathPolicy:
    """Explicit filesystem policy for plugin package management."""

    builtin_plugins_root: Path
    user_plugins_root: Path
    package_artifacts_root: Path
    package_profiles_root: Path

    @classmethod
    def from_settings(cls) -> "PluginCliPathPolicy":
        """Build policy from plugin.settings only.

        The settings module is imported lazily so tests and packaged-runtime
        bootstrapping can override settings before each operation.
        """

        from plugin import settings

        return cls(
            builtin_plugins_root=Path(settings.BUILTIN_PLUGIN_CONFIG_ROOT).expanduser().resolve(),
            user_plugins_root=Path(settings.USER_PLUGIN_CONFIG_ROOT).expanduser().resolve(),
            package_artifacts_root=Path(settings.USER_PLUGIN_PACKAGES_ROOT).expanduser().resolve(),
            package_profiles_root=Path(settings.USER_PACKAGE_PROFILES_ROOT).expanduser().resolve(),
        )

    @property
    def build_source_roots(self) -> tuple[tuple[PluginCliRootId, Path], ...]:
        roots: list[tuple[PluginCliRootId, Path]] = []
        seen: set[Path] = set()
        for root_id, root in (
            ("builtin", self.builtin_plugins_root),
            ("user", self.user_plugins_root),
        ):
            resolved = root.resolve(strict=False)
            if resolved in seen:
                continue
            roots.append((root_id, resolved))
            seen.add(resolved)
        return tuple(roots)

    def plugin_root(self, root_id: PluginCliRootId) -> Path:
        if root_id == "builtin":
            return self.builtin_plugins_root
        if root_id == "user":
            return self.user_plugins_root
        raise ValueError(f"unsupported plugin root_id: {root_id!r}")
