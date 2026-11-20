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

"""Trusted, process-local device providers selected when the host is assembled.

The host owns the permission and lease of each slot. A provider contributes
its tool schemas and a lazy driver. The desktop driver accepts cancellable
actions and releases held input. The browser driver accepts named calls with
the host's refusal and reopen checks and owns the corresponding window.
The host can call open_driver() on the event loop: construct the driver
without blocking, and defer device I/O until an action is carried out.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from config.prompts.prompts_nekoclaw_builtin import (
    BROWSER_SCREENSHOT_TOOL_NAME,
    BROWSER_TOOLS,
    COMPUTER_TOOL,
    COMPUTER_TOOL_NAME,
)
from nekoclaw.host.capabilities import BUILTIN_GROUP_BROWSER, BUILTIN_GROUP_KEYBOARD_MOUSE, Capability, builtin_tool

if TYPE_CHECKING:
    from nekoclaw.builtin.browser.tools import BrowserTools
    from nekoclaw.builtin.desktop import Desktop


class DesktopDeviceProvider:
    """One computer tool dispatching by its action argument; the driver also handles release."""

    def tools(self) -> Iterable[tuple[dict[str, Any], bool]]:
        yield COMPUTER_TOOL, True

    def open_driver(self) -> Desktop:
        from nekoclaw.builtin.desktop import Desktop

        return Desktop()


class BrowserDeviceProvider:
    """Browser tools, their image requirements and the matching browser installation."""

    def tools(self) -> Iterable[tuple[dict[str, Any], bool]]:
        for definition in BROWSER_TOOLS:
            yield definition, definition["name"] == BROWSER_SCREENSHOT_TOOL_NAME

    def installed(self) -> bool:
        """Blocking probe; neither opens a driver nor starts a browser."""
        from nekoclaw.builtin.browser.launcher import NoBrowserError, pick_browser
        from nekoclaw.builtin.browser.settings import BrowserSettingsStore

        try:
            pick_browser(BrowserSettingsStore().load().executable)
            return True
        except NoBrowserError:
            return False

    def open_driver(self, *, own_ports: Callable[[], Iterable[int]] | None) -> BrowserTools:
        from nekoclaw.builtin.browser.session import BrowserSession
        from nekoclaw.builtin.browser.tools import BrowserTools

        return BrowserTools(BrowserSession(own_ports=own_ports))


def registered_tools(
    desktop: DesktopDeviceProvider, browser: BrowserDeviceProvider
) -> dict[str, dict[str, Capability]]:
    """Bind each tool to its slot's permission and lease before any driver is opened."""
    groups: dict[str, dict[str, Capability]] = {}
    registered_names: set[str] = set()
    for group, provider in ((BUILTIN_GROUP_KEYBOARD_MOUSE, desktop), (BUILTIN_GROUP_BROWSER, browser)):
        tools: dict[str, Capability] = {}
        for definition, needs_images in provider.tools():
            name = definition.get("name")
            if not isinstance(name, str) or not name or name.strip() != name or name in registered_names:
                raise ValueError(f"invalid or duplicate built-in tool name {name!r}")
            tools[name] = builtin_tool(group, definition, needs_images=needs_images)
            registered_names.add(name)
        groups[group] = tools
    # The desktop driver receives an action through one tool. Browser calls
    # pass the registered tool name to the driver as well as its arguments.
    if groups[BUILTIN_GROUP_KEYBOARD_MOUSE].keys() != {COMPUTER_TOOL_NAME}:
        raise ValueError(f"the desktop provider must register exactly {COMPUTER_TOOL_NAME!r}")
    if not groups[BUILTIN_GROUP_KEYBOARD_MOUSE][COMPUTER_TOOL_NAME].needs_images:
        raise ValueError("the desktop tool requires a model that can see screenshots")
    return groups
