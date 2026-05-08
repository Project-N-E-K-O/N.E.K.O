# -*- coding: utf-8 -*-
"""
Integration test for the archive sweep loop (RFC §3.5 / PR-2).

Boots a real `MemoryServer` fixture (event_log + reflection_engine +
persona_manager + reconciler wired together — same as production
startup), populates a reflection with a strongly negative evidence
score, drives the sub_zero counter to threshold via repeated
`aincrement_sub_zero` calls (one per simulated day), then verifies
`aarchive_reflection` lands the entry into a sharded archive file and
removes it from the active view.

This is the per-loop body of `_periodic_archive_sweep_loop` exercised
end-to-end without the asyncio.sleep / character-loader / multi-char
gather wrapper. The wrapper itself is exercised by direct unit tests
in `test_evidence_archive.py`; this file's value is asserting the full
view+event+shard+reconcile chain holds across the production wiring
path (catches "I forgot to register the handler" type regressions).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import EVIDENCE_ARCHIVE_DAYS


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


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


def _boot_real_stack(tmpdir: str):
    """Mirror `memory_server.startup_event_handler` step 3 (component
    instantiation) without bringing FastAPI into the picture. Returns
    the same tuple of (event_log, fact_store, persona_manager,
    reflection_engine, reconciler, config_manager) that production
    holds in module-globals."""
    from memory.event_log import EventLog, Reconciler
    from memory.evidence_handlers import register_evidence_handlers
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
        rec = Reconciler(event_log)
        register_evidence_handlers(rec, pm, re)
    return event_log, fs, pm, re, rec, cm


async def test_full_sweep_cycle_archives_negative_reflection(tmp_path):
    """End-to-end: confirmed reflection with strongly negative evidence
    → daily ticks for EVIDENCE_ARCHIVE_DAYS → archive shard appears,
    active view loses the entry, event log carries replay-stable events.
    """
    ev, _fs, pm, re, rec, _cm = _boot_real_stack(str(tmp_path))
    rid = "ref_integration_arch"
    base = datetime(2026, 4, 23, 12, 0, 0)
    seed = [{
        "id": rid, "text": "整合测试", "entity": "master",
        "status": "confirmed", "source_fact_ids": [],
        "created_at": base.isoformat(),
        "feedback": None, "next_eligible_at": base.isoformat(),
        "reinforcement": 0.0,
        "disputation": 5.0,
        "rein_last_signal_at": None,
        "disp_last_signal_at": base.isoformat(),
        "sub_zero_days": 0,
        "sub_zero_last_increment_date": None,
    }]
    await re.asave_reflections("小天", seed)

    # Day-by-day increment (same per-day cadence as the periodic sweep)
    counts: list[int] = []
    for d in range(EVIDENCE_ARCHIVE_DAYS):
        # Refresh disp_last_signal_at to keep the score reading negative
        # under read-time decay — without this, after ~180 days the disp
        # would drop below rein and the entry would stop registering as
        # sub-zero. For EVIDENCE_ARCHIVE_DAYS=14 we are well within one
        # disp half-life (180d) so it's a no-op here, but explicit beats
        # accidental coupling to half-life value (RFC §6.5 Gate 1
        # placeholder).
        c = await re.aincrement_sub_zero("小天", rid, base + timedelta(days=d))
        counts.append(c)
    assert counts == list(range(1, EVIDENCE_ARCHIVE_DAYS + 1)), (
        f"expected monotonic 1..{EVIDENCE_ARCHIVE_DAYS}, got {counts}"
    )

    # Now archive (sweep loop's archive branch)
    archived_ok = await re.aarchive_reflection("小天", rid)
    assert archived_ok is True

    # Active view: gone
    active = await re.aload_reflections("小天", include_archived=True)
    assert all(r.get("id") != rid for r in active), (
        f"expected {rid} removed, found in {[r.get('id') for r in active]}"
    )

    # Shard file: contains the entry with archived_at + path metadata
    archive_dir = re._reflections_archive_dir("小天")
    assert os.path.isdir(archive_dir)
    shard_files = sorted(
        f for f in os.listdir(archive_dir) if f.endswith(".json")
    )
    assert len(shard_files) >= 1
    found = None
    for fn in shard_files:
        with open(os.path.join(archive_dir, fn), encoding="utf-8") as f:
            data = json.load(f)
        for entry in data:
            if entry.get("id") == rid:
                found = (fn, entry)
                break
        if found:
            break
    assert found is not None, f"{rid} not found in any shard"
    fn, archived = found
    assert archived["status"] == "archived"
    assert archived["archived_at"]
    assert archived["archive_shard_path"] == fn

    # Reconciler replay: starting from sentinel=None and re-applying every
    # event must NOT reintroduce the archived entry. This catches a
    # missing-handler regression where the reconciler would pause on
    # state_changed and silently skip subsequent evidence updates.
    # Reset the sentinel to None so we replay everything.
    await ev.aadvance_sentinel("小天", None)
    applied = await rec.areconcile("小天")
    # The view is already in its final state (saved by record_and_save),
    # so the handlers find it consistent → most return False (no change)
    # but the loop completes and never throws / pauses.
    assert applied >= 0
    # Final state still has the entry archived
    refls = await re.aload_reflections("小天", include_archived=True)
    assert all(r.get("id") != rid for r in refls)


async def test_sweep_skips_protected_persona_entry(tmp_path):
    """Protected persona entry: even with massive disputation and many
    sub_zero ticks, archive sweep must not remove it (RFC §3.5.7)."""
    _ev, _fs, pm, _re, _rec, _cm = _boot_real_stack(str(tmp_path))
    base = datetime(2026, 4, 23, 12, 0, 0)
    persona = await pm.aensure_persona("小天")
    persona.setdefault("master", {}).setdefault("facts", []).append({
        "id": "card_protected_int", "text": "user is 主人",
        "source": "character_card", "protected": True,
        "reinforcement": 0.0, "disputation": 100.0,
        "disp_last_signal_at": base.isoformat(),
        "sub_zero_days": 0,
    })
    await pm.asave_persona("小天", persona)

    # Try ticks for a week — every increment is a no-op because protected
    for d in range(7):
        result = await pm.aincrement_sub_zero(
            "小天", "master", "card_protected_int", base + timedelta(days=d),
        )
        assert result is None

    # And explicit archive call is also a no-op
    archived = await pm.aarchive_persona_entry(
        "小天", "master", "card_protected_int",
    )
    assert archived is False
    persona = await pm.aensure_persona("小天")
    facts = persona.get("master", {}).get("facts", [])
    assert any(f.get("id") == "card_protected_int" for f in facts)
