# -*- coding: utf-8 -*-
"""
Reconciler apply-handlers for the three evidence event types
(memory-evidence-rfc §3.3.6).

Extracted from memory_server.py so unit tests can register the same
handler bodies the production reconciler runs — per CodeRabbit PR #929
review, duplicating handler logic inside a test fixture defeats the
idempotency assertion (if production drifts, the test stays green).

Handlers are pure sync functions matching `ApplyHandler` contract:
  (character_name: str, payload: dict) -> bool (True=view changed).
"""
from __future__ import annotations

import hashlib
import json
import os

from memory.event_log import (
    EVT_PERSONA_ENTRY_UPDATED,
    EVT_PERSONA_EVIDENCE_UPDATED,
    EVT_REFLECTION_EVIDENCE_UPDATED,
    EVT_REFLECTION_STATE_CHANGED,
    Reconciler,
)
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")


_EVIDENCE_SNAPSHOT_KEYS = (
    'reinforcement', 'disputation',
    'rein_last_signal_at', 'disp_last_signal_at',
    'sub_zero_days',
    # user_fact combo counter (RFC §3.1.8) — 必须走 event log 的 replay
    # 路径才能让重放后 view 的 combo 状态一致
    'user_fact_reinforce_count',
)
# Reflection-side evidence events also carry promote throttle counters
# (RFC §3.9.2) — must replay so a crash between event-append and view-save
# preserves the backoff window / dead-letter logic.
_REFLECTION_EVIDENCE_SNAPSHOT_KEYS = _EVIDENCE_SNAPSHOT_KEYS + (
    'last_promote_attempt_at', 'promote_attempt_count',
)
_PERSONA_ENTRY_SNAPSHOT_KEYS = _EVIDENCE_SNAPSHOT_KEYS + ('merged_from_ids',)


def make_reflection_evidence_handler(reflection_engine):
    """Build the `reflection.evidence_updated` apply handler bound to an
    engine instance (for file-path resolution)."""

    def _apply(name: str, payload: dict) -> bool:
        rid = payload.get('reflection_id')
        if not rid:
            return False
        path = reflection_engine._reflections_path(name)
        # File-not-exists is a normal state (e.g. first boot, new character)
        # → empty view, return False (no-op). But load FAILURES (corrupt
        # JSON, disk IO error) must propagate: swallowing them would let
        # `Reconciler.areconcile` advance the sentinel past this event,
        # permanently losing the mutation (CodeRabbit PR #929 critical).
        data: list[dict] = []
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        if not isinstance(data, list):
            # Top-level shape wrong — can't apply the event, but silently
            # coercing to empty + returning False would let the reconciler
            # advance the sentinel past this event and lose it forever.
            # Raise instead so replay pauses and operator can fix the file
            # (CodeRabbit PR #929 round-2 on round-11).
            raise RuntimeError(
                f"[EvidenceHandler] {path}: 期望 list，实际 "
                f"{type(data).__name__}；view 结构异常，暂停 replay"
            )
        changed = False
        for r in data:
            if not isinstance(r, dict) or r.get('id') != rid:
                continue
            for k in _REFLECTION_EVIDENCE_SNAPSHOT_KEYS:
                if k in payload and r.get(k) != payload[k]:
                    r[k] = payload[k]
                    changed = True
            break
        if changed:
            atomic_write_json(path, data, indent=2, ensure_ascii=False)
        return changed

    return _apply


def make_reflection_state_changed_handler(reflection_engine):
    """Build the `reflection.state_changed` apply handler.

    Replays the `to` status onto the reflection identified by
    `reflection_id`, plus any audit fields the producer recorded
    (`absorbed_into`, `promote_blocked_reason`, `reject_reason`,
    `<status>_at` timestamp). RFC §3.9.6 — without a handler, a crash
    after the event log append but before the view save would leak a
    "phantom unflipped" reflection on next boot.
    """

    def _apply(name: str, payload: dict) -> bool:
        rid = payload.get('reflection_id')
        to_status = payload.get('to')
        if not rid or not to_status:
            return False
        path = reflection_engine._reflections_path(name)
        data: list[dict] = []
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        if not isinstance(data, list):
            raise RuntimeError(
                f"[EvidenceHandler] {path}: 期望 list，实际 "
                f"{type(data).__name__}；view 结构异常，暂停 replay"
            )
        changed = False
        for r in data:
            if not isinstance(r, dict) or r.get('id') != rid:
                continue
            if r.get('status') != to_status:
                r['status'] = to_status
                changed = True
            ts = payload.get('ts')
            if ts:
                ts_key = f'{to_status}_at'
                if r.get(ts_key) != ts:
                    r[ts_key] = ts
                    changed = True
            for k in ('absorbed_into',):
                if k in payload and r.get(k) != payload[k]:
                    r[k] = payload[k]
                    changed = True
            if 'reason' in payload and to_status == 'promote_blocked':
                if r.get('promote_blocked_reason') != payload['reason']:
                    r['promote_blocked_reason'] = payload['reason']
                    changed = True
            if 'reject_explanation' in payload:
                if r.get('reject_reason') != payload['reject_explanation']:
                    r['reject_reason'] = payload['reject_explanation']
                    changed = True
            break
        if changed:
            atomic_write_json(path, data, indent=2, ensure_ascii=False)
        return changed

    return _apply


def make_persona_evidence_handler(persona_manager):
    """Build the `persona.evidence_updated` apply handler."""

    def _apply(name: str, payload: dict) -> bool:
        entity_key = payload.get('entity_key')
        entry_id = payload.get('entry_id')
        if not entity_key or not entry_id:
            return False
        path = persona_manager._persona_path(name)
        # Let load failures propagate — see reflection handler above for
        # the full rationale.
        persona: dict = {}
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                persona = json.load(f)
        if not isinstance(persona, dict):
            # Same rationale as the reflection handler above: don't let
            # replay advance past an event we can't apply.
            raise RuntimeError(
                f"[EvidenceHandler] {path}: 期望 dict，实际 "
                f"{type(persona).__name__}；view 结构异常，暂停 replay"
            )
        section = persona.get(entity_key)
        if not isinstance(section, dict):
            return False
        facts = section.get('facts', [])
        changed = False
        for e in facts:
            if not isinstance(e, dict) or e.get('id') != entry_id:
                continue
            for k in _EVIDENCE_SNAPSHOT_KEYS:
                if k in payload and e.get(k) != payload[k]:
                    e[k] = payload[k]
                    changed = True
            break
        if changed:
            persona_manager._personas[name] = persona
            atomic_write_json(path, persona, indent=2, ensure_ascii=False)
        return changed

    return _apply


def make_persona_entry_handler(persona_manager):
    """Build the `persona.entry_updated` apply handler.

    RFC §3.3.6: text 不在 payload；通过 rewrite_text_sha256 核对 view 是否
    已 apply。mismatch → raise，让 reconciler 暂停等人工。PR-1 只处理
    evidence 字段；PR-3 的 merge-on-promote 会实际改写 text。
    """

    def _apply(name: str, payload: dict) -> bool:
        entity_key = payload.get('entity_key')
        entry_id = payload.get('entry_id')
        expected_sha = payload.get('rewrite_text_sha256')
        if not entity_key or not entry_id:
            return False
        path = persona_manager._persona_path(name)
        if not os.path.exists(path):
            return False
        # Let JSONDecodeError / OSError propagate — same rationale as the
        # evidence handlers above. A silent fallback would advance the
        # sentinel past a text-rewrite event while the view still holds
        # the old text; the sha256 mismatch check one level down would
        # then fire on the NEXT event pointing at this entry instead of
        # on this one, and the human chasing the bug would look at the
        # wrong event.
        with open(path, encoding='utf-8') as f:
            persona = json.load(f)
        if not isinstance(persona, dict):
            raise RuntimeError(
                f"[EvidenceHandler] {path}: 期望 dict，实际 "
                f"{type(persona).__name__}；view 结构异常，暂停 replay"
            )
        section = persona.get(entity_key)
        if not isinstance(section, dict):
            return False
        facts = section.get('facts', [])
        for e in facts:
            if not isinstance(e, dict) or e.get('id') != entry_id:
                continue
            if expected_sha:
                current_sha = hashlib.sha256(
                    (e.get('text') or '').encode('utf-8'),
                ).hexdigest()
                if current_sha != expected_sha:
                    raise RuntimeError(
                        f"[Reconciler] {name}/persona.entry_updated: "
                        f"entry {entry_id} text sha256 mismatch; "
                        f"view drifted from log, manual inspection required"
                    )
            changed = False
            for k in _PERSONA_ENTRY_SNAPSHOT_KEYS:
                if k in payload and e.get(k) != payload[k]:
                    e[k] = payload[k]
                    changed = True
            if changed:
                persona_manager._personas[name] = persona
                atomic_write_json(path, persona, indent=2, ensure_ascii=False)
            return changed
        return False

    return _apply


def register_evidence_handlers(
    reconciler: Reconciler,
    persona_manager,
    reflection_engine,
) -> None:
    """Register the evidence + state-change handlers on a reconciler.

    Call once per boot (memory_server startup) and per hot-reload — the
    closures capture the current manager instances so reload-swapped
    instances see their own file paths."""
    reconciler.register(
        EVT_REFLECTION_EVIDENCE_UPDATED,
        make_reflection_evidence_handler(reflection_engine),
    )
    reconciler.register(
        EVT_PERSONA_EVIDENCE_UPDATED,
        make_persona_evidence_handler(persona_manager),
    )
    reconciler.register(
        EVT_PERSONA_ENTRY_UPDATED,
        make_persona_entry_handler(persona_manager),
    )
    # PR-3 (RFC §3.9.6): merge-on-promote flips reflection status to
    # `merged` / `promote_blocked` / `denied` etc. via state_changed events.
    # Replay needs a handler to re-apply the status to the view.
    reconciler.register(
        EVT_REFLECTION_STATE_CHANGED,
        make_reflection_state_changed_handler(reflection_engine),
    )
