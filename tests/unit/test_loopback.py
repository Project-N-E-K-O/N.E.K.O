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
