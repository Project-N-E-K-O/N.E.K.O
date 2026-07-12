import json
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_WEBSOCKET_PATH = ROOT / "static" / "app" / "app-websocket.js"
MAIN_SERVER_PATH = ROOT / "app" / "main_server.py"
PLUGIN_PATH = ROOT / "plugin" / "plugins" / "jukebox_controller" / "__init__.py"


class _FakePushSocket:
    def __init__(self):
        self.events = []

    def send_json(self, event, flags=None):
        self.events.append(json.loads(json.dumps(event)))


def test_jukebox_websocket_handler_uses_canonical_query_key():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    block = source.split("function handleJukeboxControlResponse(response)", 1)[1].split(
        "function readNewUserIcebreakerStore()",
        1,
    )[0]

    assert "action: command.action" in block
    assert "query: command.query || ''" in block
    assert "value: command.value" in block
    assert "mode: command.mode" in block
    assert "command.song" not in block
    assert "command.name" not in block
    assert "command.volume" not in block
    assert "command.delta" not in block


def test_jukebox_event_bus_uses_canonical_query_key():
    source = MAIN_SERVER_PATH.read_text(encoding="utf-8")
    block = source.split('elif event_type == "jukebox_control":', 1)[1].split(
        "async def _send_jukebox_control",
        1,
    )[0]

    assert '"action": action' in block
    assert '"query": event.get("query") or ""' in block
    assert '"value": event.get("value")' in block
    assert '"mode": event.get("mode") or ""' in block
    assert 'event.get("song")' not in block
    assert 'event.get("name")' not in block
    assert 'event.get("volume")' not in block
    assert 'event.get("delta")' not in block


def test_jukebox_plugin_schema_uses_canonical_actions():
    source = PLUGIN_PATH.read_text(encoding="utf-8")

    assert '_VALID_ACTIONS = {"play", "next", "previous", "stop", "set_volume", "adjust_volume", "set_mode"}' in source
    assert '"enum": ["play", "next", "previous", "stop", "set_volume", "adjust_volume", "set_mode"]' in source
    assert '"enum": ["none", "sequence", "single", "random"]' in source
    assert '"skip"' not in source


def test_jukebox_proactive_bridge_uses_canonical_control_keys(monkeypatch):
    from plugin.server.messaging import proactive_bridge

    if proactive_bridge.zmq is None:
        monkeypatch.setattr(proactive_bridge, "zmq", types.SimpleNamespace(NOBLOCK=1))

    push = _FakePushSocket()
    proactive_bridge.ProactiveBridge()._dispatch(
        {
            "plugin_id": "jukebox_controller",
            "time": "now",
            "metadata": {"query": "metadata-query", "song": "legacy-metadata-song"},
            "visibility": ["chat"],
            "ai_behavior": "blind",
            "parts": [
                {
                    "type": "ui_action",
                    "action": "jukebox_control",
                    "jukebox_action": "play",
                    "control": "stop",
                    "command": "next",
                    "query": "桃园",
                    "value": 50,
                    "mode": "random",
                    "song": "legacy-song",
                }
            ],
        },
        push,
    )

    assert push.events == [
        {
            "event_type": "jukebox_control",
            "lanlan_name": None,
            "action": "play",
            "query": "桃园",
            "value": 50,
            "mode": "random",
            "source": "jukebox_controller",
            "timestamp": "now",
        }
    ]

    metadata_only_push = _FakePushSocket()
    proactive_bridge.ProactiveBridge()._dispatch(
        {
            "plugin_id": "jukebox_controller",
            "time": "now",
            "metadata": {"query": "metadata-query"},
            "visibility": ["chat"],
            "ai_behavior": "blind",
            "parts": [
                {
                    "type": "ui_action",
                    "action": "jukebox_control",
                    "jukebox_action": "play",
                }
            ],
        },
        metadata_only_push,
    )

    assert metadata_only_push.events[0]["query"] is None
    assert metadata_only_push.events[0]["value"] is None
    assert metadata_only_push.events[0]["mode"] is None

    legacy_push = _FakePushSocket()
    proactive_bridge.ProactiveBridge()._dispatch(
        {
            "plugin_id": "jukebox_controller",
            "time": "now",
            "metadata": {"query": "metadata-query", "song": "legacy-metadata-song"},
            "visibility": ["chat"],
            "ai_behavior": "blind",
            "parts": [
                {
                    "type": "ui_action",
                    "action": "jukebox_control",
                    "control": "play",
                    "command": "next",
                    "song": "legacy-song",
                }
            ],
        },
        legacy_push,
    )

    assert legacy_push.events == []
