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

"""Tools served by the host's capability gateway.

The host runs an MCP service on the loopback address and gives every session
its own token (``TaskAgentSpec.gateway``). When a session starts, the runtime
asks the gateway for its tool list and offers those tools next to the built-in
ones; a call is forwarded to the gateway and its text comes back as the tool
result, followed by the pictures it carries (a screenshot of the desktop or
of a page). Plugin entries, MCP tools and the keyboard, mouse and browser
tools reach a task agent this way, so they are implemented once for every
runtime.

This is a small MCP client: JSON-RPC 2.0 over HTTP POST, one request per
message, with ``initialize``, ``tools/list`` and ``tools/call``. It reads an
``application/json`` answer and also the single-message event stream a
standard MCP server may answer with. Permissions for these tools are decided
by the gateway, not by the runtime's own permission hook.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from nekoclaw.runtime.loop.messages import ImageContent, TextContent
from nekoclaw.runtime.loop.tools import AgentTool, AgentToolResult
from nekoclaw.runtime.tools.base import ToolCallInfo, ToolError, define_tool, run_cancellable

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "nekoclaw-runtime"
HANDSHAKE_TIMEOUT_SECONDS = 30.0
CONNECT_TIMEOUT_SECONDS = 5.0
_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


MAX_IMAGES_PER_RESULT = 4
# About 6 MB of image; a screenshot is a few hundred kilobytes.
MAX_IMAGE_BASE64_CHARS = 8 * 1024 * 1024
ACCEPTED_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})


class GatewayError(Exception):
    """The gateway could not be reached or answered with an error."""


@dataclass(frozen=True, slots=True)
class GatewayResult:
    """What one tool call came back with."""

    text: str
    # Pictures the tool handed back (screenshots), in the order it gave them.
    images: tuple[ImageContent, ...]
    is_error: bool


class GatewayClient:
    """Talks to one gateway endpoint on behalf of one session."""

    def __init__(self, url: str, token: str) -> None:
        self._url = url
        self._token = token
        self._next_id = 0
        self._protocol_version: str | None = None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version
        return headers

    async def _post(self, message: dict[str, Any], *, read_timeout: float | None) -> Any:
        import httpx

        timeout = httpx.Timeout(read_timeout, connect=CONNECT_TIMEOUT_SECONDS)
        try:
            # The gateway is on the loopback address: no proxy, no environment.
            async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False) as client:
                response = await client.post(self._url, headers=self._headers(), content=json.dumps(message))
        except httpx.HTTPError as exc:
            raise GatewayError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code == 202 or not response.content:
            return None
        if response.status_code != 200:
            raise GatewayError(f"HTTP {response.status_code}: {response.text[:200]}")
        content_type = response.headers.get("content-type", "")
        try:
            if "text/event-stream" in content_type:
                return _last_event_data(response.text)
            return response.json()
        except ValueError as exc:
            raise GatewayError(f"the gateway's answer is not JSON: {exc}") from exc

    async def request(self, method: str, params: dict[str, Any] | None = None, *, read_timeout: float | None) -> Any:
        """Send one request and return its ``result``."""
        self._next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            message["params"] = params
        answer = await self._post(message, read_timeout=read_timeout)
        if not isinstance(answer, dict):
            raise GatewayError(f"no answer to {method}")
        error = answer.get("error")
        if error:
            detail = error.get("message") if isinstance(error, dict) else error
            raise GatewayError(str(detail))
        return answer.get("result")

    async def notify(self, method: str) -> None:
        await self._post({"jsonrpc": "2.0", "method": method}, read_timeout=HANDSHAKE_TIMEOUT_SECONDS)

    async def connect(self) -> list[dict[str, Any]]:
        """Do the MCP handshake and return the tool definitions on offer."""
        result = await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": "1"},
            },
            read_timeout=HANDSHAKE_TIMEOUT_SECONDS,
        )
        if isinstance(result, dict) and isinstance(result.get("protocolVersion"), str):
            self._protocol_version = result["protocolVersion"]
        await self.notify("notifications/initialized")
        listed = await self.request("tools/list", {}, read_timeout=HANDSHAKE_TIMEOUT_SECONDS)
        tools = listed.get("tools") if isinstance(listed, dict) else None
        return [tool for tool in tools or [] if isinstance(tool, dict) and tool.get("name")]

    async def call(self, name: str, arguments: dict[str, Any]) -> GatewayResult:
        """Call one tool and return its text and pictures; waits as long as the tool takes."""
        result = await self.request("tools/call", {"name": name, "arguments": arguments}, read_timeout=None)
        if not isinstance(result, dict):
            return GatewayResult("", (), False)
        parts: list[str] = []
        images: list[ImageContent] = []
        dropped = 0
        for item in result.get("content") or []:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "text":
                parts.append(str(item.get("text", "")))
            elif kind == "image":
                data = item.get("data")
                mime = item.get("mimeType")
                if (
                    isinstance(data, str)
                    and data
                    and len(data) <= MAX_IMAGE_BASE64_CHARS
                    and mime in ACCEPTED_IMAGE_TYPES
                    and len(images) < MAX_IMAGES_PER_RESULT
                ):
                    images.append(ImageContent(data=data, mime_type=mime))
                else:
                    dropped += 1
        if dropped:
            # Said rather than silently lost: the model may be waiting for a picture.
            parts.append(f"[{dropped} picture(s) of this result could not be passed on.]")
        return GatewayResult("\n".join(part for part in parts if part), tuple(images), bool(result.get("isError")))

    async def aclose(self) -> None:
        """Nothing to release: every request uses a connection of its own."""


def _last_event_data(stream: str) -> Any:
    """Return the JSON of the last ``data:`` block of a server-sent event stream."""
    blocks: list[str] = []
    current: list[str] = []
    for line in stream.splitlines():
        if line.startswith("data:"):
            current.append(line[5:].lstrip())
        elif not line.strip() and current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    if not blocks:
        raise ValueError("empty event stream")
    return json.loads(blocks[-1])


def build_gateway_tools(
    client: GatewayClient,
    definitions: Iterable[dict[str, Any]],
    *,
    reserved: Iterable[str] = (),
    disabled: Iterable[str] = (),
) -> list[AgentTool]:
    """Wrap the gateway's tool definitions as loop tools.

    A tool whose name a built-in tool already has is dropped, so the gateway
    can never replace a built-in tool.
    """
    taken = set(reserved)
    hidden = set(disabled)
    tools: list[AgentTool] = []
    for definition in definitions:
        name = str(definition["name"])
        if name in taken or name in hidden:
            if name in taken:
                logger.warning("gateway tool %s dropped: the name is taken", name)
            continue
        taken.add(name)
        schema = definition.get("inputSchema")
        tools.append(
            define_tool(
                {
                    "name": name,
                    "description": str(definition.get("description") or ""),
                    "parameters": schema if isinstance(schema, dict) else dict(_EMPTY_SCHEMA),
                },
                _forwarder(client, name),
            )
        )
    return tools


def _forwarder(client: GatewayClient, name: str) -> Any:
    async def forward(args: dict[str, Any], call: ToolCallInfo) -> str | AgentToolResult:
        try:
            # Aborting the run drops the request; the gateway then stops the work.
            result = await run_cancellable(client.call(name, args), call)
        except GatewayError as exc:
            raise ToolError(f"{name} failed: {exc}") from exc
        if result.is_error:
            raise ToolError(result.text or f"{name} failed")
        text = result.text or "(no output)"
        if not result.images:
            return text
        # The model adapter decides how the pictures travel (or leaves a
        # placeholder for a model that cannot see them).
        return AgentToolResult(content=[TextContent(text=text), *result.images])

    return forward


async def connect_gateway(
    url: str,
    token: str,
    *,
    reserved: Iterable[str] = (),
    disabled: Iterable[str] = (),
) -> tuple[GatewayClient, list[AgentTool]]:
    """Connect to the gateway and return the client with its tools."""
    client = GatewayClient(url, token)
    definitions = await client.connect()
    return client, build_gateway_tools(client, definitions, reserved=reserved, disabled=disabled)
