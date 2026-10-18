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

"""Visit data across a local character's rename and delete (design OD-13, section 3.2.6, PR-09b).

Both transactions leave a recoverable marker at the top level of
``visit_peers.json`` (outside every account partition) for as long as the
visit data is not consistent with the character config:

* rename -- ``pending_rename = {old, new, uid}``, written before the rename
  commits. :func:`settle_rename` then reads the config: the character now
  named ``new`` moves the roster ``by_char`` (with ``last_summary``) and the
  ``own_char`` of every spool header / ``state.json`` (the debrief replay
  queue lives in ``state.json``) to ``new``; a rolled-back rename moves
  whatever was already moved back to ``old``. Only then is the marker
  cleared, so a failure leaves it for startup reconciliation, which runs the
  same :func:`reconcile_rename`.
* delete -- ``pending_retire = [{name, character_uid}, ...]``, one item per
  deleted character, written before the delete commits.
  :func:`settle_retire` drops the item when the character is still
  configured (the delete rolled back) and otherwise retires its visit data:
  spools / ``state.json`` owned by ``character_uid`` (legacy files without
  an owner uid by name), every ``by_char[name]`` of the roster (peers left
  empty go) and the visit persona. ``.upload.json`` files and
  ``visit_reports/`` stay. The memory_server staging of unfinished digests
  lives under ``memory_dir/<character>/`` and goes with the character's
  memory directory in the delete transaction itself. While an item is
  pending, a new character may not take its name (:func:`name_marker_state`).
  A delete of a character whose own rename is still unreconciled settles
  that rename first (otherwise the entries left under its other name would
  outlive it), and a cloudsave import that removes local characters records
  them with :func:`record_removed_characters_sync` before it commits.

While either marker names a character, no new character may take that name
(:func:`name_marker_state`): the roster keys entries by name.

Machines without any visit data never get a marker: the transactions are
unchanged for them.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

from config.visit_settings import VISIT_PEERS_FILENAME, VISIT_PERSONA_DIRNAME, VISIT_SPOOL_DIRNAME
from main_logic.visit import memory_bridge
from main_logic.visit.spool import VisitSpool
from main_logic.visit.subjects import (
    PeerRoster,
    RosterCorruptError,
    add_roster_marker_item,
    add_roster_marker_item_sync,
    clear_roster_marker,
    promote_roster_marker,
    prune_roster_marker_items,
    read_roster_marker,
    read_roster_marker_sync,
    read_roster_markers,
    read_roster_markers_sync,
    rename_marker_names,
    replace_roster_marker,
    replace_roster_marker_sync,
    rotate_roster_marker,
    set_roster_marker_sync,
    remove_roster_marker_item,
    set_roster_marker,
)
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

PENDING_RENAME = "pending_rename"
PENDING_RENAME_QUEUE = "pending_rename_queue"
PENDING_RETIRE = "pending_retire"
CLOUDSAVE_SOURCE = "cloudsave"
"""``source`` of a rename marker a cloudsave import wrote: nothing is migrated before startup recovery."""

RetirePersona = Callable[[str], Awaitable[Any]]
"""``retire_persona(character_uid)``: delete the visit persona of a deleted character."""

LoadNames = Callable[[], Awaitable[tuple[set[str], dict[str, str] | None]]]
"""Returns ``(every configured name, {name: character_uid})`` read from the character config."""


class RenamePendingElsewhere(RuntimeError):
    """An earlier rename's marker is still unreconciled, so a new rename cannot write its own."""


def _may_exist(path: Path) -> bool:
    # Only definite absence counts as missing; failed stat from permissions/contention must not suppress rename/deletion
    # markers, or nobody migrates/retires this data when permissions recover
    try:
        os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


def _has_visit_files_sync(config_dir: Path, character_uid: str | None) -> bool:
    if _may_exist(config_dir / VISIT_PEERS_FILENAME):
        return True
    spool_dir = config_dir / VISIT_SPOOL_DIRNAME
    try:
        with os.scandir(spool_dir) as entries:
            if any(True for _ in entries):
                return True
    except (FileNotFoundError, NotADirectoryError):
        pass  # No spool directory means no sessions; continue checking personas
    except OSError:
        # Failed listing does not mean no visit data: record markers normally, letting migration/retirement report failures and retain them
        return True
    return bool(character_uid) and _may_exist(config_dir / VISIT_PERSONA_DIRNAME / f"{character_uid}.json")


async def has_visit_data(config_dir: str | Path, character_uid: str | None = None) -> bool:
    """Whether this machine has any visit data a rename / delete would have to follow.

    The roster, any file in the spool directory, or (with ``character_uid``)
    that character's visit persona.
    """
    return await asyncio.to_thread(_has_visit_files_sync, Path(config_dir), character_uid)


# ── Renaming ──────────────────────────────────────────────────────────────

ALL_NAMES = None
"""Result of :func:`reconcile_rename`: every name-dependent step must wait."""

MARKER_CHANGED = object()
"""Returned by :func:`reconcile_rename` when the marker is not the one the caller expected."""


async def reconcile_rename(
    config_dir: Path, names: set[str], uid_of: dict[str, str] | None = None,
    expected: Any = None, *, undo_moves: bool = True,
) -> Any:
    """Finish or roll back a pending character rename; return the names still unsettled.

    The marker is ``{old, new}`` plus, when the rename transaction wrote it,
    the renamed character's ``uid``: with ``uid_of`` (current name -> uid)
    the direction is decided by which name that uid has now. Without a uid,
    by which of the two names exists. When neither name exists the
    character was deleted (its data is retired by uid): the marker is
    dropped. When the uid now has a third name (a cloudsave import renamed
    it again while this marker held the slot) and neither old name is reused,
    entries under both names move to the current one. A migration into a
    name that still has a ``pending_retire`` item waits for that retirement
    (the marker is kept). An empty set means no rename is pending; ``{old, new}`` that
    the marker is kept as genuinely ambiguous (only those two names wait);
    ``None`` that the roster or marker is unreadable (everything waits).
    ``undo_moves=False`` (the rename transaction itself, which migrates only
    after it committed) settles a rolled-back rename by clearing the marker
    without rewriting anything.
    """
    config_dir = Path(config_dir)
    try:
        marker = await read_roster_marker(config_dir, PENDING_RENAME)
    except RosterCorruptError as exc:
        logger.error("visit recovery: roster unreadable, rename not reconciled: %s", exc)
        return ALL_NAMES
    if expected is not None and marker != expected:
        # Caller acquired a guard from another marker; this read belongs to a different character, so do not migrate with the wrong guard
        return MARKER_CHANGED
    if marker is None:
        return frozenset()
    old = marker.get("old") if isinstance(marker, dict) else None
    new = marker.get("new") if isinstance(marker, dict) else None
    if not isinstance(old, str) or not old or not isinstance(new, str) or not new:
        # Malformed markers contain no reconcilable information and permanently block recovery/clearing; diagnose, then remove
        memory_bridge.diag("pending_rename_malformed")
        logger.error("visit recovery: malformed pending_rename %r dropped", marker)
        return frozenset() if await clear_roster_marker(config_dir, PENDING_RENAME, marker) else ALL_NAMES
    # rename_char rewrites all account partitions by local character names, independently of own_uid
    roster = PeerRoster(config_dir, own_uid="pending-rename")
    uid = marker.get("uid") if isinstance(marker.get("uid"), str) and marker.get("uid") else None
    if uid is not None and uid_of is not None:
        # When uid exists, use its current name to determine direction, even if both names exist because another character occupies the old one
        current = {name for name, value in uid_of.items() if value == uid}
        # Another character occupies the alternate name after reuse; name-only roster entries would mix
        # both characters' records. If inseparable, do not migrate; retain the marker and block only these names
        # A cloud import handed the name to a previously absent character (handed_over); at import, its existing entries
        # still belonged to this character, so migrate normally instead of letting the new character inherit them
        handed = _handed_over_names(marker)
        held = rename_marker_names(marker) or (old, new)
        if {name for name, value in uid_of.items() if value == uid} & {old, new} and marker.get("migrated") is not True:
            # Character remains under one of this rename's two names: chained names came from a rolled-back cloud import,
            # or it renamed back. They no longer belong to it and do not indicate reuse, unless migration began (migrated):
            # some entries may already occupy chained names and must migrate back too
            held = (old, new)
        elif len(current) == 1 and marker.get("migrated") is not True and next(iter(current)) in held[2:]:
            # Similarly, a character still under an intermediate chained name excludes later names added by rolled-back imports
            held = held[:held.index(next(iter(current))) + 1]
        reused = any(name in uid_of and uid_of[name] != uid and name not in handed for name in held)
        forward = new in current and not reused
        backward = old in current and not forward and not reused
        deleted = not current
        # Character renamed again through cloud import while this marker occupied the slot, preventing a separate marker; names were recorded
        # in chained. Entries under every marked name still belong to it; migrate all to its current name
        moved = len(current) == 1 and not current & {old, new} and not reused
    else:
        held = (old, new)
        forward = new in names and old not in names
        backward = old in names and new not in names
        deleted = old not in names and new not in names
        moved = False
    # Cloud-import renames migrate only during startup reconciliation, so rollback means nothing moved. Do not reverse them as ordinary renames:
    # the new name holds real data of another local character whose planned deletion also rolled back
    # Once migrated, this is no longer untouched: import committed, and subsequent return to the old name requires normal reverse migration
    migrated = marker.get("migrated") is True
    from_cloudsave = marker.get("source") == CLOUDSAVE_SOURCE and not migrated
    undo = undo_moves and not from_cloudsave
    if from_cloudsave and uid is not None and uid_of is not None and old in current and not forward:
        # Import rolled back, leaving the old name: nothing migrated, so remove markers even if another character occupies the new name
        backward = True
    target = new if forward else next(iter(current)) if moved else old if backward and undo else None
    if target is not None:
        try:
            retiring = await read_roster_marker(config_dir, PENDING_RETIRE)
        except RosterCorruptError as exc:
            logger.error("visit recovery: roster unreadable, rename not reconciled: %s", exc)
            return ALL_NAMES
        # Retirement entries with roster_done no longer touch that name; remaining uid-based steps
        # cannot affect incoming entries, so must not keep renames waiting indefinitely
        if isinstance(retiring, list) and any(
                (_item_fields(item) or ("", None))[0] == target and item.get("roster_done") is not True
                for item in retiring):
            # Target still has unretired residue from a deleted character; migrating now lets later name-based retirement delete these entries too.
            # Retain markers until retirement completes; startup recovery finishes retirements before reconciliation
            logger.warning("visit recovery: pending_rename %r -> %r waits for the retirement of %r", old, new, target)
            # Target may be a third name outside held; report it too so name-occupancy checks recognize retirement waits rather than ambiguity
            return frozenset(held) | {target}
    if (forward or moved) and not migrated and (from_cloudsave or len(held) > 2):
        # Migration comprises independent writes; partial failures retain markers after moving some entries. Record migration started first,
        # so later returns to old names reverse normally, including chained names, rather than removing markers as if import rolled back untouched
        started = {**marker, "migrated": True}
        if not await replace_roster_marker(config_dir, PENDING_RENAME, marker, started):
            logger.warning("visit recovery: pending_rename %r -> %r changed before migrating, deferred", old, new)
            return ALL_NAMES
        marker = started
    if forward or moved:
        # Migrate entries under every marked name, including chained intermediates, to its current name
        for name in held:
            if name != target:
                await roster.rename_char(name, target)
                await VisitSpool.rename_own_char(config_dir, name, target)
    elif backward and not undo:
        # Transaction finalizes itself: migration occurs only after commit, leaving rollback untouched. Do not reverse migration,
        # which could wrongly attach deleted-character residue under the new name to this character
        pass
    elif backward:
        # Rename did not apply: restore sessions rewritten under the new name to the old name, including partially migrated chained names
        for name in held:
            if name != old:
                await VisitSpool.rename_own_char(config_dir, name, old)
                await roster.rename_char(name, old)
    elif deleted:
        # Neither name exists: character deleted, with roster/sessions handled by deletion retirement by uid.
        # Nothing remains to reconcile, and retaining markers only permanently blocks clearing/per-session recovery
        logger.warning("visit recovery: pending_rename %r -> %r names a deleted character, dropped", old, new)
    else:
        logger.warning("visit recovery: pending_rename %r -> %r is ambiguous, kept", old, new)
        # Every marked name, including chained, remains occupied; overwrite downloads for any must request deletion of one character first
        return frozenset(held)
    return frozenset() if await clear_roster_marker(config_dir, PENDING_RENAME, marker) else ALL_NAMES


async def promote_queued_rename(config_dir: str | Path) -> bool:
    """Move the next queued rename into the empty ``pending_rename`` slot; True when one moved."""
    return await promote_roster_marker(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE) is not None


async def drop_deleted_queued_renames(config_dir: str | Path, uid_of: dict[str, str] | None) -> None:
    """Drop the queued renames of characters no longer configured (needs ``uid_of``).

    The same outcome as the slot's deleted branch: nothing moves, the
    delete's retirement handles the data. Done without waiting for the slot,
    which an ambiguous rename may hold for good.
    """
    if uid_of is None:
        return
    configured = set(uid_of.values())
    for item in _queued_renames(await read_roster_marker(config_dir, PENDING_RENAME_QUEUE)):
        uid = item.get("uid") if isinstance(item, dict) else None
        if isinstance(uid, str) and uid and uid not in configured:
            logger.warning("visit recovery: queued rename %r names a deleted character, dropped", item)
            await remove_roster_marker_item(config_dir, PENDING_RENAME_QUEUE, item)


async def drop_malformed_queued_renames(config_dir: str | Path) -> None:
    """Drop the ``pending_rename_queue`` items that carry no ``old`` / ``new`` (a non-list queue entirely).

    The same outcome as the slot's malformed branch: nothing is left to
    reconcile, and keeping them would defer every clearing and replay (the
    queued names cannot be read) for good.
    """
    dropped = await prune_roster_marker_items(
        config_dir, PENDING_RENAME_QUEUE, lambda item: rename_marker_names(item) is not None)
    if dropped:
        memory_bridge.diag("pending_rename_queue_malformed")
        logger.error("visit recovery: malformed pending_rename_queue items %r dropped", dropped)


async def reconcile_renames(
    config_dir: Path, names: set[str], uid_of: dict[str, str] | None = None,
    reconcile: Callable[[], Awaitable[Any]] | None = None, pending_groups: list[frozenset[str]] | None = None,
    drop_deleted: bool = True,
) -> Any:
    """:func:`reconcile_rename` for the slot and every queued rename; return the names still unsettled.

    A cloudsave import that renamed several characters queues all but the
    first (``pending_rename_queue``); the slot takes them one at a time.
    Malformed and deleted characters' queued renames are dropped first. A
    rename that stays pending (ambiguous, or waiting for a retirement) moves
    to the end of the queue so the others still settle; one that shares a
    name with a pending one keeps its turn behind it (migrating it first
    would mix both characters' entries). Each marker is tried once per call.
    Returns the names of the renames reconciled and still pending (empty when
    all settled), or ``ALL_NAMES`` when the roster is unreadable.
    ``reconcile`` (startup recovery: under the character's lifecycle guard)
    replaces the plain :func:`reconcile_rename` of the slot.
    ``pending_groups`` (optional) receives the names of each rename still
    pending, one set per rename. ``drop_deleted=False`` skips dropping the
    deleted characters' queued renames (startup recovery already did it
    under the character-config lock, with a fresher map).
    """
    await drop_malformed_queued_renames(config_dir)
    if drop_deleted:
        await drop_deleted_queued_renames(config_dir, uid_of)
    run = reconcile or (lambda: reconcile_rename(config_dir, names, uid_of))
    pending: set[str] = set()
    waiting: set[str] = set()
    seen: list[Any] = []
    while True:
        try:
            marker = await read_roster_marker(config_dir, PENDING_RENAME)
        except RosterCorruptError as exc:
            logger.error("visit recovery: roster unreadable, rename not reconciled: %s", exc)
            return ALL_NAMES
        if marker is None:
            if await promote_queued_rename(config_dir):
                continue
            break
        if marker in seen:
            # A full cycle was attempted; all remaining entries are blocked, keeping the earliest blocked entry at the head
            break
        seen.append(marker)
        held = set(rename_marker_names(marker) or ())
        if held & (pending | waiting):
            # Sharing names with an earlier pending rename (such as A -> B after B -> C) means migrating first would mix
            # both characters' entries; queue behind it and wait
            waiting |= held
        else:
            unsettled = await run()
            if not isinstance(unsettled, frozenset):
                return unsettled
            if not unsettled:
                continue
            pending |= unsettled
            if pending_groups is not None:
                pending_groups.append(unsettled)
        # Still pending due to permanent ambiguity/retirement: move to queue tail so other characters reconcile normally
        if await rotate_roster_marker(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE, marker) is None:
            break
    return frozenset(pending)


def _queued_renames(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _handed_over_names(marker: Any) -> frozenset[str]:
    names = marker.get("handed_over") if isinstance(marker, dict) else None
    if not isinstance(names, list):
        return frozenset()
    return frozenset(name for name in names if isinstance(name, str) and name)


async def begin_rename(
    config_dir: str | Path, old: str, new: str, character_uid: str | None, *,
    names: set[str], uid_of: dict[str, str] | None,
) -> dict | None:
    """Write ``pending_rename`` before the rename commits; return the marker (``None``: no visit data).

    Runs inside the rename transaction (under the character-config lock),
    with ``names`` / ``uid_of`` read from the config before the rename. An
    earlier marker is reconciled first, and the whole queue when this
    character has a queued rename or ``old`` was handed over to it by another
    character's pending rename; when it cannot be (ambiguous, unreadable
    spools, this character's own or the handing rename still pending)
    :class:`RenamePendingElsewhere` is raised and nothing is written. An unreadable roster raises :class:`RosterCorruptError`.
    """
    config_dir = Path(config_dir)
    if not await has_visit_data(config_dir):
        return None
    # Cloud import just handed the old name to this character while the original owner's rename remains unreconciled; migration would steal its entries

    async def current() -> tuple[set[str], dict[str, str] | None]:
        return names, uid_of

    if await _settle_handed_over(config_dir, old, character_uid, current):
        # Rename handing over the old name is permanently ambiguous; migrating would take the original character's remaining entries, so await resolution
        # Character deletion remains available; see begin_retire
        raise RenamePendingElsewhere("another character's rename still owns this name's visit data")
    # This character has another queued cloud-import rename; reconciling the head does not complete that entry.
    # Reconcile the whole queue first; if still pending, reject this rename, or the old two-name marker fails to reserve the new name
    if await _own_rename_pending(config_dir, old, character_uid, queued_only=True):
        try:
            await reconcile_renames(config_dir, names, uid_of)
        except Exception as exc:  # noqa: BLE001 - reject rename on incomplete reconciliation and leave markers for startup recovery
            logger.warning("visit rename: queued rename of %r not reconciled: %r", old, exc)
            raise RenamePendingElsewhere("this character's queued rename is still being migrated") from exc
        if await _own_rename_pending(config_dir, old, character_uid, queued_only=False):
            raise RenamePendingElsewhere("this character's queued rename is still being migrated")
    marker: dict[str, str] = {"old": old, "new": new}
    if character_uid:
        marker["uid"] = character_uid
    for attempt in range(2):
        written, _existing = await set_roster_marker(config_dir, PENDING_RENAME, marker)
        if written:
            return marker
        if attempt:
            break
        # Previous rename migration failed/crashed; one marker slot exists, so reconcile it with current configuration first
        try:
            unsettled = await reconcile_rename(config_dir, names, uid_of)
        except Exception as exc:  # noqa: BLE001 - reject rename on incomplete reconciliation; retain markers for startup recovery
            logger.warning("visit rename: earlier pending_rename not reconciled: %r", exc)
            break
        if unsettled != frozenset():
            break
    raise RenamePendingElsewhere("an earlier rename's visit data is still being migrated")


async def settle_rename(config_dir: str | Path, marker: Mapping[str, Any], load_names: LoadNames) -> bool:
    """Migrate (or roll back) the visit data of a finished rename transaction; True once the marker is gone.

    ``load_names`` re-reads the config after the transaction: the direction
    is whatever the character is called now (a rolled-back rename moves
    nothing forward). Any failure leaves the marker for startup recovery.
    """
    try:
        names, uid_of = await load_names()
        result = await reconcile_rename(Path(config_dir), names, uid_of, expected=dict(marker), undo_moves=False)
    except Exception as exc:  # noqa: BLE001 - migration failure does not undo committed rename; retain markers for startup recovery
        logger.warning("visit rename: visit data not migrated yet, startup recovery retries: %r", exc)
        return False
    if result is MARKER_CHANGED:
        # Another flow such as startup reconciliation already handled the marker under the same migration rules
        return True
    return result == frozenset()


# ── Deletion retirement ──────────────────────────────────────────────────────────


def retire_item(name: str, character_uid: str | None) -> dict:
    """The ``pending_retire`` entry of one deleted character."""
    return {"name": name, "character_uid": character_uid or None}


async def _settle_own_rename(
    config_dir: Path, name: str, character_uid: str | None, load_names: LoadNames,
) -> tuple[str, ...]:
    """Reconcile a pending rename of the character about to be deleted; raise when it cannot be.

    Retirement goes by the character's current name only: entries a failed
    rename migration left under its other name would outlive it, and a new
    character taking that name later would inherit them. An unsettleable
    rename (unreadable spools) raises :class:`RenamePendingElsewhere`; an
    ambiguous one (the other name was taken by another character meanwhile)
    lets the delete go ahead, nothing being separable any more. Returns the
    other names of every rename of this character still pending afterwards
    (ambiguous, waiting for a retirement, or queued behind a pending rename
    sharing one of its names) that
    no other local character holds: they are retired with the character.
    """
    marker, queued = await read_roster_markers(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE)
    queued = _queued_renames(queued)
    if not _rename_names_of(marker, name, character_uid) and not any(
            _rename_names_of(item, name, character_uid) for item in queued):
        return ()
    try:
        names, uid_of = await load_names()
        unsettled = await reconcile_renames(config_dir, names, uid_of)
    except Exception as exc:  # noqa: BLE001 - reject deletion on incomplete reconciliation; retain markers for startup recovery
        raise RenamePendingElsewhere("this character's rename is still being migrated") from exc
    if unsettled is ALL_NAMES:
        raise RenamePendingElsewhere("this character's rename is still being migrated")
    if unsettled:
        # Another character occupies the alternate name, leaving inseparable entries; deleting this character does not worsen that,
        # and disappearance of its uid lets the next reconciliation clear the marker as deleted
        logger.warning("visit retire: ambiguous pending_rename of %r left as is, delete goes ahead", name)
    # This character's rename remains pending after reconciliation, awaiting retirement or queued behind permanent ambiguity:
    # retire alternate names unoccupied by other characters along with it, then clear its rename as deleted.
    # Truly ambiguous names occupied by other characters remain untouched because entries are inseparable
    held = set(names) - {name}
    marker, queued = await read_roster_markers(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE)
    return tuple(dict.fromkeys(
        other
        for item in (marker, *_queued_renames(queued))
        for other in _rename_names_of(item, name, character_uid)
        if other != name and other not in held
    ))


async def begin_retire(
    config_dir: str | Path, name: str, character_uid: str | None, *, load_names: LoadNames | None = None,
) -> dict | None:
    """Add the ``pending_retire`` item before the delete commits; return it (``None``: no visit data).

    With ``load_names`` (the config before the delete), a pending rename of
    this very character, or one that handed ``name`` over to it, is
    reconciled first; when it cannot be,
    :class:`RenamePendingElsewhere` is raised and nothing is written. An
    unreadable roster raises :class:`RosterCorruptError` (the delete is
    refused rather than leaving visit data no marker points at).
    """
    config_dir = Path(config_dir)
    if not await has_visit_data(config_dir, character_uid):
        return None
    others: tuple[str, ...] = ()
    keep_entries = False
    if load_names is not None:
        # Cloud import just handed this name to the character while the original owner's rename is pending; name-based retirement would delete the original owner's entries
        keep_entries = await _settle_handed_over(config_dir, name, character_uid, load_names)
        others = await _settle_own_rename(config_dir, name, character_uid, load_names)
    for other in others:
        await add_roster_marker_item(config_dir, PENDING_RETIRE, retire_item(other, character_uid))
    item = retire_item(name, character_uid)
    if keep_entries:
        # That rename is permanently ambiguous: delete normally but mark roster cleanup complete without executing it, leaving entries for the original character;
        # the pending rename still reserves this name, preventing new-character inheritance
        item = {**item, "roster_done": True}
    await add_roster_marker_item(config_dir, PENDING_RETIRE, item)
    return item


def _item_fields(item: Any) -> tuple[str, str | None] | None:
    if not isinstance(item, dict):
        return None
    name, uid = item.get("name"), item.get("character_uid")
    if not isinstance(name, str) or not name or not (uid is None or (isinstance(uid, str) and uid)):
        return None
    return name, uid


async def _retire(config_dir: Path, name: str, uid: str | None, names: set[str],
                  retire_persona: RetirePersona | None, *, roster_done: bool = False,
                  ) -> tuple[list[BaseException], bool]:
    """Run every retirement step; return the failures and whether the roster step is done."""
    failures: list[BaseException] = []
    try:
        # Legacy sessions lacking own_char_uid in their header use names only when no new same-named character exists
        await VisitSpool.retire_char(config_dir, uid or "", legacy_name=None if name in names else name)
    except Exception as exc:  # noqa: BLE001 - keep steps independent and report errors together afterward
        failures.append(exc)
    roster_ok = roster_done
    if not roster_done:
        try:
            await PeerRoster(config_dir, own_uid="pending-retire").retire_char(name)
            roster_ok = True
        except Exception as exc:  # noqa: BLE001
            failures.append(exc)
    if uid and retire_persona is not None:
        try:
            await retire_persona(uid)
        except ValueError:
            # Invalid uid: no corresponding persona file can exist
            pass
        except Exception as exc:  # noqa: BLE001
            failures.append(exc)
    return failures, roster_ok


async def settle_retire(
    config_dir: str | Path, item: Mapping[str, Any], *, names: set[str], uid_of: Mapping[str, str] | None,
    retire_persona: RetirePersona | None,
) -> bool:
    """Finish one ``pending_retire`` item against the current config; True once the item is removed.

    Still configured (by uid, or by name for a character without one or
    when ``uid_of`` is not known): the
    delete did not commit, the item is dropped and nothing is retired.
    Otherwise every retirement step runs (idempotent) and the item is
    removed only when all of them succeeded. The roster step goes by name
    and runs once: when only other steps failed, the item is marked
    ``roster_done`` so a retry never clears what a later character of that
    name wrote meanwhile.
    """
    config_dir = Path(config_dir)
    fields = _item_fields(item)
    if fields is None:
        memory_bridge.diag("pending_retire_malformed")
        logger.error("visit retire: malformed pending_retire item %r dropped", item)
        await remove_roster_marker_item(config_dir, PENDING_RETIRE, dict(item) if isinstance(item, Mapping) else item)
        return True
    name, uid = fields
    configured = uid in set(uid_of.values()) if uid and uid_of is not None else name in names
    if not configured:
        roster_done = item.get("roster_done") is True
        failures, roster_ok = await _retire(config_dir, name, uid, names, retire_persona, roster_done=roster_done)
        if failures:
            # Do not roll back character changes; retain markers for startup reconciliation
            logger.warning("visit retire: visit data of a deleted character not fully retired: %r", failures[0])
            if roster_ok and not roster_done:
                # Name-based roster cleanup completed: record this so retries never clear by name again, since the name may later
                # belong to a new character through cloud import bypassing occupancy checks; preserve its new entries
                await replace_roster_marker(config_dir, PENDING_RETIRE, dict(item), {**item, "roster_done": True})
            return False
    await remove_roster_marker_item(config_dir, PENDING_RETIRE, dict(item))
    return True


async def replay_retires(
    config_dir: str | Path, load_names: LoadNames, *, retire_persona: RetirePersona | None,
    config_lock: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
) -> bool:
    """Startup recovery: settle every ``pending_retire`` item; True when none is left.

    Each item is decided and retired under ``config_lock`` (the
    character-config mutation lock), so it never races a delete whose
    marker is written but whose config is not committed yet.
    """
    config_dir = Path(config_dir)
    try:
        marker = await read_roster_marker(config_dir, PENDING_RETIRE)
    except RosterCorruptError as exc:
        logger.error("visit recovery: roster unreadable, retirements not replayed: %s", exc)
        return False
    if marker is None:
        return True
    if not isinstance(marker, list):
        memory_bridge.diag("pending_retire_malformed")
        logger.error("visit recovery: malformed pending_retire %r dropped", marker)
        return await clear_roster_marker(config_dir, PENDING_RETIRE, marker)
    clean = True
    for item in marker:
        async with (config_lock() if config_lock is not None else contextlib.nullcontext()):
            # Reread markers under lock: while waiting, another flow may have retired this entry and created a new same-named character;
            # retiring from an old snapshot would delete that new character's roster
            try:
                current = await read_roster_marker(config_dir, PENDING_RETIRE)
            except RosterCorruptError:
                clean = False
                continue
            if not isinstance(current, list) or item not in current:
                continue
            # Reread configuration under lock; deletion may just have committed or rolled back while waiting
            names, uid_of = await load_names()
            try:
                clean = await settle_retire(config_dir, item, names=names, uid_of=uid_of,
                                            retire_persona=retire_persona) and clean
            except (OSError, RosterCorruptError) as exc:
                # One failed write (roster_done/marker removal disk error) must not abort all replay; leave it for the next attempt
                logger.error("visit recovery: retirement of %r not settled: %r", item, exc)
                clean = False
    return clean


NAME_FREE = "free"
NAME_RETIRING = "retiring"
NAME_RENAMING = "renaming"
NAME_UNREADABLE = "unreadable"


async def name_marker_state(config_dir: str | Path, name: str) -> str:
    """Whether a pending marker still holds ``name``, as one of the ``NAME_*`` states.

    ``NAME_RETIRING``: a deleted character of that name is being retired.
    ``NAME_RENAMING``: it is the old or the new name of an unreconciled
    rename (the roster entries under it are not settled yet).
    ``NAME_UNREADABLE``: the roster cannot be read, so nothing is known;
    callers creating characters let it pass: the retirement cannot run either
    until the file is readable again.
    """
    try:
        # Read all three markers together: separate reads can synthesize nonexistent state, such as recovery moving queue head into the slot after the slot read,
        # then reading an empty queue and mistaking its reserved name for free
        retiring, renaming, queued = await read_roster_markers(
            config_dir, PENDING_RETIRE, PENDING_RENAME, PENDING_RENAME_QUEUE)
        queued = _queued_renames(queued)
    except RosterCorruptError as exc:
        logger.warning("visit lifecycle: roster unreadable, name %r not checked: %s", name, exc)
        return NAME_UNREADABLE
    if isinstance(retiring, list) and any((_item_fields(item) or ("", None))[0] == name for item in retiring):
        return NAME_RETIRING
    if any(name in (rename_marker_names(marker) or ()) for marker in (renaming, *queued)):
        return NAME_RENAMING
    return NAME_FREE


async def names_retiring(config_dir: str | Path, names: Iterable[str]) -> bool:
    """Whether a ``pending_retire`` item still clearing its roster names one of ``names`` (unreadable: True)."""
    wanted = set(names)
    try:
        retiring = await read_roster_marker(config_dir, PENDING_RETIRE)
    except RosterCorruptError:
        return True
    # roster_done retirement entries no longer block rename migration, so exclude them here too
    return isinstance(retiring, list) and any(
        (_item_fields(item) or ("", None))[0] in wanted and item.get("roster_done") is not True
        for item in retiring)


async def is_name_renaming(config_dir: str | Path, name: str) -> bool:
    """Whether a pending or queued rename holds ``name`` (unreadable roster: False)."""
    try:
        marker, queued = await read_roster_markers(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE)
    except RosterCorruptError:
        return False
    return any(name in (rename_marker_names(item) or ()) for item in (marker, *_queued_renames(queued)))


async def is_name_retiring(config_dir: str | Path, name: str) -> bool:
    """Whether a deleted character called ``name`` still has a ``pending_retire`` item (unreadable: False)."""
    return await name_marker_state(config_dir, name) == NAME_RETIRING


async def _handed_over_pending(config_dir: Path, name: str, character_uid: str | None) -> bool:
    """Whether another character's pending rename lists ``name`` as handed over (to this character)."""
    slot, queued = await read_roster_markers(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE)
    for item in (slot, *_queued_renames(queued)):
        uid = item.get("uid") if isinstance(item, dict) else None
        if name in _handed_over_names(item) and not (uid and character_uid and uid == character_uid):
            return True
    return False


async def _settle_handed_over(
    config_dir: Path, name: str, character_uid: str | None, load_names: LoadNames,
) -> bool:
    """Reconcile the renames that handed ``name`` over before it is renamed or deleted.

    A cloudsave import gave ``name`` to this character while another
    character's rename still owns the entries under it: retiring or moving
    them by name would destroy or carry off that character's data. Returns
    True when that rename stays ambiguous for good (the entries must stay
    under ``name``, which the rename keeps holding); raises
    :class:`RenamePendingElsewhere` while it can still settle (unreadable
    spools, waiting for a retirement or for another rename).
    """
    if not await _handed_over_pending(config_dir, name, character_uid):
        return False
    groups: list[frozenset[str]] = []
    try:
        names, uid_of = await load_names()
        unsettled = await reconcile_renames(config_dir, names, uid_of, pending_groups=groups)
    except Exception as exc:  # noqa: BLE001 - reject on incomplete reconciliation; retain markers for startup recovery
        logger.warning("visit lifecycle: rename handing %r over not reconciled: %r", name, exc)
        raise RenamePendingElsewhere("another character's rename still owns this name's visit data") from exc
    if not await _handed_over_pending(config_dir, name, character_uid):
        return False
    held = next((group for group in groups if name in group), None) if isinstance(unsettled, frozenset) else None
    if held is not None and not await names_retiring(config_dir, held):
        # The rename handing over this name is permanently ambiguous and cannot finish; blocking would make the receiving character permanently undeletable
        logger.warning("visit lifecycle: rename handing %r over is ambiguous; its entries stay under the name", name)
        return True
    raise RenamePendingElsewhere("another character's rename still owns this name's visit data")


async def handover_pending(config_dir: str | Path, name: str, character_uid: str | None) -> bool:
    """Whether another character's pending rename still owns the entries under ``name`` (unreadable: False).

    A cloudsave import handed ``name`` over to this character
    (``handed_over``) before startup recovery migrated them. Cheap: one read
    of the roster markers.
    """
    try:
        return await _handed_over_pending(Path(config_dir), name, character_uid)
    except RosterCorruptError:
        return False


async def handover_blocks_visit(
    config_dir: str | Path, name: str, character_uid: str | None, load_names: LoadNames,
) -> bool:
    """Whether this character must not start a visit yet: the entries under ``name`` are not its own yet.

    The rename that handed ``name`` over is reconciled first (under the
    caller's character-config lock). False once it settled; True while its
    marker still owns ``name``, ambiguous included: that ambiguity lifts when
    the character occupying one of its names is deleted, and reconciliation
    then carries every entry under ``name`` off to the renamed character,
    visits this character made meanwhile among them. An unreadable roster
    blocks nothing, like every other name check.
    """
    try:
        # True means ambiguity and still blocks: deleting the other-name occupant resolves it, and reconciliation migrates every entry under this name,
        # including visits written by its interim occupant, back to the original character
        return await _settle_handed_over(Path(config_dir), name, character_uid, load_names)
    except RenamePendingElsewhere:
        return True
    except RosterCorruptError:
        return False


async def _own_rename_pending(config_dir: Path, name: str, character_uid: str | None, *, queued_only: bool) -> bool:
    """Whether a rename of this character is queued (or, unless ``queued_only``, in the slot either)."""
    slot, queued = await read_roster_markers(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE)
    markers = [*_queued_renames(queued)] if queued_only else [slot, *_queued_renames(queued)]
    return any(_rename_names_of(item, name, character_uid) for item in markers)


def _rename_names_of(marker: Any, name: str, character_uid: str | None) -> tuple[str, ...]:
    """The names ``marker`` holds when it is a pending rename of this character, else ``()``."""
    held = rename_marker_names(marker)
    if held is None:
        return ()
    marker_uid = marker.get("uid") if isinstance(marker.get("uid"), str) and marker.get("uid") else None
    involved = marker_uid == character_uid if marker_uid and character_uid else name in held
    return held if involved else ()


def record_removed_characters_sync(
    config_dir: str | Path, removed: Iterable[Mapping[str, Any]], kept_local_names: Iterable[str] = (),
    replaced: Iterable[Mapping[str, Any]] = (),
) -> int:
    """Add a ``pending_retire`` item for each character a cloudsave import is about to remove.

    Called from the import thread right before it commits, with the
    ``{name, character_uid}`` of every local character absent from the
    snapshot and the names of the local characters it keeps. Startup
    recovery then settles the items against the committed config like any
    delete (an import that rolled back only drops them). A removed character
    whose own rename is still pending gets an item for its other name too:
    recovery drops the rename marker of a character that is gone, so the
    entries a failed migration left under that name would otherwise outlive
    it, and a character the snapshot brings under that name would inherit
    them. Only a name another local character already holds is left alone:
    its entries are mixed with that character's and cannot be told apart
    (the same rule as an ordinary delete). ``replaced`` characters (kept by
    name under a new uid: their entries under that name stay with it) get
    items for those other names only. Machines without visit data get
    nothing; returns the number of items added.
    """
    config_dir = Path(config_dir)
    kept = set(kept_local_names)
    try:
        slot, queued = read_roster_markers_sync(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE)
        rename_markers = [slot, *_queued_renames(queued)]
    except RosterCorruptError:
        rename_markers = []  # Unreadable roster also prevents marker writing; let the caller log the failure
    added = 0
    entries = [(entry, True) for entry in removed] + [(entry, False) for entry in replaced]
    for entry, own_name in entries:
        fields = _item_fields(retire_item(entry.get("name"), entry.get("character_uid")))
        if fields is None or not _has_visit_files_sync(config_dir, fields[1]):
            continue
        name, uid = fields
        if not own_name and uid is None:
            continue  # Without uid, its rename marker cannot be identified
        names = list(dict.fromkeys(([name] if own_name else []) + [
            other for marker in rename_markers for other in _rename_names_of(marker, name, uid)
            if other != name and other not in kept
        ]))
        for retiring in names:
            added += bool(add_roster_marker_item_sync(config_dir, PENDING_RETIRE, retire_item(retiring, uid)))
    return added


def record_renamed_characters_sync(config_dir: str | Path, renamed: Iterable[Mapping[str, Any]]) -> int:
    """Write ``pending_rename`` for a character a cloudsave import keeps under another name.

    Called from the import thread right before it commits, with the
    ``{old, new, uid}`` of every local character the snapshot renamed (on
    another device). Startup recovery then reconciles the marker by uid like
    any rename: the roster entries and spools follow the character to its new
    name (or stay, when the import rolled back). The marker has one slot:
    a rename finding it taken by another character waits in
    ``pending_rename_queue`` and takes the slot once it clears; one finding it
    taken by the same character's unfinished rename is left to that one
    (recovery sees the character under a third name and moves both). Machines
    without visit data get nothing; returns the number of renames recorded.
    An old name the snapshot gives to a new character is marked afterwards
    by :func:`record_handed_over_names_sync`.
    """
    config_dir = Path(config_dir)
    written = 0
    for entry in renamed:
        old, new, uid = entry.get("old"), entry.get("new"), entry.get("uid")
        if not all(isinstance(value, str) and value for value in (old, new, uid)) or old == new:
            continue
        if not _has_visit_files_sync(config_dir, uid):
            continue
        marker = {"old": old, "new": new, "uid": uid, "source": CLOUDSAVE_SOURCE}
        handed = _handed_over_names(entry)
        if handed:
            # Chained renaming: old name immediately passes to another local character whose rename is queued later; reconciliation must not count that as reuse
            marker["handed_over"] = sorted(handed)
        ok, existing = set_roster_marker_sync(config_dir, PENDING_RENAME, marker)
        if ok:
            written += 1
        elif isinstance(existing, dict) and existing.get("uid") == uid:
            # This character's preceding rename remains unreconciled; reaching a third name makes reconciliation migrate entries under both older names there.
            # Record the new name in chained and reserve it before reconciliation, or overwrite downloads replace its uid,
            # making reconciliation treat it as deleted and orphan entries under both older names
            chained = list(existing.get("chained")) if isinstance(existing.get("chained"), list) else []
            value = dict(existing)
            if new not in chained and new not in (existing.get("old"), existing.get("new")):
                value["chained"] = [*chained, new]
            # Carry handed_over into existing markers too, when chained rename passes the old name to the next local character
            handed = _handed_over_names(existing) | _handed_over_names(marker)
            if handed:
                value["handed_over"] = sorted(handed)
            if value != existing:
                written += bool(replace_roster_marker_sync(config_dir, PENDING_RENAME, existing, value))
            logger.info("visit rename: cloudsave renamed %r to %r on top of pending %r; recovery chains it",
                        old, new, existing)
        else:
            # One marker slot exists: enqueue remaining renames for sequential startup reconciliation after this entry clears, supporting multi-character imports
            written += bool(add_roster_marker_item_sync(config_dir, PENDING_RENAME_QUEUE, marker))
            logger.info("visit rename: cloudsave renamed %r to %r queued behind pending %r", old, new, existing)
    return written


def record_handed_over_names_sync(
    config_dir: str | Path, fresh_names: Mapping[str, str | None] | Iterable[str],
    removed: Iterable[Mapping[str, Any]] = (),
) -> int:
    """Mark the names a cloudsave import gives to new characters on the pending renames still holding them.

    A pending rename (the slot or a queued one) of a character the import
    keeps still has entries under its two names; when the snapshot hands one
    of them to a character new to this machine, those entries remain the
    renamed character's and the marker records the name as ``handed_over``
    (otherwise the reconciliation would see the name reused and keep the
    entries there, for the new character to inherit). ``fresh_names`` maps
    each such name to the uid of its holder before the import (``None``: no
    local character had it): a name another local character held stays as
    it is, its entries mixed with that character's. Renames of removed
    characters (``{name, character_uid}``; a uid-less one by name) are left
    to their retirement. Returns the markers updated.
    """
    config_dir = Path(config_dir)
    fresh = dict(fresh_names) if isinstance(fresh_names, Mapping) else dict.fromkeys(fresh_names)
    removed = list(removed)
    gone_uids = {entry.get("character_uid") for entry in removed if entry.get("character_uid")}
    gone_names = {entry.get("name") for entry in removed if not entry.get("character_uid")}
    if not fresh or not _may_exist(config_dir / VISIT_PEERS_FILENAME):
        return 0
    updated = 0
    slot, queued = read_roster_markers_sync(config_dir, PENDING_RENAME, PENDING_RENAME_QUEUE)
    queued = _queued_renames(queued)
    for key, marker in [(PENDING_RENAME, slot)] + [(PENDING_RENAME_QUEUE, item) for item in queued]:
        held = rename_marker_names(marker)
        if held is None:
            continue
        marker_uid = marker.get("uid") if isinstance(marker.get("uid"), str) and marker.get("uid") else None
        # Deleted characters' renames belong to their retirement; uid-less markers identify deleted characters by name
        if (marker_uid in gone_uids) if marker_uid else bool(gone_names & set(held)):
            continue
        handed = _handed_over_names(marker)
        # This name belonged to another local character before import; entries are already mixed and cannot all migrate away
        adding = [name for name in held
                  if name in fresh and name not in handed and fresh[name] in (None, marker_uid)]
        if not adding:
            continue
        value = {**marker, "handed_over": sorted(handed | set(adding))}
        updated += bool(replace_roster_marker_sync(config_dir, key, marker, value))
    return updated


def names_of(characters: Any) -> tuple[set[str], dict[str, str]]:
    """``(names, {name: character_uid})`` of a loaded ``characters.json`` document."""
    from utils.config_manager import get_character_uid

    catgirls = characters.get("猫娘") if isinstance(characters, dict) else None
    names: set[str] = set()
    uid_of: dict[str, str] = {}
    for name, data in (catgirls or {}).items():
        if isinstance(name, str) and name:
            names.add(name)
            uid = get_character_uid(data) if isinstance(data, dict) else None
            if uid:
                uid_of[name] = uid
    return names, uid_of

