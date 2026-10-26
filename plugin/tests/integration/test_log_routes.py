"""Real ASGI HTTP, archive and WebSocket contracts for log management."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.plugin_integration


@pytest.fixture
def log_client(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from plugin.server.routes import logs
    from plugin.server.application.logs import service as service_module

    monkeypatch.setattr(service_module, "resolve_log_directory", lambda **_: tmp_path)
    monkeypatch.setattr(logs, "log_service", service_module.LogService())
    app = FastAPI()
    app.include_router(logs.router)
    with TestClient(app) as client:
        yield client


def test_http_query_and_zip_export_use_same_ownership(log_client, tmp_path):
    import io
    import zipfile

    (tmp_path / "N.E.K.O_Plugin_demo_20261026.log").write_text("hello\n")
    (tmp_path / "N.E.K.O_Plugin_demo_error.log.1").write_text("error\n")
    (tmp_path / "N.E.K.O_Plugin_demo_other_20261026.log").write_text("other\n")
    response = log_client.get("/plugin/demo/logs?lines=500")
    assert response.status_code == 200
    assert [entry["message"] for entry in response.json()["logs"]] == ["hello"]
    assert "type" not in response.json()
    assert "reset" not in response.json()
    files = log_client.get("/plugin/demo/logs/files").json()
    assert files["plugin_id"] == "demo"
    assert files["count"] == 2
    assert files["time"]
    assert {item["filename"] for item in files["log_files"]} == {
        "N.E.K.O_Plugin_demo_20261026.log", "N.E.K.O_Plugin_demo_error.log.1"}
    directory = log_client.get("/plugin/demo/logs/directory").json()
    assert directory["plugin_id"] == "demo"
    assert directory["directory"] == str(tmp_path)
    assert directory["time"]
    response = log_client.get("/plugin/demo/logs/export")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {
            "N.E.K.O_Plugin_demo_20261026.log", "N.E.K.O_Plugin_demo_error.log.1"}


def test_socket_query_and_independent_sessions(log_client, tmp_path):
    path = tmp_path / "N.E.K.O_Plugin_demo_20261026.log"
    path.write_text("".join(f"2026-10-26 12:00:00 - INFO - hello {i}\n" for i in range(600)))
    with log_client.websocket_connect("/ws/logs/demo?lines=500&level=INFO&search=hello") as first:
        initial = first.receive_json()
        assert initial["type"] == "initial"
        assert len(initial["logs"]) == 500
        with path.open("a") as stream:
            stream.write("2026-10-26 12:00:01 - INFO - hello appended\n")
        with log_client.websocket_connect("/ws/logs/demo?lines=500&level=INFO&search=hello") as second:
            second_initial = second.receive_json()
            assert second_initial["logs"][-1]["message"] == "hello appended"
            first_append = first.receive_json()
            assert [entry["message"] for entry in first_append["logs"]] == ["hello appended"]
            with path.open("a") as stream:
                stream.write("2026-10-26 12:00:02 - ERROR - ignored\n")
                stream.write("2026-10-26 12:00:03 - INFO - hello third\n")
            for socket in [first, second]:
                assert [entry["message"] for entry in socket.receive_json()["logs"]] == ["hello third"]


def test_read_failure_is_explicit_in_http_and_socket(log_client, monkeypatch):
    from plugin.server.routes import logs

    def denied(*args):
        raise PermissionError("denied")
    monkeypatch.setattr(logs.log_service, "_files", denied)
    response = log_client.get("/plugin/demo/logs")
    assert response.status_code == 500
    assert response.headers["x-error-code"] == "LOG_READ_FAILED"
    with log_client.websocket_connect("/ws/logs/demo") as socket:
        packet = socket.receive_json()
        assert packet["type"] == "error"
        assert packet["error"]


def test_socket_serializes_a_file_reset_as_a_new_initial_snapshot(log_client, tmp_path):
    path = tmp_path / "N.E.K.O_Plugin_demo_20261027.log"
    path.write_text("older long log record\n", encoding="utf-8")
    with log_client.websocket_connect("/ws/logs/demo") as socket:
        assert socket.receive_json()["type"] == "initial"
        path.write_text("new\n", encoding="utf-8")
        packet = socket.receive_json()
        assert packet["type"] == "initial"
        assert packet["reset"] is True
        assert [entry["message"] for entry in packet["logs"]] == ["new"]
