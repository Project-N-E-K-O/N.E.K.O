# -*- coding: utf-8 -*-
"""
Unit tests for memory.outbox.Outbox.

Focus: P1.b — persistent job queue that survives process kill.
Verifies: append/done pairing, pending_ops correctness across restart,
corruption tolerance, compact semantics, async duality.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest


def _fresh_outbox(tmpdir: str):
    from memory.outbox import Outbox

    mock_cm = MagicMock()
    mock_cm.memory_dir = tmpdir
    with patch("memory.outbox.get_config_manager", return_value=mock_cm):
        ob = Outbox()
    ob._config_manager = mock_cm
    return ob


def test_append_pending_returns_unique_op_id(tmp_path):
    ob = _fresh_outbox(str(tmp_path))
    id1 = ob.append_pending("小天", "extract_facts", {"msgs": [1, 2]})
    id2 = ob.append_pending("小天", "extract_facts", {"msgs": [3, 4]})
    assert id1 != id2


def test_pending_before_done_shows_up(tmp_path):
    ob = _fresh_outbox(str(tmp_path))
    op_id = ob.append_pending("小天", "extract_facts", {"payload": "x"})
    pending = ob.pending_ops("小天")
    assert len(pending) == 1
    assert pending[0]["op_id"] == op_id
    assert pending[0]["type"] == "extract_facts"


def test_done_cancels_pending(tmp_path):
    ob = _fresh_outbox(str(tmp_path))
    op_id = ob.append_pending("小天", "extract_facts", {"p": 1})
    ob.append_done("小天", op_id)
    assert ob.pending_ops("小天") == []


def test_pending_persists_across_fresh_instances(tmp_path):
    """Core case: process killed during extract_facts LLM call.

    Process A registers pending and dies before append_done; process B starts,
    and apending_ops must expose the unfinished operation for replay.
    """
    ob_a = _fresh_outbox(str(tmp_path))
    op_id = ob_a.append_pending("小天", "extract_facts", {"msg_count": 3})

    ob_b = _fresh_outbox(str(tmp_path))
    pending = ob_b.pending_ops("小天")
    assert len(pending) == 1
    assert pending[0]["op_id"] == op_id
    assert pending[0]["payload"] == {"msg_count": 3}


def test_multiple_characters_isolated(tmp_path):
    ob = _fresh_outbox(str(tmp_path))
    id_a = ob.append_pending("小天", "extract_facts", {})
    id_b = ob.append_pending("小雪", "extract_facts", {})
    assert {r["op_id"] for r in ob.pending_ops("小天")} == {id_a}
    assert {r["op_id"] for r in ob.pending_ops("小雪")} == {id_b}


def test_corrupt_line_is_skipped(tmp_path):
    """A corrupt intermediate row must not break all pending_ops."""
    ob = _fresh_outbox(str(tmp_path))
    id1 = ob.append_pending("小天", "extract_facts", {"k": 1})
    # Insert corrupt row between two valid records.
    path = ob._outbox_path("小天")
    with open(path, "a", encoding="utf-8") as f:
        f.write("this is not json\n")
    id2 = ob.append_pending("小天", "extract_facts", {"k": 2})

    pending = ob.pending_ops("小天")
    assert {r["op_id"] for r in pending} == {id1, id2}


def test_compact_drops_completed_pairs(tmp_path):
    ob = _fresh_outbox(str(tmp_path))
    finished = [ob.append_pending("小天", "extract_facts", {"i": i}) for i in range(3)]
    for op in finished:
        ob.append_done("小天", op)
    live = ob.append_pending("小天", "extract_facts", {"i": "unfinished"})

    dropped = ob.compact("小天")
    # Three pending + three done = six completed rows dropped; retain one live operation.
    assert dropped == 6
    pending = ob.pending_ops("小天")
    assert len(pending) == 1
    assert pending[0]["op_id"] == live


def test_compact_on_empty_file_is_noop(tmp_path):
    ob = _fresh_outbox(str(tmp_path))
    assert ob.compact("小天") == 0


def test_maybe_compact_below_threshold(tmp_path):
    ob = _fresh_outbox(str(tmp_path))
    for _ in range(5):
        op = ob.append_pending("小天", "x", {})
        ob.append_done("小天", op)
    # Far below threshold (_COMPACT_LINES_THRESHOLD=1000).
    assert ob.maybe_compact("小天") == 0
    # File remains, just uncompacted.
    assert os.path.exists(ob._outbox_path("小天"))


def test_payload_unicode_roundtrip(tmp_path):
    """ensure_ascii must not corrupt Chinese/emoji in payloads."""
    ob = _fresh_outbox(str(tmp_path))
    payload = {"text": "主人喜欢咖啡 ☕", "nested": {"k": "值"}}
    op_id = ob.append_pending("小天", "extract_facts", payload)
    fresh = _fresh_outbox(str(tmp_path))
    pending = fresh.pending_ops("小天")
    assert pending[0]["payload"] == payload
    assert pending[0]["op_id"] == op_id


@pytest.mark.asyncio
async def test_async_append_and_scan(tmp_path):
    ob = _fresh_outbox(str(tmp_path))
    op_id = await ob.aappend_pending("小天", "synth_reflection", {"fact_ids": ["f1", "f2"]})
    pending = await ob.apending_ops("小天")
    assert len(pending) == 1
    assert pending[0]["op_id"] == op_id
    await ob.aappend_done("小天", op_id)
    assert await ob.apending_ops("小天") == []


@pytest.mark.asyncio
async def test_concurrent_async_appends_serialize_correctly(tmp_path):
    """Twenty concurrent aappend_pending calls each write complete records without interleaving."""
    ob = _fresh_outbox(str(tmp_path))

    async def _append(i: int):
        return await ob.aappend_pending("小天", "extract_facts", {"i": i})

    ids = await asyncio.gather(*(_append(i) for i in range(20)))
    assert len(set(ids)) == 20
    pending = await ob.apending_ops("小天")
    assert len(pending) == 20
    assert {r["op_id"] for r in pending} == set(ids)


@pytest.mark.asyncio
async def test_compact_preserves_pending_across_restart(tmp_path):
    """After compaction, a new process still reads pending_ops correctly."""
    ob = _fresh_outbox(str(tmp_path))
    # Three completed + two in progress.
    for i in range(3):
        op = await ob.aappend_pending("小天", "x", {"i": i})
        await ob.aappend_done("小天", op)
    live1 = await ob.aappend_pending("小天", "synth_reflection", {"tag": "L1"})
    live2 = await ob.aappend_pending("小天", "synth_reflection", {"tag": "L2"})

    dropped = await ob.acompact("小天")
    assert dropped == 6

    fresh = _fresh_outbox(str(tmp_path))
    pending = await fresh.apending_ops("小天")
    assert {r["op_id"] for r in pending} == {live1, live2}
    # Payload content intact.
    tags = {r["payload"]["tag"] for r in pending}
    assert tags == {"L1", "L2"}


def test_file_path_matches_character_dir_convention(tmp_path):
    """Outbox shares directory with facts.json / reflections.json for review."""
    ob = _fresh_outbox(str(tmp_path))
    ob.append_pending("小天", "extract_facts", {})
    expected = os.path.join(str(tmp_path), "小天", "outbox.ndjson")
    assert os.path.exists(expected)
