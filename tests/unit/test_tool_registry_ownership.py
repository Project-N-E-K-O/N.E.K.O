"""Ownership check and write are one step in the tool registry (issue #3322).

``/api/tools/register`` and ``/api/tools/unregister`` refuse to replace or
remove a same-name tool owned by another source. The check used to run in
the router, separate from the write the manager performs later; a write by
another source landing in between was then overwritten or deleted by name.
The check now runs inside ``ToolRegistry.register`` / ``unregister``
(``owner_check``), under the same lock as the write.

The race tests below pause the manager between the router's call and the
registry write with an ``asyncio.Event`` (no sleeps), let another writer take
the name, then resume.
"""

from __future__ import annotations

import asyncio

import pytest

from main_logic.core.tool_calling import ToolCallingMixin
from main_logic.tool_calling import (
    ToolDefinition,
    ToolOwnershipError,
    ToolRegistry,
    ToolRegistryError,
)
from main_routers import tool_router as tr


def _tool(name: str, source: str, **metadata) -> ToolDefinition:
    return ToolDefinition(name=name, description="", metadata={"source": source, **metadata})


def _same_source(source: str):
    return lambda existing: existing.metadata.get("source") == source


# ---------------------------------------------------------------------------
# ToolRegistry: conditional register / unregister
# ---------------------------------------------------------------------------


def test_register_owner_check_refuses_foreign_holder_and_keeps_it():
    reg = ToolRegistry()
    theirs = _tool("x", "plugin:b")
    reg.register(theirs)

    with pytest.raises(ToolOwnershipError) as info:
        reg.register(_tool("x", "plugin:a"), owner_check=_same_source("plugin:a"))

    assert info.value.existing is theirs
    assert reg.get("x") is theirs


def test_register_owner_check_allows_own_refresh_and_free_name():
    reg = ToolRegistry()
    seen = []

    def check(existing):
        seen.append(existing)
        return existing.metadata.get("source") == "plugin:a"

    first = _tool("x", "plugin:a")
    reg.register(first, owner_check=check)
    assert seen == []  # nothing to check against for a free name
    refreshed = _tool("x", "plugin:a", callback_url="http://127.0.0.1:2/cb")
    reg.register(refreshed, owner_check=check)
    assert seen == [first]
    assert reg.get("x") is refreshed


def test_register_without_replace_still_rejects_existing_name():
    reg = ToolRegistry()
    reg.register(_tool("x", "plugin:a"))
    with pytest.raises(ToolRegistryError) as info:
        reg.register(_tool("x", "plugin:a"), replace=False, owner_check=_same_source("plugin:a"))
    assert not isinstance(info.value, ToolOwnershipError)


def test_unregister_owner_check_refuses_foreign_holder_and_keeps_it():
    reg = ToolRegistry()
    theirs = _tool("x", "plugin:b")
    reg.register(theirs)

    with pytest.raises(ToolOwnershipError) as info:
        reg.unregister("x", owner_check=_same_source("plugin:a"))

    assert info.value.existing is theirs
    assert reg.get("x") is theirs


def test_unregister_owner_check_removes_own_tool_and_reports_missing():
    reg = ToolRegistry()
    reg.register(_tool("x", "plugin:a"))
    assert reg.unregister("x", owner_check=_same_source("plugin:a")) is True
    assert reg.get("x") is None
    assert reg.unregister("x", owner_check=_same_source("plugin:a")) is False


# ---------------------------------------------------------------------------
# LLMSessionManager mixin forwards owner_check on every entry
# ---------------------------------------------------------------------------


class _MixinMgr(ToolCallingMixin):
    def __init__(self):
        self.tool_registry = ToolRegistry()
        self.synced = 0

    async def _sync_tools_to_active_session(self, *, raise_on_failure=False):
        self.synced += 1

    def _fire_task(self, coro):
        coro.close()


@pytest.mark.asyncio
async def test_mixin_entries_forward_owner_check_and_skip_sync_on_refusal():
    mgr = _MixinMgr()
    theirs = _tool("x", "plugin:b")
    mgr.tool_registry.register(theirs)
    mine = _same_source("plugin:a")

    with pytest.raises(ToolOwnershipError):
        mgr.register_tool(_tool("x", "plugin:a"), owner_check=mine)
    with pytest.raises(ToolOwnershipError):
        await mgr.register_tool_and_sync(_tool("x", "plugin:a"), owner_check=mine)
    with pytest.raises(ToolOwnershipError):
        mgr.unregister_tool("x", owner_check=mine)
    with pytest.raises(ToolOwnershipError):
        await mgr.unregister_tool_and_sync("x", owner_check=mine)

    assert mgr.tool_registry.get("x") is theirs
    assert mgr.synced == 0


# ---------------------------------------------------------------------------
# Router: a write landing between the call and the registry write
# ---------------------------------------------------------------------------


class _PausingMgr:
    """Manager that stops before touching its registry until released."""

    is_active = False
    is_starting = False
    websocket = None

    def __init__(self, name: str):
        self.lanlan_name = name
        self.tool_registry = ToolRegistry()
        self.paused = asyncio.Event()
        self.release = asyncio.Event()

    async def _pause(self):
        self.paused.set()
        await self.release.wait()

    async def register_tool_and_sync(self, tool, *, replace=True, owner_check=None):
        await self._pause()
        self.tool_registry.register(tool, replace=replace, owner_check=owner_check)

    async def unregister_tool_and_sync(self, name, *, owner_check=None):
        await self._pause()
        return self.tool_registry.unregister(name, owner_check=owner_check)

    async def clear_tools_and_sync(self, *, source=None):
        return self.tool_registry.clear(source=source)


@pytest.fixture
def mgr(monkeypatch):
    tr._remote_tool_ledger.clear()
    m = _PausingMgr("Alpha")
    monkeypatch.setattr(tr, "get_session_manager", lambda: {"Alpha": m})
    yield m
    tr._remote_tool_ledger.clear()


@pytest.mark.asyncio
async def test_unregister_does_not_delete_tool_another_source_registered_meanwhile(mgr):
    mgr.tool_registry.register(_tool("x", "plugin:a"))
    pending = asyncio.create_task(
        tr.unregister_tool(tr.ToolUnregisterRequest(name="x", expected_source="plugin:a"))
    )
    await mgr.paused.wait()
    # plugin:a's tool goes away and plugin:b takes the name before the removal runs.
    theirs = _tool("x", "plugin:b")
    mgr.tool_registry.register(theirs)
    mgr.release.set()
    result = await pending

    assert mgr.tool_registry.get("x") is theirs
    assert result["removed"] is False
    assert result["refused_roles"] == [{"role": "Alpha", "owned_by": "plugin:b"}]


@pytest.mark.asyncio
async def test_register_does_not_overwrite_tool_another_source_registered_meanwhile(mgr):
    pending = asyncio.create_task(
        tr.register_tool(
            tr.ToolRegisterRequest(
                name="x",
                description="",
                callback_url="http://127.0.0.1:9001/cb",
                source="plugin:a",
            )
        )
    )
    await mgr.paused.wait()
    # Another writer (here a local registration) claims the free name first.
    theirs = _tool("x", "plugin:b")
    mgr.tool_registry.register(theirs)
    mgr.release.set()
    result = await pending

    assert mgr.tool_registry.get("x") is theirs
    assert result["ok"] is False
    assert result["affected_roles"] == []
    assert result["failed_roles"] == [{
        "role": "Alpha",
        "error": "tool 'x' already owned by source 'plugin:b' (cross-source overwrite refused)",
    }]


@pytest.mark.asyncio
async def test_same_source_register_and_unregister_still_go_through(mgr):
    mgr.release.set()
    result = await tr.register_tool(
        tr.ToolRegisterRequest(
            name="x", description="", callback_url="http://127.0.0.1:9001/cb", source="plugin:a",
        )
    )
    assert result["ok"] is True
    refreshed = await tr.register_tool(
        tr.ToolRegisterRequest(
            name="x", description="v2", callback_url="http://127.0.0.1:9002/cb", source="plugin:a",
        )
    )
    assert refreshed["ok"] is True
    assert mgr.tool_registry.get("x").description == "v2"

    removed = await tr.unregister_tool(tr.ToolUnregisterRequest(name="x", expected_source="plugin:a"))
    assert removed["removed"] is True
    assert removed["refused_roles"] == []
    assert mgr.tool_registry.get("x") is None
