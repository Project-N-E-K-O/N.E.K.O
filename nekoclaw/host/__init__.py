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

"""The NekoClaw host: everything around the agent runtime.

Design: ``docs/design/nekoclaw-rewrite.md``, sections 4 to 6 and 10; what
differs from it is listed in section 6.9 there. The host lives in the agent
process. Nothing here runs at startup: the ledger file is opened, the runtime
is started and the gateway begins to listen on first use.

Modules:

* ``ledger``       - the commission ledger (SQLite): commissions, events,
  outbox, preferences. Status changes go through one function.
* ``preferences``  - task preferences: stated ones apply at once, inferred
  ones after the second observation.
* ``permissions``  - ``permissions.json`` and the two places it is consulted.
* ``plugin_runs``  - the plugin list and the one caller of ``/runs``.
* ``selection``    - plugin visibility and the local half of the two-stage
  filter, ported from the analyzer.
* ``capabilities`` - the capability list: plugin entries, built-in tools
  (keyboard and mouse, browser), MCP tools.
* ``leases``       - which commission is using the desktop, and which the
  browser.
* ``model_capabilities`` - models known not to take pictures.
* ``decision``     - the decision model: a replaceable interface and the
  keyword / BM25 / coarse-screen / fine-selection implementation.
* ``task_brain``   - dispatch (two rounds), query, stopping, answers.
* ``reports``      - what she is told: results, questions, corrections.
  Written to the outbox first; ``outbox`` sends and retries.
* ``task_agents``    - starts and drives task agents; the host side of the
  runtime's callbacks.
* ``gateway``      - the MCP service on the loopback address that hands
  plugin entries, MCP tools and the built-in tools to task agents.
* ``paths``        - where the host keeps its files.
* ``service``      - puts the parts together.
* ``mount``        - one host per process, built on first use; the agent
  process registers itself here at startup.

The package may use ``config``, ``utils``, ``nekoclaw.runtime`` and
``plugin.settings``. It reaches plugins over HTTP only, and never imports the
analyzer it replaces or any memory code (``scripts/check_module_layering.py``).
"""
