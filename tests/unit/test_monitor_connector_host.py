from app.main_server.character_runtime import _monitor_connector_host


def test_monitor_connector_host_maps_wildcard_bind_addresses_to_loopback():
    assert _monitor_connector_host("0.0.0.0") == "127.0.0.1"
    assert _monitor_connector_host("::") == "127.0.0.1"
    assert _monitor_connector_host("[::]") == "127.0.0.1"


def test_monitor_connector_host_preserves_specific_host():
    assert _monitor_connector_host("localhost") == "localhost"
    assert _monitor_connector_host("192.168.1.20") == "192.168.1.20"
