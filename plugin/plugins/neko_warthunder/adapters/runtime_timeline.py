"""Lightweight runtime observability for War Thunder decision flow."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Any


_OUTPUT_STAGES = {
    "dispatcher_dry_run",
    "dispatcher_pushed",
    "dispatcher_failed",
    "dispatcher_suppressed",
    "context_pushed",
    "context_failed",
    "test_say_pushed",
    "test_say_blocked",
    "test_say_failed",
    "tts_pending",
    "tts_failed",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_record(data: dict[str, Any]) -> dict[str, Any]:
    banned = {"raw_payload", "prompt", "payload", "battle_state", "push_message"}
    return {k: v for k, v in data.items() if k not in banned and v is not None}


class RuntimeTimeline:
    """In-memory observe state.

    Normal mode keeps only latest summaries. Debug mode additionally keeps a
    bounded ring buffer of small metadata records.
    """

    def __init__(
        self,
        *,
        observability_enabled: bool = False,
        max_events: int = 100,
        include_prompt_preview: bool = False,
    ) -> None:
        self.enabled = bool(observability_enabled)
        self.max_events = max(1, int(max_events or 100))
        self.include_prompt_preview = bool(include_prompt_preview)
        self._records: deque[dict[str, Any]] = deque(maxlen=self.max_events)
        self._seq = 0
        self._lock = Lock()
        self._last_event: dict[str, Any] | None = None
        self._last_decision: dict[str, Any] | None = None
        self._last_output_status: dict[str, Any] | None = None
        self._last_tick_at: str | None = None

    def configure(
        self,
        *,
        observability_enabled: bool,
        max_events: int,
        include_prompt_preview: bool,
    ) -> None:
        with self._lock:
            old_records = list(self._records)[-max(1, int(max_events or 100)) :]
            self.enabled = bool(observability_enabled)
            self.max_events = max(1, int(max_events or 100))
            self.include_prompt_preview = bool(include_prompt_preview)
            self._records = deque(old_records, maxlen=self.max_events)

    def mark_tick(self) -> None:
        with self._lock:
            self._last_tick_at = _now_iso()

    def record_decision(
        self,
        *,
        event_id: str | None,
        stage: str,
        outcome: str,
        reason: str,
        scenario: str | None = None,
        safety_status: str | None = None,
        dry_run: bool | None = None,
    ) -> None:
        record = _clean_record(
            {
                "ts": _now_iso(),
                "event_id": event_id,
                "stage": stage,
                "outcome": outcome,
                "reason": reason,
                "scenario": scenario,
                "safety_status": safety_status,
                "dry_run": dry_run,
            }
        )
        with self._lock:
            if (
                self._last_decision
                and self._last_decision.get("outcome") == "allowed"
                and record.get("stage") == "arbiter_preempted"
            ):
                return
            self._last_decision = dict(record)
            if event_id:
                self._last_event = {"event_id": event_id}

    def record_stage(self, *, stage: str, outcome: str, reason: str, **metadata: Any) -> None:
        try:
            safe_summary = metadata.get("safe_summary")
            record = _clean_record(
                {
                    "seq": self._seq + 1,
                    "ts": _now_iso(),
                    "stage": stage,
                    "outcome": outcome,
                    "reason": reason,
                    "trace_id": metadata.get("trace_id"),
                    "event_id": metadata.get("event_id"),
                    "edge": metadata.get("edge"),
                    "scenario": metadata.get("scenario"),
                    "priority": metadata.get("priority"),
                    "level": metadata.get("level"),
                    "dry_run": metadata.get("dry_run"),
                    "in_battle": metadata.get("in_battle"),
                    "replay": metadata.get("replay"),
                    "cooldown_key": metadata.get("cooldown_key"),
                    "window": metadata.get("window"),
                    "safety_status": metadata.get("safety_status"),
                    "dispatcher_status": metadata.get("dispatcher_status"),
                    "kind": metadata.get("kind"),
                    "source": metadata.get("source"),
                    "ai_behavior": metadata.get("ai_behavior"),
                    "visibility": metadata.get("visibility"),
                    "pushed": metadata.get("pushed"),
                    "target_lanlan": metadata.get("target_lanlan"),
                    "coalesce_key": metadata.get("coalesce_key"),
                    "event_ts": metadata.get("event_ts"),
                    "event_age_seconds": metadata.get("event_age_seconds"),
                    "event_max_age_seconds": metadata.get("event_max_age_seconds"),
                    "event_expires_at": metadata.get("event_expires_at"),
                    "battle_reply_contract": metadata.get("battle_reply_contract"),
                    "live_reply_contract": metadata.get("live_reply_contract"),
                    "max_reply_chars": metadata.get("max_reply_chars"),
                    "response_module_hint": metadata.get("response_module_hint"),
                    "plugin_recommended_reply": metadata.get("plugin_recommended_reply"),
                    "plugin_owned_output": metadata.get("plugin_owned_output"),
                    "replace_pending": metadata.get("replace_pending"),
                    "interrupt_battle_event": metadata.get("interrupt_battle_event"),
                    "interrupt_pending": metadata.get("interrupt_pending"),
                    "reply_style_contract": metadata.get("reply_style_contract"),
                    "reply_contract": metadata.get("reply_contract"),
                    "reply_max_chars": metadata.get("reply_max_chars"),
                    "dialogue_policy_owner": metadata.get("dialogue_policy_owner"),
                    "plugin_dialogue_policy": metadata.get("plugin_dialogue_policy"),
                    "plugin_quiet_window_policy": metadata.get("plugin_quiet_window_policy"),
                    "quiet_window_remaining_seconds": metadata.get("quiet_window_remaining_seconds"),
                    "host_callback_contract_version": metadata.get("host_callback_contract_version"),
                    "message": safe_summary,
                }
            )
            with self._lock:
                self._seq += 1
                record["seq"] = self._seq
                if record.get("event_id"):
                    self._last_event = {
                        "event_id": record.get("event_id"),
                        "edge": record.get("edge"),
                        "level": record.get("level"),
                    }
                if stage in _OUTPUT_STAGES:
                    self._last_output_status = {
                        "stage": stage,
                        "outcome": outcome,
                        "reason": reason,
                    }
                    for key in (
                        "kind",
                        "ai_behavior",
                        "visibility",
                        "pushed",
                        "event_id",
                        "edge",
                        "level",
                        "priority",
                        "dry_run",
                        "target_lanlan",
                        "coalesce_key",
                        "event_ts",
                        "event_age_seconds",
                        "event_max_age_seconds",
                        "event_expires_at",
                        "battle_reply_contract",
                        "live_reply_contract",
                        "max_reply_chars",
                        "response_module_hint",
                        "plugin_recommended_reply",
                        "plugin_owned_output",
                        "replace_pending",
                        "interrupt_battle_event",
                        "interrupt_pending",
                        "reply_style_contract",
                        "reply_contract",
                        "reply_max_chars",
                        "dialogue_policy_owner",
                        "plugin_dialogue_policy",
                        "plugin_quiet_window_policy",
                        "quiet_window_remaining_seconds",
                        "host_callback_contract_version",
                    ):
                        if record.get(key) is not None:
                            self._last_output_status[key] = record.get(key)
                if self.enabled:
                    self._records.append(record)
        except Exception:
            return

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "last_tick_at": self._last_tick_at,
                "last_event": dict(self._last_event) if self._last_event else None,
                "last_decision": dict(self._last_decision) if self._last_decision else None,
                "last_output_status": dict(self._last_output_status) if self._last_output_status else None,
                "recent_timeline": [dict(r) for r in self._records] if self.enabled else [],
            }

    def dashboard_context(self, **state: Any) -> dict[str, Any]:
        context = dict(state)
        context["observe"] = self.snapshot()
        return context


def arbiter_chain_to_observe_records(chain: list[dict[str, Any]], *, scenario: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in chain:
        result = str(item.get("result") or "")
        reason = str(item.get("reason") or "")
        stage = "arbiter_dropped"
        outcome = "dropped"
        normalized_reason = reason or "unknown"

        if result == "spoken":
            stage = "arbiter_allowed"
            outcome = "allowed"
            normalized_reason = reason if reason in {"kill_coalesced"} else "selected"
        elif reason == "cooldown":
            stage = "arbiter_cooldown"
            normalized_reason = "cooldown_active"
        elif reason.startswith("scenario_gated"):
            stage = "arbiter_scenario_gated"
            normalized_reason = "scenario_gated"
        elif reason == "lost_to_preempt":
            stage = "arbiter_preempted"
            normalized_reason = "preempted_by_critical"
        elif result == "buffered":
            stage = "arbiter_dropped"
            outcome = "buffered"
        elif result == "suppressed":
            stage = "arbiter_suppressed"
            outcome = "suppressed"

        out.append(
            _clean_record(
                {
                    "stage": stage,
                    "outcome": outcome,
                    "reason": normalized_reason,
                    "event_id": item.get("event_id"),
                    "edge": item.get("edge"),
                    "level": item.get("level"),
                    "scenario": scenario,
                }
            )
        )
    return out
