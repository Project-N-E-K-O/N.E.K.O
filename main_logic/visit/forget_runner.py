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

"""Local forget operations: "forget this person", "forget everyone", and their replay.

Design: ``docs/design/visit-infrastructure.md`` section 3.7.6 item 5 and
section 4.6 ``POST /api/visit/memory/forget | forget_all``.

Persist first, then execute, at two levels:

1. A clearing sentinel (:class:`ClearingSentinels`) records the scope of the
   whole operation before the roster is even read.
2. Inside that scope every pair gets its revocation log (written under the
   pair's :func:`peer_lock`, and the pair's last-visit summary is removed right
   after the log is on disk), and only once every log is written does
   execution start (:func:`run_revocation`, again under the pair's lock).

The sentinel is deleted when every log in its scope is closed. A crash at any
point leaves enough on disk for :func:`replay_forgets` (startup recovery) to
finish the same scope. Only the local visit memory is touched: transcripts
uploaded to Servers, queued reports and the blocklist stay.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncContextManager

from main_logic.visit import memory_bridge
from main_logic.visit.forget import (
    ClearingSentinels,
    ForgetEpochsUnreadable,
    ForgetEpochsUnsynced,
    ForgetStepFailed,
    RevocationLog,
    RevocationLogUnreadable,
    plan_forget_person,
    run_revocation,
    sentinel_covers,
)
from main_logic.visit.memory_commit import ResolveCharName, peer_lock
from main_logic.visit.spool import (
    DEBRIEF_CHOICES,
    SpoolBusy,
    SpoolStateCorrupt,
    SpoolStateError,
    SpoolStateUnreadable,
    VisitSpool,
    STATE_SUFFIX,
)
from main_logic.visit.subjects import (
    PeerRoster,
    RosterCorruptError,
    pending_rename_names,
    read_roster_markers,
)
from memory.scoped_client import ScopedMemoryClient
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

VoidPending = Callable[[dict], Awaitable[None]]
AdmissionLock = Callable[[str], AsyncContextManager[Any]]
IsVisitActive = Callable[[str], bool]
LifecycleGuard = Callable[[list[str]], AsyncContextManager[Any]]
"""``lifecycle_guard(own_char_uids)``: held for a whole clearing so the characters cannot be renamed / deleted."""


class VisitActive(RuntimeError):
    """A character named by the clearing is visiting right now (checked under its admission lock)."""


class CharacterUnresolved(ValueError):
    """A character named by a clearing has no current name (deleted, or its config unreadable)."""


class RenamePending(CharacterUnresolved):
    """A character rename has not been reconciled yet (``visit_peers.json.pending_rename``)."""


async def _refuse_pending_rename(config_dir: str | Path, names: Iterable[str]) -> None:
    # Rename crashed after config changed but before roster migration; expanding the new name finds nothing,
    # making clear falsely succeed without deletion before backfill revives old-name data. Await startup reconciliation.
    # Block only clears involving either rename endpoint; unrelated roster entries must not be blocked
    # by temporary inconsistency. Queued renames from multicharacter cloud snapshots also remain unreconciled,
    # with config already renamed but entries still under old names; include them too.
    slot, queued = await read_roster_markers(config_dir, "pending_rename", "pending_rename_queue")
    if slot is None and queued is None:
        return
    held = pending_rename_names(slot, queued)
    if held is None:
        # Missing/nonempty-string-invalid fields prevent identifying affected names; do not assume unrelatedness and allow clearing.
        # Unhashable values such as lists must not raise TypeError while building sets below and produce HTTP 500.
        raise RenamePending("a malformed rename marker is pending")
    if held & set(names):
        raise RenamePending("a rename of this character is not reconciled yet")

# Clearing this person invalidates all debriefs not yet written into private-chat memory, marking them do-not-record;
# otherwise later diary recording would reinsert the person just requested for deletion.
_VOIDABLE_CHOICES = (None, "ask_later", "generating:diary", "preview:diary")
# Invalidation intentionally ignores debriefs whose writes began or final outcomes already exist.
_UNVOIDABLE_CHOICES = tuple(choice for choice in DEBRIEF_CHOICES if choice not in _VOIDABLE_CHOICES)
_STEP_ERRORS = (
    ForgetStepFailed, ForgetEpochsUnreadable, ForgetEpochsUnsynced, SpoolBusy, SpoolStateUnreadable,
    RosterCorruptError, OSError, ValueError,
)


@dataclass
class ForgetOutcome:
    """Result of one clearing operation: ``done`` once every log closed, ``forgotten`` persons."""

    done: bool
    forgotten: int = 0
    pending_logs: list[str] = field(default_factory=list)


def _names_other_owner(doc: Mapping[str, Any], record: Mapping[str, Any], pairs: set[str]) -> bool:
    """Whether raw identity fields clearly put a visit outside this log (another character, account or pair)."""
    char = doc.get("own_char_uid")
    if isinstance(char, str) and char and char != record["own_char_uid"]:
        return True
    account = doc.get("own_uid")
    if isinstance(account, str) and account and account != record["own_uid"]:
        return True
    pair = doc.get("pair_id")
    return isinstance(pair, str) and bool(pair) and pair not in pairs


async def _unreadable_visit_excluded(
    spool: VisitSpool, record: Mapping[str, Any], pairs: set[str], *, schema_invalid: bool,
) -> bool:
    """Whether a visit whose ``state.json`` cannot be validated is clearly outside this log.

    Same rule as the spool lookup of the wipe step: a schema-invalid state is
    judged by its raw fields (another character / account / pair, or a
    debrief that already left the voidable choices); a state that cannot be
    read at all is judged by the spool header. Anything unclear is not
    excluded (fail closed).
    """
    try:
        doc = await (spool.read_raw_state() if schema_invalid else spool.read_header())
    except (OSError, ValueError):
        return False
    if doc is None:
        return False
    if _names_other_owner(doc, record, pairs):
        return True
    return schema_invalid and doc.get("debrief_choice") in _UNVOIDABLE_CHOICES


def default_void_pending(config_dir: str | Path) -> VoidPending:
    """Return the ``void_pending`` step used by local forgets.

    Voids the not-yet-written debriefs of the forgotten person's visits under
    the log's local character: visits still naming one of the log's pairs, and
    visits whose peer identity was already wiped (``wipe_spool`` runs first,
    and a wiped visit belongs to some forgotten person). Their choice becomes
    ``forget``. Debriefs already committing or failed are left to their own
    retry / abandon flow.

    A ``state.json`` whose content is corrupt (no version can use it) is
    skipped. One that cannot be read (``OSError``) or parses but fails the
    current schema is skipped only when its spool header (resp. its raw
    fields) clearly belongs to another character, account or pair; otherwise
    :class:`SpoolStateUnreadable` is raised after every other visit is
    handled, and the step stays pending for a later replay.
    """

    async def void(record: dict) -> None:
        own_char_uid = record["own_char_uid"]
        pairs = set(record["pair_ids"])
        unreadable: list[str] = []
        for visit_id in await VisitSpool.list_visit_ids(config_dir, (STATE_SUFFIX,)):
            spool = VisitSpool(config_dir, visit_id)
            try:
                state = await spool.read_state()
            except SpoolStateCorrupt as exc:
                # Corrupt content, invalid JSON or nonobjects, is unusable by every flow including debrief;
                # do not let one unrelated corrupt file permanently block all clears. Log and skip.
                logger.warning("visit forget: skipping corrupt state of %s: %r", visit_id, exc)
                continue
            except (OSError, SpoolStateError) as exc:
                # Temporarily unreadable/locked files or parseable states incompatible with the current schema after downgrade
                # may still be useful to other versions/retries. Do not skip as corrupt, which would close the journal and allow
                # users to record the cleared person again after upgrading. Match identity-erasure lookup semantics: skip only when headers/
                # raw fields explicitly belong to someone else; otherwise leave this journal unfinished for retry.
                if await _unreadable_visit_excluded(
                    spool, record, pairs, schema_invalid=isinstance(exc, SpoolStateError),
                ):
                    continue
                logger.warning("visit forget: state of %s unreadable, void step kept: %r", visit_id, exc)
                unreadable.append(visit_id)
                continue
            if state is None or state["own_char_uid"] != own_char_uid:
                continue
            if state["own_uid"] != record["own_uid"]:
                # Visits under other accounts, including identity-erased ones, are unrelated to this clear.
                continue
            # pair_id=None can only result from prior identity erasure because new_state rejects unbound visits;
            # such visits may belong to this person, so invalidate normally.
            if state["pair_id"] is not None and state["pair_id"] not in pairs:
                continue
            # Ignore finalized: startup replays clears before marking crashes, leaving crashed visits unfinalized here.
            # Missing them lets later backfill show chips anyway. Clears occur only without in-flight visits for this character.
            if state["debrief_choice"] not in _VOIDABLE_CHOICES:
                continue
            try:
                await spool.mark_forget()
            except (SpoolStateError, FileNotFoundError):
                continue
        if unreadable:
            raise SpoolStateUnreadable(unreadable)

    return void


async def open_person_log(
    config_dir: str | Path,
    *,
    own_uid: str,
    own_char: str,
    own_char_uid: str,
    peer_uid: str,
    current: tuple[str, str] | None = None,
) -> str:
    """Write (or merge into) the revocation log of one person, then drop their last-visit summary.

    Runs under the pair's :func:`peer_lock`. The summary removal is a local
    roster write and does not need memory_server.
    """
    async with peer_lock(own_char_uid, peer_uid):
        roster = PeerRoster(config_dir, own_uid=own_uid)
        plan = await plan_forget_person(roster, peer_uid, own_char, own_char_uid, current=current)
        rev_id = await RevocationLog(config_dir, own_uid=own_uid).open_plan(plan)
        await roster.clear_last_summary(peer_uid, own_char)
    return rev_id


async def execute_log(
    config_dir: str | Path,
    rev_id: str,
    *,
    own_uid: str,
    own_char: str,
    own_char_uid: str,
    peer_uid: str,
    client: ScopedMemoryClient | None = None,
    void_pending: VoidPending | None = None,
) -> bool:
    """Run (or resume) one revocation log under the pair's lock; False leaves it for replay."""
    log = RevocationLog(config_dir, own_uid=own_uid)
    roster = PeerRoster(config_dir, own_uid=own_uid)

    async def forget_subject(subject: dict) -> bool:
        return await memory_bridge.post_visit_forget(
            own_char, [subject], config_dir=config_dir, client=client,
        )

    async def sync_epochs(subjects: list[dict]) -> None:
        await memory_bridge.sync_forget_epochs(own_char, subjects, config_dir=config_dir, client=client)

    async with peer_lock(own_char_uid, peer_uid):
        try:
            await run_revocation(
                log, rev_id, roster=roster, forget_subject=forget_subject,
                void_pending=void_pending or default_void_pending(config_dir),
                own_char=own_char, sync_epochs=sync_epochs,
            )
        except _STEP_ERRORS as exc:
            logger.warning("visit forget %s not finished, kept for replay: %r", rev_id, exc)
            return False
    return True


async def _open_logs_in_scope(
    config_dir: Path,
    sentinel: Mapping[str, Any],
    *,
    resolve_char_name: ResolveCharName,
    drop_deleted_chars: bool = False,
) -> list[tuple[str, str, str, str]]:
    """Expand a sentinel's scope from the roster and write every log; return ``(rev_id, name, uid, peer)``.

    ``drop_deleted_chars`` (startup replay, the character config verified
    readable): a character without a name was deleted and is skipped (its
    data is retired by uid); otherwise :class:`CharacterUnresolved`.
    """
    own_uid = sentinel["own_uid"]
    roster = PeerRoster(config_dir, own_uid=own_uid)
    opened: list[tuple[str, str, str, str]] = []
    for own_char_uid in sentinel["own_char_uids"]:
        name = await resolve_char_name(own_char_uid)
        if not name:
            if drop_deleted_chars:
                # Character config was read successfully, confirming deletion; deletion retirement handles data by uid.
                # Exclude it from this expansion, clearing other sentinel characters normally and allowing settlement.
                continue
            # Name-resolution failure may be temporary config-read failure replaced with defaults; treat scope as unexpanded
            # and retain the sentinel, rather than assuming nobody needs clearing and permanently deleting clear intent.
            raise CharacterUnresolved(f"character {own_char_uid} of {sentinel['op_id']} has no name")
        if sentinel["scope"] == "person":
            peers = [sentinel["peer_uid"]]
        else:
            # Read strictly: unreadable rosters or malformed person entries do not mean the person is absent,
            # or clear-all would miss them yet remove the sentinel and report completion.
            peers = await roster.peers_of_char(name)
        for peer_uid in peers:
            rev_id = await open_person_log(
                config_dir, own_uid=own_uid, own_char=name, own_char_uid=own_char_uid,
                peer_uid=peer_uid,
            )
            opened.append((rev_id, name, own_char_uid, peer_uid))
    return opened


async def _run_scope(
    config_dir: Path,
    sentinel: Mapping[str, Any],
    *,
    resolve_char_name: ResolveCharName,
    client: ScopedMemoryClient | None,
    void_pending: VoidPending | None,
) -> ForgetOutcome:
    opened = await _open_logs_in_scope(config_dir, sentinel, resolve_char_name=resolve_char_name)
    # Rosters list only remaining people; prior attempts may finish remove_char before later failure, removing them from the roster
    # while journals stay open. Resume existing open journals within scope too, or retry falsely reports completion,
    # deletes the sentinel, and leaves transcript identity/previews until next startup.
    seen = {rev_id for rev_id, *_rest in opened}
    for record in await RevocationLog.list_all_open(config_dir):
        if (
            record["id"] in seen or record["own_uid"] != sentinel["own_uid"]
            or not sentinel_covers(sentinel, record["own_char_uid"], record["peer_uid"])
        ):
            continue
        name = await resolve_char_name(record["own_char_uid"])
        if not name:
            raise CharacterUnresolved(f"character of {record['id']} has no name")
        opened.append((record["id"], name, record["own_char_uid"], record["peer_uid"]))
        seen.add(record["id"])
    if sentinel["scope"] == "chars":
        # Open single-person clear sentinels in scope may name people already absent from the roster, after remove_char
        # but before sentinel deletion, or before expansion. Open journals and process them together;
        # clear untouched data now, idempotently reclear prior work, then remove their sentinels with this operation.
        pairs = {(uid, peer) for *_rest, uid, peer in opened}
        for other in await ClearingSentinels(config_dir).list_open():
            if other["scope"] != "person" or not _sentinel_within(sentinel, other):
                continue
            for own_char_uid in other["own_char_uids"]:
                if (own_char_uid, other["peer_uid"]) in pairs:
                    continue
                name = await resolve_char_name(own_char_uid)
                if not name:
                    raise CharacterUnresolved(f"character {own_char_uid} of {other['op_id']} has no name")
                rev_id = await open_person_log(
                    config_dir, own_uid=sentinel["own_uid"], own_char=name, own_char_uid=own_char_uid,
                    peer_uid=other["peer_uid"],
                )
                opened.append((rev_id, name, own_char_uid, other["peer_uid"]))
                pairs.add((own_char_uid, other["peer_uid"]))
    # Persist all journals before processing pairs so crashes leave replayable journals even for people not reached yet.
    pending: list[str] = []
    for rev_id, name, own_char_uid, peer_uid in opened:
        ok = await execute_log(
            config_dir, rev_id, own_uid=sentinel["own_uid"], own_char=name,
            own_char_uid=own_char_uid, peer_uid=peer_uid, client=client, void_pending=void_pending,
        )
        if not ok:
            pending.append(rev_id)
    # Clear-all can have multiple local-character journals for one person. Count per person,
    # considering them cleared only when all their journals complete.
    persons = {peer for *_rest, peer in opened}
    unfinished = {peer for rev_id, *_rest, peer in opened if rev_id in pending}
    if pending:
        return ForgetOutcome(done=False, forgotten=len(persons - unfinished), pending_logs=pending)
    store = ClearingSentinels(config_dir)
    await store.remove(sentinel["op_id"])
    await _remove_covered_sentinels(
        config_dir, store, sentinel, executed={(uid, peer) for _rev, _name, uid, peer in opened},
    )
    return ForgetOutcome(done=True, forgotten=len(persons))


def _sentinel_within(outer: Mapping[str, Any], inner: Mapping[str, Any]) -> bool:
    """Whether ``inner``'s whole scope lies inside ``outer``'s (same account)."""
    if inner["own_uid"] != outer["own_uid"] or not set(inner["own_char_uids"]) <= set(outer["own_char_uids"]):
        return False
    return outer["scope"] == "chars" or (
        inner["scope"] == "person" and inner.get("peer_uid") == outer.get("peer_uid")
    )


async def _remove_covered_sentinels(
    config_dir: Path, store: ClearingSentinels, done: Mapping[str, Any],
    *, executed: set[tuple[str, str]],
) -> None:
    # Old sentinels from prior attempts may fall within the now-completed scope, e.g. new characters changed the
    # character set and prevented reuse. Their journals completed here too; retaining them would block admission/memory
    # assembly until next startup. Remove best-effort, leaving unreadable ones for startup replay.
    try:
        others = await store.list_open()
        remaining = await RevocationLog.list_all_open(config_dir)
    except RevocationLogUnreadable:
        return
    for other in others:
        if other["op_id"] == done["op_id"] or not _sentinel_within(done, other):
            continue
        if other["scope"] == "person" and not all(
            (uid, other.get("peer_uid")) in executed for uid in other["own_char_uids"]
        ):
            # A single-person sentinel absent from this roster expansion was not cleared here; their participant memories can be
            # inferred without a roster and need that sentinel's own execution. Do not delete it opportunistically.
            continue
        if any(
            log["own_uid"] == other["own_uid"]
            and sentinel_covers(other, log["own_char_uid"], log["peer_uid"])
            for log in remaining
        ):
            continue
        await store.remove(other["op_id"])


async def _with_admission_locks(
    admission_lock: AdmissionLock | None, own_char_uids: Iterable[str],
) -> contextlib.AsyncExitStack:
    stack = contextlib.AsyncExitStack()
    if admission_lock is not None:
        try:
            for uid in sorted(set(own_char_uids)):
                await stack.enter_async_context(admission_lock(uid))
        except BaseException:
            # On partial acquisition failure/cancellation, release acquired locks or these characters can never visit again.
            await stack.aclose()
            raise
    return stack


def _refuse_active(is_visit_active: IsVisitActive | None, names: Iterable[str]) -> None:
    # Recheck under admission locks, blocking visits started after the unlocked check but before sentinel persistence.
    if is_visit_active is not None and any(is_visit_active(name) for name in names):
        raise VisitActive("a character in scope is visiting")


async def forget_person(
    config_dir: str | Path,
    *,
    own_uid: str,
    own_char: str,
    own_char_uid: str,
    peer_uid: str,
    client: ScopedMemoryClient | None = None,
    void_pending: VoidPending | None = None,
    admission_lock: AdmissionLock | None = None,
    is_visit_active: IsVisitActive | None = None,
    lifecycle_guard: LifecycleGuard | None = None,
    resolve_char_name: ResolveCharName | None = None,
) -> ForgetOutcome:
    """"Forget this person" under one local character (``scope='person'``).

    ``admission_lock(own_char_uid)`` (optional, the visit admission lock of a
    character) is held only while the sentinel is written, so a visit admitted
    before it is visible either already exists or sees the sentinel.
    ``is_visit_active(name)`` is checked again under that lock and raises
    :class:`VisitActive` before anything is written. ``lifecycle_guard``
    (optional) is held for the whole operation, so the character cannot be
    renamed or deleted while its name is used for the roster and memory_server.
    ``resolve_char_name`` (optional) re-reads the character's current name
    once the guard is held (a rename may have landed before it was taken);
    :class:`CharacterUnresolved` when the character is gone.
    """
    async with (lifecycle_guard([own_char_uid]) if lifecycle_guard else contextlib.nullcontext()):
        if resolve_char_name is not None:
            own_char = await resolve_char_name(own_char_uid)
            if not own_char:
                raise CharacterUnresolved(f"character {own_char_uid} has no name")
        await _refuse_pending_rename(config_dir, [own_char])
        return await _forget_person(
            Path(config_dir), own_uid=own_uid, own_char=own_char, own_char_uid=own_char_uid,
            peer_uid=peer_uid, client=client, void_pending=void_pending,
            admission_lock=admission_lock, is_visit_active=is_visit_active,
        )


async def _forget_person(
    config_dir: Path,
    *,
    own_uid: str,
    own_char: str,
    own_char_uid: str,
    peer_uid: str,
    client: ScopedMemoryClient | None,
    void_pending: VoidPending | None,
    admission_lock: AdmissionLock | None,
    is_visit_active: IsVisitActive | None,
) -> ForgetOutcome:
    stack = await _with_admission_locks(admission_lock, [own_char_uid])
    async with stack:
        _refuse_active(is_visit_active, [own_char])
        sentinel = await ClearingSentinels(config_dir).find_or_create(
            own_uid=own_uid, scope="person", own_char_uids=[own_char_uid], peer_uid=peer_uid,
        )

    async def resolve(uid: str) -> str | None:
        return own_char if uid == own_char_uid else None

    return await _run_scope(config_dir, sentinel, resolve_char_name=resolve,
                            client=client, void_pending=void_pending)


async def forget_all(
    config_dir: str | Path,
    *,
    own_uid: str,
    chars: Mapping[str, str],
    client: ScopedMemoryClient | None = None,
    void_pending: VoidPending | None = None,
    admission_lock: AdmissionLock | None = None,
    is_visit_active: IsVisitActive | None = None,
    lifecycle_guard: LifecycleGuard | None = None,
    resolve_char_name: ResolveCharName | None = None,
) -> ForgetOutcome:
    """"Forget everyone" under the local characters ``chars`` (``{name: character_uid}``).

    One sentinel (``scope='chars'``) names every character; the roster is
    expanded only after it is on disk, every person's log is written before
    any is executed. ``lifecycle_guard`` is held for the whole operation;
    ``resolve_char_name`` (optional) re-reads every current name under it and
    raises :class:`CharacterUnresolved` when any of them has none (deleted in
    between, or the character config unreadable: indistinguishable here, so
    the caller answers a retryable error instead of reporting success).
    """
    if not chars:
        return ForgetOutcome(done=True)
    async with (lifecycle_guard(sorted(chars.values())) if lifecycle_guard
                else contextlib.nullcontext()):
        if resolve_char_name is not None:
            current: dict[str, str] = {}
            for uid in chars.values():
                name = await resolve_char_name(uid)
                if not name:
                    # Unresolvable character: possibly deleted or config temporarily unreadable/replaced by defaults.
                    # These cannot be distinguished here; skipping falsely reports clear success without any sentinel/journal for that character.
                    raise CharacterUnresolved(f"character {uid} has no name")
                current[name] = uid
            chars = current
        await _refuse_pending_rename(config_dir, chars)
        return await _forget_all(
            Path(config_dir), own_uid=own_uid, chars=chars, client=client,
            void_pending=void_pending, admission_lock=admission_lock,
            is_visit_active=is_visit_active,
        )


async def _forget_all(
    config_dir: Path,
    *,
    own_uid: str,
    chars: Mapping[str, str],
    client: ScopedMemoryClient | None,
    void_pending: VoidPending | None,
    admission_lock: AdmissionLock | None,
    is_visit_active: IsVisitActive | None,
) -> ForgetOutcome:
    by_uid = {uid: name for name, uid in chars.items()}
    stack = await _with_admission_locks(admission_lock, by_uid)
    async with stack:
        _refuse_active(is_visit_active, chars)
        sentinel = await ClearingSentinels(config_dir).find_or_create(
            own_uid=own_uid, scope="chars", own_char_uids=list(by_uid),
        )

    async def resolve(uid: str) -> str | None:
        return by_uid.get(uid)

    return await _run_scope(config_dir, sentinel, resolve_char_name=resolve,
                            client=client, void_pending=void_pending)


async def replay_forgets(
    config_dir: str | Path,
    *,
    resolve_char_name: ResolveCharName,
    client: ScopedMemoryClient | None = None,
    void_pending: VoidPending | None = None,
    lifecycle_guard: LifecycleGuard | None = None,
    drop_deleted_chars: bool = False,
) -> bool:
    """Finish every unfinished clearing operation (startup recovery); True when nothing is left.

    ``drop_deleted_chars``: the caller verified the character config is
    readable, so a character uid without a name was deleted. Its part of a
    sentinel is skipped and its open logs are kept as they are for the
    deletion's retirement to reconcile; they no longer keep the sentinel
    (and the other characters it names) open.

    ``lifecycle_guard`` (optional, the one the clearing endpoints use) is held
    around each sentinel expansion and each log replay, from resolving the
    character name by uid until the replay ends.

    Leftover sentinels are re-expanded within their own scope first (covers a
    crash before their logs were written), then every open revocation log of
    every account is resumed from its ``done_steps``, and finally each
    sentinel whose scope has no open log left is deleted. Unreadable logs or
    sentinels stay (fail closed) and are reported as unfinished.
    """
    config_dir = Path(config_dir)
    sentinels_store = ClearingSentinels(config_dir)
    try:
        sentinels = await sentinels_store.list_open()
    except RevocationLogUnreadable as exc:
        logger.error("visit forget replay: unreadable clearing sentinels %s", exc.ids)
        sentinels = []
        clean = False
    else:
        clean = True
    unexpanded: set[str] = set()
    def guarded(uids: list[str]):
        return lifecycle_guard(sorted(uids)) if lifecycle_guard else contextlib.nullcontext()

    for sentinel in sentinels:
        try:
            async with guarded(sentinel["own_char_uids"]):
                # Backfill reconciles renames initially, but another rename may crash before guard acquisition; recheck inside the guard.
                names = [n for uid in sentinel["own_char_uids"] if (n := await resolve_char_name(uid))]
                await _refuse_pending_rename(config_dir, names)
                await _open_logs_in_scope(config_dir, sentinel, resolve_char_name=resolve_char_name,
                                          drop_deleted_chars=drop_deleted_chars)
        except _STEP_ERRORS as exc:
            logger.warning("visit forget replay: cannot expand %s: %r", sentinel["op_id"], exc)
            # Failed scope expansion, e.g. unreadable roster: retain the sentinel as this clear's only record until next time.
            unexpanded.add(sentinel["op_id"])
            clean = False
    logs, unreadable_logs = await RevocationLog.list_all_open_with_unreadable(config_dir)
    if unreadable_logs:
        # Retain unreadable journals as unfinished; admission/memory assembly blocks only their filename-identified pair. Replay readable
        # journals normally so one corrupt file cannot indefinitely block every clear.
        logger.error("visit forget replay: unreadable revocation logs %s", unreadable_logs)
        clean = False
    for record in logs:
        # Hold the endpoint's lifecycle guard from uid-to-name resolution through replay, preventing
        # renames/deletions from sending clears to vacated old names and falsely settling journals.
        async with guarded([record["own_char_uid"]]):
            name = await resolve_char_name(record["own_char_uid"])
            if not name:
                # Deleted characters or temporarily unreadable configs prevent execution; retain the journal for retirement reconciliation,
                # preserving clear intent against residual roster entries or transcript peer identities.
                # With drop_deleted_chars, the sentinel recheck below stops counting them as still clearing;
                # other characters sharing the sentinel settle normally.
                logger.warning("visit forget replay: character of %s has no name, kept", record["id"])
                clean = False
                continue
            try:
                await _refuse_pending_rename(config_dir, [name])
            except (RenamePending, RosterCorruptError) as exc:
                # Unreconciled rename: replay under the new name would settle an empty entry's journal. Defer until next startup.
                logger.warning("visit forget replay: %s deferred: %r", record["id"], exc)
                clean = False
                continue
            ok = await execute_log(
                config_dir, record["id"], own_uid=record["own_uid"], own_char=name,
                own_char_uid=record["own_char_uid"], peer_uid=record["peer_uid"],
                client=client, void_pending=void_pending,
            )
        clean = clean and ok
    for sentinel in sentinels:
        if sentinel["op_id"] in unexpanded:
            clean = False
            continue
        # Guard the final recheck/deletion with the sentinel's lock too; otherwise an endpoint could reuse it and
        # begin expanding new journals while this code deletes it from an old snapshot, hiding active clearing from admission.
        async with guarded(sentinel["own_char_uids"]):
            try:
                remaining = await RevocationLog.list_all_open(config_dir)
            except RevocationLogUnreadable:
                return False
            busy = False
            for log in remaining:
                if log["own_uid"] != sentinel["own_uid"] or not sentinel_covers(
                    sentinel, log["own_char_uid"], log["peer_uid"],
                ):
                    continue
                if drop_deleted_chars and not await resolve_char_name(log["own_char_uid"]):
                    # Retired-character journals await retirement reconciliation without blocking this sentinel's settlement.
                    continue
                busy = True
                break
            if busy:
                clean = False
            else:
                await sentinels_store.remove(sentinel["op_id"])
    return clean
