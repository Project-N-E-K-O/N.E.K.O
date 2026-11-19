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

"""Model-facing text of a task agent that runs on dsh (``nekoclaw/dsh/``).

dsh assembles its own system prompt, one paragraph per tool, and lets a
deployment put a paragraph in front of them and one behind. Both are the
same for every session of a dsh process, so what belongs to one session
(the host's instructions, and the sentence that says the language to write
to the user in is named by each commission) goes into the first message of
the session instead. The language itself is named in the message that opens
each commission.

The tools the host carries out reach dsh as tools of a service it is handed,
which dsh names ``mcp__<service>__<tool>``; the text here says them by those
names. Their definitions are the built-in runtime's
(``config/prompts/prompts_nekoclaw_tools.py``).

English text read by the model, not translated; see ``prompts_nekoclaw.py``.
dsh fills ``{{name}}`` in the two paragraphs with values of its own and
refuses a name it does not know, so neither may contain two opening braces.
"""

from __future__ import annotations

from config.prompts.prompts_nekoclaw import NOTE_LESSON_GUIDANCE

# The service the host's own tools are served as, and so the prefix dsh gives them.
HOST_SERVICE = "host"
HOST_TOOL_PREFIX = f"mcp__{HOST_SERVICE}__"

# The paragraph behind dsh's own: the host's tools, in place of the built-in
# runtime's ``REPORT_GUIDANCE`` and ``NOTE_LESSON_GUIDANCE``. A question for
# the user is a ``waiting`` report here: dsh ends a call of such a tool after
# a minute, which an answer from a person does not fit into.
DSH_HOST_TOOLS_SECTION = (
    f"Tools whose names start with {HOST_TOOL_PREFIX} are carried out by the host you work for.\n"
    "\n"
    f"Use {HOST_TOOL_PREFIX}report to tell whoever commissioned you how the commission stands. Send "
    "`progress` for a milestone worth knowing about while you keep working. When the work of a "
    "turn is finished, end it with `ready`, `blocked`, `failed` or `waiting`, as the only tool "
    "call of that reply; a turn that ends without a report leaves them with nothing but your "
    "last message.\n"
    "\n"
    "To ask the user something, report `waiting` with the question as the summary. The turn ends "
    "there, and the answer arrives as your next message.\n"
    "\n"
    # The built-in runtime's own words, with the tool called what dsh calls it.
    + NOTE_LESSON_GUIDANCE.replace("note_lesson", f"{HOST_TOOL_PREFIX}note_lesson")
)

# Added to what the host tells a session in its first message (``DSH_FIRST_MESSAGE``)
# when the session is offered the subagent tools. Not in the paragraph above:
# the system prompt is one for every session of a dsh process, and a task
# agent started by another is offered none of them.
DSH_SUBAGENT_SECTION = (
    f"{HOST_TOOL_PREFIX}subagent starts another agent on a part of the work and returns at once; "
    "its result arrives as a message once it has finished. A message for you is delivered when "
    f"your current turn ends, not in the middle of it: {HOST_TOOL_PREFIX}wait_agent waits for one "
    "and hands it to you as its result."
)

# The first message of a session: what the host tells this one commission,
# then the work. Rendered with str.replace on the markers.
DSH_FIRST_MESSAGE = (
    "[From the host, for this commission]\n"
    "<<INSTRUCTIONS>>\n"
    "\n"
    "[Your work]\n"
    "<<WORK>>"
)

# What a model request made right after a closing report is answered with,
# without being sent to the model: the report was the turn's last word.
DSH_AFTER_REPORT_REPLY = "Reported."

# Result of one of the host's tools whose call did not finish in the time dsh allows a call.
DSH_TOOL_TOOK_TOO_LONG = (
    "The call did not finish in the time a tool call may take here, and waiting for it was given up. "
    "No result came of it, but what it had set going may have been done all the same: "
    "after starting a subagent, look with list_agents before you start another."
)
