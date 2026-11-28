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
"""Internal trust profile and speaker identity pool management routes."""

import re
from fastapi import HTTPException
from pydantic import BaseModel, Field
from .runtime import app

from . import routes as _routes
from .scoped_trust_helpers import _count_stranded_rows

class TrustLegacyImportRequest(BaseModel):
    source: str
    platform: str
    chunk_index: int = Field(default=0, ge=0)
    final: bool = False
    # Deliberately ``dict``, not a strict sub-model: the legacy normalizer this
    # replaces was per-field tolerant, and all-or-nothing validation would let a
    # single dirty profile 422 the whole request — which wedges the migration
    # permanently, because a 422 never succeeds on retry either.
    profiles: dict = Field(default_factory=dict)


class TrustWaiveBarrierRequest(BaseModel):
    platform: str


class TrustReconcileRequest(BaseModel):
    character_names: list[str] | None = None


class IdentityBindRequest(BaseModel):
    account_id: str
    entity_id: str
    bound_by: str | None = None
    require_unbound: bool = False


class IdentityAccountRequest(BaseModel):
    account_id: str
    require_provenance: bool = False


class IdentityMergeRequest(BaseModel):
    entity_id: str
    other_entity_id: str


class IdentityScopeDeclareRequest(BaseModel):
    platform: str
    channel: str
    actor_scope: str
    conversation_scope: str
    asserted_by: str


class IdentityEntityRequest(BaseModel):
    entity_id: str


def _identity_error(exc) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _require_loaded_identity_pool() -> None:
    """Refuse identity mutations while the pool is read-only degraded.

    ``_with_pool_write`` vetoes the write and returns ``persisted=False``, but
    the endpoints would still answer 200 — so a human-triggered bind/unbind/
    merge/forget silently becomes a no-op that reads as success. For unbind it
    is worse than a no-op: ``_count_stranded_rows`` also resolves nothing on an
    unloaded snapshot, so the operator's only remediation signal comes back as
    a confident ``0``.

    Same fail-closed rule already applied to ``scoped_forget``.
    """
    from memory import trust_store

    if not trust_store.trust_snapshot().loaded:
        raise HTTPException(
            status_code=503,
            detail=(
                "identity pool unreadable; identity changes are refused while "
                "the trust pool is read-only, retry once it loads"
            ),
        )


@app.get("/internal/trust/profile")
async def get_trust_profile(account_id: str):
    """Read-only diagnostics for one account. Never returns the ledger rings."""
    from memory import trust_store

    return trust_store.trust_snapshot().profile(account_id)


@app.post("/internal/trust/import_legacy_profiles")
async def import_legacy_trust_profiles(req: TrustLegacyImportRequest):
    """Import one chunk of a platform's legacy trust ledger.

    Additive merge, idempotent per (source, account_id). Safe to merge rather
    than overwrite ONLY because the barrier guarantees this platform had zero
    server-side evolution beforehand — that is the barrier's entire purpose.
    A malformed profile lands in ``skipped``; the request never 422s as a whole.
    """
    from config import SPEAKER_TRUST_LEGACY_IMPORT_CHUNK_MAX
    from memory import trust_store

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", req.platform or ""):
        raise HTTPException(status_code=422, detail="invalid platform")
    if not (req.source or "").strip():
        raise HTTPException(status_code=422, detail="source is required")
    if len(req.profiles) > SPEAKER_TRUST_LEGACY_IMPORT_CHUNK_MAX:
        # Chunking is the caller's job; exceeding it is a contract bug.
        raise HTTPException(
            status_code=422,
            detail=(
                f"profiles must contain at most "
                f"{SPEAKER_TRUST_LEGACY_IMPORT_CHUNK_MAX} items"
            ),
        )
    return await trust_store.aimport_legacy_profiles(
        platform=req.platform,
        source=req.source,
        profiles=req.profiles,
        final=bool(req.final),
    )


@app.post("/internal/trust/waive_legacy_barrier")
async def waive_legacy_trust_barrier(req: TrustWaiveBarrierRequest):
    """Escape hatch: give up on a platform's legacy import and open its gate."""
    from memory import trust_store

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", req.platform or ""):
        raise HTTPException(status_code=422, detail="invalid platform")
    return await trust_store.awaive_legacy_barrier(req.platform)


@app.post("/internal/trust/reconcile_from_facts")
async def reconcile_trust_from_facts(req: TrustReconcileRequest):
    """Disaster recovery only — manual trigger, never automatic.

    NOT a complete self-healer: ``scoped_forget`` deletes the fact rows that
    carry ``_speaker_trust_signal_events``, so signals on a forgotten subject
    have no reconstruction source. Correctness comes from the
    ``trust.persisted`` round-trip and the caller's retain-and-retry, not from
    this endpoint.
    """
    from memory import trust_store

    if _routes.runtime.fact_store is None:
        raise HTTPException(
            status_code=503,
            detail="memory_server not fully initialized (limited mode or startup incomplete)",
        )
    names = req.character_names
    if not names:
        character_data = await _routes.runtime._config_manager.aload_characters()
        names = list(character_data.get("猫娘", {}).keys())
    return await trust_store.areconcile_from_facts(
        _routes.runtime.fact_store, [_routes.validate_lanlan_name(name) for name in names],
    )


@app.post("/internal/identity/scope")
async def declare_identity_scope(req: IdentityScopeDeclareRequest):
    """Record what a platform's identifiers mean on the wire.

    A connector may call this on every startup: the declaration is a transcript
    of the vendor's published protocol, so it is a constant of the connection
    mode rather than something learned from traffic. Re-declaring the same
    tuple writes nothing.

    This is emphatically NOT a place to report an observation. The request body
    carries no account id and no sample precisely so that "we saw two different
    ids, so it must be per_conversation" cannot be expressed — see the kill list
    in ``memory.trust_store``. Deriving a scope from traffic and posting it here
    would launder an inference into an assertion, and downstream consumers show
    this value to the operator as ground truth.
    """
    from memory import trust_store

    _require_loaded_identity_pool()
    try:
        return await trust_store.adeclare_platform_identity_scope(
            req.platform,
            channel=req.channel,
            actor_scope=req.actor_scope,
            conversation_scope=req.conversation_scope,
            asserted_by=req.asserted_by,
        )
    except trust_store.TrustIdentityError as exc:
        raise _identity_error(exc) from exc


@app.post("/internal/identity/accounts/ensure")
async def ensure_identity_account(req: IdentityAccountRequest):
    """Register one account so it has an entity to be bound to.

    ``bind`` takes an entity id and 404s on an unknown one, but an entity is
    only born from ledger activity -- so a roster account that has never
    accrued a trust event has none, and on a fresh install that describes the
    very account everything else needs to merge INTO (the owner authorised in
    DMs). This is the seam that lets the dashboard offer it as a merge target.

    Creating the seed entity is not an edge and asserts nothing about who the
    person is: it links the account to itself. The human assertion is the bind
    that follows. No channel is recorded either -- ``channels_seen`` is an
    observation of traffic, and this call is not traffic.
    """
    from memory import trust_store

    _require_loaded_identity_pool()
    entity_id, persisted = await trust_store.aensure_account(
        req.account_id, report_persisted=True,
    )
    if entity_id is None:
        raise HTTPException(status_code=422, detail="invalid account_id")
    # Pass ``persisted`` through like bind/unbind do. Without it a failed disk
    # write is invisible here and only surfaces one step later as the bind's
    # 404 on an unknown entity -- the operator is then told "merge failed"
    # instead of "the write failed", which points at the wrong thing.
    return {
        "account_id": req.account_id,
        "entity_id": entity_id,
        "persisted": persisted,
    }


@app.post("/internal/identity/accounts/bind")
async def bind_identity_account(req: IdentityBindRequest):
    """Link one account to an entity. HUMAN-TRIGGERED ONLY.

    The number of automatic bind paths is zero and must stay zero. Never derive
    an edge from a nickname, a bootstrap elevation, temporal adjacency, an edit
    distance, or the observed channel — see the kill list in
    ``memory.trust_store``. A dashboard offering candidates must rank them by
    LEDGER WEIGHT ONLY and pre-select nothing; ranking by name similarity would
    hand the user a rejected heuristic as the default answer.

    Operational note, and both halves have to be stated together: on the TRUST
    axis bind EARLY (a late bind lets self-attested signals accumulate first,
    and merge does not refund them), while on the SUBJECT axis bind LATE (rows
    written while a wrong binding is active stay in the canonical pile
    irreversibly). Publishing only one of these is worse than publishing both.
    """
    from memory import trust_store

    _require_loaded_identity_pool()
    try:
        return await trust_store.abind_account(
            req.account_id, req.entity_id, bound_by=req.bound_by,
            require_unbound=bool(req.require_unbound),
        )
    except trust_store.TrustIdentityError as exc:
        raise _identity_error(exc) from exc


@app.post("/internal/identity/accounts/unbind")
async def unbind_identity_account(req: IdentityAccountRequest):
    """Detach one account into a fresh entity. The only rollback for a mis-bind.

    Returns BOTH ``ledger_delta`` and ``effective_delta``, and they are usually
    different numbers — that is not a bug to be "fixed". Under a clamped
    aggregate, "how much did this account take with it" has no unique answer:
    removing an account can move the effective score by more than the clamp
    itself, because it also releases the other accounts' saturation. An operator
    given only the ledger number can never reconcile it with the score.

    Known under-count: the activity write-amplification no-op skips recording
    event ids once the ENTITY is saturated, so an account unbound below the cap
    may have those messages counted again later. Bounded by
    ``SPEAKER_TRUST_ACTIVITY_MAX_BONUS`` (0.02), far under the 0.15 margin.
    """
    from memory import trust_store

    _require_loaded_identity_pool()
    snapshot_before = trust_store.trust_snapshot()
    try:
        result = await trust_store.aunbind_account(
            req.account_id,
            require_provenance=bool(req.require_provenance),
        )
    except trust_store.TrustIdentityError as exc:
        raise _identity_error(exc) from exc
    result["stranded_rows"] = await _count_stranded_rows(
        req.account_id, snapshot_before,
    )
    return result


@app.post("/internal/identity/entities/merge")
async def merge_identity_entities(req: IdentityMergeRequest):
    """Merge two entities. HUMAN-TRIGGERED ONLY. Idempotent/commutative/associative."""
    from memory import trust_store

    _require_loaded_identity_pool()
    try:
        return await trust_store.amerge_entities(
            req.entity_id, req.other_entity_id,
        )
    except trust_store.TrustIdentityError as exc:
        raise _identity_error(exc) from exc


@app.post("/internal/identity/entities/forget")
async def forget_identity_entity(req: IdentityEntityRequest):
    """Drop one entity's identity records. Minimal by design.

    Known weakness, stated rather than hidden: the signal EVENTS themselves live
    on other people's fact rows and in the archive, so if the same account comes
    back and the owner repeats the same sentence, a "forgotten" correction is
    applied again.
    """
    from memory import trust_store

    _require_loaded_identity_pool()
    try:
        return await trust_store.aforget_entity(req.entity_id)
    except trust_store.TrustIdentityError as exc:
        raise _identity_error(exc) from exc
