"""Application-owned recommendation workers; never schedules proactive speech."""
from __future__ import annotations

import asyncio
import hashlib
import math
import time
from collections import deque
from dataclasses import dataclass, field
from copy import deepcopy
from uuid import uuid4

from config.topic_recommendation_settings import TopicRecommendationSettings, get_topic_recommendation_settings
from main_logic.topic.recommendation.analysis import RecommendationAnalyzer
from main_logic.topic.recommendation.contracts import (
    AnalysisResult, RecommendationError, RecommendationSnapshot, TurnEvidence, validate_state,
)


def _validate_profile(state: dict) -> dict:
    """Validate persisted domain records before any of their fields are consumed."""
    validate_state(state)
    def text(record, key, *, optional=False):
        value = record.get(key, "" if optional else None)
        if not isinstance(value, str) or len(value) > 1600 or (not optional and not value):
            raise RecommendationError("state_corrupt")
    def timestamp(record, key):
        value = record.get(key)
        if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
            raise RecommendationError("state_corrupt")
    for collection, key in (("subjects", "subject_id"), ("interests", "subject_id"),
                            ("restrictions", "restriction_id"), ("deliveries", "delivery_id")):
        identifiers = [record.get(key) for record in state[collection]]
        if any(not isinstance(value, str) for value in identifiers) or len(set(identifiers)) != len(identifiers):
            raise RecommendationError("state_corrupt")
    for subject in state["subjects"]:
        for key in ("subject_id", "summary", "angle"):
            text(subject, key)
        if subject.get("basis") not in {"explicit", "inferred"} or subject.get("status") not in {"active", "completed", "withdrawn"}:
            raise RecommendationError("state_corrupt")
        for key in ("last_evidence_at", "expires_at"):
            timestamp(subject, key)
        if type(subject.get("version")) is not int or subject["version"] < 1:
            raise RecommendationError("state_corrupt")
        if "context_confirmed" in subject and type(subject["context_confirmed"]) is not bool:
            raise RecommendationError("state_corrupt")
        refs = subject.get("evidence_refs")
        conversations = subject.get("conversation_ids")
        if not isinstance(refs, list) or not refs or len(refs) > 64 or any(not isinstance(r, str) or not r or len(r) > 1024 for r in refs):
            raise RecommendationError("state_corrupt")
        if not isinstance(conversations, list) or not conversations or len(conversations) > 16 or any(not isinstance(c, str) or not c or len(c) > 256 for c in conversations):
            raise RecommendationError("state_corrupt")
    for interest in state["interests"]:
        for key in ("subject_id", "summary"):
            text(interest, key)
        if interest.get("basis") not in {"explicit", "inferred"} or type(interest.get("independent_conversations")) is not int or not 1 <= interest["independent_conversations"] <= 16:
            raise RecommendationError("state_corrupt")
        timestamp(interest, "expires_at")
        timestamp(interest, "updated_at")
    for restriction in state["restrictions"]:
        for key in ("restriction_id", "subject_id", "summary"):
            text(restriction, key)
        text(restriction, "angle", optional=True)
        if restriction.get("scope") not in {"subject", "angle"}:
            raise RecommendationError("state_corrupt")
        if restriction["scope"] == "angle" and not restriction.get("angle", "").strip():
            raise RecommendationError("state_corrupt")
        for key in ("updated_at", "evidence_at", "expires_at"):
            if key in restriction:
                timestamp(restriction, key)
    for delivery in state["deliveries"]:
        for key in ("delivery_id", "subject_id", "session_id", "text"):
            text(delivery, key)
        if delivery.get("publication_status") != "server_committed" or delivery.get("assessment") not in {"engaged", "disengaged", "unknown"}:
            raise RecommendationError("state_corrupt")
        timestamp(delivery, "published_at")
    return state


@dataclass
class _Character:
    character_id: str
    name: str
    state: dict | None = None
    session_id: str = ""
    binding_generation: str = ""
    subjects_scope: tuple[dict, ...] | None = None
    private_memory_allowed: bool = False
    events: deque = field(default_factory=deque)
    seen: deque = field(default_factory=lambda: deque(maxlen=256))
    captures: dict = field(default_factory=dict)
    watermark: int = 0
    publication_watermark: int = 0
    analyzed_watermark: int = 0
    evidence_gap: bool = False
    gap_watermark: int = 0
    last_error: str | None = None
    deleted: bool = False
    deleted_cleanup_complete: bool = False
    resetting: bool = False
    reset_operations: int = 0
    reset_cutoffs: dict = field(default_factory=dict)
    analysis_commit: tuple[dict, frozenset[str], int] | None = None
    task: asyncio.Task | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    first_pending: float = 0.0
    last_pending: float = 0.0


class TopicRecommendationService:
    def __init__(self, store, analyzer=None, settings=None, memory_reader=None, *, controls_refresher=None) -> None:
        self.store = store
        self.settings = settings or get_topic_recommendation_settings()
        self.analyzer = analyzer or RecommendationAnalyzer(self.settings)
        self.memory_reader = memory_reader
        self.controls_refresher = controls_refresher
        self._characters: dict[str, _Character] = {}
        self._master = False
        self._beta = False
        self._controls_valid = False
        self._config_revision = -1
        self._enable_generation = 0
        self._closing = False
        self._maintenance = False
        self._semaphore = asyncio.Semaphore(self.settings.global_concurrency)

    async def start(self, characters: dict[str, str]) -> None:
        await self.sync_characters(characters)

    async def sync_characters(self, characters: dict[str, str]) -> None:
        if self._closing:
            raise RecommendationError("closing")
        for character_id in tuple(self._characters):
            if character_id not in characters:
                await self.delete_character(character_id)
        for character_id, name in characters.items():
            existing = self._characters.get(character_id)
            if existing and not existing.deleted:
                if existing.name != str(name):
                    existing.name = str(name)
                    existing.binding_generation = uuid4().hex
                    existing.changed.set()
                if existing.state is None:
                    try:
                        existing.state = _validate_profile(await self.store.load(character_id))
                        existing.last_error = None
                    except Exception as exc:
                        existing.last_error = self._error_code(exc)
                continue
            if existing and existing.deleted:
                raise RecommendationError("deleted_character")
            slot = _Character(character_id, str(name))
            self._characters[character_id] = slot
            try:
                slot.state = _validate_profile(await self.store.load(character_id))
                for subject in slot.state["subjects"]:
                    slot.seen.extend(("user", turn_id) for turn_id in subject.get("evidence_turn_ids", []))
            except Exception as exc:
                slot.last_error = self._error_code(exc)

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, RecommendationError):
            return exc.code
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            return "analysis_timeout"
        return "analysis_failed"

    def _enabled(self) -> bool:
        return self.settings.enabled and self._controls_valid and self._master and self._beta and not self._closing and not self._maintenance

    @property
    def reset_generation(self) -> str:
        """Fence confirmations across owner, maintenance and control changes."""
        return f"{self.store.root_generation}:{self._enable_generation}"

    def pause_controls(self) -> None:
        self._controls_valid = False
        self._enable_generation += 1
        for slot in self._characters.values():
            slot.changed.set()

    def controls_match_payload(self, data: dict, full_snapshot: bool = False) -> bool:
        if not self._controls_valid or not isinstance(data, dict):
            return False
        for key, expected in (("proactiveChatEnabled", self._master), ("proactiveTopicRecommendationEnabled", self._beta)):
            if key in data or full_snapshot:
                value = data.get(key, False)
                if type(value) is not bool or value != expected:
                    return False
        return True

    async def apply_controls(self, master_enabled: bool, beta_enabled: bool, revision: int, valid: bool = True) -> None:
        if type(revision) is not int or revision < self._config_revision:
            return
        if type(master_enabled) is not bool or type(beta_enabled) is not bool:
            valid = False
        changed = (self._master, self._beta, self._controls_valid) != (master_enabled, beta_enabled, valid)
        if changed:
            self._enable_generation += 1
        self._master, self._beta, self._controls_valid = master_enabled, beta_enabled, valid
        self._config_revision = revision
        for slot in self._characters.values():
            slot.changed.set()
            if slot.captures or (self._enabled() and slot.events):
                self._wake(slot)

    def set_maintenance(self, active: bool) -> None:
        self._maintenance = bool(active)
        self._enable_generation += 1
        for slot in self._characters.values():
            slot.changed.set()
            if not active and (slot.captures or (self._enabled() and slot.events)):
                self._wake(slot)

    async def recover_after_maintenance(self) -> None:
        """Reload only unavailable slots and finish previously retired deletions.

        Called after the physical root fence was checked, while outputs are
        still paused. It never creates a second writer or fabricates an empty
        profile in place of a failed read.
        """
        if self._closing:
            return
        if self.controls_refresher is not None:
            try:
                await asyncio.wait_for(self.controls_refresher(), self.settings.store_timeout)
            except Exception:
                self.pause_controls()
                return
        for slot in tuple(self._characters.values()):
            async with slot.lock:
                if self._closing or self._characters.get(slot.character_id) is not slot:
                    return
                try:
                    if slot.deleted and not slot.deleted_cleanup_complete:
                        await asyncio.wait_for(self.store.delete(slot.character_id), self.settings.store_timeout)
                        slot.deleted_cleanup_complete = True
                        slot.analysis_commit = None
                    elif slot.state is None:
                        state = _validate_profile(await asyncio.wait_for(self.store.load(slot.character_id), self.settings.store_timeout))
                        if not self._closing and not slot.deleted:
                            slot.state = state
                            slot.last_error = None
                except Exception as exc:
                    slot.last_error = self._error_code(exc)

    def bind(self, character_id: str, session_id: str, display_name: str | None = None, subjects=None,
             allow_private_memory: bool = False):
        from main_logic.topic.recommendation.adapters import RecommendationTurnSink
        slot = self._characters.get(character_id)
        if slot is None or slot.deleted or not isinstance(session_id, str) or not session_id or len(session_id) > 256 or self._closing:
            raise RecommendationError("invalid_character_id")
        if display_name is not None and display_name != slot.name:
            raise RecommendationError("character_identity_unconfirmed")
        if subjects is not None and (not isinstance(subjects, (list, tuple)) or not 1 <= len(subjects) <= 8 or any(not isinstance(s, dict) for s in subjects)):
            raise RecommendationError("invalid_memory_scope")
        if slot.session_id != session_id:
            slot.binding_generation = uuid4().hex
            slot.session_id = session_id
            # Retain accepted evidence for the service-owned worker's next batch.
            # It is still attributed to its original session/turn, while the old
            # sink and old in-flight operation lose their binding authority.
        if subjects is not None:
            slot.subjects_scope = tuple(deepcopy(subjects))
        else:
            slot.subjects_scope = None
        slot.private_memory_allowed = allow_private_memory is True and subjects is None
        if self._enabled() and (slot.events or slot.captures):
            self._wake(slot)
        return RecommendationTurnSink(self, character_id, session_id, slot.binding_generation)

    def binding_is_current(self, character_id: str, session_id: str, binding_generation: str,
                           display_name: str | None = None) -> bool:
        slot = self._characters.get(character_id)
        return bool(slot is not None and not slot.deleted and not self._closing
                    and (slot.session_id, slot.binding_generation) == (session_id, binding_generation)
                    and (display_name is None or display_name == slot.name))

    def note_turn(self, character_id: str, session_id: str, binding_generation: str, event) -> None:
        slot = self._characters.get(character_id)
        if not self._enabled() or slot is None or slot.deleted or slot.state is None:
            return
        if (slot.session_id, slot.binding_generation) != (session_id, binding_generation):
            return
        # This metadata is supplied at the real input chokepoint, never guessed from text.
        if getattr(event, "input_mode", None) != "text" or getattr(event, "session_id", None) != session_id or getattr(event, "synthetic", False):
            return
        turn_id = getattr(event, "turn_id", None)
        text = event.raw_text
        if not isinstance(turn_id, str) or not turn_id or len(turn_id) > 256 or event.actor not in {"user", "ai"} or not isinstance(text, str) or not text.strip():
            return
        key = (event.actor, turn_id)
        if key in slot.seen:
            return
        slot.seen.append(key)
        if event.actor == "user":
            slot.watermark += 1  # Before any asynchronous analysis: withdraw old snapshots.
        if len(slot.events) >= self.settings.max_events or len(text) > self.settings.event_tokens * 8:
            slot.evidence_gap = True
            slot.gap_watermark = slot.watermark
            slot.last_error = "evidence_gap"
            return
        now = time.time()
        captured = getattr(event, "timestamp", now)
        if type(captured) not in {int, float} or not math.isfinite(captured) or captured <= 0 or captured > now + 60:
            captured = now
        ref = "turn:" + session_id + ":" + event.actor + ":" + turn_id
        slot.events.append(TurnEvidence(ref, turn_id, session_id, event.actor, text, event.lang,
                                        float(captured), slot.watermark, binding_generation))
        stamp = time.monotonic()
        if not slot.first_pending:
            slot.first_pending = stamp
        slot.last_pending = stamp
        self._wake(slot)

    def _wake(self, slot: _Character) -> None:
        slot.changed.set()
        if slot.resetting:
            return
        if slot.task is None or slot.task.done():
            slot.task = asyncio.create_task(self._worker(slot), name="topic-recommendation-worker")

    def unbind(self, character_id: str, session_id: str) -> None:
        slot = self._characters.get(character_id)
        if slot is None or slot.session_id != session_id:
            return
        slot.session_id = ""
        slot.binding_generation = uuid4().hex
        slot.changed.set()

    def _operation_current(self, slot: _Character, generation: int, epoch: str, binding: str, *, enabled: bool = True) -> bool:
        return (not self._closing and not self._maintenance and not slot.deleted and not slot.resetting and bool(slot.session_id) and slot.state is not None
                and self._characters.get(slot.character_id) is slot and slot.state["state_epoch"] == epoch
                and slot.binding_generation == binding and (not enabled or self._enabled())
                and (not enabled or self._enable_generation == generation))

    async def _worker(self, slot: _Character) -> None:
        retries = 0
        try:
            while not self._closing and not self._maintenance and not slot.deleted and not slot.resetting and (slot.captures or (self._enabled() and slot.session_id and any(e.actor == "user" for e in slot.events))):
                delay = 0.0 if slot.captures else max(0.0, min(
                    slot.last_pending + self.settings.debounce_seconds,
                    slot.first_pending + self.settings.max_batch_wait_seconds) - time.monotonic())
                slot.changed.clear()
                if delay:
                    try:
                        await asyncio.wait_for(slot.changed.wait(), delay)
                        continue
                    except asyncio.TimeoutError:
                        pass
                try:
                    await self.process_pending(slot.character_id)
                    retries = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    slot.last_error = self._error_code(exc)
                    if slot.last_error == "stale_operation":
                        continue
                    if retries >= len(self.settings.retry_delays):
                        break
                    delay = self.settings.retry_delays[retries]
                    retries += 1
                    slot.changed.clear()
                    try:
                        await asyncio.wait_for(slot.changed.wait(), delay)
                    except asyncio.TimeoutError:
                        pass
        finally:
            slot.task = None

    async def process_pending(self, character_id: str) -> None:
        """One bounded batch, also usable by deterministic integration tests."""
        slot = self._characters[character_id]
        async with slot.lock:
            if self._closing or self._maintenance or slot.deleted or slot.resetting or slot.state is None:
                return
            generation, epoch, binding = self._enable_generation, slot.state["state_epoch"], slot.binding_generation
            guard = lambda: self._operation_current(slot, generation, epoch, binding)
            await self._flush_captures_locked(slot)
            if not self._enabled() or not slot.session_id:
                return
            # Atomic replacement may have completed before a cancelled/expired
            # waiter updated memory. Rebase the next finite batch on actual disk
            # state; original turn identities prevent counting that evidence twice.
            current = _validate_profile(await asyncio.wait_for(
                self.store.load(character_id), self.settings.store_timeout))
            if not guard():
                raise RecommendationError("stale_operation")
            self._adopt_durable_state(slot, current)
            if current["state_epoch"] != epoch:
                # Accepted reset receipts have retired their original evidence;
                # restart this batch with the new epoch rather than the old guard.
                raise RecommendationError("stale_operation")
            # Keep total buffering <=24, and analyze finite cutoffs instead of
            # repeatedly clipping the most recent conversation during fast input.
            events = tuple(list(slot.events)[:4])
            if not events:
                return
            if slot.evidence_gap:
                raise RecommendationError("evidence_gap")
            if not any(e.actor == "user" for e in events):
                # AI replies alone provide context, but cannot form interests.
                if len(slot.events) > len(events):
                    consumed = {event.ref for event in events}
                    slot.events = deque(event for event in slot.events if event.ref not in consumed)
                return
            memories = ()
            await asyncio.wait_for(self._semaphore.acquire(), self.settings.worker_wait_seconds)
            try:
                async with asyncio.timeout(self.settings.batch_timeout):
                    if not guard():
                        raise RecommendationError("stale_operation")
                    if self.memory_reader is not None and not slot.state["subjects"] and (slot.subjects_scope is not None or slot.private_memory_allowed):
                        memories = await asyncio.wait_for(self.memory_reader.read(
                            display_name=slot.name, subjects=slot.subjects_scope,
                            allow_private=slot.private_memory_allowed,
                            query=events[-1].text, language=events[-1].language), self.settings.candidate_timeout)
                        if not guard():
                            raise RecommendationError("stale_operation")
                    async def commit_feedback(feedback):
                        if not guard():
                            raise RecommendationError("stale_operation")
                        state = self._merge(slot.state, AnalysisResult((), feedback), events)
                        if state != slot.state:
                            # Feedback refs are durable idempotency evidence.
                            # This stage neither consumes candidate input nor
                            # advances the fully analyzed context watermark.
                            await self._commit(slot, state, guard)
                        if not guard():
                            raise RecommendationError("stale_operation")
                        return deepcopy(slot.state)
                    result = await self.analyzer.analyze(events, deepcopy(slot.state), memories=memories,
                                                         on_feedback=commit_feedback)
            finally:
                self._semaphore.release()
            if not guard():
                raise RecommendationError("stale_operation")
            state = self._merge(slot.state, result, events)
            # Keep one bounded intent until actual disk state proves its outcome.
            # The model may return a different subject or no subject on a retry;
            # subject-level evidence dedup cannot acknowledge a committed batch.
            slot.analysis_commit = (state, frozenset(event.ref for event in events),
                                    max(e.watermark for e in events if e.actor == "user"))
            await self._commit(slot, state, guard)
            self._retire_analysis_commit(slot)

    async def _flush_captures_locked(self, slot: _Character, *, allow_closing: bool = False, allow_maintenance: bool = False) -> None:
        captures = tuple(slot.captures.items())
        if not captures or slot.deleted:
            return
        # A cancelled physical commit may have completed before its async waiter
        # saw cancellation. Read actual durable revision before retrying receipts.
        current = _validate_profile(await asyncio.wait_for(self.store.load(slot.character_id), self.settings.store_timeout))
        if slot.deleted or self._characters.get(slot.character_id) is not slot:
            raise RecommendationError("stale_operation")
        self._adopt_durable_state(slot, current)
        epoch = current["state_epoch"]
        def guard():
            return (not slot.deleted and self._characters.get(slot.character_id) is slot
                    and slot.state is not None and slot.state["state_epoch"] == epoch
                    and (not self._closing or allow_closing) and (not self._maintenance or allow_maintenance))
        if not guard():
            raise RecommendationError("stale_operation")
        state = deepcopy(current)
        existing = {d["delivery_id"] for d in state["deliveries"]}
        accepted = [(identifier, delivery) for identifier, delivery in captures
                    if delivery["state_epoch"] == epoch and delivery["root_generation"] == self.store.root_generation]
        for identifier, delivery in accepted:
            if identifier not in existing:
                state["deliveries"].append(deepcopy(delivery))
        if len(state["deliveries"]) != len(current["deliveries"]):
            state["deliveries"] = state["deliveries"][-self.settings.max_deliveries:]
            await self._commit(slot, state, guard)
        for identifier, delivery in captures:
            if slot.captures.get(identifier) is delivery:
                del slot.captures[identifier]

    async def flush_publications(self, *, allow_closing: bool = False, allow_maintenance: bool = False) -> None:
        async with asyncio.timeout(self.settings.close_timeout):
            for slot in tuple(self._characters.values()):
                async with slot.lock:
                    await self._flush_captures_locked(slot, allow_closing=allow_closing, allow_maintenance=allow_maintenance)

    async def _commit(self, slot: _Character, state: dict, guard) -> None:
        validate_state(state, self.settings)
        old = slot.state
        state["revision"] = old["revision"] + 1
        committed = await asyncio.wait_for(self.store.commit(slot.character_id, state, expected_epoch=old["state_epoch"],
                                            expected_revision=old["revision"], guard=guard), self.settings.store_timeout)
        # store guard protects the physical replacement; retain the actual committed
        # state even if a control pause arrived after its atomic replacement.
        if not slot.deleted and self._characters.get(slot.character_id) is slot and slot.state["state_epoch"] == old["state_epoch"]:
            slot.state = committed

    @staticmethod
    def _retire_analysis_commit(slot: _Character) -> None:
        _, completed_refs, cutoff = slot.analysis_commit
        slot.events = deque(event for event in slot.events if event.ref not in completed_refs)
        slot.analyzed_watermark = max(slot.analyzed_watermark, cutoff)
        slot.first_pending = time.monotonic() if slot.events else 0.0
        slot.last_error = "evidence_gap" if slot.evidence_gap else None
        slot.analysis_commit = None

    def _adopt_durable_state(self, slot: _Character, current: dict) -> None:
        """Reconcile uncertain writes through actual receipts, before any replay."""
        old = slot.state
        if old is not None and current["state_epoch"] != old["state_epoch"]:
            accepted = [(receipt, (receipt.get("request_id"), receipt.get("expected_epoch")))
                        for receipt in current["reset_requests"]
                        if (receipt.get("request_id"), receipt.get("expected_epoch")) in slot.reset_cutoffs]
            if not accepted:
                raise RecommendationError("epoch_conflict")
            cutoff = max(slot.reset_cutoffs[key] for _, key in accepted)
            slot.events = deque(event for event in slot.events if event.watermark > cutoff)
            slot.captures = {key: value for key, value in slot.captures.items()
                             if value["state_epoch"] == current["state_epoch"]}
            slot.analyzed_watermark = max(slot.analyzed_watermark, cutoff)
            slot.evidence_gap = slot.gap_watermark > cutoff
            slot.last_error = "evidence_gap" if slot.evidence_gap else None
            for _, key in accepted:
                slot.reset_cutoffs.pop(key, None)
            slot.analysis_commit = None
        elif old is not None and current["revision"] < old["revision"]:
            raise RecommendationError("revision_conflict")
        elif slot.analysis_commit is not None:
            proposal = slot.analysis_commit[0]
            if current == proposal:
                # Equality includes epoch, revision and every persisted field;
                # a newer revision alone does not prove this intent committed.
                self._retire_analysis_commit(slot)
            elif current["revision"] < proposal["revision"]:
                # load runs after the physical writer under the store lock.
                # This intent did not replace the file; its evidence stays queued.
                slot.analysis_commit = None
            else:
                raise RecommendationError("revision_conflict")
        slot.state = current

    def _merge(self, old: dict, result: AnalysisResult, events: tuple[TurnEvidence, ...]) -> dict:
        state = deepcopy(old)
        by_ref = {e.ref: e for e in events if e.actor == "user"}
        now = time.time()
        for item in result.subjects:
            refs = item["evidence_refs"]
            if not refs or any(ref not in by_ref for ref in refs):
                raise RecommendationError("invalid_evidence")
            prior = next((s for s in state["subjects"] if s["subject_id"] == item["subject_id"]), None)
            if prior is None:
                if len(state["subjects"]) >= self.settings.max_subjects:
                    expired = [s for s in state["subjects"] if s.get("expires_at", 0) <= now or s.get("status") != "active"]
                    if not expired:
                        raise RecommendationError("capacity_exhausted")
                    state["subjects"].remove(expired[0])
                prior = {"subject_id": uuid4().hex, "version": 0, "evidence_refs": [], "evidence_turn_ids": [], "conversation_ids": []}
                state["subjects"].append(prior)
            fresh = [ref for ref in refs if ref not in prior["evidence_refs"] and by_ref[ref].turn_id not in prior.get("evidence_turn_ids", [])]
            if not fresh:
                continue
            latest = max(by_ref[ref].captured_at for ref in fresh)
            prior.update({k: item[k] for k in ("summary", "angle", "basis", "status")})
            prior["context_confirmed"] = True
            prior["version"] += 1
            prior["evidence_refs"] = list(dict.fromkeys(prior["evidence_refs"] + refs))[-64:]
            prior["evidence_turn_ids"] = list(dict.fromkeys(prior.get("evidence_turn_ids", []) + [by_ref[ref].turn_id for ref in fresh]))[-64:]
            prior["last_evidence_at"] = max(prior.get("last_evidence_at", 0), latest)
            prior["expires_at"] = prior["last_evidence_at"] + self.settings.expiry_seconds
            conversations = list(dict.fromkeys(prior.get("conversation_ids", []) + [by_ref[ref].session_id for ref in fresh]))[-16:]
            prior["conversation_ids"] = conversations
            interest = next((i for i in state["interests"] if i["subject_id"] == prior["subject_id"]), None)
            if interest is None:
                if len(state["interests"]) >= self.settings.max_interests:
                    expired = [i for i in state["interests"] if i.get("expires_at", 0) <= now]
                    if not expired:
                        raise RecommendationError("capacity_exhausted")
                    state["interests"].remove(expired[0])
                interest = {"subject_id": prior["subject_id"]}
                state["interests"].append(interest)
            interest.update({"basis": item["basis"], "summary": item["summary"], "independent_conversations": len(conversations),
                             "updated_at": prior["last_evidence_at"], "expires_at": prior["expires_at"]})
        for correction in result.restriction_revocations:
            identifier = correction["restriction_id"]
            restriction = next((r for r in state["restrictions"] if r["restriction_id"] == identifier), None)
            refs = correction["evidence_refs"]
            if (restriction is None or not refs or any(ref not in by_ref for ref in refs)
                    or any(by_ref[ref].captured_at < restriction.get("evidence_at", restriction.get("updated_at", 0)) for ref in refs)):
                raise RecommendationError("invalid_restriction")
            state["restrictions"].remove(restriction)
        for feedback in result.feedback:
            delivery = next((d for d in state["deliveries"] if d["delivery_id"] == feedback["delivery_id"]), None)
            if delivery is None:
                raise RecommendationError("invalid_delivery")
            if set(feedback["evidence_refs"]) <= set(delivery.get("feedback_refs", [])):
                continue
            if not set(feedback["evidence_refs"]) <= by_ref.keys():
                raise RecommendationError("invalid_evidence")
            delivery.update({"assessment": feedback["assessment"], "reason": feedback["reason"],
                             "feedback_refs": feedback["evidence_refs"], "feedback_revision": delivery.get("feedback_revision", 0) + 1})
            restriction = feedback["restriction"]
            revocations = feedback.get("revoke_restriction_ids", [])
            permitted = {r["restriction_id"] for r in old["restrictions"] if r["subject_id"] == delivery["subject_id"]}
            if revocations:
                if feedback["assessment"] != "engaged" or not feedback["evidence_refs"] or not set(revocations) <= permitted:
                    raise RecommendationError("invalid_restriction")
                if any(by_ref[ref].captured_at < r.get("evidence_at", r.get("updated_at", 0))
                       for r in old["restrictions"] if r["restriction_id"] in revocations
                       for ref in feedback["evidence_refs"]):
                    raise RecommendationError("invalid_restriction")
                state["restrictions"] = [r for r in state["restrictions"] if r["restriction_id"] not in revocations]
            if restriction:
                restriction_id = delivery["delivery_id"] + ":" + restriction["scope"]
                existing = next((r for r in state["restrictions"] if r["restriction_id"] == restriction_id), None)
                if existing is None:
                    if len(state["restrictions"]) >= self.settings.max_restrictions:
                        raise RecommendationError("capacity_exhausted")
                    existing = {"restriction_id": restriction_id, "subject_id": delivery["subject_id"]}
                    state["restrictions"].append(existing)
                evidence_at = max(by_ref[ref].captured_at for ref in feedback["evidence_refs"])
                if evidence_at < existing.get("evidence_at", existing.get("updated_at", 0)):
                    continue
                existing.update({**restriction, "evidence_refs": feedback["evidence_refs"],
                                 "evidence_at": evidence_at, "updated_at": now})
            # Positive engagement never silently erases a prior refusal.
        return state

    def snapshot(self, character_id: str, session_id: str | None = None) -> RecommendationSnapshot | None:
        slot = self._characters.get(character_id)
        if not self._enabled() or slot is None or slot.deleted or slot.resetting or slot.state is None or slot.evidence_gap or slot.last_error or slot.watermark != slot.analyzed_watermark:
            return None
        if not slot.session_id or (session_id is not None and session_id != slot.session_id):
            return None
        state, now = slot.state, time.time()
        refused = {r["subject_id"] for r in state["restrictions"] if r.get("scope") == "subject" and r.get("expires_at", float("inf")) > now}
        recent = {d["subject_id"] for d in [*state["deliveries"], *slot.captures.values()] if now - d.get("published_at", 0) < 86400}
        candidates = [s for s in state["subjects"] if s.get("status") == "active" and s.get("context_confirmed", True) and s.get("expires_at", 0) > now
                      and s["subject_id"] not in refused and s["subject_id"] not in recent]
        candidates.sort(key=lambda s: s.get("last_evidence_at", 0), reverse=True)
        if not candidates:
            return None
        return RecommendationSnapshot(character_id, slot.session_id, slot.binding_generation, self.store.root_generation,
                                      state["state_epoch"], state["revision"], self._enable_generation, slot.watermark,
                                      tuple(deepcopy(candidates[:self.settings.max_candidates])), tuple(deepcopy(state["restrictions"])),
                                      slot.publication_watermark)

    def is_current(self, snapshot: RecommendationSnapshot) -> bool:
        slot = self._characters.get(snapshot.character_id)
        return bool(slot and self._enabled() and not slot.deleted and not slot.resetting and slot.state is not None
                    and not slot.evidence_gap and not slot.last_error and slot.watermark == slot.analyzed_watermark
                    and slot.watermark == snapshot.user_evidence_watermark and slot.session_id == snapshot.session_id
                    and slot.binding_generation == snapshot.binding_generation and self.store.root_generation == snapshot.root_generation
                    and slot.state["state_epoch"] == snapshot.state_epoch and slot.state["revision"] == snapshot.revision
                    and self._enable_generation == snapshot.enable_generation
                    and slot.publication_watermark == snapshot.publication_watermark)

    def capture_publication(self, snapshot: RecommendationSnapshot, subject_id: str, delivery_id: str, text: str,
                            published_at: float | None = None, speech_id: str | None = None) -> bool:
        slot = self._characters.get(snapshot.character_id)
        if slot is not None and slot.state is not None:
            prior = slot.captures.get(delivery_id) or next((d for d in slot.state["deliveries"] if d["delivery_id"] == delivery_id), None)
            if prior is not None:
                return (prior["subject_id"] == subject_id and prior["text"] == text and prior["state_epoch"] == snapshot.state_epoch
                        and prior["root_generation"] == snapshot.root_generation and prior["session_id"] == snapshot.session_id)
        if not self.is_current(snapshot) or subject_id not in {s["subject_id"] for s in snapshot.candidates}:
            return False
        slot = self._characters[snapshot.character_id]
        stamp = published_at if published_at is not None else time.time()
        if (not isinstance(delivery_id, str) or not delivery_id or len(delivery_id) > 256
                or not isinstance(text, str) or not text.strip() or len(text) > 1600
                or type(stamp) not in {int, float} or not math.isfinite(stamp) or stamp <= 0 or stamp > time.time() + 60
                or (speech_id is not None and (not isinstance(speech_id, str) or len(speech_id) > 256))):
            slot.last_error = "capture_invalid"
            return False
        if len(slot.captures) >= self.settings.max_deliveries:
            slot.last_error = "capacity_exhausted"
            return False
        slot.captures[delivery_id] = {"delivery_id": delivery_id, "subject_id": subject_id,
            "publication_status": "server_committed", "published_at": stamp, "text": text,
            "session_id": snapshot.session_id, "speech_id": speech_id, "assessment": "unknown",
            "root_generation": snapshot.root_generation, "state_epoch": snapshot.state_epoch,
            "binding_generation": snapshot.binding_generation,
            "feedback_revision": 0, "feedback_refs": []}
        slot.publication_watermark += 1
        self._wake(slot)
        return True

    async def status(self, character_id: str) -> dict:
        slot = self._characters.get(character_id)
        if slot is None or slot.deleted:
            raise RecommendationError("invalid_character_id")
        if slot.reset_cutoffs and not self._closing and not self._maintenance:
            # A reset can commit physically before its response is invalidated.
            # Confirm its actual receipt without requiring another user turn;
            # never return a cached epoch for the next destructive confirmation.
            generation = self.reset_generation
            async with asyncio.timeout(self.settings.store_timeout):
                async with slot.lock:
                    if slot.reset_cutoffs:
                        current = _validate_profile(await self.store.load(character_id))
                        if (self._characters.get(character_id) is not slot or slot.deleted
                                or self._closing or self._maintenance or self.reset_generation != generation):
                            raise RecommendationError("stale_operation")
                        self._adopt_durable_state(slot, current)
        state = slot.state
        if self._closing:
            availability = "closing"
        elif self._maintenance:
            availability = "maintenance"
        elif not self.settings.enabled:
            availability = "capability_disabled"
        elif not self._controls_valid or slot.last_error or slot.evidence_gap or state is None:
            availability = "degraded"
        elif not self._master or not self._beta:
            availability = "user_disabled"
        elif slot.watermark != slot.analyzed_watermark:
            availability = "waiting_context"
        else:
            availability = "ready"
        return {"character_id": character_id, "availability": availability,
                "reset_confirmation": self.reset_confirmation(character_id),
                "epoch": state["state_epoch"] if state else None, "revision": state["revision"] if state else None,
                "config_revision": self._config_revision, "counts": {key: len(state[key]) if state else None for key in ("subjects", "interests", "deliveries")},
                "last_error": slot.last_error, "capability_enabled": self.settings.enabled,
                "controls_enabled": self._controls_valid and self._master and self._beta}

    def reset_confirmation(self, character_id: str) -> str | None:
        slot = self._characters.get(character_id)
        if slot is None or slot.deleted or slot.state is None:
            return None
        boundary = (character_id, self.reset_generation, slot.binding_generation,
                    slot.state["state_epoch"], slot.state["revision"], slot.watermark, slot.publication_watermark)
        return hashlib.sha256(repr(boundary).encode("utf-8")).hexdigest()

    async def reset(self, character_id: str, expected_epoch: str, request_id: str, *, preserve_profile: bool = False,
                    expected_confirmation: str | None = None) -> dict:
        slot = self._characters.get(character_id)
        if slot is None or slot.deleted:
            raise RecommendationError("invalid_character_id")
        generation = self.reset_generation
        reset_key = (request_id, expected_epoch)
        if reset_key not in slot.reset_cutoffs:
            if len(slot.reset_cutoffs) >= 16:
                raise RecommendationError("capacity_exhausted")
            slot.reset_cutoffs[reset_key] = slot.watermark
        # Preserve the first accepted intent across uncertain physical outcomes.
        # A retry must not move its erasure boundary over subsequently accepted input.
        slot.reset_operations += 1
        slot.resetting = True
        slot.changed.set()
        receipt = None
        try:
            async with slot.lock:
                if expected_confirmation is not None:
                    current = _validate_profile(await asyncio.wait_for(self.store.load(character_id), self.settings.store_timeout))
                    self._adopt_durable_state(slot, current)
                    accepted = any(r.get("request_id") == request_id for r in current["reset_requests"])
                    if not accepted and current["state_epoch"] != expected_epoch:
                        raise RecommendationError("epoch_conflict")
                    if not accepted and expected_confirmation != self.reset_confirmation(character_id):
                        raise RecommendationError("stale_operation")
                boundary = (slot.watermark, slot.publication_watermark, slot.binding_generation, slot.state["state_epoch"])
                if preserve_profile:
                    await self._flush_captures_locked(slot)
                if boundary != (slot.watermark, slot.publication_watermark, slot.binding_generation, slot.state["state_epoch"]):
                    raise RecommendationError("stale_operation")
                binding = slot.binding_generation
                guard = lambda: (not self._closing and not self._maintenance and not slot.deleted
                                 and slot.binding_generation == binding and self.reset_generation == generation)
                confirmation = self.reset_confirmation(character_id)
                confirmation_args = ({"expected_confirmation": expected_confirmation,
                                      "expected_revision": slot.state["revision"],
                                      "confirm_if": lambda: self.reset_confirmation(character_id) == confirmation}
                                     if expected_confirmation is not None else {})
                receipt = await asyncio.wait_for(self.store.reset(character_id, expected_epoch=expected_epoch, request_id=request_id, guard=guard,
                    **({"preserve_profile": True} if preserve_profile else {}), **confirmation_args), self.settings.store_timeout)
                state = _validate_profile(await asyncio.wait_for(self.store.load(character_id), self.settings.store_timeout))
                if not guard():
                    raise RecommendationError("stale_operation")
                self._adopt_durable_state(slot, state)
                slot.reset_cutoffs.pop(reset_key, None)
                return {"character_id": character_id, "epoch": receipt["state_epoch"], "revision": receipt["revision"], "request_id": request_id}
        except Exception as exc:
            code = self._error_code(exc)
            if receipt is None and code in {"epoch_conflict", "invalid_request", "stale_operation"}:
                slot.reset_cutoffs.pop(reset_key, None)
            if code not in {"epoch_conflict", "invalid_request", "stale_operation"}:
                slot.last_error = code
            raise
        finally:
            slot.reset_operations -= 1
            slot.resetting = slot.reset_operations > 0
            if not slot.resetting and self._enabled() and (slot.events or slot.captures):
                self._wake(slot)

    def restrictions_snapshot(self, character_id: str) -> tuple[dict, ...]:
        slot = self._characters.get(character_id)
        if not self._enabled() or slot is None or slot.deleted or slot.resetting or slot.state is None:
            return ()
        now = time.time()
        return tuple(deepcopy([restriction for restriction in slot.state["restrictions"]
                               if restriction.get("expires_at", float("inf")) > now]))

    def control_token(self, character_id: str):
        slot = self._characters.get(character_id)
        if not self._enabled() or slot is None or slot.deleted or slot.resetting or slot.state is None:
            return None
        return (character_id, self._enable_generation, slot.state["state_epoch"], slot.binding_generation, self.store.root_generation,
                slot.state["revision"], slot.watermark, slot.analyzed_watermark, slot.publication_watermark)

    def control_token_current(self, token) -> bool:
        return token is not None and self.control_token(token[0]) == token

    async def output_allowed(self, character_id: str, text: str, language: str = "en") -> bool:
        restrictions = self.restrictions_snapshot(character_id)
        if not restrictions:
            return True
        token = self.control_token(character_id)
        acquired = False
        try:
            await asyncio.wait_for(self._semaphore.acquire(), self.settings.worker_wait_seconds)
            acquired = True
            if not self.control_token_current(token):
                return False
            allowed = await self.analyzer.output_allowed(text, restrictions, language)
        except Exception:
            return False
        finally:
            if acquired:
                self._semaphore.release()
        return self.control_token_current(token) and allowed

    async def validate_selection(self, snapshot: RecommendationSnapshot, subject_id: str, text: str, language: str = "en") -> bool:
        if not self.is_current(snapshot):
            return False
        candidate = next((s for s in snapshot.candidates if s["subject_id"] == subject_id), None)
        if candidate is None:
            return False
        acquired = False
        try:
            await asyncio.wait_for(self._semaphore.acquire(), self.settings.worker_wait_seconds)
            acquired = True
            if not self.is_current(snapshot):
                return False
            adopted = await self.analyzer.choice_matches(text, candidate, language)
        except Exception:
            return False
        finally:
            if acquired:
                self._semaphore.release()
        return self.is_current(snapshot) and adopted

    async def delete_character(self, character_id: str) -> None:
        slot = self._characters.get(character_id)
        if slot is None:
            return
        slot.deleted = True
        slot.binding_generation = uuid4().hex
        slot.changed.set()
        task = slot.task
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with slot.lock:
            await self.store.delete(character_id)
            slot.deleted_cleanup_complete = True
            slot.events.clear()
            slot.captures.clear()
            slot.analysis_commit = None

    async def close(self, *, deadline: float | None = None) -> None:
        if deadline is None:
            deadline = asyncio.get_running_loop().time() + self.settings.close_timeout
        self._closing = True
        self._enable_generation += 1
        tasks = []
        for slot in self._characters.values():
            slot.changed.set()
            if slot.task and slot.task is not asyncio.current_task():
                slot.task.cancel()
                tasks.append(slot.task)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=max(0, deadline - asyncio.get_running_loop().time()))
            if pending:
                # Retain writer ownership rather than handing it to a successor
                # while a provider or physical writer is still alive.
                raise RecommendationError("closing_timeout")
        try:
            async with asyncio.timeout_at(deadline):
                await self.flush_publications(allow_closing=True, allow_maintenance=True)
        finally:
            # Even a failed flush must seal/join a finished writer. A live
            # physical task instead causes a bounded error and keeps its lock.
            await self.store.close(deadline=deadline)
