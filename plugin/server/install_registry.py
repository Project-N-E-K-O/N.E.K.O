from __future__ import annotations

import importlib.util
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
import threading
from types import MappingProxyType

from plugin.logging_config import get_logger


logger = get_logger("server.install_registry")


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
_builtin_install_registry_lock = threading.RLock()


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


def _plugins_root() -> Path:
    return Path(__file__).resolve().parents[1] / "plugins"


def _plugin_module_available(plugin_id: str) -> bool:
    try:
        return importlib.util.find_spec(f"plugin.plugins.{plugin_id}") is not None
    except ImportError:
        return False


def _copy_legacy_galgame_tutorial_progress_if_missing(store_path: Path) -> None:
    try:
        from plugin.plugins.galgame_plugin._tutorial_migration import (
            copy_legacy_tutorial_progress_if_missing,
        )
    except ModuleNotFoundError as exc:
        missing_name = str(getattr(exc, "name", "") or "")
        optional_missing = {
            "plugin.plugins",
            "plugin.plugins.galgame_plugin",
            "plugin.plugins.galgame_plugin._tutorial_migration",
        }
        if missing_name in optional_missing:
            logger.warning(
                "galgame tutorial migration module unavailable; skipping legacy migration: {}",
                exc,
            )
            return
        raise

    copy_legacy_tutorial_progress_if_missing(store_path)


def bootstrap_builtin_install_plugins() -> None:
    with _builtin_install_registry_lock:
        plugins_root = _plugins_root()
        if (
            "galgame_plugin" not in _install_plugin_registry
            and _plugin_module_available("galgame_plugin")
        ):
            register_install_plugin(
                "galgame_plugin",
                install_kinds={
                    "textractor": InstallKindRegistration(
                        entry_id="galgame_install_textractor",
                        label="Textractor",
                        queued_message="Textractor install queued",
                    ),
                    "rapidocr_models": InstallKindRegistration(
                        entry_id="galgame_download_rapidocr_models",
                        label="RapidOCR Models",
                        queued_message="RapidOCR model download queued",
                    ),
                },
                ui_i18n_dir=plugins_root / "galgame_plugin" / "i18n" / "ui",
                tutorial_enabled=True,
            )
        if (
            "study_companion" not in _install_plugin_registry
            and _plugin_module_available("study_companion")
        ):
            register_install_plugin(
                "study_companion",
                install_kinds={
                    "rapidocr_models": InstallKindRegistration(
                        entry_id="study_download_rapidocr_models",
                        label="RapidOCR Models",
                        queued_message="RapidOCR model download queued",
                    ),
                    "tesseract": InstallKindRegistration(
                        entry_id="study_install_tesseract",
                        label="Tesseract",
                        queued_message="Tesseract install queued",
                    ),
                },
                ui_i18n_dir=plugins_root / "study_companion" / "i18n",
                tutorial_enabled=True,
            )
        if _plugin_module_available("galgame_plugin"):
            register_tutorial_migration_hook(
                _copy_legacy_galgame_tutorial_progress_if_missing,
                plugin_id="galgame_plugin",
            )


def get_install_plugin_registration(plugin_id: str) -> InstallPluginRegistration | None:
    normalized_plugin_id = normalize_registered_plugin_id(plugin_id)
    bootstrap_builtin_install_plugins()
    return _install_plugin_registry.get(normalized_plugin_id)



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
    normalized_plugin_id = normalize_registered_plugin_id(plugin_id) if plugin_id else ""
    if isinstance(_tutorial_migration_hooks, list):
        return list(_tutorial_migration_hooks)
    return [
        *_tutorial_migration_hooks.get("", []),
        *_tutorial_migration_hooks.get(normalized_plugin_id, []),
    ]
