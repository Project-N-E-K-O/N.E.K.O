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
"""Forget-fencing and staged-write cancellation helpers for keyed scoped history."""

import copy
import os
from utils.cloudsave_runtime import MaintenanceModeError

from . import routes as _routes

async def _fenced_segments(lanlan_name: str, staging: dict) -> set[int]:
    """Segment indexes a forget fenced off: dropped items, a segment drop mark, or a newer tombstone."""
    from . import idempotency

    segments = staging.get("segments") or []
    fenced = {
        index for index, segment in enumerate(segments)
        if isinstance(segment, dict) and segment.get(_routes._SEGMENT_DROPPED_BY_FORGET) is True
    }
    dropped_seqs = {
        entry.get("seq") for entry in staging.get("applied") or []
        if isinstance(entry, dict) and any(entry.get(name) is True for name in _routes._KEYED_DROP_MARKERS)
    }
    fenced |= {
        item.get("segment") for item in staging.get("items") or []
        if isinstance(item, dict) and item.get("seq") in dropped_seqs
    }
    try:
        tombstones = await idempotency.read_tombstones(lanlan_name)
        epochs = staging.get("epochs") or {}
        for index, segment in enumerate(segments):
            if not isinstance(segment, dict):
                continue
            # Activity-only segments with no extracted entries never checked tombstones; check here.
            tombstone_epoch = idempotency.tombstone_epoch(tombstones, segment.get("tombstone_keys") or [])
            if tombstone_epoch is not None and int(epochs.get(segment.get("wire_key"), 0)) < tombstone_epoch:
                fenced.add(index)
    except idempotency.IdempotencyStateError:
        # Unreadable tombstones prevent identifying blocked segments. Do not drop all activity and finalize (done retries return
        # duplicate forever, losing trust writes); raise so caller returns 503 and retries once tombstones are readable.
        raise
    return fenced


def _without_trust_mutation(state: dict) -> dict:
    source = state.get("trust_source") or {}
    return {
        **state,
        "trust_signal_events": (),
        "trust_activity_events": (),
        "trust_source": {**source, "channel": None},
    }


def _mark_items_forgotten_during_generation(
    lanlan_name: str, staging: dict, contexts: list[dict], generations: dict[int, int] | None,
) -> set[int]:
    """Journal as dropped every item of a subject whose forget generation moved.

    Returns the indexes of the segments this call journaled something new for
    (dropped items or a fresh segment mark), else an empty set, so a caller
    can persist exactly those as a fallback; a segment fenced by an earlier
    recheck is not reported again.

    A subject whose forget is still running (between ``abegin`` and
    ``aend``) counts as moved too: a baseline captured inside that window
    equals the current generation, yet the fact store already refuses the
    segment's writes.
    """
    if not generations:
        return set()
    fact_store = _routes.runtime.fact_store
    changed = {
        index for index, subject in enumerate(_routes._staged_segment_subjects(staging, contexts))
        if index in generations
        and (
            fact_store._subject_forget_generation(lanlan_name, subject) != generations[index]
            or fact_store._subject_forget_is_active(lanlan_name, subject)
        )
    }
    if not changed:
        return set()
    applied = staging.setdefault("applied", [])
    done = {entry.get("seq") for entry in applied if isinstance(entry, dict)}
    added = False
    touched: set[int] = set()
    items = staging.get("items") or []
    for position, item in enumerate(items):
        if (
            item.get("segment") in changed
            and item.get("kind") != _routes._KEYED_ITEM_LOCALE
            and item["seq"] not in done
        ):
            applied.append({"seq": item["seq"], "dropped_forget_during_generation": True})
            # Erase cleared subjects' extraction source/display names too, leaving none in staging if later interrupted.
            items[position] = _stripped_item(item)
            added = True
            touched.add(item.get("segment"))
    segments = staging.get("segments")
    if isinstance(segments, list):
        newly = {
            index for index in changed
            if index < len(segments) and isinstance(segments[index], dict)
            and segments[index].get(_routes._SEGMENT_DROPPED_BY_FORGET) is not True
        }
        _mark_segments_dropped(segments, newly)
        touched |= newly
        # Persist marks even for language-only segments (language items never record discard), or segment marks remain memory-only.
        added = added or bool(newly)
    # Report only newly marked segments; do not reinclude prior blocked ones in fallback (valid segments may use their wire keys).
    return touched if added else set()


def _fingerprint_of_staging(document: dict) -> dict | None:
    segments = document.get("segments")
    if not isinstance(segments, list) or not isinstance(document.get("shape"), str):
        return None
    return {
        "shape": document["shape"],
        "wire_keys": [seg.get("wire_key") for seg in segments if isinstance(seg, dict)],
        "content_hash": document.get("request_hash"),
    }


async def _drop_leftover_staging(lanlan_name: str, key: str) -> None:
    from . import idempotency

    try:
        await idempotency.delete_staging(lanlan_name, key)
    except Exception as exc:  # noqa: BLE001 - Space reclamation only: leave deletion failures to startup cleanup; duplicate response is unaffected.
        _routes.logger.warning(f"[scoped_history] {lanlan_name}: 终态键的残留暂存删不掉，留给启动清理: {exc}")


def _staging_identity(document: dict) -> dict:
    """The request identity a cancelled record of this staging gets (never empty).

    A staging whose own identity fields are damaged gets a placeholder that
    no real request matches: a same-key retry is refused with 422 instead of
    answering 503 forever on a terminal record without an identity.
    """
    fingerprint = _fingerprint_of_staging(document)
    if fingerprint is not None:
        return fingerprint
    return {"shape": None, "wire_keys": sorted(_staged_subject_keys(document)), "content_hash": None}


def _forget_fences(
    request_subject_key: str | None, forget_epoch: int | None, fanout_fences: dict[str, int] | None,
) -> dict[str, int]:
    """``{wire key: forget epoch}`` a staged write's own epochs can be compared against."""
    fences = dict(fanout_fences or {})
    if request_subject_key is not None and forget_epoch is not None:
        fences[request_subject_key] = forget_epoch
    return fences


def _wire_after_forget(wire_key: object, epochs: object, fences: dict[str, int]) -> bool:
    """Whether a write stamped for ``wire_key`` was started after that key's forget fence."""
    if not isinstance(wire_key, str) or wire_key not in fences or not isinstance(epochs, dict):
        return False
    epoch = epochs.get(wire_key)
    return isinstance(epoch, int) and not isinstance(epoch, bool) and epoch >= fences[wire_key]


def _staged_after_forget(document: dict, subject_keys: set[str], fences: dict[str, int]) -> bool:
    """Whether a staged write was started knowing this forget (so it must not be cancelled).

    Decided per segment, each against the fence of its OWN wire key (the same
    rule that lets the segment pass the tombstone): every segment touching a
    forgotten subject must carry an epoch at or above that wire key's fence.
    ``fences`` holds the request subject's epoch plus, when the caller sent
    them, each fan-out target's own epoch; a wire key without a fence (a
    fan-out target nobody gave an epoch for, or an unrelated account routed
    into the erased subject) has no comparable epoch and counts as before.
    """
    if not fences:
        return False
    epochs = document.get("epochs")
    # Decide per segment: each involving a cleared subject compares its own wire-key fence (routed subject is only
    # that wire's destination; sufficiently new wire generation makes it a valid post-clear write). No fence means incomparable,
    # so treat as pre-clear.
    segments = document.get("segments")
    if isinstance(segments, list) and segments:
        touching = [segment for segment in segments if _segment_subject_keys(segment) & subject_keys]
        return bool(touching) and all(
            isinstance(segment, dict) and _wire_after_forget(segment.get("wire_key"), epochs, fences)
            for segment in touching
        )
    wires = document.get("wire_keys")
    if not isinstance(wires, list):
        return False
    touching_wires = [str(wire) for wire in wires if str(wire) in subject_keys]
    return bool(touching_wires) and all(_wire_after_forget(wire, epochs, fences) for wire in touching_wires)


def _stale_record_marks(record: dict, subject_keys: set[str], fences: dict[str, int]) -> set[str]:
    """Keys to journal as ``forgotten_keys`` on a pending record that has no staging yet.

    Position by position (the record keeps the wire keys and, aligned with
    them, ``routed_positions``): a position touching a forgotten subject
    through its wire or its routed destination is marked by its WIRE key,
    unless that wire was started after its own fence. The retry drops every
    segment whose wire OR routed destination is a marked key, so marking the
    wire (not the shared destination) spares a fresh wire routed to the same
    forgotten subject; a fresh wire routed onto a stale wire's own key is
    still dropped (over-dropping, never under-erasing). A record without
    aligned positions (older records) also marks the forgotten routed keys,
    as before.
    """
    request = record.get("request")
    raw_wires = request.get("wire_keys") if isinstance(request, dict) else None
    wires = [str(wire) for wire in raw_wires] if isinstance(raw_wires, list) else []
    epochs = record.get("epochs") or {}
    positions = record.get("routed_positions")
    aligned = (
        isinstance(positions, list) and len(positions) == len(wires)
        and all(isinstance(position, str) and position for position in positions)
    )
    marks: set[str] = set()
    for index, wire in enumerate(wires):
        routed = str(positions[index]) if aligned else None
        if wire not in subject_keys and routed not in subject_keys:
            continue
        if _wire_after_forget(wire, epochs, fences):
            continue
        marks.add(wire)
    if not aligned:
        routed_keys = record.get("routed_keys")
        if isinstance(routed_keys, list):
            marks |= subject_keys.intersection(str(k) for k in routed_keys)
    return marks


def _staged_subject_keys(document: dict) -> set[str]:
    """Every subject key a staged journal touches: wire keys plus each segment's routed subject."""
    staged = document.get("subjects")
    keys = {str(s) for s in staged} if isinstance(staged, list) else set()
    segments = document.get("segments")
    for segment in segments if isinstance(segments, list) else []:
        keys |= _segment_subject_keys(segment)
    return keys


def _segment_subject_keys(segment: object) -> set[str]:
    if not isinstance(segment, dict):
        return set()
    keys = {str(segment.get("wire_key"))}
    subject = segment.get("subject")
    if isinstance(subject, dict) and subject.get("subject_kind") and subject.get("subject_id"):
        keys.add(f"{subject['subject_kind']}:{subject['subject_id']}")
    return keys


def _items_shape_valid(items: list, segments: list) -> bool:
    """Items numbered ``seq == position`` with a known kind and an in-range segment; every segment an object."""
    if not all(isinstance(segment, dict) for segment in segments):
        return False
    for position, item in enumerate(items):
        seq, segment = item.get("seq"), item.get("segment")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq != position:
            return False
        if not isinstance(segment, int) or isinstance(segment, bool) or not 0 <= segment < len(segments):
            return False
        if item.get("kind") not in (_routes._KEYED_ITEM_LOCALE, _routes._KEYED_ITEM_FACTS, _routes._KEYED_ITEM_DISPLAY_NAME):
            return False
    return True


def _applied_seqs_valid(applied: list, item_count: int) -> bool:
    """Every applied entry is an object whose ``seq`` is a distinct in-range integer (as replay requires)."""
    seqs = [entry.get("seq") if isinstance(entry, dict) else None for entry in applied]
    return all(
        isinstance(seq, int) and not isinstance(seq, bool) and 0 <= seq < item_count for seq in seqs
    ) and len(set(seqs)) == len(seqs)


def _drop_forgotten_segments(
    document: dict, subject_keys: set[str], fences: dict[str, int] | None = None,
) -> bool:
    """Drop only the forgotten segments of a partly affected multi-segment journal.

    A segment is affected when it touches ``subject_keys`` and was not started
    after its own wire key's fence (``fences``, see :func:`_staged_after_forget`).
    Returns False (nothing changed) when no segment or every segment is
    affected: the whole key is cancelled then. Otherwise every not-yet-applied
    item of an affected segment is journaled as ``dropped_forget`` and every
    item of such a segment loses its extracted content, so a retry applies
    only the remaining segments.
    """
    segments = document.get("segments")
    if not isinstance(segments, list) or not segments:
        return False
    epochs = document.get("epochs")
    affected = {
        index for index, segment in enumerate(segments)
        if _segment_subject_keys(segment) & subject_keys
        and not (
            isinstance(segment, dict)
            and _wire_after_forget(segment.get("wire_key"), epochs, fences or {})
        )
    }
    if not affected or len(affected) == len(segments):
        return False
    # Inspect raw values before defaults: only missing fields mean empty lists. {} / "" / null are corrupt journals;
    # accepting through `or []` or None-as-empty leaves corrupt values in rewritten copies, making clear return 500.
    applied = document["applied"] if "applied" in document else []
    items = document["items"] if "items" in document else []
    if (
        not isinstance(applied, list) or not isinstance(items, list)
        or not all(isinstance(item, dict) for item in items)
        or not _applied_seqs_valid(applied, len(items))
        or not _items_shape_valid(items, segments)
    ):
        # Corrupt journal structure (non-list applied, noniterable items, etc.) prevents selective segment discard; cancel whole key.
        # Corrupt auxiliary state must not make every privacy clear return 500 without erasing a row.
        return False
    # Rewrite a copy, then validate: corrupt cleared payload is fine because erased; retain journals only if remaining
    # segments can replay per-item on retry. Otherwise (scalar effect keys, etc.) cancel whole key to avoid permanent 503 for uncleared segments.
    rewritten = copy.deepcopy(document)
    _rewrite_forgotten_segments(rewritten, affected)
    if not _routes._keyed_staging_items_valid(rewritten, structural_only=True):
        return False
    document.clear()
    document.update(rewritten)
    return True


def _rewrite_forgotten_segments(document: dict, affected: set[int]) -> None:
    segments = document["segments"]
    applied = document.setdefault("applied", [])
    items = document.get("items") or []
    forgotten_seqs = {
        item.get("seq") for item in items
        if isinstance(item, dict) and item.get("segment") in affected
        and item.get("kind") != _routes._KEYED_ITEM_LOCALE
    }
    # Applied results for cleared segments (fact_ids/identities) refer to erased rows: replace with discard records,
    # so retry responses no longer report nonexistent facts.
    applied[:] = [
        {"seq": entry.get("seq"), "dropped_forget": True}
        if isinstance(entry, dict) and entry.get("seq") in forgotten_seqs else entry
        for entry in applied
    ]
    done = {entry.get("seq") for entry in applied if isinstance(entry, dict)}
    for position, item in enumerate(items):
        if (
            not isinstance(item, dict) or item.get("segment") not in affected
            or item.get("kind") == _routes._KEYED_ITEM_LOCALE
        ):
            # Retain language-order items as when cleared during generation (no cleared-subject content).
            continue
        if item.get("seq") not in done:
            applied.append({"seq": item.get("seq"), "dropped_forget": True})
        # Cleared subjects' extraction source / display names must not remain on disk.
        items[position] = _stripped_item(item)
    _mark_segments_dropped(segments, affected)


def _drop_segments_for_keys(document: dict, subject_keys: set[str]) -> None:
    """Journal every unapplied item of segments touching ``subject_keys`` as dropped, stripped.

    Locale items included: replaying a persisted ``forgotten_keys`` marker, the
    locale order was reserved after the forget and no longer proves the
    request predates it, so applying it would recreate erased locale state.
    """
    segments = document.get("segments") or []
    affected = {
        index for index, segment in enumerate(segments)
        if _segment_subject_keys(segment) & subject_keys
    }
    if not affected:
        return
    applied = document.setdefault("applied", [])
    items = document.get("items")
    items = [] if items is None else items
    if not isinstance(applied, list) or not isinstance(items, list):
        raise ValueError("staging journal is malformed")
    done = {entry.get("seq") for entry in applied if isinstance(entry, dict)}
    for position, item in enumerate(items):
        if not isinstance(item, dict) or item.get("segment") not in affected:
            continue
        if item.get("seq") not in done:
            applied.append({"seq": item.get("seq"), "dropped_forget": True})
        if item.get("kind") != _routes._KEYED_ITEM_LOCALE:
            items[position] = _stripped_item(item)
    _mark_segments_dropped(segments, affected)


def _forgotten_keys_of(record) -> set[str]:
    """The ``forgotten_keys`` marker of a key record (empty when absent).

    It is the only durable evidence that a forget hit a key while it was
    generating; a present but malformed value raises ``IdempotencyStateError``
    instead of reading as "nothing forgotten", which would regenerate (and
    write back) the forgotten subject's history.
    """
    from . import idempotency

    if not isinstance(record, dict) or "forgotten_keys" not in record:
        return set()
    value = record["forgotten_keys"]
    if not isinstance(value, list) or not all(isinstance(key, str) and key for key in value):
        raise idempotency.IdempotencyStateError("forgotten_keys marker is malformed")
    return set(value)


def _mark_segments_dropped(segments: list, indexes: set[int]) -> None:
    # Mark segments cleared/discarded: language-only segments cannot be identified from items, which never mark discard;
    # recovery name restoration would otherwise apply pre-clear key metadata to subsequently recreated sections.
    for index in indexes:
        if isinstance(segments[index], dict):
            segments[index][_routes._SEGMENT_DROPPED_BY_FORGET] = True


def _stripped_item(item: dict) -> dict:
    # Keep only sequence numbers needed for responses/skipping and effect keys (content-free; item validation derives sequences from them).
    stripped = {"seq": item.get("seq"), "kind": item.get("kind"), "segment": item.get("segment")}
    if item.get("kind") == _routes._KEYED_ITEM_FACTS:
        effect_keys = item.get("effect_keys")
        stripped["effect_keys"] = list(effect_keys) if isinstance(effect_keys, list) else []
    return stripped


def _is_cancelled_staging_marker(document: dict) -> bool:
    """Whether ``document`` has exactly the stripped shape :func:`_cancelled_staging_marker` writes."""
    segments = document.get("segments")
    return (
        document.get(_routes._KEYED_STAGING_CANCELLED) is True
        and document.get("items") == []
        and document.get("applied") == []
        and isinstance(segments, list)
        and all(isinstance(segment, dict) and set(segment) == {"wire_key"} for segment in segments)
    )


def _cancelled_staging_marker(document: dict) -> dict:
    """The staging document reduced to what a retry needs to recognise its key as cancelled.

    Drops every extracted product (facts, display names, the subjects'
    fields): a forget must not leave the forgotten subject's content on disk.
    """
    segments = document.get("segments")
    return {
        "key": document.get("key"),
        "state": document.get("state"),
        "shape": document.get("shape"),
        "subjects": document.get("subjects"),
        "epochs": document.get("epochs"),
        "created_at": document.get("created_at"),
        "request_hash": document.get("request_hash"),
        "segments": [
            {"wire_key": segment.get("wire_key")}
            for segment in (segments if isinstance(segments, list) else [])
            if isinstance(segment, dict)
        ],
        "items": [],
        "applied": [],
        _routes._KEYED_STAGING_CANCELLED: True,
    }


async def _cancel_staged_writes_for_subjects(
    lanlan_name: str,
    subject_keys: set[str],
    *,
    request_subject_key: str | None = None,
    forget_epoch: int | None = None,
    fanout_fences: dict[str, int] | None = None,
    best_effort: bool = False,
) -> int:
    """Cancel every staged keyed write that touches one of ``subject_keys``.

    ``best_effort`` (the pass before the erase) skips a journal or key record
    whose handling fails and goes on with the rest; the pass after the erase
    raises instead, so the forget is retried.

    A staging started with an epoch at or above ``forget_epoch`` for the
    request subject was issued after this forget and is left alone; so is a
    segment whose wire key is a fan-out target stamped at or above that
    target's own fence in ``fanout_fences``.

    Runs after the erase, holding no other lock: each affected key's lock is
    taken on its own (an in-flight apply of the same key finishes first; an
    in-flight GENERATION never has a staging file yet, so a forget is never
    blocked behind an LLM call). The key is marked ``cancelled`` before its
    staging file is removed, so a crash in between leaves a cancelled key
    plus a leftover file the TTL sweep removes, never a revivable journal.
    """
    from . import idempotency

    fences = _forget_fences(request_subject_key, forget_epoch, fanout_fences)
    cancelled = 0
    async def _cancel_one_document(path, document) -> None:
        nonlocal cancelled
        if not isinstance(document, dict):
            # Unreadable staging hides its subjects; without an owning key record, record-based cancellation misses it too.
            # Retaining could preserve cleared source text, later claimed by same-key retry after repair. Delete it.
            if await idempotency.drop_unreadable_orphan_staging(lanlan_name, path):
                cancelled += 1
            return
        key = document.get("key")
        # Match recorded routed write subjects too: changed account bindings may exclude this staging's wire subject from current fanout,
        # yet application writes to the subject recorded in staging.
        # Missing/corrupt subjects index still requires per-segment matching: a bad index must not bypass clear; later same-key retries
        # could claim orphan staging and restore pre-clear facts.
        if not subject_keys.intersection(_staged_subject_keys(document)):
            return
        if not isinstance(key, str) or not key or not idempotency.is_staging_path_of(lanlan_name, key, path):
            # Embedded key differs from filename: never follow an untrusted key to another staging path.
            # Erase this file in place into a cancellation marker (retain mismatched key, so retries still fail closed),
            # leaving no cleared subject extraction source on disk.
            await idempotency.scrub_misplaced_staging(
                lanlan_name, path, _cancelled_staging_marker,
            )
            cancelled += 1
            return
        async with idempotency.key_lock(lanlan_name, key):
            current = await idempotency.read_staging(lanlan_name, key)
            if current is None or _staged_after_forget(current, subject_keys, fences):
                return
            if _drop_forgotten_segments(current, subject_keys, fences):
                # Multi-segment batch with only some subjects cleared: discard only those unapplied items,
                # erase their extraction source, retain pending key/staging; retry writes remaining segments normally.
                await idempotency.write_staging(lanlan_name, key, current)
                cancelled += 1
                return
            identity = _staging_identity(document)

            def _cancel(old, identity=identity):
                # Missing records inherit staged request identity: staging is deleted after cancellation; identity-less cancelled records let
                # other requests obtain duplicate with this key. Retain existing recorded identity: corrupt staged identity produces
                # a placeholder, which must not overwrite valid identity and turn legitimate duplicate retries into perpetual 422.
                stored = old.get("request") if isinstance(old, dict) else None
                request = stored if isinstance(stored, dict) else identity
                return idempotency.transition(idempotency.KEY_STATE_CANCELLED, request=request)(old)

            try:
                await idempotency.update_key(lanlan_name, key, _cancel)
            except idempotency.IdempotencyStateError as exc:
                # Unreadable key file: corrupt auxiliary data must not block privacy clear, but cancellation cannot be recorded there.
                # Deleting staging lets retries after key-file repair see pending/no-staging and re-extract with
                # post-clear generation (and no tombstone without forget_epoch), restoring content.
                # Instead retain cancellation in staging: retry repairs cancelled record and returns duplicate.
                _routes.logger.warning(
                    f"[scoped_forget] {lanlan_name}: 幂等键文件不可读，取消改记在暂存里: {exc}"
                )
                # Retain only identity needed to recognize cancellation; erase extracted fact text and display names together.
                await idempotency.write_staging(lanlan_name, key, _cancelled_staging_marker(current))
                cancelled += 1
                return
            await idempotency.delete_staging(lanlan_name, key)
            cancelled += 1
    for path, document, _mtime in await idempotency.list_staging(lanlan_name):
        try:
            await _cancel_one_document(path, document)
        except MaintenanceModeError:
            raise
        except Exception as exc:
            if not best_effort:
                raise
            # Best-effort pass: failure in one staging file skips only it; cancel others normally (post-erasure pass is fallback).
            _routes.logger.warning(f"[scoped_forget] {lanlan_name}: 取消暂存 {os.path.basename(path)} 失败，跳过: {exc}")
    # Pending precedes staging: a crash between leaves only records, invisible to the scan above.
    # Claim by recorded request identity and mark cancelled too, preventing later same-key retries regenerating
    # with post-clear generation and writing back.
    try:
        records = await _routes.asyncio.to_thread(
            idempotency._read_json_object, idempotency.keys_path(lanlan_name),
        )
    except idempotency.IdempotencyCorruptError as exc:
        # Corrupt key-file content: keyed requests already fail closed (503), writing nothing;
        # auxiliary corruption must not block privacy clear; log, skip claiming, and continue clearing.
        _routes.logger.warning(f"[scoped_forget] {lanlan_name}: 幂等键文件已损坏，跳过 pending 认领: {exc}")
        records = {}
    except Exception as exc:  # noqa: BLE001
        # Temporary unreadability (sharing retries exhausted/permissions) may resolve for next same-key retry. Skipping claiming
        # lets unstaged pending keys regenerate with post-clear generation and restore content. Pre-erasure pass
        # logs only (post-erasure pass covers it); post-erasure pass raises for clear retry (tombstones persisted, retry safe).
        if not best_effort:
            raise
        _routes.logger.warning(f"[scoped_forget] {lanlan_name}: 幂等键文件一时读不出，本遍跳过 pending 认领: {exc}")
        records = {}
    async def _cancel_one_record(key, record) -> None:
        nonlocal cancelled
        if not isinstance(record, dict) or record.get("state") != idempotency.KEY_STATE_PENDING:
            return
        request = record.get("request")
        wire_keys = request.get("wire_keys") if isinstance(request, dict) else None
        wire_only = [str(k) for k in wire_keys] if isinstance(wire_keys, list) else []
        routed = record.get("routed_keys")
        wire_keys = wire_only + ([str(k) for k in routed] if isinstance(routed, list) else [])
        # Staging scan above is only a snapshot; staging written later may route to cleared subjects while records
        # hold only wire keys. If staging exists, match all recorded wire+routed subjects; only absent staging
        # falls back to wire keys. Pre-read unlocked to exclude unrelated keys, whose requests may hold
        # key locks while awaiting LLM; privacy clear must not queue behind them. Re-read/recheck relevant keys under lock.
        peek_unreadable = False
        try:
            peek = await idempotency.read_staging(lanlan_name, key)
        except idempotency.IdempotencyStateError:
            peek = None
            peek_unreadable = True
        peek_touched = _staged_subject_keys(peek) | set(wire_keys) if peek is not None else set(wire_keys)
        if not subject_keys.intersection(peek_touched):
            return
        # Without staging, evaluate recorded positions individually: only wires predating their own fences enter forgotten_keys;
        # sufficiently new wires remain writable on retry (never reduce the whole request to one discard boolean). Include unfenced
        # wires routed to cleared subjects positionally too, not merely wires whose own key was cleared.
        stale_marks = _stale_record_marks(record, subject_keys, fences)
        if peek is None and not stale_marks:
            # No staging and no pre-fence positions: all are valid new writes started with knowledge of this clear.
            return
        def _mark_forgotten(touched: set[str]):
            def _mark(old, touched=touched):
                if old is None or old.get("state") != idempotency.KEY_STATE_PENDING:
                    return None
                # Corrupt old values (scalar / embedded objects): retain usable keys only; corrupt auxiliary state must not return
                # 500 before erasure.
                prior = old.get("forgotten_keys")
                if prior is None:
                    usable: set[str] = set()
                elif isinstance(prior, list) and all(isinstance(k, str) and k for k in prior):
                    usable = set(prior)
                else:
                    # Corrupt old markers are sole durable evidence of clearing during generation; do not simply drop them. Conservatively mark
                    # all wire/routed subjects in this key record (treat all as previously cleared).
                    request = old.get("request") if isinstance(old.get("request"), dict) else {}
                    every = [
                        value
                        for source in (request.get("wire_keys"), old.get("routed_keys"))
                        if isinstance(source, list)
                        for value in source
                    ]
                    usable = {k for k in every if isinstance(k, str) and k}
                merged = sorted(usable | touched)
                return {**old, "forgotten_keys": merged}

            return _mark

        if peek is None and not peek_unreadable and idempotency.key_lock(lanlan_name, key).locked():
            # No staging and key lock busy: owning request awaits LLM; do not queue behind it. Durably record cleared subjects
            # only on the record (atomic single-record rewrite under character lock). Before staging after generation, or regenerating
            # after failure/process kill, retries use this to discard segments rather than restore old content with post-clear generation.
            if not stale_marks:
                return
            await idempotency.update_key(lanlan_name, key, _mark_forgotten(stale_marks))
            cancelled += 1
            return
        async with idempotency.key_lock(lanlan_name, key):
            unreadable = None
            try:
                staged = await idempotency.read_staging(lanlan_name, key)
            except idempotency.IdempotencyStateError as exc:
                staged = None
                unreadable = exc
            touched = _staged_subject_keys(staged) | set(wire_keys) if staged is not None else set(wire_keys)
            if not subject_keys.intersection(touched):
                return
            if unreadable is not None:
                # Pending record with unreadable staging is invisible to content scans above. It must neither block
                # clear with repeated 500 nor remain for same-key retries to fail closed then apply after repair.
                # Claim by record, mark cancelled, then delete corrupt staging.
                _routes.logger.warning(f"[scoped_forget] {lanlan_name}: 暂存不可读，按键记录取消: {unreadable}")
            if staged is not None and _staged_after_forget(staged, subject_keys, fences):
                # New request carrying post-clear generation: output is valid new memory; do not cancel.
                return
            if staged is not None and _drop_forgotten_segments(staged, subject_keys, fences):
                # Like staging scan above: discard only cleared-subject segments, retaining others for retry.
                await idempotency.write_staging(lanlan_name, key, staged)
                cancelled += 1
                return
            if staged is None and unreadable is None and (
                not set(wire_keys) <= subject_keys or not set(wire_only) <= stale_marks
            ):
                # No staging (failed generation/process killed and lock released), but uncleared subjects or sufficiently new
                # wires remain: as for busy key locks, record only pre-fence wires; regeneration retries discard
                # those segments alone. Whole-key cancellation permanently loses others (client gets duplicate, never resends).
                if not stale_marks:
                    return
                await idempotency.update_key(lanlan_name, key, _mark_forgotten(stale_marks))
                cancelled += 1
                return
            # Cancel under key lock regardless of staging presence: scan above is a snapshot; staging written afterward
            # (failed request released lock) also needs cancellation, or same-key retries between erasure and second scan
            # could apply it using post-clear generation.
            await idempotency.update_key(
                lanlan_name, key, idempotency.transition(idempotency.KEY_STATE_CANCELLED),
            )
            await idempotency.delete_staging(lanlan_name, key)
            cancelled += 1
    for key, record in records.items():
        try:
            await _cancel_one_record(key, record)
        except MaintenanceModeError:
            raise
        except Exception as exc:
            if not best_effort:
                raise
            _routes.logger.warning(f"[scoped_forget] {lanlan_name}: 按记录取消键失败，跳过: {exc}")
    return cancelled
