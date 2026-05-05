"""Backward-compatible shim for plugin source helpers."""

from __future__ import annotations

from ..core.plugin_source import *  # noqa: F401,F403
from ..core.plugin_source import extract_runtime_config, load_plugin_source
