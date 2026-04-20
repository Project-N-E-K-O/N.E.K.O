"""Unit tests for plugin.server.application.actions.execution_service."""
from __future__ import annotations

import threading
from typing import Any

import pytest

from plugin.server.application.actions.execution_service import ActionExecutionService
from plugin.server.domain.action_models import ActionDescriptor, ActionExecuteResponse
from plugin.server.domain.errors import ServerDomainError


# ── Fakes ────────────────────────────────────────────────────────────

class _FakeLifecycleService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def start_plugin(self, plugin_id: str) -> dict[str, Any]:
        self.calls.append(("start", plugin_id))
        return {"success": True, "message": "started"}

    async def stop_plugin(self, plugin_id: str) -> dict[str, Any]:
        self.calls.append(("stop", plugin_id))
        return {"success": True, "message": "stopped"}

    async def reload_plugin(self, plugin_id: str) -> dict[str, Any]:
        self.calls.append(("reload", plugin_id))
        return {"success": True, "message": "reloaded"}


class _FakeAggregationService:
    def __init__(self, actions: list[ActionDescriptor] | None = None) -> None:
        self._actions = actions or []

    async def aggregate_actions(self, plugin_id: str | None = None) -> list[ActionDescriptor]:
        return [a for a in self._actions if plugin_id is None or a.plugin_id == plugin_id]


class _FakeHost:
    def __init__(self) -> None:
        self.enabled_entries: list[str] = []
        self.disabled_entries: list[str] = []
        self.triggered_entries: list[str] = []

    def enable_entry(self, entry_id: str) -> None:
        self.enabled_entries.append(entry_id)

    def disable_entry(self, entry_id: str) -> None:
        self.disabled_entries.append(entry_id)

    async def trigger(self, entry_id: str, args: dict, timeout: float = 30.0) -> object:
        self.triggered_entries.append(entry_id)
        return {"ok": True}


class _FakeState:
    def __init__(self, hosts: dict[str, Any] | None = None) -> None:
        self.plugin_hosts = hosts or {}
        self._lock = threading.RLock()

    def acquire_plugin_hosts_read_lock(self) -> Any:
        return self._lock

    def acquire_plugin_hosts_write_lock(self) -> Any:
        return self._lock


def _build_service(
    lifecycle: _FakeLifecycleService | None = None,
    aggregation: _FakeAggregationService | None = None,
) -> ActionExecutionService:
    svc = ActionExecutionService.__new__(ActionExecutionService)
    svc._lifecycle = lifecycle or _FakeLifecycleService()
    svc._aggregation = aggregation or _FakeAggregationService()

    from plugin.server.application.actions.execution_service import (
        _ListActionHandler,
        _SettingsActionHandler,
        _SystemActionHandler,
    )

    svc._settings_handler = _SettingsActionHandler(svc._aggregation)
    svc._system_handler = _SystemActionHandler(svc._lifecycle)
    svc._list_action_handler = _ListActionHandler()
    return svc


# ── System handler tests ─────────────────────────────────────────────

@pytest.mark.plugin_unit
@pytest.mark.asyncio
class TestSystemActions:
    async def test_toggle_starts_stopped_plugin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        lifecycle = _FakeLifecycleService()
        svc = _build_service(lifecycle=lifecycle)
        monkeypatch.setattr(
            "plugin.server.application.actions.execution_service._is_plugin_running",
            lambda pid: False,
        )
        resp = await svc.execute("system:demo:toggle", value=True)
        assert resp.success is True
        assert ("start", "demo") in lifecycle.calls

    async def test_toggle_stops_running_plugin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        lifecycle = _FakeLifecycleService()
        svc = _build_service(lifecycle=lifecycle)
        monkeypatch.setattr(
            "plugin.server.application.actions.execution_service._is_plugin_running",
            lambda pid: True,
        )
        resp = await svc.execute("system:demo:toggle", value=False)
        assert resp.success is True
        assert ("stop", "demo") in lifecycle.calls

    async def test_start(self) -> None:
        lifecycle = _FakeLifecycleService()
        svc = _build_service(lifecycle=lifecycle)
        resp = await svc.execute("system:demo:start")
        assert resp.success is True
        assert ("start", "demo") in lifecycle.calls

    async def test_stop(self) -> None:
        lifecycle = _FakeLifecycleService()
        svc = _build_service(lifecycle=lifecycle)
        resp = await svc.execute("system:demo:stop")
        assert resp.success is True
        assert ("stop", "demo") in lifecycle.calls

    async def test_reload(self) -> None:
        lifecycle = _FakeLifecycleService()
        svc = _build_service(lifecycle=lifecycle)
        resp = await svc.execute("system:demo:reload")
        assert resp.success is True
        assert ("reload", "demo") in lifecycle.calls

    async def test_unknown_system_action_raises(self) -> None:
        svc = _build_service()
        with pytest.raises(ServerDomainError) as exc_info:
            await svc.execute("system:demo:explode")
        assert exc_info.value.code == "ACTION_NOT_FOUND"

    async def test_malformed_system_action_raises(self) -> None:
        svc = _build_service()
        with pytest.raises(ServerDomainError) as exc_info:
            await svc.execute("system:bad")
        assert exc_info.value.code == "ACTION_NOT_FOUND"


# ── Entry toggle / button tests ──────────────────────────────────────

@pytest.mark.plugin_unit
@pytest.mark.asyncio
class TestEntryActions:
    async def test_button_entry_triggers_via_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Button-type entries (value=null) should call host.trigger."""
        host = _FakeHost()
        from plugin.core.state import state as real_state
        original_hosts = real_state.plugin_hosts
        real_state.plugin_hosts = {"demo": host}
        try:
            svc = _build_service()
            resp = await svc.execute("system:demo:entry:do_thing", value=None)
            assert resp.success is True
            assert "do_thing" in host.triggered_entries
        finally:
            real_state.plugin_hosts = original_hosts

    async def test_button_entry_plugin_not_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plugin.core.state import state as real_state
        original_hosts = real_state.plugin_hosts
        real_state.plugin_hosts = {}
        try:
            svc = _build_service()
            with pytest.raises(ServerDomainError) as exc_info:
                await svc.execute("system:demo:entry:do_thing", value=None)
            assert exc_info.value.code == "PLUGIN_NOT_RUNNING"
        finally:
            real_state.plugin_hosts = original_hosts

    async def test_toggle_entry_enable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        host = _FakeHost()
        monkeypatch.setattr(
            "plugin.server.application.actions.execution_service._is_plugin_running",
            lambda pid: True,
        )
        from plugin.core.state import state as real_state
        original_hosts = real_state.plugin_hosts
        real_state.plugin_hosts = {"demo": host}
        try:
            svc = _build_service()
            resp = await svc.execute("system:demo:entry:my_svc", value=True)
            assert resp.success is True
            assert "my_svc" in host.enabled_entries
        finally:
            real_state.plugin_hosts = original_hosts

    async def test_toggle_entry_disable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        host = _FakeHost()
        monkeypatch.setattr(
            "plugin.server.application.actions.execution_service._is_plugin_running",
            lambda pid: True,
        )
        from plugin.core.state import state as real_state
        original_hosts = real_state.plugin_hosts
        real_state.plugin_hosts = {"demo": host}
        try:
            svc = _build_service()
            resp = await svc.execute("system:demo:entry:my_svc", value=False)
            assert resp.success is True
            assert "my_svc" in host.disabled_entries
        finally:
            real_state.plugin_hosts = original_hosts

    async def test_toggle_entry_plugin_not_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "plugin.server.application.actions.execution_service._is_plugin_running",
            lambda pid: False,
        )
        svc = _build_service()
        with pytest.raises(ServerDomainError) as exc_info:
            await svc.execute("system:demo:entry:my_svc", value=True)
        assert exc_info.value.code == "PLUGIN_NOT_RUNNING"


# ── Settings handler tests ───────────────────────────────────────────

@pytest.mark.plugin_unit
@pytest.mark.asyncio
class TestSettingsActions:
    async def test_settings_action_calls_hot_update(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        async def fake_hot_update(plugin_id: str, updates: dict, mode: str) -> dict:
            captured["plugin_id"] = plugin_id
            captured["updates"] = updates
            captured["mode"] = mode
            return {"message": "ok"}

        monkeypatch.setattr(
            "plugin.server.application.actions.execution_service.hot_update_plugin_config",
            fake_hot_update,
        )
        monkeypatch.setattr(
            "plugin.server.application.actions.execution_service.resolve_settings_class",
            lambda pid: None,
        )

        svc = _build_service()
        resp = await svc.execute("demo:settings:enabled", value=True)
        assert resp.success is True
        assert captured["plugin_id"] == "demo"
        assert captured["updates"] == {"settings": {"enabled": True}}
        assert captured["mode"] == "temporary"


# ── List action handler tests ────────────────────────────────────────

@pytest.mark.plugin_unit
@pytest.mark.asyncio
class TestListActions:
    async def test_list_action_raises_not_implemented(self) -> None:
        svc = _build_service()
        with pytest.raises(ServerDomainError) as exc_info:
            await svc.execute("demo:some_action")
        assert exc_info.value.code == "ACTION_NOT_IMPLEMENTED"


# ── Dispatch routing tests ───────────────────────────────────────────

@pytest.mark.plugin_unit
@pytest.mark.asyncio
class TestDispatchRouting:
    async def test_unknown_action_id_raises(self) -> None:
        svc = _build_service()
        with pytest.raises(ServerDomainError) as exc_info:
            await svc.execute("single-segment-no-colon")
        assert exc_info.value.code == "ACTION_NOT_FOUND"

    async def test_profile_requires_string_value(self) -> None:
        svc = _build_service()
        with pytest.raises(ServerDomainError) as exc_info:
            await svc.execute("system:demo:profile", value=123)
        assert exc_info.value.code == "INVALID_ARGUMENT"

    async def test_profile_requires_nonempty_string(self) -> None:
        svc = _build_service()
        with pytest.raises(ServerDomainError) as exc_info:
            await svc.execute("system:demo:profile", value="  ")
        assert exc_info.value.code == "INVALID_ARGUMENT"
