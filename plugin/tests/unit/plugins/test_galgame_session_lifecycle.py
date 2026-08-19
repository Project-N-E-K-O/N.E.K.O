from __future__ import annotations

from pathlib import Path

import pytest

from plugin.plugins.galgame_plugin.reader import snapshot_events_boundary
from plugin.plugins.galgame_plugin.session_lifecycle import (
    SESSION_ORIGIN_CURRENT_RUN,
    SESSION_ORIGIN_PREEXISTING,
    classify_session_origin,
    event_releases_empty_snapshot_gate,
)


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    ("data_source", "runtime_field"),
    [
        ("memory_reader", "memory_reader_session_id"),
        ("ocr_reader", "ocr_reader_session_id"),
    ],
)
def test_runtime_owned_session_wins_over_preexisting_evidence(
    data_source: str,
    runtime_field: str,
) -> None:
    kwargs = {
        "data_source": data_source,
        "session_id": "owned-session",
        "started_at": "2026-08-14T00:00:00Z",
        "plugin_run_started_at": 1_800_000_000.0,
        "startup_existing_session_ids": {"owned-session"},
        runtime_field: "owned-session",
    }

    assert classify_session_origin(**kwargs) == SESSION_ORIGIN_CURRENT_RUN


@pytest.mark.plugin_unit
def test_startup_scan_set_does_not_make_invalid_outside_session_current() -> None:
    common = {
        "data_source": "bridge_sdk",
        "started_at": "not-a-timestamp",
        "plugin_run_started_at": 1_800_000_000.0,
        "startup_existing_session_ids": {"old-session"},
    }

    assert (
        classify_session_origin(session_id="old-session", **common)
        == SESSION_ORIGIN_PREEXISTING
    )
    assert (
        classify_session_origin(session_id="outside-session", **common)
        == SESSION_ORIGIN_PREEXISTING
    )


@pytest.mark.plugin_unit
def test_startup_scan_set_allows_outside_session_started_after_run() -> None:
    assert (
        classify_session_origin(
            data_source="bridge_sdk",
            session_id="new-session",
            started_at="2099-01-01T00:00:00Z",
            plugin_run_started_at=1_800_000_000.0,
            startup_existing_session_ids={"old-session"},
        )
        == SESSION_ORIGIN_CURRENT_RUN
    )


@pytest.mark.plugin_unit
def test_startup_session_identity_is_scoped_by_source_and_game() -> None:
    startup_existing_session_ids = {
        ("bridge_sdk", "game-a", "shared-session"),
    }
    common = {
        "session_id": "shared-session",
        "started_at": "2099-01-01T00:00:00Z",
        "plugin_run_started_at": 1_800_000_000.0,
        "startup_existing_session_ids": startup_existing_session_ids,
    }

    assert (
        classify_session_origin(
            data_source="bridge_sdk",
            game_id="game-a",
            **common,
        )
        == SESSION_ORIGIN_PREEXISTING
    )
    assert (
        classify_session_origin(
            data_source="bridge_sdk",
            game_id="game-b",
            **common,
        )
        == SESSION_ORIGIN_CURRENT_RUN
    )
    assert (
        classify_session_origin(
            data_source="memory_reader",
            game_id="game-a",
            **common,
        )
        == SESSION_ORIGIN_CURRENT_RUN
    )


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    ("started_at", "expected"),
    [
        ("2026-08-14T00:00:01Z", SESSION_ORIGIN_CURRENT_RUN),
        ("2026-08-14T00:00:00Z", SESSION_ORIGIN_PREEXISTING),
        ("2026-08-13T23:59:59Z", SESSION_ORIGIN_PREEXISTING),
        ("invalid", SESSION_ORIGIN_PREEXISTING),
        ("", SESSION_ORIGIN_PREEXISTING),
    ],
)
def test_started_at_fallback_is_strict_and_conservative(
    started_at: str,
    expected: str,
) -> None:
    assert (
        classify_session_origin(
            data_source="bridge_sdk",
            session_id="session-a",
            started_at=started_at,
            plugin_run_started_at=1_786_665_600.0,
            startup_existing_session_ids=None,
        )
        == expected
    )


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    ("started_at", "plugin_run_started_at"),
    [
        ("2026-08-14T00:00:00Z", 1_786_665_600.75),
        ("2026-08-14T00:00:00.123Z", 1_786_665_600.1235),
    ],
)
def test_lower_precision_session_started_at_does_not_guess_same_tick_origin(
    started_at: str,
    plugin_run_started_at: float,
) -> None:
    assert (
        classify_session_origin(
            data_source="bridge_sdk",
            session_id="same-tick-new-session",
            started_at=started_at,
            plugin_run_started_at=plugin_run_started_at,
            startup_existing_session_ids=set(),
        )
        == SESSION_ORIGIN_PREEXISTING
    )


@pytest.mark.plugin_unit
def test_missing_session_id_is_always_preexisting() -> None:
    assert (
        classify_session_origin(
            data_source="bridge_sdk",
            session_id="",
            started_at="2099-01-01T00:00:00Z",
            plugin_run_started_at=1.0,
            startup_existing_session_ids=set(),
        )
        == SESSION_ORIGIN_PREEXISTING
    )


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    "event_type",
    [
        "session_started",
        "screen_classified",
        "line_observed",
        "line_changed",
        "choices_shown",
        "choice_selected",
        "scene_changed",
        "save_loaded",
    ],
)
def test_state_mutating_events_release_empty_snapshot_gate(event_type: str) -> None:
    assert event_releases_empty_snapshot_gate({"type": event_type}) is True


@pytest.mark.plugin_unit
@pytest.mark.parametrize("event", [{"type": "heartbeat"}, {"type": "error"}, {}, None])
def test_non_state_events_keep_empty_snapshot_gate(event: object) -> None:
    assert event_releases_empty_snapshot_gate(event) is False


@pytest.mark.plugin_unit
def test_snapshot_events_boundary_uses_eof_for_complete_jsonl(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_bytes(b'{"seq":1}\n{"seq":2}\n')

    boundary = snapshot_events_boundary(events_path)

    assert boundary.offset == events_path.stat().st_size
    assert boundary.file_size == events_path.stat().st_size
    assert boundary.error == ""


@pytest.mark.plugin_unit
def test_snapshot_events_boundary_keeps_incomplete_line_for_tail_reader(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    complete_prefix = b'{"seq":1}\n'
    events_path.write_bytes(complete_prefix + b'{"seq":2')

    boundary = snapshot_events_boundary(events_path)

    assert boundary.offset == len(complete_prefix)
    assert boundary.file_size == events_path.stat().st_size
    assert boundary.error == ""


@pytest.mark.plugin_unit
def test_snapshot_events_boundary_keeps_oversized_partial_after_checkpoint(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    checkpoint_line = b'{"session_id":"sess-a","seq":1}\n'
    oversized_partial = b'{"session_id":"sess-a","seq":2,"text":"' + b"x" * 128
    events_path.write_bytes(checkpoint_line + oversized_partial)

    boundary = snapshot_events_boundary(
        events_path,
        session_id="sess-a",
        last_seq=1,
        bytes_limit=32,
        events_limit=2,
    )

    assert boundary.offset == len(checkpoint_line)
    assert boundary.file_size == events_path.stat().st_size
    assert boundary.last_seq == 1
    assert boundary.checkpoint
    assert boundary.error == ""


@pytest.mark.plugin_unit
def test_snapshot_events_boundary_pairs_captured_offset_with_seq_high_water(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    first_line = b'{"session_id":"sess-a","seq":1,"type":"line_changed"}\n'
    checkpoint_ahead_line = (
        b'{"session_id":"sess-a","seq":2,"type":"line_changed"}\n'
    )
    events_path.write_bytes(first_line + checkpoint_ahead_line)

    boundary = snapshot_events_boundary(
        events_path,
        session_id="sess-a",
        last_seq=2,
        snapshot_file_size=len(first_line),
    )

    assert boundary.offset == len(first_line)
    assert boundary.last_seq == 1
    assert boundary.checkpoint


@pytest.mark.plugin_unit
def test_snapshot_events_boundary_keeps_initial_partial_after_checkpoint(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    initial_partial = b'{"session_id":"sess-a","seq":1,"type":"line_changed"'
    events_path.write_bytes(initial_partial)

    boundary = snapshot_events_boundary(
        events_path,
        session_id="sess-a",
        last_seq=1,
    )

    assert boundary.offset == 0
    assert boundary.file_size == len(initial_partial)
    assert boundary.error == ""


@pytest.mark.plugin_unit
def test_snapshot_events_boundary_stops_at_session_checkpoint(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    checkpoint_line = b'{"session_id":"sess-a","seq":1}\n'
    events_path.write_bytes(
        checkpoint_line
        + b'{"session_id":"sess-a","seq":2}\n'
        + b'{"session_id":"sess-b","seq":1}\n'
    )

    boundary = snapshot_events_boundary(
        events_path,
        session_id="sess-a",
        last_seq=1,
    )

    assert boundary.offset == len(checkpoint_line)
    assert boundary.file_size == events_path.stat().st_size
    assert boundary.error == ""


@pytest.mark.plugin_unit
def test_snapshot_events_boundary_bounds_checkpoint_scan_to_warmup_window(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    old_prefix = b"".join(
        b'{"session_id":"old","seq":%d,"padding":"' % seq
        + b"x" * 80
        + b'"}\n'
        for seq in range(1, 101)
    )
    checkpoint_line = b'{"session_id":"sess-a","seq":100}\n'
    appended_line = b'{"session_id":"sess-a","seq":101}\n'
    events_path.write_bytes(old_prefix + checkpoint_line + appended_line)

    boundary = snapshot_events_boundary(
        events_path,
        session_id="sess-a",
        last_seq=100,
        bytes_limit=len(checkpoint_line) + len(appended_line) + 1,
        events_limit=2,
    )

    assert boundary.offset == len(old_prefix) + len(checkpoint_line)
    assert boundary.file_size == events_path.stat().st_size
    assert boundary.error == ""


@pytest.mark.plugin_unit
def test_snapshot_events_boundary_uses_bounded_fallback_when_checkpoint_is_old(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    checkpoint_line = b'{"session_id":"sess-a","seq":1}\n'
    old_prefix = checkpoint_line + b'{"session_id":"old","seq":1,"padding":"' + b"x" * 512 + b'"}\n'
    new_lines = (
        b'{"session_id":"sess-a","seq":2}\n'
        b'{"session_id":"sess-a","seq":3}\n'
    )
    events_path.write_bytes(old_prefix + new_lines)

    boundary = snapshot_events_boundary(
        events_path,
        session_id="sess-a",
        last_seq=1,
        bytes_limit=len(new_lines) + 1,
        events_limit=2,
    )

    assert boundary.offset == len(old_prefix)
    assert boundary.file_size == events_path.stat().st_size
    assert boundary.error == ""


@pytest.mark.plugin_unit
def test_snapshot_events_boundary_keeps_record_at_exact_window_start(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    old_prefix = (
        b'{"session_id":"sess-a","seq":1}\n'
        + b'{"session_id":"old","seq":1,"padding":"'
        + b"x" * 512
        + b'"}\n'
    )
    new_lines = (
        b'{"session_id":"sess-a","seq":2}\n'
        b'{"session_id":"sess-a","seq":3}\n'
    )
    events_path.write_bytes(old_prefix + new_lines)

    boundary = snapshot_events_boundary(
        events_path,
        session_id="sess-a",
        last_seq=1,
        bytes_limit=len(new_lines),
        events_limit=2,
    )

    assert boundary.offset == len(old_prefix)
    assert boundary.file_size == events_path.stat().st_size
    assert boundary.error == ""


@pytest.mark.plugin_unit
def test_snapshot_events_boundary_preserves_zero_checkpoint_racing_event(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    snapshot_line = b'{"session_id":"sess-a","seq":0,"type":"session_started"}\n'
    racing_line = b'{"session_id":"sess-a","seq":1,"type":"line_observed"}\n'
    events_path.write_bytes(snapshot_line + racing_line)

    boundary = snapshot_events_boundary(
        events_path,
        session_id="sess-a",
        last_seq=0,
        snapshot_file_size=len(snapshot_line),
    )

    assert boundary.offset == len(snapshot_line)
    assert boundary.file_size == len(snapshot_line) + len(racing_line)
    assert boundary.error == ""


@pytest.mark.plugin_unit
@pytest.mark.parametrize("last_seq", [0, -1])
def test_snapshot_events_boundary_uses_eof_for_nonpositive_checkpoint(
    tmp_path: Path,
    last_seq: int,
) -> None:
    events_path = tmp_path / "events.jsonl"
    complete_stream = (
        b'{"session_id":"sess-a","seq":1}\n'
        b'{"session_id":"sess-a","seq":2}\n'
    )
    events_path.write_bytes(complete_stream)

    boundary = snapshot_events_boundary(
        events_path,
        session_id="sess-a",
        last_seq=last_seq,
    )

    assert boundary.offset == len(complete_stream)
    assert boundary.file_size == len(complete_stream)
    assert boundary.error == ""


@pytest.mark.plugin_unit
def test_snapshot_events_boundary_handles_missing_and_unreadable_paths(
    tmp_path: Path,
) -> None:
    missing = snapshot_events_boundary(tmp_path / "missing.jsonl")
    unreadable_path = tmp_path / "events.jsonl"
    unreadable_path.mkdir()
    unreadable = snapshot_events_boundary(unreadable_path)

    assert (missing.offset, missing.file_size, missing.error) == (0, 0, "")
    assert unreadable.offset == 0
    assert unreadable.file_size == 0
    assert unreadable.error
