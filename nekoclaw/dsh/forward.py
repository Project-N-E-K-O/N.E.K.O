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

"""The model requests of dsh sessions, forwarded to the model each session is configured with.

dsh is given one provider, this forwarder, and a fixed set of model names
(``profile.slot_model``). A session is bound to one of them while it lives,
so the name in a request says which session made it. The request is then
sent on to that session's real endpoint:

* with the real model name and key, which dsh never sees;
* with what the endpoint's provider needs beyond plain chat completions,
  taken from the same registry the built-in runtime's model client uses
  (``config.providers``): the body fields that switch thinking, the field
  the output cap goes by, the provider's cache header;
* unchanged otherwise, and the answer is passed back as it arrives.

Two things are done here because only the forwarder sees every request:

* **Usage.** ACP tells a client how full the context is, not what a request
  cost. The usage the endpoint reports is read from each answer and handed
  to the driver, which makes the ``usage`` event of it.
* **The request after a final report is not made.** In the built-in
  runtime a ``report`` that ends the turn stops the loop. dsh knows no such
  tool and would ask the model once more with the report's result. When the
  report said the commission is done (``ready``) or cannot be done
  (``failed``), that request is recognised by its last messages and
  answered here with a fixed word, so a commission does not pay a model
  request to say nothing. The word goes into dsh's record of the session as
  a reply of the model's, which it is not; that is why it is done for these
  two only, after which the commission is over. After ``waiting`` and
  ``blocked``, which a commission goes on from, the model is asked.

Only endpoints that speak chat completions are forwarded; a session whose
model is reached through the Anthropic Messages API is not started on dsh
(``unsupported_reason``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from nekoclaw.runtime.protocol import ModelConfig

logger = logging.getLogger(__name__)

# How much of a refused request's answer is read for what it says of the model's window.
_REFUSAL_READ_BYTES = 4096
# The statuses a request that is too long is refused with
# (``nekoclaw.runtime.model.OVERFLOW_STATUSES``; a test keeps the two the same).
_OVERFLOW_STATUSES = (400, 413, 422)

# ``report`` statuses after which a commission is over: done, or not to be done.
FINAL_REPORT_STATUSES = frozenset({"ready", "failed"})
# How the result of a delivered report begins (same module).
REPORT_DELIVERED_PREFIX = "report delivered"
_ERROR_BODY_MAX_BYTES = 1_000_000

Send = Callable[[dict[str, Any]], Awaitable[None]]
Receive = Callable[[], Awaitable[dict[str, Any]]]
# Called with the session and the usage one answer reported, as the endpoint wrote it.
UsageListener = Callable[[str, dict[str, Any]], None]


def unsupported_reason(model: ModelConfig) -> str | None:
    """Return why a session with this model cannot run on dsh, or None when it can."""
    from utils.llm_client import _is_anthropic_endpoint

    if not model.base_url:
        return "the model has no address to forward its requests to"
    if _is_anthropic_endpoint(model.base_url, model.provider_type):
        return "the model is reached through the Anthropic Messages API, which is not forwarded for dsh"
    return None


@dataclass(frozen=True, slots=True)
class Route:
    """Where the requests of one slot of one dsh process go."""

    session_id: str
    model: ModelConfig


def _text_of(content: Any) -> str:
    """Return the text of a chat message's content, a string or a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def follows_final_report(messages: Any, report_tool: str) -> bool:
    """Return whether a request was made only to answer a report that finished the commission.

    True when the conversation ends in the results of the last reply's tool
    calls, every one of those calls was the report tool with a status that
    finishes the commission, and every one was delivered.
    """
    if not isinstance(messages, list):
        return False
    results: list[dict[str, Any]] = []
    index = len(messages) - 1
    while index >= 0 and isinstance(messages[index], dict) and messages[index].get("role") == "tool":
        results.append(messages[index])
        index -= 1
    if not results or index < 0:
        return False
    reply = messages[index]
    calls = reply.get("tool_calls") if isinstance(reply, dict) and reply.get("role") == "assistant" else None
    if not isinstance(calls, list) or not calls or len(calls) != len(results):
        return False
    answered = {str(result.get("tool_call_id")) for result in results}
    for call in calls:
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict) or function.get("name") != report_tool:
            return False
        if str(call.get("id")) not in answered:
            return False
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except (TypeError, ValueError):
            return False
        if not isinstance(arguments, dict) or arguments.get("status") not in FINAL_REPORT_STATUSES:
            return False
    return all(_text_of(result.get("content")).lstrip().startswith(REPORT_DELIVERED_PREFIX) for result in results)


def prepare_request(payload: dict[str, Any], model: ModelConfig) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Return the address, headers and body of a request as the real endpoint gets it.

    Mirrors what ``utils.llm_client.ChatOpenAI`` does for the same endpoint.
    """
    from config.providers import get_cache_kwargs, get_extra_body, get_token_limit_field, rewrite_router_reasoning
    from utils.http_client import ensure_user_agent

    base_url = str(model.base_url or "")
    body = dict(payload)
    body["model"] = model.model
    limit = body.pop("max_completion_tokens", None)
    older = body.pop("max_tokens", None)
    limit = limit if limit is not None else older
    if limit is not None:
        body[get_token_limit_field(base_url)] = limit
    if model.extra_body is not None:
        extra: dict[str, Any] | None = dict(model.extra_body)
    elif model.auto_extra_body:
        extra = get_extra_body(model.model, base_url) or None
    else:
        extra = None
    extra, effort = rewrite_router_reasoning(base_url, model.model, extra)
    if effort is not None:
        body["reasoning_effort"] = effort
    if extra:
        # As the OpenAI client does with ``extra_body``: merged into the body.
        body.update(extra)
    headers: dict[str, Any] = dict(get_cache_kwargs(base_url).get("default_headers") or {})
    headers = ensure_user_agent(headers)
    key = model.api_key if model.api_key is not None else "sk-placeholder"
    if "api.anthropic.com" in base_url:
        # Its chat-completions endpoint takes the key in a header of its own.
        headers["x-api-key"] = model.api_key or ""
        headers["anthropic-version"] = "2023-06-01"
        key = "anthropic-via-header"
    headers["Authorization"] = f"Bearer {key}"
    headers["Content-Type"] = "application/json"
    # Uncompressed: the answer is passed on as it arrives and read for its usage.
    headers["Accept-Encoding"] = "identity"
    return f"{base_url.rstrip('/')}/chat/completions", {str(k): str(v) for k, v in headers.items()}, body


class _UsageReader:
    """Finds the usage in an answer as it streams by, without holding the answer."""

    def __init__(self, streamed: bool) -> None:
        self._streamed = streamed
        self._pending = b""
        self._whole: list[bytes] = []
        self._size = 0
        self.usage: dict[str, Any] | None = None

    def feed(self, chunk: bytes) -> None:
        if not self._streamed:
            if self._size <= _ERROR_BODY_MAX_BYTES * 8:
                self._whole.append(chunk)
                self._size += len(chunk)
            return
        data = self._pending + chunk
        lines = data.split(b"\n")
        self._pending = lines.pop()
        for line in lines:
            self._line(line)

    def _line(self, line: bytes) -> None:
        line = line.strip()
        if not line.startswith(b"data:") or b'"usage"' not in line:
            return
        try:
            event = json.loads(line[5:].decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        usage = event.get("usage") if isinstance(event, dict) else None
        if isinstance(usage, dict):
            self.usage = usage

    def finish(self) -> dict[str, Any] | None:
        if self._streamed:
            if self._pending:
                self._line(self._pending)
            return self.usage
        try:
            answer = json.loads(b"".join(self._whole).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        usage = answer.get("usage") if isinstance(answer, dict) else None
        return usage if isinstance(usage, dict) else None


def _error(message: str, kind: str = "nekoclaw_forward_error") -> dict[str, Any]:
    return {"error": {"message": message, "type": kind, "code": kind}}


async def respond_json(send: Send, status: int, payload: Any) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": data})


class ModelForwarder:
    """Sends the model requests of dsh sessions to their real endpoints."""

    def __init__(
        self,
        on_usage: UsageListener,
        *,
        report_tool: str,
        transport: Any = None,
        on_local_reply: Callable[[str], None] | None = None,
        on_window: Callable[[str, int, str], None] | None = None,
    ) -> None:
        self._on_usage = on_usage
        # Told the session, the number of tokens and what was said, when a
        # model endpoint refuses a request as too long and states its window.
        self._on_window = on_window
        self._report_tool = report_tool
        # Told the session whose request is about to be answered here, with
        # a fixed word in the model's place, instead of being forwarded.
        self._on_local_reply = on_local_reply
        # An ``httpx`` transport, for tests; None uses the network.
        self._transport = transport
        # By dsh process and slot. Every process numbers its sessions' slots
        # from zero, so the name of a slot alone does not tell the session.
        self._routes: dict[tuple[str, str], Route] = {}
        self._client: Any = None
        self._client_lock = asyncio.Lock()

    def _learn_window(self, route: Route, refusal: bytes) -> None:
        """Hand on the window a model endpoint stated when it refused a request as too long.

        dsh is told the refusal as it came and deals with it as it does.
        What the endpoint said of its window is for the host: sessions it
        starts for this model later are managed for that window.
        """
        if self._on_window is None:
            return
        found = _stated_window(refusal)
        if found is None:
            return
        try:
            # With the model the request went to: under the session's id
            # there may be another session by now, with another model.
            self._on_window(route.session_id, route.model, found[0], found[1])
        except Exception:  # noqa: BLE001 - learning must not fail the request
            logger.exception("handing on the window a model endpoint stated failed")

    # -- routes -----------------------------------------------------------

    def bind(self, process: str, slot: str, session_id: str, model: ModelConfig) -> None:
        """Send the requests of ``process`` that name ``slot`` to ``model``, on behalf of ``session_id``.

        ``process`` is whatever tells the dsh processes apart for whoever
        calls ``serve``: the token a process sends with its requests.
        """
        self._routes[(process, slot)] = Route(session_id, model)

    def release(self, process: str, slot: str) -> None:
        self._routes.pop((process, slot), None)

    def route(self, process: str, slot: str) -> Route | None:
        return self._routes.get((process, slot))

    async def _http(self) -> Any:
        async with self._client_lock:
            if self._client is None:
                import httpx

                from utils.llm_client.lifecycle import _get_default_ssl_context

                options: dict[str, Any] = {"follow_redirects": False}
                if self._transport is not None:
                    options["transport"] = self._transport
                else:
                    options["verify"] = await asyncio.to_thread(_get_default_ssl_context)
                self._client = httpx.AsyncClient(**options)
            return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        self._routes.clear()
        if client is not None:
            await client.aclose()

    # -- one request ------------------------------------------------------

    async def serve(self, process: str, body: bytes, receive: Receive, send: Send) -> None:
        """Answer one chat-completions request of the dsh process ``process`` (an ASGI request whose body was read)."""
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            await respond_json(send, 400, _error("the request body is not JSON"))
            return
        route = self._routes.get((process, str(payload.get("model")))) if isinstance(payload, dict) else None
        if route is None:
            await respond_json(send, 404, _error("no session is bound to this model name", "model_not_found"))
            return
        streamed = bool(payload.get("stream"))
        if follows_final_report(payload.get("messages"), self._report_tool):
            if self._on_local_reply is not None:
                self._on_local_reply(route.session_id)
            await _answer_locally(send, str(payload.get("model")), streamed)
            return
        try:
            url, headers, upstream_body = prepare_request(payload, route.model)
        except Exception as exc:  # noqa: BLE001 - reported to dsh as a failed request
            logger.exception("preparing a forwarded model request failed")
            await respond_json(send, 500, _error(f"{type(exc).__name__}: {exc}"))
            return
        work = asyncio.ensure_future(self._forward(route, url, headers, upstream_body, streamed, send))
        gone = asyncio.ensure_future(_wait_disconnect(receive))
        try:
            await asyncio.wait({work, gone}, return_when=asyncio.FIRST_COMPLETED)
            if not work.done():
                # dsh gave the request up (its turn was stopped): so do we.
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
                return
            work.result()
        finally:
            gone.cancel()
            if not work.done():
                work.cancel()

    async def _forward(
        self, route: Route, url: str, headers: dict[str, str], body: dict[str, Any], streamed: bool, send: Send
    ) -> None:
        import httpx

        from config.nekoclaw_settings import DSH_FORWARD_CONNECT_SECONDS

        client = await self._http()
        timeout = httpx.Timeout(
            connect=DSH_FORWARD_CONNECT_SECONDS,
            read=route.model.request_timeout_seconds,
            write=DSH_FORWARD_CONNECT_SECONDS,
            pool=DSH_FORWARD_CONNECT_SECONDS,
        )
        started = False
        counted = False
        # Fed only with an answer that is no error, so an error counts as no request.
        reader = _UsageReader(streamed)
        # The beginning of what a request was refused with, as far as it may
        # say that the input was too long.
        refusal = bytearray()

        def count() -> None:
            """Hand on what the request cost, once, if the answer has told it."""
            nonlocal counted
            if counted:
                return
            usage = reader.finish()
            if usage is None:
                return
            counted = True
            try:
                self._on_usage(route.session_id, usage)
            except Exception:  # noqa: BLE001 - counting must not fail the request
                logger.exception("handing on the usage of a model request failed")

        try:
            async with client.stream(
                "POST", url, headers=headers, content=json.dumps(body, ensure_ascii=False).encode("utf-8"), timeout=timeout
            ) as answer:
                content_type = answer.headers.get("content-type") or "application/json"
                await send(
                    {
                        "type": "http.response.start",
                        "status": answer.status_code,
                        "headers": [(b"content-type", content_type.encode("latin-1")), (b"cache-control", b"no-cache")],
                    }
                )
                started = True
                async for chunk in answer.aiter_bytes():
                    if not chunk:
                        continue
                    if answer.status_code < 400:
                        reader.feed(chunk)
                    elif answer.status_code in _OVERFLOW_STATUSES and len(refusal) < _REFUSAL_READ_BYTES:
                        refusal.extend(chunk[: _REFUSAL_READ_BYTES - len(refusal)])
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                # Counted before the last piece is sent: with that piece the
                # request is over for dsh, the connection is reported as gone,
                # and whatever this was still doing is given up.
                count()
                self._learn_window(route, bytes(refusal))
                await send({"type": "http.response.body", "body": b"", "more_body": False})
        except asyncio.CancelledError:
            # dsh hung up. It does so as soon as it has what it needs, which
            # may be before the endpoint ends the answer; what the answer had
            # told of its cost by then was spent all the same.
            count()
            raise
        except httpx.HTTPError as exc:
            # The address is the user's own configuration; the key is not in it.
            logger.warning("a forwarded model request failed: %s", type(exc).__name__)
            count()
            if not started:
                await respond_json(send, 502, _error(f"the model endpoint could not be reached: {type(exc).__name__}: {exc}"))
            else:
                # Cut off in the middle: dsh sees a broken stream and repeats the request.
                await send({"type": "http.response.body", "body": b"", "more_body": False})


def _stated_window(refusal: bytes) -> tuple[int, str] | None:
    """Return the window a model endpoint stated when it refused a request as too long, and what it said."""
    # Imported here: the model client is not needed to forward a request.
    from nekoclaw.runtime.model import says_context_overflow, window_from_overflow

    if not refusal:
        return None
    said = " ".join(refusal.decode("utf-8", "replace").split())
    if not says_context_overflow(said):
        return None
    stated = window_from_overflow(said)
    return None if stated is None else (stated, said)


async def _answer_locally(send: Send, model: str, streamed: bool) -> None:
    """Answer a request with a fixed word instead of sending it to the model."""
    from config.prompts.prompts_nekoclaw_dsh import DSH_AFTER_REPORT_REPLY

    base = {"id": "chatcmpl-nekoclaw-local", "created": 0, "model": model}
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if not streamed:
        message = {"role": "assistant", "content": DSH_AFTER_REPORT_REPLY}
        await respond_json(
            send,
            200,
            {
                **base,
                "object": "chat.completion",
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": usage,
            },
        )
        return
    chunk = {**base, "object": "chat.completion.chunk"}
    events = [
        {**chunk, "choices": [{"index": 0, "delta": {"role": "assistant", "content": DSH_AFTER_REPORT_REPLY}, "finish_reason": None}]},
        {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {**chunk, "choices": [], "usage": usage},
    ]
    data = b"".join(b"data: " + json.dumps(event).encode("utf-8") + b"\n\n" for event in events) + b"data: [DONE]\n\n"
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/event-stream"), (b"cache-control", b"no-cache")],
        }
    )
    await send({"type": "http.response.body", "body": data})


async def _wait_disconnect(receive: Receive) -> None:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return
