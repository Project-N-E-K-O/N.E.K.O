from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from plugin.core import host as host_module
from plugin.core.host import PluginHost
from plugin.server.messaging.handlers import plugin_config
from plugin.server.messaging.request_router import PluginRouter
from plugin.server.application.plugins import dispatch_service

pytestmark = pytest.mark.plugin_integration


@pytest.fixture
async def rpc_hosts(monkeypatch, tmp_path):
    monkeypatch.setattr(
        host_module, "_refresh_child_storage_layout_env", lambda logger: None
    )
    hosts = {}
    monkeypatch.setattr(dispatch_service.state, "_plugin_comm_queue", asyncio.Queue())
    monkeypatch.setattr(
        dispatch_service.state,
        "get_plugin_hosts_snapshot_cached",
        lambda timeout=1: hosts,
    )

    async def config(**kwargs):
        return {"config": {"answer": 42}}

    monkeypatch.setattr(plugin_config.config_query_service, "get_plugin_config", config)
    router = PluginRouter()
    await router.start()
    try:
        for plugin_id in ("rpc_a", "rpc_b", "rpc_c"):
            directory = tmp_path / plugin_id
            directory.mkdir()
            path = directory / "plugin.toml"
            (directory / "__init__.py").write_text(
                (
                    Path(__file__).resolve().parents[3]
                    / "tests"
                    / "fixtures"
                    / "plugin_test_rpc_fixture.py"
                ).read_text()
            )
            entry_point = f"plugins.{plugin_id}:RpcFixturePlugin"
            path.write_text(
                f"[plugin]\nid='{plugin_id}'\nname='{plugin_id}'\nentry='{entry_point}'\n"
            )
            host = PluginHost(plugin_id, entry_point, path, source_only=True)
            hosts[plugin_id] = host
            await host.start(startup_timeout=5, startup_failure="abort")
        yield hosts
    finally:
        await router.stop()
        for host in hosts.values():
            await host.shutdown(timeout=1)


async def test_failure_is_returned_and_later_calls_still_work(rpc_hosts):
    caller = rpc_hosts["rpc_a"]
    failure = await caller.trigger("call", {"target": "rpc_b:reject"}, timeout=2)
    assert failure["ok"] is False
    assert "target rejected input" in failure["error"]
    assert "timed out" not in failure["error"]
    assert failure["code"] == "PLUGIN_EXECUTION_FAILED"
    success = await caller.trigger("call", {"target": "rpc_b:ping"}, timeout=2)
    assert success == {"ok": True, "value": {"value": "ok"}}


async def test_nested_calls_and_host_queries_complete(rpc_hosts):
    caller = rpc_hosts["rpc_a"]
    result = await caller.trigger(
        "call", {"target": "rpc_b:relay", "params": {"target": "rpc_c:ping"}}, timeout=2
    )
    assert result == {"ok": True, "value": {"value": "ok"}}
    config = await caller.trigger("call", {"target": "rpc_b:config"}, timeout=2)
    assert config["ok"] is True


async def test_parallel_calls_keep_reply_ownership(rpc_hosts):
    result = await rpc_hosts["rpc_a"].trigger(
        "parallel", {"target": "rpc_b:ping"}, timeout=2
    )
    assert result == [{"value": str(i)} for i in range(4)]


async def test_models_and_ctx_match_direct_invocation(rpc_hosts):
    params = {"query": "hello", "_ctx": {"run_id": "typed-call"}}
    direct = await rpc_hosts["rpc_b"].trigger("typed", params, timeout=2)
    remote = await rpc_hosts["rpc_a"].trigger(
        "call", {"target": "rpc_b:typed", "params": params}, timeout=2
    )
    assert remote == {"ok": True, "value": direct}


async def test_timeout_does_not_break_next_call(rpc_hosts):
    caller = rpc_hosts["rpc_a"]
    failure = await caller.trigger(
        "call",
        {"target": "rpc_b:ping", "params": {"delay": 1}, "timeout": 0.05},
        timeout=2,
    )
    assert failure["ok"] is False
    assert failure["code"] == "DEADLINE_EXCEEDED"
    success = await caller.trigger("call", {"target": "rpc_b:ping"}, timeout=2)
    assert success["ok"] is True


async def test_cancellation_reaches_the_target_process(rpc_hosts):
    caller, target = rpc_hosts["rpc_a"], rpc_hosts["rpc_b"]
    call = asyncio.create_task(
        caller.trigger(
            "call",
            {"target": "rpc_b:ping", "params": {"delay": 10}, "timeout": 20},
            timeout=30,
        )
    )
    async with asyncio.timeout(2):
        while await target.trigger("active", {}, timeout=1) == 0:
            await asyncio.sleep(0.01)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    async with asyncio.timeout(2):
        while await target.trigger("active", {}, timeout=1) != 0:
            await asyncio.sleep(0.01)


async def test_process_exit_ends_an_unlimited_wait(rpc_hosts):
    target = rpc_hosts["rpc_b"]
    call = asyncio.create_task(target.trigger("ping", {"delay": 30}, timeout=None))
    async with asyncio.timeout(1):
        while target.comm_manager.get_pending_requests_count() == 0:
            await asyncio.sleep(0)
    target.process.terminate()
    with pytest.raises(RuntimeError, match="connection closed"):
        await asyncio.wait_for(call, timeout=2)


async def test_source_process_exit_cancels_its_outgoing_calls(rpc_hosts):
    caller, target = rpc_hosts["rpc_a"], rpc_hosts["rpc_b"]
    call = asyncio.create_task(
        caller.trigger(
            "call",
            {"target": "rpc_b:ping", "params": {"delay": 30}, "timeout": 30},
            timeout=None,
        )
    )
    async with asyncio.timeout(2):
        while await target.trigger("active", {}, timeout=1) == 0:
            await asyncio.sleep(0.01)
    caller.process.terminate()
    with pytest.raises(RuntimeError, match="connection closed"):
        await asyncio.wait_for(call, 2)
    async with asyncio.timeout(2):
        while await target.trigger("active", {}, timeout=1) != 0:
            await asyncio.sleep(0.01)


@pytest.mark.parametrize(
    ("kind", "expected"), [("integer_keys", {2024: 3}), ("path", "cache/image.png")]
)
async def test_wire_value_types_match_direct_and_cross_plugin_calls(
    rpc_hosts, kind, expected
):
    direct = await rpc_hosts["rpc_b"].trigger("wire_value", {"kind": kind}, timeout=2)
    remote = await rpc_hosts["rpc_a"].trigger(
        "call", {"target": "rpc_b:wire_value", "params": {"kind": kind}}, timeout=2
    )
    assert direct == expected
    assert remote == {"ok": True, "value": expected}


async def test_unserializable_entry_returns_a_fault_over_the_real_transport(rpc_hosts):
    result = await rpc_hosts["rpc_a"].trigger(
        "call",
        {"target": "rpc_b:wire_value", "params": {"kind": "unsupported"}},
        timeout=2,
    )
    assert result["ok"] is False
    assert result["code"] == "SERIALIZATION_FAILED"


@pytest.mark.parametrize("invocation", ["direct", "cross_plugin"])
async def test_background_call_after_entry_deadline_uses_its_own_timeout(rpc_hosts, invocation):
    if invocation == "direct":
        background_host = rpc_hosts["rpc_a"]
        receipt = await background_host.trigger(
            "start_background", {"target": "rpc_b:ping"}, timeout=0.2
        )
        assert receipt == "scheduled"
    else:
        background_host = rpc_hosts["rpc_b"]
        receipt = await rpc_hosts["rpc_a"].trigger(
            "call", {"target": "rpc_b:start_background", "params": {"target": "rpc_c:ping"}, "timeout": 0.2},
            timeout=2,
        )
        assert receipt == {"ok": True, "value": "scheduled"}
    await asyncio.sleep(0.25)
    await background_host.trigger("release_background", {}, timeout=2)
    async with asyncio.timeout(2):
        while True:
            result = await background_host.trigger("background_result", {}, timeout=1)
            if result["ready"]:
                break
            await asyncio.sleep(0.01)
    assert result["result"] == {"ok": True, "value": {"value": "ok"}}
