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

"""Which browser the browser tools use, and how.

Kept in ``<data root>/nekoclaw/browser.json``::

    {"version": 1, "mode": "own_profile", "executable": null, "headless": false}

* ``mode``: ``"own_profile"`` (the default) starts the user's browser with a
  profile directory of its own, ``<data root>/nekoclaw/browser_profile``;
  ``"attach"`` drives the browser the user is using, which they have to allow
  in that browser (see ``launcher.py``).
* ``executable``: a browser to use instead of the one found; null to search.
* ``headless``: start the browser without a window (``own_profile`` only).
  Where no window can be shown (Linux without a display), it is started
  without one in any case.

The file is read each time a browser is needed, so a change takes effect with
the next browser that is started, without a restart of the application. A
file that is missing or cannot be read means the defaults.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODE_OWN_PROFILE = "own_profile"
MODE_ATTACH = "attach"
MODES: tuple[str, ...] = (MODE_OWN_PROFILE, MODE_ATTACH)
_FILE_VERSION = 1


@dataclass(frozen=True, slots=True)
class BrowserSettings:
    mode: str = MODE_OWN_PROFILE
    executable: str | None = None
    headless: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _root() -> Path:
    from utils.config_manager import get_config_manager

    return Path(get_config_manager(migrate=False).app_docs_dir) / "nekoclaw"


class BrowserSettingsStore:
    """Reads and writes ``browser.json``. Both block: call them from a worker thread."""

    def __init__(self, root: Path | Callable[[], Path] | None = None) -> None:
        self._root = root

    def root(self) -> Path:
        source = self._root
        if source is None:
            return _root()
        return Path(source() if callable(source) else source)

    def path(self) -> Path:
        return self.root() / "browser.json"

    def profile_dir(self) -> Path:
        """Return the profile directory the browser is started with in ``own_profile`` mode."""
        return self.root() / "browser_profile"

    def load(self) -> BrowserSettings:
        try:
            raw = json.loads(self.path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return BrowserSettings()
        except (OSError, ValueError) as exc:
            logger.warning("the browser settings cannot be read (%s); using the defaults", type(exc).__name__)
            return BrowserSettings()
        if not isinstance(raw, dict):
            return BrowserSettings()
        mode = raw.get("mode")
        executable = raw.get("executable")
        return BrowserSettings(
            mode=mode if mode in MODES else MODE_OWN_PROFILE,
            executable=executable.strip() if isinstance(executable, str) and executable.strip() else None,
            headless=raw.get("headless") is True,
        )

    def save(self, settings: BrowserSettings) -> None:
        from utils.file_utils import atomic_write_json

        if settings.mode not in MODES:
            raise ValueError(f"unknown browser mode {settings.mode!r}")
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, {"version": _FILE_VERSION, **settings.to_json()})
