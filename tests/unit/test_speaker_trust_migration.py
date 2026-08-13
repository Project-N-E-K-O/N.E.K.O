"""Legacy trust-ledger migration: the barrier, additive merge, and self-healing."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from memory import trust_store
from memory.identity import activity_count_cap

SOURCE = "qq_auto_reply.business_config.speaker_trust_profiles.v1"


@pytest.fixture(autouse=True)
def pool(tmp_path, monkeypatch):
    path = tmp_path / "speaker_trust.json"
    monkeypatch.setattr(trust_store, "pool_path", lambda: str(path))
    trust_store.reset_for_tests()
    yield path
    trust_store.reset_for_tests()


def _record(account_id: str) -> dict:
    snap = trust_store.trust_snapshot()
    entity_id = snap.entity_of(account_id)
    return trust_store._POOL["entities"][entity_id]["accounts"][account_id]


async def _import(profiles: dict, *, final: bool = True, source: str = SOURCE):
    return await trust_store.aimport_legacy_profiles(
        platform="qq", source=source, profiles=profiles, final=final,
    )


# ── identity migration: the bare key IS the actor ───────────────────────────

async def test_migration_key_is_byte_identical_to_the_runtime_account_id():
    """``f"qq:{bare}"`` here and ``f"qq:{sender_id}"`` at runtime are one string.

    That identity is what removes the need for any "adoption" step: there is no
    seam between the legacy ledger and live traffic for one to paper over.
    """
    await _import({"123456": {"adjustment": -0.08, "message_count": 3}})
    assert trust_store.trust_snapshot().entity_of("qq:123456") is not None
    assert _record("qq:123456")["adjustment"] == pytest.approx(-0.08)


async def test_openid_shaped_keys_migrate_unchanged():
    """A deployment that ran open_platform has openid keys, not bare QQ numbers.

    The migration deliberately does NOT decide which transport a key came from
    — that provenance genuinely does not exist in the legacy profile.
    """
    await _import({"ABCD1234EFGH": {"adjustment": 0.04}})
    assert trust_store.trust_snapshot().entity_of("qq:ABCD1234EFGH") is not None


# ── idempotency by (source, account_id) ─────────────────────────────────────

async def test_second_import_of_the_same_source_is_a_no_op(pool):
    await _import({"1": {"adjustment": -0.08, "message_count": 4}})
    before = dict(_record("qq:1"))
    mtime = pool.stat().st_mtime_ns
    result = await _import({"1": {"adjustment": -0.08, "message_count": 4}})
    assert result["imported"] == []
    assert _record("qq:1") == before
    assert pool.stat().st_mtime_ns == mtime


async def test_server_side_evolution_survives_a_re_push():
    """Re-pushing the frozen snapshot must never clobber later evolution."""
    await _import({"1": {"adjustment": -0.08, "message_count": 2}})
    await trust_store.aapply_trust_mutations([
        trust_store.TrustMutation(
            speaker_account_id="qq:1",
            activity_events=(
                trust_store.ActivityEvent(id="activity_after0001", count=5),
            ),
            signal_events=(
                {"speaker_id": "qq:1", "event_id": "after",
                 "kind": "confirmation"},
            ),
        ),
    ])
    evolved = dict(_record("qq:1"))
    await _import({"1": {"adjustment": -0.08, "message_count": 2}})
    assert _record("qq:1") == evolved


# ── the barrier is the only thing that makes additive merge safe ────────────

async def test_traffic_during_the_pending_window_produces_no_evolution():
    """R1: the window's cost is bounded ABSTENTION, never a wrong value."""
    assert trust_store.trust_snapshot().barrier_pending("qq") is True
    await trust_store.aapply_trust_mutations([
        trust_store.TrustMutation(
            speaker_account_id="qq:1",
            activity_events=(
                trust_store.ActivityEvent(id="activity_window001", count=9),
            ),
            signal_events=(
                {"speaker_id": "qq:1", "event_id": "replayed",
                 "kind": "correction"},
            ),
        ),
    ])
    assert trust_store.trust_snapshot().trust_inputs("qq:1") == (0.0, 0)


async def test_import_window_double_count_is_impossible():
    """The guard test for the hardest migration bug.

    Setup: the legacy ledger ALREADY contains event E. During the pending
    window the owner repeats the same sentence, which makes the replay ring
    re-deliver E. Without the barrier the server would apply E against an empty
    ledger, then the import would add the legacy adjustment that already
    includes E — a single correction counted twice is 0.16, which crosses the
    0.15 arbitration margin, and the ring stores only ids (no kind), so it
    cannot be reconstructed afterwards.
    """
    from config import SPEAKER_TRUST_CORRECTION_DELTA

    replayed = {"speaker_id": "qq:9", "event_id": "E", "kind": "correction"}
    # Pending window: the replayed event is deferred, not applied.
    result = await trust_store.aapply_trust_mutations([
        trust_store.TrustMutation(
            speaker_account_id="qq:owner", signal_events=(replayed,),
        ),
    ])
    assert result.signals_deferred == 1
    # The legacy ledger already accounts for E exactly once.
    await _import({"9": {
        "adjustment": -SPEAKER_TRUST_CORRECTION_DELTA,
        "processed_signal_events": ["E"],
    }})
    assert _record("qq:9")["adjustment"] == pytest.approx(
        -SPEAKER_TRUST_CORRECTION_DELTA
    )
    # After the gate opens, re-delivering E stays idempotent.
    await trust_store.aapply_trust_mutations([
        trust_store.TrustMutation(
            speaker_account_id="qq:owner", signal_events=(replayed,),
        ),
    ])
    assert _record("qq:9")["adjustment"] == pytest.approx(
        -SPEAKER_TRUST_CORRECTION_DELTA
    )


# ── chunking and tolerance ──────────────────────────────────────────────────

async def test_only_the_final_chunk_opens_the_gate_and_partials_are_lossless():
    for index in range(3):
        await _import(
            {f"{index}": {"adjustment": -0.01 * (index + 1)}},
            final=(index == 2),
        )
        expected_pending = index != 2
        assert trust_store.trust_snapshot().barrier_pending("qq") is (
            expected_pending
        )
    for index in range(3):
        assert _record(f"qq:{index}")["adjustment"] == pytest.approx(
            -0.01 * (index + 1)
        )


async def test_one_dirty_profile_never_rejects_the_whole_chunk():
    """A 422 would wedge the migration permanently — retry never succeeds."""
    result = await _import({
        "1": {"adjustment": -0.08},
        "": {"adjustment": -0.08},
        "  ": "not even a dict",
        "with space": {"adjustment": -0.08},
    })
    assert "qq:1" in result["imported"]
    assert {entry["reason"] for entry in result["skipped"]} == {
        "invalid_account_id",
    }
    assert len(result["skipped"]) == 3


async def test_import_merges_additively_and_never_clamps_adjustment():
    from config import SPEAKER_TRUST_ADJUSTMENT_LIMIT

    await _import(
        {"1": {"adjustment": -0.8, "message_count": 5}}, final=False,
    )
    await _import(
        {"1": {"adjustment": -0.8}}, final=True, source="another.source.v1",
    )
    # Raw sum is kept lossless (clamping each write is non-commutative)...
    assert _record("qq:1")["adjustment"] == pytest.approx(-1.6)
    # ...and the clamp only shows up at read time.
    assert trust_store.trust_snapshot().resolve_trust(
        "qq:1", tier="normal",
    ) == pytest.approx(0.5 - SPEAKER_TRUST_ADJUSTMENT_LIMIT + 0.005)


async def test_import_keeps_the_signal_ring_whole_and_caps_only_activity():
    from config import SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT

    await _import({"1": {
        "processed_signal_events": [f"s{index}" for index in range(3000)],
        "processed_activity_events": [f"a{index}" for index in range(3000)],
    }})
    record = _record("qq:1")
    assert record["processed_signal_events"] == [
        f"s{index}" for index in range(3000)
    ]
    assert len(record["processed_activity_events"]) == (
        SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT
    )


async def test_message_count_is_capped_on_merge():
    cap = activity_count_cap()
    await _import({"1": {"message_count": cap * 3}})
    assert _record("qq:1")["message_count"] == cap


# ── self-healing: no cross-file double marker ───────────────────────────────

async def test_a_lost_pool_recovers_to_the_migration_time_state(pool):
    """The guard test for "pool lost = permanent silent deadlock".

    The original design put "migration done" in the plugin config and "already
    imported" in the pool, with no atomic relationship. Losing the pool once
    made the plugin skip the push forever while the new pool's barrier stayed
    pending — every user's trust silently zero, unrecoverable.
    """
    frozen = {"1": {"adjustment": -0.08, "message_count": 4}}
    await _import(frozen)
    assert trust_store.trust_snapshot().barrier_pending("qq") is False
    # Pool file is lost.
    pool.unlink()
    trust_store.reset_for_tests()
    await trust_store.aload_pool()
    assert trust_store.trust_snapshot().barrier_pending("qq") is True
    assert trust_store.trust_snapshot().trust_inputs("qq:1") == (0.0, 0)
    # Next startup re-pushes the same frozen snapshot, unprompted.
    await _import(frozen)
    assert trust_store.trust_snapshot().barrier_pending("qq") is False
    assert _record("qq:1")["adjustment"] == pytest.approx(-0.08)


async def test_import_that_never_finalized_recovers_on_the_next_startup():
    await _import({"1": {"adjustment": -0.08}}, final=False)
    assert trust_store.trust_snapshot().barrier_pending("qq") is True
    await _import({"1": {"adjustment": -0.08}}, final=True)
    assert trust_store.trust_snapshot().barrier_pending("qq") is False
    # Zero loss: the sentinel skipped the re-send, the value did not double.
    assert _record("qq:1")["adjustment"] == pytest.approx(-0.08)


async def test_waive_is_an_escape_hatch_not_an_import():
    await trust_store.awaive_legacy_barrier("qq")
    assert trust_store.trust_snapshot().barrier_pending("qq") is False
    assert trust_store._POOL["legacy_barriers"]["qq"]["waived"] is True


# ── plugin side: pusher runs forever, config_store passes the key through ──

async def test_pusher_sends_one_empty_chunk_on_a_fresh_install():
    """A fresh install has nothing to import but still must open its gate."""
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    bridge = SimpleNamespace(
        post_legacy_speaker_trust=AsyncMock(return_value={"persisted": True}),
    )
    plugin = SimpleNamespace(
        _qq_settings={},
        memory_bridge=bridge,
        logger=SimpleNamespace(
            warning=lambda *_: None, info=lambda *_: None,
            debug=lambda *_: None, error=lambda *_: None,
        ),
        trust_ready=__import__("asyncio").Event(),
    )
    service = QQSettingsService.__new__(QQSettingsService)
    service.plugin = plugin
    await service.push_legacy_speaker_trust_forever()
    assert bridge.post_legacy_speaker_trust.await_count == 1
    kwargs = bridge.post_legacy_speaker_trust.await_args.kwargs
    assert kwargs["profiles"] == {}
    assert kwargs["final"] is True
    assert plugin.trust_ready.is_set()


async def test_pusher_retries_and_leaves_trust_reporting_off_until_it_lands():
    import asyncio

    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    attempts = {"n": 0}

    async def _flaky(**_kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("memory_server not up")
        return {"persisted": True}

    bridge = SimpleNamespace(post_legacy_speaker_trust=_flaky)
    plugin = SimpleNamespace(
        _qq_settings={"speaker_trust_profiles": {"1": {"adjustment": -0.08}}},
        memory_bridge=bridge,
        logger=SimpleNamespace(
            warning=lambda *_: None, info=lambda *_: None,
            debug=lambda *_: None, error=lambda *_: None,
        ),
        trust_ready=asyncio.Event(),
    )
    service = QQSettingsService.__new__(QQSettingsService)
    service.plugin = plugin
    with patch("asyncio.sleep", new=AsyncMock()):
        await service.push_legacy_speaker_trust_forever()
    assert attempts["n"] == 3
    assert plugin.trust_ready.is_set()


async def test_pusher_treats_persisted_false_as_a_retry():
    import asyncio

    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    results = [{"persisted": False}, {"persisted": True}]

    async def _responder(**_kwargs):
        return results.pop(0)

    plugin = SimpleNamespace(
        _qq_settings={},
        memory_bridge=SimpleNamespace(post_legacy_speaker_trust=_responder),
        logger=SimpleNamespace(
            warning=lambda *_: None, info=lambda *_: None,
            debug=lambda *_: None, error=lambda *_: None,
        ),
        trust_ready=asyncio.Event(),
    )
    service = QQSettingsService.__new__(QQSettingsService)
    service.plugin = plugin
    with patch("asyncio.sleep", new=AsyncMock()):
        await service.push_legacy_speaker_trust_forever()
    assert results == []
    assert plugin.trust_ready.is_set()


async def test_config_store_round_trip_never_rewrites_the_legacy_pool(tmp_path):
    """The disk key is the migration source; normalizing it rewrites history."""
    from plugin.plugins.qq_auto_reply.config_store import (
        QQAutoReplyConfigStore,
    )

    hostile = {
        "  spaced  ": {"adjustment": -0.08, "extra_unknown_field": 1},
        "123": {"adjustment": -0.04, "message_count": 999},
    }
    store = QQAutoReplyConfigStore(tmp_path)
    store._path.parent.mkdir(parents=True, exist_ok=True)
    store._path.write_text(
        json.dumps({"speaker_trust_profiles": hostile}), encoding="utf-8",
    )
    loaded = await store.load()
    assert loaded["speaker_trust_profiles"] == hostile
    await store.save(loaded)
    written = json.loads(store._path.read_text(encoding="utf-8"))
    assert written["speaker_trust_profiles"] == hostile
