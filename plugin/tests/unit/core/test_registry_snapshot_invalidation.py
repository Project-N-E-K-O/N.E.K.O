from __future__ import annotations

import copy
import time

import pytest

from plugin._types.models import PluginMeta
from plugin.core import registry as module
from plugin.sdk.plugin.decorators import plugin_entry


class _RegistryCachePlugin:
    @plugin_entry(id="demo_entry", name="Demo Entry")
    async def demo_entry(self) -> dict[str, object]:
        return {"ok": True}


class _CaptureLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def warning(self, message: object, *args: object, **_kwargs: object) -> None:
        self.warnings.append(_render_log_message(message, args))

    def error(self, message: object, *args: object, **_kwargs: object) -> None:
        self.errors.append(_render_log_message(message, args))


def _render_log_message(message: object, args: tuple[object, ...]) -> str:
    rendered = str(message)
    for arg in args:
        rendered = rendered.replace("{}", str(arg), 1)
    return rendered


def test_register_plugin_invalidates_plugins_snapshot_cache() -> None:
    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
        now = time.time()
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache["plugins"] = {"data": {}, "timestamp": now}

        resolved_id = module.register_plugin(
            PluginMeta(
                id="demo_registry",
                name="Demo Registry",
                type="plugin",
                description="",
                version="0.1.0",
                sdk_version="test",
            )
        )

        snapshot = module.state.get_plugins_snapshot_cached()
        assert resolved_id == "demo_registry"
        assert "demo_registry" in snapshot
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.parametrize("plugin_type", ["plugin", "adapter"])
def test_register_plugin_accepts_active_plugin_types(plugin_type: str) -> None:
    plugin_id = f"register_{plugin_type}_type"
    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.pop(plugin_id, None)

        resolved_id = module.register_plugin(
            PluginMeta(id=plugin_id, name="Type Test", type=plugin_type)
        )

        assert resolved_id == plugin_id
        with module.state.acquire_plugins_read_lock():
            assert module.state.plugins[plugin_id]["type"] == plugin_type
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.parametrize(
    "plugin_type",
    [
        pytest.param("script", id="removed-script"),
        pytest.param({"malicious": "script"}, id="non-string-mapping"),
        pytest.param(None, id="missing-type"),
    ],
)
def test_register_plugin_rejects_constructed_unsupported_type(plugin_type: object) -> None:
    plugin_id = "constructed_legacy_script"
    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)
    logger = _CaptureLogger()
    malicious_meta = PluginMeta.model_construct(
        id=plugin_id,
        name="Legacy Script",
        type=plugin_type,
    )

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.pop(plugin_id, None)

        resolved_id = module.register_plugin(malicious_meta, logger=logger)

        assert resolved_id is None
        with module.state.acquire_plugins_read_lock():
            assert plugin_id not in module.state.plugins
        if plugin_type == "script":
            assert any("unsupported type='script'" in message for message in logger.errors)
        assert any("不支持" in message and "未対応" in message for message in logger.errors)
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


def test_register_plugin_keeps_extension_compatible_with_deprecation_warning() -> None:
    plugin_id = "deprecated_extension_type"
    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)
    logger = _CaptureLogger()

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.pop(plugin_id, None)

        resolved_id = module.register_plugin(
            PluginMeta(id=plugin_id, name="Extension", type="extension"),
            logger=logger,
        )

        assert resolved_id == plugin_id
        with module.state.acquire_plugins_read_lock():
            assert module.state.plugins[plugin_id]["type"] == "extension"
        assert any("type='extension' is deprecated" in message for message in logger.warnings)
        assert any("已弃用" in message and "非推奨" in message for message in logger.warnings)
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


def test_scan_static_metadata_invalidates_handlers_snapshot_cache() -> None:
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()
        now = time.time()
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache["handlers"] = {"data": {}, "timestamp": now}

        module.scan_static_metadata(
            "demo_registry",
            _RegistryCachePlugin,
            conf={},
            pdata={},
        )

        snapshot = module.state.get_event_handlers_snapshot_cached()
        assert "demo_registry.demo_entry" in snapshot
    finally:
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()
            module.state.event_handlers.update(handlers_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup
