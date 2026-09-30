from plugin.server import market_protocol_handler
from plugin.server.routes import market_bridge


def test_protocol_install_poll_timeout_covers_bridge_download_timeout() -> None:
    assert market_protocol_handler._INSTALL_POLL_TIMEOUT_SECONDS > market_bridge._DOWNLOAD_TIMEOUT


def test_protocol_install_requires_market_id_and_version(monkeypatch) -> None:
    notices: list[str] = []

    async def forbidden(**kwargs):
        raise AssertionError("install without a catalogue identity reached the bridge")

    monkeypatch.setattr(market_protocol_handler, "_call_local_install", forbidden)
    monkeypatch.setattr(
        market_protocol_handler,
        "_show_notification",
        lambda message, title: notices.append(message),
    )
    base = {"url": "https://example.test/p.neko-plugin", "sha256": "a" * 64}
    for params in (base, {**base, "id": "42"}, {**base, "version": "1.0.0"}):
        assert market_protocol_handler._handle_install(params) == 1
    assert len(notices) == 3
