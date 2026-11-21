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

"""The optional second runtime of task agents: dsh (DeepSeek Harness).

The built-in runtime (``nekoclaw.runtime``) is always there. dsh is a
program a user may download; once it is there, new commissions run in it
(design section 9). This package is everything the host needs for that:

* ``install``    - where dsh is kept below the data root; download, check,
  remove; which runtime the user wants.
* ``driver``     - ``DshRuntime``: the ``AgentRuntime`` interface over dsh's
  ACP server, with the same events as the built-in runtime.
* ``acp``        - the JSON-RPC connection to a dsh process.
* ``profile``    - what a dsh process is started with: its configuration,
  the permission guard, its environment.
* ``forward``    - the model endpoint dsh is given, which sends each request
  on to the session's real model.
* ``host_tools`` - the host's own tools (report, lessons, web search,
  subagents) as an MCP service for dsh sessions.
* ``endpoint``   - the listener on the loopback address serving those two.

The names below are loaded on first use, so importing this package stays
cheap and starts no process, opens no connection and touches no data
directory.
"""

from __future__ import annotations

from typing import Any

_EXPORTS = {
    "DshInstallation": "nekoclaw.dsh.install",
    "DshLaunch": "nekoclaw.dsh.profile",
    "DshRuntime": "nekoclaw.dsh.driver",
    "DshUnavailable": "nekoclaw.dsh.profile",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value
