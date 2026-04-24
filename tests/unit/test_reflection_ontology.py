# -*- coding: utf-8 -*-
"""Unit tests for reflection ontology constraints (RFC memory-enhancements §3).

The synthesize flow parses relation_type / confidence / temporal_scope from
the LLM response and validates them against RELATION_TYPES /
ENTITY_RELATION_MAP / MIN_REFLECTION_CONFIDENCE. Invalid or low-confidence
fields degrade to None (soft fail per §3.3.6) — the reflection text itself
is preserved."""
from __future__ import annotations

import json
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
    return fs, re


async def _run_synth(fs, re, payload: dict):
    """Seed 5 facts + mock LLM to return `payload`, then synthesize."""
    for i in range(5):
        fs._facts.setdefault("小天", []).append({
            "id": f"f{i}", "text": f"事实 {i}",
            "importance": 5, "entity": "master",
            "tags": [], "hash": f"h{i}",
            "created_at": "2026-04-23T10:00:00",
            "absorbed": False,
        })
    await fs.asave_facts("小天")

    resp = MagicMock()
    resp.content = json.dumps(payload)

    async def _ainvoke(*_a, **_k):
        return resp

    async def _aclose():
        return None

    fake_llm = MagicMock()
    fake_llm.ainvoke = _ainvoke
    fake_llm.aclose = _aclose
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        return await re.synthesize_reflections("小天")


# ── happy path ─────────────────────────────────────────────────────


def test_validate_accepts_matching_entity_and_type():
    from memory.reflection import _validate_reflection_ontology
    ok, _ = _validate_reflection_ontology("master", "preference", 0.9, "current", "主人喜欢猫")
    assert ok is True


def test_validate_rejects_cross_entity_relation():
    """master 不能用 dynamic（属于 relationship）。"""
    from memory.reflection import _validate_reflection_ontology
    ok, reason = _validate_reflection_ontology("master", "dynamic", 0.9, "current", "x")
    assert ok is False
    assert "not valid for entity" in reason


def test_validate_rejects_unknown_relation_type():
    from memory.reflection import _validate_reflection_ontology
    ok, reason = _validate_reflection_ontology("master", "nonsense", 0.9, "current", "x")
    assert ok is False
    assert "unknown relation_type" in reason


def test_validate_rejects_low_confidence():
    from memory.reflection import _validate_reflection_ontology
    ok, reason = _validate_reflection_ontology("master", "preference", 0.3, "current", "x")
    assert ok is False
    assert "low confidence" in reason


def test_validate_rejects_out_of_range_confidence():
    """prompt contract is 0.0–1.0; hallucinated 1.7 must not persist."""
    from memory.reflection import _validate_reflection_ontology
    ok, reason = _validate_reflection_ontology("master", "preference", 1.7, "current", "x")
    assert ok is False
    assert "out of range" in reason
    ok2, reason2 = _validate_reflection_ontology("master", "preference", -0.2, "current", "x")
    assert ok2 is False
    assert "out of range" in reason2


def test_validate_rejects_non_finite_confidence():
    """NaN / Infinity must not land on disk — they serialize to non-standard JSON."""
    from memory.reflection import _validate_reflection_ontology
    for bad in (float("nan"), float("inf"), float("-inf")):
        ok, reason = _validate_reflection_ontology("master", "preference", bad, "current", "x")
        assert ok is False, f"{bad!r} should be rejected"
        assert "non-finite" in reason


def test_validate_rejects_overlong_text():
    from memory.reflection import _validate_reflection_ontology
    ok, reason = _validate_reflection_ontology(
        "master", "preference", 0.9, "current", "x" * 250,
    )
    assert ok is False
    assert "text too long" in reason


def test_validate_tolerates_missing_optional_fields():
    """None-valued optional fields should not cause validation to fail."""
    from memory.reflection import _validate_reflection_ontology
    ok, _ = _validate_reflection_ontology("master", None, None, None, "主人喜欢猫")
    assert ok is True


# ── synthesize integration ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_synthesize_persists_valid_ontology_fields(tmp_path):
    fs, re = _install(str(tmp_path))
    results = await _run_synth(fs, re, {
        "reflection": "主人偏好用 Python 而非 JavaScript",
        "entity": "master",
        "relation_type": "preference",
        "confidence": 0.9,
        "temporal_scope": "current",
    })
    assert len(results) == 1

    reflections = await re._aload_reflections_full("小天")
    r = reflections[0]
    assert r["relation_type"] == "preference"
    assert r["confidence"] == pytest.approx(0.9)
    assert r["temporal_scope"] == "current"


@pytest.mark.asyncio
async def test_synthesize_degrades_invalid_relation_type_to_null(tmp_path):
    """LLM 返回了非法的 entity→relation_type 映射时，反思应保留但字段置空。"""
    fs, re = _install(str(tmp_path))
    results = await _run_synth(fs, re, {
        "reflection": "观察到的某个模式",
        "entity": "master",
        "relation_type": "dynamic",  # illegal: dynamic is relationship-only
        "confidence": 0.9,
        "temporal_scope": "current",
    })
    assert len(results) == 1

    reflections = await re._aload_reflections_full("小天")
    r = reflections[0]
    # Soft fail: text preserved but ontology stripped.
    assert r["text"] == "观察到的某个模式"
    assert r["relation_type"] is None
    assert r["confidence"] is None
    assert r["temporal_scope"] is None


@pytest.mark.asyncio
async def test_synthesize_degrades_low_confidence_to_null(tmp_path):
    fs, re = _install(str(tmp_path))
    results = await _run_synth(fs, re, {
        "reflection": "某个不太确定的观察",
        "entity": "master",
        "relation_type": "preference",
        "confidence": 0.2,
        "temporal_scope": "current",
    })
    assert len(results) == 1

    reflections = await re._aload_reflections_full("小天")
    r = reflections[0]
    assert r["relation_type"] is None
    assert r["confidence"] is None


@pytest.mark.asyncio
async def test_synthesize_handles_legacy_prompt_missing_ontology(tmp_path):
    """旧 prompt / 旧 model 不返回 ontology 字段时应当静默通过。"""
    fs, re = _install(str(tmp_path))
    results = await _run_synth(fs, re, {
        "reflection": "legacy-style reflection",
        "entity": "relationship",
    })
    assert len(results) == 1

    reflections = await re._aload_reflections_full("小天")
    r = reflections[0]
    assert r["relation_type"] is None
    assert r["confidence"] is None
    assert r["temporal_scope"] is None
    # Entity and text still carry through.
    assert r["entity"] == "relationship"
    assert r["text"] == "legacy-style reflection"


@pytest.mark.asyncio
async def test_synthesize_tolerates_non_numeric_confidence(tmp_path):
    """LLM 偶尔会返回 confidence 为字符串 'high'；降级为 null，不应崩溃。"""
    fs, re = _install(str(tmp_path))
    results = await _run_synth(fs, re, {
        "reflection": "示例反思",
        "entity": "master",
        "relation_type": "preference",
        "confidence": "high",
        "temporal_scope": "current",
    })
    assert len(results) == 1
    reflections = await re._aload_reflections_full("小天")
    assert reflections[0]["confidence"] is None


def test_normalize_reflection_backfills_ontology_defaults():
    """Legacy on-disk reflections missing ontology fields normalize to None."""
    from memory.reflection import ReflectionEngine
    legacy = {"id": "r1", "text": "旧反思", "entity": "master", "status": "pending"}
    out = ReflectionEngine._normalize_reflection(legacy)
    assert out["relation_type"] is None
    assert out["confidence"] is None
    assert out["temporal_scope"] is None
    assert out["subject"] is None
