"""Local session presence gate for proactive chat (#3552).

Covers the platform-neutral state machine in
``main_logic/activity/system_presence.py``, the Windows notification dispatch
(pure functions, so it runs on every platform), the fail-open rules, and the
proactive entry guard that consumes it.
"""

from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from main_logic.activity import system_presence as sp
from main_logic.proactive_chat.contracts import (
    PROACTIVE_REASON_PASS_SYSTEM_INACTIVE,
    PROACTIVE_STAGE_ENTRY_GUARD,
)
from main_routers.system_router import proactive_chat_flow as flow


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _armed_state(clock: FakeClock | None = None) -> sp.SystemPresenceState:
    state = sp.SystemPresenceState(clock=clock or FakeClock())
    state.on_session_lock_state(False)
    state.on_display_state(True)
    assert state.inactive_reason() is None
    return state


# ── fail-open: never suppress before both "display on" and "unlocked" were seen ──


def test_display_off_from_startup_never_suppresses():
    # Headless box / service session: the first and only display report is "off".
    state = sp.SystemPresenceState(clock=FakeClock())
    state.on_session_lock_state(False)
    state.on_display_state(False)
    assert state.inactive_reason() is None


def test_lock_state_unknown_never_suppresses():
    # Lock query failed (e.g. service session): display off must not suppress.
    state = sp.SystemPresenceState(clock=FakeClock())
    state.on_display_state(True)
    state.on_display_state(False)
    assert state.inactive_reason() is None


def test_locked_from_startup_never_suppresses():
    state = sp.SystemPresenceState(clock=FakeClock())
    state.on_session_lock_state(True)
    state.on_display_state(True)
    assert state.inactive_reason() is None


def test_failed_watcher_allows():
    state = _armed_state()
    state.on_display_state(False)
    assert state.inactive_reason() == sp.REASON_DISPLAY_OFF
    state.mark_failed()
    assert state.inactive_reason() is None


# ── armed behaviour ──


def test_display_off_then_on_suppresses_and_keeps_quiet_after_return():
    clock = FakeClock()
    state = _armed_state(clock)
    state.on_display_state(False)
    assert state.inactive_reason() == sp.REASON_DISPLAY_OFF
    clock.now += 3600
    state.on_display_state(True)
    assert state.inactive_reason() == sp.REASON_RECENTLY_RETURNED
    clock.now += sp.SYSTEM_RETURN_QUIET_SECONDS - 1
    assert state.inactive_reason() == sp.REASON_RECENTLY_RETURNED
    clock.now += 1
    assert state.inactive_reason() is None


def test_lock_then_unlock():
    clock = FakeClock()
    state = _armed_state(clock)
    state.on_session_lock_state(True)
    assert state.inactive_reason() == sp.REASON_SESSION_LOCKED
    state.on_session_lock_state(False)
    assert state.inactive_reason() == sp.REASON_RECENTLY_RETURNED
    clock.now += sp.SYSTEM_RETURN_QUIET_SECONDS
    assert state.inactive_reason() is None


def test_suspend_then_resume():
    clock = FakeClock()
    state = _armed_state(clock)
    state.on_suspend()
    assert state.inactive_reason() == sp.REASON_SUSPENDED
    clock.now += 8 * 3600
    state.on_resume()
    assert state.inactive_reason() == sp.REASON_RECENTLY_RETURNED
    clock.now += sp.SYSTEM_RETURN_QUIET_SECONDS
    assert state.inactive_reason() is None


def test_dimmed_display_counts_as_on():
    state = _armed_state()
    sp.apply_power_broadcast(state, sp.PBT_POWERSETTINGCHANGE, 2)
    assert state.inactive_reason() is None
    sp.apply_power_broadcast(state, sp.PBT_POWERSETTINGCHANGE, sp.DISPLAY_STATUS_OFF)
    assert state.inactive_reason() == sp.REASON_DISPLAY_OFF


def test_power_broadcast_dispatch():
    clock = FakeClock()
    state = _armed_state(clock)
    sp.apply_power_broadcast(state, sp.PBT_APMSUSPEND)
    assert state.inactive_reason() == sp.REASON_SUSPENDED
    sp.apply_power_broadcast(state, sp.PBT_APMRESUMEAUTOMATIC)
    assert state.inactive_reason() == sp.REASON_RECENTLY_RETURNED
    clock.now += sp.SYSTEM_RETURN_QUIET_SECONDS
    # Another power setting (display_status=None) is ignored.
    sp.apply_power_broadcast(state, sp.PBT_POWERSETTINGCHANGE, None)
    assert state.inactive_reason() is None


def test_session_lock_dispatch():
    state = _armed_state()
    sp.apply_session_change(state, sp.WTS_SESSION_LOCK, lambda: None)
    assert state.inactive_reason() == sp.REASON_SESSION_LOCKED


# ── remote desktop disconnect must not leave proactive chat stuck ──


def test_remote_disconnect_forgets_state_and_reconnect_rearms_from_scratch():
    clock = FakeClock()
    state = _armed_state(clock)
    sp.apply_session_change(state, sp.WTS_SESSION_LOCK, lambda: None)
    sp.apply_power_broadcast(state, sp.PBT_POWERSETTINGCHANGE, sp.DISPLAY_STATUS_OFF)
    assert state.inactive_reason() is not None

    sp.apply_session_change(state, sp.WTS_REMOTE_DISCONNECT, lambda: None)
    assert state.inactive_reason() is None
    # Disconnected session keeps reporting "off"/"locked": still unarmed, still allowed.
    sp.apply_power_broadcast(state, sp.PBT_POWERSETTINGCHANGE, sp.DISPLAY_STATUS_OFF)
    sp.apply_session_change(state, sp.WTS_SESSION_LOCK, lambda: None)
    assert state.inactive_reason() is None

    # Reconnect: lock state is queried again; display unknown until reported.
    sp.apply_session_change(state, sp.WTS_REMOTE_CONNECT, lambda: False)
    assert state.inactive_reason() is None
    sp.apply_power_broadcast(state, sp.PBT_POWERSETTINGCHANGE, 1)
    assert state.inactive_reason() is None
    sp.apply_session_change(state, sp.WTS_SESSION_LOCK, lambda: None)
    assert state.inactive_reason() == sp.REASON_SESSION_LOCKED


def test_console_reconnect_with_unknown_lock_state_stays_unarmed():
    state = _armed_state()
    sp.apply_session_change(state, sp.WTS_CONSOLE_DISCONNECT, lambda: None)
    sp.apply_session_change(state, sp.WTS_CONSOLE_CONNECT, lambda: None)
    sp.apply_power_broadcast(state, sp.PBT_POWERSETTINGCHANGE, 1)
    sp.apply_power_broadcast(state, sp.PBT_POWERSETTINGCHANGE, sp.DISPLAY_STATUS_OFF)
    assert state.inactive_reason() is None


# ── watcher loading ──


@pytest.fixture
def fresh_singleton(monkeypatch):
    state = sp.SystemPresenceState(clock=FakeClock())
    monkeypatch.setattr(sp, "_state", state)
    monkeypatch.setattr(sp, "_watcher_started", False)
    return state


def test_non_windows_never_loads_windows_module(monkeypatch, fresh_singleton):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delitem(sys.modules, "main_logic.activity.system_presence_windows", raising=False)
    assert sp.get_system_inactive_reason() is None
    assert "main_logic.activity.system_presence_windows" not in sys.modules


def test_remote_deployment_never_loads_windows_module(monkeypatch, fresh_singleton):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("NEKO_ACTIVITY_TRACKER_REMOTE", "1")
    fake = types.ModuleType("main_logic.activity.system_presence_windows")
    fake.start_watcher = lambda state: pytest.fail("watcher must not start for remote deployments")
    monkeypatch.setitem(sys.modules, "main_logic.activity.system_presence_windows", fake)
    assert sp.get_system_inactive_reason() is None


def test_watcher_start_failure_fails_open_and_logs_once(monkeypatch, fresh_singleton, caplog):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.setattr(sp, "_watcher_supported", lambda: True)
    calls = []

    def boom(state):
        calls.append(state)
        raise OSError("RegisterClassExW failed")

    fake = types.ModuleType("main_logic.activity.system_presence_windows")
    fake.start_watcher = boom
    monkeypatch.setitem(sys.modules, "main_logic.activity.system_presence_windows", fake)
    monkeypatch.setattr(sys.modules["main_logic.activity"], "system_presence_windows", fake, raising=False)

    with caplog.at_level("WARNING", logger=sp.__name__):
        for _ in range(3):
            fresh_singleton.on_session_lock_state(False)
            fresh_singleton.on_display_state(True)
            fresh_singleton.on_display_state(False)
            assert sp.get_system_inactive_reason() is None
    assert len(calls) == 1
    assert len([r for r in caplog.records if "watcher unavailable" in r.getMessage()]) == 1


# ── proactive entry guard ──


def _request(client_host: str, body: dict | None = None, headers: dict | None = None) -> Request:
    raw = json.dumps(body or {}).encode()
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": raw, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/proactive_chat",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (client_host, 50000),
        "server": ("127.0.0.1", 48911),
        "scheme": "http",
        "query_string": b"",
    }
    return Request(scope, receive)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_entry_guard_uses_signal_for_loopback(monkeypatch, host):
    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)
    monkeypatch.setattr(flow, "get_system_inactive_reason", lambda: sp.REASON_DISPLAY_OFF)
    assert flow._local_system_inactive_reason(_request(host)) == sp.REASON_DISPLAY_OFF


@pytest.mark.parametrize("host", ["192.168.1.20", "10.0.0.5", "testclient", ""])
def test_entry_guard_ignores_signal_for_non_loopback(monkeypatch, host):
    monkeypatch.setattr(
        flow, "get_system_inactive_reason",
        lambda: pytest.fail("remote requests must not consult local OS signals"),
    )
    assert flow._local_system_inactive_reason(_request(host)) is None


def test_entry_guard_ignores_signal_for_remote_deployment(monkeypatch):
    monkeypatch.setenv("NEKO_ACTIVITY_TRACKER_REMOTE", "1")
    monkeypatch.setattr(
        flow, "get_system_inactive_reason",
        lambda: pytest.fail("remote deployments must not consult local OS signals"),
    )
    assert flow._local_system_inactive_reason(_request("127.0.0.1")) is None


def _wire_route(monkeypatch, reason):
    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)
    monkeypatch.setattr(flow, "_validate_local_mutation_request", lambda _request: None)
    monkeypatch.setattr(flow, "get_config_manager", lambda: SimpleNamespace(
        aget_character_data=AsyncMock(return_value=("Master", "Neko", None, None, None, {}, None, None, None))))
    monkeypatch.setattr(flow, "get_session_manager", lambda: {})
    monkeypatch.setattr(flow, "is_theater_active", lambda _: False)
    monkeypatch.setattr(flow, "get_system_inactive_reason", lambda: reason)
    service = AsyncMock(side_effect=AssertionError("service must not run"))
    monkeypatch.setattr(flow.proactive_service, "handle_proactive_chat", service)
    return service


@pytest.mark.asyncio
async def test_route_passes_before_service_when_local_session_inactive(monkeypatch):
    service = _wire_route(monkeypatch, sp.REASON_SESSION_LOCKED)
    response = await flow.proactive_chat(_request("127.0.0.1", {"lanlan_name": "Neko", "voice_mode": True}))
    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["action"] == "pass"
    assert body["success"] is True
    assert body["reason_code"] == PROACTIVE_REASON_PASS_SYSTEM_INACTIVE
    assert body["stage"] == PROACTIVE_STAGE_ENTRY_GUARD
    assert body["system_inactive_reason"] == sp.REASON_SESSION_LOCKED
    service.assert_not_called()


@pytest.mark.asyncio
async def test_route_runs_service_from_remote_client_even_if_local_session_inactive(monkeypatch):
    from main_logic.proactive_chat.contracts import ProactiveChatResult

    _wire_route(monkeypatch, sp.REASON_DISPLAY_OFF)
    reached = []

    async def handled(*args, **kwargs):
        reached.append(True)
        return ProactiveChatResult(body={"success": True, "action": "pass", "reason_code": "PASS_MODEL_PASS"})

    monkeypatch.setattr(flow.proactive_service, "handle_proactive_chat", handled)
    monkeypatch.setattr(flow, "capture_owner", AsyncMock(return_value=None))
    response = await flow.proactive_chat(_request("192.168.1.20", {"lanlan_name": "Neko"}))
    assert reached == [True]
    assert json.loads(response.body)["reason_code"] == "PASS_MODEL_PASS"


def test_entry_guard_fails_open_on_unexpected_request_shape(monkeypatch):
    monkeypatch.setattr(flow, "get_system_inactive_reason", lambda: sp.REASON_DISPLAY_OFF)
    assert flow._local_system_inactive_reason(SimpleNamespace()) is None
