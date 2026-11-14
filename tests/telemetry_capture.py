# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Drive TokenTracker's process-exit flush against a local collector.

Tests about telemetry itself must never reach the production server, and the
suite-wide opt-out (``tests/process_isolation.py``) must not stop them from
testing. ``exit_flush_requests`` builds a fresh tracker, points the reporting
URL at a loopback HTTP server and runs the exact atexit path
(``_atexit_save``), returning what the collector received. Whether anything is
sent is left to ``reporting._DO_NOT_TRACK``: a test that wants a report patches
it to ``False`` itself.
"""

from __future__ import annotations

import atexit
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def exit_flush_requests(monkeypatch) -> list[str]:
    """Run one tracker's atexit flush; return the request paths a loopback collector saw."""
    from utils import instrument
    from utils.token_tracker import TokenTracker, reporting

    received: list[str] = []

    class _Collector(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server naming
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            received.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    monkeypatch.setattr(reporting, "_TELEMETRY_SERVER_URL", f"http://{host}:{port}")
    # Payload metadata probes Steamworks and hardware; irrelevant to whether a
    # report is sent at all.
    monkeypatch.setattr(reporting, "_get_anonymous_device_id", lambda: "test-device")
    monkeypatch.setattr(reporting, "_get_legacy_device_id", lambda: "")
    monkeypatch.setattr(reporting, "_get_telemetry_metadata", lambda: ("source", ""))
    monkeypatch.setattr(reporting, "_get_device_hw", lambda: "")

    tracker = TokenTracker()
    # The constructor registered the real exit hook; this one must not outlive
    # the test, or it would flush again at interpreter exit with the URL restored.
    atexit.unregister(tracker._atexit_save)
    try:
        tracker._atexit_save()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        # Drop the session_end metrics the flush emitted (sent or not), so
        # they do not show up in a later test's instrument snapshot.
        instrument.snapshot()
    return received
