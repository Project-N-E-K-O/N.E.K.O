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

"""The capability list, the plugin list cache, and how the host is mounted."""

from __future__ import annotations

import ast
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from nekoclaw.host.capabilities import (
    BUILTIN_GROUP_BROWSER,
    BUILTIN_GROUP_KEYBOARD_MOUSE,
    KIND_BUILTIN,
    KIND_MCP_TOOL,
    KIND_PLUGIN_ENTRY,
    TOOL_NAME_MAX_LENGTH,
    BuiltinImage,
    BuiltinOutcome,
    CapabilityRegistry,
    builtin_tool,
    capabilities_of_plugin,
    gateway_tool_name,
)
from nekoclaw.host.plugin_runs import CATALOG_TTL_SECONDS, PluginCatalog
from tests.unit.nekoclaw.host_fakes import FakeDecisionLLM, fake_model, make_host
from tests.unit.nekoclaw.plugin_server_fake import FakePluginServer, entry, plugin

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def server(monkeypatch: Any) -> FakePluginServer:
    return FakePluginServer.install(monkeypatch)


# ---------------------------------------------------------------------------
# The capability list
# ---------------------------------------------------------------------------


def _mixed(server: FakePluginServer) -> None:
    server.add_plugin(
        plugin(
            "weather",
            "Weather lookups",
            [
                entry("today", "Today's weather", name="Today"),
                entry("admin", "Reset", metadata={"agent_auto": False}),
                entry("debug", "Dump", metadata={"agent_exposed": False}),
                entry("raw", "Raw", metadata={"llm_exposed": False}),
                entry("secret", "Secret", metadata={"agent_hidden": True}),
            ],
            name="Weather",
        )
    )
    server.add_plugin(plugin("radio", "Plays radio", [entry("play", "Play")], passive=True))
    server.add_plugin(plugin("stopped", "Not running", [entry("run", "Run")], status="stopped"))
    server.add_plugin(
        plugin(
            "mcp_adapter",
            "MCP Adapter",
            [
                entry("mcp_my_notes_search_notes", "Search the notes", name="[my_notes] search_notes"),
                entry("connect_server", "Connect a server", name="Connect"),
                entry("mcp_status", "Adapter status", name="Status"),
            ],
        )
    )


class _Builtins:
    """A provider that lists the tools it is given, or raises the exception it is given."""

    def __init__(self, tools: Any) -> None:
        self.tools = tools

    def available(self) -> list[Any]:
        if isinstance(self.tools, BaseException):
            raise self.tools
        return list(self.tools)


_COMPUTER = {
    "name": "computer",
    "description": "Operate the desktop.",
    "parameters": {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]},
}
_PAGES = {"name": "browser_list_pages", "description": "List the open pages."}


async def test_list_has_plugin_entries_mcp_tools_and_the_builtin_tools_of_the_provider(server):
    _mixed(server)
    builtins = _Builtins(
        [
            builtin_tool(BUILTIN_GROUP_KEYBOARD_MOUSE, _COMPUTER, needs_images=True),
            builtin_tool(BUILTIN_GROUP_BROWSER, _PAGES, available=False, unavailable_reason="no browser is installed"),
        ]
    )
    registry = CapabilityRegistry(PluginCatalog(client_factory=server.client), builtins=builtins)

    listed = {item.id: item for item in await registry.list()}

    assert set(listed) == {
        "plugin:weather:today",
        "mcp:my_notes:search_notes",
        "plugin:mcp_adapter:connect_server",
        "plugin:mcp_adapter:mcp_status",
        "builtin:keyboard_mouse:computer",
        "builtin:browser:browser_list_pages",
    }
    computer = listed["builtin:keyboard_mouse:computer"]
    # Called by its own name, guarded by its group's permission, and never
    # something one call gets a task done with.
    assert (computer.kind, computer.tool_name, computer.title, computer.description) == (
        KIND_BUILTIN,
        "computer",
        "computer",
        "Operate the desktop.",
    )
    assert (computer.group, computer.permission, computer.permission_key) == ("keyboard_mouse", "keyboard_mouse", None)
    assert (computer.one_step, computer.available, computer.needs_images) == (False, True, True)
    assert computer.input_schema == _COMPUTER["parameters"]
    pages = listed["builtin:browser:browser_list_pages"]
    assert (pages.group, pages.permission, pages.needs_images) == ("browser", "browser", False)
    assert (pages.available, pages.unavailable_reason) == (False, "no browser is installed")
    # A definition without parameters takes none.
    assert pages.input_schema == {"type": "object", "properties": {}}
    # What is not available is listed, and not served.
    served = [item.id for item in await registry.callable_by_task_agents()]
    assert "builtin:keyboard_mouse:computer" in served and "builtin:browser:browser_list_pages" not in served
    today = listed["plugin:weather:today"]
    assert (today.kind, today.title, today.permission, today.one_step, today.available) == (
        KIND_PLUGIN_ENTRY,
        "Weather: Today",
        "plugin_entry",
        True,
        True,
    )
    assert (today.plugin_id, today.entry_id, today.tool_name) == ("weather", "today", "plugin__weather__today")
    notes = listed["mcp:my_notes:search_notes"]
    # An MCP tool: permission is asked for its server, and the task brain
    # does not treat it as something one call gets a task done with.
    assert (notes.kind, notes.permission, notes.permission_key, notes.one_step) == (
        KIND_MCP_TOOL,
        "mcp_server",
        "my_notes",
        False,
    )
    assert (notes.plugin_id, notes.entry_id, notes.tool_name) == (
        "mcp_adapter",
        "mcp_my_notes_search_notes",
        "mcp__my_notes__search_notes",
    )


async def test_without_a_provider_there_are_no_builtin_tools(server):
    _mixed(server)
    registry = CapabilityRegistry(PluginCatalog(client_factory=server.client))

    assert registry.builtins() == []
    assert [item.kind for item in await registry.list() if item.kind == KIND_BUILTIN] == []
    # Nothing to carry a call out, and nobody to tell that a group came free.
    outcome = await registry.run_builtin(
        builtin_tool(BUILTIN_GROUP_BROWSER, _PAGES), commission_id="c1", arguments={}
    )
    assert (outcome.success, outcome.images) == (False, ())
    assert await registry.builtin_released(BUILTIN_GROUP_BROWSER, "c1") is None


def test_a_builtin_tool_belongs_to_one_of_the_two_groups():
    with pytest.raises(ValueError, match="unknown built-in group"):
        builtin_tool("printer", _PAGES)


async def test_only_builtin_tools_of_the_two_groups_are_taken_from_the_provider(server):
    import dataclasses

    good = builtin_tool(BUILTIN_GROUP_BROWSER, _PAGES)
    elsewhere = dataclasses.replace(good, id="builtin:printer:print", tool_name="print", group="printer")
    not_builtin = dataclasses.replace(good, id="plugin:x:y", tool_name="plugin__x__y", kind=KIND_PLUGIN_ENTRY)
    registry = CapabilityRegistry(
        PluginCatalog(client_factory=server.client), builtins=_Builtins([good, elsewhere, not_builtin])
    )

    assert registry.builtins() == [good]


async def test_a_provider_that_cannot_be_listed_offers_nothing(server):
    _mixed(server)
    registry = CapabilityRegistry(
        PluginCatalog(client_factory=server.client), builtins=_Builtins(RuntimeError("the display is gone"))
    )

    assert registry.builtins() == []
    # The plugins are served as without it.
    assert "plugin:weather:today" in [item.id for item in await registry.callable_by_task_agents()]


async def test_the_provider_carries_a_call_out_and_hears_when_a_group_comes_free(server):
    calls: list[Any] = []

    class Provider(_Builtins):
        async def run(self, capability: Any, *, commission_id: str, arguments: dict[str, Any]) -> Any:
            calls.append(("run", capability.id, commission_id, arguments))
            return BuiltinOutcome(True, "2 pages", images=(BuiltinImage("QUJD", "image/jpeg"),))

        async def released(self, group: str, commission_id: str) -> None:
            calls.append(("released", group, commission_id))
            raise RuntimeError("the browser would not close")

    tool = builtin_tool(BUILTIN_GROUP_BROWSER, _PAGES)
    registry = CapabilityRegistry(PluginCatalog(client_factory=server.client), builtins=Provider([tool]))

    outcome = await registry.run_builtin(tool, commission_id="c1", arguments={"verbose": True})
    # What the provider kept is its own business: a cleanup that fails is not raised.
    await registry.builtin_released(BUILTIN_GROUP_BROWSER, "c1")

    assert (outcome.success, outcome.text, outcome.images) == (True, "2 pages", (BuiltinImage("QUJD", "image/jpeg"),))
    assert calls == [
        ("run", "builtin:browser:browser_list_pages", "c1", {"verbose": True}),
        ("released", "browser", "c1"),
    ]


async def test_only_what_is_available_is_served_to_task_agents(server):
    _mixed(server)
    registry = CapabilityRegistry(PluginCatalog(client_factory=server.client))

    served = [item.id for item in await registry.callable_by_task_agents()]

    assert served == [
        "plugin:weather:today",
        "mcp:my_notes:search_notes",
        "plugin:mcp_adapter:connect_server",
    ]


async def test_an_mcp_tool_whose_server_cannot_be_told_is_not_served(server):
    _mixed(server)
    registry = CapabilityRegistry(PluginCatalog(client_factory=server.client))

    listed = {item.id: item for item in await registry.list()}

    # Named like no server's tool, yet its id says it is one: no single
    # server's permission can be said to cover it.
    status = listed["plugin:mcp_adapter:mcp_status"]
    assert status.available is False
    assert "cannot be told" in status.unavailable_reason


@pytest.mark.parametrize(
    ("entry_id", "name", "expected"),
    [
        # A server whose own name holds "] ": the id tells which reading is meant.
        ("mcp_notes] v2_search", "[notes] v2] search", "mcp:notes] v2:search"),
        ("mcp_notes_v2] search", "[notes] v2] search", "mcp:notes:v2] search"),
        # Read either way and agreeing with neither: it cannot be told.
        ("mcp_other_search", "[notes] v2] search", "plugin:mcp_adapter:mcp_other_search"),
        # A line break in the name does not hide its server.
        ("mcp_github_create_issue", "[github] create\nissue", "mcp:github:create\nissue"),
    ],
)
async def test_the_server_of_an_mcp_tool_is_read_from_its_name_and_id(server, entry_id, name, expected):
    server.add_plugin(plugin("mcp_adapter", "MCP Adapter", [entry(entry_id, "A tool", name=name)]))
    registry = CapabilityRegistry(PluginCatalog(client_factory=server.client))

    (item,) = [item for item in await registry.list() if item.plugin_id == "mcp_adapter"]

    assert item.id == expected
    assert item.available is expected.startswith("mcp:")


async def test_selection_still_sees_mcp_tools_as_entries_of_the_adapter(server):
    _mixed(server)
    registry = CapabilityRegistry(PluginCatalog(client_factory=server.client))

    plugins = await registry.plugins()

    assert [item["id"] for item in plugins] == ["weather", "mcp_adapter"]
    adapter = plugins[1]
    assert [item["id"] for item in adapter["entries"]] == ["mcp_my_notes_search_notes", "connect_server", "mcp_status"]


async def test_plugin_switch_off_empties_the_list_of_plugins(server):
    _mixed(server)
    registry = CapabilityRegistry(PluginCatalog(client_factory=server.client), plugins_enabled=lambda: False)

    assert await registry.plugins() == []
    assert await registry.list() == []
    summary = await registry.summary()
    assert (summary.installed, summary.running, summary.routable, summary.enabled) == (4, 3, 2, False)


async def test_tools_registered_for_the_main_dialogue_are_not_entries(server):
    server.add_plugin(plugin("memo", "Memos", [entry("__llm_tool__add", "Add"), entry("list", "List")]))
    registry = CapabilityRegistry(PluginCatalog(client_factory=server.client))

    assert [item.id for item in await registry.callable_by_task_agents()] == ["plugin:memo:list"]


def test_passive_plugin_contributes_nothing():
    assert capabilities_of_plugin(plugin("radio", "Radio", passive=True)) == []
    assert capabilities_of_plugin({"description": "no id"}) == []


@pytest.mark.parametrize(
    "parts, expected",
    [
        (("plugin", "weather", "today"), "plugin__weather__today"),
        (("mcp", "my-notes", "search_notes"), "mcp__my-notes__search_notes"),
    ],
)
def test_tool_names_of_plain_ids_are_readable(parts, expected):
    assert gateway_tool_name(*parts) == expected


def test_tool_names_are_valid_short_and_distinct():
    import re

    names = {
        gateway_tool_name("plugin", "天气", "today"),
        gateway_tool_name("plugin", "天氣", "today"),
        gateway_tool_name("plugin", "a.b", "c"),
        gateway_tool_name("plugin", "a_b", "c"),
        gateway_tool_name("plugin", "x" * 80, "run"),
        gateway_tool_name("plugin", "x" * 81, "run"),
    }

    assert len(names) == 6
    for name in names:
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name), name
        assert len(name) <= TOOL_NAME_MAX_LENGTH


# ---------------------------------------------------------------------------
# The plugin list is fetched when it may have changed
# ---------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_list_is_cached_until_it_is_thirty_seconds_old(server):
    server.add_plugin(plugin("weather", "Weather"))
    clock = Clock()
    catalog = PluginCatalog(client_factory=server.client, clock=clock)

    await catalog.plugins()
    await catalog.plugins()
    assert server.plugins_requests == 1

    clock.now = CATALOG_TTL_SECONDS - 0.1
    await catalog.plugins()
    assert server.plugins_requests == 1
    clock.now = CATALOG_TTL_SECONDS
    await catalog.plugins()
    assert server.plugins_requests == 2


async def test_lifecycle_event_makes_the_next_read_fetch_again(server):
    server.add_plugin(plugin("weather", "Weather"))
    registry = CapabilityRegistry(PluginCatalog(client_factory=server.client))
    assert [item["id"] for item in await registry.plugins()] == ["weather"]

    server.add_plugin(plugin("lamp", "Lamp"))
    assert [item["id"] for item in await registry.plugins()] == ["weather"]
    registry.invalidate()

    assert [item["id"] for item in await registry.plugins()] == ["weather", "lamp"]
    assert server.plugins_requests == 2


async def test_change_token_makes_the_next_read_fetch_again(server):
    server.add_plugin(plugin("weather", "Weather"))
    token = [1]
    catalog = PluginCatalog(client_factory=server.client, change_token=lambda: token[0])

    await catalog.plugins()
    await catalog.plugins()
    assert server.plugins_requests == 1
    token[0] = 2
    await catalog.plugins()
    assert server.plugins_requests == 2


async def test_unreadable_change_token_costs_a_fetch_not_an_error(server):
    server.add_plugin(plugin("weather", "Weather"))

    def broken() -> Any:
        raise RuntimeError("snapshot unavailable")

    catalog = PluginCatalog(client_factory=server.client, change_token=broken)

    assert [item["id"] for item in await catalog.plugins()] == ["weather"]
    assert [item["id"] for item in await catalog.plugins()] == ["weather"]
    assert server.plugins_requests == 2


async def test_failed_fetch_keeps_the_last_list(server):
    server.add_plugin(plugin("weather", "Weather"))
    broken = [False]

    def client(**kwargs: Any) -> Any:
        if broken[0]:
            raise ConnectionError("plugin server unreachable")
        return server.client(**kwargs)

    catalog = PluginCatalog(client_factory=client)
    await catalog.plugins()
    broken[0] = True
    catalog.invalidate()

    assert [item["id"] for item in await catalog.plugins()] == ["weather"]


# ---------------------------------------------------------------------------
# Building and mounting the host costs nothing
# ---------------------------------------------------------------------------


async def test_building_the_host_starts_nothing(server, tmp_path):
    before = {thread.ident for thread in threading.enumerate()}

    host = make_host(tmp_path, server, task_agent_model=fake_model, decision_llm=FakeDecisionLLM())

    try:
        assert not (tmp_path / "nekoclaw").exists()
        assert not host.ledger.opened
        assert not host.task_agents.runtime_started
        assert not host.gateway.started
        assert server.plugins_requests == 0
        assert {thread.ident for thread in threading.enumerate()} == before
    finally:
        await host.shutdown()
    # Shutting down a host nothing ever used leaves nothing behind either.
    assert not (tmp_path / "nekoclaw").exists()


async def test_shutdown_is_safe_to_repeat(server, tmp_path):
    host = make_host(tmp_path, server, decision_llm=FakeDecisionLLM())
    await host.query("", lanlan_name="Lan")

    await host.shutdown()
    await host.shutdown()

    receipt = await host.dispatch("check the weather", lanlan_name="Lan")
    assert (receipt.kind, receipt.reason) == ("no_capability", "disabled")


@pytest.fixture
def mount() -> Any:
    from nekoclaw.host import mount as module

    module.reset_for_tests()
    yield module
    module.reset_for_tests()


async def test_configure_builds_nothing_and_get_host_builds_once(mount, tmp_path):
    mount.configure(root=tmp_path / "nekoclaw", enabled=lambda: True)

    assert mount.is_configured()
    assert mount.peek_host() is None
    # Before the host exists a lifecycle event has nothing to tell.
    mount.notify_plugins_changed("add", {"record": {}})
    assert mount.peek_host() is None

    host = mount.get_host()
    try:
        assert mount.get_host() is host
        assert mount.peek_host() is host
        assert host.paths.root() == tmp_path / "nekoclaw"
        host.catalog._stale = False
        mount.notify_plugins_changed("add", {"record": {}})
        assert host.catalog._stale is True
    finally:
        await mount.shutdown_host()
    assert mount.peek_host() is None
    # The host itself was shut down, not merely forgotten.
    assert host._shut_down is True
    assert (await host.dispatch("anything", lanlan_name="Lan")).reason == "disabled"
    await mount.shutdown_host()


def test_importing_the_mount_loads_nothing_else():
    # What the agent process imports at startup must stay this small.
    code = (
        "import sys\n"
        "import nekoclaw.host.mount\n"
        "heavy = [name for name in ('sqlite3', 'httpx', 'uvicorn', 'pydantic', 'tiktoken', "
        "'nekoclaw.host.service', 'nekoclaw.host.ledger', 'nekoclaw.runtime.core', 'utils.config_manager') "
        "if name in sys.modules]\n"
        "print('HEAVY', heavy)\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120, check=False
    )

    assert result.returncode == 0, result.stderr
    assert "HEAVY []" in result.stdout


async def test_agent_process_registers_the_host_without_building_it(mount, monkeypatch):
    from app.agent_server import _shared, nekoclaw_mount
    from plugin.core.state import state as plugin_state

    subscribed: list[tuple[str, Any]] = []
    unsubscribed: list[int] = []

    def subscribe(bus: str, callback: Any) -> Any:
        subscribed.append((bus, callback))
        return lambda: unsubscribed.append(1)

    monkeypatch.setattr(plugin_state.bus_change_hub, "subscribe", subscribe)
    monkeypatch.setattr(nekoclaw_mount, "_unsubscribe", None)
    monkeypatch.setattr(_shared.Modules, "analyzer_enabled", False)
    monkeypatch.setattr(_shared.Modules, "agent_flags", {"user_plugin_enabled": True})
    token = object()

    nekoclaw_mount.mount_nekoclaw_host(lambda: token)
    nekoclaw_mount.mount_nekoclaw_host(lambda: token)

    assert mount.is_configured()
    assert mount.peek_host() is None
    # Subscribed once to plugin lifecycle events, with the callback that only sets a flag.
    assert subscribed == [("lifecycle", mount.notify_plugins_changed)]
    host = mount.get_host()
    try:
        # The host reads this process's switches as they are at the time.
        assert host.brain._is_enabled() is False
        assert host.registry.plugins_enabled() is True
        monkeypatch.setattr(_shared.Modules, "analyzer_enabled", True)
        assert host.brain._is_enabled() is True
        # The master switch alone is not NekoClaw switched on.
        monkeypatch.setattr(_shared.Modules, "agent_flags", {"user_plugin_enabled": False})
        assert host.brain._is_enabled() is False
        assert host.registry.plugins_enabled() is False
        monkeypatch.setattr(
            _shared.Modules, "agent_flags", {"user_plugin_enabled": False, "browser_use_enabled": True}
        )
        assert host.brain._is_enabled() is True
        assert host.registry.plugins_enabled() is False
        assert host.catalog._change_token() is token
    finally:
        await nekoclaw_mount.shutdown_nekoclaw_host()
    assert unsubscribed == [1]
    assert mount.peek_host() is None


async def test_shutting_down_an_agent_process_that_never_used_the_host(mount, monkeypatch):
    from app.agent_server import nekoclaw_mount

    monkeypatch.setattr(nekoclaw_mount, "_unsubscribe", None)

    await nekoclaw_mount.shutdown_nekoclaw_host()

    assert mount.peek_host() is None


def test_agent_startup_and_shutdown_call_the_mount():
    source = (REPO_ROOT / "app" / "agent_server" / "api_runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in ("startup", "shutdown"):
            calls[node.name] = [
                inner.func.id
                for inner in ast.walk(node)
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
            ]

    assert "mount_nekoclaw_host" in calls["startup"]
    assert "shutdown_nekoclaw_host" in calls["shutdown"]
    # Startup only registers; the host itself is never built or used there.
    assert "get_host" not in calls["startup"]


# ---------------------------------------------------------------------------
# What the host layer may import
# ---------------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_host_reaches_plugins_over_http_and_imports_only_their_settings():
    project_packages = {
        "app", "brain", "config", "knowledge", "launcher_core", "local_server", "main_logic",
        "main_routers", "memory", "nekoclaw", "plugin", "services", "steamworks", "theater_workshop", "utils",
    }
    used: dict[str, set[str]] = {}
    for path in sorted((REPO_ROOT / "nekoclaw" / "host").glob("*.py")):
        for module in _imports(path):
            top = module.split(".")[0]
            if top in project_packages:
                used.setdefault(top, set()).add(module)

    assert set(used) <= {"config", "utils", "nekoclaw", "plugin"}
    assert used.get("plugin", set()) <= {"plugin.settings"}
    # The runtime below it knows nothing of the host.
    for path in sorted((REPO_ROOT / "nekoclaw" / "runtime").rglob("*.py")):
        assert not any(module.startswith("nekoclaw.host") for module in _imports(path)), path


def test_package_init_stays_empty_of_imports():
    for name in ("nekoclaw/__init__.py", "nekoclaw/host/__init__.py"):
        assert _imports(REPO_ROOT / name) == set(), name
