from __future__ import annotations

import threading

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


def test_private_bridge_preserves_live_frame_token_for_proactive_delivery() -> None:
    socket = _PushSocket()
    bridge = ProactiveBridge()

    assert bridge.enqueue_private_payload(
        {
            "plugin_id": "demo_plugin",
            "schema": "push_message.v2",
            "visibility": [],
            "ai_behavior": "respond",
            "parts": [{"type": "text", "text": "look at this"}],
            "metadata": {"live_frame_permission_token": "generation-secret"},
        }
    ) is True

    bridge._drain_private_payloads(socket)

    assert socket.events[0]["metadata"]["live_frame_permission_token"] == "generation-secret"


def test_proactive_bridge_ignores_redacted_private_delivery_copy() -> None:
    socket = _PushSocket()

    ProactiveBridge()._dispatch(
        {
            "plugin_id": "demo_plugin",
            "schema": "push_message.v2",
            "visibility": [],
            "ai_behavior": "respond",
            "parts": [{"type": "text", "text": "already delivered privately"}],
            "_proactive_bridge_suppressed": True,
        },
        socket,
    )

    assert socket.events == []


def test_private_bridge_discards_only_stopped_plugin_payloads() -> None:
    bridge = ProactiveBridge()
    for plugin_id in ("stopped", "still-running"):
        assert bridge.enqueue_private_payload(
            {
                "plugin_id": plugin_id,
                "schema": "push_message.v2",
                "visibility": [],
                "ai_behavior": "respond",
                "parts": [{"type": "text", "text": plugin_id}],
            }
        ) is True

    assert bridge.discard_private_payloads("stopped") == 1

    socket = _PushSocket()
    bridge._drain_private_payloads(socket)

    assert [event["source_name"] for event in socket.events] == ["still-running"]


def test_private_bridge_discard_waits_for_in_flight_dispatch() -> None:
    bridge = ProactiveBridge()
    assert bridge.enqueue_private_payload({"plugin_id": "stopped"}) is True
    dispatch_started = threading.Event()
    allow_dispatch = threading.Event()
    discard_finished = threading.Event()

    def _blocking_dispatch(_payload, _socket) -> None:
        dispatch_started.set()
        assert allow_dispatch.wait(timeout=2.0)

    bridge._dispatch = _blocking_dispatch  # type: ignore[method-assign]
    drain_thread = threading.Thread(
        target=bridge._drain_private_payloads,
        args=(_PushSocket(),),
    )
    drain_thread.start()
    assert dispatch_started.wait(timeout=1.0)

    discard_thread = threading.Thread(
        target=lambda: (
            bridge.discard_private_payloads("stopped"),
            discard_finished.set(),
        )
    )
    discard_thread.start()
    try:
        assert not discard_finished.wait(timeout=0.1)
    finally:
        allow_dispatch.set()
        drain_thread.join(timeout=1.0)
        discard_thread.join(timeout=1.0)

    assert discard_finished.is_set()
