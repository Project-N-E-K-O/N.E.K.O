from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from plugin.plugins.claude_companion import (
    ClaudeCompanionPlugin,
    InboxFullError,
    InboxQueue,
    _HookHTTPHandler,
)

pytestmark = pytest.mark.plugin_unit


class _DummyLogger:
    def __init__(self) -> None:
        self.infos: list[tuple[str, tuple[object, ...]]] = []

    def info(self, msg: str, *args: object) -> None:
        self.infos.append((msg, args))

    def warning(self, *_args: object, **_kwargs: object) -> None:
        pass


def _make_plugin_with_inbox(tmp_path: Path, max_size: int = 2) -> ClaudeCompanionPlugin:
    plugin = object.__new__(ClaudeCompanionPlugin)
    plugin._inbox = InboxQueue(str(tmp_path / "inbox.json"), max_size=max_size)
    plugin._api_token = ""
    plugin.logger = _DummyLogger()
    return plugin


def test_inbox_queue_evicts_read_and_raises_when_full(tmp_path: Path) -> None:
    queue = InboxQueue(str(tmp_path / "inbox.json"), max_size=2)
    first = queue.add("one")
    second = queue.add("two")

    with pytest.raises(InboxFullError, match="full"):
        queue.add("three")

    assert len(queue.pending()) == 2

    # Acking one allows eviction of read items and accepts a new message.
    assert queue.ack([first["id"]]) == 1
    third = queue.add("three")
    assert third["content"] == "three"
    assert [m["id"] for m in queue.pending()] == [second["id"], third["id"]]


def test_on_inbox_send_rejects_full_inbox_without_logging(tmp_path: Path) -> None:
    plugin = _make_plugin_with_inbox(tmp_path, max_size=1)
    ok_res = plugin._on_inbox_send({"content": "hello", "source": "neko"})
    assert ok_res["status"] == "ok"
    assert len(plugin.logger.infos) == 1

    err_res = plugin._on_inbox_send({"content": "overflow", "source": "neko"})
    assert err_res["status"] == "error"
    assert err_res["error"] == "inbox_full"
    assert len(plugin.logger.infos) == 1


def test_http_inbox_send_returns_409_when_full_and_200_on_success(tmp_path: Path) -> None:
    plugin = _make_plugin_with_inbox(tmp_path, max_size=1)

    class _Handler(_HookHTTPHandler):
        plugin_instance = plugin
        api_token = ""

        def log_message(self, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
        conn.request(
            "POST",
            "/inbox/send",
            body=json.dumps({"content": "first"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        r1 = conn.getresponse()
        b1 = json.loads(r1.read().decode("utf-8"))
        assert r1.status == 200
        assert b1["status"] == "ok"

        conn.request(
            "POST",
            "/inbox/send",
            body=json.dumps({"content": "second"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        r2 = conn.getresponse()
        b2 = json.loads(r2.read().decode("utf-8"))
        assert r2.status == 409
        assert b2["status"] == "error"
        assert b2["error"] == "inbox_full"

        conn.request(
            "POST",
            "/inbox/send",
            body=json.dumps({"content": ""}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        r3 = conn.getresponse()
        b3 = json.loads(r3.read().decode("utf-8"))
        assert r3.status == 400
        assert b3["error"] == "content_required"
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
