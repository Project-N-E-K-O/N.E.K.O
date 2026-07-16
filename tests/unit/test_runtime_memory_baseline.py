from __future__ import annotations

import signal
from email.message import Message
from types import SimpleNamespace

import pytest

from scripts import runtime_memory_baseline as baseline


@pytest.mark.unit
def test_service_health_requires_roles_and_one_instance(monkeypatch):
    payloads = {
        43111: {
            "app": baseline.HEALTH_APP_SIGNATURE,
            "status": "ok",
            "service": "main",
            "instance_id": "same",
        },
        43112: {
            "app": baseline.HEALTH_APP_SIGNATURE,
            "status": "ok",
            "service": "memory",
            "instance_id": "same",
        },
        43115: {
            "app": baseline.HEALTH_APP_SIGNATURE,
            "status": "ok",
            "service": "agent",
            "instance_id": "same",
        },
    }
    monkeypatch.setattr(baseline, "_probe_health", lambda port: payloads.get(port))

    ready, state = baseline._service_health_state(
        [43111, 43112, 43115],
        ["main", "memory", "agent"],
    )
    assert ready is True
    assert all(item["ready"] for item in state.values())

    payloads[43115] = {**payloads[43115], "instance_id": "other"}
    ready, _state = baseline._service_health_state(
        [43111, 43112, 43115],
        ["main", "memory", "agent"],
    )
    assert ready is False

    payloads[43115] = {**payloads[43115], "instance_id": "same", "service": "main"}
    ready, state = baseline._service_health_state(
        [43111, 43112, 43115],
        ["main", "memory", "agent"],
    )
    assert ready is False
    assert state["agent"]["actual_service"] == "main"


@pytest.mark.unit
def test_metadata_records_effective_sampling_and_topology(monkeypatch):
    provenance_calls = []

    def _provenance(path):
        provenance_calls.append(path)
        return {
            "commit": "abc123",
            "dirty": True,
            "status_sha256": "status",
            "tracked_diff_sha256": "diff",
            "uv_lock_sha256": "lock",
        }

    monkeypatch.setattr(baseline, "_git_provenance", _provenance)
    args = SimpleNamespace(
        interval=0.5,
        window=2.0,
        command="stack",
        settle=7.0,
        timeout=90.0,
        shutdown_timeout=25.0,
        env=["NEKO_MERGED=1"],
        backend_cwd="backend-root",
    )

    metadata = baseline._metadata(args)

    assert metadata["sample_interval_s"] == 0.5
    assert metadata["sample_window_s"] == 2.0
    assert metadata["stack"]["topology"] == "1"
    assert metadata["stack"]["settle_s"] == 7.0
    assert metadata["source"]["backend"]["dirty"] is True
    assert metadata["source"]["backend"]["tracked_diff_sha256"] == "diff"
    assert provenance_calls[1].name == "backend-root"


@pytest.mark.unit
def test_graceful_shutdown_reports_clean_exit(monkeypatch):
    class _Process:
        def __init__(self):
            self.returncode = None
            self.signals = []

        def send_signal(self, value):
            self.signals.append(value)
            self.returncode = 0

        def poll(self):
            return self.returncode

    process = _Process()
    monkeypatch.setattr(baseline, "_port_released", lambda _port: True)
    monkeypatch.setattr(baseline.os, "name", "posix")

    result = baseline._request_graceful_shutdown(
        process,
        ports=[43111, 43112, 43115],
        timeout=0.1,
    )

    assert process.signals == [signal.SIGTERM]
    assert result["graceful"] is True
    assert result["ports_closed"] is True
    assert result["exit_code"] == 0


@pytest.mark.unit
def test_failed_graceful_shutdown_is_a_cli_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        baseline,
        "_stack",
        lambda _args: {
            "scenario": "stack",
            "validation_errors": ["graceful shutdown failed"],
        },
    )
    monkeypatch.setattr(baseline, "_metadata", lambda _args: {})
    written = []
    monkeypatch.setattr(baseline, "_write_json", lambda path, payload: written.append((path, payload)))

    result = baseline.main(
        [
            "--output",
            str(tmp_path / "result.json"),
            "stack",
            "--backend-command",
            '["launcher"]',
            "--graceful-shutdown",
        ]
    )

    assert result == 1
    assert written[0][1]["validation_errors"] == ["graceful shutdown failed"]


@pytest.mark.unit
def test_http_path_probe_records_status_without_body(monkeypatch):
    class _Response:
        status = 200

        def __init__(self):
            self.headers = Message()
            self.headers["Content-Type"] = "text/html; charset=utf-8"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"private page contents are not retained"

    monkeypatch.setattr(baseline.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    result = baseline._probe_http_paths(43111, ["/", "chat", "/subtitle"])

    assert set(result) == {"/", "/chat", "/subtitle"}
    assert all(item["status"] == 200 for item in result.values())
    assert all(item["content_type"] == "text/html" for item in result.values())
    assert all("body" not in item for item in result.values())


@pytest.mark.unit
def test_failed_http_path_probe_is_a_cli_failure(monkeypatch, tmp_path):
    probes = {
        "/": {"status": 200},
        "/chat": {"status": 404},
        "/subtitle": {"status": None, "error": "URLError"},
    }
    validation_errors = baseline._http_probe_validation_errors(probes)
    assert validation_errors == ["HTTP route probes failed: /chat, /subtitle"]

    monkeypatch.setattr(
        baseline,
        "_stack",
        lambda _args: {
            "scenario": "stack",
            "http_probes": probes,
            "validation_errors": validation_errors,
        },
    )
    monkeypatch.setattr(baseline, "_metadata", lambda _args: {})
    written = []
    monkeypatch.setattr(
        baseline,
        "_write_json",
        lambda path, payload: written.append((path, payload)),
    )

    result = baseline.main(
        [
            "--output",
            str(tmp_path / "result.json"),
            "stack",
            "--backend-command",
            '["launcher"]',
            "--probe-path",
            "/chat",
        ]
    )

    assert result == 1
    assert written[0][1]["validation_errors"] == validation_errors
