# -*- coding: utf-8 -*-
"""
Unit tests for memory.evidence_analytics.funnel_counts (RFC §3.10 / §4.4 / §8 S24).

Covers:
- empty / missing event log → all zeros
- each of the 6 funnel-relevant event types increments the right bucket
- date window filtering (events before `since` / after `until` are excluded)
- reflection.state_changed dispatches by `to` field
  (confirmed / promoted / merged / denied / archived)
- persona.fact_added with `archive_shard_path` → persona_entries_archived,
  without → persona_entries_added
- malformed JSON line is skipped, scan continues
- unknown event types silently ignored
- realistic mixed funnel (S24 fixture) → exact bucket counts
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────


_LANLAN_NAME = "小天"


def _events_file_for(tmpdir: str, name: str = _LANLAN_NAME) -> str:
    """Mirror EventLog._events_path: <memory_dir>/<name>/events.ndjson."""
    char_dir = os.path.join(tmpdir, name)
    os.makedirs(char_dir, exist_ok=True)
    return os.path.join(char_dir, "events.ndjson")


def _write_events(tmpdir: str, events: list[dict], name: str = _LANLAN_NAME) -> str:
    """Write a list of event records to the character's events.ndjson.

    Each event is `{type, ts, payload}` (event_id is auto-filled).
    Returns the absolute file path.
    """
    path = _events_file_for(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        for evt in events:
            rec = {
                "event_id": evt.get("event_id") or str(uuid.uuid4()),
                "type": evt["type"],
                "ts": evt["ts"],
                "payload": evt.get("payload", {}),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def _patched_funnel(tmpdir: str):
    """Return funnel_counts with config_manager pointing at tmpdir.

    `funnel_counts` calls `get_config_manager().memory_dir` indirectly via
    `_events_path`; we patch that single call at the module level.
    """
    cm = MagicMock()
    cm.memory_dir = tmpdir
    return cm


@pytest.fixture
def fixed_now():
    return datetime(2026, 4, 22, 12, 0, 0)


@pytest.fixture
def wide_window(fixed_now):
    return fixed_now - timedelta(days=30), fixed_now + timedelta(days=1)


def _empty_buckets() -> dict:
    return {
        "facts_added": 0,
        "reflections_synthesized": 0,
        "reflections_confirmed": 0,
        "reflections_promoted": 0,
        "reflections_merged": 0,
        "reflections_denied": 0,
        "reflections_archived": 0,
        "persona_entries_added": 0,
        "persona_entries_rewritten": 0,
        "persona_entries_archived": 0,
    }


def _run(tmpdir, since, until, name=_LANLAN_NAME):
    cm = _patched_funnel(tmpdir)
    with patch("memory.evidence_analytics.get_config_manager", return_value=cm):
        from memory.evidence_analytics import funnel_counts
        return funnel_counts(name, since, until)


# ── tests ────────────────────────────────────────────────────────────


def test_no_event_file_returns_all_zeros(tmp_path, wide_window):
    since, until = wide_window
    counts = _run(str(tmp_path), since, until)
    assert counts == _empty_buckets()


def test_empty_event_file_returns_all_zeros(tmp_path, wide_window):
    _write_events(str(tmp_path), [])
    since, until = wide_window
    counts = _run(str(tmp_path), since, until)
    assert counts == _empty_buckets()


def test_fact_added_increments_facts_added(tmp_path, fixed_now, wide_window):
    _write_events(str(tmp_path), [
        {"type": "fact.added", "ts": fixed_now.isoformat(), "payload": {"fact_id": "f1"}},
        {"type": "fact.added", "ts": fixed_now.isoformat(), "payload": {"fact_id": "f2"}},
    ])
    since, until = wide_window
    counts = _run(str(tmp_path), since, until)
    expected = _empty_buckets() | {"facts_added": 2}
    assert counts == expected


def test_reflection_synthesized_increments_bucket(tmp_path, fixed_now, wide_window):
    _write_events(str(tmp_path), [
        {"type": "reflection.synthesized", "ts": fixed_now.isoformat(), "payload": {"reflection_id": "r1"}},
    ])
    since, until = wide_window
    assert _run(str(tmp_path), since, until)["reflections_synthesized"] == 1


@pytest.mark.parametrize("to_value,bucket", [
    ("confirmed", "reflections_confirmed"),
    ("promoted", "reflections_promoted"),
    ("merged", "reflections_merged"),
    ("denied", "reflections_denied"),
    ("archived", "reflections_archived"),
])
def test_reflection_state_changed_dispatches_by_to(tmp_path, fixed_now, wide_window, to_value, bucket):
    _write_events(str(tmp_path), [
        {
            "type": "reflection.state_changed",
            "ts": fixed_now.isoformat(),
            "payload": {"reflection_id": "r1", "from": "pending", "to": to_value},
        },
    ])
    since, until = wide_window
    counts = _run(str(tmp_path), since, until)
    assert counts[bucket] == 1
    # All other buckets stay 0
    for other in counts:
        if other != bucket:
            assert counts[other] == 0, f"bucket {other!r} should be 0 but got {counts[other]}"


def test_reflection_state_changed_unknown_to_silently_ignored(tmp_path, fixed_now, wide_window):
    """`to` 值不是 RFC §3.10.2 enumerate 的终态（pending / promote_blocked / 未来扩展）→ 不计数。"""
    _write_events(str(tmp_path), [
        {
            "type": "reflection.state_changed",
            "ts": fixed_now.isoformat(),
            "payload": {"reflection_id": "r1", "from": "denied", "to": "pending"},
        },
        {
            "type": "reflection.state_changed",
            "ts": fixed_now.isoformat(),
            "payload": {"reflection_id": "r2", "from": "confirmed", "to": "promote_blocked"},
        },
    ])
    since, until = wide_window
    assert _run(str(tmp_path), since, until) == _empty_buckets()


def test_persona_fact_added_without_archive_path_routes_to_added(tmp_path, fixed_now, wide_window):
    _write_events(str(tmp_path), [
        {
            "type": "persona.fact_added",
            "ts": fixed_now.isoformat(),
            "payload": {"entity_key": "master", "entry_id": "p1"},
        },
    ])
    since, until = wide_window
    counts = _run(str(tmp_path), since, until)
    assert counts["persona_entries_added"] == 1
    assert counts["persona_entries_archived"] == 0


def test_persona_fact_added_with_archive_path_routes_to_archived(tmp_path, fixed_now, wide_window):
    _write_events(str(tmp_path), [
        {
            "type": "persona.fact_added",
            "ts": fixed_now.isoformat(),
            "payload": {
                "entity_key": "master",
                "entry_id": "p1",
                "archive_shard_path": "persona_archive/2026-04-22_abcd1234.json",
            },
        },
    ])
    since, until = wide_window
    counts = _run(str(tmp_path), since, until)
    assert counts["persona_entries_added"] == 0
    assert counts["persona_entries_archived"] == 1


def test_persona_entry_updated_increments_rewritten(tmp_path, fixed_now, wide_window):
    _write_events(str(tmp_path), [
        {
            "type": "persona.entry_updated",
            "ts": fixed_now.isoformat(),
            "payload": {
                "entity_key": "master",
                "entry_id": "p1",
                "merged_from_ids": ["r1", "r2"],
            },
        },
    ])
    since, until = wide_window
    assert _run(str(tmp_path), since, until)["persona_entries_rewritten"] == 1


def test_window_filtering_excludes_before_since_and_after_until(tmp_path, fixed_now):
    """Events outside [since, until] do not contribute."""
    before = (fixed_now - timedelta(days=10)).isoformat()
    inside = fixed_now.isoformat()
    after = (fixed_now + timedelta(days=10)).isoformat()
    _write_events(str(tmp_path), [
        {"type": "fact.added", "ts": before, "payload": {"fact_id": "old"}},
        {"type": "fact.added", "ts": inside, "payload": {"fact_id": "live"}},
        {"type": "fact.added", "ts": after, "payload": {"fact_id": "future"}},
    ])
    since = fixed_now - timedelta(days=1)
    until = fixed_now + timedelta(days=1)
    counts = _run(str(tmp_path), since, until)
    assert counts["facts_added"] == 1


def test_window_inclusive_on_both_ends(tmp_path, fixed_now):
    """Boundary events at exactly `since` and `until` are included."""
    _write_events(str(tmp_path), [
        {"type": "fact.added", "ts": fixed_now.isoformat(), "payload": {"fact_id": "boundary_low"}},
        {"type": "fact.added", "ts": (fixed_now + timedelta(days=1)).isoformat(), "payload": {"fact_id": "boundary_hi"}},
    ])
    since = fixed_now
    until = fixed_now + timedelta(days=1)
    counts = _run(str(tmp_path), since, until)
    assert counts["facts_added"] == 2


def test_unknown_event_type_silently_ignored(tmp_path, fixed_now, wide_window):
    """Forward-compat: future event types should not crash old binaries."""
    _write_events(str(tmp_path), [
        {"type": "fact.added", "ts": fixed_now.isoformat(), "payload": {"fact_id": "f1"}},
        {"type": "unknown.future_event_v3", "ts": fixed_now.isoformat(), "payload": {"foo": "bar"}},
        {"type": "reflection.synthesized", "ts": fixed_now.isoformat(), "payload": {"reflection_id": "r1"}},
    ])
    since, until = wide_window
    counts = _run(str(tmp_path), since, until)
    assert counts["facts_added"] == 1
    assert counts["reflections_synthesized"] == 1


def test_malformed_json_line_skipped_others_counted(tmp_path, fixed_now, wide_window):
    """A corrupt line in the middle must not abort the whole scan."""
    path = _events_file_for(str(tmp_path))
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "event_id": str(uuid.uuid4()),
            "type": "fact.added",
            "ts": fixed_now.isoformat(),
            "payload": {"fact_id": "f1"},
        }) + "\n")
        f.write("this-is-not-json{{{\n")
        f.write(json.dumps({
            "event_id": str(uuid.uuid4()),
            "type": "fact.added",
            "ts": fixed_now.isoformat(),
            "payload": {"fact_id": "f2"},
        }) + "\n")

    since, until = wide_window
    counts = _run(str(tmp_path), since, until)
    assert counts["facts_added"] == 2


def test_blank_lines_skipped(tmp_path, fixed_now, wide_window):
    path = _events_file_for(str(tmp_path))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n")
        f.write(json.dumps({
            "event_id": str(uuid.uuid4()),
            "type": "fact.added",
            "ts": fixed_now.isoformat(),
            "payload": {},
        }) + "\n")
        f.write("\n\n")

    since, until = wide_window
    counts = _run(str(tmp_path), since, until)
    assert counts["facts_added"] == 1


def test_event_with_unparseable_ts_skipped(tmp_path, fixed_now, wide_window):
    """A record missing or with malformed `ts` is skipped (can't filter into window)."""
    _write_events(str(tmp_path), [
        {"type": "fact.added", "ts": "not-a-timestamp", "payload": {"fact_id": "bad"}},
        {"type": "fact.added", "ts": fixed_now.isoformat(), "payload": {"fact_id": "ok"}},
    ])
    since, until = wide_window
    counts = _run(str(tmp_path), since, until)
    assert counts["facts_added"] == 1


def test_realistic_mixed_funnel_s24(tmp_path, fixed_now, wide_window):
    """RFC §8 S24: realistic 20-event funnel with mixed types/payloads.

    Typical lifecycle: 5 facts → 3 reflections synthesized → 2 confirmed →
    1 promoted (with persona.fact_added in main view) → 1 merged
    (with persona.entry_updated rewriting an existing entry) →
    1 reflection archived → 1 persona archive add.
    """
    ts = fixed_now.isoformat()
    events = [
        # 5 facts
        *[{"type": "fact.added", "ts": ts, "payload": {"fact_id": f"f{i}"}} for i in range(5)],
        # 3 reflections synthesized
        *[{"type": "reflection.synthesized", "ts": ts, "payload": {"reflection_id": f"r{i}"}} for i in range(3)],
        # 2 confirmed
        {"type": "reflection.state_changed", "ts": ts, "payload": {"reflection_id": "r0", "from": "pending", "to": "confirmed"}},
        {"type": "reflection.state_changed", "ts": ts, "payload": {"reflection_id": "r1", "from": "pending", "to": "confirmed"}},
        # 1 promoted
        {"type": "reflection.state_changed", "ts": ts, "payload": {"reflection_id": "r0", "from": "confirmed", "to": "promoted"}},
        # 1 persona main-view add (from promotion)
        {"type": "persona.fact_added", "ts": ts, "payload": {"entity_key": "master", "entry_id": "p0"}},
        # 1 merged
        {"type": "reflection.state_changed", "ts": ts, "payload": {"reflection_id": "r1", "from": "confirmed", "to": "merged"}},
        # 1 persona rewritten (target of the merge)
        {"type": "persona.entry_updated", "ts": ts, "payload": {"entity_key": "master", "entry_id": "p0", "merged_from_ids": ["r1"]}},
        # 1 reflection archived
        {"type": "reflection.state_changed", "ts": ts, "payload": {"reflection_id": "r2", "from": "pending", "to": "archived"}},
        # 1 persona archive add
        {"type": "persona.fact_added", "ts": ts, "payload": {"entity_key": "master", "entry_id": "p_old", "archive_shard_path": "persona_archive/2026-04-22_x.json"}},
    ]
    # 5 facts + 3 synth + 2 confirmed + 1 promoted + 1 persona-add + 1 merged
    # + 1 persona-rewritten + 1 reflection-archived + 1 persona-archive-add = 16
    assert len(events) == 16

    _write_events(str(tmp_path), events)
    since, until = wide_window
    counts = _run(str(tmp_path), since, until)

    expected = _empty_buckets() | {
        "facts_added": 5,
        "reflections_synthesized": 3,
        "reflections_confirmed": 2,
        "reflections_promoted": 1,
        "reflections_merged": 1,
        "reflections_denied": 0,
        "reflections_archived": 1,
        "persona_entries_added": 1,
        "persona_entries_rewritten": 1,
        "persona_entries_archived": 1,
    }
    assert counts == expected


def test_aware_window_bounds_do_not_raise_typeerror(tmp_path, fixed_now):
    """Regression: codex P1 — `datetime.fromisoformat("...Z")` returns aware
    in Py3.11+. Event-log `ts` is naive (local-clock from
    `datetime.now().isoformat()`). Without normalization, comparing
    aware bounds against naive parsed `ts` raises
    `TypeError: can't compare offset-naive and offset-aware datetimes`.

    `funnel_counts` must accept either flavor of bound and still produce
    the right counts.
    """
    _write_events(str(tmp_path), [
        {"type": "fact.added", "ts": fixed_now.isoformat(), "payload": {"fact_id": "f1"}},
        {"type": "fact.added", "ts": fixed_now.isoformat(), "payload": {"fact_id": "f2"}},
    ])
    # Aware bounds in UTC — what `datetime.fromisoformat("2026-04-22T...Z")`
    # would yield via the API.  Pick a window that, after astimezone() to
    # local clock, brackets `fixed_now` (which is naive local).
    aware_since = (fixed_now - timedelta(days=1)).replace(tzinfo=timezone.utc)
    aware_until = (fixed_now + timedelta(days=1)).replace(tzinfo=timezone.utc)
    # Should not raise; should at minimum not crash. Whether the event
    # falls inside depends on the local UTC offset, so we only assert the
    # structural invariants (no TypeError + 10 buckets present).
    counts = _run(str(tmp_path), aware_since, aware_until)
    assert set(counts.keys()) == set(_empty_buckets().keys())
    # facts_added is either 0 or 2 depending on tz offset; never partial.
    assert counts["facts_added"] in (0, 2)


def test_naive_and_aware_bounds_produce_same_count_when_window_covers_event(
    tmp_path, fixed_now,
):
    """When the window comfortably brackets the event in BOTH naive-local
    and aware-UTC views, both forms must produce identical counts."""
    _write_events(str(tmp_path), [
        {"type": "fact.added", "ts": fixed_now.isoformat(), "payload": {"fact_id": "f1"}},
    ])
    naive_since = fixed_now - timedelta(days=30)
    naive_until = fixed_now + timedelta(days=30)
    naive_counts = _run(str(tmp_path), naive_since, naive_until)

    # Same wall-clock instants but tagged UTC. After astimezone() to local
    # they collapse back onto the local naive window (since/until are
    # generous enough to swallow any offset on Earth).
    aware_since = naive_since.replace(tzinfo=timezone.utc)
    aware_until = naive_until.replace(tzinfo=timezone.utc)
    aware_counts = _run(str(tmp_path), aware_since, aware_until)

    assert naive_counts == aware_counts == _empty_buckets() | {"facts_added": 1}


def test_mixed_naive_until_and_aware_since_does_not_raise(tmp_path, fixed_now):
    """One bound naive, one bound aware. Per the normalization contract,
    both should be coerced to naive-local before comparison — no
    TypeError, even on the mixed-flavor input."""
    _write_events(str(tmp_path), [
        {"type": "fact.added", "ts": fixed_now.isoformat(), "payload": {"fact_id": "f1"}},
    ])
    aware_since = (fixed_now - timedelta(days=30)).replace(tzinfo=timezone.utc)
    naive_until = fixed_now + timedelta(days=30)
    counts = _run(str(tmp_path), aware_since, naive_until)
    assert counts["facts_added"] == 1


def test_aware_event_ts_compared_against_naive_window_does_not_raise(
    tmp_path, fixed_now,
):
    """Defensive: a future writer might store aware `ts` (e.g.
    `datetime.now(timezone.utc).isoformat()`). Naive window from caller
    must still work — `_parse_ts` normalizes per-event."""
    aware_ts = fixed_now.replace(tzinfo=timezone.utc).isoformat()
    _write_events(str(tmp_path), [
        {"type": "fact.added", "ts": aware_ts, "payload": {"fact_id": "f1"}},
    ])
    naive_since = fixed_now - timedelta(days=30)
    naive_until = fixed_now + timedelta(days=30)
    counts = _run(str(tmp_path), naive_since, naive_until)
    # Window is wide enough that the event falls inside regardless of
    # local-vs-UTC offset.
    assert counts["facts_added"] == 1


def test_per_character_isolation(tmp_path, fixed_now, wide_window):
    """Each character has its own events.ndjson; scanning one must not
    pick up the other's events."""
    _write_events(str(tmp_path), [
        {"type": "fact.added", "ts": fixed_now.isoformat(), "payload": {"fact_id": "a1"}},
    ], name="角色A")
    _write_events(str(tmp_path), [
        {"type": "fact.added", "ts": fixed_now.isoformat(), "payload": {"fact_id": "b1"}},
        {"type": "fact.added", "ts": fixed_now.isoformat(), "payload": {"fact_id": "b2"}},
    ], name="角色B")
    since, until = wide_window
    assert _run(str(tmp_path), since, until, name="角色A")["facts_added"] == 1
    assert _run(str(tmp_path), since, until, name="角色B")["facts_added"] == 2
