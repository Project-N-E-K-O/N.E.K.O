# -*- coding: utf-8 -*-
"""
Journal compaction against the real memory stack (managers, archive
shards, the production evidence handlers).

  - after a compaction, reconciliation is a no-op and the views are
    byte-for-byte unchanged, also when the sentinel is lost afterwards and
    the whole remaining body is replayed (zero retention window);
  - an older binary (only the five v0.9.0 evidence handlers) reads a
    compacted journal exactly like the uncompacted one, paused tail
    included;
  - restore -> re-archive -> lost shard append -> sentinel moved on: the
    second archival exists only in the journal, and compaction keeps it
    (reflection and persona, same flow).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio

NAME = "小天"


def _mock_cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    cm.load_root_state.return_value = {"mode": "normal"}
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    return cm


def _install(tmpdir: str):
    from memory.event_log import EventLog
    from memory.facts import FactStore
    from memory.persona import PersonaManager
    from memory.reflection import ReflectionEngine

    cm = _mock_cm(tmpdir)
    with patch("memory.event_log.get_config_manager", return_value=cm), \
         patch("memory.facts.get_config_manager", return_value=cm), \
         patch("memory.persona.manager.get_config_manager", return_value=cm), \
         patch("memory.reflection.manager.get_config_manager", return_value=cm):
        event_log = EventLog()
        event_log._config_manager = cm
        fs = FactStore()
        fs._config_manager = cm
        pm = PersonaManager(event_log=event_log)
        pm._config_manager = cm
        re = ReflectionEngine(fs, pm, event_log=event_log)
        re._config_manager = cm
    return event_log, pm, re


def _reconciler(event_log, pm, re):
    """The v0.9.0 handler set: the five evidence/lifecycle handlers only."""
    from memory.event_log import Reconciler
    from memory.evidence_handlers import register_evidence_handlers

    rec = Reconciler(event_log)
    register_evidence_handlers(rec, pm, re)
    return rec


@contextmanager
def _zero_window():
    from memory import event_log as m

    with patch.object(m, "_COMPACT_TRIGGER_BYTES", 1), \
            patch.object(m, "_RETAIN_APPLIED_DAYS", 0), \
            patch.object(m, "_RETAIN_APPLIED_MAX_BYTES", 0):
        yield


def _reflection(rid: str) -> dict:
    now_iso = datetime.now().isoformat()
    return {
        "id": rid, "text": f"反思 {rid}", "entity": "master", "status": "confirmed",
        "source_fact_ids": [], "created_at": now_iso, "feedback": "confirmed",
        "reinforcement": 0.0, "disputation": 0.0,
        "rein_last_signal_at": None, "disp_last_signal_at": None,
        "sub_zero_days": 0, "sub_zero_last_increment_date": None,
        "user_fact_reinforce_count": 0, "protected": False,
        "last_promote_attempt_at": None, "promote_attempt_count": 0,
    }


def _persona_entry(eid: str) -> dict:
    return {
        "id": eid, "text": f"条目 {eid}",
        "reinforcement": 0.0, "disputation": 0.0,
        "rein_last_signal_at": None, "disp_last_signal_at": None,
        "sub_zero_days": 0, "user_fact_reinforce_count": 0,
        "merged_from_ids": [], "protected": False,
        "suppress": False, "suppressed_at": None, "recent_mentions": [],
        "source": "manual", "source_id": None,
    }


def _snapshot_views(char_dir: str) -> dict[str, bytes]:
    """Every file in the character dir except the journal and the sentinel."""
    out: dict[str, bytes] = {}
    for root, _, files in os.walk(char_dir):
        for fn in files:
            if fn in ("events.ndjson", "events_applied.json"):
                continue
            path = os.path.join(root, fn)
            with open(path, "rb") as f:
                out[os.path.relpath(path, char_dir)] = f.read()
    return out


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _journal_ids(char_dir: str) -> list[str]:
    ids = []
    with open(os.path.join(char_dir, "events.ndjson"), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                ids.append(json.loads(line)["event_id"])
    return ids


async def _build_history(event_log, pm, re) -> None:
    await re.asave_reflections(NAME, [_reflection("r1"), _reflection("r2")])
    await pm.asave_persona(NAME, {"master": {"facts": [_persona_entry("p1"), _persona_entry("p2")]}})
    for _ in range(3):
        assert await re.aapply_signal(NAME, "r1", {"reinforcement": 0.5}, "user_fact")
        assert await pm.aapply_signal(NAME, "master", "p1", {"reinforcement": 0.5}, "user_fact")
    assert await re.aarchive_reflection(NAME, "r2")
    assert await pm.aarchive_persona_entry(NAME, "master", "p2")
    assert await re.aapply_signal(NAME, "r1", {"reinforcement": 0.25}, "user_fact")


async def test_compacted_journal_reconciles_to_nothing_and_survives_a_lost_sentinel(tmp_path):
    from memory.evidence_handlers import make_archive_copy_check

    event_log, pm, re = _install(str(tmp_path))
    char_dir = os.path.join(str(tmp_path), NAME)
    await _build_history(event_log, pm, re)
    assert await _reconciler(event_log, pm, re).areconcile(NAME) == 0
    views = _snapshot_views(char_dir)
    assert any(k.startswith("reflection_archive") for k in views)
    assert any(k.startswith("persona_archive") for k in views)
    lines_before = len(_journal_ids(char_dir))

    with _zero_window():
        result = await event_log.acompact_applied_history(
            NAME, needs_journal_copy=make_archive_copy_check(char_dir),
        )
    assert result.outcome == "compacted"
    assert result.pinned == 0, "分片都写成功了，不该有钉住行"
    assert len(_journal_ids(char_dir)) == 1 < lines_before

    event_log2, pm2, re2 = _install(str(tmp_path))
    assert await _reconciler(event_log2, pm2, re2).areconcile(NAME) == 0
    assert _snapshot_views(char_dir) == views

    os.remove(os.path.join(char_dir, "events_applied.json"))
    event_log3, pm3, re3 = _install(str(tmp_path))
    assert await _reconciler(event_log3, pm3, re3).areconcile(NAME) == 0
    assert _snapshot_views(char_dir) == views, "哨兵丢失后的全量重放改动了视图"


async def test_old_binary_reads_a_compacted_journal_like_the_original(tmp_path):
    """Downgrade: the five-handler set, no long-term replay handler. A tail
    that pauses on an event this set cannot replay pauses at the same place
    whether or not the journal was compacted."""
    from memory.event_log import EVT_LONG_TERM_MEMORY_REPLACED, EVT_REFLECTION_EVIDENCE_UPDATED
    from memory.evidence_handlers import make_archive_copy_check

    root_a = os.path.join(str(tmp_path), "compacted")
    root_b = os.path.join(str(tmp_path), "original")
    os.makedirs(root_a)
    event_log, pm, re = _install(root_a)
    await _build_history(event_log, pm, re)
    # A long-term snapshot event in front of the anchor: dropped by compaction.
    event_log.append(NAME, EVT_LONG_TERM_MEMORY_REPLACED,
                     {"characterSyncId": "u", "inboxSha256": "0" * 64})
    assert await re.aapply_signal(NAME, "r1", {"reinforcement": 0.125}, "user_fact")
    # Unapplied tail: an event v0.9.0 cannot replay, then one it can.
    event_log.append(NAME, EVT_LONG_TERM_MEMORY_REPLACED,
                     {"characterSyncId": "u", "inboxSha256": "1" * 64})
    event_log.append(NAME, EVT_REFLECTION_EVIDENCE_UPDATED,
                     {"reflection_id": "r1", "reinforcement": 9.0})
    shutil.copytree(root_a, root_b)

    char_a = os.path.join(root_a, NAME)
    with _zero_window():
        result = event_log.compact_applied_history(
            NAME, needs_journal_copy=make_archive_copy_check(char_a),
        )
    assert result.outcome == "compacted"
    assert result.pending_tail == 2

    outcomes = {}
    for root in (root_a, root_b):
        el, p, r = _install(root)
        sentinel_before = _read(os.path.join(root, NAME, "events_applied.json"))
        applied = await _reconciler(el, p, r).areconcile(NAME)
        outcomes[root] = (
            applied,
            el.read_sentinel(NAME),
            [e["event_id"] for e in el.read_since(NAME, el.read_sentinel(NAME))],
            _read(os.path.join(root, NAME, "events_applied.json")) == sentinel_before,
            _snapshot_views(os.path.join(root, NAME)),
        )
    assert outcomes[root_a] == outcomes[root_b]
    applied, _, tail, sentinel_untouched, _ = outcomes[root_a]
    assert applied == 0 and sentinel_untouched and len(tail) == 2


@pytest.mark.parametrize("side", ["reflection", "persona"])
async def test_reachived_entry_whose_shard_append_failed_is_pinned(tmp_path, side):
    """H1 regression, both archive producers.

    1. X is archived (A1); its shard gets copy X@t1.
    2. X comes back to the active view (subject restore keeps the shard copy).
    3. X is archived again (A2, X@t2); the shard append after the event fails.
    4. A later live write moves the sentinel past A2.
    The shards still hold an X, but not X@t2: A2 is the only copy and must
    survive compaction, A1 may go.
    """
    from memory.evidence_handlers import make_archive_copy_check

    event_log, pm, re = _install(str(tmp_path))
    char_dir = os.path.join(str(tmp_path), NAME)
    await re.asave_reflections(NAME, [_reflection("anchor-r"), _reflection("x")])
    await pm.asave_persona(NAME, {"master": {"facts": [_persona_entry("x")]}})

    async def archive():
        if side == "reflection":
            return await re.aarchive_reflection(NAME, "x")
        return await pm.aarchive_persona_entry(NAME, "master", "x")

    async def restore():
        if side == "reflection":
            full = await re._aload_reflections_full(NAME)
            await re.asave_reflections(NAME, full + [_reflection("x")])
        else:
            await pm.asave_persona(NAME, {"master": {"facts": [_persona_entry("x")]}})

    assert await archive()
    first_ids = _journal_ids(char_dir)
    a1 = first_ids[-1]
    await restore()
    await asyncio.sleep(0.02)  # distinct archived_at even on a coarse clock

    async def lost_append(*_a, **_k):
        raise OSError("disk full")

    with patch("memory.archive_shards.aappend_to_shard", lost_append):
        with pytest.raises(OSError):
            await archive()
    a2 = _journal_ids(char_dir)[-1]
    assert a2 != a1
    assert await re.aapply_signal(NAME, "anchor-r", {"reinforcement": 0.5}, "user_fact")

    with _zero_window():
        result = await event_log.acompact_applied_history(
            NAME, needs_journal_copy=make_archive_copy_check(char_dir),
        )
    assert result.outcome == "compacted"
    assert result.pinned == 1 and result.pinned_event_ids == (a2,)
    remaining = _journal_ids(char_dir)
    assert a2 in remaining, "唯一副本被压缩删掉了"
    assert a1 not in remaining
    assert remaining.index(a2) < remaining.index(event_log.read_sentinel(NAME))
