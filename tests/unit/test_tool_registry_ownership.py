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
from types import SimpleNamespace

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
    """Manager that stops before touching its registry until released.

    Not the real timing: ``ToolCallingMixin.register_tool_and_sync`` writes
    the registry first and only then awaits the session sync. These tests pin
    the contract that the router leaves the ownership decision to the registry
    write (``owner_check``) instead of checking ahead of the call. The
    interleaving production can hit (a later target taken while an earlier one
    syncs during a global registration) is covered by
    ``test_refused_role_is_excluded_before_the_next_target_is_awaited``.
    """

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
    # No role accepted plugin:a's registration: its ledger record is withdrawn,
    # so it neither replays over plugin:b nor blocks plugin:b's own refresh.
    assert "x" not in {name for _, name in tr._remote_tool_ledger}
    refresh = await tr.register_tool(
        tr.ToolRegisterRequest(
            name="x", description="v2", callback_url="http://127.0.0.1:9002/cb", source="plugin:b",
        )
    )
    assert refresh["ok"] is True
    assert mgr.tool_registry.get("x").metadata["source"] == "plugin:b"
    assert tr._remote_tool_ledger[(None, "x")].tool.metadata["source"] == "plugin:b"


@pytest.mark.asyncio
async def test_global_register_refused_in_one_role_keeps_record_for_accepting_roles(monkeypatch):
    tr._remote_tool_ledger.clear()
    alpha = _PausingMgr("Alpha")
    beta = _PausingMgr("Beta")
    beta.release.set()
    monkeypatch.setattr(tr, "get_session_manager", lambda: {"Alpha": alpha, "Beta": beta})
    try:
        pending = asyncio.create_task(
            tr.register_tool(
                tr.ToolRegisterRequest(
                    name="x", description="", callback_url="http://127.0.0.1:9001/cb", source="plugin:a",
                )
            )
        )
        await alpha.paused.wait()
        theirs = _tool("x", "plugin:b")
        alpha.tool_registry.register(theirs)
        alpha.release.set()
        result = await pending

        assert result["ok"] is True
        assert result["affected_roles"] == ["Beta"]
        assert alpha.tool_registry.get("x") is theirs
        entry = tr._remote_tool_ledger[(None, "x")]
        assert entry.tool.metadata["source"] == "plugin:a"
        assert entry.excluded_roles == {"Alpha"}
        # A rebuilt Alpha does not get plugin:a's tool replayed over plugin:b's slot.
        fresh = SimpleNamespace(tool_registry=ToolRegistry())
        assert tr.replay_remote_tools(fresh, "Alpha") == []
        assert tr.replay_remote_tools(SimpleNamespace(tool_registry=ToolRegistry()), "Beta") == ["x"]
    finally:
        tr._remote_tool_ledger.clear()


def _two_managers(monkeypatch, *, host_id=None):
    tr._remote_tool_ledger.clear()
    alpha = _PausingMgr("Alpha")
    beta = _PausingMgr("Beta")
    monkeypatch.setattr(tr, "get_session_manager", lambda: {"Alpha": alpha, "Beta": beta})
    request = tr.ToolRegisterRequest(
        name="x", description="", callback_url="http://127.0.0.1:9001/cb", source="plugin:a",
        host_instance_id=host_id, host_pid=1 if host_id else None,
        host_process_started_at=1 if host_id else None,
    )
    return alpha, beta, request


@pytest.mark.asyncio
async def test_refused_role_is_excluded_before_the_next_target_is_awaited(monkeypatch):
    alpha, beta, request = _two_managers(monkeypatch)
    try:
        pending = asyncio.create_task(tr.register_tool(request))
        await alpha.paused.wait()
        alpha.tool_registry.register(_tool("x", "plugin:b"))
        alpha.release.set()
        await beta.paused.wait()
        # Beta is still syncing; an Alpha rebuilt now must not replay plugin:a's tool.
        assert tr._remote_tool_ledger[(None, "x")].excluded_roles == {"Alpha"}
        assert tr.replay_remote_tools(SimpleNamespace(tool_registry=ToolRegistry()), "Alpha") == []
        beta.release.set()
        result = await pending
        assert result["affected_roles"] == ["Beta"]
    finally:
        tr._remote_tool_ledger.clear()


@pytest.mark.asyncio
async def test_record_dropped_when_every_attempted_target_refused_and_rest_skipped(monkeypatch):
    alpha, beta, request = _two_managers(monkeypatch, host_id="host-1")
    try:
        pending = asyncio.create_task(tr.register_tool(request))
        await alpha.paused.wait()
        alpha.tool_registry.register(_tool("x", "plugin:b"))
        # Beta leaves the record meanwhile (e.g. a role-scoped unregister), so it is skipped.
        tr._remote_tool_ledger[(None, "x")].excluded_roles.add("Beta")
        alpha.release.set()
        result = await pending

        assert result["ok"] is False
        assert not beta.paused.is_set()  # Beta was never attempted
        assert (None, "x") not in tr._remote_tool_ledger
    finally:
        tr._remote_tool_ledger.clear()


@pytest.mark.asyncio
async def test_register_whose_sync_fails_keeps_its_ledger_record(mgr, monkeypatch):
    class _SyncFails(_PausingMgr):
        async def register_tool_and_sync(self, tool, *, replace=True, owner_check=None):
            self.tool_registry.register(tool, replace=replace, owner_check=owner_check)
            raise RuntimeError("session.update failed")

    broken = _SyncFails("Alpha")
    monkeypatch.setattr(tr, "get_session_manager", lambda: {"Alpha": broken})
    result = await tr.register_tool(
        tr.ToolRegisterRequest(
            name="x", description="", callback_url="http://127.0.0.1:9001/cb", source="plugin:a",
        )
    )
    assert result["ok"] is False
    assert broken.tool_registry.get("x").metadata["source"] == "plugin:a"
    assert (None, "x") in tr._remote_tool_ledger


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


# ---------------------------------------------------------------------------
# registration_id: unregister can target one registration (PR #3975)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_returns_and_stores_a_registration_id(mgr):
    mgr.release.set()
    given = await tr.register_tool(tr.ToolRegisterRequest(
        name="x", description="", callback_url="http://127.0.0.1:9001/cb", source="plugin:a",
        registration_id="reg-1",
    ))
    assert given["registration_id"] == "reg-1"
    assert mgr.tool_registry.get("x").metadata["registration_id"] == "reg-1"
    assert tr._remote_tool_ledger[(None, "x")].tool.metadata["registration_id"] == "reg-1"

    generated = await tr.register_tool(tr.ToolRegisterRequest(
        name="y", description="", callback_url="http://127.0.0.1:9001/cb", source="plugin:a",
    ))
    assert isinstance(generated["registration_id"], str) and generated["registration_id"]
    assert mgr.tool_registry.get("y").metadata["registration_id"] == generated["registration_id"]


@pytest.mark.asyncio
async def test_unregister_with_registration_id_removes_that_registration(mgr):
    mgr.release.set()
    await tr.register_tool(tr.ToolRegisterRequest(
        name="x", description="", callback_url="http://127.0.0.1:9001/cb", source="plugin:a",
        registration_id="reg-1",
    ))
    removed = await tr.unregister_tool(tr.ToolUnregisterRequest(
        name="x", expected_source="plugin:a", expected_registration_id="reg-1",
    ))
    assert removed["removed"] is True and removed["refused_roles"] == []
    assert mgr.tool_registry.get("x") is None
    assert (None, "x") not in tr._remote_tool_ledger


@pytest.mark.asyncio
async def test_late_unregister_of_an_old_registration_keeps_the_newer_one(mgr):
    """A cleanup for registration 1 is still running when the plugin
    registers the same name again (registration 2). When the cleanup
    reaches the registry it must refuse, not delete registration 2."""
    mgr.release.set()
    await tr.register_tool(tr.ToolRegisterRequest(
        name="x", description="", callback_url="http://127.0.0.1:9001/cb", source="plugin:a",
        registration_id="reg-1",
    ))
    mgr.release.clear()
    pending = asyncio.create_task(tr.unregister_tool(tr.ToolUnregisterRequest(
        name="x", expected_source="plugin:a", expected_registration_id="reg-1",
    )))
    await mgr.paused.wait()
    # The newer registration of the same source lands meanwhile.
    newer = _tool("x", "plugin:a", registration_id="reg-2")
    mgr.tool_registry.register(newer)
    tr._ledger_record(newer, None)
    mgr.release.set()
    result = await pending

    assert mgr.tool_registry.get("x") is newer
    assert result["removed"] is False
    assert result["refused_roles"] == [
        {"role": "Alpha", "owned_by": "plugin:a", "reason": "different registration"},
    ]
    assert tr._remote_tool_ledger[(None, "x")].tool is newer


@pytest.mark.asyncio
async def test_unregister_without_registration_id_removes_any_registration_of_the_source(mgr):
    """Unchanged behaviour when the new field is not sent."""
    mgr.release.set()
    await tr.register_tool(tr.ToolRegisterRequest(
        name="x", description="", callback_url="http://127.0.0.1:9001/cb", source="plugin:a",
        registration_id="reg-2",
    ))
    removed = await tr.unregister_tool(tr.ToolUnregisterRequest(name="x", expected_source="plugin:a"))
    assert removed["removed"] is True and removed["refused_roles"] == []
    assert mgr.tool_registry.get("x") is None


@pytest.mark.asyncio
async def test_registration_id_mismatch_on_another_source_keeps_the_old_refusal_shape(mgr):
    mgr.tool_registry.register(_tool("x", "plugin:b", registration_id="theirs"))
    mgr.release.set()
    result = await tr.unregister_tool(tr.ToolUnregisterRequest(
        name="x", expected_source="plugin:a", expected_registration_id="mine",
    ))
    assert result["refused_roles"] == [{"role": "Alpha", "owned_by": "plugin:b"}]
