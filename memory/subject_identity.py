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

"""Resolve subjects into participants. The ONLY subject-layer trust_store importer.

ONE SENTENCE: the stored bytes of ``subject_id`` / ``scope`` never change.
"One entity has exactly one participant per conversation" is implemented in the
server's RESOLUTION layer — an incoming request folds its subject slots by
participant, a read expands each participant into a set of markers, and a write
routes the subject to one sealed canonical account. The plugin side changes not
at all.

THREE STRUCTURAL PRECONDITIONS (each verified against the code):

1. The server never cross-checks ``segment.speaker_id`` against a subject's
   third segment. They are independent wire fields, and ``memory/scopes.py``
   puts no constraint on the CONTENT of a ``group_participant``'s third segment
   beyond "three non-empty parts". Rewriting it is therefore structurally legal
   and needs zero validation changes.
2. Attribution has exactly one entry point, ``entry_matches_subject``, whose
   semantics are byte equality of ``(key, scope)`` — no aliases, no prefixes, no
   normalization. Its set form is a membership test, so a WIDER marker set costs
   O(1) to filter.
3. "Read several subjects and merge" is already a shipped production path
   (``scoped_context`` / ``query_memory`` / ``scoped_mentions``, each 1..8).
   Nothing new has to be invented.

THREE RULES THAT ARE EASY TO BREAK AND FATAL IF BROKEN
======================================================

**N-1 — domain restriction.** Folding and canonical routing apply ONLY to
default-scope subjects (``scope == f"{kind}:{subject_id}"``); anything else is
an identity no-op. (a) The plugin never sends a scope, so 100% of production
traffic is default-scope and the restriction costs nothing real; (b) a custom
scope is an isolation boundary the caller declared explicitly, and silently
redirecting it would void that boundary on their behalf; (c) the two subject
foldings already in the tree (``_fact_dedup_domain``, ``_in_signal_scope``) are
likewise default-scope-only, so this introduces no third dialect.

**N-2 — canonical must RE-DERIVE its scope.** ``MemorySubject`` is frozen+slots,
so the handy way to rewrite ``subject_id`` is ``dataclasses.replace`` — and that
keeps the OLD scope. Since attribution is byte equality of the ``(key, scope)``
PAIR, a replace-built subject lands in nobody's marker set, and every newly
written row becomes an orphan readable by no one: precisely the opposite of the
point. Canonical subjects are therefore always rebuilt through the
``MemorySubject.group_participant`` / ``.participant`` / ``.group_chat``
constructors, which derive the scope. ``dataclasses.replace`` is banned in this
module and there is a grep guard test for it.

**N-3 — expansion must be defensive.** ``stable_speaker_id`` allows a colon
inside the actor, while ``group_participant`` demands exactly three segments.
Recombining ``(platform, conversation, actor)`` can therefore raise
``MemoryScopeError``; those combinations are DROPPED and counted, never allowed
to 500 the whole read path.

NO READ-SIDE TRUNCATION (M-1). ``members`` is every account of the participant.
Any "take the first K" rule would make "starting from A₁" and "starting from A₅"
produce structurally unequal groups that value-dedup cannot rescue, and would
make one marker belong to two groups. The bound lives at bind time instead
(``IDENTITY_MAX_ACCOUNTS_PER_ENTITY_PER_PLATFORM``, 409 on overflow), which is
an acceptable failure mode for a rare human action in a way read-time
truncation is not.
"""

from __future__ import annotations

from typing import Any, Iterable

from memory.scopes import (
    SUBJECT_GROUP_CHAT,
    SUBJECT_GROUP_PARTICIPANT,
    SUBJECT_PARTICIPANT,
    MemoryScopeError,
    MemorySubject,
    ParticipantGroup,
)

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")

#: Bumped whenever an expansion combination is structurally unbuildable. Read by
#: the guard test for I-P-4; never a request-blocking condition.
expansion_drop_count = 0


def _is_default_scope(subject: MemorySubject) -> bool:
    return subject.scope == f"{subject.kind}:{subject.subject_id}"


def _decode_component(value: str) -> str:
    """Inverse of ``scopes._encode_component`` (``%3A`` first, then ``%25``)."""
    return value.replace("%3A", ":").replace("%25", "%")


def _split_subject(subject: MemorySubject) -> tuple[str, str | None, str | None]:
    """Return ``(platform, conversation, actor)`` in DECODED form.

    ``conversation`` / ``actor`` are ``None`` for the kinds that lack them
    (``participant`` has no conversation; ``group_chat`` has no actor). This is
    the symmetric treatment S2.c demands — the folding must not be
    ``group_participant``-only.
    """
    parts = subject.subject_id.split(":")
    if subject.kind == SUBJECT_GROUP_PARTICIPANT:
        if len(parts) != 3:
            return subject.subject_id, None, None
        return (
            _decode_component(parts[0]),
            _decode_component(parts[1]),
            _decode_component(parts[2]),
        )
    if len(parts) != 2:
        return subject.subject_id, None, None
    if subject.kind == SUBJECT_PARTICIPANT:
        return _decode_component(parts[0]), None, _decode_component(parts[1])
    if subject.kind == SUBJECT_GROUP_CHAT:
        return _decode_component(parts[0]), _decode_component(parts[1]), None
    return subject.subject_id, None, None


def _account_of(subject: MemorySubject, platform: str, actor: str) -> str | None:
    from memory.identity import normalize_account_id

    return normalize_account_id(f"{platform}:{actor}")


def _build(
    kind: str, platform: str, conversation: str | None, actor: str | None,
) -> MemorySubject | None:
    """Rebuild a subject from decoded components. N-2 and N-3 both live here."""
    global expansion_drop_count
    try:
        if kind == SUBJECT_GROUP_PARTICIPANT:
            return MemorySubject.group_participant(
                platform, str(conversation), str(actor),
            )
        if kind == SUBJECT_PARTICIPANT:
            return MemorySubject.participant(platform, str(actor))
        if kind == SUBJECT_GROUP_CHAT:
            return MemorySubject.group_chat(platform, str(conversation))
    except MemoryScopeError as exc:
        # N-3: an actor containing a colon (legal for stable_speaker_id) can
        # produce a four-segment group_participant id. Drop it loudly; never
        # let a read-path expansion 500 the endpoint.
        expansion_drop_count += 1
        logger.warning(
            "[SubjectIdentity] 丢弃不可构造的展开组合 kind=%s platform=%s "
            "conversation=%s: %s",
            kind, platform, conversation, exc,
        )
    return None


def participant_key(subject: MemorySubject, snap: Any = None) -> tuple:
    """The identity of the participant this subject belongs to.

    Degrades to ``(kind, subject_id, scope)`` — i.e. the identity — whenever the
    pool is unloaded, the account is unregistered, or the scope is non-default.
    That degradation IS the design's most important property (I-P-1), not a
    fallback: the input to this layer is the abstraction "entity relations", and
    an empty relation must yield the identity map.
    """
    identity = (subject.kind, subject.subject_id, subject.scope)
    if snap is None or not getattr(snap, "loaded", False):
        return identity
    if not _is_default_scope(subject):
        return identity
    platform, conversation, actor = _split_subject(subject)
    entity_id = None
    if actor is not None:
        account_id = _account_of(subject, platform, actor)
        if account_id is not None:
            entity_id = snap.entity_of(account_id)
    if entity_id is None and subject.kind != SUBJECT_GROUP_CHAT:
        return identity
    # ``conversation`` is currently its own raw id; conversation-entity binding
    # (the O(number of groups) half of S2) reuses this slot without changing
    # anything else here.
    return (subject.kind, entity_id or subject.subject_id, conversation)


def expand_subject(
    subject: MemorySubject, snap: Any = None,
) -> tuple[MemorySubject, ...]:
    """Every subject of this participant, canonical first. Never truncated."""
    if snap is None or not getattr(snap, "loaded", False):
        return (subject,)
    if not _is_default_scope(subject):
        return (subject,)
    platform, conversation, actor = _split_subject(subject)
    if actor is None:
        # group_chat: the conversation ontology is the axis that would expand
        # here. Until a conversation is bound this is the identity, and it must
        # stay symmetric with the other two kinds rather than being skipped.
        return (subject,)
    account_id = _account_of(subject, platform, actor)
    if account_id is None or snap.entity_of(account_id) is None:
        return (subject,)
    expanded: list[MemorySubject] = []
    seen: set[tuple[str, str]] = set()
    for member_account in snap.accounts_of(account_id):
        member_platform, _, member_actor = str(member_account).partition(":")
        if not member_actor:
            continue
        if member_platform != platform:
            # A participant is (entity × conversation), and a conversation id
            # is itself platform-prefixed, so a cross-platform account can
            # never be part of THIS participant. Filtering here makes that a
            # checked property instead of a coincidence.
            continue
        built = _build(subject.kind, platform, conversation, member_actor)
        if built is None:
            continue
        marker = (built.key, built.scope)
        if marker not in seen:
            seen.add(marker)
            expanded.append(built)
    # The originally requested subject is always in the set: the expansion may
    # only ever GROW the readable domain, never shrink it, otherwise existing
    # corpora would become unreachable orphans.
    if (subject.key, subject.scope) not in seen:
        expanded.append(subject)
    return tuple(expanded)


def canonical_subject(subject: MemorySubject, snap: Any = None) -> MemorySubject:
    """Route a write to the participant's sealed canonical subject.

    P-4 INTERLOCK — this MUST return ``subject`` unchanged when the pool is
    unloaded, the account is unregistered, or the scope is non-default. That
    interlock is what keeps the "unknown provenance" state from ever being
    recorded as "known mixed".
    """
    if snap is None or not getattr(snap, "loaded", False):
        return subject
    if not _is_default_scope(subject):
        return subject
    platform, conversation, actor = _split_subject(subject)
    if actor is None:
        return subject
    account_id = _account_of(subject, platform, actor)
    if account_id is None:
        return subject
    entity_id = snap.entity_of(account_id)
    if entity_id is None:
        return subject
    canonical_account = snap.canonical_account(entity_id, platform)
    if not canonical_account:
        return subject
    canonical_platform, _, canonical_actor = str(
        canonical_account
    ).partition(":")
    if canonical_platform != platform or not canonical_actor:
        return subject
    # N-2: rebuilt through the constructor so the scope is re-derived. Never
    # ``dataclasses.replace`` — that would keep the old scope and orphan the row.
    built = _build(subject.kind, platform, conversation, canonical_actor)
    return built if built is not None else subject


def fold_participants(
    subjects: Iterable[MemorySubject] | None, snap: Any = None,
) -> tuple[ParticipantGroup, ...]:
    """Fold a request's subject list into one slot per participant.

    Folding is a REQUEST-LEVEL two-phase operation, not a per-subject one. The
    plugin's ``_recent_other_speakers`` dedupes by sender_id (account), not by
    person, so one person's two alts speaking in the same group legitimately
    arrive as two subjects. Expanding each of them independently would yield two
    slots, two budgets and two ``### `` headings whose primary subject_id is
    IDENTICAL — the model would see "the same id, twice", which is worse than
    not expanding at all.

    Slot order is preserved by first appearance, because subject order IS the
    render budget priority. Folding can only SHRINK the slot list, so the wire's
    ``1..8`` check (which runs before folding) still holds.
    """
    groups: list[ParticipantGroup] = []
    index: dict[tuple, int] = {}
    for subject in subjects or ():
        key = participant_key(subject, snap)
        position = index.get(key)
        members = expand_subject(subject, snap)
        if position is None:
            index[key] = len(groups)
            primary = canonical_subject(subject, snap)
            if (primary.key, primary.scope) not in {
                (member.key, member.scope) for member in members
            }:
                # A canonical that is not in the expansion means the pool moved
                # under us. Prefer the requested subject as primary so the slot
                # can never be represented by a marker nobody can read.
                primary = subject
            groups.append(ParticipantGroup.of(primary, members))
            continue
        existing = groups[position]
        groups[position] = ParticipantGroup.of(
            existing.primary, (*existing.members, *members, subject),
        )
    return tuple(groups)


def group_for_marker(
    groups: Iterable[ParticipantGroup] | None,
) -> dict[tuple[str, str], ParticipantGroup]:
    """Build the ``marker -> group`` lookup used by rendering and bucketing.

    One-to-one by construction: request-level folding guarantees no two groups
    share a participant, and the no-truncation rule guarantees every group holds
    its participant's complete marker set.
    """
    lookup: dict[tuple[str, str], ParticipantGroup] = {}
    for group in groups or ():
        for marker in group.markers:
            lookup.setdefault(marker, group)
    return lookup


def coalesce_participant_last_writes(
    last_writes: dict[tuple, tuple[MemorySubject, Any]],
    group_resolver,
) -> dict[tuple, tuple[MemorySubject, Any]]:
    """Give every marker of a participant the participant's newest write time.

    Without this, canonical write routing archives live people: the
    non-canonical piles stop receiving writes, and
    ``SCOPED_SUBJECT_STALE_DAYS = 90`` later judges them stale even though the
    person is active. Because there is also no read-side truncation, the
    expansion is PERMANENT rather than transitional — letting the old piles
    decay would simply make S2 expire after 90 days.

    ``group_resolver`` maps one subject to its participant's subjects; it is
    injected by the sweep so ``memory/subject_archive.py`` keeps not importing
    ``trust_store``.
    """
    if not last_writes:
        return last_writes
    merged: dict[tuple, tuple[MemorySubject, Any]] = dict(last_writes)
    clusters: dict[int, list[tuple]] = {}
    cluster_of: dict[tuple, int] = {}
    for marker, (subject, _ts) in last_writes.items():
        if marker in cluster_of:
            continue
        try:
            members = tuple(group_resolver(subject) or ())
        except Exception as exc:  # noqa: BLE001 - archival must never break
            logger.warning(f"[SubjectIdentity] 归档合并解析失败: {exc}")
            members = (subject,)
        cluster_id = len(clusters)
        related = [
            member_marker for member_marker in (
                (member.key, member.scope) for member in members
            ) if member_marker in merged
        ]
        if marker not in related:
            related.append(marker)
        clusters[cluster_id] = related
        for member_marker in related:
            cluster_of.setdefault(member_marker, cluster_id)
    for members in clusters.values():
        newest = max(
            (merged[member][1] for member in members if member in merged),
            default=None,
        )
        if newest is None:
            continue
        for member in members:
            if member in merged:
                merged[member] = (merged[member][0], newest)
    return merged
