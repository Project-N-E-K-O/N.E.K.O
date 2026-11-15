# -*- coding: utf-8 -*-
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
#
# ---------------------------------------------------------------------------
# BROWSER_TOOLS below follows Chrome DevTools MCP,
# https://github.com/ChromeDevTools/chrome-devtools-mcp, commit
# ec6fd2cf7a84434f42efe3e886ce2c6ee7b8d83b (src/tools/input.ts, pages.ts,
# snapshot.ts, screenshot.ts), Copyright 2025 Google LLC, which is distributed
# under the Apache License, Version 2.0. The split into tools, the tool and
# parameter names and the wording of the descriptions are taken from it; every
# name carries the prefix ``browser_``. The descriptions are shortened where
# that loses nothing a model needs: they are sent with every request of a task
# agent. What else differs from it is noted at the tool.
# ---------------------------------------------------------------------------

"""Definitions of the built-in tools of NekoClaw as a model sees them.

Two groups (design section 11): the keyboard and the mouse (``COMPUTER_TOOL``)
and the browser (``BROWSER_TOOLS``). A task agent is offered them by the
host's capability gateway next to its own tools. The paragraphs of guidance
that go with them are in ``prompts_nekoclaw.TOOL_GUIDANCE_SECTIONS``, and they
appear in a task agent's system prompt only when the tools are offered.

All of it is English text read by the model and is deliberately not
translated; the language a task agent uses towards the user is set elsewhere.
The definitions are sent with every request of a task agent, so their size is
a fixed cost: ``tests/unit/nekoclaw/test_builtin_tool_budget.py`` keeps them
within ``GATEWAY_BUILTIN_TOOL_LIST_MAX_TOKENS``.
"""

# ---------------------------------------------------------------------------
# Keyboard and mouse
# ---------------------------------------------------------------------------
# One tool with an ``action`` argument rather than one tool per action. Split
# into eight tools the same text comes to about twice the tokens in every
# request (the note on coordinates has to be repeated at each tool that takes
# them), and the shape is the one models know from other computer-use tools.

COMPUTER_TOOL_NAME = "computer"

COMPUTER_TOOL = {
    "name": COMPUTER_TOOL_NAME,
    "description": (
        "Operate this computer's desktop with the mouse and the keyboard, one action per call. Every "
        "call returns a screenshot taken after the action. Coordinates are integers from 0 to 999 on "
        "both axes whatever the size of the screen: (0, 0) is the top-left corner, (999, 999) the "
        "bottom-right corner, (500, 500) the centre. They are not pixels.\n"
        "Actions:\n"
        "- screenshot: look at the screen.\n"
        "- click: click at x, y. button is left (default), right or middle; clicks is 1 (default), "
        "2 for a double click or 3.\n"
        "- move: move the pointer to x, y, to hover.\n"
        "- drag: press the left button at x, y, move to to_x, to_y and release.\n"
        "- scroll: turn the wheel at x, y, or where the pointer is. direction is up, down, left or "
        "right; amount is the number of notches (default 3).\n"
        "- type: type text where the keyboard focus is.\n"
        "- key: press one key or a combination joined with +, such as enter, esc, ctrl+c, alt+f4 or "
        "win+r. hold_seconds keeps it down that long.\n"
        "- wait: wait seconds (default 1) for something to load."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["screenshot", "click", "move", "drag", "scroll", "type", "key", "wait"],
            },
            "x": {"type": "integer", "description": "0 to 999 from the left edge"},
            "y": {"type": "integer", "description": "0 to 999 from the top edge"},
            "to_x": {"type": "integer"},
            "to_y": {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
            "clicks": {"type": "integer"},
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "integer"},
            "text": {"type": "string"},
            "keys": {"type": "string"},
            "hold_seconds": {"type": "number"},
            "seconds": {"type": "number"},
        },
        "required": ["action"],
    },
}

# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------

_UID = "The uid of an element from the page snapshot"
_INCLUDE_SNAPSHOT = {"type": "boolean", "description": "Include a snapshot in the response. Default is false."}
_TIMEOUT = {"type": "integer", "description": "Maximum wait time in milliseconds."}

BROWSER_SCREENSHOT_TOOL_NAME = "browser_take_screenshot"
BROWSER_SNAPSHOT_TOOL_NAME = "browser_take_snapshot"

BROWSER_TOOLS: tuple[dict, ...] = (
    {
        # Differs: the result carries a snapshot of the loaded page, which is
        # what is asked for next every time; ``ignoreCache``,
        # ``handleBeforeUnload`` and ``initScript`` are not taken over.
        "name": "browser_navigate_page",
        "description": "Go to a URL, or back, forward, or reload. Returns a snapshot of the loaded page.",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["url", "back", "forward", "reload"]},
                "url": {"type": "string", "description": "Target URL (only type=url)"},
                "timeout": _TIMEOUT,
            },
        },
    },
    {
        # Differs: no ``filePath``; a snapshot has an upper size, and
        # ``offset`` reads on where one was cut off.
        "name": BROWSER_SNAPSHOT_TOOL_NAME,
        "description": (
            "Take a text snapshot of the selected page based on the a11y tree. The snapshot lists page "
            "elements along with a unique identifier (uid). Always use the latest snapshot. Prefer "
            "taking a snapshot over taking a screenshot."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "verbose": {
                    "type": "boolean",
                    "description": "Include everything in the full a11y tree. Default is false.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip this many lines; to read on where a snapshot was cut off.",
                },
            },
        },
    },
    {
        "name": "browser_click",
        "description": "Clicks on the provided element",
        "parameters": {
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": _UID},
                "dblClick": {"type": "boolean", "description": "Double click. Default is false."},
                "includeSnapshot": _INCLUDE_SNAPSHOT,
            },
            "required": ["uid"],
        },
    },
    {
        "name": "browser_hover",
        "description": "Hover over the provided element",
        "parameters": {
            "type": "object",
            "properties": {"uid": {"type": "string", "description": _UID}, "includeSnapshot": _INCLUDE_SNAPSHOT},
            "required": ["uid"],
        },
    },
    {
        "name": "browser_fill",
        "description": "Type text into an input, text area or select an option from a <select> element.",
        "parameters": {
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": _UID},
                "value": {
                    "type": "string",
                    "description": (
                        'The value to fill in. "true" or "false" for checkboxes and toggles, "true" for '
                        "radio buttons."
                    ),
                },
                "includeSnapshot": _INCLUDE_SNAPSHOT,
            },
            "required": ["uid", "value"],
        },
    },
    {
        "name": "browser_fill_form",
        "description": (
            "Fill out multiple form elements (inputs, selects, checkboxes, radios) at once. ALWAYS prefer "
            "this tool over multiple individual browser_fill or browser_click calls when interacting "
            "with forms: it is faster, more reliable, and reduces turn count."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "uid": {"type": "string"},
                            "value": {"type": "string", "description": "As for browser_fill."},
                        },
                        "required": ["uid", "value"],
                    },
                },
                "includeSnapshot": _INCLUDE_SNAPSHOT,
            },
            "required": ["elements"],
        },
    },
    {
        "name": "browser_type_text",
        "description": "Type text using keyboard into a previously focused input",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to type"},
                "submitKey": {
                    "type": "string",
                    "description": 'Optional key to press after typing. E.g., "Enter", "Tab"',
                },
            },
            "required": ["text"],
        },
    },
    {
        # Differs: the note about native <select> elements on macOS is left
        # out (``browser_fill`` sets them directly); scrolling is named,
        # since there is no tool for it.
        "name": "browser_press_key",
        "description": (
            "Press a key or key combination (keyboard shortcuts, navigation keys). PageDown, PageUp, End "
            "and Home scroll the page."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        'A key or a combination (e.g., "Enter", "Control+A", "Control+Shift+R"). '
                        "Modifiers: Control, Shift, Alt, Meta"
                    ),
                },
                "includeSnapshot": _INCLUDE_SNAPSHOT,
            },
            "required": ["key"],
        },
    },
    {
        # Differs: always a JPEG handed back as a picture; no ``format``,
        # ``quality`` or ``filePath``.
        "name": BROWSER_SCREENSHOT_TOOL_NAME,
        "description": "Take a screenshot of the page or element.",
        "parameters": {
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "An element's uid. If omitted, takes a page screenshot."},
                "fullPage": {
                    "type": "boolean",
                    "description": "The full page instead of the visible viewport. Incompatible with uid.",
                },
            },
        },
    },
    {
        "name": "browser_list_pages",
        "description": "Get a list of pages open in the browser.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        # Differs: the response carries a snapshot of the page selected.
        "name": "browser_select_page",
        "description": "Select a page as a context for future tool calls. Returns a snapshot of it.",
        "parameters": {
            "type": "object",
            "properties": {
                "pageId": {"type": "integer", "description": "The ID of the page, from browser_list_pages."},
                "bringToFront": {"type": "boolean", "description": "Also bring it to the front."},
            },
            "required": ["pageId"],
        },
    },
    {
        # Differs: no ``isolatedContext``; the response carries a snapshot.
        "name": "browser_new_page",
        "description": "Open a new tab and load a URL. Returns a snapshot of the page.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "background": {"type": "boolean", "description": "Open it in the background. Default is false."},
                "timeout": _TIMEOUT,
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_close_page",
        "description": "Closes the page by its ID. The last open page cannot be closed.",
        "parameters": {
            "type": "object",
            "properties": {"pageId": {"type": "integer"}},
            "required": ["pageId"],
        },
    },
    {
        # Differs: no ``visible``; hidden text counts, as it does there by default.
        "name": "browser_wait_for",
        "description": "Wait for the specified text to appear on the selected page.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Resolves when any of them appears on the page. Matching is case-sensitive.",
                },
                "timeout": _TIMEOUT,
            },
            "required": ["text"],
        },
    },
    {
        "name": "browser_handle_dialog",
        "description": "If a browser dialog was opened, use this command to handle it",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["accept", "dismiss"]},
                "promptText": {"type": "string", "description": "Optional prompt text to enter into the dialog."},
            },
            "required": ["action"],
        },
    },
)

BROWSER_TOOL_NAMES: tuple[str, ...] = tuple(tool["name"] for tool in BROWSER_TOOLS)
