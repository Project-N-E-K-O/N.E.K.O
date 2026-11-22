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


def test_sync_register_keeps_accepting_dot_only_names():
    """Unchanged existing behaviour of the sync API."""
    plugin, _ = _make_plugin()
    assert plugin.register_llm_tool(**_register_kwargs("..")) is True


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
    plugin, _ = await _plugin_with_tool()
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


@pytest.mark.asyncio
async def test_stale_removal_keeps_what_the_first_request_removed():
    """role=None: the first request removed the held registration on R1
    while R2 reported another one. If the follow-up fails, the result still
    says R1 was removed."""
    plugin, _ = await _plugin_with_tool()
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


@pytest.mark.asyncio
async def test_stale_removal_keeps_other_sources_refusals():
    plugin, _ = await _plugin_with_tool()
    first = _held_by("stale")
    first["refused_roles"].append({"role": "R9", "owned_by": "plugin:other"})
    host = _ScriptedHost(first, dict(_CLEANED))
    _install_host(plugin, host)

    result = await plugin.unregister_llm_tool_async("tool_a")

    assert result["ok"] is True
    assert result["refused_roles"] == [{"role": "R9", "owned_by": "plugin:other"}]


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

    assert late["ok"] is False and late["error"] == "timeout"
    assert len(host2.calls) == 1
    assert _local_names(plugin2) == {"tool_a"}


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


async def _plugin_with_tool():
    plugin, ctx = _make_plugin()
    _install_host(plugin, _FakeHost(reply={"ok": True, "affected_roles": ["Lanlan"], "failed_roles": []}))
    result = await plugin.register_llm_tool_async(**_register_kwargs())
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
    yield llm_tool_registry
    llm_tool_registry._plugin_tools.pop(PLUGIN_ID, None)


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
    The SDK times out, rolls back, and its cleanup removes the stored copy."""
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
        assert result["cleanup"]["ok"] is True and result["cleanup"]["removed"] is True
        assert remote == set()
        # The host gave up too; it never recorded the tool.
        await asyncio.sleep(0.05)
        assert not registry.has_plugin_tool(PLUGIN_ID, "tool_a")
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
