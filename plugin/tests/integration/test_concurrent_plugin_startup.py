"""Real metadata scans, PluginHost/ZMQ startup, and operation-lock release."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from plugin.core import host as host_module
from plugin.core.state import state
from plugin.server.application.plugins import lifecycle_service, operation_lock


_PLUGIN_SOURCE = '''
import asyncio
from pathlib import Path
from plugin.sdk.plugin import NekoPluginBase, Ok, lifecycle, neko_plugin, plugin_entry

@neko_plugin
class ConcurrentPlugin(NekoPluginBase):
    @lifecycle(id="startup")
    async def startup(self, **kwargs):
        root = Path(__file__).parent
        (root / "ready").touch()
        async with asyncio.timeout(30):
            while not (root.parent / "release").exists():
                await asyncio.sleep(0.01)
        return Ok({"status": "ready"})

    @plugin_entry(id="ping", name="Ping", description="Isolated startup probe")
    async def ping(self):
        return Ok({"ready": True})
'''


@pytest.mark.plugin_integration
@pytest.mark.parametrize("count", [2, 8])
async def test_real_autostart_keeps_initialization_concurrent_and_releases_operation_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int,
):
    sources = tmp_path / "sources"
    records = {}
    for index in range(count):
        plugin_id = f"concurrent_{index}"
        directory = sources / plugin_id
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_text(_PLUGIN_SOURCE, encoding="utf-8")
        config = directory / "plugin.toml"
        config.write_text(
            f"[plugin]\nid='{plugin_id}'\nname='{plugin_id}'\n"
            f"entry='plugins.{plugin_id}:ConcurrentPlugin'\n"
            "[plugin.store]\nenabled=false\n"
            "[plugin_runtime]\nenabled=true\nauto_start=true\ntimeout=30\nstartup_failure='fail'\n",
            encoding="utf-8",
        )
        records[plugin_id] = {"id": plugin_id, "config_path": str(config), "type": "plugin"}

    monkeypatch.setattr(state, "plugins", records)
    monkeypatch.setattr(state, "plugin_hosts", {})
    monkeypatch.setattr(state, "event_handlers", {})
    state.invalidate_snapshot_cache()
    monkeypatch.setattr(host_module, "_refresh_child_storage_layout_env", lambda _: None)
    monkeypatch.setenv("NEKO_PLUGIN_OPERATION_LOCK_PATH", str(tmp_path / "operations.lock"))
    monkeypatch.setattr(operation_lock, "_reload_install_source_manager_sync", lambda: None)
    monkeypatch.setattr(lifecycle_service.development_store, "registration_for_plugin_sync", lambda _: None)
    service = lifecycle_service.PluginLifecycleService()
    batch = asyncio.create_task(service.start_plugins_batch(
        list(records), concurrency=count, refresh_registry=False,
    ))
    try:
        # All real child processes must enter startup before any is allowed
        # to finish. Serializing imports/handshakes would fail this assertion.
        async with asyncio.timeout(20):
            while not all((sources / plugin_id / "ready").exists() for plugin_id in records):
                if batch.done():
                    pytest.fail(f"batch ended before all startups entered: {batch.result()}")
                await asyncio.sleep(0.01)
        (sources / "release").touch()
        result = await asyncio.wait_for(batch, 8)
        assert sorted(result["started"]) == sorted(records)
        assert result["failed"] == []
        for plugin_id in records:
            host = state.plugin_hosts[plugin_id]
            assert host.is_alive()
            assert (await host.trigger("ping", {}, timeout=3))["ready"] is True
        # An ordinary lifecycle request can acquire the lock after the wave.
        repeated = await asyncio.wait_for(service.start_plugin("concurrent_0", refresh_registry=False), 3)
        assert repeated["already_running"] is True
    finally:
        (sources / "release").touch()
        await asyncio.gather(batch, return_exceptions=True)
        for host in list(state.plugin_hosts.values()):
            await host.shutdown(timeout=1)
        state.invalidate_snapshot_cache()
