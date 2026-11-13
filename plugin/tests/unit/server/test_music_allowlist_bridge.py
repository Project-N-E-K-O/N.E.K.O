from __future__ import annotations

from plugin.server.messaging.proactive_bridge import ProactiveBridge


class _PushSocket:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def send_json(self, event: dict, _flags: int) -> None:
        self.events.append(event)


def test_music_allowlist_bridge_preserves_exact_http_urls() -> None:
    url = "http://127.0.0.1:48916/plugin/music_pusher/ui/uploads/song.mp3"
    socket = _PushSocket()

    ProactiveBridge()._dispatch(
        {
            "plugin_id": "music_pusher",
            "schema": "push_message.v2",
            "visibility": [],
            "ai_behavior": "blind",
            "parts": [
                {
                    "type": "ui_action",
                    "action": "media_allowlist_add",
                    "domains": ["127.0.0.1", "localhost", "::1"],
                    "http_urls": [url],
                }
            ],
        },
        socket,
    )

    assert socket.events == [
        {
            "event_type": "music_allowlist_add",
            "lanlan_name": None,
            "domains": ["127.0.0.1", "localhost", "::1"],
            "http_urls": [url],
            "source": "music_pusher",
            "timestamp": "",
        }
    ]


def test_proactive_bridge_forwards_plugin_event_ttl() -> None:
    socket = _PushSocket()

    ProactiveBridge()._dispatch(
        {
            "plugin_id": "demo_plugin",
            "schema": "push_message.v2",
            "visibility": [],
            "ai_behavior": "respond",
            "parts": [{"type": "text", "text": "urgent cue"}],
            "metadata": {"expires_in_s": 12.5},
        },
        socket,
    )

    assert socket.events[0]["expires_in_s"] == 12.5


def test_proactive_bridge_ignores_an_oversized_expiry() -> None:
    socket = _PushSocket()

    ProactiveBridge()._dispatch(
        {
            "plugin_id": "demo_plugin",
            "schema": "push_message.v2",
            "visibility": [],
            "ai_behavior": "respond",
            "parts": [{"type": "text", "text": "urgent cue"}],
            "metadata": {"expires_in_s": 10 ** 400},
        },
        socket,
    )

    assert len(socket.events) == 1
    assert "expires_in_s" not in socket.events[0]
    assert "expires_in_s" not in socket.events[0]["metadata"]


async def test_proactive_music_source_survives_plugin_and_websocket_bridges(monkeypatch):
    from plugin.tests.unit.plugins.netease_music.test_plugin import _make_plugin, _Provider, _ctx
    from plugin.plugins.netease_music.models import PlayRequest
    from app import main_server
    from app.main_server import character_runtime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    plugin, pushes, _ = _make_plugin(_Provider())
    await plugin.play_netease_music(PlayRequest(query="晴天 周杰伦"), _ctx={**_ctx(), "request_source": "proactive"})
    play = next(push for push in pushes if push["parts"][0].get("action") == "media_play_url")
    assert play["parts"][0]["playback_source"] == "proactive"
    socket = _PushSocket()
    ProactiveBridge()._dispatch({**play, "plugin_id": "netease_music", "schema": "push_message.v2"}, socket)
    event = next(e for e in socket.events if e["event_type"] == "music_play_url")
    assert event["playback_source"] == "proactive"
    manager = SimpleNamespace(websocket=SimpleNamespace(send_json=AsyncMock()))
    monkeypatch.setattr(character_runtime, "_get_session_manager", lambda name: manager)
    monkeypatch.setattr(main_server, "_get_session_manager", lambda name: manager)
    await main_server._handle_agent_event(event)
    payload = manager.websocket.send_json.call_args.args[0]
    assert payload["playback_source"] == "proactive"
    assert payload["netease_id"] == 1
