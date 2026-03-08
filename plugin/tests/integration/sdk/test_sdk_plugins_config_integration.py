from __future__ import annotations

import pytest

from plugin.sdk.config import PluginConfig
from plugin.sdk.plugins import PluginCallError, Plugins

from plugin.tests.integration.sdk.test_sdk_real_context_integration import (
    _HostBridge,
    _HostPlugin,
    _build_contexts,
)

@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_plugins_and_config_async_combinations_real_context(tmp_path) -> None:
    contexts = _build_contexts(tmp_path)
    bridge = _HostBridge(
        request_queue=contexts.req_queue,
        response_queues={"caller": contexts.caller._response_queue, "host": contexts.host._response_queue},
        plugins={"host": _HostPlugin(contexts.host), "caller": _HostPlugin(contexts.caller)},
        configs={"caller": {"runtime": {"enabled": True, "level": 1}}},
    )
    await bridge.start()
    try:
        plugins = Plugins(ctx=contexts.caller)
        cfg = PluginConfig(contexts.caller)

        listed = await plugins.list_async()
        assert isinstance(listed, dict)
        assert any(p.get("plugin_id") == "host" for p in listed.get("plugins", []))

        called = await plugins.call_entry_async("host:sum", {"a": 1, "b": 2}, timeout=2.0)
        assert called["value"] == 4
        assert called["after"] is True

        # Config chain in same real context
        before = await cfg.get("runtime.level")
        assert before == 1
        await cfg.set("runtime.level", 2)
        after = await cfg.get("runtime.level")
        assert after == 2

        await plugins.require_async("host")
        with pytest.raises(PluginCallError):
            await plugins.require_async("missing-plugin")
    finally:
        await bridge.stop()
        contexts.caller.close()
        contexts.host.close()
