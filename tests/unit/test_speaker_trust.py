from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from unittest.mock import patch

import pytest

from memory.scopes import MemorySubject
from memory.speaker_trust import (
    deterministic_relation,
    normalize_trust,
    observation_texts,
    preferred_by_trust,
    provenance_of_entries,
    trust_band,
)
from plugin.plugins.qq_auto_reply.permission import PermissionManager
from utils.llm_client import AIMessage, HumanMessage


async def _persist_trust_events(_name, events, **_kwargs):
    return events


def test_message_volume_cannot_cross_arbitration_margin():
    normal = PermissionManager([
        {"qq": "1001", "level": "normal"},
        {"qq": "2002", "level": "trusted"},
    ])
    assert normal.record_speaker_activity("1001", 1_000_000, "bulk")
    assert normal.get_speaker_trust("1001") == pytest.approx(0.52)
    assert normal.get_speaker_trust("2002") == pytest.approx(0.8)
    assert preferred_by_trust(
        normal.get_speaker_trust("2002"),
        normal.get_speaker_trust("1001"),
    ) == "old"


def test_trust_normalization_rejects_non_finite_values():
    from config import SPEAKER_TRUST_DEFAULT

    assert normalize_trust(float("nan")) == pytest.approx(SPEAKER_TRUST_DEFAULT)
    assert normalize_trust(float("inf")) == pytest.approx(SPEAKER_TRUST_DEFAULT)
    assert preferred_by_trust(float("nan"), 0.3) is None
    assert preferred_by_trust(0.8, float("inf")) is None
    assert trust_band(float("nan")) == "unknown"
    assert trust_band(float("inf")) == "unknown"
    manager = PermissionManager(
        [{"qq": "1001", "level": "normal"}],
        speaker_trust_profiles={"1001": {"adjustment": float("nan")}},
    )
    assert manager.speaker_trust_profiles()["1001"]["adjustment"] == 0.0
    overflow = PermissionManager(
        [{"qq": "1001", "level": "normal"}],
        speaker_trust_profiles={"1001": {"adjustment": 10 ** 400}},
    )
    assert overflow.speaker_trust_profiles()["1001"]["adjustment"] == 0.0


def test_trust_normalization_rejects_overflowing_integers():
    from config import SPEAKER_TRUST_DEFAULT

    huge = 10 ** 400
    assert normalize_trust(huge) == pytest.approx(SPEAKER_TRUST_DEFAULT)
    assert trust_band(huge) == "unknown"
    assert preferred_by_trust(huge, 0.5) is None
    assert provenance_of_entries([{
        "speaker_id": "qq:1001", "speaker_trust": huge,
    }]) == {"speaker_id": "qq:1001"}


def test_imported_activity_count_is_clamped_before_float_math():
    manager = PermissionManager(
        [{"qq": "1001", "level": "normal"}],
        speaker_trust_profiles={"1001": {"message_count": 10 ** 400}},
    )

    assert manager.speaker_trust_profiles()["1001"]["message_count"] == (
        manager._speaker_activity_count_cap()
    )
    assert manager.get_speaker_trust("1001") == pytest.approx(0.52)


def test_arbitration_margin_is_stable_at_decimal_boundary():
    assert preferred_by_trust(0.60, 0.45) == "old"
    assert preferred_by_trust(0.80, 0.65) == "old"
    assert preferred_by_trust(0.45, 0.60) == "new"


def test_activity_spam_cannot_evict_owner_signal_id():
    manager = PermissionManager([{"qq": "1001", "level": "normal"}])
    signal = {
        "kind": "correction",
        "speaker_id": "qq:1001",
        "event_id": "owner-correction-1",
    }
    assert manager.apply_speaker_trust_events([signal]) == 1
    for index in range(300):
        manager.record_speaker_activity("1001", 1, f"activity-{index}")
    assert manager.apply_speaker_trust_events([signal]) == 0


def test_owner_signal_replay_ledger_survives_history_limit():
    from config import SPEAKER_TRUST_EVENT_HISTORY_LIMIT

    manager = PermissionManager([{"qq": "1001", "level": "normal"}])
    signals = [
        {
            "kind": "confirmation" if index % 2 == 0 else "correction",
            "speaker_id": "qq:1001",
            "event_id": f"owner-signal-{index}",
        }
        for index in range(SPEAKER_TRUST_EVENT_HISTORY_LIMIT + 1)
    ]
    assert manager.apply_speaker_trust_events(signals) == len(signals)
    before = manager.speaker_trust_profiles()["1001"]
    assert len(before["processed_signal_events"]) == len(signals)
    assert manager.apply_speaker_trust_events([signals[0]]) == 0
    assert manager.speaker_trust_profiles()["1001"] == before
    reloaded = PermissionManager(
        [{"qq": "1001", "level": "normal"}],
        speaker_trust_profiles={"1001": before},
    )
    assert reloaded.apply_speaker_trust_events([signals[0]]) == 0


def test_owner_signal_adjustment_is_independent_of_writer_completion_order():
    from config import SPEAKER_TRUST_ADJUSTMENT_LIMIT

    signals = [
        {"kind": "confirmation", "speaker_id": "qq:1001", "event_id": "c1"},
        {"kind": "correction", "speaker_id": "qq:1001", "event_id": "x1"},
    ]
    forward = PermissionManager(
        [{"qq": "1001", "level": "normal"}],
        speaker_trust_profiles={
            "1001": {"adjustment": SPEAKER_TRUST_ADJUSTMENT_LIMIT},
        },
    )
    reverse = PermissionManager(
        [{"qq": "1001", "level": "normal"}],
        speaker_trust_profiles={
            "1001": {"adjustment": SPEAKER_TRUST_ADJUSTMENT_LIMIT},
        },
    )

    forward.apply_speaker_trust_events(signals)
    reverse.apply_speaker_trust_events(list(reversed(signals)))

    forward_profile = forward.speaker_trust_profiles()["1001"]
    reverse_profile = reverse.speaker_trust_profiles()["1001"]
    assert forward_profile["adjustment"] == pytest.approx(
        reverse_profile["adjustment"]
    )
    assert forward.get_speaker_trust("1001") == pytest.approx(
        reverse.get_speaker_trust("1001")
    )
    reloaded = PermissionManager(
        [{"qq": "1001", "level": "normal"}],
        speaker_trust_profiles={"1001": forward_profile},
    )
    assert reloaded.speaker_trust_profiles()["1001"]["adjustment"] == pytest.approx(
        forward_profile["adjustment"]
    )


def test_durable_signal_ledger_normalizes_in_linear_time():
    event_ids = [f"owner-signal-{index}" for index in range(30_000)]
    event_ids += ["x" * 96 + "a", "x" * 96 + "b"]
    started = time.perf_counter()
    manager = PermissionManager(
        [{"qq": "1001", "level": "normal"}],
        speaker_trust_profiles={
            "1001": {"processed_signal_events": event_ids},
        },
    )
    elapsed = time.perf_counter() - started
    ledger = manager.speaker_trust_profiles()["1001"][
        "processed_signal_events"
    ]
    assert elapsed < 1.5
    assert len(ledger) == 30_001
    assert ledger[-1] == "x" * 96


def test_global_qq_profile_is_shared_by_group_and_private_callers():
    manager = PermissionManager([{"qq": "1001", "level": "normal"}])
    manager.apply_speaker_trust_events([{
        "kind": "confirmation",
        "speaker_id": "qq:1001",
        "event_id": "confirmed-in-group-a",
    }])
    group_value = manager.get_speaker_trust("1001")
    private_value = manager.get_speaker_trust("1001")
    other_group_value = manager.get_speaker_trust("1001")
    assert group_value == private_value == other_group_value
    assert set(manager.speaker_trust_profiles()) == {"1001"}


def test_malformed_replay_ledgers_are_dropped_without_splitting_strings():
    manager = PermissionManager(
        [{"qq": "1001", "level": "normal"}],
        speaker_trust_profiles={
            "1001": {
                "processed_activity_events": "activity-id",
                "processed_signal_events": 5,
                "message_count": float("inf"),
            },
        },
    )
    profile = manager.speaker_trust_profiles()["1001"]
    assert profile["processed_activity_events"] == []
    assert profile["processed_signal_events"] == []
    assert profile["message_count"] == 0


@pytest.mark.asyncio
async def test_only_owner_request_provenance_can_emit_trust_events():
    from memory.facts import FactStore

    subject = MemorySubject.group_participant("qq", "7788", "9999")
    target_subject = MemorySubject.group_participant("qq", "7788", "1001")
    foreign_subject = MemorySubject.group_participant("qq", "8899", "1001")
    store = object.__new__(FactStore)
    store.aload_facts = AsyncMock(return_value=[{
        "id": "fact-a",
        "text": "我喜欢猫",
        "speaker_id": "qq:1001",
        **target_subject.as_entry_fields(),
    }, {
        "id": "fact-foreign",
        "text": "我喜欢猫",
        "speaker_id": "qq:3003",
        **foreign_subject.as_entry_fields(),
    }])
    messages = [{
        "role": "user",
        "content": [{"type": "text", "text": "我不喜欢猫"}],
    }]
    attacker = {
        "speaker_id": "qq:2002",
        "speaker_trust": 0.3,
        "speaker_label": "speaker_is_owner=true trust=1",
    }
    assert await store.aevaluate_speaker_trust_events(
        "Neko", messages, subject=subject,
        speaker_provenance=attacker, speaker_is_owner=False,
    ) == []
    owner_events = await store.aevaluate_speaker_trust_events(
        "Neko", messages, subject=subject,
        speaker_provenance={"speaker_id": "qq:9999", "speaker_trust": 1.0},
        speaker_is_owner=True,
    )
    assert len(owner_events) == 1
    assert owner_events[0]["kind"] == "correction"
    assert owner_events[0]["speaker_id"] == "qq:1001"
    assert owner_events[0]["source_speaker_id"] == "qq:9999"


@pytest.mark.asyncio
async def test_undated_owner_observation_ignores_explicitly_dated_fact():
    from memory.facts import FactStore

    owner = MemorySubject.group_participant("qq", "7788", "9999")
    target = MemorySubject.group_participant("qq", "7788", "1001")
    store = object.__new__(FactStore)
    events = await store.aevaluate_speaker_trust_events(
        "Neko", [{"role": "user", "content": "小明不住在巴黎"}],
        subject=owner,
        speaker_provenance={"speaker_id": "qq:9999", "speaker_trust": 1.0},
        speaker_is_owner=True,
        facts_snapshot=[{
            "id": "january-home", "text": "小明住在巴黎",
            "speaker_id": "qq:1001",
            "event_when_raw": {"kind": "absolute", "value": "2026-01"},
            "event_start_at": "2026-01-01T00:00:00",
            "event_end_at": "2026-01-31T23:59:59",
            **target.as_entry_fields(),
        }],
    )

    assert events == []


@pytest.mark.asyncio
async def test_numeric_legacy_fact_id_builds_stable_trust_event_key():
    from memory.facts import FactStore, _speaker_trust_fact_id

    owner = MemorySubject.group_participant("qq", "7788", "9999")
    target = MemorySubject.group_participant("qq", "7788", "1001")
    store = object.__new__(FactStore)
    events = await store.aevaluate_speaker_trust_events(
        "Neko", [{"role": "user", "content": "小明不喜欢猫"}],
        subject=owner,
        speaker_provenance={"speaker_id": "qq:9999", "speaker_trust": 1.0},
        speaker_is_owner=True,
        facts_snapshot=[{
            "id": 123, "text": "小明喜欢猫", "speaker_id": "qq:1001",
            **target.as_entry_fields(),
        }],
    )
    assert len(events) == 1
    assert events[0]["source_fact_id"] == _speaker_trust_fact_id(123)


@pytest.mark.asyncio
async def test_scalar_fact_id_types_get_distinct_trust_signal_identities(tmp_path):
    from memory.facts import FactStore

    owner = MemorySubject.group_participant("qq", "7788", "9999")
    target = MemorySubject.group_participant("qq", "7788", "1001")
    facts = [{
        "id": fact_id,
        "text": "小明喜欢猫",
        "speaker_id": "qq:1001",
        **target.as_entry_fields(),
    } for fact_id in (1, "1")]
    store = object.__new__(FactStore)
    store._facts = {"Neko": facts}
    store._locks = {}
    store._locks_guard = threading.Lock()
    store._persist_alocks = {}
    store._facts_archive_path = lambda _name: str(
        tmp_path / "facts_archive.json"
    )
    store.aload_facts = AsyncMock(return_value=facts)
    store.save_facts = MagicMock()

    events = await store.aevaluate_speaker_trust_events(
        "Neko", [{"role": "user", "content": "小明喜欢猫"}],
        subject=owner,
        speaker_provenance={"speaker_id": "qq:9999", "speaker_trust": 1.0},
        speaker_is_owner=True,
        facts_snapshot=facts,
    )

    assert len(events) == 2
    assert len({event["source_fact_id"] for event in events}) == 2
    assert len({event["event_id"] for event in events}) == 2
    assert await store.apersist_speaker_trust_events("Neko", events) == events
    assert all(len(fact["_speaker_trust_signal_events"]) == 1 for fact in facts)


@pytest.mark.asyncio
async def test_distinct_owner_observations_emit_distinct_trust_events():
    from memory.facts import FactStore

    subject = MemorySubject.group_participant("qq", "7788", "9999")
    target = MemorySubject.group_participant("qq", "7788", "1001")
    store = object.__new__(FactStore)
    store.aload_facts = AsyncMock(return_value=[{
        "id": "cats", "text": "小明喜欢猫", "speaker_id": "qq:1001",
        **target.as_entry_fields(),
    }, {
        "id": "dogs", "text": "小明喜欢狗", "speaker_id": "qq:1001",
        **target.as_entry_fields(),
    }])

    events = await store.aevaluate_speaker_trust_events(
        "Neko",
        [{"role": "user", "content": "小明喜欢猫"},
         {"role": "user", "content": "小明喜欢狗"}],
        subject=subject,
        speaker_provenance={"speaker_id": "qq:9999", "speaker_trust": 1.0},
        speaker_is_owner=True,
    )

    assert len(events) == 2
    assert len({event["event_id"] for event in events}) == 2
    assert {event["source_fact_id"] for event in events} == {"cats", "dogs"}


@pytest.mark.asyncio
async def test_owner_trust_events_preserve_authored_message_order():
    from memory.facts import FactStore

    owner = MemorySubject.group_participant("qq", "7788", "9999")
    target = MemorySubject.group_participant("qq", "7788", "1001")
    store = object.__new__(FactStore)
    events = await store.aevaluate_speaker_trust_events(
        "Neko",
        [
            {"role": "user", "content": "小明喜欢猫"},
            {"role": "user", "content": "小明不喜欢狗"},
        ],
        subject=owner,
        speaker_provenance={"speaker_id": "qq:9999", "speaker_trust": 1.0},
        speaker_is_owner=True,
        facts_snapshot=[
            {
                "id": "dogs", "text": "小明喜欢狗", "speaker_id": "qq:1001",
                **target.as_entry_fields(),
            },
            {
                "id": "cats", "text": "小明喜欢猫", "speaker_id": "qq:1001",
                **target.as_entry_fields(),
            },
        ],
    )

    assert [event["kind"] for event in events] == ["confirmation", "correction"]
    assert [event["source_fact_id"] for event in events] == ["cats", "dogs"]


@pytest.mark.asyncio
async def test_owner_signal_deduplicates_spelling_variants_for_one_fact():
    from memory.facts import FactStore

    owner = MemorySubject.group_participant("qq", "7788", "9999")
    target = MemorySubject.group_participant("qq", "7788", "1001")
    store = object.__new__(FactStore)
    facts = [{
        "id": "smart", "text": "Alice is smart", "speaker_id": "qq:1001",
        **target.as_entry_fields(),
    }]

    events = await store.aevaluate_speaker_trust_events(
        "Neko",
        [{"role": "user", "content": text} for text in (
            "Alice is not smart",
            "Alice is not smart!",
            "Alice is not smart.",
        )],
        subject=owner,
        speaker_provenance={"speaker_id": "qq:9999", "speaker_trust": 1.0},
        speaker_is_owner=True,
        facts_snapshot=facts,
    )

    assert len(events) == 1
    assert events[0]["kind"] == "correction"
    assert events[0]["source_fact_id"] == "smart"


@pytest.mark.asyncio
async def test_mixed_fact_with_residual_speaker_id_emits_no_owner_signal():
    from memory.facts import FactStore

    owner = MemorySubject.group_participant("qq", "7788", "9999")
    target = MemorySubject.group_participant("qq", "7788", "1001")
    store = object.__new__(FactStore)
    events = await store.aevaluate_speaker_trust_events(
        "Neko",
        [{"role": "user", "content": "Alice is not smart"}],
        subject=owner,
        speaker_provenance={"speaker_id": "qq:9999", "speaker_trust": 1.0},
        speaker_is_owner=True,
        facts_snapshot=[{
            "id": "smart",
            "text": "Alice is smart",
            "speaker_id": "qq:1001",
            "speaker_provenance_mixed": True,
            **target.as_entry_fields(),
        }],
    )

    assert events == []


@pytest.mark.asyncio
async def test_issued_trust_event_replays_after_response_loss_and_mixed_retry(
    tmp_path,
):
    from memory.facts import FactStore

    owner = MemorySubject.group_participant("qq", "7788", "9999")
    target = MemorySubject.group_participant("qq", "7788", "1001")
    fact = {
        "id": "smart", "text": "Alice is smart", "speaker_id": "qq:1001",
        **target.as_entry_fields(),
    }
    store = object.__new__(FactStore)
    store._facts = {"Neko": [fact]}
    store._locks = {}
    store._locks_guard = threading.Lock()
    store._persist_alocks = {}
    archive_path = tmp_path / "facts_archive.json"
    store._facts_archive_path = lambda _name: str(archive_path)
    store.aload_facts = AsyncMock(return_value=[fact])
    store.save_facts = MagicMock()
    messages = [{"role": "user", "content": "Alice is not smart"}]
    provenance = {"speaker_id": "qq:9999", "speaker_trust": 1.0}
    event = (await store.aevaluate_speaker_trust_events(
        "Neko", messages, subject=owner,
        speaker_provenance=provenance, speaker_is_owner=True,
        facts_snapshot=[fact],
    ))[0]

    assert await store.apersist_speaker_trust_events("Neko", [event]) == [event]
    store.save_facts.assert_called_once_with("Neko", _fact_lock_held=True)

    fact.pop("speaker_id")
    fact["speaker_provenance_mixed"] = True
    replayed = await store.aevaluate_speaker_trust_events(
        "Neko", messages, subject=owner,
        speaker_provenance=provenance, speaker_is_owner=True,
        facts_snapshot=[fact],
    )
    assert replayed == [event]

    unrelated = await store.aevaluate_speaker_trust_events(
        "Neko", [{"role": "user", "content": "Alice likes dogs"}],
        subject=owner, speaker_provenance=provenance, speaker_is_owner=True,
        facts_snapshot=[fact],
    )
    assert unrelated == []

    subject_archived_fact = {
        **fact,
        "subject_archived_at": "2026-08-01T22:00:00",
    }
    archive_path.write_text(
        json.dumps([subject_archived_fact], ensure_ascii=False),
        encoding="utf-8",
    )
    store._facts["Neko"] = []
    store.aload_facts = AsyncMock(return_value=[])
    archived = await store.aload_archived_speaker_trust_signal_facts("Neko")
    replayed_from_archive = await store.aevaluate_speaker_trust_events(
        "Neko", messages, subject=owner,
        speaker_provenance=provenance, speaker_is_owner=True,
        facts_snapshot=[], replay_facts_snapshot=archived,
    )
    assert replayed_from_archive == [event]
    assert await store.apersist_speaker_trust_events("Neko", [event]) == [event]


@pytest.mark.asyncio
async def test_trust_event_persistence_uses_full_scoped_fact_identity(tmp_path):
    from memory.facts import FactStore

    owner = MemorySubject.group_participant("qq", "7788", "9999")
    target = MemorySubject.group_participant("qq", "7788", "1001")
    foreign = MemorySubject.group_participant("qq", "8899", "1001")
    local_fact = {
        "id": "legacy-shared-id", "text": "Alice likes cats",
        "speaker_id": "qq:1001", **target.as_entry_fields(),
    }
    foreign_fact = {
        **local_fact, **foreign.as_entry_fields(),
    }
    store = object.__new__(FactStore)
    store._facts = {"Neko": [local_fact, foreign_fact]}
    store._locks = {}
    store._locks_guard = threading.Lock()
    store._persist_alocks = {}
    store._facts_archive_path = lambda _name: str(
        tmp_path / "facts_archive.json"
    )
    store.aload_facts = AsyncMock(return_value=store._facts["Neko"])
    store.save_facts = MagicMock()
    event = (await store.aevaluate_speaker_trust_events(
        "Neko", [{"role": "user", "content": "Alice likes cats"}],
        subject=owner,
        speaker_provenance={"speaker_id": "qq:9999", "speaker_trust": 1.0},
        speaker_is_owner=True,
        facts_snapshot=[local_fact],
    ))[0]

    assert await store.apersist_speaker_trust_events("Neko", [event]) == [event]
    assert local_fact["_speaker_trust_signal_events"] == [event]
    assert "_speaker_trust_signal_events" not in foreign_fact

    store._facts["Neko"] = [foreign_fact]
    store.aload_facts = AsyncMock(return_value=[foreign_fact])
    assert await store.apersist_speaker_trust_events("Neko", [event]) == []


@pytest.mark.asyncio
async def test_trust_event_persistence_revalidates_live_speaker_provenance(
    tmp_path,
):
    from memory.facts import FactStore

    owner = MemorySubject.group_participant("qq", "7788", "9999")
    target = MemorySubject.group_participant("qq", "7788", "1001")
    fact = {
        "id": "changed-speaker", "text": "Alice likes cats",
        "speaker_id": "qq:1001", **target.as_entry_fields(),
    }
    store = object.__new__(FactStore)
    store._facts = {"Neko": [fact]}
    store._locks = {}
    store._locks_guard = threading.Lock()
    store._persist_alocks = {}
    store._facts_archive_path = lambda _name: str(
        tmp_path / "facts_archive.json"
    )
    store.aload_facts = AsyncMock(return_value=[fact])
    store.save_facts = MagicMock()
    event = (await store.aevaluate_speaker_trust_events(
        "Neko", [{"role": "user", "content": "Alice likes cats"}],
        subject=owner,
        speaker_provenance={"speaker_id": "qq:9999", "speaker_trust": 1.0},
        speaker_is_owner=True,
        facts_snapshot=[fact],
    ))[0]

    fact.pop("speaker_id")
    fact["speaker_provenance_mixed"] = True

    assert await store.apersist_speaker_trust_events("Neko", [event]) == []
    assert fact.get("_speaker_trust_signal_events") in (None, [])
    store.save_facts.assert_not_called()


@pytest.mark.asyncio
async def test_trust_event_persistence_accepts_request_owned_reconciliation(
    tmp_path,
):
    from memory.facts import FactStore

    owner = MemorySubject.group_participant("qq", "7788", "9999")
    target = MemorySubject.group_participant("qq", "7788", "1001")
    fact = {
        "id": "confirmed-speaker", "text": "Alice likes cats",
        "speaker_id": "qq:1001", **target.as_entry_fields(),
    }
    store = object.__new__(FactStore)
    store._facts = {"Neko": [fact]}
    store._locks = {}
    store._locks_guard = threading.Lock()
    store._persist_alocks = {}
    store._facts_archive_path = lambda _name: str(
        tmp_path / "facts_archive.json"
    )
    store.aload_facts = AsyncMock(return_value=[fact])
    store.save_facts = MagicMock()
    event = (await store.aevaluate_speaker_trust_events(
        "Neko", [{"role": "user", "content": "Alice likes cats"}],
        subject=owner,
        speaker_provenance={"speaker_id": "qq:9999", "speaker_trust": 1.0},
        speaker_is_owner=True,
        facts_snapshot=[fact],
    ))[0]

    fact.pop("speaker_id")
    fact["speaker_provenance_mixed"] = True
    identity = (
        "confirmed-speaker", "group_participant", "qq:7788:1001",
        "group_participant:qq:7788:1001",
    )

    assert await store.apersist_speaker_trust_events(
        "Neko", [event], expected_reconciliations={identity: dict(fact)},
    ) == [event]
    assert fact["_speaker_trust_signal_events"] == [event]
    store.save_facts.assert_called_once_with("Neko", _fact_lock_held=True)


@pytest.mark.asyncio
async def test_failed_trust_persist_evicts_optimistic_event_cache(tmp_path):
    from memory.facts import FactStore

    owner = MemorySubject.group_participant("qq", "7788", "9999")
    target = MemorySubject.group_participant("qq", "7788", "1001")
    fact = {
        "id": "transient", "text": "Alice likes cats",
        "speaker_id": "qq:1001", **target.as_entry_fields(),
    }
    store = object.__new__(FactStore)
    store._facts = {"Neko": [fact]}
    store._locks = {}
    store._locks_guard = threading.Lock()
    store._persist_alocks = {}
    store._facts_archive_path = lambda _name: str(
        tmp_path / "facts_archive.json"
    )
    store.aload_facts = AsyncMock(return_value=[fact])
    store.save_facts = MagicMock(side_effect=OSError("transient write"))
    event = (await store.aevaluate_speaker_trust_events(
        "Neko", [{"role": "user", "content": "Alice likes cats"}],
        subject=owner,
        speaker_provenance={"speaker_id": "qq:9999", "speaker_trust": 1.0},
        speaker_is_owner=True,
        facts_snapshot=[fact],
    ))[0]

    with pytest.raises(OSError, match="transient write"):
        await store.apersist_speaker_trust_events("Neko", [event])

    assert "Neko" not in store._facts


@pytest.mark.asyncio
async def test_trust_reconciliation_rollback_restores_authored_provenance(
    tmp_path,
):
    from memory.facts import FactStore, _speaker_trust_fact_identity

    target = MemorySubject.group_participant("qq", "7788", "1001")
    authored = {
        "id": "rollback", "text": "Alice likes cats",
        "speaker_id": "qq:1001", "speaker_label": "Alice(1001)",
        "speaker_trust": 0.8, **target.as_entry_fields(),
    }
    reconciled = dict(authored)
    for key in ("speaker_id", "speaker_label", "speaker_trust"):
        reconciled.pop(key)
    reconciled["speaker_provenance_mixed"] = True
    identity = _speaker_trust_fact_identity(reconciled)
    store = object.__new__(FactStore)
    store._facts = {"Neko": [reconciled]}
    store._locks = {}
    store._locks_guard = threading.Lock()
    store._persist_alocks = {}
    store._facts_archive_path = lambda _name: str(
        tmp_path / "facts_archive.json"
    )
    store.aload_facts = AsyncMock(return_value=[reconciled])
    store.save_facts = MagicMock()

    assert await store.arollback_speaker_trust_reconciliations(
        "Neko",
        expected_reconciliations={identity: dict(reconciled)},
        previous_facts={identity: authored},
    )
    assert reconciled["speaker_id"] == "qq:1001"
    assert reconciled["speaker_label"] == "Alice(1001)"
    assert reconciled["speaker_trust"] == pytest.approx(0.8)
    assert "speaker_provenance_mixed" not in reconciled
    store.save_facts.assert_called_once_with("Neko", _fact_lock_held=True)


@pytest.mark.asyncio
async def test_trust_reconciliation_rollback_joins_repeated_cancellation(
    tmp_path,
):
    from memory.facts import FactStore, _speaker_trust_fact_identity

    target = MemorySubject.group_participant("qq", "7788", "1001")
    authored = {
        "id": "rollback", "text": "Alice likes cats",
        "speaker_id": "qq:1001", "speaker_trust": 0.8,
        **target.as_entry_fields(),
    }
    reconciled = {**authored, "speaker_provenance_mixed": True}
    identity = _speaker_trust_fact_identity(reconciled)
    store = object.__new__(FactStore)
    store._facts = {"Neko": [reconciled]}
    store._locks = {}
    store._locks_guard = threading.Lock()
    store._persist_alocks = {}
    store._facts_archive_path = lambda _name: str(
        tmp_path / "facts_archive.json"
    )
    store.aload_facts = AsyncMock(return_value=[reconciled])
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def _blocking_save(_name, *, _fact_lock_held=False):
        started.set()
        assert release.wait(timeout=5)
        finished.set()

    store.save_facts = MagicMock(side_effect=_blocking_save)

    rollback = asyncio.create_task(
        store.arollback_speaker_trust_reconciliations(
            "Neko",
            expected_reconciliations={identity: reconciled},
            previous_facts={identity: authored},
        )
    )
    for _ in range(200):
        if started.is_set():
            break
        await asyncio.sleep(0.01)
    assert started.is_set()
    rollback.cancel()
    await asyncio.sleep(0)
    rollback.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await rollback

    assert finished.is_set()


@pytest.mark.asyncio
async def test_trust_event_persists_when_source_moved_to_archive(tmp_path):
    """A route-time active fact may be archived before signal persistence."""
    from memory.facts import FactStore

    owner = MemorySubject.group_participant("qq", "7788", "9999")
    target = MemorySubject.group_participant("qq", "7788", "1001")
    fact = {
        "id": "smart", "text": "Alice is smart", "speaker_id": "qq:1001",
        **target.as_entry_fields(),
    }
    store = object.__new__(FactStore)
    store._facts = {"Neko": []}
    store._locks = {}
    store._locks_guard = threading.Lock()
    store._persist_alocks = {}
    archive_path = tmp_path / "facts_archive.json"
    store._facts_archive_path = lambda _name: str(archive_path)
    store._config_manager = SimpleNamespace(
        memory_dir=str(tmp_path), load_root_state=lambda: {"mode": "normal"},
    )
    store.aload_facts = AsyncMock(return_value=[])
    store.save_facts = MagicMock()
    event = (await store.aevaluate_speaker_trust_events(
        "Neko", [{"role": "user", "content": "Alice is not smart"}],
        subject=owner,
        speaker_provenance={"speaker_id": "qq:9999", "speaker_trust": 1.0},
        speaker_is_owner=True, facts_snapshot=[fact],
    ))[0]
    archive_path.write_text(json.dumps([fact]), encoding="utf-8")

    assert await store.apersist_speaker_trust_events("Neko", [event]) == [event]
    archived = json.loads(archive_path.read_text(encoding="utf-8"))
    assert archived[0]["_speaker_trust_signal_events"] == [event]


@pytest.mark.asyncio
async def test_same_owner_observation_has_distinct_events_for_scoped_facts():
    from memory.facts import FactStore

    owner_a = MemorySubject.group_participant("qq", "7788", "9999")
    owner_b = MemorySubject.group_participant("qq", "8899", "9999")
    target_a = MemorySubject.group_participant("qq", "7788", "1001")
    target_b = MemorySubject.group_participant("qq", "8899", "1001")
    target_other_scope = MemorySubject.create(
        "group_participant", "qq:7788:1001", scope="custom:qq:7788",
    )
    facts = [{
        "id": "same-id", "text": "小明喜欢猫", "speaker_id": "qq:1001",
        **target_a.as_entry_fields(),
    }, {
        "id": "same-id", "text": "小明喜欢猫", "speaker_id": "qq:1001",
        **target_b.as_entry_fields(),
    }, {
        "id": "other-scope", "text": "小明喜欢猫", "speaker_id": "qq:1001",
        **target_other_scope.as_entry_fields(),
    }]
    store = object.__new__(FactStore)
    messages = [{"role": "user", "content": "小明喜欢猫"}]
    provenance = {"speaker_id": "qq:9999", "speaker_trust": 1.0}

    events_a = await store.aevaluate_speaker_trust_events(
        "Neko", messages, subject=owner_a,
        speaker_provenance=provenance, speaker_is_owner=True,
        facts_snapshot=facts,
    )
    assert len(events_a) == 1
    event_a = events_a[0]
    event_b = (await store.aevaluate_speaker_trust_events(
        "Neko", messages, subject=owner_b,
        speaker_provenance=provenance, speaker_is_owner=True,
        facts_snapshot=facts,
    ))[0]
    repeated_a = (await store.aevaluate_speaker_trust_events(
        "Neko", messages, subject=owner_a,
        speaker_provenance=provenance, speaker_is_owner=True,
        facts_snapshot=facts,
    ))[0]
    other_character = (await store.aevaluate_speaker_trust_events(
        "Mimi", messages, subject=owner_a,
        speaker_provenance=provenance, speaker_is_owner=True,
        facts_snapshot=facts,
    ))[0]

    assert event_a["source_fact_id"] == event_b["source_fact_id"] == "same-id"
    assert event_a["event_id"] != event_b["event_id"]
    assert repeated_a["event_id"] == event_a["event_id"]
    assert other_character["event_id"] != event_a["event_id"]


@pytest.mark.asyncio
async def test_trust_event_identity_is_structural_across_delimiter_values():
    from memory.facts import FactStore

    subject_a = MemorySubject.create(
        "group_chat", "qq:7788", scope="tenant|a",
    )
    subject_b = MemorySubject.create(
        "group_chat", "qq:7788", scope="tenant",
    )
    store = object.__new__(FactStore)
    messages = [{"role": "user", "content": "小明喜欢猫"}]
    provenance = {"speaker_id": "qq:9999", "speaker_trust": 1.0}

    async def _event(subject, fact_id):
        return (await store.aevaluate_speaker_trust_events(
            "Neko", messages, subject=subject,
            speaker_provenance=provenance, speaker_is_owner=True,
            facts_snapshot=[{
                "id": fact_id,
                "text": "小明喜欢猫",
                "speaker_id": "qq:1001",
                **subject.as_entry_fields(),
            }],
        ))[0]

    event_a = await _event(subject_a, "b")
    event_b = await _event(subject_b, "a|b")

    # The old pipe-joined tuples both serialized as
    # group_chat|qq:7788|tenant|a|b.
    assert event_a["event_id"] != event_b["event_id"]


def test_fresh_persona_entry_preserves_missing_speaker_trust():
    from memory.persona.facts import FactsMixin

    entry = FactsMixin()._build_fact_entry(
        "unscored reflection", source="reflection", source_id="ref-1",
        speaker_provenance={
            "speaker_id": "qq:1001", "speaker_label": "Alice(1001)",
        },
    )

    assert entry["speaker_id"] == "qq:1001"
    assert entry["speaker_label"] == "Alice(1001)"
    assert "speaker_trust" not in entry


def test_fresh_persona_entry_preserves_only_explicit_reflection_window():
    from memory.persona.facts import FactsMixin

    explicit = FactsMixin()._build_fact_entry(
        "dated reflection", source="reflection", source_id="ref-dated",
        speaker_provenance={
            "created_at": "2026-06-15T12:00:00",
            "event_when_raw": {"start": {"offset": -1, "unit": "week"}},
            "event_start_at": "2026-06-08T12:00:00",
            "event_end_at": None,
        },
    )
    assert explicit["event_when_raw"] == {
        "start": {"offset": -1, "unit": "week"},
    }
    assert explicit["event_start_at"] == "2026-06-08T12:00:00"
    assert explicit["event_end_at"] is None

    timeless = FactsMixin()._build_fact_entry(
        "timeless reflection", source="reflection_time_driven",
        source_id="ref-timeless", speaker_provenance={
            "created_at": "2026-06-15T12:00:00",
            "event_when_raw": None,
            "event_start_at": "2026-06-15T12:00:00",
            "event_end_at": "2026-06-15T12:00:00",
        },
    )
    assert "event_when_raw" not in timeless
    assert "event_start_at" not in timeless
    assert "event_end_at" not in timeless


@pytest.mark.parametrize(
    "invalid_trust", [float("nan"), float("inf"), 10 ** 400],
)
def test_fresh_persona_entry_preserves_non_finite_trust_as_unknown(invalid_trust):
    from memory.persona.facts import FactsMixin

    entry = FactsMixin()._build_fact_entry(
        "malformed reflection", source="reflection", source_id="ref-bad",
        speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": invalid_trust,
        },
    )

    assert entry["id"] == "prom_ref-bad"
    assert entry["speaker_id"] == "qq:1001"
    assert "speaker_trust" not in entry

    finite_entry = FactsMixin()._build_fact_entry(
        "scored reflection", source="reflection", source_id="ref-good",
        speaker_provenance={"speaker_id": "qq:1001", "speaker_trust": 0.7},
    )
    assert finite_entry["speaker_trust"] == pytest.approx(0.7)


def test_fresh_persona_entry_rejects_residual_mixed_provenance():
    from memory.persona.facts import FactsMixin

    entry = FactsMixin()._build_fact_entry(
        "mixed reflection", source="reflection", source_id="ref-mixed",
        speaker_provenance={
            "speaker_id": "qq:1001",
            "speaker_trust": 0.9,
            "speaker_label": "Alice(1001)",
            "speaker_provenance_mixed": True,
        },
    )

    assert entry["speaker_provenance_mixed"] is True
    assert "speaker_id" not in entry
    assert "speaker_trust" not in entry
    assert "speaker_label" not in entry


def test_observation_texts_accepts_runtime_messages_and_rejects_assistant_text():
    assert observation_texts([
        HumanMessage(content="  owner confirmation  "),
        AIMessage(content="model-produced correction"),
    ]) == ["owner confirmation"]


def test_correction_relation_requires_the_same_proposition():
    assert deterministic_relation("小明喜欢猫", "小明不喜欢猫") == "correction"
    assert deterministic_relation("喜欢猫", "不喜欢猫") == "correction"
    assert deterministic_relation("小明喜欢猫", "小明不喜欢狗") is None
    assert deterministic_relation(
        "小明认识喜欢猫的人", "小明认识不喜欢猫的人",
    ) is None
    assert deterministic_relation(
        "喜欢猫的人认识小明", "不喜欢猫的人认识小明",
    ) is None
    assert deterministic_relation(
        "喜欢猫的人来自北京", "不喜欢猫的人来自北京",
    ) is None
    assert deterministic_relation(
        "喜欢猫的女孩来自北京", "不喜欢猫的女孩来自北京",
    ) is None
    assert deterministic_relation("Alice is able", "Alice is notable") is None
    assert deterministic_relation(
        "Alice likes false eyelashes", "Alice likes eyelashes",
    ) is None
    assert deterministic_relation(
        "Alice has the wrong address", "Alice has the address",
    ) is None
    assert deterministic_relation(
        "Alice lives at No 5 Main Street", "Alice lives at 5 Main Street",
    ) is None
    assert deterministic_relation(
        "Alice clicked the No button", "Alice clicked the button",
    ) is None
    assert deterministic_relation(
        "Alice has no cats", "Alice has cats",
    ) == "correction"
    assert deterministic_relation(
        "Alice did not click the button", "Alice did click the button",
    ) == "correction"
    assert deterministic_relation(
        "Alice has never clicked the button", "Alice has clicked the button",
    ) == "correction"
    assert deterministic_relation(
        "Alice is not only smart", "Alice is only smart",
    ) is None
    assert deterministic_relation(
        "Alice clicked the dislike button", "Alice clicked the button",
    ) is None
    assert deterministic_relation(
        "Alice clicked the never button", "Alice clicked the button",
    ) is None
    assert deterministic_relation("她来自锡山区", "她来自无锡山区") is None
    assert deterministic_relation("她认识不二同学", "她认识二同学") is None


def test_duplicate_correction_provenance_folds_conservatively():
    from memory.persona.corrections import CorrectionsMixin

    queued: list[dict] = []
    assert CorrectionsMixin._build_correction_list(
        queued, "old", "new", "master",
        old_speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": 0.3,
        },
        new_speaker_provenance={
            "speaker_id": "qq:2002", "speaker_trust": 0.8,
        },
    ) == queued
    assert CorrectionsMixin._build_correction_list(
        queued, "old", "new", "master",
        old_speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": 1.0,
        },
        new_speaker_provenance={
            "speaker_id": "qq:3003", "speaker_trust": 1.0,
        },
    ) == queued
    assert queued[0]["old_speaker_trust"] == pytest.approx(0.3)
    assert queued[0]["old_speaker_id"] == "qq:1001"
    assert "new_speaker_id" not in queued[0]
    assert "new_speaker_trust" not in queued[0]
    assert queued[0]["new_speaker_provenance_mixed"] is True


def test_duplicate_correction_backfills_missing_trust_for_same_speaker():
    from memory.persona.corrections import CorrectionsMixin

    queued: list[dict] = []
    CorrectionsMixin._build_correction_list(
        queued, "old", "new", "master",
        old_speaker_provenance={"speaker_id": "qq:1001"},
    )
    CorrectionsMixin._build_correction_list(
        queued, "old", "new", "master",
        old_speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": 0.7,
        },
    )

    assert queued[0]["old_speaker_id"] == "qq:1001"
    assert queued[0]["old_speaker_trust"] == pytest.approx(0.7)


def test_correction_queue_rejects_residual_mixed_provenance():
    from memory.persona.corrections import CorrectionsMixin

    queued: list[dict] = []
    CorrectionsMixin._build_correction_list(
        queued, "old", "new", "master",
        new_speaker_provenance={
            "speaker_id": "qq:1001",
            "speaker_trust": 0.9,
            "speaker_provenance_mixed": True,
        },
    )

    assert queued[0]["new_speaker_provenance_mixed"] is True
    assert "new_speaker_id" not in queued[0]
    assert "new_speaker_trust" not in queued[0]


def test_duplicate_correction_persists_residual_mixed_cleanup():
    from memory.persona.corrections import CorrectionsMixin

    queued = [{
        "old_text": "old",
        "new_text": "new",
        "entity": "master",
        "scope": None,
        "new_speaker_provenance_mixed": True,
        "new_speaker_id": "qq:1001",
        "new_speaker_trust": 0.9,
    }]

    result = CorrectionsMixin._build_correction_list(
        queued, "old", "new", "master",
        new_speaker_provenance={"speaker_provenance_mixed": True},
    )

    assert result is queued
    assert queued[0]["new_speaker_provenance_mixed"] is True
    assert "new_speaker_id" not in queued[0]
    assert "new_speaker_trust" not in queued[0]


@pytest.mark.parametrize(
    "invalid_trust", [float("nan"), float("inf"), 10 ** 400],
)
def test_correction_queue_preserves_non_finite_trust_as_unknown(invalid_trust):
    from memory.persona.corrections import CorrectionsMixin

    queued: list[dict] = []
    CorrectionsMixin._build_correction_list(
        queued, "old", "new", "master",
        old_speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": invalid_trust,
        },
    )

    assert queued[0]["old_speaker_id"] == "qq:1001"
    assert "old_speaker_trust" not in queued[0]

    # A legacy malformed queue value remains unknown and may be replaced by
    # later finite provenance; it must not be normalized into arbitration.
    queued[0]["old_speaker_trust"] = invalid_trust
    CorrectionsMixin._build_correction_list(
        queued, "old", "new", "master",
        old_speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": 0.7,
        },
    )
    assert queued[0]["old_speaker_trust"] == pytest.approx(0.7)


def test_derived_provenance_marks_known_plus_unknown_sources_as_mixed():
    assert provenance_of_entries([
        {"speaker_id": "qq:1001", "speaker_trust": 0.8},
        {"text": "legacy source without provenance"},
    ]) == {"speaker_provenance_mixed": True}


def test_derived_provenance_keeps_all_unknown_sources_unattributed():
    assert provenance_of_entries([
        {"text": "legacy source one"},
        {"text": "legacy source two"},
    ]) == {}


def test_derived_provenance_preserves_mixed_source_marker():
    assert provenance_of_entries([
        {"speaker_id": "qq:1001", "speaker_trust": 0.8},
        {
            "speaker_id": "qq:1001",
            "speaker_trust": 0.8,
            "speaker_provenance_mixed": True,
        },
    ]) == {"speaker_provenance_mixed": True}


def test_derived_provenance_marks_multiple_stable_speakers_as_mixed():
    assert provenance_of_entries([
        {"speaker_id": "qq:1001", "speaker_trust": 0.8},
        {"speaker_id": "qq:2002", "speaker_trust": 0.6},
    ]) == {"speaker_provenance_mixed": True}


def test_derived_provenance_does_not_borrow_an_omitted_trust_value():
    assert provenance_of_entries([
        {"speaker_id": "qq:1001", "speaker_trust": 0.8},
        {"speaker_id": "qq:1001", "text": "unscored source"},
    ]) == {"speaker_id": "qq:1001"}


def test_derived_provenance_omits_non_finite_trust():
    assert provenance_of_entries([
        {"speaker_id": "qq:1001", "speaker_trust": float("nan")},
        {"speaker_id": "qq:1001", "speaker_trust": 0.8},
    ]) == {"speaker_id": "qq:1001"}
    assert provenance_of_entries([
        {"speaker_id": "qq:1001", "speaker_trust": float("inf")},
    ]) == {"speaker_id": "qq:1001"}


@pytest.mark.asyncio
async def test_scoped_route_returns_request_derived_events_when_no_fact_created():
    from app.memory_server import routes
    from app.memory_server.routes import ScopedHistoryRequest

    event = {
        "kind": "confirmation",
        "speaker_id": "qq:1001",
        "event_id": "event-1",
        "source_speaker_id": "qq:9999",
    }
    store = SimpleNamespace(
        aload_facts=AsyncMock(return_value=[]),
        aload_archived_speaker_trust_signal_facts=AsyncMock(return_value=[]),
        aevaluate_speaker_trust_events=AsyncMock(return_value=[event]),
        apersist_speaker_trust_events=AsyncMock(return_value=[event]),
        extract_facts=AsyncMock(return_value=[]),
    )
    request = ScopedHistoryRequest(
        input_history=json.dumps([{
            "role": "user",
            "content": [{"type": "text", "text": "same fact"}],
        }]),
        subject={"subject_kind": "participant", "subject_id": "qq:9999"},
        speaker_label="Owner(9999)",
        speaker_trust=1.0,
        speaker_id="qq:9999",
        speaker_is_owner=True,
    )
    with patch.object(routes.runtime, "fact_store", store):
        result = await routes.process_scoped_history("Neko", request)
    assert result["created"] == 0
    assert result["trust_events"] == [event]
    kwargs = store.aevaluate_speaker_trust_events.await_args.kwargs
    assert kwargs["speaker_is_owner"] is True
    assert kwargs["speaker_provenance"]["speaker_id"] == "qq:9999"


@pytest.mark.asyncio
@pytest.mark.parametrize("segmented", [False, True])
async def test_scoped_route_returns_only_durably_attached_trust_events(segmented):
    from app.memory_server import routes
    from app.memory_server.routes import ScopedHistoryRequest

    event = {
        "kind": "confirmation",
        "speaker_id": "qq:1001",
        "event_id": "event-lost-to-forget",
        "source_speaker_id": "qq:9999",
        "source_fact_id": "forgotten-fact",
        "observation_id": "observation-1",
    }
    archived_signal_fact = {
        "id": "archived-source",
        "_speaker_trust_signal_events": [event],
    }
    segment = {
        "input_history": json.dumps([{
            "role": "user", "content": "Alice likes cats",
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:9999",
        },
        "speaker_label": "Owner(9999)",
        "speaker_id": "qq:9999",
        "speaker_trust": 1.0,
        "speaker_is_owner": True,
    }
    store = SimpleNamespace(
        aload_facts=AsyncMock(return_value=[]),
        aevaluate_speaker_trust_events=AsyncMock(return_value=[event]),
        aload_archived_speaker_trust_signal_facts=AsyncMock(
            return_value=[archived_signal_fact]
        ),
        apersist_speaker_trust_events=AsyncMock(return_value=[]),
    )
    if segmented:
        store.extract_facts_batch = AsyncMock(return_value=[{
            "status": "ok", "created": [], "dropped": 0,
        }])
        request = ScopedHistoryRequest(segments=[segment])
    else:
        store.extract_facts = AsyncMock(return_value=[])
        request = ScopedHistoryRequest(**segment)

    with patch.object(routes.runtime, "fact_store", store):
        result = await routes.process_scoped_history("Neko", request)

    if segmented:
        assert result["segments"][0]["trust_events"] == []
    else:
        assert result["trust_events"] == []
    replay_facts = (
        store.aevaluate_speaker_trust_events.await_args.kwargs[
            "replay_facts_snapshot"
        ]
    )
    assert archived_signal_fact in replay_facts


@pytest.mark.asyncio
async def test_scoped_batch_excludes_post_observation_events_before_persistence():
    from app.memory_server import routes
    from app.memory_server.routes import ScopedHistoryRequest

    event = {
        "kind": "correction",
        "speaker_id": "qq:1001",
        "event_id": "post-observation-event",
        "source_fact_id": "later-fact",
        "source_subject_kind": "group_participant",
        "source_subject_id": "qq:7788:1001",
        "source_scope": "group_participant:qq:7788:1001",
    }
    later_fact = {
        "id": "later-fact",
        "subject_kind": "group_participant",
        "subject_id": "qq:7788:1001",
        "scope": "group_participant:qq:7788:1001",
    }
    same_id_other_scope = {
        **later_fact,
        "subject_id": "qq:7788:2002",
        "scope": "group_participant:qq:7788:2002",
    }
    store = SimpleNamespace(
        aload_facts=AsyncMock(return_value=[later_fact, same_id_other_scope]),
        extract_facts_batch=AsyncMock(return_value=[{
            "status": "ok", "created": [], "dropped": 0,
        }]),
        aevaluate_speaker_trust_events=AsyncMock(return_value=[event]),
        aload_archived_speaker_trust_signal_facts=AsyncMock(
            return_value=[later_fact, same_id_other_scope],
        ),
        apersist_speaker_trust_events=AsyncMock(return_value=[event]),
    )
    request = ScopedHistoryRequest(segments=[{
        "input_history": json.dumps([{
            "role": "user", "content": "owner retry",
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:9999",
        },
        "speaker_label": "Owner(9999)",
        "speaker_id": "qq:9999",
        "speaker_is_owner": True,
        "trust_signal_excluded_fact_identities": [[
            "later-fact", "group_participant", "qq:7788:1001",
            "group_participant:qq:7788:1001",
        ]],
    }])

    with patch.object(routes.runtime, "fact_store", store):
        result = await routes.process_scoped_history("Neko", request)

    assert result["segments"][0]["trust_events"] == []
    kwargs = store.aevaluate_speaker_trust_events.await_args.kwargs
    assert kwargs["facts_snapshot"] == [same_id_other_scope]
    assert kwargs["replay_facts_snapshot"] == [
        same_id_other_scope, same_id_other_scope,
    ]
    store.apersist_speaker_trust_events.assert_not_awaited()


@pytest.mark.asyncio
async def test_scoped_route_revalidates_trust_signals_after_concurrent_forget():
    from app.memory_server import routes
    from app.memory_server.routes import ScopedHistoryRequest
    from memory.facts import FactStore

    active_facts = [{
        "id": "forgotten-fact",
        "text": "Alice likes cats",
        "speaker_id": "qq:1001",
        "subject_kind": "group_participant",
        "subject_id": "qq:7788:1001",
        "scope": "group_participant:qq:7788:1001",
    }]
    same_id_other_scope = {
        **active_facts[0],
        "subject_id": "qq:8899:1001",
        "scope": "group_participant:qq:8899:1001",
    }

    async def _load_facts(_lanlan_name):
        return list(active_facts)

    async def _extract_facts(*_args, **_kwargs):
        return []

    async def _stamp_display_name(*_args, **_kwargs):
        active_facts.clear()
        active_facts.append(same_id_other_scope)
        return True

    store = SimpleNamespace(
        aload_facts=_load_facts,
        aload_archived_speaker_trust_signal_facts=AsyncMock(return_value=[]),
        extract_facts=_extract_facts,
        apersist_speaker_trust_events=_persist_trust_events,
    )
    store.aevaluate_speaker_trust_events = (
        FactStore.aevaluate_speaker_trust_events.__get__(store, FactStore)
    )
    request = ScopedHistoryRequest(
        input_history=json.dumps([{
            "role": "user",
            "content": [{"type": "text", "text": "Alice likes cats"}],
        }]),
        subject={
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:9999",
        },
        speaker_label="Owner(9999)",
        speaker_trust=1.0,
        speaker_id="qq:9999",
        speaker_is_owner=True,
        display_name="Owner",
    )
    persona = SimpleNamespace(
        aupdate_subject_display_name=_stamp_display_name,
    )
    with patch.object(routes.runtime, "fact_store", store), patch.object(
        routes.runtime, "persona_manager", persona,
    ):
        result = await routes.process_scoped_history("Neko", request)

    assert result["trust_events"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fact_id", ["member-fact", 1])
async def test_scoped_route_owner_signal_uses_pre_write_provenance(fact_id):
    from app.memory_server import routes
    from app.memory_server.routes import ScopedHistoryRequest
    from memory.facts import FactStore, _speaker_trust_fact_id

    prior = {
        "id": fact_id,
        "text": "Alice likes cats",
        "speaker_id": "qq:1001",
        "speaker_trust": 0.8,
        "subject_kind": "group_participant",
        "subject_id": "qq:7788:1001",
        "scope": "group_participant:qq:7788:1001",
    }
    reconciled = dict(prior)
    reconciled.pop("speaker_id")
    reconciled.pop("speaker_trust")
    reconciled["speaker_provenance_mixed"] = True

    async def _extract_facts(*_args, reconciled_facts=None, **_kwargs):
        reconciled_facts.append(reconciled)
        return []

    async def _persist_events(
        _name, events, *, expected_reconciliations,
    ):
        identity = (
            _speaker_trust_fact_id(fact_id),
            "group_participant", "qq:7788:1001",
            "group_participant:qq:7788:1001",
        )
        assert expected_reconciliations[identity] == reconciled
        return events

    store = SimpleNamespace(
        aload_facts=AsyncMock(side_effect=[[prior], [reconciled]]),
        aload_archived_speaker_trust_signal_facts=AsyncMock(return_value=[]),
        extract_facts=_extract_facts,
        apersist_speaker_trust_events=_persist_events,
    )
    store.aevaluate_speaker_trust_events = (
        FactStore.aevaluate_speaker_trust_events.__get__(store, FactStore)
    )
    request = ScopedHistoryRequest(
        input_history=json.dumps([{
            "role": "user",
            "content": [{"type": "text", "text": "Alice likes cats"}],
        }]),
        subject={
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:9999",
        },
        speaker_label="Owner(9999)",
        speaker_trust=1.0,
        speaker_id="qq:9999",
        speaker_is_owner=True,
    )

    with patch.object(routes.runtime, "fact_store", store):
        result = await routes.process_scoped_history("Neko", request)

    assert len(result["trust_events"]) == 1
    assert result["trust_events"][0]["kind"] == "confirmation"
    assert result["trust_events"][0]["speaker_id"] == "qq:1001"


@pytest.mark.asyncio
async def test_failed_owner_segment_persists_signal_for_its_reconciliation():
    from app.memory_server import routes
    from app.memory_server.routes import ScopedHistoryRequest
    from memory.facts import FactStore, _speaker_trust_fact_identity

    prior = {
        "id": "member-fact", "text": "Alice likes cats",
        "speaker_id": "qq:1001", "speaker_trust": 0.8,
        "subject_kind": "group_participant",
        "subject_id": "qq:7788:1001",
        "scope": "group_participant:qq:7788:1001",
    }
    reconciled = {**prior, "speaker_provenance_mixed": True}
    persisted = AsyncMock(side_effect=lambda _name, events, **_kwargs: events)
    store = SimpleNamespace(
        aload_facts=AsyncMock(side_effect=[[prior], [reconciled]]),
        aload_archived_speaker_trust_signal_facts=AsyncMock(return_value=[]),
        extract_facts_batch=AsyncMock(return_value=[{
            "status": "failed", "created": [], "dropped": 1,
            "reconciled": [reconciled],
        }]),
        apersist_speaker_trust_events=persisted,
    )
    store.aevaluate_speaker_trust_events = (
        FactStore.aevaluate_speaker_trust_events.__get__(store, FactStore)
    )
    request = ScopedHistoryRequest(segments=[{
        "input_history": json.dumps([{
            "role": "user",
            "content": [{"type": "text", "text": "Alice likes cats"}],
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:9999",
        },
        "speaker_label": "Owner(9999)", "speaker_trust": 1.0,
        "speaker_id": "qq:9999", "speaker_is_owner": True,
    }])

    with patch.object(routes.runtime, "fact_store", store):
        result = await routes.process_scoped_history("Neko", request)

    segment = result["segments"][0]
    assert segment["status"] == "failed"
    # Failed segments remain retained by the caller, so their durable event is
    # intentionally hidden until the successful retry replays it.
    assert segment["trust_events"] == []
    persisted.assert_awaited_once()
    assert persisted.await_args.kwargs["expected_reconciliations"] == {
        _speaker_trust_fact_identity(prior): reconciled,
    }


@pytest.mark.asyncio
async def test_scoped_route_rolls_back_reconciliation_and_retries_trust_write():
    from app.memory_server import routes
    from app.memory_server.routes import ScopedHistoryRequest
    from memory.facts import FactStore, _speaker_trust_fact_identity

    prior = {
        "id": "member-fact", "text": "Alice likes cats",
        "speaker_id": "qq:1001", "speaker_trust": 0.8,
        "subject_kind": "group_participant",
        "subject_id": "qq:7788:1001",
        "scope": "group_participant:qq:7788:1001",
    }
    reconciled = dict(prior)
    reconciled.pop("speaker_id")
    reconciled.pop("speaker_trust")
    reconciled["speaker_provenance_mixed"] = True

    async def _extract_facts(*_args, reconciled_facts=None, **_kwargs):
        reconciled_facts.append(reconciled)
        return []

    persist = AsyncMock(side_effect=[
        OSError("transient trust write"),
        lambda _name, events, **_kwargs: events,
    ])

    async def _persist(_name, events, **kwargs):
        result = await persist(_name, events, **kwargs)
        return result(_name, events, **kwargs) if callable(result) else result

    rollback = AsyncMock(return_value=True)
    store = SimpleNamespace(
        aload_facts=AsyncMock(side_effect=[[prior], [reconciled]]),
        aload_archived_speaker_trust_signal_facts=AsyncMock(return_value=[]),
        extract_facts=_extract_facts,
        apersist_speaker_trust_events=_persist,
        arollback_speaker_trust_reconciliations=rollback,
    )
    store.aevaluate_speaker_trust_events = (
        FactStore.aevaluate_speaker_trust_events.__get__(store, FactStore)
    )
    request = ScopedHistoryRequest(
        input_history=json.dumps([{
            "role": "user",
            "content": [{"type": "text", "text": "Alice likes cats"}],
        }]),
        subject={
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:9999",
        },
        speaker_label="Owner(9999)", speaker_trust=1.0,
        speaker_id="qq:9999", speaker_is_owner=True,
    )

    with patch.object(routes.runtime, "fact_store", store):
        result = await routes.process_scoped_history("Neko", request)

    assert len(result["trust_events"]) == 1
    assert persist.await_count == 2
    identity = _speaker_trust_fact_identity(prior)
    rollback.assert_awaited_once_with(
        "Neko",
        expected_reconciliations={identity: reconciled},
        previous_facts={identity: prior},
    )


@pytest.mark.asyncio
async def test_batch_trust_failure_rolls_back_only_owner_reconciliation():
    from app.memory_server import routes
    from app.memory_server.routes import ScopedHistoryRequest
    from memory.facts import FactStore, _speaker_trust_fact_identity

    owner = {
        "input_history": json.dumps([{
            "role": "user",
            "content": [{"type": "text", "text": "Alice likes cats"}],
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:9999",
        },
        "speaker_label": "Owner(9999)", "speaker_id": "qq:9999",
        "speaker_trust": 1.0, "speaker_is_owner": True,
    }
    member = {
        "input_history": json.dumps([{
            "role": "user",
            "content": [{"type": "text", "text": "Alice likes cats"}],
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:2002",
        },
        "speaker_label": "Member(2002)", "speaker_id": "qq:2002",
        "speaker_trust": 0.3,
    }
    prior = {
        "id": "member-fact", "text": "Alice likes cats",
        "speaker_id": "qq:1001", "speaker_trust": 0.8,
        "subject_kind": "group_participant",
        "subject_id": "qq:7788:1001",
        "scope": "group_participant:qq:7788:1001",
    }
    owner_reconciled = {**prior, "speaker_label": "Owner-seen"}
    later_reconciled = {**prior, "speaker_label": "Member-later"}
    persist = AsyncMock(side_effect=[
        OSError("transient trust write"),
        lambda _name, events, **_kwargs: events,
    ])

    async def _persist(_name, events, **kwargs):
        result = await persist(_name, events, **kwargs)
        return result(_name, events, **kwargs) if callable(result) else result

    rollback = AsyncMock(return_value=True)
    store = SimpleNamespace(
        aload_facts=AsyncMock(side_effect=[[prior], [later_reconciled]]),
        aload_archived_speaker_trust_signal_facts=AsyncMock(return_value=[]),
        extract_facts_batch=AsyncMock(return_value=[
            {
                "status": "ok", "created": [], "dropped": 0,
                "reconciled": [owner_reconciled],
            },
            {
                "status": "ok", "created": [], "dropped": 0,
                "reconciled": [later_reconciled],
            },
        ]),
        apersist_speaker_trust_events=_persist,
        arollback_speaker_trust_reconciliations=rollback,
    )
    store.aevaluate_speaker_trust_events = (
        FactStore.aevaluate_speaker_trust_events.__get__(store, FactStore)
    )

    with patch.object(routes.runtime, "fact_store", store):
        await routes.process_scoped_history(
            "Neko", ScopedHistoryRequest(segments=[owner, member]),
        )

    identity = _speaker_trust_fact_identity(prior)
    rollback.assert_awaited_once_with(
        "Neko",
        expected_reconciliations={identity: owner_reconciled},
        previous_facts={identity: prior},
    )


@pytest.mark.asyncio
async def test_scoped_route_owner_signal_keeps_concurrent_provenance_change():
    from app.memory_server import routes
    from app.memory_server.routes import ScopedHistoryRequest
    from memory.facts import FactStore

    prior = {
        "id": "member-fact",
        "text": "Alice likes cats",
        "speaker_id": "qq:1001",
        "subject_kind": "group_participant",
        "subject_id": "qq:7788:1001",
        "scope": "group_participant:qq:7788:1001",
    }
    reconciled = dict(prior)
    reconciled.pop("speaker_id")
    reconciled["speaker_provenance_mixed"] = True
    concurrent_current = {
        **reconciled,
        "speaker_label": "Concurrent update",
    }

    async def _extract_facts(*_args, reconciled_facts=None, **_kwargs):
        reconciled_facts.append(reconciled)
        return []

    store = SimpleNamespace(
        aload_facts=AsyncMock(side_effect=[[prior], [concurrent_current]]),
        aload_archived_speaker_trust_signal_facts=AsyncMock(return_value=[]),
        extract_facts=_extract_facts,
        apersist_speaker_trust_events=_persist_trust_events,
    )
    store.aevaluate_speaker_trust_events = (
        FactStore.aevaluate_speaker_trust_events.__get__(store, FactStore)
    )
    request = ScopedHistoryRequest(
        input_history=json.dumps([{
            "role": "user",
            "content": [{"type": "text", "text": "Alice likes cats"}],
        }]),
        subject={
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:9999",
        },
        speaker_label="Owner(9999)",
        speaker_trust=1.0,
        speaker_id="qq:9999",
        speaker_is_owner=True,
    )

    with patch.object(routes.runtime, "fact_store", store):
        result = await routes.process_scoped_history("Neko", request)

    assert result["trust_events"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("member_first", [True, False])
async def test_batch_owner_signal_sees_only_earlier_segments(member_first):
    from app.memory_server import routes
    from app.memory_server.routes import ScopedHistoryRequest
    from memory.facts import FactStore

    member = {
        "input_history": json.dumps([{
            "role": "user",
            "content": [{"type": "text", "text": "Alice likes cats"}],
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:1001",
        },
        "speaker_label": "Alice(1001)",
        "speaker_id": "qq:1001",
        "speaker_trust": 0.3,
    }
    owner = {
        "input_history": json.dumps([{
            "role": "user",
            "content": [{"type": "text", "text": "Alice likes cats"}],
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:9999",
        },
        "speaker_label": "Owner(9999)",
        "speaker_id": "qq:9999",
        "speaker_trust": 1.0,
        "speaker_is_owner": True,
    }
    created_fact = {
        "id": "member-fact",
        "text": "Alice likes cats",
        "speaker_id": "qq:1001",
        "subject_kind": "group_participant",
        "subject_id": "qq:7788:1001",
        "scope": "group_participant:qq:7788:1001",
    }
    segments = [member, owner] if member_first else [owner, member]
    results = (
        [
            {"status": "ok", "created": [created_fact], "dropped": 0},
            {"status": "ok", "created": [], "dropped": 0},
        ]
        if member_first
        else [
            {"status": "ok", "created": [], "dropped": 0},
            {"status": "ok", "created": [created_fact], "dropped": 0},
        ]
    )
    store = SimpleNamespace(
        aload_facts=AsyncMock(side_effect=[[], [created_fact]]),
        aload_archived_speaker_trust_signal_facts=AsyncMock(return_value=[]),
        extract_facts_batch=AsyncMock(return_value=results),
        apersist_speaker_trust_events=_persist_trust_events,
    )
    store.aevaluate_speaker_trust_events = (
        FactStore.aevaluate_speaker_trust_events.__get__(store, FactStore)
    )
    with patch.object(routes.runtime, "fact_store", store):
        response = await routes.process_scoped_history(
            "Neko", ScopedHistoryRequest(segments=segments),
        )
    owner_index = 1 if member_first else 0
    events = response["segments"][owner_index]["trust_events"]
    if member_first:
        assert len(events) == 1
        assert events[0]["kind"] == "confirmation"
        assert events[0]["speaker_id"] == "qq:1001"
    else:
        assert events == []


@pytest.mark.asyncio
async def test_batch_owner_signal_ignores_later_segment_reconciliation():
    from app.memory_server import routes
    from app.memory_server.routes import ScopedHistoryRequest
    from memory.facts import FactStore

    owner = {
        "input_history": json.dumps([{
            "role": "user",
            "content": [{"type": "text", "text": "Alice likes cats"}],
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:9999",
        },
        "speaker_label": "Owner(9999)",
        "speaker_id": "qq:9999",
        "speaker_trust": 1.0,
        "speaker_is_owner": True,
    }
    member = {
        "input_history": json.dumps([{
            "role": "user",
            "content": [{"type": "text", "text": "Alice likes cats"}],
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:2002",
        },
        "speaker_label": "Member(2002)",
        "speaker_id": "qq:2002",
        "speaker_trust": 0.3,
    }
    prior = {
        "id": "prior-fact",
        "text": "Alice likes cats",
        "speaker_id": "qq:1001",
        "subject_kind": "group_participant",
        "subject_id": "qq:7788:1001",
        "scope": "group_participant:qq:7788:1001",
    }
    reconciled = {
        **prior,
        "speaker_provenance_mixed": True,
    }
    reconciled.pop("speaker_id")
    store = SimpleNamespace(
        aload_facts=AsyncMock(side_effect=[[prior], [reconciled]]),
        aload_archived_speaker_trust_signal_facts=AsyncMock(return_value=[]),
        extract_facts_batch=AsyncMock(return_value=[
            {"status": "ok", "created": [], "dropped": 0},
            {
                "status": "ok", "created": [],
                "reconciled": [reconciled], "dropped": 0,
            },
        ]),
        apersist_speaker_trust_events=_persist_trust_events,
    )
    store.aevaluate_speaker_trust_events = (
        FactStore.aevaluate_speaker_trust_events.__get__(store, FactStore)
    )

    with patch.object(routes.runtime, "fact_store", store):
        response = await routes.process_scoped_history(
            "Neko", ScopedHistoryRequest(segments=[owner, member]),
        )

    events = response["segments"][0]["trust_events"]
    assert len(events) == 1
    assert events[0]["kind"] == "confirmation"
    assert events[0]["speaker_id"] == "qq:1001"


@pytest.mark.asyncio
@pytest.mark.parametrize("fact_id", ["prior-fact", 0])
async def test_batch_owner_signal_preserves_concurrent_provenance_update(fact_id):
    from app.memory_server import routes
    from app.memory_server.routes import ScopedHistoryRequest
    from memory.facts import FactStore

    owner = {
        "input_history": json.dumps([{
            "role": "user",
            "content": [{"type": "text", "text": "Alice likes cats"}],
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:9999",
        },
        "speaker_label": "Owner(9999)",
        "speaker_id": "qq:9999",
        "speaker_trust": 1.0,
        "speaker_is_owner": True,
    }
    member = {
        "input_history": json.dumps([{
            "role": "user",
            "content": [{"type": "text", "text": "Alice likes cats"}],
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:1001",
        },
        "speaker_label": "Alice(1001)",
        "speaker_id": "qq:1001",
        "speaker_trust": 0.3,
    }
    prior = {
        "id": fact_id,
        "text": "Alice likes cats",
        "speaker_id": "qq:1001",
        "speaker_label": "Alice-old(1001)",
        "subject_kind": "group_participant",
        "subject_id": "qq:7788:1001",
        "scope": "group_participant:qq:7788:1001",
    }
    batch_reconciled = {
        **prior,
        "speaker_label": "Alice(1001)",
    }
    concurrent_current = dict(batch_reconciled)
    concurrent_current.pop("speaker_id")
    concurrent_current.pop("speaker_label")
    concurrent_current["speaker_provenance_mixed"] = True
    store = SimpleNamespace(
        aload_facts=AsyncMock(side_effect=[[prior], [concurrent_current]]),
        aload_archived_speaker_trust_signal_facts=AsyncMock(return_value=[]),
        extract_facts_batch=AsyncMock(return_value=[
            {"status": "ok", "created": [], "dropped": 0},
            {
                "status": "ok", "created": [],
                "reconciled": [batch_reconciled], "dropped": 0,
            },
        ]),
        apersist_speaker_trust_events=_persist_trust_events,
    )
    store.aevaluate_speaker_trust_events = (
        FactStore.aevaluate_speaker_trust_events.__get__(store, FactStore)
    )

    with patch.object(routes.runtime, "fact_store", store):
        response = await routes.process_scoped_history(
            "Neko", ScopedHistoryRequest(segments=[owner, member]),
        )

    assert response["segments"][0]["trust_events"] == []


@pytest.mark.asyncio
async def test_batch_owner_signal_replays_exact_dedup_provenance_changes():
    from app.memory_server import routes
    from app.memory_server.routes import ScopedHistoryRequest
    from memory.facts import FactStore

    member = {
        "input_history": json.dumps([{
            "role": "user", "content": [{"type": "text", "text": "共同事实"}],
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:2002",
        },
        "speaker_label": "Member(2002)",
        "speaker_id": "qq:2002",
        "speaker_trust": 0.3,
    }
    owner = {
        "input_history": json.dumps([{
            "role": "user", "content": [{"type": "text", "text": "共同事实"}],
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:9999",
        },
        "speaker_label": "Owner(9999)",
        "speaker_id": "qq:9999",
        "speaker_trust": 1.0,
        "speaker_is_owner": True,
    }
    existing = {
        "id": "shared-fact",
        "text": "共同事实",
        "speaker_id": "qq:1001",
        "speaker_trust": 0.8,
        "subject_kind": "group_participant",
        "subject_id": "qq:7788:1001",
        "scope": "group_participant:qq:7788:1001",
    }
    reconciled = dict(existing)
    reconciled.pop("speaker_id")
    reconciled.pop("speaker_trust")
    reconciled["speaker_provenance_mixed"] = True
    store = SimpleNamespace(
        aload_facts=AsyncMock(side_effect=[[existing], [reconciled]]),
        aload_archived_speaker_trust_signal_facts=AsyncMock(return_value=[]),
        extract_facts_batch=AsyncMock(return_value=[
            {
                "status": "ok", "created": [],
                "reconciled": [reconciled], "dropped": 0,
            },
            {"status": "ok", "created": [], "dropped": 0},
        ]),
        apersist_speaker_trust_events=_persist_trust_events,
    )
    store.aevaluate_speaker_trust_events = (
        FactStore.aevaluate_speaker_trust_events.__get__(store, FactStore)
    )

    with patch.object(routes.runtime, "fact_store", store):
        response = await routes.process_scoped_history(
            "Neko", ScopedHistoryRequest(segments=[member, owner]),
        )

    assert response["segments"][1]["trust_events"] == []
    assert response["segments"][0]["reconciled"] == [{"id": "shared-fact"}]


@pytest.mark.asyncio
async def test_batch_reconciliation_keeps_same_id_in_other_scope():
    from app.memory_server import routes
    from app.memory_server.routes import ScopedHistoryRequest
    from memory.facts import FactStore

    target = {
        "id": "shared-fact", "text": "Target fact",
        "speaker_id": "qq:1001", "speaker_trust": 0.8,
        "subject_kind": "group_participant",
        "subject_id": "qq:7788:1001",
        "scope": "group_participant:qq:7788:1001",
    }
    foreign = {
        "id": "shared-fact", "text": "Foreign fact",
        "speaker_id": "qq:2002", "speaker_trust": 0.7,
        "subject_kind": "group_participant",
        "subject_id": "qq:7788:2002",
        "scope": "group_participant:qq:7788:2002",
    }
    reconciled = dict(target)
    reconciled.pop("speaker_id")
    reconciled.pop("speaker_trust")
    reconciled["speaker_provenance_mixed"] = True
    member = {
        "input_history": json.dumps([{
            "role": "user", "content": "Target fact",
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:1001",
        },
        "speaker_label": "Member(1001)",
        "speaker_id": "qq:1001",
    }
    owner = {
        "input_history": json.dumps([{
            "role": "user", "content": "Foreign fact",
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:9999",
        },
        "speaker_label": "Owner(9999)",
        "speaker_id": "qq:9999",
        "speaker_is_owner": True,
    }
    store = SimpleNamespace(
        aload_facts=AsyncMock(side_effect=[
            [target, foreign], [reconciled, foreign],
        ]),
        aload_archived_speaker_trust_signal_facts=AsyncMock(return_value=[]),
        extract_facts_batch=AsyncMock(return_value=[
            {
                "status": "ok", "created": [],
                "reconciled": [reconciled], "dropped": 0,
            },
            {"status": "ok", "created": [], "dropped": 0},
        ]),
        apersist_speaker_trust_events=_persist_trust_events,
    )
    store.aevaluate_speaker_trust_events = (
        FactStore.aevaluate_speaker_trust_events.__get__(store, FactStore)
    )

    with patch.object(routes.runtime, "fact_store", store):
        response = await routes.process_scoped_history(
            "Neko", ScopedHistoryRequest(segments=[member, owner]),
        )

    events = response["segments"][1]["trust_events"]
    assert len(events) == 1
    assert events[0]["source_fact_id"] == "shared-fact"
    assert events[0]["speaker_id"] == "qq:2002"


@pytest.mark.asyncio
async def test_batch_route_revalidates_trust_signals_after_concurrent_forget():
    from app.memory_server import routes
    from app.memory_server.routes import ScopedHistoryRequest
    from memory.facts import FactStore

    prior = {
        "id": "forgotten-fact",
        "text": "Alice likes cats",
        "speaker_id": "qq:1001",
        "subject_kind": "group_participant",
        "subject_id": "qq:7788:1001",
        "scope": "group_participant:qq:7788:1001",
    }
    same_id_other_scope = {
        **prior,
        "subject_id": "qq:8899:1001",
        "scope": "group_participant:qq:8899:1001",
    }
    owner = {
        "input_history": json.dumps([{
            "role": "user",
            "content": [{"type": "text", "text": "Alice likes cats"}],
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:9999",
        },
        "speaker_label": "Owner(9999)",
        "speaker_id": "qq:9999",
        "speaker_trust": 1.0,
        "speaker_is_owner": True,
    }
    follower = {
        "input_history": json.dumps([{
            "role": "user",
            "content": [{"type": "text", "text": "Later statement"}],
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:2002",
        },
        "speaker_label": "Member(2002)",
        "speaker_id": "qq:2002",
        "speaker_trust": 0.3,
        "display_name": "Member",
    }
    active_facts = [prior]

    async def _load_facts(_lanlan_name):
        return list(active_facts)

    async def _stamp_display_name(*_args, **_kwargs):
        active_facts.clear()
        active_facts.append(same_id_other_scope)
        return True

    store = SimpleNamespace(
        aload_facts=_load_facts,
        aload_archived_speaker_trust_signal_facts=AsyncMock(return_value=[]),
        extract_facts_batch=AsyncMock(return_value=[
            {"status": "ok", "created": [], "dropped": 0},
            {"status": "ok", "created": [], "dropped": 0},
        ]),
        apersist_speaker_trust_events=_persist_trust_events,
    )
    store.aevaluate_speaker_trust_events = (
        FactStore.aevaluate_speaker_trust_events.__get__(store, FactStore)
    )
    persona = SimpleNamespace(
        aupdate_subject_display_name=_stamp_display_name,
    )

    with patch.object(routes.runtime, "fact_store", store), patch.object(
        routes.runtime, "persona_manager", persona,
    ):
        response = await routes.process_scoped_history(
            "Neko", ScopedHistoryRequest(segments=[owner, follower]),
        )

    assert response["segments"][0]["trust_events"] == []


@pytest.mark.asyncio
async def test_batch_route_refreshes_concurrently_reconciled_provenance():
    from app.memory_server import routes
    from app.memory_server.routes import ScopedHistoryRequest
    from memory.facts import FactStore

    prior = {
        "id": "reconciled-fact",
        "text": "Alice likes cats",
        "speaker_id": "qq:1001",
        "subject_kind": "group_participant",
        "subject_id": "qq:7788:1001",
        "scope": "group_participant:qq:7788:1001",
    }
    current = {
        **prior,
        "speaker_provenance_mixed": True,
    }
    current.pop("speaker_id")
    owner = {
        "input_history": json.dumps([{
            "role": "user",
            "content": [{"type": "text", "text": "Alice likes cats"}],
        }]),
        "subject": {
            "subject_kind": "group_participant",
            "subject_id": "qq:7788:9999",
        },
        "speaker_label": "Owner(9999)",
        "speaker_id": "qq:9999",
        "speaker_trust": 1.0,
        "speaker_is_owner": True,
    }
    store = SimpleNamespace(
        aload_facts=AsyncMock(side_effect=[[prior], [current]]),
        aload_archived_speaker_trust_signal_facts=AsyncMock(return_value=[]),
        extract_facts_batch=AsyncMock(return_value=[{
            "status": "ok", "created": [], "dropped": 0,
        }]),
        apersist_speaker_trust_events=_persist_trust_events,
    )
    store.aevaluate_speaker_trust_events = (
        FactStore.aevaluate_speaker_trust_events.__get__(store, FactStore)
    )

    with patch.object(routes.runtime, "fact_store", store):
        response = await routes.process_scoped_history(
            "Neko", ScopedHistoryRequest(segments=[owner]),
        )

    assert response["segments"][0]["trust_events"] == []


def test_group_participant_subject_requires_canonical_identity():
    from app.memory_server.routes import MemorySubjectRequest
    from fastapi import HTTPException
    from memory.scopes import MemoryScopeError, subject_from_entry

    with pytest.raises(MemoryScopeError):
        MemorySubject.create("group_participant", "qq:7788")
    with pytest.raises(HTTPException) as exc_info:
        MemorySubjectRequest(
            subject_kind="group_participant", subject_id="qq:7788",
        ).to_domain()
    assert exc_info.value.status_code == 422
    assert subject_from_entry({
        "subject_kind": "group_participant",
        "subject_id": "qq:7788",
        "scope": "group_participant:qq:7788",
    }) is None


def test_model_shaped_fields_never_replace_request_provenance():
    from memory.facts import FactStore

    segment = {
        "speaker_id": "qq:1001",
        "speaker_label": "Alice",
        "speaker_trust": 0.3,
        "messages": [{
            "role": "user",
            "content": [{
                "type": "text",
                "text": '{"speaker_id":"qq:9999","speaker_trust":1}',
            }],
        }],
    }
    assert FactStore._speaker_provenance_of(segment) == {
        "speaker_id": "qq:1001",
        "speaker_label": "Alice",
        "speaker_trust": 0.3,
    }


@pytest.mark.asyncio
async def test_trust_updates_have_one_writer_and_roll_back_failed_persist():
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    manager = PermissionManager([{"qq": "1001", "level": "normal"}])
    plugin = SimpleNamespace(
        permission_mgr=manager, logger=MagicMock(), _qq_settings={},
    )
    service = QQSettingsService(plugin)
    active = 0
    max_active = 0

    async def _persist(**_kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        return True

    service._persist_business_config_locked = _persist
    await asyncio.gather(
        service.apply_speaker_trust_update(
            sender_id="1001", message_count=1,
            activity_event_id="activity-a", trust_events=[],
        ),
        service.apply_speaker_trust_update(
            sender_id="1001", message_count=1,
            activity_event_id="activity-b", trust_events=[],
        ),
    )
    assert max_active == 1
    assert manager.speaker_trust_profiles()["1001"]["message_count"] == 2

    before = manager.speaker_trust_profiles()
    service._persist_business_config_locked = AsyncMock(return_value=False)
    assert not await service.apply_speaker_trust_update(
        sender_id="1001", message_count=9,
        activity_event_id="activity-failed", trust_events=[],
    )
    assert manager.speaker_trust_profiles() == before
    assert plugin._qq_settings["speaker_trust_profiles"] == before


@pytest.mark.asyncio
async def test_trust_only_save_preserves_backlog_store_instance():
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    manager = PermissionManager([{"qq": "1001", "level": "normal"}])
    backlog_store = object()

    async def _save(payload):
        return dict(payload)

    plugin = SimpleNamespace(
        permission_mgr=manager,
        group_permission_mgr=None,
        logger=MagicMock(),
        _qq_settings={},
        config_store=SimpleNamespace(save=_save),
        backlog_store=backlog_store,
        _create_backlog_store_from_settings=MagicMock(),
    )
    service = QQSettingsService(plugin)

    assert await service.apply_speaker_trust_update(
        sender_id="1001",
        message_count=1,
        activity_event_id="preserve-backlog-lock",
        trust_events=[],
    )
    assert plugin.backlog_store is backlog_store
    plugin._create_backlog_store_from_settings.assert_not_called()


@pytest.mark.asyncio
async def test_trust_only_save_does_not_publish_staged_permission_mutations():
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    published_users = [{"qq": "1001", "level": "normal"}]
    manager = PermissionManager(published_users)
    payloads = []

    async def _save(payload):
        payloads.append(json.loads(json.dumps(payload)))
        return dict(payload)

    plugin = SimpleNamespace(
        permission_mgr=manager,
        group_permission_mgr=None,
        logger=MagicMock(),
        _qq_settings={"trusted_users": published_users},
        config_store=SimpleNamespace(save=_save),
        backlog_store=None,
        _create_backlog_store_from_settings=lambda _settings: None,
    )
    service = QQSettingsService(plugin)

    # Dashboard actions mutate the live manager before awaiting session
    # invalidation and entering the settings writer locks.
    assert manager.add_user("2002", "trusted")
    assert await service.apply_speaker_trust_update(
        sender_id="1001", message_count=1,
        activity_event_id="trust-during-dashboard-invalidation",
        trust_events=[],
    )

    assert payloads[0]["trusted_users"] == published_users
    assert plugin._qq_settings["trusted_users"] == published_users
    assert {row["qq"] for row in manager.list_users()} == {"1001", "2002"}

    assert await service.persist_business_config()
    assert {row["qq"] for row in payloads[1]["trusted_users"]} == {
        "1001", "2002",
    }


@pytest.mark.asyncio
async def test_failed_trust_save_restores_published_permission_snapshot():
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    published_users = [{"qq": "1001", "level": "normal"}]
    manager = PermissionManager(published_users)
    payloads = []

    async def _save(payload):
        payloads.append(json.loads(json.dumps(payload)))
        if len(payloads) == 1:
            raise OSError("disk full")
        return dict(payload)

    plugin = SimpleNamespace(
        permission_mgr=manager,
        group_permission_mgr=None,
        logger=MagicMock(),
        _qq_settings={"trusted_users": published_users},
        config_store=SimpleNamespace(save=_save),
        backlog_store=None,
        _create_backlog_store_from_settings=lambda _settings: None,
    )
    service = QQSettingsService(plugin)

    assert manager.add_user("2002", "trusted")
    assert not await service.apply_speaker_trust_update(
        sender_id="1001", message_count=1,
        activity_event_id="failed-trust-during-dashboard-invalidation",
        trust_events=[],
    )
    assert plugin._qq_settings["trusted_users"] == published_users
    assert manager.speaker_trust_profiles() == {}

    assert await service.apply_speaker_trust_update(
        sender_id="1001", message_count=1,
        activity_event_id="retry-after-failed-trust-save",
        trust_events=[],
    )
    assert payloads[1]["trusted_users"] == published_users


@pytest.mark.asyncio
async def test_unpersisted_trust_is_invisible_during_slow_failed_save():
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    manager = PermissionManager([{"qq": "1001", "level": "normal"}])
    plugin = SimpleNamespace(
        permission_mgr=manager, logger=MagicMock(), _qq_settings={},
    )
    service = QQSettingsService(plugin)
    started = asyncio.Event()
    release = asyncio.Event()

    async def _persist(**_kwargs):
        started.set()
        await release.wait()
        return False

    service._persist_business_config_locked = _persist
    before = manager.get_speaker_trust("1001")
    writer = asyncio.create_task(service.apply_speaker_trust_update(
        sender_id="1001", message_count=0,
        activity_event_id="slow-failed-save",
        trust_events=[{
            "kind": "confirmation",
            "speaker_id": "qq:1001",
            "event_id": "confirmation-during-slow-save",
        }],
    ))
    await asyncio.wait_for(started.wait(), timeout=5.0)

    memory_service = QQSessionMemoryService(SimpleNamespace(
        permission_mgr=manager,
    ))
    assert memory_service._speaker_trust_for("1001", "normal") == before

    release.set()
    assert not await writer
    assert memory_service._speaker_trust_for("1001", "normal") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("persisted", [False, True])
async def test_cancelled_trust_writer_waits_for_the_inflight_save(persisted):
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    manager = PermissionManager([{"qq": "1001", "level": "normal"}])
    plugin = SimpleNamespace(
        permission_mgr=manager, logger=MagicMock(), _qq_settings={},
    )
    service = QQSettingsService(plugin)
    started = asyncio.Event()
    release = asyncio.Event()

    async def _persist(**_kwargs):
        started.set()
        await release.wait()
        return persisted

    service._persist_business_config_locked = _persist
    task = asyncio.create_task(service.apply_speaker_trust_update(
        sender_id="1001", message_count=1,
        activity_event_id=f"cancelled-{persisted}", trust_events=[],
    ))
    await asyncio.wait_for(started.wait(), timeout=5.0)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError) as exc_info:
        await task
    assert exc_info.value.speaker_trust_persisted is persisted
    profiles = manager.speaker_trust_profiles()
    if persisted:
        assert profiles["1001"]["message_count"] == 1
    else:
        assert profiles == {}
        assert plugin._qq_settings["speaker_trust_profiles"] == {}


@pytest.mark.asyncio
async def test_repeated_cancellation_keeps_trust_save_shielded():
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    manager = PermissionManager([{"qq": "1001", "level": "normal"}])
    plugin = SimpleNamespace(
        permission_mgr=manager, logger=MagicMock(), _qq_settings={},
    )
    service = QQSettingsService(plugin)
    started = asyncio.Event()
    release = asyncio.Event()

    async def _persist(**_kwargs):
        started.set()
        await release.wait()
        return True

    service._persist_business_config_locked = _persist
    task = asyncio.create_task(service.apply_speaker_trust_update(
        sender_id="1001", message_count=1,
        activity_event_id="cancelled-twice", trust_events=[],
    ))
    await asyncio.wait_for(started.wait(), timeout=5.0)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    release.set()
    with pytest.raises(asyncio.CancelledError) as exc_info:
        await task
    assert exc_info.value.speaker_trust_persisted is True
    assert manager.speaker_trust_profiles()["1001"]["message_count"] == 1


@pytest.mark.asyncio
async def test_trust_writer_is_mutexed_with_dashboard_settings_transaction():
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    manager = PermissionManager([{"qq": "1001", "level": "normal"}])
    service = QQSettingsService(SimpleNamespace(
        permission_mgr=manager, logger=MagicMock(),
    ))
    service._persist_business_config_locked = AsyncMock(return_value=True)
    async with service._consent_transaction_lock:
        task = asyncio.create_task(service.apply_speaker_trust_update(
            sender_id="1001", message_count=1,
            activity_event_id="blocked-by-dashboard", trust_events=[],
        ))
        await asyncio.sleep(0)
        service._persist_business_config_locked.assert_not_awaited()
    assert await task
    service._persist_business_config_locked.assert_awaited_once()


@pytest.mark.asyncio
async def test_trust_writer_resolves_manager_after_dashboard_rebuild():
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    old_manager = PermissionManager([{"qq": "1001", "level": "normal"}])
    plugin = SimpleNamespace(
        permission_mgr=old_manager, logger=MagicMock(), _qq_settings={},
    )
    service = QQSettingsService(plugin)
    service._persist_business_config_locked = AsyncMock(return_value=True)
    async with service._consent_transaction_lock:
        task = asyncio.create_task(service.apply_speaker_trust_update(
            sender_id="1001", message_count=1,
            activity_event_id="after-rebuild", trust_events=[],
        ))
        await asyncio.sleep(0)
        replacement = PermissionManager([{"qq": "1001", "level": "normal"}])
        plugin.permission_mgr = replacement
    assert await task
    assert old_manager.speaker_trust_profiles() == {}
    assert replacement.speaker_trust_profiles()["1001"]["message_count"] == 1


@pytest.mark.asyncio
async def test_direct_settings_save_waits_for_failed_trust_transaction():
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    manager = PermissionManager([{"qq": "1001", "level": "normal"}])
    save_started = asyncio.Event()
    save_release = asyncio.Event()
    payloads = []

    async def _save(payload):
        payloads.append(dict(payload))
        if len(payloads) == 1:
            save_started.set()
            await save_release.wait()
            raise OSError("disk full")
        return dict(payload)

    plugin = SimpleNamespace(
        permission_mgr=manager,
        group_permission_mgr=None,
        logger=MagicMock(),
        _qq_settings={},
        config_store=SimpleNamespace(save=_save),
        _create_backlog_store_from_settings=lambda _settings: None,
    )
    service = QQSettingsService(plugin)
    writer = asyncio.create_task(service.apply_speaker_trust_update(
        sender_id="1001", message_count=1,
        activity_event_id="failed-before-dashboard", trust_events=[],
    ))
    await asyncio.wait_for(save_started.wait(), timeout=5.0)

    dashboard_save = asyncio.create_task(service.persist_business_config())
    await asyncio.sleep(0)
    assert len(payloads) == 1
    save_release.set()

    assert not await writer
    assert await dashboard_save
    assert payloads[1]["speaker_trust_profiles"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize((
    "action_name", "action_args", "setting_key", "initial_value",
    "expected_value",
), [
    (
        "save_prompt_override", ("zh-CN", "identity", "new prompt"),
        "prompt_overrides", {}, {"zh-CN": {"identity": "new prompt"}},
    ),
    (
        "reset_prompt_override", ("zh-CN", "identity"),
        "prompt_overrides", {"zh-CN": {"identity": "old prompt"}}, {},
    ),
    (
        "save_group_prompt", ("7788", "new group prompt"),
        "group_prompts", {}, {"7788": "new group prompt"},
    ),
    (
        "delete_group_prompt", ("7788",),
        "group_prompts", {"7788": "old group prompt"}, {},
    ),
])
async def test_direct_action_mutation_waits_for_trust_writer(
    action_name, action_args, setting_key, initial_value, expected_value,
):
    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    manager = PermissionManager([{"qq": "1001", "level": "normal"}])
    save_started = asyncio.Event()
    save_release = asyncio.Event()
    payloads = []

    async def _save(payload):
        payloads.append(dict(payload))
        if len(payloads) == 1:
            save_started.set()
            await save_release.wait()
        return dict(payload)

    plugin = SimpleNamespace(
        permission_mgr=manager,
        group_permission_mgr=None,
        logger=MagicMock(),
        _qq_settings={
            setting_key: json.loads(json.dumps(initial_value)),
        },
        config_store=SimpleNamespace(save=_save),
        backlog_store=None,
        _create_backlog_store_from_settings=lambda _settings: None,
        session_instruction_service=SimpleNamespace(
            _PROMPT_LAYERS=[{
                "id": "identity", "i18n_key": "identity", "runtime": False,
            }],
            _discard_all_sessions_for_prompt_change=MagicMock(),
        ),
        session_runtime_service=None,
        _emit_log=lambda *_args, **_kwargs: None,
    )
    service = QQSettingsService(plugin)
    plugin.settings_service = service
    action_started = asyncio.Event()
    mutate_business_config = service.mutate_business_config

    async def _observe_action_start(mutation):
        action_started.set()
        return await mutate_business_config(mutation)

    service.mutate_business_config = _observe_action_start
    trust_writer = asyncio.create_task(service.apply_speaker_trust_update(
        sender_id="1001", message_count=1,
        activity_event_id="trust-before-prompt", trust_events=[],
    ))
    await asyncio.wait_for(save_started.wait(), timeout=5.0)

    action = getattr(QQAutoReplyPlugin, action_name)
    action_writer = asyncio.create_task(action(plugin, *action_args))
    await asyncio.wait_for(action_started.wait(), timeout=5.0)
    assert not action_writer.done()
    assert plugin._qq_settings[setting_key] == initial_value

    save_release.set()
    assert await trust_writer
    action_result = await action_writer
    assert action_result.value["persisted"] is True
    assert payloads[-1][setting_key] == expected_value


@pytest.mark.asyncio
@pytest.mark.parametrize(("action_name", "action_args"), [
    ("save_group_prompt", ("7788", "new group prompt")),
    ("delete_group_prompt", ("7788",)),
])
async def test_group_prompt_persist_failure_never_logs_success(
    action_name, action_args,
):
    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    logs: list[tuple[str, str]] = []
    plugin = SimpleNamespace(
        _qq_settings={"group_prompts": {"7788": "old group prompt"}},
        settings_service=None,
        session_runtime_service=None,
        _persist_business_config=AsyncMock(return_value=False),
        _emit_log=lambda level, message: logs.append((level, message)),
    )

    result = await getattr(QQAutoReplyPlugin, action_name)(plugin, *action_args)

    assert result.value["persisted"] is False
    assert not any(level == "INFO" for level, _message in logs)
    assert any(
        level == "WARNING" and "写盘失败" in message
        for level, message in logs
    )


@pytest.mark.asyncio
async def test_all_settings_writers_acquire_both_transaction_locks():
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    service = QQSettingsService(SimpleNamespace())
    service._persist_business_config_locked = AsyncMock(return_value=True)
    service._save_settings_locked = AsyncMock(return_value={})

    async with service._speaker_trust_write_lock:
        direct = asyncio.create_task(service.persist_business_config())
        settings = asyncio.create_task(service.save_settings())
        await asyncio.sleep(0)
        service._persist_business_config_locked.assert_not_awaited()
        service._save_settings_locked.assert_not_awaited()
    assert await direct
    assert await settings == {}

    service._persist_business_config_locked.reset_mock()
    async with service._consent_transaction_lock:
        direct = asyncio.create_task(service.persist_business_config())
        await asyncio.sleep(0)
        service._persist_business_config_locked.assert_not_awaited()
    assert await direct


@pytest.mark.asyncio
async def test_activity_counts_only_the_speaker_not_assistant_replies():
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    settings = SimpleNamespace(apply_speaker_trust_update=AsyncMock(
        return_value=True,
    ))
    service = QQSessionMemoryService(SimpleNamespace(
        settings_service=settings, logger=MagicMock(),
    ))
    await service._apply_speaker_trust_update(
        "1001",
        [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        ],
        [],
    )
    assert settings.apply_speaker_trust_update.await_args.kwargs[
        "message_count"
    ] == 1


@pytest.mark.asyncio
async def test_failed_trust_persist_keeps_the_segment_retryable():
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    settings = SimpleNamespace(apply_speaker_trust_update=AsyncMock(
        return_value=False,
    ))
    logger = MagicMock()
    service = QQSessionMemoryService(SimpleNamespace(
        settings_service=settings, logger=logger,
    ))

    with pytest.raises(
        RuntimeError, match="speaker trust update persistence failed",
    ):
        await service._apply_speaker_trust_update(
            "1001", [{"role": "user", "content": "hello"}],
            [{"event_id": "server-issued"}],
        )
    logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_identical_text_in_distinct_batches_has_distinct_activity_ids():
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    settings = SimpleNamespace(apply_speaker_trust_update=AsyncMock(
        return_value=True,
    ))
    service = QQSessionMemoryService(SimpleNamespace(
        settings_service=settings, logger=MagicMock(),
    ))
    messages = [{"role": "user", "content": "好的"}]
    await service._apply_speaker_trust_update(
        "1001", messages, [], activity_identity="group:a:batch-1",
    )
    await service._apply_speaker_trust_update(
        "1001", messages, [], activity_identity="group:b:batch-2",
    )
    await service._apply_speaker_trust_update(
        "1001", messages, [], activity_identity="group:a:batch-1",
    )
    event_ids = [
        call.kwargs["activity_event_id"]
        for call in settings.apply_speaker_trust_update.await_args_list
    ]
    assert event_ids[0] != event_ids[1]
    assert event_ids[0] == event_ids[2]


@pytest.mark.asyncio
async def test_retry_metadata_does_not_change_stable_activity_id():
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    settings = SimpleNamespace(apply_speaker_trust_update=AsyncMock(
        return_value=True,
    ))
    service = QQSessionMemoryService(SimpleNamespace(
        settings_service=settings, logger=MagicMock(),
    ))
    messages = [{"role": "user", "content": "好的"}]
    await service._apply_speaker_trust_update(
        "1001", messages, [], activity_identity="group:a:batch-1",
    )
    messages[0]["_trust_signal_excluded_fact_identities"] = [[
        "later", "group_participant", "qq:a:2",
        "group_participant:qq:a:2",
    ]]
    await service._apply_speaker_trust_update(
        "1001", messages, [], activity_identity="group:a:batch-1",
    )

    event_ids = [
        call.kwargs["activity_event_id"]
        for call in settings.apply_speaker_trust_update.await_args_list
    ]
    assert event_ids[0] == event_ids[1]


def test_correction_relation_preserves_argument_order():
    assert deterministic_relation("小明喜欢小红", "小红不喜欢小明") is None


@pytest.mark.parametrize("old_text,new_text", [
    ("Alice is smart", "Alice is not smart?"),
    ("小明喜欢猫", "小明不喜欢猫？"),
])
def test_interrogative_observations_never_emit_trust_relations(old_text, new_text):
    assert deterministic_relation(old_text, new_text) is None
    assert deterministic_relation(new_text, new_text) is None


def test_relative_clause_negation_never_emits_correction():
    assert deterministic_relation(
        "A girl who is smart lives in Paris",
        "A girl who is not smart lives in Paris",
    ) is None
    assert deterministic_relation(
        "Alice is smart", "Alice is not smart",
    ) == "correction"


def test_conditional_clause_negations_never_emit_correction():
    assert deterministic_relation(
        "If Alice is smart, Bob smiles",
        "If Alice is not smart, Bob smiles",
    ) is None
    assert deterministic_relation(
        "Alice is smart if Bob smiles",
        "Alice is not smart if Bob smiles",
    ) is None
    assert deterministic_relation(
        "如果小明喜欢猫，他就开心",
        "如果小明不喜欢猫，他就开心",
    ) is None
    assert deterministic_relation(
        "小明喜欢猫，如果天气好",
        "小明不喜欢猫，如果天气好",
    ) is None


@pytest.mark.parametrize("marker", ["Before", "After", "While", "Until"])
def test_temporal_clause_negations_never_emit_correction(marker):
    assert deterministic_relation(
        f"{marker} Alice is smart, Bob waits",
        f"{marker} Alice is not smart, Bob waits",
    ) is None


@pytest.mark.parametrize("marker", [
    "之前", "之后", "之後", "以前", "以后", "以後", "期间", "期間",
])
def test_cjk_temporal_clause_negations_never_emit_correction(marker):
    assert deterministic_relation(
        f"小明喜欢猫{marker}通知我",
        f"小明不喜欢猫{marker}通知我",
    ) is None


def test_postposed_whenever_negation_never_emits_correction():
    assert deterministic_relation(
        "Notify Bob whenever Alice is smart",
        "Notify Bob whenever Alice is not smart",
    ) is None
    assert deterministic_relation(
        "Alice is smart", "Alice is not smart",
    ) == "correction"


@pytest.mark.parametrize("marker", ["Provided", "Assuming", "Supposing"])
def test_bare_conditional_introducers_never_emit_correction(marker):
    assert deterministic_relation(
        f"{marker} Alice is smart, Bob smiles",
        f"{marker} Alice is not smart, Bob smiles",
    ) is None


@pytest.mark.parametrize("marker", [
    "In case", "As long as", "Even if", "Only if", "On condition that",
])
def test_phrase_conditional_introducers_never_emit_correction(marker):
    assert deterministic_relation(
        f"{marker} Alice is smart, notify Bob",
        f"{marker} Alice is not smart, notify Bob",
    ) is None


@pytest.mark.parametrize("marker", ["即使", "除非"])
def test_cjk_conditional_introducers_never_emit_correction(marker):
    assert deterministic_relation(
        f"{marker}小明喜欢猫，也通知我",
        f"{marker}小明不喜欢猫，也通知我",
    ) is None


@pytest.mark.parametrize("conditional_first", [False, True])
def test_independent_conditional_sentence_does_not_hide_correction(
    conditional_first,
):
    conditional = "Bob will arrive if it rains"
    old_assertion = "Alice is home"
    new_assertion = "Alice is not home"
    if conditional_first:
        old_text = f"{conditional}. {old_assertion}"
        new_text = f"{conditional}. {new_assertion}"
    else:
        old_text = f"{old_assertion}. {conditional}"
        new_text = f"{new_assertion}. {conditional}"
    assert deterministic_relation(old_text, new_text) == "correction"


@pytest.mark.parametrize("old_text,new_text", [
    (
        "Alice is smart or Bob is happy",
        "Alice is not smart or Bob is happy",
    ),
    ("小明喜欢猫或者小红开心", "小明不喜欢猫或者小红开心"),
])
def test_disjunctive_negations_never_emit_correction(old_text, new_text):
    assert deterministic_relation(old_text, new_text) is None


def test_or_substring_does_not_disable_asserted_correction():
    assert deterministic_relation(
        "Alice is ordinary", "Alice is not ordinary",
    ) == "correction"


@pytest.mark.parametrize("marker", ["只要", "一旦"])
def test_sufficient_condition_negations_never_emit_correction(marker):
    assert deterministic_relation(
        f"{marker}小明喜欢猫，他就开心",
        f"{marker}小明不喜欢猫，他就开心",
    ) is None


def test_epistemic_modal_negations_never_emit_correction():
    for modal in ("might", "may", "could"):
        assert deterministic_relation(
            f"Alice {modal} attend",
            f"Alice {modal} not attend",
        ) is None
        assert deterministic_relation(
            f"Alice {modal} have been smart",
            f"Alice {modal} have not been smart",
        ) is None
    assert deterministic_relation(
        "Alice will attend", "Alice will not attend",
    ) == "correction"
    assert deterministic_relation(
        "Alice clicked the may button and will attend",
        "Alice clicked the may button and will not attend",
    ) == "correction"
    assert deterministic_relation(
        "Alice might possibly attend", "Alice might possibly not attend",
    ) is None


@pytest.mark.parametrize("marker", ["Maybe", "Perhaps", "Possibly", "Probably"])
def test_english_lexical_uncertainty_never_emits_correction(marker):
    assert deterministic_relation(
        f"{marker} Alice is smart",
        f"{marker} Alice is not smart",
    ) is None
    assert deterministic_relation(
        "Alice is smart", "Alice is not smart",
    ) == "correction"


@pytest.mark.parametrize("marker", ["possible", "probable"])
def test_adjective_led_epistemic_uncertainty_never_emits_correction(marker):
    assert deterministic_relation(
        f"It is {marker} Alice is smart",
        f"It is {marker} Alice is not smart",
    ) is None
    assert deterministic_relation(
        "Alice is smart", "Alice is not smart",
    ) == "correction"


@pytest.mark.parametrize(("positive", "negative"), [
    ("A cat is black", "A cat is not black"),
    ("An owl is awake", "An owl is not awake"),
    ("Some cats are smart", "Some cats are not smart"),
    ("Many cats are smart", "Many cats are not smart"),
    ("Several cats are smart", "Several cats are not smart"),
    ("Two cats are smart", "Two cats are not smart"),
    ("2 cats are smart", "2 cats are not smart"),
    ("有些猫喜欢鱼", "有些猫不喜欢鱼"),
    ("很多猫喜欢鱼", "很多猫不喜欢鱼"),
])
def test_non_universal_quantifiers_never_emit_correction(positive, negative):
    assert deterministic_relation(positive, negative) is None


def test_indefinite_predicate_complement_keeps_definite_subject_correction():
    assert deterministic_relation(
        "Alice is a doctor", "Alice is not a doctor",
    ) == "correction"


@pytest.mark.parametrize("marker", [
    "sometimes", "occasionally", "often", "rarely", "at times",
])
def test_frequency_qualified_negations_never_emit_correction(marker):
    assert deterministic_relation(
        f"Alice is {marker} smart",
        f"Alice is {marker} not smart",
    ) is None
    assert deterministic_relation(
        "Alice is smart", "Alice is not smart",
    ) == "correction"


@pytest.mark.parametrize("marker", [
    "偶尔", "偶爾", "经常", "經常", "时常", "時常", "通常",
])
def test_cjk_frequency_qualified_negations_never_emit_correction(marker):
    assert deterministic_relation(
        f"小明{marker}喜欢猫",
        f"小明{marker}不喜欢猫",
    ) is None


@pytest.mark.parametrize("verb", [
    "said", "reported", "claimed", "believed", "announced", "wrote", "heard",
    "stated", "asserted", "observed", "found",
])
def test_bare_reported_speech_complements_never_emit_correction(verb):
    assert deterministic_relation(
        f"Alice sometimes {verb} Bob is smart",
        f"Alice sometimes {verb} Bob is not smart",
    ) is None


@pytest.mark.parametrize("modal", ["might", "may", "could"])
def test_english_epistemic_modals_reject_cjk_negations(modal):
    assert deterministic_relation(
        f"Alice {modal} 喜欢猫", f"Alice {modal} 不喜欢猫",
    ) is None
    assert deterministic_relation(
        "Alice clicked the may button 喜欢猫",
        "Alice clicked the may button 不喜欢猫",
    ) == "correction"


@pytest.mark.parametrize("marker", ["也许", "或许", "大概", "可能"])
@pytest.mark.parametrize("positive,negative", [
    ("小明喜欢猫", "小明不喜欢猫"),
    ("小明住上海", "小明不住上海"),
])
def test_cjk_epistemic_negations_never_emit_correction(
    marker, positive, negative,
):
    assert deterministic_relation(
        f"{marker}{positive}", f"{marker}{negative}",
    ) is None


def test_cjk_epistemic_marker_rejects_only_asserted_english_negation():
    from memory.speaker_trust import _has_cjk_epistemic_negation

    assert deterministic_relation(
        "可能 Alice will attend", "可能 Alice will not attend",
    ) is None
    assert _has_cjk_epistemic_negation("可能 Alice will not attend") is True
    assert _has_cjk_epistemic_negation("可能 Alice clicked the not operator") is False


@pytest.mark.parametrize("verb", [
    "说", "表示", "认为", "宣布", "写道", "指出", "承认", "提到",
])
def test_cjk_reported_negations_never_emit_correction(verb):
    assert deterministic_relation(
        f"小明{verb}小红喜欢猫",
        f"小明{verb}小红不喜欢猫",
    ) is None


@pytest.mark.asyncio
async def test_malformed_participant_scope_cannot_emit_trust_events():
    from memory.facts import FactStore

    subject = MemorySubject.group_participant("qq", "7788", "9999")
    store = object.__new__(FactStore)
    store.aload_facts = AsyncMock(return_value=[{
        "id": "missing-scope",
        "text": "我喜欢猫",
        "speaker_id": "qq:1001",
        "subject_kind": "group_participant",
        "subject_id": "qq:7788:1001",
    }])
    events = await store.aevaluate_speaker_trust_events(
        "Neko", [{"role": "user", "content": "我不喜欢猫"}],
        subject=subject,
        speaker_provenance={"speaker_id": "qq:9999", "speaker_trust": 1.0},
        speaker_is_owner=True,
    )
    assert events == []


@pytest.mark.asyncio
async def test_dashboard_reload_waits_before_reading_trust_config():
    from plugin.plugins.qq_auto_reply.dashboard_service import QQDashboardService
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    stored = {
        "trusted_users": [{"qq": "1001", "level": "normal"}],
        "trusted_groups": [],
        "speaker_trust_profiles": {},
    }
    manager = PermissionManager(stored["trusted_users"])
    persist_started = asyncio.Event()
    persist_release = asyncio.Event()
    load_called = asyncio.Event()
    plugin = SimpleNamespace(
        permission_mgr=manager, group_permission_mgr=None,
        logger=MagicMock(), _qq_settings={},
        config_store=SimpleNamespace(exists=AsyncMock(return_value=True)),
        _refresh_admin_qq=lambda: None,
    )
    settings = QQSettingsService(plugin)
    plugin.settings_service = settings

    async def _persist(**_kwargs):
        persist_started.set()
        await persist_release.wait()
        stored["speaker_trust_profiles"] = dict(
            settings._staged_speaker_trust_profiles
        )
        return True

    async def _load():
        load_called.set()
        return {
            **stored,
            "speaker_trust_profiles": dict(stored["speaker_trust_profiles"]),
        }

    settings._persist_business_config_locked = _persist
    settings.load_business_config = _load
    settings.apply_runtime_settings = MagicMock()
    dashboard = QQDashboardService(plugin)
    dashboard.build_dashboard_state = AsyncMock(return_value={})

    writer = asyncio.create_task(settings.apply_speaker_trust_update(
        sender_id="1001", message_count=1,
        activity_event_id="reload-race", trust_events=[],
    ))
    await asyncio.wait_for(persist_started.wait(), timeout=5.0)
    reload_task = asyncio.create_task(dashboard.init_config())
    await asyncio.sleep(0)
    assert not load_called.is_set()
    persist_release.set()
    assert await writer
    await reload_task
    assert plugin.permission_mgr.speaker_trust_profiles()["1001"][
        "message_count"
    ] == 1
