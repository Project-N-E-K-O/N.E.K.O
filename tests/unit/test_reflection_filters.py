# -*- coding: utf-8 -*-
"""
Unit tests for the reflection filter tightening (PR #929 round 8):

- aget_confirmed_reflections: score > 0 AND not suppress
- aget_followup_topics: also excludes derived-confirmed (score >= CONFIRMED)
- arecord_mentions: 5h window suppress机制 apply to confirmed reflection
- synthesize 的 importance-based initial rein seed
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake", "base_url": "http://fake", "api_key": "sk-fake",
    })
    return cm


def _install(tmpdir: str):
    from memory.event_log import EventLog
    from memory.facts import FactStore
    from memory.persona import PersonaManager
    from memory.reflection import ReflectionEngine

    cm = _mock_cm(tmpdir)
    with patch("memory.event_log.get_config_manager", return_value=cm), \
         patch("memory.facts.get_config_manager", return_value=cm), \
         patch("memory.persona.get_config_manager", return_value=cm), \
         patch("memory.reflection.get_config_manager", return_value=cm):
        event_log = EventLog()
        event_log._config_manager = cm
        fs = FactStore()
        fs._config_manager = cm
        pm = PersonaManager(event_log=event_log)
        pm._config_manager = cm
        re = ReflectionEngine(fs, pm, event_log=event_log)
        re._config_manager = cm
    return event_log, fs, pm, re, cm


def _seed(rid: str, status: str, rein: float = 0.0, disp: float = 0.0,
          suppress: bool = False, text: str = "主人喜欢猫娘") -> dict:
    from datetime import datetime
    now_iso = datetime.now().isoformat()
    return {
        "id": rid, "text": text, "entity": "master", "status": status,
        "source_fact_ids": [], "created_at": now_iso, "feedback": None,
        "next_eligible_at": now_iso,
        "reinforcement": rein, "disputation": disp,
        "rein_last_signal_at": now_iso if rein > 0 else None,
        "disp_last_signal_at": now_iso if disp > 0 else None,
        "suppress": suppress,
    }


# ── Change 1: aget_confirmed_reflections ────────────────────────────


@pytest.mark.asyncio
async def test_aget_confirmed_excludes_score_zero(tmp_path):
    """confirmed + score = 0 (rein=1, disp=1) 不该渲染 —— 已被用户抵消。"""
    _, _, _, re, _ = _install(str(tmp_path))
    await re.asave_reflections("小天", [
        _seed("ref_visible", "confirmed", rein=1.0),
        _seed("ref_zero",    "confirmed", rein=1.0, disp=1.0),
    ])
    result = await re.aget_confirmed_reflections("小天")
    ids = {r["id"] for r in result}
    assert ids == {"ref_visible"}


@pytest.mark.asyncio
async def test_aget_confirmed_excludes_negative(tmp_path):
    _, _, _, re, _ = _install(str(tmp_path))
    await re.asave_reflections("小天", [
        _seed("ref_neg", "confirmed", rein=1.0, disp=2.0),  # score = -1
    ])
    result = await re.aget_confirmed_reflections("小天")
    assert result == []


@pytest.mark.asyncio
async def test_aget_confirmed_excludes_suppressed(tmp_path):
    _, _, _, re, _ = _install(str(tmp_path))
    await re.asave_reflections("小天", [
        _seed("ref_supp", "confirmed", rein=2.0, suppress=True),
    ])
    result = await re.aget_confirmed_reflections("小天")
    assert result == []


# ── Change 2: aget_followup_topics derived-confirmed exclusion ──────


@pytest.mark.asyncio
async def test_followup_excludes_derived_confirmed(tmp_path):
    """存盘 status='pending' 但 score>=1 应被 followup 过滤（等 periodic 转存盘）。"""
    _, _, _, re, _ = _install(str(tmp_path))
    await re.asave_reflections("小天", [
        _seed("ref_pending_real", "pending", rein=0.0),     # 真 pending
        _seed("ref_pending_derived_confirmed", "pending", rein=1.5),  # 派生 confirmed
        _seed("ref_pending_derived_promoted",  "pending", rein=2.5),  # 派生 promoted
    ])
    result = await re.aget_followup_topics("小天")
    ids = {r["id"] for r in result}
    assert ids == {"ref_pending_real"}


@pytest.mark.asyncio
async def test_followup_still_excludes_negative(tmp_path):
    """原有 score<0 过滤不变。"""
    _, _, _, re, _ = _install(str(tmp_path))
    await re.asave_reflections("小天", [
        _seed("ref_cold", "pending", rein=0.0, disp=1.0),  # score=-1
    ])
    result = await re.aget_followup_topics("小天")
    assert result == []


# ── Change 3: arecord_mentions for confirmed reflection ─────────────


@pytest.mark.asyncio
async def test_arecord_mentions_suppresses_confirmed_over_limit(tmp_path):
    """5h 窗口内 > 2 次 mention 后 suppress 打开；pending 不参与。"""
    _, _, _, re, _ = _install(str(tmp_path))
    await re.asave_reflections("小天", [
        _seed("ref_conf", "confirmed", rein=2.0, text="主人喝咖啡"),
        _seed("ref_pend", "pending",   rein=0.0, text="主人喝茶"),
    ])
    for _ in range(3):
        await re.arecord_mentions("小天", "主人今天又喝咖啡了")
        await re.arecord_mentions("小天", "主人喝茶也不错")

    reloaded = await re._aload_reflections_full("小天")
    by_id = {r["id"]: r for r in reloaded}
    # confirmed 被 suppress
    assert by_id["ref_conf"]["suppress"] is True
    assert len(by_id["ref_conf"]["recent_mentions"]) >= 3
    # pending 本意是"AI 试探用户确认"，不参与 suppress 机制
    assert by_id["ref_pend"]["suppress"] is False
    assert by_id["ref_pend"]["recent_mentions"] == []


@pytest.mark.asyncio
async def test_arecord_mentions_under_limit_stays_unsuppressed(tmp_path):
    _, _, _, re, _ = _install(str(tmp_path))
    await re.asave_reflections("小天", [
        _seed("ref_conf", "confirmed", rein=2.0, text="主人喝咖啡"),
    ])
    # 只提一次
    await re.arecord_mentions("小天", "主人喝咖啡了")
    reloaded = await re._aload_reflections_full("小天")
    r = reloaded[0]
    assert r["suppress"] is False
    assert len(r["recent_mentions"]) == 1


# ── Change 4b: importance → initial rein seed in synthesis ──────────


@pytest.mark.asyncio
async def test_synth_high_importance_seeds_initial_rein(tmp_path):
    """importance=10 的 fact → reflection 初始 rein=0.8 + rein_last_signal_at 非空。"""
    from memory.facts import FactStore
    from memory.reflection import ReflectionEngine

    ev, fs, pm, re, _cm = _install(str(tmp_path))

    # 5 facts, max importance=10 (critical nickname) — triggers the 0.8 tier
    fact_ids = []
    for i, imp in enumerate([10, 7, 6, 8, 5]):
        fact = {
            'id': f'fact_{i}',
            'text': f'高重要事实 {i}（importance={imp}）',
            'importance': imp,
            'entity': 'master',
            'tags': [], 'hash': f'h{i}',
            'created_at': '2026-04-23T10:00:00',
            'absorbed': False,
        }
        fs._facts.setdefault('小天', []).append(fact)
        fact_ids.append(fact['id'])
    await fs.asave_facts('小天')

    # Mock the LLM call: return a reflection text for this batch
    async def _fake_llm(*args, **kwargs):
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.content = '{"reflection": "主人的核心偏好", "entity": "master"}'
        return resp

    async def _fake_aclose():
        return None

    fake_llm_obj = MagicMock()
    fake_llm_obj.ainvoke = _fake_llm
    fake_llm_obj.aclose = _fake_aclose

    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm_obj):
        results = await re.synthesize_reflections("小天")

    assert len(results) == 1
    reflections = await re._aload_reflections_full("小天")
    r = reflections[0]
    assert r["reinforcement"] == pytest.approx(0.8)
    assert r["rein_last_signal_at"] is not None
    assert r["disputation"] == 0.0


@pytest.mark.asyncio
async def test_synth_low_importance_no_seed(tmp_path):
    """max importance in {5,6} → initial rein=0, rein_last_signal_at=None."""
    ev, fs, pm, re, _cm = _install(str(tmp_path))

    for i in range(5):
        fact = {
            'id': f'fact_{i}',
            'text': f'普通事实 {i}',
            'importance': 6, 'entity': 'master',
            'tags': [], 'hash': f'h{i}',
            'created_at': '2026-04-23T10:00:00',
            'absorbed': False,
        }
        fs._facts.setdefault('小天', []).append(fact)
    await fs.asave_facts('小天')

    async def _fake_llm(*args, **kwargs):
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.content = '{"reflection": "一些日常偏好", "entity": "master"}'
        return resp

    async def _fake_aclose():
        return None

    fake_llm_obj = MagicMock()
    fake_llm_obj.ainvoke = _fake_llm
    fake_llm_obj.aclose = _fake_aclose

    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm_obj):
        results = await re.synthesize_reflections("小天")

    assert len(results) == 1
    reflections = await re._aload_reflections_full("小天")
    r = reflections[0]
    assert r["reinforcement"] == 0.0
    assert r["rein_last_signal_at"] is None
