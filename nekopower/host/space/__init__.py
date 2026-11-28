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

"""The task agent space: what task agents keep for later commissions.

One per user, shared by every task agent, kept as plain text below
``<data root>/nekopower/task_agent_space/`` (the layout is in
``paths.SpacePaths``). It follows the way Claude Code keeps its memory:

* ``AGENTS.md`` - rules, written by the user. The host never writes them.
* ``MEMORY.md`` - an index, one line per entry, handed to a task agent when it
  starts. The entries themselves are read when they are needed.
* ``memory/*.md`` - one entry per file, of four kinds: ``lesson`` (a reusable
  finding), ``reflection`` (what went wrong and what to do instead),
  ``reference`` (where something is found), ``progress`` (how a project
  stands). Corrections and approvals from the user are task preferences
  (``preferences.py``) and are not kept here.

There is one such set for the space as a whole and one for every project;
``_daily`` is the project of commissions that belong to none. A project nothing
was commissioned for in a while is moved to ``archive/`` whole, and is moved
back when a commission names it again.

An entry a task agent or a review filed says how it is known (``verified``,
``observed`` or ``inferred``), which files it rests on, and how many
commissions have filed it. From these the host tells, without asking a
model, whether a note takes effect: one that was verified does; one that was
observed does when a file it names exists; one that was only inferred does
once a second commission files the same. An entry has one of four states.
It is in effect (a line of its index points at it); or pending, a note that
has not taken effect yet (a file like any other that says so in its front
matter, with no line in any index: a task agent comes upon it only by
searching or reading the directory, and may confirm it by checking it and
filing it again as verified); or expired, once none of the files it rests
on exists any more (the file stays, the line goes); or the user removed it,
and then ``notes.json`` remembers it so that the same note is not kept
again.

Only the host writes here. Every write goes through one lock and replaces the
file atomically, so two task agents finishing at the same time cannot lose
each other's lines. What the user edited by hand is kept: an index is changed
one line at a time, and an entry that is added to gets a dated section at its
end while what it said before stays as it was.

Nothing here runs at startup and nothing asks a model. The reviews written
after a commission, and archiving, are in ``space_keeper.py``.


Package layout:
- ``models`` -- constants, dataclasses, regexes, exceptions, and shared state.
- ``markdown`` -- front-matter parsing, markdown rendering, index lines, and digests.
- ``evidence`` -- path matching and asynchronous evidence verification helpers.
- ``fs`` -- filesystem guards and cross-volume atomic directory moves.
- ``storage`` -- ``StorageMixin`` (file locks, I/O, index/rules/state persistence).
- ``projects`` -- ``ProjectsMixin`` (project registration, resolution, archiving).
- ``tidy`` -- ``TidyMixin`` (layer inspection, duplicate merges, outdated tidying).
- ``manager`` -- ``TaskAgentSpace`` assembly, entries, reviews, and briefs.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import errno  # noqa: F401
import hashlib  # noqa: F401
import json  # noqa: F401
import logging  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import shutil  # noqa: F401
import threading  # noqa: F401
import time  # noqa: F401
import unicodedata  # noqa: F401

from collections.abc import Awaitable, Callable, Iterable  # noqa: F401
from dataclasses import asdict, dataclass, field, replace  # noqa: F401
from datetime import datetime, timezone  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any, TypeVar  # noqa: F401
from nekopower.host.ledger import Commission  # noqa: F401
from nekopower.host.paths import DEFAULT_PROJECT, SpacePaths, safe_segment  # noqa: F401

from .models import (
    logger,
    T,
    ENTRY_TYPES,
    _DEFAULT_TYPE,
    CONFIDENCES,
    VERIFIED,
    OBSERVED,
    INFERRED,
    ENTRY_ACTIVE,
    ENTRY_PENDING,
    ENTRY_EXPIRED,
    _OUTDATED,
    _SUPERSEDED_BY,
    _NOTES_FILE_VERSION,
    PROJECT_ACTIVE,
    PROJECT_ARCHIVED,
    _PROJECT_FILE_VERSION,
    _STATE_FILE_VERSION,
    _SLUG_MAX_CHARS,
    _WRITE_ATTEMPTS,
    _UNFINISHED_PREFIX,
    _REMOVING_PREFIX,
    _GONE_PREFIX,
    _WRITE_RETRY_SECONDS,
    _SLUG_UNSAFE,
    _FRONT_MATTER_KEY,
    _WHITESPACE,
    redact,
    Entry,
    DESCRIPTION_AUTHORS,
    Project,
    Review,
    _Document,
    _Rested,
    _Known,
    _Note,
    _Taken,
    _CREATED,
    _ADDED,
    _THERE,
    _HELD,
    _PLAIN_TARGET,
    _WORD_OPENERS,
    _WORD_CLOSERS,
    _MARKS_OF_UNSPACED,
    _MARKS_OF_BOTH,
    _MARKS_INSIDE_UNSPACED,
    _WORD_BREAKS,
    _PLAIN_PATH,
    _WEB_ADDRESS,
    _QUOTED,
    _HAS_EXTENSION,
    _CLAUSE_MARKS,
    _A_NUMBER,
    _Question,
    _ASKING,
    _OUT,
    _NobodyWaits,
    _ADDITION,
    _STANDING_STEPS_MAX,
    _STANDING_CHARACTERS_MAX,
    _COUNTED_COMMISSIONS,
    _BRIEF_CUT_ROOM,
    _STEP_IN_PLACE,
    _STEP_BACK,
    _STEPS_BACK_MAX,
    _HOME_NAMES,
    SpaceError,
    NoteRemoved,
    _NoPlace,
    _Rewritten,
    _NotAsWritten,
    _LineNotAdded,
    _LeftAside,
    _NotMade,
    Layer,
    _LineToPut,
)  # noqa: F401
from .markdown import (
    _one_line,
    _plain_lines,
    _cut,
    _shown,
    _parse_document,
    _render_document,
    _date,
    _datetime,
    slug,
    normalize_name,
    _spaced_letter,
    name_occurs,
    _entry_type,
    _link,
    _wide,
    _kept_of,
    _normal,
    _text_mark,
    _digest,
    _NO_TEXT,
    _confidence,
    _stronger,
    _evidence_in,
    _noted_in,
    _set_noted,
    _seen_in,
    _not_among,
    _joined,
    _texts_of,
    _dated_texts_of,
    _days_of,
    _notes_of,
    _notes_text,
    _strings,
    _uncounted,
    _outside_in,
    _status_of,
    _set_evidence,
    _removed_from,
    _removed_note,
    _removed_there,
    _listed,
    _points_at,
    _with_line_first,
    _lines_as_text,
    _standing_marks,
    _said_already,
    _index_marks,
    _index_line,
    _said_mark,
    _added_to,
    _as_words,
    _link_title,
    _line_put_right,
    _a_relative_time,
)  # noqa: F401
from .evidence import (
    _lexically_inside,
    _real_place,
    evidence_of,
    paths_named_in,
    _stands,
    _drive_away,
    _place_of,
    _places_out,
    _question,
    _asking,
    _answered,
    _give_up,
    _asked,
    _asked_off_the_loop,
    _not_looked_at,
    _stood,
    _recorded,
    _gone,
    _outside_of,
    _gone_for_good,
    _may_be_a_file,
    _speaks_no_more,
    _keep_known,
)  # noqa: F401
from .fs import (
    _is_project,
    _free_beside,
    _set_aside_name,
    _remove_set_aside,
    _bring_up_to,
    _files_of,
    _copy_across,
    _move,
)  # noqa: F401

# Keep this import at the very bottom: it imports ``manager`` and the mixin
# modules, which bind this facade module as ``_space_facade`` for
# late-binding reads of the test-patchable symbols re-exported above.
from .manager import TaskAgentSpace  # noqa: F401
