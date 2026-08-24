"""Participant folding, read-side expansion and canonical write routing.

The invariants here are the reason the whole design exists: one person holds one
slot, one heading and one id, while the stored bytes of ``subject_id`` /
``scope`` never change.
"""
from __future__ import annotations

import random

import pytest

from memory import subject_identity, trust_store
from memory.scopes import MemorySubject, flatten_groups
from memory.subject_identity import (
    canonical_subject,
    expand_subject,
    fold_participants,
    group_for_marker,
    participant_key,
)

GROUP = MemorySubject.group_chat("qq", "G")
A1 = MemorySubject.group_participant("qq", "G", "111")
A2 = MemorySubject.group_participant("qq", "G", "222")
OTHER = MemorySubject.group_participant("qq", "G", "999")
P1 = MemorySubject.participant("qq", "111")
P2 = MemorySubject.participant("qq", "222")


@pytest.fixture(autouse=True)
def pool(tmp_path, monkeypatch):
    path = tmp_path / "speaker_trust.json"
    monkeypatch.setattr(trust_store, "pool_path", lambda: str(path))
    trust_store.reset_for_tests()
    yield path
    trust_store.reset_for_tests()


async def _linked(*accounts: str) -> str:
    """Register accounts and bind them all into one entity."""
    await trust_store.awaive_legacy_barrier("qq")
    entity_id = await trust_store.aensure_account(accounts[0])
    for account in accounts[1:]:
        await trust_store.aensure_account(account)
        await trust_store.abind_account(account, entity_id)
    return entity_id


def _snap():
    return trust_store.trust_snapshot()


# ── I-P-1: identity degradation is THE most important property ─────────────

@pytest.mark.parametrize("subjects", [
    [GROUP, A1, A2],
    [P1, P2],
    [MemorySubject.group_chat("qq", "G2")],
])
def test_unloaded_pool_degrades_to_the_exact_identity(subjects):
    trust_store._set_load_failed(True)
    try:
        snap = _snap()
        groups = fold_participants(subjects, snap)
        assert flatten_groups(groups) == tuple(subjects)
        assert [group.primary for group in groups] == subjects
        for subject in subjects:
            assert canonical_subject(subject, snap) == subject
            assert expand_subject(subject, snap) == (subject,)
    finally:
        trust_store._set_load_failed(False)


async def test_unregistered_accounts_degrade_to_the_identity():
    await trust_store.awaive_legacy_barrier("qq")
    snap = _snap()
    groups = fold_participants([GROUP, A1, A2], snap)
    assert flatten_groups(groups) == (GROUP, A1, A2)
    assert canonical_subject(A1, snap) == A1


async def test_a_single_account_entity_degrades_to_the_identity():
    await _linked("qq:111")
    await trust_store.aseal_canonical("qq:111")
    snap = _snap()
    assert canonical_subject(A1, snap) == A1
    assert expand_subject(A1, snap) == (A1,)
    assert flatten_groups(fold_participants([GROUP, A1], snap)) == (GROUP, A1)


async def test_a_custom_scope_is_never_folded_or_rerouted():
    """N-1: a custom scope is an isolation boundary the caller declared.

    Silently redirecting it would void that boundary on the caller's behalf,
    and it matches the domain of the two subject foldings already in the tree.

    The binding below is load-bearing: with no registered accounts the
    resolver degrades to the identity anyway, so the assertions would pass
    whether or not the custom-scope guard exists. With a real alias plus a
    sealed canonical, the DEFAULT-scope twin genuinely reroutes — which is what
    makes this a test of the guard rather than of an empty pool.
    """
    trust_store._set_load_failed(False)
    await _linked("qq:111", "qq:222")
    await trust_store.aseal_canonical("qq:222")
    assert canonical_subject(A1, _snap()).subject_id == "qq:G:222"
    custom = MemorySubject.create(
        "group_participant", "qq:G:111", scope="tenant-a",
    )
    snap = _snap()
    assert participant_key(custom, snap) == (
        custom.kind, custom.subject_id, custom.scope,
    )
    assert canonical_subject(custom, snap) == custom
    assert expand_subject(custom, snap) == (custom,)


# ── I-S2-1: one participant, one slot ───────────────────────────────────────

async def test_two_accounts_of_one_person_fold_into_a_single_slot():
    """The request really does carry both: ``_recent_other_speakers`` dedupes
    by sender_id (account), not by person."""
    await _linked("qq:111", "qq:222")
    snap = _snap()
    groups = fold_participants([GROUP, A1, A2, OTHER], snap)
    assert len(groups) == 3
    keys = [
        (group.primary.kind, participant_key(group.primary, snap))
        for group in groups
    ]
    assert len(keys) == len(set(keys)), "two slots share a participant"
    member = groups[1]
    assert {marker[0] for marker in member.markers} == {
        "group_participant:qq:G:111", "group_participant:qq:G:222",
    }


async def test_the_symmetric_kinds_fold_too():
    """I-S2-5: participant (private) must not be left behind."""
    await _linked("qq:111", "qq:222")
    snap = _snap()
    groups = fold_participants([P1, P2], snap)
    assert len(groups) == 1
    assert {marker[0] for marker in groups[0].markers} == {
        "participant:qq:111", "participant:qq:222",
    }


async def test_marker_set_is_identical_from_either_account():
    """I-S2-2. Any read-side truncation would break this immediately."""
    await _linked("qq:111", "qq:222")
    snap = _snap()
    assert expand_subject(A1, snap) == expand_subject(A2, snap)
    assert fold_participants([A1], snap)[0].markers == (
        fold_participants([A2], snap)[0].markers
    )


async def test_expansion_always_contains_the_requested_subject():
    """Expansion may only ever GROW the readable domain, never shrink it."""
    await _linked("qq:111", "qq:222")
    snap = _snap()
    for subject in (A1, A2, P1, P2):
        expanded = expand_subject(subject, snap)
        assert (subject.key, subject.scope) in {
            (item.key, item.scope) for item in expanded
        }


async def test_expansion_never_crosses_a_platform():
    """A conversation id is platform-prefixed, so a cross-platform account is
    structurally never part of this participant. Checked, not assumed."""
    entity_id = await _linked("qq:111")
    await trust_store.awaive_legacy_barrier("bili")
    await trust_store.aensure_account("bili:999")
    await trust_store.abind_account("bili:999", entity_id)
    snap = _snap()
    assert snap.same_entity("qq:111", "bili:999") is True
    expanded = expand_subject(A1, snap)
    assert all(
        item.subject_id.split(":")[0] == "qq" for item in expanded
    )


async def test_folding_preserves_first_appearance_order():
    """Subject order IS the render budget priority — folding must not reorder."""
    await _linked("qq:111", "qq:222")
    snap = _snap()
    groups = fold_participants([A2, GROUP, A1], snap)
    assert groups[0].primary.kind == "group_participant"
    assert groups[1].primary == GROUP
    assert len(groups) == 2


async def test_marker_to_group_lookup_is_one_to_one():
    await _linked("qq:111", "qq:222")
    snap = _snap()
    groups = fold_participants([GROUP, A1, A2, OTHER], snap)
    lookup = group_for_marker(groups)
    seen: dict = {}
    for marker, group in lookup.items():
        seen.setdefault(id(group), set()).add(marker)
    assert sum(len(markers) for markers in seen.values()) == len(lookup)
    assert lookup[(A1.key, A1.scope)] is lookup[(A2.key, A2.scope)]


# ── I-P-2 / N-2: canonical must RE-DERIVE its scope ────────────────────────

async def test_canonical_reroutes_and_rederives_the_scope():
    await _linked("qq:111", "qq:222")
    await trust_store.aseal_canonical("qq:222")
    snap = _snap()
    routed = canonical_subject(A1, snap)
    assert routed.subject_id == "qq:G:222"
    assert routed.scope == f"{routed.kind}:{routed.subject_id}"
    # A ``dataclasses.replace`` would keep A1's old scope and orphan the row.
    assert routed.scope != A1.scope


def test_the_resolver_never_uses_dataclasses_replace():
    """Structural guard for N-2: ``replace`` keeps the OLD scope.

    Attribution is byte equality of the ``(key, scope)`` PAIR, so a
    replace-built subject lands in nobody's marker set and every newly written
    row becomes an orphan readable by no one.

    Asserted over the AST rather than the source text, because the module
    docstring necessarily NAMES the banned call while explaining the ban — a
    substring guard would fail on the explanation and pass on a version that
    deleted it.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(subject_identity))
    offenders = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "replace"
            and isinstance(node.value, ast.Name)
            and node.value.id == "dataclasses"
        ):
            offenders.append(f"dataclasses.replace @ line {node.lineno}")
        if isinstance(node, ast.ImportFrom) and node.module == "dataclasses":
            for alias in node.names:
                if alias.name == "replace":
                    offenders.append(f"from dataclasses import replace @ {node.lineno}")
    assert offenders == []
    # And the guard must be able to see one: a call-shaped occurrence in the
    # same position IS detected (mutation check for the assertion itself).
    planted = ast.parse("dataclasses.replace(subject, subject_id='x')")
    assert any(
        isinstance(node, ast.Attribute)
        and node.attr == "replace"
        and isinstance(node.value, ast.Name)
        and node.value.id == "dataclasses"
        for node in ast.walk(planted)
    )


async def test_canonical_write_is_readable_from_every_account(tmp_path):
    """I-S2-4: the reason this whole design exists.

    A row written through canonical routing must be reachable from EVERY
    account of that person in that conversation.
    """
    from memory.scopes import filter_entries_for_subjects

    await _linked("qq:111", "qq:222")
    await trust_store.aseal_canonical("qq:222")
    snap = _snap()
    routed = canonical_subject(A1, snap)
    row = {"text": "hi", **routed.as_entry_fields()}
    for origin in (A1, A2):
        allowed = expand_subject(origin, snap)
        assert filter_entries_for_subjects([row], allowed) == [row]


async def test_canonical_is_identity_while_the_pool_is_unavailable():
    """P-4 interlock: this is what keeps "unknown" from becoming "mixed"."""
    await _linked("qq:111", "qq:222")
    await trust_store.aseal_canonical("qq:222")
    assert canonical_subject(A1, _snap()).subject_id == "qq:G:222"
    trust_store._set_load_failed(True)
    try:
        assert canonical_subject(A1, _snap()) == A1
    finally:
        trust_store._set_load_failed(False)


async def test_malformed_expansion_combinations_are_dropped_not_raised():
    """I-P-4: an actor may legally contain a colon; a subject_id may not."""
    long_conversation = "C" * 200
    subject = MemorySubject.group_participant(
        "qq", long_conversation, "111",
    )
    await _linked("qq:111", "qq:" + "Z" * 90)
    before = subject_identity.expansion_drop_count
    snap = _snap()
    expanded = expand_subject(subject, snap)
    # The oversized combination is dropped and counted, never raised.
    assert subject_identity.expansion_drop_count > before
    assert (subject.key, subject.scope) in {
        (item.key, item.scope) for item in expanded
    }


# ── forget fan-out ──────────────────────────────────────────────────────────

async def test_forget_fans_out_to_the_whole_participant_in_a_stable_order():
    from app.memory_server.routes import _forget_fanout_targets

    await _linked("qq:111", "qq:222")
    targets = _forget_fanout_targets(A1)
    assert [target.subject_id for target in targets] == [
        "qq:G:111", "qq:G:222",
    ]
    # Deterministic order across concurrent forgets ⇒ no lock-ordering deadlock.
    assert targets == sorted(targets, key=lambda item: (item.key, item.scope))
    # Same result from either account.
    assert _forget_fanout_targets(A2) == targets


async def test_forget_never_fans_out_across_platforms():
    """Maintainer decision: cross-platform sweeps are a separate, explicit op."""
    from app.memory_server.routes import _forget_fanout_targets

    entity_id = await _linked("qq:111")
    await trust_store.awaive_legacy_barrier("bili")
    await trust_store.aensure_account("bili:111")
    await trust_store.abind_account("bili:111", entity_id)
    targets = _forget_fanout_targets(A1)
    assert all(
        target.subject_id.split(":")[0] == "qq" for target in targets
    )


# ── archival coalescing ─────────────────────────────────────────────────────

async def test_a_dormant_account_is_not_archived_while_the_person_is_active():
    """I-F-2: without this, canonical routing archives live people at 90 days."""
    from datetime import datetime, timedelta

    from memory.subject_archive import _coalesce_by_participant, find_stale_subjects

    await _linked("qq:111", "qq:222")
    await trust_store.aseal_canonical("qq:222")
    now = datetime(2026, 8, 5, 12, 0, 0)
    last_writes = {
        (A2.key, A2.scope): (A2, now - timedelta(days=1)),
        (A1.key, A1.scope): (A1, now - timedelta(days=200)),
    }
    coalesced = _coalesce_by_participant(last_writes)
    assert find_stale_subjects(coalesced, now=now, stale_days=90) == []
    # Without coalescing the dormant pile WOULD be archived — proving the
    # guard is doing the work rather than the numbers being harmless.
    assert len(
        find_stale_subjects(last_writes, now=now, stale_days=90)
    ) == 1


async def test_coalescing_leaves_an_unrelated_subject_stale():
    from datetime import datetime, timedelta

    from memory.subject_archive import _coalesce_by_participant, find_stale_subjects

    await _linked("qq:111", "qq:222")
    now = datetime(2026, 8, 5, 12, 0, 0)
    last_writes = {
        (A1.key, A1.scope): (A1, now - timedelta(days=1)),
        (OTHER.key, OTHER.scope): (OTHER, now - timedelta(days=200)),
    }
    coalesced = _coalesce_by_participant(last_writes)
    stale = find_stale_subjects(coalesced, now=now, stale_days=90)
    assert [subject.subject_id for subject, _ in stale] == ["qq:G:999"]


# ── stored bytes never change ───────────────────────────────────────────────

async def test_the_plugin_subject_builders_are_untouched_by_all_of_this():
    """T1: the whole point is that stored subject ids stay byte-identical."""
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge

    assert QQMemoryBridge.group_participant_subject("G", "111") == {
        "subject_kind": "group_participant", "subject_id": "qq:G:111",
    }
    assert QQMemoryBridge.participant_subject("111") == {
        "subject_kind": "participant", "subject_id": "qq:111",
    }
    assert QQMemoryBridge.group_subject("G") == {
        "subject_kind": "group_chat", "subject_id": "qq:G",
    }
    assert QQMemoryBridge.speaker_account_id("111") == "qq:111"


async def test_folding_is_stable_under_repeated_application():
    """Folding an already-folded list must be a fixed point."""
    await _linked("qq:111", "qq:222")
    snap = _snap()
    once = fold_participants([GROUP, A1, A2, OTHER], snap)
    twice = fold_participants(flatten_groups(once), snap)
    assert len(twice) == len(once)
    assert flatten_groups(twice) == flatten_groups(once)


async def test_random_request_shapes_never_produce_duplicate_slots():
    """Property form of I-S2-1 over shuffled, duplicated request lists."""
    await _linked("qq:111", "qq:222")
    snap = _snap()
    rng = random.Random(90210)
    pool_of_subjects = [GROUP, A1, A2, OTHER, P1, P2]
    for _ in range(150):
        request = [
            rng.choice(pool_of_subjects) for _ in range(rng.randint(1, 8))
        ]
        groups = fold_participants(request, snap)
        keys = [participant_key(group.primary, snap) for group in groups]
        assert len(keys) == len(set(keys))
        # Authorization never loses a requested subject.
        flat = {(item.key, item.scope) for item in flatten_groups(groups)}
        for subject in request:
            assert (subject.key, subject.scope) in flat


# ── review round 1: the gaps the reviewer found ────────────────────────────

def test_owner_signals_without_a_tier_still_report_persistence():
    """An owner segment sent BEFORE the migration push must not be popped.

    The plugin sets ``speaker_is_owner`` unconditionally but withholds
    ``speaker_tier`` until the legacy push lands. The route still evaluates,
    persists and folds that segment's owner signals, so reporting
    ``persisted: null`` would let the caller pop a bucket whose correction was
    deferred by the barrier or lost to a failed pool write — and the replay
    ring is keyed on THIS request's text, so it would never come back.
    """
    from app.memory_server.routes import _trust_response_block
    from memory.trust_store import MutationOutcome, TrustApplyResult

    no_source = {"has_server_source": False}
    # Nothing to settle at all ⇒ null is correct.
    assert _trust_response_block(
        {"trust_source": no_source}, None, MutationOutcome(),
    )["persisted"] is None
    # Owner signals but no tier ⇒ the write outcome MUST be reported.
    failed = _trust_response_block(
        {"trust_source": no_source, "trust_signal_events": ({"event_id": "e"},)},
        TrustApplyResult(persisted=False),
        MutationOutcome(),
    )
    assert failed["persisted"] is False
    deferred = _trust_response_block(
        {"trust_source": no_source, "trust_signal_events": ({"event_id": "e"},)},
        TrustApplyResult(persisted=True),
        MutationOutcome(signals_deferred=1),
    )
    assert deferred["persisted"] is True
    assert deferred["gated"] == "legacy_import_pending"


def test_a_skipped_participant_hides_its_suppressed_entries_too():
    """Budget-exempt sections must follow their participant off the page.

    ``skipped`` holds the participant's PRIMARY marker, but a suppressed entry
    can be stamped on a non-canonical account of that same person. Without
    folding through the aliases it bypasses the skip and renders as exactly the
    fragment ``SCOPED_RENDER_SUBJECT_MIN_TOKENS`` exists to prevent.
    """
    from memory.persona.rendering import RenderingMixin

    aliases = {
        (A2.key, A2.scope): (A1.key, A1.scope),
        (A1.key, A1.scope): (A1.key, A1.scope),
    }
    skipped = {(A1.key, A1.scope)}
    non_canonical_entry = {"text": "x", "suppress": True, **A2.as_entry_fields()}
    unrelated_entry = {"text": "y", "suppress": True, **OTHER.as_entry_fields()}
    # Without aliases the non-canonical account's entry escapes the skip.
    assert RenderingMixin._entry_is_skipped(
        non_canonical_entry, skipped,
    ) is False
    # With them it is dropped along with the rest of its participant...
    assert RenderingMixin._entry_is_skipped(
        non_canonical_entry, skipped, aliases,
    ) is True
    # ...and an unrelated participant is untouched.
    assert RenderingMixin._entry_is_skipped(
        unrelated_entry, skipped, aliases,
    ) is False


async def test_correction_queue_carries_the_entity_id_for_offline_guarding():
    """The same-person guard must survive a pool it cannot read.

    With the pool loaded the live lookup answers; with it unreadable
    ``same_provenance_source`` returns "unknown" and arbitration would proceed
    between one person's two accounts — the exact self-arbitration the guard
    exists to stop. The persisted entity id closes that window.
    """
    from memory.persona.corrections import CorrectionsMixin
    from memory.speaker_trust import same_provenance_source

    entity_id = await _linked("qq:111", "qq:222")
    queued = CorrectionsMixin._build_correction_list(
        [], "旧说法", "新说法", "关于主人",
        old_speaker_provenance={
            "speaker_id": "qq:111", "speaker_trust": 1.0,
            "speaker_entity_id": entity_id,
        },
        new_speaker_provenance={
            "speaker_id": "qq:222", "speaker_trust": 0.3,
            "speaker_entity_id": entity_id,
        },
    )
    item = queued[0]
    assert item["old_speaker_entity_id"] == entity_id
    assert item["new_speaker_entity_id"] == entity_id
    # The guard's own inputs resolve to "same person" WITHOUT touching the pool.
    trust_store._set_load_failed(True)
    try:
        assert same_provenance_source(
            {"speaker_id": item["old_speaker_id"],
             "speaker_entity_id": item["old_speaker_entity_id"]},
            {"speaker_id": item["new_speaker_id"],
             "speaker_entity_id": item["new_speaker_entity_id"]},
        ) is True
    finally:
        trust_store._set_load_failed(False)


def test_the_queue_row_identity_is_unchanged_by_the_new_field():
    """Adding a key must not alter dedup identity of queued corrections."""
    from memory.persona.corrections import _LEGACY_CORRECTION_IDENTITY_FIELDS

    assert "old_speaker_entity_id" not in _LEGACY_CORRECTION_IDENTITY_FIELDS
    assert "new_speaker_entity_id" not in _LEGACY_CORRECTION_IDENTITY_FIELDS


def test_forget_stats_are_summed_across_the_fan_out_not_overwritten():
    """The response is the operator's only receipt for a privacy operation.

    Per-target ``dict.update`` reports only the LAST account's numbers: if the
    first account deleted rows and a later one deleted none, the endpoint would
    report zero for data it had just erased.
    """
    from app.memory_server.routes import _merge_forget_stats

    stats: dict = {}
    _merge_forget_stats(stats, {"facts": 3, "reflections": 1, "restored": True})
    _merge_forget_stats(stats, {"facts": 0, "reflections": 2, "restored": False})
    assert stats["facts"] == 3
    assert stats["reflections"] == 3
    # A boolean "something happened" must not be flipped back off.
    assert stats["restored"] is True
    # Non-numeric payloads keep the last non-null value; None never clobbers.
    _merge_forget_stats(stats, {"note": "a"})
    _merge_forget_stats(stats, {"note": None})
    assert stats["note"] == "a"
    _merge_forget_stats(stats, "not a dict")
    assert stats["facts"] == 3


# ── review round 3 (CodeRabbit) ────────────────────────────────────────────

async def test_the_same_conversation_id_on_two_platforms_never_folds():
    """The conversation segment is a RAW id, so it collides across platforms.

    One entity active in `qq:G` and `bili:G` must stay two participants: they
    are two conversations, and folding them would share one heading, one
    primary subject_id and one token budget across both. ``expand_subject``
    already isolates members by platform — the participant KEY has to agree.
    """
    entity_id = await _linked("qq:111")
    await trust_store.awaive_legacy_barrier("bili")
    await trust_store.aensure_account("bili:222")
    await trust_store.abind_account("bili:222", entity_id)
    snap = _snap()
    assert snap.same_entity("qq:111", "bili:222") is True

    qq_side = MemorySubject.group_participant("qq", "G", "111")
    bili_side = MemorySubject.group_participant("bili", "G", "222")
    assert participant_key(qq_side, snap) != participant_key(bili_side, snap)
    groups = fold_participants([qq_side, bili_side], snap)
    assert len(groups) == 2
    # Each slot authorizes exactly one platform, AND the two slots cover both.
    # ``marker[0]`` is ``MemorySubject.key`` = "group_participant:<platform>:
    # <conversation>:<actor>", so index 1 is the platform. Asserting only
    # "one platform per group" would still pass if BOTH groups came out as qq.
    per_group = [
        {marker[0].split(":")[1] for marker in group.markers}
        for group in groups
    ]
    assert all(len(platforms) == 1 for platforms in per_group)
    assert {next(iter(platforms)) for platforms in per_group} == {"qq", "bili"}


async def test_a_merge_that_would_clobber_a_ledger_raises_under_O():
    """The guard must not be an ``assert`` — ``-O`` strips those.

    This is the one place a violation would silently overwrite a live account
    sub-ledger (adjustment plus both rings) irreversibly, so it has to fail
    loud regardless of the interpreter's optimize flag.
    """
    import ast
    import inspect

    source = inspect.getsource(trust_store._merge_entities_locked)
    tree = ast.parse(source.lstrip())
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Assert)]
    assert "raise TrustIdentityError" in source

    # And the guard actually fires rather than clobbering.
    await _linked("qq:111")
    snap = _snap()
    survivor_id = snap.entity_of("qq:111")
    draft = trust_store._Draft(trust_store._POOL)
    other_id = "ent_" + "b" * 24
    draft.pool["entities"][other_id] = {
        "entity_id": other_id, "status": "active", "created_at": "2099-01-01",
        "accounts": {"qq:111": {"account_id": "qq:111", "adjustment": -0.5,
                                "message_count": 0,
                                "processed_activity_events": [],
                                "processed_signal_events": []}},
    }
    draft._owned_entities.add(other_id)
    with pytest.raises(trust_store.TrustIdentityError):
        trust_store._merge_entities_locked(
            draft, survivor_id, other_id, now="2026-08-05T00:00:00+00:00",
        )


def test_the_trust_response_block_has_one_shape():
    """A field present in only one branch is a contract callers cannot use."""
    from app.memory_server.routes import _trust_response_block
    from memory.trust_store import MutationOutcome, TrustApplyResult

    empty = _trust_response_block(
        {"trust_source": {"has_server_source": False}}, None, MutationOutcome(),
    )
    filled = _trust_response_block(
        {"trust_source": {"has_server_source": True}},
        TrustApplyResult(persisted=True), MutationOutcome(),
    )
    assert set(empty) == set(filled)
    assert empty["channel_collision"] is False


# ── review round 4 (Codex) ─────────────────────────────────────────────────

async def test_forget_fails_closed_when_the_identity_pool_is_unreadable():
    """A partial erase reported as success is the one outcome worth a 503.

    With the pool unreadable the fan-out set is unknown and ``expand_subject``
    degrades to the requested subject alone — so a non-canonical account whose
    rows were routed into the canonical pile would keep them, while the caller
    is told ``forgotten``.
    """
    from app.memory_server import routes

    await _linked("qq:111", "qq:222")
    # Loaded pool: the fan-out is known, so the endpoint proceeds.
    assert trust_store.trust_snapshot().loaded is True
    assert len(_forget_targets(A1)) == 2

    trust_store._set_load_failed(True)
    stubs = {
        "fact_store": object(), "fact_dedup_resolver": object(),
        "persona_manager": object(), "reflection_engine": object(),
    }
    saved = {name: getattr(routes.runtime, name) for name in stubs}
    try:
        # The endpoint 503s for TWO different reasons, and in a bare unit test
        # the runtime components are all None — so the "memory_server not fully
        # initialized" branch fires FIRST and a bare `status_code == 503`
        # assertion passes whether or not the fail-closed guard exists at all.
        # Stub the components out and assert on the DETAIL to pin the branch.
        for name, stub in stubs.items():
            setattr(routes.runtime, name, stub)
        # Degradation is real: expansion silently narrows to one subject...
        assert expand_subject(A1, _snap()) == (A1,)
        # ...so the endpoint must refuse rather than under-delete quietly.
        request = routes.ScopedForgetRequest(subject={
            "subject_kind": A1.kind, "subject_id": A1.subject_id,
        })
        with pytest.raises(routes.HTTPException) as excinfo:
            await routes.forget_scoped_subject("Neko", request)
        assert excinfo.value.status_code == 503
        assert "identity pool unreadable" in str(excinfo.value.detail)
        assert "not fully initialized" not in str(excinfo.value.detail)
    finally:
        for name, original in saved.items():
            setattr(routes.runtime, name, original)
        trust_store._set_load_failed(False)


def _forget_targets(subject):
    from app.memory_server.routes import _forget_fanout_targets

    return _forget_fanout_targets(subject)


async def test_the_entity_stamp_comes_from_the_request_snapshot():
    """One pool read per request — routing, trust and the entity id agree.

    A fresh live lookup at persistence time would let an unbind landing
    mid-request write the row under the OLD canonical subject while stamping
    the account's NEW entity, and those stranded rows would then miss the
    persisted-entity equality that keeps the mixed pump closed.
    """
    import ast
    import inspect

    from memory import facts as facts_module

    # FactStore must not reach into the pool for this field at all.
    source = inspect.getsource(facts_module)
    assert "_speaker_entity_id_for" not in source
    tree = ast.parse(source)
    provenance_fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_speaker_provenance_of"
    )
    assert "trust_snapshot" not in ast.dump(provenance_fn)

    # And the route fills it from the snapshot it already took.
    entity_id = await _linked("qq:111")
    snap = _snap()
    parsed: dict = {"speaker_id": "qq:111", "trust_source": {
        "has_server_source": True, "tier": "normal", "base": None,
    }}
    from app.memory_server.routes import _stamp_resolved_trust
    _stamp_resolved_trust(parsed, snap)
    assert parsed["speaker_entity_id"] == entity_id


def test_the_locale_resolver_bounds_work_before_canonicalizing():
    """The bound must stay a property of the resolver, not of its callers.

    This helper runs AHEAD of the endpoint's own ``1..8`` rejection, so an
    oversized list would otherwise be fully coerced and canonicalized on its
    way to a 422. Slicing first is also behaviour-preserving for valid input:
    folding can only ever shrink the list.
    """
    import inspect

    from app.memory_server import routes

    source = inspect.getsource(routes._resolve_scoped_memory_language)
    # The slice has to be applied to the ARGUMENT, not to the result.
    assert "_locale_lookup_subjects(subjects)[" not in source
    assert "[:_SCOPED_LOCALE_LOOKUP_LIMIT]" in source
    sliced = source.index("_SCOPED_LOCALE_LOOKUP_LIMIT")
    called = source.index("_locale_lookup_subjects(")
    assert called < sliced, "slice must be inside the call, not after it"

    # Behavioural half: an oversized list only canonicalizes the bounded prefix.
    oversized = [
        MemorySubject.group_participant("qq", "G", str(index))
        for index in range(40)
    ]
    resolved = routes._locale_lookup_subjects(
        oversized[:routes._SCOPED_LOCALE_LOOKUP_LIMIT]
    )
    assert len(resolved) == routes._SCOPED_LOCALE_LOOKUP_LIMIT


def test_stranded_row_counting_decodes_the_actor_segment():
    """An actor containing ``:`` is percent-encoded in the subject, not raw.

    ``stable_speaker_id`` allows a colon inside the actor while
    ``MemorySubject`` escapes it, so the account id reads ``a:b`` and the
    subject segment reads ``a%3Ab``. A raw compare silently never matches and
    reports zero stranded rows — hiding the operator's only remediation signal.
    """
    from memory.subject_identity import subject_actor

    colonful = MemorySubject.group_participant("qq", "G", "a:b")
    assert "%3A" in colonful.subject_id
    assert colonful.subject_id.split(":")[-1] != "a:b"
    assert subject_actor(colonful) == "a:b"
    # Plain actors round-trip unchanged, and kinds without an actor say so.
    assert subject_actor(A1) == "111"
    assert subject_actor(P1) == "111"
    assert subject_actor(GROUP) is None


async def test_identity_mutations_are_refused_while_the_pool_is_unreadable():
    """A human-triggered mutation must not silently become a no-op.

    ``_with_pool_write`` vetoes the write and reports ``persisted=False``, but a
    200 reads as success — and for unbind it is worse: ``stranded_rows`` also
    resolves nothing on an unloaded snapshot, so the operator's only remediation
    signal comes back as a confident ``0``.
    """
    from app.memory_server import routes

    await _linked("qq:111", "qq:222")
    entity_id = _snap().entity_of("qq:111")
    trust_store._set_load_failed(True)
    try:
        for call in (
            routes.bind_identity_account(routes.IdentityBindRequest(
                account_id="qq:333", entity_id=entity_id,
            )),
            routes.unbind_identity_account(routes.IdentityAccountRequest(
                account_id="qq:222",
            )),
            routes.merge_identity_entities(routes.IdentityMergeRequest(
                entity_id=entity_id, other_entity_id=entity_id,
            )),
            routes.forget_identity_entity(routes.IdentityEntityRequest(
                entity_id=entity_id,
            )),
        ):
            with pytest.raises(routes.HTTPException) as excinfo:
                await call
            assert excinfo.value.status_code == 503
            # Pin the branch: these endpoints have no runtime-component check,
            # but asserting the detail keeps the test honest if one is added.
            assert "identity pool unreadable" in str(excinfo.value.detail)
    finally:
        trust_store._set_load_failed(False)
    # Nothing was detached behind the 503.
    assert _snap().same_entity("qq:111", "qq:222") is True
