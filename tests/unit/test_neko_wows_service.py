"""Service supervision: identity before action, and never kill someone else's."""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import urllib.error

import pytest

from tests.fake_clock import patch_module_clock

from plugin.plugins.neko_wows.adapters import service_manager as sm
from plugin.plugins.neko_wows.adapters.service_manager import (
    MODE_CONFLICT,
    MODE_DISABLED,
    MODE_EXTERNAL,
    MODE_MANAGED,
    MODE_OFFLINE,
    SERVICE_ID,
    ServiceHealth,
    ServiceStatus,
    WowsServiceManager,
    api_major,
    is_loopback_url,
    is_usable_service_url,
    probe_health,
)
from plugin.plugins.neko_wows.domain.contracts import WowsConfig
from plugin.plugins.neko_wows import NekoWowsPlugin, _mod_hint


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class GarbageResponse:
    def read(self):
        return b"<html>not json</html>"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def healthz_http_error(code=404, msg="Not Found"):
    return urllib.error.HTTPError(
        "http://127.0.0.1:8111/healthz", code, msg, {}, None,
    )


def patch_urlopen(monkeypatch, payload=None, error=None):
    def fake_urlopen(url, timeout=None):
        if error is not None:
            raise error
        return FakeResponse(payload)

    monkeypatch.setattr(sm.urllib.request, "urlopen", fake_urlopen)


def skip_startup_wait(monkeypatch):
    """Let the startup poll loop run out instantly.

    The clock has to advance for the deadline to pass, and the fake is scoped to
    the service-manager module so background threads keep the real one.
    """
    ticks = iter([0.0] + [t * 0.5 for t in range(1, 200)])
    patch_module_clock(
        monkeypatch, sm,
        monotonic=lambda: next(ticks, 1000.0),
        sleep=lambda _seconds: None,
    )


def healthy_payload(**overrides):
    payload = {
        "ok": True,
        "serviceId": SERVICE_ID,
        "apiVersion": "1.0",
        "instanceId": "inst-a",
        "status": "waiting",
    }
    payload.update(overrides)
    return payload


def cfg(**overrides):
    data = {"service_url": "http://127.0.0.1:8111", "service_auto_start": True}
    data.update(overrides)
    return WowsConfig.from_mapping(data)


# --- helpers -------------------------------------------------------------

def test_api_major_reads_the_leading_number():
    assert api_major("1.0") == 1
    assert api_major("2.5") == 2
    assert api_major("") is None
    assert api_major("abc") is None


def test_loopback_detection():
    assert is_loopback_url("http://127.0.0.1:8111") is True
    assert is_loopback_url("http://localhost:8111") is True
    assert is_loopback_url("http://192.168.1.5:8111") is False


def test_a_malformed_address_is_not_usable():
    assert is_usable_service_url("http://127.0.0.1:8111") is True
    assert is_usable_service_url("http://127.0.0.1:not-a-port") is False
    assert is_usable_service_url("127.0.0.1:8111") is False
    assert is_usable_service_url("") is False


# --- identity probing ----------------------------------------------------

def test_our_service_is_recognized(monkeypatch):
    patch_urlopen(monkeypatch, healthy_payload())
    health = probe_health("http://127.0.0.1:8111", 1.0)
    assert health.usable is True
    assert health.instance_id == "inst-a"
    assert health.source_status == "waiting"


def test_healthz_status_is_preferred_over_legacy_source_status(monkeypatch):
    patch_urlopen(monkeypatch, healthy_payload(
        status="live", sourceStatus="waiting"))
    health = probe_health("http://127.0.0.1:8111", 1.0)
    assert health.source_status == "live"


def test_legacy_source_status_still_fills_the_card(monkeypatch):
    patch_urlopen(monkeypatch, {
        "ok": True,
        "serviceId": SERVICE_ID,
        "apiVersion": "1.0",
        "instanceId": "inst-a",
        "sourceStatus": "stale",
    })
    health = probe_health("http://127.0.0.1:8111", 1.0)
    assert health.source_status == "stale"


def test_published_8111_for_wows_identity_is_recognized(monkeypatch):
    patch_urlopen(
        monkeypatch,
        healthy_payload(serviceId="8111_for_wows"),
    )

    health = probe_health("http://127.0.0.1:8111", 1.0)

    assert health.usable is True


def test_a_foreign_service_on_the_port_is_not_ours(monkeypatch):
    """War Thunder's telemetry lives on 8111 too and answers nothing like ours."""
    patch_urlopen(monkeypatch, {"valid": True, "type": "F-16"})
    health = probe_health("http://127.0.0.1:8111", 1.0)
    assert health.reachable is True
    assert health.usable is False
    assert "unidentified" in health.error


def test_a_differently_named_service_is_reported_by_name(monkeypatch):
    patch_urlopen(monkeypatch, healthy_payload(serviceId="some-other-bridge"))
    health = probe_health("http://127.0.0.1:8111", 1.0)
    assert health.usable is False
    assert "some-other-bridge" in health.error


def test_an_unsupported_major_version_is_refused(monkeypatch):
    patch_urlopen(monkeypatch, healthy_payload(apiVersion="2.0"))
    health = probe_health("http://127.0.0.1:8111", 1.0)
    assert health.usable is False
    assert "unsupported apiVersion" in health.error


def test_an_unreachable_port_is_not_an_error_state(monkeypatch):
    patch_urlopen(monkeypatch, error=urllib.error.URLError("refused"))
    health = probe_health("http://127.0.0.1:8111", 1.0)
    assert health.reachable is False
    assert health.usable is False


def test_an_http_error_from_healthz_is_a_reachable_conflict(monkeypatch):
    """A 404 still proves the address is occupied; do not treat it as empty."""
    patch_urlopen(monkeypatch, error=healthz_http_error())
    health = probe_health("http://127.0.0.1:8111", 1.0)
    assert health.reachable is True
    assert health.ours is False
    assert health.usable is False
    assert health.conflict_cause == sm.CONFLICT_CAUSE_PORT


def test_garbage_response_does_not_raise(monkeypatch):
    monkeypatch.setattr(
        sm.urllib.request, "urlopen", lambda *a, **k: GarbageResponse())
    health = probe_health("http://127.0.0.1:8111", 1.0)
    assert health.reachable is True
    assert health.ours is False
    assert health.usable is False
    assert health.conflict_cause == sm.CONFLICT_CAUSE_PORT


# --- start_if_needed ----------------------------------------------------

def test_an_already_healthy_service_is_reused_not_relaunched(monkeypatch):
    patch_urlopen(monkeypatch, healthy_payload())
    launched = []
    monkeypatch.setattr(
        sm.subprocess, "Popen",
        lambda *a, **k: launched.append(a) or pytest.fail("must not launch"))

    manager = WowsServiceManager(cfg(service_source_dir="D:/8111_for_wows"))
    status = manager.start_if_needed()
    assert status.mode == MODE_EXTERNAL
    assert launched == []


def test_a_foreign_service_blocks_both_launch_and_shutdown(monkeypatch):
    patch_urlopen(monkeypatch, {"valid": True})
    monkeypatch.setattr(
        sm.subprocess, "Popen",
        lambda *a, **k: pytest.fail("must not launch onto a busy port"))

    manager = WowsServiceManager(cfg(service_source_dir="D:/8111_for_wows"))
    status = manager.start_if_needed()
    assert status.mode == MODE_CONFLICT
    assert "unidentified service" in status.detail
    assert "not stopping anything" in status.detail

    # And stopping must be a no-op: we own nothing here.
    stopped = manager.stop()
    assert "nothing to stop" in stopped.detail


def test_an_http_error_from_healthz_blocks_launch_and_transport(monkeypatch):
    patch_urlopen(monkeypatch, error=healthz_http_error())
    monkeypatch.setattr(
        sm.subprocess, "Popen",
        lambda *a, **k: pytest.fail("must not launch onto a busy port"))

    manager = WowsServiceManager(cfg(service_source_dir="D:/8111_for_wows"))
    status = manager.start_if_needed()
    assert status.mode == MODE_CONFLICT
    assert status.transport_allowed is False
    assert "unidentified service" in status.detail
    assert "not stopping anything" in status.detail


def test_malformed_healthz_json_blocks_launch_and_transport(monkeypatch):
    monkeypatch.setattr(
        sm.urllib.request, "urlopen", lambda *a, **k: GarbageResponse())
    monkeypatch.setattr(
        sm.subprocess, "Popen",
        lambda *a, **k: pytest.fail("must not launch onto a busy port"))

    manager = WowsServiceManager(cfg(service_source_dir="D:/8111_for_wows"))
    status = manager.start_if_needed()
    assert status.mode == MODE_CONFLICT
    assert status.transport_allowed is False
    assert "unidentified service" in status.detail


def test_identity_mismatch_detail_does_not_claim_the_port_is_busy(monkeypatch):
    patch_urlopen(
        monkeypatch,
        healthy_payload(serviceId="8111-for-wows"),
    )
    manager = WowsServiceManager(cfg(service_source_dir="D:/8111_for_wows"))

    status = manager.start_if_needed()

    assert status.mode == MODE_CONFLICT
    assert "serviceId mismatch" in status.detail
    assert "port busy" not in status.detail


def test_api_version_mismatch_detail_does_not_claim_the_port_is_busy(monkeypatch):
    patch_urlopen(monkeypatch, healthy_payload(apiVersion="2.0"))
    manager = WowsServiceManager(cfg(service_source_dir="D:/8111_for_wows"))

    status = manager.start_if_needed()

    assert status.mode == MODE_CONFLICT
    assert "unsupported apiVersion: 2.0" in status.detail
    assert "port busy" not in status.detail


def test_a_conflicting_service_is_not_safe_for_transport():
    status = ServiceStatus(
        mode=MODE_CONFLICT,
        health=ServiceHealth(reachable=True, ours=False),
    )
    assert status.transport_allowed is False


def test_identity_mismatch_conflict_gets_an_identity_hint():
    status = ServiceStatus(
        mode=MODE_CONFLICT,
        health=ServiceHealth(
            reachable=True,
            ours=False,
            service_id="8111-for-wows",
            api_version="1.0",
            error="foreign service: 8111-for-wows",
        ),
    )

    assert _mod_hint(status, {}) == "identity_mismatch"


def test_supported_identity_with_wrong_api_major_gets_a_version_hint():
    status = ServiceStatus(
        mode=MODE_CONFLICT,
        health=ServiceHealth(
            reachable=True,
            ours=False,
            service_id=SERVICE_ID,
            api_version="2.0",
            error="unsupported apiVersion: 2.0",
        ),
    )

    assert _mod_hint(status, {}) == "api_version_mismatch"


def test_unidentified_service_conflict_keeps_the_port_hint():
    status = ServiceStatus(
        mode=MODE_CONFLICT,
        health=ServiceHealth(
            reachable=True,
            ours=False,
            error="unidentified service on this port",
        ),
    )

    assert _mod_hint(status, {}) == "port_conflict"


@pytest.mark.parametrize("mode", [MODE_EXTERNAL, MODE_MANAGED, MODE_OFFLINE, MODE_DISABLED])
def test_non_conflicting_service_states_allow_transport_supervision(mode):
    assert ServiceStatus(mode=mode).transport_allowed is True


def test_plugin_does_not_start_transport_for_a_conflicting_service():
    starts = []
    plugin = object.__new__(NekoWowsPlugin)
    plugin._state_lock = threading.RLock()
    plugin.transport = type("Transport", (), {"start": lambda _self: starts.append(1)})()
    plugin._running = False
    plugin._reconnect_required = False
    status = ServiceStatus(
        mode=MODE_CONFLICT,
        health=ServiceHealth(reachable=True, ours=False),
    )

    assert NekoWowsPlugin._activate_transport(plugin, status) is False
    assert starts == []
    assert plugin._running is False
    assert plugin._reconnect_required is True


def test_config_change_keeps_reconnect_required_after_conflict_blocked_start():
    plugin = object.__new__(NekoWowsPlugin)
    plugin._state_lock = threading.RLock()
    plugin._running = False
    plugin._reconnect_required = True
    plugin.cfg = WowsConfig()

    async def reload_config():
        cfg = WowsConfig()
        cfg.service_url = "http://127.0.0.1:18111"
        plugin.cfg = cfg
        return cfg

    plugin._reload_config = reload_config

    result = asyncio.run(NekoWowsPlugin.on_config_change(plugin))

    assert result.is_ok()
    assert plugin._reconnect_required is True


def test_config_change_requires_reconnect_when_websocket_preference_changes():
    """_run() creates the WebSocket task once; a live session cannot flip it."""
    plugin = object.__new__(NekoWowsPlugin)
    plugin._state_lock = threading.RLock()
    plugin._running = True
    plugin._reconnect_required = False
    plugin.cfg = WowsConfig()
    assert plugin.cfg.transport_prefer_ws is True

    async def reload_config():
        cfg = WowsConfig()
        cfg.transport_prefer_ws = False
        plugin.cfg = cfg
        return cfg

    plugin._reload_config = reload_config

    result = asyncio.run(NekoWowsPlugin.on_config_change(plugin))

    assert result.is_ok()
    assert result.unwrap()["reconnect_required"] is True
    assert plugin._reconnect_required is True


def test_config_change_requires_reconnect_when_http_timeout_changes():
    """_run() binds ClientTimeout when the transport session is created."""
    plugin = object.__new__(NekoWowsPlugin)
    plugin._state_lock = threading.RLock()
    plugin._running = True
    plugin._reconnect_required = False
    plugin.cfg = WowsConfig()
    assert plugin.cfg.http_timeout_seconds == 1.5

    async def reload_config():
        cfg = WowsConfig()
        cfg.http_timeout_seconds = 2.5
        plugin.cfg = cfg
        return cfg

    plugin._reload_config = reload_config

    result = asyncio.run(NekoWowsPlugin.on_config_change(plugin))

    assert result.is_ok()
    assert result.unwrap()["reconnect_required"] is True
    assert plugin._reconnect_required is True


def test_config_change_stops_output_when_the_plugin_is_disabled():
    calls = []
    stop_threads = {}
    event_loop_thread = threading.get_ident()
    plugin = object.__new__(NekoWowsPlugin)
    plugin._state_lock = threading.RLock()
    plugin._pipeline_lock = threading.Lock()
    plugin._running = True
    plugin._reconnect_required = False
    plugin._latest = ("live-frame",)
    plugin._previous = ("previous-frame",)
    plugin.cfg = WowsConfig()

    def record(name):
        calls.append((name, plugin._pipeline_lock.locked()))
        if name in {"transport_stop", "service_stop"}:
            stop_threads[name] = threading.get_ident()

    async def reload_config():
        cfg = WowsConfig()
        cfg.enabled = False
        plugin.cfg = cfg
        return cfg

    plugin._reload_config = reload_config
    plugin.transport = type("Transport", (), {
        "stop": lambda _self: record("transport_stop"),
    })()
    plugin.service = type("Service", (), {
        "stop": lambda _self: record("service_stop"),
    })()
    plugin.dispatcher = type("Dispatcher", (), {
        "pause": lambda _self, _reason: record("dispatcher_pause"),
    })()
    plugin.arbiter = type("Arbiter", (), {
        "pause": lambda _self: record("arbiter_pause"),
    })()
    plugin.context_injector = type("Injector", (), {
        "restore": lambda _self, *_a, **_k: record("restore"),
    })()
    plugin.ship_context = type("Ctx", (), {
        "reset": lambda _self, *_a: record("ship_reset"),
    })()
    plugin.timeline = type("Timeline", (), {
        "record": lambda _self, *_a, **_k: record("timeline"),
    })()

    result = asyncio.run(NekoWowsPlugin.on_config_change(plugin))

    assert plugin._running is False
    assert plugin._latest is None
    assert plugin._previous is None
    assert calls == [
        ("transport_stop", False),
        ("service_stop", False),
        ("dispatcher_pause", True),
        ("arbiter_pause", True),
        ("restore", True),
        ("ship_reset", True),
        ("timeline", True),
    ]
    assert set(stop_threads) == {"transport_stop", "service_stop"}
    assert all(thread_id != event_loop_thread
               for thread_id in stop_threads.values())
    assert result.unwrap()["status"] == "disabled"


def test_config_change_restarts_output_when_the_plugin_is_re_enabled():
    calls = []
    plugin = object.__new__(NekoWowsPlugin)
    plugin._state_lock = threading.RLock()
    plugin._pipeline_lock = threading.Lock()
    plugin._running = False
    plugin._reconnect_required = False
    plugin._previous = None
    plugin._blocked_signature = ()
    plugin.cfg = WowsConfig()
    plugin.cfg.enabled = False

    def record(name):
        calls.append((name, plugin._pipeline_lock.locked()))

    async def reload_config():
        cfg = WowsConfig()
        cfg.enabled = True
        plugin.cfg = cfg
        return cfg

    status = ServiceStatus(mode=MODE_EXTERNAL)
    plugin._reload_config = reload_config
    plugin.transport = type("Transport", (), {
        "stop": lambda _self: record("transport_stop"),
        "start": lambda _self: record("transport_start"),
    })()
    plugin.service = type("Service", (), {
        "stop": lambda _self: record("service_stop"),
        "start_if_needed": lambda _self: record("service_start") or status,
    })()
    plugin.dispatcher = type("Dispatcher", (), {
        "pause": lambda _self, _reason: record("dispatcher_pause"),
        "resume": lambda _self: record("dispatcher_resume"),
    })()
    plugin.arbiter = type("Arbiter", (), {
        "pause": lambda _self: record("arbiter_pause"),
        "resume": lambda _self: record("arbiter_resume"),
        "reset_battle": lambda _self, *_a: record("arbiter_reset_battle"),
    })()
    plugin.gate = type("Gate", (), {"reset": lambda _self: record("gate_reset")})()
    plugin.registry = type("Registry", (), {
        "reset": lambda _self: record("registry_reset"),
    })()
    plugin.ship_context = type("Ctx", (), {
        "reset": lambda _self, *_a: record("ship_reset"),
    })()
    plugin._record_service = lambda _status: record("record_service")

    result = asyncio.run(NekoWowsPlugin.on_config_change(plugin))

    assert plugin._running is True
    assert ("dispatcher_resume", True) in calls
    assert ("arbiter_resume", True) in calls
    assert ("service_start", False) in calls
    assert ("transport_start", False) in calls
    assert calls.index(("dispatcher_resume", True)) < calls.index(
        ("transport_start", False))
    assert result.unwrap()["transport_started"] is True


def test_reconnect_resets_pipeline_and_battle_state_before_transport_restart():
    calls = []
    event_loop_thread = threading.get_ident()
    transport_stop_threads = []
    plugin = object.__new__(NekoWowsPlugin)
    plugin._state_lock = threading.RLock()
    plugin._pipeline_lock = threading.Lock()
    plugin._running = False
    plugin._reconnect_required = True
    plugin._previous = ("old-frame",)
    plugin._blocked_signature = (("old", ("battle",)),)

    def record(name, *args):
        if name == "transport_stop":
            transport_stop_threads.append(threading.get_ident())
        calls.append((name, args, plugin._pipeline_lock.locked()))

    class ResetProbe:
        def __init__(self, name):
            self.name = name

        def reset(self, *args):
            record(self.name, *args)

        def reset_battle(self, *args):
            record(self.name, *args)

    status = ServiceStatus(mode=MODE_EXTERNAL)
    plugin.transport = type("Transport", (), {
        "stop": lambda _self: record("transport_stop"),
        "start": lambda _self: record("transport_start"),
    })()
    plugin.service = type("Service", (), {
        "stop": lambda _self: record("service_stop"),
        "start_if_needed": lambda _self: record("service_start") or status,
    })()
    plugin.gate = ResetProbe("gate_reset")
    plugin.registry = ResetProbe("registry_reset")
    plugin.ship_context = ResetProbe("ship_context_reset")
    plugin.arbiter = ResetProbe("arbiter_reset_battle")
    plugin._record_service = lambda _status: record("record_service")

    result = asyncio.run(NekoWowsPlugin.reconnect(plugin))

    assert calls == [
        ("transport_stop", (), False),
        ("service_stop", (), False),
        ("service_start", (), False),
        ("gate_reset", (), True),
        ("registry_reset", (), True),
        ("ship_context_reset", ("reconnect",), True),
        ("arbiter_reset_battle", (None,), True),
        ("transport_start", (), False),
        ("record_service", (), False),
    ]
    assert plugin._blocked_signature == ()
    assert plugin._previous is None
    assert plugin._running is True
    assert plugin._reconnect_required is False
    assert transport_stop_threads[0] != event_loop_thread
    assert result.unwrap() == {
        "service": status.as_dict(),
        "transport_started": True,
    }


def _plugin_for_reconnect(service):
    plugin = object.__new__(NekoWowsPlugin)
    plugin._state_lock = threading.RLock()
    plugin._pipeline_lock = threading.Lock()
    plugin._running = True
    plugin._reconnect_required = True
    plugin._previous = None
    plugin._blocked_signature = ()
    plugin.transport = type("Transport", (), {
        "stop": lambda _self: None,
        "start": lambda _self: None,
    })()
    plugin.service = service
    plugin.gate = type("Gate", (), {"reset": lambda _self: None})()
    plugin.registry = type("Registry", (), {"reset": lambda _self: None})()
    plugin.ship_context = type("Ctx", (), {"reset": lambda _self, *_a: None})()
    plugin.arbiter = type("Arbiter", (), {"reset_battle": lambda _self, *_a: None})()
    plugin._record_service = lambda _status: None
    return plugin


def test_reconnect_stops_a_managed_child_so_new_game_dir_takes_effect(
        monkeypatch, tmp_path):
    source = prepare_source(tmp_path)
    original = FakeProcess()
    replacement = FakeProcess()
    replacement.pid = 4343
    launches = []

    def fake_urlopen(url, timeout=None):
        if original.poll() is None and not launches:
            return FakeResponse(healthy_payload())
        if launches:
            return FakeResponse(healthy_payload(instanceId="inst-b"))
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(sm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        sm.subprocess, "Popen",
        lambda command, **_k: launches.append(command) or replacement)
    monkeypatch.setattr(sm, "_which_uv", lambda: "uv")

    manager = WowsServiceManager(cfg(
        service_source_dir=str(source), game_dir="D:/Games/Old"))
    manager._process = original
    manager._owned_instance_id = "inst-a"
    manager.apply_config(cfg(
        service_source_dir=str(source), game_dir="D:/Games/New"))

    result = asyncio.run(NekoWowsPlugin.reconnect(_plugin_for_reconnect(manager)))

    assert original.terminated is True
    assert launches, "must relaunch so the new game_dir can take effect"
    assert "--game-dir" in launches[0] and "D:/Games/New" in launches[0]
    assert manager._process is replacement
    assert result.unwrap()["transport_started"] is True


def test_reconnect_stops_a_managed_child_before_launching_on_a_new_url(
        monkeypatch, tmp_path):
    source = prepare_source(tmp_path)
    original = FakeProcess()
    replacement = FakeProcess()
    replacement.pid = 4343
    launches = []

    def fake_urlopen(url, timeout=None):
        if ":18111" in url:
            if launches:
                return FakeResponse(healthy_payload(instanceId="inst-b"))
            raise urllib.error.URLError("refused")
        if original.poll() is None:
            return FakeResponse(healthy_payload())
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(sm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        sm.subprocess, "Popen",
        lambda command, **_k: launches.append(command) or replacement)
    monkeypatch.setattr(sm, "_which_uv", lambda: "uv")

    manager = WowsServiceManager(cfg(service_source_dir=str(source)))
    manager._process = original
    manager._owned_instance_id = "inst-a"
    manager.apply_config(cfg(
        service_source_dir=str(source),
        service_url="http://127.0.0.1:18111",
    ))

    result = asyncio.run(NekoWowsPlugin.reconnect(_plugin_for_reconnect(manager)))

    assert original.terminated is True
    assert launches, "must launch on the new URL instead of overwriting a live child"
    assert "--port" in launches[0] and "18111" in launches[0]
    assert manager._process is replacement
    assert result.unwrap()["transport_started"] is True


def test_auto_start_disabled_is_reported_not_attempted(monkeypatch):
    patch_urlopen(monkeypatch, error=urllib.error.URLError("refused"))
    monkeypatch.setattr(
        sm.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))

    manager = WowsServiceManager(cfg(service_auto_start=False))
    status = manager.start_if_needed()
    assert status.mode == MODE_DISABLED


def test_no_source_directory_means_no_launch(monkeypatch):
    patch_urlopen(monkeypatch, error=urllib.error.URLError("refused"))
    monkeypatch.setattr(
        sm.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))

    manager = WowsServiceManager(cfg(service_source_dir=""))
    status = manager.start_if_needed()
    assert status.mode == MODE_OFFLINE
    assert "service_source_dir is empty" in status.detail


def test_a_remote_address_is_never_auto_managed(monkeypatch):
    patch_urlopen(monkeypatch, error=urllib.error.URLError("refused"))
    monkeypatch.setattr(
        sm.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))

    manager = WowsServiceManager(cfg(
        service_url="http://192.168.1.20:8111",
        service_source_dir="D:/8111_for_wows",
    ))
    status = manager.start_if_needed()
    assert "non-loopback" in status.detail


def test_a_malformed_service_url_is_reported_instead_of_raising(monkeypatch, tmp_path):
    """`urlparse(...).port` raises on a typo; startup must survive it."""
    source = prepare_source(tmp_path)
    patch_urlopen(monkeypatch, error=urllib.error.URLError("refused"))
    monkeypatch.setattr(
        sm.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))
    monkeypatch.setattr(sm, "_which_uv", lambda: "uv")

    manager = WowsServiceManager(cfg(
        service_url="http://127.0.0.1:not-a-port", service_source_dir=str(source)))
    status = manager.start_if_needed()
    assert "not a usable address" in status.detail

    # And the command builder, the code that actually reads the port, is safe
    # on its own: it drops `--port` rather than blowing up.
    command = manager._build_command(source, source / "server" / "server.py")
    assert "--port" not in command


def test_missing_server_script_is_a_failed_start(monkeypatch, tmp_path):
    patch_urlopen(monkeypatch, error=urllib.error.URLError("refused"))
    monkeypatch.setattr(
        sm.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))

    manager = WowsServiceManager(cfg(service_source_dir=str(tmp_path)))
    status = manager.start_if_needed()
    assert "server.py not found" in status.detail
    assert status.crash_count == 1


# --- launching ----------------------------------------------------------

class FakeProcess:
    def __init__(self, *, exit_code=None):
        self.pid = 4242
        self._exit_code = exit_code
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._exit_code

    def terminate(self):
        self.terminated = True
        self._exit_code = 0

    def wait(self, timeout=None):
        return self._exit_code

    def kill(self):
        self.killed = True


def prepare_source(tmp_path):
    server_dir = tmp_path / "server"
    server_dir.mkdir(parents=True, exist_ok=True)
    (server_dir / "server.py").write_text("# stub", encoding="utf-8")
    return tmp_path


def test_a_healthy_launch_becomes_the_managed_service(monkeypatch, tmp_path):
    source = prepare_source(tmp_path)
    states = iter([None, healthy_payload()])
    payloads = {"current": None}

    def fake_urlopen(url, timeout=None):
        try:
            payloads["current"] = next(states)
        except StopIteration:
            pass
        if payloads["current"] is None:
            raise urllib.error.URLError("refused")
        return FakeResponse(payloads["current"])

    monkeypatch.setattr(sm.urllib.request, "urlopen", fake_urlopen)
    process = FakeProcess()
    recorded = {}

    def fake_popen(command, **kwargs):
        recorded["command"] = command
        recorded["cwd"] = kwargs.get("cwd")
        return process

    monkeypatch.setattr(sm.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(sm, "_which_uv", lambda: "uv")

    manager = WowsServiceManager(cfg(
        service_source_dir=str(source), game_dir="D:/Games/World_of_Warships"))
    status = manager.start_if_needed()

    assert status.mode == MODE_MANAGED
    assert status.pid == 4242
    assert status.owned_instance_id == "inst-a"
    assert "server/server.py" in recorded["command"]
    assert "--port" in recorded["command"] and "8111" in recorded["command"]
    assert "--game-dir" in recorded["command"]


def test_the_project_venv_interpreter_is_preferred(monkeypatch, tmp_path):
    source = prepare_source(tmp_path)
    venv = source / ".venv" / "Scripts"
    venv.mkdir(parents=True)
    interpreter = venv / "python.exe"
    interpreter.write_text("", encoding="utf-8")

    patch_urlopen(monkeypatch, error=urllib.error.URLError("refused"))
    monkeypatch.setattr(sm, "_which_uv", lambda: "uv")
    manager = WowsServiceManager(cfg(service_source_dir=str(source)))
    command = manager._build_command(source, source / "server" / "server.py")
    assert command[0] == str(interpreter)


def test_uv_is_the_fallback_when_no_venv_exists(monkeypatch, tmp_path):
    source = prepare_source(tmp_path)
    patch_urlopen(monkeypatch, error=urllib.error.URLError("refused"))
    monkeypatch.setattr(sm, "_which_uv", lambda: "uv")
    manager = WowsServiceManager(cfg(service_source_dir=str(source)))
    command = manager._build_command(source, source / "server" / "server.py")
    assert command[:3] == ["uv", "run", "python"]


def test_a_startup_timeout_is_cleaned_up_and_counted(monkeypatch, tmp_path):
    source = prepare_source(tmp_path)
    patch_urlopen(monkeypatch, error=urllib.error.URLError("refused"))
    process = FakeProcess()
    monkeypatch.setattr(sm.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(sm, "_which_uv", lambda: "uv")
    skip_startup_wait(monkeypatch)

    manager = WowsServiceManager(cfg(
        service_source_dir=str(source), service_startup_timeout_seconds=1.0))
    status = manager.start_if_needed()
    assert status.mode != MODE_MANAGED
    assert process.terminated is True
    assert status.crash_count == 1


def test_a_failed_popen_closes_the_service_log_handle(monkeypatch, tmp_path):
    """Popen OSError must close the locally opened log; _close_log never sees it."""
    source = prepare_source(tmp_path)
    log_dir = tmp_path / "logs"
    opened = {}
    real_open_log = WowsServiceManager._open_log

    def tracking_open_log(self):
        handle = real_open_log(self)
        opened["handle"] = handle
        return handle

    def fail_popen(*args, **kwargs):
        raise OSError("simulated spawn failure")

    patch_urlopen(monkeypatch, error=urllib.error.URLError("refused"))
    monkeypatch.setattr(WowsServiceManager, "_open_log", tracking_open_log)
    monkeypatch.setattr(sm.subprocess, "Popen", fail_popen)
    monkeypatch.setattr(sm, "_which_uv", lambda: "uv")

    manager = WowsServiceManager(
        cfg(service_source_dir=str(source)),
        log_dir=log_dir,
    )
    status = manager.start_if_needed()

    assert "launch failed" in status.detail
    assert manager._log_handle is None
    handle = opened["handle"]
    assert handle is not None
    assert handle.closed is True


def test_a_successful_launch_owns_the_service_log_handle(monkeypatch, tmp_path):
    source = prepare_source(tmp_path)
    log_dir = tmp_path / "logs"
    states = iter([None, healthy_payload()])
    payloads = {"current": None}

    def fake_urlopen(url, timeout=None):
        try:
            payloads["current"] = next(states)
        except StopIteration:
            pass
        if payloads["current"] is None:
            raise urllib.error.URLError("refused")
        return FakeResponse(payloads["current"])

    monkeypatch.setattr(sm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sm.subprocess, "Popen", lambda *a, **k: FakeProcess())
    monkeypatch.setattr(sm, "_which_uv", lambda: "uv")

    manager = WowsServiceManager(
        cfg(service_source_dir=str(source)),
        log_dir=log_dir,
    )
    status = manager.start_if_needed()

    assert status.mode == MODE_MANAGED
    assert manager._log_handle is not None
    assert manager._log_handle.closed is False
    manager.stop()
    assert manager._log_handle is None


def test_repeated_failed_starts_pause_auto_management(monkeypatch, tmp_path):
    source = prepare_source(tmp_path)
    patch_urlopen(monkeypatch, error=urllib.error.URLError("refused"))
    monkeypatch.setattr(
        sm.subprocess, "Popen", lambda *a, **k: FakeProcess(exit_code=1))
    monkeypatch.setattr(sm, "_which_uv", lambda: "uv")
    skip_startup_wait(monkeypatch)

    manager = WowsServiceManager(cfg(
        service_source_dir=str(source), service_startup_timeout_seconds=1.0))
    for _ in range(3):
        manager.start_if_needed()

    status = manager.snapshot()
    assert status.paused is True
    assert "paused after" in status.detail

    manager.resume()
    assert manager.snapshot().paused is False


def test_backoff_grows_then_holds(monkeypatch, tmp_path):
    manager = WowsServiceManager(cfg())
    delays = []
    for _ in range(4):
        delays.append(manager.backoff_seconds())
        manager.note_crash()
    assert delays == [1.0, 2.0, 4.0, 4.0]


# --- supervision --------------------------------------------------------

def freeze_clock(monkeypatch, now):
    patch_module_clock(
        monkeypatch, sm, monotonic=lambda: now["t"], sleep=lambda _seconds: None)


def test_a_stale_reap_does_not_clear_a_replacement_process():
    manager = WowsServiceManager(cfg())
    original = FakeProcess(exit_code=1)
    replacement = FakeProcess()
    replacement_log = object()
    manager._process = replacement
    manager._owned_instance_id = "inst-b"
    manager._log_handle = replacement_log

    reaped = manager._reap(original, 1)

    assert reaped is False
    assert manager._process is replacement
    assert manager._owned_instance_id == "inst-b"
    assert manager._log_handle is replacement_log
    assert manager.snapshot().crash_count == 0


def test_direct_start_relaunches_when_owned_process_exits_during_health(
        monkeypatch, tmp_path):
    source = prepare_source(tmp_path)
    original = FakeProcess()
    replacement = FakeProcess()
    probes = 0
    launches = []

    def fake_urlopen(url, timeout=None):
        nonlocal probes
        probes += 1
        if probes == 1:
            original._exit_code = 7
            raise urllib.error.URLError("refused")
        return FakeResponse(healthy_payload(instanceId="inst-b"))

    monkeypatch.setattr(sm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        sm.subprocess, "Popen", lambda *a, **k: launches.append(a) or replacement)
    monkeypatch.setattr(sm, "_which_uv", lambda: "uv")

    manager = WowsServiceManager(cfg(service_source_dir=str(source)))
    manager._process = original
    manager._owned_instance_id = "inst-a"

    status = manager.start_if_needed()

    assert len(launches) == 1
    assert manager._process is replacement
    assert status.mode == MODE_MANAGED
    assert status.owned_instance_id == "inst-b"
    assert status.crash_count == 1


def test_a_managed_service_that_dies_mid_battle_is_relaunched(monkeypatch, tmp_path):
    source = prepare_source(tmp_path)
    now = {"t": 1000.0}
    freeze_clock(monkeypatch, now)
    launches = []

    def fake_urlopen(url, timeout=None):
        if not launches:
            raise urllib.error.URLError("refused")
        return FakeResponse(healthy_payload(instanceId="inst-b"))

    monkeypatch.setattr(sm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        sm.subprocess, "Popen",
        lambda *a, **k: launches.append(a) or FakeProcess())
    monkeypatch.setattr(sm, "_which_uv", lambda: "uv")

    manager = WowsServiceManager(cfg(service_source_dir=str(source)))
    manager._process = FakeProcess(exit_code=1)
    manager._owned_instance_id = "inst-a"

    # First pass only notices the exit: the backoff owns the retry.
    noticed = manager.supervise()
    assert launches == []
    assert "exited unexpectedly" in noticed.detail
    assert noticed.crash_count == 1

    now["t"] += 30.0
    recovered = manager.supervise()
    assert len(launches) == 1
    assert recovered.mode == MODE_MANAGED
    assert recovered.owned_instance_id == "inst-b"


def test_an_alive_managed_service_with_failed_health_is_not_relaunched(
        monkeypatch, tmp_path):
    source = prepare_source(tmp_path)
    patch_urlopen(monkeypatch, error=urllib.error.URLError("refused"))
    monkeypatch.setattr(
        sm.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))
    monkeypatch.setattr(sm, "_which_uv", lambda: "uv")

    manager = WowsServiceManager(cfg(service_source_dir=str(source)))
    process = FakeProcess()
    manager._process = process
    manager._owned_instance_id = "inst-a"

    status = manager.supervise()

    assert manager._process is process
    assert status.mode == MODE_OFFLINE
    assert status.pid == process.pid
    assert status.owned_instance_id == "inst-a"
    assert "still running" in status.detail
    assert status.crash_count == 0


def test_a_managed_service_exiting_during_failed_health_is_reaped_not_relaunched(
        monkeypatch, tmp_path):
    source = prepare_source(tmp_path)
    process = FakeProcess()

    def fake_urlopen(url, timeout=None):
        process._exit_code = 7
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(sm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        sm.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))
    monkeypatch.setattr(sm, "_which_uv", lambda: "uv")

    manager = WowsServiceManager(cfg(service_source_dir=str(source)))
    manager._process = process
    manager._owned_instance_id = "inst-a"

    status = manager.supervise()

    assert manager._process is None
    assert status.mode == MODE_OFFLINE
    assert status.pid is None
    assert status.owned_instance_id == ""
    assert "exited unexpectedly (code=7)" in status.detail
    assert status.crash_count == 1


def test_a_healthy_service_is_left_alone_by_supervision(monkeypatch, tmp_path):
    source = prepare_source(tmp_path)
    patch_urlopen(monkeypatch, healthy_payload())
    monkeypatch.setattr(
        sm.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))

    manager = WowsServiceManager(cfg(service_source_dir=str(source)))
    status = manager.supervise()
    assert status.mode == MODE_EXTERNAL
    assert status.crash_count == 0


def test_a_service_that_keeps_dying_stops_being_relaunched(monkeypatch, tmp_path):
    source = prepare_source(tmp_path)
    now = {"t": 1000.0}
    freeze_clock(monkeypatch, now)
    patch_urlopen(monkeypatch, error=urllib.error.URLError("refused"))
    launches = []
    monkeypatch.setattr(
        sm.subprocess, "Popen",
        lambda *a, **k: launches.append(a) or FakeProcess(exit_code=1))
    monkeypatch.setattr(sm, "_which_uv", lambda: "uv")

    manager = WowsServiceManager(cfg(service_source_dir=str(source)))
    manager._process = FakeProcess(exit_code=1)
    for _ in range(6):
        manager.supervise()
        now["t"] += 10.0

    status = manager.snapshot()
    assert status.paused is True
    settled = len(launches)

    now["t"] += 60.0
    manager.supervise()
    assert len(launches) == settled, "a paused manager must not relaunch"

    # The user can still overrule the fuse.
    manager.resume()
    manager.supervise()
    assert len(launches) == settled + 1


# --- stopping -----------------------------------------------------------

def test_stopping_terminates_only_our_own_process(monkeypatch, tmp_path):
    source = prepare_source(tmp_path)
    patch_urlopen(monkeypatch, healthy_payload())
    process = FakeProcess()
    monkeypatch.setattr(sm.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(sm, "_which_uv", lambda: "uv")

    manager = WowsServiceManager(cfg(service_source_dir=str(source)))
    # Force the managed path even though the probe already answers.
    manager._process = process
    manager._owned_instance_id = "inst-a"

    manager.stop()
    assert process.terminated is True


def test_a_changed_instance_id_stops_us_from_terminating(monkeypatch, tmp_path):
    """Our child is alive but a different service now answers the port."""
    patch_urlopen(monkeypatch, healthy_payload(instanceId="someone-else"))
    process = FakeProcess()

    manager = WowsServiceManager(cfg())
    manager._process = process
    manager._owned_instance_id = "inst-a"

    status = manager.stop()
    assert process.terminated is False
    assert "instance mismatch" in status.detail


def test_a_stubborn_process_is_killed(monkeypatch):
    class Stubborn(FakeProcess):
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="server.py", timeout=timeout)

    patch_urlopen(monkeypatch, healthy_payload())
    process = Stubborn()
    manager = WowsServiceManager(cfg())
    manager._process = process
    manager._owned_instance_id = "inst-a"

    manager.stop()
    assert process.killed is True


def test_stopping_an_already_dead_child_is_harmless(monkeypatch):
    patch_urlopen(monkeypatch, error=urllib.error.URLError("refused"))
    manager = WowsServiceManager(cfg())
    manager._process = FakeProcess(exit_code=0)
    status = manager.stop()
    assert "already exited" in status.detail
