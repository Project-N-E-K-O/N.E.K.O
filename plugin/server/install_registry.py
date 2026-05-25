from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType


@dataclass(frozen=True)
class InstallKindRegistration:
    entry_id: str
    label: str
    queued_message: str
    entry_timeout: float = 600.0


@dataclass(frozen=True)
class InstallPluginRegistration:
    plugin_id: str
    install_kinds: Mapping[str, InstallKindRegistration]
    ui_i18n_dir: Path | None = None
    tutorial_enabled: bool = False


_install_plugin_registry: dict[str, InstallPluginRegistration] = {}
_tutorial_migration_hooks: dict[str, list[Callable[[Path], None]]] | list[Callable[[Path], None]] = {}


def normalize_registered_plugin_id(plugin_id: str) -> str:
    normalized = str(plugin_id or "").strip()
    if not normalized or ".." in normalized or "/" in normalized or "\\" in normalized:
        raise ValueError(f"invalid plugin id: {plugin_id!r}")
    return normalized


def register_install_plugin(
    plugin_id: str,
    *,
    install_kinds: Mapping[str, InstallKindRegistration],
    ui_i18n_dir: Path | str | None = None,
    tutorial_enabled: bool = False,
) -> None:
    normalized_plugin_id = normalize_registered_plugin_id(plugin_id)
    normalized_kinds: dict[str, InstallKindRegistration] = {}
    for raw_kind, registration in install_kinds.items():
        normalized_kind = str(raw_kind or "").strip().lower()
        if not normalized_kind:
            raise ValueError("install kind must not be empty")
        if not isinstance(registration, InstallKindRegistration):
            raise TypeError("install kind registrations must use InstallKindRegistration")
        if not str(registration.entry_id or "").strip():
            raise ValueError(f"install entry_id for kind {normalized_kind!r} must not be empty")
        normalized_kinds[normalized_kind] = registration

    _install_plugin_registry[normalized_plugin_id] = InstallPluginRegistration(
        plugin_id=normalized_plugin_id,
        install_kinds=MappingProxyType(normalized_kinds),
        ui_i18n_dir=Path(ui_i18n_dir).resolve() if ui_i18n_dir is not None else None,
        tutorial_enabled=bool(tutorial_enabled),
    )


def get_install_plugin_registration(plugin_id: str) -> InstallPluginRegistration | None:
    return _install_plugin_registry.get(normalize_registered_plugin_id(plugin_id))


def register_tutorial_migration_hook(
    hook: Callable[[Path], None],
    *,
    plugin_id: str = "",
) -> None:
    normalized_plugin_id = normalize_registered_plugin_id(plugin_id) if plugin_id else ""
    # Some tests monkeypatch the pre-plugin registry shape; keep that compatibility
    # path explicit while production code uses a plugin-id keyed hook map.
    if isinstance(_tutorial_migration_hooks, list):
        if hook not in _tutorial_migration_hooks:
            _tutorial_migration_hooks.append(hook)
        return
    hooks = _tutorial_migration_hooks.setdefault(normalized_plugin_id, [])
    if hook not in hooks:
        hooks.append(hook)


def tutorial_migration_hooks_for(plugin_id: str) -> list[Callable[[Path], None]]:
    if isinstance(_tutorial_migration_hooks, list):
        return list(_tutorial_migration_hooks)
    return [
        *_tutorial_migration_hooks.get("", []),
        *_tutorial_migration_hooks.get(plugin_id, []),
    ]
