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

"""Starting and driving task agents: commissions, each worked on in a session.

``TaskAgentManager`` is the only user of the runtime's driver interface
(``nekopower.runtime``). It

* starts a runtime when the first task agent that runs in it is needed, never
  before, and gives every commission the runtime it runs in: the built-in
  one, or dsh for a user who installed it (``runtimes.py``);
* gives every commission the session it is worked on in (``ledger.Session``):
  the one session of its project, for a commission filed under a project;
  the session of the commission it follows up; a new one otherwise, and for
  every task agent another task agent starts. A session is a conversation
  that is kept and gone on with, so one takes several commissions, one
  after the other: the commission at work holds its session, and the next
  one waits ``queued`` until that one is over and has left the runtime. A
  commission that waits for the user's answer holds its session meanwhile.
  The runtime knows sessions, the ledger and everything else here know
  commissions: one session, one commission at a time, so the one tells the
  other (``_held_by``);
* opens the runtime's session anew for every commission and closes it when
  the commission is over, as it always did; what was said stays in the
  session's directory and the next commission goes on from it. What a
  commission is (what was dispatched, what the user said, how the user wants
  such things done, the entries picked for it) is the message that opens it
  (``_opening``); the system prompt says nothing of one commission and is
  kept as the session was first opened with it (``runtime/snapshot.py``);
* keeps at most ``TASK_AGENT_MAX_CONCURRENT`` task agents at work and leaves the
  rest ``queued`` until a place is free. Task agents started by other task agents
  count the same;
* turns the runtime's events into ledger rows and status changes, and into
  reports for her;
* serves the runtime's host callbacks: permissions, questions for the user,
  reports, lessons, web search and child task agents;
* hands a task agent what the task agent space (``space.py``) holds for its
  commission when it starts, files the commission under the project it names,
  and lets task agents read the space without asking and never write it;
* keeps count of what the model requests of every session use, with the
  session and with the commission (``Ledger.add_usage``), and asks the user
  before the work on one commission uses more than it may (``_cb_budget``);
* makes a session from another by copying what was said in it
  (``fork_session``), and, when that is switched on, sums up the
  conversation of a session that has been idle for long (``_idle_compact``);
* lets a session of one commission (``single``) end once its last commission
  has been over for ``SESSION_SINGLE_LIFETIME_DAYS`` days: nothing gives it
  new work after that (``Ledger.session_alive``, asked wherever new work is
  handed to a session).

Status changes all go through ``Ledger.transition``. In particular a
commission that was stopped is ``cancelled`` before its session is told to
stop, so nothing the session still says can turn it into anything else.

Package layout (split from the former single-file ``nekopower/host/task_agents.py``;
the import path ``nekopower.host.task_agents`` is unchanged):

- ``types``: module-level constants, package logger, dataclasses, types, and pure helpers.
- ``idle_compact``: idle-session compaction and service-session teardown (``IdleCompactMixin``).
- ``billing_usage``: model tier selection, free-quota metering, usage accounting, and budget limits (``BillingUsageMixin``).
- ``callbacks``: permission gating, user questions, reports, lessons, and web search (``CallbacksMixin``).
- ``forks_beside``: session forking, shadow-copy carry-on/hand-back, and beside members (``ForksBesideMixin``).
- ``messaging``: cross-session messaging, resting commissions, and scheduled wakes (``MessagingMixin``).
- ``delegation``: leader hand-over, child task agents, and session/file holds (``DelegationMixin``).
- ``opening``: opening prompt assembly, space brief, repo docs, and project filing (``OpeningMixin``).
- ``manager``: ``TaskAgentManager`` (``__init__`` and core scheduling/events/driving/shutdown).
- ``__init__``: compatibility facade re-exporting all original top-level names; mixin and
  manager methods read test-patched facade symbols late through ``_task_agents_facade``.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import contextlib  # noqa: F401
import dataclasses  # noqa: F401
import json  # noqa: F401
import logging  # noqa: F401
import sqlite3  # noqa: F401
import uuid  # noqa: F401
from collections import deque  # noqa: F401
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable  # noqa: F401
from contextlib import AsyncExitStack, asynccontextmanager  # noqa: F401
from dataclasses import dataclass, field  # noqa: F401
from typing import Any, Protocol  # noqa: F401

from nekopower.host import call_audit  # noqa: F401
from nekopower.host.capabilities import CapabilityRegistry  # noqa: F401
from nekopower.host.execution_audit import ExecutionAudit  # noqa: F401
from nekopower.host.ledger import (  # noqa: F401
    ACTIVE_STATUSES,
    COMMISSION_OVER_REASON,
    ENDED_STATUSES,
    PROJECT_KINDS,
    Commission,
    Ledger,
    MessageLimits,
    Session,
    SessionRow,
    Transition,
    WakeRequest,
    new_session_id,
)
from nekopower.host.paths import DEFAULT_PROJECT, DSH_SESSION_NAME, TRANSCRIPT_NAME, HostPaths  # noqa: F401
from nekopower.host.permissions import (  # noqa: F401
    CHOICES,
    DENIED_BY_SETTINGS,
    DENIED_BY_USER,
    GrantScope,
    OPEN_BESIDE,
    OPEN_SESSION,
    SPACE_COMMAND,
    USAGE_LIMIT,
    PermissionAsk,
    PermissionDecision,
    PermissionGate,
    SessionActionScope,
)
from nekopower.host.plugin_runs import PluginRunner, audited_run_entry  # noqa: F401
from nekopower.host.preferences import PreferenceStore, feedback_text  # noqa: F401
from nekopower.host.reports import CHANNEL_TASK_AGENT, Reporter  # noqa: F401
from nekopower.host.runtimes import RUNTIME_BUILTIN, RUNTIME_DSH, Runtimes  # noqa: F401
from nekopower.host.tree_work import TreeWork  # noqa: F401
from nekopower import TASK_CONTROL_TOOLS  # noqa: F401

# Re-exports from ``types`` so existing imports and test monkeypatches on
# ``nekopower.host.task_agents.<attr>`` keep working unchanged.
#
# Rebind/monkeypatch semantics: ``TaskAgentManager`` methods live in
# ``manager`` and the domain mixin modules, whose globals resolve against
# their own module dicts. Every test-patched symbol on this facade
# (_FORK_REMOVE_RETRY_SECONDS, _SERVICE_CLOSE_POLL_SECONDS, _SERVICE_CLOSE_SECONDS, _SERVICE_HANDOVER_SECONDS, _SHUTDOWN_START_WAIT_SECONDS, _begun_in, _lies_in) is therefore read late via ``_task_agents_facade.<attr>``
# in ``manager`` and the mixins. When adding a new facade patch target in
# tests, route its read points through ``_task_agents_facade`` the same way.
# That includes ``types`` itself: its helpers call one another by their bare
# names (``_begun_in`` and ``_has_transcript`` call ``_in_dsh``), so a patch
# on the facade of one of those would not reach the others. The contract test
# (``test_task_agents_package_contracts.py``) checks ``types`` as well and
# fails on such a read once its symbol becomes a patch target.
from .types import (  # noqa: F401
    logger,
    LEAD_NAME,
    WEB_SEARCH_PLUGIN_ID,
    WEB_SEARCH_ENTRY_ID,
    _WEB_SEARCH_PARALLEL,
    _SHUTDOWN_START_WAIT_SECONDS,
    _SERVICE_CLOSE_SECONDS,
    _SERVICE_CLOSE_POLL_SECONDS,
    _SERVICE_HANDOVER_SECONDS,
    _FORK_REMOVE_RETRIES,
    _FORK_REMOVE_RETRY_SECONDS,
    _SESSION_NOT_GIVEN_BACK,
    _REACHED,
    _THEN,
    _CAUGHT_UP_SECONDS,
    _SHUT_DOWN_REASON,
    _QUOTA_REASON,
    _QUOTA_RUN_REASON,
    _QUIET_FAILURES,
    _STOPPED_MEANWHILE_REASON,
    _WOKEN_REASON,
    _ASK_NOT_HANDED_OVER_REASON,
    _ASK_NOT_ASKED_REASON,
    _STOPPABLE_CHILD_STATUSES,
    _CHILD_DISABLED_TOOLS,
    _COPY_CHAIN_MAX,
    _PRESET_ON_RECORD_WITHIN,
    _CLOSING_RETRY_SECONDS,
    _ADDRESSED_KINDS,
    _LAST_REQUESTS_KEPT,
    _USAGE_REASON,
    _SESSION_ENDED_REASON,
    _IDLE_COMPACT_KINDS,
    _IDLE_TIMER_MARGIN_SECONDS,
    _NO_PLACE_REASON,
    _STILL_WORKING,
    _CHANGING_TOOLS,
    _REPORT_TO_STATUS,
    SCREENSHOT_POOL,
    _STATUS_TO_REPORT,
    _REPLY_LANGUAGES,
    ModelConfigProvider,
    RuntimeFactory,
    UsageMeter,
    _search_queries,
    LessonSink,
    Handover,
    _clock_time,
    _until_tomorrow,
    ForkRefused,
    LedgerLessonSink,
    _SessionAction,
    _Beside,
    _Session,
    _ENDS_KEPT_MAX,
    _carried_on,
    _forked_work_text,
    _number,
    _ends_kept,
    _ended_as,
    _not_refused,
    _withdraw,
    _refused_answer,
    _refusal,
    _Handed,
    _Unsaid,
    _Found,
    _shows_work_taken,
    _may_be_a_record,
    _has_second_name,
    _lies_in,
    _shows_run_at_work,
    _in_builtin,
    _in_dsh,
    _has_transcript,
    _begun_in,
    _has_files,
    default_runtime_factory,
    answer_words,
    build_answers,
    _UsageCut,
)

# Keep this import at the very bottom: it imports ``manager`` and the mixin
# modules, which bind this facade module as ``_task_agents_facade`` for
# late-binding reads of the test-patchable symbols re-exported above.
from .manager import TaskAgentManager  # noqa: F401
