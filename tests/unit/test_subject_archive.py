# -*- coding: utf-8 -*-
"""Unit tests for memory.subject_archive — time-driven scoped subject archival.

Contracts under test (群记忆系列 5/7 主线一):

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
    """SUBJ_STALE last write 91 天前；SUBJ_ACTIVE 10 天前。"""
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
    """变异验证的两个方向：把活跃 subject 的判据改紧（10 天前的写入在
    stale_days=8 下必须被归档）；把 stale subject 的判据改松
    （stale_days=365 下必须原样不动）。"""
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
    """absorbed 收缩会把新近 fact 搬进 facts_archive.json——最后写入时间
    必须从 active+archive 全池推导。只看活跃池的错误实现会把「最近还在
    写、但新写入都被 absorbed 归档了」的 subject 误判为 stale。"""
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
    """复活语义：subject 归档后成员重述同一事实，FTS 近似命中不得把新
    写入判成重复——否则这条信息（归档行已退出召回）永久不可见。反向
    对照：absorbed 归档行照旧挡重复。"""
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
async def test_restore_skips_age_archived_terminal_reflections(tmp_path):
    """30 天年龄归档进 shard 的 promoted/denied 条目保留原 status（只有
    subject/evidence 归档路径盖 status='archived'）——subject restore 绝
    不能把已晋升的终态反思复活回主文件。"""
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
    """结构护栏（对齐 test_reflection_synthesis_loop 的注册断言风格）：
    _periodic_archive_sweep_loop 的角色扫描体必须调用 scoped subject
    归档阶段——防止将来重构时静默掉线。"""
    import inspect
    from app.memory_server import evidence_loops

    src = inspect.getsource(evidence_loops._periodic_archive_sweep_loop)
    assert "_amaybe_sweep_subject_archive(" in src
