# -*- coding: utf-8 -*-
"""
Regression: a crash-repair replayed by the Reconciler must survive a live
writer that is already inside record_and_save's critical section.

Scope, precisely. Production no longer overlaps the two at all: startup
awaits reconciliation to completion before it resumes any outbox operation
(app/memory_server/runtime.py). This file covers the second line of defence
— the per-character lock — for the residual case where a live writer does
run concurrently: another caller of areconcile, or a future startup edit
that reintroduces the overlap.

It does NOT cover the window that the lock cannot close: the reflection and
persona paths load their whole view snapshot *before* entering the critical
section, so a writer parked between its own load and its own lock
acquisition still overwrites a repair that landed in between. Only the
startup ordering keeps that from happening.

The damage being guarded against is silent and permanent: the reconciler's
apply-handler rewrites reflections.json, then a live writer saves its own
stale snapshot back over it. The repair is gone from disk while the
sentinel has already advanced past the event, so no later boot ever replays
it again — and not a single exception is raised anywhere.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NAME = "小天"
_NOW = "2026-04-22T10:00:00"


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
    """Same component wiring as memory_server startup (no FastAPI)."""
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
        engine = ReflectionEngine(fs, pm, event_log=event_log)
        engine._config_manager = cm
        rec = Reconciler(event_log)
        register_evidence_handlers(rec, pm, engine)
    return event_log, pm, engine, rec


def _seed_two_reflections():
    return [
        {"id": "A", "text": "A条", "entity": "master", "status": "pending",
         "source_fact_ids": ["f1"], "created_at": _NOW, "feedback": None,
         "next_eligible_at": _NOW, "reinforcement": 0.0, "disputation": 0.0},
        {"id": "B", "text": "B条", "entity": "master", "status": "pending",
         "source_fact_ids": ["f2"], "created_at": _NOW, "feedback": None,
         "next_eligible_at": _NOW, "reinforcement": 0.0, "disputation": 0.0},
    ]


def _read_reflections(tmpdir: str) -> dict[str, dict]:
    with open(os.path.join(tmpdir, NAME, "reflections.json"), encoding="utf-8") as f:
        return {r["id"]: r for r in json.load(f)}


async def test_replayed_repair_survives_live_writer_inside_critical_section(tmp_path):
    """Reconciler replay of A must not be clobbered by a live signal on B.

    The interleaving is forced, not raced:
      1. the reconciler reads its tail (nothing applied yet);
      2. a live writer then enters record_and_save and parks inside the
         critical section, mid event-append — its in-memory view snapshot
         predates the replay and will be written to disk when it resumes;
      3. only then is the reconciler let go.

    Whoever runs the apply-handler must therefore wait for that critical
    section to finish, or the handler's write lands in the middle of it
    and is overwritten seconds later while the sentinel has already moved
    past the event. Note the writer is parked *after* acquiring the lock:
    park it one step earlier, between its own view load and the lock, and
    no lock can save the repair — that case is the startup ordering's job.
    """
    from memory.event_log import EVT_REFLECTION_EVIDENCE_UPDATED

    tmpdir = str(tmp_path)
    ev, _pm, engine, rec = _boot_real_stack(tmpdir)
    await engine.asave_reflections(NAME, _seed_two_reflections())

    # Leave a tail event appended before a crash preceding view save: A's
    # reinforcement should be 7.0, but the view still shows 0.0 with an empty sentinel.
    event_id = ev.append(NAME, EVT_REFLECTION_EVIDENCE_UPDATED, {
        "reflection_id": "A", "reinforcement": 7.0, "disputation": 0.0,
        "rein_last_signal_at": _NOW, "disp_last_signal_at": None,
        "sub_zero_days": 0, "user_fact_reinforce_count": 0,
        "source": "user_confirm",
    })
    assert _read_reflections(tmpdir)["A"]["reinforcement"] == 0.0

    tail_read = asyncio.Event()
    released = asyncio.Event()
    real_aread_since = ev.aread_since

    async def _paused_aread_since(name, after_event_id):
        # After reading the tail (read_since's lock released), pause before apply,
        # allowing the live writer to run. Pause on the event loop without holding locks.
        tail = await real_aread_since(name, after_event_id)
        tail_read.set()
        await released.wait()
        return tail

    parked = threading.Event()
    real_write_line = ev._write_line_unlocked

    def _parked_write_line(path, line):
        # Simulate slow fsync: the live writer holds the per-character lock, but the event
        # and view are not persisted yet, exactly where the old handler could intrude.
        parked.set()
        time.sleep(0.4)
        return real_write_line(path, line)

    pending: list[asyncio.Task] = []
    with patch.object(ev, "aread_since", _paused_aread_since), \
            patch.object(ev, "_write_line_unlocked", _parked_write_line):
        try:
            reconcile_task = asyncio.create_task(rec.areconcile(NAME))
            pending.append(reconcile_task)
            await asyncio.wait_for(tail_read.wait(), timeout=10)

            live_write = asyncio.create_task(
                engine.aapply_signal(NAME, "B", {"reinforcement": 3.0},
                                     source="user_confirm"),
            )
            pending.append(live_write)
            entered = await asyncio.to_thread(parked.wait, 10.0)
            assert entered, "live writer never entered its critical section; 并发前提不成立"

            released.set()
            await asyncio.wait_for(reconcile_task, timeout=30)
            assert await asyncio.wait_for(live_write, timeout=30) is True
        finally:
            # Even an intermediate assertion failure must release paused reconcile so no coroutine/thread remains running,
            # Then collect all unfinished tasks so subsequent cases are not contaminated.
            released.set()
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    final = _read_reflections(tmpdir)
    assert final["A"]["reinforcement"] == 7.0, (
        "replayed crash repair for A was overwritten by the concurrent "
        "live write — this is the silent permanent data loss"
    )
    assert final["B"]["reinforcement"] == 3.0, "live signal on B was lost"
    # Replay must already have passed this event's sentinel: it cannot be replayed again, so an unpersisted A repair
    # is permanently lost rather than retried next startup. The final writer determines sentinel position: the live
    # writer advances it to its own event after A under the lock; replay detects this advancement and
    # freezes without writing back, instead rescanning from its applied position through the log tail to apply A and B
    # in log order. A precedes the sentinel in either case.
    remaining = [r.get("event_id") for r in ev.read_since(NAME, ev.read_sentinel(NAME))]
    assert event_id not in remaining, "A 的事件还在尾巴里，本用例的永久丢失前提不成立"
