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

"""Filesystem safety helpers and cross-volume atomic directory moves for project folders."""

from __future__ import annotations

import errno
import os
import shutil
import time

from pathlib import Path

from .models import (
    logger,
    _WRITE_ATTEMPTS,
    _UNFINISHED_PREFIX,
    _REMOVING_PREFIX,
    _GONE_PREFIX,
    _LeftAside,
)

# Late-binding read point for symbols that tests rebind on
# ``nekopower.host.space`` via ``monkeypatch.setattr`` / ``patch``. Do NOT
# from-import those names here: a from-import snapshots the value at
# import time and the facade patch would silently miss this module.
from nekopower.host import space as _space_facade


def _is_project(directory: Path) -> bool:
    """Whether a directory among the projects is one.

    Not one whose name begins with a dot: a copy still being made, or what
    is left of a directory that was copied elsewhere (``_copy_across``).
    """
    return directory.is_dir() and not directory.name.startswith(".")


def _free_beside(source: Path, prefix: str) -> Path:
    """Return a name next to ``source`` that begins with ``prefix`` and that nothing has."""
    free = source.with_name(f"{prefix}{source.name}")
    number = 2
    while free.exists():
        free = source.with_name(f"{prefix}{source.name}-{number}")
        number += 1
    return free


def _set_aside_name(source: Path) -> Path:
    """Return a name next to ``source`` that nothing has, for it to be out of sight under.

    What an earlier move left under such a name is not touched: a move
    that was cut off before it had looked at what it set aside may have
    left the only copy of what the user saved last there.
    """
    return _free_beside(source, _REMOVING_PREFIX)


def _remove_set_aside(aside: Path, source: Path) -> None:
    """Remove a directory that was set aside and is known to be of no use any more.

    Given the name of what can go first, in one step: removing can stop
    half way, and what is left is then told from what must stay. What
    earlier moves left under such a name is cleared away with it,
    whatever it was called before: all of it can go. Told by how the name
    begins, not by a pattern: a project's name can have ``[`` or ``*`` in it.
    """
    try:
        left_over = [item for item in source.parent.iterdir() if item.name.startswith(_GONE_PREFIX)]
    except OSError:
        left_over = []
    for left in left_over:
        shutil.rmtree(left, ignore_errors=True)
    gone = _free_beside(source, _GONE_PREFIX)
    try:
        os.replace(aside, gone)
    except OSError:
        logger.warning("a directory that can go could not be given the name of one", exc_info=True)
        return
    shutil.rmtree(gone, ignore_errors=True)


def _bring_up_to(copy: Path, original: Path) -> None:
    """Make a copy of a directory hold what the original holds now: files written anew, and none that are gone.

    Raises OSError when the copy does not hold the same files in the end,
    or when the original was written to meanwhile (a program that had a
    file of it open before it was set aside can still write to it); the
    original is then the only place that has it all.
    """
    before = _space_facade._files_of(original)
    # What is a file in the one and a directory in the other is taken out
    # of the copy first: copied over a directory of its name, a file would
    # land inside it.
    for directory, directories, names in os.walk(original, followlinks=True):
        inside = copy / Path(directory).relative_to(original)
        for name in names:
            if (inside / name).is_dir() and not (inside / name).is_symlink():
                shutil.rmtree(inside / name)
        for name in directories:
            if (inside / name).is_file() or (inside / name).is_symlink():
                (inside / name).unlink()
    shutil.copytree(original, copy, dirs_exist_ok=True)
    held = {name: size for name, _written, size in _space_facade._files_of(original)}
    for name, _written, _size in _space_facade._files_of(copy):
        if name not in held:
            (copy / name).unlink()
    if {name: size for name, _written, size in _space_facade._files_of(copy)} != held:
        raise OSError(f"the copy of {original.name} does not hold what was saved last")
    if _space_facade._files_of(original) != before:
        raise OSError(f"{original.name} was written to while its copy was brought up to it")


def _files_of(root: Path) -> list[tuple[str, int, int]]:
    """Return what a directory holds: every file's path in it, when it was last written and its size.

    Raises OSError when a directory in it cannot be listed or a file
    cannot be looked at: left out, two such lists could be the same though
    the directories are not.
    """

    def fail(error: OSError) -> None:
        raise error

    found: list[tuple[str, int, int]] = []
    # Links to directories are gone into: copying goes into them too, and
    # what is behind one would otherwise be in the copy and not in this list.
    for directory, _directories, names in os.walk(root, onerror=fail, followlinks=True):
        for name in names:
            path = Path(directory) / name
            stat = path.stat()
            found.append((path.relative_to(root).as_posix(), stat.st_mtime_ns, stat.st_size))
    return sorted(found)


def _copy_across(source: Path, target: Path) -> None:
    """Move a directory to another volume, so that it is whole under its name somewhere at every moment.

    1. Copied whole under another name on the target's volume. A copy that
       stops half way (the volume is full) is removed again.
    2. The copy is given the target's name. From here on the directory is
       whole in both places.
    3. The source is renamed out of sight where it is. That is one step on
       its own volume: it happens whole or not at all. If it cannot be done
       (a file in it is held open), the copy is taken out of sight again
       and removed, and the source stays.
    4. What was set aside is removed. Before that it is looked at once
       more: when a file in it was saved while it was copied, it is put
       back, the copy is taken back, and OSError is raised. Only once it
       is known to hold nothing the target lacks is it given a name that
       says it can go, and removed; removing can stop half way, and what
       is left then has a name that is no project's. Stopped before that,
       it keeps the name it was set aside under, which nothing removes.

    So neither place ever has part of the directory under its name:
    removing the source under its own name could leave half of an active
    project behind, which would be taken for the project itself. And there
    is no moment at which the directory is under its name in neither
    place. A process stopped anywhere on the way leaves it whole at the
    source, at the target, or at both. What has a name that begins with a
    dot is no project: a copy that stopped half way and what was known to
    be of no use can go, while what was set aside and not looked at yet
    may hold what the target lacks, and is never removed. (Setting the
    source aside before the copy has its name would, stopped there, leave
    both out of sight.)
    """
    unfinished = target.with_name(f"{_UNFINISHED_PREFIX}{target.name}")
    shutil.rmtree(unfinished, ignore_errors=True)
    before = _space_facade._files_of(source)
    try:
        shutil.copytree(source, unfinished)
        os.replace(unfinished, target)
    except BaseException:
        shutil.rmtree(unfinished, ignore_errors=True)
        raise

    def take_back() -> None:
        try:
            os.replace(target, unfinished)
        except OSError:
            logger.warning("the copy of a project that stays where it was could not be taken back", exc_info=True)
        else:
            shutil.rmtree(unfinished, ignore_errors=True)

    aside = _set_aside_name(source)
    try:
        os.replace(source, aside)
    except BaseException:
        take_back()
        raise
    # Out of sight, nothing is saved into it any more. A file the user saved
    # from an editor while it was copied is not in the copy: the directory
    # goes back to where it was, and the copy is taken back.
    try:
        after: list[tuple[str, int, int]] | None = _space_facade._files_of(aside)
    except OSError:
        # Not known to be what was copied: taken for changed.
        after = None
    if after != before:
        try:
            os.replace(aside, source)
        except OSError:
            # It cannot be put back (something has its name by now). What
            # the user saved last is only in what was set aside, and nothing
            # is saved into that any more: the copy is brought up to it, and
            # the move is done after all. If that cannot be done either,
            # what was set aside stays as it is: nothing removes it.
            logger.warning("a project that changed while it was copied could not be put back", exc_info=True)
            try:
                _bring_up_to(target, aside)
            except OSError as exc:
                raise _LeftAside(
                    f"{source.name} changed while it was copied and could not be put back; it is at {aside.name}"
                ) from exc
        else:
            take_back()
            raise OSError(f"{source.name} was changed while it was copied to another volume; it stays where it was")
    # Only now is it known that the target has all of it.
    _remove_set_aside(aside, source)


def _move(source: Path, target: Path) -> None:
    """Move a directory by renaming it, trying again for a moment while Windows says it is in use.

    A directory that cannot be renamed stays where it is, whole, and the
    error is raised. It is not copied and then removed: with a file in it
    held open, the removing would stop half way, and the project would be
    in two places with neither of them complete. Only when the target is on
    another volume (a link somewhere in between), where renaming cannot
    work at all, is it copied.
    """
    last: OSError | None = None
    for attempt in range(_WRITE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            last = exc
            if exc.errno == errno.EXDEV:
                _copy_across(source, target)
                return
            if not source.exists():
                raise
            time.sleep(_space_facade._WRITE_RETRY_SECONDS * (attempt + 1))
    raise last if last is not None else OSError("the directory could not be moved")
