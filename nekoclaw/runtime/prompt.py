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

"""Assemble the system prompt of a task agent session.

The prompt is built once per session and never changes afterwards, so a
provider can cache it. Anything that changes while the session runs (the time,
running jobs, the todo list) is appended to the conversation instead.

Layout: our identity paragraphs, the environment, one paragraph per offered
tool in dsh's order, our paragraphs for ``report`` and ``note_lesson``, the
reply-language sentence, and finally the host's own instructions.
"""

from __future__ import annotations

from collections.abc import Collection

from config.prompts import prompts_nekoclaw as texts

_PLATFORM_NAMES = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}


def _tool_paragraph(name: str, text: str, offered: Collection[str]) -> str:
    """Apply dsh's wording variants for tools that are not offered together."""
    if name == "write" and "edit" not in offered:
        return text.replace(" and prefer edit for targeted changes", "")
    if name == "grep" and "read" not in offered:
        return text.replace(
            " Use read on a matched file when you need surrounding context.", ""
        )
    if name == "web_search" and "web_fetch" not in offered:
        return text.replace(
            "Follow up with web_fetch when you need the full content of a specific result, and cite",
            "Use the returned source snippets when available, and cite",
        )
    return text


def build_system_prompt(
    *,
    tool_names: Collection[str],
    workdir: str,
    platform: str,
    reply_language: str | None = None,
    instructions: str | None = None,
) -> str:
    """Return the system prompt for a session offering ``tool_names``."""
    offered = set(tool_names)
    shell = "pwsh" if "pwsh" in offered else "bash" if "bash" in offered else "(none)"
    environment = (
        texts.ENVIRONMENT_SECTION.replace("<<WORKDIR>>", workdir)
        .replace("<<PLATFORM>>", _PLATFORM_NAMES.get(platform, platform))
        .replace("<<SHELL>>", shell)
    )
    parts = [texts.TASK_AGENT_IDENTITY, environment]
    for name, triggers, text in texts.TOOL_GUIDANCE_SECTIONS:
        if any(trigger in offered for trigger in triggers):
            parts.append(_tool_paragraph(name, text, offered))
    if "report" in offered:
        parts.append(texts.REPORT_GUIDANCE)
    if "note_lesson" in offered:
        parts.append(texts.NOTE_LESSON_GUIDANCE)
    if reply_language and reply_language.strip():
        parts.append(
            texts.REPLY_LANGUAGE_SENTENCE.replace("<<LANGUAGE>>", reply_language.strip())
        )
    if instructions and instructions.strip():
        parts.append(instructions.strip())
    return "\n\n".join(parts)
