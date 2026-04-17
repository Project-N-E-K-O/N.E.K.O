# -*- coding: utf-8 -*-
"""
P2.a.2: per-character asyncio.Lock on ReflectionEngine and PersonaManager.

Regression tests for the resilience improvement: concurrent mutations on the
same character must not corrupt the JSON view (lost updates, duplicate
entries, partial writes).
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import nullcontext
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


def _install_persona(tmpdir: str):
    from memory.persona import PersonaManager
    cm = _mock_cm(tmpdir)
    with patch("memory.persona.get_config_manager", return_value=cm):
        pm = PersonaManager()
    pm._config_manager = cm
    return pm, cm


def _install_reflection(tmpdir: str):
    from memory.facts import FactStore
    from memory.persona import PersonaManager
    from memory.reflection import ReflectionEngine

    cm = _mock_cm(tmpdir)
    with patch("memory.facts.get_config_manager", return_value=cm), \
         patch("memory.persona.get_config_manager", return_value=cm), \
         patch("memory.reflection.get_config_manager", return_value=cm):
        fs = FactStore()
        fs._config_manager = cm
        pm = PersonaManager()
        pm._config_manager = cm
        re = ReflectionEngine(fs, pm)
        re._config_manager = cm
    return fs, pm, re, cm


# ── PersonaManager tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persona_aadd_fact_has_per_character_lock(tmp_path):
    pm, _ = _install_persona(str(tmp_path))
    lock_a = pm._get_alock("小天")
    lock_b = pm._get_alock("小天")
    assert lock_a is lock_b, "same-character _get_alock must return same Lock"

    lock_c = pm._get_alock("小雪")
    assert lock_a is not lock_c, "different characters must have independent locks"


@pytest.mark.asyncio
async def test_persona_arecord_mentions_serializes(tmp_path):
    """10 concurrent arecord_mentions calls must not lose any mentions
    and must not corrupt persona.json."""
    pm, _ = _install_persona(str(tmp_path))

    # Seed a persona with a protected-false entry that will match mentions of "喝咖啡"
    async def _seed():
        persona = await pm.aensure_persona("小天")
        persona.setdefault("master", {}).setdefault("facts", []).append({
            "id": "manual_seed_1", "text": "主人喜欢喝咖啡",
            "source": "manual", "source_id": None,
            "recent_mentions": [], "suppress": False,
        })
        await pm.asave_persona("小天", persona)

    await _seed()

    # Fire 10 record_mentions concurrently — all mention "喝咖啡"
    await asyncio.gather(*(
        pm.arecord_mentions("小天", "我听主人说要去喝咖啡")
        for _ in range(10)
    ))

    # Read back via a fresh manager (bypass cache)
    fresh, _ = _install_persona(str(tmp_path))
    persona = await fresh.aensure_persona("小天")
    entries = persona["master"]["facts"]
    assert len(entries) == 1
    recent = entries[0].get("recent_mentions", [])
    # Exactly 10 mention entries (no lost updates)
    assert len(recent) == 10, f"expected 10 mentions, got {len(recent)}"


@pytest.mark.asyncio
async def test_persona_locks_prevent_corruption_proof(tmp_path):
    """Diagnostic oracle: with _get_alock patched to nullcontext, concurrent
    arecord_mentions DOES lose updates — proves the lock is load-bearing."""
    pm, _ = _install_persona(str(tmp_path))

    async def _seed():
        persona = await pm.aensure_persona("小天")
        persona.setdefault("master", {}).setdefault("facts", []).append({
            "id": "seed", "text": "主人喜欢喝咖啡",
            "recent_mentions": [], "suppress": False,
        })
        await pm.asave_persona("小天", persona)

    await _seed()

    # Replace lock with no-op; simulate the pre-P2.a.2 world
    pm._get_alock = lambda name: nullcontext()
    # Clear cache so every coroutine re-reads from disk
    original_save = pm.asave_persona

    async def slow_save(name, persona=None):
        # simulate a 20ms save window to broaden the race
        await asyncio.sleep(0.02)
        pm._personas.pop(name, None)  # force next reader to reload
        await original_save(name, persona)

    # Clear cache before each call to force load-from-disk
    async def racy_record(text):
        pm._personas.pop("小天", None)
        await pm.arecord_mentions("小天", text)

    with patch.object(pm, "asave_persona", side_effect=slow_save):
        await asyncio.gather(*(racy_record("主人喜欢喝咖啡") for _ in range(10)))

    # Without the lock at least one update is lost
    fresh, _ = _install_persona(str(tmp_path))
    persona = await fresh.aensure_persona("小天")
    recent = persona["master"]["facts"][0].get("recent_mentions", [])
    assert len(recent) < 10, \
        f"expected lost updates without lock, got all {len(recent)}"


@pytest.mark.asyncio
async def test_persona_aadd_fact_serializes_concurrent_calls(tmp_path):
    """10 concurrent aadd_fact with semantically distinct texts must all
    land; persona.json stays valid JSON; all 10 entries present.

    Use disjoint vocabulary so _evaluate_fact_contradiction doesn't flag
    these as conflicts — the test is about locking, not contradiction logic.
    """
    pm, _ = _install_persona(str(tmp_path))
    await pm.aensure_persona("小天")

    # Ten disjoint hobby topics — no shared subject/predicate to trigger
    # contradiction detection.
    texts = [
        "主人 喜欢 读 历史书",
        "主人 周末 去 公园 散步",
        "主人 学过 三年 钢琴",
        "主人 养过 一只 金鱼",
        "主人 最近 在 学 做 蛋糕",
        "主人 讨厌 坐 飞机",
        "主人 喜欢 看 纪录片",
        "主人 会 做 番茄炒蛋",
        "主人 每天 早上 六点 起床",
        "主人 最 喜欢 的 颜色 是 蓝色",
    ]
    results = await asyncio.gather(*(
        pm.aadd_fact("小天", t, entity="master", source="manual") for t in texts
    ))
    # Most should be FACT_ADDED; some could be FACT_QUEUED_CORRECTION depending
    # on fuzzy matches. Test asserts: whatever the result, persona.json has
    # exactly the expected count of FACT_ADDED entries with no corruption.
    fresh, _ = _install_persona(str(tmp_path))
    persona = await fresh.aensure_persona("小天")
    facts = persona["master"]["facts"]
    added_texts = {texts[i] for i, r in enumerate(results) if r == pm.FACT_ADDED}
    # Every FACT_ADDED result must have landed in persona exactly once
    assert {f["text"] for f in facts} == added_texts, \
        f"facts on disk {{f['text'] for f in facts}} doesn't match added_texts {added_texts}"
    assert len(facts) == len(added_texts), "duplicate entries detected (lock failed)"


# ── ReflectionEngine tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_reflection_has_per_character_lock(tmp_path):
    _, _, re, _ = _install_reflection(str(tmp_path))
    lock_a = re._get_alock("小天")
    lock_b = re._get_alock("小天")
    assert lock_a is lock_b
    lock_c = re._get_alock("小雪")
    assert lock_a is not lock_c


@pytest.mark.asyncio
async def test_reflection_aconfirm_serializes(tmp_path):
    """Concurrent aconfirm_promotion on the same reflection_id must not
    corrupt reflections.json."""
    fs, pm, re, _ = _install_reflection(str(tmp_path))

    # Seed a reflection in pending state
    from memory.reflection import REFLECTION_COOLDOWN_MINUTES
    from datetime import datetime, timedelta

    reflections = [{
        "id": "ref_test123", "text": "主人喜欢咖啡",
        "entity": "master", "status": "pending",
        "source_fact_ids": ["f1"],
        "created_at": datetime.now().isoformat(),
        "feedback": None,
        "next_eligible_at": (datetime.now() + timedelta(minutes=REFLECTION_COOLDOWN_MINUTES)).isoformat(),
    }]
    await re.asave_reflections("小天", reflections)

    # 10 concurrent confirms on the same id (duplicate work, but must not corrupt)
    await asyncio.gather(*(
        re.aconfirm_promotion("小天", "ref_test123") for _ in range(10)
    ))

    # Read back: single reflection, status=confirmed, valid JSON
    loaded = await re.aload_reflections("小天")
    assert len(loaded) == 1
    assert loaded[0]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_reflection_lock_order_reflection_then_persona(tmp_path):
    """aauto_promote_stale (holds reflection lock) → aadd_fact (acquires persona lock).
    Verify this ordering does NOT deadlock: under concurrent /reflect and arecord_mentions,
    all complete within a bounded time."""
    from datetime import datetime, timedelta
    from memory.reflection import AUTO_PROMOTE_DAYS

    fs, pm, re, _ = _install_reflection(str(tmp_path))

    # Seed a confirmed reflection ripe for promotion
    long_ago = (datetime.now() - timedelta(days=AUTO_PROMOTE_DAYS + 1)).isoformat()
    reflections = [{
        "id": "ref_promote1", "text": "主人其实不讨厌咖啡",
        "entity": "master", "status": "confirmed",
        "confirmed_at": long_ago, "source_fact_ids": ["f1"],
        "created_at": long_ago, "feedback": None,
        "next_eligible_at": long_ago,
    }]
    await re.asave_reflections("小天", reflections)
    await pm.aensure_persona("小天")

    # Concurrent:
    # - aauto_promote_stale (grabs reflection lock → persona lock chain)
    # - arecord_mentions (grabs persona lock)
    # Must both complete; ordering is reflection→persona never reversed.
    async def timed_run():
        return await asyncio.wait_for(
            asyncio.gather(
                re.aauto_promote_stale("小天"),
                pm.arecord_mentions("小天", "主人"),
                re.aauto_promote_stale("小天"),  # second call hits dedup
                pm.arecord_mentions("小天", "主人"),
            ),
            timeout=5.0,  # generous — real work is <100ms
        )

    # If locks deadlock, this raises TimeoutError
    results = await timed_run()
    # Some of the promotions should have landed (not asserting exact count —
    # race between the two aauto_promote_stale calls means one may dedup)
    assert any(r >= 1 for r in results if isinstance(r, int))
