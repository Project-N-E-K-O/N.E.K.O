from __future__ import annotations

import asyncio

import pytest

from plugin.plugins.neko_live.modules.live_support_events import scheduler as scheduler_module
from plugin.plugins.neko_live.modules.live_support_events.scheduler import (
    SupportEventScheduler,
    SupportPriority,
    _DispatchTask,
    classify_support_priority,
)


class _Audit:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, op, message="", level="info", detail=None) -> None:
        self.records.append({"op": op, "message": message, "level": level, "detail": detail or {}})


class _ExplodingAudit:
    def record(self, *_args, **_kwargs) -> None:
        raise RuntimeError("audit unavailable")


def _payload(event_id: str, *, value: int = 0, coin_type: str = "silver") -> dict:
    return {
        "event_type": "gift",
        "uid": "viewer-1",
        "room_ref": "42",
        "gift_name": "Heart",
        "gift_value": value,
        "coin_type": coin_type,
        "support_verified": True,
        "support_evidence": "bilibili_typed_command",
        "provider_event_id": event_id,
        "provider_event_type": "SEND_GIFT",
    }


def test_classify_support_priority_uses_event_type_coin_type_and_value():
    assert classify_support_priority({"event_type": "super_chat"}) is SupportPriority.MILESTONE
    assert classify_support_priority({"event_type": "guard"}) is SupportPriority.MILESTONE
    assert classify_support_priority(
        {"event_type": "gift", "coin_type": "gold", "gift_value": 10_000}
    ) is SupportPriority.HIGH
    assert classify_support_priority(
        {"event_type": "gift", "coin_type": "gold", "gift_value": 1_000}
    ) is SupportPriority.MEDIUM
    assert classify_support_priority(
        {"event_type": "gift", "coin_type": "silver", "gift_value": 999_999}
    ) is SupportPriority.LIGHT


@pytest.mark.asyncio
async def test_queue_limit_is_clamped_to_safe_maximum():
    async def dispatch(_payload: dict) -> None:
        return None

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=10_000)

    assert scheduler.status()["queue_limit"] == 100
    await scheduler.close()


@pytest.mark.asyncio
async def test_runtime_queue_limit_decrease_keeps_accepted_work_and_limits_new_admission():
    dispatched: list[str] = []
    active_started = asyncio.Event()
    release_active = asyncio.Event()

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload["provider_event_id"])
        if payload["provider_event_id"] == "active":
            active_started.set()
            await release_active.wait()

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=3)
    assert scheduler.submit(_payload("active")) is True
    await active_started.wait()
    assert scheduler.submit(_payload("accepted-1")) is True
    assert scheduler.submit(_payload("accepted-2")) is True
    assert scheduler.submit(_payload("accepted-3")) is True

    assert scheduler.update_queue_limit(1) == 1
    assert scheduler.status()["queue_limit"] == 1
    assert scheduler.status()["pending_count"] == 3
    assert scheduler.submit(_payload("rejected-after-decrease")) is False
    assert scheduler.status()["pending_count"] == 3

    release_active.set()
    await scheduler.wait_idle()

    assert dispatched == ["active", "accepted-1", "accepted-2", "accepted-3"]
    await scheduler.close()


@pytest.mark.asyncio
async def test_queue_limit_decrease_keeps_admission_open_while_retired_tasks_drain():
    release_retired = asyncio.Event()
    dispatched: list[str] = []

    async def retire() -> None:
        await release_retired.wait()

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload["provider_event_id"])

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=3)
    retired = [asyncio.create_task(retire()) for _ in range(2)]
    for task in retired:
        scheduler._retire_task(task)

    assert scheduler.update_queue_limit(1) == 1
    assert scheduler.submit(_payload("accepted-while-retiring")) is True

    release_retired.set()
    await scheduler.wait_idle()
    assert dispatched == ["accepted-while-retiring"]
    await scheduler.close()


@pytest.mark.asyncio
async def test_finalized_combo_tombstones_use_bounded_ten_minute_default():
    async def dispatch(_payload: dict) -> None:
        return None

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=5)

    assert scheduler.status()["finalized_combo_seconds"] == 600.0
    assert scheduler.status()["finalized_combo_limit"] == 4096
    assert scheduler.status()["processed_id_seconds"] == 600.0
    assert scheduler.status()["processed_id_limit"] == 4096
    await scheduler.close()


@pytest.mark.asyncio
async def test_pending_higher_priority_dispatches_before_pending_light_event():
    dispatched: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload["provider_event_id"])
        if payload["provider_event_id"] == "active":
            first_started.set()
            await release_first.wait()

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=5)
    scheduler.submit(_payload("active"))
    await asyncio.wait_for(first_started.wait(), timeout=1.0)
    scheduler.submit(_payload("light"))
    scheduler.submit(_payload("high", value=10_000, coin_type="gold"))
    release_first.set()

    await scheduler.wait_idle()

    assert dispatched == ["active", "high", "light"]
    await scheduler.close()


@pytest.mark.asyncio
async def test_equal_priorities_preserve_submission_order():
    dispatched: list[str] = []

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload["provider_event_id"])

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=5)
    scheduler.submit(_payload("one", value=1_000, coin_type="gold"))
    scheduler.submit(_payload("two", value=1_000, coin_type="gold"))
    scheduler.submit(_payload("three", value=1_000, coin_type="gold"))

    await scheduler.wait_idle()

    assert dispatched == ["one", "two", "three"]
    await scheduler.close()


def _combo_payload(event_id: str, count: int, *, combo_end: bool = False) -> dict:
    return {
        **_payload(event_id, value=1_000, coin_type="gold"),
        "provider_event_type": "COMBO_SEND",
        "combo_id": "combo-1",
        "combo_count": count,
        "combo_end": combo_end,
        "gift_count": count,
    }


@pytest.mark.asyncio
async def test_combo_updates_dispatch_once_at_explicit_end_with_maximum_count():
    dispatched: list[dict] = []

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload)

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=5)
    scheduler.submit(_combo_payload("evt-1", 1))
    scheduler.submit(_combo_payload("evt-2", 3))
    await asyncio.sleep(0)
    assert dispatched == []

    scheduler.submit(_combo_payload("evt-3", 2, combo_end=True))
    await scheduler.wait_idle()

    assert len(dispatched) == 1
    assert dispatched[0]["gift_count"] == 3
    assert dispatched[0]["combo_count"] == 3
    await scheduler.close()


@pytest.mark.asyncio
async def test_combo_finalizes_after_idle_timeout():
    dispatched: list[dict] = []

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload)

    scheduler = SupportEventScheduler(
        dispatch=dispatch,
        audit=_Audit(),
        queue_limit=5,
        combo_idle_seconds=0.01,
    )
    scheduler.submit(_combo_payload("evt-1", 4))

    await scheduler.wait_idle()

    assert [item["gift_count"] for item in dispatched] == [4]
    await scheduler.close()


@pytest.mark.asyncio
async def test_combo_late_smaller_update_does_not_reduce_accumulated_count():
    dispatched: list[dict] = []

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload)

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=5)
    scheduler.submit(_combo_payload("evt-1", 8))
    scheduler.submit(_combo_payload("evt-2", 3, combo_end=True))

    await scheduler.wait_idle()

    assert dispatched[0]["gift_count"] == 8
    await scheduler.close()


@pytest.mark.asyncio
async def test_combo_identity_conflict_fails_closed_without_overwriting_first_packet():
    dispatched: list[dict] = []

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload)

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=5)
    first = _combo_payload("evt-1", 1)
    conflicting = _combo_payload("evt-2", 9, combo_end=True)
    conflicting["gift_name"] = "Different Gift"
    conflicting["coin_type"] = "silver"
    valid_end = _combo_payload("evt-3", 3, combo_end=True)

    assert scheduler.submit(first) is True
    assert scheduler.submit(conflicting) is False
    assert scheduler.submit(valid_end) is True
    await scheduler.wait_idle()

    assert len(dispatched) == 1
    assert dispatched[0]["gift_name"] == "Heart"
    assert dispatched[0]["coin_type"] == "gold"
    assert dispatched[0]["combo_count"] == 3
    await scheduler.close()


@pytest.mark.asyncio
async def test_combo_conflict_remains_rejected_when_audit_write_fails():
    dispatched: list[dict] = []

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload)

    scheduler = SupportEventScheduler(
        dispatch=dispatch,
        audit=_ExplodingAudit(),
        queue_limit=5,
    )
    first = _combo_payload("evt-1", 1)
    conflicting = _combo_payload("evt-2", 2)
    conflicting["gift_name"] = "Different Gift"

    assert scheduler.submit(first) is True
    assert scheduler.submit(conflicting) is False
    assert scheduler.submit(_combo_payload("evt-3", 3, combo_end=True)) is True
    await scheduler.wait_idle()

    assert len(dispatched) == 1
    assert dispatched[0]["combo_count"] == 3
    await scheduler.close()


@pytest.mark.asyncio
async def test_same_provider_event_id_can_advance_active_combo():
    dispatched: list[dict] = []

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload)

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=5)
    assert scheduler.submit(_combo_payload("evt-1", 1)) is True
    assert scheduler.submit(_combo_payload("evt-1", 4, combo_end=True)) is True

    await scheduler.wait_idle()

    assert len(dispatched) == 1
    assert dispatched[0]["combo_count"] == 4
    await scheduler.close()


@pytest.mark.asyncio
async def test_late_combo_end_after_idle_does_not_dispatch_twice():
    dispatched: list[dict] = []

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload)

    scheduler = SupportEventScheduler(
        dispatch=dispatch,
        audit=_Audit(),
        queue_limit=5,
        combo_idle_seconds=0.01,
    )
    assert scheduler.submit(_combo_payload("evt-1", 3)) is True
    await scheduler.wait_idle()
    assert scheduler.submit(_combo_payload("evt-2", 3, combo_end=True)) is False
    await scheduler.wait_idle()

    assert len(dispatched) == 1
    assert dispatched[0]["combo_count"] == 3
    await scheduler.close()


@pytest.mark.asyncio
async def test_distinct_combo_flood_stays_within_active_combo_limit():
    async def dispatch(_payload: dict) -> None:
        return None

    scheduler = SupportEventScheduler(
        dispatch=dispatch,
        audit=_Audit(),
        queue_limit=2,
        combo_idle_seconds=60,
    )
    first = _combo_payload("evt-1", 1)
    second = _combo_payload("evt-2", 1)
    third = _combo_payload("evt-3", 1)
    first["combo_id"] = "combo-1"
    second["combo_id"] = "combo-2"
    third["combo_id"] = "combo-3"

    assert scheduler.submit(first) is True
    assert scheduler.submit(second) is True
    assert scheduler.submit(third) is False
    assert scheduler.status()["active_combo_count"] == 2
    assert scheduler.status()["dropped_count"] == 1
    await scheduler.close()


@pytest.mark.asyncio
async def test_repeated_combo_updates_keep_one_timer_task():
    async def dispatch(_payload: dict) -> None:
        return None

    scheduler = SupportEventScheduler(
        dispatch=dispatch,
        audit=_Audit(),
        queue_limit=8,
        combo_idle_seconds=60,
    )
    for count in range(1, 10_001):
        assert scheduler.submit(_combo_payload("evt-1", count)) is True

    assert scheduler.status()["active_combo_count"] == 1
    assert scheduler.status()["active_combo_task_count"] == 1
    await scheduler.close()


@pytest.mark.asyncio
async def test_reset_cancels_open_combo_without_dispatching_it():
    dispatched: list[dict] = []

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload)

    scheduler = SupportEventScheduler(
        dispatch=dispatch,
        audit=_Audit(),
        queue_limit=5,
        combo_idle_seconds=0.01,
    )
    scheduler.submit(_combo_payload("evt-1", 4))
    scheduler.reset()
    await asyncio.sleep(0.02)

    assert dispatched == []
    await scheduler.close()


@pytest.mark.asyncio
async def test_queue_pressure_evicts_light_before_medium_or_high():
    dispatched: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def dispatch(payload: dict) -> None:
        event_id = payload["provider_event_id"]
        dispatched.append(event_id)
        if event_id == "active":
            first_started.set()
            await release_first.wait()

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=2)
    scheduler.submit(_payload("active"))
    await first_started.wait()
    scheduler.submit(_payload("light-1"))
    scheduler.submit(_payload("light-2"))
    scheduler.submit(_payload("medium", value=1_000, coin_type="gold"))
    scheduler.submit(_payload("high", value=10_000, coin_type="gold"))
    release_first.set()

    await scheduler.wait_idle()

    assert dispatched == ["active", "high", "medium"]
    await scheduler.close()


@pytest.mark.asyncio
async def test_full_high_priority_queue_rejects_new_high_without_exceeding_limit():
    dispatched: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def dispatch(payload: dict) -> None:
        event_id = payload["provider_event_id"]
        dispatched.append(event_id)
        if event_id == "active":
            first_started.set()
            await release_first.wait()

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=1)
    scheduler.submit(_payload("active"))
    await first_started.wait()
    assert scheduler.submit(_payload("high-1", value=10_000, coin_type="gold")) is True
    assert scheduler.submit(_payload("high-2", value=20_000, coin_type="gold")) is False
    assert scheduler.status()["pending_count"] == 1
    release_first.set()

    await scheduler.wait_idle()

    assert dispatched == ["active", "high-1"]
    assert scheduler.status()["overflow_count"] == 1
    await scheduler.close()


@pytest.mark.asyncio
async def test_milestone_evicts_pending_high_when_queue_is_full():
    dispatched: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def dispatch(payload: dict) -> None:
        event_id = payload["provider_event_id"]
        dispatched.append(event_id)
        if event_id == "active":
            first_started.set()
            await release_first.wait()

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=1)
    scheduler.submit(_payload("active"))
    await first_started.wait()
    assert scheduler.submit(_payload("high", value=10_000, coin_type="gold")) is True
    milestone = _payload("milestone")
    milestone["event_type"] = "super_chat"

    try:
        assert scheduler.submit(milestone) is True
        assert scheduler.status()["pending_count"] == 1
        release_first.set()
        await scheduler.wait_idle()

        assert dispatched == ["active", "milestone"]
    finally:
        release_first.set()
        await scheduler.close()


@pytest.mark.asyncio
async def test_evicted_pending_event_releases_provider_id_for_retry():
    dispatched: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def dispatch(payload: dict) -> None:
        event_id = payload["provider_event_id"]
        dispatched.append(event_id)
        if event_id == "active":
            first_started.set()
            await release_first.wait()

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=1)
    scheduler.submit(_payload("active"))
    await first_started.wait()

    try:
        assert scheduler.submit(_payload("evicted")) is True
        assert scheduler.submit(_payload("high", value=10_000, coin_type="gold")) is True
        release_first.set()
        await scheduler.wait_idle()

        assert scheduler.submit(_payload("evicted")) is True
        await scheduler.wait_idle()
        assert dispatched == ["active", "high", "evicted"]
    finally:
        release_first.set()
        await scheduler.close()


@pytest.mark.asyncio
async def test_evicted_pending_combo_releases_tombstone_for_retry():
    dispatched: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def dispatch(payload: dict) -> None:
        event_id = payload["provider_event_id"]
        dispatched.append(event_id)
        if event_id == "active":
            first_started.set()
            await release_first.wait()

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=1)
    scheduler.submit(_payload("active"))
    await first_started.wait()
    combo_end = _combo_payload("combo", 3, combo_end=True)
    milestone = _payload("milestone")
    milestone["event_type"] = "guard"

    try:
        assert scheduler.submit(combo_end) is True
        assert scheduler.submit(milestone) is True
        release_first.set()
        await scheduler.wait_idle()

        assert scheduler.submit(combo_end) is True
        await scheduler.wait_idle()
        assert dispatched == ["active", "milestone", "combo"]
    finally:
        release_first.set()
        await scheduler.close()


@pytest.mark.asyncio
async def test_capacity_rejection_does_not_consume_provider_event_id():
    dispatched: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def dispatch(payload: dict) -> None:
        event_id = payload["provider_event_id"]
        dispatched.append(event_id)
        if event_id == "active":
            first_started.set()
            await release_first.wait()

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=1)
    scheduler.submit(_payload("active"))
    await first_started.wait()
    scheduler.submit(_payload("high-1", value=10_000, coin_type="gold"))
    assert scheduler.submit(_payload("retry-me", value=20_000, coin_type="gold")) is False

    release_first.set()
    await scheduler.wait_idle()
    assert scheduler.submit(_payload("retry-me", value=20_000, coin_type="gold")) is True
    await scheduler.wait_idle()

    assert dispatched == ["active", "high-1", "retry-me"]
    await scheduler.close()


@pytest.mark.asyncio
async def test_rejected_combo_end_is_not_tombstoned_and_can_retry():
    dispatched: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def dispatch(payload: dict) -> None:
        event_id = payload["provider_event_id"]
        dispatched.append(event_id)
        if event_id == "active":
            first_started.set()
            await release_first.wait()

    scheduler = SupportEventScheduler(
        dispatch=dispatch,
        audit=_Audit(),
        queue_limit=1,
        combo_idle_seconds=60,
    )
    scheduler.submit(_payload("active"))
    await first_started.wait()
    milestone = _payload("milestone")
    milestone["event_type"] = "super_chat"
    assert scheduler.submit(milestone) is True
    combo_end = _combo_payload("combo-end", 3, combo_end=True)

    try:
        assert scheduler.submit(combo_end) is False
        assert scheduler.status()["finalized_combo_count"] == 0

        release_first.set()
        await scheduler.wait_idle()
        assert scheduler.submit(combo_end) is True
        await scheduler.wait_idle()

        assert dispatched == ["active", "milestone", "combo-end"]
    finally:
        release_first.set()
        await scheduler.close()


@pytest.mark.asyncio
async def test_rejected_idle_combo_is_not_tombstoned_and_can_retry():
    dispatched: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def dispatch(payload: dict) -> None:
        event_id = payload["provider_event_id"]
        dispatched.append(event_id)
        if event_id == "active":
            first_started.set()
            await release_first.wait()

    scheduler = SupportEventScheduler(
        dispatch=dispatch,
        audit=_Audit(),
        queue_limit=1,
        combo_idle_seconds=0.01,
    )
    scheduler.submit(_payload("active"))
    await first_started.wait()
    milestone = _payload("milestone")
    milestone["event_type"] = "super_chat"
    assert scheduler.submit(milestone) is True
    assert scheduler.submit(_combo_payload("combo-start", 2)) is True

    try:
        await asyncio.sleep(0.03)
        assert scheduler.status()["active_combo_count"] == 0
        assert scheduler.status()["finalized_combo_count"] == 0

        release_first.set()
        await scheduler.wait_idle()
        assert scheduler.submit(_combo_payload("combo-retry", 3, combo_end=True)) is True
        await scheduler.wait_idle()

        assert dispatched == ["active", "milestone", "combo-retry"]
    finally:
        release_first.set()
        await scheduler.close()


@pytest.mark.asyncio
async def test_light_queue_pressure_aggregates_without_inventing_total_value():
    dispatched: list[dict] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload)
        if payload.get("provider_event_id") == "active":
            first_started.set()
            await release_first.wait()

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=1)
    scheduler.submit(_payload("active"))
    await first_started.wait()
    first = _payload("light-1")
    second = _payload("light-2")
    first.pop("provider_event_id")
    second.pop("provider_event_id")
    assert scheduler.submit(first) is True
    assert scheduler.submit(second) is True
    release_first.set()

    await scheduler.wait_idle()

    aggregate = dispatched[1]
    assert aggregate["aggregated_event_count"] == 2
    assert "provider_event_id" not in aggregate
    assert aggregate.get("gift_value", 0) == 0
    await scheduler.close()


@pytest.mark.asyncio
async def test_identified_light_events_do_not_aggregate_and_remain_retryable_after_eviction():
    dispatched: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def dispatch(payload: dict) -> None:
        event_id = payload["provider_event_id"]
        dispatched.append(event_id)
        if event_id == "active":
            first_started.set()
            await release_first.wait()

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=1)
    scheduler.submit(_payload("active"))
    await first_started.wait()

    try:
        assert scheduler.submit(_payload("light-1")) is True
        assert scheduler.submit(_payload("light-2")) is False
        assert scheduler.submit(_payload("high", value=10_000, coin_type="gold")) is True
        release_first.set()
        await scheduler.wait_idle()

        assert scheduler.submit(_payload("light-1")) is True
        await scheduler.wait_idle()
        assert scheduler.submit(_payload("light-2")) is True
        await scheduler.wait_idle()
        assert dispatched == ["active", "high", "light-1", "light-2"]
    finally:
        release_first.set()
        await scheduler.close()


@pytest.mark.asyncio
async def test_light_queue_pressure_does_not_merge_different_viewers():
    dispatched: list[dict] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload)
        if payload.get("provider_event_id") == "active":
            first_started.set()
            await release_first.wait()

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=1)
    scheduler.submit(_payload("active"))
    await first_started.wait()
    first = _payload("light-1")
    second = _payload("light-2")
    second["uid"] = "viewer-2"

    try:
        assert scheduler.submit(first) is True
        assert scheduler.submit(second) is False
        release_first.set()
        await scheduler.wait_idle()

        queued = dispatched[1]
        assert queued["uid"] == "viewer-1"
        assert queued["provider_event_id"] == "light-1"
        assert "aggregated_event_count" not in queued
    finally:
        release_first.set()
        await scheduler.close()


@pytest.mark.asyncio
async def test_provider_event_id_deduplicates_only_the_same_delivery():
    dispatched: list[str] = []

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload["provider_event_id"])

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=5)
    scheduler.submit(_payload("evt-1"))
    scheduler.submit(_payload("evt-1"))
    scheduler.submit(_payload("evt-2"))

    await scheduler.wait_idle()

    assert dispatched == ["evt-1", "evt-2"]
    await scheduler.close()


@pytest.mark.asyncio
async def test_provider_event_id_can_be_accepted_after_bounded_dedupe_window(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_clock,
):
    clock = [100.0]
    patch_module_clock(
        monkeypatch,
        scheduler_module,
        monotonic=lambda: clock[0],
    )
    dispatched: list[str] = []

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload["provider_event_id"])

    scheduler = SupportEventScheduler(
        dispatch=dispatch,
        audit=_Audit(),
        queue_limit=5,
        processed_id_seconds=10.0,
    )
    assert scheduler.submit(_payload("evt-1")) is True
    await scheduler.wait_idle()
    assert scheduler.submit(_payload("evt-1")) is False

    clock[0] += 11.0
    assert scheduler.submit(_payload("evt-1")) is True
    await scheduler.wait_idle()

    assert dispatched == ["evt-1", "evt-1"]
    await scheduler.close()


@pytest.mark.asyncio
async def test_provider_event_id_history_evicts_oldest_at_configured_limit():
    async def dispatch(_payload: dict) -> None:
        return None

    scheduler = SupportEventScheduler(
        dispatch=dispatch,
        audit=_Audit(),
        queue_limit=1,
        processed_id_limit=3,
    )
    for index in range(4):
        scheduler._remember_event_id(f"evt-{index}")

    assert list(scheduler._processed_ids) == ["evt-1", "evt-2", "evt-3"]
    assert scheduler.status()["processed_id_count"] == 3
    await scheduler.close()


@pytest.mark.asyncio
async def test_provider_event_dedupe_retains_more_than_legacy_2048_entries():
    async def dispatch(_payload: dict) -> None:
        return None

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=5)
    for index in range(2050):
        assert scheduler.submit(_payload(f"evt-{index}")) is True
        await scheduler.wait_idle()

    assert scheduler.submit(_payload("evt-0")) is False
    assert scheduler.status()["processed_id_count"] == 2050
    await scheduler.close()


@pytest.mark.asyncio
async def test_dispatch_failure_audits_without_duplicate_retry_and_continues():
    audit = _Audit()
    attempts: dict[str, int] = {}
    dispatched: list[str] = []

    async def dispatch(payload: dict) -> None:
        event_id = payload["provider_event_id"]
        attempts[event_id] = attempts.get(event_id, 0) + 1
        if event_id == "broken":
            raise RuntimeError("boom")
        dispatched.append(event_id)

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=audit, queue_limit=5)
    scheduler.submit(_payload("broken"))
    scheduler.submit(_payload("healthy"))

    await scheduler.wait_idle()

    assert attempts == {"broken": 1, "healthy": 1}
    assert dispatched == ["healthy"]
    assert [record["op"] for record in audit.records] == [
        "support.dispatch_failed",
        "support.dispatch_submission_finalized",
        "support.dispatch_submission_finalized",
    ]
    failed, broken_finished, healthy_finished = audit.records
    assert failed["detail"]["task_id"] == broken_finished["detail"]["task_id"]
    assert broken_finished["detail"]["classification"] == "current"
    assert broken_finished["detail"]["outcome"] == "failed"
    assert healthy_finished["detail"]["classification"] == "current"
    assert healthy_finished["detail"]["outcome"] == "submitted"
    assert scheduler.status()["current_dispatch_count"] == 0
    await scheduler.close()


@pytest.mark.asyncio
async def test_dispatch_tracks_bounded_pipeline_result_status_without_payload_data():
    audit = _Audit()

    class _Result:
        def __init__(self, status: str, private_output: str) -> None:
            self.status = status
            self.output = private_output

    results = {
        "dry": _Result("dry_run", "private dry-run output"),
        "skip": _Result("skipped", "private skipped output"),
        "invalid": _Result("invented", "private invalid output"),
    }

    async def dispatch(payload: dict):
        return results[payload["provider_event_id"]]

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=audit, queue_limit=5)
    for event_id in results:
        assert scheduler.submit(_payload(event_id)) is True
    await scheduler.wait_idle()

    status = scheduler.status()
    assert status["pipeline_result_dry_run_count"] == 1
    assert status["pipeline_result_skipped_count"] == 1
    assert status["pipeline_result_unknown_count"] == 1
    assert status["pipeline_result_pushed_count"] == 0
    history_text = repr(scheduler._dispatched_history)
    audit_text = repr(audit.records)
    assert "private dry-run output" not in history_text
    assert "private skipped output" not in history_text
    assert "private invalid output" not in history_text
    assert "private dry-run output" not in audit_text
    assert "private skipped output" not in audit_text
    assert "private invalid output" not in audit_text
    assert all(
        record["detail"]["pipeline_result_status"] in {"dry_run", "skipped", "unknown"}
        for record in audit.records
        if record["op"] == "support.dispatch_submission_finalized"
    )
    await scheduler.close()


@pytest.mark.asyncio
async def test_dispatch_failure_does_not_escape_when_audit_write_fails():
    attempts = 0

    async def dispatch(_payload: dict) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("boom")

    scheduler = SupportEventScheduler(
        dispatch=dispatch,
        audit=_ExplodingAudit(),
        queue_limit=5,
    )

    await scheduler._dispatch_once(_payload("broken"))

    assert attempts == 1
    await scheduler.close()


@pytest.mark.asyncio
async def test_concurrent_claims_have_exactly_one_current_owner():
    async def dispatch(_payload: dict) -> None:
        return None

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=5)
    first = _DispatchTask(
        task_id="support_first",
        payload=_payload("first"),
        priority=SupportPriority.LIGHT,
        sequence=1,
        generation=scheduler._dispatch_generation,
    )
    second = _DispatchTask(
        task_id="support_second",
        payload=_payload("second"),
        priority=SupportPriority.LIGHT,
        sequence=2,
        generation=scheduler._dispatch_generation,
    )

    claimed = await asyncio.gather(
        scheduler._try_claim_dispatch(first),
        scheduler._try_claim_dispatch(second),
    )

    assert claimed.count(True) == 1
    assert claimed.count(False) == 1
    owner = first if claimed[0] else second
    assert scheduler._current_dispatch is owner
    assert scheduler.status()["current_dispatch_count"] == 1
    assert await scheduler._finish_dispatch(owner, outcome="cancelled") == "current"
    assert scheduler.status()["current_dispatch_count"] == 0
    await scheduler.close()


@pytest.mark.asyncio
async def test_claim_conflict_resumes_queue_after_current_owner_finishes():
    audit = _Audit()
    dispatched: list[str] = []

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload["provider_event_id"])

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=audit, queue_limit=5)
    owner = _DispatchTask(
        task_id="support_manual_owner",
        payload=_payload("owner"),
        priority=SupportPriority.LIGHT,
        sequence=1,
        generation=scheduler._dispatch_generation,
    )
    assert await scheduler._try_claim_dispatch(owner) is True
    assert scheduler.submit(_payload("queued")) is True
    assert scheduler._worker is None

    conflicting_worker = asyncio.create_task(scheduler._run())
    scheduler._worker = conflicting_worker
    await conflicting_worker

    assert scheduler.status()["pending_count"] == 1
    assert dispatched == []
    assert any(
        record["op"] == "support.dispatch_claim_conflict"
        for record in audit.records
    )

    assert await scheduler._finish_dispatch(owner, outcome="submitted") == "current"
    await scheduler.wait_idle()
    assert dispatched == ["queued"]
    await scheduler.close()


@pytest.mark.asyncio
async def test_old_dispatch_completion_is_retroactive_and_cannot_release_new_owner():
    audit = _Audit()
    old_started = asyncio.Event()
    old_cancelled = asyncio.Event()
    release_old = asyncio.Event()
    new_started = asyncio.Event()
    release_new = asyncio.Event()

    async def dispatch(payload: dict) -> None:
        if payload["provider_event_id"] == "old":
            old_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                old_cancelled.set()
                await release_old.wait()
                return
        new_started.set()
        await release_new.wait()

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=audit, queue_limit=5)
    scheduler.submit(_payload("old"))
    await old_started.wait()
    old_task = scheduler._current_dispatch
    assert old_task is not None

    scheduler.reset()
    scheduler.submit(_payload("new"))
    await old_cancelled.wait()
    await new_started.wait()
    new_task = scheduler._current_dispatch
    assert new_task is not None
    assert new_task is not old_task

    retired = list(scheduler._retired_tasks)
    release_old.set()
    await asyncio.gather(*retired, return_exceptions=True)

    assert scheduler._current_dispatch is new_task
    old_finished = next(
        record
        for record in audit.records
        if record["op"] == "support.dispatch_submission_finalized"
        and record["detail"]["task_id"] == old_task.task_id
    )
    assert old_finished["detail"]["classification"] == "retroactive"
    assert old_finished["detail"]["outcome"] == "submitted"

    release_new.set()
    await scheduler.wait_idle()
    new_finished = next(
        record
        for record in audit.records
        if record["op"] == "support.dispatch_submission_finalized"
        and record["detail"]["task_id"] == new_task.task_id
    )
    assert new_finished["detail"]["classification"] == "current"
    assert scheduler.status()["retroactive_finalization_count"] == 1
    await scheduler.close()


@pytest.mark.asyncio
async def test_unknown_completion_is_stray_and_audit_is_payload_free():
    audit = _Audit()

    async def dispatch(_payload: dict) -> None:
        return None

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=audit, queue_limit=5)
    task = _DispatchTask(
        task_id="support_unknown",
        payload=_payload("private-provider-id"),
        priority=SupportPriority.LIGHT,
        sequence=1,
        generation=scheduler._dispatch_generation,
    )

    assert await scheduler._finish_dispatch(task, outcome="submitted") == "stray"

    record = audit.records[-1]
    assert record["op"] == "support.dispatch_submission_finalized"
    assert record["level"] == "warning"
    assert record["detail"] == {
        "task_id": "support_unknown",
        "event_type": "gift",
        "priority": "light",
        "classification": "stray",
        "outcome": "submitted",
        "pipeline_result_status": "unknown",
    }
    audit_text = repr(audit.records)
    assert "viewer-1" not in audit_text
    assert "Heart" not in audit_text
    assert "private-provider-id" not in audit_text
    assert scheduler.status()["stray_finalization_count"] == 1
    await scheduler.close()


@pytest.mark.asyncio
async def test_dispatched_history_is_bounded_to_32_sanitized_records():
    async def dispatch(_payload: dict) -> None:
        return None

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=64)
    for index in range(40):
        assert scheduler.submit(_payload(f"provider-{index}")) is True

    await scheduler.wait_idle()

    assert scheduler.status()["dispatched_history_count"] == 32
    assert scheduler.status()["dispatched_history_limit"] == 32
    history_text = repr(scheduler._dispatched_history)
    assert "viewer-1" not in history_text
    assert "Heart" not in history_text
    assert "provider-" not in history_text
    await scheduler.close()


@pytest.mark.asyncio
async def test_cancelled_old_worker_cannot_mark_replacement_worker_idle():
    old_started = asyncio.Event()
    new_started = asyncio.Event()
    third_started = asyncio.Event()
    release_new = asyncio.Event()
    completed: list[str] = []

    async def dispatch(payload: dict) -> None:
        event_id = payload["provider_event_id"]
        if event_id == "old":
            old_started.set()
            await asyncio.Event().wait()
        if event_id == "third":
            third_started.set()
            completed.append(event_id)
            return
        new_started.set()
        await release_new.wait()
        completed.append(event_id)

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=5)
    scheduler.submit(_payload("old"))
    await old_started.wait()
    scheduler.reset()
    scheduler.submit(_payload("new"))
    await new_started.wait()
    await asyncio.sleep(0)
    scheduler.submit(_payload("third"))
    await asyncio.sleep(0)
    assert not third_started.is_set()

    idle_wait = asyncio.create_task(scheduler.wait_idle())
    await asyncio.sleep(0)
    assert not idle_wait.done()

    release_new.set()
    await idle_wait
    assert completed == ["new", "third"]
    await scheduler.close()


@pytest.mark.asyncio
async def test_wait_idle_waits_for_cancelled_retired_worker_to_finish():
    started = asyncio.Event()
    cancellation_seen = asyncio.Event()
    release = asyncio.Event()

    async def dispatch(_payload: dict) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_seen.set()
            await release.wait()

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=5)
    scheduler.submit(_payload("old"))
    await started.wait()

    scheduler.reset()
    await cancellation_seen.wait()
    idle_wait = asyncio.create_task(scheduler.wait_idle())
    await asyncio.sleep(0)
    assert not idle_wait.done()

    release.set()
    await asyncio.wait_for(idle_wait, timeout=1)
    await scheduler.close()


@pytest.mark.asyncio
async def test_close_cancels_owned_dispatch_and_does_not_leave_current_locked():
    audit = _Audit()
    started = asyncio.Event()

    async def dispatch(_payload: dict) -> None:
        started.set()
        await asyncio.Event().wait()

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=audit, queue_limit=5)
    scheduler.submit(_payload("cancel-on-close"))
    await started.wait()
    task = scheduler._current_dispatch
    assert task is not None

    await asyncio.wait_for(scheduler.close(), timeout=1)

    finished = next(
        record
        for record in audit.records
        if record["op"] == "support.dispatch_submission_finalized"
        and record["detail"]["task_id"] == task.task_id
    )
    assert finished["detail"]["classification"] == "retroactive"
    assert finished["detail"]["outcome"] == "cancelled"
    assert scheduler.status()["current_dispatch_count"] == 0


@pytest.mark.asyncio
async def test_close_permanently_rejects_future_submissions():
    dispatched: list[str] = []

    async def dispatch(payload: dict) -> None:
        dispatched.append(payload["provider_event_id"])

    scheduler = SupportEventScheduler(dispatch=dispatch, audit=_Audit(), queue_limit=5)
    await scheduler.close()

    assert scheduler.submit(_payload("late")) is False
    await asyncio.sleep(0)
    assert dispatched == []
    assert scheduler.status()["pending_count"] == 0
