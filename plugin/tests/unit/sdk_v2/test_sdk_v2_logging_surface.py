from __future__ import annotations

from plugin.sdk_v2.adapter import runtime as adapter_runtime
from plugin.sdk_v2.extension import runtime as extension_runtime
from plugin.sdk_v2.plugin import runtime as plugin_runtime
from plugin.sdk_v2.shared import logging as shared_logging


def test_shared_logging_exports() -> None:
    assert shared_logging.SDK_COMPONENT_ROOT == "sdk_v2"
    assert shared_logging.PLUGIN_COMPONENT_ROOT == "plugin"
    assert shared_logging.EXTENSION_COMPONENT_ROOT == "extension"
    assert shared_logging.ADAPTER_COMPONENT_ROOT == "adapter"
    for name in shared_logging.__all__:
        assert hasattr(shared_logging, name)


def test_runtime_logging_exports_are_aligned() -> None:
    for runtime_mod, getter_name in (
        (plugin_runtime, "get_plugin_logger"),
        (extension_runtime, "get_extension_logger"),
        (adapter_runtime, "get_adapter_logger"),
    ):
        assert runtime_mod.LogLevel is shared_logging.LogLevel
        assert runtime_mod.LoggerLike is shared_logging.LoggerLike
        assert runtime_mod.get_sdk_logger is shared_logging.get_sdk_logger
        assert runtime_mod.setup_sdk_logging is shared_logging.setup_sdk_logging
        assert runtime_mod.configure_sdk_default_logger is shared_logging.configure_sdk_default_logger
        assert runtime_mod.intercept_standard_logging is shared_logging.intercept_standard_logging
        assert runtime_mod.format_log_text is shared_logging.format_log_text
        assert getattr(runtime_mod, getter_name) is getattr(shared_logging, getter_name)
