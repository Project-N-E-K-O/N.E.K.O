from plugin.server import market_protocol_handler
from plugin.server.routes import market_bridge


def test_protocol_install_poll_timeout_covers_bridge_download_timeout() -> None:
    assert market_protocol_handler._INSTALL_POLL_TIMEOUT_SECONDS > market_bridge._DOWNLOAD_TIMEOUT
