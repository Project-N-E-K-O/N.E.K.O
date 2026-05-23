from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any


@dataclass(slots=True)
class STS2RuntimeState:
    base_url: str = "http://127.0.0.1:8080"
    transport_state: str = "disconnected"
    last_error: str = ""
    snapshot: dict[str, Any] = field(default_factory=dict)
    raw_state: dict[str, Any] = field(default_factory=dict)
    raw_actions: dict[str, Any] = field(default_factory=dict)
    control_mode: str = "program"
    autoplay_state: str = "idle"
    stop_reason: str = ""
    pause_reason: str = ""
    step_count: int = 0
    consecutive_errors: int = 0
    last_poll_at: float = 0.0
    last_action_at: float = 0.0
    last_sync_at: float = 0.0
    last_success_at: float = 0.0
    last_summary: str = ""
    last_decision_source: str = ""
    last_decision_reason: str = ""
    last_planner_type: str = ""
    latest_sync_packet: dict[str, Any] = field(default_factory=dict)
    latest_report_packet: dict[str, Any] = field(default_factory=dict)
    latest_snapshot_summary: dict[str, Any] = field(default_factory=dict)
    latest_continuous_delta: dict[str, Any] = field(default_factory=dict)
    latest_action_frame: dict[str, Any] = field(default_factory=dict)
    latest_player_operation_observation: dict[str, Any] = field(default_factory=dict)
    recent_action_frames: list[dict[str, Any]] = field(default_factory=list)
    recent_continuous_deltas: list[dict[str, Any]] = field(default_factory=list)
    recent_decision_memory: list[dict[str, Any]] = field(default_factory=list)
    recent_companion_player_ops: list[dict[str, Any]] = field(default_factory=list)
    run_intent: dict[str, Any] = field(default_factory=dict)
    recent_deliveries: list[dict[str, Any]] = field(default_factory=list)
    last_companion_turn_key: str = ""
    last_companion_scene_key: str = ""
    last_companion_evaluation_key: str = ""
    last_companion_combat_comment_key: str = ""
    last_companion_player_op_fingerprint: str = ""
    last_companion_player_op_at: float = 0.0
    last_plugin_action_fingerprint: str = ""
    snapshot_signature: str = ""
    pending_guidance: list[dict[str, Any]] = field(default_factory=list)
    guidance_generation: int = 0
    last_consumed_guidance_generation: int = 0
    last_decision_generation: int = 0
    interrupt_requested: bool = False
    interrupt_reason: str = ""
    last_sync_fingerprint: str = ""
    last_sync_screen: str = ""
    last_sync_summary_kind: str = ""
    last_sync_reason: str = ""
    last_push_scene_key: str = ""
    last_push_reason: str = ""
    last_push_step_count: int = -1
    last_push_at: float = 0.0
    sync_repeat_count: int = 0

    @property
    def standby(self) -> bool:
        return self.control_mode == "standby"

    @property
    def guidance_queue_size(self) -> int:
        return len(self.pending_guidance)

    def touch_poll(self) -> None:
        self.last_poll_at = time()

    def touch_action(self) -> None:
        self.last_action_at = time()

    def touch_sync(self) -> None:
        self.last_sync_at = time()

    def touch_success(self) -> None:
        self.last_success_at = time()
        self.consecutive_errors = 0

    def remember_delivery(self, delivery: dict[str, Any]) -> None:
        self.recent_deliveries.append(dict(delivery))
        self.recent_deliveries = self.recent_deliveries[-20:]

    def remember_action_frame(self, frame: dict[str, Any]) -> None:
        self.latest_action_frame = dict(frame)
        self.recent_action_frames.append(dict(frame))
        self.recent_action_frames = self.recent_action_frames[-20:]

    def remember_continuous_delta(self, delta: dict[str, Any]) -> None:
        self.latest_continuous_delta = dict(delta)
        self.recent_continuous_deltas.append(dict(delta))
        self.recent_continuous_deltas = self.recent_continuous_deltas[-20:]

    def remember_decision_memory(self, item: dict[str, Any]) -> None:
        self.recent_decision_memory.append(dict(item))
        self.recent_decision_memory = self.recent_decision_memory[-20:]

    def remember_companion_player_op(self, observation: dict[str, Any]) -> None:
        self.latest_player_operation_observation = dict(observation)
        self.last_companion_player_op_fingerprint = str(observation.get("fingerprint") or "")
        self.last_companion_player_op_at = time()
        self.recent_companion_player_ops.append(dict(observation))
        self.recent_companion_player_ops = self.recent_companion_player_ops[-20:]

    def note_companion_player_op_comment(self, fingerprint: str) -> None:
        self.last_companion_player_op_fingerprint = str(fingerprint or "")
        self.last_companion_player_op_at = time()


__all__ = ["STS2RuntimeState"]
