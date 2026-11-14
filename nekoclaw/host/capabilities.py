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
  ``gateway_invoke``) are not served to task agents;
* built-in capabilities: two groups of small tools, one for the keyboard and
  the mouse and one for the browser (design section 11). A task agent calls
  them one step at a time from its own loop; none of them runs a loop of its
  own. The host does not implement them: whoever embeds it passes a
  ``BuiltinCapabilities`` provider that lists the tools with their current
  availability and carries a call out. Without one there are none. Only task
  agents call them, through the gateway; the task brain never routes to them
  directly.

For the task brain's own selection the MCP tools remain ordinary entries of
the ``mcp_adapter`` plugin, exactly as the analyzer saw them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from nekoclaw.host.plugin_runs import PluginCatalog
from nekoclaw.host.selection import routable_plugins, visible_entries

logger = logging.getLogger(__name__)

MCP_ADAPTER_PLUGIN_ID = "mcp_adapter"
# Entries of the adapter that call a tool of any server (or plugin) named in
# their arguments, so no single server's permission covers them. Every tool of
# a connected server is served on its own, with that server's permission.
MCP_ADAPTER_GENERIC_ENTRIES = frozenset({"call_tool", "gateway_invoke"})
_MCP_ENTRY_PREFIX = "mcp_"
_MCP_NAME_SEPARATOR = "] "
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
    # The name a task agent calls it by through the gateway.
    tool_name: str = ""
    # For a built-in tool: the group it belongs to. The group is also what a
    # commission occupies while it uses the tool (``leases.py``).
    group: str | None = None
    # True for a tool that is of no use to a model that cannot see pictures
    # (it works from screenshots, or hands one back). Such a tool is offered
    # only to a task agent whose model can.
    needs_images: bool = False


# The two groups of built-in tools. A group's name is also its permission
# category and the thing one commission occupies at a time.
BUILTIN_GROUP_KEYBOARD_MOUSE = "keyboard_mouse"
BUILTIN_GROUP_BROWSER = "browser"
BUILTIN_GROUPS: tuple[str, ...] = (BUILTIN_GROUP_KEYBOARD_MOUSE, BUILTIN_GROUP_BROWSER)


def builtin_tool(
    group: str,
    definition: dict[str, Any],
    *,
    available: bool = True,
    unavailable_reason: str = "",
    needs_images: bool = False,
) -> Capability:
    """Return one tool of a built-in group as a capability.

    ``definition`` is the tool as the model is to see it: ``name``,
    ``description`` and ``parameters`` (a JSON schema of type object). The
    name is used as it is, so it must be one a model may call and must not
    be the name of a tool the runtime has itself.
    """
    if group not in BUILTIN_GROUPS:
        raise ValueError(f"unknown built-in group {group!r}")
    name = str(definition["name"])
    parameters = definition.get("parameters")
    return Capability(
        id=f"builtin:{group}:{name}",
        kind=KIND_BUILTIN,
        title=name,
        description=str(definition.get("description") or ""),
        input_schema=parameters if isinstance(parameters, dict) else {"type": "object", "properties": {}},
        permission=group,
        permission_key=None,
        one_step=False,
        available=available,
        unavailable_reason=unavailable_reason,
        tool_name=name,
        group=group,
        needs_images=needs_images,
    )


@dataclass(frozen=True, slots=True)
class BuiltinImage:
    """A picture a built-in tool hands back, a screenshot as a rule."""

    # Base64 of the encoded image.
    data: str
    mime_type: str


@dataclass(frozen=True, slots=True)
class BuiltinOutcome:
    """How one call of a built-in tool ended."""

    success: bool
    # What it did or found, or why it failed; given to the task agent.
    text: str
    # Pictures for the task agent's model, placed after the text.
    images: tuple[BuiltinImage, ...] = ()


class BuiltinCapabilities(Protocol):
    """Lists the built-in tools and carries their calls out.

    Implemented outside the host, next to the code that operates the keyboard,
    the mouse and the browser.
    """

    def available(self) -> list[Capability]:
        """Return the built-in tools, each with its availability as of now. Must not block."""

    async def run(self, capability: Capability, *, commission_id: str, arguments: dict[str, Any]) -> BuiltinOutcome:
        """Carry out one call. Cancelling it must stop what it started."""

    async def released(self, group: str, commission_id: str) -> None:
        """A commission no longer occupies a group: drop what was kept for it."""


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
    the parts. ``CapabilityRegistry.callable_by_task_agents`` drops any name
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
    # The adapter names every tool "[server] tool" and gives it the id
    # "mcp_<server>_<tool>"; it lists no server field of its own. A server
    # name may hold "] " itself, so every way of reading the name is tried,
    # and when there is more than one the id has to agree with exactly one.
    name = str(entry.get("name") or "")
    if not name.startswith("["):
        return None
    readings = []
    at = name.find(_MCP_NAME_SEPARATOR, 1)
    while at != -1:
        readings.append((name[1:at], name[at + len(_MCP_NAME_SEPARATOR) :]))
        at = name.find(_MCP_NAME_SEPARATOR, at + 1)
    readings = [(server, tool) for server, tool in readings if server and tool]
    if len(readings) > 1:
        readings = [
            (server, tool) for server, tool in readings if entry_id == f"{_MCP_ENTRY_PREFIX}{server}_{tool}"
        ]
    # None when it cannot be told: no server's permission can be said to cover it.
    return readings[0] if len(readings) == 1 else None


@dataclass(frozen=True)
class McpDenial:
    """The MCP server settings, read the way ``PermissionStore.mode`` reads them.

    ``servers`` holds the modes set for single servers (``"*"`` is the slot
    for a request that names no server); ``category`` is the mode of the
    ``mcp_server`` category, which a server without its own mode falls back to.
    """

    servers: dict[str, str]
    category: str

    def server_denied(self, server: str) -> bool:
        """Whether this server is forbidden: its own mode first, else the category's."""
        slot = (server or "").strip() or "*"
        return self.servers.get(slot, self.category) == "deny"

    @property
    def any_denied(self) -> bool:
        """Whether some server, or a server not named, may be forbidden."""
        return self.category == "deny" or "deny" in self.servers.values()


def mcp_entry_forbidden(plugin_id: str, entry: dict[str, Any], denial: McpDenial) -> bool:
    """Whether a plugin entry reaches an MCP server the permission settings forbid.

    A server's tool is forbidden when its server is. The adapter's entries
    that can reach any server, and a tool entry whose server cannot be read
    from its name, are forbidden as soon as any server may be, since which
    one they reach cannot be told.
    """
    if plugin_id != MCP_ADAPTER_PLUGIN_ID:
        return False
    identity = _mcp_identity(entry)
    if identity is not None:
        return denial.server_denied(identity[0])
    entry_id = str(entry.get("id") or "")
    if entry_id in MCP_ADAPTER_GENERIC_ENTRIES or entry_id.startswith(_MCP_ENTRY_PREFIX):
        return denial.any_denied
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
        # The adapter's entries that can reach any server, and a server's tool
        # whose server cannot be told from its name: neither is covered by one
        # server's permission, so neither is served as a plain plugin entry.
        generic = plugin_id == MCP_ADAPTER_PLUGIN_ID and (
            entry_id in MCP_ADAPTER_GENERIC_ENTRIES or entry_id.startswith(_MCP_ENTRY_PREFIX)
        )
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
                    ""
                    if not generic
                    else "calls a tool of any MCP server without that server's permission; each tool is served on its own"
                    if entry_id in MCP_ADAPTER_GENERIC_ENTRIES
                    else "its MCP server cannot be told from its name, so no server's permission covers it"
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
        builtins: BuiltinCapabilities | None = None,
        short_descriptions: Any = None,
    ) -> None:
        self._catalog = catalog
        self._plugins_enabled = plugins_enabled
        self._builtins = builtins
        # ``nekoclaw.host.short_descriptions.ShortDescriptions``, or None.
        self._short_descriptions = short_descriptions

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
        plugin switch is off. A plugin whose manifest has no short description
        gets the one written for it, once there is one.
        """
        if not self.plugins_enabled():
            return []
        plugins = routable_plugins(await self._catalog.plugins())
        if self._short_descriptions is not None:
            try:
                plugins = await self._short_descriptions.apply(plugins)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - listed by their descriptions, as without it
                logger.warning("applying the plugin short descriptions failed", exc_info=True)
        return plugins

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
        items.extend(self.builtins())
        return items

    def builtins(self) -> list[Capability]:
        """Return the built-in tools with their availability as of now."""
        if self._builtins is None:
            return []
        try:
            return [
                item
                for item in self._builtins.available()
                if item.kind == KIND_BUILTIN and item.group in BUILTIN_GROUPS
            ]
        except Exception:  # noqa: BLE001 - an unreadable provider offers nothing
            logger.exception("listing the built-in capabilities failed")
            return []

    async def run_builtin(
        self, capability: Capability, *, commission_id: str, arguments: dict[str, Any]
    ) -> BuiltinOutcome:
        """Carry out one call of a built-in tool through the provider."""
        if self._builtins is None:
            return BuiltinOutcome(False, "this capability is not offered by this process")
        return await self._builtins.run(capability, commission_id=commission_id, arguments=arguments)

    async def builtin_released(self, group: str, commission_id: str) -> None:
        """Tell the provider that a commission no longer occupies a group."""
        released = getattr(self._builtins, "released", None)
        if released is None:
            return
        try:
            await released(group, commission_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - what it kept is its own; the group is free either way
            logger.warning("releasing the built-in group %s failed", group, exc_info=True)

    async def callable_by_task_agents(self) -> list[Capability]:
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
