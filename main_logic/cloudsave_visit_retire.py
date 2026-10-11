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

"""Startup cloudsave import -> visit retirement of the local characters it removes (design OD-13, PR-09b).

The import lives in ``utils`` and may not reach the visit layer, so both
startup callers (the launcher's pre-launch import and main_server's
``import_if_needed``) hand it :func:`removed_characters_recorder`. This
module stays import-light: ``main_logic.visit`` (about half a second to
import) is loaded only when an import really removes a character.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePath
from typing import Any


def removed_characters_recorder(
    config_manager: Any,
) -> Callable[[list[dict[str, Any]], frozenset[str]], int] | None:
    """The ``on_characters_removed`` callback recording ``pending_retire`` items under its ``config_dir``.

    ``None`` for a config object without a real ``config_dir`` (``str`` /
    ``Path``; the application's ``ConfigManager`` always has one): it has no
    visit data, and the import then runs without a callback.
    """
    value = getattr(config_manager, "config_dir", None)
    if not isinstance(value, (str, PurePath)) or not str(value):
        return None
    config_dir = Path(value)

    def record(removed: list[dict[str, Any]], kept_names: frozenset[str]) -> int:
        from main_logic.visit.char_lifecycle import record_removed_characters_sync

        return record_removed_characters_sync(config_dir, removed, kept_names)

    return record
