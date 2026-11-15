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

"""Budgets and limits of the NekoClaw host (``nekoclaw/host/``).

Design: ``docs/design/nekoclaw-rewrite.md``, sections 5, 6 and 10. Token
budgets bound text that reaches a model; they are applied with
``utils.tokenize.truncate_to_tokens``, never by counting characters.
"""

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

DISPATCH_FIRST_ROUND_SECONDS = 1.0
"""How long ``dispatch`` waits for the first round before answering "received".
The first round is local (duplicates, keyword and BM25 candidates, whether a
task agent can be started); only when nothing matches locally and no task agent
can be started does it make one small-model call."""

TASK_TOOL_TIMEOUT_SECONDS = 2.5
"""How long the main dialogue's task tools wait for the agent process, for
the whole request from connecting to the last byte of the answer. The host
answers a dispatch within ``DISPATCH_FIRST_ROUND_SECONDS`` and a query at
once; the rest is room for the local request. When it runs out she is told
that the task system did not respond, never that the task was taken."""

DISPATCH_TEXT_MAX_TOKENS = 300
"""Cap on the sentence she dispatches, as stored and as shown to the decision model."""

DISPATCH_USER_TEXT_MAX_TOKENS = 300
"""Cap on the user's own words that come with a dispatch."""

COMMISSION_TITLE_MAX_TOKENS = 24
"""Cap on a commission's title (derived from the dispatched sentence)."""

DUPLICATE_SIMILARITY = 0.7
"""Token-set similarity at or above which a dispatch repeats an active commission."""

DUPLICATE_SIMILARITY_SAME_USER_TEXT = 0.4
"""The same bar when the user's words are identical to the active commission's."""

SUPPLEMENT_MIN_OVERLAP = 0.15
"""Least similarity for a sentence to count as being about an active commission."""

# ---------------------------------------------------------------------------
# Turn check
# ---------------------------------------------------------------------------

TURN_CHECK_ENABLED = True
"""Whether a turn in which she called no task tool is checked afterwards
(design section 6.4). Off means nothing she only says is ever acted on."""

TURN_CHECK_INTENT_THRESHOLD = 0.2
"""The gate in front of the turn check, the one the analyzer had. The score is
what the small model that reads the user's input already gave for "needs
something outside the conversation" (0 to 1). A turn is checked unless that
score is known and below this value and no plugin keyword matches either; a
missing score never holds a turn back."""

TURN_CHECK_USER_TEXT_MAX_TOKENS = 300
"""Cap on the user's sentence shown to the turn check."""

TURN_CHECK_REPLY_MAX_TOKENS = 300
"""Cap on her reply shown to the turn check; the end of a long reply is kept
out, the opening is what says whether she agreed or declined."""

TURN_CHECK_OUTPUT_MAX_TOKENS = 120
"""``max_completion_tokens`` of the turn check: a verdict and one sentence."""

TURN_CHECK_TASK_MAX_TOKENS = 60
"""Cap on the task the turn check names, as quoted back to her."""

TURN_CHECK_STATUS_ANSWER_SECONDS = 60.0
"""How long after her question about her tasks was answered with one that is
done a claim that something is done, which no task matches, gets the softer
notice (if she meant those, nothing to do; if something else, put it right)
instead of "no record". Only the next check of her turns may use it; the
answer comes within the turn, and the check follows its end within seconds."""

QUESTION_TEXT_MAX_TOKENS = 300
"""Cap on a commission's question as it is put to her to pass on."""

PERMISSION_SUBJECT_MAX_TOKENS = 160
"""Cap on the command, path or name a permission question quotes. A longer one
keeps its beginning and its end with a mark in between that says the middle
was left out. It is the part that gives way: the answers on offer and the
instruction to hand the answer back come after it, and with this cap the
whole sentence stays within ``QUESTION_TEXT_MAX_TOKENS`` in every language."""

PERMISSION_SUBJECT_MIN_TOKENS = 24
"""The least room that part is given in one line of a query answer or a
brief, mark included."""

# ---------------------------------------------------------------------------
# What the decision model is shown
# ---------------------------------------------------------------------------

DECISION_LEDGER_ROWS = 3
"""Ledger rows shown to the fine selection ("the two or three related ones")."""

DECISION_LEDGER_ROW_MAX_TOKENS = 60
"""Cap on one ledger row in the fine-selection prompt."""

PREFERENCE_PROMPT_MAX_TOKENS = 150
"""Cap on the task preferences injected into the fine selection and into a
task agent's instructions (design section 5.1)."""

PREFERENCE_TEXT_MAX_TOKENS = 60
"""Cap on a single stored preference."""

PREFERENCE_INFERRED_THRESHOLD = 2
"""Observations an inferred preference needs before it takes effect."""

PREFERENCE_SAME_TENDENCY_SIMILARITY = 0.5
"""Token-set similarity at or above which two inferred observations count as
the same tendency."""

# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

QUERY_LEDGER_ROWS = 8
"""Most commissions one query answer lists."""

QUERY_RESULT_MAX_TOKENS = 400
"""Cap on the text of one query answer."""

QUERY_ROW_MAX_TOKENS = 80
"""Cap on one commission line in a query answer."""

QUERY_PERMISSION_ROW_MAX_TOKENS = 120
"""Cap on the line of a commission that waits for a permission. It names what
is to be allowed and lists the answers on offer, which in some languages do
not fit in ``QUERY_ROW_MAX_TOKENS`` next to a title of full length."""

QUERY_RECENT_SECONDS = 24 * 3600.0
"""How far back finished commissions are still listed by a query."""

# ---------------------------------------------------------------------------
# Task agents
# ---------------------------------------------------------------------------

TASK_AGENT_MAX_CONCURRENT = 10
"""Task agents at work at the same time; further commissions wait in the queue."""

TASK_AGENT_BRIEF_MAX_TOKENS = 1200
"""Cap on the host's instructions appended to a task agent's system prompt."""

TASK_AGENT_RESULT_SUMMARY_MAX_TOKENS = 400
"""Cap on the result summary stored for a commission."""

TASK_AGENT_EVENT_TEXT_MAX_CHARS = 4000
"""Size of one stored ``text`` or ``tool_update`` event row (display only,
never sent to a model). Longer runs of output are split into several rows."""

TASK_AGENT_CHILD_RESULT_MAX_TOKENS = 1500
"""Cap on a child task agent's result when it is handed back to its parent."""

DEFERRED_COMMISSION_SECONDS = 3600.0
"""How long a deferred plugin result may stay open before the commission fails."""

# ---------------------------------------------------------------------------
# Task agent space (design section 12.1; ``nekoclaw/host/space.py``)
# ---------------------------------------------------------------------------

SPACE_BRIEF_GLOBAL_RULES_MAX_TOKENS = 250
"""Cap on the rules for every task agent (``AGENTS.md`` of the space) as they
are appended to a task agent's system prompt."""

SPACE_BRIEF_GLOBAL_INDEX_MAX_TOKENS = 350
"""Cap on the index of the space (``MEMORY.md``) in a task agent's system
prompt. New lines go to the top of an index, so what is cut is the oldest."""

SPACE_BRIEF_PROJECT_RULES_MAX_TOKENS = 250
"""Cap on the rules of the commission's project in a task agent's system prompt."""

SPACE_BRIEF_PROJECT_INDEX_MAX_TOKENS = 350
"""Cap on the index of the commission's project in a task agent's system prompt."""

SPACE_BRIEF_MAX_TOKENS = 1400
"""Cap on everything the space adds to a task agent's system prompt: the four
parts above (1200 together), their headings and the lines that say where the
files are (under 200). It is paid with every request of the session. Nothing
is added while the space holds nothing for the commission."""

SPACE_ENTRY_TITLE_MAX_TOKENS = 24
SPACE_ENTRY_DESCRIPTION_MAX_TOKENS = 40
"""Caps on an entry's title and on its line in an index."""

SPACE_ENTRY_CONTENT_MAX_TOKENS = 600
"""Cap on what one ``note_lesson`` call, or one review, adds to an entry."""

SPACE_SAME_TOPIC_SIMILARITY = 0.6
"""Token-set similarity of two titles at or above which a new entry is taken
to be about the topic of an existing one, and is added to it instead of
becoming a file of its own."""

SPACE_PROJECT_NAME_MAX_TOKENS = 12
SPACE_PROJECT_DESCRIPTION_MAX_TOKENS = 40
"""Caps on a project's name and on its one-line description."""

SPACE_PROJECT_MATCH_MIN_CHARS = 3
"""Least length of a project's name (white space removed) for a commission to
be filed under it because the name occurs in what was dispatched. Shorter
names are too likely to occur by chance."""

SPACE_PROJECT_IDLE_SECONDS = 30 * 24 * 3600.0
"""How long after its last commission a project is archived."""

SPACE_TRANSCRIPT_KEPT_SECONDS = 30 * 24 * 3600.0
"""How long after a commission ended what was said in its session
(``transcript.jsonl`` and ``outputs/``) is kept. Its working directory is
never removed, nor are its review and the entries made from it."""

SPACE_UPKEEP_INTERVAL_SECONDS = 6 * 3600.0
"""Least time between two rounds of tidying the space (archiving idle
projects, removing old transcripts, reviewing failed commissions nobody took
up again). A round is started after a commission has ended, never at startup."""

SPACE_ARCHIVES_PER_UPKEEP = 2
"""Most projects one round of tidying archives; each costs one model call."""

SPACE_ARCHIVE_MAX_ATTEMPTS = 3
"""How many times a summary is asked for before an idle project is archived
without one."""

TASK_AGENT_REVIEW_DELAY_SECONDS = 2.0
"""How long after a commission ended its review is started: the report to her
goes out first, and the last events of the session are in the ledger."""

TASK_AGENT_REVIEW_MIN_TOOL_CALLS = 3
"""A commission whose task agent made fewer tool calls than this (reports,
lessons and the todo list not counted) is not reviewed: there is too little
to learn from, and a review costs one model call."""

TASK_AGENT_REVIEW_FAILED_AFTER_SECONDS = 24 * 3600.0
"""How long a failed commission is left alone before it is reviewed. A failed
commission is often taken up again; reviewed at once, the review would miss
how it ended, and every commission is reviewed once at most."""

TASK_AGENT_REVIEW_INPUT_MAX_TOKENS = 4000
"""Cap on everything sent to the model for one review, the instructions
included."""

TASK_AGENT_REVIEW_PROCESS_MAX_TOKENS = 1700
"""Cap on the account of what the task agent did within that input. Of a
longer one the beginning and the end are kept. With every other part at its
own cap the whole still fits in ``TASK_AGENT_REVIEW_INPUT_MAX_TOKENS``, so
nothing is cut off at the end; a test keeps it so."""

TASK_AGENT_REVIEW_PROCESS_LINE_MAX_TOKENS = 60
"""Cap on one line of that account (one tool call, or one thing said)."""

TASK_AGENT_REVIEW_INDEX_MAX_TOKENS = 250
"""Cap on each of the two indexes (the space's and the project's) shown to
the review, so it adds to an existing entry instead of repeating it."""

TASK_AGENT_REVIEW_PROJECTS_MAX_TOKENS = 200
"""Cap on the list of projects shown to the review."""

TASK_AGENT_REVIEW_OUTPUT_MAX_TOKENS = 800
"""``max_completion_tokens`` of a review: the review itself, up to
``TASK_AGENT_REVIEW_MAX_ENTRIES`` entries and the project."""

TASK_AGENT_REVIEW_TEXT_MAX_TOKENS = 300
"""Cap on the review as it is kept."""

TASK_AGENT_REVIEW_MAX_ENTRIES = 3
"""Most entries one review may file."""

TASK_AGENT_REVIEWS_PER_UPKEEP = 3
"""Most late reviews one round of tidying makes (failed commissions nobody
took up again, and reviews a shutdown cut off)."""

SPACE_ARCHIVE_INPUT_MAX_TOKENS = 3000
"""Cap on everything sent to the model for the summary of a project that is
archived."""

SPACE_ARCHIVE_OUTPUT_MAX_TOKENS = 600
"""``max_completion_tokens`` of that summary."""

# ---------------------------------------------------------------------------
# Capability gateway
# ---------------------------------------------------------------------------

GATEWAY_TOOL_LIST_MAX_TOKENS = 3000
"""Budget for the tool definitions one commission is handed up front. Entries
beyond it stay reachable through the gateway's search tool."""

GATEWAY_TOOL_DESCRIPTION_MAX_TOKENS = 200
"""Cap on one tool description served by the gateway."""

GATEWAY_SEARCH_RESULTS = 8
"""Most tools one gateway search returns."""

GATEWAY_RESULT_MAX_TOKENS = 4000
"""Cap on the text of one tool result served by the gateway."""

GATEWAY_REPLY_META_KEY = "nekoclaw/reply"
"""Where a task agent's runtime names the model reply a ``tools/call`` was
written in, under the request's ``_meta``: a number that changes with every
reply. A group of built-in tools handed over while the calls of one reply
are carried out is in hand from the next reply on, since the calls after it
in the same reply were written before the group could be read. A client that
names no reply has a group in hand as soon as it is handed over."""

GATEWAY_TOOL_GROUP_MAX_TOKENS = 2000
"""Cap on what loading one group of built-in tools (keyboard and mouse,
browser) hands a task agent: how the group is used, then the definitions of
its tools. The groups are not listed up front; a task agent loads one when
it needs it and the text then stays in its conversation, so this is what a
group costs a task that uses it. The text is ours
(``config/prompts/prompts_nekoclaw_builtin.py``); a test keeps every group
inside this cap, so in practice nothing is ever cut. It is kept below the
size at which the runtime prunes old tool results to their head and tail
(``ContextPolicy.prune_threshold_tokens``): a loaded group must not lose its
middle while the task agent still works from it. That holds for a group
handed over with its first call too: it comes as a block of its own in front
of what the call returned, and the runtime takes a result's first block
apart from the rest of its text, both when it caps a result and when it
prunes an old one."""

BUILTIN_OCCUPIED_WAIT_SECONDS = 120.0
"""How long a call of a built-in tool waits for the desktop or the browser
while another commission occupies it (``nekoclaw/host/leases.py``). After
that the task agent is told who has it and decides how to go on."""

TASK_AGENT_SCREENSHOTS_KEPT = 3
"""How many of the most recent screenshots stay in a task agent's context,
whichever tools they came from (the keyboard-and-mouse tool and the browser's
screenshot tools are counted together); older ones are replaced with a
placeholder. The old keyboard-and-mouse loop sent the current screenshot and
the two before it."""

MODEL_IMAGE_REFUSAL_REMEMBERED_SECONDS = 7 * 24 * 3600
"""How long "this model takes no pictures", learned from a provider's refusal,
is believed. After that the model is sent pictures again: if it still takes
none, one request is refused and sent once more without them, and it is
written down anew. A model that was misjudged, or has learned to see since,
gets its picture tools back by itself."""

BROWSER_SNAPSHOT_MAX_TOKENS = 3000
"""Cap on one page snapshot handed to a task agent. A longer one is cut and
ends with a line saying so and how to see the rest."""

BROWSER_DIALOG_TEXT_MAX_TOKENS = 200
BROWSER_PAGE_URL_MAX_TOKENS = 40
"""Caps on text a page writes that goes into a tool result outside the
snapshot: what a dialog of the page says, and the address of a page in the
list of pages. Longer text is cut and the number of characters left out is
told."""

GATEWAY_CONFIRMATION_QUESTION_MAX_TOKENS = 200
"""Cap on a plugin's request for confirmation when the gateway puts it to the
user; it reaches the main dialogue model as part of a report."""

# ---------------------------------------------------------------------------
# Shutting down
# ---------------------------------------------------------------------------
# The launcher gives the agent process 8 seconds to stop on its own
# (``graceful_shutdown_timeout`` of the Agent Server in
# ``launcher_core/runtime.py``, the same in merged mode) before it terminates
# it. In that time the process also stops the plugin lifecycle and the embedded
# plugin server, so the host gets a part of it, not all.

HOST_SHUTDOWN_BUDGET_SECONDS = 4.0
"""How long the agent process waits for the host to shut down before it goes
on with the rest of its own shutdown. The host's steps keep running after
that until the process exits; nothing is lost by it, because every status is
already in the ledger and unsent reports go out with the next start."""

BROWSER_KILL_AT_SHUTDOWN_SECONDS = 5.0
"""How long after the agent process began to shut down the browser the
browser tools started is killed, whatever the shutdown has got to by then.
The launcher gives the process 8 seconds and then ends it, without what it
started (``graceful_shutdown_timeout`` in ``launcher_core/runtime.py``). The
steps of the shutdown have bounds of their own (the host's above, the
background tasks', and some have none) that add up to more than that, so the
step that closes the browser is reached late or not at all: this does not
depend on it being reached."""

BROWSER_CLOSE_AT_SHUTDOWN_SECONDS = 4.0
"""How long the browser is given to close by itself once the shutdown gets to
it; less when less than that is left before it is killed (above)."""

BROWSER_CLOSE_AT_END_ALL_SECONDS = 4.0
"""How long the browser the tools started is given to close when the user
stops every task (``end_all``); killed after that. Asking it to close has
several steps that each have a time of their own and add up to more than the
page waits for an answer to that request (5 seconds)."""

SPACE_KEEPER_CLOSE_WAIT_SECONDS = 1.0
"""How long shutting down waits for what the task agent space does in the
background (a review, a round of tidying). A model call is cancelled at
once. Removing the files of an old session runs in a thread, which cannot
be stopped; it takes milliseconds, but a storage that does not answer must
not keep the outbox and the ledger from being closed. Past this time the
thread is left to finish by itself, and the session it works on stays
claimed until it has."""

HOST_CLOSE_STOP_WAIT_SECONDS = 1.5
"""How long closing the task brain waits for stops that are under way.
A stop writes the status first; what is left is one local request to the
plugin server and closing the task agent's session, which take well under a
second. A stop cut off here has still stopped the commission in the ledger,
and the plugin server and the runtime are shut down right afterwards."""

# ---------------------------------------------------------------------------
# Outbox
# ---------------------------------------------------------------------------

OUTBOX_RETRY_BASE_SECONDS = 1.0
OUTBOX_RETRY_MAX_SECONDS = 300.0
"""Reports that could not be delivered are retried after 1 s, 2 s, 4 s ...,
at most every five minutes."""

# ---------------------------------------------------------------------------
# Plugin short descriptions
# ---------------------------------------------------------------------------

PLUGIN_SHORT_DESCRIPTION_INPUT_MAX_TOKENS = 1000
"""Cap on a plugin's description as it is given to the small model that writes
a short description for a plugin whose manifest has none. A manifest's
description is uncapped."""

PLUGIN_SHORT_DESCRIPTION_RETRY_SECONDS = 600.0
"""How long a plugin whose short description could not be written waits before
it is tried again. Without it a model that is not configured, or one that keeps
failing, would be asked again every time the plugins are listed."""

PLUGIN_SHORT_DESCRIPTION_MAX_FAILURES = 3
"""How many times a short description is asked for a plugin and one version of
its description before it is not asked for again, until the description
changes. Failures are kept on disk with the short descriptions, so a restart
neither resets the count nor retries at once."""

PLUGIN_SHORT_DESCRIPTION_FAILURE_RESET_SECONDS = 86400.0
"""After this long since the last failure, a plugin's failures count from zero
again: a model that was not configured, or a network that was down, gets
another try the next day. At most ``PLUGIN_SHORT_DESCRIPTION_MAX_FAILURES``
requests per plugin and description in that time."""

PLUGIN_SHORT_DESCRIPTION_REREAD_SECONDS = 60.0
"""How long a short-descriptions file that exists but could not be read (held
open by another program) is left alone before it is read again."""

