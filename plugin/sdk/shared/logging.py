"""Shared logging facade for SDK v2.

This module standardizes the logger contract and helper entrypoints used across
`plugin`, `extension`, and `adapter` facades.
"""

from __future__ import annotations

from typing import Final

from plugin.logging_config import (
    LogLevel,
    configure_default_logger,
    format_log_text,
    get_logger,
    intercept_standard_logging,
    setup_logging,
)
from plugin.sdk.shared.core.types import LoggerLike

SDK_COMPONENT_ROOT: Final[str] = "sdk"
PLUGIN_COMPONENT_ROOT: Final[str] = "plugin"
EXTENSION_COMPONENT_ROOT: Final[str] = "extension"
ADAPTER_COMPONENT_ROOT: Final[str] = "adapter"


def build_component_name(root: str, identifier: str, suffix: str | None = None) -> str:
    component = f"{root}.{identifier}"
    if suffix:
        clean_suffix = suffix.strip(".")
        if clean_suffix:
            component = f"{component}.{clean_suffix}"
    return component


def get_sdk_logger(component: str = SDK_COMPONENT_ROOT) -> LoggerLike:
    return get_logger(component)


def setup_sdk_logging(component: str = SDK_COMPONENT_ROOT, *, level: LogLevel | None = None, force: bool = False) -> None:
    setup_logging(component=component, level=level, force=force)


def configure_sdk_default_logger(level: str = "INFO") -> None:
    configure_default_logger(level=level)


def get_plugin_logger(plugin_id: str, suffix: str | None = None) -> LoggerLike:
    return get_logger(build_component_name(PLUGIN_COMPONENT_ROOT, plugin_id, suffix))


def get_extension_logger(extension_id: str, suffix: str | None = None) -> LoggerLike:
    return get_logger(build_component_name(EXTENSION_COMPONENT_ROOT, extension_id, suffix))


def get_adapter_logger(adapter_id: str, suffix: str | None = None) -> LoggerLike:
    return get_logger(build_component_name(ADAPTER_COMPONENT_ROOT, adapter_id, suffix))


__all__ = [
    "LoggerLike",
    "LogLevel",
    "SDK_COMPONENT_ROOT",
    "PLUGIN_COMPONENT_ROOT",
    "EXTENSION_COMPONENT_ROOT",
    "ADAPTER_COMPONENT_ROOT",
    "build_component_name",
    "get_sdk_logger",
    "setup_sdk_logging",
    "configure_sdk_default_logger",
    "intercept_standard_logging",
    "format_log_text",
    "get_plugin_logger",
    "get_extension_logger",
    "get_adapter_logger",
]
