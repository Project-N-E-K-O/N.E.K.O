import importlib
import logging

import pytest

from app.monitor_auth import MonitorQueryLogFilter
from config import network


@pytest.mark.parametrize("name, default", [("MONITOR_HOST", "0.0.0.0"), ("MONITOR_TOKEN", ""), ("MONITOR_VIEWER_TOKEN", "")])
def test_monitor_config_defaults_and_env_precedence(monkeypatch, name, default):
    monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(f"NEKO_{name}", raising=False)
    assert network._read_str_env(name, default) == default
    monkeypatch.setenv(name, "bare-value")
    assert network._read_str_env(name, default) == "bare-value"
    monkeypatch.setenv(f"NEKO_{name}", "preferred-value")
    assert network._read_str_env(name, default) == "preferred-value"


@pytest.mark.parametrize("message, args", [
    ('%s - "%s %s HTTP/%s" %d', ("127.0.0.1", "GET", "/subtitle?token=secret&name=test", "1.1", 200)),
    ('%s - "WebSocket %s" [accepted]', ("127.0.0.1", "/ws/name?token=secret")),
    ('request /ws/name?token=secret rejected', ()),
])
def test_uvicorn_request_logs_omit_query_strings(message, args):
    record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, message, args, None)
    assert MonitorQueryLogFilter().filter(record)
    rendered = record.getMessage()
    assert "secret" not in rendered
    assert "?" not in rendered
    assert "/" in rendered


@pytest.mark.parametrize("message, args", [
    ("Unexpected value?%s", ("x",)),
    ("ratio?%d%%", (5,)),
])
def test_redaction_never_breaks_a_format_template(message, args):
    record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, message, args, None)
    assert MonitorQueryLogFilter().filter(record)
    assert record.getMessage() == message % args
