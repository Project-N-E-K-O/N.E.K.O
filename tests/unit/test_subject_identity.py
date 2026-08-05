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


def test_a_custom_scope_is_never_folded_or_rerouted():
    """N-1: a custom scope is an isolation boundary the caller declared.

    Silently redirecting it would void that boundary on the caller's behalf,
    and it matches the domain of the two subject foldings already in the tree.
    """
    trust_store._set_load_failed(False)
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
