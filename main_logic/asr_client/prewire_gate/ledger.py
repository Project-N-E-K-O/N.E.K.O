"""Synchronous in-memory ledger for pre-wire audio interval ownership."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from uuid import uuid4

from .contracts import (
    PrewireCommitStage,
    PrewireContiguousPlan,
    PrewireContractError,
    PrewireDecisionState,
    PrewireGap,
    PrewireIntervalIdentity,
    PrewireIntervalRecord,
    PrewireIntervalSpec,
    PrewireRelease,
    PrewireStreamKey,
    OriginalAsrMapping,
    SampleRange,
)


class PrewireLedgerError(RuntimeError):
    """Base error for invalid state changes in the pre-wire ledger."""


class PrewireCapacityError(PrewireLedgerError):
    pass


class PrewireIdentityError(PrewireLedgerError):
    pass


class PrewireOverlapError(PrewireLedgerError):
    pass


class PrewireTransitionError(PrewireLedgerError):
    pass


_DECISIONS_REQUIRING_SCORE = {
    PrewireDecisionState.KEEP,
    PrewireDecisionState.DROP,
    PrewireDecisionState.UNCERTAIN,
}
_FINAL_CONTIGUOUS_DECISIONS = {
    PrewireDecisionState.KEEP,
    PrewireDecisionState.DROP,
    PrewireDecisionState.UNCERTAIN,
    PrewireDecisionState.UNAVAILABLE,
    PrewireDecisionState.STALE,
}
_COMMIT_TRANSITIONS = {
    PrewireCommitStage.SELECTED: {PrewireCommitStage.ENQUEUED},
    PrewireCommitStage.ENQUEUED: {
        PrewireCommitStage.WRITTEN,
        PrewireCommitStage.UNKNOWN,
    },
    PrewireCommitStage.WRITTEN: {
        PrewireCommitStage.REMOTE_CONFIRMED,
        PrewireCommitStage.UNKNOWN,
    },
    PrewireCommitStage.UNKNOWN: {PrewireCommitStage.REMOTE_CONFIRMED},
}


class PrewireIntervalLedger:
    """Bounded local writer for interval decisions and delivery progress."""

    def __init__(self, *, capacity: int = 128) -> None:
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self._capacity = capacity
        self._ledger_id = uuid4().hex
        self._revision = 0
        self._records: OrderedDict[
            tuple[PrewireStreamKey, int], PrewireIntervalRecord
        ] = OrderedDict()
        self._release_cursors: dict[PrewireStreamKey, int] = {}
        self._asr_cursors: dict[PrewireStreamKey, int] = {}
        self._stream_origins: dict[PrewireStreamKey, tuple[int, int]] = {}

    def open_stream(
        self,
        stream: PrewireStreamKey,
        *,
        original_cursor: int,
        asr_cursor: int = 0,
    ) -> None:
        if type(stream) is not PrewireStreamKey:
            raise TypeError("stream must be PrewireStreamKey")
        if type(original_cursor) is not int or original_cursor < 0:
            raise ValueError("original_cursor must be a non-negative integer")
        if type(asr_cursor) is not int or asr_cursor < 0:
            raise ValueError("asr_cursor must be a non-negative integer")
        origin = (original_cursor, asr_cursor)
        existing_origin = self._stream_origins.get(stream)
        if existing_origin is not None and existing_origin != origin:
            raise PrewireIdentityError("stream is already open at a different cursor")
        if existing_origin is None:
            self._stream_origins[stream] = origin
            self._release_cursors[stream] = original_cursor
            self._asr_cursors[stream] = asr_cursor
            self._revision += 1

    def add(self, spec: PrewireIntervalSpec) -> PrewireIntervalRecord:
        if type(spec) is not PrewireIntervalSpec:
            raise TypeError("spec must be PrewireIntervalSpec")
        identity = spec.identity
        if identity.stream not in self._release_cursors:
            raise PrewireIdentityError(
                "stream must be opened before intervals are added"
            )
        if spec.commit_range.start < self._release_cursors[identity.stream]:
            raise PrewireIdentityError(
                "commit range precedes the stream release cursor"
            )
        key = self._key(identity)
        existing = self._records.get(key)
        if existing is not None:
            if existing.spec == spec:
                return existing
            raise PrewireIdentityError(
                "segment identity is already bound to another interval"
            )
        for current in self._records.values():
            if current.spec.identity.stream != identity.stream:
                continue
            if current.spec.commit_range.overlaps(spec.commit_range):
                raise PrewireOverlapError(
                    "original commit ranges cannot overlap within a stream"
                )
        self._make_capacity()
        record = PrewireIntervalRecord(spec)
        self._records[key] = record
        self._revision += 1
        return record

    def get(self, identity: PrewireIntervalIdentity) -> PrewireIntervalRecord | None:
        record = self._records.get(self._key(identity))
        if record is None or record.spec.identity != identity:
            return None
        return record

    def record_score(
        self,
        identity: PrewireIntervalIdentity,
        *,
        score: float,
        scoring_parameters_digest: str,
    ) -> PrewireIntervalRecord:
        record = self._require_exact(identity)
        if self._original_range_is_consumed(record):
            raise PrewireTransitionError("consumed original range is immutable")
        if record.commit_stage is not PrewireCommitStage.PENDING:
            raise PrewireTransitionError("score cannot change after commit selection")
        if record.decision in {
            PrewireDecisionState.KEEP,
            PrewireDecisionState.DROP,
            PrewireDecisionState.STALE,
        }:
            raise PrewireTransitionError("score cannot change after a final decision")
        updated = replace(
            record,
            score=score,
            scoring_parameters_digest=scoring_parameters_digest,
        )
        self._store(updated)
        self._revision += 1
        return updated

    def decide(
        self,
        identity: PrewireIntervalIdentity,
        decision: PrewireDecisionState,
        *,
        reason: str,
        used_ended_micro_event_rule: bool = False,
    ) -> PrewireIntervalRecord:
        if (
            type(decision) is not PrewireDecisionState
            or decision is PrewireDecisionState.PENDING
        ):
            raise PrewireTransitionError("decision must be a non-pending state")
        record = self._require_exact(identity)
        if self._original_range_is_consumed(record):
            raise PrewireTransitionError("consumed original range is immutable")
        if record.commit_stage is not PrewireCommitStage.PENDING:
            raise PrewireTransitionError(
                "decision cannot change after commit selection"
            )
        if record.decision in {
            PrewireDecisionState.KEEP,
            PrewireDecisionState.DROP,
            PrewireDecisionState.STALE,
        }:
            if (
                record.decision is decision
                and record.decision_reason == reason
                and record.used_ended_micro_event_rule is used_ended_micro_event_rule
            ):
                return record
            raise PrewireTransitionError("final decision is immutable")
        if (
            used_ended_micro_event_rule
            and not record.spec.permits_ended_micro_event_rule
        ):
            raise PrewireTransitionError(
                "200ms rule requires an ended, trusted, independent micro event"
            )
        scoreless_micro_drop = bool(
            decision is PrewireDecisionState.DROP
            and used_ended_micro_event_rule
            and record.spec.permits_ended_micro_event_rule
        )
        if (
            decision in _DECISIONS_REQUIRING_SCORE
            and not scoreless_micro_drop
            and record.score is None
        ):
            raise PrewireTransitionError(
                "scored decision requires score and parameters"
            )
        try:
            updated = replace(
                record,
                decision=decision,
                decision_reason=reason,
                used_ended_micro_event_rule=used_ended_micro_event_rule,
            )
        except PrewireContractError as exc:
            raise PrewireTransitionError(str(exc)) from exc
        self._store(updated)
        self._revision += 1
        return updated

    def plan_contiguous(self, stream: PrewireStreamKey) -> PrewireContiguousPlan:
        """Describe the currently releasable prefix without changing ledger state."""

        if type(stream) is not PrewireStreamKey:
            raise TypeError("stream must be PrewireStreamKey")
        if stream not in self._release_cursors:
            raise PrewireIdentityError("stream is not open")
        original_cursor_start = self._release_cursors[stream]
        asr_cursor_start = self._asr_cursors[stream]
        cursor = original_cursor_start
        asr_cursor = asr_cursor_start
        record_identities: list[PrewireIntervalIdentity] = []
        releases: list[PrewireRelease] = []
        gaps: list[PrewireGap] = []
        ordered = sorted(
            (
                record
                for record in self._records.values()
                if record.spec.identity.stream == stream
                and record.spec.commit_range.end > cursor
            ),
            key=lambda record: record.spec.commit_range.start,
        )
        for record in ordered:
            commit_range = record.spec.commit_range
            if commit_range.start != cursor:
                break
            if record.decision not in _FINAL_CONTIGUOUS_DECISIONS:
                break
            if (
                record.decision
                in {
                    PrewireDecisionState.UNCERTAIN,
                    PrewireDecisionState.UNAVAILABLE,
                }
                and not record.spec.event_ended
            ):
                break
            record_identities.append(record.spec.identity)
            if record.decision is PrewireDecisionState.KEEP:
                if record.commit_stage is not PrewireCommitStage.PENDING:
                    raise PrewireTransitionError(
                        "contiguous keep range was already claimed"
                    )
                asr_range = SampleRange(
                    asr_cursor,
                    asr_cursor + commit_range.sample_count,
                )
                releases.append(
                    PrewireRelease(
                        record.spec.identity,
                        commit_range,
                        asr_range,
                    )
                )
                asr_cursor = asr_range.end
            else:
                gaps.append(
                    PrewireGap(
                        record.spec.identity,
                        commit_range,
                        record.decision,
                    )
                )
            cursor = commit_range.end
        return PrewireContiguousPlan(
            ledger_id=self._ledger_id,
            revision=self._revision,
            stream=stream,
            original_cursor_start=original_cursor_start,
            original_cursor_end=cursor,
            asr_cursor_start=asr_cursor_start,
            asr_cursor_end=asr_cursor,
            record_identities=tuple(record_identities),
            releases=tuple(releases),
            gaps=tuple(gaps),
        )

    def claim_enqueued(
        self,
        plan: PrewireContiguousPlan,
    ) -> tuple[PrewireIntervalRecord, ...]:
        """CAS a successfully enqueued plan into the ledger as one atomic change."""

        self._validate_plan(plan)
        if not plan.releases:
            raise PrewireTransitionError("contiguous plan has no enqueued audio")

        release_by_identity = {release.identity: release for release in plan.releases}
        updated_records = self._records.copy()
        claimed: list[PrewireIntervalRecord] = []
        for identity in plan.record_identities:
            release = release_by_identity.get(identity)
            if release is None:
                continue
            key = self._key(identity)
            record = updated_records[key]
            updated = replace(
                record,
                commit_stage=PrewireCommitStage.ENQUEUED,
                original_to_asr=OriginalAsrMapping(
                    release.original_range,
                    release.asr_range,
                ),
            )
            updated_records[key] = updated
            claimed.append(updated)

        self._records = updated_records
        self._release_cursors[plan.stream] = plan.original_cursor_end
        self._asr_cursors[plan.stream] = plan.asr_cursor_end
        self._revision += 1
        return tuple(claimed)

    def claim_gaps(self, plan: PrewireContiguousPlan) -> tuple[PrewireGap, ...]:
        """CAS a gap-only plan without claiming that any audio was enqueued."""

        self._validate_plan(plan)
        if plan.releases:
            raise PrewireTransitionError("gap claim cannot include ASR audio")
        if not plan.gaps:
            raise PrewireTransitionError("contiguous plan has no gaps to claim")
        self._release_cursors[plan.stream] = plan.original_cursor_end
        self._revision += 1
        return plan.gaps

    def advance_commit(
        self,
        identity: PrewireIntervalIdentity,
        *,
        expected: PrewireCommitStage,
        next_stage: PrewireCommitStage,
    ) -> PrewireIntervalRecord:
        if (
            type(expected) is not PrewireCommitStage
            or type(next_stage) is not PrewireCommitStage
        ):
            raise TypeError("commit stages must be PrewireCommitStage")
        record = self._require_exact(identity)
        if record.commit_stage is not expected:
            raise PrewireTransitionError("commit stage compare-and-set failed")
        if next_stage not in _COMMIT_TRANSITIONS.get(expected, set()):
            raise PrewireTransitionError("commit stage transition is not allowed")
        updated = replace(record, commit_stage=next_stage)
        self._store(updated)
        self._revision += 1
        return updated

    def release_cursor(self, stream: PrewireStreamKey) -> int:
        try:
            return self._release_cursors[stream]
        except KeyError as exc:
            raise PrewireIdentityError("stream is not open") from exc

    def asr_cursor(self, stream: PrewireStreamKey) -> int:
        try:
            return self._asr_cursors[stream]
        except KeyError as exc:
            raise PrewireIdentityError("stream is not open") from exc

    def _make_capacity(self) -> None:
        if len(self._records) < self._capacity:
            return
        for key, record in self._records.items():
            cursor = self._release_cursors[record.spec.identity.stream]
            safely_consumed = record.spec.commit_range.end <= cursor
            safe_stage = record.commit_stage in {
                PrewireCommitStage.PENDING,
                PrewireCommitStage.REMOTE_CONFIRMED,
            }
            if safely_consumed and safe_stage:
                del self._records[key]
                return
        raise PrewireCapacityError(
            "capacity cannot evict pending delivery or ambiguous remote state"
        )

    @staticmethod
    def _key(identity: PrewireIntervalIdentity) -> tuple[PrewireStreamKey, int]:
        if type(identity) is not PrewireIntervalIdentity:
            raise TypeError("identity must be PrewireIntervalIdentity")
        return identity.stream, identity.segment_id

    def _require_exact(
        self, identity: PrewireIntervalIdentity
    ) -> PrewireIntervalRecord:
        record = self._records.get(self._key(identity))
        if record is None or record.spec.identity != identity:
            raise PrewireIdentityError("interval identity is stale or unknown")
        return record

    def _store(self, record: PrewireIntervalRecord) -> None:
        key = self._key(record.spec.identity)
        self._records[key] = record
        self._records.move_to_end(key)

    def _original_range_is_consumed(self, record: PrewireIntervalRecord) -> bool:
        return bool(
            record.spec.commit_range.end
            <= self._release_cursors[record.spec.identity.stream]
        )

    def _validate_plan(self, plan: PrewireContiguousPlan) -> None:
        if type(plan) is not PrewireContiguousPlan:
            raise TypeError("plan must be PrewireContiguousPlan")
        if plan.ledger_id != self._ledger_id:
            raise PrewireIdentityError("contiguous plan belongs to another ledger")
        if plan.revision != self._revision:
            raise PrewireTransitionError("contiguous plan is stale")
        if (
            self._release_cursors.get(plan.stream) != plan.original_cursor_start
            or self._asr_cursors.get(plan.stream) != plan.asr_cursor_start
        ):
            raise PrewireTransitionError("contiguous plan cursors are stale")
        if self.plan_contiguous(plan.stream) != plan:
            raise PrewireTransitionError(
                "contiguous plan no longer matches ledger state"
            )
