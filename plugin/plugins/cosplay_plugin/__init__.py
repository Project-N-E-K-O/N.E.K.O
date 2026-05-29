"""Cosplay plugin package entry point.

The plugin runtime resolves ``plugin.toml``'s
``entry = "plugin.plugins.cosplay_plugin:CosplayPlugin"`` against this module,
so re-exporting ``CosplayPlugin`` (and its ``CosplayBridgePlugin`` alias) from
``plugin_core`` keeps the public import surface unchanged after the PR2 split.

Before the split, ``__init__.py`` was a 7,500-line monolith whose top-level
imports (``time``, ``json_copy``, ``build_summarize_context``,
``MemoryReaderManager``, ...) became attributes of the package object.
Several tests and external callers reach into that surface
(``monkeypatch.setattr(cosplay_plugin_module, "build_summarize_context", ...)``,
``from plugin.plugins.cosplay_plugin import CosplayPluginConfigService``),
so we star-import from ``plugin_core`` here to keep the original public
attribute surface intact. The explicit private re-exports below cover the
two underscore-prefixed helpers tests monkeypatch - star-import skips them.
``__all__`` stays narrow (only the three classes external code is meant to
depend on) so ``from plugin.plugins.cosplay_plugin import *`` still yields a
curated surface.
"""
from __future__ import annotations

from pathlib import Path

from .plugin_config_service import CosplayPluginConfigService
from .plugin_core import *  # noqa: F401, F403 - preserve original package surface
from .plugin_core import (  # explicit: star-import skips underscore names
    _after_advance_screen_refresh_needed,
    _open_url_in_browser,
)


def _register_install_routes() -> None:
    from plugin.server.install_registry import (
        InstallKindRegistration,
        register_install_plugin,
    )

    register_install_plugin(
        "cosplay_plugin",
        install_kinds={
            "textractor": InstallKindRegistration(
                entry_id="cosplay_install_textractor",
                label="Textractor",
                queued_message="Textractor install queued",
            ),
            "rapidocr_models": InstallKindRegistration(
                entry_id="cosplay_download_rapidocr_models",
                label="RapidOCR Models",
                queued_message="RapidOCR model download queued",
            ),
        },
        ui_i18n_dir=Path(__file__).resolve().parent / "i18n" / "ui",
        tutorial_enabled=True,
    )


def _register_tutorial_migration_hook() -> None:
    from plugin.server.install_registry import register_tutorial_migration_hook

    from ._tutorial_migration import copy_legacy_tutorial_progress_if_missing

    register_tutorial_migration_hook(
        copy_legacy_tutorial_progress_if_missing,
        plugin_id="cosplay_plugin",
    )


try:
    _register_install_routes()
except Exception:  # noqa: BLE001 - route registration should not block package import.
    from plugin.logging_config import get_logger

    get_logger("cosplay.install_routes").warning(
        "cosplay install route registration failed",
        exc_info=True,
    )

try:
    _register_tutorial_migration_hook()
except Exception:  # noqa: BLE001 - migration hook registration should not block package import.
    from plugin.logging_config import get_logger

    get_logger("cosplay.install_routes").warning(
        "cosplay tutorial migration hook registration failed",
        exc_info=True,
    )

__all__ = ["CosplayBridgePlugin", "CosplayPlugin", "CosplayPluginConfigService"]
