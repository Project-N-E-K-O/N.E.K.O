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

"""NekoPower: the base that runs task agents (execution sessions).

Design: ``docs/design/nekopower-rewrite.md``. Three parts: the built-in runtime
a task agent runs in (``nekopower.runtime``), the host around it (``nekopower.host``:
task brain, ledger, gateway, permissions) and the built-in tools the host
offers task agents (``nekopower.builtin``: keyboard and mouse, browser).
Importing any module here starts no process, opens no connection and touches
no data directory.
"""

# Local session-state tools provided by both runtimes, with host-managed admission.
TASK_CONTROL_TOOLS = frozenset({"todo_write", "create_goal", "get_goal", "update_goal"})
