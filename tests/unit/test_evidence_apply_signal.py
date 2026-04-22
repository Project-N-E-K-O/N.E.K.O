# -*- coding: utf-8 -*-
"""
Unit tests for ReflectionEngine.aapply_signal / PersonaManager.aapply_signal
(memory-evidence-rfc §3.4 / §3.8.4 / S4).

Verifies:
- full-snapshot EVT_*_EVIDENCE_UPDATED event written
- view (reflections.json / persona.json) updated with new evidence fields
- independent clocks: only the touched side's last_signal_at changes
- unknown target_id → returns False, no event written
"""
from __future__ import annotations

import json
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


# ── Reflection.aapply_signal ────────────────────────────────────────


@pytest.mark.asyncio
async def test_reflection_apply_reinforcement_updates_fields(tmp_path):
    ev, _fs, _pm, re, _cm = _install(str(tmp_path))
    now_iso = "2026-04-22T10:00:00"
    rid = "ref_abc"
    seed = [{
        "id": rid, "text": "主人喜欢猫娘", "entity": "master",
        "status": "pending", "source_fact_ids": ["f1"],
        "created_at": now_iso, "feedback": None,
        "next_eligible_at": now_iso,
    }]
    await re.asave_reflections("小天", seed)

    ok = await re.aapply_signal("小天", rid, {"reinforcement": 1.0}, source="user_confirm")
    assert ok is True

    reloaded = await re.aload_reflections("小天")
    r = [x for x in reloaded if x["id"] == rid][0]
    assert r["reinforcement"] == pytest.approx(1.0)
    assert r["disputation"] == 0.0
    assert r["rein_last_signal_at"] is not None
    assert r["disp_last_signal_at"] is None


@pytest.mark.asyncio
async def test_reflection_apply_independent_clocks(tmp_path):
    """rein signal then disp signal — rein_last_signal_at must stay at the
    original rein tick, not get overwritten by the disp tick."""
    ev, _fs, _pm, re, _cm = _install(str(tmp_path))
    rid = "ref_clock"
    seed = [{
        "id": rid, "text": "x", "entity": "master", "status": "pending",
        "source_fact_ids": ["f1"], "created_at": "2026-04-22T10:00:00",
        "feedback": None, "next_eligible_at": "2026-04-22T10:00:00",
    }]
    await re.asave_reflections("小天", seed)

    # First: reinforcement signal
    await re.aapply_signal("小天", rid, {"reinforcement": 1.0}, source="user_confirm")
    r = [x for x in await re.aload_reflections("小天") if x["id"] == rid][0]
    rein_ts_before = r["rein_last_signal_at"]
    assert rein_ts_before is not None
    assert r["disp_last_signal_at"] is None

    # Second: disputation signal — must NOT overwrite rein timestamp
    await re.aapply_signal("小天", rid, {"disputation": 1.0}, source="user_rebut")
    r = [x for x in await re.aload_reflections("小天") if x["id"] == rid][0]
    assert r["rein_last_signal_at"] == rein_ts_before  # preserved
    assert r["disp_last_signal_at"] is not None  # now set


@pytest.mark.asyncio
async def test_reflection_apply_unknown_id_returns_false(tmp_path):
    ev, _fs, _pm, re, _cm = _install(str(tmp_path))
    await re.asave_reflections("小天", [])
    ok = await re.aapply_signal("小天", "does_not_exist",
                                 {"reinforcement": 1.0}, source="user_confirm")
    assert ok is False
    # Unknown target_id must not produce an event — emitting then rolling
    # back would violate the "append before mutate" discipline (CodeRabbit
    # PR #929 nit).
    assert ev.read_since("小天", None) == []


@pytest.mark.asyncio
async def test_reflection_apply_emits_evidence_event(tmp_path):
    from memory.event_log import EVT_REFLECTION_EVIDENCE_UPDATED
    ev, _fs, _pm, re, _cm = _install(str(tmp_path))
    rid = "ref_evt"
    await re.asave_reflections("小天", [{
        "id": rid, "text": "x", "entity": "master", "status": "pending",
        "source_fact_ids": [], "created_at": "2026-04-22T10:00:00",
        "feedback": None, "next_eligible_at": "2026-04-22T10:00:00",
    }])

    await re.aapply_signal("小天", rid, {"reinforcement": 1.0}, source="user_confirm")

    # Read event log — assert the full-snapshot payload contract so a
    # future regression that drops clock fields or sub_zero_days is
    # caught early (CodeRabbit PR #929 nit).
    events = ev.read_since("小天", None)
    rein_events = [e for e in events if e["type"] == EVT_REFLECTION_EVIDENCE_UPDATED]
    assert len(rein_events) == 1
    payload = rein_events[0]["payload"]
    assert payload["reflection_id"] == rid
    assert payload["reinforcement"] == pytest.approx(1.0)
    assert payload["disputation"] == 0.0
    assert payload["rein_last_signal_at"] is not None  # rein side touched
    assert payload["disp_last_signal_at"] is None      # disp side untouched
    assert payload["sub_zero_days"] == 0
    assert payload["source"] == "user_confirm"


# ── Persona.aapply_signal ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_persona_apply_signal_updates_and_emits_event(tmp_path):
    from memory.event_log import EVT_PERSONA_EVIDENCE_UPDATED
    ev, _fs, pm, _re, _cm = _install(str(tmp_path))

    # Seed a persona entry directly on disk
    persona_path = pm._persona_path("小天")
    os.makedirs(os.path.dirname(persona_path), exist_ok=True)
    persona = {
        "master": {"facts": [{
            "id": "p_001", "text": "主人喜欢咖啡", "source": "manual",
            "reinforcement": 0.0, "disputation": 0.0,
            "rein_last_signal_at": None, "disp_last_signal_at": None,
            "protected": False,
        }]}
    }
    with open(persona_path, "w", encoding="utf-8") as f:
        json.dump(persona, f)

    ok = await pm.aapply_signal(
        "小天", "master", "p_001",
        {"reinforcement": 1.0}, source="user_fact",
    )
    assert ok is True

    # Reload persona, verify fields
    persona = await pm.aensure_persona("小天")
    entry = persona["master"]["facts"][0]
    assert entry["reinforcement"] == pytest.approx(1.0)
    assert entry["rein_last_signal_at"] is not None

    events = ev.read_since("小天", None)
    pe = [e for e in events if e["type"] == EVT_PERSONA_EVIDENCE_UPDATED]
    assert len(pe) == 1
    assert pe[0]["payload"]["entry_id"] == "p_001"


@pytest.mark.asyncio
async def test_persona_apply_signal_unknown_entry_returns_false(tmp_path):
    ev, _fs, pm, _re, _cm = _install(str(tmp_path))
    # Empty persona — aensure_persona will create it
    await pm.aensure_persona("小天")
    ok = await pm.aapply_signal(
        "小天", "master", "p_nope",
        {"reinforcement": 1.0}, source="user_fact",
    )
    assert ok is False
    # Unknown entry must not produce an event (CodeRabbit PR #929 nit).
    assert ev.read_since("小天", None) == []


# ── S4: reconciler handler idempotency ──────────────────────────────


@pytest.mark.asyncio
async def test_reflection_evidence_handler_is_idempotent_on_replay(tmp_path):
    """S4: replay the production reflection.evidence_updated handler 10
    times → view fields stay identical (full-snapshot payload is trivially
    idempotent). Uses the real handler from `memory.evidence_handlers` so
    if production drifts, this test catches it (CodeRabbit PR #929 nit)."""
    from memory.event_log import EVT_REFLECTION_EVIDENCE_UPDATED
    from memory.evidence_handlers import make_reflection_evidence_handler

    ev, _fs, _pm, re, _cm = _install(str(tmp_path))
    rid = "ref_idem"
    await re.asave_reflections("小天", [{
        "id": rid, "text": "x", "entity": "master", "status": "pending",
        "source_fact_ids": [], "created_at": "2026-04-22T10:00:00",
        "feedback": None, "next_eligible_at": "2026-04-22T10:00:00",
    }])

    # Apply once normally — captures the event
    await re.aapply_signal("小天", rid, {"reinforcement": 1.0}, source="user_confirm")
    events = ev.read_since("小天", None)
    [evt] = [e for e in events if e["type"] == EVT_REFLECTION_EVIDENCE_UPDATED]
    payload = evt["payload"]

    # Grab the actual production handler closure
    apply_fn = make_reflection_evidence_handler(re)

    # First replay should no-op (view already at snapshot value) — we
    # still call it to exercise the read/compare path.
    changed_first = apply_fn("小天", payload)
    assert changed_first is False

    # 9 more replays — each must also report no-op to harden the
    # idempotence guarantee (CodeRabbit PR #929 nit).
    for _ in range(9):
        assert apply_fn("小天", payload) is False

    reloaded = await re.aload_reflections("小天")
    r = [x for x in reloaded if x["id"] == rid][0]
    assert r["reinforcement"] == pytest.approx(1.0)
    assert r["disputation"] == 0.0
    assert r["rein_last_signal_at"] is not None


@pytest.mark.asyncio
async def test_prepare_save_preserves_merged_and_promote_blocked(tmp_path):
    """`merged` / `promote_blocked` are non-archivable terminals and must
    stay in the main reflections file across save cycles (RFC §3.11.3 +
    CodeRabbit PR #929). Regression for silent drop when `aauto_promote_stale`
    filters its active set through REFLECTION_TERMINAL_STATUSES."""
    _ev, _fs, _pm, re, _cm = _install(str(tmp_path))
    now_iso = "2026-04-22T10:00:00"

    # Seed disk with three on-disk entries: one active-pending,
    # one merged (non-archivable), one promote_blocked (dead-letter).
    disk = [
        {"id": "ref_active", "text": "a", "entity": "master", "status": "pending",
         "source_fact_ids": [], "created_at": now_iso, "feedback": None,
         "next_eligible_at": now_iso},
        {"id": "ref_merged", "text": "m", "entity": "master", "status": "merged",
         "source_fact_ids": [], "created_at": now_iso, "feedback": None,
         "next_eligible_at": now_iso, "absorbed_into": "p_target"},
        {"id": "ref_blocked", "text": "b", "entity": "master", "status": "promote_blocked",
         "source_fact_ids": [], "created_at": now_iso, "feedback": None,
         "next_eligible_at": now_iso, "promote_blocked_reason": "llm_unavailable"},
    ]
    # Write directly bypassing the merge-save logic to simulate prior state
    path = re._reflections_path("小天")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(disk, f)

    # Now save only the active entry (as `_aauto_promote_stale_locked` would
    # do after filtering via REFLECTION_TERMINAL_STATUSES).
    await re.asave_reflections("小天", [disk[0]])

    # Re-read ALL entries (include_archived=True sidesteps the active filter).
    reloaded = await re.aload_reflections("小天", include_archived=True)
    ids = {r["id"] for r in reloaded}
    assert "ref_active" in ids
    assert "ref_merged" in ids, "merged must survive save cycles"
    assert "ref_blocked" in ids, "promote_blocked must survive save cycles"


@pytest.mark.asyncio
async def test_persona_entry_handler_sha_mismatch_raises(tmp_path):
    """S5: persona.entry_updated handler raises on text sha256 mismatch —
    reconciler must stop and let a human inspect (RFC §3.3.6)."""
    from memory.evidence_handlers import make_persona_entry_handler

    _ev, _fs, pm, _re, _cm = _install(str(tmp_path))
    # Seed a persona entry with known text
    persona_path = pm._persona_path("小天")
    os.makedirs(os.path.dirname(persona_path), exist_ok=True)
    persona = {"master": {"facts": [{"id": "p_x", "text": "current text",
                                     "source": "manual", "protected": False}]}}
    with open(persona_path, "w", encoding="utf-8") as f:
        json.dump(persona, f)

    apply_fn = make_persona_entry_handler(pm)
    bad_payload = {
        "entity_key": "master",
        "entry_id": "p_x",
        "rewrite_text_sha256": "0" * 64,  # definitely not the sha of "current text"
        "reinforcement": 5.0,
    }
    with pytest.raises(RuntimeError):
        apply_fn("小天", bad_payload)
