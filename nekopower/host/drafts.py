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

"""Filling in the form of a new session for the user (``NekoPowerHost.draft_session``).

The user writes what a task agent is to look after and may choose the
folder it works in; one call of the summary model (``TIER_SESSION_DRAFT``)
answers with the fields of the form: a name, the card's title and duty, a
preset, what the project is for, and a draft of rules. The lines of the
card are asked for in the words the review of a commission is told of them,
so a card reads the same whoever wrote it.

Nothing is made and nothing is written: the page shows the answer, the user
changes what they like, and whoever makes the session is called after that.
Of the folder only names and sizes are read; no file is opened.

It gives the whole form or raises ``DraftError`` with a reason
(``DRAFT_REASONS``): the page then lets the user fill the form in.
"""

from __future__ import annotations

import asyncio
import heapq
import logging
import os
import re
from typing import Any

from nekopower.host.decision import TIER_SESSION_DRAFT, parse_json_object
from nekopower.host.templates import TemplateStore, model_name

logger = logging.getLogger(__name__)

# Why no form was filled in: nothing was written to go by; no model is
# configured for it; the preset such a session has by default cannot be used
# (its template is wrong, or the templates cannot be read); the call took too
# long; it failed; what came back was no JSON object; the object has no name
# or no duty.
DRAFT_REASONS: tuple[str, ...] = ("empty", "no_model", "no_preset", "timeout", "failed", "unreadable", "incomplete")

# The preset of a session the user sets up, unless the answer names another
# that is there: it leads a project of its own.
DEFAULT_PRESET = "boss"
# Presets such a session is not offered: how a task agent another one started
# is opened, and a session of one errand that belongs to no project.
NOT_OFFERED: frozenset[str] = frozenset({"worker", "general"})


class DraftError(RuntimeError):
    """The form of a new session could not be filled in; ``reason`` is one of ``DRAFT_REASONS``."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def _size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024  # type: ignore[assignment]
    return ""


def list_folder(workdir: str, most: int) -> list[str]:
    """Return what a folder holds, one line an entry: its name and size, or that it is a folder. Blocking.

    The first ``most`` by name, of the folder itself and nothing below
    it. No file is opened. A folder that cannot be listed gives nothing.
    """
    try:
        with os.scandir(workdir) as found:
            # The first by name, without holding all a large folder has.
            entries = heapq.nsmallest(most, found, key=lambda entry: entry.name.casefold())
            lines: list[str] = []
            for entry in entries:
                try:
                    if entry.is_dir():
                        lines.append(f"{entry.name}/")
                    else:
                        lines.append(f"{entry.name} ({_size(entry.stat().st_size)})")
                except OSError:
                    lines.append(entry.name)
            return lines
    except OSError:
        return []


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def _project_name(name: str) -> str:
    """Return a name as a project can be called: one a folder can have; empty when there is none in it.

    A project's name is the name of its folder. What a folder cannot be
    called is taken out of what the model wrote, so the form is not
    handed back with a name the session could not be made under.
    """
    from nekopower.host.paths import safe_segment

    cleaned = " ".join(re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", name).split()).strip(" ._")
    try:
        return safe_segment(cleaned, "project") if cleaned else ""
    except ValueError:
        return ""


async def draft_session(
    llm: Any, templates: TemplateStore, text: str, workdir: str | None = None
) -> dict[str, str]:
    """Return ``{name, title, preset, duty, description, rules}`` for a new session, from what the user wrote of it.

    ``llm`` is a ``DecisionLLM`` that knows ``TIER_SESSION_DRAFT``.
    ``preset`` is the name of a template in effect for every project,
    ``DEFAULT_PRESET`` when the answer names none of them or one such a
    session is not offered (``NOT_OFFERED``). Raises ``DraftError``;
    never returns part of a form.
    """
    from config import nekopower_settings as settings
    from config.prompts import prompts_nekopower_presets as texts
    from utils.tokenize import count_tokens, truncate_to_tokens

    wanted = str(text or "").strip()
    if not wanted:
        raise DraftError("empty")
    if llm is None:
        raise DraftError("no_model")
    listed = [item for item in await asyncio.to_thread(templates.listing, None) if item.name not in NOT_OFFERED]
    default = next(item for item in listed if item.name == DEFAULT_PRESET)
    if default.template is None:
        # The form would name a preset no session can be opened by: said
        # now, before a model is asked, and not when the session is made.
        raise DraftError("no_preset", default.error)
    # The default is named in the instructions; the others one line each, as
    # many as fit, a template that is wrong left out. Only one the model was
    # told of can come back; anything else is the default.
    names: dict[str, str] = {}
    lines: list[str] = []
    room = settings.SESSION_DRAFT_PRESETS_MAX_TOKENS
    for item in listed:
        if item.template is None or not item.offered or item.name == DEFAULT_PRESET:
            continue
        line = texts.PRESET_ROSTER_LINE.format(name=model_name(item.name), description=item.template.description)
        room -= count_tokens(line) + 1
        if room < 0:
            break
        lines.append(line)
        names[model_name(item.name)] = item.name
    system = texts.SESSION_DRAFT_SYSTEM_PROMPT.format(presets="\n".join(lines), default=model_name(DEFAULT_PRESET))
    held = await asyncio.to_thread(list_folder, workdir, settings.SESSION_DRAFT_LISTING_ENTRIES) if workdir else []
    user = texts.SESSION_DRAFT_USER_TEMPLATE.format(
        text=truncate_to_tokens(wanted, settings.SESSION_DRAFT_TEXT_MAX_TOKENS),
        workdir=truncate_to_tokens(str(workdir), 60) if workdir else texts.SESSION_DRAFT_NO_FOLDER,
        listing=(
            truncate_to_tokens("\n".join(held), settings.SESSION_DRAFT_LISTING_MAX_TOKENS)
            or texts.SESSION_DRAFT_NOTHING
        )
        if workdir
        else "",
    )
    room = settings.SESSION_DRAFT_INPUT_MAX_TOKENS - count_tokens(system)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": truncate_to_tokens(user, max(room, 0))},
    ]
    try:
        raw = await asyncio.wait_for(llm.complete(TIER_SESSION_DRAFT, messages), settings.SESSION_DRAFT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise DraftError("timeout") from exc
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - whatever the model call raises, there is no form
        logger.warning("filling in the form of a new session failed (%s)", type(exc).__name__)
        raise DraftError("failed", type(exc).__name__) from exc
    data = parse_json_object(raw)
    if data is None:
        raise DraftError("unreadable")
    name = _project_name(truncate_to_tokens(_text(data.get("name")), settings.SPACE_PROJECT_NAME_MAX_TOKENS))
    duty = truncate_to_tokens(_text(data.get("duty")), settings.CARD_DUTY_MAX_TOKENS)
    if not name or not duty:
        raise DraftError("incomplete")
    rules = data.get("rules")
    return {
        "name": name,
        "title": truncate_to_tokens(_text(data.get("title")), settings.CARD_TITLE_MAX_TOKENS),
        # Only one that is there: whatever else the model wrote is not a preset.
        "preset": names.get(_text(data.get("preset")), DEFAULT_PRESET),
        "duty": duty,
        "description": truncate_to_tokens(
            _text(data.get("description")), settings.SPACE_PROJECT_DESCRIPTION_MAX_TOKENS
        ),
        "rules": truncate_to_tokens(rules.strip(), settings.SESSION_DRAFT_RULES_MAX_TOKENS)
        if isinstance(rules, str)
        else "",
    }
