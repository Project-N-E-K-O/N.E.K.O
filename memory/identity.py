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

"""Two-layer speaker identity: ``entity`` (a person) <- ``account`` (a credential).

Pure functions, zero I/O. Everything stateful lives in :mod:`memory.trust_store`.

WHY TWO LAYERS. An entity is a person; an account is one credential that person
holds. The system only ever observes credentials, never people:

1. Every inbound message carries a credential, never a person. With only an
   entity layer, each unseen id forces a choice between "this is a new person"
   (which just renames account) and *guessing* which known person it is — and
   guessing means nickname matching or co-occurrence heuristics, which are
   categorically forbidden here (see the kill list in ``memory.trust_store``).
2. Permissions are granted to credentials, not to people. Admin in a QQ group
   does not imply admin on another platform; deriving one from the other makes
   the weakest integration the entry point for the whole system. So "what you
   were granted" lives on the account and only "what you earned" lives on the
   entity.
3. Assertions can be wrong, and a wrong one must be revocable. Revoking
   requires naming exactly what an account takes with it. A per-entity total
   has no answer to that question — not a hard one, *no* answer. Per-account
   partitioning makes unbind a byte-exact move of one sub-record.
4. The isolation unit of memory is "who spoke where", and the speaker is always
   a credential.

DETERMINISTIC DERIVATION HAS A PRECONDITION. ``derive_entity_id`` is a pure
function of ``(account_id, generation)`` — no allocator, no lock, no
write-before-persist window, and a lost pool file can be recomputed offline.
That only stays correct while **the derivation input is the sole truth of
identity**. Anyone adding a second identity dimension to the derivation must
first answer: can two accounts that differ on that dimension derive to the same
entity_id? If yes, deterministic derivation stops being a convenience and
starts *actively manufacturing* wrong merges.

``account_id`` IS ``stable_speaker_id`` OUTPUT, BYTE FOR BYTE. Never fold case,
never add a reserved-platform blacklist, never splice a channel into it. Three
SHA256 ledgers bake it in (``trust_event_id`` third arg, ``signal_identity``
source_id, the plugin's activity id), and ``memory/facts.py``'s
``_reconcile_existing_provenance`` turns any byte change into a permanent
``speaker_provenance_mixed`` stamp on the historical row — an absorbing state
with no clearing path anywhere in the repo, which also permanently retires that
row from trust signalling. That is memory-corpus damage, and neither ``unbind``
nor any linking mechanism can reach it.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

ENTITY_ID_PREFIX = "ent_"
CONVERSATION_ID_PREFIX = "conv_"

# NOTE: Python ``re`` here, so ``\A...\Z`` is correct. The pydantic wire models
# in ``app/memory_server/routes.py`` compile their patterns with the Rust regex
# crate, which does NOT recognise ``\Z`` and raises ``SchemaError`` at model
# definition time — those must use ``\A...\z`` (lowercase) or ``^...$``.
# The two layers must not copy the same pattern string.
_ENTITY_ID_RE = re.compile(r"\Aent_[0-9a-f]{24}\Z")
_CONVERSATION_ID_RE = re.compile(r"\Aconv_[0-9a-f]{24}\Z")
_CHANNEL_RE = re.compile(r"\A[a-z0-9_]{1,16}\Z")


def normalize_account_id(value: Any) -> str | None:
    """The one normalization entry point for account ids.

    Literally ``stable_speaker_id``, with no extra rejection. Sharing the exact
    function with the request path is what makes the legacy migration an
    identity map: the plugin's ``f"qq:{sender_id}"`` and the migration's
    ``f"qq:{bare_key}"`` land on the same string, so there is no seam for an
    "adoption" step to paper over.
    """
    from memory.speaker_trust import stable_speaker_id

    return stable_speaker_id(value)


def derive_entity_id(account_id: str, generation: int = 0) -> str:
    """Derive a seed entity id for one account.

    Deterministic rather than uuid4 so concurrent first-sight of the same
    account converges without an in-lock allocator, and so a lost pool can be
    recomputed. ``generation`` only ever increments in ``unbind``.
    """
    raw = f"neko.entity.v1|{account_id}|{int(generation)}"
    return ENTITY_ID_PREFIX + hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:24]


def derive_conversation_id(
    conversation_account_id: str, generation: int = 0,
) -> str:
    """Derive a seed conversation-entity id.

    Same construction as :func:`derive_entity_id` (and therefore the same
    namespace-disjointness proof): the prefix carries no colon, while every
    ``account_id`` must contain one.
    """
    raw = f"neko.conversation.v1|{conversation_account_id}|{int(generation)}"
    return CONVERSATION_ID_PREFIX + hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:24]


def is_entity_id(value: Any) -> bool:
    return _ENTITY_ID_RE.fullmatch(str(value or "")) is not None


def is_conversation_id(value: Any) -> bool:
    return _CONVERSATION_ID_RE.fullmatch(str(value or "")) is not None


def account_platform(account_id: Any) -> str:
    """Platform segment of an account id (already lowercased by normalization)."""
    return str(account_id or "").partition(":")[0]


def normalize_channel(value: Any) -> str | None:
    """Normalize an observed channel token. Never raises.

    ``None`` is a first-class value meaning "channel unknown", not a
    placeholder for a default. Legacy records genuinely have no channel
    provenance (the pre-upgrade plugin profile carried only adjustment /
    message_count / two rings), and encoding an unknown as a concrete channel
    is exactly the move that makes a stranger inherit somebody else's ledger.

    Historical alias: the QQ plugin's channel was renamed ``napcat`` ->
    ``onebot``; old ledgers may still carry ``napcat`` in ``channels_seen``.
    Normalize it to ``onebot`` so a legacy account and a fresh message are not
    misjudged as a multi-channel collision.
    """
    text = str(value or "").strip().lower()
    if not _CHANNEL_RE.fullmatch(text):
        return None
    return "onebot" if text == "napcat" else text


def activity_count_cap() -> int:
    """``ceil(MAX_BONUS / WEIGHT)`` — the message count beyond which activity is flat."""
    from config import (
        SPEAKER_TRUST_ACTIVITY_MAX_BONUS,
        SPEAKER_TRUST_ACTIVITY_WEIGHT,
    )

    if SPEAKER_TRUST_ACTIVITY_MAX_BONUS <= 0 or SPEAKER_TRUST_ACTIVITY_WEIGHT <= 0:
        return 0
    return max(0, math.ceil(
        SPEAKER_TRUST_ACTIVITY_MAX_BONUS / SPEAKER_TRUST_ACTIVITY_WEIGHT
    ))


def effective_trust(
    base: float, adjustment_sum: float, activity_count_sum: int,
) -> float:
    """The one scoring formula. Both clamps live here and nowhere else.

    ``trust = clamp01(base + clamp(Σadj, ±LIMIT) + min(MAX_BONUS, min(cap, Σmc)·W))``

    Both clamps sit OUTSIDE their sums, and each is applied exactly once:

    * Per-account clamping of ``adjustment`` would make the displacement
      ceiling ``±0.30·N`` — at N=2 that is already 4× the arbitration margin,
      i.e. binding becomes score farming. It is also non-equivalent under mixed
      signs (raw +0.50 and −0.40 sum to +0.10, but clamp-then-sum gives 0.00),
      and it would put truncation order into the result, destroying the
      commutativity that makes merge lossless.
    * Averaging is rejected outright: ``mean(adj)`` lets someone dilute one
      correction to ``−0.08/N`` by binding clean accounts, turning bind into a
      laundering tool. Addition plus a single clamp is the only shape that
      defends against farming and laundering at once.
    * The outer ``min(MAX_BONUS, ...)`` on activity looks redundant against the
      inner ``cap`` and is therefore the thing a refactor deletes. Deleting it
      makes ``sup(activity) = MAX_BONUS·N``; at N=8 that is 0.16 > margin 0.15.
      Keep both.

    ``base`` is NOT summed across accounts and must already be resolved for the
    single requesting account (see ``TrustSnapshot.resolve_trust``).
    """
    from config import (
        SPEAKER_TRUST_ACTIVITY_MAX_BONUS,
        SPEAKER_TRUST_ACTIVITY_WEIGHT,
        SPEAKER_TRUST_ADJUSTMENT_LIMIT,
    )

    adjustment = max(
        -SPEAKER_TRUST_ADJUSTMENT_LIMIT,
        min(SPEAKER_TRUST_ADJUSTMENT_LIMIT, float(adjustment_sum or 0.0)),
    )
    counted = min(activity_count_cap(), max(0, int(activity_count_sum or 0)))
    activity = min(
        SPEAKER_TRUST_ACTIVITY_MAX_BONUS,
        counted * SPEAKER_TRUST_ACTIVITY_WEIGHT,
    )
    return max(0.0, min(1.0, float(base) + adjustment + activity))


def _clean_event_ids(
    raw_events: Any, *, truncate_to: int | None = None,
) -> list[str]:
    """Normalize one event ring: strip, cap length, dedup keeping order."""
    if not isinstance(raw_events, list):
        return []
    events: list[str] = []
    seen: set[str] = set()
    for event_id in raw_events:
        normalized = str(event_id or "").strip()[:96]
        if normalized and normalized not in seen:
            seen.add(normalized)
            events.append(normalized)
    if truncate_to is None:
        return events
    return events[-truncate_to:]


def dedup_keep_order(values: Any) -> list[str]:
    """Public alias used by the legacy-import merge path."""
    return _clean_event_ids(values)


def normalize_channels_seen(value: Any) -> dict[str, dict[str, Any]]:
    """Normalize the per-account channel observation ledger (diagnostic only)."""
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(value, dict):
        return result
    for raw_channel, raw_stats in value.items():
        channel = normalize_channel(raw_channel)
        if channel is None or not isinstance(raw_stats, dict):
            continue
        try:
            count = max(0, int(raw_stats.get("count", 0) or 0))
        except (TypeError, ValueError, OverflowError):
            count = 0
        result[channel] = {
            "first": str(raw_stats.get("first") or "") or None,
            "last": str(raw_stats.get("last") or "") or None,
            "count": count,
        }
    return result


def normalize_account_record(
    account_id: str, value: Any = None,
) -> dict[str, Any]:
    """Normalize one account sub-record. Tolerant per field, never raises.

    Ported verbatim (arithmetic and ledger discipline) from the plugin's
    ``PermissionManager._normalize_speaker_profile``, minus the ``platform !=
    "qq"`` filter that made the whole thing platform-bound.
    """
    from config import SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT

    raw = value if isinstance(value, dict) else {}
    try:
        adjustment = float(raw.get("adjustment", 0.0) or 0.0)
    except (TypeError, ValueError, OverflowError):
        adjustment = 0.0
    if not math.isfinite(adjustment):
        adjustment = 0.0
    try:
        message_count = min(
            activity_count_cap(),
            max(0, int(raw.get("message_count", 0) or 0)),
        )
    except (TypeError, ValueError, OverflowError):
        message_count = 0
    try:
        generation = max(0, int(raw.get("generation", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        generation = 0
    legacy_import = raw.get("legacy_import")
    record: dict[str, Any] = {
        "account_id": account_id,
        "generation": generation,
        "bound_at": str(raw.get("bound_at") or "") or None,
        # WHO asserted this link. Auditability is one of the three edges that
        # make the bind-time trust transfer (+0.32 / −0.30, both >= 2x the
        # arbitration margin) acceptable at all — an operator has to be able to
        # ask "who said these are the same person, and when".
        "bound_by": str(raw.get("bound_by") or "") or None,
        "adjustment": adjustment,
        "message_count": message_count,
        # The two rings must never share an eviction policy: message spam
        # would otherwise evict correction ids and let them replay.
        "processed_activity_events": _clean_event_ids(
            raw.get("processed_activity_events"),
            truncate_to=SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT,
        ),
        "processed_signal_events": _clean_event_ids(
            raw.get("processed_signal_events"),
        ),
        "channels_seen": normalize_channels_seen(raw.get("channels_seen")),
    }
    if isinstance(legacy_import, dict):
        record["legacy_import"] = {
            "source": str(legacy_import.get("source") or ""),
            "at": str(legacy_import.get("at") or ""),
        }
    return record


def normalize_legacy_profile(value: Any) -> dict[str, Any]:
    """Normalize one inbound legacy profile. Per-field tolerant, never raises.

    Deliberately NOT a strict pydantic sub-model: the legacy normalizer this
    replaces was per-field tolerant, and turning migration into all-or-nothing
    would let a single dirty profile 422 the whole request — and a 422 wedges
    the migration permanently.
    """
    raw = value if isinstance(value, dict) else {}
    try:
        adjustment = float(raw.get("adjustment", 0.0) or 0.0)
    except (TypeError, ValueError, OverflowError):
        adjustment = 0.0
    if not math.isfinite(adjustment):
        adjustment = 0.0
    try:
        message_count = max(0, int(raw.get("message_count", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        message_count = 0
    return {
        "adjustment": adjustment,
        "message_count": message_count,
        # No truncation on either ring here — the caller merges into the
        # durable record and applies the activity ring's limit there, while
        # the signal ring stays append-only all the way through.
        "processed_activity_events": _clean_event_ids(
            raw.get("processed_activity_events"),
        ),
        "processed_signal_events": _clean_event_ids(
            raw.get("processed_signal_events"),
        ),
    }


def normalize_entity_record(
    entity_id: str, value: Any = None,
) -> dict[str, Any]:
    """Normalize one entity record, including its account sub-dict."""
    raw = value if isinstance(value, dict) else {}
    status = str(raw.get("status") or "active").strip().lower()
    if status not in {"active", "merged"}:
        status = "active"
    record: dict[str, Any] = {
        "entity_id": entity_id,
        "status": status,
        "created_at": str(raw.get("created_at") or "") or None,
        "updated_at": str(raw.get("updated_at") or "") or None,
        "accounts": {},
    }
    if status == "merged":
        record["merged_into"] = str(raw.get("merged_into") or "") or None
        record["merged_at"] = str(raw.get("merged_at") or "") or None
        merged_accounts = raw.get("merged_accounts")
        if isinstance(merged_accounts, list):
            record["merged_accounts"] = [
                normalized for normalized in (
                    normalize_account_id(item) for item in merged_accounts
                ) if normalized is not None
            ]
    raw_accounts = raw.get("accounts")
    if isinstance(raw_accounts, dict):
        for raw_account_id, raw_account in raw_accounts.items():
            account_id = normalize_account_id(raw_account_id)
            if account_id is None:
                continue
            record["accounts"][account_id] = normalize_account_record(
                account_id, raw_account,
            )
    canonical_accounts = raw.get("canonical_accounts")
    if isinstance(canonical_accounts, dict):
        cleaned: dict[str, dict[str, Any]] = {}
        for platform, entry in canonical_accounts.items():
            if not isinstance(entry, dict):
                continue
            account_id = normalize_account_id(entry.get("account_id"))
            platform_key = str(platform or "").strip().lower()
            if account_id is None or not platform_key:
                continue
            # A canonical pointer that no longer names a live member of this
            # entity is stale (hand-edited pool, partial restore). Drop it:
            # the next write re-seals, which is exactly the unbind path.
            if account_id not in record["accounts"]:
                continue
            cleaned[platform_key] = {
                "account_id": account_id,
                "sealed_at": str(entry.get("sealed_at") or "") or None,
            }
        if cleaned:
            record["canonical_accounts"] = cleaned
    superseded = raw.get("superseded_canonicals")
    if isinstance(superseded, list):
        kept = [item for item in superseded if isinstance(item, dict)]
        if kept:
            record["superseded_canonicals"] = kept
    return record


def record_activity(
    account_record: dict, count: int, event_id: str, *,
    entity_message_count: int | None = None,
) -> bool:
    """Apply one idempotent activity event. Returns whether the record changed.

    ``entity_message_count`` is the entity-wide sum ``Σ_{i∈E} mc_i``. The
    write-amplification no-op MUST be judged on that sum, not on this account's
    own count: a three-account entity with 7 messages each is already saturated
    (21 ≥ cap) while every individual ``mc_i`` is below cap, so a per-account
    test would rewrite the whole JSON on every flush for exactly the
    multi-account entities the feature exists for.

    Known cost of the no-op: a skipped message records no event id either, so
    an account later unbound with ``mc < cap`` can have those messages counted
    again on replay. Bounded by ``SPEAKER_TRUST_ACTIVITY_MAX_BONUS = 0.02``,
    far below the 0.15 arbitration margin. This is documented on the unbind
    endpoint.
    """
    from config import SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT

    event = str(event_id or "").strip()[:96]
    try:
        added = max(0, int(count or 0))
    except (TypeError, ValueError, OverflowError):
        return False
    if not event or added == 0:
        return False
    cap = activity_count_cap()
    saturated = (
        entity_message_count
        if entity_message_count is not None
        else int(account_record.get("message_count", 0) or 0)
    )
    if cap and saturated >= cap:
        return False
    processed = account_record["processed_activity_events"]
    if event in processed:
        return False
    processed.append(event)
    del processed[:-SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT]
    # Per-account write-side clamping to cap is retained (bounded storage,
    # byte-compatible with the pre-migration shape). Because the read side is
    # ``min(cap, Σ)``, pre-clamping each part can only SHRINK Σ and therefore
    # only shrink the bonus — fail-closed, and the repartition invariant is
    # declared over post-clamp partitions accordingly.
    account_record["message_count"] = min(
        cap, int(account_record.get("message_count", 0) or 0) + added,
    )
    return True


def apply_signal_event(account_record: dict, event: dict) -> bool:
    """Apply one owner confirmation/correction, idempotent by event id."""
    from config import (
        SPEAKER_TRUST_CONFIRMATION_DELTA,
        SPEAKER_TRUST_CORRECTION_DELTA,
    )

    if not isinstance(event, dict):
        return False
    event_id = str(event.get("event_id") or "").strip()[:96]
    kind = event.get("kind")
    if not event_id or kind not in {"confirmation", "correction"}:
        return False
    processed = account_record["processed_signal_events"]
    if event_id in processed:
        return False
    # Owner signals change arbitration power. Their deterministic ids form an
    # exact append-only replay ledger: truncating this list would let an old
    # correction apply a second time after eviction.
    processed.append(event_id)
    delta = (
        SPEAKER_TRUST_CONFIRMATION_DELTA
        if kind == "confirmation"
        else -SPEAKER_TRUST_CORRECTION_DELTA
    )
    # Keep the authored signal sum lossless: clamping each write is
    # non-commutative near the cap, so completion order could change the final
    # trust. The effective adjustment is clamped once, in ``effective_trust``.
    account_record["adjustment"] = (
        float(account_record.get("adjustment", 0.0) or 0.0) + delta
    )
    return True


def merge_order_key(entity_record: dict) -> tuple[str, str]:
    """Total order deciding the merge survivor.

    ``(created_at, entity_id)`` is a total order over entity records, so merge
    is idempotent, commutative and associative, and the eventual survivor is a
    function of the final entity set rather than of merge order. That property
    is what keeps ``canonical_accounts`` stable: a derived canonical (min
    account id / earliest bound_at / seed account) flips whenever the union
    grows, and each flip strands the previous period's writes on yet another
    subject.
    """
    return (
        str(entity_record.get("created_at") or ""),
        str(entity_record.get("entity_id") or ""),
    )
