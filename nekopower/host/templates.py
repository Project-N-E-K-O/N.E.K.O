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

"""Presets of task agent sessions: the built-in ones and the user's template files.

Every session has a preset (``ledger.Session.preset``), set when it is made
and never changed. A preset is a ``Template``: one sentence that says when
it is used, what a session of it is told of its role, and what it is opened
without. Six come with the program (``PRESETS``):

* ``boss``      - the leader of a project: hands its commissions out and
  does none of the work. It has no tool that writes or edits a file or runs
  a command; the task agents it starts and hands over to have theirs
  (``withhold_own``).
* ``general``   - a session of one commission that belongs to no project;
  nothing is withheld, and it is told nothing of a role.
* ``worker``    - a task agent another one started, as it always was.
* ``scout``     - reads only: no tool that writes, runs a command, calls a
  capability, files a note or starts a task agent, and a gateway token that
  calls nothing.
* ``planner``   - reads only, likewise.
* ``inspector`` - cannot write files or call capabilities, but runs
  commands (it has checks to run), and is opened without the notes the task
  agent space keeps: it judges with nothing but the work before it. It is
  told the user's rules like every session (``TaskAgentSpace.brief``).

A session whose preset withholds a tool, or caps its requests, is opened in
the built-in runtime (``Template.needs_builtin``): dsh has file and command
tools of its own, which the host cannot keep from a session there.

Withholding the tools that write files is not a sandbox. A preset that
keeps the shell (the inspector, or a template with ``writes: none`` alone)
can still change a file through a command, under the user's permission for
commands like any other session; what it is told of its role says not to.
Only a preset that reads only (``read_only``) has nothing left to change
anything with.

``shadow`` is the preset of a copy of a session and no template: a copy is
opened as the session it was copied from, without the tools every copy is
without (``TASK_AGENT_FORK_WITHHELD_TOOLS``).

The user adds templates as Markdown files, YAML front matter and the role
below it, in the task agent space: ``templates/*.md`` for every project and
``projects/<project>/templates/*.md`` for one. A file is named after its
template. Of two of a name the project's is used, then the user's, then the
built-in one. A task agent's tools that write files are refused there and
the user is asked about every command that names the space, so a session
does not change what it may do without the user seeing it; that is no
sandbox either (a command that reaches the space by a relative path or a
variable is not seen), and only a preset without the shell cannot at all.

A template only takes away: it withholds tools, makes a session read only,
gives it no memory. What it takes from a session it takes from the task
agents that session starts as well (``Template.under``), except what it
names under ``withhold_own``: tools a session of it is without itself and
the task agents it starts keep, for one that is to hand work out and not
do it. It names no model, address or key (``model`` says only
whose model a session of it follows by default, and is kept for the pull
request that makes use of it). What a file gets wrong is an error with a
reason, never ignored: a field this does not know, a value that is not one
of those allowed, a text over its cap. A file that is wrong takes the place
of the template it would have replaced all the same: a session of that
preset is not opened with more than its user meant it to have. Other
templates are not affected, and ``TemplateStore.listing`` lists the wrong
one with what is wrong with it. Likewise a folder of templates that is
there and cannot be read: it is not taken for an empty one, and no preset
is used until it can be read again.

Nothing here is read until a session is opened or the templates are listed;
what was read is kept until a file of the directories changes.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from nekopower.host.paths import DEFAULT_PROJECT, SpacePaths

PRESETS: tuple[str, ...] = ("boss", "worker", "scout", "inspector", "planner", "general")
# The preset of a copy of a session; no template.
SHADOW = "shadow"
# The pictures a template may be drawn with: those of the built-in presets.
AVATARS: tuple[str, ...] = PRESETS

SOURCE_BUILTIN = "builtin"
SOURCE_GLOBAL = "global"
SOURCE_PROJECT = "project"

WRITES: tuple[str, ...] = ("inherit", "none")
# In a work tree of its own: for the pull request that makes them.
WRITES_LATER = "tree"
MEMORIES: tuple[str, ...] = ("project", "none")
MODELS: tuple[str, ...] = ("agent", "inherit")

# What a session that reads only is opened without: what writes files, runs
# commands, calls capabilities (which can write as well), files notes for
# later commissions and starts task agents. A task agent it started would be
# its own hands: it works in the same directory, on the same commission, with
# what the user allowed that commission.
#
# It keeps the tools that reach other sessions (``message_session``,
# ``ask_session``) and ``schedule_wake``, where it has them at all (a task
# agent another one started has none of the three). A note to another
# session is word to a colleague, not a way to write: the host hands it over
# as what a session wrote, with no authority of the user's, and the other
# session decides under its own commission and its own permissions what to
# do about it. A session that only looks is the one that has to be able to
# say what it saw (an inspector in a team, a scout that watches something
# and wakes itself to look again). A copy is without them for another
# reason: it answers once and is done with.
READ_ONLY_WITHHELD: tuple[str, ...] = ("write", "edit", "bash", "pwsh", "capability_call", "note_lesson", "subagent")
_WRITES_NONE_WITHHELD: tuple[str, ...] = ("write", "edit")
_MEMORY_NONE_WITHHELD: tuple[str, ...] = ("note_lesson",)
# A session reports through it: one without it could not end a commission.
_NEVER_WITHHELD: frozenset[str] = frozenset({"report"})
# The tools of the gateway, which the runtime's own list of tools does not hold.
_GATEWAY_TOOLS: tuple[str, ...] = ("capability_search", "capability_call")
# Presets no task agent is started with by another: the first is the lead of
# a project, the second a session of its own.
NOT_FOR_CHILDREN: frozenset[str] = frozenset({"boss", "general"})
# What the leader of a project is without, itself only: what writes or edits
# a file and what runs a command. It keeps what reads and searches (it has
# to understand the work to hand it out), what reaches other sessions, and
# what starts task agents and hands a commission over.
LEADER_WITHHELD: tuple[str, ...] = ("write", "edit", "bash", "pwsh")

_FIELDS: frozenset[str] = frozenset(
    {
        "name", "description", "withhold", "withhold_own", "read_only", "writes", "memory", "model", "max_requests",
        "avatar", "label",
    }
)
_NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
_SUFFIX = ".md"
_FENCE = "---"


class TemplateError(ValueError):
    """A template cannot be used; the message says why."""


@dataclass(frozen=True, slots=True)
class Template:
    """One preset: when it is used, what its sessions are told, and what they are opened without."""

    name: str
    # When it is used: what a task agent that starts another chooses by.
    description: str
    # What a session of it is told of its role; may be empty.
    body: str = ""
    # Tools its sessions are opened without, besides those the next three imply.
    withhold: tuple[str, ...] = ()
    # Tools its sessions are opened without that the task agents they start
    # keep (``under``): for a preset that hands work out and does none.
    withhold_own: tuple[str, ...] = ()
    read_only: bool = False
    # ``inherit``: writes where its session works. ``none``: has no tools
    # that write files (a command still can, where the shell is not withheld).
    writes: str = "inherit"
    # ``project``: is told what the task agent space keeps and may add to
    # it. ``none``: neither; it is told the user's rules and nothing else.
    memory: str = "project"
    # Whose model its sessions use unless the user says otherwise: the task
    # agents' (``agent``) or that of the session that started it
    # (``inherit``). Kept and shown; nothing goes by it yet.
    model: str = "inherit"
    # Most model requests one run of its sessions may make; None sets none.
    max_requests: int | None = None
    # Which built-in preset's picture it is drawn with.
    avatar: str = "general"
    # What a template of the user's is shown as; empty for a built-in one,
    # which the interface names in the user's language.
    label: str = ""
    source: str = SOURCE_BUILTIN

    def handed_down(self) -> frozenset[str]:
        """Return the tools a session of this preset is opened without that its task agents are without as well."""
        tools = set(self.withhold)
        if self.read_only:
            tools.update(READ_ONLY_WITHHELD)
        if self.writes == "none":
            tools.update(_WRITES_NONE_WITHHELD)
        if self.memory == "none":
            tools.update(_MEMORY_NONE_WITHHELD)
        return frozenset(tools)

    def withheld(self) -> frozenset[str]:
        """Return every tool a session of this preset is opened without."""
        return self.handed_down() | frozenset(self.withhold_own)

    @property
    def writes_files(self) -> bool:
        """Whether its sessions have a tool that writes or edits a file.

        By ``write`` and ``edit`` alone, on purpose: a preset that keeps
        the shell and has neither (the built-in inspector) is there to
        look at work and run its checks, and does not count as one that
        writes, though a command can leave files behind. So such a
        session does not wait its turn at writing in a project, the user
        is not asked before a leader opens one, and a leader may start
        one itself. A template of the user's that is to change files
        through the shell alone is not held to those rules either: one
        that changes files keeps ``write`` or ``edit``.
        """
        return not {"write", "edit"} <= self.withheld()

    def under(self, starter: Template) -> Template:
        """Return this preset as a task agent has it that a session of ``starter`` started.

        Without the tools ``starter`` is without as well, and without
        memory where that one has none, so that a session cannot get back
        through a task agent what its template took from it: neither a
        tool, nor what was noted before (a task agent that read the notes
        would bring them into what it reports). Not what ``starter`` is
        without itself only (``withhold_own``): a preset that hands work
        out says so to keep its own hands off, and whoever it starts does
        the work. The role and the cap on requests are the task agent's
        own preset's. What ``starter`` hands down is handed down by the
        task agent in turn, also where its own preset withholds the same
        tool from itself only: the task agents it starts are without it.
        """
        more = starter.handed_down() - self.handed_down()
        memory = "none" if starter.memory == "none" else self.memory
        if not more and memory == self.memory:
            return self
        return replace(
            self,
            withhold=tuple(sorted({*self.withhold, *more})),
            withhold_own=tuple(tool for tool in self.withhold_own if tool not in more),
            memory=memory,
        )

    @property
    def calls_nothing(self) -> bool:
        """Whether its sessions' gateway token finds capabilities and calls none.

        Whenever it is without the tool that calls them: in dsh the token
        is what keeps a session from calling, not the list of its tools.
        """
        return "capability_call" in self.withheld()

    @property
    def needs_builtin(self) -> bool:
        """Whether its sessions are opened in the built-in runtime, whatever else would be chosen.

        dsh has tools of its own that write files and run commands, and
        makes its model requests by itself: the host can neither keep a
        tool from a session there nor count its requests. A preset that
        withholds a tool or caps the requests is held to only where the
        host opens the session itself.
        """
        return bool(self.withheld()) or self.max_requests is not None

    @property
    def delegates(self) -> bool:
        """Whether its sessions may start task agents, as far as the preset goes."""
        return "subagent" not in self.withheld()

    @property
    def remembers(self) -> bool:
        """Whether its sessions are told what the task agent space keeps of earlier work (the user's rules all are told)."""
        return self.memory != "none"


@dataclass(frozen=True, slots=True)
class Listed:
    """A template as ``TemplateStore.listing`` has it: in effect, or wrong and with what is wrong."""

    name: str
    source: str
    template: Template | None = None
    # What is wrong with it; or, of one that is in effect and not offered,
    # why a task agent is not told of it.
    error: str = ""
    # Whether a task agent that starts another is told of it. One that is
    # in effect and not offered can still be named, and a session of it is
    # opened as before: only the list has no room for its sentence.
    offered: bool = True
    # Its file could not be read just now (held by another program, say),
    # which nothing about the file shows once it can be read again: a
    # listing with such a one is not kept, and the file is read anew.
    unread: bool = False

    @property
    def valid(self) -> bool:
        return self.template is not None

    def to_json(self) -> dict[str, Any]:
        found = self.template
        return {
            "name": self.name,
            "label": found.label if found else "",
            "description": found.description if found else "",
            "avatar": found.avatar if found else "general",
            "source": self.source,
            "read_only": found.read_only if found else None,
            "writes": found.writes if found else None,
            "memory": found.memory if found else None,
            "model": found.model if found else None,
            "valid": found is not None,
            "error": self.error,
        }


def model_name(preset: str) -> str:
    """Return what a model reads a preset called."""
    from config.prompts.prompts_nekopower_presets import PRESET_MODEL_NAMES

    return PRESET_MODEL_NAMES.get(preset, preset)


def reserved_names() -> frozenset[str]:
    """Return the names no template of the user's can have.

    That of a copy's preset, which is no template, and what a model reads
    a built-in preset called where that is not its key: a template called
    ``lead`` would be taken for the lead of a project wherever a model
    names a preset.
    """
    from config.prompts.prompts_nekopower_presets import PRESET_MODEL_NAMES

    return frozenset({SHADOW, *PRESET_MODEL_NAMES.values()})


def _builtin(name: str, **values: Any) -> Template:
    from config.prompts.prompts_nekopower_presets import PRESET_BODIES, PRESET_DESCRIPTIONS

    return Template(name=name, description=PRESET_DESCRIPTIONS[name], body=PRESET_BODIES[name], avatar=name, **values)


def builtin_templates() -> dict[str, Template]:
    """Return the presets that come with the program, by name, in the order they are listed in."""
    made = {
        "boss": _builtin("boss", model="agent", withhold_own=LEADER_WITHHELD),
        "worker": _builtin("worker"),
        "scout": _builtin("scout", read_only=True, writes="none"),
        "inspector": _builtin(
            "inspector",
            withhold=("capability_call", "note_lesson", "subagent"),
            writes="none",
            memory="none",
        ),
        "planner": _builtin("planner", read_only=True, writes="none"),
        "general": _builtin("general", model="agent"),
    }
    return {name: made[name] for name in PRESETS}


def known_tools() -> frozenset[str]:
    """Return the names of the tools a template may withhold: every tool a session can be offered."""
    from nekopower.runtime.tools import tool_schemas

    return frozenset(tool_schemas()) | frozenset(_GATEWAY_TOOLS)


def _one_line(value: Any) -> str:
    return " ".join(str(value).split())


def _split(text: str) -> tuple[str, str]:
    """Return the front matter and the text below it; raises when the file does not begin with front matter."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != _FENCE:
        raise TemplateError("the file does not begin with front matter (a line of ---)")
    for at in range(1, len(lines)):
        if lines[at].strip() == _FENCE:
            return "\n".join(lines[1:at]), "\n".join(lines[at + 1 :]).strip()
    raise TemplateError("the front matter is not closed (no second line of ---)")


def _choice(data: dict[str, Any], key: str, allowed: tuple[str, ...], default: str) -> str:
    if key not in data:
        return default
    value = data[key]
    if not isinstance(value, str) or value not in allowed:
        raise TemplateError(f"{key} is one of {', '.join(allowed)}, not {value!r}")
    return value


def _fields_once() -> Any:
    """Return a YAML loader as safe as ``yaml.safe_load``'s that refuses a field given twice.

    YAML takes the last of two fields of a name without a word: a file
    that withholds a tool in one place and nothing in another would
    withhold nothing, and nobody would be told.
    """
    import yaml

    class FieldsOnce(yaml.SafeLoader):
        def construct_mapping(self, node: Any, deep: bool = False) -> Any:
            seen: list[Any] = []
            for key_node, _value in node.value:
                key = self.construct_object(key_node, deep=True)
                if key in seen:
                    raise TemplateError(f"the field {key} is given twice")
                seen.append(key)
            return super().construct_mapping(node, deep)

    return FieldsOnce


def parse_template(text: str, *, name: str, source: str) -> Template:
    """Return the template a file holds; raises ``TemplateError`` with what is wrong with it.

    ``name`` is the name of the file without its ending, which the
    template is called. Nothing is corrected or left out: a field that is
    not known, a value that is not allowed and a text over its cap are
    each an error.
    """
    import yaml

    from config import nekopower_settings as settings
    from utils.tokenize import count_tokens

    front, body = _split(text)
    try:
        data = yaml.load(front, Loader=_fields_once())  # noqa: S506 - a safe loader, with one more check
    except TemplateError:
        raise
    except Exception as exc:  # noqa: BLE001 - whatever the parser raises (a date that is none, say), the file is wrong
        raise TemplateError(f"the front matter is not valid YAML ({type(exc).__name__})") from exc
    if not isinstance(data, dict):
        raise TemplateError("the front matter is not a set of fields")
    unknown = sorted(str(key) for key in data if key not in _FIELDS)
    if unknown:
        # A template only takes away: it has no field for a model's name,
        # an address or a key, nor for anything else this does not know.
        raise TemplateError(f"unknown fields: {', '.join(unknown)}")
    if not _NAME.fullmatch(name) or name in reserved_names():
        raise TemplateError(
            "a template is named in lower-case letters, digits and hyphens, beginning with a letter, "
            f"32 characters at most, and not {', '.join(sorted(reserved_names()))}"
        )
    if data.get("name") != name:
        raise TemplateError(f"name is {data.get('name')!r}; it has to be the name of the file, {name!r}")
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise TemplateError("description is missing: one sentence that says when the template is used")
    description = _one_line(description)
    if count_tokens(description) > settings.TEMPLATE_DESCRIPTION_MAX_TOKENS:
        raise TemplateError(f"description is longer than {settings.TEMPLATE_DESCRIPTION_MAX_TOKENS} tokens")
    if count_tokens(body) > settings.TEMPLATE_BODY_MAX_TOKENS:
        raise TemplateError(f"the text below the front matter is longer than {settings.TEMPLATE_BODY_MAX_TOKENS} tokens")

    withhold = data.get("withhold", [])
    if isinstance(withhold, str):
        withhold = [item for item in (part.strip() for part in withhold.split(",")) if item]
    if not isinstance(withhold, list) or not all(isinstance(item, str) for item in withhold):
        raise TemplateError("withhold is a list of tool names")
    not_tools = sorted(set(withhold) - known_tools())
    if not_tools:
        raise TemplateError(f"withhold names no tool: {', '.join(not_tools)}")
    kept = sorted(set(withhold) & _NEVER_WITHHELD)
    if kept:
        raise TemplateError(f"{', '.join(kept)} cannot be withheld: a session ends its commissions with it")

    withhold_own = data.get("withhold_own", [])
    if isinstance(withhold_own, str):
        withhold_own = [item for item in (part.strip() for part in withhold_own.split(",")) if item]
    if not isinstance(withhold_own, list) or not all(isinstance(item, str) for item in withhold_own):
        raise TemplateError("withhold_own is a list of tool names")
    not_tools = sorted(set(withhold_own) - known_tools())
    if not_tools:
        raise TemplateError(f"withhold_own names no tool: {', '.join(not_tools)}")
    kept = sorted(set(withhold_own) & _NEVER_WITHHELD)
    if kept:
        raise TemplateError(f"{', '.join(kept)} cannot be withheld: a session ends its commissions with it")

    read_only = data.get("read_only", False)
    if not isinstance(read_only, bool):
        raise TemplateError(f"read_only is true or false, not {read_only!r}")
    if data.get("writes") == WRITES_LATER:
        raise TemplateError("writes: tree (a work tree of its own) is not available yet")
    writes = _choice(data, "writes", WRITES, "none" if read_only else "inherit")
    if read_only and writes != "none":
        raise TemplateError("read_only is true and writes is not none: a session that reads only writes nowhere")
    memory = _choice(data, "memory", MEMORIES, "project")
    model = _choice(data, "model", MODELS, "inherit")
    avatar = _choice(data, "avatar", AVATARS, "general")

    max_requests = data.get("max_requests")
    if max_requests is not None and (
        isinstance(max_requests, bool)
        or not isinstance(max_requests, int)
        or not 1 <= max_requests <= settings.TEMPLATE_MAX_REQUESTS_CAP
    ):
        raise TemplateError(
            f"max_requests is a whole number from 1 to {settings.TEMPLATE_MAX_REQUESTS_CAP}, not {max_requests!r}"
        )

    label = data.get("label", "")
    if not isinstance(label, str):
        raise TemplateError(f"label is a text, not {label!r}")
    label = _one_line(label)
    if count_tokens(label) > settings.TEMPLATE_LABEL_MAX_TOKENS:
        raise TemplateError(f"label is longer than {settings.TEMPLATE_LABEL_MAX_TOKENS} tokens")

    return Template(
        name=name,
        description=description,
        body=body,
        withhold=tuple(sorted(set(withhold))),
        # What is withheld from its task agents as well is said once, there.
        withhold_own=tuple(sorted(set(withhold_own) - set(withhold))),
        read_only=read_only,
        writes=writes,
        memory=memory,
        model=model,
        max_requests=max_requests,
        avatar=avatar,
        label=label,
        source=source,
    )


# What stands for a directory as it was read: every template file's name,
# date and size. Nothing for a directory that is not there; None for one
# that is there and cannot be read, which is never taken for one read before.
_Signature = tuple[tuple[str, int, int], ...] | None


def _signature(directory: Path) -> _Signature:
    try:
        entries = [entry for entry in directory.iterdir() if entry.name.endswith(_SUFFIX)]
    except FileNotFoundError:
        return ()
    except OSError:
        return None
    found: list[tuple[str, int, int]] = []
    for entry in entries:
        try:
            stat = entry.stat()
        except OSError:
            # There, and not to be looked at (a link that leads nowhere,
            # say): it counts all the same. Left out, a file that takes the
            # place of a preset would go unseen, and what was listed
            # before it was there would be handed out again.
            found.append((entry.name, -1, -1))
            continue
        found.append((entry.name, stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(found))


def _all_seen(signature: tuple[_Signature, ...]) -> bool:
    """Return whether every file of the directories a signature stands for could be looked at."""
    return all(part is not None and all(size >= 0 for _name, _date, size in part) for part in signature)


def _read_directory(directory: Path, source: str) -> dict[str, Listed]:
    """Return the templates of one directory, by name: each in effect or wrong.

    A directory that is not there holds none. One that is there and
    cannot be read raises ``TemplateError``: which presets its files
    narrow cannot be told, and taking it for empty would hand back the
    tools a file in it withholds.
    """
    from config.nekopower_settings import TEMPLATE_FILE_MAX_BYTES, TEMPLATE_FILES_MAX

    try:
        # Whatever is named as a template is taken for one: what cannot be
        # read as a file is a template that is wrong, not one that is absent.
        files = sorted((entry for entry in directory.iterdir() if entry.name.endswith(_SUFFIX)), key=lambda entry: entry.name)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise TemplateError(f"the folder of templates {directory.name} cannot be read ({type(exc).__name__})") from exc
    found: dict[str, Listed] = {}
    for count, path in enumerate(files):
        name = path.name[: -len(_SUFFIX)]
        if count >= TEMPLATE_FILES_MAX:
            found[name] = Listed(name, source, error=f"more than {TEMPLATE_FILES_MAX} template files in one folder")
            continue
        try:
            if path.stat().st_size > TEMPLATE_FILE_MAX_BYTES:
                raise TemplateError(f"the file is larger than {TEMPLATE_FILE_MAX_BYTES} bytes")
            template = parse_template(path.read_text(encoding="utf-8-sig"), name=name, source=source)
        except TemplateError as exc:
            found[name] = Listed(name, source, error=str(exc))
        except UnicodeDecodeError as exc:
            found[name] = Listed(name, source, error=f"the file cannot be read ({type(exc).__name__})")
        except OSError as exc:
            found[name] = Listed(name, source, error=f"the file cannot be read ({type(exc).__name__})", unread=True)
        else:
            found[name] = Listed(name, source, template)
    return found


class TemplateStore:
    """The presets in effect: the built-in ones under the user's template files.

    ``paths`` returns the paths of the task agent space. Every method reads
    directories and is blocking: callers on the event loop use a thread.
    """

    def __init__(self, paths: Callable[[], SpacePaths]) -> None:
        self._paths = paths
        # What was listed for a project (None: for no project), with the
        # directories as they were read then.
        self._listed: dict[str | None, tuple[tuple[_Signature, _Signature], list[Listed]]] = {}

    def _directories(self, project: str | None) -> tuple[Path, Path | None]:
        paths = self._paths()
        if project is None or project == DEFAULT_PROJECT:
            return paths.templates_dir(None), None
        try:
            return paths.templates_dir(None), paths.templates_dir(project)
        except ValueError:
            # A name that is no directory name has no directory.
            return paths.templates_dir(None), None

    def signature(self, project: str | None = None) -> tuple[_Signature, _Signature]:
        """Return what changes whenever a template file a project goes by is added, removed or written."""
        return _signature(self._directories(None)[0]), self.project_signature(project)

    def project_signature(self, project: str | None) -> _Signature:
        """Return what changes whenever a template file of a project's own is added, removed or written."""
        own = self._directories(project)[1]
        return _signature(own) if own is not None else ()

    def listing(self, project: str | None = None) -> list[Listed]:
        """Return every template in effect for a project (None: for sessions of no project), the wrong ones too.

        The built-in ones first, in their order, each replaced by a file
        of its name; then the user's others by name. Of two files of a
        name the project's is listed. The sentences that say when each is
        used may come to ``TEMPLATE_DESCRIPTIONS_TOTAL_MAX_TOKENS`` taken
        together: the template that goes past that is not offered
        (``Listed.offered``) and stays in effect, the built-in ones
        counted first, then the user's, then the project's.
        """
        from config.nekopower_settings import TEMPLATE_DESCRIPTIONS_TOTAL_MAX_TOKENS
        from utils.tokenize import count_tokens

        key = None if project is None or project == DEFAULT_PROJECT else project
        signature = self.signature(key)
        kept = self._listed.get(key)
        # (What was listed of a folder that could not be read is not kept,
        # and such a folder is like none that was read before.)
        if kept is not None and kept[0] == signature:
            return list(kept[1])
        everywhere, own = self._directories(key)
        effective: dict[str, Listed] = {
            name: Listed(name, SOURCE_BUILTIN, template) for name, template in builtin_templates().items()
        }
        try:
            effective.update(_read_directory(everywhere, SOURCE_GLOBAL))
            if own is not None:
                effective.update(_read_directory(own, SOURCE_PROJECT))
        except TemplateError as exc:
            # A folder of templates is there and cannot be read: any preset
            # may be narrowed by a file in it, so none is used as if none
            # were. Not kept: the folder is looked at again the next time.
            return [Listed(item.name, item.source, error=str(exc)) for item in effective.values()]
        order = {SOURCE_BUILTIN: 0, SOURCE_GLOBAL: 1, SOURCE_PROJECT: 2}
        used = 0
        for item in sorted(effective.values(), key=lambda item: (order[item.source], item.name)):
            if item.template is None:
                continue
            size = count_tokens(item.template.description)
            if used + size > TEMPLATE_DESCRIPTIONS_TOTAL_MAX_TOKENS:
                # The cap is on what the list says, not on what may be used:
                # a template past it is in effect all the same (a session of
                # that preset is opened by it, and it can be named), and is
                # left out of the list a task agent chooses from.
                effective[item.name] = Listed(
                    item.name,
                    item.source,
                    item.template,
                    error=(
                        "not offered: the descriptions of all templates together are longer than "
                        f"{TEMPLATE_DESCRIPTIONS_TOTAL_MAX_TOKENS} tokens"
                    ),
                    offered=False,
                )
                continue
            used += size
        rank = {name: at for at, name in enumerate(PRESETS)}
        listed = sorted(effective.values(), key=lambda item: (rank.get(item.name, len(rank)), item.name))
        # Nor is what was listed while a file could not be looked at: what
        # stands for such a file is the same whatever is in it, so it would
        # be taken for unchanged when it has become another.
        if not any(item.unread for item in listed) and _all_seen(signature):
            self._listed[key] = (signature, listed)
        return list(listed)

    def resolve(self, name: str, project: str | None = None) -> Template:
        """Return the template a preset names for a project; raises ``TemplateError`` when there is none to use.

        A file of the name that is wrong is not passed over for the
        template it replaced: see the module docstring.
        """
        for item in self.listing(project):
            if item.name == name:
                if item.template is None:
                    raise TemplateError(item.error)
                return item.template
        raise TemplateError("there is no template of that name")

    def roster(self, project: str | None = None) -> list[Template]:
        """Return the templates a task agent can be started with by another, in the order they are listed in."""
        return [
            item.template
            for item in self.listing(project)
            if item.template is not None and item.offered and item.name not in NOT_FOR_CHILDREN
        ]
