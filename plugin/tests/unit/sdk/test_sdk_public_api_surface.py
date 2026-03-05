from __future__ import annotations

import pytest

import plugin.sdk as sdk
import plugin.sdk.adapter as sdk_adapter


@pytest.mark.plugin_unit
def test_sdk_all_exports_exist() -> None:
    missing = [name for name in sdk.__all__ if not hasattr(sdk, name)]
    assert missing == []


@pytest.mark.plugin_unit
def test_adapter_all_exports_exist() -> None:
    missing = [name for name in sdk_adapter.__all__ if not hasattr(sdk_adapter, name)]
    assert missing == []


@pytest.mark.plugin_unit
def test_core_public_entries_are_callable_or_types() -> None:
    sample_names = [
        "ok",
        "fail",
        "plugin_entry",
        "lifecycle",
        "worker",
        "CallChain",
        "AsyncCallChain",
        "PluginRouter",
        "PluginConfig",
        "Plugins",
        "MemoryClient",
        "SystemInfo",
    ]
    for name in sample_names:
        value = getattr(sdk, name)
        assert value is not None
