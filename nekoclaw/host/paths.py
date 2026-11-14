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

"""Where the host keeps its files, below ``<data root>/nekoclaw/``.

::

    <data root>/nekoclaw/
    ├─ ledger.db                 the commission ledger
    ├─ permissions.json          permission settings
    ├─ plugin_short_descriptions.json
    │                            short descriptions written for plugins
    │                            whose manifest has none
    └─ task_agents/projects/_daily/sessions/<commission id>/
        ├─ transcript.jsonl      written by the runtime
        ├─ outputs/              large tool output, written by the runtime
        └─ work/                 the task agent's working directory, unless
                                 the commission names another one

The session directory follows the layout planned for the task agent space
(design section 12.1), so nothing has to move once that exists. Every path is
computed when it is asked for; resolving the data root reads configuration, so
callers on the event loop do it in a worker thread.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

DEFAULT_PROJECT = "_daily"

# Separators, drive and stream colons, characters Windows forbids in a name,
# and control characters.
_UNSAFE_CHARACTERS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_MAX_SEGMENT_LENGTH = 128
# Names Windows reads as devices, with or without an extension ("nul.txt").
# Refused on every platform, so a data root moved between systems stays usable.
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "123456789¹²³"}
)


def _reserved(value: str) -> bool:
    return value.split(".", 1)[0].rstrip(" ").upper() in _RESERVED_NAMES


def _segment(value: str, what: str) -> str:
    """Return ``value`` if it is safe as one directory name, else raise ValueError.

    Project names and commission ids end up in paths; one that climbs out of
    the host's directory, or that Windows would read differently (it drops a
    trailing dot or space, and reads names such as ``CON`` or ``nul.txt`` as
    devices), is refused. Leading dots and spaces are refused too.
    """
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_SEGMENT_LENGTH
        or value in (".", "..")
        or value != value.strip(" .")
        or _UNSAFE_CHARACTERS.search(value)
        or _reserved(value)
    ):
        raise ValueError(f"{what} {value!r} cannot be used as a directory name")
    return value


def data_root() -> Path:
    """Return the user's data root, without running any migration."""
    from utils.config_manager import get_config_manager

    return Path(get_config_manager(migrate=False).app_docs_dir)


class HostPaths:
    """Paths below the host's directory."""

    def __init__(self, root: Path | Callable[[], Path] | None = None) -> None:
        self._root = root

    def root(self) -> Path:
        source = self._root
        if source is None:
            return data_root() / "nekoclaw"
        return Path(source() if callable(source) else source)

    def ledger_db(self) -> Path:
        return self.root() / "ledger.db"

    def permissions_json(self) -> Path:
        return self.root() / "permissions.json"

    def short_descriptions_json(self) -> Path:
        return self.root() / "plugin_short_descriptions.json"

    def session_dir(self, commission_id: str, project: str | None = None) -> Path:
        """Return a commission's session directory; raises ValueError for an unsafe name."""
        project_name = _segment(project or DEFAULT_PROJECT, "project")
        session_name = _segment(commission_id, "commission id")
        return self.root() / "task_agents" / "projects" / project_name / "sessions" / session_name

    def work_dir(self, commission_id: str, project: str | None = None) -> Path:
        return self.session_dir(commission_id, project) / "work"
