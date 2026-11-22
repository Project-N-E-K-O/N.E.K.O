"""Security tests for token_tracker telemetry URL/secret defaults and TLS enforcement (F0545)."""

from __future__ import annotations

import atexit
import importlib
import urllib.request
from types import SimpleNamespace

import pytest

import utils.token_tracker as tt
import utils.token_tracker.reporting as reporting


def test_telemetry_defaults_do_not_ship_hardcoded_public_ip_or_secret(monkeypatch):
    """F0545: default module import without env vars must have empty URL and empty HMAC secret."""
    monkeypatch.delenv("NEKO_TELEMETRY_SERVER_URL", raising=False)
    monkeypatch.delenv("NEKO_TELEMETRY_HMAC_SECRET", raising=False)
    reloaded = importlib.reload(reporting)
    try:
        assert reloaded._TELEMETRY_SERVER_URL == ""
        assert reloaded._TELEMETRY_HMAC_SECRET == ""
    finally:
        importlib.reload(reporting)


def test_telemetry_init_rejects_remote_plaintext_http_and_accepts_https_or_loopback(monkeypatch):
    """F0545: remote http:// URLs are cleared at init; https:// and loopback http:// are allowed."""
    monkeypatch.setenv("NEKO_TELEMETRY_SERVER_URL", "http://118.31.122.91:8099")
    reloaded = importlib.reload(reporting)
    try:
        assert reloaded._TELEMETRY_SERVER_URL == ""
    finally:
        monkeypatch.delenv("NEKO_TELEMETRY_SERVER_URL", raising=False)
        importlib.reload(reporting)

    for allowed_url in ("https://telemetry.example.com", "http://127.0.0.1:8099", "http://localhost:8099"):
        monkeypatch.setenv("NEKO_TELEMETRY_SERVER_URL", allowed_url)
        reloaded = importlib.reload(reporting)
        try:
            assert reloaded._TELEMETRY_SERVER_URL == allowed_url
        finally:
            monkeypatch.delenv("NEKO_TELEMETRY_SERVER_URL", raising=False)
            importlib.reload(reporting)


def test_report_to_server_blocks_runtime_injected_remote_plaintext_http(tmp_path, monkeypatch):
    """F0545: even if _TELEMETRY_SERVER_URL is mutated at runtime, remote http:// is never contacted."""
    called = False

    def fail_urlopen(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("remote plaintext http URL must not be contacted")

    monkeypatch.setattr(tt, "get_config_manager", lambda: SimpleNamespace(config_dir=tmp_path))
    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)
    monkeypatch.setattr(reporting, "_TELEMETRY_SERVER_URL", "http://203.0.113.10:8099")
    monkeypatch.setattr(reporting, "_DO_NOT_TRACK", False)

    tracker = tt.TokenTracker()
    try:
        tracker._last_report_time = 0.0
        tracker._report_to_server({"2026-01-01": {"qwen": {"prompt": 10}}}, [])
        assert called is False
    finally:
        atexit.unregister(tracker._atexit_save)


def test_token_tracker_facade_live_forwarding_preserved(monkeypatch):
    """TokenTrackerFacade continues forwarding _TELEMETRY_SERVER_URL and _TELEMETRY_HMAC_SECRET live."""
    monkeypatch.setattr(reporting, "_TELEMETRY_SERVER_URL", "https://telemetry.example.com")
    monkeypatch.setattr(reporting, "_TELEMETRY_HMAC_SECRET", "env-provided-secret")
    assert tt._TELEMETRY_SERVER_URL == "https://telemetry.example.com"
    assert tt._TELEMETRY_HMAC_SECRET == "env-provided-secret"

    # Also verify loopback HTTP helper matches IPv4/IPv6/localhost and rejects remote/userinfo/ftp.
    assert reporting._is_allowed_telemetry_url("http://127.0.0.1:8099") is True
    assert reporting._is_allowed_telemetry_url("http://[::1]:8099") is True
    assert reporting._is_allowed_telemetry_url("https://telemetry.example.com:8443") is True
    assert reporting._is_allowed_telemetry_url("http://118.31.122.91:8099") is False
    assert reporting._is_allowed_telemetry_url("http://example.com") is False
    assert reporting._is_allowed_telemetry_url("https://user@telemetry.example.com") is False
    assert reporting._is_allowed_telemetry_url("ftp://telemetry.example.com") is False
