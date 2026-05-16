"""SDK v2 root namespace.

The root package is intentionally conservative: it provides namespace-level
navigation for the primary facades plus SDK-wide constants/version metadata.
Developer-facing APIs should normally be imported from one of:
- `plugin.sdk.plugin`   — standard plugin development (most common)
- `plugin.sdk.extension` — extension development (add routes to existing plugins)
- `plugin.sdk.adapter`   — adapter development (bridge external protocols)

The `shared` subpackage is an internal implementation detail and should NOT be
imported directly by plugin developers.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from . import adapter, extension, plugin
from .shared.constants import (
    EVENT_META_ATTR,
    HOOK_META_ATTR,
    NEKO_PLUGIN_META_ATTR,
    NEKO_PLUGIN_TAG,
    PERSIST_ATTR,
)
from .shared.constants import SDK_VERSION


def get_config_manager(*args: Any, **kwargs: Any) -> Any:
    return import_module("utils.config_manager").get_config_manager(*args, **kwargs)


def get_global_language_full(*args: Any, **kwargs: Any) -> str:
    return import_module("utils.language_utils").get_global_language_full(*args, **kwargs)


def create_chat_llm(*args: Any, **kwargs: Any) -> Any:
    return import_module("utils.llm_client").create_chat_llm(*args, **kwargs)


def robust_json_loads(*args: Any, **kwargs: Any) -> Any:
    return import_module("utils.file_utils").robust_json_loads(*args, **kwargs)


def set_call_type(*args: Any, **kwargs: Any) -> None:
    return import_module("utils.token_tracker").set_call_type(*args, **kwargs)


def __getattr__(name: str) -> Any:
    if name == "ChatOpenAI":
        return import_module("utils.llm_client").ChatOpenAI
    raise AttributeError(name)


__all__ = [
    "plugin",
    "extension",
    "adapter",
    "get_config_manager",
    "get_global_language_full",
    "create_chat_llm",
    "ChatOpenAI",
    "robust_json_loads",
    "set_call_type",
    "SDK_VERSION",
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "EVENT_META_ATTR",
    "HOOK_META_ATTR",
    "PERSIST_ATTR",
]
