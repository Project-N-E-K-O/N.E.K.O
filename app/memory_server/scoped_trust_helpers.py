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
"""Scoped memory request schemas, trust routing helpers, and display-name helpers."""

from typing import Annotated, Literal
from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import routes as _routes

class MemorySubjectRequest(BaseModel):
    subject_kind: Literal["group_chat", "participant", "group_participant"]
    subject_id: str
    scope: str | None = None

    def to_domain(self):
        from memory.scopes import MemoryScopeError, MemorySubject
        try:
            return MemorySubject.create(
                self.subject_kind, self.subject_id, scope=self.scope,
            )
        except MemoryScopeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


class ScopedFactInput(BaseModel):
    text: str
    importance: int = Field(default=5, ge=1, le=10)
    source: Literal["user_observation", "ai_disclosure"] = "user_observation"


class ScopedFactsWriteRequest(BaseModel):
    subject: MemorySubjectRequest
    facts: list[ScopedFactInput]
    language: str | None = None
    # Optional human-readable name for the subject (group name / member
    # nickname). Untrusted user data: sanitized like speaker_label, then
    # stamped onto the subject's existing persona section metadata so the
    # rendered section header can show a name instead of the bare id.
    # Purely cosmetic — never part of the isolation key.
    display_name: str | None = None


#: Wire-side anchored pattern. pydantic v2 compiles ``Field(pattern=...)`` with
#: the Rust regex crate under UNANCHORED SEARCH semantics, so a bare
#: ``[A-Za-z0-9_.:-]+`` accepts ``'participant:猫娘 A:12:34:56'`` and even values
#: containing newlines — i.e. it is zero validation. The anchors must be
#: ``\A...\z`` (LOWERCASE z) or ``^...$``: the Rust crate does not recognise
#: ``\Z`` and raises ``SchemaError`` at model-definition time. Note that
#: ``memory/identity.py`` goes through Python's ``re``, where ``\A...\Z`` is
#: correct — the two layers must NOT share a pattern string.
_ACTIVITY_EVENT_ID_PATTERN = r"\A[A-Za-z0-9_.:-]+\z"


_SPEAKER_CHANNEL_PATTERN = r"\A[a-z0-9_]{1,16}\z"


_IDEMPOTENCY_KEY_PATTERN = r"\A[A-Za-z0-9_.:-]+\z"


class ActivityEvent(BaseModel):
    """One idempotent per-message activity token.

    Per-message rather than per-batch: the old batch-level identity changed
    whenever a retry grew the batch, so already-acknowledged prefixes got
    counted again — which is the entire reason the plugin grew a three-layer
    ``cancelled.speaker_trust_persisted`` protocol. Deduplicating by id on the
    server makes an amplified retry harmless by construction.
    """

    id: str = Field(
        min_length=8, max_length=96, pattern=_ACTIVITY_EVENT_ID_PATTERN,
    )
    count: int = Field(default=1, ge=1, le=1000)


class ScopedHistorySegment(BaseModel):
    """One single-speaker slice of a batched /scoped_history request."""
    input_history: str
    subject: MemorySubjectRequest
    # Required per segment: the batch prompt attributes facts by segment,
    # and a segment IS one speaker's bucket — an unlabeled segment would
    # render anonymous turns the model cannot attribute.
    speaker_label: str
    # Legacy caller-computed trust. Kept only so a not-yet-flipped plugin build
    # keeps working; mutually exclusive with the server-derived source below.
    speaker_trust: float | None = Field(default=None, ge=0.0, le=1.0)
    # Server-derived trust source, exactly one of these two:
    #   * speaker_tier — platforms with a four-rung permission ladder. A Literal
    #     so a mistyped "Admin" 422s instead of silently landing on a default.
    #   * speaker_base_trust — platforms without a ladder (danmaku guard_level,
    #     medal level). Clamped server-side to SPEAKER_TRUST_MAX_REPORTED_BASE.
    speaker_tier: Literal["admin", "trusted", "normal", "none"] | None = None
    speaker_base_trust: float | None = Field(default=None, ge=0.0, le=1.0)
    # Per-message idempotent activity tokens for this speaker.
    speaker_activity_events: list[ActivityEvent] | None = None
    # Observed transport ("napcat" / "open"). An OBSERVED ATTRIBUTE, never a
    # key: it takes part in no ledger partitioning, no bind/merge predicate and
    # no permission decision. Its only jobs are collision detection and ops
    # diagnostics.
    speaker_channel: str | None = Field(
        default=None, pattern=_SPEAKER_CHANNEL_PATTERN,
    )
    # Stable internal identity. Unlike speaker_label this never enters a prompt.
    speaker_id: str | None = None
    # Request-side authorization bit. It is never rendered or copied from LLM
    # output; only owner-authored raw text may evolve another speaker's trust.
    speaker_is_owner: bool = False
    # Full fact identities authored after this retained owner's observation.
    # Bare ids are not unique across participant scopes.
    trust_signal_excluded_fact_identities: list[
        tuple[str, str, str, str]
    ] = Field(default_factory=list)
    # Optional display name for this segment's subject (see
    # ScopedFactsWriteRequest.display_name).
    display_name: str | None = None


class ScopedHistoryRequest(BaseModel):
    # Legacy single-subject shape (group digests still use it): both fields
    # required together. Optional at the model level only because the
    # batched shape below replaces them; the endpoint 422s when neither
    # shape is complete.
    input_history: str | None = None
    subject: MemorySubjectRequest | None = None
    # Optional speaker identity for single-speaker batches (group-member
    # buckets, private participant digests). The extraction prompt otherwise
    # renders every 'user' turn as the configured private-chat master and
    # extracts facts about the master, misattributing member statements.
    # Group digests omit it — their turns already carry per-message speaker
    # headers in the content.
    speaker_label: str | None = None
    # Optional 0..1 initial trust for the single speaker (same field the
    # batched segments carry; stage one of the speaker-trust mechanism).
    # Only meaningful alongside speaker_label — without a speaker there is
    # no one to trust, so the handler drops it when the label is absent.
    speaker_trust: float | None = Field(default=None, ge=0.0, le=1.0)
    # Server-derived trust source (see ScopedHistorySegment for the contract).
    speaker_tier: Literal["admin", "trusted", "normal", "none"] | None = None
    speaker_base_trust: float | None = Field(default=None, ge=0.0, le=1.0)
    speaker_activity_events: list[ActivityEvent] | None = None
    speaker_channel: str | None = Field(
        default=None, pattern=_SPEAKER_CHANNEL_PATTERN,
    )
    speaker_id: str | None = None
    speaker_is_owner: bool = False
    # Optional display name for the single-subject shape's subject (see
    # ScopedFactsWriteRequest.display_name). Group digests pass the group
    # name here.
    display_name: str | None = None
    # Batched multi-speaker shape: one extraction call covers every segment,
    # each dispatched back to its own subject. Mutually exclusive with the
    # legacy fields. Internal endpoint (the QQ plugin is the only caller,
    # shipped in the same deployment), but the legacy shape stays anyway —
    # the group-digest paths keep using it unchanged.
    segments: list[ScopedHistorySegment] | None = None
    language: str | None = None
    # Optional idempotency key (both shapes). Absent: the request behaves
    # exactly as before and the two fields below are ignored. Present: the
    # handler switches to the generate-then-apply journal (see
    # ``_process_scoped_history_keyed``). Same anchoring rule as
    # ``_ACTIVITY_EVENT_ID_PATTERN`` (Rust regex: ``\A...\z``).
    idempotency_key: str | None = Field(
        default=None, min_length=1, max_length=128,
        pattern=_IDEMPOTENCY_KEY_PATTERN,
    )
    # Diagnostics only. Never compared with anything: ordering against a
    # forget is decided by ``subject_epochs`` alone, so neither side's clock
    # (skew, rollback) can let a stale write through.
    client_requested_at: float | None = None
    # ``{MemorySubject.key: forget_epoch}`` captured by the caller when the
    # round started; reused verbatim by every retry of the same key.
    subject_epochs: dict[str, Annotated[int, Field(ge=0)]] | None = None


def _resolve_trust_source(
    source, *, position: str, speaker_id: str | None,
) -> dict:
    """Validate one segment's trust source and normalize it. 422 on conflict.

    The legacy caller-computed ``speaker_trust`` channel stays accepted while a
    not-yet-flipped plugin build may be in the field, and mutual-exclusion 422s
    make each request self-describing about which protocol it speaks. Removing
    the legacy field entirely is a separate, release-timed change.
    """
    tier = getattr(source, "speaker_tier", None)
    base = getattr(source, "speaker_base_trust", None)
    legacy = getattr(source, "speaker_trust", None)
    channel = getattr(source, "speaker_channel", None)
    raw_events = getattr(source, "speaker_activity_events", None) or []
    has_server_source = tier is not None or base is not None

    if tier is not None and base is not None:
        raise HTTPException(
            status_code=422,
            detail=f"{position}speaker_tier and speaker_base_trust are exclusive",
        )
    if legacy is not None and has_server_source:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{position}speaker_trust is exclusive with the "
                f"server-derived trust source"
            ),
        )
    if has_server_source and not speaker_id:
        # Same rule the label path already states: without a speaker there is
        # nobody to trust.
        raise HTTPException(
            status_code=422,
            detail=f"{position}trust source requires a valid speaker_id",
        )
    if raw_events and not has_server_source:
        raise HTTPException(
            status_code=422,
            detail=f"{position}speaker_activity_events requires a trust source",
        )
    if channel is not None and not has_server_source:
        raise HTTPException(
            status_code=422,
            detail=f"{position}speaker_channel requires a trust source",
        )
    if (
        getattr(source, "speaker_is_owner", False)
        and has_server_source
        and tier != "admin"
    ):
        # Hardening: with the tier on the wire no platform can mint an owner
        # channel by nickname matching, and the unauthenticated self-reported
        # base channel can never grant signing power over other speakers.
        raise HTTPException(
            status_code=422,
            detail=f"{position}speaker_is_owner requires the admin tier",
        )
    # Repeated ids inside one batch are legitimate (identical text sent twice),
    # so deduplicate rather than reject.
    events: list = []
    seen: set[str] = set()
    for event in raw_events:
        if event.id not in seen:
            seen.add(event.id)
            events.append(event)
    return {
        "tier": tier,
        "base": base,
        "legacy": legacy,
        "channel": channel,
        "activity_events": tuple(events),
        "has_server_source": has_server_source,
    }


async def _count_stranded_rows(account_id, snapshot_before) -> int | None:
    """Rows this account wrote into someone else's pile while routing was on.

    The one remediation signal an operator gets for the irreversible surface:
    rows written during a binding carry the CANONICAL subject_id and this
    account's ``speaker_id``, and after an unbind they stay there. There is
    deliberately no move-back endpoint — moving a row means recomputing its
    subject-salted hash, which can collapse it into an existing row at the
    destination. So the operator has to be told a count and decide whether the
    nuclear option (``scoped_forget``) is warranted.

    Best-effort: returns ``None`` if the scan cannot run. An unbind must never
    fail because a diagnostic count did.
    """
    from memory.identity import account_platform, normalize_account_id
    from memory.scopes import subject_from_entry
    from memory.subject_identity import subject_actor

    normalized = normalize_account_id(account_id)
    if normalized is None or _routes.runtime.fact_store is None:
        return None
    entity_id = snapshot_before.entity_of(normalized)
    if entity_id is None:
        return 0
    platform = account_platform(normalized)
    canonical = snapshot_before.canonical_account(entity_id, platform)
    if not canonical or canonical == normalized:
        # This account WAS the canonical, so nothing of its was ever routed
        # away from its own subject.
        return 0
    canonical_actor = str(canonical).partition(":")[2]
    try:
        character_data = await _routes.runtime._config_manager.aload_characters()
        names = list(character_data.get("猫娘", {}).keys())
    except Exception as exc:  # noqa: BLE001 - diagnostics must not break unbind
        _routes.logger.warning(f"[Identity] stranded_rows 无法枚举角色: {exc}")
        return None
    stranded = 0
    for name in names:
        try:
            # ``aload_facts_full`` = active + archived. Rows routed into the
            # canonical pile can have aged into ``facts_archive.json`` already,
            # and counting only the active file would report zero for an
            # account whose stranded copies all archived — while this count is
            # the operator's only cue that ``scoped_forget`` is still needed.
            # The loader already collapses rows present in both files and
            # degrades to active-only on a corrupt archive.
            rows = await _routes.runtime.fact_store.aload_facts_full(name, include_cold_stubs=True)
        except Exception as exc:  # noqa: BLE001 - same
            _routes.logger.warning(f"[Identity] stranded_rows 读取 {name} 失败: {exc}")
            return None
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            if normalize_account_id(row.get("speaker_id")) != normalized:
                continue
            subject = subject_from_entry(row)
            if subject is None:
                continue
            if subject.subject_id.split(":")[0] != platform:
                continue
            # Through the DECODING accessor, never a raw segment compare: the
            # subject constructors percent-encode ``:``, so an actor that
            # legitimately contains one reads ``a%3Ab`` here while the account
            # id is ``a:b`` — a raw compare would silently never match and
            # report zero stranded rows.
            if subject_actor(subject) == canonical_actor:
                stranded += 1
    return stranded


def _merge_forget_stats(stats: dict, delta) -> None:
    """Accumulate one target's forget counters into the running total.

    Numeric counters add; booleans OR (a store either did or did not act);
    anything else keeps the last non-null value. Never silently replaces a
    non-zero count with a later zero.
    """
    if not isinstance(delta, dict):
        return
    for key, value in delta.items():
        current = stats.get(key)
        if isinstance(value, bool) or isinstance(current, bool):
            stats[key] = bool(current) or bool(value)
        elif isinstance(value, (int, float)) and isinstance(
            current, (int, float),
        ):
            stats[key] = current + value
        elif value is not None or key not in stats:
            stats[key] = value


def _forget_fanout_targets(subject):
    """Every subject a forget must erase, in a deterministic total order.

    Fans out to the WHOLE PARTICIPANT (maintainer decision): a participant is
    (entity × conversation) and is one isolation unit, so it must be one unit on
    the delete axis too. Erasing only the requested subject would also fail to
    be a real erase once canonical write routing is active, because the routed
    rows sit in the canonical account's pile — "left the group, wipe my data"
    would silently keep a copy.

    NEVER CROSSES A PLATFORM (also a maintainer decision). A conversation id is
    itself platform-prefixed, so a cross-platform account is structurally never
    part of this participant and ``expand_subject`` already filters on it. The
    assertion below makes that a CHECKED property rather than a coincidence, and
    is the seam where a future opt-in cross-platform sweep would plug in — that
    sweep is deliberately a separate, explicitly-requested operation, not a side
    effect of leaving one group.

    Sorted by ``(key, scope)`` so concurrent forgets acquire the per-subject
    locks in the same order and cannot deadlock; the requested subject is
    guaranteed present because expansion only ever grows the set.
    """
    from memory.subject_identity import expand_subject
    from memory import trust_store

    expanded = expand_subject(subject, trust_store.trust_snapshot())
    requested_platform = subject.subject_id.split(":")[0]
    targets = []
    for candidate in expanded:
        if candidate.subject_id.split(":")[0] != requested_platform:
            _routes.logger.warning(
                "[scoped_forget] 跳过跨平台扇出目标 %s（forget 不跨平台）",
                candidate.subject_id,
            )
            continue
        targets.append(candidate)
    if not any(
        (target.key, target.scope) == (subject.key, subject.scope)
        for target in targets
    ):  # pragma: no cover - expansion always contains the requested subject
        targets.append(subject)
    return sorted(targets, key=lambda item: (item.key, item.scope))


def _fold_request_subjects(wire_subjects):
    """``wire subjects -> (participant groups, flattened authorization list)``.

    Read-side expansion is never truncated: a participant's marker set is every
    account of that (entity, conversation), because a "first K" rule would make
    the set depend on which account the request happened to start from, and
    filtering is a set-membership test whose cost does not grow with set size
    anyway. The bound lives at bind time.
    """
    from memory.scopes import flatten_groups
    from memory.subject_identity import fold_participants
    from memory import trust_store

    domain = [subject.to_domain() for subject in wire_subjects]
    groups = fold_participants(domain, trust_store.trust_snapshot())
    return groups, list(flatten_groups(groups))


async def _trust_snapshot_for_request():
    """One pool snapshot per request. A single atomic attribute read."""
    from memory import trust_store

    return trust_store.trust_snapshot()


def _apply_canonical_write_routing(parsed: dict, snap) -> None:
    """Route this segment's write to the participant's canonical subject.

    READ-ONLY against the snapshot: it resolves the canonical subject and
    rewrites ``parsed["subject"]``, and seals NOTHING itself. R-CANON-1 lazy
    sealing lives in ``trust_store._apply_trust_mutations_locked``, inside the
    pool's critical section, so it shares that handler's single file write.

    A consequence worth stating on a function labelled IRREVERSIBLE SURFACE:
    ``/scoped_facts`` calls this but carries no trust mutation, so it never
    seals — it only routes by an ALREADY sealed canonical. That is correct, but
    it is not what "lazily seals on first write" would lead a reader to expect.

    IRREVERSIBLE SURFACE, stated plainly: rows written while routing is active
    carry the CANONICAL subject_id and the REAL account's speaker_id. After an
    unbind those rows stay in somebody else's pile, and there is deliberately no
    move-back endpoint — moving a row requires recomputing its subject-salted
    hash, which can collapse it into an existing row at the destination, i.e.
    exactly the trap being avoided. ``unbind`` reports a ``stranded_rows``
    count; the nuclear option is ``scoped_forget``.
    """
    from memory.subject_identity import canonical_subject

    subject = parsed.get("subject")
    if subject is None:
        return
    routed = canonical_subject(subject, snap)
    if routed is not subject and (
        routed.key != subject.key or routed.scope != subject.scope
    ):
        parsed["subject"] = routed
        parsed["canonical_routed"] = True


def _stamp_resolved_trust(parsed: dict, snap) -> None:
    """Resolve this segment's trust from the pool and stamp it, or abstain.

    The key name ``speaker_trust`` is unchanged on purpose: ``FactStore``'s
    ``_speaker_provenance_of`` / ``extract_facts`` / ``extract_facts_batch``
    then need no change at all.

    ``None`` means DO NOT WRITE THE KEY. Falling back to a default would stamp
    a finite value on rows that legitimately carry none today (group digests
    already go through a shape that omits it), flipping arbitration from
    abstention to an active vote.
    """
    # From the SAME snapshot that routed the subject — one pool read per
    # request (§4.4), so the stamp stays a pure function of the request-start
    # state even if a bind/unbind lands mid-request.
    speaker_id = parsed.get("speaker_id")
    if speaker_id:
        entity_id = snap.entity_of(speaker_id)
        if entity_id:
            parsed["speaker_entity_id"] = entity_id
    source = parsed.get("trust_source") or {}
    if not source.get("has_server_source"):
        return
    resolved = snap.resolve_trust(
        parsed.get("speaker_id"),
        tier=source.get("tier"),
        base=source.get("base"),
    )
    if resolved is None:
        # Either the platform's legacy barrier is still pending, or the id is
        # unusable. Both abstain; only the former is worth reporting.
        parsed["speaker_trust"] = None
        from memory.identity import account_platform

        if parsed.get("speaker_id") and snap.barrier_pending(
            account_platform(parsed["speaker_id"])
        ):
            parsed["trust_gated"] = "legacy_import_pending"
        return
    parsed["speaker_trust"] = resolved


def _trust_mutation_for(parsed: dict) -> "object | None":
    """Build the pool mutation for one parsed segment, or ``None``."""
    from memory.trust_store import ActivityEvent as PoolActivityEvent
    from memory.trust_store import TrustMutation

    speaker_id = parsed.get("speaker_id")
    trust_source = parsed.get("trust_source") or {}
    signal_events = tuple(parsed.get("trust_signal_events") or ())
    activity = tuple(
        PoolActivityEvent(id=event.id, count=event.count)
        for event in (parsed.get("trust_activity_events") or ())
    )
    if not signal_events and not activity and not trust_source.get("channel"):
        return None
    return TrustMutation(
        speaker_account_id=speaker_id,
        activity_events=activity,
        signal_events=signal_events,
        channel=trust_source.get("channel"),
    )


async def _apply_trust_for_segments(parsed_segments):
    """Fold the whole batch into the pool with ONE write. Never raises.

    Returns ``(result, outcomes)`` where ``outcomes`` is aligned index-for-index
    with ``parsed_segments`` so each segment can report its own numbers.

    This is the handler's last durable write and it comes after every FactStore
    call. The keyed path calls it while holding the fact persistence lock
    (key_lock -> persist_alock -> pool lock); the pool lock stays a leaf
    because nothing is acquired and no FactStore code runs while it is held.
    Any failure before this point shows up as "trust did not move at all".
    """
    from memory.trust_store import MutationOutcome

    mutations = []
    positions = []
    for index, segment in enumerate(parsed_segments):
        mutation = _trust_mutation_for(segment)
        if mutation is not None:
            mutations.append(mutation)
            positions.append(index)
    outcomes = [MutationOutcome() for _ in parsed_segments]
    if not mutations:
        return None, outcomes
    from memory import trust_store

    result = await trust_store.aapply_trust_mutations(mutations)
    for position, outcome in zip(positions, result.per_mutation):
        outcomes[position] = outcome
    if not result.persisted:
        _routes.logger.warning(
            "[Trust] 池未落盘，本批 %d 段回传 persisted=false 让调用方保留重试",
            len(mutations),
        )
    return result, outcomes


def _trust_response_block(parsed: dict, result, outcome) -> dict:
    """The per-segment ``trust`` block.

    ``persisted`` drives the caller's retain-or-pop decision and MUST be
    reported honestly:

    | segment status | trust.persisted   | caller action        |
    |----------------|-------------------|----------------------|
    | ok             | true / null       | pop the bucket       |
    | ok             | false             | RETAIN and retry     |
    | failed / lost  | —                 | retain (unchanged)   |

    ``false`` has to reach the plugin because at-least-once delivery of owner
    signals depends on it: the replay ring in ``memory/facts.py`` is gated on
    ``observation_id``, which is a hash of one of the CURRENT request's owner
    messages — retry semantics, not time semantics. Always answering 200 and
    letting the caller pop would break that chain, and one disk hiccup would
    silently and permanently lose a ±0.04/0.08 owner correction.

    ``persisted=null`` means this segment had NOTHING to settle, which is
    different from "the write failed".

    The reported condition is "did this segment attempt a pool mutation", NOT
    "did it carry a tier". An owner segment sent before the migration push
    lands still carries ``speaker_is_owner`` with no ``speaker_tier``, and the
    route still evaluates, persists and folds its owner signals — reporting
    ``null`` for those would let the caller pop a bucket whose correction was
    deferred by the barrier or lost to a failed pool write.
    """
    trust_source = parsed.get("trust_source") or {}
    attempted = bool(
        trust_source.get("has_server_source")
        or parsed.get("trust_signal_events")
    )
    if not attempted:
        # Same key set as the branch below — a field that appears in only one
        # shape of the same response block is a contract a caller cannot write
        # against without a KeyError guard.
        return {
            "resolved": parsed.get("speaker_trust"),
            "persisted": None,
            "signals_applied": 0,
            "activity_applied": 0,
            "gated": None,
            "channel_collision": False,
        }
    return {
        "resolved": parsed.get("speaker_trust"),
        "persisted": bool(result.persisted) if result is not None else None,
        "signals_applied": int(getattr(outcome, "signals_applied", 0) or 0),
        "activity_applied": int(getattr(outcome, "activity_applied", 0) or 0),
        "gated": (
            "legacy_import_pending"
            if parsed.get("trust_gated")
            or int(getattr(outcome, "signals_deferred", 0) or 0) > 0
            else None
        ),
        "channel_collision": bool(
            getattr(outcome, "channel_collision", False)
        ),
    }


class ScopedContextRequest(BaseModel):
    subjects: list[MemorySubjectRequest]
    language: str | None = None


class ScopedMentionsRequest(BaseModel):
    response_text: str
    subjects: list[MemorySubjectRequest]


def _sanitized_display_name(raw: str | None, *, context: str) -> str | None:
    """Normalize an untrusted display_name from a scoped write request.

    Same length contract as speaker_label (>64 is a caller bug, fail loud);
    same structural-character neutralization (the value ends up in a prompt
    section header, the exact attack surface #2605 closed for speaker_label).
    Unlike speaker_label there is no fallback when sanitization empties it:
    the name is cosmetic, absent is a valid state.
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if len(value) > 64:
        raise HTTPException(
            status_code=422,
            detail=f"{context}: display_name must contain at most 64 characters",
        )
    from memory.facts import FactStore

    return FactStore.sanitize_speaker_label(value) or None


async def _stamp_subject_display_name(
    lanlan_name: str, subject, display_name: str | None, *, strict: bool = False,
) -> None:
    """Best-effort display-name refresh after a successful scoped write.

    Never fails the write: the facts are already persisted, and a display
    name is metadata the next write can supply again. ``strict`` (the keyed
    journal) re-raises instead: there the item is only marked applied once
    the name is persisted, and a same-key retry is the only next write.
    """
    if not display_name or _routes.runtime.persona_manager is None:
        return
    try:
        # strict raises on unreadable persona files (False means normal no-op, such as missing section / no change).
        await _routes.runtime.persona_manager.aupdate_subject_display_name(
            lanlan_name, subject, display_name, **({"strict": True} if strict else {}),
        )
    except Exception as exc:
        if strict:
            raise
        _routes.logger.warning(
            f"[scoped] display_name 刷新失败（忽略，写入已完成）: {exc}"
        )
