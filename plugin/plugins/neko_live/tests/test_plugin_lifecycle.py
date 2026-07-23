from types import SimpleNamespace

import pytest

from plugin.plugins.neko_live import NekoLivePlugin
from plugin.plugins.neko_live.core import runtime as runtime_module


@pytest.mark.asyncio
async def test_twitch_authorize_action_logs_config_and_public_result_without_client_id():
    class Logger:
        def __init__(self) -> None:
            self.lines: list[str] = []

        def info(self, message: str) -> None:
            self.lines.append(message)

        def error(self, message: str) -> None:
            self.lines.append(message)

    class Runtime:
        async def update_config(self, updates: dict[str, str]) -> None:
            assert updates == {"twitch_client_id": "clientid123"}

        async def twitch_device_authorization_start(self) -> dict[str, object]:
            return {
                "platform": "twitch",
                "started": True,
                "pending": True,
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://www.twitch.tv/activate",
            }

    logger = Logger()
    plugin = NekoLivePlugin(SimpleNamespace(logger=logger))
    plugin.runtime = Runtime()

    result = await plugin.twitch_device_authorization_start(client_id="clientid123")

    lines = "\n".join(logger.lines)
    assert result.is_ok() is True
    assert "stage=entry_start client_id_len=11" in lines
    assert "stage=config_saved" in lines
    assert "stage=entry_result started=True pending=True user_code_present=True verification_uri_present=True" in lines
    assert "clientid123" not in lines
    assert "ABCD-EFGH" not in lines
    assert "https://www.twitch.tv/activate" not in lines


@pytest.mark.asyncio
async def test_startup_syncs_live_instructions_instead_of_unconditional_inject(monkeypatch):
    calls: list[tuple[str, bool | None]] = []

    class Runtime:
        def __init__(self, _plugin) -> None:
            self.config = SimpleNamespace(developer_tools_enabled=False)

        async def start(self) -> None:
            calls.append(("start", None))

        async def inject_instructions(self, *, force: bool = False) -> str:
            calls.append(("inject", force))
            return "injected"

        async def sync_live_instructions(self, *, force: bool = False) -> str:
            calls.append(("sync_live", force))
            return "not_injected"

        async def sync_developer_mode(
            self, *, announce: bool = False, force: bool = False
        ) -> str:
            calls.append(("sync_developer", force))
            return "developer_not_injected"

    monkeypatch.setattr("plugin.plugins.neko_live.core.runtime.LiveRuntime", Runtime)
    plugin = NekoLivePlugin(SimpleNamespace(logger=None))

    result = await plugin.startup()

    assert result.is_ok() is True
    assert ("sync_live", False) in calls
    assert ("sync_developer", False) in calls
    assert not any(name == "inject" for name, _ in calls)


@pytest.mark.asyncio
async def test_config_change_syncs_live_instructions_instead_of_unconditional_inject():
    calls: list[tuple[str, bool | None]] = []

    class Runtime:
        def __init__(self) -> None:
            self.config = SimpleNamespace(developer_tools_enabled=False)

        async def reload_config(self) -> None:
            calls.append(("reload", None))

        async def inject_instructions(self, *, force: bool = False) -> str:
            calls.append(("inject", force))
            return "injected"

        async def sync_live_instructions(self, *, force: bool = False) -> str:
            calls.append(("sync_live", force))
            return "not_injected"

        async def sync_developer_mode(
            self, *, announce: bool = False, force: bool = False
        ) -> str:
            calls.append(("sync_developer", force))
            return "developer_not_injected"

    plugin = NekoLivePlugin(SimpleNamespace(logger=None))
    plugin.runtime = Runtime()

    result = await plugin.on_config_change()

    assert result.is_ok() is True
    assert ("sync_live", False) in calls
    assert ("sync_developer", False) in calls
    assert not any(name == "inject" for name, _ in calls)


@pytest.mark.asyncio
async def test_config_change_without_runtime_stays_pending():
    plugin = NekoLivePlugin(SimpleNamespace(logger=None))

    result = await plugin.on_config_change()

    assert result.is_ok() is True
    assert result.value == {"status": "ready", "runtime": "pending"}


@pytest.mark.asyncio
async def test_startup_syncs_prompt_context_without_forcing_empty_restores(monkeypatch):
    calls = []

    class FakeRuntime:
        def __init__(self, _plugin):
            pass

        async def start(self):
            calls.append(("start",))

        async def sync_live_instructions(self, *, force=False):
            calls.append(("live", force))

        async def sync_developer_mode(self, *, announce=False, force=False):
            calls.append(("developer", announce, force))

    monkeypatch.setattr(runtime_module, "LiveRuntime", FakeRuntime)
    plugin = NekoLivePlugin(SimpleNamespace(logger=None))
    monkeypatch.setattr(plugin, "register_dynamic_entry", lambda *args, **kwargs: None)
    monkeypatch.setattr(plugin, "_sync_developer_entries", lambda: None)

    result = await plugin.startup()

    assert result.is_ok() is True
    assert calls == [("start",), ("live", False), ("developer", False, False)]


@pytest.mark.asyncio
async def test_update_config_second_developer_sync_only_announces(monkeypatch):
    sync_calls = []
    injections = 0
    config = SimpleNamespace(
        developer_tools_enabled=False,
        to_dict=lambda: {"developer_tools_enabled": True},
    )

    class FakeRuntime:
        def __init__(self):
            self.config = config

        async def update_config(self, _updates):
            self.config.developer_tools_enabled = True
            await self.sync_developer_mode(announce=False, force=True)
            return self.config

        async def sync_developer_mode(self, *, announce=False, force=False):
            nonlocal injections
            sync_calls.append((announce, force))
            if force or injections == 0:
                injections += 1

    plugin = NekoLivePlugin(SimpleNamespace(logger=None))
    plugin.runtime = FakeRuntime()
    monkeypatch.setattr(plugin, "_sync_developer_entries", lambda: None)

    result = await plugin.update_config_entry(developer_tools_enabled=True)

    assert result.is_ok() is True
    assert sync_calls == [(False, True), (True, False)]
    assert injections == 1


@pytest.mark.asyncio
async def test_clear_sandbox_data_requires_developer_mode():
    class FakeRuntime:
        config = SimpleNamespace(developer_tools_enabled=False)
        clear_calls = 0

        def clear_sandbox_data(self):
            self.clear_calls += 1
            return {"records": 2, "preview_files": 1}

    plugin = NekoLivePlugin(SimpleNamespace(logger=None))
    runtime = FakeRuntime()
    plugin.runtime = runtime

    result = await plugin.clear_sandbox_data()

    assert result.is_ok() is False
    assert runtime.clear_calls == 0


@pytest.mark.asyncio
async def test_set_live_room_entry_returns_platform_room_ref():
    class Runtime:
        def __init__(self) -> None:
            self.received_room = ""

        async def set_live_room(self, room_id: str):
            self.received_room = room_id
            return SimpleNamespace(live_platform="douyin", live_room_ref="room-42", live_room_id=0)

        def live_connection_snapshot(self) -> dict:
            return {"platform": "douyin", "room_ref": "room-42", "room_id": 0}

    plugin = NekoLivePlugin(SimpleNamespace(logger=None))
    runtime = Runtime()
    plugin.runtime = runtime

    result = await plugin.set_live_room("https://live.douyin.com/room-42")

    assert result.is_ok() is True
    assert runtime.received_room == "https://live.douyin.com/room-42"
    assert result.value == {
        "platform": "douyin",
        "room_ref": "room-42",
        "room_id": 0,
        "connection": {"platform": "douyin", "room_ref": "room-42", "room_id": 0},
    }


@pytest.mark.asyncio
async def test_command_loop_start_restarts_idle_hosting_loop():
    class Runtime:
        def __init__(self) -> None:
            self.starts = 0

        def _start_idle_hosting_loop(self) -> None:
            self.starts += 1

    plugin = NekoLivePlugin(SimpleNamespace(logger=None))
    runtime = Runtime()
    plugin.runtime = runtime

    await plugin._on_command_loop_start()

    assert runtime.starts == 1
