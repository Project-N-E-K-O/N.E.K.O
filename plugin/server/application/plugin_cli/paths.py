from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PluginCliRootId = Literal["builtin", "managed", "user"]


@dataclass(frozen=True, slots=True)
class PluginCliPathPolicy:
    """Explicit filesystem policy for plugin package management."""

    builtin_plugins_root: Path
    user_plugins_root: Path
    package_artifacts_root: Path
    package_profiles_root: Path
    managed_plugins_root: Path | None = None

    @classmethod
    def from_settings(cls) -> "PluginCliPathPolicy":
        """Build policy from plugin.settings only.

        The settings module is imported lazily so tests and packaged-runtime
        bootstrapping can override settings before each operation.
        """

        from plugin import settings

        legacy_root = Path(settings.USER_PLUGIN_CONFIG_ROOT).expanduser().resolve()
        configured_managed_root = Path(
            settings.MANAGED_PLUGIN_INSTALLATIONS_ROOT
        ).expanduser().resolve()
        return cls(
            builtin_plugins_root=Path(settings.BUILTIN_PLUGIN_CONFIG_ROOT).expanduser().resolve(),
            user_plugins_root=legacy_root,
            package_artifacts_root=Path(settings.USER_PLUGIN_PACKAGES_ROOT).expanduser().resolve(),
            package_profiles_root=Path(settings.USER_PACKAGE_PROFILES_ROOT).expanduser().resolve(),
            managed_plugins_root=(
                None if configured_managed_root == legacy_root else configured_managed_root
            ),
        )

    @property
    def install_plugins_root(self) -> Path:
        return (self.managed_plugins_root or self.user_plugins_root).resolve(strict=False)

    @property
    def build_source_roots(self) -> tuple[tuple[PluginCliRootId, Path], ...]:
        roots: list[tuple[PluginCliRootId, Path]] = []
        seen: set[Path] = set()
        configured_roots: list[tuple[PluginCliRootId, Path]] = [
            ("builtin", self.builtin_plugins_root)
        ]
        if self.managed_plugins_root is not None:
            configured_roots.append(("managed", self.managed_plugins_root))
        configured_roots.append(("user", self.user_plugins_root))
        for root_id, root in configured_roots:
            resolved = root.resolve(strict=False)
            if resolved in seen:
                continue
            roots.append((root_id, resolved))
            seen.add(resolved)
        return tuple(roots)

    def plugin_root(self, root_id: PluginCliRootId) -> Path:
        if root_id == "builtin":
            return self.builtin_plugins_root
        if root_id == "managed":
            return self.install_plugins_root
        if root_id == "user":
            return self.user_plugins_root
        raise ValueError(f"unsupported plugin root_id: {root_id!r}")
