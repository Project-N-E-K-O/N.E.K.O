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
"""Scoped subject erasure, forget epoch queries, and scoped subject listing routes."""

import json
import os
from typing import Annotated
from fastapi import HTTPException, Query
from pydantic import BaseModel, Field
from utils.cloudsave_runtime import MaintenanceModeError
from .runtime import app

from . import routes as _routes
from .scoped_trust_helpers import (
    MemorySubjectRequest,
    _merge_forget_stats,
)

class ScopedForgetRequest(BaseModel):
    subject: MemorySubjectRequest
    # Optional caller-side forget generation. When present, every erased
    # subject gets a durable tombstone holding the largest epoch seen, and a
    # keyed scoped_history whose ``subject_epochs`` for that subject is lower
    # is dropped at apply time. Absent: no tombstone is written.
    forget_epoch: int | None = Field(default=None, ge=0)
    # Optional ``{MemorySubject.key: forget_epoch}`` of every OTHER subject
    # identity expansion erases along with the requested one, each from its
    # own per-key counter (requires ``forget_epoch``). Absent: only the
    # requested subject is fenced, exactly as before. Present but missing a
    # fan-out target: 409 listing the missing keys, before anything is
    # written or erased, so the caller bumps those counters and resends.
    # A NEW forget must bump EVERY target's counter, not only the missing
    # ones: a target whose own completion marker already reaches the epoch
    # given for it is treated as a replay and is not erased again, so reusing
    # its last epoch leaves the rows routed into its pile since then.
    target_epochs: dict[str, Annotated[int, Field(ge=0)]] | None = None


_FORGET_TARGET_EPOCHS_MAX = 64


@app.post("/internal/memory/{lanlan_name}/scoped_forget")
async def forget_scoped_subject(lanlan_name: str, req: ScopedForgetRequest):
    """Erase one subject domain; epoch-tagged forgets of a subject run one at a time.

    The per-subject fence spans the completed-epoch check, the erase, both
    cancellation passes and the completion marker: without it an older
    forget could recheck after a newer one released its erase locks but
    before it published its completed epoch, and erase again.

    The fence is taken for the requested subject AND every fan-out target,
    all of them in sorted key order before any other lock: two forgets that
    name different linked aliases share targets, and one must not recheck
    or raise a shared target's fence between the other's erase and its
    completion markers. When the fan-out changes while the fences are being
    taken, they are released and taken again for the new set.
    """
    if req.forget_epoch is None:
        return await _run_forget(lanlan_name, req)
    import contextlib

    from . import idempotency

    try:
        subject = req.subject.to_domain()
        fence_name = _routes.validate_lanlan_name(lanlan_name)
    except Exception:  # noqa: BLE001 - Let implementation below report invalid requests normally.
        return await _run_forget(lanlan_name, req)
    for _attempt in range(_FORGET_FENCE_ATTEMPTS):
        fence_keys = _forget_fence_keys(subject)
        async with contextlib.AsyncExitStack() as stack:
            # Acquire all fences once in key order: every clear uses the same order, avoiding mutual waits.
            for fence_key in fence_keys:
                await stack.enter_async_context(idempotency.forget_fence(fence_name, fence_key))
            try:
                return await _run_forget(lanlan_name, req, fenced_keys=frozenset(fence_keys))
            except _FanoutChangedUnderFence:
                # Bindings changed while acquiring fences, adding unfenced fanout targets: release/reacquire (no writes yet).
                continue
    raise HTTPException(
        status_code=503,
        detail="identity links kept changing during the scoped forget; retry",
    )


_FORGET_FENCE_ATTEMPTS = 3


class _FanoutChangedUnderFence(Exception):
    """The fan-out grew a target whose forget fence this request does not hold."""


def _forget_fence_keys(subject) -> list[str]:
    """Sorted subject keys whose forget fences an epoch-tagged forget of ``subject`` takes."""
    from memory import trust_store as _trust_store

    if not _trust_store.trust_snapshot().loaded:
        # Unreadable identity pool makes clear itself return 503 without partial erasure; acquire only request subject's own fence.
        return [subject.key]
    return sorted({subject.key} | {target.key for target in _routes._forget_fanout_targets(subject)})


async def _forget_scoped_subject(lanlan_name: str, req: ScopedForgetRequest, *, fenced_keys=None):
    """Delete every stored memory of one exact (subject, scope) domain.

    Retraction entry: after removing a friend/leaving a group, clear this subject's facts
    (active + archived), reflections (including surfaced references), persona sections
    (including display_name), and pending corrections together. Previously the four scoped
    endpoints only added data, without retraction. Match (key, scope) exactly: unstamped legacy
    data and other scopes are excluded. Idempotent: repeats report zero. Partial failures return 500;
    safe to retry, with deleted data never revived. Reflection/persona archive shards remain as event-sourcing
    records; persistent forget watermarks prevent restoration even after event replay rebuilds them.
    """  # noqa: DOCSTRING_CJK
    lanlan_name = _routes.validate_lanlan_name(lanlan_name)
    if (
        _routes.runtime.fact_store is None
        or _routes.runtime.fact_dedup_resolver is None
        or _routes.runtime.persona_manager is None
        or _routes.runtime.reflection_engine is None
    ):
        raise HTTPException(
            status_code=503,
            detail="memory_server not fully initialized (limited mode or startup incomplete)",
        )
    subject = req.subject.to_domain()
    _validate_target_epochs(req, subject)
    # A pending snapshot journal restores its before-image when recovered, which
    # would bring back whatever this erase removes. Settle it first or refuse;
    # the erase checks again once it holds the lock snapshot commits take.
    from memory.long_term_sync import LongTermMemoryRecoveryRequired, require_consistent_views
    from .long_term_sync import recover_pending_snapshot
    try:
        await recover_pending_snapshot(lanlan_name)
    except LongTermMemoryRecoveryRequired as error:
        raise HTTPException(status_code=503, detail="long_term_memory_recovery_required") from error
    from memory import trust_store as _trust_store
    if not _trust_store.trust_snapshot().loaded:
        # FAIL CLOSED. With the pool unreadable the fan-out set is unknown, and
        # ``expand_subject`` degrades to just the requested subject — so a
        # non-canonical account whose rows were routed into the canonical pile
        # would get a PARTIAL erase reported as ``forgotten``. Under-deleting
        # on a privacy path and calling it success is the one outcome worth a
        # hard failure; the caller retries once the pool loads.
        #
        # Narrow by construction: a fresh or empty pool is ``loaded`` with no
        # entities, so a deployment that never linked accounts never sees this.
        raise HTTPException(
            status_code=503,
            detail=(
                "identity pool unreadable; refusing a partial scoped forget, "
                "retry once the trust pool loads"
            ),
        )
    targets = _routes._forget_fanout_targets(subject)
    if fenced_keys is not None and not {target.key for target in targets} <= fenced_keys:
        # Fanout contains unfenced target: return to outer layer to reacquire before tombstone writes/erasure.
        raise _FanoutChangedUnderFence()
    forgotten_subject_keys = {subject.key} | {target.key for target in targets}
    default_scope = subject.scope == subject.key
    if req.forget_epoch is not None and not default_scope:
        # Generations/tombstones/erased markers use kind:id; a nondefault generation-bearing scope could make another
        # scope under the same key appear erased and skip deletion. Generation-bearing clears support default scope only.
        raise HTTPException(status_code=422, detail="forget_epoch requires the default subject scope")
    # Per-fanout-target generations (when client supplies target_epochs): any missing target returns 409 before writes.
    fanout_epochs = _fanout_target_epochs(req, subject, targets)
    fence_at_start = None
    effective_fence = req.forget_epoch
    erase_targets = list(targets)
    # Fanout targets: pre-erasure fences / effective cancellation fences are each recorded by their own keys.
    fanout_fence_at_start: dict[str, int] = {}
    effective_fanout_fences = dict(fanout_epochs or {})
    if req.forget_epoch is not None:
        # Persist tombstones before any erasure: application snapshots generation then checks tombstone,
        # so applications starting afterward see it; items that already passed tombstone checks
        # are discarded by the forget generation advanced during erasure below.
        from . import idempotency

        try:
            # Generations count separately by MemorySubject.key: request subject uses forget_epoch; fanout targets only
            # their client-supplied generations, never the request subject's (which would permanently block smaller target generations).
            # Without target_epochs, omit fanout tombstones; fact-store forget generation alone protects in-flight writes.
            recorded = await idempotency.record_tombstone_epochs(
                lanlan_name, {**effective_fanout_fences, subject.key: req.forget_epoch},
            )
            # Fence present before erasure (a newer clear may have raised it then failed before erasing): this erasure
            # follows it, so completion also covers that generation.
            fence_at_start = _recorded_fence(recorded, subject.key)
            if fence_at_start is not None and fence_at_start > effective_fence:
                # Cancel staging against the effective existing fence, not this possibly stale replay's lower generation:
                # all pre-fence staging must be cancelled.
                effective_fence = fence_at_start
            _raise_fanout_fences(recorded, effective_fanout_fences, fanout_fence_at_start)
            # Caller supplied a generation for a key no longer in current fanout (unbound between requests): write no new tombstone,
            # but if previously cleared (existing fence), include that persisted fence in cancellation scans,
            # or pre-clear staging arriving after original erasure stays pending with plaintext forever. Use persisted fences only:
            # current supplied generations could wrongly cancel its valid independent post-unbind writes.
            for retained_key in _retained_target_keys(req, subject, targets):
                fence = _recorded_fence(recorded, retained_key)
                if fence is not None:
                    effective_fanout_fences[retained_key] = fence
        except MaintenanceModeError:
            raise
        except idempotency.IdempotencyStateError as exc:
            # Entire tombstone file unreadable: corrupt auxiliary data must not block privacy erasure; erase normally. All keyed
            # writes also fail closed (503) reading it, so old requests cannot restore content; no completion marker until repaired.
            _routes.logger.warning(f"[scoped_forget] {lanlan_name}: 墓碑文件不可读，跳过墓碑照常擦除: {exc}")
        except Exception as exc:
            _routes.logger.error(f"[scoped_forget] {lanlan_name}: 墓碑落盘失败: {exc}")
            raise HTTPException(
                status_code=500,
                detail="scoped forget failed; retry is safe and idempotent",
            ) from exc
        # This or newer generation already erased: do not erase again on replay/late old clear,
        # which would delete subsequent valid new-generation memory. Still erase newly linked fanout targets
        # carrying generations not yet erased; erase only those. Recheck after acquiring erasure transaction lock.
        erase_targets = await _routes._targets_still_to_erase(
            lanlan_name, subject, targets, req.forget_epoch, fanout_epochs,
        )
        if not erase_targets:
            return await _forget_duplicate_after_cancel(
                lanlan_name, subject, targets, effective_fence, effective_fanout_fences,
            )
    erase_keys = _erase_subject_keys(
        subject, targets, erase_targets, forgotten_subject_keys, effective_fanout_fences,
    )
    # Before erasure, cancel existing keyed staging/records once (no other locks held, so no cycle
    # with same-key application). Later retries get duplicate, unable to race between erasure and
    # the cancellation scan below, restoring old output with post-clear generation.
    try:
        if default_scope:
            # Keyed writes support default scope only; clears of other scopes must not wrongly cancel by kind:id.
            await _routes._cancel_staged_writes_for_subjects(
                lanlan_name, erase_keys,
                request_subject_key=subject.key, forget_epoch=effective_fence,
                fanout_fences=effective_fanout_fences, best_effort=True,
            )
    except MaintenanceModeError:
        raise
    except Exception as exc:
        # Pre-erasure pass is best effort (per-file/key tolerance; skip only failures): one unreadable/unwritable staging file cannot block unrelated privacy clear
        # (including legacy callers never using keyed writes). Post-erasure cancellation and tombstone/generation provide the real fallback;
        # only that pass's failure returns an error for caller retry.
        _routes.logger.warning(f"[scoped_forget] {lanlan_name}: 擦除前取消带键暂存未完成（擦除后再取消一遍）: {exc}")
    stats: dict = {}
    fact_forget_started: list = []
    reflection_forget_started: list = []
    acquired_locks: list = []
    # Component references are atomically replaced under this lock. Keep the
    # same generation alive until every tombstone is closed, otherwise reload
    # can split one forget transaction across old and new managers.
    #
    # ONE TRANSACTION FOR ALL TARGETS, not N independent ones. Splitting would
    # break two things at once: (a) ``_reload_lock`` would be released between
    # subjects, letting a reload cut in; (b) subject i's tombstone would close
    # BEFORE subject i+1's opens, and fact extraction / reflection synthesis
    # release their locks during LLM calls — an in-flight write captured before
    # the forget could then land after its tombstone closed, growing data back
    # inside a domain that was just erased. This is a privacy path; ordering
    # arguments ("delete canonical last") are not enough.
    await _routes.runtime._reload_lock.acquire()
    try:
        await _routes.asyncio.to_thread(
            require_consistent_views,
            os.path.join(_routes.runtime._config_manager.memory_dir, lanlan_name),
        )
        for target in erase_targets:
            lock = _routes.runtime.fact_store._get_subject_forget_transaction_lock(
                lanlan_name, target,
            )
            await lock.acquire()
            acquired_locks.append(lock)
        # Concurrent older/newer clears may both pass unlocked checks; newer erases/releases lock,
        # then older acquires it. Recheck under lock to preserve valid writes after newer clearing.
        if req.forget_epoch is not None:
            # Erased markers only increase: targets still requiring erasure under lock are a subset calculated outside it.
            still = await _routes._targets_still_to_erase(
                lanlan_name, subject, targets, req.forget_epoch, fanout_epochs,
            )
            locked = {(target.key, target.scope) for target in erase_targets}
            still = [target for target in still if (target.key, target.scope) in locked]
            if not still:
                # After releasing lock, run the outer cancellation pass too, cancelling pre-clear staging written while waiting for lock.
                raise _ForgetAlreadyErased(
                    lanlan_name, subject, targets, effective_fence, effective_fanout_fences,
                )
            if len(still) != len(erase_targets):
                erase_targets = still
                erase_keys = _erase_subject_keys(
                    subject, targets, erase_targets, forgotten_subject_keys, effective_fanout_fences,
                )
        if req.forget_epoch is not None:
            # Between unlocked fence read and erasure lock, another clear may raise a target fence (fanout clears are not
            # protected by this request's forget_fence). This erasure follows it; completion/cancellation use the last fence
            # present before erasure, increasing only.
            try:
                fences_now = await idempotency.read_tombstones(lanlan_name)
            except idempotency.IdempotencyStateError:
                fences_now = None
            if fences_now is not None:
                fence = _recorded_fence(fences_now, subject.key)
                if fence is not None and (fence_at_start is None or fence > fence_at_start):
                    fence_at_start = fence
                    effective_fence = max(effective_fence, fence)
                _raise_fanout_fences(fences_now, effective_fanout_fences, fanout_fence_at_start)
        # ALL tombstones open before ANY erase.
        for target in erase_targets:
            await _routes.runtime.fact_store.abegin_subject_forget(lanlan_name, target)
            fact_forget_started.append(target)
        for target in erase_targets:
            await _routes.runtime.reflection_engine.abegin_subject_forget(
                lanlan_name, target,
            )
            reflection_forget_started.append(target)
        for target in erase_targets:
            # SUM, never replace. Every store returns the same counter keys, so
            # a per-target ``update`` reports only the LAST account's numbers —
            # if the first account deleted rows and a later one deleted none,
            # the endpoint would report zero for data it just erased. This is a
            # privacy operation whose response is the operator's only receipt.
            _merge_forget_stats(
                stats,
                await _routes.runtime.fact_dedup_resolver.aforget_subject(
                    lanlan_name, target,
                ),
            )
            _merge_forget_stats(
                stats,
                await _routes.runtime.fact_store.aforget_subject(lanlan_name, target),
            )
            _merge_forget_stats(
                stats,
                await _routes.runtime.reflection_engine.aforget_subject(
                    lanlan_name, target,
                ),
            )
            _merge_forget_stats(
                stats,
                await _routes.runtime.persona_manager.aforget_subject(
                    lanlan_name, target,
                ),
            )
            _merge_forget_stats(stats, {
                "prompt_locale": await _routes.asyncio.to_thread(
                    _routes.locale_state.forget_subject_prompt_locale,
                    lanlan_name,
                    target,
                ),
            })
        # Reflection/persona archive writers take their store locks. Their
        # forget calls above therefore drain any writer that had already
        # snapshotted this subject. Advance the persistent cutoff only now,
        # while every write tombstone is still open, so a snapshot archived
        # after the initial facts erase cannot become restore-eligible.
        #
        # The cutoff is PER SUBJECT and persistent
        # (``subject_forget_tombstones.json``). Skipping any target's cutoff
        # would leave that account's archived shards restorable through event
        # replay — i.e. a "left the group, wiped my data" request that quietly
        # keeps a copy.
        for target in erase_targets:
            await _routes.runtime.fact_store.afinalize_subject_forget(
                lanlan_name, target,
            )
    except _ForgetAlreadyErased:
        raise
    except LongTermMemoryRecoveryRequired as exc:
        raise HTTPException(status_code=503, detail="long_term_memory_recovery_required") from exc
    except Exception as exc:
        _routes.logger.error(f"[scoped_forget] {lanlan_name}: 删除失败: {exc}")
        raise HTTPException(
            status_code=500,
            detail="scoped forget failed; retry is safe and idempotent",
        ) from exc
    finally:
        try:
            try:
                for target in reversed(reflection_forget_started):
                    await _routes.runtime.reflection_engine.aend_subject_forget(
                        lanlan_name, target,
                    )
            finally:
                # Never strand a fact-write tombstone if the independent
                # reflection close encounters an unexpected failure.
                for target in reversed(fact_forget_started):
                    await _routes.runtime.fact_store.aend_subject_forget(
                        lanlan_name, target,
                    )
        finally:
            for lock in reversed(acquired_locks):
                lock.release()
            _routes.runtime._reload_lock.release()
    # Cancel keyed staging involving these subjects only after erasure completes and all locks above release.
    # Cancellation acquires each key lock; applying requests hold those locks while awaiting fact-pool persistence
    # lock (held by this handler during erasure). Awaiting key locks within erasure transactions would
    # deadlock; here no other locks are held while waiting, avoiding cycles.
    try:
        if default_scope:
            await _routes._cancel_staged_writes_for_subjects(
                lanlan_name, erase_keys,
                request_subject_key=subject.key, forget_epoch=effective_fence,
                fanout_fences=effective_fanout_fences,
            )
    except MaintenanceModeError:
        raise
    except Exception as exc:
        _routes.logger.error(f"[scoped_forget] {lanlan_name}: 取消带键暂存失败: {exc}")
        raise HTTPException(
            status_code=500,
            detail="scoped forget failed; retry is safe and idempotent",
        ) from exc
    if req.forget_epoch is not None:
        from . import idempotency

        try:
            # Mark generation erased only after erasure and both cancellation passes finish. Mark fanout first, request subject last:
            # duplicate-clear shortcut checks only request subject completion, which implies every target marker already persisted.
            erased_keys = {target.key for target in erase_targets}
            # Partial erasure skipped request subject/already-erased targets: do not mark targets erased using higher existing fences.
            # That higher-generation clear (requesting this target and fanning out to the whole participant) has not finished;
            # borrowing it would make retry take duplicate shortcut, missing intervening writes from other participant accounts.
            full_erase = len(erase_targets) == len(targets)
            for target_key, target_epoch in sorted((fanout_epochs or {}).items()):
                if target_key not in erased_keys:
                    continue
                await idempotency.mark_tombstone_erased(
                    lanlan_name, target_key, target_epoch,
                    covered_epoch=fanout_fence_at_start.get(target_key) if full_erase else None,
                )
            if subject.key in erased_keys:
                # When only additional fanout targets erased, request subject stayed untouched; existing completion already covers forget_epoch.
                # Do not use covered_epoch to mark higher fences that failed before erasure, or that erasure can never be retried.
                await idempotency.mark_tombstone_erased(
                    lanlan_name, subject.key, req.forget_epoch, covered_epoch=fence_at_start,
                )
        except MaintenanceModeError:
            raise
        except idempotency.IdempotencyStateError as exc:
            # Unreadable tombstones/corrupt row: erasure finished but generation completion cannot persist. Return 503 for
            # retries until repaired and marked; keyed writes for this subject also return 503 reading tombstones meanwhile.
            _routes.logger.warning(f"[scoped_forget] {lanlan_name}: 墓碑损坏，未记擦除完成: {exc}")
            raise HTTPException(
                status_code=503,
                detail="scoped forget erased but its completion marker could not be recorded; retry",
            ) from exc
        except Exception as exc:
            # Erasure complete but generation-complete marker not persisted: success would stop retries, allowing same/older generation
            # replay to erase subsequent valid memory again. Return error until completion persists.
            _routes.logger.error(f"[scoped_forget] {lanlan_name}: 记录擦除完成失败: {exc}")
            raise HTTPException(
                status_code=500,
                detail="scoped forget failed; retry is safe and idempotent",
            ) from exc
    return {
        "status": "forgotten",
        "subject": subject.as_entry_fields(),
        "forgotten_subjects": [
            target.as_entry_fields() for target in targets
        ],
        # Subjects actually erased this time: replay filling newly linked targets erases fewer than forgotten_subjects;
        # privacy clear's sole receipt must distinguish additional erasure from complete erasure.
        "erased_subjects": [
            target.as_entry_fields() for target in erase_targets
        ],
        **stats,
    }


def _validate_target_epochs(req: ScopedForgetRequest, subject) -> None:
    """422 for a ``target_epochs`` map that cannot be honoured as sent."""
    target_epochs = req.target_epochs
    if target_epochs is None:
        return
    if req.forget_epoch is None:
        raise HTTPException(status_code=422, detail="target_epochs requires forget_epoch")
    if len(target_epochs) > _FORGET_TARGET_EPOCHS_MAX or any(
        not key or len(key) > 512 or not key.isprintable() for key in target_epochs
    ):
        raise HTTPException(status_code=422, detail="invalid target_epochs keys")
    if subject.key in target_epochs and target_epochs[subject.key] != req.forget_epoch:
        # Request subject generation has one source: differing values at the two locations leave no trustworthy choice.
        raise HTTPException(status_code=422, detail="target_epochs disagrees with forget_epoch")


def _fanout_target_epochs(req: ScopedForgetRequest, subject, targets) -> dict[str, int] | None:
    """``{fan-out target key: its own epoch}``, or None when the caller did not opt in.

    Raises 409 (before anything is written or erased) listing every fan-out
    target the caller gave no epoch for: the server cannot pick one for it,
    since a copied or guessed epoch either blocks that subject's own later
    writes or lets its delayed pre-forget writes through.
    """
    if req.target_epochs is None:
        return None
    fanout_keys = sorted({target.key for target in targets} - {subject.key})
    missing = [key for key in fanout_keys if key not in req.target_epochs]
    if missing:
        raise HTTPException(
            status_code=409,
            detail={"error": "missing_target_epochs", "missing_target_epochs": missing},
        )
    # Extra keys outside fanout (unbound between requests) get no tombstones: outside this erasure's scope.
    return {key: int(req.target_epochs[key]) for key in fanout_keys}


async def _targets_still_to_erase(lanlan_name: str, subject, targets, forget_epoch, fanout_epochs) -> list:
    """The fan-out targets this forget still has to erase (empty: a duplicate).

    Each target is judged by its own completion marker: the request subject
    and every target the caller gave no epoch for go with the request
    subject's marker for ``forget_epoch``; a target the caller gave its own
    epoch for is erased only while its own marker is below that epoch. So a
    replay naming an account linked after the original erase erases just
    that account, and a retry after a partly recorded completion does not
    erase again a target whose marker already landed (either would delete
    writes made legitimately after that erase). An unreadable tombstone file
    counts as "not erased" (bias towards deleting).
    """
    from . import idempotency

    if forget_epoch is None:
        return list(targets)
    request_done = await _routes._forget_epoch_already_erased(lanlan_name, subject.key, forget_epoch)
    if request_done and not fanout_epochs:
        return []
    tombstones: dict = {}
    if fanout_epochs:
        try:
            tombstones = await idempotency.read_tombstones(lanlan_name)
        except idempotency.IdempotencyStateError:
            tombstones = {}
    pending = []
    for target in targets:
        epoch = None if target.key == subject.key else (fanout_epochs or {}).get(target.key)
        if epoch is None:
            # Request subject and fanout targets without generations follow request subject's completion marker.
            if not request_done:
                pending.append(target)
            continue
        done = idempotency.erased_epoch(tombstones, target.key)
        if done is None or done < epoch:
            pending.append(target)
    return pending


def _erase_subject_keys(
    subject, targets, erase_targets, forgotten_subject_keys: set[str], fenced_keys,
) -> set[str]:
    """Subject keys the cancellation passes match for this erase.

    The whole fan-out for a full erase. For a partial one (some targets
    were erased already) the erase scope is narrower, but cancellation is
    not: the request subject, the targets erased now, and every key with a
    comparable fence (``fenced_keys``), the same set the duplicate path
    cancels for. ``fenced_keys`` also holds keys the caller named that left
    the fan-out but were fenced by an earlier erase. A pre-forget journal
    of an already erased key that staged after its marker landed is
    cancelled too, while a fresh one survives the per-key fence comparison.
    """
    if len(erase_targets) == len(targets):
        return set(forgotten_subject_keys) | set(fenced_keys)
    return {subject.key} | {target.key for target in erase_targets} | set(fenced_keys)


def _retained_target_keys(req: ScopedForgetRequest, subject, targets) -> list[str]:
    """Keys the caller gave an epoch for that are no longer in the current fan-out (unlinked since)."""
    current = {target.key for target in targets} | {subject.key}
    return sorted(set(req.target_epochs or {}) - current)


def _raise_fanout_fences(tombstones, effective: dict[str, int], at_start: dict[str, int]) -> None:
    """Raise each fan-out target's fences to the tombstone epoch now stored for it (never lowers)."""
    for target_key in list(effective):
        fence = _recorded_fence(tombstones, target_key)
        if fence is None:
            continue
        if fence > at_start.get(target_key, -1):
            at_start[target_key] = fence
        if fence > effective[target_key]:
            effective[target_key] = fence


def _recorded_fence(recorded, subject_key: str) -> int | None:
    """The tombstone epoch now stored for ``subject_key`` (None when absent or damaged)."""
    row = recorded.get(subject_key) if isinstance(recorded, dict) else None
    fence = row.get("forget_epoch") if isinstance(row, dict) else None
    if isinstance(fence, int) and not isinstance(fence, bool) and fence >= 0:
        return fence
    return None


async def _forget_epoch_already_erased(lanlan_name: str, subject_key: str, forget_epoch: int) -> bool:
    """Whether the erase of ``forget_epoch`` (or a newer one) already completed; unreadable reads as no."""
    from . import idempotency

    try:
        done = idempotency.erased_epoch(await idempotency.read_tombstones(lanlan_name), subject_key)
    except idempotency.IdempotencyStateError:
        # On unreadability erase normally (favor extra deletion).
        return False
    return done is not None and done >= forget_epoch


class _ForgetAlreadyErased(Exception):
    """Raised under the erase locks when the epoch turns out to be erased already."""

    def __init__(self, lanlan_name, subject, targets, effective_fence, fanout_fences=None) -> None:
        super().__init__("forget epoch already erased")
        self.args_for_cancel = (lanlan_name, subject, targets, effective_fence, fanout_fences)


async def _forget_duplicate_after_cancel(
    lanlan_name, subject, targets, effective_fence, fanout_fences=None,
) -> dict:
    """Answer an already-erased forget after cancelling pre-forget staging of the fenced subjects.

    The store is not erased again, but a pre-forget keyed request that arrived
    after the original erase may have staged plaintext and crashed; pending,
    it is never swept by the TTL. Only subjects with a comparable epoch are
    matched: the request subject, plus each fan-out target the caller gave
    its own epoch for (``fanout_fences``). A target without one is left
    alone: its later keyed write carries an epoch from another counter that
    cannot be compared to this forget's, and is legitimate.
    """
    fanout_fences = dict(fanout_fences or {})
    try:
        await _routes._cancel_staged_writes_for_subjects(
            lanlan_name, {subject.key} | set(fanout_fences),
            request_subject_key=subject.key, forget_epoch=effective_fence,
            fanout_fences=fanout_fences,
        )
    except MaintenanceModeError:
        raise
    except Exception as exc:
        _routes.logger.error(f"[scoped_forget] {lanlan_name}: 重复清除取消带键暂存失败: {exc}")
        raise HTTPException(
            status_code=500,
            detail="scoped forget failed; retry is safe and idempotent",
        ) from exc
    return _forget_duplicate_response(subject, targets)


async def _run_forget(lanlan_name: str, req: ScopedForgetRequest, *, fenced_keys=None):
    try:
        return await _forget_scoped_subject(lanlan_name, req, fenced_keys=fenced_keys)
    except _ForgetAlreadyErased as done:
        return await _forget_duplicate_after_cancel(*done.args_for_cancel)


def _forget_duplicate_response(subject, targets) -> dict:
    return {
        "status": "forgotten",
        "subject": subject.as_entry_fields(),
        "forgotten_subjects": [target.as_entry_fields() for target in targets],
        "erased_subjects": [],
        "duplicate": True,
    }


def _read_json_list_for_listing(path: str) -> list:
    """Write-free read of one JSON list file for the listing endpoint (never creates directories)."""
    from utils.file_utils import read_json_tolerating_replace

    if not os.path.exists(path):
        return []
    try:
        # Tolerate concurrent os.replace sharing conflicts on Windows, avoiding subjects appearing empty or disappearing.
        data = read_json_tolerating_replace(path)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError) as exc:
        _routes.logger.warning(f"[scoped_subjects] {os.path.basename(path)} 读取失败，按空处理: {exc}")
        return []
    return data if isinstance(data, list) else []


def _read_locale_subjects_for_listing(path: str, lanlan_name: str) -> list:
    """Write-free read of the subjects that have a live scoped prompt-locale row."""
    from memory.scopes import subject_from_entry
    from utils.file_utils import read_json_tolerating_replace

    if not os.path.exists(path):
        return []
    try:
        data = read_json_tolerating_replace(path)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError) as exc:
        _routes.logger.warning(f"[scoped_subjects] scoped_prompt_locales.json 读取失败，按空处理: {exc}")
        return []
    rows = data.get("subjects") if isinstance(data, dict) else None
    live = None
    if isinstance(rows, dict):
        try:
            # Filter cleared rows still on disk (crash after cutoff write, before sidecar rewrite) as the loader does,
            # or cleared subjects keep appearing via prompt_locale and resist repeated clearing.
            live = _routes.locale_state.live_subject_locale_keys(lanlan_name, rows)
        except _routes.locale_state.PromptLocalePersistenceError as exc:
            _routes.logger.warning(f"[scoped_subjects] 语言清除 cutoff 读不出，按全部列出: {exc}")
    subjects = []
    for key, row in (rows.items() if isinstance(rows, dict) else ()):
        if not isinstance(key, str) or not isinstance(row, dict):
            continue
        if live is not None and key not in live:
            continue
        try:
            parts = json.loads(key)
        except (json.JSONDecodeError, RecursionError):
            continue
        if not isinstance(parts, list) or len(parts) != 3 or not all(isinstance(p, str) for p in parts):
            continue
        subject = subject_from_entry({"subject_kind": parts[0], "subject_id": parts[1], "scope": parts[2]})
        if subject is not None:
            subjects.append(subject)
    return subjects


def _read_staged_subjects_for_listing(directory: str) -> list:
    """Write-free read of the subjects named by pending keyed staging journals."""
    from memory.scopes import subject_from_entry

    from .idempotency import _list_staging_sync

    from .idempotency import IDEMPOTENCY_KEYS_FILENAME, TERMINAL_KEY_STATES, key_digest

    # Look up key records by filename: terminal done/cancelled staging left by failed cleanup has no unapplied work,
    # so must not appear staged. Orphans can be claimed on retry: list normally; unreadable records list all (favor inclusion).
    terminal_files: set[str] = set()
    from utils.file_utils import read_json_tolerating_replace

    try:
        records = read_json_tolerating_replace(os.path.join(os.path.dirname(directory), IDEMPOTENCY_KEYS_FILENAME))
    except (OSError, ValueError, RecursionError):
        records = {}
    if isinstance(records, dict):
        for key, record in records.items():
            # Recognize explicit terminal states only: missing/corrupt state fails closed on write; staging may hold sole plaintext, so list it.
            state = record.get("state") if isinstance(record, dict) else None
            if isinstance(key, str) and key and isinstance(state, str) and state in TERMINAL_KEY_STATES:
                terminal_files.add(f"{key_digest(key)}.json")
    subjects = []
    for path, document, _mtime in _list_staging_sync(directory):
        if not isinstance(document, dict) or document.get(_routes._KEYED_STAGING_CANCELLED) is True:
            continue
        if os.path.basename(path) in terminal_files:
            continue
        segments = document.get("segments")
        for segment in segments if isinstance(segments, list) else []:
            if isinstance(segment, dict) and segment.get(_routes._SEGMENT_DROPPED_BY_FORGET) is True:
                # Cleared/discarded segments (content erased) have no pending memory: do not make cleared entities reappear.
                continue
            raw = segment.get("subject") if isinstance(segment, dict) else None
            subject = subject_from_entry(raw) if isinstance(raw, dict) else None
            if subject is not None:
                subjects.append(subject)
    return subjects


def _read_correction_subjects_for_listing(path: str) -> list:
    """Write-free read of the subjects owning a pending persona correction (one entry per row).

    Same attribution as ``PersonaManager.aforget_subject``: the subject stamp,
    or for older unstamped rows the ``@subject/<kind>:<id>`` entity.
    """
    from memory.scopes import SCOPED_PERSONA_PREFIX, MemoryScopeError, MemorySubject, subject_from_entry
    from utils.file_utils import read_json_tolerating_replace

    if not os.path.exists(path):
        return []
    try:
        data = read_json_tolerating_replace(path)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError) as exc:
        _routes.logger.warning(f"[scoped_subjects] persona_corrections.json 读取失败，按空处理: {exc}")
        return []
    subjects = []
    for correction in data if isinstance(data, list) else []:
        if not isinstance(correction, dict):
            continue
        subject = subject_from_entry(correction)
        if subject is None:
            entity_raw = correction.get("entity")
            entity = entity_raw.strip() if isinstance(entity_raw, str) else ""
            if not entity.startswith(SCOPED_PERSONA_PREFIX):
                continue
            body = entity[len(SCOPED_PERSONA_PREFIX):]
            kind, _, subject_id = body.partition(":")
            scope = correction.get("scope")
            try:
                subject = MemorySubject.create(kind, subject_id, scope=scope if isinstance(scope, str) and scope else body)
            except MemoryScopeError:
                continue
        subjects.append(subject)
    return subjects


def _read_persona_for_listing(path: str) -> dict:
    """Strict, write-free persona read for the listing endpoint."""
    from utils.file_utils import read_json_tolerating_replace

    if not os.path.exists(path):
        return {}
    try:
        data = read_json_tolerating_replace(path)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError) as exc:
        _routes.logger.warning(f"[scoped_subjects] persona 读取失败，按无 persona 处理: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


_FORGET_EPOCHS_MAX_SUBJECTS = 64


@app.get("/internal/memory/{lanlan_name}/forget_epochs")
async def get_forget_epochs(lanlan_name: str, subject: list[str] = Query(default=[])):
    """Current forget-epoch fence of each requested subject key (read-only).

    Answers ``{"epochs": {subject_key: forget_epoch}}`` for the keys that
    carry a tombstone; keys without one are left out. A client whose local
    epoch counter was reset (cloud restore, new installation) raises it to
    this value before opening a keyed write or sending a forget, so neither
    falls below a restored tombstone. A tombstone file or row that cannot be
    read answers 503: an unknown fence must not read as "no fence".
    """
    from . import idempotency

    lanlan_name = _routes.validate_lanlan_name(lanlan_name)
    if _routes.runtime._config_manager is None:
        raise HTTPException(
            status_code=503,
            detail="memory_server not fully initialized (limited mode or startup incomplete)",
        )
    if len(subject) > _FORGET_EPOCHS_MAX_SUBJECTS or any(
        not key or len(key) > 512 or not key.isprintable() for key in subject
    ):
        raise HTTPException(status_code=422, detail="invalid subject keys")
    try:
        tombstones = await idempotency.read_tombstones(lanlan_name)
        epochs = {}
        for key in dict.fromkeys(subject):
            fence = idempotency.tombstone_epoch(tombstones, [key])
            if fence is not None:
                epochs[key] = fence
    except idempotency.IdempotencyStateError as exc:
        raise HTTPException(status_code=503, detail="forget epochs unreadable; retry later") from exc
    return {"epochs": epochs}


@app.get("/internal/memory/{lanlan_name}/scoped_subjects")
async def list_scoped_subjects(lanlan_name: str, platform: str):
    """List the scoped subjects stored for one platform (read-only).

    Filters on the platform component of ``subject_id`` (its first
    ``:``-separated segment) for every subject kind, NOT on a raw key
    prefix: a ``participant`` id ``neko_visit:<uid>`` and a ``group_chat``
    id ``neko_visit:<pair>`` share their prefix, and ``neko_visit_x:...``
    must not match ``neko_visit``. Read-only: persona is read straight from
    disk instead of through ``aensure_persona`` (which recovers and saves a
    malformed file), and a character without a directory answers an empty
    list without creating one. Not part of the write fence (GET is never in
    ``_CHARACTER_SCOPED_WRITE_OPS``).
    """
    from memory.scopes import (
        SCOPED_PERSONA_PREFIX,
        entry_matches_subject,
        persona_subject_from_section,
        subject_from_entry,
    )
    from memory.subject_archive import collect_subject_last_writes

    lanlan_name = _routes.validate_lanlan_name(lanlan_name)
    if _routes.runtime._config_manager is None:
        # Listing reads disk directly (facts / reflections / persona), depending only on config manager.
        raise HTTPException(
            status_code=503,
            detail="memory_server not fully initialized (limited mode or startup incomplete)",
        )
    platform = (platform or "").strip()
    if not platform or ":" in platform or len(platform) > 64:
        raise HTTPException(status_code=422, detail="invalid platform")
    character_dir = os.path.join(
        str(_routes.runtime._config_manager.memory_dir), lanlan_name,
    )
    if not await _routes.asyncio.to_thread(os.path.isdir, character_dir):
        return {"subjects": []}

    def _platform_of(subject) -> str:
        return subject.subject_id.split(":", 1)[0]

    # Read disk directly, bypassing store loaders whose path lookup calls ensure_character_dir:
    # this GET has no write fence; concurrent delete/rename would recreate just-deleted character directories.
    active_facts = await _routes.asyncio.to_thread(
        _read_json_list_for_listing, os.path.join(character_dir, "facts.json"),
    )
    archived_facts = await _routes.asyncio.to_thread(
        _read_json_list_for_listing, os.path.join(character_dir, "facts_archive.json"),
    )
    # Cold facts count too (archived subjects live there), as stubs without text.
    from memory import cold_store
    try:
        archived_facts = list(archived_facts) + await cold_store.astatus(character_dir)
    except cold_store.ColdStoreUnreadable as exc:
        _routes.logger.warning(f"[MemoryServer] list_scoped_subjects {lanlan_name}: cold facts unreadable: {exc}")
    def _fact_id(fact: dict):
        # Old/manual files may have unhashable list/object IDs: accept strings/integers only;
        # treat others as no ID, preventing one corrupt row from making the entire listing return 500.
        fid = fact.get("id")
        if isinstance(fid, bool) or not isinstance(fid, (str, int)):
            return None
        return fid

    def _identity(fact: dict):
        # Same full identity as fact layer: (id, subject_kind, subject_id, scope);
        # identical bare IDs across different subjects/scopes must not suppress one another.
        fid = _fact_id(fact)
        if fid is None:
            return None
        parts = (fact.get("subject_kind"), fact.get("subject_id"), fact.get("scope"))
        # Manually corrupted list/object identity fields are unhashable: treat as no identity, excluding from deduplication.
        if any(part is not None and not isinstance(part, str) for part in parts):
            return None
        return (fid, *parts)

    active_fact_ids = {
        _identity(fact)
        for fact in active_facts
        if isinstance(fact, dict) and _identity(fact) is not None
    }
    # Deduplicate IDs like load_facts_full: archiving writes facts_archive.json first, then
    # facts.json; interruption between steps temporarily puts one fact in both files.
    active_rows = [fact for fact in active_facts if isinstance(fact, dict)]
    # Activity depends on source file, not ID: active facts with corrupt IDs are not archive-only.
    active_row_refs = {id(fact) for fact in active_rows}
    facts_full = active_rows + [
        fact for fact in archived_facts
        if isinstance(fact, dict)
        and (_identity(fact) is None or _identity(fact) not in active_fact_ids)
    ]
    reflections_path = os.path.join(character_dir, "reflections.json")
    from memory.reflection._shared import REFLECTION_TERMINAL_STATUSES

    # List all stored reflections, including terminal promoted/denied/merged states and missing/corrupt IDs:
    # still on disk and in scoped_forget's deletion scope (subject-based, not ID-based). Subjects containing only these
    # must remain discoverable/clearable, so bypass the active loader's ID filtering.
    reflections = [
        row for row in await _routes.asyncio.to_thread(_read_json_list_for_listing, reflections_path)
        if isinstance(row, dict)
    ]
    persona = await _routes.asyncio.to_thread(
        _read_persona_for_listing,
        os.path.join(character_dir, "persona.json"),
    )
    locale_subjects = await _routes.asyncio.to_thread(
        _read_locale_subjects_for_listing,
        os.path.join(character_dir, "scoped_prompt_locales.json"),
        lanlan_name,
    )
    correction_subjects = await _routes.asyncio.to_thread(
        _read_correction_subjects_for_listing,
        os.path.join(character_dir, "persona_corrections.json"),
    )
    from .idempotency import STAGING_DIRNAME

    staged_subjects = await _routes.asyncio.to_thread(
        _read_staged_subjects_for_listing, os.path.join(character_dir, STAGING_DIRNAME),
    )

    rows: dict[tuple[str, str], dict] = {}

    def _row(subject) -> dict | None:
        if _platform_of(subject) != platform:
            return None
        marker = (subject.key, subject.scope)
        row = rows.get(marker)
        if row is None:
            row = {
                "subject": subject,
                "display_name": None,
                "facts": 0,
                "active_facts": 0,
                "reflections": 0,
                "active_reflections": 0,
                "persona": False,
                "prompt_locale": False,
                "corrections": 0,
                "staged": False,
            }
            rows[marker] = row
        return row

    for fact in facts_full:
        if not isinstance(fact, dict):
            continue
        subject = subject_from_entry(fact)
        row = _row(subject) if subject is not None else None
        if row is None:
            continue
        row["facts"] += 1
        if id(fact) in active_row_refs and not fact.get("subject_archived_at"):
            row["active_facts"] += 1
    for reflection in reflections:
        if not isinstance(reflection, dict):
            continue
        subject = subject_from_entry(reflection)
        row = _row(subject) if subject is not None else None
        if row is not None:
            row["reflections"] += 1
            status = reflection.get("status")
            # Corrupt unhashable states (list/object) count as nonterminal, preventing one bad reflection from breaking listing with 500.
            if not isinstance(status, str) or status not in REFLECTION_TERMINAL_STATUSES:
                row["active_reflections"] += 1
    persona_entries: list[dict] = []
    for section_key, section in persona.items():
        if not isinstance(section, dict) or not str(section_key).startswith(
            SCOPED_PERSONA_PREFIX,
        ):
            continue
        section_subject = persona_subject_from_section(section_key, section)
        raw_entries = section.get("facts")
        # A section's corrupt non-list facts count as empty, never breaking the entire listing with 500.
        entries = [
            entry for entry in (raw_entries if isinstance(raw_entries, list) else [])
            if isinstance(entry, dict)
        ]
        persona_entries.extend(entries)
        # Section keys exclude scope: different scopes sharing kind:id may share a section, whose
        # metadata tracks only latest writer. Record each item's own subject stamp, or persona-only subjects in other scopes disappear.
        for entry in entries:
            entry_subject = subject_from_entry(entry)
            entry_row = _row(entry_subject) if entry_subject is not None else None
            if entry_row is not None:
                entry_row["persona"] = True
        if section_subject is None:
            continue
        has_entries = any(
            entry_matches_subject(entry, section_subject) for entry in entries
        )
        display_name = section.get("display_name")
        if not has_entries and not display_name:
            continue
        row = _row(section_subject)
        if row is None:
            continue
        if has_entries:
            row["persona"] = True
        if isinstance(display_name, str) and display_name:
            row["display_name"] = display_name
    # Language-only subjects (no extracted facts, or interrupted keyed generation leaving reserved language rows) also fall
    # within scoped_forget deletion scope and must be discoverable/clearable.
    for locale_subject in locale_subjects:
        row = _row(locale_subject)
        if row is not None:
            row["prompt_locale"] = True
    # Pending persona corrections also fall within deletion scope (resolution can restore deleted persona entries); subjects with only these
    # must remain discoverable/clearable.
    for correction_subject in correction_subjects:
        row = _row(correction_subject)
        if row is not None:
            row["corrections"] += 1
    # Staging left by crashes after keyed generation but before first application may be a subject's sole data: clear targets it,
    # and same-key retries can still apply it, so it must remain discoverable too.
    for staged_subject in staged_subjects:
        row = _row(staged_subject)
        if row is not None:
            row["staged"] = True
    last_writes, _no_timestamp = collect_subject_last_writes(
        [facts_full, reflections, persona_entries],
    )
    subjects = []
    for marker in sorted(rows, key=lambda m: (rows[m]["subject"].kind, m)):
        row = rows[marker]
        subject = row["subject"]
        last = last_writes.get(marker)
        subjects.append({
            "subject_kind": subject.kind,
            "subject_id": subject.subject_id,
            "scope": subject.scope,
            "display_name": row["display_name"],
            "facts": row["facts"],
            "reflections": row["reflections"],
            "persona": row["persona"],
            "prompt_locale": row["prompt_locale"],
            "corrections": row["corrections"],
            "staged": row["staged"],
            "last_write_at": last[1].isoformat() if last is not None else None,
            # Facts remain only in archives; no active facts / reflections / persona.
            # Terminal reflections (promoted / denied, etc.) are listed/counted in reflections but excluded from active data.
            "archived": (
                row["facts"] > 0
                and row["active_facts"] == 0
                and row["active_reflections"] == 0
                and not row["persona"]
            ),
        })
    return {"subjects": subjects}
