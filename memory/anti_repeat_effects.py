# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""Privacy-bounded, per-character observability for runtime anti-repeat decisions.

The corpus remains the source of scoring context.  This module stores only
daily aggregate counters and short repeated fragments extracted from detector
evidence; it never persists complete rejected drafts, user text, prompts, URLs,
code, or templates.
"""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import json
import math
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger
from utils.natural_expression_candidates import (
    _URL_RE,
    _runtime_protected_spans,
    normalize_language,
)

logger = get_module_logger(__name__, "Memory")

SCHEMA_VERSION = "anti-repeat-effects/v1"
RETENTION_DAYS = 120
MAX_PATTERNS_PER_DAY = 64
MAX_RESPONSE_BUCKETS = 512
MAX_PHRASE_CHARS = 48
_DEFAULT_KEY = "default"
_BLOCKED_OUTCOMES = frozenset(
    {
        "blocked_initial",
        "blocked_after_regen_bm25",
        "blocked_after_regen_unanswered",
        "blocked_after_regen_literal",
        "regen_failed",
        "break_reminder_suppressed",
    }
)
_VALID_REASONS = frozenset({"literal_similarity", "bm25", "unanswered_repeat"})
_VALID_SOURCES = frozenset({"regular_prompt", "proactive", "break_reminder"})
_VALID_ACTIONS = frozenset({"block", "regenerate"})
_VALID_OUTCOMES = _BLOCKED_OUTCOMES | {
    "regen_guard_passed",
    "abandoned_user_interaction",
}
_PROTECTED_RE = re.compile(
    r"```[\s\S]*?```|`[^`\r\n]+`|\{\{[^{}\r\n]*\}\}|"
    r"\$\{[^{}\r\n]*\}|<%[^%\r\n]*%>|<[^<>\r\n]{1,80}>|"
    r"\[[A-Z][A-Z0-9_-]{1,63}\]"
)
_EDGE_PUNCTUATION = " \t\r\n,，。.!！?？;；:：、()（）[]【】{}<>《》\"'“”‘’"


def _resolve_name(name: str | None) -> str:
    return name or _DEFAULT_KEY


def _utc_day(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()


def _normalized_phrase(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", " ", normalized).strip(_EDGE_PUNCTUATION).casefold()


def _safe_fragment(value: str) -> str:
    fragment = unicodedata.normalize("NFKC", value or "")
    if _URL_RE.search(fragment) or _PROTECTED_RE.search(fragment):
        return ""
    fragment = re.sub(r"\s+", " ", fragment).strip(_EDGE_PUNCTUATION)
    if not fragment:
        return ""
    if len(fragment) > MAX_PHRASE_CHARS:
        return ""
    return fragment


def _without_protected_text(value: str) -> str:
    spans = _runtime_protected_spans(value)
    spans.extend(match.span() for match in _PROTECTED_RE.finditer(value))
    spans.sort()
    chunks: list[str] = []
    cursor = 0
    for start, end in spans:
        if end <= cursor:
            continue
        if start > cursor:
            chunks.append(value[cursor:start])
        chunks.append(" ")
        cursor = end
    chunks.append(value[cursor:])
    return "".join(chunks)


@dataclass(frozen=True, slots=True)
class RepeatSignature:
    phrase: str
    normalized_phrase: str
    language: str


@dataclass(frozen=True, slots=True)
class AntiRepeatDecision:
    source: str
    reasons: tuple[str, ...]
    action: str
    outcome: str
    signature: RepeatSignature | None = None
    score_before: float | None = None
    score_after: float | None = None
    response_id: str | None = None

    def validate(self) -> None:
        if self.source not in _VALID_SOURCES:
            raise ValueError("unsupported anti-repeat decision source")
        if self.action not in _VALID_ACTIONS:
            raise ValueError("unsupported anti-repeat decision action")
        if self.outcome not in _VALID_OUTCOMES:
            raise ValueError("unsupported anti-repeat decision outcome")
        if not self.reasons or any(
            reason not in _VALID_REASONS for reason in self.reasons
        ):
            raise ValueError("unsupported anti-repeat decision reason")
        if self.response_id is not None and not (0 < len(self.response_id) <= 128):
            raise ValueError("invalid anti-repeat response ID")


def build_repeat_signature(
    draft_text: str,
    evidence_terms: Iterable[str] = (),
    *,
    language: str,
    fallback_fragment: str = "",
) -> RepeatSignature | None:
    """Build one short, readable detector signature without retaining a draft."""
    try:
        normalized_language = normalize_language(language)
    except Exception:
        return None

    draft_normalized = unicodedata.normalize("NFKC", draft_text or "")
    full_draft_phrase = _normalized_phrase(draft_normalized)
    unprotected_draft_phrase = _normalized_phrase(
        _without_protected_text(draft_normalized)
    )
    candidates: list[str] = []
    fallback = _safe_fragment(fallback_fragment)
    if fallback:
        candidates.append(fallback)
    seen: set[str] = set()
    for raw_term in evidence_terms:
        term = _safe_fragment(str(raw_term))
        normalized = _normalized_phrase(term)
        if not term or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if normalized in _normalized_phrase(draft_normalized):
            candidates.append(term)
    for phrase in candidates:
        normalized = _normalized_phrase(phrase)
        if (
            len(normalized) < 2
            or normalized == full_draft_phrase
            or normalized not in unprotected_draft_phrase
        ):
            continue
        return RepeatSignature(phrase, normalized, normalized_language)
    return None


def _default_payload(now: float) -> dict[str, Any]:
    return {
        "version": 1,
        "schema_version": SCHEMA_VERSION,
        "started_at": now,
        "daily_buckets": {},
        "response_buckets": {},
    }


def _response_key(response_id: str) -> str:
    return hashlib.sha256(response_id.encode("utf-8")).hexdigest()[:24]


def _empty_counters() -> dict[str, int]:
    return {
        "soft_hint_injected": 0,
        "detected": 0,
        "regen_triggered": 0,
        "regen_guard_passed": 0,
        "blocked_delivery": 0,
        "break_reminder_suppressed": 0,
        "abandoned_user_interaction": 0,
        "unattributed": 0,
    }


def _empty_bucket() -> dict[str, Any]:
    return {
        "counters": _empty_counters(),
        "reason_counts": {reason: 0 for reason in sorted(_VALID_REASONS)},
        "bm25": {"before_sum": 0.0, "after_sum": 0.0, "pair_count": 0},
        "patterns": {},
    }


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _as_float(value: Any) -> float:
    try:
        normalized = float(value or 0.0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return max(0.0, normalized) if math.isfinite(normalized) else 0.0


def _response_retention_key(response: Any) -> tuple[bool, float]:
    if not isinstance(response, dict):
        return False, 0.0
    created_at = _as_float(response.get("created_at"))
    delivered_at = _as_float(response.get("delivered_at"))
    return delivered_at > 0, max(created_at, delivered_at)


def _normalize_bucket(raw: Any) -> dict[str, Any]:
    bucket = _empty_bucket()
    if not isinstance(raw, dict):
        return bucket
    raw_counters = raw.get("counters")
    if isinstance(raw_counters, dict):
        for key in bucket["counters"]:
            bucket["counters"][key] = _as_int(raw_counters.get(key))
    raw_reasons = raw.get("reason_counts")
    if isinstance(raw_reasons, dict):
        for reason in bucket["reason_counts"]:
            bucket["reason_counts"][reason] = _as_int(raw_reasons.get(reason))
    raw_bm25 = raw.get("bm25")
    if isinstance(raw_bm25, dict):
        bucket["bm25"] = {
            "before_sum": _as_float(raw_bm25.get("before_sum")),
            "after_sum": _as_float(raw_bm25.get("after_sum")),
            "pair_count": _as_int(raw_bm25.get("pair_count")),
        }
    raw_patterns = raw.get("patterns")
    if isinstance(raw_patterns, dict):
        for pattern_id, raw_pattern in list(raw_patterns.items())[
            :MAX_PATTERNS_PER_DAY
        ]:
            if not isinstance(pattern_id, str) or not isinstance(raw_pattern, dict):
                continue
            phrase = _safe_fragment(str(raw_pattern.get("phrase", "")))
            normalized = _normalized_phrase(phrase)
            language = str(raw_pattern.get("language", ""))
            if not phrase or not normalized or not language:
                continue
            reasons = raw_pattern.get("reasons")
            bucket["patterns"][pattern_id] = {
                "phrase": phrase,
                "normalized_phrase": normalized,
                "language": language,
                "reasons": {
                    reason: _as_int(reasons.get(reason))
                    for reason in sorted(_VALID_REASONS)
                    if isinstance(reasons, dict) and _as_int(reasons.get(reason))
                },
                "detected_count": _as_int(raw_pattern.get("detected_count")),
                "regen_triggered_count": _as_int(
                    raw_pattern.get("regen_triggered_count")
                ),
                "regen_guard_passed_count": _as_int(
                    raw_pattern.get("regen_guard_passed_count")
                ),
                "blocked_count": _as_int(raw_pattern.get("blocked_count")),
                "last_seen_at": _as_float(raw_pattern.get("last_seen_at")),
            }
    return bucket


def _apply_decision_to_bucket(
    bucket: dict[str, Any],
    decision: AntiRepeatDecision,
    now: float,
) -> None:
    """Apply one decision to a response-scoped aggregate bucket."""
    counters = bucket.setdefault("counters", _empty_counters())
    counters["detected"] = int(counters.get("detected", 0)) + 1
    if decision.action == "regenerate":
        counters["regen_triggered"] = int(counters.get("regen_triggered", 0)) + 1
    if decision.outcome == "regen_guard_passed":
        counters["regen_guard_passed"] = int(counters.get("regen_guard_passed", 0)) + 1
    if decision.outcome in _BLOCKED_OUTCOMES:
        counters["blocked_delivery"] = int(counters.get("blocked_delivery", 0)) + 1
    if decision.outcome == "break_reminder_suppressed":
        counters["break_reminder_suppressed"] = (
            int(counters.get("break_reminder_suppressed", 0)) + 1
        )
    if decision.outcome == "abandoned_user_interaction":
        counters["abandoned_user_interaction"] = (
            int(counters.get("abandoned_user_interaction", 0)) + 1
        )

    reason_counts = bucket.setdefault("reason_counts", {})
    for reason in dict.fromkeys(decision.reasons):
        reason_counts[reason] = int(reason_counts.get(reason, 0)) + 1

    bm25 = bucket.setdefault("bm25", {})
    if (
        decision.score_before is not None
        and decision.score_after is not None
        and decision.score_before > 0
    ):
        bm25["before_sum"] = float(bm25.get("before_sum", 0.0)) + float(
            decision.score_before
        )
        bm25["after_sum"] = float(bm25.get("after_sum", 0.0)) + max(
            0.0, float(decision.score_after)
        )
        bm25["pair_count"] = int(bm25.get("pair_count", 0)) + 1

    signature = decision.signature
    patterns = bucket.setdefault("patterns", {})
    if signature is None:
        counters["unattributed"] = int(counters.get("unattributed", 0)) + 1
        return
    pattern_id = hashlib.sha256(
        f"{signature.language}\0{signature.normalized_phrase}".encode("utf-8")
    ).hexdigest()[:16]
    pattern = patterns.get(pattern_id)
    if pattern is None and len(patterns) >= MAX_PATTERNS_PER_DAY:
        counters["unattributed"] = int(counters.get("unattributed", 0)) + 1
        return
    if pattern is None:
        pattern = {
            "phrase": signature.phrase,
            "normalized_phrase": signature.normalized_phrase,
            "language": signature.language,
            "reasons": {},
            "detected_count": 0,
            "regen_triggered_count": 0,
            "regen_guard_passed_count": 0,
            "blocked_count": 0,
            "last_seen_at": now,
        }
        patterns[pattern_id] = pattern
    pattern["detected_count"] += 1
    if decision.action == "regenerate":
        pattern["regen_triggered_count"] += 1
    if decision.outcome == "regen_guard_passed":
        pattern["regen_guard_passed_count"] += 1
    if decision.outcome in _BLOCKED_OUTCOMES:
        pattern["blocked_count"] += 1
    pattern["last_seen_at"] = max(float(pattern.get("last_seen_at", 0.0)), now)
    for reason in dict.fromkeys(decision.reasons):
        pattern["reasons"][reason] = int(pattern["reasons"].get(reason, 0)) + 1


def _summarize_effect_buckets(buckets: Iterable[dict[str, Any]]) -> dict[str, Any]:
    totals = _empty_counters()
    reason_totals = {reason: 0 for reason in sorted(_VALID_REASONS)}
    before_sum = after_sum = 0.0
    pair_count = 0
    patterns_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for bucket in buckets:
        for key, value in bucket.get("counters", {}).items():
            if key in totals:
                totals[key] += _as_int(value)
        for key, value in bucket.get("reason_counts", {}).items():
            if key in reason_totals:
                reason_totals[key] += _as_int(value)
        bm25 = bucket.get("bm25", {})
        before_sum += _as_float(bm25.get("before_sum"))
        after_sum += _as_float(bm25.get("after_sum"))
        pair_count += _as_int(bm25.get("pair_count"))
        for pattern in bucket.get("patterns", {}).values():
            if not isinstance(pattern, dict):
                continue
            key = (
                str(pattern.get("language", "")),
                str(pattern.get("normalized_phrase", "")),
            )
            if not all(key):
                continue
            merged = patterns_by_key.setdefault(
                key,
                {
                    "phrase": str(pattern.get("phrase", "")),
                    "normalized_phrase": key[1],
                    "language": key[0],
                    "reasons": {},
                    "detected_count": 0,
                    "regen_triggered_count": 0,
                    "regen_guard_passed_count": 0,
                    "blocked_count": 0,
                    "last_seen_at": 0.0,
                },
            )
            for field in (
                "detected_count",
                "regen_triggered_count",
                "regen_guard_passed_count",
                "blocked_count",
            ):
                merged[field] += _as_int(pattern.get(field))
            merged["last_seen_at"] = max(
                merged["last_seen_at"],
                _as_float(pattern.get("last_seen_at")),
            )
            for reason, count in pattern.get("reasons", {}).items():
                if reason in _VALID_REASONS:
                    merged["reasons"][reason] = int(
                        merged["reasons"].get(reason, 0)
                    ) + _as_int(count)

    average_before = before_sum / pair_count if pair_count else 0.0
    average_after = after_sum / pair_count if pair_count else 0.0
    reduction_ratio = 1.0 - after_sum / before_sum if before_sum > 0 else 0.0
    patterns = sorted(
        patterns_by_key.values(),
        key=lambda item: (
            -item["blocked_count"],
            -item["detected_count"],
            item["normalized_phrase"],
        ),
    )
    return {
        "totals": totals,
        "reason_counts": reason_totals,
        "bm25": {
            "pair_count": pair_count,
            "average_before": round(average_before, 4),
            "average_after": round(average_after, 4),
            "reduction_ratio": round(reduction_ratio, 4),
        },
        "patterns": patterns,
    }


class AntiRepeatEffectStore:
    """Thread-safe daily aggregate store with ordered atomic snapshots."""

    def __init__(self) -> None:
        self._config_manager = get_config_manager()
        self._cache: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._write_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._staged_seq: dict[str, int] = {}
        self._written_seq: dict[str, int] = {}
        self._detached_flushes: set[asyncio.Task] = set()

    def _existing_file_path(self, name: str) -> str:
        return os.path.join(
            str(self._config_manager.memory_dir),
            name,
            "anti_repeat_effects.json",
        )

    def _write_file_path(self, name: str) -> str:
        from memory import ensure_character_dir

        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            "anti_repeat_effects.json",
        )

    def _get_lock(self, name: str) -> threading.Lock:
        if name not in self._locks:
            with self._locks_guard:
                self._locks.setdefault(name, threading.Lock())
        return self._locks[name]

    def _get_write_lock(self, name: str) -> threading.Lock:
        if name not in self._write_locks:
            with self._locks_guard:
                self._write_locks.setdefault(name, threading.Lock())
        return self._write_locks[name]

    @staticmethod
    def _normalize_payload(raw: Any, now: float) -> dict[str, Any]:
        if not isinstance(raw, dict) or raw.get("version") != 1:
            return _default_payload(now)
        payload = _default_payload(now)
        payload["started_at"] = _as_float(raw.get("started_at")) or now
        buckets = raw.get("daily_buckets")
        if isinstance(buckets, dict):
            payload["daily_buckets"] = {
                day: _normalize_bucket(bucket)
                for day, bucket in buckets.items()
                if isinstance(day, str) and isinstance(bucket, dict)
            }
        response_buckets = raw.get("response_buckets")
        if isinstance(response_buckets, dict):
            normalized_response_buckets: dict[str, dict[str, Any]] = {}
            retained_responses = heapq.nlargest(
                MAX_RESPONSE_BUCKETS,
                (
                    (response_key, raw_response)
                    for response_key, raw_response in response_buckets.items()
                    if isinstance(response_key, str)
                    and isinstance(raw_response, dict)
                ),
                key=lambda item: _response_retention_key(item[1]),
            )
            for response_key, raw_response in retained_responses:
                normalized_response_buckets[response_key] = {
                    "created_at": _as_float(raw_response.get("created_at")),
                    "delivered_at": _as_float(raw_response.get("delivered_at")),
                    "bucket": _normalize_bucket(raw_response.get("bucket")),
                }
            payload["response_buckets"] = normalized_response_buckets
        return payload

    def _read_payload_from_disk(self, name: str, now: float) -> dict[str, Any]:
        path = self._existing_file_path(name)
        if not os.path.exists(path):
            return _default_payload(now)
        try:
            with open(path, encoding="utf-8") as handle:
                return self._normalize_payload(json.load(handle), now)
        except Exception as exc:
            logger.warning(
                "[AntiRepeatEffects] load failed for %s: %s",
                name,
                type(exc).__name__,
            )
            return _default_payload(now)

    def _load_unlocked(self, name: str, now: float) -> dict[str, Any]:
        if name not in self._cache:
            self._cache[name] = self._read_payload_from_disk(name, now)
        return self._cache[name]

    @staticmethod
    def _prune(payload: dict[str, Any], now: float) -> bool:
        changed = False
        cutoff = (
            datetime.fromtimestamp(now, timezone.utc).date()
            - timedelta(days=RETENTION_DAYS - 1)
        ).isoformat()
        current_day = _utc_day(now)
        buckets = payload.get("daily_buckets", {})
        for day in list(buckets):
            if day < cutoff or day > current_day:
                del buckets[day]
                changed = True
        response_buckets = payload.get("response_buckets", {})
        cutoff_timestamp = now - RETENTION_DAYS * 24 * 60 * 60
        for response_key, response in list(response_buckets.items()):
            retention_timestamp = (
                max(
                    _as_float(response.get("created_at")),
                    _as_float(response.get("delivered_at")),
                )
                if isinstance(response, dict)
                else 0.0
            )
            if (
                retention_timestamp < cutoff_timestamp
                or retention_timestamp > now
            ):
                del response_buckets[response_key]
                changed = True
        if len(response_buckets) > MAX_RESPONSE_BUCKETS:
            oldest = sorted(
                response_buckets,
                key=lambda key: _response_retention_key(response_buckets[key]),
            )
            for response_key in oldest[: len(response_buckets) - MAX_RESPONSE_BUCKETS]:
                del response_buckets[response_key]
                changed = True
        return changed

    def _stage_unlocked(self, name: str) -> tuple[str, dict[str, Any], int]:
        seq = self._staged_seq.get(name, 0) + 1
        self._staged_seq[name] = seq
        payload = json.loads(json.dumps(self._cache[name], ensure_ascii=False))
        return name, payload, seq

    def _flush_snapshot(
        self,
        name: str,
        payload: dict[str, Any],
        seq: int,
        *,
        raise_on_error: bool = False,
    ) -> None:
        try:
            from utils.cloudsave_runtime import cloudsave_writable_transaction

            with cloudsave_writable_transaction(
                self._config_manager,
                operation="save",
                target=f"memory/{name}/anti_repeat_effects.json",
            ):
                with self._get_write_lock(name):
                    if seq <= self._written_seq.get(name, 0):
                        return
                    atomic_write_json(
                        self._write_file_path(name),
                        payload,
                        indent=2,
                        ensure_ascii=False,
                    )
                    self._written_seq[name] = seq
        except Exception as exc:
            logger.warning(
                "[AntiRepeatEffects] save failed for %s: %s",
                name,
                type(exc).__name__,
            )
            if raise_on_error:
                raise

    def _apply_decision(
        self,
        name: str,
        decision: AntiRepeatDecision,
        now: float,
    ) -> tuple[str, dict[str, Any], int]:
        decision.validate()
        name = _resolve_name(name)
        with self._get_lock(name):
            payload = self._load_unlocked(name, now)
            self._prune(payload, now)
            bucket = payload["daily_buckets"].setdefault(_utc_day(now), _empty_bucket())
            _apply_decision_to_bucket(bucket, decision, now)

            if decision.response_id:
                response_buckets = payload.setdefault("response_buckets", {})
                response_bucket = response_buckets.setdefault(
                    _response_key(decision.response_id),
                    {
                        "created_at": now,
                        "delivered_at": 0.0,
                        "bucket": _empty_bucket(),
                    },
                )
                response_bucket["created_at"] = min(
                    _as_float(response_bucket.get("created_at")) or now,
                    now,
                )
                _apply_decision_to_bucket(response_bucket["bucket"], decision, now)
                self._prune(payload, now)

            self._cache[name] = payload
            return self._stage_unlocked(name)

    def stage_decision(
        self,
        name: str,
        decision: AntiRepeatDecision,
        *,
        now: float | None = None,
    ) -> tuple[str, dict[str, Any], int]:
        timestamp = time.time() if now is None else float(now)
        return self._apply_decision(name, decision, timestamp)

    def stage_response_delivered(
        self,
        name: str,
        response_id: str,
        *,
        now: float | None = None,
    ) -> tuple[str, dict[str, Any], int] | None:
        """Mark a response-scoped aggregate as belonging to persisted output."""
        if not isinstance(response_id, str) or not (0 < len(response_id) <= 128):
            return None
        timestamp = time.time() if now is None else float(now)
        name = _resolve_name(name)
        with self._get_lock(name):
            payload = self._load_unlocked(name, timestamp)
            response = payload.get("response_buckets", {}).get(
                _response_key(response_id)
            )
            if not isinstance(response, dict):
                return None
            response["delivered_at"] = timestamp
            self._prune(payload, timestamp)
            self._cache[name] = payload
            return self._stage_unlocked(name)

    def stage_soft_hint(
        self,
        name: str,
        *,
        now: float | None = None,
    ) -> tuple[str, dict[str, Any], int]:
        timestamp = time.time() if now is None else float(now)
        name = _resolve_name(name)
        with self._get_lock(name):
            payload = self._load_unlocked(name, timestamp)
            self._prune(payload, timestamp)
            bucket = payload["daily_buckets"].setdefault(
                _utc_day(timestamp), _empty_bucket()
            )
            counters = bucket.setdefault("counters", _empty_counters())
            counters["soft_hint_injected"] = (
                int(counters.get("soft_hint_injected", 0)) + 1
            )
            self._cache[name] = payload
            return self._stage_unlocked(name)

    async def aflush_staged(
        self,
        staged: tuple[str, dict[str, Any], int] | None,
    ) -> None:
        if staged is None:
            return
        await asyncio.to_thread(self._flush_snapshot, *staged)

    def flush_staged_detached(
        self,
        staged: tuple[str, dict[str, Any], int] | None,
    ) -> None:
        if staged is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._flush_snapshot(*staged)
            return
        task = loop.create_task(self.aflush_staged(staged))
        self._detached_flushes.add(task)

        def _done(finished: asyncio.Task) -> None:
            self._detached_flushes.discard(finished)
            if not finished.cancelled() and finished.exception() is not None:
                logger.debug(
                    "[AntiRepeatEffects] detached flush failed: %s",
                    type(finished.exception()).__name__,
                )

        task.add_done_callback(_done)

    def record_decision(
        self,
        name: str,
        decision: AntiRepeatDecision,
        *,
        now: float | None = None,
    ) -> None:
        self._flush_snapshot(*self.stage_decision(name, decision, now=now))

    def record_soft_hint(self, name: str, *, now: float | None = None) -> None:
        self._flush_snapshot(*self.stage_soft_hint(name, now=now))

    def query_effects(
        self,
        name: str,
        days: int,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        if days not in {7, 30, 90}:
            raise ValueError("effect days must be one of 7, 30, or 90")
        timestamp = time.time() if now is None else float(now)
        name = _resolve_name(name)
        cached = self._cache.get(name)
        source_available = os.path.exists(self._existing_file_path(name)) or bool(
            cached and cached.get("daily_buckets")
        )
        with self._get_lock(name):
            payload = self._load_unlocked(name, timestamp)
            staged = self._stage_unlocked(name) if self._prune(payload, timestamp) else None
            cutoff = (
                datetime.fromtimestamp(timestamp, timezone.utc).date()
                - timedelta(days=days - 1)
            ).isoformat()
            buckets = [
                bucket
                for day, bucket in payload.get("daily_buckets", {}).items()
                if cutoff <= day <= _utc_day(timestamp) and isinstance(bucket, dict)
            ]

        if staged is not None:
            self._flush_snapshot(*staged)

        result = {
            "schema_version": SCHEMA_VERSION,
            "source_available": source_available,
            "started_at": float(payload.get("started_at", timestamp)),
            "period_days": days,
        }
        result.update(_summarize_effect_buckets(buckets))
        return result

    def query_effects_for_responses(
        self,
        name: str,
        response_ids: Iterable[str],
        assistant_message_limit: int,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Aggregate delivered effects linked to a bounded assistant history slice."""
        if assistant_message_limit < 1:
            raise ValueError("assistant message limit must be greater than zero")
        timestamp = time.time() if now is None else float(now)
        name = _resolve_name(name)
        keys = {
            _response_key(response_id)
            for response_id in response_ids
            if isinstance(response_id, str) and 0 < len(response_id) <= 128
        }
        with self._get_lock(name):
            payload = self._load_unlocked(name, timestamp)
            staged = self._stage_unlocked(name) if self._prune(payload, timestamp) else None
            response_buckets = payload.get("response_buckets", {})
            linked = [
                response_buckets[key]
                for key in keys
                if isinstance(response_buckets.get(key), dict)
                and _as_float(response_buckets[key].get("delivered_at")) > 0
            ]
            source_available = bool(linked)
            buckets = [
                response["bucket"]
                for response in linked
                if isinstance(response.get("bucket"), dict)
            ]
            started_at = float(payload.get("started_at", timestamp))

        if staged is not None:
            self._flush_snapshot(*staged)

        result = {
            "schema_version": SCHEMA_VERSION,
            "source_available": source_available,
            "started_at": started_at,
            "scope_type": "assistant_messages",
            "assistant_message_limit": assistant_message_limit,
            "linked_message_count": len(linked),
        }
        result.update(_summarize_effect_buckets(buckets))
        return result

    def clear_effects(self, name: str) -> None:
        timestamp = time.time()
        name = _resolve_name(name)
        with self._get_lock(name):
            previous = self._load_unlocked(name, timestamp)
            self._cache[name] = _default_payload(timestamp)
            staged = self._stage_unlocked(name)
            try:
                self._flush_snapshot(*staged, raise_on_error=True)
            except Exception:
                self._cache[name] = previous
                raise

    def evict_character(self, name: str) -> None:
        """Forget one identity and fence snapshots staged before its removal."""
        name = _resolve_name(name)
        with self._get_lock(name):
            with self._get_write_lock(name):
                fence = max(
                    self._staged_seq.get(name, 0),
                    self._written_seq.get(name, 0),
                )
                self._cache.pop(name, None)
                self._staged_seq[name] = fence
                self._written_seq[name] = fence


_GLOBAL_STORE: Optional[AntiRepeatEffectStore] = None
_GLOBAL_LOCK = threading.Lock()


def get_anti_repeat_effect_store() -> AntiRepeatEffectStore:
    global _GLOBAL_STORE
    if _GLOBAL_STORE is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_STORE is None:
                _GLOBAL_STORE = AntiRepeatEffectStore()
    return _GLOBAL_STORE


def evict_cached_anti_repeat_effects(*character_names: str) -> None:
    """Evict loaded identities without creating the global store."""
    store = _GLOBAL_STORE
    if store is None:
        return
    for character_name in dict.fromkeys(character_names):
        store.evict_character(character_name)


def record_anti_repeat_decision(
    character_name: str,
    decision: AntiRepeatDecision,
) -> None:
    """Best-effort runtime helper that never changes delivery behavior."""
    try:
        store = get_anti_repeat_effect_store()
        store.flush_staged_detached(store.stage_decision(character_name, decision))
    except Exception as exc:
        logger.debug(
            "[AntiRepeatEffects] decision record skipped: %s",
            type(exc).__name__,
        )


def mark_anti_repeat_response_delivered(
    character_name: str,
    response_id: str,
    *,
    now: float | None = None,
) -> None:
    """Best-effort link from one delivered reply to its aggregate decisions."""
    try:
        store = get_anti_repeat_effect_store()
        staged = store.stage_response_delivered(
            character_name,
            response_id,
            now=now,
        )
        store.flush_staged_detached(staged)
    except Exception as exc:
        logger.debug(
            "[AntiRepeatEffects] delivered response link skipped: %s",
            type(exc).__name__,
        )


def record_anti_repeat_soft_hint(character_name: str) -> None:
    """Best-effort soft-hint counter without detector text."""
    try:
        store = get_anti_repeat_effect_store()
        store.flush_staged_detached(store.stage_soft_hint(character_name))
    except Exception as exc:
        logger.debug(
            "[AntiRepeatEffects] soft-hint record skipped: %s",
            type(exc).__name__,
        )
