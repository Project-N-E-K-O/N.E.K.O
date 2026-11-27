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


"""Bounded pack reads and verified immutable history copies.

Callers retain path selection, mutation locks and data-retention ownership.
These helpers never clean a directory or choose a user's active revision.
"""

from pathlib import Path

from utils.file_utils import atomic_write_bytes
from .models import MAX_PACK_BYTES, pack_sha256


def file_identity(path: Path) -> tuple[int, ...] | None:
    """Cache key for a file's state, or None when it cannot be read.

    Inode and change time are included, so restoring a same-size file with a
    preserved mtime (``cp -p``, ``rsync -a``) still counts as a change.
    """
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def retain_revision(history: Path, raw: bytes, expected_sha256: str) -> None:
    """Keep a pack revision in history without ever replacing a good copy.

    History file names carry the revision digest, so an existing copy is
    only overwritten by bytes that match the registry. A damaged active file
    is still kept when no copy exists yet, and never blocks the caller.
    """
    if pack_sha256(raw) != expected_sha256 and history.exists():
        return
    history.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(history, raw)


def read_pack_bytes(path: Path) -> bytes:
    """Read a raw pack, never more than the pack limit (+1 to detect overflow).

    A damaged or replaced file of any size must not be loaded whole into the
    shared Memory Server; an oversized one simply fails the hash check.
    """
    with path.open("rb") as handle:
        return handle.read(MAX_PACK_BYTES + 1)
