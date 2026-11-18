from __future__ import annotations

import asyncio
import threading

import pytest

from plugin import settings
from plugin.core.call_registry import (
    ORIGIN_RUN,
    CallLink,
    CallRegistry,
    Origin,
    current_delivery_link,
    delivery_link,
)
from plugin.core.communication import PluginCommunicationResourceManager
from plugin.core.rpc import RpcDispatcher, RpcRemoteError, owns_deadline
from plugin.server.application.plugins import capability_gate
from plugin.server.application.plugins.call_audit import CallAudit
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging.handlers import registry as handler_registry

pytestmark = pytest.mark.plugin_unit


@pytest.fixture
def gate(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", "observe")
    grants: dict[str, set[str]] = {}
    monkeypatch.setattr(capability_gate, "registry", CallRegistry())
    audit = CallAudit(path_resolver=lambda: tmp_path / "audit.jsonl")
    monkeypatch.setattr(capability_gate, "audit", audit)
    monkeypatch.setattr(
        capability_gate, "granted_capabilities",
        lambda plugin_id: (frozenset(grants.get(plugin_id, ())), True),
    )
    yield grants
    audit.close(1.0)


def _decisions():
    return [e for e in capability_gate.audit.entries(limit=100) if e["kind"] == "decision"]


# ── registry ────────────────────────────────────────────────────


def test_register_lookup_remove():
    registry = CallRegistry()
    record = registry.register(target="a", call_id="r1", event_type="plugin_entry", event_id="x", link=None)
    assert registry.lookup("a", "r1") is record
    assert record.chain == ("a",) and record.trace_id == "r1" and record.origin.kind == "system"
    assert registry.remove(record) == {}
    assert registry.lookup("a", "r1") is None and len(registry) == 0
    assert registry.remove(record) == {}


def test_lookup_is_scoped_by_the_connection_plugin():
    registry = CallRegistry()
    registry.register(target="a", call_id="r1", event_type="plugin_entry", event_id="x", link=None)
    # Another plugin presenting a's delivery id finds nothing.
    assert registry.lookup("b", "r1") is None
    assert registry.lookup("a", "r" * 129) is None
    assert registry.lookup("a", 123) is None


def test_child_record_extends_the_parent_chain():
    registry = CallRegistry()
    root = registry.register(
        target="b", call_id="r1", event_type="plugin_entry", event_id="call",
        link=CallLink(Origin.of(ORIGIN_RUN, "run-1")),
    )
    child = registry.register(
        target="a", call_id="r2", event_type="plugin_entry", event_id="read",
        link=CallLink(root.origin, caller="b", parent=root),
    )
    assert child.chain == ("b", "a")
    assert child.trace_id == "r1" and child.parent_call_id == "r1" and child.depth == 1
    assert child.origin == Origin("run", "run-1")
    assert registry.has_live_chain("a") and not registry.has_live_chain("b")
    registry.remove(child)
    assert not registry.has_live_chain("a")


def test_registry_is_bounded():
    registry = CallRegistry(max_live=1)
    assert registry.register(target="a", call_id="1", event_type="e", event_id="x", link=None)
    assert registry.register(target="a", call_id="2", event_type="e", event_id="x", link=None) is None
    assert registry.registry_full == 1


def test_uses_are_counted_and_returned_on_removal():
    registry = CallRegistry()
    record = registry.register(target="a", call_id="1", event_type="e", event_id="x", link=None)
    registry.note_use(record, "memory.read")
    registry.note_use(record, "memory.read")
    assert registry.remove(record) == {"memory.read": 2}
    assert registry.note_use(record, "memory.read") is False


# ── decisions ───────────────────────────────────────────────────


def test_unknown_parent_is_unattributed_with_self_only_chain(gate):
    decision = capability_gate.check("a", "nope", "memory.read", "MEMORY_SEARCH")
    assert decision.chain == ("a",)
    (entry,) = _decisions()
    assert entry["flags"] == ["unattributed"] and entry["verdict"] == "would_deny"


def test_unattributed_allowed_use_is_only_counted(gate):
    gate["a"] = {"memory.read"}
    capability_gate.check("a", None, "memory.read", "MEMORY_SEARCH")
    assert _decisions() == []
    capability_gate.audit.flush_summaries(mode="observe")
    (summary,) = capability_gate.audit.entries(limit=10)
    assert summary["kind"] == "unattributed_summary"
    assert summary["counts"] == {"memory.read": 1}


def test_unjudged_capability_writes_nothing(gate):
    decision = capability_gate.check("a", None, "screen.capture", "X")
    assert decision.judged is False
    assert capability_gate.audit.entries(limit=10) == []


def test_identical_denials_are_deduplicated(gate):
    for _ in range(3):
        capability_gate.check("a", None, "memory.read", "MEMORY_SEARCH")
    assert len(_decisions()) == 1


def test_consent_read_failure_is_flagged(gate, monkeypatch):
    monkeypatch.setattr(capability_gate, "granted_capabilities", lambda plugin_id: (frozenset(), False))
    capability_gate.check("a", None, "memory.read", "MEMORY_SEARCH")
    (entry,) = _decisions()
    assert "consent_unavailable" in entry["flags"]


def test_observe_never_raises(gate):
    root = capability_gate.begin_call("b", "r1", "plugin_entry", "call", CallLink(Origin.of(ORIGIN_RUN)))
    child = capability_gate.begin_call(
        "a", "r2", "plugin_entry", "read", capability_gate.link_for_plugin_call("b", "r1"),
    )
    gate["a"] = {"memory.read"}
    decision = capability_gate.check("a", "r2", "memory.read", "MEMORY_SEARCH")
    assert decision.reason == "caller_chain"
    capability_gate.end_call(child, "ok", 0.01)
    capability_gate.end_call(root, "ok", 0.02)


def test_off_mode_skips_everything(gate, monkeypatch):
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", "off")
    assert capability_gate.begin_call("a", "r", "e", "x", None) is None
    assert capability_gate.check("a", None, "memory.read", "MEMORY_SEARCH") is None
    assert len(capability_gate.registry) == 0
    assert capability_gate.audit.entries(limit=10) == []


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (None, "ok"),
        (asyncio.CancelledError(), "CANCELLED"),
        (TimeoutError("SECRET timeout text"), "TIMEOUT"),
        (RuntimeError("SECRET body"), "ERROR"),
        (RpcRemoteError({"code": "PLUGIN_EXECUTION_FAILED", "message": "SECRET"}), "PLUGIN_EXECUTION_FAILED"),
        (RpcRemoteError({"code": "lower case SECRET", "message": "x"}), "ERROR"),
    ],
)
def test_result_codes_never_carry_text(exc, code):
    assert capability_gate.result_code(exc) == code


# ── RPC handler duality ─────────────────────────────────────────


def test_every_judged_operation_is_wrapped_the_same_way_and_nothing_else():
    handlers = handler_registry.build_request_handlers()
    wrapped = {op for op, handler in handlers.items() if hasattr(handler, "__capability__")}
    assert wrapped == set(capability_gate.CAPABILITY_BY_OPERATION)
    for operation, capability in capability_gate.CAPABILITY_BY_OPERATION.items():
        handler = handlers[operation]
        assert handler.__capability__ == capability
        assert handler.__code__ is handlers["MEMORY_QUERY"].__code__  # one entry point
        assert not hasattr(handler.__wrapped__, "__capability__")


def test_wrapper_keeps_the_deadline_marker():
    @owns_deadline
    async def handler(ctx, request):
        return "ok"

    wrapped = capability_gate.capability_checked("memory.read", handler)
    assert getattr(wrapped, "__rpc_owns_deadline__", False) is True


@pytest.mark.parametrize("operation", sorted(capability_gate.CAPABILITY_BY_OPERATION))
async def test_dispatcher_judges_each_operation_with_the_registry_parent(gate, operation):
    seen = []

    async def inner(ctx, request):
        seen.append(ctx.parent_request_id)
        return "ok"

    capability = capability_gate.CAPABILITY_BY_OPERATION[operation]
    dispatcher = RpcDispatcher({operation: capability_gate.capability_checked(capability, inner)})
    root = capability_gate.begin_call("b", "r1", "plugin_entry", "call", CallLink(Origin.of(ORIGIN_RUN)))
    child = capability_gate.begin_call(
        "a", "r2", "plugin_entry", "read", capability_gate.link_for_plugin_call("b", "r1"),
    )
    gate["a"] = {capability}
    reply = await dispatcher.dispatch({
        "type": operation, "from_plugin": "a", "request_id": "q1", "_rpc_parent_request_id": "r2",
        "_ctx": {"run_id": "forged", "caller": "x"},
    })
    assert reply["error"] is None and seen == ["r2"]
    (entry,) = _decisions()
    assert (entry["operation"], entry["chain"], entry["reason"]) == (operation, ["b", "a"], "caller_chain")
    capability_gate.end_call(child, "ok", 0)
    capability_gate.end_call(root, "ok", 0)


async def test_wrapper_off_mode_calls_straight_through(gate, monkeypatch):
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", "off")
    monkeypatch.setattr(capability_gate, "check", lambda *a, **k: pytest.fail("checked while off"))

    async def inner(ctx, request):
        return "ok"

    dispatcher = RpcDispatcher({"MEMORY_SEARCH": capability_gate.capability_checked("memory.read", inner)})
    reply = await dispatcher.dispatch({"type": "MEMORY_SEARCH", "from_plugin": "a", "request_id": "q"})
    assert reply["result"] == "ok"


async def test_domain_errors_pass_through_the_delivery_link():
    # contextlib.contextmanager would try to rewrite the frozen error's traceback.
    with pytest.raises(ServerDomainError):
        with delivery_link(CallLink(Origin.of(ORIGIN_RUN))):
            raise ServerDomainError(code="X", message="m", status_code=400)
    assert current_delivery_link() is None


# ── communication manager ───────────────────────────────────────


class _Transport:
    def __init__(self, manager_ref):
        self.sent = []
        self._manager_ref = manager_ref

    async def send_command(self, msg):
        self.sent.append(dict(msg))
        manager = self._manager_ref[0]
        loop = asyncio.get_running_loop()
        loop.call_soon(lambda: manager._rpc_peer.receive(msg["req_id"], result={"ok": 1}))


def _manager():
    ref = []
    manager = PluginCommunicationResourceManager(plugin_id="a", transport=_Transport(ref))  # type: ignore[arg-type]
    ref.append(manager)
    return manager


async def test_delivery_registers_sends_view_and_removes(gate):
    manager = _manager()
    args = {"x": 1, "_ctx": {"run_id": "run-1"}}
    with delivery_link(CallLink(Origin.of(ORIGIN_RUN, "run-1"))):
        assert await manager.trigger("entry", args, 2) == {"ok": 1}
    (msg,) = manager.transport.sent
    assert msg["args"] == {"x": 1, "_ctx": {"run_id": "run-1"}}  # args untouched
    chain = msg["_call_chain"]
    assert chain["hops"] == [["a", "plugin_entry", "entry"]]
    assert chain["origin"] == {"kind": "run", "ref": "run-1"} and chain["rights"] == []
    assert len(capability_gate.registry) == 0
    (forward,) = capability_gate.audit.entries(limit=10)
    assert forward["kind"] == "forward" and forward["call_id"] == msg["req_id"]


async def test_off_mode_delivery_has_no_chain_field(gate, monkeypatch):
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", "off")
    manager = _manager()
    await manager.trigger_custom_event("plugin_entry", "entry", {"x": 1}, 2)
    (msg,) = manager.transport.sent
    assert "_call_chain" not in msg


async def test_failed_delivery_is_removed_and_recorded_by_code(gate):
    ref = []

    class Failing(_Transport):
        async def send_command(self, msg):
            self.sent.append(dict(msg))
            manager = ref[0]
            asyncio.get_running_loop().call_soon(lambda: manager._rpc_peer.receive(
                msg["req_id"], error={"code": "PLUGIN_EXECUTION_FAILED", "message": "SECRET-x"},
            ))

    manager = PluginCommunicationResourceManager(plugin_id="a", transport=Failing(ref))  # type: ignore[arg-type]
    ref.append(manager)
    with pytest.raises(Exception):
        await manager.trigger("entry", {}, 2)
    assert len(capability_gate.registry) == 0
    (forward,) = capability_gate.audit.entries(limit=10)
    assert forward["result"] == "PLUGIN_EXECUTION_FAILED"
    assert "SECRET" not in str(forward)


async def test_link_is_read_on_the_callers_loop_and_passed_across_the_hop(gate):
    # The owner loop runs elsewhere; the link travels as an argument, not
    # through the context variable.
    owner = asyncio.new_event_loop()
    thread = threading.Thread(target=owner.run_forever, daemon=True)
    thread.start()
    try:
        manager = _manager()
        manager._owner_loop = owner
        seen = []
        original = manager._trigger_local

        async def spy(entry_id, args, timeout, *, call_link=None):
            seen.append((call_link, current_delivery_link()))
            with delivery_link(None):
                return await original(entry_id, args, timeout, call_link=call_link)

        manager._trigger_local = spy  # type: ignore[method-assign]
        link = CallLink(Origin.of(ORIGIN_RUN, "run-2"))
        with delivery_link(link):
            await manager.trigger("entry", {}, 2)
        assert seen[0][0] is link
        assert manager.transport.sent[0]["_call_chain"]["origin"]["ref"] == "run-2"
    finally:
        owner.call_soon_threadsafe(owner.stop)
        thread.join(2)
        owner.close()


def test_shutdown_flush_writes_pending_summaries(gate, tmp_path):
    gate["a"] = {"memory.read"}
    capability_gate.check("a", None, "memory.read", "MEMORY_SEARCH")
    assert capability_gate.flush_and_close(2.0) is True
    written = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert '"unattributed_summary"' in written and '"memory.read":1' in written
    capability_gate.reopen()


class _Silent:
    def __init__(self):
        self.sent = []

    async def send_command(self, msg):
        self.sent.append(dict(msg))


async def test_timed_out_delivery_is_removed(gate):
    manager = PluginCommunicationResourceManager(plugin_id="a", transport=_Silent())  # type: ignore[arg-type]
    with pytest.raises(TimeoutError):
        await manager.trigger("entry", {}, 0.05)
    assert len(capability_gate.registry) == 0
    (forward,) = capability_gate.audit.entries(limit=10)
    assert forward["result"] in {"DEADLINE_EXCEEDED", "TIMEOUT"}


async def test_cancelled_delivery_is_removed(gate):
    manager = PluginCommunicationResourceManager(plugin_id="a", transport=_Silent())  # type: ignore[arg-type]
    task = asyncio.create_task(manager.trigger("entry", {}, 5))
    async with asyncio.timeout(2):
        while len(capability_gate.registry) == 0:
            await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(capability_gate.registry) == 0
    (forward,) = capability_gate.audit.entries(limit=10)
    assert forward["result"] == "CANCELLED"
