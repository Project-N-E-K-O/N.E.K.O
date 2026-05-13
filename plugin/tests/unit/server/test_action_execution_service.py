"""Unit tests for plugin.server.application.actions.execution_service."""
from __future__ import annotations

import threading
from typing import Any

import pytest

from plugin.server.application.actions.execution_service import (
    ActionExecutionService,
    _plugin_id_from_action_id,
)
from plugin.server.domain.action_models import ActionDescriptor
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

    async def test_unknown_system_action_falls_through_to_list_handler(self) -> None:
        """Once the lifecycle-keyword gate was added, ``system:demo:explode``
        no longer matches a lifecycle action — `explode` isn't in the keyword
        set — so it falls through to the list-action handler (which itself
        currently raises ACTION_NOT_IMPLEMENTED). The point of this test is
        that it does NOT raise ACTION_NOT_FOUND from the system handler."""
        svc = _build_service()
        with pytest.raises(ServerDomainError) as exc_info:
            await svc.execute("system:demo:explode")
        assert exc_info.value.code == "ACTION_NOT_IMPLEMENTED"

    async def test_two_segment_system_action_falls_through_to_list_handler(self) -> None:
        """``system:bad`` (2 segments) is now treated as plugin "system"
        calling list action "bad" — no longer reserved by the system handler
        — so the dispatch routes it to the list handler."""
        svc = _build_service()
        with pytest.raises(ServerDomainError) as exc_info:
            await svc.execute("system:bad")
        assert exc_info.value.code == "ACTION_NOT_IMPLEMENTED"

    async def test_lifecycle_action_rejects_extra_segments(self) -> None:
        """A crafted id like `system:demo:stop:unexpected` must NOT execute the
        lifecycle handler — only exactly three segments are valid for
        start/stop/reload/toggle/profile."""
        lifecycle = _FakeLifecycleService()
        svc = _build_service(lifecycle=lifecycle)
        with pytest.raises(ServerDomainError) as exc_info:
            await svc.execute("system:demo:stop:unexpected")
        assert exc_info.value.code == "ACTION_NOT_FOUND"
        assert lifecycle.calls == []  # the stop must not have run


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

    async def test_button_entry_preserves_unsupported_error(self) -> None:
        """If the host has no ``trigger`` method, the handler must surface the
        intentional ENTRY_TRIGGER_UNSUPPORTED (501) instead of collapsing it
        into a generic ENTRY_TRIGGER_FAILED (500)."""
        from types import SimpleNamespace

        host = SimpleNamespace()  # no `trigger` attribute on purpose
        from plugin.core.state import state as real_state
        original_hosts = real_state.plugin_hosts
        real_state.plugin_hosts = {"demo": host}
        try:
            svc = _build_service()
            with pytest.raises(ServerDomainError) as exc_info:
                await svc.execute("system:demo:entry:do_thing", value=None)
            assert exc_info.value.code == "ENTRY_TRIGGER_UNSUPPORTED"
            assert exc_info.value.status_code == 501
        finally:
            real_state.plugin_hosts = original_hosts

    async def test_toggle_entry_preserves_unsupported_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the host lacks ``enable_entry`` / ``disable_entry``, the handler
        must surface ENTRY_TOGGLE_UNSUPPORTED (501) rather than wrap it as
        ENTRY_TOGGLE_FAILED (500)."""
        from types import SimpleNamespace

        host = SimpleNamespace()  # no enable_entry / disable_entry
        monkeypatch.setattr(
            "plugin.server.application.actions.execution_service._is_plugin_running",
            lambda pid: True,
        )
        from plugin.core.state import state as real_state
        original_hosts = real_state.plugin_hosts
        real_state.plugin_hosts = {"demo": host}
        try:
            svc = _build_service()
            with pytest.raises(ServerDomainError) as exc_info:
                await svc.execute("system:demo:entry:my_svc", value=True)
            assert exc_info.value.code == "ENTRY_TOGGLE_UNSUPPORTED"
            assert exc_info.value.status_code == 501
        finally:
            real_state.plugin_hosts = original_hosts


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

        # Provide a fake PluginSettings class with a hot field
        from pydantic import BaseModel, Field

        class _FakeSettings(BaseModel):
            model_config = {"toml_section": "settings"}
            enabled: bool = Field(default=False, json_schema_extra={"hot": True})

        monkeypatch.setattr(
            "plugin.server.application.actions.execution_service.hot_update_plugin_config",
            fake_hot_update,
        )
        monkeypatch.setattr(
            "plugin.server.application.actions.execution_service.resolve_settings_class",
            lambda pid, **kw: _FakeSettings,
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

    async def test_profile_reload_failure_surfaces_in_message(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ``set_plugin_active_profile`` succeeds but ``reload_plugin``
        fails, the response must surface the reload failure in the message
        rather than silently returning a misleading "Profile switched"
        success — the running plugin is still on the old profile."""
        # Stub out the on-disk profile switch as a sync no-op (the production
        # call site invokes it via ``asyncio.to_thread`` so a coroutine
        # wouldn't work here).
        import plugin.config.service as config_service_mod
        monkeypatch.setattr(
            config_service_mod, "set_plugin_active_profile",
            lambda *a, **kw: None, raising=False,
        )

        class _FailingReloadLifecycle(_FakeLifecycleService):
            async def reload_plugin(self, plugin_id: str) -> dict[str, Any]:
                raise RuntimeError("reload boom")

        svc = _build_service(lifecycle=_FailingReloadLifecycle())
        resp = await svc.execute("system:demo:profile", value="prod")
        assert resp.success is True  # config did change
        assert "reload failed" in resp.message
        assert "prod" in resp.message
        assert "reload boom" in resp.message

    async def test_profile_activation_preserves_domain_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import plugin.config.service as config_service_mod

        def _raise_missing_profile(*_args: object, **_kwargs: object) -> None:
            raise ServerDomainError(
                code="PLUGIN_PROFILE_NOT_FOUND",
                message="Profile 'missing' not found",
                status_code=404,
                details={"plugin_id": "demo", "profile_name": "missing"},
            )

        monkeypatch.setattr(
            config_service_mod,
            "set_plugin_active_profile",
            _raise_missing_profile,
            raising=False,
        )

        lifecycle = _FakeLifecycleService()
        svc = _build_service(lifecycle=lifecycle)
        with pytest.raises(ServerDomainError) as exc_info:
            await svc.execute("system:demo:profile", value="missing")

        assert exc_info.value.code == "PLUGIN_PROFILE_NOT_FOUND"
        assert exc_info.value.status_code == 404
        assert lifecycle.calls == []


# ── _plugin_id_from_action_id reverse lookup ────────────────────────

@pytest.mark.plugin_unit
class TestPluginIdFromActionId:
    """The reverse lookup must mirror ``ActionExecutionService.execute`` so
    that ``_find_action`` after a settings/list-action execute hits the
    right plugin slice. The previous implementation read ``parts[1]`` for
    every ``system:`` prefix, which routed plugin "system" settings to a
    nonexistent plugin "settings"."""

    def test_lifecycle_uses_parts_1(self) -> None:
        assert _plugin_id_from_action_id("system:demo:start") == "demo"
        assert _plugin_id_from_action_id("system:demo:stop") == "demo"
        assert _plugin_id_from_action_id("system:demo:entry:do_thing") == "demo"
        assert _plugin_id_from_action_id("system:demo:profile") == "demo"

    def test_system_plugin_settings_uses_parts_0(self) -> None:
        """For plugin literally named "system", ``parts[0]`` is the plugin
        id; only authorize the lifecycle shortcut when ``parts[2]`` is a
        known lifecycle keyword."""
        assert _plugin_id_from_action_id("system:settings:enabled") == "system"
        assert _plugin_id_from_action_id("system:foo") == "system"

    def test_settings_precedence_beats_lifecycle_keyword(self) -> None:
        """Field name colliding with a lifecycle keyword (e.g. ``start``)
        must not flip the resolver to the lifecycle shortcut — settings
        precedence wins."""
        assert _plugin_id_from_action_id("system:settings:start") == "system"
        assert _plugin_id_from_action_id("system:settings:stop") == "system"

    def test_normal_plugin_settings_uses_parts_0(self) -> None:
        assert _plugin_id_from_action_id("demo:settings:enabled") == "demo"
        assert _plugin_id_from_action_id("demo:greet") == "demo"


# ── Plugin literally named "system" — namespace collision regression ─

@pytest.mark.plugin_unit
@pytest.mark.asyncio
class TestSystemPluginIdNamespace:
    """A plugin whose ``plugin_id == "system"`` must not have its actions
    swallowed by the ``system:`` lifecycle prefix. Dispatch is structural —
    we only treat ``system:{x}:{y}`` as a lifecycle when ``{y}`` is a known
    lifecycle keyword."""

    async def test_system_named_plugin_list_action_falls_through(self) -> None:
        """``system:foo`` (2 segments) is a list action for plugin "system",
        not a lifecycle call. The list handler currently raises
        ACTION_NOT_IMPLEMENTED, which is the *correct* routing — the test
        verifies the dispatch reaches the list handler, not the system one."""
        svc = _build_service()
        with pytest.raises(ServerDomainError) as exc_info:
            await svc.execute("system:foo")
        assert exc_info.value.code == "ACTION_NOT_IMPLEMENTED"

    async def test_system_named_plugin_settings_routes_to_settings_handler(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``system:settings:enabled`` for plugin_id=="system" must reach the
        settings handler — not get misclassified as a lifecycle 'settings'
        action (which would fail ACTION_NOT_FOUND).

        Also locks the ``_find_action`` re-fetch path: a previously-buggy
        ``_find_action`` mapped this action_id to plugin "settings", so the
        aggregation filter would miss the descriptor and
        ``resp.action`` would come back ``None``.
        """
        captured: dict[str, Any] = {}

        async def fake_hot_update(plugin_id: str, updates: dict, mode: str) -> dict:
            captured["plugin_id"] = plugin_id
            captured["updates"] = updates
            captured["mode"] = mode
            return {"message": "ok"}

        from pydantic import BaseModel, Field

        class _FakeSettings(BaseModel):
            model_config = {"toml_section": "settings"}
            enabled: bool = Field(default=False, json_schema_extra={"hot": True})

        monkeypatch.setattr(
            "plugin.server.application.actions.execution_service.hot_update_plugin_config",
            fake_hot_update,
        )
        monkeypatch.setattr(
            "plugin.server.application.actions.execution_service.resolve_settings_class",
            lambda pid, **kw: _FakeSettings,
        )

        descriptor = ActionDescriptor(
            action_id="system:settings:enabled",
            type="instant",
            label="Enabled",
            category="System",
            plugin_id="system",
            control="toggle",
            current_value=True,
        )
        aggregation = _FakeAggregationService(actions=[descriptor])
        svc = _build_service(aggregation=aggregation)
        resp = await svc.execute("system:settings:enabled", value=True)
        assert resp.success is True
        assert captured["plugin_id"] == "system"
        assert captured["updates"] == {"settings": {"enabled": True}}
        # The whole point of fixing _find_action's namespace handling: the
        # refreshed descriptor must come back so the palette can update its
        # inline widget without a full re-fetch.
        assert resp.action is not None
        assert resp.action.action_id == "system:settings:enabled"

    async def test_lifecycle_keyword_collision_with_system_plugin_field(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Even if plugin "system" has a hot field literally named "start"
        (which clashes with a lifecycle keyword), ``system:settings:start``
        must still reach the settings handler because the structural form
        ``{x}:settings:{y}`` wins over the ``system:`` prefix shortcut."""
        captured: dict[str, Any] = {}

        async def fake_hot_update(plugin_id: str, updates: dict, mode: str) -> dict:
            captured["plugin_id"] = plugin_id
            captured["updates"] = updates
            return {"message": "ok"}

        from pydantic import BaseModel, Field

        class _FakeSettings(BaseModel):
            model_config = {"toml_section": "settings"}
            start: bool = Field(default=False, json_schema_extra={"hot": True})

        monkeypatch.setattr(
            "plugin.server.application.actions.execution_service.hot_update_plugin_config",
            fake_hot_update,
        )
        monkeypatch.setattr(
            "plugin.server.application.actions.execution_service.resolve_settings_class",
            lambda pid, **kw: _FakeSettings,
        )

        descriptor = ActionDescriptor(
            action_id="system:settings:start",
            type="instant",
            label="Start",
            category="System",
            plugin_id="system",
            control="toggle",
            current_value=True,
        )
        aggregation = _FakeAggregationService(actions=[descriptor])
        svc = _build_service(aggregation=aggregation)
        resp = await svc.execute("system:settings:start", value=True)
        assert resp.success is True
        assert captured["plugin_id"] == "system"
        assert captured["updates"] == {"settings": {"start": True}}
        assert resp.action is not None
        assert resp.action.action_id == "system:settings:start"
