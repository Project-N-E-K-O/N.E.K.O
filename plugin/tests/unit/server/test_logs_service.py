"""Contracts for the single file-backed log application (no private watchers)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from plugin.server.application.logs import LogService
from plugin.server.application.logs import service as service_module
from plugin.server.domain.errors import ServerDomainError
from utils.logging_layout import sanitize_log_identifier

pytestmark = pytest.mark.plugin_unit


@pytest.fixture
def service(monkeypatch, tmp_path):
    monkeypatch.setattr(service_module, "resolve_log_directory", lambda **_: tmp_path)
    return LogService()


def write_log(tmp_path, message="regular", plugin_id="demo", suffix="20261026.log"):
    path = tmp_path / f"N.E.K.O_Plugin_{sanitize_log_identifier(plugin_id)}_{suffix}"
    path.write_text(f"2026-10-26 12:00:00 - INFO - {message}\n", encoding="utf-8")
    return path


def messages(packet):
    return [entry["message"] for entry in packet.logs]


def test_read_does_not_create_directory(monkeypatch, tmp_path):
    missing = tmp_path / "missing" / "plugin"
    monkeypatch.setattr(service_module, "resolve_log_directory", lambda **_: missing)
    service = LogService()
    assert service.get_plugin_logs(plugin_id="demo").logs == []
    assert service.get_plugin_log_files("demo") == []
    assert service.files_for_export("demo") == []
    assert service.get_plugin_log_directory("demo") == missing
    assert not missing.exists()


@pytest.mark.parametrize("plugin_id", ["../demo", "demo/foo", "demo\\foo", "foo\n", "foo\r", ""])
def test_all_operations_validate_target(service, plugin_id):
    for operation in [lambda: service.get_plugin_logs(plugin_id=plugin_id),
                      lambda: service.get_plugin_log_directory(plugin_id),
                      lambda: service.get_plugin_log_files(plugin_id),
                      lambda: service.files_for_export(plugin_id),
                      lambda: service.open_session(plugin_id)]:
        with pytest.raises(ServerDomainError) as error:
            operation()
        assert error.value.status_code == 400


def test_query_and_export_share_exact_ownership(service, tmp_path):
    daily = write_log(tmp_path, "mine", plugin_id="foo")
    write_log(tmp_path, "other", plugin_id="foo_bar")
    error = write_log(tmp_path, "error", plugin_id="foo", suffix="error.log.2")
    rotation = write_log(tmp_path, "past", plugin_id="foo", suffix="20261025.log.2026-10-25")
    write_log(tmp_path, "invalid", plugin_id="foo", suffix="20261026.log.tmp")
    assert messages(service.get_plugin_logs(plugin_id="foo")) == ["mine"]
    assert set(service.files_for_export("foo")) == {daily, error, rotation}
    assert {entry["filename"] for entry in service.get_plugin_log_files("foo")} == {
        daily.name, error.name, rotation.name}


def test_long_id_is_identical_for_read_and_export(service, tmp_path):
    plugin_id = "a" * 70
    path = write_log(tmp_path, plugin_id=plugin_id)
    assert service.files_for_export(plugin_id) == [path]
    assert messages(service.get_plugin_logs(plugin_id=plugin_id)) == ["regular"]


def test_tail_excludes_newer_error_and_supports_rotation_only(service, tmp_path):
    daily = write_log(tmp_path)
    write_log(tmp_path, "error", suffix="error.log")
    os.utime(daily, (1, 1))
    assert messages(service.get_plugin_logs(plugin_id="demo")) == ["regular"]
    daily.rename(daily.with_suffix(".log.1"))
    assert messages(service.get_plugin_logs(plugin_id="demo")) == ["regular"]


def test_server_legacy_is_labelled_and_never_assigned_to_plugin(service, tmp_path):
    legacy = tmp_path / "N.E.K.O_Plugin_20261026.log"
    legacy.write_text("shared history\n", encoding="utf-8")
    result = service.get_plugin_logs(plugin_id="_server")
    assert result.legacy_source is True
    assert messages(result) == ["shared history"]
    assert service.get_plugin_logs(plugin_id="demo").logs == []
    canonical = tmp_path / "N.E.K.O_PluginServer_20261026.log"
    canonical.write_text("server\n", encoding="utf-8")
    os.utime(canonical, (1, 1))
    result = service.get_plugin_logs(plugin_id="_server")
    assert result.legacy_source is False
    assert messages(result) == ["server"]
    assert set(service.files_for_export("_server")) == {canonical, legacy}


def test_snapshot_follow_and_second_subscriber_have_independent_progress(service, tmp_path):
    path = write_log(tmp_path, "first")
    first = service.open_session("demo", lines=500)
    initial = first.snapshot()
    with path.open("a") as stream:
        stream.write("second\n")
    second = service.open_session("demo", lines=500)
    assert messages(second.snapshot()) == ["first", "second"]
    appended = first.poll()
    assert messages(appended) == ["second"]
    assert initial.logs[0]["id"] != appended.logs[0]["id"]
    assert first.poll() is None
    assert second.poll() is None


def test_snapshot_and_increment_share_same_query(service, tmp_path):
    path = write_log(tmp_path, "hello")
    session = service.open_session("demo", lines=500, level="INFO", search="hello")
    assert messages(session.snapshot()) == ["hello"]
    with path.open("a") as stream:
        stream.write("2026-10-26 12:00:01 - ERROR - hello error\n")
        stream.write("2026-10-26 12:00:02 - INFO - irrelevant\n")
        stream.write("2026-10-26 12:00:03 - INFO - hello again\n")
    assert messages(session.poll()) == ["hello again"]
    assert session.snapshot().returned_lines == 2


def test_requested_snapshot_size_is_not_overwritten(service, tmp_path):
    path = write_log(tmp_path)
    path.write_text("".join(f"message {i}\n" for i in range(600)))
    session = service.open_session("demo", lines=500)
    assert len(session.snapshot().logs) == 500
    with path.open("a") as stream:
        stream.write("last\n")
    assert messages(session.poll()) == ["last"]
    assert session.poll() is None


def test_snapshot_parses_only_the_requested_history_window(service, tmp_path, monkeypatch):
    path = write_log(tmp_path)
    path.write_text(''.join(f'message {i}\n' for i in range(50000)), encoding='utf-8')
    original = service_module.parse_log_line
    parsed = []

    def record_parse(line):
        parsed.append(line)
        return original(line)

    monkeypatch.setattr(service_module, 'parse_log_line', record_parse)
    session = service.open_session('demo', lines=500)
    result = session.snapshot()
    assert messages(result) == [f'message {i}' for i in range(49500, 50000)]
    assert len(parsed) == 500
    with path.open('a', encoding='utf-8') as stream:
        stream.write('next\n')
    assert messages(session.poll()) == ['next']
    assert len(parsed) == 501


@pytest.mark.parametrize("change", ["truncate", "replace", "rotate"])
def test_file_lifecycle_resets_session(service, tmp_path, change):
    path = write_log(tmp_path, "old long content")
    session = service.open_session("demo")
    session.snapshot()
    if change == "replace":
        replacement = tmp_path / "new"
        replacement.write_text("new\n")
        replacement.replace(path)
    elif change == "rotate":
        path.rename(path.with_suffix(".log.1"))
        path.write_text("new\n")
    else:
        path.write_text("new\n")
    packet = session.poll()
    assert packet.reset is True
    assert messages(packet) == ["new"]
    assert session.poll() is None


def test_file_appearing_and_partial_utf8_are_followed(service, tmp_path):
    session = service.open_session("demo")
    assert session.snapshot().logs == []
    path = write_log(tmp_path)
    assert session.poll().reset is True
    encoded = "猫娘\n".encode()
    with path.open("ab") as stream:
        stream.write(encoded[:2])
    assert session.poll() is None
    with path.open("ab") as stream:
        stream.write(encoded[2:])
    assert messages(session.poll()) == ["猫娘"]


def test_io_errors_are_not_reported_as_empty_logs(service, monkeypatch):
    def denied(*args):
        raise PermissionError("denied")
    monkeypatch.setattr(service, "_files", denied)
    with pytest.raises(PermissionError):
        service.get_plugin_logs(plugin_id="demo")


@pytest.mark.parametrize("line", [
    "2026-10-26 12:00:00 - [plugin.demo] - INFO - file.py:12 - historical",
    "2026-10-26 12:00:00,123 - plugin.demo - INFO - historical",
    "2026-10-26 12:00:00 - INFO - historical",
    "2026-10-26 12:00:00 INFO historical",
    "2026-10-26 12:00:00 | INFO | module:function:12 | historical",
    "2026-10-26 12:00:00 | INFO | [Plugin-demo] historical",
    "2026-10-26 12:00:00.123 | INFO | module:function:12 - historical",
    "historical",
])
def test_historical_text_remains_readable(service, tmp_path, line):
    path = write_log(tmp_path)
    path.write_text(line + "\n", encoding="utf-8")
    assert messages(service.get_plugin_logs(plugin_id="demo")) == ["historical"]
