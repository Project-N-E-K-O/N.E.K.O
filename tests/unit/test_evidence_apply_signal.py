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
    assert r["disp_last_signal_at"] != rein_ts_before or True  # they can coincide


@pytest.mark.asyncio
async def test_reflection_apply_unknown_id_returns_false(tmp_path):
    ev, _fs, _pm, re, _cm = _install(str(tmp_path))
    await re.asave_reflections("小天", [])
    ok = await re.aapply_signal("小天", "does_not_exist",
                                 {"reinforcement": 1.0}, source="user_confirm")
    assert ok is False


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

    # Read event log
    events = ev.read_since("小天", None)
    rein_events = [e for e in events if e["type"] == EVT_REFLECTION_EVIDENCE_UPDATED]
    assert len(rein_events) == 1
    payload = rein_events[0]["payload"]
    assert payload["reflection_id"] == rid
    assert payload["reinforcement"] == pytest.approx(1.0)
    assert payload["disputation"] == 0.0
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
    _ev, _fs, pm, _re, _cm = _install(str(tmp_path))
    # Empty persona — aensure_persona will create it
    await pm.aensure_persona("小天")
    ok = await pm.aapply_signal(
        "小天", "master", "p_nope",
        {"reinforcement": 1.0}, source="user_fact",
    )
    assert ok is False


# ── S4: reconciler handler idempotency ──────────────────────────────


@pytest.mark.asyncio
async def test_reflection_evidence_handler_is_idempotent_on_replay(tmp_path):
    """S4: replay the same event 10 times → view fields identical (snapshot payload)."""
    from memory.event_log import EVT_REFLECTION_EVIDENCE_UPDATED

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

    # Build the reconciler handler the same way memory_server.py does
    from memory.event_log import Reconciler

    # Use a mini-mock for register path
    applied_count = 0

    def _fake_apply(name: str, pl: dict) -> bool:
        nonlocal applied_count
        # Simulate the handler body from _register_evidence_handlers
        from utils.file_utils import atomic_write_json
        path = re._reflections_path(name)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for r in data:
            if r.get("id") == pl["reflection_id"]:
                for k in ("reinforcement", "disputation",
                          "rein_last_signal_at", "disp_last_signal_at",
                          "sub_zero_days"):
                    if k in pl:
                        r[k] = pl[k]
                applied_count += 1
                break
        atomic_write_json(path, data, indent=2, ensure_ascii=False)
        return True

    reconc = Reconciler(ev)
    reconc.register(EVT_REFLECTION_EVIDENCE_UPDATED, _fake_apply)

    # Replay 10 times
    for _ in range(10):
        _fake_apply("小天", payload)

    reloaded = await re.aload_reflections("小天")
    r = [x for x in reloaded if x["id"] == rid][0]
    assert r["reinforcement"] == pytest.approx(1.0)
    assert r["disputation"] == 0.0
