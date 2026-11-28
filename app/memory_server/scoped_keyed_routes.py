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
"""Idempotency-keyed scoped history staging, generation, and replay pipeline."""

import hashlib
import json
import time
from fastapi import HTTPException
from utils.language_utils import is_supported_language_code
from utils.cloudsave_runtime import MaintenanceModeError

from . import routes as _routes
from .scoped_trust_helpers import (
    ScopedHistoryRequest,
    _stamp_subject_display_name,
    _trust_response_block,
)

_KEYED_ITEM_LOCALE = "locale"


_KEYED_ITEM_FACTS = "facts"


_KEYED_ITEM_DISPLAY_NAME = "display_name"


# On clear, unreadable key file prevents recording cancellation: record in staging; retries repair cancelled on read.
_KEYED_STAGING_CANCELLED = "cancelled_by_forget"


# Same-key retry cap for failed display-name writes (display data cannot block the whole key indefinitely).
_DISPLAY_NAME_MAX_ATTEMPTS = 3


# Field exists but is null, etc.: distinguish from absent fields and let validation classify as corrupt state.
_MALFORMED = object()


_SEGMENT_DROPPED_BY_FORGET = "dropped_by_forget"


def _reject_owner_signal_on_keyed_request(req: ScopedHistoryRequest) -> None:
    """Keyed requests carry no owner trust signals (422 otherwise).

    Owner signals produce trust events whose replay safety rests on the
    request-scoped observation chain of the unkeyed path; journaling them
    would need per-event effect keys. The only keyed caller (visit digests)
    always sends ``speaker_tier="none"`` without owner bits, so the keyed
    path rejects them instead of half-supporting them.
    """
    if req.speaker_is_owner or any(
        segment.speaker_is_owner for segment in (req.segments or [])
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "idempotency_key does not support speaker_is_owner: owner "
                "trust signals are not journaled; send owner batches without "
                "an idempotency_key"
            ),
        )


def _keyed_null_trust_block() -> dict:
    """A trust block with every key ``_trust_response_block`` reports, persisted null."""
    return {**_trust_response_block({}, None, None), "persisted": None}


def _keyed_fact_identity(fact: dict) -> tuple:
    from memory.facts import _speaker_trust_fact_identity

    identity = _speaker_trust_fact_identity(fact)
    if identity is not None:
        return identity
    return (
        str(fact.get("id")), fact.get("subject_kind"),
        fact.get("subject_id"), fact.get("scope"),
    )


def _keyed_duplicate_response(shape: str, contexts: list[dict]) -> dict:
    if shape == "single":
        return {
            "status": "processed",
            "duplicate": True,
            "subject": contexts[0]["subject"].as_entry_fields(),
            "created": 0,
            "fact_ids": [],
            "trust": _keyed_null_trust_block(),
            "trust_events": [],
        }
    return {
        "status": "processed",
        "duplicate": True,
        "segments": [
            {
                "subject": context["subject"].as_entry_fields(),
                "trust": _keyed_null_trust_block(),
                "status": "ok",
                "created": 0,
                "dropped": 0,
                "fact_ids": [],
                "fact_identities": [],
                "created_fact_identities": [],
                "reconciled": [],
                "trust_events": [],
            }
            for context in contexts
        ],
    }


def _keyed_request_hash(req: ScopedHistoryRequest) -> str:
    """Canonical hash of what a keyed request asks to be extracted.

    Covers every ``input_history`` (by position), ``subject_epochs``,
    ``language`` and the trust inputs (``speaker_id``, tier / base trust,
    activity events, channel): the fields that decide which facts, locale
    state and trust mutations are produced and whether they may land.
    Display names / speaker labels are left out on purpose: they are
    cosmetic set-to-value data a caller may legitimately refresh between
    retries of the same batch.
    """
    sources = [req] if req.segments is None else list(req.segments)
    histories = [source.input_history for source in sources]

    def _trust(source) -> dict:
        events = getattr(source, "speaker_activity_events", None) or []
        return {
            "speaker_id": getattr(source, "speaker_id", None),
            "speaker_tier": getattr(source, "speaker_tier", None),
            "speaker_base_trust": getattr(source, "speaker_base_trust", None),
            "speaker_trust": getattr(source, "speaker_trust", None),
            "speaker_channel": getattr(source, "speaker_channel", None),
            "activity": [[event.id, event.count] for event in events],
        }

    payload = {
        "histories": histories,
        # Subject scope is part of identity (same kind:id under different scopes means isolated subjects):
        # Reusing one key with changed scope cannot count as the same request.
        # Store normalized scope: omitted scope and explicit default identify the same subject;
        # switching representations on retry must not make a different request.
        "scopes": [
            source.subject.to_domain().scope if getattr(source, "subject", None) is not None else None
            for source in sources
        ],
        "trust": [_trust(source) for source in sources],
        # Include generations only for this request's wire subjects: application/staging validate those alone;
        # extra/missing generations for other subjects change no effects and must not cause same-key retry 422.
        "subject_epochs": {
            wire_key: epoch for wire_key, epoch in (req.subject_epochs or {}).items()
            if wire_key in {
                source.subject.to_domain().key for source in sources
                if getattr(source, "subject", None) is not None
            }
        },
        # Language determines extraction context and staged language-state items, so also affects identity.
        "language": req.language,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _keyed_staging_items_valid(
    staging: dict, routed_keys: list | None = None, request_epochs: dict | None = None,
    language: str | None = None, routed_positions: object = None, *, structural_only: bool = False,
) -> bool:
    """Whether every journal item of a restored staging document is safe to apply.

    Every segment's persisted destination (``subject``) must be a valid
    subject that is either its own wire subject or one of ``routed_keys``
    (the routed subjects recorded on the key when the journal was created).

    Items must be objects numbered ``seq == position`` with a known kind and a
    segment index inside ``segments``; an item not yet in ``applied`` must
    still carry its payload (facts with one effect key each, or a display
    name). Items already applied / dropped may have been stripped by a forget.

    ``structural_only`` skips the checks bound to the retrying request (the
    destination against the recorded routes, the locale count / language):
    a forget uses it to tell whether a journal it keeps could ever replay.
    """
    from memory.scopes import MemoryScopeError, coerce_subject

    from . import idempotency

    segments = staging.get("segments")
    items = staging.get("items")
    applied = staging.get("applied")
    if not isinstance(segments, list) or not isinstance(items, list) or not isinstance(applied, list):
        return False
    allowed_routes = {str(k) for k in routed_keys} if routed_keys is not None else None
    if routed_positions is not None and not (
        isinstance(routed_positions, list) and len(routed_positions) == len(segments)
        and all(isinstance(k, str) for k in routed_positions)
    ):
        # Present but corrupt positional routing cannot fall back to set-only checks (replace a target then truncate to bypass);
        # use sets only when this field genuinely does not exist.
        return False
    for position, segment in enumerate(segments):
        # Write targets must be well formed and match this segment's wire subject or recorded initial routed subject:
        # tampering to another valid subject would write facts into an unrelated memory domain.
        if not isinstance(segment, dict):
            return False
        try:
            destination = coerce_subject(segment.get("subject"))
        except (MemoryScopeError, TypeError, ValueError):
            return False
        if destination is None:
            return False
        # Without a key record (orphan staging), no trusted route exists to compare: accept only this segment's wire subject.
        permitted = (allowed_routes if allowed_routes is not None else set()) | {segment.get("wire_key")}
        if routed_positions is not None:
            # Check positions: swapping two targets leaves both in the overall routing set but writes into each other's domains.
            permitted = {routed_positions[position], segment.get("wire_key")}
        if destination.key not in permitted and not structural_only:
            return False
        dropped = segment.get("dropped", 0)
        if not isinstance(dropped, int) or isinstance(dropped, bool) or dropped < 0:
            # Response assembly calls int() on this: corrupt values cause 500 before finalization on every same-key retry.
            return False
        if destination.scope != destination.key:
            # Keyed writes support default scope only: a changed target scope writes into a different isolated domain.
            return False
        if segment.get("tombstone_keys") != [segment.get("wire_key")]:
            # Compare tombstones only by this segment's wire key (generation contract); empty/changed keys bypass clearing.
            return False
    if request_epochs is not None:
        # Staged request generations must match this request (content hash covers them); raising one would
        # bypass a generation-bearing clear that happened in between.
        staged_epochs = staging.get("epochs")
        wire_keys = {segment.get("wire_key") for segment in segments if isinstance(segment, dict)}
        if not isinstance(staged_epochs, dict) or any(
            staged_epochs.get(wire_key) != request_epochs.get(wire_key) for wire_key in wire_keys
        ):
            return False
    if any(not isinstance(entry, dict) for entry in applied):
        return False
    seqs = [entry.get("seq") for entry in applied]
    # Applied sequence numbers must be distinct in-range true integers: {"seq": true} would count as 1, skipping an item.
    if any(not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq < len(items) for seq in seqs):
        return False
    if len(set(seqs)) != len(seqs):
        return False
    for entry in applied:
        # Result fields (fact IDs and identity recorded during application) must also be well formed: corrupt values break response assembly
        # (500 on every retry) or return forged IDs.
        for name in ("fact_ids", "reconciled"):
            if name in entry and not (
                isinstance(entry[name], list) and all(
                    (isinstance(v, str) and v) or (isinstance(v, int) and not isinstance(v, bool))
                    for v in entry[name]
                )
            ):
                return False
        for name in ("created_fact_identities", "reconciled_fact_identities"):
            if name in entry and not (
                isinstance(entry[name], list) and all(
                    isinstance(identity, list) and len(identity) == 4
                    and all(isinstance(part, str) and part for part in identity)
                    for identity in entry[name]
                )
            ):
                return False
    done = set(seqs)
    key = staging.get("key")
    if not isinstance(key, str) or not key:
        return False
    ordinal = 0
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            return False
        seq, segment, kind = item.get("seq"), item.get("segment"), item.get("kind")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq != position:
            return False
        if not isinstance(segment, int) or isinstance(segment, bool) or not 0 <= segment < len(segments):
            return False
        if kind not in (_KEYED_ITEM_LOCALE, _KEYED_ITEM_FACTS, _KEYED_ITEM_DISPLAY_NAME):
            return False
        if kind == _KEYED_ITEM_FACTS:
            # Effect keys derive from staging key + global sequence; verify individually, not shape alone: another row's
            # existing effect key would incorrectly skip this fact as replay.
            effect_keys = item.get("effect_keys")
            if not isinstance(effect_keys, list) or effect_keys != [
                idempotency.effect_key_for(key, ordinal + offset) for offset in range(len(effect_keys))
            ]:
                return False
            ordinal += len(effect_keys)
        if seq in done:
            continue
        if kind == _KEYED_ITEM_FACTS:
            facts = item.get("facts")
            if not isinstance(facts, list) or len(facts) != len(item["effect_keys"]):
                return False
            if not _restored_provenance_valid(item.get("speaker_provenance")):
                # Corrupt speaker provenance is silently ignored/stringified by persistence, losing ownership or attaching a fabricated
                # label while still marking applied.
                return False
            # Same post-generation requirement: each item is an object with nonempty body. Persistence silently skips corrupt
            # entries but still marks applied, permanently losing their effects.
            if not all(
                isinstance(fact, dict) and isinstance(fact.get("text"), str) and fact["text"].strip()
                for fact in facts
            ):
                return False
        elif kind == _KEYED_ITEM_DISPLAY_NAME:
            if item.get("display_name") is not None and not isinstance(item.get("display_name"), str):
                return False
        elif kind == _KEYED_ITEM_LOCALE:
            # Language items must also be well formed: invalid order is silently ignored as absent yet marked applied.
            order = item.get("order")
            # Accept supported language codes only: unsupported codes persist as None, clearing the subject's
            # existing valid language while still marking applied.
            if not is_supported_language_code(item.get("language")):
                return False
            if language is not None and not structural_only and item.get("language") != language:
                # Another supported language is also invalid: request hash covers language; staging records this request's language only.
                return False
            # Order is a positive causal timestamp reserved at turn start: zero/negative is silently ignored yet marked applied.
            if not isinstance(order, int) or isinstance(order, bool) or order <= 0:
                return False
    # Request hash covers language; reserve one supported-language item per segment at start. Clearing erases content, not items:
    # mismatched counts mean an item was deleted; finalizing would permanently omit that segment's language write.
    manifest = staging.get("manifest")
    if not (
        isinstance(manifest, dict) and set(manifest) == {"facts", "effects", "language", "facts_segments"}
        and all(
            isinstance(manifest[name], int) and not isinstance(manifest[name], bool)
            for name in ("facts", "effects")
        )
        and manifest["facts"] == sum(1 for item in items if item.get("kind") == _KEYED_ITEM_FACTS)
        and manifest["effects"] == ordinal
        and manifest["facts_segments"] == [
            item.get("segment") for item in items if item.get("kind") == _KEYED_ITEM_FACTS
        ]
        and (manifest["language"] is None or is_supported_language_code(manifest["language"]))
    ):
        # Fact-item/effect counts differ from generation: an entire fact item was lost; finalizing remaining items permanently omits it.
        return False
    # Validate language items against manifest language (independent of retries, usable before partial clear rewrites): each uncleared segment
    # must have exactly one matching-language item; full validation also matches manifest to this request's language.
    staged_language = manifest["language"]
    if not structural_only and staged_language != (language if is_supported_language_code(language) else None):
        return False
    if any(
        item.get("kind") == _KEYED_ITEM_LOCALE and item.get("language") != staged_language for item in items
    ):
        return False
    expected_locale = 1 if staged_language is not None else 0
    locale_per_segment = [0] * len(segments)
    for item in items:
        if item.get("kind") == _KEYED_ITEM_LOCALE:
            locale_per_segment[item["segment"]] += 1
    if any(
        count != expected_locale
        # Cleared/discarded segments may lack language items (retry reserves none, avoiding recreating cleared subjects in language storage).
        and not (count == 0 and isinstance(segments[index], dict)
                 and segments[index].get(_SEGMENT_DROPPED_BY_FORGET) is True)
        for index, count in enumerate(locale_per_segment)
    ):
        return False
    # Every applied item needs type-specific completion evidence (result or discard marker); a record with only {"seq": n}
    # would skip that item yet mark the key done.
    kind_of = {item.get("seq"): item.get("kind") for item in items if isinstance(item, dict)}
    for entry in applied:
        if any(entry.get(name) is True for name in _KEYED_DROP_MARKERS):
            continue
        kind = kind_of.get(entry.get("seq"))
        if kind == _KEYED_ITEM_FACTS:
            # Empty fact_ids is itself a legal result, insufficient to prove application: require a dedicated completion marker.
            evidence = entry.get("facts_applied") is True
        elif kind == _KEYED_ITEM_LOCALE:
            evidence = entry.get("locale_recorded") is True
        else:
            evidence = (
                entry.get("display_name_stamped") is True
                or entry.get("display_name_gave_up") is True
                or isinstance(entry.get("display_name_from_request"), bool)
            )
        if not evidence:
            return False
    return True


# Fact-item discard markers written by this module (matching write sites): only these names prove completion.
_KEYED_DROP_MARKERS = ("dropped_tombstone", "dropped_forget", "dropped_forget_during_generation")


_RESTORED_PROVENANCE_FIELDS = frozenset({"speaker_label", "speaker_trust", "speaker_id", "speaker_entity_id"})


def _restored_provenance_valid(provenance) -> bool:
    """A restored ``speaker_provenance`` has exactly the shape the route builds (or is absent)."""
    if provenance is None:
        return True
    if not isinstance(provenance, dict) or not provenance or set(provenance) - _RESTORED_PROVENANCE_FIELDS:
        return False
    # Both provenance construction paths include label (required per segment; entirely None for unlabeled single requests),
    # already trimmed and limited to 64 characters; incomplete ownership missing label cannot be marked applied.
    label = provenance.get("speaker_label")
    if not isinstance(label, str) or not label:
        return False
    from memory.facts import FactStore

    # Both request paths use the same label sanitizer. A value differing from its output (newlines / brackets / structural characters)
    # was tampered with; persistence only truncates, never sanitizes, so corrupt labels enter ownership unchanged.
    if FactStore.sanitize_speaker_label(label) != label:
        return False
    if "speaker_trust" in provenance:
        trust = provenance["speaker_trust"]
        if not isinstance(trust, (int, float)) or isinstance(trust, bool) or not 0.0 <= float(trust) <= 1.0:
            return False
    if "speaker_id" in provenance:
        from memory.speaker_trust import stable_speaker_id

        speaker_id = provenance["speaker_id"]
        if not isinstance(speaker_id, str) or stable_speaker_id(speaker_id) != speaker_id:
            return False
    elif "speaker_entity_id" in provenance:
        return False
    if "speaker_entity_id" in provenance:
        entity_id = provenance["speaker_entity_id"]
        if not isinstance(entity_id, str) or not entity_id.strip():
            return False
    return True


def _keyed_staging_matches(
    staging: dict, shape: str, contexts: list[dict], request_hash: str | None = None,
) -> bool:
    """Same key, same request: shape, per-position wire subjects and content agree."""
    segments = staging.get("segments")
    if staging.get("shape") != shape or not isinstance(segments, list):
        return False
    stored_hash = staging.get("request_hash")
    # Missing staged content hash is no wildcard: truncating an old journal's hash would let a different request claim it.
    if request_hash is not None and (not isinstance(stored_hash, str) or stored_hash != request_hash):
        return False
    if len(segments) != len(contexts):
        return False
    return all(
        isinstance(stored, dict)
        and stored.get("wire_key") == context["wire_subject"].key
        for stored, context in zip(segments, contexts)
    )


async def _generate_keyed_facts(
    lanlan_name: str,
    shape: str,
    contexts: list[dict],
    prompt_segments: list[dict] | None,
    *,
    skip: set[int] = frozenset(),
) -> tuple[list[list[dict]], list[int]]:
    """Run the extraction LLM once; return ``(facts per context, dropped per context)``.

    Fail-closed and stricter than the unkeyed path: ANY incomplete outcome
    (terminal LLM failure, malformed / unplaceable / suspect entries, a
    segment missing from the batch output) is a 502 and nothing is staged.
    The unkeyed path can persist the recognised part and let the caller
    retry the rest; here the staged result is final for the key, so a
    partial one would be pinned forever.
    """
    fact_store = _routes.runtime.fact_store
    failure = HTTPException(
        status_code=502,
        detail="scoped fact extraction failed; retry later",
    )
    if len(skip) >= len(contexts):
        # All segments cleared: send nothing (callers usually finalized cancellation earlier).
        return [[] for _ in contexts], [0] * len(contexts)
    if shape == "single":
        context = contexts[0]
        extracted = await fact_store._allm_extract_facts(
            lanlan_name,
            context["messages"],
            treat_malformed_as_failure=True,
            speaker_label=context.get("speaker_label"),
            label_own_words=bool(context.get("label_own_words")),
        )
        if extracted is None:
            raise failure
        if any(
            not (
                isinstance(fact, dict)
                and isinstance(fact.get("text"), str)
                and fact["text"].strip()
            )
            for fact in extracted
        ):
            raise failure
        return [list(extracted)], [0]

    # Send only uncleared segments; map results positionally to original indices, with empty outputs for cleared segments.
    live = [index for index in range(len(contexts)) if index not in skip]
    extracted = await fact_store._allm_extract_facts_batch(
        lanlan_name, [prompt_segments[index] for index in live],
    )
    if extracted is None:
        raise failure
    count = len(live)
    per_segment: list[list[dict] | None] = [None] * count
    dropped_live = [0] * count
    for item in extracted:
        index, facts, item_dropped, suspect = fact_store._parse_batch_segment_entry(
            item, count,
        )
        if index is None or suspect:
            raise failure
        if per_segment[index] is None:
            per_segment[index] = []
        per_segment[index].extend(facts)
        dropped_live[index] += item_dropped
    if not extracted:
        per_segment = [[] for _ in range(count)]
    if any(facts is None for facts in per_segment):
        raise failure
    facts_all: list[list[dict]] = [[] for _ in contexts]
    dropped = [0] * len(contexts)
    for position, index in enumerate(live):
        facts_all[index] = list(per_segment[position] or [])
        dropped[index] = dropped_live[position]
    return facts_all, dropped


async def _reserve_keyed_locale_orders(
    lanlan_name: str, language: str | None, contexts: list[dict], key: str | None = None,
) -> list[int | None]:
    """Reserve one causal locale order per subject BEFORE the LLM call.

    The order is stored in the staging document and reused by every apply
    attempt. Because it is reserved before generation, a forget that lands
    later always ends up with a cutoff >= this order, so the locale item is
    also rejected by the locale store's own forget cutoff, independently of
    tombstones; and a newer unkeyed write keeps priority over a retried
    older digest.

    With ``key``, the first reservation is kept on the pending key record
    and reused by every later attempt of that key (a generation that failed
    before staging must not move the old request forward in causal order).
    """
    from . import idempotency

    if not is_supported_language_code(language):
        return [None] * len(contexts)
    stored = None
    forgotten: set[str] = set()
    if key is not None:
        try:
            record = await idempotency.read_key(lanlan_name, key)
            forgotten = _routes._forgotten_keys_of(record)
        except (idempotency.IdempotencyStateError, OSError) as exc:
            raise HTTPException(status_code=503, detail="idempotency state unreadable; retry later") from exc
        candidate = record.get("locale_orders") if isinstance(record, dict) else None
        if candidate is not None:
            if not (
                isinstance(candidate, list) and len(candidate) == len(contexts)
                and all(
                    # None is permitted only for segments already cleared at start; elsewhere it is corrupt and would silently omit language writes.
                    (order is None and bool({contexts[index]["wire_subject"].key, contexts[index]["subject"].key} & forgotten))
                    or (isinstance(order, int) and not isinstance(order, bool) and order > 0)
                    for index, order in enumerate(candidate)
                )
            ):
                # Existing reservation is corrupt: fresh numbers cannot be recorded without overwriting fields, and every retry would move
                # the old request later. Treat as corrupt state.
                raise idempotency.IdempotencyStateError(f"locale reservation of {key!r} is malformed")
            stored = candidate
    # Do not reserve segments cleared during earlier attempts (forgotten_keys): clearing removed their language rows;
    # reservation would recreate cleared subjects in scoped_prompt_locales.json despite doomed language items.
    live = [
        index for index, context in enumerate(contexts)
        if not ({context["wire_subject"].key, context["subject"].key} & forgotten)
    ]
    if stored is None:
        allocated = _routes.locale_state.allocate_subject_prompt_locale_orders(
            lanlan_name, [contexts[index]["subject"] for index in live],
        )
        admission: list[int | None] = [None] * len(contexts)
        for index, order in zip(live, allocated):
            admission[index] = order
    else:
        admission = list(stored)
    if key is not None and stored is None:
        # Record allocated numbers on the key before persisting reservations: failed generation/crash between steps makes same-key retries reuse
        # them, rather than obtain later numbers and overwrite newer language written by intervening requests.
        def _remember(old, orders=list(admission)):
            if old is None or old.get("state") != idempotency.KEY_STATE_PENDING or old.get("locale_orders"):
                return None
            return {**old, "locale_orders": orders}

        try:
            await idempotency.update_key(lanlan_name, key, _remember)
        except (idempotency.IdempotencyStateError, OSError) as exc:
            raise HTTPException(status_code=503, detail="idempotency state unreadable; retry later") from exc
    if key is not None:
        # A clear may have added a segment to forgotten_keys during writes above; recheck before persisting reservations.
        try:
            forgotten |= _routes._forgotten_keys_of(await idempotency.read_key(lanlan_name, key))
        except (idempotency.IdempotencyStateError, OSError) as exc:
            raise HTTPException(status_code=503, detail="idempotency state unreadable; retry later") from exc
        live = [
            index for index in live
            if not ({contexts[index]["wire_subject"].key, contexts[index]["subject"].key} & forgotten)
        ]
    reserve = [index for index in live if admission[index] is not None]
    if reserve:
        await _routes.asyncio.to_thread(
            _routes.locale_state.reserve_subject_prompt_locale_orders,
            lanlan_name,
            [contexts[index]["subject"] for index in reserve],
            orders=[admission[index] for index in reserve],
        )
    return [admission[index] if index in reserve else None for index in range(len(contexts))]


async def _build_keyed_staging(
    lanlan_name: str,
    req: ScopedHistoryRequest,
    *,
    shape: str,
    contexts: list[dict],
    prompt_segments: list[dict] | None,
) -> dict:
    from . import idempotency

    key = req.idempotency_key
    orders = await _reserve_keyed_locale_orders(lanlan_name, req.language, contexts, key)
    # Do not extract segments cleared during earlier attempts (forgotten_keys): output will be discarded,
    # and sending their retained original text to extraction models would disclose it again.
    forgotten = _routes._forgotten_keys_of(await idempotency.read_key(lanlan_name, key))
    skip = {
        index for index, context in enumerate(contexts)
        if {context["wire_subject"].key, context["subject"].key} & forgotten
    }
    facts_per_context, dropped = await _generate_keyed_facts(
        lanlan_name, shape, contexts, prompt_segments, skip=skip,
    )
    segments = []
    items: list[dict] = []
    subject_keys: set[str] = set()
    effect_ordinal = 0
    for index, context in enumerate(contexts):
        wire_key = context["wire_subject"].key
        # Compare tombstones only with the request's own wire key: request generation belongs to its domain;
        # adding routed-key tombstones would compare two unrelated counters.
        tombstone_keys = [wire_key]
        subject_keys.update(tombstone_keys)
        segments.append({
            "wire_key": wire_key,
            "subject": context["subject"].as_entry_fields(),
            "tombstone_keys": tombstone_keys,
            "dropped": int(dropped[index]),
        })
        if orders[index] is not None:
            items.append({
                "seq": len(items),
                "kind": _KEYED_ITEM_LOCALE,
                "segment": index,
                "language": req.language,
                "order": orders[index],
            })
        facts = facts_per_context[index]
        if facts:
            effect_keys = []
            for _fact in facts:
                effect_keys.append(idempotency.effect_key_for(key, effect_ordinal))
                effect_ordinal += 1
            items.append({
                "seq": len(items),
                "kind": _KEYED_ITEM_FACTS,
                "segment": index,
                "facts": facts,
                "effect_keys": effect_keys,
                "speaker_provenance": context.get("speaker_provenance"),
            })
        if context.get("display_name"):
            items.append({
                "seq": len(items),
                "kind": _KEYED_ITEM_DISPLAY_NAME,
                "segment": index,
                "display_name": context["display_name"],
            })
    request_epochs = req.subject_epochs or {}
    return {
        "key": key,
        "state": idempotency.STAGING_STATE_GENERATED,
        "shape": shape,
        "subjects": sorted(subject_keys),
        "epochs": {
            subject_key: int(request_epochs[subject_key])
            for subject_key in sorted(subject_keys)
            if subject_key in request_epochs
        },
        # Diagnostics only; never participates in comparisons.
        "client_requested_at": req.client_requested_at,
        "created_at": time.time(),
        "request_hash": _routes._keyed_request_hash(req),
        "segments": segments,
        "items": items,
        "applied": [],
        # Generated fact-item/effect counts detect wholly missing items during recovery (per-item checks cannot detect absence).
        # Language items are checked one per segment; missing display-name items are restored from current retry request and excluded.
        "manifest": {
            "facts": sum(1 for item in items if item["kind"] == _KEYED_ITEM_FACTS),
            "effects": effect_ordinal,
            "language": req.language if is_supported_language_code(req.language) else None,
            # Original segment of each generated fact item: tampering segment index (with valid sequence/effect key/targets) would
            # write this segment's facts into another subject's memory domain.
            "facts_segments": [item["segment"] for item in items if item["kind"] == _KEYED_ITEM_FACTS],
        },
    }


async def _apply_keyed_item(lanlan_name: str, item: dict, segment: dict, generation) -> dict:
    from memory.scopes import coerce_subject

    subject = coerce_subject(segment["subject"])
    kind = item.get("kind")
    entry: dict = {"seq": item["seq"]}
    if kind == _KEYED_ITEM_FACTS:
        reconciled: list[dict] = []
        created = await _routes.runtime.fact_store._apersist_new_facts(
            lanlan_name,
            list(item.get("facts") or []),
            subject=subject,
            speaker_provenance=item.get("speaker_provenance"),
            expected_subject_generation=generation,
            reconciled_facts=reconciled,
            effect_keys=list(item.get("effect_keys") or []),
        )
        # Store strings consistently with _keyed_fact_identity: FactStore retains legacy integer IDs.
        entry["fact_ids"] = [str(fact.get("id")) for fact in created if fact.get("id")]
        entry["facts_applied"] = True
        entry["created_fact_identities"] = [
            list(_keyed_fact_identity(fact))
            for fact in created
            if isinstance(fact, dict)
            and fact.get("id") is not None
            and all(_keyed_fact_identity(fact))
        ]
        entry["reconciled"] = [
            str(fact.get("id")) for fact in reconciled
            if isinstance(fact, dict) and fact.get("id")
        ]
        entry["reconciled_fact_identities"] = [
            list(_keyed_fact_identity(fact))
            for fact in reconciled
            if isinstance(fact, dict)
            and fact.get("id")
            and all(_keyed_fact_identity(fact))
        ]
    elif kind == _KEYED_ITEM_LOCALE:
        await _routes.asyncio.to_thread(
            _routes.locale_state.record_subject_prompt_locale,
            lanlan_name,
            subject,
            item.get("language"),
            order=item.get("order"),
        )
        entry["locale_recorded"] = True
    elif kind == _KEYED_ITEM_DISPLAY_NAME:
        # Setting a value is inherently idempotent; set names on existing sections only.
        await _stamp_subject_display_name(
            lanlan_name, subject, item.get("display_name"), strict=True,
        )
        entry["display_name_stamped"] = True
    else:  # pragma: no cover - staging written by this module only
        raise RuntimeError(f"unknown keyed item kind {kind!r}")
    return entry


def _restore_missing_display_items(staging: dict, display_names: dict[int, str | None]) -> None:
    """Re-add the display-name item of a segment whose item is missing from a restored journal.

    The display name is not part of the request identity and a restored retry
    applies the current request's value anyway, so a journal that lost a
    trailing display item is completed from the retry instead of finalizing
    the key without it. Segments already dropped by a forget are left alone.
    """
    items = staging["items"]
    dropped_seqs = {
        entry.get("seq") for entry in staging.get("applied") or []
        if isinstance(entry, dict) and any(entry.get(name) is True for name in _KEYED_DROP_MARKERS)
    }
    dropped_segments = {item.get("segment") for item in items if item.get("seq") in dropped_seqs}
    dropped_segments |= {
        index for index, segment in enumerate(staging.get("segments") or [])
        if isinstance(segment, dict) and segment.get(_SEGMENT_DROPPED_BY_FORGET) is True
    }
    present = {item.get("segment") for item in items if item.get("kind") == _KEYED_ITEM_DISPLAY_NAME}
    for index, name in sorted(display_names.items()):
        if name and index not in present and index not in dropped_segments:
            items.append({
                "seq": len(items),
                "kind": _KEYED_ITEM_DISPLAY_NAME,
                "segment": index,
                "display_name": name,
            })


def _capture_forget_generations(lanlan_name: str, subjects: list) -> dict[int, int]:
    """Segment index -> current forget generation of that segment's (routed) subject."""
    return {
        index: _routes.runtime.fact_store._subject_forget_generation(lanlan_name, subject)
        for index, subject in enumerate(subjects)
    }


async def _persist_fence_marks(lanlan_name: str, key: str, staging: dict, fenced: set[str]) -> None:
    """Persist the segment drop marks a recheck just added, or answer 503 with a durable fallback.

    ``fenced`` are the journal subjects of the segments this recheck fenced
    (each one's own subject moved or is being forgotten; never the routed
    destination of an older dropped segment, which a valid segment may share). When the journal write fails, the marks would
    exist only in memory and a same-key retry restored from the unmarked
    journal (its baseline captured after the forget) would write those
    segments; ``fenced`` is then merged into the key record's
    ``forgotten_keys``, which the restored path replays before applying. A
    cancellation landing during the journal write records the same fallback
    (the write's outcome is unknown) before propagating. If the fallback
    write fails too there is no durable copy of these marks.
    """
    from . import idempotency

    try:
        await idempotency.write_staging(lanlan_name, key, staging)
        return
    except MaintenanceModeError:
        raise
    except _routes.asyncio.CancelledError:
        # Threaded write finished but success is unknown here: still record fallback, then re-raise cancellation.
        fallback = await _record_fence_fallback(lanlan_name, key, fenced)
        _routes.logger.warning(
            f"[scoped_history] {lanlan_name}: 清除丢弃标记的暂存写入时请求被取消（键记录兜底：{fallback}）"
        )
        raise
    except Exception as exc:
        journal_error = exc
    # Log original persistence error first: cancellation during fallback could make the later log unreachable.
    _routes.logger.error(f"[scoped_history] {lanlan_name}: 清除丢弃标记的暂存落盘失败，改记键记录兜底: {journal_error}")
    fallback = await _record_fence_fallback(lanlan_name, key, fenced)
    _routes.logger.error(f"[scoped_history] {lanlan_name}: 清除丢弃标记的键记录兜底：{fallback}")
    raise HTTPException(
        status_code=503,
        detail="scoped history staging failed; retry with the same key",
    ) from journal_error


async def _record_fence_fallback(lanlan_name: str, key: str, fenced: set[str]) -> str:
    """Merge ``fenced`` into the pending key record's ``forgotten_keys``; return what happened.

    Runs to completion even when the calling task is cancelled meanwhile
    (the caller holds the key lock and must not release it before this
    record is written); the cancellation is re-raised afterwards.
    """
    from . import idempotency

    if not fenced:
        return "skipped"

    def _remember(old):
        if old is None or old.get("state") != idempotency.KEY_STATE_PENDING:
            return None
        prior = _routes._forgotten_keys_of(old)
        if fenced <= prior:
            return None
        return {**old, "forgotten_keys": sorted(prior | fenced)}

    task = _routes.asyncio.ensure_future(idempotency.update_key(lanlan_name, key, _remember))
    cancellation = None
    while True:
        try:
            stored = await _routes.asyncio.shield(task)
        except _routes.asyncio.CancelledError as exc:
            current = _routes.asyncio.current_task()
            if current is not None and current.cancelling():
                # Caller cancelled: wait for fallback write, then re-raise cancellation anyway.
                cancellation = cancellation or exc
            if task.cancelled():
                # Only fallback task cancelled (shutdown sweep): not written; caller logs and returns 503 normally.
                outcome = "cancelled before it was written"
                break
            # Caller cancelled while fallback still running / just completed or failed: inspect its real result next iteration.
            continue
        except MaintenanceModeError:
            if cancellation is not None:
                raise cancellation
            raise
        except Exception as exc:  # noqa: BLE001 - Neither location writable: caller still returns 503.
            outcome = f"failed too: {exc}"
            break
        outcome = (
            "recorded in forgotten_keys"
            if isinstance(stored, dict) and fenced <= set(stored.get("forgotten_keys") or [])
            else "not recorded (key not pending)"
        )
        break
    if cancellation is not None:
        _routes.logger.warning(f"[scoped_history] {lanlan_name}: 请求被取消，清除丢弃标记的键记录兜底：{outcome}")
        raise cancellation
    return outcome


def _segment_fence_keys(segment: object) -> set[str]:
    """Keys a fallback fence records for one segment: its journal subject and its stable wire key.

    The journal subject covers a restored retry of this journal. The wire key
    is the segment's stable identity: if the journal is later lost and the
    canonical routing changes, a regenerating retry resolves a new destination
    but still matches ``forgotten_keys`` through it.
    """
    return _routes._segment_subject_keys(segment) - {"None", ""}


def _fenced_journal_keys(staging: dict, indexes: set[int]) -> set[str]:
    """Fallback fence keys of the segments a recheck fenced (see :func:`_segment_fence_keys`)."""
    segments = staging.get("segments") if isinstance(staging, dict) else None
    if not isinstance(segments, list):
        return set()
    return set().union(*(
        _segment_fence_keys(segments[index]) for index in indexes if 0 <= index < len(segments)
    ))


def _staged_segment_subjects(staging: dict, contexts: list[dict]) -> list:
    """The subject each staged segment is applied to (the journal's, not today's routing).

    A restored retry applies every segment to the subject recorded in the
    staging document, which may differ from the current routing; the
    forget-generation baseline and its recheck must watch that same subject.
    """
    from memory.scopes import coerce_subject

    segments = staging.get("segments") if isinstance(staging, dict) else None
    subjects = []
    for index, context in enumerate(contexts):
        segment = segments[index] if isinstance(segments, list) and index < len(segments) else None
        recorded = coerce_subject(segment.get("subject")) if isinstance(segment, dict) and segment.get("subject") else None
        subjects.append(recorded if recorded is not None else context["subject"])
    return subjects


async def _apply_keyed_staging(
    lanlan_name: str,
    key: str,
    staging: dict,
    generations: dict[int, int] | None = None,
    display_names: dict[int, str | None] | None = None,
    fact_generations: dict[int, int] | None = None,
) -> None:
    """Apply every item not yet in ``applied``; journal each one atomically.

    Tombstone rule, per item and by forget GENERATION only (never time, never
    arrival order): when any of the item's subject keys carries a tombstone
    whose ``forget_epoch`` exceeds the epoch the request was started with,
    the item is dropped and journaled as ``dropped_tombstone``.

    For fact items the fact store's forget generation is captured BEFORE the
    tombstone read and passed as ``expected_subject_generation``: a forget
    that starts after the tombstone check but before persistence bumps that
    generation and the fact store discards the write by itself.

    ``generations`` (segment index -> generation) is given only on the
    request that generated the staging: the values were captured BEFORE the
    LLM call, so a forget landing while the model ran (a window with no
    staging file to cancel and, without ``forget_epoch``, no tombstone)
    still invalidates the write, as on the unkeyed path. A retry restored
    from an existing staging file passes ``fact_generations`` instead,
    captured under the key lock just before applying: the pre-erase
    cancellation scan is best effort and may skip that staging, so a forget
    running during the restored apply must still invalidate its fact writes
    (and the trust step compares against the same baseline).
    ``generations is None`` keeps meaning "restored retry" for the display
    name handling.
    """
    from memory.scopes import coerce_subject

    from . import idempotency

    if generations is None and display_names:
        _restore_missing_display_items(staging, display_names)
    applied = list(staging.get("applied") or [])
    done_seqs = {
        entry.get("seq") for entry in applied if isinstance(entry, dict)
    }
    journaled_before = {
        entry.get("seq") for entry in applied
        if isinstance(entry, dict) and not any(entry.get(name) is True for name in _KEYED_DROP_MARKERS)
    }
    epochs = staging.get("epochs") or {}
    segments = staging.get("segments") or []
    for item in staging.get("items") or []:
        seq = item.get("seq")
        if seq in done_seqs:
            continue
        segment = segments[item["segment"]]
        generation = None
        fenced_now: set[str] = set()
        if item.get("kind") == _KEYED_ITEM_FACTS:
            baseline = generations if generations is not None else fact_generations
            if baseline is not None and item["segment"] in baseline:
                generation = baseline[item["segment"]]
            else:
                generation = _routes.runtime.fact_store._subject_forget_generation(
                    lanlan_name, coerce_subject(segment["subject"]),
                )
        tombstones = await idempotency.read_tombstones(lanlan_name)
        tombstone_epoch = idempotency.tombstone_epoch(
            tombstones, segment.get("tombstone_keys") or [],
        )
        request_epoch = epochs.get(segment.get("wire_key"), 0)
        if isinstance(segment, dict) and segment.get(_SEGMENT_DROPPED_BY_FORGET) is True:
            # Segment already marked cleared/discarded (e.g. generation changed while applying facts): do not write later items
            # of that segment (display names follow facts), or recreate names just erased by clear.
            entry = {"seq": seq, "dropped_forget": True}
        elif tombstone_epoch is not None and int(request_epoch) < tombstone_epoch:
            entry = {"seq": seq, "dropped_tombstone": True}
        elif item.get("kind") == _KEYED_ITEM_DISPLAY_NAME:
            if generations is None:
                # Recovery retry uses current request display names, not stale staged names:
                # newer writes may have changed them; restoring old values regresses, but omitting leaves this batch nameless forever.
                current = (display_names or {}).get(item["segment"])
                to_apply = {**item, "display_name": current} if current else None
                done_entry = {"seq": seq, "display_name_from_request": bool(current)}
            else:
                to_apply, done_entry = item, None
            try:
                applied_entry = (
                    await _routes._apply_keyed_item(lanlan_name, to_apply, segment, None)
                    if to_apply is not None else None
                )
                entry = done_entry or applied_entry
            except Exception:
                # Display names are cosmetic: persistent failures (read-only directory/full disk) cannot block the key forever,
                # also starving trust-pool writes. After several failed retries, log and finalize as handled.
                attempts = int(item.get("display_attempts") or 0) + 1
                if attempts < _DISPLAY_NAME_MAX_ATTEMPTS:
                    item["display_attempts"] = attempts
                    await idempotency.write_staging(lanlan_name, key, staging)
                    raise
                _routes.logger.warning(f"[scoped_history] {lanlan_name}: 显示名写入连续失败，放弃这一项")
                entry = {"seq": seq, "display_name_gave_up": True}
        else:
            entry = await _routes._apply_keyed_item(lanlan_name, item, segment, generation)
            if item.get("kind") == _KEYED_ITEM_FACTS and generation is not None and (
                _routes.runtime.fact_store._subject_forget_generation(lanlan_name, coerce_subject(segment["subject"]))
                != generation
            ):
                # Generation-free clear advanced generation around application: fact layer silently discarded these writes. Mark this
                # segment discarded so trust isolation and memory discard use identical evidence.
                _routes._mark_segments_dropped(segments, {item["segment"]})
                fenced_now = _segment_fence_keys(segments[item["segment"]])
        applied.append(entry)
        done_seqs.add(seq)
        staging["applied"] = applied
        if fenced_now:
            # Persist new segment marks through _persist_fence_marks like the rechecks, falling back to key records on failure;
            # otherwise same-key retries baselined after clear could restore this segment's pre-clear facts.
            await _persist_fence_marks(lanlan_name, key, staging, fenced_now)
        else:
            await idempotency.write_staging(lanlan_name, key, staging)
    if generations is None and display_names:
        # Recovery retries reapply even previously written display-name items with current request values (idempotent set):
        # names are outside request identity; changes while pending must not finalize stale names. Do not rename cleared segments.
        fenced = await _routes._fenced_segments(lanlan_name, staging)
        for item in staging.get("items") or []:
            if (
                item.get("kind") == _KEYED_ITEM_DISPLAY_NAME and item.get("seq") in journaled_before
                and item.get("segment") not in fenced
            ):
                current = display_names.get(item["segment"])
                if not current:
                    continue
                try:
                    await _routes._apply_keyed_item(
                        lanlan_name, {**item, "display_name": current}, segments[item["segment"]], None,
                    )
                except MaintenanceModeError:
                    raise
                except Exception as exc:
                    # Best-effort refresh only: persistently unwritable persona must not prevent done forever.
                    # Retry even previously abandoned display-name items: once writable, finalization retries can restore names.
                    _routes.logger.warning(f"[scoped_history] {lanlan_name}: 恢复重试刷新显示名失败，跳过: {exc}")


def _keyed_response(
    shape: str,
    contexts: list[dict],
    staging: dict,
    trust_result,
    trust_outcomes,
) -> dict:
    segment_of_seq = {
        item.get("seq"): item.get("segment") for item in staging.get("items") or []
    }
    per_segment: list[dict] = [
        {
            "fact_ids": [],
            "created_fact_identities": [],
            "reconciled": [],
            "reconciled_fact_identities": [],
        }
        for _ in contexts
    ]
    for entry in staging.get("applied") or []:
        index = segment_of_seq.get(entry.get("seq"))
        if index is None or not (0 <= index < len(per_segment)):
            continue
        for field in (
            "fact_ids", "created_fact_identities", "reconciled",
            "reconciled_fact_identities",
        ):
            per_segment[index][field].extend(entry.get(field) or [])
    stored_segments = staging.get("segments") or []
    if shape == "single":
        context = contexts[0]
        return {
            "status": "processed",
            "subject": context["subject"].as_entry_fields(),
            "created": len(per_segment[0]["fact_ids"]),
            "fact_ids": per_segment[0]["fact_ids"],
            "trust": _trust_response_block(
                context["trust_state"], trust_result, trust_outcomes[0],
            ),
            "trust_events": [],
        }
    return {
        "status": "processed",
        "segments": [
            {
                "subject": context["subject"].as_entry_fields(),
                "trust": _trust_response_block(
                    context["trust_state"], trust_result, outcome,
                ),
                "status": "ok",
                "created": len(collected["fact_ids"]),
                "dropped": int(
                    (stored_segments[index] if index < len(stored_segments) else {})
                    .get("dropped") or 0
                ),
                "fact_ids": collected["fact_ids"],
                "fact_identities": (
                    list(collected["created_fact_identities"])
                    + list(collected["reconciled_fact_identities"])
                ),
                "created_fact_identities": list(collected["created_fact_identities"]),
                "reconciled": [{"id": fid} for fid in collected["reconciled"]],
                "trust_events": [],
            }
            for index, (context, collected, outcome) in enumerate(
                zip(contexts, per_segment, trust_outcomes)
            )
        ],
    }


async def _process_scoped_history_keyed(
    lanlan_name: str,
    req: ScopedHistoryRequest,
    *,
    shape: str,
    contexts: list[dict],
    prompt_segments: list[dict] | None = None,
) -> dict:
    """The keyed (journaled) form of /scoped_history, both shapes."""
    from . import idempotency

    key = req.idempotency_key
    missing_epochs = [
        context["wire_subject"].key for context in contexts
        if not req.subject_epochs or context["wire_subject"].key not in req.subject_epochs
    ]
    if missing_epochs:
        # Missing generation cannot mean zero: after one generation-bearing clear, tombstones would silently discard
        # all later writes while marking keys done. Keyed requests must provide every wire subject's generation.
        raise HTTPException(
            status_code=422,
            detail="idempotency_key requires subject_epochs for every subject",
        )
    if any(context["wire_subject"].scope != context["wire_subject"].key for context in contexts):
        # Tombstones/cancellation matching/cleared generations use MemorySubject.key (kind:id), without scope: only default
        # scope makes a key identify one domain uniquely. Sole caller (visit digest) uses default scope only.
        raise HTTPException(
            status_code=422,
            detail="idempotency_key requires the default subject scope",
        )
    # Request identity (form + positional wire subjects) remains permanently with key records; terminal key reuse by a different
    # request cannot silently count as already handled.
    fingerprint = {
        "shape": shape,
        "wire_keys": [context["wire_subject"].key for context in contexts],
        "content_hash": _routes._keyed_request_hash(req),
    }
    # Actual routed write subjects are separately stored on pending records (not request identity: routes may change between
    # retries). Before staging exists, this alone lets clear identify keys routing writes to the cleared subject.
    routed_keys = sorted({context["subject"].key for context in contexts})
    # Store routed subject per position; recovery checks targets positionally because sets cannot detect swapped targets.
    routed_positions = [context["subject"].key for context in contexts]
    # Store request clear generations on pending records too: before staging exists, clear identifies valid requests started
    # after clearing and leaves them uncancelled (same accounting as _staged_after_forget for staged data).
    request_epochs = {
        wire_key: int(req.subject_epochs[wire_key])
        for wire_key in sorted({context["wire_subject"].key for context in contexts})
        if req.subject_epochs and wire_key in req.subject_epochs
    }
    async with idempotency.key_lock(lanlan_name, key):
        try:
            record = await idempotency.read_key(lanlan_name, key)
            # In every state, validate recorded request identity first: pending keys with lost staging cannot let
            # another request regenerate under this key and overwrite its original identity.
            stored = record.get("request") if record is not None else None
            if stored is not None and stored != fingerprint:
                raise HTTPException(
                    status_code=422,
                    detail="idempotency_key was already used for a different request",
                )
            if (
                record is not None
                and record.get("state") in idempotency.TERMINAL_KEY_STATES
            ):
                if stored is None:
                    # Terminal record lacks request identity: equality cannot be checked, so arbitrary new requests reusing the key
                    # cannot be swallowed as duplicate.
                    raise idempotency.IdempotencyStateError("terminal key record has no request identity")
                # Useless staging left after terminal finalization: delete now under key lock, without waiting for next startup cleanup
                # (startup skips busy key locks).
                await _routes._drop_leftover_staging(lanlan_name, key)
                return _keyed_duplicate_response(shape, contexts)
            staging = await idempotency.read_staging(lanlan_name, key)
        except idempotency.IdempotencyStateError as exc:
            _routes.logger.error(f"[scoped_history] {lanlan_name}: 幂等记录不可读: {exc}")
            raise HTTPException(
                status_code=503,
                detail="idempotency state unreadable; retry later",
            ) from exc
        if staging is not None and staging.get(idempotency.UNREADABLE_CANCELLED_MARKER) is True:
            # Staging unreadable/unclaimed at clear time had original text erased: finalize cancelled (repair cancelled record), never
            # treat as absent staging and regenerate, which would restore the cleared subject.
            try:
                await idempotency.update_key(
                    lanlan_name, key,
                    idempotency.transition(idempotency.KEY_STATE_CANCELLED, request=fingerprint),
                )
                await idempotency.delete_staging(lanlan_name, key)
            except MaintenanceModeError:
                raise
            except Exception as exc:
                _routes.logger.error(f"[scoped_history] {lanlan_name}: 补记 cancelled 失败: {exc}")
                raise HTTPException(
                    status_code=503,
                    detail="idempotency state unreadable; retry later",
                ) from exc
            return _keyed_duplicate_response(shape, contexts)
        if staging is not None and not _keyed_staging_matches(
            staging, shape, contexts, fingerprint["content_hash"],
        ):
            raise HTTPException(
                status_code=422,
                detail="idempotency_key was already used for a different request",
            )
        if (
            staging is not None
            and staging.get(_KEYED_STAGING_CANCELLED) is True
            and not _routes._is_cancelled_staging_marker(staging)
        ):
            # Cancellation flag without clear's fully erased shape: one boolean cannot cancel the whole request
            # and delete output; treat as corrupt staging.
            _routes.logger.error(f"[scoped_history] {lanlan_name}: 暂存取消标记形状不符，拒绝处理")
            raise HTTPException(
                status_code=503,
                detail="idempotency state unreadable; retry later",
            )
        routed_on_record = record.get("routed_keys") if isinstance(record, dict) else None
        positions_on_record = record.get("routed_positions") if isinstance(record, dict) else None
        if isinstance(record, dict) and (
            ("routed_positions" in record and positions_on_record is None)
            # These fields are always written together: set without positional routes is corrupt, never set-only fallback.
            or ("routed_keys" in record and "routed_positions" not in record)
        ):
            positions_on_record = _MALFORMED
        if record is None:
            # Orphan staging (lost record/reset key file): no trusted record; validate each write target against current request routing
            # (allow only matches), rather than accepting wire subjects alone and permanently 503'ing routed segments.
            positions_on_record = routed_positions
        if (
            staging is not None
            # Cancellation markers retain only identity (text erased); branch below records cancelled, without per-item validation.
            and staging.get(_KEYED_STAGING_CANCELLED) is not True
            and not _keyed_staging_items_valid(
                staging, routed_on_record if isinstance(routed_on_record, list) else None,
                dict(req.subject_epochs or {}), req.language,
                positions_on_record,
            )
        ):
            # Corrupt staged items (out-of-range/negative segment indices, bad sequences, mismatched effect keys) must never apply;
            # negative indices would write facts to another subject.
            _routes.logger.error(f"[scoped_history] {lanlan_name}: 暂存条目结构损坏，拒绝应用")
            raise HTTPException(
                status_code=503,
                detail="idempotency state unreadable; retry later",
            )
        if staging is not None and staging.get(_KEYED_STAGING_CANCELLED) is True:
            # Clear could not read key file and recorded cancellation in staging only: repair cancelled, delete staging, never apply.
            try:
                await idempotency.update_key(
                    lanlan_name,
                    key,
                    idempotency.transition(
                        idempotency.KEY_STATE_CANCELLED, request=fingerprint,
                    ),
                )
                await idempotency.delete_staging(lanlan_name, key)
            except MaintenanceModeError:
                raise
            except Exception as exc:
                _routes.logger.error(f"[scoped_history] {lanlan_name}: 补记 cancelled 失败: {exc}")
                raise HTTPException(
                    status_code=503,
                    detail="idempotency state unreadable; retry later",
                ) from exc
            return _keyed_duplicate_response(shape, contexts)
        if staging is None and record is not None and stored is None:
            # Pending record without request identity or staging offers no proof this retry matches the original
            # (some effects may have applied before staging loss, causing sequence-derived effect-key collisions).
            # Cannot adopt it as a new request.
            _routes.logger.error(f"[scoped_history] {lanlan_name}: pending 记录缺请求身份且无暂存，拒绝接手")
            raise HTTPException(
                status_code=503,
                detail="idempotency state unreadable; retry later",
            )
        if staging is not None and stored is None:
            # Orphan staging from failure between staged write and pending key record: first repair a pending record with
            # request identity, then apply; otherwise eventual done lacks identity to validate.
            try:
                await idempotency.update_key(
                    lanlan_name,
                    key,
                    idempotency.transition(
                        idempotency.KEY_STATE_PENDING,
                        client_requested_at=req.client_requested_at,
                        request=fingerprint,
                        routed_keys=routed_keys,
                        routed_positions=routed_positions,
                        epochs=request_epochs,
                    ),
                )
            except MaintenanceModeError:
                raise
            except Exception as exc:
                _routes.logger.error(f"[scoped_history] {lanlan_name}: 补记 pending 失败: {exc}")
                raise HTTPException(
                    status_code=503,
                    detail="scoped history staging failed; retry with the same key",
                ) from exc
        generations = None
        if staging is None:
            # Before LLM call, persist pending with request identity and routed subjects: even failed generation leaves
            # a durable record that later clear can cancel; otherwise same-key retry after clear re-extracts using post-clear
            # generation and restores old content.
            try:
                await idempotency.update_key(
                    lanlan_name,
                    key,
                    idempotency.transition(
                        idempotency.KEY_STATE_PENDING,
                        client_requested_at=req.client_requested_at,
                        request=fingerprint,
                        routed_keys=routed_keys,
                        routed_positions=routed_positions,
                        epochs=request_epochs,
                    ),
                )
            except MaintenanceModeError:
                raise
            except Exception as exc:
                _routes.logger.error(f"[scoped_history] {lanlan_name}: 预记 pending 失败: {exc}")
                raise HTTPException(
                    status_code=503,
                    detail="scoped history staging failed; retry with the same key",
                ) from exc
            # Clear during an earlier attempt already put all segment subjects in forgotten_keys: all outputs will be
            # discarded; skip full extraction under key lock and finalize cancellation directly.
            try:
                prior = await idempotency.read_key(lanlan_name, key)
            except idempotency.IdempotencyStateError as exc:
                raise HTTPException(
                    status_code=503, detail="idempotency state unreadable; retry later",
                ) from exc
            try:
                prior_forgotten = _routes._forgotten_keys_of(prior)
            except idempotency.IdempotencyStateError as exc:
                raise HTTPException(
                    status_code=503, detail="idempotency state unreadable; retry later",
                ) from exc
            if prior_forgotten and all(
                {context["wire_subject"].key, context["subject"].key} & prior_forgotten
                for context in contexts
            ):
                try:
                    await idempotency.update_key(
                        lanlan_name, key, idempotency.transition(idempotency.KEY_STATE_CANCELLED),
                    )
                except MaintenanceModeError:
                    raise
                except Exception as exc:
                    raise HTTPException(
                        status_code=503, detail="idempotency state unreadable; retry later",
                    ) from exc
                return _keyed_duplicate_response(shape, contexts)
            # Same timing as unkeyed extract_facts: snapshot each subject's forget generation before LLM call,
            # retaining in memory only. Clear during generation (no staging to cancel yet,
            # and no tombstone without forget_epoch) advances generation, allowing fact storage
            # to discard this write during first application.
            generations = _capture_forget_generations(lanlan_name, [context["subject"] for context in contexts])
            try:
                staging = await _build_keyed_staging(
                    lanlan_name,
                    req,
                    shape=shape,
                    contexts=contexts,
                    prompt_segments=prompt_segments,
                )
            except idempotency.IdempotencyStateError as exc:
                # Corrupt key-record state (e.g. language-order reservation): respond as for unreadable idempotency state elsewhere.
                raise HTTPException(
                    status_code=503, detail="idempotency state unreadable; retry later",
                ) from exc
            except BaseException:
                # Failed/terminated generation: intervening clear durably recorded cleared subjects on this key
                # (forgotten_keys); same-key regeneration retries discard those segments, requiring no repair here.
                raise
            # If clear advanced a subject's forget generation during generation, mark its output discarded
            # before persistence. Otherwise a crash after staging but before application leaves retries only the post-clear
            # generation, wrongly treating pre-clear facts as new and restoring them.
            _routes._mark_items_forgotten_during_generation(lanlan_name, staging, contexts, generations)
            # Clear during this or earlier generation (key lock busy, so only records cleared subjects):
            # mark those segment outputs discarded too. After earlier generation failure/process kill, this retry arrives here
            # with post-clear generation; only the durable record identifies them.
            try:
                forgotten_during = _routes._forgotten_keys_of(await idempotency.read_key(lanlan_name, key))
            except idempotency.IdempotencyStateError as exc:
                raise HTTPException(
                    status_code=503, detail="idempotency state unreadable; retry later",
                ) from exc
            if forgotten_during:
                _routes._drop_segments_for_keys(staging, forgotten_during)
            try:
                await idempotency.write_staging(lanlan_name, key, staging)
            except MaintenanceModeError:
                raise
            except Exception as exc:
                _routes.logger.error(f"[scoped_history] {lanlan_name}: 暂存落盘失败: {exc}")
                raise HTTPException(
                    status_code=503,
                    detail="scoped history staging failed; retry with the same key",
                ) from exc
            # Recheck after staging persists: clear completed during the two writes above could not see staging during
            # cancellation scan. It necessarily advanced generation before scanning, so this check sees it.
            # Later clears find/cancel staging under key lock. Persist newly marked segments like the other two rechecks via
            # _persist_fence_marks (fall back to key records on write failure).
            newly = _routes._mark_items_forgotten_during_generation(lanlan_name, staging, contexts, generations)
            if newly:
                await _persist_fence_marks(
                    lanlan_name, key, staging, _fenced_journal_keys(staging, newly),
                )
        if generations is None:
            # Recovery retries likewise discard segments in recorded forgotten_keys: clear during generation marked only records,
            # then staging persisted but application failed; this recovery path must not restore cleared segments.
            try:
                forgotten_now = _routes._forgotten_keys_of(await idempotency.read_key(lanlan_name, key))
            except idempotency.IdempotencyStateError as exc:
                raise HTTPException(
                    status_code=503, detail="idempotency state unreadable; retry later",
                ) from exc
            if forgotten_now & _routes._staged_subject_keys(staging):
                _routes._drop_segments_for_keys(staging, forgotten_now)
                try:
                    await idempotency.write_staging(lanlan_name, key, staging)
                except MaintenanceModeError:
                    raise
                except Exception as exc:
                    raise HTTPException(
                        status_code=503,
                        detail="scoped history staging failed; retry with the same key",
                    ) from exc
        # Shared generation baseline for application and trust: initial generation request uses the pre-LLM snapshot; recovery retries snapshot here
        # (under key lock, before application). Later clear advances generation, proving staging predates it; fact layer discards
        # facts and trust step isolates based on that same evidence.
        trust_generations = (
            generations if generations is not None
            else _capture_forget_generations(lanlan_name, _staged_segment_subjects(staging, contexts))
        )
        newly = (
            _routes._mark_items_forgotten_during_generation(lanlan_name, staging, contexts, trust_generations)
            if generations is None else set()
        )
        if newly:
            # Symmetric with generation-path post-staging recheck: freshly taken baseline equals current generation, so only an active clear
            # triggers here. Fact layer rejects this segment without markers; mark first or later display names would recreate cleared subjects.
            await _persist_fence_marks(
                lanlan_name, key, staging, _fenced_journal_keys(staging, newly),
            )
        try:
            await _routes._apply_keyed_staging(
                lanlan_name, key, staging, generations,
                {index: context.get("display_name") for index, context in enumerate(contexts)},
                fact_generations=trust_generations,
            )
        except (HTTPException, MaintenanceModeError):
            raise
        except Exception as exc:
            _routes.logger.error(
                f"[scoped_history] {lanlan_name}: 暂存应用中断（保留暂存，"
                f"同键重试只补剩余项）: {exc}"
            )
            raise HTTPException(
                status_code=503,
                detail="scoped history apply interrupted; retry with the same key",
            ) from exc
        # Keyed paths have no owner signals (entry already rejects with 422); activity / channel only,
        # Pool mutations are idempotent by event ID, so every attempt can replay unchanged.
        trust_states = [context["trust_state"] for context in contexts]
        for state in trust_states:
            state["trust_signal_events"] = ()
        # Generation-free clear may advance generation during application: only fact items compare it, missing segments with
        # activity/language items alone. Recheck against baseline under the fact-store persistence lock, together with trust writes:
        # clear advances generation under this same lock (abegin_subject_forget), matching compare-then-persist facts semantics;
        # no clear can intervene between recheck and trust persistence. Pool lock is a leaf, and no FactStore calls occur while held, avoiding cycles.
        async with _routes.runtime.fact_store._get_persist_alock(lanlan_name):
            newly = _routes._mark_items_forgotten_during_generation(lanlan_name, staging, contexts, trust_generations)
            if newly:
                # Persist segment marks so same-key recovery retries are blocked even if pool persistence failed.
                await _persist_fence_marks(
                    lanlan_name, key, staging, _fenced_journal_keys(staging, newly),
                )
            # Segments blocked by clear (tombstone/discard) lost their memory; their activity/channel must not reenter trust pools.
            try:
                fenced = await _routes._fenced_segments(lanlan_name, staging)
            except idempotency.IdempotencyStateError as exc:
                raise HTTPException(
                    status_code=503, detail="idempotency state unreadable; retry later",
                ) from exc
            # On cancellation wait for unstoppable threaded pool writes to finish (aapply_trust_mutations), retaining persistence lock until then.
            trust_result, trust_outcomes = await _routes._apply_trust_for_segments([
                _routes._without_trust_mutation(state) if index in fenced else state
                for index, state in enumerate(trust_states)
            ])
        response = _keyed_response(
            shape, contexts, staging, trust_result, trust_outcomes,
        )
        if trust_result is not None and not trust_result.persisted:
            # Pool not persisted: truthfully report persisted=false for same-key retry; keep key pending
            # and staging intact, or duplicate retries would discard this activity batch.
            return response
        try:
            await idempotency.update_key(
                lanlan_name,
                key,
                idempotency.transition(idempotency.KEY_STATE_DONE),
            )
        except MaintenanceModeError:
            raise
        except Exception as exc:
            _routes.logger.error(f"[scoped_history] {lanlan_name}: 键标 done 失败: {exc}")
            raise HTTPException(
                status_code=503,
                detail="scoped history finalize failed; retry with the same key",
            ) from exc
        try:
            await idempotency.delete_staging(lanlan_name, key)
        except Exception as exc:  # noqa: BLE001 - key is done; leftover is swept by TTL
            _routes.logger.warning(
                f"[scoped_history] {lanlan_name}: 删除暂存失败（键已 done，"
                f"残留由启动清理回收）: {exc}"
            )
        return response
