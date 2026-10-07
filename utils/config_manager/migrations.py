# -*- coding: utf-8 -*-
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

"""Startup migration mixin.

Config/memory file migration into the runtime root, localized default
characters source selection, default card-face backfill and the soft
migration of legacy Documents memory directories.
"""
import json
import os
import shutil
import stat
import tempfile
import sys
import threading
import time
import uuid
from pathlib import Path

from config import CONFIG_FILES, DEFAULT_CONFIG_DATA
from utils.file_utils import (
    publish_without_replacing,
    replace_with_busy_retry,
)


# Staging lives OUTSIDE the character namespace, beside memory/ rather than
# inside it. Everything that went wrong with it before came from that one
# decision: a dot-prefixed character name is legal, so any in-namespace name
# could collide with a real character, and no ownership marker placed inside
# the namespace can be trusted -- ordinary contents can reproduce it. Beside
# memory/ there is nothing to collide with, and it is still the same
# filesystem, which is what rename needs.
# Short on purpose. Every staged descendant carries this component plus one
# temporary name, and a destination close to the platform path limit can
# fail while staging even though the final path would have fitted.
_MIGRATION_STAGING_DIR = ".mig-staging"

# Two threads can enter the migration: config_manager/__init__.py notes that
# ``_config_manager_migrated`` is not thread-safe. Without this they share a
# workspace parent, and one run's cleanup deletes the other's live copy.
# Serialising is cheaper than making every step concurrency-safe, and this
# runs once per process.
# A workspace nothing has touched for this long is not a live migration,
# it is what a run killed outright left behind. Generous on purpose: the
# cost of being wrong is deleting a concurrent run's live copy, and the
# cost of being slow is one directory surviving one extra start.
_MIGRATION_STAGING_STALE_SECONDS = 24 * 60 * 60

# Preparing the workspace races another run's cleanup: the parent can be
# removed between creating it and minting inside it, because it is empty
# at that instant. Two more goes is plenty for a window that small, and
# bounded so a genuinely missing parent still surfaces.
_MIGRATION_STAGING_ATTEMPTS = 3

# Far below the stale threshold, and far above the cost of one utime.
_MIGRATION_HEARTBEAT_SECONDS = 30

_MIGRATION_LOCK = threading.Lock()


def _release_inherited_workspace_lock() -> None:
    """Drop the workspace lock this process inherited but does not own.

    ``fork`` copies the open file description, so the child goes on
    holding the advisory lock on a workspace it is not migrating into.
    If the parent is then killed, every later run reads that workspace as
    LIVE for as long as the child survives, and its staged tree stays.
    """
    global _MIGRATION_WORKSPACE_LOCK

    handle = _MIGRATION_WORKSPACE_LOCK
    _MIGRATION_WORKSPACE_LOCK = None
    if handle is None:
        return
    try:
        handle.close()
    except OSError:
        # Releasing a lock this process was never entitled to hold.
        pass


def _reset_migration_lock_after_fork() -> None:
    """Give the child a fresh lock, because it inherited a held one.

    ``app/main_server`` sets the multiprocessing start method to "fork",
    so this is reachable rather than theoretical: fork copies the lock in
    whatever state it was in, but not the thread that held it. A child
    that forked while a migration was running would then block in
    ``migrate_memory_files`` for good, with nothing left to release it.

    Replacing it is safe precisely because the child has no other
    threads: whatever the parent was part-way through is not running
    here, so there is nothing for the old lock to still be protecting.
    """
    global _MIGRATION_LOCK

    _MIGRATION_LOCK = threading.Lock()
    _release_inherited_workspace_lock()


if hasattr(os, "register_at_fork"):
    # POSIX only, which is also the only place fork exists.
    os.register_at_fork(after_in_child=_reset_migration_lock_after_fork)


def _workspace_heartbeat(workspace):
    """A callable that keeps a workspace's mtime moving, at most so often.

    Its own object rather than something the copy owns, because the COPY
    is not the only slow phase: the flush after it can stall on writeback
    independently of how long the copy took, which is the correction that
    sent this. Both phases beat on one timer, so the interval means the
    same thing across the whole run.
    """
    last = [time.monotonic()]

    def _beat():
        now = time.monotonic()
        if now - last[0] >= _MIGRATION_HEARTBEAT_SECONDS:
            last[0] = now
            try:
                os.utime(str(workspace), None)
            except OSError:
                # Cosmetic on any filesystem that also holds the lock;
                # nothing to do if neither works.
                pass

    return _beat


# Big enough that the loop is not the cost, small enough that a stalled
# device cannot sit inside one read for long.
_MIGRATION_COPY_CHUNK = 4 * 1024 * 1024


def _copy_with_heartbeat(beat):
    """A copy2 that beats DURING a file, not only between files.

    The lock is the real answer to "is this workspace live", but it can
    fail to be taken at all -- a filesystem that will not hold one, a
    marker that cannot be created. The age check is then the only thing
    left, and a directory's mtime does NOT move while a deep copytree
    fills it, so a single large character could age past the threshold
    while it is being written and be reclaimed out from under itself.

    Touching the workspace once per ENTRY is not enough: the run can spend
    the whole time inside one entry. Beating between FILES was not enough
    either, for the same reason one level down -- the run can spend the
    whole time inside one file, which is the entire flat-file branch and
    is also reachable through copytree when one member dominates.

    Chunked rather than ``copy2``, so the beat lands while the bytes move.
    Metadata is still copied afterwards, and a symlink that must not be
    followed is handed straight to ``copy2`` -- there are no bytes to
    stream in that case.
    """

    def _copy(source, destination, *, follow_symlinks=True):
        beat()
        if not follow_symlinks and os.path.islink(str(source)):
            return shutil.copy2(source, destination, follow_symlinks=False)
        destination = str(destination)
        # A NAMED PIPE is refused, not opened. ``copy2`` detects one and
        # raises ``SpecialFileError``; reading it instead blocks for ever
        # waiting for a writer, and this runs while the migration lock is
        # held, so a FIFO anywhere in a seed tree would stop startup dead.
        # Losing that check is what replacing copy2 cost, so it is restored
        # here rather than assumed.
        for candidate in (str(source), destination):
            try:
                mode = os.stat(candidate).st_mode
            except OSError:
                continue
            if stat.S_ISFIFO(mode):
                raise shutil.SpecialFileError(
                    "`%s` is a named pipe" % candidate
                )
        if os.path.isdir(destination):
            destination = os.path.join(
                destination, os.path.basename(str(source))
            )
        with open(str(source), "rb") as reader:
            with open(destination, "wb") as writer:
                while True:
                    chunk = reader.read(_MIGRATION_COPY_CHUNK)
                    if not chunk:
                        break
                    writer.write(chunk)
                    beat()
        # Same metadata ``copy2`` would have preserved, including the mode
        # a packaged seed arrives read-only with.
        shutil.copystat(str(source), destination)
        return destination

    return _copy


def _force_rmtree(path):
    """Remove a tree even when Windows made part of it read-only.

    ``copy2`` preserves a seed's mode, so a staged tree can hold
    read-only files -- and Windows refuses to unlink those, which
    ``ignore_errors`` then swallows, leaving the tree standing. That is
    not merely untidy here: every later directory entry stages under the
    SAME name, so one such leftover makes every subsequent character fail
    with FileExistsError. Measured: a read-only file in a failed entry
    strands all the entries after it.

    Still best effort at the end -- what it cannot remove is reported by
    the caller checking, not by raising out of a cleanup path.
    """
    def _clear_read_only(_func, target, _exc):
        try:
            # ADD the write bit; do not replace the mode with it. Setting
            # S_IWRITE alone is 0o200, which on POSIX takes read and execute
            # off a directory and leaves it untraversable -- so the retry
            # cannot unlink what is inside and the tree stays. Windows only
            # reads the write bit here, so keeping the rest costs nothing
            # there.
            try:
                current = os.stat(target).st_mode
            except OSError:
                current = 0
            os.chmod(target, current | stat.S_IWRITE | stat.S_IREAD)
            if os.path.isdir(target):
                # Traversal, which is what an unwritable parent was blocking.
                os.chmod(target, os.stat(target).st_mode | stat.S_IEXEC)
            _func(target)
        except OSError:
            # The caller checks whether the tree actually went; a
            # cleanup that raises would replace the real failure.
            pass

    handler = ("onexc" if sys.version_info >= (3, 12) else "onerror")
    try:
        shutil.rmtree(str(path), **{handler: _clear_read_only})
    except OSError:
        # Same reason. Whether it is gone is a question the caller asks
        # of the filesystem, not of this call.
        pass


def _fsync_directory(path):
    """Flush a directory entry, where the platform allows it.

    ``os.replace`` publishes a NAME, and the name lives in the parent
    directory -- so fsyncing the staged data is only half of it: a power
    loss can still lose the entry and leave the destination missing, or
    present but unlinked from its data. Since the migration skips a
    destination that exists, a half-published name is not something a
    later start repairs.

    Best effort on purpose. Windows cannot open a directory for reading at
    all -- ``os.open`` raises PermissionError -- so this is a POSIX-only
    guarantee, and durability is not worth failing a startup migration
    over.
    """
    try:
        handle = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        # Best effort by design, per the docstring: a platform that will
        # not flush a directory is not a disk that could not take the
        # write, and durability is not worth failing a startup migration.
        pass
    finally:
        os.close(handle)


def _fsync_file(path):
    """Flush one staged file, even one ``copy2`` made read-only.

    A packaged or checked-out seed is often read-only, and ``copy2``
    preserves the mode -- so the staged copy is read-only too and opening
    it "rb+" raises PermissionError. That is not cosmetic: the file branch
    discards the stage and the file never migrates at all, which the plain
    copy2 it replaced handled fine.

    Opening READ-ONLY is the obvious remedy and does not work here:
    measured on Windows, both open(path, "rb") and os.open(O_RDONLY)
    followed by fsync raise OSError EBADF -- the platform wants a writable
    handle. So the mode is widened just long enough to flush and put back,
    which leaves the published file with exactly the mode it arrived with.

    Failures PROPAGATE. ENOSPC and EIO are the conditions this call exists
    to catch, and a caller that swallows them publishes data that never
    reached storage -- after which the destination exists and every later
    start skips it, which is the failure this whole branch set out to
    remove. Only putting the mode BACK stays silent: the flush has already
    happened by then, and abandoning a migrated file over a mode bit is
    worse than publishing one that is writable when its seed was not.
    """
    original = stat.S_IMODE(os.stat(path).st_mode)
    widened = False
    try:
        try:
            handle = open(path, "rb+")
        except PermissionError:
            # READ as well as write. "rb+" needs both, and a seed
            # installed by another user can arrive group- or
            # other-readable with the owner bit clear -- copy2 reads it
            # fine through the bit it does have, then hands us a staged
            # copy WE own and cannot open. Adding only S_IWRITE leaves
            # the second open failing exactly like the first.
            os.chmod(path, original | stat.S_IWRITE | stat.S_IREAD)
            widened = True
            handle = open(path, "rb+")
        try:
            os.fsync(handle.fileno())
        finally:
            handle.close()
    finally:
        if widened:
            try:
                os.chmod(path, original)
            except OSError:
                # The flush already happened. Abandoning a migrated file
                # over a mode bit is worse than publishing one that is
                # writable when its seed was not.
                pass


# Dot-prefixed so no enumerator reads it as a character, and the same
# prefix in BOTH layouts: inside memory_dir it is half of the evidence
# that an entry is ours to reclaim, and two spellings would have meant
# two rules.
_MIGRATION_WORKSPACE_PREFIX = ".mig-"
_MIGRATION_WORKSPACE_LOCK_NAME = ".lock"

# Workspaces minted inside the character namespace are written down
# here, in the directory beside it that IS ours. Nothing on disk inside
# memory_dir can prove a directory was ever a workspace: a character may
# legally be named ".mig-anything", and a character directory may hold a
# file called ".lock" -- so a name and a marker are shapes anyone can
# reproduce, and reclaiming on the strength of them means deleting a
# whole character. A path only reaches this file because we created it.
_MIGRATION_LEDGER_NAME = "minted"

# The live workspace lock, held for the length of one run. Module state
# rather than instance state because only one migration runs at a time --
# _MIGRATION_LOCK sees to that -- and because the after-fork hook has to
# be able to reach it: a child inherits the open file description and
# goes on holding a lock on a workspace it is not migrating into.
# Written and read only through ``global`` inside the two functions above,
# which is what a lock held for the length of a run looks like -- and why
# a reader can take this binding for dead.
_MIGRATION_WORKSPACE_LOCK = None


def _is_direct_child(root, candidate):
    """Whether ``candidate`` resolves to an immediate child of ``root``.

    DIRECT, not merely underneath. ``_record_minted_workspace`` is only ever
    given a child of memory_dir, and reclamation discards anything deeper --
    so "somewhere under the root" was a wider claim than either the writer
    makes or the reader honours. A nested "/memory/Carol/.mig-x" passed the
    ownership check, was then dropped by reclamation's own shape check, and a
    ledger holding only such lines came out empty and got unlinked.

    Unprobeable counts as NOT ours. Every caller is deciding whether a path
    is ours to rewrite, and "we could not tell" has to mean no there --
    a symlink loop raises from ``resolve`` (OSError on most platforms,
    RuntimeError on some), and either way it is not an answer.
    """
    try:
        root_parts = Path(root).resolve(strict=False).parts
        candidate_parts = Path(candidate).resolve(strict=False).parts
    except (OSError, RuntimeError, ValueError):
        return False
    if len(candidate_parts) != len(root_parts) + 1:
        return False
    return candidate_parts[: len(root_parts)] == root_parts


def _ledger_lines_are_all_ours(lines, memory_root):
    """Whether every line in a ledger looks like something we wrote.

    A ``minted`` file that already belonged to a user or a plugin is adopted
    by the reader, and the tidy-up at the end of reclamation would then
    DELETE it. Path validation stops the directories it names from being
    removed; it does not stop the file itself from being.

    Three things make a line ours, and all three are needed:

      * an absolute path, because that is what we write;
      * whose final component carries the workspace prefix;
      * INSIDE the memory root. The ledger exists only for workspaces minted
        in the character namespace -- see its own comment -- so a line
        pointing anywhere else was written by somebody else. Without this a
        crafted "/tmp/.mig-x" passed, and the file holding it was adopted.

    An empty ledger IS ours -- that is what our own file looks like once the
    last workspace has been reclaimed.
    """
    for line in lines:
        entry = line.strip()
        if not entry:
            continue
        if not os.path.basename(entry).startswith(
            _MIGRATION_WORKSPACE_PREFIX
        ):
            return False
        if not os.path.isabs(entry):
            return False
        if not _is_direct_child(memory_root, entry):
            return False
    return True


def _live_character_names(config_manager):
    """Characters that currently exist, by name.

    Empty on any failure. The caller treats "live" as a reason to KEEP
    seeding, so an unreadable roster falls back to the tombstone answer,
    which is the behaviour before the roster was consulted at all.

    Read as PLAIN JSON, not through ``load_characters``. That path normalizes
    reserved fields and writes the result back, so asking it during migration
    materialized characters.json in the runtime config directory -- and a
    runtime root holding only pristine defaults then reported itself as
    having user content, which is what decides whether a cloud snapshot may
    be imported on first launch. Measured: it turned that case from "import
    the snapshot" into "keep the local defaults".

    Deciding whether to seed must not write anything, and names are all this
    needs.
    """
    try:
        roster_path = str(config_manager.get_config_path("characters.json"))
        with open(roster_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        # Absent, unreadable or malformed all read the same way here, and an
        # existence check ahead of the open would be a second spelling of the
        # same rule: opening for READ cannot create the file. That check was
        # load-bearing only while this went through ``load_characters``,
        # which writes.
        return frozenset()
    catgirls = data.get("猫娘") if isinstance(data, dict) else None
    if not isinstance(catgirls, dict):
        return frozenset()
    return frozenset(name for name in catgirls if isinstance(name, str) and name)


def _tombstone_suppresses_seed(entry_name, tombstoned, live):
    """Whether a seed entry belongs to a character that was deleted.

    A candidate that is a LIVE character wins outright: the file is hers, and
    a tombstone on some other decoding of the same filename says nothing
    about it. Only when no candidate is live does a tombstoned one suppress
    the seed.

    This replaced a specificity ranking -- most literal text matched wins --
    which cannot be right in general: whether "facts_archive_Alice.json"
    belongs to Alice or to archive_Alice depends on which of them exists, not
    on how the patterns are shaped. The roster was available on this class
    the whole time.
    """
    candidates = _seed_entry_owner_candidates(entry_name) | {entry_name}
    if candidates & live:
        return False
    return bool(candidates & tombstoned)


def _tombstoned_character_names(config_manager):
    """Characters recorded as deliberately deleted, by name.

    Best effort and EMPTY on any failure, which is the safe direction: an
    unreadable tombstone file republishes a seed, exactly as before this
    existed. Refusing to migrate on a bad read would be a startup that
    silently stops seeding.
    """
    try:
        state = config_manager.load_character_tombstones_state()
    except Exception:
        return frozenset()
    entries = state.get("tombstones") if isinstance(state, dict) else None
    if not isinstance(entries, list):
        return frozenset()
    names = set()
    for entry in entries:
        name = entry.get("character_name") if isinstance(entry, dict) else entry
        if isinstance(name, str) and name:
            names.add(name)
    return frozenset(names)


def _seed_entry_owner_candidates(filename):
    """Every character name a seed entry could belong to.

    A SET, and deliberately not a ranked guess. "facts_archive_Alice.json" is
    Alice's archive under "facts_archive_{name}.json" and archive_Alice's
    facts under "facts_{name}.json", and which one it is depends on which of
    those two is a character -- pattern shape cannot tell, so this does not
    try. ``_tombstone_suppresses_seed`` asks that question against the live
    roster instead.

    Derived from the two migration tables rather than a third list of
    patterns: the file map for the flat files, the extra entries for the
    legacy "semantic_memory_{name}" directory.
    """
    from utils.character_memory import (
        LEGACY_CHARACTER_MEMORY_EXTRA_ENTRIES,
        LEGACY_CHARACTER_MEMORY_FILE_MAP,
    )

    # BOTH tables. The extra entries hold "semantic_memory_{name}", a legacy
    # vector store that is a DIRECTORY -- so a seed called
    # "semantic_memory_Carol/" belongs to Carol, and reading only the file
    # table left it republished after Carol was deleted.
    candidates = set()
    for pattern in (
        list(LEGACY_CHARACTER_MEMORY_FILE_MAP)
        + list(LEGACY_CHARACTER_MEMORY_EXTRA_ENTRIES)
    ):
        prefix, _, suffix = pattern.partition("{name}")
        if not filename.startswith(prefix) or not filename.endswith(suffix):
            continue
        name = filename[len(prefix):len(filename) - len(suffix) or None]
        if name:
            candidates.add(name)
    return candidates


def _ledger_content_is_ours(ledger, memory_root):
    """Whether an EXISTING ledger holds only lines we could have written.

    The refusal went in on the read side alone, so reclamation stopped
    adopting a "minted" that belonged to a user or a plugin -- while the
    append went on adding workspace paths to it. Writing into somebody's file
    is the half that actually damages it.

    Absent or unreadable counts as ours: there is nothing of theirs to
    damage, and creating the file is the ordinary case.
    """
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        # Nothing of theirs to damage, and creating it is the ordinary case.
        return True
    except OSError:
        # Present but unreadable -- a permission that allows APPEND and not
        # read is enough for this, and the append below would then succeed
        # and modify a file we were never able to look at. Absent is the only
        # OSError that means "ours to create".
        return False
    except UnicodeDecodeError:
        # Truncated mid-character by a kill. Ours or not, appending to it is
        # not going to make it more readable; the read side skips it and so
        # does this.
        return False
    if not any(line.strip() for line in lines):
        # An EXISTING but empty ledger is not ours to write into, and that
        # has to match the tidy-up, which already refuses to delete one: our
        # own file is unlinked the moment it empties, so an empty one left on
        # disk is somebody else's or a truncated remnant. Treating it as ours
        # to append to while refusing to delete it was the two halves
        # disagreeing about the same file.
        #
        # The cost is that a remnant of ours stops being written to until it
        # is removed, so workspaces go unrecorded and unreclaimed -- a
        # directory left behind, which is the cheaper mistake this path keeps
        # choosing.
        return False
    return _ledger_lines_are_all_ours(lines, memory_root)


def _ledger_is_usable(ledger):
    """Whether this path is a ledger we may read or append to.

    An ORDINARY FILE, or nothing. A symlinked leaf or parent points at
    somebody else's file and its contents are not our record; and a FIFO
    passes every link check while ``read_text`` blocks for ever on it,
    waiting for a writer, on the startup path with the migration lock held.
    Absent is fine -- that is the normal case on the layout that never mints
    inside the character namespace.

    The LEAF needs no separate link check: ``lstat`` does not follow one, so
    S_ISREG already rejects a link, a dangling link and a FIFO alike. An
    explicit ``is_symlink()`` here was a second spelling of the same rule --
    a mutation deleting it changed nothing -- and two spellings drift. The
    PARENT still needs its own, because that one is about a directory.
    """
    parent = ledger.parent
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        return False
    try:
        mode = os.lstat(str(ledger)).st_mode
    except OSError:
        # Absent, which is the ordinary case: nothing minted in the
        # character namespace on this layout.
        return True
    return stat.S_ISREG(mode)


def recorded_workspace_paths(app_docs_dir):
    """Workspace paths this migration wrote down as its own.

    The only ownership evidence there is, and the same file reclamation has
    always keyed on. Callers outside this module need it because a name and a
    marker inside ``memory_dir`` are shapes anyone can reproduce -- a
    character may legally be called ".mig-anything" and may hold a ".lock".

    Resolved, so a caller comparing an enumerated child against these gets the
    same spelling on a junctioned memory root.

    Empty on anything unreadable, which is the safe direction for the caller
    this exists for: an import that cannot read the ledger exempts nothing and
    deletes stale data, rather than exempting everything and keeping it.
    """
    ledger = (
        Path(app_docs_dir) / _MIGRATION_STAGING_DIR / _MIGRATION_LEDGER_NAME
    )
    if not _ledger_is_usable(ledger):
        return set()
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    except UnicodeDecodeError:
        # NOT an OSError, so it escapes the handler above. Reclamation
        # already learned this one: a ledger truncated mid-character by a
        # kill. There it came out of a finally and failed the launch; here
        # it would come out of a cloud import. A second reader that did not
        # inherit the first reader's handling is the same asymmetry this
        # module keeps paying for.
        return set()
    recorded = set()
    for line in lines:
        entry = line.strip()
        if not entry:
            continue
        try:
            recorded.add(Path(entry).resolve(strict=False))
        except OSError:
            continue
    return recorded


def _hold_workspace_lock(handle):
    """Take an exclusive, non-blocking lock on an open workspace marker."""
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _claim_workspace(path):
    """Mark a workspace LIVE for as long as the returned handle is open.

    Age alone cannot tell a stale workspace from a slow one. The reclaim
    only ever had a clock, so a run that spent longer than the threshold
    on a single entry -- or one suspended with the machine -- looked
    exactly like one that had been killed, and a second process could
    delete a workspace still being written into.

    Returns None when the lock cannot be taken at all. That is not a
    failure: the lock only ever VETOES a deletion, so not having one
    leaves the age check exactly as it was.
    """
    marker = os.path.join(str(path), _MIGRATION_WORKSPACE_LOCK_NAME)
    # The handle is RETURNED STILL OPEN, on purpose: holding it open is
    # what holds the lock, and the lock is the whole point. The caller
    # keeps it in _MIGRATION_WORKSPACE_LOCK for the length of the run and
    # _release_inherited_workspace_lock closes it.
    try:
        handle = open(marker, "a+b")
        handle.write(b"1")
        handle.flush()
        _hold_workspace_lock(handle)
    except (OSError, ImportError):
        try:
            handle.close()
        except (OSError, NameError, UnboundLocalError):
            # The open itself may be what failed, so there may be no
            # handle to close. Either way the answer is the same and
            # the caller carries on unlocked.
            pass
        return None
    return handle


def _workspace_is_live(path):
    """Whether another run still holds this workspace.

    Answers TRUE for anything it cannot rule out -- an unopenable marker
    is what a live owner looks like on Windows, where the file is held
    exclusively. Only actually taking the lock counts as evidence the
    owner is gone, so this can veto a deletion but never authorise one.

    A workspace with NO marker is not live: it comes from a run killed
    before it could claim one. The age check is what covers that case,
    which is why both conditions are kept rather than one replacing the
    other.
    """
    marker = os.path.join(str(path), _MIGRATION_WORKSPACE_LOCK_NAME)
    # An ORDINARY FILE, checked without following links. "unopenable means a
    # live owner" is the right reading for a real marker held exclusively on
    # Windows -- but a marker that is a DIRECTORY is unopenable too, and it
    # read as live for ever, so a deleted character called ".mig-x" holding a
    # ".lock" directory survived every import. A workspace this migration
    # made always has a plain file there.
    try:
        marker_mode = os.lstat(marker).st_mode
    except OSError:
        # No marker at all: a run killed before it could claim one. The age
        # check covers that case, which is why both conditions are kept.
        return False
    if not stat.S_ISREG(marker_mode):
        return False
    try:
        handle = open(marker, "r+b")
    except OSError:
        return True
    try:
        _hold_workspace_lock(handle)
    except (OSError, ImportError):
        return True
    finally:
        # Closing releases the lock on both platforms, and on Windows it
        # has to happen before anything tries to remove the file.
        handle.close()
    return False


def _same_device(left, right):
    """Whether a rename between these two can work at all.

    ``os.replace`` and ``Path.rename`` raise EXDEV across filesystems, so
    staging is only atomic when the workspace shares a device with the
    destination. Unprobeable counts as SAME: the answer only ever picks a
    staging location, and the default one is right on every install.
    """
    try:
        return os.stat(str(left)).st_dev == os.stat(str(right)).st_dev
    except OSError:
        return True


def _fsync_tree(root, beat=None):
    """Flush every file in a staged tree, then its directories.

    A file-level failure propagates, so the caller abandons the entry
    instead of renaming it into place. Directory flushing stays best
    effort because Windows cannot do it at all -- an unsupported operation
    and a disk that could not take the write are not the same thing.
    """
    for directory, _subdirs, files in os.walk(str(root)):
        for name in files:
            if beat is not None:
                # The flush is a second slow phase, and its length is
                # NOT bounded by the copy that filled the tree -- a
                # writeback stall is its own thing. Without this, a
                # workspace whose lock could not be taken could age out
                # while it was still being made durable.
                beat()
            _fsync_file(os.path.join(directory, name))
        _fsync_directory(directory)


class MigrationsMixin:
    """One-shot startup migrations into the runtime root."""

    def migrate_default_card_faces(self):
        """Backfill built-in default card faces without overwriting user-created ones."""
        source_dir = self.project_config_dir.parent / "static" / "default" / "card_faces"
        if not source_dir.exists():
            return
        if not self.ensure_card_faces_directory():
            return

        try:
            source_files = list(source_dir.glob("*.png"))
        except Exception as e:
            self._log(f"Warning: Failed to scan default card faces: {e}")
            return

        for source_path in source_files:
            target_path = self.card_faces_dir / source_path.name
            if not target_path.exists():
                try:
                    shutil.copy2(source_path, target_path)
                    self._log(f"[ConfigManager] Migrated default card face: {source_path.name}")
                except Exception as e:
                    self._log(f"Warning: Failed to migrate default card face {source_path.name}: {e}")

            source_meta_path = source_path.with_suffix(".json")
            target_meta_path = self.card_face_meta_path(source_path.stem)
            if source_meta_path.exists() and not target_meta_path.exists():
                try:
                    shutil.copy2(source_meta_path, target_meta_path)
                    self._log(f"[ConfigManager] Migrated default card face meta: {source_meta_path.name}")
                except Exception as e:
                    self._log(f"Warning: Failed to migrate default card face meta {source_meta_path.name}: {e}")

    def _get_localized_characters_source(self):
        """Get the localized characters.json source file path based on user language.
        
        Returns:
            Path | None: localized file path, or None when language detection fails or the file does not exist (fall back to default)
        """
        try:
            from utils.language_utils import _get_steam_language, _get_system_language, normalize_language_code
            
            # 优先使用 Steam 语言，其次系统语言
            raw_lang = _get_steam_language()
            if not raw_lang:
                raw_lang = _get_system_language()
            if not raw_lang:
                return None
            
            lang = normalize_language_code(raw_lang, format='full')
        except Exception as e:
            self._log(f"[ConfigManager] Failed to detect language for characters config: {e}")
            return None
        
        if not lang:
            return None
        
        # 映射语言代码到文件后缀
        lang_lower = lang.lower()
        if lang_lower in ('zh-cn', 'zh'):
            suffix = 'zh-CN'
        elif 'tw' in lang_lower or 'hk' in lang_lower:
            suffix = 'zh-TW'
        elif lang_lower.startswith('ja'):
            suffix = 'ja'
        elif lang_lower.startswith('en'):
            suffix = 'en'
        elif lang_lower.startswith('ko'):
            suffix = 'ko'
        elif lang_lower.startswith('ru'):
            suffix = 'ru'
        elif lang_lower.startswith('es'):
            suffix = 'es'
        elif lang_lower.startswith('pt'):
            suffix = 'pt'
        else:
            # 未知语言，回退
            return None

        localized_path = self.project_config_dir / 'characters' / f"{suffix}.json"
        return localized_path if localized_path.exists() else None
    
    def migrate_config_files(self):
        """
        Migrate config files to Documents
        
        Strategy:
        1. Check the config folder under Documents; create it if missing
        2. For each config file:
           - if present under Documents, skip
           - if absent under Documents:
             - characters.json: pick the localized version by language, falling back to default
             - other files: copy from the project config
           - if neither exists, do nothing (defaults are created later)
        """
        # 确保目录存在
        if not self.ensure_config_directory():
            print("Warning: Cannot create config directory, using project config", file=sys.stderr)
            return
        
        # 显示项目配置目录位置（调试用）
        self._log(f"[ConfigManager] Project config directory: {self.project_config_dir}")
        self._log(f"[ConfigManager] User config directory: {self.config_dir}")
        
        # 迁移每个配置文件
        for filename in CONFIG_FILES:
            docs_config_path = self.config_dir / filename
            project_config_path = self.project_config_dir / filename
            
            # 如果我的文档下已有，跳过
            if docs_config_path.exists():
                self._log(f"[ConfigManager] Config already exists: {filename}")
                continue
            
            # 对 characters.json 特殊处理：根据语言选择本地化版本
            if filename == 'characters.json':
                lang_source = self._get_localized_characters_source()
                if lang_source:
                    try:
                        shutil.copy2(lang_source, docs_config_path)
                        self._log(f"[ConfigManager] ✓ Migrated localized config: {lang_source.name} -> {docs_config_path}")
                        continue
                    except Exception as e:
                        self._log(f"Warning: Failed to migrate localized {lang_source.name}: {e}")
                        # 继续走默认拷贝逻辑
            
            # 如果项目config下有，复制过去
            if project_config_path.exists():
                try:
                    shutil.copy2(project_config_path, docs_config_path)
                    self._log(f"[ConfigManager] ✓ Migrated config: {filename} -> {docs_config_path}")
                except Exception as e:
                    self._log(f"Warning: Failed to migrate {filename}: {e}")
            else:
                if filename in DEFAULT_CONFIG_DATA:
                    self._log(f"[ConfigManager] ~ Using in-memory default for {filename}")
                else:
                    self._log(f"[ConfigManager] ✗ Source config not found: {project_config_path}")
    
    def _migration_staging_parent(self):
        """Where run workspaces live, and whether the PARENT is ours.

        Normally beside ``memory/``: ``memory_dir`` is ``app_docs_dir /
        "memory"``, so the sibling is the same volume and ``rename`` works.
        Nothing in ``app_docs_dir`` can be a character, so a reserved name
        there is ours to create, sweep and remove.

        But ``memory`` can be a junction or a mount onto another volume, and
        then that sibling is not on the destination's filesystem at all:
        every publish raises EXDEV, the per-entry handler swallows it, and
        nothing migrates -- worse than the plain copy2 this replaced, which
        never needed them to match. So the workspace has to go inside
        ``memory_dir``.

        It does NOT get a reserved name there. ``.mig-staging`` is a legal
        character name (``allow_dots=True`` accepts a leading dot), so
        claiming a fixed path inside the namespace means claiming a
        directory that can be somebody's character -- and an age sweep in it
        would delete their hidden files. Measured, not assumed:
        ``validate_character_name(".mig-staging", allow_dots=True)`` passes.

        So the cross-device parent is ``memory_dir`` itself and it is NOT
        ours: workspaces are minted there under a name nothing else can
        hold, and reclamation there has to prove ownership by something
        other than position. See ``_reclaim_migration_staging``.
        """
        if _same_device(self.app_docs_dir, self.memory_dir):
            return Path(self.app_docs_dir) / _MIGRATION_STAGING_DIR, True
        return Path(self.memory_dir), False

    def _reclaim_migration_staging(self, workspace):
        """Remove this run's workspace, and what a killed run left behind.

        Called unconditionally, including on the early return for a missing
        seed root. A run killed after staging a large tree leaves it in the
        user-data root, and if the next installed build has no
        ``memory/store`` the preparation step is never reached again -- so a
        reclaim that only ran when this run had staged something would never
        run at all, and the copy would sit there indefinitely.

        Only this run's OWN workspace is removed outright. A sibling has to
        be aged out AND unlocked: age alone could not tell a stale workspace
        from a slow or suspended one, and the lock alone cannot speak for a
        run killed before it claimed one, so both are required and either
        saying "leave it" is enough. That is also what makes a second
        PROCESS safe -- ``_MIGRATION_LOCK`` covers threads only and the
        single-instance lock can fail open, so two runs really can share
        this parent; removing it whole meant whichever finished first
        deleted the other's live copy.

        Ownership is PROVED rather than inferred from position, and the
        same way in both parents: the name carries our prefix and the
        directory holds a lock marker. Inside ``memory_dir`` that is the
        only thing standing between the sweep and somebody's character --
        a character directory holds ``facts.json``, ``persona.json``,
        ``settings.json``, the time index and its sidecars, never a
        ``.lock``. Beside it, the reserved name is far less likely to be
        anyone else's, but "far less likely" is not a reason to hold two
        rules, and being wrong there deletes data just the same.

        The marker cannot authorise anything on its own; its ABSENCE is
        what vetoes, so the failure direction is "left behind", never
        "deleted". The price is a run killed in the moment between minting
        a workspace and claiming it: that one is never reclaimed, and it
        keeps the parent alive with it. A directory, not data.

        The parent is removed only where it is ours -- inside
        ``memory_dir`` it is the namespace itself.
        """
        try:
            # Before the removal, not after: on Windows an open file
            # cannot be unlinked, and this one is inside the tree.
            _release_inherited_workspace_lock()
            if workspace is not None:
                _force_rmtree(workspace)
            parent, owned = self._migration_staging_parent()
            cutoff = time.time() - _MIGRATION_STAGING_STALE_SECONDS
            # ALWAYS, whichever layout THIS run sees. The ledger holds
            # only paths we minted, and the layout can change under it:
            # a junction removed after a cross-device run leaves records
            # that the same-device branch would never read again, so
            # those workspaces would sit in memory_dir for good.
            self._reclaim_recorded_workspaces(workspace, cutoff)
            if not owned:
                # The character namespace. Nothing THERE can prove
                # ownership, so the ledger is the whole answer and
                # there is nothing else in here to enumerate.
                return
            # OWNERSHIP HERE IS POSITION, and that is a decision rather
            # than a proof.
            #
            # The sweep below treats every aged, marked, unlocked child of
            # this directory as a workspace of ours. That holds only while
            # nothing else claims the reserved name, and app_docs_dir is not
            # exclusively ours -- plugin/plugins/_shared/rapidocr/_paths.py
            # and plugin/server/routes/_install_task_store.py both create
            # their own directories under it. So a pre-existing
            # ".mig-staging" belonging to somebody else, holding an aged
            # child with a stray ".lock", would have that child removed.
            #
            # Weighed and kept, deliberately. Refusing to sweep a directory
            # this run did not create is the obvious guard, but our own is
            # rmdir'd at the end of a run -- so one found on disk means the
            # PREVIOUS run crashed, which is the case this sweep exists for.
            # That guard would trade a narrow collision for crash remnants
            # accumulating for ever. Moving this namespace onto the ledger
            # was the other option; the ledger lives inside the directory
            # whose ownership is in question, so it cannot settle it.
            #
            # The character namespace does NOT rely on position, because
            # nothing there can prove ownership -- see the ledger above.
            #
            # A link or a plain file at the reserved name means we never
            # used it -- the preparation step worked around it. Do not walk
            # it and do not rmdir it: on Windows rmdir removes a DIRECTORY
            # SYMLINK outright, which would delete the very thing
            # preparation refused to touch.
            if parent.is_symlink() or not parent.is_dir():
                return
            for entry in parent.iterdir():
                if not entry.name.startswith(_MIGRATION_WORKSPACE_PREFIX):
                    continue
                try:
                    if entry.stat().st_mtime >= cutoff:
                        continue
                except OSError:
                    # No age, no evidence it is stale. Leave it.
                    continue
                if not entry.is_dir() or entry.is_symlink():
                    # Left alone. Nothing here ever mints a FILE in the
                    # parent -- staged files go inside the run workspace --
                    # so this arm could only ever have deleted something
                    # that was not ours, and a marker is not a thing a
                    # file can carry. The cost is that a stray one keeps
                    # the parent alive, since rmdir wants it empty.
                    continue
                if not (entry / _MIGRATION_WORKSPACE_LOCK_NAME).exists():
                    # No marker, no proof it was ever a workspace of ours.
                    continue
                if _workspace_is_live(entry):
                    # Old, but somebody still has it open.
                    continue
                _force_rmtree(entry)
            # Empty only. A live run keeps its own workspace here, and that
            # is precisely when this must not succeed. Only reached on the
            # owned parent -- the namespace returns above, since removing
            # memory_dir is not a thing to contemplate.
            parent.rmdir()
        except OSError:
            # Reclamation runs in a finally on the startup path. Failing to
            # tidy up is never worth replacing the real outcome.
            pass

    def _migration_ledger_path(self):
        """Where minted-in-the-namespace workspaces are written down."""
        return (
            Path(self.app_docs_dir)
            / _MIGRATION_STAGING_DIR
            / _MIGRATION_LEDGER_NAME
        )

    def _record_minted_workspace(self, workspace):
        """Write down a workspace minted where position proves nothing.

        Best effort, and the failure direction is the safe one: an
        unrecorded workspace is never reclaimed, which costs a directory.
        The other way round costs a character.
        """
        ledger = self._migration_ledger_path()
        parent = ledger.parent
        # The SAME refusal reclamation makes, one step earlier. Both sides
        # have to make it: "mkdir(exist_ok=True)" accepts a
        # symlink-to-directory at the reserved name and the append then
        # follows it, so a link pointing at unrelated user or plugin data got
        # a "minted" file written into it -- or an existing one of theirs
        # appended to. Refusing to WRITE through it is what stops the file
        # existing at all.
        #
        # Skipping the record is the safe direction, and the one this
        # function already takes on any other failure: an unrecorded
        # workspace is never reclaimed, which costs a directory. The other
        # way round costs a character.
        if not _ledger_is_usable(ledger):
            return
        # And the CONTENT, matching the read side. That refusal went in
        # there alone, so reclamation stopped adopting somebody else's
        # "minted" while this went on appending workspace paths into it --
        # which is the half that damages their file.
        if not _ledger_content_is_ours(ledger, self.memory_dir):
            return
        try:
            parent.mkdir(parents=True, exist_ok=True)
            with open(ledger, "a", encoding="utf-8") as handle:
                handle.write(str(workspace) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            # Nothing to do about it, and nothing unsafe about it.
            pass

    def _reclaim_recorded_workspaces(self, keep, cutoff):
        """Remove workspaces we WROTE DOWN, and forget the ones already gone.

        The namespace is enumerated by nothing here. A path is a candidate
        only because a previous run recorded creating it, which is the one
        claim a character directory cannot make -- a legal character name
        may start with ".mig-", and a character directory may hold a file
        called ".lock", so neither the name nor the marker is evidence.

        Age and the lock still apply on top, as vetoes: a recorded
        workspace that is fresh, or that somebody still holds, stays.
        """
        ledger = self._migration_ledger_path()
        # The same refusal preparation makes: a link at the reserved name --
        # leaf or parent -- points at somebody else's file, so anything
        # called "minted" in there is theirs. Reading it is bad enough; the
        # tidy-up at the end of this function would then UNLINK it.
        if not _ledger_is_usable(ledger):
            return
        try:
            recorded = ledger.read_text(encoding="utf-8").splitlines()
        except OSError:
            # No record, nothing reclaimable. Not an error: the layout
            # that writes one is the exotic one.
            return
        except UnicodeDecodeError:
            # Truncated mid-character by a kill, or a file that was
            # never ours. This is NOT an OSError, so it escaped the
            # handler above, came out of a finally on the startup path,
            # and would have failed the launch on every attempt --
            # reclamation is best effort and must never do that.
            return
        if not _ledger_lines_are_all_ours(recorded, self.memory_dir):
            # A "minted" that already belonged to a user or a plugin. Path
            # validation stops the directories its lines name from being
            # removed, but the tidy-up below would still DELETE the file
            # itself -- so the whole pass is skipped rather than adopting
            # somebody else's record and then unlinking it.
            #
            # The cost is that a genuinely corrupted ledger of ours stops
            # being reclaimed, leaving directories behind. That is the
            # cheaper of the two mistakes, and the direction every other
            # refusal on this path already takes.
            return
        kept = Path(keep) if keep is not None else None
        namespace = Path(self.memory_dir)
        remaining = []
        for line in recorded:
            recorded_path = line.strip()
            if not recorded_path:
                continue
            entry = Path(recorded_path)
            # The record is the one claim a character cannot forge, and
            # that is the ONLY thing it is. It is not a licence to
            # delete: this file could predate the migration, be written
            # by something else, or be corrupted, and every line was
            # taken as a path to remove recursively -- including one
            # naming a directory outside the application root entirely.
            #
            # So shape and containment are required TOO. Each condition
            # can only ever refuse, so together they are strictly safer
            # than any one of them, and a line that fails them is
            # forgotten rather than kept: nothing will ever act on it.
            if entry.parent != namespace:
                continue
            if not entry.name.startswith(_MIGRATION_WORKSPACE_PREFIX):
                continue
            if kept is not None and entry == kept:
                # This run's own, already removed above -- unless the removal
                # did not take. Dropping the record unconditionally made a
                # leftover that rmtree could not clear unreclaimable for
                # good, because the record is the only thing that ever brings
                # anyone back to a path inside the namespace.
                if entry.exists():
                    remaining.append(recorded_path)
                continue
            if entry.is_symlink() or not entry.is_dir():
                # Gone, or no longer the directory we made. Either way
                # there is nothing of ours left at that path.
                continue
            try:
                if entry.stat().st_mtime >= cutoff:
                    remaining.append(recorded_path)
                    continue
            except OSError:
                remaining.append(recorded_path)
                continue
            if not (entry / _MIGRATION_WORKSPACE_LOCK_NAME).exists():
                # Recorded, in the right place, named right -- and still
                # not carrying what we put in every workspace we make.
                # The marker stays required: without it, a corrupted
                # ledger naming a ".mig-"-prefixed CHARACTER directory
                # would have it deleted, and a directory left behind is
                # the cheaper of the two mistakes.
                #
                # But the record is KEPT. A cleanup that removed the
                # marker and then failed on a file still open would
                # otherwise have the next run drop the only thing that
                # points at a workspace still sitting there.
                remaining.append(recorded_path)
                continue
            if _workspace_is_live(entry):
                remaining.append(recorded_path)
                continue
            _force_rmtree(entry)
            if entry.exists():
                remaining.append(recorded_path)
        try:
            if remaining:
                ledger.write_text(
                    "\n".join(remaining) + "\n", encoding="utf-8"
                )
            elif any(line.strip() for line in recorded):
                # Ours to remove: it HELD real entries, every one of them
                # ours, checked before any was acted on, and none is left.
                #
                # A ledger with NO entries is not removed, and blank lines do
                # not count as entries -- testing ``recorded`` for truth told
                # a whitespace-only file apart from an empty one, though
                # neither carries any ownership evidence. Our own emptied
                # file and a user's or plugin's are the same either way, so
                # leaving ours behind costs an empty file while removing
                # theirs is data loss.
                ledger.unlink()
        except OSError:
            # A stale record costs one revisit next run.
            pass

    def _prepare_migration_staging_root(self):
        """Return one private staging workspace for this RUN.

        Every item stages directly in it -- items run one at a time, so they
        cannot collide, and the depth is the same as staging in the parent
        itself. Per-run rather than shared because the parent is reachable
        by a second PROCESS, which ``_MIGRATION_LOCK`` does nothing about;
        the run that finished first used to delete the other's live copy
        along with the parent.

        Retried, because between ``mkdir`` and ``mkdtemp`` another run's
        reclamation can remove the parent -- it is empty at that instant, so
        its ``rmdir`` succeeds. Without the retry ``mkdtemp`` raises
        FileNotFoundError, every seed entry is skipped, and the process is
        still marked migrated for the rest of its session.
        """
        for remaining in reversed(range(_MIGRATION_STAGING_ATTEMPTS)):
            parent, owned = self._migration_staging_parent()
            if not owned:
                # memory_dir itself. Nothing is reserved and nothing is
                # removed: a minted name cannot collide with a character
                # however that character is named, which a fixed one can --
                # ".mig-staging" passes validate_character_name.
                minted = tempfile.mkdtemp(
                    dir=str(parent), prefix=_MIGRATION_WORKSPACE_PREFIX
                )
                # Written down BEFORE it is used, so a run killed while
                # copying is still reclaimable. A run killed in the two
                # statements before this is not, which costs a directory.
                self._record_minted_workspace(minted)
                return self._claimed_workspace(minted)
            # Something else holding the reserved name is worked AROUND, not
            # deleted. A link would be followed out of the tree we are
            # allowed to write, and a plain file makes mkdir raise
            # FileExistsError, which ends the whole loop and migrates
            # nothing -- on every start, forever, since nothing clears it.
            # But removing it means the migration destroying data it cannot
            # identify, and no ownership marker settles that: a fixed
            # filename is something ordinary contents can reproduce, which
            # is the argument that removed the sentinel earlier here.
            #
            # So mint the workspace one level up instead -- app_docs_dir,
            # which this branch only reaches on the same-device layout, so
            # it is the destination's volume. The cost is confined here:
            # nothing sweeps app_docs_dir, so a run killed on this path
            # leaves one directory. That is the right way round -- the leak
            # needs a squatted name AND a kill, while deleting a stranger's
            # data needs only the squatted name.
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                return self._claimed_workspace(
                    tempfile.mkdtemp(
                        dir=str(parent.parent),
                        prefix=_MIGRATION_WORKSPACE_PREFIX,
                    )
                )
            try:
                parent.mkdir(parents=True, exist_ok=True)
                return self._claimed_workspace(
                    tempfile.mkdtemp(
                        dir=str(parent), prefix=_MIGRATION_WORKSPACE_PREFIX
                    )
                )
            except FileNotFoundError:
                if not remaining:
                    raise
                # Another run's cleanup took the parent between the two
                # calls. Ask again from the top.
        # Unreachable: the last pass re-raises rather than falling out.
        # Spelled anyway, because a loop that can end without returning
        # reads as one that returns None on some path.
        raise RuntimeError("migration staging preparation did not settle")

    def _claimed_workspace(self, path):
        """Hold this workspace's lock for the rest of the run."""
        global _MIGRATION_WORKSPACE_LOCK

        workspace = Path(path)
        _MIGRATION_WORKSPACE_LOCK = _claim_workspace(workspace)
        return workspace

    def migrate_memory_files(self):
        """Migrate seeded memory into the runtime root, once, serialised.

        Two threads can reach this: config_manager/__init__.py notes that
        ``_config_manager_migrated`` is not thread-safe. Unserialised they
        share the staging parent, and one run cleans up while the other
        is still copying. It runs once per process, so a lock is cheaper
        than making every step concurrency-safe.
        """
        with _MIGRATION_LOCK:
            self._migrate_memory_files_unlocked()

    def _migrate_memory_files_unlocked(self):
        """
        Migrate memory files to Documents
        
        Strategy:
        1. Check the memory folder under Documents; create it if missing
        2. Migrate all memory files and directories
        """
        # 确保目录存在
        if not self.ensure_memory_directory():
            self._log("Warning: Cannot create memory directory, using project memory")
            return

        # Characters the user deleted. "Destination missing" cannot tell
        # "never migrated" from "migrated, then deleted": a cloud snapshot
        # that keeps a profile but omits every managed memory file has the
        # import unlink them and remove the emptied directory, and the next
        # startup would republish the whole stale seed -- restoring facts and
        # history that were deliberately removed.
        #
        # The record already exists and is written by the delete path; this
        # migration simply never read it.
        tombstoned = _tombstoned_character_names(self)
        live_characters = _live_character_names(self)
        
        # 如果项目memory/store目录不存在，跳过
        
        # 一次未完成的拷贝不能留下半份目录。旧写法直接 copytree 到目标位置，
        # 中途断电/进程被杀就会留下一个残缺的 memory/<name>/——而顶层跳过
        # 看到目录已存在就再也不管它，那些文件从此进不来。没有读者会去看
        # 项目根（facts、persona、settings、三个 sidecar、时间索引全部经由
        # ensure_character_dir(memory_dir, name) 解析，项目根只在枚举时被
        # 扫到），所以那个角色会被列进选择器却分析出一片空白。
        #
        # 先拷进同盘的暂存目录、成功后整体 rename 就位：要么目标完整存在，
        # 要么根本不存在，没有中间态。中断后下次启动因为目标不存在会重新
        # 完整拷一遍，比事后逐个补空缺更彻底——补空缺无法识别「被截断的
        # 文件」，它同样满足 exists()。
        #
        # 反过来也重要：目标已存在时一律不碰。「文件不在运行时根」不等于
        # 「它没拷过来」——云端导入会有意删掉受管文件并 unlink，用户也会
        # 删。往里补会在每次启动复活它们，拿数据搁浅换数据删不掉，是更糟
        # 的一边。
        # INSIDE the try. A full disk, a read-only root or a permission problem
        # makes creating the staging root raise, and this runs on the startup
        # path -- outside the handler it would take get_config_manager() down
        # with it and migrate nothing. A migration that cannot start is a
        # migration skipped, not a broken launch.
        staging_root = None
        try:
            # Inside the handler, like the staging setup below it. Probing a
            # path can raise on a permission problem or an unreadable
            # component, and this runs on the startup path -- outside, that
            # would fail the launch rather than skip a migration.
            if not self.project_memory_dir.exists():
                return
            staging_root = self._prepare_migration_staging_root()
            for item in self.project_memory_dir.iterdir():
                # 每个条目单独兜底。这个 try 原本只包在整个循环外面，于是
                # 第一个失败的条目会把它后面所有角色和散文件一起留在项目
                # 根里——比这次要修的那个缺口更糟。
                try:
                    # The workspace is still in use. Its mtime does not
                    # move on its own during a deep copytree, so without
                    # this the age check ages out a run that is merely
                    # slow. Belt and braces with the lock, which covers
                    # the same case on filesystems that can hold one.
                    try:
                        os.utime(staging_root, None)
                    except OSError:
                        # Cosmetic; the lock is the real answer.
                        pass
                    dest_path = self.memory_dir / item.name

                    # 目标已存在（任何类型、包括坏掉的符号链接）就不碰。
                    # lexists 而不是 exists：断链的 exists() 是 False，
                    # 而 copy2 会顺着它写到 memory_dir 外面去。
                    if dest_path.is_symlink() or dest_path.exists():
                        continue

                    if item.is_symlink():
                        # 种子目录里的链接不跟：拷它的目标等于把 memory_dir
                        # 外面的东西搬进来。
                        print(
                            f"Warning: skip {item.name}: project entry is a symlink",
                            file=sys.stderr,
                        )
                        continue

                    if item.is_file():
                        if _tombstone_suppresses_seed(
                            item.name, tombstoned, live_characters
                        ):
                            # Deleted on purpose. Republishing the seed is
                            # how deleted history came back.
                            continue
                        # 同样要原子化。散文件这一支原先是裸 copy2，中断会
                        # 在目标留下一个被截断的文件——而「目标已存在就跳过」
                        # 会把它永久当成迁移完成，正是目录那支要避免的东西。
                        # mkstemp, NOT a name built from item.name: a source
                        # already close to the filesystem name limit pushes
                        # the staged name over it and the copy fails with
                        # ENAMETOOLONG.
                        handle, staged_name = tempfile.mkstemp(
                            dir=str(staging_root)
                        )
                        os.close(handle)
                        staged_file = Path(staged_name)
                        try:
                            # The SAME heartbeat copy the directory branch
                            # uses. A bare copy2 here never refreshed the
                            # workspace mtime, so a large seed on a stalled
                            # device could age past the reclamation
                            # threshold mid-copy and be deleted out from
                            # under itself by a concurrent run -- the age
                            # check is all that is left whenever the
                            # workspace lock could not be taken.
                            beat = _workspace_heartbeat(staging_root)
                            _copy_with_heartbeat(beat)(item, staged_file)
                            # Durability before publication, the same step
                            # this repo's atomic-write helpers take: without
                            # it a power loss after the rename can leave the
                            # destination NAME on disk with incomplete
                            # contents, and the next start treats it as
                            # authoritative and never retries it.
                            #
                            # Beaten around rather than during: one fsync is
                            # a single call and cannot be subdivided, so the
                            # most this can do is refuse to let the flush be
                            # preceded by a long silent copy.
                            beat()
                            _fsync_file(str(staged_file))
                            beat()
                            # Re-checked HERE, not only before the copy.
                            # A runtime entry that appears while we are
                            # staging is authoritative too, and the
                            # window used to be the whole copy.
                            if dest_path.is_symlink() or dest_path.exists():
                                continue
                            # Windows lets antivirus, indexing or a
                            # preview handler hold the staged file for a
                            # moment, and os.replace then fails with a
                            # sharing violation. Dropping the entry there
                            # costs the whole session: the migration is
                            # marked done for this process and the seed
                            # never arrives. This is the same window
                            # utils/file_utils already backs off over, so
                            # it uses that rather than a second copy of
                            # the error codes and delays.
                            try:
                                publish_without_replacing(
                                    staged_file, dest_path
                                )
                            except FileExistsError:
                                # It appeared after the check above. An
                                # existing runtime entry is
                                # authoritative, so the seed loses.
                                continue
                            except OSError:
                                # No no-replace primitive here -- FAT, or
                                # a network filesystem without links.
                                # Removing the fallback would mean loose
                                # seeds never migrate at all on those
                                # roots, so it stays; what it does not do
                                # is take the earlier check on trust.
                                # Re-asked HERE, one statement before the
                                # overwrite, which is the tightest this
                                # window gets without a primitive the
                                # filesystem does not have.
                                if (
                                    dest_path.is_symlink()
                                    or dest_path.exists()
                                ):
                                    continue
                                replace_with_busy_retry(
                                    staged_file, dest_path
                                )
                            # The NAME too, not just its contents.
                            _fsync_directory(dest_path.parent)
                        finally:
                            if staged_file.exists():
                                try:
                                    staged_file.unlink()
                                except OSError:
                                    # Best effort, and deliberately silent:
                                    # this runs while an earlier failure is
                                    # propagating, so raising here would
                                    # replace the real cause with a cleanup
                                    # error. What is left behind is inside
                                    # the staging root, which comes down
                                    # wholesale in the outer finally and is
                                    # reclaimed by the next run regardless.
                                    pass
                        print(f"Migrated memory file: {item.name}")
                        continue

                    if not item.is_dir():
                        continue

                    if _tombstone_suppresses_seed(
                        item.name, tombstoned, live_characters
                    ):
                        # A directory seed is either named for its character
                        # outright, or is a legacy entry naming it --
                        # "semantic_memory_Carol/" is Carol's vector store,
                        # and comparing the bare name missed it.
                        continue

                    # 同盘暂存 → 原子 rename。暂存目录用点前缀，且只在这
                    # 一小段时间内存在；角色枚举只看目录名与已知文件模式，
                    # 不会把它当成角色。
                    # Straight into the run workspace, under the shortest
                    # name there is -- not a child named for the character,
                    # which would add the longest component of all to every
                    # descendant. Items are sequential, so one name is
                    # enough, and it is gone again by the end of the entry.
                    staging = staging_root / "d"
                    if staging.exists() or staging.is_symlink():
                        # A previous entry failed and something in its
                        # tree would not go. Try harder, then give up on
                        # the shared name entirely rather than let one
                        # entry take every later character down with it.
                        _force_rmtree(staging)
                    if staging.exists() or staging.is_symlink():
                        staging = Path(
                            tempfile.mkdtemp(dir=str(staging_root))
                        ) / "d"
                    try:
                        beat = _workspace_heartbeat(staging_root)
                        shutil.copytree(
                            item,
                            staging,
                            copy_function=_copy_with_heartbeat(beat),
                        )
                        # Same two steps as the file branch, over a tree:
                        # the copied contents first, then the published
                        # NAME. copytree only closes what it writes.
                        _fsync_tree(staging, beat)
                        # Same last-moment re-check as the file branch.
                        # It narrows the window to the rename itself and
                        # does not close it: POSIX rename replaces an
                        # EMPTY directory by design, and RENAME_NOREPLACE
                        # is Linux-only and not exposed by CPython. What
                        # remains reachable can only ever replace a
                        # directory with nothing in it -- a non-empty one
                        # raises ENOTEMPTY, and Windows refuses a
                        # directory destination outright.
                        if dest_path.is_symlink() or dest_path.exists():
                            continue
                        # Same window, same backoff. os.replace moves a
                        # directory onto a name that does not exist yet on
                        # both platforms, which is the only case here.
                        replace_with_busy_retry(staging, dest_path)
                        _fsync_directory(dest_path.parent)
                        print(f"Migrated memory directory: {item.name}")
                    finally:
                        # Only this entry's copy. The workspace is the
                        # whole run's and the next item needs it.
                        _force_rmtree(staging)
                except Exception as exc:
                    print(
                        f"Warning: Failed to migrate memory entry {item.name}: {exc}",
                        file=sys.stderr,
                    )
        except Exception as e:
            print(f"Warning: Failed to migrate memory files: {e}", file=sys.stderr)
        finally:
            # Unconditional, staging_root or not: the early return above
            # for a missing seed root leaves it None, and that is exactly
            # the build on which an earlier kill's leavings would otherwise
            # never be reached again.
            self._reclaim_migration_staging(staging_root)

    def migrate_legacy_documents_memory(self):
        """
        At startup, perform only a **soft migration** of ``memory/`` under legacy roots
        (``Documents\\N.E.K.O`` / original CFA read-only paths, etc.): move character
        directories still present in ``characters.json[猫娘]`` to the current runtime
        ``memory_dir``; if the runtime already has a directory of the same name, keep
        the legacy copy and print a warning — never overwrite.

        **Unlinked entries** (orphan memory whose directory name is not in
        ``characters.json[猫娘]``) are out of scope here; they are handled entirely by
        the Workshop page's "clean up legacy memory" button via
        ``/api/memory/legacy/scan`` + ``purge`` with explicit user selection.

        This method should be called after ``migrate_config_files`` /
        ``migrate_memory_files``, when ``characters.json`` is in place. Any failure is
        only logged, never raised — startup must not be blocked.
        """  # noqa: DOCSTRING_CJK
        try:
            # get_legacy_app_root_candidates 已排除当前 app_docs_dir，且去重
            legacy_roots = list(self.get_legacy_app_root_candidates() or [])
        except Exception as exc:
            self._log(
                f"[ConfigManager] migrate_legacy_documents_memory: 获取 legacy roots 失败: {exc}"
            )
            return

        # CFA 回退场景：_readable_docs_dir 是只读原 Documents，也要纳入。
        # 只读根意味着 rmtree 永远失败、target 永远存在，下面会基于
        # readonly_legacy_roots 跳过 rmtree 并静默 target_exists 噪音，
        # 避免每次启动都打"清理失败/已存在"的重复日志。
        readonly_legacy_roots: set[str] = set()
        readable_docs = getattr(self, "_readable_docs_dir", None)
        if readable_docs:
            try:
                extra = Path(readable_docs) / self.app_name
                extra_str = str(extra)
                if all(extra_str != str(existing) for existing in legacy_roots):
                    legacy_roots.append(extra)
                readonly_legacy_roots.add(extra_str)
            except Exception:
                pass

        if not legacy_roots:
            return

        try:
            characters = self.load_characters()
        except Exception as exc:
            self._log(
                f"[ConfigManager] migrate_legacy_documents_memory: 加载 characters.json 失败: {exc}"
            )
            return

        # characters.json 是用户可写边界；"猫娘" 字段若被损坏成 list / 字符串等
        # 非空但非 dict 的值，.keys() 会抛 AttributeError 并被外层吞掉。
        catgirl_map = characters.get("猫娘")
        if not isinstance(catgirl_map, dict):
            if catgirl_map is not None:
                self._log(
                    f"[ConfigManager] migrate_legacy_documents_memory: "
                    f"characters.json 中猫娘字段类型异常 "
                    f"({type(catgirl_map).__name__})，跳过本次软迁移"
                )
            else:
                self._log(
                    "[ConfigManager] migrate_legacy_documents_memory: "
                    "characters.json 中无猫娘字段，跳过本次软迁移"
                )
            return

        known_characters = set(catgirl_map.keys())
        if not known_characters:
            # characters.json 异常/为空时无从判断哪些应当迁移，直接退出。
            self._log(
                "[ConfigManager] migrate_legacy_documents_memory: "
                "characters.json 中无角色，跳过本次软迁移"
            )
            return

        # 分项计数便于运维排查"到底为什么没迁"。隐藏/下划线前缀、未关联角色
        # 这两类 skip 是正常 no-op，不单独计数。
        migrated_count = 0
        target_exists_count = 0  # runtime 已存在同名目录，保留 legacy 副本
        non_dir_count = 0  # 命中角色名但条目不是目录（反常，需关注）
        failed_count = 0  # copytree/rename 失败

        def _legacy_error_summary(exc: BaseException) -> str:
            """
            Squash the exception into a sanitized string: keep only the class name +
            errno + strerror, never printing the filename argument carried by
            OSError/PermissionError (it would expose the Documents username +
            character directory name).
            """
            if isinstance(exc, OSError):
                parts = [type(exc).__name__]
                if exc.errno is not None:
                    parts.append(f"errno={exc.errno}")
                strerror = getattr(exc, "strerror", None)
                if strerror:
                    parts.append(f"reason={strerror}")
                return " ".join(parts)
            return type(exc).__name__

        # 日志脱敏策略：所有 self._log 绝不包含完整 legacy 路径 / 角色目录名 /
        # 用户 Documents 路径，只打 root 序号 + 计数 + 条目类型。这些日志可能
        # 被收集到日志文件或遥测，泄露用户本地信息不值当。
        for legacy_root_index, legacy_root in enumerate(legacy_roots, start=1):
            source_is_readonly = str(legacy_root) in readonly_legacy_roots
            try:
                legacy_memory = Path(legacy_root) / "memory"
            except Exception:
                continue
            if not legacy_memory.exists() or not legacy_memory.is_dir():
                continue
            # 保护：绝不处理 runtime memory 自身（防御性重复检查）
            try:
                if legacy_memory.resolve() == Path(self.memory_dir).resolve():
                    continue
            except Exception:
                pass

            # Per-root 兜底：权限错误或 I/O 错误不应中断后续 legacy roots 的迁移
            try:
                legacy_entries = list(legacy_memory.iterdir())
            except Exception as exc:
                self._log(
                    f"[ConfigManager] 枚举 legacy memory 根 #{legacy_root_index} "
                    f"失败，跳过该根: {_legacy_error_summary(exc)}"
                )
                continue

            for entry in legacy_entries:
                try:
                    entry_name = entry.name
                    # 只过滤真正的隐藏条目（dot-file），其它形态的合法性交给
                    # known_characters 裁定——用户如果把角色命名为 "_foo"，
                    # 之前的 "_" 前缀黑名单会直接把它当临时条目静默跳过。
                    if entry_name.startswith("."):
                        continue

                    # 未关联条目交给手动清理按钮，此处不做任何操作
                    if entry_name not in known_characters:
                        continue

                    # runtime 角色记忆期望是目录结构（memory_dir/{name}/time_indexed.db
                    # 等）；同名普通文件会占位并阻断后续写入，必须跳过。
                    if not entry.is_dir():
                        non_dir_count += 1
                        self._log(
                            f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                            f"命中角色名的条目不是目录（类型异常），跳过自动软迁移"
                        )
                        continue

                    target = self.memory_dir / entry_name
                    # target.exists() 对断链软链接返回 False（跟随软链找不到目标），
                    # 但 os.replace 会直接覆盖该软链接，违反"绝不覆盖 runtime 已有
                    # 目标"的语义。is_symlink() 不跟随，把断链也当成"已存在"。
                    if target.exists() or target.is_symlink():
                        # 只读根（如 CFA _readable_docs_dir）上的源永远删不掉，
                        # target 存在是上一次成功迁移后的常态；静默跳过以免每次
                        # 启动都打"已存在"日志噪音。可写根仍正常计数 + 打日志。
                        if not source_is_readonly:
                            target_exists_count += 1
                            self._log(
                                f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                                f"目标已存在于 runtime，保留 legacy 副本避免覆盖"
                            )
                        continue
                    # 跨盘 shutil.move 退化为 copy 时若半途失败，target 可能已
                    # 存在但不完整，下次启动会被 target.exists() 跳过。改为
                    # "复制到同父级临时路径 → 原子 rename → best-effort 清源"。
                    temp_target = target.parent / f".{entry_name}.migrating-{uuid.uuid4().hex}"
                    try:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        # symlinks=False：跟随 legacy 源里的软链，把实际内容拷到
                        # runtime。若保留软链（symlinks=True），legacy 里用户手动
                        # 创建的、指向 memory_dir 外部的链接会让 runtime 的
                        # memory_dir/{name}/time_indexed.db 写入逃出边界。
                        shutil.copytree(str(entry), str(temp_target), symlinks=False)
                        os.replace(str(temp_target), str(target))
                        # 只读根（CFA _readable_docs_dir）上根本不可写，rmtree
                        # 永远会抛 PermissionError。成功迁移后直接跳过清源，
                        # 避免每次启动都打一遍"legacy 源清理失败"日志。
                        if not source_is_readonly:
                            try:
                                shutil.rmtree(str(entry))
                            except Exception as cleanup_exc:
                                self._log(
                                    f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                                    f"已复制到 runtime，但 legacy 源清理失败，保留 legacy 副本: "
                                    f"{_legacy_error_summary(cleanup_exc)}"
                                )
                        migrated_count += 1
                        self._log(
                            f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                            f"已迁移 1 个条目到 runtime"
                        )
                    except Exception as exc:
                        failed_count += 1
                        # 清理可能残留的临时目录/文件，避免下次启动误判
                        try:
                            if temp_target.exists():
                                if temp_target.is_dir():
                                    shutil.rmtree(str(temp_target), ignore_errors=True)
                                else:
                                    temp_target.unlink()
                        except Exception:
                            pass
                        self._log(
                            f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                            f"迁移条目失败: {_legacy_error_summary(exc)}"
                        )
                except Exception as exc:
                    failed_count += 1
                    self._log(
                        f"[ConfigManager] legacy memory 根 #{legacy_root_index}: "
                        f"处理条目时出错: {_legacy_error_summary(exc)}"
                    )

        if migrated_count or target_exists_count or non_dir_count or failed_count:
            self._log(
                f"[ConfigManager] legacy memory 软迁移汇总: "
                f"迁移 {migrated_count} 个, "
                f"目标已存在跳过 {target_exists_count} 个, "
                f"非目录跳过 {non_dir_count} 个, "
                f"失败 {failed_count} 个"
            )
