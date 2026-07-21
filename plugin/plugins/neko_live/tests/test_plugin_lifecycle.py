from types import SimpleNamespace

import pytest

from plugin.plugins.neko_live import NekoLivePlugin
from plugin.plugins.neko_live.core import runtime as runtime_module


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


def test_recent_chat_tool_registration_is_role_scoped_and_live_only(monkeypatch):
    plugin = NekoLivePlugin(SimpleNamespace(logger=None))
    registered: list[dict] = []
    current: list[dict] = []

    monkeypatch.setattr(
        "plugin.plugins.neko_live.adapters.neko_dispatcher.resolve_plugin_target_lanlan",
        lambda _plugin: "测试猫猫",
    )
    monkeypatch.setattr(plugin, "list_llm_tools", lambda: list(current))

    def register(**kwargs):
        registered.append(kwargs)
        current.append({"name": kwargs["name"], "role": kwargs["role"]})
        return True

    def unregister(name):
        current.clear()
        return name == "get_recent_live_chat"

    monkeypatch.setattr(plugin, "register_llm_tool", register)
    monkeypatch.setattr(plugin, "unregister_llm_tool", unregister)

    assert plugin._set_recent_chat_tool_enabled(True) is True
    assert registered[0]["name"] == "get_recent_live_chat"
    assert registered[0]["role"] == "测试猫猫"
    assert registered[0]["timeout"] == 5.0
    assert "query" in registered[0]["parameters"]["properties"]
    assert registered[0]["parameters"]["properties"]["limit"]["maximum"] == 3
    assert registered[0]["parameters"]["properties"]["position"]["maximum"] == 3
    assert plugin._set_recent_chat_tool_enabled(True) is True
    assert len(registered) == 1
    assert plugin._set_recent_chat_tool_enabled(False) is True
    assert current == []


@pytest.mark.asyncio
async def test_recent_chat_tool_returns_only_current_live_session_snapshot():
    plugin = NekoLivePlugin(SimpleNamespace(logger=None))
    plugin.runtime = SimpleNamespace(
        _accepting_live_events=True,
        live_events=SimpleNamespace(
            recent_chat_snapshot=lambda *, limit: [
                {
                    "seq": 7,
                    "uid": "42",
                    "nickname": "观众甲",
                    "text": "喵喵喵",
                    "seconds_ago": 1.0,
                    "selected": False,
                    "within_fresh_window": False,
                }
            ][:limit]
        ),
        live_provider=SimpleNamespace(
            platform="bilibili",
            configured_room_ref=lambda: "123",
        ),
    )

    result = await plugin._get_recent_live_chat_tool(limit=99)

    assert result["available"] is True
    assert result["status"] == "ok"
    assert result["mode"] == "session_tail"
    assert result["platform"] == "bilibili"
    assert result["room_ref"] == "123"
    assert result["entries"][0]["text"] == "喵喵喵"

    plugin.runtime._accepting_live_events = False
    assert await plugin._get_recent_live_chat_tool() == {
        "available": False,
        "status": "not_live",
        "entries": [],
    }


@pytest.mark.asyncio
async def test_recent_chat_tool_selects_requested_position_in_backend():
    plugin = NekoLivePlugin(SimpleNamespace(logger=None))
    calls: list[int] = []
    rows = [
        {"text": "latest", "within_fresh_window": True},
        {"text": "previous", "within_fresh_window": False},
        {"text": "third", "within_fresh_window": True},
    ]

    def snapshot(*, limit: int):
        calls.append(limit)
        return rows[:limit]

    plugin.runtime = SimpleNamespace(
        _accepting_live_events=True,
        live_events=SimpleNamespace(recent_chat_snapshot=snapshot),
        live_provider=SimpleNamespace(
            platform="bilibili",
            configured_room_ref=lambda: "123",
        ),
    )

    result = await plugin._get_recent_live_chat_tool(limit=1, position=2)

    assert calls == [2]
    assert result["position"] == 2
    assert result["mode"] == "session_tail"
    assert result["entries"] == [rows[1]]

    rows.pop()
    result = await plugin._get_recent_live_chat_tool(position=3)
    assert result["available"] is False
    assert result["status"] == "position_unavailable"
    assert result["position"] == 3
    assert result["entries"] == []

    result = await plugin._get_recent_live_chat_tool(position=99)
    assert result["status"] == "invalid_position"
    assert result["entries"] == []
    assert calls == [2, 3]


@pytest.mark.asyncio
async def test_recent_chat_tool_uses_relevant_mode_without_leaking_provider_objects():
    plugin = NekoLivePlugin(SimpleNamespace(logger=None))
    calls: list[dict] = []

    def relevant_chat_snapshot(**kwargs):
        calls.append(kwargs)
        return [
            {
                "seq": 8,
                "uid": "7",
                "nickname": "viewer",
                "text": "今晚的小零食我选薯片",
                "seconds_ago": 3.0,
                "selected": False,
            }
        ]

    plugin.runtime = SimpleNamespace(
        _accepting_live_events=True,
        live_events=SimpleNamespace(relevant_chat_snapshot=relevant_chat_snapshot),
        live_provider=SimpleNamespace(
            platform={"unsafe": True},
            configured_room_ref=lambda: {"token": "must-not-leak"},
        ),
    )

    result = await plugin._get_recent_live_chat_tool(
        limit=5,
        query="最近弹幕关于 零食",
    )

    assert calls == [{"query": "零食", "limit": 1}]
    assert result["mode"] == "relevant"
    assert result["status"] == "ok"
    assert result["platform"] == ""
    assert result["room_ref"] == ""


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
