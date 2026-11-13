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

"""The built-in capabilities of NekoClaw: the keyboard and mouse, and the browser.

Design: ``docs/design/nekoclaw-rewrite.md``, section 11. Each capability is a
group of small tools (take a screenshot, click, type; open a page, read it,
click an element). A task agent calls them one step at a time from its own
loop, through the host's capability gateway, so the built-in runtime and any
other runtime get the same tools. Nothing here runs a loop of its own and
nothing here calls a model.

Modules:

* ``desktop``   - the keyboard-and-mouse tools: screenshots of the desktop
  and input at coordinates on them.
* ``browser``   - the browser tools: a browser found on this computer and
  driven over its debugging protocol, pages read as text snapshots whose
  elements carry ids.
* ``provider``  - lists the tools for the host and carries their calls out
  (``nekoclaw.host.capabilities.BuiltinCapabilities``).

The definitions of the tools as a model sees them, and the paragraphs of
guidance that go with them, are in ``config/prompts/prompts_nekoclaw_builtin.py``
and ``config/prompts/prompts_nekoclaw.py``.

The package may use ``config``, ``utils`` and ``nekoclaw.host.capabilities``
(the types the host is handed). It is used by the agent process, which tells
it which switches are on.
"""
