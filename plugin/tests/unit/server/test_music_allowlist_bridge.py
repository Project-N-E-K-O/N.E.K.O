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
