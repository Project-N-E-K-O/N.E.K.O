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
    (`absorbed_into`, `promote_blocked_reason`, `denied_reason`,
    `reject_reason`, `<status>_at` timestamp). RFC §3.9.6 — without a
    handler, a crash after the event log append but before the view
    save would leak a "phantom unflipped" reflection on next boot.
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
            # Mirror of `_arecord_state_change._sync_mutate` — route the
            # audit `reason` to a status-specific field so denied / blocked
            # semantics don't bleed across. Without the `denied` branch
            # here, a crash-replay would silently drop the denied_reason
            # that the live writer recorded.
            if 'reason' in payload and to_status == 'promote_blocked':
                if r.get('promote_blocked_reason') != payload['reason']:
                    r['promote_blocked_reason'] = payload['reason']
                    changed = True
            if 'reason' in payload and to_status == 'denied':
                if r.get('denied_reason') != payload['reason']:
                    r['denied_reason'] = payload['reason']
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


def make_reflection_archive_handler(reflection_engine):
    """Build the `reflection.state_changed` apply handler.

    PR-2: only the archive transition (to='archived') is wired.
    Non-archive state_changed events remain view-writer-driven
    (confirm_promotion / reject_promotion write the view directly and
    currently do not emit state_changed events).

    Self-healing semantics (coderabbit PR #934 round-2 Major #3):
      The archive write path is `record_and_save` (event + active-view
      removal) FOLLOWED BY shard append. If the process dies between
      those two steps, the entry is gone from the active view but the
      shard never received it — pure data loss without a self-heal.
      So on every replay, this handler:
        1. Reads `archive_shard_path` + `entry_snapshot` from payload.
        2. Ensures the named shard contains the snapshot (idempotent;
           dedup by entry id).
        3. THEN removes the entry from the active view if still there.
      Both steps are individually idempotent → N replays leave the
      same state as one application. Older events lacking
      `entry_snapshot` (round-1 schema) skip step 2 and behave as the
      original "view-only" handler.
    """

    def _apply(name: str, payload: dict) -> bool:
        rid = payload.get('reflection_id')
        to_status = payload.get('to')
        if not rid or to_status != 'archived':
            # Forward-compat: unknown state transitions are a no-op in
            # this handler; PR-3 may add handlers for promoted/denied.
            return False

        # Step 1+2: shard self-heal (write entry into the named shard
        # if missing). Skipped if the event lacks the snapshot fields
        # — older logs from round-1 don't carry them.
        #
        # Coderabbit PR #934 round-3 Major: if the named shard exists
        # but is corrupt, ``ensure_entry_in_named_shard_sync`` raises
        # ``ShardCorruptError`` instead of silently returning False.
        # We MUST NOT proceed to step 3 in that case — removing the
        # entry from the active view when we couldn't verify the shard
        # would lose the only remaining copy. Bail with view intact;
        # the next reconciler boot will retry once the operator has
        # repaired (or removed) the corrupt shard.
        from memory.archive_shards import (
            ShardCorruptError,
            ensure_entry_in_named_shard_sync,
        )
        shard_basename = payload.get('archive_shard_path')
        snapshot = payload.get('entry_snapshot')
        shard_changed = False
        if shard_basename and isinstance(snapshot, dict):
            archive_dir = reflection_engine._reflections_archive_dir(name)
            try:
                shard_changed = ensure_entry_in_named_shard_sync(
                    archive_dir, shard_basename, snapshot,
                )
            except ShardCorruptError as e:
                logger.warning(
                    f"[ArchiveHandler] {name}: 目标分片损坏，保留 active view "
                    f"中的 reflection_id={rid} 待人工修复后重放: {e}"
                )
                return False

        # Step 3: remove from the active view (the original handler
        # behavior — still idempotent).
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
        view_changed = len(data) != before
        if view_changed:
            atomic_write_json(path, data, indent=2, ensure_ascii=False)
        return view_changed or shard_changed

    return _apply


def make_persona_archive_handler(persona_manager):
    """Build the `persona.fact_added` apply handler for archive events.

    RFC §3.5.6: persona archive 复用 fact_added 事件，用 payload 里的
    `archive_shard_path` 字段区分主路径的 fact_added（该路径未来可能由
    PR-3 emit，但当前代码未使用）。PR-2 handler 只对带 archive_shard_path
    的 payload 做归档。不带该字段的 payload 当前视为 no-op — 正向
    fact_added 还没走事件路径。

    Self-healing semantics (coderabbit PR #934 round-2 Major #3 — twin
    of `make_reflection_archive_handler`):
      The archive write path is `record_and_save` (event + view
      mutation) FOLLOWED BY shard append. A crash between those two
      steps leaves the entry gone from the persona but never written
      to the shard. So on every replay, this handler:
        1. Reads `archive_shard_path` + `entry_snapshot` from payload.
        2. Ensures the named shard contains the snapshot (idempotent;
           dedup by entry id).
        3. THEN removes the entry from the persona main view if still
           there.
      Both steps are individually idempotent → N replays = 1 apply.
      Older events without `entry_snapshot` (round-1 schema) skip
      step 2 and behave as the original view-only handler.
    """

    def _apply(name: str, payload: dict) -> bool:
        shard_basename = payload.get('archive_shard_path')
        if not shard_basename:
            return False  # Not an archive event → no-op for PR-2
        entity_key = payload.get('entity_key')
        entry_id = payload.get('entry_id')
        if not entity_key or not entry_id:
            return False

        # Step 1+2: shard self-heal. Skipped if event predates
        # entry_snapshot (round-1 logs).
        #
        # Coderabbit PR #934 round-3 Major (twin of reflection handler):
        # corrupt target shard raises ``ShardCorruptError`` — bail with
        # the persona view untouched so the entry isn't lost.
        from memory.archive_shards import (
            ShardCorruptError,
            ensure_entry_in_named_shard_sync,
        )
        snapshot = payload.get('entry_snapshot')
        shard_changed = False
        if isinstance(snapshot, dict):
            archive_dir = persona_manager._persona_archive_dir(name)
            try:
                shard_changed = ensure_entry_in_named_shard_sync(
                    archive_dir, shard_basename, snapshot,
                )
            except ShardCorruptError as e:
                logger.warning(
                    f"[ArchiveHandler] {name}: 目标分片损坏，保留 persona "
                    f"{entity_key}/{entry_id} 待人工修复后重放: {e}"
                )
                return False

        # Step 3: persona main-view removal.
        path = persona_manager._persona_path(name)
        if not os.path.exists(path):
            return shard_changed
        with open(path, encoding='utf-8') as f:
            persona = json.load(f)
        if not isinstance(persona, dict):
            raise RuntimeError(
                f"[ArchiveHandler] {path}: 期望 dict，实际 "
                f"{type(persona).__name__}；view 结构异常，暂停 replay"
            )
        section = persona.get(entity_key)
        if not isinstance(section, dict):
            return shard_changed
        facts = section.get('facts', [])
        before = len(facts)
        section['facts'] = [
            e for e in facts
            if not (isinstance(e, dict) and e.get('id') == entry_id)
        ]
        view_changed = len(section['facts']) != before
        if view_changed:
            persona_manager._personas[name] = persona
            atomic_write_json(path, persona, indent=2, ensure_ascii=False)
        return view_changed or shard_changed

    return _apply


def make_reflection_state_changed_composite(reflection_engine):
    """Composite handler for `EVT_REFLECTION_STATE_CHANGED`.

    The event is emitted by two disjoint paths that happen to share the
    same event type (RFC §3.5.6 deliberately reuses it to keep the event
    vocabulary compact):

      1. **Archive** (PR-2, RFC §3.5): `to='archived'` — remove from
         active view + self-heal the sharded archive file.
      2. **Merge-on-promote** (PR-3, RFC §3.9.6): `to` ∈ {confirmed,
         promoted, merged, denied, promote_blocked} — flip the status
         field + update audit fields on the existing entry.

    `Reconciler.register` replaces rather than chains, so we dispatch
    here rather than registering two handlers. The `to` value is the
    only source of truth needed to pick a branch — both producers must
    set it.
    """
    archive_handler = make_reflection_archive_handler(reflection_engine)
    status_handler = make_reflection_state_changed_handler(reflection_engine)

    def _apply(name: str, payload: dict) -> bool:
        to_status = payload.get('to')
        if to_status == 'archived':
            return archive_handler(name, payload)
        return status_handler(name, payload)

    return _apply


def register_evidence_handlers(
    reconciler: Reconciler,
    persona_manager,
    reflection_engine,
) -> None:
    """Register all evidence + state-change + archive handlers on a reconciler.

    Call once per boot (memory_server startup) and per hot-reload — the
    closures capture the current manager instances so reload-swapped
    instances see their own file paths.
    """
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
    # RFC §3.5.6 + §3.9.6: archive + merge-on-promote both emit
    # EVT_REFLECTION_STATE_CHANGED. Dispatch on `to` inside the composite.
    reconciler.register(
        EVT_REFLECTION_STATE_CHANGED,
        make_reflection_state_changed_composite(reflection_engine),
    )
    # RFC §3.5.6: persona archive reuses EVT_PERSONA_FACT_ADDED (payload
    # carries `archive_shard_path` to differentiate from a regular add).
    reconciler.register(
        EVT_PERSONA_FACT_ADDED,
        make_persona_archive_handler(persona_manager),
    )
