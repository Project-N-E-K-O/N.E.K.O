"""Deterministic Numeric v2 state engine and Runtime entry points."""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from copy import deepcopy
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime
import re
from pathlib import Path
from typing import Any, Mapping

from utils.tokenize import truncate_to_tokens

from .numeric_v2_context import pending_transition_record
from .numeric_v2_fixed_narration import add_entry, displayed_ids, required_pending, validate_delivery

from .numeric_v2 import (
    CompiledNumericV2Package,
    NumericV2Compiler,
)
from .numeric_v2_performance import (
    is_silent_invitation_control,
    transition_source_dialogue_policy,
    valid_mixed_performance_policy,
    valid_ordered_content,
    valid_scene_narration,
)
from .numeric_v2_budget import (
    NUMERIC_V2_ACTOR_BUDGET_PROFILES,
    NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE,
)
from .numeric_v2_action_projection import (
    normalize_player_action_projection,
    project_player_action_result,
)
from .numeric_v2_narrative import apply_narrative_record, validate_narrative_state
from .numeric_v2_store import (
    NumericV2SessionStore,
    NumericV2StoredSession,
)


SESSION_SCHEMA = "neko.script.session.numeric.v3"
LEDGER_EVENT_SCHEMA = "neko.script.ledger_event.numeric.v2"
PERFORMANCE_RECORD_SCHEMA = "neko.script.performance_record.numeric.v2"
FACT_PROJECTION_SCHEMA = "neko.script.fact_projection.numeric.v1"
TIMELINE_PROJECTION_SCHEMA = "neko.script.timeline_projection.numeric.v1"
STORY_STATE_SCHEMA = "neko.script.story_state.numeric.v1"
NUMERIC_V2_PLAYER_INPUT_MAX_TOKENS = 140
NUMERIC_V2_INPUT_SOURCES = frozenset({"freeform", "suggestion", "reinvite"})
# Current Sessions store only body, numeric, and transition states; old evidence-chain Sessions are no longer recoverable.
_DIALOGUE_POLICIES = frozenset({"required", "optional", "forbidden"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_STORY_FACT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_STORY_FACT_VISIBILITIES = frozenset({"public", "story"})
_STORY_STATE_MAX_FACTS = 256
# Runtime-owned scene enter/leave events; one pair is added per scene change.
_RUNTIME_SCENE_EVENT_KEY_RE = re.compile(
    r"^event:scene\.(?:entered|left):[A-Za-z0-9][A-Za-z0-9._-]{0,127}:r[0-9]+$"
)
_STORY_STATE_MAX_FACT_OPS = 32
_STORY_STATE_MAX_VALUE_CHARS = 240
_FACT_CONTRACT_VALUE_TYPES = frozenset({"bool", "int", "string"})
_FACT_CONTRACT_VISIBILITIES = frozenset({"public", "story"})
_FACT_CANDIDATE_EVIDENCE_SOURCES = frozenset({"player_input", "actor_performance", "runtime_fact"})
_FACT_CANDIDATE_MAX_ITEMS = 16
_FACT_CANDIDATE_MAX_EVIDENCE = 4
# Completion facts may reference only outcomes that already happened; these terms block explicit future states without mistaking completed statements such as
# 'preparations are complete', containing homographic nouns, for plans.
_UNCONFIRMED_FACT_EVIDENCE_RE = re.compile(
    r"即将|将要|尚未|还没|"
    r"(?:准备|考虑|计划)(?:去|做|开始|进入|执行|进行|前往|离开|启动|按下|发力)|"
    r"打算"
)
# Arrival contracts require crossing the target boundary, not merely moving toward it. Scope by contract description
# to avoid rejecting facts intentionally recording startup states, such as 'evacuation has begun'.
_COMPLETED_LOCATION_FACT_RE = re.compile(
    r"(?:已经|已).{0,48}(?:进入|抵达|到达|安置|撤离)"
)
_IN_PROGRESS_LOCATION_EVIDENCE_RE = re.compile(
    r"(?:向|往).{0,48}(?:移动|前进|赶去|走去|跑去|撤离)"
)
_COMPLETED_LOCATION_EVIDENCE_RE = re.compile(
    r"(?:已经|已|全部).{0,48}(?:进入|抵达|到达|安置)|"
    r"(?:进入|抵达|到达).{0,16}(?:安全|完成)"
)
_COMPARATORS = {
    "==": lambda left, right: left == right,
    "!=": lambda left, right: left != right,
    ">": lambda left, right: left > right,
    "<": lambda left, right: left < right,
    ">=": lambda left, right: left >= right,
    "<=": lambda left, right: left <= right,
}


class NumericV2RuntimeError(ValueError):
    """The Numeric v2 turn cannot settle against current deterministic state."""  # noqa: DOCSTRING_CJK


class NumericV2RevisionConflictError(NumericV2RuntimeError):
    """The client submitted against an expired revision."""  # noqa: DOCSTRING_CJK


class NumericV2DuplicateTurnError(NumericV2RuntimeError):
    """The same client_turn_id has already been committed successfully."""  # noqa: DOCSTRING_CJK


def _stable_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise NumericV2RuntimeError(f"{field}_invalid")
    return value


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NumericV2RuntimeError(f"{field}_invalid")
    return value


def _story_scalar(value: Any, field: str) -> bool | int | str:
    """Restrict fact values to simple scalars that serialize consistently and can be replayed."""  # noqa: DOCSTRING_CJK

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value and len(value) <= _STORY_STATE_MAX_VALUE_CHARS:
        return value
    raise NumericV2RuntimeError(f"{field}_invalid")


def _story_fact_contract(value: Any) -> dict[str, dict[str, str]]:
    """Extract compiler-validated fact contracts; missing contracts disable model fact writes."""  # noqa: DOCSTRING_CJK

    if value is None:
        return {}
    if not isinstance(value, Mapping) or set(value) != {"facts"}:
        raise NumericV2RuntimeError("story_fact_contract_invalid")
    facts = value.get("facts")
    if not isinstance(facts, Mapping):
        raise NumericV2RuntimeError("story_fact_contract_invalid")
    result: dict[str, dict[str, str]] = {}
    for key, definition in facts.items():
        if (
            not isinstance(key, str)
            or not isinstance(definition, Mapping)
            or not {"value_type", "visibility"}.issubset(definition)
            or set(definition).difference({"value_type", "visibility", "description"})
            or definition.get("value_type") not in _FACT_CONTRACT_VALUE_TYPES
            or definition.get("visibility") not in _FACT_CONTRACT_VISIBILITIES
            or (
                "description" in definition
                and (
                    not isinstance(definition.get("description"), str)
                    or not str(definition.get("description") or "").strip()
                )
            )
        ):
            raise NumericV2RuntimeError("story_fact_contract_invalid")
        result[key] = {
            "value_type": str(definition["value_type"]),
            "visibility": str(definition["visibility"]),
            **(
                {"description": str(definition["description"])}
                if "description" in definition
                else {}
            ),
        }
    return result


def _story_fact_value_matches(value: bool | int | str, value_type: str) -> bool:
    """Check fact value types against the contract; reject booleans as integers despite Python subclassing."""  # noqa: DOCSTRING_CJK

    if value_type == "bool":
        return type(value) is bool
    if value_type == "int":
        return type(value) is int
    if value_type == "string":
        return type(value) is str
    return False


def validate_fact_candidates(
    candidates: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    *,
    fact_contract: Mapping[str, Any] | None,
    evidence_sources: Mapping[str, str],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    """Convert only definite candidates with complete quadruples and verbatim evidence into fact operations and audit records."""  # noqa: DOCSTRING_CJK

    contract = _story_fact_contract(fact_contract)
    if not contract:
        raise NumericV2RuntimeError("story_fact_candidate_contract_missing")
    if not isinstance(candidates, (list, tuple)) or len(candidates) > _FACT_CANDIDATE_MAX_ITEMS:
        raise NumericV2RuntimeError("story_fact_candidates_invalid")
    if not isinstance(evidence_sources, Mapping):
        raise NumericV2RuntimeError("story_fact_candidate_evidence_invalid")
    operations: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    expected_fields = {
        "op", "key", "value", "visibility", "confidence",
        "subject", "action", "object", "result", "evidence",
    }
    for candidate in candidates:
        if not isinstance(candidate, Mapping) or set(candidate) != expected_fields:
            raise NumericV2RuntimeError("story_fact_candidate_shape_invalid")
        if candidate.get("op") != "set" or candidate.get("confidence") != "confirmed":
            raise NumericV2RuntimeError("story_fact_candidate_not_confirmed")
        key = candidate.get("key")
        if not isinstance(key, str) or key in seen_keys or key not in contract:
            raise NumericV2RuntimeError("story_fact_candidate_key_not_allowed")
        definition = contract[key]
        value = _story_scalar(candidate.get("value"), "story_state_fact_value")
        if candidate.get("visibility") != definition["visibility"]:
            raise NumericV2RuntimeError("story_fact_candidate_visibility_not_allowed")
        if not _story_fact_value_matches(value, definition["value_type"]):
            raise NumericV2RuntimeError("story_fact_candidate_value_type_not_allowed")
        tuple_fields: dict[str, str] = {}
        for field in ("subject", "action", "object", "result"):
            text = candidate.get(field)
            if not isinstance(text, str) or not text.strip() or len(text.strip()) > 160:
                raise NumericV2RuntimeError("story_fact_candidate_tuple_invalid")
            tuple_fields[field] = text.strip()
        evidence = candidate.get("evidence")
        if (
            not isinstance(evidence, list)
            or not 1 <= len(evidence) <= _FACT_CANDIDATE_MAX_EVIDENCE
        ):
            raise NumericV2RuntimeError("story_fact_candidate_evidence_invalid")
        normalized_evidence: list[dict[str, str]] = []
        for row in evidence:
            if not isinstance(row, Mapping) or set(row) != {"source", "quote"}:
                raise NumericV2RuntimeError("story_fact_candidate_evidence_invalid")
            source = row.get("source")
            quote = row.get("quote")
            source_text = evidence_sources.get(source) if isinstance(source, str) else None
            if (
                source not in _FACT_CANDIDATE_EVIDENCE_SOURCES
                or not isinstance(source_text, str)
                or not isinstance(quote, str)
                or not quote.strip()
                or quote not in source_text
            ):
                raise NumericV2RuntimeError("story_fact_candidate_evidence_unverifiable")
            if _UNCONFIRMED_FACT_EVIDENCE_RE.search(quote):
                raise NumericV2RuntimeError("story_fact_candidate_evidence_not_completed")
            description = str(definition.get("description") or "")
            if (
                _COMPLETED_LOCATION_FACT_RE.search(description)
                and _IN_PROGRESS_LOCATION_EVIDENCE_RE.search(quote)
                and not _COMPLETED_LOCATION_EVIDENCE_RE.search(quote)
            ):
                raise NumericV2RuntimeError("story_fact_candidate_evidence_not_completed")
            normalized_evidence.append({"source": source, "quote": quote})
        seen_keys.add(key)
        operations.append({
            "op": "set",
            "key": key,
            "value": value,
            "visibility": definition["visibility"],
        })
        audit.append({"key": key, **tuple_fields, "evidence": normalized_evidence})
    return tuple(operations), tuple(audit)


def _normalize_actor_fact_candidates(
    candidates: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    *,
    fact_contract: Mapping[str, Mapping[str, Any]],
    allowed_keys: set[str],
) -> tuple[dict[str, Any], ...]:
    """Expand the Actor's three-field candidates into a uniform audit shape; take permissions and semantics only from the author's contract."""  # noqa: DOCSTRING_CJK

    if not isinstance(candidates, (list, tuple)) or len(candidates) > _FACT_CANDIDATE_MAX_ITEMS:
        raise NumericV2RuntimeError("actor_fact_candidates_invalid")
    normalized: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping) or set(candidate) != {"key", "value", "evidence_quote"}:
            raise NumericV2RuntimeError("actor_fact_candidate_shape_invalid")
        key = candidate.get("key")
        if not isinstance(key, str) or key not in allowed_keys:
            raise NumericV2RuntimeError("actor_fact_candidate_key_not_allowed")
        definition = fact_contract.get(key)
        if not isinstance(definition, Mapping):
            raise NumericV2RuntimeError("actor_fact_candidate_key_not_allowed")
        description = definition.get("description")
        quote = candidate.get("evidence_quote")
        if not isinstance(description, str) or not description.strip():
            raise NumericV2RuntimeError("actor_fact_candidate_description_missing")
        if not isinstance(quote, str) or not quote.strip():
            raise NumericV2RuntimeError("actor_fact_candidate_evidence_invalid")
        normalized.append({
            "op": "set",
            "key": key,
            "value": candidate.get("value"),
            "visibility": definition.get("visibility"),
            "confidence": "confirmed",
            # These four internal audit labels derive from contracts; Actor need not regenerate them.
            "subject": "本轮最终演绎",
            "action": "确认",
            "object": key,
            "result": description.strip(),
            "evidence": [{"source": "actor_performance", "quote": quote.strip()}],
        })
    return tuple(normalized)


def _validate_story_state(value: Any) -> dict[str, Any]:
    """Validate the story-state projection to ensure it contains only deterministic event facts."""  # noqa: DOCSTRING_CJK

    if not isinstance(value, Mapping) or set(value) != {"schema", "revision", "facts"}:
        raise NumericV2RuntimeError("story_state_schema_invalid")
    if value.get("schema") != STORY_STATE_SCHEMA:
        raise NumericV2RuntimeError("story_state_schema_invalid")
    revision = _integer(value.get("revision"), "story_state_revision")
    if revision < 0:
        raise NumericV2RuntimeError("story_state_revision_invalid")
    facts = value.get("facts")
    if not isinstance(facts, Mapping) or len(facts) > _STORY_STATE_MAX_FACTS:
        raise NumericV2RuntimeError("story_state_facts_invalid")
    normalized_facts: dict[str, dict[str, Any]] = {}
    expected_fact_keys = {
        "value",
        "visibility",
        "source_revision",
        "client_turn_id",
        "updated_revision",
    }
    for raw_key, raw_fact in facts.items():
        key = raw_key if isinstance(raw_key, str) else ""
        if not _STORY_FACT_KEY_RE.fullmatch(key) or not isinstance(raw_fact, Mapping):
            raise NumericV2RuntimeError("story_state_fact_invalid")
        if set(raw_fact) != expected_fact_keys:
            raise NumericV2RuntimeError("story_state_fact_invalid")
        visibility = raw_fact.get("visibility")
        if visibility not in _STORY_FACT_VISIBILITIES:
            raise NumericV2RuntimeError("story_state_fact_visibility_invalid")
        source_revision = _integer(raw_fact.get("source_revision"), "story_state_fact_source_revision")
        updated_revision = _integer(raw_fact.get("updated_revision"), "story_state_fact_updated_revision")
        if (
            source_revision < 0
            or updated_revision < 0
            or source_revision > revision
            or updated_revision > revision
        ):
            raise NumericV2RuntimeError("story_state_fact_revision_invalid")
        normalized_facts[key] = {
            "value": _story_scalar(raw_fact.get("value"), "story_state_fact_value"),
            "visibility": visibility,
            "source_revision": source_revision,
            "client_turn_id": _stable_id(raw_fact.get("client_turn_id"), "story_state_fact_client_turn_id"),
            "updated_revision": updated_revision,
        }
    return {
        "schema": STORY_STATE_SCHEMA,
        "revision": revision,
        "facts": normalized_facts,
    }


def apply_fact_ops(
    current: Mapping[str, Any],
    *,
    revision: int,
    client_turn_id: str,
    ops: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    allowed_keys: set[str] | frozenset[str] | None = None,
    fact_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically apply fact operations to a copy; this is the sole permitted writer of the story fact ledger."""  # noqa: DOCSTRING_CJK

    current_state = _validate_story_state(current)
    if _integer(revision, "story_state_revision") != current_state["revision"] + 1:
        raise NumericV2RuntimeError("story_state_revision_mismatch")
    turn_id = _stable_id(client_turn_id, "client_turn_id")
    if not isinstance(ops, (list, tuple)) or len(ops) > _STORY_STATE_MAX_FACT_OPS:
        raise NumericV2RuntimeError("story_state_fact_ops_invalid")
    if ops and allowed_keys is None and fact_contract is None:
        raise NumericV2RuntimeError("story_state_fact_allowlist_missing")
    contract = _story_fact_contract(fact_contract)
    permitted = set(allowed_keys) if allowed_keys is not None else set(contract)
    if allowed_keys is None and fact_contract is None:
        permitted = None
    if permitted is not None and any(
        not isinstance(key, str) or not _STORY_FACT_KEY_RE.fullmatch(key)
        for key in permitted
    ):
        raise NumericV2RuntimeError("story_state_fact_key_invalid")
    facts = deepcopy(current_state["facts"])
    seen_keys: set[str] = set()
    for raw_op in ops:
        if not isinstance(raw_op, Mapping):
            raise NumericV2RuntimeError("story_state_fact_op_invalid")
        operation = raw_op.get("op")
        key = raw_op.get("key")
        if operation not in {"set", "delete"} or not isinstance(key, str) or not _STORY_FACT_KEY_RE.fullmatch(key):
            raise NumericV2RuntimeError("story_state_fact_op_invalid")
        if key in seen_keys:
            raise NumericV2RuntimeError("story_state_fact_duplicate_op")
        seen_keys.add(key)
        if permitted is not None and key not in permitted:
            raise NumericV2RuntimeError("story_state_fact_key_not_allowed")
        definition = contract.get(key)
        if fact_contract is not None and definition is None:
            raise NumericV2RuntimeError("story_state_fact_key_not_allowed")
        if operation == "delete":
            if set(raw_op) != {"op", "key"}:
                raise NumericV2RuntimeError("story_state_fact_op_invalid")
            facts.pop(key, None)
            continue
        if set(raw_op) != {"op", "key", "value", "visibility"}:
            raise NumericV2RuntimeError("story_state_fact_op_invalid")
        visibility = raw_op.get("visibility")
        if visibility not in _STORY_FACT_VISIBILITIES:
            raise NumericV2RuntimeError("story_state_fact_visibility_invalid")
        value = _story_scalar(raw_op.get("value"), "story_state_fact_value")
        if definition is not None:
            if visibility != definition["visibility"]:
                raise NumericV2RuntimeError("story_state_fact_visibility_not_allowed")
            if not _story_fact_value_matches(value, definition["value_type"]):
                raise NumericV2RuntimeError("story_state_fact_value_type_not_allowed")
        facts[key] = {
            "value": value,
            "visibility": visibility,
            "source_revision": revision,
            "client_turn_id": turn_id,
            "updated_revision": revision,
        }
        if len(facts) > _STORY_STATE_MAX_FACTS:
            raise NumericV2RuntimeError("story_state_facts_invalid")
    # Persist lexicographically for stable replay and save diffs over identical operations.
    return _validate_story_state({
        "schema": STORY_STATE_SCHEMA,
        "revision": revision,
        "facts": {key: facts[key] for key in sorted(facts)},
    })


def _initial_story_state(start_node_id: str) -> dict[str, Any]:
    """Record entry into the opening event; Session.current_node_id still owns the current position."""  # noqa: DOCSTRING_CJK

    return _validate_story_state({
        "schema": STORY_STATE_SCHEMA,
        "revision": 0,
        "facts": {
            f"event:scene.entered:{start_node_id}:r0": {
                "value": True,
                "visibility": "public",
                "source_revision": 0,
                "client_turn_id": "opening",
                "updated_revision": 0,
            },
        },
    })


def _prune_runtime_scene_events(
    current: Mapping[str, Any],
    *,
    operations: list[dict[str, Any]],
    author_keys: set[str],
) -> Mapping[str, Any]:
    """Drop the oldest Runtime scene events only when this turn would exceed the fact cap.

    Each scene change adds two unique event keys, so loop or hub stories would
    otherwise hit the cap and reject every later transition. Pruning is a pure
    function of the committed state and this turn's operations, so ledger replay
    reproduces it exactly; states that never reached the cap are left untouched,
    which keeps existing sessions replaying byte-for-byte. Prompt projections only
    read the newest few scene events, which always survive.
    """
    state = _validate_story_state(current)
    facts = state["facts"]
    touched: set[str] = set()
    added = 0
    removed = 0
    for operation in operations:
        if not isinstance(operation, Mapping) or not isinstance(operation.get("key"), str):
            continue
        key = operation["key"]
        if key in touched:
            continue
        touched.add(key)
        if operation.get("op") == "set" and key not in facts:
            added += 1
        elif operation.get("op") == "delete" and key in facts:
            removed += 1
    overflow = len(facts) + added - removed - _STORY_STATE_MAX_FACTS
    if overflow <= 0:
        return state
    prunable = sorted(
        (int(fact["updated_revision"]), key)
        for key, fact in facts.items()
        if _RUNTIME_SCENE_EVENT_KEY_RE.fullmatch(key)
        and key not in author_keys
        and key not in touched
    )
    kept = dict(facts)
    for _revision, key in prunable[:overflow]:
        kept.pop(key)
    return {**state, "facts": kept}


def _advance_story_state(
    current: Mapping[str, Any],
    *,
    from_node_id: str,
    to_node_id: str,
    revision: int,
    client_turn_id: str,
    fact_operations: tuple[Mapping[str, Any], ...] = (),
    fact_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically advance scene events and verified facts by committed turn without copying Session position or numeric fields."""  # noqa: DOCSTRING_CJK

    operations: list[dict[str, Any]] = []
    allowed_keys: set[str] = set()
    if from_node_id != to_node_id:
        for kind, node_id in (("left", from_node_id), ("entered", to_node_id)):
            key = f"event:scene.{kind}:{node_id}:r{revision}"
            operations.append({
                "op": "set",
                "key": key,
                "value": True,
                "visibility": "public",
            })
            allowed_keys.add(key)
    operations.extend(deepcopy(dict(operation)) for operation in fact_operations)
    contract_facts = dict((fact_contract or {}).get("facts") or {})
    # Runtime declares scene-entry/exit facts itself and commits atomically with script-allowed model facts.
    for key in allowed_keys:
        contract_facts.setdefault(key, {"value_type": "bool", "visibility": "public"})
    # Candidate facts must share the declared contract; allowed sets include internal events and script allowlists.
    allowed_keys.update(contract_facts)
    current = _prune_runtime_scene_events(
        current,
        operations=operations,
        author_keys=set((fact_contract or {}).get("facts") or {}),
    )
    return apply_fact_ops(
        current,
        revision=revision,
        client_turn_id=client_turn_id,
        ops=operations,
        allowed_keys=allowed_keys,
        fact_contract={"facts": contract_facts},
    )


def _fact_projection(
    event: Mapping[str, Any],
    performance: Mapping[str, Any],
) -> dict[str, Any]:
    """Save fact evidence directly provable by Runtime this turn; do not pass natural-language guesses off as semantic facts."""  # noqa: DOCSTRING_CJK

    visible_parts: list[dict[str, str]] = []
    segments = performance.get("segments")
    if isinstance(segments, list):
        for segment in segments:
            if not isinstance(segment, Mapping):
                continue
            text = "\n".join(
                str(segment.get(key) or "").strip()
                for key in ("performance", "scene_narration")
                if str(segment.get(key) or "").strip()
            )
            if text:
                visible_parts.append({"phase": str(segment.get("phase") or "unknown"), "text": text})
    else:
        text = "\n".join(
            str(performance.get(key) or "").strip()
            for key in ("performance", "scene_narration")
            if str(performance.get(key) or "").strip()
        )
        if text:
            visible_parts.append({"phase": "ordinary", "text": text})
    return {
        "schema": FACT_PROJECTION_SCHEMA,
        "revision": int(event.get("result_revision", 0)),
        "subject_evidence": {
            "player_input": str(event.get("input_text") or ""),
            "actor_visible_parts": visible_parts,
        },
        "deterministic_events": [
            *[
                {
                    "kind": "metric_change",
                    "metric_id": str(change.get("metric_id") or ""),
                    "before": change.get("before"),
                    "after": change.get("after"),
                }
                for change in event.get("metric_changes") or []
                if isinstance(change, Mapping)
            ],
            *([{
                "kind": "node_transition",
                "from_node_id": str(event.get("from_node_id") or ""),
                "to_node_id": str(event.get("to_node_id") or ""),
            }] if str(event.get("from_node_id") or "") != str(event.get("to_node_id") or "") else []),
        ],
        "semantic_status": "evidence_only",
    }


def current_visit_started_revision(session: Any) -> int:
    """Use committed scene-entry boundaries, including control revisions."""
    records = tuple(getattr(session, "performance_history", ()) or ())
    for record in reversed(records):
        if record.get("to_node_id") == session.current_node_id and record.get("from_node_id") != session.current_node_id:
            return int(record.get("revision", 0))
        if record.get("to_node_id") != session.current_node_id:
            break
    if len(records) == session.revision:
        return 0
    controls = sum(record.get("input_source") == "reinvite" for record in records
                   if record.get("to_node_id") == session.current_node_id)
    return max(session.revision - session.node_turn_count - controls, 0)


def _timeline_projection(event: Mapping[str, Any]) -> dict[str, Any]:
    """Record scene visit order provable by Runtime without inferring calendar dates from narrative text."""  # noqa: DOCSTRING_CJK

    revision = int(event.get("result_revision", 0) or 0)
    from_node_id = str(event.get("from_node_id") or "")
    to_node_id = str(event.get("to_node_id") or from_node_id)
    node_turn_count = int(event.get("node_turn_count", 0) or 0)
    started_revision = int(event.get("visit_started_revision", max(revision - node_turn_count, 0)))
    events: list[dict[str, Any]] = []
    if from_node_id != to_node_id:
        events.extend((
            {
                "kind": "scene_left",
                "node_id": from_node_id,
                "revision": revision,
            },
            {
                "kind": "scene_entered",
                "node_id": to_node_id,
                "revision": revision,
            },
        ))
    else:
        events.append({
            "kind": "scene_turn",
            "node_id": to_node_id,
            "revision": revision,
        })
    return {
        "schema": TIMELINE_PROJECTION_SCHEMA,
        "revision": revision,
        "scene_scope": {
            "node_id": to_node_id,
            "visit_id": f"{to_node_id}:r{started_revision}",
            "started_revision": started_revision,
        },
        "events": events,
        "semantic_status": "deterministic",
    }


# Match forms of address as independent whole words bounded by text edges, whitespace, or listed punctuation, avoiding false matches in '小哥哥'/'我哥哥'.
PLAYER_ADDRESS_BOUNDARY_CHARS = r"\s,，。.!！;；:："


def _player_address_disclosed(message: str, configured_address: str) -> bool:
    """Accept only explicit self-introductions or address requests containing the complete nickname."""  # noqa: DOCSTRING_CJK

    text = str(message or "").strip()
    address = str(configured_address or "").strip()
    if not text or not address or address in {"你", "男主"}:
        return False
    escaped = re.escape(address)
    left = rf"(?:^|[{PLAYER_ADDRESS_BOUNDARY_CHARS}])"
    right = rf"(?=$|[{PLAYER_ADDRESS_BOUNDARY_CHARS}])"
    quoted_address = rf"[\"'“‘「『]?{escaped}[\"'”’」』]?"
    patterns = (
        # Chinese: require first-person identity declarations or explicit address instructions, excluding '你认识小明吗' ('do you know Xiaoming?').
        rf"{left}(?:我(?:的名字)?(?:是|叫)|请?(?:叫|称呼)我(?:为)?|你可以叫我)\s*{quoted_address}(?:吧|就好|即可)?{right}",
        rf"{left}{quoted_address}\s*(?:就是我|是我){right}",
        # Common self-introduction forms in supported UI languages; nicknames always match complete exact strings.
        rf"{left}(?:i\s+am|i['’]m|my\s+name\s+is|call\s+me|you\s+can\s+call\s+me)\s+{quoted_address}{right}",
        rf"{left}(?:me\s+llamo|ll[aá]mame|me\s+chamo|pode\s+me\s+chamar\s+de|меня\s+зовут)\s+{quoted_address}{right}",
        rf"{left}(?:私は|僕は|俺は|名前は)\s*{quoted_address}\s*(?:です|だ){right}",
        rf"{left}{quoted_address}\s*と呼んで{right}",
        rf"{left}(?:저는|나는|제\s*이름은|내\s*이름은)\s*{quoted_address}\s*(?:입니다|예요|이에요){right}",
        rf"{left}{quoted_address}\s*(?:라고|이라고)\s*불러{right}",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _player_address_known_after_turn(
    session: "ScriptSessionV2",
    message: str,
) -> bool:
    """Advance nickname knowledge only after the player explicitly discloses the complete configured nickname."""  # noqa: DOCSTRING_CJK

    if session.player_address_known:
        return True
    configured_address = str(session.catgirl_binding.get("player_address") or "").strip()
    return _player_address_disclosed(message, configured_address)


def _transition_offered(value: Mapping[str, Any]) -> bool:
    """Read whether the previous Actor turn delivered a visible, concrete transition proposal."""  # noqa: DOCSTRING_CJK

    raw = value.get("transition_offered", False)
    if not isinstance(raw, bool):
        raise NumericV2RuntimeError("session_transition_offered_invalid")
    return raw


def _dialogue_policy(value: Any, *, default: str = "required") -> str:
    policy = str(value if value is not None else default)
    if policy not in _DIALOGUE_POLICIES:
        raise NumericV2RuntimeError("session_dialogue_policy_invalid")
    return policy


@dataclass(frozen=True, slots=True)
class MetricChangeV2:
    """Determine a single numeric change proposed by the model and revalidated by the deterministic engine."""  # noqa: DOCSTRING_CJK

    metric_id: str
    delta: int
    criterion: str
    evidence: str

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        metric_schema: Mapping[str, Any],
    ) -> "MetricChangeV2":
        if set(value) != {"metric_id", "delta", "criterion", "evidence"}:
            raise NumericV2RuntimeError("metric_change_fields_invalid")
        metric_id = _stable_id(value.get("metric_id"), "metric_id")
        if metric_id not in metric_schema:
            raise NumericV2RuntimeError("metric_change_unknown_metric")
        delta = _integer(value.get("delta"), "metric_delta")
        if delta == 0:
            raise NumericV2RuntimeError("metric_delta_zero")
        definition = metric_schema[metric_id]
        direction = "increase" if delta > 0 else "decrease"
        limit = int(definition["per_turn_limit"][direction])
        if abs(delta) > limit:
            raise NumericV2RuntimeError("metric_delta_limit_exceeded")
        criterion = str(value.get("criterion") or "").strip()
        evidence = str(value.get("evidence") or "").strip()
        if not criterion or not evidence:
            raise NumericV2RuntimeError("metric_change_reason_required")
        # Evaluator may use only author-declared evidence, never extend numeric rules through free text.
        allowed_criteria = definition[f"{direction}_criteria"]
        if criterion not in allowed_criteria:
            raise NumericV2RuntimeError("metric_change_criterion_invalid")
        return cls(metric_id, delta, criterion, evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "delta": self.delta,
            "criterion": self.criterion,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class TurnRequestV2:
    client_turn_id: str
    base_revision: int
    message: str
    # reinvite is an explicit control operation; other sources describe normal-input UI origins only.
    input_source: str = "freeform"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TurnRequestV2":
        request = cls(
            client_turn_id=_stable_id(value.get("client_turn_id"), "client_turn_id"),
            base_revision=_integer(value.get("base_revision"), "base_revision"),
            message=str(value.get("message") or "").strip(),
            input_source=str(value.get("input_source") or "freeform").strip(),
        )
        if (
            request.base_revision < 0
            or (not request.message and request.input_source != "reinvite")
            or request.input_source not in NUMERIC_V2_INPUT_SOURCES
        ):
            raise NumericV2RuntimeError("numeric_turn_request_invalid")
        if truncate_to_tokens(request.message, NUMERIC_V2_PLAYER_INPUT_MAX_TOKENS) != request.message:
            raise NumericV2RuntimeError("numeric_turn_input_too_long")
        return request


@dataclass(frozen=True, slots=True)
class ScriptSessionV2:
    session_id: str
    story_package_id: str
    story_package_revision: str
    story_package_hash: str
    catgirl_binding: dict[str, str]
    current_node_id: str
    metrics: dict[str, int]
    node_turn_count: int
    revision: int
    status: str
    processed_client_turn_ids: tuple[str, ...]
    opening_performance: dict[str, Any]
    performance_history: tuple[dict[str, Any], ...]
    # Story state stores only Runtime-verifiable event facts; current_node_id remains the sole location authority.
    story_state: dict[str, Any]
    # Budget tier belongs to Session snapshots; continuation retains it, and only restart allows reselection.
    actor_budget_profile: str = NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE
    # Performance revision counts formal turns only; ending/continuation use separate versions to prevent late requests overwriting each other.
    lifecycle_revision: int = 0
    player_address_known: bool = False
    ended_reason: str | None = None
    # Explicit forgetting stops future memory/cold-archive projections without deleting Session history required for continuation.
    forgotten_through_revision: int = -1
    # Speech capability is deterministic Session state; sleep, muteness, or shutdown cannot be guessed ad hoc from free text.
    dialogue_policy: str = "required"
    # Latch true only when Actor explicitly proposes a concrete next step in committed text.
    transition_offered: bool = False
    # Recorded by the live runtime, never inferred from a later archive/replay.
    opening_performed_at: str = ""
    # Absence means legacy saves recorded no narrative state; never invent it from the author's opening.
    narrative_state: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SESSION_SCHEMA,
            "session_id": self.session_id,
            "story_package_id": self.story_package_id,
            "story_package_revision": self.story_package_revision,
            "story_package_hash": self.story_package_hash,
            "catgirl_binding": deepcopy(self.catgirl_binding),
            "current_node_id": self.current_node_id,
            "metrics": dict(self.metrics),
            "node_turn_count": self.node_turn_count,
            "revision": self.revision,
            "status": self.status,
            "processed_client_turn_ids": list(self.processed_client_turn_ids),
            "opening_performance": deepcopy(self.opening_performance),
            "performance_history": deepcopy(list(self.performance_history)),
            "story_state": deepcopy(self.story_state),
            "actor_budget_profile": self.actor_budget_profile,
            "lifecycle_revision": self.lifecycle_revision,
            "player_address_known": self.player_address_known,
            "ended_reason": self.ended_reason,
            "forgotten_through_revision": self.forgotten_through_revision,
            "dialogue_policy": self.dialogue_policy,
            "transition_offered": self.transition_offered,
            "opening_performed_at": self.opening_performed_at,
            **({"narrative_state": deepcopy(self.narrative_state)}
               if self.narrative_state is not None else {}),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScriptSessionV2":
        if value.get("schema") != SESSION_SCHEMA:
            raise NumericV2RuntimeError("numeric_session_schema_invalid")
        if any(
            key in value
            for key in (
                "scene_completion_ready",
                "scene_goal_evidence",
                "in_progress_goal_evidence",
                "continuity_goal_evidence",
                "evidence_chain_version",
            )
        ):
            # Old evidence-chain saves are removed; reimport requires author upgrade to v2.2, never implicit migration.
            raise NumericV2RuntimeError("numeric_v2_legacy_session_unsupported")
        raw_player_address_known = value.get("player_address_known", False)
        if not isinstance(raw_player_address_known, bool):
            raise NumericV2RuntimeError("session_player_address_known_invalid")
        actor_budget_profile = str(
            value.get(
                "actor_budget_profile",
                NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE,
            )
            or ""
        )
        if actor_budget_profile not in NUMERIC_V2_ACTOR_BUDGET_PROFILES:
            raise NumericV2RuntimeError("numeric_actor_budget_profile_invalid")
        session_revision = _integer(value.get("revision"), "revision")
        story_state = _validate_story_state(value.get("story_state"))
        if story_state["revision"] != session_revision:
            raise NumericV2RuntimeError("story_state_revision_mismatch")
        narrative_state = value.get("narrative_state")
        if narrative_state is not None:
            narrative_state = validate_narrative_state(narrative_state, revision=session_revision)
        return cls(
            session_id=_stable_id(value.get("session_id"), "session_id"),
            story_package_id=_stable_id(value.get("story_package_id"), "story_package_id"),
            story_package_revision=str(value.get("story_package_revision") or ""),
            story_package_hash=str(value.get("story_package_hash") or ""),
            catgirl_binding=deepcopy(dict(value.get("catgirl_binding") or {})),
            current_node_id=_stable_id(value.get("current_node_id"), "current_node_id"),
            metrics={str(key): _integer(item, "session_metric") for key, item in dict(value.get("metrics") or {}).items()},
            node_turn_count=_integer(value.get("node_turn_count"), "node_turn_count"),
            revision=session_revision,
            status=str(value.get("status") or ""),
            processed_client_turn_ids=tuple(str(item) for item in value.get("processed_client_turn_ids") or []),
            opening_performance=deepcopy(dict(value.get("opening_performance") or {})),
            performance_history=tuple(deepcopy(list(value.get("performance_history") or []))),
            opening_performed_at=str(value.get("opening_performed_at") or ""),
            story_state=story_state,
            actor_budget_profile=actor_budget_profile,
            lifecycle_revision=_integer(value.get("lifecycle_revision", 0), "lifecycle_revision"),
            player_address_known=raw_player_address_known,
            ended_reason=(str(value.get("ended_reason")) if value.get("ended_reason") is not None else None),
            forgotten_through_revision=_integer(
                value.get("forgotten_through_revision", -1),
                "forgotten_through_revision",
            ),
            dialogue_policy=_dialogue_policy(value.get("dialogue_policy")),
            transition_offered=_transition_offered(value),
            narrative_state=narrative_state,
        )


@dataclass(frozen=True, slots=True)
class TurnOutcomeV2:
    session: ScriptSessionV2
    ledger_event: dict[str, Any]
    metric_changes: tuple[MetricChangeV2, ...]
    route: dict[str, Any] | None
    route_status: str
    transition_contract: dict[str, Any] | None


class NumericV2Engine:
    """Apply v2.2 numeric, author-route, and transition rules only to the candidate Session."""  # noqa: DOCSTRING_CJK

    def __init__(self, compiled: CompiledNumericV2Package):
        # Prevent callers bypassing from_mapping/registry by injecting old compiled contracts directly into runtime.
        meta = compiled.story.get("meta")
        if not isinstance(meta, Mapping) or meta.get("contract_version") != "v2.2":
            raise NumericV2RuntimeError("numeric_v2_upgrade_required")
        self.compiled = compiled
        self.story = compiled.story
        self.nodes = {str(node["id"]): node for node in self.story["nodes"]}
        self.metric_schema = self.story["metric_schema"]
        self.fact_contract = _story_fact_contract(self.story.get("fact_contract"))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NumericV2Engine":
        compiler = NumericV2Compiler()
        meta = value.get("meta")
        # Runtime accepts v2.2 only; authors must upgrade old packages, which cannot bypass the registry for direct loading.
        contract_version = meta.get("contract_version") if isinstance(meta, Mapping) else None
        if contract_version != "v2.2":
            raise NumericV2RuntimeError("numeric_v2_upgrade_required")
        compiled = compiler.compile_v2_2(value)
        return cls(compiled)

    @property
    def story_id(self) -> str:
        return self.compiled.story_id

    def finalize_actor_fact_candidates(
        self,
        base_session: ScriptSessionV2,
        outcome: TurnOutcomeV2,
        *,
        candidates: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
        evidence_sources: Mapping[str, str],
    ) -> tuple[TurnOutcomeV2, tuple[dict[str, Any], ...]]:
        """Merge final Actor candidates with Evaluator facts, then rebuild atomically at the same revision."""  # noqa: DOCSTRING_CJK

        node = self.nodes.get(base_session.current_node_id)
        completion_contract = node.get("completion_contract") if isinstance(node, Mapping) else None
        allowed_keys = {
            str(requirement.get("key") or "")
            for requirement in (
                completion_contract.get("all")
                if isinstance(completion_contract, Mapping)
                else ()
            )
            if isinstance(requirement, Mapping)
        }
        normalized_candidates = _normalize_actor_fact_candidates(
            candidates,
            fact_contract=self.fact_contract,
            allowed_keys=allowed_keys,
        )
        actor_operations, audit = validate_fact_candidates(
            normalized_candidates,
            fact_contract={"facts": self.fact_contract},
            evidence_sources=evidence_sources,
        )
        return self.finalize_fact_operations(base_session, outcome, operations=actor_operations), audit

    def finalize_fact_operations(
        self,
        base_session: ScriptSessionV2,
        outcome: TurnOutcomeV2,
        *,
        operations: tuple[Mapping[str, Any], ...],
    ) -> TurnOutcomeV2:
        """Merge verified operations and rebuild facts and visit records at the same revision without duplicate scoring or act changes."""  # noqa: DOCSTRING_CJK

        self.validate_session(base_session)
        event = outcome.ledger_event
        if (
            event.get("base_revision") != base_session.revision
            or event.get("result_revision") != base_session.revision + 1
            or event.get("client_turn_id") not in outcome.session.processed_client_turn_ids
        ):
            raise NumericV2RuntimeError("actor_fact_outcome_mismatch")
        existing_operations = tuple(event.get("fact_operations") or ())
        existing_keys = {
            str(operation.get("key") or "")
            for operation in existing_operations
            if isinstance(operation, Mapping)
        }
        actor_keys = {str(operation["key"]) for operation in operations}
        if existing_keys.intersection(actor_keys):
            raise NumericV2RuntimeError("actor_fact_candidate_duplicate_key")
        combined_operations = (*existing_operations, *operations)
        story_state = _advance_story_state(
            base_session.story_state,
            from_node_id=str(event["from_node_id"]),
            to_node_id=str(event["to_node_id"]),
            revision=int(event["result_revision"]),
            client_turn_id=str(event["client_turn_id"]),
            fact_operations=combined_operations,
            fact_contract={"facts": self.fact_contract},
        )
        ledger_event = dict(event)
        ledger_event["fact_operations"] = deepcopy(
            [dict(operation) for operation in combined_operations]
        )
        ledger_event["player_action_projection"] = project_player_action_result(
            str(event["input_text"]),
            revision=int(event["result_revision"]),
            transition_intent=str(event["transition_intent"]),
            route_changed=event["from_node_id"] != event["to_node_id"],
            fact_operations=combined_operations,
        )
        return replace(
            outcome,
            session=replace(outcome.session, story_state=story_state),
            ledger_event=ledger_event,
        )

    @staticmethod
    def _node_dialogue_policy(node: Mapping[str, Any], fallback: str) -> str:
        beat = node.get("story_beat")
        contract = beat.get("acting_contract") if isinstance(beat, Mapping) else None
        value = contract.get("dialogue_policy") if isinstance(contract, Mapping) else None
        return _dialogue_policy(value, default=fallback)

    def create_session(
        self,
        *,
        session_id: str,
        catgirl_binding: Mapping[str, Any],
        opening_performance: Mapping[str, Any],
        actor_budget_profile: str = NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE,
    ) -> ScriptSessionV2:
        if actor_budget_profile not in NUMERIC_V2_ACTOR_BUDGET_PROFILES:
            raise NumericV2RuntimeError("numeric_actor_budget_profile_invalid")
        generated_opening = add_entry(
            self.nodes[str(self.story["start_node_id"])],
            {key: value for key, value in opening_performance.items() if key != "fixed_narrations"},
            catgirl_binding, bool(self.story["initial_state"]["player_address_known"]),
        )
        # Public creation accepts only our current entry projection, never caller
        # claims about condition triggers or different display-name bindings.
        if ("fixed_narrations" in opening_performance
                and opening_performance["fixed_narrations"] != generated_opening.get("fixed_narrations", [])):
            raise NumericV2RuntimeError("numeric_fixed_narration_invalid")
        validate_delivery(self.story, generated_opening)
        narrative_state = None
        if "narrative_record" in generated_opening:
            narrative_state = apply_narrative_record(
                None, generated_opening["narrative_record"], revision=0,
                node_id=str(self.story["start_node_id"]),
                from_node_id=str(self.story["start_node_id"]),
                performance=generated_opening, message="",
            )
        return ScriptSessionV2(
            session_id=_stable_id(session_id, "session_id"),
            story_package_id=self.story_id,
            story_package_revision=str(self.story["meta"]["revision"]),
            story_package_hash=self.compiled.package_hash,
            catgirl_binding={str(key): str(item) for key, item in catgirl_binding.items()},
            current_node_id=str(self.story["start_node_id"]),
            metrics={str(key): int(item) for key, item in self.story["initial_state"]["metrics"].items()},
            node_turn_count=0,
            revision=0,
            status="active",
            processed_client_turn_ids=(),
            opening_performance=generated_opening,
            performance_history=(),
            story_state=_initial_story_state(str(self.story["start_node_id"])),
            narrative_state=narrative_state,
            actor_budget_profile=actor_budget_profile,
            player_address_known=bool(self.story["initial_state"]["player_address_known"]),
            dialogue_policy=self._node_dialogue_policy(
                self.nodes[str(self.story["start_node_id"])],
                "required",
            ),
        )

    def validate_session(self, session: ScriptSessionV2) -> None:
        if session.story_package_id != self.story_id:
            raise NumericV2RuntimeError("story_package_id_mismatch")
        if session.story_package_revision != str(self.story["meta"]["revision"]):
            raise NumericV2RuntimeError("story_package_revision_mismatch")
        if session.story_package_hash != self.compiled.package_hash:
            raise NumericV2RuntimeError("story_package_hash_mismatch")
        if session.current_node_id not in self.nodes:
            raise NumericV2RuntimeError("session_current_node_missing")
        story_state = _validate_story_state(session.story_state)
        if story_state["revision"] != session.revision:
            raise NumericV2RuntimeError("story_state_revision_mismatch")
        if session.narrative_state is not None:
            narrative_state = validate_narrative_state(session.narrative_state)
            # Undelivered outcomes already advanced turn numbers; narrative views still start from committed state.
            if narrative_state["revision"] > session.revision:
                raise NumericV2RuntimeError("narrative_state_revision_mismatch")
        if session.status not in {"active", "ended"}:
            raise NumericV2RuntimeError("session_status_invalid")
        if session.actor_budget_profile not in NUMERIC_V2_ACTOR_BUDGET_PROFILES:
            raise NumericV2RuntimeError("numeric_actor_budget_profile_invalid")
        if set(session.metrics) != set(self.metric_schema):
            raise NumericV2RuntimeError("session_metrics_mismatch")
        for metric_id, value in session.metrics.items():
            definition = self.metric_schema[metric_id]
            if not definition["min"] <= value <= definition["max"]:
                raise NumericV2RuntimeError("session_metric_out_of_range")
        if session.node_turn_count < 0 or session.revision < 0 or session.lifecycle_revision < 0:
            raise NumericV2RuntimeError("session_counter_invalid")
        if not -1 <= session.forgotten_through_revision <= session.revision:
            raise NumericV2RuntimeError("session_forgotten_revision_invalid")
        if not isinstance(session.player_address_known, bool):
            raise NumericV2RuntimeError("session_player_address_known_invalid")
        if not isinstance(session.transition_offered, bool):
            raise NumericV2RuntimeError("session_transition_offered_invalid")
        _dialogue_policy(session.dialogue_policy)

    def resolve_turn(
        self,
        session: ScriptSessionV2,
        request: TurnRequestV2,
        changes: tuple[MetricChangeV2, ...],
        *,
        scene_complete: bool = False,
        transition_intent: str = "unclear",
        natural_ending_ready: bool = False,
        fact_operations: tuple[Mapping[str, Any], ...] = (),
        ledger_events: tuple[Mapping[str, Any], ...] = (),
        condition_narrations_enabled: bool = True,
    ) -> TurnOutcomeV2:
        """Settle a v2.2 turn; targets, evidence, and completion latches no longer enter the state machine."""  # noqa: DOCSTRING_CJK

        self.validate_session(session)
        if not isinstance(condition_narrations_enabled, bool):
            raise NumericV2RuntimeError("condition_narrations_enabled_invalid")
        if session.status == "ended":
            raise NumericV2RuntimeError("session_already_ended")
        if request.base_revision != session.revision:
            raise NumericV2RevisionConflictError("base_revision_mismatch")
        if request.client_turn_id in session.processed_client_turn_ids:
            raise NumericV2DuplicateTurnError("duplicate_client_turn_id")
        if len({change.metric_id for change in changes}) != len(changes):
            raise NumericV2RuntimeError("metric_change_duplicate")
        if transition_intent not in {"accept", "initiate", "reject", "unclear"}:
            raise NumericV2RuntimeError("transition_intent_invalid")
        reinvitation = request.input_source == "reinvite"
        if reinvitation and (changes or fact_operations or scene_complete or natural_ending_ready or transition_intent != "unclear"):
            raise NumericV2RuntimeError("numeric_reinvitation_not_available")
        # Reaccept only invitations published during this scene visit; add no state, and evaluate the same history on cold recovery.
        offered_record = pending_transition_record(
            session, ledger_events=ledger_events, include_withdrawn=True,
        )
        can_accept_offer = session.transition_offered or (
            transition_intent == "accept"
            and offered_record is not None
        )
        effective_transition_intent = transition_intent
        if (
            transition_intent in {"accept", "reject"}
            and not (can_accept_offer if transition_intent == "accept" else session.transition_offered)
        ):
            effective_transition_intent = "unclear"

        before = dict(session.metrics)
        after = dict(before)
        applied: list[dict[str, Any]] = []
        for change in changes:
            definition = self.metric_schema[change.metric_id]
            next_value = max(definition["min"], min(definition["max"], after[change.metric_id] + change.delta))
            applied.append({**change.to_dict(), "before": after[change.metric_id], "after": next_value})
            after[change.metric_id] = next_value

        source = self.nodes[session.current_node_id]
        next_turn_count = session.node_turn_count if reinvitation else session.node_turn_count + 1
        route = None
        route_status = "playing"
        accepted_offer_route_id = None
        offer_route_changed = False
        if effective_transition_intent == "initiate":
            # Players may explicitly request a published next stage, without fabricating invitations or bypassing this turn's numeric routing.
            # Evaluator recognizes explicit requests; formal-transition review checks public destinations/authorization, still refusing invalid bodies.
            route, route_status = self._select_route(source, after)
        elif effective_transition_intent == "accept" and can_accept_offer:
            # Active/reaccepted historical invitations share routing conditions; author goals or unsupported model accept cannot transition acts.
            route, route_status = self._select_route(source, after)
            # Acceptance concerns the original invitation, not a follow-up question or another exit selected by this turn's score increase.
            # Read invitation-time numeric values from existing Ledger, without separate drift-prone pending-route state.
            offered_event = next((event for event in ledger_events
                if offered_record is not None
                and event.get("result_revision") == offered_record.get("revision")), None)
            if offered_event is not None:
                offered_route, _ = self._select_route(source, offered_event["after_metrics"])
                accepted_offer_route_id = str(offered_route["id"]) if offered_route else ""
                offer_route_changed = route is None or route["id"] != accepted_offer_route_id
                if offer_route_changed:
                    route, route_status = None, "playing"
            if route is None:
                # If the invitation route remains unreachable, stay in the current act rather than fabricate advanced.
                route_status = "playing" if offer_route_changed else "transition_offered"
        elif session.transition_offered and effective_transition_intent == "unclear":
            # unclear preserves proposals; next turn responds without repeated urging.
            route_status = "transition_offered"
        elif effective_transition_intent == "reject":
            # reject clears the current proposal; Actor may propose anew after new causal developments.
            route_status = "playing"

        # Natural ending allows only endings ready to conclude this turn, never scene_complete-driven automatic advancement of ordinary acts.
        # Retain route priorities; if a normal act wins, do not skip it to find an ending.
        if route is None and not offer_route_changed and scene_complete and natural_ending_ready is True and effective_transition_intent != "reject":
            ending_route, _ = self._select_route(source, after)
            # Evaluation sees the presettlement candidate ending; numeric changes selecting another exit cannot reuse its authorization.
            preview_route, _ = self._select_route(source, before)
            if ending_route is not None and preview_route is not None and ending_route["id"] == preview_route["id"]:
                ending_target = self.nodes[str(ending_route["target_node_id"])]
                if ending_target.get("type") == "ending" or ending_target.get("terminal") is True:
                    route = ending_route

        # Only explicitly required immutable pieces gate departure; ordinary goals
        # remain optional creative material and keep their existing semantics.
        # Conditional segments can be delivered only through review-module trigger declarations; with the module disabled they cannot appear,
        # so must not keep exits locked. Record the switch in Ledger and replay its original decision.
        if route is not None and required_pending(
            source, session, condition_triggers_enabled=condition_narrations_enabled,
        ):
            route, route_status = None, "playing"
        target_node_id = session.current_node_id
        next_status = "active"
        transition = None
        if route is not None:
            target_node_id = str(route["target_node_id"])
            target = self.nodes[target_node_id]
            next_turn_count = 0
            next_status = "ended" if target.get("type") == "ending" or target.get("terminal") is True else "active"
            transition = deepcopy(dict(route["transition_contract"]))
            route_status = "advanced"

        player_address_known = session.player_address_known if reinvitation else _player_address_known_after_turn(session, request.message)
        dialogue_policy = session.dialogue_policy
        if route is not None:
            dialogue_policy = self._node_dialogue_policy(
                self.nodes[target_node_id],
                dialogue_policy,
            )

        revision = session.revision + 1
        story_state = _advance_story_state(
            session.story_state,
            from_node_id=session.current_node_id,
            to_node_id=target_node_id,
            revision=revision,
            client_turn_id=request.client_turn_id,
            fact_operations=fact_operations,
            fact_contract={"facts": self.fact_contract},
        )
        next_session = replace(
            session,
            current_node_id=target_node_id,
            metrics=after,
            node_turn_count=next_turn_count,
            revision=revision,
            story_state=story_state,
            status=next_status,
            processed_client_turn_ids=(*session.processed_client_turn_ids, request.client_turn_id),
            player_address_known=player_address_known,
            dialogue_policy=dialogue_policy,
            # This turn's Actor body is not generated yet; acceptance, rejection, or formal transition clears old proposals first,
            # while unclear alone retains them for further responses next turn.
            transition_offered=(
                session.transition_offered
                if route is None and effective_transition_intent == "unclear"
                else False
            ),
        )
        event = {
            "schema": LEDGER_EVENT_SCHEMA,
            "event_id": f"event_{session.session_id}_{revision}",
            "session_id": session.session_id,
            "client_turn_id": request.client_turn_id,
            "base_revision": session.revision,
            "result_revision": revision,
            "input_text": request.message,
            "metric_changes": applied,
            "before_metrics": before,
            "after_metrics": after,
            "from_node_id": session.current_node_id,
            "to_node_id": target_node_id,
            "route_id": route.get("id") if route else None,
            "route_status": route_status,
            "scene_complete": scene_complete,
            "node_turn_count": next_turn_count,
            "status": next_status,
            "player_address_known": player_address_known,
            "before_dialogue_policy": session.dialogue_policy,
            # Evaluator/node overrides apply before Actor; state effects from Actor's exact delivery affect only the next turn.
            # Latch the speech contract seen during body generation separately, preventing later updated state from retroactively rejecting valid dialogue.
            "performance_dialogue_policy": dialogue_policy,
            "dialogue_policy": dialogue_policy,
            # After body validation, finalize_transition_offer_state centrally latches new proposals;
            # record Evaluator's preserve/clear decision for old proposals here first.
            "transition_offered": (
                session.transition_offered
                if route is None and effective_transition_intent == "unclear"
                else False
            ),
            # Version 2 determines address knowledge from complete nickname + explicit disclosure patterns; legacy events replay committed facts compatibly.
            "player_address_disclosure_version": 2,
        }
        # Generate this projection only from current input and Runtime-confirmed route/fact outcomes; Actor, clients,
        # and author goals cannot retroactively write completed player actions.
        event["player_action_projection"] = project_player_action_result(
            request.message,
            revision=revision,
            transition_intent=effective_transition_intent,
            route_changed=route is not None,
            fact_operations=fact_operations,
        )
        event["transition_intent"] = effective_transition_intent
        if reinvitation:
            event["input_source"] = "reinvite"
            event["input_text"] = ""
            event["player_action_projection"] = normalize_player_action_projection({})
        event["visit_started_revision"] = revision if route is not None else current_visit_started_revision(session)
        if accepted_offer_route_id is not None:
            # Also marks audit/fork replay boundaries; old turns without this field retain old routing rules.
            event["accepted_offer_route_id"] = accepted_offer_route_id
        if route is not None and accepted_offer_route_id is not None:
            receipt = offered_event.get("program_invitation") if offered_event else None
            if (isinstance(receipt, Mapping) and receipt.get("silent") is True
                    and receipt.get("route_id") == accepted_offer_route_id
                    and receipt.get("revision") == session.revision
                    and receipt.get("offer") == ""
                    and request.input_source == "suggestion"
                    and request.message.strip() == receipt.get("accept_input")):
                # This receipt constrains only the departing scene, never Session policy.
                event["source_transition_dialogue_policy"] = "forbidden"
                event["input_source"] = request.input_source
        if offer_route_changed:
            event["transition_offer_invalidated"] = True
        if fact_operations:
            # Validate candidate facts before commit; Ledger stores normalized operations for deterministic replay.
            event["fact_operations"] = deepcopy([dict(operation) for operation in fact_operations])
        # Record positive new signals only; legacy Ledger defaults false, preventing fork replay from ending old history early.
        if natural_ending_ready is True:
            event["natural_ending_ready"] = True
        # Record disabled values only; legacy Ledger defaults enabled, retaining original act-exit thresholds on replay.
        if condition_narrations_enabled is False:
            event["condition_narrations_enabled"] = False
        return TurnOutcomeV2(next_session, event, changes, route, route_status, transition)

    def finalize_transition_offer_state(
        self,
        outcome: TurnOutcomeV2,
        performance: Mapping[str, Any],
        *,
        new_offer: bool,
        invalidate_previous_offer: bool = False,
    ) -> tuple[TurnOutcomeV2, dict[str, Any]]:
        """Let Runtime alone latch this turn's public proposal and synchronize Session, Ledger, and narrative text."""  # noqa: DOCSTRING_CJK

        if not isinstance(new_offer, bool) or not isinstance(invalidate_previous_offer, bool):
            raise NumericV2RuntimeError("transition_offered_invalid")
        # May run before revision and again before commit; later calls must retain withdrawal boundaries already confirmed in the candidate.
        invalidate_previous_offer = invalidate_previous_offer or outcome.ledger_event.get("transition_offer_invalidated") is True
        transition_offered = new_offer or (outcome.session.transition_offered and not invalidate_previous_offer)
        finalized_performance = {
            **performance,
            "transition_offered": transition_offered,
        }
        # Only Runtime routing drift or explicit Workflow review may withdraw old invitations; Actor cannot inject historical boundaries.
        finalized_performance.pop("transition_offer_invalidated", None)
        # Distinguish newly republished valid invitations from retained old ones; reply binding cannot rely solely on the final boolean.
        finalized_performance.pop("transition_offer_presented", None)
        ledger_event = {**outcome.ledger_event, "transition_offered": transition_offered}
        if new_offer:
            finalized_performance["transition_offer_presented"] = True
            ledger_event["transition_offer_presented"] = True
        if invalidate_previous_offer:
            finalized_performance["transition_offer_invalidated"] = True
            ledger_event["transition_offer_invalidated"] = True
        finalized_outcome = replace(
            outcome,
            session=replace(
                outcome.session,
                transition_offered=transition_offered,
            ),
            ledger_event=ledger_event,
        )
        return finalized_outcome, finalized_performance

    def finalize_transition_performance(
        self,
        outcome: TurnOutcomeV2,
        performance: Mapping[str, Any],
        *,
        target_opening: str,
        bridge_required: bool = False,
        bridge_scene_narration: str = "",
        source_dialogue_policy: str = "required",
        target_dialogue_policy: str = "required",
    ) -> dict[str, Any]:
        """Fix the three-part commit structure; new narration continues history, while legacy array calls retain the original assembly protocol."""  # noqa: DOCSTRING_CJK

        target_node_id = str(outcome.ledger_event["to_node_id"])
        if outcome.ledger_event["from_node_id"] == target_node_id:
            return deepcopy(dict(performance))
        result = deepcopy(dict(performance))
        segments = result.get("segments")
        authored_bridge = str(bridge_scene_narration or "").strip()
        if {
            "source_performance",
            "target_performance",
        }.issubset(result):
            # Both narration segments must come from the same generation; author text constrains facts without overwriting history-adapted output.
            # Compact primary flow and three-segment validation below share bridge permissions; target narration must always be nonempty.
            if (
                not valid_scene_narration(
                    {"scene_narration": result.get("bridge_scene_narration")},
                    allow_empty=not bridge_required,
                )
                or not valid_scene_narration({"scene_narration": result.get("target_scene_narration")})
            ):
                raise NumericV2RuntimeError("numeric_transition_performance_invalid")
            authored_bridge = result.pop("bridge_scene_narration")
            target_opening = result.pop("target_scene_narration")
            segments = [
                {
                    "phase": "source_response",
                    "performance": str(result.pop("source_performance") or "").strip(),
                },
                {
                    "phase": "transition_bridge",
                    "scene_narration": authored_bridge,
                },
                {
                    "phase": "target_opening",
                    "performance": str(result.pop("target_performance") or "").strip(),
                },
            ]
            # Optional source narration remains segment one in the same commit, never moved to post-transition bridges or catgirl dialogue.
            if "source_scene_narration" in result:
                segments[0]["scene_narration"] = result.pop("source_scene_narration")
            result["segments"] = segments
        if (
            authored_bridge
            and isinstance(segments, list)
            and len(segments) == 3
            and isinstance(segments[1], Mapping)
        ):
            # Compact output already selected generated narration above; retain original assembly for legacy three-segment array callers too.
            segments[1] = {
                "phase": "transition_bridge",
                "scene_narration": authored_bridge,
            }
        if (
            not valid_scene_narration({"scene_narration": target_opening})
            or not isinstance(segments, list)
            or len(segments) != 3
            or not all(isinstance(item, Mapping) for item in segments)
            or [item.get("phase") for item in segments]
            != ["source_response", "transition_bridge", "target_opening"]
            or set(segments[0]).difference({"fixed_narrations"}) not in ({"phase", "performance"}, {"phase", "performance", "scene_narration"})
            or ("scene_narration" in segments[0] and not valid_scene_narration(segments[0]))
            or set(segments[1]) != {"phase", "scene_narration"}
            or set(segments[2]) != {"phase", "performance"}
            or not valid_mixed_performance_policy(
                segments[0], source_dialogue_policy
            )
            # Bridges must remain visible when contracts contain independent time/location/environment facts; allow empty only for same-scene continuity without independent facts.
            or not valid_scene_narration(segments[1], allow_empty=not bridge_required)
            or not valid_mixed_performance_policy(
                segments[2], target_dialogue_policy
            )
        ):
            raise NumericV2RuntimeError("numeric_transition_performance_invalid")
        result["segments"] = [
            deepcopy(dict(segments[0])),
            deepcopy(dict(segments[1])),
            {
                "phase": "target_opening",
                "scene_narration": target_opening.strip(),
                "performance": str(segments[2]["performance"]).strip(),
            },
        ]
        result["transition_delivered"] = True
        result["visible_node_id"] = target_node_id
        result["segments"][2] = add_entry(
            self.nodes[target_node_id], result["segments"][2], outcome.session.catgirl_binding,
            outcome.session.player_address_known, session=outcome.session,
        )
        return result

    def _select_route(self, node: Mapping[str, Any], metrics: Mapping[str, int]) -> tuple[dict[str, Any] | None, str]:
        eligible = [route for route in node.get("route_gates", []) if self._conditions_match(route["conditions"], metrics)]
        if not eligible:
            return None, "conditions_blocked"
        highest = max(int(route["priority"]) for route in eligible)
        winners = [route for route in eligible if int(route["priority"]) == highest]
        if len(winners) != 1:
            raise NumericV2RuntimeError("route_priority_tie")
        return deepcopy(dict(winners[0])), "eligible"

    def preview_route(
        self,
        node_id: str,
        metrics: Mapping[str, int],
    ) -> dict[str, Any] | None:
        """Return, read-only, the route Runtime would select at the current numeric values without changing Session."""  # noqa: DOCSTRING_CJK

        node = self.nodes.get(str(node_id))
        if node is None:
            raise NumericV2RuntimeError("node_not_found")
        route, _status = self._select_route(node, metrics)
        return route

    def completion_contract_satisfied(self, session: ScriptSessionV2) -> bool | None:
        """Check the current act's author completion condition read-only; return None if undeclared."""  # noqa: DOCSTRING_CJK

        self.validate_session(session)
        node = self.nodes[session.current_node_id]
        contract = node.get("completion_contract")
        if not isinstance(contract, Mapping):
            return None
        story_state = _validate_story_state(session.story_state)
        facts = story_state["facts"]
        displayed = (displayed_ids(session)
                     if any("fixed_narration_id" in row for row in contract["all"]) else set())
        return all(
            (session.current_node_id, requirement["fixed_narration_id"]) in displayed
            if "fixed_narration_id" in requirement else (
                isinstance(facts.get(str(requirement["key"])), Mapping)
                and facts[str(requirement["key"])]["value"] == requirement["equals"]
            )
            for requirement in contract["all"]
        )

    @staticmethod
    def _conditions_match(conditions: Mapping[str, Any], metrics: Mapping[str, int]) -> bool:
        mode = "any" if "any" in conditions else "all"
        rows = list(conditions.get(mode) or [])
        checks = [
            _COMPARATORS[str(row["op"])](metrics[str(row["metric"])], int(row["value"]))
            for row in rows
        ]
        return any(checks) if mode == "any" else all(checks)


class NumericV2Runtime:
    """Combine the v2 Engine with an independent persistence directory."""  # noqa: DOCSTRING_CJK

    def __init__(self, engine: NumericV2Engine, root: Path, *, write_transaction=None):
        self.engine = engine
        self.store = NumericV2SessionStore(Path(root), engine)
        if write_transaction is not None:
            self.store.write_transaction = write_transaction

    async def start_session(
        self,
        *,
        session_id: str,
        catgirl_binding: Mapping[str, Any],
        opening_performance: Mapping[str, Any],
        actor_budget_profile: str = NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE,
    ) -> NumericV2StoredSession:
        session = self.engine.create_session(
            session_id=session_id,
            catgirl_binding=catgirl_binding,
            opening_performance=opening_performance,
            actor_budget_profile=actor_budget_profile,
        )
        session = replace(session, opening_performed_at=datetime.now().astimezone().isoformat())
        return await self.store.create_story_session(session)

    async def restore_story_session(
        self,
        catgirl_binding: Mapping[str, Any],
    ) -> NumericV2StoredSession | None:
        stored = await self.store.restore_story_session(
            self.engine.story_id,
            str(catgirl_binding.get("character_id") or ""),
            str(catgirl_binding.get("catgirl_name") or ""),
        )
        return stored

    @asynccontextmanager
    async def story_session_guard(self):
        async with self.store.story_session_guard(self.engine.story_id):
            yield

    async def restore_story_session_unlocked(
        self,
        catgirl_binding: Mapping[str, Any],
    ) -> NumericV2StoredSession | None:
        stored = await self.store._restore_story_session_unlocked(
            self.engine.story_id,
            str(catgirl_binding.get("character_id") or ""),
            str(catgirl_binding.get("catgirl_name") or ""),
        )
        return stored

    async def replace_active_session(
        self,
        *,
        previous_session_id: str,
        session_id: str,
        catgirl_binding: Mapping[str, Any],
        opening_performance: Mapping[str, Any],
        actor_budget_profile: str = NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE,
    ) -> NumericV2StoredSession:
        session = self.engine.create_session(
            session_id=session_id,
            catgirl_binding=catgirl_binding,
            opening_performance=opening_performance,
            actor_budget_profile=actor_budget_profile,
        )
        session = replace(session, opening_performed_at=datetime.now().astimezone().isoformat())
        stored = await self.store.replace_active(previous_session_id, session)
        return stored

    async def restore_session(self, session_id: str) -> NumericV2StoredSession | None:
        return await self.store.load(session_id)

    async def restore_session_for_lifecycle(
        self,
        session_id: str,
    ) -> NumericV2StoredSession | None:
        """Read the save only to end an old performance; callers must not use it to continue generating plot."""  # noqa: DOCSTRING_CJK

        return await self.store.load_for_lifecycle(session_id)

    async def fork_session_for_test(
        self,
        source_session_id: str,
        *,
        session_id: str,
        through_revision: int,
    ) -> NumericV2StoredSession:
        """Create an isolated load-test fork from the given revision without overwriting the official script's continuation slot."""  # noqa: DOCSTRING_CJK

        source = await self.store.load(source_session_id)
        if source is None:
            raise NumericV2RuntimeError("numeric_source_session_not_found")
        if (
            isinstance(through_revision, bool)
            or not isinstance(through_revision, int)
            or not 0 <= through_revision <= source.session.revision
        ):
            raise NumericV2RuntimeError("numeric_fork_revision_invalid")

        replay_session = self.engine.create_session(
            session_id=session_id,
            catgirl_binding=source.session.catgirl_binding,
            opening_performance={key: value for key, value in source.session.opening_performance.items()
                                 if key != "narrative_record"},
            actor_budget_profile=source.session.actor_budget_profile,
        )
        narrative_state = None
        if "narrative_record" in source.session.opening_performance:
            narrative_state = apply_narrative_record(
                None, source.session.opening_performance["narrative_record"], revision=0,
                node_id=str(self.engine.story["start_node_id"]),
                from_node_id=str(self.engine.story["start_node_id"]),
                performance=source.session.opening_performance, message="",
            )
        replay_session = replace(
            replay_session, opening_performance=deepcopy(source.session.opening_performance),
            opening_performed_at=source.session.opening_performed_at, narrative_state=narrative_state,
        )
        replay_events: list[dict[str, Any]] = []
        for index, source_event in enumerate(source.ledger_events[:through_revision]):
            changes = tuple(
                MetricChangeV2.from_mapping(
                    {
                        key: change.get(key)
                        for key in ("metric_id", "delta", "criterion", "evidence")
                    },
                    self.engine.metric_schema,
                )
                for change in source_event.get("metric_changes") or []
                if isinstance(change, Mapping)
            )
            request = TurnRequestV2.from_mapping({
                "client_turn_id": source_event.get("client_turn_id"),
                "base_revision": replay_session.revision,
                "message": source_event.get("input_text"),
                "input_source": source_event.get("input_source", "freeform"),
            })
            outcome = self.engine.resolve_turn(
                replay_session,
                request,
                changes,
                scene_complete=bool(source_event.get("scene_complete")),
                transition_intent=str(source_event.get("transition_intent") or "unclear"),
                natural_ending_ready=source_event.get("natural_ending_ready") is True,
                ledger_events=tuple(replay_events) if "accepted_offer_route_id" in source_event else (),
                condition_narrations_enabled=source_event.get("condition_narrations_enabled") is not False,
                fact_operations=tuple(
                    dict(operation)
                    for operation in source_event.get("fact_operations") or []
                    if isinstance(operation, Mapping)
                ),
            )
            source_performance = deepcopy(
                dict(source.session.performance_history[index])
            )
            replayed_event = deepcopy(outcome.ledger_event)
            if isinstance(source_event.get("program_invitation"), Mapping):
                replayed_event["program_invitation"] = deepcopy(source_event["program_invitation"])
            if source_event.get("transition_offer_invalidated") is True:
                replayed_event["transition_offer_invalidated"] = True
            if source_event.get("transition_offer_presented") is True:
                replayed_event["transition_offer_presented"] = True
            if "transition_offer_source" in source_event:
                replayed_event["transition_offer_source"] = source_event["transition_offer_source"]
            replayed_session_after_turn = outcome.session
            if "transition_offered" in source_event:
                # Fork replay uses committed original proposal states rather than reinterpreting Actor results.
                committed_transition_offered = source_event.get("transition_offered")
                if not isinstance(committed_transition_offered, bool):
                    raise NumericV2RuntimeError("session_transition_offered_invalid")
                replayed_event["transition_offered"] = committed_transition_offered
                replayed_session_after_turn = replace(
                    outcome.session,
                    transition_offered=committed_transition_offered,
                )
            if (
                source_event.get("player_address_disclosure_version") is None
                and source_event.get("player_address_known") is True
                and outcome.session.player_address_known is False
            ):
                # Old Ledger committed knowledge upon nickname occurrence; source Session passed compatibility auditing,
                # so test forks retain that established state rather than silently rewrite history using new rules.
                replayed_event.pop("player_address_disclosure_version", None)
                replayed_event["player_address_known"] = True
                replayed_session_after_turn = replace(
                    outcome.session,
                    player_address_known=True,
                )
            if "narrative_record" in source_event:
                narrative_record = deepcopy(source_event["narrative_record"])
                replayed_event["narrative_record"] = narrative_record
                replayed_session_after_turn = replace(
                    replayed_session_after_turn,
                    narrative_state=apply_narrative_record(
                        replay_session.narrative_state, narrative_record,
                        revision=int(source_event["result_revision"]),
                        node_id=str(source_event["to_node_id"]),
                        from_node_id=str(source_event["from_node_id"]),
                        performance=source_performance, message=request.message,
                    ),
                )
            # Forks change only Session identity; preserve story text and formal per-turn input verbatim.
            source_performance.update({
                "schema": PERFORMANCE_RECORD_SCHEMA,
                "client_turn_id": request.client_turn_id,
                "revision": outcome.session.revision,
                "input_text": request.message,
                "from_node_id": outcome.ledger_event["from_node_id"],
                "to_node_id": outcome.ledger_event["to_node_id"],
            })
            replay_session = replace(
                replayed_session_after_turn,
                performance_history=(
                    *replayed_session_after_turn.performance_history,
                    source_performance,
                ),
            )
            replay_events.append(replayed_event)

        snapshot = NumericV2StoredSession(
            replay_session,
            tuple(replay_events),
        )
        return await self.store.create_isolated_snapshot(snapshot)

    def prepare_turn(
        self,
        current: NumericV2StoredSession,
        request: TurnRequestV2,
        changes: tuple[MetricChangeV2, ...],
        *,
        scene_complete: bool = False,
        transition_intent: str = "unclear",
        natural_ending_ready: bool = False,
        fact_operations: tuple[Mapping[str, Any], ...] = (),
        condition_narrations_enabled: bool = True,
    ) -> TurnOutcomeV2:
        return self.engine.resolve_turn(
            current.session,
            request,
            changes,
            scene_complete=scene_complete,
            transition_intent=transition_intent,
            natural_ending_ready=natural_ending_ready,
            ledger_events=current.ledger_events,
            fact_operations=fact_operations,
            condition_narrations_enabled=condition_narrations_enabled,
        )

    async def commit_turn(
        self,
        outcome: TurnOutcomeV2,
        performance: Mapping[str, Any],
    ) -> NumericV2StoredSession:
        validate_delivery(self.engine.story, performance, session=outcome.session,
                          node_id=str(outcome.ledger_event["from_node_id"]))
        route_changed = outcome.ledger_event["from_node_id"] != outcome.ledger_event["to_node_id"]
        if route_changed:
            segments = performance.get("segments")
            new_contract = (
                isinstance(segments, list)
                and bool(segments)
                and isinstance(segments[0], Mapping)
                and "performance" in segments[0]
            )
            if (
                performance.get("transition_delivered") is not True
                or performance.get("visible_node_id") != outcome.ledger_event["to_node_id"]
                or not isinstance(segments, list)
                or [item.get("phase") for item in segments if isinstance(item, Mapping)]
                != ["source_response", "transition_bridge", "target_opening"]
                or (
                    new_contract
                    and (
                        set(segments[0]).difference({"fixed_narrations"}) not in ({"phase", "performance"}, {"phase", "performance", "scene_narration"})
                        or ("scene_narration" in segments[0] and not valid_scene_narration(segments[0]))
                        or set(segments[1]) != {"phase", "scene_narration"}
                        or set(segments[2]).difference({"fixed_narrations"}) != {"phase", "scene_narration", "performance"}
                        or not valid_mixed_performance_policy(
                            segments[0],
                            transition_source_dialogue_policy(
                                str(
                                    outcome.ledger_event.get("source_transition_dialogue_policy")
                                    or outcome.ledger_event.get("before_dialogue_policy")
                                    or "required"
                                )
                            ),
                        )
                        or not valid_scene_narration(segments[1], allow_empty=True)
                        or not valid_scene_narration(segments[2])
                        or not valid_mixed_performance_policy(
                            segments[2], outcome.session.dialogue_policy
                        )
                    )
                )
                or (
                    not new_contract
                    and (
                        not valid_ordered_content(segments[0], require_dialogue=True)
                        or not valid_ordered_content(segments[1], require_narration=True)
                        or not valid_ordered_content(segments[2], require_narration=True)
                    )
                )
            ):
                raise NumericV2RuntimeError("numeric_transition_performance_invalid")
        elif (
            "performance" in performance
            and (
                not is_silent_invitation_control(outcome.ledger_event, performance)
                and not valid_mixed_performance_policy(
                    performance,
                    str(
                        outcome.ledger_event.get("performance_dialogue_policy")
                        or outcome.session.dialogue_policy
                    ),
                )
                or (
                    "scene_narration" in performance
                    and not valid_scene_narration(performance)
                )
            )
        ):
            raise NumericV2RuntimeError("numeric_performance_invalid")
        new_contract = "performance" in performance or (
            route_changed
            and isinstance(performance.get("segments"), list)
            and any(
                isinstance(segment, Mapping)
                and ("performance" in segment or "scene_narration" in segment)
                for segment in performance["segments"]
            )
        )
        record = {
            "schema": PERFORMANCE_RECORD_SCHEMA,
            "client_turn_id": outcome.ledger_event["client_turn_id"],
            "revision": outcome.session.revision,
            "input_text": outcome.ledger_event["input_text"],
            "from_node_id": outcome.ledger_event["from_node_id"],
            "to_node_id": outcome.ledger_event["to_node_id"],
            **deepcopy(dict(performance)),
            # The actor cannot supply this clock. Only committed live turns use it.
            "performed_at": datetime.now().astimezone().isoformat(),
            # Legacy records lack this marker; enforce mixed-body contracts only from version 3 so historical Sessions remain recoverable.
            "performance_contract_version": (
                3
                if new_contract
                else (2 if "content" in performance or route_changed else 1)
            ),
        }
        record["player_action_projection"] = normalize_player_action_projection(
            outcome.ledger_event.get("player_action_projection")
        )
        if outcome.ledger_event.get("input_source") == "reinvite":
            record["input_source"] = "reinvite"
        fact_projection = _fact_projection(outcome.ledger_event, record)
        record["fact_projection"] = fact_projection
        timeline_projection = _timeline_projection(outcome.ledger_event)
        record["timeline_projection"] = timeline_projection
        ledger_event = {
            **outcome.ledger_event,
            "player_action_projection": deepcopy(record["player_action_projection"]),
            "fact_projection": deepcopy(fact_projection),
            "timeline_projection": deepcopy(timeline_projection),
        }
        narrative_state = outcome.session.narrative_state
        if "narrative_record" not in ledger_event and narrative_state is not None:
            # Reinvitation only publishes author text; v2 replay preserves prior gaps.
            control_turn = ledger_event.get("input_source") == "reinvite"
            ledger_event["narrative_record"] = {
                "schema": "numeric_v2_narrative_record_v2" if control_turn else "numeric_v2_narrative_record_v1",
                "coverage": "confirmed_no_change" if control_turn else "unrecorded",
                "events": [], "proposal_count": 0,
            }
        if "narrative_record" in ledger_event:
            narrative_record = deepcopy(ledger_event["narrative_record"])
            record["narrative_record"] = narrative_record
            narrative_state = apply_narrative_record(
                narrative_state, narrative_record, revision=outcome.session.revision,
                node_id=str(ledger_event["to_node_id"]),
                from_node_id=str(ledger_event["from_node_id"]),
                performance=record, message=str(ledger_event["input_text"]),
            )
        session = replace(
            outcome.session,
            performance_history=(*outcome.session.performance_history, record),
            narrative_state=narrative_state,
        )
        return await self.store.commit(session, ledger_event)

    async def end_session(
        self,
        session_id: str,
        *,
        base_revision: int,
        base_lifecycle_revision: int,
        reason: str,
    ) -> NumericV2StoredSession:
        return await self.store.end_session(
            session_id,
            base_revision=base_revision,
            base_lifecycle_revision=base_lifecycle_revision,
            reason=reason,
        )

    async def resume_session(
        self,
        session_id: str,
        *,
        base_revision: int,
        base_lifecycle_revision: int,
    ) -> NumericV2StoredSession:
        return await self.store.resume_session(
            session_id,
            base_revision=base_revision,
            base_lifecycle_revision=base_lifecycle_revision,
        )

    async def forget_history_through_current_revision(
        self,
        session_id: str,
    ) -> NumericV2StoredSession:
        """Persist the forgetting watermark while retaining Runtime context for continued performance."""  # noqa: DOCSTRING_CJK

        return await self.store.forget_history_through_current_revision(session_id)


__all__ = [
    "apply_fact_ops",
    "validate_fact_candidates",
    "LEDGER_EVENT_SCHEMA",
    "MetricChangeV2",
    "NumericV2DuplicateTurnError",
    "NumericV2Engine",
    "NumericV2RevisionConflictError",
    "NumericV2Runtime",
    "NumericV2RuntimeError",
    "PERFORMANCE_RECORD_SCHEMA",
    "SESSION_SCHEMA",
    "STORY_STATE_SCHEMA",
    "ScriptSessionV2",
    "TurnOutcomeV2",
    "TurnRequestV2",
]
