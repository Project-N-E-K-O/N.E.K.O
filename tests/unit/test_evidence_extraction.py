# -*- coding: utf-8 -*-
"""
Unit tests for FactStore.aextract_facts_and_detect_signals (§3.4.2)
and negative-keyword scanning (§3.4.5).

Covers S6, S7, S8 from memory-evidence-rfc §8 success criteria.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakeMessage:
    def __init__(self, content, mtype="human"):
        self.content = content
        self.type = mtype


def _mock_cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "ai": "小天", "system": "SYS"},
        {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "ai": "小天", "system": "SYS"},
        {}, {}, {}, {},
    ))
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake", "base_url": "http://fake", "api_key": "sk-fake",
    })
    return cm


def _install_factstore(tmpdir: str):
    from memory.facts import FactStore

    cm = _mock_cm(tmpdir)
    with patch("memory.facts.get_config_manager", return_value=cm):
        fs = FactStore()
    fs._config_manager = cm
    return fs, cm


# ── scan_negative_keywords ──────────────────────────────────────────


def test_negative_keywords_hit():
    from config.prompts_memory import scan_negative_keywords
    assert scan_negative_keywords("这个话题别再说了", "zh") is True
    assert scan_negative_keywords("换个话题吧", "zh") is True
    assert scan_negative_keywords("今天天气不错", "zh") is False


def test_negative_keywords_fallback_unknown_lang():
    from config.prompts_memory import scan_negative_keywords
    # Unknown lang → fall back to zh
    assert scan_negative_keywords("别提了", "xyz") is True


def test_negative_keywords_english():
    from config.prompts_memory import scan_negative_keywords
    assert scan_negative_keywords("please stop talking about this", "en") is True
    assert scan_negative_keywords("the weather is nice", "en") is False


# ── S6: Stage-1 prompt carries no existing-observation context ──────


@pytest.mark.asyncio
async def test_stage1_prompt_has_no_existing_observation_section(tmp_path):
    """Stage-1 prompt must not include existing reflection/persona text
    to avoid self-cycling (RFC §3.4.2)."""
    fs, _cm = _install_factstore(str(tmp_path))
    captured_prompt = {}

    async def _fake_llm(prompt, lanlan_name, tier, call_type, **kw):
        captured_prompt['value'] = prompt
        return []

    with patch.object(fs, '_allm_call_with_retries', new=AsyncMock(side_effect=_fake_llm)):
        await fs._allm_extract_facts("小天", [_FakeMessage("主人说他喜欢咖啡")])

    text = captured_prompt['value']
    # Sanity: prompt contains the conversation
    assert "主人说他喜欢咖啡" in text
    # Must NOT have an existing observations section
    assert "已有观察" not in text
    assert "existing observation" not in text.lower()


# ── S7: Stage-2 target_id validation rejects hallucinations ─────────


@pytest.mark.asyncio
async def test_stage2_drops_unknown_target_ids(tmp_path):
    fs, _cm = _install_factstore(str(tmp_path))
    new_facts = [{"id": "fact_001", "text": "主人爱咖啡", "entity": "master",
                  "importance": 7, "tags": [], "hash": "h1",
                  "created_at": "2026-04-22T10:00:00", "absorbed": False}]
    existing = [
        {"id": "reflection.r_real", "raw_id": "r_real",
         "target_type": "reflection", "text": "主人喜欢咖啡",
         "entity": "master", "score": 1.0},
    ]

    async def _fake_llm(*a, **kw):
        # LLM returns one valid, one hallucinated
        return {
            "signals": [
                {"source_fact_id": "fact_001", "target_type": "reflection",
                 "target_id": "r_real", "signal": "reinforces",
                 "reason": "咖啡"},
                {"source_fact_id": "fact_001", "target_type": "reflection",
                 "target_id": "r_fake_hallucinated", "signal": "reinforces",
                 "reason": "编造的"},
            ]
        }

    with patch.object(fs, '_allm_call_with_retries', new=AsyncMock(side_effect=_fake_llm)):
        signals = await fs._allm_detect_signals("小天", new_facts, existing)

    assert signals is not None
    assert len(signals) == 1
    assert signals[0]['target_id'] == 'r_real'


@pytest.mark.asyncio
async def test_stage2_accepts_raw_id_prefix_mismatch(tmp_path):
    """LLM often returns 'r_real' instead of 'reflection.r_real' — resolve
    by endswith match when the raw id is unambiguous."""
    fs, _cm = _install_factstore(str(tmp_path))
    new_facts = [{"id": "fact_001", "text": "主人爱咖啡", "entity": "master",
                  "importance": 7, "tags": [], "hash": "h1",
                  "created_at": "2026-04-22T10:00:00", "absorbed": False}]
    existing = [
        {"id": "reflection.r_real", "raw_id": "r_real",
         "target_type": "reflection", "text": "主人喜欢咖啡",
         "entity": "master", "score": 1.0},
    ]

    async def _fake_llm(*a, **kw):
        return {
            "signals": [{
                "source_fact_id": "fact_001", "target_type": "reflection",
                "target_id": "r_real",  # raw id, not full prompt id
                "signal": "reinforces", "reason": "咖啡",
            }]
        }

    with patch.object(fs, '_allm_call_with_retries', new=AsyncMock(side_effect=_fake_llm)):
        signals = await fs._allm_detect_signals("小天", new_facts, existing)

    assert signals is not None
    assert len(signals) == 1
    assert signals[0]['target_id'] == 'r_real'
    assert signals[0]['target_full_id'] == 'reflection.r_real'


# ── S8: Stage-1 failure aborts; Stage-2 failure keeps facts ─────────


@pytest.mark.asyncio
async def test_stage1_failure_raises_no_cursor_advance(tmp_path):
    """Stage-1 terminal failure must raise FactExtractionFailed so the
    caller keeps the cursor untouched (RFC §3.4.3 Codex review P1)."""
    from memory.facts import FactExtractionFailed

    fs, _cm = _install_factstore(str(tmp_path))

    async def _fail_call(*a, **kw):
        return None  # terminal LLM failure

    with patch.object(fs, '_allm_call_with_retries',
                       new=AsyncMock(side_effect=_fail_call)):
        with pytest.raises(FactExtractionFailed):
            await fs.aextract_facts_and_detect_signals(
                "小天", [_FakeMessage("主人喜欢咖啡")],
            )

    # No facts written
    assert await fs.aload_facts("小天") == []


@pytest.mark.asyncio
async def test_legacy_extract_facts_swallows_stage1_failure(tmp_path):
    """The Stage-1-only backward-compat entry must still return [] on
    Stage-1 terminal failure (per-turn caller treats it as best-effort)."""
    fs, _cm = _install_factstore(str(tmp_path))

    async def _fail_call(*a, **kw):
        return None

    with patch.object(fs, '_allm_call_with_retries',
                       new=AsyncMock(side_effect=_fail_call)):
        facts = await fs.extract_facts([_FakeMessage("主人喜欢咖啡")], "小天")
    assert facts == []


@pytest.mark.asyncio
async def test_stage2_failure_keeps_facts_drops_signals(tmp_path):
    fs, _cm = _install_factstore(str(tmp_path))

    calls = {"n": 0}

    async def _staged(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            # Stage-1 returns a valid fact list
            return [{"text": "主人爱咖啡", "importance": 7, "entity": "master"}]
        # Stage-2 fails
        return None

    # Fake reflection/persona managers that expose at least one signal target
    fake_ref = MagicMock()
    fake_ref._aload_reflections_full = AsyncMock(return_value=[
        {"id": "r_real", "status": "confirmed", "text": "主人喜欢咖啡",
         "entity": "master", "reinforcement": 1.0, "disputation": 0.0,
         "rein_last_signal_at": "2026-04-22T09:00:00",
         "disp_last_signal_at": None, "protected": False},
    ])
    fake_persona = MagicMock()
    fake_persona.aensure_persona = AsyncMock(return_value={})

    with patch.object(fs, '_allm_call_with_retries',
                       new=AsyncMock(side_effect=_staged)):
        facts, signals, batch_ids = await fs.aextract_facts_and_detect_signals(
            "小天", [_FakeMessage("主人喜欢咖啡")],
            reflection_engine=fake_ref, persona_manager=fake_persona,
        )

    # Stage-1 succeeded — facts written
    assert len(facts) == 1
    stored = await fs.aload_facts("小天")
    assert len(stored) == 1
    # Stage-2 failed — signals empty, batch_ids empty (no checkpoint), but facts retained
    assert signals == []
    assert batch_ids == []
    # signal_processed must remain False so next idle tick retries this batch
    assert not stored[0].get('signal_processed', False)


# ── importance < 5 facts are now stored (§3.1.3) ─────────────────────


@pytest.mark.asyncio
async def test_low_importance_facts_are_persisted(tmp_path):
    fs, _cm = _install_factstore(str(tmp_path))

    async def _extract(*a, **kw):
        return [
            {"text": "主人今天喝了咖啡", "importance": 2, "entity": "master"},
            {"text": "主人喜欢猫娘", "importance": 8, "entity": "master"},
        ]

    with patch.object(fs, '_allm_call_with_retries',
                       new=AsyncMock(side_effect=_extract)):
        facts = await fs.extract_facts([_FakeMessage("...")], "小天")

    # Both persisted (low importance no longer dropped at extraction)
    assert len(facts) == 2
    stored = await fs.aload_facts("小天")
    assert len(stored) == 2
    # But get_unabsorbed_facts(min_importance=5) only sees the high one
    high_only = await fs.aget_unabsorbed_facts("小天", min_importance=5)
    assert len(high_only) == 1
    assert high_only[0]['importance'] == 8
