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

"""The capability list: what can be used to get a commission done.

One register shared by the task brain and the gateway (design section 5.2).
It is never stored: every read computes it from the plugin list, the built-in
capabilities and the MCP tools the user connected. The plugin list is fetched
again after a plugin starts, stops, reloads, is installed or is removed
(``invalidate``).

What is in it:

* plugin entries, with the existing visibility switches applied: a
  ``passive`` plugin and an entry whose metadata has ``agent_auto`` /
  ``agent_exposed`` / ``llm_exposed`` set to false or ``agent_hidden`` set to
  true are left out;
* MCP tools: the tools of the servers the user configured. The existing
  ``mcp_adapter`` plugin holds those connections and lists each tool as an
  entry named ``mcp_<server>_<tool>``; here they count as MCP tools of their
  server, which is what permissions are asked for. The adapter's entries that
  call a tool of whatever server their arguments name (``call_tool``,
  ``gateway_invoke``) are not served to familiars;
* built-in capabilities (keyboard and mouse, browser): listed as not yet
  available; they arrive with a later change.

For the task brain's own selection the MCP tools remain ordinary entries of
the ``mcp_adapter`` plugin, exactly as the analyzer saw them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nekoclaw.host.plugin_runs import PluginCatalog
from nekoclaw.host.selection import routable_plugins, visible_entries

logger = logging.getLogger(__name__)

MCP_ADAPTER_PLUGIN_ID = "mcp_adapter"
# Entries of the adapter that call a tool of any server (or plugin) named in
# their arguments, so no single server's permission covers them. Every tool of
# a connected server is served on its own, with that server's permission.
MCP_ADAPTER_GENERIC_ENTRIES = frozenset({"call_tool", "gateway_invoke"})
_MCP_ENTRY_PREFIX = "mcp_"
_MCP_ENTRY_NAME_RE = re.compile(r"^\[(?P<server>.+?)\]\s+(?P<tool>.+)$")
_TOOL_NAME_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_-]")
TOOL_NAME_MAX_LENGTH = 64

KIND_PLUGIN_ENTRY = "plugin_entry"
KIND_MCP_TOOL = "mcp_tool"
KIND_BUILTIN = "builtin"


@dataclass(frozen=True, slots=True)
class Capability:
    """One thing a commission can use."""

    # Stable within one listing: "plugin:<plugin>:<entry>", "mcp:<server>:<tool>",
    # "builtin:<name>".
    id: str
    kind: str
    title: str
    description: str
    input_schema: dict[str, Any]
    # The permission category that guards it, and its key (an MCP server's name).
    permission: str
    permission_key: str | None
    # True when one call gets a task done, so the task brain may call it directly.
    one_step: bool
    available: bool
    unavailable_reason: str = ""
    # How it is called: a plugin entry (also for MCP tools, through mcp_adapter).
    plugin_id: str | None = None
    entry_id: str | None = None
    # The name a familiar calls it by through the gateway.
    tool_name: str = ""


BUILTIN_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        id="builtin:keyboard_mouse",
        kind=KIND_BUILTIN,
        title="Keyboard and mouse",
        description="Operate the computer through the keyboard and the mouse.",
        input_schema={"type": "object", "properties": {}},
        permission="keyboard_mouse",
        permission_key=None,
        one_step=False,
        available=False,
        unavailable_reason="not connected to the gateway yet",
    ),
    Capability(
        id="builtin:browser",
        kind=KIND_BUILTIN,
        title="Browser",
        description="Open and operate web pages in a browser.",
        input_schema={"type": "object", "properties": {}},
        permission="browser",
        permission_key=None,
        one_step=False,
        available=False,
        unavailable_reason="not connected to the gateway yet",
    ),
)


def _safe(part: str) -> str:
    return _TOOL_NAME_UNSAFE_RE.sub("_", part)


def _plain_part(part: str) -> bool:
    """Whether a part can stand in a name as it is without blurring the ``__`` separators."""
    return bool(part) and "__" not in part and not part.startswith("_") and not part.endswith("_")


def gateway_tool_name(prefix: str, first: str, second: str) -> str:
    """Return a tool name of at most ``TOOL_NAME_MAX_LENGTH`` characters.

    Characters a tool name may not contain become ``_``. A name that had to be
    changed or cut, or whose parts could be split at ``__`` in more than one
    way (``a__b`` + ``c`` against ``a`` + ``b__c``), ends in a short hash of
    the parts. ``CapabilityRegistry.callable_by_familiars`` drops any name
    that still comes out twice.
    """
    raw = f"{prefix}__{first}__{second}"
    name = f"{prefix}__{_safe(first)}__{_safe(second)}"
    if name == raw and len(name) <= TOOL_NAME_MAX_LENGTH and _plain_part(first) and _plain_part(second):
        return name
    encoded = json.dumps([prefix, first, second], ensure_ascii=False)
    digest = hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:8]
    return f"{name[: TOOL_NAME_MAX_LENGTH - 9]}_{digest}"


def _schema(entry: dict[str, Any]) -> dict[str, Any]:
    schema = entry.get("input_schema")
    if isinstance(schema, dict) and schema.get("type") == "object":
        return schema
    return {"type": "object", "properties": {}}


def _mcp_identity(entry: dict[str, Any]) -> tuple[str, str] | None:
    """Return ``(server, tool)`` when an ``mcp_adapter`` entry is a server's tool."""
    entry_id = str(entry.get("id") or "")
    if not entry_id.startswith(_MCP_ENTRY_PREFIX):
        return None
    match = _MCP_ENTRY_NAME_RE.match(str(entry.get("name") or ""))
    if match is None:
        # The adapter names every tool "[server] tool"; anything else is one of
        # its own entries.
        return None
    return match.group("server"), match.group("tool")


def mcp_entry_forbidden(plugin_id: str, entry: dict[str, Any], denied_servers: Any, all_denied: bool) -> bool:
    """Whether a plugin entry reaches an MCP server the permission settings forbid.

    ``denied_servers`` are the servers set to "deny"; ``all_denied`` is True
    when MCP servers as a whole are. A server's tool is forbidden when its
    server is; the adapter's entries that can reach any server are forbidden
    as soon as any server is, since which one they reach cannot be told.
    """
    if plugin_id != MCP_ADAPTER_PLUGIN_ID:
        return False
    identity = _mcp_identity(entry)
    if identity is not None:
        return all_denied or identity[0] in denied_servers
    if str(entry.get("id") or "") in MCP_ADAPTER_GENERIC_ENTRIES:
        return all_denied or bool(denied_servers)
    return False


def capabilities_of_plugin(plugin: dict[str, Any]) -> list[Capability]:
    """Return the capabilities one running plugin contributes."""
    plugin_id = str(plugin.get("id") or "")
    if not plugin_id or plugin.get("passive"):
        return []
    plugin_title = str(plugin.get("name") or plugin_id)
    items: list[Capability] = []
    for entry in visible_entries(plugin):
        entry_id = str(entry.get("id") or "")
        if not entry_id:
            continue
        description = str(entry.get("description") or "")
        mcp = _mcp_identity(entry) if plugin_id == MCP_ADAPTER_PLUGIN_ID else None
        if mcp is not None:
            server, tool = mcp
            items.append(
                Capability(
                    id=f"mcp:{server}:{tool}",
                    kind=KIND_MCP_TOOL,
                    title=f"{server}: {tool}",
                    description=description,
                    input_schema=_schema(entry),
                    permission="mcp_server",
                    permission_key=server,
                    one_step=False,
                    available=True,
                    plugin_id=plugin_id,
                    entry_id=entry_id,
                    tool_name=gateway_tool_name("mcp", server, tool),
                )
            )
            continue
        generic = plugin_id == MCP_ADAPTER_PLUGIN_ID and entry_id in MCP_ADAPTER_GENERIC_ENTRIES
        items.append(
            Capability(
                id=f"plugin:{plugin_id}:{entry_id}",
                kind=KIND_PLUGIN_ENTRY,
                title=f"{plugin_title}: {entry.get('name') or entry_id}",
                description=description or str(plugin.get("description") or ""),
                input_schema=_schema(entry),
                permission="plugin_entry",
                permission_key=None,
                one_step=True,
                available=not generic,
                unavailable_reason=(
                    "calls a tool of any MCP server without that server's permission; each tool is served on its own"
                    if generic
                    else ""
                ),
                plugin_id=plugin_id,
                entry_id=entry_id,
                tool_name=gateway_tool_name("plugin", plugin_id, entry_id),
            )
        )
    return items


@dataclass(frozen=True, slots=True)
class CatalogSummary:
    """Why there may be nothing to select from."""

    installed: int
    running: int
    routable: int
    enabled: bool


class CapabilityRegistry:
    """Computes the capability list on demand."""

    def __init__(
        self,
        catalog: PluginCatalog,
        *,
        plugins_enabled: Callable[[], bool] | None = None,
    ) -> None:
        self._catalog = catalog
        self._plugins_enabled = plugins_enabled

    def invalidate(self) -> None:
        """Forget the cached plugin list. Call it when a plugin's lifecycle changes."""
        self._catalog.invalidate()

    def plugins_enabled(self) -> bool:
        if self._plugins_enabled is None:
            return True
        try:
            return bool(self._plugins_enabled())
        except Exception:  # noqa: BLE001 - an unreadable switch counts as off
            return False

    async def plugins(self) -> list[dict[str, Any]]:
        """Return the plugins the decision model selects from.

        Running, not passive, with at least one visible entry; none when the
        plugin switch is off.
        """
        if not self.plugins_enabled():
            return []
        return routable_plugins(await self._catalog.plugins())

    async def summary(self) -> CatalogSummary:
        installed = await self._catalog.installed()
        running = [plugin for plugin in installed if plugin.get("status") == "running"]
        return CatalogSummary(
            installed=len(installed),
            running=len(running),
            routable=len(routable_plugins(running)),
            enabled=self.plugins_enabled(),
        )

    async def list(self) -> list[Capability]:
        """Return every capability: plugin entries, MCP tools, built-in ones."""
        items: list[Capability] = []
        for plugin in await self.plugins():
            items.extend(capabilities_of_plugin(plugin))
        items.extend(BUILTIN_CAPABILITIES)
        return items

    async def callable_by_familiars(self) -> list[Capability]:
        """Return the capabilities the gateway can serve right now.

        A tool name shared by two capabilities is served for neither: a call
        by that name could reach the wrong one.
        """
        items = [item for item in await self.list() if item.available and item.tool_name]
        counts = Counter(item.tool_name for item in items)
        shared = {name for name, count in counts.items() if count > 1}
        if shared:
            logger.warning("capabilities left out: their tool names are not unique: %s", sorted(shared))
        return [item for item in items if item.tool_name not in shared]
