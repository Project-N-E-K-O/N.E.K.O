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

"""Canonical inventory of data that follows the selected runtime root.

The storage-location workflow used to duplicate this list in migration,
preflight, cleanup, legacy discovery, and diagnostics.  Keep every selected-
root entry here so adding a new persistent directory cannot silently make only
part of that workflow aware of it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .policy import path_chain_has_symlink


RUNTIME_ENTRY_KIND_USER_DATA = "user_data"
RUNTIME_ENTRY_KIND_RUNTIME_CACHE = "runtime_cache"


@dataclass(frozen=True, slots=True)
class RuntimeStorageEntry:
    key: str
    relative_path: str
    kind: str = RUNTIME_ENTRY_KIND_USER_DATA
    config_attribute: str = ""


RUNTIME_STORAGE_ENTRIES = (
    RuntimeStorageEntry("config", "config", config_attribute="config_dir"),
    RuntimeStorageEntry("memory", "memory", config_attribute="memory_dir"),
    RuntimeStorageEntry("plugins", "plugins", config_attribute="plugins_dir"),
    RuntimeStorageEntry("live2d", "live2d", config_attribute="live2d_dir"),
    RuntimeStorageEntry("vrm", "vrm", config_attribute="vrm_dir"),
    RuntimeStorageEntry("mmd", "mmd", config_attribute="mmd_dir"),
    RuntimeStorageEntry("pngtuber", "pngtuber", config_attribute="pngtuber_dir"),
    RuntimeStorageEntry("workshop", "workshop", config_attribute="workshop_dir"),
    RuntimeStorageEntry(
        "character_cards",
        "character_cards",
        config_attribute="chara_dir",
    ),
    RuntimeStorageEntry("card_faces", "card_faces", config_attribute="card_faces_dir"),
    RuntimeStorageEntry("jukebox", "jukebox"),
    RuntimeStorageEntry("avatar_tools", "avatar_tools", config_attribute="avatar_tools_dir"),
    # Scores historically live below ``state`` even though ``state`` itself is
    # anchored control data.  Tracking only this child preserves scores without
    # ever moving storage_policy.json or storage_migration.json.
    RuntimeStorageEntry("game_scores", "state/game_scores"),
    RuntimeStorageEntry(
        "embedding_models",
        "embedding_models",
        kind=RUNTIME_ENTRY_KIND_RUNTIME_CACHE,
    ),
    RuntimeStorageEntry("runtimes", "runtimes", kind=RUNTIME_ENTRY_KIND_RUNTIME_CACHE),
    RuntimeStorageEntry(
        "plugin_runtime",
        "plugin-runtime",
        kind=RUNTIME_ENTRY_KIND_RUNTIME_CACHE,
    ),
)

RUNTIME_STORAGE_ENTRY_BY_KEY = {entry.key: entry for entry in RUNTIME_STORAGE_ENTRIES}
RUNTIME_STORAGE_RELATIVE_PATHS = tuple(entry.relative_path for entry in RUNTIME_STORAGE_ENTRIES)
RUNTIME_STORAGE_TOP_LEVEL_DIR_NAMES = tuple(
    entry.relative_path
    for entry in RUNTIME_STORAGE_ENTRIES
    if "/" not in entry.relative_path and "\\" not in entry.relative_path
)
RUNTIME_USER_DATA_ENTRIES = tuple(
    entry for entry in RUNTIME_STORAGE_ENTRIES if entry.kind == RUNTIME_ENTRY_KIND_USER_DATA
)


def runtime_entry_path(root: Path, entry: RuntimeStorageEntry) -> Path:
    """Resolve an inventory entry below ``root`` without resolving symlinks."""

    return root / entry.relative_path


class RuntimeStorageEntryBoundaryError(ValueError):
    """Raised when an inventory entry can escape its lexical storage root."""


def checked_runtime_entry_path(
    root: Path | str,
    entry: RuntimeStorageEntry | str,
) -> Path:
    """Return an entry path only when its complete lexical chain is safe.

    Do not use ``resolve`` here: resolving first would hide the symlink/junction
    that this boundary is meant to detect.  Missing leaves are valid, but every
    existing ancestor from the storage root through the leaf must be an ordinary
    path.  ``path_chain_has_symlink`` also treats Windows reparse points as links.
    """

    root_path = Path(os.path.abspath(os.fspath(Path(root).expanduser())))
    relative_value = entry.relative_path if isinstance(entry, RuntimeStorageEntry) else str(entry)
    relative_path = Path(relative_value)
    if relative_path.is_absolute() or any(part == ".." for part in relative_path.parts):
        raise RuntimeStorageEntryBoundaryError(
            f"运行时存储条目不在存储根目录内: {relative_value}"
        )

    entry_path = root_path / relative_path
    try:
        entry_path.relative_to(root_path)
    except ValueError as exc:
        raise RuntimeStorageEntryBoundaryError(
            f"运行时存储条目不在存储根目录内: {relative_value}"
        ) from exc

    if path_chain_has_symlink(entry_path):
        raise RuntimeStorageEntryBoundaryError(
            f"运行时存储条目路径包含符号链接或重解析点: {relative_value}"
        )
    return entry_path
