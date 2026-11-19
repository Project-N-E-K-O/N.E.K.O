"""Unit tests for the shared socket-peer loopback check."""

import pytest

from utils.loopback import is_loopback_peer_host


@pytest.mark.unit
@pytest.mark.parametrize("host", [
    "127.0.0.1", "127.0.0.2", "::1", "localhost", "::ffff:127.0.0.1", "::ffff:7f00:1",
])
def test_loopback_peers_are_local(host):
    assert is_loopback_peer_host(host) is True


@pytest.mark.unit
@pytest.mark.parametrize("host", [
    "192.168.1.2", "::ffff:192.168.1.2", "203.0.113.9", "::ffff:203.0.113.9", "2001:db8::1",
    "::", "0.0.0.0", "testclient", "LOCALHOST", "[::1]", "", None,
])
def test_other_peers_are_not_local(host):
    assert is_loopback_peer_host(host) is False


@pytest.mark.unit
@pytest.mark.parametrize("proxy,headers,expected", [
    (False, {}, True),
    (False, {"x-forwarded-for": "203.0.113.9"}, True),
    (True, {}, True),
    (True, {"x-forwarded-for": "203.0.113.9"}, False),
    (True, {"X-Real-IP": "203.0.113.9"}, False),
    (True, {"forwarded": "for=203.0.113.9"}, False),
])
def test_direct_loopback_peer_rejects_forwarded_connections_in_proxy_mode(monkeypatch, proxy, headers, expected):
    from utils.loopback import is_direct_loopback_peer

    if proxy:
        monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    else:
        monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)
    assert is_direct_loopback_peer("::ffff:127.0.0.1", headers) is expected
    assert is_direct_loopback_peer("::ffff:203.0.113.9", headers) is False
