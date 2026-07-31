# -*- coding: utf-8 -*-
"""Unit tests for memory.subject_archive — time-driven scoped subject archival.

Contracts under test (group-memory series 5/7, mainline 1):

  1. Last-write derivation: per-subject max(created_at / confirmed_at) over
     the full pools; subjects with zero parseable timestamps are fail-closed
     excluded; partial/corrupt stamps never enter the map.
  2. Staleness boundary: strictly-greater-than N days; exactly N days is NOT
     stale; a future last write (clock rollback) is NOT stale.
  3. The sweep archives ALL THREE stores of a stale subject (facts move to
     facts_archive.json with subject_archived_at; reflections through the
     event-sourced shard path; persona entries through the shard path) and
     leaves active subjects untouched.
  4. Dry-run moves nothing. A second sweep over an already-archived subject
     is a no-op (idempotence via emptiness).
  5. Archived-subject facts leave the recall archive pool while
     absorbed-archived rows stay in it; the FTS near-dup guard lets a
     revived subject re-state an archived fact as a NEW active fact.
  6. Restore round-trips all three stores and never resurrects age-based
     terminal (promoted/denied) shard entries.
  7. The archive-sweep loop stage is throttled and gated by
     SCOPED_SUBJECT_ARCHIVE_ENABLED.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.scopes import MemorySubject
from memory.subject_archive import (
    arestore_scoped_subject,
    asweep_scoped_subject_archive,
    collect_subject_last_writes,
    find_stale_subjects,
)


NOW = datetime(2026, 7, 1, 12, 0, 0)
STALE_DAYS = 90


# ── shared fixtures (mirroring tests/unit/test_evidence_archive.py) ──


def _mock_cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    return cm


def _install(tmpdir: str):
    from memory.event_log import EventLog, Reconciler
    from memory.evidence_handlers import register_evidence_handlers
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
        rec = Reconciler(event_log)
        register_evidence_handlers(rec, pm, re)
    return event_log, fs, pm, re, rec, cm


SUBJ_STALE = MemorySubject.group_chat("qq", "111")
SUBJ_ACTIVE = MemorySubject.group_chat("qq", "222")


def _scoped_fact(fid: str, text: str, subject: MemorySubject, *,
                 created_at: str, absorbed: bool = False) -> dict:
    return {
        "id": fid,
        "text": text,
        "entity": subject.kind,
        "importance": 6,
        "tags": [],
        "hash": fid + "h",
        "created_at": created_at,
        "absorbed": absorbed,
        "signal_processed": True,
        **subject.as_entry_fields(),
    }


def _scoped_reflection(rid: str, text: str, subject: MemorySubject, *,
                       created_at: str) -> dict:
    return {
        "id": rid,
        "text": text,
        "entity": subject.kind,
        "status": "confirmed",
        "confirmed_at": created_at,
        "auto_confirmed": True,
        "source_fact_ids": [],
        "created_at": created_at,
        "reinforcement": 0.1,
        "rein_last_signal_at": created_at,
        **subject.as_entry_fields(),
    }


async def _seed_persona_entry(pm, name: str, subject: MemorySubject,
                              text: str) -> str:
    """Append one scoped persona entry through the real section helper."""
    persona = await pm.aensure_persona(name)
    section = pm._get_section_facts(persona, subject.kind, subject=subject)
    entry = pm._build_fact_entry(
        text, 'reflection_time_driven', None, subject=subject,
    )
    section.append(entry)
    await pm.asave_persona(name, persona)
    return entry['id']


def _iso(days_before_now: float) -> str:
    return (NOW - timedelta(days=days_before_now)).isoformat()


# ── 判据纯函数 ────────────────────────────────────────────────────────


def test_last_write_takes_max_across_stores():
    fact_old = _scoped_fact("f1", "a", SUBJ_STALE, created_at=_iso(120))
    refl_newer = _scoped_reflection("r1", "b", SUBJ_STALE, created_at=_iso(50))
    last, no_ts = collect_subject_last_writes([[fact_old], [refl_newer]])
    marker = (SUBJ_STALE.key, SUBJ_STALE.scope)
    assert marker in last
    assert last[marker][1] == NOW - timedelta(days=50)
    assert no_ts == set()


def test_confirmed_at_counts_toward_last_write():
    refl = _scoped_reflection("r1", "b", SUBJ_STALE, created_at=_iso(120))
    refl["confirmed_at"] = _iso(30)
    last, _ = collect_subject_last_writes([[refl]])
    assert last[(SUBJ_STALE.key, SUBJ_STALE.scope)][1] == NOW - timedelta(days=30)


def test_no_timestamp_subject_fail_closed():
    fact = _scoped_fact("f1", "a", SUBJ_STALE, created_at="not-a-date")
    fact.pop("confirmed_at", None)
    last, no_ts = collect_subject_last_writes([[fact]])
    assert (SUBJ_STALE.key, SUBJ_STALE.scope) not in last
    assert (SUBJ_STALE.key, SUBJ_STALE.scope) in no_ts
    # 无时间戳 subject 绝不进 stale 名单。
    assert find_stale_subjects(last, now=NOW, stale_days=STALE_DAYS) == []


def test_partial_stamp_rows_ignored():
    row = {"id": "x", "text": "t", "created_at": _iso(400),
           "subject_kind": "group_chat"}  # 缺 subject_id/scope → 孤儿行
    last, no_ts = collect_subject_last_writes([[row]])
    assert last == {}
    assert no_ts == set()


@pytest.mark.parametrize("age_days,expect_stale", [
    (STALE_DAYS - 1, False),   # N-1 天：不动
    (STALE_DAYS, False),       # 恰好 N 天：不动（判据是严格大于）
    (STALE_DAYS + 1, True),    # N+1 天：归档
    (-1, False),               # 未来时间（时钟回拨）：绝不归档
    (-(STALE_DAYS + 10), False),  # 大幅回拨：|age|>N 也不归档（杀 abs 变体）
])
def test_stale_boundary(age_days, expect_stale):
    fact = _scoped_fact("f1", "a", SUBJ_STALE, created_at=_iso(age_days))
    last, _ = collect_subject_last_writes([[fact]])
    stale = find_stale_subjects(last, now=NOW, stale_days=STALE_DAYS)
    assert bool(stale) is expect_stale


def test_stale_boundary_one_second_past_n_days():
    # 超出哪怕 1 秒也算「超过 N 天」——钉死 > 与 >= 的边界。
    fact = _scoped_fact(
        "f1", "a", SUBJ_STALE,
        created_at=(NOW - timedelta(days=STALE_DAYS, seconds=1)).isoformat(),
    )
    last, _ = collect_subject_last_writes([[fact]])
    assert find_stale_subjects(last, now=NOW, stale_days=STALE_DAYS)


# ── sweep 集成（真实三存储栈 + tmp_path IO） ─────────────────────────


async def _seed_two_subjects(fs, pm, re, name: str = "小天"):
    """Seed SUBJ_STALE with writes >90 days old and SUBJ_ACTIVE at 10 days."""
    fs._facts[name] = [
        _scoped_fact("fs1", "陈年群事实一", SUBJ_STALE, created_at=_iso(120)),
        _scoped_fact("fs2", "陈年群事实二", SUBJ_STALE,
                     created_at=_iso(STALE_DAYS + 1)),
        _scoped_fact("fa1", "活跃群事实", SUBJ_ACTIVE, created_at=_iso(10)),
    ]
    await fs.asave_facts(name)
    await re.asave_reflections(name, [
        _scoped_reflection("rs1", "陈年群反思", SUBJ_STALE,
                           created_at=_iso(100)),
        _scoped_reflection("ra1", "活跃群反思", SUBJ_ACTIVE,
                           created_at=_iso(9)),
    ])
    stale_pid = await _seed_persona_entry(pm, name, SUBJ_STALE, "陈年群人设条目")
    active_pid = await _seed_persona_entry(pm, name, SUBJ_ACTIVE, "活跃群人设条目")
    return stale_pid, active_pid


def _sweep_kwargs(fs, pm, re):
    return dict(
        fact_store=fs, persona_manager=pm, reflection_engine=re,
        now=NOW, stale_days=STALE_DAYS, dry_run=False,
    )


@pytest.mark.asyncio
async def test_sweep_archives_all_three_stores_and_spares_active(tmp_path):
    _, fs, pm, re, _, _ = _install(str(tmp_path))
    stale_pid, active_pid = await _seed_two_subjects(fs, pm, re)

    report = await asweep_scoped_subject_archive(
        "小天", **_sweep_kwargs(fs, pm, re),
    )
    key = f"{SUBJ_STALE.key}|{SUBJ_STALE.scope}"
    assert report['archived'][key]['facts'] == 2
    assert report['archived'][key]['reflections'] == 1
    assert report['archived'][key]['persona_entries'] == 1
    assert len(report['archived']) == 1  # 活跃 subject 不在归档名单

    # facts：活跃池只剩 active subject；归档文件带 subject_archived_at 标记。
    active = await fs.aload_facts("小天")
    assert {f['id'] for f in active} == {"fa1"}
    with open(os.path.join(str(tmp_path), "小天", "facts_archive.json"),
              encoding="utf-8") as f:
        archived_rows = json.load(f)
    archived_ids = {r['id'] for r in archived_rows}
    assert archived_ids == {"fs1", "fs2"}
    assert all(r.get('subject_archived_at') for r in archived_rows)
    # 原文与 created_at 原样保留（归档不是删除）。
    assert any(r['text'] == "陈年群事实一" for r in archived_rows)

    # reflections：stale 的物理离开主文件，active 的还在。
    refls = await re._aload_reflections_full("小天")
    assert {r['id'] for r in refls} == {"ra1"}

    # persona：stale 条目离开 section，active 条目还在。
    persona = await pm.aensure_persona("小天")
    remaining_ids = {
        e.get('id')
        for sec in persona.values() if isinstance(sec, dict)
        for e in sec.get('facts', []) if isinstance(e, dict)
    }
    assert stale_pid not in remaining_ids
    assert active_pid in remaining_ids


@pytest.mark.asyncio
async def test_sweep_dry_run_moves_nothing(tmp_path):
    _, fs, pm, re, _, _ = _install(str(tmp_path))
    stale_pid, _ = await _seed_two_subjects(fs, pm, re)
    kwargs = _sweep_kwargs(fs, pm, re)
    kwargs['dry_run'] = True

    report = await asweep_scoped_subject_archive("小天", **kwargs)
    key = f"{SUBJ_STALE.key}|{SUBJ_STALE.scope}"
    assert report['dry_run'] is True
    assert report['archived'][key]['facts'] == 2

    # 三个存储原样未动。
    assert {f['id'] for f in await fs.aload_facts("小天")} == {"fs1", "fs2", "fa1"}
    assert not os.path.exists(
        os.path.join(str(tmp_path), "小天", "facts_archive.json"),
    )
    assert {r['id'] for r in await re._aload_reflections_full("小天")} == {"rs1", "ra1"}
    persona = await pm.aensure_persona("小天")
    all_ids = {
        e.get('id')
        for sec in persona.values() if isinstance(sec, dict)
        for e in sec.get('facts', []) if isinstance(e, dict)
    }
    assert stale_pid in all_ids


@pytest.mark.asyncio
async def test_sweep_second_run_is_noop(tmp_path):
    _, fs, pm, re, _, _ = _install(str(tmp_path))
    await _seed_two_subjects(fs, pm, re)
    kwargs = _sweep_kwargs(fs, pm, re)
    await asweep_scoped_subject_archive("小天", **kwargs)
    report2 = await asweep_scoped_subject_archive("小天", **kwargs)
    # 归档后的 subject 仍是 stale（last_write 从归档行推导不变），但已无
    # 活跃条目可归 → 静默跳过，不再出现在 archived 名单里。
    assert report2['archived'] == {}


@pytest.mark.asyncio
async def test_sweep_respects_stale_days_override(tmp_path):
    """Mutation guard in both directions: tightening the criterion
    (stale_days=8) must archive the 10-day-old subject too, and loosening
    it (stale_days=365) must leave everything untouched."""
    _, fs, pm, re, _, _ = _install(str(tmp_path))
    await _seed_two_subjects(fs, pm, re)
    kwargs = _sweep_kwargs(fs, pm, re)

    kwargs['stale_days'] = 365
    report = await asweep_scoped_subject_archive("小天", **kwargs)
    assert report['archived'] == {}
    assert {f['id'] for f in await fs.aload_facts("小天")} == {"fs1", "fs2", "fa1"}

    kwargs['stale_days'] = 8
    report = await asweep_scoped_subject_archive("小天", **kwargs)
    assert set(report['archived']) == {
        f"{SUBJ_STALE.key}|{SUBJ_STALE.scope}",
        f"{SUBJ_ACTIVE.key}|{SUBJ_ACTIVE.scope}",
    }
    assert await fs.aload_facts("小天") == []


@pytest.mark.asyncio
async def test_last_write_derivation_reads_full_pool_including_archive(tmp_path):
    """The absorbed-shrink path moves recent facts into facts_archive.json,
    so last-write MUST be derived from the FULL pool (active + archive).
    An active-pool-only implementation would misjudge a subject as stale
    when its recent writes were all absorbed and archived."""
    from utils.file_utils import atomic_write_json

    _, fs, pm, re, _, _ = _install(str(tmp_path))
    # 活跃池只剩一条 120 天前的旧 fact。
    fs._facts["小天"] = [
        _scoped_fact("f_old", "旧事实", SUBJ_STALE, created_at=_iso(120)),
    ]
    await fs.asave_facts("小天")
    # 10 天前的新 fact 已被 absorbed 收缩搬进归档文件（无 subject_archived_at）。
    recent_absorbed = _scoped_fact(
        "f_recent", "新近但已吸收的事实", SUBJ_STALE,
        created_at=_iso(10), absorbed=True,
    )
    atomic_write_json(
        fs._facts_archive_path("小天"), [recent_absorbed],
        indent=2, ensure_ascii=False,
    )

    report = await asweep_scoped_subject_archive(
        "小天", **_sweep_kwargs(fs, pm, re),
    )
    assert report['archived'] == {}  # 全池 max = 10 天前 → 不 stale
    assert {f['id'] for f in await fs.aload_facts("小天")} == {"f_old"}


@pytest.mark.asyncio
async def test_fact_archival_aborts_on_fresh_write_in_window(tmp_path):
    """Race guard: a fact written AFTER the sweep judged the subject stale
    but BEFORE the fact store took its write lock means the subject just
    revived — the whole fact archival must abort instead of sweeping the
    subject's first fresh memory out of recall."""
    _, fs, pm, re, _, _ = _install(str(tmp_path))
    fs._facts["小天"] = [
        _scoped_fact("fs1", "陈年一", SUBJ_STALE, created_at=_iso(120)),
        _scoped_fact("fs2", "陈年二", SUBJ_STALE, created_at=_iso(100)),
        # 判定后、加锁前落进来的新写入。
        _scoped_fact("fresh", "复活后的新事实", SUBJ_STALE, created_at=_iso(1)),
    ]
    await fs.asave_facts("小天")
    cutoff = NOW - timedelta(days=STALE_DAYS)
    moved = await fs.aarchive_subject_facts(
        "小天", SUBJ_STALE, NOW.isoformat(), cutoff,
    )
    # None = 复活中止（区别于普通零结果），caller 要跳过其余存储。
    assert moved is None
    assert {f['id'] for f in await fs.aload_facts("小天")} == {"fs1", "fs2", "fresh"}
    assert not os.path.exists(fs._facts_archive_path("小天"))


@pytest.mark.asyncio
async def test_fact_archival_aborts_when_archive_top_level_is_not_list(tmp_path):
    """A valid JSON object must not be overwritten as an empty archive."""
    from utils.file_utils import atomic_write_json

    _, fs, pm, re, _, _ = _install(str(tmp_path))
    fs._facts["小天"] = [
        _scoped_fact("fs1", "陈年事实", SUBJ_STALE, created_at=_iso(120)),
    ]
    await fs.asave_facts("小天")
    malformed = {"unexpected": "object"}
    atomic_write_json(
        fs._facts_archive_path("小天"), malformed,
        indent=2, ensure_ascii=False,
    )

    moved = await fs.aarchive_subject_facts(
        "小天", SUBJ_STALE, NOW.isoformat(),
        NOW - timedelta(days=STALE_DAYS),
    )

    assert moved is None
    assert {f['id'] for f in await fs.aload_facts("小天")} == {"fs1"}
    with open(fs._facts_archive_path("小天"), encoding="utf-8") as fh:
        assert json.load(fh) == malformed


@pytest.mark.asyncio
async def test_sweep_skips_other_stores_when_fact_archival_aborts(tmp_path):
    """greptile/codex round-2 P1: when the fact store aborts because the
    subject revived mid-window, the sweep must NOT go on to archive the
    subject's reflections and persona entries — a revived subject keeps
    all three stores or archives all three, never a partial split."""
    from unittest.mock import AsyncMock, patch as _patch

    _, fs, pm, re, _, _ = _install(str(tmp_path))
    stale_pid, _ = await _seed_two_subjects(fs, pm, re)

    with _patch.object(
        fs, "aarchive_subject_facts", AsyncMock(return_value=None),
    ):
        report = await asweep_scoped_subject_archive(
            "小天", **_sweep_kwargs(fs, pm, re),
        )
    assert report['archived'] == {}
    # reflection/persona 全部原样：没有出现「fact 留下、其余被归档」的劈叉。
    assert {r['id'] for r in await re._aload_reflections_full("小天")} == {"rs1", "ra1"}
    persona = await pm.aensure_persona("小天")
    all_ids = {
        e.get('id')
        for sec in persona.values() if isinstance(sec, dict)
        for e in sec.get('facts', []) if isinstance(e, dict)
    }
    assert stale_pid in all_ids


@pytest.mark.asyncio
async def test_sweep_stamps_absorbed_archived_rows_of_stale_subject(tmp_path):
    """codex round-2 P1: a stale subject whose facts were ALREADY moved to
    facts_archive.json by the absorbed-shrink path must still exit recall —
    the sweep stamps those rows in place, including when the subject has
    no active facts at all."""
    from memory.hybrid_recall import _aload_archive_facts
    from utils.file_utils import atomic_write_json

    _, fs, pm, re, _, _ = _install(str(tmp_path))
    # 该 subject 只有 absorbed 归档历史，活跃池为空。
    absorbed_row = _scoped_fact(
        "abs1", "只剩 absorbed 历史的群", SUBJ_STALE,
        created_at=_iso(120), absorbed=True,
    )
    atomic_write_json(
        fs._facts_archive_path("小天"), [absorbed_row],
        indent=2, ensure_ascii=False,
    )
    # 归档前：absorbed 行在召回池里（活跃 subject 的设计行为）。
    assert {r['id'] for r in await _aload_archive_facts(fs, "小天")} == {"abs1"}

    report = await asweep_scoped_subject_archive(
        "小天", **_sweep_kwargs(fs, pm, re),
    )
    key = f"{SUBJ_STALE.key}|{SUBJ_STALE.scope}"
    assert report['archived'][key]['facts'] == 1
    # 归档后：补了标记，退出召回池；absorbed 溯源保留。
    assert await _aload_archive_facts(fs, "小天") == []
    with open(fs._facts_archive_path("小天"), encoding="utf-8") as f:
        rows = json.load(f)
    assert rows[0]['subject_archived_at']
    assert rows[0]['absorbed'] is True

    # 幂等：第二轮无待办。
    report2 = await asweep_scoped_subject_archive(
        "小天", **_sweep_kwargs(fs, pm, re),
    )
    assert report2['archived'] == {}


@pytest.mark.asyncio
async def test_fact_archival_aborts_on_fresh_restore_in_window(tmp_path):
    """codex round-3: a concurrent explicit restore stamps restored_at but
    keeps the old created_at — the locked revalidation must honour every
    ledger timestamp field, or the just-restored rows get re-archived."""
    _, fs, pm, re, _, _ = _install(str(tmp_path))
    restored_row = _scoped_fact("r1", "刚恢复的旧事实", SUBJ_STALE,
                                created_at=_iso(120))
    restored_row['restored_at'] = _iso(0.01)  # 判定窗口内的恢复
    fs._facts["小天"] = [restored_row]
    await fs.asave_facts("小天")
    cutoff = NOW - timedelta(days=STALE_DAYS)
    moved = await fs.aarchive_subject_facts(
        "小天", SUBJ_STALE, NOW.isoformat(), cutoff,
    )
    assert moved is None
    assert {f['id'] for f in await fs.aload_facts("小天")} == {"r1"}


@pytest.mark.asyncio
async def test_fact_archival_aborts_on_corrupt_archive_file(tmp_path):
    """codex round-3: a corrupt facts_archive.json must abort (None) — an
    ordinary 0 would let the sweep archive reflections/persona while the
    facts stay active and recallable, splitting the subject permanently."""
    _, fs, pm, re, _, _ = _install(str(tmp_path))
    fs._facts["小天"] = [
        _scoped_fact("fs1", "陈年", SUBJ_STALE, created_at=_iso(120)),
    ]
    await fs.asave_facts("小天")
    os.makedirs(os.path.dirname(fs._facts_archive_path("小天")), exist_ok=True)
    with open(fs._facts_archive_path("小天"), "w", encoding="utf-8") as f:
        f.write("{not json")
    cutoff = NOW - timedelta(days=STALE_DAYS)
    moved = await fs.aarchive_subject_facts(
        "小天", SUBJ_STALE, NOW.isoformat(), cutoff,
    )
    assert moved is None
    assert {f['id'] for f in await fs.aload_facts("小天")} == {"fs1"}


@pytest.mark.asyncio
async def test_sweep_skips_other_stores_when_fact_archival_raises(tmp_path):
    """coderabbit round-3 Major: a hard write failure in the fact store is
    treated like the revival abort — skip the subject's other stores, never
    leave facts active while reflections/persona moved out."""
    from unittest.mock import AsyncMock, patch as _patch

    _, fs, pm, re, _, _ = _install(str(tmp_path))
    stale_pid, _ = await _seed_two_subjects(fs, pm, re)

    with _patch.object(
        fs, "aarchive_subject_facts",
        AsyncMock(side_effect=OSError("disk full")),
    ):
        report = await asweep_scoped_subject_archive(
            "小天", **_sweep_kwargs(fs, pm, re),
        )
    assert report['archived'] == {}
    assert {r['id'] for r in await re._aload_reflections_full("小天")} == {"rs1", "ra1"}
    persona = await pm.aensure_persona("小天")
    all_ids = {
        e.get('id')
        for sec in persona.values() if isinstance(sec, dict)
        for e in sec.get('facts', []) if isinstance(e, dict)
    }
    assert stale_pid in all_ids


def test_parse_iso_normalizes_timezone_aware_timestamps():
    """codex round-3: import/migration paths can write offset-bearing ISO
    strings; mixing aware values with the sweep's naive now() would raise
    TypeError and kill the whole stage every interval."""
    aware_fact = _scoped_fact(
        "tz1", "带时区的行", SUBJ_STALE,
        created_at="2026-03-01T12:00:00+00:00",
    )
    last, no_ts = collect_subject_last_writes([[aware_fact]])
    marker = (SUBJ_STALE.key, SUBJ_STALE.scope)
    assert marker in last
    assert last[marker][1].tzinfo is None  # 已折算成 naive
    # naive now 与之混算不抛 TypeError，且 120+ 天前的行判 stale。
    stale = find_stale_subjects(last, now=NOW, stale_days=STALE_DAYS)
    assert [s.key for s, _ in stale] == [SUBJ_STALE.key]


@pytest.mark.asyncio
async def test_fact_archival_keeps_unparseable_rows_without_veto(tmp_path):
    """A row with a corrupt created_at neither archives (unknown age must
    not mean old) nor vetoes the pass (one corrupt row must not immortalize
    the subject)."""
    _, fs, pm, re, _, _ = _install(str(tmp_path))
    corrupt = _scoped_fact("bad", "坏时间戳", SUBJ_STALE, created_at="not-a-date")
    fs._facts["小天"] = [
        _scoped_fact("fs1", "陈年一", SUBJ_STALE, created_at=_iso(120)),
        corrupt,
    ]
    await fs.asave_facts("小天")
    cutoff = NOW - timedelta(days=STALE_DAYS)
    moved = await fs.aarchive_subject_facts(
        "小天", SUBJ_STALE, NOW.isoformat(), cutoff,
    )
    assert moved == 1
    assert {f['id'] for f in await fs.aload_facts("小天")} == {"bad"}


@pytest.mark.asyncio
async def test_restore_resets_archival_clock(tmp_path):
    """codex P1: without a restored_at stamp in the staleness ledger, a
    restored subject whose data is older than N days is immediately stale
    again and the next sweep silently undoes the restore."""
    _, fs, pm, re, _, _ = _install(str(tmp_path))
    await _seed_two_subjects(fs, pm, re)
    await asweep_scoped_subject_archive("小天", **_sweep_kwargs(fs, pm, re))

    await arestore_scoped_subject(
        "小天", SUBJ_STALE,
        fact_store=fs, persona_manager=pm, reflection_engine=re,
        now=NOW + timedelta(hours=1),
    )
    restored_facts = [
        f for f in await fs.aload_facts("小天")
        if f.get('id') in {"fs1", "fs2"}
    ]
    assert restored_facts and all(f.get('restored_at') for f in restored_facts)

    # restore 之后紧接着的 sweep 绝不能把 subject 原样再归档。
    kwargs = _sweep_kwargs(fs, pm, re)
    kwargs['now'] = NOW + timedelta(hours=2)
    report = await asweep_scoped_subject_archive("小天", **kwargs)
    assert report['archived'] == {}
    assert {"fs1", "fs2"} <= {f['id'] for f in await fs.aload_facts("小天")}


@pytest.mark.asyncio
async def test_synthesis_related_context_excludes_subject_archived_rows(tmp_path):
    """codex P2: `_build_related_context_block` reads the FULL fact pool on
    its own — subject-archived rows must not re-enter a revived subject's
    synthesis prompt through that side door."""
    import numpy as np
    from memory.embeddings import stamp_embedding_fields

    _, fs, pm, re, _, _ = _install(str(tmp_path))
    model_id = "test-model"

    def _stamped_fact(fid, text, days, **extra):
        row = _scoped_fact(fid, text, SUBJ_STALE, created_at=_iso(days), **extra)
        stamp_embedding_fields(
            row, np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
            text, model_id,
        )
        return row

    unabsorbed = _stamped_fact("q1", "查询事实", 1)
    normal_absorbed = _stamped_fact("ok1", "正常已吸收", 30, absorbed=True)
    archived_absorbed = _stamped_fact("arch1", "已归档已吸收", 200, absorbed=True)
    archived_absorbed['subject_archived_at'] = _iso(5)
    fs._facts["小天"] = [unabsorbed, normal_absorbed, archived_absorbed]
    await fs.asave_facts("小天")

    class _Service:
        def is_disabled(self):
            return False

        def is_available(self):
            return True

        def model_id(self):
            return model_id

    captured = {}

    async def _capture_topk(self, pool, query_texts, **kwargs):
        captured['pool_ids'] = {f.get('id') for f in pool}
        return []

    from unittest.mock import patch as _patch
    with _patch("memory.embeddings.get_embedding_service",
                return_value=_Service()), \
         _patch("memory.recall.MemoryRecallReranker.aretrieve_per_query_topk",
                _capture_topk):
        await re._build_related_context_block(
            "小天", [unabsorbed], subject=SUBJ_STALE,
        )
    assert captured.get('pool_ids') == {"ok1"}


@pytest.mark.asyncio
async def test_protected_entries_survive_sweep(tmp_path):
    _, fs, pm, re, _, _ = _install(str(tmp_path))
    fs._facts["小天"] = [
        _scoped_fact("fs1", "陈年", SUBJ_STALE, created_at=_iso(120)),
    ]
    await fs.asave_facts("小天")
    protected = _scoped_reflection("rp", "受保护反思", SUBJ_STALE,
                                   created_at=_iso(120))
    protected['protected'] = True
    await re.asave_reflections("小天", [protected])

    await asweep_scoped_subject_archive("小天", **_sweep_kwargs(fs, pm, re))
    refls = await re._aload_reflections_full("小天")
    assert {r['id'] for r in refls} == {"rp"}


# ── 召回与去重的归档语义 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recall_archive_pool_excludes_subject_archived_rows(tmp_path):
    from memory.hybrid_recall import _aload_archive_facts

    _, fs, pm, re, _, _ = _install(str(tmp_path))
    fs._facts["小天"] = [
        _scoped_fact("fs1", "陈年群事实", SUBJ_STALE, created_at=_iso(120)),
    ]
    await fs.asave_facts("小天")
    # absorbed 归档行（无 subject 标记）走既有路径落归档文件——它必须继续
    # 可召回；subject 归档行必须被滤掉。
    absorbed_row = {
        "id": "old1", "text": "absorbed 旧事实", "entity": "master",
        "importance": 5, "hash": "old1h", "created_at": _iso(30),
        "absorbed": True,
    }
    archive_path = fs._facts_archive_path("小天")
    from utils.file_utils import atomic_write_json
    atomic_write_json(archive_path, [absorbed_row], indent=2, ensure_ascii=False)

    await asweep_scoped_subject_archive("小天", **_sweep_kwargs(fs, pm, re))

    pool = await _aload_archive_facts(fs, "小天")
    ids = {r['id'] for r in pool}
    assert "old1" in ids          # absorbed 归档：仍在召回池
    assert "fs1" not in ids       # subject 归档：退出召回池


@pytest.mark.asyncio
async def test_fts_dedup_lets_revived_subject_restate_archived_fact(tmp_path):
    """Revival semantics: after subject archival, a member re-stating the
    same fact must NOT be deduped away by the FTS near-match against the
    archived row — that row already left recall, so blocking the re-write
    would make the information permanently invisible. Counter-case:
    absorbed-archived rows (no marker) still block duplicates."""
    _, fs, pm, re, _, _ = _install(str(tmp_path))
    fs._facts["小天"] = [
        _scoped_fact("fs1", "小明住在幸福路", SUBJ_STALE, created_at=_iso(120)),
    ]
    await fs.asave_facts("小天")
    await asweep_scoped_subject_archive("小天", **_sweep_kwargs(fs, pm, re))
    assert await fs.aload_facts("小天") == []

    # FTS stub：命中已 subject 归档的 fs1（分数 -10 = 强相似）。
    class _FTSStub:
        async def asearch_facts(self, _name, _text, _limit):
            return [("fs1", -10.0)]

        async def aindex_fact(self, *_a, **_k):
            return None

    fs._time_indexed = _FTSStub()
    created = await fs.apersist_scoped_facts(
        "小天",
        [{"text": "小明重新说了自己住在幸福路", "importance": 6}],
        subject=SUBJ_STALE,
    )
    assert len(created) == 1  # 复活写入成功，没有被归档行去重掉

    # 对照：absorbed 归档行（无 subject_archived_at）仍然挡重复。
    absorbed_row = _scoped_fact(
        "ab1", "小红喜欢喝咖啡", SUBJ_ACTIVE, created_at=_iso(30),
        absorbed=True,
    )
    from utils.file_utils import atomic_write_json
    with open(fs._facts_archive_path("小天"), encoding="utf-8") as f:
        rows = json.load(f)
    rows.append(absorbed_row)
    atomic_write_json(fs._facts_archive_path("小天"), rows,
                      indent=2, ensure_ascii=False)

    class _FTSStub2:
        async def asearch_facts(self, _name, _text, _limit):
            return [("ab1", -10.0)]

        async def aindex_fact(self, *_a, **_k):
            return None

    fs._time_indexed = _FTSStub2()
    created2 = await fs.apersist_scoped_facts(
        "小天",
        [{"text": "小红爱喝咖啡", "importance": 6}],
        subject=SUBJ_ACTIVE,
    )
    assert created2 == []  # absorbed 归档行照旧参与近似去重


# ── restore ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_restore_roundtrip_all_three_stores(tmp_path):
    _, fs, pm, re, _, _ = _install(str(tmp_path))
    stale_pid, _ = await _seed_two_subjects(fs, pm, re)
    await asweep_scoped_subject_archive("小天", **_sweep_kwargs(fs, pm, re))

    result = await arestore_scoped_subject(
        "小天", SUBJ_STALE,
        fact_store=fs, persona_manager=pm, reflection_engine=re,
    )
    assert result == {'facts': 2, 'reflections': 1, 'persona_entries': 1}

    # facts 回活跃池且标记已剥。
    active = await fs.aload_facts("小天")
    ids = {f['id'] for f in active}
    assert {"fs1", "fs2", "fa1"} <= ids
    assert all(not f.get('subject_archived_at') for f in active)
    # 归档文件里不再有该 subject 的标记行。
    with open(fs._facts_archive_path("小天"), encoding="utf-8") as f:
        leftover = json.load(f)
    assert all(not r.get('subject_archived_at') for r in leftover)

    # reflection 回主文件，状态回 confirmed，可被活跃读取。
    refls = await re.aload_reflections("小天")
    back = next(r for r in refls if r['id'] == "rs1")
    assert back['status'] == 'confirmed'
    assert back.get('restored_at')
    assert not back.get('archive_shard_path')

    # persona 条目回 section。
    persona = await pm.aensure_persona("小天")
    section = persona.get(SUBJ_STALE.persona_section_key, {})
    section_ids = {
        e.get('id') for e in section.get('facts', []) if isinstance(e, dict)
    }
    assert stale_pid in section_ids

    # 幂等：重复 restore 不产生重复条目。
    result2 = await arestore_scoped_subject(
        "小天", SUBJ_STALE,
        fact_store=fs, persona_manager=pm, reflection_engine=re,
    )
    assert result2 == {'facts': 0, 'reflections': 0, 'persona_entries': 0}
    refls2 = await re._aload_reflections_full("小天")
    assert len([r for r in refls2 if r['id'] == "rs1"]) == 1


@pytest.mark.asyncio
async def test_restore_does_not_revive_snapshots_preceding_scoped_forget(tmp_path):
    """A persistent forget cutoff must outlive replay-recreated shard copies."""
    _, fs, pm, re, _, _ = _install(str(tmp_path))
    await _seed_two_subjects(fs, pm, re)
    await asweep_scoped_subject_archive("小天", **_sweep_kwargs(fs, pm, re))

    # This mirrors scoped_forget after the subject has already gone stale:
    # facts archive rows are purged, while event-sourced shard snapshots stay.
    await fs.aforget_subject("小天", SUBJ_STALE)
    await re.aforget_subject("小天", SUBJ_STALE)
    await pm.aforget_subject("小天", SUBJ_STALE)

    result = await arestore_scoped_subject(
        "小天", SUBJ_STALE,
        fact_store=fs, persona_manager=pm, reflection_engine=re,
    )
    assert result == {'facts': 0, 'reflections': 0, 'persona_entries': 0}
    assert not any(
        row.get('id') == 'rs1'
        for row in await re._aload_reflections_full("小天")
    )
    persona = await pm.aensure_persona("小天")
    assert SUBJ_STALE.persona_section_key not in persona


@pytest.mark.asyncio
async def test_restore_picks_newest_snapshot_across_shards(tmp_path):
    """codex P2: after archive → restore → archive cycles the same entry id
    can sit in multiple shards. Same-day shard suffixes are random uuid8,
    so filename order is NOT chronological — restore must pick the copy
    with the newest archived_at, never whichever file happens to sort last."""
    from utils.file_utils import atomic_write_json

    _, fs, pm, re, _, _ = _install(str(tmp_path))
    archive_dir = re._reflections_archive_dir("小天")
    os.makedirs(archive_dir, exist_ok=True)

    def _snapshot(text: str, archived_at: str) -> dict:
        entry = _scoped_reflection("rdup", text, SUBJ_STALE, created_at=_iso(200))
        entry['status'] = 'archived'
        entry['archived_at'] = archived_at
        entry['archive_shard_path'] = 'x'
        return entry

    # 最新快照刻意放在文件序的中间位：first-wins 拿到首文件的旧快照、
    # last-wins 拿到末文件的更旧快照，只有按 archived_at 比较才能全对。
    atomic_write_json(
        os.path.join(archive_dir, "2026-06-01_aaaaaaaa.json"),
        [_snapshot("旧快照文本", _iso(40))], indent=2, ensure_ascii=False,
    )
    atomic_write_json(
        os.path.join(archive_dir, "2026-06-02_bbbbbbbb.json"),
        [_snapshot("新快照文本", _iso(10))], indent=2, ensure_ascii=False,
    )
    atomic_write_json(
        os.path.join(archive_dir, "2026-06-03_cccccccc.json"),
        [_snapshot("更旧快照文本", _iso(60))], indent=2, ensure_ascii=False,
    )

    result = await arestore_scoped_subject(
        "小天", SUBJ_STALE,
        fact_store=fs, persona_manager=pm, reflection_engine=re,
    )
    assert result['reflections'] == 1
    refls = await re.aload_reflections("小天")
    back = next(r for r in refls if r['id'] == "rdup")
    assert back['text'] == "新快照文本"


@pytest.mark.asyncio
async def test_restore_aborts_higher_stores_on_corrupt_fact_archive(tmp_path):
    """codex round-4: a corrupt facts_archive.json must abort the whole
    restore (mirroring the archival-side abort) — restoring reflections /
    persona while the facts stay archived would split the subject until
    the archive file is repaired."""
    from memory.archive_shards import append_to_shard_sync

    _, fs, pm, re, _, _ = _install(str(tmp_path))
    # 分片里有一条可恢复的 reflection。
    archived_refl = _scoped_reflection("rres", "可恢复反思", SUBJ_STALE,
                                       created_at=_iso(120))
    archived_refl['status'] = 'archived'
    archived_refl['archived_at'] = _iso(10)
    archived_refl['archive_shard_path'] = 'x'
    append_to_shard_sync(re._reflections_archive_dir("小天"), [archived_refl])
    # facts 归档文件损坏。
    os.makedirs(os.path.dirname(fs._facts_archive_path("小天")), exist_ok=True)
    with open(fs._facts_archive_path("小天"), "w", encoding="utf-8") as f:
        f.write("{not json")

    result = await arestore_scoped_subject(
        "小天", SUBJ_STALE,
        fact_store=fs, persona_manager=pm, reflection_engine=re,
    )
    assert result.get('aborted') is True
    assert result['reflections'] == 0
    # reflection 未被恢复——没有出现「facts 仍归档、反思已活跃」的劈叉。
    assert await re._aload_reflections_full("小天") == []


@pytest.mark.asyncio
async def test_restore_skips_age_archived_terminal_reflections(tmp_path):
    """Age-based terminal archival (promoted/denied >30 days) keeps the
    original status in the shard copy — only the subject/evidence archive
    paths stamp status='archived'. A subject restore must never resurrect
    a promoted terminal reflection back into the main file."""
    from memory.archive_shards import append_to_shard_sync

    _, fs, pm, re, _, _ = _install(str(tmp_path))
    archive_dir = re._reflections_archive_dir("小天")
    promoted = _scoped_reflection("rprom", "已晋升的反思", SUBJ_STALE,
                                  created_at=_iso(200))
    promoted['status'] = 'promoted'
    promoted['archived_at'] = _iso(40)
    promoted['archive_shard_path'] = 'x'
    append_to_shard_sync(archive_dir, [promoted])

    result = await arestore_scoped_subject(
        "小天", SUBJ_STALE,
        fact_store=fs, persona_manager=pm, reflection_engine=re,
    )
    assert result['reflections'] == 0
    assert await re._aload_reflections_full("小天") == []


# ── sweep loop 接线与节流 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_stage_throttles_and_respects_enable_gate(tmp_path):
    import config as config_module
    from app.memory_server import evidence_loops

    calls = []

    async def _fake_sweep(name, **kwargs):
        calls.append(name)
        return {}

    with patch("memory.subject_archive.asweep_scoped_subject_archive",
               _fake_sweep), \
         patch.object(evidence_loops.runtime, "fact_store", MagicMock()), \
         patch.object(evidence_loops.runtime, "persona_manager", MagicMock()), \
         patch.object(evidence_loops.runtime, "reflection_engine", MagicMock()):
        evidence_loops._subject_archive_last_run.clear()
        t0 = datetime(2026, 7, 1, 0, 0, 0)
        await evidence_loops._amaybe_sweep_subject_archive("小天", t0)
        assert calls == ["小天"]
        # 同一节流窗口内的第二次调用 no-op。
        await evidence_loops._amaybe_sweep_subject_archive(
            "小天", t0 + timedelta(seconds=60),
        )
        assert calls == ["小天"]
        # 窗口过后再跑。
        await evidence_loops._amaybe_sweep_subject_archive(
            "小天",
            t0 + timedelta(
                seconds=config_module.SCOPED_SUBJECT_ARCHIVE_MIN_INTERVAL_SECONDS + 1,
            ),
        )
        assert calls == ["小天", "小天"]

        # 总开关关掉 → 不再触发。
        evidence_loops._subject_archive_last_run.clear()
        with patch.object(
            config_module, "SCOPED_SUBJECT_ARCHIVE_ENABLED", False,
        ):
            await evidence_loops._amaybe_sweep_subject_archive(
                "小天", t0 + timedelta(days=1),
            )
        assert calls == ["小天", "小天"]


def test_archive_sweep_loop_source_wires_subject_stage():
    """Structural guardrail (same style as the registration assertion in
    test_reflection_synthesis_loop): the per-character sweep body of
    _periodic_archive_sweep_loop must invoke the scoped-subject stage,
    so a future refactor cannot silently unwire it."""
    import inspect
    from app.memory_server import evidence_loops

    src = inspect.getsource(evidence_loops._periodic_archive_sweep_loop)
    assert "_amaybe_sweep_subject_archive(" in src
