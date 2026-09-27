"""Remote tool registrations survive a session-manager rebuild.

A character reload with no live session replaces the whole
``LLMSessionManager`` (``app/main_server/character_runtime.py``), and the new
manager's ``ToolRegistry`` only holds builtin tools. Plugins register once, at
plugin start, so before the ledger in ``main_routers.tool_router`` a saved API
config silently removed every plugin tool from every later session.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from main_logic.tool_calling import ToolDefinition, ToolRegistry
from main_routers import tool_router as tr


class _Mgr:
    """Minimal manager exposing the registry calls the router makes."""

    is_active = False
    is_starting = False
    websocket = None

    def __init__(self, name: str):
        self.lanlan_name = name
        self.tool_registry = ToolRegistry()

    async def register_tool_and_sync(self, tool, *, replace=True):
        self.tool_registry.register(tool, replace=replace)

    async def unregister_tool_and_sync(self, name):
        return self.tool_registry.unregister(name)

    async def clear_tools_and_sync(self, *, source=None):
        return self.tool_registry.clear(source=source)

    async def _sync_tools_to_active_session(self):
        return None

    def _fire_task(self, coro):
        coro.close()


@pytest.fixture(autouse=True)
def _clean_ledger():
    tr._remote_tool_ledger.clear()
    tr._consecutive_connect_failures.clear()
    yield
    tr._remote_tool_ledger.clear()
    tr._consecutive_connect_failures.clear()


def _use_managers(monkeypatch, *managers):
    mapping = {m.lanlan_name: m for m in managers}
    monkeypatch.setattr(tr, "get_session_manager", lambda: mapping)
    return mapping


async def _register(name, *, role=None, source="plugin:mc", description="", port=9001):
    return await tr.register_tool(
        tr.ToolRegisterRequest(
            name=name,
            description=description,
            callback_url=f"http://127.0.0.1:{port}/cb",
            role=role,
            source=source,
        )
    )


def _rebuilt(name):
    return _Mgr(name)


@pytest.mark.asyncio
async def test_rebuilt_manager_gets_global_remote_tool_back(monkeypatch):
    _use_managers(monkeypatch, _Mgr("Alpha"), _Mgr("Beta"))
    result = await _register("minecraft_task", description="act in game")
    assert result["ok"] is True

    fresh = _rebuilt("Alpha")
    assert fresh.tool_registry.names() == []
    assert tr.replay_remote_tools(fresh, "Alpha") == ["minecraft_task"]

    tool = fresh.tool_registry.get("minecraft_task")
    assert tool is not None and tool.handler is None
    assert tool.description == "act in game"
    assert tool.metadata["callback_url"] == "http://127.0.0.1:9001/cb"
    assert tool.metadata["source"] == "plugin:mc"
    # Without a bound dispatcher the replayed remote tool would fail at call time.
    assert fresh.tool_registry._remote_dispatcher is tr._remote_dispatch


@pytest.mark.asyncio
async def test_unregistered_tool_is_not_replayed(monkeypatch):
    _use_managers(monkeypatch, _Mgr("Alpha"))
    await _register("minecraft_task")
    await tr.unregister_tool(tr.ToolUnregisterRequest(name="minecraft_task", role=None))

    fresh = _rebuilt("Alpha")
    assert tr.replay_remote_tools(fresh, "Alpha") == []
    assert fresh.tool_registry.names() == []


@pytest.mark.asyncio
async def test_role_scoped_registration_replays_only_to_that_role(monkeypatch):
    _use_managers(monkeypatch, _Mgr("Alpha"), _Mgr("Beta"))
    await _register("scoped_tool", role="Alpha")

    assert tr.replay_remote_tools(_rebuilt("Alpha"), "Alpha") == ["scoped_tool"]
    assert tr.replay_remote_tools(_rebuilt("Beta"), "Beta") == []


@pytest.mark.asyncio
async def test_role_scoped_unregister_of_global_tool_only_skips_that_role(monkeypatch):
    alpha, beta = _Mgr("Alpha"), _Mgr("Beta")
    _use_managers(monkeypatch, alpha, beta)
    await _register("minecraft_task")
    await tr.unregister_tool(tr.ToolUnregisterRequest(name="minecraft_task", role="Alpha"))

    # Mirrors the live registries: Alpha lost it, Beta still has it.
    assert alpha.tool_registry.names() == []
    assert beta.tool_registry.names() == ["minecraft_task"]
    assert tr.replay_remote_tools(_rebuilt("Alpha"), "Alpha") == []
    assert tr.replay_remote_tools(_rebuilt("Beta"), "Beta") == ["minecraft_task"]

    # A new global registration applies to every role again.
    await _register("minecraft_task")
    assert tr.replay_remote_tools(_rebuilt("Alpha"), "Alpha") == ["minecraft_task"]


@pytest.mark.asyncio
async def test_clear_by_source_is_mirrored_globally_and_per_role(monkeypatch):
    _use_managers(monkeypatch, _Mgr("Alpha"), _Mgr("Beta"))
    await _register("mc_a", source="plugin:mc")
    await _register("mc_b", source="plugin:mc")
    await _register("other", source="plugin:other")

    await tr.clear_tools(tr.ToolClearRequest(source="plugin:mc", role="Alpha"))
    assert tr.replay_remote_tools(_rebuilt("Alpha"), "Alpha") == ["other"]
    assert tr.replay_remote_tools(_rebuilt("Beta"), "Beta") == ["mc_a", "mc_b", "other"]

    await tr.clear_tools(tr.ToolClearRequest(source="plugin:mc", role=None))
    assert tr.replay_remote_tools(_rebuilt("Beta"), "Beta") == ["other"]


@pytest.mark.asyncio
async def test_dead_plugin_eviction_also_drops_ledger_entries(monkeypatch):
    _use_managers(monkeypatch, _Mgr("Alpha"))
    await _register("dead_a", source="plugin:dead", port=9999)
    await _register("alive_same_source", source="plugin:dead", port=9998)
    await _register("other_plugin", source="plugin:other", port=9999)

    tr._evict_dead_callback_origin("plugin:dead", tr._callback_origin("http://127.0.0.1:9999/cb"))

    assert tr.replay_remote_tools(_rebuilt("Alpha"), "Alpha") == [
        "alive_same_source",
        "other_plugin",
    ]


@pytest.mark.asyncio
async def test_registration_with_no_manager_is_not_ledgered(monkeypatch):
    _use_managers(monkeypatch)
    result = await _register("minecraft_task")
    assert result["ok"] is False
    assert tr.replay_remote_tools(_rebuilt("Alpha"), "Alpha") == []


@pytest.mark.asyncio
async def test_registration_that_fails_to_sync_is_still_ledgered(monkeypatch):
    class _SyncFails(_Mgr):
        async def register_tool_and_sync(self, tool, *, replace=True):
            # The real mixin writes the registry before the wire sync raises.
            self.tool_registry.register(tool, replace=replace)
            raise RuntimeError("session.update failed")

    broken = _SyncFails("Alpha")
    _use_managers(monkeypatch, broken)
    result = await _register("minecraft_task")

    assert result["ok"] is False
    assert broken.tool_registry.names() == ["minecraft_task"]
    assert tr.replay_remote_tools(_rebuilt("Alpha"), "Alpha") == ["minecraft_task"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "order, winner",
    [(("Alpha", None), "global"), ((None, "Alpha"), "scoped")],
)
async def test_replay_keeps_last_writer_wins(monkeypatch, order, winner):
    _use_managers(monkeypatch, _Mgr("Alpha"))
    for role in order:
        await _register("t", role=role, description="scoped" if role else "global")

    fresh = _rebuilt("Alpha")
    tr.replay_remote_tools(fresh, "Alpha")
    assert fresh.tool_registry.get("t").description == winner


@pytest.mark.asyncio
async def test_re_registration_moves_entry_to_the_end(monkeypatch):
    _use_managers(monkeypatch, _Mgr("Alpha"))
    await _register("first")
    await _register("second")
    await _register("first", description="updated")

    fresh = _rebuilt("Alpha")
    assert tr.replay_remote_tools(fresh, "Alpha") == ["second", "first"]
    assert fresh.tool_registry.get("first").description == "updated"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["unregister", "clear"])
async def test_rejected_request_leaves_ledger_untouched(monkeypatch, kind):
    from fastapi import HTTPException

    _use_managers(monkeypatch, _Mgr("Alpha"))
    await _register("t")
    await _register("scoped", role="Alpha")
    before = {k: set(v.excluded_roles) for k, v in tr._remote_tool_ledger.items()}

    with pytest.raises(HTTPException) as exc_info:
        if kind == "unregister":
            await tr.unregister_tool(tr.ToolUnregisterRequest(name="t", role="Ghost"))
        else:
            await tr.clear_tools(tr.ToolClearRequest(source="plugin:mc", role="Ghost"))

    assert exc_info.value.status_code == 404
    assert {k: set(v.excluded_roles) for k, v in tr._remote_tool_ledger.items()} == before
    # A manager built later for that name still gets the global tool.
    assert tr.replay_remote_tools(_rebuilt("Ghost"), "Ghost") == ["t"]


async def _clear(source, role=None):
    await tr.clear_tools(tr.ToolClearRequest(source=source, role=role))


async def _evict(source, port):
    tr._evict_dead_callback_origin(source, tr._callback_origin(f"http://127.0.0.1:{port}/cb"))


_SHADOW_SCENARIOS = {
    # Global registration overwrites the scoped copy, then the global one is cleared.
    "global_over_scoped_then_clear": (
        [("Alpha", "plugin:a", 9001), (None, "plugin:b", 9002)],
        lambda: _clear("plugin:b"),
    ),
    # Same source, two origins: the overwriting global origin dies.
    "global_over_scoped_then_evict": (
        [("Alpha", "plugin:x", 9001), (None, "plugin:x", 9002)],
        lambda: _evict("plugin:x", 9002),
    ),
    # Scoped registration overwrites Alpha's global copy, then the scoped one is cleared.
    "scoped_over_global_then_clear": (
        [(None, "plugin:b", 9002), ("Alpha", "plugin:a", 9001)],
        lambda: _clear("plugin:a"),
    ),
    # Scoped registration overwrites Alpha's global copy, then its origin dies.
    "scoped_over_global_then_evict": (
        [(None, "plugin:x", 9002), ("Alpha", "plugin:x", 9001)],
        lambda: _evict("plugin:x", 9001),
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", sorted(_SHADOW_SCENARIOS))
async def test_overwritten_registration_is_not_resurrected(monkeypatch, scenario):
    registrations, removal = _SHADOW_SCENARIOS[scenario]
    live = {"Alpha": _Mgr("Alpha"), "Beta": _Mgr("Beta")}
    _use_managers(monkeypatch, *live.values())
    for role, source, port in registrations:
        await _register("t", role=role, source=source, port=port, description=f"{role}/{source}")
    await removal()

    for name, mgr in live.items():
        fresh = _rebuilt(name)
        tr.replay_remote_tools(fresh, name)
        # A rebuild must reproduce exactly what the live registry holds.
        assert [
            (t.name, t.description, t.metadata["callback_url"]) for t in fresh.tool_registry.all()
        ] == [
            (t.name, t.description, t.metadata["callback_url"]) for t in mgr.tool_registry.all()
        ], name


@pytest.mark.asyncio
async def test_forget_role_drops_scoped_entries_and_exclusions(monkeypatch):
    _use_managers(monkeypatch, _Mgr("Alpha"), _Mgr("Beta"))
    await _register("global_tool")
    await _register("scoped_tool", role="Alpha")
    await tr.unregister_tool(tr.ToolUnregisterRequest(name="global_tool", role="Alpha"))
    assert tr.replay_remote_tools(_rebuilt("Alpha"), "Alpha") == ["scoped_tool"]

    tr.forget_role("Alpha")

    # An unrelated character reusing the name starts like any new character.
    assert tr.replay_remote_tools(_rebuilt("Alpha"), "Alpha") == ["global_tool"]
    assert tr.replay_remote_tools(_rebuilt("Beta"), "Beta") == ["global_tool"]


@pytest.mark.asyncio
async def test_character_slot_removal_forgets_role_in_ledger(monkeypatch):
    from app.main_server import character_runtime

    name = "NekoToolLedgerRemoved"
    _use_managers(monkeypatch, _Mgr(name), _Mgr("Beta"))
    await _register("global_tool")
    await _register("scoped_tool", role=name)
    await tr.unregister_tool(tr.ToolUnregisterRequest(name="global_tool", role=name))

    role = SimpleNamespace(
        websocket_lock=asyncio.Lock(),
        session_manager=None,
        sync_message_queue=SimpleNamespace(empty=lambda: True),
        sync_task=None,
    )
    monkeypatch.setitem(character_runtime.role_state, name, role)

    await character_runtime._unregister_and_cleanup_character_slot(name)

    assert name not in character_runtime.role_state
    assert tr.replay_remote_tools(_rebuilt(name), name) == ["global_tool"]


def test_replay_ignores_manager_without_registry():
    tr._ledger_record(ToolDefinition(name="t", description=""), None)
    assert tr.replay_remote_tools(SimpleNamespace(), "Alpha") == []


@pytest.mark.asyncio
async def test_character_rebuild_replays_remote_tools(monkeypatch):
    """The rebuild branch of ``_init_character_resources`` must replay the ledger."""
    from app.main_server import character_runtime, voice_identity_runtime

    name = "NekoToolReplayRebuild"

    class _OldManager(_Mgr):
        user_language = None

        def shutdown(self):
            return None

    class _NewManager(_Mgr):
        def __init__(self, queue, lanlan_name, prompt):
            super().__init__(lanlan_name)
            self.websocket_lock = None

    old_mgr = _OldManager(name)
    _use_managers(monkeypatch, old_mgr)
    await _register("minecraft_task")
    assert old_mgr.tool_registry.names() == ["minecraft_task"]

    async def _noop(*_args, **_kwargs):
        return True

    async def _run_connector(*_args, **_kwargs):
        await asyncio.Event().wait()

    async def _refresh_globals():
        return None

    role = SimpleNamespace(
        websocket_lock=asyncio.Lock(),
        session_manager=old_mgr,
        sync_task=None,
        sync_message_queue=SimpleNamespace(empty=lambda: True),
    )
    monkeypatch.setitem(character_runtime.role_state, name, role)
    monkeypatch.setattr(character_runtime, "lanlan_prompt", {name: "prompt"})
    monkeypatch.setattr(character_runtime, "master_name", "Master")
    monkeypatch.setattr(character_runtime.core, "LLMSessionManager", _NewManager)
    monkeypatch.setattr(character_runtime.cross_server, "run_sync_connector", _run_connector)
    monkeypatch.setattr(character_runtime, "_refresh_character_globals", _refresh_globals)
    monkeypatch.setattr(voice_identity_runtime, "register_voice_identity_manager", _noop)
    monkeypatch.setattr(voice_identity_runtime, "unregister_voice_identity_manager", _noop)

    try:
        await character_runtime._init_character_resources(name, False)
        new_mgr = role.session_manager
        assert isinstance(new_mgr, _NewManager)
        assert new_mgr is not old_mgr
        assert new_mgr.tool_registry.names() == ["minecraft_task"]
        assert new_mgr.tool_registry._remote_dispatcher is tr._remote_dispatch
    finally:
        task = role.sync_task
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                # The connector stub is cancelled on purpose; how it ends is not
                # under test, only that no task outlives the test.
                pass
