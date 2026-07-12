from __future__ import annotations

import importlib.util
REMOVED_ROOT_SDK_MODULES = (
    "plugin.sdk.base",
    "plugin.sdk.decorators",
    "plugin.sdk.events",
    "plugin.sdk.logger",
    "plugin.sdk.version",
    "plugin.sdk.extension",
)


def test_removed_root_sdk_modules_are_not_importable() -> None:
    for removed_module in REMOVED_ROOT_SDK_MODULES:
        assert importlib.util.find_spec(removed_module) is None
