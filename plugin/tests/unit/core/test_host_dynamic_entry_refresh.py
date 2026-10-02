from __future__ import annotations

import asyncio

import pytest

from plugin._types.exceptions import PluginExecutionError
from plugin.core.host import PluginProcessHost


@pytest.mark.asyncio
@pytest.mark.parametrize("mode, expected_saves", [("auto", 1), ("manual", 0), ("off", 0)])
async def test_host_initializes_and_persists_from_startup_config(tmp_path, mode, expected_saves) -> None:
    config_path = tmp_path / "plugin.toml"
    config_path.write_text(
        "[plugin]\nid='startup_config'\nname='startup'\ntype='plugin'\n"
        "entry='tests.fixtures.plugin_test_startup_config_fixture:StartupConfigFixturePlugin'\n"
        "[plugin.store]\nenabled=true\n"
        f"[plugin_state]\npersist_mode='{mode}'\n",
        encoding="utf-8",
    )
    host = PluginProcessHost(
        plugin_id=f"startup_config_{mode}",
        entry_point="tests.fixtures.plugin_test_startup_config_fixture:StartupConfigFixturePlugin",
        config_path=config_path,
    )
    try:
        await host.start(message_target_queue=asyncio.Queue(), startup_timeout=10.0)
        first = await host.trigger("touch", {}, timeout=2.0)
        second = await host.trigger("touch", {}, timeout=2.0)
        assert first["store_enabled"] is True
        assert first["config"]["plugin_state"]["persist_mode"] == mode
        assert first["saves"] == 0
        assert second["saves"] == expected_saves
    finally:
        await host.shutdown(timeout=1.0)


@pytest.mark.asyncio
async def test_host_rebuilds_entry_map_for_dynamic_entry(tmp_path) -> None:
    config_path = tmp_path / "plugin.toml"
    config_path.write_text("[plugin]\nname='dynamic'\n", encoding="utf-8")

    host = PluginProcessHost(
        plugin_id="dynamic_fixture",
        entry_point="tests.fixtures.plugin_test_dynamic_entry_fixture:DynamicEntryFixturePlugin",
        config_path=config_path,
    )

    try:
        await host.start(message_target_queue=asyncio.Queue())
        result = await host.trigger("late_entry", {}, timeout=2.0)
        assert result == {"ok": True, "source": "dynamic"}
    finally:
        await host.shutdown(timeout=1.0)


@pytest.mark.asyncio
async def test_host_prefers_dynamic_entry_timeout_metadata(tmp_path) -> None:
    config_path = tmp_path / "plugin.toml"
    config_path.write_text("[plugin]\nname='dynamic'\n", encoding="utf-8")

    host = PluginProcessHost(
        plugin_id="dynamic_fixture_timeout",
        entry_point="tests.fixtures.plugin_test_dynamic_entry_fixture:DynamicEntryFixturePlugin",
        config_path=config_path,
    )

    try:
        await host.start(message_target_queue=asyncio.Queue())
        with pytest.raises(PluginExecutionError, match=r"timed out after 0.05s"):
            await host.trigger("slow_entry", {}, timeout=2.0)
    finally:
        await host.shutdown(timeout=1.0)
