from __future__ import annotations

from plugin.sdk_v2.adapter import runtime as adapter_runtime
from plugin.sdk_v2.extension import runtime as extension_runtime
from plugin.sdk_v2.plugin import runtime as plugin_runtime
from plugin.sdk_v2.shared import runtime_common


def test_runtime_common_exports_are_shared() -> None:
    assert plugin_runtime.COMMON_RUNTIME_EXPORTS == list(runtime_common.__all__)
    assert extension_runtime.COMMON_RUNTIME_EXPORTS == list(runtime_common.__all__)
    assert adapter_runtime.COMMON_RUNTIME_EXPORTS == list(runtime_common.__all__)


def test_runtime_specific_exports_remain_flavor_specific() -> None:
    assert plugin_runtime.get_plugin_logger is not None
    assert "get_plugin_logger" in plugin_runtime.PLUGIN_RUNTIME_EXPORTS
    assert "PluginConfig" in plugin_runtime.PLUGIN_RUNTIME_EXPORTS

    assert "get_extension_logger" in extension_runtime.EXTENSION_RUNTIME_EXPORTS
    assert "ExtensionRuntime" in extension_runtime.EXTENSION_RUNTIME_EXPORTS

    assert "get_adapter_logger" in adapter_runtime.ADAPTER_RUNTIME_EXPORTS
    assert "AdapterGatewayCore" in adapter_runtime.ADAPTER_RUNTIME_EXPORTS
