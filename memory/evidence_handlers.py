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
    Reconciler,
)
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")


_EVIDENCE_SNAPSHOT_KEYS = (
    'reinforcement', 'disputation',
    'rein_last_signal_at', 'disp_last_signal_at',
    'sub_zero_days',
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
        data: list[dict] = []
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                data = []
        if not isinstance(data, list):
            data = []
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
        persona: dict = {}
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    persona = json.load(f)
            except (json.JSONDecodeError, OSError):
                persona = {}
        if not isinstance(persona, dict):
            persona = {}
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
        try:
            with open(path, encoding='utf-8') as f:
                persona = json.load(f)
        except (json.JSONDecodeError, OSError):
            return False
        if not isinstance(persona, dict):
            return False
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
    """Register all three evidence handlers on a reconciler.

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
