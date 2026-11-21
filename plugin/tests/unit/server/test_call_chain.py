"""Unit tests for the call-chain pieces that the process tests do not reach directly."""

from __future__ import annotations

import asyncio
import json
import logging.handlers

import httpx
import pytest
from fastapi import FastAPI

from plugin import settings
from plugin.core.call_registry import CallLink, CallRegistry, Origin, delivery_link
from plugin.core.capability_policy import decide
from plugin.core.communication import PluginCommunicationResourceManager
from plugin.core.rpc import RpcDispatcher, owns_deadline
from plugin.sdk.shared.core.call_chain_view import call_chain_scope, current_call_chain
from plugin.server.application.plugins import call_audit, capability_gate
from plugin.server.messaging.handlers import registry as handler_registry
from plugin.server.routes.plugin_audit import router as audit_router

pytestmark = pytest.mark.plugin_unit

_REAL_GRANTED = capability_gate.granted_capabilities
GRANTS = {"a": frozenset({"memory.read"}), "b": frozenset()}


def granted(plugin_id):
    return GRANTS.get(plugin_id, frozenset())


@pytest.fixture
def gate(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", "observe")
    monkeypatch.setattr(capability_gate, "registry", CallRegistry())
    monkeypatch.setattr(capability_gate, "_last_written", {})
    monkeypatch.setattr(capability_gate, "granted_capabilities", lambda p: (granted(p), True))
    monkeypatch.setattr(call_audit, "audit_directory", lambda: tmp_path)
    monkeypatch.setattr(call_audit, "_ring", call_audit.deque(maxlen=1000))
    monkeypatch.setattr(call_audit, "_decision_ring", call_audit.deque(maxlen=500))
    monkeypatch.setattr(capability_gate, "_consent_failed_at", -60.0)
    monkeypatch.setattr(call_audit, "_abandoned", None)
    call_audit.close()
    call_audit.reopen()
    yield tmp_path
    call_audit.close()
    call_audit.reopen()


def test_decide():
    assert decide("memory.read", ("a",), granted).allowed
    # A -> B -> A: the first A is a caller even though A also uses it.
    assert decide("memory.read", ("b", "a", "b"), granted).reason == "caller_chain"
    laundering = decide("memory.read", ("b", "a"), granted)
    assert (laundering.blocked_by, laundering.reason) == (("b",), "caller_chain")
    assert decide("memory.read", ("b",), granted).reason == "self_not_approved"
    assert decide("screen.capture", ("b",), granted).judged is False


def test_view_is_never_none_and_unknown_rights_never_allow():
    view = current_call_chain("me")
    assert view.callers == () and view.origin.kind == "plugin_self"
    assert view.rights_known is False and view.allows("memory.read") is False


async def test_view_closes_for_background_work():
    wire = {"hops": [["b", "plugin_entry", "call"], ["me", "plugin_entry", "x"]],
            "origin": {"kind": "run", "ref": "r"}, "rights": [], "call_id": "c"}
    seen, release = [], asyncio.Event()

    async def background():
        seen.append(current_call_chain("me").callers)
        await release.wait()
        seen.append(current_call_chain("me").callers)

    with call_chain_scope("me", wire):
        view = current_call_chain("me")
        assert view.rights_known and not view.allows("memory.read")  # known and empty
        task = asyncio.create_task(background())
        await asyncio.sleep(0)
    release.set()
    await task
    assert seen == [("b",), ()]


def test_lookup_is_scoped_by_the_connection_plugin():
    registry = CallRegistry()
    registry.register("a", "r1", "plugin_entry", "x", None)
    assert registry.lookup("a", "r1") is not None
    assert registry.lookup("b", "r1") is None


def test_every_judged_operation_uses_the_same_wrapper_and_nothing_else():
    handlers = handler_registry.build_request_handlers()
    wrapped = {op: h.__capability__ for op, h in handlers.items() if hasattr(h, "__capability__")}
    assert wrapped == capability_gate.CAPABILITY_BY_OPERATION
    assert len({h.__code__ for op, h in handlers.items() if op in wrapped}) == 1

    @owns_deadline
    async def marked(ctx, request):
        return "ok"

    assert getattr(capability_gate.capability_checked("memory.read", marked), "__rpc_owns_deadline__", False)


@pytest.mark.parametrize("broken", ["lookup", "consent"])
async def test_observe_failures_never_change_the_call(gate, monkeypatch, broken):
    def boom(*args, **kwargs):
        raise RuntimeError("SECRET")

    if broken == "lookup":
        monkeypatch.setattr(capability_gate.registry, "lookup", boom)
    else:
        from plugin.server.infrastructure import plugin_consent

        monkeypatch.setattr(capability_gate, "granted_capabilities", _REAL_GRANTED)
        monkeypatch.setattr(plugin_consent.consent_store, "read", boom)

    async def inner(ctx, request):
        return "ok"

    dispatcher = RpcDispatcher({"MEMORY_SEARCH": capability_gate.capability_checked("memory.read", inner)})
    reply = await dispatcher.dispatch({"type": "MEMORY_SEARCH", "from_plugin": "a", "request_id": "q"})
    assert (reply["result"], reply["error"]) == ("ok", None)
    if broken == "consent":  # the check ran and recorded the failed read
        (entry,) = call_audit.entries()
        assert "consent_unavailable" in entry["flags"]
    if broken == "lookup":  # a failed link keeps the caller instead of looking host-initiated
        link = capability_gate.link_for_plugin_call("b", "r1")
        assert (link.caller, link.origin.kind) == ("b", "plugin_self")


async def test_off_calls_nothing(gate, monkeypatch):
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", "off")
    monkeypatch.setattr(capability_gate.registry, "lookup", lambda *a: pytest.fail("looked up while off"))
    assert capability_gate.check("a", "r", "memory.read", "MEMORY_SEARCH") is None


async def test_link_crosses_the_loop_hop_as_an_argument(gate):
    """The delivery link is read on the caller's loop and passed explicitly."""
    owner = asyncio.new_event_loop()
    import threading

    thread = threading.Thread(target=owner.run_forever, daemon=True)
    thread.start()
    sent = []

    class Transport:
        async def send_command(self, msg):
            assert asyncio.get_running_loop() is owner  # delivered across the hop
            sent.append(msg)
            asyncio.get_running_loop().call_soon(lambda: manager._rpc_peer.receive(msg["req_id"], result=1))

    manager = PluginCommunicationResourceManager(plugin_id="a", transport=Transport())  # type: ignore[arg-type]
    manager._owner_loop = owner
    original = manager._trigger_local

    async def without_context(*args, call_link=None, **kwargs):
        with delivery_link(None):  # only the explicit argument is left
            return await original(*args, call_link=call_link, **kwargs)

    manager._trigger_local = without_context  # type: ignore[method-assign]
    try:
        running = threading.Event()
        owner.call_soon_threadsafe(running.set)
        ready = running.wait(2)
        assert ready  # the owner loop is really running elsewhere
        with delivery_link(CallLink(Origin.of("run", "run-2"))):
            assert await manager.trigger("entry", {"x": 1}, 2) == 1
    finally:
        owner.call_soon_threadsafe(owner.stop)
        thread.join(2)
        owner.close()
    assert sent[0]["args"] == {"x": 1}
    assert sent[0]["_call_chain"]["origin"] == {"kind": "run", "ref": "run-2"}
    assert len(capability_gate.registry) == 0


def test_audit_uses_the_stdlib_rotating_writer_and_writes_json_lines(gate):
    call_audit.record({"kind": "forward", "target": "a"})
    handler = call_audit._listener.handlers[0]
    assert isinstance(handler, logging.handlers.RotatingFileHandler)
    assert (handler.maxBytes, handler.backupCount) == (4 * 1024 * 1024, 3)
    assert logging.getLogger("neko.plugin_call_audit").propagate is False
    flushed = call_audit.close()
    assert flushed is True
    (line,) = (gate / "call_audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["target"] == "a"
    call_audit.record({"kind": "forward", "target": "late"})  # after close: ring only
    assert call_audit._listener is None and call_audit.entries()[0]["target"] == "late"


def test_audit_close_is_bounded_when_the_disk_is_stuck(gate, monkeypatch):
    import threading
    import time

    stuck, writing = threading.Event(), threading.Event()
    real_emit = logging.handlers.RotatingFileHandler.emit

    def blocked_emit(self, record):
        writing.set()
        stuck.wait(30)
        real_emit(self, record)

    monkeypatch.setattr(logging.handlers.RotatingFileHandler, "emit", blocked_emit)
    baseline = call_audit.dropped
    stuck_writer = None
    try:
        for index in range(3000):  # more than the queue holds
            call_audit.record({"kind": "forward", "n": index})
        assert writing.wait(5)  # the writer holds the handler inside emit
        assert call_audit.dropped > baseline
        started = time.monotonic()
        flushed = call_audit.close(0.3)
        assert flushed is False
        assert time.monotonic() - started < 1.5
        flushed = call_audit.close(0.1)  # still not exited: no false success
        assert flushed is False
        # logging.shutdown() at exit must not wait on the stuck handler's lock.
        stuck_writer = call_audit._abandoned
        assert not any(ref() in stuck_writer.handlers for ref in logging._handlerList)
        # The stuck writer keeps the file: a restart must not start a second one ...
        call_audit.reopen()
        before = call_audit.dropped
        call_audit.record({"kind": "forward", "n": "after"})
        assert call_audit._listener is None and call_audit.dropped == before + 1
    finally:
        stuck.set()
        if stuck_writer is not None:  # the reserved slot let close() queue its stop
            stuck_writer._thread.join(10)
    # ... it exits on its own once the disk recovers, and the next record takes over.
    assert not stuck_writer._thread.is_alive()
    call_audit.record({"kind": "forward", "n": "resumed"})
    assert call_audit._listener is not None


def test_a_close_issued_before_a_restart_leaves_the_new_run_open(gate):
    stale = call_audit.generation()
    call_audit.reopen()  # restart wins the race against the old shutdown's close
    closed = call_audit.close(0.1, stale)
    assert closed is True
    call_audit.record({"kind": "forward", "n": "after"})
    assert call_audit._listener is not None and not call_audit._closed


def test_unwritable_audit_file_is_counted_as_dropped(gate, monkeypatch):
    blocker = gate / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setattr(call_audit, "audit_directory", lambda: blocker)
    baseline = call_audit.dropped
    call_audit.record({"kind": "forward"})
    flushed = call_audit.close(2.0)
    assert flushed is True
    assert call_audit.dropped == baseline + 1


def test_failed_rights_lookup_does_not_leak_the_registration(gate, monkeypatch):
    def broken(plugin_id):
        raise TypeError("malformed consent")

    monkeypatch.setattr(capability_gate, "granted_capabilities", broken)
    assert capability_gate.begin_call("a", "r1", "plugin_entry", "x", None) is None
    assert len(capability_gate.registry) == 0 and not capability_gate.registry.has_live_chain("a")


@pytest.mark.parametrize(
    ("client", "base", "headers", "status"),
    [
        ("127.0.0.1", "http://127.0.0.1:48916", {}, 200),
        ("192.168.1.5", "http://127.0.0.1:48916", {}, 403),
        ("127.0.0.1", "http://nas.local:48916", {}, 403),
        ("127.0.0.1", "http://127.0.0.1:48916", {"X-Forwarded-For": "10.0.0.2"}, 403),
        ("127.0.0.1", "http://127.0.0.1:48916", {"Origin": "http://127.0.0.1:48911"}, 403),
        # A same-origin page omits Origin on GET but sends fetch metadata.
        ("127.0.0.1", "http://127.0.0.1:48916", {"Sec-Fetch-Site": "same-origin"}, 403),
        # Node / Electron fetch sends Sec-Fetch-Mode only; a typed URL sends Site: none.
        ("127.0.0.1", "http://127.0.0.1:48916", {"Sec-Fetch-Mode": "cors"}, 200),
        ("127.0.0.1", "http://127.0.0.1:48916", {"Sec-Fetch-Site": "none"}, 200),
    ],
)
async def test_audit_route_is_local_only(gate, client, base, headers, status):
    app = FastAPI()
    app.include_router(audit_router)
    transport = httpx.ASGITransport(app=app, client=(client, 50000))
    async with httpx.AsyncClient(transport=transport, base_url=base) as http:
        response = await http.get("/plugin-audit/call-chain", headers=headers)
    assert response.status_code == status
    if status == 200:
        assert response.json()["mode"] == "observe"


@pytest.mark.parametrize("ending", ["ok", "CANCELLED"])
def test_push_arriving_after_the_call_ended_keeps_its_chain(gate, monkeypatch, ending):
    # A push rides its own uplink and may be routed after the call's reply.
    monkeypatch.setattr(capability_gate, "granted_capabilities", lambda p: ({"a": frozenset({"messages.push"})}.get(p, frozenset()), True))
    root = capability_gate.begin_call("b", "r1", "plugin_entry", "call", CallLink(Origin("run")))
    child = capability_gate.begin_call("a", "r2", "plugin_entry", "push", capability_gate.link_for_plugin_call("b", "r1"))
    capability_gate.end_call(child, ending, 0)  # returned, or the caller cancelled it
    capability_gate.check("a", "r2", "messages.push", "MESSAGE_PUSH")
    capability_gate.check("a", "r2", "memory.read", "MEMORY_SEARCH")  # control RPCs alike
    capability_gate.end_call(root, "ok", 0)
    decisions = [e for e in call_audit.entries() if e["kind"] == "decision"]
    assert sorted((d["operation"], tuple(d["chain"]), d["verdict"], d["reason"]) for d in decisions) == [
        ("MEMORY_SEARCH", ("b", "a"), "would_deny", "caller_chain"),
        ("MESSAGE_PUSH", ("b", "a"), "would_deny", "caller_chain"),
    ]
    assert len(capability_gate.registry) == 0


def test_decisions_are_not_evicted_by_forward_entries(gate):
    call_audit.record({"kind": "decision", "verdict": "would_deny"})
    for index in range(1500):
        call_audit.record({"kind": "forward", "n": index})
    assert [e for e in call_audit.entries() if e["kind"] == "decision"]


def test_failed_consent_reads_are_not_retried_on_every_use(gate, monkeypatch):
    from plugin.server.infrastructure import plugin_consent

    reads = []

    def broken(plugin_id):
        reads.append(plugin_id)
        raise OSError("unreadable")

    monkeypatch.setattr(capability_gate, "granted_capabilities", _REAL_GRANTED)
    monkeypatch.setattr(plugin_consent.consent_store, "read", broken)
    assert capability_gate.granted_capabilities("a") == (frozenset(), False)
    assert capability_gate.granted_capabilities("a") == (frozenset(), False)
    assert reads == ["a"]


async def test_internal_push_field_is_never_forwarded(monkeypatch):
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", "off")
    forwarded = []

    async def forward(self, msg):
        forwarded.append(msg)

    monkeypatch.setattr(PluginCommunicationResourceManager, "_forward_message", forward)
    manager = PluginCommunicationResourceManager(plugin_id="a", transport=object())  # type: ignore[arg-type]
    await manager._route_message({"type": "MESSAGE_PUSH", "_rpc_parent_request_id": "r1", "parts": []})
    assert "_rpc_parent_request_id" not in forwarded[0]


def test_a_malformed_record_only_affects_its_own_plugin(gate, monkeypatch):
    from plugin.server.infrastructure import plugin_consent

    records = {"bad": {"decision": "approved", "approved_capabilities": 7},
               "good": {"decision": "approved", "approved_capabilities": ["memory.read"]}}
    monkeypatch.setattr(capability_gate, "granted_capabilities", _REAL_GRANTED)
    monkeypatch.setattr(plugin_consent.consent_store, "read", lambda plugin_id: records[plugin_id])
    assert capability_gate.granted_capabilities("bad") == (frozenset(), False)
    assert capability_gate.granted_capabilities("good") == (frozenset({"memory.read"}), True)


async def test_audit_route_returns_nothing_when_off(gate, monkeypatch):
    call_audit.record({"kind": "forward"})
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", "off")
    app = FastAPI()
    app.include_router(audit_router)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:48916") as http:
        body = (await http.get("/plugin-audit/call-chain")).json()
    assert body["mode"] == "off" and body["entries"] == []


def test_audit_keeps_only_identifier_shaped_event_names_and_valid_grants(gate, monkeypatch):
    from plugin.server.infrastructure import plugin_consent

    record = capability_gate.begin_call("a", "r1", "plugin_entry", "user said: hello there", None)
    capability_gate.end_call(record, "ok", 0)
    (forward,) = call_audit.entries()
    assert forward["event_id"] == "<invalid>" and "hello" not in json.dumps(forward)
    for event_id in ("", "搜索", "tools/search"):  # empty (voice dispatch), Unicode, path-like
        capability_gate.end_call(capability_gate.begin_call("a", "r2", "plugin_entry", event_id, None), "ok", 0)
        assert call_audit.entries()[0]["event_id"] == event_id
    depth_error = RuntimeError()
    depth_error.code = "CALL_DEPTH_EXCEEDED"
    assert capability_gate.result_code(depth_error) == "CALL_DEPTH_EXCEEDED"
    monkeypatch.setattr(capability_gate, "granted_capabilities", _REAL_GRANTED)
    monkeypatch.setattr(plugin_consent.consent_store, "read",
                        lambda plugin_id: {"decision": "approved", "approved_capabilities": {"memory.read": False}})
    assert capability_gate.granted_capabilities("a") == (frozenset(), False)
    monkeypatch.setattr(plugin_consent.consent_store, "read", lambda plugin_id: {"decision": "approved"})
    assert capability_gate.granted_capabilities("a") == (frozenset(), True)  # missing = none, as derive_consent


async def test_audit_route_returns_every_retained_entry(gate, monkeypatch):
    # Identical timestamps: order comes from write order, not the clock.
    class Frozen:
        @staticmethod
        def now(tz=None):
            from datetime import datetime, timezone
            return datetime(2026, 1, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(call_audit, "datetime", Frozen)
    call_audit.record({"kind": "decision", "verdict": "would_deny", "n": "old"})
    for index in range(1000):
        call_audit.record({"kind": "forward", "n": index})
    call_audit.record({"kind": "decision", "verdict": "would_deny"})
    app = FastAPI()
    app.include_router(audit_router)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:48916") as http:
        body = (await http.get("/plugin-audit/call-chain")).json()
    assert len(body["entries"]) == 1002 and body["entries"][0]["kind"] == "decision"
    # A decision older than a full ring of forwards is still returned.
    assert body["entries"][-1] == {**body["entries"][-1], "kind": "decision", "n": "old"}
