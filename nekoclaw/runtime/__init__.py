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

"""Built-in agent runtime: a complete harness without memory.

A task agent (an execution session) runs its own loop here and calls tools to do
its work. Nothing in the application imports this package yet; later changes
connect it to the host.

Modules:

* ``loop``        - the provider/tool loop, ported from huggingface/tau.
* ``model``       - adapter from the loop to ``utils.llm_client``.
* ``tools``       - the built-in tools (files, search, shell, jobs, web, todo,
  goal) and the host-backed ones.
* ``permissions`` - the gate in front of the built-in tools.
* ``context``     - result cap, pruning, summary and overflow recovery.
* ``guards``      - repeated-call reminder and goal rounds.
* ``session``     - one task agent: loop, tools, context and transcript together.
* ``core``        - many sessions on one event loop; the in-process runtime.
* ``process``     - the same runtime in a child process.
* ``protocol``    - session spec, commands and events as JSON.
* ``host``        - the callbacks a host injects.

The names below are loaded on first use, so importing this package stays cheap
and starts no process, opens no connection and touches no data directory.
"""

from __future__ import annotations

from typing import Any

_EXPORTS = {
    "AgentRuntime": "nekoclaw.runtime.core",
    "InProcessRuntime": "nekoclaw.runtime.core",
    "SubprocessRuntime": "nekoclaw.runtime.process",
    "TaskAgentSpec": "nekoclaw.runtime.protocol",
    "GoalSpec": "nekoclaw.runtime.protocol",
    "ModelConfig": "nekoclaw.runtime.protocol",
    "RuntimeLimits": "nekoclaw.runtime.protocol",
    "HostBridge": "nekoclaw.runtime.host",
    "HostCallbacks": "nekoclaw.runtime.host",
    "PermissionRequest": "nekoclaw.runtime.permissions",
    "default_permission_decision": "nekoclaw.runtime.permissions",
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
