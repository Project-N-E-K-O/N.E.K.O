"""A local OpenAI-compatible chat endpoint for tests, served over plain http.

It lets a test drive the real model layer (``utils.llm_client``) without any
provider: the test supplies a ``respond(body)`` function that returns the
stream chunks for each request. Listening on 127.0.0.1 only, on a port the
operating system picks.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

Responder = Callable[[dict[str, Any]], list[dict[str, Any]]]


def _chunk(delta: dict[str, Any], finish_reason: str | None = None) -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "fake-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def sse_text(text: str) -> dict[str, Any]:
    return _chunk({"role": "assistant", "content": text})


def sse_reasoning(text: str) -> dict[str, Any]:
    return _chunk({"role": "assistant", "content": None, "reasoning_content": text})


def sse_tool_call(
    name: str,
    arguments: dict[str, Any] | str,
    call_id: str = "call_1",
    *,
    index: int = 0,
    extra_content: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = arguments if isinstance(arguments, str) else json.dumps(arguments)
    call: dict[str, Any] = {
        "index": index,
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": text},
    }
    if extra_content is not None:
        call["extra_content"] = extra_content
    return _chunk({"tool_calls": [call]})


def sse_finish(reason: str = "stop") -> dict[str, Any]:
    return _chunk({}, reason)


def sse_usage(prompt: int, completion: int) -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "fake-model",
        "choices": [],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


def sse_sleep(seconds: float) -> dict[str, Any]:
    """Not a chunk: makes the server pause before sending what follows."""
    return {"__sleep__": seconds}


class FakeOpenAIServer:
    """Context manager running the endpoint on a background thread."""

    def __init__(self, respond: Responder) -> None:
        self._respond = respond
        self._lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                del format, args

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                with owner._lock:
                    owner.requests.append(body)
                    owner.headers.append({key.lower(): value for key, value in self.headers.items()})
                    chunks = owner._respond(body)
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("cache-control", "no-cache")
                self.send_header("connection", "close")
                self.end_headers()
                try:
                    for chunk in chunks:
                        if "__sleep__" in chunk:
                            time.sleep(float(chunk["__sleep__"]))
                            continue
                        self.wfile.write(b"data: " + json.dumps(chunk).encode("utf-8") + b"\n\n")
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except OSError:
                    # The client closed the stream (an abort): nothing left to send.
                    pass
                self.close_connection = True

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def __enter__(self) -> "FakeOpenAIServer":
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(5)
