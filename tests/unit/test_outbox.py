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
    """核心用例：模拟 extract_facts LLM 调用途中进程被 kill。

    进程 A 登记 pending，未 append_done 就"挂了"；进程 B 启动，
    apending_ops 应当能看到未完成的 op，提供补跑依据。
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
    """中途损坏的一行不应让整个 pending_ops 挂掉。"""
    ob = _fresh_outbox(str(tmp_path))
    id1 = ob.append_pending("小天", "extract_facts", {"k": 1})
    # 在两条有效记录之间塞一行损坏数据
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
    # 3 pending + 3 done = 6 已完成行应被全部丢弃；1 live 保留
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
    # 远低于阈值 (_COMPACT_LINES_THRESHOLD=1000)
    assert ob.maybe_compact("小天") == 0
    # 文件还在，只是没压
    assert os.path.exists(ob._outbox_path("小天"))


def test_payload_unicode_roundtrip(tmp_path):
    """payload 中的中文/emoji 不应被 ensure_ascii 破坏。"""
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
    """并发 aappend_pending 20 次：每条记录都要完整写入、无交叉。"""
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
    """compact 后的文件，新进程读 pending_ops 仍然正确。"""
    ob = _fresh_outbox(str(tmp_path))
    # 3 完成 + 2 进行中
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
    # payload 内容未损
    tags = {r["payload"]["tag"] for r in pending}
    assert tags == {"L1", "L2"}


def test_file_path_matches_character_dir_convention(tmp_path):
    """outbox 与 facts.json / reflections.json 同目录，便于审阅。"""
    ob = _fresh_outbox(str(tmp_path))
    ob.append_pending("小天", "extract_facts", {})
    expected = os.path.join(str(tmp_path), "小天", "outbox.ndjson")
    assert os.path.exists(expected)
