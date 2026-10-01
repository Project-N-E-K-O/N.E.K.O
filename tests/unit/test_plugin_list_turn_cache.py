# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The analyze turn reuses the cached plugin list instead of re-fetching it
every turn; it re-fetches only when the change token moves, the TTL expires,
or the cache is empty."""
from __future__ import annotations

import importlib

import pytest

import brain.task_executor as te

PLUGINS = [{"id": "demo", "description": "demo plugin"}]


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Provider:
    def __init__(self, result=None, exc: Exception | None = None) -> None:
        self.calls = 0
        self.result = PLUGINS if result is None else result
        self.exc = exc

    async def __call__(self, force_refresh: bool):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return list(self.result)


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(te, "_monotonic", c)
    return c


def _executor(monkeypatch, provider, token_fn=None):
    ex = object.__new__(te.DirectTaskExecutor)
    ex.plugin_list = []
    ex._external_plugin_provider = provider
    ex._plugin_list_change_token = token_fn
    ex._plugin_list_fetched_at = None
    ex._plugin_list_fetched_token = None
    monkeypatch.setattr(ex, "_schedule_short_desc_prewarm", lambda plugins: None, raising=False)
    return ex


async def _turns(ex, n: int) -> None:
    for _ in range(n):
        await ex.plugin_list_provider(force_refresh=False)


@pytest.mark.asyncio
async def test_consecutive_turns_fetch_once(monkeypatch, clock):
    provider = _Provider()
    ex = _executor(monkeypatch, provider, token_fn=lambda: 7)
    await _turns(ex, 10)
    assert provider.calls == 1
    assert ex.plugin_list == PLUGINS


@pytest.mark.asyncio
async def test_legacy_force_refresh_fetches_every_turn(monkeypatch, clock):
    # Baseline for comparison: the old per-turn force_refresh=True behaviour.
    provider = _Provider()
    ex = _executor(monkeypatch, provider, token_fn=lambda: 7)
    for _ in range(10):
        await ex.plugin_list_provider(force_refresh=True)
    assert provider.calls == 10


@pytest.mark.asyncio
async def test_change_token_triggers_refetch(monkeypatch, clock):
    rev = {"v": 1}
    provider = _Provider()
    ex = _executor(monkeypatch, provider, token_fn=lambda: rev["v"])
    await _turns(ex, 3)
    assert provider.calls == 1
    rev["v"] = 2  # e.g. a plugin was started / installed
    await _turns(ex, 3)
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_change_during_fetch_is_seen_next_turn(monkeypatch, clock):
    rev = {"v": 1}

    class _BumpingProvider(_Provider):
        async def __call__(self, force_refresh: bool):
            rev["v"] += 1  # lifecycle event lands while the fetch is in flight
            return await super().__call__(force_refresh)

    provider = _BumpingProvider()
    ex = _executor(monkeypatch, provider, token_fn=lambda: rev["v"])
    await _turns(ex, 1)
    await _turns(ex, 1)
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_ttl_expiry_triggers_refetch(monkeypatch, clock):
    provider = _Provider()
    ex = _executor(monkeypatch, provider)  # no change signal: TTL only
    await _turns(ex, 3)
    assert provider.calls == 1
    clock.now += te._PLUGIN_LIST_CACHE_TTL_SECONDS - 0.001
    await _turns(ex, 1)
    assert provider.calls == 1
    clock.now += 0.001
    await _turns(ex, 1)
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_empty_cache_always_fetches(monkeypatch, clock):
    provider = _Provider(result=[])
    ex = _executor(monkeypatch, provider, token_fn=lambda: 1)
    await _turns(ex, 3)
    assert provider.calls == 3
    provider.result = PLUGINS  # plugin comes up
    await _turns(ex, 2)
    assert provider.calls == 4
    assert ex.plugin_list == PLUGINS


@pytest.mark.asyncio
async def test_unreadable_change_token_forces_refetch(monkeypatch, clock):
    def broken_token():
        raise RuntimeError("state unavailable")

    provider = _Provider()
    ex = _executor(monkeypatch, provider, token_fn=broken_token)
    await _turns(ex, 3)
    assert provider.calls == 3


@pytest.mark.asyncio
async def test_provider_failure_keeps_cache_and_retries(monkeypatch, clock):
    provider = _Provider()
    ex = _executor(monkeypatch, provider, token_fn=lambda: 1)
    await _turns(ex, 1)

    class _NoNetwork:
        def __init__(self, *a, **k):
            raise RuntimeError("no network in unit tests")

    monkeypatch.setattr(te.httpx, "AsyncClient", _NoNetwork)
    provider.exc = RuntimeError("plugin server down")
    clock.now += te._PLUGIN_LIST_CACHE_TTL_SECONDS
    await _turns(ex, 2)
    # TTL expired → cache is stale, so a failed refresh must not reuse it.
    assert ex.plugin_list == []
    assert provider.calls == 3  # keeps retrying

    provider.exc = None
    await _turns(ex, 3)
    assert provider.calls == 4
    assert ex.plugin_list == PLUGINS


@pytest.mark.asyncio
async def test_failed_refresh_within_ttl_and_same_token_keeps_cache(monkeypatch, clock):
    provider = _Provider()
    ex = _executor(monkeypatch, provider, token_fn=lambda: 1)
    await _turns(ex, 1)

    async def failing(force_refresh):
        return None  # transient /plugins timeout

    ex._external_plugin_provider = failing
    result = await ex.plugin_list_provider(force_refresh=True)
    assert result == PLUGINS  # still fresh: transient failure keeps it


@pytest.mark.asyncio
async def test_failed_refresh_after_stop_does_not_reuse_stale_list(monkeypatch, clock):
    rev = {"v": 1}
    provider = _Provider()
    ex = _executor(monkeypatch, provider, token_fn=lambda: rev["v"])
    await _turns(ex, 1)
    assert ex.plugin_list == PLUGINS

    async def failing(force_refresh):
        return None  # /plugins timed out

    ex._external_plugin_provider = failing
    rev["v"] = 2  # plugin stopped normally → lifecycle revision moved
    for _ in range(3):
        assert await ex.plugin_list_provider(force_refresh=False) == []
    assert ex.plugin_list == []


@pytest.mark.asyncio
async def test_crashed_plugin_changes_token_and_triggers_refetch(monkeypatch, clock):
    # A crash emits no lifecycle event; the alive-host part of the token moves.
    alive = {"demo"}
    provider = _Provider()
    ex = _executor(
        monkeypatch, provider, token_fn=lambda: (1, tuple(sorted(alive)))
    )
    await _turns(ex, 2)
    assert provider.calls == 1
    alive.clear()  # plugin process died on its own
    provider.result = []
    await _turns(ex, 1)
    assert provider.calls == 2
    assert ex.plugin_list == []


def test_agent_change_token_tracks_host_liveness(monkeypatch):
    from app.agent_server import api_runtime as srv
    from plugin.core.state import state

    class _Host:
        def __init__(self, alive: bool) -> None:
            self.alive = alive

        def is_alive(self) -> bool:
            return self.alive

    host = _Host(True)
    monkeypatch.setattr(
        state, "get_plugin_hosts_snapshot_nowait", lambda: {"demo": host}
    )
    before = srv._plugin_list_change_token()
    assert before[1] == ("demo",)
    host.alive = False  # crash: no lifecycle event, revision unchanged
    after = srv._plugin_list_change_token()
    assert after[0] == before[0]
    assert after != before


def test_lifecycle_event_bumps_change_token():
    # agent_server wires the change token to the lifecycle bus revision; every
    # start/stop/reload/delete/load emits through emit_lifecycle_event.
    from plugin.core.state import state
    from plugin.server.messaging.lifecycle_events import emit_lifecycle_event

    before = state.get_bus_rev("lifecycle")
    emit_lifecycle_event({"type": "plugin_started", "plugin_id": "demo"})
    assert state.get_bus_rev("lifecycle") > before


def _failing_provider():
    async def failing(force_refresh):
        return None  # transient /plugins timeout

    return failing


@pytest.mark.asyncio
async def test_failed_refresh_after_ttl_keeps_alive_plugins(monkeypatch, clock):
    # TTL expiry alone does not mean the plugin stopped: a transient failure
    # keeps plugins whose host is still alive, and the next turn retries.
    provider = _Provider()
    ex = _executor(monkeypatch, provider, token_fn=lambda: (1, ("demo",)))
    await _turns(ex, 1)
    ex._external_plugin_provider = _failing_provider()
    clock.now += te._PLUGIN_LIST_CACHE_TTL_SECONDS + 1
    assert await ex.plugin_list_provider(force_refresh=False) == PLUGINS

    ex._external_plugin_provider = provider
    await _turns(ex, 1)
    assert provider.calls == 2  # pruned list is not treated as fresh


@pytest.mark.asyncio
async def test_failed_refresh_after_unrelated_event_keeps_alive_plugins(monkeypatch, clock):
    token = {"v": (1, ("demo",))}
    ex = _executor(monkeypatch, _Provider(), token_fn=lambda: token["v"])
    await _turns(ex, 1)
    ex._external_plugin_provider = _failing_provider()
    token["v"] = (2, ("demo", "other"))  # another plugin started
    assert await ex.plugin_list_provider(force_refresh=False) == PLUGINS


@pytest.mark.asyncio
async def test_failed_refresh_removes_only_dead_plugins(monkeypatch, clock):
    both = [{"id": "demo"}, {"id": "gone"}]
    token = {"v": (1, ("demo", "gone"))}
    ex = _executor(monkeypatch, _Provider(result=both), token_fn=lambda: token["v"])
    await _turns(ex, 1)
    ex._external_plugin_provider = _failing_provider()
    token["v"] = (1, ("demo",))  # "gone" crashed: no lifecycle event
    assert await ex.plugin_list_provider(force_refresh=False) == [{"id": "demo"}]


@pytest.mark.asyncio
async def test_failed_refresh_after_stop_removes_stopped_plugin(monkeypatch, clock):
    token = {"v": (1, ("demo",))}
    ex = _executor(monkeypatch, _Provider(), token_fn=lambda: token["v"])
    await _turns(ex, 1)
    ex._external_plugin_provider = _failing_provider()
    token["v"] = (2, ())  # stopped normally
    for _ in range(2):
        assert await ex.plugin_list_provider(force_refresh=False) == []
    assert ex.plugin_list == []


def test_change_token_never_waits_on_plugin_hosts_lock(monkeypatch):
    # The token is read synchronously on the event loop. With a writer holding
    # the plugin-hosts lock it must return immediately (last cached snapshot),
    # never block on the lock or cache an empty snapshot from lock contention.
    state_mod = importlib.import_module("plugin.core.state")
    from app.agent_server import api_runtime as srv

    class _Host:
        def is_alive(self) -> bool:
            return True

    st = state_mod.GlobalState()
    monkeypatch.setattr(state_mod, "state", st)
    st.plugin_hosts["demo"] = _Host()
    assert srv._plugin_list_change_token()[1] == ("demo",)

    st.invalidate_snapshot_cache("hosts")
    assert st._plugin_hosts_rwlock.acquire_write(timeout=1.0)
    try:
        # Would deadlock/wait here if the read lock were awaited.
        assert srv._plugin_list_change_token()[1] == ("demo",)
        assert st._snapshot_cache["hosts"]["data"]  # contention cached nothing empty
    finally:
        st._plugin_hosts_rwlock.release_write()


def test_change_token_unreadable_when_no_snapshot_without_blocking(monkeypatch):
    state_mod = importlib.import_module("plugin.core.state")
    from app.agent_server import api_runtime as srv

    st = state_mod.GlobalState()
    monkeypatch.setattr(state_mod, "state", st)
    assert st._plugin_hosts_rwlock.acquire_write(timeout=1.0)
    try:
        with pytest.raises(RuntimeError):
            srv._plugin_list_change_token()
    finally:
        st._plugin_hosts_rwlock.release_write()


class _AliveHost:
    def is_alive(self) -> bool:
        return True


@pytest.mark.parametrize(
    "marker",
    [{"runtime_source_missing": True}, {"runtime_load_state": "failed"}],
)
def test_change_token_excludes_live_plugins_server_would_not_report_running(monkeypatch, marker):
    # /plugins reports such plugins as source_missing / load_failed, which the
    # analyzer excludes; the token's alive set must agree, even though the
    # host process is still alive.
    state_mod = importlib.import_module("plugin.core.state")
    from app.agent_server import api_runtime as srv

    st = state_mod.GlobalState()
    monkeypatch.setattr(state_mod, "state", st)
    st.plugin_hosts["demo"] = _AliveHost()
    st.plugins["demo"] = {"id": "demo"}
    before = srv._plugin_list_change_token()
    assert before[1] == ("demo",)

    st.plugins["demo"] = {"id": "demo", **marker}
    st.invalidate_snapshot_cache("plugins")
    after = srv._plugin_list_change_token()
    assert after[1] == ()
    assert after != before


@pytest.mark.asyncio
async def test_failed_refresh_drops_source_missing_plugin_with_live_host(monkeypatch, clock):
    state_mod = importlib.import_module("plugin.core.state")
    from app.agent_server import api_runtime as srv

    st = state_mod.GlobalState()
    monkeypatch.setattr(state_mod, "state", st)
    st.plugin_hosts["demo"] = _AliveHost()
    st.plugins["demo"] = {"id": "demo"}
    ex = _executor(monkeypatch, _Provider(), token_fn=srv._plugin_list_change_token)
    await _turns(ex, 1)
    ex._external_plugin_provider = _failing_provider()

    st.plugins["demo"]["runtime_source_missing"] = True  # registry refresh
    st.invalidate_snapshot_cache("plugins")
    for _ in range(2):
        assert await ex.plugin_list_provider(force_refresh=False) == []


def _invalidate_after_snapshot(monkeypatch, st, rwlock, cache_type):
    # Deterministically land an invalidation between the snapshot being taken
    # (read lock held) and the snapshot being written back to the cache.
    real_release = rwlock.release_read

    def release_then_invalidate():
        real_release()
        st.plugin_hosts["late"] = _AliveHost()  # e.g. a plugin registering
        st.invalidate_snapshot_cache(cache_type)

    monkeypatch.setattr(rwlock, "release_read", release_then_invalidate, raising=False)
    return real_release


@pytest.mark.parametrize("reader", ["nowait", "cached"])
def test_stale_hosts_snapshot_does_not_overwrite_invalidated_cache(monkeypatch, reader):
    state_mod = importlib.import_module("plugin.core.state")
    st = state_mod.GlobalState()
    st.plugin_hosts["demo"] = _AliveHost()
    lock = st._plugin_hosts_rwlock
    real_release = _invalidate_after_snapshot(monkeypatch, st, lock, "hosts")

    read = (
        st.get_plugin_hosts_snapshot_nowait
        if reader == "nowait"
        else lambda: st.get_plugin_hosts_snapshot_cached(timeout=0.1)
    )
    assert set(read()) == {"demo"}  # this read raced with the registration

    monkeypatch.setattr(lock, "release_read", real_release, raising=False)
    # The stale snapshot must not have been cached as fresh.
    assert set(read()) == {"demo", "late"}


def _entry_handler(entry_id: str):
    from plugin._types.events import EventHandler, EventMeta

    meta = EventMeta(event_type="plugin_entry", id=entry_id, name=entry_id)
    return EventHandler(meta=meta, handler=lambda *a, **k: None)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["register", "unregister"])
async def test_dynamic_entry_change_moves_token_and_refetches(monkeypatch, clock, change):
    # enable_entry / disable_entry of a dynamic entry emits ENTRY_UPDATE, which
    # only touches event_handlers: no lifecycle event, host still alive.
    state_mod = importlib.import_module("plugin.core.state")
    from app.agent_server import api_runtime as srv

    st = state_mod.GlobalState()
    monkeypatch.setattr(state_mod, "state", st)
    st.plugin_hosts["demo"] = _AliveHost()
    st.plugins["demo"] = {"id": "demo"}
    if change == "unregister":
        st.register_event_handler("demo", _entry_handler("dynamic"))

    provider = _Provider()
    ex = _executor(monkeypatch, provider, token_fn=srv._plugin_list_change_token)
    await _turns(ex, 2)
    assert provider.calls == 1
    before = srv._plugin_list_change_token()

    if change == "register":
        st.register_event_handler("demo", _entry_handler("dynamic"))
    else:
        st.unregister_event_handler("demo", "dynamic")

    after = srv._plugin_list_change_token()
    assert after[0][0] == before[0][0]  # lifecycle revision unchanged
    assert after[1] == before[1] == ("demo",)
    assert after != before
    await _turns(ex, 1)
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_executing_entry_leaves_handlers_revision_unchanged(monkeypatch):
    # The token must stay put across ordinary entry runs, or the cache is useless.
    state_mod = importlib.import_module("plugin.core.state")
    from app.agent_server import api_runtime as srv
    from plugin.runs import trigger_service

    st = state_mod.GlobalState()
    monkeypatch.setattr(state_mod, "state", st)
    monkeypatch.setattr(trigger_service, "state", st)
    st.plugin_hosts["demo"] = _AliveHost()
    st.plugins["demo"] = {"id": "demo"}
    st.register_event_handler("demo", _entry_handler("run"))

    class _TriggerHost:
        async def trigger(self, entry_id, args, timeout):
            return {"ok": True}

    before = srv._plugin_list_change_token()
    revision = st.get_event_handlers_revision()
    for _ in range(3):
        await trigger_service._execute_trigger(
            host=_TriggerHost(), plugin_id="demo", entry_id="run", args={}, trace_id="t",
        )
    assert st.get_event_handlers_revision() == revision
    assert srv._plugin_list_change_token() == before
