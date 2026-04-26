# -*- coding: utf-8 -*-
"""Unit tests for reflection ontology constraints (RFC memory-enhancements §3).

The synthesize flow parses relation_type / temporal_scope from the LLM
response and validates them against the kind-indexed
RELATION_TYPES / KIND_RELATION_MAP (resolved via ENTITY_KINDS). Invalid
fields degrade to None (soft fail) rather than dropping the whole
reflection — the text itself is always preserved."""
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
    ok, _ = _validate_reflection_ontology("master", "preference", "current", "主人喜欢猫")
    assert ok is True


def test_validate_rejects_cross_entity_relation():
    """master 不能用 dynamic（属于 relationship）。"""
    from memory.reflection import _validate_reflection_ontology
    ok, reason = _validate_reflection_ontology("master", "dynamic", "current", "x")
    assert ok is False
    assert "not valid for entity" in reason


def test_validate_rejects_unknown_relation_type():
    from memory.reflection import _validate_reflection_ontology
    ok, reason = _validate_reflection_ontology("master", "nonsense", "current", "x")
    assert ok is False
    assert "unknown relation_type" in reason


def test_validate_rejects_unknown_temporal_scope():
    from memory.reflection import _validate_reflection_ontology
    ok, reason = _validate_reflection_ontology("master", "preference", "yesterday", "x")
    assert ok is False
    assert "temporal_scope" in reason


def test_validate_rejects_overlong_text():
    from memory.reflection import _validate_reflection_ontology
    ok, reason = _validate_reflection_ontology(
        "master", "preference", "current", "x" * 250,
    )
    assert ok is False
    assert "text too long" in reason


def test_validate_tolerates_missing_optional_fields():
    """None-valued optional fields should not cause validation to fail."""
    from memory.reflection import _validate_reflection_ontology
    ok, _ = _validate_reflection_ontology("master", None, None, "主人喜欢猫")
    assert ok is True


# ── kind-based ontology (group-chat readiness) ─────────────────────


def test_entity_kind_resolves_canonical_entities():
    """master/neko/relationship resolve to their declared kinds."""
    from memory.reflection import _entity_kind
    assert _entity_kind("master") == "user"
    assert _entity_kind("neko") == "character"
    assert _entity_kind("relationship") == "relationship"


def test_unknown_entity_defaults_to_user_kind():
    """Future group members (e.g. 'guest_alice') should auto-inherit the
    user template without requiring a code change to RELATION_TYPES."""
    from memory.reflection import _allowed_relation_types, _entity_kind
    assert _entity_kind("guest_alice") == "user"
    allowed = _allowed_relation_types("guest_alice")
    # Same set as `master`, since both are user-kind.
    assert "preference" in allowed
    assert "trait" in allowed
    assert "habit" in allowed
    # Must NOT bleed in character-kind or relationship-kind types.
    assert "self_awareness" not in allowed
    assert "dynamic" not in allowed


def test_validate_accepts_unknown_user_kind_entity():
    """Validator must let a hypothetical group-chat user use the user
    relation set without a schema migration."""
    from memory.reflection import _validate_reflection_ontology
    ok, _ = _validate_reflection_ontology(
        "guest_alice", "preference", "current", "alice 喜欢咖啡",
    )
    assert ok is True


def test_validate_rejects_kind_mismatch_with_helpful_reason():
    """Reason string includes resolved kind so debugging group-chat
    misclassification doesn't require digging into the map."""
    from memory.reflection import _validate_reflection_ontology
    ok, reason = _validate_reflection_ontology(
        "neko", "preference", "current", "x",
    )
    assert ok is False
    assert "kind=" in reason
    assert "character" in reason


# ── synthesize integration ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_synthesize_persists_valid_ontology_fields(tmp_path):
    fs, re = _install(str(tmp_path))
    results = await _run_synth(fs, re, {
        "reflection": "主人偏好用 Python 而非 JavaScript",
        "entity": "master",
        "relation_type": "preference",
        "temporal_scope": "current",
    })
    assert len(results) == 1

    reflections = await re._aload_reflections_full("小天")
    r = reflections[0]
    assert r["relation_type"] == "preference"
    assert r["temporal_scope"] == "current"
    # `confidence` is no longer part of the schema.
    assert "confidence" not in r


@pytest.mark.asyncio
async def test_synthesize_degrades_invalid_relation_type_to_null(tmp_path):
    """LLM 返回了非法的 entity→relation_type 映射时，反思应保留但字段置空。"""
    fs, re = _install(str(tmp_path))
    results = await _run_synth(fs, re, {
        "reflection": "观察到的某个模式",
        "entity": "master",
        "relation_type": "dynamic",  # illegal: dynamic is relationship-only
        "temporal_scope": "current",
    })
    assert len(results) == 1

    reflections = await re._aload_reflections_full("小天")
    r = reflections[0]
    # Soft fail: text preserved but ontology stripped.
    assert r["text"] == "观察到的某个模式"
    assert r["relation_type"] is None
    assert r["temporal_scope"] is None


@pytest.mark.asyncio
async def test_synthesize_demotes_subject_alongside_other_ontology(tmp_path):
    """Soft-fail must strip `subject` too — keeping it would leave a
    half-structured record (no class but still a label)."""
    fs, re = _install(str(tmp_path))
    results = await _run_synth(fs, re, {
        "reflection": "某观察",
        "entity": "master",
        "relation_type": "dynamic",  # illegal for master → triggers demotion
        "temporal_scope": "current",
        "subject": "主人",
    })
    assert len(results) == 1
    reflections = await re._aload_reflections_full("小天")
    r = reflections[0]
    assert r["relation_type"] is None
    assert r["temporal_scope"] is None
    assert r["subject"] is None


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
    assert r["temporal_scope"] is None
    # Entity and text still carry through.
    assert r["entity"] == "relationship"
    assert r["text"] == "legacy-style reflection"


def test_normalize_reflection_backfills_ontology_defaults():
    """Legacy on-disk reflections missing ontology fields normalize to None."""
    from memory.reflection import ReflectionEngine
    legacy = {"id": "r1", "text": "旧反思", "entity": "master", "status": "pending"}
    out = ReflectionEngine._normalize_reflection(legacy)
    assert out["relation_type"] is None
    assert out["temporal_scope"] is None
    assert out["subject"] is None
    # `confidence` is intentionally not in the default schema anymore.
    assert "confidence" not in out
