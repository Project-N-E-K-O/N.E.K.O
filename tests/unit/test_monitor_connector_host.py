import pytest

from config import network


@pytest.mark.parametrize("bind_host, dial_host", [
    ("0.0.0.0", "127.0.0.1"),
    # asyncio binds "::" with IPV6_V6ONLY, so dialing IPv4 loopback would fail.
    ("::", "[::1]"),
    ("::1", "[::1]"),
    ("localhost", "localhost"),
    ("192.168.1.20", "192.168.1.20"),
    ("fd00::20", "[fd00::20]"),
])
def test_monitor_dial_host_maps_bind_address_to_connectable_url_host(bind_host, dial_host):
    assert network._monitor_dial_host(bind_host) == dial_host


@pytest.mark.parametrize("raw, bind_host", [("[::1]", "::1"), ("[::]", "::"), ("::1", "::1"), ("127.0.0.1", "127.0.0.1")])
def test_monitor_host_strips_ipv6_brackets_for_uvicorn(monkeypatch, raw, bind_host):
    monkeypatch.delenv("MONITOR_HOST", raising=False)
    monkeypatch.setenv("NEKO_MONITOR_HOST", raw)
    assert network._read_monitor_host() == bind_host
