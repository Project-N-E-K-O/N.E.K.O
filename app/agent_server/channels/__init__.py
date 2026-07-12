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

"""Per-channel dispatch handlers for the agent analyzer.

One module per ``execution_method`` of the former ``_do_analyze_and_plan``
elif chain in the monolithic ``app/agent_server.py``. Every submodule
exposes the same symmetric coroutine::

    async def dispatch(result, *, messages, lanlan_name,
                       conversation_id, trigger_user_msg_sig) -> None

so the facade dispatcher stays a uniform six-way branch.
"""

from . import computer_use, browser_use, openclaw, openfang, user_plugin, mcp  # noqa: F401
