# Ported from claudian/src/core/settings/defaultSettings.ts
# Original author: Claudian contributors
# License: MIT

"""
Default settings.
"""

from __future__ import annotations

from typing import Any, Dict

from .claudian_settings import ClaudianSettings


DEFAULT_SETTINGS = ClaudianSettings()

DEFAULT_KEYBOARD_NAVIGATION = {
    "scrollUpKey": "w",
    "scrollDownKey": "s",
    "focusInputKey": "i",
}


def get_default_settings() -> ClaudianSettings:
    """Get default settings."""
    return ClaudianSettings()


def get_default_settings_dict() -> Dict[str, Any]:
    """Get default settings as dict."""
    return get_default_settings().to_dict()
