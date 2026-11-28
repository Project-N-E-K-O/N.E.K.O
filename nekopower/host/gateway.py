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

"""The capability gateway: an MCP service for task agents, on the loopback address.

Task agents reach everything that is not a built-in tool through here (design
section 10), so a capability is implemented once whichever runtime runs the
task agent. Each commission gets a token of its own; a request without a known
token is refused, and the token stops working when the commission ends.

What the gateway serves:

* plugin entries: every entry visible to automatic routing, as one tool each.
  A call sends the existing ``/runs`` request through
  ``PluginRunner.run_entry``, the same code the task brain uses for a direct
  call, and stopping the commission cancels the run;
* MCP tools of the servers the user configured (through the ``mcp_adapter``
  plugin, see ``capabilities.py``);
* the built-in tools (keyboard and mouse, browser) while they are available.
  The host lists the groups relevant to a commission up front, with
  their usage guidance. Other groups remain discoverable and load on demand.
  The search tool's description carries one line about each
  group on offer, with the group's first call. A group is handed over, how
  it is used and then the definitions of its tools (within
  ``GATEWAY_TOOL_GROUP_MAX_TOKENS``), in one of three ways, and its tools
  are from then on called by name through ``capability_call``:
  ``capability_search`` with ``group`` hands it over by itself; the group's
  first call (going to an address, taking a screenshot, and nothing else
  the same tool can do) is
  carried out and, when it went well, answered with the group in front of
  its result, so that a task which starts that way spends no request on
  loading (one that failed is answered as any failed call, and the group is
  still to be handed over); any other call
  to a tool of a group this token has not been handed is not carried out
  and is answered with the group, so that nothing but that first step is
  done before the task agent has read how the tools are used, and it can
  call again at once. Read, not just sent: a model writes all the calls
  of one reply before any of their answers reaches it, so a group handed
  over by one of them is in hand only from the next reply on, and the
  calls after it in the same reply (but the first call) are not carried
  out either. The runtime names the reply with every call
  (``GATEWAY_REPLY_META_KEY``); a client that names none is taken to
  have read what it was sent. A call is one small step, carried out by the
  provider the host was given; stopping the commission stops it. The
  commission occupies the desktop or the browser from its first call
  (``leases.py``), and a result may carry pictures (screenshots) next to its
  text. A tool that hands back pictures is offered only to a task agent
  whose model can see them;
* two tools of its own, ``capability_search`` and ``capability_call``, and
  no others: they are all a task agent is offered of the gateway as tools,
  whichever commission it works on, so the tool definitions of a session
  stay the same from one commission to the next. A session takes several
  commissions, and what the task brain picked for one is not what it picked
  for the next. The entries it picked as candidates for a commission are
  told to the task agent in the message that opens that commission
  (``candidate_definitions``, within ``GATEWAY_TOOL_LIST_MAX_TOKENS``), and
  called by name through ``capability_call`` like everything else. A
  ``tools/call`` that names a capability directly is carried out all the
  same: a client that lists none of them may still know their names.

What was handed over in the answer to a call (a group of built-in tools) is
only in hand while it is in the task agent's conversation. A runtime that
sums a conversation up says so with every call
(``GATEWAY_COMPACTIONS_META_KEY``): when the number has changed, the groups
count as not handed over, and each is handed over anew when one of its
tools is next called.

On a complete summary, a runtime with native device tools refreshes its
list. The grant keeps groups in use, awaiting execution or approval, used
since its last summary, or named by fresh user input. The search and call
tools stay available. Discovery or a new device instruction can restore
one of the groups the runtime registered when the commission opened.

Permissions are decided here, at the gateway's entry, by the same
``PermissionGate`` the built-in tools use. A runtime whose client gives a
call little time (dsh ends a call after a minute, and a person asked for an
approval may take longer) has the gateway decide before it makes the call
(``clear_call``): whoever has to be asked is asked then, and the call that
follows finds the answer and asks nobody a second time.

A plugin entry that answers ``needs_confirmation`` did not carry the call out:
it wants the user to confirm first. The task agent must not answer for the user,
so the gateway puts the plugin's question to the user itself (the commission
waits for the user meanwhile) and hands the task agent the plugin's reply
together with what the user said. A result that asks for a clarification, and
a deferred one, go back to the task agent with a note, as before.

Transport: MCP over HTTP POST with JSON answers (``initialize``, ``ping``,
``tools/list``, ``tools/call``). The listener is started when the first
task agent needs it, on a port chosen by the system, and never at startup.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import secrets
from collections import Counter
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from nekopower.host import call_audit
from nekopower.host.capabilities import (
    BUILTIN_GROUP_BROWSER,
    BUILTIN_GROUP_KEYBOARD_MOUSE,
    KIND_BUILTIN,
    Capability,
    CapabilityRegistry,
)
from nekopower.host.leases import BuiltinLeases
from nekopower.host.ledger import Commission, Ledger
from nekopower.host.permissions import (
    BROWSER_REOPEN,
    DENIED_BY_SETTINGS,
    DENIED_BY_USER,
    ENABLE_BROWSER,
    ENABLE_KEYBOARD_MOUSE,
    ENABLE_PLUGINS,
    GrantScope,
    PermissionGate,
)
from nekopower.host.plugin_runs import (
    PluginRunner,
    audited_run_entry,
    describe_result,
    llm_result_fields,
    plugin_terminal_status,
)
from nekopower.host.selection import bm25_scores, find_entry

logger = logging.getLogger(__name__)

SERVER_NAME = "nekopower-gateway"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MCP_PATH = "/mcp"
SEARCH_TOOL = "capability_search"
CALL_TOOL = "capability_call"
# Not a tool of the gateway's own list: a name ``capability_call`` takes, with
# which a task agent asks the user to turn on the browser, the keyboard and mouse
# or the user plugins when it finds one switched off (``CapabilityGateway._call_enable``).
ENABLE_TOOL = "enable_capability"
_ENABLE_ID = f"host:{ENABLE_TOOL}"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})
_START_TIMEOUT_SECONDS = 10.0
# What a plugin's request for confirmation was answered with by a button of a
# page, as the task agent that called the plugin is told it.
CONFIRMATION_ANSWERS = {"yes": "Yes, go ahead.", "no": "No, do not do it."}

_SEARCH_TOOL_DEFINITION = {
    "name": SEARCH_TOOL,
    "description": (
        "Find further capabilities of the host (plugin entries and tools of connected services) "
        "by what they do. Returns their names, descriptions and argument schemas. Call one with "
        f"{CALL_TOOL}."
    ),
    # Nothing is required: a group of built-in tools is loaded with
    # ``group`` alone (``_search_tool_definition``).
    "inputSchema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What the capability should do."}},
    },
}

_CALL_TOOL_DEFINITION = {
    "name": CALL_TOOL,
    "description": f"Call a capability found with {SEARCH_TOOL}, by its name, with arguments matching its schema.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": f"The name {SEARCH_TOOL} returned."},
            "arguments": {"type": "object", "description": "Arguments for the capability."},
        },
        "required": ["name"],
    },
}


# Puts a question to the user on behalf of a commission and returns the
# answer as ``{"text": ..., "choice": ...}``.
AskUser = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class CapabilityEnabler(Protocol):
    """Turns on what a task agent found switched off; implemented by whoever owns the switches.

    ``capability`` is ``browser``, ``keyboard_mouse`` or ``plugins``. Both
    calls return whether it can be (or was) done and, if not, why.
    ``can_enable`` changes nothing: it is asked before the user is.
    ``enable`` is given what the user's answer stands on (``still_valid``)
    and must look at it again at the moment it writes a switch, after
    whatever it waited on (a check of the computer): a switch is not
    written for an answer that no longer stands. It calls ``on_written``
    (when given) as it writes the switch itself, and not for one it found
    on or left to someone else: the answer may stop standing after the
    write, while ``enable`` still waits (for the plugins to come up), and
    the gateway then tells the switch this call wrote from one it did not.
    ``may_request`` (optional; the gateway takes a missing one as "yes")
    says whether a capability may be offered to a task agent at all on this
    computer; nothing that takes time is looked at in it.
    """

    async def can_enable(self, capability: str) -> tuple[bool, str]: ...

    def may_request(self, capability: str) -> bool: ...

    async def enable(
        self,
        capability: str,
        *,
        lanlan_name: str | None = None,
        still_valid: Callable[[], bool] | None = None,
        on_written: Callable[[], None] | None = None,
    ) -> tuple[bool, str]: ...


@dataclass(frozen=True, slots=True)
class _EnableTarget:
    """One thing a task agent may ask the user to turn on."""

    canonical: str
    aliases: tuple[str, ...]
    # The name a model knows it by (the group's name, or ``plugins``).
    display: str
    # The permission category the settings forbid or allow it by.
    permission: str
    # What the user is asked.
    ask_category: str
    builtin_group: str | None


_ENABLE_TARGETS: tuple[_EnableTarget, ...] = (
    _EnableTarget("browser", ("browser", "browser_use"), "browser", "browser", ENABLE_BROWSER, BUILTIN_GROUP_BROWSER),
    _EnableTarget(
        BUILTIN_GROUP_KEYBOARD_MOUSE,
        ("desktop", "keyboard_mouse", "computer", "computer_use"),
        "desktop",
        "keyboard_mouse",
        ENABLE_KEYBOARD_MOUSE,
        BUILTIN_GROUP_KEYBOARD_MOUSE,
    ),
    _EnableTarget("plugins", ("plugins", "plugin", "user_plugin", "user_plugins"), "plugins", "plugin_entry", ENABLE_PLUGINS, None),
)
_ENABLE_NAMES = ", ".join(target.display for target in _ENABLE_TARGETS)
# What a task agent is told when the answer it was given no longer stands (not a refusal of the settings).
_APPROVAL_STALE = (
    "The earlier approval no longer stands because the run or the settings changed. "
    "Ask again if the task still needs it."
)


def _approval_stale_switched_on(display: str) -> str:
    """What a task agent is told when this call wrote the switch and the answer stopped standing after the write."""
    return (
        f"The {display} was turned on by this request and stays on, but the earlier approval no longer stands "
        "because the run or the settings changed, so it covers no call. Ask again with "
        f"{ENABLE_TOOL} if the task still needs it: it is not turned on again, and its use is asked about as usual."
    )


# What a failed start of the plugins says when it may still come up by itself.
_PLUGINS_PENDING_HINTS = ("still starting", "did not respond in time")
# What a search query says when it is about one of the things that may be asked for: whole words,
# and phrases (the Chinese words are looked for as they stand: they have no spaces to split on).
_OFF_QUERY_WORDS: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {
    "browser": (
        frozenset({"browser", "web", "webpage", "website", "url", "http", "https", "site", "tab", "navigate", "html", "chrome", "edge"}),
        ("浏览器", "网页", "网站", "网址"),
    ),
    BUILTIN_GROUP_KEYBOARD_MOUSE: (
        frozenset({"desktop", "computer", "screen", "screenshot", "mouse", "keyboard", "click", "hotkey", "shortcut", "cursor", "window", "gui", "type", "typing"}),
        ("桌面", "屏幕", "截图", "鼠标", "键盘", "点击"),
    ),
    "plugins": (
        frozenset({"plugin", "plugins", "extension", "extensions", "mcp", "web_search"}),
        ("web search", "插件"),
    ),
}


def _target_named(asked: Any) -> _EnableTarget | None:
    wanted = str(asked or "").strip().lower()
    return next((target for target in _ENABLE_TARGETS if wanted in target.aliases), None) if wanted else None


def _target_of_group(group: str | None) -> _EnableTarget | None:
    return next((target for target in _ENABLE_TARGETS if group and target.builtin_group == group), None)


def _enable_how(display: str) -> str:
    return f'call {CALL_TOOL} with name "{ENABLE_TOOL}" and arguments {{"capability": "{display}"}}'


@dataclass(slots=True)
class _EnableGate:
    """What was found out about a request to turn something on, before the user is asked."""

    target: _EnableTarget | None = None
    commission: Commission | None = None
    # What the request is answered with when it is refused or settled without asking.
    answer: dict[str, Any] | None = None
    # It is on already.
    already: bool = False
    # Why the settings refused it, for the ledger.
    denied: str | None = None


def wants_confirmation(run_data: Any) -> bool:
    """Return whether a plugin result asks for the user's confirmation (not a clarification)."""
    if not isinstance(run_data, dict) or bool(run_data.get("observation_only")):
        return False
    status = str(run_data.get("status") or "").strip().lower()
    return bool(run_data.get("needs_confirmation")) or status == "confirm_required"


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class _Cleared:
    """What was found out about a call before it was made; see ``CapabilityGateway.clear_call``."""

    # What the call is answered with instead of being carried out, or None.
    refusal: dict[str, Any] | None = None
    reason: str | None = None
    # The user's agreement to this call opening a browser they closed, if one was needed.
    agreement: Any = None
    grant_id: str | None = None
    # Drops it when it has waited for its call long enough.
    expiry: asyncio.TimerHandle | None = None
    device_group: str | None = None
    # For a request to turn something on: whether what the user approved still stands, as it was
    # when they were asked (the run it was asked in, and the settings of what it was asked about).
    still_valid: Callable[[], bool] | None = None


def _call_key(capability_id: str, arguments: dict[str, Any]) -> tuple[str, str]:
    """Return what tells one call from another: the capability, and a digest of what it is called with."""
    try:
        text = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(arguments)
    return capability_id, hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


@dataclass(slots=True)
class Grant:
    """What one commission's token gives access to."""

    commission_id: str
    token: str
    # Plugins whose entries the commission's opening message tells of.
    candidate_plugin_ids: tuple[str, ...] = ()
    # Whether the model of this commission's task agent can see pictures.
    supports_images: bool = False
    # The token of a session that only reads and answers (a copy of another
    # session, in that one's working directory): capabilities are found
    # with it, and none is called or handed over.
    reads_only: bool = False
    # Requested for this commission, and the exact tools actually declared
    # to its runtime. A later switch-on still goes through discovery.
    first_turn_groups: tuple[str, ...] = ()
    mounted_groups: set[str] = field(default_factory=set)
    used_groups: set[str] = field(default_factory=set)
    active_groups: Counter[str] = field(default_factory=Counter)
    # A repeated tools/list for one summary must not consume a second cycle.
    folded_at: int = 0
    initial_devices: dict[str, str] | None = None
    declared_devices: set[str] = field(default_factory=set)
    # The groups of built-in tools that were handed over through this token:
    # how they are used and their tools' definitions. A new token starts
    # without any, as the session it is for may no longer hold them.
    loaded_groups: set[str] = field(default_factory=set)
    # The model reply during which each of them was handed over, as the
    # client names its replies (``GATEWAY_REPLY_META_KEY``); nothing for a
    # group handed to a client that names none.
    handed_in: dict[str, int | str] = field(default_factory=dict)
    # How often the client's conversation had been summed up as of its last
    # call (``GATEWAY_COMPACTIONS_META_KEY``); None for a client that says nothing of it.
    compactions: int | None = None
    calls: int = 0
    in_flight: set[asyncio.Task[Any]] = field(default_factory=set)
    # Calls ruled on before they were made, by capability and arguments
    # (``_call_key``); each is taken by the call it was ruled on for.
    cleared: dict[tuple[str, str], _Cleared] = field(default_factory=dict)

    @contextlib.contextmanager
    def using_device(self, name: str) -> Iterator[None]:
        group = self.initial_devices.get(name) if self.initial_devices else None
        if group is None:
            yield
            return
        self.active_groups[group] += 1
        try:
            yield
        finally:
            self.active_groups[group] -= 1
            if not self.active_groups[group]:
                del self.active_groups[group]


_READS_ONLY_TEXT = (
    "Nothing can be called from this session: it is a copy of another session and only reads and answers, "
    "while the other may be at work in the same place. Answer from what you have, or report what would "
    "have to be done."
)


def _text(text: str, *, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _reply_of(params: dict[str, Any]) -> int | str | None:
    """Return the model reply a ``tools/call`` says it was written in, if it names one."""
    from config.nekopower_settings import GATEWAY_REPLY_META_KEY

    meta = params.get("_meta")
    reply = meta.get(GATEWAY_REPLY_META_KEY) if isinstance(meta, dict) else None
    if isinstance(reply, bool) or not isinstance(reply, (int, str)):
        return None
    return reply


def _compactions_of(params: dict[str, Any]) -> int | None:
    """Return how often a ``tools/call`` says its conversation has been summed up, if it says."""
    from config.nekopower_settings import GATEWAY_COMPACTIONS_META_KEY

    meta = params.get("_meta")
    count = meta.get(GATEWAY_COMPACTIONS_META_KEY) if isinstance(meta, dict) else None
    if isinstance(count, bool) or not isinstance(count, int):
        return None
    return count


def _note_compactions(grant: Grant, compactions: int | None) -> None:
    """Take what was handed over as gone when the client's conversation was summed up since.

    The groups of built-in tools were handed over as text in the
    conversation; a summary does not keep that text. Each group is then
    handed over anew by the call that next needs it. A client that says
    nothing of summaries is taken to hold what it was handed.
    """
    if compactions is None or compactions < grant.folded_at:
        return
    if grant.compactions is not None and compactions != grant.compactions:
        grant.loaded_groups.clear()
        grant.handed_in.clear()
    grant.compactions = compactions


def _in_hand(grant: Grant, group: str, reply: int | str | None) -> bool:
    """Whether a call was written with a group in hand: handed over, and not in the reply the call is of."""
    if group not in grant.loaded_groups:
        return False
    return reply is None or grant.handed_in.get(group) != reply


def _hand_over(grant: Grant, group: str, reply: int | str | None) -> None:
    """Note that a group goes back with the answer to a call of ``reply``."""
    if group in grant.loaded_groups:
        # Handed over before (loaded a second time): when it first was stands.
        return
    grant.loaded_groups.add(group)
    if reply is not None:
        grant.handed_in[group] = reply


def _text_and_images(text: str, images: Any, *, error: bool = False) -> dict[str, Any]:
    """Return an MCP result of one text block followed by image blocks."""
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for image in images:
        content.append({"type": "image", "data": image.data, "mimeType": image.mime_type})
    return {"content": content, "isError": error}


def _group_name(group: str) -> str:
    """Return a group of built-in tools by the name a model knows it by."""
    from config.prompts.prompts_nekopower_builtin import BUILTIN_GROUP_NAMES

    return BUILTIN_GROUP_NAMES.get(group, group)


def _groups_of(capabilities: list[Capability]) -> list[str]:
    """Return the groups of the built-in tools among ``capabilities``, in the order a model is told of them."""
    from config.prompts.prompts_nekopower_builtin import BUILTIN_GROUP_NAMES

    # Only a built-in tool has a group.
    present = {item.group for item in capabilities if item.group}
    return [group for group in BUILTIN_GROUP_NAMES if group in present]


def _first_call(group: str, capabilities: list[Capability]) -> tuple[Capability, dict[str, Any]] | None:
    """Return the tool a group is started with and what is written of that call, if the tool is among ``capabilities``."""
    from config.prompts.prompts_nekopower_builtin import BUILTIN_GROUP_FIRST_CALLS

    first = BUILTIN_GROUP_FIRST_CALLS.get(group)
    if first is None:
        return None
    for item in capabilities:
        if item.group == group and item.tool_name == first["name"]:
            return item, first
    return None


def _search_tool_definition(capabilities: list[Capability], *, off_capabilities: Sequence[str] = ()) -> dict[str, Any]:
    """Return the search tool as offered to a task agent that can call ``capabilities``.

    ``off_capabilities`` are the things it may ask the user to turn on
    (``browser``, ``desktop``, ``plugins``): with any, one line says how.
    With none, the definition is exactly what it was.
    """
    definition = _search_tool_base(capabilities)
    if not off_capabilities:
        return definition
    note = (
        f"Switched off; to ask the user to turn one on, {_enable_how('<name>')}: "
        f"{', '.join(off_capabilities)}."
    )
    return {**definition, "description": f"{definition['description']}\n{note}"}


def _search_tool_base(capabilities: list[Capability]) -> dict[str, Any]:
    """Return the search tool as offered to a task agent that can call ``capabilities``.

    With a built-in tool among them, its description ends in one line
    about each of their groups, with the group's first call, which is all a
    request carries of a group that was not loaded. Without one it says
    nothing of groups, and still takes ``group``.

    ``group`` is any string, never one of a list: a session keeps the
    definition it was opened with, a group that is switched on while it
    lasts is named by a search result (``TOOL_GROUP_POINTER``), and
    loading it with ``group`` alone has to be a call the definition
    allows. Whether there is such a group is told when it is asked for.
    """
    from config.prompts.prompts_nekopower_builtin import (
        BUILTIN_GROUP_SUMMARIES,
        TOOL_GROUP_ARGUMENT,
        TOOL_GROUP_ARGUMENT_UNNAMED,
        TOOL_GROUP_FIRST_CALL,
        TOOL_GROUPS_INTRO,
    )

    groups = _groups_of(capabilities)
    if not groups:
        schema = _SEARCH_TOOL_DEFINITION["inputSchema"]
        properties = {**schema["properties"], "group": {"type": "string", "description": TOOL_GROUP_ARGUMENT_UNNAMED}}
        return {**_SEARCH_TOOL_DEFINITION, "inputSchema": {**schema, "properties": properties}}

    lines = [str(_SEARCH_TOOL_DEFINITION["description"]), TOOL_GROUPS_INTRO.format(call_tool=CALL_TOOL)]
    for group in groups:
        # Told only when the tool is there to be called.
        found = _first_call(group, capabilities)
        how = TOOL_GROUP_FIRST_CALL.format(name=found[0].tool_name, example=found[1]["example"]) if found else ""
        lines.append(f"- {_group_name(group)}: {BUILTIN_GROUP_SUMMARIES[group]}{how}")
    return {
        "name": SEARCH_TOOL,
        "description": "\n".join(lines),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What the capability should do."},
                "group": {"type": "string", "description": TOOL_GROUP_ARGUMENT},
            },
        },
    }


def _is_first_call(capability: Capability, arguments: dict[str, Any]) -> bool:
    """Whether a call is the first call of its group: the one carried out although the group was not loaded."""
    found = _first_call(capability.group or "", [capability])
    if found is None:
        return False
    first = found[1]
    # Only the step itself: the tool can do more under the same name.
    if any(arguments.get(key) not in allowed for key, allowed in first["arguments"].items()):
        return False
    return all(isinstance(arguments.get(key), str) and arguments[key].strip() for key in first["given"])


def _group_text(group: str, tools: list[Capability]) -> str:
    """Return what loading a group hands over: how it is used, then its tools' definitions."""
    from config.nekopower_settings import GATEWAY_TOOL_GROUP_MAX_TOKENS
    from config.prompts.prompts_nekopower_builtin import BUILTIN_GROUP_GUIDANCE, TOOL_GROUP_LOADED_HEADER
    from utils.tokenize import truncate_to_tokens

    parts = [
        TOOL_GROUP_LOADED_HEADER.format(name=_group_name(group), call_tool=CALL_TOOL),
        BUILTIN_GROUP_GUIDANCE[group],
        "\n".join(json.dumps(_definition(capability), ensure_ascii=False) for capability in tools),
    ]
    text = "\n\n".join(parts)
    cut = truncate_to_tokens(text, GATEWAY_TOOL_GROUP_MAX_TOKENS)
    if len(cut) < len(text):
        # Ours never are (a test keeps them inside the cap); said for a
        # provider whose definitions are larger.
        logger.warning("the tools of the group %s do not fit what loading a group may hand over; cut", group)
    return cut


def _definition(capability: Capability) -> dict[str, Any]:
    from config.nekopower_settings import GATEWAY_TOOL_DESCRIPTION_MAX_TOKENS
    from utils.tokenize import truncate_to_tokens

    if capability.kind == KIND_BUILTIN:
        # Our own definitions, served as written: the name says what it is.
        return {
            "name": capability.tool_name,
            "description": capability.description,
            "inputSchema": capability.input_schema,
        }
    description = capability.description or capability.title
    return {
        "name": capability.tool_name,
        "description": truncate_to_tokens(f"{capability.title}. {description}", GATEWAY_TOOL_DESCRIPTION_MAX_TOKENS),
        "inputSchema": capability.input_schema,
    }


class CapabilityGateway:
    """Serves capabilities to task agents. See the module docstring."""

    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        runner: PluginRunner,
        permissions: PermissionGate,
        ledger: Ledger,
        ask_user: AskUser | None = None,
        leases: BuiltinLeases | None = None,
        capability_enabler: CapabilityEnabler | None = None,
    ) -> None:
        self._registry = registry
        self._runner = runner
        self._permissions = permissions
        self._ledger = ledger
        self._ask_user = ask_user
        # Who turns on what a task agent finds switched off; without it a
        # task agent is not told it may ask.
        self._enabler = capability_enabler
        # Who occupies the desktop and the browser; without it nothing is
        # occupied and every call of a built-in tool goes straight through.
        self._leases = leases
        self._grants: dict[str, Grant] = {}  # by token
        self._by_commission: dict[str, Grant] = {}
        self._server: Any = None
        self._serve_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._port: int | None = None

    def set_ask_user(self, ask_user: AskUser | None) -> None:
        """Set how a plugin's request for confirmation is put to the user."""
        self._ask_user = ask_user

    @property
    def can_enable_capabilities(self) -> bool:
        """Whether a task agent may ask the user to turn on what it finds switched off."""
        return self._enabler is not None

    def can_enable_for(self, commission_id: str) -> bool:
        """Whether this commission's task agent may ask the user to turn something on.

        Not a reads-only copy, and not a commission that has no token here (it has no call tool to ask with).
        """
        if self._enabler is None:
            return False
        grant = self._by_commission.get(commission_id)
        return grant is not None and not grant.reads_only

    def _enabler_allows_request(self, canonical: str) -> bool:
        """Whether what turns the switches on says this one may be asked for here (not, say, a screen the user must share first)."""
        if self._enabler is None:
            return False
        check = getattr(self._enabler, "may_request", None)
        return bool(check(canonical)) if callable(check) else True

    async def _requestable_off_capabilities(
        self, grant: Grant, callable_now: list[Capability] | None = None
    ) -> list[str]:
        """Return what this commission may ask the user to turn on, by the names a model knows: browser, desktop, plugins.

        Only what is switched off (not what cannot be used on this
        computer), that the settings do not forbid, that the commission's
        model can make something of, and only for a commission that may
        call things at all.
        """
        if self._enabler is None or grant.reads_only:
            return []
        if callable_now is None:
            callable_now = await self._registry.callable_by_task_agents()
        on = set(_groups_of(callable_now))
        off_groups = {
            item.group
            for item in self._registry.builtins()
            if not item.available and item.unavailable_reason == "switched off"
        }
        found: list[str] = []
        for target in _ENABLE_TARGETS:
            if target.builtin_group is not None:
                if target.builtin_group in on or target.builtin_group not in off_groups:
                    continue
                if target.canonical == BUILTIN_GROUP_KEYBOARD_MOUSE and not grant.supports_images:
                    continue
            elif self._registry.plugins_enabled():
                continue
            if not self._enabler_allows_request(target.canonical):
                continue
            if await self._permissions.forbidden(target.permission):
                continue
            found.append(target.display)
        return found

    # -- tokens -----------------------------------------------------------

    def issue(
        self,
        commission_id: str,
        *,
        candidate_plugin_ids: Any = (),
        supports_images: bool = False,
        reads_only: bool = False,
        first_turn_groups: tuple[str, ...] = (),
    ) -> str:
        """Return a new token for a commission, replacing any earlier one.

        ``supports_images`` says whether the model of the commission's task
        agent can see pictures; tools that need that are offered only then.
        With ``reads_only`` the token finds capabilities and calls none:
        for a session that is not to change anything (``Grant.reads_only``).
        """
        self.revoke(commission_id)
        grant = Grant(
            commission_id=commission_id,
            token=secrets.token_urlsafe(32),
            candidate_plugin_ids=tuple(str(item) for item in candidate_plugin_ids),
            supports_images=bool(supports_images),
            reads_only=bool(reads_only),
            first_turn_groups=() if reads_only else first_turn_groups,
            mounted_groups=set() if reads_only else set(first_turn_groups),
        )
        self._grants[grant.token] = grant
        self._by_commission[commission_id] = grant
        return grant.token

    def images_refused(self, commission_id: str) -> None:
        """The model of a commission's task agent turned out not to take pictures.

        The tools that work from pictures are no longer offered to it nor
        carried out for it: it would go on clicking a screen it cannot see.
        """
        grant = self._by_commission.get(commission_id)
        if grant is not None:
            grant.supports_images = False

    def revoke(self, commission_id: str) -> None:
        """Make a commission's token useless and stop its calls that are still going."""
        grant = self._by_commission.pop(commission_id, None)
        if grant is None:
            return
        self._grants.pop(grant.token, None)
        for task in list(grant.in_flight):
            task.cancel()
        for key in list(grant.cleared):
            self._drop_cleared(grant, key)

    def _grant(self, token: str | None) -> Grant | None:
        if not token:
            return None
        for known, grant in self._grants.items():
            if hmac.compare_digest(known, token):
                return grant
        return None

    # -- listener ---------------------------------------------------------

    @property
    def started(self) -> bool:
        return self._port is not None

    @property
    def port(self) -> int | None:
        """Return the port the gateway listens on, or None while it does not listen."""
        return self._port

    @property
    def url(self) -> str:
        if self._port is None:
            raise RuntimeError("the gateway is not listening")
        return f"http://127.0.0.1:{self._port}{MCP_PATH}"

    async def start(self) -> str:
        """Start listening if not yet, and return the endpoint URL."""
        async with self._start_lock:
            if self._port is not None:
                return self.url
            import uvicorn

            config = uvicorn.Config(
                self._asgi,
                host="127.0.0.1",
                port=0,
                log_config=None,
                access_log=False,
                lifespan="off",
                interface="asgi3",
                proxy_headers=False,
            )
            server = uvicorn.Server(config)

            @contextlib.contextmanager
            def no_signals() -> Any:
                yield

            # The process's signal handlers belong to the servers that own the process.
            server.capture_signals = no_signals  # type: ignore[method-assign]
            task = asyncio.ensure_future(server.serve())
            loop = asyncio.get_running_loop()
            deadline = loop.time() + _START_TIMEOUT_SECONDS
            try:
                while not server.started:
                    if task.done():
                        error = task.exception()
                        raise RuntimeError(f"the gateway could not start: {error}")
                    if loop.time() > deadline:
                        raise RuntimeError("the gateway did not start in time")
                    await asyncio.sleep(0.01)
            except BaseException:
                # Not registered yet, so ``stop`` could never reach it: a
                # listener that did not start, or whose start was given up
                # on, is closed here.
                await _close_listener(server, task)
                raise
            sockets = [sock for listener in server.servers for sock in listener.sockets]
            self._port = int(sockets[0].getsockname()[1])
            self._server = server
            self._serve_task = task
            return self.url

    async def stop(self) -> None:
        """Stop listening and drop every token."""
        for commission_id in list(self._by_commission):
            self.revoke(commission_id)
        server, self._server = self._server, None
        task, self._serve_task = self._serve_task, None
        self._port = None
        await _close_listener(server, task)

    # -- HTTP -------------------------------------------------------------

    async def _asgi(self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        if scope["type"] != "http":
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}

        async def respond(status: int, body: Any = None, extra: dict[str, str] | None = None) -> None:
            payload = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
            response_headers = [(b"content-length", str(len(payload)).encode())]
            if payload:
                response_headers.append((b"content-type", b"application/json"))
            for name, value in (extra or {}).items():
                response_headers.append((name.encode(), value.encode()))
            await send({"type": "http.response.start", "status": status, "headers": response_headers})
            await send({"type": "http.response.body", "body": payload})

        if scope.get("path") != MCP_PATH:
            await respond(404, {"error": "not found"})
            return
        if scope.get("method") != "POST":
            await respond(405, {"error": "use POST"}, {"allow": "POST"})
            return
        # A page in a browser cannot be allowed to drive this, even with a token.
        origin = headers.get("origin")
        if origin is not None and _origin_host(origin) not in _LOOPBACK_HOSTS:
            await respond(403, {"error": "forbidden origin"})
            return
        authorization = headers.get("authorization", "")
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else None
        grant = self._grant(token)
        if grant is None:
            await respond(401, {"error": "unknown or missing token"}, {"www-authenticate": "Bearer"})
            return

        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        try:
            payload = json.loads(b"".join(chunks).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            await respond(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            return
        # The token may have been revoked or replaced while the body arrived;
        # such a request is not yet among the calls ``revoke`` stops.
        if self._grants.get(grant.token) is not grant:
            await respond(401, {"error": "unknown or missing token"}, {"www-authenticate": "Bearer"})
            return

        work = asyncio.ensure_future(self.handle(grant, payload))
        grant.in_flight.add(work)
        disconnect = asyncio.ensure_future(_wait_disconnect(receive))
        try:
            await asyncio.wait({work, disconnect}, return_when=asyncio.FIRST_COMPLETED)
            if not work.done():
                # The task agent gave up on this call (its run was stopped):
                # stop the work, which also cancels a plugin run under way.
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
                return
            try:
                answer = work.result()
            except asyncio.CancelledError:
                await respond(499, {"error": "cancelled"})
                return
        finally:
            grant.in_flight.discard(work)
            disconnect.cancel()
            if not work.done():
                # The listener itself is going away.
                work.cancel()
        if answer is None:
            await respond(202)
        else:
            await respond(200, answer)

    # -- JSON-RPC ---------------------------------------------------------

    async def handle(self, grant: Grant, payload: Any) -> Any:
        """Answer one JSON-RPC message, or a list of them. None for notifications."""
        if isinstance(payload, list):
            answers = [answer for answer in [await self._handle_one(grant, item) for item in payload] if answer]
            return answers or None
        return await self._handle_one(grant, payload)

    async def _handle_one(self, grant: Grant, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        request_id = message.get("id")
        is_notification = "id" not in message
        params = message.get("params")
        params = params if isinstance(params, dict) else {}
        try:
            result = await self._dispatch(grant, message["method"], params)
        except JsonRpcError as exc:
            if is_notification:
                return None
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": exc.code, "message": exc.message}}
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported to the caller, never raised into the server
            logger.exception("gateway request %s failed", message.get("method"))
            if is_notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
            }
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    async def _dispatch(self, grant: Grant, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
            return {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": "1"},
            }
        if method.startswith("notifications/"):
            return None
        if method == "ping":
            return {}
        if method == "tools/list":
            from config.nekopower_settings import GATEWAY_DEVICE_INTENT_META_KEY, GATEWAY_IDLE_DEVICE_CYCLE_META_KEY

            meta = params.get("_meta")
            asked = meta.get(GATEWAY_DEVICE_INTENT_META_KEY) if isinstance(meta, dict) else None
            wanted = {g for g in asked if isinstance(g, str)} if isinstance(asked, list) else set()
            idle_cycle = isinstance(meta, dict) and meta.get(GATEWAY_IDLE_DEVICE_CYCLE_META_KEY) is True
            return {"tools": await self.tool_definitions(
                grant, compactions=_compactions_of(params), wanted=wanted, idle_device_cycle=idle_cycle
            )}
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or not name:
                raise JsonRpcError(-32602, "tools/call needs a tool name")
            arguments = params.get("arguments")
            return await self.call_tool(
                grant,
                name,
                arguments if isinstance(arguments, dict) else {},
                reply=_reply_of(params),
                compactions=_compactions_of(params),
            )
        raise JsonRpcError(-32601, f"unknown method {method}")

    # -- tools ------------------------------------------------------------

    async def tool_definitions(
        self, grant: Grant, *, compactions: int | None = None, wanted: set[str] | None = None,
        idle_device_cycle: bool = False,
    ) -> list[dict[str, Any]]:
        """Offer discovery and call to every session, plus devices selected for this commission."""
        from config.nekopower_settings import GATEWAY_COMMISSION_TOOL_META_KEY, GATEWAY_DEVICE_INTENT_META_KEY
        from config.prompts.prompts_nekopower_builtin import BUILTIN_GROUP_GUIDANCE

        requested_at = grant.folded_at
        # Read before the cycle is rotated below, with the registry: nothing is awaited from there on.
        off_capabilities = await self._requestable_off_capabilities(grant)
        callable_now = self._offered(grant, await self._registry.callable_by_task_agents())
        # Read the registry before rotating the cycle. No await from here
        # through the returned declarations, including on simultaneous lists.
        if compactions is not None and compactions > grant.folded_at:
            grant.folded_at = compactions
            grant.loaded_groups.clear()
            grant.handed_in.clear()
            grant.compactions = compactions
            waiting = {decision.device_group for decision in grant.cleared.values() if decision.refusal is None}
            keep = grant.active_groups.keys() | waiting
            if not idle_device_cycle:
                keep.update(grant.used_groups)
            grant.mounted_groups.intersection_update(keep)
            grant.used_groups.clear()
        epoch = requested_at if compactions is None else compactions
        if wanted and epoch >= grant.folded_at:
            grant.mounted_groups.update(wanted.intersection(grant.first_turn_groups))
        listed = [_search_tool_definition(callable_now, off_capabilities=off_capabilities), _CALL_TOOL_DEFINITION]
        grant.declared_devices.clear()
        described: set[str] = set()
        for item in callable_now:
            if item.kind != KIND_BUILTIN or item.group not in grant.mounted_groups:
                continue
            if grant.initial_devices is not None and item.tool_name not in grant.initial_devices:
                continue
            tool = _definition(item)
            if item.group not in described:
                tool["description"] += "\n\n" + BUILTIN_GROUP_GUIDANCE[item.group]
                described.add(item.group)
            meta = {GATEWAY_COMMISSION_TOOL_META_KEY: True, GATEWAY_DEVICE_INTENT_META_KEY: item.group}
            listed.append({**tool, "_meta": meta})
            grant.declared_devices.add(item.tool_name)
        if grant.initial_devices is None:
            grant.initial_devices = {
                item.tool_name: item.group for item in callable_now if item.tool_name in grant.declared_devices and item.group
            }
        return listed

    async def candidate_definitions(self, commission_id: str) -> list[dict[str, Any]]:
        """Return the entries picked for a commission, as its opening message tells of them.

        The entries of the candidate plugins its token was issued with, as
        far as their budget reaches (``GATEWAY_TOOL_LIST_MAX_TOKENS``), each
        with its name, what it does and its arguments. They are called by
        name through the call tool. Empty for a commission without a token
        or without candidates.
        """
        from config.nekopower_settings import GATEWAY_TOOL_LIST_MAX_TOKENS
        from utils.tokenize import count_tokens

        grant = self._by_commission.get(commission_id)
        if grant is None or not grant.candidate_plugin_ids:
            return []
        candidates = set(grant.candidate_plugin_ids)
        listed: list[dict[str, Any]] = []
        used = 0
        for capability in self._offered(grant, await self._registry.callable_by_task_agents()):
            # Plugin entries, and MCP tools when the adapter was among the
            # candidates. (A built-in tool belongs to no plugin, so it is
            # never one of them.)
            if capability.plugin_id not in candidates:
                continue
            definition = _definition(capability)
            cost = count_tokens(json.dumps(definition, ensure_ascii=False))
            if used + cost > GATEWAY_TOOL_LIST_MAX_TOKENS:
                continue
            listed.append(definition)
            used += cost
        return listed

    @staticmethod
    def _offered(grant: Grant, capabilities: list[Capability]) -> list[Capability]:
        """Leave out the tools this commission's model can make nothing of."""
        if grant.supports_images:
            return capabilities
        return [item for item in capabilities if not item.needs_images]

    async def call_tool(
        self,
        grant: Grant,
        name: str,
        arguments: dict[str, Any],
        *,
        reply: int | str | None = None,
        compactions: int | None = None,
    ) -> dict[str, Any]:
        """Carry out one ``tools/call`` and return its MCP result.

        ``reply`` is the model reply the call was written in, as the client
        names it (``GATEWAY_REPLY_META_KEY``), ``None`` for a client that
        names none. ``compactions`` is how often the client's conversation
        has been summed up (``GATEWAY_COMPACTIONS_META_KEY``), likewise.
        """
        grant.calls += 1
        _note_compactions(grant, compactions)
        if name == SEARCH_TOOL:
            group = arguments.get("group")
            if isinstance(group, str) and group.strip():
                if grant.reads_only:
                    # What a group hands over is how to call its tools.
                    return _text(_READS_ONLY_TEXT, error=True)
                return await self._load_group(grant, group.strip(), reply)
            return _text(await self._search(grant, str(arguments.get("query") or "")))
        if grant.reads_only:
            # Whatever is named, through the call tool or by itself: here,
            # where every call comes by, and not by the tools a session is
            # opened with alone.
            return _text(_READS_ONLY_TEXT, error=True)
        if name == CALL_TOOL:
            target = arguments.get("name")
            if not isinstance(target, str) or not target:
                return _text(f"{CALL_TOOL} needs the name of a capability", error=True)
            inner = arguments.get("arguments")
            name, arguments = target, inner if isinstance(inner, dict) else {}
        # Hold a native group from receipt, including while the registry
        # and the ledger are read. Concurrent tools/list may run there.
        with grant.using_device(name):
            return await self._call_named(grant, name, arguments, reply)

    async def _call_named(
        self, grant: Grant, name: str, arguments: dict[str, Any], reply: int | str | None
    ) -> dict[str, Any]:
        if name == ENABLE_TOOL:
            return await self._call_enable(grant, arguments, reply)
        callable_now = await self._registry.callable_by_task_agents()
        capability = next((item for item in self._offered(grant, callable_now) if item.tool_name == name), None)
        if capability is None:
            if any(item.tool_name == name for item in callable_now):
                # Left out by ``_offered``: it works from pictures.
                return _text(
                    f"{name} works from pictures, and the model in use does not take pictures: it cannot be used "
                    "in this task. Go on without it, or report that the task needs a model that can see.",
                    error=True,
                )
            switched_off = await self._switched_off_tool(grant, name)
            if switched_off is not None:
                return _text(
                    f"{name} is one of the {switched_off.display} tools, which are switched off right now. "
                    f"If this task needs them, {_enable_how(switched_off.display)} to ask the user to turn them on.",
                    error=True,
                )
            return _text(f"no capability named {name}; use {SEARCH_TOOL} to find one", error=True)
        commission = await self._ledger.get(grant.commission_id)
        if commission is None or not commission.active:
            return _text("this commission is no longer under way", error=True)
        group = capability.group
        if not group or capability.tool_name in grant.declared_devices or _in_hand(grant, group, reply):
            return await self._call_capability(commission, capability, arguments)
        # A tool of a group the task agent did not have in hand when it
        # wrote this call: how the group is used has not been read, and but
        # for the group's first call, which the task agent was shown, the
        # arguments were written without the tool's definition. Either way
        # the group goes back with the answer, so the calls of the replies
        # after this one go through.
        from config.prompts.prompts_nekopower_builtin import (
            TOOL_GROUP_FIRST_RESULT,
            TOOL_GROUP_NOT_LOADED,
            TOOL_GROUP_NOT_READ,
        )

        first = _is_first_call(capability, arguments)
        if group in grant.loaded_groups:
            # Handed over by an earlier call of this same reply (the calls
            # of one reply are carried out one after the other): the model
            # wrote this call before that answer reached it. The first call
            # needs no reading; anything else waits for the next reply, and
            # the group is not sent again.
            if first:
                return await self._call_capability(commission, capability, arguments)
            return _text(TOOL_GROUP_NOT_READ.format(tool=name, name=_group_name(group)), error=True)
        handed_over = _group_text(group, [item for item in self._offered(grant, callable_now) if item.group == group])
        if not first:
            # Not carried out: nothing is asked of the user, nothing is occupied.
            _hand_over(grant, group, reply)
            refusal = TOOL_GROUP_NOT_LOADED.format(tool=name, name=_group_name(group))
            return _text(f"{refusal}\n\n{handed_over}", error=True)
        # The first call (going to an address, taking a screenshot) is
        # carried out like any call, the user asked first and the group
        # occupied. The group is put in front of what it returns, as
        # a block of its own: each is capped by itself, and the runtime
        # keeps them apart (``GatewayResult.parts``), so that when it cuts
        # old results down the group stays whole next to a long page.
        result = await self._call_capability(commission, capability, arguments)
        if result.get("isError"):
            # A first call that failed (declined, the browser occupied, a
            # page that would not load) comes back as any failed call does,
            # and the group is still to be handed over: a runtime keeps an
            # error as one text, in which the group would be cut together
            # with whatever the error says.
            return result
        # Only now, with the answer on its way: a call that was stopped
        # handed nothing over.
        _hand_over(grant, group, reply)
        opening = {"type": "text", "text": f"{handed_over}\n\n{TOOL_GROUP_FIRST_RESULT}"}
        return {**result, "content": [opening, *result["content"]]}

    async def _load_group(self, grant: Grant, asked: str, reply: int | str | None = None) -> dict[str, Any]:
        """Hand a group of built-in tools over: how it is used and its tools' definitions.

        ``asked`` is the group as a model names it (the name the search
        tool's description gives it; the host's own name for it is taken
        too). What is handed over are the tools this commission's model can
        make something of. ``reply`` is the model reply the load was
        written in (see ``call_tool``): the group is in hand from the next.
        """
        from config.prompts.prompts_nekopower_builtin import BUILTIN_GROUP_NAMES

        wanted = asked.lower()
        callable_now = await self._registry.callable_by_task_agents()
        if (
            wanted in _ENABLE_TARGETS[-1].aliases
            and self._enabler is not None
            and not self._registry.plugins_enabled()
            and self._enabler_allows_request(_ENABLE_TARGETS[-1].canonical)
            and not await self._permissions.forbidden(_ENABLE_TARGETS[-1].permission)
        ):
            # Not a group of built-in tools, but what a task agent may look for here all the same.
            return _text(
                f"The plugins are switched off right now. If this task needs them, {_enable_how('plugins')} "
                "to ask the user to turn them on.",
                error=True,
            )
        group = next((key for key, known in BUILTIN_GROUP_NAMES.items() if wanted in (key, known)), None)
        if group is None:
            on_offer = ", ".join(_group_name(item) for item in _groups_of(self._offered(grant, callable_now)))
            return _text(f"no tool group named {asked}; the groups on offer: {on_offer or 'none'}", error=True)
        name = _group_name(group)
        tools = [item for item in callable_now if item.group == group]
        if not tools:
            # A group there is, but none of its tools can be called now.
            reason = next((item.unavailable_reason for item in self._registry.builtins() if item.group == group), "")
            target = _target_of_group(group)
            if (
                reason == "switched off"
                and target is not None
                and self._enabler is not None
                and self._enabler_allows_request(target.canonical)
                and (target.canonical != BUILTIN_GROUP_KEYBOARD_MOUSE or grant.supports_images)
                and not await self._permissions.forbidden(target.permission)
            ):
                return _text(
                    f"The {name} tools are not available right now: switched off. If this task needs them, "
                    f"{_enable_how(name)} to ask the user to turn them on.",
                    error=True,
                )
            return _text(
                f"The {name} tools are not available right now: {reason or 'switched off'}. "
                "Go on without them, or report that the task needs them.",
                error=True,
            )
        offered = self._offered(grant, tools)
        if not offered:
            return _text(
                f"The {name} tools work from pictures, and the model in use does not take pictures: they cannot "
                "be used in this task. Go on without them, or report that the task needs a model that can see.",
                error=True,
            )
        _hand_over(grant, group, reply)
        result = _text(_group_text(group, offered))
        if group in grant.first_turn_groups and group not in grant.mounted_groups:
            from config.nekopower_settings import GATEWAY_DEVICE_INTENT_META_KEY

            grant.mounted_groups.add(group)
            result["_meta"] = {GATEWAY_DEVICE_INTENT_META_KEY: group}
        return result

    async def _search(self, grant: Grant, query: str) -> str:
        from config.nekopower_settings import GATEWAY_RESULT_MAX_TOKENS, GATEWAY_SEARCH_RESULTS
        from config.prompts.prompts_nekopower_builtin import BUILTIN_GROUP_SUMMARIES, TOOL_GROUP_POINTER
        from utils.tokenize import truncate_to_tokens

        capabilities = self._offered(grant, await self._registry.callable_by_task_agents())
        off = await self._requestable_off_capabilities(grant, capabilities)
        # Said where nothing is found, or where what is found is not what the query is about
        # because it is switched off.
        note = (
            f"\nSwitched off right now: {', '.join(off)}. If this task needs one, {_enable_how('<name>')} "
            "to ask the user to turn it on."
            if off
            else ""
        )
        if not capabilities:
            # The two tools are there whether or not anything can be called,
            # and the session goes on after this answer: what it says holds
            # for now only.
            return (
                "No capabilities are available right now. That can change while you work (a plugin gets "
                "installed, the browser gets switched on): search again when a later commission needs one."
                + note
            )
        # A built-in tool is not found on its own: its definition is of no
        # use without how its group is used, and the rest of the group is
        # needed with it. A group is searched as one thing, by all its tools
        # say, and a match names the group and how to load it.
        found: list[Capability | str] = [item for item in capabilities if not item.group]
        searched = [f"{item.title} {item.description} {item.tool_name}" for item in found]
        for group in _groups_of(capabilities):
            tools = [item for item in capabilities if item.group == group]
            found.append(group)
            said = [_group_name(group), BUILTIN_GROUP_SUMMARIES[group]]
            said.extend(f"{item.tool_name} {item.description}" for item in tools)
            searched.append(" ".join(said))
        ranked = sorted(zip(bm25_scores(query, searched), found, strict=True), key=lambda pair: pair[0], reverse=True)
        matches = [item for score, item in ranked if score > 0][:GATEWAY_SEARCH_RESULTS]
        if not matches:
            return f"No capability matches {query!r}." + note
        blocks = [
            TOOL_GROUP_POINTER.format(
                name=_group_name(item), summary=BUILTIN_GROUP_SUMMARIES[item], search_tool=SEARCH_TOOL
            )
            if isinstance(item, str)
            else json.dumps(_definition(item), ensure_ascii=False)
            for item in matches
        ]
        if note and self._off_matches(off, query):
            blocks.append(note.strip())
        return truncate_to_tokens("\n".join(blocks), GATEWAY_RESULT_MAX_TOKENS)

    def _off_matches(self, off: list[str], query: str) -> bool:
        """Whether a query is about one of the switched-off things that may be asked for.

        By a few words of each: matching a query against the tools' own
        descriptions took in every common word ("the", "to") and put the
        note on nearly every search.
        """
        import re

        lowered = query.lower()
        words = set(re.findall(r"[a-z0-9_]+", lowered))
        # A tool name is one word to the pattern above (``browser_navigate_page``): its parts count too.
        words |= {part for word in tuple(words) for part in word.split("_") if part}
        for display in off:
            target = next((item for item in _ENABLE_TARGETS if item.display == display), None)
            if target is None:
                continue
            ascii_words, phrases = _OFF_QUERY_WORDS.get(target.canonical, (frozenset(), ()))
            if words & ascii_words or any(phrase in lowered for phrase in phrases):
                return True
        return False

    async def _switched_off_tool(self, grant: Grant, name: str) -> _EnableTarget | None:
        """Return what a call of a built-in tool that is switched off may ask the user to turn on; None if not that."""
        if self._enabler is None:
            return None
        for item in self._registry.builtins():
            if item.tool_name != name or item.available or item.unavailable_reason != "switched off":
                continue
            target = _target_of_group(item.group)
            if (
                target is None
                or (target.canonical == BUILTIN_GROUP_KEYBOARD_MOUSE and not grant.supports_images)
                or not self._enabler_allows_request(target.canonical)
            ):
                return None
            return None if await self._permissions.forbidden(target.permission) else target
        return None

    async def clear_call(self, commission_id: str, name: str, arguments: dict[str, Any]) -> None:
        """Rule on a call before it is made, asking whoever has to be asked.

        ``name`` and ``arguments`` are those of the ``tools/call`` that is
        about to come. The permission the call needs is decided now, and for
        a browser the user closed they are asked now whether it may be
        opened again; the commission waits for the user meanwhile, for as
        long as it takes. What came of it waits for the call
        (``GATEWAY_CLEARED_CALL_SECONDS``), which then neither decides nor
        asks again: carried out, or answered with the refusal, in the words
        a call refused at the entry is answered with.

        It is for that one call: the same capability with the same
        arguments. A call with other arguments does not take it and is ruled
        on at the entry, so an approval "for this once" never covers a call
        the user was not asked about. When its time is up it is dropped, and
        an agreement to open the browser with it, whether or not a call
        comes by. (dsh, the one runtime that asks ahead, asks about a call
        and carries it out before it asks about the next: it runs the tools
        of a service one at a time. Should the same call be ruled on twice
        before it comes, the later ruling replaces the earlier.)

        Nothing is ruled on for a call that will carry nothing out: the
        search tool, an unknown capability, a tool of a group that the call
        will hand over instead.
        """
        grant = self._by_commission.get(commission_id)
        if grant is None or grant.reads_only or name == SEARCH_TOOL:
            return
        if name == CALL_TOOL:
            target = arguments.get("name")
            if not isinstance(target, str) or not target:
                return
            inner = arguments.get("arguments")
            name, arguments = target, inner if isinstance(inner, dict) else {}
        if name == ENABLE_TOOL:
            await self._clear_enable(grant, arguments)
            return
        with grant.using_device(name):
            await self._clear_named(grant, name, arguments)

    async def _enable_gate(
        self, grant: Grant, arguments: dict[str, Any], *, check_enabler: bool = True
    ) -> _EnableGate:
        """Find out what a request to turn something on comes to before the user is asked about it.

        Nothing is asked of the user, and nothing is changed, for what is
        refused or settled here: an unknown name, nobody to turn it on, an
        ended commission, a model that cannot see (for the desktop), what is
        on already, what the settings forbid, what cannot be turned on on
        this computer.
        """
        asked = arguments.get("capability")
        target = _target_named(asked)
        if target is None:
            return _EnableGate(
                answer=_text(f'nothing named "{asked}" can be turned on; the ones that can: {_ENABLE_NAMES}', error=True)
            )
        if self._enabler is None:
            return _EnableGate(
                target=target, answer=_text("turning capabilities on is not offered by this process", error=True)
            )
        commission = await self._ledger.get(grant.commission_id)
        if commission is None or not commission.active:
            return _EnableGate(
                target=target, answer=_text("this commission is no longer under way", error=True)
            )
        gate = _EnableGate(target=target, commission=commission)
        if target.canonical == BUILTIN_GROUP_KEYBOARD_MOUSE and not grant.supports_images:
            gate.answer = _text(
                f"The {target.display} tools work from pictures, and the model in use does not take pictures: they "
                "cannot be used in this task. Go on without them, or report that the task needs a model that can see.",
                error=True,
            )
            return gate
        if target.builtin_group is not None:
            gate.already = any(
                item.group == target.builtin_group for item in await self._registry.callable_by_task_agents()
            )
        else:
            gate.already = self._registry.plugins_enabled()
        if gate.already:
            return gate
        if await self._permissions.forbidden(target.permission):
            gate.answer, gate.denied = _text(DENIED_BY_SETTINGS, error=True), DENIED_BY_SETTINGS
            return gate
        if not check_enabler:
            # Ruled on ahead already (``clear_call``): whether it can be done is not asked a second time.
            return gate
        try:
            possible, why = await self._enabler.can_enable(target.canonical)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - not asked about what cannot be told to work
            logger.warning("looking whether %s can be turned on failed: %s", target.canonical, type(exc).__name__)
            possible, why = False, type(exc).__name__
        if not possible:
            gate.answer = _text(
                f"The {target.display} cannot be turned on: {why or 'unavailable'}. Go on without it, or report "
                "that the task needs it.",
                error=True,
            )
        return gate

    async def _permit_enable(self, commission: Commission, target: _EnableTarget) -> _Cleared:
        """Ask the user whether to turn something on; what the question says is the host's alone.

        What the answer stands on is taken before the user is asked
        (``_Cleared.still_valid``): the run this is asked in, and the
        settings of what it is asked about. Text another session adds
        meanwhile, or a setting tightened and put back, ends it: the
        approval is then no ground for turning anything on, nor for
        covering the call that follows.
        """
        in_this_run = self._permissions.current_run(commission.id)
        same_settings = self._permissions.current_rules(commission.id, target.permission)

        def still_valid() -> bool:
            return in_this_run() and same_settings()

        decision = await self._permissions.ask_each_time(commission.id, target.ask_category, detail={})
        if not decision.allow:
            await self._ledger.append_event(
                commission.id, "capability_denied", {"capability": f"host:enable:{target.canonical}", "reason": decision.reason}
            )
            if decision.reason == DENIED_BY_USER:
                said = f"The user declined to turn on the {target.display}. Nothing was changed."
                return _Cleared(refusal=_text(f"{said} Go on without it, or report that the task needs it.", error=True), reason="user")
            return _Cleared(
                refusal=_text(
                    f"The user could not be asked whether to turn on the {target.display}. Nothing was changed.",
                    error=True,
                ),
                reason="no_approval",
            )
        # The answer may have been long in coming: forbidden in the settings since, it stays forbidden.
        if await self._permissions.forbidden(target.permission):
            await self._ledger.append_event(
                commission.id,
                "capability_denied",
                {"capability": f"host:enable:{target.canonical}", "reason": DENIED_BY_SETTINGS},
            )
            return _Cleared(refusal=_text(DENIED_BY_SETTINGS, error=True), reason="settings")
        return _Cleared(still_valid=still_valid)

    async def _clear_enable(self, grant: Grant, arguments: dict[str, Any]) -> None:
        """Ask ahead of time what ``_call_enable`` would ask, for a runtime that gives a call little time."""
        from config.nekopower_settings import GATEWAY_CLEARED_CALL_SECONDS

        gate = await self._enable_gate(grant, arguments)
        if gate.answer is not None or gate.already or gate.target is None or gate.commission is None:
            return
        key = _call_key(_ENABLE_ID, {"capability": gate.target.canonical})
        cleared = await self._permit_enable(gate.commission, gate.target)
        if self._by_commission.get(grant.commission_id) is not grant:
            return
        self._drop_cleared(grant, key)
        cleared.expiry = asyncio.get_running_loop().call_later(
            GATEWAY_CLEARED_CALL_SECONDS, self._drop_cleared, grant, key
        )
        grant.cleared[key] = cleared

    async def _call_enable(self, grant: Grant, arguments: dict[str, Any], reply: int | str | None) -> dict[str, Any]:
        """Turn on the browser, the keyboard and mouse or the plugins, after the user said yes, and hand over what that gives.

        What is asked is the host's own question with no word of the task
        agent in it. What the settings forbid is not asked about and not done,
        nor is what cannot be done on this computer.
        """
        asked = arguments.get("capability")
        target = _target_named(asked)
        cleared = (
            self._take_cleared(grant.commission_id, _ENABLE_ID, {"capability": target.canonical})
            if target is not None
            else None
        )
        gate = await self._enable_gate(grant, arguments, check_enabler=cleared is None or cleared.refusal is None)
        if gate.target is None or gate.commission is None or self._enabler is None:
            return gate.answer or _text("this commission is no longer under way", error=True)
        target, commission = gate.target, gate.commission
        if gate.denied is not None and (cleared is None or cleared.reason != "settings"):
            # (Not twice: what was ruled on ahead has put a settings refusal on record already.)
            await self._ledger.append_event(
                commission.id, "capability_denied", {"capability": f"host:enable:{target.canonical}", "reason": gate.denied}
            )
        if gate.already:
            if target.builtin_group is not None:
                return await self._load_group(grant, target.display, reply)
            return _text(f"The plugins are on already. Use {SEARCH_TOOL} to find what they offer.")
        if gate.answer is not None:
            return gate.answer
        try:
            async with call_audit.call(self._ledger, commission.id, "gateway", f"host:enable:{target.canonical}") as record:
                if cleared is None:
                    cleared = await self._permit_enable(commission, target)
                if cleared.refusal is not None:
                    record.status, record.reason = "denied", cleared.reason
                    return cleared.refusal
                current = await self._ledger.get(commission.id)
                if current is None or not current.active:
                    return _text("this commission is no longer under way", error=True)
                # Everything that waits comes first (the registry may ask the plugin server): what the
                # answer stands on is looked at last, with nothing awaited between it and turning on.
                turned_on_meanwhile = target.builtin_group is not None and any(
                    item.group == target.builtin_group for item in await self._registry.callable_by_task_agents()
                )
                if await self._permissions.forbidden(target.permission):
                    await self._ledger.append_event(
                        commission.id,
                        "capability_denied",
                        {"capability": f"host:enable:{target.canonical}", "reason": DENIED_BY_SETTINGS},
                    )
                    record.status, record.reason = "denied", "settings"
                    return _text(DENIED_BY_SETTINGS, error=True)
                if cleared.still_valid is not None and not cleared.still_valid():
                    # Approved before text another session added, or before a setting was tightened
                    # (and put back): that answer is no ground for this. Nothing is turned on, and
                    # the settings do not forbid it now (looked at just above): it may be asked again.
                    await self._ledger.append_event(
                        commission.id,
                        "capability_denied",
                        {"capability": f"host:enable:{target.canonical}", "reason": "approval_stale"},
                    )
                    record.status, record.reason = "denied", "stale_approval"
                    return _text(_APPROVAL_STALE, error=True)
                # Whether this call wrote the switch itself (``CapabilityEnabler``).
                written: list[bool] = []
                if turned_on_meanwhile:
                    # Turned on meanwhile by someone else (the user at the panel): not turned on again.
                    done, why = True, ""
                else:
                    try:
                        done, why = await self._enabler.enable(
                            target.canonical,
                            lanlan_name=commission.lanlan_name,
                            still_valid=cleared.still_valid,
                            on_written=lambda: written.append(True),
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - the task agent is told and decides how to go on
                        logger.exception("turning on %s failed", target.canonical)
                        done, why = False, type(exc).__name__
                if cleared.still_valid is not None and not cleared.still_valid():
                    # The answer stopped standing while ``enable`` ran. Either the switch was not written
                    # for it, or it was turned on meanwhile by someone else, or this call wrote it and the
                    # answer fell after the write (while the plugins were waited for). In every case it is
                    # left as it is (not turned off), nothing is recorded as turned on for the task agent
                    # and no free call is lent; the last is told and recorded as what it is.
                    switched_on = bool(written) and done
                    forbidden_now = await self._permissions.forbidden(target.permission)
                    if forbidden_now:
                        reason = DENIED_BY_SETTINGS
                    else:
                        reason = "approval_stale_switched_on" if switched_on else "approval_stale"
                    payload: dict[str, Any] = {"capability": f"host:enable:{target.canonical}", "reason": reason}
                    if switched_on:
                        payload["switched_on"] = True
                    await self._ledger.append_event(commission.id, "capability_denied", payload)
                    record.status, record.reason = "denied", "settings" if forbidden_now else "stale_approval"
                    if forbidden_now:
                        return _text(DENIED_BY_SETTINGS, error=True)
                    if switched_on:
                        return _text(_approval_stale_switched_on(target.display), error=True)
                    return _text(_APPROVAL_STALE, error=True)
                if not done:
                    record.status = "failed"
                    if (
                        target.builtin_group is None
                        and self._registry.plugins_enabled()
                        and (not why or any(hint in why for hint in _PLUGINS_PENDING_HINTS))
                    ):
                        # The switch stays on for the plugin server to recover: not "could not be turned on".
                        return _text(
                            f"The plugins switch is on, but they are not ready yet: {why or 'still starting'}. "
                            f"Try {SEARCH_TOOL} again in a moment, or go on without them.",
                            error=True,
                        )
                    return _text(
                        f"The {target.display} could not be turned on: {why or 'unavailable'}. Go on without it, or "
                        "report that the task needs it.",
                        error=True,
                    )
                await self._ledger.append_event(commission.id, "capability_enabled", {"capability": target.canonical})
                return await self._handed_after_enable(grant, commission, target, reply, still_valid=cleared.still_valid)
        except (call_audit.AuditFull, call_audit.CommissionEnded) as exc:
            return _text(str(exc), error=True)

    async def _handed_after_enable(
        self,
        grant: Grant,
        commission: Commission,
        target: _EnableTarget,
        reply: int | str | None,
        *,
        still_valid: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Hand a task agent what it asked for, now that it is on: the group's tools, or the plugins' entries."""
        from config.nekopower_settings import GATEWAY_RESULT_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        if target.builtin_group is not None:
            # The user just said yes to this very group: the first call of the task agent is not asked again,
            # as long as that yes still stands (``_Cleared.still_valid``).
            await self._permissions.grant_run(
                commission.id,
                target.permission,
                scope=GrantScope(KIND_BUILTIN, target.builtin_group),
                still_valid=still_valid,
            )
            tools = [
                item
                for item in await self._registry.callable_by_task_agents()
                if item.group == target.builtin_group
            ]
            offered = self._offered(grant, tools)
            if not offered:
                return _text(
                    f"The {target.display} was turned on, but none of its tools can be used now.", error=True
                )
            _hand_over(grant, target.builtin_group, reply)
            return _text(f"The {target.display} is on now.\n\n{_group_text(target.builtin_group, offered)}")
        self._registry.invalidate()
        plugins = await self._registry.plugins()
        if not plugins:
            return _text("The plugins are on, but there is no plugin with an entry to call.")
        listed = "\n".join(
            f"- {plugin.get('name') or plugin.get('id')}: {plugin.get('short_description') or plugin.get('description') or ''}".rstrip(": ")
            for plugin in plugins
        )
        text = (
            f"The plugins are on now. Find their entries with {SEARCH_TOOL} and call them with {CALL_TOOL}. "
            f"Plugins:\n{listed}"
        )
        return _text(truncate_to_tokens(text, GATEWAY_RESULT_MAX_TOKENS))

    async def _clear_named(self, grant: Grant, name: str, arguments: dict[str, Any]) -> None:
        from config.nekopower_settings import GATEWAY_CLEARED_CALL_SECONDS

        commission_id = grant.commission_id
        callable_now = await self._registry.callable_by_task_agents()
        capability = next((item for item in self._offered(grant, callable_now) if item.tool_name == name), None)
        if capability is None:
            return
        commission = await self._ledger.get(commission_id)
        if commission is None or not commission.active:
            return
        group = capability.group
        if (
            group and name not in grant.declared_devices and group not in grant.loaded_groups
            and not _is_first_call(capability, arguments)
        ):
            return
        key = _call_key(capability.id, arguments)
        cleared = await self._permit(commission, capability)
        cleared.device_group = group
        if self._by_commission.get(commission_id) is not grant:
            # The token was taken back while the user was asked: no call will come for it.
            self._permissions.release_grant(cleared.grant_id)
            if cleared.agreement is not None:
                self._registry.browser_reopen_agreement_over(cleared.agreement)
            return
        # One ruled on for this very call that never came is over.
        self._drop_cleared(grant, key)
        cleared.expiry = asyncio.get_running_loop().call_later(
            GATEWAY_CLEARED_CALL_SECONDS, self._drop_cleared, grant, key
        )
        grant.cleared[key] = cleared

    def _drop_cleared(self, grant: Grant, key: tuple[str, str]) -> None:
        """Forget what was ruled on for a call that did not come."""
        cleared = grant.cleared.pop(key, None)
        if cleared is None:
            return
        self._permissions.release_grant(cleared.grant_id)
        if cleared.expiry is not None:
            # Its time is not to run out on whatever is ruled on for the same call next.
            cleared.expiry.cancel()
        if cleared.agreement is not None:
            # The agreement was for that call and goes with it.
            self._registry.browser_reopen_agreement_over(cleared.agreement)

    def _take_cleared(self, commission_id: str, capability_id: str, arguments: dict[str, Any]) -> _Cleared | None:
        """Return what was ruled on ahead for this very call, once."""
        grant = self._by_commission.get(commission_id)
        if grant is None:
            return None
        cleared = grant.cleared.pop(_call_key(capability_id, arguments), None)
        if cleared is not None and cleared.expiry is not None:
            cleared.expiry.cancel()
        return cleared

    async def _permit(self, commission: Commission, capability: Capability) -> _Cleared:
        """Decide whether a call may be carried out, asking the user where the settings say so."""
        decision = await self._permissions.check(
            commission.id,
            capability.permission,
            key=capability.permission_key,
            scope=GrantScope(capability.kind, capability.group or capability.id),
            hold_once=True,
            detail={"tool": capability.tool_name, "capability": capability.id, "title": capability.title},
        )
        if not decision.allow:
            await self._ledger.append_event(
                commission.id, "capability_denied", {"capability": capability.id, "reason": decision.reason}
            )
            reason = {DENIED_BY_SETTINGS: "settings", DENIED_BY_USER: "user"}.get(decision.reason, "no_approval")
            return _Cleared(refusal=_text(decision.reason or "permission denied", error=True), reason=reason)
        if capability.kind == KIND_BUILTIN and capability.group == BUILTIN_GROUP_BROWSER:
            try:
                refusal, agreement = await self._ask_before_reopening(commission, capability, just_asked=decision.asked)
            except BaseException:
                self._permissions.release_grant(decision.grant_id)
                raise
            return _Cleared(
                refusal=refusal, agreement=agreement, reason="browser_reopen" if refusal else None,
                grant_id=decision.grant_id,
            )
        return _Cleared(grant_id=decision.grant_id)

    async def _call_capability(
        self, commission: Commission, capability: Capability, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        kind = "device" if capability.kind == KIND_BUILTIN else "gateway"
        try:
            async with call_audit.call(self._ledger, commission.id, kind, capability.id) as record:
                result = await self._execute(commission, capability, arguments, record)
                if result.get("isError") and record.status == "completed":
                    record.status = "failed"
                return result
        except (call_audit.AuditFull, call_audit.CommissionEnded) as exc:
            return _text(str(exc), error=True)

    async def _execute(
        self, commission: Commission, capability: Capability, arguments: dict[str, Any], record: call_audit.Call
    ) -> dict[str, Any]:
        # Ruled on before the call was made (``clear_call``), or now.
        permitted = self._take_cleared(commission.id, capability.id, arguments)
        if permitted is None:
            permitted = await self._permit(commission, capability)
        record.grant_id = self._permissions.authorization_ref(permitted.grant_id)
        try:
            return await self._run_permitted(commission, capability, arguments, record, permitted)
        finally:
            self._permissions.release_grant(permitted.grant_id)

    async def _run_permitted(
        self, commission: Commission, capability: Capability, arguments: dict[str, Any],
        record: call_audit.Call, permitted: _Cleared,
    ) -> dict[str, Any]:
        from config.nekopower_settings import GATEWAY_RESULT_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        if permitted.refusal is None and not self._permissions.grant_active(permitted.grant_id):
            # Allowed a moment ago, forbidden by the settings since: a deny
            # set meanwhile wins, as it does over an answer. Nobody is asked again.
            if permitted.agreement is not None:
                self._registry.browser_reopen_agreement_over(permitted.agreement)
            await self._ledger.append_event(
                commission.id, "capability_denied", {"capability": capability.id, "reason": DENIED_BY_SETTINGS}
            )
            record.status, record.reason = "denied", "settings"
            return _text(DENIED_BY_SETTINGS, error=True)
        if permitted.refusal is not None:
            record.status, record.reason = "denied", permitted.reason
            return permitted.refusal

        if capability.kind == KIND_BUILTIN:
            if permitted.agreement is not None:
                try:
                    # What the user agreed to is this call opening the
                    # browser: the agreement goes with it, and no other
                    # call opens the browser on the strength of it.
                    return await self._call_builtin(
                        commission, capability, arguments, record, permitted.grant_id, reopen_agreement=permitted.agreement
                    )
                finally:
                    # Should it have ended before it got to the browser
                    # (stopped, or the browser stayed in use by another
                    # task): the agreement ends with it.
                    self._registry.browser_reopen_agreement_over(permitted.agreement)
            return await self._call_builtin(commission, capability, arguments, record, permitted.grant_id)

        async def run_created(run_id: str) -> None:
            await self._ledger.append_event(
                commission.id,
                "plugin_run",
                {"run_id": run_id, "plugin_id": capability.plugin_id, "entry_id": capability.entry_id, "via": "gateway"},
            )

        # The entry's result contract as it stands for this call: the plugin
        # list may no longer show the entry once the run is over.
        _plugin, entry = find_entry(await self._registry.plugins(), str(capability.plugin_id), capability.entry_id)
        outcome = await audited_run_entry(
            self._ledger, self._runner,
            commission_id=commission.id,
            plugin_id=capability.plugin_id,
            entry_id=capability.entry_id,
            args=arguments,
            lanlan_name=commission.lanlan_name,
            conversation_id=commission.conversation_id,
            latest_user_request=commission.user_text,
            on_run_created=run_created,
            matches_entry=capability.matches_entry,
            still_wanted=lambda: self._registry.plugins_enabled() and self._permissions.grant_active(permitted.grant_id),
        )
        if outcome.reason == "entry_changed":
            record.status, record.reason = "denied", "settings"
            return _text(str(outcome.error), error=True)
        text = describe_result(entry, outcome, lang=commission.lang)
        terminal = plugin_terminal_status(outcome.success, outcome.data)
        await self._ledger.append_event(
            commission.id,
            "plugin_run_ended",
            {"run_id": outcome.run_id, "status": outcome.status, "result": terminal, "via": "gateway"},
        )
        if terminal == "failed":
            return _text(truncate_to_tokens(text or str(outcome.error or "failed"), GATEWAY_RESULT_MAX_TOKENS), error=True)
        if entry is not None and llm_result_fields(entry) is None and outcome.data is not None:
            # The entry does not say which fields a model should read; a
            # task agent gets the data itself so it can go on working with it.
            # Without the entry nothing says the data may be shown.
            text = f"{text}\n{json.dumps(outcome.data, ensure_ascii=False, default=str)}"
        if terminal == "blocked":
            answer = await self._confirm_with_user(commission, capability, text) if wants_confirmation(outcome.data) else None
            if answer is not None:
                text = (
                    "[The plugin did not carry this out: it needs the user's confirmation first. The user was "
                    f"asked and answered: {json.dumps(answer, ensure_ascii=False)}. Only if that is a go-ahead, "
                    "call it again the way the plugin asks; otherwise leave it.]\n" + text
                )
            else:
                text = (
                    "[The plugin did not carry this out: it needs the user's confirmation or a clarification first.]\n"
                    + text
                )
        elif isinstance(outcome.data, dict) and outcome.data.get("deferred") is True:
            text = "[Accepted; the plugin completes this later on its own.]\n" + text
        return _text(truncate_to_tokens(text, GATEWAY_RESULT_MAX_TOKENS))

    async def _confirm_with_user(self, commission: Commission, capability: Capability, text: str) -> str | None:
        """Put a plugin's request for confirmation to the user; return the answer, or None if it cannot be asked.

        The commission waits for the user meanwhile. The task agent is not asked
        and cannot answer in the user's place.
        """
        from config.nekopower_settings import GATEWAY_CONFIRMATION_QUESTION_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        if self._ask_user is None:
            return None
        question = {
            "kind": "confirmation",
            "question": truncate_to_tokens(text or capability.title, GATEWAY_CONFIRMATION_QUESTION_MAX_TOKENS),
            "capability": capability.id,
            "title": capability.title,
        }
        try:
            answer = await self._ask_user(commission.id, question)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - it could not be asked; the task agent is told so
            logger.warning("a plugin's request for confirmation could not be put to the user: %s", type(exc).__name__)
            return None
        said = str(answer.get("text") or "").strip()
        # Answered with a button of a page: yes or no, with what the user added to it.
        pressed = CONFIRMATION_ANSWERS.get(str(answer.get("choice") or ""))
        return f"{pressed} {said}".strip() if pressed else said

    async def _ask_before_reopening(
        self, commission: Commission, capability: Capability, *, just_asked: bool
    ) -> tuple[dict[str, Any] | None, Any]:
        """Ask the user before a browser they closed is opened again.

        Returns what the task agent is told when it is not to be opened (or
        None), and what the provider calls the user's agreement to this
        call opening it (None when nothing was to be agreed to).

        The browser the tools started stays open between tasks. When the
        user has closed it, a call would start it anew and put the window
        back on their screen: they are asked first, each time it was closed,
        whatever the permission settings say of the browser. The commission
        waits for the user meanwhile, as for any approval. ``just_asked``:
        this very call has just been approved by the user (the permission to
        operate the browser), and that answer stands for this, too.
        """
        if not self._registry.browser_closed_by_user():
            return None, None
        if not just_asked:
            decision = await self._permissions.ask_each_time(
                commission.id, BROWSER_REOPEN, detail={"tool": capability.tool_name, "capability": capability.id}
            )
            if not decision.allow:
                await self._ledger.append_event(
                    commission.id, "capability_denied", {"capability": capability.id, "reason": decision.reason}
                )
                if decision.reason == DENIED_BY_USER:
                    return _text(
                        "The user closed the browser, and does not want it opened again. Nothing was done. Go on "
                        "without the browser, or report that the task cannot be done without it.",
                        error=True,
                    ), None
                return _text(
                    "The user closed the browser, and could not be asked whether it may be opened again. Nothing "
                    "was done.",
                    error=True,
                ), None
            # The answer may have been long in coming, and the permission to
            # operate the browser was decided before the question: forbidden
            # in the settings since, it stays forbidden.
            if await self._permissions.forbidden(capability.permission, capability.permission_key):
                await self._ledger.append_event(
                    commission.id, "capability_denied", {"capability": capability.id, "reason": DENIED_BY_SETTINGS}
                )
                return _text(DENIED_BY_SETTINGS, error=True), None
        return None, self._registry.browser_reopen_agreed()

    async def _call_builtin(
        self, commission: Commission, capability: Capability, arguments: dict[str, Any],
        record: call_audit.Call, grant_id: str | None, *, reopen_agreement: Any = None
    ) -> dict[str, Any]:
        """Carry out one call of a built-in tool for a commission.

        The commission occupies the tool's group first and keeps it until its
        run ends, it waits for the user or it is over (``leases.py``). The
        enclosing host call records the device identifier and outcome.
        """
        from config.nekopower_settings import GATEWAY_RESULT_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        group = capability.group or ""
        claim = object()
        keep = False
        try:
            if self._leases is not None and group:
                in_the_way = await self._leases.acquire(group, commission.id, title=commission.title, claim=claim)
                if in_the_way is not None:
                    await self._ledger.append_event(
                        commission.id, "builtin_occupied", {"capability": capability.id, "by": in_the_way.team}
                    )
                    what = _group_name(group)
                    return _text(
                        f"The {what} is in use by another task ({in_the_way.title or 'no title'}) and did not come "
                        "free in time. Nothing was done. Go on with what does not need it, try again later, or "
                        "report that you are waiting for it.",
                        error=True,
                    )
                # The wait can be long; the commission may have been stopped or
                # have started waiting for the user meanwhile.
                current = await self._ledger.get(commission.id)
                if current is None or current.status != "working":
                    self._leases.release(commission.id)
                    return _text("this commission is no longer under way", error=True)
            if not self._permissions.grant_active(grant_id):
                record.status, record.reason = "denied", "settings"
                return _text(DENIED_BY_SETTINGS, error=True)
            keep = True
            try:
                grant = self._by_commission.get(commission.id)
                if grant is not None and group in grant.active_groups:
                    grant.used_groups.add(group)
                outcome = await self._registry.run_builtin(
                    capability, commission_id=commission.id, arguments=arguments, reopen_agreement=reopen_agreement,
                    still_allowed=lambda: self._permissions.grant_active(grant_id),
                )
            except asyncio.CancelledError:
                # The device may have started. Its run returns the lease once the worker stops.
                raise
            except Exception as exc:  # noqa: BLE001 - the task agent is told and decides how to go on
                logger.exception("built-in tool %s failed", capability.id)
                return _text(f"{capability.tool_name} failed: {type(exc).__name__}", error=True)
            if not outcome.success and outcome.text == "permission denied":
                keep = False
                record.status, record.reason = "denied", "settings"
        finally:
            if self._leases is not None and group:
                self._leases.finish_claim(group, commission.id, claim, keep=keep)
        text = truncate_to_tokens(outcome.text or ("done" if outcome.success else "failed"), GATEWAY_RESULT_MAX_TOKENS)
        if not outcome.images:
            return _text(text, error=not outcome.success)
        grant = self._by_commission.get(commission.id)
        if grant is None or not grant.supports_images:
            # Not offered to such a task agent as a rule; reached through the
            # call tool it is told what it missed rather than sent a picture.
            return _text(f"{text}\n[A picture was returned, but this model cannot see pictures.]", error=not outcome.success)
        return _text_and_images(text, outcome.images, error=not outcome.success)


async def _close_listener(server: Any, task: "asyncio.Task[Any] | None") -> None:
    """Ask a listener to stop and wait for it, cancelling it if it takes too long."""
    if server is not None:
        server.should_exit = True
    if task is None:
        return
    try:
        # ``asyncio.wait`` never raises what the listener raised, so a cancel
        # seen here is always one of the caller waiting.
        await asyncio.wait({task}, timeout=5.0)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    except asyncio.CancelledError:
        # The caller goes away; the listener is closed without it, and kept
        # referenced until it has ended.
        task.cancel()
        _closing_listeners.add(task)
        task.add_done_callback(_forget_closing_listener)
        raise
    if not task.cancelled() and task.exception() is not None:
        logger.debug("gateway listener ended with an error", exc_info=task.exception())


# Listeners whose closing was left to run on its own after the caller went away.
_closing_listeners: set["asyncio.Task[Any]"] = set()


def _forget_closing_listener(task: "asyncio.Task[Any]") -> None:
    _closing_listeners.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.debug("gateway listener ended with an error", exc_info=task.exception())


def _origin_host(origin: str) -> str:
    rest = origin.split("://", 1)[-1]
    host = rest.split("/", 1)[0]
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


async def _wait_disconnect(receive: Callable[..., Any]) -> None:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return
