# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Speaker trust event evaluation, persistence, and reconciliation rollback methods."""

from __future__ import annotations

import hashlib
import json
import os
from memory import cold_store
from memory.scopes import MemorySubject, coerce_subject, entry_matches_subject, subject_from_entry

from memory import facts as _facts_facade
from ._shared import (
    _readable_fact_id,
    _speaker_trust_fact_id,
    _speaker_trust_fact_identity,
)

class SpeakerTrustMixin:
    async def aevaluate_speaker_trust_events(
        self,
        name: str,
        messages: list[dict],
        *,
        subject: MemorySubject | dict,
        speaker_provenance: dict | None,
        speaker_is_owner: bool,
        facts_snapshot: list[dict] | None = None,
        replay_facts_snapshot: list[dict] | None = None,
        identity=None,
    ) -> list[dict]:
        """Derive owner confirmation/correction signals from raw request text.

        ``identity`` is an optional ``TrustSnapshot``. With it, the
        self-attestation ban widens from "same account" to "same PERSON", which
        closes off "the owner endorses their own earlier statement from a second
        account". Default ``None`` degrades byte-for-byte to the pre-existing
        string comparison, so every existing caller and test is unaffected.

        The entity layer decides whether an event is PRODUCED. It never decides
        whose ledger the event lands in — that stays keyed by account all the
        way down.
        """
        if not speaker_is_owner or not isinstance(speaker_provenance, dict):
            return []
        from memory.speaker_trust import (
            deterministic_relation,
            observation_texts,
            stable_speaker_id,
            trust_event_id,
            trust_observation_id,
        )
        from memory.temporal import explicit_event_window

        source_id = stable_speaker_id(speaker_provenance.get('speaker_id'))
        memory_subject = coerce_subject(subject)
        texts = observation_texts(messages)
        if source_id is None or memory_subject is None or not texts:
            return []
        facts = (
            facts_snapshot
            if facts_snapshot is not None
            else await self.aload_facts(name)
        )
        replay_facts = (
            replay_facts_snapshot
            if replay_facts_snapshot is not None
            else facts
        )

        def _in_signal_scope(entry: dict) -> bool:
            if memory_subject.kind != 'group_participant':
                return entry_matches_subject(entry, memory_subject)
            candidate_subject = subject_from_entry(entry)
            if (
                candidate_subject is None
                or candidate_subject.kind != 'group_participant'
            ):
                return False
            # A group participant subject is qq:<group>:<speaker>. Owner
            # confirmation in their own participant bucket may evaluate other
            # members of that same group, but never a different group.
            current_prefix = memory_subject.subject_id.rsplit(':', 1)[0]
            candidate_prefix = candidate_subject.subject_id.rsplit(':', 1)[0]
            current_scope_is_default = memory_subject.scope == (
                f"{memory_subject.kind}:{memory_subject.subject_id}"
            )
            candidate_scope_is_default = candidate_subject.scope == (
                f"{candidate_subject.kind}:{candidate_subject.subject_id}"
            )
            return (
                candidate_prefix == current_prefix
                and (
                    candidate_subject.scope == memory_subject.scope
                    or (
                        current_scope_is_default
                        and candidate_scope_is_default
                    )
                )
            )

        events: list[dict] = []
        seen_event_ids: set[str] = set()
        for text in texts:
            observation_id = trust_observation_id(text)
            for prior in replay_facts:
                if not isinstance(prior, dict) or not _in_signal_scope(prior):
                    continue
                # A server-issued signal is persisted before the response is
                # returned.  Re-deliver it after a lost response; the plugin's
                # durable event-id ledger makes this replay idempotent.
                for recorded in prior.get('_speaker_trust_signal_events') or []:
                    if not isinstance(recorded, dict):
                        continue
                    event_id = str(recorded.get('event_id') or '').strip()[:96]
                    prior_identity = _speaker_trust_fact_identity(prior)
                    recorded_identity = (
                        str(recorded.get('source_fact_id') or ''),
                        recorded.get('source_subject_kind'),
                        recorded.get('source_subject_id'),
                        recorded.get('source_scope'),
                    )
                    if (
                        event_id
                        and event_id not in seen_event_ids
                        and recorded.get('observation_id') == observation_id
                        and prior_identity is not None
                        and recorded_identity == prior_identity
                        # DELIBERATELY account-level, NOT entity-level. This
                        # is the replay ring: it re-delivers an already-durable
                        # event after a lost response, keyed on the owner's own
                        # account string. Widening it to ``same_entity`` would
                        # let the replay ring re-issue events on behalf of a
                        # DIFFERENT account of the same person. Asymmetry with
                        # the self-attestation ban above is intended: after the
                        # owner switches accounts the ban gets stricter (fewer
                        # events) and replay gets stricter (fewer re-deliveries)
                        # — both directions under-count, i.e. fail closed. Do
                        # not "symmetrize" this.
                        and stable_speaker_id(
                            recorded.get('source_speaker_id')
                        ) == source_id
                        and recorded.get('kind') in {
                            'confirmation', 'correction',
                        }
                        and stable_speaker_id(recorded.get('speaker_id'))
                    ):
                        seen_event_ids.add(event_id)
                        events.append(dict(recorded))
            for prior in facts:
                if not isinstance(prior, dict) or not _in_signal_scope(prior):
                    continue
                if prior.get('speaker_provenance_mixed') is True:
                    continue
                target_id = stable_speaker_id(prior.get('speaker_id'))
                if target_id is None or target_id == source_id:
                    continue
                if identity is not None and identity.same_entity(
                    source_id, target_id,
                ):
                    # Same person, second account: still self-attestation.
                    # ``same_entity`` is conservative (unloaded pool ⇒ False),
                    # so this can only ever suppress events, never invent them.
                    continue
                # Raw owner observations carry no structured event window.
                # Do not reinterpret an explicitly dated historical fact as a
                # present-day confirmation/correction. Persisted events above
                # still replay idempotently after a lost response.
                if any(explicit_event_window(prior)):
                    continue
                relation = deterministic_relation(prior.get('text', ''), text)
                if relation is None:
                    continue
                candidate_subject = subject_from_entry(prior)
                if candidate_subject is None:
                    continue
                raw_source_fact_id = _readable_fact_id(prior)
                source_fact_id = (
                    _speaker_trust_fact_id(raw_source_fact_id)
                    if raw_source_fact_id is not None else None
                )
                fallback_fact_id = hashlib.sha256(
                    ' '.join(str(prior.get('text') or '').split()).casefold().encode(
                        'utf-8'
                    )
                ).hexdigest()[:24]
                signal_identity = json.dumps([
                    name,
                    source_id,
                    candidate_subject.kind,
                    candidate_subject.subject_id,
                    candidate_subject.scope,
                    source_fact_id or fallback_fact_id,
                ], ensure_ascii=False, separators=(',', ':'))
                signal_key = hashlib.sha256(
                    signal_identity.encode('utf-8')
                ).hexdigest()[:24]
                event_id = trust_event_id(
                    relation, signal_key, target_id,
                )
                if event_id in seen_event_ids:
                    continue
                seen_event_ids.add(event_id)
                events.append({
                    'kind': relation,
                    'speaker_id': target_id,
                    'event_id': event_id,
                    'source_speaker_id': source_id,
                    'source_fact_id': source_fact_id,
                    'source_subject_kind': candidate_subject.kind,
                    'source_subject_id': candidate_subject.subject_id,
                    'source_scope': candidate_subject.scope,
                    'observation_id': observation_id,
                })
        return events

    async def apersist_speaker_trust_events(
        self,
        name: str,
        events: list[dict],
        *,
        expected_reconciliations: dict[
            tuple[str, str, str, str], dict
        ] | None = None,
    ) -> list[dict]:
        """Attach issued owner signals and return only durably backed events."""
        from memory.speaker_trust import stable_speaker_id

        def _provenance(entry: dict) -> dict:
            return {
                key: entry[key]
                for key in (
                    'speaker_id', 'speaker_label', 'speaker_trust',
                    'speaker_entity_id', 'speaker_provenance_mixed',
                )
                if key in entry
            }

        valid_by_fact: dict[tuple[str, str, str, str], list[dict]] = {}
        for raw in events or []:
            if not isinstance(raw, dict):
                continue
            fact_id = str(raw.get('source_fact_id') or '').strip()
            event_id = str(raw.get('event_id') or '').strip()[:96]
            speaker_id = stable_speaker_id(raw.get('speaker_id'))
            source_speaker_id = stable_speaker_id(raw.get('source_speaker_id'))
            observation_id = str(raw.get('observation_id') or '').strip()[:96]
            fact_identity = (
                fact_id,
                str(raw.get('source_subject_kind') or ''),
                str(raw.get('source_subject_id') or ''),
                str(raw.get('source_scope') or ''),
            )
            kind = raw.get('kind')
            if (
                not fact_id or not event_id or speaker_id is None
                or source_speaker_id is None
                or not observation_id
                or not all(fact_identity[1:])
                or kind not in {'confirmation', 'correction'}
            ):
                continue
            valid_by_fact.setdefault(fact_identity, []).append({
                'kind': kind,
                'speaker_id': speaker_id,
                'event_id': event_id,
                'source_speaker_id': source_speaker_id,
                'source_fact_id': fact_id,
                'source_subject_kind': fact_identity[1],
                'source_subject_id': fact_identity[2],
                'source_scope': fact_identity[3],
                'observation_id': observation_id,
            })
        if not valid_by_fact:
            return []
        expected_provenance_by_fact = {
            identity: _provenance(snapshot)
            for identity, snapshot in (expected_reconciliations or {}).items()
            if identity in valid_by_fact and isinstance(snapshot, dict)
        }

        async with self._get_persist_alock(name):
            await self.aload_facts(name)

            def _persist() -> list[dict]:
                changed = 0
                durable_events: list[dict] = []
                durable_event_ids: set[str] = set()
                with self._get_lock(name):
                    facts = self._facts.get(name) or []
                    for fact in facts:
                        if not isinstance(fact, dict):
                            continue
                        fact_identity = _speaker_trust_fact_identity(fact)
                        additions = valid_by_fact.get(fact_identity)
                        if not additions:
                            continue
                        matches_expected_reconciliation = (
                            expected_provenance_by_fact.get(fact_identity)
                            == _provenance(fact)
                        )
                        recorded = fact.get('_speaker_trust_signal_events')
                        if not isinstance(recorded, list):
                            recorded = []
                            fact['_speaker_trust_signal_events'] = recorded
                        known = {
                            str(item.get('event_id') or '')
                            for item in recorded if isinstance(item, dict)
                        }
                        for event in additions:
                            is_replay = event['event_id'] in known
                            if (
                                not is_replay
                                and not matches_expected_reconciliation
                                and (
                                    fact.get('speaker_provenance_mixed') is True
                                    or stable_speaker_id(fact.get('speaker_id'))
                                    != event['speaker_id']
                                )
                            ):
                                continue
                            if not is_replay:
                                recorded.append(event)
                                known.add(event['event_id'])
                                changed += 1
                            if event['event_id'] not in durable_event_ids:
                                durable_event_ids.add(event['event_id'])
                                durable_events.append(dict(event))
                    if changed:
                        self.save_facts(name, _fact_lock_held=True)
                    archive_path = self._facts_archive_path(name)
                    archived: list = []
                    if os.path.exists(archive_path):
                        try:
                            with open(archive_path, encoding='utf-8') as fh:
                                archived = json.load(fh)
                        except (
                            json.JSONDecodeError, UnicodeDecodeError, OSError,
                        ) as exc:
                            raise RuntimeError(
                                "facts_archive unreadable during trust persist: "
                                f"{exc}"
                            ) from exc
                        if not isinstance(archived, list):
                            raise RuntimeError(
                                "facts_archive is not a list during trust persist"
                            )
                    def _attach(rows: list) -> bool:
                        rows_changed = False
                        for fact in rows:
                            if not isinstance(fact, dict):
                                continue
                            fact_identity = _speaker_trust_fact_identity(fact)
                            additions = valid_by_fact.get(fact_identity)
                            if not additions:
                                continue
                            matches_expected_reconciliation = (
                                expected_provenance_by_fact.get(fact_identity)
                                == _provenance(fact)
                            )
                            recorded = fact.get('_speaker_trust_signal_events')
                            if not isinstance(recorded, list):
                                recorded = []
                                fact['_speaker_trust_signal_events'] = recorded
                            known = {
                                str(item.get('event_id') or '')
                                for item in recorded
                                if isinstance(item, dict)
                            }
                            for event in additions:
                                is_replay = event['event_id'] in known
                                if (
                                    not is_replay
                                    and not matches_expected_reconciliation
                                    and (
                                        fact.get('speaker_provenance_mixed') is True
                                        or stable_speaker_id(fact.get('speaker_id'))
                                        != event['speaker_id']
                                    )
                                ):
                                    continue
                                if not is_replay:
                                    recorded.append(event)
                                    known.add(event['event_id'])
                                    rows_changed = True
                                if event['event_id'] not in durable_event_ids:
                                    durable_event_ids.add(event['event_id'])
                                    durable_events.append(dict(event))
                        return rows_changed

                    if _attach(archived):
                        _facts_facade.assert_cloudsave_writable(
                            self._config_manager,
                            operation="save",
                            target=f"memory/{name}/facts_archive.json",
                        )
                        _facts_facade.atomic_write_json(
                            archive_path, archived,
                            indent=2, ensure_ascii=False,
                        )

                    def _attach_cold(row: dict) -> dict:
                        copy = json.loads(json.dumps(row, ensure_ascii=False))
                        return copy if _attach([copy]) else row

                    try:
                        cold_store.rewrite_rows(self._character_dir(name), _attach_cold)
                    except cold_store.ColdStoreUnreadable as exc:
                        raise RuntimeError(
                            f"cold facts unreadable during trust persist: {exc}"
                        ) from exc
                return durable_events

            persist_task = _facts_facade.asyncio.create_task(_facts_facade.asyncio.to_thread(_persist))
            try:
                return await _facts_facade.asyncio.shield(persist_task)
            except _facts_facade.asyncio.CancelledError as cancellation:
                current_task = _facts_facade.asyncio.current_task()
                if current_task is not None:
                    while current_task.cancelling():
                        current_task.uncancel()
                try:
                    while not persist_task.done():
                        try:
                            await _facts_facade.asyncio.shield(persist_task)
                        except _facts_facade.asyncio.CancelledError:
                            if current_task is not None:
                                while current_task.cancelling():
                                    current_task.uncancel()
                    _ = persist_task.result()
                except BaseException:
                    with self._get_lock(name):
                        self._facts.pop(name, None)
                    raise
                raise cancellation
            except BaseException:
                # _persist mutates cached rows before the atomic file write.
                # If that write fails, retaining those event objects would
                # make the retry treat them as durable replay entries and
                # skip the write that actually failed. Reload from disk on
                # the next attempt instead.
                with self._get_lock(name):
                    self._facts.pop(name, None)
                raise

    async def arollback_speaker_trust_reconciliations(
        self,
        name: str,
        *,
        expected_reconciliations: dict[
            tuple[str, str, str, str], dict
        ],
        previous_facts: dict[tuple[str, str, str, str], dict],
    ) -> bool:
        """Restore authored provenance after trust-event persistence fails.

        Exact dedup can reconcile a source row to mixed provenance before the
        separately persisted trust event is attached.  A transient failure in
        that second write must not leave the retry unable to reconstruct the
        original speaker.  Restore only rows whose current provenance still
        equals this request's reconciliation snapshot; concurrent provenance
        changes are deliberately left untouched.
        """
        provenance_keys = (
            'speaker_id', 'speaker_label', 'speaker_trust',
            'speaker_entity_id', 'speaker_provenance_mixed',
        )

        def _provenance(entry: dict) -> dict:
            return {
                key: entry[key] for key in provenance_keys if key in entry
            }

        expected = {
            identity: _provenance(snapshot)
            for identity, snapshot in (expected_reconciliations or {}).items()
            if isinstance(snapshot, dict)
        }
        previous = {
            identity: _provenance(snapshot)
            for identity, snapshot in (previous_facts or {}).items()
            if identity in expected and isinstance(snapshot, dict)
        }
        if not previous:
            return False

        async with self._get_persist_alock(name):
            await self.aload_facts(name)

            def _rollback() -> bool:
                changed = False

                def _restore(rows: list) -> bool:
                    view_changed = False
                    for fact in rows:
                        if not isinstance(fact, dict):
                            continue
                        identity = _speaker_trust_fact_identity(fact)
                        if (
                            identity not in previous
                            or _provenance(fact) != expected.get(identity)
                        ):
                            continue
                        for key in provenance_keys:
                            fact.pop(key, None)
                        fact.update(previous[identity])
                        view_changed = True
                    return view_changed

                with self._get_lock(name):
                    facts = self._facts.get(name) or []
                    if _restore(facts):
                        self.save_facts(name, _fact_lock_held=True)
                        changed = True

                    archive_path = self._facts_archive_path(name)
                    archived: list = []
                    if os.path.exists(archive_path):
                        with open(archive_path, encoding='utf-8') as fh:
                            archived = json.load(fh)
                        if not isinstance(archived, list):
                            raise RuntimeError(
                                'facts_archive is not a list during trust rollback'
                            )
                    if _restore(archived):
                        _facts_facade.assert_cloudsave_writable(
                            self._config_manager,
                            operation='save',
                            target=f'memory/{name}/facts_archive.json',
                        )
                        _facts_facade.atomic_write_json(
                            archive_path, archived,
                            indent=2, ensure_ascii=False,
                        )
                        changed = True

                    def _restore_cold(row: dict) -> dict:
                        copy = dict(row)
                        return copy if _restore([copy]) else row

                    try:
                        if cold_store.rewrite_rows(self._character_dir(name), _restore_cold):
                            changed = True
                    except cold_store.ColdStoreUnreadable as exc:
                        raise RuntimeError(
                            f"cold facts unreadable during trust rollback: {exc}"
                        ) from exc
                return changed

            rollback_task = _facts_facade.asyncio.create_task(_facts_facade.asyncio.to_thread(_rollback))
            try:
                return await _facts_facade.asyncio.shield(rollback_task)
            except _facts_facade.asyncio.CancelledError as cancellation:
                # ``to_thread`` keeps running after the caller is cancelled;
                # keep shielding through any later cancellation (for example,
                # request timeout followed by shutdown) until its mutation and
                # disk writes have actually finished under the async lock.
                current_task = _facts_facade.asyncio.current_task()
                if current_task is not None:
                    while current_task.cancelling():
                        current_task.uncancel()
                while not rollback_task.done():
                    try:
                        await _facts_facade.asyncio.shield(rollback_task)
                    except _facts_facade.asyncio.CancelledError:
                        if current_task is not None:
                            while current_task.cancelling():
                                current_task.uncancel()
                        continue
                _ = rollback_task.result()
                raise cancellation
