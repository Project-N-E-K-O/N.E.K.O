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
    EVT_PERSONA_FACT_ADDED,
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
    # 防抖字段（archive sweep 写入）：与 sub_zero_days 一对，必须随其
    # 一起 replay，否则重放后 view 同一天可能再被 +1（破坏防抖语义）。
    'sub_zero_last_increment_date',
    # user_fact combo counter (RFC §3.1.8) — 必须走 event log 的 replay
    # 路径才能让重放后 view 的 combo 状态一致
    'user_fact_reinforce_count',
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
            for k in _EVIDENCE_SNAPSHOT_KEYS:
                if k in payload and r.get(k) != payload[k]:
                    r[k] = payload[k]
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


def make_reflection_archive_handler(reflection_engine):
    """Build the `reflection.state_changed` apply handler.

    PR-2: only the archive transition (to='archived') is wired.
    Non-archive state_changed events remain view-writer-driven
    (confirm_promotion / reject_promotion write the view directly and
    currently do not emit state_changed events). For `to='archived'`:
    remove the entry from the active view; the shard file was already
    written before the event was appended (see
    `ReflectionEngine.aarchive_reflection`). Idempotent if replayed —
    the `if entry exists` guard makes a second pass a no-op.
    """

    def _apply(name: str, payload: dict) -> bool:
        rid = payload.get('reflection_id')
        to_status = payload.get('to')
        if not rid or to_status != 'archived':
            # Forward-compat: unknown state transitions are a no-op in
            # this handler; PR-3 may add handlers for promoted/denied.
            return False
        path = reflection_engine._reflections_path(name)
        data: list[dict] = []
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        if not isinstance(data, list):
            raise RuntimeError(
                f"[ArchiveHandler] {path}: 期望 list，实际 "
                f"{type(data).__name__}；view 结构异常，暂停 replay"
            )
        before = len(data)
        data = [r for r in data if not (isinstance(r, dict) and r.get('id') == rid)]
        if len(data) == before:
            return False  # already removed (idempotent replay)
        atomic_write_json(path, data, indent=2, ensure_ascii=False)
        return True

    return _apply


def make_persona_archive_handler(persona_manager):
    """Build the `persona.fact_added` apply handler for archive events.

    RFC §3.5.6: persona archive 复用 fact_added 事件，用 payload 里的
    `archive_shard_path` 字段区分主路径的 fact_added（该路径未来可能由
    PR-3 emit，但当前代码未使用）。PR-2 handler 只对带 archive_shard_path
    的 payload 做归档（从主视图移除 entry）。不带该字段的 payload 当前
    视为 no-op — 正向 fact_added 还没走事件路径。
    """

    def _apply(name: str, payload: dict) -> bool:
        if not payload.get('archive_shard_path'):
            return False  # Not an archive event → no-op for PR-2
        entity_key = payload.get('entity_key')
        entry_id = payload.get('entry_id')
        if not entity_key or not entry_id:
            return False
        path = persona_manager._persona_path(name)
        if not os.path.exists(path):
            return False
        with open(path, encoding='utf-8') as f:
            persona = json.load(f)
        if not isinstance(persona, dict):
            raise RuntimeError(
                f"[ArchiveHandler] {path}: 期望 dict，实际 "
                f"{type(persona).__name__}；view 结构异常，暂停 replay"
            )
        section = persona.get(entity_key)
        if not isinstance(section, dict):
            return False
        facts = section.get('facts', [])
        before = len(facts)
        section['facts'] = [
            e for e in facts
            if not (isinstance(e, dict) and e.get('id') == entry_id)
        ]
        if len(section['facts']) == before:
            return False
        persona_manager._personas[name] = persona
        atomic_write_json(path, persona, indent=2, ensure_ascii=False)
        return True

    return _apply


def register_evidence_handlers(
    reconciler: Reconciler,
    persona_manager,
    reflection_engine,
) -> None:
    """Register all evidence + archive handlers on a reconciler.

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
    # RFC §3.5.6: archive events reuse state_changed / fact_added
    reconciler.register(
        EVT_REFLECTION_STATE_CHANGED,
        make_reflection_archive_handler(reflection_engine),
    )
    reconciler.register(
        EVT_PERSONA_FACT_ADDED,
        make_persona_archive_handler(persona_manager),
    )
