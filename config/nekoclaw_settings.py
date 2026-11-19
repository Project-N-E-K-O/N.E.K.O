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

DISPATCH_TEXT_MAX_TOKENS = 600
"""Cap on the sentence she dispatches, as stored and as shown to the decision model."""

DISPATCH_USER_TEXT_MAX_TOKENS = 800
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

PREFERENCE_PROMPT_MAX_TOKENS = 300
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

QUERY_RESULT_MAX_TOKENS = 520
"""Cap on the text of one query answer."""

QUERY_ROW_MAX_TOKENS = 100
"""Cap on one commission line in a query answer: its title, where it stands,
what it used so far (``QUERY_USAGE_MAX_TOKENS``) and what came of it."""

QUERY_PERMISSION_ROW_MAX_TOKENS = 140
"""Cap on the line of a commission that waits for a permission. It names what
is to be allowed and lists the answers on offer, which in some languages do
not fit in ``QUERY_ROW_MAX_TOKENS`` next to a title of full length."""

QUERY_USAGE_MAX_TOKENS = 16
"""Room in a commission line for what the commission used so far (a number
of tokens, in a few words). The caps above were raised by about this much
when it was added, so a line says as much of its commission as before."""

QUERY_RECENT_SECONDS = 24 * 3600.0
"""How far back finished commissions are still listed by a query."""

FOLLOW_UP_RECENT_SECONDS = 24 * 3600.0
"""How long after a commission a task agent worked on is over a sentence that
adds to it still goes on with it, in its session. A sentence about something
longer ago is a commission of its own (unless its project takes it)."""

FOLLOW_UP_MIN_OVERLAP = 0.25
"""Least share of a sentence's own words that must occur in a commission that
is over, or in what came of it, for the sentence to go on with it. Higher
than for an addition to a commission under way (``SUPPLEMENT_MIN_OVERLAP``):
taken for a follow-up, a sentence is worked on with all that was said in the
earlier commission's session in front of it."""

FOLLOW_UP_ROWS = 8
"""Most commissions that are over a dispatched sentence is compared with."""

# ---------------------------------------------------------------------------
# Task agents
# ---------------------------------------------------------------------------

TASK_AGENT_MAX_CONCURRENT = 10
"""Task agents at work at the same time; further commissions wait in the queue."""

TASK_AGENT_CONTEXT_WINDOW = 200_000
"""Preset context window a task agent's session is managed for, in tokens,
unless the user set another for its model
(``ModelCapabilityStore.set_context_window``). A model whose window is known
to be smaller is given its own: what is used is the smaller of the two."""

TASK_AGENT_COMPACT_AT_TOKENS = 170_000
"""Preset length of a conversation from which it is cut down and summed up,
in a window of ``TASK_AGENT_CONTEXT_WINDOW``. In a window of another size
the point lies at the same part of it (17 in 20), and never so near its end
that the output and a summary have no room. The user may set another for a
model (``ModelCapabilityStore.set_compact_at``)."""

TASK_AGENT_KNOWN_CONTEXT_WINDOWS = {"free-agent-model": 128_000}
"""Windows of models that are known and cannot be learned, by model name.
Used in place of ``TASK_AGENT_CONTEXT_WINDOW`` for a model named here,
unless the user set one for it.

A window is otherwise learned from what a provider says when it refuses a
request as too long. The free tier's agent model refuses none: measured on
2026-11-18, it takes about 131,000 tokens of input and cuts a longer
request off without a word (requests of about 250,000 and of about 500,000
tokens, by its own count, both came back as 131,276 tokens of input, and
were answered). Managed for the preset window, a session with it would be
sent requests of up to ``TASK_AGENT_COMPACT_AT_TOKENS``, and lose part of
every one above 131,000 without anything to tell it. This is the model a
user who configured nothing has."""

TASK_AGENT_CONTEXT_WINDOW_MIN = 4_000
"""The smallest window a session is managed for. A model that takes less
cannot hold a task agent's instructions and tools."""

TASK_AGENT_INFERRED_WINDOW_MIN_TOKENS = 32_000
"""A provider that refuses a request as too long without stating its window
still tells one thing: the window is smaller than that request. The size of
the refused request is taken for the window from then on, when it is at
least this much. Below it nothing is concluded: a refusal of so short a
request is more likely about something else, and a window taken that small
by mistake would be believed for ``MODEL_CONTEXT_WINDOW_REMEMBERED_SECONDS``."""

TASK_AGENT_UNSURE_REFUSAL_TOKENS = 100_000
"""A request of more than this that a provider refuses outright, in words
that are not recognized, is taken for one that was too long: the
conversation is summed up and the request is sent once more. Likewise a
session opened again whose last request failed, with a conversation of more
than this: it is summed up before its first request.

It is the size at which a conversation was summed up while every session
was managed for 128,000 tokens. A model that task agents worked with then
never saw a longer request, so nothing is known of how it answers one;
without this, a session that outlasts a commission would fail every
commission from the first such refusal on, having grown past what its model
takes without anything to say so."""

MODEL_CONTEXT_WINDOW_REMEMBERED_SECONDS = 30 * 24 * 3600
"""How long a model's window, learned from what its provider said when a
request was too long, is believed. After that the setting counts again: if
the model still takes less, one request is refused, the conversation is
summed up, and the window is written down anew."""

TASK_AGENT_BRIEF_MAX_TOKENS = 2400
"""Cap on what the host says of one commission in the message that opens it
(what was dispatched, what the user said, how the user wants such things
done), and likewise on its own part of a task agent's system prompt. What
the task agent space adds to the system prompt and the entries picked for a
commission have caps of their own (``SPACE_BRIEF_MAX_TOKENS``,
``GATEWAY_TOOL_LIST_MAX_TOKENS``)."""

TASK_AGENT_SESSION_NAME_MAX_TOKENS = 16
"""Cap on the name a session is given from the title of its first commission."""

TASK_AGENT_CONTINUED_RESULT_MAX_TOKENS = 500
"""Cap on what a commission is told of the outcome of the one it follows up,
when the session's record of that one was removed since. More than a result
summary is stored with (``TASK_AGENT_RESULT_SUMMARY_MAX_TOKENS``), so one is
told whole."""

REPO_DOCUMENTS: tuple[tuple[str, str], ...] = (
    ("file", "AGENTS.md"),
    ("file", "CLAUDE.md"),
    ("file", ".claude/CLAUDE.md"),
    ("files", ".agent/rules"),
    ("files", ".cursor/rules"),
    ("file", ".github/copilot-instructions.md"),
    ("count", "docs/adr"),
)
"""What a repository keeps for agents, as a task agent that works in it is
told (``nekoclaw/host/repo_docs.py``): paths below the repository's root,
in the order they are listed. ``file`` is one file; ``files`` is a
directory whose files are listed one by one; ``count`` is a directory of
which only the number of files is told. Only names and sizes are read."""

REPO_DOCUMENTS_MAX_LINES = 15
"""Most lines of that list; when there are more files, a last line says how
many were left out."""

REPO_DOCUMENTS_MAX_TOKENS = 600
"""Cap on the whole of it, the sentence in front included. It goes with the
message that opens a commission, after what the commission is and the
entries picked for it, each of which has a cap of its own; from then on it
is part of what was said in the session, and paid with every request until
that is summed up. Fifteen lines of ordinary paths are some 330 tokens; the
cap leaves room for long ones."""

REPO_DOCUMENTS_SCAN_SECONDS = 3.0
"""How long a task agent's start waits for that list to be made. Looking at
the names in a few directories takes milliseconds on a disk; a folder on a
network share that does not answer takes for ever, and the commission is
at work by its status meanwhile. Past this it starts without the list."""

REPO_DOCUMENTS_SCANS_AT_ONCE = 4
"""Most looks for that list that may be under way. One that was given up on
goes on in its thread until the folder answers; with so many out, a task
agent starts without the list at once instead of taking another thread."""

SPACE_EVIDENCE_LOOK_SECONDS = 30.0
"""How long tidying waits for the look at the files the entries rest on:
whether a few hundred files exist, which takes well under a second on a
disk, and never ends on a share that does not answer. Past this, what was
found so far is used, the entry the look is at is left out this time, and
the rest of the tidying goes on."""

SPACE_EVIDENCE_BEFORE_SECONDS = 10.0
"""How long a note waits to be told whether the files it names are there,
before its write takes its turn. Nothing is held meanwhile; the task agent
that filed it waits for its answer. Past this, the files were not looked
at: the note is kept without them, and is told so."""

SPACE_EVIDENCE_ASK_SECONDS = 2.0
"""How long a write to the task agent space waits to be told whether files
that are on record with a note are still there. Asked while the write holds
the lock every write of the space takes, so a share that does not answer
would hold them all up. On a disk the answer takes far less than a
millisecond. Past this, nothing is known of those files: they stay on
record, and speak for nothing this time."""

SPACE_EVIDENCE_ASKED_AT_ONCE = 8
"""Most such questions that were given up on and are not answered, each in
a thread of its own until the file system answers it. While one is out,
nothing is asked about the drive or share it is about; with so many out
nothing is asked at all, and nothing is known of any file a note rests on,
until one of them is answered. A share that never answers keeps its one
until the host is started again."""

SPACE_EVIDENCE_LOOKS_AT_ONCE = 4
"""Most looks that were given up on and have not come back. Each is at one
entry, in a thread of its own, until the files of that entry answer; such
an entry is not looked at again meanwhile. With so many out no look is
started, and the entries that rest on files are passed over."""

REPO_DOCUMENT_PATH_MAX_TOKENS = 60
"""Cap on one path of that list as it is written out. A file may be called
anything, and what it is called goes into a task agent's instructions. A
path longer than that is left out of the list and counted among the ones
that are not listed."""

REPO_DOCUMENTS_ROOT_MAX_TOKENS = 120
"""Cap on the path of the repository's root as that list names it. With a
root longer than that there is no list: a path that is cut names another
place."""

REPO_DOCUMENT_DIRECTORY_MAX_FILES = 200
"""Most files of one directory that are taken into that list: its first by
name."""

REPO_DOCUMENT_DIRECTORY_MAX_SCANNED = 5000
"""Most names of one directory that are looked at to find those. Names
only: no file is opened or measured for it."""

REPO_ROOT_SEARCH_MAX_LEVELS = 32
"""How many directories up from the working directory a repository's root
(a ``.git``) is looked for."""

TASK_AGENT_RESULT_SUMMARY_MAX_TOKENS = 400
"""Cap on the result summary stored for a commission."""

TASK_AGENT_EVENT_TEXT_MAX_CHARS = 4000
"""Size of one stored ``text`` or ``tool_update`` event row (display only,
never sent to a model). Longer runs of output are split into several rows."""

TASK_AGENT_CHILD_RESULT_MAX_TOKENS = 3000
"""Cap on a child task agent's result when it is handed back to its parent."""

# -- the excerpt of the main dialogue a new session is handed ---------------

TASK_AGENT_EXCERPT_MAX_TOKENS = 1000
"""Cap on the excerpt of the main dialogue in the message that opens a
commission in a new session: what she and the user said just before, word for
word, from the newest back. It is in that session's conversation from then
on, so every request of the session carries it until the conversation is
summed up. 0 hands no excerpt over."""

TASK_AGENT_EXCERPT_MESSAGE_MAX_TOKENS = 300
"""Cap on one message within that excerpt: a long one (she read something
out, the user pasted a text) does not push the others out."""

TASK_AGENT_EXCERPT_MAX_AGE_SECONDS = 1800.0
"""How old a message may be to be part of an excerpt. What was said longer
ago is not what a sentence dispatched now refers to."""

TASK_AGENT_EXCERPT_KEPT_MESSAGES = 24
"""How many messages of the main dialogue the agent process keeps in memory
for each character to make excerpts from (``nekoclaw/host/conversation.py``).
More than an excerpt takes: short messages fit by the dozen."""

TASK_AGENT_EXCERPT_KEPT_CHARS = 4000
"""How much of one message is kept there, in characters: a bound on memory,
not on what a model is sent (that is the token cap above)."""

# -- what a commission uses, and how much it may -----------------------------

TASK_AGENT_USAGE_LIMIT_TOKENS = 5_000_000
"""How many tokens the work on one commission may use before the user is
asked whether it is to go on: what every model request sent and got back,
input read from a provider's cache included, the requests of the task agents
it started counted with its own. When that much is used the commission waits
for the user before its next request; allowed, it gets as much again from
there, and is asked again when that is used; refused, the task agent is told
to hand over what it has. A conversation at the length it is summed up from
(``TASK_AGENT_COMPACT_AT_TOKENS``) gets here in about 29 requests. 0 sets no
limit. Not applied in dsh, which makes its requests by itself."""

TASK_AGENT_USAGE_WIND_UP_REQUESTS = 4
"""How many model requests a task agent gets to hand over what it has once
the user allowed no more: enough to write it down and report. They are
counted with what the commission used and are not held back for it. When
they are used and nothing was reported, the commission is put down as stuck,
with the reason."""

# -- copying a session -------------------------------------------------------

TASK_AGENT_FORK_SECONDS = 60.0
"""How long the runtime gets to copy a session's record
(``TaskAgentManager.fork_session``): a record is a file of a few megabytes at
the most, and the time is mostly a runtime process coming up."""

TASK_AGENT_FORK_WITHHELD_TOOLS = (
    "write", "edit", "bash", "pwsh", "subagent", "capability_call", "note_lesson",
    "message_session", "schedule_wake", "ask_session",
)
"""The tools a session that is a copy of another is opened without. It has
the working directory of the session it was copied from, which may be at
work there: one working directory is written by one session at a time. A
copy reads, searches and answers. ``capability_call`` is among them: what
it reaches (plugin entries, the keyboard and mouse, the browser) can write
as well, and the gateway refuses a copy's calls besides; ``note_lesson``
writes into the task agents' space, which later commissions go by. Nor does
it write to other sessions or set itself a wake: a copy answers and is done
with, and a wake would keep a commission in it under way. The tools stay in
what the model is told of (as in the session they were copied from, so a
provider's cache of that is used) and answer that they are not available."""

# -- summing up a session nothing is at work in -------------------------------

TASK_AGENT_IDLE_COMPACT_ENABLED = False
"""Whether the host sums up the conversation of a session that has been idle
for a while (below), so that the commission that comes next sends a summary
instead of all of it. Off: it is one more model request for each time, and
what it saves depends on the provider and on whether anything comes next;
see the design, section 17."""

TASK_AGENT_IDLE_COMPACT_AFTER_SECONDS = 3600.0
"""How long after its last commission ended a session is summed up. Longer
than providers keep the head of a request in their cache: summed up while
that is kept, the next commission would lose what it could have read from
the cache for a tenth of the price."""

TASK_AGENT_IDLE_COMPACT_MIN_TOKENS = 100_000
"""How long a session's conversation has to be, as of its last request, for
it to be summed up while idle. A shorter one costs more to sum up than its
next requests save."""

TASK_AGENT_IDLE_COMPACT_MIN_GAP_SECONDS = 6 * 3600.0
"""Least time between two times the same session is summed up while idle."""

TASK_AGENT_IDLE_COMPACTS_PER_DAY = 6
"""Most times within 24 hours the host tells an idle session to sum itself
up, all sessions together: each is one model request of up to the length
of a whole conversation. Counted when the session is told to, whatever
becomes of the summary; those on their way to it count as well."""

TASK_AGENT_IDLE_COMPACT_SECONDS = 300.0
"""How long one such summing up may take, the session being opened for it
included; cut short after that, and when a commission is to be worked on in
the session."""

DEFERRED_COMMISSION_SECONDS = 3600.0
"""How long a deferred plugin result may stay open before the commission fails."""

# ---------------------------------------------------------------------------
# Sessions writing to one another, and being woken
# ---------------------------------------------------------------------------

SESSION_MESSAGE_MAX_TOKENS = 1500
"""Cap on one note a task agent writes to another session. A longer one is
refused, not cut: what does not fit is written to a file and its path sent."""

SESSION_MESSAGE_MAX_DEPTH = 2
"""How far notes may carry work away from what the user asked for. A
commission she dispatched is at depth 0 and writes notes of depth 1; a
commission that is filed for a note has that note's depth and writes one
deeper. A note deeper than this is refused: a commission filed for a note
may itself have one filed, and that one writes to nobody."""

SESSION_MESSAGE_TARGET_INTERVAL_SECONDS = 60.0
"""Shortest time between two notes of one session to the same other session."""

SESSION_MESSAGES_PER_HOUR = 20
"""Notes one session may write within an hour, to whomever. A question it asks
another session (``ask_session``) counts as one too."""

SESSION_ASKS_PER_HOUR = 6
"""Questions one session may ask other sessions within an hour (``ask_session``).
Each one has a copy of the other session's conversation answer it, and every
request of that copy carries the whole of that conversation."""

SESSION_ASK_MAX_REQUESTS = 8
"""Model requests the copy that answers such a question may make; with them
used up its run ends, and the asker is told what came of it. A question is
cut to ``SESSION_MESSAGE_MAX_TOKENS`` like a note."""

SESSION_MESSAGE_QUEUE_MAX = 50
"""Notes that may wait to be handed to one session at a time; with that many
waiting, a further one is refused."""

SESSION_MESSAGE_DEDUP_SECONDS = 600.0
"""Within this time the same words from one session to the same other one are
sent once; the second time the sender is told they went out already."""

SESSION_NOTICE_WAIT_SECONDS = 12 * 3600.0
"""How long the sender of a note that asked to be told when it was dealt with
waits for that word. After it, the sender is told it waited in vain."""

SESSION_NOTICE_TEXT_MAX_TOKENS = 300
"""Cap on what the sender of a note is told of how the turn it began ended:
the last words of that turn."""

SESSION_WAKE_BATCH_ROWS = 10
"""How many waiting things (notes, times that came) are handed to a session
in one go. What is left over follows with the next."""

SESSION_LIST_ROWS = 10
"""Sessions on one page of ``list_sessions``."""

SESSION_LIST_ROW_MAX_TOKENS = 80
"""Cap on one row of ``list_sessions``: name, character, project, where it
stands, its latest commission, when it was last in use."""

SESSION_LIST_LOOKED_AT = 300
"""How many sessions, the ones last in use, ``list_sessions`` looks through
for the words it is given."""

SESSION_LIST_COMMISSIONS = 3
"""How many of a session's latest commissions are looked through for those
words, by their titles."""

SESSION_RESULTS_MAX_TOKENS = 3000
"""Cap on what ``read_session`` hands back of a session's results and
reviews; each of them is short already (``TASK_AGENT_RESULT_SUMMARY_MAX_TOKENS``,
``TASK_AGENT_REVIEW_TEXT_MAX_TOKENS``)."""

SESSION_RESULTS_COMMISSIONS = 8
"""How many of a session's latest commissions ``read_session`` tells the
results of."""

SESSION_SEARCH_HITS = 20
"""Places ``read_session`` names for one search of a session's record."""

SESSION_SEARCH_EXCERPT_MAX_TOKENS = 50
"""Cap on the one line shown of each such place."""

SESSION_READ_SEGMENT_MAX_TOKENS = 2000
"""Cap on one stretch of a session's record handed back by ``read_session``.
More is read by asking for the stretch that follows."""

SESSION_READ_ENTRY_MAX_TOKENS = 600
"""Cap on one entry within such a stretch (one message, one tool result): its
beginning and its end are kept, so one long tool result does not take the
whole stretch."""

SESSION_RECORD_MAX_BYTES = 64 * 1024 * 1024
"""A session's record larger than this is not read by ``read_session``; the
task agent is told so and reads the results instead."""

SESSION_RECORD_ALIAS_SCAN_ENTRIES = 100000
"""How many entries of a directory are looked at, before it is searched, for a
second name (a hard link) of another session's record. Looked for only where
such a record has a second name at all; a directory with more entries than
this is then not searched, as it cannot be told that the record is not in it.
What a version control system keeps of itself (``.git``) is not counted: a
search does not read it."""

WAKE_MIN_DELAY_SECONDS = 600.0
"""Shortest time a task agent may ask to be woken after. A shorter one is
moved to this, and the task agent is told."""

WAKE_MAX_DELAY_SECONDS = 30 * 24 * 3600.0
"""Longest such time."""

WAKE_PENDING_MAX = 10
"""How many times a session may have set for itself and not yet come to."""

WAKE_DAILY_MAX = 300
"""How often within a day a session may be woken at its own asking."""

WAKE_NOTE_MAX_TOKENS = 200
"""Cap on what a task agent notes for itself with a time it sets."""

WAKE_COALESCE_SECONDS = 2.0
"""How long what is due for a session waits for more of the same, so that
several things that come together start one turn, not one each."""

WAKE_COALESCE_MAX_SECONDS = 10.0
"""The longest that waiting gets when more keeps coming."""

WAKE_HELD_RETRY_SECONDS = 300.0
"""After how long something that could not be handed over is tried again:
NekoClaw was switched off, or the session's commission waits for the user."""

WAKE_DUE_PAGE_ROWS = 1000
"""How many wake requests that are due are read from the ledger at a time. All
that are due are read, a page after the other."""

WAKES_CLOSE_WAIT_SECONDS = 1.0
"""How long shutting down waits for what is being handed to a session this
moment to be handed over, before it is cut off."""

WAKE_FAILED_RETRY_MAX_SECONDS = 3600.0
"""The longest pause between two tries to hand over something that failed
for a reason of the host's own (it doubles from ``OUTBOX_RETRY_BASE_SECONDS``)."""

REST_LOOK_RETRY_DELAYS: tuple[float, ...] = (0.2, 1.0)
"""The pauses before the ledger is asked again, after it could not be read,
for what the end of a run means: how its commission stands, whether task
agents it started are still at work, whether it has asked to be woken. Only
once these are used up is the end given up on, or does the commission end
as one that asked for nothing: ended on the first failure, a time it had
set would be dropped with it. Short, since the events of every other
session wait meanwhile."""

DSH_ANSWER_SETTLE_SECONDS = 2.0
"""How long the end of a turn of a dsh session waits to learn whether the
answers of its ``wait_agent`` calls reached dsh, where one is still on its
way as the turn ends. Whoever serves the call says so a moment after the
answer is written; a text whose answer is not known to have got there by
then is not told of as taken."""

TURNS_OWED_CLOSE_WAIT_SECONDS = 1.0
"""How long shutting down gives what turns that are over still owe (word to
the senders of the notes they took, and that those notes were taken) to be
written, where the ledger could not be written when they ended."""

# ---------------------------------------------------------------------------
# Task agent space (design section 12.1; ``nekoclaw/host/space.py``)
#
# Everything about it that is likely to be tuned is here: what is added to a
# task agent's instructions and how much, whether commissions are reviewed
# and which are skipped, which model does it, how its input is cut, and how
# much one commission may add. The texts are in
# ``config/prompts/prompts_nekoclaw_space.py``.
# ---------------------------------------------------------------------------

SPACE_BRIEF_GLOBAL_RULES_MAX_TOKENS = 400
"""Cap on the rules for every task agent (``AGENTS.md`` of the space) as they
are appended to a task agent's system prompt. A Chinese character is some
0.6 to 0.75 of a token here: 400 tokens are some 560 characters."""

SPACE_BRIEF_GLOBAL_INDEX_MAX_TOKENS = 500
"""Cap on the index of the space (``MEMORY.md``) in a task agent's system
prompt. New lines go to the top of an index, so what is cut is the oldest."""

SPACE_BRIEF_PROJECT_RULES_MAX_TOKENS = 400
"""Cap on the rules of the commission's project in a task agent's system prompt."""

SPACE_BRIEF_PROJECT_INDEX_MAX_TOKENS = 500
"""Cap on the index of the commission's project in a task agent's system prompt."""

SPACE_BRIEF_MAX_TOKENS = 2100
"""Cap on everything the space adds to a task agent's system prompt: the four
parts above (1800 together), their headings, the line each has when it was
cut, and the note in front of them, which says where the files are, how a
note is to be read and what a pending file is (some 195 tokens with an
ordinary path). It is paid with every request of the session. Nothing is
added while the space holds nothing for the commission. With all four parts
at their caps the whole is some 2040 tokens, as measured; the sixty that
are left are for a data root with a long path. A test keeps the whole
within the cap."""

SPACE_ENTRY_TITLE_MAX_TOKENS = 24
SPACE_ENTRY_DESCRIPTION_MAX_TOKENS = 40
"""Caps on an entry's title and on its line in an index."""

SPACE_ENTRY_CONTENT_MAX_TOKENS = 600
"""Cap on what one ``note_lesson`` call adds to an entry."""

SPACE_LESSONS_PER_COMMISSION = 3
"""Most notes one commission may add with ``note_lesson``, the ones of the
task agents it started counted with its own. What a task agent files goes
into an index as it is, and the newest lines of an index are what the next
task agents are shown: without a limit one commission that was led astray by
a page it read could fill that window with its own lines. Three lines are
about two thirds of it."""

SPACE_SAME_TOPIC_SIMILARITY = 0.6
"""Token-set similarity of two titles at or above which a new entry is taken
to be about the topic of an existing one, and is added to it instead of
becoming a file of its own."""

SPACE_EVIDENCE_MAX_PATHS = 5
"""Most files one note may name as its evidence; the rest are not looked at.
An entry several notes added to keeps the newest so many."""

SPACE_EVIDENCE_PATH_MAX_TOKENS = 80
"""A path longer than this is not taken for evidence. A long absolute path
on Windows is some forty tokens."""

SPACE_SEEN_TO_TAKE_EFFECT = 2
"""How many commissions must have filed a note for it to take effect when
nothing else speaks for it: one that is only inferred, or observed with no
file that exists. A commission and the task agents it started are one."""

SPACE_SEEN_BY_KEPT = 16
"""How many of the commissions that filed an entry are remembered, so that
none of them is counted twice. One that filed it before so many others did
could be counted again; the count itself is kept whole."""

SPACE_HELD_MAX_NOTES = 30
"""Most pending entries (notes that are not in effect yet) which are kept for
one project, or for the space as a whole, while they wait to be seen again.
With more, the ones filed longest ago are removed, file and all. They are
not remembered as removed: the same note may be filed again."""

SPACE_HELD_KEPT_SECONDS = 90 * 24 * 3600.0
"""How long a pending entry waits to be filed again before a round of tidying
removes it, counted by the day it was last filed."""

SPACE_REMOVED_MAX_RECORDS = 1000
"""Most entries the user removed that are remembered for one project, or for
the space as a whole, so that the same note is not kept again."""

SPACE_EVIDENCE_ENTRIES_PER_UPKEEP = 500
"""Most entries one round of tidying looks at for whether the files they
rest on are still there. With more, the next round goes on where this one
stopped."""

SPACE_EVIDENCE_NAMED_MAX_PATHS = 20
"""Most paths that are looked for in the words of an entry a review or an
archive summary made, to find the files it rests on."""

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

TASK_AGENT_REVIEW_ENABLED = True
"""Whether finished commissions are reviewed at all. With False no review is
written and no entry is made from one, so that model call is never made;
what task agents file themselves is still kept, and idle projects are still
archived."""

TASK_AGENT_REVIEW_MODEL_SLOT = "summary"
"""Which of the user's configured models writes reviews: ``summary`` (the
cheap one) or ``emotion``."""

SPACE_ARCHIVE_MODEL_SLOT = "summary"
"""Which of the user's configured models summarises a project that is
archived; as above."""

TASK_AGENT_REVIEW_DELAY_SECONDS = 2.0
"""How long after a commission ended its review is started: the report to her
goes out first, and the last events of the session are in the ledger."""

TASK_AGENT_REVIEW_MIN_TOOL_CALLS = 3
"""A commission whose task agents made fewer tool calls than this (the ones
in ``TASK_AGENT_REVIEW_UNCOUNTED_TOOLS`` not counted) is not reviewed: there
is too little to learn from, and a review costs one model call. The calls of
the task agents it started count with its own."""

TASK_AGENT_REVIEW_UNCOUNTED_TOOLS = ("report", "note_lesson", "todo_write")
"""Tool calls that say nothing about how much work a commission was."""

TASK_AGENT_REVIEW_FAILED_AFTER_SECONDS = 24 * 3600.0
"""How long a failed commission is left alone before it is reviewed. A failed
commission is often taken up again; reviewed at once, the review would miss
how it ended, and every commission is reviewed once at most."""

TASK_AGENT_REVIEW_INPUT_MAX_TOKENS = 7000
"""Cap on everything sent to the model for one review, the instructions
included."""

TASK_AGENT_REVIEW_PROCESS_MAX_TOKENS = 3100
"""Cap on the account of what the task agent did within that input. Of a
longer one the beginning and the end are kept. With every other part at its
own cap the whole still fits in ``TASK_AGENT_REVIEW_INPUT_MAX_TOKENS``, so
nothing is cut off at the end; a test keeps it so. It is what gives way
when the review's own instructions grow.

It is what is left of the input when what she dispatched may be 600 tokens
and what the user said 800 (``DISPATCH_TEXT_MAX_TOKENS``,
``DISPATCH_USER_TEXT_MAX_TOKENS``)."""

TASK_AGENT_REVIEW_PROCESS_LINE_MAX_TOKENS = 100
"""Cap on one line of that account (one tool call, or one thing said)."""

TASK_AGENT_REVIEW_INDEX_MAX_TOKENS = 400
"""Cap on each of the two indexes (the space's and the project's) shown to
the review, so it adds to an existing entry instead of repeating it."""

TASK_AGENT_REVIEW_PROJECTS_MAX_TOKENS = 300
"""Cap on the list of projects shown to the review."""

TASK_AGENT_REVIEW_FILED_MAX_TOKENS = 200
"""Cap on the list of what the commission filed itself, shown to the review
so that it does not file the same again."""

TASK_AGENT_REVIEW_OUTPUT_MAX_TOKENS = 1500
"""``max_completion_tokens`` of a review: the review itself, up to
``TASK_AGENT_REVIEW_MAX_ENTRIES`` entries and the project. What may be kept
of an answer (the caps below, and those of a title, a description, the
files an entry rests on and a project) comes to some 1230 tokens with the
JSON around it; a test keeps it within the cap. The fifth that is left over
is for the provider counting otherwise than this code does, Chinese above
all: an answer cut off inside its JSON is worse than room that is not
used. An answer that is cut off all the same is not thrown away whole: the
review and the entries that are complete are kept."""

TASK_AGENT_REVIEW_TEXT_MAX_TOKENS = 400
"""Cap on the review as it is kept. Its prompt asks for 250 words at most,
which is within it (some 330 tokens of English; 400 tokens are some 560
Chinese characters); a longer one is ended where a sentence ends."""

TASK_AGENT_REVIEW_MAX_ENTRIES = 2
"""Most entries one review may file. Few on purpose: a review is one
commission's view, and every entry is a line later task agents are shown."""

TASK_AGENT_REVIEW_ENTRIES_WHEN_STOPPED = 0
"""Most entries the review of a commission the user stopped may file. A
commission is often stopped because it went wrong, and what it did then is
not what later commissions should learn from; its review is still written."""

TASK_AGENT_REVIEW_ENTRY_CONTENT_MAX_TOKENS = 200
"""Cap on the text of one entry a review files, and of one the summary of a
project files. Their prompts ask for two or three sentences of 120 words at
most: some 160 tokens of English, and 200 tokens are some 280 Chinese
characters. A longer one is ended where a sentence ends, since the entry
stays in the space and is read by later task agents."""

TASK_AGENT_REVIEW_ENTRY_EVIDENCE_MAX_PATHS = 2
"""Most files a review, or the summary of a project, may name as what one of
its entries rests on."""

TASK_AGENT_REVIEW_ENTRY_EVIDENCE_PATH_MAX_TOKENS = 40
"""Cap on one of those paths as the model writes it; a longer one is not
looked at. A path below a working directory is some ten tokens, a full one
some twenty-five."""

TASK_AGENT_REVIEWS_PER_UPKEEP = 3
"""Most late reviews one round of tidying has a model write (failed
commissions nobody took up again, and reviews a shutdown cut off). One whose
commission was taken up again while the model wrote is thrown away and not
counted, though the model was asked: that is rare, and counting it would
keep a commission that is due waiting for the next round."""

TASK_AGENT_REVIEWS_LOOKED_AT_PER_UPKEEP = 20
"""Most commissions one round of tidying looks at for a late review, the
oldest first. One that is too short to review costs no model call and does
not use up ``TASK_AGENT_REVIEWS_PER_UPKEEP``."""

SPACE_ARCHIVE_INPUT_MAX_TOKENS = 5000
"""Cap on everything sent to the model for the summary of a project that is
archived."""

SPACE_ARCHIVE_REVIEWS = 12
"""How many of a project's reviews, the newest, its summary is written from."""

SPACE_ARCHIVE_REVIEW_MAX_TOKENS = 300
"""Cap on each of those reviews within that input."""

SPACE_ARCHIVE_REVIEWS_MAX_TOKENS = 3000
"""Cap on all of them together."""

SPACE_ARCHIVE_INDEX_MAX_TOKENS = 800
"""Cap on the project's index within that input."""

SPACE_ARCHIVE_SUMMARY_MAX_TOKENS = 600
"""Cap on the summary as it is kept. Its prompt asks for 300 words at most."""

SPACE_ARCHIVE_MAX_ENTRIES = 2
"""Most entries the summary of a project may file for every project."""

SPACE_ARCHIVE_OUTPUT_MAX_TOKENS = 1800
"""``max_completion_tokens`` of that summary. What may be kept of an answer
(the summary, its line for the index, and ``SPACE_ARCHIVE_MAX_ENTRIES``
entries with the caps a review's entries have) comes to some 1400 tokens
with the JSON around it; a test keeps it within the cap. The rest is for
the provider counting otherwise than this code does; see
``TASK_AGENT_REVIEW_OUTPUT_MAX_TOKENS``."""

SPACE_CONSOLIDATE_ENABLED = True
"""Whether a round of tidying has a model look over the entries of a layer
(the space as a whole, or one project in use) for ones that say the same
thing, have gone out of date, are easily confused, or say "yesterday".
With False that model call is never made; nothing else changes."""

SPACE_CONSOLIDATE_MODEL_SLOT = "summary"
"""Which of the user's configured models does that; see
``TASK_AGENT_REVIEW_MODEL_SLOT``."""

SPACE_CONSOLIDATE_LAYERS_PER_UPKEEP = 2
"""Most layers one round of tidying has looked over; each costs one model
call. A layer is looked over only when an entry of it was added or changed
since it was last looked over, and only when it has
``SPACE_CONSOLIDATE_MIN_ENTRIES`` entries the host made. The layers looked
over longest ago go first; the rest wait for the next round."""

SPACE_CONSOLIDATE_MIN_ENTRIES = 2
"""Least number of entries the host made (in effect or pending) a layer must
have to be looked over: with one there is nothing to compare."""

SPACE_CONSOLIDATE_INPUT_MAX_TOKENS = 7000
"""Cap on everything sent to the model to look over one layer, the
instructions included. The two parts below have caps of their own (5400
together); a test keeps the whole within the cap with both at theirs."""

SPACE_CONSOLIDATE_LIST_MAX_TOKENS = 2400
"""Cap on the list of the layer's entries, one line each (title, description,
how it is known, its state): the entries in effect, the pending ones, and
the ones the user wrote. A line is some 40 tokens, 90 at most."""

SPACE_CONSOLIDATE_TEXTS_MAX_TOKENS = 3000
"""Cap on the full texts within that input: of the entries that are new or
changed since the layer was last looked over, then of the pending ones."""

SPACE_CONSOLIDATE_TEXT_MAX_TOKENS = 400
"""Cap on the text of one entry within those."""

SPACE_CONSOLIDATE_MAX_OPERATIONS = 8
"""Most changes the model may ask for in one layer; more are not made."""

SPACE_CONSOLIDATE_REASON_MAX_TOKENS = 40
"""Cap on why an entry is out of date, as it is kept in the entry."""

SPACE_CONSOLIDATE_MERGED_MAX_TOKENS = 1500
"""Most an entry may say once the texts of another were put into it by
tidying; a merge that would make it longer is not made. Two or three texts
of ``SPACE_ENTRY_CONTENT_MAX_TOKENS``."""

SPACE_CONSOLIDATE_OUTPUT_MAX_TOKENS = 750
"""``max_completion_tokens`` of looking over one layer. What may be kept of an
answer (``SPACE_CONSOLIDATE_MAX_OPERATIONS`` changes of the longest kind)
comes to some 600 tokens with the JSON around it; a test keeps it within
the cap. The rest is for the provider counting otherwise than this code
does; see ``TASK_AGENT_REVIEW_OUTPUT_MAX_TOKENS``. An answer that is cut off
all the same keeps the changes that are complete."""

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

GATEWAY_COMPACTIONS_META_KEY = "nekoclaw/compactions"
"""Where a task agent's runtime tells, under a ``tools/call``'s ``_meta``, how
often the session's conversation has been summed up so far. A group of
built-in tools handed over before a summary is not in the conversation after
it: when the number has changed, the gateway hands the group over anew the
next time one of its tools is called. A client that names none is taken to
hold what it was handed."""

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

GATEWAY_CLEARED_CALL_SECONDS = 30.0
"""How long what the gateway ruled on before a call was made (the permission,
the user's agreement to open a browser they closed) waits for that call. The
call follows at once, so this only has to cover a busy moment. When it is up
the ruling is dropped, and an agreement to open the browser with it, whether
or not another call comes by: a call whose turn was stopped never comes, and
one that comes later is ruled on anew."""

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

KEYS_RETRY_UNTIL_SHUTDOWN_SECONDS = 5.0
"""Until how long after the agent process began to shut down held keys that
the first try after the host stopped could not let go of are tried once more
(after the browser). Bounded by what is left before then, and skipped when
nothing is. The launcher's 8 seconds start before this process's shutdown
does: in merged mode connections are drained first, for up to
``MERGED_CONNECTION_DRAIN_TIMEOUT`` (1 second, ``launcher_core/runtime.py``).
So, scheduling delays aside, this ends about 6 seconds into the launcher's 8.
The last 2 are an estimate, not a guarantee, for what comes after: queueing
the final stats save, waiting for it (bounded only by the save itself;
``report_timeout`` bounds just its report's socket waits, not a busy save
worker or the file lock), and then the last status update, which a slow save
can push past the launcher's end."""

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


# ---------------------------------------------------------------------------
# The dsh runtime (``nekoclaw/dsh/``)
# ---------------------------------------------------------------------------
# dsh (DeepSeek Harness) is an agent runtime the user may download. It is a
# developer preview whose releases break compatibility, and the permission
# guard (``nekoclaw/dsh/profile.py``) rests on one of its internal events, so
# exactly one version is used: the one everything here was verified against.

DSH_VERSION = "0.2.1rc2"
"""The release of ``deepseek-harness-runtime-bin`` on PyPI that is downloaded
and driven. Another version found on the disk is not used."""

DSH_PACKAGE = "deepseek-harness-runtime-bin"

DSH_WHEELS: dict[str, dict[str, str | int]] = {
    "win-x64": {
        "file": "deepseek_harness_runtime_bin-0.2.1rc2-py3-none-win_amd64.whl",
        "path": "bc/95/8164c49a5d3430add58db8aeba6546cd05c9ab7ddd2ea3e1acf174448d86",
        "sha256": "d38c18f5117859feedaaa791a0eea37b8644bc187118e7e6f3afa231a5381cb7",
        "size": 83860784,
    },
    "macos-arm64": {
        "file": "deepseek_harness_runtime_bin-0.2.1rc2-py3-none-macosx_14_0_arm64.whl",
        "path": "10/99/723905c63929056e05494e8a659bc8eb9adf671ee6712fb4316930b97ac8",
        "sha256": "96a173abd14141d5328a78791b69729cbfe22fa3e9daf1dc2600d0daa4d6bb28",
        "size": 90016291,
    },
    "macos-x64": {
        "file": "deepseek_harness_runtime_bin-0.2.1rc2-py3-none-macosx_14_0_x86_64.whl",
        "path": "7b/b5/8bd76d1247955ba1f502bd684f9307352a879d703475b547cb6934495b6e",
        "sha256": "80614eee24916ce97d692baf8aed3ba146ed00fc2155d266cb0ede98d9354139",
        "size": 93636346,
    },
    "linux-x64": {
        "file": "deepseek_harness_runtime_bin-0.2.1rc2-py3-none-manylinux_2_28_x86_64.whl",
        "path": "01/f3/1f7b9ea37ece210a5e432089bd63d048b2c45acc910561ec5e730c46a532",
        "sha256": "0a0a4eb85a22cd9339972e6a5ced77a27ec791353a5b220e3e10b8cdbbdeca39",
        "size": 94615763,
    },
    "linux-arm64": {
        "file": "deepseek_harness_runtime_bin-0.2.1rc2-py3-none-manylinux_2_28_aarch64.whl",
        "path": "2e/36/dd6d86ab8e4dd4ec2b3ed09ccb3bb28864d86c4e1c31189f7753bd0c07c6",
        "sha256": "71ed869e3e9a0e3ea398521730889d2d7e154767834165f1553755818e3f2a78",
        "size": 96833408,
    },
}
"""The file of ``DSH_VERSION`` for each platform dsh is released for, by
dsh's own platform tag: its name, where it lies below ``packages/`` on a PyPI
host, its SHA-256 and its size in bytes. The release is not signed; a
download whose SHA-256 differs is deleted and not installed. There is no
build for Windows on ARM."""

DSH_MIN_MACOS = 14
DSH_MIN_GLIBC = (2, 28)
"""The oldest macOS and the oldest C library of a Linux the files above run
on, as their names say (``macosx_14_0``, ``manylinux_2_28``). On an older
system dsh would be installed and then not start, so it counts as a platform
dsh is not released for."""

DSH_DOWNLOAD_HOSTS: tuple[str, ...] = (
    "https://files.pythonhosted.org/packages",
    "https://pypi.tuna.tsinghua.edu.cn/packages",
    "https://mirrors.aliyun.com/pypi/packages",
)
"""Where the file is fetched from, tried in this order: PyPI, then two
mirrors of it that are reachable where PyPI is slow. They serve the same
file; the SHA-256 is checked whichever answered."""

DSH_DOWNLOAD_CONNECT_SECONDS = 15.0
DSH_DOWNLOAD_READ_SECONDS = 60.0
"""How long one host gets to accept the connection, and to send the next
piece of the file, before the next host is tried."""

DSH_SESSION_SLOTS = 16
"""How many sessions one dsh process can hold at a time. Each takes one of
this many model names the process is configured with, which is how a model
request is told from another session's (``nekoclaw/dsh/forward.py``). More
than ``TASK_AGENT_MAX_CONCURRENT``: a session that is closing still holds
its name while the next one starts."""

DSH_IDLE_EXIT_SECONDS = 300.0
"""How long every session of a dsh process has to be idle before its sessions
are closed and the process is ended, which gives its memory back (230 MB and
more). A commission taken up again later resumes its dsh session. The same
time the built-in runtime's child process waits."""

DSH_READY_SECONDS = 60.0
"""How long a dsh process gets to answer the first request after it was
started. It answers within a second once its file is in the disk cache; the
first start after a download reads 270 MB."""

DSH_GUARD_READY_SECONDS = 10.0
"""How long after that first answer the permission guard is waited for to say
it was loaded. A process whose guard did not load is ended and not used: its
tools would act without asking."""

DSH_REQUEST_SECONDS = 60.0
"""How long dsh gets to answer a request that does no work of its own:
creating, resuming and closing a session, choosing its model."""

DSH_CLOSE_SECONDS = 15.0
"""How long a dsh process gets to exit after it was told to before it is
killed; closing a session waits this long too."""

DSH_MODEL_RETRIES = 2
"""How often dsh repeats a model request that failed in a way worth
repeating; the built-in runtime's ``ModelConfig.max_retries``."""

DSH_FORWARD_CONNECT_SECONDS = 30.0
"""How long the model endpoint gets to accept a forwarded request. How long
it may take to send the next piece of its answer is the session's
``ModelConfig.request_timeout_seconds``."""

DSH_HOST_TOOL_SECONDS = 50.0
"""How long a call of one of the host's tools may take on dsh. dsh gives a
tool of a service it was handed 60 seconds and then fails the call, with no
way to ask for more; a call that would run into that is answered before it
does, saying that it is still under way."""

DSH_SPILL_KEPT_DAYS = 30
"""How long dsh keeps the full text of a tool result too long for the
context; it removes older ones when it starts."""

DSH_STDERR_LINES_KEPT = 40
"""How many of the last lines dsh wrote to its standard error are kept, for
the reason given when it could not be started."""

DSH_RETRY_SECONDS = 300.0
"""How long after dsh could not be made ready (its process did not come up,
or did not load the permission guard) new commissions go to the built-in
runtime without trying dsh again. A commission that began in dsh is tried
there all the same: only dsh has what was said in it."""
