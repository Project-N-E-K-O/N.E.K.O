from __future__ import annotations

from dataclasses import dataclass

import pytest

from app import main_server
from app.main_server import character_runtime


@dataclass
class _WebSocket:
    payloads: list[dict]

    async def send_json(self, payload: dict) -> None:
        self.payloads.append(payload)


@dataclass
class _Manager:
    websocket: _WebSocket


@pytest.mark.asyncio
async def test_music_allowlist_event_sends_exact_http_urls(monkeypatch) -> None:
    url = "http://localhost:48916/plugin/music_pusher/ui/uploads/song.mp3"
    websocket = _WebSocket([])
    manager = _Manager(websocket)
    monkeypatch.setattr(character_runtime, "_get_session_manager", lambda _lanlan: manager)
    monkeypatch.setattr(main_server, "_get_session_manager", lambda _lanlan: manager)

    await main_server._handle_agent_event(
        {
            "event_type": "music_allowlist_add",
            "lanlan_name": "cat",
            "domains": ["localhost"],
            "http_urls": [url],
        }
    )

    assert websocket.payloads == [
        {
            "type": "music_allowlist_add",
            "domains": ["localhost"],
            "http_urls": [url],
        }
    ]
