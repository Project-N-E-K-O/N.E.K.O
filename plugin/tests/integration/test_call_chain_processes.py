"""Call-chain registry, view, observe-mode decisions and audit over real plugin processes.

See docs/design/plugin-call-chain.md. Three fixture plugins run as separate
processes behind the real control router; consent grants and the memory
service are stubbed on the host side.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from plugin import settings
from plugin.core import host as host_module
from plugin.core.call_registry import ORIGIN_RUN, CallLink, CallRegistry, Origin, delivery_link
from plugin.core.communication import PluginCommunicationResourceManager
from plugin.core.host import PluginHost
from plugin.server.application.plugins import capability_gate, dispatch_service
from plugin.server.application.plugins.call_audit import CallAudit
from plugin.server.messaging.handlers import memory as memory_handlers
from plugin.server.messaging.request_router import PluginRouter

pytestmark = pytest.mark.plugin_integration

FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugin_test_call_chain_fixture.py"
SENTINELS = ("SENTINEL-ARG-7f3a", "SENTINEL-RESULT-9b2c", "SENTINEL-PUSH-4d1e", "SENTINEL-ERROR-2a8f")


class World:
    def __init__(self, hosts, grants, audit_path, forwarded, memory_calls):
        self.hosts = hosts
        self.grants = grants
        self.audit_path = audit_path
        self.forwarded = forwarded
        self.memory_calls = memory_calls

    async def run(self, plugin_id: str, entry: str, args: dict | None = None, *, ref: str = "run-1"):
        with delivery_link(CallLink(Origin.of(ORIGIN_RUN, ref))):
            return await self.hosts[plugin_id].trigger(entry, args or {}, timeout=5)

    def entries(self, **match):
        return [
            e for e in reversed(capability_gate.audit.entries(limit=1000))
            if all(e.get(k) == v for k, v in match.items())
        ]


async def _start_hosts(monkeypatch, tmp_path, names):
    monkeypatch.setattr(host_module, "_refresh_child_storage_layout_env", lambda logger: None)
    hosts: dict[str, PluginHost] = {}
    monkeypatch.setattr(dispatch_service.state, "_plugin_comm_queue", asyncio.Queue())
    monkeypatch.setattr(
        dispatch_service.state, "get_plugin_hosts_snapshot_cached", lambda timeout=1: hosts
    )
    router = PluginRouter()
    await router.start()
    try:
        for plugin_id in names:
            directory = tmp_path / plugin_id
            directory.mkdir()
            (directory / "__init__.py").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            entry_point = f"plugins.{plugin_id}:CallChainFixturePlugin"
            path = directory / "plugin.toml"
            path.write_text(f"[plugin]\nid='{plugin_id}'\nname='{plugin_id}'\nentry='{entry_point}'\n")
            host = PluginHost(plugin_id, entry_point, path, source_only=True)
            hosts[plugin_id] = host
            await host.start(startup_timeout=10, startup_failure="abort")
    except BaseException:
        await router.stop()
        for host in hosts.values():
            await host.shutdown(timeout=1)
        raise
    return router, hosts


def _isolate_gate(monkeypatch, tmp_path, grants):
    audit_path = tmp_path / "audit" / "call_audit.jsonl"
    monkeypatch.setattr(capability_gate, "registry", CallRegistry())
    monkeypatch.setattr(capability_gate, "audit", CallAudit(path_resolver=lambda: audit_path))
    monkeypatch.setattr(
        capability_gate,
        "granted_capabilities",
        lambda plugin_id: (frozenset(grants.get(plugin_id, ())), True),
    )
    return audit_path


def _stub_host_services(monkeypatch):
    forwarded: list[dict] = []
    memory_calls: list[str] = []

    async def forward(self, msg):
        forwarded.append(dict(msg))

    async def query_memory(**kwargs):
        memory_calls.append("called")
        return {"memories": []}

    monkeypatch.setattr(PluginCommunicationResourceManager, "_forward_message", forward)
    monkeypatch.setattr(memory_handlers.memory_query_service, "query_memory", query_memory)
    return forwarded, memory_calls


@pytest.fixture
async def world(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", "observe")
    monkeypatch.setenv("NEKO_PLUGIN_CAPABILITY_POLICY", "observe")
    grants: dict[str, set[str]] = {}
    audit_path = _isolate_gate(monkeypatch, tmp_path, grants)
    forwarded, memory_calls = _stub_host_services(monkeypatch)
    router, hosts = await _start_hosts(monkeypatch, tmp_path, ("chain_a", "chain_b", "chain_c"))
    try:
        yield World(hosts, grants, audit_path, forwarded, memory_calls)
    finally:
        await router.stop()
        for host in hosts.values():
            await host.shutdown(timeout=1)
        capability_gate.audit.close(1.0)


@pytest.mark.parametrize("entry", ["chain", "routed_chain", "dynamic_chain"])
async def test_every_entry_kind_sees_the_host_chain(world, entry):
    world.grants["chain_a"] = {"memory.read"}
    view = await world.run("chain_a", entry, ref="run-7")
    assert view["callers"] == []
    assert view["hops"] == [["chain_a", "plugin_entry", entry]]
    assert view["origin"] == ["run", "run-7", None]
    assert view["rights_known"] is True and view["rights"] == ["memory.read"]
    assert view["allows_memory"] is True
    assert view["call_id"] == view["trace_id"]
    # Registered while it ran, removed afterwards.
    assert len(capability_gate.registry) == 0
    (forward,) = world.entries(kind="forward", target="chain_a")
    assert forward["call_id"] == view["call_id"]
    assert forward["origin_kind"] == "run" and forward["result"] == "ok"


async def test_cross_plugin_call_extends_the_chain_and_inherits_the_origin(world):
    world.grants.update({"chain_a": {"memory.read", "messages.push"}, "chain_b": {"memory.read"}})
    reply = await world.run("chain_b", "call", {"target": "chain_a:chain"}, ref="run-9")
    view = reply["value"]
    assert view["callers"] == ["chain_b"]
    assert view["hops"] == [["chain_b", "plugin_entry", "call"], ["chain_a", "plugin_entry", "chain"]]
    assert view["depth"] == 1
    assert view["origin"] == ["run", "run-9", None]
    assert view["rights"] == ["memory.read"]
    forwards = {e["target"]: e for e in world.entries(kind="forward")}
    assert view["trace_id"] == forwards["chain_b"]["call_id"]
    assert forwards["chain_a"]["caller"] == "chain_b"
    assert forwards["chain_a"]["parent_call_id"] == forwards["chain_b"]["call_id"]
    assert len(capability_gate.registry) == 0


async def test_laundering_is_recorded_as_would_deny_and_not_blocked(world):
    # A has memory.read, B does not; B calls A and A reads memory.
    world.grants.update({"chain_a": {"memory.read"}, "chain_b": set()})
    reply = await world.run("chain_b", "call", {"target": "chain_a:read_memory"})
    assert reply["ok"] is True
    assert world.memory_calls == ["called"]
    (decision,) = world.entries(kind="decision")
    assert decision["verdict"] == "would_deny"
    assert decision["reason"] == "caller_chain"
    assert decision["blocked_by"] == ["chain_b"]
    assert decision["chain"] == ["chain_b", "chain_a"]
    assert decision["operation"] == "MEMORY_QUERY"
    assert decision["plugin"] == "chain_a"
    assert decision["flags"] == []


async def test_self_not_approved_is_recorded(world):
    reply = await world.run("chain_a", "read_memory")
    assert reply == {"done": "SENTINEL-RESULT-9b2c"}
    (decision,) = world.entries(kind="decision")
    assert (decision["verdict"], decision["reason"], decision["chain"]) == (
        "would_deny", "self_not_approved", ["chain_a"]
    )


async def test_allowed_uses_are_aggregated_when_the_call_ends(world):
    world.grants["chain_a"] = {"memory.read"}
    await world.run("chain_a", "read_memory")
    await world.run("chain_a", "read_memory")
    assert world.entries(kind="decision") == []
    summaries = world.entries(kind="allow_summary", target="chain_a")
    assert [s["counts"] for s in summaries] == [{"memory.read": 1}, {"memory.read": 1}]


async def test_message_push_is_judged_and_reaches_the_plane_unchanged(world):
    world.grants.update({"chain_a": {"messages.push"}, "chain_b": set()})
    reply = await world.run("chain_b", "call", {"target": "chain_a:push"})
    assert reply == {"ok": True, "value": {"submitted": True}}
    async with asyncio.timeout(3):
        while not world.forwarded:
            await asyncio.sleep(0.01)
    (message,) = world.forwarded
    assert "_rpc_parent_request_id" not in message
    assert message["plugin_id"] == "chain_a"
    (decision,) = world.entries(kind="decision")
    assert decision["operation"] == "MESSAGE_PUSH"
    assert decision["verdict"] == "would_deny" and decision["reason"] == "caller_chain"


async def test_bare_thread_use_is_unattributed(world):
    await world.run("chain_a", "read_memory_in_bare_thread")
    (decision,) = world.entries(kind="decision")
    assert decision["chain"] == ["chain_a"]
    assert decision["flags"] == ["unattributed"]
    assert decision["reason"] == "self_not_approved"


async def test_unattributed_use_inside_a_live_chain_is_possible_laundering(world):
    world.grants.update({"chain_a": {"memory.read"}, "chain_b": {"memory.read"}})
    reply = await world.run("chain_b", "call", {"target": "chain_a:read_memory_in_bare_thread"})
    assert reply["ok"] is True
    (decision,) = world.entries(kind="decision")
    # Allowed (A has it on its own), but written because A dropped its chain.
    assert decision["verdict"] == "allow"
    assert decision["chain"] == ["chain_a"]
    assert decision["flags"] == ["possible_laundering", "unattributed"]


async def _hold(world, plugin_id):
    task = asyncio.create_task(world.run(plugin_id, "hold"))
    async with asyncio.timeout(3):
        while not any(r.target == plugin_id for r in capability_gate.registry._records.values()):
            await asyncio.sleep(0.01)
    (record,) = [r for r in capability_gate.registry._records.values() if r.target == plugin_id]
    return task, record


async def test_forged_parent_and_ctx_cannot_pull_another_plugin_into_a_chain(world):
    world.grants.update({"chain_a": {"memory.read"}, "chain_b": {"memory.read"}, "chain_c": {"memory.read"}})
    held, a_record = await _hold(world, "chain_a")
    try:
        reply = await world.run("chain_c", "forge_call", {"parent": a_record.call_id, "target": "chain_b:chain"})
    finally:
        await world.hosts["chain_a"].trigger("release", {}, timeout=5)
        await held
    view = reply["result"]
    assert view["callers"] == ["chain_c"]
    assert "chain_a" not in [hop[0] for hop in view["hops"]]
    assert view["trace_id"] != "forged-trace" and view["depth"] == 1
    assert view["origin"][0] == "plugin_self"
    (to_b,) = world.entries(kind="forward", target="chain_b")
    assert to_b["caller"] == "chain_c" and to_b["parent_call_id"] is None


async def test_forged_parent_cannot_borrow_another_plugins_rights(world):
    world.grants.update({"chain_a": {"memory.read"}, "chain_c": set()})
    held, a_record = await _hold(world, "chain_a")
    try:
        await world.run("chain_c", "forge_memory", {"parent": a_record.call_id})
    finally:
        await world.hosts["chain_a"].trigger("release", {}, timeout=5)
        await held
    (decision,) = world.entries(kind="decision")
    assert decision["plugin"] == "chain_c"
    assert decision["chain"] == ["chain_c"]
    assert decision["verdict"] == "would_deny"
    assert "unattributed" in decision["flags"]


async def test_audit_never_contains_arguments_results_bodies_or_error_text(world):
    world.grants.update({"chain_a": set(), "chain_b": set()})
    secret_arg, _, secret_push, _ = SENTINELS
    await world.run("chain_b", "call", {"target": "chain_a:read_memory", "params": {"secret": secret_arg}})
    await world.run("chain_b", "call", {"target": "chain_a:push", "params": {"secret": secret_push}})
    await world.run("chain_b", "call", {"target": "chain_a:fail"})
    await world.run("chain_a", "chain", {"note": secret_arg})
    failures = world.entries(kind="forward", target="chain_a", result="PLUGIN_EXECUTION_FAILED")
    assert failures, world.entries(kind="forward")
    assert capability_gate.flush_and_close(2.0) is True
    ring = json.dumps(capability_gate.audit.entries(limit=1000), ensure_ascii=False)
    written = world.audit_path.read_text(encoding="utf-8")
    assert written.strip()
    for sentinel in SENTINELS:
        assert sentinel not in ring
        assert sentinel not in written


async def test_observe_never_returns_capability_not_granted(world):
    world.grants.update({"chain_a": {"memory.read"}, "chain_b": set()})
    reply = await world.run("chain_b", "call", {"target": "chain_a:read_memory"})
    assert reply["ok"] is True
    assert "CAPABILITY_NOT_GRANTED" not in json.dumps(reply)


@pytest.fixture
async def off_world(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", "off")
    monkeypatch.setenv("NEKO_PLUGIN_CAPABILITY_POLICY", "off")
    _isolate_gate(monkeypatch, tmp_path, {})
    calls: list[str] = []

    def forbidden(name):
        def fail(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} called with the policy off")

        return fail

    for name in ("begin_call", "end_call", "check", "link_for_plugin_call", "granted_capabilities"):
        monkeypatch.setattr(capability_gate, name, forbidden(name))
    monkeypatch.setattr(capability_gate.registry, "register", forbidden("register"))
    monkeypatch.setattr(capability_gate.audit, "record", forbidden("record"))
    forwarded, memory_calls = _stub_host_services(monkeypatch)
    router, hosts = await _start_hosts(monkeypatch, tmp_path, ("chain_a", "chain_b"))
    try:
        yield World(hosts, {}, None, forwarded, memory_calls), calls
    finally:
        await router.stop()
        for host in hosts.values():
            await host.shutdown(timeout=1)


async def test_off_registers_nothing_and_keeps_the_wire_unchanged(off_world):
    world, calls = off_world
    view = await world.run("chain_a", "chain")
    assert view["callers"] == [] and view["rights_known"] is False and view["allows_memory"] is False
    assert view["origin"][0] == "plugin_self" and view["call_id"] is None
    reply = await world.run("chain_b", "call", {"target": "chain_a:read_memory"})
    assert reply["ok"] is True
    await world.run("chain_a", "push")
    async with asyncio.timeout(3):
        while not world.forwarded:
            await asyncio.sleep(0.01)
    assert "_rpc_parent_request_id" not in world.forwarded[0]
    assert calls == []
    assert len(capability_gate.registry) == 0
    assert capability_gate.audit.entries(limit=10) == []
