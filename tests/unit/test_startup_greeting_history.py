from __future__ import annotations

import asyncio
import json
from glob import glob
from unittest.mock import MagicMock

import pytest

from memory import startup_greeting_history as startup_history_module
from memory.startup_greeting_history import StartupGreetingHistory


def _build_history(tmp_path) -> StartupGreetingHistory:
    config_manager = MagicMock()
    config_manager.memory_dir = str(tmp_path)
    return StartupGreetingHistory(config_manager)


def test_committed_greeting_is_immediately_visible_and_keeps_metadata(tmp_path):
    history = _build_history(tmp_path)

    staged = history.stage_committed(
        "Neko",
        "  下午好，刚才那本书还挺有意思。  ",
        variant_key="memory_followup",
        topic_key="ref_123",
        committed_at=1000.0,
    )

    assert staged is not None
    records = history.recent("Neko", now=1001.0)
    assert len(records) == 1
    record = records[0]
    assert record.text == "下午好，刚才那本书还挺有意思。"
    assert record.variant_key == "memory_followup"
    assert record.topic_key == "ref_123"


@pytest.mark.asyncio
async def test_detached_flush_round_trips_without_awaiting_at_commit_point(tmp_path):
    history = _build_history(tmp_path)
    await history.apreload("Neko")
    staged = history.stage_committed(
        "Neko",
        "又见面啦。",
        variant_key="simple_presence",
        committed_at=2000.0,
    )

    history.flush_staged_detached(staged)
    pending = list(history._detached_flushes)
    assert pending
    await asyncio.gather(*pending)

    reloaded = _build_history(tmp_path)
    await reloaded.apreload("Neko")
    records = reloaded.recent("Neko", now=2001.0)
    assert [(item.text, item.variant_key) for item in records] == [
        ("又见面啦。", "simple_presence")
    ]


def test_startup_avoidance_survives_trigger_gap_and_expires_after_24_hours(tmp_path):
    history = _build_history(tmp_path)
    history.stage_committed(
        "Neko",
        "下午好，又见面啦。",
        variant_key="recent_continuity",
        committed_at=10_000.0,
    )

    assert history.recent("Neko", now=10_901.0)
    assert history.recent("Neko", now=10_000.0 + 86_399.0)
    assert history.recent("Neko", now=10_000.0 + 86_400.0) == []
    assert history.recent("Neko", now=10_000.0 + 86_401.0) == []


def test_history_is_newest_first_and_count_capped(tmp_path):
    cap = startup_history_module._MAX_RECORDS
    overflow = 8
    history = _build_history(tmp_path)
    for index in range(cap + overflow):
        history.stage_committed(
            "Neko",
            f"opening {index}",
            variant_key=f"variant_{index}",
            committed_at=float(index + 1),
        )

    records = history.recent(
        "Neko",
        now=float(cap + overflow),
        max_age_seconds=10 * (cap + overflow),
        limit=cap + overflow,
    )
    assert len(records) == cap
    assert records[0].text == f"opening {cap + overflow - 1}"
    assert records[-1].text == f"opening {overflow}"


def test_record_cap_outlasts_the_full_recall_window():
    """The rolling cap must not evict records the recall window still cites.

    Capacity is measured against the trigger gap gate, not the burst gate: the
    burst suppression is waived whenever the user spoke after the last
    greeting, so back-to-back committed greetings can be one gap apart. Sizing
    against the burst window silently under-provisions by 2x and drops the
    oldest day while the prompt still claims to avoid three days of openings.
    """

    from main_logic.startup_greeting_policy import (
        _STARTUP_GREETING_BURST_SECONDS,
        _STARTUP_GREETING_MIN_GAP_SECONDS,
        _STARTUP_GREETING_RECALL_SECONDS,
    )

    max_commits_in_recall_window = (
        _STARTUP_GREETING_RECALL_SECONDS / _STARTUP_GREETING_MIN_GAP_SECONDS
    )
    assert startup_history_module._MAX_RECORDS >= max_commits_in_recall_window
    # The gap gate really is the tighter of the two bounds.
    assert _STARTUP_GREETING_MIN_GAP_SECONDS < _STARTUP_GREETING_BURST_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "persisted_bytes",
    [
        b'{"records":',
        json.dumps({"records": "not-a-list"}).encode(),
        b'\xff\xfeinvalid utf-8',
        json.dumps(
            {
                "records": [
                    {
                        "ts": 99.0,
                        "text": "A valid opening.",
                        "variant_key": "simple_presence",
                    },
                    {"ts": 100.0, "text": "Missing its variant."},
                ]
            }
        ).encode(),
        json.dumps(
            {
                "records": [
                    {
                        "ts": 99.0,
                        "text": "A valid opening.",
                        "variant_key": "simple_presence",
                    },
                    {
                        "ts": "not-a-timestamp",
                        "text": "Invalid timestamp.",
                        "variant_key": "simple_presence",
                    },
                ]
            }
        ).encode(),
    ],
    ids=[
        "bad-json",
        "bad-schema",
        "bad-encoding",
        "bad-record-missing-field",
        "bad-record-timestamp",
    ],
)
async def test_corrupt_history_is_backed_up_and_self_heals(
    tmp_path, persisted_bytes
):
    history = _build_history(tmp_path)
    path = history._file_path("Neko")
    with open(path, "wb") as file:
        file.write(persisted_bytes)

    await history.apreload("Neko")

    assert history.recent("Neko", now=100.0) == []
    assert history._cache["Neko"] == []
    backup_paths = glob(f"{path}.corrupt.*.bak")
    assert len(backup_paths) == 1
    with open(backup_paths[0], "rb") as file:
        assert file.read() == persisted_bytes

    token = history.try_reserve("Neko", now=100.0)
    assert token
    staged = history.stage_committed(
        "Neko",
        "A recovered opening.",
        variant_key="simple_presence",
        committed_at=101.0,
        reservation_token=token,
    )
    await history.aflush_staged(staged)
    with open(backup_paths[0], "rb") as file:
        assert file.read() == persisted_bytes

    reloaded = _build_history(tmp_path)
    await reloaded.apreload("Neko")
    assert [record.text for record in reloaded.recent("Neko", now=102.0)] == [
        "A recovered opening."
    ]


@pytest.mark.asyncio
async def test_stale_legacy_backup_does_not_mask_current_corruption(tmp_path):
    history = _build_history(tmp_path)
    path = history._file_path("Neko")
    legacy_backup_path = f"{path}.corrupt.bak"
    earlier_corruption = b'{"records":"earlier"}'
    current_corruption = b'{"records":'
    with open(legacy_backup_path, "wb") as file:
        file.write(earlier_corruption)
    with open(path, "wb") as file:
        file.write(current_corruption)

    await history.apreload("Neko")

    assert history._cache["Neko"] == []
    with open(legacy_backup_path, "rb") as file:
        assert file.read() == earlier_corruption
    backup_paths = glob(f"{path}.corrupt.*.bak")
    assert len(backup_paths) == 1
    with open(backup_paths[0], "rb") as file:
        assert file.read() == current_corruption

    retry = _build_history(tmp_path)
    await retry.apreload("Neko")
    assert retry._cache["Neko"] == []
    assert glob(f"{path}.corrupt.*.bak") == backup_paths

    token = history.try_reserve("Neko", now=100.0)
    assert token
    staged = history.stage_committed(
        "Neko",
        "A recovered opening.",
        variant_key="simple_presence",
        committed_at=101.0,
        reservation_token=token,
    )
    await history.aflush_staged(staged)

    with open(legacy_backup_path, "rb") as file:
        assert file.read() == earlier_corruption
    with open(backup_paths[0], "rb") as file:
        assert file.read() == current_corruption
    reloaded = _build_history(tmp_path)
    await reloaded.apreload("Neko")
    assert [record.text for record in reloaded.recent("Neko", now=102.0)] == [
        "A recovered opening."
    ]


@pytest.mark.asyncio
async def test_recovery_backs_up_parsed_snapshot_and_retries_changed_source(
    tmp_path, monkeypatch
):
    history = _build_history(tmp_path)
    path = history._file_path("Neko")
    corrupt_bytes = b'{"records":'
    replacement_bytes = json.dumps(
        {
            "version": 1,
            "records": [
                {
                    "ts": 90.0,
                    "text": "A concurrent valid opening.",
                    "variant_key": "simple_presence",
                }
            ],
        }
    ).encode()
    with open(path, "wb") as file:
        file.write(corrupt_bytes)

    real_read = startup_history_module.read_bytes_tolerating_replace
    first_read = True

    def _replace_after_first_read(target_path):
        nonlocal first_read
        persisted_bytes = real_read(target_path)
        if first_read:
            first_read = False
            with open(target_path, "wb") as file:
                file.write(replacement_bytes)
        return persisted_bytes

    monkeypatch.setattr(
        startup_history_module,
        "read_bytes_tolerating_replace",
        _replace_after_first_read,
    )

    await history.apreload("Neko")

    assert "Neko" not in history._cache
    backup_paths = glob(f"{path}.corrupt.*.bak")
    assert len(backup_paths) == 1
    with open(backup_paths[0], "rb") as file:
        assert file.read() == corrupt_bytes
    with open(path, "rb") as file:
        assert file.read() == replacement_bytes

    await history.apreload("Neko")

    assert [record.text for record in history.recent("Neko", now=100.0)] == [
        "A concurrent valid opening."
    ]


@pytest.mark.asyncio
async def test_corrupt_history_backup_failure_denies_reservation(
    tmp_path, monkeypatch
):
    history = _build_history(tmp_path)
    path = history._file_path("Neko")
    persisted_bytes = b'{"records":'
    with open(path, "wb") as file:
        file.write(persisted_bytes)

    def _reject_fsync(_file_descriptor):
        raise PermissionError("backup sync unavailable")

    monkeypatch.setattr(
        "memory.startup_greeting_history.os.fsync",
        _reject_fsync,
    )

    await history.apreload("Neko")

    assert "Neko" not in history._cache
    assert history.try_reserve("Neko", now=100.0) is None
    with open(path, "rb") as file:
        assert file.read() == persisted_bytes
    assert glob(f"{path}.corrupt.*.bak") == []


@pytest.mark.asyncio
async def test_transient_read_oserror_keeps_cache_absent_and_denies_reservation(
    tmp_path, monkeypatch
):
    history = _build_history(tmp_path)
    path = history._file_path("Neko")
    with open(path, "w", encoding="utf-8") as file:
        json.dump({"version": 1, "records": []}, file)

    def _raise_oserror(_path):
        raise PermissionError("temporarily unavailable")

    monkeypatch.setattr(
        "memory.startup_greeting_history.read_bytes_tolerating_replace",
        _raise_oserror,
    )

    await history.apreload("Neko")

    assert "Neko" not in history._cache
    assert history.try_reserve("Neko", now=100.0) is None
    assert glob(f"{path}.corrupt.*.bak") == []


@pytest.mark.asyncio
async def test_reservation_is_atomic_and_failed_delivery_can_release(tmp_path):
    history = _build_history(tmp_path)
    await history.apreload("Neko")

    first = history.try_reserve("Neko", now=1000.0)
    assert first
    assert history.try_reserve("Neko", now=1000.0) is None

    history.release_reservation("Neko", first)
    second = history.try_reserve("Neko", now=1000.0)
    assert second and second != first

    staged = history.stage_committed(
        "Neko",
        "A committed opening.",
        variant_key="simple_presence",
        committed_at=1000.0,
        reservation_token=second,
    )
    assert staged is not None
    assert history.try_reserve("Neko", now=1001.0) is None

    # A real user turn after the greeting ends the burst and permits a new
    # startup after the ordinary 15-minute gap gate.
    third = history.try_reserve(
        "Neko",
        now=1901.0,
        last_user_engagement_at=1001.0,
    )
    assert third
    history.release_reservation("Neko", third)


def test_invalid_commit_timestamps_are_rejected(tmp_path):
    history = _build_history(tmp_path)

    for timestamp in (float("nan"), float("inf"), -1.0, 0.0):
        assert (
            history.stage_committed(
                "Neko",
                "opening",
                variant_key="simple_presence",
                committed_at=timestamp,
            )
            is None
        )
