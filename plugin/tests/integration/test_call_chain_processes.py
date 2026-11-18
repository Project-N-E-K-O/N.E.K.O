"""Call-chain registry, view, observe decisions and audit over real plugin processes.

Three fixture plugins run as separate processes behind the real control
router; consent grants and the memory service are stubbed on the host side.
See docs/design/plugin-call-chain.md.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from plugin import settings
from plugin.core import host as host_module
from plugin.core.call_registry import CallLink, CallRegistry, Origin, delivery_link
from plugin.core.communication import PluginCommunicationResourceManager
from plugin.core.host import PluginHost
from plugin.server.application.plugins import call_audit, capability_gate, dispatch_service
from plugin.server.messaging.handlers import memory as memory_handlers
from plugin.server.messaging.request_router import PluginRouter

pytestmark = pytest.mark.plugin_integration

FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugin_test_call_chain_fixture.py"


class World:
    def __init__(self, hosts, grants, forwarded, audit_dir):
        self.hosts, self.grants, self.forwarded, self.audit_dir = hosts, grants, forwarded, audit_dir

    async def run(self, plugin_id, entry, args=None, ref="run-1"):
        with delivery_link(CallLink(Origin.of("run", ref))):
            return await self.hosts[plugin_id].trigger(entry, args or {}, timeout=5)

    def entries(self, **match):
        return [e for e in reversed(call_audit.entries()) if all(e.get(k) == v for k, v in match.items())]


async def _world(monkeypatch, tmp_path, mode):
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", mode)
    monkeypatch.setenv("NEKO_PLUGIN_CAPABILITY_POLICY", mode)  # the plugin processes read it too
    grants: dict[str, set[str]] = {}
    monkeypatch.setattr(capability_gate, "registry", CallRegistry())
    monkeypatch.setattr(capability_gate, "_last_written", {})
    monkeypatch.setattr(capability_gate, "granted_capabilities", lambda p: (frozenset(grants.get(p, ())), True))
    monkeypatch.setattr(call_audit, "audit_directory", lambda: tmp_path / "audit")
    monkeypatch.setattr(call_audit, "_ring", call_audit.deque(maxlen=1000))
    monkeypatch.setattr(call_audit, "_decision_ring", call_audit.deque(maxlen=500))
    monkeypatch.setattr(call_audit, "_abandoned", None)
    call_audit.close()
    call_audit.reopen()
    forwarded: list[dict] = []

    async def forward(self, msg):
        forwarded.append(dict(msg))

    async def query_memory(**kwargs):
        return {"memories": []}

    monkeypatch.setattr(PluginCommunicationResourceManager, "_forward_message", forward)
    monkeypatch.setattr(memory_handlers.memory_query_service, "query_memory", query_memory)
    monkeypatch.setattr(host_module, "_refresh_child_storage_layout_env", lambda logger: None)
    hosts: dict[str, PluginHost] = {}
    monkeypatch.setattr(dispatch_service.state, "_plugin_comm_queue", asyncio.Queue())
    monkeypatch.setattr(dispatch_service.state, "get_plugin_hosts_snapshot_cached", lambda timeout=1: hosts)
    router = PluginRouter()
    await router.start()
    try:
        for plugin_id in ("chain_a", "chain_b", "chain_c"):
            directory = tmp_path / plugin_id
            directory.mkdir()
            (directory / "__init__.py").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            entry_point = f"plugins.{plugin_id}:CallChainFixturePlugin"
            (directory / "plugin.toml").write_text(f"[plugin]\nid='{plugin_id}'\nname='{plugin_id}'\nentry='{entry_point}'\n")
            hosts[plugin_id] = PluginHost(plugin_id, entry_point, directory / "plugin.toml", source_only=True)
            await hosts[plugin_id].start(startup_timeout=10, startup_failure="abort")
    except BaseException:
        await router.stop()
        for host in hosts.values():
            await host.shutdown(timeout=1)
        raise
    return router, World(hosts, grants, forwarded, tmp_path / "audit")


@pytest.fixture
async def world(monkeypatch, tmp_path):
    router, world = await _world(monkeypatch, tmp_path, "observe")
    try:
        yield world
    finally:
        await router.stop()
        for host in world.hosts.values():
            await host.shutdown(timeout=1)
        call_audit.close()
        call_audit.reopen()


@pytest.mark.parametrize("entry", ["chain", "routed_chain", "dynamic_chain"])
async def test_every_entry_kind_sees_the_host_chain(world, entry):
    world.grants["chain_a"] = {"memory.read"}
    view = await world.run("chain_a", entry, ref="run-7")
    assert view["callers"] == [] and view["hops"] == [["chain_a", "plugin_entry", entry]]
    assert view["origin"] == ["run", "run-7"]
    assert view["rights_known"] is True and view["rights"] == ["memory.read"]
    assert len(capability_gate.registry) == 0  # removed once it ended
    (forward,) = world.entries(kind="forward")
    assert (forward["target"], forward["origin_kind"], forward["result"]) == ("chain_a", "run", "ok")


async def test_cross_plugin_call_extends_the_chain(world):
    world.grants.update({"chain_a": {"memory.read", "messages.push"}, "chain_b": {"memory.read"}})
    view = (await world.run("chain_b", "call", {"target": "chain_a:chain"}, ref="run-9"))["value"]
    assert view["callers"] == ["chain_b"]
    assert view["hops"] == [["chain_b", "plugin_entry", "call"], ["chain_a", "plugin_entry", "chain"]]
    assert view["origin"] == ["run", "run-9"] and view["rights"] == ["memory.read"]
    to_a = world.entries(kind="forward", target="chain_a")[0]
    assert to_a["caller"] == "chain_b" and to_a["trace_id"] == view["trace_id"]


async def test_laundering_is_recorded_as_would_deny_and_not_blocked(world):
    # A has memory.read, B does not; B calls A and A reads memory.
    world.grants.update({"chain_a": {"memory.read"}})
    reply = await world.run("chain_b", "call", {"target": "chain_a:read_memory"})
    assert reply == {"ok": True, "value": "SENTINEL-RESULT"}
    (decision,) = world.entries(kind="decision")
    assert decision["verdict"] == "would_deny" and decision["reason"] == "caller_chain"
    assert decision["chain"] == ["chain_b", "chain_a"] and decision["blocked_by"] == ["chain_b"]
    assert decision["operation"] == "MEMORY_QUERY"


async def test_self_not_approved_and_allowed_aggregation(world):
    await world.run("chain_a", "read_memory")
    (decision,) = world.entries(kind="decision")
    assert (decision["reason"], decision["chain"]) == ("self_not_approved", ["chain_a"])
    world.grants["chain_a"] = {"memory.read"}
    await world.run("chain_a", "read_memory")
    (summary,) = world.entries(kind="allow_summary")
    assert summary["counts"] == {"memory.read": 1}


async def test_message_push_is_judged_and_reaches_the_plane_unchanged(world):
    world.grants["chain_a"] = {"messages.push"}
    assert (await world.run("chain_b", "call", {"target": "chain_a:push"}))["value"] is True
    async with asyncio.timeout(3):
        while not world.forwarded:
            await asyncio.sleep(0.01)
    assert "_rpc_parent_request_id" not in world.forwarded[0]
    (decision,) = world.entries(kind="decision")
    assert (decision["operation"], decision["reason"]) == ("MESSAGE_PUSH", "caller_chain")


async def test_push_routed_after_the_result_keeps_its_chain(world, monkeypatch):
    # Results and pushes ride separate uplinks; hold the push until the call
    # has returned and its record was removed.
    world.grants["chain_a"] = {"messages.push"}
    release = asyncio.Event()
    route = PluginCommunicationResourceManager._route_message

    async def held(self, msg):
        if msg.get("type") == "MESSAGE_PUSH":
            await release.wait()
        await route(self, msg)

    monkeypatch.setattr(PluginCommunicationResourceManager, "_route_message", held)
    assert (await world.run("chain_b", "call", {"target": "chain_a:push"}))["value"] is True
    assert len(capability_gate.registry) == 0 and world.forwarded == []
    release.set()
    async with asyncio.timeout(3):
        while not world.forwarded:
            await asyncio.sleep(0.01)
    assert "_rpc_parent_request_id" not in world.forwarded[0]
    (decision,) = world.entries(kind="decision")
    assert decision["chain"] == ["chain_b", "chain_a"] and decision["reason"] == "caller_chain"


async def test_unattributed_and_possible_laundering(world):
    await world.run("chain_a", "read_memory_in_bare_thread")
    await world.run("chain_b", "call", {"target": "chain_a:read_memory_in_bare_thread"})
    first, second = world.entries(kind="decision")
    assert first["chain"] == second["chain"] == ["chain_a"]
    assert first["flags"] == ["unattributed"]
    assert second["flags"] == ["possible_laundering", "unattributed"]


async def test_forged_parent_and_ctx_neither_borrow_nor_drag_in(world):
    world.grants.update({"chain_a": {"memory.read"}, "chain_b": {"memory.read"}})
    held = asyncio.create_task(world.run("chain_a", "hold"))
    async with asyncio.timeout(3):
        while len(capability_gate.registry) == 0:
            await asyncio.sleep(0.01)
    (a_record,) = capability_gate.registry._records.values()
    try:
        view = await world.run("chain_c", "forge", {"parent": a_record.call_id, "target": "chain_b:chain"})
    finally:
        await world.hosts["chain_a"].trigger("release", {}, timeout=5)
        await held
    # C's memory read is judged on C alone (A's rights are not borrowed) ...
    (decision,) = world.entries(kind="decision")
    assert decision["chain"] == ["chain_c"] and decision["verdict"] == "would_deny"
    # ... and B sees only C as caller: A is not dragged in, forged trace ignored.
    assert view["callers"] == ["chain_c"] and view["trace_id"] != "forged-trace"
    assert view["origin"][0] == "plugin_self"


async def test_audit_holds_no_arguments_results_bodies_or_error_text(world):
    read = await world.run("chain_b", "call", {"target": "chain_a:read_memory", "params": {"secret": "SENTINEL-ARG"}})
    assert read == {"ok": True, "value": "SENTINEL-RESULT"}
    pushed = await world.run("chain_b", "call", {"target": "chain_a:push", "params": {"secret": "SENTINEL-PUSH"}})
    assert pushed == {"ok": True, "value": True}
    async with asyncio.timeout(3):
        while not world.forwarded:
            await asyncio.sleep(0.01)
    await world.run("chain_b", "call", {"target": "chain_a:fail"})
    assert world.entries(kind="forward", target="chain_a", result="PLUGIN_EXECUTION_FAILED")
    assert call_audit.close() is True  # the writer has exited: the file is complete
    written = (world.audit_dir / "call_audit.jsonl").read_text(encoding="utf-8")
    ring = json.dumps(call_audit.entries())
    assert '"result":"PLUGIN_EXECUTION_FAILED"' in written
    for sentinel in ("SENTINEL-ARG", "SENTINEL-RESULT", "SENTINEL-PUSH", "SENTINEL-ERROR"):
        assert sentinel not in written and sentinel not in ring


async def test_off_registers_nothing_and_keeps_the_wire_unchanged(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("called with the policy off")

    router, world = await _world(monkeypatch, tmp_path, "off")
    for name in ("begin_call", "end_call", "link_for_plugin_call", "granted_capabilities"):
        monkeypatch.setattr(capability_gate, name, forbidden)
    monkeypatch.setattr(capability_gate.registry, "lookup", forbidden)
    monkeypatch.setattr(call_audit, "record", forbidden)
    try:
        view = await world.run("chain_a", "chain")
        assert view["rights_known"] is False and view["origin"][0] == "plugin_self"
        assert (await world.run("chain_b", "call", {"target": "chain_a:read_memory"}))["ok"] is True
        await world.run("chain_a", "push")
        async with asyncio.timeout(3):
            while not world.forwarded:
                await asyncio.sleep(0.01)
        assert "_rpc_parent_request_id" not in world.forwarded[0]
    finally:
        await router.stop()
        for host in world.hosts.values():
            await host.shutdown(timeout=1)
