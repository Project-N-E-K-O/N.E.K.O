"""Per-plugin shares of the host control router: one plugin cannot starve others."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from types import SimpleNamespace

import pytest

from plugin.core.communication import PluginCommunicationResourceManager
from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.messaging import request_router as module
from plugin.settings import get_plugin_rpc_quotas

_QUEUED = "NEKO_PLUGIN_RPC_MAX_QUEUED_PER_PLUGIN"
_IN_FLIGHT = "NEKO_PLUGIN_RPC_MAX_IN_FLIGHT_PER_PLUGIN"
_PER_TARGET = "NEKO_PLUGIN_RPC_MAX_IN_FLIGHT_PER_TARGET_PLUGIN"


class Downlinks:
    def __init__(self):
        self.replies: defaultdict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
        self.blocked: dict[str, asyncio.Event] = {}

    def sender(self, plugin_id: str):
        async def send(reply):
            gate = self.blocked.get(plugin_id)
            if gate is not None:
                await gate.wait()
            await self.replies[plugin_id].put(reply)

        return send

    async def next(self, plugin_id: str, timeout: float = 1.0) -> dict:
        return await asyncio.wait_for(self.replies[plugin_id].get(), timeout)


@pytest.fixture
async def setup(monkeypatch):
    monkeypatch.setattr(state, "_plugin_comm_queue", asyncio.Queue())
    monkeypatch.setattr(state, "plugin_control_ingress", None)
    monkeypatch.setenv(_QUEUED, "4")
    monkeypatch.setenv(_IN_FLIGHT, "2")
    monkeypatch.setenv(_PER_TARGET, "2")
    downlinks = Downlinks()
    monkeypatch.setattr(state, "get_downlink_sender", downlinks.sender)
    router = module.PluginRouter()
    release = asyncio.Event()
    entered: defaultdict[str, list[str]] = defaultdict(list)
    cancelled: list[str] = []

    async def held(ctx, request):
        entered[ctx.from_plugin].append(ctx.request_id)
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.append(ctx.request_id)
            raise
        return "done"

    async def echo(ctx, request):
        return "alive"

    router._handlers.update(HELD=held, ECHO=echo)
    try:
        yield SimpleNamespace(
            router=router,
            downlinks=downlinks,
            release=release,
            entered=entered,
            cancelled=cancelled,
        )
    finally:
        release.set()
        for gate in downlinks.blocked.values():
            gate.set()
        await asyncio.wait_for(router.stop(), 2)


def request(plugin_id: str, request_id: str, operation: str = "HELD", **extra) -> dict:
    return {
        "type": operation,
        "request_id": request_id,
        "from_plugin": plugin_id,
        "_rpc_connection_id": f"conn-{plugin_id}",
        **extra,
    }


async def turns(count: int = 50) -> None:
    for _ in range(count):
        await asyncio.sleep(0)


async def settle(router, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while router._busy_replies or router._dispatcher._tasks or not router._queue.empty():
            await asyncio.sleep(0.005)


def assert_released(router) -> None:
    assert router._dispatcher._owner_counts == {}
    assert router._dispatcher._target_counts == {}
    assert router._dispatcher._owner_target_counts == {}
    assert not router._ingress._held


def plugin_caller(calls, slow):
    async def plugin_call(ctx, request):
        calls.append((ctx.from_plugin, request["to_plugin"]))
        if request["to_plugin"] == "B":
            await slow.wait()
        return f"from {request['to_plugin']}"

    return plugin_call


async def test_full_queue_share_rejects_only_the_flooding_plugin(setup):
    router = setup.router
    await router.start()
    ingress = router._ingress
    # Same-loop admission never yields, so the router has not drained yet.
    for index in range(4):
        await ingress.enqueue(request("A", f"a{index}"))
    with pytest.raises(asyncio.QueueFull):
        await ingress.enqueue(request("A", "a-over"))
    await ingress.enqueue(request("C", "c-echo", "ECHO"))

    flooder = PluginCommunicationResourceManager("A", SimpleNamespace(), get_logger("test.quota"))
    flooder._connection_id = "conn-A"
    rejected = []

    async def reject(reply):
        rejected.append(reply)

    flooder.send_plugin_response = reject
    await flooder._route_comm({"type": "HELD", "request_id": "a-route"})
    assert rejected[0]["request_id"] == "a-route"
    assert rejected[0]["error"]["code"] == "BUSY"

    replies = [await setup.downlinks.next("C")]
    assert replies[0]["result"] == "alive"
    setup.release.set()
    await settle(router)
    assert_released(router)

    # A stopped router discards its queue and shares; the next one starts empty.
    for index in range(4):
        await ingress.enqueue(request("A", f"b{index}"))
    await router.stop()
    assert not ingress._held
    await router.start()
    for index in range(4):
        await router._ingress.enqueue(request("A", f"c{index}", "ECHO"))
    answered = set()
    while len(answered) < 4:
        reply = await setup.downlinks.next("A")
        if reply["request_id"].startswith("c"):
            answered.add(reply["request_id"])
    await settle(router)
    assert_released(router)


async def test_in_flight_share_rejects_only_the_flooding_plugin(setup):
    router = setup.router
    await router.start()
    for index in range(4):
        await router._ingress.enqueue(request("A", f"a{index}"))
    busy = [await setup.downlinks.next("A") for _ in range(2)]
    assert {reply["request_id"] for reply in busy} == {"a2", "a3"}
    assert all(reply["error"]["code"] == "BUSY" for reply in busy)
    assert setup.entered["A"] == ["a0", "a1"]

    await router._ingress.enqueue(request("C", "c0"))
    await router._ingress.enqueue(request("C", "c-echo", "ECHO"))
    assert (await setup.downlinks.next("C"))["result"] == "alive"
    assert setup.entered["C"] == ["c0"]

    setup.release.set()
    done = [await setup.downlinks.next("A") for _ in range(2)]
    assert all(reply["result"] == "done" for reply in done)
    assert (await setup.downlinks.next("C"))["result"] == "done"
    await settle(router)
    assert_released(router)


async def test_calls_to_a_hung_plugin_hold_at_most_the_per_target_share(setup, monkeypatch):
    monkeypatch.setenv(_IN_FLIGHT, "8")
    monkeypatch.setenv(_PER_TARGET, "4")
    router = setup.router
    hung = asyncio.Event()
    calls = []
    router._handlers["PLUGIN_TO_PLUGIN"] = plugin_caller(calls, hung)
    await router.start()
    ingress = router._ingress
    for index in range(4):
        await ingress.enqueue(request("A", f"a{index}", "PLUGIN_TO_PLUGIN", to_plugin="B"))
    busy = [await setup.downlinks.next("A") for _ in range(2)]
    assert all(reply["error"]["code"] == "BUSY" for reply in busy)
    await ingress.enqueue(request("E", "e0", "PLUGIN_TO_PLUGIN", to_plugin="B"))
    await ingress.enqueue(request("E", "e1", "PLUGIN_TO_PLUGIN", to_plugin="B"))
    await turns()
    # The share is per target, across callers.
    await ingress.enqueue(request("F", "f0", "PLUGIN_TO_PLUGIN", to_plugin="B"))
    assert (await setup.downlinks.next("F"))["error"]["code"] == "BUSY"
    # Other targets and other operations are unaffected.
    await ingress.enqueue(request("D", "d0", "PLUGIN_TO_PLUGIN", to_plugin="C"))
    assert (await setup.downlinks.next("D"))["result"] == "from C"
    await ingress.enqueue(request("C", "c0", "ECHO"))
    assert (await setup.downlinks.next("C"))["result"] == "alive"
    assert router._dispatcher._target_counts == {"B": 4}

    # Disconnecting the callers frees their slots on the hung target.
    for caller in ("A", "E"):
        assert ingress.notify_disconnect(
            {"type": "RPC_DISCONNECT", "from_plugin": caller, "_rpc_connection_id": f"conn-{caller}"}
        )
    await settle(router)
    assert_released(router)
    await ingress.enqueue(request("F", "f1", "PLUGIN_TO_PLUGIN", to_plugin="B"))
    await turns()
    assert calls[-1] == ("F", "B")
    assert router._dispatcher._target_counts == {"B": 1}

    # Stopping cancels the hung call; a restarted router starts with fresh shares.
    stopped = router._dispatcher
    await router.stop()
    assert stopped._owner_counts == {} and stopped._target_counts == {}
    assert stopped._owner_target_counts == {}
    await router.start()
    assert_released(router)
    hung.set()


async def test_one_caller_cannot_take_a_slow_targets_whole_share(setup, monkeypatch):
    monkeypatch.setenv(_IN_FLIGHT, "32")
    monkeypatch.setenv(_PER_TARGET, "32")
    monkeypatch.setenv(_QUEUED, "64")
    router = setup.router
    slow = asyncio.Event()
    calls = []
    router._handlers["PLUGIN_TO_PLUGIN"] = plugin_caller(calls, slow)
    await router.start()
    ingress = router._ingress
    for index in range(32):
        await ingress.enqueue(request("A", f"a{index}", "PLUGIN_TO_PLUGIN", to_plugin="B"))
    busy = [await setup.downlinks.next("A") for _ in range(16)]
    assert all(reply["error"]["code"] == "BUSY" for reply in busy)
    assert calls.count(("A", "B")) == 16

    await ingress.enqueue(request("C", "c0", "PLUGIN_TO_PLUGIN", to_plugin="B"))
    await turns()
    assert ("C", "B") in calls
    slow.set()
    reply = await setup.downlinks.next("C")
    assert reply["request_id"] == "c0" and reply["result"] == "from B"
    await settle(router)
    assert_released(router)


@pytest.mark.parametrize(
    ("per_target", "per_plugin"), [(4, 4), (2, 8), (3, 3), (5, 2), (8, 16)]
)
async def test_caller_share_of_a_target_holds_for_any_configured_values(
    setup, monkeypatch, per_target, per_plugin
):
    monkeypatch.setenv(_IN_FLIGHT, str(per_plugin))
    monkeypatch.setenv(_PER_TARGET, str(per_target))
    monkeypatch.setenv(_QUEUED, "64")
    router = setup.router
    slow = asyncio.Event()
    calls = []
    router._handlers["PLUGIN_TO_PLUGIN"] = plugin_caller(calls, slow)
    await router.start()
    ingress = router._ingress
    for index in range(2 * per_target):
        await ingress.enqueue(request("A", f"a{index}", "PLUGIN_TO_PLUGIN", to_plugin="B"))
    await turns()
    assert 1 <= calls.count(("A", "B")) <= per_target // 2
    await ingress.enqueue(request("C", "c0", "PLUGIN_TO_PLUGIN", to_plugin="B"))
    await turns()
    assert ("C", "B") in calls
    slow.set()
    await settle(router)
    assert_released(router)


@pytest.mark.parametrize("control", ["RPC_CANCEL", "RPC_DISCONNECT"])
async def test_cancel_and_disconnect_bypass_an_exhausted_share(setup, monkeypatch, control):
    monkeypatch.setenv(_QUEUED, "1")
    monkeypatch.setenv(_IN_FLIGHT, "1")
    router = setup.router
    await router.start()
    ingress = router._ingress
    await ingress.enqueue(request("A", "active"))
    await turns()
    assert setup.entered["A"] == ["active"]
    await ingress.enqueue(request("A", "queued"))
    with pytest.raises(asyncio.QueueFull):
        await ingress.enqueue(request("A", "over"))

    await ingress.enqueue(request("A", "active", control))
    await turns()
    assert setup.cancelled == ["active"]
    await settle(router)
    assert_released(router)
    await ingress.enqueue(request("A", "after", "ECHO"))
    while (reply := await setup.downlinks.next("A"))["request_id"] != "after":
        pass
    assert reply["result"] == "alive"


async def test_blocked_downlink_keeps_busy_replies_within_its_own_share(setup, monkeypatch):
    monkeypatch.setenv(_IN_FLIGHT, "1")
    router = setup.router
    gate = setup.downlinks.blocked["A"] = asyncio.Event()
    await router.start()
    ingress = router._ingress
    await ingress.enqueue(request("A", "active"))
    rejected = 0
    for index in range(200):
        try:
            await ingress.enqueue(request("A", f"a{index}"))
        except asyncio.QueueFull:
            rejected += 1
        await asyncio.sleep(0)
        assert len(router._busy_replies) <= 4
    assert rejected > 0

    # Another plugin still gets its own overload replies at once.
    await ingress.enqueue(request("C", "c0"))
    await ingress.enqueue(request("C", "c1"))
    reply = await setup.downlinks.next("C", 0.2)
    assert reply["request_id"] == "c1" and reply["error"]["code"] == "BUSY"

    gate.set()
    setup.release.set()
    await settle(router)
    assert_released(router)


async def test_quota_warning_is_rate_limited_and_omits_request_content(setup, caplog):
    router = setup.router
    await router.start()
    for index in range(6):
        await router._ingress.enqueue(request("A", f"a{index}", args={"text": "SECRET-PAYLOAD"}))
        await turns(5)
    warnings = [line for line in caplog.text.splitlines() if "Plugin RPC quota reached" in line]
    assert len(warnings) == 1
    assert "quota=in_flight_per_plugin" in warnings[0]
    assert "plugin_id=A" in warnings[0] and "operation=HELD" in warnings[0]
    assert "count=2" in warnings[0] and "limit=2" in warnings[0]
    assert "SECRET-PAYLOAD" not in caplog.text
    for index in range(4):
        await router._ingress.enqueue(request("Q", f"q{index}"))
    with pytest.raises(asyncio.QueueFull):
        await router._ingress.enqueue(request("Q", "q-over", args={"text": "SECRET-PAYLOAD"}))
    assert "quota=queued_per_plugin, plugin_id=Q, operation=HELD" in caplog.text
    assert "SECRET-PAYLOAD" not in caplog.text


def test_environment_overrides_quotas_and_rejects_invalid_values(monkeypatch):
    monkeypatch.setenv(_QUEUED, "7")
    monkeypatch.setenv(_IN_FLIGHT, "5")
    monkeypatch.setenv(_PER_TARGET, "3")
    quotas = get_plugin_rpc_quotas()
    assert (
        quotas.max_queued_per_plugin,
        quotas.max_in_flight_per_plugin,
        quotas.max_in_flight_per_target_plugin,
    ) == (7, 5, 3)
    monkeypatch.setenv(_QUEUED, "0")
    monkeypatch.setenv(_IN_FLIGHT, "-1")
    monkeypatch.setenv(_PER_TARGET, "many")
    assert get_plugin_rpc_quotas() == type(quotas)(64, 32, 32)
    for name in (_QUEUED, _IN_FLIGHT, _PER_TARGET):
        monkeypatch.delenv(name)
    assert get_plugin_rpc_quotas() == type(quotas)(64, 32, 32)


async def test_router_start_applies_environment_quotas(setup, monkeypatch):
    monkeypatch.setenv(_QUEUED, "9")
    monkeypatch.setenv(_IN_FLIGHT, "6")
    monkeypatch.setenv(_PER_TARGET, "4")
    router = setup.router
    await router.start()
    assert router._ingress._limit == 9
    assert router._dispatcher._owner_limit == 6
    assert router._dispatcher._target_limit == 4
