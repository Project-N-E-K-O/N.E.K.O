"""Server-authoritative trust pool: ledger, barriers, single writer, lifecycle.

Every test points ``pool_path()`` at a tmp file and resets the module singleton,
because the real pool lives at the memory_dir root of the user's install.
"""
from __future__ import annotations

import json
import os
import random
from unittest.mock import patch

import pytest

from memory import trust_store
from memory.identity import activity_count_cap


@pytest.fixture(autouse=True)
def pool(tmp_path, monkeypatch):
    path = tmp_path / "speaker_trust.json"
    monkeypatch.setattr(trust_store, "pool_path", lambda: str(path))
    trust_store.reset_for_tests()
    yield path
    trust_store.reset_for_tests()


async def _open_gate(platform: str = "qq") -> None:
    await trust_store.awaive_legacy_barrier(platform)


def _mutation(account_id: str, *, activity=(), signals=(), channel=None):
    return trust_store.TrustMutation(
        speaker_account_id=account_id,
        activity_events=tuple(
            trust_store.ActivityEvent(id=event_id, count=count)
            for event_id, count in activity
        ),
        signal_events=tuple(signals),
        channel=channel,
    )


# ── barriers: one gate, three paths ─────────────────────────────────────────

async def test_pending_barrier_gates_scoring_activity_and_signals():
    """The single mechanism that prevents import-window double counting."""
    snap = trust_store.trust_snapshot()
    assert snap.barrier_pending("qq") is True
    # 1) scoring abstains entirely — no key is written onto the fact
    assert snap.resolve_trust("qq:1", tier="admin") is None
    # 2) activity is skipped, 3) signals are deferred and counted
    result = await trust_store.aapply_trust_mutations([
        _mutation(
            "qq:1",
            activity=[("activity_aaaaaaaa", 5)],
            signals=[{"speaker_id": "qq:2", "event_id": "e1",
                      "kind": "correction"}],
        ),
    ])
    assert result.signals_deferred == 1
    assert result.activity_applied == 0
    after = trust_store.trust_snapshot()
    assert after.trust_inputs("qq:1") == (0.0, 0)
    assert after.trust_inputs("qq:2") == (0.0, 0)


async def test_barrier_opens_only_on_the_final_chunk():
    await trust_store.aimport_legacy_profiles(
        platform="qq", source="s", profiles={"1": {"adjustment": -0.08}},
        final=False,
    )
    assert trust_store.trust_snapshot().barrier_pending("qq") is True
    await trust_store.aimport_legacy_profiles(
        platform="qq", source="s", profiles={}, final=True,
    )
    assert trust_store.trust_snapshot().barrier_pending("qq") is False


async def test_barrier_is_account_local_not_entity_global():
    """A pending platform must not strip trust on an already-cleared one.

    The opposite reading ("any pending platform makes the whole person
    abstain") would make onboarding a new platform instantly zero that person
    everywhere they were already established.
    """
    await _open_gate("qq")
    entity_id = await trust_store.aensure_account("qq:1")
    await trust_store.aensure_account("bili:1")
    await trust_store.abind_account("bili:1", entity_id)
    with patch.dict(
        trust_store._POOL["legacy_barriers"],
        {"bili": {"status": "pending"}},
        clear=False,
    ):
        snap = trust_store.trust_snapshot()
        assert snap.resolve_trust("qq:1", tier="normal") is not None
        assert snap.resolve_trust("bili:1", tier="normal") is None


# ── resolve_trust: three request conditions + one process-level gate ───────

async def test_resolve_trust_has_exactly_three_none_conditions():
    """I-T-6. A fourth condition would silently change arbitration."""
    await _open_gate()
    snap = trust_store.trust_snapshot()
    # 1) missing / malformed account id
    assert snap.resolve_trust(None, tier="admin") is None
    assert snap.resolve_trust("no-separator", tier="admin") is None
    # 2) barrier pending (covered above, assert the negative here)
    assert snap.barrier_pending("qq") is False
    # 3) neither trust source supplied
    assert snap.resolve_trust("qq:1") is None
    # ...and these are explicitly NOT abstention conditions:
    assert snap.resolve_trust("qq:unregistered", tier="none") is not None
    assert snap.trust_inputs("qq:unregistered") == (0.0, 0)


async def test_an_unreadable_pool_abstains_on_every_platform():
    """The process-level gate, on a different axis from the three above.

    A platform with no seeded barrier would otherwise keep stamping base-only
    scores while the adjustments on disk are unreadable — recording a guess as
    a fact, in a process that has already stopped accepting writes.
    """
    await _open_gate()
    snap = trust_store.trust_snapshot()
    assert snap.resolve_trust("qq:1", tier="admin") is not None
    assert snap.resolve_trust("bili:1", tier="admin") is not None
    trust_store._set_load_failed(True)
    try:
        degraded = trust_store.trust_snapshot()
        assert degraded.loaded is False
        assert degraded.resolve_trust("qq:1", tier="admin") is None
        # ``bili`` has no seeded barrier, so only the process-level gate can
        # stop it — this is the case the barrier cannot cover.
        assert degraded.resolve_trust("bili:1", tier="admin") is None
        assert degraded.resolve_trust("bili:1", base=0.5) is None
    finally:
        trust_store._set_load_failed(False)


async def test_missing_ledger_scores_from_the_permission_tier_alone():
    from config import SPEAKER_TRUST_BY_PERMISSION_LEVEL

    await _open_gate()
    snap = trust_store.trust_snapshot()
    for tier, base in SPEAKER_TRUST_BY_PERMISSION_LEVEL.items():
        assert snap.resolve_trust("qq:new", tier=tier) == pytest.approx(base)


async def test_self_reported_base_is_clamped_on_the_FINAL_score():
    """Clamping only ``base`` leaves 0.8+0.30+0.02 = 1.0, i.e. admin parity.

    The resulting asymmetry is deliberate: ``tier='trusted'`` (also 0.8) may
    earn its way to 1.0 because a platform permission model vouches for it; an
    unauthenticated self-report may not.
    """
    from config import SPEAKER_TRUST_MAX_REPORTED_BASE

    await _open_gate()
    for index in range(30):
        await trust_store.aapply_trust_mutations([
            _mutation("qq:rich", signals=[{
                "speaker_id": "qq:rich", "event_id": f"c{index}",
                "kind": "confirmation",
            }]),
        ])
    snap = trust_store.trust_snapshot()
    assert snap.resolve_trust("qq:rich", base=0.8) == pytest.approx(
        SPEAKER_TRUST_MAX_REPORTED_BASE
    )
    # The tier channel is NOT capped at 0.8 — that is the intended asymmetry.
    assert snap.resolve_trust("qq:rich", tier="trusted") > (
        SPEAKER_TRUST_MAX_REPORTED_BASE
    )


# ── the two axes never share an account ─────────────────────────────────────

async def test_signals_route_to_their_target_not_to_the_speaker():
    """Activity follows the speaker; every signal follows ITS OWN target."""
    await _open_gate()
    await trust_store.aapply_trust_mutations([
        _mutation(
            "qq:owner",
            activity=[("activity_owner001", 4)],
            signals=[
                {"speaker_id": "qq:a", "event_id": "e1", "kind": "correction"},
                {"speaker_id": "qq:b", "event_id": "e2", "kind": "correction"},
                {"speaker_id": "qq:c", "event_id": "e3", "kind": "confirmation"},
            ],
        ),
    ])
    snap = trust_store.trust_snapshot()
    owner_adjustment, owner_activity = snap.trust_inputs("qq:owner")
    assert owner_adjustment == 0.0
    assert owner_activity == 4
    assert snap.trust_inputs("qq:a")[0] < 0
    assert snap.trust_inputs("qq:b")[0] < 0
    assert snap.trust_inputs("qq:c")[0] > 0


async def test_signal_target_is_auto_vivified():
    """The target comes from a durable fact row the server itself wrote."""
    await _open_gate()
    await trust_store.aapply_trust_mutations([
        _mutation("qq:owner", signals=[
            {"speaker_id": "qq:never-seen", "event_id": "e1",
             "kind": "correction"},
        ]),
    ])
    assert trust_store.trust_snapshot().entity_of("qq:never-seen") is not None


# ── the two rings never share an eviction policy ────────────────────────────

async def test_flooding_activity_never_evicts_a_signal_id():
    await _open_gate()
    await trust_store.aapply_trust_mutations([
        _mutation("qq:1", signals=[
            {"speaker_id": "qq:1", "event_id": "keepme", "kind": "correction"},
        ]),
    ])
    for index in range(200):
        await trust_store.aapply_trust_mutations([
            _mutation("qq:1", activity=[(f"activity_{index:08d}", 1)]),
        ])
    record = _account_record("qq:1")
    assert "keepme" in record["processed_signal_events"]
    # Replaying the same signal must stay a no-op.
    before = record["adjustment"]
    await trust_store.aapply_trust_mutations([
        _mutation("qq:1", signals=[
            {"speaker_id": "qq:1", "event_id": "keepme", "kind": "correction"},
        ]),
    ])
    assert _account_record("qq:1")["adjustment"] == pytest.approx(before)


def _account_record(account_id: str) -> dict:
    snap = trust_store.trust_snapshot()
    entity_id = snap.entity_of(account_id)
    return trust_store._POOL["entities"][entity_id]["accounts"][account_id]


# ── write amplification ─────────────────────────────────────────────────────

async def test_saturated_entity_skips_the_disk_write_entirely(pool):
    """R8/R9: without this every flush rewrites the whole pool JSON."""
    await _open_gate()
    cap = activity_count_cap()
    await trust_store.aapply_trust_mutations([
        _mutation("qq:1", activity=[("activity_bulk0001", cap)]),
    ])
    mtime = os.path.getmtime(pool)
    result = await trust_store.aapply_trust_mutations([
        _mutation("qq:1", activity=[("activity_bulk0002", 1)]),
    ])
    assert result.activity_applied == 0
    assert os.path.getmtime(pool) == mtime


async def test_saturation_is_judged_on_the_entity_sum():
    """Three accounts at 7 each = 21 >= cap, though each is individually below."""
    await _open_gate()
    cap = activity_count_cap()
    entity_id = await trust_store.aensure_account("qq:a")
    for account in ("qq:b", "qq:c"):
        await trust_store.aensure_account(account)
        await trust_store.abind_account(account, entity_id)
    per_account = cap // 3 + 1
    for index, account in enumerate(("qq:a", "qq:b", "qq:c")):
        await trust_store.aapply_trust_mutations([
            _mutation(account, activity=[(f"activity_seed{index:04d}",
                                          per_account)]),
        ])
    assert trust_store.trust_snapshot().trust_inputs("qq:a")[1] >= cap
    result = await trust_store.aapply_trust_mutations([
        _mutation("qq:a", activity=[("activity_extra001", 1)]),
    ])
    assert result.activity_applied == 0


# ── failure containment ─────────────────────────────────────────────────────

async def test_failed_write_leaves_the_published_snapshot_untouched():
    """Every mutated container must be a fresh object, at every level."""
    await _open_gate()
    before_index = dict(trust_store._POOL["account_index"])
    with patch.object(
        trust_store, "atomic_write_json", side_effect=OSError("disk full"),
    ):
        result = await trust_store.aapply_trust_mutations([
            _mutation("qq:ghost", activity=[("activity_ghost001", 3)]),
        ])
    assert result.persisted is False
    assert trust_store._POOL["account_index"] == before_index
    assert "qq:ghost" not in trust_store._POOL["account_index"]
    assert trust_store.trust_snapshot().entity_of("qq:ghost") is None


async def test_failed_write_does_not_mutate_an_existing_account_in_place():
    await _open_gate()
    await trust_store.aapply_trust_mutations([
        _mutation("qq:1", activity=[("activity_first0001", 2)]),
    ])
    before = dict(_account_record("qq:1"))
    with patch.object(
        trust_store, "atomic_write_json", side_effect=OSError("nope"),
    ):
        await trust_store.aapply_trust_mutations([
            _mutation("qq:1", activity=[("activity_second001", 3)]),
            _mutation("qq:1", signals=[
                {"speaker_id": "qq:1", "event_id": "x", "kind": "correction"},
            ]),
        ])
    after = _account_record("qq:1")
    assert after["message_count"] == before["message_count"]
    assert after["adjustment"] == before["adjustment"]
    assert after["processed_activity_events"] == (
        before["processed_activity_events"]
    )
    assert after["processed_signal_events"] == (
        before["processed_signal_events"]
    )


async def test_load_failure_vetoes_every_subsequent_write(pool):
    """A read failure must not let the next write overwrite everything."""
    pool.write_text("{ this is not json", encoding="utf-8")
    await trust_store.aload_pool()
    assert trust_store._load_failed is True
    assert trust_store.trust_snapshot().loaded is False
    result = await trust_store.aapply_trust_mutations([
        _mutation("qq:1", activity=[("activity_aaaaaaaa", 1)]),
    ])
    assert result.persisted is False
    # The corrupt file is left exactly as found — never overwritten with {}.
    assert pool.read_text(encoding="utf-8") == "{ this is not json"


async def test_unicode_decode_error_is_caught_at_load(pool):
    """UnicodeDecodeError is a ValueError subclass: neither JSON nor OS error."""
    pool.write_bytes(b"\xff\xfe\x00\x00 not utf-8")
    await trust_store.aload_pool()
    assert trust_store._load_failed is True


async def test_missing_pool_seeds_pending_barriers(pool):
    """A fresh install starts gated, and the file appears on the first write."""
    assert not pool.exists()
    await trust_store.aload_pool()
    assert trust_store.trust_snapshot().barrier_pending("qq") is True
    assert trust_store.trust_snapshot().loaded is True
    await trust_store.awaive_legacy_barrier("qq")
    assert pool.exists()
    assert json.loads(pool.read_text(encoding="utf-8"))["version"] == (
        trust_store.POOL_VERSION
    )


async def test_load_uses_the_replace_tolerating_reader(pool, monkeypatch):
    """A bare read turns a concurrent Windows os.replace into "no trust".

    Asserts the CALL, not the source text: a docstring mentioning the wrong
    reader would otherwise pass or fail for the wrong reason.
    """
    pool.write_text(json.dumps({"version": 2}), encoding="utf-8")
    seen: list[str] = []

    def _reader(path, **kwargs):
        seen.append(str(path))
        return {"version": 2}

    monkeypatch.setattr(trust_store, "read_json_tolerating_replace", _reader)
    await trust_store.aload_pool()
    assert seen == [str(pool)]


# ── merge / bind / unbind ───────────────────────────────────────────────────

async def test_merge_is_idempotent_commutative_and_associative():
    await _open_gate()
    ids = []
    for account in ("qq:1", "qq:2", "qq:3"):
        ids.append(await trust_store.aensure_account(account))
    forward = await trust_store.amerge_entities(ids[0], ids[1])
    again = await trust_store.amerge_entities(ids[0], ids[1])
    assert again["entity_id"] == forward["entity_id"]
    assert again["persisted"] is True
    reverse = await trust_store.amerge_entities(ids[1], ids[0])
    assert reverse["entity_id"] == forward["entity_id"]
    await trust_store.amerge_entities(forward["entity_id"], ids[2])
    snap = trust_store.trust_snapshot()
    assert snap.same_entity("qq:1", "qq:3")
    assert len(snap.accounts_of("qq:1")) == 3


async def test_merge_moves_ledgers_without_loss():
    await _open_gate()
    left = await trust_store.aensure_account("qq:1")
    right = await trust_store.aensure_account("qq:2")
    await trust_store.aapply_trust_mutations([
        _mutation("qq:1", activity=[("activity_l0000001", 3)], signals=[
            {"speaker_id": "qq:1", "event_id": "l1", "kind": "correction"},
        ]),
        _mutation("qq:2", activity=[("activity_r0000001", 4)], signals=[
            {"speaker_id": "qq:2", "event_id": "r1", "kind": "confirmation"},
        ]),
    ])
    before_left = dict(_account_record("qq:1"))
    before_right = dict(_account_record("qq:2"))
    await trust_store.amerge_entities(left, right)
    assert _account_record("qq:1") == before_left
    assert _account_record("qq:2") == before_right
    snap = trust_store.trust_snapshot()
    assert snap.trust_inputs("qq:1") == (
        pytest.approx(before_left["adjustment"] + before_right["adjustment"]),
        before_left["message_count"] + before_right["message_count"],
    )


async def test_merge_survivor_is_a_function_of_the_final_entity_set():
    """I-C-1: shuffling the merge order must not change the survivor."""
    await _open_gate()
    accounts = [f"qq:{index}" for index in range(6)]
    for account in accounts:
        await trust_store.aensure_account(account)
    baseline = None
    rng = random.Random(1234)
    for attempt in range(12):
        trust_store.reset_for_tests()
        await _open_gate()
        for account in accounts:
            await trust_store.aensure_account(account)
        pairs = [
            (accounts[index], accounts[index + 1])
            for index in range(len(accounts) - 1)
        ]
        rng.shuffle(pairs)
        for left, right in pairs:
            snap = trust_store.trust_snapshot()
            await trust_store.amerge_entities(
                snap.entity_of(left), snap.entity_of(right),
            )
        survivor = trust_store.trust_snapshot().entity_of(accounts[0])
        if baseline is None:
            baseline = survivor
        assert survivor == baseline, f"attempt {attempt} diverged"


async def test_unbind_moves_the_ledger_out_byte_for_byte():
    await _open_gate()
    entity_id = await trust_store.aensure_account("qq:1")
    await trust_store.aensure_account("qq:2")
    await trust_store.abind_account("qq:2", entity_id)
    await trust_store.aapply_trust_mutations([
        _mutation("qq:2", activity=[("activity_x0000001", 3)], signals=[
            {"speaker_id": "qq:2", "event_id": "s1", "kind": "correction"},
        ]),
    ])
    before = dict(_account_record("qq:2"))
    result = await trust_store.aunbind_account("qq:2")
    assert result["changed"] is True
    after = _account_record("qq:2")
    for key in ("adjustment", "message_count", "processed_signal_events",
                "processed_activity_events"):
        assert after[key] == before[key]
    assert trust_store.trust_snapshot().same_entity("qq:1", "qq:2") is False


async def test_unbind_reports_both_deltas_and_they_can_disagree():
    """I-T-7. Making the two numbers agree would be WRONG, not a fix.

    With the entity's raw sum already far past the clamp, removing an account
    moves the ledger but not the effective score at all.
    """
    from config import SPEAKER_TRUST_ADJUSTMENT_LIMIT

    await _open_gate()
    entity_id = await trust_store.aensure_account("qq:1")
    await trust_store.aensure_account("qq:2")
    await trust_store.abind_account("qq:2", entity_id)
    for index in range(30):
        await trust_store.aapply_trust_mutations([
            _mutation("qq:1", signals=[{
                "speaker_id": "qq:1", "event_id": f"a{index}",
                "kind": "correction",
            }]),
        ])
    for index in range(3):
        await trust_store.aapply_trust_mutations([
            _mutation("qq:2", signals=[{
                "speaker_id": "qq:2", "event_id": f"b{index}",
                "kind": "correction",
            }]),
        ])
    assert trust_store.trust_snapshot().trust_inputs("qq:1")[0] < (
        -SPEAKER_TRUST_ADJUSTMENT_LIMIT * 2
    )
    result = await trust_store.aunbind_account("qq:2")
    assert result["ledger_delta"] != pytest.approx(0.0)
    assert result["effective_delta"] == pytest.approx(0.0)
    assert result["ledger_delta"] != pytest.approx(result["effective_delta"])


async def test_bind_rejects_more_than_the_per_platform_account_limit():
    from config import IDENTITY_MAX_ACCOUNTS_PER_ENTITY_PER_PLATFORM

    await _open_gate()
    entity_id = await trust_store.aensure_account("qq:0")
    for index in range(1, IDENTITY_MAX_ACCOUNTS_PER_ENTITY_PER_PLATFORM):
        await trust_store.aensure_account(f"qq:{index}")
        await trust_store.abind_account(f"qq:{index}", entity_id)
    over = f"qq:{IDENTITY_MAX_ACCOUNTS_PER_ENTITY_PER_PLATFORM}"
    await trust_store.aensure_account(over)
    with pytest.raises(trust_store.TrustIdentityError) as excinfo:
        await trust_store.abind_account(over, entity_id)
    assert excinfo.value.status_code == 409
    # A different platform is counted separately.
    await trust_store.aensure_account("bili:1")
    assert (await trust_store.abind_account("bili:1", entity_id))["changed"]


async def test_same_entity_is_false_when_the_pool_is_unloaded():
    await _open_gate()
    entity_id = await trust_store.aensure_account("qq:1")
    await trust_store.aensure_account("qq:2")
    await trust_store.abind_account("qq:2", entity_id)
    assert trust_store.trust_snapshot().same_entity("qq:1", "qq:2") is True
    trust_store._set_load_failed(True)
    try:
        assert trust_store.trust_snapshot().same_entity("qq:1", "qq:2") is False
    finally:
        trust_store._set_load_failed(False)


async def test_merged_chain_is_followed_and_a_cycle_is_refused_not_guessed():
    await _open_gate()
    entity_id = await trust_store.aensure_account("qq:1")
    pool = trust_store._POOL
    pool["entities"]["ent_" + "0" * 24] = {
        "entity_id": "ent_" + "0" * 24, "status": "merged",
        "merged_into": entity_id, "accounts": {},
    }
    assert trust_store._resolve_entity_locked(
        pool, "ent_" + "0" * 24,
    ) == entity_id
    pool["entities"]["ent_" + "1" * 24] = {
        "entity_id": "ent_" + "1" * 24, "status": "merged",
        "merged_into": "ent_" + "1" * 24, "accounts": {},
    }
    assert trust_store._resolve_entity_locked(pool, "ent_" + "1" * 24) is None


# ── normalization of hand-edited pools ──────────────────────────────────────

def test_duplicate_account_across_two_entities_is_merged_not_dropped():
    """facts.json-level hand edits are an acknowledged reality in this repo."""
    cap = activity_count_cap()
    data = {
        "version": 2,
        "entities": {
            "ent_" + "a" * 24: {
                "entity_id": "ent_" + "a" * 24, "status": "active",
                "created_at": "2026-01-01",
                "accounts": {"qq:1": {
                    "account_id": "qq:1", "adjustment": -0.08,
                    "message_count": 5,
                    "processed_signal_events": ["s1"],
                    "processed_activity_events": ["a1"],
                }},
            },
            "ent_" + "b" * 24: {
                "entity_id": "ent_" + "b" * 24, "status": "active",
                "created_at": "2026-02-01",
                "accounts": {"qq:1": {
                    "account_id": "qq:1", "adjustment": -0.04,
                    "message_count": 7,
                    "processed_signal_events": ["s2"],
                    "processed_activity_events": ["a2"],
                }},
            },
        },
    }
    pool = trust_store._normalize_pool(data)
    owners = [
        entity_id for entity_id, record in pool["entities"].items()
        if "qq:1" in (record.get("accounts") or {})
    ]
    assert len(owners) == 1
    kept = pool["entities"][owners[0]]["accounts"]["qq:1"]
    assert kept["adjustment"] == pytest.approx(-0.12)
    assert kept["message_count"] == min(cap, 12)
    assert set(kept["processed_signal_events"]) == {"s1", "s2"}


def test_account_index_on_disk_is_rebuilt_not_trusted():
    data = {
        "version": 2,
        "account_index": {"qq:1": "ent_" + "9" * 24, "qq:ghost": "ent_x"},
        "entities": {
            "ent_" + "a" * 24: {
                "entity_id": "ent_" + "a" * 24, "status": "active",
                "accounts": {"qq:1": {"account_id": "qq:1"}},
            },
        },
    }
    pool = trust_store._normalize_pool(data)
    assert pool["account_index"] == {"qq:1": "ent_" + "a" * 24}


# ── canonical sealing ───────────────────────────────────────────────────────

async def test_canonical_seals_lazily_on_first_write_and_then_holds():
    await _open_gate()
    entity_id = await trust_store.aensure_account("qq:1")
    await trust_store.aensure_account("qq:2")
    await trust_store.abind_account("qq:2", entity_id)
    assert trust_store.trust_snapshot().canonical_account(
        entity_id, "qq",
    ) is None
    await trust_store.aapply_trust_mutations([
        _mutation("qq:2", activity=[("activity_seal00001", 1)]),
    ])
    snap = trust_store.trust_snapshot()
    assert snap.canonical_account(snap.entity_of("qq:1"), "qq") == "qq:2"
    # A later write by the other account must not re-seal.
    await trust_store.aapply_trust_mutations([
        _mutation("qq:1", activity=[("activity_seal00002", 1)]),
    ])
    snap = trust_store.trust_snapshot()
    assert snap.canonical_account(snap.entity_of("qq:1"), "qq") == "qq:2"


async def test_canonical_is_released_only_when_that_account_leaves():
    """I-C-2."""
    await _open_gate()
    entity_id = await trust_store.aensure_account("qq:1")
    await trust_store.aensure_account("qq:2")
    await trust_store.abind_account("qq:2", entity_id)
    await trust_store.aapply_trust_mutations([
        _mutation("qq:1", activity=[("activity_seal00001", 1)]),
    ])
    snap = trust_store.trust_snapshot()
    canonical = snap.canonical_account(snap.entity_of("qq:1"), "qq")
    assert canonical == "qq:1"
    # Unbinding the NON-canonical account leaves it byte-identical.
    await trust_store.aunbind_account("qq:2")
    snap = trust_store.trust_snapshot()
    assert snap.canonical_account(snap.entity_of("qq:1"), "qq") == "qq:1"
    # Unbinding the canonical account releases it.
    await trust_store.aunbind_account("qq:1")
    snap = trust_store.trust_snapshot()
    assert snap.canonical_account(snap.entity_of("qq:1"), "qq") is None


async def test_merge_keeps_the_survivor_canonical_and_files_the_other():
    await _open_gate()
    left = await trust_store.aensure_account("qq:1")
    right = await trust_store.aensure_account("qq:2")
    await trust_store.aseal_canonical("qq:1")
    await trust_store.aseal_canonical("qq:2")
    survivor = (await trust_store.amerge_entities(left, right))["entity_id"]
    snap = trust_store.trust_snapshot()
    survivor_account = "qq:1" if survivor == left else "qq:2"
    assert snap.canonical_account(survivor, "qq") == survivor_account
    superseded = trust_store._POOL["entities"][survivor].get(
        "superseded_canonicals",
    )
    assert superseded and superseded[0]["platform"] == "qq"


async def test_accounts_of_orders_canonical_first_and_never_by_channel():
    await _open_gate()
    entity_id = await trust_store.aensure_account("qq:aaa")
    for account in ("qq:bbb", "qq:ccc"):
        await trust_store.aensure_account(account)
        await trust_store.abind_account(account, entity_id)
    await trust_store.aseal_canonical("qq:ccc")
    order = trust_store.trust_snapshot().accounts_of("qq:aaa")
    assert order[0] == "qq:ccc"
    # Observing a channel must not reorder anything.
    await trust_store.aapply_trust_mutations([
        _mutation("qq:aaa", activity=[("activity_ch000001", 1)],
                  channel="napcat"),
    ])
    assert trust_store.trust_snapshot().accounts_of("qq:aaa")[0] == "qq:ccc"


# ── channel: observation only ───────────────────────────────────────────────

async def test_channel_collision_is_detected_without_touching_the_ledger():
    await _open_gate()
    await trust_store.aapply_trust_mutations([
        _mutation("qq:1", activity=[("activity_n0000001", 1)],
                  channel="napcat"),
    ])
    snap = trust_store.trust_snapshot()
    assert snap.channels_seen("qq:1") == ("napcat",)
    assert snap.channel_collision("qq:1") is False
    before = dict(_account_record("qq:1"))
    result = await trust_store.aapply_trust_mutations([
        _mutation("qq:1", activity=[("activity_o0000001", 1)], channel="open"),
    ])
    snap = trust_store.trust_snapshot()
    assert snap.channels_seen("qq:1") == ("napcat", "open")
    assert snap.channel_collision("qq:1") is True
    assert result.channel_collisions == ("qq:1",)
    # One ledger, one accumulation — the channel changed nothing about it.
    after = _account_record("qq:1")
    assert after["adjustment"] == before["adjustment"]
    assert after["message_count"] == before["message_count"] + 1


async def test_platform_identity_scope_is_never_inferred_by_code(pool):
    """Showing "unknown" is the honest state until a human fills it in."""
    await _open_gate()
    await trust_store.aapply_trust_mutations([
        _mutation("qq:1", activity=[("activity_a0000001", 1)],
                  channel="napcat"),
    ])
    assert trust_store.trust_snapshot().platform_identity_scope("qq") == {}
    stored = json.loads(pool.read_text(encoding="utf-8"))
    assert stored["platform_identity_scope"] == {}


# ── reconcile ───────────────────────────────────────────────────────────────

class _FakeFactStore:
    def __init__(self, rows):
        self._rows = rows

    async def aload_facts(self, _name):
        return self._rows


async def test_reconcile_folds_missing_events_and_is_idempotent():
    await _open_gate()
    rows = [{
        "id": "fact_1",
        "_speaker_trust_signal_events": [
            {"speaker_id": "qq:5", "event_id": "lost1", "kind": "correction"},
        ],
    }]
    store = _FakeFactStore(rows)
    first = await trust_store.areconcile_from_facts(store, ["Neko"])
    assert first["applied"] == 1
    second = await trust_store.areconcile_from_facts(store, ["Neko"])
    assert second["applied"] == 0


async def test_reconcile_respects_the_pending_barrier():
    """Otherwise reconcile folds an event the pending import also contains."""
    rows = [{
        "id": "fact_1",
        "_speaker_trust_signal_events": [
            {"speaker_id": "qq:5", "event_id": "e1", "kind": "correction"},
        ],
    }]
    result = await trust_store.areconcile_from_facts(
        _FakeFactStore(rows), ["Neko"],
    )
    assert result["applied"] == 0
    assert result["gated"] == 1


# ── cancellation safety ─────────────────────────────────────────────────────

async def test_cancelling_the_awaiter_still_lands_the_write_and_frees_the_lock():
    """``asyncio.to_thread`` cannot be cancelled once handed off."""
    import asyncio
    import threading

    await _open_gate()
    entered = threading.Event()
    release = threading.Event()
    real_write = trust_store.atomic_write_json

    def _slow_write(*args, **kwargs):
        entered.set()
        release.wait(5.0)
        return real_write(*args, **kwargs)

    with patch.object(trust_store, "atomic_write_json", _slow_write):
        task = asyncio.ensure_future(trust_store.aapply_trust_mutations([
            _mutation("qq:1", activity=[("activity_cancel001", 3)]),
        ]))
        # Cancel only once the worker thread is provably inside the write.
        while not entered.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The thread was handed off, so it finishes regardless.
        for _ in range(500):
            if trust_store.trust_snapshot().trust_inputs("qq:1")[1]:
                break
            await asyncio.sleep(0.01)
    assert trust_store.trust_snapshot().trust_inputs("qq:1")[1] == 3
    assert trust_store._pool_lock.acquire(blocking=False) is True
    trust_store._pool_lock.release()


# ── migrated from test_speaker_trust.py's PermissionManager ledger block ───
# The arithmetic and ledger discipline moved to the server; these cases moved
# with it rather than being deleted.

async def test_message_volume_alone_cannot_cross_the_arbitration_margin():
    await _open_gate()
    await trust_store.aapply_trust_mutations([
        _mutation("qq:1001", activity=[("activity_bulk0001", 1_000_000)]),
    ])
    snap = trust_store.trust_snapshot()
    assert snap.resolve_trust("qq:1001", tier="normal") == pytest.approx(0.52)
    assert snap.resolve_trust("qq:2002", tier="trusted") == pytest.approx(0.8)
    from memory.speaker_trust import preferred_by_trust
    assert preferred_by_trust(
        snap.resolve_trust("qq:2002", tier="trusted"),
        snap.resolve_trust("qq:1001", tier="normal"),
    ) == "old"


def test_non_finite_and_overflowing_ledger_values_normalize_to_zero():
    from memory.identity import normalize_account_record

    for bad in (float("nan"), float("inf"), 10 ** 400):
        assert normalize_account_record(
            "qq:1", {"adjustment": bad},
        )["adjustment"] == 0.0
    assert normalize_account_record(
        "qq:1", {"message_count": 10 ** 400},
    )["message_count"] == activity_count_cap()


def test_malformed_rings_are_dropped_without_splitting_strings():
    """A bare string ledger must become ``[]``, never a list of characters."""
    from memory.identity import normalize_account_record

    record = normalize_account_record("qq:1", {
        "processed_activity_events": "activity-id",
        "processed_signal_events": 5,
        "message_count": float("inf"),
    })
    assert record["processed_activity_events"] == []
    assert record["processed_signal_events"] == []
    assert record["message_count"] == 0


def test_signal_ledger_normalizes_in_linear_time():
    """A quadratic dedup here would stall startup on a long-lived ledger."""
    import time

    from memory.identity import normalize_account_record

    def _elapsed(size: int) -> float:
        ids = [f"owner-signal-{index}" for index in range(size)]
        started = time.perf_counter()
        normalize_account_record("qq:1", {"processed_signal_events": ids})
        return time.perf_counter() - started

    # Scale RATIO, not wall clock: an absolute threshold red-lines whenever a
    # shared runner is busy, which is luck rather than a regression. Linear
    # gives ~10x for 10x input, quadratic ~100x — machine speed cancels out.
    small = max(_elapsed(3_000), 1e-6)
    large = _elapsed(30_000)
    assert large / small < 30, f"superlinear: {large / small:.1f}x for 10x input"

    event_ids = [f"owner-signal-{index}" for index in range(30_000)]
    event_ids += ["x" * 96 + "a", "x" * 96 + "b"]
    record = normalize_account_record(
        "qq:1", {"processed_signal_events": event_ids},
    )
    # The two 97-char ids both truncate to the same 96-char prefix and dedup.
    assert len(record["processed_signal_events"]) == 30_001
    assert record["processed_signal_events"][-1] == "x" * 96


async def test_one_ledger_is_shared_across_groups_characters_and_dm():
    """The cross-scope / cross-character channel is a product decision.

    The pool path carries no ``lanlan_name`` and no conversation, so the same
    account resolves identically no matter which route asked.
    """
    await _open_gate()
    await trust_store.aapply_trust_mutations([
        _mutation("qq:1001", signals=[{
            "speaker_id": "qq:1001", "event_id": "confirmed-in-group-a",
            "kind": "confirmation",
        }]),
    ])
    snap = trust_store.trust_snapshot()
    resolved = {snap.resolve_trust("qq:1001", tier="normal") for _ in range(3)}
    assert len(resolved) == 1
    assert "lanlan" not in trust_store.pool_path().lower()
    assert list(trust_store._POOL["entities"].values())[0]["accounts"].keys() == {
        "qq:1001",
    }


def test_the_pool_lock_is_a_threading_lock_not_an_asyncio_lock():
    """A module-level asyncio.Lock binds to the first loop that contends it.

    pytest here is asyncio_mode=auto with a function-scoped loop, so the second
    contended test would RuntimeError and leave the lock stuck held.
    """
    import threading

    assert isinstance(trust_store._pool_lock, type(threading.Lock()))


# ── review round 2 ─────────────────────────────────────────────────────────

class _ArchiveFactStore(_FakeFactStore):
    def __init__(self, rows, archived):
        super().__init__(rows)
        self._archived = archived

    async def aload_archived_speaker_trust_signal_facts(self, _name):
        return self._archived


async def test_reconcile_also_restores_signals_from_archived_rows():
    """The DR tool must not skip the OLDEST adjustments.

    ``apersist_speaker_trust_events`` updates archived rows too, so a signal
    that has aged out of ``facts.json`` still exists — and those are exactly
    the ones least likely to be re-earned by an owner repeating themselves.
    """
    await _open_gate()
    store = _ArchiveFactStore(
        rows=[{
            "id": "fact_active",
            "_speaker_trust_signal_events": [
                {"speaker_id": "qq:5", "event_id": "live", "kind": "correction"},
            ],
        }],
        archived=[{
            "id": "fact_archived",
            "_speaker_trust_signal_events": [
                {"speaker_id": "qq:5", "event_id": "aged",
                 "kind": "correction"},
            ],
        }],
    )
    result = await trust_store.areconcile_from_facts(store, ["Neko"])
    assert result["applied"] == 2
    ring = _account_record("qq:5")["processed_signal_events"]
    assert set(ring) == {"live", "aged"}


async def test_reconcile_survives_a_store_without_the_archive_loader():
    """Archive access is best-effort: a store that lacks it still reconciles."""
    await _open_gate()
    result = await trust_store.areconcile_from_facts(
        _FakeFactStore([{
            "id": "f",
            "_speaker_trust_signal_events": [
                {"speaker_id": "qq:6", "event_id": "e", "kind": "correction"},
            ],
        }]),
        ["Neko"],
    )
    assert result["applied"] == 1


async def test_malformed_channel_diagnostics_survive_a_load_and_a_write(pool):
    """Hand-edited diagnostics must not reach a mutation as a non-dict.

    ``channel_observations`` nests one level deeper than the other containers,
    so a shallow copy would leave a per-channel value un-normalized. The
    copy-on-write draft coerces it today, but the published pool and the draft
    then disagree about what is in there.
    """
    pool.write_text(json.dumps({
        "version": 2,
        "legacy_barriers": {"qq": {"status": "cleared"}},
        "channel_observations": {
            "qq": {"napcat": "not-a-dict"},
            "bili": "also-not-a-dict",
        },
        "entities": {"ent_" + "a" * 24: {
            "entity_id": "ent_" + "a" * 24, "status": "active",
            "accounts": {"qq:1": {
                "account_id": "qq:1",
                "channels_seen": {"napcat": "string-too"},
            }},
        }},
    }), encoding="utf-8")
    await trust_store.aload_pool()
    assert trust_store._POOL["channel_observations"] == {}
    assert _account_record("qq:1")["channels_seen"] == {}
    result = await trust_store.aapply_trust_mutations([
        _mutation("qq:1", activity=[("activity_aaaaaaaa", 1)],
                  channel="napcat"),
    ])
    assert result.persisted is True
    observed = trust_store._POOL["channel_observations"]["qq"]["napcat"]
    assert isinstance(observed, dict) and observed["accounts"] == 1
