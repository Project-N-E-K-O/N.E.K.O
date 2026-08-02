"""Service supervision: identity before action, and never kill someone else's."""

from __future__ import annotations

import json
import subprocess
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
    WowsServiceManager,
    api_major,
    is_loopback_url,
    is_usable_service_url,
    probe_health,
)
from plugin.plugins.neko_wows.domain.contracts import WowsConfig


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


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
        "sourceStatus": "waiting",
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


def test_garbage_response_does_not_raise(monkeypatch):
    def fake_urlopen(url, timeout=None):
        class Broken:
            def read(self):
                return b"<html>not json</html>"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Broken()

    monkeypatch.setattr(sm.urllib.request, "urlopen", fake_urlopen)
    assert probe_health("http://127.0.0.1:8111", 1.0).usable is False


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
    assert "not stopping anything" in status.detail

    # And stopping must be a no-op: we own nothing here.
    stopped = manager.stop()
    assert "nothing to stop" in stopped.detail


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
