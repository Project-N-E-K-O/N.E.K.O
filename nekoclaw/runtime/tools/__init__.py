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

"""Built-in tools of the agent runtime.

Names, parameters and descriptions come from
``config/prompts/prompts_nekoclaw_tools.py``; the implementations live in this
package. ``build_tools`` returns exactly the tools a session offers its model:

* always: ``read``, ``write``, ``edit``, ``glob``, ``grep``, the command tool
  (``pwsh`` on Windows, ``bash`` elsewhere), the three job tools,
  ``web_fetch``, ``todo_write`` and the three goal tools;
* ``read_image`` when the model accepts images;
* the host-backed tools whose callback the host injected.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from nekoclaw.runtime.loop.tools import AgentTool
from nekoclaw.runtime.tools.base import ToolContext

# Tools the loop may run side by side when the model asks for several at once.
PARALLEL_TOOLS: frozenset[str] = frozenset(
    {"read", "glob", "grep", "read_image", "web_fetch", "web_search"}
)


def tool_schemas() -> dict[str, dict[str, Any]]:
    """Return every tool definition, keyed by tool name."""
    from config.prompts.prompts_nekoclaw_tools import DSH_TOOL_SCHEMAS, NEKOCLAW_TOOL_SCHEMAS

    return {**DSH_TOOL_SCHEMAS, **NEKOCLAW_TOOL_SCHEMAS}


def build_tools(ctx: ToolContext, *, disabled: Iterable[str] = ()) -> list[AgentTool]:
    """Build the tools offered to one session's model, sorted by name."""
    from nekoclaw.runtime.tools.fs import build_fs_tools
    from nekoclaw.runtime.tools.goal import build_goal_tools
    from nekoclaw.runtime.tools.host_tools import build_host_tools
    from nekoclaw.runtime.tools.image import build_image_tool
    from nekoclaw.runtime.tools.jobs import build_job_tools
    from nekoclaw.runtime.tools.search import build_search_tools
    from nekoclaw.runtime.tools.shell import build_shell_tool
    from nekoclaw.runtime.tools.todo import build_todo_tool
    from nekoclaw.runtime.tools.web import build_web_tools

    schemas = tool_schemas()
    tools: list[AgentTool] = [
        *build_fs_tools(ctx, schemas),
        *build_search_tools(ctx, schemas),
        *build_image_tool(ctx, schemas),
        *build_shell_tool(ctx, schemas),
        *build_job_tools(ctx, schemas),
        *build_web_tools(ctx, schemas),
        *build_todo_tool(ctx, schemas),
        *build_goal_tools(ctx, schemas),
        *build_host_tools(ctx, schemas),
    ]
    hidden = set(disabled)
    offered = [tool for tool in tools if tool.name not in hidden]
    for tool in offered:
        expected = "parallel" if tool.name in PARALLEL_TOOLS else "sequential"
        assert tool.execution_mode == expected, tool.name
    return sorted(offered, key=lambda tool: tool.name)
