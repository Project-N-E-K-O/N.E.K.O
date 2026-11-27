# -*- coding: utf-8 -*-
"""``register_llm_tool_async`` / ``unregister_llm_tool_async`` report what
main_server actually answered, and keep the SDK's local ``_llm_tools`` in
step with the host's ``_plugin_tools`` (issue #3321).

Three layers:

* SDK only -- a fake host request function; checks result shapes and the
  local commit / rollback rules.
* Host handlers -- ``plugin/server/messaging/handlers/llm_tools.py`` with a
  fake main_server HTTP client; checks the per-role outcome is passed back,
  the plugin id comes from the connection, and host tracking.
* End to end -- real ``PluginContext`` + ``RpcPeer`` + ``RpcDispatcher`` +
  host handlers, fake main_server only; covers the host timeout path.

No real HTTP, no real plugin process, no real main_server.
"""
from __future__ import annotations

import asyncio
import types
import logging
import queue
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


PLUGIN_ID = "demo_plugin"


def _make_plugin_ctx():
    from plugin.core.context import PluginContext

    return PluginContext(
        plugin_id=PLUGIN_ID,
        config_path=Path("plugin.toml"),
        logger=logging.getLogger("test"),
        status_queue=queue.Queue(),
        message_queue=queue.Queue(),
    )


def _make_plugin(ctx=None):
    from plugin.sdk.plugin import NekoPluginBase, neko_plugin

    @neko_plugin
    class P(NekoPluginBase):
        pass

    ctx = ctx or _make_plugin_ctx()
    return P(ctx), ctx


def _drain(ctx) -> list[dict]:
    msgs: list[dict] = []
    while not ctx.message_queue.empty():
        msgs.append(ctx.message_queue.get_nowait())
    return msgs


async def _handler(**kwargs):
    return kwargs


def _register_kwargs(name: str = "tool_a", **extra):
    kwargs = dict(
        name=name,
        description="d",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        timeout=12.0,
        role="Lanlan",
    )
    kwargs.update(extra)
    return kwargs


class _FakeHost:
    """Stands in for ``PluginContext._send_request_and_wait_async``."""

    def __init__(self, reply=None, exc: BaseException | None = None, delay: float = 0.0):
        self.reply = reply
        self.exc = exc
        self.delay = delay
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.reply


def _install_host(plugin, host) -> None:
    # The SDK reads the request function off the raw host context.
    plugin._host_ctx._send_request_and_wait_async = host


def _entry_registered(plugin, name: str) -> bool:
    return f"__llm_tool__{name}" in plugin._dynamic_entries


# ---------------------------------------------------------------------------
# SDK: register
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_async_success_keeps_local_and_reports_roles():
    plugin, ctx = _make_plugin()
    host = _FakeHost(reply={
        "ok": True, "affected_roles": ["Lanlan"], "failed_roles": [], "error": None, "message": None,
        "cleanup": None,
    })
    _install_host(plugin, host)

    result = await plugin.register_llm_tool_async(**_register_kwargs(reply_tail=True), request_timeout=3.0)

    assert result == {
        "ok": True, "affected_roles": ["Lanlan"], "failed_roles": [], "error": None, "message": None,
        "cleanup": None,
    }
    assert [t["name"] for t in plugin.list_llm_tools()] == ["tool_a"]
    assert _entry_registered(plugin, "tool_a")
    (call,) = host.calls
    assert call["request_type"] == "LLM_TOOL_REGISTER_REQUEST"
    assert 0 < call["timeout"] <= 3.0  # budget left after taking the name lock
    assert call["wrap_result"] is False
    payload = call["request_data"]
    assert payload["name"] == "tool_a"
    assert payload["role"] == "Lanlan"
    assert payload["timeout_seconds"] == 12.0
    assert payload["reply_tail"] is True
    # The async path must not also emit the fire-and-forget notification,
    # or the host would register the tool twice.
    assert not [m for m in _drain(ctx) if m.get("type") == "LLM_TOOL_REGISTER"]


@pytest.mark.asyncio
async def test_register_async_partial_role_failure_is_ok_and_kept():
    plugin, _ = _make_plugin()
    _install_host(plugin, _FakeHost(reply={
        "ok": True,
        "affected_roles": ["A"],
        "failed_roles": [{"role": "B", "error": "RuntimeError: sync failed"}],
        "error": None,
    }))

    result = await plugin.register_llm_tool_async(**_register_kwargs(role=None))

    assert result["ok"] is True
    assert result["affected_roles"] == ["A"]
    assert result["failed_roles"] == [{"role": "B", "error": "RuntimeError: sync failed"}]
    assert [t["name"] for t in plugin.list_llm_tools()] == ["tool_a"]


@pytest.mark.asyncio
async def test_register_async_foreign_owner_rolls_back_and_allows_retry():
    plugin, ctx = _make_plugin()
    _install_host(plugin, _FakeHost(reply={
        "ok": False,
        "affected_roles": [],
        "failed_roles": [{"role": "*", "error": "cross-source ledger overwrite refused"}],
        "error": "rejected",
        "message": "main_server rejected register",
    }))

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False
    assert result["error"] == "rejected"
    assert result["failed_roles"] == [{"role": "*", "error": "cross-source ledger overwrite refused"}]
    assert plugin.list_llm_tools() == []
    assert not _entry_registered(plugin, "tool_a")
    # The host was told the dynamic entry is gone again.
    updates = [m for m in _drain(ctx) if m.get("type") == "ENTRY_UPDATE"]
    assert [u["action"] for u in updates] == ["register", "unregister"]
    # Rolled back cleanly: the same name can be registered again.
    _install_host(plugin, _FakeHost(reply={"ok": True, "affected_roles": ["Lanlan"], "failed_roles": []}))
    retry = await plugin.register_llm_tool_async(**_register_kwargs())
    assert retry["ok"] is True
    assert [t["name"] for t in plugin.list_llm_tools()] == ["tool_a"]


class _ScriptedHost:
    """Answers each request with the next scripted reply (or raises it)."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


_ALL_SYNC_FAILED = {
    "ok": False, "affected_roles": [],
    "failed_roles": [{"role": "Lanlan", "error": "RuntimeError: session sync failed"}],
    "error": "rejected", "message": "main_server rejected register",
}
_CLEANED = {"ok": True, "removed": True, "affected_roles": ["Lanlan"], "failed_roles": [], "refused_roles": []}


@pytest.mark.asyncio
async def test_failed_register_cleans_up_what_main_server_may_have_stored():
    """Every role's session sync failed: main_server answers ok=false but
    keeps the tool in its registry. The SDK must unregister it."""
    plugin, _ = _make_plugin()
    host = _ScriptedHost(_ALL_SYNC_FAILED, _CLEANED)
    _install_host(plugin, host)

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False and result["error"] == "rejected"
    assert result["cleanup"] is not None
    assert result["cleanup"]["ok"] is True and result["cleanup"]["removed"] is True
    assert [c["request_type"] for c in host.calls] == ["LLM_TOOL_REGISTER_REQUEST", "LLM_TOOL_UNREGISTER_REQUEST"]
    cleanup = host.calls[1]
    assert cleanup["request_data"]["name"] == "tool_a"
    assert cleanup["request_data"]["role"] == "Lanlan"
    assert plugin.list_llm_tools() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("first", [
    TimeoutError("late"),
    {"ok": False, "error": "timeout", "message": "ReadTimeout: x"},
    {"ok": False, "error": "host_unavailable"},
    # The host's own withdrawal after a plugin stop may have failed.
    {"ok": False, "error": "plugin_stopped"},
    RuntimeError("Plugin context is closed"),
])
async def test_register_failures_that_may_have_stored_are_cleaned_up(first):
    plugin, _ = _make_plugin()
    host = _ScriptedHost(first, _CLEANED)
    _install_host(plugin, host)

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False
    assert result["cleanup"]["ok"] is True
    assert len(host.calls) == 2


@pytest.mark.asyncio
async def test_failed_cleanup_is_reported_not_raised():
    plugin, _ = _make_plugin()
    host = _ScriptedHost(_ALL_SYNC_FAILED, TimeoutError("late"))
    _install_host(plugin, host)

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False and result["error"] == "rejected"
    assert result["cleanup"]["ok"] is False and result["cleanup"]["error"] == "timeout"
    assert host.calls[1]["timeout"] == 15.0
    assert plugin.list_llm_tools() == []
    # The documented way out: unregister later (name not held locally).
    _install_host(plugin, _ScriptedHost(_CLEANED))
    assert (await plugin.unregister_llm_tool_async("tool_a"))["removed"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("error", ["invalid_request", "main_server_unavailable", "main_server_error"])
async def test_register_failures_that_stored_nothing_send_no_cleanup(error):
    """e.g. main_server 404 for an unknown role, before anything is stored:
    a cleanup with the same role would only fail the same way."""
    plugin, _ = _make_plugin()
    host = _ScriptedHost({"ok": False, "error": error})
    _install_host(plugin, host)
    result = await plugin.register_llm_tool_async(**_register_kwargs())
    assert result["ok"] is False
    assert result["cleanup"] is None and len(host.calls) == 1
    assert plugin.list_llm_tools() == []


@pytest.mark.asyncio
async def test_success_sends_no_cleanup():
    plugin, _ = _make_plugin()

    host = _ScriptedHost({"ok": True, "affected_roles": ["A"], "failed_roles": [{"role": "B", "error": "x"}]})
    _install_host(plugin, host)
    result = await plugin.register_llm_tool_async(**_register_kwargs())
    assert result["ok"] is True and result["cleanup"] is None and len(host.calls) == 1


@pytest.mark.asyncio
async def test_cancelled_register_sends_no_cleanup():
    plugin, _ = _make_plugin()
    host = _FakeHost(reply={"ok": True}, delay=10.0)
    _install_host(plugin, host)

    task = asyncio.create_task(plugin.register_llm_tool_async(**_register_kwargs()))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(host.calls) == 1


@pytest.mark.asyncio
async def test_retry_waits_for_the_cleanup_of_a_failed_register():
    """The cleanup runs under the name lock, so a retry queued behind the
    failed register is sent only after the cleanup: the cleanup can never
    remove the retry's tool."""
    plugin, _ = _make_plugin()
    host = _StatefulHost(delays=[0.05, 0.0])
    first = {"pending": True}

    async def host_call(**kwargs):
        if first["pending"]:
            first["pending"] = False
            host.order.append(kwargs["request_type"])
            host.remote.add(kwargs["request_data"]["name"])  # stored, then every sync failed
            return dict(_ALL_SYNC_FAILED)
        return await host(**kwargs)

    _install_host(plugin, host_call)
    failed, retried = await asyncio.gather(
        plugin.register_llm_tool_async(**_register_kwargs()),
        plugin.register_llm_tool_async(**_register_kwargs()),
    )

    assert failed["ok"] is False and failed["cleanup"]["removed"] is True
    assert retried["ok"] is True
    assert host.order == [
        "LLM_TOOL_REGISTER_REQUEST", "LLM_TOOL_UNREGISTER_REQUEST", "LLM_TOOL_REGISTER_REQUEST",
    ]
    assert host.remote == {"tool_a"}
    assert _local_names(plugin) == {"tool_a"}


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [60.5, 120])
async def test_request_timeout_above_host_limit_is_refused(bad):
    plugin, _ = _make_plugin()
    host = _FakeHost(reply={"ok": True})
    _install_host(plugin, host)

    with pytest.raises(ValueError):
        await plugin.register_llm_tool_async(**_register_kwargs(), request_timeout=bad)
    with pytest.raises(ValueError):
        await plugin.unregister_llm_tool_async("tool_a", request_timeout=bad)
    assert host.calls == []
    ok = await plugin.register_llm_tool_async(**_register_kwargs(), request_timeout=60)
    assert ok["ok"] is True


def test_request_timeout_limit_matches_host_dispatcher():
    from plugin.core.rpc import MAX_HANDLER_TIMEOUT_SECONDS
    from plugin.sdk.plugin import base

    assert base._LLM_TOOL_MAX_REQUEST_TIMEOUT == MAX_HANDLER_TIMEOUT_SECONDS
    assert base.DEFAULT_LLM_TOOL_REQUEST_TIMEOUT <= MAX_HANDLER_TIMEOUT_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [".", "..", "..."])
async def test_async_calls_refuse_dot_only_names(name):
    plugin, _ = _make_plugin()
    host = _FakeHost(reply={"ok": True})
    _install_host(plugin, host)

    with pytest.raises(ValueError):
        await plugin.register_llm_tool_async(**_register_kwargs(name))
    with pytest.raises(ValueError):
        await plugin.unregister_llm_tool_async(name)
    assert host.calls == []


@pytest.mark.parametrize("name", [".", "..", "..."])
def test_sync_calls_refuse_dot_only_names(name):
    """Such a name is normalised away in the callback URL path, so a tool
    registered under it could never be called: the sync calls refuse it like
    the async ones, before anything is stored or sent to the host."""
    plugin, ctx = _make_plugin()
    while not ctx.message_queue.empty():
        ctx.message_queue.get_nowait()

    with pytest.raises(ValueError):
        plugin.register_llm_tool(**_register_kwargs(name))
    with pytest.raises(ValueError):
        plugin.unregister_llm_tool(name)
    assert plugin.list_llm_tools() == []
    assert not _entry_registered(plugin, name)
    assert ctx.message_queue.empty()


def test_sync_calls_still_accept_names_with_dots():
    plugin, ctx = _make_plugin()
    assert plugin.register_llm_tool(**_register_kwargs("a.b")) is True
    assert plugin.register_llm_tool(**_register_kwargs(".hidden")) is True
    assert plugin.unregister_llm_tool("a.b") is True
    assert plugin.unregister_llm_tool(".hidden") is True


@pytest.mark.parametrize("name", [".", ".."])
def test_decorator_refuses_dot_only_names(name):
    from plugin.sdk.plugin.llm_tool import llm_tool

    with pytest.raises(ValueError):
        llm_tool(name=name)(lambda self: None)


# ---------------------------------------------------------------------------
# registration_id: cleanup / unregister target one registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_async_sends_a_fresh_registration_id_and_cleanup_targets_it():
    plugin, _ = _make_plugin()
    host = _ScriptedHost(_ALL_SYNC_FAILED, _CLEANED, {"ok": True, "affected_roles": ["Lanlan"], "failed_roles": []})
    _install_host(plugin, host)

    await plugin.register_llm_tool_async(**_register_kwargs())
    await plugin.register_llm_tool_async(**_register_kwargs())

    first_id = host.calls[0]["request_data"]["registration_id"]
    assert first_id and host.calls[1]["request_data"]["expected_registration_id"] == first_id
    second_id = host.calls[2]["request_data"]["registration_id"]
    assert second_id and second_id != first_id


@pytest.mark.asyncio
async def test_unregister_async_targets_the_registration_it_holds():
    plugin, _ = await _plugin_with_tool()
    held = plugin._llm_tools["tool_a"].registration_id
    host = _FakeHost(reply=dict(_CLEANED))
    _install_host(plugin, host)

    await plugin.unregister_llm_tool_async("tool_a")

    assert held and host.calls[0]["request_data"]["expected_registration_id"] == held


@pytest.mark.asyncio
async def test_unregister_async_without_a_held_registration_id_targets_any():
    """Sync / decorator tools and names not held locally: no id, so any
    registration of this plugin is removed, as before."""
    plugin, _ = _make_plugin()
    plugin.register_llm_tool(**_register_kwargs("sync_tool"))
    host = _FakeHost(reply=dict(_CLEANED))
    _install_host(plugin, host)

    await plugin.unregister_llm_tool_async("sync_tool")
    await plugin.unregister_llm_tool_async("not_held")

    assert all("expected_registration_id" not in c["request_data"] for c in host.calls)


def test_sync_register_payload_has_no_registration_id():
    plugin, ctx = _make_plugin()
    _drain(ctx)
    plugin.register_llm_tool(**_register_kwargs())
    (msg,) = [m for m in _drain(ctx) if m.get("type") == "LLM_TOOL_REGISTER"]
    assert "registration_id" not in msg


class _MainServerWithIds:
    """Fake host + main_server keyed by registration id. A request keeps
    running at "main_server" after the SDK stopped waiting for it, like a
    real one whose caller timed out."""

    def __init__(self):
        self.remote: dict[str, str] = {}  # name -> registration_id
        self.unregister_delay = 0.0
        self.background: list[asyncio.Task] = []
        self.first_register_fails = True

    async def _apply(self, request_type, data):
        name = data["name"]
        if request_type == "LLM_TOOL_REGISTER_REQUEST":
            self.remote[name] = data["registration_id"]
            if self.first_register_fails:
                self.first_register_fails = False
                return dict(_ALL_SYNC_FAILED)  # stored, every sync failed
            return {"ok": True, "affected_roles": ["Lanlan"], "failed_roles": []}
        await asyncio.sleep(self.unregister_delay)
        expected = data.get("expected_registration_id")
        current = self.remote.get(name)
        if current is not None and expected is not None and current != expected:
            return {"ok": True, "removed": False, "affected_roles": [], "failed_roles": [],
                    "refused_roles": [{"role": "Lanlan", "owned_by": "plugin:x", "reason": "different registration"}]}
        removed = self.remote.pop(name, None) is not None
        return {"ok": True, "removed": removed, "affected_roles": [], "failed_roles": [], "refused_roles": []}

    async def __call__(self, *, request_type, request_data, timeout, **_):
        task = asyncio.ensure_future(self._apply(request_type, dict(request_data)))
        self.background.append(task)
        return await asyncio.wait_for(asyncio.shield(task), timeout)


@pytest.mark.asyncio
async def test_unregister_removes_a_stale_registration_that_displaced_the_held_one():
    """A timed-out register landed late and replaced the registration the
    plugin holds. An id-targeted unregister is refused there; the SDK must
    not report ok and drop the handler while main_server keeps the stale
    one: it removes the name without the filter."""
    plugin, _ = await _plugin_with_tool()
    host = _ScriptedHost(
        {"ok": True, "removed": False, "affected_roles": [], "failed_roles": [],
         "refused_roles": [{"role": "Lanlan", "owned_by": "plugin:demo_plugin", "reason": "different registration", "registration_id": "stale"}]},
        dict(_CLEANED),
    )
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is True and result["removed"] is True
    held = host.calls[0]["request_data"]["expected_registration_id"]
    assert held and held != "stale"
    # Exactly the registration main_server reported, never an unfiltered one
    # that a late arrival could turn against a later registration.
    assert host.calls[1]["request_data"]["expected_registration_id"] == "stale"
    assert _local_names(plugin) == set()


@pytest.mark.asyncio
async def test_stale_registration_without_a_reported_id_is_not_removed_broadly():
    plugin, _ = await _plugin_with_tool()
    host = _ScriptedHost(
        {"ok": True, "removed": False, "affected_roles": [], "failed_roles": [],
         "refused_roles": [{"role": "Lanlan", "owned_by": "plugin:demo_plugin", "reason": "different registration"}]},
    )
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is False and result["error"] == "partial_failure"
    assert len(host.calls) == 1
    assert _local_names(plugin) == {"tool_a"}


@pytest.mark.asyncio
async def test_stale_removal_follows_registrations_revealed_by_a_follow_up():
    """The follow-up for one reported id reveals yet another registration
    holding the name: it is removed too, by its own id."""
    plugin, _ = await _plugin_with_tool()
    host = _ScriptedHost(_held_by("stale"), _held_by("newer"), dict(_CLEANED))
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is True
    assert [c["request_data"].get("expected_registration_id") for c in host.calls[1:]] == ["stale", "newer"]
    assert _local_names(plugin) == set()


def _held_by(*holders, removed=False, affected=()):
    """An unregister answer where each (role, id) in holders is another
    registration of this plugin holding the name."""
    pairs = [h if isinstance(h, tuple) else ("Lanlan", h) for h in holders]
    return {
        "ok": True, "removed": removed, "affected_roles": list(affected), "failed_roles": [],
        "refused_roles": [
            {"role": role, "owned_by": "plugin:demo_plugin", "reason": "different registration",
             "registration_id": rid}
            for role, rid in pairs
        ],
    }


@pytest.mark.asyncio
async def test_stale_removal_tries_every_reported_id():
    """Two roles held by two different registrations (C, D): removing C
    still reports D, which must then be removed too instead of stopping."""
    # A global tool (role=None): main_server visits every role, so
    # refusals and removals can come from several of them.
    plugin, _ = await _plugin_with_tool(role=None)
    host = _ScriptedHost(
        _held_by(("R2", "C"), ("R3", "D")),
        _held_by(("R3", "D"), removed=True, affected=["R2"]),
        {"ok": True, "removed": True, "affected_roles": ["R3"], "failed_roles": [], "refused_roles": []},
    )
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is True and result["refused_roles"] == []
    assert [c["request_data"].get("expected_registration_id") for c in host.calls[1:]] == ["C", "D"]
    assert result["affected_roles"] == ["R2", "R3"]
    assert all(c["request_data"]["role"] is None for c in host.calls)


@pytest.mark.asyncio
async def test_stale_removal_retries_a_registration_that_shows_up_again():
    """C is removed, but a later answer reports C on another role again (a
    timed-out register still writing roles): it gets one more targeted
    attempt, and the call is only ok once it is gone."""
    plugin, _ = await _plugin_with_tool(role=None)
    host = _ScriptedHost(
        _held_by(("R1", "C"), ("R2", "D")),
        _held_by(("R2", "D"), removed=True, affected=["R1"]),   # C removed
        _held_by(("R3", "C"), removed=True, affected=["R2"]),   # D removed, C back on R3
        {"ok": True, "removed": True, "affected_roles": ["R3"], "failed_roles": [], "refused_roles": []},
    )
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is True
    assert [c["request_data"].get("expected_registration_id") for c in host.calls[1:]] == ["C", "D", "C"]
    assert all(c["request_data"]["role"] is None for c in host.calls)


@pytest.mark.asyncio
async def test_stale_removal_is_not_ok_when_a_registration_keeps_coming_back():
    """Two registrations keep alternating: main_server refuses the request
    targeting C because D holds the role, and the one targeting D because C
    holds it (the only way it answers "different registration"). Each id
    gets at most two targeted attempts, then the call gives up."""
    plugin, _ = await _plugin_with_tool(role=None)
    host = _ScriptedHost(
        _held_by(("R1", "C")),
        _held_by(("R1", "D")),  # targeting C: D holds R1
        _held_by(("R1", "C")),  # targeting D: C holds R1
        _held_by(("R1", "D")),
        _held_by(("R1", "C")),
    )
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a")

    targeted = [c["request_data"].get("expected_registration_id") for c in host.calls[1:]]
    assert targeted == ["C", "D", "C", "D"]
    assert max(targeted.count(rid) for rid in set(targeted)) <= 2
    assert result["ok"] is False and result["error"] == "partial_failure"
    assert any(r.get("registration_id") == "C" for r in result["refused_roles"])
    assert _local_names(plugin) == {"tool_a"}


@pytest.mark.asyncio
async def test_failed_follow_up_still_reports_a_registration_that_came_back():
    """C is removed by id; the follow-up for D then fails, and its answer
    reports C on another role again (a timed-out register still writing
    roles). C must be listed in refused_roles, not counted as removed."""
    plugin, _ = await _plugin_with_tool(role=None)
    failed_with_c_back = _held_by(("R3", "C"), removed=True, affected=["R2"])
    failed_with_c_back.update(
        ok=False, error="partial_failure", message="some roles failed",
        failed_roles=[{"role": "R4", "error": "RuntimeError: sync failed"}],
    )
    host = _ScriptedHost(
        _held_by(("R1", "C"), ("R2", "D")),
        _held_by(("R2", "D"), removed=True, affected=["R1"]),   # C removed
        failed_with_c_back,                                      # D: fails, C back on R3
    )
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert [c["request_data"].get("expected_registration_id") for c in host.calls[1:]] == ["C", "D"]
    assert result["ok"] is False and result["error"] == "partial_failure"
    held = {(r["role"], r["registration_id"]) for r in result["refused_roles"]}
    assert ("R3", "C") in held
    # R1 was removed by the targeted request for C; only R3 still holds it.
    assert ("R1", "C") not in held
    assert _local_names(plugin) == {"tool_a"}


@pytest.mark.asyncio
async def test_registration_that_came_back_reports_only_the_roles_it_holds():
    """C is removed from R1, then reported on R3 again, and keeps coming
    back after its second (last) targeted attempt. refused_roles lists
    ("R3", "C") only, not ("R1", "C") where its removal succeeded."""
    plugin, _ = await _plugin_with_tool(role=None)
    host = _ScriptedHost(
        _held_by(("R1", "C"), ("R2", "D")),
        _held_by(("R2", "D"), removed=True, affected=["R1"]),   # C removed from R1
        _held_by(("R3", "C"), removed=True, affected=["R2"]),   # D removed, C back on R3
        _held_by(("R4", "E"), removed=True, affected=["R3"]),   # C removed from R3, E on R4
        _held_by(("R3", "C"), removed=True, affected=["R4"]),   # E removed, C back on R3
    )
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert [c["request_data"].get("expected_registration_id") for c in host.calls[1:]] == ["C", "D", "C", "E"]
    assert result["ok"] is False and result["error"] == "partial_failure"
    held = [(r["role"], r["registration_id"]) for r in result["refused_roles"]]
    assert held == [("R3", "C")]
    assert _local_names(plugin) == {"tool_a"}


@pytest.mark.asyncio
async def test_failed_follow_up_resolves_the_roles_it_removed_the_id_from():
    """The targeted request for C removes it from R1 and fails on R2:
    refused_roles keeps ("R2", "C") but not ("R1", "C")."""
    plugin, _ = await _plugin_with_tool(role=None)
    failed = {
        "ok": False, "removed": True, "affected_roles": ["R1"],
        "failed_roles": [{"role": "R2", "error": "RuntimeError: sync failed"}],
        "refused_roles": [], "error": "partial_failure", "message": "some roles failed",
    }
    host = _ScriptedHost(_held_by(("R1", "C"), ("R2", "C")), failed)
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is False and result["error"] == "partial_failure"
    held = [(r["role"], r["registration_id"]) for r in result["refused_roles"]]
    assert held == [("R2", "C")]
    assert _local_names(plugin) == {"tool_a"}


@pytest.mark.asyncio
async def test_stale_removal_keeps_what_the_first_request_removed():
    """role=None: the first request removed the held registration on R1
    while R2 reported another one. If the follow-up fails, the result still
    says R1 was removed."""
    # A global tool (role=None): main_server visits every role, so
    # refusals and removals can come from several of them.
    plugin, _ = await _plugin_with_tool(role=None)
    host = _ScriptedHost(
        _held_by(("R2", "stale"), removed=True, affected=["R1"]),
        TimeoutError("late"),
    )
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is False and result["error"] == "timeout"
    assert result["removed"] is True and result["affected_roles"] == ["R1"]
    assert result["refused_roles"] == [
        {"role": "R2", "owned_by": "plugin:demo_plugin", "reason": "different registration",
         "registration_id": "stale"},
    ]
    assert _local_names(plugin) == {"tool_a"}
    assert all(c["request_data"]["role"] is None for c in host.calls)


@pytest.mark.asyncio
async def test_stale_removal_keeps_other_sources_refusals():
    # A global tool (role=None): main_server visits every role, so
    # refusals and removals can come from several of them.
    plugin, _ = await _plugin_with_tool(role=None)
    first = _held_by("stale")
    first["refused_roles"].append({"role": "R9", "owned_by": "plugin:other"})
    host = _ScriptedHost(first, dict(_CLEANED))
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is True
    assert result["refused_roles"] == [{"role": "R9", "owned_by": "plugin:other"}]
    assert all(c["request_data"]["role"] is None for c in host.calls)


@pytest.mark.asyncio
async def test_stale_removal_keeps_refusals_reported_by_follow_ups():
    """While the follow-up runs, another source takes over R1: that plain
    refusal only appears in the follow-up's answer and must be reported."""
    plugin, _ = await _plugin_with_tool(role=None)
    follow_up = dict(_CLEANED)
    follow_up["refused_roles"] = [{"role": "R1", "owned_by": "plugin:other"}]
    host = _ScriptedHost(_held_by(("R1", "stale")), follow_up)
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert {"role": "R1", "owned_by": "plugin:other"} in result["refused_roles"]
    assert all(r.get("reason") != "different registration" for r in result["refused_roles"])
    assert all(c["request_data"]["role"] is None for c in host.calls)


def test_concurrent_sync_unregisters_never_raise_and_one_wins():
    """Before this PR the sync unregister was a single dict.pop; the
    check-then-delete that replaced it must stay atomic across threads.
    Deterministic: the first check waits (briefly) for a second thread to
    reach the same check, which only an unprotected check-then-delete
    allows; both would then pass and the second delete raise KeyError."""
    import threading

    plugin, _ = _make_plugin()
    plugin.register_llm_tool(**_register_kwargs("raced"))
    second_checking = threading.Event()
    per_thread = threading.local()
    order = {"n": 0}
    order_lock = threading.Lock()

    class _SlowCheck(dict):
        def get(self, key, default=None):
            # Each unregister reads the name twice: the lookup, then the
            # identity check inside the release. Hold the first thread at
            # its identity check until a second thread reaches its own.
            per_thread.n = getattr(per_thread, "n", 0) + 1
            value = super().get(key, default)
            if key == "raced" and per_thread.n == 2:
                with order_lock:
                    order["n"] += 1
                    first = order["n"] == 1
                if first:
                    # Between this check and the delete that follows it.
                    second_checking.wait(0.5)
                else:
                    second_checking.set()
            return value

    plugin._llm_tools = _SlowCheck(plugin._llm_tools)
    results: list[object] = []

    def worker():
        try:
            results.append(plugin.unregister_llm_tool("raced"))
        except Exception as exc:  # the regression
            results.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5.0)

    assert all(isinstance(r, bool) for r in results), results
    assert sorted(results) == [False, True]


@pytest.mark.asyncio
async def test_cleanup_also_removes_a_stale_registration_it_meets():
    """After a failed register, the cleanup's own id is refused because an
    older registration of this plugin landed late: the cleanup removes it
    by its reported id, and only reports ok when it is gone."""
    plugin, _ = _make_plugin()
    host = _ScriptedHost(_ALL_SYNC_FAILED, _held_by("older"), dict(_CLEANED))
    _install_host(plugin, host)

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False
    assert result["cleanup"]["ok"] is True and result["cleanup"]["removed"] is True
    assert host.calls[2]["request_data"]["expected_registration_id"] == "older"

    plugin2, _ = _make_plugin()
    host2 = _ScriptedHost(_ALL_SYNC_FAILED, _held_by("older"), TimeoutError("late"))
    _install_host(plugin2, host2)
    failed = await plugin2.register_llm_tool_async(**_register_kwargs())
    assert failed["cleanup"]["ok"] is False


@pytest.mark.asyncio
async def test_failed_stale_removal_keeps_the_handler():
    plugin, _ = await _plugin_with_tool()
    host = _ScriptedHost(
        {"ok": True, "removed": False, "affected_roles": [], "failed_roles": [],
         "refused_roles": [{"role": "Lanlan", "owned_by": "plugin:demo_plugin", "reason": "different registration", "registration_id": "stale"}]},
        TimeoutError("late"),
    )
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is False and result["error"] == "timeout"
    assert _local_names(plugin) == {"tool_a"}


@pytest.mark.asyncio
async def test_stale_removal_uses_what_is_left_of_request_timeout(monkeypatch):
    from plugin.sdk.plugin import base

    plugin, _ = await _plugin_with_tool()
    refused = {"ok": True, "removed": False, "affected_roles": [], "failed_roles": [],
               "refused_roles": [{"role": "Lanlan", "owned_by": "plugin:demo_plugin", "reason": "different registration", "registration_id": "stale"}]}
    host = _ScriptedHost(dict(refused), dict(_CLEANED))
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a", request_timeout=2.0)

    assert result["ok"] is True
    assert 0 < host.calls[1]["timeout"] <= 2.0  # not a fresh 15s

    # Budget used up by the first request: no second request, handler kept.
    plugin2, _ = await _plugin_with_tool()
    host2 = _ScriptedHost(dict(refused), dict(_CLEANED))
    _install_host(plugin2, host2)
    real = base.time.monotonic
    calls = {"n": 0}

    class _Clock:
        @staticmethod
        def monotonic():
            calls["n"] += 1
            # lock start, lock acquired, deadline base, then "after the first request"
            # 1.5s of the 2s budget used: 0.5s left is under the send
            # threshold (half the budget), so nothing more is sent.
            return real() + (1.5 if calls["n"] > 3 else 0.0)

    monkeypatch.setattr(base, "time", _Clock)
    late = await plugin2.unregister_llm_tool_async("tool_a", request_timeout=2.0)

    # Nothing more was sent, so the outcome is known: busy, not timeout.
    assert late["ok"] is False and late["error"] == "busy"
    assert len(host2.calls) == 1
    assert _local_names(plugin2) == {"tool_a"}
    assert any(r.get("registration_id") == "stale" for r in late["refused_roles"])


@pytest.mark.asyncio
async def test_cleanup_reports_busy_when_too_little_budget_is_left_for_the_follow_up(monkeypatch):
    """The failed register's cleanup used most of its 15s budget and its
    answer reports another registration: the follow-up is not sent and the
    cleanup reports busy (outcome known, holder kept), not timeout."""
    from plugin.sdk.plugin import base

    real = base.time.monotonic
    offset = {"s": 0.0}

    class _Clock:
        @staticmethod
        def monotonic():
            return real() + offset["s"]

    monkeypatch.setattr(base, "time", _Clock)
    plugin, _ = _make_plugin()
    replies = [_ALL_SYNC_FAILED, _held_by("older")]
    calls: list[dict] = []

    async def host(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            # The cleanup's answer arrives after 14 of its 15 seconds.
            offset["s"] += 14.0
        return replies[len(calls) - 1]

    _install_host(plugin, host)

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False and result["error"] == "rejected"
    assert result["cleanup"]["ok"] is False and result["cleanup"]["error"] == "busy"
    assert len(calls) == 2
    assert any(r.get("registration_id") == "older" for r in result["cleanup"]["refused_roles"])


@pytest.mark.asyncio
@pytest.mark.parametrize("holders", [
    (("R1", ""), ("R2", "C")),   # id-less holder checked before the budget runs out
    (("R1", "C"), ("R2", "")),   # id-less holder still queued when it runs out
], ids=["seen_first", "still_queued"])
async def test_busy_does_not_hide_an_earlier_holder_without_an_id(monkeypatch, holders):
    """A holder reported without an id cannot be removed (partial_failure);
    when the budget runs out before the next follow-up, that error is
    reported instead of busy, wherever the id-less holder is queued."""
    from plugin.sdk.plugin import base

    real = base.time.monotonic
    offset = {"s": 0.0}

    class _Clock:
        @staticmethod
        def monotonic():
            return real() + offset["s"]

    monkeypatch.setattr(base, "time", _Clock)
    plugin, _ = await _plugin_with_tool(role=None)
    calls: list[dict] = []

    async def host(**kwargs):
        calls.append(kwargs)
        # The first answer arrives after 38 of the 40 seconds.
        offset["s"] += 38.0
        return _held_by(*holders)

    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert len(calls) == 1
    assert result["ok"] is False and result["error"] == "partial_failure"
    assert "did not report its id" in result["message"]
    assert _local_names(plugin) == {"tool_a"}


@pytest.mark.asyncio
async def test_cleanup_leaves_a_registration_whose_handler_is_back_locally():
    """While the failed register's cleanup runs, another thread registers
    the same name with the sync call (which does not take the async name
    lock). The registration main_server then reports may be that live one,
    so the cleanup must not remove it."""
    plugin, _ = _make_plugin()
    calls: list[dict] = []
    replies = [_ALL_SYNC_FAILED, _held_by("sync_one"), dict(_CLEANED)]

    async def host(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            plugin.register_llm_tool(**_register_kwargs())
        return replies[len(calls) - 1]

    _install_host(plugin, host)

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False and result["error"] == "rejected"
    assert len(calls) == 2  # register + its own cleanup, no follow-up removal
    assert result["cleanup"]["ok"] is True
    assert any(r.get("registration_id") == "sync_one" for r in result["cleanup"]["refused_roles"])
    assert _local_names(plugin) == {"tool_a"}


@pytest.mark.asyncio
async def test_timed_out_cleanup_does_not_remove_the_retry(monkeypatch):
    """greptile P1: the cleanup after a failed register times out but keeps
    running at main_server; the plugin registers the name again; when the
    old cleanup finally runs it must not delete the new registration."""
    from plugin.sdk.plugin import base

    monkeypatch.setattr(base, "_LLM_TOOL_CLEANUP_TIMEOUT", 0.05)
    plugin, _ = _make_plugin()
    server = _MainServerWithIds()
    server.unregister_delay = 0.2
    _install_host(plugin, server)

    failed = await plugin.register_llm_tool_async(**_register_kwargs())
    assert failed["ok"] is False and failed["cleanup"]["error"] == "timeout"
    retry = await plugin.register_llm_tool_async(**_register_kwargs())
    assert retry["ok"] is True
    await asyncio.gather(*server.background)

    assert server.remote == {"tool_a": plugin._llm_tools["tool_a"].registration_id}
    assert _local_names(plugin) == {"tool_a"}


@pytest.mark.asyncio
async def test_register_async_main_server_unavailable_rolls_back():
    plugin, _ = _make_plugin()
    _install_host(plugin, _FakeHost(reply={
        "ok": False, "affected_roles": [], "failed_roles": [],
        "error": "main_server_unavailable", "message": "ConnectError: refused",
    }))

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False
    assert result["error"] == "main_server_unavailable"
    assert plugin.list_llm_tools() == []
    assert not _entry_registered(plugin, "tool_a")


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [
    TimeoutError("late"),
    "DEADLINE_EXCEEDED",
    "OPERATION_TIMEOUT",
    "TRANSPORT_TIMEOUT",
])
async def test_register_async_host_timeout_rolls_back(exc):
    from plugin.core.rpc import RpcRemoteError

    if isinstance(exc, str):
        exc = RpcRemoteError({"code": exc, "message": "x"})
    plugin, _ = _make_plugin()
    _install_host(plugin, _FakeHost(exc=exc))

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False
    assert result["error"] == "timeout"
    assert plugin.list_llm_tools() == []
    assert not _entry_registered(plugin, "tool_a")


@pytest.mark.asyncio
async def test_register_not_sent_because_the_entry_deadline_passed_is_busy():
    """Nothing reached the host, so the outcome is known: busy, and no
    cleanup is sent for a register that was never sent."""
    from plugin.core.rpc import RpcDeadlineExpiredBeforeSendError

    plugin, _ = _make_plugin()
    host = _ScriptedHost(RpcDeadlineExpiredBeforeSendError("not sent"))
    _install_host(plugin, host)

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False and result["error"] == "busy"
    assert result["cleanup"] is None
    assert len(host.calls) == 1
    assert plugin.list_llm_tools() == []


@pytest.mark.asyncio
async def test_cleanup_not_sent_because_the_entry_deadline_passed_is_busy():
    from plugin.core.rpc import RpcDeadlineExceededError, RpcDeadlineExpiredBeforeSendError

    plugin, _ = _make_plugin()
    host = _ScriptedHost(RpcDeadlineExceededError("sent, no answer"), RpcDeadlineExpiredBeforeSendError("not sent"))
    _install_host(plugin, host)

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False and result["error"] == "timeout"
    assert result["cleanup"]["ok"] is False and result["cleanup"]["error"] == "busy"


@pytest.mark.asyncio
async def test_unregister_not_sent_because_the_entry_deadline_passed_is_busy():
    from plugin.core.rpc import RpcDeadlineExpiredBeforeSendError

    plugin, _ = await _plugin_with_tool()
    _install_host(plugin, _ScriptedHost(RpcDeadlineExpiredBeforeSendError("not sent")))

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is False and result["error"] == "busy"
    assert _local_names(plugin) == {"tool_a"}


@pytest.mark.asyncio
async def test_register_async_host_unavailable_rolls_back():
    from plugin.core.rpc import RpcRemoteError

    plugin, _ = _make_plugin()
    _install_host(plugin, _FakeHost(exc=RpcRemoteError({"code": "BUSY", "message": "busy"})))

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False
    assert result["error"] == "host_unavailable"
    assert "BUSY" in (result["message"] or "")
    assert "busy" in (result["message"] or "")  # str(exc) is carried too
    assert plugin.list_llm_tools() == []


@pytest.mark.asyncio
async def test_register_async_without_host_request_support_rolls_back():
    plugin, _ = _make_plugin()
    plugin._host_ctx = object()  # a host context with no request function

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False
    assert result["error"] == "host_unavailable"
    assert plugin.list_llm_tools() == []


@pytest.mark.asyncio
async def test_register_async_malformed_host_reply_rolls_back():
    plugin, _ = _make_plugin()
    _install_host(plugin, _FakeHost(reply=["not", "a", "dict"]))

    result = await plugin.register_llm_tool_async(**_register_kwargs())

    assert result["ok"] is False
    assert result["error"] == "host_unavailable"
    assert plugin.list_llm_tools() == []


@pytest.mark.asyncio
async def test_register_async_cancelled_rolls_back():
    plugin, _ = _make_plugin()
    _install_host(plugin, _FakeHost(reply={"ok": True}, delay=10.0))

    task = asyncio.create_task(plugin.register_llm_tool_async(**_register_kwargs()))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert [t["name"] for t in plugin.list_llm_tools()] == ["tool_a"]  # claimed while in flight
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert plugin.list_llm_tools() == []
    assert not _entry_registered(plugin, "tool_a")


@pytest.mark.asyncio
async def test_register_async_duplicate_name_raises_like_sync_api():
    from plugin.sdk.shared.models.exceptions import EntryConflictError

    plugin, _ = _make_plugin()
    host = _FakeHost(reply={"ok": True})
    _install_host(plugin, host)
    plugin.register_llm_tool(**_register_kwargs())

    with pytest.raises(EntryConflictError):
        await plugin.register_llm_tool_async(**_register_kwargs())
    assert host.calls == []
    # The existing registration is untouched.
    assert [t["name"] for t in plugin.list_llm_tools()] == ["tool_a"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -1, 300.5, float("inf"), float("nan"), True, "5"])
async def test_register_async_rejects_bad_tool_timeout_before_claiming(bad):
    plugin, _ = _make_plugin()
    host = _FakeHost(reply={"ok": True})
    _install_host(plugin, host)

    with pytest.raises(ValueError):
        await plugin.register_llm_tool_async(**_register_kwargs(timeout=bad))
    assert host.calls == []
    assert plugin.list_llm_tools() == []


def test_sync_register_keeps_accepting_any_positive_tool_timeout():
    """The range check is new-API only; the sync call is unchanged."""
    plugin, _ = _make_plugin()
    assert plugin.register_llm_tool(**_register_kwargs(timeout=301.0)) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -1, float("inf"), float("nan"), True, "5"])
async def test_register_async_rejects_bad_request_timeout_before_claiming(bad):
    plugin, _ = _make_plugin()
    host = _FakeHost(reply={"ok": True})
    _install_host(plugin, host)

    with pytest.raises(ValueError):
        await plugin.register_llm_tool_async(**_register_kwargs(), request_timeout=bad)
    assert host.calls == []
    assert plugin.list_llm_tools() == []


@pytest.mark.asyncio
async def test_register_async_failure_does_not_drop_a_newer_registration():
    """While the request is in flight the plugin unregisters and registers
    the same name again (sync API). The late failure must not remove the
    newer registration."""
    plugin, _ = _make_plugin()
    gate = asyncio.Event()

    async def host(**kwargs):
        await gate.wait()
        return {"ok": False, "error": "rejected"}

    _install_host(plugin, host)
    task = asyncio.create_task(plugin.register_llm_tool_async(**_register_kwargs()))
    await asyncio.sleep(0)
    assert plugin.unregister_llm_tool("tool_a") is True
    plugin.register_llm_tool(**_register_kwargs(description="newer"))
    gate.set()
    result = await task

    assert result["ok"] is False
    assert [t["description"] for t in plugin.list_llm_tools()] == ["newer"]
    assert _entry_registered(plugin, "tool_a")


# ---------------------------------------------------------------------------
# SDK: unregister
# ---------------------------------------------------------------------------


async def _plugin_with_tool(role="Lanlan"):
    plugin, ctx = _make_plugin()
    _install_host(plugin, _FakeHost(reply={"ok": True, "affected_roles": ["Lanlan"], "failed_roles": []}))
    result = await plugin.register_llm_tool_async(**_register_kwargs(role=role))
    assert result["ok"] is True
    _drain(ctx)
    return plugin, ctx


@pytest.mark.asyncio
async def test_unregister_async_success_drops_local():
    plugin, ctx = await _plugin_with_tool()
    host = _FakeHost(reply={
        "ok": True, "removed": True, "affected_roles": ["Lanlan"],
        "failed_roles": [], "refused_roles": [], "error": None, "message": None,
    })
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a", request_timeout=4.0)

    assert result == {
        "ok": True, "removed": True, "affected_roles": ["Lanlan"],
        "failed_roles": [], "refused_roles": [], "error": None, "message": None,
    }
    assert plugin.list_llm_tools() == []
    assert not _entry_registered(plugin, "tool_a")
    (call,) = host.calls
    assert call["request_type"] == "LLM_TOOL_UNREGISTER_REQUEST"
    assert 0 < call["timeout"] <= 4.0
    assert call["request_data"]["name"] == "tool_a"
    assert call["request_data"]["role"] == "Lanlan"
    assert not [m for m in _drain(ctx) if m.get("type") == "LLM_TOOL_UNREGISTER"]


@pytest.mark.asyncio
async def test_unregister_async_partial_failure_keeps_local():
    plugin, _ = await _plugin_with_tool()
    _install_host(plugin, _FakeHost(reply={
        "ok": False, "removed": True, "affected_roles": ["A"],
        "failed_roles": [{"role": "B", "error": "boom"}], "refused_roles": [],
        "error": "partial_failure",
    }))

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is False
    assert result["removed"] is True
    assert result["error"] == "partial_failure"
    assert result["failed_roles"] == [{"role": "B", "error": "boom"}]
    # Role B still has the tool, so its calls must still reach a handler.
    assert [t["name"] for t in plugin.list_llm_tools()] == ["tool_a"]
    assert _entry_registered(plugin, "tool_a")


@pytest.mark.asyncio
async def test_unregister_async_foreign_owner_is_reported_and_drops_local():
    plugin, _ = await _plugin_with_tool()
    _install_host(plugin, _FakeHost(reply={
        "ok": True, "removed": False, "affected_roles": [], "failed_roles": [],
        "refused_roles": [{"role": "Lanlan", "owned_by": "plugin:other"}],
    }))

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is True
    assert result["removed"] is False
    assert result["refused_roles"] == [{"role": "Lanlan", "owned_by": "plugin:other"}]
    assert plugin.list_llm_tools() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reply,exc,error", [
    (None, TimeoutError("late"), "timeout"),
    (None, RuntimeError("Plugin context is closed"), "host_unavailable"),
    ({"ok": False, "error": "main_server_unavailable"}, None, "main_server_unavailable"),
])
async def test_unregister_async_failure_keeps_local(reply, exc, error):
    plugin, _ = await _plugin_with_tool()
    _install_host(plugin, _FakeHost(reply=reply, exc=exc))

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is False
    assert result["error"] == error
    assert [t["name"] for t in plugin.list_llm_tools()] == ["tool_a"]
    assert _entry_registered(plugin, "tool_a")


@pytest.mark.asyncio
async def test_unregister_async_unknown_name_asks_host_for_every_role():
    """Nothing local and nothing of ours on main_server: not_registered,
    but only after the host confirmed it."""
    plugin, _ = _make_plugin()
    host = _FakeHost(reply={
        "ok": True, "removed": False, "affected_roles": [], "failed_roles": [],
        "refused_roles": [{"role": "Lanlan", "owned_by": "plugin:other"}],
    })
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("nope")

    assert result["ok"] is False
    assert result["error"] == "not_registered"
    assert result["removed"] is False
    assert result["refused_roles"] == [{"role": "Lanlan", "owned_by": "plugin:other"}]
    (call,) = host.calls
    assert call["request_type"] == "LLM_TOOL_UNREGISTER_REQUEST"
    assert call["request_data"]["name"] == "nope"
    assert call["request_data"]["role"] is None  # role unknown: all roles


@pytest.mark.asyncio
async def test_unregister_async_removes_remote_leftover_after_register_timeout():
    """A register timed out (local rolled back) but main_server kept the
    tool: unregister_llm_tool_async must still be able to remove it."""
    plugin, _ = _make_plugin()
    _install_host(plugin, _FakeHost(exc=TimeoutError("late")))
    timed_out = await plugin.register_llm_tool_async(**_register_kwargs())
    assert timed_out["error"] == "timeout" and plugin.list_llm_tools() == []

    host = _FakeHost(reply={
        "ok": True, "removed": True, "affected_roles": ["Lanlan"], "failed_roles": [], "refused_roles": [],
    })
    _install_host(plugin, host)
    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is True
    assert result["removed"] is True
    assert result["error"] is None
    assert host.calls[0]["request_data"]["role"] is None


@pytest.mark.asyncio
async def test_unregister_async_unknown_name_failure_is_passed_through():
    plugin, _ = _make_plugin()
    _install_host(plugin, _FakeHost(exc=TimeoutError("late")))

    result = await plugin.unregister_llm_tool_async("nope")

    assert result["ok"] is False
    assert result["error"] == "timeout"


@pytest.mark.asyncio
async def test_unregister_async_rejects_invalid_name_without_contacting_host():
    plugin, _ = _make_plugin()
    host = _FakeHost(reply={"ok": True})
    _install_host(plugin, host)

    with pytest.raises(ValueError):
        await plugin.unregister_llm_tool_async("../other")
    assert host.calls == []


# ---------------------------------------------------------------------------
# SDK: same-name async calls are serialised
# ---------------------------------------------------------------------------


class _StatefulHost:
    """Fake host + main_server that keeps the remote tool set, answers each
    request after a per-call delay (taken in call order) and runs requests
    concurrently, like the real RPC dispatcher does."""

    def __init__(self, delays):
        self.remote: set[str] = set()
        self.delays = list(delays)
        self.order: list[str] = []

    async def __call__(self, *, request_type, request_data, **_):
        name = request_data["name"]
        self.order.append(request_type)
        await asyncio.sleep(self.delays.pop(0) if self.delays else 0.01)
        if request_type == "LLM_TOOL_REGISTER_REQUEST":
            self.remote.add(name)
            return {"ok": True, "affected_roles": ["Lanlan"], "failed_roles": []}
        removed = name in self.remote
        self.remote.discard(name)
        return {"ok": True, "removed": removed, "affected_roles": ["Lanlan"] if removed else [],
                "failed_roles": [], "refused_roles": []}


def _local_names(plugin) -> set[str]:
    return {t["name"] for t in plugin.list_llm_tools()}


@pytest.mark.asyncio
async def test_concurrent_register_and_unregister_end_consistent():
    """gather(register, unregister): register is slow at main_server and
    unregister fast. Without per-name ordering both return ok=True while
    main_server keeps the tool and the plugin has no handler."""
    plugin, _ = _make_plugin()
    host = _StatefulHost(delays=[0.05, 0.0])
    _install_host(plugin, host)

    registered, unregistered = await asyncio.gather(
        plugin.register_llm_tool_async(**_register_kwargs()),
        plugin.unregister_llm_tool_async("tool_a"),
    )

    assert registered["ok"] is True
    assert unregistered["ok"] is True and unregistered["removed"] is True
    assert host.remote == set()
    assert _local_names(plugin) == set()
    assert not _entry_registered(plugin, "tool_a")
    assert plugin._llm_tool_locks == {}


@pytest.mark.asyncio
async def test_late_unregister_does_not_remove_a_newer_registration():
    """Two overlapping unregisters, and the plugin registers the name again
    after the first returned. The delayed second unregister must not
    delete the new registration."""
    plugin, _ = _make_plugin()
    # Call order: initial register, unregister 1, unregister 2 (slow), register 2.
    host = _StatefulHost(delays=[0.0, 0.0, 0.05, 0.0])
    _install_host(plugin, host)
    assert (await plugin.register_llm_tool_async(**_register_kwargs()))["ok"] is True

    first = asyncio.create_task(plugin.unregister_llm_tool_async("tool_a"))
    second = asyncio.create_task(plugin.unregister_llm_tool_async("tool_a"))
    assert (await first)["ok"] is True
    again = await plugin.register_llm_tool_async(**_register_kwargs())
    late = await second

    assert again["ok"] is True
    assert late["error"] == "not_registered"  # ran before the new register
    assert host.remote == {"tool_a"}
    assert _local_names(plugin) == {"tool_a"}
    assert _entry_registered(plugin, "tool_a")
    assert host.order == [
        "LLM_TOOL_REGISTER_REQUEST", "LLM_TOOL_UNREGISTER_REQUEST",
        "LLM_TOOL_UNREGISTER_REQUEST", "LLM_TOOL_REGISTER_REQUEST",
    ]
    assert plugin._llm_tool_locks == {}


@pytest.mark.asyncio
async def test_lock_wait_counts_against_request_timeout():
    """A call stuck behind an earlier same-name call returns ``timeout``
    within its own budget, without sending anything or claiming the name."""
    plugin, _ = _make_plugin()
    gate = asyncio.Event()
    calls: list[str] = []

    async def host(*, request_type, **_):
        calls.append(request_type)
        await gate.wait()
        return {"ok": True, "affected_roles": ["Lanlan"], "failed_roles": []}

    _install_host(plugin, host)
    first = asyncio.create_task(plugin.register_llm_tool_async(**_register_kwargs()))
    await asyncio.sleep(0)

    late = await asyncio.wait_for(
        plugin.unregister_llm_tool_async("tool_a", request_timeout=0.1), timeout=2.0,
    )

    assert late["ok"] is False
    assert late["error"] == "busy"
    assert "nothing was sent" in (late["message"] or "")
    assert calls == ["LLM_TOOL_REGISTER_REQUEST"]
    gate.set()
    assert (await first)["ok"] is True
    assert _local_names(plugin) == {"tool_a"}
    assert plugin._llm_tool_locks == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("request_timeout,elapsed_fraction,sent", [
    (1.0, 0.6, False),   # 0.4s left < half of 1s: busy, nothing sent
    (1.0, 0.4, True),    # 0.6s left: sent
    (40.0, 0.9, False),  # 4s left < 5s: busy
    (40.0, 0.8, True),   # 8s left: sent
])
async def test_too_little_budget_after_the_lock_sends_nothing(monkeypatch, request_timeout, elapsed_fraction, sent):
    from plugin.sdk.plugin import base

    plugin, _ = _make_plugin()
    host = _FakeHost(reply={"ok": True, "affected_roles": ["Lanlan"], "failed_roles": []})
    _install_host(plugin, host)
    ticks = iter([0.0, request_timeout * elapsed_fraction])

    class _Clock:
        @staticmethod
        def monotonic():
            return next(ticks)

    monkeypatch.setattr(base, "time", _Clock)
    result = await plugin.register_llm_tool_async(**_register_kwargs(), request_timeout=request_timeout)

    if sent:
        assert result["ok"] is True and len(host.calls) == 1
    else:
        assert result["error"] == "busy" and result["cleanup"] is None
        assert host.calls == []
        assert plugin.list_llm_tools() == []


@pytest.mark.asyncio
async def test_cancelled_lock_waiter_does_not_block_later_calls():
    plugin, _ = _make_plugin()
    gate = asyncio.Event()

    async def host(*, request_type, **_):
        if request_type == "LLM_TOOL_REGISTER_REQUEST":
            await gate.wait()
            return {"ok": True, "affected_roles": ["Lanlan"], "failed_roles": []}
        return {"ok": True, "removed": True, "affected_roles": ["Lanlan"],
                "failed_roles": [], "refused_roles": []}

    _install_host(plugin, host)
    first = asyncio.create_task(plugin.register_llm_tool_async(**_register_kwargs()))
    await asyncio.sleep(0)
    waiter = asyncio.create_task(plugin.unregister_llm_tool_async("tool_a"))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    gate.set()
    assert (await first)["ok"] is True
    removed = await asyncio.wait_for(plugin.unregister_llm_tool_async("tool_a"), 1.0)
    assert removed["ok"] is True
    assert _local_names(plugin) == set()
    assert plugin._llm_tool_locks == {}


@pytest.mark.asyncio
async def test_cross_loop_lock_skips_a_waiter_cancelled_while_queued():
    from plugin.sdk.plugin.base import _CrossLoopLock

    lock = _CrossLoopLock()
    await lock.acquire(1.0)
    a = asyncio.create_task(lock.acquire(1.0))
    b = asyncio.create_task(lock.acquire(1.0))
    await asyncio.sleep(0)
    a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a
    lock.release()
    await asyncio.wait_for(b, 0.5)  # b, not the cancelled a, got the lock
    lock.release()
    await asyncio.wait_for(lock.acquire(0.5), 0.5)


@pytest.mark.asyncio
async def test_cross_loop_lock_passes_on_ownership_granted_during_cancel():
    from plugin.sdk.plugin.base import _CrossLoopLock

    lock = _CrossLoopLock()
    await lock.acquire(1.0)
    a = asyncio.create_task(lock.acquire(1.0))
    b = asyncio.create_task(lock.acquire(1.0))
    await asyncio.sleep(0)
    lock.release()  # hands the lock to a ...
    a.cancel()      # ... which is cancelled before it runs
    with pytest.raises(asyncio.CancelledError):
        await a
    await asyncio.wait_for(b, 0.5)  # a passed it on


def test_dynamic_entry_readers_iterate_a_snapshot():
    """Another thread may add an entry while collect_entries / list_entries
    walk the dict (async calls run on timer threads too). Simulated
    deterministically: reading one entry adds another."""
    plugin, _ = _make_plugin()
    plugin.register_dynamic_entry(entry_id="a", handler=lambda **_: None)

    class _AddsWhileRead(dict):
        def get(self, key, default=None):
            if key == "enabled" and "b" not in plugin._dynamic_entries:
                plugin._dynamic_entries["b"] = {"meta": None, "handler": None, "enabled": True}
            return super().get(key, default)

    plugin._dynamic_entries["a"] = _AddsWhileRead(plugin._dynamic_entries["a"])
    assert "a" in plugin.collect_entries()
    del plugin._dynamic_entries["b"]
    assert {e["id"] for e in plugin.list_entries(include_disabled=True)} >= {"a"}


def test_list_llm_tools_iterates_a_snapshot():
    """Another thread may register a tool while list_llm_tools walks the
    dict. Simulated deterministically: reading one tool adds another."""
    plugin, _ = _make_plugin()
    plugin.register_llm_tool(**_register_kwargs("a"))
    real = plugin._llm_tools["a"]

    class _AddsWhileRead:
        def __getattr__(self, attr):
            if "b" not in plugin._llm_tools:
                plugin._llm_tools["b"] = real
            return getattr(real, attr)

    plugin._llm_tools["a"] = _AddsWhileRead()
    assert "a" in {t["name"] for t in plugin.list_llm_tools()}


def test_dynamic_entry_insert_rechecks_under_the_lock(monkeypatch):
    """Two threads registering the same id: the second must still get
    EntryConflictError even if both passed the first duplicate check."""
    from plugin.sdk.shared.models.exceptions import EntryConflictError

    from plugin.sdk.plugin import base

    plugin, _ = _make_plugin()
    real_build = base._dynamic_entry_registration

    def build_then_race(*args, **kwargs):
        # Runs after the first duplicate check, just before the insert.
        plugin._dynamic_entries["x"] = {"meta": None, "handler": None, "enabled": False}
        return real_build(*args, **kwargs)

    monkeypatch.setattr(base, "_dynamic_entry_registration", build_then_race)
    with pytest.raises(EntryConflictError):
        plugin.register_dynamic_entry(entry_id="x", handler=lambda **_: None)


def test_same_name_calls_from_two_event_loops_are_serialised():
    """Plugin entries run on the command loop while interval timers run on
    their own threads with their own ``asyncio.run``. Same-name calls from
    both must still run one at a time, and the waiter on the other loop
    must be woken (asyncio.Lock can do neither)."""
    import threading

    import time

    plugin, _ = _make_plugin()
    state = {"active": 0, "max_active": 0, "remote": set()}
    state_lock = threading.Lock()
    first_in_flight = threading.Event()
    second_queued = threading.Event()

    async def host(*, request_type, request_data, **_):
        with state_lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        first_in_flight.set()
        # Hold the first request until the other loop's call is queued on
        # the lock, so the two calls really contend.
        await asyncio.to_thread(second_queued.wait, 5.0)
        with state_lock:
            state["active"] -= 1
            name = request_data["name"]
            if request_type == "LLM_TOOL_REGISTER_REQUEST":
                state["remote"].add(name)
                return {"ok": True, "affected_roles": ["Lanlan"], "failed_roles": []}
            removed = name in state["remote"]
            state["remote"].discard(name)
        return {"ok": True, "removed": removed, "affected_roles": [], "failed_roles": [], "refused_roles": []}

    _install_host(plugin, host)
    results: dict[str, object] = {}

    def command_loop():
        results["register"] = asyncio.run(plugin.register_llm_tool_async(**_register_kwargs()))

    def timer_loop():
        first_in_flight.wait(2.0)
        results["unregister"] = asyncio.run(plugin.unregister_llm_tool_async("tool_a"))

    threads = [threading.Thread(target=command_loop), threading.Thread(target=timer_loop)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        entry = plugin._llm_tool_locks.get("tool_a")
        if entry is not None and entry[0]._waiters:
            break
        time.sleep(0.005)
    assert plugin._llm_tool_locks["tool_a"][0]._waiters, "second call never queued"
    second_queued.set()
    for t in threads:
        t.join(5.0)
        assert not t.is_alive()

    assert results["register"]["ok"] is True
    assert results["unregister"]["ok"] is True and results["unregister"]["removed"] is True
    assert state["max_active"] == 1
    assert state["remote"] == set()
    assert _local_names(plugin) == set()
    assert plugin._llm_tool_locks == {}


@pytest.mark.asyncio
async def test_different_names_are_not_serialised():
    plugin, _ = _make_plugin()
    gate = asyncio.Event()
    seen: list[str] = []

    async def host(*, request_data, **_):
        seen.append(request_data["name"])
        if request_data["name"] == "slow":
            await gate.wait()
        return {"ok": True, "affected_roles": ["Lanlan"], "failed_roles": []}

    _install_host(plugin, host)
    slow = asyncio.create_task(plugin.register_llm_tool_async(**_register_kwargs("slow")))
    await asyncio.sleep(0)
    fast = await asyncio.wait_for(plugin.register_llm_tool_async(**_register_kwargs("fast")), 1.0)
    assert fast["ok"] is True
    gate.set()
    assert (await slow)["ok"] is True
    assert _local_names(plugin) == {"slow", "fast"}


# ---------------------------------------------------------------------------
# Host handlers (fake main_server)
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, body, status_code: int = 200):
        self._body = body
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self.text = str(body)

    def json(self):
        return self._body


class _FakeMainServer:
    def __init__(self, body=None, status_code: int = 200, exc: BaseException | None = None, delay: float = 0.0):
        self.body = body
        self.status_code = status_code
        self.exc = exc
        self.delay = delay
        self.posts: list[tuple[str, dict]] = []

    async def post(self, url, json=None, **_):
        self.posts.append((url, json))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return _FakeResp(self.body, self.status_code)


@pytest.fixture
def registry(monkeypatch):
    from plugin.server.messaging import llm_tool_registry

    llm_tool_registry._plugin_tools.pop(PLUGIN_ID, None)
    llm_tool_registry._stop_generations.pop(PLUGIN_ID, None)
    yield llm_tool_registry
    llm_tool_registry._plugin_tools.pop(PLUGIN_ID, None)
    llm_tool_registry._stop_generations.pop(PLUGIN_ID, None)


def _use_main_server(monkeypatch, registry, server: _FakeMainServer) -> _FakeMainServer:
    monkeypatch.setattr(registry, "_get_http_client", lambda: server)
    return server


def _rpc_ctx(from_plugin: str = PLUGIN_ID):
    import time

    from plugin.core.rpc import RpcContext

    return RpcContext(
        from_plugin=from_plugin, request_id="r1", deadline=time.monotonic() + 5, trace_id="r1", depth=0,
    )


_REGISTER_REQUEST = {
    "type": "LLM_TOOL_REGISTER_REQUEST",
    "plugin_id": "someone_else",  # untrusted; the connection identity wins
    "name": "tool_a",
    "description": "d",
    "parameters": {"type": "object", "properties": {}},
    "timeout_seconds": 12.0,
    "role": "Lanlan",
}


@pytest.mark.asyncio
async def test_host_register_success_tracks_and_uses_connection_identity(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    server = _use_main_server(monkeypatch, registry, _FakeMainServer({
        "ok": True, "registered": "tool_a", "affected_roles": ["Lanlan"], "failed_roles": [],
    }))

    result = await handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST))

    assert result == {
        "ok": True, "affected_roles": ["Lanlan"], "failed_roles": [], "error": None, "message": None,
        "cleanup": None,
    }
    ((url, payload),) = server.posts
    assert url.endswith("/api/tools/register")
    assert payload["source"] == f"plugin:{PLUGIN_ID}"
    assert f"/callback/{PLUGIN_ID}/tool_a" in payload["callback_url"]
    assert registry.has_plugin_tool(PLUGIN_ID, "tool_a")
    assert not registry.has_plugin_tool("someone_else", "tool_a")


@pytest.mark.asyncio
async def test_host_register_partial_role_failure(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    _use_main_server(monkeypatch, registry, _FakeMainServer({
        "ok": True, "affected_roles": ["A"], "failed_roles": [{"role": "B", "error": "boom"}],
    }))

    result = await handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST))

    assert result["ok"] is True
    assert result["failed_roles"] == [{"role": "B", "error": "boom"}]
    assert registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
async def test_host_register_foreign_owner_rejected(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    refusal = {"role": "Lanlan", "error": "tool 'tool_a' already owned by source 'plugin:other'"}
    _use_main_server(monkeypatch, registry, _FakeMainServer({
        "ok": False, "affected_roles": [], "failed_roles": [refusal],
        "error": "no role accepted the registration",
    }))

    result = await handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST))

    assert result["ok"] is False
    assert result["error"] == "rejected"
    assert result["failed_roles"] == [refusal]
    assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
async def test_host_register_main_server_unavailable(monkeypatch, registry):
    import httpx

    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    _use_main_server(monkeypatch, registry, _FakeMainServer(exc=httpx.ConnectError("refused")))

    result = await handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST))

    assert result["ok"] is False
    assert result["error"] == "main_server_unavailable"
    assert result["affected_roles"] == [] and result["failed_roles"] == []
    assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["register", "unregister"])
@pytest.mark.parametrize("exc_name,error", [
    ("ConnectError", "main_server_unavailable"),
    ("ConnectTimeout", "main_server_unavailable"),
    ("PoolTimeout", "main_server_unavailable"),
    ("ProxyError", "main_server_unavailable"),
    ("UnsupportedProtocol", "main_server_unavailable"),
    ("LocalProtocolError", "main_server_unavailable"),
    ("DecodingError", "timeout"),
    ("CloseError", "timeout"),
    ("ReadTimeout", "timeout"),
    ("WriteTimeout", "timeout"),
    ("ReadError", "timeout"),
    ("WriteError", "timeout"),
    ("RemoteProtocolError", "timeout"),
])
async def test_host_separates_unreachable_from_unknown_outcome(monkeypatch, registry, op, exc_name, error):
    """Only failures where the request cannot have reached main_server are
    'unavailable'; a read/write timeout may have been applied."""
    import httpx

    from plugin.server.messaging.handlers.llm_tools import (
        handle_llm_tool_register,
        handle_llm_tool_unregister,
    )

    _use_main_server(monkeypatch, registry, _FakeMainServer(exc=getattr(httpx, exc_name)("x")))
    handler = handle_llm_tool_register if op == "register" else handle_llm_tool_unregister
    request = dict(_REGISTER_REQUEST if op == "register" else _UNREGISTER_REQUEST)

    result = await handler(_rpc_ctx(), request)

    assert result["ok"] is False
    assert result["error"] == error


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["register", "unregister"])
async def test_host_request_path_refuses_dot_only_names(monkeypatch, registry, op):
    from plugin.server.messaging.handlers.llm_tools import (
        handle_llm_tool_register,
        handle_llm_tool_unregister,
    )

    server = _use_main_server(monkeypatch, registry, _FakeMainServer({"ok": True}))
    handler = handle_llm_tool_register if op == "register" else handle_llm_tool_unregister
    request = dict(_REGISTER_REQUEST if op == "register" else _UNREGISTER_REQUEST, name="..")

    result = await handler(_rpc_ctx(), request)

    assert result["error"] == "invalid_request"
    assert server.posts == []


@pytest.mark.parametrize("name", [".", "..", "..."])
def test_host_parsers_refuse_dot_only_names(name):
    """The notification handlers and the request-response handlers share
    these parsers, so both paths apply the same name rule."""
    from plugin.server.messaging.llm_tool_registry import (
        parse_register_request,
        parse_unregister_request,
    )

    assert parse_register_request(dict(_REGISTER_REQUEST, name=name)) is None
    assert parse_unregister_request(dict(_UNREGISTER_REQUEST, name=name)) is None
    assert parse_register_request(dict(_REGISTER_REQUEST, name="a.b"))["name"] == "a.b"
    assert parse_unregister_request(dict(_UNREGISTER_REQUEST, name="a.b"))["name"] == "a.b"


def _notification_handler_owner():
    import types

    return types.SimpleNamespace(plugin_id=PLUGIN_ID, logger=logging.getLogger("test"))


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["register", "unregister"])
@pytest.mark.parametrize("name", [".", ".."])
async def test_host_notification_path_refuses_dot_only_names(monkeypatch, registry, op, name):
    from plugin.core.communication import PluginCommunicationResourceManager as Comm

    server = _use_main_server(monkeypatch, registry, _FakeMainServer({"ok": True}))
    owner = _notification_handler_owner()
    if op == "register":
        msg = dict(_REGISTER_REQUEST, type="LLM_TOOL_REGISTER", plugin_id=PLUGIN_ID, name=name)
        await Comm._handle_llm_tool_register(owner, msg)
    else:
        msg = dict(_UNREGISTER_REQUEST, type="LLM_TOOL_UNREGISTER", plugin_id=PLUGIN_ID, name=name)
        await Comm._handle_llm_tool_unregister(owner, msg)

    assert server.posts == []
    assert not registry.has_plugin_tool(PLUGIN_ID, name)


@pytest.mark.asyncio
async def test_host_notification_path_still_registers_normal_names(monkeypatch, registry):
    from plugin.core.communication import PluginCommunicationResourceManager as Comm

    server = _use_main_server(monkeypatch, registry, _FakeMainServer({
        "ok": True, "affected_roles": ["Lanlan"], "failed_roles": [],
    }))
    msg = dict(_REGISTER_REQUEST, type="LLM_TOOL_REGISTER", plugin_id=PLUGIN_ID, name="a.b")
    await Comm._handle_llm_tool_register(_notification_handler_owner(), msg)

    assert [url.rsplit("/", 1)[-1] for url, _ in server.posts] == ["register"]
    assert registry.has_plugin_tool(PLUGIN_ID, "a.b")


@pytest.mark.asyncio
async def test_host_forwards_registration_ids_and_tracks_them(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import (
        handle_llm_tool_register,
        handle_llm_tool_unregister,
    )

    server = _use_main_server(monkeypatch, registry, _FakeMainServer({
        "ok": True, "registration_id": "reg-2", "affected_roles": ["Lanlan"], "failed_roles": [],
    }))
    await handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST, registration_id="reg-2"))
    assert server.posts[-1][1]["registration_id"] == "reg-2"
    assert registry._plugin_tools[PLUGIN_ID]["tool_a"]["registration_id"] == "reg-2"

    # A late unregister aimed at an older registration: main_server refuses
    # it, and host tracking of the newer one stays.
    server.body = {"ok": True, "removed": False, "affected_roles": [], "failed_roles": [],
                   "refused_roles": [{"role": "Lanlan", "owned_by": f"plugin:{PLUGIN_ID}",
                                      "reason": "different registration"}]}
    result = await handle_llm_tool_unregister(
        _rpc_ctx(), dict(_UNREGISTER_REQUEST, expected_registration_id="reg-1"),
    )
    assert server.posts[-1][1]["expected_registration_id"] == "reg-1"
    assert result["refused_roles"][0]["reason"] == "different registration"
    assert registry.has_plugin_tool(PLUGIN_ID, "tool_a")

    # Without an id (notification path, sync API): same as before.
    server.body = dict(_CLEANED)
    await handle_llm_tool_unregister(_rpc_ctx(), dict(_UNREGISTER_REQUEST))
    assert "expected_registration_id" not in server.posts[-1][1]
    assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
async def test_host_register_main_server_http_error(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    _use_main_server(monkeypatch, registry, _FakeMainServer({"detail": "unknown role: Lanlan"}, status_code=404))

    result = await handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST))

    assert result["ok"] is False
    assert result["error"] == "main_server_error"
    assert "404" in (result["message"] or "")
    assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")


class _RawResp:
    """A 200 answer as httpx would give it: ``json()`` parses ``text``."""

    status_code = 200

    def __init__(self, text: str, content_type: str | None):
        self.text = text
        self.headers = {} if content_type is None else {"content-type": content_type}

    def json(self):
        import json as _json

        return _json.loads(self.text)


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["register", "unregister"])
@pytest.mark.parametrize("text,content_type", [
    ("<html>oops</html>", "text/html"),
    ("<html>oops</html>", None),
    ("not json", "application/json"),
    ('["a list"]', "application/json"),
    ("null", "application/json"),
])
async def test_host_unreadable_answer_is_an_unknown_outcome(monkeypatch, registry, op, text, content_type):
    from plugin.server.messaging.handlers.llm_tools import (
        handle_llm_tool_register,
        handle_llm_tool_unregister,
    )

    class _Server(_FakeMainServer):
        async def post(self, url, json=None, **_):
            self.posts.append((url, json))
            return _RawResp(text, content_type)

    await _seed_tracking(registry)
    _use_main_server(monkeypatch, registry, _Server())
    if op == "register":
        result = await handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST))
    else:
        result = await handle_llm_tool_unregister(_rpc_ctx(), dict(_UNREGISTER_REQUEST))

    assert result["ok"] is False
    # main_server answered but we cannot read what it did: outcome
    # unknown, so the SDK cleans up after a register (like a timeout).
    assert result["error"] == "timeout"
    # Neither path drops existing tracking on an unreadable answer.
    assert registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["register", "unregister"])
@pytest.mark.parametrize("name", ["../other_plugin/x", "a/b", "", None, "x" * 65])
async def test_host_rejects_invalid_tool_names(monkeypatch, registry, op, name):
    from plugin.server.messaging.handlers.llm_tools import (
        handle_llm_tool_register,
        handle_llm_tool_unregister,
    )

    server = _use_main_server(monkeypatch, registry, _FakeMainServer({"ok": True}))
    request = dict(_REGISTER_REQUEST if op == "register" else _UNREGISTER_REQUEST, name=name)
    handler = handle_llm_tool_register if op == "register" else handle_llm_tool_unregister

    result = await handler(_rpc_ctx(), request)

    assert result["ok"] is False
    assert result["error"] == "invalid_request"
    assert server.posts == []


def test_role_records_drop_null_values():
    from plugin.sdk.plugin.llm_tool import build_register_result

    result = build_register_result(
        {"failed_roles": [{"role": "A", "error": None}, "junk"]}, ok=True,
    )
    assert result["failed_roles"] == [{"role": "A"}]


@pytest.mark.asyncio
async def test_host_register_missing_name_is_invalid_request(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    server = _use_main_server(monkeypatch, registry, _FakeMainServer({"ok": True}))

    result = await handle_llm_tool_register(_rpc_ctx(), {"type": "LLM_TOOL_REGISTER_REQUEST"})

    assert result["ok"] is False
    assert result["error"] == "invalid_request"
    assert server.posts == []


async def _seed_tracking(registry):
    async with registry._lock:
        registry._plugin_tools[PLUGIN_ID]["tool_a"] = {"timeout_seconds": 12.0}


_UNREGISTER_REQUEST = {
    "type": "LLM_TOOL_UNREGISTER_REQUEST", "plugin_id": "someone_else", "name": "tool_a", "role": "Lanlan",
}


@pytest.mark.asyncio
async def test_host_unregister_success_sends_expected_source_and_drops_tracking(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_unregister

    await _seed_tracking(registry)
    server = _use_main_server(monkeypatch, registry, _FakeMainServer({
        "ok": True, "removed": True, "name": "tool_a", "affected_roles": ["Lanlan"],
        "failed_roles": [], "refused_roles": [],
    }))

    result = await handle_llm_tool_unregister(_rpc_ctx(), dict(_UNREGISTER_REQUEST))

    assert result == {
        "ok": True, "removed": True, "affected_roles": ["Lanlan"], "failed_roles": [],
        "refused_roles": [], "error": None, "message": None,
    }
    ((url, payload),) = server.posts
    assert url.endswith("/api/tools/unregister")
    assert payload["expected_source"] == f"plugin:{PLUGIN_ID}"
    assert payload["name"] == "tool_a" and payload["role"] == "Lanlan"
    assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
async def test_host_unregister_partial_failure_keeps_tracking(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_unregister

    await _seed_tracking(registry)
    _use_main_server(monkeypatch, registry, _FakeMainServer({
        "ok": False, "removed": True, "affected_roles": ["A"],
        "failed_roles": [{"role": "B", "error": "boom"}], "refused_roles": [],
    }))

    result = await handle_llm_tool_unregister(_rpc_ctx(), dict(_UNREGISTER_REQUEST))

    assert result["ok"] is False
    assert result["error"] == "partial_failure"
    assert result["removed"] is True
    assert result["failed_roles"] == [{"role": "B", "error": "boom"}]
    assert registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
async def test_host_unregister_foreign_owner_reported(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_unregister

    await _seed_tracking(registry)
    _use_main_server(monkeypatch, registry, _FakeMainServer({
        "ok": True, "removed": False, "affected_roles": [], "failed_roles": [],
        "refused_roles": [{"role": "Lanlan", "owned_by": "plugin:other"}],
    }))

    result = await handle_llm_tool_unregister(_rpc_ctx(), dict(_UNREGISTER_REQUEST))

    assert result["ok"] is True
    assert result["removed"] is False
    assert result["refused_roles"] == [{"role": "Lanlan", "owned_by": "plugin:other"}]
    assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
async def test_host_unregister_main_server_unavailable_keeps_tracking(monkeypatch, registry):
    import httpx

    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_unregister

    await _seed_tracking(registry)
    _use_main_server(monkeypatch, registry, _FakeMainServer(exc=httpx.ConnectError("refused")))

    result = await handle_llm_tool_unregister(_rpc_ctx(), dict(_UNREGISTER_REQUEST))

    assert result["ok"] is False
    assert result["error"] == "main_server_unavailable"
    assert registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "x" * 65, 123])
async def test_host_refuses_an_invalid_registration_id_on_register(monkeypatch, registry, bad):
    """Same rule as for expected_registration_id: a dropped id would make
    main_server store one the caller does not know."""
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    server = _use_main_server(monkeypatch, registry, _FakeMainServer({"ok": True}))
    result = await handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST, registration_id=bad))
    assert result["error"] == "invalid_request"
    assert server.posts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "x" * 65, 123])
async def test_host_refuses_an_invalid_expected_registration_id(monkeypatch, registry, bad):
    """Dropping it would turn a targeted unregister into 'remove any'."""
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_unregister

    server = _use_main_server(monkeypatch, registry, _FakeMainServer(dict(_CLEANED)))
    result = await handle_llm_tool_unregister(
        _rpc_ctx(), dict(_UNREGISTER_REQUEST, expected_registration_id=bad),
    )
    assert result["error"] == "invalid_request"
    assert server.posts == []


@pytest.mark.asyncio
async def test_host_keeps_tracking_when_another_registration_holds_the_name(monkeypatch, registry):
    """A late-landed older registration holds the name at main_server while
    host tracking still has the id the SDK targets. The targeted unregister
    is refused there: tracking must stay, or model calls to the name that
    main_server still advertises would be rejected by the host."""
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_unregister

    async with registry._lock:
        registry._plugin_tools[PLUGIN_ID]["tool_a"] = {"timeout_seconds": 12.0, "registration_id": "held"}
    _use_main_server(monkeypatch, registry, _FakeMainServer({
        "ok": True, "removed": False, "affected_roles": [], "failed_roles": [],
        "refused_roles": [{"role": "Lanlan", "owned_by": f"plugin:{PLUGIN_ID}", "reason": "different registration"}],
    }))

    result = await handle_llm_tool_unregister(_rpc_ctx(), dict(_UNREGISTER_REQUEST, expected_registration_id="held"))

    assert result["ok"] is True and result["removed"] is False
    assert registry.has_plugin_tool(PLUGIN_ID, "tool_a")


def test_request_handlers_include_llm_tool_operations():
    from plugin.server.messaging.handlers.llm_tools import (
        handle_llm_tool_register,
        handle_llm_tool_unregister,
    )
    from plugin.server.messaging.handlers.registry import build_request_handlers

    handlers = build_request_handlers()
    assert handlers["LLM_TOOL_REGISTER_REQUEST"] is handle_llm_tool_register
    assert handlers["LLM_TOOL_UNREGISTER_REQUEST"] is handle_llm_tool_unregister


# ---------------------------------------------------------------------------
# End to end: PluginContext -> RpcPeer -> RpcDispatcher -> host handler
# ---------------------------------------------------------------------------


def _wire_rpc(ctx):
    """Connect the plugin context's RPC peer straight to a host dispatcher
    running the real request handlers (no ZMQ)."""
    from plugin.core.rpc import RpcDispatcher, RpcPeer
    from plugin.server.messaging.handlers.registry import build_request_handlers

    dispatcher = RpcDispatcher(build_request_handlers())

    async def reply(response):
        peer.receive(response["request_id"], result=response.get("result"), error=response.get("error"))

    async def send(request, _timeout):
        assert dispatcher.submit(dict(request, from_plugin=ctx.plugin_id), reply)

    async def cancel(request):
        dispatcher.submit({"type": "RPC_CANCEL", "from_plugin": ctx.plugin_id,
                           "request_id": request["request_id"]}, reply)

    peer = RpcPeer(send, cancel=cancel)
    ctx._rpc_peer = peer
    return dispatcher


@pytest.mark.asyncio
async def test_end_to_end_register_then_unregister(monkeypatch, registry):
    plugin, ctx = _make_plugin()
    dispatcher = _wire_rpc(ctx)
    server = _use_main_server(monkeypatch, registry, _FakeMainServer({
        "ok": True, "affected_roles": ["Lanlan"], "failed_roles": [],
    }))
    try:
        registered = await plugin.register_llm_tool_async(**_register_kwargs())
        assert registered["ok"] is True and registered["affected_roles"] == ["Lanlan"]
        assert registry.has_plugin_tool(PLUGIN_ID, "tool_a")

        server.body = {"ok": True, "removed": True, "affected_roles": ["Lanlan"],
                       "failed_roles": [], "refused_roles": []}
        unregistered = await plugin.unregister_llm_tool_async("tool_a")
        assert unregistered["ok"] is True and unregistered["removed"] is True
        assert server.posts[-1][1]["expected_source"] == f"plugin:{PLUGIN_ID}"
        assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")
        assert plugin.list_llm_tools() == []
    finally:
        await dispatcher.close()


@pytest.mark.asyncio
async def test_end_to_end_host_timeout_rolls_back_both_sides(monkeypatch, registry):
    """main_server stores the tool, then hangs syncing it past the budget.
    The SDK times out and rolls back; the stored copy is removed, either by
    the host (its cancelled register withdraws the registration it sent) or
    by the SDK's cleanup, whichever reaches main_server first."""
    plugin, ctx = _make_plugin()
    dispatcher = _wire_rpc(ctx)
    remote: set[str] = set()

    class _StoresThenHangs(_FakeMainServer):
        async def post(self, url, json=None, **_):
            self.posts.append((url, json))
            if url.endswith("/register"):
                remote.add(json["name"])
                await asyncio.sleep(5.0)
                return _FakeResp({"ok": True, "affected_roles": ["Lanlan"], "failed_roles": []})
            assert json["expected_source"] == f"plugin:{PLUGIN_ID}"
            removed = json["name"] in remote
            remote.discard(json["name"])
            return _FakeResp({"ok": True, "removed": removed, "affected_roles": ["Lanlan"] if removed else [],
                              "failed_roles": [], "refused_roles": []})

    _use_main_server(monkeypatch, registry, _StoresThenHangs())
    try:
        result = await asyncio.wait_for(
            plugin.register_llm_tool_async(**_register_kwargs(), request_timeout=0.2), timeout=3.0,
        )
        assert result["ok"] is False
        assert result["error"] == "timeout"
        assert plugin.list_llm_tools() == []
        assert result["cleanup"] is not None
        assert result["cleanup"]["ok"] is True
        assert remote == set()
        # The host gave up too; it never recorded the tool.
        await asyncio.sleep(0.05)
        assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")
    finally:
        await dispatcher.close()


@pytest.mark.asyncio
async def test_end_to_end_cleanup_after_the_entry_deadline_is_busy_not_timeout(monkeypatch, registry):
    """Inside an entry with a deadline: the register is sent and times out
    at the entry's deadline ("timeout", outcome unknown); the cleanup is then
    never sent ("busy", nothing reached the host)."""
    import time

    from plugin.core.rpc import request_scope

    plugin, ctx = _make_plugin()
    dispatcher = _wire_rpc(ctx)

    class _Hangs(_FakeMainServer):
        async def post(self, url, json=None, **_):
            self.posts.append((url, json))
            if url.endswith("/unregister"):
                return _FakeResp({"ok": True, "removed": True, "affected_roles": ["Lanlan"],
                                  "failed_roles": [], "refused_roles": []})
            await asyncio.sleep(5.0)
            return _FakeResp({"ok": True, "affected_roles": ["Lanlan"], "failed_roles": []})

    server = _use_main_server(monkeypatch, registry, _Hangs())
    try:
        with request_scope({"request_id": "entry", "_request_deadline_monotonic": time.monotonic() + 0.2}):
            result = await asyncio.wait_for(plugin.register_llm_tool_async(**_register_kwargs()), timeout=3.0)
        assert result["ok"] is False and result["error"] == "timeout"
        assert result["cleanup"]["ok"] is False and result["cleanup"]["error"] == "busy"
        await asyncio.sleep(0.05)
        # No SDK cleanup was sent; the one unregister is the host withdrawing
        # the registration its cancelled register had sent.
        assert [url.rsplit("/", 1)[-1] for url, _ in server.posts] == ["register", "unregister"]
        assert server.posts[1][1]["expected_registration_id"] == server.posts[0][1]["registration_id"]
        assert plugin.list_llm_tools() == []
    finally:
        await dispatcher.close()


@pytest.mark.asyncio
async def test_end_to_end_main_server_unavailable(monkeypatch, registry):
    import httpx

    plugin, ctx = _make_plugin()
    dispatcher = _wire_rpc(ctx)
    _use_main_server(monkeypatch, registry, _FakeMainServer(exc=httpx.ConnectError("refused")))
    try:
        result = await plugin.register_llm_tool_async(**_register_kwargs())
        assert result["ok"] is False
        assert result["error"] == "main_server_unavailable"
        assert plugin.list_llm_tools() == []
        assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")
    finally:
        await dispatcher.close()


# ---------------------------------------------------------------------------
# Host: same-name register / unregister run one at a time
# ---------------------------------------------------------------------------


class _GatedMainServer:
    """main_server whose unregister answer is held until ``release_unregister``
    is set; register answers at once with a fresh registration id."""

    def __init__(self):
        self.posts: list[tuple[str, dict]] = []
        self.unregister_arrived = asyncio.Event()
        self.release_unregister = asyncio.Event()

    async def post(self, url, json=None, **_):
        self.posts.append((url, json))
        if url.endswith("/unregister"):
            self.unregister_arrived.set()
            await self.release_unregister.wait()
            return _FakeResp({
                "ok": True, "removed": True, "name": json["name"], "affected_roles": ["Lanlan"],
                "failed_roles": [], "refused_roles": [],
            })
        return _FakeResp({
            "ok": True, "registration_id": json.get("registration_id") or "reg-new",
            "affected_roles": ["Lanlan"], "failed_roles": [],
        })


@pytest.mark.asyncio
async def test_host_late_unregister_does_not_drop_a_newer_register(monkeypatch, registry):
    """An old unregister (its SDK caller already gave up) is held at
    main_server while a new register of the same name arrives; when the old
    one finally answers, the new register's tracking must survive."""
    from plugin.server.messaging.handlers.llm_tools import (
        handle_llm_tool_register,
        handle_llm_tool_unregister,
    )

    await _seed_tracking(registry)
    server = _use_main_server(monkeypatch, registry, _GatedMainServer())

    old_unregister = asyncio.create_task(handle_llm_tool_unregister(
        _rpc_ctx(), dict(_UNREGISTER_REQUEST, expected_registration_id="reg-old"),
    ))
    await server.unregister_arrived.wait()
    new_register = asyncio.create_task(handle_llm_tool_register(
        _rpc_ctx(), dict(_REGISTER_REQUEST, registration_id="reg-new"),
    ))
    # Let the register run as far as it can while the unregister is held.
    for _ in range(20):
        await asyncio.sleep(0)
    server.release_unregister.set()

    assert (await old_unregister)["ok"] is True
    assert (await new_register)["ok"] is True
    assert [url.rsplit("/", 1)[-1] for url, _ in server.posts] == ["unregister", "register"]
    assert registry.has_plugin_tool(PLUGIN_ID, "tool_a")
    assert registry._plugin_tools[PLUGIN_ID]["tool_a"]["registration_id"] == "reg-new"
    assert (PLUGIN_ID, "tool_a") not in registry._name_locks


@pytest.mark.asyncio
async def test_host_name_lock_is_per_name_and_does_not_block_plugin_stop(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import (
        handle_llm_tool_register,
        handle_llm_tool_unregister,
    )

    await _seed_tracking(registry)
    server = _use_main_server(monkeypatch, registry, _GatedMainServer())

    held = asyncio.create_task(handle_llm_tool_unregister(_rpc_ctx(), dict(_UNREGISTER_REQUEST)))
    await server.unregister_arrived.wait()
    try:
        # Another name of the same plugin is not held up.
        other = await asyncio.wait_for(
            handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST, name="tool_b")), timeout=2.0,
        )
        assert other["ok"] is True
        # Plugin-stop cleanup does not wait for the held name either.
        cleared = await asyncio.wait_for(registry.clear_plugin_tools(PLUGIN_ID), timeout=2.0)
        assert isinstance(cleared, dict)
        assert registry.get_plugin_tool_names(PLUGIN_ID) == []
    finally:
        server.release_unregister.set()
        await held
    assert registry._name_locks == {}


@pytest.mark.asyncio
async def test_host_name_lock_is_released_when_the_call_is_cancelled(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import (
        handle_llm_tool_register,
        handle_llm_tool_unregister,
    )

    await _seed_tracking(registry)
    server = _use_main_server(monkeypatch, registry, _GatedMainServer())

    held = asyncio.create_task(handle_llm_tool_unregister(_rpc_ctx(), dict(_UNREGISTER_REQUEST)))
    await server.unregister_arrived.wait()
    held.cancel()
    # Lets the retry the cancelled unregister sends be answered.
    server.release_unregister.set()
    with pytest.raises(asyncio.CancelledError):
        await held
    assert registry._name_locks == {}

    result = await asyncio.wait_for(
        handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST)), timeout=2.0,
    )
    assert result["ok"] is True
    assert registry.has_plugin_tool(PLUGIN_ID, "tool_a")
    assert registry._name_locks == {}


# ---------------------------------------------------------------------------
# Host: registers / unregisters interrupted by plugin stop or cancellation
# ---------------------------------------------------------------------------


async def _until_name_has_waiters(registry, count: int, name: str = "tool_a") -> None:
    """Yield until ``count`` calls hold or wait for the name lock."""
    for _ in range(1000):
        entry = registry._name_locks.get((PLUGIN_ID, name))
        if entry is not None and entry[1] >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"name lock never reached {count} holders/waiters")


class _HeldMainServer:
    """main_server that holds the first request of each kind listed in
    ``hold`` until ``release`` is set. Register echoes the registration id
    it was sent; unregister removes (or raises ``unregister_exc``)."""

    def __init__(self, hold=("register",)):
        self.posts: list[tuple[str, dict]] = []
        self.hold = set(hold)
        self.arrived = {op: asyncio.Event() for op in ("register", "unregister", "clear")}
        self.release = asyncio.Event()
        self.unregister_exc: BaseException | None = None

    def ops(self) -> list[str]:
        return [op for op, _ in self.posts]

    async def post(self, url, json=None, **_):
        op = url.rsplit("/", 1)[-1]
        self.posts.append((op, json))
        self.arrived[op].set()
        if op in self.hold:
            self.hold.discard(op)
            await self.release.wait()
        if op == "register":
            return _FakeResp({"ok": True, "registration_id": json["registration_id"],
                              "affected_roles": ["Lanlan"], "failed_roles": []})
        if op == "unregister":
            if self.unregister_exc is not None:
                raise self.unregister_exc
            return _FakeResp(dict(_CLEANED))
        return _FakeResp({"ok": True, "removed": 1})


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["request", "notification"])
async def test_register_in_flight_when_the_plugin_stops_is_withdrawn(monkeypatch, registry, path):
    """Item 6 of #3994: the plugin is stopped (its tools cleared) while a
    register is at main_server. When the register's answer arrives, the
    stored registration is removed again by its id and nothing is tracked."""
    from plugin.core.communication import PluginCommunicationResourceManager as Comm
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    server = _use_main_server(monkeypatch, registry, _HeldMainServer())
    if path == "request":
        call = handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST))
    else:  # the @llm_tool / sync path: no registration id from the SDK
        call = Comm._handle_llm_tool_register(
            _notification_handler_owner(), dict(_REGISTER_REQUEST, type="LLM_TOOL_REGISTER", plugin_id=PLUGIN_ID),
        )
    in_flight = asyncio.create_task(call)
    await asyncio.wait_for(server.arrived["register"].wait(), timeout=2.0)

    await registry.clear_plugin_tools(PLUGIN_ID)
    server.release.set()
    result = await asyncio.wait_for(in_flight, timeout=2.0)

    if path == "request":
        assert result["ok"] is False and result["error"] == "plugin_stopped"
        # Withdrawn: the register's roles are not reported as affected.
        assert result["affected_roles"] == [] and "was withdrawn" in result["message"]
    assert server.ops() == ["register", "clear", "unregister"]
    sent_id = server.posts[0][1]["registration_id"]
    assert sent_id  # generated by the host when the SDK sent none
    assert server.posts[2][1]["expected_registration_id"] == sent_id
    assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")
    assert registry._name_locks == {}


@pytest.mark.asyncio
async def test_register_after_the_stop_whose_withdrawal_fails_says_so(monkeypatch, registry):
    import httpx
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    server = _use_main_server(monkeypatch, registry, _HeldMainServer())
    server.unregister_exc = httpx.ConnectError("refused")
    in_flight = asyncio.create_task(handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST)))
    await asyncio.wait_for(server.arrived["register"].wait(), timeout=2.0)
    await registry.clear_plugin_tools(PLUGIN_ID)
    server.release.set()
    result = await asyncio.wait_for(in_flight, timeout=2.0)

    assert result["ok"] is False and result["error"] == "plugin_stopped"
    assert "could not be confirmed" in result["message"]
    # Not withdrawn: the roles that may still hold the tool are reported.
    assert result["affected_roles"] == ["Lanlan"]
    assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
async def test_register_waiting_for_its_name_when_the_plugin_stops_is_not_sent(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import (
        handle_llm_tool_register,
        handle_llm_tool_unregister,
    )

    await _seed_tracking(registry)
    server = _use_main_server(monkeypatch, registry, _HeldMainServer(hold=("unregister",)))
    held = asyncio.create_task(handle_llm_tool_unregister(_rpc_ctx(), dict(_UNREGISTER_REQUEST)))
    await asyncio.wait_for(server.arrived["unregister"].wait(), timeout=2.0)
    queued = asyncio.create_task(handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST)))
    await _until_name_has_waiters(registry, 2)  # the register waits for the name lock

    await registry.clear_plugin_tools(PLUGIN_ID)
    server.release.set()
    await held
    result = await asyncio.wait_for(queued, timeout=2.0)

    assert result["ok"] is False and result["error"] == "plugin_stopped"
    assert server.ops() == ["unregister", "clear"]
    assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
async def test_startup_llm_tool_registers_are_not_taken_for_a_stopped_plugin(monkeypatch, registry):
    """A restart: the previous stop cleared the plugin's tools, then the new
    process registers its ``@llm_tool`` tools during startup (notification
    path, before the host counts the plugin as running). Those registers
    started after the stop, so they are kept, even while the host does not
    yet list the plugin as running."""
    from plugin.core.communication import PluginCommunicationResourceManager as Comm
    from plugin.server.application.plugins.lifecycle_service import plugin_is_running_sync

    server = _use_main_server(monkeypatch, registry, _HeldMainServer())
    await registry.clear_plugin_tools(PLUGIN_ID)  # the stop before the restart
    assert not plugin_is_running_sync(PLUGIN_ID)

    starting = asyncio.create_task(Comm._handle_llm_tool_register(
        _notification_handler_owner(), dict(_REGISTER_REQUEST, type="LLM_TOOL_REGISTER", plugin_id=PLUGIN_ID),
    ))
    await asyncio.wait_for(server.arrived["register"].wait(), timeout=2.0)
    server.release.set()
    await asyncio.wait_for(starting, timeout=2.0)

    assert server.ops() == ["clear", "register"]
    assert registry.has_plugin_tool(PLUGIN_ID, "tool_a")
    assert registry._plugin_tools[PLUGIN_ID]["tool_a"]["registration_id"] == server.posts[1][1]["registration_id"]


@pytest.mark.asyncio
async def test_register_cancelled_after_it_was_sent_is_withdrawn(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    server = _use_main_server(monkeypatch, registry, _HeldMainServer())
    task = asyncio.create_task(handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST, registration_id="reg-1")))
    await asyncio.wait_for(server.arrived["register"].wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)

    assert server.ops() == ["register", "unregister"]
    assert server.posts[1][1]["expected_registration_id"] == "reg-1"
    assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")
    assert registry._name_locks == {}


@pytest.mark.asyncio
async def test_register_cancelled_and_not_withdrawn_stays_tracked(monkeypatch, registry):
    """The withdrawal fails too: main_server may hold the tool, so the host
    keeps it tracked (model calls stay routable; unregister / stop clean it)."""
    import httpx

    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    server = _use_main_server(monkeypatch, registry, _HeldMainServer())
    server.unregister_exc = httpx.ReadTimeout("no answer")
    task = asyncio.create_task(handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST, registration_id="reg-1")))
    await asyncio.wait_for(server.arrived["register"].wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)

    assert server.ops() == ["register", "unregister"]
    assert registry._plugin_tools[PLUGIN_ID]["tool_a"] == {"timeout_seconds": 12.0, "registration_id": "reg-1"}


@pytest.mark.asyncio
async def test_unregister_cancelled_after_it_was_sent_is_retried(monkeypatch, registry):
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_unregister

    await _seed_tracking(registry)
    server = _use_main_server(monkeypatch, registry, _HeldMainServer(hold=("unregister",)))
    task = asyncio.create_task(handle_llm_tool_unregister(
        _rpc_ctx(), dict(_UNREGISTER_REQUEST, expected_registration_id="reg-1"),
    ))
    await asyncio.wait_for(server.arrived["unregister"].wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)

    assert server.ops() == ["unregister", "unregister"]
    assert server.posts[1][1]["expected_registration_id"] == "reg-1"
    assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")
    assert registry._name_locks == {}


@pytest.mark.asyncio
async def test_unregister_cancelled_and_retry_failed_keeps_tracking(monkeypatch, registry):
    import httpx

    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_unregister

    await _seed_tracking(registry)
    server = _use_main_server(monkeypatch, registry, _HeldMainServer(hold=("unregister",)))
    task = asyncio.create_task(handle_llm_tool_unregister(_rpc_ctx(), dict(_UNREGISTER_REQUEST)))
    await asyncio.wait_for(server.arrived["unregister"].wait(), timeout=2.0)
    server.unregister_exc = httpx.ReadTimeout("no answer")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)

    assert server.ops() == ["unregister", "unregister"]
    assert registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
async def test_reconcile_finishes_under_the_name_lock_despite_a_second_cancel(monkeypatch, registry):
    """The withdrawal runs to its end even if the caller is cancelled again,
    and a later register of the same name waits for it (per-name lock)."""
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    server = _use_main_server(monkeypatch, registry, _HeldMainServer(hold=("register", "unregister")))
    task = asyncio.create_task(handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST, registration_id="reg-1")))
    await asyncio.wait_for(server.arrived["register"].wait(), timeout=2.0)
    task.cancel()
    await asyncio.wait_for(server.arrived["unregister"].wait(), timeout=2.0)  # the withdrawal is held at main_server
    task.cancel()
    later = asyncio.create_task(handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST, registration_id="reg-2")))
    await _until_name_has_waiters(registry, 2)
    for _ in range(10):  # the later register stays queued while the withdrawal runs
        await asyncio.sleep(0)
    assert server.ops() == ["register", "unregister"]  # the later register waits for the name
    assert not task.done()

    server.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)
    assert (await asyncio.wait_for(later, timeout=2.0))["ok"] is True
    assert server.ops() == ["register", "unregister", "register"]
    assert registry._plugin_tools[PLUGIN_ID]["tool_a"]["registration_id"] == "reg-2"


@pytest.mark.asyncio
async def test_failed_withdrawal_keeps_an_earlier_registration_tracked(monkeypatch, registry):
    import httpx

    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    async with registry._lock:
        registry._plugin_tools[PLUGIN_ID]["tool_a"] = {"timeout_seconds": 5.0, "registration_id": "reg-0"}
    server = _use_main_server(monkeypatch, registry, _HeldMainServer())
    server.unregister_exc = httpx.ReadTimeout("no answer")
    task = asyncio.create_task(handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST, registration_id="reg-1")))
    await asyncio.wait_for(server.arrived["register"].wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)

    assert registry._plugin_tools[PLUGIN_ID]["tool_a"] == {"timeout_seconds": 5.0, "registration_id": "reg-0"}


@pytest.mark.asyncio
async def test_withdrawal_keeps_tracking_of_an_earlier_registration_of_the_name(monkeypatch, registry):
    """Tracking is per name, not per role: withdrawing a cancelled register
    (role B) must not drop the entry of the registration main_server still
    holds for role A."""
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    async with registry._lock:
        registry._plugin_tools[PLUGIN_ID]["tool_a"] = {"timeout_seconds": 5.0, "registration_id": "reg-0"}
    server = _use_main_server(monkeypatch, registry, _HeldMainServer())
    task = asyncio.create_task(handle_llm_tool_register(
        _rpc_ctx(), dict(_REGISTER_REQUEST, role="RoleB", registration_id="reg-1"),
    ))
    await asyncio.wait_for(server.arrived["register"].wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)

    assert server.ops() == ["register", "unregister"]  # the withdrawal succeeded
    assert server.posts[1][1]["expected_registration_id"] == "reg-1"
    assert registry._plugin_tools[PLUGIN_ID]["tool_a"] == {"timeout_seconds": 5.0, "registration_id": "reg-0"}


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["rejected", "server_error"])
async def test_failed_register_after_the_plugin_stops_is_withdrawn(monkeypatch, registry, answer):
    """main_server records the tool before syncing roles, so ``ok: false``
    (every role's sync failed) may still leave it stored; any other failure
    after the stop is withdrawn too (harmless when nothing was stored), and
    reported as ``plugin_stopped``."""
    from plugin.server.messaging.handlers.llm_tools import handle_llm_tool_register

    class _FailsAfterStoring(_HeldMainServer):
        async def post(self, url, json=None, **kw):
            if url.endswith("/register"):
                self.posts.append(("register", json))
                self.arrived["register"].set()
                await self.release.wait()
                if answer == "server_error":
                    return _FakeResp({"detail": "boom"}, status_code=500)
                return _FakeResp({"ok": False, "registration_id": json["registration_id"], "affected_roles": [],
                                  "failed_roles": [{"role": "Lanlan", "error": "sync failed"}]})
            return await super().post(url, json=json, **kw)

    server = _use_main_server(monkeypatch, registry, _FailsAfterStoring())
    task = asyncio.create_task(handle_llm_tool_register(_rpc_ctx(), dict(_REGISTER_REQUEST, registration_id="reg-1")))
    await asyncio.wait_for(server.arrived["register"].wait(), timeout=2.0)
    await registry.clear_plugin_tools(PLUGIN_ID)
    server.release.set()
    result = await asyncio.wait_for(task, timeout=2.0)

    assert result["ok"] is False and result["error"] == "plugin_stopped"
    assert server.ops() == ["register", "clear", "unregister"]
    assert server.posts[2][1]["expected_registration_id"] == "reg-1"
    assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")


@pytest.mark.asyncio
async def test_notification_register_failing_after_the_stop_logs_no_traceback(monkeypatch, registry):
    from plugin.core.communication import PluginCommunicationResourceManager as Comm

    class _Rejects(_HeldMainServer):
        async def post(self, url, json=None, **kw):
            if url.endswith("/register"):
                self.posts.append(("register", json))
                self.arrived["register"].set()
                await self.release.wait()
                return _FakeResp({"ok": False, "affected_roles": [], "failed_roles": []})
            return await super().post(url, json=json, **kw)

    server = _use_main_server(monkeypatch, registry, _Rejects())
    owner = _notification_handler_owner()
    calls: list[str] = []
    owner.logger = types.SimpleNamespace(
        info=lambda *a, **k: calls.append("info") or logged.append(a),
        warning=lambda *a, **k: calls.append("warning"),
        exception=lambda *a, **k: calls.append("exception"),
    )
    logged: list[tuple] = []
    task = asyncio.create_task(Comm._handle_llm_tool_register(
        owner, dict(_REGISTER_REQUEST, type="LLM_TOOL_REGISTER", plugin_id=PLUGIN_ID),
    ))
    await asyncio.wait_for(server.arrived["register"].wait(), timeout=2.0)
    await registry.clear_plugin_tools(PLUGIN_ID)
    server.release.set()
    await asyncio.wait_for(task, timeout=2.0)

    assert calls == ["info"]
    assert any("was withdrawn" in str(arg) for arg in logged[0])  # the outcome is logged
    assert server.ops() == ["register", "clear", "unregister"]
